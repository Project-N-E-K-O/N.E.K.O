/**
 * page_paths.js — Diagnostics → Paths 子页 (P20).
 *
 * 替换 P19 阶段的 placeholder. 列出所有 `testbench_data` 下的目录 +
 * code-side 只读目录 (docs / builtin schemas / builtin templates), 每
 * 行提供:
 *   - path (原生分隔符, `<code>` 展示便于复制)
 *   - 存在标记 (✓ / ✗)
 *   - 大小 + 文件数 (递归统计, 由后端 `/system/paths` 返回)
 *   - [Copy path] — 走 navigator.clipboard.writeText
 *   - [在文件管理器中打开] — POST /system/open_path, **仅对 testbench_data
 *     子路径启用**, code-side 条目 disabled (tooltip 解释原因)
 *   - `?` tooltip — 解释"这个目录放什么"
 *
 * 数据流: mount 时拉一次 `/system/paths`, 提供 [刷新] 按钮手动重拉.
 * 没有自动 polling — 目录大小在测试人员查问题的几秒内几乎不变, 定时
 * 拉反而会打断他们看列表.
 *
 * 边界/安全:
 *   - 后端 `open_path` 会拒绝 DATA_DIR 之外的路径 (403). 前端按 `key`
 *     是否在白名单里提前 disable 按钮, 是双保险: 即使 JS 被改, 后端
 *     仍然把关.
 *   - `navigator.clipboard.writeText` 在 http://localhost 一定可用
 *     (仅 https 或 localhost 允许); 失败给 toast 提示请手动复制.
 */

import { api } from '../../core/api.js';
import { i18n } from '../../core/i18n.js';
import { toast } from '../../core/toast.js';
import { store } from '../../core/state.js';
import { el } from '../_dom.js';
import { openSessionExportModal } from '../session_export_modal.js';

//: 哪些 key 会被 `/system/open_path` 接受 (DATA_DIR 下). 前端用此集
//: 合决定 [打开] 按钮是 active 还是 disabled + 带解释 tooltip.
//:
//: `data_root` 是顶部卡片用的特殊 key (对应 testbench_data/ 本身), 不
//: 属于 `/system/paths` 的 entries 列表, 但同样在 DATA_DIR 白名单内 —
//: 后端 `_path_is_inside_data_dir` 对 path == DATA_DIR 也会返回 True,
//: `handleOpen` 走 path-based 请求可以直接打开整体数据目录. 早期遗漏
//: 此项导致 "数据目录 (整体 gitignored)" 卡片的 [打开] 按钮被当成代码
//: 侧只读路径而灰显.
const OPENABLE_KEYS = new Set([
  'data_root',
  'current_sandbox',
  'current_session_log',
  'sandboxes_all',
  'logs_all',
  'saved_sessions',
  'autosave',
  'exports',
  'user_schemas',
  'user_dialog_templates',
]);

function defaultState() {
  return {
    loading: false,
    data: null,        // /system/paths 响应: { data_root, entries, platform }
    error: null,
    // P24 §15.2 A/B + §3.1: orphan sandbox triage + health card
    health: null,      // /system/health 响应: { status, checks, checked_at }
    orphans: null,     // /system/orphans 响应: { orphans, scanned_at, total_bytes }
    orphansError: null,
  };
}

// P24 §14.4 M3: abort prev trio on rapid Refresh clicks.
let _pathsLoadController = null;

async function loadPaths(state) {
  if (_pathsLoadController) {
    try { _pathsLoadController.abort(); } catch { /* ignore */ }
  }
  const controller = new AbortController();
  _pathsLoadController = controller;
  state.loading = true;
  state.error = null;
  // Parallel fetches: paths + health + orphans all load at once so the
  // page renders in one pass with full data. None of the three blocks
  // is critical for the others — a failed /orphans still lets /paths
  // show, etc. 同一 AbortController 给三个请求, 任一被 abort 即全组放弃.
  const [pathsRes, healthRes, orphansRes] = await Promise.all([
    api.get('/system/paths', { signal: controller.signal }),
    api.get('/system/health', { signal: controller.signal }),
    api.get('/system/orphans', { signal: controller.signal }),
  ]);
  if (pathsRes.error?.type === 'aborted'
    || healthRes.error?.type === 'aborted'
    || orphansRes.error?.type === 'aborted') return;
  if (_pathsLoadController === controller) _pathsLoadController = null;
  state.loading = false;
  if (pathsRes.ok) state.data = pathsRes.data || null;
  else {
    state.data = null;
    state.error = pathsRes.error?.message || `HTTP ${pathsRes.status}`;
  }
  state.health = healthRes.ok ? healthRes.data : null;
  if (orphansRes.ok) {
    state.orphans = orphansRes.data || null;
    state.orphansError = null;
  } else {
    state.orphans = null;
    state.orphansError = orphansRes.error?.message || `HTTP ${orphansRes.status}`;
  }
}

export async function renderPathsPage(host) {
  host.innerHTML = '';
  host.classList.add('diag-paths');
  const state = defaultState();
  await loadPaths(state);
  renderAll(state, host);
}

function renderAll(state, host) {
  host.innerHTML = '';

  host.append(
    el('h2', {}, i18n('diagnostics.paths.title')),
    el('p', { className: 'diag-page-intro' },
      i18n('diagnostics.paths.intro')),
  );

  // Toolbar: 刷新按钮 + 平台徽章 + P23 Export sandbox snapshot.
  const toolbar = el('div', { className: 'diag-paths-toolbar' });
  toolbar.append(
    el('button', {
      className: 'ghost tiny',
      onClick: async () => {
        await loadPaths(state);
        renderAll(state, host);
      },
    }, i18n('diagnostics.paths.refresh_btn')),
  );
  // P23 sandbox-snapshot export: pre-selects scope=full + format=json +
  // include_memory=true so the resulting file is compatible with
  // `POST /api/session/import` and captures the entire sandbox on disk.
  // The button is disabled (with an explanatory tooltip) when there is
  // no active session, so Diagnostics → Paths stays functional for
  // "inspect directories only" workflows.
  const hasSession = !!store.session;
  const exportBtn = el('button', {
    className: 'ghost tiny',
    title: hasSession
      ? i18n('diagnostics.paths.action.export_sandbox_hint')
      : i18n('diagnostics.paths.action.export_sandbox_disabled'),
    onClick: () => {
      if (!hasSession) {
        toast.info(i18n('session.no_active'));
        return;
      }
      openSessionExportModal({
        scope: 'full',
        format: 'json',
        include_memory: true,
        subtitle: i18n('diagnostics.paths.export_modal_subtitle'),
      });
    },
  }, i18n('diagnostics.paths.action.export_sandbox'));
  exportBtn.disabled = !hasSession;
  toolbar.append(exportBtn);
  if (state.data?.platform) {
    toolbar.append(el('span', { className: 'diag-paths-platform' },
      i18n('diagnostics.paths.platform_fmt', state.data.platform)));
  }
  host.append(toolbar);

  // P24 §3.1 H1: system health card at the top of the Paths page.
  // Non-blocking — even if the health fetch fails we still render the
  // rest of the page (health is an indicator, not a gate).
  if (state.health) {
    host.append(renderHealthCard(state.health));
  }

  if (state.loading) {
    host.append(el('div', { className: 'empty-state' },
      i18n('diagnostics.paths.loading')));
    return;
  }
  if (state.error) {
    host.append(el('div', { className: 'empty-state' },
      i18n('diagnostics.paths.load_failed_fmt', state.error)));
    return;
  }
  if (!state.data) {
    host.append(el('div', { className: 'empty-state' },
      i18n('diagnostics.paths.empty')));
    return;
  }

  // data_root 大卡片 (突出"本目录整体 gitignore" 这个信息).
  host.append(renderDataRootCard(state.data.data_root));

  // 按组分段显示: session / shared / code. 分组由 entries 里 key 决定,
  // 用 switch-based 归属表避免后端 / 前端各加一处维护成本.
  const grouped = groupEntries(state.data.entries || []);
  if (grouped.session.length > 0) {
    host.append(renderGroup('session', grouped.session));
  }
  host.append(renderGroup('shared', grouped.shared));
  host.append(renderGroup('code', grouped.code));

  // P24 §15.2 B / P-D: orphan sandbox section. Always rendered (even
  // if empty — empty state matters so user knows orphan detection is
  // working). Placed after the main groups since it's a separate
  // "triage this stale data" activity, not part of the normal
  // "where is my data?" flow.
  host.append(renderOrphansSection(state, host));

  // 底部提示条.
  host.append(el('div', { className: 'diag-paths-footer' },
    i18n('diagnostics.paths.gitignore_note')));
}


// ── P24 §3.1 H1 health card ─────────────────────────────────────────

function formatCheckValue(checkKey, info) {
  if (checkKey === 'autosave_scheduler') {
    if (info.alive === null) return i18n('diagnostics.paths.health.no_session');
    return info.alive
      ? i18n('diagnostics.paths.health.scheduler_alive')
      : i18n('diagnostics.paths.health.scheduler_dead');
  }
  if (checkKey === 'orphan_sandboxes') {
    return i18n('diagnostics.paths.health.orphans_count', info.value ?? '?');
  }
  if (checkKey === 'disk_free_gb') {
    return info.value === null ? '?' : `${info.value} GB`;
  }
  if (checkKey === 'log_dir_size_mb') {
    return info.value === null ? '?' : `${info.value} MB`;
  }
  if (checkKey === 'diagnostics_errors') {
    return i18n('diagnostics.paths.health.errors_count', info.value ?? '?');
  }
  return String(info.value ?? '—');
}

function renderHealthCard(health) {
  const wrap = el('div', {
    className: `diag-paths-health-card diag-paths-health-${health.status}`,
  });
  const title = el('div', { className: 'diag-paths-health-title' });
  const statusLabel = i18n(`diagnostics.paths.health.status.${health.status}`)
    || health.status;
  title.append(
    el('span', { className: 'diag-paths-health-label' },
      i18n('diagnostics.paths.health.title')),
    el('span', {
      className: `diag-paths-health-badge badge-${health.status}`,
    }, statusLabel),
  );
  wrap.append(title);

  // Problem summary: list each non-healthy check with human-readable
  // detail, so users know *why* the status is warning/critical without
  // having to eyeball all 5 rows. Only shown when status != healthy.
  const checks = health.checks || {};
  if (health.status !== 'healthy') {
    const problems = el('div', { className: 'diag-paths-health-problems' });
    problems.append(el('strong', {},
      i18n('diagnostics.paths.health.problem_heading')));
    for (const [checkKey, info] of Object.entries(checks)) {
      const s = info.status || 'healthy';
      if (s === 'healthy') continue;
      const labelText = i18n(`diagnostics.paths.health.check.${checkKey}`)
        || checkKey;
      const valueText = formatCheckValue(checkKey, info);
      const thresholdText = i18n(
        `diagnostics.paths.health.threshold_${s}`,
        info.threshold_warn, info.threshold_critical, checkKey,
      );
      const adviceText = i18n(`diagnostics.paths.health.advice.${checkKey}`) || '';
      problems.append(el('div', { className: 'diag-paths-health-problem-item' },
        '• ', el('strong', {}, `${labelText}: `),
        valueText,
        thresholdText ? ' ' : '',
        thresholdText ? el('span', { className: 'hint' }, thresholdText) : null,
        adviceText ? ' — ' : '',
        adviceText ? el('span', {}, adviceText) : null,
      ));
    }
    wrap.append(problems);
  }

  // Checks grid: per-check row with label / value / status color.
  const grid = el('div', { className: 'diag-paths-health-grid' });
  for (const [checkKey, info] of Object.entries(checks)) {
    const row = el('div', {
      className: `diag-paths-health-row status-${info.status || 'healthy'}`,
    });
    const labelText = i18n(`diagnostics.paths.health.check.${checkKey}`)
      || checkKey;
    row.append(
      el('span', { className: 'diag-paths-health-key' }, labelText),
      el('span', { className: 'diag-paths-health-value' },
        formatCheckValue(checkKey, info)),
    );
    grid.append(row);
  }
  wrap.append(grid);

  if (health.checked_at) {
    wrap.append(el('div', { className: 'diag-paths-health-timestamp' },
      i18n('diagnostics.paths.health.checked_at_fmt', health.checked_at)));
  }
  return wrap;
}


// ── P24 §15.2 B / P-D orphan sandbox section ────────────────────────

function renderOrphansSection(state, host) {
  const wrap = el('div', { className: 'diag-paths-group diag-paths-orphans' });
  wrap.append(el('h3', { className: 'diag-paths-group-title' },
    i18n('diagnostics.paths.orphans.title')));
  wrap.append(el('p', { className: 'diag-paths-group-intro' },
    i18n('diagnostics.paths.orphans.intro')));

  if (state.orphansError) {
    wrap.append(el('div', { className: 'empty-state' },
      i18n('diagnostics.paths.orphans.load_failed_fmt', state.orphansError)));
    return wrap;
  }
  const orphans = state.orphans?.orphans || [];
  if (orphans.length === 0) {
    wrap.append(el('div', { className: 'empty-state' },
      i18n('diagnostics.paths.orphans.empty')));
    return wrap;
  }

  const emptyCount = orphans.filter((o) => (o.size_bytes || 0) === 0).length;

  const meta = el('div', { className: 'diag-paths-orphans-meta' },
    i18n('diagnostics.paths.orphans.summary_fmt',
      orphans.length, formatBytes(state.orphans?.total_bytes || 0)),
  );
  wrap.append(meta);

  // Bulk "clear empty (0-byte) orphans" button — always visible so users
  // know the capability exists; disabled when emptyCount === 0 with a
  // tooltip explaining why (usually because boot_cleanup already took
  // care of them on the previous restart).
  const toolbar = el('div', { className: 'orphans-toolbar' });
  // Button is bulk-action scale (not tiny), so users don't miss it —
  // this is the one-click "cleanup the easy ones" affordance.
  const clearEmptyBtn = el('button', {
    className: emptyCount > 0 ? 'primary' : 'ghost',
    title: emptyCount > 0
      ? i18n('diagnostics.paths.orphans.clear_empty_hint')
      : i18n('diagnostics.paths.orphans.clear_empty_disabled_hint'),
    onClick: () => {
      if (emptyCount === 0) {
        toast.info(i18n('diagnostics.paths.orphans.clear_empty_none_toast'));
        return;
      }
      confirmClearEmpty(state, host, emptyCount);
    },
  }, i18n('diagnostics.paths.orphans.clear_empty_btn', emptyCount));
  if (emptyCount === 0) clearEmptyBtn.disabled = true;
  toolbar.append(clearEmptyBtn);
  wrap.append(toolbar);

  const table = el('table', { className: 'diag-paths-table' });
  const thead = el('thead', {});
  thead.append(el('tr', {},
    el('th', {}, i18n('diagnostics.paths.orphans.col.session_id')),
    el('th', { className: 'num' }, i18n('diagnostics.paths.orphans.col.size')),
    el('th', {}, i18n('diagnostics.paths.orphans.col.mtime')),
    el('th', { className: 'actions' }, i18n('diagnostics.paths.col.actions')),
  ));
  table.append(thead);

  const tbody = el('tbody', {});
  for (const orphan of orphans) {
    tbody.append(renderOrphanRow(orphan, state, host));
  }
  table.append(tbody);
  wrap.append(table);
  return wrap;
}

function renderOrphanRow(orphan, state, host) {
  const tr = el('tr', { className: 'diag-paths-orphan-row' });
  tr.append(
    el('td', {},
      el('code', {
        className: 'u-truncate',
        title: orphan.session_id || '',
      }, orphan.session_id || '?'),
      el('div', { className: 'hint' }, orphan.path || ''),
    ),
    el('td', { className: 'num' }, formatBytes(orphan.size_bytes || 0)),
    el('td', {}, orphan.mtime || '—'),
    el('td', { className: 'actions' },
      el('button', {
        className: 'ghost tiny danger',
        onClick: () => confirmDeleteOrphan(orphan, state, host),
      }, i18n('diagnostics.paths.orphans.delete_btn')),
    ),
  );
  return tr;
}

async function confirmClearEmpty(state, host, emptyCount) {
  const message = i18n('diagnostics.paths.orphans.confirm_clear_empty_fmt',
    emptyCount);
  if (!window.confirm(message)) return;
  const res = await api.post('/api/system/orphans/clear_empty', {});
  if (!res.ok) {
    toast.err(i18n('diagnostics.paths.orphans.clear_empty_err'),
      { message: res.error?.message || '' });
    return;
  }
  const data = res.data || {};
  const cleared = data.cleared || 0;
  const errors = data.errors || [];
  if (errors.length === 0) {
    toast.ok(i18n('diagnostics.paths.orphans.clear_empty_ok_fmt', cleared));
  } else {
    toast.warn(
      i18n('diagnostics.paths.orphans.clear_empty_partial_fmt',
        cleared, errors.length),
      { message: errors.map((e) => `${e.session_id}: ${e.message}`).join(' · ') },
    );
  }
  await loadPaths(state);
  renderAll(state, host);
}

async function confirmDeleteOrphan(orphan, state, host) {
  // Simple confirm — no extra modal for now; consistent with other
  // "destructive action" patterns in diagnostics pages. Browser's
  // native confirm is intentionally "ugly" for danger ops.
  const message = i18n(
    'diagnostics.paths.orphans.confirm_delete_fmt',
    orphan.session_id,
    formatBytes(orphan.size_bytes || 0),
  );
  if (!window.confirm(message)) return;

  const res = await api.delete(
    `/system/orphans/${encodeURIComponent(orphan.session_id)}`,
    { expectedStatuses: [400, 404, 409] },
  );
  if (res.ok) {
    if (res.data?.fully_removed) {
      toast.ok(i18n('diagnostics.paths.orphans.delete_ok_fmt',
        orphan.session_id,
        formatBytes(res.data.deleted_bytes || 0)));
    } else {
      toast.warn(
        i18n('diagnostics.paths.orphans.delete_partial'),
        { message: i18n('diagnostics.paths.orphans.delete_partial_detail_fmt',
          formatBytes(res.data?.remaining_bytes || 0)) },
      );
    }
  } else {
    toast.err(
      i18n('diagnostics.paths.orphans.delete_err'),
      { message: res.error?.message || '' },
    );
  }
  // Re-fetch & re-render so the list reflects the new state.
  await loadPaths(state);
  renderAll(state, host);
}

function groupEntries(entries) {
  const SESSION_KEYS = new Set(['current_sandbox', 'current_session_log']);
  const CODE_KEYS = new Set([
    'code_dir', 'builtin_schemas', 'builtin_dialog_templates', 'docs',
  ]);
  const session = [];
  const shared = [];
  const code = [];
  for (const it of entries) {
    if (SESSION_KEYS.has(it.key)) session.push(it);
    else if (CODE_KEYS.has(it.key)) code.push(it);
    else shared.push(it);
  }
  return { session, shared, code };
}

function renderDataRootCard(root) {
  const card = el('div', { className: 'diag-paths-root-card' });
  card.append(
    el('div', { className: 'diag-paths-root-label' },
      i18n('diagnostics.paths.data_root_label')),
    el('code', { className: 'diag-paths-root-path' },
      root?.path || '?'),
    el('div', { className: 'diag-paths-root-meta' },
      i18n('diagnostics.paths.data_root_meta_fmt',
        formatBytes(root?.size_bytes || 0),
        root?.file_count || 0)),
    renderActions('data_root', root),
  );
  return card;
}

function renderGroup(groupKey, entries) {
  const wrap = el('div', { className: 'diag-paths-group' });
  wrap.append(el('h3', { className: 'diag-paths-group-title' },
    i18n(`diagnostics.paths.group.${groupKey}.title`)));
  wrap.append(el('p', { className: 'diag-paths-group-intro' },
    i18n(`diagnostics.paths.group.${groupKey}.intro`)));

  const table = el('table', { className: 'diag-paths-table' });
  const thead = el('thead', {});
  thead.append(el('tr', {},
    el('th', {}, i18n('diagnostics.paths.col.name')),
    el('th', {}, i18n('diagnostics.paths.col.path')),
    el('th', { className: 'num' }, i18n('diagnostics.paths.col.size')),
    el('th', { className: 'num' }, i18n('diagnostics.paths.col.files')),
    el('th', {}, i18n('diagnostics.paths.col.exists')),
    el('th', { className: 'actions' }, i18n('diagnostics.paths.col.actions')),
  ));
  table.append(thead);

  const tbody = el('tbody', {});
  for (const it of entries) {
    tbody.append(renderRow(it));
  }
  table.append(tbody);
  wrap.append(table);
  return wrap;
}

function renderRow(item) {
  const tr = el('tr', {
    className: 'diag-paths-row'
      + (item.session_scoped ? ' session-scoped' : '')
      + (!item.exists ? ' missing' : ''),
  });

  // Name 列: 本地化 label + `?` tooltip.
  const nameCell = el('td', { className: 'name' });
  nameCell.append(
    el('div', { className: 'label' },
      i18n(`diagnostics.paths.label.${item.key}`) || item.key),
    el('span', {
      className: 'hint-marker',
      title: i18n(`diagnostics.paths.hint.${item.key}`) || '',
    }, '?'),
  );
  if (item.session_scoped) {
    nameCell.append(el('span', { className: 'badge subtle' },
      i18n('diagnostics.paths.badge_session')));
  }
  tr.append(nameCell);

  tr.append(
    el('td', { className: 'path' },
      el('code', {}, item.path || '')),
    el('td', { className: 'num' },
      item.exists ? formatBytes(item.size_bytes || 0) : '-'),
    el('td', { className: 'num' },
      item.exists ? String(item.file_count || 0) : '-'),
    el('td', { className: 'exists' },
      item.exists
        ? el('span', { className: 'ok' }, '✓')
        : el('span', { className: 'missing-mark' }, '✗')),
    el('td', { className: 'actions' }, renderActions(item.key, item)),
  );
  return tr;
}

function renderActions(key, item) {
  const wrap = el('div', { className: 'diag-paths-actions' });
  wrap.append(el('button', {
    className: 'ghost tiny',
    onClick: () => handleCopy(item.path),
  }, i18n('diagnostics.paths.action.copy')));

  const openable = OPENABLE_KEYS.has(key) && item.exists;
  wrap.append(el('button', {
    className: 'ghost tiny',
    disabled: !openable,
    title: !item.exists
      ? i18n('diagnostics.paths.action.open_disabled_missing')
      : (!OPENABLE_KEYS.has(key)
        ? i18n('diagnostics.paths.action.open_disabled_readonly')
        : ''),
    onClick: () => openable && handleOpen(item.path),
  }, i18n('diagnostics.paths.action.open')));
  return wrap;
}

async function handleCopy(path) {
  if (!path) return;
  try {
    await navigator.clipboard.writeText(path);
    toast.ok(i18n('diagnostics.paths.toast.copied'));
  } catch {
    // Clipboard API 在 insecure context (例如老的 http://) 会拒绝;
    // 在 127.0.0.1 不会, 但用户如果把 bind host 改了就要兜底.
    toast.err(i18n('diagnostics.paths.toast.copy_failed'));
  }
}

async function handleOpen(path) {
  const res = await api.post('/system/open_path', { path });
  if (res.ok) {
    toast.ok(i18n('diagnostics.paths.toast.opened'));
  } else {
    toast.err(i18n('diagnostics.paths.toast.open_failed_fmt',
      res.error?.message || `HTTP ${res.status}`));
  }
}

// ── helpers ────────────────────────────────────────────────────────

function formatBytes(n) {
  if (!Number.isFinite(n) || n <= 0) return '0 B';
  const UNITS = ['B', 'KB', 'MB', 'GB'];
  let v = n;
  let u = 0;
  while (v >= 1024 && u < UNITS.length - 1) {
    v /= 1024;
    u += 1;
  }
  return `${v < 10 && u > 0 ? v.toFixed(1) : Math.round(v)} ${UNITS[u]}`;
}
