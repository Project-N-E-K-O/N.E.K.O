/**
 * page_schemas.js — Evaluation → Schemas 子页 (P15).
 *
 * 作用: 在浏览器里阅览 / 复制 / 编辑 / 新建 / 删除 / 导出 / 预览 scoring
 * schema, 不再需要手改 JSON 文件.  ScoringSchema 是 P15 起的一等公民,
 * 驱动 P16 的四类 Judger (Absolute/Comparative × Single/Conversation).
 *
 * 布局沿用 Setup → Scripts 子页 (page_scripts.js) 的模式: 左列 user + builtin
 * 分组列表, 右列头部 + 基本信息 + mode/granularity + 维度 + 锚点 + penalty +
 * 公式 + prompt 模板 + tags + errors.  所有交互 "先拉详情, 本地 draft, 校验
 * 后保存" 的单向流.
 *
 * 关键决策 (对齐 Setup/Scripts):
 *   1. builtin schema 不可原地编辑, 只能 [复制为可编辑] 生成 user 副本再改.
 *   2. 同 id 的 user 覆盖 builtin, UI 加 "覆盖 builtin" 徽章.
 *   3. id 即文件名, 改 id = Save As (走 "save 新 id + delete 旧 user id").
 *   4. 校验只发生在 [保存] 时: 前端不独立暴露 [校验] 按钮 (冗余), 直接走
 *      POST /schemas, 422 带 errors 清单 → 红框高亮到对应字段/维度. 这个决策
 *      和 P12.5 Scripts 子页最后砍掉冗余 [校验] 按钮同款思路, 避免两条校验
 *      入口行为不一致的维护负担.
 *   5. Preview prompt 开一个 modal, 展示 render_prompt 输出 + 字符数 +
 *      used/missing placeholders.
 *   6. 维度卡用 foldable-card 折叠: 折叠态只显 `#N 显示名 + key/weight/锚点
 *      数徽章 + 上下移/删按钮`, 展开态显完整表单. 默认折叠, 有错或全空时
 *      自动展开. 持久化到 localStorage `fold:_global:eval:schema:<id>:dim:<idx>`.
 *   7. 本页**不跑评分**, 只管 schema. Run 子页是 P16 的事.
 */

import { i18n } from '../../core/i18n.js';
import { api } from '../../core/api.js';
import { toast } from '../../core/toast.js';
import { el, field } from '../_dom.js';

const API_BASE = '/api/judge/schemas';
const VALID_MODES = ['absolute', 'comparative'];
const VALID_GRANULARITY = ['single', 'conversation'];

// ── 轻量可折叠卡片 (本页专用) ────────────────────────────────
//
// 核心 core/collapsible.js 的 createCollapsible 不太合适: 它假设内容是静态文本
// 或已构造好的 DOM + 提供 "预览摘要 / 字符数徽章 / 复制" 这类偏 read-only 的
// 功能. 维度卡是"有一堆输入框 + 上下移/删按钮在标题旁" 的编辑态, 需要:
//   1. 标题栏里能挤操作按钮 (move up/down/remove), 且点击按钮不触发折叠
//   2. 有错误时强制展开, 让用户一眼看到红框
//   3. 状态用 localStorage 持久化 (沿用 fold:<scope>:<blockId> key 的约定)
//
// makeFoldableCard 专门解决这三点. 如果未来别处也要类似编辑态折叠, 可抽到
// core/collapsible.js 做 createEditableCollapsible, 但目前只有这一家调用者,
// 就地实现更直接, 避免过度工程.
function readFoldOpen(blockId, fallback) {
  try {
    const raw = localStorage.getItem(`fold:_global:${blockId}`);
    if (raw === '1') return true;
    if (raw === '0') return false;
  } catch (_) { /* noop */ }
  return fallback;
}
function writeFoldOpen(blockId, open) {
  try {
    localStorage.setItem(`fold:_global:${blockId}`, open ? '1' : '0');
  } catch (_) { /* noop */ }
}

/**
 * 构造一张可折叠的编辑态卡片.
 *
 * @param {object} o
 * @param {string} o.blockId                         持久化 key 用的唯一 id
 * @param {boolean} [o.defaultCollapsed=true]        首次访问是否默认折叠
 * @param {boolean} [o.forceOpen=false]              为 true 时覆盖 LS 永远展开 (有错时用)
 * @param {HTMLElement|string} o.title               标题 (主文本)
 * @param {Array<HTMLElement|string>} [o.badges]     标题后小徽章 (可为空)
 * @param {Array<HTMLElement>} [o.headerActions]     右侧操作按钮 (点击不触发折叠)
 * @param {HTMLElement} o.body                       展开后显示的主体 DOM
 * @param {boolean} [o.hasError=false]               true 时给卡片加红框
 * @returns {HTMLElement}
 */
function makeFoldableCard(o) {
  const {
    blockId, title, body,
    badges = [], headerActions = [],
    defaultCollapsed = true,
    forceOpen = false,
    hasError = false,
  } = o;
  const persisted = readFoldOpen(blockId, !defaultCollapsed);
  let open = forceOpen || persisted;

  const root = el('div', {
    className: 'foldable-card' + (hasError ? ' has-error' : ''),
  });
  root.dataset.open = String(open);

  const caret = el('span', { className: 'foldable-card-caret' }, '▸');
  const titleEl = el('span', { className: 'foldable-card-title' },
    title instanceof HTMLElement ? title : String(title));
  const badgeWrap = el('span', { className: 'foldable-card-badges' },
    ...badges.map((b) => (b instanceof HTMLElement
      ? b
      : el('span', { className: 'badge' }, String(b)))));
  const actionsWrap = el('span', {
    className: 'foldable-card-actions',
    onClick: (ev) => ev.stopPropagation(),
  }, ...headerActions);

  const header = el('div', {
    className: 'foldable-card-header',
    role: 'button',
    tabindex: '0',
    onClick: toggle,
    onKeyDown: (ev) => {
      if (ev.key === 'Enter' || ev.key === ' ') {
        ev.preventDefault();
        toggle();
      }
    },
  }, caret, titleEl, badgeWrap, actionsWrap);

  const bodyWrap = el('div', { className: 'foldable-card-body' }, body);
  root.append(header, bodyWrap);

  function toggle() {
    open = !open;
    root.dataset.open = String(open);
    // 持久化只记用户显式点击行为; forceOpen 驱动的展开不写 LS, 避免 "有错时
    // 一展开就永久记忆为展开".
    if (!forceOpen) writeFoldOpen(blockId, open);
  }
  return root;
}

// ── 页级状态 ──────────────────────────────────────────────────────

function makeState() {
  return {
    schemas: [],          // meta 列表 (从 GET /schemas)
    selectedId: null,     // 当前选中的 schema id
    loadingDetails: false,
    details: null,        // { active, has_builtin, has_user, overriding_builtin }
    draft: null,          // 可变 form 状态
    baseline: null,       // 最近一次服务器同步后的 draft 副本, Reload 回到这里
    errors: [],           // 字段级 errors, save/validate 后填充
  };
}

export async function renderSchemasPage(host) {
  host.innerHTML = '';
  const { openFolderButton } = await import('../_open_folder_btn.js');
  const header = el('div', {
    style: { display: 'flex', alignItems: 'baseline', gap: '12px', justifyContent: 'space-between' },
  });
  header.append(
    el('h2', { style: { margin: 0 } }, i18n('evaluation.schemas.heading')),
    openFolderButton('user_schemas'),
  );
  host.append(
    header,
    el('p', { className: 'intro' }, i18n('evaluation.schemas.intro')),
  );

  const state = makeState();
  const pageRoot = el('div', { className: 'script-editor-root' });
  host.append(pageRoot);

  async function refreshList() {
    const res = await api.get(API_BASE);
    if (!res.ok) {
      state.schemas = [];
      toast.err(i18n('evaluation.schemas.toast.list_failed'),
        { message: String(res.error || res.status) });
    } else {
      state.schemas = res.data?.schemas || [];
    }
    rerender();
  }

  async function loadDetails(id) {
    state.selectedId = id;
    state.loadingDetails = true;
    state.errors = [];
    rerender();
    const res = await api.get(`${API_BASE}/${encodeURIComponent(id)}`);
    state.loadingDetails = false;
    if (!res.ok) {
      state.details = null;
      state.draft = null;
      state.baseline = null;
      toast.err(i18n('evaluation.schemas.toast.load_failed'),
        { message: String(res.error || res.status) });
      rerender();
      return;
    }
    state.details = {
      active: res.data.active,
      has_builtin: !!res.data.has_builtin,
      has_user: !!res.data.has_user,
      overriding_builtin: !!res.data.overriding_builtin,
    };
    state.draft = schemaToDraft(res.data.active);
    state.baseline = schemaToDraft(res.data.active);
    rerender();
  }

  function rerender() {
    pageRoot.innerHTML = '';
    pageRoot.append(
      renderToolbar(state, {
        refreshList,
        onNewBlank: () => newBlank(state, rerender),
        onImport: () => importFromFile(state, { refreshList, loadDetails }),
      }),
      renderBody(state, { loadDetails, refreshList, rerender }),
    );
  }

  await refreshList();
}

// ── 顶部工具栏 ──────────────────────────────────────────────────

function renderToolbar(state, { refreshList, onNewBlank, onImport }) {
  return el('div', { className: 'script-editor-topbar' },
    el('button', { onClick: () => refreshList() },
      i18n('evaluation.schemas.buttons.refresh_list')),
    el('button', { className: 'primary', onClick: onNewBlank },
      i18n('evaluation.schemas.buttons.new_blank')),
    el('button', { onClick: onImport },
      i18n('evaluation.schemas.buttons.import')),
    el('span', { className: 'script-editor-count' },
      i18n('evaluation.schemas.list.count_fmt', state.schemas.length)),
  );
}

function newBlank(state, rerender) {
  const suggested = suggestUniqueId(state.schemas, 'my_schema');
  const id = (window.prompt(i18n('evaluation.schemas.prompt.new_id'), suggested) || '').trim();
  if (!id) return;
  if (state.schemas.some((s) => s.id === id && s.source === 'user')) {
    toast.warn(i18n('evaluation.schemas.toast.id_taken', id));
    return;
  }
  state.selectedId = id;
  state.details = {
    active: null,
    has_builtin: false,
    has_user: false,
    overriding_builtin: false,
  };
  state.draft = emptyDraft(id);
  state.baseline = null;
  state.errors = [];
  rerender();
}

function suggestUniqueId(schemas, base) {
  const taken = new Set(schemas.map((s) => s.id));
  if (!taken.has(base)) return base;
  for (let i = 2; i < 1000; i++) {
    const candidate = `${base}_${i}`;
    if (!taken.has(candidate)) return candidate;
  }
  return `${base}_${Date.now()}`;
}

async function importFromFile(state, { refreshList, loadDetails }) {
  const input = document.createElement('input');
  input.type = 'file';
  input.accept = '.json,application/json';
  input.onchange = async () => {
    const file = input.files?.[0];
    if (!file) return;
    try {
      const text = await file.text();
      const payload = JSON.parse(text);
      if (!payload || typeof payload !== 'object') {
        toast.err(i18n('evaluation.schemas.toast.import_failed'),
          { message: 'JSON root must be object' });
        return;
      }
      const res = await api.post(`${API_BASE}/import`, payload);
      if (!res.ok) {
        if (Array.isArray(res.error?.errors) && res.error.errors.length > 0) {
          state.errors = res.error.errors;
          toast.err(i18n('evaluation.schemas.toast.save_errors', state.errors.length));
        } else {
          toast.err(i18n('evaluation.schemas.toast.import_failed'),
            { message: res.error?.message || `HTTP ${res.status}` });
        }
        return;
      }
      toast.ok(i18n('evaluation.schemas.toast.imported', res.data.schema.id));
      await refreshList();
      await loadDetails(res.data.schema.id);
    } catch (exc) {
      toast.err(i18n('evaluation.schemas.toast.import_failed'),
        { message: exc?.message || String(exc) });
    }
  };
  input.click();
}

// ── 主体 = 左列表 + 右编辑器 ──────────────────────────────────

function renderBody(state, ctx) {
  return el('div', { className: 'script-editor-body' },
    renderList(state, ctx),
    renderEditor(state, ctx),
  );
}

// ── 左列: schema 列表 ────────────────────────────────────────

function renderList(state, ctx) {
  const list = el('div', { className: 'script-editor-list' });

  const userSchemas = state.schemas.filter((s) => s.source === 'user');
  const builtinSchemas = state.schemas.filter((s) => s.source === 'builtin');

  if (userSchemas.length > 0) {
    list.append(el('div', { className: 'subnav-group' },
      i18n('evaluation.schemas.list.user_group', userSchemas.length)));
    for (const s of userSchemas) list.append(renderListItem(s, state, ctx));
  }

  if (builtinSchemas.length > 0) {
    list.append(el('div', { className: 'subnav-group' },
      i18n('evaluation.schemas.list.builtin_group', builtinSchemas.length)));
    for (const s of builtinSchemas) list.append(renderListItem(s, state, ctx));
  }

  if (state.schemas.length === 0) {
    list.append(el('div', { className: 'empty-state-compact' },
      i18n('evaluation.schemas.list.empty')));
  }

  return list;
}

function renderListItem(meta, state, ctx) {
  const isSelected = meta.id === state.selectedId;
  const isUser = meta.source === 'user';
  const classes = ['script-editor-list-item'];
  if (isSelected) classes.push('active');
  if (!isUser) classes.push('readonly');

  const badges = [];
  if (!isUser) {
    badges.push(el('span', { className: 'badge secondary' },
      i18n('evaluation.schemas.list.badge_builtin')));
  }
  if (isUser && meta.overriding_builtin) {
    badges.push(el('span', { className: 'badge warn' },
      i18n('evaluation.schemas.list.badge_overriding')));
  }
  const modeBadge = meta.mode === 'comparative'
    ? i18n('evaluation.schemas.list.badge_comparative')
    : i18n('evaluation.schemas.list.badge_absolute');
  badges.push(el('span', { className: 'badge' }, modeBadge));
  const granBadge = meta.granularity === 'conversation'
    ? i18n('evaluation.schemas.list.badge_conversation')
    : i18n('evaluation.schemas.list.badge_single');
  badges.push(el('span', { className: 'badge' }, granBadge));
  if (meta.has_ai_ness_penalty) {
    badges.push(el('span', { className: 'badge' },
      i18n('evaluation.schemas.list.badge_penalty')));
  }

  const actions = [];
  if (!isUser) {
    actions.push(el('button', {
      className: 'small',
      onClick: (ev) => {
        ev.stopPropagation();
        duplicateSchema(meta.id, state, ctx);
      },
    }, i18n('evaluation.schemas.list.duplicate')));
  } else {
    actions.push(el('button', {
      className: 'danger small',
      onClick: (ev) => {
        ev.stopPropagation();
        deleteUserSchema(meta.id, state, ctx);
      },
    }, i18n('evaluation.schemas.list.delete')));
  }

  return el('div', {
    className: classes.join(' '),
    onClick: () => ctx.loadDetails(meta.id),
  },
    el('div', {
      className: 'script-editor-list-item-title u-truncate',
      title: meta.id,
    }, meta.id),
    el('div', { className: 'script-editor-list-item-meta' },
      i18n('evaluation.schemas.list.dims_fmt', meta.dimensions_count),
      ...badges,
    ),
    meta.description
      ? el('div', {
          className: 'script-editor-list-item-desc u-truncate-2',
          title: meta.description,
        }, meta.description)
      : null,
    el('div', { className: 'script-editor-list-item-actions' }, ...actions),
  );
}

async function duplicateSchema(sourceId, state, ctx) {
  const suggested = suggestUniqueId(state.schemas, `${sourceId}_copy`);
  const target = (window.prompt(
    i18n('evaluation.schemas.prompt.duplicate_id', sourceId), suggested,
  ) || '').trim();
  if (!target) return;
  const res = await api.post(`${API_BASE}/duplicate`, {
    source_id: sourceId,
    target_id: target,
    overwrite: false,
  });
  if (!res.ok) {
    toast.err(i18n('evaluation.schemas.toast.duplicate_failed'),
      { message: res.error?.message || `HTTP ${res.status}` });
    return;
  }
  toast.ok(i18n('evaluation.schemas.toast.duplicated', target));
  await ctx.refreshList();
  await ctx.loadDetails(target);
}

async function deleteUserSchema(id, state, ctx) {
  const confirmed = window.confirm(i18n('evaluation.schemas.prompt.confirm_delete', id));
  if (!confirmed) return;
  const res = await api.delete(`${API_BASE}/${encodeURIComponent(id)}`);
  if (!res.ok) {
    toast.err(i18n('evaluation.schemas.toast.delete_failed'),
      { message: res.error?.message || `HTTP ${res.status}` });
    return;
  }
  toast.ok(i18n('evaluation.schemas.toast.deleted', id));
  if (res.data?.resurfaces_builtin) {
    toast.info(i18n('evaluation.schemas.toast.resurfaces_builtin', id));
  }
  if (state.selectedId === id) {
    state.selectedId = null;
    state.details = null;
    state.draft = null;
    state.baseline = null;
  }
  await ctx.refreshList();
}

// ── 右列: 编辑器 ─────────────────────────────────────────────

function renderEditor(state, ctx) {
  const pane = el('div', { className: 'script-editor-pane' });
  if (state.loadingDetails) {
    pane.append(el('div', { className: 'empty-state' },
      i18n('evaluation.schemas.editor.loading')));
    return pane;
  }
  if (!state.details || !state.draft) {
    pane.append(renderEditorEmptyState());
    return pane;
  }

  const readonly = state.details.has_builtin && !state.details.has_user
    && state.draft.id === state.details.active?.id;

  pane.append(renderEditorHeader(state, ctx, readonly));
  pane.append(renderBasicFields(state, readonly));
  pane.append(renderModeFields(state, readonly));
  pane.append(renderDimensions(state, ctx, readonly));
  pane.append(renderPenalty(state, ctx, readonly));
  pane.append(renderFormulaFields(state, readonly));
  pane.append(renderPromptField(state, readonly));
  pane.append(renderTagsField(state, readonly));
  const errorsNode = renderEditorErrors(state);
  if (errorsNode) pane.append(errorsNode);
  return pane;
}

function renderEditorEmptyState() {
  return el('div', { className: 'empty-state' },
    el('h3', {}, i18n('evaluation.schemas.editor.empty_title')),
    el('p', {}, i18n('evaluation.schemas.editor.empty_hint')),
  );
}

function renderEditorHeader(state, ctx, readonly) {
  const dirty = isDirty(state);
  const header = el('div', { className: 'script-editor-header' });
  const title = el('div', { className: 'script-editor-header-title' },
    el('h3', {}, state.draft.id || i18n('evaluation.schemas.editor.untitled')),
  );
  if (readonly) {
    title.append(el('span', { className: 'badge secondary' },
      i18n('evaluation.schemas.editor.readonly_badge')));
  } else if (dirty) {
    title.append(el('span', { className: 'badge warn' },
      i18n('evaluation.schemas.editor.dirty_badge')));
  }
  if (state.details.overriding_builtin) {
    title.append(el('span', { className: 'badge warn' },
      i18n('evaluation.schemas.list.badge_overriding')));
  }
  header.append(title);

  const buttons = el('div', { className: 'script-editor-header-actions' });
  if (readonly) {
    buttons.append(
      el('button', {
        className: 'primary',
        onClick: () => duplicateSchema(state.draft.id, state, ctx),
      }, i18n('evaluation.schemas.list.duplicate')),
      el('button', { onClick: () => openPreview(state) },
        i18n('evaluation.schemas.buttons.preview_prompt')),
      el('button', { onClick: () => exportDraft(state) },
        i18n('evaluation.schemas.buttons.export')),
    );
  } else {
    // No separate [校验] button — Save 已走后端 validate_schema_dict, 失败时
    // 返回 422 + errors 列表, 前端同路径做红框高亮, 独立一个按钮是冗余.
    buttons.append(
      el('button', {
        className: 'primary' + (dirty ? ' is-dirty' : ''),
        onClick: () => saveDraft(state, ctx),
      }, i18n('evaluation.schemas.buttons.save')),
      el('button', {
        onClick: () => reloadDraft(state, ctx),
        disabled: !dirty || !state.baseline,
      }, i18n('evaluation.schemas.buttons.reload')),
      el('button', { onClick: () => openPreview(state) },
        i18n('evaluation.schemas.buttons.preview_prompt')),
      el('button', { onClick: () => exportDraft(state) },
        i18n('evaluation.schemas.buttons.export')),
    );
    if (state.details.has_user) {
      buttons.append(el('button', {
        className: 'danger',
        onClick: () => deleteUserSchema(state.draft.id, state, ctx),
      }, i18n('evaluation.schemas.list.delete')));
    }
  }
  header.append(buttons);

  if (readonly) {
    header.append(el('p', { className: 'script-editor-header-hint' },
      i18n('evaluation.schemas.editor.readonly_hint')));
  }

  // Meta 行 — max_raw_score / dim count / penalty on-off — 方便一眼看总览.
  const meta = el('div', { className: 'script-editor-header-meta' },
    el('span', {},
      i18n('evaluation.schemas.editor.meta.max_raw_score',
        computeMaxRawScore(state.draft))),
    el('span', {},
      i18n('evaluation.schemas.editor.meta.dims_count', state.draft.dimensions.length)),
    el('span', {},
      state.draft.ai_ness_penalty
        ? i18n('evaluation.schemas.editor.meta.penalty_enabled')
        : i18n('evaluation.schemas.editor.meta.penalty_disabled')),
  );
  header.append(meta);

  return header;
}

function renderBasicFields(state, readonly) {
  const card = el('div', { className: 'script-editor-card' });
  card.append(el('h4', {}, i18n('evaluation.schemas.editor.basic_heading')));

  const idInput = el('input', {
    type: 'text',
    value: state.draft.id,
    disabled: readonly,
    onInput: (ev) => { state.draft.id = ev.target.value.trim(); },
  });
  card.append(field(i18n('evaluation.schemas.editor.fields.id'), idInput, {
    hint: readonly
      ? i18n('evaluation.schemas.editor.hints.id_readonly')
      : i18n('evaluation.schemas.editor.hints.id'),
  }));

  const nameInput = el('input', {
    type: 'text',
    value: state.draft.name || '',
    disabled: readonly,
    onInput: (ev) => { state.draft.name = ev.target.value; },
  });
  card.append(field(i18n('evaluation.schemas.editor.fields.name'), nameInput));

  const descArea = el('textarea', {
    rows: 2,
    disabled: readonly,
    onInput: (ev) => { state.draft.description = ev.target.value; },
  });
  descArea.value = state.draft.description || '';
  card.append(field(i18n('evaluation.schemas.editor.fields.description'), descArea,
    { wide: true }));

  const versionInput = el('input', {
    type: 'number',
    step: '1',
    min: '1',
    value: state.draft.version ?? 1,
    disabled: readonly,
    onInput: (ev) => {
      const n = Number(ev.target.value);
      state.draft.version = Number.isFinite(n) && n >= 1 ? Math.trunc(n) : 1;
    },
  });
  card.append(field(i18n('evaluation.schemas.editor.fields.version'), versionInput));

  return card;
}

function renderModeFields(state, readonly) {
  const card = el('div', { className: 'script-editor-card' });
  card.append(el('h4', {}, i18n('evaluation.schemas.editor.mode_heading')));

  const modeSel = el('select', {
    disabled: readonly,
    onChange: (ev) => { state.draft.mode = ev.target.value; },
  },
    ...VALID_MODES.map((m) => el('option', {
      value: m, selected: state.draft.mode === m,
    }, m)),
  );
  modeSel.value = state.draft.mode;
  card.append(field(i18n('evaluation.schemas.editor.fields.mode'), modeSel, {
    hint: i18n('evaluation.schemas.editor.hints.mode'),
  }));

  const granSel = el('select', {
    disabled: readonly,
    onChange: (ev) => { state.draft.granularity = ev.target.value; },
  },
    ...VALID_GRANULARITY.map((g) => el('option', {
      value: g, selected: state.draft.granularity === g,
    }, g)),
  );
  granSel.value = state.draft.granularity;
  card.append(field(i18n('evaluation.schemas.editor.fields.granularity'), granSel, {
    hint: i18n('evaluation.schemas.editor.hints.granularity'),
  }));

  return card;
}

// ── 维度 + 锚点 ─────────────────────────────────────────────

function renderDimensions(state, ctx, readonly) {
  const card = el('div', { className: 'script-editor-card' });
  const header = el('div', { className: 'script-editor-turns-header' },
    el('h4', {},
      i18n('evaluation.schemas.editor.dimensions_heading',
        state.draft.dimensions.length)),
  );
  if (!readonly) {
    header.append(el('div', { className: 'script-editor-turns-actions' },
      el('button', {
        className: 'small',
        onClick: () => {
          state.draft.dimensions.push(emptyDimension());
          ctx.rerender();
        },
      }, i18n('evaluation.schemas.editor.buttons.add_dim')),
    ));
  }
  card.append(header);

  const turnsWrap = el('div', { className: 'script-editor-turns' });
  state.draft.dimensions.forEach((dim, idx) => {
    turnsWrap.append(renderDimensionCard(dim, idx, state, ctx, readonly));
  });
  card.append(turnsWrap);

  return card;
}

function renderDimensionCard(dim, idx, state, ctx, readonly) {
  const dimErrors = state.errors.filter((e) => e.path.startsWith(`dimensions[${idx}]`));
  const isEmpty = !(dim.key || '').trim() && !(dim.label || '').trim();

  // ── header 装饰 ──
  // 折叠态的标题 = "#1 共情度", 徽章 = key / weight / anchors 数量, 让用户
  // 在折叠状态下不展开也能掌握这维的关键属性.
  const displayLabel = (dim.label || '').trim()
    || (dim.key || '').trim()
    || i18n('evaluation.schemas.editor.dim_untitled');
  const titleText = `#${idx + 1}  ${displayLabel}`;
  const badges = [];
  badges.push(`${i18n('evaluation.schemas.editor.fields.dim_key')}: ${dim.key || '—'}`);
  badges.push(`${i18n('evaluation.schemas.editor.fields.dim_weight')}: ${dim.weight ?? 1}`);
  const nAnchors = Object.keys(dim.anchors || {}).length;
  badges.push(i18n('evaluation.schemas.editor.dim_anchor_badge_fmt', nAnchors));

  const actions = [];
  if (!readonly) {
    actions.push(
      el('button', {
        className: 'small',
        disabled: idx === 0,
        onClick: () => { swap(state.draft.dimensions, idx, idx - 1); ctx.rerender(); },
      }, i18n('evaluation.schemas.editor.buttons.move_up')),
      el('button', {
        className: 'small',
        disabled: idx === state.draft.dimensions.length - 1,
        onClick: () => { swap(state.draft.dimensions, idx, idx + 1); ctx.rerender(); },
      }, i18n('evaluation.schemas.editor.buttons.move_down')),
      el('button', {
        className: 'danger small',
        onClick: () => { state.draft.dimensions.splice(idx, 1); ctx.rerender(); },
      }, i18n('evaluation.schemas.editor.buttons.remove')),
    );
  }

  // ── body ──
  const body = el('div', { className: 'foldable-card-body-inner' });

  const row = el('div', { className: 'script-editor-bootstrap-row' });
  const keyInput = el('input', {
    type: 'text',
    value: dim.key || '',
    disabled: readonly,
    onInput: (ev) => { dim.key = ev.target.value.trim(); },
  });
  row.append(field(i18n('evaluation.schemas.editor.fields.dim_key'), keyInput, {
    hint: i18n('evaluation.schemas.editor.hints.dim_key'),
  }));
  const labelInput = el('input', {
    type: 'text',
    value: dim.label || '',
    disabled: readonly,
    onInput: (ev) => { dim.label = ev.target.value; },
  });
  row.append(field(i18n('evaluation.schemas.editor.fields.dim_label'), labelInput));
  const weightInput = el('input', {
    type: 'number',
    step: '0.1',
    min: '0',
    value: dim.weight ?? 1,
    disabled: readonly,
    onInput: (ev) => {
      const n = Number(ev.target.value);
      dim.weight = Number.isFinite(n) ? n : 0;
      refreshHeaderMeta();
    },
  });
  row.append(field(i18n('evaluation.schemas.editor.fields.dim_weight'), weightInput, {
    hint: i18n('evaluation.schemas.editor.hints.dim_weight'),
  }));
  body.append(row);

  const descArea = el('textarea', {
    rows: 2,
    placeholder: i18n('evaluation.schemas.editor.placeholders.dim_description'),
    disabled: readonly,
    onInput: (ev) => { dim.description = ev.target.value; },
  });
  descArea.value = dim.description || '';
  body.append(field(i18n('evaluation.schemas.editor.fields.dim_description'), descArea,
    { wide: true }));

  const anchorsWrap = el('div', { className: 'script-editor-card tight' });
  anchorsWrap.append(el('h5', {}, i18n('evaluation.schemas.editor.anchors_heading')));
  Object.entries(dim.anchors || {}).forEach(([rng, text], aIdx) => {
    anchorsWrap.append(renderAnchorRow(dim, rng, text, aIdx, state, ctx, readonly));
  });
  if (!readonly) {
    anchorsWrap.append(el('button', {
      className: 'small',
      onClick: () => {
        dim.anchors = dim.anchors || {};
        const nextKey = suggestNextAnchorRange(dim.anchors);
        dim.anchors[nextKey] = '';
        ctx.rerender();
      },
    }, i18n('evaluation.schemas.editor.buttons.add_anchor')));
  }
  body.append(anchorsWrap);

  if (dimErrors.length) {
    body.append(el('div', { className: 'script-editor-turn-errors' },
      ...dimErrors.map((e) => el('div', {}, `${e.path}: ${e.message}`))));
  }

  // schemaId 作 blockId 前缀是为了跨 schema 各自保留自己的折叠记忆; idx 作
  // 后缀则是位置稳定假设 — 用户若频繁重排, LS 可能"记错"哪个展开, 但那只是
  // 视觉状态, 成本低, 不值得为此去生成维度级稳定 uid (那会把 dim 字段又多
  // 塞一个内部 id, 反而污染 schema 存盘数据).
  const schemaId = state.draft?.id || '_new_';
  const blockId = `eval:schema:${schemaId}:dim:${idx}`;

  return makeFoldableCard({
    blockId,
    title: titleText,
    badges,
    headerActions: actions,
    body,
    // 新建/空的维度默认展开, 让用户立刻能填; 已填的默认折叠省空间; 有错的
    // 条目 forceOpen 以便用户一眼看到红框.
    defaultCollapsed: !isEmpty,
    forceOpen: dimErrors.length > 0,
    hasError: dimErrors.length > 0,
  });
}

function renderAnchorRow(dim, rng, text, aIdx, state, ctx, readonly) {
  const row = el('div', { className: 'script-editor-bootstrap-row' });
  const rngInput = el('input', {
    type: 'text',
    value: rng,
    disabled: readonly,
    placeholder: '9-10',
    onInput: (ev) => {
      const newKey = ev.target.value.trim();
      if (!newKey || newKey === rng) return;
      if (newKey in dim.anchors) {
        ev.target.value = rng;  // revert
        return;
      }
      const oldText = dim.anchors[rng];
      delete dim.anchors[rng];
      dim.anchors[newKey] = oldText;
      ctx.rerender();
    },
  });
  row.append(field(i18n('evaluation.schemas.editor.fields.anchor_range'), rngInput, {
    hint: i18n('evaluation.schemas.editor.hints.anchor_range'),
  }));

  const txtArea = el('textarea', {
    rows: 2,
    placeholder: i18n('evaluation.schemas.editor.placeholders.anchor_text'),
    disabled: readonly,
    onInput: (ev) => { dim.anchors[rng] = ev.target.value; },
  });
  txtArea.value = text || '';
  row.append(field(i18n('evaluation.schemas.editor.fields.anchor_text'), txtArea, {
    wide: true,
  }));

  if (!readonly) {
    row.append(el('button', {
      className: 'danger small',
      onClick: () => {
        delete dim.anchors[rng];
        ctx.rerender();
      },
    }, i18n('evaluation.schemas.editor.buttons.remove')));
  }
  return row;
}

function suggestNextAnchorRange(anchors) {
  const existing = Object.keys(anchors);
  const defaults = ['9-10', '7-8', '5-6', '3-4', '1-2'];
  for (const d of defaults) {
    if (!existing.includes(d)) return d;
  }
  return `0-${existing.length + 1}`;
}

// ── penalty ────────────────────────────────────────────────

function renderPenalty(state, ctx, readonly) {
  const card = el('div', { className: 'script-editor-card' });
  const heading = el('div', { className: 'script-editor-turns-header' },
    el('h4', {}, i18n('evaluation.schemas.editor.penalty_heading')),
  );
  if (!readonly) {
    heading.append(el('div', { className: 'script-editor-turns-actions' },
      el('label', { className: 'inline-checkbox' },
        el('input', {
          type: 'checkbox',
          checked: !!state.draft.ai_ness_penalty,
          onChange: (ev) => {
            state.draft.ai_ness_penalty = ev.target.checked
              ? (state.draft.ai_ness_penalty || emptyPenalty())
              : null;
            ctx.rerender();
          },
        }),
        ' ',
        i18n('evaluation.schemas.editor.buttons.toggle_penalty'),
      ),
    ));
  }
  card.append(heading);

  const pen = state.draft.ai_ness_penalty;
  if (!pen) {
    card.append(el('p', { className: 'hint' },
      i18n('evaluation.schemas.editor.meta.penalty_disabled')));
    return card;
  }

  const row = el('div', { className: 'script-editor-bootstrap-row' });
  const maxInput = el('input', {
    type: 'number',
    step: '1',
    min: '1',
    value: pen.max ?? 15,
    disabled: readonly,
    onInput: (ev) => {
      const n = Number(ev.target.value);
      pen.max = Number.isFinite(n) && n > 0 ? Math.trunc(n) : 1;
    },
  });
  row.append(field(i18n('evaluation.schemas.editor.fields.penalty_max'), maxInput));

  const okInput = el('input', {
    type: 'number',
    step: '1',
    min: '0',
    value: pen.max_passable ?? 9,
    disabled: readonly,
    onInput: (ev) => {
      const n = Number(ev.target.value);
      pen.max_passable = Number.isFinite(n) && n >= 0 ? Math.trunc(n) : 0;
    },
  });
  row.append(field(i18n('evaluation.schemas.editor.fields.penalty_max_passable'), okInput));
  card.append(row);

  const descArea = el('textarea', {
    rows: 2,
    disabled: readonly,
    onInput: (ev) => { pen.description = ev.target.value; },
  });
  descArea.value = pen.description || '';
  card.append(field(i18n('evaluation.schemas.editor.fields.penalty_description'), descArea,
    { wide: true }));

  // penalty anchors - reuse anchor row but scoped to penalty.anchors
  const anchorsWrap = el('div', { className: 'script-editor-card tight' });
  anchorsWrap.append(el('h5', {}, i18n('evaluation.schemas.editor.anchors_heading')));
  pen.anchors = pen.anchors || {};
  Object.entries(pen.anchors).forEach(([rng, text], aIdx) => {
    anchorsWrap.append(renderPenaltyAnchorRow(pen, rng, text, aIdx, ctx, readonly));
  });
  if (!readonly) {
    anchorsWrap.append(el('button', {
      className: 'small',
      onClick: () => {
        const nextKey = suggestNextAnchorRange(pen.anchors);
        pen.anchors[nextKey] = '';
        ctx.rerender();
      },
    }, i18n('evaluation.schemas.editor.buttons.add_anchor')));
  }
  card.append(anchorsWrap);

  return card;
}

function renderPenaltyAnchorRow(pen, rng, text, aIdx, ctx, readonly) {
  const row = el('div', { className: 'script-editor-bootstrap-row' });
  const rngInput = el('input', {
    type: 'text',
    value: rng,
    disabled: readonly,
    placeholder: '0-2',
    onInput: (ev) => {
      const newKey = ev.target.value.trim();
      if (!newKey || newKey === rng || newKey in pen.anchors) return;
      pen.anchors[newKey] = pen.anchors[rng];
      delete pen.anchors[rng];
      ctx.rerender();
    },
  });
  row.append(field(i18n('evaluation.schemas.editor.fields.anchor_range'), rngInput));

  const txt = el('textarea', {
    rows: 2,
    disabled: readonly,
    onInput: (ev) => { pen.anchors[rng] = ev.target.value; },
  });
  txt.value = text || '';
  row.append(field(i18n('evaluation.schemas.editor.fields.anchor_text'), txt, { wide: true }));

  if (!readonly) {
    row.append(el('button', {
      className: 'danger small',
      onClick: () => { delete pen.anchors[rng]; ctx.rerender(); },
    }, i18n('evaluation.schemas.editor.buttons.remove')));
  }
  return row;
}

// ── 公式 / 规则 ────────────────────────────────────────────

function renderFormulaFields(state, readonly) {
  const card = el('div', { className: 'script-editor-card' });
  card.append(el('h4', {}, i18n('evaluation.schemas.editor.formula_heading')));

  const rawInput = el('textarea', {
    rows: 3,
    disabled: readonly,
    onInput: (ev) => { state.draft.raw_score_formula = ev.target.value; },
  });
  rawInput.value = state.draft.raw_score_formula || '';
  card.append(field(
    i18n('evaluation.schemas.editor.fields.raw_score_formula'),
    rawInput, { wide: true },
  ));

  const normInput = el('textarea', {
    rows: 2,
    disabled: readonly,
    onInput: (ev) => { state.draft.normalize_formula = ev.target.value; },
  });
  normInput.value = state.draft.normalize_formula || '';
  card.append(field(
    i18n('evaluation.schemas.editor.fields.normalize_formula'),
    normInput, { wide: true },
  ));

  const verdictInput = el('textarea', {
    rows: 2,
    disabled: readonly,
    onInput: (ev) => { state.draft.verdict_rule = ev.target.value; },
  });
  verdictInput.value = state.draft.verdict_rule || '';
  card.append(field(
    i18n('evaluation.schemas.editor.fields.verdict_rule'),
    verdictInput, { wide: true },
  ));

  const passInput = el('textarea', {
    rows: 2,
    disabled: readonly,
    onInput: (ev) => { state.draft.pass_rule = ev.target.value; },
  });
  passInput.value = state.draft.pass_rule || '';
  card.append(field(
    i18n('evaluation.schemas.editor.fields.pass_rule'),
    passInput, { wide: true },
  ));

  return card;
}

// ── prompt_template ───────────────────────────────────────

function renderPromptField(state, readonly) {
  const card = el('div', { className: 'script-editor-card' });
  card.append(el('h4', {}, i18n('evaluation.schemas.editor.prompt_heading')));
  card.append(el('p', { className: 'hint' },
    i18n('evaluation.schemas.editor.prompt_hint')));

  // PLAN §13 F4 security advisory: the POST /api/judge/run body's
  // ``extra_context`` can overlay any of the prompt placeholders listed
  // above; collisions silently replace testbench-managed context with
  // caller-controlled text. UI doesn't expose this knob, so it only
  // applies to script / direct-API callers — but anyone reading a
  // third-party schema + its associated runner script should be warned.
  // Back-end audit lands in Diagnostics → Errors via
  // ``judge_router._audit_extra_context_override``. See also
  // AGENT_NOTES §4.27 #97 (I2) for attack-surface mapping.
  card.append(el('p', { className: 'preview-hint danger' },
    i18n('evaluation.schemas.editor.prompt_extra_context_warn')));

  const ta = el('textarea', {
    rows: 20,
    className: 'mono',
    placeholder: i18n('evaluation.schemas.editor.placeholders.prompt_template'),
    disabled: readonly,
    onInput: (ev) => { state.draft.prompt_template = ev.target.value; },
  });
  ta.value = state.draft.prompt_template || '';
  card.append(field(
    i18n('evaluation.schemas.editor.fields.prompt_template'),
    ta, { wide: true, hint: i18n('evaluation.schemas.editor.hints.prompt_template') },
  ));
  return card;
}

// ── tags ───────────────────────────────────────────────────

function renderTagsField(state, readonly) {
  const card = el('div', { className: 'script-editor-card' });
  card.append(el('h4', {}, i18n('evaluation.schemas.editor.tags_heading')));

  const input = el('input', {
    type: 'text',
    value: (state.draft.tags || []).join(', '),
    disabled: readonly,
    onInput: (ev) => {
      state.draft.tags = ev.target.value
        .split(',')
        .map((t) => t.trim())
        .filter(Boolean);
    },
  });
  card.append(field(i18n('evaluation.schemas.editor.fields.tags'), input, { wide: true }));
  return card;
}

function renderEditorErrors(state) {
  if (!state.errors.length) return null;
  // dim / anchor 错误已贴在对应卡片, 这里只 surfaceno-prefix 的顶层错误.
  const topLevel = state.errors.filter((e) => !e.path.startsWith('dimensions['));
  if (!topLevel.length) return null;
  return el('div', { className: 'script-editor-errors' },
    el('h4', {}, i18n('evaluation.schemas.editor.errors_heading')),
    ...topLevel.map((e) => el('div', { className: 'script-editor-error-item' },
      el('code', {}, e.path || '(root)'),
      ': ',
      e.message,
    )),
  );
}

// ── actions ──────────────────────────────────────────────────────

async function saveDraft(state, ctx) {
  const payload = draftToPayload(state.draft);
  const oldId = state.baseline?.id;
  const newId = payload.id;
  const renaming = oldId && oldId !== newId;

  const res = await api.post(API_BASE, payload);
  if (!res.ok) {
    // 2026-04-22 Day 8 手测 #2: api.js 现在把 errors / message 平铺到
    // res.error 上, 不用再 `res.data?.detail` (非 2xx 时 res.data = null).
    if (Array.isArray(res.error?.errors) && res.error.errors.length > 0) {
      state.errors = res.error.errors;
      ctx.rerender();
      toast.err(i18n('evaluation.schemas.toast.save_errors', state.errors.length));
    } else {
      toast.err(i18n('evaluation.schemas.toast.save_failed'),
        { message: res.error?.message || `HTTP ${res.status}` });
    }
    return;
  }

  if (renaming && state.details?.has_user) {
    const delRes = await api.delete(`${API_BASE}/${encodeURIComponent(oldId)}`);
    if (!delRes.ok) {
      toast.warn(i18n('evaluation.schemas.toast.rename_left_old', oldId));
    }
  }

  state.errors = [];
  toast.ok(i18n('evaluation.schemas.toast.saved', newId));
  if (res.data.overriding_builtin) {
    toast.info(i18n('evaluation.schemas.toast.now_overriding_builtin', newId));
  }
  await ctx.refreshList();
  await ctx.loadDetails(newId);
}

function reloadDraft(state, ctx) {
  if (!state.baseline) return;
  state.draft = JSON.parse(JSON.stringify(state.baseline));
  state.errors = [];
  ctx.rerender();
}

function exportDraft(state) {
  const payload = draftToPayload(state.draft);
  const blob = new Blob([JSON.stringify(payload, null, 2) + '\n'],
    { type: 'application/json' });
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = `${payload.id || 'schema'}.json`;
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

// ── preview prompt modal ─────────────────────────────────

async function openPreview(state) {
  const id = state.draft?.id;
  if (!id) return;
  // preview 端点按 id 查磁盘 schema 而非接收 body, 所以为了看到未保存的改动,
  // P15 简化为"落盘后预览" — 当 draft dirty 时 toast 提示"看到的是磁盘版本",
  // 先 [保存] 再 [预览 prompt] 就能拿到最新结果. 若 draft 与 baseline 一致
  // (非 dirty), 直接走后端 preview 即可.
  const dirty = isDirty(state);
  if (dirty && state.details?.has_user === false) {
    toast.warn(i18n('evaluation.schemas.editor.preview_dirty_new'));
  } else if (dirty) {
    toast.warn(i18n('evaluation.schemas.editor.preview_dirty_existing'));
  }
  const res = await api.post(`${API_BASE}/${encodeURIComponent(id)}/preview_prompt`, {});
  if (!res.ok) {
    toast.err(i18n('evaluation.schemas.toast.preview_failed'),
      { message: String(res.error || res.status) });
    return;
  }
  showPreviewModal(res.data);
}

function showPreviewModal(data) {
  const backdrop = el('div', { className: 'modal-backdrop' });
  const dialog = el('div', { className: 'modal modal-wide' });
  const closeBtn = el('button', {
    className: 'small',
    onClick: () => backdrop.remove(),
  }, i18n('evaluation.schemas.editor.preview_dialog.close'));
  const copyBtn = el('button', {
    className: 'small',
    onClick: async () => {
      try {
        await navigator.clipboard.writeText(data.prompt || '');
        copyBtn.textContent = i18n('evaluation.schemas.editor.preview_dialog.copy_done');
      } catch {
        toast.warn(i18n('evaluation.schemas.editor.preview_copy_fail'));
      }
    },
  }, i18n('evaluation.schemas.editor.preview_dialog.copy'));

  dialog.append(
    el('div', { className: 'modal-header' },
      el('h3', {}, i18n('evaluation.schemas.editor.preview_dialog.title')),
      el('div', {}, copyBtn, ' ', closeBtn),
    ),
    el('div', { className: 'modal-meta' },
      el('span', {}, i18n('evaluation.schemas.editor.preview_dialog.char_count', data.char_count)),
    ),
    el('pre', { className: 'prompt-preview' }, data.prompt || ''),
    el('div', { className: 'modal-meta' },
      el('div', {},
        el('strong', {}, i18n('evaluation.schemas.editor.preview_dialog.used_label')),
        ': ',
        (data.used_placeholders || []).join(', ') || '(none)',
      ),
      el('div', {},
        el('strong', {}, i18n('evaluation.schemas.editor.preview_dialog.missing_label')),
        ': ',
        (data.missing_placeholders || []).join(', ') || '(none)',
      ),
    ),
  );
  backdrop.append(dialog);
  backdrop.addEventListener('click', (ev) => {
    if (ev.target === backdrop) backdrop.remove();
  });
  document.body.append(backdrop);
}

// ── 数据转换 / util ──────────────────────────────────────

function schemaToDraft(src) {
  if (!src) return null;
  return {
    id: src.id || '',
    name: src.name || '',
    description: src.description || '',
    mode: src.mode || 'absolute',
    granularity: src.granularity || 'single',
    dimensions: (src.dimensions || []).map((d) => ({
      key: d.key || '',
      label: d.label || '',
      weight: d.weight ?? 1,
      description: d.description || '',
      anchors: { ...(d.anchors || {}) },
    })),
    prompt_template: src.prompt_template || '',
    ai_ness_penalty: src.ai_ness_penalty
      ? {
          max: src.ai_ness_penalty.max ?? 15,
          max_passable: src.ai_ness_penalty.max_passable ?? 9,
          description: src.ai_ness_penalty.description || '',
          anchors: { ...(src.ai_ness_penalty.anchors || {}) },
        }
      : null,
    pass_rule: src.pass_rule || '',
    verdict_rule: src.verdict_rule || '',
    raw_score_formula: src.raw_score_formula || '',
    normalize_formula: src.normalize_formula || '',
    version: src.version ?? 1,
    tags: Array.isArray(src.tags) ? [...src.tags] : [],
  };
}

function emptyDraft(id) {
  return {
    id,
    name: id,
    description: '',
    mode: 'absolute',
    granularity: 'single',
    dimensions: [emptyDimension()],
    prompt_template: '{system_prompt}\n\n=== 对话 ===\n{history}\n\n=== 用户 ===\n{user_input}\n\n=== AI ===\n{ai_response}\n\n=== 评分 ===\n{dimensions_block}\n\n{anchors_block}\n\n{formula_block}\n\n请输出 JSON:\n{{\n  "overall_score": 0-100\n}}',
    ai_ness_penalty: null,
    pass_rule: '',
    verdict_rule: '',
    raw_score_formula: '',
    normalize_formula: '',
    version: 1,
    tags: [],
  };
}

function emptyDimension() {
  return {
    key: '',
    label: '',
    weight: 1,
    description: '',
    anchors: {},
  };
}

function emptyPenalty() {
  return {
    max: 15,
    max_passable: 9,
    description: '',
    anchors: {},
  };
}

function draftToPayload(draft) {
  const out = {
    id: (draft.id || '').trim(),
    name: (draft.name || '').trim(),
    description: (draft.description || '').trim(),
    mode: draft.mode,
    granularity: draft.granularity,
    dimensions: (draft.dimensions || []).map((d) => ({
      key: (d.key || '').trim(),
      label: (d.label || '').trim(),
      weight: Number(d.weight) || 0,
      description: d.description || '',
      anchors: { ...(d.anchors || {}) },
    })),
    prompt_template: draft.prompt_template || '',
    pass_rule: draft.pass_rule || '',
    verdict_rule: draft.verdict_rule || '',
    raw_score_formula: draft.raw_score_formula || '',
    normalize_formula: draft.normalize_formula || '',
    version: Number(draft.version) || 1,
    tags: [...(draft.tags || [])],
  };
  if (draft.ai_ness_penalty) {
    out.ai_ness_penalty = {
      max: Number(draft.ai_ness_penalty.max) || 15,
      max_passable: Number(draft.ai_ness_penalty.max_passable) || 9,
      description: draft.ai_ness_penalty.description || '',
      anchors: { ...(draft.ai_ness_penalty.anchors || {}) },
    };
  }
  return out;
}

function isDirty(state) {
  if (!state.baseline) return !!state.draft;
  return JSON.stringify(draftToPayload(state.draft))
    !== JSON.stringify(draftToPayload(state.baseline));
}

function computeMaxRawScore(draft) {
  const total = (draft.dimensions || [])
    .reduce((acc, d) => acc + (Number(d.weight) || 0) * 10, 0);
  return Math.round(total * 10000) / 10000;
}

function swap(arr, i, j) {
  const tmp = arr[i];
  arr[i] = arr[j];
  arr[j] = tmp;
}

// dim weight 的 onInput 改了 draft, 但是 header 的 max_raw_score meta 不
// 自动重算.  调用方有 ctx.rerender 时就用它; 这里给一个 noop fallback 以
// 便 weight 实时刷新靠的是整页 rerender (save/validate 时) — 实时数字不
// 准问题不大, 一致以最终保存为准.
function refreshHeaderMeta() {
  // 实时刷新 header 的 max_raw_score 成本高 (要 rerender 整页), P15 简化为
  // "改完 weight 后点其他控件触发 blur → 任何后续交互都会触发 rerender →
  // 届时再刷新". 初次滑过不同步的数字不影响功能性.
}
