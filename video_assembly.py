"""
video_assembly.py
==================
Composites a background footage clip (from footage_library.py) with a
narration audio track (Task #59's TTS output, once that's wired up) into
one finished vertical short — the last step of the Story Studio pipeline
that turns a scored Story into an actual video.

Deliberately takes a footage file path and an audio file path as plain
arguments rather than reaching into the DB itself (BackgroundFootage
"least-used" selection, StoryProject lookups) — same separation as
footage_library.py: this module is pure ffmpeg composition, no Flask/DB
dependency, so it stays testable standalone. The DB wiring (which
footage row to use, saving the result against a StoryProject) belongs in
an app.py route, same as cut_youtube_clip's DB-free design.

Captions are optional and driven by word_timings — a flat list of
{"word"/"text", "start", "end"} dicts, the shape a real TTS engine's
word-boundary events naturally produce (Azure Neural TTS emits exactly
this via SSML boundary events). _word_timings_to_chunks() adapts that
into the same chunk-dict shape captions.chunk_captions_for_clip already
produces for transcript-based clip captions, so build_ass_subtitle (also
in captions.py) works unmodified for either source. Until Task #59
exists, callers simply omit word_timings and get footage+voiceover with
no captions burned in — captions.py's own subtitle styling/rendering
needed zero changes to support this.
"""

import os
import uuid
import shutil
import subprocess
import tempfile
from typing import List, Optional

from captions import build_ass_subtitle, STYLE_PRESETS, DEFAULT_STYLE

STORY_VIDEO_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "story_videos")
os.makedirs(STORY_VIDEO_DIR, exist_ok=True)

DEFAULT_OUTPUT_WIDTH = 1080
DEFAULT_OUTPUT_HEIGHT = 1920


def probe_audio_duration(path: str) -> float:
    """ffprobe wrapper for narration audio — callers need this to know
    how long the final video should be before calling assemble_story_video
    (which also independently truncates via -shortest, but callers doing
    Smart Pairing/footage-length decisions need the number ahead of time)."""
    result = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", path],
        capture_output=True, text=True, timeout=15, check=True,
    )
    return round(float(result.stdout.strip()), 2)


def _word_timings_to_chunks(word_timings: List[dict], max_words_per_chunk: int = 4) -> List[dict]:
    """Groups a flat, already-chronological list of per-word timings into
    the same {"rel_start", "rel_end", "words": [...]} chunk shape
    captions.chunk_captions_for_clip produces from transcript Lines —
    build_ass_subtitle doesn't know or care which produced them. Timings
    here are absolute seconds from the start of the narration audio,
    which IS "relative to clip start" since the assembled video's
    timeline starts at 0 with the narration."""
    chunks = []
    for i in range(0, len(word_timings), max_words_per_chunk):
        group = [w for w in word_timings[i : i + max_words_per_chunk] if (w.get("word") or w.get("text"))]
        if not group:
            continue
        chunk_start = max(0.0, float(group[0]["start"]))
        chunk_end = max(chunk_start + 0.05, float(group[-1]["end"]))
        chunks.append(
            {
                "rel_start": round(chunk_start, 3),
                "rel_end": round(chunk_end, 3),
                "words": [
                    {
                        "text": w.get("word") or w.get("text") or "",
                        "rel_start": round(max(0.0, float(w["start"])), 3),
                        "rel_end": round(max(float(w["start"]) + 0.02, float(w["end"])), 3),
                    }
                    for w in group
                ],
            }
        )
    return chunks


def assemble_story_video(
    footage_path: str,
    audio_path: str,
    output_width: int = DEFAULT_OUTPUT_WIDTH,
    output_height: int = DEFAULT_OUTPUT_HEIGHT,
    captions: bool = False,
    caption_style: str = DEFAULT_STYLE,
    word_timings: Optional[List[dict]] = None,
) -> str:
    """Loops/crops the background footage to cover a vertical
    output_width x output_height frame for exactly as long as the
    narration audio runs, replaces the footage's own (usually
    gameplay-noise) audio with the narration track, and optionally burns
    in word-synced captions. Returns the output file's path (inside
    STORY_VIDEO_DIR). Raises RuntimeError with a message safe to surface
    to the caller on failure.
    """
    if shutil.which("ffmpeg") is None:
        raise RuntimeError(
            "ffmpeg isn't installed on this server. This app needs to be deployed with the "
            "Dockerfile (which installs ffmpeg) rather than a plain Python runtime — see DEPLOY.md."
        )
    if not os.path.exists(footage_path):
        raise RuntimeError("That background footage file doesn't exist on disk anymore.")
    if not os.path.exists(audio_path):
        raise RuntimeError("That narration audio file doesn't exist on disk anymore.")
    if caption_style not in STYLE_PRESETS:
        caption_style = DEFAULT_STYLE

    # -shortest alone turned out unreliable here: with -stream_loop -1 on
    # the footage input, real testing showed the output sometimes running
    # a couple seconds past the narration's actual length instead of
    # stopping exactly when it ends (looping + a multi-filter -vf chain
    # seems to confuse ffmpeg's shortest-stream detection). Probing the
    # narration's real duration and passing it explicitly via -t is
    # deterministic regardless of that — confirmed against both a
    # narration shorter than the footage and one longer than it.
    audio_duration = probe_audio_duration(audio_path)
    if audio_duration <= 0:
        raise RuntimeError("That narration audio has no duration.")

    video_id = uuid.uuid4().hex[:12]
    workdir = tempfile.mkdtemp(prefix=f"storyvid_{video_id}_")
    out_path = os.path.join(STORY_VIDEO_DIR, f"{video_id}.mp4")

    # scale-to-cover then center-crop, rather than app.py's clip-crop
    # (which only trims width off an already-~16:9 source) — background
    # footage can come in any aspect ratio depending on what got
    # downloaded, so this needs to work regardless of source shape, not
    # just landscape-cropped-to-portrait.
    vf_parts = [
        f"scale={output_width}:{output_height}:force_original_aspect_ratio=increase",
        f"crop={output_width}:{output_height}",
    ]

    if captions and word_timings:
        chunks = _word_timings_to_chunks(word_timings)
        if chunks:
            ass_content = build_ass_subtitle(chunks, caption_style, output_width, output_height)
            ass_path = os.path.join(workdir, "captions.ass")
            with open(ass_path, "w", encoding="utf-8") as f:
                f.write(ass_content)
            vf_parts.append(f"subtitles={ass_path}")
        # else: no word timings resolved to a real chunk (empty
        # word_timings list) — proceed without captions rather than
        # failing the whole assembly over a cosmetic extra.

    cmd = [
        "ffmpeg", "-y",
        # Looped so a short footage source (or a story that runs longer
        # than the downloaded footage clip) never runs out mid-narration
        # — the explicit -t below then cuts the whole output to exactly
        # the narration's real length once it's done looping.
        "-stream_loop", "-1", "-i", footage_path,
        "-i", audio_path,
        "-map", "0:v", "-map", "1:a",
        "-vf", ",".join(vf_parts),
        "-t", str(audio_duration),
        "-c:v", "libx264", "-crf", "20", "-preset", "medium",
        "-c:a", "aac",
        out_path,
    ]

    try:
        subprocess.run(cmd, check=True, capture_output=True, timeout=600)
    except subprocess.CalledProcessError as e:
        stderr = e.stderr.decode("utf-8", errors="replace")[-500:] if e.stderr else str(e)
        raise RuntimeError(f"Video assembly failed ({stderr}).")
    except subprocess.TimeoutExpired:
        raise RuntimeError("Video assembly took too long and timed out.")
    finally:
        shutil.rmtree(workdir, ignore_errors=True)

    if not os.path.exists(out_path):
        raise RuntimeError("Video assembly didn't produce an output file.")

    return out_path
