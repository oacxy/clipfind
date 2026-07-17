"""
Burned-in caption generation — turns a clip's transcript lines into a
styled .ass subtitle file that ffmpeg can burn directly into the video
via libass (the `subtitles=` video filter).

.ass (Advanced SubStation Alpha) rather than plain .srt/.vtt because it's
the only common subtitle format with real styling support (font, color,
outline, box background, and per-word karaoke sweep timing via \\kf tags)
that libass renders natively — no extra dependencies beyond ffmpeg
already having --enable-libass (confirmed present on both the dev
sandbox and Debian's apt ffmpeg package).

Caption timing is estimated, not exact: YouTube auto-captions only give
per-line start/end (not per-word), so each word within a line gets a
time-slice proportional to its character length across that line's
[start, end] window. This is the same approach most captioning tools use
without a dedicated word-level ASR pass — close enough to look right,
not frame-perfect.
"""

from typing import List, Optional


# Each preset maps to a real .ass [V4+ Styles] line. Keys here are the
# values the frontend sends as `caption_style` and must stay in sync with
# the style picker in app.py's INDEX_HTML.
STYLE_PRESETS = {
    "bold_impact": {
        "label": "Bold Impact",
        "description": "Big bold white caps, black outline — the default viral-clip look.",
        "fontname": "DejaVu Sans",
        "bold": -1,  # ASS boolean true
        "uppercase": True,
        "primary_color": "#FFFFFF",
        "secondary_color": "#FFFFFF",
        "outline_color": "#000000",
        "back_color": "#000000",
        "border_style": 1,  # outline + shadow, no filled box
        "outline": 3,
        "shadow": 1,
        "alignment": 2,  # bottom-center
        "karaoke": False,
    },
    "karaoke_highlight": {
        "label": "Karaoke Highlight",
        "description": "Words highlight in color as they're spoken — the TikTok/Hormozi look.",
        "fontname": "DejaVu Sans",
        "bold": -1,
        "uppercase": False,
        "primary_color": "#39FF14",  # already-spoken / highlight color
        "secondary_color": "#FFFFFF",  # not-yet-spoken color
        "outline_color": "#000000",
        "back_color": "#000000",
        "border_style": 1,
        "outline": 2.5,
        "shadow": 1,
        "alignment": 2,
        "karaoke": True,
    },
    "boxed": {
        "label": "Boxed",
        "description": "White text on a solid color bar — high readability.",
        "fontname": "DejaVu Sans",
        "bold": -1,
        "uppercase": False,
        "primary_color": "#FFFFFF",
        "secondary_color": "#FFFFFF",
        # For BorderStyle=3 (opaque box), libass fills the box using the
        # *outline* colour field, not BackColour (confirmed by rendering
        # a real test frame — BackColour has no visible effect in box
        # mode). back_color is set but unused here as a result.
        "outline_color": "#7C5CFF",  # brand purple bar (= the box fill)
        "back_color": "#000000",
        "border_style": 3,  # opaque box
        "outline": 16,  # box padding around the text in box mode
        "shadow": 0,
        "alignment": 2,
        "karaoke": False,
    },
}

DEFAULT_STYLE = "bold_impact"


def _words_with_timing(words: List[str], start: float, end: float):
    """Splits [start, end] across words proportional to character length
    (a longer word gets a longer time-slice) — a simple, dependency-free
    stand-in for real word-level speech timing."""
    if not words:
        return []
    total_chars = sum(len(w) for w in words) or 1
    dur = max(0.01, end - start)
    out = []
    t = start
    for w in words:
        share = len(w) / total_chars
        w_dur = dur * share
        out.append((w, t, t + w_dur))
        t += w_dur
    return out


def chunk_captions_for_clip(
    lines: List, clip_start: float, clip_end: float, max_words_per_chunk: int = 4
) -> List[dict]:
    """Turns transcript Lines overlapping [clip_start, clip_end] into short
    on-screen caption chunks (a few words each, like real burned-in
    captions use) with per-word timing, all relative to clip_start (i.e.
    ready to drop straight into the cut clip's own 0-based timeline).

    Pass fetch_youtube_transcript_raw()'s per-fragment Lines here, not the
    merged sentence Lines from fetch_youtube_transcript() — raw fragments
    are small enough that the within-fragment timing estimate stays close
    to real speech pacing, whereas estimating word timing across a whole
    merged sentence drifts noticeably once speech speeds up or slows
    down. Works with merged Lines too (e.g. the plain-text CLI transcript
    path has no raw fragments to fall back on), just less precisely."""
    clip_dur = clip_end - clip_start
    if clip_dur <= 0:
        return []

    ordered = sorted(lines, key=lambda l: l.timestamp)

    # Compute each line's effective end, then clamp it to the next line's
    # start. Without this, overlapping source timestamps (YouTube's raw
    # captions do this sometimes) or an overestimated fallback duration
    # both lead to two caption windows being "on screen" at once — which
    # is what burns in as stacked/overlapping text.
    spans = []
    for i, line in enumerate(ordered):
        raw_end = (
            line.end if getattr(line, "end", None) is not None
            else line.timestamp + max(1.0, len(line.text.split()) / 2.5)
        )
        if i + 1 < len(ordered):
            raw_end = min(raw_end, ordered[i + 1].timestamp)
        spans.append((line, raw_end))

    chunks = []
    for line, raw_end in spans:
        if line.timestamp >= clip_end or raw_end <= clip_start:
            continue
        line_start = max(line.timestamp, clip_start)
        line_end = min(raw_end, clip_end)
        if line_end <= line_start:
            continue

        words = line.text.split()
        if not words:
            continue

        timed_words = _words_with_timing(words, line_start, line_end)
        for i in range(0, len(timed_words), max_words_per_chunk):
            group = timed_words[i : i + max_words_per_chunk]
            chunk_start = group[0][1]
            chunk_end = group[-1][2]
            chunks.append(
                {
                    "rel_start": chunk_start - clip_start,
                    "rel_end": chunk_end - clip_start,
                    "words": [
                        {"text": w, "rel_start": s - clip_start, "rel_end": e - clip_start}
                        for (w, s, e) in group
                    ],
                }
            )

    # Clamp everything inside [0, clip_dur] — line-duration estimates can
    # overrun the clip boundary slightly, and ffmpeg/libass don't need
    # negative or out-of-range timestamps.
    for c in chunks:
        c["rel_start"] = round(max(0.0, min(c["rel_start"], clip_dur)), 3)
        c["rel_end"] = round(max(c["rel_start"] + 0.05, min(c["rel_end"], clip_dur)), 3)
        for w in c["words"]:
            w["rel_start"] = round(max(0.0, min(w["rel_start"], clip_dur)), 3)
            w["rel_end"] = round(max(w["rel_start"] + 0.02, min(w["rel_end"], clip_dur)), 3)

    # Belt-and-suspenders: also clamp consecutive *chunks* against each
    # other (word-grouping or the per-line clamp above can still leave a
    # sliver of overlap at a boundary) so no two chunks ever render at
    # once regardless of source data quirks.
    chunks.sort(key=lambda c: c["rel_start"])
    for i in range(len(chunks) - 1):
        if chunks[i]["rel_end"] > chunks[i + 1]["rel_start"]:
            chunks[i]["rel_end"] = max(chunks[i]["rel_start"] + 0.05, chunks[i + 1]["rel_start"])

    return chunks


def _ass_time(seconds: float) -> str:
    seconds = max(0.0, seconds)
    cs = round(seconds * 100)
    h, rem = divmod(cs, 360000)
    m, rem = divmod(rem, 6000)
    s, cs = divmod(rem, 100)
    return f"{h}:{m:02d}:{s:02d}.{cs:02d}"


def _ass_color(hex_str: str, alpha: int = 0x00) -> str:
    """ASS colors are &HAABBGGRR — alpha inverted (00 = opaque, FF = fully
    transparent), and channel order is BGR, not RGB."""
    hex_str = hex_str.lstrip("#")
    r = int(hex_str[0:2], 16)
    g = int(hex_str[2:4], 16)
    b = int(hex_str[4:6], 16)
    return f"&H{alpha:02X}{b:02X}{g:02X}{r:02X}"


def _escape_ass_text(text: str) -> str:
    # {, }, and \ all have override-code meaning in .ass — strip them from
    # spoken-word text rather than risk corrupting the style codes below.
    return text.replace("\\", "").replace("{", "").replace("}", "").replace("\n", " ").strip()


def _render_chunk_text(chunk: dict, preset: dict) -> str:
    words = chunk["words"]

    def word_text(w):
        t = _escape_ass_text(w["text"])
        return t.upper() if preset.get("uppercase") else t

    if preset.get("karaoke"):
        parts = []
        for w in words:
            cs = max(1, round((w["rel_end"] - w["rel_start"]) * 100))
            parts.append(f"{{\\kf{cs}}}{word_text(w)} ")
        return "".join(parts).strip()

    return " ".join(word_text(w) for w in words)


def build_ass_subtitle(chunks: List[dict], style_key: str, width: int, height: int) -> str:
    """Renders the full .ass file contents for the given caption chunks,
    sized/positioned for a video of `width`x`height` (pass the *final*
    output dimensions — i.e. post-crop if a vertical crop is also being
    applied — so PlayResX/Y match what libass will actually draw onto)."""
    preset = STYLE_PRESETS.get(style_key, STYLE_PRESETS[DEFAULT_STYLE])

    fontsize = max(24, round(height / 16))
    marginv = max(30, round(height * 0.08))

    primary = _ass_color(preset["primary_color"])
    secondary = _ass_color(preset["secondary_color"])
    outline_c = _ass_color(preset["outline_color"])
    # BackColour only visibly affects BorderStyle=1's drop shadow — it's
    # not the box fill (that's outline_color, see the "boxed" preset
    # comment above) — semi-transparent is the right default either way.
    back_c = _ass_color(preset["back_color"], alpha=0x80)

    style_line = (
        f"Style: Default,{preset['fontname']},{fontsize},{primary},{secondary},"
        f"{outline_c},{back_c},{preset['bold']},0,0,0,100,100,0,0,"
        f"{preset['border_style']},{preset['outline']},{preset['shadow']},"
        f"{preset['alignment']},20,20,{marginv},1"
    )

    events = []
    for chunk in chunks:
        text = _render_chunk_text(chunk, preset)
        if not text:
            continue
        start = _ass_time(chunk["rel_start"])
        end = _ass_time(chunk["rel_end"])
        events.append(f"Dialogue: 0,{start},{end},Default,,0,0,0,,{text}")

    return (
        "[Script Info]\n"
        "ScriptType: v4.00+\n"
        f"PlayResX: {width}\n"
        f"PlayResY: {height}\n"
        "ScaledBorderAndShadow: yes\n"
        "\n"
        "[V4+ Styles]\n"
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, "
        "Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, "
        "Alignment, MarginL, MarginR, MarginV, Encoding\n"
        f"{style_line}\n"
        "\n"
        "[Events]\n"
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
        + "\n".join(events)
        + "\n"
    )
