#!/usr/bin/env python3
"""
Phase 2 — edge-tts (step 4 + timing for step 7)

  4. Voiceover from cleaned script (no metadata, no citation markers)
  7. Per-scene audio + scene_durations.json + captions.srt (word-synced)
"""

from __future__ import annotations

import argparse
import asyncio
import os
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import edge_tts

from captions import merge_srt_blocks
from common import CONFIG, clean_script_for_tts, load_json, save_json, split_script_for_scenes


async def synthesize_with_captions(
    text: str, voice: str, rate: str, dest: Path
) -> str:
    """Synthesize MP3 and return SRT block for this segment (no timestamps in spoken text)."""
    if not text.strip():
        dest.write_bytes(b"")
        return ""
    communicate = edge_tts.Communicate(text, voice, rate=rate)
    submaker = edge_tts.SubMaker()
    with dest.open("wb") as audio_file:
        async for chunk in communicate.stream():
            if chunk["type"] == "audio":
                audio_file.write(chunk["data"])
            elif chunk["type"] == "WordBoundary":
                submaker.feed(chunk)
    return submaker.get_srt()


def concat_audio(parts: list[Path], output: Path) -> None:
    valid = [p for p in parts if p.exists() and p.stat().st_size > 0]
    if not valid:
        raise ValueError("No audio segments to concatenate")
    list_file = output.parent / "_concat_list.txt"
    with list_file.open("w", encoding="utf-8") as f:
        for p in valid:
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


async def run_phase(
    input_dir: Path,
    output_dir: Path,
    voice: str,
    rate: str,
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
    clock = 0.0

    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        for i, item in enumerate(scenes_meta):
            sid = int(item["scene_id"])
            text = segments[i] if i < len(segments) else ""
            part = tmp_path / f"scene_{sid:02d}.mp3"
            srt = await synthesize_with_captions(text, voice, rate, part)
            dur = probe_duration(part)
            durations.append(
                {"scene_id": sid, "duration_sec": round(dur, 3), "file": f"scene_{sid:02d}.png"}
            )
            part_files.append(part)
            srt_blocks.append(srt)
            offsets.append(clock)
            clock += dur

        concat_audio(part_files, narration)

    save_json(output_dir / "scene_durations.json", durations)
    srt_full = merge_srt_blocks(srt_blocks, offsets)
    (output_dir / "captions.srt").write_text(srt_full, encoding="utf-8")

    save_json(
        output_dir / "script_segments.json",
        [{"scene_id": int(s["scene_id"]), "text": segments[i] if i < len(segments) else ""}
         for i, s in enumerate(scenes_meta)],
    )

    meta = load_json(input_dir / "metadata.json") if (input_dir / "metadata.json").exists() else {}
    meta["total_audio_sec"] = round(sum(d["duration_sec"] for d in durations), 3)
    meta["tts_voice"] = voice
    target = meta.get("duration_minutes", 0) * 60
    if target:
        meta["duration_drift_sec"] = round(meta["total_audio_sec"] - target, 1)
    save_json(output_dir / "metadata.json", meta)


def main() -> None:
    parser = argparse.ArgumentParser(description="Phase 2: edge-tts")
    parser.add_argument("--input", type=Path, default=Path("output"))
    parser.add_argument("--output", type=Path, default=Path("output"))
    parser.add_argument("--pipeline", type=Path, default=CONFIG / "pipeline.json")
    parser.add_argument("--voice", default=None)
    parser.add_argument("--rate", default=None)
    args = parser.parse_args()

    pipeline = load_json(args.pipeline) if args.pipeline.exists() else {}
    voice = args.voice or os.environ.get("TTS_VOICE") or pipeline.get("tts_voice", "en-US-ChristopherNeural")
    rate = args.rate or os.environ.get("TTS_RATE") or pipeline.get("tts_rate", "-5%")

    try:
        asyncio.run(run_phase(args.input, args.output, voice, rate))
    except Exception as exc:
        print(exc, file=sys.stderr)
        sys.exit(1)
    print(f"Wrote narration.mp3 + captions.srt + scene_durations.json -> {args.output}")


if __name__ == "__main__":
    main()
