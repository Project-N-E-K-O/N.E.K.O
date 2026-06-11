/**
 * P21 UI smoke — jsdom mount for session_save_modal / session_load_modal.
 *
 * Run once after touching either modal or topbar.js::renderSessionMenu::
 *
 *     node tests/testbench/smoke/p21_ui_smoke.mjs
 *
 * Why jsdom mount instead of unit-testing bits in isolation:
 *   - The project has been burned multiple times by ``i18n(key)(arg)``
 *     misuse (see AGENT_NOTES §recurring-error). A real mount against
 *     the real i18n dict catches those `TypeError: i18n(...) is not a
 *     function` crashes at the exact line they happen, instead of
 *     bubbling up as "subpage failed to render".
 *   - Also verifies the dropdown wiring survives module-side-effect
 *     ordering (topbar.js registers a document-level click listener
 *     before renderSessionMenu is ever called).
 */

import { fileURLToPath, pathToFileURL } from 'node:url';
import { dirname, resolve } from 'node:path';
import { createRequire } from 'node:module';

// jsdom lives in frontend/react-neko-chat/node_modules (no hoisted
// project-root install). Resolve from that prefix so this smoke works
// regardless of where node was invoked from.
const require = createRequire(import.meta.url);
const here = dirname(fileURLToPath(import.meta.url));
const repoRoot = resolve(here, '../../..');
const jsdomPkgRoot = resolve(
  repoRoot, 'frontend/react-neko-chat/node_modules/jsdom',
);
const jsdomApi = require(`${jsdomPkgRoot}/lib/api.js`);
const { JSDOM } = jsdomApi;

// ── stub fetch ──────────────────────────────────────────────────────
//
// api.js treats anything 2xx as .ok. We short-circuit every URL we
// see, record the calls, and let the test harness assert on them.

const fetchCalls = [];
function fakeFetch(url, init = {}) {
  const call = { url, method: (init.method || 'GET').toUpperCase(), body: null };
  if (init.body && typeof init.body === 'string') {
    try { call.body = JSON.parse(init.body); }
    catch { call.body = init.body; }
  }
  fetchCalls.push(call);

  // Handy accessors.
  const json = (obj) => ({
    ok: true,
    status: 200,
    headers: new Map([['content-type', 'application/json']]),
    json: async () => obj,
    text: async () => JSON.stringify(obj),
  });
  const jsonErr = (status, detail) => ({
    ok: false,
    status,
    headers: new Map([['content-type', 'application/json']]),
    json: async () => ({ detail }),
    text: async () => JSON.stringify({ detail }),
  });

  // jsdom's Map-like headers object needs a case-insensitive ``.get``.
  function patchHeaders(resp) {
    resp.headers = {
      get: (name) => name.toLowerCase() === 'content-type'
        ? 'application/json' : null,
    };
    return resp;
  }

  if (url === '/api/session' && call.method === 'GET') {
    return Promise.resolve(patchHeaders(json({
      has_session: true,
      id: 'sess_abc',
      name: 'smoke_session',
      state: 'idle',
      busy_op: null,
      created_at: '2026-04-21T12:00:00',
      message_count: 0,
      snapshot_count: 1,
      eval_count: 0,
      stage: 'persona_setup',
      stage_history_count: 1,
      clock: {},
      sandbox: {},
    })));
  }
  if (url === '/api/session/saved' && call.method === 'GET') {
    return Promise.resolve(patchHeaders(json({
      items: [
        {
          name: 'demo_run_01',
          saved_at: '2026-04-21T10:00:00',
          session_name: 'demo_run_01',
          session_id: 'sess_xxx',
          message_count: 3,
          snapshot_count: 2,
          eval_count: 1,
          size_bytes: 2345,
          schema_version: 1,
          redacted: true,
          error: null,
        },
        {
          name: 'broken_archive',
          saved_at: '',
          session_name: '',
          session_id: '',
          message_count: 0,
          snapshot_count: 0,
          eval_count: 0,
          size_bytes: 1024,
          schema_version: 0,
          redacted: false,
          error: 'InvalidArchive: bad magic',
        },
      ],
      count: 2,
    })));
  }
  if (url === '/api/session/save_as' && call.method === 'POST') {
    // Echo back the body so the test can inspect it.
    if (call.body?.name === 'existing_name') {
      return Promise.resolve(patchHeaders(jsonErr(409, {
        error_type: 'ArchiveExists',
        message: 'already exists',
      })));
    }
    return Promise.resolve(patchHeaders(json({
      ok: true,
      stats: { name: call.body?.name, json_bytes: 2048, tar_bytes: 128 },
    })));
  }
  if (url.startsWith('/api/session/load/') && call.method === 'POST') {
    const name = decodeURIComponent(url.split('/').pop());
    return Promise.resolve(patchHeaders(json({
      ok: true,
      name,
      id: 'sess_new',
      name_field: 'loaded_session',
      restore_stats: { files_restored: 0, bytes_restored: 0 },
      apply_stats: { messages: 3 },
    })));
  }
  if (url.startsWith('/api/session/saved/') && call.method === 'DELETE') {
    return Promise.resolve(patchHeaders(json({
      ok: true, name: url.split('/').pop(), json_removed: true, tar_removed: true,
    })));
  }
  // Unknown route: emulate 404.
  return Promise.resolve(patchHeaders(jsonErr(404, { error_type: 'NotFound' })));
}

// ── jsdom bootstrap ─────────────────────────────────────────────────

const dom = new JSDOM(
  `<!doctype html><html><body><div id="topbar"></div></body></html>`,
  { url: 'http://localhost/' },
);
globalThis.window = dom.window;
globalThis.document = dom.window.document;
globalThis.Node = dom.window.Node;
globalThis.HTMLElement = dom.window.HTMLElement;
globalThis.HTMLButtonElement = dom.window.HTMLButtonElement;
globalThis.HTMLInputElement = dom.window.HTMLInputElement;
globalThis.HTMLTextAreaElement = dom.window.HTMLTextAreaElement;
globalThis.CustomEvent = dom.window.CustomEvent;
globalThis.localStorage = dom.window.localStorage;
globalThis.fetch = fakeFetch;
// Suppress confirm() which blocks in jsdom; always accept.
dom.window.confirm = () => true;
globalThis.confirm = () => true;
// Make i18n(key)(arg) misuse surface immediately on the real console.
dom.window.console = console;

// Small helper: tick microtasks a few times so promise chains settle.
async function tick(n = 5) {
  for (let i = 0; i < n; i += 1) {
    await new Promise((r) => setTimeout(r, 0));
  }
}

// ── run ────────────────────────────────────────────────────────────

const topbarPath = resolve(here, '../static/ui/topbar.js');
const saveModalPath = resolve(here, '../static/ui/session_save_modal.js');
const loadModalPath = resolve(here, '../static/ui/session_load_modal.js');
const statePath = resolve(here, '../static/core/state.js');

const { mountTopbar } = await import(pathToFileURL(topbarPath).href);
const { openSessionSaveModal } = await import(pathToFileURL(saveModalPath).href);
const { openSessionLoadModal } = await import(pathToFileURL(loadModalPath).href);
const stateMod = await import(pathToFileURL(statePath).href);

// ── topbar mounts without throwing (core i18n / rendering guardrail) ──

const host = document.getElementById('topbar');
mountTopbar(host);
await tick(10);

// Session dropdown trigger exists.
const sessionTrigger = host.querySelector('.dropdown .chip');
if (!sessionTrigger) throw new Error('session chip not rendered');

// Open menu by click.
sessionTrigger.dispatchEvent(
  new dom.window.MouseEvent('click', { bubbles: true, cancelable: true }),
);
const menu = host.querySelector('.dropdown .dropdown-menu');
if (!menu) throw new Error('session dropdown menu not found');
if (!menu.classList.contains('open')) {
  throw new Error('clicking session chip did not open dropdown');
}
const saveItem = [...menu.querySelectorAll('.item')].find(
  (b) => b.textContent.includes('保存') && !b.textContent.includes('另存'),
);
if (!saveItem) throw new Error('save item not rendered in dropdown');
if (saveItem.disabled) throw new Error('save item should be enabled (session active)');

console.log('[smoke] topbar.mount OK');

// ── save modal happy path ──────────────────────────────────────────

openSessionSaveModal({ mode: 'save_as', defaultName: 'my_archive' });
await tick(2);
const saveModal = document.querySelector('.modal-backdrop.session-save-modal');
if (!saveModal) throw new Error('save modal did not mount');

const nameInput = saveModal.querySelector('.session-save-modal__name');
if (nameInput.value !== 'my_archive') {
  throw new Error(`default name not populated, got ${nameInput.value}`);
}
const redactCb = saveModal.querySelector('.session-save-modal__redact');
if (!redactCb.checked) throw new Error('redact checkbox should default to checked');

// Submit (click the primary button).
const primary = saveModal.querySelector('button.primary');
primary.dispatchEvent(
  new dom.window.MouseEvent('click', { bubbles: true, cancelable: true }),
);
await tick(10);

const saveCall = fetchCalls.find(
  (c) => c.url === '/api/session/save_as' && c.body?.name === 'my_archive',
);
if (!saveCall) {
  console.error('fetchCalls:', fetchCalls);
  throw new Error('save_as was not called with name=my_archive');
}
if (saveCall.body.redact_api_keys !== true) {
  throw new Error('redact_api_keys should have been true');
}
if (document.querySelector('.modal-backdrop.session-save-modal')) {
  throw new Error('save modal did not close after successful save');
}
console.log('[smoke] save modal happy path OK');

// ── save modal invalid name path ───────────────────────────────────

openSessionSaveModal({ mode: 'save_as', defaultName: '../bad_name' });
await tick(2);
const modal2 = document.querySelector('.modal-backdrop.session-save-modal');
const prevCallCount = fetchCalls.length;
const primary2 = modal2.querySelector('button.primary');
primary2.dispatchEvent(
  new dom.window.MouseEvent('click', { bubbles: true, cancelable: true }),
);
await tick(5);
if (fetchCalls.length !== prevCallCount) {
  throw new Error('invalid name should not trigger network call');
}
const err = modal2.querySelector('.session-save-modal__name_err');
if (!err || !err.textContent) {
  throw new Error('invalid name should show inline error');
}
modal2.remove();
console.log('[smoke] save modal invalid name OK');

// ── save modal 409 ArchiveExists path ──────────────────────────────

openSessionSaveModal({ mode: 'save_as', defaultName: 'existing_name' });
await tick(2);
const modal3 = document.querySelector('.modal-backdrop.session-save-modal');
modal3.querySelector('button.primary').dispatchEvent(
  new dom.window.MouseEvent('click', { bubbles: true, cancelable: true }),
);
await tick(10);
const modal3After = document.querySelector('.modal-backdrop.session-save-modal');
if (!modal3After) {
  throw new Error('modal should stay open on 409 for user to rename');
}
const err3 = modal3After.querySelector('.session-save-modal__name_err');
if (!err3.textContent.includes('existing_name')) {
  throw new Error(`409 error text should mention name, got: ${err3.textContent}`);
}
modal3After.remove();
console.log('[smoke] save modal 409 ArchiveExists OK');

// ── load modal list + load flow ────────────────────────────────────

openSessionLoadModal();
await tick(15);
const loadModal = document.querySelector('.modal-backdrop.session-load-modal');
if (!loadModal) throw new Error('load modal did not mount');

const listCall = fetchCalls.find((c) => c.url === '/api/session/saved');
if (!listCall) throw new Error('load modal should list saved archives');

const rows = loadModal.querySelectorAll('.session-load-modal__row');
if (rows.length !== 2) {
  throw new Error(`expected 2 rows, got ${rows.length}`);
}
// The broken archive row should have its Load button disabled.
const brokenRow = [...rows].find((r) => r.textContent.includes('broken_archive'));
if (!brokenRow) throw new Error('broken archive row not rendered');
const brokenLoadBtnDisabled = [...brokenRow.querySelectorAll('button')].find(
  (b) => b.textContent === '加载' && b.disabled,
);
if (!brokenLoadBtnDisabled) {
  throw new Error('broken row load button should be disabled');
}

// Click Load on the good row.
let sessionLoadedPayload = null;
stateMod.on('session:loaded', (p) => { sessionLoadedPayload = p; });

const goodRow = [...rows].find((r) => r.textContent.includes('demo_run_01'));
const loadBtn = [...goodRow.querySelectorAll('button')].find(
  (b) => b.textContent === '加载',
);
if (!loadBtn) throw new Error('load button not found on good row');
loadBtn.dispatchEvent(
  new dom.window.MouseEvent('click', { bubbles: true, cancelable: true }),
);
await tick(15);

const loadCall = fetchCalls.find(
  (c) => c.url.startsWith('/api/session/load/') && c.method === 'POST',
);
if (!loadCall) throw new Error('load endpoint was not called');
if (!loadCall.url.endsWith('demo_run_01')) {
  throw new Error(`expected load/demo_run_01, got ${loadCall.url}`);
}
if (!sessionLoadedPayload || sessionLoadedPayload.name !== 'demo_run_01') {
  throw new Error('session:loaded event was not emitted with correct name');
}
console.log('[smoke] load modal list+load OK');

console.log('\nP21 UI SMOKE OK');
