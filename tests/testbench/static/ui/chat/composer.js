/**
 * chat/composer.js — Chat workspace 底部输入栏
 * (P09 Manual + P11 SimUser + P12 Scripted + P13 Auto-Dialog 四模式).
 *
 * PLAN §Chat workspace 约定两行扁平布局:
 *   Row 1: Clock + Next turn +Δt | Role (User/System) | Mode (Manual/SimUser/Script/Auto)
 *   Row 2: textarea | [Send] [Inject sys] [⋯ more]
 *
 * 四种模式都已接入. Auto 模式下的实际 SSE 连接在 auto_banner.js 里, composer
 * 只负责 "采集 config + 点 [启动 Auto]"; 运行期 UI (进度 + 暂停/停止) 全部
 * 归顶部进度横幅管.
 *
 * Script 模式流程:
 *   1. Row1 Mode = script → 展开 Script 子控件: 剧本下拉 + [加载] + [下一轮]
 *      + [跑完] + [卸载] + 进度 badge (cursor/N).
 *   2. 点 [加载] → POST /api/chat/script/load {name}. bootstrap 可能被
 *      跳过 (会话已有消息), 此时 toast.ok + warning.
 *   3. 点 [下一轮] → SSE POST /api/chat/script/next. 后端转发 chat_runner.stream_send
 *      的 user/assistant_start/delta/assistant/done 事件, 额外发 script_turn_warnings
 *      / script_turn_done / script_exhausted. 前端复用 message_stream 的 handle.
 *   4. 点 [跑完] → SSE POST /api/chat/script/run_all. 循环直到 script_exhausted
 *      或 error. 期间 [发送] / [生成] / [下一轮] / [跑完] 全部禁用; 完成后恢复.
 *   5. 点 [卸载] → POST /api/chat/script/unload. 清空 script_state.
 *   6. 脚本执行的 user turn source='script', assistant 消息 source='llm' 与手动一致;
 *      若有 pending_reference, assistant 消息的 reference_content 自动回填 expected.
 *
 * SimUser 模式流程:
 *   1. Row1 Mode = simuser → 展开 Style 下拉 + [自定义 persona] 按钮 + [生成]
 *      按钮. [注入 sys] 保留原语义 (SimUser 不管系统注入, 仍走手动路径).
 *   2. 点 [生成] → POST /api/chat/simulate_user {style, user_persona_prompt,
 *      extra_hint}. 返回的 content 直接填进 textarea, role 强制切到 user,
 *      source 内部记为 'simuser' (直到用户再次编辑 textarea 为止).
 *   3. 用户可以改草稿后点 [发送]: 走 /api/chat/send (source=simuser), 与
 *      Manual 发送**完全同一路径**, 只是 source 标签不同. 时钟推进 / 落盘 /
 *      preview 刷新逻辑因此无需特殊分支.
 *   4. 用户清空或继续编辑草稿时, source 自动退回 'manual' — 因为这时内容
 *      已经不再是 SimUser 的原产.
 *
 * 保留一条 source 跟随规则: 从 simuser 草稿编辑一个字, source 就回退到
 * manual. 这避免"测试人员小改后依然被标 simuser"造成的审计歧义.
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
 * @param {object} deps  { stream, autoBanner }
 *   stream     — mountMessageStream 的 handle
 *   autoBanner — mountAutoBanner 的 handle (P13); 可选, 没挂就禁用 Auto 模式
 */
export function mountComposer(host, { stream, autoBanner = null }) {
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

  // Mode: manual / simuser / script / auto — 四模式全部可用 (P13 起).
  const modeGroup = el('span', { className: 'mode-group' });
  modeGroup.append(
    el('span', { className: 'muted' }, i18n('chat.composer.mode_prefix')),
  );
  const modeSelect = el('select', { className: 'mode-select' });
  modeSelect.append(
    el('option', { value: 'manual' }, i18n('chat.composer.mode.manual')),
    el('option', { value: 'simuser' }, i18n('chat.composer.mode.simuser')),
    el('option', { value: 'script' }, i18n('chat.composer.mode.script')),
    el('option', { value: 'auto' }, i18n('chat.composer.mode.auto')),
  );
  modeGroup.append(modeSelect);

  // ── SimUser 子控件 ─────────────────────────────────────────────
  // style 下拉 / [自定义 persona] 折叠按钮 / [生成] 按钮. 默认隐藏,
  // 当 mode=simuser 时整组显示.
  const simuserControls = el('span', {
    className: 'simuser-controls',
    style: { display: 'none' },
  });
  const styleSelect = el('select', { className: 'simuser-style-select' });
  // options 在 refreshStyleOptions() 里首次加载时注入, 避免在 i18n 字典
  // 缺某个 style key 时报错 (fallback 用 id 原文).
  const personaToggleBtn = el('button', {
    type: 'button',
    className: 'small subtle',
    onClick: () => togglePersonaEditor(),
    title: i18n('chat.composer.simuser.persona_toggle_title'),
  }, i18n('chat.composer.simuser.persona_toggle'));
  const generateBtn = el('button', {
    type: 'button',
    className: 'small',
    onClick: () => generateDraft(),
    title: i18n('chat.composer.simuser.generate_title'),
  }, i18n('chat.composer.simuser.generate'));
  simuserControls.append(
    el('span', { className: 'muted' }, i18n('chat.composer.simuser.style_prefix')),
    styleSelect,
    personaToggleBtn,
    generateBtn,
  );
  modeGroup.append(simuserControls);

  // ── Script 子控件 (P12) ─────────────────────────────────────────
  // 剧本下拉 / [加载] / [下一轮] / [跑完] / [卸载] + 进度 badge. 默认隐藏.
  const scriptControls = el('span', {
    className: 'script-controls',
    style: { display: 'none' },
  });
  const templateSelect = el('select', {
    className: 'script-template-select',
    title: i18n('chat.composer.script.load_title'),
    // 选择任意剧本后立即重算 [加载] 按钮 disabled. 不接这行会导致
    // "选中剧本但按钮一直灰着" — 必须触发其它 sync 路径 (切模式 / 刷新
    // 列表 / session:change) 才能恢复, 是一个高欺骗性的 UI bug. 见
    // AGENT_NOTES §4.17 #39.
    onChange: () => syncScriptButtons(),
  });
  templateSelect.append(el('option', {
    value: '',
  }, i18n('chat.composer.script.no_template_selected')));
  const templateRefreshBtn = el('button', {
    type: 'button',
    className: 'small subtle',
    onClick: () => loadTemplateList(true),
    title: i18n('chat.composer.script.refresh_title'),
  }, i18n('chat.composer.script.refresh_templates'));
  const loadBtn = el('button', {
    type: 'button',
    className: 'small',
    onClick: () => loadScript(),
    title: i18n('chat.composer.script.load_title'),
  }, i18n('chat.composer.script.load'));
  const nextBtn = el('button', {
    type: 'button',
    className: 'small',
    onClick: () => scriptNext(),
    title: i18n('chat.composer.script.next_title'),
  }, i18n('chat.composer.script.next'));
  const runAllBtn = el('button', {
    type: 'button',
    className: 'small',
    onClick: () => scriptRunAll(),
    title: i18n('chat.composer.script.run_all_title'),
  }, i18n('chat.composer.script.run_all'));
  const unloadBtn = el('button', {
    type: 'button',
    className: 'small subtle',
    onClick: () => unloadScript(),
    title: i18n('chat.composer.script.unload_title'),
  }, i18n('chat.composer.script.unload'));
  // P19 hotfix 5: Script run_all / next 运行期间允许手动 Stop — 通过 abort 当前 SSE
  // 流. abort 后 sse_client 会走 onDone() 分支, 回调里 scriptRunning = false 并
  // refreshScriptState, 后端 /chat/script/* 的 generator 在 disconnect 时会提前 return.
  const stopBtn = el('button', {
    type: 'button',
    className: 'small danger',
    onClick: () => scriptStop(),
    title: i18n('chat.composer.script.stop_title'),
    style: { display: 'none' },
  }, i18n('chat.composer.script.stop'));
  const scriptProgressBadge = el('span', {
    className: 'script-progress-badge muted',
    style: { display: 'none' },
  });
  scriptControls.append(
    el('span', { className: 'muted' }, i18n('chat.composer.script.template_prefix')),
    templateSelect,
    templateRefreshBtn,
    loadBtn,
    nextBtn,
    runAllBtn,
    stopBtn,
    unloadBtn,
    scriptProgressBadge,
  );
  modeGroup.append(scriptControls);

  // ── Auto-Dialog 子控件 (P13) ────────────────────────────────────
  // style 下拉 / [自定义 persona] 折叠 / 轮数 input / step mode 下拉 /
  // step 秒数 input / [启动 Auto] 按钮. 默认隐藏, 当 mode=auto 时整组显示.
  // persona 编辑区与 SimUser 模式的**互相独立** — 不继承、不共享, 切模式
  // 只切显示, 数据各走各.
  const autoControls = el('span', {
    className: 'auto-controls',
    style: { display: 'none' },
  });
  const autoStyleSelect = el('select', { className: 'auto-style-select' });
  const autoPersonaToggleBtn = el('button', {
    type: 'button',
    className: 'small subtle',
    onClick: () => toggleAutoPersonaEditor(),
    title: i18n('chat.composer.auto.persona_toggle_title'),
  }, i18n('chat.composer.auto.persona_toggle'));
  const autoTotalInput = el('input', {
    type: 'number',
    className: 'auto-total-input',
    min: '1',
    max: '50',
    value: '5',
    title: i18n('chat.composer.auto.total_turns_title'),
  });
  const autoStepModeSelect = el('select', {
    className: 'auto-step-mode-select',
    title: i18n('chat.composer.auto.step_mode_title'),
    onChange: () => syncAutoFields(),
  });
  autoStepModeSelect.append(
    el('option', { value: 'off' }, i18n('chat.composer.auto.step_mode.off')),
    el('option', { value: 'fixed' }, i18n('chat.composer.auto.step_mode.fixed')),
  );
  const autoStepSecondsInput = el('input', {
    type: 'number',
    className: 'auto-step-seconds-input',
    min: '1',
    max: '604800',
    value: '300',
    title: i18n('chat.composer.auto.step_seconds_title'),
  });
  const autoStartBtn = el('button', {
    type: 'button',
    className: 'small primary',
    onClick: () => startAutoDialog(),
    title: i18n('chat.composer.auto.start_title'),
  }, i18n('chat.composer.auto.start'));
  autoControls.append(
    el('span', { className: 'muted' }, i18n('chat.composer.auto.style_prefix')),
    autoStyleSelect,
    autoPersonaToggleBtn,
    el('span', { className: 'muted' }, i18n('chat.composer.auto.total_turns_prefix')),
    autoTotalInput,
    el('span', { className: 'muted' }, i18n('chat.composer.auto.step_mode_prefix')),
    autoStepModeSelect,
    autoStepSecondsInput,
    el('span', { className: 'muted' }, i18n('chat.composer.auto.step_seconds_unit')),
    autoStartBtn,
  );
  modeGroup.append(autoControls);

  row1.append(modeGroup);

  const pendingBadge = el('span', { className: 'pending-badge', style: { display: 'none' } });
  row1.append(pendingBadge);

  host.append(row1);

  // ── SimUser persona 编辑区 (折叠, 只有 mode=simuser 且点 Persona 按钮才显示)
  // extra_hint 做成 single-line 输入紧挨 textarea 更简洁, 留到后续如果需要
  // 再补; 本期只暴露 user_persona_prompt + 风格, hint 先不给 UI.
  const personaEditor = el('div', {
    className: 'simuser-persona-editor',
    style: { display: 'none' },
  });
  const personaTextarea = el('textarea', {
    className: 'simuser-persona-textarea',
    placeholder: i18n('chat.composer.simuser.persona_placeholder'),
    rows: 2,
  });
  personaEditor.append(
    el('div', { className: 'muted' }, i18n('chat.composer.simuser.persona_intro')),
    personaTextarea,
  );
  host.append(personaEditor);

  // Auto-Dialog 自己的 persona 编辑区 (与 SimUser 模式独立, 不共享).
  const autoPersonaEditor = el('div', {
    className: 'auto-persona-editor',
    style: { display: 'none' },
  });
  const autoPersonaTextarea = el('textarea', {
    className: 'auto-persona-textarea',
    placeholder: i18n('chat.composer.auto.persona_placeholder'),
    rows: 2,
  });
  autoPersonaEditor.append(
    el('div', { className: 'muted' }, i18n('chat.composer.auto.persona_intro')),
    autoPersonaTextarea,
  );
  host.append(autoPersonaEditor);

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

  // ── mode 切换 → 切 SimUser / Script / Auto 控件显隐 ─────────
  function syncModeUI() {
    const mode = modeSelect.value;
    const isSimUser = mode === 'simuser';
    const isScript = mode === 'script';
    const isAuto = mode === 'auto';
    simuserControls.style.display = isSimUser ? '' : 'none';
    scriptControls.style.display = isScript ? '' : 'none';
    autoControls.style.display = isAuto ? '' : 'none';

    if (isSimUser) {
      // SimUser 模式下 role 只用 user (生成的是用户消息). system 语义
      // 保留给手动路径, 切回 Manual 后自动恢复.
      roleSelect.value = 'user';
      roleSelect.disabled = true;
      syncRoleUI();
      ensureStylesLoaded();
    } else if (isScript) {
      // Script 模式下 role 同样锁在 user — 脚本的发送走 /chat/script/next,
      // 后端写死 ROLE_USER; 即使测试人员改了 role 也不影响, 但锁死避免歧义.
      roleSelect.value = 'user';
      roleSelect.disabled = true;
      syncRoleUI();
      // 首次进入时拉模板列表 + 读当前 script_state, 此后按需手动刷新.
      loadTemplateList(false);
      refreshScriptState();
    } else if (isAuto) {
      // Auto 模式下 role / Send 都无用 (SimUser + Target 自动轮流发), 锁死 user.
      roleSelect.value = 'user';
      roleSelect.disabled = true;
      syncRoleUI();
      // style 下拉 lazy 加载, 沿用 SimUser 的 /styles 接口 (同一套 STYLE_PRESETS).
      // ensureAutoStylesLoaded 内部会先 await ensureStylesLoaded (单飞 Promise),
      // 确保 styleSelect 被填满之后再往 autoStyleSelect 拷贝, 不会拷到空.
      ensureAutoStylesLoaded();
    } else {
      roleSelect.disabled = false;
    }

    if (!isSimUser) {
      personaEditor.style.display = 'none';
      // draftOrigin 只对 simuser 有意义, 切出就重置 (避免切到 script 还
      // 把 textarea 当成 simuser 草稿).
      draftOrigin = null;
    }
    if (!isAuto) {
      autoPersonaEditor.style.display = 'none';
    }
    syncGenerateBtn();
    syncScriptButtons();
    syncAutoFields();
  }
  modeSelect.addEventListener('change', syncModeUI);

  function togglePersonaEditor() {
    const willShow = personaEditor.style.display === 'none';
    personaEditor.style.display = willShow ? '' : 'none';
    if (willShow) { personaTextarea.focus(); }
  }

  function toggleAutoPersonaEditor() {
    const willShow = autoPersonaEditor.style.display === 'none';
    autoPersonaEditor.style.display = willShow ? '' : 'none';
    if (willShow) { autoPersonaTextarea.focus(); }
  }

  // ── state ──────────────────────────────────────────────────────
  let pending = false;
  let generating = false;
  let currentStream = null;
  // 当前 textarea 内容是否源自 SimUser 生成. 只要用户编辑过一次,
  // 就回退到 null (= 手动). Send 时据此决定 source 标签.
  let draftOrigin = null;  // null | 'simuser'

  // P12 Script 状态. templates 是缓存的下拉列表 (lazy 拉, [刷新列表]
  // 强制重拉); scriptState 是当前会话是否加载了脚本 (/api/chat/script/state
  // 返回的 shape 或 null); scriptRunning 在 next/run_all 期间置 true 锁 UI.
  let templatesLoaded = false;
  let scriptTemplates = [];
  let scriptState = null;
  let scriptRunning = false;
  let scriptCurrentAssistantHandle = null;

  // P13 Auto-Dialog 状态. autoRunning 在 [启动 Auto] 之后置 true 锁 UI;
  // auto_banner 结束时会 emit `auto_dialog:finished`, 我们监听了解锁.
  let autoRunning = false;

  function setPending(value) {
    pending = value;
    sendBtn.disabled = value;
    injectBtn.disabled = value;
    sendBtn.textContent = value
      ? i18n('chat.composer.sending')
      : i18n('chat.composer.send');
    syncGenerateBtn();
    syncScriptButtons();
    syncAutoFields();
  }

  function syncGenerateBtn() {
    // [生成] 只在 SimUser 模式 + 非 pending + 非 generating 可用.
    const isSimUser = modeSelect.value === 'simuser';
    generateBtn.disabled = !isSimUser || pending || generating || scriptRunning || autoRunning;
    generateBtn.textContent = generating
      ? i18n('chat.composer.simuser.generating')
      : i18n('chat.composer.simuser.generate');
  }

  function syncScriptButtons() {
    // 只有 Script 模式下这些按钮真实可见; 但我们仍统一更新 disabled 状态
    // (避免用户在 script 模式内多重状态切换时按钮错位).
    const isScript = modeSelect.value === 'script';
    const hasSession = !!store.session?.id;
    const hasTemplate = !!(templateSelect.value || '').trim();
    const loaded = scriptState != null;
    const exhausted = loaded && scriptState.exhausted;
    // [加载] / [刷新列表]: 只要在 Script 模式 + 非 pending/running 就可用;
    // 加载要求下拉有选中项.
    loadBtn.disabled = !isScript || !hasSession || !hasTemplate || pending || scriptRunning || autoRunning;
    templateRefreshBtn.disabled = !isScript || pending || scriptRunning || autoRunning;
    // [下一轮] / [跑完]: 需要加载了脚本且未跑完.
    const canAdvance = isScript && loaded && !exhausted && hasSession && !pending && !scriptRunning && !autoRunning;
    nextBtn.disabled = !canAdvance;
    runAllBtn.disabled = !canAdvance;
    unloadBtn.disabled = !isScript || !loaded || pending || scriptRunning || autoRunning;
    // Stop 仅在 Script 模式且正在跑时显示 / 可用; 不跑时直接隐藏避免占位.
    stopBtn.style.display = isScript && scriptRunning ? '' : 'none';
    stopBtn.disabled = !scriptRunning;

    nextBtn.textContent = scriptRunning
      ? i18n('chat.composer.script.next_running')
      : i18n('chat.composer.script.next');
    runAllBtn.textContent = scriptRunning
      ? i18n('chat.composer.script.run_all_running')
      : i18n('chat.composer.script.run_all');
    loadBtn.textContent = i18n('chat.composer.script.load');

    // 进度 badge
    if (isScript && loaded) {
      scriptProgressBadge.style.display = '';
      const label = exhausted
        ? i18n('chat.composer.script.exhausted_status')
        : i18n('chat.composer.script.progress', scriptState.cursor, scriptState.turns_count);
      scriptProgressBadge.textContent = `[${scriptState.template_name}] ${label}`;
    } else {
      scriptProgressBadge.style.display = 'none';
    }
  }

  function syncAutoFields() {
    const isAuto = modeSelect.value === 'auto';
    const canStart = isAuto && !autoRunning && !pending && !scriptRunning && !!store.session?.id;
    // step_seconds input 只在 fixed 模式下可见
    const stepFixed = autoStepModeSelect.value === 'fixed';
    autoStepSecondsInput.style.display = stepFixed ? '' : 'none';
    // 运行中 disable 所有配置字段, 防止误改 (即使改了也不影响已在跑的 run).
    autoStyleSelect.disabled = autoRunning;
    autoPersonaToggleBtn.disabled = autoRunning;
    autoTotalInput.disabled = autoRunning;
    autoStepModeSelect.disabled = autoRunning;
    autoStepSecondsInput.disabled = autoRunning;
    autoStartBtn.disabled = !canStart;
    autoStartBtn.textContent = autoRunning
      ? i18n('chat.composer.auto.running_hint')
      : i18n('chat.composer.auto.start');
  }

  // 同样用单飞 Promise 守卫 — 避免多次 fire-and-forget 调用重复拷贝 options.
  // 内部先 await ensureStylesLoaded() 等后端 /styles 真正返回再拷贝, 保证不拷空.
  // P24 欠账清返 (§13.5 sweep): 加 .catch 清空, 同 ensureStylesLoaded.
  let autoStylesLoadedPromise = null;
  function ensureAutoStylesLoaded() {
    if (autoStylesLoadedPromise) return autoStylesLoadedPromise;
    autoStylesLoadedPromise = (async () => {
      await ensureStylesLoaded();
      autoStyleSelect.innerHTML = '';
      // 复制 styleSelect 的 options 过来 — 保持 label / title 一致.
      for (const opt of Array.from(styleSelect.options)) {
        autoStyleSelect.append(opt.cloneNode(true));
      }
      autoStyleSelect.value = styleSelect.value || 'friendly';
    })().catch((err) => {
      autoStylesLoadedPromise = null;
      throw err;
    });
    return autoStylesLoadedPromise;
  }

  // 一旦用户手动编辑草稿, draftOrigin 就掉回 null.
  textarea.addEventListener('input', () => {
    if (draftOrigin === 'simuser') {
      draftOrigin = null;
    }
  });

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
    if (res.ok) {
      reflectPending(res.data?.clock);
      emit('clock:change', { clock: res.data?.clock || res.data, source: '/api/time/stage_next_turn' });
    }
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
    if (res.ok) {
      reflectPending(res.data?.clock);
      emit('clock:change', { clock: res.data?.clock || res.data, source: '/api/time/stage_next_turn' });
    }
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
    const role = roleSelect.value || 'user';

    // 2026-04-22 Day 8 #3: 空 textarea 走 pipeline 的 "只跑 LLM 回复末尾
    // user" 路径 (chat_runner `stream_send(user_content=None)`). 典型场景:
    // 用户对 user 消息点 [从此处重跑] 截断后, 直接按 Send 让 AI 对这条已
    // 有 user 生成回复, 避免产生连续两条 user 消息. 如果末尾不是 user, 后端
    // 会用 `InvalidSendState` SSE error 反馈, 前端的 'error' case 会 toast
    // 出友好提示 ("需要末尾有 user 待回复"), 不在这里本地预检.

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
    // source 取决于草稿来源: SimUser 模式生成且未被编辑 = 'simuser',
    // 否则 = 'manual'. Send 后 draftOrigin 重置 (textarea 已清空).
    const sourceTag = draftOrigin === 'simuser' ? 'simuser' : 'manual';
    // 清 textarea 提前: 用户可继续写下一条草稿 (更符合即时通讯直觉).
    textarea.value = '';
    draftOrigin = null;

    currentStream = streamPostSse('/api/chat/send', {
      content: userContent,
      role,
      source: sourceTag,
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
          case 'warning': {
            // P24 §12.5 follow-up: backend coerced a message ts forward
            // to preserve monotonicity (user rewound the virtual clock).
            // Toast is the user-visible surfacing the chokepoint design
            // was missing in Day 2 initial landing.
            //
            // 2026-04-23 r5: empty_content_ignored — 空 textarea (或只
            // 打了空白) + session.messages 末尾不是 user. 后端不走 error,
            // 走 warning + diagnostics info, 前端只提示一下别让 tester
            // 误以为系统挂了.
            const warn = ev.warning || {};
            if (warn.type === 'timestamp_coerced') {
              toast.warn(
                i18n('chat.composer.timestamp_coerced_toast'),
                { message: i18n(
                  'chat.composer.timestamp_coerced_detail',
                  warn.original_ts || '?', warn.coerced_ts || '?',
                )},
              );
            } else if (warn.type === 'empty_content_ignored') {
              toast.warn(i18n('chat.composer.empty_content_toast'));
            } else {
              console.debug('[composer] SSE warning:', warn);
            }
            break;
          }
          case 'injection_warning': {
            // 2026-04-22 Day 8 手测 #6: Chat 发送命中注入模式 — 按"检测
            // 不改, 不阻断"原则只弹 advisory toast, 不打断消息流. 用户
            // 可以在 Diagnostics → Errors 安全筛选 → [注入命中] 看详情.
            // 2026-04-22 验收反馈: toast 简化到单行, 不再挂冗长 detail —
            // 详细说明放在 Diagnostics 筛选 hint 里, toast 要轻.
            const warn = ev.warning || {};
            toast.warn(i18n('chat.composer.injection_warning_toast_fmt',
              warn.hits_count || 1));
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
    draftOrigin = null;
    emit('chat:messages_changed', { reason: 'inject' });
  }

  // ── SimUser draft generation (P11) ─────────────────────────────

  // 风格列表: 首次切到 simuser / auto 时 lazy 拉取. 拉取成功后填 styleSelect,
  // 失败则 fallback 到硬编码 ['friendly'] 保证按钮仍可用.
  //
  // 注: 用 Promise 单飞 (single-flight) 做幂等守卫, **不**用 boolean flag.
  // 原因: 若 flag 在 await 网络请求前就置 true, 紧随其后的第二次调用会命中
  // "已加载" 分支立即返回, 但此时 select 里还没填进任何 option — 下游
  // ensureAutoStylesLoaded 拷贝空的 styleSelect 就会造成 Auto 风格下拉空白
  // (触发场景: syncModeUI 里背靠背调 ensureStylesLoaded + ensureAutoStylesLoaded).
  // 改成缓存 Promise, 所有 caller await 同一个真实网络返回的 Promise, 就没这个坑.
  //
  // P24 欠账清返 (§13.5 sweep, 2026-04-22): 加 .catch 清空 Promise, 失败后
  // 下一次 caller 能重试; skill `async-lazy-init-promise-cache` 规则 3.
  let stylesLoadedPromise = null;
  function ensureStylesLoaded() {
    if (stylesLoadedPromise) return stylesLoadedPromise;
    stylesLoadedPromise = (async () => {
      const res = await api.get('/api/chat/simulate_user/styles',
        { expectedStatuses: [404] });
      const styles = res.ok && Array.isArray(res.data?.styles)
        ? res.data.styles : [{ id: 'friendly' }];
      const defaultId = res.ok ? (res.data?.default || 'friendly') : 'friendly';
      styleSelect.innerHTML = '';
      for (const s of styles) {
        const labelKey = `chat.composer.simuser.style.${s.id}`;
        const labelRaw = i18n(labelKey);
        // i18n 缺 key 时返回 key 本身; 此时退到 id.
        const label = labelRaw === labelKey ? s.id : labelRaw;
        const opt = el('option', {
          value: s.id,
          title: (s.prompt || '').slice(0, 120),
        }, label);
        styleSelect.append(opt);
      }
      styleSelect.value = defaultId;
    })().catch((err) => {
      stylesLoadedPromise = null;
      throw err;
    });
    return stylesLoadedPromise;
  }

  async function generateDraft() {
    if (generating || pending) return;
    if (modeSelect.value !== 'simuser') return;
    if (!store.session?.id) {
      toast.err(i18n('chat.composer.no_session'));
      return;
    }
    // textarea 非空 → 提示是否覆盖 (避免测试人员不小心清掉手改的草稿).
    const existing = textarea.value.trim();
    if (existing) {
      const confirmed = window.confirm(
        i18n('chat.composer.simuser.confirm_overwrite'),
      );
      if (!confirmed) return;
    }
    await ensureStylesLoaded();
    generating = true;
    syncGenerateBtn();
    const res = await api.post('/api/chat/simulate_user', {
      style: styleSelect.value || 'friendly',
      user_persona_prompt: personaTextarea.value || '',
      extra_hint: '',
    }, { expectedStatuses: [404, 409, 412, 502] });
    generating = false;
    syncGenerateBtn();
    if (!res.ok) {
      const err = res.error || {};
      toast.err(err.message || i18n('chat.composer.simuser.generate_failed'),
        { message: err.type || '' });
      return;
    }
    const data = res.data || {};
    const content = String(data.content || '');
    if (!content) {
      // SimUser 故意"沉默" 或 LLM 摆烂; warnings 已经包含详情.
      toast.ok(i18n('chat.composer.simuser.generated_empty'),
        { message: (data.warnings || []).join(' · ') });
      return;
    }
    textarea.value = content;
    draftOrigin = 'simuser';
    textarea.focus();
    // 光标放到末尾, 便于用户直接追加修改.
    try {
      textarea.setSelectionRange(content.length, content.length);
    } catch (_) { /* ignore (some browsers throw on non-text inputs) */ }
    if (data.warnings && data.warnings.length) {
      toast.ok(i18n('chat.composer.simuser.generated_ok'),
        { message: data.warnings.join(' · ') });
    }
  }

  // ── Script mode actions (P12) ──────────────────────────────────

  // P24 欠账清返 (§13.5 sweep, 2026-04-22): promise-cached single-flight.
  // `force=true` (手动 [刷新]) 清缓存重拉; `force=false` 初次加载 / mode
  // 切回 script 时用. templatesLoaded 仍存, 但只作"是否至少加载过一次"
  // 的快路径判据, 并发竞态交给 Promise cache 解决.
  let templateListPromise = null;
  async function loadTemplateList(force) {
    if (force) {
      templateListPromise = null;
      templatesLoaded = false;
    }
    if (templatesLoaded) return;
    if (templateListPromise) return templateListPromise;
    templateListPromise = (async () => {
      const res = await api.get('/api/chat/script/templates',
        { expectedStatuses: [404] });
      if (!res.ok) {
        templatesLoaded = true;
        populateTemplateSelect([]);
        return;
      }
      scriptTemplates = Array.isArray(res.data?.templates) ? res.data.templates : [];
      templatesLoaded = true;
      populateTemplateSelect(scriptTemplates);
    })().catch((err) => {
      templateListPromise = null;
      // Populate with empty list so the UI is not stuck "loading"; caller
      // decides whether to retry. templatesLoaded stays false so next
      // non-force call will retry naturally.
      populateTemplateSelect([]);
      throw err;
    });
    return templateListPromise;
  }

  function populateTemplateSelect(list) {
    const prevValue = templateSelect.value;
    templateSelect.innerHTML = '';
    templateSelect.append(el('option', {
      value: '',
    }, i18n('chat.composer.script.no_template_selected')));
    if (!list.length) {
      const opt = el('option', { value: '', disabled: true },
        i18n('chat.composer.script.templates_empty'));
      templateSelect.append(opt);
    } else {
      for (const t of list) {
        const srcLabel = t.source === 'user'
          ? i18n('chat.composer.script.source_user')
          : i18n('chat.composer.script.source_builtin');
        const overrideTag = t.overriding_builtin
          ? i18n('chat.composer.script.overriding_builtin')
          : '';
        const label = `[${srcLabel}] ${t.name} (${t.turns_count}${overrideTag})`;
        const opt = el('option', {
          value: t.name,
          title: [
            t.description
              ? `${i18n('chat.composer.script.description_prefix')}: ${t.description}`
              : '',
            t.user_persona_hint
              ? `${i18n('chat.composer.script.persona_hint_prefix')}: ${t.user_persona_hint}`
              : '',
          ].filter(Boolean).join('\n'),
        }, label);
        templateSelect.append(opt);
      }
    }
    // 若刷新前选中的模板还在新列表里, 保留选中; 否则退回占位.
    if (prevValue && list.some((t) => t.name === prevValue)) {
      templateSelect.value = prevValue;
    } else if (scriptState) {
      templateSelect.value = scriptState.template_name || '';
    } else {
      templateSelect.value = '';
    }
    syncScriptButtons();
  }

  async function refreshScriptState() {
    if (!store.session?.id) {
      scriptState = null;
      syncScriptButtons();
      return;
    }
    const res = await api.get('/api/chat/script/state',
      { expectedStatuses: [404] });
    scriptState = res.ok ? (res.data?.script_state || null) : null;
    if (scriptState && templatesLoaded) {
      templateSelect.value = scriptState.template_name || '';
    }
    syncScriptButtons();
  }

  async function loadScript() {
    if (pending || scriptRunning) return;
    if (!store.session?.id) {
      toast.err(i18n('chat.composer.script.no_session'));
      return;
    }
    const name = (templateSelect.value || '').trim();
    if (!name) {
      toast.err(i18n('chat.composer.script.no_template'));
      return;
    }
    loadBtn.disabled = true;
    loadBtn.textContent = i18n('chat.composer.script.loading');
    const res = await api.post('/api/chat/script/load', { name },
      { expectedStatuses: [404, 409, 412, 422] });
    loadBtn.textContent = i18n('chat.composer.script.load');
    if (!res.ok) {
      const err = res.error || {};
      const typeToMsg = {
        ScriptNotFound: i18n('chat.composer.script.not_found'),
        ScriptSchemaInvalid: i18n('chat.composer.script.schema_invalid'),
      };
      toast.err(typeToMsg[err.type] || err.message || i18n('chat.composer.script.load_failed'),
        { message: err.message || err.type || '' });
      syncScriptButtons();
      return;
    }
    scriptState = res.data?.script_state || null;
    const warnings = res.data?.warnings || [];
    toast.ok(
      i18n('chat.composer.script.loaded_toast', name, scriptState?.turns_count || 0),
      warnings.length ? { message: warnings.join(' · ') } : undefined,
    );
    // bootstrap 可能改了时钟, 刷新显示.
    refreshClock();
    syncScriptButtons();
  }

  async function unloadScript() {
    if (pending || scriptRunning) return;
    const res = await api.post('/api/chat/script/unload', {},
      { expectedStatuses: [404, 409] });
    if (!res.ok) return;
    scriptState = null;
    toast.ok(i18n('chat.composer.script.unloaded_toast'));
    syncScriptButtons();
  }

  /**
   * Shared SSE handler for /chat/script/next and /chat/script/run_all.
   * Reuses the message_stream handle protocol from manual /chat/send;
   * additionally updates script progress / toast on script_* events.
   */
  function openScriptStream(url) {
    if (scriptRunning) return null;
    scriptRunning = true;
    scriptCurrentAssistantHandle = null;
    syncScriptButtons();
    syncGenerateBtn();

    let userMsgPersisted = false;
    let persistedChangeEmitted = false;
    const emitPersistedChange = () => {
      if (persistedChangeEmitted || !userMsgPersisted) return;
      persistedChangeEmitted = true;
      refreshClock();
      emit('chat:messages_changed', { reason: 'script' });
    };

    const sseStream = streamPostSse(url, {}, {
      onEvent(ev) {
        switch (ev.event) {
          case 'user':
            stream.appendIncomingMessage(ev.message);
            userMsgPersisted = true;
            // 每跑完一个 user 事件就通知刷新 (脚本每轮之间可能要过几秒).
            emitPersistedChange();
            persistedChangeEmitted = false;  // 下一轮 user 时再 emit 一次.
            break;
          case 'assistant_start':
            scriptCurrentAssistantHandle = stream.beginAssistantStream({
              id: ev.message_id,
              role: 'assistant',
              content: '',
              timestamp: ev.timestamp,
              source: 'llm',
            });
            break;
          case 'delta':
            scriptCurrentAssistantHandle?.appendDelta(ev.content || '');
            break;
          case 'assistant':
            scriptCurrentAssistantHandle?.commit(ev.message);
            scriptCurrentAssistantHandle = null;
            // 如果脚本回填了 reference_content, 给个低调的 toast 提示.
            if (ev.message?.reference_content) {
              toast.ok(i18n('chat.composer.script.ref_auto_filled'));
            }
            break;
          case 'script_turn_warnings': {
            const warns = ev.warnings || [];
            if (warns.length) {
              toast.err(i18n('chat.composer.script.turn_warning_title'),
                { message: warns.join(' · ') });
            }
            break;
          }
          case 'script_turn_done':
            if (scriptState) {
              scriptState = {
                ...scriptState,
                cursor: ev.cursor,
                turns_count: ev.turns_count,
                exhausted: ev.cursor >= ev.turns_count,
              };
            }
            syncScriptButtons();
            // 该轮可能含有 warning 文案 (assistant-only 末尾场景).
            if (ev.warning) {
              toast.ok(ev.warning);
            }
            break;
          case 'script_exhausted':
            toast.ok(i18n('chat.composer.script.exhausted_toast'));
            if (scriptState) {
              scriptState = { ...scriptState, exhausted: true };
            }
            syncScriptButtons();
            break;
          case 'done':
          case 'wire_built':
          case 'usage':
            break;
          case 'warning': {
            // Same timestamp_coerced surfacing as the manual /chat/send path.
            const warn = ev.warning || {};
            if (warn.type === 'timestamp_coerced') {
              toast.warn(
                i18n('chat.composer.timestamp_coerced_toast'),
                { message: i18n(
                  'chat.composer.timestamp_coerced_detail',
                  warn.original_ts || '?', warn.coerced_ts || '?',
                )},
              );
            } else {
              console.debug('[composer] script SSE warning:', warn);
            }
            break;
          }
          case 'error': {
            const err = ev.error || {};
            toast.err(err.message || i18n('chat.composer.script.turn_failed'),
              { message: err.type || '' });
            scriptCurrentAssistantHandle?.abort();
            scriptCurrentAssistantHandle = null;
            break;
          }
          default:
            console.debug('[composer] unknown script SSE event:', ev);
        }
      },
      onError(err) {
        toast.err(i18n('chat.composer.stream_error'), { message: err.message });
        scriptCurrentAssistantHandle?.abort();
        scriptCurrentAssistantHandle = null;
        emitPersistedChange();
        scriptRunning = false;
        currentStream = null;
        refreshScriptState();
      },
      onDone() {
        emitPersistedChange();
        scriptRunning = false;
        currentStream = null;
        // 以后端状态为准再同步一次, 防止中途断掉漏 script_turn_done.
        refreshScriptState();
      },
    });
    currentStream = sseStream;
    return sseStream;
  }

  async function scriptNext() {
    if (!scriptState || scriptState.exhausted) {
      toast.err(i18n('chat.composer.script.no_template'));
      return;
    }
    openScriptStream('/api/chat/script/next');
  }

  async function scriptRunAll() {
    if (!scriptState || scriptState.exhausted) {
      toast.err(i18n('chat.composer.script.no_template'));
      return;
    }
    openScriptStream('/api/chat/script/run_all');
  }

  function scriptStop() {
    // 只有正在跑 script SSE 时才 abort; 避免误把手动 /chat/send 的 currentStream
    // 也 abort 掉 (理论上 scriptRunning=true 时同一时刻不会并发 manual send,
    // 但挡一道更安全).
    if (!scriptRunning || !currentStream) return;
    try {
      currentStream.abort();
    } catch (err) {
      console.debug('[composer] scriptStop abort failed:', err);
    }
    // 视觉上立刻反馈 — onDone() 回调里还会再清一次 scriptRunning/refreshScriptState.
    toast.ok(i18n('chat.composer.script.stop_toast'));
  }

  // ── Auto-Dialog (P13) ──────────────────────────────────────────

  function startAutoDialog() {
    if (autoRunning || pending || scriptRunning) return;
    if (!store.session?.id) {
      toast.err(i18n('chat.composer.auto.no_session'));
      return;
    }
    if (!autoBanner) {
      // 开发期兜底 — workspace_chat 正常会挂 banner. 没挂说明集成链路断了.
      toast.err('Auto-Dialog banner 未挂载, 请检查 workspace_chat 集成.');
      return;
    }
    const style = (autoStyleSelect.value || '').trim();
    if (!style) {
      toast.err(i18n('chat.composer.auto.no_style'));
      return;
    }
    const totalTurns = parseInt(autoTotalInput.value, 10);
    if (!Number.isFinite(totalTurns) || totalTurns < 1 || totalTurns > 50) {
      toast.err(i18n('chat.composer.auto.invalid_turns'));
      return;
    }
    const stepMode = autoStepModeSelect.value || 'off';
    let stepSeconds = null;
    if (stepMode === 'fixed') {
      stepSeconds = parseInt(autoStepSecondsInput.value, 10);
      if (!Number.isFinite(stepSeconds) || stepSeconds < 1 || stepSeconds > 604800) {
        toast.err(i18n('chat.composer.auto.invalid_step_seconds'));
        return;
      }
    }

    const config = {
      total_turns: totalTurns,
      simuser_style: style,
      step_mode: stepMode,
      step_seconds: stepSeconds,
      simuser_persona_hint: autoPersonaTextarea.value || '',
      simuser_extra_hint: '',
    };
    const stream = autoBanner.startAutoDialog(config);
    if (!stream) return;  // banner 已 toast 了 failure
    autoRunning = true;
    syncAutoFields();
    syncScriptButtons();
    syncGenerateBtn();
    const stepLabel = stepMode === 'fixed' ? `${stepSeconds}s` : 'off';
    toast.ok(i18n('chat.composer.auto.toast_started', totalTurns, stepLabel));
  }

  // ── lifecycle ──────────────────────────────────────────────────

  refreshClock();
  syncModeUI();

  const offSession = on('session:change', () => {
    pendingBadge.style.display = 'none';
    refreshClock();
    // 切会话时 script_state 也跟着变 (后端是"单活跃会话"模型, 但销毁/重建
    // 后 script_state 清零 — 前端同步拉一次).
    scriptState = null;
    if (modeSelect.value === 'script') {
      refreshScriptState();
    } else {
      syncScriptButtons();
    }
    // auto_state 同样会随会话销毁清零; 重置本地锁.
    if (autoRunning) {
      autoRunning = false;
      syncAutoFields();
    }
  });

  // Setup → Scripts 页保存 / 删除 / 复制 / 改名后会广播这个事件; composer
  // 的模板下拉是惰性加载 + 本地缓存的 (templatesLoaded + scriptTemplates),
  // 不刷新就会一直显示旧列表. 这里强制重拉一次:
  //   - 当前是 script 模式 → 立刻 loadTemplateList(true) 更新 <select>
  //   - 当前不是 script 模式 → 只清 templatesLoaded 让下次切到 script 时重拉
  // 避免在 manual/simuser/auto 模式下做无用功.
  const offScriptTemplates = on('scripts:templates_changed', () => {
    templatesLoaded = false;
    templateListPromise = null; // 清 Promise cache, 下次会重拉
    if (modeSelect.value === 'script') {
      loadTemplateList(true);
    }
  });

  // 外部改了时钟 (Setup → Virtual Clock 页) 也要同步显示.
  const offClock = on('clock:change', refreshClock);

  // auto_banner 结束 (正常跑完 / stop / error / 传输失败) 都会 emit 这个
  // 事件, composer 据此解锁 Start 按钮.
  const offAutoDone = on('auto_dialog:finished', () => {
    autoRunning = false;
    syncAutoFields();
    syncScriptButtons();
    syncGenerateBtn();
    // 每完成一个 auto 跑批, refreshClock 让 clock chip 反映最终时间.
    refreshClock();
  });

  return {
    focus() { textarea.focus(); },
    destroy() {
      offSession();
      offClock();
      offAutoDone();
      offScriptTemplates();
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
