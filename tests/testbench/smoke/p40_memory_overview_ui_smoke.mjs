/**
 * P40 UI smoke — jsdom mount for the 系统概况 (Memory System Overview) sub-page
 * (P29).
 *
 * Run after touching memory_trace/overview.js / workspace_memory_trace.js /
 * memory_trace/embedding_space.js (ctx.opts drill-in) / the
 * memory_trace.overview i18n namespace::
 *
 *     node tests/testbench/smoke/p40_memory_overview_ui_smoke.mjs
 *
 * Guards (assert DOM structure + data flow via host.__overview, not pixels):
 *   W1 — 系统概况 is the DEFAULT sub-page; mounts against the REAL i18n dict
 *        without throwing; renders cards + the "需关注" banner + findings.
 *   W2 — info-severity findings (B2 candidate) are collapsed behind a toggle;
 *        clicking it reveals them. (Honesty: B2 is a candidate, not a verdict.)
 *   W3 — a finding's drill button navigates to 记忆溯源 (lineage) sub-page.
 *   W4 — a finding's drill button navigates to 向量空间 with the right view:
 *        the embedding sub-page consumes ctx.opts and fetches /duplicates
 *        (P29 cross-link → embedding_space.js ctx.opts extension).
 *   W5 — LLM 体检报告 button POSTs /overview/ai_report and renders the report;
 *        an 'unavailable' result surfaces the actionable reason (which API).
 *   W6 — 矛盾 NLI 裁决 button POSTs /overview/contradictions and renders the
 *        verdicts with localized relation labels (互相矛盾 …).
 *   W7 — no session → no_session empty state (not a crash).
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

let aiMode = 'llm';     // 'llm' | 'unavailable'
let contraMode = 'llm'; // 'llm' | 'unavailable'

const AI_REASON = '请先在 Settings → Models → memory 填好 base_url 与 model。';

const OVERVIEW_OK = {
  character: 'NEKO',
  cards: {
    composition: { messages: 0, recent_memos: 0, facts: 5, reflections: 4, persona: 5, corrections: 1, convo_turns: 0 },
    coverage: { embedded: 9, missing: 3, stale: 1, corrupt: 0, total: 18, embedded_ratio: 0.5 },
    space: { primary_dim: 8, primary_count: 9, other_space_count: 1, numpy_ok: true },
    clusters: { n_clusters: 2, noise_count: 0, algo: 'hdbscan' },
    pipeline: { absorb_rate: 0, promote_rate: 0.25, reject_rate: 0.5, extract_yield: null, pending: 1, pending_old: 1 },
  },
  findings: [
    { code: 'N1', category: 'contradiction', stage: 'correct', severity: 'bad', count: 1,
      data: {}, drill: { page: 'lineage', opts: { focusNodeId: 'p_corrected' } },
      examples: [{ correction: 'corr:x', persona: 'p_corrected' }] },
    { code: 'A1', category: 'redundancy', stage: 'dedup', severity: 'warn', count: 2,
      data: { threshold: 0.92, capped: false },
      drill: { page: 'embedding', opts: { mode: 'duplicates', threshold: 0.92 } },
      examples: [{ a: 'fact_a', b: 'p_1', score: 0.99, a_label: '主人喜欢深夜调试', b_label: '主人是夜猫子' }] },
    { code: 'D1', category: 'structure', stage: 'structure', severity: 'warn', count: 2,
      data: {}, drill: { page: 'lineage', opts: { focusNodeId: 'ref_orphan' } },
      examples: [{ id: 'ref_orphan', label: '无来源的反思' }] },
    { code: 'B2', category: 'contradiction', stage: 'correct', severity: 'info', count: 3,
      data: { low: 0.55, high: 0.92 }, drill: { page: 'embedding', opts: { mode: 'matrix' } },
      examples: [{ a: 'fact_a', b: 'fact_b2', a_label: '主人爱咖啡', b_label: '主人点拿铁' }] },
  ],
  attention_count: 3,
  meta: {
    sources_present: { events_ndjson: false, time_indexed_db: false, trace_provenance: false },
    generated_with_embeddings: true,
    confidence: { level: 'medium', embedded_ratio: 0.5, notes: ['LOW_EMBED_COVERAGE', 'SPLIT_SPACES'] },
    warnings: [],
  },
};

const AI_LLM = { method: 'llm', report: '总体良好。优先处理未解决的矛盾与冗余重复。', overview: OVERVIEW_OK, warnings: [] };
const AI_UNAVAIL = { method: 'unavailable', report: '', overview: OVERVIEW_OK, warnings: [`AI 报告生成失败。原因: ${AI_REASON}`] };
const CONTRA_LLM = {
  method: 'llm',
  verdicts: [
    { a: 'fact_x', b: 'fact_y', entity: 'master', relation: 'contradiction', reason: '极性相反', score: 0.7, a_label: '主人不喝酒', b_label: '主人爱喝酒' },
  ],
  candidates: [{ a: 'fact_x', b: 'fact_y', a_label: '主人不喝酒', b_label: '主人爱喝酒' }],
  warnings: [],
};
const CONTRA_UNAVAIL = {
  method: 'unavailable', verdicts: [],
  candidates: [{ a: 'fact_x', b: 'fact_y', a_label: '主人不喝酒', b_label: '主人爱喝酒' }],
  warnings: [`矛盾裁决失败, 仅返回候选对。原因: ${AI_REASON}`],
};

const SPACE_OK = {
  points: [
    { id: 'fact_a', type: 'fact', entity: 'master', x: -1, y: 0.4, label: '主人喜欢深夜调试' },
    { id: 'p_1', type: 'persona', entity: 'master', x: -1.1, y: 0.5, label: '主人是夜猫子' },
  ],
  meta: { total: 2, embedded: 2, missing: 0, stale: 0, corrupt: 0, dims_present: { '8': 2 },
    primary_dim: 8, primary_count: 2, other_space_count: 0, numpy_ok: true,
    reducer_requested: 'pca', reducer_used: 'pca', umap_available: false, warnings: [] },
};
const DUPLICATES = {
  threshold: 0.92, count: 1, capped: false, candidates: 2,
  pairs: [{ a: 'fact_a', b: 'p_1', score: 0.999, a_type: 'fact', b_type: 'persona',
    a_entity: 'master', b_entity: 'master', a_label: '主人喜欢深夜调试', b_label: '主人是夜猫子', same_type: false }],
};
const LINEAGE = {
  nodes: [{ id: 'p_corrected', type: 'persona_entry', lane: 4, label: '主人不喝酒', status: 'active',
    entity: 'master', created_at: '2026-06-29T12:00:00', meta: { text: '主人不喝酒' }, warnings: [] }],
  edges: [],
  meta: { character: 'NEKO',
    counts: { messages: 0, recent_memos: 0, facts: 0, reflections: 0, persona: 1, corrections: 0 },
    sources_present: { events_ndjson: false, time_indexed_db: false, trace_provenance: false },
    file_warnings: [], corpus_warnings: [], node_budget: { total: 1, shown: 1, truncated: false }, edge_count: 0 },
};

let overviewMode = 'ok'; // 'ok' | 'no_session'
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

  if (url.startsWith('/api/memory/overview/ai_report')) {
    return Promise.resolve(json(aiMode === 'llm' ? AI_LLM : AI_UNAVAIL));
  }
  if (url.startsWith('/api/memory/overview/contradictions')) {
    return Promise.resolve(json(contraMode === 'llm' ? CONTRA_LLM : CONTRA_UNAVAIL));
  }
  if (url.startsWith('/api/memory/overview')) {
    if (overviewMode === 'no_session') {
      return Promise.resolve(jsonErr(404, { error_type: 'NoActiveSession' }));
    }
    return Promise.resolve(json(OVERVIEW_OK));
  }
  if (url.startsWith('/api/memory/embedding/duplicates')) return Promise.resolve(json(DUPLICATES));
  if (url.startsWith('/api/memory/embedding/space')) return Promise.resolve(json(SPACE_OK));
  if (url.startsWith('/api/memory/embedding/')) return Promise.resolve(json({}));
  if (url === '/api/memory/lineage' && method === 'GET') return Promise.resolve(json(LINEAGE));
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
  // Pin 系统概况 as the entry sub-page (a prior drill persists the visited
  // sub-page into localStorage; re-mounting must always start on overview).
  localStorage.setItem('testbench:memory_analysis:active_subpage', 'overview');
  stateMod.set('session', { id: 'sess_1', name: 'smoke', stage: 'chat_turn' });
  mountMemoryTraceWorkspace(host);
  await tick(14);
}

// ── W1 — default sub-page is 系统概况; cards + banner + findings ───────

await mountFresh();
let sub = host.querySelector('.subpage.memory-overview');
if (!sub) { console.error(host.innerHTML.slice(0, 600)); fail('W1: overview sub-page not the default mount'); }
if (!sub.__overview) fail('W1: test hook __overview missing');
const cards = host.querySelectorAll('.mov-card');
if (cards.length < 6) fail(`W1: expected ≥6 cards, got ${cards.length}`);
const banner = host.querySelector('.mov-attention.has-issues');
if (!banner || !banner.textContent.includes('3')) fail(`W1: attention banner wrong: ${banner && banner.textContent}`);
// 3 non-info findings shown (N1, A1, D1); B2 (info) collapsed.
let shown = host.querySelectorAll('.mov-finding');
if (shown.length !== 3) fail(`W1: expected 3 important findings, got ${shown.length}`);
if (host.querySelector('.mov-finding[data-code="B2"]')) fail('W1: info finding B2 must be collapsed by default');
if (!host.querySelector('.mov-finding[data-code="N1"].sev-bad')) fail('W1: N1 should render as bad severity');
console.log('[smoke] W1 default overview + cards + banner + findings OK');

// ── W2 — info findings collapse/expand toggle ────────────────────────

const toggle = host.querySelector('.mov-info-toggle');
if (!toggle || !toggle.textContent.includes('1')) fail(`W2: info toggle missing/wrong: ${toggle && toggle.textContent}`);
click(toggle);
await tick(4);
if (!host.querySelector('.mov-finding[data-code="B2"]')) fail('W2: B2 should appear after expanding');
const b2 = host.querySelector('.mov-finding[data-code="B2"]');
if (!b2.classList.contains('sev-info')) fail('W2: B2 must be info severity (candidate, not verdict)');
console.log('[smoke] W2 info findings expand toggle OK');

// ── W3 — drill to 记忆溯源 (lineage) ─────────────────────────────────

await mountFresh();
const n1Drill = host.querySelector('.mov-finding[data-code="N1"] .mov-drill-btn');
if (!n1Drill) fail('W3: N1 drill button missing');
click(n1Drill);
await tick(14);
if (!host.querySelector('.subpage[data-subpage="lineage"]')) fail('W3: drill did not switch to lineage sub-page');
if (!host.querySelector('.memory-trace')) fail('W3: lineage page did not mount after drill');
console.log('[smoke] W3 drill → 记忆溯源 OK');

// ── W4 — drill to 向量空间 with ctx.opts (duplicates view) ────────────

await mountFresh();
const a1Drill = host.querySelector('.mov-finding[data-code="A1"] .mov-drill-btn');
if (!a1Drill) fail('W4: A1 drill button missing');
const before = fetchCalls.length;
click(a1Drill);
await tick(16);
if (!host.querySelector('.subpage[data-subpage="embedding"]')) fail('W4: drill did not switch to embedding sub-page');
const dupFetched = fetchCalls.slice(before).some((c) => c.url.startsWith('/api/memory/embedding/duplicates'));
if (!dupFetched) fail('W4: embedding sub-page did not honor ctx.opts.mode=duplicates (no /duplicates fetch)');
console.log('[smoke] W4 drill → 向量空间 honors ctx.opts (duplicates) OK');

// ── W5 — AI 体检报告 (llm OK + unavailable reason) ───────────────────

await mountFresh();
aiMode = 'llm';
const aiBtn = host.querySelector('.mov-ai-btn');
if (!aiBtn) fail('W5: AI report button missing');
click(aiBtn);
await tick(8);
const report = host.querySelector('.mov-ai-report');
if (!report || !report.textContent.includes('总体良好')) fail(`W5: AI report not rendered: ${report && report.textContent}`);
// unavailable path surfaces the actionable reason (v1.8.1 lesson).
await mountFresh();
aiMode = 'unavailable';
click(host.querySelector('.mov-ai-btn'));
await tick(8);
const reason = host.querySelector('.mov-llm-reason');
if (!reason || !reason.textContent.includes('memory')) fail(`W5: AI failure reason not surfaced: ${reason && reason.textContent}`);
console.log('[smoke] W5 AI report (llm + actionable reason) OK');

// ── W6 — 矛盾 NLI 裁决 renders verdicts with localized labels ─────────

await mountFresh();
contraMode = 'llm';
const cBtn = host.querySelector('.mov-contra-btn');
if (!cBtn) fail('W6: contradictions button missing');
click(cBtn);
await tick(8);
const verdict = host.querySelector('.mov-verdict.rel-contradiction');
if (!verdict) fail('W6: contradiction verdict not rendered');
if (!verdict.textContent.includes('互相矛盾')) fail(`W6: localized relation label missing: ${verdict.textContent}`);
console.log('[smoke] W6 contradiction NLI verdicts OK');

// ── W7 — no session empty state ──────────────────────────────────────

// Backend reports NoActiveSession even though the client thinks it has one
// (e.g. server restarted) → the overview must show an honest empty state.
overviewMode = 'no_session';
await mountFresh();
if (!host.querySelector('.mov-empty')) fail('W7: expected an empty-state for no_session');
console.log('[smoke] W7 no_session empty state OK');

console.log('\nP40 MEMORY OVERVIEW UI SMOKE OK');
