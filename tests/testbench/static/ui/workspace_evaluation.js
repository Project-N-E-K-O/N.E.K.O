/**
 * workspace_evaluation.js — Evaluation workspace 入口 (P15 起).
 *
 * 布局沿用 `workspace.two-col` (subnav + pane), 子页清单:
 *   - Schemas   — P15 实装; ScoringSchema CRUD + Preview.
 *   - Run       — P16 实装; 选会话 / 选消息子集 / 选 schema / 触发四类
 *                 Judger (LLM / Heuristic / Comparative / Prompt), 支持
 *                 persist=false 的 dry-run 预览.
 *   - Results   — P17 实装; Judger 结果列表, 详情抽屉, 过滤/分页, 导出
 *                 Markdown / JSON 报告, 重试失败项.
 *   - Aggregate — P17 实装; 会话 / schema 维度的平均分与分布统计.
 *
 * 不同于 Setup workspace, Evaluation 本身不需要活动 session — Schemas
 * 子页管理全局资产, 没有 session 也能编辑. Run / Results / Aggregate 会
 * 自己判 session / 筛选, 切换子页不会强制联动活动会话.
 */

import { i18n } from '../core/i18n.js';
import { store, on } from '../core/state.js';
import { el } from './_dom.js';
import { renderSchemasPage } from './evaluation/page_schemas.js';
import { renderRunPage } from './evaluation/page_run.js';
import { renderResultsPage } from './evaluation/page_results.js';
import { renderAggregatePage } from './evaluation/page_aggregate.js';

const PAGES = [
  { kind: 'page', id: 'schemas',   render: renderSchemasPage,    navKey: 'evaluation.nav.schemas' },
  { kind: 'page', id: 'run',       render: renderRunPage,        navKey: 'evaluation.nav.run' },
  { kind: 'page', id: 'results',   render: renderResultsPage,    navKey: 'evaluation.nav.results' },
  { kind: 'page', id: 'aggregate', render: renderAggregatePage,  navKey: 'evaluation.nav.aggregate' },
];

const LS_KEY = 'testbench:evaluation:active_subpage';

function firstPage() {
  return PAGES.find((p) => p.kind === 'page');
}

export function mountEvaluationWorkspace(host) {
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
      console.error(`[evaluation] render ${page.id} failed:`, err);
      subpage.innerHTML = '';
      subpage.append(el('div', { className: 'empty-state' },
        `子页渲染失败: ${err?.message || err}`));
    });
  }

  // Schemas 子页是 session-agnostic 的, session 变化不触发重渲染;
  // Run / Results 子页 P16/P17 实装后再按需监听.
  on('session:change', () => {
    if (store.active_workspace === 'evaluation' && currentId !== 'schemas') {
      selectPage(currentId);
    } else if (currentId !== 'schemas') {
      dirty = true;
    }
  });

  // 跨 workspace 导航 hint 消费 (P17 徽章点击):
  //
  // 设计原则: "force-remount 由导航协调者决定, 不由接收子页决定".
  //
  // 历史踩点 (#76, #77): 最初把 hint 消费放到 page_results.js 的 mount 时 ——
  // "warm-other-subpage" (从 Schemas 跳 Results) 工作正常, 但 "warm-same-subpage"
  // (从 Results 跳 Chat 再点徽章回 Results) 不 remount, 所以 hint 没被读. 后续
  // 给 page_results 加 on('ui_prefs:change') 监听在 jsdom 单测里能过, 但浏览器
  // 实测仍偶发失败 —— 原因是 "接收方订阅" 依赖子页挂载生命周期, 而子页挂载/
  // 拆除/切换的时机和全局状态事件流不同步, 各种栈帧交错下非常难稳定.
  //
  // 本版改用更简单的设计: workspace_evaluation 本身的生命周期贯穿 app, 监听
  // active_workspace:change 和 evaluation:navigate 永远存在. 在这两条 handler
  // 里统一检查 store.ui_prefs.evaluation_results_filter, 如果有就无条件
  // selectPage(stored) — 即使 currentId===stored 也要强制 remount, 让新的
  // renderResultsPage 经 applyHintFromStore 消费 hint. 这样:
  //   - cold 路径: mountEvaluationWorkspace 里的 selectPage(initial) 先消费 hint,
  //     新注册的 active_workspace:change listener 被同一次 emit 可见并再触发,
  //     此时 hint 已清 (null), 走 fallback 分支 no-op, 无重复 mount.
  //   - warm-other-subpage: active_workspace:change fires, hint 存在 →
  //     selectPage('results'), currentId 从 'schemas' 切到 'results', 消费 hint.
  //   - warm-same-subpage: active_workspace:change fires, hint 存在 →
  //     selectPage('results') 强制 remount (无视 currentId==='results'), 消费 hint.
  //
  // 关键不变式: hint 只存活一轮事件流 —— 谁先读到谁消费并写 null, 后来者都
  // 看到 null 并 no-op. 因此多路径同时命中 (例如 active_workspace:change 和
  // evaluation:navigate 都检测到 hint) 不会重复 mount.
  function consumeHintIfPresent() {
    const hint = store.ui_prefs?.evaluation_results_filter;
    if (!hint) return false;
    const stored = localStorage.getItem(LS_KEY);
    if (!stored || !PAGES.some((p) => p.kind === 'page' && p.id === stored)) return false;
    selectPage(stored);
    return true;
  }

  on('active_workspace:change', (id) => {
    if (id !== 'evaluation') return;
    if (consumeHintIfPresent()) return;
    // Fallback 常规路径: localStorage 指向的子页和当前 currentId 不一致时
    // 尊重 localStorage (视为"用户最近一次导航意图"), 否则按 dirty 决定.
    const stored = localStorage.getItem(LS_KEY);
    if (stored && stored !== currentId && PAGES.some((p) => p.kind === 'page' && p.id === stored)) {
      selectPage(stored);
    } else if (dirty) {
      selectPage(currentId);
    }
  });

  // 同 workspace 内由其它模块 (如 chat 内联徽章在已处于 Evaluation 时点击)
  // 发起的子页导航. payload = { subpage: 'results' | 'aggregate' | ... }.
  on('evaluation:navigate', (payload) => {
    const target = payload?.subpage;
    if (!target) return;
    if (!PAGES.some((p) => p.kind === 'page' && p.id === target)) return;
    if (store.active_workspace !== 'evaluation') {
      // 不在当前 workspace: 只更新 localStorage, 让 active_workspace:change
      // 里的逻辑在切换回来时再 selectPage. 这样避免 off-screen DOM 写入.
      localStorage.setItem(LS_KEY, target);
      return;
    }
    if (consumeHintIfPresent()) return;
    if (currentId !== target) selectPage(target);
  });

  selectPage(initial);
}
