"""
Story Studio — AI story generation + Story Analyst scoring.

First building block of ClipFind's story-video feature: instead of sourcing
real Reddit posts (real API costs/ToS, scraping risk), stories are
generated fresh by Claude in a chosen genre, Reddit-story style (first
person, narrative, satisfying payoff). Every story — generated or
user-submitted — gets the same Story Analyst breakdown so creators can
judge it before spending TTS/video-assembly time on it, mirroring how
clipfind.py's clips get an Analyst breakdown before a user commits to
cutting one.

Deliberately mirrors llm_scorer.py's patterns: defensive JSON parsing with
a regex-fallback extractor, clamped/validated numeric fields, raises on
total failure rather than silently returning something broken.
"""

import os
import re
import json
from dataclasses import dataclass, field
from typing import List, Optional

DEFAULT_MODEL = os.environ.get("ANTHROPIC_MODEL", "claude-haiku-4-5-20251001")

# Content genres for AI-generated stories. Reddit-adjacent naming (AITA,
# TIFU) is kept since that's the tone/format people expect from this style
# of short-form video, but nothing here actually touches Reddit's API —
# every story is generated fresh.
GENRES = [
    "AITA",
    "TIFU",
    "Confession",
    "Horror",
    "Relationship Drama",
    "Revenge",
    "True Crime",
    "Inspirational",
    "Funny",
    "Wholesome",
    "Cheating",
    "Pet Stories",
    "Work Stories",
    "School Stories",
    "Embarrassing Moments",
]

# The 12 Story Analyst metrics from the product spec. Kept as an ordered
# tuple (not just dict keys) so the frontend can render them in a
# consistent, deliberate order rather than whatever order the model
# happens to emit JSON keys in.
ANALYST_METRICS = (
    "virality",
    "hook_strength",
    "emotional_impact",
    "suspense",
    "curiosity",
    "payoff_quality",
    "story_flow",
    "replay_potential",
    "completion_prediction",
    "difficulty_to_adapt",
)


@dataclass
class StoryAnalysis:
    overall_score: int = 0
    sub_scores: dict = field(default_factory=dict)  # ANALYST_METRICS keys, 0-100
    estimated_watch_time_seconds: int = 0
    reasoning: str = ""  # the "why" explanation shown alongside the scores


@dataclass
class Story:
    title: str
    body: str
    genre: str
    analysis: StoryAnalysis


GENERATE_PROMPT_TEMPLATE = """You are an experienced short-form video content strategist who writes viral Reddit-story-style narratives for TikTok/YouTube Shorts/Reels story channels. Write ONE original story in the "{genre}" genre.

Style rules:
- First-person narrative, written like a real Reddit post (natural voice, not overly polished).
- Should read well when narrated aloud by a text-to-speech voice: clear sentences, natural pacing, minimal punctuation-heavy asides.
- Needs a strong hook in the first sentence, real narrative tension or emotional stakes, and a satisfying payoff/ending.
- Length: 150-400 words (short enough for a 45-120 second narrated video).
- Do NOT reference real, identifiable people, and do not include any real names of public figures.

After writing the story, evaluate it yourself as a Story Analyst would, honestly — not every generated story is great, and the scores should reflect real quality differences, not default to high numbers.

Respond with ONLY a JSON object, no other text before or after, no markdown code fences. Fields:
- "title": a short, punchy title for the story (under 12 words), Reddit-post-title style
- "body": the full story text
- "overall_score": integer 0-100, overall short-form video potential
- "sub_scores": object with these ten integer 0-100 ratings:
  - "virality": overall likelihood this specific story performs well if posted
  - "hook_strength": how strong the first sentence is at creating curiosity or surprise
  - "emotional_impact": how much genuine emotional weight the story has
  - "suspense": how much tension is built and sustained
  - "curiosity": how much the story makes a viewer need to know what happens next
  - "payoff_quality": how satisfying the ending/resolution is
  - "story_flow": how smoothly the narrative moves, no confusing jumps
  - "replay_potential": how likely someone is to watch it again or share it
  - "completion_prediction": how likely a viewer is to watch to the end without dropping off
  - "difficulty_to_adapt": how much editing/rewriting this would need before it's ready to narrate (higher = needs more work, lower = ready as-is)
- "estimated_watch_time_seconds": integer, realistic estimate of how long this story takes to narrate aloud at a natural pace
- "reasoning": 2-3 sentences explaining specifically why this story would (or wouldn't) retain viewers, written like an experienced editor giving feedback — be specific to this story's actual content, not generic praise
"""

ANALYZE_PROMPT_TEMPLATE = """You are an experienced short-form video content strategist acting as a Story Analyst. Evaluate the following user-submitted story for its potential as a narrated short-form video (TikTok/YouTube Shorts/Reels).

Story:
{story_text}

Respond with ONLY a JSON object, no other text before or after, no markdown code fences. Fields:
- "title": a short, punchy title for this story if it doesn't already have one (under 12 words)
- "overall_score": integer 0-100, overall short-form video potential
- "sub_scores": object with these ten integer 0-100 ratings:
  - "virality": overall likelihood this specific story performs well if posted
  - "hook_strength": how strong the first sentence/line is at creating curiosity or surprise
  - "emotional_impact": how much genuine emotional weight the story has
  - "suspense": how much tension is built and sustained
  - "curiosity": how much the story makes a viewer need to know what happens next
  - "payoff_quality": how satisfying the ending/resolution is
  - "story_flow": how smoothly the narrative moves, no confusing jumps
  - "replay_potential": how likely someone is to watch it again or share it
  - "completion_prediction": how likely a viewer is to watch to the end without dropping off
  - "difficulty_to_adapt": how much editing/rewriting this would need before it's ready to narrate (higher = needs more work, lower = ready as-is)
- "estimated_watch_time_seconds": integer, realistic estimate of how long this story takes to narrate aloud at a natural pace
- "reasoning": 2-3 sentences explaining specifically why this story would (or wouldn't) retain viewers, written like an experienced editor giving feedback — be specific to this story's actual content, not generic praise
"""


def _extract_json_object(text: str) -> dict:
    """Same defensive-parse approach as llm_scorer._extract_json_array —
    models occasionally wrap JSON in a code fence or add stray text
    despite instructions."""
    text = text.strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if match:
        return json.loads(match.group(0))
    raise ValueError("Couldn't find a JSON object in the model's response.")


def _parse_analysis(item: dict) -> StoryAnalysis:
    sub_scores = {}
    raw_sub_scores = item.get("sub_scores")
    if isinstance(raw_sub_scores, dict):
        for key in ANALYST_METRICS:
            val = raw_sub_scores.get(key)
            if isinstance(val, (int, float)) and not isinstance(val, bool):
                sub_scores[key] = max(0, min(100, int(val)))

    overall = item.get("overall_score", 0)
    overall = max(0, min(100, int(overall))) if isinstance(overall, (int, float)) and not isinstance(overall, bool) else 0

    watch_time = item.get("estimated_watch_time_seconds", 0)
    watch_time = max(0, int(watch_time)) if isinstance(watch_time, (int, float)) and not isinstance(watch_time, bool) else 0

    return StoryAnalysis(
        overall_score=overall,
        sub_scores=sub_scores,
        estimated_watch_time_seconds=watch_time,
        reasoning=str(item.get("reasoning", "")).strip()[:600],
    )


def _call_claude(prompt: str, model: Optional[str], max_tokens: int) -> str:
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise RuntimeError("ANTHROPIC_API_KEY is not set.")

    import anthropic  # imported lazily, same pattern as llm_scorer.py

    client = anthropic.Anthropic(api_key=api_key)
    response = client.messages.create(
        model=model or DEFAULT_MODEL,
        max_tokens=max_tokens,
        messages=[{"role": "user", "content": prompt}],
    )
    return "".join(block.text for block in response.content if hasattr(block, "text"))


def generate_story(genre: str, model: Optional[str] = None) -> Story:
    """Generates a new story in the given genre and scores it in the same
    call — one round trip instead of generate-then-analyze separately,
    since the model already has full context on the story it just wrote.
    Raises RuntimeError/ValueError on failure; no silent fallback (unlike
    clip scoring, there's no heuristic story generator to fall back to)."""
    if genre not in GENRES:
        raise ValueError(f"Unknown genre: {genre!r}. Must be one of {GENRES}.")

    raw_text = _call_claude(
        GENERATE_PROMPT_TEMPLATE.format(genre=genre),
        model=model,
        max_tokens=2000,
    )
    item = _extract_json_object(raw_text)

    body = str(item.get("body", "")).strip()
    if not body:
        raise ValueError("Model response didn't contain a story body.")

    return Story(
        title=str(item.get("title", "")).strip()[:150] or "Untitled Story",
        body=body,
        genre=genre,
        analysis=_parse_analysis(item),
    )


def analyze_story(story_text: str, model: Optional[str] = None) -> Story:
    """Scores a user-submitted story (pasted text, personal script, etc.)
    without generating anything — same Analyst breakdown either way, so
    the UI can treat AI-generated and user-submitted stories identically
    once they're in hand."""
    story_text = (story_text or "").strip()
    if not story_text:
        raise ValueError("Story text is empty.")
    if len(story_text) > 8000:
        raise ValueError("Story text is too long (max 8000 characters).")

    raw_text = _call_claude(
        ANALYZE_PROMPT_TEMPLATE.format(story_text=story_text),
        model=model,
        max_tokens=1200,
    )
    item = _extract_json_object(raw_text)

    return Story(
        title=str(item.get("title", "")).strip()[:150] or "Untitled Story",
        body=story_text,
        genre="user_submitted",
        analysis=_parse_analysis(item),
    )
