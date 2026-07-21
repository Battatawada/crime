"""Post-process Flow thumbnail: add bold documentary title text (PIL)."""

from __future__ import annotations

import re
from pathlib import Path

# True-crime CTR style: 2–4 short words, heavy sans, white + black stroke.
_FONT_CANDIDATES = (
    Path(r"C:\Windows\Fonts\impact.ttf"),
    Path(r"C:\Windows\Fonts\arialbd.ttf"),
    Path(r"C:\Windows\Fonts\ARIALNB.TTF"),
    Path("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"),
    Path("/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf"),
)


def _load_font(size: int):
    from PIL import ImageFont

    for path in _FONT_CANDIDATES:
        if path.is_file():
            try:
                return ImageFont.truetype(str(path), size=size)
            except OSError:
                continue
    return ImageFont.load_default()


def normalize_overlay_text(text: str, *, max_words: int = 4) -> str:
    """2–4 word hook for thumbnail overlay."""
    raw = re.sub(r"[#*_\"']", "", (text or "").strip())
    if not raw:
        return ""
    # Prefer first sentence / fragment before period.
    chunk = re.split(r"[.!?]", raw, maxsplit=1)[0].strip()
    words = chunk.split()
    if len(words) > max_words:
        words = words[:max_words]
    return " ".join(words).upper()


def derive_overlay_from_title(title: str, *, max_words: int = 4) -> str:
    return normalize_overlay_text(title, max_words=max_words)


def compose_thumbnail_text(
    image_path: Path,
    overlay_text: str,
    *,
    position: str = "left",
) -> bool:
    """
    Burn professional title text onto thumbnail.png.
    Returns True if the file was updated.
    """
    text = normalize_overlay_text(overlay_text)
    if not text or not image_path.is_file():
        return False

    try:
        from PIL import Image, ImageDraw, ImageFont
    except ImportError:
        return False

    im = Image.open(image_path).convert("RGBA")
    w, h = im.size
    if w < 320 or h < 180:
        return False

    # Scale font to frame height (Impact-style large type).
    font_size = max(52, int(h * 0.14))
    font = _load_font(font_size)

    # Word-wrap into lines (~max 12 chars per line for phone readability).
    words = text.split()
    lines: list[str] = []
    current: list[str] = []
    for word in words:
        trial = " ".join(current + [word])
        bbox = ImageDraw.Draw(im).textbbox((0, 0), trial, font=font)
        line_w = bbox[2] - bbox[0]
        if line_w > w * 0.42 and current:
            lines.append(" ".join(current))
            current = [word]
        else:
            current.append(word)
    if current:
        lines.append(" ".join(current))
    lines = lines[:3]
    if not lines:
        return False

    overlay = Image.new("RGBA", im.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)

    line_heights = []
    line_widths = []
    for line in lines:
        bbox = draw.textbbox((0, 0), line, font=font)
        line_widths.append(bbox[2] - bbox[0])
        line_heights.append(bbox[3] - bbox[1])
    block_h = sum(line_heights) + int(font_size * 0.12) * (len(lines) - 1)
    y_start = (h - block_h) // 2

    margin_x = int(w * 0.06)
    stroke = max(3, int(font_size * 0.08))

    for i, line in enumerate(lines):
        lw = line_widths[i]
        lh = line_heights[i]
        if position == "right":
            x = w - margin_x - lw
        else:
            x = margin_x
        y = y_start + sum(line_heights[:i]) + int(font_size * 0.12) * i

        # Soft dark scrim behind text block (left third).
        if i == 0:
            scrim_left = max(0, x - int(w * 0.03))
            scrim_right = min(w, x + int(w * 0.44))
            scrim = Image.new("RGBA", im.size, (0, 0, 0, 0))
            scrim_draw = ImageDraw.Draw(scrim)
            scrim_draw.rectangle(
                [scrim_left, y_start - int(h * 0.04), scrim_right, y_start + block_h + int(h * 0.04)],
                fill=(0, 0, 0, 110),
            )
            im = Image.alpha_composite(im, scrim)
            draw = ImageDraw.Draw(overlay)

        # Red accent on last word of last line (brand accent).
        if i == len(lines) - 1 and " " in line:
            parts = line.rsplit(" ", 1)
            white_part, accent = parts[0] + " ", parts[1]
            draw.text(
                (x, y),
                white_part,
                font=font,
                fill=(255, 255, 255, 255),
                stroke_width=stroke,
                stroke_fill=(0, 0, 0, 255),
            )
            bbox_wp = draw.textbbox((x, y), white_part, font=font)
            draw.text(
                (bbox_wp[2], y),
                accent,
                font=font,
                fill=(220, 38, 38, 255),
                stroke_width=stroke,
                stroke_fill=(0, 0, 0, 255),
            )
        else:
            draw.text(
                (x, y),
                line,
                font=font,
                fill=(255, 255, 255, 255),
                stroke_width=stroke,
                stroke_fill=(0, 0, 0, 255),
            )

    composed = Image.alpha_composite(im, overlay).convert("RGB")
    if composed.size != (1280, 720):
        composed = composed.resize((1280, 720), Image.Resampling.LANCZOS)
    composed.save(image_path, format="PNG", optimize=True)
    return True
