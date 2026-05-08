/**
 * page_run.js — Evaluation → Run 子页 (P16).
 *
 * 把 P15 的 Schemas 评分模板和 P16 的四类 Judger 接起来:
 * 选 schema → 确认模式/粒度 → 挑评分目标 (整段 / 某条消息) → 按需配 judge 模型
 * 覆盖 → [运行评分]. 按钮点下后走 `POST /api/judge/run`, 结果摘要在同页
 * 底部 "本次结果" 区渲染 (per-result 卡片: verdict 徽章 + overall / gap +
 * analysis), 并自动写入 session.eval_results 供 P17 Results 子页消费.
 *
 * 设计要点
 * --------
 * *   **一页完成配置 + 运行 + 立即反馈**. 不分多步向导, 否则单次评分要点 3
 *     次按钮 UX 太重. 唯一分步的是 "高级: judge 模型覆盖" 用 `<details>`
 *     折叠 — 多数跑 builtin schema 的场景不用改模型.
 * *   **schema 选择驱动整页状态**. 选 schema 之后, mode / granularity /
 *     是否要 reference 全部自动确定; 消息选择器 / reference 源控件都按
 *     schema 的 mode+granularity 自适应显隐 + 必填性. 避免 "用户选了
 *     absolute 还要看到 reference 字段" 这种噪声.
 * *   **scope=messages + granularity=single 支持多选 + 批量评分**. 最常见
 *     的 "跑一遍看 10 条 reply 各自得多少分" 场景. batch 最大 50 (后端硬
 *     限), 超出就截断 + toast 警告.
 * *   **scope=conversation + granularity=conversation 只跑一次**. 消息挑
 *     选器直接隐藏 (没得挑, 就是全部).
 * *   **reference 源三选一** (仅 comparative): 内联文本 / 消息内附
 *     reference_content / 稍后引入 script 导入. P16 先支持前两种.
 * *   **进度条**: 单次 LLM 调用 5-15s, 如果 batch=10 那就 1min+. run
 *     按钮按下后立即显示 "运行中 (0/N)", 用轮询或 SSE... 简化起见 P16 用
 *     一条 POST, 后端跑完一次性返回 — 前端按钮保持 disabled + "运行中..."
 *     文案, 不做逐条流式进度. 这个够用, 实装复杂度值得权衡.
 * *   **失败 per-result 展示 + 重试**. batch 里某条 LLM 调用失败, 那一张
 *     卡片标红 + [重试] 按钮, 重试只跑这一条.
 *
 * UI 约定
 * ------
 * 全页用三段式布局:
 *   1. Config form (顶部)
 *   2. Run button + status (中间)
 *   3. Recent results list (底部, 运行完填充; 不跨 session 保留)
 * 三段之间各有 `<hr class="soft">` 分隔.
 *
 * 与 Schemas 子页共享: 小工具函数 `humanGranularity` / `humanMode` 复用
 * i18n 键 `evaluation.schemas.list.badge_*`, 不再新造一套.
 */

import { api } from '../../core/api.js';
import { i18n } from '../../core/i18n.js';
import { store, on, emit } from '../../core/state.js';
import { toast } from '../../core/toast.js';
import { el, field } from '../_dom.js';
import { openPromptPreviewModal } from '../_prompt_preview_modal.js';

//
// ── module-local state ────────────────────────────────────────────
//
// `state` drives the render; any mutation ends with a `renderAll()` so
// we don't have to sprinkle `control.disabled = x` all over. Scope is
// per-mount (page remounted on session change → state resets).
//

function defaultOverride() {
  return { provider: '', base_url: '', api_key: '', model: '', temperature: '', max_tokens: '', timeout: '' };
}

const LS_OVERRIDE_OPEN = 'testbench:evaluation:run:override_open';
const LS_REF_MODE      = 'testbench:evaluation:run:ref_mode';

function defaultState() {
  return {
    loading: false,
    running: false,
    sessionId: null,
    // Raw API responses cached here so re-renders don't refetch.
    schemasList: null,           // [{ id, name, mode, granularity, ... }, ...]
    selectedSchemaId: null,
    selectedSchema: null,        // full schema dict (lazy-fetched)
    messages: null,              // session.messages array (fetched)
    // form fields
    scope: 'messages',           // 'messages' | 'conversation'
    selectedMessageIds: new Set(),  // scope=messages
    referenceMode: localStorage.getItem(LS_REF_MODE) || 'inline',
      // 'inline' | 'msg_ref' (comparative only).
      //   inline   : single B text applied to every selected A (1:N).
      //   msg_ref  : each selected A uses *its own* reference_content
      //              field as its B (1:1 per-target auto-pairing).
    referenceText: '',
    overrideOpen: localStorage.getItem(LS_OVERRIDE_OPEN) === '1',
    override: defaultOverride(),
    // P24 F6: align judger persona_system with main chat's
    // build_prompt_bundle output. Off by default — the legacy behavior
    // sends only persona.system_prompt without gap/memory context.
    matchMainChat: localStorage.getItem('testbench:eval:match_main_chat') === '1',
    // results of the most recent run (not persisted; refresh when
    // user navigates away and comes back).
    runResults: [],  // [ EvalResult dict, ... ]
    runError: null,
  };
}

//
// ── main entry ────────────────────────────────────────────────────
//

export async function renderRunPage(host) {
  // On re-mount (user navigated away + back, or session flipped), tear
  // down any previously bound subscriptions before rebinding, otherwise
  // we'd accumulate a listener per visit (memory leak + duplicate work
  // on a single event).
  for (const k of ['__offSession', '__offChatMsgs']) {
    if (typeof host[k] === 'function') {
      try { host[k](); } catch { /* ignore */ }
      host[k] = null;
    }
  }

  host.innerHTML = '';
  const root = el('div', { className: 'eval-run' });
  host.append(root);

  const state = defaultState();
  state.sessionId = store.session?.id || null;

  host.__offSession = on('session:change', () => {
    renderRunPage(host).catch((err) => {
      console.error('[evaluation/run] remount failed:', err);
    });
  });

  // 消息列表刷新: composer / auto_dialog / script / clear-all / truncate /
  // rerun 等都会 emit `chat:messages_changed`, 本页订阅后重拉 /api/chat/messages
  // 并清理已选中但被删掉的 id, 然后 rerender 整页. 避免 "我重开对话, 评分页里
  // 可选消息列表还是旧的" 这种 UX 断层. loadData 里只在 mount 时跑一次, 无法
  // 覆盖"同一 session 内消息数量变动" 这条关键路径.
  host.__offChatMsgs = on('chat:messages_changed', async () => {
    if (!state.sessionId) return;
    try {
      const msgsResp = await api.get('/api/chat/messages', { expectedStatuses: [404] });
      state.messages = msgsResp.ok ? (msgsResp.data?.messages || []) : [];
      pruneStaleSelections(state);
      renderAll(root, state);
    } catch (err) {
      console.error('[evaluation/run] refresh on chat change failed:', err);
    }
  });

  if (!state.sessionId) {
    root.append(renderEmptyState());
    return;
  }

  await loadData(state);
  renderAll(root, state);
}

/**
 * 重拉 messages 后, 清掉那些已经不在新 messages 里的选中 id / reference id.
 * 否则 clear-all 后 state.selectedMessageIds 仍指向被删 id, 运行时会被后端
 * 拒绝 (UnknownMessageId) 而且 meta 提示 "已选 3/0" 这种不自洽显示.
 */
function pruneStaleSelections(state) {
  const validIds = new Set((state.messages || []).map((m) => m.id));
  if (state.selectedMessageIds.size) {
    const keep = new Set();
    for (const id of state.selectedMessageIds) {
      if (validIds.has(id)) keep.add(id);
    }
    state.selectedMessageIds = keep;
  }
}

/**
 * Fetch schemas + messages in parallel; stash on state. Errors surface
 * as toasts; state.loading toggles drive a skeleton look.
 */
async function loadData(state) {
  state.loading = true;
  const [schemasResp, sessionResp] = await Promise.all([
    api.get('/api/judge/schemas'),
    api.get('/api/session'),
  ]);
  if (schemasResp.ok) {
    state.schemasList = schemasResp.data?.schemas || [];
    // Pick last-used or first schema by default.
    const lastId = localStorage.getItem('testbench:evaluation:run:last_schema');
    const fallbackId = state.schemasList[0]?.id || null;
    state.selectedSchemaId = state.schemasList.some((s) => s.id === lastId)
      ? lastId : fallbackId;
  } else {
    state.schemasList = [];
  }
  // 这个端点有时回 has_session=false 而非 404; 包装成空消息列表.
  if (sessionResp.ok) {
    const raw = sessionResp.data;
    state.messages = raw?.messages || raw?.session?.messages || [];
    // GET /api/session may return only describe() summary; fetch
    // messages via chat router if needed.
    if (!Array.isArray(state.messages) || state.messages.length === 0) {
      const msgsResp = await api.get('/api/chat/messages');
      if (msgsResp.ok) {
        state.messages = msgsResp.data?.messages || [];
      } else {
        state.messages = [];
      }
    }
  } else {
    state.messages = [];
  }
  if (state.selectedSchemaId) {
    await loadSelectedSchema(state);
  }
  state.loading = false;
}

async function loadSelectedSchema(state) {
  if (!state.selectedSchemaId) {
    state.selectedSchema = null;
    return;
  }
  const resp = await api.get(`/api/judge/schemas/${encodeURIComponent(state.selectedSchemaId)}`);
  if (resp.ok) {
    state.selectedSchema = resp.data?.active || null;
    localStorage.setItem(
      'testbench:evaluation:run:last_schema', state.selectedSchemaId,
    );
  } else {
    state.selectedSchema = null;
    toast.err(i18n('evaluation.run.toast.schema_load_failed'), {
      message: resp.error?.message || state.selectedSchemaId,
    });
  }
}

//
// ── render ────────────────────────────────────────────────────────
//

function renderAll(host, state) {
  host.innerHTML = '';
  host.append(renderHeader());
  host.append(renderIntro());
  host.append(renderConfigForm(host, state));
  host.append(el('hr', { className: 'soft' }));
  host.append(renderRunBar(host, state));
  host.append(el('hr', { className: 'soft' }));
  host.append(renderResultsSection(host, state));
}

function renderHeader() {
  return el('h2', {}, i18n('evaluation.run.heading'));
}

function renderIntro() {
  return el('p', { className: 'intro' }, i18n('evaluation.run.intro'));
}

function renderEmptyState() {
  return el('div', { className: 'empty-state' },
    el('h3', {}, i18n('evaluation.run.no_session.heading')),
    el('p', {}, i18n('evaluation.run.no_session.body')),
  );
}

//
// ── config form ──────────────────────────────────────────────────
//

function renderConfigForm(root, state) {
  const form = el('div', { className: 'form-grid eval-run-form' });
  form.append(renderSchemaPicker(root, state));
  if (state.selectedSchema) {
    form.append(renderSchemaBadges(state));
    form.append(renderTargetPicker(root, state));
    if (state.selectedSchema.mode === 'comparative') {
      form.append(renderReferencePicker(root, state));
    }
    form.append(renderOverrideSection(root, state));
    form.append(renderMatchMainChatOption(root, state));
  }
  return form;
}

function renderMatchMainChatOption(root, state) {
  const cb = el('input', {
    type: 'checkbox',
    checked: !!state.matchMainChat,
    onChange: (e) => {
      state.matchMainChat = e.target.checked;
      try { localStorage.setItem('testbench:eval:match_main_chat', state.matchMainChat ? '1' : '0'); }
      catch { /* storage full / private mode — preference won't persist across reloads */ }
    },
  });
  const label = el('label', {
    className: 'eval-run-match-main-chat',
    style: { display: 'flex', gap: '8px', alignItems: 'flex-start' },
  }, cb, el('span', {},
    el('strong', {}, i18n('evaluation.run.fields.match_main_chat')),
    el('div', { className: 'hint' }, i18n('evaluation.run.fields.match_main_chat_hint')),
  ));
  const wrap = el('div', { className: 'field wide' });
  wrap.append(label);
  return wrap;
}

function renderSchemaPicker(root, state) {
  const sel = el('select', {
    onChange: async (e) => {
      state.selectedSchemaId = e.target.value;
      state.selectedMessageIds = new Set();
      state.selectedSchema = null;
      await loadSelectedSchema(state);
      renderAll(root, state);
    },
  });
  const list = state.schemasList || [];
  if (!list.length) {
    sel.append(el('option', { value: '' }, i18n('evaluation.run.picker.no_schemas')));
    sel.disabled = true;
  } else {
    // Group by user/builtin for easier scanning in the dropdown.
    const userOnes  = list.filter((s) => !s.is_builtin);
    const builtins  = list.filter((s) => s.is_builtin);
    const pushGroup = (group, options) => {
      if (!options.length) return;
      const og = el('optgroup', { label: group });
      for (const s of options) {
        const label = `${s.name || s.id} · ${humanMode(s.mode)} · ${humanGranularity(s.granularity)}`;
        og.append(el('option', {
          value: s.id,
          selected: s.id === state.selectedSchemaId ? true : undefined,
        }, label));
      }
      sel.append(og);
    };
    pushGroup(i18n('evaluation.schemas.list.user_group', userOnes.length), userOnes);
    pushGroup(i18n('evaluation.schemas.list.builtin_group', builtins.length), builtins);
  }
  return field(i18n('evaluation.run.fields.schema'), sel, {
    hint: i18n('evaluation.run.fields.schema_hint'),
    wide: true,
  });
}

function renderSchemaBadges(state) {
  const schema = state.selectedSchema;
  const row = el('div', { className: 'badge-row', 'data-wide': 'true' });
  row.append(el('span', { className: 'badge' }, humanMode(schema.mode)));
  row.append(el('span', { className: 'badge' }, humanGranularity(schema.granularity)));
  if (schema.ai_ness_penalty) {
    row.append(el('span', { className: 'badge subtle' },
      i18n('evaluation.schemas.list.badge_penalty')));
  }
  const dimCount = (schema.dimensions || []).length;
  row.append(el('span', { className: 'badge subtle' },
    i18n('evaluation.run.badges.dims_fmt', dimCount)));
  if (schema.pass_rule) {
    row.append(el('span', { className: 'badge subtle', title: schema.pass_rule },
      i18n('evaluation.run.badges.has_pass_rule')));
  }
  return el('div', { className: 'field wide' },
    el('label', {}, i18n('evaluation.run.fields.schema_summary')),
    row,
  );
}

function renderTargetPicker(root, state) {
  const schema = state.selectedSchema;
  const wrap = el('div', { className: 'field wide eval-run-target' });
  wrap.append(el('label', {}, i18n('evaluation.run.fields.target')));

  if (schema.granularity === 'conversation') {
    // For conversation-granularity runs, the target is always the
    // whole session — we don't expose scope=messages here because the
    // judger would be asked to score a multi-turn schema against a
    // partial transcript (and the LLM would get confused by missing
    // context). Force scope=conversation.
    state.scope = 'conversation';
    wrap.append(el('div', { className: 'hint' },
      i18n('evaluation.run.target.conversation_forced')));
    return wrap;
  }

  // Single-granularity — let the user pick scope + which assistant
  // messages. Default scope=messages because evaluating "every single
  // assistant msg in the whole transcript" is usually too expensive;
  // testers pick specific turns they care about.
  const scopeRow = el('div', { className: 'radio-row' });
  for (const s of ['messages', 'conversation']) {
    const id = `eval-run-scope-${s}`;
    const input = el('input', {
      type: 'radio', name: 'eval-run-scope', id,
      value: s, checked: state.scope === s ? true : undefined,
      onChange: (e) => { state.scope = e.target.value; renderAll(root, state); },
    });
    scopeRow.append(el('label', { className: 'radio-pill', htmlFor: id },
      input,
      el('span', {}, i18n(`evaluation.run.target.scope_${s}`))));
  }
  wrap.append(scopeRow);

  if (state.scope === 'conversation') {
    wrap.append(el('div', { className: 'hint' },
      i18n('evaluation.run.target.scope_conversation_hint')));
    return wrap;
  }

  const allMessages = state.messages || [];
  const assistantMsgs = allMessages.filter((m) => m.role === 'assistant');
  if (!assistantMsgs.length) {
    wrap.append(el('div', { className: 'empty-inline' },
      i18n('evaluation.run.target.no_assistant_messages')));
    return wrap;
  }

  const listWrap = el('div', { className: 'eval-run-msg-list' });
  const toolbar = el('div', { className: 'eval-run-msg-toolbar' });
  const selectAllBtn = el('button', {
    type: 'button', className: 'ghost tiny',
    onClick: () => {
      if (state.selectedMessageIds.size === assistantMsgs.length) {
        state.selectedMessageIds = new Set();
      } else {
        state.selectedMessageIds = new Set(assistantMsgs.map((m) => m.id));
      }
      renderAll(root, state);
    },
  }, state.selectedMessageIds.size === assistantMsgs.length
    ? i18n('evaluation.run.target.clear_all')
    : i18n('evaluation.run.target.select_all'));
  // 手动 [刷新消息列表] — 事件订阅已经能覆盖 99% 的情况, 但如果用户在别的标签页
  // 或隔壁 agent 改了消息, chat:messages_changed 可能没 emit 到本 tab; 保留一个
  // 显式刷新入口做兜底 + 让用户看到"我现在能刷新"明确控制感.
  const refreshBtn = el('button', {
    type: 'button', className: 'ghost tiny',
    title: i18n('evaluation.run.target.refresh_hint'),
    onClick: async () => {
      const msgsResp = await api.get('/api/chat/messages', { expectedStatuses: [404] });
      state.messages = msgsResp.ok ? (msgsResp.data?.messages || []) : [];
      pruneStaleSelections(state);
      renderAll(root, state);
      toast.ok(i18n('evaluation.run.target.refresh_ok',
        state.messages.filter((m) => m.role === 'assistant').length));
    },
  }, i18n('evaluation.run.target.refresh'));
  toolbar.append(
    el('span', { className: 'meta' },
      i18n('evaluation.run.target.selection_fmt',
        state.selectedMessageIds.size, assistantMsgs.length,
      )),
    el('span', { className: 'eval-run-msg-toolbar-actions' },
      refreshBtn, selectAllBtn),
  );
  listWrap.append(toolbar);

  for (const msg of assistantMsgs) {
    const row = el('label', {
      className: 'eval-run-msg-row' + (state.selectedMessageIds.has(msg.id) ? ' selected' : ''),
    });
    const cb = el('input', {
      type: 'checkbox',
      checked: state.selectedMessageIds.has(msg.id),
      onChange: (e) => {
        if (e.target.checked) state.selectedMessageIds.add(msg.id);
        else state.selectedMessageIds.delete(msg.id);
        // 注意:这里**必须** renderAll, 否则 runbar (canRun / disabled reason) 以及
        // 依赖选中条数的参考区 hint 都不会刷. 之前只 toggle 行的 class 看着省 DOM
        // 但漏刷按钮状态, 用户反馈 "勾了消息 [运行评分] 还说请选择消息".
        // 唯一副作用是 msg list 会被重建, 滚动位置会回到顶部 — 下面显式保留.
        const scrollEl = root.querySelector('.eval-run-msg-list');
        const savedScroll = scrollEl ? scrollEl.scrollTop : 0;
        renderAll(root, state);
        const newScrollEl = root.querySelector('.eval-run-msg-list');
        if (newScrollEl) newScrollEl.scrollTop = savedScroll;
      },
    });
    const preview = (msg.content || '').replace(/\s+/g, ' ').slice(0, 120);
    const tsShort = (msg.timestamp || '').replace('T', ' ').slice(0, 16);
    row.append(
      cb,
      el('div', { className: 'eval-run-msg-main' },
        el('div', { className: 'eval-run-msg-preview' }, preview || i18n('evaluation.run.target.empty_preview')),
        el('div', { className: 'eval-run-msg-meta' },
          el('span', {}, msg.id.slice(0, 8)),
          el('span', {}, tsShort),
          msg.reference_content
            ? el('span', { className: 'badge subtle' },
                i18n('evaluation.run.target.has_reference'))
            : null,
        ),
      ),
    );
    listWrap.append(row);
  }
  wrap.append(listWrap);
  return wrap;
}

function renderReferencePicker(root, state) {
  const wrap = el('div', { className: 'field wide eval-run-ref' });
  wrap.append(el('label', {}, i18n('evaluation.run.fields.reference')));
  wrap.append(el('div', { className: 'hint' }, i18n('evaluation.run.fields.reference_hint')));

  // P16 MVP 不在 UI 中支持"整段对比": 两种触发路径等价 —
  //   (a) schema 本身 granularity=conversation + mode=comparative
  //   (b) schema granularity=single + mode=comparative, 但用户在目标区选了
  //       scope=conversation (相当于把 "整段" 当一个 target 来比)
  // 两者走的都是 reference_conversation 字段, UI 只暴露了 reference_response
  // 两种 mode, 所以一律走"不支持" 分支: 清空 + 禁用内联输入, 提示直接调 API.
  // 这里不清 state.referenceText — 用户切走 scope 后应能恢复原输入, 不惩罚误操作.
  const isConvUnsupported = state.selectedSchema.granularity === 'conversation'
    || state.scope === 'conversation';
  if (isConvUnsupported) {
    wrap.append(el('div', { className: 'empty-inline warn' },
      i18n('evaluation.run.reference.conversation_unsupported')));
    // 给一个 disabled 的占位 textarea 视觉上占住位子, 让用户一眼看出"这里
    // 本来能填但现在禁了", 而不是 "界面看起来缺了一块".
    const placeholderTa = el('textarea', {
      rows: 3,
      disabled: true,
      value: '',
      placeholder: i18n('evaluation.run.reference.disabled_placeholder'),
    });
    wrap.append(placeholderTa);
    return wrap;
  }

  const modeRow = el('div', { className: 'radio-row' });
  for (const mode of ['inline', 'msg_ref']) {
    const id = `eval-run-refmode-${mode}`;
    const input = el('input', {
      type: 'radio', name: 'eval-run-refmode', id,
      value: mode, checked: state.referenceMode === mode ? true : undefined,
      onChange: (e) => {
        state.referenceMode = e.target.value;
        localStorage.setItem(LS_REF_MODE, state.referenceMode);
        renderAll(root, state);
      },
    });
    modeRow.append(el('label', { className: 'radio-pill', htmlFor: id },
      input, el('span', {}, i18n(`evaluation.run.reference.mode_${mode}`))));
  }
  wrap.append(modeRow);

  // 显式告知"这份参考 B 如何与目标消息配对", 避免用户面对多选+inline 时
  // 误以为 B 会被自动拆分. 两个模式的语义:
  //   - inline: 一份 B 字符串, 与每一条选中的 A 分别做 pairwise 对比 (1:N).
  //   - msg_ref: 每条 A 各用自身 reference_content 作 B (1:1 自动配对).
  const selectedCount = state.scope === 'messages'
    ? state.selectedMessageIds.size : 0;
  const semanticHint = el('div', { className: 'hint eval-run-ref-semantic' });
  if (state.scope === 'conversation') {
    semanticHint.textContent = i18n('evaluation.run.reference.pairing_conv_scope');
  } else if (state.referenceMode === 'msg_ref') {
    semanticHint.textContent = selectedCount > 1
      ? i18n('evaluation.run.reference.pairing_msg_ref_multi', selectedCount)
      : i18n('evaluation.run.reference.pairing_msg_ref_single');
  } else if (selectedCount > 1) {
    semanticHint.textContent = i18n(
      'evaluation.run.reference.pairing_multi', selectedCount);
    semanticHint.classList.add('warn');
  } else if (selectedCount === 1) {
    semanticHint.textContent = i18n('evaluation.run.reference.pairing_single');
  } else {
    semanticHint.textContent = i18n('evaluation.run.reference.pairing_none');
  }
  wrap.append(semanticHint);

  if (state.referenceMode === 'inline') {
    // Multi-select + inline 是最容易被新用户误用的 1:N 组合: 看到
    // [插入模板] 按钮后以为每段注释会被 judger 分开解析, 其实不会 —
    // judger 只把整段 B 当一个字符串和每条 A 配对. 多加一块步骤条显
    // 式说清"这份 B 怎么写 + 模板按钮不做语义切分 + 哪些替代路径".
    // 仅在 scope=messages 且选了 >1 条时出现, 单选/整段 scope 没这困扰.
    if (state.scope === 'messages' && selectedCount > 1) {
      wrap.append(renderInlineMultiHowto(selectedCount));
    }
    const ta = el('textarea', {
      className: 'mono',
      rows: 4,
      placeholder: i18n('evaluation.run.reference.inline_placeholder'),
      value: state.referenceText || '',
      // Edge-triggered renderAll: per §3A B1 ("state.X 变 → 无脑 renderAll")
      // every input event normally should end with renderAll so the run
      // button's disabled state recomputes. For a textarea a full render
      // on every keystroke would destroy + recreate the element and kill
      // focus/cursor, so we only renderAll when the boolean state that
      // affects the UI (empty ↔ non-empty, which drives canRun) flips;
      // and we restore focus/selection around that renderAll to keep
      // typing fluid. A plain "just renderAll every keystroke" would be
      // equally correct but annoying to type into.
      onInput: (e) => {
        const prevHad = Boolean((state.referenceText || '').trim());
        state.referenceText = e.target.value;
        const nowHas = Boolean(state.referenceText.trim());
        if (prevHad !== nowHas) {
          const caretStart = e.target.selectionStart;
          const caretEnd = e.target.selectionEnd;
          renderAll(root, state);
          const restored = root.querySelector('.eval-run-ref textarea.mono');
          if (restored) {
            restored.focus();
            try { restored.setSelectionRange(caretStart, caretEnd); }
            catch { /* some browsers throw for certain input types */ }
          }
        }
      },
    });
    // 多条目标场景下给一个 [插入模板] 的辅助: 把选中消息的 8 字符 id + 预览
    // 做成分段注释, 让用户清楚自己在为哪 N 条 assistant 回复写同一份 B.
    // 模板不改变后端语义 (仍然是 1:N 同一字符串), 纯粹提醒用户选中的范围.
    if (state.scope === 'messages' && selectedCount > 1) {
      const allMessages = state.messages || [];
      const assistantMsgs = allMessages.filter((m) => m.role === 'assistant');
      const selectedMsgs = assistantMsgs.filter(
        (m) => state.selectedMessageIds.has(m.id));
      const toolsRow = el('div', { className: 'eval-run-ref-tools' });
      toolsRow.append(el('button', {
        type: 'button', className: 'ghost tiny',
        onClick: () => {
          const header = i18n(
            'evaluation.run.reference.tpl_header', selectedCount);
          const sections = selectedMsgs.map((m, i) => {
            const preview = (m.content || '').replace(/\s+/g, ' ').slice(0, 80);
            return `# [${i + 1}/${selectedCount}] ${m.id.slice(0, 8)} — ${preview}`;
          }).join('\n');
          const existing = (state.referenceText || '').trim();
          state.referenceText = [header, sections, '', existing].filter(Boolean).join('\n');
          renderAll(root, state);
        },
      }, i18n('evaluation.run.reference.tpl_insert')));
      toolsRow.append(el('button', {
        type: 'button', className: 'ghost tiny',
        onClick: () => {
          if (!state.referenceText) return;
          if (!confirm(i18n('evaluation.run.reference.tpl_clear_confirm'))) return;
          state.referenceText = '';
          renderAll(root, state);
        },
      }, i18n('evaluation.run.reference.tpl_clear')));
      wrap.append(toolsRow);
    }
    wrap.append(ta);
  } else {
    // msg_ref: per-target auto-pairing. Each selected A uses its own
    // reference_content as B (1:1). There is no single "pick one ref
    // message" dropdown any more — that shape conflicted with the UI
    // hint "如需为每条消息单独指定 B, 请切到 [挑带 reference_content 的消息]
    // 模式" because the old code still used the same B for all A. Now
    // we show a per-target status list (✓/✗ for each selected A) so
    // testers see exactly which messages have a usable reference and
    // which will error as MissingReference.
    wrap.append(renderPerTargetRefStatus(state));
  }
  return wrap;
}

function renderInlineMultiHowto(selectedCount) {
  // 用普通 details + ol 而不是 createCollapsible, 因为这里不需要
  // per-session 记忆展开状态 — 第一次手测就应该看到全文, 熟了之后
  // 想折就折 (浏览器原生 details 不落盘状态, 刚好符合"仅提示, 无
  // 长期状态"的需求). `details[open]` 属性让它初始展开.
  const wrap = el('details', {
    className: 'eval-run-ref-howto',
    open: true,
  });
  wrap.append(el('summary', {},
    i18n('evaluation.run.reference.inline_multi_howto_heading', selectedCount)));
  const ol = el('ol', { className: 'eval-run-ref-howto-steps' });
  const steps = i18n('evaluation.run.reference.inline_multi_howto') || [];
  for (const step of steps) {
    ol.append(el('li', {}, step));
  }
  wrap.append(ol);
  return wrap;
}

function renderPerTargetRefStatus(state) {
  const wrap = el('div', { className: 'eval-run-ref-per-target' });
  const allMessages = state.messages || [];
  const assistantMsgs = allMessages.filter((m) => m.role === 'assistant');
  const selectedMsgs = assistantMsgs.filter(
    (m) => state.selectedMessageIds.has(m.id));

  if (!selectedMsgs.length) {
    wrap.append(el('div', { className: 'empty-inline' },
      i18n('evaluation.run.reference.per_target_no_selection')));
    return wrap;
  }

  const withRef = selectedMsgs.filter(
    (m) => (m.reference_content || '').trim());
  const withoutRef = selectedMsgs.filter(
    (m) => !(m.reference_content || '').trim());

  wrap.append(el('div', { className: 'hint' },
    i18n('evaluation.run.reference.per_target_intro_fmt',
      withRef.length, selectedMsgs.length)));

  const list = el('ul', { className: 'eval-run-ref-per-target-list' });
  for (const m of selectedMsgs) {
    const hasRef = Boolean((m.reference_content || '').trim());
    const li = el('li', {
      className: 'eval-run-ref-per-target-item'
        + (hasRef ? ' ok' : ' missing'),
    });
    const icon = el('span', {
      className: 'eval-run-ref-per-target-icon',
      'aria-hidden': 'true',
    }, hasRef ? '\u2713' : '\u2717');
    const idLabel = el('code', {
      className: 'eval-run-ref-per-target-id',
    }, m.id.slice(0, 8));
    const preview = hasRef
      ? (m.reference_content || '').replace(/\s+/g, ' ').slice(0, 120)
      : i18n('evaluation.run.reference.per_target_missing_item');
    const previewEl = el('span', {
      className: 'eval-run-ref-per-target-preview',
    }, preview);
    li.append(icon, idLabel, previewEl);
    list.append(li);
  }
  wrap.append(list);

  if (withoutRef.length && withRef.length) {
    // Partial coverage: batch will still run, but some targets will
    // surface MissingReference per-item. Warn so the tester isn't
    // confused by those error cards later.
    wrap.append(el('div', { className: 'hint warn' },
      i18n('evaluation.run.reference.per_target_partial_warn_fmt',
        withoutRef.length, selectedMsgs.length)));
  }
  return wrap;
}

function renderOverrideSection(root, state) {
  const wrap = el('details', {
    className: 'field wide eval-run-override',
    open: state.overrideOpen ? true : undefined,
  });
  const summary = el('summary', {}, i18n('evaluation.run.fields.override_heading'));
  wrap.addEventListener('toggle', () => {
    state.overrideOpen = wrap.open;
    localStorage.setItem(LS_OVERRIDE_OPEN, state.overrideOpen ? '1' : '0');
  });
  wrap.append(summary);
  wrap.append(el('div', { className: 'hint' }, i18n('evaluation.run.fields.override_hint')));

  const grid = el('div', { className: 'form-grid' });
  const bind = (k, label, hint, type = 'text') => {
    const input = el('input', {
      type, value: state.override[k] || '',
      placeholder: i18n('evaluation.run.override.use_session'),
      onInput: (e) => { state.override[k] = e.target.value; },
    });
    return field(label, input, { hint });
  };
  grid.append(bind('provider', i18n('evaluation.run.override.provider'), ''));
  grid.append(bind('base_url', i18n('evaluation.run.override.base_url'), ''));
  grid.append(bind('model', i18n('evaluation.run.override.model'), ''));
  grid.append(bind('api_key', i18n('evaluation.run.override.api_key'),
    i18n('evaluation.run.override.api_key_hint'), 'password'));
  grid.append(bind('temperature', i18n('evaluation.run.override.temperature'), '', 'number'));
  grid.append(bind('max_tokens', i18n('evaluation.run.override.max_tokens'), '', 'number'));
  grid.append(bind('timeout', i18n('evaluation.run.override.timeout'),
    i18n('evaluation.run.override.timeout_hint'), 'number'));
  wrap.append(grid);
  return wrap;
}

//
// ── run bar ──────────────────────────────────────────────────────
//

function renderRunBar(root, state) {
  const wrap = el('div', { className: 'eval-run-runbar' });
  const disabled = !canRun(state);
  const btn = el('button', {
    className: 'primary',
    disabled: (state.running || disabled) ? true : undefined,
    onClick: () => startRun(root, state, null),
  }, state.running
    ? i18n('evaluation.run.button.running')
    : i18n('evaluation.run.button.run'));
  wrap.append(btn);

  // P25 r7 — Dry-run 旁边 [预览 prompt]. 和运行按钮共享 canRun 的禁用
  // 逻辑 (没有有效 schema / target 就没法构 prompt). 调 /run_prompt_preview
  // 拿到每个 target 的 wire 弹共享 modal.
  const previewBtn = el('button', {
    className: 'ghost',
    type: 'button',
    disabled: (state.running || disabled) ? true : undefined,
    title: i18n('evaluation.run.preview_prompt.tooltip'),
    onClick: () => previewJudgePrompt(state),
  }, i18n('evaluation.run.preview_prompt.label'));
  wrap.append(previewBtn);

  const reason = describeDisabledReason(state);
  if (reason && !state.running) {
    wrap.append(el('span', { className: 'hint' }, reason));
  }
  if (state.running) {
    wrap.append(el('span', { className: 'hint' },
      i18n('evaluation.run.button.running_hint')));
  }
  return wrap;
}

async function previewJudgePrompt(state) {
  const schema = state.selectedSchema;
  if (!schema) return;
  const body = {
    schema_id: state.selectedSchemaId,
    scope: state.scope,
    persist: false,
  };
  if (schema.granularity === 'single' && state.scope === 'messages') {
    body.message_ids = Array.from(state.selectedMessageIds);
  } else {
    body.message_ids = [];
  }
  if (schema.mode === 'comparative') {
    if (state.referenceMode === 'inline') {
      body.reference_response = state.referenceText.trim();
    }
  }
  const override = collectOverride(state.override);
  if (override) body.judge_model_override = override;
  if (state.matchMainChat) body.match_main_chat = true;

  const resp = await api.post('/api/judge/run_prompt_preview', body, {
    expectedStatuses: [200, 422, 404],
  });
  if (!resp.ok) {
    toast.err(i18n('evaluation.run.preview_prompt.failed'),
      { message: resp.error?.message || `HTTP ${resp.status}` });
    return;
  }

  const data = resp.data || {};
  const previews = Array.isArray(data.previews) ? data.previews : [];
  const skipped = Array.isArray(data.skipped) ? data.skipped : [];

  if (!previews.length) {
    toast.warn(i18n('evaluation.run.preview_prompt.no_previews'));
    return;
  }

  // Batch preview: 拼成一个大 wire list, 每个 target 之间用 role=system
  // 分隔一条 "── Target #k / message_id=... ──" 标签, 让 tester 在一
  // 个 modal 里看完所有 targets. 这比弹 N 个 modal 友好得多.
  const mergedWire = [];
  previews.forEach((p, idx) => {
    const tid = (p.target_message_ids || []).join(',') || `batch#${idx + 1}`;
    mergedWire.push({
      role: 'system',
      content: `── preview #${idx + 1} · target=${tid} · chars=${p.prompt_char_count || 0} ──`,
    });
    for (const m of (p.wire_messages || [])) mergedWire.push(m);
  });

  const warnings = skipped.length
    ? skipped.map(
      (s) => i18n('evaluation.run.preview_prompt.skipped_fmt',
        (s.target_message_ids || []).join(',') || '?',
        `${s.error_type || 'Error'}: ${s.message || '?'}`))
    : [];

  openPromptPreviewModal({
    title: i18n('evaluation.run.preview_prompt.modal_title', schema.id || '?'),
    intro: i18n('evaluation.run.preview_prompt.intro',
      data.count || previews.length),
    wireMessages: mergedWire,
    metaRows: [
      { label: i18n('evaluation.run.preview_prompt.meta.schema'), value: schema.id || '?' },
      { label: i18n('evaluation.run.preview_prompt.meta.mode'), value: schema.mode || '?' },
      { label: i18n('evaluation.run.preview_prompt.meta.granularity'), value: schema.granularity || '?' },
      { label: i18n('evaluation.run.preview_prompt.meta.target_count'), value: String(previews.length) },
    ],
    warnings,
  });
}

function canRun(state) {
  if (!state.selectedSchema) return false;
  const { mode, granularity } = state.selectedSchema;
  // Comparative + 整段 scope 等价于 comparative+conversation 语义 (后端走
  // reference_conversation 字段), 而 UI 只暴露了 reference_response 两种 mode,
  // 所以无论 granularity 是 single 还是 conversation, 只要 mode=comparative
  // 且 scope=conversation, 就不能跑 — 直接在 canRun 拦.
  if (mode === 'comparative' && state.scope === 'conversation') return false;
  if (granularity === 'single' && state.scope === 'messages') {
    if (!state.selectedMessageIds.size) return false;
  }
  if (mode === 'comparative') {
    if (granularity === 'conversation') return false;
    if (state.referenceMode === 'inline' && !(state.referenceText || '').trim()) {
      return false;
    }
    if (state.referenceMode === 'msg_ref') {
      // Per-target mode: at least one selected target must carry a
      // non-empty reference_content. Partial batches are allowed (the
      // per-target status list surfaces which ones will MissingReference),
      // but if ALL selected targets lack references the batch is pure
      // noise and we block the run outright.
      const allMessages = state.messages || [];
      const selected = allMessages.filter(
        (m) => m.role === 'assistant' && state.selectedMessageIds.has(m.id));
      if (!selected.some((m) => (m.reference_content || '').trim())) {
        return false;
      }
    }
  }
  return true;
}

function describeDisabledReason(state) {
  if (!state.selectedSchema) return i18n('evaluation.run.disabled.no_schema');
  const { mode, granularity } = state.selectedSchema;
  // 保持和 canRun 相同的优先级: comparative + 整段 scope 直接不支持, 比 "缺
  // 消息 / 缺参考" 更高优, 否则会给用户错误的 "去填参考" 暗示.
  if (mode === 'comparative' && state.scope === 'conversation') {
    return i18n('evaluation.run.disabled.conv_comparative_unsupported');
  }
  if (granularity === 'single' && state.scope === 'messages' && !state.selectedMessageIds.size) {
    return i18n('evaluation.run.disabled.no_message');
  }
  if (mode === 'comparative') {
    if (granularity === 'conversation') {
      return i18n('evaluation.run.disabled.conv_comparative_unsupported');
    }
    if (state.referenceMode === 'inline' && !(state.referenceText || '').trim()) {
      return i18n('evaluation.run.disabled.no_ref_inline');
    }
    if (state.referenceMode === 'msg_ref') {
      const allMessages = state.messages || [];
      const selected = allMessages.filter(
        (m) => m.role === 'assistant' && state.selectedMessageIds.has(m.id));
      if (!selected.some((m) => (m.reference_content || '').trim())) {
        return i18n('evaluation.run.disabled.no_ref_per_target');
      }
    }
  }
  return '';
}

//
// ── POST /judge/run + retry ──────────────────────────────────────
//

async function startRun(root, state, onlyMessageId) {
  if (state.running) return;
  state.running = true;
  state.runError = null;
  renderAll(root, state);

  const schema = state.selectedSchema;
  const body = {
    schema_id: state.selectedSchemaId,
    scope: state.scope,
    persist: true,
  };

  if (schema.granularity === 'single' && state.scope === 'messages') {
    body.message_ids = onlyMessageId
      ? [onlyMessageId]
      : Array.from(state.selectedMessageIds);
  } else {
    body.message_ids = [];
  }

  if (schema.mode === 'comparative') {
    if (state.referenceMode === 'inline') {
      body.reference_response = state.referenceText.trim();
    }
    // msg_ref mode: deliberately do NOT set reference_response or
    // reference_message_id. The backend falls back to each target's own
    // ``reference_content`` field (1:1 auto-pairing), which is what the
    // UI's "per-target" status list just told the user to expect.
  }

  const override = collectOverride(state.override);
  if (override) body.judge_model_override = override;

  if (state.matchMainChat) body.match_main_chat = true;

  const resp = await api.post('/api/judge/run', body, {
    expectedStatuses: [200, 422, 404],
  });
  state.running = false;

  if (!resp.ok) {
    state.runError = resp.error?.message || resp.error?.type || 'unknown error';
    toast.err(i18n('evaluation.run.toast.run_failed'), { message: state.runError });
    renderAll(root, state);
    return;
  }

  const data = resp.data || {};
  const results = data.results || [];
  if (onlyMessageId) {
    // Retry — replace the single-item result in runResults.
    state.runResults = state.runResults.map(
      (r) => (r.target_message_ids || [])[0] === onlyMessageId
        ? (results[0] || r) : r,
    );
  } else {
    state.runResults = results;
  }
  if (data.error_count > 0) {
    toast.warn(i18n('evaluation.run.toast.partial_error', data.error_count, data.total));
  } else {
    toast.ok(i18n('evaluation.run.toast.run_ok', data.total));
  }
  // P24 §3.4 F6 — if the user ticked "match_main_chat" but the backend
  // had to fall back (persona not ready / bundle error), tell them so
  // they don't mistakenly interpret the score as reflecting the full
  // main-chat context.
  if (data.match_main_chat_requested && !data.match_main_chat_applied) {
    const reason = data.match_main_chat_fallback_reason || 'unknown';
    toast.warn(
      i18n('evaluation.run.toast.match_main_chat_fallback', reason),
      { duration: 8000 },
    );
  }
  // 广播给 Results / Aggregate 子页 + Chat 消息徽章, 让它们无需手动刷新.
  // payload 给出本次新增的 id 列表, 便于订阅者判断是否需要清某个过滤再
  // 重新拉数据 (例如选中某条消息时, 新增结果若匹配当前过滤就会自动出现).
  emit('judge:results_changed', {
    added_ids: results.map((r) => r.id).filter(Boolean),
    total: data.total || results.length,
    error_count: data.error_count || 0,
  });
  renderAll(root, state);
}

function collectOverride(override) {
  const out = {};
  for (const [k, v] of Object.entries(override || {})) {
    if (v === null || v === undefined || v === '') continue;
    if (['temperature', 'max_tokens', 'timeout'].includes(k)) {
      const num = Number(v);
      if (!Number.isFinite(num)) continue;
      out[k] = num;
    } else {
      out[k] = v;
    }
  }
  return Object.keys(out).length ? out : null;
}

//
// ── results section (inline, post-run) ────────────────────────────
//

function renderResultsSection(root, state) {
  const wrap = el('div', { className: 'eval-run-results' });
  wrap.append(el('h3', {}, i18n('evaluation.run.results.heading')));
  if (state.runError) {
    wrap.append(el('div', { className: 'eval-run-result eval-run-result--error' },
      el('div', { className: 'eval-run-result-title' },
        i18n('evaluation.run.results.batch_error')),
      el('pre', { className: 'mono' }, state.runError),
    ));
    return wrap;
  }
  if (!state.runResults.length) {
    wrap.append(el('div', { className: 'hint' }, i18n('evaluation.run.results.empty')));
    return wrap;
  }
  wrap.append(el('div', { className: 'hint' },
    i18n('evaluation.run.results.empty_after_nav')));
  for (const r of state.runResults) {
    wrap.append(renderResultCard(root, state, r));
  }
  return wrap;
}

function renderResultCard(root, state, r) {
  const isError = Boolean(r.error);
  const card = el('div', {
    className: 'eval-run-result' + (isError ? ' eval-run-result--error' : ''),
  });

  const verdictBadge = el('span', {
    className: 'badge verdict-' + (r.verdict || 'unknown').replace(/[^a-z_]/gi, '_'),
  }, r.verdict || i18n('evaluation.run.results.verdict_unknown'));

  const passedBadge = r.passed
    ? el('span', { className: 'badge success' }, i18n('evaluation.run.results.passed'))
    : el('span', { className: 'badge danger' }, i18n('evaluation.run.results.failed'));

  const scoreLabel = r.mode === 'comparative'
    ? i18n('evaluation.run.results.gap_fmt', r.gap ?? 0)
    : i18n('evaluation.run.results.overall_fmt', r.scores?.overall_score ?? 0);

  const meta = el('div', { className: 'eval-run-result-meta' },
    verdictBadge,
    isError ? null : passedBadge,
    el('span', { className: 'badge subtle' }, scoreLabel),
    el('span', { className: 'badge subtle' },
      `${r.duration_ms || 0}ms`),
    el('span', { className: 'meta' },
      r.target_message_ids?.length
        ? r.target_message_ids.map((id) => id.slice(0, 8)).join(', ')
        : i18n('evaluation.run.results.target_conversation')),
  );

  const targetPreview = r.target_preview || {};
  const previewText = targetPreview.ai_response
    || targetPreview.user_input
    || '';

  const title = el('div', { className: 'eval-run-result-title' },
    el('span', {}, r.schema_id),
    el('span', { className: 'spacer' }),
    el('button', {
      type: 'button', className: 'ghost tiny',
      onClick: () => retryOne(root, state, r),
      title: i18n('evaluation.run.results.retry_hint'),
    }, i18n('evaluation.run.results.retry')),
  );

  card.append(title, meta);

  if (isError) {
    card.append(el('pre', { className: 'mono' }, r.error));
  } else {
    if (r.analysis) {
      card.append(el('p', { className: 'eval-run-result-analysis' }, r.analysis));
    }
    if (previewText) {
      card.append(el('div', { className: 'eval-run-result-preview' },
        el('strong', {}, i18n('evaluation.run.results.preview_label')),
        el('span', {}, previewText),
      ));
    }
    if (r.strengths?.length || r.weaknesses?.length) {
      const sw = el('div', { className: 'eval-run-result-sw' });
      if (r.strengths?.length) {
        sw.append(renderBulletList(i18n('evaluation.run.results.strengths'), r.strengths));
      }
      if (r.weaknesses?.length) {
        sw.append(renderBulletList(i18n('evaluation.run.results.weaknesses'), r.weaknesses));
      }
      card.append(sw);
    }
    if (r.mode === 'comparative' && r.scores?.per_dim_gap) {
      card.append(renderPerDimGap(r.scores.per_dim_gap));
    }
    if (r.mode !== 'comparative' && r.scores) {
      card.append(renderDimScores(r.scores));
    }
    // Always offer a [详情] collapsible that dumps the full raw scores
    // dict, so even a custom schema's non-standard fields are visible.
    card.append(renderDetailsDisclosure(r));
  }
  return card;
}

function renderBulletList(label, items) {
  const wrap = el('div', { className: 'eval-run-result-list' });
  wrap.append(el('strong', {}, label));
  const ul = el('ul', {});
  for (const it of items) ul.append(el('li', {}, it));
  wrap.append(ul);
  return wrap;
}

function renderDimScores(scores) {
  const wrap = el('div', { className: 'eval-run-result-dims' });
  for (const [k, v] of Object.entries(scores || {})) {
    if (['raw_score', 'overall_score'].includes(k)) continue;
    wrap.append(el('span', { className: 'badge subtle' }, `${k} · ${v}`));
  }
  return wrap;
}

function renderPerDimGap(perDimGap) {
  const wrap = el('div', { className: 'eval-run-result-dims' });
  for (const [k, v] of Object.entries(perDimGap || {})) {
    const cls = v > 0 ? 'badge success' : v < 0 ? 'badge danger' : 'badge subtle';
    const sign = v > 0 ? '+' : '';
    wrap.append(el('span', { className: cls }, `${k} ${sign}${v}`));
  }
  return wrap;
}

function renderDetailsDisclosure(r) {
  const det = el('details', { className: 'eval-run-result-details' });
  det.append(el('summary', {}, i18n('evaluation.run.results.details')));
  det.append(el('pre', { className: 'mono' },
    JSON.stringify(r.scores, null, 2)));
  return det;
}

async function retryOne(root, state, oldResult) {
  const mid = (oldResult.target_message_ids || [])[0];
  if (!mid) {
    toast.warn(i18n('evaluation.run.results.retry_unavailable'));
    return;
  }
  await startRun(root, state, mid);
}

//
// ── helpers ──────────────────────────────────────────────────────
//

function humanMode(mode) {
  return mode === 'comparative'
    ? i18n('evaluation.schemas.list.badge_comparative')
    : i18n('evaluation.schemas.list.badge_absolute');
}

function humanGranularity(granularity) {
  return granularity === 'conversation'
    ? i18n('evaluation.schemas.list.badge_conversation')
    : i18n('evaluation.schemas.list.badge_single');
}
