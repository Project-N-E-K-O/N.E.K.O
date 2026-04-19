/**
 * topbar_stage_chip.js — Stage Coach 顶栏 chip (P14).
 *
 * 根据当前 active_workspace 决定两种形态:
 *   - setup / chat     → 展开为 "Stage: 对话 ▶︎ [去 Chat 发送消息] [预览] [执行并推进] [跳过] [回退] [⋯ 展开面板]"
 *   - evaluation / diagnostics / settings → 折叠为 "Stage: 对话 ▾" 小徽章
 *
 * 两种形态都可以点右侧触发器展开**下拉面板**, 里面包含:
 *   - 完整的流水线阶段可视化 (6 个阶段圆点 / 当前高亮)
 *   - 推荐 op 卡片 (label + description + when_to_run / when_to_skip)
 *   - 上下文快照面板 (messages / memory_counts / persona_configured / auto_running 等)
 *   - 最近几条 history 记录
 *   - 四个动作按钮: 预览 / 执行并推进 / 跳过 / 回退 (回退下拉选任意 stage)
 *
 * 数据来自 GET /api/stage (``describe_stage`` 返回体). 任何 advance/skip/rewind
 * 请求成功后本地立刻用返回体更新, 避免二次请求. 会话变动 (session:change)
 * 和 active_workspace 切换时重新拉取以保持快照新鲜.
 *
 * PLAN §P14: chip 不自动 advance — 所有 stage 切换都必须由测试人员显式点按钮.
 * 这里不在 /chat/send 成功回调里塞 advance 逻辑.
 */

import { i18n } from '../core/i18n.js';
import { api } from '../core/api.js';
import { toast } from '../core/toast.js';
import { store, set, on, emit } from '../core/state.js';

// 顶部说明 banner 的折叠状态持久 (默认展开, 用户手动折叠后记住).
const INTRO_COLLAPSED_LS = 'testbench:stage:intro_collapsed';

// ── 小工具 ──────────────────────────────────────────────────────────

function el(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (v == null) continue;
    if (k === 'className') node.className = v;
    else if (k === 'onClick') node.addEventListener('click', v);
    else if (k.startsWith('data-')) node.setAttribute(k, v);
    else if (k === 'title' || k === 'disabled' || k === 'type') node[k] = v;
    else node.setAttribute(k, v);
  }
  for (const c of children) {
    if (c == null || c === false) continue;
    node.append(c instanceof Node ? c : document.createTextNode(String(c)));
  }
  return node;
}

/** setup/chat 默认展开; 其它 workspace 折叠. */
function isExpandedWorkspace(ws) {
  return ws === 'setup' || ws === 'chat';
}

// ── 组件 ────────────────────────────────────────────────────────────

export function mountStageChip(host) {
  const container = el('div', { className: 'stage-chip-wrap dropdown' });
  host.append(container);

  // 缓存最新 describe_stage 返回体 + 单航 fetch promise.
  let lastData = null;
  let fetchInflight = null;

  // 下拉面板 (全状态详细视图); 默认隐藏, 通过 chip 上的触发按钮开合.
  // 与顶栏其它 dropdown 不同, 这里自带关闭逻辑 (文档级 click 捕获 + ESC).
  const panel = el('div', {
    className: 'dropdown-menu stage-panel',
    'data-align': 'right',
  });
  let panelOpen = false;
  function openPanel() {
    if (panelOpen) return;
    panelOpen = true;
    panel.classList.add('open');
    // 打开时刷一次快照, 保证 messages/memory count 是新的.
    refresh().catch(() => {});
  }
  function closePanel() {
    if (!panelOpen) return;
    panelOpen = false;
    panel.classList.remove('open');
  }
  document.addEventListener('click', (ev) => {
    if (!panelOpen) return;
    if (container.contains(ev.target)) return;
    closePanel();
  });
  document.addEventListener('keydown', (ev) => {
    if (ev.key === 'Escape') closePanel();
  });

  // 主 chip 内容 (collapsed) 或扩展工具栏 (expanded).
  const chipSlot = el('div', { className: 'stage-chip-slot' });
  container.append(chipSlot, panel);

  // ── 渲染 ────────────────────────────────────────────────────────

  function renderAll() {
    const ws = store.active_workspace;
    const expanded = isExpandedWorkspace(ws);
    renderChip(expanded);
    renderPanel();
  }

  function renderChip(expanded) {
    chipSlot.innerHTML = '';
    const session = store.session;
    const stageName = lastData?.current;
    const stageShort = stageName
      ? i18n(`stage.name_short.${stageName}`)
      : null;

    // 触发按钮 / 标签: 点了总是开/关面板.
    const chip = el('button', {
      className: 'chip stage-chip' + (session ? '' : ' muted'),
      title: session
        ? (panelOpen
          ? i18n('stage.chip.collapse_hint')
          : i18n('stage.chip.expand_hint'))
        : i18n('stage.toast.no_session'),
      onClick: (ev) => {
        ev.stopPropagation();
        if (!session) {
          toast.info(i18n('stage.toast.no_session'));
          return;
        }
        if (panelOpen) closePanel();
        else openPanel();
      },
    });
    chip.append(
      el('span', {},
        session && stageShort
          ? i18n('stage.chip.collapsed_prefix', stageShort)
          : i18n('stage.chip.no_session')),
      el('span', { className: 'caret' }, '▾'),
    );
    chipSlot.append(chip);

    // 展开形态下, 旁边额外挂一排动作按钮 (PLAN §P14: 单行 Preview/Advance/Skip/Rewind).
    if (expanded && session && lastData) {
      const op = lastData.suggested_op || {};
      const actions = el('div', { className: 'stage-inline-actions' });

      // [目标页] 跳转 (如果 op 指定了 ui_action).
      if (op.ui_action) {
        const navBtn = el('button', {
          className: 'chip stage-action-btn stage-action-nav',
          title: i18n(op.label_i18n_key),
          onClick: (ev) => {
            ev.stopPropagation();
            handleUiAction(op.ui_action);
          },
        }, truncate(i18n(op.label_i18n_key), 18));
        actions.append(navBtn);
      }

      actions.append(
        actionBtn('stage.buttons.preview', handlePreview, {
          disabled: !op.dry_run_available,
          title: op.dry_run_available
            ? null
            : i18n('stage.buttons.preview_disabled_hint'),
        }),
        actionBtn('stage.buttons.advance', handleAdvance, { primary: true }),
        actionBtn('stage.buttons.skip', handleSkip, {}),
        actionBtn('stage.buttons.rewind_open', handleRewindOpen, {}),
      );
      chipSlot.append(actions);
    }
  }

  function renderPanel() {
    panel.innerHTML = '';
    if (!store.session) {
      panel.append(el('div', { className: 'stage-panel-empty' },
        i18n('stage.toast.no_session')));
      return;
    }
    if (!lastData) {
      panel.append(el('div', { className: 'stage-panel-empty' },
        i18n('common.loading')));
      return;
    }

    const op = lastData.suggested_op || {};
    const ctx = lastData.context_snapshot || {};
    const stages = lastData.stages || [];
    const currentIdx = stages.indexOf(lastData.current);

    // 顶部说明 banner — 首次打开时对新人强调 "这是 checklist 辅助表, 不是强制流程".
    // 用 details 折叠, 默认展开; 用户点 summary 可以收起, 状态存 localStorage 下次记住.
    const introWrap = el('details', { className: 'stage-panel-intro' });
    const introCollapsed = localStorage.getItem(INTRO_COLLAPSED_LS) === '1';
    if (!introCollapsed) introWrap.open = true;
    introWrap.addEventListener('toggle', () => {
      localStorage.setItem(INTRO_COLLAPSED_LS, introWrap.open ? '0' : '1');
    });
    const introSummary = el('summary', { className: 'stage-panel-intro-summary' },
      el('span', { className: 'stage-panel-intro-icon' }, 'ⓘ '),
      i18n('stage.panel.intro_title'));
    introWrap.append(introSummary);
    const introBody = el('div', { className: 'stage-panel-intro-body' });
    // intro_body 里有 markdown 风格的 **粗体** 和 \n 换行, 简易渲染: 按 \n 分段 + 按 ** 分割交替 strong.
    const rawIntro = i18n('stage.panel.intro_body');
    for (const line of rawIntro.split('\n')) {
      const p = el('p', { className: 'stage-panel-intro-line' });
      const parts = line.split('**');
      parts.forEach((seg, i) => {
        if (i % 2 === 1) p.append(el('strong', {}, seg));
        else if (seg) p.append(document.createTextNode(seg));
      });
      introBody.append(p);
    }
    introWrap.append(introBody);
    panel.append(introWrap);

    // 阶段轨迹条.
    const trackTitle = el('div', { className: 'stage-panel-section-title' },
      i18n('stage.panel.stage_bar_title'));
    const track = el('div', { className: 'stage-panel-track' });
    stages.forEach((s, i) => {
      const node = el('div', {
        className: 'stage-panel-track-node'
          + (i === currentIdx ? ' active' : (i < currentIdx ? ' past' : '')),
        title: i18n(`stage.name.${s}`),
        onClick: (ev) => {
          ev.stopPropagation();
          if (s === lastData.current) return;
          handleRewindTo(s);
        },
      }, String(i + 1));
      track.append(node);
      const label = el('div', { className: 'stage-panel-track-label' },
        i18n(`stage.name_short.${s}`));
      track.append(label);
    });

    panel.append(trackTitle, track);

    // 推荐 op 卡片.
    const opTitle = el('div', { className: 'stage-panel-section-title' },
      i18n('stage.panel.op_card_title'));
    const opCard = el('div', { className: 'stage-panel-op-card' });
    opCard.append(
      el('div', { className: 'stage-panel-op-label' },
        i18n(op.label_i18n_key || 'common.not_implemented')),
      el('div', { className: 'stage-panel-op-desc' },
        i18n(op.description_i18n_key || 'common.not_implemented')),
      el('div', { className: 'stage-panel-op-sub-title' },
        i18n('stage.panel.when_to_run')),
      el('div', { className: 'stage-panel-op-sub' },
        i18n(op.when_to_run_i18n_key || 'common.not_implemented')),
      el('div', { className: 'stage-panel-op-sub-title' },
        i18n('stage.panel.when_to_skip')),
      el('div', { className: 'stage-panel-op-sub' },
        i18n(op.when_to_skip_i18n_key || 'common.not_implemented')),
    );
    panel.append(opTitle, opCard);

    // 上下文快照.
    const ctxTitle = el('div', { className: 'stage-panel-section-title' },
      i18n('stage.panel.context_title'));
    const ctxBody = el('div', { className: 'stage-panel-context' });
    ctxBody.append(
      line(i18n('stage.context.messages_count', ctx.messages_count ?? 0) + ' '
        + i18n('stage.context.messages_split',
          ctx.user_messages_count ?? 0, ctx.assistant_messages_count ?? 0)),
      line(i18n('stage.context.last_message', ctx.last_message_role || '')),
      line(i18n('stage.context.memory_counts', ctx.memory_counts || {
        recent: 0, facts: 0, reflections: 0, persona_facts: 0,
      })),
      line(i18n('stage.context.persona_configured',
        !!ctx.persona_configured)),
      line(
        (ctx.pending_memory_previews && ctx.pending_memory_previews.length)
          ? i18n('stage.context.pending_previews', ctx.pending_memory_previews)
          : i18n('stage.context.pending_previews_none')),
      line(i18n('stage.context.script_loaded', !!ctx.script_loaded)),
      line(i18n('stage.context.auto_running', !!ctx.auto_running)),
      line(i18n('stage.context.virtual_now', ctx.virtual_now)),
      line(i18n('stage.context.pending_advance',
        ctx.virtual_pending_advance_seconds)),
    );
    if (Array.isArray(ctx.warnings) && ctx.warnings.length) {
      ctxBody.append(line(i18n('stage.context.warnings', ctx.warnings),
        'stage-panel-context-warning'));
    }
    panel.append(ctxTitle, ctxBody);

    // 动作按钮组 (面板内也放一遍, 让折叠态下也能直接操作).
    const btnRow = el('div', { className: 'stage-panel-actions' });
    const op2 = lastData.suggested_op || {};
    if (op2.ui_action) {
      btnRow.append(actionBtn('stage.buttons.go_target',
        () => { handleUiAction(op2.ui_action); }, { secondary: true }));
    }
    btnRow.append(
      actionBtn('stage.buttons.preview', handlePreview, {
        disabled: !op2.dry_run_available,
        title: op2.dry_run_available ? null
          : i18n('stage.buttons.preview_disabled_hint'),
      }),
      actionBtn('stage.buttons.advance', handleAdvance, { primary: true }),
      actionBtn('stage.buttons.skip', handleSkip, {}),
    );
    panel.append(btnRow);

    // History.
    const histTitle = el('div', { className: 'stage-panel-section-title' },
      i18n('stage.panel.history_title'));
    const hist = el('div', { className: 'stage-panel-history' });
    const entries = Array.isArray(lastData.history) ? lastData.history : [];
    if (!entries.length) {
      hist.append(el('div', { className: 'stage-panel-history-empty' },
        i18n('stage.panel.history_empty')));
    } else {
      const recent = entries.slice(-6).reverse();
      for (const e of recent) {
        hist.append(el('div', { className: 'stage-panel-history-row' },
          el('span', { className: 'stage-panel-history-at' }, e.at || ''),
          ` ${e.action || ''} → `,
          el('span', { className: 'stage-panel-history-stage' },
            i18n(`stage.name_short.${e.stage}`) || e.stage),
          e.skipped ? ' (skipped)' : '',
        ));
      }
    }
    panel.append(histTitle, hist);
  }

  function line(text, cls) {
    return el('div', { className: 'stage-panel-context-line' + (cls ? ' ' + cls : '') },
      text);
  }

  function actionBtn(labelKey, handler, { primary, secondary, disabled, title } = {}) {
    let cls = 'chip stage-action-btn';
    if (primary) cls += ' stage-action-primary';
    if (secondary) cls += ' stage-action-secondary';
    const btn = el('button', {
      className: cls,
      disabled: !!disabled,
      title: title || null,
      onClick: (ev) => {
        ev.stopPropagation();
        if (disabled) {
          if (title) toast.info(title);
          return;
        }
        handler();
      },
    }, i18n(labelKey));
    return btn;
  }

  function truncate(s, n) {
    if (!s) return '';
    return s.length > n ? s.slice(0, n - 1) + '…' : s;
  }

  // ── 交互 ────────────────────────────────────────────────────────

  async function handlePreview() {
    const res = await api.post('/api/stage/preview', {},
      { expectedStatuses: [412] });
    if (res.ok) {
      toast.info(i18n('common.not_implemented'));
    } else {
      toast.info(i18n('stage.toast.preview_unsupported'));
    }
  }

  async function handleAdvance() {
    const from = lastData?.current;
    const res = await api.post('/api/stage/advance', {});
    if (res.ok && res.data) {
      applyStageResponse(res.data);
      toast.ok(i18n('stage.toast.advance_ok',
        i18n(`stage.name_short.${from}`) || from,
        i18n(`stage.name_short.${res.data.current}`) || res.data.current));
    } else {
      toast.err(i18n('stage.toast.advance_failed'),
        { message: res.error?.message });
    }
  }

  async function handleSkip() {
    const from = lastData?.current;
    const res = await api.post('/api/stage/skip', {});
    if (res.ok && res.data) {
      applyStageResponse(res.data);
      toast.ok(i18n('stage.toast.skip_ok',
        i18n(`stage.name_short.${from}`) || from,
        i18n(`stage.name_short.${res.data.current}`) || res.data.current));
    } else {
      toast.err(i18n('stage.toast.advance_failed'),
        { message: res.error?.message });
    }
  }

  function handleRewindOpen() {
    openPanel();
    // 面板里的 track node 已经支持点任一阶段回退; 这里只是把下拉面板弹起来
    // 并 toast 一句提示让测试人员知道"点阶段圆点就能跳".
    toast.info(i18n('stage.panel.stage_bar_title') + ': '
      + i18n('stage.buttons.rewind_apply'));
  }

  async function handleRewindTo(targetStage) {
    const from = lastData?.current;
    const res = await api.post('/api/stage/rewind', { target_stage: targetStage });
    if (res.ok && res.data) {
      applyStageResponse(res.data);
      toast.ok(i18n('stage.toast.rewind_ok',
        i18n(`stage.name_short.${from}`) || from,
        i18n(`stage.name_short.${res.data.current}`) || res.data.current));
    } else {
      toast.err(i18n('stage.toast.advance_failed'),
        { message: res.error?.message });
    }
  }

  function handleUiAction(action) {
    switch (action) {
      case 'nav_to_setup_persona':
        set('active_workspace', 'setup');
        emit('setup:goto_page', 'persona');
        toast.info(i18n('stage.action.nav_persona'));
        break;
      case 'nav_to_setup_memory':
        set('active_workspace', 'setup');
        emit('setup:goto_page', 'memory_recent');
        toast.info(i18n('stage.action.nav_memory'));
        break;
      case 'nav_to_chat_preview':
        set('active_workspace', 'chat');
        toast.info(i18n('stage.action.nav_chat_preview'));
        break;
      case 'chat_send_hint':
        set('active_workspace', 'chat');
        toast.info(i18n('stage.action.nav_chat_send'));
        break;
      case 'memory_trigger_hint':
        set('active_workspace', 'setup');
        emit('setup:goto_page', 'memory_recent');
        toast.info(i18n('stage.action.nav_memory'));
        break;
      case 'evaluation_pending':
        toast.info(i18n('stage.action.evaluation_pending_toast'));
        break;
      default:
        toast.info(i18n('common.not_implemented'));
    }
    closePanel();
  }

  // ── 状态同步 ───────────────────────────────────────────────────

  function applyStageResponse(data) {
    lastData = data;
    renderAll();
    emit('stage:change', data);
  }

  async function refresh() {
    if (!store.session) {
      lastData = null;
      renderAll();
      return;
    }
    if (fetchInflight) return fetchInflight;
    fetchInflight = (async () => {
      const res = await api.get('/api/stage', { expectedStatuses: [404] });
      if (res.ok) {
        lastData = res.data;
      } else {
        lastData = null;
      }
      renderAll();
    })().finally(() => { fetchInflight = null; });
    return fetchInflight;
  }

  // 初次渲染 + 事件订阅.
  renderAll();
  refresh().catch(() => {});

  on('session:change', () => {
    lastData = null;
    closePanel();
    refresh().catch(() => {});
  });
  on('active_workspace:change', () => {
    // workspace 切换只需要重新决定展开/折叠外观, 不强制重新拉数据.
    renderAll();
  });
  // 其它组件完成了副作用 op (如 chat.send / memory.accept) 后可 emit 此事件.
  // 本文件里发 advance/skip/rewind 也会 emit, 但自己订阅会形成无害循环,
  // 所以只监听**外部来源** — 靠 payload.source !== 'self' 区分.
  on('stage:needs_refresh', () => { refresh().catch(() => {}); });
}
