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
# relevant and keeps API quota usage bounded and predictable. Grouped
# under category keys so the frontend can offer a genre filter (Gaming,
# Comedy, etc.) over a single shared feed refresh instead of needing a
# separate YouTube search — and separate quota spend — per category per
# visitor. Each category maps to a list so a category can later gain a
# second query without restructuring anything.
CATEGORY_NICHES: Dict[str, List[str]] = {
    "podcasts": ["podcast interview"],
    "business": ["business advice"],
    "motivation": ["motivational speech"],
    "startups": ["startup founder interview"],
    "gaming": ["gaming highlights"],
    "comedy": ["stand up comedy funny moments"],
    "sports": ["sports highlights"],
    "education": ["educational explainer"],
}

LOOKBACK_HOURS = 48  # only consider videos published in this window
CANDIDATES_PER_NICHE = 6
FEED_SIZE = 16  # how many final picks to show/store — bumped alongside the
# category split below so each of the 8 categories has a reasonable shot
# at showing up rather than a small shared pool getting dominated by
# whichever niche happens to have the highest-velocity videos this hour.

# Scoring each candidate (transcript fetch + LLM call) is the slow part —
# doing it one at a time for up to ~24 candidates can take minutes and blow
# past the server's request timeout. Two guardrails: only ever attempt a
# bounded number of candidates regardless of how many fail, and run the
# attempts concurrently (they're I/O-bound network calls, so threads are
# enough — no need for real parallelism). Set to 2x the category count so
# the round-robin selection below (see build_discover_feed) can give every
# category up to 2 scoring attempts before any category gets a 3rd.
MAX_CANDIDATES_TO_ATTEMPT = 16
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
    except Exception as e:
        # Deliberately quiet at the per-candidate level (Discover tries
        # ~16 candidates per refresh and a handful of misses — disabled
        # captions, private videos — is normal) *except* when the failure
        # looks like the proxy itself is down, since that would silently
        # tank every candidate in the refresh and is worth a signal in
        # the logs rather than just "0 picks" with no explanation.
        msg = str(e)
        if "ProxyError" in msg or "Max retries" in msg:
            print(f"[DISCOVER] proxy fetch failed for {video_id}: {type(e).__name__}: {e}", flush=True)
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
    """Search across the curated category niches, then run a
    round-robin-selected slice of candidates through the real clip
    scorer. Returns a list of dicts ready to store/serve — raises on
    total failure (e.g. bad/missing API key) so the caller can decide
    how to handle an empty feed."""
    api_key = _api_key()

    niche_to_category = {
        niche: category
        for category, niches in CATEGORY_NICHES.items()
        for niche in niches
    }

    all_candidates: Dict[str, dict] = {}
    video_ids_by_niche: Dict[str, List[str]] = {}
    for niche, category in niche_to_category.items():
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
        video_ids_by_niche[niche] = list(stats.keys())
        for vid, info in stats.items():
            info["video_id"] = vid
            info["niche"] = niche
            info["category"] = category

    if not all_candidates:
        return []

    channel_ids = [info["channel_id"] for info in all_candidates.values()]
    subs = _get_channel_subscriber_counts(channel_ids, api_key)

    for info in all_candidates.values():
        sub_count = subs.get(info["channel_id"], 0)
        info["velocity_score"] = _velocity_score(
            info["view_count"], info["published_at"], sub_count
        )
        info["subscriber_count"] = sub_count

    # Rank candidates within each niche separately (not just globally) so
    # the round-robin below can pull fairly from every category.
    ranked_by_niche: Dict[str, List[dict]] = {}
    for niche, vids in video_ids_by_niche.items():
        items = [all_candidates[v] for v in vids if v in all_candidates]
        items.sort(key=lambda x: x["velocity_score"], reverse=True)
        ranked_by_niche[niche] = items

    # Round-robin one candidate per niche per pass, instead of ranking
    # every candidate globally by velocity and taking the top N. A pure
    # global ranking tends to let a couple of naturally higher-velocity
    # niches (gaming/comedy content usually spikes harder than, say,
    # business advice) crowd out every scoring slot — which would leave
    # other category filters on the frontend empty most of the time.
    # This guarantees every niche gets at least one shot at being scored
    # before any niche gets a second.
    to_attempt: List[dict] = []
    niche_order = list(ranked_by_niche.keys())
    round_idx = 0
    while len(to_attempt) < MAX_CANDIDATES_TO_ATTEMPT:
        added_this_round = False
        for niche in niche_order:
            if round_idx < len(ranked_by_niche[niche]):
                to_attempt.append(ranked_by_niche[niche][round_idx])
                added_this_round = True
                if len(to_attempt) >= MAX_CANDIDATES_TO_ATTEMPT:
                    break
        if not added_this_round:
            break  # every niche's candidate list is exhausted
        round_idx += 1

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
