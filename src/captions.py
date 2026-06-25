"""On-screen caption PNGs and SRT generation."""

from __future__ import annotations

import re
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from common import clean_script_for_tts

FONT_CANDIDATES = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
    "C:/Windows/Fonts/arialbd.ttf",
]


def _load_font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    for path in FONT_CANDIDATES:
        if Path(path).exists():
            return ImageFont.truetype(path, size)
    return ImageFont.load_default()


def scene_caption_phrase(segment: str, max_words: int = 6) -> tuple[str, int]:
    """Short uppercase phrase + index of word to highlight in yellow."""
    clean = clean_script_for_tts(segment)
    if not clean:
        return "KEEP GOING", 0
    sentence = re.split(r"(?<=[.!?])\s+", clean)[0].strip()
    words = [w for w in re.findall(r"[A-Za-z0-9']+", sentence) if w][:max_words]
    if not words:
        words = clean.split()[:max_words]
    if not words:
        return "KEEP GOING", 0
    phrase = " ".join(words).upper()
    highlight = max(range(len(words)), key=lambda i: len(words[i]))
    return phrase, highlight


def render_caption_png(
    phrase: str,
    highlight_idx: int,
    dest: Path,
    *,
    width: int = 1920,
    height: int = 140,
    font_size: int = 52,
) -> Path:
    """White bold text, black outline, one yellow word — bottom overlay strip."""
    words = phrase.split()
    if not words:
        words = ["..."]
        highlight_idx = 0
    highlight_idx = max(0, min(highlight_idx, len(words) - 1))

    font = _load_font(font_size)
    img = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    stroke = 4
    spacing = 14
    sizes = [draw.textbbox((0, 0), w, font=font) for w in words]
    total_w = sum(s[2] - s[0] for s in sizes) + spacing * (len(words) - 1)
    x = (width - total_w) // 2
    y = (height - (sizes[0][3] - sizes[0][1])) // 2

    for i, word in enumerate(words):
        fill = (255, 220, 0, 255) if i == highlight_idx else (255, 255, 255, 255)
        for ox, oy in [(-stroke, 0), (stroke, 0), (0, -stroke), (0, stroke),
                       (-stroke, -stroke), (stroke, stroke), (-stroke, stroke), (stroke, -stroke)]:
            draw.text((x + ox, y + oy), word, font=font, fill=(0, 0, 0, 255))
        draw.text((x, y), word, font=font, fill=fill)
        x += sizes[i][2] - sizes[i][0] + spacing

    dest.parent.mkdir(parents=True, exist_ok=True)
    img.save(dest)
    return dest


def format_srt_time(seconds: float) -> str:
    ms = int(round(seconds * 1000))
    h, rem = divmod(ms, 3_600_000)
    m, rem = divmod(rem, 60_000)
    s, ms = divmod(rem, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def merge_srt_blocks(blocks: list[str], offsets: list[float]) -> str:
    """Merge per-scene SRT snippets with time offsets."""
    out: list[str] = []
    idx = 1
    for block, offset in zip(blocks, offsets):
        block = block.strip()
        if not block:
            continue
        for entry in re.split(r"\n\s*\n", block):
            lines = entry.strip().splitlines()
            if len(lines) < 2:
                continue
            times = lines[0]
            text = "\n".join(lines[1:])
            m = re.match(
                r"(\d{2}:\d{2}:\d{2},\d{3}) --> (\d{2}:\d{2}:\d{2},\d{3})",
                times,
            )
            if not m:
                continue

            def _parse(t: str) -> float:
                h, m_, rest = t.split(":")
                s, ms = rest.split(",")
                return int(h) * 3600 + int(m_) * 60 + int(s) + int(ms) / 1000

            start = _parse(m.group(1)) + offset
            end = _parse(m.group(2)) + offset
            out.append(f"{idx}\n{format_srt_time(start)} --> {format_srt_time(end)}\n{text}\n")
            idx += 1
    return "\n".join(out).strip() + "\n"
