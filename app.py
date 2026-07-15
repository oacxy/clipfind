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

from flask import Flask, request, jsonify, render_template_string

from clipfind import (
    fetch_youtube_transcript,
    load_transcript,
    score_transcript,
    build_clips,
    fmt_timestamp,
)

app = Flask(__name__)

DEMO_TRANSCRIPT_PATH = "sample_transcript.txt"


def clips_to_json(clips):
    out = []
    for c in clips:
        preview = " ".join(l.text for l in c.lines)
        if len(preview) > 240:
            preview = preview[:240] + "..."
        out.append(
            {
                "start": fmt_timestamp(max(c.start, 0)),
                "end": fmt_timestamp(c.end),
                "score": c.score,
                "hook": c.hook,
                "caption": c.hook.strip().rstrip("."),
                "preview": preview,
            }
        )
    return out


@app.route("/api/analyze", methods=["POST"])
def analyze():
    data = request.get_json(silent=True) or {}
    url = (data.get("youtube_url") or "").strip()
    top = int(data.get("top", 6))

    if not url:
        return jsonify({"error": "Paste a YouTube URL first."}), 400

    try:
        lines = fetch_youtube_transcript(url)
    except Exception as e:
        msg = str(e)
        if "Subtitles are disabled" in msg or "NoTranscriptFound" in msg:
            friendly = "This video doesn't have captions available, so there's no transcript to score."
        elif "ProxyError" in msg or "Max retries" in msg:
            friendly = "Couldn't reach YouTube from this server right now. Try again in a moment."
        else:
            friendly = f"Couldn't fetch that video's transcript ({msg})."
        return jsonify({"error": friendly}), 502

    if not lines:
        return jsonify({"error": "Got an empty transcript for that video."}), 502

    lines = score_transcript(lines)
    clips = build_clips(lines, top_n=top)
    return jsonify({"clips": clips_to_json(clips), "source": "youtube"})


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
  .clip .preview{color:var(--text-dim);font-size:0.85rem;}
  .footer-note{margin-top:28px;text-align:center;color:var(--text-dim);font-size:0.8rem;}
</style>
</head>
<body>
<div class="wrap">
  <div class="logo">Clip<span>Find</span></div>
  <div class="tag">Paste a YouTube link. Get the moments worth clipping.</div>
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

function renderClips(clips) {
  resultsEl.innerHTML = '';
  clips.forEach((c, i) => {
    const div = document.createElement('div');
    div.className = 'clip';
    div.innerHTML = `
      <div class="meta"><span>${c.start} – ${c.end}</span><span class="score">score ${c.score}</span></div>
      <div class="hook">"${c.hook}"</div>
      <div class="preview">${c.preview}</div>
    `;
    resultsEl.appendChild(div);
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
      statusEl.textContent = data.error || 'Something went wrong.';
      return;
    }
    statusEl.textContent = `${data.clips.length} clips found${data.source === 'demo' ? ' (demo transcript)' : ''}`;
    renderClips(data.clips);
  } catch (e) {
    statusEl.className = 'status error';
    statusEl.textContent = 'Network error — is the server running?';
  } finally {
    analyzeBtn.disabled = false; demoBtn.disabled = false;
  }
}

analyzeBtn.addEventListener('click', () => {
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
