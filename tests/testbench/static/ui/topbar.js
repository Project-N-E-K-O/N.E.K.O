/**
 * topbar.js — 顶栏渲染 + 交互.
 *
 * 职责:
 *   - 品牌 + Session dropdown (P21 起: New / Destroy / Save / Save as /
 *     Load (入口也带 Import JSON); Restore autosave 留给 P22)
 *   - Stage chip (P14, 细节在 ./topbar_stage_chip.js)
 *   - Timeline chip (P18, 细节在 ./topbar_timeline_chip.js)
 *   - Err 徽章: 订阅 `http:error`, P03 只做简易计数 (P19 会完整 Errors 子页)
 *   - 右侧 Menu: 跳到 Diagnostics / Settings / About; Export/Reset 占位
 *
 * 对外只暴露 `mountTopbar(hostEl)`; 其余全靠 state 事件驱动刷新.
 */

import { i18n } from '../core/i18n.js';
import { api } from '../core/api.js';
import { toast } from '../core/toast.js';
import { store, set, on, emit } from '../core/state.js';
import { mountStageChip } from './topbar_stage_chip.js';
import { mountTimelineChip } from './topbar_timeline_chip.js';
import { openSessionSaveModal } from './session_save_modal.js';
import { openSessionLoadModal } from './session_load_modal.js';
import { openSessionRestoreModal } from './session_restore_modal.js';
import { openSessionExportModal } from './session_export_modal.js';

function el(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (k === 'className') node.className = v;
    else if (k === 'onClick') node.addEventListener('click', v);
    else if (k.startsWith('data-')) node.setAttribute(k, v);
    else if (k === 'title') node.title = v;
    else node[k] = v;
  }
  for (const c of children) {
    if (c == null) continue;
    node.append(c instanceof Node ? c : document.createTextNode(c));
  }
  return node;
}

// ── dropdown helper ─────────────────────────────────────────────────
// 每页最多一个打开的下拉; 外部点击自动关闭.
let _openMenu = null;

function openMenu(menuEl) {
  closeMenu();
  menuEl.classList.add('open');
  _openMenu = menuEl;
}
function closeMenu() {
  if (_openMenu) {
    _openMenu.classList.remove('open');
    _openMenu = null;
  }
}
document.addEventListener('click', (ev) => {
  if (!_openMenu) return;
  if (_openMenu.contains(ev.target)) return;
  // 触发按钮自己会 stopPropagation, 其它点击都关
  closeMenu();
});

function makeDropdown(trigger, menuEl) {
  const wrap = el('div', { className: 'dropdown' });
  wrap.append(trigger, menuEl);
  trigger.addEventListener('click', (ev) => {
    ev.stopPropagation();
    if (_openMenu === menuEl) closeMenu();
    else openMenu(menuEl);
  });
  return wrap;
}

// ── Session dropdown ────────────────────────────────────────────────

function renderSessionChip() {
  const session = store.session;
  const fullLabel = session
    ? `${i18n('topbar.session.label')}: ${session.name || session.id}`
    : `${i18n('topbar.session.label')}: ${i18n('topbar.session.none')}`;
  const chip = el('button', {
    className: 'chip chip--session',
    // The tooltip carries the full (possibly very long) session/archive
    // name, since the visible label below is truncated with ellipsis.
    title: fullLabel,
  });
  // Label is a dedicated span so CSS can ellipsis-truncate it while the
  // caret chevron stays fully visible.
  const label = el('span', { className: 'chip__label' });
  label.textContent = fullLabel;
  chip.append(label, el('span', { className: 'caret' }, '▾'));
  return chip;
}

function renderSessionMenu() {
  const menu = el('div', { className: 'dropdown-menu' });

  const headingSession = el('div', { className: 'heading' }, i18n('topbar.session.label'));
  menu.append(headingSession);

  const newBtn = el('button', {
    className: 'item',
    onClick: async (ev) => {
      ev.stopPropagation();
      closeMenu();
      const res = await api.post('/api/session', {});
      if (res.ok) {
        // ⚠️ P24 hotfix (2026-04-21, #105): 新建会话**必须**走 reload 而不是
        // surgical `set('session', ...)`. 同族决策链:
        //   - P20 hotfix 1 (§4.26 #87) 改 Hard Reset → reload: 半死状态订阅者
        //     并发 fetch empty-sandbox 导致浏览器→电脑级卡死崩溃
        //   - P21 session:loaded → reload: 同原因, Load 存档后直接 reload
        //   - **New / Destroy 却一直沿用 P03 原始 surgical 路径** — 这次同款
        //     bug 再次发生 (用户 create 新会话 → 400/200 OK flood → 整机卡死
        //     强制断电). 这次一并统一到 reload, 消除最后两个 surgical 入口.
        // 详见 §4.26 #87 + §4.27 #105 + LESSONS_LEARNED #20.
        toast.ok(i18n('session.created', res.data.name));
        setTimeout(() => {
          try { window.location.reload(); }
          catch { /* jsdom / headless */ }
        }, 300);
      } else {
        toast.err(i18n('session.create_failed'), { message: res.error?.message });
      }
    },
  }, i18n('topbar.session.new'));
  menu.append(newBtn);

  const destroyBtn = el('button', {
    className: 'item',
    onClick: async (ev) => {
      ev.stopPropagation();
      closeMenu();
      if (!store.session) {
        toast.info(i18n('session.no_active'));
        return;
      }
      if (!confirm(i18n('session.confirm_destroy'))) return;
      const res = await api.delete('/api/session');
      if (res.ok) {
        // 同 new 一致走 reload. Destroy 后 session=null 也是半死状态 — 所有
        // session-scoped 订阅者会 fetch 到 "has_session=false" / 404, 部分
        // 组件的 no-session 分支未必覆盖完整 (尤其 chat 子页已 mount 的 DOM).
        toast.ok(i18n('session.destroyed'));
        setTimeout(() => {
          try { window.location.reload(); }
          catch { /* jsdom / headless */ }
        }, 300);
      } else {
        toast.err(i18n('session.destroy_failed'), { message: res.error?.message });
      }
    },
  }, i18n('topbar.session.delete'));
  menu.append(destroyBtn);

  menu.append(el('div', { className: 'divider' }));

  // ── P21: Save / Save as / Load / Import / Delete ─────────────────

  const saveBtn = el('button', {
    className: 'item',
    onClick: (ev) => {
      ev.stopPropagation();
      closeMenu();
      if (!store.session) {
        toast.info(i18n('session.no_active'));
        return;
      }
      openSessionSaveModal({ mode: 'save', defaultName: store.session?.name || '' });
    },
  }, i18n('topbar.session.save'));
  if (!store.session) saveBtn.disabled = true;
  menu.append(saveBtn);

  const saveAsBtn = el('button', {
    className: 'item',
    onClick: (ev) => {
      ev.stopPropagation();
      closeMenu();
      if (!store.session) {
        toast.info(i18n('session.no_active'));
        return;
      }
      openSessionSaveModal({ mode: 'save_as', defaultName: store.session?.name || '' });
    },
  }, i18n('topbar.session.save_as'));
  if (!store.session) saveAsBtn.disabled = true;
  menu.append(saveAsBtn);

  // Load modal embeds the Import-JSON inline flow (click "导入 JSON…"
  // inside it), so we expose a single entry point in the dropdown
  // instead of two buttons that both open the same modal.
  const loadBtn = el('button', {
    className: 'item',
    onClick: (ev) => {
      ev.stopPropagation();
      closeMenu();
      openSessionLoadModal();
    },
  }, i18n('topbar.session.load'));
  menu.append(loadBtn);

  menu.append(el('div', { className: 'divider' }));

  // Restore autosave: 打开 session_restore_modal (P22).
  const restoreItem = el('button', {
    className: 'item',
    onClick: (ev) => {
      ev.stopPropagation();
      closeMenu();
      openSessionRestoreModal();
    },
  }, i18n('topbar.session.restore_autosave'));
  menu.append(restoreItem);

  return menu;
}

function mountSessionDropdown(host) {
  const wrap = makeDropdown(renderSessionChip(), renderSessionMenu());
  host.append(wrap);

  function rebuildTrigger() {
    const newChip = renderSessionChip();
    const oldChip = wrap.firstElementChild;
    oldChip.replaceWith(newChip);
    newChip.addEventListener('click', (ev) => {
      ev.stopPropagation();
      const menuEl = wrap.querySelector('.dropdown-menu');
      if (_openMenu === menuEl) closeMenu();
      else openMenu(menuEl);
    });
  }

  function rebuildMenu() {
    // The Save/Save-as items need their `disabled` state recomputed
    // whenever the active session changes (new/destroy). Rebuild the
    // menu subtree in place so the dropdown dom node stays stable.
    const oldMenu = wrap.querySelector('.dropdown-menu');
    if (!oldMenu) return;
    const newMenu = renderSessionMenu();
    oldMenu.replaceWith(newMenu);
  }

  // 初次加载时拉一次后端, 让 UI 与后端状态同步.
  (async () => {
    const res = await api.get('/api/session');
    if (res.ok && res.data?.has_session) {
      set('session', res.data);
    } else {
      set('session', null);
    }
  })();

  on('session:change', () => {
    rebuildTrigger();
    rebuildMenu();
  });

  // P21: Load / Import finished — whole page must hard-reload so every
  // workspace re-fetches its slice of session state instead of relying
  // on surgical updates (§3A B13). We keep the reload delayed so the
  // success toast flashes briefly before the window blanks.
  on('session:loaded', ({ name } = {}) => {
    toast.ok(i18n('session.load_modal.reload_hint', name || ''));
    setTimeout(() => {
      try { window.location.reload(); }
      catch { /* jsdom / headless */ }
    }, 300);
  });

  // Save finished — dropdown label doesn't actually need to change
  // (session name is independent of saved archive names), but we still
  // refresh the menu so any "last saved at" hints stay fresh when we
  // add them in a follow-up.
  on('session:saved', () => {
    rebuildMenu();
  });
}

// ── Err 徽章 ───────────────────────────────────────────────────────

function mountErrBadge(host) {
  const count = el('span', { className: 'err-count' }, '0');
  const chip = el('button', {
    className: 'chip',
    title: i18n('topbar.error_badge.title_none'),
  }, 'Err ', count);
  count.hidden = true;

  function update() {
    const errs = store.errors || [];
    const n = errs.length;
    count.textContent = String(n);
    count.hidden = n === 0;
    chip.classList.toggle('err-active', n > 0);
    chip.title = n === 0
      ? i18n('topbar.error_badge.title_none')
      : i18n('topbar.error_badge.title_some', n);
  }

  // 收集逻辑集中在 core/errors_bus.js, 这里只消费 `errors:change`.
  on('errors:change', update);

  chip.addEventListener('click', (ev) => {
    ev.stopPropagation();
    const n = (store.errors || []).length;
    if (n === 0) {
      toast.info(i18n('topbar.error_badge.empty'));
      return;
    }
    // P20 hotfix 2: 不仅切到 Diagnostics, 还要确保子页是 Errors (不管
    // 用户上次离开时看的是哪个子页). 走 workspace_diagnostics 的
    // 'diagnostics:navigate' 协调者事件 — 它会 force-select 'errors'
    // 子页, 并且在未挂载的情况下把目标写 LS 等下次激活时读出.
    // 只 `set('active_workspace', 'diagnostics')` 会沿用上次子页
    // (可能是 logs/paths/reset), 看起来像"点 Err 徽章没有跳转".
    // LS key 必须与 workspace_diagnostics.js::LS_KEY 一致, 否则协调者
    // 读不到这条 "先切 errors" 的 hint, 回到它上次记住的子页.
    try { localStorage.setItem('testbench:diagnostics:active_subpage', 'errors'); }
    catch { /* ignore */ }
    set('active_workspace', 'diagnostics');
    emit('diagnostics:navigate', { subpage: 'errors' });
  });

  host.append(chip);
  update();
}

// ── 右侧 Menu ──────────────────────────────────────────────────────

function mountRightMenu(host) {
  const trigger = el('button', {
    className: 'chip',
    title: i18n('topbar.menu.label'),
  }, '⋮');
  const menu = el('div', { className: 'dropdown-menu', 'data-align': 'right' });

  const gotoDiag = el('button', {
    className: 'item',
    onClick: (ev) => {
      ev.stopPropagation();
      closeMenu();
      set('active_workspace', 'diagnostics');
    },
  }, i18n('topbar.menu.diagnostics'));
  const gotoSet = el('button', {
    className: 'item',
    onClick: (ev) => {
      ev.stopPropagation();
      closeMenu();
      set('active_workspace', 'settings');
    },
  }, i18n('topbar.menu.settings'));

  // P23: Export menu item — opens the unified session export modal
  // with no preset (scope/format defaults to `full` + `json`). The
  // modal itself gates on "no active session" so we don't duplicate
  // that check here; keeping the button always-enabled mirrors the
  // Save menu's "click-to-get-friendly-toast" flow.
  const exportItem = el('button', {
    className: 'item',
    onClick: (ev) => {
      ev.stopPropagation();
      closeMenu();
      if (!store.session) {
        toast.info(i18n('session.no_active'));
        return;
      }
      openSessionExportModal();
    },
  }, i18n('topbar.menu.export'));

  // P24 cleanup (2026-04-21, see P24_BLUEPRINT §12.3.B):
  //
  // * `topbar.menu.reset` removed — Diagnostics → Reset subpage already
  //   exposes all three reset tiers (soft/medium/hard) with full backup UX;
  //   a second entry here would violate §3A B6 ("single entry per feature").
  //
  // 2026-04-24 (P26 Commit 3.1): `topbar.menu.about` unhidden now that
  // USER_MANUAL / ARCHITECTURE_OVERVIEW / CHANGELOG docs ship as part
  // of v1.1.0 and Settings → About is a real destination (version +
  // last-updated date + 相关文档 deep-links). Click flows:
  //   1. set('active_workspace', 'settings') — switches right pane.
  //   2. emit('settings:goto_page', 'about') — coordinator event the
  //      Settings workspace already listens for (mirrors the Errors
  //      badge's 'diagnostics:navigate' pattern so the subpage is
  //      force-selected even if the user last left Settings on
  //      Models/API Keys/etc.).
  const gotoAbout = el('button', {
    className: 'item',
    onClick: (ev) => {
      ev.stopPropagation();
      closeMenu();
      set('active_workspace', 'settings');
      emit('settings:goto_page', 'about');
    },
  }, i18n('topbar.menu.about'));

  menu.append(
    gotoDiag, gotoSet, gotoAbout,
    el('div', { className: 'divider' }),
    exportItem,
  );
  host.append(makeDropdown(trigger, menu));
}

// ── 入口 ───────────────────────────────────────────────────────────

export function mountTopbar(hostEl) {
  hostEl.innerHTML = '';

  const brand = el('div', { className: 'brand' },
    i18n('app.name'),
    el('span', { className: 'sub' }, i18n('app.tagline')),
  );
  hostEl.append(brand);

  mountSessionDropdown(hostEl);
  mountStageChip(hostEl);
  mountTimelineChip(hostEl);

  const spacer = el('div', { className: 'spacer' });
  hostEl.append(spacer);

  mountErrBadge(hostEl);
  mountRightMenu(hostEl);
}
