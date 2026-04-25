/**
 * _prompt_preview_modal.js — Shared "Preview Prompt" modal (P25 r7).
 *
 * Background
 * ----------
 * r7 partitioned the wire display by domain: Chat page's Preview Panel
 * only shows conversational AI wires ({chat.send, auto_dialog_target,
 * avatar_event, agent_callback, proactive_chat}). Wires from
 * ``memory.llm`` / ``judge.llm`` live on their own pages behind
 * "预览 prompt" buttons next to the Dry-run / Run triggers.
 *
 * This module centralizes the modal that those buttons pop open, so
 * Memory sub-pages, the Evaluation/Run page, and any future domain can
 * share **one** styling + copy-to-clipboard + keyboard-close contract.
 *
 * Usage
 * -----
 *     import { openPromptPreviewModal } from '../_prompt_preview_modal.js';
 *
 *     openPromptPreviewModal({
 *       title: i18n('some.modal.title'),
 *       intro: i18n('some.modal.intro'),   // optional short hint line
 *       wireMessages: [{role, content}, ...],
 *       metaRows: [{label, value}, ...],   // optional tiny KV strip
 *       warnings: ['...'],                 // optional soft warnings
 *       emptyHint: i18n('...empty_hint'),  // shown when wireMessages=[]
 *     });
 *
 * The modal is identical in feel to the r5 External-Events preview
 * modal (r6 removed its top-right close button; we keep only the
 * bottom close + backdrop click + Escape). No second "edit → commit"
 * step: this is **preview only**, the caller remains responsible for
 * the actual trigger action.
 */

import { i18n } from '../core/i18n.js';
import { toast } from '../core/toast.js';
import { el } from './_dom.js';


/**
 * Open the shared preview modal.
 *
 * @param {object} opts
 * @param {string} opts.title              Heading text.
 * @param {string} [opts.intro]            Short paragraph above the wire.
 * @param {Array<{role:string, content:any}>} [opts.wireMessages]
 *                                          The messages to render.
 * @param {Array<{label:string, value:string}>} [opts.metaRows]
 *                                          Tiny KV strip shown between
 *                                          intro and wire list.
 * @param {string[]} [opts.warnings]        Soft warnings rendered as hint
 *                                          rows under intro.
 * @param {string} [opts.emptyHint]         Text when wireMessages is empty.
 */
export function openPromptPreviewModal(opts = {}) {
  const {
    title = '',
    intro = '',
    wireMessages = [],
    metaRows = [],
    warnings = [],
    emptyHint = '',
  } = opts;

  const backdrop = el('div', {
    className: 'modal-backdrop prompt-preview-modal',
  });
  const dialog = el('div', { className: 'modal' });

  const head = el('div', { className: 'modal-head' },
    el('h3', {}, String(title || '')),
  );

  const body = el('div', { className: 'modal-body' });

  if (intro) {
    body.append(el('p', { className: 'hint' }, intro));
  }

  if (warnings && warnings.length) {
    const warnBox = el('div', { className: 'prompt-preview-warnings' });
    for (const w of warnings) {
      warnBox.append(el('div', { className: 'hint warn' }, `· ${w}`));
    }
    body.append(warnBox);
  }

  if (metaRows && metaRows.length) {
    const metaStrip = el('div', { className: 'prompt-preview-meta preview-meta' });
    for (const row of metaRows) {
      const v = String(row?.value ?? '');
      metaStrip.append(el('span', {
        className: 'meta-badge',
        title: `${row?.label || ''}: ${v}`,
      },
        el('span', { className: 'meta-label' }, `${row?.label || ''}: `),
        el('span', { className: 'meta-value u-wrap-anywhere' }, v),
      ));
    }
    body.append(metaStrip);
  }

  if (Array.isArray(wireMessages) && wireMessages.length) {
    body.append(el('h4', {},
      i18n('prompt_preview_modal.wire_heading')));

    const list = el('div', { className: 'prompt-preview-wire' });
    wireMessages.forEach((msg, idx) => {
      const role = msg?.role || '?';
      const content = typeof msg?.content === 'string'
        ? msg.content
        : JSON.stringify(msg?.content);
      const isTail = idx === wireMessages.length - 1;
      const row = el('details', {
        className: `wire-row wire-role-${role}` + (isTail ? ' wire-tail' : ''),
        open: isTail,
      });
      row.append(el('summary', {},
        `#${idx} · ${role.toUpperCase()} · ${content.length} chars`,
      ));
      row.append(el('pre', { className: 'mono' }, content));
      list.append(row);
    });
    body.append(list);

    body.append(el('div', { className: 'modal-actions-inline' },
      el('button', {
        type: 'button',
        className: 'btn',
        onClick: async () => {
          try {
            await navigator.clipboard.writeText(
              JSON.stringify(wireMessages, null, 2));
            toast.ok(i18n('prompt_preview_modal.copied_wire'));
          } catch {
            toast.err(i18n('prompt_preview_modal.copy_failed'));
          }
        },
      }, i18n('prompt_preview_modal.copy_wire_btn')),
    ));
  } else {
    body.append(el('div', { className: 'empty-state muted' },
      emptyHint || i18n('prompt_preview_modal.wire_empty')));
  }

  const foot = el('div', { className: 'modal-foot' },
    el('button', {
      type: 'button',
      className: 'btn primary',
      onClick: close,
    }, i18n('prompt_preview_modal.close_btn')),
  );

  dialog.append(head, body, foot);
  backdrop.append(dialog);

  function close() {
    backdrop.remove();
    document.removeEventListener('keydown', onKey);
  }
  function onKey(ev) {
    if (ev.key === 'Escape') {
      ev.preventDefault();
      close();
    }
  }
  backdrop.addEventListener('click', (ev) => {
    if (ev.target === backdrop) close();
  });
  document.addEventListener('keydown', onKey);

  document.body.append(backdrop);
  return { close };
}
