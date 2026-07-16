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
import uuid
import shutil
import functools
import subprocess
import tempfile

import stripe
from flask import Flask, request, jsonify, render_template_string, send_from_directory
from flask_login import (
    LoginManager,
    login_user,
    logout_user,
    login_required,
    current_user,
)

from llm_scorer import score_with_llm

from clipfind import (
    fetch_youtube_transcript,
    load_transcript,
    score_transcript,
    build_clips,
    fmt_timestamp,
    parse_timestamp,
)
from models import db, User, FREE_DAILY_LIMIT, PAID_MAX_CLIP_SECONDS

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

with app.app_context():
    db.create_all()

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
            }
        )
    return out


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


def cut_youtube_clip(
    youtube_url: str,
    start_seconds: float,
    end_seconds: float,
    max_seconds: int = 90,
    max_height: int = 480,
) -> str:
    """Download just the needed section of a YouTube video with yt-dlp,
    trim it precisely with ffmpeg, and return the filename (inside
    CLIPS_DIR) of the resulting mp4. Raises RuntimeError with a message
    safe to show the user on failure.

    max_seconds/max_height are tier-gated by the caller (free vs paid) —
    free users get shorter, lower-res clips since actual video download
    is the real Webshare-bandwidth cost, unlike text-only transcript
    fetches."""
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
        "format": f"mp4[height<={max_height}]/mp4/best",
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

    def run_ffmpeg(extra_args):
        cmd = ["ffmpeg", "-y", "-ss", str(relative_start), "-i", raw_path, "-t", str(duration)]
        cmd += extra_args + [out_path]
        subprocess.run(cmd, check=True, capture_output=True, timeout=90)

    try:
        # Fast path: stream copy (no re-encode). Fails if the cut point
        # doesn't land near a keyframe, in which case we fall back below.
        run_ffmpeg(["-c", "copy"])
    except subprocess.CalledProcessError:
        try:
            run_ffmpeg(["-c:v", "libx264", "-c:a", "aac", "-preset", "veryfast"])
        except subprocess.CalledProcessError as e:
            shutil.rmtree(workdir, ignore_errors=True)
            raise RuntimeError("Couldn't trim the video to that time range.")

    shutil.rmtree(workdir, ignore_errors=True)
    return f"{clip_id}.mp4"


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
            success_url=f"{base_url}/?checkout=success",
            cancel_url=f"{base_url}/?checkout=cancelled",
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

    if event_type == "checkout.session.completed":
        user_id = data_object.get("client_reference_id")
        customer_id = data_object.get("customer")
        subscription_id = data_object.get("subscription")
        if user_id:
            user = db.session.get(User, int(user_id))
            if user:
                user.is_paid = True
                user.stripe_customer_id = customer_id
                user.stripe_subscription_id = subscription_id
                db.session.commit()

    elif event_type in ("customer.subscription.deleted", "customer.subscription.updated"):
        status = data_object.get("status")
        customer_id = data_object.get("customer")
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
    response = {
        "clips": clips_to_json(clips),
        "scoring_method": scoring_method,
        "source": "youtube",
        "remaining_today": current_user.remaining_today(),
    }
    # TEMPORARY debug aid: surfaces the real LLM failure reason directly in
    # the response so it's visible without digging through Render logs.
    # Remove this before real users are hitting the app — you don't want
    # to expose internal error details/tracebacks to end users long-term.
    if llm_error:
        response["llm_debug"] = llm_error
    return jsonify(response)


@app.route("/api/cut", methods=["POST"])
@json_login_required
def cut():
    data = request.get_json(silent=True) or {}
    url = (data.get("youtube_url") or "").strip()
    start = data.get("start")
    end = data.get("end")

    if not url or start is None or end is None:
        return jsonify({"error": "Need youtube_url, start, and end."}), 400

    if not current_user.can_use("cut"):
        return jsonify(
            {
                "error": f"You've used all {FREE_DAILY_LIMIT} free clip downloads today. Upgrade for unlimited.",
                "limit_reached": True,
            }
        ), 402

    try:
        start_s = _to_seconds(start)
        end_s = _to_seconds(end)
    except Exception:
        return jsonify({"error": "Couldn't parse start/end time."}), 400

    try:
        filename = cut_youtube_clip(
            url,
            start_s,
            end_s,
            max_seconds=current_user.max_clip_seconds(),
            max_height=current_user.max_height(),
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
    return jsonify({"clips": clips_to_json(clips), "source": "demo"})


INDEX_HTML = """
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>ClipFind — paste a video, get the clips</title>
<style>
  :root{
    --bg:#0a0a0f; --card:#16161f; --border:#26262f;
    --text:#f2f2f5; --text-dim:#9a9aa8;
    --accent:#7c5cff; --accent2:#ff5c9a; --green:#3ddc97; --red:#ff6b6b;
  }
  *{box-sizing:border-box;margin:0;padding:0;}
  body{background:var(--bg);color:var(--text);font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Inter,Roboto,sans-serif;}
  .wrap{max-width:760px;margin:0 auto;padding:64px 24px;}
  .logo{font-weight:800;font-size:1.4rem;text-align:center;margin-bottom:8px;}
  .logo span{color:var(--accent);}
  .tag{text-align:center;color:var(--text-dim);margin-bottom:36px;}
  .panel{background:var(--card);border:1px solid var(--border);border-radius:14px;padding:24px;}
  .row{display:flex;gap:10px;flex-wrap:wrap;}
  input[type=text]{
    flex:1;min-width:220px;padding:14px 16px;border-radius:8px;border:1px solid var(--border);
    background:#0d0d13;color:var(--text);font-size:0.95rem;
  }
  input[type=text]:focus{outline:none;border-color:var(--accent);}
  button{
    padding:14px 22px;border-radius:8px;font-weight:600;font-size:0.95rem;border:none;cursor:pointer;
    background:linear-gradient(135deg,var(--accent),var(--accent2));color:white;
  }
  button.secondary{background:transparent;border:1px solid var(--border);color:var(--text);}
  button:disabled{opacity:0.5;cursor:not-allowed;}
  .status{margin-top:14px;font-size:0.9rem;color:var(--text-dim);min-height:1.2em;}
  .status.error{color:var(--red);}
  .results{margin-top:28px;display:flex;flex-direction:column;gap:14px;}
  .clip{background:#0d0d13;border:1px solid var(--border);border-radius:12px;padding:18px;}
  .clip .meta{display:flex;justify-content:space-between;color:var(--text-dim);font-size:0.8rem;margin-bottom:8px;}
  .clip .score{color:var(--green);font-weight:700;}
  .clip .hook{font-weight:600;margin-bottom:6px;}
  .clip .reasoning{color:var(--accent);font-size:0.85rem;margin-bottom:8px;font-style:italic;}
  .clip .preview{color:var(--text-dim);font-size:0.85rem;}
  .clip .actions{margin-top:12px;display:flex;gap:10px;align-items:center;flex-wrap:wrap;}
  .clip .actions button{padding:8px 14px;font-size:0.82rem;}
  .clip .cut-status{font-size:0.8rem;color:var(--text-dim);}
  .clip .cut-status.error{color:var(--red);}
  .clip video{margin-top:12px;width:100%;border-radius:8px;display:block;}
  .clip .dl-link{font-size:0.82rem;color:var(--green);}
  .footer-note{margin-top:28px;text-align:center;color:var(--text-dim);font-size:0.8rem;}
  .account-bar{display:flex;justify-content:space-between;align-items:center;background:var(--card);border:1px solid var(--border);border-radius:12px;padding:12px 18px;margin-bottom:16px;font-size:0.85rem;flex-wrap:wrap;gap:10px;}
  .account-bar .left{color:var(--text-dim);}
  .account-bar .left b{color:var(--text);}
  .account-bar .actions{display:flex;gap:8px;}
  .account-bar button{padding:8px 14px;font-size:0.82rem;}
  input[type=password]{
    flex:1;min-width:160px;padding:14px 16px;border-radius:8px;border:1px solid var(--border);
    background:#0d0d13;color:var(--text);font-size:0.95rem;
  }
  input[type=email]{
    flex:1;min-width:220px;padding:14px 16px;border-radius:8px;border:1px solid var(--border);
    background:#0d0d13;color:var(--text);font-size:0.95rem;
  }
  input[type=email]:focus, input[type=password]:focus{outline:none;border-color:var(--accent);}
  .auth-note{margin-top:10px;font-size:0.8rem;color:var(--text-dim);}
</style>
</head>
<body>
<div class="wrap">
  <div class="logo">Clip<span>Find</span></div>
  <div class="tag">Paste a YouTube link. Get the moments worth clipping.</div>

  <div class="panel" id="authPanel">
    <div class="row">
      <input type="email" id="authEmail" placeholder="you@email.com" />
      <input type="password" id="authPassword" placeholder="password (6+ characters)" />
      <button id="authBtn">Continue</button>
    </div>
    <div class="status" id="authStatus"></div>
    <div class="auth-note">New here? This creates your account. Already have one? This logs you in. 3 free clips a day, no card needed.</div>
  </div>

  <div class="account-bar" id="accountBar" style="display:none;">
    <div class="left" id="accountInfo"></div>
    <div class="actions">
      <button id="upgradeBtn" style="display:none;">Upgrade — unlimited</button>
      <button class="secondary" id="logoutBtn">Log out</button>
    </div>
  </div>

  <div class="panel">
    <div class="row">
      <input type="text" id="urlInput" placeholder="https://www.youtube.com/watch?v=..." />
      <button id="analyzeBtn">Find clips</button>
      <button id="demoBtn" class="secondary">Try demo</button>
    </div>
    <div class="status" id="status"></div>
    <div class="results" id="results"></div>
  </div>
  <div class="footer-note">Prototype — scoring is heuristic-based, not yet LLM-powered.</div>
</div>

<script>
const statusEl = document.getElementById('status');
const resultsEl = document.getElementById('results');
const analyzeBtn = document.getElementById('analyzeBtn');
const demoBtn = document.getElementById('demoBtn');
const urlInput = document.getElementById('urlInput');

const authPanel = document.getElementById('authPanel');
const authEmail = document.getElementById('authEmail');
const authPassword = document.getElementById('authPassword');
const authBtn = document.getElementById('authBtn');
const authStatus = document.getElementById('authStatus');
const accountBar = document.getElementById('accountBar');
const accountInfo = document.getElementById('accountInfo');
const upgradeBtn = document.getElementById('upgradeBtn');
const logoutBtn = document.getElementById('logoutBtn');

let lastYoutubeUrl = null; // set when the results came from a real video, not the demo
let session = { logged_in: false };

function renderAccountUI() {
  if (session.logged_in) {
    authPanel.style.display = 'none';
    accountBar.style.display = 'flex';
    const remainingText = session.is_paid
      ? 'Unlimited clips'
      : `${session.remaining_today} analyses + ${session.remaining_cuts_today} downloads left today`;
    accountInfo.innerHTML = `Signed in as <b>${session.email}</b> · ${remainingText}`;
    upgradeBtn.style.display = session.is_paid ? 'none' : 'inline-block';
  } else {
    authPanel.style.display = 'block';
    accountBar.style.display = 'none';
  }
}

async function refreshSession() {
  const res = await fetch('/api/me');
  session = await res.json();
  renderAccountUI();
}

authBtn.addEventListener('click', async () => {
  const email = authEmail.value.trim();
  const password = authPassword.value;
  authStatus.className = 'status';
  authStatus.textContent = 'Working...';
  authBtn.disabled = true;
  try {
    const res = await fetch('/api/auth', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ email, password }),
    });
    const data = await res.json();
    if (!res.ok) {
      authStatus.className = 'status error';
      authStatus.textContent = data.error || 'Could not sign in.';
      return;
    }
    session = data;
    authStatus.textContent = '';
    authPassword.value = '';
    renderAccountUI();
  } catch (e) {
    authStatus.className = 'status error';
    authStatus.textContent = 'Network error.';
  } finally {
    authBtn.disabled = false;
  }
});

logoutBtn.addEventListener('click', async () => {
  await fetch('/api/logout', { method: 'POST' });
  session = { logged_in: false };
  renderAccountUI();
});

upgradeBtn.addEventListener('click', async () => {
  upgradeBtn.disabled = true;
  upgradeBtn.textContent = 'Redirecting...';
  try {
    const res = await fetch('/api/create-checkout-session', { method: 'POST' });
    const data = await res.json();
    if (!res.ok) {
      alert(data.error || 'Could not start checkout.');
      upgradeBtn.disabled = false;
      upgradeBtn.textContent = 'Upgrade — unlimited';
      return;
    }
    window.location.href = data.checkout_url;
  } catch (e) {
    alert('Network error starting checkout.');
    upgradeBtn.disabled = false;
    upgradeBtn.textContent = 'Upgrade — unlimited';
  }
});

if (new URLSearchParams(window.location.search).get('checkout') === 'success') {
  statusEl.textContent = "Payment received — you're upgraded! (may take a few seconds to reflect below)";
}

refreshSession();

async function cutClip(youtubeUrl, start, end, statusNode, videoWrap) {
  statusNode.className = 'cut-status';
  statusNode.textContent = 'Cutting the clip from the video (this can take a bit)...';
  try {
    const res = await fetch('/api/cut', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ youtube_url: youtubeUrl, start, end }),
    });
    const data = await res.json();
    if (!res.ok) {
      statusNode.className = 'cut-status error';
      if (data.auth_required) {
        statusNode.textContent = 'Sign in above first.';
      } else if (data.limit_reached) {
        statusNode.textContent = data.error;
        upgradeBtn.scrollIntoView({ behavior: 'smooth' });
      } else {
        statusNode.textContent = data.error || 'Could not cut that clip.';
      }
      return;
    }
    statusNode.textContent = '';
    videoWrap.innerHTML = `
      <video controls src="${data.clip_url}"></video>
      <a class="dl-link" href="${data.clip_url}" download>Download mp4</a>
    `;
    if (typeof data.remaining_cuts_today !== 'undefined') {
      session.remaining_cuts_today = data.remaining_cuts_today;
      renderAccountUI();
    }
  } catch (e) {
    statusNode.className = 'cut-status error';
    statusNode.textContent = 'Network error while cutting.';
  }
}

function renderClips(clips, isYoutube) {
  resultsEl.innerHTML = '';
  clips.forEach((c) => {
    const div = document.createElement('div');
    div.className = 'clip';
    div.innerHTML = `
      <div class="meta"><span>${c.start} – ${c.end}</span><span class="score">score ${c.score}</span></div>
      <div class="hook">"${c.hook}"</div>
      ${c.reasoning ? `<div class="reasoning">🧠 ${c.reasoning}</div>` : ''}
      <div class="preview">${c.preview}</div>
      <div class="actions"></div>
      <div class="cut-status"></div>
      <div class="video-wrap"></div>
    `;
    resultsEl.appendChild(div);

    const actions = div.querySelector('.actions');
    const cutStatus = div.querySelector('.cut-status');
    const videoWrap = div.querySelector('.video-wrap');

    if (isYoutube) {
      const cutBtn = document.createElement('button');
      cutBtn.className = 'secondary';
      cutBtn.textContent = 'Cut & download this clip';
      cutBtn.addEventListener('click', () => {
        cutBtn.disabled = true;
        cutClip(lastYoutubeUrl, c.start_seconds, c.end_seconds, cutStatus, videoWrap)
          .finally(() => { cutBtn.disabled = false; });
      });
      actions.appendChild(cutBtn);
    } else {
      cutStatus.textContent = 'Cutting only works on real videos, not the demo transcript.';
    }
  });
}

async function run(endpoint, body) {
  statusEl.className = 'status';
  statusEl.textContent = 'Analyzing...';
  resultsEl.innerHTML = '';
  analyzeBtn.disabled = true; demoBtn.disabled = true;
  try {
    const res = await fetch(endpoint, {
      method: body ? 'POST' : 'GET',
      headers: body ? { 'Content-Type': 'application/json' } : undefined,
      body: body ? JSON.stringify(body) : undefined,
    });
    const data = await res.json();
    if (!res.ok) {
      statusEl.className = 'status error';
      if (data.auth_required) {
        statusEl.textContent = 'Sign in above first (3 free clips a day, no card needed).';
        authPanel.scrollIntoView({ behavior: 'smooth' });
      } else if (data.limit_reached) {
        statusEl.textContent = data.error;
        upgradeBtn.scrollIntoView({ behavior: 'smooth' });
      } else {
        statusEl.textContent = data.error || 'Something went wrong.';
      }
      return;
    }
    const isYoutube = data.source === 'youtube';
    lastYoutubeUrl = isYoutube ? (body && body.youtube_url) : null;
    let methodNote = data.scoring_method === 'llm' ? ' — AI-analyzed' : (data.scoring_method === 'heuristic' && data.source === 'youtube' ? ' — basic scoring (AI analysis unavailable right now)' : '');
    if (data.llm_debug) { methodNote += ` [debug: ${data.llm_debug}]`; }
    statusEl.textContent = `${data.clips.length} clips found${data.source === 'demo' ? ' (demo transcript)' : ''}${methodNote}`;
    renderClips(data.clips, isYoutube);
    if (typeof data.remaining_today !== 'undefined') {
      session.remaining_today = data.remaining_today;
      renderAccountUI();
    }
  } catch (e) {
    statusEl.className = 'status error';
    statusEl.textContent = 'Network error — is the server running?';
  } finally {
    analyzeBtn.disabled = false; demoBtn.disabled = false;
  }
}

analyzeBtn.addEventListener('click', () => {
  if (!session.logged_in) {
    statusEl.className = 'status error';
    statusEl.textContent = 'Sign in above first (3 free clips a day, no card needed).';
    authPanel.scrollIntoView({ behavior: 'smooth' });
    return;
  }
  const url = urlInput.value.trim();
  if (!url) { statusEl.className = 'status error'; statusEl.textContent = 'Paste a YouTube URL first.'; return; }
  run('/api/analyze', { youtube_url: url, top: 6 });
});

demoBtn.addEventListener('click', () => run('/api/demo'));
</script>
</body>
</html>
"""


@app.route("/")
def index():
    return render_template_string(INDEX_HTML)


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)
