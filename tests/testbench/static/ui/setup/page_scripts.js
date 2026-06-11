/**
 * page_scripts.js — Setup → Scripts 子页 (P12.5).
 *
 * 作用: 在浏览器里阅览 / 复制 / 编辑 / 新建 / 删除 / 导出 ``dialog_templates/``
 * 下的对话剧本模板, 不再需要外部文本编辑器改 JSON.  **评分 prompt / 评分
 * 维度不在本页**, 归 P15 Evaluation → Schemas 子页.
 *
 * 布局: 左列列表 (用户模板 + 内置模板两组), 右列编辑器 (空态 / 只读 /
 * 可编辑).  所有交互都是 "先拉详情, 本地 draft, 校验后保存" 的单向流,
 * 没有复杂状态机.
 *
 * 设计决策 (见 PROGRESS.md P12.5):
 *   1. builtin 不可原地编辑, 只能 [复制为可编辑] 生成 user 副本再改.
 *   2. user 的 name 与 builtin 重名 = 覆盖 builtin.  UI 加 "覆盖中" 徽章.
 *   3. name 即文件名.  改 name = Save As (走 "save 新 + delete 旧").
 *   4. 校验隐式内嵌在 Save 里 (打字过程不打扰): POST /templates 失败 422
 *      带 ``detail.errors``, UI 按路径红框高亮. 没有独立的 Validate 按钮.
 *   5. 本页不做"试跑"按钮 — 回 Chat workspace 用 Composer 加载 / 下一轮.
 */

import { i18n } from '../../core/i18n.js';
import { api } from '../../core/api.js';
import { toast } from '../../core/toast.js';
import { emit } from '../../core/state.js';
import { el, field } from '../_dom.js';

const API_BASE = '/api/chat/script/templates';
const ROLE_USER = 'user';
const ROLE_ASSISTANT = 'assistant';

// 页级状态.  每次 renderScriptsPage 重建 host 时整个替换; 子交互改
// state 后调 rerender() 局部刷新.
function makeState() {
  return {
    templates: [],        // meta 列表 (从 GET /script/templates)
    selectedName: null,   // 当前选中的 template name
    loadingDetails: false,
    details: null,        // { active, has_builtin, has_user, overriding_builtin }
    draft: null,          // 可变 form 状态 (用户在编辑器里改什么这里对应)
    baseline: null,       // 最近一次服务器同步后的副本, Reload 回到这里
    errors: [],           // 字段级 errors, 保存/校验后填充
  };
}

export async function renderScriptsPage(host) {
  host.innerHTML = '';
  const { openFolderButton } = await import('../_open_folder_btn.js');
  const header = el('div', {
    style: { display: 'flex', alignItems: 'baseline', gap: '12px', justifyContent: 'space-between' },
  });
  header.append(
    el('h2', { style: { margin: 0 } }, i18n('setup.scripts.heading')),
    openFolderButton('user_dialog_templates'),
  );
  host.append(
    header,
    el('p', { className: 'intro' }, i18n('setup.scripts.intro')),
  );

  const state = makeState();
  const pageRoot = el('div', { className: 'script-editor-root' });
  host.append(pageRoot);

  async function refreshList() {
    const res = await api.get(API_BASE.replace(/\/$/, ''));
    if (!res.ok) {
      state.templates = [];
      toast.err(i18n('setup.scripts.toast.list_failed'), { message: String(res.error || res.status) });
    } else {
      state.templates = res.data?.templates || [];
    }
    rerender();
  }

  async function loadDetails(name) {
    state.selectedName = name;
    state.loadingDetails = true;
    state.errors = [];
    rerender();
    const res = await api.get(`${API_BASE}/${encodeURIComponent(name)}`);
    state.loadingDetails = false;
    if (!res.ok) {
      state.details = null;
      state.draft = null;
      state.baseline = null;
      toast.err(i18n('setup.scripts.toast.load_failed'), { message: String(res.error || res.status) });
      rerender();
      return;
    }
    state.details = {
      active: res.data.template,
      has_builtin: !!res.data.has_builtin,
      has_user: !!res.data.has_user,
      overriding_builtin: !!res.data.overriding_builtin,
    };
    state.draft = templateToDraft(res.data.template);
    state.baseline = templateToDraft(res.data.template);  // 独立副本给 dirty 判断
    rerender();
  }

  function rerender() {
    pageRoot.innerHTML = '';
    pageRoot.append(
      renderToolbar(state, { refreshList, onNewBlank: () => newBlank(state, rerender, loadDetails) }),
      renderBody(state, { loadDetails, refreshList, rerender }),
    );
  }

  await refreshList();
}

// ── 顶部工具栏 ────────────────────────────────────────────────────

function renderToolbar(state, { refreshList, onNewBlank }) {
  return el('div', { className: 'script-editor-topbar' },
    el('button', {
      onClick: () => refreshList(),
    }, i18n('setup.scripts.buttons.refresh_list')),
    el('button', {
      className: 'primary',
      onClick: onNewBlank,
    }, i18n('setup.scripts.buttons.new_blank')),
    el('span', { className: 'script-editor-count' },
      i18n('setup.scripts.list.count_fmt', state.templates.length)),
  );
}

function newBlank(state, rerender, loadDetails) {
  // 让用户先输个 name 再进入编辑器, 比"空 name 进编辑器后再改"少一次卡壳.
  const suggested = suggestUniqueName(state.templates, 'my_script');
  const name = (window.prompt(i18n('setup.scripts.prompt.new_name'), suggested) || '').trim();
  if (!name) return;
  if (state.templates.some((t) => t.name === name && t.source === 'user')) {
    toast.warn(i18n('setup.scripts.toast.name_taken', name));
    return;
  }
  // 直接在本地构造一个空 draft, 不访问后端 (还没保存).  loadDetails 会被
  // 覆盖的副作用: selectedName = name.
  state.selectedName = name;
  state.details = {
    active: null,
    has_builtin: false,
    has_user: false,
    overriding_builtin: false,
  };
  state.draft = emptyDraft(name);
  state.baseline = null;  // 没有 baseline → 任何输入都算 dirty
  state.errors = [];
  rerender();
}

function suggestUniqueName(templates, base) {
  const taken = new Set(templates.map((t) => t.name));
  if (!taken.has(base)) return base;
  for (let i = 2; i < 1000; i++) {
    const candidate = `${base}_${i}`;
    if (!taken.has(candidate)) return candidate;
  }
  return `${base}_${Date.now()}`;
}

// ── 主体 = 左列表 + 右编辑器 ────────────────────────────────────

function renderBody(state, ctx) {
  return el('div', { className: 'script-editor-body' },
    renderList(state, ctx),
    renderEditor(state, ctx),
  );
}

// ── 左列: 模板列表 ───────────────────────────────────────────────

function renderList(state, ctx) {
  const list = el('div', { className: 'script-editor-list' });

  const userTemplates = state.templates.filter((t) => t.source === 'user');
  const builtinTemplates = state.templates.filter((t) => t.source === 'builtin');

  if (userTemplates.length > 0) {
    list.append(el('div', { className: 'subnav-group' },
      i18n('setup.scripts.list.user_group', userTemplates.length)));
    for (const t of userTemplates) list.append(renderListItem(t, state, ctx));
  }

  if (builtinTemplates.length > 0) {
    list.append(el('div', { className: 'subnav-group' },
      i18n('setup.scripts.list.builtin_group', builtinTemplates.length)));
    for (const t of builtinTemplates) list.append(renderListItem(t, state, ctx));
  }

  if (state.templates.length === 0) {
    list.append(el('div', { className: 'empty-state-compact' },
      i18n('setup.scripts.list.empty')));
  }

  return list;
}

function renderListItem(meta, state, ctx) {
  const isSelected = meta.name === state.selectedName && !meta._userHide;
  const isUser = meta.source === 'user';
  const classes = ['script-editor-list-item'];
  if (isSelected) classes.push('active');
  if (!isUser) classes.push('readonly');

  const badges = [];
  if (!isUser) {
    badges.push(el('span', { className: 'badge secondary' }, i18n('setup.scripts.list.badge_builtin')));
  }
  if (isUser && meta.overriding_builtin) {
    badges.push(el('span', { className: 'badge warn' }, i18n('setup.scripts.list.badge_overriding')));
  }

  const actions = [];
  if (!isUser) {
    actions.push(el('button', {
      className: 'small',
      onClick: (ev) => {
        ev.stopPropagation();
        duplicateBuiltin(meta.name, state, ctx);
      },
    }, i18n('setup.scripts.list.duplicate')));
  } else {
    actions.push(el('button', {
      className: 'danger small',
      onClick: (ev) => {
        ev.stopPropagation();
        deleteUserTemplate(meta.name, state, ctx);
      },
    }, i18n('setup.scripts.list.delete')));
  }

  return el('div', {
    className: classes.join(' '),
    onClick: () => ctx.loadDetails(meta.name),
  },
    el('div', {
      className: 'script-editor-list-item-title u-truncate',
      title: meta.name,
    }, meta.name),
    el('div', { className: 'script-editor-list-item-meta' },
      i18n('setup.scripts.list.turns_fmt', meta.turns_count),
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

async function duplicateBuiltin(sourceName, state, ctx) {
  const suggested = suggestUniqueName(state.templates, `${sourceName}_copy`);
  const target = (window.prompt(i18n('setup.scripts.prompt.duplicate_name', sourceName), suggested) || '').trim();
  if (!target) return;
  const res = await api.post(`${API_BASE}/duplicate`, {
    source_name: sourceName,
    target_name: target,
    overwrite: false,
  });
  if (!res.ok) {
    toast.err(i18n('setup.scripts.toast.duplicate_failed'),
      { message: res.error?.message || `HTTP ${res.status}` });
    return;
  }
  toast.ok(i18n('setup.scripts.toast.duplicated', target));
  await ctx.refreshList();
  await ctx.loadDetails(target);
  emit('scripts:templates_changed', {
    reason: 'duplicate',
    name: target,
    old_name: null,
  });
}

async function deleteUserTemplate(name, state, ctx) {
  const confirmed = window.confirm(i18n('setup.scripts.prompt.confirm_delete', name));
  if (!confirmed) return;
  const res = await api.delete(`${API_BASE}/${encodeURIComponent(name)}`);
  if (!res.ok) {
    toast.err(i18n('setup.scripts.toast.delete_failed'),
      { message: res.error?.message || `HTTP ${res.status}` });
    return;
  }
  toast.ok(i18n('setup.scripts.toast.deleted', name));
  for (const w of (res.data?.warnings || [])) toast.warn(w);
  if (res.data?.resurfaces_builtin) {
    toast.info(i18n('setup.scripts.toast.resurfaces_builtin', name));
  }
  if (state.selectedName === name) {
    state.selectedName = null;
    state.details = null;
    state.draft = null;
    state.baseline = null;
  }
  await ctx.refreshList();
  // If the deletion resurfaces a builtin, the Chat composer's dropdown is
  // still stale (same name, but different source tag); broadcast so the
  // composer re-fetches and re-labels accordingly.
  emit('scripts:templates_changed', {
    reason: 'delete',
    name: null,
    old_name: name,
  });
}

// ── 右列: 编辑器 ─────────────────────────────────────────────────

function renderEditor(state, ctx) {
  const pane = el('div', { className: 'script-editor-pane' });
  if (state.loadingDetails) {
    pane.append(el('div', { className: 'empty-state' }, i18n('setup.scripts.editor.loading')));
    return pane;
  }
  if (!state.details || !state.draft) {
    pane.append(renderEditorEmptyState());
    return pane;
  }

  const readonly = state.details.has_builtin && !state.details.has_user
    && state.draft.name === state.details.active?.name;
  // readonly 判据: 只有 builtin 存在, 且还没在 user 目录落过自己的版本.
  // 新建空白草稿时 details.has_builtin=false, 所以不会误判为 readonly.

  // NB: Node.prototype.append(null) 会把 "null" 当文本塞进 DOM — 不是跳过,
  // 所以对可能返回 null 的子渲染函数必须 guard.  el() helper 自带 null 过
  // 滤但只对它的 children 参数有效, 走 .append 链路时要自己挡.
  pane.append(renderEditorHeader(state, ctx, readonly));
  pane.append(renderEditorBasicFields(state, readonly));
  pane.append(renderEditorBootstrap(state, readonly));
  pane.append(renderEditorTurns(state, ctx, readonly));
  const errorsNode = renderEditorErrors(state);
  if (errorsNode) pane.append(errorsNode);
  return pane;
}

function renderEditorEmptyState() {
  return el('div', { className: 'empty-state' },
    el('h3', {}, i18n('setup.scripts.editor.empty_title')),
    el('p', {}, i18n('setup.scripts.editor.empty_hint')),
  );
}

function renderEditorHeader(state, ctx, readonly) {
  const dirty = isDirty(state);
  const header = el('div', { className: 'script-editor-header' });
  const title = el('div', { className: 'script-editor-header-title' },
    el('h3', {}, state.draft.name || i18n('setup.scripts.editor.untitled')),
  );
  if (readonly) {
    title.append(el('span', { className: 'badge secondary' },
      i18n('setup.scripts.editor.readonly_badge')));
  } else if (dirty) {
    title.append(el('span', { className: 'badge warn' },
      i18n('setup.scripts.editor.dirty_badge')));
  }
  if (state.details.overriding_builtin) {
    title.append(el('span', { className: 'badge warn' },
      i18n('setup.scripts.list.badge_overriding')));
  }
  header.append(title);

  // 工具栏: readonly 只有 Duplicate + Export; 可编辑时全套.
  const buttons = el('div', { className: 'script-editor-header-actions' });
  if (readonly) {
    buttons.append(
      el('button', {
        className: 'primary',
        onClick: () => duplicateBuiltin(state.draft.name, state, ctx),
      }, i18n('setup.scripts.list.duplicate')),
      el('button', {
        onClick: () => exportDraft(state),
      }, i18n('setup.scripts.buttons.export')),
    );
  } else {
    buttons.append(
      el('button', {
        className: 'primary' + (dirty ? ' is-dirty' : ''),
        onClick: () => saveDraft(state, ctx),
      }, i18n('setup.scripts.buttons.save')),
      el('button', {
        onClick: () => reloadDraft(state, ctx),
        disabled: !dirty || !state.baseline,
      }, i18n('setup.scripts.buttons.reload')),
      el('button', {
        onClick: () => exportDraft(state),
      }, i18n('setup.scripts.buttons.export')),
    );
    if (state.details.has_user) {
      // 新建空白草稿 (has_user=false) 时不挂 Delete — 还没有磁盘上的
      // 东西可删. 之前写 ternary `... : null` 走 buttons.append 会把 "null"
      // 当字符串塞进 DOM, 改成 if 分支.
      buttons.append(el('button', {
        className: 'danger',
        onClick: () => deleteUserTemplate(state.draft.name, state, ctx),
      }, i18n('setup.scripts.list.delete')));
    }
  }
  header.append(buttons);

  if (readonly) {
    header.append(el('p', { className: 'script-editor-header-hint' },
      i18n('setup.scripts.editor.readonly_hint')));
  }

  return header;
}

function renderEditorBasicFields(state, readonly) {
  const card = el('div', { className: 'script-editor-card' });
  card.append(el('h4', {}, i18n('setup.scripts.editor.basic_heading')));

  const nameInput = el('input', {
    type: 'text',
    value: state.draft.name,
    disabled: readonly,
    onInput: (ev) => { state.draft.name = ev.target.value.trim(); },
  });
  card.append(field(i18n('setup.scripts.editor.fields.name'), nameInput, {
    hint: readonly
      ? i18n('setup.scripts.editor.hints.name_readonly')
      : i18n('setup.scripts.editor.hints.name'),
  }));

  const descInput = el('input', {
    type: 'text',
    value: state.draft.description || '',
    disabled: readonly,
    onInput: (ev) => { state.draft.description = ev.target.value; },
  });
  card.append(field(i18n('setup.scripts.editor.fields.description'), descInput));

  const hintArea = el('textarea', {
    rows: 2,
    disabled: readonly,
    onInput: (ev) => { state.draft.user_persona_hint = ev.target.value; },
  });
  hintArea.value = state.draft.user_persona_hint || '';
  card.append(field(i18n('setup.scripts.editor.fields.user_persona_hint'), hintArea, {
    hint: i18n('setup.scripts.editor.hints.user_persona_hint'),
    wide: true,
  }));

  return card;
}

function renderEditorBootstrap(state, readonly) {
  const card = el('div', { className: 'script-editor-card' });
  card.append(
    el('h4', {}, i18n('setup.scripts.editor.bootstrap_heading')),
    el('p', { className: 'hint' }, i18n('setup.scripts.editor.bootstrap_intro')),
  );

  const row = el('div', { className: 'script-editor-bootstrap-row' });

  const vnowInput = el('input', {
    type: 'text',
    placeholder: '2025-01-01T09:00',
    value: state.draft.bootstrap?.virtual_now || '',
    disabled: readonly,
    onInput: (ev) => {
      state.draft.bootstrap = state.draft.bootstrap || {};
      state.draft.bootstrap.virtual_now = ev.target.value.trim();
    },
  });
  row.append(field(i18n('setup.scripts.editor.fields.virtual_now'), vnowInput, {
    hint: i18n('setup.scripts.editor.hints.virtual_now'),
  }));

  const gapInput = el('input', {
    type: 'number',
    step: '1',
    min: '0',
    placeholder: '10',
    value: state.draft.bootstrap?.last_gap_minutes ?? '',
    disabled: readonly,
    onInput: (ev) => {
      state.draft.bootstrap = state.draft.bootstrap || {};
      const raw = ev.target.value.trim();
      state.draft.bootstrap.last_gap_minutes = raw === '' ? null : Number(raw);
    },
  });
  row.append(field(i18n('setup.scripts.editor.fields.last_gap_minutes'), gapInput, {
    hint: i18n('setup.scripts.editor.hints.last_gap_minutes'),
  }));

  card.append(row);
  return card;
}

// ── turns 编辑区 ────────────────────────────────────────────────

function renderEditorTurns(state, ctx, readonly) {
  const card = el('div', { className: 'script-editor-card' });
  const header = el('div', { className: 'script-editor-turns-header' },
    el('h4', {}, i18n('setup.scripts.editor.turns_heading',
      state.draft.turns.length)),
  );
  if (!readonly) {
    header.append(
      el('div', { className: 'script-editor-turns-actions' },
        el('button', {
          className: 'small',
          onClick: () => {
            state.draft.turns.push({ role: ROLE_USER, content: '', time: null });
            ctx.rerender();
          },
        }, i18n('setup.scripts.editor.buttons.add_user')),
        el('button', {
          className: 'small',
          onClick: () => {
            state.draft.turns.push({ role: ROLE_ASSISTANT, expected: '' });
            ctx.rerender();
          },
        }, i18n('setup.scripts.editor.buttons.add_assistant')),
      ),
    );
  }
  card.append(header);

  if (state.draft.turns.length === 0) {
    card.append(el('div', { className: 'empty-state tiny' },
      i18n('setup.scripts.editor.turns_empty')));
  }

  const turnsWrap = el('div', { className: 'script-editor-turns' });
  state.draft.turns.forEach((t, idx) => {
    turnsWrap.append(renderTurnCard(t, idx, state, ctx, readonly));
  });
  card.append(turnsWrap);
  return card;
}

function renderTurnCard(turn, idx, state, ctx, readonly) {
  const turnErrors = state.errors.filter((e) => e.path.startsWith(`turns[${idx}]`));
  const card = el('div', {
    className: 'script-editor-turn-card'
      + (turn.role === ROLE_ASSISTANT ? ' turn-assistant' : ' turn-user')
      + (turnErrors.length ? ' has-error' : ''),
  });

  const head = el('div', { className: 'script-editor-turn-head' });
  head.append(
    el('span', { className: 'script-editor-turn-idx' }, `#${idx + 1}`),
  );
  const roleSel = el('select', {
    disabled: readonly,
    onChange: (ev) => {
      const newRole = ev.target.value;
      turn.role = newRole;
      if (newRole === ROLE_USER) {
        turn.content = turn.content || '';
        delete turn.expected;
      } else {
        turn.expected = turn.expected || '';
        delete turn.content;
        turn.time = null;
      }
      ctx.rerender();
    },
  },
    el('option', { value: ROLE_USER, selected: turn.role === ROLE_USER }, i18n('setup.scripts.editor.role_user')),
    el('option', { value: ROLE_ASSISTANT, selected: turn.role === ROLE_ASSISTANT }, i18n('setup.scripts.editor.role_assistant')),
  );
  roleSel.value = turn.role;
  head.append(roleSel);

  if (!readonly) {
    head.append(
      el('div', { className: 'script-editor-turn-actions' },
        el('button', {
          className: 'small',
          disabled: idx === 0,
          onClick: () => {
            swap(state.draft.turns, idx, idx - 1);
            ctx.rerender();
          },
        }, '↑'),
        el('button', {
          className: 'small',
          disabled: idx === state.draft.turns.length - 1,
          onClick: () => {
            swap(state.draft.turns, idx, idx + 1);
            ctx.rerender();
          },
        }, '↓'),
        el('button', {
          className: 'danger small',
          onClick: () => {
            state.draft.turns.splice(idx, 1);
            ctx.rerender();
          },
        }, '×'),
      ),
    );
  }
  card.append(head);

  if (turn.role === ROLE_USER) {
    const contentArea = el('textarea', {
      rows: 3,
      placeholder: i18n('setup.scripts.editor.placeholders.user_content'),
      disabled: readonly,
      onInput: (ev) => { turn.content = ev.target.value; },
    });
    contentArea.value = turn.content || '';
    card.append(field(i18n('setup.scripts.editor.fields.user_content'), contentArea, { wide: true }));
    card.append(renderTurnTimeRow(turn, readonly));
  } else {
    const expectedArea = el('textarea', {
      rows: 4,
      placeholder: i18n('setup.scripts.editor.placeholders.assistant_expected'),
      disabled: readonly,
      onInput: (ev) => { turn.expected = ev.target.value; },
    });
    expectedArea.value = turn.expected || '';
    card.append(field(i18n('setup.scripts.editor.fields.assistant_expected'), expectedArea, {
      hint: i18n('setup.scripts.editor.hints.assistant_expected'),
      wide: true,
    }));
  }

  if (turnErrors.length) {
    card.append(el('div', { className: 'script-editor-turn-errors' },
      ...turnErrors.map((e) => el('div', {}, `${e.path}: ${e.message}`))));
  }

  return card;
}

function renderTurnTimeRow(turn, readonly) {
  const row = el('div', { className: 'script-editor-bootstrap-row' });

  const advInput = el('input', {
    type: 'text',
    placeholder: '5m / 1h30m / 2d',
    value: turn.time?.advance || '',
    disabled: readonly,
    onInput: (ev) => {
      const raw = ev.target.value.trim();
      if (!raw) {
        if (turn.time) delete turn.time.advance;
        if (turn.time && !turn.time.advance && !turn.time.at && !turn.time.advance_seconds) {
          turn.time = null;
        }
        return;
      }
      turn.time = turn.time || {};
      turn.time.advance = raw;
    },
  });
  row.append(field(i18n('setup.scripts.editor.fields.time_advance'), advInput, {
    hint: i18n('setup.scripts.editor.hints.time_advance'),
  }));

  const atInput = el('input', {
    type: 'text',
    placeholder: '2025-01-01T09:05',
    value: turn.time?.at || '',
    disabled: readonly,
    onInput: (ev) => {
      const raw = ev.target.value.trim();
      if (!raw) {
        if (turn.time) delete turn.time.at;
        if (turn.time && !turn.time.advance && !turn.time.at && !turn.time.advance_seconds) {
          turn.time = null;
        }
        return;
      }
      turn.time = turn.time || {};
      turn.time.at = raw;
    },
  });
  row.append(field(i18n('setup.scripts.editor.fields.time_at'), atInput, {
    hint: i18n('setup.scripts.editor.hints.time_at'),
  }));

  return row;
}

function renderEditorErrors(state) {
  if (!state.errors.length) return null;
  // 只展示顶层错误; 每 turn 的错误已经贴在对应 turn card 里了, 这里只收集
  // path = '' / 'name' / 'bootstrap.*' 这种非 turn 级的.
  const topLevel = state.errors.filter((e) => !e.path.startsWith('turns['));
  if (!topLevel.length) return null;
  return el('div', { className: 'script-editor-errors' },
    el('h4', {}, i18n('setup.scripts.editor.errors_heading')),
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
  // 改 name 时先准备好"走 Save As 的删旧逻辑".  baseline 可能是 null
  // (新建), 那就没有旧 name 要删.
  const oldName = state.baseline?.name;
  const newName = payload.name;
  const renaming = oldName && oldName !== newName;

  const res = await api.post(API_BASE, payload);
  if (!res.ok) {
    if (Array.isArray(res.error?.errors) && res.error.errors.length > 0) {
      state.errors = res.error.errors;
      ctx.rerender();
      toast.err(i18n('setup.scripts.toast.save_errors', state.errors.length));
    } else {
      toast.err(i18n('setup.scripts.toast.save_failed'),
        { message: res.error?.message || `HTTP ${res.status}` });
    }
    return;
  }

  // 新 name 存盘成功了.  如果是改名, 把旧 name 的 user 版本删掉 (保证文件
  // 名和 name 一致).  删失败不回滚, 给 warning.
  if (renaming && state.details?.has_user) {
    const delRes = await api.delete(`${API_BASE}/${encodeURIComponent(oldName)}`);
    if (!delRes.ok) {
      toast.warn(i18n('setup.scripts.toast.rename_left_old', oldName));
    }
  }

  state.errors = [];
  toast.ok(i18n('setup.scripts.toast.saved', newName));
  if (res.data.overriding_builtin) {
    toast.info(i18n('setup.scripts.toast.now_overriding_builtin', newName));
  }
  await ctx.refreshList();
  await ctx.loadDetails(newName);
  // Broadcast to Chat composer (and anyone else caching template list) so
  // the Script-mode dropdown reflects the save without the tester having to
  // click [刷新列表]. Covers rename (Save As) too because `renaming` above
  // already deleted the old name before we reach here.
  emit('scripts:templates_changed', {
    reason: renaming ? 'rename' : 'save',
    name: newName,
    old_name: renaming ? oldName : null,
  });
}

function reloadDraft(state, ctx) {
  if (!state.baseline) return;
  state.draft = JSON.parse(JSON.stringify(state.baseline));
  state.errors = [];
  ctx.rerender();
}

function exportDraft(state) {
  const payload = draftToPayload(state.draft);
  const blob = new Blob(
    [JSON.stringify(payload, null, 2) + '\n'],
    { type: 'application/json' },
  );
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = `${payload.name || 'script'}.json`;
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

// ── 数据形状转换 ─────────────────────────────────────────────────

// 从后端返回的规范化模板 (已 drop user 的 expected / assistant 的 time)
// 变为 UI draft.  draft 里允许"半填"状态, 例如 time 字段只填了 advance
// 没填 at, 保存时由服务端再 normalize 一次.
function templateToDraft(tmpl) {
  if (!tmpl) return null;
  return {
    name: tmpl.name || '',
    description: tmpl.description || '',
    user_persona_hint: tmpl.user_persona_hint || '',
    bootstrap: tmpl.bootstrap ? { ...tmpl.bootstrap } : null,
    turns: (tmpl.turns || []).map((t) => ({
      role: t.role,
      content: t.role === ROLE_USER ? (t.content || '') : undefined,
      expected: t.role === ROLE_ASSISTANT ? (t.expected || '') : undefined,
      time: t.role === ROLE_USER && t.time ? { ...t.time } : null,
    })),
  };
}

function emptyDraft(name) {
  return {
    name,
    description: '',
    user_persona_hint: '',
    bootstrap: null,
    turns: [
      { role: ROLE_USER, content: '', time: null },
      { role: ROLE_ASSISTANT, expected: '' },
    ],
  };
}

// Draft → 后端 POST /templates 的 payload.  额外清理: 空 bootstrap 变 null,
// time dict 里的空值 drop 掉 (避免后端把 "" 当成错误 ISO 时间).
function draftToPayload(draft) {
  const payload = {
    name: (draft.name || '').trim(),
    description: (draft.description || '').trim(),
    user_persona_hint: (draft.user_persona_hint || '').trim(),
    turns: (draft.turns || []).map((t) => {
      const out = { role: t.role };
      if (t.role === ROLE_USER) {
        out.content = t.content || '';
        if (t.time) {
          const cleanedTime = {};
          if (t.time.advance) cleanedTime.advance = t.time.advance;
          if (t.time.at) cleanedTime.at = t.time.at;
          if (t.time.advance_seconds) cleanedTime.advance_seconds = t.time.advance_seconds;
          if (Object.keys(cleanedTime).length) out.time = cleanedTime;
        }
      } else {
        if (t.expected) out.expected = t.expected;
      }
      return out;
    }),
  };
  if (draft.bootstrap) {
    const b = {};
    if (draft.bootstrap.virtual_now) b.virtual_now = draft.bootstrap.virtual_now;
    if (draft.bootstrap.last_gap_minutes != null && draft.bootstrap.last_gap_minutes !== '') {
      b.last_gap_minutes = Number(draft.bootstrap.last_gap_minutes);
    }
    if (Object.keys(b).length) payload.bootstrap = b;
  }
  return payload;
}

function isDirty(state) {
  if (!state.baseline) return !!state.draft;  // 新建态 → 总是 dirty
  return JSON.stringify(draftToPayload(state.draft)) !== JSON.stringify(draftToPayload(state.baseline));
}

function swap(arr, i, j) {
  const tmp = arr[i];
  arr[i] = arr[j];
  arr[j] = tmp;
}
