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
import { el } from '../_dom.js';

//: 哪些 key 会被 `/system/open_path` 接受 (DATA_DIR 下). 前端用此集
//: 合决定 [打开] 按钮是 active 还是 disabled + 带解释 tooltip.
const OPENABLE_KEYS = new Set([
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
  };
}

async function loadPaths(state) {
  state.loading = true;
  state.error = null;
  const res = await api.get('/system/paths');
  state.loading = false;
  if (res.ok) {
    state.data = res.data || null;
  } else {
    state.data = null;
    state.error = res.error?.message || `HTTP ${res.status}`;
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

  // Toolbar: 刷新按钮 + 平台徽章.
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
  if (state.data?.platform) {
    toolbar.append(el('span', { className: 'diag-paths-platform' },
      i18n('diagnostics.paths.platform_fmt', state.data.platform)));
  }
  host.append(toolbar);

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

  // 底部提示条.
  host.append(el('div', { className: 'diag-paths-footer' },
    i18n('diagnostics.paths.gitignore_note')));
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
    const detail = res.error?.message
      || res.data?.detail
      || `HTTP ${res.status}`;
    toast.err(i18n('diagnostics.paths.toast.open_failed_fmt', detail));
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
