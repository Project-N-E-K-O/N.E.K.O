/**
 * topbar.js — 顶栏渲染 + 交互.
 *
 * 职责:
 *   - 品牌 + Session dropdown (New / Destroy; P21 之后补 Load/Save/Import/Restore)
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
  const chip = el('button', {
    className: 'chip',
    title: i18n('topbar.session.label'),
  });
  const label = el('span', {});
  label.textContent = session
    ? `${i18n('topbar.session.label')}: ${session.name || session.id}`
    : `${i18n('topbar.session.label')}: ${i18n('topbar.session.none')}`;
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
        set('session', res.data);
        toast.ok(i18n('session.created', res.data.name));
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
        set('session', null);
        toast.ok(i18n('session.destroyed'));
      } else {
        toast.err(i18n('session.destroy_failed'), { message: res.error?.message });
      }
    },
  }, i18n('topbar.session.delete'));
  menu.append(destroyBtn);

  menu.append(el('div', { className: 'divider' }));

  // 占位项 — 后续 phase 实装
  for (const [textKey] of [
    ['topbar.session.load'],
    ['topbar.session.save'],
    ['topbar.session.save_as'],
    ['topbar.session.import'],
    ['topbar.session.restore_autosave'],
  ]) {
    const item = el('button', {
      className: 'item',
      onClick: (ev) => {
        ev.stopPropagation();
        closeMenu();
        toast.info(i18n('topbar.session.not_implemented'));
      },
    }, i18n(textKey));
    item.disabled = true;
    menu.append(item);
  }

  return menu;
}

function mountSessionDropdown(host) {
  const wrap = makeDropdown(renderSessionChip(), renderSessionMenu());
  host.append(wrap);

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
    // 替换 trigger chip 内容, 菜单结构不变.
    const newChip = renderSessionChip();
    const oldChip = wrap.firstElementChild;
    oldChip.replaceWith(newChip);
    // 重新绑定 trigger 事件 (makeDropdown 依赖闭包, 简单起见重建 wrap)
    // —— 但这里直接复用原 dropdown menu 更简单:
    newChip.addEventListener('click', (ev) => {
      ev.stopPropagation();
      const menuEl = wrap.querySelector('.dropdown-menu');
      if (_openMenu === menuEl) closeMenu();
      else openMenu(menuEl);
    });
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

  const placeholders = [
    ['topbar.menu.export', 'topbar.session.not_implemented'],
    ['topbar.menu.reset',  'topbar.session.not_implemented'],
    ['topbar.menu.about',  'common.not_implemented'],
  ].map(([labelKey, hintKey]) => {
    const btn = el('button', {
      className: 'item',
      onClick: (ev) => {
        ev.stopPropagation();
        closeMenu();
        toast.info(i18n(hintKey));
      },
    }, i18n(labelKey));
    btn.disabled = true;
    return btn;
  });

  menu.append(gotoDiag, gotoSet, el('div', { className: 'divider' }), ...placeholders);
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
