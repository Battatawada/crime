#!/usr/bin/env python3
"""Phase 4 — Static slides + word-synced karaoke captions (captions fixed at bottom, no zoom)."""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from captions import estimate_word_timings, write_scene_karaoke_ass
from common import load_json

FPS = 30
BG_COLOR = "0xF5F0E8"
MAX_ZOOM = 1.0  # 1.0 = no Ken Burns zoom


def _run(cmd: list[str]) -> None:
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(result.stderr or result.stdout or f"Command failed: {' '.join(cmd)}")


def _ass_filter_path(ass_path: Path, fonts_dir: Path | None) -> str:
    """Escape ASS path for ffmpeg filter."""
    p = ass_path.resolve().as_posix().replace(":", "\\:")
    if fonts_dir and fonts_dir.is_dir():
        fd = fonts_dir.resolve().as_posix().replace(":", "\\:")
        return f"ass={p}:fontsdir={fd}"
    return f"ass={p}"


def render_clip(
    img: Path,
    dur: float,
    ass_path: Path | None,
    dest: Path,
    *,
    fps: int = FPS,
    max_zoom: float = MAX_ZOOM,
    fonts_dir: Path | None = None,
) -> None:
    # Letterbox/pad only — no zoompan unless max_zoom > 1.0
    if max_zoom <= 1.0:
        base_vf = (
            f"scale=1920:1080:force_original_aspect_ratio=decrease,"
            f"pad=1920:1080:(ow-iw)/2:(oh-ih)/2:color={BG_COLOR},"
            f"fps={fps}"
        )
        _run(
            [
                "ffmpeg", "-y", "-loop", "1", "-i", str(img),
                "-vf", base_vf, "-t", str(dur), "-pix_fmt", "yuv420p", str(dest),
            ]
        )
        if not (ass_path and ass_path.exists()):
            return
        ass_esc = _ass_filter_path(ass_path, fonts_dir)
        captioned = dest.parent / f"_cap_{dest.name}"
        _run(
            [
                "ffmpeg", "-y", "-i", str(dest),
                "-vf", ass_esc,
                "-t", str(dur), "-pix_fmt", "yuv420p", str(captioned),
            ]
        )
        captioned.replace(dest)
        return

    frames = max(1, int(dur * fps))
    zstep = (max_zoom - 1.0) / frames
    zexpr = f"min({max_zoom},1+on*{zstep:.7f})"
    base_vf = (
        f"scale=1920:1080:force_original_aspect_ratio=decrease,"
        f"pad=1920:1080:(ow-iw)/2:(oh-ih)/2:color={BG_COLOR},"
        f"zoompan=z='{zexpr}':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':"
        f"d={frames}:s=1920x1080:fps={fps}"
    )
    kb = dest.parent / f"_kb_{dest.stem}.mp4"
    _run(
        [
            "ffmpeg", "-y", "-loop", "1", "-i", str(img),
            "-vf", base_vf, "-t", str(dur), "-pix_fmt", "yuv420p", str(kb),
        ]
    )
    if ass_path and ass_path.exists():
        ass_esc = _ass_filter_path(ass_path, fonts_dir)
        _run(
            [
                "ffmpeg", "-y", "-i", str(kb),
                "-vf", ass_esc,
                "-t", str(dur), "-pix_fmt", "yuv420p", str(dest),
            ]
        )
    else:
        _run(["ffmpeg", "-y", "-i", str(kb), "-c", "copy", str(dest)])
    kb.unlink(missing_ok=True)


def _find_fonts_dir() -> Path | None:
    candidates = [
        Path("/usr/share/fonts/truetype/dejavu"),
        Path("/usr/share/fonts/truetype/liberation"),
        Path("C:/Windows/Fonts"),
    ]
    for c in candidates:
        if c.is_dir():
            return c
    return None


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("output_dir", type=Path, default=Path("output"), nargs="?")
    parser.add_argument("--max-zoom", type=float, default=None)
    args = parser.parse_args()

    out = args.output_dir
    img_dir = out / "images"
    audio = out / "narration.mp3"
    durations_path = out / "scene_durations.json"
    timings_path = out / "word_timings.json"
    final = out / "final_video.mp4"
    work = out / "_ffmpeg_work"
    work.mkdir(parents=True, exist_ok=True)

    pipeline_path = Path(__file__).resolve().parents[1] / "config" / "pipeline.json"
    pipeline = load_json(pipeline_path) if pipeline_path.exists() else {}
    max_zoom = args.max_zoom if args.max_zoom is not None else float(pipeline.get("max_zoom", MAX_ZOOM))
    fonts_dir = _find_fonts_dir()

    for old in work.glob("*.mp4"):
        old.unlink()

    if not audio.exists() or not durations_path.exists():
        sys.exit("Missing narration.mp3 or scene_durations.json")

    durations = load_json(durations_path)
    timings_by_scene: dict[int, list] = {}
    segments_by_scene: dict[int, str] = {}
    if timings_path.exists():
        for row in load_json(timings_path):
            timings_by_scene[int(row["scene_id"])] = row.get("words", [])
    seg_path = out / "script_segments.json"
    if seg_path.exists():
        for row in load_json(seg_path):
            segments_by_scene[int(row["scene_id"])] = row.get("text", "")

    ass_dir = work / "ass"
    ass_dir.mkdir(exist_ok=True)
    clips: list[Path] = []

    for item in durations:
        scene_id = int(item["scene_id"])
        dur = float(item["duration_sec"])
        img = img_dir / item.get("file", f"scene_{scene_id:02d}.png")
        if not img.exists():
            sys.exit(f"Missing image {img}")

        words = timings_by_scene.get(scene_id, [])
        if not words:
            words = estimate_word_timings(segments_by_scene.get(scene_id, ""), dur)

        ass_path = ass_dir / f"scene_{scene_id:02d}.ass"
        ass_written = write_scene_karaoke_ass(words, ass_path, duration=dur)

        clip = work / f"clip_{scene_id:02d}.mp4"
        render_clip(
            img, dur,
            ass_path if ass_written else None,
            clip,
            max_zoom=max_zoom,
            fonts_dir=fonts_dir,
        )
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
