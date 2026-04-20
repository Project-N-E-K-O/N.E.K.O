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

// toast 右下角浮窗宽度固定 380px (见 #toast-stack max-width). 超长的
// 错误 message (典型来源: 后端异常 traceback / WinError 32 全路径 / JSON
// dump) 会把 .toast-body 撑成单行几千字符, 把 .toast-close 顶出右边, 用户
// 既看不全消息也点不到 X 关闭. 2026-04-20 用户反馈此问题后加此截断:
//   - 显示层 max TOAST_MSG_MAX_CHARS 字符 + "..." 省略号
//   - 完整原文放 `title=` 属性里, 鼠标 hover 可看全文
//   - CSS 侧 `.toast-msg { overflow-wrap: anywhere; max-height: ... }`
//     兜底, 保证即使 JS 截断失败也不撑破 container
// 阈值选 280 — 3 行可读长度, 够贴常见"HTTP 409 + hint"+"路径"级消息,
// 又足够短使得 380px 宽 + ~3 行能完整显示不横向滚动.
const TOAST_MSG_MAX_CHARS = 280;
const TOAST_TITLE_MAX_CHARS = 120;

function _truncate(s, limit) {
  if (typeof s !== 'string') return s;
  if (s.length <= limit) return s;
  return s.slice(0, limit - 1) + '…';
}

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
    const titleStr = typeof title === 'string' ? title : String(title);
    const titleShort = _truncate(titleStr, TOAST_TITLE_MAX_CHARS);
    t.textContent = titleShort;
    // 原文若被截断, 挂 title 属性, 鼠标 hover 可看全文.
    if (titleStr !== titleShort) t.title = titleStr;
    body.appendChild(t);
  }
  if (message) {
    const m = document.createElement('div');
    m.className = 'toast-msg';
    const msgStr = typeof message === 'string' ? message : String(message);
    const msgShort = _truncate(msgStr, TOAST_MSG_MAX_CHARS);
    m.textContent = msgShort;
    if (msgStr !== msgShort) m.title = msgStr;
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
