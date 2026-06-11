/**
 * page_logs.js — Diagnostics → Logs 子页 (P19).
 *
 * 把 `tests/testbench_data/logs/<session_id>-YYYYMMDD.jsonl` 拉来尾部
 * 浏览 + 过滤 + 导出. 数据源 `/api/diagnostics/logs` (服务端一次读整文
 * 件并按条过滤, 因为每会话每日通常 O(几百) 条, 开销可忽略).
 *
 * 布局:
 *   - 顶部 session/date 选择条 (默认选最新一对) + level 过滤 + op facet
 *     按钮 + keyword 搜索 + auto-refresh 勾选 + 导出原始 JSONL.
 *   - 主体: 每条日志一个 CollapsibleBlock, 折叠态显示
 *     `ts · level · op · 摘要`, 展开态显示 payload + error 完整 JSON.
 *   - 空态 / 错误态 / 多 session 列表为空时分别给清晰文案.
 *
 * 与 Errors 子页的区别:
 *   - Logs = 全量结构化记录 (INFO/WARNING/ERROR 都在), 是"发生过什么"的
 *     权威回放; Errors = 最近错误摘要, 是"哪里失败了"的工作队列.
 *   - Logs 按 session+date 维度切, Errors 是进程全局.
 *   - Logs 数据源是磁盘 JSONL, 服务重启不丢; Errors 是内存 ring buffer.
 */

import { api } from '../../core/api.js';
import { i18n, i18nRaw } from '../../core/i18n.js';
import { store } from '../../core/state.js';
import { toast } from '../../core/toast.js';
import { formatIsoReadable } from '../../core/time_utils.js';
import { el } from '../_dom.js';
import { lookupOp, opDescription, opLabel } from './op_catalog.js';

const LS_SESSION_KEY = 'testbench:diagnostics:logs:session:v1';
const LS_DATE_KEY    = 'testbench:diagnostics:logs:date:v1';
const POLL_INTERVAL_MS = 5000;
// sentinel session id for "merge every session's file for this date"
// — backend accepts '*' or 'all' in /api/diagnostics/logs.
const ALL_SESSIONS_VALUE = '*';

function defaultState() {
  return {
    loading: false,
    // Session/date 选择.
    sessions: [],   // [{ session_id, dates: [...], latest: '...' }]
    allDates: [],   // 所有 session 的 date 并集 (用于 session=* 时的 date 下拉)
    sessionId: localStorage.getItem(LS_SESSION_KEY) || null,
    date: localStorage.getItem(LS_DATE_KEY) || null,
    // 过滤.
    level: '',
    op: '',
    keyword: '',
    // 结果.
    total: 0,
    matched: 0,
    items: [],
    facets: { op: [], level: [] },
    pageSize: 200,
    offset: 0,
    autoRefresh: true,
    loadError: null,
    sessionLoadError: null,
    // 保留策略 + 占用统计 (P19 hotfix 2).
    retention: null, // { retention_days, usage: { ... }, debug_enabled }
    cleanupBusy: false,
    debugToggleBusy: false,
    // P19 hotfix 4: 记录用户显式点开/折叠过的 entry, 跨 auto-refresh 保留.
    // key: entryKey(entry) → bool (true=open). 未在此 map 的 entry 走
    // defaultOpenFor(level): WARNING/ERROR → 默认展开, INFO/DEBUG → 折叠.
    toggledKeys: new Map(),
    // P25 Day 2 hotfix (2026-04-23): sub-<details> 展开态持久化.
    // renderEntry() 里有 "原始 JSON" 的 nested <details>; auto-refresh
    // rebuild 整个 entry list 时, 原来的 <details open> 节点被丢掉重建,
    // 用户刚点开的原始 JSON 就收回去了. 这个 Set 记录用户手动展开的
    // 子节点, key 格式 `${entryKey}|raw` (父 entry key + "|raw"). 父
    // 折叠时不清理 — 父重新展开能直接恢复. filter / 分页 / 换 session
    // 等路径会连同 toggledKeys.clear() 一起做 openSubDetails.clear(),
    // 避免 Set 无限增长 (L11 精神: 前端 map/set 不能无界).
    openSubDetails: new Set(),
  };
}

// P25 Day 2 helper: filter / 分页 / 换 session 等 "entry 集合换了"
// 的场景统一调用, 保证 toggledKeys / openSubDetails 一起清, 防止
// Set 无限增长. 之前 6 处 naked ``toggledKeys.clear()`` 漏扫子 Set.
function clearEntryCaches(state) {
  state.toggledKeys.clear();
  state.openSubDetails.clear();
}

async function loadRetentionInfo(state) {
  const resp = await api.get('/api/diagnostics/logs/retention', { expectedStatuses: [404] });
  if (resp.ok) {
    state.retention = resp.data || null;
  } else {
    state.retention = null;
  }
}

async function loadSessionsList(state) {
  const resp = await api.get('/api/diagnostics/logs/sessions', { expectedStatuses: [404] });
  if (resp.ok) {
    state.sessions = resp.data?.sessions || [];
    state.allDates = resp.data?.all_dates || [];
    state.sessionLoadError = null;
  } else {
    state.sessions = [];
    state.allDates = [];
    state.sessionLoadError = resp.error?.message || `HTTP ${resp.status}`;
  }
}

function ensureSelection(state) {
  if (!state.sessions.length) {
    state.sessionId = null;
    state.date = null;
    return;
  }
  // "全部会话" 是合法的持久化值; 日期从 allDates 挑最新.
  if (state.sessionId === ALL_SESSIONS_VALUE) {
    if (!state.allDates.length) {
      // 理论上 allDates 不会比 sessions 先空, 但保守处理.
      state.sessionId = state.sessions[0].session_id;
      state.date = state.sessions[0].latest;
    } else if (!state.allDates.includes(state.date)) {
      state.date = state.allDates[0];
    }
    try {
      localStorage.setItem(LS_SESSION_KEY, state.sessionId);
      localStorage.setItem(LS_DATE_KEY, state.date || '');
    } catch { /* ignore quota */ }
    return;
  }
  // 若持久化的 sessionId 还存在, 保留; 否则挑最新的.
  let match = state.sessions.find((s) => s.session_id === state.sessionId);
  if (!match) {
    match = state.sessions[0];
    state.sessionId = match.session_id;
  }
  // 日期同理.
  const dateOk = match.dates.includes(state.date);
  if (!dateOk) {
    state.date = match.latest;
  }
  try {
    localStorage.setItem(LS_SESSION_KEY, state.sessionId);
    localStorage.setItem(LS_DATE_KEY, state.date);
  } catch { /* ignore quota */ }
}

// P24 §14.4 M3: AbortController — rapid toolbar / filter / pagination
// clicks abort the pending request so "last click wins".
let _logsLoadController = null;

async function loadLogs(state) {
  if (!state.sessionId || !state.date) {
    state.items = [];
    state.total = 0;
    state.matched = 0;
    state.facets = { op: [], level: [] };
    return;
  }
  if (_logsLoadController) {
    try { _logsLoadController.abort(); } catch { /* ignore */ }
  }
  const controller = new AbortController();
  _logsLoadController = controller;
  state.loading = true;
  const usp = new URLSearchParams({
    session_id: state.sessionId,
    date: state.date,
    limit: String(state.pageSize),
    offset: String(state.offset),
  });
  if (state.level)   usp.set('level', state.level);
  if (state.op)      usp.set('op', state.op);
  if (state.keyword) usp.set('keyword', state.keyword);
  const resp = await api.get(`/api/diagnostics/logs?${usp.toString()}`, {
    expectedStatuses: [404],
    signal: controller.signal,
  });
  if (resp.error?.type === 'aborted') return;
  if (_logsLoadController === controller) _logsLoadController = null;
  state.loading = false;
  if (resp.ok) {
    state.items = resp.data?.items || [];
    state.total = resp.data?.total || 0;
    state.matched = resp.data?.matched || 0;
    state.facets = resp.data?.facets || { op: [], level: [] };
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

export async function renderLogsPage(host) {
  if (host.__pollTimer) { clearInterval(host.__pollTimer); host.__pollTimer = null; }

  host.innerHTML = '';
  const root = el('div', { className: 'diag-logs' });
  host.append(root);

  const state = defaultState();
  await Promise.all([loadSessionsList(state), loadRetentionInfo(state)]);
  ensureSelection(state);
  await loadLogs(state);
  renderAll(root, state);

  host.__pollTimer = setInterval(() => {
    if (!state.autoRefresh) return;
    // auto-refresh 只重拉当前 session+date 的日志; 不重拉 sessions list
    // (sessions list 不频繁变化, 但新会话出现时用户手动点 Refresh).
    loadLogs(state).then(() => renderAll(root, state));
  }, POLL_INTERVAL_MS);
}

//
// ── render ───────────────────────────────────────────────────────
//

function renderAll(root, state) {
  root.innerHTML = '';
  root.append(renderHeader());
  const retentionBar = renderRetentionBar(root, state);
  if (retentionBar) root.append(retentionBar);
  root.append(renderSelectorBar(root, state));
  if (state.sessionLoadError) {
    root.append(el('div', { className: 'empty-state' },
      i18n('diagnostics.logs.session_load_failed', state.sessionLoadError)));
    return;
  }
  if (!state.sessions.length) {
    root.append(el('div', { className: 'empty-state' },
      i18n('diagnostics.logs.no_sessions')));
    return;
  }
  if (!state.sessionId || !state.date) {
    root.append(el('div', { className: 'empty-state' },
      i18n('diagnostics.logs.pick_session')));
    return;
  }
  root.append(renderToolbar(root, state));
  const facets = renderFacets(root, state);
  if (facets) root.append(facets);
  if (state.loading && !state.items.length) {
    root.append(el('div', { className: 'empty-state' },
      i18n('diagnostics.logs.loading')));
    return;
  }
  if (state.loadError) {
    root.append(el('div', { className: 'empty-state' },
      i18n('diagnostics.logs.load_failed', state.loadError)));
    return;
  }
  if (state.matched === 0 && state.total === 0) {
    root.append(el('div', { className: 'empty-state' },
      i18n('diagnostics.logs.empty_file')));
    return;
  }
  if (state.matched === 0) {
    root.append(el('div', { className: 'empty-state' },
      i18n('diagnostics.logs.empty_filtered', state.total)));
    return;
  }
  root.append(renderList(state));
  const pager = renderPager(root, state);
  if (pager) root.append(pager);
}

function renderHeader() {
  return el('div', { className: 'diag-page-header' },
    el('h2', {}, i18n('diagnostics.logs.heading')),
    el('p', { className: 'diag-page-intro' },
      i18n('diagnostics.logs.intro')),
    el('p', { className: 'diag-page-intro diag-page-intro-secondary' },
      i18n('diagnostics.logs.intro_help')),
  );
}

function renderRetentionBar(root, state) {
  const r = state.retention;
  if (!r) return null;
  const days = r.retention_days ?? 14;
  const usage = r.usage || { total_files: 0, total_bytes: 0 };
  const debugOn = !!r.debug_enabled;

  const stats = el('span', { className: 'diag-retention-stats' },
    i18n('diagnostics.logs.retention_fmt',
         days, usage.total_files || 0, formatBytes(usage.total_bytes || 0)));

  // Debug toggle: 勾上后, 后端 SessionLogger 会开始把 DEBUG 级落盘
  // (例如 chat.prompt_preview). 默认关, 因为 DEBUG 在正常使用中占比很
  // 高且事后价值低. 切换不需重启 server, 读 `LOG_DEBUG_ENABLED` 是
  // 每写 fresh read, 所以点完立即生效.
  const debugChk = el('input', {
    type: 'checkbox',
    checked: debugOn,
    disabled: state.debugToggleBusy,
    onChange: async (e) => {
      const nextEnabled = !!e.target.checked;
      state.debugToggleBusy = true;
      renderAll(root, state);
      try {
        const resp = await api.post(
          '/api/diagnostics/logs/debug',
          { enabled: nextEnabled },
          { expectedStatuses: [400, 422, 500] },
        );
        if (!resp.ok) {
          toast.err(i18n('diagnostics.logs.debug_toggle_failed_fmt',
                         resp.error?.message || `HTTP ${resp.status}`));
        } else {
          if (resp.data?.enabled) {
            toast.ok(i18n('diagnostics.logs.debug_turned_on'));
          } else {
            toast.info(i18n('diagnostics.logs.debug_turned_off'));
          }
        }
      } catch (err) {
        toast.err(i18n('diagnostics.logs.debug_toggle_failed_fmt',
                       (err && err.message) || String(err)));
      }
      state.debugToggleBusy = false;
      await loadRetentionInfo(state);
      renderAll(root, state);
    },
  });
  const debugLabel = el('label', {
    className: 'diag-checkbox-label',
    title: i18n('diagnostics.logs.debug_toggle_tooltip'),
  },
    debugChk,
    el('span', {}, i18n('diagnostics.logs.debug_toggle_label')),
  );

  const cleanupBtn = el('button', {
    className: 'ghost tiny',
    disabled: state.cleanupBusy,
    onClick: async () => {
      const confirmed = window.confirm(
        i18n('diagnostics.logs.cleanup_confirm_fmt', days)
      );
      if (!confirmed) return;
      state.cleanupBusy = true;
      renderAll(root, state);
      try {
        const resp = await api.post(
          '/api/diagnostics/logs/cleanup',
          {},
          { expectedStatuses: [404, 500] },
        );
        if (!resp.ok) {
          toast.err(i18n('diagnostics.logs.cleanup_failed_fmt',
                         resp.error?.message || `HTTP ${resp.status}`));
        } else if ((resp.data?.deleted || 0) === 0) {
          toast.info(i18n('diagnostics.logs.cleanup_nothing'));
        } else {
          toast.ok(i18n('diagnostics.logs.cleanup_done_fmt',
                        resp.data.deleted,
                        formatBytes(resp.data.bytes_freed || 0)));
        }
      } catch (err) {
        toast.err(i18n('diagnostics.logs.cleanup_failed_fmt',
                       (err && err.message) || String(err)));
      }
      state.cleanupBusy = false;
      await Promise.all([loadSessionsList(state), loadRetentionInfo(state)]);
      ensureSelection(state);
      await loadLogs(state);
      renderAll(root, state);
    },
  }, state.cleanupBusy
      ? i18n('diagnostics.logs.cleanup_running')
      : i18n('diagnostics.logs.cleanup_btn'));

  const right = el('div', { className: 'diag-retention-actions' },
    debugLabel, cleanupBtn);
  return el('div', { className: 'diag-retention-bar' }, stats, right);
}

function renderSelectorBar(root, state) {
  // 当前活跃 session (前端 store 已维护, 不依赖新 API).
  // 目的: 把 "刚才我点了新建会话" 那个 session 在 dropdown 里一眼可辨,
  //       避免测试人员对着一堆 hash id 抓瞎.
  const activeSessionId = store.session?.id || null;
  const isAllMode = state.sessionId === ALL_SESSIONS_VALUE;

  const sortedSessions = sortSessionsForDropdown(state.sessions, activeSessionId);

  // "全部会话" 选项永远置顶, 让测试人员一眼就能开启合并视图.
  const allOpt = el('option', { value: ALL_SESSIONS_VALUE },
    i18n('diagnostics.logs.session_opt_all_fmt', state.sessions.length));
  if (isAllMode) allOpt.selected = true;
  const sessionOpts = [allOpt, ...sortedSessions.map((s) => {
    const isCurrent = s.session_id === activeSessionId;
    const isAnon = s.session_id === '_anon';
    const labelText = formatSessionOptionLabel(s, { isCurrent, isAnon });
    const opt = el('option', { value: s.session_id }, labelText);
    if (!isAllMode && s.session_id === state.sessionId) opt.selected = true;
    return opt;
  })];
  const sessionSel = el('select', {
    className: 'tiny diag-session-select',
    onChange: (e) => {
      state.sessionId = e.target.value;
      if (state.sessionId === ALL_SESSIONS_VALUE) {
        state.date = state.allDates[0] || null;
      } else {
        const match = state.sessions.find((s) => s.session_id === state.sessionId);
        state.date = match ? match.latest : null;
      }
      state.offset = 0;
      // 切换数据源 = 用户关心的上下文变了, 展开记录不再相关, 清空.
      clearEntryCaches(state);
      try {
        localStorage.setItem(LS_SESSION_KEY, state.sessionId);
        localStorage.setItem(LS_DATE_KEY, state.date || '');
      } catch { /* ignore */ }
      loadLogs(state).then(() => renderAll(root, state));
    },
  }, ...sessionOpts);

  // 日期选项: 单 session 模式用那个 session 的 dates; "全部会话" 模式用并集.
  let dateSource;
  if (isAllMode) {
    dateSource = state.allDates;
  } else {
    const match = state.sessions.find((s) => s.session_id === state.sessionId);
    dateSource = match?.dates || [];
  }
  const dateOpts = dateSource.map((d) => {
    const opt = el('option', { value: d }, formatDate(d));
    if (d === state.date) opt.selected = true;
    return opt;
  });
  const dateSel = el('select', {
    className: 'tiny',
    onChange: (e) => {
      state.date = e.target.value;
      state.offset = 0;
      clearEntryCaches(state);
      try { localStorage.setItem(LS_DATE_KEY, state.date); } catch { /* ignore */ }
      loadLogs(state).then(() => renderAll(root, state));
    },
  }, ...dateOpts);

  const refreshBtn = el('button', {
    className: 'ghost tiny',
    onClick: async () => {
      await loadSessionsList(state);
      ensureSelection(state);
      await loadLogs(state);
      renderAll(root, state);
    },
  }, i18n('diagnostics.logs.refresh_sessions'));

  // 合并视图没有单一文件可导出; backend 会 404 掉, 前端直接 disable + 换文案.
  const exportBtn = el('button', {
    className: 'ghost tiny',
    disabled: !state.sessionId || !state.date || isAllMode,
    title: isAllMode ? i18n('diagnostics.logs.export_disabled_all_mode') : '',
    onClick: () => {
      if (!state.sessionId || !state.date || isAllMode) return;
      const usp = new URLSearchParams({
        session_id: state.sessionId,
        date: state.date,
      });
      window.location.href = `/api/diagnostics/logs/export?${usp.toString()}`;
    },
  }, i18n('diagnostics.logs.export'));

  const sessionLabel = el('span', {
    className: 'diag-selector-label',
    title: i18n('diagnostics.logs.session_help_tooltip'),
  }, i18n('diagnostics.logs.session_label'));
  const dateLabel = el('span', {
    className: 'diag-selector-label',
    title: i18n('diagnostics.logs.date_help_tooltip'),
  }, i18n('diagnostics.logs.date_label'));

  return el('div', { className: 'diag-toolbar diag-selector' },
    el('div', { className: 'diag-toolbar-left' },
      sessionLabel,
      sessionSel,
      dateLabel,
      dateSel,
    ),
    el('div', { className: 'diag-toolbar-right' },
      refreshBtn, exportBtn,
    ),
  );
}

/** Sort sessions so current one is first, then by latest date desc. */
function sortSessionsForDropdown(sessions, activeSessionId) {
  const copy = sessions.slice();
  copy.sort((a, b) => {
    if (a.session_id === activeSessionId) return -1;
    if (b.session_id === activeSessionId) return 1;
    // 把 _anon 放在末尾 (不是某次会话, 是进程级 fallback).
    if (a.session_id === '_anon' && b.session_id !== '_anon') return 1;
    if (b.session_id === '_anon' && a.session_id !== '_anon') return -1;
    if ((b.latest || '') !== (a.latest || '')) {
      return (b.latest || '').localeCompare(a.latest || '');
    }
    return a.session_id.localeCompare(b.session_id);
  });
  return copy;
}

function formatSessionOptionLabel(s, { isCurrent, isAnon }) {
  // 短 id (8 位) 够区分, 长 id 过度挤占 dropdown 宽度.
  const shortId = s.session_id.length > 10 ? s.session_id.slice(0, 10) : s.session_id;
  const latest = formatDate(s.latest || '');
  const prefix = isCurrent
    ? i18n('diagnostics.logs.session_opt_current_prefix')
    : (isAnon ? i18n('diagnostics.logs.session_opt_anon_prefix') : '');
  const suffix = ` · ${latest} · ${s.dates.length} ${i18n('diagnostics.logs.session_opt_days_suffix')}`;
  return `${prefix}${shortId}${suffix}`;
}

function renderToolbar(root, state) {
  // DEBUG 级默认不落盘 (TESTBENCH_LOG_DEBUG=0 或运行期 toggle), 打开
  // 开关后本会话后续写入才会出现 DEBUG 行; 但历史文件里可能已经有
  // 以前跑测试时开过的 DEBUG, 所以 filter 选项常驻可选.
  const levels = ['', 'DEBUG', 'INFO', 'WARNING', 'ERROR'];
  const levelSel = el('select', {
    className: 'tiny',
    title: i18n('diagnostics.logs.level_tooltip'),
    onChange: (e) => {
      state.level = e.target.value || '';
      state.offset = 0;
      // P20 hotfix 3 (round 2): 切 level 过滤等价于换了一批 entry,
      // 旧 toggledKeys 对新集合无参考意义. 和 page_errors.js 对齐.
      clearEntryCaches(state);
      loadLogs(state).then(() => renderAll(root, state));
    },
  }, ...levels.map((l) => {
    const opt = el('option', { value: l },
      l || i18n('diagnostics.logs.all_levels'));
    if (state.level === l) opt.selected = true;
    return opt;
  }));

  const search = el('input', {
    type: 'search',
    className: 'tiny',
    placeholder: i18n('diagnostics.logs.search_placeholder'),
    value: state.keyword,
    onInput: (e) => {
      state.keyword = e.target.value;
      state.offset = 0;
      clearEntryCaches(state);
      if (state._searchDebounce) clearTimeout(state._searchDebounce);
      state._searchDebounce = setTimeout(() => {
        state._searchDebounce = null;
        loadLogs(state).then(() => renderAll(root, state));
      }, 300);
    },
  });

  const opClear = state.op ? el('button', {
    className: 'ghost tiny',
    onClick: () => {
      state.op = '';
      state.offset = 0;
      clearEntryCaches(state);
      loadLogs(state).then(() => renderAll(root, state));
    },
  }, i18n('diagnostics.logs.op_clear', state.op)) : null;

  const autoChk = el('input', {
    type: 'checkbox',
    checked: state.autoRefresh,
    onChange: (e) => { state.autoRefresh = e.target.checked; },
  });
  const autoLabel = el('label', { className: 'diag-checkbox-label' },
    autoChk,
    el('span', {}, i18n('diagnostics.logs.auto_refresh')));

  const manualRefresh = el('button', {
    className: 'ghost tiny',
    onClick: () => { loadLogs(state).then(() => renderAll(root, state)); },
  }, i18n('diagnostics.logs.refresh'));

  return el('div', { className: 'diag-toolbar' },
    el('div', { className: 'diag-toolbar-left' },
      el('span', { className: 'diag-count' },
        i18n('diagnostics.logs.count_fmt', state.matched, state.total)),
      opClear,
    ),
    el('div', { className: 'diag-toolbar-right' },
      levelSel, search, autoLabel, manualRefresh,
    ),
  );
}

function renderFacets(root, state) {
  const opFacet = state.facets?.op || [];
  if (!opFacet.length) return null;
  const wrap = el('div', { className: 'diag-facet-row' });
  const label = el('span', {
    className: 'diag-facet-label',
    title: i18n('diagnostics.logs.op_facet_help'),
  }, i18n('diagnostics.logs.op_facet_label'));
  wrap.append(label);
  for (const [opName, count] of opFacet) {
    const active = state.op === opName;
    // chip 主文本用 op 原字符串 (便于复制到 bug 报告); 中文标签留 tooltip,
    // 否则每个 chip 太宽 dropdown 一眼看不过来 20+ op.
    const friendly = opLabel(opName);
    const desc = opDescription(opName);
    const tooltipParts = [];
    if (friendly) tooltipParts.push(friendly);
    if (desc) tooltipParts.push(desc);
    const tooltip = tooltipParts.join(' — ') || opName;
    const chip = el('button', {
      className: 'ghost tiny diag-facet-chip' + (active ? ' active' : ''),
      title: tooltip,
      onClick: () => {
        state.op = active ? '' : opName;
        state.offset = 0;
        clearEntryCaches(state);
        loadLogs(state).then(() => renderAll(root, state));
      },
    }, `${opName} · ${count}`);
    wrap.append(chip);
  }
  return wrap;
}

function renderList(state) {
  const wrap = el('div', { className: 'diag-log-list' });
  const isAllMode = state.sessionId === ALL_SESSIONS_VALUE;
  for (const entry of state.items) {
    wrap.append(renderEntry(entry, state, { showSession: isAllMode }));
  }
  return wrap;
}

/** Stable identifier for a log entry — survives auto-refresh re-renders.
 *
 * JSONL 没有原生 id, 所以拼 `ts|level|op|session_id|error头|payload键数`.
 * 就算真碰上同秒同 op 的两条, payload 结构通常不同 → 误合风险可忽略.
 */
function entryKey(entry) {
  const parts = [
    String(entry.ts || ''),
    String(entry.level || ''),
    String(entry.op || ''),
    String(entry.session_id || ''),
    String(entry.error || '').slice(0, 40),
    entry.payload ? Object.keys(entry.payload).sort().join(',') : '',
  ];
  return parts.join('|');
}

/** Default expand state for an entry that the user has NOT explicitly
 *  toggled yet. WARN/ERROR 自动展开 (它们通常就是来排查的, 强制先点一下
 *  是纯粹的摩擦); INFO/DEBUG 折叠, 因为量大且大多数只需看摘要.
 */
function defaultOpenFor(level) {
  const lv = (level || '').toUpperCase();
  return lv === 'WARNING' || lv === 'ERROR' || lv === 'WARN';
}

function renderEntry(entry, state, opts = {}) {
  const key = entryKey(entry);
  const toggled = state.toggledKeys?.get(key);
  const initialOpen = typeof toggled === 'boolean'
    ? toggled
    : defaultOpenFor(entry.level);

  const cb = el('div', {
    className: 'cb',
    'data-open': String(initialOpen),
    'data-entry-key': key,
  });
  const header = el('div', { className: 'cb-header' });

  const caret = el('span', { className: 'cb-caret' }, '▸');
  const ts = el('span', { className: 'cb-title' },
    formatLogTimestamp(entry.ts));
  const levelBadge = buildLevelBadge(entry.level);

  // 合并视图里, 每条必须显示来自哪个 session, 否则根本没法排查多客户端
  // 情况. 单 session 视图就不显示 (上面 selector 已经写明了).
  let sessionBadge = null;
  if (opts.showSession && entry.session_id) {
    const sid = String(entry.session_id);
    const shortSid = sid.length > 10 ? sid.slice(0, 10) : sid;
    sessionBadge = el('span', {
      className: 'diag-entry-session-badge',
      title: i18n('diagnostics.logs.session_badge_tooltip_fmt', sid),
    }, shortSid);
  }

  // op 显示: 原 op 字符串 (mono) + 友好中文 label (若 catalog 命中).
  // 鼠标 hover op 整体显示一句话描述, 帮测试人员立刻懂"这条是啥".
  const opRaw = entry.op || '—';
  const friendly = opLabel(opRaw);
  const desc = opDescription(opRaw);
  const opTooltip = desc
    ? (friendly ? `${friendly} — ${desc}` : desc)
    : (friendly || '');
  const opBlock = el('span', {
    className: 'diag-entry-op',
    title: opTooltip || opRaw,
  });
  opBlock.append(el('span', { className: 'mono diag-entry-type' }, opRaw));
  if (friendly) {
    opBlock.append(el('span', { className: 'diag-entry-op-label' }, friendly));
  }

  const preview = el('span', { className: 'cb-preview' },
    shortPreview(entry));

  header.append(caret, ts, levelBadge);
  if (sessionBadge) header.append(sessionBadge);
  header.append(opBlock, preview);
  header.addEventListener('click', () => {
    const open = cb.getAttribute('data-open') === 'true';
    const next = !open;
    cb.setAttribute('data-open', String(next));
    // 记下来: 下次 auto-refresh 重建 DOM 时要保留这个用户意图.
    // 存 true 和 false 都要存 — 否则 WARN 默认展开, 用户点一下折叠,
    // 5s 后又自动展开回去, 又是一次体验事故.
    if (state && state.toggledKeys) {
      state.toggledKeys.set(key, next);
    }
  });

  const body = el('div', { className: 'cb-body diag-entry-body' });
  // 先显示 op 描述 (若 catalog 命中), 让测试人员展开第一眼就看到这是
  // 什么场景. 未命中就跳过, 不强塞"unknown op"噪声.
  const spec = lookupOp(entry.op);
  if (spec) {
    body.append(el('div', { className: 'diag-entry-op-hint' },
      el('span', { className: 'diag-entry-op-hint-label' }, spec.label),
      el('span', { className: 'diag-entry-op-hint-sep' }, '—'),
      el('span', { className: 'diag-entry-op-hint-desc' }, spec.description),
    ));
  }
  if (entry.error) {
    body.append(el('div', { className: 'diag-entry-section diag-entry-error' },
      el('div', { className: 'diag-entry-section-label' },
        i18n('diagnostics.logs.error_label')),
      el('pre', { className: 'mono' }, String(entry.error)),
    ));
  }
  const payloadKeys = entry.payload ? Object.keys(entry.payload) : [];
  if (payloadKeys.length) {
    body.append(el('div', { className: 'diag-entry-section' },
      el('div', { className: 'diag-entry-section-label' },
        i18n('diagnostics.logs.payload_label')),
      el('pre', { className: 'mono' }, safeStringify(entry.payload)),
    ));
  }
  // P25 Day 2 hotfix (2026-04-23): sub-<details> open state must survive
  // auto-refresh. Previously this was a naked ``<details>`` — each 5-second
  // refresh rebuilt ``renderEntry`` from scratch, so the user's "open
  // 原始 JSON" click got wiped. Pattern mirrors the parent-level
  // ``toggledKeys`` (header click → Map), but scoped to the nested
  // details children via ``openSubDetails`` (Set of compound keys).
  body.append(buildStickyDetails(
    state,
    `${key}|raw`,
    i18n('diagnostics.logs.raw_label'),
    el('pre', { className: 'mono' }, safeStringify(entry)),
    { extraClass: 'diag-entry-raw' },
  ));

  cb.append(header, body);
  return cb;
}

/**
 * Build a ``<details>`` whose open state is persisted across re-renders
 * via ``state.openSubDetails`` (Set<string>). ``subKey`` must be unique
 * across concurrently-rendered sub-details on the page — using
 * ``${entryKey}|${slot}`` is the canonical pattern.
 *
 * The ``toggle`` event fires whenever ``open`` flips, so we listen for
 * it (not click) to catch keyboard ENTER and programmatic changes.
 *
 * Not extracted to ``_dom.js`` because it is diagnostics-specific:
 * it needs the ``openSubDetails`` Set (a state-shape concern) plus
 * the ``initialOpen`` semantics (closed by default, opt-in expand).
 * Hoisting it would require either threading ``state`` through a
 * generic helper or standing up a per-module factory — both more
 * overhead than a 20-line local helper.
 */
function buildStickyDetails(state, subKey, summaryText, contentNode, { extraClass = '' } = {}) {
  const set = state.openSubDetails;
  const initialOpen = set?.has(subKey) === true;
  const details = el('details', {
    className: extraClass ? `diag-entry-raw ${extraClass}` : 'diag-entry-raw',
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

function renderPager(root, state) {
  if (state.matched <= state.pageSize) return null;
  const page = Math.floor(state.offset / state.pageSize) + 1;
  const pageCount = Math.max(1, Math.ceil(state.matched / state.pageSize));
  const prev = el('button', {
    className: 'ghost tiny',
    disabled: page <= 1,
    onClick: () => {
      state.offset = Math.max(0, state.offset - state.pageSize);
      loadLogs(state).then(() => renderAll(root, state));
    },
  }, i18n('diagnostics.logs.pager_prev'));
  const next = el('button', {
    className: 'ghost tiny',
    disabled: page >= pageCount,
    onClick: () => {
      state.offset = Math.min((pageCount - 1) * state.pageSize, state.offset + state.pageSize);
      loadLogs(state).then(() => renderAll(root, state));
    },
  }, i18n('diagnostics.logs.pager_next'));
  return el('div', { className: 'diag-pager' },
    prev,
    el('span', { className: 'diag-pager-info' },
      i18n('diagnostics.logs.pager_fmt', page, pageCount)),
    next,
  );
}

//
// ── helpers ─────────────────────────────────────────────────────
//

function formatDate(d) {
  if (!d || d.length !== 8) return d || '—';
  return `${d.slice(0, 4)}-${d.slice(4, 6)}-${d.slice(6, 8)}`;
}

function formatLogTimestamp(iso) {
  // P25 Day 2 hotfix (2026-04-23): switch from ``toISOString()`` (UTC
  // wall clock) to local-wall-clock formatting via
  // :func:`formatIsoReadable`. The old implementation was
  // ``new Date(iso).toISOString().replace('T', ' ').slice(0, 19)``, which
  // silently converted the backend's naive local ISO string (e.g.
  // ``2026-04-23T13:37:00``) to the browser's UTC representation and
  // displayed ``05:37`` for an Asia/Shanghai-based tester — an 8-hour
  // drift that looked like a backend logging bug but was a frontend
  // rendering bug. ``formatIsoReadable`` pads via ``getHours()`` /
  // ``getMinutes()`` / ``getSeconds()`` which honor the local timezone.
  //
  // Same fix is applied to page_errors.js::formatTimestamp in the same
  // patch so the two sibling pages stay consistent.
  return formatIsoReadable(iso);
}

function levelLabel(level) {
  const labels = i18nRaw('diagnostics.logs.level_labels') || {};
  const key = (level || '').toUpperCase();
  return labels[key] || key || '—';
}

function buildLevelBadge(level) {
  const key = (level || 'info').toLowerCase();
  return el('span', {
    className: `badge diag-badge-level diag-level-${key.replace(/[^a-z0-9_]/g, '_')}`,
  }, levelLabel(level));
}

function shortPreview(entry) {
  const err = entry.error && typeof entry.error === 'string' ? entry.error : '';
  const payloadSample = entry.payload
    ? Object.entries(entry.payload)
        .slice(0, 3)
        .map(([k, v]) => {
          let s = v;
          if (typeof v !== 'string') {
            try { s = JSON.stringify(v); } catch { s = String(v); }
          }
          return `${k}=${String(s).slice(0, 40)}`;
        })
        .join(' ')
    : '';
  const raw = err || payloadSample || '';
  if (raw.length <= 180) return raw;
  return raw.slice(0, 180) + '…';
}

function safeStringify(obj) {
  try { return JSON.stringify(obj, null, 2); }
  catch { return String(obj); }
}

function formatBytes(n) {
  const v = Number(n) || 0;
  if (v < 1024) return `${v} B`;
  if (v < 1024 * 1024) return `${(v / 1024).toFixed(1)} KB`;
  if (v < 1024 * 1024 * 1024) return `${(v / (1024 * 1024)).toFixed(2)} MB`;
  return `${(v / (1024 * 1024 * 1024)).toFixed(2)} GB`;
}
