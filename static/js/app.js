// ClipFind dashboard shell + core logic.
// Migrated from the old single-page embedded script — same API contract
// with the Flask backend (/api/auth, /api/analyze, /api/cut, /api/discover,
// /api/me, /api/create-checkout-session), just wired into the new
// sidebar/topbar/view-switching dashboard shell instead of one scrolling
// page.

const authScreen = document.getElementById('authScreen');
const authEmail = document.getElementById('authEmail');
const authPassword = document.getElementById('authPassword');
const authBtn = document.getElementById('authBtn');
const authStatus = document.getElementById('authStatus');

const appShell = document.getElementById('appShell');
const accountInfo = document.getElementById('accountInfo');
const topbarTitle = document.getElementById('topbarTitle');

const planName = document.getElementById('planName');
const planUsage = document.getElementById('planUsage');
const planBarFill = document.getElementById('planBarFill');
const sidebarUpgradeBtn = document.getElementById('sidebarUpgradeBtn');
const settingsUpgradeBtn = document.getElementById('settingsUpgradeBtn');
const settingsInfo = document.getElementById('settingsInfo');
const logoutBtn = document.getElementById('logoutBtn');

const statusEl = document.getElementById('status');
const resultsEl = document.getElementById('results');
const analyzeBtn = document.getElementById('analyzeBtn');
const demoBtn = document.getElementById('demoBtn');
const urlInput = document.getElementById('urlInput');

const discoverStatus = document.getElementById('discoverStatus');
const discoverResults = document.getElementById('discoverResults');
const refreshDiscoverBtn = document.getElementById('refreshDiscoverBtn');
const discoverCategoryChips = document.getElementById('discoverCategoryChips');

const timelineStatus = document.getElementById('timelineStatus');
const timelineWrap = document.getElementById('timelineWrap');
const timelineTrack = document.getElementById('timelineTrack');
const timelineRuler = document.getElementById('timelineRuler');

const focusUrlInput = document.getElementById('focusUrlInput');
const focusQueryInput = document.getElementById('focusQueryInput');
const focusBtn = document.getElementById('focusBtn');
const focusStatus = document.getElementById('focusStatus');
const focusResults = document.getElementById('focusResults');
const focusPresets = document.getElementById('focusPresets');

let session = { logged_in: false };
let lastYoutubeUrl = null; // set when the results came from a real video, not the demo
let discoverLoaded = false;
let lastDiscoverFeed = []; // full unfiltered feed from the last fetch — category chips filter this client-side, no refetch
let lastDiscoverComputedAt = null;
let activeDiscoverCategory = 'all';
let lastAnalyzeData = null; // { clips, video_duration, isYoutube } from the most recent /api/analyze or /api/demo — feeds the Timeline view

// ---------------------------------------------------------------------
// View switching (sidebar nav)
// ---------------------------------------------------------------------
const VIEW_TITLES = {
  dashboard: 'Dashboard',
  projects: 'Projects',
  focusmode: 'AI Focus Mode',
  timeline: 'Timeline',
  discover: 'Discover',
  collections: 'Collections',
  exports: 'Exports',
  templates: 'Templates',
  analytics: 'Analytics',
  team: 'Team',
  settings: 'Settings',
};

function switchView(view) {
  document.querySelectorAll('.nav-item').forEach((btn) => {
    btn.classList.toggle('active', btn.dataset.view === view);
  });
  document.querySelectorAll('.view').forEach((el) => {
    el.classList.toggle('active', el.id === `view-${view}`);
  });
  topbarTitle.textContent = VIEW_TITLES[view] || 'ClipFind';
  if (view === 'discover' && !discoverLoaded) {
    loadDiscover(false);
  }
  if (view === 'settings') {
    renderSettings();
  }
  if (view === 'focusmode' && !focusUrlInput.value && lastYoutubeUrl) {
    focusUrlInput.value = lastYoutubeUrl;
  }
}

document.querySelectorAll('.nav-item').forEach((btn) => {
  btn.addEventListener('click', () => switchView(btn.dataset.view));
});

document.getElementById('newProjectBtn').addEventListener('click', () => {
  switchView('projects');
  urlInput.focus();
});

// ---------------------------------------------------------------------
// Session / account
// ---------------------------------------------------------------------
function renderAccountUI() {
  if (!session.logged_in) {
    authScreen.style.display = 'flex';
    appShell.style.display = 'none';
    return;
  }
  authScreen.style.display = 'none';
  appShell.style.display = 'grid';

  const isPaid = session.is_paid;
  accountInfo.innerHTML = `<b>${session.email}</b> · ${isPaid ? 'Unlimited' : `${session.remaining_today} analyses left today`}`;

  planName.textContent = isPaid ? 'Unlimited plan' : 'Free plan';
  if (isPaid) {
    planUsage.textContent = 'Unlimited clips';
    planBarFill.style.width = '100%';
  } else {
    const limit = session.free_daily_limit || 3;
    const used = Math.max(0, limit - (session.remaining_today ?? limit));
    planUsage.textContent = `${session.remaining_today ?? '—'} analyses + ${session.remaining_cuts_today ?? '—'} downloads left today`;
    planBarFill.style.width = `${Math.min(100, (used / limit) * 100)}%`;
  }
  sidebarUpgradeBtn.style.display = isPaid ? 'none' : 'block';

  renderSettings();
}

function renderSettings() {
  if (!session.logged_in) return;
  const isPaid = session.is_paid;
  settingsInfo.innerHTML = `Signed in as <b>${session.email}</b><br>Plan: <b>${isPaid ? 'Unlimited' : 'Free (3 clips/day)'}</b>`;
  settingsUpgradeBtn.style.display = isPaid ? 'none' : 'inline-block';
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

async function startCheckout(triggerBtn) {
  triggerBtn.disabled = true;
  const originalText = triggerBtn.textContent;
  triggerBtn.textContent = 'Redirecting...';
  try {
    const res = await fetch('/api/create-checkout-session', { method: 'POST' });
    const data = await res.json();
    if (!res.ok) {
      alert(data.error || 'Could not start checkout.');
      triggerBtn.disabled = false;
      triggerBtn.textContent = originalText;
      return;
    }
    window.location.href = data.checkout_url;
  } catch (e) {
    alert('Network error starting checkout.');
    triggerBtn.disabled = false;
    triggerBtn.textContent = originalText;
  }
}
sidebarUpgradeBtn.addEventListener('click', () => startCheckout(sidebarUpgradeBtn));
settingsUpgradeBtn.addEventListener('click', () => startCheckout(settingsUpgradeBtn));

// ---------------------------------------------------------------------
// Discover
// ---------------------------------------------------------------------
const DISCOVER_CATEGORIES = [
  { key: 'all', label: 'All' },
  { key: 'podcasts', label: 'Podcasts' },
  { key: 'business', label: 'Business' },
  { key: 'motivation', label: 'Motivation' },
  { key: 'startups', label: 'Startups' },
  { key: 'gaming', label: 'Gaming' },
  { key: 'comedy', label: 'Comedy' },
  { key: 'sports', label: 'Sports' },
  { key: 'education', label: 'Education' },
];

function renderDiscoverCategoryChips() {
  discoverCategoryChips.innerHTML = '';
  DISCOVER_CATEGORIES.forEach((cat) => {
    const chip = document.createElement('button');
    chip.type = 'button';
    chip.className = 'preset-chip' + (cat.key === activeDiscoverCategory ? ' active' : '');
    chip.textContent = cat.label;
    chip.addEventListener('click', () => {
      if (activeDiscoverCategory === cat.key) return;
      activeDiscoverCategory = cat.key;
      renderDiscoverCategoryChips();
      renderDiscover(lastDiscoverFeed);
    });
    discoverCategoryChips.appendChild(chip);
  });
}
renderDiscoverCategoryChips();

function renderDiscover(feed) {
  lastDiscoverFeed = feed;
  const activeLabel = (DISCOVER_CATEGORIES.find((c) => c.key === activeDiscoverCategory) || {}).label || 'All';
  const filtered = activeDiscoverCategory === 'all' ? feed : feed.filter((p) => p.category === activeDiscoverCategory);

  discoverResults.innerHTML = '';
  discoverStatus.className = 'status';

  if (!feed.length) {
    discoverStatus.textContent = 'No picks available right now — try refreshing in a bit.';
    return;
  }
  if (!filtered.length) {
    discoverStatus.textContent = `No picks in ${activeLabel} right now — try "All" or refresh in a bit.`;
    return;
  }

  const updatedNote = lastDiscoverComputedAt ? ` · updated ${new Date(lastDiscoverComputedAt).toLocaleString()}` : '';
  const categoryNote = activeDiscoverCategory !== 'all' ? ` in ${activeLabel}` : '';
  discoverStatus.textContent = `${filtered.length} pick${filtered.length === 1 ? '' : 's'}${categoryNote}${updatedNote}`;

  filtered.forEach((pick) => {
    const div = document.createElement('div');
    const clip = pick.clip || {};
    div.className = pick.thumbnail ? 'feed-slide' : 'feed-slide no-thumb';
    if (pick.thumbnail) {
      div.style.backgroundImage = `url('${pick.thumbnail}')`;
    }
    div.innerHTML = `
      <div class="feed-scrim"></div>
      <div class="feed-top-badges"><span class="velocity-pill">🔥 ${pick.velocity_score}x normal</span></div>
      <div class="feed-overlay">
        <div class="feed-channel">${pick.channel_title}</div>
        <div class="feed-title">${pick.title}</div>
        <div class="feed-clip">🧠 <b>${clip.hook || 'Clip found'}</b> — ${clip.reasoning || ''}</div>
        <button class="secondary open-btn">Analyze this video</button>
      </div>
    `;
    discoverResults.appendChild(div);
    div.querySelector('.open-btn').addEventListener('click', () => {
      urlInput.value = `https://www.youtube.com/watch?v=${pick.video_id}`;
      switchView('projects');
      run('/api/analyze', { youtube_url: urlInput.value, top: 6 });
    });
  });
}

async function loadDiscover(forceRefresh) {
  discoverStatus.className = 'status';
  discoverStatus.textContent = 'Loading discover feed...';
  discoverResults.innerHTML = '';
  try {
    const res = await fetch(`/api/discover${forceRefresh ? '?refresh=1' : ''}`);
    const data = await res.json();
    if (!res.ok) {
      discoverStatus.className = 'status error';
      if (data.auth_required) {
        discoverStatus.textContent = 'Sign in first.';
      } else {
        discoverStatus.textContent = data.error || 'Could not load the discover feed.';
      }
      return;
    }
    discoverLoaded = true;
    lastDiscoverComputedAt = data.computed_at || null;
    renderDiscover(data.feed);
  } catch (e) {
    discoverStatus.className = 'status error';
    discoverStatus.textContent = 'Network error loading discover feed.';
  }
}
refreshDiscoverBtn.addEventListener('click', () => loadDiscover(true));

// ---------------------------------------------------------------------
// Timeline
// ---------------------------------------------------------------------
function formatSeconds(totalSeconds) {
  const s = Math.max(0, Math.round(totalSeconds));
  const m = Math.floor(s / 60);
  const sec = s % 60;
  return `${m}:${String(sec).padStart(2, '0')}`;
}

function scoreTier(score) {
  if (score >= 80) return 'tier-high';
  if (score >= 60) return 'tier-mid';
  return 'tier-low';
}

function jumpToClip(index) {
  switchView('projects');
  const cards = resultsEl.querySelectorAll('.clip');
  const target = cards[index];
  if (target) {
    target.scrollIntoView({ behavior: 'smooth', block: 'center' });
    target.classList.remove('flash');
    // force reflow so the animation restarts if the same clip is clicked twice in a row
    void target.offsetWidth;
    target.classList.add('flash');
  }
}

function renderTimeline() {
  timelineTrack.innerHTML = '';
  timelineRuler.innerHTML = '';

  if (!lastAnalyzeData || !lastAnalyzeData.clips.length) {
    timelineWrap.style.display = 'none';
    timelineStatus.className = 'status';
    timelineStatus.textContent = 'Analyze a video under Projects first — Timeline maps out whatever was last analyzed.';
    return;
  }

  const { clips, video_duration, isYoutube } = lastAnalyzeData;
  const duration = Math.max(video_duration || 0, 1);

  timelineWrap.style.display = 'block';
  timelineStatus.className = 'status';
  timelineStatus.textContent = `${clips.length} moments across ${formatSeconds(duration)}${isYoutube ? '' : ' (demo transcript)'} — click a segment to jump to it.`;

  clips.forEach((c, i) => {
    const seg = document.createElement('div');
    seg.className = `timeline-seg ${scoreTier(c.score)}`;
    const leftPct = (c.start_seconds / duration) * 100;
    const widthPct = Math.max(((c.end_seconds - c.start_seconds) / duration) * 100, 0.6);
    seg.style.left = `${leftPct}%`;
    seg.style.width = `${widthPct}%`;
    seg.title = `${c.start} – ${c.end} · score ${c.score}\n"${c.hook}"`;
    seg.addEventListener('click', () => jumpToClip(i));
    timelineTrack.appendChild(seg);
  });

  const tickCount = 6;
  for (let i = 0; i <= tickCount; i++) {
    const tick = document.createElement('span');
    tick.className = 'timeline-tick';
    const pct = (i / tickCount) * 100;
    tick.style.left = `${pct}%`;
    if (i === 0) tick.style.transform = 'translateX(0)';
    if (i === tickCount) tick.style.transform = 'translateX(-100%)';
    tick.textContent = formatSeconds((duration / tickCount) * i);
    timelineRuler.appendChild(tick);
  }
}

// ---------------------------------------------------------------------
// AI Focus Mode
// ---------------------------------------------------------------------
focusPresets.querySelectorAll('.preset-chip').forEach((chip) => {
  chip.addEventListener('click', () => {
    focusQueryInput.value = chip.dataset.query;
    focusQueryInput.focus();
  });
});

async function runFocusSearch() {
  const url = focusUrlInput.value.trim();
  const query = focusQueryInput.value.trim();
  if (!url) {
    focusStatus.className = 'status error';
    focusStatus.textContent = 'Paste a YouTube URL first.';
    return;
  }
  if (!query) {
    focusStatus.className = 'status error';
    focusStatus.textContent = 'Type what to search for, or pick a preset below.';
    return;
  }

  focusStatus.className = 'status';
  focusStatus.textContent = 'Searching the video...';
  focusResults.innerHTML = '';
  focusBtn.disabled = true;
  try {
    const res = await fetch('/api/focus', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ youtube_url: url, query }),
    });
    const data = await res.json();
    if (!res.ok) {
      focusStatus.className = 'status error';
      if (data.auth_required) {
        focusStatus.textContent = 'Sign in first.';
      } else if (data.limit_reached) {
        focusStatus.textContent = data.error;
        switchView('settings');
      } else {
        focusStatus.textContent = data.error || 'Could not run that search.';
      }
      return;
    }
    focusStatus.textContent = data.clips.length
      ? `${data.clips.length} moment${data.clips.length === 1 ? '' : 's'} found matching "${data.query}".`
      : `No moments found matching "${data.query}" — try rephrasing, or the video just doesn't have that.`;
    renderClips(data.clips, true, focusResults, url);
    if (typeof data.remaining_today !== 'undefined') {
      session.remaining_today = data.remaining_today;
      renderAccountUI();
    }
  } catch (e) {
    focusStatus.className = 'status error';
    focusStatus.textContent = 'Network error while searching.';
  } finally {
    focusBtn.disabled = false;
  }
}
focusBtn.addEventListener('click', runFocusSearch);
focusQueryInput.addEventListener('keydown', (e) => {
  if (e.key === 'Enter') runFocusSearch();
});

// ---------------------------------------------------------------------
// Cutting clips (captions/vertical crop)
// ---------------------------------------------------------------------
async function cutClip(youtubeUrl, start, end, statusNode, videoWrap, extras) {
  statusNode.className = 'cut-status';
  const willStyle = extras && (extras.captions || extras.vertical);
  statusNode.textContent = willStyle
    ? 'Cutting and styling the clip (captions/crop take a bit longer)...'
    : 'Cutting the clip from the video (this can take a bit)...';
  try {
    const res = await fetch('/api/cut', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ youtube_url: youtubeUrl, start, end, ...(extras || {}) }),
    });
    const data = await res.json();
    if (!res.ok) {
      statusNode.className = 'cut-status error';
      if (data.auth_required) {
        statusNode.textContent = 'Sign in first.';
      } else if (data.limit_reached || data.upgrade_required) {
        statusNode.textContent = data.error;
        switchView('settings');
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

const CAPTION_STYLES = [
  { value: 'bold_impact', label: 'Bold Impact' },
  { value: 'karaoke_highlight', label: 'Karaoke Highlight' },
  { value: 'boxed', label: 'Boxed' },
];

const SUB_SCORE_LABELS = {
  hook: 'Hook',
  virality: 'Virality',
  entertainment: 'Entertainment',
  retention: 'Retention',
  emotional_impact: 'Emotional Impact',
  pacing: 'Pacing',
  originality: 'Originality',
};

function renderAnalystBreakdown(subScores, suggestions) {
  const hasSubScores = subScores && Object.keys(subScores).length > 0;
  const hasSuggestions = suggestions && suggestions.length > 0;
  if (!hasSubScores && !hasSuggestions) return '';

  const bars = hasSubScores
    ? Object.entries(subScores).map(([key, val]) => `
        <div class="score-row">
          <span class="score-label">${SUB_SCORE_LABELS[key] || key}</span>
          <div class="score-bar"><div class="score-bar-fill" style="width:${val}%;"></div></div>
          <span class="score-val">${val}</span>
        </div>`).join('')
    : '';

  const suggestionItems = hasSuggestions
    ? `<ul class="suggestion-list">${suggestions.map((s) => `<li>${s}</li>`).join('')}</ul>`
    : '';

  return `
    <details class="analyst-breakdown">
      <summary>View full analysis</summary>
      ${bars ? `<div class="score-rows">${bars}</div>` : ''}
      ${suggestionItems}
    </details>
  `;
}

function renderClips(clips, isYoutube, container = resultsEl, youtubeUrl = lastYoutubeUrl) {
  container.innerHTML = '';
  clips.forEach((c) => {
    const div = document.createElement('div');
    div.className = 'clip';
    div.innerHTML = `
      <div class="meta"><span>${c.start} – ${c.end}</span><span class="score">score ${c.score}</span></div>
      <div class="hook">"${c.hook}"</div>
      ${c.reasoning ? `<div class="reasoning">🧠 ${c.reasoning}</div>` : ''}
      <div class="preview">${c.preview}</div>
      ${renderAnalystBreakdown(c.sub_scores, c.suggestions)}
      <div class="style-controls"></div>
      <div class="actions"></div>
      <div class="cut-status"></div>
      <div class="video-wrap"></div>
    `;
    container.appendChild(div);

    const actions = div.querySelector('.actions');
    const cutStatus = div.querySelector('.cut-status');
    const videoWrap = div.querySelector('.video-wrap');
    const styleControls = div.querySelector('.style-controls');

    if (isYoutube) {
      const isPaid = session.is_paid;
      const styleSelect = document.createElement('select');
      CAPTION_STYLES.forEach((s) => {
        const opt = document.createElement('option');
        opt.value = s.value;
        opt.textContent = s.label;
        styleSelect.appendChild(opt);
      });
      const captionsLabel = document.createElement('label');
      const captionsCheck = document.createElement('input');
      captionsCheck.type = 'checkbox';
      captionsLabel.appendChild(captionsCheck);
      captionsLabel.append(' Captions');

      const verticalLabel = document.createElement('label');
      const verticalCheck = document.createElement('input');
      verticalCheck.type = 'checkbox';
      verticalLabel.appendChild(verticalCheck);
      verticalLabel.append(' Vertical (9:16)');

      styleControls.appendChild(captionsLabel);
      styleControls.appendChild(styleSelect);
      styleControls.appendChild(verticalLabel);

      if (!isPaid) {
        styleControls.classList.add('locked');
        captionsCheck.disabled = true;
        verticalCheck.disabled = true;
        styleSelect.disabled = true;
        const lockNote = document.createElement('span');
        lockNote.className = 'lock-note';
        lockNote.textContent = 'Upgrade to unlock styled captions & vertical crop';
        lockNote.addEventListener('click', () => switchView('settings'));
        styleControls.appendChild(lockNote);
      }

      const cutBtn = document.createElement('button');
      cutBtn.className = 'secondary';
      cutBtn.textContent = 'Cut & download this clip';
      cutBtn.addEventListener('click', () => {
        cutBtn.disabled = true;
        const extras = isPaid
          ? { captions: captionsCheck.checked, caption_style: styleSelect.value, vertical: verticalCheck.checked }
          : {};
        cutClip(youtubeUrl, c.start_seconds, c.end_seconds, cutStatus, videoWrap, extras)
          .finally(() => { cutBtn.disabled = false; });
      });
      actions.appendChild(cutBtn);
    } else {
      cutStatus.textContent = 'Cutting only works on real videos, not the demo transcript.';
    }
  });
}

// ---------------------------------------------------------------------
// Analyze / demo
// ---------------------------------------------------------------------
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
        statusEl.textContent = 'Sign in first (3 free clips a day, no card needed).';
      } else if (data.limit_reached) {
        statusEl.textContent = data.error;
        switchView('settings');
      } else {
        statusEl.textContent = data.error || 'Something went wrong.';
      }
      return;
    }
    const isYoutube = data.source === 'youtube';
    lastYoutubeUrl = isYoutube ? (body && body.youtube_url) : null;
    lastAnalyzeData = { clips: data.clips, video_duration: data.video_duration || 0, isYoutube };
    renderTimeline();
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
  const url = urlInput.value.trim();
  if (!url) { statusEl.className = 'status error'; statusEl.textContent = 'Paste a YouTube URL first.'; return; }
  run('/api/analyze', { youtube_url: url, top: 6 });
});

demoBtn.addEventListener('click', () => run('/api/demo'));

// ---------------------------------------------------------------------
// Boot
// ---------------------------------------------------------------------
if (new URLSearchParams(window.location.search).get('checkout') === 'success') {
  statusEl.textContent = "Payment received — you're upgraded! (may take a few seconds to reflect below)";
}

refreshSession().then(() => {
  // Digest emails link here with ?tab=discover so clicking "Open ClipFind"
  // lands people straight on the feed instead of the dashboard.
  if (new URLSearchParams(window.location.search).get('tab') === 'discover') {
    switchView('discover');
  }
});
