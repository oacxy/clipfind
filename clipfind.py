#!/usr/bin/env python3
"""
ClipFind — AI Clip Finder
==========================
Takes a timestamped transcript (VTT, SRT, or plain "MM:SS text" lines) and
scores every sentence for "clip-worthiness" using a heuristic model, then
groups the highest-scoring moments into ready-to-cut clip windows with a
suggested hook/caption.

This is a fully algorithmic MVP (no external LLM API key required), so it
runs anywhere for free. It's designed to be swapped later for an LLM-based
scorer (e.g. call Claude/GPT to re-rank the top candidates) once you have
API budget — the interface (score_transcript) stays the same.

Usage:
    python3 clipfind.py transcript.txt --top 5
    python3 clipfind.py --youtube https://www.youtube.com/watch?v=VIDEO_ID --top 5
    python3 clipfind.py --youtube VIDEO_ID --top 5

Transcript format (plain text, one line per line of speech):
    00:00:12 So the reason most people are still broke isn't external.
    00:00:18 It's internal. It's fear of judgement.
    ...

YouTube auto-import requires: pip install youtube-transcript-api
"""

import re
import sys
import time
import argparse
from dataclasses import dataclass, field
from typing import List, Optional


# ---------------------------------------------------------------------------
# Scoring signals. Each is a (name, regex_or_fn, weight) rule. Weights were
# tuned against manually-labeled "viral" clip openers from short-form video
# (hooks, confessions, contrarian takes, stats, direct address, urgency).
# ---------------------------------------------------------------------------

SUPERLATIVES = re.compile(
    r"\b(never|always|nobody|everyone|illegal|insane|crazy|secret|shocking|"
    r"unbelievable|huge|massive|biggest|worst|best|only|literally|actually)\b",
    re.IGNORECASE,
)
CONTRARIAN = re.compile(
    r"\b(but actually|the truth is|most people think|nobody tells you|"
    r"here'?s the thing|the reason|not because|it'?s not|isn'?t because)\b",
    re.IGNORECASE,
)
DIRECT_ADDRESS = re.compile(r"\byou('re|'ll|'ve)?\b", re.IGNORECASE)
NUMBERS = re.compile(r"\b\d+([.,]\d+)?%?\b")
QUESTION = re.compile(r"\?\s*$")
CONFESSION = re.compile(
    r"\b(I think|I believe|honestly|the real reason|what nobody says|I used to)\b",
    re.IGNORECASE,
)
URGENCY = re.compile(r"\b(right now|today|immediately|stop|start|need to)\b", re.IGNORECASE)

RULES = [
    ("superlative", SUPERLATIVES, 2.0),
    ("contrarian", CONTRARIAN, 3.0),
    ("direct_address", DIRECT_ADDRESS, 1.0),
    ("numbers", NUMBERS, 1.5),
    ("question_hook", QUESTION, 2.5),
    ("confession", CONFESSION, 2.0),
    ("urgency", URGENCY, 1.5),
]

IDEAL_SENTENCE_LEN = (6, 18)  # words; short punchy sentences score a bonus


@dataclass
class Line:
    timestamp: float  # seconds
    text: str
    score: float = 0.0
    hits: List[str] = field(default_factory=list)
    # When available (YouTube auto-captions), the timestamp the underlying
    # speech actually ends — used by captions.py to time burned-in caption
    # display windows. None for plain-text-file transcripts (load_transcript
    # has no duration data), in which case caption timing falls back to a
    # words-per-second estimate.
    end: Optional[float] = None


@dataclass
class Clip:
    start: float
    end: float
    lines: List[Line]
    score: float
    hook: str
    reasoning: str = ""  # populated by the LLM scorer; empty for the heuristic path
    # Analyst breakdown — also LLM-only. sub_scores keys: hook, virality,
    # entertainment, retention, emotional_impact, pacing, originality
    # (each 0-100). suggestions is a short list of concrete, clip-specific
    # improvement tips ("start 1.5s earlier"). Both empty for the
    # heuristic fallback path — the frontend only shows the breakdown
    # panel when sub_scores is non-empty.
    sub_scores: dict = field(default_factory=dict)
    suggestions: List[str] = field(default_factory=list)


def parse_timestamp(ts: str) -> float:
    parts = [float(p) for p in ts.split(":")]
    while len(parts) < 3:
        parts.insert(0, 0.0)
    h, m, s = parts
    return h * 3600 + m * 60 + s


def fmt_timestamp(seconds: float) -> str:
    seconds = int(seconds)
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    return f"{h:02d}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"


LINE_RE = re.compile(r"^\s*(\d{1,2}:\d{2}(?::\d{2})?)\s+(.*)$")


def load_transcript(path: str) -> List[Line]:
    lines: List[Line] = []
    with open(path, "r", encoding="utf-8") as f:
        for raw in f:
            raw = raw.strip()
            if not raw:
                continue
            m = LINE_RE.match(raw)
            if not m:
                continue
            ts, text = m.groups()
            lines.append(Line(timestamp=parse_timestamp(ts), text=text))
    return lines


# ---------------------------------------------------------------------------
# YouTube auto-import
#
# Auto-generated YouTube captions arrive as tiny fragments (2-8 words every
# few seconds, usually no punctuation) rather than full sentences. Scoring
# fragments directly wrecks the heuristics below (no "?" to catch a question
# hook, "the reason" split across two fragments, etc). merge_fragments()
# reconstructs sentence-ish lines before they hit the scorer. Manually
# uploaded/creator-provided captions (which do have punctuation) score
# noticeably better than auto-captions — that's a real product caveat, not
# a bug: tell users to prefer videos with real captions when possible.
# ---------------------------------------------------------------------------

VIDEO_ID_RE = re.compile(r"^[A-Za-z0-9_-]{11}$")

YOUTUBE_URL_PATTERNS = [
    re.compile(r"(?:v=|/videos/|embed/|shorts/|youtu\.be/)([A-Za-z0-9_-]{11})"),
]


def extract_video_id(url_or_id: str) -> str:
    """Accept a full YouTube URL (watch, youtu.be, shorts, embed) or a bare
    11-char video ID and return the video ID."""
    url_or_id = url_or_id.strip()
    if VIDEO_ID_RE.match(url_or_id):
        return url_or_id
    for pattern in YOUTUBE_URL_PATTERNS:
        m = pattern.search(url_or_id)
        if m:
            return m.group(1)
    raise ValueError(f"Couldn't find a YouTube video ID in: {url_or_id!r}")


def merge_fragments(entries: list, max_words: int = 20, max_gap: float = 2.0) -> List[Line]:
    """Greedily merge raw caption fragments into sentence-ish Lines.

    entries: list of dicts/objects with .text/.start (raw API output).
    A merge group ends when: a fragment ends in terminal punctuation,
    the word count hits max_words, or the gap to the next fragment
    exceeds max_gap seconds (a pause usually means a new thought).
    """
    lines: List[Line] = []
    buf_text: List[str] = []
    buf_start: Optional[float] = None
    buf_end: Optional[float] = None
    prev_end = 0.0

    def flush():
        nonlocal buf_text, buf_start, buf_end
        if buf_text:
            text = " ".join(buf_text).strip()
            if text:
                lines.append(Line(timestamp=buf_start or 0.0, text=text, end=buf_end))
        buf_text = []
        buf_start = None
        buf_end = None

    for e in entries:
        text = getattr(e, "text", None) if not isinstance(e, dict) else e.get("text", "")
        start = getattr(e, "start", None) if not isinstance(e, dict) else e.get("start", 0.0)
        duration = getattr(e, "duration", 0.0) if not isinstance(e, dict) else e.get("duration", 0.0)
        text = (text or "").strip().replace("\n", " ")
        if not text:
            continue

        gap = start - prev_end
        if buf_start is not None and gap > max_gap:
            flush()

        if buf_start is None:
            buf_start = start

        buf_text.append(text)
        prev_end = start + duration
        buf_end = prev_end

        word_count = sum(len(t.split()) for t in buf_text)
        if text.rstrip().endswith((".", "!", "?")) or word_count >= max_words:
            flush()

    flush()
    return lines


def _build_transcript_api():
    """Build a YouTubeTranscriptApi instance, routed through a Webshare
    residential proxy if credentials are set in the environment.

    YouTube blocks most cloud-provider IPs (Render, AWS, GCP, Azure, etc.)
    outright with RequestBlocked/IpBlocked errors — this isn't a bug in
    this code, it's YouTube's own anti-bot policy. The documented fix
    (see youtube-transcript-api's README, "Working around IP bans") is to
    route requests through a rotating residential proxy. Set
    WEBSHARE_PROXY_USERNAME / WEBSHARE_PROXY_PASSWORD as environment
    variables (e.g. in Render's Environment tab) once you have a Webshare
    "Residential" proxy package, and this will pick them up automatically.
    Without them set, this falls back to a direct (likely to be blocked
    on cloud hosts) connection — fine for local testing.
    """
    import os
    from youtube_transcript_api import YouTubeTranscriptApi

    username = os.environ.get("WEBSHARE_PROXY_USERNAME")
    password = os.environ.get("WEBSHARE_PROXY_PASSWORD")

    if username and password:
        from youtube_transcript_api.proxies import WebshareProxyConfig

        return YouTubeTranscriptApi(
            proxy_config=WebshareProxyConfig(
                proxy_username=username,
                proxy_password=password,
            )
        )

    return YouTubeTranscriptApi()


def _is_transient_proxy_error(msg: str) -> bool:
    """True for errors that look like 'this specific request/exit-IP
    failed' rather than 'this will never work' — a rotating residential
    proxy pool (Webshare) occasionally hands out a slow or momentarily
    blocked exit IP, and simply trying again usually gets a different one.
    Deliberately does NOT match RequestBlocked/IpBlocked (that means no
    proxy is configured at all, or YouTube blocked the proxy IP itself —
    retrying won't help) or NoTranscriptFound/Subtitles disabled (a real,
    permanent fact about the video, not a network hiccup)."""
    return any(
        s in msg
        for s in ("ProxyError", "Max retries", "Connection reset", "Connection aborted", "timed out", "Timeout")
    )


def _fetch_raw_with_retry(api, video_id: str, languages, max_retries: int = 3):
    """Retries api.fetch() on transient-looking proxy/network failures,
    with a short backoff between attempts. Real, permanent failures
    (disabled captions, no proxy configured at all) raise immediately on
    the first try — no point burning three attempts on something that
    will never succeed."""
    last_error = None
    for attempt in range(max_retries):
        try:
            return api.fetch(video_id, languages=list(languages))
        except Exception as e:
            last_error = e
            if not _is_transient_proxy_error(str(e)) or attempt == max_retries - 1:
                raise
            time.sleep(1.5 * (attempt + 1))
    raise last_error  # unreachable, but keeps type checkers happy


def fetch_youtube_transcript(url_or_id: str, languages=("en",)) -> List[Line]:
    """Fetch a YouTube video's transcript and return it as merged Lines,
    ready to pass straight into score_transcript()."""
    try:
        api = _build_transcript_api()
    except ImportError:
        raise RuntimeError(
            "youtube-transcript-api isn't installed. Run: pip install youtube-transcript-api"
        )

    video_id = extract_video_id(url_or_id)
    raw = _fetch_raw_with_retry(api, video_id, languages)
    return merge_fragments(raw)


def fetch_youtube_transcript_raw(url_or_id: str, languages=("en",)) -> List[Line]:
    """Fetch a YouTube video's transcript WITHOUT merging fragments into
    sentences — one Line per raw caption fragment (YouTube's own
    ~2-8-word chunks), each carrying its own real start/end.

    This is what captions.py uses for burned-in caption timing, not
    fetch_youtube_transcript()'s merged sentence Lines. Merging is right
    for scoring (regex rules need real sentences), but it's wrong for
    caption sync: once several fragments get glued into one long Line,
    the only timing data left is the merged line's overall start/end, so
    word-level timing within it has to be *estimated* (proportional to
    character count) rather than using YouTube's own real per-fragment
    timestamps — which reads as visible drift once speech speeds up or
    slows down. Raw fragments track actual speech pacing far more
    closely since they're small enough that estimation error within each
    one stays tiny."""
    try:
        api = _build_transcript_api()
    except ImportError:
        raise RuntimeError(
            "youtube-transcript-api isn't installed. Run: pip install youtube-transcript-api"
        )

    video_id = extract_video_id(url_or_id)
    raw = _fetch_raw_with_retry(api, video_id, languages)

    lines: List[Line] = []
    for e in raw:
        text = getattr(e, "text", None) if not isinstance(e, dict) else e.get("text", "")
        start = getattr(e, "start", None) if not isinstance(e, dict) else e.get("start", 0.0)
        duration = getattr(e, "duration", 0.0) if not isinstance(e, dict) else e.get("duration", 0.0)
        text = (text or "").strip().replace("\n", " ")
        if not text:
            continue
        lines.append(Line(timestamp=start, text=text, end=start + duration))

    lines.sort(key=lambda l: l.timestamp)
    return lines


def score_line(line: Line) -> Line:
    score = 0.0
    hits = []
    for name, pattern, weight in RULES:
        n = len(pattern.findall(line.text))
        if n:
            score += weight * min(n, 2)  # cap repeats so one line can't game it
            hits.append(name)

    word_count = len(line.text.split())
    lo, hi = IDEAL_SENTENCE_LEN
    if lo <= word_count <= hi:
        score += 1.0

    line.score = round(score, 2)
    line.hits = hits
    return line


def score_transcript(lines: List[Line]) -> List[Line]:
    return [score_line(l) for l in lines]


def build_clips(lines: List[Line], top_n: int = 5, window: float = 25.0) -> List[Clip]:
    """Pick the highest scoring lines as clip anchors, then pull in
    neighboring lines within `window` seconds to form a cuttable clip."""
    ranked = sorted(lines, key=lambda l: l.score, reverse=True)
    clips: List[Clip] = []
    used_ranges = []

    for anchor in ranked:
        if len(clips) >= top_n:
            break
        if anchor.score <= 0:
            continue
        start = anchor.timestamp - window * 0.3
        end = anchor.timestamp + window * 0.7

        # skip if overlaps an already-selected clip
        if any(not (end < s or start > e) for s, e in used_ranges):
            continue

        clip_lines = [l for l in lines if start <= l.timestamp <= end]
        clip_score = sum(l.score for l in clip_lines)
        clips.append(
            Clip(start=start, end=end, lines=clip_lines, score=round(clip_score, 2), hook=anchor.text)
        )
        used_ranges.append((start, end))

    clips.sort(key=lambda c: c.score, reverse=True)
    return clips


def print_report(clips: List[Clip]) -> None:
    print(f"\n{'='*70}\nClipFind — {len(clips)} suggested clips\n{'='*70}")
    for i, clip in enumerate(clips, 1):
        print(f"\n#{i}  [{fmt_timestamp(max(clip.start,0))} - {fmt_timestamp(clip.end)}]  score={clip.score}")
        print(f"    Hook line: \"{clip.hook}\"")
        print(f"    Suggested caption: \"{clip.hook.strip().rstrip('.')}\" 🔥")
        transcript_preview = " ".join(l.text for l in clip.lines)
        if len(transcript_preview) > 200:
            transcript_preview = transcript_preview[:200] + "..."
        print(f"    Preview: {transcript_preview}")


def main():
    parser = argparse.ArgumentParser(description="Find clip-worthy moments in a transcript")
    parser.add_argument(
        "transcript", nargs="?", help="Path to transcript file (MM:SS text per line)"
    )
    parser.add_argument(
        "--youtube", metavar="URL_OR_ID", help="YouTube URL or video ID to auto-import"
    )
    parser.add_argument("--top", type=int, default=5, help="Number of clips to suggest")
    parser.add_argument(
        "--save", metavar="PATH", help="Save the fetched transcript as a MM:SS text file"
    )
    args = parser.parse_args()

    if not args.transcript and not args.youtube:
        parser.error("provide a transcript file or --youtube URL_OR_ID")

    if args.youtube:
        try:
            lines = fetch_youtube_transcript(args.youtube)
        except Exception as e:
            print(f"Couldn't fetch YouTube transcript: {e}")
            sys.exit(1)
        if args.save:
            with open(args.save, "w", encoding="utf-8") as f:
                for l in lines:
                    f.write(f"{fmt_timestamp(l.timestamp)} {l.text}\n")
            print(f"Saved transcript to {args.save}")
    else:
        lines = load_transcript(args.transcript)

    if not lines:
        print("No parseable lines found. Format each line as: 00:00:12 Your text here")
        sys.exit(1)

    lines = score_transcript(lines)
    clips = build_clips(lines, top_n=args.top)
    print_report(clips)


if __name__ == "__main__":
    main()
