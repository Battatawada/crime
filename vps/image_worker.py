"""VPS image worker — FastAPI service for sequential FlowKit generation."""

from __future__ import annotations

import asyncio
import os
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from phase3_sequential import run_generation_async

app = FastAPI(title="Niche Image Worker", version="0.1.0")

RUNS_DIR = Path(os.environ.get("RUNS_DIR", "./runs")).resolve()
WEBHOOK_SECRET = os.environ.get("WEBHOOK_SECRET", "")

_jobs: dict[str, asyncio.Task] = {}


class GeneratePayload(BaseModel):
    run_id: str
    scenes: list[dict[str, Any]] = Field(default_factory=list)
    entities: list[dict[str, Any]] = Field(default_factory=list)
    thumbnail: dict[str, Any] | None = None


def verify_auth(request: Request) -> None:
    if not WEBHOOK_SECRET:
        raise HTTPException(500, "WEBHOOK_SECRET not configured")
    auth = request.headers.get("Authorization", "")
    if auth != f"Bearer {WEBHOOK_SECRET}":
        raise HTTPException(401, "Unauthorized")


def _scenes_path(run_id: str) -> Path:
    return RUNS_DIR / run_id / "scenes.json"


def _save_scenes_payload(run_id: str, scenes: list[dict[str, Any]], entities: list[dict[str, Any]]) -> None:
    import json

    _scenes_path(run_id).write_text(
        json.dumps({"scenes": scenes, "entities": entities}, indent=2),
        encoding="utf-8",
    )


def _load_scenes_payload(run_id: str) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    import json

    path = _scenes_path(run_id)
    if not path.exists():
        raise HTTPException(404, "No saved scenes for this run — POST scenes.json with /generate")
    data = json.loads(path.read_text(encoding="utf-8"))
    return data.get("scenes", []), data.get("entities", [])


def _state_path(run_id: str) -> Path:
    return RUNS_DIR / run_id / "state.json"


def _read_state(run_id: str) -> dict[str, Any]:
    path = _state_path(run_id)
    if not path.exists():
        raise HTTPException(404, "Run not found")
    import json

    return json.loads(path.read_text(encoding="utf-8"))


async def _run_job(run_id: str, scenes: list[dict[str, Any]], entities: list[dict[str, Any]]) -> None:
    await run_generation_async(run_id, scenes, entities, RUNS_DIR)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "service": "niche-image-worker"}


@app.post("/generate")
async def generate(
    payload: GeneratePayload,
    _: None = Depends(verify_auth),
) -> dict[str, str]:
    import json

    run_id = payload.run_id
    if run_id in _jobs and not _jobs[run_id].done():
        return {"run_id": run_id, "status": "already_running"}

    RUNS_DIR.mkdir(parents=True, exist_ok=True)
    (RUNS_DIR / run_id / "images").mkdir(parents=True, exist_ok=True)

    scenes = payload.scenes
    entities = payload.entities
    if scenes:
        _save_scenes_payload(run_id, scenes, entities)
    else:
        scenes, entities = _load_scenes_payload(run_id)

    if payload.thumbnail:
        import json

        thumb_path = RUNS_DIR / run_id / "thumbnail.json"
        thumb_path.parent.mkdir(parents=True, exist_ok=True)
        thumb_path.write_text(json.dumps(payload.thumbnail, indent=2), encoding="utf-8")

    state_path = _state_path(run_id)
    if state_path.exists():
        existing = json.loads(state_path.read_text(encoding="utf-8"))
        ready = int(existing.get("images_ready", 0))
        total = int(existing.get("total_scenes", len(scenes)))
        if existing.get("status") == "complete":
            return {"run_id": run_id, "status": "already_complete"}
        if existing.get("status") == "failed" and ready > 0 and ready < total:
            existing["status"] = "pending"
            existing["error"] = None
            state_path.write_text(json.dumps(existing, indent=2), encoding="utf-8")
            task = asyncio.create_task(_run_job(run_id, scenes, entities))
            _jobs[run_id] = task
            return {"run_id": run_id, "status": "resumed", "images_ready": str(ready)}

    initial = {
        "run_id": run_id,
        "status": "pending",
        "phase": "queued",
        "total_scenes": len(scenes),
        "images_ready": 0,
        "current_scene": 0,
        "completed": [],
        "failed_scenes": [],
        "error": None,
    }
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(json.dumps(initial, indent=2), encoding="utf-8")

    task = asyncio.create_task(_run_job(run_id, scenes, entities))
    _jobs[run_id] = task
    return {"run_id": run_id, "status": "accepted"}


@app.post("/runs/{run_id}/resume")
async def resume_run(run_id: str, _: None = Depends(verify_auth)) -> dict[str, str]:
    """Resume a failed run using saved scenes.json on disk (skips completed images)."""
    import json

    if run_id in _jobs and not _jobs[run_id].done():
        return {"run_id": run_id, "status": "already_running"}

    scenes, entities = _load_scenes_payload(run_id)
    state_path = _state_path(run_id)
    if not state_path.exists():
        raise HTTPException(404, "Run not found")

    existing = json.loads(state_path.read_text(encoding="utf-8"))
    ready = int(existing.get("images_ready", 0))
    total = int(existing.get("total_scenes", len(scenes)))
    if existing.get("status") == "complete":
        return {"run_id": run_id, "status": "already_complete"}
    if ready >= total:
        return {"run_id": run_id, "status": "already_complete"}

    existing["status"] = "pending"
    existing["error"] = None
    state_path.write_text(json.dumps(existing, indent=2), encoding="utf-8")
    task = asyncio.create_task(_run_job(run_id, scenes, entities))
    _jobs[run_id] = task
    return {"run_id": run_id, "status": "resumed", "images_ready": str(ready)}


@app.get("/runs/{run_id}/status")
def run_status(run_id: str, _: None = Depends(verify_auth)) -> dict[str, Any]:
    return _read_state(run_id)


@app.get("/runs/{run_id}/images/{filename}")
def get_image(run_id: str, filename: str, _: None = Depends(verify_auth)) -> FileResponse:
    if ".." in filename or "/" in filename:
        raise HTTPException(400, "Invalid filename")
    path = RUNS_DIR / run_id / "images" / filename
    if not path.exists():
        raise HTTPException(404, "Image not found")
    return FileResponse(path, media_type="image/png")


def main() -> None:
    import uvicorn

    host = os.environ.get("NICHE_HOST", "0.0.0.0")
    port = int(os.environ.get("NICHE_PORT", "8765"))
    uvicorn.run("image_worker:app", host=host, port=port, reload=False)


if __name__ == "__main__":
    main()
