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

const manualEditor = document.getElementById('manualEditor');
const manualRangeTrack = document.getElementById('manualRangeTrack');
const manualRangeFill = document.getElementById('manualRangeFill');
const manualHandleStart = document.getElementById('manualHandleStart');
const manualHandleEnd = document.getElementById('manualHandleEnd');
const manualStartInput = document.getElementById('manualStartInput');
const manualEndInput = document.getElementById('manualEndInput');
const manualDuration = document.getElementById('manualDuration');
const manualPreviewWrap = document.getElementById('manualPreviewWrap');
const manualPreviewBtn = document.getElementById('manualPreviewBtn');
const manualStyleControls = document.getElementById('manualStyleControls');
const manualCutBtn = document.getElementById('manualCutBtn');
const manualSaveBtn = document.getElementById('manualSaveBtn');
const manualSaveRow = document.getElementById('manualSaveRow');
const manualSaveNameInput = document.getElementById('manualSaveNameInput');
const manualSaveConfirmBtn = document.getElementById('manualSaveConfirmBtn');
const manualCutStatus = document.getElementById('manualCutStatus');
const manualVideoWrap = document.getElementById('manualVideoWrap');

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

const storyModeTabs = document.getElementById('storyModeTabs');
const storyGenerateMode = document.getElementById('storyGenerateMode');
const storyAnalyzeMode = document.getElementById('storyAnalyzeMode');
const storyGenreChips = document.getElementById('storyGenreChips');
const storyGenerateBtn = document.getElementById('storyGenerateBtn');
const storyTextInput = document.getElementById('storyTextInput');
const storyAnalyzeBtn = document.getElementById('storyAnalyzeBtn');
const storyStatus = document.getElementById('storyStatus');
const storyResult = document.getElementById('storyResult');
const storyProjectsList = document.getElementById('storyProjectsList');
const refreshStoriesBtn = document.getElementById('refreshStoriesBtn');

const dashUserName = document.getElementById('dashUserName');
const dashNewProjectBtn = document.getElementById('dashNewProjectBtn');
const dashUrlInput = document.getElementById('dashUrlInput');
const dashAnalyzeBtn = document.getElementById('dashAnalyzeBtn');
const dashStatus = document.getElementById('dashStatus');
const dashActionsGrid = document.getElementById('dashActionsGrid');
const dashProjectsGrid = document.getElementById('dashProjectsGrid');
const dashViewAllProjects = document.getElementById('dashViewAllProjects');
const dashAnalystWidget = document.getElementById('dashAnalystWidget');
const dashTopClipsWidget = document.getElementById('dashTopClipsWidget');

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
let manualSelStart = 0; // seconds — the custom trim editor's current selection
let manualSelEnd = 30;
let manualDragHandle = null; // 'start' | 'end' | null — which handle is being dragged, if any
let projectListCache = [];
let projectListFetchedOnce = false;
let storyStudioLoaded = false; // genres fetched once per page load
let storyGenres = [];
let selectedStoryGenre = null;
let storyMode = 'generate'; // 'generate' | 'analyze' — which input panel is showing
let storyProjectsCache = [];
let storyProjectsFetchedOnce = false;
// Captured once at page load — someone arriving via clipfind.com/?ref=CODE
// (or /app?ref=CODE directly) should still get attributed even if they
// poke around before actually signing up.
let pendingReferralCode = new URLSearchParams(window.location.search).get('ref') || null;

// ---------------------------------------------------------------------
// Loading indicator — animated bar + rotating status messages, used
// anywhere a request takes a real, noticeable amount of time (analyze,
// focus search, cutting). Replaces flat "Analyzing..." style text.
// Messages describe real sequential steps (transcript fetch, then LLM
// scoring; download, then encode) rather than a fabricated percentage —
// there's no reliable progress signal from the server for any of these,
// so an indeterminate sliding bar is the honest version of "this is
// actively working," and the rotating text gives a sense of where the
// time is actually going instead of one static word for the whole wait.
// ---------------------------------------------------------------------
const LOADING_MESSAGES = {
  analyze: [
    'Fetching the transcript…',
    'Reading through the video…',
    'Finding the best moments…',
    'Scoring each clip…',
  ],
  focus: [
    'Fetching the transcript…',
    'Searching for matching moments…',
  ],
  cut: [
    'Downloading the clip…',
    'Encoding the video…',
  ],
  cutStyled: [
    'Downloading the clip…',
    'Cropping and styling…',
    'Burning in captions…',
  ],
  storyGenerate: [
    'Writing the story…',
    'Scoring it as a Story Analyst would…',
  ],
  storyAnalyze: [
    'Reading through your story…',
    'Scoring it as a Story Analyst would…',
  ],
};

function showLoadingBar(el, messages) {
  el.innerHTML =
    '<div class="loading-bar"><div class="loading-bar-fill"></div></div>' +
    `<div class="loading-message">${messages[0]}</div>`;
  const msgEl = el.querySelector('.loading-message');
  let i = 0;
  const timer = setInterval(() => {
    i = (i + 1) % messages.length;
    msgEl.style.opacity = '0';
    setTimeout(() => {
      msgEl.textContent = messages[i];
      msgEl.style.opacity = '1';
    }, 250);
  }, 2200);
  return () => clearInterval(timer); // caller stops it once the real result is in
}

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
  storystudio: 'Story Studio',
  seriesmode: 'Series Mode',
  publishing: 'Publishing',
  brandkit: 'Brand Kit',
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
  if (view === 'dashboard') {
    renderDashboard();
  }
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
  if (view === 'storystudio' && !storyStudioLoaded) {
    loadStoryStudio();
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
// Manual clip editor — cut ANY custom range, not just AI-suggested
// clips. Lives on the Timeline tab since that's already the
// full-video-overview surface. Reuses cutClip()/style-controls/
// save-to-collection exactly like the AI clip cards do, so a manual
// selection is a first-class clip, not a second-class feature.
// ---------------------------------------------------------------------
function parseTimeToSeconds(text) {
  const parts = String(text).trim().split(':').map((p) => parseFloat(p));
  if (!parts.length || parts.some((p) => isNaN(p) || p < 0)) return null;
  let seconds = 0;
  for (const p of parts) seconds = seconds * 60 + p;
  return seconds;
}

function manualDuration_() {
  return Math.max((lastAnalyzeData && lastAnalyzeData.video_duration) || 1, 1);
}

function renderManualEditorPositions() {
  const duration = manualDuration_();
  const startPct = (manualSelStart / duration) * 100;
  const endPct = (manualSelEnd / duration) * 100;
  manualHandleStart.style.left = `${startPct}%`;
  manualHandleEnd.style.left = `${endPct}%`;
  manualRangeFill.style.left = `${startPct}%`;
  manualRangeFill.style.width = `${Math.max(endPct - startPct, 0)}%`;
  manualStartInput.value = formatSeconds(manualSelStart);
  manualEndInput.value = formatSeconds(manualSelEnd);
  manualDuration.textContent = `${formatSeconds(manualSelEnd - manualSelStart)} selected`;
  // Selection changed — the preview iframe (if shown) no longer matches
  // it, so hide it rather than show a stale range until "Preview" is
  // pressed again.
  manualPreviewWrap.style.display = 'none';
  manualPreviewWrap.innerHTML = '';
}

function setManualSelection(start, end) {
  const duration = manualDuration_();
  const minGap = 1; // seconds — handles can't cross/overlap
  start = Math.max(0, Math.min(start, duration));
  end = Math.max(0, Math.min(end, duration));
  if (end - start < minGap) {
    if (manualDragHandle === 'start') start = Math.max(0, end - minGap);
    else end = Math.min(duration, start + minGap);
  }
  manualSelStart = start;
  manualSelEnd = end;
  renderManualEditorPositions();
}

function trackPositionToSeconds(clientX) {
  const rect = manualRangeTrack.getBoundingClientRect();
  const pct = Math.max(0, Math.min(1, (clientX - rect.left) / rect.width));
  return pct * manualDuration_();
}

function initManualEditorDrag() {
  [manualHandleStart, manualHandleEnd].forEach((handle) => {
    handle.addEventListener('mousedown', (e) => {
      e.preventDefault();
      manualDragHandle = handle.dataset.handle;
    });
    handle.addEventListener('touchstart', (e) => {
      manualDragHandle = handle.dataset.handle;
    }, { passive: true });
  });

  document.addEventListener('mousemove', (e) => {
    if (!manualDragHandle) return;
    const seconds = trackPositionToSeconds(e.clientX);
    if (manualDragHandle === 'start') setManualSelection(seconds, manualSelEnd);
    else setManualSelection(manualSelStart, seconds);
  });
  document.addEventListener('touchmove', (e) => {
    if (!manualDragHandle || !e.touches.length) return;
    const seconds = trackPositionToSeconds(e.touches[0].clientX);
    if (manualDragHandle === 'start') setManualSelection(seconds, manualSelEnd);
    else setManualSelection(manualSelStart, seconds);
  }, { passive: true });
  document.addEventListener('mouseup', () => { manualDragHandle = null; });
  document.addEventListener('touchend', () => { manualDragHandle = null; });

  // Clicking empty track space (not a handle) jumps the nearer handle
  // there — quicker than dragging from wherever it currently sits.
  manualRangeTrack.addEventListener('mousedown', (e) => {
    if (e.target === manualHandleStart || e.target === manualHandleEnd) return;
    const seconds = trackPositionToSeconds(e.clientX);
    const distToStart = Math.abs(seconds - manualSelStart);
    const distToEnd = Math.abs(seconds - manualSelEnd);
    if (distToStart <= distToEnd) setManualSelection(seconds, manualSelEnd);
    else setManualSelection(manualSelStart, seconds);
  });

  manualStartInput.addEventListener('change', () => {
    const val = parseTimeToSeconds(manualStartInput.value);
    if (val === null) { renderManualEditorPositions(); return; }
    setManualSelection(val, manualSelEnd);
  });
  manualEndInput.addEventListener('change', () => {
    const val = parseTimeToSeconds(manualEndInput.value);
    if (val === null) { renderManualEditorPositions(); return; }
    setManualSelection(manualSelStart, val);
  });
}
initManualEditorDrag();

manualPreviewBtn.addEventListener('click', () => {
  const videoId = extractYoutubeVideoId(lastYoutubeUrl);
  if (!videoId) return;
  const start = Math.round(manualSelStart);
  const end = Math.round(manualSelEnd);
  manualPreviewWrap.innerHTML = `<iframe src="https://www.youtube.com/embed/${videoId}?start=${start}&end=${end}" allow="accelerate-compute; autoplay; encrypted-media" allowfullscreen></iframe>`;
  manualPreviewWrap.style.display = 'block';
});

function renderManualStyleControls() {
  manualStyleControls.innerHTML = '';
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

  manualStyleControls.appendChild(captionsLabel);
  manualStyleControls.appendChild(styleSelect);
  manualStyleControls.appendChild(verticalLabel);

  if (!isPaid) {
    manualStyleControls.classList.add('locked');
    captionsCheck.disabled = true;
    verticalCheck.disabled = true;
    styleSelect.disabled = true;
    const lockNote = document.createElement('span');
    lockNote.className = 'lock-note';
    lockNote.textContent = 'Upgrade to unlock styled captions & vertical crop';
    lockNote.addEventListener('click', () => switchView('settings'));
    manualStyleControls.appendChild(lockNote);
  } else {
    manualStyleControls.classList.remove('locked');
  }

  manualCutBtn.onclick = () => {
    manualCutBtn.disabled = true;
    const extras = isPaid
      ? { captions: captionsCheck.checked, caption_style: styleSelect.value, vertical: verticalCheck.checked }
      : {};
    cutClip(lastYoutubeUrl, manualSelStart, manualSelEnd, manualCutStatus, manualVideoWrap, extras)
      .finally(() => { manualCutBtn.disabled = false; });
  };
}

manualSaveBtn.addEventListener('click', () => {
  const showing = manualSaveRow.style.display !== 'none';
  manualSaveRow.style.display = showing ? 'none' : 'flex';
  if (!showing) {
    ensureCollectionNamesLoaded();
    manualSaveNameInput.focus();
  }
});
manualSaveConfirmBtn.addEventListener('click', async () => {
  const name = manualSaveNameInput.value.trim() || 'Saved Clips';
  manualSaveConfirmBtn.disabled = true;
  try {
    const res = await fetch('/api/collections/save', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        collection_name: name,
        youtube_url: lastYoutubeUrl,
        start_seconds: manualSelStart,
        end_seconds: manualSelEnd,
        hook: 'Custom clip',
        reasoning: '',
        score: 0,
      }),
    });
    const data = await res.json();
    if (!res.ok) {
      alert(data.error || 'Could not save that clip.');
      return;
    }
    collectionsFetchedOnce = false;
    manualSaveConfirmBtn.textContent = 'Saved ✓';
    setTimeout(() => {
      manualSaveRow.style.display = 'none';
      manualSaveConfirmBtn.textContent = 'Save';
      manualSaveNameInput.value = '';
    }, 1200);
  } catch (e) {
    alert('Network error saving that clip.');
  } finally {
    manualSaveConfirmBtn.disabled = false;
  }
});

function renderManualEditor() {
  if (!lastAnalyzeData || !lastAnalyzeData.isYoutube || !lastYoutubeUrl) {
    // No real source video to cut from (e.g. viewing the demo transcript) —
    // same gate the AI clip cards already use for their cut/save actions.
    manualEditor.style.display = 'none';
    return;
  }
  manualEditor.style.display = 'block';
  const duration = manualDuration_();
  // Default to the first 30s (or the whole video if shorter) rather than
  // carrying over a stale selection from a previously-viewed project.
  manualSelStart = 0;
  manualSelEnd = Math.min(30, duration);
  renderManualEditorPositions();
  renderManualStyleControls();
  manualCutStatus.textContent = '';
  manualVideoWrap.innerHTML = '';
  manualSaveRow.style.display = 'none';
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
  const stopLoading = showLoadingBar(focusStatus, LOADING_MESSAGES.focus);
  focusResults.innerHTML = '';
  focusBtn.disabled = true;
  try {
    const res = await fetch('/api/focus', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ youtube_url: url, query }),
    });
    const data = await res.json();
    stopLoading();
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
    stopLoading();
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
  const stopLoading = showLoadingBar(statusNode, willStyle ? LOADING_MESSAGES.cutStyled : LOADING_MESSAGES.cut);
  try {
    const res = await fetch('/api/cut', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ youtube_url: youtubeUrl, start, end, ...(extras || {}) }),
    });
    const data = await res.json();
    stopLoading();
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
    stopLoading();
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
  // Story Analyst metrics (story_studio.ANALYST_METRICS) — virality and
  // emotional_impact above are already shared with the clip Analyst, so
  // only the story-specific ones need adding here.
  hook_strength: 'Hook Strength',
  suspense: 'Suspense',
  curiosity: 'Curiosity',
  payoff_quality: 'Payoff Quality',
  story_flow: 'Story Flow',
  replay_potential: 'Replay Potential',
  completion_prediction: 'Completion Prediction',
  difficulty_to_adapt: 'Difficulty to Adapt',
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
  renderManualEditor();
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
// Dashboard
// ---------------------------------------------------------------------
function extractYoutubeVideoId(url) {
  if (!url) return null;
  const m = String(url).match(/(?:v=|\/videos\/|embed\/|shorts\/|youtu\.be\/)([A-Za-z0-9_-]{11})/);
  return m ? m[1] : null;
}

function youtubeThumbUrl(url) {
  const id = extractYoutubeVideoId(url);
  return id ? `https://img.youtube.com/vi/${id}/hqdefault.jpg` : '';
}

const DASH_QUICK_ACTIONS = [
  {
    icon: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M3.5 6.5a1 1 0 0 1 1-1H9l2 2.2h8.5a1 1 0 0 1 1 1V17a1 1 0 0 1-1 1h-15a1 1 0 0 1-1-1z"/></svg>',
    color: 'linear-gradient(135deg, var(--accent), var(--accent2))',
    title: 'AI Clip Finder', sub: 'Find viral-worthy clips', view: 'projects',
  },
  {
    icon: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="7.5"/><circle cx="12" cy="12" r="3.2"/><path d="M12 2.5v3M12 18.5v3M2.5 12h3M18.5 12h3"/></svg>',
    color: 'linear-gradient(135deg, #3ddc97, #2bb37c)',
    title: 'AI Focus Mode', sub: 'Search for exact moments', view: 'focusmode',
  },
  {
    icon: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M12 3c1 3-3 4-3 7.5a3 3 0 0 0 6 0c0-1.2-.6-2-.6-2 1.8 1 3.1 3 3.1 5.2A5.5 5.5 0 0 1 12 19a5.5 5.5 0 0 1-5.5-5.5C6.5 8.5 10 8 12 3z"/></svg>',
    color: 'linear-gradient(135deg, #ffc857, #e8a83e)',
    title: 'Discover', sub: 'Trending videos to clip', view: 'discover',
  },
  {
    icon: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M6 3.5h12a1 1 0 0 1 1 1V21l-7-4-7 4V4.5a1 1 0 0 1 1-1z"/></svg>',
    color: 'linear-gradient(135deg, #5c8cff, #7c5cff)',
    title: 'Collections', sub: 'Your saved clips', view: 'collections',
  },
  {
    icon: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M12 15V4M12 4 8 8M12 4l4 4"/><path d="M4.5 14v4.5a1.5 1.5 0 0 0 1.5 1.5h12a1.5 1.5 0 0 0 1.5-1.5V14"/></svg>',
    color: 'linear-gradient(135deg, #ff5c9a, #ff8c5c)',
    title: 'Exports', sub: 'Titles, hashtags, captions', view: 'exports',
  },
  {
    icon: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M12 3.5c.7 2.2-1.8 3-1.8 5.2a1.8 1.8 0 0 0 3.6 0c0-.7-.3-1.2-.3-1.2 1 .6 1.8 1.8 1.8 3.1A3.3 3.3 0 0 1 12 14a3.3 3.3 0 0 1-3.3-3.3C8.7 7.8 10.9 6 12 3.5z"/><path d="M6 15.5h12M7.5 18.5h9M9 21.5h6"/></svg>',
    color: 'linear-gradient(135deg, #7c5cff, #ff5c9a)',
    title: 'Story Studio', sub: 'AI narrated story videos', view: 'storystudio', soon: true,
  },
];

function renderDashActions() {
  dashActionsGrid.innerHTML = DASH_QUICK_ACTIONS.map((a, i) => `
    <div class="dash-action-card${a.soon ? ' soon' : ''}" data-action-idx="${i}">
      <div class="dash-action-icon" style="background:${a.color};">${a.icon}</div>
      <div class="dash-action-title">${a.title}${a.soon ? ' <span class="soon-badge">soon</span>' : ''}</div>
      <div class="dash-action-sub">${a.sub}</div>
    </div>
  `).join('');
  dashActionsGrid.querySelectorAll('.dash-action-card').forEach((el) => {
    el.addEventListener('click', () => {
      const action = DASH_QUICK_ACTIONS[Number(el.dataset.actionIdx)];
      if (action.soon) return;
      switchView(action.view);
    });
  });
}

function renderDashProjects() {
  const recent = projectListCache.slice(0, 6);
  if (!recent.length) {
    dashProjectsGrid.innerHTML = `<div class="dash-empty-mini" style="grid-column:1/-1;">No projects yet — paste a link above to analyze your first video.</div>`;
    return;
  }
  dashProjectsGrid.innerHTML = recent.map((p) => {
    const thumb = youtubeThumbUrl(p.youtube_url);
    const date = new Date(p.created_at).toLocaleDateString(undefined, { month: 'short', day: 'numeric' });
    const label = extractYoutubeVideoId(p.youtube_url) || p.youtube_url;
    return `
      <div class="dash-project-card" data-project-id="${p.id}">
        <div class="dash-project-thumb" style="${thumb ? `background-image:url('${thumb}')` : ''}">
          <div class="dash-project-score">${p.top_score}</div>
        </div>
        <div class="dash-project-body">
          <div class="dash-project-title">${label}</div>
          <div class="dash-project-meta">${p.clip_count} clip${p.clip_count === 1 ? '' : 's'} · ${date}</div>
        </div>
      </div>
    `;
  }).join('');
  dashProjectsGrid.querySelectorAll('.dash-project-card').forEach((el) => {
    el.addEventListener('click', () => {
      switchView('projects');
      openProjectById(Number(el.dataset.projectId));
    });
  });
}

function renderDashAnalystWidget() {
  const scores = projectListCache.map((p) => p.top_score).filter((s) => typeof s === 'number');
  if (!scores.length) {
    dashAnalystWidget.innerHTML = `
      <div class="dash-widget-title">AI Analyst Overview</div>
      <div class="dash-empty-mini">Analyze a video to see your score breakdown here.</div>
    `;
    return;
  }
  const avg = scores.reduce((a, b) => a + b, 0) / scores.length;
  const bucketCounts = { excellent: 0, good: 0, average: 0, poor: 0 };
  scores.forEach((s) => {
    if (s >= 85) bucketCounts.excellent++;
    else if (s >= 70) bucketCounts.good++;
    else if (s >= 50) bucketCounts.average++;
    else bucketCounts.poor++;
  });
  const total = scores.length;
  const order = ['excellent', 'good', 'average', 'poor'];
  const labels = { excellent: 'Excellent (85+)', good: 'Good (70-84)', average: 'Average (50-69)', poor: 'Poor (<50)' };
  const colors = { excellent: 'var(--green)', good: '#5c8cff', average: 'var(--yellow)', poor: 'var(--red)' };
  const pcts = {};
  order.forEach((k) => { pcts[k] = Math.round((bucketCounts[k] / total) * 100); });

  let acc = 0;
  const stops = order.map((k) => {
    const start = acc;
    acc += pcts[k];
    return `${colors[k]} ${start}% ${acc}%`;
  }).join(', ');

  const legendRows = order.map((k) => `
    <div class="dash-score-legend-row"><i style="background:${colors[k]};"></i><span class="label">${labels[k]}</span><span class="pct">${pcts[k]}%</span></div>
  `).join('');

  dashAnalystWidget.innerHTML = `
    <div class="dash-widget-title">AI Analyst Overview</div>
    <div class="dash-score-gauge-wrap">
      <div class="dash-score-gauge" style="background: conic-gradient(${stops});">
        <div class="dash-score-gauge-val">${Math.round(avg)}</div>
      </div>
      <div class="dash-score-legend">${legendRows}</div>
    </div>
  `;
}

function renderDashTopClipsWidget() {
  const top = [...projectListCache]
    .filter((p) => p.clip_count > 0)
    .sort((a, b) => b.top_score - a.top_score)
    .slice(0, 3);
  if (!top.length) {
    dashTopClipsWidget.innerHTML = `
      <div class="dash-widget-title">Top Performing Clips</div>
      <div class="dash-empty-mini">Nothing scored yet.</div>
    `;
    return;
  }
  const rows = top.map((p) => {
    const thumb = youtubeThumbUrl(p.youtube_url);
    const label = extractYoutubeVideoId(p.youtube_url) || p.youtube_url;
    return `
      <div class="dash-top-clip-row" data-project-id="${p.id}">
        <div class="dash-top-clip-thumb" style="${thumb ? `background-image:url('${thumb}')` : ''}"></div>
        <div class="dash-top-clip-info">
          <div class="dash-top-clip-title">${label}</div>
          <div class="dash-top-clip-meta">${p.clip_count} clip${p.clip_count === 1 ? '' : 's'} found</div>
        </div>
        <div class="dash-top-clip-score">${p.top_score}</div>
      </div>
    `;
  }).join('');
  dashTopClipsWidget.innerHTML = `<div class="dash-widget-title">Top Performing Clips</div>${rows}`;
  dashTopClipsWidget.querySelectorAll('.dash-top-clip-row').forEach((el) => {
    el.addEventListener('click', () => {
      switchView('projects');
      openProjectById(Number(el.dataset.projectId));
    });
  });
}

async function renderDashboard() {
  const name = (session.email || '').split('@')[0];
  dashUserName.textContent = name ? `, ${name}` : '';
  renderDashActions();
  if (!projectListFetchedOnce) {
    await loadProjectList(false);
  }
  renderDashProjects();
  renderDashAnalystWidget();
  renderDashTopClipsWidget();
}

dashNewProjectBtn.addEventListener('click', () => {
  switchView('projects');
  showProjectsList();
  urlInput.focus();
});

dashViewAllProjects.addEventListener('click', (e) => {
  e.preventDefault();
  switchView('projects');
});

dashAnalyzeBtn.addEventListener('click', () => {
  const url = dashUrlInput.value.trim();
  if (!url) {
    dashStatus.className = 'status error';
    dashStatus.textContent = 'Paste a YouTube URL first.';
    return;
  }
  dashStatus.className = 'status';
  dashStatus.textContent = '';
  urlInput.value = url;
  switchView('projects');
  showProjectsList();
  run('/api/analyze', { youtube_url: url, top: 6 });
});
dashUrlInput.addEventListener('keydown', (e) => {
  if (e.key === 'Enter') dashAnalyzeBtn.click();
});

// ---------------------------------------------------------------------
// Analyze / demo
// ---------------------------------------------------------------------
async function run(endpoint, body) {
  statusEl.className = 'status';
  const stopLoading = showLoadingBar(statusEl, LOADING_MESSAGES.analyze);
  resultsEl.innerHTML = '';
  analyzeBtn.disabled = true; demoBtn.disabled = true;
  try {
    const res = await fetch(endpoint, {
      method: body ? 'POST' : 'GET',
      headers: body ? { 'Content-Type': 'application/json' } : undefined,
      body: body ? JSON.stringify(body) : undefined,
    });
    const data = await res.json();
    stopLoading();
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
    stopLoading();
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
// Story Studio
// ---------------------------------------------------------------------
// Generate-vs-analyze mode toggle reuses the same .preset-chip look as
// the Focus Mode presets — no new chip styling needed, just different
// data attributes and two panels swapped by display:none.
storyModeTabs.querySelectorAll('.preset-chip').forEach((tab) => {
  tab.addEventListener('click', () => {
    storyMode = tab.dataset.mode;
    storyModeTabs.querySelectorAll('.preset-chip').forEach((t) => t.classList.toggle('active', t === tab));
    storyGenerateMode.style.display = storyMode === 'generate' ? 'block' : 'none';
    storyAnalyzeMode.style.display = storyMode === 'analyze' ? 'block' : 'none';
    storyStatus.className = 'status';
    storyStatus.textContent = '';
  });
});

function renderStoryGenreChips() {
  if (!selectedStoryGenre && storyGenres.length) selectedStoryGenre = storyGenres[0];
  storyGenreChips.innerHTML = storyGenres
    .map((g) => `<button type="button" class="preset-chip${g === selectedStoryGenre ? ' active' : ''}" data-genre="${g}">${g}</button>`)
    .join('');
  storyGenreChips.querySelectorAll('.preset-chip').forEach((chip) => {
    chip.addEventListener('click', () => {
      selectedStoryGenre = chip.dataset.genre;
      renderStoryGenreChips();
    });
  });
}

let storyVoicesCache = [];
let storyVoicesFetchedOnce = false;
let storyTtsConfigured = false;
let storyFootageCategoriesCache = [];
let storyFootageCategoriesFetchedOnce = false;
let activeStoryVideoPoll = null; // setInterval id — only one story's status should ever be polling at once

async function loadStoryStudio() {
  storyStudioLoaded = true;
  try {
    const res = await fetch('/api/story-studio/genres');
    const data = await res.json();
    if (res.ok) {
      storyGenres = data.genres || [];
      renderStoryGenreChips();
    }
  } catch (e) {
    // Non-fatal — genre chips just stay empty, Generate will fail with a
    // clear "pick a genre" message rather than silently doing nothing.
  }
  loadStoryProjects(false);
}

async function loadStoryVoicesAndCategories() {
  if (!storyVoicesFetchedOnce) {
    try {
      const res = await fetch('/api/story-studio/voices');
      const data = await res.json();
      if (res.ok) {
        storyVoicesCache = data.voices || [];
        storyTtsConfigured = !!data.tts_configured;
      }
    } catch (e) {
      // silent — the video section just shows "no voices available"
    }
    storyVoicesFetchedOnce = true;
  }
  if (!storyFootageCategoriesFetchedOnce) {
    try {
      const res = await fetch('/api/story-studio/footage-categories');
      const data = await res.json();
      if (res.ok) storyFootageCategoriesCache = data.categories || [];
    } catch (e) {
      // silent, same reasoning
    }
    storyFootageCategoriesFetchedOnce = true;
  }
}

function stopStoryVideoPoll() {
  if (activeStoryVideoPoll) {
    clearInterval(activeStoryVideoPoll);
    activeStoryVideoPoll = null;
  }
}

function pollStoryVideoStatus(storyId, statusEl, playerWrapEl, generateBtn) {
  stopStoryVideoPoll(); // only one story's video should ever be polling — switching stories replaces it
  activeStoryVideoPoll = setInterval(async () => {
    try {
      const res = await fetch(`/api/story-studio/projects/${storyId}/video-status`);
      const data = await res.json();
      if (!res.ok) return; // transient — next tick retries rather than giving up on one bad response
      if (data.status === 'processing') {
        statusEl.className = 'status';
        statusEl.textContent = 'Generating narration and assembling the video — this can take a minute or two…';
        return;
      }
      stopStoryVideoPoll();
      generateBtn.disabled = false;
      if (data.status === 'ready') {
        statusEl.className = 'status';
        statusEl.textContent = 'Video ready.';
        playerWrapEl.innerHTML = `<video controls preload="metadata" src="${data.video_url}"></video>`;
        // Keep the saved-stories cache in sync so navigating away and back
        // (which re-renders from this cache) shows the finished video
        // instead of a stale "not generated yet" state.
        const cached = storyProjectsCache.find((s) => s.id === storyId);
        if (cached) {
          cached.video_status = 'ready';
          cached.video_url = data.video_url;
        }
      } else if (data.status === 'failed') {
        statusEl.className = 'status error';
        statusEl.textContent = data.error || 'Video generation failed.';
        const cached = storyProjectsCache.find((s) => s.id === storyId);
        if (cached) {
          cached.video_status = 'failed';
          cached.video_error = data.error;
        }
      }
    } catch (e) {
      // transient network hiccup — next tick retries
    }
  }, 3000);
}

function renderStoryVideoSectionHtml(story) {
  const voiceOptions = storyVoicesCache
    .map((v) => `<option value="${v.key}"${v.key === story.voice ? ' selected' : ''}>${v.label}</option>`)
    .join('');
  const categoryOptions = storyFootageCategoriesCache
    .map((c) => `<option value="${c.key}"${c.key === story.footage_category ? ' selected' : ''}>${c.label} (${c.footage_count} clip${c.footage_count === 1 ? '' : 's'})</option>`)
    .join('');
  return `
    <div class="story-video-section">
      <div class="story-video-controls">
        <select id="storyVideoVoiceSelect">${voiceOptions || '<option value="">No voices available</option>'}</select>
        <select id="storyVideoCategorySelect">${categoryOptions || '<option value="">No footage categories available</option>'}</select>
        <button type="button" id="storyGenerateVideoBtn">Generate video</button>
      </div>
      <div class="status" id="storyVideoStatus"></div>
      <div id="storyVideoPlayerWrap"></div>
    </div>
  `;
}

function wireStoryVideoSection(story) {
  const voiceSelect = document.getElementById('storyVideoVoiceSelect');
  const categorySelect = document.getElementById('storyVideoCategorySelect');
  const generateBtn = document.getElementById('storyGenerateVideoBtn');
  const statusEl = document.getElementById('storyVideoStatus');
  const playerWrap = document.getElementById('storyVideoPlayerWrap');
  if (!generateBtn) return;

  if (!storyTtsConfigured) {
    generateBtn.disabled = true;
    statusEl.textContent = "Voice narration isn't configured on this server yet.";
  } else if (!storyFootageCategoriesCache.length) {
    generateBtn.disabled = true;
    statusEl.textContent = 'No background footage categories are available yet.';
  }

  if (story.video_status === 'processing') {
    generateBtn.disabled = true;
    pollStoryVideoStatus(story.id, statusEl, playerWrap, generateBtn);
  } else if (story.video_status === 'ready' && story.video_url) {
    statusEl.className = 'status';
    statusEl.textContent = 'Video ready.';
    playerWrap.innerHTML = `<video controls preload="metadata" src="${story.video_url}"></video>`;
  } else if (story.video_status === 'failed') {
    statusEl.className = 'status error';
    statusEl.textContent = story.video_error || 'Video generation failed.';
  }

  generateBtn.addEventListener('click', async () => {
    generateBtn.disabled = true;
    statusEl.className = 'status';
    statusEl.textContent = 'Starting…';
    playerWrap.innerHTML = '';
    try {
      const res = await fetch(`/api/story-studio/projects/${story.id}/generate-video`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ voice: voiceSelect.value, footage_category: categorySelect.value }),
      });
      const data = await res.json();
      if (!res.ok) {
        generateBtn.disabled = false;
        statusEl.className = 'status error';
        statusEl.textContent = data.error || 'Could not start video generation.';
        if (data.upgrade_required) switchView('settings');
        return;
      }
      pollStoryVideoStatus(story.id, statusEl, playerWrap, generateBtn);
    } catch (e) {
      generateBtn.disabled = false;
      statusEl.className = 'status error';
      statusEl.textContent = 'Network error starting video generation.';
    }
  });
}

async function renderStoryVideoSectionAsync(story) {
  await loadStoryVoicesAndCategories();
  const wrap = document.getElementById('storyVideoSectionWrap');
  if (!wrap) return; // the user navigated to a different story before this resolved
  wrap.innerHTML = renderStoryVideoSectionHtml(story);
  wireStoryVideoSection(story);
}

function renderStoryResult(story) {
  stopStoryVideoPoll(); // switching to a different story shouldn't keep polling the old one's status
  const genreLabel = story.source === 'user_submitted' ? 'Your story' : story.genre;
  storyResult.innerHTML = `
    <div class="results">
      <div class="clip">
        <div class="meta">
          <span>${genreLabel} · ${formatSeconds(story.estimated_watch_time_seconds)} watch time</span>
          <span class="score">${story.overall_score}/100</span>
        </div>
        <div class="hook">${story.title}</div>
        <div class="preview" style="white-space:pre-wrap;">${story.body}</div>
        ${story.reasoning ? `<div class="reasoning">${story.reasoning}</div>` : ''}
        ${renderAnalystBreakdown(story.sub_scores, [])}
        <div id="storyVideoSectionWrap"></div>
      </div>
    </div>
  `;
  // Only stories that made it to the DB (generate/analyze always save one,
  // and every entry in the saved-stories list obviously has one) can have
  // a video generated against them — id is the only thing this depends on.
  if (story.id) {
    renderStoryVideoSectionAsync(story);
  }
}

async function runStoryGenerate() {
  if (!selectedStoryGenre) {
    storyStatus.className = 'status error';
    storyStatus.textContent = 'Pick a genre first.';
    return;
  }
  storyStatus.className = 'status';
  const stopLoading = showLoadingBar(storyStatus, LOADING_MESSAGES.storyGenerate);
  storyResult.innerHTML = '';
  storyGenerateBtn.disabled = true;
  try {
    const res = await fetch('/api/story-studio/generate', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ genre: selectedStoryGenre }),
    });
    const data = await res.json();
    stopLoading();
    if (!res.ok) {
      storyStatus.className = 'status error';
      if (data.auth_required) {
        storyStatus.textContent = 'Sign in first.';
      } else if (data.upgrade_required) {
        storyStatus.textContent = data.error;
        switchView('settings');
      } else {
        storyStatus.textContent = data.error || 'Could not generate a story.';
      }
      return;
    }
    storyStatus.textContent = '';
    renderStoryResult(data.story);
    storyProjectsFetchedOnce = false;
    loadStoryProjects(false);
  } catch (e) {
    stopLoading();
    storyStatus.className = 'status error';
    storyStatus.textContent = 'Network error while generating.';
  } finally {
    storyGenerateBtn.disabled = false;
  }
}
storyGenerateBtn.addEventListener('click', runStoryGenerate);

async function runStoryAnalyze() {
  const text = storyTextInput.value.trim();
  if (!text) {
    storyStatus.className = 'status error';
    storyStatus.textContent = 'Paste a story first.';
    return;
  }
  storyStatus.className = 'status';
  const stopLoading = showLoadingBar(storyStatus, LOADING_MESSAGES.storyAnalyze);
  storyResult.innerHTML = '';
  storyAnalyzeBtn.disabled = true;
  try {
    const res = await fetch('/api/story-studio/analyze', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ story_text: text }),
    });
    const data = await res.json();
    stopLoading();
    if (!res.ok) {
      storyStatus.className = 'status error';
      if (data.auth_required) {
        storyStatus.textContent = 'Sign in first.';
      } else if (data.upgrade_required) {
        storyStatus.textContent = data.error;
        switchView('settings');
      } else {
        storyStatus.textContent = data.error || 'Could not analyze that story.';
      }
      return;
    }
    storyStatus.textContent = '';
    renderStoryResult(data.story);
    storyProjectsFetchedOnce = false;
    loadStoryProjects(false);
  } catch (e) {
    stopLoading();
    storyStatus.className = 'status error';
    storyStatus.textContent = 'Network error while analyzing.';
  } finally {
    storyAnalyzeBtn.disabled = false;
  }
}
storyAnalyzeBtn.addEventListener('click', runStoryAnalyze);

async function loadStoryProjects(force) {
  if (storyProjectsFetchedOnce && !force) return;
  try {
    const res = await fetch('/api/story-studio/projects');
    const data = await res.json();
    if (!res.ok) return;
    storyProjectsCache = data.stories || [];
    storyProjectsFetchedOnce = true;
    renderStoryProjectsList();
  } catch (e) {
    // Silent — this is a secondary "saved stories" list under the main
    // generate/analyze flow, not worth a dedicated error UI of its own.
  }
}

function renderStoryProjectsList() {
  if (!storyProjectsCache.length) {
    storyProjectsList.innerHTML = '<div style="color:var(--text-dimmer);font-size:0.85rem;">No saved stories yet — generate or analyze one above.</div>';
    return;
  }
  storyProjectsList.innerHTML = storyProjectsCache.map((s) => `
    <div class="clip">
      <div class="meta">
        <span>${s.source === 'user_submitted' ? 'Your story' : s.genre} · ${new Date(s.created_at).toLocaleDateString()}</span>
        <span class="score">${s.overall_score}/100</span>
      </div>
      <div class="hook">${s.title}</div>
      <div class="row" style="margin-top:10px;">
        <button type="button" class="secondary" data-view-story="${s.id}">View</button>
        <button type="button" class="secondary" data-delete-story="${s.id}">Delete</button>
      </div>
    </div>
  `).join('');

  storyProjectsList.querySelectorAll('[data-view-story]').forEach((btn) => {
    btn.addEventListener('click', () => {
      const story = storyProjectsCache.find((s) => String(s.id) === btn.dataset.viewStory);
      if (story) {
        renderStoryResult(story);
        storyStatus.className = 'status';
        storyStatus.textContent = '';
      }
    });
  });
  storyProjectsList.querySelectorAll('[data-delete-story]').forEach((btn) => {
    btn.addEventListener('click', async () => {
      const id = btn.dataset.deleteStory;
      btn.disabled = true;
      try {
        const res = await fetch(`/api/story-studio/projects/${id}`, { method: 'DELETE' });
        if (res.ok) {
          storyProjectsCache = storyProjectsCache.filter((s) => String(s.id) !== id);
          renderStoryProjectsList();
        } else {
          btn.disabled = false;
        }
      } catch (e) {
        btn.disabled = false;
      }
    });
  });
}
refreshStoriesBtn.addEventListener('click', () => loadStoryProjects(true));

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
  } else if (session.logged_in) {
    // Dashboard is the default active view in the HTML itself (no nav
    // click fires switchView('dashboard') to trigger its data load), so
    // it needs an explicit render here once the session/account info is
    // actually available.
    renderDashboard();
  }
});
