/**
 * chat/preview_panel.js — Prompt Preview 右侧面板 (P08).
 *
 * 消费 `GET /api/chat/prompt_preview` 返回的 PromptBundle, 提供两种视图:
 *
 *   - **Structured**  — 每个 "逻辑分节" 独立 CollapsibleBlock. 给测试人员看
 *     "哪一段是从哪来的". 带来源 tag (session_init/character_prompt/memory/...)
 *     和字符数徽章. 其中 recent_history 是 list<{speaker,content}>, 会展开
 *     成一组小块 (不再嵌套折叠, 避免三层 caret).
 *   - **Raw wire**   — 真正送到 LLM 的 `messages: [{role, content}, ...]`
 *     数组. 首条 system 一定是"session_init + character_prompt +
 *     memory_flat + closing"连起来的大字符串 (PLAN §3). 顶部固定条提示
 *     "这是真正送到 AI 的内容, Structured 仅人类视图".
 *
 * 与会话状态的联动:
 *   - 第一次 `mount` 默认不触发请求 (尊重 Chat workspace 的 lazy-load 语义),
 *     用户点击 [刷新] 或切到 Chat workspace 后展开 panel 时再拉.
 *   - 订阅 `session:change` — 无论是新建还是销毁, 都把 "data" 区清空, 改贴
 *     空态提示 (避免旧会话的 preview 残留到新会话). 不自动预载新 preview,
 *     因为新会话大概率 persona_name 还没填, 主动请求只会换来一个 409.
 *   - P09 之后会加 "持久化消息列表 → 需要重新刷新" 的脏标; 这里先留 hook
 *     (`markDirty()` 暴露在返回对象上) 但不调用.
 *
 * 没有 401 / 403 走的必要: 端点自身除了 404 NoActiveSession / 409
 * PersonaNotReady 之外不会"业务预期性失败". 404/409 都走 expectedStatuses
 * 路径不吐 toast, 改成面板内空态提示.
 */

import { api } from '../../core/api.js';
import { i18n, i18nRaw } from '../../core/i18n.js';
import { toast } from '../../core/toast.js';
import { on, store } from '../../core/state.js';
import { createCollapsible, mountContainerToolbar } from '../../core/collapsible.js';
import { el } from '../_dom.js';

// P25 Day 2 polish r5: 外部事件注入的 instruction 是 "一次性结构, 不会进
// session.messages". 当真实 wire 来自这三类路径时, 面板顶部插一条突出
// 提示避免 tester 误以为超长指令会污染对话历史. 其它 source
// (chat.send / judge.llm / memory.llm / auto_dialog_target) 不显示提示 —
// 要么本来就进 session.messages (chat.send), 要么 tester 不会在意.
const EPHEMERAL_SOURCES = new Set([
  'avatar_event',
  'agent_callback',
  'proactive_chat',
]);

// P25 Day 2 polish r7 (2026-04-23): Chat 页 Preview Panel 只显示**对话 AI**
// 实际收到的 wire. 其它域的 LLM 调用 (memory.llm 记忆合成 / judge.llm 评
// 分) 仍然会 stamp ``session.last_llm_wire`` (backend 不变), 但前端过滤
// 掉它们 — 避免 "刚压缩了 recent.json, Chat 页就以为下次对话 wire 已经
// 变成 memory compress prompt" 的错觉. 这些域的 wire 有各自的入口
// (Memory 子页 / Evaluation Run 页的 [预览 prompt] 按钮) 单独预览.
//
// SimUser (``simulated_user`` / ``auto_dialog_simuser``) 在 r7 已改成
// NOSTAMP, 永远不会 stamp, 所以这里不用列它.
const CHAT_VISIBLE_SOURCES = new Set([
  'chat.send',
  'auto_dialog_target',
  'avatar_event',
  'agent_callback',
  'proactive_chat',
]);

/**
 * 挂载预览面板到给定 host.
 *
 * 返回对象暴露 `refresh()` 和 `markDirty()`, 供父 workspace 后续接会话事件
 * (比如 P09 发送消息完要重刷) 时调用.
 */
export function mountPreviewPanel(host) {
  host.innerHTML = '';
  host.classList.add('preview-panel');

  // ── header ─────────────────────────────────────────────────────
  const header = el('div', { className: 'preview-panel-header' });
  header.append(
    el('h3', {}, i18n('chat.preview.heading')),
  );

  const viewToggle = el('div', { className: 'view-toggle' });
  const btnStructured = el('button', {
    type: 'button',
    className: 'view-btn active',
    'data-view': 'structured',
  }, i18n('chat.preview.view.structured'));
  const btnRaw = el('button', {
    type: 'button',
    className: 'view-btn',
    'data-view': 'raw',
  }, i18n('chat.preview.view.raw'));
  viewToggle.append(btnStructured, btnRaw);
  header.append(viewToggle);

  const refreshBtn = el('button', {
    type: 'button',
    className: 'primary small',
  }, i18n('chat.preview.refresh_btn'));
  header.append(refreshBtn);

  host.append(header);

  // ── status line ────────────────────────────────────────────────
  const statusLine = el('div', { className: 'preview-status muted' },
    i18n('chat.preview.status.not_loaded'));
  host.append(statusLine);

  // ── body (content host) ────────────────────────────────────────
  const body = el('div', { className: 'preview-body' });
  host.append(body);

  // ── state ──────────────────────────────────────────────────────
  let currentBundle = null;
  let currentView = 'structured';
  let dirty = false;

  function setView(view) {
    currentView = view;
    for (const btn of viewToggle.querySelectorAll('.view-btn')) {
      btn.classList.toggle('active', btn.dataset.view === view);
    }
    renderBody();
  }

  btnStructured.addEventListener('click', () => setView('structured'));
  btnRaw.addEventListener('click', () => setView('raw'));

  refreshBtn.addEventListener('click', () => { refresh(); });

  /** 渲染空态 (无会话 / 缺 character_name / 出错). */
  function renderEmpty(messageKey, extra = null) {
    body.innerHTML = '';
    const box = el('div', { className: 'empty-state' },
      el('p', {}, i18n(messageKey)),
    );
    if (extra) box.append(extra);
    body.append(box);
  }

  function renderIdle() {
    body.innerHTML = '';
    body.append(el('div', { className: 'empty-state muted' },
      el('p', {}, i18n('chat.preview.status.click_to_load')),
    ));
  }

  /** 拉一次 preview + 渲染. */
  async function refresh() {
    statusLine.textContent = i18n('chat.preview.status.loading');
    const res = await api.get('/api/chat/prompt_preview', {
      expectedStatuses: [404, 409],
    });
    if (!res.ok) {
      currentBundle = null;
      if (res.status === 404) {
        statusLine.textContent = i18n('chat.preview.status.no_session');
        renderEmpty('chat.preview.empty.no_session');
        return;
      }
      if (res.status === 409) {
        statusLine.textContent = i18n('chat.preview.status.not_ready');
        renderEmpty('chat.preview.empty.no_character',
          el('p', { className: 'muted' }, res.error?.message || ''));
        return;
      }
      statusLine.textContent = i18n('chat.preview.status.load_failed');
      renderEmpty('chat.preview.empty.error',
        el('p', { className: 'muted' }, res.error?.message || `HTTP ${res.status}`));
      return;
    }
    currentBundle = res.data;
    dirty = false;
    const now = new Date().toLocaleTimeString();
    statusLine.textContent = i18n('chat.preview.status.loaded', now);
    renderBody();
  }

  /** 根据 currentBundle + currentView 重绘 body. */
  function renderBody() {
    body.innerHTML = '';
    if (!currentBundle) {
      renderIdle();
      return;
    }

    // ── metadata strip ──
    const meta = currentBundle.metadata || {};
    const metaStrip = el('div', { className: 'preview-meta' });
    metaStrip.append(
      metaBadge(i18n('chat.preview.meta.character'), meta.character_name || '-'),
      metaBadge(i18n('chat.preview.meta.master'), meta.master_name || '-'),
      metaBadge(i18n('chat.preview.meta.language'), meta.language_short || '-'),
      metaBadge(
        i18n('chat.preview.meta.template'),
        meta.template_used === 'default'
          ? i18n('chat.preview.meta.template_default')
          : i18n('chat.preview.meta.template_stored'),
      ),
      metaBadge(
        i18n('chat.preview.meta.system_chars'),
        String(currentBundle.char_counts?.system_prompt_total ?? 0),
      ),
      metaBadge(
        i18n('chat.preview.meta.approx_tokens'),
        String(currentBundle.char_counts?.approx_tokens ?? 0),
      ),
      metaBadge(
        i18n('chat.preview.meta.virtual_now'),
        (meta.built_at_virtual || '').replace('T', ' '),
      ),
    );
    body.append(metaStrip);

    // ── warnings ──
    const warnings = currentBundle.warnings || [];
    if (warnings.length) {
      const warnBox = el('div', { className: 'preview-warnings' });
      warnBox.append(el('div', { className: 'warn-heading' },
        i18n('chat.preview.warnings_heading', warnings.length)));
      for (const w of warnings) {
        warnBox.append(el('div', { className: 'warn-item' }, w));
      }
      body.append(warnBox);
    }

    if (dirty) {
      body.append(el('div', { className: 'preview-dirty-banner' },
        i18n('chat.preview.status.dirty')));
    }

    // ── main view ──
    if (currentView === 'structured') {
      body.append(renderStructured(currentBundle));
    } else {
      body.append(renderRawWire(currentBundle));
    }
  }

  function metaBadge(label, value) {
    const valueStr = String(value ?? '');
    return el('span', {
      className: 'meta-badge',
      title: `${label}: ${valueStr}`,
    },
      el('span', { className: 'meta-label' }, label + ': '),
      el('span', {
        className: 'meta-value u-wrap-anywhere',
      }, valueStr),
    );
  }

  /** Structured 视图: 各分节独立折叠块. */
  function renderStructured(bundle) {
    const { structured = {}, char_counts = {} } = bundle;
    const container = el('div', { className: 'preview-view structured-view' });

    // 顶部固定提示: 本视图仅展示首轮 system_prompt, 不含后续 user/assistant
    // 轮次. 切到 Raw wire 才能看真实的 wire_messages 流水.
    container.append(el('div', { className: 'preview-hint info' },
      i18n('chat.preview.hint.structured')));

    // section 顺序严格镜像 system_prompt 真实拼装顺序 (见
    // prompt_builder._flatten_memory_components):
    //   session_init + character_prompt
    //     + persona_header + persona_content
    //     + inner_thoughts_header + inner_thoughts_dynamic
    //     + recent_history (list → 行拼接)
    //     + time_context + holiday_context
    //   + closing
    // recent_history 早期为方便实现放在了 closing 之后, 容易让测试人员误以
    // 为"最近对话是 system_prompt 的结尾". 现在按真实顺序放回中段.
    const renderText = (key) => {
      const text = structured[key] || '';
      return createCollapsible({
        blockId: `preview-structured-${key}`,
        title: i18n(`chat.preview.section.${key}`),
        content: text,
        lengthBadge: i18n('chat.preview.length_badge', text.length),
        defaultCollapsed: _defaultCollapsedFor(key, text),
        copyable: true,
      });
    };

    container.append(renderText('session_init'));
    container.append(renderText('character_prompt'));
    container.append(renderText('persona_header'));
    container.append(renderText('persona_content'));
    container.append(renderText('inner_thoughts_header'));
    container.append(renderText('inner_thoughts_dynamic'));

    // recent_history 是 list, 单独用 speaker|content 表格渲染.
    const recent = structured.recent_history || [];
    const recentBody = el('div', { className: 'recent-history' });
    if (!recent.length) {
      recentBody.append(el('div', { className: 'muted' },
        i18n('chat.preview.section.recent_history_empty')));
    } else {
      for (const e of recent) {
        recentBody.append(el('div', { className: 'recent-entry' },
          el('span', { className: 'recent-speaker' }, e.speaker || '?'),
          el('span', { className: 'recent-sep' }, ' | '),
          el('span', { className: 'recent-content' }, e.content || ''),
        ));
      }
    }
    container.append(createCollapsible({
      blockId: 'preview-structured-recent_history',
      title: i18n('chat.preview.section.recent_history'),
      content: recentBody,
      summary: recent.length
        ? i18n('chat.preview.recent_summary', recent.length)
        : i18n('chat.preview.section.recent_history_empty'),
      lengthBadge: i18n('chat.preview.recent_badge',
        recent.length, char_counts.recent_history ?? 0),
      defaultCollapsed: true,
      copyable: false,
    }));

    container.append(renderText('time_context'));
    container.append(renderText('holiday_context'));
    container.append(renderText('closing'));

    // toolbar 挂最顶
    mountContainerToolbar(container);
    return container;
  }

  /** Raw wire 视图: 单一面板.
   *
   * P25 Day 2 polish r5: 早期 r4 把面板拆成"真实 wire (顶) + 预估 wire
   * (底)"两段, 用户手测反馈两个面板并列让人困惑, 要求只保留一个.
   * 现在的策略:
   *   - 优先显示 `bundle.last_llm_wire.wire_messages` (ground truth —
   *     上一轮真正送到 LLM 的消息数组, 由 pipeline/wire_tracker.py
   *     在每个 LLM 调用点 stamp). 标题标注"真实发给 LLM 的".
   *   - 如果会话还没调用过 LLM (last_llm_wire 为空), 退回展示
   *     `bundle.wire_messages` (从 session.messages 反推的"发送前预估"),
   *     并在标题里明确告诉 tester "尚未真实发送".
   *
   * 外部事件 / auto-dialog 等 ephemeral instruction 路径只会出现在
   * ground truth 那侧, 永远不会进 session.messages 也不会进预估 wire.
   * 为了让 tester 不误以为 "那条超长的道具指令会污染 session.messages",
   * 额外在 source ∈ {avatar_event, agent_callback, proactive_chat} 时
   * 顶部插一条突出提示.
   */
  function renderRawWire(bundle) {
    const container = el('div', { className: 'preview-view raw-view' });

    container.append(el('div', { className: 'preview-hint warn' },
      i18n('chat.preview.hint.raw')));

    container.append(renderWireSection(bundle));

    mountContainerToolbar(container);
    return container;
  }

  /** 单一 wire section — 真实 wire 优先, 无则回退预估 wire.
   *
   * r7 白名单过滤: last_llm_wire.source 必须 ∈ CHAT_VISIBLE_SOURCES.
   * 否则 (来自 memory.llm / judge.llm 等非对话域) 视作 "Chat 页看不到的",
   * 回退展示 bundle.wire_messages 预估, 并插一条提示告诉 tester 去哪
   * 找该 source 的 wire.
   */
  function renderWireSection(bundle) {
    const section = el('div', { className: 'raw-subsection wire-section' });

    // 数据源选择:
    //   1. last_llm_wire 存在且非空 且 source ∈ CHAT_VISIBLE_SOURCES
    //      → 用真实 wire (hasReal = true);
    //   2. last_llm_wire 存在但 source ∉ 白名单 (例如 memory.llm)
    //      → 不展示, 回退到预估 wire, 并插一条 "去对应页面看" 提示;
    //   3. last_llm_wire 不存在 → 回退预估, 不插提示.
    const last = bundle.last_llm_wire;
    const hasRealAny = !!(last && last.wire_messages && last.wire_messages.length);
    const sourceIsChatVisible = hasRealAny && CHAT_VISIBLE_SOURCES.has(last.source);
    const hasReal = hasRealAny && sourceIsChatVisible;
    const nonChatSource = hasRealAny && !sourceIsChatVisible;

    const wireMessages = hasReal
      ? last.wire_messages
      : (bundle.wire_messages || []);

    const headingKey = hasReal
      ? 'chat.preview.wire_section.heading_real'
      : 'chat.preview.wire_section.heading_estimate';
    section.append(
      el('h4', { className: 'raw-subsection-heading' }, i18n(headingKey)),
    );

    // r7: 若最新一次 stamp 来自非对话域 (memory.llm / judge.llm 等),
    // 插一条友好引导让 tester 知道该去哪个页面查看那个 wire.
    if (nonChatSource) {
      const labelMap = i18nRaw('chat.preview.last_wire.source_label') || {};
      const srcLabel = labelMap[last.source] || last.source || '?';
      section.append(el('div', { className: 'preview-hint info non-chat-source-hint' },
        i18n('chat.preview.wire_section.non_chat_source_hint', srcLabel)));
    }

    // ephemeral 提示 — 仅在外部事件 / proactive / agent_callback 路径的
    // 真实 wire 下显示, 普通 chat.send / 回退预估路径不展示避免干扰.
    if (hasReal && EPHEMERAL_SOURCES.has(last.source)) {
      section.append(el('div', { className: 'preview-hint ephemeral-warning' },
        i18n('chat.preview.wire_section.ephemeral_warning')));
    }

    // ── metadata strip (仅 hasReal 时有意义) ──
    if (hasReal) {
      // source_label 是个对象, 用 i18nRaw 显式拿对象; 找不到的 source 兜底回
      // 原始 slug 而不是 "chat.preview.last_wire.source_label.xxx" 整 key.
      const sourceLabelMap = i18nRaw('chat.preview.last_wire.source_label') || {};
      const sourceLabel = sourceLabelMap[last.source] || last.source || '?';
      const recordedReal = (last.recorded_at_real || '').replace('T', ' ');
      const recordedVirtual = (last.recorded_at_virtual || '').replace('T', ' ');
      const replyChars = typeof last.reply_chars === 'number' ? last.reply_chars : -1;
      const replyCharsText = replyChars < 0
        ? i18n('chat.preview.last_wire.meta_reply_pending')
        : String(replyChars);

      const metaStrip = el('div', { className: 'preview-meta last-wire-meta' });
      metaStrip.append(
        metaBadge(i18n('chat.preview.last_wire.meta_source'), sourceLabel),
        metaBadge(i18n('chat.preview.last_wire.meta_recorded_at'), recordedReal || '-'),
        metaBadge(i18n('chat.preview.last_wire.meta_virtual_time'), recordedVirtual || '-'),
        metaBadge(i18n('chat.preview.last_wire.meta_reply_chars'), replyCharsText),
      );
      if (last.note) {
        metaStrip.append(
          metaBadge(i18n('chat.preview.last_wire.meta_note'), String(last.note)),
        );
      }
      section.append(metaStrip);
    }

    // ── copy buttons ──
    const actions = el('div', { className: 'raw-actions' });
    actions.append(
      el('button', {
        type: 'button',
        className: 'small',
        onClick: () => copyText(
          JSON.stringify(wireMessages, null, 2),
          'chat.preview.copied_wire',
        ),
      }, i18n('chat.preview.copy_wire_json')),
    );
    // 回退路径: 还有 system_prompt 可以单独复制. 真实路径下
    // system_prompt 是 wire[0] 本体, 复制 wire JSON 已包含, 不重复给按钮.
    if (!hasReal && bundle.system_prompt) {
      actions.append(
        el('button', {
          type: 'button',
          className: 'small',
          onClick: () => copyText(bundle.system_prompt || '', 'chat.preview.copied_system'),
        }, i18n('chat.preview.copy_system_string')),
      );
    }
    section.append(actions);

    // ── wire messages list ──
    if (!wireMessages.length) {
      section.append(el('div', { className: 'empty-state muted' },
        i18n(hasReal
          ? 'chat.preview.last_wire.none'
          : 'chat.preview.empty.no_wire')));
      return section;
    }

    const list = el('div', { className: 'wire-list' });
    const blockIdPrefix = hasReal ? 'preview-last-wire' : 'preview-wire';
    wireMessages.forEach((msg, idx) => {
      const role = msg.role || '?';
      const content = typeof msg.content === 'string'
        ? msg.content
        : JSON.stringify(msg.content);
      const isSystem = role === 'system';
      const isTail = idx === wireMessages.length - 1;
      const title = i18n('chat.preview.wire.title', idx, role.toUpperCase());
      // 末尾消息 (通常是 ephemeral instruction 或最后一条 user) 默认展开 —
      // 这正是 tester 想看的 ground truth, 折叠掉反而失去修复意义.
      // 首条 system 默认折叠 (很长, 干扰阅读). 中段消息依字符数决定.
      const defaultCollapsed = isSystem
        ? true
        : (isTail ? false : content.length > 500);
      const block = createCollapsible({
        blockId: `${blockIdPrefix}-${idx}`,
        title,
        content,
        lengthBadge: i18n('chat.preview.length_badge', content.length),
        defaultCollapsed,
        copyable: true,
      });
      block.classList.add(`wire-role-${role}`);
      if (isTail) block.classList.add('wire-tail');
      list.append(block);
    });
    section.append(list);

    return section;
  }

  async function copyText(text, okKey) {
    try {
      await navigator.clipboard.writeText(text || '');
      toast.ok(i18n(okKey));
    } catch (err) {
      toast.err(i18n('collapsible.copy_fail'), { message: String(err) });
    }
  }

  function _defaultCollapsedFor(key, text) {
    // 短的 header / closing 默认展开方便读; 长的 persona_content 默认折叠.
    if (key === 'session_init' || key === 'closing') return false;
    if (!text) return true;
    return text.length > 200;
  }

  // ── lifecycle ──────────────────────────────────────────────────

  // 首次挂载: 如果已有会话, 自动拉一次; 否则显示空态提示.
  if (store.session?.id) {
    refresh();
  } else {
    renderIdle();
  }

  // 会话变更: 清空 currentBundle (旧数据无效); 如果新会话存在则自动刷新一次.
  // destroy 走的路径 session=null → 渲染空态.
  const offSession = on('session:change', (s) => {
    currentBundle = null;
    if (s?.id) {
      refresh();
    } else {
      statusLine.textContent = i18n('chat.preview.status.not_loaded');
      renderEmpty('chat.preview.empty.no_session');
    }
  });

  return {
    refresh,
    markDirty() {
      if (!currentBundle) return;
      dirty = true;
      renderBody();
    },
    destroy() {
      offSession();
    },
  };
}
