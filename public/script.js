const API_BASE = (() => {
  const { hostname, protocol, port, origin } = window.location;
  if (hostname === 'localhost' || hostname === '127.0.0.1') {
    return `http://${hostname}:8000`;
  }
  if (hostname.includes('replit') || hostname.includes('repl.co') || port === '5000') {
    return `${protocol}//${hostname}:8000`;
  }
  return origin;
})();

const AUTH = 'Bearer freecraft_key_2025';
const HEADERS = { 'Content-Type': 'application/json', 'Authorization': AUTH };

const state = {
  view: 'browse',
  searchType: 'name',
  searchQuery: '',
  searchResults: [],
  searchSkip: 0,
  searchHasMore: false,
  browseCategory: '',
  browseResults: [],
  browseSkip: 0,
  browseHasMore: false,
  apiOnline: false,
  searchDebounce: null,
};

const LIMIT = 24;

function toast(msg, type = 'info', ms = 3500) {
  const c = document.getElementById('toastContainer');
  const icon = { ok: '✅', err: '⚠️', info: 'ℹ️' }[type] || 'ℹ️';
  const el = document.createElement('div');
  el.className = `toast toast-${type}`;
  el.innerHTML = `<span>${icon}</span><span>${msg}</span>`;
  c.appendChild(el);
  setTimeout(() => {
    el.classList.add('out');
    el.addEventListener('animationend', () => el.remove(), { once: true });
  }, ms);
}

function setStatus(online) {
  state.apiOnline = online;
  const badge = document.getElementById('statusBadge');
  const txt = document.getElementById('statusText');
  if (!badge || !txt) return;
  badge.className = 'api-status ' + (online ? 'online' : 'error');
  txt.textContent = online ? 'Online' : 'Offline';
}

async function checkHealth() {
  try {
    const r = await fetch(`${API_BASE}/api/health`, {
      headers: HEADERS, signal: AbortSignal.timeout(5000)
    });
    setStatus(r.ok);
    return r.ok;
  } catch {
    setStatus(false);
    return false;
  }
}

function switchView(view) {
  state.view = view;
  document.querySelectorAll('.view').forEach(v => v.classList.remove('active'));
  document.querySelectorAll('.nav-btn').forEach(t => {
    t.classList.toggle('active', t.dataset.view === view);
  });
  const el = document.getElementById(view === 'browse' ? 'viewBrowse' : 'viewSearch');
  if (el) el.classList.add('active');
  if (view === 'search') {
    setTimeout(() => document.getElementById('searchInput')?.focus(), 50);
  }
  window.scrollTo({ top: 0, behavior: 'smooth' });
}

function goHome() {
  switchView('browse');
  backToCategories();
}

const CAT_NAMES = {
  addon: 'Add-Ons', texture: 'Textures', skin: 'Skins',
  mashup: 'Mashups', newest: 'New Arrivals', name: 'All Content'
};

async function browseCategory(cat) {
  state.browseCategory = cat;
  state.browseSkip = 0;
  state.browseResults = [];

  document.getElementById('categoriesSection').style.display = 'none';
  document.getElementById('suggestSection').style.display = 'none';
  const rs = document.getElementById('browseResultsSection');
  rs.style.display = 'block';

  const titleEl = document.getElementById('browseTitle');
  const subEl = document.getElementById('browseSubtitle');
  const lmBtn = document.getElementById('loadMoreBtn');
  if (titleEl) titleEl.textContent = CAT_NAMES[cat] || cat;
  if (subEl) subEl.textContent = 'Loading…';
  if (lmBtn) lmBtn.style.display = 'none';

  const grid = document.getElementById('browseGrid');
  grid.innerHTML = skeletonHTML(LIMIT);

  try {
    const data = await apiBrowse(cat, LIMIT, 0);
    state.browseResults = data.data || [];
    state.browseHasMore = state.browseResults.length >= LIMIT;

    if (!state.browseResults.length) {
      grid.innerHTML = `<div class="no-results"><h3>No content found</h3><p>Try a different category.</p></div>`;
      if (subEl) subEl.textContent = '0 items';
      return;
    }

    renderCardsInto(grid, state.browseResults, true);
    if (subEl) subEl.textContent = `${state.browseResults.length} items`;
    if (lmBtn) lmBtn.style.display = state.browseHasMore ? 'inline-flex' : 'none';

  } catch (e) {
    grid.innerHTML = `<div class="no-results"><h3>Failed to load</h3><p>${e.message}</p></div>`;
    toast('Failed to load category', 'err');
  }
}

async function loadMoreBrowse() {
  const btn = document.getElementById('loadMoreBtn');
  if (!btn || btn.disabled) return;
  btn.disabled = true;
  btn.textContent = 'Loading…';

  state.browseSkip += LIMIT;
  try {
    const data = await apiBrowse(state.browseCategory, LIMIT, state.browseSkip);
    const newItems = data.data || [];
    state.browseHasMore = newItems.length >= LIMIT;
    state.browseResults.push(...newItems);

    const grid = document.getElementById('browseGrid');
    newItems.forEach((item, i) => {
      const div = document.createElement('div');
      div.innerHTML = cardHTML(item, state.browseResults.length - newItems.length + i);
      grid.appendChild(div.firstElementChild);
    });

    btn.disabled = false;
    btn.textContent = 'Load More';
    if (!state.browseHasMore) btn.style.display = 'none';
  } catch {
    btn.disabled = false;
    btn.textContent = 'Load More';
    toast('Failed to load more', 'err');
  }
}

function backToCategories() {
  document.getElementById('categoriesSection').style.display = 'block';
  document.getElementById('suggestSection').style.display = 'block';
  document.getElementById('browseResultsSection').style.display = 'none';
  state.browseCategory = '';
}

async function apiBrowse(cat, limit, skip) {
  const url = `${API_BASE}/api/browse?category=${cat}&limit=${limit}&skip=${skip}`;
  const r = await fetch(url, { headers: HEADERS, signal: AbortSignal.timeout(20000) });
  if (!r.ok) throw new Error(`Server error ${r.status}`);
  return r.json();
}

async function runSearch(reset = true) {
  const input = document.getElementById('searchInput');
  const query = input?.value.trim() || '';
  if (!query) { input?.focus(); return; }

  if (reset) {
    state.searchQuery = query;
    state.searchSkip = 0;
    state.searchResults = [];
  }

  const grid = document.getElementById('searchGrid');
  const bar = document.getElementById('resultsBar');
  const lmWrap = document.getElementById('searchLoadMore');
  const displayCount = document.getElementById('displayCount');
  const queryLabel = document.getElementById('queryLabel');
  const btn = document.getElementById('searchBtn');

  if (reset) {
    grid.innerHTML = skeletonHTML(LIMIT);
    if (bar) bar.style.display = 'none';
    if (lmWrap) lmWrap.style.display = 'none';
  }

  if (btn) { btn.classList.add('loading'); btn.disabled = true; }

  try {
    const r = await fetch(`${API_BASE}/api/search`, {
      method: 'POST',
      headers: HEADERS,
      body: JSON.stringify({ query, search_type: state.searchType, limit: LIMIT }),
      signal: AbortSignal.timeout(25000)
    });
    if (!r.ok) throw new Error(`HTTP ${r.status}`);
    const data = await r.json();
    const items = data.data || [];

    if (reset) state.searchResults = items;
    else state.searchResults.push(...items);

    state.searchHasMore = items.length >= LIMIT;

    if (!state.searchResults.length) {
      grid.innerHTML = `<div class="no-results"><h3>No results for "${query}"</h3><p>Try different keywords or a different filter.</p></div>`;
      if (bar) bar.style.display = 'none';
      return;
    }

    if (reset) renderCardsInto(grid, state.searchResults, true);
    else {
      items.forEach((item, i) => {
        const div = document.createElement('div');
        div.innerHTML = cardHTML(item, state.searchResults.length - items.length + i);
        grid.appendChild(div.firstElementChild);
      });
    }

    const total = state.searchResults.length;
    if (displayCount) displayCount.textContent = total;
    if (queryLabel) queryLabel.textContent = query;
    if (bar) bar.style.display = 'flex';
    if (lmWrap) lmWrap.style.display = state.searchHasMore ? 'block' : 'none';

  } catch (e) {
    if (reset) grid.innerHTML = `<div class="no-results"><h3>Search failed</h3><p>Make sure the API is running.</p></div>`;
    toast('Search failed — check API', 'err');
  } finally {
    if (btn) { btn.classList.remove('loading'); btn.disabled = false; }
  }
}

async function loadMoreSearch() {
  const lmBtn = document.getElementById('searchLoadMoreBtn');
  if (!lmBtn || lmBtn.disabled) return;
  lmBtn.disabled = true;
  lmBtn.textContent = 'Loading…';
  state.searchSkip += LIMIT;
  await runSearch(false);
  lmBtn.disabled = false;
  lmBtn.textContent = 'Load More Results';
  if (!state.searchHasMore) document.getElementById('searchLoadMore').style.display = 'none';
}

function clearSearch() {
  const input = document.getElementById('searchInput');
  const grid = document.getElementById('searchGrid');
  const bar = document.getElementById('resultsBar');
  const lmWrap = document.getElementById('searchLoadMore');
  const clearBtn = document.getElementById('clearBtn');
  if (input) { input.value = ''; input.focus(); }
  if (clearBtn) clearBtn.style.display = 'none';
  if (bar) bar.style.display = 'none';
  if (lmWrap) lmWrap.style.display = 'none';
  state.searchResults = [];
  grid.innerHTML = `
    <div class="empty-state" id="searchEmpty">
      <div class="empty-icon">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><circle cx="11" cy="11" r="8"/><path d="m21 21-4.35-4.35"/></svg>
      </div>
      <h3>Find anything</h3>
      <p>Search across thousands of free Minecraft items</p>
      <div class="chips-row chips-center mt-lg">
        <button class="chip sm" onclick="quickSearch('Actions & Stuff')">⚡ Actions &amp; Stuff</button>
        <button class="chip sm" onclick="quickSearch('Better on Bedrock')">💎 Better on Bedrock</button>
        <button class="chip sm" onclick="quickSearch('Realistic Biomes')">🌿 Realistic Biomes</button>
      </div>
    </div>
  `;
}

function quickSearch(term) {
  switchView('search');
  const input = document.getElementById('searchInput');
  if (input) {
    input.value = term;
    document.getElementById('clearBtn').style.display = 'flex';
  }
  runSearch(true);
}

function triggerHeroSearch() {
  const val = document.getElementById('heroInput')?.value.trim();
  if (!val) return;
  switchView('search');
  const input = document.getElementById('searchInput');
  if (input) {
    input.value = val;
    document.getElementById('clearBtn').style.display = 'flex';
  }
  runSearch(true);
}

function getType(item) {
  const t = (item.Tags || []).join(' ').toLowerCase();
  if (t.includes('skin') || t.includes('persona')) return 'Skin Pack';
  if (t.includes('texture') || t.includes('resource')) return 'Texture Pack';
  if (t.includes('addon') || t.includes('behavior')) return 'Add-On';
  if (t.includes('world') || t.includes('map')) return 'World';
  if (t.includes('mashup')) return 'Mashup';
  return 'Content';
}

function getThumb(images) {
  if (!images?.length) return null;
  return (images.find(i => i.Tag === 'Thumbnail') || images[0])?.Url || null;
}

const BLANK = `data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='320' height='180'%3E%3Crect width='100%25' height='100%25' fill='%230c1319'/%3E%3Ctext x='50%25' y='50%25' font-family='monospace' font-size='11' fill='%23475569' text-anchor='middle' dominant-baseline='middle'%3ENo Preview%3C/text%3E%3C/svg%3E`;

function esc(s) { return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;').replace(/'/g,'&#39;'); }

function cardHTML(item, idx = 0) {
  const title = item.Title?.['en-US'] || 'Unknown Title';
  const creator = item.DisplayProperties?.creatorName || 'Unknown';
  const thumb = getThumb(item.Images);
  const id = item.Id;
  const type = getType(item);
  const delay = Math.min(idx * 35, 350);
  const safeId = esc(id);
  const safeTitle = esc(title);

  return `
<div class="card" style="animation-delay:${delay}ms">
  <div class="card-img">
    <img src="${thumb || BLANK}" alt="${safeTitle}" loading="lazy" onerror="this.src='${BLANK}'">
    <div class="card-img-overlay"></div>
    <span class="type-badge">${type}</span>
  </div>
  <div class="card-body">
    <div class="card-title" title="${safeTitle}">${title}</div>
    <div class="card-creator"><span class="creator-dot"></span>${esc(creator)}</div>
    <div class="card-foot">
      <button class="dl-btn" id="btn-${safeId}" onclick="download('${safeId}','${safeTitle.replace(/'/g,"\\'")}')">
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="7 10 12 15 17 10"/><line x1="12" y1="15" x2="12" y2="3"/></svg>
        Download
      </button>
    </div>
    <div id="ds-${safeId}" class="dl-status" style="display:none"></div>
  </div>
</div>`;
}

function renderCardsInto(grid, items, clear = true) {
  if (clear) grid.innerHTML = '';
  items.forEach((item, i) => {
    const d = document.createElement('div');
    d.innerHTML = cardHTML(item, i);
    grid.appendChild(d.firstElementChild);
  });
}

function skeletonHTML(n = 8) {
  return Array.from({ length: n }, () => `
<div class="skel">
  <div class="skel-img"></div>
  <div class="skel-body">
    <div class="skel-line w75"></div>
    <div class="skel-line w50"></div>
    <div class="skel-line w40"></div>
  </div>
  <div class="skel-foot"><div class="skel-btn"></div></div>
</div>`).join('');
}

async function download(id, title) {
  const btn = document.getElementById(`btn-${id}`);
  const ds = document.getElementById(`ds-${id}`);
  if (!btn || btn.disabled) return;

  const setDS = (html, cls) => {
    if (!ds) return;
    ds.style.display = 'block';
    ds.className = `dl-status ${cls}`;
    ds.innerHTML = html;
  };
  const reset = () => {
    btn.disabled = false;
    btn.className = 'dl-btn';
    btn.innerHTML = `<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="7 10 12 15 17 10"/><line x1="12" y1="15" x2="12" y2="3"/></svg> Download`;
    if (ds) { ds.style.display = 'none'; ds.innerHTML = ''; }
  };

  btn.disabled = true;
  btn.className = 'dl-btn st-loading';
  btn.innerHTML = `<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M21 12a9 9 0 1 1-6.219-8.56"/></svg> Preparing…`;
  setDS('Connecting to server…', 'st-info');

  try {
    const r = await fetch(`${API_BASE}/api/download`, {
      method: 'POST',
      headers: HEADERS,
      body: JSON.stringify({ item_id: id, process_content: true }),
      signal: AbortSignal.timeout(120000)
    });

    if (!r.ok) throw new Error(`Server error ${r.status}`);

    btn.innerHTML = `<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M21 12a9 9 0 1 1-6.219-8.56"/></svg> Processing…`;
    setDS('Processing content, please wait…', 'st-prog');

    const blob = await r.blob();
    const url = URL.createObjectURL(blob);
    const a = Object.assign(document.createElement('a'), { href: url, download: `${id}.zip` });
    document.body.appendChild(a);
    a.click();
    URL.revokeObjectURL(url);
    a.remove();

    btn.className = 'dl-btn st-done';
    btn.innerHTML = `<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><polyline points="20 6 9 17 4 12"/></svg> Done!`;
    setDS('Download complete! Check your Downloads folder.', 'st-ok');
    toast(`"${title}" downloaded!`, 'ok');
    setTimeout(reset, 4500);

  } catch (e) {
    btn.className = 'dl-btn st-err';
    btn.innerHTML = `<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><circle cx="12" cy="12" r="10"/><line x1="12" y1="8" x2="12" y2="12"/><line x1="12" y1="16" x2="12.01" y2="16"/></svg> Failed`;
    setDS('Download failed. Try again.', 'st-fail');
    toast('Download failed — try again', 'err');
    setTimeout(reset, 4000);
  }
}

document.addEventListener('DOMContentLoaded', async () => {
  await checkHealth();

  const heroInput = document.getElementById('heroInput');
  if (heroInput) {
    heroInput.addEventListener('keydown', e => {
      if (e.key === 'Enter') triggerHeroSearch();
    });
  }

  const searchInput = document.getElementById('searchInput');
  const clearBtn = document.getElementById('clearBtn');

  if (searchInput) {
    searchInput.addEventListener('input', () => {
      const has = searchInput.value.trim().length > 0;
      if (clearBtn) clearBtn.style.display = has ? 'flex' : 'none';
      clearTimeout(state.searchDebounce);
      if (has) {
        state.searchDebounce = setTimeout(() => runSearch(true), 500);
      }
    });

    searchInput.addEventListener('keydown', e => {
      if (e.key === 'Enter') {
        clearTimeout(state.searchDebounce);
        runSearch(true);
      }
    });
  }

  if (clearBtn) {
    clearBtn.addEventListener('click', clearSearch);
  }

  document.getElementById('searchBtn')?.addEventListener('click', () => {
    clearTimeout(state.searchDebounce);
    runSearch(true);
  });

  document.querySelectorAll('.fchip').forEach(chip => {
    chip.addEventListener('click', () => {
      document.querySelectorAll('.fchip').forEach(c => c.classList.remove('active'));
      chip.classList.add('active');
      state.searchType = chip.dataset.type;
      if (state.searchQuery || document.getElementById('searchInput')?.value.trim()) {
        runSearch(true);
      }
    });
  });
});
