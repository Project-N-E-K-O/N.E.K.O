/**
 * toast.js — 右下角悬浮提示栈.
 *
 * API:
 *     import { toast } from './core/toast.js';
 *     toast.ok('操作成功');
 *     toast.info('切换到 Chat');
 *     toast.warn('磁盘接近满');
 *     toast.err('请求失败', { title: '网络错误', actions: [{label:'重试', onClick:() => ...}] });
 *     toast.show({ kind, title, message, duration, actions });
 *
 * 容器 (#toast-stack) 必须在 DOM 中; 不存在时自动创建.
 * 默认 3.5s 后淡出, `err` 与 `warn` 默认持久, 点叉关闭.
 */

import { i18n } from './i18n.js';

const DEFAULT_DURATIONS = {
  ok:   3500,
  info: 3500,
  warn: 0,     // 0 = 不自动关闭
  err:  0,
};

function ensureStack() {
  let stack = document.getElementById('toast-stack');
  if (!stack) {
    stack = document.createElement('div');
    stack.id = 'toast-stack';
    document.body.appendChild(stack);
  }
  return stack;
}

function createToast({ kind = 'info', title, message, actions = [], duration }) {
  const el = document.createElement('div');
  el.className = `toast ${kind}`;

  const body = document.createElement('div');
  body.className = 'toast-body';

  if (title) {
    const t = document.createElement('div');
    t.className = 'toast-title';
    t.textContent = title;
    body.appendChild(t);
  }
  if (message) {
    const m = document.createElement('div');
    m.className = 'toast-msg';
    m.textContent = message;
    body.appendChild(m);
  }

  if (actions.length) {
    const row = document.createElement('div');
    row.className = 'toast-actions';
    for (const a of actions) {
      const btn = document.createElement('button');
      btn.textContent = a.label;
      btn.addEventListener('click', () => {
        try { a.onClick?.(); } finally {
          if (a.dismiss !== false) dismiss(el);
        }
      });
      row.appendChild(btn);
    }
    body.appendChild(row);
  }

  el.appendChild(body);

  const closeBtn = document.createElement('button');
  closeBtn.className = 'toast-close';
  closeBtn.setAttribute('aria-label', i18n('toast.close'));
  closeBtn.textContent = '×';
  closeBtn.addEventListener('click', () => dismiss(el));
  el.appendChild(closeBtn);

  const realDuration = duration ?? DEFAULT_DURATIONS[kind] ?? 3500;
  if (realDuration > 0) {
    setTimeout(() => dismiss(el), realDuration);
  }
  return el;
}

function dismiss(el) {
  if (!el || !el.isConnected) return;
  el.classList.add('exit');
  setTimeout(() => el.remove(), 200);
}

function show(opts) {
  const stack = ensureStack();
  const el = createToast(opts);
  stack.appendChild(el);
  return el;
}

export const toast = {
  show,
  ok:   (message, opts = {}) => show({ kind: 'ok',   message, ...opts }),
  info: (message, opts = {}) => show({ kind: 'info', message, ...opts }),
  warn: (message, opts = {}) => show({ kind: 'warn', message, ...opts }),
  err:  (message, opts = {}) => show({ kind: 'err',  message, ...opts }),
  dismissAll() {
    document.querySelectorAll('#toast-stack .toast').forEach(dismiss);
  },
};
