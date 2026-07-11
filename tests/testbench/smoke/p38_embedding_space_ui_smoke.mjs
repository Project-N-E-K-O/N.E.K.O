/**
 * P38 UI smoke — jsdom mount for the 向量空间 (Memory Embedding Space) sub-page
 * (P28).
 *
 * Run after touching memory_trace/embedding_space.js / workspace_memory_trace.js
 * / the memory_trace.embedding i18n namespace::
 *
 *     node tests/testbench/smoke/p38_embedding_space_ui_smoke.mjs
 *
 * Guards (jsdom has no real 2D canvas / layout metrics — we assert DOM
 * structure + data flow via the host.__embspace test hook, not pixels):
 *   V1 — mounts against the REAL i18n dict without throwing; coverage banner
 *        reflects GET /api/memory/embedding/space meta (embedded/total/dim);
 *        a <canvas.embspace-canvas> exists for the scatter.
 *   V2 — selecting a point (host hook) fetches /neighbors and renders the
 *        neighbor list + a "在记忆溯源中查看" jump button.
 *   V3 — switching to the bridges mode fetches /bridges and renders one card
 *        per reflection with semantic/extra chips + a jump button.
 *   V4 — the bridges jump button calls ctx.goTo('lineage', {focusNodeId}),
 *        i.e. the sub-page actually switches to 记忆溯源 (cross-link).
 *   V5 — a character with zero vectors renders the empty state (embedded 0),
 *        not a crash.
 *   V6 — reducer switch: UMAP on-demand install flow flips reducer to umap and
 *        re-fetches /space?reducer=umap; switching back to PCA does not reinstall.
 *   V7 — duplicates mode fetches /duplicates, renders the threshold slider +
 *        pair list (canvas reused); raising the threshold clears the list.
 *   V8 — matrix mode fetches /matrix and renders the heatmap canvas + color scale.
 *   V9 — auto-cluster toggle fetches /clusters, renders the cluster list (medoid
 *        labels), and [用 LLM 概括] POSTs /cluster_labels and applies the labels;
 *        an LLM failure surfaces the concrete reason (which API to configure).
 */

import { fileURLToPath, pathToFileURL } from 'node:url';
import { dirname, resolve } from 'node:path';
import { createRequire } from 'node:module';

const require = createRequire(import.meta.url);
const here = dirname(fileURLToPath(import.meta.url));
const repoRoot = resolve(here, '../../..');
const jsdomPkgRoot = resolve(repoRoot, 'frontend/react-neko-chat/node_modules/jsdom');
const { JSDOM, VirtualConsole } = require(`${jsdomPkgRoot}/lib/api.js`);

// jsdom has no real 2D canvas; it emits a "Not implemented: getContext" jsdomError
// for every draw. That's expected here (we assert DOM, not pixels) — drop the noise.
const virtualConsole = new VirtualConsole();
virtualConsole.on('jsdomError', (e) => {
  if (!/getContext/.test(String(e && e.message))) console.error(e);
});

// ── configurable backend responses ──────────────────────────────────

let spaceMode = 'ok';  // 'ok' | 'empty'
let umapEnabled = false;   // flipped by the simulated /enable_umap install
let enableUmapCalls = 0;
let clusterLabelsFail = false;  // when true, /cluster_labels degrades to medoid + reason

const CLUSTER_LABELS_FAIL_REASON =
  '请先在 Settings → Models → memory 填好 base_url 与 model。';

const SPACE_OK = {
  points: [
    { id: 'fact_a', type: 'fact', entity: 'master', x: -1.2, y: 0.4, label: '主人喜欢深夜调试' },
    { id: 'fact_b', type: 'fact', entity: 'master', x: 1.1, y: -0.3, label: '主人爱喝美式咖啡' },
    { id: 'ref_1', type: 'reflection', entity: 'master', x: 1.0, y: -0.1, label: '主人偏好黑咖啡' },
    { id: 'p_1', type: 'persona', entity: 'master', x: -1.1, y: 0.5, label: '主人是夜猫子' },
  ],
  meta: {
    total: 6, embedded: 4, missing: 1, stale: 1, corrupt: 0,
    dims_present: { '8': 4 }, primary_dim: 8, primary_count: 4,
    other_space_count: 0, numpy_ok: true,
    reducer_requested: 'pca', reducer_used: 'pca', umap_available: false, warnings: [],
  },
};
const SPACE_EMPTY = {
  points: [],
  meta: {
    total: 2, embedded: 0, missing: 2, stale: 0, corrupt: 0,
    dims_present: {}, primary_dim: null, primary_count: 0,
    other_space_count: 0, numpy_ok: true,
    reducer_requested: 'pca', reducer_used: 'pca', umap_available: false, warnings: [],
  },
};
const NEIGHBORS = {
  query_id: 'fact_a', found: true,
  neighbors: [
    { id: 'p_1', type: 'persona', entity: 'master', score: 0.98, label: '主人是夜猫子' },
    { id: 'ref_1', type: 'reflection', entity: 'master', score: 0.12, label: '主人偏好黑咖啡' },
  ],
};
const BRIDGES = {
  fact_count: 2, reflection_count: 1,
  rows: [
    {
      reflection_id: 'ref_1', reflection_label: '主人偏好黑咖啡', entity: 'master',
      declared: ['fact_a', 'fact_c'],
      semantic_top: [{ fact_id: 'fact_b', score: 0.97, label: '主人爱喝美式咖啡' }],
      missing_in_declared: [{ fact_id: 'fact_b', score: 0.97, label: '主人爱喝美式咖啡' }],
      extra_in_declared: [
        { fact_id: 'fact_a', embedded: true, exists: true, label: '主人喜欢深夜调试' },
        { fact_id: 'fact_c', embedded: false, exists: true, label: '未嵌入的事实' },
      ],
      agreement: 0,
    },
  ],
};
const DUPLICATES = {
  threshold: 0.95, count: 2, capped: false, candidates: 4,
  pairs: [
    { a: 'fact_a', b: 'p_1', score: 0.999, a_type: 'fact', b_type: 'persona',
      a_entity: 'master', b_entity: 'master', a_label: '主人喜欢深夜调试',
      b_label: '主人是夜猫子', same_type: false },
    { a: 'fact_b', b: 'ref_1', score: 0.998, a_type: 'fact', b_type: 'reflection',
      a_entity: 'master', b_entity: 'master', a_label: '主人爱喝美式咖啡',
      b_label: '主人偏好黑咖啡', same_type: false },
  ],
};
const MATRIX = {
  order: ['fact_a', 'p_1', 'fact_b', 'ref_1'], n: 4, truncated: false, requested: 4,
  labels: { fact_a: '主人喜欢深夜调试', p_1: '主人是夜猫子', fact_b: '主人爱喝美式咖啡', ref_1: '主人偏好黑咖啡' },
  types: { fact_a: 'fact', p_1: 'persona', fact_b: 'fact', ref_1: 'reflection' },
  entities: { fact_a: 'master', p_1: 'master', fact_b: 'master', ref_1: 'master' },
  cells: [
    [1, 0.999, 0.02, 0.03],
    [0.999, 1, 0.04, 0.05],
    [0.02, 0.04, 1, 0.998],
    [0.03, 0.05, 0.998, 1],
  ],
};

const CLUSTERS = {
  algo: 'hdbscan', n_clusters: 2, noise_count: 0,
  assignments: { fact_a: 0, p_1: 0, fact_b: 1, ref_1: 1 },
  clusters: [
    { cluster: 0, size: 2, medoid_id: 'p_1', label: '主人是夜猫子',
      samples: ['主人是夜猫子', '主人喜欢深夜调试'], member_ids: ['p_1', 'fact_a'] },
    { cluster: 1, size: 2, medoid_id: 'fact_b', label: '主人爱喝美式咖啡',
      samples: ['主人爱喝美式咖啡', '主人偏好黑咖啡'], member_ids: ['fact_b', 'ref_1'] },
  ],
  meta: { total: 4, min_cluster_size: 2, cc_threshold: null }, warnings: [],
};
const CLUSTER_LABELS = {
  method: 'llm',
  labels: { 0: '夜间习惯', 1: '咖啡偏好' },
  clusters: CLUSTERS.clusters, algo: 'hdbscan', n_clusters: 2, noise_count: 0,
  warnings: [],
};

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

  if (url.startsWith('/api/memory/embedding/space')) {
    if (spaceMode === 'empty') return Promise.resolve(json(SPACE_EMPTY));
    const wantUmap = /reducer=umap/.test(url);
    const reducerUsed = wantUmap && umapEnabled ? 'umap' : 'pca';
    const body = {
      points: SPACE_OK.points,
      meta: { ...SPACE_OK.meta, umap_available: umapEnabled,
        reducer_requested: wantUmap ? 'umap' : 'pca', reducer_used: reducerUsed },
    };
    return Promise.resolve(json(body));
  }
  if (url.startsWith('/api/memory/embedding/neighbors')) {
    return Promise.resolve(json(NEIGHBORS));
  }
  if (url.startsWith('/api/memory/embedding/bridges')) {
    return Promise.resolve(json(BRIDGES));
  }
  if (url.startsWith('/api/memory/embedding/duplicates')) {
    const mthr = /threshold=([0-9.]+)/.exec(url);
    const thr = mthr ? parseFloat(mthr[1]) : 0.95;
    if (thr > 0.999) {
      return Promise.resolve(json({ threshold: thr, count: 0, capped: false, candidates: 4, pairs: [] }));
    }
    return Promise.resolve(json({ ...DUPLICATES, threshold: thr }));
  }
  if (url.startsWith('/api/memory/embedding/matrix')) {
    return Promise.resolve(json(MATRIX));
  }
  if (url.startsWith('/api/memory/embedding/cluster_labels')) {
    if (clusterLabelsFail) {
      return Promise.resolve(json({
        method: 'medoid',
        labels: { 0: '主人是夜猫子', 1: '主人爱喝美式咖啡' },
        clusters: CLUSTERS.clusters, algo: 'hdbscan', n_clusters: 2, noise_count: 0,
        warnings: [`LLM 概括失败, 已回退到每簇代表记忆作标签。原因: ${CLUSTER_LABELS_FAIL_REASON}`],
      }));
    }
    return Promise.resolve(json(CLUSTER_LABELS));
  }
  if (url.startsWith('/api/memory/embedding/clusters')) {
    return Promise.resolve(json(CLUSTERS));
  }
  if (url.startsWith('/api/memory/embedding/enable_umap')) {
    enableUmapCalls += 1;
    umapEnabled = true;  // install succeeded → subsequent /space sees umap
    // Simulate a successful on-demand install so the reducer flips to umap.
    return Promise.resolve(json({
      ok: true, installed: true, reducer_available: true, log: 'ok',
    }));
  }
  // lineage GET (in case the lineage sub-page mounts after a cross-link jump).
  if (url === '/api/memory/lineage' && method === 'GET') {
    return Promise.resolve(json({
      nodes: [{ id: 'ref_1', type: 'reflection', lane: 3, label: '主人偏好黑咖啡',
        status: 'promoted', entity: 'master', created_at: '2026-06-30T13:00:00',
        meta: { text: '主人偏好黑咖啡', source_fact_ids: [] }, warnings: [] }],
      edges: [],
      meta: { character: 'NEKO',
        counts: { messages: 0, recent_memos: 0, facts: 0, reflections: 1, persona: 0, corrections: 0 },
        sources_present: { events_ndjson: false, time_indexed_db: false, trace_provenance: false },
        file_warnings: [], corpus_warnings: [], node_budget: { total: 1, shown: 1, truncated: false }, edge_count: 0 },
    }));
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
function click(node) {
  node.dispatchEvent(new dom.window.MouseEvent('click', { bubbles: true, cancelable: true }));
}
function fail(msg) { throw new Error(msg); }

// Select the 向量空间 sub-page on first mount.
localStorage.setItem('testbench:memory_analysis:active_subpage', 'embedding');

// ── imports ─────────────────────────────────────────────────────────

const wsPath = resolve(here, '../static/ui/workspace_memory_trace.js');
const statePath = resolve(here, '../static/core/state.js');
const { mountMemoryTraceWorkspace } = await import(pathToFileURL(wsPath).href);
const stateMod = await import(pathToFileURL(statePath).href);

const host = document.getElementById('host');

// ── V1 — mount + coverage banner + canvas ───────────────────────────

stateMod.set('session', { id: 'sess_1', name: 'smoke', stage: 'chat_turn' });
mountMemoryTraceWorkspace(host);
await tick(14);

const sub = host.querySelector('.subpage.embedding-space');
if (!sub) { console.error(host.innerHTML.slice(0, 600)); fail('V1: embedding-space sub-page not mounted'); }
const cov = host.querySelector('.embspace-coverage');
if (!cov || !cov.textContent.includes('4/6')) fail(`V1: coverage banner wrong: ${cov && cov.textContent}`);
if (!cov.textContent.includes('8')) fail('V1: coverage should report primary dim 8');
if (!host.querySelector('canvas.embspace-canvas')) fail('V1: scatter canvas missing');
if (!host.querySelector('.embspace-mode-chip')) fail('V1: mode chips missing');
console.log('[smoke] V1 mount + coverage + canvas OK');

// ── V2 — select a point → neighbors + jump button ───────────────────

if (!sub.__embspace) fail('V2: test hook __embspace missing on sub-page');
sub.__embspace.selectPoint('fact_a');
await tick(10);
if (!fetchCalls.some((c) => c.url.startsWith('/api/memory/embedding/neighbors'))) {
  fail('V2: neighbors not fetched on select');
}
const nbrItems = host.querySelectorAll('.embspace-nbr-item');
if (nbrItems.length !== NEIGHBORS.neighbors.length) {
  fail(`V2: expected ${NEIGHBORS.neighbors.length} neighbors, got ${nbrItems.length}`);
}
if (!host.querySelector('.embspace-detail').textContent.includes('主人是夜猫子')) {
  fail('V2: nearest neighbor label not shown');
}
if (!host.querySelector('.embspace-detail .embspace-jump-btn')) {
  fail('V2: jump-to-lineage button missing in detail');
}
console.log('[smoke] V2 select → neighbors + jump button OK');

// ── V6 — reducer switch: UMAP not yet installed → on-demand install ──

if (!host.querySelector('.embspace-reducer-chip')) fail('V6: reducer chips missing in sidebar');
if (sub.__embspace.state.umapAvailable !== false) fail('V6: umap should start unavailable');
sub.__embspace.setReducer('umap');
await tick(12);
if (enableUmapCalls !== 1) fail(`V6: expected 1 enable_umap call, got ${enableUmapCalls}`);
if (sub.__embspace.state.reducer !== 'umap') fail('V6: reducer should be umap after install');
if (sub.__embspace.state.reducerUsed !== 'umap') fail('V6: backend should report reducer_used=umap');
if (!fetchCalls.some((c) => /\/embedding\/space\?reducer=umap/.test(c.url))) {
  fail('V6: space not re-fetched with reducer=umap');
}
// switch back to PCA (no reinstall).
sub.__embspace.setReducer('pca');
await tick(8);
if (sub.__embspace.state.reducer !== 'pca') fail('V6: reducer should switch back to pca');
if (enableUmapCalls !== 1) fail('V6: switching to pca must not trigger another install');
console.log('[smoke] V6 reducer UMAP on-demand install + switch OK');

// ── V7 — duplicates mode: threshold slider + pair list + canvas reuse ──

sub.__embspace.setMode('duplicates');
await tick(12);
if (!fetchCalls.some((c) => c.url.startsWith('/api/memory/embedding/duplicates'))) {
  fail('V7: duplicates not fetched on mode switch');
}
if (!host.querySelector('.embspace-dup-slider')) fail('V7: threshold slider missing');
if (!host.querySelector('canvas.embspace-canvas')) fail('V7: scatter canvas missing in duplicates mode');
const dupItems = host.querySelectorAll('.embspace-dup-item');
if (dupItems.length !== DUPLICATES.pairs.length) {
  fail(`V7: expected ${DUPLICATES.pairs.length} pair rows, got ${dupItems.length}`);
}
sub.__embspace.setThreshold(0.9995);
await tick(12);
if (host.querySelectorAll('.embspace-dup-item').length !== 0) {
  fail('V7: high threshold should clear the pair list');
}
console.log('[smoke] V7 duplicates threshold + pair list OK');

// ── V8 — matrix mode: heatmap canvas + subset/color scale ──

sub.__embspace.setMode('matrix');
await tick(12);
if (!fetchCalls.some((c) => c.url.startsWith('/api/memory/embedding/matrix'))) {
  fail('V8: matrix not fetched on mode switch');
}
if (!host.querySelector('canvas.embspace-matrix-canvas')) fail('V8: matrix heatmap canvas missing');
if (!host.querySelector('.embspace-matrix-scale')) fail('V8: matrix color scale legend missing');
sub.__embspace.setMode('scatter');
await tick(8);
console.log('[smoke] V8 matrix heatmap + subset info OK');

// ── V9 — auto-cluster toggle: clusters fetch + list + LLM relabel ──

if (!host.querySelector('.embspace-cluster-toggle input')) {
  fail('V9: auto-cluster toggle missing in scatter sidebar');
}
sub.__embspace.toggleCluster(true);
await tick(12);
if (!fetchCalls.some((c) => c.url.startsWith('/api/memory/embedding/clusters'))) {
  fail('V9: /clusters not fetched on toggle');
}
if (sub.__embspace.state.clusterOn !== true) fail('V9: clusterOn should be true');
const cluItems = host.querySelectorAll('.embspace-cluster-item');
if (cluItems.length !== CLUSTERS.clusters.length) {
  fail(`V9: expected ${CLUSTERS.clusters.length} cluster rows, got ${cluItems.length}`);
}
// default labels = medoid text.
if (!host.querySelector('.embspace-cluster').textContent.includes('主人是夜猫子')) {
  fail('V9: medoid default label not shown');
}
// LLM relabel.
sub.__embspace.labelClusters();
await tick(12);
if (!fetchCalls.some((c) => c.url.startsWith('/api/memory/embedding/cluster_labels')
  && c.method === 'POST')) {
  fail('V9: /cluster_labels POST not issued');
}
if (!host.querySelector('.embspace-cluster').textContent.includes('夜间习惯')) {
  fail('V9: LLM cluster label not applied to the list');
}
// LLM failure (e.g. memory model not configured) must surface the concrete
// reason so the tester knows which API to fill — not just a generic fallback.
clusterLabelsFail = true;
sub.__embspace.labelClusters();
await tick(12);
const reasonEl = host.querySelector('.embspace-cluster-llm-reason');
if (!reasonEl || !reasonEl.textContent.includes(CLUSTER_LABELS_FAIL_REASON)) {
  fail(`V9: LLM failure reason not surfaced: ${reasonEl && reasonEl.textContent}`);
}
clusterLabelsFail = false;
// toggle off → cluster section gone.
sub.__embspace.toggleCluster(false);
await tick(8);
if (sub.__embspace.state.clusterOn !== false) fail('V9: clusterOn should be false');
if (host.querySelector('.embspace-cluster-item')) {
  fail('V9: cluster list should be removed when toggled off');
}
console.log('[smoke] V9 auto-cluster toggle + LLM relabel OK');

// ── V3 — bridges mode ───────────────────────────────────────────────

sub.__embspace.setMode('bridges');
await tick(10);
if (!fetchCalls.some((c) => c.url.startsWith('/api/memory/embedding/bridges'))) {
  fail('V3: bridges not fetched on mode switch');
}
const cards = host.querySelectorAll('.embspace-bridge-card');
if (cards.length !== BRIDGES.rows.length) fail(`V3: expected ${BRIDGES.rows.length} bridge card(s), got ${cards.length}`);
const cardText = host.querySelector('.embspace-bridge-card').textContent;
if (!cardText.includes('主人爱喝美式咖啡')) fail('V3: semantic-top fact not shown in bridge card');
// issue-1 regression: a declared-but-unembedded source must render its real
// text (+ a marker), never a bare id like "fact_c (∅)".
const unembChip = Array.from(host.querySelectorAll('.embspace-bridge-chip.extra'))
  .find((c) => c.classList.contains('unembedded'));
if (!unembChip) fail('V3: unembedded declared-source chip missing (.unembedded)');
if (!unembChip.textContent.includes('未嵌入的事实')) {
  fail(`V3: unembedded chip lost its text (regression): "${unembChip.textContent}"`);
}
if (unembChip.textContent.trim() === 'fact_c') fail('V3: unembedded chip shows bare id');
console.log('[smoke] V3 bridges mode renders cards OK');

// ── V4 — bridges jump cross-links to 记忆溯源 ───────────────────────

const jumpBtn = host.querySelector('.embspace-bridge-card .embspace-jump-btn');
if (!jumpBtn) fail('V4: bridge jump button missing');
click(jumpBtn);
await tick(14);
if (!host.querySelector('.subpage.memory-trace')) {
  fail('V4: cross-link did not switch to the 记忆溯源 sub-page');
}
console.log('[smoke] V4 cross-link jump to 记忆溯源 OK');

// ── V5 — empty (no vectors) state ───────────────────────────────────

spaceMode = 'empty';
// Re-mount fresh into the embedding sub-page.
localStorage.setItem('testbench:memory_analysis:active_subpage', 'embedding');
mountMemoryTraceWorkspace(host);
await tick(14);
if (!host.querySelector('.subpage.embedding-space')) fail('V5: embedding sub-page not re-mounted');
const cov2 = host.querySelector('.embspace-coverage');
if (!cov2 || !cov2.textContent.includes('0/2')) fail(`V5: empty coverage wrong: ${cov2 && cov2.textContent}`);
if (!host.querySelector('.embspace-empty')) fail('V5: empty state not rendered for zero vectors');
if (host.querySelector('canvas.embspace-canvas')) fail('V5: canvas should be absent when no points');
console.log('[smoke] V5 zero-vector empty state OK');

console.log('\nP38 EMBEDDING SPACE UI SMOKE OK');
