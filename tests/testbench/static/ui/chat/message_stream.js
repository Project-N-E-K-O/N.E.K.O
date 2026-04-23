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
import { emit, on, set, store } from '../../core/state.js';
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
  // 2026-04-23 P25 Day 2 polish r5: tester 反馈 [刷新] 按钮没实际意义
  // — 所有写入 session.messages 的路径 (send / inject_system / external
  // event / script turn / auto_dialog / judge) 都通过 `chat:messages_
  // changed` 事件总线驱动 refresh(), 不需要用户手动触发. 直接从 toolbar
  // 里摘掉, 保留内部 refresh() 函数让订阅者继续用.
  //
  // 2026-04-23 P25 Day 2 polish r6: 新增 [保存到最近对话] 快捷钮, 放在
  // [清空] 左侧. 一键把当前 session.messages 追加落盘到 memory/<char>/
  // recent.json (走 POST /api/memory/recent/import_from_session, 后端
  // 过滤 banner / 空消息 / 非对话 role). 原本要这么做必须手动开 Setup
  // → Memory → Recent 然后自己拼 LangChain canonical JSON, 太硬核.
  const saveToRecentBtn = el('button', {
    type: 'button',
    className: 'small',
    title: i18n('chat.stream.save_to_recent_title'),
    onClick: () => { saveToRecent(); },
  }, i18n('chat.stream.save_to_recent_btn'));
  const clearBtn = el('button', {
    type: 'button',
    className: 'small danger',
    onClick: () => { confirmClearAll(); },
  }, i18n('chat.stream.clear_btn'));
  toolbar.append(
    countBadge,
    el('span', { className: 'spacer' }),
    saveToRecentBtn,
    clearBtn,
  );
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

  // Map `assistant_msg_id → latest EvalResult dict` used to render the
  // inline evaluation badge on each assistant bubble (P17). Populated
  // by `refreshEvalMap()`, which walks `GET /api/judge/results`; the
  // latest result wins when a message has been evaluated more than
  // once. We keep this in the closure (not in module scope) so
  // hot-reloading a new workspace doesn't leak cached state across
  // sessions.
  let latestEvalByMsg = new Map();

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
    // 2026-04-23 P25 Day 2 polish r5 T7: external-event "banner" pseudo-
    // messages are visual timeline markers ("测试用户触发了一次 Agent 回
    // 调事件"), not real dialogue — they MUST NOT expose the edit / rerun
    // / delete menu (tester rewriting a banner would cause confusion), and
    // MUST NOT show an eval badge (not an assistant reply), and MUST NOT
    // show the role pill ("系统" - banner isn't a real system prompt, it's
    // a UI marker). Instead we render a compact centered one-liner with
    // just timestamp + content. Same structural shape as a .chat-message
    // (so renderAll's time-sep insertion logic still works) but a stripped-
    // down header and a dedicated CSS class for styling.
    const isBanner = (msg.source || '') === 'external_event_banner';

    const node = el('div', {
      className: 'chat-message' + (isBanner ? ' is-event-banner' : ''),
      'data-role': msg.role,
      'data-source': msg.source || 'manual',
      'data-msg-id': msg.id,
    });

    // header: role + source + timestamp + eval badge + menu
    //
    // P17 hotfix: `buildEvalBadge(msg)` 返回 null 的情况下 (非 assistant /
    // 还没跑过评分), 不能再直接传给 `Node.prototype.append` — per §3A C3
    // / #42 native append 会把 `null` 字符串化成文本节点 "null" 塞进 DOM.
    // P17 首版徽章代码正好踩了这个 gotcha, 导致每条没评分的消息都拖着
    // 一串 "null" 字样, 容易被误判成"徽章逻辑坏了". 改成先算后过滤, 保
    // 持 `el()` helper 里 "null/undefined/false 自动跳过" 同样的语义.
    const header = el('div', { className: 'msg-header' });
    const evalBadge = isBanner ? null : buildEvalBadge(msg);
    const menuButton = isBanner ? null : buildMenuButton(msg);
    const roleSpan = isBanner
      ? null
      : el('span', { className: `msg-role role-${msg.role}` }, roleLabel(msg.role));
    const sourceSpan = isBanner
      // Banner 的"身份"是 source=external_event_banner, 已经通过 i18n
      // 给出明确文案 ("测试事件"), 但在 header 里放一个 pill 会让 banner
      // 显得像普通消息; 把 source 文案直接并进 timestamp 行即可.
      ? el('span', { className: 'msg-source muted' }, sourceLabel(msg.source))
      : el('span', { className: 'msg-source' }, sourceLabel(msg.source || 'manual'));
    header.append(
      ...[
        roleSpan,
        sourceSpan,
        el('span', { className: 'msg-timestamp muted', title: msg.timestamp },
          formatTimestamp(msg.timestamp)),
        el('span', { className: 'spacer' }),
        evalBadge,
        menuButton,
      ].filter(Boolean),
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

  // ── P17 evaluation badge ───────────────────────────────────────

  /** Return the inline eval badge node (or `null` for non-assistant /
   * un-evaluated messages). Clicking the badge jumps to
   * Evaluation → Results with ``message_id`` pre-filtered so the tester
   * can see every pass this message has under. We don't expand the
   * badge into a tooltip with the full result — the click-through to
   * the drawer does that job better, and a tooltip with e.g. 5 dims of
   * data would be unreadable in a header row.
   */
  function buildEvalBadge(msg) {
    if (msg.role !== 'assistant') return null;
    const latest = latestEvalByMsg.get(msg.id);
    if (!latest) return null;

    const isError = Boolean(latest.error);
    let text;
    let className = 'msg-eval-badge badge';
    if (isError) {
      text = i18n('chat.stream.eval_badge.errored');
      className += ' danger';
    } else if (latest.mode === 'comparative') {
      const g = Number(latest.gap ?? 0);
      const sign = g >= 0 ? '+' : '';
      text = i18n('chat.stream.eval_badge.gap_fmt', `${sign}${g.toFixed(1)}`);
      className += g > 0 ? ' success' : g < 0 ? ' danger' : ' subtle';
    } else {
      const ov = Number(latest.scores?.overall_score ?? 0);
      text = i18n('chat.stream.eval_badge.overall_fmt', ov.toFixed(0));
      className += latest.passed ? ' success' : ' subtle';
    }

    const tooltipParts = [
      i18n('chat.stream.eval_badge.tooltip_fmt',
        latest.schema_id || '-', latest.verdict || '-'),
      (latest.created_at || '').replace('T', ' ').slice(0, 19),
      i18n('chat.stream.eval_badge.click_hint'),
    ].filter(Boolean);

    return el('button', {
      type: 'button',
      className,
      title: tooltipParts.join(' · '),
      onClick: (ev) => {
        ev.stopPropagation();
        jumpToResultsFilter(msg.id);
      },
    }, text);
  }

  /** Stash a filter override on `store.ui_prefs` and switch to the
   * Evaluation workspace on the Results subpage.
   * `workspace_evaluation` reads `active_subpage` from localStorage on
   * mount; `page_results` consumes `ui_prefs.evaluation_results_filter`
   * on first render and clears it. Doing both means a cold jump
   * (workspace just mounted) and a warm jump (already on Evaluation)
   * both land the user on the filtered Results table.
   */
  function jumpToResultsFilter(messageId) {
    try {
      localStorage.setItem('testbench:evaluation:active_subpage', 'results');
    } catch { /* ignore */ }
    set('ui_prefs', {
      ...(store.ui_prefs || {}),
      evaluation_results_filter: { message_id: messageId },
    });
    // 先切 workspace (若未在 Evaluation) 再发 navigate. 已经在
    // Evaluation 时 set 是 no-op, 由 navigate 负责跳子页. 这两步顺序
    // 对首次冷挂载也安全: mountEvaluationWorkspace 里会用 localStorage
    // 读出初始子页.
    set('active_workspace', 'evaluation');
    emit('evaluation:navigate', { subpage: 'results' });
  }

  async function refreshEvalMap() {
    latestEvalByMsg = new Map();
    if (!store.session?.id) return;
    // Pull all results in one request. The backend caps at 200 per
    // request; we ask for 200 and rely on the fact that a single
    // session rarely accumulates more. If future work raises that cap
    // we can switch to paginated walking here.
    const res = await api.get('/api/judge/results?limit=200', {
      expectedStatuses: [404],
    });
    if (!res.ok) return;
    const results = Array.isArray(res.data?.results) ? res.data.results : [];
    // The backend already sorts newest-first; walk once and keep the
    // first-seen entry for each message_id (== latest). A result can
    // target several messages (comparative batch) — we register the
    // same result under every targeted id so every badge points at a
    // meaningful latest.
    for (const r of results) {
      for (const mid of (r.target_message_ids || [])) {
        if (!mid) continue;
        if (!latestEvalByMsg.has(mid)) latestEvalByMsg.set(mid, r);
      }
    }
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
    // 2026-04-22 Day 8 #3: 根据末尾 role 区分 toast 文案.
    // - 末尾是 user → 提示用户可直接按 [Send] 让 AI 对这条回复 (空 textarea
    //   也能 send, 后端走 "只跑 LLM 回复末尾 user" 路径; 避免产生连续两条 user);
    // - 末尾是 assistant / system → 常规提示.
    const tailRole = msg.role;
    const key = tailRole === 'user'
      ? 'chat.stream.toast.rerun_done_user_tail'
      : 'chat.stream.toast.rerun_done';
    toast.ok(i18n(key, res.data.removed_count));
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

  // 2026-04-23 P25 Day 2 polish r6: [保存到最近对话] 处理. 把当前
  // session.messages 追加写入 memory/<character>/recent.json. 后端
  // (memory_router.import_recent_from_session) 会:
  //   - 过滤 source=external_event_banner (banner 是 UI-only 标记,
  //     不进 LLM wire, 也不该进 recent)
  //   - 过滤 空 content / 非 user|assistant|system role
  //   - 转成 LangChain canonical ({type, data:{content}}) 形状
  //   - 原子读 existing → append → 原子写 (默认 append 模式)
  // 409 NoCharacterSelected / NoMessagesToImport 在 confirm 之前挡不住,
  // 就走 toast.warn 告诉 tester 怎么修 (expectedStatuses 声明为已知业务码,
  // 避免 api.js 的 http:error 广播弹成红色系统错误).
  async function saveToRecent() {
    if (!messages.length) {
      toast.warn(i18n('chat.stream.toast.save_to_recent_empty'));
      return;
    }
    if (!confirm(i18n('chat.stream.prompt.save_to_recent', messages.length))) return;
    const res = await api.post(
      '/api/memory/recent/import_from_session',
      { mode: 'append' },
      { expectedStatuses: [404, 409] },
    );
    if (!res.ok) {
      if (res.error?.type === 'NoMessagesToImport') {
        toast.warn(i18n('chat.stream.toast.save_to_recent_empty'));
        return;
      }
      toast.err(
        i18n('chat.stream.toast.save_to_recent_error', res.error?.message || ''),
      );
      return;
    }
    const { added, total, skipped } = res.data || {};
    toast.ok(
      i18n('chat.stream.toast.save_to_recent_ok', added, total, skipped || {}),
    );
  }

  function afterMutation(reason) {
    emit('chat:messages_changed', { reason });
  }

  // ── public refresh ─────────────────────────────────────────────

  async function refresh() {
    if (!store.session?.id) {
      messages = [];
      latestEvalByMsg = new Map();
      renderAll();
      return;
    }
    const res = await api.get('/api/chat/messages', {
      expectedStatuses: [404],
    });
    if (!res.ok) {
      messages = [];
      latestEvalByMsg = new Map();
      renderAll();
      return;
    }
    messages = Array.isArray(res.data?.messages) ? res.data.messages : [];
    // Pull eval results in parallel with render — badges appear the
    // moment the eval map resolves, and if it fails we just render
    // without badges (which is the correct "no evaluations yet" look).
    await refreshEvalMap();
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
      latestEvalByMsg = new Map();
      renderAll();
    }
  });

  // Re-pull the eval map whenever Run 子页 completes a batch or
  // Results 子页 deletes rows. We don't re-pull the messages because
  // evaluations never mutate the chat log — only the badges. Worth
  // the extra request: the alternative is stale green badges pointing
  // at results the user just deleted.
  const offResults = on('judge:results_changed', async () => {
    await refreshEvalMap();
    renderAll();
  });

  // External (out-of-band) session.messages writes → pull-refresh.
  // Subscribe to `chat:messages_changed` **但只处理带外写入的 reason**:
  // 主 /chat/send 流 / inject / script / auto_dialog / 本地 edit / delete /
  // truncate / patch_timestamp 都已经通过 stream handle (beginAssistantStream
  // / appendIncomingMessage / replaceTailWith) 或本地状态数组直接维护
  // DOM, 再对自己 refresh() 会:
  //   (a) 把正在流的 msg-streaming 节点抹掉 (renderAll → innerHTML 重建).
  //   (b) 和 SSE 回调产生竞态 (refresh 的 GET 覆盖 append 的局部变更).
  // 因此只有 "后端路径直接 append_message 写 session.messages 但没有
  // stream handle 对应"的 reason 才需要本订阅 refresh. 当前只有
  // `external_event` 属于这类 (POST /api/session/external-event 同步返回
  // SimulationResult, 不经 SSE). 未来任何新增的同族路径 (比如导入消息
  // / 补录消息) 只要 emit reason='external_event' 或新 reason 并在下面
  // 白名单中加一个 case 就能免费获得 UI 刷新.
  const offMessagesChanged = on('chat:messages_changed', (payload) => {
    const reason = payload?.reason;
    if (reason !== 'external_event') return;
    if (!store.session?.id) return;
    refresh();
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
    destroy() { offSession(); offResults(); offMessagesChanged(); },
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
