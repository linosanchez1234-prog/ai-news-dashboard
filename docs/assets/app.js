/* ===== AI News Dashboard — Client-side App ===== */
(function () {
  'use strict';

  // --- Constants ---
  const SCHEMA_VERSION = 2;
  const STORAGE_PREFIX = `ainews_v${SCHEMA_VERSION}_`;
  const DEFAULT_SHOW = 30;
  const STALE_HOURS = 36;
  const DATA_URL = 'data/latest.json';

  // --- State ---
  let allArticles = [];
  let filteredArticles = [];
  let currentTab = 'all';
  let activeChips = new Set();
  let focusIndex = -1;
  let showCount = DEFAULT_SHOW;
  let schemaVersion = null;

  // --- DOM refs ---
  const $ = (sel) => document.querySelector(sel);
  const $$ = (sel) => document.querySelectorAll(sel);

  const searchInput = $('#searchInput');
  const lastUpdatedEl = $('#lastUpdated');
  const staleBanner = $('#staleBanner');
  const articleList = $('#articleList');
  const loadingState = $('#loadingState');
  const emptyState = $('#emptyState');
  const emptyTitle = $('#emptyTitle');
  const emptyMessage = $('#emptyMessage');
  const clearFiltersBtn = $('#clearFiltersBtn');
  const showMoreWrap = $('#showMoreWrap');
  const showMoreBtn = $('#showMoreBtn');
  const sortSelect = $('#sortSelect');
  const unreadToggle = $('#unreadToggle');
  const markAllReadBtn = $('#markAllRead');
  const articleCountEl = $('#articleCount');
  const themeToggle = $('#themeToggle');
  const primaryTabs = $('#primaryTabs');
  const productChips = $('#productChips');
  const topicChips = $('#topicChips');
  const discoverySection = $('#discoverySection');
  const spotlightWrap = $('#spotlightWrap');
  const discoveryGrid = $('#discoveryGrid');
  const randomPickBtn = $('#randomPickBtn');

  // --- LocalStorage Helpers ---
  function lsGet(key, fallback) {
    try {
      const v = localStorage.getItem(STORAGE_PREFIX + key);
      return v ? JSON.parse(v) : fallback;
    } catch { return fallback; }
  }
  function lsSet(key, val) {
    try { localStorage.setItem(STORAGE_PREFIX + key, JSON.stringify(val)); } catch {}
  }
  function migrateStorage(newVersion) {
    const theme = lsGet('theme', null);
    // Clear all ainews keys
    Object.keys(localStorage).forEach(k => {
      if (k.startsWith('ainews_')) localStorage.removeItem(k);
    });
    // Restore theme
    if (theme) lsSet('theme', theme);
  }

  let readSet = new Set(lsGet('read', []));
  let savedSet = new Set(lsGet('saved', []));

  function saveReadState() { lsSet('read', [...readSet]); }
  function saveSavedState() { lsSet('saved', [...savedSet]); }

  // --- Theme ---
  function initTheme() {
    const saved = lsGet('theme', null);
    const theme = saved || (window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light');
    document.body.setAttribute('data-theme', theme);
  }
  themeToggle.addEventListener('click', () => {
    const current = document.body.getAttribute('data-theme');
    const next = current === 'dark' ? 'light' : 'dark';
    document.body.setAttribute('data-theme', next);
    lsSet('theme', next);
  });

  // --- Data Loading ---
  async function loadData() {
    try {
      const res = await fetch(DATA_URL);
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = await res.json();

      // Schema version check
      schemaVersion = data.schema_version || 1;
      if (schemaVersion !== SCHEMA_VERSION) {
        migrateStorage(schemaVersion);
        readSet = new Set();
        savedSet = new Set();
      }

      allArticles = data.articles || [];
      updateLastUpdated(data.last_updated);
      buildChips();
      applyFilters();
      loadingState.style.display = 'none';
    } catch (err) {
      loadingState.innerHTML = `<p style="color:var(--trending-text)">Failed to load articles. ${err.message}</p>`;
    }
  }

  function updateLastUpdated(ts) {
    if (!ts) return;
    const d = new Date(ts);
    const now = new Date();
    const hoursAgo = (now - d) / 3600000;
    const fmt = new Intl.RelativeTimeFormat('en', { numeric: 'auto' });

    if (hoursAgo < 1) lastUpdatedEl.textContent = `Updated: just now`;
    else if (hoursAgo < 24) lastUpdatedEl.textContent = `Updated: ${Math.round(hoursAgo)}h ago`;
    else lastUpdatedEl.textContent = `Updated: ${Math.round(hoursAgo / 24)}d ago`;

    staleBanner.style.display = hoursAgo > STALE_HOURS ? 'block' : 'none';
  }

  // --- Build filter chips dynamically ---
  function buildChips() {
    const platformCounts = {};
    const topicCounts = {};

    allArticles.forEach(a => {
      (a.categories || []).forEach(c => {
        if (['Research', 'Industry', 'Open Source'].includes(c)) {
          topicCounts[c] = (topicCounts[c] || 0) + 1;
        } else if (c !== 'Discovery') {
          platformCounts[c] = (platformCounts[c] || 0) + 1;
        }
      });
    });

    productChips.innerHTML = '';
    Object.keys(platformCounts).sort().forEach(name => {
      const btn = document.createElement('button');
      btn.className = 'chip';
      btn.dataset.filter = name;
      btn.textContent = `${name} (${platformCounts[name]})`;
      btn.addEventListener('click', () => toggleChip(btn));
      productChips.appendChild(btn);
    });

    topicChips.innerHTML = '';
    ['Research', 'Industry', 'Open Source'].forEach(name => {
      if (!topicCounts[name]) return;
      const btn = document.createElement('button');
      btn.className = 'chip';
      btn.dataset.filter = name;
      btn.textContent = `${name} (${topicCounts[name]})`;
      btn.addEventListener('click', () => toggleChip(btn));
      topicChips.appendChild(btn);
    });
  }

  function toggleChip(btn) {
    const filter = btn.dataset.filter;
    if (activeChips.has(filter)) {
      activeChips.delete(filter);
      btn.classList.remove('active');
    } else {
      activeChips.add(filter);
      btn.classList.add('active');
    }
    showCount = DEFAULT_SHOW;
    applyFilters();
  }

  // --- Tabs ---
  primaryTabs.addEventListener('click', (e) => {
    const btn = e.target.closest('.tab-btn');
    if (!btn) return;
    $$('.tab-btn').forEach(b => b.classList.remove('active'));
    btn.classList.add('active');
    currentTab = btn.dataset.tab;
    activeChips.clear();
    $$('.chip').forEach(c => c.classList.remove('active'));
    showCount = DEFAULT_SHOW;

    productChips.style.display = currentTab === 'products' ? 'flex' : 'none';
    topicChips.style.display = currentTab === 'topics' ? 'flex' : 'none';

    applyFilters();
  });

  // --- Filtering & Sorting ---
  function applyFilters() {
    const query = searchInput.value.toLowerCase().trim();
    const unreadOnly = unreadToggle.checked;
    const sort = sortSelect.value;

    let articles = [...allArticles];

    // Tab filter
    if (currentTab === 'discovery') {
      articles = articles.filter(a => (a.categories || []).includes('Discovery'));
    } else if (currentTab === 'saved') {
      articles = articles.filter(a => savedSet.has(a.id));
    } else if (currentTab === 'products' || currentTab === 'topics') {
      if (activeChips.size > 0) {
        articles = articles.filter(a =>
          (a.categories || []).some(c => activeChips.has(c))
        );
      }
    }

    // Search
    if (query) {
      articles = articles.filter(a =>
        (a.title || '').toLowerCase().includes(query) ||
        (a.summary || '').toLowerCase().includes(query) ||
        (a.source || '').toLowerCase().includes(query) ||
        (a.categories || []).some(c => c.toLowerCase().includes(query))
      );
    }

    // Unread only
    if (unreadOnly) {
      articles = articles.filter(a => !readSet.has(a.id));
    }

    // Sort
    if (sort === 'recent') {
      articles.sort((a, b) => new Date(b.published) - new Date(a.published));
    } else if (sort === 'authority') {
      articles.sort((a, b) => (b.source_authority || 0) - (a.source_authority || 0));
    } else {
      articles.sort((a, b) => (b.score || 0) - (a.score || 0));
    }

    filteredArticles = articles;
    focusIndex = -1;

    if (currentTab === 'discovery') {
      renderDiscovery(articles);
    } else {
      renderArticles(articles);
    }
  }

  // --- Render Articles ---
  function renderArticles(articles) {
    discoverySection.style.display = 'none';
    articleList.style.display = 'block';

    if (articles.length === 0) {
      articleList.innerHTML = '';
      emptyState.style.display = 'block';
      emptyTitle.textContent = searchInput.value ? 'No articles match your search' : 'No articles found';
      emptyMessage.textContent = searchInput.value ? 'Try different keywords.' : 'Check back later for fresh articles.';
      clearFiltersBtn.style.display = (activeChips.size > 0 || searchInput.value) ? 'inline-block' : 'none';
      showMoreWrap.style.display = 'none';
      articleCountEl.textContent = '';
      return;
    }

    emptyState.style.display = 'none';
    const visible = articles.slice(0, showCount);
    articleCountEl.textContent = `${visible.length} of ${articles.length}`;

    articleList.innerHTML = visible.map((a, i) => cardHTML(a, i)).join('');
    showMoreWrap.style.display = articles.length > showCount ? 'block' : 'none';

    // Attach events
    articleList.querySelectorAll('.article-card').forEach((el, i) => {
      el.addEventListener('click', (e) => {
        if (e.target.closest('.card-actions')) return;
        toggleExpand(i);
      });
    });

    articleList.querySelectorAll('.btn-save').forEach(btn => {
      btn.addEventListener('click', (e) => {
        e.stopPropagation();
        const id = btn.dataset.id;
        if (savedSet.has(id)) { savedSet.delete(id); btn.classList.remove('saved'); btn.textContent = '\u2606'; }
        else { savedSet.add(id); btn.classList.add('saved'); btn.textContent = '\u2605'; }
        saveSavedState();
      });
    });

    articleList.querySelectorAll('.btn-open').forEach(btn => {
      btn.addEventListener('click', (e) => e.stopPropagation());
    });
  }

  function cardHTML(article, index) {
    const isRead = readSet.has(article.id);
    const isSaved = savedSet.has(article.id);
    const timeAgo = getTimeAgo(article.published);
    const tierClass = article.tier === 'trending' ? 'tier-trending' : article.tier === 'notable' ? 'tier-notable' : '';
    const tierLabel = article.tier === 'trending' ? 'Trending' : article.tier === 'notable' ? 'Notable' : '';
    const cats = (article.categories || []).map(c => `<span class="tag">#${c.toLowerCase().replace(/\s/g,'-')}</span>`).join(' ');
    const covered = (article.also_covered_by || []).length > 0
      ? `<div class="also-covered">Also covered by: ${article.also_covered_by.join(', ')}</div>` : '';

    return `
      <div class="article-card${isRead ? ' read' : ''}" data-index="${index}" data-id="${article.id}">
        <div class="card-header">
          <span class="source-badge">${escText(article.source)}</span>
          <span class="card-title">${escText(article.title)}</span>
        </div>
        <div class="card-meta">
          <span>${timeAgo}</span>
          ${tierLabel ? `<span class="tier-badge ${tierClass}">${tierLabel}</span>` : ''}
          ${cats}
          <span class="card-actions">
            <button class="btn-save${isSaved ? ' saved' : ''}" data-id="${article.id}" title="Save">${isSaved ? '\u2605' : '\u2606'}</button>
            <a class="btn-open" href="${escAttr(article.url)}" target="_blank" rel="noopener noreferrer" title="Open">&#8599;</a>
          </span>
        </div>
        <div class="card-expanded" style="display:none;">
          <p>${escText(article.summary || 'No summary available.')}</p>
          ${covered}
        </div>
      </div>`;
  }

  function toggleExpand(index) {
    const cards = articleList.querySelectorAll('.article-card');
    cards.forEach((c, i) => {
      const expanded = c.querySelector('.card-expanded');
      if (i === index) {
        const isOpen = expanded.style.display !== 'none';
        expanded.style.display = isOpen ? 'none' : 'block';
        if (!isOpen) {
          const id = c.dataset.id;
          readSet.add(id);
          saveReadState();
          c.classList.add('read');
        }
      } else {
        expanded.style.display = 'none';
      }
    });
    focusIndex = index;
    updateFocus();
  }

  // --- Discovery Rendering ---
  function renderDiscovery(articles) {
    articleList.style.display = 'none';
    discoverySection.style.display = 'block';
    showMoreWrap.style.display = 'none';
    emptyState.style.display = articles.length === 0 ? 'block' : 'none';

    if (articles.length === 0) {
      spotlightWrap.innerHTML = '';
      discoveryGrid.innerHTML = '';
      return;
    }

    // Spotlight: highest scored discovery article
    const spotlight = articles[0];
    const isNew = !readSet.has(spotlight.id);
    spotlightWrap.innerHTML = `
      <div class="spotlight-card article-card" data-id="${spotlight.id}">
        <div class="card-header">
          <span class="source-badge">${escText(spotlight.source)}</span>
          <span class="card-title">${escText(spotlight.title)}${isNew ? '<span class="new-badge">NEW</span>' : ''}</span>
        </div>
        <p style="margin-top:8px;font-size:0.85rem;color:var(--text-muted)">${escText(spotlight.summary || '')}</p>
        <div class="card-meta" style="margin-top:8px;">
          <span>${getTimeAgo(spotlight.published)}</span>
          <a class="btn-open" href="${escAttr(spotlight.url)}" target="_blank" rel="noopener noreferrer">Open article &#8599;</a>
        </div>
      </div>`;

    // Grid: rest
    discoveryGrid.innerHTML = articles.slice(1, 13).map(a => cardHTML(a, 0)).join('');

    articleCountEl.textContent = `${articles.length} discoveries`;
  }

  // --- Keyboard Navigation ---
  document.addEventListener('keydown', (e) => {
    if (e.target.tagName === 'INPUT' || e.target.tagName === 'SELECT') return;

    const cards = articleList.querySelectorAll('.article-card');
    if (cards.length === 0) return;

    if (e.key === 'j' || e.key === 'J') {
      e.preventDefault();
      focusIndex = Math.min(focusIndex + 1, cards.length - 1);
      updateFocus();
    } else if (e.key === 'k' || e.key === 'K') {
      e.preventDefault();
      focusIndex = Math.max(focusIndex - 1, 0);
      updateFocus();
    } else if (e.key === 'Enter' && focusIndex >= 0) {
      e.preventDefault();
      toggleExpand(focusIndex);
    } else if (e.key === 'Escape') {
      cards.forEach(c => c.querySelector('.card-expanded').style.display = 'none');
    } else if ((e.key === 'r' || e.key === 'R') && focusIndex >= 0) {
      e.preventDefault();
      const id = cards[focusIndex].dataset.id;
      readSet.add(id);
      saveReadState();
      cards[focusIndex].classList.add('read');
    } else if ((e.key === 's' || e.key === 'S') && focusIndex >= 0) {
      e.preventDefault();
      const id = cards[focusIndex].dataset.id;
      const btn = cards[focusIndex].querySelector('.btn-save');
      if (savedSet.has(id)) { savedSet.delete(id); btn.classList.remove('saved'); btn.textContent = '\u2606'; }
      else { savedSet.add(id); btn.classList.add('saved'); btn.textContent = '\u2605'; }
      saveSavedState();
    }
  });

  function updateFocus() {
    const cards = articleList.querySelectorAll('.article-card');
    cards.forEach((c, i) => c.classList.toggle('focused', i === focusIndex));
    if (focusIndex >= 0 && cards[focusIndex]) {
      cards[focusIndex].scrollIntoView({ block: 'nearest', behavior: 'smooth' });
    }
  }

  // --- Event Listeners ---
  searchInput.addEventListener('input', () => { showCount = DEFAULT_SHOW; applyFilters(); });
  sortSelect.addEventListener('change', applyFilters);
  unreadToggle.addEventListener('change', applyFilters);
  showMoreBtn.addEventListener('click', () => { showCount += DEFAULT_SHOW; renderArticles(filteredArticles); });
  markAllReadBtn.addEventListener('click', () => {
    filteredArticles.forEach(a => readSet.add(a.id));
    saveReadState();
    applyFilters();
  });
  clearFiltersBtn.addEventListener('click', () => {
    searchInput.value = '';
    activeChips.clear();
    $$('.chip').forEach(c => c.classList.remove('active'));
    applyFilters();
  });
  randomPickBtn.addEventListener('click', () => {
    const disc = allArticles.filter(a => (a.categories || []).includes('Discovery'));
    if (disc.length === 0) return;
    const pick = disc[Math.floor(Math.random() * disc.length)];
    spotlightWrap.querySelector('.spotlight-card').outerHTML = `
      <div class="spotlight-card article-card" data-id="${pick.id}">
        <div class="card-header">
          <span class="source-badge">${escText(pick.source)}</span>
          <span class="card-title">${escText(pick.title)}</span>
        </div>
        <p style="margin-top:8px;font-size:0.85rem;color:var(--text-muted)">${escText(pick.summary || '')}</p>
        <div class="card-meta" style="margin-top:8px;">
          <span>${getTimeAgo(pick.published)}</span>
          <a class="btn-open" href="${escAttr(pick.url)}" target="_blank" rel="noopener noreferrer">Open article &#8599;</a>
        </div>
      </div>`;
  });

  // --- Utilities ---
  function getTimeAgo(dateStr) {
    if (!dateStr) return '';
    const diff = (Date.now() - new Date(dateStr).getTime()) / 1000;
    if (diff < 3600) return `${Math.round(diff / 60)}m ago`;
    if (diff < 86400) return `${Math.round(diff / 3600)}h ago`;
    return `${Math.round(diff / 86400)}d ago`;
  }

  function escText(str) {
    const el = document.createElement('span');
    el.textContent = str || '';
    return el.innerHTML;
  }

  function escAttr(str) {
    return (str || '').replace(/&/g, '&amp;').replace(/"/g, '&quot;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
  }

  // --- Init ---
  initTheme();
  loadData();

})();
