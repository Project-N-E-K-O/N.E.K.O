/**
 * P46 UI smoke — jsdom mount for the 代码线索 (开发者) sub-page (P32).
 *
 * Run after touching memory_trace/code_leads.js / workspace_memory_trace.js /
 * the memory_trace.code_leads i18n namespace / the .code-leads-* CSS::
 *
 *     node tests/testbench/smoke/p46_memory_code_leads_ui_smoke.mjs
 *
 * This page's whole point is to LOWER confidence, so the UI guards focus on the
 * anti-mislead scaffolding (blueprint P32 §3):
 *   V1 — code_leads registers as a sub-page; its nav title carries "(开发者)";
 *        mounts against the REAL i18n dict without throwing; test hook present.
 *   V2 — a page-top RED danger notice exists, is NOT collapsible (a div, not a
 *        <details>), lists the 6 caveat要素, and links the feasibility doc.
 *   V3 — wording discipline: lead-card TEXT contains no verdict words
 *        (bug/缺陷/确认存在/检测到问题/错误); action reads as an imperative "建议排查…".
 *   V4 — each lead card carries a strength chip + persistent "(仍需人工确认)" +
 *        a suspect-modules block + a missing-evidence block.
 *   V5 — the excluded-区 shows the content-quality counts + "故意不…" wording.
 *   V6 — honest empty: no leads → "不代表…没问题" empty state (not "code is fine").
 *   V7 — status rows: embedding/evt "ran" vs "unavailable(未检查)" are surfaced.
 *
 * NOTE (LR-8 §5.3): jsdom asserts structure, NOT visual prominence. The red
 * notice's actual conspicuousness / CSS cascade must be hand-verified once in a
 * real browser; this smoke is only the first filter.
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
  if (!/getContext/.test(String(e && e.message))) console.error(e);
});

// ── configurable backend responses ──────────────────────────────────

const CODE_LEADS_OK = {
  character: 'NEKO',
  leads: [
    { code: 'D4', invariant: 'D4', strength: 'high',
      suspect_modules: ['memory/facts.py (删除路径)', 'memory/reflection/persistence.py'],
      missing_evidence: ['事实删除事件与其引用反思的时序', '删除路径是否级联清理引用'],
      count: 2, examples: [{ id: 'ref_denied', label: '被否决的反思甲' }],
      needs_human_confirm: true },
    { code: 'EVT-DUP', invariant: 'EVT-DUP', strength: 'high',
      suspect_modules: ['memory/event_log.py'],
      missing_evidence: ['事件 append/reconcile 路径是否重复写入同一 event_id'],
      count: 1, examples: [{ id: 'evt-1', label: 'fact_added' }],
      needs_human_confirm: true },
    { code: 'D2', invariant: 'D2', strength: 'medium',
      suspect_modules: ['memory/reflection/promotion.py'],
      missing_evidence: ['晋升/合并写入时的 source/merged_from 赋值轨迹'],
      count: 1, examples: [{ id: 'p_orphan', label: '孤儿人设' }],
      needs_human_confirm: true },
  ],
  excluded_content_findings: [
    { code: 'A1', category: 'redundancy', count: 2 },
    { code: 'B1', category: 'contradiction', count: 4 },
    { code: 'H1', category: 'retention', count: 1 },
  ],
  embedding_status: 'ran',
  evt_status: 'ran',
  warnings: [],
  generated_at: '2026-07-15T00:00:00+00:00',
};

const CODE_LEADS_EMPTY = {
  character: 'NEKO', leads: [], excluded_content_findings: [],
  embedding_status: 'unavailable', evt_status: 'unavailable', warnings: [],
  generated_at: '2026-07-15T00:00:00+00:00',
};

let mode = 'ok'; // 'ok' | 'empty'
const fetchCalls = [];
function fakeFetch(url, init = {}) {
  const method = (init.method || 'GET').toUpperCase();
  fetchCalls.push({ url, method });
  const patch = (resp) => {
    resp.headers = { get: (n) => (n.toLowerCase() === 'content-type' ? 'application/json' : null) };
    return resp;
  };
  const json = (obj) => patch({ ok: true, status: 200, json: async () => obj, text: async () => JSON.stringify(obj) });
  const jsonErr = (status, detail) => patch({ ok: false, status, json: async () => ({ detail }), text: async () => JSON.stringify({ detail }) });

  if (url.startsWith('/api/memory/code_leads')) {
    return Promise.resolve(json(mode === 'empty' ? CODE_LEADS_EMPTY : CODE_LEADS_OK));
  }
  return Promise.resolve(jsonErr(404, { error_type: 'NotFound' }));
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
dom.window.console = console;

async function tick(n = 10) {
  for (let i = 0; i < n; i += 1) await new Promise((r) => setTimeout(r, 0));
}
function fail(msg) { throw new Error(msg); }

// ── imports ─────────────────────────────────────────────────────────

const wsPath = resolve(here, '../static/ui/workspace_memory_trace.js');
const statePath = resolve(here, '../static/core/state.js');
const { mountMemoryTraceWorkspace } = await import(pathToFileURL(wsPath).href);
const stateMod = await import(pathToFileURL(statePath).href);

const host = document.getElementById('host');

async function mountFresh() {
  localStorage.setItem('testbench:memory_analysis:active_subpage', 'code_leads');
  stateMod.set('session', { id: 'sess_1', name: 'smoke', stage: 'chat_turn' });
  mountMemoryTraceWorkspace(host);
  await tick(14);
}

const FORBIDDEN = ['bug', '缺陷', '确认存在', '检测到问题', '错误'];

// ── V1 — sub-page registration + nav title "(开发者)" + hook ──────────

mode = 'ok';
await mountFresh();
const navBtns = [...host.querySelectorAll('.subnav .subnav-item')];
if (!navBtns.some((b) => b.textContent.includes('(开发者)'))) {
  fail(`V1: no nav item carries "(开发者)": ${navBtns.map((b) => b.textContent)}`);
}
let sub = host.querySelector('.subpage.memory-code-leads');
if (!sub) { console.error(host.innerHTML.slice(0, 600)); fail('V1: code_leads sub-page did not mount'); }
if (!sub.__codeLeads) fail('V1: test hook __codeLeads missing');
console.log('[smoke] V1 registration + nav "(开发者)" + hook OK');

// ── V2 — page-top RED danger notice, non-collapsible, 6要素 + doc link ─

const notice = host.querySelector('.code-leads-notice--danger');
if (!notice) fail('V2: red danger notice missing');
if (notice.tagName.toLowerCase() === 'details') fail('V2: notice must NOT be collapsible (<details>)');
if (notice.closest('details')) fail('V2: notice must not be nested inside a <details>');
const noticeItems = notice.querySelectorAll('.code-leads-notice-list li');
if (noticeItems.length !== 6) fail(`V2: expected 6 caveat items, got ${noticeItems.length}`);
const docLink = notice.querySelector('a.code-leads-doc');
if (!docLink) fail('V2: notice must carry a real doc <a> link (a.code-leads-doc)');
if (docLink.getAttribute('href') !== '/docs/code_leads_guide') {
  fail(`V2: doc link must point at /docs/code_leads_guide, got ${docLink.getAttribute('href')}`);
}
if (docLink.getAttribute('target') !== '_blank') {
  fail('V2: doc link should open in a new tab (target=_blank)');
}
if (!docLink.textContent.trim()) fail('V2: doc link must have visible text');
if (!notice.textContent.includes('不是') || !notice.textContent.includes('bug 报告')) {
  fail(`V2: notice must state these are NOT bug reports: ${notice.textContent.slice(0, 120)}`);
}
console.log('[smoke] V2 fixed red danger notice (6要素 + doc link) OK');

// ── V3 — wording discipline in lead cards (no verdict words) ─────────

const cards = [...host.querySelectorAll('.code-leads-card')];
if (cards.length !== 3) fail(`V3: expected 3 lead cards, got ${cards.length}`);
for (const card of cards) {
  const text = card.textContent || '';
  for (const bad of FORBIDDEN) {
    if (text.includes(bad)) fail(`V3: lead card contains forbidden verdict word "${bad}": ${text.slice(0, 120)}`);
  }
  const action = card.querySelector('.code-leads-action');
  if (!action || !action.textContent.includes('建议排查')) {
    fail(`V3: lead card action must be imperative "建议排查…": ${action && action.textContent}`);
  }
}
console.log('[smoke] V3 lead-card wording discipline OK');

// ── V4 — each lead card: strength chip + confirm + suspect + missing ─

for (const card of cards) {
  if (!card.querySelector('.code-leads-strength')) fail('V4: strength chip missing');
  const confirm = card.querySelector('.code-leads-confirm');
  if (!confirm || !confirm.textContent.includes('仍需人工确认')) fail('V4: "(仍需人工确认)" missing');
  if (!card.querySelector('.code-leads-suspect .code-leads-module')) fail('V4: suspect modules block missing');
  if (!card.querySelector('.code-leads-missing')) fail('V4: missing-evidence block missing');
}
console.log('[smoke] V4 lead card anti-mislead scaffolding OK');

// ── V5 — excluded-区 shows counts + "故意不…" wording ─────────────────

const excluded = host.querySelector('.code-leads-excluded');
if (!excluded) fail('V5: excluded-区 missing');
if (!excluded.textContent.includes('故意不')) fail(`V5: excluded-区 must say "故意不…": ${excluded.textContent.slice(0, 80)}`);
const exItems = excluded.querySelectorAll('.code-leads-excluded-list li');
if (exItems.length !== 3) fail(`V5: expected 3 excluded items, got ${exItems.length}`);
console.log('[smoke] V5 excluded-区 OK');

// ── V6 — honest empty (no leads ≠ code is fine) ──────────────────────

mode = 'empty';
await mountFresh();
const empty = host.querySelector('.code-leads-empty');
if (!empty) fail('V6: honest empty state missing');
if (!empty.textContent.includes('不代表')) fail(`V6: empty state must say "不代表…": ${empty.textContent.slice(0, 80)}`);
// The danger notice must STILL be shown even when there are no leads.
if (!host.querySelector('.code-leads-notice--danger')) fail('V6: danger notice must persist in empty state');
console.log('[smoke] V6 honest empty state OK');

// ── V7 — status rows (ran vs unavailable=未检查) ─────────────────────

// empty payload declares both statuses unavailable → must render "未检查".
let status = host.querySelector('.code-leads-status');
if (!status) fail('V7: status block missing');
if (!status.textContent.includes('未检查')) fail(`V7: unavailable status must say "未检查": ${status.textContent.slice(0, 120)}`);
// ok payload declares both ran → must render "已运行" / "已核对".
mode = 'ok';
await mountFresh();
status = host.querySelector('.code-leads-status');
if (!status || !status.textContent.includes('已运行')) fail(`V7: ran status must say "已运行": ${status && status.textContent.slice(0, 120)}`);
console.log('[smoke] V7 honest status rows OK');

console.log('\nP46 MEMORY CODE LEADS UI SMOKE OK');
