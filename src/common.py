"""Shared utilities for the Dark Narrative pipeline."""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / "config"
PROMPTS = CONFIG / "prompts"


def load_json(path: Path) -> Any:
    with path.open(encoding="utf-8") as f:
        return json.load(f)


def save_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def load_prompt(name: str) -> str:
    return (PROMPTS / name).read_text(encoding="utf-8").strip()


def new_run_id() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")


def strip_markdown(text: str) -> str:
    text = re.sub(r"^#{1,6}\s+", "", text, flags=re.MULTILINE)
    text = re.sub(r"\*\*(.+?)\*\*", r"\1", text)
    text = re.sub(r"\*(.+?)\*", r"\1", text)
    return text.strip()


def normalize_tts_punctuation(text: str) -> str:
    """Fix spacing so TTS pauses naturally at punctuation."""
    text = re.sub(r"\s+([,.!?;:])", r"\1", text)
    text = re.sub(r"([,.!?;:])(?=[A-Za-z\"'])", r"\1 ", text)
    text = re.sub(r"\s*,\s*", ", ", text)
    text = re.sub(r"\s*\.\s*", ". ", text)
    text = re.sub(r"\s*\?\s*", "? ", text)
    text = re.sub(r"\s*!\s*", "! ", text)
    text = re.sub(r"\s+", " ", text)
    text = re.sub(r"\.\s+\.", ".", text)
    return text.strip()


def clean_script_for_tts(text: str) -> str:
    """Remove NotebookLM junk and citation markers; keep narration-only text."""
    text = strip_markdown(text)
    # Profanity / graphic markers from true-crime prompts → spoken beep
    text = re.sub(r"\[BEEP\]", "beep", text, flags=re.IGNORECASE)
    # Inline metadata (multi-part merges, conversation IDs, etc.)
    text = re.sub(
        r"(?i)\b(?:new conversation|continuing conversation|conversation)\s*:\s*"
        r"[a-f0-9-]{8,}(?:\s*\(\s*turn\s+\d+\s*\))?",
        "",
        text,
    )
    text = re.sub(r"(?i)\banswer\s*:\s*", "", text)
    text = re.sub(r"(?i)\btotal\s+(parts|scenes)\s*:\s*\d+", "", text)
    text = re.sub(r"(?i)\bpart\s+\d+\b", "", text)
    text = re.sub(r"\b[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}\b", "", text, flags=re.IGNORECASE)

    kept: list[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if re.match(r"^answer:\s*$", stripped, re.IGNORECASE):
            continue
        if re.match(r"^answer:\s*total\s+(parts|scenes)\s*:\s*\d+", stripped, re.IGNORECASE):
            continue
        if re.match(r"^total\s+(parts|scenes)\s*:\s*\d+", stripped, re.IGNORECASE):
            continue
        if re.match(r"^part\s+\d+\s*$", stripped, re.IGNORECASE):
            continue
        if re.match(r"^next\s*$", stripped, re.IGNORECASE):
            continue
        if re.match(
            r"^(?:new conversation|continuing conversation|conversation)\s*:\s*[a-f0-9-]+",
            stripped,
            re.IGNORECASE,
        ):
            continue
        kept.append(stripped)
    merged = " ".join(kept)
    merged = re.sub(r"\[\d+(?:,\s*\d+)*\]", "", merged)
    merged = re.sub(r"\s+", " ", merged)
    return normalize_tts_punctuation(merged)


def extract_json_blocks(text: str) -> list[Any]:
    """Parse one or more JSON arrays/objects from LLM output."""
    blocks: list[Any] = []
    fenced = re.findall(r"```(?:json)?\s*([\s\S]*?)```", text, flags=re.IGNORECASE)
    candidates = fenced if fenced else [text]
    for chunk in candidates:
        chunk = chunk.strip()
        if not chunk:
            continue
        try:
            blocks.append(json.loads(chunk))
        except json.JSONDecodeError:
            for pattern in (r"(\{[\s\S]*\})", r"(\[[\s\S]*\])"):
                match = re.search(pattern, chunk)
                if match:
                    try:
                        blocks.append(json.loads(match.group(1)))
                        break
                    except json.JSONDecodeError:
                        continue
    if not blocks:
        raise ValueError("No JSON found in model response")
    return blocks


def split_script_scenes(script: str) -> list[tuple[int, str]]:
    """Split script on [SCENE_NN] markers."""
    pattern = re.compile(r"\[SCENE_(\d+)\]", re.IGNORECASE)
    parts = pattern.split(script)
    if len(parts) < 3:
        raise ValueError("Script must contain [SCENE_01]..[SCENE_NN] markers")
    scenes: list[tuple[int, str]] = []
    # parts: [preamble, id1, text1, id2, text2, ...]
    i = 1
    while i + 1 < len(parts):
        scene_id = int(parts[i])
        body = strip_markdown(parts[i + 1]).strip()
        if body:
            scenes.append((scene_id, body))
        i += 2
    return scenes


def is_transient_notebooklm_error(message: str) -> bool:
    """True for network/RPC timeouts that are worth retrying on CI."""
    lower = message.lower()
    return any(
        s in lower
        for s in (
            "get_notebook",
            "network error",
            "timed out",
            "timeout",
            "transportservererror",
            "server-error retries exhausted",
            "connection reset",
            "temporarily unavailable",
            "rate limit",
        )
    )


def run_cmd(args: list[str], *, env: dict[str, str] | None = None, check: bool = True) -> subprocess.CompletedProcess[str]:
    merged = os.environ.copy()
    if env:
        merged.update(env)
    result = subprocess.run(
        args,
        capture_output=True,
        text=True,
        env=merged,
        cwd=ROOT,
    )
    if check and result.returncode != 0:
        sys.stderr.write(result.stderr or result.stdout or "")
        raise RuntimeError(f"Command failed ({result.returncode}): {' '.join(args)}")
    return result


def notebooklm(*args: str, json_out: bool = False) -> str:
    cmd = ["notebooklm", *args]
    if json_out:
        cmd.append("--json")
    result = run_cmd(cmd)
    return result.stdout.strip()


def notebooklm_json(*args: str) -> dict[str, Any]:
    """Run notebooklm with --json and parse the response envelope."""
    data = json.loads(notebooklm(*args, json_out=True))
    if isinstance(data, dict) and data.get("error"):
        raise RuntimeError(data.get("message") or str(data))
    if not isinstance(data, dict):
        raise RuntimeError(f"Unexpected notebooklm JSON response: {data!r}")
    return data


def extract_notebook_id(payload: dict[str, Any]) -> str:
    """notebooklm 0.7+ nests create output under ``notebook``."""
    nb = payload.get("notebook", payload)
    if isinstance(nb, dict) and nb.get("id"):
        return str(nb["id"])
    raise RuntimeError(f"Unexpected notebooklm create response: {payload}")


def extract_source_id(payload: dict[str, Any]) -> str:
    """notebooklm 0.7+ nests source add output under ``source``."""
    src = payload.get("source", payload)
    if isinstance(src, dict) and src.get("id"):
        return str(src["id"])
    if isinstance(src, dict) and src.get("source_id"):
        return str(src["source_id"])
    if payload.get("source_id"):
        return str(payload["source_id"])
    raise RuntimeError(f"Unexpected notebooklm source add response: {payload}")


def append_github_output(key: str, value: str) -> None:
    out_path = os.environ.get("GITHUB_OUTPUT")
    if out_path:
        with open(out_path, "a", encoding="utf-8") as f:
            f.write(f"{key}={value}\n")


def parse_total_parts(text: str) -> int:
    match = re.search(r"Total (?:Parts|Scenes):\s*(\d+)", text, re.IGNORECASE)
    return int(match.group(1)) if match else 1


def strip_total_parts_header(text: str) -> str:
    lines = text.strip().splitlines()
    if lines and re.match(r"Total (?:Parts|Scenes):\s*\d+", lines[0], re.IGNORECASE):
        return "\n".join(lines[1:]).strip()
    if lines and re.match(r"^Answer:\s*$", lines[0], re.IGNORECASE):
        lines = lines[1:]
    if lines and re.match(r"Total (?:Parts|Scenes):\s*\d+", lines[0], re.IGNORECASE):
        return "\n".join(lines[1:]).strip()
    return text.strip()


def notebooklm_json_with_retry(*args: str, retries: int = 4) -> dict[str, Any]:
    """notebooklm_json with retries on transient RPC/network errors."""
    last_err = ""
    for attempt in range(retries):
        try:
            return notebooklm_json(*args)
        except RuntimeError as exc:
            last_err = str(exc)
            if attempt + 1 < retries and is_transient_notebooklm_error(last_err):
                wait = 15 * (attempt + 1)
                print(f"  notebooklm retry {attempt + 2}/{retries} in {wait}s...", flush=True)
                time.sleep(wait)
                continue
            raise
    raise RuntimeError(last_err)


def is_transient_http_status(status_code: int) -> bool:
    return status_code in {408, 429, 500, 502, 503, 504}


def httpx_get_json_with_retry(url: str, *, headers: dict | None = None, timeout: float = 60.0, retries: int = 5):
    import httpx

    last_err: Exception | None = None
    for attempt in range(retries):
        try:
            resp = httpx.get(url, headers=headers, timeout=timeout)
            if resp.status_code in {502, 503, 504, 429} and attempt + 1 < retries:
                time.sleep(min(60, 5 * (attempt + 1)))
                continue
            resp.raise_for_status()
            return resp.json()
        except Exception as exc:  # noqa: BLE001
            last_err = exc
            retry = isinstance(exc, Exception) and (
                "timeout" in str(exc).lower()
                or "connect" in str(exc).lower()
                or (hasattr(exc, "response") and getattr(exc.response, "status_code", 0) in {502, 503, 504, 429})
            )
            if retry and attempt + 1 < retries:
                time.sleep(min(60, 5 * (attempt + 1)))
                continue
            raise
    raise last_err or RuntimeError(f"GET failed: {url}")


def httpx_post_json_with_retry(
    url: str,
    *,
    json_body: dict,
    headers: dict | None = None,
    timeout: float = 120.0,
    retries: int = 5,
):
    import httpx

    last_err: Exception | None = None
    for attempt in range(retries):
        try:
            resp = httpx.post(url, json=json_body, headers=headers, timeout=timeout)
            if resp.status_code in {502, 503, 504, 429} and attempt + 1 < retries:
                time.sleep(min(60, 5 * (attempt + 1)))
                continue
            resp.raise_for_status()
            return resp.json()
        except Exception as exc:  # noqa: BLE001
            last_err = exc
            if attempt + 1 < retries and (
                "timeout" in str(exc).lower() or "connect" in str(exc).lower()
            ):
                time.sleep(min(60, 5 * (attempt + 1)))
                continue
            raise
    raise last_err or RuntimeError(f"POST failed: {url}")


def httpx_download_with_retry(
    url: str,
    dest: Path,
    *,
    headers: dict | None = None,
    timeout: float = 120.0,
    retries: int = 5,
) -> None:
    import httpx

    for attempt in range(retries):
        try:
            resp = httpx.get(url, headers=headers, timeout=timeout)
            if resp.status_code in {502, 503, 504, 429} and attempt + 1 < retries:
                time.sleep(min(60, 5 * (attempt + 1)))
                continue
            resp.raise_for_status()
            dest.write_bytes(resp.content)
            return
        except Exception as exc:  # noqa: BLE001
            if attempt + 1 < retries and (
                "timeout" in str(exc).lower() or "connect" in str(exc).lower()
            ):
                time.sleep(min(60, 5 * (attempt + 1)))
                continue
            raise
    raise RuntimeError(f"Download failed: {url}")


def parse_image_prompt_lines(text: str) -> list[str]:
    """Parse blank-line-separated image prompts from NotebookLM output."""
    body = strip_total_parts_header(text)
    blocks = re.split(r"\n\s*\n", body)
    prompts: list[str] = []
    for block in blocks:
        line = " ".join(ln.strip() for ln in block.splitlines() if ln.strip())
        if is_valid_image_prompt(line):
            prompts.append(line)
    return prompts


_METADATA_PROMPT_RE = re.compile(
    r"^(answer:\s*)?(total parts:\s*\d+|part\s+\d+\s*$|next\s*$)",
    re.IGNORECASE,
)
_CONVERSATION_PROMPT_RE = re.compile(
    r"^(?:new conversation|continuing conversation|conversation)\s*:\s*[a-f0-9-]+",
    re.IGNORECASE,
)


def is_valid_image_prompt(line: str, *, min_words: int = 8) -> bool:
    """Drop NotebookLM headers and other non-prompt lines."""
    cleaned = line.strip()
    if not cleaned:
        return False
    if _METADATA_PROMPT_RE.match(cleaned):
        return False
    if _CONVERSATION_PROMPT_RE.match(cleaned):
        return False
    if re.search(r"(?i)\banswer:\s*total\s+(parts|scenes)\s*:", cleaned):
        return False
    if re.search(r"(?i)\b(?:new conversation|conversation)\s*:\s*[a-f0-9-]{8,}", cleaned):
        return False
    if re.match(r"^total parts:\s*\d+", cleaned, re.IGNORECASE):
        return False
    if re.match(r"^scene\s+\d+\b", cleaned, re.IGNORECASE):
        return False
    if re.search(r"\bscene\s+\d+\b.*\blearning\b", cleaned, re.IGNORECASE):
        return False
    if len(cleaned.split()) < min_words:
        return False
    return True


def strip_prompt_labels(prompt: str) -> str:
    """Remove Flow-prone title prefixes from image prompts."""
    p = " ".join(prompt.split()).strip()
    p = re.sub(r"(?i)^scene\s+\d+\s*[:\-]?\s*", "", p)
    p = re.sub(r"(?i)\b(scene|chapter|part)\s+\d+\s*title\s*[:\-]?\s*", "", p)
    return p.strip()


def cap_scenes(prompts: list[str], max_scenes: int) -> list[str]:
    if max_scenes > 0 and len(prompts) > max_scenes:
        return prompts[:max_scenes]
    return prompts


def estimate_scene_count(script: str, pipeline: dict[str, Any] | None = None) -> int:
    """Scene count from narration length — not from LLM prompt spam."""
    pipeline = pipeline or {}
    max_scenes = int(pipeline.get("max_scenes", 60))
    min_scenes = int(pipeline.get("min_scenes", 10))
    words_per_scene = int(pipeline.get("words_per_scene", 35))
    text = clean_script_for_tts(script)
    word_count = len(text.split())
    if word_count < 1:
        return min_scenes
    n = max(min_scenes, round(word_count / max(1, words_per_scene)))
    return min(max_scenes, n)


def align_scenes_to_narration(
    script: str,
    prompts: list[str],
    pipeline: dict[str, Any] | None = None,
) -> tuple[list[str], list[str]]:
    """
    One image per narrated beat. Drop tail scenes that would be silent 0.35s flashes.
    Returns (prompts, script_segments) with equal length.
    """
    pipeline = pipeline or {}
    max_scenes = int(pipeline.get("max_scenes", 60))
    min_words = int(pipeline.get("min_words_per_scene", 12))
    text = clean_script_for_tts(script)
    target = min(len(prompts), estimate_scene_count(text, pipeline), max_scenes)
    target = max(1, target)
    prompts = prompts[:target]
    segments = split_script_for_scenes(text, len(prompts))

    while len(segments) > 1 and len(segments[-1].split()) < min_words:
        segments.pop()
        prompts.pop()

    if len(segments) != len(prompts):
        segments = split_script_for_scenes(text, len(prompts))

    return prompts, segments


def load_topic_history(path: Path | None = None) -> list[dict[str, Any]]:
    path = path or CONFIG / "topic_history.json"
    if not path.exists():
        return []
    data = load_json(path)
    if isinstance(data, dict):
        return list(data.get("topics", []))
    if isinstance(data, list):
        return data
    return []


def format_topic_history_for_prompt(topics: list[dict[str, Any]], limit: int = 12) -> str:
    if not topics:
        return "(none yet — this is the first video)"
    lines: list[str] = []
    for row in topics[-limit:]:
        title = row.get("title") or row.get("topic") or "Unknown"
        run_id = row.get("run_id", "")
        lines.append(f"- {title}" + (f" [{run_id}]" if run_id else ""))
    return "\n".join(lines)


def append_topic_history(
    path: Path,
    *,
    run_id: str,
    topic: str,
    title: str,
    series_type: str | None = None,
    max_entries: int = 30,
) -> None:
    existing = load_topic_history(path)
    row: dict[str, Any] = {
        "run_id": run_id,
        "topic": topic,
        "title": title,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    if series_type:
        row["series_type"] = series_type
    existing.append(row)
    save_json(path, {"topics": existing[-max_entries:]})


def next_series_type(history: list[dict[str, Any]] | None = None) -> str:
    """Alternate incident ↔ serial_killer. First video = incident."""
    history = history if history is not None else load_topic_history()
    if not history:
        return "incident"
    last = str(history[-1].get("series_type") or "incident").strip().lower()
    return "serial_killer" if last == "incident" else "incident"


def prompts_to_scenes(prompts: list[str], entity_refs: list[str] | None = None) -> list[dict]:
    refs = entity_refs or ["style_host", "style_host_react", "style_fact_card", "style_case_scene"]
    return [
        {"scene_id": i + 1, "prompt": p, "entity_refs": list(refs)}
        for i, p in enumerate(prompts)
    ]


def dedupe_prompts(prompts: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for p in prompts:
        key = p.strip().lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(p.strip())
    return out


def split_script_for_scenes(script: str, num_scenes: int) -> list[str]:
    """
    Split narration into N sequential chunks for per-scene TTS.
    Image prompt i aligns with audio chunk i → editor-accurate timing.
    Distributes words evenly — no empty trailing scenes.
    """
    if num_scenes < 1:
        raise ValueError("num_scenes must be >= 1")
    text = re.sub(r"\s+", " ", script.strip())
    if not text:
        return [""] * num_scenes

    sentences = re.split(r"(?<=[.!?])\s+", text)
    sentences = [s.strip() for s in sentences if s.strip()]
    if not sentences:
        return [text] + [""] * (num_scenes - 1)

    # Fewer sentences than scenes: split by word budget (avoid rapid empty tail clips)
    if len(sentences) < num_scenes:
        words = text.split()
        if not words:
            return [""] * num_scenes
        words_per = len(words) / num_scenes
        chunks: list[str] = []
        for i in range(num_scenes):
            a = int(i * words_per)
            b = len(words) if i == num_scenes - 1 else int((i + 1) * words_per)
            chunks.append(" ".join(words[a:b]))
        return chunks

    if len(sentences) <= num_scenes:
        return sentences + [""] * (num_scenes - len(sentences))

    total_words = sum(len(s.split()) for s in sentences)
    words_per_chunk = total_words / num_scenes
    chunks = []
    current: list[str] = []
    word_count = 0

    for sent in sentences:
        current.append(sent)
        word_count += len(sent.split())
        if len(chunks) < num_scenes - 1 and word_count >= words_per_chunk:
            chunks.append(" ".join(current))
            current = []
            word_count = 0

    if current:
        chunks.append(" ".join(current))

    while len(chunks) < num_scenes:
        chunks.append("")
    return chunks[:num_scenes]


def parse_seo_json(text: str) -> dict:
    blocks = extract_json_blocks(text)
    for block in blocks:
        if isinstance(block, dict) and "title" in block:
            return block
    raise ValueError("No SEO JSON object in NotebookLM response")


def sanitize_seo_title(title: str, max_chars: int = 65) -> str:
    cleaned = re.sub(r"\*+", "", title or "").strip(" -–—")
    return cleaned[:max_chars].strip()


def fallback_seo(topic: str) -> dict:
    """Rich SEO metadata when NotebookLM returns non-JSON."""
    niche = load_json(CONFIG / "niche.json") if (CONFIG / "niche.json").exists() else {}
    channel = niche.get("name", "Criminally Drawn")
    tagline = niche.get("tagline", "Real cases. Drawn cold.")
    title = sanitize_seo_title(topic)
    description = (
        f"{tagline}\n\n"
        f"A cold documentary retelling of {topic} — minimal 2D animation, "
        f"public-source facts, no gore graphics.\n\n"
        f"In this video:\n"
        f"• What happened, in order\n"
        f"• The people involved\n"
        f"• How the case unfolded\n\n"
        f"If this kept you watching, subscribe to {channel} for the next case."
    )
    return {
        "title": title,
        "description": description,
        "tags": [
            "true crime",
            "documentary",
            "criminally drawn",
            "real crime story",
            "cold case",
        ],
        "hashtags": ["#truecrime", "#criminallydrawn"],
    }
