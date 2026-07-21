"""
Azure Neural TTS — primary Phase 2 provider.

SSML tuned for documentary narration (Microsoft `documentary-narration` style +
prosody). Falls back to edge-tts when credentials are missing or synthesis fails.
"""

from __future__ import annotations

import os
import re
import xml.sax.saxutils
from pathlib import Path
from typing import Any

from captions import format_srt_time
from tts_narration import TtsChunk

# Microsoft-documented express-as styles only (invalid styles are ignored by Azure
# but can cause synthesis errors on some voices).
_ROLE_STYLES: dict[str, str] = {
    "narration": "documentary-narration",
    "narration_alt": "documentary-narration",
    "outro": "friendly",
    "quote": "serious",
    "authority": "newscast-formal",
    "witness": "empathetic",
}

# Irish Emily + similar voices: prosody-only (no express-as support in gallery).
_PROSODY_ONLY_VOICES = frozenset(
    {
        "en-IE-EmilyNeural",
        "en-IE-ConnorNeural",
        "en-GB-SoniaNeural",
        "en-GB-RyanNeural",
    }
)

# Prefixes for voices that reliably support mstts:express-as styles.
_STYLE_VOICE_PREFIXES = (
    "en-US-Ava",
    "en-US-Andrew",
    "en-US-Guy",
    "en-US-Jenny",
    "en-US-Aria",
    "en-US-Christopher",
    "en-US-Davis",
    "en-US-Jane",
    "en-US-Brian",
    "en-US-Emma",
    "en-US-Eric",
    "en-US-Steffan",
)


def is_configured() -> bool:
    return bool(os.environ.get("AZURE_SPEECH_KEY") and os.environ.get("AZURE_SPEECH_REGION"))


def _style_degree() -> float:
    raw = os.environ.get("TTS_AZURE_STYLE_DEGREE", "0.9")
    try:
        val = float(raw)
    except ValueError:
        val = 0.9
    return max(0.5, min(1.2, val))


def _voice_supports_express_as(voice: str) -> bool:
    if voice in _PROSODY_ONLY_VOICES:
        return False
    return any(voice.startswith(prefix) for prefix in _STYLE_VOICE_PREFIXES)


def _escape_ssml(text: str) -> str:
    return xml.sax.saxutils.escape(text)


def _inject_reading_pauses(text: str) -> str:
    """Comma / clause pauses — natural documentary cadence without extra API calls."""
    if len(text) < 48 or text.count(",") < 2:
        return text
    # Light pause after commas in dense factual sentences (dates, lists).
    return re.sub(r",(\s+)", r',<break time="120ms"/>\1', text, count=3)


def _role_style(voice: str, role: str) -> str | None:
    if not _voice_supports_express_as(voice):
        return None
    return _ROLE_STYLES.get(role, "documentary-narration")


def build_ssml(chunk: TtsChunk) -> str:
    """Build Azure SSML: voice → express-as style → prosody → text + breaks."""
    voice = chunk.voice
    lang_match = re.match(r"([a-z]{2}-[A-Z]{2})", voice)
    lang = lang_match.group(1) if lang_match else "en-US"

    body = _inject_reading_pauses(_escape_ssml(chunk.text.strip()))
    if chunk.break_before_ms > 0:
        body = f'<break time="{chunk.break_before_ms}ms"/>{body}'
    if chunk.break_after_ms > 0:
        body = f"{body}<break time=\"{chunk.break_after_ms}ms\"/>"

    inner = (
        f'<prosody rate="{chunk.rate}" pitch="{chunk.pitch}" volume="{chunk.volume}">'
        f"<s>{body}</s></prosody>"
    )

    style = _role_style(voice, chunk.role)
    if style:
        degree = _style_degree()
        inner = (
            f'<mstts:express-as style="{style}" styledegree="{degree:.2f}">'
            f"{inner}</mstts:express-as>"
        )

    return (
        '<speak version="1.0" '
        'xmlns="http://www.w3.org/2001/10/synthesis" '
        'xmlns:mstts="https://www.w3.org/2001/mstts" '
        f'xml:lang="{lang}">'
        f'<voice name="{voice}">{inner}</voice></speak>'
    )


def _words_to_srt(words: list[dict[str, Any]]) -> str:
    if not words:
        return ""
    lines: list[str] = []
    for i, w in enumerate(words, 1):
        start = float(w["start"])
        end = float(w.get("end", start + 0.15))
        if end <= start:
            end = start + 0.08
        lines.append(
            f"{i}\n{format_srt_time(start)} --> {format_srt_time(end)}\n{w['text']}\n"
        )
    return "\n".join(lines)


def synthesize_chunk(chunk: TtsChunk, dest: Path) -> tuple[str, list[dict[str, Any]]]:
    """Synthesize one chunk to MP3 via Azure Speech SDK."""
    import azure.cognitiveservices.speech as speechsdk

    key = os.environ["AZURE_SPEECH_KEY"]
    region = os.environ["AZURE_SPEECH_REGION"]
    ssml = build_ssml(chunk)

    speech_config = speechsdk.SpeechConfig(subscription=key, region=region)
    speech_config.set_speech_synthesis_output_format(
        speechsdk.SpeechSynthesisOutputFormat.Audio24Khz160KBitRateMonoMp3
    )
    speech_config.set_property(
        speechsdk.PropertyId.SpeechServiceResponse_RequestWordBoundary,
        "true",
    )

    words: list[dict[str, Any]] = []

    def _on_word_boundary(evt: speechsdk.SpeechSynthesisWordBoundaryEventArgs) -> None:
        if evt.boundary_type != speechsdk.SpeechSynthesisBoundaryType.Word:
            return
        start = evt.audio_offset / 10_000_000
        token = (evt.text or "").strip()
        if not token:
            return
        words.append({"text": token, "start": round(start, 4), "end": round(start, 4)})

    synthesizer = speechsdk.SpeechSynthesizer(speech_config=speech_config, audio_config=None)
    synthesizer.synthesis_word_boundary.connect(_on_word_boundary)

    result = synthesizer.speak_ssml_async(ssml).get()
    if result.reason != speechsdk.ResultReason.SynthesizingAudioCompleted:
        details = result.cancellation_details
        msg = details.error_details if details else str(result.reason)
        raise RuntimeError(f"Azure TTS failed: {msg}")

    audio = result.audio_data
    if not audio:
        raise RuntimeError("Azure TTS returned empty audio")

    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(audio)

    for i, w in enumerate(words):
        if i + 1 < len(words):
            w["end"] = words[i + 1]["start"]
        else:
            dur = probe_duration(dest)
            w["end"] = round(max(float(w["start"]) + 0.12, dur), 4)

    return _words_to_srt(words), words


def probe_duration(path: Path) -> float:
    import subprocess

    if not path.exists() or path.stat().st_size == 0:
        return 0.5
    result = subprocess.run(
        [
            "ffprobe", "-v", "error",
            "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1",
            str(path),
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    return max(0.5, float(result.stdout.strip()))


def is_transient_error(exc: BaseException) -> bool:
    msg = str(exc).lower()
    return any(s in msg for s in ("timeout", "connect", "network", "503", "502", "429", "throttl"))
