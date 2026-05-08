/**
 * session_export_modal.js — P23 统一导出对话框.
 *
 * 三个入口共用同一个 modal, 只是初始化 preset 不同:
 *   - 顶栏 Menu → Export        (默认 full + json)
 *   - Evaluation → Aggregate    (预选 conversation_evaluations + markdown)
 *   - Diagnostics → Paths       (预选 full + json + include_memory)
 *
 * 用法:
 *   openSessionExportModal({
 *     scope?: 'full' | 'persona_memory' | 'conversation'
 *           | 'conversation_evaluations' | 'evaluations',
 *     format?: 'json' | 'markdown' | 'dialog_template',
 *     include_memory?: boolean,
 *     lockScope?: boolean,     // 锁定 scope 选择 (Aggregate / Paths 入口用)
 *     lockFormat?: boolean,    // 锁定 format 选择
 *     title?: string,          // 覆盖默认 i18n 标题
 *     subtitle?: string,       // 副标题提示, 解释预选原因
 *   });
 *
 * 设计原则:
 *   - **API key 脱敏是硬约束**: 没有"明文导出"开关 (PLAN §P23 决策,
 *     与后端 session_export.py 同源). 模态里只给一条只读提示.
 *   - **dialog_template 只配 conversation**: 选择 dialog_template 时
 *     自动切 scope=conversation 并禁用其它 scope radio; 选其它 scope
 *     时也对应禁用 dialog_template. 前后端两边都做 (VALID_COMBINATIONS).
 *   - **下载路径走 Blob + createObjectURL**: 后端返回 `text/markdown`
 *     或 `application/json` 的 raw Response (带 Content-Disposition);
 *     我们直接读 blob → 创建 object URL → `<a>.click()` → revoke.
 *     不走 `core/api.js::request` 因为那个会 parse JSON, 而 export
 *     想拿到原 bytes.
 *   - **Esc 关闭 / Enter 触发 Export**: 与 save modal 一致.
 */

import { i18n } from '../core/i18n.js';
import { toast } from '../core/toast.js';
import { store } from '../core/state.js';

const ALL_SCOPES = [
  'full',
  'persona_memory',
  'conversation',
  'conversation_evaluations',
  'evaluations',
];

const ALL_FORMATS = ['json', 'markdown', 'dialog_template'];

// 与后端 session_export.VALID_COMBINATIONS 严格镜像. 新增/修改必须
// 两边一起改, 否则 modal 放行的组合会被后端 400 打回.
const VALID_COMBOS = new Set([
  'full|json', 'full|markdown',
  'persona_memory|json', 'persona_memory|markdown',
  'conversation|json', 'conversation|markdown', 'conversation|dialog_template',
  'conversation_evaluations|json', 'conversation_evaluations|markdown',
  'evaluations|json', 'evaluations|markdown',
]);

function isValidCombo(scope, format) {
  return VALID_COMBOS.has(`${scope}|${format}`);
}

function el(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (k === 'className') node.className = v;
    else if (k === 'onClick') node.addEventListener('click', v);
    else if (k === 'onInput') node.addEventListener('input', v);
    else if (k === 'onChange') node.addEventListener('change', v);
    else if (k === 'onKeyDown') node.addEventListener('keydown', v);
    else if (k.startsWith('data-')) node.setAttribute(k, v);
    else if (k === 'title') node.title = v;
    else node[k] = v;
  }
  for (const c of children) {
    if (c == null || c === false) continue;
    node.append(c instanceof Node ? c : document.createTextNode(c));
  }
  return node;
}

function pad2(n) { return String(n).padStart(2, '0'); }

function nowTimestamp() {
  const d = new Date();
  return (
    d.getFullYear().toString()
    + pad2(d.getMonth() + 1) + pad2(d.getDate())
    + '_' + pad2(d.getHours()) + pad2(d.getMinutes()) + pad2(d.getSeconds())
  );
}

function sanitizeForFilename(s) {
  return (s || '').replace(/[^A-Za-z0-9._-]+/g, '_') || 'session';
}

function previewFilename(sessionName, scope, format) {
  const ts = nowTimestamp();
  const safeName = sanitizeForFilename(sessionName);
  if (format === 'dialog_template') {
    return `tbscript_${safeName}_${ts}.json`;
  }
  const ext = format === 'markdown' ? 'md' : 'json';
  const safeScope = sanitizeForFilename(scope);
  return `tbsession_${safeName}_${safeScope}_${ts}.${ext}`;
}

/**
 * Trigger the actual download by creating an object URL for the
 * response blob and clicking an invisible anchor.
 *
 * We parse `Content-Disposition` for the filename when present to stay
 * consistent with what the backend generated (timestamp / scope tags).
 * If the header is missing (reverse proxy strip, legacy route) we fall
 * back to the preview filename we showed in the modal.
 */
async function downloadBlob(resp, fallbackName) {
  const cd = resp.headers.get('Content-Disposition') || '';
  const match = /filename="?([^"]+)"?/i.exec(cd);
  const filename = (match && match[1]) || fallbackName;
  const blob = await resp.blob();
  const objUrl = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = objUrl;
  a.download = filename;
  document.body.append(a);
  a.click();
  setTimeout(() => {
    a.remove();
    URL.revokeObjectURL(objUrl);
  }, 100);
  return { filename, bytes: blob.size };
}

/**
 * Extract `{error_type, message}` from a failed export response body.
 * FastAPI wraps the detail dict under `detail` when the router raises
 * `HTTPException(detail={...})`; we peel both layers defensively.
 */
async function extractExportError(resp) {
  try {
    const body = await resp.json();
    const detail = body?.detail || body;
    return {
      type: detail?.error_type || String(resp.status),
      message: detail?.message || detail || 'unknown error',
    };
  } catch (_err) {
    return { type: String(resp.status), message: resp.statusText };
  }
}

export function openSessionExportModal(opts = {}) {
  const session = store.session;
  if (!session) {
    toast.info(i18n('session.no_active'));
    return;
  }

  const state = {
    scope: ALL_SCOPES.includes(opts.scope) ? opts.scope : 'full',
    format: ALL_FORMATS.includes(opts.format) ? opts.format : 'json',
    include_memory: !!opts.include_memory,
  };
  // Snap to a valid combo if the preset itself is invalid.
  if (!isValidCombo(state.scope, state.format)) {
    // dialog_template preset without conversation scope: force
    // conversation; otherwise fall back to json (the safe default).
    if (state.format === 'dialog_template') state.scope = 'conversation';
    else state.format = 'json';
  }

  const backdrop = el('div', { className: 'modal-backdrop session-export-modal' });
  const dialog = el('div', { className: 'modal' });

  const filenamePreview = el('code', {
    className: 'session-export-modal__filename',
  });
  const noteEl = el('div', {
    className: 'hint session-export-modal__note',
  });
  const errEl = el('div', { className: 'hint session-export-modal__err' });
  errEl.style.color = 'var(--accent-danger, #e06c75)';
  errEl.style.minHeight = '1em';

  const scopeGroup = el('div', { className: 'session-export-modal__radio-group' });
  const formatGroup = el('div', { className: 'session-export-modal__radio-group' });

  const scopeRadios = new Map();
  const formatRadios = new Map();

  function updateNote() {
    let key;
    if (state.format === 'dialog_template') {
      key = 'session.export_modal.note.dialog_template';
    } else if (state.scope === 'full') {
      key = 'session.export_modal.note.full';
    } else if (state.scope === 'persona_memory') {
      key = 'session.export_modal.note.persona_memory';
    } else if (state.scope === 'conversation') {
      key = 'session.export_modal.note.conversation';
    } else if (state.scope === 'conversation_evaluations') {
      key = 'session.export_modal.note.conversation_evaluations';
    } else {
      key = 'session.export_modal.note.evaluations';
    }
    noteEl.textContent = i18n(key);
  }

  function updateFilename() {
    filenamePreview.textContent = previewFilename(
      session.name || session.id || 'session',
      state.scope,
      state.format,
    );
  }

  function updateRadioEnabledness() {
    for (const [scope, input] of scopeRadios) {
      const valid = isValidCombo(scope, state.format);
      input.disabled = !valid || !!opts.lockScope;
      input.parentElement.classList.toggle('is-disabled', input.disabled);
      input.checked = scope === state.scope;
    }
    for (const [fmt, input] of formatRadios) {
      const valid = isValidCombo(state.scope, fmt);
      input.disabled = !valid || !!opts.lockFormat;
      input.parentElement.classList.toggle('is-disabled', input.disabled);
      input.checked = fmt === state.format;
    }
    // include_memory 仅在 (full, json) 或 (persona_memory, json) 组合下
    // 对 payload 有实质效果 — 其它组合后端会 silently ignore; 但我们仍把
    // checkbox 置灰 + 强制清空, 让用户一看就知道"这组合没效果".
    const memoryApplies =
      state.format === 'json'
      && (state.scope === 'full' || state.scope === 'persona_memory');
    memoryCb.disabled = !memoryApplies;
    memoryLbl.classList.toggle('is-disabled', !memoryApplies);
    if (!memoryApplies) memoryCb.checked = false;
    state.include_memory = memoryCb.checked;
  }

  function onScopeChange(scope) {
    state.scope = scope;
    // 若新 scope 与当前 format 不兼容, 自动跳到同 scope 的首个合法 format.
    if (!isValidCombo(state.scope, state.format)) {
      const firstValid = ALL_FORMATS.find((f) => isValidCombo(state.scope, f));
      if (firstValid) state.format = firstValid;
    }
    errEl.textContent = '';
    updateRadioEnabledness();
    updateFilename();
    updateNote();
  }

  function onFormatChange(fmt) {
    state.format = fmt;
    if (!isValidCombo(state.scope, state.format)) {
      const firstValid = ALL_SCOPES.find((s) => isValidCombo(s, state.format));
      if (firstValid) state.scope = firstValid;
    }
    errEl.textContent = '';
    updateRadioEnabledness();
    updateFilename();
    updateNote();
  }

  for (const scope of ALL_SCOPES) {
    const input = el('input', {
      type: 'radio',
      name: 'session-export-scope',
      value: scope,
      onChange: () => onScopeChange(scope),
    });
    const row = el('label', { className: 'row session-export-modal__radio' },
      input, ' ',
      el('span', { className: 'session-export-modal__radio-label' },
        i18n(`session.export_modal.scope.${scope}`),
      ),
    );
    scopeRadios.set(scope, input);
    scopeGroup.append(row);
  }

  for (const fmt of ALL_FORMATS) {
    const input = el('input', {
      type: 'radio',
      name: 'session-export-format',
      value: fmt,
      onChange: () => onFormatChange(fmt),
    });
    const row = el('label', { className: 'row session-export-modal__radio' },
      input, ' ',
      el('span', { className: 'session-export-modal__radio-label' },
        i18n(`session.export_modal.format.${fmt}`),
      ),
    );
    formatRadios.set(fmt, input);
    formatGroup.append(row);
  }

  const memoryCb = el('input', {
    type: 'checkbox',
    onChange: () => { state.include_memory = memoryCb.checked; },
  });
  const memoryLbl = el('label', { className: 'row session-export-modal__memory' },
    memoryCb, ' ',
    el('span', {}, i18n('session.export_modal.include_memory')),
  );
  const memoryHint = el('div', { className: 'hint' },
    i18n('session.export_modal.include_memory_hint'),
  );

  const cancelBtn = el('button', {
    className: 'small',
    onClick: () => close(),
  }, i18n('common.cancel'));
  const okBtn = el('button', {
    className: 'primary',
    onClick: () => submit(),
  }, i18n('session.export_modal.export_btn'));

  dialog.append(
    el('div', { className: 'modal-header' },
      el('h3', {},
        opts.title || i18n('session.export_modal.title'),
      ),
    ),
    el('div', { className: 'field session-export-modal__body' },
      opts.subtitle
        ? el('p', { className: 'hint session-export-modal__subtitle' }, opts.subtitle)
        : null,
      el('div', { className: 'session-export-modal__grid' },
        el('div', {},
          el('h4', {}, i18n('session.export_modal.scope_heading')),
          scopeGroup,
        ),
        el('div', {},
          el('h4', {}, i18n('session.export_modal.format_heading')),
          formatGroup,
        ),
      ),
      el('div', { className: 'field' },
        memoryLbl,
        memoryHint,
      ),
      el('div', { className: 'hint session-export-modal__api-key-note' },
        i18n('session.export_modal.api_key_redacted_hint'),
      ),
      noteEl,
      el('div', { className: 'hint session-export-modal__filename-row' },
        i18n('session.export_modal.filename_label'), ' ',
        filenamePreview,
      ),
      errEl,
    ),
    el('div', { className: 'modal-actions' },
      cancelBtn, okBtn,
    ),
  );

  backdrop.append(dialog);
  backdrop.addEventListener('click', (ev) => {
    if (ev.target === backdrop) close();
  });
  document.body.append(backdrop);

  setTimeout(() => { okBtn.focus(); }, 0);
  dialog.addEventListener('keydown', (ev) => {
    if (ev.key === 'Enter') {
      // Avoid double-trigger when focus is already on the Export button.
      if (document.activeElement === cancelBtn) return;
      ev.preventDefault();
      submit();
    } else if (ev.key === 'Escape') {
      ev.preventDefault();
      close();
    }
  });

  updateRadioEnabledness();
  updateFilename();
  updateNote();

  function close() {
    backdrop.remove();
  }

  async function submit() {
    errEl.textContent = '';
    if (!isValidCombo(state.scope, state.format)) {
      errEl.textContent = i18n('session.export_modal.err.invalid_combo');
      return;
    }
    okBtn.disabled = true;
    cancelBtn.disabled = true;
    okBtn.textContent = i18n('session.export_modal.exporting');

    let resp;
    try {
      resp = await fetch('/api/session/export', {
        method: 'POST',
        headers: {
          'Accept': 'application/json, text/markdown, */*',
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({
          scope: state.scope,
          format: state.format,
          include_memory: state.include_memory,
        }),
      });
    } catch (networkErr) {
      okBtn.disabled = false;
      cancelBtn.disabled = false;
      okBtn.textContent = i18n('session.export_modal.export_btn');
      errEl.textContent = i18n('session.export_modal.err.network');
      return;
    }

    if (!resp.ok) {
      const info = await extractExportError(resp);
      okBtn.disabled = false;
      cancelBtn.disabled = false;
      okBtn.textContent = i18n('session.export_modal.export_btn');
      if (info.type === 'InvalidCombination') {
        errEl.textContent = i18n('session.export_modal.err.invalid_combo');
      } else if (resp.status === 404) {
        errEl.textContent = i18n('session.no_active');
      } else if (resp.status === 409) {
        errEl.textContent = i18n('session.export_modal.err.busy');
      } else {
        errEl.textContent = i18n(
          'session.export_modal.err.backend',
          info.message || `HTTP ${resp.status}`,
        );
      }
      return;
    }

    try {
      const { filename, bytes } = await downloadBlob(
        resp,
        filenamePreview.textContent,
      );
      toast.ok(i18n('session.export_modal.ok_toast', filename));
      // P24 §12.1 event bus audit (2026-04-21): 删了 `session:exported` emit
      // — P23 新增时直接违反 B12 (emit 前查 listener), 全仓 0 listener. 导出成功
      // 的 UX 已由 toast.ok 承担. 未来若做"最近导出历史"面板再重建并同步 listener.
      close();
    } catch (downloadErr) {
      okBtn.disabled = false;
      cancelBtn.disabled = false;
      okBtn.textContent = i18n('session.export_modal.export_btn');
      errEl.textContent = i18n(
        'session.export_modal.err.download',
        String(downloadErr),
      );
    }
  }
}
