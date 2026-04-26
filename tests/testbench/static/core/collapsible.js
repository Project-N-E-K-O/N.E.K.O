/**
 * collapsible.js — 统一折叠块组件.
 *
 * PLAN §UI 约定 规定: 所有长内容 (system prompt / 消息 / reference / eval
 * analysis / 日志 entry / trace / schema template ...) 都必须以 CollapsibleBlock
 * 包裹, 展开/折叠状态持久化到 localStorage, 并提供容器级 Expand/Collapse all.
 *
 * 用法:
 *     import { createCollapsible, mountContainerToolbar } from './core/collapsible.js';
 *
 *     const block = createCollapsible({
 *       blockId: 'chat-msg-42',
 *       title: '用户消息',
 *       content: longText,                // string | HTMLElement
 *       lengthBadge: `${longText.length} 字符`,
 *       defaultCollapsed: true,
 *       copyable: true,
 *     });
 *     parent.appendChild(block);
 *
 *     mountContainerToolbar(parentEl);    // 可选: 挂 Expand/Collapse all 按钮
 *
 * localStorage 键:  `fold:<session_id|'_global'>:<blockId>` → `'0' | '1'`
 */

import { i18n } from './i18n.js';
import { store, on as onState } from './state.js';
import { toast } from './toast.js';

function sessionScope() {
  return store.session?.id || '_global';
}

function storageKey(blockId) {
  return `fold:${sessionScope()}:${blockId}`;
}

function readPersistedOpen(blockId, fallback) {
  try {
    const raw = localStorage.getItem(storageKey(blockId));
    if (raw === '1') return true;
    if (raw === '0') return false;
  } catch (_) { /* localStorage 不可用时悄悄回退 */ }
  return fallback;
}

function persistOpen(blockId, open) {
  try {
    localStorage.setItem(storageKey(blockId), open ? '1' : '0');
  } catch (_) { /* quota exceeded 等忽略 */ }
}

/**
 * 构建一个折叠块元素.
 *
 * @param {object} opts
 * @param {string} opts.blockId          本块在当前 scope 里的唯一 id
 * @param {string} opts.title            标题 (必填)
 * @param {string|HTMLElement} opts.content 完整内容
 * @param {string} [opts.summary]        折叠态预览; 未传则用 content 前 120 字符
 * @param {string} [opts.lengthBadge]    右侧灰标; 未传则用 `content.length 字符`
 * @param {boolean} [opts.defaultCollapsed=true]  首次展示默认折叠
 * @param {boolean} [opts.copyable=false] 展开态显示 "复制" 按钮
 * @returns {HTMLElement} 整块容器, 挂有 `cb` class
 */
export function createCollapsible(opts) {
  const {
    blockId,
    title,
    content,
    summary: summaryOverride,
    lengthBadge: badgeOverride,
    defaultCollapsed = true,
    copyable = false,
  } = opts;

  if (!blockId || !title) {
    throw new Error('createCollapsible: blockId + title are required');
  }

  // Resolve content to string for preview / length; keep original for body DOM.
  const isElement = content instanceof HTMLElement;
  const textForPreview = isElement ? (content.textContent || '') : String(content ?? '');

  const summary = summaryOverride ?? (
    textForPreview.replace(/\s+/g, ' ').slice(0, 120)
    + (textForPreview.length > 120 ? '…' : '')
  );
  const badge = badgeOverride ?? i18n('collapsible.length_chars', textForPreview.length);

  const isOpen = readPersistedOpen(blockId, !defaultCollapsed);

  const root = document.createElement('div');
  root.className = 'cb';
  root.dataset.blockId = blockId;
  root.dataset.open = String(isOpen);

  // Header -----------------------------------------------------------
  const header = document.createElement('div');
  header.className = 'cb-header';
  header.setAttribute('role', 'button');
  header.setAttribute('tabindex', '0');
  header.setAttribute('aria-expanded', String(isOpen));

  const caret = document.createElement('span');
  caret.className = 'cb-caret';
  caret.textContent = '▸';
  header.appendChild(caret);

  const titleEl = document.createElement('span');
  titleEl.className = 'cb-title';
  titleEl.textContent = title;
  header.appendChild(titleEl);

  const previewEl = document.createElement('span');
  previewEl.className = 'cb-preview';
  previewEl.textContent = summary;
  header.appendChild(previewEl);

  const badgeEl = document.createElement('span');
  badgeEl.className = 'cb-badge';
  badgeEl.textContent = badge;
  header.appendChild(badgeEl);

  const actions = document.createElement('span');
  actions.className = 'cb-actions';
  if (copyable) {
    const copyBtn = document.createElement('button');
    copyBtn.type = 'button';
    copyBtn.textContent = i18n('collapsible.copy');
    copyBtn.addEventListener('click', async (ev) => {
      ev.stopPropagation();
      try {
        await navigator.clipboard.writeText(textForPreview);
        toast.ok(i18n('collapsible.copy_ok'));
      } catch (err) {
        toast.err(i18n('collapsible.copy_fail'), { message: String(err) });
      }
    });
    actions.appendChild(copyBtn);
  }
  header.appendChild(actions);

  root.appendChild(header);

  // Body -------------------------------------------------------------
  const body = document.createElement('div');
  body.className = 'cb-body';
  if (isElement) {
    body.appendChild(content);
  } else {
    body.textContent = textForPreview;
  }
  root.appendChild(body);

  function setOpen(open) {
    root.dataset.open = String(open);
    header.setAttribute('aria-expanded', String(open));
    persistOpen(blockId, open);
  }

  header.addEventListener('click', (ev) => {
    // Alt+Click: 一次性展开/折叠所有兄弟块 (PLAN 约定)
    if (ev.altKey) {
      const parent = root.parentElement;
      if (parent) {
        const open = root.dataset.open !== 'true';
        parent.querySelectorAll(':scope > .cb').forEach((sib) => {
          sib._cb?.setOpen?.(open);
        });
      }
      return;
    }
    setOpen(root.dataset.open !== 'true');
  });
  header.addEventListener('keydown', (ev) => {
    if (ev.key === 'Enter' || ev.key === ' ') {
      ev.preventDefault();
      setOpen(root.dataset.open !== 'true');
    }
  });

  // Expose programmatic control on the element itself for container ops.
  root._cb = { setOpen, getOpen: () => root.dataset.open === 'true' };

  return root;
}

/**
 * 给一个容器挂 `[展开全部] [折叠全部]` 工具栏. 工具栏插在容器最顶部.
 * 容器内之后 (或现在) 的 `.cb` 子块都会受控.
 */
export function mountContainerToolbar(container) {
  if (!container) return;
  if (container.querySelector(':scope > .cb-container-toolbar')) return; // 幂等
  const bar = document.createElement('div');
  bar.className = 'cb-container-toolbar';

  const expandBtn = document.createElement('button');
  expandBtn.type = 'button';
  expandBtn.textContent = i18n('collapsible.expand_all');
  expandBtn.addEventListener('click', () => {
    container.querySelectorAll(':scope .cb').forEach((el) => el._cb?.setOpen(true));
  });

  const collapseBtn = document.createElement('button');
  collapseBtn.type = 'button';
  collapseBtn.textContent = i18n('collapsible.collapse_all');
  collapseBtn.addEventListener('click', () => {
    container.querySelectorAll(':scope .cb').forEach((el) => el._cb?.setOpen(false));
  });

  bar.append(expandBtn, collapseBtn);
  container.insertBefore(bar, container.firstChild);
}

// 当 session 变更时, 已渲染的折叠块 localStorage 键会换 scope, 所以后续新建的
// 块会读新 scope 的值; 已存在的块保持当前状态不重新持久化, 避免污染.
onState('session:change', () => { /* no-op; 只需要让 sessionScope() 返回新值 */ });
