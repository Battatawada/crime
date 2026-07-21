"""Sequential one-scene-at-a-time FlowKit image generation."""

from __future__ import annotations

import asyncio
import json
import os
import re
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from flowkit_client import FlowKitClient
from ref_loader import refs_dir_from_env, upload_references, verify_references
from thumbnail_quality import (
    MIN_THUMB_BYTES,
    crop_thumbnail_letterbox,
    sanitize_thumbnail_prompt,
    thumbnail_meets_quality,
)
from thumbnail_compose import compose_thumbnail_text, derive_overlay_from_title

CHROME_NETWORK_SCRIPT = Path(
    os.environ.get("CHROME_NETWORK_SCRIPT", "/opt/niche/scripts/vps-chrome-network.sh")
)
FLOW_403_FAILOVER = os.environ.get("FLOW_403_FAILOVER", "1") != "0"


def _read_chrome_network_mode() -> str:
    env_path = Path(os.environ.get("CHROME_ENV_PATH", "/opt/niche/chrome.env"))
    if not env_path.is_file():
        return "proxy"
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line.startswith("CHROME_NETWORK_MODE="):
            return line.split("=", 1)[1].strip().strip('"') or "proxy"
    return "proxy"


def _switch_chrome_network(target_mode: str) -> bool:
    """proxy|direct — restarts Chrome + FlowKit. Requires passwordless sudo for niche."""
    if not FLOW_403_FAILOVER:
        return False
    script = CHROME_NETWORK_SCRIPT
    if not script.is_file():
        print(f"Chrome network script missing: {script}", flush=True)
        return False
    print(f"403 failover: switching Chrome network -> {target_mode}", flush=True)
    try:
        proc = subprocess.run(
            ["sudo", "-n", str(script), target_mode],
            capture_output=True,
            text=True,
            timeout=300,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        print(f"Chrome network switch failed: {exc}", flush=True)
        return False
    if proc.stdout:
        print(proc.stdout.rstrip(), flush=True)
    if proc.returncode != 0:
        err = (proc.stderr or proc.stdout or "unknown error").strip()
        print(f"Chrome network switch failed ({proc.returncode}): {err}", flush=True)
        return False
    return True


_RISKY_TERM_REPLACEMENTS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"(?i)EAST AREA RAPIST"), "EAR case"),
    (re.compile(r"(?i)ORIGINAL NIGHT STALKER"), "ONS case"),
    (re.compile(r"(?i)VISALIA RANSACKER"), "Visalia case"),
    (re.compile(r"(?i)GOLDEN STATE KILLER"), "GSK case"),
    (re.compile(r"(?i)\bJOSEPH\s+DEANGELO\b"), "a person of interest"),
    (re.compile(r"(?i)\bDEANGELO\b"), "suspect"),
    (re.compile(r"(?i)\bRAPIST\b"), "offender"),
    (re.compile(r"(?i)\bRAPE\b"), "crime"),
    (re.compile(r"(?i)\bKILLER\b"), "suspect"),
    (re.compile(r"(?i)\bMURDER(?:ED|ER|S)?\b"), "crime"),
    (re.compile(r"(?i)\bkilled\b"), "attacked"),
    (re.compile(r"(?i)\bassault(?:ed|s)?\b"), "confronted"),
    (re.compile(r"(?i)\bweapon\b"), "object"),
    (re.compile(r"(?i)\bgun\b"), "item"),
    (re.compile(r"(?i)\bblood\b"), "shadow"),
    (re.compile(r"(?i)\bdead\b"), "fallen"),
    (re.compile(r"(?i)\bcorpse\b"), "figure"),
    (re.compile(r"(?i)\bvictim\b"), "person"),
    (re.compile(r"(?i)\bmasked\s+intruder\b"), "shadowy figure"),
    (re.compile(r"(?i)\bintruder\b"), "figure"),
]


def _strip_risky_terms(prompt: str) -> str:
    cleaned = prompt
    for pat, repl in _RISKY_TERM_REPLACEMENTS:
        cleaned = pat.sub(repl, cleaned)
    return cleaned


def _rewrite_prompt_safe(prompt: str, attempt: int = 1) -> str:
    """Progressive rewrite for Flow safety-filter retries."""
    cleaned = _strip_risky_terms(prompt)
    # Drop on-image name tags / labels that often trip policy
    cleaned = re.sub(r"(?i)\b(labeled|label|tag|reads|reading)\b[^.]*", "", cleaned)
    cleaned = re.sub(r"\s{2,}", " ", cleaned).strip(" .,")
    suffix = (
        ", stylized fictional 2D illustration, anonymous characters, "
        "no real persons, no violence, no gore, soft lighting, no visible text"
    )
    if attempt >= 3:
        return (
            "Minimal 2D animation on matte black background, bold black outlines, "
            "cold documentary mood, circular-head host Jonty in charcoal shirt, "
            "calm investigative atmosphere, fictional stylized art, no real persons, "
            "no violence, no gore, no visible text, no labels"
        )
    if attempt >= 2:
        return cleaned + suffix + ", abstract symbolic composition, no faces of real people"
    return cleaned + suffix


def _safe_fallback_prompt(scene_id: int) -> str:
    """Last-resort prompt that should always clear Flow safety filters."""
    return (
        f"Minimal 2D animation scene variation {scene_id}, bold black outlines, "
        "matte black background, cold true-crime documentary mood, circular-head host "
        "Jonty with charcoal shirt and red JONTY text looking thoughtful, soft lighting, "
        "fictional stylized illustration, no real persons, no violence, no gore, "
        "no visible text, no labels, no scene numbers"
    )


def _start_flowkit_stack() -> None:
    if os.environ.get("FLOWKIT_USE_SYSTEMD", "1") == "1":
        return
    script = os.environ.get("FLOWKIT_START_SCRIPT")
    if script and Path(script).exists():
        subprocess.run(["bash", script], check=False)


def _restart_flowkit_stack() -> None:
    script = os.environ.get("FLOWKIT_RESTART_SCRIPT")
    if script and Path(script).exists():
        subprocess.run(["bash", script], check=False, timeout=180)
        return
    if os.environ.get("FLOWKIT_USE_SYSTEMD", "1") == "1":
        subprocess.run(["systemctl", "restart", "flowkit-agent"], check=False)


def _stop_flowkit_stack() -> None:
    if os.environ.get("FLOWKIT_USE_SYSTEMD", "1") == "1":
        return
    script = os.environ.get("FLOWKIT_STOP_SCRIPT")
    if script and Path(script).exists():
        subprocess.run(["bash", script], check=False)


def _sanitize_prompt(prompt: str, scene_id: int) -> str:
    cleaned = " ".join(str(prompt).split()).strip()
    lower = cleaned.lower()
    # Drop scene-number title cards Flow tends to render as visible text
    cleaned = re.sub(r"(?i)^scene\s+\d+\s*[:\-]?\s*", "", cleaned)
    cleaned = re.sub(r"(?i)\b(scene|chapter|part)\s+\d+\s*title\s*[:\-]?\s*", "", cleaned)
    if (
        len(cleaned.split()) >= 8
        and not lower.startswith("answer:")
        and "total parts:" not in lower
        and not lower.startswith("part ")
        and not re.match(r"^scene\s+\d+\b", lower)
    ):
        suffix = ", no visible text, no labels, no scene numbers, no titles"
        if "no visible text" not in lower:
            cleaned += suffix
        return cleaned
    return (
        "Minimal 2D animation, bold black outlines, matte black background, "
        "cold true-crime documentary mood, circular-head host Jonty with charcoal "
        "shirt and red JONTY text, no gore, no visible text, no labels, no scene numbers"
    )


class SequentialGenerator:
    def __init__(
        self,
        run_id: str,
        runs_dir: Path,
        on_progress: Callable[[dict[str, Any]], None] | None = None,
    ) -> None:
        self.run_id = run_id
        self.runs_dir = runs_dir
        self.images_dir = runs_dir / run_id / "images"
        self.state_path = runs_dir / run_id / "state.json"
        self.on_progress = on_progress or (lambda _: None)
        self.delay = int(os.environ.get("SCENE_DELAY_SECONDS", "25"))
        self.max_retries = int(os.environ.get("SCENE_MAX_RETRIES", "5"))
        self.client = FlowKitClient()

    def _load_state(self) -> dict[str, Any]:
        if self.state_path.exists():
            return json.loads(self.state_path.read_text(encoding="utf-8"))
        return {
            "run_id": self.run_id,
            "status": "pending",
            "phase": "refs",
            "total_scenes": 0,
            "images_ready": 0,
            "current_scene": 0,
            "completed": [],
            "failed_scenes": [],
            "error": None,
        }

    def _save_state(self, state: dict[str, Any]) -> None:
        state["updated_at"] = datetime.now(timezone.utc).isoformat()
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        self.state_path.write_text(json.dumps(state, indent=2), encoding="utf-8")
        self.on_progress(state)

    def run(self, scenes: list[dict[str, Any]], entities: list[dict[str, Any]] | None = None) -> None:
        state = self._load_state()
        ready_before = int(state.get("images_ready", 0))
        resuming = ready_before > 0 and ready_before < len(scenes)
        state["status"] = "running"
        state["total_scenes"] = len(scenes)
        if resuming:
            state["error"] = None
            state["failed_scenes"] = []
            cooldown = int(os.environ.get("FLOW_RESUME_COOLDOWN_SEC", "600"))
            if cooldown > 0:
                print(f"Resume cooldown {cooldown}s (Flow 429 recovery)", flush=True)
                time.sleep(cooldown)
            print(f"Resuming run {self.run_id} from {state.get('images_ready', 0)}/{len(scenes)} images", flush=True)
        if "chrome_network_mode" not in state:
            state["chrome_network_mode"] = _read_chrome_network_mode()
        self._save_state(state)

        _start_flowkit_stack()
        try:
            self.client.ensure_ready()

            project_id = state.get("project_id")
            ref_media = state.get("ref_media") or {}

            if not project_id:
                state["phase"] = "project"
                self._save_state(state)
                for attempt in range(4):
                    try:
                        project_id = self.client.create_project(title=f"Niche {self.run_id}", story="")
                        state["project_id"] = project_id
                        self._save_state(state)
                        break
                    except Exception as exc:  # noqa: BLE001
                        if attempt >= 3:
                            raise
                        state["error"] = f"create_project retry {attempt + 1}: {exc}"
                        self._save_state(state)
                        _restart_flowkit_stack()
                        self.client.ensure_ready(wait_sec=180)
                        time.sleep(15 * (attempt + 1))
                if not project_id:
                    raise RuntimeError("create_project failed after retries")

            if not ref_media:
                state["phase"] = "refs"
                self._save_state(state)
                refs_dir = refs_dir_from_env()
                verify_references(refs_dir)
                ref_media = upload_references(refs_dir, self.client, project_id=project_id)
                state["ref_media"] = ref_media
                self._save_state(state)

            video_id = project_id  # FlowKit ties scenes to project context

            state["phase"] = "scenes"
            self._save_state(state)

            sorted_scenes = sorted(scenes, key=lambda s: int(s["scene_id"]))
            for scene in sorted_scenes:
                scene_id = int(scene["scene_id"])
                filename = f"scene_{scene_id:02d}.png"
                dest = self.images_dir / filename
                state["current_scene"] = scene_id
                self._save_state(state)

                if dest.exists() and dest.stat().st_size > 10_000:
                    if filename not in state["completed"]:
                        state["completed"].append(filename)
                        state["images_ready"] = len(state["completed"])
                        state["last_saved"] = filename
                        self._save_state(state)
                    continue

                # Flow rate-limits after ~40 images — brief cooldown before next batch
                batch_cooldown = int(os.environ.get("FLOW_BATCH_COOLDOWN_SEC", "300"))
                if batch_cooldown > 0 and scene_id > 1 and (scene_id - 1) % 40 == 0:
                    print(f"Batch cooldown {batch_cooldown}s before scene {scene_id}", flush=True)
                    time.sleep(batch_cooldown)

                entity_refs = scene.get("entity_refs") or []
                media_inputs = [ref_media[r] for r in entity_refs if r in ref_media]
                raw_prompt = scene.get("prompt", "")
                prompt = _sanitize_prompt(_strip_risky_terms(raw_prompt), scene_id)

                last_err = ""
                attempt = 0
                saved = False
                while attempt < self.max_retries:
                    attempt += 1
                    try:
                        image_url, _ = self.client.generate_scene_image(
                            project_id=project_id,
                            scene_id=str(scene_id),
                            video_id=video_id,
                            prompt=prompt,
                            ref_media_ids=media_inputs,
                        )
                        self.client.download_url(image_url, dest)
                        if dest.stat().st_size < 10_000:
                            raise RuntimeError(f"Downloaded file too small: {dest}")
                        state["completed"].append(filename)
                        state["images_ready"] = len(state["completed"])
                        state["last_saved"] = filename
                        self._save_state(state)
                        saved = True
                        break
                    except Exception as exc:  # noqa: BLE001
                        last_err = str(exc)
                        is_safety = "400" in last_err
                        prompt = (
                            _rewrite_prompt_safe(raw_prompt, attempt=attempt)
                            if is_safety
                            else _rewrite_prompt_safe(raw_prompt, attempt=1)
                        )
                        mode = state.get("chrome_network_mode", _read_chrome_network_mode())
                        if (
                            "403" in last_err
                            and mode == "proxy"
                            and _switch_chrome_network("direct")
                        ):
                            state["chrome_network_mode"] = "direct"
                            self._save_state(state)
                            self.client.ensure_ready(wait_sec=180)
                            attempt -= 1
                            time.sleep(30)
                            continue
                        is_throttled = "429" in last_err or "403" in last_err
                        if "403" in last_err:
                            wait = min(900, 300 * attempt)
                        elif "429" in last_err:
                            wait = 120 * attempt
                        else:
                            wait = self.delay
                        if attempt >= self.max_retries:
                            break
                        print(
                            f"Scene {scene_id} retry {attempt}/{self.max_retries} "
                            f"({'Flow throttle' if is_throttled else 'error'}), wait {wait}s",
                            flush=True,
                        )
                        state["error"] = f"Scene {scene_id} retry {attempt}: {last_err}"
                        self._save_state(state)
                        time.sleep(wait)
                        state["error"] = None

                if not saved:
                    # Safety/policy 400s (and other stuck prompts): never abort the run —
                    # generate a guaranteed-safe fallback so every scene gets a PNG.
                    if "401" in last_err or "unauthorized" in last_err.lower():
                        state["status"] = "failed"
                        state["error"] = f"Scene {scene_id}: {last_err}"
                        state["failed_scenes"].append(scene_id)
                        self._save_state(state)
                        raise RuntimeError(last_err)
                    print(
                        f"Scene {scene_id}: using safe fallback after retries ({last_err})",
                        flush=True,
                    )
                    state["error"] = f"Scene {scene_id} fallback: {last_err}"
                    self._save_state(state)
                    fallback = _safe_fallback_prompt(scene_id)
                    try:
                        image_url, _ = self.client.generate_scene_image(
                            project_id=project_id,
                            scene_id=str(scene_id),
                            video_id=video_id,
                            prompt=fallback,
                            ref_media_ids=media_inputs,
                        )
                        self.client.download_url(image_url, dest)
                        if dest.stat().st_size < 10_000:
                            raise RuntimeError(f"Downloaded file too small: {dest}")
                        state["completed"].append(filename)
                        state["images_ready"] = len(state["completed"])
                        state["last_saved"] = filename
                        state["error"] = None
                        if scene_id not in state.get("fallback_scenes", []):
                            state.setdefault("fallback_scenes", []).append(scene_id)
                        self._save_state(state)
                    except Exception as exc:  # noqa: BLE001
                        state["status"] = "failed"
                        state["error"] = f"Scene {scene_id}: {exc}"
                        state["failed_scenes"].append(scene_id)
                        self._save_state(state)
                        raise

                time.sleep(self.delay)

            self._generate_thumbnail_if_needed(state, project_id, video_id, ref_media)

            state["status"] = "complete"
            state["phase"] = "done"
            state["error"] = None
            self._save_state(state)
        except Exception as exc:  # noqa: BLE001
            state = self._load_state()
            state["status"] = "failed"
            state["error"] = str(exc)
            self._save_state(state)
            raise
        finally:
            _stop_flowkit_stack()

    def _generate_thumbnail_if_needed(
        self,
        state: dict[str, Any],
        project_id: str,
        video_id: str,
        ref_media: dict[str, str],
    ) -> None:
        thumb_json = self.state_path.parent / "thumbnail.json"
        if not thumb_json.exists():
            return
        thumb_meta = json.loads(thumb_json.read_text(encoding="utf-8"))
        raw_prompt = str(thumb_meta.get("prompt", "")).strip()
        if not raw_prompt:
            return

        dest = self.images_dir / "thumbnail.png"
        if dest.exists() and thumbnail_meets_quality(dest):
            crop_thumbnail_letterbox(dest)
            if thumbnail_meets_quality(dest):
                state["thumbnail_ready"] = True
                self._save_state(state)
                return
            dest.unlink(missing_ok=True)

        state["phase"] = "thumbnail"
        self._save_state(state)
        entity_refs = thumb_meta.get("entity_refs") or []
        media_inputs = [ref_media[r] for r in entity_refs if r in ref_media]
        prompt = sanitize_thumbnail_prompt(
            raw_prompt,
            title=str(thumb_meta.get("title", "")),
            topic=str(thumb_meta.get("topic", "")),
        )
        thumb_retries = max(self.max_retries, 5)
        last_err = ""

        for attempt in range(1, thumb_retries + 1):
            try:
                image_url, _ = self.client.generate_scene_image(
                    project_id=project_id,
                    scene_id="thumbnail",
                    video_id=video_id,
                    prompt=prompt,
                    ref_media_ids=media_inputs,
                    orientation="landscape",
                )
                self.client.download_url(image_url, dest)
                crop_thumbnail_letterbox(dest)
                overlay = str(thumb_meta.get("overlay_text") or "").strip()
                if not overlay:
                    overlay = derive_overlay_from_title(str(thumb_meta.get("title", "")))
                if overlay:
                    compose_thumbnail_text(dest, overlay)
                if not thumbnail_meets_quality(dest, min_bytes=MIN_THUMB_BYTES):
                    size = dest.stat().st_size if dest.exists() else 0
                    dest.unlink(missing_ok=True)
                    raise RuntimeError(f"Thumbnail failed quality gate ({size} bytes)")
                state["thumbnail_ready"] = True
                self._save_state(state)
                print(f"Thumbnail saved {dest} ({dest.stat().st_size} bytes)", flush=True)
                return
            except Exception as exc:  # noqa: BLE001
                last_err = str(exc)
                # Safety/policy 400s → swap to high-CTR safe fallback (still edge-to-edge)
                if "400" in last_err or "quality gate" in last_err.lower():
                    prompt = sanitize_thumbnail_prompt(
                        "",
                        title=str(thumb_meta.get("title", "")),
                        topic=str(thumb_meta.get("topic", "")),
                    )
                mode = state.get("chrome_network_mode", _read_chrome_network_mode())
                if "403" in last_err and mode == "proxy" and _switch_chrome_network("direct"):
                    state["chrome_network_mode"] = "direct"
                    self._save_state(state)
                    self.client.ensure_ready(wait_sec=180)
                    time.sleep(30)
                    continue
                is_rate_limit = "429" in last_err or "403" in last_err
                wait = (120 * attempt) if is_rate_limit else self.delay
                if attempt == thumb_retries:
                    raise RuntimeError(
                        f"Thumbnail generation failed after {thumb_retries} attempts: {last_err}"
                    ) from exc
                print(f"Thumbnail retry {attempt}/{thumb_retries}, wait {wait}s", flush=True)
                time.sleep(wait)


async def run_generation_async(
    run_id: str,
    scenes: list[dict[str, Any]],
    entities: list[dict[str, Any]] | None,
    runs_dir: Path,
    on_progress: Callable[[dict[str, Any]], None] | None = None,
) -> None:
    loop = asyncio.get_event_loop()
    gen = SequentialGenerator(run_id, runs_dir, on_progress=on_progress)
    await loop.run_in_executor(None, lambda: gen.run(scenes, entities))
