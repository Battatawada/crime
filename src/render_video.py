#!/usr/bin/env python3
"""Phase 4 — Ken Burns render with bottom captions (captions do not zoom)."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from captions import render_caption_png, scene_caption_phrase
from common import clean_script_for_tts, load_json

FPS = 30
BG_COLOR = "0xF5F0E8"
MAX_ZOOM = 1.06
CAPTION_BOTTOM_MARGIN = 48


def _run(cmd: list[str]) -> None:
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(result.stderr or result.stdout or f"Command failed: {' '.join(cmd)}")


def render_clip(
    img: Path,
    dur: float,
    scene_id: int,
    caption_png: Path | None,
    dest: Path,
    *,
    fps: int = FPS,
    max_zoom: float = MAX_ZOOM,
) -> None:
    frames = max(1, int(dur * fps))
    zstep = (max_zoom - 1.0) / frames
    if scene_id % 2 == 0:
        zexpr = f"max(1.0,{max_zoom}-on*{zstep:.7f})"
    else:
        zexpr = f"min({max_zoom},1+on*{zstep:.7f})"

    base_vf = (
        f"scale=1920:1080:force_original_aspect_ratio=decrease,"
        f"pad=1920:1080:(ow-iw)/2:(oh-ih)/2:color={BG_COLOR},"
        f"zoompan=z='{zexpr}':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':"
        f"d={frames}:s=1920x1080:fps={fps}"
    )

    if caption_png and caption_png.exists():
        work = dest.parent
        kb = work / f"_kb_{dest.stem}.mp4"
        _run(
            [
                "ffmpeg", "-y", "-loop", "1", "-i", str(img),
                "-vf", base_vf, "-t", str(dur), "-pix_fmt", "yuv420p", str(kb),
            ]
        )
        # Caption overlay fixed at bottom — does not zoom with Ken Burns.
        _run(
            [
                "ffmpeg", "-y", "-i", str(kb), "-i", str(caption_png),
                "-filter_complex",
                f"[1:v]format=rgba[c];[0:v][c]overlay=(W-w)/2:H-h-{CAPTION_BOTTOM_MARGIN}",
                "-t", str(dur), "-pix_fmt", "yuv420p", str(dest),
            ]
        )
        kb.unlink(missing_ok=True)
    else:
        _run(
            [
                "ffmpeg", "-y", "-loop", "1", "-i", str(img),
                "-vf", base_vf, "-t", str(dur), "-pix_fmt", "yuv420p", str(dest),
            ]
        )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("output_dir", type=Path, default=Path("output"), nargs="?")
    parser.add_argument("--max-zoom", type=float, default=MAX_ZOOM)
    args = parser.parse_args()

    out = args.output_dir
    img_dir = out / "images"
    audio = out / "narration.mp3"
    durations_path = out / "scene_durations.json"
    final = out / "final_video.mp4"
    work = out / "_ffmpeg_work"
    work.mkdir(parents=True, exist_ok=True)

    for old in work.glob("*.mp4"):
        old.unlink()

    if not audio.exists() or not durations_path.exists():
        sys.exit("Missing narration.mp3 or scene_durations.json")

    durations = load_json(durations_path)
    segments_path = out / "script_segments.json"
    segments: dict[int, str] = {}
    if segments_path.exists():
        for row in load_json(segments_path):
            segments[int(row["scene_id"])] = clean_script_for_tts(row.get("text", ""))

    caption_dir = work / "captions"
    caption_dir.mkdir(exist_ok=True)
    clips: list[Path] = []

    for item in durations:
        scene_id = int(item["scene_id"])
        dur = float(item["duration_sec"])
        img = img_dir / item.get("file", f"scene_{scene_id:02d}.png")
        if not img.exists():
            sys.exit(f"Missing image {img}")

        seg_text = segments.get(scene_id, "")
        phrase, hi = scene_caption_phrase(seg_text)
        cap_png = caption_dir / f"scene_{scene_id:02d}.png"
        render_caption_png(phrase, hi, cap_png)

        clip = work / f"clip_{scene_id:02d}.mp4"
        render_clip(img, dur, scene_id, cap_png, clip, max_zoom=args.max_zoom)
        clips.append(clip)

    list_file = work / "concat.txt"
    with list_file.open("w", encoding="utf-8") as f:
        for c in clips:
            f.write(f"file '{c.resolve().as_posix()}'\n")

    video_only = work / "video_only.mp4"
    _run(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(list_file), "-c", "copy", str(video_only)])
    _run(
        [
            "ffmpeg", "-y", "-i", str(video_only), "-i", str(audio),
            "-c:v", "libx264", "-c:a", "aac", "-pix_fmt", "yuv420p",
            "-shortest", str(final),
        ]
    )
    print(f"Wrote {final}")


if __name__ == "__main__":
    main()
