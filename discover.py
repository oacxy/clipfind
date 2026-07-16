"""
Discover feed — surfaces videos worth clipping right now, instead of
requiring the user to already know which video to paste in.

Uses YouTube's official Data API v3 (a normal, sanctioned, key-based REST
API — not the unofficial scraping that transcript/video fetching relies
on, so it isn't subject to the same IP-blocking issues). Free quota is
10,000 units/day; search.list costs 100 units per call, videos.list and
channels.list cost 1 unit per call (batched up to 50 IDs), so a refresh
across a handful of niches comfortably fits in a day's free quota as long
as refreshes are cached rather than run per visitor.

The "evidence" behind each pick: view velocity relative to the channel's
subscriber count (views gained per hour since publish, normalized by
subscriber count) — a real, checkable number, not a vibe. The single best
clip inside each candidate video is found by reusing the exact same
scoring pipeline (LLM scorer, heuristic fallback) that /api/analyze uses
on a user-submitted link — no separate/duplicate judgment logic.
"""

import os
import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import List, Dict, Optional

import requests

from clipfind import fetch_youtube_transcript, score_transcript, build_clips
from llm_scorer import score_with_llm

YOUTUBE_API_BASE = "https://www.googleapis.com/youtube/v3"

# Curated niches matching the clipping/creator audience this tool is
# built for, rather than an open-ended search box — keeps results
# relevant and keeps API quota usage bounded and predictable.
NICHES = [
    "podcast interview",
    "motivational speech",
    "business advice",
    "startup founder interview",
]

LOOKBACK_HOURS = 48  # only consider videos published in this window
CANDIDATES_PER_NICHE = 6
FEED_SIZE = 8  # how many final picks to show/store

# Scoring each candidate (transcript fetch + LLM call) is the slow part —
# doing it one at a time for up to ~24 candidates can take minutes and blow
# past the server's request timeout. Two guardrails: only ever attempt a
# bounded number of candidates regardless of how many fail, and run the
# attempts concurrently (they're I/O-bound network calls, so threads are
# enough — no need for real parallelism).
MAX_CANDIDATES_TO_ATTEMPT = 14
SCORING_CONCURRENCY = 6


def _api_key() -> str:
    key = os.environ.get("YOUTUBE_API_KEY")
    if not key:
        raise RuntimeError("YOUTUBE_API_KEY is not set.")
    return key


def _search_recent_videos(query: str, api_key: str, max_results: int = 6) -> List[str]:
    published_after = (
        datetime.datetime.utcnow() - datetime.timedelta(hours=LOOKBACK_HOURS)
    ).strftime("%Y-%m-%dT%H:%M:%SZ")
    resp = requests.get(
        f"{YOUTUBE_API_BASE}/search",
        params={
            "part": "snippet",
            "q": query,
            "type": "video",
            "order": "viewCount",
            "publishedAfter": published_after,
            "maxResults": max_results,
            "key": api_key,
        },
        timeout=15,
    )
    resp.raise_for_status()
    return [item["id"]["videoId"] for item in resp.json().get("items", [])]


def _get_video_stats(video_ids: List[str], api_key: str) -> Dict[str, dict]:
    if not video_ids:
        return {}
    resp = requests.get(
        f"{YOUTUBE_API_BASE}/videos",
        params={
            "part": "statistics,snippet",
            "id": ",".join(video_ids),
            "key": api_key,
        },
        timeout=15,
    )
    resp.raise_for_status()
    out = {}
    for item in resp.json().get("items", []):
        out[item["id"]] = {
            "title": item["snippet"]["title"],
            "channel_title": item["snippet"]["channelTitle"],
            "channel_id": item["snippet"]["channelId"],
            "published_at": item["snippet"]["publishedAt"],
            "thumbnail": item["snippet"]["thumbnails"].get("medium", {}).get("url", ""),
            "view_count": int(item["statistics"].get("viewCount", 0)),
        }
    return out


def _get_channel_subscriber_counts(channel_ids: List[str], api_key: str) -> Dict[str, int]:
    if not channel_ids:
        return {}
    resp = requests.get(
        f"{YOUTUBE_API_BASE}/channels",
        params={
            "part": "statistics",
            "id": ",".join(set(channel_ids)),
            "key": api_key,
        },
        timeout=15,
    )
    resp.raise_for_status()
    out = {}
    for item in resp.json().get("items", []):
        stats = item.get("statistics", {})
        # hiddenSubscriberCount channels won't have this field
        out[item["id"]] = int(stats.get("subscriberCount", 0)) if not stats.get(
            "hiddenSubscriberCount", False
        ) else 0
    return out


def _velocity_score(view_count: int, published_at: str, subscriber_count: int) -> float:
    published = datetime.datetime.strptime(published_at, "%Y-%m-%dT%H:%M:%SZ")
    hours_since = max(
        1.0, (datetime.datetime.utcnow() - published).total_seconds() / 3600.0
    )
    views_per_hour = view_count / hours_since
    # Normalize by subscriber count so small and large channels are
    # comparable; floor subscriber count to avoid divide-by-zero blowing
    # up the ranking for brand-new/hidden-count channels.
    denom = max(subscriber_count, 1000)
    return round((views_per_hour / denom) * 1000, 3)


def _best_clip_for_video(video_id: str) -> Optional[dict]:
    """Reuses the exact same scoring pipeline /api/analyze uses — no
    separate judgment logic to keep in sync."""
    try:
        lines = fetch_youtube_transcript(video_id)
    except Exception:
        return None
    if not lines:
        return None

    try:
        clips = score_with_llm(lines, top_n=1)
        method = "llm"
    except Exception:
        try:
            scored = score_transcript(lines)
            clips = build_clips(scored, top_n=1)
            method = "heuristic"
        except Exception:
            return None

    if not clips:
        return None
    top = clips[0]
    return {
        "hook": top.hook,
        "reasoning": top.reasoning,
        "score": top.score,
        "start_seconds": round(max(top.start, 0), 2),
        "end_seconds": round(top.end, 2),
        "scoring_method": method,
    }


def build_discover_feed(feed_size: int = FEED_SIZE) -> List[dict]:
    """Search across the curated niches, rank candidates by view
    velocity, then run the top handful through the real clip scorer.
    Returns a list of dicts ready to store/serve — raises on total
    failure (e.g. bad/missing API key) so the caller can decide how to
    handle an empty feed."""
    api_key = _api_key()

    all_candidates: Dict[str, dict] = {}
    for niche in NICHES:
        try:
            video_ids = _search_recent_videos(niche, api_key, max_results=CANDIDATES_PER_NICHE)
        except requests.RequestException as e:
            # One bad niche query shouldn't kill the whole refresh — log
            # and move on to the next niche. Catches HTTP errors (bad
            # key, quota) as well as timeouts/connection errors, not
            # just HTTPError.
            print(f"[DISCOVER] search failed for {niche!r}: {e}", flush=True)
            continue
        stats = _get_video_stats(video_ids, api_key)
        all_candidates.update(stats)
        for vid, info in stats.items():
            info["video_id"] = vid
            info["niche"] = niche

    if not all_candidates:
        return []

    channel_ids = [info["channel_id"] for info in all_candidates.values()]
    subs = _get_channel_subscriber_counts(channel_ids, api_key)

    ranked = []
    for vid, info in all_candidates.items():
        sub_count = subs.get(info["channel_id"], 0)
        info["velocity_score"] = _velocity_score(
            info["view_count"], info["published_at"], sub_count
        )
        info["subscriber_count"] = sub_count
        ranked.append(info)

    ranked.sort(key=lambda x: x["velocity_score"], reverse=True)

    # Only attempt scoring on a bounded slice of the ranked list — trying
    # every single candidate (transcript fetch + LLM call each) is what
    # was pushing this past the request timeout. Attempting them
    # concurrently instead of one-by-one is what actually keeps wall time
    # down; the cap is just a backstop for a genuinely bad batch.
    to_attempt = ranked[:MAX_CANDIDATES_TO_ATTEMPT]

    results: Dict[str, Optional[dict]] = {}
    with ThreadPoolExecutor(max_workers=SCORING_CONCURRENCY) as pool:
        futures = {
            pool.submit(_best_clip_for_video, info["video_id"]): info["video_id"]
            for info in to_attempt
        }
        for future in as_completed(futures):
            video_id = futures[future]
            try:
                results[video_id] = future.result()
            except Exception as e:
                print(f"[DISCOVER] scoring crashed for {video_id}: {e}", flush=True)
                results[video_id] = None

    feed = []
    for info in to_attempt:
        if len(feed) >= feed_size:
            break
        clip = results.get(info["video_id"])
        if clip is None:
            continue  # e.g. captions disabled — skip, next candidate
        feed.append({**info, "clip": clip})

    return feed
