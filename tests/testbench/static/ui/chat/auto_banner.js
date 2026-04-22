/**
 * chat/auto_banner.js — Chat workspace 顶部 Auto-Dialog 进度横幅 (P13).
 *
 * 职责:
 *   - 当有活跃 Auto-Dialog 时在 ``.chat-main`` 顶部渲染 sticky 横幅 (进度 N/M +
 *     step 标签 + [暂停]/[继续] + [停止]); 空闲时隐藏.
 *   - 对外暴露 ``startAutoDialog(config)`` 让 composer 的 [启动 Auto] 按钮驱动
 *     POST /api/chat/auto_dialog/start SSE 流 — 流打开后 banner 自动挂起 + 消
 *     费事件推进进度 + 把 SSE 里 user / assistant_start / delta / assistant
 *     等事件转发给 message_stream (与手动 /chat/send 完全同一条渲染路径).
 *   - mount 时主动调 GET /api/chat/auto_dialog/state 探测: 如果后端正在跑
 *     (比如测试人员刷新了页面但后端还在跑), banner 会立刻显示出来 (只给出
 *     控制按钮 + 当前进度, **不重新接 SSE** — POST SSE 连接无法跨请求复用,
 *     这是浏览器 SSE 的物理限制. UI 会看不到实时 delta, 但能看到计数 +
 *     Stop/Pause 按钮可用).
 *
 * SSE 事件消费 (与 pipeline/auto_dialog.py 的 schema 对齐):
 *   - start:          初始化 banner 状态, 从此挂起可见
 *   - turn_begin:     更新 "当前正在跑第几轮 + step 标签"
 *   - simuser_done:   stream.appendIncomingMessage(message)
 *   - user:           **去重** — auto_dialog._run_simuser_step 里手工 append
 *                     了 user 消息并 yield simuser_done, 没有 user 事件;
 *                     target step 走 stream_send(user_content=None) 也不会
 *                     yield user. 所以这里理论上收不到 user, 保留 default
 *                     分支兜底.
 *   - wire_built:     忽略 (诊断用, 未来可能显示字符数)
 *   - assistant_start/delta/assistant/usage: 透传给 message_stream (与 /send 同)
 *   - turn_done:      进度 +1, 更新文本
 *   - paused/resumed: 切换状态 + 按钮 label
 *   - stopped:        析构 banner, toast 最终结果
 *   - error:          toast 错误; 紧跟的 stopped 会关闭 banner
 *
 * ⚠️ 控制端点 (pause/resume/stop) 不走 SSE, 是普通 POST. 它们的作用是向
 * **正在跑的 start SSE** 的后端 generator 发信号; HTTP 响应只是 ack. 真正
 * 的状态变迁由后续 SSE {event:'paused'|'resumed'|'stopped'} 承载, banner
 * 等 SSE 事件到来才切 UI, 这样前后端状态严格一致, 不会出现 "点了暂停
 * 按钮 UI 变了但后端还没停" 的竞态幻觉.
 */

import { api } from '../../core/api.js';
import { i18n } from '../../core/i18n.js';
import { toast } from '../../core/toast.js';
import { emit } from '../../core/state.js';
import { el } from '../_dom.js';
import { streamPostSse } from './sse_client.js';

/**
 * @param {HTMLElement} host  独占的 banner 宿主 (通常是 .chat-main 顶部的一个 div)
 * @param {object} deps       { stream } — mountMessageStream 的 handle
 */
export function mountAutoBanner(host, { stream }) {
  host.innerHTML = '';
  host.classList.add('auto-banner-host');

  const bar = el('div', { className: 'auto-banner', style: { display: 'none' } });
  const leftGroup = el('div', { className: 'auto-banner-left' });
  const labelEl = el('span', { className: 'auto-banner-label' },
    i18n('chat.auto_banner.label_running'));
  const progressEl = el('span', { className: 'auto-banner-progress' }, '0/0');
  const stepEl = el('span', { className: 'auto-banner-step muted' }, '');
  leftGroup.append(labelEl, progressEl, stepEl);

  const rightGroup = el('div', { className: 'auto-banner-right' });
  const pauseBtn = el('button', {
    type: 'button',
    className: 'small',
    onClick: () => requestPause(),
    title: i18n('chat.auto_banner.pause_title'),
  }, i18n('chat.auto_banner.pause'));
  const resumeBtn = el('button', {
    type: 'button',
    className: 'small',
    onClick: () => requestResume(),
    title: i18n('chat.auto_banner.resume_title'),
    style: { display: 'none' },
  }, i18n('chat.auto_banner.resume'));
  const stopBtn = el('button', {
    type: 'button',
    className: 'small danger',
    onClick: () => requestStop(),
    title: i18n('chat.auto_banner.stop_title'),
  }, i18n('chat.auto_banner.stop'));
  rightGroup.append(pauseBtn, resumeBtn, stopBtn);

  bar.append(leftGroup, rightGroup);
  host.append(bar);

  // ── 状态 ───────────────────────────────────────────────────────
  // 本地观测态; 权威源是后端 SSE 事件. 这些只是给 UI 快速显示用.
  let running = false;      // SSE 连接是否活跃 (或从 /state 探测到 is_running)
  let paused = false;
  let totalTurns = 0;
  let completedTurns = 0;
  let currentStream = null;
  let currentAssistantHandle = null;
  let pendingChangeEmitted = false;
  let hasPersistedMsgInRun = false;

  // 观察到的外部控制变更 — 仅用于 disable 按钮避免"双击发两个 pause 请求"
  let controlInFlight = false;

  function show() { bar.style.display = ''; }
  function hide() { bar.style.display = 'none'; }

  function renderState() {
    labelEl.textContent = paused
      ? i18n('chat.auto_banner.label_paused')
      : i18n('chat.auto_banner.label_running');
    bar.classList.toggle('paused', paused);
    progressEl.textContent = i18n('chat.auto_banner.progress',
      completedTurns, totalTurns);
    pauseBtn.style.display = paused ? 'none' : '';
    resumeBtn.style.display = paused ? '' : 'none';
    pauseBtn.disabled = controlInFlight;
    resumeBtn.disabled = controlInFlight;
    stopBtn.disabled = controlInFlight;
  }

  function setStepLabel(stepSeconds) {
    if (stepSeconds && stepSeconds > 0) {
      stepEl.textContent = i18n('chat.auto_banner.step_fixed', stepSeconds);
    } else {
      stepEl.textContent = i18n('chat.auto_banner.step_off');
    }
  }

  // ── 启动 ───────────────────────────────────────────────────────

  /**
   * 由 composer 的 [启动 Auto] 按钮调用. config shape 见
   * ``routers.chat_router._AutoDialogStartRequest`` / 后端
   * ``AutoDialogConfig.from_request``.
   *
   * @returns {{ abort: () => void } | null}  fail-start 时返回 null (已 toast).
   */
  function startAutoDialog(config) {
    if (running) {
      toast.err('Auto-Dialog 已在运行中');
      return null;
    }
    running = true;
    paused = false;
    totalTurns = config.total_turns || 0;
    completedTurns = 0;
    pendingChangeEmitted = false;
    hasPersistedMsgInRun = false;
    currentAssistantHandle = null;
    setStepLabel(config.step_mode === 'fixed' ? config.step_seconds : null);
    renderState();
    show();

    const emitPersistedChange = () => {
      if (pendingChangeEmitted || !hasPersistedMsgInRun) return;
      pendingChangeEmitted = true;
      emit('chat:messages_changed', { reason: 'auto_dialog' });
    };

    currentStream = streamPostSse('/api/chat/auto_dialog/start', config, {
      onEvent(ev) {
        switch (ev.event) {
          case 'start':
            // 后端 echo 回来, 修正本地展示 (e.g. first_kind 决定首轮步骤).
            if (ev.total_turns) totalTurns = ev.total_turns;
            if (ev.auto_state) {
              completedTurns = ev.auto_state.completed_turns || 0;
              paused = !!ev.auto_state.paused;
            }
            renderState();
            break;
          case 'turn_begin':
            if (ev.kind === 'target' && ev.step_seconds != null) {
              setStepLabel(ev.step_seconds);
            }
            break;
          case 'simuser_done':
            if (ev.message) {
              stream.appendIncomingMessage(ev.message);
              hasPersistedMsgInRun = true;
              pendingChangeEmitted = false;  // 本轮 auto 刷新会在 turn_done
            }
            if (ev.warnings && ev.warnings.length) {
              toast.ok('SimUser 生成有提示',
                { message: ev.warnings.join(' · ') });
            }
            break;
          case 'user':
            // 通常 auto-dialog 不 yield user (sim 模式用 simuser_done, target
            // 走 user_content=None 不 yield). 兜底: 若后端以后改了也不会炸.
            if (ev.message) {
              stream.appendIncomingMessage(ev.message);
              hasPersistedMsgInRun = true;
            }
            break;
          case 'wire_built':
          case 'usage':
            // 本期 banner 不显示这俩; 透传给 stream 也无意义.
            break;
          case 'assistant_start':
            currentAssistantHandle = stream.beginAssistantStream({
              id: ev.message_id,
              role: 'assistant',
              content: '',
              timestamp: ev.timestamp,
              source: 'llm',
            });
            break;
          case 'delta':
            currentAssistantHandle?.appendDelta(ev.content || '');
            break;
          case 'assistant':
            currentAssistantHandle?.commit(ev.message);
            currentAssistantHandle = null;
            break;
          case 'turn_done':
            completedTurns = ev.completed_turns || completedTurns;
            if (ev.total_turns) totalTurns = ev.total_turns;
            renderState();
            emitPersistedChange();
            pendingChangeEmitted = false;  // 下一轮再 emit 一次
            break;
          case 'paused':
            paused = true;
            renderState();
            break;
          case 'resumed':
            paused = false;
            renderState();
            break;
          case 'stopped': {
            // 本地 completedTurns 是每次 turn_done 递增的权威累计, stopped
            // 帧的 ev.completed_turns 应当 ≥ 本地; 但历史上曾经因为后端
            // finally 清 auto_state 顺序 bug 把 ev.completed_turns 发成 0,
            // 所以这里**取较大值兜底** (本地也有 = 从 /state 恢复上来的场景).
            // 额外: 不能用 `??` — 0 不触发 nullish fallback 会吞掉本地值.
            const evDone = Number.isFinite(ev.completed_turns)
              ? ev.completed_turns : 0;
            const done = Math.max(evDone, completedTurns);
            const total = ev.total_turns || totalTurns || 0;
            // 同步本地状态, 这样 banner 在 hide 前的最后一瞬也显示正确数字.
            completedTurns = done;
            if (total) totalTurns = total;
            renderState();
            toast.ok(i18n('chat.auto_banner.stopped_toast',
              ev.reason || 'completed', done, total));
            // 置状态 + hide 留到 onDone; 但有时 SSE 在 stopped 之后仍会补 error
            // (非典型, 正常情况 reason=error 时 error 已经在前面了), 这里不
            // hide 以防漏掉 trailing events.
            break;
          }
          case 'error': {
            // toast.err 签名是 (message, opts). opts 里的 `message` 会
            // 覆盖首参. 之前错写成 toast.err(err.message, {message: err.type})
            // 导致正文只显示 err.type (如 "LlmFailed"), 详细的 err.message
            // (如 "调用假想用户 LLM 失败: RateLimitError: Error code: 429...")
            // 完全被吞. 2026-04-22 Day 8 验收反馈发现此 bug.
            // 正确用法: 用 opts.title 放 err.type (粗体大字), 首参放
            // err.message (小字正文), 两行都要给用户看见.
            const err = ev.error || {};
            toast.err(err.message || i18n('chat.auto_banner.error_title'), {
              title: err.type || i18n('chat.auto_banner.error_title'),
            });
            currentAssistantHandle?.abort();
            currentAssistantHandle = null;
            break;
          }
          case 'done':
            // auto_dialog 不用这个事件名, 但 stream_send 会透传过来 (发生在
            // 每条 target 完成的尾部). 无需特殊处理.
            break;
          default:
            console.debug('[auto-banner] unknown SSE event:', ev);
        }
      },
      onError(err) {
        // P24 Day 7 (§12.3.F): 启动期配置校验失败 (400 InvalidConfig)
        // 会带 `err.detail = {error_type, message, errors: [...]}`,
        // ``errors`` 是逐条校验信息. 用专门的 banner 错误态显示列表,
        // 避免 toast 被 280 字符截断 + 避免用户需要手动解析
        // "errA; errB; errC" 合成字符串.
        const detail = err?.detail;
        if (Array.isArray(detail?.errors) && detail.errors.length > 1) {
          showErrorPanel(detail.errors, detail.message);
        } else {
          toast.err(i18n('chat.auto_banner.error_title'),
            { message: err.message });
          finish();
        }
        currentAssistantHandle?.abort();
        currentAssistantHandle = null;
        emitPersistedChange();
      },
      onDone() {
        emitPersistedChange();
        finish();
      },
    });

    return currentStream;
  }

  function finish() {
    running = false;
    paused = false;
    currentStream = null;
    controlInFlight = false;
    hide();
    // 通知 composer 解锁 Start 按钮 (通过事件; 更简单的耦合方式).
    emit('auto_dialog:finished', {});
  }

  /**
   * 启动期配置校验多条失败 — 把 banner 替换成错误详情面板 (P24 §12.3.F).
   *
   * 设计:
   *   - 覆盖 banner 原有内容 (不保留 leftGroup/rightGroup, 因为 Start
   *     根本没成功, 进度/控制按钮都无意义).
   *   - 顶部 h4 "启动失败 · N 条错误", 居中 [关闭] 按钮.
   *   - 每条 error 一行, li 列表样式, 首字符加 `• ` 视觉 bullet.
   *   - 关闭后 banner 整体 hide, 用户可重新调整配置再点 [启动 Auto].
   *
   * 没加"单独的 error-modal" 是为了视觉连续性 — 启动动作刚从 Chat
   * workspace 的 composer 触发, 错误也应该显示在 Chat workspace 里,
   * 而不是弹一个覆盖全屏的 modal.
   */
  function showErrorPanel(errors, headerMessage) {
    // 拆掉原 banner 内容 (leftGroup + rightGroup), 插错误面板 DOM.
    bar.innerHTML = '';
    bar.classList.add('auto-banner--error');
    bar.style.display = 'flex';
    bar.style.flexDirection = 'column';
    bar.style.alignItems = 'stretch';

    const header = el('div', { className: 'auto-banner-error-header' });
    const title = el('strong', {},
      i18n('chat.auto_banner.error_panel_title_fmt', errors.length));
    const closeBtn = el('button', {
      type: 'button',
      className: 'small ghost',
      title: i18n('common.close'),
      onClick: () => dismissErrorPanel(),
    }, '×');
    header.append(title, closeBtn);

    const summary = el('div', { className: 'auto-banner-error-summary' },
      headerMessage || i18n('chat.auto_banner.error_title'));

    const list = el('ul', { className: 'auto-banner-error-list' });
    for (const e of errors) {
      list.append(el('li', {}, String(e)));
    }

    bar.append(header, summary, list);
  }

  function dismissErrorPanel() {
    bar.classList.remove('auto-banner--error');
    bar.style.flexDirection = '';
    bar.style.alignItems = '';
    // 恢复 banner 的原结构 (下次 start 前 render*() 会重新填充), 这里
    // 只需要把错误 DOM 清掉 + hide 让横幅塌成 0 高度.
    bar.innerHTML = '';
    bar.append(leftGroup, rightGroup);
    finish();
  }

  // ── 控制按钮 ───────────────────────────────────────────────────

  async function requestPause() {
    if (!running || paused || controlInFlight) return;
    controlInFlight = true;
    renderState();
    const res = await api.post('/api/chat/auto_dialog/pause', {},
      { expectedStatuses: [404, 409] });
    controlInFlight = false;
    if (!res.ok) {
      toast.err(i18n('chat.auto_banner.pause_failed'),
        { message: res.error?.message || '' });
    }
    renderState();
  }

  async function requestResume() {
    if (!running || !paused || controlInFlight) return;
    controlInFlight = true;
    renderState();
    const res = await api.post('/api/chat/auto_dialog/resume', {},
      { expectedStatuses: [404, 409] });
    controlInFlight = false;
    if (!res.ok) {
      toast.err(i18n('chat.auto_banner.resume_failed'),
        { message: res.error?.message || '' });
    }
    renderState();
  }

  async function requestStop() {
    if (!running || controlInFlight) return;
    controlInFlight = true;
    labelEl.textContent = i18n('chat.auto_banner.label_stopping');
    renderState();
    const res = await api.post('/api/chat/auto_dialog/stop', {},
      { expectedStatuses: [404, 409] });
    // controlInFlight 保持 true 直到真 stopped SSE 帧; 防止连点.
    if (!res.ok) {
      controlInFlight = false;
      toast.err(i18n('chat.auto_banner.stop_failed'),
        { message: res.error?.message || '' });
      renderState();
    }
  }

  // ── 刷新态探测 (mount / session 切换) ─────────────────────────
  //
  // 用于"刷新页面期间 auto 还在跑"场景. 但因 POST SSE 无法续接, 我们只
  // 把按钮露出来供用户 Stop/Pause 观察; 进度数字靠轮询 /state (低频).

  let probeTimer = null;

  async function probeRunningState() {
    if (running) return;  // 已经在本标签页内跑着, /state 不是权威源
    const res = await api.get('/api/chat/auto_dialog/state',
      { expectedStatuses: [404] });
    if (!res.ok || !res.data?.is_running) {
      hide();
      return;
    }
    // 外部 SSE 未连上, 本标签仅做"观察者"模式: 显示 banner + 按钮可 stop,
    // 但不消费 SSE (物理上接不了). 进度靠每 3 秒轮询.
    const state = res.data.auto_state || {};
    running = true;
    paused = !!state.paused;
    totalTurns = state.total_turns || 0;
    completedTurns = state.completed_turns || 0;
    setStepLabel(state.config?.step_mode === 'fixed'
      ? state.config?.step_seconds : null);
    renderState();
    show();
    if (probeTimer) clearInterval(probeTimer);
    probeTimer = setInterval(async () => {
      const r = await api.get('/api/chat/auto_dialog/state',
        { expectedStatuses: [404] });
      if (!r.ok || !r.data?.is_running) {
        clearInterval(probeTimer);
        probeTimer = null;
        finish();
        return;
      }
      const s = r.data.auto_state || {};
      paused = !!s.paused;
      completedTurns = s.completed_turns || 0;
      renderState();
    }, 3000);
  }

  // 初次挂载后异步探测 (不阻塞 mount).
  probeRunningState();

  return {
    startAutoDialog,
    isRunning: () => running,
    destroy() {
      try { currentStream?.abort?.(); } catch (_) { /* ignore */ }
      if (probeTimer) clearInterval(probeTimer);
      host.innerHTML = '';
    },
  };
}
