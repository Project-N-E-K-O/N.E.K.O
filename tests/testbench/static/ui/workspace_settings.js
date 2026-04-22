/**
 * workspace_settings.js — Settings workspace 入口 (P04).
 *
 * 布局: `.workspace.two-col` = 左 sub-nav + 右 sub-page.
 * 五子页: Models / API Keys / Providers / UI / About.
 *
 * 各页模块放在 ./settings/*, 每个模块暴露一个 `render*(host)` 函数, 挂载时
 * 先清空右栏再重新挂. 重新挂载比增量更新成本高一点, 但对 Settings 这种
 * 低频交互完全够用, 代码量也少得多.
 */

import { i18n } from '../core/i18n.js';
import { store, on } from '../core/state.js';
import { el } from './_dom.js';
import { renderModelsPage } from './settings/page_models.js';
import { renderApiKeysPage } from './settings/page_api_keys.js';
import { renderProvidersPage } from './settings/page_providers.js';
import { renderUiPage } from './settings/page_ui.js';
import { renderAboutPage } from './settings/page_about.js';
import { renderAutosavePage } from './settings/page_autosave.js';

const PAGES = [
  { id: 'models',    render: renderModelsPage,    navKey: 'settings.nav.models' },
  { id: 'api_keys',  render: renderApiKeysPage,   navKey: 'settings.nav.api_keys' },
  { id: 'providers', render: renderProvidersPage, navKey: 'settings.nav.providers' },
  { id: 'autosave',  render: renderAutosavePage,  navKey: 'settings.nav.autosave' },
  { id: 'ui',        render: renderUiPage,        navKey: 'settings.nav.ui' },
  { id: 'about',     render: renderAboutPage,     navKey: 'settings.nav.about' },
];

const LS_KEY = 'testbench:settings:active_subpage';

export function mountSettingsWorkspace(host) {
  host.classList.add('two-col');
  host.innerHTML = '';

  const nav = el('div', { className: 'subnav' });
  const pane = el('div', {});
  host.append(nav, pane);

  const initial = localStorage.getItem(LS_KEY) || PAGES[0].id;

  const buttons = {};
  for (const page of PAGES) {
    const btn = el('button', {
      className: 'subnav-item',
      onClick: () => selectPage(page.id),
    }, i18n(page.navKey));
    buttons[page.id] = btn;
    nav.append(btn);
  }

  // 会话变更时刷新用: 记录当前子页; 若当前 workspace 不可见先打 dirty 标记.
  let currentId = initial;
  let dirty = false;

  function selectPage(id) {
    const page = PAGES.find((p) => p.id === id) || PAGES[0];
    currentId = page.id;
    dirty = false;
    for (const [bid, btn] of Object.entries(buttons)) {
      btn.classList.toggle('active', bid === page.id);
    }
    localStorage.setItem(LS_KEY, page.id);
    pane.innerHTML = '';
    const subpage = el('div', { className: 'subpage active', 'data-subpage': page.id });
    pane.append(subpage);
    // renderer 可能是 async, 但本体同步创建 DOM (异步填充). 我们不 await.
    Promise.resolve(page.render(subpage)).catch((err) => {
      console.error(`[settings] render ${page.id} failed:`, err);
      subpage.innerHTML = '';
      subpage.append(el('div', { className: 'empty-state' },
        `子页渲染失败: ${err?.message || err}`));
    });
  }

  // 会话创建/销毁自动刷新当前可见子页 (Models 等依赖会话的页能立即反映状态);
  // 不可见时只标脏, 下次切回来再刷, 避免无谓的后台请求.
  on('session:change', () => {
    if (store.active_workspace === 'settings') {
      selectPage(currentId);
    } else {
      dirty = true;
    }
  });
  on('active_workspace:change', (id) => {
    if (id === 'settings' && dirty) {
      selectPage(currentId);
    }
  });

  // 外部组件 (例如首页的 AI 模型配置提醒横幅) 需要直接跳到某个子页. 和
  // `setup:goto_page` 对称 — 未挂载态只更新 LS, 下次切回 settings 会读 LS.
  on('settings:goto_page', (pageId) => {
    if (typeof pageId !== 'string' || !PAGES.some((p) => p.id === pageId)) return;
    localStorage.setItem(LS_KEY, pageId);
    if (store.active_workspace === 'settings') {
      selectPage(pageId);
    } else {
      currentId = pageId;
      dirty = true;
    }
  });

  selectPage(initial);
}
