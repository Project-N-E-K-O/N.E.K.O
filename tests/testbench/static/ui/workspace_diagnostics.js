/**
 * workspace_diagnostics.js — Diagnostics workspace (P04 临时版).
 *
 * 本版本只做一件事: 列出 `core/errors_bus.js` 收集的所有错误, 便于点击顶栏
 * Err 徽章后能立刻看到具体现象. P19 会按 PLAN 替换为 Logs / Errors /
 * Snapshots / Paths / Reset 五子页的完整版, 届时本文件会被拆分.
 *
 * 功能:
 *   - 顶部工具栏: 错误数 / [制造测试错误] / [展开全部] / [折叠全部] / [清空]
 *   - 列表: 每条错误为一个 CollapsibleBlock, 标题显示 `时间 · 来源 · 类型 · 摘要`,
 *     展开后打印完整 detail (JSON 格式化)
 *   - 空态: 友好文案
 *   - 自动跟随 `errors:change` 事件全量重渲染 (错误通常 O(10) 级, 成本可忽略)
 */

import { i18n, i18nRaw } from '../core/i18n.js';
import { store, on } from '../core/state.js';
import { clearErrors, recordError } from '../core/errors_bus.js';
import { toast } from '../core/toast.js';

function el(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v == null) continue;
    if (k === 'className') node.className = v;
    else if (k === 'onClick') node.addEventListener('click', v);
    else if (k === 'style' && typeof v === 'object') Object.assign(node.style, v);
    else if (k.startsWith('data-')) node.setAttribute(k, v);
    else if (k === 'title') node.title = v;
    else node[k] = v;
  }
  for (const c of children.flat()) {
    if (c == null || c === false) continue;
    node.append(c instanceof Node ? c : document.createTextNode(String(c)));
  }
  return node;
}

function formatTimestamp(iso) {
  if (!iso) return '—';
  try {
    return new Date(iso).toISOString().replace('T', ' ').slice(0, 19);
  } catch {
    return iso;
  }
}

function sourceLabel(source) {
  const labels = i18nRaw('diagnostics.errors.sources') || {};
  return labels[source] || source || '—';
}

function shortMessage(entry) {
  // 防御: errors_bus 会把 message 归一化成字符串, 但万一旧数据/第三方塞进对象,
  // 这里再兜底一次, 不让单条坏数据整段渲染崩溃.
  let raw = entry.message ?? entry.type ?? '';
  if (typeof raw !== 'string') {
    try { raw = JSON.stringify(raw); } catch { raw = String(raw); }
  }
  if (raw.length <= 180) return raw;
  return raw.slice(0, 180) + '…';
}

function formatDetail(entry) {
  const snapshot = {
    id: entry.id,
    at: entry.at,
    source: entry.source,
    type: entry.type,
    message: entry.message,
    url: entry.url,
    method: entry.method,
    status: entry.status,
    detail: entry.detail,
  };
  try {
    return JSON.stringify(snapshot, null, 2);
  } catch {
    return String(entry);
  }
}

export function mountDiagnosticsWorkspace(host) {
  host.innerHTML = '';

  const root = el('div', {
    style: { padding: 'var(--density-lg)', maxWidth: '1200px' },
  });
  host.append(root);

  root.append(
    el('h2', {}, i18n('diagnostics.errors.heading')),
    el('p', { className: 'muted', style: { marginTop: 0, lineHeight: 1.55 } },
      i18n('diagnostics.errors.notice')),
  );

  const toolbar = el('div', {
    className: 'row',
    style: { marginBottom: 'var(--density-sm)', justifyContent: 'space-between' },
  });
  const countLabel = el('span', { className: 'muted' });
  const btnTest = el('button', {
    className: 'ghost',
    onClick: () => {
      recordError({
        source: 'js',
        type: 'SyntheticTestError',
        message: '人工触发的测试错误 (用于验证诊断面板).',
        detail: { triggered_by: 'diagnostics_ui', at_local: new Date().toString() },
      });
      toast.info(i18n('diagnostics.errors.trigger_test_done'));
    },
  }, i18n('diagnostics.errors.trigger_test'));
  const btnExpand = el('button', {
    className: 'ghost',
    onClick: () => setAllOpen(true),
  }, i18n('diagnostics.errors.expand_all'));
  const btnCollapse = el('button', {
    className: 'ghost',
    onClick: () => setAllOpen(false),
  }, i18n('diagnostics.errors.collapse_all'));
  const btnClear = el('button', {
    className: 'danger',
    onClick: () => {
      const n = clearErrors();
      toast.ok(i18n('diagnostics.errors.cleared', n));
    },
  }, i18n('diagnostics.errors.clear'));

  toolbar.append(
    countLabel,
    el('div', { className: 'row' }, btnTest, btnExpand, btnCollapse, btnClear),
  );
  root.append(toolbar);

  const listHost = el('div', {});
  root.append(listHost);

  function setAllOpen(open) {
    for (const cb of listHost.querySelectorAll('.cb')) {
      cb.setAttribute('data-open', String(!!open));
    }
  }

  function render() {
    const errs = (store.errors || []).slice().reverse();  // 新的在上面
    countLabel.textContent = errs.length === 0
      ? ''
      : i18n('diagnostics.errors.count', errs.length);
    btnClear.disabled = errs.length === 0;
    btnExpand.disabled = errs.length === 0;
    btnCollapse.disabled = errs.length === 0;

    listHost.innerHTML = '';
    if (errs.length === 0) {
      listHost.append(el('div', { className: 'empty-state' },
        i18n('diagnostics.errors.empty')));
      return;
    }
    for (const entry of errs) {
      // 单条渲染失败不应连累整页 — 塞一个降级 placeholder + console.
      try {
        listHost.append(renderEntry(entry));
      } catch (err) {
        console.error('[diagnostics] renderEntry failed for', entry, err);
        listHost.append(el('div', { className: 'cb', 'data-open': 'true' },
          el('div', { className: 'cb-header' },
            el('span', { className: 'badge err' }, 'render-fail'),
            el('span', { className: 'cb-title' }, entry.id || '?'),
          ),
          el('pre', { className: 'cb-body' }, String(err) + '\n\n' + JSON.stringify(entry, null, 2)),
        ));
      }
    }
  }

  function renderEntry(entry) {
    const cb = el('div', { className: 'cb', 'data-open': 'false' });
    const header = el('div', { className: 'cb-header' });
    const caret = el('span', { className: 'cb-caret' }, '▸');
    const ts    = el('span', { className: 'cb-title' }, formatTimestamp(entry.at));
    const src   = el('span', { className: 'badge', style: { marginLeft: '6px' } },
      sourceLabel(entry.source));
    if (entry.status) {
      src.textContent = `${sourceLabel(entry.source)} ${entry.status}`;
    }
    // Source badge 颜色按错误类型粗分.
    if (entry.source === 'http')        src.classList.add('err');
    else if (entry.source === 'sse')    src.classList.add('warn');
    else if (entry.source === 'js')     src.classList.add('err');
    else if (entry.source === 'promise') src.classList.add('warn');
    else if (entry.source === 'resource') src.classList.add('warn');
    else src.classList.add('info');

    const typ = el('span', { className: 'mono', style: { fontSize: '12px' } }, entry.type || '—');
    const preview = el('span', { className: 'cb-preview' }, shortMessage(entry));

    header.append(caret, ts, src, typ, preview);
    header.addEventListener('click', () => {
      const open = cb.getAttribute('data-open') === 'true';
      cb.setAttribute('data-open', String(!open));
    });

    const body = el('pre', {
      className: 'cb-body',
      style: { margin: 0, whiteSpace: 'pre-wrap' },
    }, formatDetail(entry));

    cb.append(header, body);
    return cb;
  }

  on('errors:change', render);
  render();
}
