"""Load bootstrap reference PNGs and register media_ids with FlowKit."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from flowkit_client import FlowKitClient


def load_manifest(refs_dir: Path) -> dict[str, dict[str, Any]]:
    manifest_path = refs_dir / "manifest.json"
    if not manifest_path.exists():
        raise FileNotFoundError(f"Missing {manifest_path} — bootstrap reference images first")
    data = json.loads(manifest_path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("manifest.json must be an object keyed by entity id")
    return data


def upload_references(
    refs_dir: Path, client: FlowKitClient | None = None, project_id: str = ""
) -> dict[str, str]:
    """Upload cached PNGs; returns entity_id -> media_id for this session."""
    client = client or FlowKitClient()
    manifest = load_manifest(refs_dir)
    media_ids: dict[str, str] = {}

    for entity_id, meta in manifest.items():
        filename = meta.get("file")
        if not filename:
            raise ValueError(f"Entity {entity_id} missing file in manifest")
        path = refs_dir / filename
        if not path.exists():
            raise FileNotFoundError(f"Reference image not found: {path}")
        media_ids[entity_id] = client.upload_image(path, project_id=project_id)

    return media_ids


def refs_dir_from_env() -> Path:
    return Path(os.environ.get("REFERENCE_IMAGES_DIR", "./config/references")).resolve()
