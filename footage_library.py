"""
footage_library.py
===================
Background footage library for Story Studio — downloads and catalogs
long-form "background gameplay/relaxation" video (Minecraft parkour,
Subway Surfers, satisfying compilations, etc.) used as the visual layer
under an AI-narrated story's voiceover. This is the same well-known
Reddit-story-short format: a voiceover the viewer listens to, paired
with unrelated eye-catching footage that keeps the video watchable.

Reuses the exact proxy + cookie-auth + retry pipeline already proven out
in app.py's cut_youtube_clip for regular clip downloads — the same
YouTube bot-detection wall applies here, so the same workarounds
(rotating residential proxy, real session cookies, fresh-connection
retries) are needed. get_proxy_url/_is_transient_proxy_error are kept as
self-contained copies rather than imported from app.py, to avoid a
footage_library <-> app.py circular import (app.py will import THIS
module for its Story Studio routes, added in a later step).

Copyright note: like the rest of ClipFind's clip-finding feature,
footage is downloaded from YouTube and reused without a license — an
accepted product risk (confirmed with the product owner), not an
oversight.
"""

import os
import time
import uuid
import shutil
import subprocess
import tempfile
import datetime
from typing import Optional, List

# Eight categories covering the genres this format is actually built
# around — gameplay loops (parkour/endless-runner/stunts) for viewers who
# want visual noise while they listen, plus calmer options (nature,
# cooking, gym, night drive) for stories that don't suit chaotic gameplay
# footage. "hint" is a suggested search phrase for whoever is sourcing
# source video URLs to feed into download_footage — not used
# programmatically (no YouTube search API call here, URLs are supplied
# directly), just a pointer for a human picking videos.
FOOTAGE_CATEGORIES = [
    {"key": "minecraft_parkour", "label": "Minecraft Parkour", "hint": "minecraft parkour gameplay no commentary"},
    {"key": "subway_surfers", "label": "Subway Surfers", "hint": "subway surfers gameplay long"},
    {"key": "gta_stunts", "label": "GTA Stunts", "hint": "gta 5 stunts gameplay no commentary"},
    {"key": "satisfying", "label": "Satisfying", "hint": "oddly satisfying video compilation"},
    {"key": "nature_walk", "label": "Nature Walk", "hint": "relaxing nature walk 4k"},
    {"key": "cooking_asmr", "label": "Cooking ASMR", "hint": "cooking asmr no talking"},
    {"key": "gym_workout", "label": "Gym Workout", "hint": "gym workout motivation no music"},
    {"key": "city_drive", "label": "City Drive", "hint": "night city drive relaxing"},
]
FOOTAGE_CATEGORY_KEYS = {c["key"] for c in FOOTAGE_CATEGORIES}

# Render's default web service filesystem is EPHEMERAL — everything
# written to it is wiped on every deploy and restart. That's invisible
# for something short-lived (a cut clip a user downloads right after
# cutting), but this footage library is explicitly meant to persist and
# get reused across every story generated afterward — a routine redeploy
# silently wiped an uploaded clip the same day it was added (August
# 2026), which then surfaced as a confusing "footage no longer
# available" error on the next video generation. PERSISTENT_DATA_DIR
# should point at a Render persistent Disk's mount path (e.g. /var/data)
# once one is attached to the service; falls back to a local folder next
# to this file so nothing breaks running locally without one configured.
_DATA_ROOT = os.environ.get("PERSISTENT_DATA_DIR") or os.path.dirname(os.path.abspath(__file__))
FOOTAGE_DIR = os.path.join(_DATA_ROOT, "footage_library")
os.makedirs(FOOTAGE_DIR, exist_ok=True)

# Background footage just needs to be long enough to cover a handful of
# stories before a viewer might see the same loop twice — not the full
# runtime of the (often hour-long) source videos people upload of this
# stuff. Capping the download keeps both disk usage and Webshare
# bandwidth bounded per source video.
DEFAULT_MAX_MINUTES = 15
DEFAULT_MAX_HEIGHT = 720


def get_proxy_url() -> Optional[str]:
    """Same Webshare rotating-residential proxy as app.py's get_proxy_url
    — duplicated here (not imported) to keep this module import-safe from
    app.py. See that function's docstring for the full rationale."""
    username = os.environ.get("WEBSHARE_PROXY_USERNAME")
    password = os.environ.get("WEBSHARE_PROXY_PASSWORD")
    if username and password:
        return f"http://{username}:{password}@p.webshare.io:80"
    return None


def _is_transient_proxy_error(msg: str) -> bool:
    """Same substrings clipfind.py/app.py already treat as "worth a
    fresh-connection retry" rather than failing fast on."""
    transient_markers = (
        "ProxyError", "Max retries", "Connection reset",
        "Connection aborted", "timed out", "Timeout",
    )
    return any(m in msg for m in transient_markers)


def _probe_duration(path: str) -> float:
    result = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", path],
        capture_output=True, text=True, timeout=15, check=True,
    )
    return round(float(result.stdout.strip()), 2)


def list_footage(category: Optional[str] = None) -> List[dict]:
    """Filesystem-backed inventory listing — reads whatever's actually on
    disk rather than trusting a DB to stay in sync with it. Good enough
    for a library that's only ever written to by download_footage (no
    concurrent writers), and means this module works standalone before
    the DB-model layer (Task #62) exists on top of it."""
    out = []
    categories = [category] if category else [c["key"] for c in FOOTAGE_CATEGORIES]
    for cat in categories:
        cat_dir = os.path.join(FOOTAGE_DIR, cat)
        if not os.path.isdir(cat_dir):
            continue
        for fname in sorted(os.listdir(cat_dir)):
            if not fname.endswith(".mp4"):
                continue
            path = os.path.join(cat_dir, fname)
            out.append({
                "id": os.path.splitext(fname)[0],
                "category": cat,
                "file_path": path,
                "size_bytes": os.path.getsize(path),
            })
    return out


def download_footage(
    youtube_url: str,
    category: str,
    max_minutes: int = DEFAULT_MAX_MINUTES,
    max_height: int = DEFAULT_MAX_HEIGHT,
    footage_id: Optional[str] = None,
) -> dict:
    """Downloads (up to max_minutes of) a YouTube video into the footage
    library, categorized for later Smart Pairing (matching a story's mood
    to a footage category — that matching logic comes with the video
    assembly pipeline, Task #61).

    footage_id: pass this when a caller already created a placeholder DB
    row (e.g. a "downloading" BackgroundFootage row) before calling this
    function, so the file this writes lines up with that row's id instead
    of getting a second, orphaned uuid.

    Returns a metadata dict a caller can hand straight to a
    BackgroundFootage DB row once that model exists (Task #62). Raises
    RuntimeError with a message safe to show to whoever's sourcing
    footage — mirrors cut_youtube_clip's error-branching so an expired
    cookie file surfaces the same actionable message here too, instead of
    a confusing "footage download broke" with no next step.
    """
    if category not in FOOTAGE_CATEGORY_KEYS:
        raise RuntimeError(
            f"Unknown footage category: {category!r}. Valid categories: "
            f"{', '.join(sorted(FOOTAGE_CATEGORY_KEYS))}."
        )
    if shutil.which("ffmpeg") is None:
        raise RuntimeError(
            "ffmpeg isn't installed on this server. This app needs to be deployed with the "
            "Dockerfile (which installs ffmpeg) rather than a plain Python runtime — see DEPLOY.md."
        )

    import yt_dlp  # lazy import, same reasoning as cut_youtube_clip

    footage_id = footage_id or uuid.uuid4().hex[:12]
    workdir = tempfile.mkdtemp(prefix=f"footage_{footage_id}_")
    raw_template = os.path.join(workdir, "raw.%(ext)s")
    category_dir = os.path.join(FOOTAGE_DIR, category)
    os.makedirs(category_dir, exist_ok=True)
    out_path = os.path.join(category_dir, f"{footage_id}.mp4")

    max_seconds = max_minutes * 60

    ydl_opts = {
        # Same progressive-mp4-first-then-merge fallback chain as
        # cut_youtube_clip — YouTube only serves pre-merged mp4 up to
        # ~720p, anything higher needs a video+audio merge.
        "format": (
            f"bestvideo[height<={max_height}][ext=mp4]+bestaudio[ext=m4a]"
            f"/bestvideo[height<={max_height}]+bestaudio"
            f"/best[height<={max_height}]/best"
        ),
        "merge_output_format": "mp4",
        "outtmpl": raw_template,
        "download_ranges": yt_dlp.utils.download_range_func(None, [(0, max_seconds)]),
        "force_keyframes_at_cuts": True,
        "noplaylist": True,
        "quiet": True,
        "no_warnings": True,
        # Bounded so a bad proxy connection fails within seconds instead
        # of hanging — same fix applied to cut_youtube_clip after the
        # earlier "Network error" debugging.
        "socket_timeout": 15,
        "retries": 3,
        "fragment_retries": 3,
    }
    proxy = get_proxy_url()
    if proxy:
        ydl_opts["proxy"] = proxy

    from clipfind import get_cookiefile_path
    source_cookiefile = get_cookiefile_path()
    cookiefile = None
    if source_cookiefile:
        # yt-dlp writes back to its cookiefile, and Render's Secret Files
        # are read-only — same "Read-only file system" bug fixed in
        # cut_youtube_clip. Copy into this download's own scratch workdir
        # first so yt-dlp always has a writable copy.
        cookiefile = os.path.join(workdir, "cookies.txt")
        shutil.copyfile(source_cookiefile, cookiefile)
        ydl_opts["cookiefile"] = cookiefile

    title = youtube_url
    download_error = None
    for attempt in range(3):
        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(youtube_url, download=True)
                title = (info or {}).get("title", youtube_url)
            download_error = None
            break
        except Exception as e:
            download_error = e
            msg = str(e)
            print(
                f"[FOOTAGE_DOWNLOAD_FAILED] attempt={attempt + 1} url={youtube_url!r} "
                f"category={category} {type(e).__name__}: {e}",
                flush=True,
            )
            if not _is_transient_proxy_error(msg) or attempt == 2:
                break
            time.sleep(1.5 * (attempt + 1))

    if download_error is not None:
        shutil.rmtree(workdir, ignore_errors=True)
        msg = str(download_error)
        if "blocking requests from your IP" in msg or "Sign in to confirm" in msg or "not a bot" in msg:
            if cookiefile:
                raise RuntimeError(
                    "YouTube's bot-detection is still blocking downloads even with cookies "
                    "configured — the cookies file has likely expired. Re-export cookies.txt "
                    "from a logged-in YouTube session and update the Secret File/YOUTUBE_COOKIES_PATH."
                )
            raise RuntimeError(
                "YouTube is blocking this download. This needs real YouTube session cookies "
                "passed to yt-dlp — see DEPLOY.md ('YouTube cookies' section) for how to set that up."
            )
        raise RuntimeError(f"Couldn't download that footage source ({msg[-200:]}).")

    raw_files = [f for f in os.listdir(workdir) if f.startswith("raw.")]
    if not raw_files:
        shutil.rmtree(workdir, ignore_errors=True)
        raise RuntimeError("Download didn't produce a video file.")
    raw_path = os.path.join(workdir, raw_files[0])
    shutil.move(raw_path, out_path)
    shutil.rmtree(workdir, ignore_errors=True)

    duration = _probe_duration(out_path)

    return {
        "id": footage_id,
        "category": category,
        "source_url": youtube_url,
        "title": title,
        "file_path": out_path,
        "duration_seconds": duration,
        "downloaded_at": datetime.datetime.utcnow(),
    }
