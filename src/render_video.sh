#!/usr/bin/env bash
# Phase 4: Ken Burns ffmpeg render from scene PNGs + narration.mp3
set -euo pipefail

OUT_DIR="${1:-output}"
IMG_DIR="${OUT_DIR}/images"
AUDIO="${OUT_DIR}/narration.mp3"
DURATIONS="${OUT_DIR}/scene_durations.json"
FINAL="${OUT_DIR}/final_video.mp4"
WORK="${OUT_DIR}/_ffmpeg_work"
FPS=30

if [[ ! -f "$AUDIO" ]]; then
  echo "Missing $AUDIO" >&2
  exit 1
fi
if [[ ! -f "$DURATIONS" ]]; then
  echo "Missing $DURATIONS" >&2
  exit 1
fi

mkdir -p "$WORK"
rm -f "$WORK"/*.mp4 "$FINAL"

python3 - "$OUT_DIR" "$WORK" "$FPS" <<'PY'
import json
import subprocess
import sys
from pathlib import Path

out_dir = Path(sys.argv[1])
work = Path(sys.argv[2])
fps = int(sys.argv[3])
img_dir = out_dir / "images"
durations = json.loads((out_dir / "scene_durations.json").read_text(encoding="utf-8"))

clips = []
for i, item in enumerate(durations):
    scene_id = item["scene_id"]
    dur = float(item["duration_sec"])
    img = img_dir / item.get("file", f"scene_{scene_id:02d}.png")
    if not img.exists():
        raise SystemExit(f"Missing image {img}")
    frames = max(1, int(dur * fps))
    clip = work / f"clip_{scene_id:02d}.mp4"
    # Alternate zoom direction per scene
    zstep = "0.0015" if scene_id % 2 else "-0.0015"
    zexpr = f"min(max(zoom+({zstep}),1.0),1.5)" if scene_id % 2 else f"min(max(zoom+({zstep}),1.0),1.5)"
    vf = (
        f"scale=1920:1080:force_original_aspect_ratio=increase,"
        f"crop=1920:1080,"
        f"zoompan=z='{zexpr}':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':"
        f"d={frames}:s=1920x1080:fps={fps}"
    )
    subprocess.run(
        [
            "ffmpeg", "-y", "-loop", "1", "-i", str(img),
            "-vf", vf, "-t", str(dur), "-pix_fmt", "yuv420p", str(clip),
        ],
        check=True,
        capture_output=True,
    )
    clips.append(clip)

list_file = work / "concat.txt"
with list_file.open("w", encoding="utf-8") as f:
    for c in clips:
        f.write(f"file '{c.resolve().as_posix()}'\n")

video_only = work / "video_only.mp4"
subprocess.run(
    [
        "ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(list_file),
        "-c", "copy", str(video_only),
    ],
    check=True,
    capture_output=True,
)

final = out_dir / "final_video.mp4"
audio = out_dir / "narration.mp3"
subprocess.run(
    [
        "ffmpeg", "-y", "-i", str(video_only), "-i", str(audio),
        "-c:v", "libx264", "-c:a", "aac", "-pix_fmt", "yuv420p",
        "-shortest", str(final),
    ],
    check=True,
    capture_output=True,
)
print(f"Wrote {final}")
PY
