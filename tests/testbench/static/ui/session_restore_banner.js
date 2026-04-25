/**
 * session_restore_banner.js — P22 "上次运行可能异常退出" 横幅.
 *
 * 在 topbar 和 tabbar 之间插一条横幅 (复用 ``.model-reminder-slot`` 的
 * 位置习惯, 但走自己的 ``.session-restore-banner`` 样式). 启动时调
 * ``GET /api/session/autosaves/boot_orphans``, 若返回非空就渲染:
 *
 *   ⏱ 检测到上次运行的 N 个会话有未保存的自动备份.
 *   [查看并恢复] [忽略]
 *
 * 点"查看并恢复"会打开 :mod:`session_restore_modal`. 点"忽略"或 ×
 * 只是把横幅从 DOM 里拿掉 — **不**删磁盘上的自动保存, 用户随时可以
 * 从 session dropdown 里的 "恢复自动保存…" 再打开模态.
 *
 * 和 model_config_reminder 的关系:
 *   两条横幅共存时 autosave 横幅显示在下方 (晚于 model-reminder 插入).
 *   都关掉后 ``.model-reminder-slot`` / ``.session-restore-banner-slot``
 *   保留空元素避免 grid track 崩塌 (参见 testbench.css 的 P14 注释).
 *
 * 不记 "已关闭" 到 sessionStorage:
 *   关了就关了, 本次 boot 内不再自动出. 若用户切到别的 workspace 又切回
 *   来, 横幅也不会自动重开. 真需要再看, 走 dropdown 入口. 这样避免一种
 *   "用户关了提示还一直弹"的骚扰.
 */

import { i18n } from '../core/i18n.js';
import { api } from '../core/api.js';
import { el } from './_dom.js';
import { openSessionRestoreModal } from './session_restore_modal.js';

let hostEl = null;
let dismissed = false;

export function mountSessionRestoreBanner() {
  const app = document.getElementById('app');
  const tabbar = document.getElementById('tabbar');
  if (!app || !tabbar) return;

  // Slot sits *after* model-reminder-slot so when both banners are
  // present, the restore banner renders below. Empty slot stays in DOM
  // (display: block, 0 height) to avoid grid track reshuffling.
  hostEl = el('div', {
    className: 'session-restore-banner-slot',
    'aria-live': 'polite',
  });
  app.insertBefore(hostEl, tabbar);

  refresh();
}

async function refresh() {
  if (dismissed) return;
  // 404 is possible if the backend was compiled without the P22
  // router for some reason; silent fallback to "no banner".
  const res = await api.get('/api/session/autosaves/boot_orphans', {
    expectedStatuses: [404, 500, 502, 503, 504],
  });
  if (!res.ok) return;
  const items = res.data?.items || [];
  if (items.length === 0) return;
  render(items);
}

function render(items) {
  if (!hostEl || dismissed) return;
  hostEl.innerHTML = '';

  const banner = el('div', {
    className: 'session-restore-banner',
    role: 'status',
  });

  const iconEl = el('span', {
    className: 'session-restore-banner__icon',
    'aria-hidden': 'true',
  }, '⏱');

  const body = el('div', { className: 'session-restore-banner__body' });
  const titleText = i18n('session.restore_banner.title', items.length);
  body.append(
    el('div', {
      className: 'session-restore-banner__title u-wrap-anywhere',
      title: titleText,
    }, titleText),
    el('div', {
      className: 'session-restore-banner__text',
    }, i18n('session.restore_banner.body')),
  );

  const actions = el('div', { className: 'session-restore-banner__actions' });

  const openBtn = el('button', {
    type: 'button',
    className: 'btn primary',
    onClick: () => {
      openSessionRestoreModal();
      // P24 §12.3.E #15: auto-dismiss on [查看并恢复] click. Rationale
      // per user feedback (2026-04-21): opening the modal is the
      // effective acknowledgement — keeping the banner after the modal
      // opens is redundant visual noise, and if the user closes the
      // modal without restoring, they can always reopen via Menu →
      // "恢复自动保存…". Orphan autosave entries stay on disk regardless.
      dismissed = true;
      if (hostEl) hostEl.innerHTML = '';
    },
  }, i18n('session.restore_banner.open_btn'));

  const dismissBtn = el('button', {
    type: 'button',
    className: 'session-restore-banner__dismiss',
    title: i18n('session.restore_banner.dismiss_hint'),
    'aria-label': i18n('session.restore_banner.dismiss_hint'),
    onClick: () => {
      dismissed = true;
      if (hostEl) hostEl.innerHTML = '';
    },
  }, '×');

  actions.append(openBtn, dismissBtn);
  banner.append(iconEl, body, actions);
  hostEl.append(banner);
}
