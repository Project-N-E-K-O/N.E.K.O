/**
 * memory_trace/embedding_space.js — 向量空间 (Memory Embedding Space) sub-page.
 *
 * Second sub-page of 记忆系统分析 (P28). Read-only analysis of the active
 * character's on-disk embedding vectors. The frontend renders the backend
 * aggregator's payloads verbatim (blueprint §3.1):
 *   GET /api/memory/embedding/space      → 体检 (health) + 2D PCA 散点
 *   GET /api/memory/embedding/neighbors  → cosine 最近邻 (on point select)
 *   GET /api/memory/embedding/bridges    → 语义源 vs 结构源 (与 P27 联动)
 *
 * Two switchable main views (mode): 'scatter' (canvas) and 'bridges' (list).
 * A coverage banner (health) is always shown so a character with no vectors
 * gets an honest "import an already-embedded character" prompt instead of a
 * blank canvas.
 *
 * Scatter is drawn on a native <canvas> with self-managed pan/zoom/pick so it
 * stays smooth at thousands of points (no SVG, no chart lib — blueprint §6).
 * In jsdom (smoke) the 2D context / layout metrics are absent; every canvas /
 * measurement path is guarded so the page still mounts and its data flow is
 * testable via the host.__embspace hook.
 */

import { api } from '../../core/api.js';
import { i18n } from '../../core/i18n.js';
import { store, on } from '../../core/state.js';
import { toast } from '../../core/toast.js';
import { el } from '../_dom.js';

const TYPES = ['fact', 'reflection', 'persona'];
const TYPE_COLORS = {
  fact: '#5b8def',
  reflection: '#e6a23c',
  persona: '#3fb27f',
};

// P28.5 cluster palette (cycled by cluster id); noise (-1 / unassigned) = grey.
const CLUSTER_PALETTE = [
  '#5b8def', '#e6a23c', '#3fb27f', '#d9534f', '#9b59b6', '#1abc9c',
  '#e67e22', '#2c82c9', '#c0392b', '#16a085', '#8e44ad', '#f39c12',
];
function clusterColor(cid) {
  if (cid == null || cid < 0) return '#b8c0cc';
  return CLUSTER_PALETTE[cid % CLUSTER_PALETTE.length];
}

const VALID_MODES = ['scatter', 'duplicates', 'matrix', 'bridges'];

export function mountEmbeddingSpacePage(host, ctx) {
  host.classList.add('embedding-space');
  host.innerHTML = '';

  // P29 cross-link entry: 系统概况 drills in with {mode, threshold, cluster,
  // selectId}. Mode uses real ids (scatter/duplicates/matrix/bridges); cluster
  // is the scatter overlay toggle; selectId picks a point (pulls neighbors).
  // Backward compatible: no ctx.opts ⇒ default scatter (unchanged).
  const entryOpts = (ctx && ctx.opts) || {};
  const entryMode = VALID_MODES.includes(entryOpts.mode) ? entryOpts.mode : 'scatter';
  let pendingSelectId = entryOpts.selectId || null;
  let entryApplied = false;

  const state = {
    phase: 'loading',            // loading|ready|no_session|no_character|error
    errorMsg: '',
    data: null,                  // {points, meta}
    mode: entryMode,             // scatter|duplicates|matrix|bridges
    selectedId: null,
    neighbors: null,             // {found, neighbors}
    neighborsLoading: false,
    bridges: null,               // {rows, fact_count, reflection_count}
    bridgesLoading: false,
    duplicates: null,            // {pairs, threshold, count, capped, candidates}
    duplicatesLoading: false,
    threshold: (typeof entryOpts.threshold === 'number'
      ? entryOpts.threshold : 0.95),  // ④ duplicates cosine cutoff (slider)
    dupSelected: null,           // {a, b} emphasized pair on the canvas
    matrix: null,                // {order, cells, n, truncated, requested, labels, types}
    matrixLoading: false,
    matrixHover: null,           // {i, j} hovered heatmap cell
    typeFilter: { fact: true, reflection: true, persona: true },
    view: null,                  // {scale, tx, ty} in CSS px; null = auto-fit
    hoverId: null,
    reducer: 'pca',              // pca|umap — requested dimensionality reducer
    reducerUsed: 'pca',          // what the backend actually used (may fall back)
    umapAvailable: false,        // umap-learn importable on the server
    umapInstalling: false,       // /enable_umap in flight
    umapMsg: '',                 // last install failure log (shown in sidebar)
    clusterOn: !!entryOpts.cluster,  // ⑦ auto-cluster overlay (scatter only, P28.5)
    clusters: null,              // {algo, n_clusters, noise_count, assignments, clusters}
    clustersLoading: false,
    clusterLabels: {},           // {str(cluster): label} — medoid default / LLM refined
    clusterLabeling: false,      // /cluster_labels (LLM) in flight
    clusterLLMTried: false,      // whether the LLM naming pass has run
    clusterLLMMethod: 'medoid',  // 'llm' | 'medoid' (last cluster_labels method)
    clusterLLMWarnings: [],      // backend reasons (e.g. which API to configure)
  };

  // Persistent canvas + draw handle (not rebuilt on every selection so the
  // viewport survives interaction).
  let canvas = null;
  let resizeObs = null;
  let tooltipEl = null;
  let mcanvas = null;          // ⑤ matrix heatmap canvas
  let mtooltipEl = null;
  let mresizeObs = null;

  const goToLineage = (nodeId) => {
    if (ctx && typeof ctx.goTo === 'function') {
      ctx.goTo('lineage', { focusNodeId: nodeId });
    }
  };

  // ── reducer (降维) switch + on-demand UMAP install (P28.4) ──
  function onReducer(val) {
    if (val === state.reducer && !(val === 'umap' && !state.umapAvailable)) return;
    if (val === 'pca') {
      state.reducer = 'pca'; state.umapMsg = ''; state.view = null;
      reload();
      return;
    }
    // val === 'umap'
    if (state.umapAvailable) {
      state.reducer = 'umap'; state.umapMsg = ''; state.view = null;
      reload();
    } else {
      enableUmap();
    }
  }

  async function enableUmap() {
    state.umapInstalling = true; state.umapMsg = '';
    renderAll();
    const res = await api.post('/api/memory/embedding/enable_umap', {});
    state.umapInstalling = false;
    const d = res.ok ? res.data : null;
    if (d && d.reducer_available) {
      state.umapAvailable = true;
      state.reducer = 'umap';
      state.umapMsg = '';
      state.view = null;
      reload();
    } else {
      state.umapAvailable = false;
      state.umapMsg = (d && d.log) || res.error?.message || 'install failed';
      renderAll();
    }
  }

  // ── data ──
  async function reload() {
    if (!store.session) { state.phase = 'no_session'; renderAll(); return; }
    state.phase = 'loading';
    renderAll();
    const res = await api.get(
      '/api/memory/embedding/space?reducer=' + encodeURIComponent(state.reducer),
      { expectedStatuses: [404, 409] });
    if (res.ok) {
      state.data = res.data;
      state.phase = 'ready';
      const meta = res.data.meta || {};
      state.umapAvailable = !!meta.umap_available;
      state.reducerUsed = meta.reducer_used || 'pca';
      const ids = new Set((res.data.points || []).map((p) => p.id));
      if (state.selectedId && !ids.has(state.selectedId)) {
        state.selectedId = null;
        state.neighbors = null;
      }
      // P29 drill-in: once the space is loaded, honor the entry view exactly
      // once (mode-specific lazy loads + cluster overlay + point selection).
      if (!entryApplied) {
        entryApplied = true;
        if (state.mode === 'bridges' && !state.bridges) loadBridges();
        else if (state.mode === 'duplicates' && !state.duplicates) loadDuplicates();
        else if (state.mode === 'matrix' && !state.matrix) loadMatrix();
        if (state.clusterOn && !state.clusters && !state.clustersLoading) loadClusters();
        if (pendingSelectId && ids.has(pendingSelectId)) {
          const want = pendingSelectId;
          pendingSelectId = null;
          selectPoint(want);
        }
      }
    } else if (res.status === 409 && res.error?.type === 'NoCharacterSelected') {
      state.phase = 'no_character';
    } else if (res.status === 404 && res.error?.type === 'NoActiveSession') {
      state.phase = 'no_session';
    } else {
      // Any other failure (incl. a bare 404 "Not Found" — e.g. the backend
      // route is missing because the server wasn't restarted after an update)
      // is a real error, NOT "no session". Surfacing it honestly avoids the
      // misleading "请新建会话" prompt when a session is actually active.
      state.phase = 'error';
      state.errorMsg = res.error?.message || `HTTP ${res.status}`;
    }
    renderAll();
  }

  async function loadBridges() {
    state.bridgesLoading = true;
    renderAll();
    const res = await api.get('/api/memory/embedding/bridges',
      { expectedStatuses: [404, 409] });
    state.bridgesLoading = false;
    state.bridges = res.ok ? res.data : { rows: [], fact_count: 0, reflection_count: 0 };
    renderAll();
  }

  async function loadDuplicates() {
    state.duplicatesLoading = true;
    renderAll();
    const qs = '?threshold=' + encodeURIComponent(state.threshold);
    const res = await api.get('/api/memory/embedding/duplicates' + qs,
      { expectedStatuses: [404, 409] });
    state.duplicatesLoading = false;
    state.duplicates = res.ok ? res.data
      : { pairs: [], threshold: state.threshold, count: 0, capped: false };
    renderAll();
  }

  async function loadMatrix() {
    state.matrixLoading = true;
    renderAll();
    // ⑤ heatmap renders a subset — drive it from the current type filter,
    // capped (backend re-clips to MATRIX_MAX_N and reports truncated).
    const ids = visiblePoints().map((p) => p.id);
    const qs = ids.length ? ('?ids=' + encodeURIComponent(ids.join(','))) : '';
    const res = await api.get('/api/memory/embedding/matrix' + qs,
      { expectedStatuses: [404, 409] });
    state.matrixLoading = false;
    state.matrix = res.ok ? res.data
      : { order: [], cells: [], n: 0, truncated: false, requested: 0, labels: {}, types: {} };
    renderAll();
  }

  // ── ⑦ auto-cluster (P28.5) ──
  function onToggleCluster(checked) {
    state.clusterOn = !!checked;
    if (state.clusterOn && !state.clusters && !state.clustersLoading) {
      loadClusters();
    } else {
      renderAll();
    }
  }

  async function loadClusters() {
    state.clustersLoading = true;
    renderAll();
    const res = await api.get('/api/memory/embedding/clusters',
      { expectedStatuses: [404, 409] });
    state.clustersLoading = false;
    if (res.ok) {
      state.clusters = res.data;
      state.clusterLabels = {};
      for (const c of (res.data.clusters || [])) {
        state.clusterLabels[String(c.cluster)] = c.label;
      }
      state.clusterLLMTried = false;
      state.clusterLLMMethod = 'medoid';
      state.clusterLLMWarnings = [];
    } else {
      state.clusters = {
        algo: 'none', n_clusters: 0, noise_count: 0, assignments: {}, clusters: [],
      };
    }
    renderAll();
  }

  async function labelClustersLLM() {
    if (state.clusterLabeling) return;
    state.clusterLabeling = true;
    state.clusterLLMWarnings = [];
    renderAll();
    const res = await api.post('/api/memory/embedding/cluster_labels', {},
      { expectedStatuses: [404, 409] });
    state.clusterLabeling = false;
    if (res.ok && res.data) {
      if (res.data.labels) state.clusterLabels = res.data.labels;
      state.clusterLLMMethod = res.data.method || 'medoid';
      state.clusterLLMWarnings = res.data.warnings || [];
    } else {
      // Endpoint itself failed (e.g. no session / server error) — show why.
      state.clusterLLMMethod = 'medoid';
      state.clusterLLMWarnings = [res.error?.message || `HTTP ${res.status}`];
    }
    state.clusterLLMTried = true;
    // Also toast the reason(s) so it's noticed even if the sidebar is scrolled.
    if (state.clusterLLMMethod !== 'llm') {
      for (const w of state.clusterLLMWarnings) toast.warn(w);
    }
    renderAll();
  }

  function clusterActive() {
    return state.mode === 'scatter' && state.clusterOn && state.clusters
      && Array.isArray(state.clusters.clusters) && state.clusters.clusters.length > 0;
  }

  async function selectPoint(id) {
    if (!id) {
      state.selectedId = null;
      state.neighbors = null;
      drawScatter();
      updateSidebar();
      return;
    }
    state.selectedId = id;
    state.neighbors = null;
    state.neighborsLoading = true;
    drawScatter();
    updateSidebar();
    const qs = `?id=${encodeURIComponent(id)}&k=10`;
    const res = await api.get('/api/memory/embedding/neighbors' + qs,
      { expectedStatuses: [404, 409] });
    // Guard against an out-of-order response: if the user clicked another point
    // (or cleared the selection) while this request was in flight, its result
    // no longer belongs to the current selection — drop it.
    if (state.selectedId !== id) return;
    state.neighborsLoading = false;
    state.neighbors = res.ok ? res.data : { found: false, neighbors: [] };
    drawScatter();
    updateSidebar();
  }

  // ── visible point set (type filter) ──
  function visiblePoints() {
    const pts = (state.data && state.data.points) || [];
    return pts.filter((p) => state.typeFilter[p.type] !== false);
  }

  // ── canvas geometry ──
  function canvasSize() {
    if (!canvas) return { w: 0, h: 0 };
    const w = canvas.clientWidth || 0;
    const h = canvas.clientHeight || 0;
    return { w, h };
  }

  function fitView() {
    const pts = visiblePoints();
    const { w, h } = canvasSize();
    if (!pts.length || w <= 0 || h <= 0) { state.view = { scale: 1, tx: w / 2, ty: h / 2 }; return; }
    let minX = Infinity, maxX = -Infinity, minY = Infinity, maxY = -Infinity;
    for (const p of pts) {
      if (p.x < minX) minX = p.x; if (p.x > maxX) maxX = p.x;
      if (p.y < minY) minY = p.y; if (p.y > maxY) maxY = p.y;
    }
    const pad = 36;
    const spanX = Math.max(maxX - minX, 1e-6);
    const spanY = Math.max(maxY - minY, 1e-6);
    const scale = Math.min((w - 2 * pad) / spanX, (h - 2 * pad) / spanY);
    const cx = (minX + maxX) / 2;
    const cy = (minY + maxY) / 2;
    state.view = { scale, tx: w / 2 - cx * scale, ty: h / 2 - cy * scale };
  }

  function worldToScreen(p) {
    const v = state.view;
    return { sx: p.x * v.scale + v.tx, sy: p.y * v.scale + v.ty };
  }

  function pick(px, py) {
    if (!state.view) return null;
    let best = null;
    let bestD = 9 * 9;  // 9px radius²
    for (const p of visiblePoints()) {
      const { sx, sy } = worldToScreen(p);
      const dx = sx - px, dy = sy - py;
      const d = dx * dx + dy * dy;
      if (d < bestD) { bestD = d; best = p; }
    }
    return best ? best.id : null;
  }

  function visiblePairs() {
    const pairs = (state.duplicates && state.duplicates.pairs) || [];
    return pairs.filter((p) => state.typeFilter[p.a_type] !== false
      && state.typeFilter[p.b_type] !== false);
  }

  function drawScatter() {
    if ((state.mode !== 'scatter' && state.mode !== 'duplicates') || !canvas) return;
    let cgGet = null;
    // jsdom throws "Not implemented" on getContext; treat any failure as
    // "no canvas pixels available" and skip drawing (DOM structure still fine).
    try {
      cgGet = typeof canvas.getContext === 'function' ? canvas.getContext('2d') : null;
    } catch { cgGet = null; }
    if (!cgGet) return;
    const cg = cgGet;
    const { w, h } = canvasSize();
    if (w <= 0 || h <= 0) return;
    const dpr = (typeof window !== 'undefined' && window.devicePixelRatio) || 1;
    if (canvas.width !== Math.round(w * dpr) || canvas.height !== Math.round(h * dpr)) {
      canvas.width = Math.round(w * dpr);
      canvas.height = Math.round(h * dpr);
    }
    if (!state.view) fitView();
    cg.setTransform(dpr, 0, 0, dpr, 0, 0);
    cg.clearRect(0, 0, w, h);

    const pts = visiblePoints();
    const byId = new Map(pts.map((p) => [p.id, p]));
    const dupMode = state.mode === 'duplicates';
    const clusterMode = clusterActive();
    const assign = clusterMode ? (state.clusters.assignments || {}) : null;
    const neighborIds = new Set(
      ((state.neighbors && state.neighbors.neighbors) || []).map((n) => n.id));

    // ④ duplicate links: one line per visible pair, opacity ∝ how far above
    // threshold (the selected pair drawn bold red).
    const dupNodeIds = new Set();
    if (dupMode) {
      const thr = (state.duplicates && state.duplicates.threshold) || state.threshold;
      const sel = state.dupSelected;
      for (const pr of visiblePairs()) {
        dupNodeIds.add(pr.a); dupNodeIds.add(pr.b);
        if (!byId.has(pr.a) || !byId.has(pr.b)) continue;
        const s = worldToScreen(byId.get(pr.a));
        const t = worldToScreen(byId.get(pr.b));
        const isSelPair = sel && ((sel.a === pr.a && sel.b === pr.b));
        const norm = Math.max(0, Math.min(1, (pr.score - thr) / Math.max(1e-6, 1 - thr)));
        cg.strokeStyle = isSelPair
          ? 'rgba(217,83,79,0.95)' : `rgba(217,83,79,${0.25 + 0.5 * norm})`;
        cg.lineWidth = isSelPair ? 2.5 : 1;
        cg.beginPath(); cg.moveTo(s.sx, s.sy); cg.lineTo(t.sx, t.sy); cg.stroke();
      }
    } else if (state.selectedId && byId.has(state.selectedId) && neighborIds.size) {
      // neighbor links from the selected point (scatter mode).
      const s = worldToScreen(byId.get(state.selectedId));
      cg.strokeStyle = 'rgba(120,140,170,0.45)';
      cg.lineWidth = 1;
      for (const nid of neighborIds) {
        if (!byId.has(nid)) continue;
        const t = worldToScreen(byId.get(nid));
        cg.beginPath(); cg.moveTo(s.sx, s.sy); cg.lineTo(t.sx, t.sy); cg.stroke();
      }
    }

    for (const p of pts) {
      const { sx, sy } = worldToScreen(p);
      const isSel = p.id === state.selectedId;
      const isNbr = neighborIds.has(p.id);
      const isHover = p.id === state.hoverId;
      const inDup = dupMode && dupNodeIds.has(p.id);
      let r = 3.2;
      if (isSel) r = 6.5; else if (isNbr || inDup) r = 5; else if (isHover) r = 4.5;
      cg.beginPath();
      cg.arc(sx, sy, r, 0, Math.PI * 2);
      cg.fillStyle = clusterMode
        ? clusterColor(assign[p.id]) : (TYPE_COLORS[p.type] || '#9aa4b2');
      if (dupMode) cg.globalAlpha = inDup ? 1 : 0.35;
      else cg.globalAlpha = (state.selectedId && !isSel && !isNbr) ? 0.5 : 1;
      cg.fill();
      if (isSel || isNbr || inDup) {
        cg.globalAlpha = 1;
        cg.lineWidth = isSel ? 2 : 1.4;
        cg.strokeStyle = isSel ? '#1f2a3a' : (inDup ? '#a33' : '#445');
        cg.stroke();
      }
      cg.globalAlpha = 1;
    }

    if (clusterMode) drawClusterLabels(cg, byId);
  }

  // Draw each cluster's name at its (visible-member) screen centroid. Labels
  // come from state.clusterLabels (medoid default, LLM-refined on demand).
  function drawClusterLabels(cg, byId) {
    cg.save();
    cg.globalAlpha = 1;
    cg.font = '600 12px system-ui, -apple-system, sans-serif';
    cg.textAlign = 'center';
    cg.textBaseline = 'middle';
    for (const cl of state.clusters.clusters) {
      let sx = 0, sy = 0, cnt = 0;
      for (const mid of (cl.member_ids || [])) {
        const p = byId.get(mid);
        if (!p) continue;
        const s = worldToScreen(p);
        sx += s.sx; sy += s.sy; cnt += 1;
      }
      if (!cnt) continue;
      sx /= cnt; sy /= cnt;
      const text = (state.clusterLabels && state.clusterLabels[String(cl.cluster)])
        || cl.label || '';
      if (!text) continue;
      const tw = (cg.measureText ? cg.measureText(text).width : text.length * 7) + 10;
      cg.fillStyle = 'rgba(255,255,255,0.82)';
      cg.fillRect(sx - tw / 2, sy - 9, tw, 18);
      cg.fillStyle = clusterColor(cl.cluster);
      cg.fillText(text, sx, sy);
    }
    cg.restore();
  }

  // ── canvas events ──
  function bindCanvas() {
    if (!canvas) return;
    let dragging = false;
    let moved = false;
    let lastX = 0, lastY = 0;

    const localXY = (ev) => {
      const rect = typeof canvas.getBoundingClientRect === 'function'
        ? canvas.getBoundingClientRect() : { left: 0, top: 0 };
      return { x: ev.clientX - rect.left, y: ev.clientY - rect.top };
    };

    canvas.addEventListener('mousedown', (ev) => {
      dragging = true; moved = false;
      const p = localXY(ev); lastX = p.x; lastY = p.y;
    });
    canvas.addEventListener('mousemove', (ev) => {
      const p = localXY(ev);
      if (dragging && state.view) {
        const dx = p.x - lastX, dy = p.y - lastY;
        if (Math.abs(dx) + Math.abs(dy) > 2) moved = true;
        state.view.tx += dx; state.view.ty += dy;
        lastX = p.x; lastY = p.y;
        drawScatter();
        return;
      }
      const hid = pick(p.x, p.y);
      if (hid !== state.hoverId) {
        state.hoverId = hid;
        drawScatter();
        showTooltip(hid, ev);
      } else if (hid) {
        showTooltip(hid, ev);
      }
    });
    const endDrag = (ev) => {
      if (dragging && !moved) {
        const p = localXY(ev);
        const id = pick(p.x, p.y);
        selectPoint(id || null);
      }
      dragging = false;
    };
    canvas.addEventListener('mouseup', endDrag);
    canvas.addEventListener('mouseleave', () => {
      dragging = false; state.hoverId = null; hideTooltip(); drawScatter();
    });
    canvas.addEventListener('wheel', (ev) => {
      ev.preventDefault();
      if (!state.view) fitView();
      const p = localXY(ev);
      const factor = ev.deltaY < 0 ? 1.15 : 1 / 1.15;
      // zoom toward cursor: keep world point under cursor fixed.
      const wx = (p.x - state.view.tx) / state.view.scale;
      const wy = (p.y - state.view.ty) / state.view.scale;
      state.view.scale *= factor;
      state.view.tx = p.x - wx * state.view.scale;
      state.view.ty = p.y - wy * state.view.scale;
      drawScatter();
    }, { passive: false });
  }

  function showTooltip(id, ev) {
    if (!tooltipEl) return;
    if (!id) { hideTooltip(); return; }
    const p = (state.data.points || []).find((q) => q.id === id);
    if (!p) { hideTooltip(); return; }
    tooltipEl.textContent = `${i18n('memory_trace.embedding.type.' + p.type)} · ${p.label || p.id}`;
    tooltipEl.style.display = 'block';
    const host2 = canvas && canvas.parentElement;
    const rect = host2 && typeof host2.getBoundingClientRect === 'function'
      ? host2.getBoundingClientRect() : { left: 0, top: 0 };
    tooltipEl.style.left = `${ev.clientX - rect.left + 12}px`;
    tooltipEl.style.top = `${ev.clientY - rect.top + 12}px`;
  }
  function hideTooltip() {
    if (tooltipEl) tooltipEl.style.display = 'none';
  }

  // ── ⑤ matrix heatmap (own canvas) ──
  function heatColor(v) {
    // sequential light→dark blue ramp over cosine [0,1] (clamped).
    const t = Math.max(0, Math.min(1, v));
    const r = Math.round(247 + (8 - 247) * t);
    const g = Math.round(251 + (48 - 251) * t);
    const b = Math.round(255 + (107 - 255) * t);
    return `rgb(${r},${g},${b})`;
  }

  function matrixGeom() {
    const m = state.matrix;
    if (!mcanvas || !m || !m.n) return null;
    const w = mcanvas.clientWidth || 0;
    const h = mcanvas.clientHeight || 0;
    if (w <= 0 || h <= 0) return null;
    const cell = Math.max(2, Math.floor(Math.min(w, h) / m.n));
    return { w, h, cell, size: cell * m.n };
  }

  function drawMatrix() {
    if (state.mode !== 'matrix' || !mcanvas) return;
    let cg = null;
    try {
      cg = typeof mcanvas.getContext === 'function' ? mcanvas.getContext('2d') : null;
    } catch { cg = null; }
    if (!cg) return;
    const g = matrixGeom();
    if (!g) return;
    const m = state.matrix;
    const dpr = (typeof window !== 'undefined' && window.devicePixelRatio) || 1;
    if (mcanvas.width !== Math.round(g.w * dpr) || mcanvas.height !== Math.round(g.h * dpr)) {
      mcanvas.width = Math.round(g.w * dpr);
      mcanvas.height = Math.round(g.h * dpr);
    }
    cg.setTransform(dpr, 0, 0, dpr, 0, 0);
    cg.clearRect(0, 0, g.w, g.h);
    for (let i = 0; i < m.n; i += 1) {
      const row = m.cells[i] || [];
      for (let j = 0; j < m.n; j += 1) {
        cg.fillStyle = heatColor(row[j] != null ? row[j] : 0);
        cg.fillRect(j * g.cell, i * g.cell, g.cell, g.cell);
      }
    }
    const hv = state.matrixHover;
    if (hv) {
      cg.strokeStyle = '#1f2a3a';
      cg.lineWidth = 1.5;
      cg.strokeRect(hv.j * g.cell + 0.5, hv.i * g.cell + 0.5, g.cell - 1, g.cell - 1);
    }
  }

  function bindMatrixCanvas() {
    if (!mcanvas) return;
    const localXY = (ev) => {
      const rect = typeof mcanvas.getBoundingClientRect === 'function'
        ? mcanvas.getBoundingClientRect() : { left: 0, top: 0 };
      return { x: ev.clientX - rect.left, y: ev.clientY - rect.top };
    };
    mcanvas.addEventListener('mousemove', (ev) => {
      const g = matrixGeom();
      const m = state.matrix;
      if (!g || !m) return;
      const p = localXY(ev);
      const j = Math.floor(p.x / g.cell);
      const i = Math.floor(p.y / g.cell);
      if (i < 0 || j < 0 || i >= m.n || j >= m.n) {
        if (state.matrixHover) { state.matrixHover = null; drawMatrix(); hideMatrixTip(); }
        return;
      }
      state.matrixHover = { i, j };
      drawMatrix();
      showMatrixTip(i, j, ev);
    });
    mcanvas.addEventListener('mouseleave', () => {
      state.matrixHover = null; drawMatrix(); hideMatrixTip();
    });
  }

  function showMatrixTip(i, j, ev) {
    if (!mtooltipEl) return;
    const m = state.matrix;
    const ri = m.order[i];
    const cj = m.order[j];
    const score = (m.cells[i] && m.cells[i][j] != null) ? m.cells[i][j] : 0;
    mtooltipEl.textContent =
      `${m.labels[ri] || ri} × ${m.labels[cj] || cj} = ${score}`;
    mtooltipEl.style.display = 'block';
    const host2 = mcanvas && mcanvas.parentElement;
    const rect = host2 && typeof host2.getBoundingClientRect === 'function'
      ? host2.getBoundingClientRect() : { left: 0, top: 0 };
    mtooltipEl.style.left = `${ev.clientX - rect.left + 12}px`;
    mtooltipEl.style.top = `${ev.clientY - rect.top + 12}px`;
  }
  function hideMatrixTip() {
    if (mtooltipEl) mtooltipEl.style.display = 'none';
  }

  // ── render: toolbar ──
  function renderToolbar() {
    const bar = el('div', { className: 'embspace-toolbar' });
    bar.append(el('h2', { className: 'embspace-title' },
      i18n('memory_trace.embedding.title')));
    const right = el('div', { className: 'embspace-toolbar-right' });
    if (state.phase === 'ready') {
      const chip = (mode, key) => el('button', {
        className: 'btn embspace-mode-chip' + (state.mode === mode ? ' active' : ''),
        onClick: () => {
          if (state.mode === mode) return;
          state.mode = mode;
          if (mode === 'bridges' && !state.bridges && !state.bridgesLoading) {
            loadBridges();
          } else if (mode === 'duplicates' && !state.duplicates && !state.duplicatesLoading) {
            loadDuplicates();
          } else if (mode === 'matrix' && !state.matrix && !state.matrixLoading) {
            loadMatrix();
          } else {
            renderAll();
          }
        },
      }, i18n(key));
      right.append(
        chip('scatter', 'memory_trace.embedding.mode.scatter'),
        chip('duplicates', 'memory_trace.embedding.mode.duplicates'),
        chip('matrix', 'memory_trace.embedding.mode.matrix'),
        chip('bridges', 'memory_trace.embedding.mode.bridges'),
      );
    }
    right.append(el('button', {
      className: 'btn embspace-reload-btn',
      onClick: () => reload(),
    }, i18n('memory_trace.reload')));
    bar.append(right);
    return bar;
  }

  // ── render: coverage banner (health) ──
  function renderCoverage() {
    const m = (state.data && state.data.meta) || {};
    const box = el('div', { className: 'embspace-coverage' });
    if (state.phase !== 'ready') return box;
    box.append(el('span', { className: 'embspace-cov-item' },
      i18n('memory_trace.embedding.cov.embedded_fmt', m.embedded || 0, m.total || 0)));
    if (m.missing) {
      box.append(el('span', { className: 'embspace-cov-item warn' },
        i18n('memory_trace.embedding.cov.missing_fmt', m.missing)));
    }
    if (m.stale) {
      box.append(el('span', { className: 'embspace-cov-item warn' },
        i18n('memory_trace.embedding.cov.stale_fmt', m.stale)));
    }
    if (m.primary_dim) {
      box.append(el('span', { className: 'embspace-cov-item' },
        i18n('memory_trace.embedding.cov.dim_fmt', m.primary_dim, m.primary_count || 0)));
    }
    if (m.other_space_count) {
      box.append(el('span', { className: 'embspace-cov-item warn' },
        i18n('memory_trace.embedding.cov.other_space_fmt', m.other_space_count)));
    }
    for (const w of m.warnings || []) {
      box.append(el('span', { className: 'embspace-cov-item warn' }, w));
    }
    return box;
  }

  // ── render: scatter main ──
  function renderScatterMain() {
    const main = el('div', { className: 'embspace-main embspace-scatter' });
    const pts = visiblePoints();
    if (!pts.length) {
      main.append(el('div', { className: 'empty-state embspace-empty' },
        el('h3', {}, i18n('memory_trace.embedding.empty.heading')),
        el('p', {}, i18n('memory_trace.embedding.empty.body'))));
      return main;
    }
    const wrap = el('div', { className: 'embspace-canvas-wrap' });
    canvas = el('canvas', { className: 'embspace-canvas' });
    tooltipEl = el('div', { className: 'embspace-tooltip', style: { display: 'none' } });
    wrap.append(canvas, tooltipEl);
    main.append(wrap);
    bindCanvas();
    // fit + draw after layout exists.
    const schedule = typeof requestAnimationFrame === 'function'
      ? requestAnimationFrame : (fn) => setTimeout(fn, 0);
    schedule(() => {
      if (state.view === null) fitView();
      drawScatter();
    });
    // Re-bind the observer to the freshly mounted wrap. renderScatterMain runs
    // on every re-render, discarding the previous canvas/wrap — keeping the old
    // observer bound to a detached node would silently miss future resizes.
    if (typeof ResizeObserver === 'function') {
      if (resizeObs) { try { resizeObs.disconnect(); } catch { /* ignore */ } }
      resizeObs = new ResizeObserver(() => { drawScatter(); });
      try { resizeObs.observe(wrap); } catch { /* ignore */ }
    }
    return main;
  }

  // ── render: bridges main ──
  function renderBridgesMain() {
    const main = el('div', { className: 'embspace-main embspace-bridges' });
    if (state.bridgesLoading) {
      main.append(el('div', { className: 'empty-state' },
        i18n('memory_trace.embedding.loading')));
      return main;
    }
    const b = state.bridges || { rows: [] };
    main.append(el('p', { className: 'embspace-bridges-intro hint' },
      i18n('memory_trace.embedding.bridges.intro')));
    if (!b.rows.length) {
      main.append(el('div', { className: 'empty-state' },
        el('p', {}, i18n('memory_trace.embedding.bridges.empty'))));
      return main;
    }
    const list = el('div', { className: 'embspace-bridge-list' });
    for (const row of b.rows) {
      const card = el('div', { className: 'embspace-bridge-card' });
      const head = el('div', { className: 'embspace-bridge-head' });
      head.append(el('span', { className: 'embspace-bridge-refl' },
        row.reflection_label || row.reflection_id));
      head.append(el('button', {
        className: 'btn embspace-jump-btn',
        title: i18n('memory_trace.embedding.bridges.jump_hint'),
        onClick: () => goToLineage(row.reflection_id),
      }, i18n('memory_trace.embedding.bridges.jump')));
      card.append(head);
      // semantic top
      const sem = el('div', { className: 'embspace-bridge-row' });
      sem.append(el('span', { className: 'embspace-bridge-tag' },
        i18n('memory_trace.embedding.bridges.semantic')));
      for (const s of row.semantic_top || []) {
        sem.append(el('span', {
          className: 'embspace-bridge-chip' +
            (row.declared.includes(s.fact_id) ? ' agree' : ' miss'),
          title: s.label || s.fact_id,
        }, `${s.label || s.fact_id} (${s.score})`));
      }
      card.append(sem);
      // declared / extra
      if ((row.extra_in_declared || []).length) {
        const ex = el('div', { className: 'embspace-bridge-row' });
        ex.append(el('span', { className: 'embspace-bridge-tag' },
          i18n('memory_trace.embedding.bridges.extra')));
        for (const x of row.extra_in_declared) {
          const lbl = x.label || x.fact_id;
          let suffix = '';
          let cls = ' extra';
          if (x.exists === false) {
            suffix = ' ' + i18n('memory_trace.embedding.bridges.fact_missing');
            cls = ' extra missing';
          } else if (!x.embedded) {
            suffix = ' ' + i18n('memory_trace.embedding.bridges.fact_unembedded');
            cls = ' extra unembedded';
          }
          ex.append(el('span', {
            className: 'embspace-bridge-chip' + cls,
            title: lbl,
          }, lbl + suffix));
        }
        card.append(ex);
      }
      list.append(card);
    }
    main.append(list);
    return main;
  }

  // ── render: duplicates main (reuses scatter canvas) ──
  function renderDuplicatesMain() {
    if (state.duplicatesLoading) {
      return el('div', { className: 'embspace-main' },
        el('div', { className: 'empty-state' }, i18n('memory_trace.embedding.loading')));
    }
    return renderScatterMain();
  }

  // ── render: matrix main (own heatmap canvas) ──
  function renderMatrixMain() {
    const main = el('div', { className: 'embspace-main embspace-matrix' });
    if (state.matrixLoading) {
      main.append(el('div', { className: 'empty-state' },
        i18n('memory_trace.embedding.loading')));
      return main;
    }
    const m = state.matrix || { n: 0 };
    if (!m.n) {
      main.append(el('div', { className: 'empty-state embspace-empty' },
        el('h3', {}, i18n('memory_trace.embedding.matrix.empty_heading')),
        el('p', {}, i18n('memory_trace.embedding.matrix.empty_body'))));
      return main;
    }
    const wrap = el('div', { className: 'embspace-matrix-wrap' });
    mcanvas = el('canvas', { className: 'embspace-matrix-canvas' });
    mtooltipEl = el('div', { className: 'embspace-tooltip', style: { display: 'none' } });
    wrap.append(mcanvas, mtooltipEl);
    main.append(wrap);
    bindMatrixCanvas();
    const schedule = typeof requestAnimationFrame === 'function'
      ? requestAnimationFrame : (fn) => setTimeout(fn, 0);
    schedule(() => { drawMatrix(); });
    // Re-bind to the freshly mounted wrap each render (see renderScatterMain).
    if (typeof ResizeObserver === 'function') {
      if (mresizeObs) { try { mresizeObs.disconnect(); } catch { /* ignore */ } }
      mresizeObs = new ResizeObserver(() => { drawMatrix(); });
      try { mresizeObs.observe(wrap); } catch { /* ignore */ }
    }
    return main;
  }

  // ── render: sidebar (scatter mode) ──
  function buildSidebar() {
    const side = el('div', { className: 'embspace-sidebar' });
    if (state.phase !== 'ready') return side;

    // reducer (降维) switch: PCA (always) vs UMAP (on-demand install).
    const red = el('div', { className: 'embspace-reducer' });
    red.append(el('div', { className: 'embspace-reducer-head' },
      i18n('memory_trace.embedding.reducer.head')));
    const chips = el('div', { className: 'embspace-reducer-chips' });
    const mk = (val, key) => el('button', {
      className: 'btn embspace-reducer-chip' + (state.reducer === val ? ' active' : ''),
      disabled: state.umapInstalling,
      onClick: () => onReducer(val),
    }, i18n(key));
    chips.append(mk('pca', 'memory_trace.embedding.reducer.pca'),
      mk('umap', 'memory_trace.embedding.reducer.umap'));
    red.append(chips);
    if (state.umapInstalling) {
      red.append(el('p', { className: 'hint' },
        i18n('memory_trace.embedding.reducer.installing')));
    } else if (state.umapMsg) {
      red.append(el('pre', { className: 'embspace-reducer-err' }, state.umapMsg));
    } else if (state.reducer === 'umap' && state.umapAvailable
               && state.reducerUsed === 'pca') {
      red.append(el('p', { className: 'hint' },
        i18n('memory_trace.embedding.reducer.fallback_small')));
    } else if (state.umapAvailable) {
      red.append(el('p', { className: 'hint' },
        i18n('memory_trace.embedding.reducer.ready')));
    } else {
      red.append(el('p', { className: 'hint' },
        i18n('memory_trace.embedding.reducer.umap_hint')));
    }
    side.append(red);

    side.append(buildLegend(() => { drawScatter(); }));

    // ⑦ auto-cluster toggle + cluster list (P28.5).
    const cluToggle = el('div', { className: 'embspace-cluster-toggle' });
    const cluLabel = el('label', { className: 'embspace-legend-item' });
    cluLabel.append(
      el('input', {
        type: 'checkbox',
        checked: state.clusterOn,
        onChange: (ev) => onToggleCluster(!!ev.target.checked),
      }),
      el('span', {}, i18n('memory_trace.embedding.cluster.toggle')));
    cluToggle.append(cluLabel);
    side.append(cluToggle);
    if (state.clusterOn) side.append(buildClusterSection());

    // selection detail (filled by updateSidebar).
    const detail = el('div', { className: 'embspace-detail' });
    side.append(detail);
    return side;
  }

  // ⑦ cluster summary: algo/noise line, [用 LLM 概括] button, per-cluster list.
  function buildClusterSection() {
    const box = el('div', { className: 'embspace-cluster' });
    box.append(el('div', { className: 'embspace-reducer-head' },
      i18n('memory_trace.embedding.cluster.head')));
    if (state.clustersLoading) {
      box.append(el('p', { className: 'hint' },
        i18n('memory_trace.embedding.cluster.loading')));
      return box;
    }
    const c = state.clusters;
    if (!c || !c.n_clusters) {
      box.append(el('p', { className: 'hint' },
        i18n('memory_trace.embedding.cluster.empty')));
      return box;
    }
    box.append(el('p', { className: 'hint' },
      i18n('memory_trace.embedding.cluster.summary_fmt', c.n_clusters, c.noise_count)));
    box.append(el('p', { className: 'hint' },
      i18n('memory_trace.embedding.cluster.proj_note')));
    if (c.algo === 'cosine_cc') {
      box.append(el('p', { className: 'hint warn' },
        i18n('memory_trace.embedding.cluster.cc_note')));
    }
    box.append(el('button', {
      className: 'btn embspace-cluster-llm-btn',
      disabled: state.clusterLabeling,
      onClick: () => labelClustersLLM(),
    }, i18n(state.clusterLabeling
      ? 'memory_trace.embedding.cluster.labeling'
      : 'memory_trace.embedding.cluster.llm_btn')));
    if (state.clusterLLMTried && state.clusterLLMMethod !== 'llm') {
      box.append(el('p', { className: 'hint warn' },
        i18n('memory_trace.embedding.cluster.llm_fallback')));
      // Show the concrete backend reason(s) (e.g. "请先在 Settings → Models →
      // memory 填好 base_url 与 model。") so the tester knows which API to set.
      for (const w of (state.clusterLLMWarnings || [])) {
        box.append(el('p', { className: 'embspace-cluster-llm-reason hint warn' }, w));
      }
    }
    const ul = el('ul', { className: 'embspace-cluster-list' });
    for (const cl of c.clusters) {
      const label = (state.clusterLabels && state.clusterLabels[String(cl.cluster)])
        || cl.label;
      ul.append(el('li', { className: 'embspace-cluster-item' },
        el('span', {
          className: 'embspace-swatch',
          style: { background: clusterColor(cl.cluster) },
        }),
        el('span', { className: 'embspace-cluster-label' }, label),
        el('span', { className: 'embspace-cluster-size' }, String(cl.size))));
    }
    box.append(ul);
    return box;
  }

  // Reusable legend + type filter. ``onFilter`` runs after a checkbox toggles
  // so each mode can react (scatter/dup redraw; matrix re-fetches its subset).
  function buildLegend(onFilter) {
    const legend = el('div', { className: 'embspace-legend' });
    legend.append(el('div', { className: 'embspace-legend-head' },
      i18n('memory_trace.embedding.legend')));
    for (const t of TYPES) {
      const row = el('label', { className: 'embspace-legend-item' });
      const cb = el('input', {
        type: 'checkbox',
        checked: state.typeFilter[t] !== false,
        onChange: (ev) => {
          state.typeFilter[t] = !!ev.target.checked;
          if (typeof onFilter === 'function') onFilter();
        },
      });
      row.append(cb,
        el('span', { className: 'embspace-swatch', style: { background: TYPE_COLORS[t] } }),
        el('span', {}, i18n('memory_trace.embedding.type.' + t)));
      legend.append(row);
    }
    return legend;
  }

  // ── ④ duplicates sidebar: threshold slider + pair list ──
  function buildDuplicatesSidebar() {
    const side = el('div', { className: 'embspace-sidebar' });
    if (state.phase !== 'ready') return side;

    const ctl = el('div', { className: 'embspace-dup-ctl' });
    ctl.append(el('div', { className: 'embspace-reducer-head' },
      i18n('memory_trace.embedding.dup.threshold')));
    const slider = el('input', {
      type: 'range', min: '0.80', max: '0.99', step: '0.01',
      value: String(state.threshold),
      className: 'embspace-dup-slider',
      onInput: (ev) => {
        state.threshold = parseFloat(ev.target.value);
        const lab = host.querySelector('.embspace-dup-thr-val');
        if (lab) lab.textContent = state.threshold.toFixed(2);
      },
      onChange: () => { loadDuplicates(); },
    });
    const row = el('div', { className: 'embspace-dup-slider-row' });
    row.append(slider, el('span', { className: 'embspace-dup-thr-val' },
      Number(state.threshold).toFixed(2)));
    ctl.append(row);
    side.append(ctl);

    side.append(buildLegend(() => { drawScatter(); }));

    const listBox = el('div', { className: 'embspace-detail' });
    const d = state.duplicates;
    if (state.duplicatesLoading) {
      listBox.append(el('p', { className: 'hint' }, i18n('memory_trace.embedding.loading')));
    } else if (!d || !d.count) {
      listBox.append(el('p', { className: 'hint' }, i18n('memory_trace.embedding.dup.empty')));
    } else {
      listBox.append(el('div', { className: 'embspace-nbr-head' },
        i18n('memory_trace.embedding.dup.count_fmt', d.count)));
      if (d.capped) {
        listBox.append(el('p', { className: 'hint warn' },
          i18n('memory_trace.embedding.dup.capped_fmt', d.pairs.length)));
      }
      const ul = el('ul', { className: 'embspace-nbr-list' });
      for (const pr of visiblePairs()) {
        const isSel = state.dupSelected
          && state.dupSelected.a === pr.a && state.dupSelected.b === pr.b;
        ul.append(el('li', {
          className: 'embspace-dup-item' + (isSel ? ' active' : ''),
          onClick: () => { state.dupSelected = { a: pr.a, b: pr.b }; renderAll(); },
        },
          el('span', { className: 'embspace-nbr-score' }, String(pr.score)),
          el('div', { className: 'embspace-dup-pair' },
            el('span', { className: 'embspace-dup-end' },
              el('span', { className: 'embspace-swatch', style: { background: TYPE_COLORS[pr.a_type] } }),
              el('span', { className: 'embspace-dup-label' }, pr.a_label || pr.a)),
            el('span', { className: 'embspace-dup-end' },
              el('span', { className: 'embspace-swatch', style: { background: TYPE_COLORS[pr.b_type] } }),
              el('span', { className: 'embspace-dup-label' }, pr.b_label || pr.b)))));
      }
      listBox.append(ul);
    }
    side.append(listBox);
    return side;
  }

  // ── ⑤ matrix sidebar: subset info + color scale + legend ──
  function buildMatrixSidebar() {
    const side = el('div', { className: 'embspace-sidebar' });
    if (state.phase !== 'ready') return side;

    side.append(buildLegend(() => { loadMatrix(); }));

    const info = el('div', { className: 'embspace-detail' });
    const m = state.matrix;
    if (m && m.n) {
      info.append(el('div', { className: 'embspace-nbr-head' },
        i18n('memory_trace.embedding.matrix.subset_fmt', m.n)));
      if (m.truncated) {
        info.append(el('p', { className: 'hint warn' },
          i18n('memory_trace.embedding.matrix.truncated_fmt', m.requested, m.n)));
      }
      // color scale legend
      const scale = el('div', { className: 'embspace-matrix-scale' });
      scale.append(el('span', { className: 'embspace-matrix-scale-lab' },
        i18n('memory_trace.embedding.matrix.scale_low')));
      scale.append(el('span', { className: 'embspace-matrix-scale-bar' }));
      scale.append(el('span', { className: 'embspace-matrix-scale-lab' },
        i18n('memory_trace.embedding.matrix.scale_high')));
      info.append(scale);
      info.append(el('p', { className: 'hint' }, i18n('memory_trace.embedding.matrix.hint')));
    } else {
      info.append(el('p', { className: 'hint' }, i18n('memory_trace.embedding.matrix.empty_body')));
    }
    side.append(info);
    return side;
  }

  function updateSidebar() {
    const detail = host.querySelector('.embspace-detail');
    if (!detail) return;
    detail.innerHTML = '';
    if (!state.selectedId) {
      detail.append(el('p', { className: 'hint' },
        i18n('memory_trace.embedding.select_hint')));
      return;
    }
    const p = ((state.data && state.data.points) || [])
      .find((q) => q.id === state.selectedId);
    if (p) {
      detail.append(el('div', { className: 'embspace-detail-head' },
        el('span', { className: 'embspace-detail-type', style: { color: TYPE_COLORS[p.type] } },
          i18n('memory_trace.embedding.type.' + p.type)),
        el('span', { className: 'embspace-detail-id' }, p.id)));
      detail.append(el('div', { className: 'embspace-detail-text' }, p.label || ''));
      detail.append(el('button', {
        className: 'btn embspace-jump-btn',
        onClick: () => goToLineage(p.id),
      }, i18n('memory_trace.embedding.jump_to_lineage')));
    }
    // neighbors.
    detail.append(el('div', { className: 'embspace-nbr-head' },
      i18n('memory_trace.embedding.neighbors')));
    if (state.neighborsLoading) {
      detail.append(el('p', { className: 'hint' }, i18n('memory_trace.embedding.loading')));
    } else if (state.neighbors && state.neighbors.found) {
      const ul = el('ul', { className: 'embspace-nbr-list' });
      for (const n of state.neighbors.neighbors) {
        ul.append(el('li', {
          className: 'embspace-nbr-item',
          onClick: () => selectPoint(n.id),
        },
          el('span', { className: 'embspace-nbr-score' }, String(n.score)),
          el('span', { className: 'embspace-swatch', style: { background: TYPE_COLORS[n.type] } }),
          el('span', { className: 'embspace-nbr-label' }, n.label || n.id)));
      }
      detail.append(ul);
    } else {
      detail.append(el('p', { className: 'hint' },
        i18n('memory_trace.embedding.neighbors_none')));
    }
  }

  // ── full render ──
  function renderMainArea() {
    if (state.phase === 'loading') {
      return el('div', { className: 'embspace-main' },
        el('div', { className: 'empty-state' }, i18n('memory_trace.embedding.loading')));
    }
    if (state.phase === 'no_session') {
      return el('div', { className: 'embspace-main' }, emptyState('memory_trace.no_session'));
    }
    if (state.phase === 'no_character') {
      return el('div', { className: 'embspace-main' }, emptyState('memory_trace.no_character'));
    }
    if (state.phase === 'error') {
      return el('div', { className: 'embspace-main' },
        el('div', { className: 'empty-state' },
          `${i18n('memory_trace.load_failed')}: ${state.errorMsg}`));
    }
    if (state.mode === 'bridges') return renderBridgesMain();
    if (state.mode === 'duplicates') return renderDuplicatesMain();
    if (state.mode === 'matrix') return renderMatrixMain();
    return renderScatterMain();
  }

  function emptyState(prefix) {
    return el('div', { className: 'empty-state' },
      el('h3', {}, i18n(`${prefix}.heading`)),
      el('p', {}, i18n(`${prefix}.body`)));
  }

  function renderAll() {
    canvas = null; tooltipEl = null; mcanvas = null; mtooltipEl = null;
    host.innerHTML = '';
    host.append(renderToolbar());
    host.append(el('p', { className: 'embspace-intro hint' },
      i18n('memory_trace.embedding.intro')));
    host.append(renderCoverage());
    const body = el('div', { className: 'embspace-body' });
    body.append(renderMainArea());
    if (state.phase === 'ready') {
      if (state.mode === 'scatter') {
        body.append(buildSidebar());
        updateSidebar();  // populate the (just-built) selection detail.
      } else if (state.mode === 'duplicates') {
        body.append(buildDuplicatesSidebar());
      } else if (state.mode === 'matrix') {
        body.append(buildMatrixSidebar());
      }
    }
    host.append(body);
  }

  // Test hook (jsdom smoke drives selection without real canvas mouse events).
  host.__embspace = {
    selectPoint,
    setMode: (m) => {
      state.mode = m;
      if (m === 'bridges' && !state.bridges) loadBridges();
      else if (m === 'duplicates' && !state.duplicates) loadDuplicates();
      else if (m === 'matrix' && !state.matrix) loadMatrix();
      else renderAll();
    },
    setReducer: onReducer,
    setThreshold: (v) => { state.threshold = v; loadDuplicates(); },
    toggleCluster: onToggleCluster,
    labelClusters: labelClustersLLM,
    get state() { return state; },
  };

  // ── subscriptions ──
  const offSession = on('session:change', () => {
    state.selectedId = null; state.neighbors = null; state.bridges = null;
    state.duplicates = null; state.matrix = null; state.dupSelected = null;
    state.clusters = null; state.clusterLabels = {}; state.clusterLLMTried = false;
    state.clusterLLMMethod = 'medoid'; state.clusterLLMWarnings = [];
    state.view = null;
    reload();
  });
  const offActive = on('active_workspace:change', (id) => {
    if (id === 'memory_trace') reload();
  });

  renderAll();
  reload();

  return () => {
    try { offSession(); } catch { /* ignore */ }
    try { offActive(); } catch { /* ignore */ }
    if (resizeObs) { try { resizeObs.disconnect(); } catch { /* ignore */ } resizeObs = null; }
    if (mresizeObs) { try { mresizeObs.disconnect(); } catch { /* ignore */ } mresizeObs = null; }
  };
}
