"""
Plan TTS narration: voice + rate/pitch/volume per chunk for human documentary feel.

Providers split text into prosody chunks and concatenate audio within each scene.
Azure (primary) uses SSML breaks; edge-tts (fallback) uses per-chunk prosody args.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from captions import ANDREW, CHRISTOPHER, EMILY, resolve_voice

# Curated pool — documentary + dialogue (all valid edge-tts neural voices).
DEFAULT_VOICE_POOL = {
    "narrator": EMILY,
    "narrator_alt": ANDREW,
    "quote_male": CHRISTOPHER,
    "quote_female": "en-US-AriaNeural",
    "authority": "en-US-GuyNeural",
    "witness": "en-US-JennyNeural",
}

_QUOTE_RE = re.compile(
    r'([“"]([^”"]{3,}?)["”])|(\b(?:said|wrote|read|asked|replied|whispered|shouted|called|told)\s+[^.?!]{0,40}?["“]([^"”]+?)["”])',
    re.IGNORECASE,
)
_FEMALE_HINT = re.compile(
    r"\b(she|her|woman|mother|wife|daughter|sister|girl|ms\.|mrs\.|miss)\b",
    re.IGNORECASE,
)
_MALE_HINT = re.compile(
    r"\b(he|him|man|father|husband|son|brother|mr\.|detective|officer|sheriff)\b",
    re.IGNORECASE,
)
_TENSION_RE = re.compile(
    r"\b(killed|murdered|dead|body|blood|stabbed|shot|vanished|missing|never found|confession|guilty)\b",
    re.IGNORECASE,
)
_REVEAL_RE = re.compile(r"\b(but|however|instead|yet|except|turns out|the problem was)\b", re.IGNORECASE)


@dataclass(frozen=True)
class TtsChunk:
    text: str
    voice: str
    rate: str = "-5%"
    pitch: str = "+0Hz"
    volume: str = "+0%"
    role: str = "narration"
    break_before_ms: int = 0
    break_after_ms: int = 0


def _clamp_rate(rate: str) -> str:
    m = re.match(r"([+-]?\d+)%", rate.strip())
    if not m:
        return "-4%"
    val = max(-12, min(6, int(m.group(1))))
    return f"{val:+d}%"


def _clamp_pitch(pitch: str) -> str:
    m = re.match(r"([+-]?\d+)Hz", pitch.strip())
    if not m:
        return "+0Hz"
    val = max(-8, min(8, int(m.group(1))))
    return f"{val:+d}Hz"


def _clamp_volume(volume: str) -> str:
    m = re.match(r"([+-]?\d+)%", volume.strip())
    if not m:
        return "+0%"
    val = max(-10, min(12, int(m.group(1))))
    return f"{val:+d}%"


def load_voice_pool(pipeline: dict[str, Any]) -> dict[str, str]:
    raw = pipeline.get("tts_voice_pool") or {}
    pool = dict(DEFAULT_VOICE_POOL)
    if isinstance(raw, dict):
        for key, val in raw.items():
            if val:
                pool[str(key)] = resolve_voice(str(val))
    # Legacy list: [Emily, Andrew] maps to narrator / narrator_alt
    voices = pipeline.get("tts_voices")
    if isinstance(voices, list) and voices:
        pool["narrator"] = resolve_voice(str(voices[0]))
        if len(voices) > 1:
            pool["narrator_alt"] = resolve_voice(str(voices[1]))
    return pool


def _prosody_for_narration(
    sentence: str,
    *,
    scene_index: int,
    is_first_in_scene: bool,
    base_rate: str,
) -> tuple[str, str, str]:
    """Return (rate, pitch, volume) for narrator lines."""
    base = _clamp_rate(base_rate)
    base_val = int(re.match(r"([+-]?\d+)%", base).group(1))  # type: ignore[union-attr]

    rate_val = base_val
    pitch_val = 0
    volume_val = 0

    # Hook scenes: slower, lower, deliberate — documentary gravity (ElevenLabs-style weight).
    if scene_index == 0 and is_first_in_scene:
        rate_val -= 6
        pitch_val -= 3
        volume_val += 5
    elif scene_index == 0:
        rate_val -= 3
        pitch_val -= 2
        volume_val += 3

    words = sentence.split()
    if len(words) <= 6:
        # Punchy beat — slightly quicker, tighter.
        rate_val += 2
        pitch_val += 1
    elif len(words) >= 22:
        # Context / backstory — breathe more.
        rate_val -= 2
        pitch_val -= 1

    if sentence.strip().endswith("?"):
        rate_val -= 2
        pitch_val += 2
        volume_val += 2
    elif sentence.strip().endswith("!"):
        rate_val -= 1
        pitch_val += 1
        volume_val += 4

    if _TENSION_RE.search(sentence):
        rate_val -= 4
        pitch_val -= 3
        volume_val += 3

    if _REVEAL_RE.search(sentence):
        rate_val -= 3
        pitch_val -= 2
        volume_val += 4

    # Dates / evidence lists — steady, clear.
    if re.search(r"\b(19|20)\d{2}\b", sentence) and len(words) < 18:
        rate_val -= 1

    return (
        _clamp_rate(f"{rate_val:+d}%"),
        _clamp_pitch(f"{pitch_val:+d}Hz"),
        _clamp_volume(f"{volume_val:+d}%"),
    )


def _pick_quote_voice(quote: str, context: str, pool: dict[str, str]) -> str:
    blob = f"{context} {quote}"
    if _FEMALE_HINT.search(blob) and not _MALE_HINT.search(blob):
        return pool.get("quote_female", pool["narrator"])
    if _MALE_HINT.search(blob):
        return pool.get("quote_male", pool.get("narrator_alt", pool["narrator"]))
    return pool.get("quote_male", pool.get("narrator_alt", pool["narrator"]))


def _breaks_for_sentence(sentence: str, *, role: str, is_first_in_scene: bool) -> tuple[int, int]:
    """Micro-pauses for natural documentary pacing (Azure SSML; harmless on edge-tts)."""
    before = 0
    after = 0
    if role == "quote":
        before = 180
        after = 220
    elif is_first_in_scene:
        after = 120
    if sentence.strip().endswith(("?", "!")):
        after = max(after, 200)
    if _TENSION_RE.search(sentence):
        after = max(after, 280)
    if _REVEAL_RE.search(sentence):
        before = max(before, 100)
        after = max(after, 180)
    return before, after


def _split_sentences(text: str) -> list[str]:
    text = text.strip()
    if not text:
        return []
    parts = re.split(r"(?<=[.!?])\s+", text)
    return [p.strip() for p in parts if p.strip()]


def _extract_quote_spans(text: str) -> list[tuple[int, int, str]]:
    """Return (start, end, inner_quote) for double-quoted spans."""
    spans: list[tuple[int, int, str]] = []
    for m in re.finditer(r'["“]([^"”]{3,}?)["”]', text):
        spans.append((m.start(), m.end(), m.group(1)))
    return spans


def plan_tts_chunks(
    text: str,
    *,
    scene_index: int,
    pool: dict[str, str],
    base_rate: str,
    modulation: bool = True,
) -> list[TtsChunk]:
    """
    Split scene text into TTS chunks with voice/prosody.
    Without modulation: single narrator chunk at base_rate.
    """
    text = text.strip()
    if not text:
        return []

    narrator = pool.get("narrator", EMILY)
    if not modulation:
        return [TtsChunk(text=text, voice=narrator, rate=_clamp_rate(base_rate))]

    chunks: list[TtsChunk] = []
    quote_spans = _extract_quote_spans(text)
    if quote_spans:
        cursor = 0
        for start, end, inner in quote_spans:
            if start > cursor:
                lead = text[cursor:start].strip()
                for i, sent in enumerate(_split_sentences(lead)):
                    rate, pitch, vol = _prosody_for_narration(
                        sent,
                        scene_index=scene_index,
                        is_first_in_scene=(not chunks and i == 0),
                        base_rate=base_rate,
                    )
                    br_before, br_after = _breaks_for_sentence(
                        sent, role="narration", is_first_in_scene=(not chunks and i == 0)
                    )
                    chunks.append(
                        TtsChunk(
                            text=sent,
                            voice=narrator,
                            rate=rate,
                            pitch=pitch,
                            volume=vol,
                            role="narration",
                            break_before_ms=br_before,
                            break_after_ms=br_after,
                        )
                    )
            q_voice = _pick_quote_voice(inner, text[max(0, start - 80):start], pool)
            q_rate_val = int(re.match(r"([+-]?\d+)%", _clamp_rate(base_rate)).group(1)) - 2  # type: ignore[union-attr]
            chunks.append(
                TtsChunk(
                    text=inner,
                    voice=q_voice,
                    rate=_clamp_rate(f"{q_rate_val:+d}%"),
                    pitch="-2Hz",
                    volume="+5%",
                    role="quote",
                    break_before_ms=200,
                    break_after_ms=250,
                )
            )
            cursor = end
        if cursor < len(text):
            tail = text[cursor:].strip()
            for i, sent in enumerate(_split_sentences(tail)):
                rate, pitch, vol = _prosody_for_narration(
                    sent,
                    scene_index=scene_index,
                    is_first_in_scene=False,
                    base_rate=base_rate,
                )
                br_before, br_after = _breaks_for_sentence(sent, role="narration", is_first_in_scene=False)
                chunks.append(
                    TtsChunk(
                        text=sent,
                        voice=narrator,
                        rate=rate,
                        pitch=pitch,
                        volume=vol,
                        role="narration",
                        break_before_ms=br_before,
                        break_after_ms=br_after,
                    )
                )
        return _merge_adjacent_same_voice(chunks)

    # No quotes — sentence-level prosody on narrator.
    sentences = _split_sentences(text)
    for i, sent in enumerate(sentences):
        rate, pitch, vol = _prosody_for_narration(
            sent,
            scene_index=scene_index,
            is_first_in_scene=(i == 0),
            base_rate=base_rate,
        )
        br_before, br_after = _breaks_for_sentence(
            sent, role="narration", is_first_in_scene=(i == 0)
        )
        # Occasional alt narrator on long-form variety (every ~5 scenes, one sentence).
        voice = narrator
        role = "narration"
        if scene_index % 5 == 4 and i == len(sentences) - 1 and len(sentences) > 2:
            voice = pool.get("narrator_alt", narrator)
            role = "narration_alt"
        chunks.append(
            TtsChunk(
                text=sent,
                voice=voice,
                rate=rate,
                pitch=pitch,
                volume=vol,
                role=role,
                break_before_ms=br_before,
                break_after_ms=br_after,
            )
        )
    return _merge_adjacent_same_voice(chunks)


def merge_for_azure_economy(chunks: list[TtsChunk], *, scene_index: int = 0) -> list[TtsChunk]:
    """
    Collapse adjacent narrator lines into one Azure request (saves character quota).
    Keeps the opening hook beat on scene 0; never merges quotes / dialogue.
    """
    if len(chunks) <= 1:
        return chunks

    def _merge_run(run: list[TtsChunk]) -> list[TtsChunk]:
        if not run:
            return []
        out: list[TtsChunk] = []
        buf: TtsChunk | None = None
        for ch in run:
            if ch.role in ("quote", "authority", "witness"):
                if buf:
                    out.append(buf)
                    buf = None
                out.append(ch)
                continue
            if buf and buf.voice == ch.voice and buf.role == ch.role:
                buf = TtsChunk(
                    text=f"{buf.text} {ch.text}",
                    voice=buf.voice,
                    rate=buf.rate,
                    pitch=buf.pitch,
                    volume=buf.volume,
                    role=buf.role,
                    break_before_ms=buf.break_before_ms,
                    break_after_ms=ch.break_after_ms,
                )
            else:
                if buf:
                    out.append(buf)
                buf = ch
        if buf:
            out.append(buf)
        return out

    if scene_index == 0 and chunks[0].role in ("narration", "narration_alt"):
        return [chunks[0]] + _merge_run(chunks[1:])
    return _merge_run(chunks)


def _merge_adjacent_same_voice(chunks: list[TtsChunk]) -> list[TtsChunk]:
    """Merge consecutive chunks only when voice + prosody match exactly."""
    if not chunks:
        return []
    out: list[TtsChunk] = [chunks[0]]
    for ch in chunks[1:]:
        prev = out[-1]
        if (
            ch.voice == prev.voice
            and ch.rate == prev.rate
            and ch.pitch == prev.pitch
            and ch.volume == prev.volume
            and ch.role == prev.role
            and ch.break_before_ms == prev.break_before_ms
            and ch.break_after_ms == prev.break_after_ms
        ):
            out[-1] = TtsChunk(
                text=f"{prev.text} {ch.text}",
                voice=prev.voice,
                rate=prev.rate,
                pitch=prev.pitch,
                volume=prev.volume,
                role=prev.role,
                break_before_ms=prev.break_before_ms,
                break_after_ms=ch.break_after_ms,
            )
        else:
            out.append(ch)
    return out


def offset_word_timings(words: list[dict], offset_sec: float) -> list[dict]:
    if offset_sec <= 0:
        return words
    return [
        {
            **w,
            "start": round(float(w["start"]) + offset_sec, 4),
            "end": round(float(w["end"]) + offset_sec, 4),
        }
        for w in words
    ]
