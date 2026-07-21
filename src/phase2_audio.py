#!/usr/bin/env python3
"""
Phase 2 — Azure Neural TTS (primary) + edge-tts fallback.

  Per-scene: split into chunks (rate/pitch/volume + voice), concat, word timings.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, Literal

sys.path.insert(0, str(Path(__file__).resolve().parent))

import edge_tts

import azure_tts
from captions import attach_punctuation_from_text, merge_srt_blocks, resolve_voice
from common import CONFIG, clean_script_for_tts, load_json, save_json, split_script_for_scenes
from tts_narration import (
    TtsChunk,
    load_voice_pool,
    merge_for_azure_economy,
    offset_word_timings,
    plan_tts_chunks,
)

MAX_TTS_RETRIES = 4
EMPTY_SCENE_SEC = 0.35
INTER_CHUNK_PAUSE_SEC = 0.14

TtsProvider = Literal["azure", "edge"]
_active_provider: TtsProvider | None = None


def _preferred_provider(pipeline: dict[str, Any]) -> TtsProvider:
    pref = str(pipeline.get("tts_provider", "azure")).lower()
    if pref == "edge":
        return "edge"
    if pref == "azure" and azure_tts.is_configured():
        return "azure"
    if azure_tts.is_configured():
        return "azure"
    return "edge"


def _get_provider(pipeline: dict[str, Any]) -> TtsProvider:
    global _active_provider
    if _active_provider is None:
        _active_provider = _preferred_provider(pipeline)
    return _active_provider


def _fallback_to_edge(reason: str) -> None:
    global _active_provider
    if _active_provider != "edge":
        print(f"Phase 2: falling back to edge-tts ({reason})", flush=True)
        _active_provider = "edge"


def write_silent_mp3(dest: Path, duration: float = EMPTY_SCENE_SEC) -> None:
    subprocess.run(
        [
            "ffmpeg", "-y", "-f", "lavfi", "-i", "anullsrc=r=24000:cl=mono",
            "-t", str(duration), "-c:a", "libmp3lame", "-q:a", "9", str(dest),
        ],
        check=True,
        capture_output=True,
    )


async def synthesize_chunk_edge(
    chunk: TtsChunk,
    dest: Path,
) -> tuple[str, list[dict[str, Any]]]:
    """Synthesize one prosody/voice chunk via edge-tts; return SRT + word timings."""
    if not chunk.text.strip():
        write_silent_mp3(dest, EMPTY_SCENE_SEC)
        return "", []

    voice = resolve_voice(chunk.voice)
    last_err: Exception | None = None

    for attempt in range(MAX_TTS_RETRIES):
        communicate = edge_tts.Communicate(
            chunk.text,
            voice,
            rate=chunk.rate,
            volume=chunk.volume,
            pitch=chunk.pitch,
            boundary="WordBoundary",
        )
        submaker = edge_tts.SubMaker()
        words: list[dict[str, Any]] = []
        try:
            with dest.open("wb") as audio_file:
                async for event in communicate.stream():
                    if event["type"] == "audio":
                        audio_file.write(event["data"])
                    elif event["type"] == "WordBoundary":
                        submaker.feed(event)
                        start = event["offset"] / 10_000_000
                        duration = event["duration"] / 10_000_000
                        words.append(
                            {
                                "text": event["text"],
                                "start": round(start, 4),
                                "end": round(start + duration, 4),
                            }
                        )
            if dest.stat().st_size == 0:
                raise edge_tts.exceptions.NoAudioReceived("TTS produced empty audio file")
            return submaker.get_srt(), words
        except edge_tts.exceptions.NoAudioReceived as exc:
            last_err = exc
            dest.unlink(missing_ok=True)
            if attempt + 1 < MAX_TTS_RETRIES:
                await asyncio.sleep(1.5 * (attempt + 1))
                continue
            raise
        except Exception as exc:
            dest.unlink(missing_ok=True)
            last_err = exc
            transient = any(
                s in str(exc).lower()
                for s in ("timeout", "connect", "network", "503", "502", "429")
            )
            if transient and attempt + 1 < MAX_TTS_RETRIES:
                await asyncio.sleep(2.0 * (attempt + 1))
                continue
            raise

    raise last_err or RuntimeError("TTS failed")


async def synthesize_chunk(
    chunk: TtsChunk,
    dest: Path,
    *,
    pipeline: dict[str, Any],
) -> tuple[str, list[dict[str, Any]]]:
    provider = _get_provider(pipeline)
    if provider == "azure":
        for attempt in range(MAX_TTS_RETRIES):
            try:
                return await asyncio.to_thread(azure_tts.synthesize_chunk, chunk, dest)
            except Exception as exc:
                dest.unlink(missing_ok=True)
                transient = azure_tts.is_transient_error(exc)
                if transient and attempt + 1 < MAX_TTS_RETRIES:
                    await asyncio.sleep(1.5 * (attempt + 1))
                    continue
                _fallback_to_edge(str(exc))
                break
    return await synthesize_chunk_edge(chunk, dest)


def concat_audio(parts: list[Path], output: Path) -> None:
    if not parts:
        raise ValueError("No audio segments to concatenate")
    missing = [p for p in parts if not p.exists() or p.stat().st_size == 0]
    if missing:
        raise ValueError(f"Missing or empty audio segments: {missing}")
    list_file = output.parent / "_concat_list.txt"
    with list_file.open("w", encoding="utf-8") as f:
        for p in parts:
            f.write(f"file '{p.resolve().as_posix()}'\n")
    subprocess.run(
        ["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(list_file), "-c", "copy", str(output)],
        check=True,
        capture_output=True,
    )
    list_file.unlink(missing_ok=True)


def probe_duration(path: Path) -> float:
    if not path.exists() or path.stat().st_size == 0:
        return 0.5
    result = subprocess.run(
        [
            "ffprobe", "-v", "error",
            "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1",
            str(path),
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    return max(0.5, float(result.stdout.strip()))


def estimate_word_timings(text: str, duration: float) -> list[dict]:
    tokens = __import__("re").findall(r"\S+", text.strip())
    if not tokens or duration <= 0:
        return []
    weights = [max(1, len(__import__("re").sub(r"[^\w']", "", t))) for t in tokens]
    total = sum(weights)
    t = 0.0
    out: list[dict] = []
    for token, weight in zip(tokens, weights):
        span = duration * (weight / total)
        out.append({"text": token, "start": round(t, 4), "end": round(t + span, 4)})
        t += span
    return out


def _apply_pipeline_env(pipeline: dict[str, Any]) -> None:
    if pipeline.get("tts_azure_style_degree") is not None:
        os.environ["TTS_AZURE_STYLE_DEGREE"] = str(pipeline["tts_azure_style_degree"])


async def synthesize_scene(
    text: str,
    *,
    scene_index: int,
    pool: dict[str, str],
    base_rate: str,
    modulation: bool,
    dest: Path,
    tmp_dir: Path,
    pipeline: dict[str, Any],
) -> tuple[str, list[dict], list[dict]]:
    """Synthesize one scene; may be multiple internal chunks."""
    if not text.strip():
        write_silent_mp3(dest, EMPTY_SCENE_SEC)
        return "", [], []

    chunks = plan_tts_chunks(
        text,
        scene_index=scene_index,
        pool=pool,
        base_rate=base_rate,
        modulation=modulation,
    )
    if (
        pipeline.get("tts_merge_chunks", True)
        and _get_provider(pipeline) == "azure"
        and len(chunks) > 1
    ):
        chunks = merge_for_azure_economy(chunks, scene_index=scene_index)
    if len(chunks) == 1:
        ch = chunks[0]
        srt, words = await synthesize_chunk(ch, dest, pipeline=pipeline)
        if words:
            words = attach_punctuation_from_text(words, ch.text)
        meta = [{"text": ch.text, "voice": ch.voice, "rate": ch.rate, "pitch": ch.pitch, "role": ch.role}]
        return srt, words, meta

    part_files: list[Path] = []
    srt_blocks: list[str] = []
    all_words: list[dict] = []
    chunk_meta: list[dict] = []
    clock = 0.0

    for i, ch in enumerate(chunks):
        part = tmp_dir / f"chunk_{scene_index}_{i}.mp3"
        srt, words = await synthesize_chunk(ch, part, pipeline=pipeline)
        dur = probe_duration(part)
        if ch.text.strip() and not words:
            words = estimate_word_timings(ch.text, dur)
        if words:
            words = attach_punctuation_from_text(words, ch.text)
            words = offset_word_timings(words, clock)
        part_files.append(part)
        srt_blocks.append(srt)
        all_words.extend(words)
        chunk_meta.append(
            {
                "text": ch.text,
                "voice": ch.voice,
                "rate": ch.rate,
                "pitch": ch.pitch,
                "volume": ch.volume,
                "role": ch.role,
            }
        )
        clock += dur
        if i + 1 < len(chunks):
            pause = tmp_dir / f"pause_{scene_index}_{i}.mp3"
            write_silent_mp3(pause, INTER_CHUNK_PAUSE_SEC)
            part_files.append(pause)
            clock += INTER_CHUNK_PAUSE_SEC

    concat_audio(part_files, dest)
    # Merge SRT with offsets for this scene only
    offsets = []
    run = 0.0
    for i, part in enumerate(part_files):
        if part.name.startswith("pause_"):
            run += INTER_CHUNK_PAUSE_SEC
            continue
        offsets.append(run)
        run += probe_duration(part)
    merged_srt = merge_srt_blocks(
        [srt_blocks[j] for j in range(len(srt_blocks))],
        offsets[: len(srt_blocks)],
    )
    return merged_srt, all_words, chunk_meta


async def run_phase(
    input_dir: Path,
    output_dir: Path,
    pool: dict[str, str],
    base_rate: str,
    modulation: bool,
    pipeline: dict[str, Any],
) -> None:
    script = clean_script_for_tts((input_dir / "script.txt").read_text(encoding="utf-8"))
    scenes_meta = load_json(input_dir / "scenes.json")
    if not script or not scenes_meta:
        raise ValueError("Need script.txt and scenes.json")

    segments_path = input_dir / "script_segments.json"
    if segments_path.exists():
        segments_data = load_json(segments_path)
        segments = [clean_script_for_tts(s.get("text", "")) for s in segments_data]
    else:
        segments = [clean_script_for_tts(t) for t in split_script_for_scenes(script, len(scenes_meta))]

    if len(segments) != len(scenes_meta):
        segments = [clean_script_for_tts(t) for t in split_script_for_scenes(script, len(scenes_meta))]

    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "script_clean.txt").write_text(script, encoding="utf-8")

    narration = output_dir / "narration.mp3"
    durations: list[dict] = []
    part_files: list[Path] = []
    srt_blocks: list[str] = []
    offsets: list[float] = []
    word_timings: list[dict] = []
    clock = 0.0

    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        for i, item in enumerate(scenes_meta):
            sid = int(item["scene_id"])
            text = segments[i] if i < len(segments) else ""
            part = tmp_path / f"scene_{sid:02d}.mp3"
            srt, words, chunk_meta = await synthesize_scene(
                text,
                scene_index=i,
                pool=pool,
                base_rate=base_rate,
                modulation=modulation,
                dest=part,
                tmp_dir=tmp_path,
                pipeline=pipeline,
            )
            dur = probe_duration(part)
            primary_voice = chunk_meta[0]["voice"] if chunk_meta else pool.get("narrator", "")
            durations.append(
                {
                    "scene_id": sid,
                    "duration_sec": round(dur, 3),
                    "file": f"scene_{sid:02d}.png",
                    "voice": primary_voice,
                    "tts_chunks": chunk_meta,
                }
            )
            word_timings.append(
                {"scene_id": sid, "voice": primary_voice, "words": words, "chunks": chunk_meta}
            )
            part_files.append(part)
            srt_blocks.append(srt)
            offsets.append(clock)
            clock += dur

        concat_audio(part_files, narration)

    save_json(output_dir / "scene_durations.json", durations)
    save_json(output_dir / "word_timings.json", word_timings)
    srt_full = merge_srt_blocks(srt_blocks, offsets)
    (output_dir / "captions.srt").write_text(srt_full, encoding="utf-8")

    save_json(
        output_dir / "script_segments.json",
        [{"scene_id": int(s["scene_id"]), "text": segments[i] if i < len(segments) else ""}
         for i, s in enumerate(scenes_meta)],
    )

    pipeline_path = CONFIG / "pipeline.json"
    pipeline_cfg = load_json(pipeline_path) if pipeline_path.exists() else {}
    end_cfg = pipeline_cfg.get("end_card", {})
    if end_cfg.get("enabled", True):
        end_script = end_cfg.get(
            "script",
            "If you want the next real case drawn the same way, subscribe to Criminally Drawn. "
            "I'm Jonty. Thank you for watching.",
        )
        end_voice = resolve_voice(end_cfg.get("voice", pool.get("narrator", "")))
        end_chunk = TtsChunk(
            text=end_script,
            voice=end_voice,
            rate="-8%",
            pitch="-2Hz",
            volume="+4%",
            role="outro",
            break_before_ms=200,
            break_after_ms=300,
        )
        end_path = output_dir / "end_card.mp3"
        await synthesize_chunk(end_chunk, end_path, pipeline=pipeline)
        save_json(
            output_dir / "end_card.json",
            {
                "enabled": True,
                "image": end_cfg.get("image", "config/end_card/subscribe.png"),
                "duration_sec": round(probe_duration(end_path), 3),
                "script": end_script,
            },
        )
        print(f"Wrote end_card.mp3 ({probe_duration(end_path):.1f}s)", flush=True)

    meta = load_json(input_dir / "metadata.json") if (input_dir / "metadata.json").exists() else {}
    meta["total_audio_sec"] = round(sum(d["duration_sec"] for d in durations), 3)
    meta["tts_voice_pool"] = pool
    meta["tts_modulation"] = modulation
    meta["tts_provider"] = _get_provider(pipeline)
    target = meta.get("duration_minutes", 0) * 60
    if target:
        drift = round(meta["total_audio_sec"] - target, 1)
        meta["duration_drift_sec"] = drift
    save_json(output_dir / "metadata.json", meta)


def main() -> None:
    parser = argparse.ArgumentParser(description="Phase 2: Azure TTS (edge-tts fallback)")
    parser.add_argument("--input", type=Path, default=Path("output"))
    parser.add_argument("--output", type=Path, default=Path("output"))
    parser.add_argument("--pipeline", type=Path, default=CONFIG / "pipeline.json")
    parser.add_argument("--rate", default=None)
    parser.add_argument("--no-modulation", action="store_true")
    args = parser.parse_args()

    pipeline = load_json(args.pipeline) if args.pipeline.exists() else {}
    _apply_pipeline_env(pipeline)
    pool = load_voice_pool(pipeline)
    modulation = pipeline.get("tts_modulation", True) and not args.no_modulation
    rate = args.rate or os.environ.get("TTS_RATE") or pipeline.get("tts_rate", "-5%")

    try:
        asyncio.run(run_phase(args.input, args.output, pool, rate, modulation, pipeline))
    except Exception as exc:
        print(exc, file=sys.stderr)
        sys.exit(1)
    provider = _get_provider(pipeline)
    print(
        f"Wrote narration.mp3 (provider={provider}, modulation={modulation}, voices={len(pool)}) "
        f"-> {args.output}",
        flush=True,
    )


if __name__ == "__main__":
    main()
