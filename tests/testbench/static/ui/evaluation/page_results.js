/**
 * page_results.js — Evaluation → Results 子页 (P17).
 *
 * 这是"历史评分结果"的消费界面, 对应 PLAN §P17 的两块之一:
 *
 *   Run 子页 (P16) ── 创建 EvalResult → 落到 session.eval_results
 *                                          ↓
 *   Results 子页 (P17)  ─── 过滤 / 详情 / 批量 / 导出
 *
 * 与 Run 页的 "本次运行结果" 段的关键差别:
 *   - Run 只展示本次 POST 返回的那几条, 切页就丢
 *   - Results 拉的是 GET /judge/results, 读的是 session.eval_results 全量,
 *     刷新后仍在 (跨 session 不保留, 属于 "当前会话内持久").
 *
 * 布局 (单列, 非 two-col — drawer 覆盖时才出现右侧板):
 *   1. Header (标题 + intro)
 *   2. Filter bar (横向多控件, 自动触发重拉; 顶部 sticky)
 *   3. Toolbar row (选中计数 + 批量操作 + 导出 + 刷新)
 *   4. Table (可选择 / 排序 / 展开详情)
 *   5. Drawer (右侧滑入, 详情多个 collapsible 区块)
 *
 * 过滤状态持久化:
 *   - localStorage 键 `testbench:evaluation:results:filter:v1`
 *   - 跨 session 保留, 方便反复做同一组过滤调研
 *   - 但 "选中行" 不持久 (跨会话语义失真)
 *
 * 与 Chat 的联动 (由 P17 chat 内联徽章驱动, 本页响应):
 *   - store.ui_prefs.evaluation_results_filter 可能被别处写入
 *     (例如: 在 Chat 点某条消息的评分徽章 → 设置 filter.message_id →
 *     切到 Evaluation → Results 子页). 本页首次 mount 时读取一次,
 *     若存在 override 则合入本页的过滤状态并清掉 override, 避免
 *     下次再进入时还受污染.
 */

import { api } from '../../core/api.js';
import { i18n } from '../../core/i18n.js';
import { store, on, set as setStore } from '../../core/state.js';
import { toast } from '../../core/toast.js';
import { createCollapsible, mountContainerToolbar } from '../../core/collapsible.js';
import { el, field } from '../_dom.js';

const LS_FILTER_KEY = 'testbench:evaluation:results:filter:v1';
const PAGE_SIZE_DEFAULT = 25;

//
// ── state ─────────────────────────────────────────────────────────
//

function defaultFilter() {
  return {
    schema_id: '',
    mode: '',              // '' | 'absolute' | 'comparative'
    granularity: '',       // '' | 'single' | 'conversation'
    scope: '',             // '' | 'messages' | 'conversation'
    verdict: '',
    judge_model: '',
    passed: '',            // '' | 'true' | 'false'
    errored: '',           // '' | 'true' | 'false'
    message_id: '',
    min_overall: '',
    max_overall: '',
    min_gap: '',
    max_gap: '',
    query: '',
  };
}

function loadPersistedFilter() {
  try {
    const raw = localStorage.getItem(LS_FILTER_KEY);
    if (!raw) return defaultFilter();
    const parsed = JSON.parse(raw);
    // Merge with default so added keys in newer builds don't throw.
    return { ...defaultFilter(), ...(parsed || {}) };
  } catch {
    return defaultFilter();
  }
}

function persistFilter(f) {
  try {
    localStorage.setItem(LS_FILTER_KEY, JSON.stringify(f));
  } catch { /* quota / private mode — ignore */ }
}

function defaultState() {
  return {
    sessionId: null,
    loading: false,
    exporting: false,
    // Server data
    schemasList: [],
    results: [],
    total: 0,
    aggregateVerdicts: {},  // for verdict dropdown options
    aggregateJudgeModels: [],
    // Filter + pagination
    filter: loadPersistedFilter(),
    offset: 0,
    limit: PAGE_SIZE_DEFAULT,
    // Selection (Set of result ids)
    selected: new Set(),
    // Drawer: currently open result id; result body is fetched from
    // state.results (all fields are embedded already).
    openResultId: null,
  };
}

//
// ── main entry ────────────────────────────────────────────────────
//

export async function renderResultsPage(host) {
  // Tear down old listeners from previous mount (same pattern as page_run).
  for (const k of ['__offSession', '__offChatMsgs', '__offJudgeResults']) {
    if (typeof host[k] === 'function') {
      try { host[k](); } catch { /* ignore */ }
      host[k] = null;
    }
  }

  host.innerHTML = '';
  const root = el('div', { className: 'eval-results' });
  host.append(root);

  const state = defaultState();
  state.sessionId = store.session?.id || null;

  // Consume any cross-workspace navigation hint (from the chat badge
  // jump-in). The hint format is {filter: {...}}; we merge and clear.
  //
  // 设计背景 (#77, #78 lessons): 本页不主动订阅 ui_prefs:change —— 早期版本
  // (hotfix 4) 这样做, 但实战中 "接收方订阅" 受子页挂载/拆除生命周期影响,
  // 在 warm-same-subpage 等栈帧交错场景下不稳定. 现在 hint 的 "触发 remount"
  // 责任下沉到 workspace_evaluation 的导航 handler, 那里无论 currentId 是否
  // 等于 target 都会 selectPage → renderResultsPage 被重新调用 → 下面这段
  // applyHintFromStore 才是 hint 的唯一消费点. 单一职责, 单一调用链.
  const applyHintFromStore = () => {
    const hint = store.ui_prefs?.evaluation_results_filter;
    if (!hint || typeof hint !== 'object') return false;
    state.filter = { ...state.filter, ...hint };
    state.offset = 0;
    persistFilter(state.filter);
    // Clear immediately so a later revisit doesn't re-apply the same filter
    // (e.g. 用户接着手动改筛选后 remount, 不能被上次的旧 hint 覆盖掉).
    setStore('ui_prefs', {
      ...(store.ui_prefs || {}),
      evaluation_results_filter: null,
    });
    return true;
  };
  applyHintFromStore();

  host.__offSession = on('session:change', () => {
    renderResultsPage(host).catch((err) => {
      console.error('[evaluation/results] remount failed:', err);
    });
  });
  // 当 Run 子页跑完一次新的评分时, 它会 emit 'judge:results_changed'. 我们
  // 重新拉一次当前过滤的 results — 新结果如果命中过滤会自动出现在表顶.
  // 不重设 offset / 过滤, 尽量保留用户的阅读位置.
  host.__offJudgeResults = on('judge:results_changed', async () => {
    if (!state.sessionId) return;
    try {
      await loadResults(state);
      renderAll(root, state);
    } catch (err) {
      console.error('[evaluation/results] refresh on judge change failed:', err);
    }
  });

  if (!state.sessionId) {
    root.append(renderEmptyState());
    return;
  }

  await loadSchemas(state);
  await loadResults(state);
  renderAll(root, state);
}

function renderEmptyState() {
  return el('div', { className: 'empty-state' },
    el('h3', {}, i18n('evaluation.results.no_session.heading')),
    el('p', {}, i18n('evaluation.results.no_session.body')),
  );
}

//
// ── data fetch ────────────────────────────────────────────────────
//

async function loadSchemas(state) {
  const resp = await api.get('/api/judge/schemas', { expectedStatuses: [404] });
  if (resp.ok) state.schemasList = resp.data?.schemas || [];
  else state.schemasList = [];
}

function filterToQuery(filter, { offset, limit } = {}) {
  const out = {};
  for (const [k, v] of Object.entries(filter)) {
    if (v === '' || v == null) continue;
    out[k] = v;
  }
  if (typeof offset === 'number') out.offset = offset;
  if (typeof limit === 'number') out.limit = limit;
  return out;
}

function buildQs(params) {
  const usp = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) {
    if (v === '' || v == null) continue;
    usp.append(k, String(v));
  }
  const s = usp.toString();
  return s ? `?${s}` : '';
}

// P24 §14.4 M3: abort prev on rapid filter / paginate clicks.
let _resultsLoadController = null;

async function loadResults(state) {
  if (_resultsLoadController) {
    try { _resultsLoadController.abort(); } catch { /* ignore */ }
  }
  const controller = new AbortController();
  _resultsLoadController = controller;
  state.loading = true;
  const qs = buildQs(filterToQuery(state.filter, { offset: state.offset, limit: state.limit }));
  const resp = await api.get(`/api/judge/results${qs}`, {
    expectedStatuses: [404],
    signal: controller.signal,
  });
  if (resp.error?.type === 'aborted') { state.loading = false; return; }
  if (_resultsLoadController === controller) _resultsLoadController = null;
  if (resp.ok) {
    state.results = resp.data?.results || [];
    state.total = resp.data?.total || 0;
    // Prune selection for rows no longer in view (edge case: filter
    // changed, old ids gone). Keep ones still visible.
    const visible = new Set(state.results.map((r) => r.id));
    for (const id of [...state.selected]) {
      if (!visible.has(id)) state.selected.delete(id);
    }
    // If openResultId is no longer present in current page, close
    // drawer (drawer would fail to resolve its row).
    if (state.openResultId && !visible.has(state.openResultId)) {
      state.openResultId = null;
    }
  } else {
    state.results = [];
    state.total = 0;
  }
  state.loading = false;
}

//
// ── render ────────────────────────────────────────────────────────
//

function renderAll(root, state) {
  root.innerHTML = '';
  root.append(renderHeader());
  root.append(renderIntro());
  root.append(renderFilterBar(root, state));
  root.append(renderToolbar(root, state));
  root.append(renderTable(root, state));
  root.append(renderPager(root, state));
  root.append(renderDrawer(root, state));
}

function renderHeader() {
  return el('h2', {}, i18n('evaluation.results.heading'));
}

function renderIntro() {
  return el('p', { className: 'intro' }, i18n('evaluation.results.intro'));
}

//
// ── filter bar ────────────────────────────────────────────────────
//

function renderFilterBar(root, state) {
  const wrap = el('div', { className: 'eval-results-filter' });

  // Helper: debounced text input that triggers reload; others reload
  // immediately because they're discrete (select / radio).
  const onChangeReload = (key) => async (e) => {
    state.filter[key] = e.target.value;
    state.offset = 0;
    persistFilter(state.filter);
    await loadResults(state);
    renderAll(root, state);
  };

  // Schema picker — only shows schemas we've actually seen or are
  // currently loaded. Empty option = all.
  const schemaSel = el('select', { onChange: onChangeReload('schema_id') });
  schemaSel.append(el('option', { value: '' }, i18n('evaluation.results.filter.any')));
  for (const s of state.schemasList || []) {
    schemaSel.append(el('option', {
      value: s.id,
      selected: state.filter.schema_id === s.id ? true : undefined,
    }, s.name || s.id));
  }

  const modeSel = el('select', { onChange: onChangeReload('mode') });
  for (const [v, label] of [
    ['',             i18n('evaluation.results.filter.any')],
    ['absolute',     i18n('evaluation.schemas.list.badge_absolute')],
    ['comparative',  i18n('evaluation.schemas.list.badge_comparative')],
  ]) {
    modeSel.append(el('option', {
      value: v, selected: state.filter.mode === v ? true : undefined,
    }, label));
  }

  const verdictSel = el('select', { onChange: onChangeReload('verdict') });
  verdictSel.append(el('option', { value: '' }, i18n('evaluation.results.filter.any')));
  // Discover verdicts already seen in the loaded page; union with a
  // known default set so fresh sessions still get options.
  const verdictsSeen = new Set(['YES', 'NO', 'PARTIAL', 'A_better', 'B_better', 'tie']);
  for (const r of state.results || []) {
    if (r.verdict) verdictsSeen.add(r.verdict);
  }
  for (const v of [...verdictsSeen].sort()) {
    verdictSel.append(el('option', {
      value: v, selected: state.filter.verdict === v ? true : undefined,
    }, v));
  }

  const passedSel = el('select', { onChange: onChangeReload('passed') });
  for (const [v, label] of [
    ['',      i18n('evaluation.results.filter.any')],
    ['true',  i18n('evaluation.run.results.passed')],
    ['false', i18n('evaluation.run.results.failed')],
  ]) {
    passedSel.append(el('option', {
      value: v, selected: state.filter.passed === v ? true : undefined,
    }, label));
  }

  const erroredSel = el('select', { onChange: onChangeReload('errored') });
  for (const [v, label] of [
    ['',      i18n('evaluation.results.filter.any')],
    ['true',  i18n('evaluation.results.filter.errored_yes')],
    ['false', i18n('evaluation.results.filter.errored_no')],
  ]) {
    erroredSel.append(el('option', {
      value: v, selected: state.filter.errored === v ? true : undefined,
    }, label));
  }

  // Numeric range — debounced so the user can type "85" without an
  // intermediate refetch at "8".
  const debouncedRange = debounce(async () => {
    state.offset = 0;
    persistFilter(state.filter);
    await loadResults(state);
    renderAll(root, state);
  }, 350);
  const makeNumInput = (key, placeholder) => el('input', {
    type: 'number', step: 'any',
    placeholder,
    value: state.filter[key] ?? '',
    onInput: (e) => { state.filter[key] = e.target.value; debouncedRange(); },
  });

  const query = el('input', {
    type: 'search',
    placeholder: i18n('evaluation.results.filter.query_placeholder'),
    value: state.filter.query || '',
    onInput: (e) => { state.filter.query = e.target.value; debouncedRange(); },
  });

  // Layout: wrap each group in `.field.compact` so they flow in the
  // same form-grid style used by Run page.
  const grid = el('div', { className: 'eval-results-filter-grid' });
  grid.append(
    field(i18n('evaluation.results.filter.schema'), schemaSel, { }),
    field(i18n('evaluation.results.filter.mode'), modeSel, { }),
    field(i18n('evaluation.results.filter.verdict'), verdictSel, { }),
    field(i18n('evaluation.results.filter.passed'), passedSel, { }),
    field(i18n('evaluation.results.filter.errored'), erroredSel, { }),
    field(i18n('evaluation.results.filter.overall_range'),
      el('div', { className: 'eval-results-range' },
        makeNumInput('min_overall', i18n('evaluation.results.filter.min')),
        el('span', { className: 'dash' }, '–'),
        makeNumInput('max_overall', i18n('evaluation.results.filter.max')),
      ),
    ),
    field(i18n('evaluation.results.filter.gap_range'),
      el('div', { className: 'eval-results-range' },
        makeNumInput('min_gap', i18n('evaluation.results.filter.min')),
        el('span', { className: 'dash' }, '–'),
        makeNumInput('max_gap', i18n('evaluation.results.filter.max')),
      ),
      { hint: i18n('evaluation.results.filter.gap_hint') },
    ),
    field(i18n('evaluation.results.filter.query'), query, {
      hint: i18n('evaluation.results.filter.query_hint'), wide: true,
    }),
  );
  wrap.append(grid);

  // Reset + active-filter badges row — makes it obvious what's narrowed.
  const active = activeFilterSummary(state.filter);
  const actions = el('div', { className: 'eval-results-filter-actions' });
  if (active.length) {
    const chips = el('span', { className: 'eval-results-filter-chips' });
    for (const { key, label } of active) {
      chips.append(el('span', {
        className: 'badge eval-results-filter-chip', title: key,
      },
        `${label}`,
        el('button', {
          type: 'button', className: 'cb-close',
          title: i18n('evaluation.results.filter.remove_one'),
          onClick: async () => {
            state.filter[key] = '';
            state.offset = 0;
            persistFilter(state.filter);
            await loadResults(state);
            renderAll(root, state);
          },
        }, '×'),
      ));
    }
    actions.append(chips);
  }
  actions.append(el('button', {
    type: 'button', className: 'ghost tiny',
    onClick: async () => {
      state.filter = defaultFilter();
      state.offset = 0;
      persistFilter(state.filter);
      await loadResults(state);
      renderAll(root, state);
    },
  }, i18n('evaluation.results.filter.reset')));
  wrap.append(actions);

  return wrap;
}

/**
 * Build `[ {key, label}, ... ]` for the 'active filter' chip row.
 * Filter keys with empty values are skipped; numeric ranges collapse
 * both ends into a single chip for brevity.
 */
function activeFilterSummary(filter) {
  const out = [];
  const has = (k) => filter[k] !== '' && filter[k] != null;
  if (has('schema_id')) out.push({ key: 'schema_id', label: `${i18n('evaluation.results.filter.schema')}: ${filter.schema_id}` });
  if (has('mode'))      out.push({ key: 'mode',      label: `${i18n('evaluation.results.filter.mode')}: ${filter.mode}` });
  if (has('verdict'))   out.push({ key: 'verdict',   label: `${i18n('evaluation.results.filter.verdict')}: ${filter.verdict}` });
  if (has('passed'))    out.push({ key: 'passed',    label: `${i18n('evaluation.results.filter.passed')}: ${filter.passed}` });
  if (has('errored'))   out.push({ key: 'errored',   label: `${i18n('evaluation.results.filter.errored')}: ${filter.errored}` });
  if (has('judge_model')) out.push({ key: 'judge_model', label: `judge: ${filter.judge_model}` });
  if (has('message_id')) out.push({ key: 'message_id', label: `msg: ${String(filter.message_id).slice(0, 8)}` });
  if (has('min_overall') || has('max_overall')) {
    out.push({
      key: 'min_overall',
      label: `${i18n('evaluation.results.filter.overall_range')}: ${filter.min_overall || '-'}–${filter.max_overall || '-'}`,
    });
  }
  if (has('min_gap') || has('max_gap')) {
    out.push({
      key: 'min_gap',
      label: `${i18n('evaluation.results.filter.gap_range')}: ${filter.min_gap || '-'}–${filter.max_gap || '-'}`,
    });
  }
  if (has('query')) out.push({ key: 'query', label: `"${filter.query}"` });
  return out;
}

//
// ── toolbar ───────────────────────────────────────────────────────
//

function renderToolbar(root, state) {
  const wrap = el('div', { className: 'eval-results-toolbar' });
  const selectedCount = state.selected.size;
  const meta = el('span', { className: 'meta' },
    i18n('evaluation.results.toolbar.count_fmt', state.total, selectedCount));
  const actions = el('span', { className: 'eval-results-toolbar-actions' });

  actions.append(el('button', {
    type: 'button', className: 'ghost tiny',
    title: i18n('evaluation.results.toolbar.refresh_hint'),
    onClick: async () => {
      await loadResults(state);
      renderAll(root, state);
    },
  }, i18n('evaluation.results.toolbar.refresh')));

  actions.append(el('button', {
    type: 'button', className: 'ghost tiny',
    disabled: state.exporting ? true : undefined,
    onClick: () => exportReport(state, 'markdown'),
  }, state.exporting
    ? i18n('evaluation.results.toolbar.exporting')
    : i18n('evaluation.results.toolbar.export_md')));

  actions.append(el('button', {
    type: 'button', className: 'ghost tiny',
    disabled: state.exporting ? true : undefined,
    onClick: () => exportReport(state, 'json'),
  }, i18n('evaluation.results.toolbar.export_json')));

  if (selectedCount > 0) {
    actions.append(el('button', {
      type: 'button', className: 'ghost tiny danger',
      onClick: () => clearSelection(root, state),
    }, i18n('evaluation.results.toolbar.clear_selection', selectedCount)));
  }

  wrap.append(meta, actions);
  return wrap;
}

async function exportReport(state, format) {
  if (state.exporting) return;
  state.exporting = true;

  try {
    const body = {
      format,
      filter: filterToQuery(state.filter),
      scope_label: buildScopeLabel(state.filter),
    };
    const resp = await fetch('/api/judge/export_report', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    });
    if (!resp.ok) {
      toast.err(i18n('evaluation.results.toast.export_failed'), { message: `HTTP ${resp.status}` });
      return;
    }
    const blob = await resp.blob();
    const cd = resp.headers.get('Content-Disposition') || '';
    // Parse filename="eval_report_xx.md" from Content-Disposition; fall
    // back to a generic name if the server didn't set it (shouldn't
    // happen but be defensive).
    const nameMatch = cd.match(/filename="([^"]+)"/);
    const filename = nameMatch
      ? nameMatch[1]
      : (format === 'json' ? 'eval_report.json' : 'eval_report.md');
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = filename;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    URL.revokeObjectURL(url);
    toast.ok(i18n('evaluation.results.toast.export_ok', filename));
  } catch (err) {
    toast.err(i18n('evaluation.results.toast.export_failed'), { message: String(err) });
  } finally {
    state.exporting = false;
  }
}

function buildScopeLabel(filter) {
  const parts = [];
  if (filter.schema_id) parts.push(filter.schema_id);
  if (filter.mode)      parts.push(filter.mode);
  if (filter.verdict)   parts.push(filter.verdict);
  return parts.join('_') || 'filtered';
}

//
// ── table ────────────────────────────────────────────────────────
//

function renderTable(root, state) {
  if (state.loading) {
    return el('div', { className: 'eval-results-loading' },
      i18n('evaluation.results.loading'));
  }
  if (!state.results.length) {
    return el('div', { className: 'empty-inline' },
      i18n('evaluation.results.table.empty'));
  }

  const table = el('table', { className: 'eval-results-table' });
  const thead = el('thead', {});
  const allChecked = state.selected.size > 0
    && state.results.every((r) => state.selected.has(r.id));
  const selectAllCb = el('input', {
    type: 'checkbox',
    checked: allChecked,
    onChange: (e) => {
      if (e.target.checked) {
        for (const r of state.results) state.selected.add(r.id);
      } else {
        for (const r of state.results) state.selected.delete(r.id);
      }
      renderAll(root, state);
    },
  });
  thead.append(el('tr', {},
    el('th', { className: 'sel' }, selectAllCb),
    el('th', {}, i18n('evaluation.results.table.col.time')),
    el('th', {}, i18n('evaluation.results.table.col.schema')),
    el('th', {}, i18n('evaluation.results.table.col.mode')),
    el('th', {}, i18n('evaluation.results.table.col.verdict')),
    el('th', {}, i18n('evaluation.results.table.col.score')),
    el('th', {}, i18n('evaluation.results.table.col.duration')),
    el('th', {}, i18n('evaluation.results.table.col.target')),
    el('th', {}, ''),
  ));
  table.append(thead);

  const tbody = el('tbody', {});
  for (const r of state.results) tbody.append(renderRow(root, state, r));
  table.append(tbody);

  return table;
}

function renderRow(root, state, r) {
  const isError = Boolean(r.error);
  const rowClass = 'eval-results-row'
    + (isError ? ' eval-results-row--error' : '')
    + (state.openResultId === r.id ? ' eval-results-row--open' : '');

  const cb = el('input', {
    type: 'checkbox',
    checked: state.selected.has(r.id),
    onChange: (e) => {
      if (e.target.checked) state.selected.add(r.id);
      else state.selected.delete(r.id);
      renderAll(root, state);
    },
    // Stop propagation so clicking the checkbox doesn't also open
    // the drawer; cell click on any other part of the row will.
    onClick: (e) => e.stopPropagation(),
  });

  const created = (r.created_at || '').replace('T', ' ').slice(0, 19);
  const mode = r.mode || '-';
  const verdictBadge = r.verdict
    ? el('span', { className: 'badge verdict-' + String(r.verdict).replace(/[^a-z_]/gi, '_') }, r.verdict)
    : el('span', { className: 'badge subtle' }, '-');
  const passedBadge = isError
    ? el('span', { className: 'badge danger' }, i18n('evaluation.results.table.errored'))
    : (r.passed
      ? el('span', { className: 'badge success' }, i18n('evaluation.run.results.passed'))
      : el('span', { className: 'badge subtle' }, i18n('evaluation.run.results.failed')));

  const scores = r.scores || {};
  const scoreCell = mode === 'comparative'
    ? formatGap(r.gap, scores)
    : formatAbsoluteScore(scores);

  const targets = r.target_message_ids || [];
  const targetsCell = targets.length
    ? targets.slice(0, 3).map((id) => id.slice(0, 8)).join(', ')
      + (targets.length > 3 ? ` +${targets.length - 3}` : '')
    : i18n('evaluation.run.results.target_conversation');

  const openBtn = el('button', {
    type: 'button', className: 'ghost tiny',
    onClick: (e) => {
      e.stopPropagation();
      openDrawer(root, state, r.id);
    },
  }, i18n('evaluation.results.table.open'));

  const tr = el('tr', {
    className: rowClass,
    onClick: () => openDrawer(root, state, r.id),
  },
    el('td', { className: 'sel' }, cb),
    el('td', { className: 'mono' }, created),
    el('td', {}, r.schema_id || '-'),
    el('td', {},
      el('span', { className: 'badge subtle' }, humanMode(mode)),
      r.granularity
        ? el('span', { className: 'badge subtle' }, humanGranularity(r.granularity))
        : null,
    ),
    el('td', {}, verdictBadge, ' ', passedBadge),
    el('td', { className: 'mono' }, scoreCell),
    el('td', { className: 'mono' }, `${r.duration_ms || 0}ms`),
    el('td', { className: 'mono', title: targets.join(', ') }, targetsCell),
    el('td', { className: 'eval-results-row-actions' }, openBtn),
  );
  return tr;
}

function formatAbsoluteScore(scores) {
  const ov = scores?.overall_score;
  if (ov == null) return '-';
  return `${Number(ov).toFixed(1)}/100`;
}

function formatGap(gap, scores) {
  if (gap == null) return '-';
  const sign = gap >= 0 ? '+' : '';
  const oa = scores?.overall_a;
  const ob = scores?.overall_b;
  if (oa != null && ob != null) {
    return `${sign}${Number(gap).toFixed(1)} (${Number(oa).toFixed(0)} vs ${Number(ob).toFixed(0)})`;
  }
  return `${sign}${Number(gap).toFixed(1)}`;
}

//
// ── pager ─────────────────────────────────────────────────────────
//

function renderPager(root, state) {
  const wrap = el('div', { className: 'eval-results-pager' });
  const total = state.total;
  if (total <= state.results.length && state.offset === 0) {
    // Everything fits in one page, no pager needed.
    return wrap;
  }
  const from = total === 0 ? 0 : state.offset + 1;
  const to = Math.min(state.offset + state.results.length, total);
  wrap.append(el('span', { className: 'meta' },
    i18n('evaluation.results.pager.fmt', from, to, total)));
  const prev = el('button', {
    type: 'button', className: 'ghost tiny',
    disabled: state.offset === 0 ? true : undefined,
    onClick: async () => {
      state.offset = Math.max(0, state.offset - state.limit);
      await loadResults(state);
      renderAll(root, state);
    },
  }, i18n('evaluation.results.pager.prev'));
  const next = el('button', {
    type: 'button', className: 'ghost tiny',
    disabled: (state.offset + state.limit >= total) ? true : undefined,
    onClick: async () => {
      state.offset += state.limit;
      await loadResults(state);
      renderAll(root, state);
    },
  }, i18n('evaluation.results.pager.next'));
  wrap.append(prev, next);
  return wrap;
}

//
// ── drawer ───────────────────────────────────────────────────────
//

function openDrawer(root, state, resultId) {
  state.openResultId = resultId;
  renderAll(root, state);
}

function closeDrawer(root, state) {
  state.openResultId = null;
  renderAll(root, state);
}

function renderDrawer(root, state) {
  if (!state.openResultId) return el('span', {});
  const r = state.results.find((x) => x.id === state.openResultId);
  if (!r) {
    // Shouldn't happen post loadResults pruning, but guard anyway.
    return el('span', {});
  }

  const overlay = el('div', {
    className: 'eval-results-drawer-overlay',
    onClick: (e) => {
      if (e.target === e.currentTarget) closeDrawer(root, state);
    },
  });
  const drawer = el('div', { className: 'eval-results-drawer' });
  overlay.append(drawer);

  const isError = Boolean(r.error);

  // ── drawer header ──
  const header = el('div', { className: 'eval-results-drawer-header' },
    el('div', { className: 'eval-results-drawer-title' },
      el('span', {}, r.schema_id || '-'),
      el('span', { className: 'badge subtle' }, humanMode(r.mode)),
      el('span', { className: 'badge subtle' }, humanGranularity(r.granularity)),
    ),
    el('button', {
      type: 'button', className: 'cb-close eval-results-drawer-close',
      onClick: () => closeDrawer(root, state),
      title: i18n('evaluation.results.drawer.close'),
    }, '×'),
  );
  drawer.append(header);

  const meta = el('div', { className: 'eval-results-drawer-meta' });
  if (r.verdict) {
    meta.append(el('span', {
      className: 'badge verdict-' + String(r.verdict).replace(/[^a-z_]/gi, '_'),
    }, r.verdict));
  }
  meta.append(isError
    ? el('span', { className: 'badge danger' }, i18n('evaluation.results.table.errored'))
    : (r.passed
      ? el('span', { className: 'badge success' }, i18n('evaluation.run.results.passed'))
      : el('span', { className: 'badge subtle' }, i18n('evaluation.run.results.failed'))));
  meta.append(el('span', { className: 'meta' },
    (r.created_at || '').replace('T', ' ').slice(0, 19)));
  meta.append(el('span', { className: 'meta' }, `${r.duration_ms || 0}ms`));
  const judgeModel = r.judge_model || {};
  if (judgeModel.model || judgeModel.provider) {
    meta.append(el('span', { className: 'meta' },
      i18n('evaluation.results.drawer.judge_model_fmt',
        judgeModel.provider || '-', judgeModel.model || '-')));
  }
  drawer.append(meta);

  const body = el('div', { className: 'eval-results-drawer-body' });

  if (isError) {
    body.append(el('div', { className: 'eval-run-result eval-run-result--error' },
      el('div', { className: 'eval-run-result-title' },
        i18n('evaluation.run.results.batch_error')),
      el('pre', { className: 'mono' }, r.error),
    ));
  } else {
    // Dimensions (absolute) or A/B side-by-side (comparative).
    if (r.mode === 'comparative') {
      body.append(renderComparativeScores(r));
    } else {
      body.append(renderAbsoluteDimensions(r));
    }

    if (r.analysis) {
      body.append(createCollapsible({
        blockId: `ev-analysis-${r.id}`,
        title: i18n('evaluation.results.drawer.analysis'),
        content: r.analysis,
        defaultCollapsed: false,
        copyable: true,
      }));
    }

    if ((r.strengths || []).length || (r.weaknesses || []).length) {
      const sw = el('div', { className: 'eval-run-result-sw' });
      if ((r.strengths || []).length) {
        sw.append(renderBulletList(i18n('evaluation.run.results.strengths'), r.strengths));
      }
      if ((r.weaknesses || []).length) {
        sw.append(renderBulletList(i18n('evaluation.run.results.weaknesses'), r.weaknesses));
      }
      body.append(sw);
    }

    if (r.mode === 'comparative') {
      if (r.diff_analysis) {
        body.append(createCollapsible({
          blockId: `ev-diff-${r.id}`,
          title: i18n('evaluation.results.drawer.diff_analysis'),
          content: r.diff_analysis,
          defaultCollapsed: false,
          copyable: true,
        }));
      }
      if ((r.problem_patterns || []).length) {
        const pp = el('div', { className: 'eval-run-result-dims' });
        pp.append(el('strong', {}, i18n('evaluation.results.drawer.problem_patterns')));
        for (const p of r.problem_patterns) {
          pp.append(el('span', { className: 'badge subtle' }, p));
        }
        body.append(pp);
      }
    }

    // Context (system prompt / user input / ai response / reference /
    // raw scores / raw response) — all behind collapsibles to avoid
    // swamping the drawer.
    body.append(renderContextSection(r));
  }

  drawer.append(body);

  // Attach expand/collapse-all toolbar to the context section's cb
  // container — more discoverable than a global one.
  mountContainerToolbar(drawer.querySelector('.eval-results-drawer-context'));

  return overlay;
}

function renderAbsoluteDimensions(r) {
  const wrap = el('div', { className: 'eval-results-dimensions' });
  const snap = r.schema_snapshot || {};
  const dims = Array.isArray(snap.dimensions) ? snap.dimensions : [];
  const scores = r.scores || {};
  const maxRaw = Number(snap.max_raw_score) || 10;
  // Always include overall and per-dim if present. Render overall at
  // top as a 0-100 bar (distinct scale), then dimension bars in 0-N.
  const overall = scores.overall_score;
  if (overall != null) {
    wrap.append(renderBar({
      label: i18n('evaluation.results.drawer.overall'),
      value: Number(overall),
      max: 100,
      unit: '/100',
      highlight: true,
    }));
  }
  if (dims.length) {
    for (const d of dims) {
      const v = scores[d.key];
      wrap.append(renderBar({
        label: d.label || d.key,
        value: v == null ? null : Number(v),
        max: maxRaw,
        unit: `/${maxRaw}`,
      }));
    }
  } else {
    // No snapshot; fall back to listing everything except meta keys.
    for (const [k, v] of Object.entries(scores)) {
      if (['overall_score', 'raw_score', 'ai_ness_penalty'].includes(k)) continue;
      wrap.append(renderBar({
        label: k, value: Number(v), max: maxRaw, unit: `/${maxRaw}`,
      }));
    }
  }
  if (scores.raw_score != null) {
    wrap.append(el('div', { className: 'eval-results-dim-sub' },
      i18n('evaluation.results.drawer.raw_score_fmt',
        Number(scores.raw_score).toFixed(2))));
  }
  if (scores.ai_ness_penalty != null) {
    wrap.append(el('div', { className: 'eval-results-dim-sub' },
      i18n('evaluation.results.drawer.penalty_fmt',
        Number(scores.ai_ness_penalty).toFixed(2))));
  }
  return wrap;
}

function renderBar({ label, value, max, unit, highlight }) {
  const row = el('div', {
    className: 'eval-results-bar' + (highlight ? ' eval-results-bar--hl' : ''),
  });
  const pct = (value != null && max > 0)
    ? Math.max(0, Math.min(100, (value / max) * 100))
    : 0;
  const bar = el('div', { className: 'eval-results-bar-track' },
    el('div', {
      className: 'eval-results-bar-fill',
      style: { width: `${pct}%` },
    }));
  row.append(
    el('span', { className: 'eval-results-bar-label' }, label),
    bar,
    el('span', { className: 'eval-results-bar-value mono' },
      value == null ? '-' : `${Number(value).toFixed(1)}${unit || ''}`),
  );
  return row;
}

function renderComparativeScores(r) {
  const wrap = el('div', { className: 'eval-results-cmp' });
  const scores = r.scores || {};
  const oa = scores.overall_a;
  const ob = scores.overall_b;
  const gap = r.gap;

  // Header row — A vs B + gap badge.
  const headline = el('div', { className: 'eval-results-cmp-head' },
    el('div', { className: 'eval-results-cmp-side' },
      el('strong', {}, i18n('evaluation.results.drawer.side_a')),
      el('span', { className: 'mono' }, oa == null ? '-' : `${Number(oa).toFixed(1)}/100`),
    ),
    el('div', { className: 'eval-results-cmp-side' },
      el('strong', {}, i18n('evaluation.results.drawer.side_b')),
      el('span', { className: 'mono' }, ob == null ? '-' : `${Number(ob).toFixed(1)}/100`),
    ),
    el('div', { className: 'eval-results-cmp-gap' },
      el('strong', {}, i18n('evaluation.results.drawer.gap')),
      el('span', {
        className: 'badge ' + (gap > 0 ? 'success' : gap < 0 ? 'danger' : 'subtle'),
      }, gap == null ? '-' : `${gap >= 0 ? '+' : ''}${Number(gap).toFixed(1)}`),
    ),
  );
  wrap.append(headline);

  // Per-dim table: A | B | gap.
  const dimKeysA = Object.keys(scores.a || {});
  const dimKeysB = Object.keys(scores.b || {});
  const allKeys = Array.from(new Set([...dimKeysA, ...dimKeysB])).filter(
    (k) => !['overall_a', 'overall_b', 'raw_score'].includes(k));
  const gapMap = scores.per_dim_gap || {};
  if (allKeys.length) {
    const table = el('table', { className: 'eval-results-cmp-table' });
    table.append(el('thead', {}, el('tr', {},
      el('th', {}, i18n('evaluation.results.drawer.cmp_col_dim')),
      el('th', {}, 'A'),
      el('th', {}, 'B'),
      el('th', {}, i18n('evaluation.results.drawer.cmp_col_gap')),
    )));
    const tbody = el('tbody', {});
    for (const k of allKeys) {
      const va = scores.a?.[k];
      const vb = scores.b?.[k];
      const dg = gapMap[k];
      const gapBadge = (dg == null) ? '-' : `${dg > 0 ? '+' : ''}${Number(dg).toFixed(1)}`;
      const gapCls = 'badge ' + (dg > 0 ? 'success' : dg < 0 ? 'danger' : 'subtle');
      tbody.append(el('tr', {},
        el('td', {}, k),
        el('td', { className: 'mono' }, va == null ? '-' : Number(va).toFixed(1)),
        el('td', { className: 'mono' }, vb == null ? '-' : Number(vb).toFixed(1)),
        el('td', {}, el('span', { className: gapCls }, gapBadge)),
      ));
    }
    table.append(tbody);
    wrap.append(table);
  }
  if (r.relative_advantage) {
    wrap.append(el('div', { className: 'eval-results-dim-sub' },
      i18n('evaluation.results.drawer.relative_advantage_fmt', r.relative_advantage)));
  }
  return wrap;
}

function renderContextSection(r) {
  const ctx = el('div', { className: 'eval-results-drawer-context' });
  const tp = r.target_preview || {};

  if (tp.ai_response) {
    ctx.append(createCollapsible({
      blockId: `ev-ai-${r.id}`,
      title: i18n('evaluation.results.drawer.target_ai'),
      content: tp.ai_response,
      defaultCollapsed: false,
      copyable: true,
    }));
  }
  if (tp.reference_response) {
    ctx.append(createCollapsible({
      blockId: `ev-ref-${r.id}`,
      title: i18n('evaluation.results.drawer.target_reference'),
      content: tp.reference_response,
      defaultCollapsed: true,
      copyable: true,
    }));
  }
  if (tp.user_input) {
    ctx.append(createCollapsible({
      blockId: `ev-user-${r.id}`,
      title: i18n('evaluation.results.drawer.target_user'),
      content: tp.user_input,
      defaultCollapsed: true,
      copyable: true,
    }));
  }
  if (tp.system_prompt) {
    ctx.append(createCollapsible({
      blockId: `ev-sys-${r.id}`,
      title: i18n('evaluation.results.drawer.target_system'),
      content: tp.system_prompt,
      defaultCollapsed: true,
      copyable: true,
    }));
  }
  if (r.raw_response) {
    ctx.append(createCollapsible({
      blockId: `ev-raw-${r.id}`,
      title: i18n('evaluation.results.drawer.raw_response'),
      content: r.raw_response,
      defaultCollapsed: true,
      copyable: true,
    }));
  }
  // Always-available raw JSON dump (the full EvalResult dict), last so
  // it's the "fallback source of truth" for anything the formatted
  // sections don't show.
  ctx.append(createCollapsible({
    blockId: `ev-json-${r.id}`,
    title: i18n('evaluation.results.drawer.raw_json'),
    content: JSON.stringify(r, null, 2),
    defaultCollapsed: true,
    copyable: true,
  }));
  return ctx;
}

function renderBulletList(label, items) {
  const wrap = el('div', { className: 'eval-run-result-list' });
  wrap.append(el('strong', {}, label));
  const ul = el('ul', {});
  for (const it of items) ul.append(el('li', {}, it));
  wrap.append(ul);
  return wrap;
}

//
// ── helpers ──────────────────────────────────────────────────────
//

function clearSelection(root, state) {
  state.selected.clear();
  renderAll(root, state);
}

function debounce(fn, ms) {
  let timer = null;
  return (...args) => {
    clearTimeout(timer);
    timer = setTimeout(() => fn(...args), ms);
  };
}

function humanMode(mode) {
  if (mode === 'comparative') return i18n('evaluation.schemas.list.badge_comparative');
  if (mode === 'absolute')    return i18n('evaluation.schemas.list.badge_absolute');
  return mode || '-';
}

function humanGranularity(granularity) {
  if (granularity === 'conversation') return i18n('evaluation.schemas.list.badge_conversation');
  if (granularity === 'single')       return i18n('evaluation.schemas.list.badge_single');
  return granularity || '';
}
