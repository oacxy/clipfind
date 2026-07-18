"""
Export copy generator: turns a saved clip's hook/reasoning into a
ready-to-post title, hashtag set, and short description for a given
platform (TikTok, YouTube Shorts, Instagram Reels).

Deliberately doesn't re-fetch the source video's transcript — the hook
and reasoning already saved on the clip (from the original Analyst
scoring or Focus Mode search) are enough context for a short caption-style
writeup, and skipping the re-fetch keeps this fast and cheap since it
only runs when a user explicitly asks to export a specific saved clip,
not on every clip automatically.
"""

import os
import re
import json
from typing import Optional

DEFAULT_MODEL = os.environ.get("ANTHROPIC_MODEL", "claude-haiku-4-5-20251001")

PLATFORM_NOTES = {
    "tiktok": "TikTok — punchy, a little chaotic/casual is fine, hashtags can mix broad (#fyp) and niche.",
    "shorts": "YouTube Shorts — title should work like a search-friendly headline, slightly more polished tone.",
    "reels": "Instagram Reels — clean, aesthetic-forward caption tone, hashtags can include a couple of broader lifestyle tags.",
}

PROMPT_TEMPLATE = """Write ready-to-post social copy for a short-form video clip being exported to {platform_name} ({platform_notes}).

Clip hook: "{hook}"
Why this clip works: "{reasoning}"

Respond with ONLY a JSON object, no markdown code fences, no other text. Exactly these fields:
- "title": a short, scroll-stopping title/caption for the post, under 80 characters
- "hashtags": an array of 5-8 relevant hashtag strings (no spaces, each starting with #)
- "description": a 1-2 sentence post description/caption body, under 200 characters

Write it like a real creator would post it — not generic, specific to what actually happens in this clip.
"""


def _extract_json_object(text: str) -> dict:
    text = text.strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if match:
        return json.loads(match.group(0))
    raise ValueError("Couldn't find a JSON object in the model's response.")


def generate_export_copy(hook: str, reasoning: str, platform: str = "tiktok", model: Optional[str] = None) -> dict:
    """Returns {"title": str, "hashtags": [str, ...], "description": str}.
    Raises on real failures (no API key, unparseable response) — there's
    no sensible "empty" fallback for this the way Focus Mode has a valid
    zero-matches case, so callers should catch and surface an error."""
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise RuntimeError("ANTHROPIC_API_KEY is not set.")

    platform_key = (platform or "tiktok").strip().lower()
    if platform_key not in PLATFORM_NOTES:
        platform_key = "tiktok"
    platform_names = {"tiktok": "TikTok", "shorts": "YouTube Shorts", "reels": "Instagram Reels"}

    import anthropic  # imported lazily, same pattern as llm_scorer.py/focus_mode.py

    client = anthropic.Anthropic(api_key=api_key)
    prompt = PROMPT_TEMPLATE.format(
        platform_name=platform_names[platform_key],
        platform_notes=PLATFORM_NOTES[platform_key],
        hook=(hook or "").strip()[:300],
        reasoning=(reasoning or "").strip()[:500],
    )

    response = client.messages.create(
        model=model or DEFAULT_MODEL,
        max_tokens=500,
        messages=[{"role": "user", "content": prompt}],
    )
    raw_text = "".join(block.text for block in response.content if hasattr(block, "text"))
    parsed = _extract_json_object(raw_text)

    title = str(parsed.get("title", "")).strip()[:200]

    hashtags = []
    raw_hashtags = parsed.get("hashtags")
    if isinstance(raw_hashtags, list):
        for h in raw_hashtags[:10]:
            if not isinstance(h, str):
                continue
            h = h.strip()
            if not h:
                continue
            if not h.startswith("#"):
                h = f"#{h}"
            hashtags.append(h[:50])

    description = str(parsed.get("description", "")).strip()[:500]

    return {"title": title, "hashtags": hashtags, "description": description}
