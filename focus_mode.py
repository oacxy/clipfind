"""
AI Focus Mode: search a single video's transcript for every moment matching
a natural-language query — "every time he laughs," "arguments," a preset
category like "Key insights," etc.

This is a different job than llm_scorer.py's main Analyst scorer. The main
scorer picks the best few clips overall out of a video. Focus Mode targets
one specific criterion the user names and returns every matching moment —
that could be zero, one, or a dozen, and zero is a legitimate answer (the
video just doesn't have that), not a failure.

Reuses clipfind.Clip as the return shape so app.py's existing
clips_to_json() serializer works unchanged. sub_scores/suggestions are left
empty on these Clips (that Analyst breakdown is specific to the main
scorer's clip-worthiness judgment, not a targeted search match).
"""

import os
import re
import json
from typing import List, Optional

from clipfind import Line, Clip, parse_timestamp, fmt_timestamp

DEFAULT_MODEL = os.environ.get("ANTHROPIC_MODEL", "claude-haiku-4-5-20251001")

PROMPT_TEMPLATE = """You are helping a video editor search a single video's transcript for every moment matching a specific description.

Search query: "{query}"

Read the timestamped transcript below and find every moment that matches this query — there could be zero matches, a couple, or many. Don't force weak or unrelated moments just to return something; if nothing genuinely matches the query, return an empty array.

Use the exact timestamps shown in the transcript for "start" and "end" — pick the timestamp of the line where the moment starts and where it ends, don't invent times that aren't in the transcript. Keep each match tight and focused on just that moment (a few seconds up to under a minute), not a broad stretch of the video.

Respond with ONLY a JSON array, no markdown code fences, no other text. Each object must have exactly these fields:
- "start": a timestamp string copied exactly from the transcript (e.g. "01:23")
- "end": a timestamp string copied exactly from the transcript
- "hook": a short label of what happens in this moment, under 15 words
- "reasoning": one sentence on specifically why this moment matches the search query
- "score": an integer 0-100, how strong/clear a match this specific moment is to the query

Return at most {max_results} matches. If there are more than that, keep only the strongest matches.

Transcript:
{transcript}
"""


def _format_transcript(lines: List[Line]) -> str:
    return "\n".join(f"{fmt_timestamp(l.timestamp)} {l.text}" for l in lines)


def _extract_json_array(text: str):
    """Same tolerant parsing as llm_scorer.py — models occasionally wrap
    JSON in a code fence or add a stray sentence despite instructions."""
    text = text.strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    match = re.search(r"\[.*\]", text, re.DOTALL)
    if match:
        return json.loads(match.group(0))
    raise ValueError("Couldn't find a JSON array in the model's response.")


def find_moments_with_llm(
    lines: List[Line],
    query: str,
    max_results: int = 12,
    model: Optional[str] = None,
) -> List[Clip]:
    """Search a transcript for every moment matching a natural-language
    query. Returns an empty list when there are genuinely no matches —
    that's a valid result, not an error. Only raises on a real failure
    (no API key, empty inputs, the model's response not parsing as JSON
    at all)."""
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise RuntimeError("ANTHROPIC_API_KEY is not set.")
    if not lines:
        raise ValueError("Empty transcript.")
    query = (query or "").strip()
    if not query:
        raise ValueError("Empty search query.")

    import anthropic  # imported lazily, same pattern as llm_scorer.py

    client = anthropic.Anthropic(api_key=api_key)
    transcript_text = _format_transcript(lines)
    prompt = PROMPT_TEMPLATE.format(query=query, max_results=max_results, transcript=transcript_text)

    response = client.messages.create(
        model=model or DEFAULT_MODEL,
        max_tokens=2500,
        messages=[{"role": "user", "content": prompt}],
    )
    raw_text = "".join(block.text for block in response.content if hasattr(block, "text"))
    items = _extract_json_array(raw_text)

    moments: List[Clip] = []
    for item in items:
        try:
            start_s = parse_timestamp(str(item["start"]))
            end_s = parse_timestamp(str(item["end"]))
        except (KeyError, ValueError):
            continue  # skip a malformed entry rather than failing the whole search
        if end_s <= start_s:
            continue

        # Ground the preview text in the *actual* transcript, not the model's words.
        clip_lines = [l for l in lines if start_s <= l.timestamp <= end_s]

        moments.append(
            Clip(
                start=start_s,
                end=end_s,
                lines=clip_lines,
                score=float(item.get("score", 0)),
                hook=str(item.get("hook", "")).strip()[:300],
                reasoning=str(item.get("reasoning", "")).strip()[:500],
            )
        )

    moments.sort(key=lambda c: c.score, reverse=True)
    return moments[:max_results]
