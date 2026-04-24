/**
 * memory_trigger_panel.js — P10 触发操作面板 + 预览 drawer.
 *
 * 职责
 * ----
 * 每个 memory 子页 (recent / facts / reflections / persona) 在编辑器下
 * 方挂一个 "触发操作" 面板, 让测试人员手动跑记忆合成操作:
 *
 *   - recent     → recent.compress        (压缩 recent.json 尾部)
 *   - facts      → facts.extract          (从会话抽事实)
 *   - reflections → reflect               (从事实合成反思)
 *   - persona    → persona.add_fact       (加 persona 事实, 含矛盾检测)
 *                  persona.resolve_corrections (批量裁决矛盾队列)
 *
 * 流程: 触发 → Dry-run 预览 → 测试人员可编辑 payload → Accept/Cancel.
 *
 * UI 约定
 * --------
 * - 触发按钮区总在编辑器下方, 折叠后默认展开单行 ("触发操作: [按钮]...")
 * - 每次触发会在同一容器内渲染预览 drawer (hidden → 展开).
 * - Accept 调 commit API, 成功后提示 toast 并 (通过回调) 让外层刷新编辑器;
 *   Cancel 调 discard API (尽量, 失败仅 toast, 不中断 UI).
 * - 预览渲染按 op 分派 (renderPreview* family), 保持字段可编辑但结构一致.
 *
 * 设计备注: 这里刻意不复用 memory_editor_structured.js 的重载 textarea /
 * 卡片, 因为预览是只读+轻量编辑, 太复杂反而让测试人员犹豫是否该改; 用
 * 原生 input/textarea 即可.
 */

import { api } from '../../core/api.js';
import { i18n } from '../../core/i18n.js';
import { toast } from '../../core/toast.js';
import { el } from '../_dom.js';
import { openPromptPreviewModal } from '../_prompt_preview_modal.js';

// ── 每个 kind 能触发的 op 列表 ───────────────────────────────────────

/**
 * Kind → [{ op, labelKey, descKey, hasParams }] 映射.
 *
 * hasParams 决定触发时是否弹参数 (目前只有 recent.compress / persona.add_fact
 * 需要). 未来若新 op 也吃参数, 这里加一行并在 renderParamForm 里加一个 case.
 */
const OPS_BY_KIND = {
  recent: [
    { op: 'recent.compress', labelKey: 'setup.memory.trigger.recent.compress.label', hasParams: true },
  ],
  facts: [
    { op: 'facts.extract', labelKey: 'setup.memory.trigger.facts.extract.label', hasParams: true },
  ],
  reflections: [
    { op: 'reflect', labelKey: 'setup.memory.trigger.reflect.label', hasParams: true },
  ],
  persona: [
    { op: 'persona.add_fact', labelKey: 'setup.memory.trigger.persona.add_fact.label', hasParams: true },
    { op: 'persona.resolve_corrections', labelKey: 'setup.memory.trigger.persona.resolve_corrections.label', hasParams: false },
  ],
};

// ── public entry ────────────────────────────────────────────────────

/**
 * 渲染触发操作面板到 host. onCommitted(op, result) 在 commit 成功后被调用,
 * 外层 memory_editor 应当据此刷新编辑器内容 (因为 commit 修改了磁盘).
 *
 * @param {HTMLElement} host
 * @param {'recent'|'facts'|'reflections'|'persona'} kind
 * @param {{onCommitted?: (op: string, result: any) => void}} opts
 */
export function renderTriggerPanel(host, kind, opts = {}) {
  const ops = OPS_BY_KIND[kind];
  if (!ops || ops.length === 0) return;

  const root = el('section', { className: 'memory-trigger-panel' });
  root.append(
    el('h3', { className: 'memory-trigger-title' },
      i18n('setup.memory.trigger.section_title')),
    el('p', { className: 'muted tiny' },
      i18n(`setup.memory.trigger.${kind}.intro`)),
  );

  const buttonRow = el('div', { className: 'form-row memory-trigger-buttons' });
  const drawerHost = el('div', { className: 'memory-preview-drawer-host' });

  const state = {
    kind,
    activeOp: null,
    onCommitted: opts.onCommitted || (() => {}),
  };

  // r7 2nd pass (2026-04-23): 外层 buttonRow 只保留一排 op 触发按钮 —
  // [预览 prompt] 从这里挪到了参数 drawer 内部, 紧贴 [执行 (Dry-run)],
  // 用户诉求是"在点开某个 op 子菜单后再看到 preview 按钮", 因为他此刻
  // 才刚填完参数, 想先看一眼要发什么 prompt 再决定跑不跑.
  for (const spec of ops) {
    const triggerBtn = el('button', { className: 'secondary' }, i18n(spec.labelKey));
    triggerBtn.addEventListener('click', () => openParamForm(drawerHost, spec, state));
    buttonRow.append(triggerBtn);
  }

  root.append(buttonRow, drawerHost);
  host.append(root);
}

// ── P25 r7 2nd pass: Preview Prompt 合并进 Dry-run drawer ──────────
//
// 此前 [预览 prompt] 有独立入口 (外层 buttonRow 里和 triggerBtn 并排),
// 用户点它会复制一份和 Dry-run 几乎一样的参数 drawer — 两个 drawer
// 结构重复. 用户反馈更自然的交互: "点 trigger → 填参数 → 想看看要
// 发什么就按 [预览 prompt], 跑真 Dry-run 就按 [执行]", 即两个按钮
// 共用同一个 drawer / 同一份 paramsRef. 现在 previewBtn 出现在 drawer
// 底部 runBtn 旁边 (见 ``openParamForm``), 点它**不清 drawer**, 只
// 弹 modal — 用户可以预览之后微调参数再预览, 或者直接按 [执行]
// 跑真 Dry-run (复用已经填的同一份 paramsRef).
async function fetchAndShowPromptPreview(spec, params) {
  const res = await api.post(`/api/memory/prompt_preview/${spec.op}`,
    { params }, { expectedStatuses: [404, 409, 412, 422, 500] });

  if (!res.ok) {
    const detail = res.error || {};
    toast.err(
      i18n('setup.memory.trigger.preview_prompt.failed'),
      { message: `${detail.error_type || 'Error'}: ${detail.message || res.status}` },
    );
    return;
  }

  const data = res.data || {};
  const metaRows = [
    { label: i18n('setup.memory.trigger.preview_prompt.meta.op'), value: data.op || spec.op },
  ];
  if (data.note) {
    metaRows.push({
      label: i18n('setup.memory.trigger.preview_prompt.meta.note'),
      value: String(data.note),
    });
  }
  for (const [k, v] of Object.entries(data.params_echo || {})) {
    metaRows.push({ label: k, value: JSON.stringify(v) });
  }

  openPromptPreviewModal({
    title: i18n('setup.memory.trigger.preview_prompt.modal_title',
      i18n(spec.labelKey)),
    intro: i18n('setup.memory.trigger.preview_prompt.intro'),
    wireMessages: data.wire_messages || [],
    metaRows,
    warnings: data.warnings || [],
  });
}


// ── 参数采集表单 (触发前) ──────────────────────────────────────────

/**
 * Step 1: 渲染 op 的参数 drawer. 底部按钮行包含
 * ``[执行 (Dry-run)] [预览 prompt] [取消]`` 三按钮 (其中 [预览 prompt]
 * 只对 ``persona.add_fact`` 以外的 op 挂 — add_fact 在 preview 阶段
 * 不调 LLM, 后端 ``NoPromptForOp`` 422). 无参 op (persona.resolve_corrections)
 * 也走这个 drawer, fields 区域显示 "此操作无可配置参数".
 *
 * 重要交互: [预览 prompt] 点击**不清 drawer**, 只弹 modal; 这样用户
 * 可以预览 → 微调参数 → 再预览 → 真跑 Dry-run, 全在一个 drawer 里.
 */
function openParamForm(drawerHost, spec, state) {
  drawerHost.innerHTML = '';
  state.activeOp = spec.op;

  const form = el('div', { className: 'memory-preview-drawer' });
  form.append(
    el('div', { className: 'memory-preview-header' },
      el('strong', {}, i18n('setup.memory.trigger.params_title', i18n(spec.labelKey))),
      el('button', { className: 'ghost memory-preview-close', type: 'button' },
        i18n('setup.memory.trigger.close')),
    ),
  );
  const closeBtn = form.querySelector('.memory-preview-close');
  closeBtn.addEventListener('click', () => { drawerHost.innerHTML = ''; });

  const fields = el('div', { className: 'memory-preview-fields' });
  const paramsRef = { value: {} };

  if (spec.hasParams) {
    switch (spec.op) {
      case 'recent.compress':
        renderRecentCompressParams(fields, paramsRef);
        break;
      case 'facts.extract':
        renderFactsExtractParams(fields, paramsRef);
        break;
      case 'reflect':
        renderReflectParams(fields, paramsRef);
        break;
      case 'persona.add_fact':
        renderPersonaAddFactParams(fields, paramsRef);
        break;
      default:
        fields.append(el('p', { className: 'muted' },
          i18n('setup.memory.trigger.no_params')));
    }
  } else {
    fields.append(el('p', { className: 'muted' },
      i18n('setup.memory.trigger.no_params')));
  }

  const runBtn = el('button', { className: 'primary' },
    i18n('setup.memory.trigger.run_button'));
  const cancelBtn = el('button', {}, i18n('setup.memory.trigger.cancel'));

  runBtn.addEventListener('click', () => {
    triggerAndShowPreview(drawerHost, spec, paramsRef.value, state);
  });
  cancelBtn.addEventListener('click', () => { drawerHost.innerHTML = ''; });

  // 按钮行: [执行 (Dry-run)] [预览 prompt] [取消].
  // persona.add_fact 的 preview 阶段不调 LLM (只做启发式矛盾评估),
  // 没 prompt 可预览; 所以仅对其它 4 个 op 挂 [预览 prompt].
  const actions = el('div', { className: 'form-row' }, runBtn);
  if (spec.op !== 'persona.add_fact') {
    const previewBtn = el('button', {
      type: 'button',
      className: 'ghost memory-trigger-preview-btn',
      title: i18n('setup.memory.trigger.preview_prompt.tooltip'),
    }, i18n('setup.memory.trigger.preview_prompt.label'));
    previewBtn.addEventListener('click',
      () => fetchAndShowPromptPreview(spec, paramsRef.value));
    actions.append(previewBtn);
  }
  actions.append(cancelBtn);

  form.append(fields, actions);
  drawerHost.append(form);
}

// ── Step 2: 调 trigger API, 拿 preview, 渲染编辑区 ─────────────────

async function triggerAndShowPreview(drawerHost, spec, params, state) {
  drawerHost.innerHTML = '';
  const loading = el('div', { className: 'memory-preview-drawer muted' },
    i18n('setup.memory.trigger.running'));
  drawerHost.append(loading);

  // 409 = 业务预期的"无可用输入" (recent 空 / facts 不够); 412 = 模型未配置;
  // 502 = LLM 失败 — 这些都要把 error_type + message 渲染到 drawer 里, 不弹 toast.
  const res = await api.post(`/api/memory/trigger/${spec.op}`, { params },
    { expectedStatuses: [404, 409, 412, 422, 500, 502] });
  drawerHost.innerHTML = '';

  if (!res.ok) {
    const detail = res.error || {};
    drawerHost.append(renderErrorDrawer(spec, detail, res.status));
    return;
  }

  renderPreviewDrawer(drawerHost, spec, res.data, state);
}

function renderErrorDrawer(spec, detail, status) {
  const drawer = el('div', { className: 'memory-preview-drawer err' });
  drawer.append(
    el('div', { className: 'memory-preview-header' },
      el('strong', {}, i18n('setup.memory.trigger.failed_title', i18n(spec.labelKey))),
    ),
    el('p', { className: 'memory-preview-error-type' },
      `${detail.error_type || 'UnknownError'} (HTTP ${status})`),
    el('p', {}, detail.message || i18n('errors.unknown')),
  );
  return drawer;
}

// ── Step 3: 预览 drawer 渲染 (op-specific) + Accept/Cancel ───────

function renderPreviewDrawer(drawerHost, spec, preview, state) {
  const { op, payload = {}, warnings = [], params = {} } = preview;
  const drawer = el('div', { className: 'memory-preview-drawer' });

  drawer.append(
    el('div', { className: 'memory-preview-header' },
      el('strong', {}, i18n('setup.memory.trigger.preview_title', i18n(spec.labelKey))),
      el('span', { className: 'badge primary' }, op),
    ),
  );

  // Warnings list (soft; always show if non-empty so tester has full context).
  if (warnings.length) {
    const warnBox = el('div', { className: 'memory-preview-warnings' });
    for (const w of warnings) {
      warnBox.append(el('div', { className: 'memory-preview-warn-row' }, `· ${w}`));
    }
    drawer.append(warnBox);
  }

  // Echo params for audit (read-only tiny chip row).
  if (Object.keys(params).length) {
    const chips = el('div', { className: 'memory-preview-params' });
    for (const [k, v] of Object.entries(params)) {
      chips.append(el('span', { className: 'badge secondary' },
        `${k}: ${JSON.stringify(v)}`));
    }
    drawer.append(chips);
  }

  const editHost = el('div', { className: 'memory-preview-edit' });
  drawer.append(editHost);

  // editsRef.value is the mutable edits payload we send to /commit.
  // Each renderer writes to editsRef.value.<field> on user input.
  const editsRef = { value: {} };

  switch (op) {
    case 'recent.compress':
      renderRecentCompressPreview(editHost, payload, editsRef);
      break;
    case 'facts.extract':
      renderFactsExtractPreview(editHost, payload, editsRef);
      break;
    case 'reflect':
      renderReflectPreview(editHost, payload, editsRef);
      break;
    case 'persona.add_fact':
      renderPersonaAddFactPreview(editHost, payload, editsRef);
      break;
    case 'persona.resolve_corrections':
      renderResolveCorrectionsPreview(editHost, payload, editsRef);
      break;
    default:
      editHost.append(el('pre', { className: 'memory-preview-raw' },
        JSON.stringify(payload, null, 2)));
  }

  const acceptBtn = el('button', { className: 'primary' },
    i18n('setup.memory.trigger.accept'));
  const cancelBtn = el('button', {}, i18n('setup.memory.trigger.reject'));
  const statusLine = el('div', { className: 'muted tiny' });

  acceptBtn.addEventListener('click', async () => {
    acceptBtn.disabled = true;
    cancelBtn.disabled = true;
    statusLine.textContent = i18n('setup.memory.trigger.committing');
    const cres = await api.post(`/api/memory/commit/${op}`, { edits: editsRef.value },
      { expectedStatuses: [404, 409, 412, 422, 500, 502] });
    if (!cres.ok) {
      statusLine.textContent = `${cres.error?.error_type || 'Error'}: ${cres.error?.message || cres.status}`;
      acceptBtn.disabled = false;
      cancelBtn.disabled = false;
      return;
    }
    toast.ok(i18n('setup.memory.trigger.committed', i18n(spec.labelKey)));
    drawerHost.innerHTML = '';
    try { state.onCommitted(op, cres.data); } catch { /* best-effort */ }
  });

  cancelBtn.addEventListener('click', async () => {
    // Best-effort discard so the cache is cleared even if the user just
    // closed the drawer without committing. Doesn't block the UI.
    await api.post(`/api/memory/discard/${op}`, {}, { expectedStatuses: [404, 409] });
    drawerHost.innerHTML = '';
  });

  drawer.append(
    el('div', { className: 'form-row memory-preview-actions' },
      acceptBtn, cancelBtn,
    ),
    statusLine,
  );
  drawerHost.append(drawer);
}

// ── param form renderers ────────────────────────────────────────────

function renderRecentCompressParams(host, paramsRef) {
  host.append(
    kvRow(i18n('setup.memory.trigger.recent.params.tail_count'),
      intInput('', v => { paramsRef.value.tail_count = v; }, { min: 1, placeholder: i18n('setup.memory.trigger.recent.params.tail_count_ph') }),
      i18n('setup.memory.trigger.recent.params.tail_count_help')),
    kvCheckboxInlineRow(
      i18n('setup.memory.trigger.recent.params.detailed'),
      checkbox(false, v => { paramsRef.value.detailed = v; }),
      i18n('setup.memory.trigger.recent.params.detailed_help')),
  );
}

function renderFactsExtractParams(host, paramsRef) {
  paramsRef.value.source = 'session.messages';
  paramsRef.value.min_importance = 5;
  host.append(
    kvRow(i18n('setup.memory.trigger.facts.params.source'),
      selectBox('session.messages', [
        { value: 'session.messages', label: i18n('setup.memory.trigger.facts.params.source_session') },
        { value: 'recent.json', label: i18n('setup.memory.trigger.facts.params.source_recent') },
      ], v => { paramsRef.value.source = v; }),
      i18n('setup.memory.trigger.facts.params.source_help')),
    kvRow(i18n('setup.memory.trigger.facts.params.min_importance'),
      intInput('5', v => { paramsRef.value.min_importance = v; }, { min: 0, max: 10 }),
      i18n('setup.memory.trigger.facts.params.min_importance_help')),
  );
}

function renderReflectParams(host, paramsRef) {
  host.append(
    kvRow(i18n('setup.memory.trigger.reflect.params.min_facts'),
      intInput('', v => { paramsRef.value.min_facts = v; },
        { min: 1, placeholder: '5' }),
      i18n('setup.memory.trigger.reflect.params.min_facts_help')),
  );
}

function renderPersonaAddFactParams(host, paramsRef) {
  paramsRef.value.entity = 'master';
  host.append(
    kvRow(i18n('setup.memory.trigger.persona.params.text'),
      textareaBox('', v => { paramsRef.value.text = v; }, { rows: 2 })),
    kvRow(i18n('setup.memory.trigger.persona.params.entity'),
      entityInput('master', v => { paramsRef.value.entity = v; })),
  );
}

// ── preview renderers ──────────────────────────────────────────────

function renderRecentCompressPreview(host, payload, editsRef) {
  editsRef.value.memo_system_content = payload.memo_system_content ?? '';

  host.append(
    el('div', { className: 'memory-preview-stat-row' },
      stat(i18n('setup.memory.trigger.recent.stats.total_before'), payload.total_before ?? 0),
      stat(i18n('setup.memory.trigger.recent.stats.tail_count'), payload.tail_count ?? 0),
      stat(i18n('setup.memory.trigger.recent.stats.kept_count'), payload.kept_count ?? 0),
      stat(i18n('setup.memory.trigger.recent.stats.total_after'), payload.total_after ?? 0),
    ),
    kvRow(
      i18n('setup.memory.trigger.recent.preview.memo'),
      textareaBox(editsRef.value.memo_system_content,
        v => { editsRef.value.memo_system_content = v; },
        { rows: 6 }),
      i18n('setup.memory.trigger.recent.preview.memo_help'),
    ),
  );

  if (payload.summary && payload.summary !== payload.memo_system_content) {
    host.append(kvRow(
      i18n('setup.memory.trigger.recent.preview.raw_summary'),
      readonlyTextarea(payload.summary),
      i18n('setup.memory.trigger.recent.preview.raw_summary_help'),
    ));
  }
}

function renderFactsExtractPreview(host, payload, editsRef) {
  const extracted = Array.isArray(payload.extracted) ? payload.extracted.slice() : [];
  editsRef.value.extracted = extracted;

  host.append(
    el('div', { className: 'memory-preview-stat-row' },
      stat(i18n('setup.memory.trigger.facts.stats.message_count'), payload.message_count ?? 0),
      stat(i18n('setup.memory.trigger.facts.stats.extracted_count'), extracted.length),
      stat(i18n('setup.memory.trigger.facts.stats.total_existing'), payload.total_existing ?? 0),
    ),
  );

  if (extracted.length === 0) {
    host.append(el('p', { className: 'muted' },
      i18n('setup.memory.trigger.facts.preview.empty')));
    return;
  }

  const list = el('div', { className: 'memory-preview-list' });
  for (let i = 0; i < extracted.length; i++) {
    list.append(buildFactEditRow(extracted, i, list));
  }
  host.append(list);
}

function buildFactEditRow(arr, idx, list) {
  const f = arr[idx];
  const row = el('div', { className: 'memory-preview-item memory-item-card' });
  row.append(
    kvRow(i18n('setup.memory.trigger.facts.fields.text'),
      textareaBox(f.text ?? '', v => { f.text = v; }, { rows: 2 })),
    el('div', { className: 'form-row' },
      kvRow(i18n('setup.memory.trigger.facts.fields.entity'),
        entityInput(f.entity ?? 'master', v => { f.entity = v; })),
      kvRow(i18n('setup.memory.trigger.facts.fields.importance'),
        intInput(String(f.importance ?? 5), v => { f.importance = v; }, { min: 0, max: 10 })),
      kvRow(i18n('setup.memory.trigger.facts.fields.tags'),
        tagsInput(f.tags ?? [], v => { f.tags = v; })),
    ),
    el('button', {
      className: 'ghost memory-preview-drop',
      type: 'button',
    }, i18n('setup.memory.trigger.drop_item')),
  );
  row.querySelector('.memory-preview-drop').addEventListener('click', () => {
    arr.splice(idx, 1);
    // Re-render whole list — indices shift after splice.
    const fresh = el('div', { className: 'memory-preview-list' });
    for (let i = 0; i < arr.length; i++) {
      fresh.append(buildFactEditRow(arr, i, fresh));
    }
    list.replaceWith(fresh);
  });
  return row;
}

function renderReflectPreview(host, payload, editsRef) {
  const reflection = { ...(payload.reflection || {}) };
  editsRef.value.reflection = reflection;
  const sourceFacts = Array.isArray(payload.source_facts) ? payload.source_facts : [];

  host.append(
    el('div', { className: 'memory-preview-stat-row' },
      stat(i18n('setup.memory.trigger.reflect.stats.unabsorbed'), payload.unabsorbed_count ?? 0),
      stat(i18n('setup.memory.trigger.reflect.stats.source_count'),
        (reflection.source_fact_ids || []).length),
    ),
    kvRow(i18n('setup.memory.trigger.reflect.fields.text'),
      textareaBox(reflection.text ?? '', v => { reflection.text = v; }, { rows: 4 })),
    kvRow(i18n('setup.memory.trigger.reflect.fields.entity'),
      selectBox(reflection.entity || 'relationship', [
        { value: 'master', label: i18n('setup.memory.trigger.reflect.fields.entity_master') },
        { value: 'neko', label: i18n('setup.memory.trigger.reflect.fields.entity_neko') },
        { value: 'relationship', label: i18n('setup.memory.trigger.reflect.fields.entity_relationship') },
      ], v => { reflection.entity = v; })),
  );

  if (sourceFacts.length) {
    const details = el('details', { className: 'memory-preview-details' });
    details.append(el('summary', {},
      i18n('setup.memory.trigger.reflect.source_facts_title', sourceFacts.length)));
    for (const f of sourceFacts) {
      details.append(el('div', { className: 'memory-preview-readonly-row' },
        `· [${f.entity || '?'}] ${f.text || ''} (importance=${f.importance ?? '-'})`));
    }
    host.append(details);
  }
}

function renderPersonaAddFactPreview(host, payload, editsRef) {
  editsRef.value.text = payload.text ?? '';
  editsRef.value.entity = payload.entity ?? 'master';

  const code = payload.code || 'added';
  const codeBadge = el('span', {
    className: `badge ${code === 'added' ? 'primary' : code === 'rejected_card' ? 'err' : 'warn'}`,
  }, i18n(`setup.memory.trigger.persona.code.${code}`));

  host.append(
    el('div', { className: 'memory-preview-stat-row' },
      el('div', { className: 'memory-preview-code-wrap' },
        el('span', { className: 'muted tiny' },
          i18n('setup.memory.trigger.persona.preview.code_label')),
        codeBadge,
      ),
      stat(i18n('setup.memory.trigger.persona.preview.existing_count'),
        payload.existing_count ?? 0),
    ),
  );

  if (payload.conflicting_text) {
    host.append(kvRow(
      i18n('setup.memory.trigger.persona.preview.conflicting'),
      readonlyTextarea(payload.conflicting_text),
      i18n('setup.memory.trigger.persona.preview.conflicting_help'),
    ));
  }

  host.append(
    kvRow(i18n('setup.memory.trigger.persona.preview.text'),
      textareaBox(editsRef.value.text,
        v => { editsRef.value.text = v; }, { rows: 2 }),
      i18n('setup.memory.trigger.persona.preview.text_help')),
    kvRow(i18n('setup.memory.trigger.persona.preview.entity'),
      entityInput(editsRef.value.entity,
        v => { editsRef.value.entity = v; })),
  );

  if (Array.isArray(payload.section_preview) && payload.section_preview.length) {
    const details = el('details', { className: 'memory-preview-details' });
    details.append(el('summary', {},
      i18n('setup.memory.trigger.persona.preview.section_preview_title',
        payload.section_preview.length)));
    for (const t of payload.section_preview) {
      details.append(el('div', { className: 'memory-preview-readonly-row' }, `· ${t}`));
    }
    host.append(details);
  }
}

function renderResolveCorrectionsPreview(host, payload, editsRef) {
  const actions = Array.isArray(payload.actions) ? payload.actions.slice() : [];
  editsRef.value.actions = actions;

  host.append(
    el('div', { className: 'memory-preview-stat-row' },
      stat(i18n('setup.memory.trigger.resolve.stats.queue_size'), payload.queue_size ?? 0),
      stat(i18n('setup.memory.trigger.resolve.stats.action_count'), actions.length),
    ),
  );

  if (actions.length === 0) {
    host.append(el('p', { className: 'muted' },
      i18n('setup.memory.trigger.resolve.empty')));
    return;
  }

  const list = el('div', { className: 'memory-preview-list' });
  for (const act of actions) {
    const row = el('div', { className: 'memory-preview-item memory-item-card' });
    row.append(
      el('div', { className: 'memory-preview-stat-row' },
        el('span', { className: 'badge secondary' }, `#${act.index}`),
        el('span', { className: 'badge secondary' }, `entity=${act.entity || 'master'}`),
      ),
      kvRow(i18n('setup.memory.trigger.resolve.fields.old_text'),
        readonlyTextarea(act.old_text || '')),
      kvRow(i18n('setup.memory.trigger.resolve.fields.new_text'),
        readonlyTextarea(act.new_text || '')),
      kvRow(i18n('setup.memory.trigger.resolve.fields.action'),
        selectBox(act.action || 'keep_both', [
          { value: 'replace', label: i18n('setup.memory.trigger.resolve.action.replace') },
          { value: 'keep_new', label: i18n('setup.memory.trigger.resolve.action.keep_new') },
          { value: 'keep_old', label: i18n('setup.memory.trigger.resolve.action.keep_old') },
          { value: 'keep_both', label: i18n('setup.memory.trigger.resolve.action.keep_both') },
        ], v => { act.action = v; })),
      kvRow(i18n('setup.memory.trigger.resolve.fields.merged_text'),
        textareaBox(act.text || '', v => { act.text = v; }, { rows: 2 }),
        i18n('setup.memory.trigger.resolve.fields.merged_text_help')),
    );
    list.append(row);
  }
  host.append(list);
}

// ── tiny form helpers ───────────────────────────────────────────────

function kvRow(labelText, control, helpText) {
  const row = el('div', { className: 'memory-preview-field' });
  row.append(el('label', {}, labelText));
  row.append(control);
  if (helpText) row.append(el('div', { className: 'muted tiny' }, helpText));
  return row;
}

/**
 * One row: label text immediately left of checkbox (same baseline as
 * ``memory_editor_structured.inlineField`` but label-first — tester asked
 * for 勾在文案右侧, not a lone checkbox centered on a second row under
 * a column ``.memory-preview-field`` layout).
 */
function kvCheckboxInlineRow(labelText, checkboxEl, helpText) {
  const row = el('div', { className: 'memory-preview-field memory-preview-field--checkbox-inline' });
  row.append(
    el('label', { className: 'memory-field-inline' },
      el('span', {}, labelText),
      checkboxEl,
    ),
  );
  if (helpText) row.append(el('div', { className: 'muted tiny' }, helpText));
  return row;
}

function textareaBox(value, onChange, { rows = 3 } = {}) {
  const t = el('textarea', { rows, spellcheck: false, value: String(value ?? '') });
  t.addEventListener('input', () => onChange(t.value));
  return t;
}

function readonlyTextarea(value) {
  const t = el('textarea', {
    rows: 2, spellcheck: false, readOnly: true, value: String(value ?? ''),
  });
  return t;
}

function intInput(value, onChange, { min, max, placeholder } = {}) {
  const props = { type: 'number', value: value ?? '' };
  if (placeholder) props.placeholder = placeholder;
  if (min != null) props.min = String(min);
  if (max != null) props.max = String(max);
  const input = el('input', props);
  input.addEventListener('input', () => {
    const s = input.value.trim();
    onChange(s === '' ? undefined : Number(s));
  });
  return input;
}

function checkbox(value, onChange) {
  const c = el('input', { type: 'checkbox', checked: !!value });
  c.addEventListener('change', () => onChange(c.checked));
  return c;
}

function selectBox(value, options, onChange) {
  const s = el('select', {});
  for (const o of options) {
    const opt = el('option', { value: o.value }, o.label);
    if (o.value === value) opt.selected = true;
    s.append(opt);
  }
  s.addEventListener('change', () => onChange(s.value));
  return s;
}

function entityInput(value, onChange) {
  // Persona 管道允许任何 entity 字符串 (master / neko / relationship / 群号 / 任意键),
  // 所以这里用文本框 + datalist 给建议而不是硬 select.
  const id = `entity-list-${Math.random().toString(36).slice(2, 8)}`;
  const input = el('input', { type: 'text', value: value ?? 'master' });
  input.setAttribute('list', id);
  input.addEventListener('input', () => onChange(input.value));
  const list = el('datalist', { id });
  for (const v of ['master', 'neko', 'relationship']) {
    list.append(el('option', { value: v }));
  }
  const wrap = el('span', { className: 'memory-entity-input' }, input, list);
  return wrap;
}

function tagsInput(value, onChange) {
  const joined = Array.isArray(value) ? value.join(', ') : String(value ?? '');
  const input = el('input', { type: 'text', value: joined });
  input.addEventListener('input', () => {
    const arr = input.value.split(',').map(s => s.trim()).filter(Boolean);
    onChange(arr);
  });
  return input;
}

function stat(label, value) {
  return el('div', { className: 'memory-preview-stat' },
    el('span', { className: 'muted tiny' }, label),
    el('span', { className: 'memory-preview-stat-value' }, String(value)),
  );
}
