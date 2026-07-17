/**
 * P44 UI smoke — jsdom mount for the Import page real-character [导出] button (P31).
 *
 * Run after touching setup/page_import.js (export button + download idioms) or
 * the setup.import.export_* i18n keys::
 *
 *     node tests/testbench/smoke/p44_persona_export_ui_smoke.mjs
 *
 * Guards (assert DOM structure + fetch wiring, not pixels):
 *   U1 — each real-character row shows a [导出] button next to [导入], with the
 *        privacy tooltip (title = setup.import.export_hint).
 *   U2 — clicking [导出] acquires the 另存为 picker with suggestedName
 *        `<角色名>.zip` (BEFORE the fetch, to keep user activation), fetches
 *        GET /api/persona/export_real/<name>, and writes the blob to the handle.
 *   U3 — cancelling the picker (AbortError) does NOT fetch and surfaces no error.
 *   U4 — when showSaveFilePicker is unavailable, it falls back to an anchor
 *        download with download="<角色名>.zip".
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
  const msg = String(e && e.message);
  if (!/getContext|navigation to another Document/.test(msg)) console.error(e);
});

const CHAR = '小天';

const REAL_CHARS_OK = {
  config_dir: '/fake/config',
  memory_dir: '/fake/memory',
  master_name: '天凌',
  characters: [
    {
      name: CHAR, is_current: true, has_system_prompt: true,
      memory_dir_exists: true, memory_files: ['persona.json', 'facts.json'],
    },
  ],
  skipped_entries: [],
  cfa_fallback: null,
  note: null,
};

const fetchCalls = [];
function jsonResp(obj, status = 200) {
  return Promise.resolve({
    ok: status >= 200 && status < 300,
    status,
    headers: { get: (n) => (n.toLowerCase() === 'content-type' ? 'application/json' : null) },
    json: async () => obj,
    text: async () => JSON.stringify(obj),
  });
}

function fakeFetch(url, init = {}) {
  const method = (init.method || 'GET').toUpperCase();
  fetchCalls.push({ url, method });
  if (url.startsWith('/api/persona/export_real/')) {
    const friendly = `${CHAR}.zip`;
    const cd = `attachment; filename="NEKO_character_export.zip"; `
      + `filename*=UTF-8''${encodeURIComponent(friendly)}`;
    return Promise.resolve({
      ok: true, status: 200,
      headers: { get: (n) => (n.toLowerCase() === 'content-disposition' ? cd : 'application/zip') },
      blob: async () => ({ size: 4096 }),
    });
  }
  if (url.startsWith('/api/persona/real_characters')) return jsonResp(REAL_CHARS_OK);
  if (url.startsWith('/api/persona/builtin_presets')) return jsonResp({ presets: [] });
  return jsonResp({ detail: { error_type: 'NotFound' } }, 404);
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
globalThis.CustomEvent = dom.window.CustomEvent;
globalThis.localStorage = dom.window.localStorage;
globalThis.fetch = fakeFetch;
globalThis.URL = dom.window.URL;
let objUrlCount = 0;
globalThis.URL.createObjectURL = () => { objUrlCount += 1; return 'blob:fake'; };
globalThis.URL.revokeObjectURL = () => {};
dom.window.console = console;

// Anchor fallback capture: record the download name instead of navigating.
const anchorDownloads = [];
dom.window.HTMLAnchorElement.prototype.click = function click() {
  if (this.download) anchorDownloads.push(this.download);
};

// Save-picker stub. `pickerMode`: 'save' | 'cancel' | 'off'.
let pickerMode = 'save';
const pickerCalls = [];
let writtenBytes = null;
function installPicker() {
  if (pickerMode === 'off') { delete dom.window.showSaveFilePicker; return; }
  dom.window.showSaveFilePicker = async (opts = {}) => {
    pickerCalls.push(opts);
    if (pickerMode === 'cancel') {
      const err = new Error('user aborted');
      err.name = 'AbortError';
      throw err;
    }
    return {
      name: opts.suggestedName,
      createWritable: async () => ({
        write: async (blob) => { writtenBytes = blob; },
        close: async () => {},
      }),
    };
  };
}

async function tick(n = 12) {
  for (let i = 0; i < n; i += 1) await new Promise((r) => setTimeout(r, 0));
}
function click(node) {
  node.dispatchEvent(new dom.window.MouseEvent('click', { bubbles: true, cancelable: true }));
}
function fail(msg) { throw new Error(msg); }

// ── imports ─────────────────────────────────────────────────────────

const pagePath = resolve(here, '../static/ui/setup/page_import.js');
const { renderImportPage } = await import(pathToFileURL(pagePath).href);

const host = document.getElementById('host');

async function mountFresh() {
  installPicker();
  host.innerHTML = '';
  await renderImportPage(host);
  await tick(16);
}

function exportButton() {
  const rows = [...host.querySelectorAll('.import-row .import-row-actions')];
  for (const actions of rows) {
    for (const b of actions.querySelectorAll('button')) {
      if ((b.textContent || '').trim() === '导出') return b;
    }
  }
  return null;
}

// ── U1 — export button present + tooltip ─────────────────────────────

await mountFresh();
const btn = exportButton();
if (!btn) fail('U1: [导出] button missing from real-character row');
if (!(btn.getAttribute('title') || '').trim()) fail('U1: export button missing privacy tooltip');
console.log('[smoke] U1 export button + tooltip OK');

// ── U2 — click → picker (suggestedName <角色名>.zip) + fetch + write ──

const before = fetchCalls.length;
click(btn);
await tick(16);
if (pickerCalls.length !== 1) fail(`U2: showSaveFilePicker should be called once, got ${pickerCalls.length}`);
if (pickerCalls[0].suggestedName !== `${CHAR}.zip`) {
  fail(`U2: wrong suggestedName: ${pickerCalls[0].suggestedName}`);
}
const exportCall = fetchCalls.slice(before).find((c) => c.url.startsWith('/api/persona/export_real/'));
if (!exportCall) fail('U2: no /api/persona/export_real fetch after click');
if (!exportCall.url.includes(encodeURIComponent(CHAR))) fail(`U2: char not in url: ${exportCall.url}`);
if (!writtenBytes) fail('U2: blob was not written through the picker handle');
console.log('[smoke] U2 picker (suggestedName) + fetch + write OK');

// ── U3 — cancel picker → no fetch, no error ──────────────────────────

pickerMode = 'cancel';
writtenBytes = null;
await mountFresh();
const before3 = fetchCalls.length;
const picks3 = pickerCalls.length;
click(exportButton());
await tick(16);
if (pickerCalls.length !== picks3 + 1) fail('U3: picker should be invoked once');
const exportCalls3 = fetchCalls.slice(before3).filter((c) => c.url.startsWith('/api/persona/export_real/'));
if (exportCalls3.length !== 0) fail('U3: cancelling the picker must NOT fetch the export');
if (writtenBytes) fail('U3: nothing should be written when cancelled');
console.log('[smoke] U3 cancel picker → no fetch, no write OK');

// ── U4 — no picker → anchor fallback with download=<角色名>.zip ───────

pickerMode = 'off';
const downloadsBefore = anchorDownloads.length;
await mountFresh();
click(exportButton());
await tick(16);
const newDownloads = anchorDownloads.slice(downloadsBefore);
if (!newDownloads.includes(`${CHAR}.zip`)) {
  fail(`U4: anchor fallback download name wrong: ${JSON.stringify(newDownloads)}`);
}
if (objUrlCount < 1) fail('U4: anchor fallback should createObjectURL');
console.log('[smoke] U4 anchor fallback (download=<角色名>.zip) OK');

console.log('\nP44 PERSONA EXPORT UI SMOKE OK');
