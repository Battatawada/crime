"""HTTP client for local FlowKit agent (port 8100)."""

from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Any

import httpx


class FlowKitClient:
    def __init__(self, base_url: str | None = None, timeout: float = 120.0) -> None:
        self.base_url = (base_url or os.environ.get("FLOWKIT_BASE_URL", "http://127.0.0.1:8100")).rstrip("/")
        self.timeout = timeout

    def health(self) -> dict[str, Any]:
        with httpx.Client(timeout=self.timeout) as client:
            resp = client.get(f"{self.base_url}/health")
            resp.raise_for_status()
            return resp.json()

    def ensure_ready(self, wait_sec: int = 120) -> None:
        deadline = time.time() + wait_sec
        last_error = ""
        while time.time() < deadline:
            try:
                data = self.health()
                if data.get("extension_connected"):
                    return
                last_error = "extension not connected"
            except Exception as exc:  # noqa: BLE001
                last_error = str(exc)
            time.sleep(5)
        raise RuntimeError(f"FlowKit not ready: {last_error}")

    def create_project(self, title: str, story: str = "") -> str:
        payload = {"title": title, "story": story}
        with httpx.Client(timeout=self.timeout) as client:
            resp = client.post(f"{self.base_url}/api/projects", json=payload)
            resp.raise_for_status()
            data = resp.json()
            return data["id"] if "id" in data else data["project"]["id"]

    def upload_image(self, path: Path) -> str:
        with httpx.Client(timeout=self.timeout) as client:
            with path.open("rb") as f:
                resp = client.post(
                    f"{self.base_url}/api/upload-image",
                    files={"file": (path.name, f, "image/png")},
                )
            resp.raise_for_status()
            data = resp.json()
            return data.get("media_id") or data.get("mediaId") or data["id"]

    def submit_request(self, body: dict[str, Any]) -> dict[str, Any]:
        with httpx.Client(timeout=self.timeout) as client:
            resp = client.post(f"{self.base_url}/api/requests", json=body)
            resp.raise_for_status()
            return resp.json()

    def poll_request(self, request_id: str, timeout_sec: int = 300) -> dict[str, Any]:
        deadline = time.time() + timeout_sec
        while time.time() < deadline:
            with httpx.Client(timeout=self.timeout) as client:
                resp = client.get(f"{self.base_url}/api/requests/{request_id}")
                resp.raise_for_status()
                data = resp.json()
            status = (data.get("status") or data.get("state") or "").lower()
            if status in {"completed", "complete", "done", "success"}:
                return data
            if status in {"failed", "error"}:
                raise RuntimeError(data.get("error_message") or data.get("error") or "FlowKit request failed")
            time.sleep(5)
        raise TimeoutError(f"Request {request_id} timed out after {timeout_sec}s")

    def download_url(self, url: str, dest: Path) -> None:
        dest.parent.mkdir(parents=True, exist_ok=True)
        with httpx.Client(timeout=self.timeout, follow_redirects=True) as client:
            resp = client.get(url)
            resp.raise_for_status()
            dest.write_bytes(resp.content)

    def generate_scene_image(
        self,
        *,
        project_id: str,
        scene_id: str,
        video_id: str,
        prompt: str,
        ref_media_ids: list[str],
        orientation: str = "landscape",
    ) -> tuple[str, str]:
        body: dict[str, Any] = {
            "type": "GENERATE_IMAGE",
            "project_id": project_id,
            "scene_id": scene_id,
            "video_id": video_id,
            "orientation": orientation,
            "prompt": prompt,
        }
        if ref_media_ids:
            body["image_inputs"] = [{"media_id": mid, "type": "IMAGE_INPUT_TYPE_REFERENCE"} for mid in ref_media_ids]

        submitted = self.submit_request(body)
        request_id = submitted.get("id") or submitted.get("request_id")
        if not request_id:
            # Synchronous response
            result = submitted
        else:
            result = self.poll_request(str(request_id))

        output = result.get("data") or result.get("output") or result
        image_url = output.get("image_url") or output.get("imageUrl") or output.get("url")
        media_id = output.get("media_id") or output.get("mediaId") or ""
        if not image_url:
            raise RuntimeError(f"No image_url in FlowKit response: {result}")
        return str(image_url), str(media_id)
