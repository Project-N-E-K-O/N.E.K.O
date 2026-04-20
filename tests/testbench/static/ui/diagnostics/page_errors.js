/**
 * page_errors.js — Diagnostics → Errors 子页 (P19 正式版).
 *
 * 替换 P04 的临时 `workspace_diagnostics.js` 面板. 数据源从纯前端
 * `store.errors` 升级为"前端 errors_bus + 后端 diagnostics_store 合并视图":
 *
 *   - 后端 `/api/diagnostics/errors` 返回进程级 ring buffer (200 条, 重启
 *     清空), 里面有 HTTP 500 middleware 捕获的异常 + 前端通过 POST 回传的
 *     运行时错误. 两边合并后 dedupe — 后端条目以 `synthetic_id` 反查匹配,
 *     前端独有的条目 (例如刚抛出还没回传) 保留本地展示.
 *   - 布局: 顶部 toolbar (计数 / source 过滤 / level 过滤 / search / 清空 /
 *     制造测试) + 分页 CollapsibleBlock 列表.
 *   - CollapsibleBlock 折叠态显示 `时间 · 来源徽章 · 类型 · 摘要`, 展开态
 *     打印完整 JSON (含 trace_digest + detail).
 *
 * 同族踩点预防:
 *   - §3A B1 "改 state 后必须 renderAll": 所有 onChange/onClick 最后一行
 *     `renderAll(root, state)`, 或 `reload().then(renderAll)`.
 *   - §3A C3 "append null" 防御: 可选子节点用 `cond ? el(...) : null`
 *     配 `el()` helper 的 null-filter, 或 `filter(Boolean)`.
 *   - §3A B7 跨 workspace hint: 本子页挂载时如果 `ui_prefs.diagnostics_errors_filter`
 *     有值, 就合并到 state.filter (供日后从 Results 错误徽章跳 Errors 用).
 */

import { api } from '../../core/api.js';
import { i18n, i18nRaw } from '../../core/i18n.js';
import { store, on, set as setStore } from '../../core/state.js';
import { toast } from '../../core/toast.js';
import { el } from '../_dom.js';

const LS_FILTER_KEY = 'testbench:diagnostics:errors:filter:v1';
const POLL_INTERVAL_MS = 5000;

function loadPersistedFilter() {
  try {
    const raw = localStorage.getItem(LS_FILTER_KEY);
    if (!raw) return {};
    const parsed = JSON.parse(raw);
    return typeof parsed === 'object' && parsed ? parsed : {};
  } catch {
    return {};
  }
}

function persistFilter(filter) {
  try {
    localStorage.setItem(LS_FILTER_KEY, JSON.stringify(filter || {}));
  } catch { /* quota exceeded: ignore */ }
}

function defaultState() {
  return {
    loading: false,
    filter: loadPersistedFilter(),
    items: [],
    total: 0,
    matched: 0,
    offset: 0,
    pageSize: 50,
    autoRefresh: true,
    // 错误自身: 加载失败 (例如后端 500) 仍要渲染一个空态提示.
    loadError: null,
    // P20 hotfix 3: key=entryKey(entry) → bool(展开与否). 用户显式
    // 点击折叠/展开后永久尊重此意图, 5s 自动刷新重建 DOM 不再回
    // 弹默认值. 未在此 map 的 entry 走 defaultOpenFor(level):
    // ERROR 默认展开, INFO/warning 其它折叠. 与 page_logs.js 同族
    // 设计 (§4.24 #81). 切 filter 时清掉 (新筛选集合里旧 key 失效).
    toggledKeys: new Map(),
  };
}

function filterToQs(state) {
  const usp = new URLSearchParams();
  const f = state.filter || {};
  for (const k of ['source', 'level', 'session_id', 'search']) {
    if (f[k] != null && String(f[k]).trim() !== '') {
      usp.set(k, String(f[k]).trim());
    }
  }
  usp.set('limit', String(state.pageSize));
  usp.set('offset', String(state.offset));
  return `?${usp.toString()}`;
}

async function loadErrors(state) {
  state.loading = true;
  const qs = filterToQs(state);
  const resp = await api.get(`/api/diagnostics/errors${qs}`, { expectedStatuses: [404] });
  state.loading = false;
  if (resp.ok) {
    state.items = resp.data?.items || [];
    state.total = resp.data?.total || 0;
    state.matched = resp.data?.matched || 0;
    state.loadError = null;
  } else {
    state.items = [];
    state.total = 0;
    state.matched = 0;
    state.loadError = resp.error?.message || `HTTP ${resp.status}`;
  }
}

//
// ── main ─────────────────────────────────────────────────────────
//

export async function renderErrorsPage(host) {
  // Teardown any previous handlers / polls from a prior mount.
  for (const k of ['__offErrorsChange', '__pollTimer']) {
    const v = host[k];
    if (k === '__offErrorsChange' && typeof v === 'function') {
      try { v(); } catch { /* ignore */ }
    } else if (k === '__pollTimer' && v != null) {
      clearInterval(v);
    }
    host[k] = null;
  }

  host.innerHTML = '';
  const root = el('div', { className: 'diag-errors' });
  host.append(root);

  const state = defaultState();
  await loadErrors(state);
  renderAll(root, state);

  // 前端本地 errors_bus 有新事件 → 立即刷新一次. 后端同步是异步的 ~100ms
  // 级别延迟, 所以也起个 5s 轮询兜底, 防止同步慢/失败时视图过期.
  host.__offErrorsChange = on('errors:change', () => {
    loadErrors(state).then(() => renderAll(root, state));
  });
  host.__pollTimer = setInterval(() => {
    if (!state.autoRefresh) return;
    loadErrors(state).then(() => renderAll(root, state));
  }, POLL_INTERVAL_MS);
}

//
// ── render ───────────────────────────────────────────────────────
//

function renderAll(root, state) {
  root.innerHTML = '';
  const chips = renderFilterChips(root, state);
  const pager = renderPager(root, state);
  root.append(renderHeader(), renderToolbar(root, state));
  if (chips) root.append(chips);
  if (state.loading && state.items.length === 0) {
    root.append(el('div', { className: 'empty-state' },
      i18n('diagnostics.errors.loading')));
    return;
  }
  if (state.loadError) {
    root.append(el('div', { className: 'empty-state' },
      i18n('diagnostics.errors.load_failed', state.loadError)));
    return;
  }
  if (state.matched === 0 && state.total === 0) {
    root.append(el('div', { className: 'empty-state' },
      i18n('diagnostics.errors.empty')));
    return;
  }
  if (state.matched === 0 && state.total > 0) {
    root.append(el('div', { className: 'empty-state' },
      i18n('diagnostics.errors.empty_filtered', state.total)));
    return;
  }
  root.append(renderList(state));
  if (pager) root.append(pager);
}

function renderHeader() {
  return el('div', { className: 'diag-page-header' },
    el('h2', {}, i18n('diagnostics.errors.heading')),
    el('p', { className: 'diag-page-intro' },
      i18n('diagnostics.errors.intro')),
  );
}

function renderToolbar(root, state) {
  const f = state.filter;
  const sources = ['', 'middleware', 'http', 'sse', 'js', 'promise', 'resource', 'pipeline', 'synthetic'];
  const levels  = ['', 'error', 'warning', 'info', 'fatal'];

  const sourceSel = el('select', {
    className: 'tiny',
    onChange: (e) => {
      f.source = e.target.value || undefined;
      state.offset = 0;
      // 新筛选集可能包含/排除不同 entry, 旧 toggledKeys 意图对新集合无参考.
      state.toggledKeys?.clear();
      persistFilter(f);
      loadErrors(state).then(() => renderAll(root, state));
    },
  }, ...sources.map((s) => {
    const opt = el('option', { value: s },
      s ? i18n(`diagnostics.errors.source_labels.${s}`) || s : i18n('diagnostics.errors.all_sources'));
    if ((f.source || '') === s) opt.selected = true;
    return opt;
  }));

  const levelSel = el('select', {
    className: 'tiny',
    onChange: (e) => {
      f.level = e.target.value || undefined;
      state.offset = 0;
      state.toggledKeys?.clear();
      persistFilter(f);
      loadErrors(state).then(() => renderAll(root, state));
    },
  }, ...levels.map((l) => {
    const opt = el('option', { value: l },
      l ? i18n(`diagnostics.errors.level_labels.${l}`) || l : i18n('diagnostics.errors.all_levels'));
    if ((f.level || '') === l) opt.selected = true;
    return opt;
  }));

  const searchBox = el('input', {
    type: 'search',
    className: 'tiny',
    placeholder: i18n('diagnostics.errors.search_placeholder'),
    value: f.search || '',
    onInput: (e) => {
      f.search = e.target.value || undefined;
      state.offset = 0;
      persistFilter(f);
      // 防抖: 300ms 内连续输入合并. 用 _searchDebounce 字段在 state 上存 timer id.
      if (state._searchDebounce) clearTimeout(state._searchDebounce);
      state._searchDebounce = setTimeout(() => {
        state._searchDebounce = null;
        loadErrors(state).then(() => renderAll(root, state));
      }, 300);
    },
  });

  const autoChk = el('input', {
    type: 'checkbox',
    checked: state.autoRefresh,
    onChange: (e) => { state.autoRefresh = e.target.checked; },
  });
  const autoLabel = el('label', { className: 'diag-checkbox-label' },
    autoChk,
    el('span', {}, i18n('diagnostics.errors.auto_refresh')));

  const refreshBtn = el('button', {
    className: 'ghost tiny',
    onClick: () => { loadErrors(state).then(() => renderAll(root, state)); },
  }, i18n('diagnostics.errors.refresh'));

  const synthBtn = el('button', {
    className: 'ghost tiny',
    onClick: async () => {
      // 直接走前端 errors_bus (本地 + 后端双写), 用户一键验证全链路.
      const { recordError } = await import('../../core/errors_bus.js');
      recordError({
        source: 'synthetic',
        type: 'SyntheticTestError',
        message: i18n('diagnostics.errors.synth_msg'),
        level: 'error',
        detail: { triggered_by: 'diagnostics_ui', at_local: new Date().toString() },
      });
      toast.info(i18n('diagnostics.errors.trigger_test_done'));
      setTimeout(() => {
        loadErrors(state).then(() => renderAll(root, state));
      }, 200);
    },
  }, i18n('diagnostics.errors.trigger_test'));

  const clearBtn = el('button', {
    className: 'danger tiny',
    onClick: async () => {
      if (!window.confirm(i18n('diagnostics.errors.clear_confirm'))) return;
      const resp = await api.delete('/api/diagnostics/errors');
      if (resp.ok) {
        toast.ok(i18n('diagnostics.errors.cleared', resp.data?.removed ?? 0));
        // 本地 store.errors 也清一下, 避免 count 虚高. 不触发 http:error.
        setStore('errors', []);
        state.offset = 0;
        state.toggledKeys?.clear();
        await loadErrors(state);
        renderAll(root, state);
      } else {
        toast.err(i18n('diagnostics.errors.clear_failed'));
      }
    },
  }, i18n('diagnostics.errors.clear'));

  return el('div', { className: 'diag-toolbar' },
    el('div', { className: 'diag-toolbar-left' },
      el('span', { className: 'diag-count' },
        i18n('diagnostics.errors.count_fmt', state.matched, state.total)),
    ),
    el('div', { className: 'diag-toolbar-right' },
      sourceSel, levelSel, searchBox, autoLabel, refreshBtn, synthBtn, clearBtn,
    ),
  );
}

function renderFilterChips(root, state) {
  const f = state.filter || {};
  const active = [];
  if (f.source) active.push(['source', f.source]);
  if (f.level)  active.push(['level', f.level]);
  if (f.session_id) active.push(['session_id', f.session_id]);
  if (f.search) active.push(['search', f.search]);
  if (!active.length) return null;
  const wrap = el('div', { className: 'diag-filter-chips' });
  for (const [k, v] of active) {
    wrap.append(el('span', { className: 'badge subtle' }, `${k}: ${v}`));
  }
  wrap.append(el('button', {
    className: 'ghost tiny',
    onClick: () => {
      state.filter = {};
      state.offset = 0;
      state.toggledKeys?.clear();
      persistFilter({});
      loadErrors(state).then(() => renderAll(root, state));
    },
  }, i18n('diagnostics.errors.clear_filter')));
  return wrap;
}

function renderList(state) {
  const wrap = el('div', { className: 'diag-error-list' });
  for (const entry of state.items) {
    wrap.append(renderEntry(entry, state));
  }
  return wrap;
}

// Stable key for an Errors entry across re-renders. Entries have a
// backend-assigned `id` when synced, and a frontend `id` (nextId) when
// local-only. Prefer `id`; fall back to a content hash for pure-frontend
// entries. This is what `state.toggledKeys` keys on — changing this
// invalidates all user-remembered expansion state on the next tick.
function entryKey(entry) {
  if (entry.id) return String(entry.id);
  return [
    String(entry.at || ''),
    String(entry.source || ''),
    String(entry.type || ''),
    String(entry.message || '').slice(0, 40),
    String(entry.status || ''),
    String(entry.url || ''),
  ].join('|');
}

// Default open state when the user has NOT explicitly toggled this
// entry yet. ERROR / WARNING 默认展开 (用户来看就是想排查细节);
// info/debug 折叠 (量大, 通常只看标题就够). 和 page_logs.js::
// defaultOpenFor 的"WARN/ERROR 自动展开"保持一致, 避免 Errors 页
// 和 Logs 页在同样一条 WARN 条目上给出不同的"默认态"让用户困惑.
//
// 注意: 这是 default. 用户的显式点击会通过 toggledKeys 覆盖此默认,
// 所以无论什么 level 的 entry, 用户展开 / 折叠 一次后 auto-refresh
// 都会尊重意图. "警告/调试级别也不会自动折叠" 靠的是 toggledKeys
// 的 level-agnostic 设计, 不是靠 default.
function defaultOpenForEntry(entry) {
  const lv = (entry.level || 'error').toLowerCase();
  return lv === 'error' || lv === 'err'
    || lv === 'warning' || lv === 'warn';
}

function renderEntry(entry, state) {
  const key = entryKey(entry);
  const toggled = state?.toggledKeys?.get(key);
  const initialOpen = typeof toggled === 'boolean'
    ? toggled
    : defaultOpenForEntry(entry);

  const cb = el('div', {
    className: 'cb',
    'data-open': String(initialOpen),
    'data-entry-key': key,
  });
  const header = el('div', { className: 'cb-header' });

  const caret = el('span', { className: 'cb-caret' }, '▸');
  const ts = el('span', { className: 'cb-title' }, formatTimestamp(entry.at));
  const sourceBadge = buildSourceBadge(entry);
  const levelBadge  = buildLevelBadge(entry);
  const typeSpan = el('span', {
    className: 'mono diag-entry-type',
  }, entry.type || '-');
  const preview = el('span', { className: 'cb-preview' }, shortMessage(entry));

  header.append(caret, ts, sourceBadge, levelBadge, typeSpan, preview);
  header.addEventListener('click', () => {
    const open = cb.getAttribute('data-open') === 'true';
    const next = !open;
    cb.setAttribute('data-open', String(next));
    // Persist user intent across 5s auto-refresh. Must store both
    // true AND false — otherwise an ERROR entry (default open) that
    // the user folds reverts to expanded on the next refresh.
    if (state && state.toggledKeys) {
      state.toggledKeys.set(key, next);
    }
  });

  const body = el('div', { className: 'cb-body diag-entry-body' });
  body.append(renderEntryMeta(entry));
  if (entry.trace_digest) {
    body.append(el('details', { className: 'diag-entry-trace', open: false },
      el('summary', {}, i18n('diagnostics.errors.trace_digest_label')),
      el('pre', { className: 'mono' }, entry.trace_digest),
    ));
  }
  const detailKeys = entry.detail ? Object.keys(entry.detail) : [];
  if (detailKeys.length) {
    body.append(el('details', { className: 'diag-entry-detail', open: false },
      el('summary', {}, i18n('diagnostics.errors.detail_label')),
      el('pre', { className: 'mono' }, safeStringify(entry.detail)),
    ));
  }

  cb.append(header, body);
  return cb;
}

function renderEntryMeta(entry) {
  const rows = [];
  const push = (key, value) => {
    if (value == null || value === '') return;
    rows.push(el('div', { className: 'diag-meta-row' },
      el('span', { className: 'diag-meta-key' }, key),
      el('span', { className: 'diag-meta-val mono' }, String(value)),
    ));
  };
  push('id',         entry.id);
  push('source',     entry.source);
  push('level',      entry.level);
  push('type',       entry.type);
  push('message',    entry.message);
  push('method',     entry.method);
  push('url',        entry.url);
  push('status',     entry.status);
  push('session_id', entry.session_id);
  push('user_agent', entry.user_agent);
  return el('div', { className: 'diag-entry-meta' }, ...rows);
}

function renderPager(root, state) {
  if (state.matched <= state.pageSize) return null;
  const page = Math.floor(state.offset / state.pageSize) + 1;
  const pageCount = Math.max(1, Math.ceil(state.matched / state.pageSize));
  const prev = el('button', {
    className: 'ghost tiny',
    disabled: page <= 1,
    onClick: () => {
      state.offset = Math.max(0, state.offset - state.pageSize);
      loadErrors(state).then(() => renderAll(root, state));
    },
  }, i18n('diagnostics.errors.pager_prev'));
  const next = el('button', {
    className: 'ghost tiny',
    disabled: page >= pageCount,
    onClick: () => {
      state.offset = Math.min((pageCount - 1) * state.pageSize, state.offset + state.pageSize);
      loadErrors(state).then(() => renderAll(root, state));
    },
  }, i18n('diagnostics.errors.pager_next'));
  return el('div', { className: 'diag-pager' },
    prev,
    el('span', { className: 'diag-pager-info' },
      i18n('diagnostics.errors.pager_fmt', page, pageCount)),
    next,
  );
}

//
// ── helpers ─────────────────────────────────────────────────────
//

function formatTimestamp(iso) {
  if (!iso) return '—';
  try {
    return new Date(iso).toISOString().replace('T', ' ').slice(0, 19);
  } catch {
    return iso;
  }
}

function sourceLabel(source) {
  const labels = i18nRaw('diagnostics.errors.source_labels') || {};
  return labels[source] || source || '—';
}

function levelLabel(level) {
  const labels = i18nRaw('diagnostics.errors.level_labels') || {};
  return labels[level] || level || '—';
}

function buildSourceBadge(entry) {
  const cls = [
    'badge',
    'diag-badge-source',
    `diag-source-${(entry.source || 'unknown').replace(/[^a-z0-9_]/gi, '_')}`,
  ].join(' ');
  const text = entry.status
    ? `${sourceLabel(entry.source)} ${entry.status}`
    : sourceLabel(entry.source);
  return el('span', { className: cls }, text);
}

function buildLevelBadge(entry) {
  const cls = [
    'badge',
    'diag-badge-level',
    `diag-level-${(entry.level || 'error').replace(/[^a-z0-9_]/gi, '_')}`,
  ].join(' ');
  return el('span', { className: cls }, levelLabel(entry.level));
}

function shortMessage(entry) {
  let raw = entry.message ?? entry.type ?? '';
  if (typeof raw !== 'string') {
    try { raw = JSON.stringify(raw); } catch { raw = String(raw); }
  }
  if (raw.length <= 200) return raw;
  return raw.slice(0, 200) + '…';
}

function safeStringify(obj) {
  try {
    return JSON.stringify(obj, null, 2);
  } catch {
    return String(obj);
  }
}
