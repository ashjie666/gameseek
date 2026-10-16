/* ==========================================================================
   新游雷达 - 前端渲染与筛选
   数据来源: web/data/news.json (由 collector 导出)
   ========================================================================== */

'use strict';

const CATEGORY_LABELS = {
  release: '新游发售',
  announce: '新作公布',
  delay: '延期跳票',
  update: '版本更新',
  review: '评测前瞻',
  sale: '促销折扣',
  rumor: '传闻爆料',
  other: '其他',
};

const CATEGORY_ORDER = ['release', 'announce', 'delay', 'update', 'review', 'sale', 'rumor', 'other'];
const PLATFORM_ORDER = ['PC', 'PS5', 'Xbox', 'Switch 2', 'Switch', 'Mobile', 'VR', 'Mac', 'PS4'];
const DAY_OPTIONS = [
  { value: 1, label: '今天' },
  { value: 3, label: '近 3 天' },
  { value: 7, label: '近 7 天' },
  { value: 30, label: '近 30 天' },
  { value: 0, label: '全部' },
];
const LANG_OPTIONS = [
  { value: 'all', label: '全部' },
  { value: 'zh', label: '中文' },
  { value: 'en', label: '英文' },
];
const SORT_OPTIONS = [
  { value: 'new', label: '最新' },
  { value: 'heat', label: '热度' },
];

const ICON_HEAT = '<svg viewBox="0 0 24 24" fill="currentColor" aria-hidden="true"><path d="M13 2 4 14h6l-1 8 9-12h-6l1-8z"/></svg>';
const ICON_CHEVRON = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M9 6l6 6-6 6"/></svg>';

const state = {
  days: 7,
  platform: 'all',
  category: 'all',
  lang: 'all',
  sort: 'new',
  onlyNew: false,
  query: '',
  expanded: new Set(),
};

let DATA = null;

/* -------------------------------------------------------------------------
   DOM helpers
   ------------------------------------------------------------------------- */

function el(tag, props, children) {
  const node = document.createElement(tag);
  const settings = props || {};
  for (const key of Object.keys(settings)) {
    const value = settings[key];
    if (value === null || value === undefined || value === false) continue;
    if (key === 'class') node.className = value;
    else if (key === 'text') node.textContent = value;
    else if (key === 'icon') node.innerHTML = value;
    else if (key === 'dataset') Object.assign(node.dataset, value);
    else if (key.startsWith('on')) node.addEventListener(key.slice(2).toLowerCase(), value);
    else node.setAttribute(key, value === true ? '' : value);
  }
  const list = children === undefined ? [] : [].concat(children);
  for (const child of list) {
    if (child === null || child === undefined || child === false) continue;
    node.append(child instanceof Node ? child : document.createTextNode(String(child)));
  }
  return node;
}

const $ = (id) => document.getElementById(id);

/* -------------------------------------------------------------------------
   Time formatting
   ------------------------------------------------------------------------- */

const pad = (value) => String(value).padStart(2, '0');

function parseMoment(iso) {
  if (!iso) return NaN;
  const value = Date.parse(iso);
  return Number.isNaN(value) ? NaN : value;
}

function formatDate(iso) {
  const time = parseMoment(iso);
  if (Number.isNaN(time)) return '';
  const date = new Date(time);
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}`;
}

function formatStamp(iso) {
  const time = parseMoment(iso);
  if (Number.isNaN(time)) return '未知';
  const date = new Date(time);
  return `${formatDate(iso)} ${pad(date.getHours())}:${pad(date.getMinutes())}`;
}

function relativeTime(iso) {
  const time = parseMoment(iso);
  if (Number.isNaN(time)) return '';
  const seconds = (Date.now() - time) / 1000;
  if (seconds < 60) return '刚刚';
  if (seconds < 3600) return `${Math.floor(seconds / 60)} 分钟前`;
  if (seconds < 86400) return `${Math.floor(seconds / 3600)} 小时前`;
  if (seconds < 86400 * 7) return `${Math.floor(seconds / 86400)} 天前`;
  return formatDate(iso);
}

function startOfToday() {
  const now = new Date();
  return new Date(now.getFullYear(), now.getMonth(), now.getDate()).getTime();
}

function releaseLabel(cluster) {
  const raw = cluster.release_date || '';
  if (/^\d{4}-\d{2}-\d{2}$/.test(raw)) return raw;
  if (/^\d{4}-\d{2}$/.test(raw)) return `${raw.slice(0, 4)} 年 ${Number(raw.slice(5, 7))} 月`;
  if (/^\d{4}-Q\d$/.test(raw)) return `${raw.slice(0, 4)} 年 Q${raw.slice(6)}`;
  return cluster.release_text || '';
}

/* -------------------------------------------------------------------------
   Filtering
   ------------------------------------------------------------------------- */

function cutoffTime() {
  if (!state.days) return 0;
  if (state.days === 1) return startOfToday();
  return Date.now() - state.days * 86400000;
}

function matchesQuery(cluster, query) {
  if (!query) return true;
  const parts = [cluster.game_name, cluster.title, cluster.developer, cluster.publisher];
  for (const item of cluster.items) parts.push(item.source_name, item.title);
  return parts.join(' ').toLowerCase().includes(query);
}

function visibleClusters() {
  if (!DATA) return [];
  const cutoff = cutoffTime();
  const query = state.query.trim().toLowerCase();

  const rows = DATA.clusters.filter((cluster) => {
    const moment = parseMoment(cluster.last_seen || cluster.first_seen);
    if (cutoff && !Number.isNaN(moment) && moment < cutoff) return false;
    if (state.platform !== 'all' && !(cluster.platforms || []).includes(state.platform)) return false;
    if (state.category !== 'all' && cluster.category !== state.category) return false;
    if (state.onlyNew && !cluster.is_new_release) return false;
    if (state.lang !== 'all') {
      const langs = new Set(cluster.items.map((item) => item.lang));
      if (!langs.has(state.lang)) return false;
    }
    return matchesQuery(cluster, query);
  });

  rows.sort((left, right) => {
    if (state.sort === 'heat' && right.heat !== left.heat) return right.heat - left.heat;
    const leftMoment = parseMoment(left.last_seen) || 0;
    const rightMoment = parseMoment(right.last_seen) || 0;
    return rightMoment - leftMoment;
  });
  return rows;
}

/* -------------------------------------------------------------------------
   Filter chrome
   ------------------------------------------------------------------------- */

function chip(label, active, onClick, count) {
  const children = [label];
  if (count !== undefined) children.push(el('span', { class: 'chip-count', text: count }));
  return el('button', {
    class: 'chip',
    type: 'button',
    'aria-pressed': String(active),
    onClick,
  }, children);
}

function renderFilterGroup(container, options, current, onPick, counts) {
  container.replaceChildren();
  for (const option of options) {
    const count = counts ? counts[option.value] : undefined;
    container.append(chip(option.label, current === option.value, () => onPick(option.value), count));
  }
}

function categoryCounts() {
  const counts = { all: DATA.clusters.length };
  for (const cluster of DATA.clusters) {
    counts[cluster.category] = (counts[cluster.category] || 0) + 1;
  }
  return counts;
}

function platformCounts() {
  const counts = { all: DATA.clusters.length };
  for (const cluster of DATA.clusters) {
    for (const platform of cluster.platforms || []) {
      counts[platform] = (counts[platform] || 0) + 1;
    }
  }
  return counts;
}

function renderFilters() {
  renderFilterGroup($('filter-days'), DAY_OPTIONS, state.days, (value) => {
    state.days = value;
    renderFilters();
    render();
  });
  renderFilterGroup($('filter-sort'), SORT_OPTIONS, state.sort, (value) => {
    state.sort = value;
    renderFilters();
    render();
  });

  const categoryOptions = CATEGORY_ORDER
    .filter((name) => name === 'other' || (DATA.stats.categories || {})[name])
    .map((name) => ({ value: name, label: CATEGORY_LABELS[name] }));
  renderFilterGroup($('filter-category'),
    [{ value: 'all', label: '全部' }].concat(categoryOptions),
    state.category,
    (value) => { state.category = value; renderFilters(); render(); },
    categoryCounts());

  const availablePlatforms = PLATFORM_ORDER.filter((name) => (DATA.stats.platforms || {})[name]);
  renderFilterGroup($('filter-platform'),
    [{ value: 'all', label: '全部' }].concat(availablePlatforms.map((name) => ({ value: name, label: name }))),
    state.platform,
    (value) => { state.platform = value; renderFilters(); render(); },
    platformCounts());

  renderFilterGroup($('filter-lang'), LANG_OPTIONS, state.lang, (value) => {
    state.lang = value;
    renderFilters();
    render();
  });

  $('only-new').checked = state.onlyNew;
}

/* -------------------------------------------------------------------------
   Stats panel
   ------------------------------------------------------------------------- */

function statBlock(label, value, note, accent) {
  const children = [
    el('div', { class: `stat-value${accent ? ' is-brand' : ''}`, text: value }),
    el('div', { class: 'stat-label', text: label }),
  ];
  if (note) children.push(el('div', { class: 'stat-note', text: note }));
  return el('div', { class: 'stat' }, children);
}

function renderStats() {
  const stats = DATA.stats;
  const grid = $('stat-grid');
  grid.replaceChildren(
    statBlock('今日新游', String(stats.today_clusters), '今天更新的情报条目', true),
    statBlock('去重后情报', String(stats.clusters), `原始条目 ${stats.items} 条`),
    statBlock('多源验证', String(stats.multi_source_clusters), '被 2 家以上媒体报道'),
    statBlock('新游相关', String(stats.new_release_clusters), '含发售日或平台信息'),
    statBlock('收录来源', `${stats.sources_ok}/${stats.sources_ok + stats.sources_failed}`,
      stats.sources_failed ? `${stats.sources_failed} 个源不可用` : '全部可用'),
  );

  const platforms = stats.platforms || {};
  const entries = Object.entries(platforms).sort((a, b) => b[1] - a[1]).slice(0, 8);
  const max = entries.length ? entries[0][1] : 1;
  const bars = $('platform-bars');
  bars.replaceChildren();
  for (const [name, count] of entries) {
    bars.append(el('div', { class: 'platform-row' }, [
      el('span', { class: 'platform-name', text: name }),
      el('span', { class: 'platform-track' }, [
        el('span', { class: 'platform-fill', style: `width:${Math.max(1, Math.round(count / max * 100))}%` }),
      ]),
      el('span', { class: 'platform-count', text: String(count) }),
    ]));
  }
  if (!entries.length) bars.append(el('div', { class: 'stat-note', text: '暂无平台分布数据' }));
}

/* -------------------------------------------------------------------------
   Cards
   ------------------------------------------------------------------------- */

function buildSourceList(cluster) {
  return cluster.items.map((item) => el('div', { class: 'source-row' }, [
    el('span', { class: 'source-name', title: item.source_name, text: item.source_name }),
    el('a', { class: 'source-link', href: item.url, target: '_blank', rel: 'noopener nofollow', text: item.title }),
    el('span', { class: 'source-time', text: relativeTime(item.published_at) }),
  ]));
}

function renderCard(cluster) {
  const accent = `var(--c-${cluster.category})`;
  const expanded = state.expanded.has(cluster.id);

  const head = el('div', { class: 'card-head' }, [
    el('span', { class: 'tag', text: CATEGORY_LABELS[cluster.category] || '其他' }),
    cluster.heat > 1
      ? el('span', { class: 'tag-heat', title: `${cluster.heat} 家媒体报道` }, [
          el('span', { class: 'heat-icon', icon: ICON_HEAT }),
          `${cluster.heat} 源`,
        ])
      : null,
    el('span', { class: 'card-time', text: relativeTime(cluster.last_seen) }),
  ]);

  const facts = [];
  for (const platform of cluster.platforms || []) {
    facts.push(el('span', { class: 'fact', text: platform }));
  }

  const label = releaseLabel(cluster);
  if (label) {
    const today = startOfToday();
    const moment = parseMoment(cluster.release_date);
    const released = /^\d{4}-\d{2}-\d{2}$/.test(cluster.release_date) && moment <= today;
    facts.push(el('span', {
      class: `fact fact-date${released ? ' is-live' : ''}`,
      text: `发售 ${label}`,
    }));
  }
  if (cluster.developer) facts.push(el('span', { class: 'fact', text: cluster.developer }));
  if (cluster.publisher) facts.push(el('span', { class: 'fact', text: cluster.publisher }));

  const toggle = el('button', {
    class: 'source-toggle',
    type: 'button',
    'aria-expanded': String(expanded),
    onClick: (event) => {
      const list = event.currentTarget.closest('.card').querySelector('.source-list');
      const next = list.hidden;
      list.hidden = !next;
      event.currentTarget.setAttribute('aria-expanded', String(next));
      if (next) state.expanded.add(cluster.id); else state.expanded.delete(cluster.id);
    },
  }, [el('span', { icon: ICON_CHEVRON }), `${cluster.item_count} 条报道`]);

  const firstDomain = cluster.items[0] && cluster.items[0].source_name;

  const foot = el('div', { class: 'card-foot' }, [
    toggle,
    el('span', { class: 'spacer' }),
    el('span', { text: firstDomain ? `首发 ${firstDomain}` : '' }),
  ]);

  const sourceList = el('div', { class: 'source-list', hidden: !expanded }, buildSourceList(cluster));

  return el('article', {
    class: 'card',
    style: `--accent:${accent}`,
  }, [
    head,
    el('h2', { class: 'game-name', text: cluster.game_name || cluster.title }),
    cluster.game_name && cluster.game_name !== cluster.title
      ? el('p', { class: 'card-lead', text: cluster.title })
      : null,
    facts.length ? el('div', { class: 'card-facts' }, facts) : null,
    foot,
    sourceList,
  ]);
}

/* -------------------------------------------------------------------------
   Render
   ------------------------------------------------------------------------- */

function renderEmpty(message, hint) {
  const panel = $('empty-state');
  panel.hidden = false;
  panel.replaceChildren(
    el('h2', { text: message }),
    hint ? el('p', { icon: hint }) : null,
  );
}

function render() {
  const rows = visibleClusters();
  const feed = $('feed');

  $('result-count').textContent = DATA
    ? `显示 ${rows.length} / ${DATA.clusters.length} 条`
    : '';

  if (!DATA) return;

  if (!rows.length) {
    feed.replaceChildren();
    renderEmpty('没有符合当前筛选条件的情报',
      '试试放宽时间范围、清除搜索词，或关闭「只看新游」。');
    return;
  }

  $('empty-state').hidden = true;
  feed.replaceChildren(...rows.map(renderCard));
}

/* -------------------------------------------------------------------------
   Theme
   ------------------------------------------------------------------------- */

function initTheme() {
  const stored = localStorage.getItem('gamefeed-theme');
  document.documentElement.dataset.theme = stored === 'light' ? 'light' : 'dark';
  $('theme-toggle').addEventListener('click', () => {
    const next = document.documentElement.dataset.theme === 'dark' ? 'light' : 'dark';
    document.documentElement.dataset.theme = next;
    localStorage.setItem('gamefeed-theme', next);
  });
}

/* -------------------------------------------------------------------------
   Boot
   ------------------------------------------------------------------------- */

function renderSourceHealth() {
  const stats = DATA.stats;
  const badge = $('health-badge');
  const age = Date.now() - (parseMoment(stats.generated_at) || 0);
  const stale = age > 86400 * 1000;

  if (stale) {
    badge.hidden = false;
    badge.className = 'chip chip-warn';
    badge.textContent = '数据超过 24 小时未更新';
  } else {
    badge.hidden = true;
    badge.className = 'chip chip-quiet';
  }

  const failed = DATA.sources.filter((source) => source.status !== 'ok');
  $('footer-meta').textContent =
    `采集窗口 ${stats.window_days} 天 · 抽取引擎 ${stats.extractor} · 更新于 ${formatStamp(stats.generated_at)}`
    + (failed.length ? ` · 不可用源 ${failed.length} 个` : '');
}

async function boot() {
  initTheme();

  $('only-new').addEventListener('change', (event) => {
    state.onlyNew = event.target.checked;
    render();
  });

  let timer = 0;
  $('search').addEventListener('input', (event) => {
    clearTimeout(timer);
    const value = event.target.value;
    timer = setTimeout(() => { state.query = value; render(); }, 140);
  });

  $('reset').addEventListener('click', () => {
    state.days = 7;
    state.platform = 'all';
    state.category = 'all';
    state.lang = 'all';
    state.sort = 'new';
    state.onlyNew = false;
    state.query = '';
    state.expanded.clear();
    $('search').value = '';
    renderFilters();
    render();
  });

  try {
    const response = await fetch('data/news.json', { cache: 'no-cache' });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    DATA = await response.json();
  } catch (error) {
    $('generated-at').textContent = '载入失败';
    renderEmpty('还没有数据',
      `先在项目根目录运行采集器：<code>python -m collector.main</code>，`
      + '然后用本地服务器打开本页：<code>python -m http.server 8321 --directory web</code>。'
      + '（直接双击打开 index.html 会因浏览器安全策略无法读取 news.json）');
    return;
  }

  $('generated-at').textContent = `更新于 ${relativeTime(DATA.stats.generated_at)}`;
  renderSourceHealth();
  renderStats();
  renderFilters();
  render();
}

boot();
