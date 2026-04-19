/**
 * workspace_setup.js — Setup workspace 入口 (P05 起).
 *
 * 布局沿用 `workspace.two-col` (nav + pane). 子页清单 (P05~P07):
 *   - Persona            — P05 实装; 编辑当前会话 persona 元数据
 *   - Import             — P05 实装; 从真实 characters.json 拷贝
 *   - Virtual Clock      — P06 实装; 虚拟时钟完整滚动游标
 *   - Scripts            — P12.5 实装; 对话剧本模板编辑器 (dialog_templates)
 *   - [分组: 记忆]       — P07 加入, 下辖 4 子页:
 *       · Recent         — recent.json 原始对话
 *       · Facts          — facts.json 事实池
 *       · Reflections    — reflections.json 反思
 *       · Persona        — persona.json 人设记忆 (注意和上方的 Persona 子页
 *                          不同: 上方那个是会话的 master/character 配置;
 *                          这个是 PersonaManager 的三层记忆档案)
 *
 * 分组标题 (`kind: 'group'`) 不是可点子页, 只是 subnav 的一条非交互标签.
 * 这样 P07 追加 4 个记忆页时不用再发明二级 nav, 左栏扁平保持统一风格.
 */

import { i18n } from '../core/i18n.js';
import { store, on } from '../core/state.js';
import { el } from './_dom.js';
import { renderPersonaPage } from './setup/page_persona.js';
import { renderImportPage } from './setup/page_import.js';
import { renderVirtualClockPage } from './setup/page_virtual_clock.js';
import { renderScriptsPage } from './setup/page_scripts.js';
import { renderMemoryRecentPage } from './setup/page_memory_recent.js';
import { renderMemoryFactsPage } from './setup/page_memory_facts.js';
import { renderMemoryReflectionsPage } from './setup/page_memory_reflections.js';
import { renderMemoryPersonaPage } from './setup/page_memory_persona.js';

// kind: 'page' 可点子页, 'group' 仅做视觉分组标题 (不可选中, 无 render).
const PAGES = [
  { kind: 'page',  id: 'persona',            render: renderPersonaPage,            navKey: 'setup.nav.persona' },
  { kind: 'page',  id: 'import',             render: renderImportPage,             navKey: 'setup.nav.import' },
  { kind: 'page',  id: 'virtual_clock',      render: renderVirtualClockPage,       navKey: 'setup.nav.virtual_clock' },
  { kind: 'page',  id: 'scripts',            render: renderScriptsPage,            navKey: 'setup.nav.scripts' },
  { kind: 'group', navKey: 'setup.nav.memory_group' },
  { kind: 'page',  id: 'memory_recent',      render: renderMemoryRecentPage,       navKey: 'setup.nav.memory_recent' },
  { kind: 'page',  id: 'memory_facts',       render: renderMemoryFactsPage,        navKey: 'setup.nav.memory_facts' },
  { kind: 'page',  id: 'memory_reflections', render: renderMemoryReflectionsPage,  navKey: 'setup.nav.memory_reflections' },
  { kind: 'page',  id: 'memory_persona',     render: renderMemoryPersonaPage,      navKey: 'setup.nav.memory_persona' },
];

const LS_KEY = 'testbench:setup:active_subpage';

function firstPage() {
  return PAGES.find((p) => p.kind === 'page');
}

export function mountSetupWorkspace(host) {
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
    if (page.kind === 'group') {
      nav.append(el('div', { className: 'subnav-group' }, i18n(page.navKey)));
      continue;
    }
    const btn = el('button', {
      className: 'subnav-item',
      onClick: () => selectPage(page.id),
    }, i18n(page.navKey));
    buttons[page.id] = btn;
    nav.append(btn);
  }

  // 当前选中的子页 id, 给会话变更时的自动刷新用.
  let currentId = initial;
  // 如果会话在 Setup 不可见时变了, 不立刻重渲染, 打个 dirty 标记等下次切回来再刷.
  // 否则每次切会话都要顺便把不可见的 workspace 也跑一遍请求, 没必要.
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
      console.error(`[setup] render ${page.id} failed:`, err);
      subpage.innerHTML = '';
      subpage.append(el('div', { className: 'empty-state' },
        `子页渲染失败: ${err?.message || err}`));
    });
  }

  // 会话创建/销毁时自动刷新当前可见子页; 其余 workspace 延迟到再次激活时刷新.
  // 子页本身的 "no session" / "no character" 空态能处理会话从无到有或反向的情况.
  on('session:change', () => {
    if (store.active_workspace === 'setup') {
      selectPage(currentId);
    } else {
      dirty = true;
    }
  });
  on('active_workspace:change', (id) => {
    if (id === 'setup' && dirty) {
      selectPage(currentId);
    }
  });

  // P14 Stage Coach 的 "跳转到目标页" 按钮会发这条事件, 内容是目标 subpage id
  // (e.g. 'persona' / 'memory_recent'). 它只需在挂载态生效 — 未挂载时 subpage
  // 的选择由 LS 在下次挂载读取, 所以这里不做 store-then-defer.
  on('setup:goto_page', (pageId) => {
    if (typeof pageId !== 'string' || !PAGES.some((p) => p.kind === 'page' && p.id === pageId)) {
      return;
    }
    localStorage.setItem(LS_KEY, pageId);
    if (store.active_workspace === 'setup') {
      selectPage(pageId);
    } else {
      currentId = pageId;
      dirty = true;
    }
  });

  selectPage(initial);
}
