#!/usr/bin/env python3
"""Smoke test FlowKit project -> upload -> generate-image."""
from __future__ import annotations

import sys
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "vps"))

from flowkit_client import FlowKitClient  # noqa: E402


def main() -> int:
    refs = Path("/opt/niche/config/references")
    client = FlowKitClient()
    client.ensure_ready()
    pid = client.create_project("smoke-test", "")
    print("project_id:", pid)
    mid = client.upload_image(refs / "character_A.png", project_id=pid)
    print("media_id:", mid)
    prompt = "Minimalist stick figure standing at a window, cream background, line art"
    url, out_mid = client.generate_scene_image(
        project_id=pid,
        scene_id="1",
        video_id=pid,
        prompt=prompt,
        ref_media_ids=[mid],
        orientation="landscape",
    )
    print("ok:", url[:80], out_mid)

    for bad in ("Answer: Total Parts: 5", ""):
        r = httpx.post(
            "http://127.0.0.1:8100/api/flow/generate-image",
            json={
                "prompt": bad,
                "project_id": pid,
                "aspect_ratio": "IMAGE_ASPECT_RATIO_LANDSCAPE",
            },
            timeout=120,
        )
        print("bad prompt", repr(bad), "->", r.status_code, r.text[:200])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
