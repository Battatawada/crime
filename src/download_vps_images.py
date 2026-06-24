#!/usr/bin/env python3
"""Download scene PNGs from VPS."""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import httpx

from common import load_json


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--output", type=Path, default=Path("output/images"))
    parser.add_argument("--durations", type=Path, default=Path("output/scene_durations.json"))
    args = parser.parse_args()

    base = os.environ.get("VPS_URL", "").rstrip("/")
    secret = os.environ.get("VPS_SECRET", "")
    if not base or not secret:
        sys.exit("Set VPS_URL and VPS_SECRET")

    headers = {"Authorization": f"Bearer {secret}"}
    args.output.mkdir(parents=True, exist_ok=True)

    if args.durations.exists():
        durations = load_json(args.durations)
        filenames = [d["file"] for d in durations]
    else:
        status = httpx.get(f"{base}/runs/{args.run_id}/status", headers=headers, timeout=30.0)
        status.raise_for_status()
        total = status.json().get("total_scenes", 20)
        filenames = [f"scene_{i:02d}.png" for i in range(1, total + 1)]

    for name in filenames:
        dest = args.output / name
        resp = httpx.get(
            f"{base}/runs/{args.run_id}/images/{name}",
            headers=headers,
            timeout=120.0,
        )
        resp.raise_for_status()
        dest.write_bytes(resp.content)
        print(f"saved {dest}")

    print(f"Downloaded {len(filenames)} images")


if __name__ == "__main__":
    main()
