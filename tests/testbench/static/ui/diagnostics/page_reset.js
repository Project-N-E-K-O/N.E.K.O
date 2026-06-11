/**
 * page_reset.js — Diagnostics → Reset 子页 (P20).
 *
 * 三级 Reset UI. 每级是一个独立 panel, 里面说明:
 *   - 会清什么
 *   - 会保什么
 *   - 是否有 pre_reset_backup 兜底 (有, 所有三级都有)
 *   - [执行 Reset] 按钮 → 弹出二次确认 modal 再次列细节, 必须显式点
 *     "确认执行" 才发 POST /api/session/reset
 *
 * 为什么不用 window.confirm:
 *   - 原生 confirm 只能给一行文字, 不能列出"会清什么/会保什么"
 *   - 二次确认对 Hard Reset 这种不可撤销操作尤其重要, 用自定义 modal
 *     能强制用户读列表再点按钮
 *
 * 响应处理:
 *   - 成功 → toast 显示 `{level} 完成, 清除 N 条消息/..., pre_reset_backup 快照 id`
 *   - 409 → 有其它长流水正在运行, 让用户等
 *   - 404 → 没有会话
 *   - 400 → confirm 字段错 (前端 bug, 只有硬编码 true 才可能触发)
 *   - 其它 → 原样展示 detail
 *
 * Reset 成功后 emit 一组 `*:needs_refresh` 事件, 让 chat messages /
 * memory / stage 组件各自重拉; 同时 emit `snapshots:changed` 让时间线
 * chip 看到新增的 pre_reset_backup 和 init_after_<level>.
 */

import { api } from '../../core/api.js';
import { i18n } from '../../core/i18n.js';
import { store, emit, set as setStore } from '../../core/state.js';
import { toast } from '../../core/toast.js';
import { el } from '../_dom.js';

const LEVELS = ['soft', 'medium', 'hard'];

function defaultState() {
  return {
    running: false,
    confirmLevel: null,     // 'soft' | 'medium' | 'hard' | null
    lastResult: null,       // 上一次成功执行的 stats, 短暂显示
  };
}

export async function renderResetPage(host) {
  host.innerHTML = '';
  host.classList.add('diag-reset');
  const state = defaultState();
  renderAll(state, host);
}

function renderAll(state, host) {
  host.innerHTML = '';

  host.append(
    el('h2', {}, i18n('diagnostics.reset.title')),
    el('p', { className: 'diag-page-intro' },
      i18n('diagnostics.reset.intro')),
  );

  if (!store.session) {
    host.append(el('div', { className: 'empty-state' },
      i18n('diagnostics.reset.no_session')));
    return;
  }

  // 三级 panel 并排 (桌面端, 窄屏会自动换行).
  const grid = el('div', { className: 'diag-reset-grid' });
  for (const level of LEVELS) {
    grid.append(renderLevelPanel(level, state, host));
  }
  host.append(grid);

  // 上次执行结果显示条 (toast 一闪而过, 不够人看清楚; 在这里保留).
  if (state.lastResult) {
    host.append(renderLastResult(state.lastResult));
  }

  // 二次确认 modal.
  if (state.confirmLevel) {
    host.append(renderConfirmModal(state, host));
  }
}

function renderLevelPanel(level, state, host) {
  const panel = el('div', {
    className: `diag-reset-panel level-${level}`,
  });

  panel.append(
    el('div', { className: 'diag-reset-panel-header' },
      el('h3', {}, i18n(`diagnostics.reset.level.${level}.title`)),
      el('span', {
        className: `diag-reset-severity severity-${level}`,
      }, i18n(`diagnostics.reset.level.${level}.severity`)),
    ),
    el('p', { className: 'diag-reset-panel-desc' },
      i18n(`diagnostics.reset.level.${level}.desc`)),
    renderList('removed', level),
    renderList('preserved', level),
    el('p', { className: 'diag-reset-backup-note' },
      i18n('diagnostics.reset.backup_note')),
    el('button', {
      className: `diag-reset-exec ghost level-${level}`,
      disabled: state.running,
      onClick: () => {
        state.confirmLevel = level;
        renderAll(state, host);
      },
    }, i18n(`diagnostics.reset.level.${level}.exec_btn`)),
  );
  return panel;
}

function renderList(kind, level) {
  const items = getBulletItems(kind, level);
  const wrap = el('div', { className: `diag-reset-bullets ${kind}` });
  wrap.append(el('div', { className: 'diag-reset-bullets-label' },
    i18n(`diagnostics.reset.bullets.${kind}_label`)));
  const ul = el('ul', {});
  for (const item of items) {
    ul.append(el('li', {}, item));
  }
  wrap.append(ul);
  return wrap;
}

function getBulletItems(kind, level) {
  //:  bullet 内容通过 i18n 的数组值返回, 每级每类 (removed/preserved)
  //:  各有一个固定数组. i18n.js 里每项是一个独立字符串, 我们按 key
  //:  列表展开, 命中就加. 这样文案可以逐条翻译, 未来加语言不影响代码.
  const BULLET_KEYS = {
    soft: {
      removed: ['messages', 'eval_results'],
      preserved: ['persona', 'memory', 'clock', 'model_config', 'schemas', 'timeline', 'stage'],
    },
    medium: {
      removed: ['messages', 'eval_results', 'memory'],
      preserved: ['persona', 'clock', 'model_config', 'schemas', 'timeline', 'stage'],
    },
    hard: {
      removed: ['messages', 'eval_results', 'memory', 'persona', 'clock', 'stage', 'timeline_non_backup'],
      preserved: ['model_config', 'schemas', 'timeline_backups'],
    },
  };
  const keys = (BULLET_KEYS[level] || {})[kind] || [];
  return keys.map((k) => i18n(`diagnostics.reset.bullet.${k}`));
}

function renderConfirmModal(state, host) {
  const level = state.confirmLevel;
  const modal = el('div', { className: 'diag-reset-modal' });
  const inner = el('div', {
    className: `diag-reset-modal-inner level-${level}`,
  });

  inner.append(
    el('h3', {},
      i18n('diagnostics.reset.confirm.title_fmt',
        i18n(`diagnostics.reset.level.${level}.title`))),
    el('p', { className: 'diag-reset-modal-desc' },
      i18n(`diagnostics.reset.level.${level}.confirm_desc`)),
    renderList('removed', level),
    renderList('preserved', level),
    el('p', { className: 'diag-reset-modal-warn' },
      i18n('diagnostics.reset.confirm.warn')),
    el('div', { className: 'diag-reset-modal-actions' },
      el('button', {
        className: 'ghost tiny',
        disabled: state.running,
        onClick: () => {
          state.confirmLevel = null;
          renderAll(state, host);
        },
      }, i18n('diagnostics.reset.confirm.cancel')),
      el('button', {
        className: `diag-reset-modal-exec danger level-${level}`,
        disabled: state.running,
        onClick: () => doReset(level, state, host),
      }, i18n('diagnostics.reset.confirm.do_fmt',
        i18n(`diagnostics.reset.level.${level}.title`))),
    ),
  );
  modal.append(inner);
  // 点背景关 (仅在未执行时).
  modal.addEventListener('click', (ev) => {
    if (ev.target === modal && !state.running) {
      state.confirmLevel = null;
      renderAll(state, host);
    }
  });
  return modal;
}

function renderLastResult(stats) {
  const wrap = el('div', { className: 'diag-reset-last-result' });
  wrap.append(
    el('div', { className: 'diag-reset-last-result-head' },
      i18n('diagnostics.reset.last_result.title_fmt',
        i18n(`diagnostics.reset.level.${stats.level}.title`))),
  );
  const removed = stats.removed || {};
  const ul = el('ul', { className: 'diag-reset-last-result-list' });
  const pairs = [
    ['messages', removed.messages],
    ['eval_results', removed.eval_results],
    ['memory_files', removed.memory_files],
    ['app_docs_files', removed.app_docs_files],
    ['snapshots', removed.snapshots],
  ];
  for (const [k, v] of pairs) {
    if (!Number.isFinite(v) || v <= 0) continue;
    ul.append(el('li', {},
      i18n(`diagnostics.reset.last_result.kind.${k}`),
      ': ',
      String(v)));
  }
  wrap.append(ul);
  if (stats.pre_reset_backup_id) {
    wrap.append(el('p', { className: 'diag-reset-last-result-backup' },
      i18n('diagnostics.reset.last_result.backup_fmt',
        stats.pre_reset_backup_id)));
  }
  return wrap;
}

async function doReset(level, state, host) {
  state.running = true;
  renderAll(state, host);

  const res = await api.post(
    '/api/session/reset',
    { level, confirm: true },
    { expectedStatuses: [404, 409] },
  );

  state.running = false;
  if (res.ok) {
    state.confirmLevel = null;
    state.lastResult = res.data?.stats || null;
    toast.ok(i18n('diagnostics.reset.toast.done_fmt',
      i18n(`diagnostics.reset.level.${level}.title`)));

    // ── Hard / Medium: 强制 reload 路径 ──────────────────────────
    // Hard Reset 语义 = "沙盒清零 + persona 清零 + memory 清零 + 时间
    // 线裁剪". 后端执行完后, session.id 保留, 但前端所有挂载的组件
    // (composer / preview / memory 页 / persona 页 / stage chip / ...
    // 共 15+ 个 `session:change` 订阅者) 仍然活着, 各自持有旧的 UI
    // state / timer / cached DOM. 继续 emit 'session:change' 让它们
    // 各自 refresh 会:
    //   (a) 突发触发 15+ 个并发 fetch, 其中 memory/persona 系列因
    //       persona.character_name="" 统统返 409, api.js 广播
    //       http:error → errors_bus 镜像到后端 → Diagnostics → Errors
    //       子页再 listener 链重渲, 形成"合法但昂贵"的放大链.
    //   (b) 某些组件在"有 session 但状态全空"下的渲染路径从未经过
    //       压测 (P18 rewind 恢复的是有数据的快照), 存在未知的 UI
    //       边界 bug.
    //
    // 2026-04-20 用户反馈"Hard Reset 后浏览器卡 → 电脑黑屏硬重启" —
    // 最可能就是 (a) + (b) 叠加把用户系统资源推上临界点.
    //
    // 2026-04-22 P24 §4.27 #105 同族 sweep: **Medium Reset 也纳入 reload**
    // — LESSONS §7 #20 量化判据第 5 条 "memory/ 目录被清空或替换" 命中
    // Medium (语义是"清 messages + 清 memory, 保留 persona"), 同族地雷.
    // Soft Reset 仍走 surgical: 只清 messages, persona/memory 仍在, 订阅
    // 者的"有数据"渲染路径安全.
    if (level === 'hard' || level === 'medium') {
      // 给 toast 200ms 显示时间让用户看到成功提示再刷.
      setTimeout(() => {
        try { window.location.reload(); } catch { /* ignore */ }
      }, 300);
      renderAll(state, host);
      return;
    }

    // Soft Reset: 只清 messages, 保留 persona + memory. 精简广播 —
    // 只发真有订阅者的事件 (messages:needs_refresh / memory:needs_refresh
    // 当前没人订阅, 发了是噪音; stage + snapshots 有订阅者, 保留).
    emit('snapshots:changed', { reason: 'reset', level });
    emit('stage:needs_refresh', { source: 'reset_page' });
    // 拉一次 session 描述同步 snapshot_count / message_count.
    // 用 try/catch 吞异常: 即使 /api/session 返 404 也不影响 toast.
    try {
      const sres = await api.get('/api/session', { expectedStatuses: [404] });
      if (sres.ok && sres.data?.has_session) {
        setStore('session', sres.data);
      }
    } catch { /* ignore */ }
  } else if (res.status === 409) {
    toast.err(i18n('diagnostics.reset.toast.busy_fmt',
      res.error?.busy_op || '?'));
  } else if (res.status === 404) {
    toast.err(i18n('diagnostics.reset.no_session'));
  } else {
    toast.err(i18n('diagnostics.reset.toast.failed_fmt',
      res.error?.message || `HTTP ${res.status}`));
  }
  renderAll(state, host);
}
