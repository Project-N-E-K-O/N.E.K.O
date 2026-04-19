/**
 * chat/message_stream.js — Chat workspace 左栏消息流 (P09).
 *
 * 负责渲染 `session.messages` 并提供 Edit/Delete/Re-run/Edit timestamp
 * 菜单. 发消息时, composer 通过 `beginAssistantStream()` 获取一个句柄,
 * 把 delta 直接喂进同一个 DOM 节点, 不经过"整列表重绘", 以保证丝滑.
 *
 * DOM 结构:
 *   <div.chat-stream>
 *     <div.chat-stream-toolbar> ... </div>
 *     <div.chat-stream-list>
 *       <div.time-sep> — 2h later — </div>
 *       <div.chat-message data-role=user> ... </div>
 *       <div.chat-message data-role=assistant streaming> ... </div>
 *     </div>
 *   </div>
 *
 * 外部 API (mountMessageStream 返回值):
 *   - refresh()                 GET /messages 重拉 + 重绘
 *   - beginAssistantStream(msg) 把 composer 的流接进新消息节点 →
 *                               返回 { appendDelta(text), commit(final), abort(err) }
 *   - appendIncomingMessage(m)  插入已落盘的 user / system 消息 (给 composer
 *                               的 {event:'user'} 事件用)
 *   - replaceTailWith(msg)      把最末一条替换为 msg (给 assistant 定稿用)
 *   - destroy()                 解绑订阅
 *
 * 事件:
 *   - 订阅 `session:change` → 换会话就整屏重拉.
 *   - 触发 `chat:messages_changed` (state bus) → preview_panel 监听后打 dirty,
 *     下次切回 Chat 自动刷新.
 */

import { api } from '../../core/api.js';
import { i18n } from '../../core/i18n.js';
import { toast } from '../../core/toast.js';
import { emit, on, store } from '../../core/state.js';
import { createCollapsible } from '../../core/collapsible.js';
import { el } from '../_dom.js';

// ── 常量 ────────────────────────────────────────────────────────────

/** 相邻消息 timestamp 差超过这个秒数时插入"X 分钟后"分隔条. */
const TIME_SEPARATOR_THRESHOLD_SEC = 30 * 60;

/** 长消息默认折叠的阈值 (PLAN: 消息 > 500 字符默认折叠). */
const MESSAGE_FOLD_THRESHOLD = 500;

// ── 入口 ────────────────────────────────────────────────────────────

/**
 * @param {HTMLElement} host
 * @returns {{
 *   refresh: () => Promise<void>,
 *   beginAssistantStream: (msg: object) => StreamingMessageHandle,
 *   appendIncomingMessage: (msg: object) => void,
 *   replaceTailWith: (msg: object) => void,
 *   destroy: () => void,
 * }}
 */
export function mountMessageStream(host) {
  host.innerHTML = '';
  host.classList.add('chat-stream');

  // ── toolbar ────────────────────────────────────────────────────
  const toolbar = el('div', { className: 'chat-stream-toolbar' });
  const countBadge = el('span', { className: 'chat-stream-count muted' },
    i18n('chat.stream.count', 0));
  const refreshBtn = el('button', {
    type: 'button',
    className: 'small',
    onClick: () => { refresh(); },
  }, i18n('chat.stream.refresh_btn'));
  const clearBtn = el('button', {
    type: 'button',
    className: 'small danger',
    onClick: () => { confirmClearAll(); },
  }, i18n('chat.stream.clear_btn'));
  toolbar.append(countBadge, el('span', { className: 'spacer' }), refreshBtn, clearBtn);
  host.append(toolbar);

  // ── list ───────────────────────────────────────────────────────
  const list = el('div', { className: 'chat-stream-list' });
  host.append(list);

  // ── empty placeholder ──────────────────────────────────────────
  const emptyBox = el('div', { className: 'chat-stream-empty muted' },
    el('p', {}, i18n('chat.stream.empty')),
    el('p', { className: 'hint' }, i18n('chat.stream.empty_hint')),
  );
  host.append(emptyBox);

  // ── state ──────────────────────────────────────────────────────
  let messages = [];

  function renderAll() {
    list.innerHTML = '';
    if (!messages.length) {
      emptyBox.style.display = '';
      countBadge.textContent = i18n('chat.stream.count', 0);
      return;
    }
    emptyBox.style.display = 'none';
    countBadge.textContent = i18n('chat.stream.count', messages.length);

    let prev = null;
    for (const msg of messages) {
      const sep = maybeBuildSeparator(prev, msg);
      if (sep) list.append(sep);
      list.append(buildMessageNode(msg));
      prev = msg;
    }
    scrollToBottom();
  }

  function scrollToBottom() {
    // 放在 rAF 里, 等浏览器完成 layout 再滚, 否则 scrollHeight 还是旧值.
    requestAnimationFrame(() => {
      list.scrollTop = list.scrollHeight;
    });
  }

  function maybeBuildSeparator(prev, curr) {
    if (!prev) return null;
    const gapSec = timestampGapSeconds(prev.timestamp, curr.timestamp);
    if (gapSec == null || gapSec < TIME_SEPARATOR_THRESHOLD_SEC) return null;
    return el('div', { className: 'time-sep' },
      el('span', {}, '— ' + formatElapsed(gapSec) + ' —'));
  }

  function buildMessageNode(msg) {
    const node = el('div', {
      className: 'chat-message',
      'data-role': msg.role,
      'data-source': msg.source || 'manual',
      'data-msg-id': msg.id,
    });

    // header: role + source + timestamp + menu
    const header = el('div', { className: 'msg-header' });
    header.append(
      el('span', { className: `msg-role role-${msg.role}` }, roleLabel(msg.role)),
      el('span', { className: 'msg-source' }, sourceLabel(msg.source || 'manual')),
      el('span', { className: 'msg-timestamp muted', title: msg.timestamp },
        formatTimestamp(msg.timestamp)),
      el('span', { className: 'spacer' }),
      buildMenuButton(msg),
    );
    node.append(header);

    // body: content + fold if long
    const body = el('div', { className: 'msg-body' });
    const text = (msg.content ?? '').toString();
    if (msg.role === 'assistant' && text === '') {
      // streaming placeholder will replace this.
      const streaming = el('div', { className: 'msg-content msg-streaming' });
      streaming.append(el('span', { className: 'dots' }, '⋯'));
      body.append(streaming);
    } else if (text.length > MESSAGE_FOLD_THRESHOLD) {
      const folded = createCollapsible({
        blockId: `msg-${msg.id}`,
        title: i18n('chat.stream.long_content_title', text.length),
        content: text,
        lengthBadge: i18n('chat.preview.length_badge', text.length),
        defaultCollapsed: true,
        copyable: true,
      });
      body.append(folded);
    } else {
      const pre = el('div', { className: 'msg-content' });
      pre.textContent = text;
      body.append(pre);
    }

    // P12: assistant 消息如果有 reference_content (脚本 expected 回填 / 测试人员
    // 手工写的"理想人类回复"), 在气泡下追加一个可折叠块. 收起 → 一个小徽章,
    // 展开 → 显示参考文本 + hint. 不做 diff 高亮 (留给 P15 ComparativeJudger
    // 的评分 UI).
    const ref = (msg.role === 'assistant' ? (msg.reference_content || '') : '').toString();
    if (ref.trim()) {
      const refWrap = el('div', { className: 'msg-reference-wrap' });
      const refBlock = createCollapsible({
        blockId: `ref-${msg.id}`,
        title: i18n('chat.stream.reference_title'),
        content: ref,
        lengthBadge: i18n('chat.preview.length_badge', ref.length),
        defaultCollapsed: true,
        copyable: true,
      });
      refBlock.classList.add('msg-reference-block');
      const hint = el('div', { className: 'muted msg-reference-hint' },
        i18n('chat.stream.reference_hint'));
      refWrap.append(refBlock, hint);
      body.append(refWrap);
    }

    node.append(body);
    return node;
  }

  function buildMenuButton(msg) {
    // 简单 click-to-open 下拉, 点击外部自动关闭.
    const wrap = el('div', { className: 'msg-menu-wrap' });
    const trigger = el('button', {
      type: 'button',
      className: 'msg-menu-trigger small',
      title: i18n('chat.stream.menu_title'),
    }, '⋯');
    const menu = el('div', { className: 'msg-menu' });
    menu.append(
      menuItem(i18n('chat.stream.menu.edit'),   () => editMessage(msg)),
      menuItem(i18n('chat.stream.menu.timestamp'), () => editTimestamp(msg)),
      menuItem(i18n('chat.stream.menu.rerun'),  () => rerunFromHere(msg)),
      menuItem(i18n('chat.stream.menu.delete'), () => deleteMessage(msg), { danger: true }),
    );
    wrap.append(trigger, menu);

    let open = false;
    const close = () => { open = false; menu.classList.remove('open'); };
    const onDocClick = (ev) => {
      if (!wrap.contains(ev.target)) close();
    };
    trigger.addEventListener('click', (ev) => {
      ev.stopPropagation();
      open = !open;
      menu.classList.toggle('open', open);
      if (open) {
        document.addEventListener('click', onDocClick, { once: true });
      }
    });
    return wrap;
  }

  function menuItem(text, onClick, { danger = false } = {}) {
    return el('button', {
      type: 'button',
      className: 'msg-menu-item' + (danger ? ' danger' : ''),
      onClick: (ev) => { ev.stopPropagation(); onClick(); },
    }, text);
  }

  // ── menu actions ───────────────────────────────────────────────

  async function editMessage(msg) {
    const initial = (msg.content ?? '').toString();
    const next = prompt(i18n('chat.stream.prompt.edit'), initial);
    if (next == null) return; // cancel
    if (next === initial) return;
    const res = await api.put(`/api/chat/messages/${msg.id}`, { content: next });
    if (!res.ok) return;
    // 原地更新
    const idx = messages.findIndex((m) => m.id === msg.id);
    if (idx >= 0) messages[idx] = res.data.message;
    renderAll();
    afterMutation('edit');
  }

  async function editTimestamp(msg) {
    const initial = msg.timestamp || '';
    const next = prompt(i18n('chat.stream.prompt.timestamp'), initial);
    if (next == null) return;
    const body = next.trim() ? { timestamp: next.trim() } : { timestamp: null };
    const res = await api.patch(`/api/chat/messages/${msg.id}/timestamp`, body, {
      expectedStatuses: [422],
    });
    if (!res.ok) {
      if (res.status === 422) {
        toast.err(i18n('chat.stream.toast.bad_timestamp'),
          { message: res.error?.message });
      }
      return;
    }
    const idx = messages.findIndex((m) => m.id === msg.id);
    if (idx >= 0) messages[idx] = res.data.message;
    renderAll();
    afterMutation('timestamp');
  }

  async function deleteMessage(msg) {
    if (!confirm(i18n('chat.stream.prompt.delete'))) return;
    const res = await api.delete(`/api/chat/messages/${msg.id}`);
    if (!res.ok) return;
    messages = messages.filter((m) => m.id !== msg.id);
    renderAll();
    afterMutation('delete');
  }

  async function rerunFromHere(msg) {
    // 语义: 保留到 msg (含), 截掉后面; 清时钟到 msg.timestamp.
    // 目标: 让 tester 立即从此刻手动编辑/重发, 但不再自动触发新 send — 新 send 由 composer 负责.
    if (!confirm(i18n('chat.stream.prompt.rerun'))) return;
    const res = await api.post('/api/chat/messages/truncate', {
      keep_id: msg.id, include: true,
    });
    if (!res.ok) return;
    // 用响应里的 count 代替重拉; 但我们需要完整列表, 重拉更稳.
    await refresh();
    toast.ok(i18n('chat.stream.toast.rerun_done', res.data.removed_count));
    afterMutation('truncate');
  }

  async function confirmClearAll() {
    if (!messages.length) return;
    if (!confirm(i18n('chat.stream.prompt.clear_all'))) return;
    const res = await api.post('/api/chat/messages/truncate', {
      keep_id: null, include: true,
    });
    if (!res.ok) return;
    messages = [];
    renderAll();
    afterMutation('clear');
  }

  function afterMutation(reason) {
    emit('chat:messages_changed', { reason });
  }

  // ── public refresh ─────────────────────────────────────────────

  async function refresh() {
    if (!store.session?.id) {
      messages = [];
      renderAll();
      return;
    }
    const res = await api.get('/api/chat/messages', {
      expectedStatuses: [404],
    });
    if (!res.ok) {
      messages = [];
      renderAll();
      return;
    }
    messages = Array.isArray(res.data?.messages) ? res.data.messages : [];
    renderAll();
  }

  // ── composer → stream hooks ────────────────────────────────────

  /** composer 收到 {event:'user'} 或 {event:'system'} 时塞进来的已落盘消息. */
  function appendIncomingMessage(msg) {
    messages.push(msg);
    renderAll();
  }

  /** 覆盖最末一条 (用于 assistant_start 占位被最终 assistant 覆盖). */
  function replaceTailWith(msg) {
    if (!messages.length) {
      messages.push(msg);
    } else if (messages[messages.length - 1].id === msg.id) {
      messages[messages.length - 1] = msg;
    } else {
      messages.push(msg);
    }
    renderAll();
  }

  /**
   * 开始一条流式 assistant 消息: 立即在 UI 上压一个空壳, 返回 handle 让
   * composer 逐 chunk 喂 delta; commit 用真实完整消息覆盖.
   * 之所以不在列表里 push 再 renderAll, 是为了不打断 delta 的 DOM 写入 (重绘
   * 会抹掉正在累积的 textContent).
   */
  function beginAssistantStream(initMsg) {
    messages.push(initMsg);
    renderAll();
    const node = list.querySelector(`.chat-message[data-msg-id="${initMsg.id}"]`);
    if (!node) {
      return { appendDelta() {}, commit() {}, abort() {} };
    }
    const body = node.querySelector('.msg-body');
    body.innerHTML = '';
    const stream = el('div', { className: 'msg-content msg-streaming' });
    body.append(stream);
    let acc = '';

    return {
      appendDelta(text) {
        if (!text) return;
        acc += text;
        stream.textContent = acc;
        scrollToBottom();
      },
      commit(finalMsg) {
        // 用正式节点重建, 以便 fold / source 徽章都按最终内容渲染.
        const idx = messages.findIndex((m) => m.id === initMsg.id);
        if (idx >= 0) messages[idx] = finalMsg;
        const fresh = buildMessageNode(finalMsg);
        node.replaceWith(fresh);
        scrollToBottom();
      },
      abort() {
        // 回滚 — 后端也会把 session.messages 最后一项 pop 掉.
        const idx = messages.findIndex((m) => m.id === initMsg.id);
        if (idx >= 0) messages.splice(idx, 1);
        node.remove();
      },
    };
  }

  // ── subscriptions ──────────────────────────────────────────────

  const offSession = on('session:change', (s) => {
    if (s?.id) refresh();
    else {
      messages = [];
      renderAll();
    }
  });

  // 初次挂载: 有会话就拉一次.
  if (store.session?.id) {
    refresh();
  } else {
    renderAll();
  }

  return {
    refresh,
    beginAssistantStream,
    appendIncomingMessage,
    replaceTailWith,
    destroy() { offSession(); },
  };
}

// ── helpers (pure) ───────────────────────────────────────────────────

function roleLabel(role) {
  return i18n(`chat.role.${role}`) || role;
}

function sourceLabel(source) {
  return i18n(`chat.source.${source}`) || source;
}

function timestampGapSeconds(a, b) {
  const ta = Date.parse(a);
  const tb = Date.parse(b);
  if (Number.isNaN(ta) || Number.isNaN(tb)) return null;
  return Math.abs(tb - ta) / 1000;
}

function formatElapsed(seconds) {
  const s = Math.round(seconds);
  if (s < 3600) return `${Math.round(s / 60)} min later`;
  if (s < 86400) {
    const h = Math.floor(s / 3600);
    const m = Math.round((s % 3600) / 60);
    return m ? `${h}h ${m}m later` : `${h}h later`;
  }
  const d = Math.floor(s / 86400);
  const h = Math.round((s % 86400) / 3600);
  return h ? `${d}d ${h}h later` : `${d}d later`;
}

function formatTimestamp(iso) {
  if (!iso) return '-';
  const t = Date.parse(iso);
  if (Number.isNaN(t)) return iso;
  const d = new Date(t);
  const pad = (n) => String(n).padStart(2, '0');
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`;
}
