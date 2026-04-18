/**
 * chat/composer.js — Chat workspace 底部输入栏 (P09 Manual 模式).
 *
 * PLAN §Chat workspace 约定两行扁平布局:
 *   Row 1: Clock + Next turn +Δt | Role (User/System) | Mode (Manual/SimUser/Script/Auto)
 *   Row 2: textarea | [Send] [Inject sys] [⋯ more]
 *
 * 本期只实现 Manual; 其它模式占位按钮 disabled + title 提示"P11+ 接入".
 *
 * 发送流程:
 *   1. 读 textarea + role; 关 send 按钮进入 pending.
 *   2. `streamPostSse('/api/chat/send', {...})`.
 *   3. 收到 `{event:'user'}` → composer 清空 textarea; stream.appendIncomingMessage
 *   4. `{event:'assistant_start'}` → stream.beginAssistantStream(stub)
 *   5. `{event:'delta'}` → handle.appendDelta(content)
 *   6. `{event:'assistant'}` → handle.commit(finalMsg)
 *   7. `{event:'done'}` → (happy-path 收尾; 真正的 emit 在 onDone 里做)
 *   8. `{event:'error'}` → toast.err + handle.abort()
 *   9. onDone (不论以 done 还是 error 收尾都会触发) → emit
 *      `chat:messages_changed` + refreshClock, 前提是已经收到过 `user` 事
 *      件 (user_msg 真的入库了). 这确保"发送配置错误/LLM 异常"分支里预览
 *      也会自动刷新, 不会留在旧状态. onError (传输层失败) 同理兜底 emit.
 *
 * Next turn:
 *   - 按钮 +5m / +1h / +1d 直接调 `/api/time/stage_next_turn`, 无本地
 *     隐藏状态 — 后端的 pending 本身是唯一真相源. Custom 走 prompt() 输入
 *     秒数 (或带 h/m/d 后缀, 例如 "1h30m").
 *   - 发送时, 后端的 OfflineChatBackend.stream_send 会 consume_pending,
 *     所以 composer 不需要主动"消费".
 *
 * Inject sys:
 *   - 独立按钮, 把 textarea 内容以 role=system 写入, 不走 LLM.
 */

import { api } from '../../core/api.js';
import { i18n } from '../../core/i18n.js';
import { toast } from '../../core/toast.js';
import { emit, on, store } from '../../core/state.js';
import { el } from '../_dom.js';
import { streamPostSse } from './sse_client.js';

/**
 * @param {HTMLElement} host
 * @param {object} deps  { stream } — 来自 mountMessageStream 的 handle
 */
export function mountComposer(host, { stream }) {
  host.innerHTML = '';
  host.classList.add('chat-composer');

  // ── Row 1 ──────────────────────────────────────────────────────
  const row1 = el('div', { className: 'composer-row row-meta' });

  const clockChip = el('span', { className: 'clock-chip muted' },
    i18n('chat.composer.clock_prefix'),
    el('span', { className: 'clock-now' }, '-'));
  row1.append(clockChip);

  const nextTurnGroup = el('span', { className: 'next-turn-group' });
  nextTurnGroup.append(
    el('span', { className: 'muted' }, i18n('chat.composer.next_turn_prefix')),
    nextTurnBtn('+5m',  () => stageDelta(5 * 60)),
    nextTurnBtn('+30m', () => stageDelta(30 * 60)),
    nextTurnBtn('+1h',  () => stageDelta(60 * 60)),
    nextTurnBtn('+1d',  () => stageDelta(24 * 60 * 60)),
    nextTurnBtn(i18n('chat.composer.next_turn_custom'), customStage),
    nextTurnBtn(i18n('chat.composer.next_turn_clear'), clearStage, { subtle: true }),
  );
  row1.append(nextTurnGroup);

  const roleGroup = el('span', { className: 'role-group' });
  roleGroup.append(
    el('span', { className: 'muted' }, i18n('chat.composer.role_prefix')),
  );
  const roleSelect = el('select', { className: 'role-select' });
  roleSelect.append(
    el('option', { value: 'user' }, i18n('chat.role.user')),
    el('option', { value: 'system' }, i18n('chat.role.system')),
  );
  roleGroup.append(roleSelect);
  row1.append(roleGroup);

  // role=system 下, [发送] 与 [注入 sys] 语义不同, 这行 hint 用来消歧.
  // role=user 时隐藏以减少视觉噪音.
  const systemHint = el('div', {
    className: 'composer-hint',
    style: { display: 'none' },
  }, i18n('chat.composer.system_mode_hint'));

  const modeGroup = el('span', { className: 'mode-group muted' },
    i18n('chat.composer.mode_prefix'),
    el('span', { className: 'mode-value' }, i18n('chat.composer.mode.manual')),
    el('span', { className: 'mode-deferred', title: i18n('chat.composer.mode_deferred_hint') },
      i18n('chat.composer.mode.deferred')),
  );
  row1.append(modeGroup);

  const pendingBadge = el('span', { className: 'pending-badge', style: { display: 'none' } });
  row1.append(pendingBadge);

  host.append(row1);
  host.append(systemHint);

  // ── Row 2 ──────────────────────────────────────────────────────
  const row2 = el('div', { className: 'composer-row row-input' });

  const textarea = el('textarea', {
    className: 'composer-textarea',
    placeholder: i18n('chat.composer.placeholder'),
    rows: 3,
  });
  // Ctrl+Enter / Cmd+Enter 发送.
  textarea.addEventListener('keydown', (ev) => {
    if ((ev.ctrlKey || ev.metaKey) && ev.key === 'Enter') {
      ev.preventDefault();
      send();
    }
  });

  const sendBtn = el('button', {
    type: 'button',
    className: 'primary',
    onClick: () => send(),
    title: i18n('chat.composer.send_title_user'),
  }, i18n('chat.composer.send'));
  const injectBtn = el('button', {
    type: 'button',
    className: 'small',
    onClick: () => injectSystem(),
    title: i18n('chat.composer.inject_title'),
  }, i18n('chat.composer.inject'));

  const btnGroup = el('div', { className: 'composer-buttons' });
  btnGroup.append(sendBtn, injectBtn);

  row2.append(textarea, btnGroup);
  host.append(row2);

  // ── role 切换 → 更新 hint + Send 按钮 tooltip ─────────────────
  function syncRoleUI() {
    const isSystem = roleSelect.value === 'system';
    systemHint.style.display = isSystem ? '' : 'none';
    sendBtn.title = isSystem
      ? i18n('chat.composer.send_title_system')
      : i18n('chat.composer.send_title_user');
  }
  roleSelect.addEventListener('change', syncRoleUI);
  syncRoleUI();

  // ── state ──────────────────────────────────────────────────────
  let pending = false;
  let currentStream = null;

  function setPending(value) {
    pending = value;
    sendBtn.disabled = value;
    injectBtn.disabled = value;
    sendBtn.textContent = value
      ? i18n('chat.composer.sending')
      : i18n('chat.composer.send');
  }

  // ── helpers ────────────────────────────────────────────────────

  function nextTurnBtn(label, onClick, { subtle = false } = {}) {
    return el('button', {
      type: 'button',
      className: 'small' + (subtle ? ' subtle' : ''),
      onClick,
    }, label);
  }

  function parseDuration(text) {
    // 支持 "5m", "1h30m", "2d", 纯数字按秒.
    const t = (text || '').trim();
    if (!t) return null;
    if (/^\d+$/.test(t)) return parseInt(t, 10);
    const re = /(\d+)\s*([dhms])/gi;
    let total = 0;
    let m;
    let any = false;
    while ((m = re.exec(t)) !== null) {
      any = true;
      const n = parseInt(m[1], 10);
      const unit = m[2].toLowerCase();
      if (unit === 'd') total += n * 86400;
      else if (unit === 'h') total += n * 3600;
      else if (unit === 'm') total += n * 60;
      else total += n;
    }
    return any ? total : null;
  }

  async function stageDelta(seconds) {
    if (!seconds || seconds <= 0) return;
    const res = await api.post('/api/time/stage_next_turn', {
      delta_seconds: seconds,
    }, { expectedStatuses: [404, 409] });
    if (res.ok) { reflectPending(res.data?.clock); }
  }

  async function customStage() {
    const input = prompt(i18n('chat.composer.custom_prompt'));
    if (!input) return;
    const secs = parseDuration(input);
    if (secs == null) {
      toast.err(i18n('chat.composer.bad_duration'), { message: input });
      return;
    }
    await stageDelta(secs);
  }

  async function clearStage() {
    const res = await api.delete('/api/time/stage_next_turn',
      { expectedStatuses: [404, 409] });
    if (res.ok) { reflectPending(res.data?.clock); }
  }

  function reflectPending(clock) {
    if (clock) updateClockDisplay(clock);
    const pendingObj = clock?.pending || {};
    const hasPending = pendingObj.advance_seconds != null || pendingObj.absolute != null;
    if (hasPending) {
      const label = pendingObj.absolute
        ? i18n('chat.composer.pending_absolute',
          pendingObj.absolute.replace('T', ' '))
        : i18n('chat.composer.pending_delta',
          formatDurationShort(pendingObj.advance_seconds));
      pendingBadge.textContent = label;
      pendingBadge.style.display = '';
    } else {
      pendingBadge.style.display = 'none';
    }
  }

  function updateClockDisplay(clock) {
    const cursor = clock?.cursor;
    const nowEl = clockChip.querySelector('.clock-now');
    nowEl.textContent = cursor ? cursor.replace('T', ' ') : i18n('chat.composer.clock_unset');
  }

  async function refreshClock() {
    const res = await api.get('/api/time', { expectedStatuses: [404] });
    if (res.ok) reflectPending(res.data?.clock);
  }

  // ── send / inject ──────────────────────────────────────────────

  function send() {
    if (pending) return;
    if (!store.session?.id) {
      toast.err(i18n('chat.composer.no_session'));
      return;
    }
    const content = textarea.value.trim();
    if (!content) return;
    const role = roleSelect.value || 'user';

    setPending(true);
    let assistantHandle = null;
    // 后端 `stream_send` 在 append(user_msg) 之后立即 `yield {event:'user'}`,
    // 因此一旦前端收到 `case 'user'`, 就意味着 session.messages 已经落盘多了
    // 一条. 后续任何 error 分支 (ChatConfigError / PreviewNotReady / LLM 流
    // 异常 / 传输层断) 都不会把这条消息撤回 (详见 AGENT_NOTES.md #4.13.9).
    // 因此 "通知 preview 刷新" 这件事必须绑在 "user_msg 已落盘" 上, 不能只
    // 在 case 'done' 里 emit — 早期版本这么写导致未配置 chat 模型时发送后
    // preview 不自动刷新, 但消息明明已经入库.
    let userMsgPersisted = false;
    let persistedChangeEmitted = false;
    const emitPersistedChange = () => {
      if (persistedChangeEmitted || !userMsgPersisted) return;
      persistedChangeEmitted = true;
      refreshClock();
      emit('chat:messages_changed', { reason: 'send' });
    };

    const userContent = content;
    // 清 textarea 提前: 用户可继续写下一条草稿 (更符合即时通讯直觉).
    textarea.value = '';

    currentStream = streamPostSse('/api/chat/send', {
      content: userContent,
      role,
      source: 'manual',
    }, {
      onEvent(ev) {
        switch (ev.event) {
          case 'user':
            stream.appendIncomingMessage(ev.message);
            userMsgPersisted = true;
            break;
          case 'assistant_start':
            assistantHandle = stream.beginAssistantStream({
              id: ev.message_id,
              role: 'assistant',
              content: '',
              timestamp: ev.timestamp,
              source: 'llm',
            });
            break;
          case 'delta':
            assistantHandle?.appendDelta(ev.content || '');
            break;
          case 'assistant':
            assistantHandle?.commit(ev.message);
            break;
          case 'done':
            // happy-path 收尾; 真正的 emit 在 onDone 里统一做, 避免与
            // error 路径 (无 done) 的通知逻辑分家.
            break;
          case 'error': {
            const err = ev.error || {};
            toast.err(err.message || i18n('chat.composer.send_failed'),
              { message: err.type || '' });
            assistantHandle?.abort();
            break;
          }
          case 'wire_built':
          case 'usage':
            // 本期不展示; 后续 phase 会用.
            break;
          default:
            // 未知事件不 crash, 但留个 console 用于排障.
            console.debug('[composer] unknown SSE event:', ev);
        }
      },
      onError(err) {
        // 传输层错误 (fetch 抛 / HTTP 非 200 / read 失败). 后端生成器若在
        // yield 'user' 之后才断掉, user_msg 仍然在 session.messages 里 —
        // emit 让 preview 刷新到真实状态.
        toast.err(i18n('chat.composer.stream_error'), { message: err.message });
        assistantHandle?.abort();
        emitPersistedChange();
        setPending(false);
        currentStream = null;
      },
      onDone() {
        // 不论以 `event:'done'` 还是 `event:'error'` 收尾, 只要 stream 干净
        // 关闭就走这里. user_msg 已落盘 → 通知 preview 刷新; 同时 clock
        // 可能在后端被推进过 (consume_pending / per_turn_default 都发生在
        // yield 'user' 之前), 因此也要 refreshClock.
        emitPersistedChange();
        setPending(false);
        currentStream = null;
      },
    });
  }

  async function injectSystem() {
    if (pending) return;
    const content = textarea.value.trim();
    if (!content) {
      toast.err(i18n('chat.composer.inject_empty'));
      return;
    }
    const res = await api.post('/api/chat/inject_system', { content });
    if (!res.ok) return;
    stream.appendIncomingMessage(res.data.message);
    textarea.value = '';
    emit('chat:messages_changed', { reason: 'inject' });
  }

  // ── lifecycle ──────────────────────────────────────────────────

  refreshClock();

  const offSession = on('session:change', () => {
    pendingBadge.style.display = 'none';
    refreshClock();
  });

  // 外部改了时钟 (Setup → Virtual Clock 页) 也要同步显示.
  const offClock = on('clock:change', refreshClock);

  return {
    focus() { textarea.focus(); },
    destroy() {
      offSession();
      offClock();
      currentStream?.abort?.();
    },
  };
}

function formatDurationShort(seconds) {
  if (seconds == null) return '';
  const s = Math.round(seconds);
  if (s < 60) return `${s}s`;
  if (s < 3600) return `${Math.round(s / 60)}m`;
  if (s < 86400) {
    const h = Math.floor(s / 3600);
    const m = Math.round((s % 3600) / 60);
    return m ? `${h}h${m}m` : `${h}h`;
  }
  const d = Math.floor(s / 86400);
  const h = Math.round((s % 86400) / 3600);
  return h ? `${d}d${h}h` : `${d}d`;
}
