#!/usr/bin/env python3
"""
ClipFind web app
=================
A real browser-usable version of clipfind.py: paste a YouTube link, get
ranked clip suggestions back. No Python knowledge required to use it.

Run locally:
    pip install -r requirements.txt
    python3 app.py
    -> open http://localhost:5000

Deploy: see DEPLOY.md for Render/Railway instructions (this needs a real
server with normal outbound internet access to reach YouTube — see the
note in DEPLOY.md about sandboxed environments that block that).
"""

import os
import json
import uuid
import shutil
import datetime
import functools
import subprocess
import tempfile
from typing import Optional, Dict

import stripe
from flask import Flask, request, jsonify, render_template, send_from_directory
from flask_login import (
    LoginManager,
    login_user,
    logout_user,
    login_required,
    current_user,
)

from llm_scorer import score_with_llm
from focus_mode import find_moments_with_llm
from export_copy import generate_export_copy
from discover import build_discover_feed
from digest import send_digest_emails, generate_unsubscribe_token, verify_unsubscribe_token
from captions import build_ass_subtitle, chunk_captions_for_clip, STYLE_PRESETS, DEFAULT_STYLE

from clipfind import (
    fetch_youtube_transcript,
    fetch_youtube_transcript_raw,
    load_transcript,
    score_transcript,
    build_clips,
    fmt_timestamp,
    parse_timestamp,
)
from models import db, User, DiscoverFeed, SavedClip, FREE_DAILY_LIMIT, PAID_MAX_CLIP_SECONDS

app = Flask(__name__)

DEMO_TRANSCRIPT_PATH = "sample_transcript.txt"
CLIPS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "clips_output")
os.makedirs(CLIPS_DIR, exist_ok=True)

# --- Database ---------------------------------------------------------
# DATABASE_URL is provided automatically by Render's Postgres add-on.
# Falls back to a local SQLite file so this runs with zero setup locally.
db_url = os.environ.get("DATABASE_URL", "sqlite:///clipfind.db")
# Render (like Heroku) hands out "postgres://" but SQLAlchemy 1.4+ wants "postgresql://"
if db_url.startswith("postgres://"):
    db_url = db_url.replace("postgres://", "postgresql://", 1)
app.config["SQLALCHEMY_DATABASE_URI"] = db_url
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
app.secret_key = os.environ.get("SECRET_KEY", "dev-only-insecure-key-set-SECRET_KEY-in-prod")
db.init_app(app)

def _ensure_email_opt_in_column():
    """db.create_all() only creates missing tables — it never alters an
    existing one. The production `users` table already has real rows, so
    adding the new email_opt_in field to the model needs an actual ALTER
    TABLE the first time this new code runs. Safe to call on every
    startup: it checks whether the column's already there first, so
    it's a no-op after the first successful run."""
    from sqlalchemy import inspect, text

    inspector = inspect(db.engine)
    existing_cols = {c["name"] for c in inspector.get_columns("users")}
    if "email_opt_in" not in existing_cols:
        with db.engine.connect() as conn:
            conn.execute(
                text("ALTER TABLE users ADD COLUMN email_opt_in BOOLEAN NOT NULL DEFAULT TRUE")
            )
            conn.commit()
        print("[MIGRATION] added users.email_opt_in", flush=True)


with app.app_context():
    db.create_all()
    _ensure_email_opt_in_column()

# --- Auth ---------------------------------------------------------------
login_manager = LoginManager()
login_manager.init_app(app)


@login_manager.user_loader
def load_user(user_id):
    return db.session.get(User, int(user_id))


def json_login_required(view):
    """Like flask_login's @login_required, but returns a JSON 401 instead
    of redirecting to a login page — this app has no server-rendered login
    page, it's all API calls from the single-page frontend."""

    @functools.wraps(view)
    def wrapped(*args, **kwargs):
        if not current_user.is_authenticated:
            return jsonify({"error": "Sign in first.", "auth_required": True}), 401
        return view(*args, **kwargs)

    return wrapped


# --- Stripe ---------------------------------------------------------------
stripe.api_key = os.environ.get("STRIPE_SECRET_KEY", "")
STRIPE_PRICE_ID = os.environ.get("STRIPE_PRICE_ID", "")
STRIPE_WEBHOOK_SECRET = os.environ.get("STRIPE_WEBHOOK_SECRET", "")

# --- Email digest -----------------------------------------------------------
RESEND_API_KEY = os.environ.get("RESEND_API_KEY", "")
# Until a real domain is verified with Resend, this has to stay
# onboarding@resend.dev — Resend only lets unverified senders deliver to
# the account owner's own inbox, which is fine for testing but won't
# reach real users. Update this (and verify the domain) once clipfind.com
# is set up with Resend.
DIGEST_FROM_EMAIL = os.environ.get("DIGEST_FROM_EMAIL", "ClipFind <onboarding@resend.dev>")
# Shared secret the external cron pinger has to send so randoms on the
# internet can't trigger mass emails by hitting the endpoint themselves.
CRON_SECRET = os.environ.get("CRON_SECRET", "")


def clips_to_json(clips):
    out = []
    for c in clips:
        preview = " ".join(l.text for l in c.lines)
        if len(preview) > 240:
            preview = preview[:240] + "..."
        start_seconds = max(c.start, 0)
        out.append(
            {
                "start": fmt_timestamp(start_seconds),
                "end": fmt_timestamp(c.end),
                "start_seconds": round(start_seconds, 2),
                "end_seconds": round(c.end, 2),
                "score": c.score,
                "hook": c.hook,
                "caption": c.hook.strip().rstrip("."),
                "preview": preview,
                "reasoning": c.reasoning,
                "sub_scores": c.sub_scores,
                "suggestions": c.suggestions,
            }
        )
    return out


def saved_clip_to_json(c: SavedClip) -> dict:
    hashtags = []
    if c.export_hashtags:
        try:
            hashtags = json.loads(c.export_hashtags)
        except (ValueError, TypeError):
            hashtags = []
    return {
        "id": c.id,
        "collection_name": c.collection_name,
        "youtube_url": c.youtube_url,
        "start_seconds": c.start_seconds,
        "end_seconds": c.end_seconds,
        "start": fmt_timestamp(max(c.start_seconds, 0)),
        "end": fmt_timestamp(c.end_seconds),
        "hook": c.hook,
        "reasoning": c.reasoning,
        "score": c.score,
        "export_title": c.export_title,
        "export_hashtags": hashtags,
        "export_description": c.export_description,
    }


def get_proxy_url():
    """Same Webshare rotating-residential proxy used for transcript fetches
    (WEBSHARE_PROXY_USERNAME / WEBSHARE_PROXY_PASSWORD). yt-dlp doesn't have
    the youtube-transcript-api library's built-in Webshare integration, so
    this builds the raw proxy URL yt-dlp's --proxy flag expects.
    Endpoint per Webshare's docs: p.webshare.io:80, HTTP-proxy protocol,
    credentials embedded in the URL."""
    username = os.environ.get("WEBSHARE_PROXY_USERNAME")
    password = os.environ.get("WEBSHARE_PROXY_PASSWORD")
    if username and password:
        return f"http://{username}:{password}@p.webshare.io:80"
    return None


def _to_seconds(value):
    """Accept either a raw number of seconds or a clipfind-style MM:SS /
    HH:MM:SS string, and always return a float number of seconds."""
    if isinstance(value, (int, float)):
        return float(value)
    return parse_timestamp(str(value))


def _probe_dimensions(path: str):
    """Returns (width, height) of a video file via ffprobe (bundled
    alongside ffmpeg). Needed before burning captions/cropping since the
    .ass file's PlayResX/Y and the crop filter's math both depend on the
    actual pixel dimensions of what's about to be re-encoded."""
    result = subprocess.run(
        [
            "ffprobe", "-v", "error", "-select_streams", "v:0",
            "-show_entries", "stream=width,height",
            "-of", "csv=s=x:p=0", path,
        ],
        capture_output=True, text=True, timeout=15, check=True,
    )
    width_str, height_str = result.stdout.strip().split("x")
    return int(width_str), int(height_str)


def cut_youtube_clip(
    youtube_url: str,
    start_seconds: float,
    end_seconds: float,
    max_seconds: int = 90,
    max_height: int = 480,
    lines: Optional[list] = None,
    captions: bool = False,
    caption_style: str = DEFAULT_STYLE,
    vertical: bool = False,
) -> str:
    """Download just the needed section of a YouTube video with yt-dlp,
    trim it precisely with ffmpeg, and return the filename (inside
    CLIPS_DIR) of the resulting mp4. Raises RuntimeError with a message
    safe to show the user on failure.

    max_seconds/max_height are tier-gated by the caller (free vs paid) —
    free users get shorter, lower-res clips since actual video download
    is the real Webshare-bandwidth cost, unlike text-only transcript
    fetches.

    captions/caption_style/vertical are the paid-tier extras: burning
    styled captions in and/or cropping to 9:16 both require a second
    ffmpeg re-encode pass (can't stream-copy once a video filter's
    involved), so they're kept optional rather than always running."""
    if end_seconds <= start_seconds:
        raise RuntimeError("End must be after start.")
    if end_seconds - start_seconds > max_seconds:
        raise RuntimeError(
            f"Clips longer than {max_seconds} seconds aren't available on your plan "
            f"{'yet' if max_seconds >= PAID_MAX_CLIP_SECONDS else '— upgrade for longer clips'}."
        )

    if shutil.which("ffmpeg") is None:
        raise RuntimeError(
            "ffmpeg isn't installed on this server. This app needs to be deployed with the "
            "Dockerfile (which installs ffmpeg) rather than a plain Python runtime — see DEPLOY.md."
        )

    import yt_dlp  # imported lazily so /api/analyze keeps working even if this dep is missing

    clip_id = uuid.uuid4().hex[:12]
    workdir = tempfile.mkdtemp(prefix=f"clipfind_{clip_id}_")
    raw_template = os.path.join(workdir, "raw.%(ext)s")
    out_path = os.path.join(CLIPS_DIR, f"{clip_id}.mp4")

    # Pad a couple seconds on each side so ffmpeg has keyframes to work
    # with, then trim to the exact requested window below.
    pad = 2.0
    section_start = max(0.0, start_seconds - pad)
    section_end = end_seconds + pad

    ydl_opts = {
        # YouTube only offers pre-merged progressive mp4 up to ~720p —
        # anything higher (the whole point of raising max_height past
        # 720) only exists as separate video-only + audio-only streams
        # that need merging. The old selector (`mp4[height<=X]/mp4/best`)
        # matched progressive mp4 first, which silently capped real
        # quality around 720p no matter what max_height said. This tries
        # a native mp4+m4a merge first (compatible container, cheap),
        # then falls back to merging whatever the best available streams
        # are regardless of container — yt-dlp/ffmpeg remux it either way.
        "format": (
            f"bestvideo[height<={max_height}][ext=mp4]+bestaudio[ext=m4a]"
            f"/bestvideo[height<={max_height}]+bestaudio"
            f"/best[height<={max_height}]/best"
        ),
        "merge_output_format": "mp4",
        "outtmpl": raw_template,
        "download_ranges": yt_dlp.utils.download_range_func(None, [(section_start, section_end)]),
        "force_keyframes_at_cuts": True,
        "noplaylist": True,
        "quiet": True,
        "no_warnings": True,
    }
    proxy = get_proxy_url()
    if proxy:
        ydl_opts["proxy"] = proxy

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            ydl.download([youtube_url])
    except Exception as e:
        shutil.rmtree(workdir, ignore_errors=True)
        msg = str(e)
        if "blocking requests from your IP" in msg or "Sign in to confirm" in msg or "not a bot" in msg:
            raise RuntimeError(
                "YouTube is blocking video downloads from this server's IP — check the "
                "WEBSHARE_PROXY_USERNAME/PASSWORD env vars are set correctly."
            )
        raise RuntimeError(f"Couldn't download that section of the video ({msg[-200:]}).")

    raw_files = [f for f in os.listdir(workdir) if f.startswith("raw.")]
    if not raw_files:
        shutil.rmtree(workdir, ignore_errors=True)
        raise RuntimeError("Download didn't produce a video file.")
    raw_path = os.path.join(workdir, raw_files[0])

    relative_start = max(0.0, start_seconds - section_start)
    duration = end_seconds - start_seconds

    if captions or vertical:
        # Captions/crop need a video filter, which means a re-encode no
        # matter what — so trim, crop, and caption burn-in all happen in
        # ONE ffmpeg pass here, straight from the raw download. Doing the
        # trim separately first and then a second re-encode pass for
        # crop/captions (the original approach) meant re-encoding an
        # already-encoded file — real, visible quality loss on top of an
        # already-lossy step, for no benefit.
        try:
            _cut_with_captions_and_crop(
                raw_path=raw_path,
                out_path=out_path,
                workdir=workdir,
                relative_start=relative_start,
                duration=duration,
                clip_start=start_seconds,
                clip_end=end_seconds,
                lines=lines or [],
                captions=captions,
                caption_style=caption_style,
                vertical=vertical,
            )
        except Exception as e:
            shutil.rmtree(workdir, ignore_errors=True)
            raise RuntimeError(f"Couldn't cut/style that clip ({e}).")
    else:
        def run_ffmpeg(extra_args):
            cmd = ["ffmpeg", "-y", "-ss", str(relative_start), "-i", raw_path, "-t", str(duration)]
            cmd += extra_args + [out_path]
            subprocess.run(cmd, check=True, capture_output=True, timeout=120)

        try:
            # Fast path: stream copy (no re-encode, no quality loss at
            # all). Fails if the cut point doesn't land near a keyframe,
            # in which case we fall back to a re-encode below.
            run_ffmpeg(["-c", "copy"])
        except subprocess.CalledProcessError:
            try:
                # CRF 18 ("visually lossless" territory) + preset medium
                # (better compression efficiency than fast, at the cost of
                # some encode time — worth it since clips are short, up to
                # 180s on the paid tier).
                run_ffmpeg(
                    ["-c:v", "libx264", "-crf", "18", "-preset", "medium", "-c:a", "aac"]
                )
            except subprocess.CalledProcessError:
                shutil.rmtree(workdir, ignore_errors=True)
                raise RuntimeError("Couldn't trim the video to that time range.")

    shutil.rmtree(workdir, ignore_errors=True)
    return f"{clip_id}.mp4"


def _cut_with_captions_and_crop(
    raw_path: str,
    out_path: str,
    workdir: str,
    relative_start: float,
    duration: float,
    clip_start: float,
    clip_end: float,
    lines: list,
    captions: bool,
    caption_style: str,
    vertical: bool,
) -> None:
    """Trims, crops to 9:16 (if requested), and burns in styled captions
    (if requested) in a single ffmpeg pass straight from the raw
    download — one re-encode total, not a trim pass followed by a second
    re-encode pass for the extras."""
    width, height = _probe_dimensions(raw_path)

    vf_parts = []
    out_width, out_height = width, height

    if vertical:
        target_width = round((height * 9 / 16) / 2) * 2  # even width, ffmpeg requirement
        even_source_width = width - (width % 2)
        out_width = min(target_width, even_source_width) if target_width > 0 else even_source_width
        crop_x = max(0, (width - out_width) // 2)
        vf_parts.append(f"crop={out_width}:{height}:{crop_x}:0")
        out_height = height

    if captions:
        chunks = chunk_captions_for_clip(lines, clip_start, clip_end)
        if chunks:
            ass_content = build_ass_subtitle(chunks, caption_style, out_width, out_height)
            ass_path = os.path.join(workdir, "captions.ass")
            with open(ass_path, "w", encoding="utf-8") as f:
                f.write(ass_content)
            # No colons/special chars possible in this path (it's under a
            # tempfile.mkdtemp() dir), so it's safe unescaped in the filter.
            vf_parts.append(f"subtitles={ass_path}")
        # else: no transcript overlap for this window (e.g. silent
        # section) — proceed without captions rather than failing the cut.

    cmd = ["ffmpeg", "-y", "-ss", str(relative_start), "-i", raw_path, "-t", str(duration)]
    if vf_parts:
        cmd += ["-vf", ",".join(vf_parts)]
    # Same CRF 18 / preset medium bump as the plain trim path — a bit
    # more encode time in exchange for noticeably less compression
    # softness, worthwhile given clips are short.
    cmd += ["-c:v", "libx264", "-crf", "18", "-preset", "medium", "-c:a", "aac", out_path]
    subprocess.run(cmd, check=True, capture_output=True, timeout=240)


@app.route("/api/auth", methods=["POST"])
def auth():
    """Single endpoint for both signup and login, to keep the frontend to
    one form/button: if the email exists, this logs in (checking the
    password); if it doesn't, this creates the account. Keeps the UI to
    one input pair instead of separate signup/login screens."""
    data = request.get_json(silent=True) or {}
    email = (data.get("email") or "").strip().lower()
    password = data.get("password") or ""

    if not email or "@" not in email:
        return jsonify({"error": "Enter a valid email."}), 400
    if len(password) < 6:
        return jsonify({"error": "Password needs to be at least 6 characters."}), 400

    user = User.query.filter_by(email=email).first()
    if user:
        if not user.check_password(password):
            return jsonify({"error": "Wrong password for that email."}), 401
    else:
        user = User(email=email)
        user.set_password(password)
        db.session.add(user)
        db.session.commit()

    login_user(user, remember=True)
    return jsonify(_user_status(user))


@app.route("/api/logout", methods=["POST"])
@json_login_required
def logout():
    logout_user()
    return jsonify({"ok": True})


@app.route("/api/me", methods=["GET"])
def me():
    if not current_user.is_authenticated:
        return jsonify({"logged_in": False})
    return jsonify(_user_status(current_user))


def _user_status(user):
    return {
        "logged_in": True,
        "email": user.email,
        "is_paid": user.is_paid,
        "remaining_today": user.remaining_today("analyze"),
        "remaining_cuts_today": user.remaining_today("cut"),
        "free_daily_limit": FREE_DAILY_LIMIT,
        "max_clip_seconds": user.max_clip_seconds(),
    }


@app.route("/api/create-checkout-session", methods=["POST"])
@json_login_required
def create_checkout_session():
    if not stripe.api_key or not STRIPE_PRICE_ID:
        return jsonify({"error": "Billing isn't configured yet on this server."}), 500

    base_url = request.host_url.rstrip("/")
    try:
        session = stripe.checkout.Session.create(
            mode="subscription",
            payment_method_types=["card"],
            line_items=[{"price": STRIPE_PRICE_ID, "quantity": 1}],
            customer_email=current_user.email,
            client_reference_id=str(current_user.id),
            success_url=f"{base_url}/app?checkout=success",
            cancel_url=f"{base_url}/app?checkout=cancelled",
        )
    except Exception as e:
        return jsonify({"error": f"Couldn't start checkout: {e}"}), 502

    return jsonify({"checkout_url": session.url})


@app.route("/webhook/stripe", methods=["POST"])
def stripe_webhook():
    payload = request.data
    sig_header = request.headers.get("Stripe-Signature", "")

    try:
        if STRIPE_WEBHOOK_SECRET:
            event = stripe.Webhook.construct_event(payload, sig_header, STRIPE_WEBHOOK_SECRET)
        else:
            # No webhook secret configured (e.g. still testing) — trust the payload as-is.
            import json as _json
            event = _json.loads(payload)
    except Exception as e:
        return jsonify({"error": f"Invalid webhook payload: {e}"}), 400

    event_type = event["type"] if isinstance(event, dict) else event.type
    data_object = event["data"]["object"] if isinstance(event, dict) else event.data.object

    # Not using data_object.get(...) here on purpose: newer stripe-python
    # versions' StripeObject no longer behaves like a real dict for
    # attribute access — calling .get(...) on it raises AttributeError
    # ("get" gets treated as a missing key, not a method call), which was
    # crashing this whole route with a 500 (confirmed live via Stripe's
    # webhook delivery log + Render's traceback). Plain dicts (the
    # no-secret JSON fallback path above) still support .get() fine, so
    # this helper handles both shapes safely.
    def field(obj, key, default=None):
        if isinstance(obj, dict):
            return obj.get(key, default)
        return getattr(obj, key, default)

    if event_type == "checkout.session.completed":
        user_id = field(data_object, "client_reference_id")
        customer_id = field(data_object, "customer")
        subscription_id = field(data_object, "subscription")
        if user_id:
            user = db.session.get(User, int(user_id))
            if user:
                user.is_paid = True
                user.stripe_customer_id = customer_id
                user.stripe_subscription_id = subscription_id
                db.session.commit()

    elif event_type in ("customer.subscription.deleted", "customer.subscription.updated"):
        status = field(data_object, "status")
        customer_id = field(data_object, "customer")
        user = User.query.filter_by(stripe_customer_id=customer_id).first()
        if user:
            user.is_paid = status in ("active", "trialing")
            db.session.commit()

    return jsonify({"received": True})


@app.route("/api/analyze", methods=["POST"])
@json_login_required
def analyze():
    data = request.get_json(silent=True) or {}
    url = (data.get("youtube_url") or "").strip()
    top = int(data.get("top", 6))

    if not url:
        return jsonify({"error": "Paste a YouTube URL first."}), 400

    if not current_user.can_analyze():
        return jsonify(
            {
                "error": f"You've used all {FREE_DAILY_LIMIT} free clips today. Upgrade for unlimited.",
                "limit_reached": True,
            }
        ), 402

    try:
        lines = fetch_youtube_transcript(url)
    except Exception as e:
        msg = str(e)
        if "Subtitles are disabled" in msg or "NoTranscriptFound" in msg:
            friendly = "This video doesn't have captions available, so there's no transcript to score."
        elif "RequestBlocked" in msg or "IpBlocked" in msg or "blocking requests from your IP" in msg:
            friendly = (
                "YouTube is blocking this server's IP (common on cloud hosts like Render). "
                "This needs a residential proxy configured — see WEBSHARE_PROXY_USERNAME/"
                "WEBSHARE_PROXY_PASSWORD in the deploy notes. Not fixable by retrying."
            )
        elif "ProxyError" in msg or "Max retries" in msg:
            friendly = "Couldn't reach YouTube from this server right now. Try again in a moment."
        else:
            friendly = f"Couldn't fetch that video's transcript ({msg})."
        return jsonify({"error": friendly}), 502

    if not lines:
        return jsonify({"error": "Got an empty transcript for that video."}), 502

    scoring_method = "heuristic"
    llm_error = None
    try:
        clips = score_with_llm(lines, top_n=top)
        scoring_method = "llm"
    except Exception as e:
        # No API key set, Claude API error, bad JSON back, rate limited, etc.
        # Never let an LLM hiccup take down the whole feature — fall back
        # to the free heuristic scorer so /api/analyze still returns
        # something useful.
        #
        # Using print() (not app.logger) here on purpose — Flask's logger
        # doesn't reliably surface through gunicorn's output stream without
        # extra config, but stdout always does. traceback included so the
        # real cause (bad key, rate limit, JSON parse failure, etc.) shows
        # up in Render's logs instead of a bare exception message.
        import traceback
        llm_error = f"{type(e).__name__}: {e}"
        print(f"[LLM_SCORING_FAILED] {llm_error}", flush=True)
        traceback.print_exc()
        scored_lines = score_transcript(lines)
        clips = build_clips(scored_lines, top_n=top)

    current_user.record_usage()
    clips_json = clips_to_json(clips)
    # Timeline view needs the full video length to position clip segments
    # proportionally along a bar. There's no dedicated metadata fetch for
    # this (that'd mean another network round-trip against YouTube) — the
    # transcript's last line already gives a good-enough estimate, widened
    # to cover any clip that runs past it so no segment ever renders off
    # the end of the bar.
    last_line = lines[-1]
    transcript_end = last_line.end if last_line.end is not None else last_line.timestamp
    video_duration = max([transcript_end] + [c["end_seconds"] for c in clips_json])
    response = {
        "clips": clips_json,
        "scoring_method": scoring_method,
        "source": "youtube",
        "remaining_today": current_user.remaining_today(),
        "video_duration": round(video_duration, 2),
    }
    # TEMPORARY debug aid: surfaces the real LLM failure reason directly in
    # the response so it's visible without digging through Render logs.
    # Remove this before real users are hitting the app — you don't want
    # to expose internal error details/tracebacks to end users long-term.
    if llm_error:
        response["llm_debug"] = llm_error
    return jsonify(response)


@app.route("/api/focus", methods=["POST"])
@json_login_required
def focus():
    """AI Focus Mode: search one video's transcript for every moment
    matching a natural-language query, instead of /api/analyze's job of
    picking the best clips overall. Counts against the same daily
    analyze limit as /api/analyze — it's the same underlying LLM cost,
    just aimed at a specific question instead of a general pass."""
    data = request.get_json(silent=True) or {}
    url = (data.get("youtube_url") or "").strip()
    query = (data.get("query") or "").strip()

    if not url:
        return jsonify({"error": "Paste a YouTube URL first."}), 400
    if not query:
        return jsonify({"error": "Tell it what to search for, or pick a preset."}), 400

    if not current_user.can_analyze():
        return jsonify(
            {
                "error": f"You've used all {FREE_DAILY_LIMIT} free searches today. Upgrade for unlimited.",
                "limit_reached": True,
            }
        ), 402

    try:
        lines = fetch_youtube_transcript(url)
    except Exception as e:
        msg = str(e)
        if "Subtitles are disabled" in msg or "NoTranscriptFound" in msg:
            friendly = "This video doesn't have captions available, so there's no transcript to search."
        elif "RequestBlocked" in msg or "IpBlocked" in msg or "blocking requests from your IP" in msg:
            friendly = (
                "YouTube is blocking this server's IP (common on cloud hosts like Render). "
                "This needs a residential proxy configured — see WEBSHARE_PROXY_USERNAME/"
                "WEBSHARE_PROXY_PASSWORD in the deploy notes. Not fixable by retrying."
            )
        elif "ProxyError" in msg or "Max retries" in msg:
            friendly = "Couldn't reach YouTube from this server right now. Try again in a moment."
        else:
            friendly = f"Couldn't fetch that video's transcript ({msg})."
        return jsonify({"error": friendly}), 502

    if not lines:
        return jsonify({"error": "Got an empty transcript for that video."}), 502

    # No heuristic fallback here (unlike /api/analyze) — there's no
    # keyword-matching equivalent to "find every moment matching an
    # arbitrary natural-language query," so an LLM failure is a real
    # error, not something to silently degrade past.
    try:
        moments = find_moments_with_llm(lines, query, max_results=12)
    except Exception as e:
        import traceback
        print(f"[FOCUS_MODE_FAILED] {type(e).__name__}: {e}", flush=True)
        traceback.print_exc()
        return jsonify({"error": "Couldn't run that search right now — try again in a moment."}), 502

    current_user.record_usage()
    response = {
        "clips": clips_to_json(moments),
        "query": query,
        "source": "youtube",
        "remaining_today": current_user.remaining_today(),
    }
    return jsonify(response)


@app.route("/api/cut", methods=["POST"])
@json_login_required
def cut():
    data = request.get_json(silent=True) or {}
    url = (data.get("youtube_url") or "").strip()
    start = data.get("start")
    end = data.get("end")
    want_captions = bool(data.get("captions"))
    caption_style = data.get("caption_style") or DEFAULT_STYLE
    want_vertical = bool(data.get("vertical"))

    if not url or start is None or end is None:
        return jsonify({"error": "Need youtube_url, start, and end."}), 400

    if not current_user.can_use("cut"):
        return jsonify(
            {
                "error": f"You've used all {FREE_DAILY_LIMIT} free clip downloads today. Upgrade for unlimited.",
                "limit_reached": True,
            }
        ), 402

    # Captions and vertical crop are paid-tier perks — free tier still
    # gets the core find-and-cut value, this is an upgrade incentive on
    # top, same as the longer clip length / higher resolution gating.
    if (want_captions or want_vertical) and not current_user.is_paid:
        return jsonify(
            {
                "error": "Styled captions and vertical crop are available on the paid plan.",
                "upgrade_required": True,
            }
        ), 402

    if caption_style not in STYLE_PRESETS:
        caption_style = DEFAULT_STYLE

    try:
        start_s = _to_seconds(start)
        end_s = _to_seconds(end)
    except Exception:
        return jsonify({"error": "Couldn't parse start/end time."}), 400

    lines = []
    if want_captions:
        try:
            # Raw (unmerged) fragments, not the sentence-merged transcript
            # /api/analyze uses — captions need YouTube's real per-fragment
            # timestamps to stay in sync, merging loses that granularity.
            lines = fetch_youtube_transcript_raw(url)
        except Exception as e:
            return jsonify(
                {"error": f"Couldn't fetch captions for this video ({e}). Try cutting without captions."}
            ), 502

    try:
        filename = cut_youtube_clip(
            url,
            start_s,
            end_s,
            max_seconds=current_user.max_clip_seconds(),
            max_height=current_user.max_height(),
            lines=lines,
            captions=want_captions,
            caption_style=caption_style,
            vertical=want_vertical,
        )
    except RuntimeError as e:
        return jsonify({"error": str(e)}), 502

    current_user.record_usage("cut")
    return jsonify(
        {
            "clip_url": f"/clips/{filename}",
            "remaining_cuts_today": current_user.remaining_today("cut"),
        }
    )


@app.route("/clips/<path:filename>")
def serve_clip(filename):
    return send_from_directory(CLIPS_DIR, filename, mimetype="video/mp4", as_attachment=False)


@app.route("/api/demo", methods=["GET"])
def demo():
    """Offline demo using the bundled sample transcript — works even with
    no internet access, so the UI is always demoable."""
    lines = load_transcript(DEMO_TRANSCRIPT_PATH)
    lines = score_transcript(lines)
    clips = build_clips(lines, top_n=5)
    clips_json = clips_to_json(clips)
    last_line = lines[-1]
    transcript_end = last_line.end if last_line.end is not None else last_line.timestamp
    video_duration = max([transcript_end] + [c["end_seconds"] for c in clips_json])
    return jsonify({
        "clips": clips_json,
        "source": "demo",
        "video_duration": round(video_duration, 2),
    })


DISCOVER_MAX_AGE = datetime.timedelta(hours=3)


def _get_or_refresh_feed(force: bool = False):
    """Shared by /api/discover and the digest cron job — both need the
    same cached-with-refresh-on-stale feed, so this is the one place
    that logic lives. Returns (feed_list, row_or_None). Raises only if
    a refresh was needed and failed AND there's no prior cache to fall
    back to."""
    row = DiscoverFeed.query.order_by(DiscoverFeed.computed_at.desc()).first()
    is_stale = row is None or (datetime.datetime.utcnow() - row.computed_at) > DISCOVER_MAX_AGE

    if is_stale or force:
        try:
            feed = build_discover_feed()
        except Exception as e:
            print(f"[DISCOVER_REFRESH_FAILED] {type(e).__name__}: {e}", flush=True)
            if row is None:
                raise
            # Fall back to serving the last good cached feed rather than erroring.
            feed = json.loads(row.feed_json)
        else:
            row = DiscoverFeed(feed_json=json.dumps(feed))
            db.session.add(row)
            db.session.commit()
    else:
        feed = json.loads(row.feed_json)

    return feed, row


@app.route("/api/discover", methods=["GET"])
@json_login_required
def discover():
    """Serves the cached Discover feed, refreshing it in-process if it's
    missing or stale. Deliberately NOT behind the daily analyze/cut
    limits — browsing the feed costs nothing extra per visitor since the
    expensive work (YouTube API calls + clip scoring) already happened
    once at refresh time, not per request.

    First visitor after the cache goes stale pays a slower request (the
    refresh runs synchronously, can take 15-30s); everyone after that
    gets the cached result instantly. Fine at this scale — worth moving
    to a proper background job later if traffic grows."""
    force = request.args.get("refresh") == "1"
    try:
        feed, row = _get_or_refresh_feed(force=force)
    except Exception as e:
        return jsonify({"error": f"Couldn't build the discover feed yet ({e})."}), 502

    return jsonify({"feed": feed, "computed_at": row.computed_at.isoformat() if row else None})


@app.route("/api/collections", methods=["GET"])
@json_login_required
def list_collections():
    """Returns every clip the current user has saved, grouped by
    collection name — {"Funny Moments": [clip, ...], "Rage Bait": [...]}.
    Grouping happens here rather than making the frontend do it since the
    Collections view and the Exports view both need this same data shaped
    slightly differently, and it's cheap to just hand back the grouping."""
    clips = (
        SavedClip.query.filter_by(user_id=current_user.id)
        .order_by(SavedClip.created_at.desc())
        .all()
    )
    grouped: Dict[str, list] = {}
    for c in clips:
        grouped.setdefault(c.collection_name, []).append(saved_clip_to_json(c))
    return jsonify({"collections": grouped})


@app.route("/api/collections/save", methods=["POST"])
@json_login_required
def save_clip():
    data = request.get_json(silent=True) or {}
    collection_name = (data.get("collection_name") or "").strip()[:120] or "Saved Clips"
    youtube_url = (data.get("youtube_url") or "").strip()
    start_seconds = data.get("start_seconds")
    end_seconds = data.get("end_seconds")

    if not youtube_url or start_seconds is None or end_seconds is None:
        return jsonify({"error": "Missing clip data — can't save this."}), 400

    try:
        start_seconds = float(start_seconds)
        end_seconds = float(end_seconds)
    except (TypeError, ValueError):
        return jsonify({"error": "Invalid clip timestamps."}), 400

    clip = SavedClip(
        user_id=current_user.id,
        collection_name=collection_name,
        youtube_url=youtube_url,
        start_seconds=start_seconds,
        end_seconds=end_seconds,
        hook=str(data.get("hook") or "").strip()[:300],
        reasoning=str(data.get("reasoning") or "").strip()[:500],
        score=float(data.get("score") or 0),
    )
    db.session.add(clip)
    db.session.commit()
    return jsonify({"saved": True, "clip": saved_clip_to_json(clip)})


@app.route("/api/collections/clip/<int:clip_id>", methods=["DELETE"])
@json_login_required
def delete_saved_clip(clip_id):
    clip = SavedClip.query.filter_by(id=clip_id, user_id=current_user.id).first()
    if not clip:
        return jsonify({"error": "That saved clip wasn't found."}), 404
    db.session.delete(clip)
    db.session.commit()
    return jsonify({"deleted": True})


@app.route("/api/collections/clip/<int:clip_id>/export-copy", methods=["POST"])
@json_login_required
def generate_clip_export_copy(clip_id):
    """Generates (and persists) a title/hashtags/description for one
    saved clip, for a given platform. Runs on-demand per clip rather than
    at save time — most saved clips are never actually exported, so
    there's no reason to spend an LLM call on every single save."""
    clip = SavedClip.query.filter_by(id=clip_id, user_id=current_user.id).first()
    if not clip:
        return jsonify({"error": "That saved clip wasn't found."}), 404

    data = request.get_json(silent=True) or {}
    platform = (data.get("platform") or "tiktok").strip().lower()

    try:
        copy = generate_export_copy(clip.hook, clip.reasoning, platform=platform)
    except Exception as e:
        import traceback
        print(f"[EXPORT_COPY_FAILED] {type(e).__name__}: {e}", flush=True)
        traceback.print_exc()
        return jsonify({"error": "Couldn't generate export copy right now — try again in a moment."}), 502

    clip.export_title = copy.get("title", "")
    clip.export_hashtags = json.dumps(copy.get("hashtags", []))
    clip.export_description = copy.get("description", "")
    db.session.commit()

    return jsonify({"clip": saved_clip_to_json(clip)})


@app.route("/api/cron/send-digest", methods=["GET", "POST"])
def cron_send_digest():
    """Triggered by an external scheduler (cron-job.org or similar) —
    Render's free/starter web service has no built-in cron, and this is
    the zero-added-cost way to get a scheduled trigger without standing
    up a separate service. Protected by a shared secret rather than
    login, since the caller here is a script, not a signed-in user."""
    if not CRON_SECRET:
        return jsonify({"error": "CRON_SECRET is not configured on this server."}), 500
    if request.args.get("secret") != CRON_SECRET:
        return jsonify({"error": "Forbidden."}), 403
    if not RESEND_API_KEY:
        return jsonify({"error": "RESEND_API_KEY is not configured on this server."}), 500

    try:
        feed, _ = _get_or_refresh_feed(force=False)
    except Exception as e:
        return jsonify({"error": f"Couldn't get a discover feed to send ({e})."}), 502

    users = User.query.filter_by(email_opt_in=True).all()
    base_url = request.host_url.rstrip("/")
    result = send_digest_emails(
        feed=feed,
        users=users,
        app_url=base_url,
        secret_key=app.secret_key,
        api_key=RESEND_API_KEY,
        from_email=DIGEST_FROM_EMAIL,
    )
    return jsonify(result)


@app.route("/unsubscribe", methods=["GET"])
def unsubscribe():
    token = request.args.get("token", "")
    user_id = verify_unsubscribe_token(token, app.secret_key)
    if user_id is None:
        message = "That unsubscribe link is invalid or expired."
    else:
        user = User.query.get(user_id)
        if user is not None:
            user.email_opt_in = False
            db.session.commit()
        message = "You're unsubscribed from the ClipFind digest. You can still use the app any time."

    return f"""
    <html><body style="background:#0a0a0f;color:#f2f2f5;font-family:-apple-system,sans-serif;
      display:flex;align-items:center;justify-content:center;height:100vh;margin:0;">
      <div style="text-align:center;max-width:420px;padding:24px;">
        <p style="font-size:16px;line-height:1.5;">{message}</p>
      </div>
    </body></html>
    """


@app.route("/")
def landing():
    return render_template("landing.html")


@app.route("/app")
def index():
    return render_template("index.html")



if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)
