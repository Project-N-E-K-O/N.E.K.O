/**
 * P36 UI smoke — Setup → Import 「从 zip 人格档案导入」错误处理与状态区.
 *
 * Run after touching ``static/ui/setup/page_import.js`` or the
 * ``setup.import.archive.*`` i18n leaves::
 *
 *     node tests/testbench/smoke/p36_import_archive_ui_smoke.mjs
 *
 * What it guards (the user's request "导入失败/格式不合法要明确告诉用户, UI 要处理"):
 *   I1 — page mounts without i18n function-leaf misuse crashing (the recurring
 *        ``i18n(key)(arg)`` TypeError class); archive section + pick button render.
 *   I2 — client-side pre-validation: a non-.zip file is rejected *before* any
 *        network call, with a persistent ``.import-archive-status.is-err`` shown.
 *   I3 — empty (0-byte) file rejected client-side, no network call.
 *   I4 — backend format error (422 NoCharactersJson) surfaces as a persistent
 *        err status whose title is the "格式不合法" heading.
 *   I5 — AmbiguousArchive 422 surfaces with the "填角色名后重试" hint in detail.
 *   I6 — happy path (200) shows a persistent ``.is-ok`` status.
 */

import { fileURLToPath, pathToFileURL } from 'node:url';
import { dirname, resolve } from 'node:path';
import { createRequire } from 'node:module';

const require = createRequire(import.meta.url);
const here = dirname(fileURLToPath(import.meta.url));
const repoRoot = resolve(here, '../../..');
const jsdomPkgRoot = resolve(repoRoot, 'frontend/react-neko-chat/node_modules/jsdom');
const { JSDOM } = require(`${jsdomPkgRoot}/lib/api.js`);

// ── controllable backend stub ───────────────────────────────────────

let archiveMode = 'ok';  // 'ok' | 'nocharsjson' | 'ambiguous'
const fetchCalls = [];

function fakeFetch(url, init = {}) {
  const method = (init.method || 'GET').toUpperCase();
  const call = { url, method, body: null };
  if (init.body && typeof init.body === 'string') {
    try { call.body = JSON.parse(init.body); } catch { call.body = init.body; }
  }
  fetchCalls.push(call);

  const mk = (ok, status, payload) => Promise.resolve({
    ok, status,
    headers: { get: (n) => (n.toLowerCase() === 'content-type' ? 'application/json' : null) },
    json: async () => payload,
    text: async () => JSON.stringify(payload),
  });

  if (url === '/api/persona/builtin_presets' && method === 'GET') {
    return mk(true, 200, { presets: [] });
  }
  if (url === '/api/persona/real_characters' && method === 'GET') {
    // 404 -> renderNoSession() path; archive section still renders after it.
    return mk(false, 404, { detail: { error_type: 'NoActiveSession' } });
  }
  if (url === '/api/persona/import_from_archive' && method === 'POST') {
    if (archiveMode === 'nocharsjson') {
      return mk(false, 422, { detail: {
        error_type: 'NoCharactersJson',
        message: '压缩包内未找到 characters.json.',
      } });
    }
    if (archiveMode === 'ambiguous') {
      return mk(false, 422, { detail: {
        error_type: 'AmbiguousArchive',
        message: "压缩包内含多个角色 ['A猫', 'B猫'], 请指定 character_name.",
      } });
    }
    return mk(true, 200, {
      ok: true, character_name: '小天',
      persona: { character_name: '小天' },
      copied_files: ['facts.json', 'persona.json'],
      warnings: [],
    });
  }
  return mk(false, 404, { detail: { error_type: 'NotFound' } });
}

// ── jsdom bootstrap ─────────────────────────────────────────────────

const dom = new JSDOM('<!doctype html><html><body><div id="host"></div></body></html>',
  { url: 'http://localhost/' });
globalThis.window = dom.window;
globalThis.document = dom.window.document;
globalThis.Node = dom.window.Node;
globalThis.HTMLElement = dom.window.HTMLElement;
globalThis.HTMLInputElement = dom.window.HTMLInputElement;
globalThis.CustomEvent = dom.window.CustomEvent;
globalThis.Event = dom.window.Event;
globalThis.localStorage = dom.window.localStorage;
globalThis.fetch = fakeFetch;
if (typeof globalThis.btoa !== 'function') {
  globalThis.btoa = (s) => Buffer.from(s, 'binary').toString('base64');
}
dom.window.console = console;

async function tick(n = 8) {
  for (let i = 0; i < n; i += 1) await new Promise((r) => setTimeout(r, 0));
}

function fail(msg) {
  console.error(`[smoke] FAIL: ${msg}`);
  process.exit(1);
}

// A minimal File-like object: page code only uses .name/.size/.arrayBuffer().
function fakeFile(name, bytes) {
  const u8 = bytes instanceof Uint8Array ? bytes : new Uint8Array(bytes);
  return { name, size: u8.length, arrayBuffer: async () => u8.buffer };
}

function pickFile(input, file) {
  Object.defineProperty(input, 'files', { configurable: true, value: [file] });
  input.dispatchEvent(new dom.window.Event('change', { bubbles: true }));
}

// ── run ─────────────────────────────────────────────────────────────

const pagePath = resolve(here, '../static/ui/setup/page_import.js');
const { renderImportPage } = await import(pathToFileURL(pagePath).href);

const host = document.getElementById('host');
await renderImportPage(host);
await tick(10);

// ── I1 — mount + archive section render ──────────────────────────────

const controls = host.querySelector('.import-archive-controls');
if (!controls) fail('I1: archive controls (.import-archive-controls) not rendered');
const fileInput = controls.querySelector('input[type=file]');
const nameField = host.querySelector('.import-archive-name');
const statusEl = host.querySelector('.import-archive-status');
if (!fileInput) fail('I1: hidden file input missing');
if (!nameField) fail('I1: character-name field missing');
if (!statusEl) fail('I1: status element missing');
if (!statusEl.hidden) fail('I1: status should start hidden');
console.log('[smoke] I1 mount + archive section render OK');

// ── I2 — bad extension rejected client-side (no network) ─────────────

let before = fetchCalls.filter((c) => c.url === '/api/persona/import_from_archive').length;
pickFile(fileInput, fakeFile('not_a_zip.txt', [1, 2, 3]));
await tick(6);
let after = fetchCalls.filter((c) => c.url === '/api/persona/import_from_archive').length;
if (after !== before) fail('I2: bad-extension file must NOT hit the network');
if (statusEl.hidden || !statusEl.classList.contains('is-err')) {
  fail('I2: bad-extension should show a persistent error status');
}
if (!statusEl.textContent.includes('不是 .zip')) {
  fail(`I2: status should explain the .zip requirement, got: ${statusEl.textContent}`);
}
console.log('[smoke] I2 bad extension rejected client-side OK');

// ── I3 — empty file rejected client-side ─────────────────────────────

before = fetchCalls.filter((c) => c.url === '/api/persona/import_from_archive').length;
pickFile(fileInput, fakeFile('empty.zip', []));
await tick(6);
after = fetchCalls.filter((c) => c.url === '/api/persona/import_from_archive').length;
if (after !== before) fail('I3: empty file must NOT hit the network');
if (!statusEl.classList.contains('is-err')) fail('I3: empty file should show error status');
console.log('[smoke] I3 empty file rejected client-side OK');

// ── I4 — backend format error (422 NoCharactersJson) ─────────────────

archiveMode = 'nocharsjson';
before = fetchCalls.filter((c) => c.url === '/api/persona/import_from_archive').length;
pickFile(fileInput, fakeFile('photos.zip', [0x50, 0x4b, 0x03, 0x04, 0x00]));
await tick(12);
after = fetchCalls.filter((c) => c.url === '/api/persona/import_from_archive').length;
if (after !== before + 1) fail('I4: valid-looking .zip should hit the import endpoint once');
if (!statusEl.classList.contains('is-err')) fail('I4: format error should show err status');
if (!statusEl.textContent.includes('格式不合法')) {
  fail(`I4: format error should use the "格式不合法" heading, got: ${statusEl.textContent}`);
}
console.log('[smoke] I4 backend format error surfaced OK');

// ── I5 — AmbiguousArchive hint ───────────────────────────────────────

archiveMode = 'ambiguous';
pickFile(fileInput, fakeFile('multi.zip', [0x50, 0x4b, 0x03, 0x04, 0x00]));
await tick(12);
if (!statusEl.classList.contains('is-err')) fail('I5: ambiguous should show err status');
if (!statusEl.textContent.includes('角色名')) {
  fail(`I5: ambiguous status should hint to fill the character name, got: ${statusEl.textContent}`);
}
console.log('[smoke] I5 ambiguous-archive hint OK');

// ── I6 — happy path persistent ok status ─────────────────────────────

archiveMode = 'ok';
pickFile(fileInput, fakeFile('好天.zip', [0x50, 0x4b, 0x03, 0x04, 0x00]));
await tick(12);
if (statusEl.hidden || !statusEl.classList.contains('is-ok')) {
  fail(`I6: happy path should show a persistent ok status, classes: ${statusEl.className}`);
}
if (!statusEl.textContent.includes('小天')) {
  fail(`I6: ok status should name the imported character, got: ${statusEl.textContent}`);
}
console.log('[smoke] I6 happy path ok status OK');

console.log('P36 IMPORT ARCHIVE UI SMOKE OK');
