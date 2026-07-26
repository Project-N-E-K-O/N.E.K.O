/**
 * P42 UI smoke — jsdom mount for the 记忆分析导出 modal (P30).
 *
 * Run after touching memory_trace/overview.js (export button) /
 * memory_trace/memory_export_modal.js / the memory_trace.overview.export
 * i18n namespace::
 *
 *     node tests/testbench/smoke/p42_memory_export_ui_smoke.mjs
 *
 * Guards (assert DOM structure + fetch wiring, not pixels):
 *   U1 — the [导出记忆分析] button appears in the overview toolbar once ready.
 *   U2 — clicking it opens the modal: 3 tier radios (default standard) +
 *        include-corpus checkbox + an always-visible 脱敏说明 warning box
 *        (fixed, NOT collapsed — it is "务必阅读" content).
 *   U3 — choosing strict + toggling corpus off, then [导出], fetches
 *        /api/memory/export with redaction=strict&include_corpus=false and
 *        saves via the 另存为 picker with the friendly CJK filename decoded
 *        from Content-Disposition filename*=UTF-8'' (closes the modal). The
 *        export fetch is held pending to prove the picker opens BEFORE the
 *        first await (transient-activation ordering, L66).
 *   U4 — a backend error (409) keeps the modal open and surfaces a message.
 *   U5 — cancelling the 另存为 picker keeps the modal open with no error.
 */

import { fileURLToPath, pathToFileURL } from 'node:url';
import { dirname, resolve } from 'node:path';
import { createRequire } from 'node:module';

const require = createRequire(import.meta.url);
const here = dirname(fileURLToPath(import.meta.url));
const repoRoot = resolve(here, '../../..');
const jsdomPkgRoot = resolve(repoRoot, 'frontend/react-neko-chat/node_modules/jsdom');
const { JSDOM, VirtualConsole } = require(`${jsdomPkgRoot}/lib/api.js`);

const virtualConsole = new VirtualConsole();
virtualConsole.on('jsdomError', (e) => {
  // jsdom can't navigate to the blob: URL an <a download>.click() opens — that
  // is the real, correct download path; the "navigation" error is harmless
  // noise (same class as the getContext canvas noise).
  const msg = String(e && e.message);
  if (!/getContext|navigation to another Document/.test(msg)) console.error(e);
});

// ── backend responses ───────────────────────────────────────────────

const OVERVIEW_OK = {
  character: 'NEKO',
  cards: {
    composition: { messages: 0, recent_memos: 0, facts: 5, reflections: 4, persona: 5, corrections: 1, convo_turns: 0 },
    coverage: { embedded: 0, missing: 5, stale: 0, corrupt: 0, total: 5, embedded_ratio: 0 },
    space: { primary_dim: 0, primary_count: 0, other_space_count: 0, numpy_ok: true },
    clusters: { n_clusters: 0, noise_count: 0, algo: 'none' },
    pipeline: { absorb_rate: 0, promote_rate: 0, reject_rate: 0, extract_yield: null, pending: 0, pending_old: 0 },
  },
  findings: [
    { code: 'D1', category: 'structure', stage: 'structure', severity: 'warn', count: 1,
      data: {}, drill: { page: 'lineage', opts: {} }, examples: [{ id: 'r1', label: 'x' }] },
  ],
  attention_count: 1,
  meta: {
    sources_present: { events_ndjson: false, time_indexed_db: false, trace_provenance: false },
    generated_with_embeddings: false,
    confidence: { level: 'low', embedded_ratio: 0, notes: ['NO_EMBEDDINGS'] },
    warnings: [],
  },
};

let exportMode = 'ok'; // 'ok' | 'busy' | 'gated'
let releaseExport = null; // when 'gated', call to resolve the pending fetch
const fetchCalls = [];
function fakeFetch(url, init = {}) {
  const method = (init.method || 'GET').toUpperCase();
  fetchCalls.push({ url, method });
  const jsonHeaders = { get: (n) => (n.toLowerCase() === 'content-type' ? 'application/json' : null) };
  if (url.startsWith('/api/memory/export')) {
    if (exportMode === 'busy') {
      return Promise.resolve({
        ok: false, status: 409, headers: jsonHeaders,
        json: async () => ({ detail: { error_type: 'NoCharacterSelected', message: '无角色' } }),
        text: async () => 'busy',
      });
    }
    // Friendly filename carries CJK via RFC 5987 filename*=UTF-8'' (standard
    // tier → 角色 placeholder, per the tier-aware naming).
    const friendly = 'NEKO testbench_记忆导出_角色_2026-07-15.zip';
    const cd = `attachment; filename="NEKO_testbench_memory_export.zip"; `
      + `filename*=UTF-8''${encodeURIComponent(friendly)}`;
    const okResp = {
      ok: true, status: 200,
      headers: { get: (n) => (n.toLowerCase() === 'content-disposition'
        ? cd : 'application/zip') },
      blob: async () => ({ size: 1234 }),
    };
    // 'gated' keeps the fetch pending so a test can assert the save picker was
    // opened BEFORE the first await (transient-activation ordering, L66).
    if (exportMode === 'gated') {
      return new Promise((res) => { releaseExport = () => res(okResp); });
    }
    return Promise.resolve(okResp);
  }
  if (url.startsWith('/api/memory/overview')) {
    return Promise.resolve({ ok: true, status: 200, headers: jsonHeaders,
      json: async () => OVERVIEW_OK, text: async () => JSON.stringify(OVERVIEW_OK) });
  }
  return Promise.resolve({ ok: false, status: 404, headers: jsonHeaders,
    json: async () => ({ detail: { error_type: 'NotFound' } }), text: async () => 'nf' });
}

// ── jsdom bootstrap ─────────────────────────────────────────────────

const dom = new JSDOM(
  `<!doctype html><html><body><section id="host" class="workspace"></section></body></html>`,
  { url: 'http://localhost/', virtualConsole },
);
globalThis.window = dom.window;
globalThis.document = dom.window.document;
globalThis.Node = dom.window.Node;
globalThis.HTMLElement = dom.window.HTMLElement;
globalThis.SVGElement = dom.window.SVGElement;
globalThis.CustomEvent = dom.window.CustomEvent;
globalThis.localStorage = dom.window.localStorage;
globalThis.fetch = fakeFetch;
globalThis.URL = dom.window.URL;
globalThis.URL.createObjectURL = () => 'blob:fake';
globalThis.URL.revokeObjectURL = () => {};
dom.window.console = console;

// Stub the File System Access "另存为" picker so we can assert the export uses
// it (and receives the friendly CJK suggestedName). `pickerMode` flips to
// 'cancel' to exercise the user-dismissed path.
let pickerMode = 'save'; // 'save' | 'cancel'
const pickerCalls = [];
let writtenBytes = null;
dom.window.showSaveFilePicker = async (opts = {}) => {
  pickerCalls.push(opts);
  if (pickerMode === 'cancel') {
    const err = new Error('user aborted');
    err.name = 'AbortError';
    throw err;
  }
  return {
    createWritable: async () => ({
      write: async (blob) => { writtenBytes = blob; },
      close: async () => {},
    }),
  };
};

async function tick(n = 10) {
  for (let i = 0; i < n; i += 1) await new Promise((r) => setTimeout(r, 0));
}
function click(node) {
  node.dispatchEvent(new dom.window.MouseEvent('click', { bubbles: true, cancelable: true }));
}
function fail(msg) { throw new Error(msg); }

// ── imports ─────────────────────────────────────────────────────────

const wsPath = resolve(here, '../static/ui/workspace_memory_trace.js');
const statePath = resolve(here, '../static/core/state.js');
const { mountMemoryTraceWorkspace } = await import(pathToFileURL(wsPath).href);
const stateMod = await import(pathToFileURL(statePath).href);

const host = document.getElementById('host');

async function mountFresh() {
  localStorage.setItem('testbench:memory_analysis:active_subpage', 'overview');
  stateMod.set('session', { id: 'sess_1', name: 'smoke', stage: 'chat_turn' });
  mountMemoryTraceWorkspace(host);
  await tick(14);
}

function modal() { return document.querySelector('.memory-export-modal'); }

// ── U1 — export button appears when ready ────────────────────────────

await mountFresh();
const exportBtn = host.querySelector('.mov-export-btn');
if (!exportBtn) fail('U1: export button missing from ready overview toolbar');
console.log('[smoke] U1 export button present OK');

// ── U2 — modal opens with tiers + corpus + notice ────────────────────

click(exportBtn);
await tick(4);
const m = modal();
if (!m) fail('U2: modal did not open');
const radios = m.querySelectorAll('input[name="memory-export-tier"]');
if (radios.length !== 3) fail(`U2: expected 3 tier radios, got ${radios.length}`);
const checked = [...radios].find((r) => r.checked);
if (!checked || checked.value !== 'standard') fail(`U2: default tier must be standard, got ${checked && checked.value}`);
if (!m.querySelector('.memory-export-modal__corpus input[type="checkbox"]')) fail('U2: corpus checkbox missing');
// 脱敏说明 must be a fixed, always-visible callout — NOT a <details> collapse.
const notice = m.querySelector('.memory-export-modal__notice');
if (!notice) fail('U2: 脱敏说明 warning box missing');
if (notice.tagName.toLowerCase() === 'details') fail('U2: 脱敏说明 must NOT be a collapsible <details>');
if (!m.querySelector('.memory-export-modal__notice-head')) fail('U2: 脱敏说明 head missing');
if (!m.querySelector('.memory-export-modal__notice-body')) fail('U2: 脱敏说明 body missing');
console.log('[smoke] U2 modal tiers + corpus + always-visible notice OK');

// ── U3 — strict + corpus off → fetch with right query + download ─────

const strictRadio = [...radios].find((r) => r.value === 'strict');
strictRadio.checked = true;
strictRadio.dispatchEvent(new dom.window.Event('change', { bubbles: true }));
const corpusCb = m.querySelector('.memory-export-modal__corpus input[type="checkbox"]');
corpusCb.checked = false;
corpusCb.dispatchEvent(new dom.window.Event('change', { bubbles: true }));

const before = fetchCalls.length;
const okBtn = m.querySelector('.modal-actions .primary');
// Gate the export fetch so it stays pending: this lets us prove the save
// picker is opened BEFORE the first await (otherwise the picker would lose
// transient activation and silently fall back — the exact L66 bug). A test
// that only checks "both eventually happen" would pass even if the order were
// wrong, so assert the ordering explicitly here.
exportMode = 'gated';
releaseExport = null;
const picksBeforeU3 = pickerCalls.length;
click(okBtn);
await tick(10);
if (pickerCalls.length !== picksBeforeU3 + 1) {
  fail('U3: showSaveFilePicker must be called BEFORE awaiting fetch (got it after / not at all)');
}
const gatedCall = fetchCalls.slice(before).find((c) => c.url.startsWith('/api/memory/export'));
if (!gatedCall) fail('U3: export fetch not issued while picker pending');
if (writtenBytes) fail('U3: bytes written before fetch resolved');
if (typeof releaseExport !== 'function') fail('U3: export fetch was not gated as expected');
releaseExport();
exportMode = 'ok';
await tick(10);
const exportCall = fetchCalls.slice(before).find((c) => c.url.startsWith('/api/memory/export'));
if (!exportCall) fail('U3: no /api/memory/export fetch after clicking 导出');
if (!exportCall.url.includes('redaction=strict')) fail(`U3: missing redaction=strict: ${exportCall.url}`);
if (!exportCall.url.includes('include_corpus=false')) fail(`U3: missing include_corpus=false: ${exportCall.url}`);
if (modal()) fail('U3: modal should close after a successful export');
// Export must go through the 另存为 picker with the friendly CJK name decoded
// from Content-Disposition filename*=UTF-8''.
if (pickerCalls.length !== 1) fail(`U3: showSaveFilePicker should be called once, got ${pickerCalls.length}`);
// strict tier → neutral 角色 placeholder in the client-side suggested name
// (date is "today", so match prefix/suffix rather than a hardcoded date).
const sugg = pickerCalls[0].suggestedName || '';
if (!sugg.startsWith('NEKO testbench_记忆导出_角色_') || !sugg.endsWith('.zip')) {
  fail(`U3: wrong suggestedName: ${sugg}`);
}
if (!/_\d{4}-\d{2}-\d{2}\.zip$/.test(sugg)) fail(`U3: suggestedName missing YYYY-MM-DD date: ${sugg}`);
if (!writtenBytes) fail('U3: blob was not written through the picker handle');
console.log('[smoke] U3 strict + corpus-off fetch + save-picker (friendly CJK name) OK');

// ── U4 — backend error keeps modal open + shows message ──────────────

exportMode = 'busy';
await mountFresh();
click(host.querySelector('.mov-export-btn'));
await tick(4);
const m2 = modal();
click(m2.querySelector('.modal-actions .primary'));
await tick(10);
if (!modal()) fail('U4: modal should stay open on backend error');
const err = modal().querySelector('.memory-export-modal__err');
if (!err || !err.textContent.trim()) fail('U4: error message not surfaced');
console.log('[smoke] U4 backend error keeps modal + message OK');

// ── U5 — user cancels the 另存为 picker → modal stays, no error ────────

exportMode = 'ok';
pickerMode = 'cancel';
const picksBefore = pickerCalls.length;
await mountFresh();
click(host.querySelector('.mov-export-btn'));
await tick(4);
const m3 = modal();
click(m3.querySelector('.modal-actions .primary'));
await tick(10);
if (pickerCalls.length !== picksBefore + 1) fail('U5: save picker should be invoked once');
if (!modal()) fail('U5: modal should stay open when the save dialog is cancelled');
const err5 = modal().querySelector('.memory-export-modal__err');
if (err5 && err5.textContent.trim()) fail(`U5: cancel must not surface an error, got: ${err5.textContent}`);
console.log('[smoke] U5 cancel save-picker keeps modal, no error OK');

console.log('\nP42 MEMORY EXPORT UI SMOKE OK');
