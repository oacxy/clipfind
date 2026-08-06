"""
azure_tts.py
============
Azure Neural TTS narration for Story Studio — turns a story's text into a
narrated audio file plus real per-word timing data.

Uses Azure's official Speech SDK (azure-cognitiveservices-speech) rather
than the plain REST synthesize endpoint specifically because word
boundary events — the thing that makes synced captions in
video_assembly.py possible — are only exposed through the SDK's
SpeechSynthesizer, not the REST API (which just returns raw audio bytes,
no timing metadata). This is the reason Azure was chosen over other TTS
providers for Story Studio in the first place.

Requires AZURE_SPEECH_KEY / AZURE_SPEECH_REGION env vars (same Speech
resource set up for testing in Speech Studio). Lazy-imports the SDK so
the rest of the app keeps working if this optional dependency isn't
installed yet, same pattern as anthropic/yt_dlp elsewhere in this repo.
"""

import os
import uuid
from typing import List, Optional, Tuple

NARRATION_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "narration_audio")
os.makedirs(NARRATION_DIR, exist_ok=True)

# A curated starting set of natural-sounding Neural voices spanning
# Story Studio's genre spread (calm narrator, energetic, dramatic) —
# not an exhaustive list of every Azure voice, just enough real variety
# to start with. Easy to extend once real usage shows what people want.
VOICES = [
    {"key": "en-US-AriaNeural", "label": "Aria (US, warm/versatile)"},
    {"key": "en-US-GuyNeural", "label": "Guy (US, energetic male)"},
    {"key": "en-US-JennyNeural", "label": "Jenny (US, friendly/clear)"},
    {"key": "en-US-DavisNeural", "label": "Davis (US, deep/dramatic)"},
    {"key": "en-GB-SoniaNeural", "label": "Sonia (UK, calm/narrator)"},
    {"key": "en-US-AnaNeural", "label": "Ana (US, youthful)"},
]
DEFAULT_VOICE = "en-US-AriaNeural"
VOICE_KEYS = {v["key"] for v in VOICES}


def is_configured() -> bool:
    """Lets callers (e.g. a /api/story-studio/voices route) check
    without triggering the lazy SDK import or risking a hard failure."""
    return bool(os.environ.get("AZURE_SPEECH_KEY") and os.environ.get("AZURE_SPEECH_REGION"))


def _clean_for_ssml(text: str) -> str:
    # Minimal XML-escaping for the handful of characters that are
    # actually special inside SSML's <voice> text content — story text
    # is plain narrative prose, not user-supplied markup, so this is
    # deliberately not a full XML-escaping library.
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def narrate_story(text: str, voice: Optional[str] = None) -> Tuple[str, List[dict]]:
    """Synthesizes `text` with Azure Neural TTS and returns
    (audio_file_path, word_timings). word_timings is already the exact
    shape video_assembly.assemble_story_video expects — a list of
    {"word", "start", "end"} dicts, seconds from the start of the audio
    — so the two modules plug together with zero glue code.

    Raises RuntimeError with a message safe to show the caller on
    failure (missing config, bad credentials, empty text, SDK not
    installed, synthesis failure)."""
    key = os.environ.get("AZURE_SPEECH_KEY")
    region = os.environ.get("AZURE_SPEECH_REGION")
    if not key or not region:
        raise RuntimeError(
            "AZURE_SPEECH_KEY / AZURE_SPEECH_REGION aren't configured on this server yet."
        )

    text = (text or "").strip()
    if not text:
        raise RuntimeError("No story text to narrate.")
    if len(text) > 8000:
        raise RuntimeError("Story text is too long to narrate (max 8000 characters).")

    voice_name = voice if voice in VOICE_KEYS else DEFAULT_VOICE

    try:
        import azure.cognitiveservices.speech as speechsdk  # lazy import, optional dependency
    except ImportError:
        raise RuntimeError(
            "The azure-cognitiveservices-speech package isn't installed on this server "
            "— add it to requirements.txt and redeploy."
        )

    speech_config = speechsdk.SpeechConfig(subscription=key, region=region)
    speech_config.speech_synthesis_voice_name = voice_name
    speech_config.set_speech_synthesis_output_format(
        speechsdk.SpeechSynthesisOutputFormat.Audio24Khz48KBitRateMonoMp3
    )
    # This is the entire reason to use the SDK instead of the plain REST
    # endpoint — word boundary events are the only source of real
    # per-word timing, which synced captions depend on.
    speech_config.set_property(
        speechsdk.PropertyId.SpeechServiceResponse_RequestWordBoundary, "true"
    )

    audio_id = uuid.uuid4().hex[:12]
    out_path = os.path.join(NARRATION_DIR, f"{audio_id}.mp3")
    audio_config = speechsdk.audio.AudioOutputConfig(filename=out_path)
    synthesizer = speechsdk.SpeechSynthesizer(speech_config=speech_config, audio_config=audio_config)

    word_timings: List[dict] = []

    def _on_word_boundary(evt):
        # audio_offset is in 100-nanosecond "ticks" (Azure's unit, not
        # ours) — divide by 1e7 to get seconds. duration is a
        # timedelta; fall back to a small default if it's ever missing
        # rather than crashing the whole synthesis over a caption detail.
        start = evt.audio_offset / 1e7
        duration = evt.duration.total_seconds() if getattr(evt, "duration", None) else 0.15
        word_timings.append(
            {"word": evt.text, "start": round(start, 3), "end": round(start + duration, 3)}
        )

    synthesizer.synthesis_word_boundary.connect(_on_word_boundary)

    ssml = (
        '<speak version="1.0" xmlns="http://www.w3.org/2001/10/synthesis" xml:lang="en-US">'
        f'<voice name="{voice_name}">{_clean_for_ssml(text)}</voice></speak>'
    )

    result = synthesizer.speak_ssml_async(ssml).get()

    if result.reason == speechsdk.ResultReason.Canceled:
        details = result.cancellation_details
        error_detail = (getattr(details, "error_details", "") or str(details.reason) or "").strip()
        if any(marker in error_detail for marker in ("401", "Unauthorized", "AuthenticationFailure")):
            raise RuntimeError(
                "Azure rejected the request — AZURE_SPEECH_KEY/AZURE_SPEECH_REGION are likely "
                "wrong, or the key was regenerated in the Azure portal since this was set up. "
                "Double check both values in Render's Environment settings."
            )
        raise RuntimeError(f"Azure TTS failed: {error_detail or 'no further detail from Azure'}.")

    if result.reason != speechsdk.ResultReason.SynthesizingAudioCompleted:
        raise RuntimeError(f"Azure TTS returned an unexpected result ({result.reason}).")

    if not os.path.exists(out_path) or os.path.getsize(out_path) == 0:
        raise RuntimeError("Azure TTS didn't produce an audio file.")

    # Word boundary events can arrive slightly out of order under load —
    # sorting here means every downstream consumer (video_assembly's
    # chunking) can assume chronological order without re-checking it.
    word_timings.sort(key=lambda w: w["start"])

    return out_path, word_timings
