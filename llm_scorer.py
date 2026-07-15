"""
LLM-powered clip scorer.

Replaces the keyword-matching heuristic in clipfind.py's score_transcript()
with a real Claude call: the model reads the whole transcript and picks
clip-worthy moments with actual written reasoning, instead of counting
regex hits.

Costs real money per call (unlike the free heuristic path) — see the
ANTHROPIC_API_KEY note in DEPLOY.md. Defaults to Haiku (cheap) since this
runs on every /api/analyze call, including free-tier users.

Grounding against hallucination: the model is given the transcript as
timestamped lines and asked to reuse those exact timestamps rather than
invent new ones. After parsing its response, every clip's line-by-line
preview text is pulled back out of the *real* transcript (not generated
by the model) — only the "hook" and "reasoning" fields are the model's
own words, and those are clearly labeled as such to the end user.
"""

import os
import re
import json
from typing import List, Optional

from clipfind import Line, Clip, parse_timestamp, fmt_timestamp

DEFAULT_MODEL = os.environ.get("ANTHROPIC_MODEL", "claude-haiku-4-5-20251001")

PROMPT_TEMPLATE = """You are an experienced short-form video editor and content strategist. Below is a timestamped transcript of a video. Your job is to find the {top_n} best moments to cut into standalone short-form clips (for TikTok, Reels, or YouTube Shorts).

Look for: strong hooks, surprising or contrarian statements, emotional moments, concrete stories with a clear payoff, useful stats or insights, and humor. Each clip should be able to stand alone and make sense without the rest of the video, and should generally run 15-90 seconds.

Use the exact timestamps shown in the transcript below for "start" and "end" — pick the timestamp of the line where the clip should start and the line where it should end, don't invent times that aren't in the transcript.

Respond with ONLY a JSON array, no other text before or after, no markdown code fences. Each object must have exactly these fields:
- "start": a timestamp string copied exactly from the transcript (e.g. "01:23")
- "end": a timestamp string copied exactly from the transcript
- "hook": a short, punchy version of the opening line, under 15 words
- "reasoning": 1-2 sentences explaining specifically why this moment works as a clip, written like an experienced editor giving feedback
- "score": an integer 0-100 rating how likely this clip is to perform well

Transcript:
{transcript}
"""


def _format_transcript(lines: List[Line]) -> str:
    return "\n".join(f"{fmt_timestamp(l.timestamp)} {l.text}" for l in lines)


def _extract_json_array(text: str):
    """Models occasionally wrap JSON in a code fence or add a stray
    sentence before/after despite instructions. Pull out the first
    [...] block rather than failing outright on strict json.loads."""
    text = text.strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    match = re.search(r"\[.*\]", text, re.DOTALL)
    if match:
        return json.loads(match.group(0))
    raise ValueError("Couldn't find a JSON array in the model's response.")


def score_with_llm(
    lines: List[Line],
    top_n: int = 6,
    model: Optional[str] = None,
) -> List[Clip]:
    """Send the transcript to Claude and return real Clip objects with
    written reasoning. Raises RuntimeError/ValueError on any failure —
    callers should catch and fall back to the heuristic scorer, this
    function does not fail silently or return partial/empty results."""
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise RuntimeError("ANTHROPIC_API_KEY is not set.")
    if not lines:
        raise ValueError("Empty transcript.")

    import anthropic  # imported lazily so the heuristic path works without this dep installed

    client = anthropic.Anthropic(api_key=api_key)
    transcript_text = _format_transcript(lines)
    prompt = PROMPT_TEMPLATE.format(top_n=top_n, transcript=transcript_text)

    response = client.messages.create(
        model=model or DEFAULT_MODEL,
        max_tokens=2000,
        messages=[{"role": "user", "content": prompt}],
    )
    raw_text = "".join(block.text for block in response.content if hasattr(block, "text"))
    items = _extract_json_array(raw_text)

    clips: List[Clip] = []
    for item in items:
        try:
            start_s = parse_timestamp(str(item["start"]))
            end_s = parse_timestamp(str(item["end"]))
        except (KeyError, ValueError):
            continue  # skip any malformed entry rather than failing the whole batch
        if end_s <= start_s:
            continue

        # Ground the preview text in the *actual* transcript, not the model's words.
        clip_lines = [l for l in lines if start_s <= l.timestamp <= end_s]

        clips.append(
            Clip(
                start=start_s,
                end=end_s,
                lines=clip_lines,
                score=float(item.get("score", 0)),
                hook=str(item.get("hook", "")).strip()[:300],
                reasoning=str(item.get("reasoning", "")).strip()[:500],
            )
        )

    if not clips:
        raise ValueError("Model response didn't contain any usable clips.")

    clips.sort(key=lambda c: c.score, reverse=True)
    return clips[:top_n]
