#!/usr/bin/env python3
"""One-shot: regenerate thumbnail.png for a completed run (quality-hardened)."""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, "/opt/niche/vps")
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "vps"))

from flowkit_client import FlowKitClient  # noqa: E402
from thumbnail_quality import (  # noqa: E402
    MIN_THUMB_BYTES,
    crop_thumbnail_letterbox,
    sanitize_thumbnail_prompt,
    thumbnail_meets_quality,
)

RUN_ID = sys.argv[1] if len(sys.argv) > 1 else "20260714-135855"
RUN = Path(f"/opt/niche/runs/{RUN_ID}")
STATE = json.loads((RUN / "state.json").read_text(encoding="utf-8"))
THUMB_META = json.loads((RUN / "thumbnail.json").read_text(encoding="utf-8"))

prompt = sanitize_thumbnail_prompt(
    str(THUMB_META.get("prompt", "")),
    title=str(THUMB_META.get("title", "")),
    topic=str(THUMB_META.get("topic", "")),
)
THUMB_META["prompt"] = prompt
THUMB_META["prompt_note"] = "quality-hardened sanitize_thumbnail_prompt"
(RUN / "thumbnail.json").write_text(json.dumps(THUMB_META, indent=2), encoding="utf-8")

project_id = STATE["project_id"]
ref_media = STATE.get("ref_media") or {}
entity_refs = THUMB_META.get("entity_refs") or []
media_ids = [ref_media[r] for r in entity_refs if r in ref_media]
dest = RUN / "images" / "thumbnail.png"

client = FlowKitClient()
client.ensure_ready(wait_sec=120)
url, mid = client.generate_scene_image(
    project_id=project_id,
    scene_id="thumbnail",
    video_id=project_id,
    prompt=prompt,
    ref_media_ids=media_ids,
    orientation="landscape",
)
client.download_url(url, dest)
crop_thumbnail_letterbox(dest)
if not thumbnail_meets_quality(dest, min_bytes=MIN_THUMB_BYTES):
    raise SystemExit(f"thumbnail failed quality gate: {dest.stat().st_size if dest.exists() else 0}")
STATE["thumbnail_ready"] = True
(RUN / "state.json").write_text(json.dumps(STATE, indent=2), encoding="utf-8")
print(f"OK thumbnail.png {dest.stat().st_size} bytes media_id={mid}")
