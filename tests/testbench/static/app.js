/**
 * app.js — 前端入口.
 *
 * 负责:
 *   1. 启动时把 DOM 中的 i18n 占位翻成中文
 *   2. 挂载顶栏 + tab 路由
 *   3. 根据 `active_workspace` 事件切换 workspace 的可见区域
 *
 * 每个 workspace 懒挂载: 首次切到该 tab 才调用对应 mount 函数填充 DOM,
 * 切走不卸载 (会话数据量通常很小, 重建成本高于保留).
 */

import { hydrateI18n, i18n } from './core/i18n.js';
import { store, set, on } from './core/state.js';
import { initErrorsBus } from './core/errors_bus.js';
import { initRenderDriftDetector, registerChecker } from './core/render_drift_detector.js';

import { mountTopbar }            from './ui/topbar.js';
import { mountModelConfigReminder } from './ui/model_config_reminder.js';
import { mountSessionRestoreBanner } from './ui/session_restore_banner.js';
import { mountSetupWorkspace }    from './ui/workspace_setup.js';
import { mountChatWorkspace }     from './ui/workspace_chat.js';
import { mountEvaluationWorkspace } from './ui/workspace_evaluation.js';
import { mountDiagnosticsWorkspace } from './ui/workspace_diagnostics.js';
import { mountSettingsWorkspace }  from './ui/workspace_settings.js';

/** Tab 声明 — 顺序即渲染顺序. */
const WORKSPACES = [
  { id: 'setup',       labelKey: 'tabs.setup',       mount: mountSetupWorkspace       },
  { id: 'chat',        labelKey: 'tabs.chat',        mount: mountChatWorkspace        },
  { id: 'evaluation',  labelKey: 'tabs.evaluation',  mount: mountEvaluationWorkspace  },
  { id: 'diagnostics', labelKey: 'tabs.diagnostics', mount: mountDiagnosticsWorkspace },
  { id: 'settings',    labelKey: 'tabs.settings',    mount: mountSettingsWorkspace    },
];

const _mountedWorkspaces = new Set();

function mountTabbar(hostEl) {
  hostEl.innerHTML = '';
  for (const ws of WORKSPACES) {
    const btn = document.createElement('button');
    btn.className = 'tab';
    btn.type = 'button';
    btn.dataset.workspace = ws.id;
    btn.textContent = i18n(ws.labelKey);
    btn.addEventListener('click', () => set('active_workspace', ws.id));
    hostEl.append(btn);
  }
}

function renderWorkspaces(hostEl) {
  hostEl.innerHTML = '';
  for (const ws of WORKSPACES) {
    const section = document.createElement('section');
    section.className = 'workspace';
    section.dataset.workspace = ws.id;
    hostEl.append(section);
  }
}

function ensureMounted(id) {
  if (_mountedWorkspaces.has(id)) return;
  const ws = WORKSPACES.find((w) => w.id === id);
  if (!ws) return;
  const section = document.querySelector(`section.workspace[data-workspace="${id}"]`);
  if (!section) return;
  try {
    ws.mount(section);
    _mountedWorkspaces.add(id);
  } catch (err) {
    console.error(`[app] mount ${id} failed:`, err);
    section.innerHTML = `<div class="empty-state">${i18n('errors.unknown')}: ${err.message}</div>`;
  }
}

function applyActiveWorkspace(id) {
  const tabbar = document.getElementById('tabbar');
  for (const btn of tabbar.querySelectorAll('button.tab')) {
    btn.classList.toggle('active', btn.dataset.workspace === id);
  }
  const host = document.getElementById('workspace-host');
  for (const sec of host.querySelectorAll('section.workspace')) {
    sec.classList.toggle('active', sec.dataset.workspace === id);
  }
  ensureMounted(id);
}

function boot() {
  document.title = i18n('app.name');

  // 错误总线必须在任何 fetch / UI 之前启动, 才能捕获启动阶段就发生的错误.
  initErrorsBus();

  // Dev-only render drift detector (P24 §3.5). ?dev=1 or
  // window.__DEBUG_RENDER_DRIFT__=true 才启用, 生产零开销.
  initRenderDriftDetector();

  hydrateI18n(document);

  const topbar = document.getElementById('topbar');
  const tabbar = document.getElementById('tabbar');
  const host   = document.getElementById('workspace-host');
  if (!topbar || !tabbar || !host) {
    console.error('[app] required host elements missing');
    return;
  }

  mountTopbar(topbar);
  // Reminder 横幅插到 topbar 和 tabbar 之间, 在 tabbar 挂载**之前**插槽, 这样 DOM 顺序是
  // topbar → reminder-slot → restore-banner-slot → tabbar → workspace-host, 视觉从上到下自然.
  mountModelConfigReminder();
  mountSessionRestoreBanner();
  mountTabbar(tabbar);
  renderWorkspaces(host);

  on('active_workspace:change', applyActiveWorkspace);
  applyActiveWorkspace(store.active_workspace || 'setup');

  // Dev-only derived-state checkers (P24 §3.5). Register them after the
  // topbar + workspaces mount so the DOM selectors in each check() resolve.
  // These are low-risk "sanity" checkers; page-local checkers should live
  // in their own mountXxx so teardown is paired naturally.
  registerChecker({
    name: 'topbar.session_chip_label',
    event: 'session:change',
    check: () => {
      const labelEl = document.querySelector('.chip--session .chip__label');
      if (!labelEl) return { ok: false, detail: 'session chip label element missing from DOM' };
      const session = store.session;
      const expected = session
        ? `${i18n('topbar.session.label')}: ${session.name || session.id}`
        : `${i18n('topbar.session.label')}: ${i18n('topbar.session.none')}`;
      const actual = labelEl.textContent || '';
      if (actual !== expected) {
        return {
          ok: false,
          detail: `topbar session chip shows '${actual}' but store.session derived to '${expected}'`,
          driftKey: `${actual}||${expected}`,
        };
      }
      return { ok: true };
    },
  });
  registerChecker({
    name: 'app.active_workspace_section',
    event: 'active_workspace:change',
    check: () => {
      const expected = store.active_workspace || 'setup';
      const activeSec = document.querySelector('section.workspace.active');
      if (!activeSec) return { ok: false, detail: 'no .workspace.active section in DOM' };
      const actual = activeSec.dataset.workspace;
      if (actual !== expected) {
        return {
          ok: false,
          detail: `active section data-workspace='${actual}' but store.active_workspace='${expected}'`,
          driftKey: `${actual}||${expected}`,
        };
      }
      return { ok: true };
    },
  });

  console.info('[app] testbench UI ready');
}

if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', boot, { once: true });
} else {
  boot();
}
