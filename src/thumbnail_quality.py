"""YouTube thumbnail prompt hardening + letterbox cleanup."""

from __future__ import annotations

import re
from pathlib import Path

# Soft-reject NotebookLM prompts that recreate the bad JonBenét-style collage.
_WEAK_THUMB_PATTERNS = re.compile(
    r"question\s*mark|\bgiant\s+\?|\bglowing\s+\?|"
    r"forensic\s+evidence|evidence\s+folder|readable\s+text|"
    r"top\s+third\s+empty|leave\s+the\s+top|"
    r"\bchild\b|\bchildren\b|\bminor\b|\bgirl\b|\bboy\b|"
    r"pageant|young\s+blonde|school[-\s]?age",
    re.IGNORECASE,
)

_CHILD_SAFE_STRIP = re.compile(
    r"\b(child|children|minor|girl|boy|pageant|toddler|infant|kid|kids)\b",
    re.IGNORECASE,
)

THUMB_QUALITY_SUFFIX = (
    ", edge-to-edge 16:9 YouTube thumbnail filling the entire frame, "
    "no white borders, no letterboxing, no empty bars, no polaroid frame, "
    "one bold large focal subject readable on a phone, high contrast cinematic lighting, "
    "matte black atmosphere with depth, no readable text, no logos, no watermarks, "
    "no giant question marks, no children, no minors"
)

THUMB_SAFE_FALLBACK = (
    "Edge-to-edge 16:9 YouTube thumbnail filling the entire frame with no white borders "
    "and no letterboxing. Cinematic night exterior of a two-story house filling the frame, "
    "one lit upstairs window, heavy rain on glass, cold blue rim light and a thin red accent, "
    "deep shadows, documentary still photography look, one dominant subject, "
    "no people faces required, no readable text, no logos, no watermarks, no children"
)

MIN_THUMB_BYTES = 80_000


def is_weak_thumbnail_prompt(prompt: str) -> bool:
    p = (prompt or "").strip()
    if len(p.split()) < 40:
        return True
    return bool(_WEAK_THUMB_PATTERNS.search(p))


def sanitize_thumbnail_prompt(prompt: str, *, title: str = "", topic: str = "") -> str:
    cleaned = " ".join(str(prompt or "").split()).strip()
    cleaned = _CHILD_SAFE_STRIP.sub("adult", cleaned)
    cleaned = re.sub(r"(?i)\bgiant\s+glowing\s+white\s+question\s+mark\b", "harsh spotlight", cleaned)
    cleaned = re.sub(r"(?i)\bquestion\s+mark\b", "harsh spotlight", cleaned)
    cleaned = re.sub(r"(?i)\bforensic evidence\b", "blurred paperwork", cleaned)
    if is_weak_thumbnail_prompt(cleaned):
        mood = " ".join(x for x in (title, topic) if x).strip()
        cleaned = THUMB_SAFE_FALLBACK
        if mood:
            cleaned += f", mood inspired by: {mood[:120]}"
    if "edge-to-edge" not in cleaned.lower():
        cleaned += THUMB_QUALITY_SUFFIX
    return cleaned


def crop_thumbnail_letterbox(path: Path, *, white_thresh: int = 245, dark_thresh: int = 12) -> bool:
    """
    Crop near-solid white/black bars often baked into bad generations.
    Returns True if the file was rewritten.
    """
    try:
        from PIL import Image
    except ImportError:
        return False
    if not path.is_file():
        return False
    im = Image.open(path).convert("RGB")
    w, h = im.size
    if w < 64 or h < 64:
        return False
    px = im.load()
    assert px is not None

    def row_is_bar(y: int) -> bool:
        whites = darks = 0
        step = max(1, w // 80)
        n = 0
        for x in range(0, w, step):
            r, g, b = px[x, y]
            if r >= white_thresh and g >= white_thresh and b >= white_thresh:
                whites += 1
            if r <= dark_thresh and g <= dark_thresh and b <= dark_thresh:
                darks += 1
            n += 1
        return (whites / n) >= 0.92 or (darks / n) >= 0.97

    def col_is_bar(x: int) -> bool:
        whites = darks = 0
        step = max(1, h // 80)
        n = 0
        for y in range(0, h, step):
            r, g, b = px[x, y]
            if r >= white_thresh and g >= white_thresh and b >= white_thresh:
                whites += 1
            if r <= dark_thresh and g <= dark_thresh and b <= dark_thresh:
                darks += 1
            n += 1
        return (whites / n) >= 0.92 or (darks / n) >= 0.97

    top = 0
    while top < h // 3 and row_is_bar(top):
        top += 1
    bottom = h - 1
    while bottom > (2 * h) // 3 and row_is_bar(bottom):
        bottom -= 1
    left = 0
    while left < w // 3 and col_is_bar(left):
        left += 1
    right = w - 1
    while right > (2 * w) // 3 and col_is_bar(right):
        right -= 1

    if top <= 2 and bottom >= h - 3 and left <= 2 and right >= w - 3:
        return False
    if right - left < w * 0.45 or bottom - top < h * 0.45:
        return False

    cropped = im.crop((left, top, right + 1, bottom + 1))
    # Normalize to 1280x720 for YouTube custom thumbs
    cropped = cropped.resize((1280, 720), Image.Resampling.LANCZOS)
    cropped.save(path, format="PNG", optimize=True)
    return True


def thumbnail_meets_quality(path: Path, *, min_bytes: int = MIN_THUMB_BYTES) -> bool:
    if not path.is_file():
        return False
    if path.stat().st_size < min_bytes:
        return False
    try:
        from PIL import Image

        with Image.open(path) as im:
            w, h = im.size
        if w < 640 or h < 360:
            return False
        # Reject extreme non-16:9 canvases that still have huge bars after crop attempt
        ratio = w / h
        if ratio < 1.2 or ratio > 2.2:
            return False
    except Exception:
        return path.stat().st_size >= min_bytes
    return True
