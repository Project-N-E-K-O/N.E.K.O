/**
 * session_save_modal.js — Save / Save as 对话框 (P21).
 *
 * 用法:
 *   openSessionSaveModal({
 *     mode: 'save' | 'save_as',
 *     defaultName: '可选默认名',
 *   });
 *
 * 行为:
 *   - mode='save_as' 必填 name, 不允许覆盖同名 (若后端 409 ArchiveExists
 *     则弹 toast + 暂存用户输入, 让用户改名).
 *   - mode='save' 后端走 overwrite=true; 如果用户没填 name, 默认用
 *     ``store.session.name`` 做名字 (会话第一次保存的场景).
 *   - "脱敏 api_key" 默认勾选 **必须**; 取消勾选会触发二次确认
 *     对话框 (PLAN.md §P21 + AGENT_NOTES §3A 关于密钥管理).
 *
 * 成功后:
 *   - 触发 ``session:saved`` 事件, dropdown 可据此刷新最近存档列表.
 *   - 只 toast; 不 reload (Save 不是"状态替换"操作, §3A B13 保留).
 */

import { i18n } from '../core/i18n.js';
import { api } from '../core/api.js';
import { toast } from '../core/toast.js';
import { store, emit } from '../core/state.js';

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

/**
 * @param {{mode?: 'save'|'save_as', defaultName?: string}} opts
 */
export function openSessionSaveModal(opts = {}) {
  const mode = opts.mode || 'save_as';
  const session = store.session;
  if (!session) {
    toast.info(i18n('session.no_active'));
    return;
  }

  const backdrop = el('div', { className: 'modal-backdrop session-save-modal' });
  const dialog = el('div', { className: 'modal' });

  // ── name field
  const nameInput = el('input', {
    type: 'text',
    className: 'session-save-modal__name',
    maxLength: 64,
    value: opts.defaultName || session.name || '',
    placeholder: i18n('session.save_modal.name_placeholder'),
  });
  const nameErr = el('div', { className: 'hint session-save-modal__name_err' });
  nameErr.style.color = 'var(--accent-danger, #e06c75)';
  nameErr.style.minHeight = '1em';

  // ── redact checkbox
  const redactCb = el('input', {
    type: 'checkbox',
    className: 'session-save-modal__redact',
  });
  redactCb.checked = true;
  const redactLbl = el('label', { className: 'row' },
    redactCb, ' ', i18n('session.save_modal.redact_api_keys'),
  );
  const redactHint = el('div', { className: 'hint' },
    i18n('session.save_modal.redact_hint'),
  );

  // ── P21.3 F3: prompt-injection advisory (detection only, never blocks)
  const injectionBadge = el('div', {
    className: 'hint session-save-modal__injection',
  });
  injectionBadge.style.minHeight = '1.2em';
  runInjectionScan(session, injectionBadge);

  // ── action buttons
  const cancelBtn = el('button', {
    className: 'small',
    onClick: () => close(),
  }, i18n('common.cancel'));
  const okBtn = el('button', {
    className: 'primary',
    onClick: () => submit(),
  }, i18n(mode === 'save' ? 'session.save_modal.save_btn' : 'session.save_modal.save_as_btn'));

  // ── layout
  dialog.append(
    el('div', { className: 'modal-header' },
      el('h3', {},
        mode === 'save'
          ? i18n('session.save_modal.title_save')
          : i18n('session.save_modal.title_save_as'),
      ),
    ),
    el('div', { className: 'field' },
      el('label', {}, i18n('session.save_modal.name_label')),
      nameInput,
      nameErr,
    ),
    el('div', { className: 'field' },
      redactLbl,
      redactHint,
      injectionBadge,
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

  // Focus + enter-to-submit.
  setTimeout(() => {
    nameInput.focus();
    nameInput.select();
  }, 0);
  nameInput.addEventListener('keydown', (ev) => {
    if (ev.key === 'Enter') {
      ev.preventDefault();
      submit();
    } else if (ev.key === 'Escape') {
      ev.preventDefault();
      close();
    }
  });

  function close() {
    backdrop.remove();
  }

  async function submit() {
    const rawName = nameInput.value.trim();
    if (!rawName) {
      nameErr.textContent = i18n('session.save_modal.name_required');
      nameInput.focus();
      return;
    }
    // Same regex as persistence.validate_name but UI-side so we fail
    // fast without the network round-trip.
    if (!/^[A-Za-z0-9][A-Za-z0-9_\-.]{0,63}$/.test(rawName)) {
      nameErr.textContent = i18n('session.save_modal.name_invalid');
      nameInput.focus();
      return;
    }
    nameErr.textContent = '';

    const redact = redactCb.checked;
    if (!redact) {
      // Second confirmation for plaintext save — API keys in the
      // archive would leak if the file is shared.
      // eslint-disable-next-line no-alert
      if (!confirm(i18n('session.save_modal.confirm_plaintext'))) {
        return;
      }
    }

    okBtn.disabled = true;
    cancelBtn.disabled = true;
    const path = mode === 'save' ? '/api/session/save' : '/api/session/save_as';
    // 400 InvalidName / 409 ArchiveExists 都是"用户再改一下名字就行"的
    // 输入校验, 不是程序错误 — 标成 expected 让 api.js 跳过 toast +
    // `http:error` 广播, 避免 Err 徽章和 Diagnostics → Errors 把它当真错.
    const res = await api.post(path, {
      name: rawName,
      redact_api_keys: redact,
    }, { expectedStatuses: [400, 409] });
    okBtn.disabled = false;
    cancelBtn.disabled = false;

    if (res.ok) {
      toast.ok(i18n('session.save_modal.ok_toast', rawName));
      emit('session:saved', { name: rawName, stats: res.data?.stats });
      close();
    } else {
      // `api.js::extractError` normalises FastAPI's nested
      // `{detail: {error_type}}` into `res.error.type`.
      const code = res.error?.type || '';
      if (code === 'ArchiveExists' || code === 'NameTaken') {
        nameErr.textContent = i18n('session.save_modal.name_taken', rawName);
        nameInput.focus();
        return;
      }
      if (code === 'InvalidName') {
        nameErr.textContent = i18n('session.save_modal.name_invalid');
        nameInput.focus();
        return;
      }
      toast.err(
        i18n('session.save_modal.err_toast'),
        { message: res.error?.message },
      );
    }
  }
}


/**
 * Run the prompt-injection detector against the session's
 * user-editable text fields and render an advisory badge.
 *
 * Never blocks Save — the core testbench principle is 永不过滤用户
 * 内容 (PLAN.md §13). This is purely informational: "here are the
 * number and kinds of suspicious patterns the framework's detector
 * found so you know what this archive is carrying". Failures (no
 * network, endpoint down) render nothing rather than a spinner; the
 * user can still save.
 *
 * We fetch persona / messages directly from the backend rather than
 * reading them off ``store.session`` because the store only caches
 * ``{id, name, state, busy_op}`` (see state.js). Keeping the fetches
 * in-modal means we scan what would actually end up in the archive,
 * not a potentially-stale client snapshot.
 */
async function runInjectionScan(session, badgeEl) {
  try {
    const fields = await collectSecurityScanFields(session);
    // Bail silently if nothing to scan.
    if (!Object.keys(fields).length) {
      return;
    }
    const res = await api.post(
      '/api/security/prompt_injection/scan',
      { fields },
      { expectedStatuses: [400, 404] },
    );
    if (!res.ok) return;
    const hitFields = (res.data || {}).fields || {};
    const fieldNames = Object.keys(hitFields);
    if (!fieldNames.length) {
      // Clean bill of health — leave the hint blank so the modal
      // doesn't shout "nothing suspicious" at every Save.
      return;
    }
    let totalHits = 0;
    for (const name of fieldNames) {
      const s = hitFields[name] || {};
      totalHits += Number(s.count || 0);
    }
    badgeEl.textContent = i18n(
      'session.save_modal.injection_badge',
      String(totalHits), String(fieldNames.length),
    );
    badgeEl.style.color = 'var(--accent-warning, #d29922)';
    badgeEl.title = [
      i18n('session.save_modal.injection_tooltip_header'),
      '',
      ...fieldNames.map(
        (n) => `• ${n}: ${hitFields[n].count} (${
          Object.keys(hitFields[n].by_category || {}).join(', ')
        })`,
      ),
    ].join('\n');
  } catch (_err) {
    // Scanner errors must never break Save UX — stay silent.
  }
}

/**
 * Collect the user-editable text fields that make sense to scan
 * for injection patterns.
 *
 * We fetch persona + messages in parallel, tolerate 404 (no active
 * session / empty conversation), and cap the message payload by
 * taking only the last 20 turns. That keeps the scan payload in the
 * "a few KB" range even on long conversations, while still catching
 * recent injection attempts.
 */
async function collectSecurityScanFields(_session) {
  const out = {};
  const [personaRes, msgRes] = await Promise.all([
    api.get('/api/persona', { expectedStatuses: [404] }),
    api.get('/api/chat/messages', { expectedStatuses: [404] }),
  ]);
  if (personaRes.ok) {
    const persona = personaRes.data?.persona || {};
    if (typeof persona.system_prompt === 'string' && persona.system_prompt.trim()) {
      out['persona.system_prompt'] = persona.system_prompt;
    }
    if (typeof persona.character_name === 'string' && persona.character_name.trim()) {
      out['persona.character_name'] = persona.character_name;
    }
  }
  if (msgRes.ok) {
    const messages = Array.isArray(msgRes.data?.messages) ? msgRes.data.messages : [];
    const recent = messages.slice(-20);
    const userBlob = recent
      .filter((m) => m && m.role === 'user' && typeof m.content === 'string')
      .map((m) => m.content)
      .join('\n');
    const assistantBlob = recent
      .filter((m) => m && m.role === 'assistant' && typeof m.content === 'string')
      .map((m) => m.content)
      .join('\n');
    if (userBlob) out['messages.user'] = userBlob;
    if (assistantBlob) out['messages.assistant'] = assistantBlob;
  }
  return out;
}
