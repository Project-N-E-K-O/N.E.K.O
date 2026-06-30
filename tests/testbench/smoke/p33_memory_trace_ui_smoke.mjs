/**
 * P33 UI smoke — jsdom mount for the Memory Trace workspace (P27.2).
 *
 * Run after touching workspace_memory_trace.js / memory_trace/*.js / the
 * memory_trace i18n namespace::
 *
 *     node tests/testbench/smoke/p33_memory_trace_ui_smoke.mjs
 *
 * Guards:
 *   U1 — mounts against the REAL i18n dict without throwing. Catches the
 *        recurring ``i18n(key)(arg)`` / missing-key crashes (function leaves
 *        counts_fmt / budget_fmt are exercised).
 *   U2 — renders the SVG graph verbatim from GET /api/memory/lineage:
 *        5 lane labels, one .mtrace-node per snapshot node, one .mtrace-edge
 *        per snapshot edge, dashed class for heuristic edges.
 *   U3 — clicking a node fills the detail rail with upstream/downstream links.
 *   U4 — clicking a node auto-focuses + COMPACTLY RELAYS OUT to just its
 *        sub-tree (unrelated nodes removed); blank-click restores the overview.
 *   U5 — 409 NoCharacterSelected renders the no_character empty state, not a
 *        crash; empty snapshot renders the empty state.
 *   U6 — Tier C single-node attribution draws a dashed heuristic edge.
 *   U7 — one-click 推测全部源头 batch attribution adds a dashed edge (asserted
 *        in overview, where heuristic edges are visible).
 *   U8 — 显示未连线 toggle reveals the parked isolated node.
 *   U9 — edge level-of-detail: a graph with > cap heuristic edges hides them in
 *        overview (with hiddenHeuristic telemetry) and reveals a focused node's
 *        sub-tree edges; heuristic edges carry no arrowhead marker (perf).
 */

import { fileURLToPath, pathToFileURL } from 'node:url';
import { dirname, resolve } from 'node:path';
import { createRequire } from 'node:module';

const require = createRequire(import.meta.url);
const here = dirname(fileURLToPath(import.meta.url));
const repoRoot = resolve(here, '../../..');
const jsdomPkgRoot = resolve(repoRoot, 'frontend/react-neko-chat/node_modules/jsdom');
const { JSDOM } = require(`${jsdomPkgRoot}/lib/api.js`);

// ── configurable lineage response ───────────────────────────────────

// Two structural chains + one isolated message node:
//   chain 1: fact_a -> ref_1 -> prom_1   (5 connected nodes total)
//   chain 2: fact_b -> ref_2             (unrelated to chain 1)
//   msg_1: isolated (no edge) -> hidden by default, parked grid on toggle.
const SNAPSHOT = {
  nodes: [
    { id: 'msg_1', type: 'message', lane: 0, label: '你好呀', status: 'user',
      entity: null, created_at: '2026-04-18T10:00:00',
      meta: { content: '你好呀', role: 'user', origin: 'time_indexed_db' }, warnings: [] },
    { id: 'fact_a', type: 'fact', lane: 2, label: '主人喜欢深夜调试', status: 'absorbed',
      entity: 'master', created_at: '2026-04-18T12:00:00',
      meta: { text: '主人喜欢深夜调试', importance: 6, tags: ['习惯'], absorbed: true }, warnings: [] },
    { id: 'ref_1', type: 'reflection', lane: 3, label: '主人作息偏夜', status: 'promoted',
      entity: 'master', created_at: '2026-04-18T13:00:00',
      meta: { text: '主人作息偏夜', source_fact_ids: ['fact_a'] }, warnings: [] },
    { id: 'prom_1', type: 'persona_entry', lane: 4, label: '主人是夜猫子', status: 'active',
      entity: 'master', created_at: '2026-04-18T14:00:00',
      meta: { text: '主人是夜猫子', source: 'reflection', source_id: 'ref_1' }, warnings: [] },
    { id: 'fact_b', type: 'fact', lane: 2, label: '主人爱喝美式', status: 'absorbed',
      entity: 'master', created_at: '2026-04-18T12:30:00',
      meta: { text: '主人爱喝美式', importance: 4, tags: ['口味'], absorbed: true }, warnings: [] },
    { id: 'ref_2', type: 'reflection', lane: 3, label: '主人偏好黑咖啡', status: 'pending',
      entity: 'master', created_at: '2026-04-18T13:30:00',
      meta: { text: '主人偏好黑咖啡', source_fact_ids: ['fact_b'] }, warnings: [] },
  ],
  edges: [
    { source: 'fact_a', target: 'ref_1', relation: 'source_fact', confidence: 'persisted', score: null, note: null },
    { source: 'ref_1', target: 'prom_1', relation: 'promoted_from', confidence: 'persisted', score: null, note: null },
    { source: 'fact_b', target: 'ref_2', relation: 'source_fact', confidence: 'persisted', score: null, note: null },
  ],
  meta: {
    character: 'NEKO',
    counts: { messages: 1, recent_memos: 0, facts: 2, reflections: 2, persona: 1, corrections: 0 },
    sources_present: { events_ndjson: false, time_indexed_db: true, trace_provenance: false },
    file_warnings: [],
    corpus_warnings: [],
    node_budget: { total: 6, shown: 6, truncated: false },
    edge_count: 3,
  },
};
const CONNECTED_COUNT = 5;  // fact_a, ref_1, prom_1, fact_b, ref_2 (msg_1 isolated)

let lineageMode = 'ok';  // 'ok' | 'empty' | 'no_character'

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

  if (url === '/api/memory/lineage/attribute' && method === 'POST') {
    let useLlm = false;
    try { useLlm = !!JSON.parse(init.body || '{}').use_llm; } catch { /* ignore */ }
    // When the LLM precision pass was requested but degraded, the backend hands
    // back a structured llm_fallback (requested/used/reason); the UI must surface
    // it persistently instead of silently showing text-similarity results.
    const fallback = useLlm
      ? { requested: 'llm', used: 'text', reason: 'NoMemoryModelConfigured: 未配置记忆模型 API' }
      : null;
    return Promise.resolve(json({
      node_id: 'fact_a',
      method: 'text',
      llm_fallback: fallback,
      target_preview: '主人喜欢深夜调试',
      candidates: [
        { id: 'tdb:s1:0', type: 'message', lane: 0, label: '我爱深夜调试', status: 'user',
          entity: null, created_at: '2026-04-18T09:00:00',
          meta: { content: '我爱深夜调试', role: 'user', origin: 'time_indexed_db' }, warnings: [] },
      ],
      edges: [
        { source: 'tdb:s1:0', target: 'fact_a', relation: 'attributed_from',
          confidence: 'heuristic', score: 0.42, note: null },
      ],
      warnings: fallback ? ['LLM 精判失败, 回退文本相似度: ' + fallback.reason] : [],
    }));
  }
  if (url === '/api/memory/lineage/attribute_all' && method === 'POST') {
    return Promise.resolve(json({
      method: 'text',
      candidates: [
        { id: 'tdb:s2:1', type: 'message', lane: 0, label: '我是夜猫子', status: 'user',
          entity: null, created_at: '2026-04-18T08:00:00',
          meta: { content: '我是夜猫子', role: 'user', origin: 'time_indexed_db' }, warnings: [] },
      ],
      edges: [
        { source: 'tdb:s2:1', target: 'prom_1', relation: 'attributed_from',
          confidence: 'heuristic', score: 0.31, note: null },
      ],
      warnings: [],
      attributed_nodes: 1,
      target_total: 3,
    }));
  }
  if (url === '/api/memory/lineage' && method === 'GET') {
    if (lineageMode === 'no_character') {
      return Promise.resolve(jsonErr(409, { error_type: 'NoCharacterSelected', message: '未选择角色' }));
    }
    if (lineageMode === 'empty') {
      return Promise.resolve(json({
        nodes: [], edges: [],
        meta: { character: 'NEKO', counts: { messages: 0, recent_memos: 0, facts: 0, reflections: 0, persona: 0, corrections: 0 },
          sources_present: { events_ndjson: false, time_indexed_db: false, trace_provenance: false },
          file_warnings: [], corpus_warnings: [], node_budget: { total: 0, shown: 0, truncated: false }, edge_count: 0 },
      }));
    }
    return Promise.resolve(json(SNAPSHOT));
  }
  return Promise.resolve(jsonErr(404, { error_type: 'NotFound' }));
}

// ── jsdom bootstrap ─────────────────────────────────────────────────

const dom = new JSDOM(
  `<!doctype html><html><body><section id="host" class="workspace"></section></body></html>`,
  { url: 'http://localhost/' },
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

async function tick(n = 8) {
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

// ── U1/U2 — mount + render graph ────────────────────────────────────

// P29 made 系统概况 (overview) the default sub-page; this suite asserts the
// 记忆溯源 (lineage) sub-page, so pin it explicitly before mounting (mirrors
// p38 seeding 'embedding'). Without this the workspace would mount overview and
// none of the lineage DOM below would exist.
localStorage.setItem('testbench:memory_analysis:active_subpage', 'lineage');
stateMod.set('session', { id: 'sess_1', name: 'smoke', stage: 'chat_turn' });
mountMemoryTraceWorkspace(host);
await tick(12);

const svg = host.querySelector('svg.mtrace-svg');
if (!svg) { console.error(host.innerHTML.slice(0, 500)); fail('U2: SVG graph not rendered'); }

const laneLabels = host.querySelectorAll('.mtrace-lane-label');
if (laneLabels.length !== 5) fail(`U2: expected 5 lane labels, got ${laneLabels.length}`);

// Default view shows only the connected flow; the isolated msg_1 is parked
// (hidden) until the toolbar toggle (see U8).
const nodeEls = host.querySelectorAll('.mtrace-node');
if (nodeEls.length !== CONNECTED_COUNT) {
  fail(`U2: expected ${CONNECTED_COUNT} connected nodes by default, got ${nodeEls.length}`);
}
if ([...nodeEls].some((n) => n.getAttribute('data-node-id') === 'msg_1')) {
  fail('U2: isolated msg_1 should be hidden by default (toggle reveals it)');
}
const edgeEls = host.querySelectorAll('.mtrace-edge');
if (edgeEls.length !== SNAPSHOT.edges.length) {
  fail(`U2: expected ${SNAPSHOT.edges.length} edges, got ${edgeEls.length}`);
}
// counts_fmt / budget_fmt function leaves did not crash → summary present.
if (!host.querySelector('.mtrace-counts')) fail('U1: counts summary missing (i18n fn leaf?)');
if (!host.querySelector('.mtrace-reload-btn')) fail('U2: reload button missing');
console.log('[smoke] U1/U2 mount + graph render OK');

// ── U3 — click node fills detail rail ───────────────────────────────

const refNode = [...nodeEls].find((n) => n.getAttribute('data-node-id') === 'ref_1');
if (!refNode) fail('U3: ref_1 node not found');
click(refNode);
await tick(6);

const detail = host.querySelector('.mtrace-detail');
if (!detail || !detail.textContent.includes('主人作息偏夜')) {
  fail('U3: detail content not shown after node click');
}
const linkBtns = host.querySelectorAll('.mtrace-link-btn');
// ref_1 has 1 upstream (fact_a) + 1 downstream (prom_1).
if (linkBtns.length !== 2) fail(`U3: expected 2 lineage links, got ${linkBtns.length}`);
const linkText = [...linkBtns].map((b) => b.textContent).join(' | ');
if (!linkText.includes('主人喜欢深夜调试') || !linkText.includes('主人是夜猫子')) {
  fail(`U3: upstream/downstream links wrong: ${linkText}`);
}
console.log('[smoke] U3 detail rail upstream/downstream OK');

// ── U4 — AUTO-focus on select compacts to the sub-tree; blank cancels ─

// Selecting ref_1 in U3 should have AUTO-entered focus (no button needed) and
// the graph RELAYS OUT to only that sub-tree: related = {ref_1, fact_a
// (ancestor), prom_1 (descendant)}. The unrelated chain (fact_b/ref_2) is
// removed from the canvas entirely (compact relayout), not just dimmed.
const focusedIds = [...host.querySelectorAll('.mtrace-node')]
  .map((n) => n.getAttribute('data-node-id'));
const EXPECTED_FOCUS = ['ref_1', 'fact_a', 'prom_1'];
if (focusedIds.length !== EXPECTED_FOCUS.length
    || !EXPECTED_FOCUS.every((id) => focusedIds.includes(id))) {
  fail(`U4: focus should relayout to sub-tree ${EXPECTED_FOCUS}, got ${focusedIds}`);
}
if (focusedIds.includes('fact_b')) {
  fail('U4: unrelated fact_b should be removed from the compact focus layout');
}
// Root centering: the focused node (ref_1) is laid out as the forest root via
// undirected adjacency, so its Y sits between its two branches (fact_a upstream
// + prom_1 downstream), not pinned to the top.
const nodeY = (id) => {
  const g = [...host.querySelectorAll('.mtrace-node')].find(
    (n) => n.getAttribute('data-node-id') === id);
  const m = /translate\(\s*[-\d.]+\s+([-\d.]+)\s*\)/.exec(g.getAttribute('transform') || '');
  return m ? parseFloat(m[1]) : NaN;
};
const yRef = nodeY('ref_1');
const yFact = nodeY('fact_a');
const yProm = nodeY('prom_1');
const lo = Math.min(yFact, yProm);
const hi = Math.max(yFact, yProm);
if (!(yRef > lo && yRef < hi)) {
  fail(`U4: focused root ref_1 should be vertically centered between its branches (ref=${yRef}, branches=${lo}..${hi})`);
}
console.log('[smoke] U4 auto-focus compact relayout + root centered OK');

// Blank-click on the SVG canvas cancels focus → original overview layout back.
function blankClick(el2) {
  for (const type of ['pointerdown', 'pointerup']) {
    const ev = new dom.window.Event(type, { bubbles: true, cancelable: true });
    Object.assign(ev, { button: 0, clientX: 5, clientY: 5, pointerId: 1 });
    el2.dispatchEvent(ev);
  }
}
blankClick(host.querySelector('svg.mtrace-svg'));
await tick(6);
const afterBlank = host.querySelectorAll('.mtrace-node');
if (afterBlank.length !== CONNECTED_COUNT) {
  fail(`U4: blank-click should restore overview (${CONNECTED_COUNT} nodes), got ${afterBlank.length}`);
}
if (![...afterBlank].some((n) => n.getAttribute('data-node-id') === 'fact_b')) {
  fail('U4: blank-click should bring the unrelated chain back into overview');
}
if ([...afterBlank].some((n) => n.classList.contains('is-dimmed'))) {
  fail('U4: overview after blank-click should have no dimmed nodes');
}
console.log('[smoke] U4b blank-click restores overview OK');

// ── U6 — Tier C attribution draws a dashed heuristic edge ───────────

// Reselect fact_a (attributable, auto-focuses) and click its text button.
// In focus mode only the focused sub-tree's edges render, so the dashed
// tdb:s1:0 -> fact_a edge (fact_a is now an ancestor target) is visible.
const factForAttr = [...host.querySelectorAll('.mtrace-node')].find(
  (n) => n.getAttribute('data-node-id') === 'fact_a');
click(factForAttr);
await tick(4);
const attrBtns = host.querySelectorAll('.mtrace-attr-btns .btn');
if (attrBtns.length < 2) fail('U6: attribute buttons not rendered for fact node');
const edgesBefore = host.querySelectorAll('.mtrace-edge').length;
click(attrBtns[0]);  // text similarity
await tick(10);
const heuristicEdges = host.querySelectorAll('.mtrace-edge--heuristic');
if (heuristicEdges.length !== 1) fail(`U6: expected 1 dashed heuristic edge, got ${heuristicEdges.length}`);
const edgesAfter = host.querySelectorAll('.mtrace-edge').length;
if (edgesAfter !== edgesBefore + 1) fail('U6: attributed edge not added to graph');
if (![...host.querySelectorAll('.mtrace-node')].some((n) => n.getAttribute('data-node-id') === 'tdb:s1:0')) {
  fail('U6: candidate turn node was not injected into graph');
}
if (!host.querySelector('.mtrace-attr-status')) fail('U6: attribution status message missing');
console.log('[smoke] U6 Tier C attribution dashed edge OK');

// ── U6b — LLM attribution that degrades surfaces a PERSISTENT fallback notice ──
// Clicking "分析来源 (LLM 精判)" with no model configured must not silently show
// text-similarity results: the status rail shows .fallback + the concrete reason.
click(attrBtns[1]);  // LLM precision pass → backend degrades to text
await tick(10);
const fbStatus = host.querySelector('.mtrace-attr-status.fallback');
if (!fbStatus) fail('U6b: LLM fallback was silent — no persistent .mtrace-attr-status.fallback');
if (!fbStatus.textContent.includes('回退')) fail('U6b: fallback notice does not say it fell back');
if (!fbStatus.textContent.includes('未配置记忆模型 API')) {
  fail(`U6b: fallback notice missing the reason: "${fbStatus.textContent}"`);
}
console.log('[smoke] U6b LLM→text attribution fallback is surfaced OK');

// ── U7 — one-click "推测全部源头" batch attribution ──────────────────

// Exit focus to overview first: the batch edge (tdb:s2:1 -> prom_1) is NOT in
// fact_a's sub-tree, so it would be (correctly) culled in focus mode. In the
// overview the small graph stays under the heuristic cap, so every dashed edge
// is visible — that's where a tester confirms the batch worked.
blankClick(host.querySelector('svg.mtrace-svg'));
await tick(6);
const attrAllBtn = host.querySelector('.mtrace-attrall-btn');
if (!attrAllBtn) fail('U7: attribute-all button missing from toolbar');
const heurBefore = host.querySelectorAll('.mtrace-edge--heuristic').length;
click(attrAllBtn);
await tick(10);
const heurAfter = host.querySelectorAll('.mtrace-edge--heuristic').length;
if (heurAfter !== heurBefore + 1) {
  fail(`U7: expected one more dashed edge after attribute-all, ${heurBefore} -> ${heurAfter}`);
}
if (![...host.querySelectorAll('.mtrace-node')].some((n) => n.getAttribute('data-node-id') === 'tdb:s2:1')) {
  fail('U7: batch candidate node not injected into graph');
}
console.log('[smoke] U7 one-click attribute-all OK');

// ── U8 — toggle "显示未连线" reveals the parked isolated node ──────────

const isoBtn = host.querySelector('.mtrace-isolated-btn');
if (!isoBtn) fail('U8: isolated-toggle button missing from toolbar');
if ([...host.querySelectorAll('.mtrace-node')].some(
  (n) => n.getAttribute('data-node-id') === 'msg_1')) {
  fail('U8: msg_1 should still be hidden before toggle');
}
click(isoBtn);
await tick(6);
if (![...host.querySelectorAll('.mtrace-node')].some(
  (n) => n.getAttribute('data-node-id') === 'msg_1')) {
  fail('U8: msg_1 should appear in parked grid after toggle');
}
const parked = host.querySelector('.mtrace-node.is-parked');
if (!parked) fail('U8: parked node should carry .is-parked class');
if (!host.querySelector('.mtrace-zone-divider')) fail('U8: parked-zone divider missing');
console.log('[smoke] U8 isolated-node toggle + parked grid OK');

// ── U5 — 409 no_character + empty states ────────────────────────────

lineageMode = 'no_character';
const reloadBtn = host.querySelector('.mtrace-reload-btn');
click(reloadBtn);
await tick(10);
if (!host.textContent.includes('先选一个角色')) {
  console.error(host.textContent.slice(0, 300));
  fail('U5: 409 should render no_character empty state');
}
if (host.querySelector('svg.mtrace-svg')) fail('U5: no graph should render on 409');

lineageMode = 'empty';
click(host.querySelector('.mtrace-reload-btn'));
await tick(10);
if (!host.textContent.includes('该角色暂无记忆')) {
  fail('U5: empty snapshot should render empty state');
}
console.log('[smoke] U5 no_character + empty states OK');

// ── U9 — a big attribute-all batch (within cap) RENDERS in overview ──────
// User report: after 推测全部源头 the conversation/summary source nodes vanished
// in overview instead of showing (the manual promises the top-similarity edges,
// backend-capped at 400, are drawn). The frontend cap is now a high safety valve
// (600) above the backend's 400, so a normal batch shows; only a pathological
// accumulation past it would be hidden.

const graphPath = resolve(here, '../static/ui/memory_trace/lineage_graph.js');
const { renderLineageGraph } = await import(pathToFileURL(graphPath).href);

const BIG_N = 120;  // a hefty batch, still < HEURISTIC_OVERVIEW_CAP (600)
const bigNodes = [
  { id: 'F', type: 'fact', lane: 2, label: '目标事实', status: 'absorbed',
    entity: null, created_at: '2026-04-18T12:00:00', meta: { text: '目标事实' }, warnings: [] },
];
const bigEdges = [];
for (let i = 0; i < BIG_N; i += 1) {
  bigNodes.push({
    id: `m${i}`, type: 'message', lane: 0, label: `对话${i}`, status: 'user',
    entity: null, created_at: '2026-04-18T09:00:00',
    meta: { content: `对话${i}`, role: 'user', origin: 'time_indexed_db' }, warnings: [],
  });
  bigEdges.push({
    source: `m${i}`, target: 'F', relation: 'attributed_from',
    confidence: 'heuristic', score: 0.3, note: null,
  });
}
const bigSnap = { nodes: bigNodes, edges: bigEdges, meta: { counts: {} } };

// Overview: all 120 dashed edges + their conversation source nodes must render.
const gOverview = renderLineageGraph(bigSnap, { focusId: null });
const drawnOverview = gOverview.querySelectorAll('.mtrace-edge--heuristic').length;
if (drawnOverview !== BIG_N) {
  fail(`U9: overview should draw all ${BIG_N} attributed edges, drew ${drawnOverview}`);
}
if (gOverview.__mtrace.hiddenHeuristic !== 0) {
  fail(`U9: nothing should be hidden under cap, got ${gOverview.__mtrace.hiddenHeuristic}`);
}
const drawnMsgNodes = [...gOverview.querySelectorAll('.mtrace-node')]
  .filter((n) => /^m\d+$/.test(n.getAttribute('data-node-id') || '')).length;
if (drawnMsgNodes !== BIG_N) {
  fail(`U9: overview should render all ${BIG_N} conversation nodes, got ${drawnMsgNodes}`);
}

// Focus F: its whole sub-tree (all 120 message ancestors) stays visible.
const gFocus = renderLineageGraph(bigSnap, { focusId: 'F' });
const drawnFocus = gFocus.querySelectorAll('.mtrace-edge--heuristic').length;
if (drawnFocus !== BIG_N) {
  fail(`U9: focusing F should keep all ${BIG_N} sub-tree edges, drew ${drawnFocus}`);
}
// Heuristic edges must NOT carry an arrowhead marker (the per-frame perf sink).
const anyHeur = gFocus.querySelector('.mtrace-edge--heuristic');
if (anyHeur && anyHeur.getAttribute('marker-end')) {
  fail('U9: heuristic edges must not use an arrowhead marker (perf)');
}
console.log('[smoke] U9 attribute-all batch renders in overview + no-marker OK');

// ── U10 — focusing a downstream node reveals the FULL multi-hop chain ─────
// User report: clicking a reflection only showed its facts, not the
// conversation sub-tree under those facts. Focus must follow heuristic edges
// transitively so the chain reflection <- facts <- conversations appears.
const ldNodes = [
  { id: 'rX', type: 'reflection', lane: 3, label: '反思', status: 'pending',
    entity: null, created_at: '2026-04-18T13:00:00', meta: { text: '反思' }, warnings: [] },
  { id: 'fA', type: 'fact', lane: 2, label: '事实A', status: 'absorbed',
    entity: null, created_at: '2026-04-18T12:00:00', meta: { text: '事实A' }, warnings: [] },
  { id: 'fB', type: 'fact', lane: 2, label: '事实B', status: 'absorbed',
    entity: null, created_at: '2026-04-18T12:01:00', meta: { text: '事实B' }, warnings: [] },
];
const ldEdges = [
  { source: 'fA', target: 'rX', relation: 'source_fact', confidence: 'persisted', score: null, note: null },
  { source: 'fB', target: 'rX', relation: 'source_fact', confidence: 'persisted', score: null, note: null },
];
// a few guessed conversation sources behind each fact (well under the cap)
for (let i = 0; i < 3; i += 1) {
  for (const f of ['fA', 'fB']) {
    const mid = `lm_${f}_${i}`;
    ldNodes.push({ id: mid, type: 'message', lane: 0, label: `对话${i}`, status: 'user',
      entity: null, created_at: '2026-04-18T09:00:00',
      meta: { content: `对话${i}`, role: 'user', origin: 'time_indexed_db' }, warnings: [] });
    ldEdges.push({ source: mid, target: f, relation: 'attributed_from',
      confidence: 'heuristic', score: 0.3, note: null });
  }
}
const ldSnap = { nodes: ldNodes, edges: ldEdges, meta: { counts: {} } };

// Overview: the conversation source nodes are drawn (not hidden).
const gLd = renderLineageGraph(ldSnap, { focusId: null });
if (!gLd.querySelector('.mtrace-node[data-node-id^="lm_"]')) {
  fail('U10: overview should render the attributed conversation source nodes');
}

// Focus the reflection: the WHOLE chain (facts + their conversations) shows.
const gLdFocus = renderLineageGraph(ldSnap, { focusId: 'rX' });
const focusIds = [...gLdFocus.querySelectorAll('.mtrace-node')]
  .map((n) => n.getAttribute('data-node-id'));
if (!focusIds.includes('fA') || !focusIds.includes('fB')) {
  fail('U10: focusing reflection should show its source facts');
}
const focusedMsgs = focusIds.filter((id) => id.startsWith('lm_')).length;
if (focusedMsgs !== 6) {
  fail(`U10: focusing reflection should reveal all 6 conversation sources, got ${focusedMsgs}`);
}
console.log('[smoke] U10 focus reveals full multi-hop conversation chain OK');

// ── U11 — overview: a downstream node is centered on its sources ──────────
// Regression (user report + 抓包): reflections were ending up ABOVE their whole
// fact block. Cause: each lane was packed against pre-pack child positions, so
// once the (spread-by-their-conversations) fact lane was packed the reflection
// no longer matched it. Fix: leaf->root barycenter sweep re-centers each node on
// its already-finalized children. Assert the reflection sits at the mean of its
// facts and strictly inside their [min,max] block.
const ctNodes = [
  { id: 'R', type: 'reflection', lane: 3, label: '反思', status: 'pending',
    entity: null, created_at: '2026-04-18T13:00:00', meta: { text: '反思' }, warnings: [] },
];
const ctEdges = [];
for (let f = 0; f < 4; f += 1) {
  ctNodes.push({ id: `cf${f}`, type: 'fact', lane: 2, label: `事实${f}`, status: 'absorbed',
    entity: null, created_at: `2026-04-18T12:0${f}:00`, meta: { text: `事实${f}` }, warnings: [] });
  ctEdges.push({ source: `cf${f}`, target: 'R', relation: 'source_fact',
    confidence: 'persisted', score: null, note: null });
  // each fact carries several guessed conversation sources so the fact lane is
  // genuinely spread (and thus gets packed) — the condition that broke centering.
  for (let m = 0; m < 5; m += 1) {
    const mid = `cm${f}_${m}`;
    ctNodes.push({ id: mid, type: 'message', lane: 0, label: `对话${f}-${m}`, status: 'user',
      entity: null, created_at: '2026-04-18T09:00:00',
      meta: { content: `对话${f}-${m}`, role: 'user', origin: 'time_indexed_db' }, warnings: [] });
    ctEdges.push({ source: mid, target: `cf${f}`, relation: 'attributed_from',
      confidence: 'heuristic', score: 0.3, note: null });
  }
}
const ctSnap = { nodes: ctNodes, edges: ctEdges, meta: { counts: {} } };
const gCt = renderLineageGraph(ctSnap, { focusId: null });
const ctY = (id) => {
  const g = [...gCt.querySelectorAll('.mtrace-node')].find(
    (n) => n.getAttribute('data-node-id') === id);
  if (!g) return NaN;
  const m = /translate\(\s*[-\d.]+\s+([-\d.]+)\s*\)/.exec(g.getAttribute('transform') || '');
  return m ? parseFloat(m[1]) : NaN;
};
const factYs = [0, 1, 2, 3].map((f) => ctY(`cf${f}`));
if (factYs.some((y) => !Number.isFinite(y))) fail('U11: facts must be placed');
const rY = ctY('R');
const fLo = Math.min(...factYs);
const fHi = Math.max(...factYs);
const fMean = factYs.reduce((a, b) => a + b, 0) / factYs.length;
if (!(rY >= fLo - 1 && rY <= fHi + 1)) {
  fail(`U11: reflection must sit inside its fact block [${Math.round(fLo)},${Math.round(fHi)}], got ${Math.round(rY)}`);
}
if (Math.abs(rY - fMean) > 2) {
  fail(`U11: reflection must sit at the barycenter of its facts (mean=${Math.round(fMean)}), got ${Math.round(rY)}`);
}
console.log('[smoke] U11 overview downstream node centered on its sources OK');

console.log('\nP33 MEMORY TRACE UI SMOKE OK');
