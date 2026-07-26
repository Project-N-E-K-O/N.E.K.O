/**
 * memory_trace/memory_export_modal.js — P30 一键脱敏记忆导出对话框.
 *
 * Opened from the 系统概况 (overview) sub-page toolbar [导出记忆分析] button.
 * Lets the user pick a redaction tier (default `standard`) + whether to
 * include the conversation corpus, shows an always-visible yellow 脱敏说明
 * callout (R-UIExplain, "务必阅读" — never collapsed), then downloads the
 * ZIP produced by `GET /api/memory/export`.
 *
 * Design (mirrors session_export_modal.js):
 *   - Save via the File System Access "另存为" picker (window.showSaveFilePicker)
 *     so the user chooses where the ZIP lands; falls back to a Blob anchor
 *     download when unavailable (Firefox / Safari / insecure context). The
 *     picker is acquired BEFORE the export fetch — it needs transient user
 *     activation that an `await fetch(...)` would consume. Never core/api.js
 *     (it JSON-parses; we want the raw ZIP bytes).
 *   - Default tier is `standard`, so a single [导出] click is the fast path
 *     (blueprint §A R-UX: one-click default, but configurable).
 *   - The 脱敏说明 callout is a fixed, always-visible yellow warning box
 *     (never a collapse) — it is honest about what each tier does and does
 *     NOT protect (blueprint §6 R-UIExplain). Same wording is mirrored in the
 *     ZIP README and the tester guide (single source: i18n keys here).
 *   - All human text via i18n `memory_trace.overview.export.*` — no literals.
 */

import { i18n } from '../../core/i18n.js';
import { toast } from '../../core/toast.js';
import { store } from '../../core/state.js';
import { deliverZip } from '../../core/download.js';
import { el } from '../_dom.js';

const T = (k, ...a) => i18n(`memory_trace.overview.export.${k}`, ...a);

const TIERS = ['minimal', 'standard', 'strict'];
const DEFAULT_TIER = 'standard';

const FALLBACK_ZIP_NAME = 'NEKO testbench_记忆导出.zip';

function cleanSegment(text) {
  const cleaned = String(text || '').replace(/[\\/:*?"<>|\x00-\x1f]+/g, '').replace(/\s+/g, ' ').trim();
  return cleaned || '角色';
}

function todayLocal() {
  const d = new Date();
  const p = (n) => String(n).padStart(2, '0');
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}`;
}

/** Client-side "另存为" suggestion, tier-aware to match the backend
 *  (minimal → real name; standard/strict → neutral 角色, no identity leak). */
function suggestedName(tier, characterName) {
  const label = tier === 'minimal' ? cleanSegment(characterName) : '角色';
  return `NEKO testbench_记忆导出_${label}_${todayLocal()}.zip`;
}

async function extractError(resp) {
  try {
    const body = await resp.json();
    const detail = body?.detail || body;
    return {
      type: detail?.error_type || String(resp.status),
      message: detail?.message || detail || 'unknown error',
    };
  } catch {
    return { type: String(resp.status), message: resp.statusText };
  }
}

export function openMemoryExportModal(opts = {}) {
  if (!store.session) {
    toast.info(T('err.no_session'));
    return;
  }

  // Display name used only for the 另存为 suggestion at the minimal tier
  // (standard/strict use a neutral placeholder). Passed in by the caller
  // (overview) because store.session does NOT cache the persona name.
  const characterName = opts.characterName || '';
  const state = { tier: DEFAULT_TIER, includeCorpus: true };

  const backdrop = el('div', { className: 'modal-backdrop memory-export-modal' });
  const dialog = el('div', { className: 'modal' });

  const errEl = el('div', { className: 'hint memory-export-modal__err' });
  errEl.style.color = 'var(--accent-danger, #e06c75)';
  errEl.style.minHeight = '1em';

  // ── tier radios ──
  const tierGroup = el('div', { className: 'memory-export-modal__radio-group' });
  const tierRadios = new Map();
  for (const tier of TIERS) {
    const input = el('input', {
      type: 'radio',
      name: 'memory-export-tier',
      value: tier,
      checked: tier === state.tier,
      onChange: () => { state.tier = tier; errEl.textContent = ''; },
    });
    tierRadios.set(tier, input);
    tierGroup.append(el('label', { className: 'row memory-export-modal__radio' },
      input, ' ',
      el('span', { className: 'memory-export-modal__radio-label' }, T(`tier.${tier}`)),
      el('span', { className: 'hint memory-export-modal__radio-desc' }, T(`tier_desc.${tier}`)),
    ));
  }

  // ── include corpus ──
  const corpusCb = el('input', {
    type: 'checkbox',
    checked: state.includeCorpus,
    onChange: () => { state.includeCorpus = corpusCb.checked; },
  });
  const corpusRow = el('label', { className: 'row memory-export-modal__corpus' },
    corpusCb, ' ', el('span', {}, T('include_corpus')));

  // ── redaction explainer (R-UIExplain), always-visible warning box ──
  // This is "务必阅读" content, so it must NOT be hidden behind a collapse.
  // Rendered as a fixed yellow-highlighted callout (role=note).
  const notice = el('div', {
    className: 'memory-export-modal__notice',
    role: 'note',
  });
  notice.append(el('div', { className: 'memory-export-modal__notice-head' }, T('notice_head')));
  const body = el('div', { className: 'memory-export-modal__notice-body' });
  for (const line of (T('notice_body') || '').split('\n')) {
    body.append(el('p', {}, line));
  }
  notice.append(body);

  const cancelBtn = el('button', { className: 'small', onClick: () => close() },
    i18n('common.cancel'));
  const okBtn = el('button', { className: 'primary', onClick: () => submit() },
    T('export_btn'));

  dialog.append(
    el('div', { className: 'modal-header' }, el('h3', {}, T('modal_title'))),
    el('div', { className: 'field memory-export-modal__body' },
      el('h4', {}, T('tier_heading')),
      tierGroup,
      el('div', { className: 'field' }, corpusRow,
        el('div', { className: 'hint' }, T('include_corpus_hint'))),
      notice,
      errEl,
    ),
    el('div', { className: 'modal-actions' }, cancelBtn, okBtn),
  );

  backdrop.append(dialog);
  backdrop.addEventListener('click', (ev) => { if (ev.target === backdrop) close(); });
  document.body.append(backdrop);
  setTimeout(() => { okBtn.focus(); }, 0);
  dialog.addEventListener('keydown', (ev) => {
    if (ev.key === 'Escape') { ev.preventDefault(); close(); }
  });

  function close() { backdrop.remove(); }

  async function submit() {
    errEl.textContent = '';

    // 1) Acquire the 另存为 handle FIRST, while we still have user activation.
    //    showSaveFilePicker requires transient activation; if we awaited the
    //    export fetch first, the activation would be gone and the call would
    //    throw SecurityError (→ silent fallback, no picker window — the bug
    //    the user hit). Acquire here, before any network await.
    let saveHandle = null;
    if (typeof window.showSaveFilePicker === 'function') {
      try {
        saveHandle = await window.showSaveFilePicker({
          suggestedName: suggestedName(state.tier, characterName),
          types: [{ description: 'ZIP archive', accept: { 'application/zip': ['.zip'] } }],
        });
      } catch (err) {
        if (err && err.name === 'AbortError') return; // user cancelled → keep modal, no error
        saveHandle = null; // other failure (permission/insecure) → anchor fallback
      }
    }

    // 2) Fetch the ZIP + deliver it.
    okBtn.disabled = true;
    cancelBtn.disabled = true;
    okBtn.textContent = T('exporting');

    const qs = new URLSearchParams({
      redaction: state.tier,
      include_corpus: state.includeCorpus ? 'true' : 'false',
    });
    let resp;
    try {
      resp = await fetch(`/api/memory/export?${qs.toString()}`, {
        method: 'GET',
        headers: { 'Accept': 'application/zip, application/json, */*' },
      });
    } catch {
      resetButtons();
      errEl.textContent = T('err.network');
      return;
    }

    if (!resp.ok) {
      const info = await extractError(resp);
      resetButtons();
      if (resp.status === 404) errEl.textContent = T('err.no_session');
      else if (resp.status === 409) errEl.textContent = T('err.busy');
      else errEl.textContent = T('err.backend_fmt', info.message || `HTTP ${resp.status}`);
      return;
    }

    try {
      const { filename } = await deliverZip(resp, saveHandle, FALLBACK_ZIP_NAME);
      toast.ok(T('ok_toast_fmt', filename));
      close();
    } catch (downloadErr) {
      resetButtons();
      errEl.textContent = T('err.download_fmt', String(downloadErr));
    }
  }

  function resetButtons() {
    okBtn.disabled = false;
    cancelBtn.disabled = false;
    okBtn.textContent = T('export_btn');
  }

  // Test hook for the jsdom smoke.
  backdrop.__memoryExport = {
    get state() { return state; },
    submit,
    close,
  };
  return backdrop;
}
