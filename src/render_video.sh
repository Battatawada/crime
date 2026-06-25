#!/usr/bin/env bash
# Phase 4: render with captions + gentle Ken Burns
set -euo pipefail
OUT_DIR="${1:-output}"
python3 "$(dirname "$0")/render_video.py" "$OUT_DIR"
