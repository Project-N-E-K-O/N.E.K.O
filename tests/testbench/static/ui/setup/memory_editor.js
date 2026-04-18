/**
 * memory_editor.js — 共用 JSON-file 编辑器 (P07 · Setup → Memory 四子页).
 *
 * 为什么一个 helper 顶四页:
 *   四个 memory 文件 (recent / facts / reflections / persona) 在 P07 的交互
 *   形态完全一致: 读 → 大 textarea 显示格式化 JSON → 编辑 → 校验 → 保存.
 *   PLAN 里提到的"Facts 表格编辑 / Reflections 两列" 等富编辑是 P10 记忆操作
 *   触发落地后再叠在这个 raw-editor 之上, 所以 P07 先把通用底盘做扎实.
 *
 * 设计取舍:
 *   - **单大文本框 + 实时校验徽章**: 比结构化表单灵活得多 (测试人员可以刻意
 *     构造畸形载荷来探 pipeline 的容错), 同时避免 4 份表单代码重复.
 *   - **"上次加载"快照**: `baseline` 记录磁盘最后一次返回的内容, Revert 回
 *     到这里. Reload 会重新请求后端并覆盖 baseline (与 Revert 语义区分开).
 *   - **Save 按钮只有 JSON 合法 + dirty 才亮**: 避免把显式未改的文件空转一次
 *     写入更新 mtime.
 *   - **后端 `exists=false` 时**: 文本框显示 `[]` 或 `{}` 占位, 保存一次即落
 *     盘. 这样"磁盘上还没有文件"的情况也不用特殊提示.
 *
 * 约定的调用形式见本文件末尾 `export function renderMemoryEditor(host, kind)`.
 */

import { api } from '../../core/api.js';
import { i18n } from '../../core/i18n.js';
import { toast } from '../../core/toast.js';
import { el } from '../_dom.js';

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

  // expectedStatuses: 404=无会话, 409=无角色 — 两者都是\u201c正常空态\u201d而非错误.
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

  renderEditor(host, kind, res.data);
}

// ── empty states (无会话 / 无角色) ─────────────────────────────────────

function renderEmpty(key) {
  return el('div', { className: 'empty-state' },
    el('h3', {}, i18n(`setup.memory.${key}.heading`)),
    el('p', {}, i18n(`setup.memory.${key}.body`)),
  );
}

// ── main editor ──────────────────────────────────────────────────────

function renderEditor(host, kind, snapshot) {
  // 序列化一次作为初始 textarea 值, 同时作为 "上次加载" 的 baseline.
  let baseline = stringify(snapshot.data);

  // 顶部元信息条: 文件路径 / exists / size / mtime / count.
  const metaLine = el('div', { className: 'meta-card-row' });
  const statusLine = el('div', { className: 'muted tiny', style: { marginTop: '4px' } });

  // 文本编辑器本体.
  const textarea = el('textarea', {
    className: 'json-editor',
    spellcheck: false,
    value: baseline,
  });
  // 校验 + dirty 徽章 (跟随 textarea 上方右侧).
  const validityBadge = el('span', { className: 'badge' });
  const dirtyBadge = el('span', { className: 'badge' }); // 只在 dirty 时显示.
  const countBadge = el('span', { className: 'badge secondary' });

  // 工具条按钮.
  const saveBtn = el('button', { className: 'primary' },
    i18n('setup.memory.editor.buttons.save'));
  const reloadBtn = el('button', {}, i18n('setup.memory.editor.buttons.reload'));
  const formatBtn = el('button', {}, i18n('setup.memory.editor.buttons.format'));
  const revertBtn = el('button', {}, i18n('setup.memory.editor.buttons.revert'));

  // ── 构建 DOM ─────────────────────────────────────────────────────
  host.append(
    metaLine,
    el('div', { className: 'meta-card-row', style: { marginTop: '8px' } },
      countBadge, validityBadge, dirtyBadge,
    ),
    textarea,
    statusLine,
    el('div', { className: 'form-row', style: { marginTop: '8px' } },
      saveBtn, reloadBtn, formatBtn, revertBtn,
    ),
  );

  // ── 状态更新 ─────────────────────────────────────────────────────
  const state = {
    baseline,         // 上次加载后的 JSON 文本
    lastMeta: snapshot,  // {kind, path, exists, data, character_name}
    lastValid: true,
    lastParsed: snapshot.data,
  };

  function updateMeta(meta) {
    metaLine.innerHTML = '';
    metaLine.append(
      el('b', {}, `${i18n('setup.memory.editor.path_label')}: `),
      el('code', {}, meta.path),
      ' ',
      el('span', { className: meta.exists ? 'badge primary' : 'badge warn' },
        meta.exists
          ? i18n('setup.memory.editor.exists_badge')
          : i18n('setup.memory.editor.not_exists_badge')),
    );
  }

  function updateValidity() {
    const text = textarea.value;
    let parsed, ok;
    try {
      parsed = JSON.parse(text);
      ok = true;
    } catch (exc) {
      ok = false;
      state.lastValid = false;
      validityBadge.className = 'badge err';
      const brief = String(exc.message || exc).split('\n')[0].slice(0, 60);
      validityBadge.textContent = i18n('setup.memory.editor.invalid', brief);
      countBadge.textContent = '';
      countBadge.style.display = 'none';
      updateDirtyAndSave();
      return;
    }
    state.lastValid = true;
    state.lastParsed = parsed;
    validityBadge.className = 'badge primary';
    validityBadge.textContent = i18n('setup.memory.editor.valid');
    const count = countItems(parsed);
    if (count != null) {
      countBadge.style.display = '';
      countBadge.textContent = Array.isArray(parsed)
        ? i18n('setup.memory.editor.count_list', count)
        : i18n('setup.memory.editor.count_dict', count);
    } else {
      countBadge.style.display = 'none';
    }
    updateDirtyAndSave();
  }

  function updateDirtyAndSave() {
    const dirty = textarea.value !== state.baseline;
    if (dirty) {
      dirtyBadge.className = 'badge warn';
      dirtyBadge.textContent = i18n('setup.memory.editor.dirty_badge');
      dirtyBadge.style.display = '';
    } else {
      dirtyBadge.style.display = 'none';
    }
    saveBtn.disabled = !(dirty && state.lastValid);
    revertBtn.disabled = !dirty;
  }

  // ── interactions ─────────────────────────────────────────────────
  textarea.addEventListener('input', updateValidity);

  saveBtn.addEventListener('click', async () => {
    if (!state.lastValid) return; // defensive: button 应该已 disabled.
    saveBtn.disabled = true;
    statusLine.textContent = i18n('setup.memory.editor.saving');
    const res = await api.put(`/api/memory/${kind}`, { data: state.lastParsed });
    if (!res.ok) {
      statusLine.textContent = res.error?.message || `HTTP ${res.status}`;
      saveBtn.disabled = false;
      return;
    }
    const meta = res.data;
    state.lastMeta = meta;
    // 服务端返回的 data 理论上 === 我们发出去的; 但为了统一 round-trip,
    // 用服务端回显重新 stringify 作为新的 baseline (保持 indent/ordering).
    state.baseline = stringify(meta.data);
    textarea.value = state.baseline;
    updateMeta(meta);
    updateValidity();
    statusLine.textContent = i18n('setup.memory.editor.saved');
    toast(i18n('setup.memory.editor.saved'));
  });

  reloadBtn.addEventListener('click', async () => {
    const dirty = textarea.value !== state.baseline;
    if (dirty && !window.confirm(i18n('setup.memory.editor.confirm_overwrite'))) return;
    statusLine.textContent = i18n('setup.memory.editor.reloading');
    const res = await api.get(`/api/memory/${kind}`, { expectedStatuses: [404, 409] });
    if (!res.ok) {
      statusLine.textContent = res.error?.message || `HTTP ${res.status}`;
      return;
    }
    const meta = res.data;
    state.lastMeta = meta;
    state.baseline = stringify(meta.data);
    textarea.value = state.baseline;
    updateMeta(meta);
    updateValidity();
    statusLine.textContent = i18n('setup.memory.editor.reloaded');
  });

  formatBtn.addEventListener('click', () => {
    try {
      const parsed = JSON.parse(textarea.value);
      textarea.value = stringify(parsed);
      statusLine.textContent = i18n('setup.memory.editor.format_done');
      updateValidity();
    } catch {
      statusLine.textContent = i18n('setup.memory.editor.format_failed');
    }
  });

  revertBtn.addEventListener('click', () => {
    textarea.value = state.baseline;
    updateValidity();
    statusLine.textContent = '';
  });

  // ── initial paint ────────────────────────────────────────────────
  updateMeta(snapshot);
  updateValidity();
}

// ── helpers ──────────────────────────────────────────────────────────

/** Stable 2-space pretty print. JSON.stringify keeps insertion order. */
function stringify(value) {
  return JSON.stringify(value, null, 2);
}

/** "几条 / 几个 entity" 徽章数值; 非 list/dict 返回 null (不显示徽章). */
function countItems(value) {
  if (Array.isArray(value)) return value.length;
  if (value && typeof value === 'object') return Object.keys(value).length;
  return null;
}
