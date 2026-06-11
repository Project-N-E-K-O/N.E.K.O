/**
 * page_snapshots.js — Diagnostics → Snapshots 子页 (P18 正式版).
 *
 * 替换 P19 阶段的 placeholder. 职责分工与 topbar timeline chip 对齐:
 *
 *   - **chip**: 针对"我想快速撤销最近一两步"场景, 只显示最近 10 条, 只
 *     有 [回退] 一个按钮, 面板小且轻.
 *   - **本子页**: 完整管理. 可以看到热 + 冷全部快照, 能重命名 / 删除 /
 *     查看 payload / 清空 / 手动建 / 回退. 场景是"测试人员排查为什么
 *     快照没合并 / 想腾空间 / 要对照两个时刻的 state".
 *
 * 数据流:
 *   - GET /api/snapshots — 列表 (只有 metadata).
 *   - POST /api/snapshots — 手动建 (trigger=manual).
 *   - GET /api/snapshots/{id} — 完整 payload (查看 modal 会按需取).
 *   - DELETE /api/snapshots/{id} — 删.
 *   - PUT /api/snapshots/{id}/label — 重命名.
 *   - POST /api/snapshots/{id}/rewind — 回退.
 *
 * 没有自动刷新 (不是 live tail 场景): 每次增删/回退后本地立即 refetch
 * 列表并 emit `snapshots:changed` 让其它组件 (chip / topbar session 描述)
 * 也同步. 外部改动 (chip 那边增删 / rewind) 通过 on('snapshots:changed')
 * 反向收到, 再刷一次.
 *
 * 同族踩点预防:
 *   - §3A B1 "改 state 后必须 renderAll": 所有 mutate + fetch 最后一行
 *     `renderAll(state, host)`.
 *   - §3A C3 "append null" 防御: 可选徽章用 `cond ? el(...) : null` 配
 *     `el()` helper 的 null-filter.
 */

import { api } from '../../core/api.js';
import { i18n } from '../../core/i18n.js';
import { store, on, emit, set as setStore } from '../../core/state.js';
import { toast } from '../../core/toast.js';
import { el } from '../_dom.js';
import { registerChecker, unregisterChecker } from '../../core/render_drift_detector.js';

function defaultState() {
  return {
    loading: false,
    items: [],             // metadata 列表, 后端返回 oldest→newest; UI 倒序展示
    maxHot: null,
    debounceSeconds: null,
    loadError: null,
    viewOpen: false,       // 查看 modal 状态
    viewLoading: false,
    viewSnapshot: null,
    viewError: null,
  };
}

// P24 §14.4 M3: abort prev on rapid Refresh / snapshot-create clicks.
let _snapshotsLoadController = null;

async function loadSnapshots(state) {
  if (!store.session) {
    state.items = [];
    state.maxHot = null;
    state.debounceSeconds = null;
    state.loadError = null;
    return;
  }
  if (_snapshotsLoadController) {
    try { _snapshotsLoadController.abort(); } catch { /* ignore */ }
  }
  const controller = new AbortController();
  _snapshotsLoadController = controller;
  state.loading = true;
  const res = await api.get('/api/snapshots', {
    expectedStatuses: [404],
    signal: controller.signal,
  });
  if (res.error?.type === 'aborted') return;
  if (_snapshotsLoadController === controller) _snapshotsLoadController = null;
  state.loading = false;
  if (res.ok) {
    state.items = Array.isArray(res.data?.items) ? res.data.items : [];
    state.maxHot = res.data?.max_hot ?? null;
    state.debounceSeconds = res.data?.debounce_seconds ?? null;
    state.loadError = null;
  } else if (res.status === 404) {
    state.items = [];
    state.maxHot = null;
    state.debounceSeconds = null;
    state.loadError = null;
  } else {
    state.items = [];
    state.maxHot = null;
    state.debounceSeconds = null;
    state.loadError = res.error?.message || `HTTP ${res.status}`;
  }
}

export async function renderSnapshotsPage(host) {
  // P24 Day 6F (§14.4 M2): tear down old listeners before (re-)mounting.
  // 历史版本的注释 "粗粒度 remount 会直接 innerHTML='' 所以不主动 off
  // 也不会泄漏事件" 是**错的** — innerHTML='' 只清 DOM, 而 state.js 的
  // listeners Map 里 fn 引用保留, 每次 remount 都会叠加一个 fn, 触发
  // 一次事件跑 N 倍工作量. page_results / page_run 早已用 host.__offX
  // 这个 pattern 正确 teardown, 这里补齐.
  for (const k of ['__offSnapshotsChanged', '__offSession', '__offDriftChecker']) {
    if (typeof host[k] === 'function') {
      try { host[k](); } catch { /* ignore */ }
      host[k] = null;
    }
  }

  host.innerHTML = '';
  host.classList.add('snapshots-page');
  const state = defaultState();

  await loadSnapshots(state);
  renderAll(state, host);

  // 外部来源变更 — 本页自己 emit 的事件: reason starts with 'page:' —
  // 已同步本地 state, 无需再拉. 外部 (chip 手动建 / rewind) 的 reason
  // 是 'manual_create' / 'rewind' / 'delete' 等, 需要重新 GET 拉取.
  host.__offSnapshotsChanged = on('snapshots:changed', async (payload) => {
    if (payload?.reason?.startsWith('page:')) return;
    if (!host.isConnected) return;
    await loadSnapshots(state);
    renderAll(state, host);
  });
  // 会话切换 → 列表清空重拉. workspace_diagnostics 也订阅了 session:change
  // 并 remount logs 页; snapshots 不强制 remount (切会话后这里显示的是新
  // 会话的 t0:init, 不需要完全重建 DOM).
  host.__offSession = on('session:change', async () => {
    if (!host.isConnected) return;
    await loadSnapshots(state);
    renderAll(state, host);
  });

  // Dev-only drift checker (P24 §3.5): the row count rendered under
  // .snapshots-page must match `state.items.length` whenever a session
  // is active (no-op otherwise). Catches "loaded new items but forgot
  // to renderAll" regressions in this historically-leaky page.
  registerChecker({
    name: 'page_snapshots.row_count',
    event: 'snapshots:changed',
    check: () => {
      if (!host.isConnected) return { ok: true }; // page not mounted
      if (!store.session) return { ok: true };    // empty-state shown instead
      const expected = state.items.length;
      const actual = host.querySelectorAll('.snapshots-row').length;
      if (expected !== actual) {
        return {
          ok: false,
          detail: `state.items.length=${expected} but .snapshots-row count=${actual}`,
          driftKey: `${expected}_vs_${actual}`,
        };
      }
      return { ok: true };
    },
  });
  // When the page unmounts (host disconnected), detector's check() returns
  // ok quickly; no hard unregister needed, but be defensive.
  host.__offDriftChecker = () => unregisterChecker('page_snapshots.row_count');
}

function renderAll(state, host) {
  host.innerHTML = '';

  // 标题 + intro + 机制说明 (折叠, 默认关).
  host.append(
    el('h2', {}, i18n('snapshots.page.title')),
    el('p', { className: 'diag-page-intro' },
      i18n('snapshots.page.intro')),
    el('p', {
      className: 'diag-page-intro',
      style: { fontSize: '12px', color: 'var(--text-tertiary)', marginTop: '-4px' },
    }, i18n('snapshots.page.time_legend')),
    renderMechanismDetails(state),
  );

  if (!store.session) {
    host.append(el('div', { className: 'empty-state' },
      i18n('snapshots.toast.no_session')));
    return;
  }

  // Toolbar.
  const toolbar = el('div', { className: 'snapshots-toolbar' });
  const manualBtn = el('button', {
    className: 'ghost tiny',
    onClick: () => handleManualCreate(state, host),
  }, i18n('snapshots.page.manual_btn'));
  const refreshBtn = el('button', {
    className: 'ghost tiny',
    onClick: async () => {
      await loadSnapshots(state);
      renderAll(state, host);
    },
  }, i18n('snapshots.page.refresh_btn'));
  const clearBtn = el('button', {
    className: 'ghost tiny',
    onClick: () => handleClearAll(state, host),
  }, i18n('snapshots.page.clear_all_btn'));
  toolbar.append(manualBtn, refreshBtn, clearBtn);

  // 摘要.
  const hot = state.items.filter((it) => !it.is_compressed).length;
  const cold = state.items.filter((it) => it.is_compressed).length;
  const summary = el('span', { className: 'snapshots-summary' },
    i18n('snapshots.page.summary_fmt',
      state.items.length, hot, cold, state.maxHot ?? '?'));
  toolbar.append(summary);
  host.append(toolbar);

  // 加载中 / 错误 / 空.
  if (state.loading) {
    host.append(el('div', { className: 'empty-state' },
      i18n('snapshots.page.loading')));
    return;
  }
  if (state.loadError) {
    host.append(el('div', { className: 'empty-state' },
      i18n('snapshots.page.load_failed_fmt', state.loadError)));
    return;
  }
  if (state.items.length === 0) {
    host.append(el('div', { className: 'empty-state' },
      i18n('snapshots.page.empty')));
    return;
  }

  // 表格 (newest-first).
  host.append(renderTable(state, host));

  // 查看 modal.
  if (state.viewOpen) {
    host.append(renderViewModal(state, host));
  }
}

function renderTable(state, host) {
  const table = el('table', { className: 'snapshots-table' });
  const thead = el('thead', {});
  thead.append(
    el('tr', {},
      el('th', {}, i18n('snapshots.col.time')),
      el('th', {}, i18n('snapshots.col.label')),
      el('th', {}, i18n('snapshots.col.trigger')),
      el('th', { className: 'num' }, i18n('snapshots.col.messages')),
      el('th', { className: 'num' }, i18n('snapshots.col.memory')),
      el('th', {}, i18n('snapshots.col.stage')),
      el('th', {}, i18n('snapshots.col.storage')),
      el('th', { className: 'actions' }, i18n('snapshots.col.actions')),
    ),
  );
  table.append(thead);

  const tbody = el('tbody', {});
  const reversed = [...state.items].reverse();
  for (const item of reversed) {
    tbody.append(renderRow(item, state, host));
  }
  table.append(tbody);
  return table;
}

function renderRow(item, state, host) {
  const tr = el('tr', {
    className: 'snapshots-row'
      + (item.is_backup ? ' backup' : '')
      + (item.is_compressed ? ' cold' : ''),
  });

  tr.append(
    el('td', { className: 'time' },
      el('code', {}, item.created_at || ''),
      item.virtual_now
        ? el('div', { className: 'virtual-now' }, '@', item.virtual_now)
        : null,
    ),
    el('td', { className: 'label u-min-width-0' },
      el('span', {
        className: 'u-truncate',
        title: item.label || '(unnamed)',
      }, item.label || '(unnamed)'),
      item.is_backup
        ? el('span', { className: 'badge subtle' },
            i18n('snapshots.badge.backup'))
        : null,
    ),
    el('td', { className: 'trigger' },
      el('code', {}, item.trigger || ''),
      el('div', { className: 'trigger-zh' },
        i18n(`snapshots.trigger.${item.trigger}`) || ''),
    ),
    el('td', { className: 'num' }, String(item.message_count ?? 0)),
    el('td', { className: 'num' }, String(item.memory_file_count ?? 0)),
    el('td', { className: 'stage' }, item.stage || '-'),
    el('td', { className: 'storage' },
      item.is_compressed
        ? i18n('snapshots.storage.cold')
        : i18n('snapshots.storage.hot')),
    el('td', { className: 'actions' }, ...renderRowActions(item, state, host)),
  );
  return tr;
}

function renderRowActions(item, state, host) {
  return [
    el('button', {
      className: 'ghost tiny',
      onClick: () => handleRewind(item, state, host),
    }, i18n('snapshots.action.rewind')),
    el('button', {
      className: 'ghost tiny',
      onClick: () => handleRename(item, state, host),
    }, i18n('snapshots.action.rename')),
    el('button', {
      className: 'ghost tiny',
      onClick: () => openView(item, state, host),
    }, i18n('snapshots.action.view')),
    el('button', {
      className: 'ghost tiny danger',
      onClick: () => handleDelete(item, state, host),
    }, i18n('snapshots.action.delete')),
  ];
}

function renderViewModal(state, host) {
  const modal = el('div', { className: 'snapshots-view-modal' });
  const inner = el('div', { className: 'snapshots-view-inner' });
  modal.append(inner);

  // 关闭按钮 + 外部点击关闭.
  const closeBtn = el('button', {
    className: 'ghost tiny snapshots-view-close',
    onClick: () => closeView(state, host),
  }, i18n('snapshots.view.close'));
  inner.append(closeBtn);

  if (state.viewLoading) {
    inner.append(el('div', {}, i18n('snapshots.page.loading')));
  } else if (state.viewError) {
    inner.append(el('div', { className: 'error' },
      i18n('snapshots.page.load_failed_fmt', state.viewError)));
  } else if (state.viewSnapshot) {
    const snap = state.viewSnapshot;
    const meta = snap.metadata || {};
    inner.append(el('h3', {},
      i18n('snapshots.view.title_fmt', snap.label || snap.id)));
    const metaTable = el('table', { className: 'snapshots-view-meta' });
    const rows = [
      ['meta_id', snap.id],
      ['meta_label', snap.label || '(unnamed)'],
      ['meta_trigger', snap.trigger + ' (' + (i18n(`snapshots.trigger.${snap.trigger}`) || '') + ')'],
      ['meta_created', snap.created_at || ''],
      ['meta_virtual', snap.virtual_now || '-'],
      ['meta_stage', meta.stage || '-'],
      ['meta_msgs', String((snap.messages || []).length)],
      ['meta_mem', String(Object.keys(snap.memory_file_sizes || {}).length)],
      ['meta_backup', snap.is_backup ? '✓' : ''],
      ['meta_storage', meta.is_compressed
        ? i18n('snapshots.storage.cold')
        : i18n('snapshots.storage.hot')],
    ];
    for (const [k, v] of rows) {
      metaTable.append(el('tr', {},
        el('th', {}, i18n(`snapshots.view.${k}`)),
        el('td', {}, v),
      ));
    }
    inner.append(metaTable);

    // Messages preview — 只显示 role + 前 80 字符.
    if (Array.isArray(snap.messages) && snap.messages.length > 0) {
      inner.append(el('div', { className: 'snapshots-view-section-title' },
        `Messages (${snap.messages.length})`));
      const msgList = el('ol', { className: 'snapshots-view-msgs' });
      for (const m of snap.messages) {
        const content = (m.content || '').slice(0, 80);
        msgList.append(el('li', {},
          el('code', {}, m.role || '?'), ' ',
          content + ((m.content || '').length > 80 ? '…' : ''),
        ));
      }
      inner.append(msgList);
    }

    // Memory files preview.
    if (snap.memory_file_sizes && Object.keys(snap.memory_file_sizes).length > 0) {
      inner.append(el('div', { className: 'snapshots-view-section-title' },
        `Memory files (${Object.keys(snap.memory_file_sizes).length})`));
      const filesList = el('ul', { className: 'snapshots-view-files' });
      for (const [relpath, size] of Object.entries(snap.memory_file_sizes)) {
        filesList.append(el('li', {},
          el('code', {}, relpath), ` · ${size}B`));
      }
      inner.append(filesList);
    }
  }

  // 点 modal 背景 (inner 外) 关闭.
  modal.addEventListener('click', (ev) => {
    if (ev.target === modal) closeView(state, host);
  });

  return modal;
}

// ── 动作 ──────────────────────────────────────────────────────────────

async function handleManualCreate(state, host) {
  const raw = window.prompt(i18n('snapshots.prompt.manual_label'), '');
  if (raw === null) return;
  const label = raw.trim() || null;
  const res = await api.post('/api/snapshots', { label });
  if (res.ok) {
    const meta = res.data?.item;
    toast.ok(i18n('snapshots.toast.created_fmt', meta?.label || '(unnamed)'));
    emit('snapshots:changed', { reason: 'page:manual_create', id: meta?.id });
    await loadSnapshots(state);
    renderAll(state, host);
  } else {
    toast.err(i18n('snapshots.toast.create_failed_fmt',
      res.error?.message || `HTTP ${res.status}`));
  }
}

async function handleRename(item, state, host) {
  const raw = window.prompt(
    i18n('snapshots.prompt.rename_label'), item.label || '');
  if (raw === null) return;
  const label = raw.trim();
  if (!label) return;
  const res = await api.put(`/api/snapshots/${item.id}/label`, { label });
  if (res.ok) {
    toast.ok(i18n('snapshots.toast.renamed'));
    emit('snapshots:changed', { reason: 'page:rename', id: item.id });
    await loadSnapshots(state);
    renderAll(state, host);
  } else {
    toast.err(i18n('snapshots.toast.rename_failed_fmt',
      res.error?.message || `HTTP ${res.status}`));
  }
}

async function handleDelete(item, state, host) {
  const ok = window.confirm(
    i18n('snapshots.prompt.delete_confirm', item.label || item.id));
  if (!ok) return;
  const res = await api.delete(`/api/snapshots/${item.id}`);
  if (res.ok) {
    toast.ok(i18n('snapshots.toast.deleted'));
    emit('snapshots:changed', { reason: 'page:delete', id: item.id });
    await loadSnapshots(state);
    renderAll(state, host);
  } else {
    toast.err(i18n('snapshots.toast.delete_failed_fmt',
      res.error?.message || `HTTP ${res.status}`));
  }
}

async function handleClearAll(state, host) {
  const ok = window.confirm(i18n('snapshots.prompt.clear_confirm'));
  if (!ok) return;
  // 后端没有"批量清空"端点 (意图: clear 属于敏感操作, 应走明确 API; 但
  // 目前 P18 router 还没有 /api/snapshots/clear). 前端简单循环删非备份
  // 的 item. 如果列表长 (>100), 这里会发很多请求 — 当前 max_hot=30, 可
  // 以接受. 后续 P20 Reset 页可能加批量 clear endpoint, 届时替换.
  const victims = state.items.filter((it) => !it.is_backup);
  let removed = 0;
  let lastErr = null;
  for (const v of victims) {
    const res = await api.delete(`/api/snapshots/${v.id}`);
    if (res.ok) removed += 1;
    else lastErr = res.error?.message || `HTTP ${res.status}`;
  }
  if (lastErr) {
    toast.err(i18n('snapshots.toast.clear_failed_fmt', lastErr));
  } else {
    toast.ok(i18n('snapshots.toast.cleared_fmt', removed));
  }
  emit('snapshots:changed', { reason: 'page:clear_all', removed });
  await loadSnapshots(state);
  renderAll(state, host);
}

async function handleRewind(item, state, host) {
  const ok = window.confirm(
    i18n('snapshots.prompt.rewind_confirm', item.label || item.id));
  if (!ok) return;
  const res = await api.post(`/api/snapshots/${item.id}/rewind`, {});
  if (res.ok) {
    const dropped = res.data?.dropped_count ?? 0;
    toast.ok(i18n('snapshots.toast.rewound_fmt',
      item.label || item.id, dropped));
    // ⚠️ P24 sweep (2026-04-22, §4.27 #105 同族 sweep): 同 topbar_timeline_chip
    // 的 doRewind — rewind 到早期快照可能让 persona / memory / messages 归空,
    // 触发 New Session 同款级联风暴. LESSONS §7 #20 判据: 任何涉及"状态清
    // 零"可能的操作一律 reload. 原 surgical (emit 事件 + setStore + loadSnapshots
    // + renderAll) 删, 单次 reload 更稳.
    setTimeout(() => {
      try { window.location.reload(); }
      catch { /* jsdom / headless */ }
    }, 300);
  } else {
    toast.err(i18n('snapshots.toast.rewind_failed_fmt',
      res.error?.message || `HTTP ${res.status}`));
  }
  // 保留: rewind 失败路径仍需 re-render 当前子页显示错误态.
  if (!res.ok) renderAll(state, host);
}

async function openView(item, state, host) {
  state.viewOpen = true;
  state.viewLoading = true;
  state.viewSnapshot = null;
  state.viewError = null;
  renderAll(state, host);
  const res = await api.get(
    `/api/snapshots/${item.id}?include_memory_files=0`,
    { expectedStatuses: [404] },
  );
  state.viewLoading = false;
  if (res.ok) {
    state.viewSnapshot = res.data?.item || null;
  } else {
    state.viewError = res.error?.message || `HTTP ${res.status}`;
  }
  renderAll(state, host);
}

function closeView(state, host) {
  state.viewOpen = false;
  state.viewSnapshot = null;
  state.viewError = null;
  state.viewLoading = false;
  renderAll(state, host);
}

// ── 机制说明 (用户点开才读, 默认折叠以免占页面高度) ─────────────────
//
// 把冷热分层 / debounce 合并 / pre_rewind_backup / 回退截断 / 会话级生命
// 周期这几条规则一次性摆出来. 参数 (max_hot / debounce_seconds) 来自后端
// `/api/snapshots` 响应, 真实反映当前实例的配置 — 未来 Settings 改了这
// 俩值, UI 说明会跟着变, 不用手动同步文案.
function renderMechanismDetails(state) {
  const maxHot = state.maxHot ?? 30;
  const debounce = state.debounceSeconds ?? 5;
  return el('details', { className: 'snapshots-mechanism' },
    el('summary', {}, i18n('snapshots.page.mechanism_heading')),
    el('p', { className: 'snapshots-mechanism-intro' },
      i18n('snapshots.page.mechanism_intro')),
    el('ul', { className: 'snapshots-mechanism-list' },
      el('li', {}, i18n('snapshots.page.mechanism_point_hotcold_fmt', maxHot)),
      el('li', {}, i18n('snapshots.page.mechanism_point_debounce_fmt', debounce)),
      el('li', {}, i18n('snapshots.page.mechanism_point_backup')),
      el('li', {}, i18n('snapshots.page.mechanism_point_rewind')),
      el('li', {}, i18n('snapshots.page.mechanism_point_reset')),
      el('li', {}, i18n('snapshots.page.mechanism_point_safety')),
    ),
  );
}
