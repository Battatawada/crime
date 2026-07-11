"""Fetch a few Wikipedia/Wikimedia images for archival beats (YouTube-safe stills)."""

from __future__ import annotations

import json
import re
import urllib.parse
from pathlib import Path
from typing import Any

from common import httpx_get_json_with_retry


def _slug_query(topic: str) -> str:
    # Drop framing words; keep searchable case/killer name
    q = re.sub(
        r"(?i)\b(the full story|explained|documentary|case of|life of)\b",
        " ",
        topic,
    )
    return " ".join(q.split())[:120]


def search_wikipedia_titles(query: str, *, limit: int = 5) -> list[str]:
    url = (
        "https://en.wikipedia.org/w/api.php"
        f"?action=query&list=search&srsearch={urllib.parse.quote(query)}"
        f"&srlimit={limit}&format=json"
    )
    data = httpx_get_json_with_retry(
        url,
        headers={"User-Agent": "TrueCrimePipeline/1.0 (educational; local automation)"},
        timeout=30.0,
    )
    return [row["title"] for row in data.get("query", {}).get("search", []) if row.get("title")]


def page_thumbnail_url(title: str) -> str | None:
    url = (
        "https://en.wikipedia.org/api/rest_v1/page/summary/"
        + urllib.parse.quote(title.replace(" ", "_"), safe="")
    )
    try:
        data = httpx_get_json_with_retry(
            url,
            headers={"User-Agent": "TrueCrimePipeline/1.0 (educational; local automation)"},
            timeout=30.0,
        )
    except Exception:
        return None
    thumb = data.get("thumbnail") or {}
    src = thumb.get("source")
    if isinstance(src, str) and src.startswith("http"):
        # Prefer larger thumbnail when API gives width param
        return re.sub(r"/\d+px-", "/800px-", src)
    return None


def download_file(url: str, dest: Path) -> bool:
    import httpx

    dest.parent.mkdir(parents=True, exist_ok=True)
    try:
        with httpx.Client(timeout=60.0, follow_redirects=True) as client:
            r = client.get(
                url,
                headers={"User-Agent": "TrueCrimePipeline/1.0 (educational; local automation)"},
            )
            r.raise_for_status()
            if len(r.content) < 8_000:
                return False
            dest.write_bytes(r.content)
            return True
    except Exception:
        return False


def fetch_archival_images(topic: str, dest_dir: Path, *, count: int = 4) -> list[dict[str, Any]]:
    """Download up to `count` Wikipedia lead images for the topic."""
    dest_dir.mkdir(parents=True, exist_ok=True)
    query = _slug_query(topic)
    titles = search_wikipedia_titles(query, limit=max(count + 2, 5))
    if not titles:
        titles = [query]

    saved: list[dict[str, Any]] = []
    seen_urls: set[str] = set()
    for title in titles:
        if len(saved) >= count:
            break
        img_url = page_thumbnail_url(title)
        if not img_url or img_url in seen_urls:
            continue
        seen_urls.add(img_url)
        ext = ".jpg"
        if ".png" in img_url.lower():
            ext = ".png"
        dest = dest_dir / f"archival_{len(saved) + 1:02d}{ext}"
        if download_file(img_url, dest):
            saved.append({"file": dest.name, "source_title": title, "url": img_url})
    return saved


def assign_archival_to_scenes(
    scene_count: int,
    archival: list[dict[str, Any]],
    *,
    every_n: int = 8,
) -> dict[str, str]:
    """Map scene_id (str) -> archival filename for a light real-photo sprinkle."""
    if scene_count < 1 or not archival:
        return {}
    mapping: dict[str, str] = {}
    # Spread evenly; skip scene 1 (hook often animated)
    idxs = list(range(every_n, scene_count + 1, every_n))
    if not idxs and scene_count >= 3:
        idxs = [max(2, scene_count // 2)]
    for i, scene_id in enumerate(idxs):
        if i >= len(archival):
            break
        mapping[str(scene_id)] = archival[i]["file"]
    return mapping


def write_archival_plan(out_dir: Path, archival: list[dict[str, Any]], scene_map: dict[str, str]) -> Path:
    path = out_dir / "archival_plan.json"
    path.write_text(
        json.dumps({"images": archival, "scene_map": scene_map}, indent=2),
        encoding="utf-8",
    )
    return path
