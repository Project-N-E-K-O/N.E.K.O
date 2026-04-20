/**
 * workspace_diagnostics.js — Diagnostics workspace (P19 正式版).
 *
 * 子页清单:
 *   - Errors    — P19 实装; 进程级 ring buffer, 前后端同步.
 *   - Logs      — P19 实装; 读 tests/testbench_data/logs/<id>-YYYYMMDD.jsonl.
 *   - Snapshots — P18 实装; 完整快照管理 (增 / 删 / 重命名 / 查看 / 回退).
 *   - Paths     — P20 会实装; 当前占位.
 *   - Reset     — P20 会实装; 当前占位.
 *
 * 布局同 Evaluation workspace: left subnav + right pane 的 `workspace.two-col`,
 * 每个子页懒渲染且切走不卸载 (Errors/Logs 自己带 auto-poll, 切回仍是最新).
 *
 * 历史包袱:
 *   - P04 夹带的临时 Errors 面板 (老版 `workspace_diagnostics.js`) 被本版
 *     完全替换; 前端 `errors_bus.js` 保留 (作为"收集四类前端事件 + 镜像到
 *     后端 /api/diagnostics/errors" 的上游适配器).
 *   - P04 的旧 i18n 键 (`diagnostics.errors.heading` "Errors · 错误面板
 *     (临时)" / `notice` / `columns.*` / `sources.*` 等) 在 i18n.js 里改写
 *     为正式版对应的 P19 键 (移除 "(临时)" 标注, 新增 intro / filter /
 *     pager 等命名空间).
 *
 * 跨 workspace hint 通道 (§3A B7 协调者驱动 force-remount):
 *   - `store.ui_prefs.diagnostics_errors_filter` 是未来 (Results 错误徽章
 *     点击跳转) 的预留: `page_errors.js` mount 时会消费一次并清零, 本协调
 *     者在 `active_workspace:change` / `diagnostics:navigate` 事件里无条
 *     件 force-remount 目标子页; 设计原则同 Evaluation.
 *   - P19 尚未有业务路径写这个 hint, 但预埋通道避免 P20+ 再改架构.
 */

import { i18n } from '../core/i18n.js';
import { store, on } from '../core/state.js';
import { el } from './_dom.js';
import { renderErrorsPage } from './diagnostics/page_errors.js';
import { renderLogsPage }   from './diagnostics/page_logs.js';
import { renderSnapshotsPage } from './diagnostics/page_snapshots.js';
import { renderPathsPage } from './diagnostics/page_paths.js';
import { renderResetPage } from './diagnostics/page_reset.js';

const PAGES = [
  { kind: 'page', id: 'errors',    render: renderErrorsPage,                      navKey: 'diagnostics.nav.errors' },
  { kind: 'page', id: 'logs',      render: renderLogsPage,                        navKey: 'diagnostics.nav.logs' },
  { kind: 'page', id: 'snapshots', render: renderSnapshotsPage,                   navKey: 'diagnostics.nav.snapshots' },
  { kind: 'page', id: 'paths',     render: renderPathsPage,                       navKey: 'diagnostics.nav.paths' },
  { kind: 'page', id: 'reset',     render: renderResetPage,                       navKey: 'diagnostics.nav.reset' },
];

const LS_KEY = 'testbench:diagnostics:active_subpage';

function firstPage() {
  return PAGES.find((p) => p.kind === 'page');
}

export function mountDiagnosticsWorkspace(host) {
  host.classList.add('two-col');
  host.innerHTML = '';

  const nav = el('div', { className: 'subnav' });
  const pane = el('div', {});
  host.append(nav, pane);

  const stored = localStorage.getItem(LS_KEY);
  const initial = PAGES.some((p) => p.kind === 'page' && p.id === stored)
    ? stored
    : firstPage().id;

  const buttons = {};
  for (const page of PAGES) {
    const btn = el('button', {
      className: 'subnav-item',
      onClick: () => selectPage(page.id),
    }, i18n(page.navKey));
    buttons[page.id] = btn;
    nav.append(btn);
  }

  let currentId = initial;
  let dirty = false;

  function selectPage(id) {
    const page = PAGES.find((p) => p.kind === 'page' && p.id === id) || firstPage();
    currentId = page.id;
    dirty = false;
    for (const [bid, btn] of Object.entries(buttons)) {
      btn.classList.toggle('active', bid === page.id);
    }
    localStorage.setItem(LS_KEY, page.id);
    pane.innerHTML = '';
    const subpage = el('div', { className: 'subpage active', 'data-subpage': page.id });
    pane.append(subpage);
    Promise.resolve(page.render(subpage)).catch((err) => {
      console.error(`[diagnostics] render ${page.id} failed:`, err);
      subpage.innerHTML = '';
      subpage.append(el('div', { className: 'empty-state' },
        `子页渲染失败: ${err?.message || err}`));
    });
  }

  // Diagnostics 子页大部分和 session 无关 (Errors/Logs 都是进程级资源),
  // 但 Logs 子页在 sessions list 里包含当前 session, 刷新一下视觉. 切会
  // 话时如果正在看 Logs, 简单 remount 让它重新选默认 session.
  on('session:change', () => {
    if (store.active_workspace === 'diagnostics') {
      // 仅当当前子页是 logs 时才重渲染, 避免 Errors 页无谓刷新.
      if (currentId === 'logs') selectPage(currentId);
    } else if (currentId === 'logs') {
      dirty = true;
    }
  });

  // 跨 workspace / 同 workspace 内部导航 — 预埋 hint 消费管道.
  // 设计与 workspace_evaluation.js 一致: hint 在协调者层消费, 不在接收子页
  // 订阅 (§3A B7 教训: 接收方订阅在 jsdom 单测能过但浏览器 warm-same 路径
  // 偶发失灵, 协调者 force-remount 最稳).
  function consumeHintIfPresent() {
    const hint = store.ui_prefs?.diagnostics_errors_filter;
    if (!hint) return false;
    const target = 'errors';
    if (!PAGES.some((p) => p.kind === 'page' && p.id === target)) return false;
    try { localStorage.setItem(LS_KEY, target); } catch { /* ignore */ }
    selectPage(target);
    return true;
  }

  on('active_workspace:change', (id) => {
    if (id !== 'diagnostics') return;
    if (consumeHintIfPresent()) return;
    const stored2 = localStorage.getItem(LS_KEY);
    if (stored2 && stored2 !== currentId && PAGES.some((p) => p.kind === 'page' && p.id === stored2)) {
      selectPage(stored2);
    } else if (dirty) {
      selectPage(currentId);
    }
  });

  on('diagnostics:navigate', (payload) => {
    const target = payload?.subpage;
    if (!target) return;
    if (!PAGES.some((p) => p.kind === 'page' && p.id === target)) return;
    if (store.active_workspace !== 'diagnostics') {
      try { localStorage.setItem(LS_KEY, target); } catch { /* ignore */ }
      return;
    }
    if (consumeHintIfPresent()) return;
    if (currentId !== target) selectPage(target);
  });

  selectPage(initial);
}
