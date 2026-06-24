#!/usr/bin/env python3
"""
Phase 2 — edge-tts (step 4 + timing for step 7)

  4. Voiceover: story text only (no timestamps in output files)
  7. Per-scene audio chunks → scene_durations.json for ffmpeg sync
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

from common import load_json, save_json, split_script_for_scenes


async def synthesize(text: str, voice: str, dest: Path) -> None:
    if not text.strip():
        dest.write_bytes(b"")
        return
    communicate = edge_tts.Communicate(text, voice)
    await communicate.save(str(dest))


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


async def run_phase(input_dir: Path, output_dir: Path, voice: str) -> None:
    script = (input_dir / "script.txt").read_text(encoding="utf-8").strip()
    scenes_meta = load_json(input_dir / "scenes.json")
    if not script or not scenes_meta:
        raise ValueError("Need script.txt and scenes.json")

    segments_path = input_dir / "script_segments.json"
    if segments_path.exists():
        segments_data = load_json(segments_path)
        segments = [s["text"] for s in segments_data]
    else:
        segments = split_script_for_scenes(script, len(scenes_meta))

    if len(segments) != len(scenes_meta):
        segments = split_script_for_scenes(script, len(scenes_meta))

    output_dir.mkdir(parents=True, exist_ok=True)
    narration = output_dir / "narration.mp3"
    durations: list[dict] = []
    part_files: list[Path] = []

    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        for i, item in enumerate(scenes_meta):
            sid = int(item["scene_id"])
            text = segments[i] if i < len(segments) else ""
            part = tmp_path / f"scene_{sid:02d}.mp3"
            await synthesize(text, voice, part)
            dur = probe_duration(part)
            durations.append(
                {"scene_id": sid, "duration_sec": round(dur, 3), "file": f"scene_{sid:02d}.png"}
            )
            part_files.append(part)
        concat_audio(part_files, narration)

    save_json(output_dir / "scene_durations.json", durations)

    meta = load_json(input_dir / "metadata.json") if (input_dir / "metadata.json").exists() else {}
    meta["total_audio_sec"] = round(sum(d["duration_sec"] for d in durations), 3)
    target = meta.get("duration_minutes", 0) * 60
    if target:
        meta["duration_drift_sec"] = round(meta["total_audio_sec"] - target, 1)
    save_json(output_dir / "metadata.json", meta)


def main() -> None:
    parser = argparse.ArgumentParser(description="Phase 2: edge-tts")
    parser.add_argument("--input", type=Path, default=Path("output"))
    parser.add_argument("--output", type=Path, default=Path("output"))
    parser.add_argument("--voice", default=os.environ.get("TTS_VOICE", "en-US-GuyNeural"))
    args = parser.parse_args()

    try:
        asyncio.run(run_phase(args.input, args.output, args.voice))
    except Exception as exc:
        print(exc, file=sys.stderr)
        sys.exit(1)
    print(f"Wrote narration.mp3 + scene_durations.json -> {args.output}")


if __name__ == "__main__":
    main()
