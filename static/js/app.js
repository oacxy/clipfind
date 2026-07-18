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

const projectsListView = document.getElementById('projectsListView');
const projectWorkspace = document.getElementById('projectWorkspace');
const projectList = document.getElementById('projectList');
const workspaceTitle = document.getElementById('workspaceTitle');
const backToProjectsBtn = document.getElementById('backToProjectsBtn');
const analystSummary = document.getElementById('analystSummary');
const videoSummary = document.getElementById('videoSummary');

const referralLinkInput = document.getElementById('referralLinkInput');
const copyReferralBtn = document.getElementById('copyReferralBtn');
const referralStatus = document.getElementById('referralStatus');

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

const collectionsStatus = document.getElementById('collectionsStatus');
const collectionsList = document.getElementById('collectionsList');
const refreshCollectionsBtn = document.getElementById('refreshCollectionsBtn');

const exportsStatus = document.getElementById('exportsStatus');
const exportsList = document.getElementById('exportsList');
const refreshExportsBtn = document.getElementById('refreshExportsBtn');

let session = { logged_in: false };
let lastYoutubeUrl = null; // set when the results came from a real video, not the demo
let discoverLoaded = false;
let lastDiscoverFeed = []; // full unfiltered feed from the last fetch — category chips filter this client-side, no refetch
let lastDiscoverComputedAt = null;
let activeDiscoverCategory = 'all';
let lastAnalyzeData = null; // { clips, video_duration, isYoutube } from the most recent /api/analyze or /api/demo — feeds the Timeline tab
let collectionsData = null; // { "Collection Name": [savedClip, ...], ... } from the last /api/collections fetch
let collectionsFetchedOnce = false;
let collectionNamesCache = []; // flat list of existing collection names, for the save-form autocomplete
let currentProject = null; // { id, youtube_url, clips, video_duration, scoring_method, isYoutube } — the project workspace currently open
let projectListCache = [];
let projectListFetchedOnce = false;
// Captured once at page load — someone arriving via clipfind.com/?ref=CODE
// (or /app?ref=CODE directly) should still get attributed even if they
// poke around before actually signing up.
let pendingReferralCode = new URLSearchParams(window.location.search).get('ref') || null;

// ---------------------------------------------------------------------
// View switching (sidebar nav)
// ---------------------------------------------------------------------
const VIEW_TITLES = {
  dashboard: 'Dashboard',
  projects: 'Projects',
  focusmode: 'AI Focus Mode',
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
  if (view === 'collections') {
    loadCollectionsView(false);
  }
  if (view === 'exports') {
    loadExportsView(false);
  }
  if (view === 'projects') {
    // Sidebar "Projects" always lands on the project list, even if a
    // specific project's workspace was open before navigating away —
    // reopening the same project takes one click from the list.
    showProjectsList();
    loadProjectList(false);
  }
}

document.querySelectorAll('.nav-item').forEach((btn) => {
  btn.addEventListener('click', () => switchView(btn.dataset.view));
});

document.getElementById('newProjectBtn').addEventListener('click', () => {
  switchView('projects');
  showProjectsList();
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
    const bonus = session.bonus_daily_clips || 0;
    const limit = (session.free_daily_limit || 3) + bonus;
    const used = Math.max(0, limit - (session.remaining_today ?? limit));
    const bonusNote = bonus > 0 ? ` (includes +${bonus} referral bonus)` : '';
    planUsage.textContent = `${session.remaining_today ?? '—'} analyses + ${session.remaining_cuts_today ?? '—'} downloads left today${bonusNote}`;
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

  if (session.referral_code) {
    referralLinkInput.value = `${window.location.origin}/?ref=${session.referral_code}`;
    const count = session.referral_count || 0;
    const bonus = session.bonus_daily_clips || 0;
    const maxBonus = session.max_referral_bonus || 15;
    referralStatus.className = 'status';
    if (!count) {
      referralStatus.textContent = 'No referrals yet — share your link to start earning bonus clips.';
    } else if (bonus >= maxBonus) {
      referralStatus.textContent = `${count} friend${count === 1 ? '' : 's'} joined through your link — you're at the max bonus of +${maxBonus} clips/day.`;
    } else {
      referralStatus.textContent = `${count} friend${count === 1 ? '' : 's'} joined through your link — +${bonus} bonus clip${bonus === 1 ? '' : 's'}/day.`;
    }
  }
}

copyReferralBtn.addEventListener('click', () => {
  if (!referralLinkInput.value) return;
  navigator.clipboard.writeText(referralLinkInput.value).then(() => {
    copyReferralBtn.textContent = 'Copied ✓';
    setTimeout(() => { copyReferralBtn.textContent = 'Copy link'; }, 1500);
  });
});

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
      // referral_code is only used server-side when this creates a brand
      // new account — harmless to always send it, an existing-user login
      // just ignores it.
      body: JSON.stringify({ email, password, referral_code: pendingReferralCode }),
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
      <div class="save-row" style="display:none;">
        <input type="text" class="save-name-input" list="collectionNamesList" placeholder="Collection name (e.g. Funny Moments)" />
        <button type="button" class="secondary save-confirm-btn">Save</button>
      </div>
      <div class="cut-status"></div>
      <div class="video-wrap"></div>
    `;
    container.appendChild(div);

    const actions = div.querySelector('.actions');
    const cutStatus = div.querySelector('.cut-status');
    const videoWrap = div.querySelector('.video-wrap');
    const styleControls = div.querySelector('.style-controls');
    const saveRow = div.querySelector('.save-row');
    const saveNameInput = div.querySelector('.save-name-input');
    const saveConfirmBtn = div.querySelector('.save-confirm-btn');

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

      const saveBtn = document.createElement('button');
      saveBtn.type = 'button';
      saveBtn.className = 'secondary';
      saveBtn.textContent = 'Save to collection';
      saveBtn.addEventListener('click', () => {
        const showing = saveRow.style.display !== 'none';
        saveRow.style.display = showing ? 'none' : 'flex';
        if (!showing) {
          ensureCollectionNamesLoaded();
          saveNameInput.focus();
        }
      });
      actions.appendChild(saveBtn);

      saveConfirmBtn.addEventListener('click', async () => {
        const name = saveNameInput.value.trim() || 'Saved Clips';
        saveConfirmBtn.disabled = true;
        try {
          const res = await fetch('/api/collections/save', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
              collection_name: name,
              youtube_url: youtubeUrl,
              start_seconds: c.start_seconds,
              end_seconds: c.end_seconds,
              hook: c.hook,
              reasoning: c.reasoning,
              score: c.score,
            }),
          });
          const data = await res.json();
          if (!res.ok) {
            alert(data.error || 'Could not save that clip.');
            return;
          }
          collectionsFetchedOnce = false; // stale — next Collections/Exports visit should refetch
          saveConfirmBtn.textContent = 'Saved ✓';
          setTimeout(() => {
            saveRow.style.display = 'none';
            saveConfirmBtn.textContent = 'Save';
            saveNameInput.value = '';
          }, 1200);
        } catch (e) {
          alert('Network error saving clip.');
        } finally {
          saveConfirmBtn.disabled = false;
        }
      });
    } else {
      cutStatus.textContent = 'Cutting only works on real videos, not the demo transcript.';
    }
  });
}

// ---------------------------------------------------------------------
// Collections
// ---------------------------------------------------------------------
function populateCollectionDatalist() {
  const list = document.getElementById('collectionNamesList');
  if (!list) return;
  list.innerHTML = '';
  collectionNamesCache.forEach((name) => {
    const opt = document.createElement('option');
    opt.value = name;
    list.appendChild(opt);
  });
}

async function ensureCollectionNamesLoaded() {
  if (collectionsFetchedOnce) return;
  try {
    await fetchCollections(false);
  } catch (e) {
    // non-critical — the save form still works without autocomplete suggestions
  }
}

async function fetchCollections(force) {
  if (collectionsFetchedOnce && !force) return collectionsData;
  const res = await fetch('/api/collections');
  const data = await res.json();
  if (!res.ok) throw new Error(data.error || 'Could not load your saved clips.');
  collectionsData = data.collections || {};
  collectionsFetchedOnce = true;
  collectionNamesCache = Object.keys(collectionsData);
  populateCollectionDatalist();
  return collectionsData;
}

async function loadCollectionsView(force) {
  collectionsStatus.className = 'status';
  collectionsStatus.textContent = 'Loading your saved clips...';
  collectionsList.innerHTML = '';
  try {
    const data = await fetchCollections(force);
    renderCollectionsView(data);
  } catch (e) {
    collectionsStatus.className = 'status error';
    collectionsStatus.textContent = e.message || 'Network error loading your saved clips.';
  }
}

function renderCollectionsView(data) {
  const names = Object.keys(data);
  collectionsList.innerHTML = '';
  collectionsStatus.className = 'status';

  if (!names.length) {
    collectionsStatus.textContent = 'No saved clips yet — hit "Save to collection" on any clip in Projects or Focus Mode.';
    return;
  }

  const totalClips = names.reduce((sum, n) => sum + data[n].length, 0);
  collectionsStatus.textContent = `${totalClips} clip${totalClips === 1 ? '' : 's'} across ${names.length} collection${names.length === 1 ? '' : 's'}.`;

  names.forEach((name) => {
    const clips = data[name];
    const section = document.createElement('div');
    section.className = 'collection-section';
    section.innerHTML = `<h3 class="collection-title">${name} <span class="collection-count">${clips.length}</span></h3>`;
    const list = document.createElement('div');
    list.className = 'results';
    section.appendChild(list);
    collectionsList.appendChild(section);

    clips.forEach((c) => {
      const div = document.createElement('div');
      div.className = 'clip';
      div.innerHTML = `
        <div class="meta"><span>${c.start} – ${c.end}</span><span class="score">score ${c.score}</span></div>
        <div class="hook">"${c.hook}"</div>
        ${c.reasoning ? `<div class="reasoning">🧠 ${c.reasoning}</div>` : ''}
        <div class="actions"></div>
        <div class="cut-status"></div>
        <div class="video-wrap"></div>
      `;
      list.appendChild(div);

      const actions = div.querySelector('.actions');
      const cutStatus = div.querySelector('.cut-status');
      const videoWrap = div.querySelector('.video-wrap');

      const cutBtn = document.createElement('button');
      cutBtn.className = 'secondary';
      cutBtn.textContent = 'Cut & download this clip';
      cutBtn.addEventListener('click', () => {
        cutBtn.disabled = true;
        cutClip(c.youtube_url, c.start_seconds, c.end_seconds, cutStatus, videoWrap, {})
          .finally(() => { cutBtn.disabled = false; });
      });
      actions.appendChild(cutBtn);

      const removeBtn = document.createElement('button');
      removeBtn.className = 'secondary';
      removeBtn.textContent = 'Remove';
      removeBtn.addEventListener('click', async () => {
        removeBtn.disabled = true;
        try {
          const res = await fetch(`/api/collections/clip/${c.id}`, { method: 'DELETE' });
          if (!res.ok) {
            const data = await res.json();
            alert(data.error || 'Could not remove that clip.');
            removeBtn.disabled = false;
            return;
          }
          collectionsFetchedOnce = false;
          div.remove();
          if (!list.children.length) {
            section.remove();
          } else {
            const countBadge = section.querySelector('.collection-count');
            if (countBadge) countBadge.textContent = String(list.children.length);
          }
        } catch (e) {
          alert('Network error removing clip.');
          removeBtn.disabled = false;
        }
      });
      actions.appendChild(removeBtn);
    });
  });
}
refreshCollectionsBtn.addEventListener('click', () => loadCollectionsView(true));

// ---------------------------------------------------------------------
// Exports
// ---------------------------------------------------------------------
const PLATFORM_PRESETS = [
  { key: 'tiktok', label: 'TikTok', style: 'karaoke_highlight' },
  { key: 'shorts', label: 'YouTube Shorts', style: 'bold_impact' },
  { key: 'reels', label: 'Instagram Reels', style: 'boxed' },
];

async function loadExportsView(force) {
  exportsStatus.className = 'status';
  exportsStatus.textContent = 'Loading your saved clips...';
  exportsList.innerHTML = '';
  try {
    const data = await fetchCollections(force);
    renderExportsView(data);
  } catch (e) {
    exportsStatus.className = 'status error';
    exportsStatus.textContent = e.message || 'Network error loading your saved clips.';
  }
}

function renderExportsView(data) {
  const allClips = Object.values(data).flat();
  exportsList.innerHTML = '';
  exportsStatus.className = 'status';

  if (!allClips.length) {
    exportsStatus.textContent = 'No saved clips yet — save some from Projects or Focus Mode first, then come back here to export them.';
    return;
  }
  exportsStatus.textContent = `${allClips.length} saved clip${allClips.length === 1 ? '' : 's'} ready to export.`;

  allClips.forEach((c) => {
    const div = document.createElement('div');
    div.className = 'clip';
    div.innerHTML = `
      <div class="meta"><span>${c.start} – ${c.end}</span><span class="score">score ${c.score}</span></div>
      <div class="hook">"${c.hook}"</div>
      <div class="platform-presets"></div>
      <div class="export-copy-wrap"></div>
      <div class="actions"></div>
      <div class="cut-status"></div>
      <div class="video-wrap"></div>
    `;
    exportsList.appendChild(div);

    const presetsRow = div.querySelector('.platform-presets');
    const copyWrap = div.querySelector('.export-copy-wrap');
    const actions = div.querySelector('.actions');
    const cutStatus = div.querySelector('.cut-status');
    const videoWrap = div.querySelector('.video-wrap');

    let selectedPreset = PLATFORM_PRESETS[0];

    function renderExportCopy() {
      if (!c.export_title && !(c.export_hashtags || []).length && !c.export_description) {
        copyWrap.innerHTML = '';
        return;
      }
      const hashtagsText = (c.export_hashtags || []).join(' ');
      copyWrap.innerHTML = `
        <div class="export-copy">
          <div class="export-copy-title">${c.export_title || ''}</div>
          <div class="export-copy-hashtags">${hashtagsText}</div>
          <div class="export-copy-desc">${c.export_description || ''}</div>
          <button type="button" class="secondary copy-btn">Copy to clipboard</button>
        </div>
      `;
      copyWrap.querySelector('.copy-btn').addEventListener('click', () => {
        const fullText = `${c.export_title || ''}\n\n${c.export_description || ''}\n\n${hashtagsText}`.trim();
        navigator.clipboard.writeText(fullText).then(() => {
          const btn = copyWrap.querySelector('.copy-btn');
          if (!btn) return;
          btn.textContent = 'Copied ✓';
          setTimeout(() => { btn.textContent = 'Copy to clipboard'; }, 1500);
        });
      });
    }
    renderExportCopy();

    PLATFORM_PRESETS.forEach((preset) => {
      const chip = document.createElement('button');
      chip.type = 'button';
      chip.className = 'preset-chip' + (preset.key === selectedPreset.key ? ' active' : '');
      chip.textContent = preset.label;
      chip.addEventListener('click', () => {
        selectedPreset = preset;
        presetsRow.querySelectorAll('.preset-chip').forEach((el) => el.classList.remove('active'));
        chip.classList.add('active');
        const cutBtn = actions.querySelector('.cut-preset-btn');
        if (cutBtn) cutBtn.textContent = `Cut for ${selectedPreset.label}`;
      });
      presetsRow.appendChild(chip);
    });

    const genBtn = document.createElement('button');
    genBtn.type = 'button';
    genBtn.className = 'secondary';
    genBtn.textContent = c.export_title ? 'Regenerate export copy' : 'Generate export copy';
    genBtn.addEventListener('click', async () => {
      genBtn.disabled = true;
      genBtn.textContent = 'Generating...';
      try {
        const res = await fetch(`/api/collections/clip/${c.id}/export-copy`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ platform: selectedPreset.key }),
        });
        const resData = await res.json();
        if (!res.ok) {
          alert(resData.error || 'Could not generate export copy.');
          return;
        }
        c.export_title = resData.clip.export_title;
        c.export_hashtags = resData.clip.export_hashtags;
        c.export_description = resData.clip.export_description;
        renderExportCopy();
      } catch (e) {
        alert('Network error generating export copy.');
      } finally {
        genBtn.disabled = false;
        genBtn.textContent = 'Regenerate export copy';
      }
    });
    actions.appendChild(genBtn);

    if (session.is_paid) {
      const cutBtn = document.createElement('button');
      cutBtn.className = 'secondary cut-preset-btn';
      cutBtn.textContent = `Cut for ${selectedPreset.label}`;
      cutBtn.addEventListener('click', () => {
        cutBtn.disabled = true;
        cutClip(c.youtube_url, c.start_seconds, c.end_seconds, cutStatus, videoWrap, {
          captions: true,
          vertical: true,
          caption_style: selectedPreset.style,
        }).finally(() => { cutBtn.disabled = false; });
      });
      actions.appendChild(cutBtn);
    } else {
      const lockNote = document.createElement('span');
      lockNote.className = 'lock-note';
      lockNote.textContent = 'Upgrade to unlock platform-ready cuts (captions + vertical crop)';
      lockNote.addEventListener('click', () => switchView('settings'));
      actions.appendChild(lockNote);
    }
  });
}
refreshExportsBtn.addEventListener('click', () => loadExportsView(true));

// ---------------------------------------------------------------------
// Project workspace (Clips / Timeline / Analyst / Summary tabs)
// ---------------------------------------------------------------------
function showProjectsList() {
  projectWorkspace.style.display = 'none';
  projectsListView.style.display = 'block';
}

function switchWorkspaceTab(tab) {
  document.querySelectorAll('.workspace-tab').forEach((btn) => {
    btn.classList.toggle('active', btn.dataset.wtab === tab);
  });
  document.querySelectorAll('.workspace-panel').forEach((el) => {
    el.classList.toggle('active', el.id === `wtab-${tab}`);
  });
}
document.querySelectorAll('.workspace-tab').forEach((btn) => {
  btn.addEventListener('click', () => switchWorkspaceTab(btn.dataset.wtab));
});
backToProjectsBtn.addEventListener('click', showProjectsList);

function openProjectWorkspace(meta) {
  currentProject = meta;
  // Timeline tab and the clip cards' cut/save actions both read off these
  // globals — same pattern the standalone Timeline view used before it
  // moved in here, just re-populated per project instead of per analyze.
  lastAnalyzeData = { clips: meta.clips, video_duration: meta.video_duration, isYoutube: meta.isYoutube };
  lastYoutubeUrl = meta.isYoutube ? meta.youtube_url : null;

  projectsListView.style.display = 'none';
  projectWorkspace.style.display = 'block';
  workspaceTitle.textContent = meta.isYoutube ? meta.youtube_url : 'Demo transcript';
  switchWorkspaceTab('clips');

  renderClips(meta.clips, meta.isYoutube, resultsEl, meta.youtube_url);
  renderTimeline();
  renderAnalystSummary(meta);
  renderVideoSummary(meta);
}

function renderAnalystSummary(meta) {
  const clips = meta.clips || [];
  if (!clips.length) {
    analystSummary.innerHTML = '<div class="status">No clips to analyze yet.</div>';
    return;
  }

  const avgScore = clips.reduce((sum, c) => sum + (c.score || 0), 0) / clips.length;
  const topClipIndex = clips.reduce((bestIdx, c, i) => (c.score > clips[bestIdx].score ? i : bestIdx), 0);
  const topClip = clips[topClipIndex];

  const clipsWithSubScores = clips.filter((c) => c.sub_scores && Object.keys(c.sub_scores).length > 0);
  let subScoreHtml;
  if (clipsWithSubScores.length) {
    const averages = {};
    Object.keys(SUB_SCORE_LABELS).forEach((key) => {
      const vals = clipsWithSubScores.map((c) => c.sub_scores[key]).filter((v) => typeof v === 'number');
      if (vals.length) averages[key] = Math.round(vals.reduce((a, b) => a + b, 0) / vals.length);
    });
    subScoreHtml = `<div class="score-rows">${Object.entries(averages).map(([key, val]) => `
      <div class="score-row">
        <span class="score-label">${SUB_SCORE_LABELS[key] || key}</span>
        <div class="score-bar"><div class="score-bar-fill" style="width:${val}%;"></div></div>
        <span class="score-val">${val}</span>
      </div>`).join('')}</div>`;
  } else {
    subScoreHtml = '<div class="status">AI Analyst breakdown wasn\'t available for this video (fell back to basic scoring), so there\'s no sub-score data to average.</div>';
  }

  analystSummary.innerHTML = `
    <div class="analyst-top-clip">
      <div class="analyst-top-clip-label">Top clip</div>
      <div class="hook">"${topClip.hook}"</div>
      <div class="meta"><span>${topClip.start} – ${topClip.end}</span><span class="score">score ${topClip.score}</span></div>
    </div>
    <h3 class="analyst-avg-heading">Average across ${clips.length} clip${clips.length === 1 ? '' : 's'} — overall score ${avgScore.toFixed(0)}</h3>
    ${subScoreHtml}
  `;

  const topClipEl = analystSummary.querySelector('.analyst-top-clip');
  if (topClipEl) {
    topClipEl.addEventListener('click', () => {
      switchWorkspaceTab('clips');
      const target = resultsEl.querySelectorAll('.clip')[topClipIndex];
      if (target) {
        target.scrollIntoView({ behavior: 'smooth', block: 'center' });
        target.classList.remove('flash');
        void target.offsetWidth;
        target.classList.add('flash');
      }
    });
  }
}

function renderVideoSummary(meta) {
  const clips = meta.clips || [];
  const duration = meta.video_duration || 0;
  if (!clips.length || !duration) {
    videoSummary.innerHTML = '<div class="status">No summary available yet.</div>';
    return;
  }

  const totalClipSeconds = clips.reduce((sum, c) => sum + Math.max(0, (c.end_seconds || 0) - (c.start_seconds || 0)), 0);
  const coveragePct = Math.min(100, Math.round((totalClipSeconds / duration) * 100));
  const avgScore = clips.reduce((sum, c) => sum + (c.score || 0), 0) / clips.length;

  let verdict;
  if (avgScore >= 80) verdict = 'Highly clippable — strong material throughout.';
  else if (avgScore >= 60) verdict = 'Solid clip potential — a handful of strong moments.';
  else verdict = 'Limited clip potential — only a few usable moments found.';

  videoSummary.innerHTML = `
    <div class="summary-stat-grid">
      <div class="summary-stat"><div class="summary-stat-val">${clips.length}</div><div class="summary-stat-label">Clips found</div></div>
      <div class="summary-stat"><div class="summary-stat-val">${formatSeconds(duration)}</div><div class="summary-stat-label">Video length</div></div>
      <div class="summary-stat"><div class="summary-stat-val">${coveragePct}%</div><div class="summary-stat-label">Video covered by clips</div></div>
      <div class="summary-stat"><div class="summary-stat-val">${avgScore.toFixed(0)}</div><div class="summary-stat-label">Average clip score</div></div>
    </div>
    <div class="summary-verdict"><b>Verdict:</b> ${verdict} <span class="summary-verdict-note">(based on the ${meta.scoring_method === 'llm' ? 'AI Analyst' : 'basic'} scores above)</span></div>
  `;
}

async function loadProjectList(force) {
  if (projectListFetchedOnce && !force) {
    renderProjectList(projectListCache);
    return;
  }
  projectList.innerHTML = '<div class="status">Loading your projects...</div>';
  try {
    const res = await fetch('/api/projects');
    const data = await res.json();
    if (!res.ok) {
      projectList.innerHTML = `<div class="status error">${data.error || 'Could not load your projects.'}</div>`;
      return;
    }
    projectListCache = data.projects || [];
    projectListFetchedOnce = true;
    renderProjectList(projectListCache);
  } catch (e) {
    projectList.innerHTML = '<div class="status error">Network error loading your projects.</div>';
  }
}

function renderProjectList(projects) {
  if (!projects.length) {
    projectList.innerHTML = `
      <div class="placeholder-card panel">
        <div class="big-icon"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M3.5 6.5a1 1 0 0 1 1-1H9l2 2.2h8.5a1 1 0 0 1 1 1V17a1 1 0 0 1-1 1h-15a1 1 0 0 1-1-1z"/></svg></div>
        <h2>No projects yet</h2>
        <p>Paste a YouTube link above and hit "Find clips" — every video you analyze is saved here so you can come back to it anytime.</p>
      </div>
    `;
    return;
  }

  projectList.innerHTML = '';
  projects.forEach((p) => {
    const row = document.createElement('div');
    row.className = 'project-row';
    const date = new Date(p.created_at).toLocaleDateString(undefined, { month: 'short', day: 'numeric', year: 'numeric' });
    row.innerHTML = `
      <div class="project-row-main">
        <div class="project-row-url">${p.youtube_url}</div>
        <div class="project-row-meta">${p.clip_count} clip${p.clip_count === 1 ? '' : 's'} · top score ${p.top_score} · ${p.scoring_method === 'llm' ? 'AI-analyzed' : 'basic scoring'} · ${date}</div>
      </div>
      <button type="button" class="secondary project-delete-btn">Delete</button>
    `;
    row.addEventListener('click', (e) => {
      if (e.target.closest('.project-delete-btn')) return;
      openProjectById(p.id);
    });
    row.querySelector('.project-delete-btn').addEventListener('click', async (e) => {
      e.stopPropagation();
      if (!window.confirm('Delete this project? This only removes it from ClipFind — nothing happens to the YouTube video itself.')) return;
      try {
        const res = await fetch(`/api/projects/${p.id}`, { method: 'DELETE' });
        if (!res.ok) {
          const data = await res.json();
          alert(data.error || 'Could not delete that project.');
          return;
        }
        projectListFetchedOnce = false;
        loadProjectList(true);
      } catch (err) {
        alert('Network error deleting that project.');
      }
    });
    projectList.appendChild(row);
  });
}

async function openProjectById(id) {
  try {
    const res = await fetch(`/api/projects/${id}`);
    const data = await res.json();
    if (!res.ok) {
      alert(data.error || 'Could not open that project.');
      return;
    }
    openProjectWorkspace({
      id: data.id,
      youtube_url: data.youtube_url,
      clips: data.clips,
      video_duration: data.video_duration || 0,
      scoring_method: data.scoring_method,
      isYoutube: true,
    });
  } catch (e) {
    alert('Network error opening that project.');
  }
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
    const youtubeUrlForThisRun = isYoutube ? (body && body.youtube_url) : null;
    let methodNote = data.scoring_method === 'llm' ? ' — AI-analyzed' : (data.scoring_method === 'heuristic' && data.source === 'youtube' ? ' — basic scoring (AI analysis unavailable right now)' : '');
    if (data.llm_debug) { methodNote += ` [debug: ${data.llm_debug}]`; }
    statusEl.textContent = `${data.clips.length} clips found${data.source === 'demo' ? ' (demo transcript)' : ''}${methodNote}`;
    openProjectWorkspace({
      id: data.project_id || null,
      youtube_url: youtubeUrlForThisRun,
      clips: data.clips,
      video_duration: data.video_duration || 0,
      scoring_method: data.scoring_method,
      isYoutube,
    });
    if (data.project_id) {
      projectListFetchedOnce = false; // new project saved server-side — next list visit should pick it up
    }
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
