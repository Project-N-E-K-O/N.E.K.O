/**
 * memory_editor.js — Setup → Memory 四子页共用容器 (P07 + P11 结构化视图补丁).
 *
 * 为什么拆成三文件:
 *   原单文件 (~255 行) 把 "取数 / textarea UI / 校验" 全塞一起, 测试人员反映两
 *   件事: 1) toast 调用漏点了函数导致保存后崩; 2) 让测试人员手写 JSON schema 是
 *   在浪费测试人员时间——比如 persona.json 顶层是 `dict[entity -> {facts:[...]}]`,
 *   但文案里只说了 "顶层 dict", 填个空 `{}` 算合法但实际没意义. 所以改成双视图:
 *     - **Structured** (默认): 按 kind 渲染表单卡片, 点 "+" 就拿默认模板建条目,
 *       常见字段直接输入框, 低频字段 (id/hash/created_at/...) 折叠在 [高级 ▾]
 *       里保留可改但不干扰.
 *     - **Raw JSON**: 保留原大 textarea, 应对 "格式化工具覆盖不了的罕见情况",
 *       比如故意测 legacy 字段 / 畸形载荷 / 特殊 unicode.
 *
 * 双视图状态同步:
 *   state.model 是权威 JS 对象; structured 视图直接改 model; raw 视图改 textarea
 *   里文本, 切回 structured 时 parse 一次灌回 model (parse 失败拒绝切换, 强制用户
 *   先修好 Raw). Save 按当前视图抽 model 送后端.
 *
 * dirty 计算用 canonical(model) !== baseline, 两视图用同一逻辑, 不会因为切视图
 * 产生伪 dirty.
 *
 * 约定的调用形式见本文件末尾 `renderMemoryEditor(host, kind)`.
 */

import { api } from '../../core/api.js';
import { i18n } from '../../core/i18n.js';
import { toast } from '../../core/toast.js';
import { el } from '../_dom.js';
import { renderRawView } from './memory_editor_raw.js';
import { renderStructuredView } from './memory_editor_structured.js';
import { renderTriggerPanel } from './memory_trigger_panel.js';

// ── public entry ─────────────────────────────────────────────────────

/**
 * @param {HTMLElement} host - subpage root
 * @param {'recent'|'facts'|'reflections'|'persona'} kind
 */
export async function renderMemoryEditor(host, kind) {
  host.innerHTML = '';
  host.append(
    el('h2', {}, i18n(`setup.memory.editor.${kind}.heading`)),
    el('p', { className: 'muted' }, i18n(`setup.memory.editor.${kind}.intro`)),
  );

  // expectedStatuses: 404=无会话, 409=无角色 — 两者都是"正常空态"而非错误.
  const res = await api.get(`/api/memory/${kind}`, { expectedStatuses: [404, 409] });
  if (res.status === 404) {
    host.append(renderEmpty('no_session'));
    return;
  }
  if (res.status === 409) {
    host.append(renderEmpty('no_character'));
    return;
  }
  if (!res.ok) {
    host.append(el('div', { className: 'empty-state err' },
      `${i18n('errors.unknown')}: ${res.error?.message || res.status}`));
    return;
  }

  mountEditor(host, kind, res.data);
}

// ── empty states (无会话 / 无角色) ─────────────────────────────────────

function renderEmpty(key) {
  return el('div', { className: 'empty-state' },
    el('h3', {}, i18n(`setup.memory.${key}.heading`)),
    el('p', {}, i18n(`setup.memory.${key}.body`)),
  );
}

// ── main container (tabs + toolbar + mounts current view) ────────────

const VIEW_STORAGE_PREFIX = 'testbench.memory_editor.view.';

function readPreferredView(kind) {
  try {
    const v = sessionStorage.getItem(VIEW_STORAGE_PREFIX + kind);
    return v === 'raw' ? 'raw' : 'structured';
  } catch { return 'structured'; }
}

function writePreferredView(kind, view) {
  try { sessionStorage.setItem(VIEW_STORAGE_PREFIX + kind, view); } catch { /* ignore */ }
}

/** 与原实现保持一致的序列化 (2-space indent, insertion order). */
function canonical(value) { return JSON.stringify(value, null, 2); }

function mountEditor(host, kind, snapshot) {
  /**
   * Shared state across tabs:
   *   model        — 权威 JS 值 (list / dict); structured 视图直接改它.
   *   baseline     — 上次 Save/Reload 后的 canonical(model); dirty = canonical(model) !== baseline.
   *   rawText      — Raw 视图 textarea 当前值 (view=raw 时是权威, 其它时候滞后).
   *   rawValid     — Raw 文本 parse 是否合法 (只在 view=raw 时有意义).
   *   view         — 'structured' | 'raw'.
   *   meta         — 最近一次服务端返回的 {kind,path,exists,data,character_name}.
   * 非状态的 UI 节点放 ui.{...}.
   */
  const state = {
    kind,
    model: snapshot.data,
    baseline: canonical(snapshot.data),
    rawText: canonical(snapshot.data),
    rawValid: true,
    view: readPreferredView(kind),
    meta: snapshot,
  };

  // ── 顶部元信息 ─────────────────────────────────────────────────────
  const metaLine = el('div', { className: 'meta-card-row' });
  const badgeRow = el('div', { className: 'meta-card-row', style: { marginTop: '8px' } });
  const countBadge = el('span', { className: 'badge secondary' });
  const validityBadge = el('span', { className: 'badge' });
  const dirtyBadge = el('span', { className: 'badge' });
  badgeRow.append(countBadge, validityBadge, dirtyBadge);

  // ── Tab switcher ───────────────────────────────────────────────────
  const tabStructured = el('button', { className: 'tab' }, i18n('setup.memory.editor.tabs.structured'));
  const tabRaw = el('button', { className: 'tab' }, i18n('setup.memory.editor.tabs.raw'));
  const tabRow = el('div', { className: 'memory-editor-tabs' }, tabStructured, tabRaw);

  // ── View mount point ───────────────────────────────────────────────
  const viewHost = el('div', { className: 'memory-editor-view' });

  // ── 工具条按钮 ─────────────────────────────────────────────────────
  const saveBtn = el('button', { className: 'primary' }, i18n('setup.memory.editor.buttons.save'));
  const reloadBtn = el('button', {}, i18n('setup.memory.editor.buttons.reload'));
  const revertBtn = el('button', {}, i18n('setup.memory.editor.buttons.revert'));
  const statusLine = el('div', { className: 'muted tiny', style: { marginTop: '4px' } });

  host.append(
    metaLine,
    badgeRow,
    tabRow,
    viewHost,
    statusLine,
    el('div', { className: 'form-row', style: { marginTop: '8px' } },
      saveBtn, reloadBtn, revertBtn,
    ),
  );

  // ── P10: 触发操作面板 (放在工具条下方, 作为独立 section 不参与 dirty) ───
  //
  // commit 成功后由面板回调触发 "reload" 逻辑, 保证编辑器里的数据与磁盘同步
  // (不复用 reloadBtn 的 click handler, 因为那里会跳 confirm_overwrite; 这里
  // 是我们主动触发的写入, 测试人员已经明确 Accept 过, 不该再弹框).
  const triggerHost = el('div', { className: 'memory-trigger-host' });
  host.append(triggerHost);
  renderTriggerPanel(triggerHost, kind, {
    onCommitted: async () => {
      const res = await api.get(`/api/memory/${kind}`, { expectedStatuses: [404, 409] });
      if (!res.ok) {
        statusLine.textContent = res.error?.message || `HTTP ${res.status}`;
        return;
      }
      state.meta = res.data;
      state.model = res.data.data;
      state.baseline = canonical(state.model);
      state.rawText = state.baseline;
      state.rawValid = true;
      renderMeta();
      mountView();
      statusLine.textContent = i18n('setup.memory.trigger.reloaded_after_commit');
    },
  });

  // ── badge / meta renderers ────────────────────────────────────────
  function renderMeta() {
    metaLine.innerHTML = '';
    metaLine.append(
      el('b', {}, `${i18n('setup.memory.editor.path_label')}: `),
      el('code', {}, state.meta.path),
      ' ',
      el('span', { className: state.meta.exists ? 'badge primary' : 'badge warn' },
        state.meta.exists
          ? i18n('setup.memory.editor.exists_badge')
          : i18n('setup.memory.editor.not_exists_badge')),
    );
  }

  function renderValidityBadge() {
    // Raw 视图下可能是 invalid; structured 视图 model 肯定合法 (类型是 obj).
    if (state.view === 'raw' && !state.rawValid) {
      validityBadge.className = 'badge err';
      validityBadge.textContent = state.rawValidMsg || i18n('setup.memory.editor.invalid', '?');
      countBadge.style.display = 'none';
      return;
    }
    validityBadge.className = 'badge primary';
    validityBadge.textContent = i18n('setup.memory.editor.valid');
    const count = countItems(state.model);
    if (count != null) {
      countBadge.style.display = '';
      countBadge.textContent = Array.isArray(state.model)
        ? i18n('setup.memory.editor.count_list', count)
        : i18n('setup.memory.editor.count_dict', count);
    } else {
      countBadge.style.display = 'none';
    }
  }

  function isDirty() {
    if (state.view === 'raw') return state.rawText !== state.baseline;
    return canonical(state.model) !== state.baseline;
  }

  function renderDirtyBadge() {
    const dirty = isDirty();
    if (dirty) {
      dirtyBadge.className = 'badge warn';
      dirtyBadge.textContent = i18n('setup.memory.editor.dirty_badge');
      dirtyBadge.style.display = '';
    } else {
      dirtyBadge.style.display = 'none';
    }
    const canSave = dirty && (state.view === 'structured' || state.rawValid);
    saveBtn.disabled = !canSave;
    revertBtn.disabled = !dirty;
  }

  function renderBadges() { renderValidityBadge(); renderDirtyBadge(); }

  // ── view mount ────────────────────────────────────────────────────
  function mountView() {
    viewHost.innerHTML = '';
    tabStructured.classList.toggle('active', state.view === 'structured');
    tabRaw.classList.toggle('active', state.view === 'raw');

    if (state.view === 'raw') {
      renderRawView(viewHost, state, {
        onTextChanged: (text, parsed) => {
          state.rawText = text;
          if (parsed.ok) {
            state.rawValid = true;
            state.rawValidMsg = null;
            // 同步到 model, 方便切 structured 时不丢失输入.
            state.model = parsed.value;
          } else {
            state.rawValid = false;
            state.rawValidMsg = parsed.message;
          }
          renderBadges();
        },
      });
    } else {
      renderStructuredView(viewHost, state, {
        onModelChanged: () => {
          // structured 视图改了 model 后同步到 rawText 让后续切过去无感.
          state.rawText = canonical(state.model);
          state.rawValid = true;
          renderBadges();
        },
      });
    }
    renderBadges();
  }

  // ── tab clicks ────────────────────────────────────────────────────
  tabStructured.addEventListener('click', () => {
    if (state.view === 'structured') return;
    // 从 Raw 切过来前必须先 parse 成功.
    if (!state.rawValid) {
      toast.err(i18n('setup.memory.editor.tab_switch_blocked', state.rawValidMsg || '?'));
      return;
    }
    state.view = 'structured';
    writePreferredView(kind, state.view);
    mountView();
  });
  tabRaw.addEventListener('click', () => {
    if (state.view === 'raw') return;
    state.rawText = canonical(state.model);
    state.rawValid = true;
    state.view = 'raw';
    writePreferredView(kind, state.view);
    mountView();
  });

  // ── toolbar buttons ───────────────────────────────────────────────
  saveBtn.addEventListener('click', async () => {
    // 以当前视图的有效 model 为准. structured 永远有效; raw 下要求 rawValid.
    if (state.view === 'raw') {
      if (!state.rawValid) return;
      try { state.model = JSON.parse(state.rawText); }
      catch (exc) { toast.err(String(exc.message || exc)); return; }
    }
    saveBtn.disabled = true;
    statusLine.textContent = i18n('setup.memory.editor.saving');
    const res = await api.put(`/api/memory/${kind}`, { data: state.model });
    if (!res.ok) {
      statusLine.textContent = res.error?.message || `HTTP ${res.status}`;
      renderBadges();
      return;
    }
    state.meta = res.data;
    state.model = res.data.data;
    state.baseline = canonical(state.model);
    state.rawText = state.baseline;
    state.rawValid = true;
    renderMeta();
    mountView();
    statusLine.textContent = i18n('setup.memory.editor.saved');
    toast.ok(i18n('setup.memory.editor.saved'));
  });

  reloadBtn.addEventListener('click', async () => {
    if (isDirty() && !window.confirm(i18n('setup.memory.editor.confirm_overwrite'))) return;
    statusLine.textContent = i18n('setup.memory.editor.reloading');
    const res = await api.get(`/api/memory/${kind}`, { expectedStatuses: [404, 409] });
    if (!res.ok) {
      statusLine.textContent = res.error?.message || `HTTP ${res.status}`;
      return;
    }
    state.meta = res.data;
    state.model = res.data.data;
    state.baseline = canonical(state.model);
    state.rawText = state.baseline;
    state.rawValid = true;
    renderMeta();
    mountView();
    statusLine.textContent = i18n('setup.memory.editor.reloaded');
  });

  revertBtn.addEventListener('click', () => {
    try { state.model = JSON.parse(state.baseline); }
    catch { state.model = Array.isArray(state.model) ? [] : {}; }
    state.rawText = state.baseline;
    state.rawValid = true;
    mountView();
    statusLine.textContent = '';
  });

  // ── initial paint ────────────────────────────────────────────────
  renderMeta();
  mountView();
}

// ── helpers ──────────────────────────────────────────────────────────

/** "几条 / 几个 entity" 徽章数值; 非 list/dict 返回 null. */
function countItems(value) {
  if (Array.isArray(value)) return value.length;
  if (value && typeof value === 'object') return Object.keys(value).length;
  return null;
}
