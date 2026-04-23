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
import { formatIsoReadable } from '../../core/time_utils.js';
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
    // P25 Day 2 hotfix (2026-04-23): sub-<details> 展开态持久化.
    // renderEntry() 里的 "trace_digest" + "detail" 两个 nested <details>
    // 之前是 naked ``open: false``, 5s auto-refresh rebuild 整棵子树,
    // 用户刚点开的 Error detail 就被收回. 参考 page_logs.js 同族 fix.
    // key 格式 ``${entryKey}|trace_digest`` / ``${entryKey}|detail``,
    // 父 entry key 隔离冲突. filter / 分页 等 "entry 集合换了" 场景
    // 调 clearEntryCaches() 一并清, 防 Set 无界 (L11).
    openSubDetails: new Set(),
  };
}

// P25 Day 2 helper: 与 page_logs.js::clearEntryCaches 对齐. 集合换了
// 的场景 (filter/search/分页/重置) 统一调它, 保证两个 cache 同步清.
function clearEntryCaches(state) {
  state.toggledKeys?.clear();
  state.openSubDetails?.clear();
}

function filterToQs(state) {
  const usp = new URLSearchParams();
  const f = state.filter || {};
  for (const k of ['source', 'level', 'session_id', 'search', 'op_type']) {
    if (f[k] != null && String(f[k]).trim() !== '') {
      usp.set(k, String(f[k]).trim());
    }
  }
  // P25 hotfix 2026-04-23: ``include_info`` default-false flag so that
  // level=info entries (e.g. avatar_interaction_simulated audit replays
  // from P25 external_events._record_and_return) don't pollute the
  // Errors page's "recent problems" default view. Only emit the param
  // when the user has actually checked it — keeps URLs clean and
  // backend default matches frontend default. If the user also
  // selected an explicit level in the dropdown, the backend will
  // honor level= and ignore include_info (see diagnostics_store
  // docstring), so sending both is safe.
  if (f.include_info === true) {
    usp.set('include_info', 'true');
  }
  usp.set('limit', String(state.pageSize));
  usp.set('offset', String(state.offset));
  return `?${usp.toString()}`;
}

// P24 §15.2 D / F7 Option B — security-relevant internal ops. Kept
// in sync with pipeline/diagnostics_ops.py::DiagnosticsOp. Order here
// mirrors user scan priority: integrity first (data corruption),
// judge override second (prompt injection vector), prompt_injection
// third (direct attack surface), timestamp_coerced last (soft warning).
// 2026-04-22 Day 8 手测 #6: 加 prompt_injection_suspected 让 Chat 发送
// 的注入命中也能被 Security filter 聚合到.
const SECURITY_OPS = [
  'integrity_check',
  'judge_extra_context_override',
  'prompt_injection_suspected',
  'timestamp_coerced',
];

// P24 §14.4 M3: per-page AbortController so rapid Refresh / filter clicks
// don't race — the prev fetch is aborted before the new one starts so the
// "last click wins" (not "last response wins"). url varies with filter/qs,
// so we can't use `makeCancellableGet(url)` which binds a fixed url.
let _errorsLoadController = null;

async function loadErrors(state) {
  if (_errorsLoadController) {
    try { _errorsLoadController.abort(); } catch { /* ignore */ }
  }
  const controller = new AbortController();
  _errorsLoadController = controller;

  state.loading = true;
  const qs = filterToQs(state);
  const resp = await api.get(`/api/diagnostics/errors${qs}`, {
    expectedStatuses: [404],
    signal: controller.signal,
  });
  // Abort 的结果 (type:'aborted') 直接忽略 — 下一次 loadErrors 已接管 state.
  if (resp.error?.type === 'aborted') return;
  if (_errorsLoadController === controller) _errorsLoadController = null;

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
      clearEntryCaches(state);
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
      clearEntryCaches(state);
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

  // P25 hotfix 2026-04-23: include_info toggle. Default off = hide
  // info-level entries (audit-replay noise). L14 "coerce must
  // surface" — the checkbox state IS the surface of "I'm currently
  // hiding info level". Tooltip explains what's hidden / why, plus
  // the interaction with the level dropdown (explicit level= wins).
  const infoChk = el('input', {
    type: 'checkbox',
    checked: f.include_info === true,
    onChange: (e) => {
      f.include_info = e.target.checked ? true : undefined;
      state.offset = 0;
      clearEntryCaches(state);
      persistFilter(f);
      loadErrors(state).then(() => renderAll(root, state));
    },
  });
  const infoLabel = el('label', {
    className: 'diag-checkbox-label',
    title: i18n('diagnostics.errors.include_info_tooltip'),
  },
    infoChk,
    el('span', {}, i18n('diagnostics.errors.include_info_label')));

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
        clearEntryCaches(state);
        await loadErrors(state);
        renderAll(root, state);
      } else {
        toast.err(i18n('diagnostics.errors.clear_failed'));
      }
    },
  }, i18n('diagnostics.errors.clear'));

  // P24 §15.2 D / F7 Option B: Security op quick-filter chips. Each
  // chip sets op_type to a single op; clicking the same active chip
  // clears it. "All 3" convenience sets op_type to all SECURITY_OPS
  // joined by comma — the backend accepts comma-separated lists.
  const secRow = renderSecurityFilters(root, state);

  return el('div', { className: 'diag-toolbar' },
    el('div', { className: 'diag-toolbar-left' },
      el('span', { className: 'diag-count' },
        i18n('diagnostics.errors.count_fmt', state.matched, state.total)),
    ),
    el('div', { className: 'diag-toolbar-right' },
      sourceSel, levelSel, searchBox, infoLabel, autoLabel, refreshBtn, synthBtn, clearBtn,
    ),
    secRow,
  );
}

function renderSecurityFilters(root, state) {
  const f = state.filter;
  const current = (f.op_type || '').trim();
  const row = el('div', { className: 'diag-security-filter-row' });
  row.append(el('span', { className: 'diag-security-filter-label' },
    i18n('diagnostics.errors.security_filter_label')));

  function chip(opName, labelKey) {
    const active = current === opName;
    return el('button', {
      className: 'chip tiny' + (active ? ' active primary' : ''),
      title: i18n(`diagnostics.errors.security_filter_hint.${opName}`),
      onClick: () => {
        f.op_type = active ? undefined : opName;
        state.offset = 0;
        clearEntryCaches(state);
        persistFilter(f);
        loadErrors(state).then(() => renderAll(root, state));
      },
    }, i18n(labelKey));
  }
  row.append(
    chip('integrity_check', 'diagnostics.errors.security_filter.integrity_check'),
    chip('judge_extra_context_override', 'diagnostics.errors.security_filter.judge_override'),
    chip('prompt_injection_suspected', 'diagnostics.errors.security_filter.prompt_injection'),
    chip('timestamp_coerced', 'diagnostics.errors.security_filter.timestamp_coerced'),
  );

  const allJoined = SECURITY_OPS.join(',');
  const allActive = current === allJoined;
  row.append(el('button', {
    className: 'chip tiny' + (allActive ? ' active primary' : ''),
    title: i18n('diagnostics.errors.security_filter_hint.all'),
    onClick: () => {
      f.op_type = allActive ? undefined : allJoined;
      state.offset = 0;
      clearEntryCaches(state);
      persistFilter(f);
      loadErrors(state).then(() => renderAll(root, state));
    },
  }, i18n('diagnostics.errors.security_filter.all')));

  return row;
}

function renderFilterChips(root, state) {
  const f = state.filter || {};
  const active = [];
  if (f.source) active.push(['source', f.source]);
  if (f.level)  active.push(['level', f.level]);
  if (f.session_id) active.push(['session_id', f.session_id]);
  if (f.search) active.push(['search', f.search]);
  if (f.op_type) active.push(['op_type', f.op_type]);
  // include_info=true is "departure from default" so surface it as a
  // chip too. Default (undefined/false) = "hide info" is not shown
  // because it's the baseline, and the checkbox itself is the surface.
  if (f.include_info === true) active.push(['include_info', 'true']);
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
      clearEntryCaches(state);
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
  // P25 Day 2 hotfix (2026-04-23): "trace_digest" 与 "detail" 两个 sub
  // <details> 需要展开态持久化, 避免 5s auto-refresh rebuild 时收回用户
  // 手动展开的节点. 同族修 page_logs.js::buildStickyDetails. 下面这个
  // helper 是 page_errors 页专属的 mirror — 不 hoist 到 _dom.js 的理由
  // 见 page_logs.js::buildStickyDetails 注释 (state-shape 耦合).
  if (entry.trace_digest) {
    body.append(buildStickyDetails(
      state,
      `${key}|trace_digest`,
      i18n('diagnostics.errors.trace_digest_label'),
      el('pre', { className: 'mono' }, entry.trace_digest),
      { extraClass: 'diag-entry-trace' },
    ));
  }
  const detailKeys = entry.detail ? Object.keys(entry.detail) : [];
  if (detailKeys.length) {
    body.append(buildStickyDetails(
      state,
      `${key}|detail`,
      i18n('diagnostics.errors.detail_label'),
      el('pre', { className: 'mono' }, safeStringify(entry.detail)),
      { extraClass: 'diag-entry-detail' },
    ));
  }

  cb.append(header, body);
  return cb;
}

/**
 * Persist sub-<details> open state across re-renders.
 *
 * Mirrors page_logs.js::buildStickyDetails (see that file for rationale).
 * Keeping it local to page_errors.js keeps the state-shape coupling
 * tight — ``openSubDetails`` is a page-level state field, not a cross-
 * page primitive.
 */
function buildStickyDetails(state, subKey, summaryText, contentNode, { extraClass = '' } = {}) {
  const set = state.openSubDetails;
  const initialOpen = set?.has(subKey) === true;
  const details = el('details', {
    className: extraClass,
    open: initialOpen,
  },
    el('summary', {}, summaryText),
    contentNode,
  );
  details.addEventListener('toggle', () => {
    if (!set) return;
    if (details.open) set.add(subKey);
    else set.delete(subKey);
  });
  return details;
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
  // P25 Day 2 hotfix (2026-04-23): same -8h UTC drift as page_logs.js.
  // Backend emits naive local ISO strings (no TZ suffix); the old
  // ``toISOString()`` forcibly converted them to UTC and sliced the
  // wrong hours/minutes out. :func:`formatIsoReadable` uses
  // ``getHours()`` / ``getMinutes()`` / ``getSeconds()`` which resolve
  // against the browser's local timezone — identical wall-clock to the
  // tester's expectation.
  return formatIsoReadable(iso);
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
