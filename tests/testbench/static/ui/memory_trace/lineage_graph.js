/**
 * memory_trace/lineage_graph.js — pure-SVG lineage node-pipeline renderer.
 *
 * Lays the lineage snapshot out as a left-to-right lane diagram
 * (conversation -> recent memo -> facts -> reflections -> persona) and draws
 * nodes as cards + edges as bezier curves. Solid edges = persisted/captured
 * true causality; dashed = heuristic (Tier C). No external chart library:
 * hand-built SVG via `createElementNS`, mirroring `evaluation/page_aggregate.js`.
 *
 * Layout philosophy (user feedback 2026-06-29 "全挤在一起, 想要 Blender 那种稀疏"):
 *   The lineage *story* is the connected sub-graph. A real character can have
 *   hundreds of facts / messages that participate in NO causal edge — packing
 *   them all into one tall column is the wall the user saw. So:
 *
 *   1. **Connected flow** (nodes with >=1 edge) is the main, sparse diagram:
 *      laid out in lanes (x by type) with a generous row pitch, and each node's
 *      Y pulled toward the median Y of its neighbours over a few left<->right
 *      sweeps (barycenter / Sugiyama coordinate assignment). Connected memories
 *      line up with their sources -> few crossings, lots of breathing room.
 *   2. **Isolated nodes** (no edge at all) are parked in a compact wrapping grid
 *      below a divider, and are *hidden by default* (toolbar toggle reveals
 *      them). So the default view is just the clean lineage; running Tier C
 *      attribution pulls the relevant conversations INTO the flow automatically.
 *   3. **Zoom + pan**: content lives in `<g class="mtrace-viewport">`; wheel
 *      zooms toward the cursor, drag pans (pointer-captured, no window leaks).
 *      `el.__mtrace = {zoomBy, reset, fit, getView}` drives toolbar buttons and
 *      every change is reported via `opts.onViewChange` for persistence.
 *
 * Stateless render: the workspace owns {snapshot, mode, focusId, view,
 * showIsolated} and calls `renderLineageGraph` on each change (B1).
 */

import { i18n } from '../../core/i18n.js';

const SVG_NS = 'http://www.w3.org/2000/svg';

const NODE_W = 188;
const NODE_H = 46;
const LANE_GAP_X = 264;
const ROW_PITCH = 82;        // connected-flow vertical pitch (sparse)
const PAD = 32;
const LANE_LABEL_H = 28;
const ZONE_GAP = 96;         // gap between the flow and the parked-grid zone
const GRID_GAP_X = 18;
const GRID_GAP_Y = 16;

const MIN_K = 0.1;
const MAX_K = 4;
const FIT_MARGIN = 24;

// Heuristic (dashed Tier-C) edges in the overview. The backend already HARD
// caps batch attribution (推测全部源头) at _MAX_ALL_EDGES = 400, so a normal
// "attribute all" returns at most ~400 dashed edges — and the user expects to
// actually SEE that result (the conversation/summary source nodes appear and
// connect). This frontend cap is therefore a *safety valve* set comfortably
// above the backend cap: only a pathological accumulation (e.g. many repeated
// single-node attributions) past it gets hidden (with a hint to focus a node).
// Perf at the ~400 scale is handled by dropping arrowhead markers + non-hit-
// testing edge layer, not by hiding the edges.
const HEURISTIC_OVERVIEW_CAP = 600;

const LANE_ORDER = [
  { lane: 0, type: 'message', key: 'memory_trace.lanes.message' },
  { lane: 1, type: 'recent_memo', key: 'memory_trace.lanes.recent_memo' },
  { lane: 2, type: 'fact', key: 'memory_trace.lanes.fact' },
  { lane: 3, type: 'reflection', key: 'memory_trace.lanes.reflection' },
  { lane: 4, type: 'persona_entry', key: 'memory_trace.lanes.persona_entry' },
];

function svg(tag, attrs = {}, ...children) {
  const node = document.createElementNS(SVG_NS, tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (v == null) continue;
    node.setAttribute(k, String(v));
  }
  for (const c of children.flat()) {
    if (c == null || c === false) continue;
    node.append(c instanceof Node ? c : document.createTextNode(String(c)));
  }
  return node;
}

function clamp(v, lo, hi) { return Math.max(lo, Math.min(hi, v)); }

function laneOfNode(n) { return Number.isInteger(n.lane) ? n.lane : 0; }

/**
 * Place a lane's nodes (already sorted by desired Y) at integer slots that
 * respect a minimum row pitch while staying as close as possible to each
 * node's desired Y — *centered*, not pressed downward.
 *
 * Why this matters: a reflection fed by N facts wants the median Y of those
 * facts, and each of those facts wants the reflection's Y. A naive "press
 * down" packer spreads the equal-desire facts *below* the anchor, leaving the
 * target node stuck at the TOP of the column (the bug the user reported). The
 * optimal centered solution minimises Σ(y_i - desired_i)² subject to
 * y_{i+1} ≥ y_i + pitch. Substituting z_i = y_i − i·pitch turns it into an
 * isotonic regression of t_i = desired_i − i·pitch, solved exactly by the
 * Pool-Adjacent-Violators algorithm (PAVA). Result: the target sits in the
 * MIDDLE of its sources' span.
 */
function packCenteredLane(sorted, desired, y, top, pitch) {
  const n = sorted.length;
  if (!n) return;
  const blocks = [];  // {sum, w, val} — val = mean of t over the merged run
  for (let i = 0; i < n; i += 1) {
    const t = desired.get(sorted[i]) - i * pitch;
    const cur = { sum: t, w: 1, val: t };
    while (blocks.length && blocks[blocks.length - 1].val > cur.val) {
      const prev = blocks.pop();
      cur.sum += prev.sum;
      cur.w += prev.w;
      cur.val = cur.sum / cur.w;
    }
    blocks.push(cur);
  }
  const yy = new Array(n);
  let idx = 0;
  for (const b of blocks) {
    for (let k = 0; k < b.w; k += 1) {
      yy[idx] = b.val + idx * pitch;
      idx += 1;
    }
  }
  let minY = Infinity;
  for (const val of yy) minY = Math.min(minY, val);
  const shift = minY < top ? top - minY : 0;
  for (let i = 0; i < n; i += 1) y.set(sorted[i], yy[i] + shift);
}

/**
 * Degree map (undirected) over edges whose endpoints both exist in the snapshot.
 */
function degreeMap(snapshot) {
  const present = new Set(snapshot.nodes.map((n) => n.id));
  const deg = new Map();
  for (const e of snapshot.edges) {
    if (!present.has(e.source) || !present.has(e.target)) continue;
    deg.set(e.source, (deg.get(e.source) || 0) + 1);
    deg.set(e.target, (deg.get(e.target) || 0) + 1);
  }
  return deg;
}

/**
 * Count isolated (degree-0) nodes — used by the toolbar toggle label.
 */
export function countIsolated(snapshot) {
  if (!snapshot || !snapshot.nodes) return 0;
  const deg = degreeMap(snapshot);
  return snapshot.nodes.reduce((acc, n) => acc + ((deg.get(n.id) || 0) ? 0 : 1), 0);
}

/**
 * Compute {x,y} per node id.
 *
 * Connected nodes => lane columns + barycenter coordinate assignment (sparse,
 * crossing-reduced). Isolated nodes => compact wrapping grid below a divider,
 * only when `opts.showIsolated`.
 *
 * Returns { positions, width, height, flowBottom, gridTop, isolatedCount }.
 * `positions[id]` may carry `parked:true` for grid nodes.
 */
export function buildLayout(snapshot, opts = {}) {
  const showIsolated = !!opts.showIsolated;
  // Focus mode hands us a `subset` (the focused node's sub-tree). We then lay out
  // ONLY those nodes — a tight, minimal arrangement that fits the smallest
  // viewport — and skip the isolated parked grid entirely (every subset node is
  // meant to be shown). Overview (no subset) lays out the whole snapshot.
  const subset = opts.subset instanceof Set && opts.subset.size ? opts.subset : null;
  // In focus mode the forest is rooted at the FOCUSED node (see below) so it
  // ends up vertically centered on its whole sub-tree.
  const rootId = subset && opts.rootId && subset.has(opts.rootId) ? opts.rootId : null;
  const work = subset
    ? {
      nodes: snapshot.nodes.filter((n) => subset.has(n.id)),
      edges: snapshot.edges.filter((e) => subset.has(e.source) && subset.has(e.target)),
    }
    : snapshot;
  const positions = {};
  const deg = degreeMap(work);
  const laneOf = new Map();
  for (const n of work.nodes) laneOf.set(n.id, laneOfNode(n));

  // In subset/focus mode every node is part of the story → all "connected"
  // (a lone focus node with no edges still gets placed, never parked).
  const connected = subset ? work.nodes : work.nodes.filter((n) => (deg.get(n.id) || 0) > 0);
  const isolated = subset ? [] : work.nodes.filter((n) => (deg.get(n.id) || 0) === 0);

  // ── connected flow: forest layout ──
  //
  // Lineage edges always point upstream(source) -> downstream(target) with a
  // strictly increasing lane (fact -> reflection -> persona; message -> fact).
  // That makes the connected sub-graph a DAG (a forest in practice). We lay it
  // out as a tree rooted on the DOWNSTREAM side so that "a node that feeds a
  // downstream root sits near that root, together with its whole sub-tree"
  // (user feedback): a single persona fed by 13 reflections pulls those 13
  // reflections (and their facts) into one contiguous cluster around it, while
  // reflections with no downstream stay in their own clusters.
  //
  // children(id) = upstream sources that feed `id`; a node's Y is the mean of
  // its children's Y (parent centered on its sub-tree), leaves get sequential
  // slots. Roots = connected nodes with no downstream edge.
  const TOP = PAD + LANE_LABEL_H;
  const children = new Map();
  const outDeg = new Map();
  for (const e of work.edges) {
    if ((deg.get(e.source) || 0) > 0 && (deg.get(e.target) || 0) > 0) {
      if (!children.has(e.target)) children.set(e.target, []);
      children.get(e.target).push(e.source);
      outDeg.set(e.source, (outDeg.get(e.source) || 0) + 1);
    }
  }
  const origIdx = new Map();
  work.nodes.forEach((n, i) => origIdx.set(n.id, i));
  const byOrig = (a, b) => origIdx.get(a) - origIdx.get(b);

  const yMap = new Map();
  const visited = new Set();
  let slot = 0;
  const leafSlot = (id) => {
    const yv = TOP + slot * ROW_PITCH;
    slot += 1;
    yMap.set(id, yv);
    return yv;
  };

  if (rootId) {
    // ── FOCUS: undirected tree rooted at the focused node ──
    //
    // The sub-tree is traversed as an UNDIRECTED tree from the focused node, so
    // that node becomes the root and its Y = the mean of all its branches —
    // i.e. it sits in the vertical CENTER of the whole focused sub-tree (user
    // request). A leaf gets a sequential slot; an internal node is centered on
    // its children. Works whether the focus is upstream (e.g. a recent memo
    // feeding many facts) or in the middle (ancestors on one side, descendants
    // on the other).
    const adj = new Map();
    for (const e of work.edges) {
      if (!adj.has(e.source)) adj.set(e.source, []);
      if (!adj.has(e.target)) adj.set(e.target, []);
      adj.get(e.source).push(e.target);
      adj.get(e.target).push(e.source);
    }
    const placeU = (id, parent) => {
      visited.add(id);
      const kids = (adj.get(id) || [])
        .filter((k) => k !== parent && !visited.has(k))
        .sort(byOrig);
      let sum = 0;
      let cnt = 0;
      for (const k of kids) {
        if (visited.has(k)) continue;  // claimed by an earlier sibling's subtree
        sum += placeU(k, id);
        cnt += 1;
      }
      if (!cnt) return leafSlot(id);
      const yv = sum / cnt;
      yMap.set(id, yv);
      return yv;
    };
    placeU(rootId, null);
  } else {
    // ── OVERVIEW: forest rooted on the DOWNSTREAM side ──
    //
    // children(id) = upstream sources that feed `id`; a node's Y is the mean of
    // its children's Y (parent centered on its sub-tree), leaves get sequential
    // slots. Roots = connected nodes with no downstream edge.
    const placeSubtree = (id) => {
      if (visited.has(id)) return yMap.get(id);
      visited.add(id);
      const kids = (children.get(id) || []).slice().sort(byOrig);
      if (!kids.length) return leafSlot(id);
      let sum = 0;
      for (const c of kids) sum += placeSubtree(c);
      const yv = sum / kids.length;
      yMap.set(id, yv);
      return yv;
    };
    const roots = connected
      .filter((n) => !(outDeg.get(n.id) > 0))
      .map((n) => n.id)
      .sort(byOrig);
    for (const r of roots) placeSubtree(r);
  }
  for (const n of connected) {
    if (!visited.has(n.id)) {  // defensive: cycle/odd DAG -> append
      yMap.set(n.id, TOP + slot * ROW_PITCH);
      slot += 1;
    }
  }

  const laneNodes = new Map();
  for (const n of connected) {
    const l = laneOf.get(n.id);
    if (!laneNodes.has(l)) laneNodes.set(l, []);
    laneNodes.get(l).push(n.id);
  }
  if (rootId) {
    // Focus: the undirected tree already put the focused root at the vertical
    // center of its sub-tree; one barycenter-preserving isotonic pack per lane
    // (min row pitch) is enough for the small focused set.
    for (const [, ids] of laneNodes) {
      const desired = new Map(ids.map((id) => [id, yMap.get(id)]));
      const sorted = ids.slice().sort((a, b) => desired.get(a) - desired.get(b));
      packCenteredLane(sorted, desired, yMap, TOP, ROW_PITCH);
    }
  } else {
    // Overview: barycenter sweep from leaves to roots. Lineage edges always run
    // upstream(lower lane) -> downstream(higher lane), so every node's children
    // live in strictly lower lanes. Processing lanes in ASCENDING order lets us
    // pack the leaf lanes first, then RE-CENTER each downstream node on its
    // already-finalized children before packing its own lane. The previous code
    // packed every lane against *pre-pack* child positions, so a dense
    // reflection lane drifted far from the facts it summarizes — reflections
    // ended up sitting entirely above their own fact block (user report).
    const lanesAsc = [...laneNodes.keys()].sort((a, b) => a - b);
    for (const l of lanesAsc) {
      const ids = laneNodes.get(l);
      const desired = new Map();
      for (const id of ids) {
        const kids = children.get(id);
        if (kids && kids.length) {
          let s = 0;
          let c = 0;
          for (const k of kids) {
            const ky = yMap.get(k);
            if (ky != null) { s += ky; c += 1; }
          }
          desired.set(id, c ? s / c : yMap.get(id));
        } else {
          desired.set(id, yMap.get(id));  // leaf keeps its forest slot
        }
      }
      const sorted = ids.slice().sort((a, b) => desired.get(a) - desired.get(b));
      packCenteredLane(sorted, desired, yMap, TOP, ROW_PITCH);
    }
  }

  let flowBottom = TOP;
  for (const n of connected) {
    const yy = yMap.get(n.id);
    positions[n.id] = { x: PAD + laneOf.get(n.id) * LANE_GAP_X, y: yy };
    flowBottom = Math.max(flowBottom, yy + NODE_H);
  }

  let maxLane = LANE_ORDER.length - 1;
  for (const n of work.nodes) maxLane = Math.max(maxLane, laneOf.get(n.id));
  const width = PAD * 2 + maxLane * LANE_GAP_X + NODE_W;

  // ── isolated parked grid (optional) ──
  const gridTop = flowBottom + ZONE_GAP;
  let bottom = flowBottom;
  if (showIsolated && isolated.length) {
    const usable = width - PAD * 2;
    const cols = Math.max(1, Math.floor((usable + GRID_GAP_X) / (NODE_W + GRID_GAP_X)));
    const iso = isolated.slice().sort((a, b) => laneOf.get(a.id) - laneOf.get(b.id));
    iso.forEach((n, i) => {
      const col = i % cols;
      const row = Math.floor(i / cols);
      positions[n.id] = {
        x: PAD + col * (NODE_W + GRID_GAP_X),
        y: gridTop + row * (NODE_H + GRID_GAP_Y),
        parked: true,
      };
    });
    const rows = Math.ceil(iso.length / cols);
    bottom = gridTop + rows * (NODE_H + GRID_GAP_Y);
  }

  const height = bottom + PAD;
  return {
    positions, width, height,
    flowBottom, gridTop,
    isolatedCount: isolated.length,
    showIsolated,
  };
}

/**
 * Transitive related-set for focus mode: the focus node + all ancestors (follow
 * edges backwards) + all descendants (follow edges forwards).
 *
 * Follows BOTH structural (real causality) and heuristic (Tier-C guessed
 * conversation sources) edges, multi-hop — so focusing a reflection reveals the
 * whole chain reflection <- facts <- conversations, not just the immediate
 * facts. The focused sub-tree is rendered compactly and root-centered (see
 * buildLayout `rootId`), so the extra depth stays readable.
 */
export function computeRelated(snapshot, focusId) {
  if (!focusId) return null;
  const out = new Map();
  const inc = new Map();
  for (const e of snapshot.edges) {
    if (!out.has(e.source)) out.set(e.source, []);
    out.get(e.source).push(e.target);
    if (!inc.has(e.target)) inc.set(e.target, []);
    inc.get(e.target).push(e.source);
  }
  const related = new Set([focusId]);
  const walk = (startId, a) => {
    const stack = [startId];
    while (stack.length) {
      const cur = stack.pop();
      for (const nxt of a.get(cur) || []) {
        if (!related.has(nxt)) { related.add(nxt); stack.push(nxt); }
      }
    }
  };
  walk(focusId, out);
  walk(focusId, inc);
  return related;
}

function edgePath(s, t) {
  const x1 = s.x + NODE_W;
  const y1 = s.y + NODE_H / 2;
  const x2 = t.x;
  const y2 = t.y + NODE_H / 2;
  const dx = Math.max(40, Math.abs(x2 - x1) * 0.5);
  return `M ${x1} ${y1} C ${x1 + dx} ${y1}, ${x2 - dx} ${y2}, ${x2} ${y2}`;
}

function renderNode(n, p, { focusId, selectedId, related, onSelect }) {
  const dimmed = related && !related.has(n.id);
  const g = svg('g', {
    class: [
      'mtrace-node',
      `mtrace-node--${n.type}`,
      p.parked ? 'is-parked' : '',
      dimmed ? 'is-dimmed' : '',
      n.id === focusId ? 'is-focus' : '',
      n.id === selectedId ? 'is-selected' : '',
    ].filter(Boolean).join(' '),
    transform: `translate(${p.x} ${p.y})`,
    'data-node-id': n.id,
    'data-node-type': n.type,
    tabindex: '0',
    role: 'button',
  });
  g.addEventListener('click', () => onSelect(n.id));
  g.addEventListener('keydown', (ev) => {
    if (ev.key === 'Enter' || ev.key === ' ') { ev.preventDefault(); onSelect(n.id); }
  });
  g.append(svg('rect', {
    width: NODE_W, height: NODE_H, rx: 7, ry: 7, class: 'mtrace-node-box',
  }));
  g.append(svg('text', { x: 9, y: 18, class: 'mtrace-node-type' },
    i18n(`memory_trace.node_type.${n.type}`)));
  g.append(svg('text', {
    x: 9, y: 36, class: 'mtrace-node-label', 'clip-path': 'url(#mtrace-node-clip)',
  }, n.label || ''));
  if (n.status) {
    g.append(svg('text', {
      x: NODE_W - 9, y: 18, class: 'mtrace-node-status', 'text-anchor': 'end',
      'clip-path': 'url(#mtrace-node-clip)',
    }, n.status));
  }
  return g;
}

/**
 * Render the SVG graph element.
 *
 * @param {object} snapshot   {nodes, edges, meta}
 * @param {object} opts
 * @param {string|null} opts.focusId
 * @param {(id:string)=>void} opts.onSelect
 * @param {string|null} opts.selectedId
 * @param {boolean} opts.showIsolated
 * @param {{k,tx,ty}|null} opts.view
 * @param {(v)=>void} opts.onViewChange
 * @returns {SVGSVGElement}  carries `el.__mtrace` zoom API
 */
export function renderLineageGraph(snapshot, opts = {}) {
  const {
    focusId = null, onSelect = () => {}, selectedId = null,
    showIsolated = false, view = null, onViewChange = () => {},
    onBlankClick = () => {},
  } = opts;
  const related = computeRelated(snapshot, focusId);

  // Edge level-of-detail decision (perf + clarity). Heuristic (dashed Tier-C)
  // edges are "show on demand": in the overview we only draw them when there are
  // few; over the cap they are hidden (you reveal a node's sources by focusing
  // it). CRUCIAL: the same set must drive the LAYOUT — otherwise hundreds of
  // hidden message->fact edges still pull the facts apart (centering them on
  // invisible message nodes), which is the "facts spread out with huge gaps"
  // bug. So when heuristic edges are hidden, they are also excluded from layout
  // (their message nodes fall back to isolated/parked, exactly as drawn).
  const nodeIds = new Set(snapshot.nodes.map((n) => n.id));
  const heuristicCount = snapshot.edges.reduce((acc, e) => (
    e.confidence === 'heuristic' && nodeIds.has(e.source) && nodeIds.has(e.target)
  ) ? acc + 1 : acc, 0);
  const showHeuristicOverview = heuristicCount <= HEURISTIC_OVERVIEW_CAP;

  // Focus = compact relayout of just the focused sub-tree (auto-arranged to the
  // smallest footprint, rooted on the focused node); overview = the full
  // snapshot, minus hidden heuristic edges. Blank-click clears focus and the
  // next render restores the original overview layout.
  let layout;
  if (related) {
    layout = buildLayout(snapshot, { subset: related, rootId: focusId });
  } else if (showHeuristicOverview) {
    layout = buildLayout(snapshot, { showIsolated });
  } else {
    const structuralOnly = {
      nodes: snapshot.nodes,
      edges: snapshot.edges.filter((e) => e.confidence !== 'heuristic'),
    };
    layout = buildLayout(structuralOnly, { showIsolated });
  }

  const root = svg('svg', { class: 'mtrace-svg' });
  const defs = svg('defs', {},
    svg('marker', {
      id: 'mtrace-arrow', viewBox: '0 0 10 10', refX: '9', refY: '5',
      markerWidth: '7', markerHeight: '7', orient: 'auto-start-reverse',
    }, svg('path', { d: 'M 0 0 L 10 5 L 0 10 z', class: 'mtrace-arrowhead' })),
    svg('clipPath', { id: 'mtrace-node-clip' },
      svg('rect', { x: 4, y: 0, width: NODE_W - 8, height: NODE_H })),
  );
  root.append(defs);

  const viewport = svg('g', { class: 'mtrace-viewport' });
  root.append(viewport);

  // lane labels — only for lanes that actually hold a placed node (keeps the
  // compact focus view from showing stray labels over empty columns).
  const lanesPresent = new Set();
  for (const n of snapshot.nodes) {
    if (layout.positions[n.id]) lanesPresent.add(laneOfNode(n));
  }
  for (const lane of LANE_ORDER) {
    if (related && !lanesPresent.has(lane.lane)) continue;
    viewport.append(svg('text', {
      x: PAD + lane.lane * LANE_GAP_X,
      y: PAD,
      class: 'mtrace-lane-label',
    }, i18n(lane.key)));
  }

  // parked-zone divider + heading (only when isolated nodes are shown).
  if (showIsolated && layout.isolatedCount > 0) {
    const dy = layout.gridTop - ZONE_GAP / 2;
    viewport.append(svg('line', {
      x1: PAD, y1: dy, x2: layout.width - PAD, y2: dy, class: 'mtrace-zone-divider',
    }));
    viewport.append(svg('text', {
      x: PAD, y: dy - 8, class: 'mtrace-zone-label',
    }, i18n('memory_trace.isolated_zone_fmt', layout.isolatedCount)));
  }

  // edges first (under nodes); only edges whose both endpoints are placed.
  // Edge level-of-detail (perf + clarity, user report: hundreds of dashed
  // conversation->fact edges = hairball + unscrollable):
  //   * focus mode  -> draw ONLY edges fully inside the focused sub-tree.
  //   * overview     -> always draw structural (solid) edges; draw heuristic
  //                     (dashed) edges only when few (<= cap), else hide them
  //                     and report the hidden count (a hint nudges the user to
  //                     focus a node to see its sources).
  // Skipped edges are not rendered at all (not just dimmed) so the DOM stays
  // small and pan/zoom stays smooth. The hidden-heuristic count is derived from
  // the snapshot (not the draw loop) because hidden heuristic edges also have
  // their message endpoints dropped from the layout, so the loop would `continue`
  // on the unplaced endpoint before it could count them.
  let hiddenHeuristic;
  if (related) {
    hiddenHeuristic = snapshot.edges.reduce((acc, e) => (
      e.confidence === 'heuristic'
      && nodeIds.has(e.source) && nodeIds.has(e.target)
      && !(related.has(e.source) && related.has(e.target))
    ) ? acc + 1 : acc, 0);
  } else {
    hiddenHeuristic = showHeuristicOverview ? 0 : heuristicCount;
  }
  const edgeLayer = svg('g', { class: 'mtrace-edges' });
  for (const e of snapshot.edges) {
    const s = layout.positions[e.source];
    const t = layout.positions[e.target];
    if (!s || !t) continue;
    const isHeuristic = e.confidence === 'heuristic';
    if (related) {
      if (!(related.has(e.source) && related.has(e.target))) {
        continue;  // focus: subtree edges only
      }
    } else if (isHeuristic && !showHeuristicOverview) {
      continue;  // overview: too many dashed edges, hide for perf/clarity
    }
    const attrs = {
      d: edgePath(s, t),
      class: ['mtrace-edge', `mtrace-edge--${e.confidence || 'persisted'}`].join(' '),
      fill: 'none',
    };
    // Arrowheads only on the (few) structural edges — markers are the dominant
    // SVG perf cost at scale, and the left->right lane flow already implies the
    // heuristic edges' direction.
    if (!isHeuristic) attrs['marker-end'] = 'url(#mtrace-arrow)';
    edgeLayer.append(svg('path', attrs));
  }
  viewport.append(edgeLayer);

  // nodes.
  const nodeLayer = svg('g', { class: 'mtrace-nodes' });
  for (const n of snapshot.nodes) {
    const p = layout.positions[n.id];
    if (!p) continue;  // isolated + hidden
    nodeLayer.append(renderNode(n, p, { focusId, selectedId, related, onSelect }));
  }
  viewport.append(nodeLayer);

  // ── pan / zoom ──
  const v = {
    k: view && Number.isFinite(view.k) ? view.k : 1,
    tx: view && Number.isFinite(view.tx) ? view.tx : 0,
    ty: view && Number.isFinite(view.ty) ? view.ty : 0,
  };
  const apply = () => viewport.setAttribute(
    'transform', `translate(${v.tx} ${v.ty}) scale(${v.k})`);
  const commit = () => onViewChange({ k: v.k, tx: v.tx, ty: v.ty });
  apply();

  // smooth view transitions (used by auto-focus fit / fit-to-whole).
  let raf = null;
  function stopAnim() { if (raf) { cancelAnimationFrame(raf); raf = null; } }
  function animateTo(target, duration = 340) {
    stopAnim();
    if (typeof requestAnimationFrame !== 'function') {
      v.k = target.k; v.tx = target.tx; v.ty = target.ty; apply(); commit();
      return;
    }
    const s = { k: v.k, tx: v.tx, ty: v.ty };
    const t0 = (typeof performance !== 'undefined' ? performance.now() : Date.now());
    const ease = (p) => (p < 0.5 ? 2 * p * p : 1 - ((-2 * p + 2) ** 2) / 2);
    const step = (now) => {
      const p = Math.min(1, (now - t0) / duration);
      const e = ease(p);
      v.k = s.k + (target.k - s.k) * e;
      v.tx = s.tx + (target.tx - s.tx) * e;
      v.ty = s.ty + (target.ty - s.ty) * e;
      apply();
      if (p < 1) { raf = requestAnimationFrame(step); } else { raf = null; commit(); }
    };
    raf = requestAnimationFrame(step);
  }

  function zoomAround(px, py, factor) {
    stopAnim();
    const nk = clamp(v.k * factor, MIN_K, MAX_K);
    const ratio = nk / v.k;
    v.tx = px - ratio * (px - v.tx);
    v.ty = py - ratio * (py - v.ty);
    v.k = nk;
    apply();
    commit();
  }

  root.addEventListener('wheel', (ev) => {
    ev.preventDefault();
    const rect = root.getBoundingClientRect();
    zoomAround(ev.clientX - rect.left, ev.clientY - rect.top,
      ev.deltaY < 0 ? 1.12 : 1 / 1.12);
  }, { passive: false });

  let panning = false;
  let panMoved = false;
  let lastX = 0;
  let lastY = 0;
  root.addEventListener('pointerdown', (ev) => {
    if (ev.button !== 0) return;
    if (ev.target.closest && ev.target.closest('.mtrace-node')) return;
    stopAnim();
    panning = true;
    panMoved = false;
    lastX = ev.clientX;
    lastY = ev.clientY;
    try { root.setPointerCapture(ev.pointerId); } catch { /* ignore */ }
  });
  root.addEventListener('pointermove', (ev) => {
    if (!panning) return;
    const dx = ev.clientX - lastX;
    const dy = ev.clientY - lastY;
    if (!panMoved && Math.abs(dx) + Math.abs(dy) > 4) {
      panMoved = true;
      root.classList.add('is-panning');
    }
    if (panMoved) {
      v.tx += dx;
      v.ty += dy;
      lastX = ev.clientX;
      lastY = ev.clientY;
      apply();
    }
  });
  const endPan = (ev) => {
    if (!panning) return;
    const moved = panMoved;
    panning = false;
    panMoved = false;
    root.classList.remove('is-panning');
    try { root.releasePointerCapture(ev.pointerId); } catch { /* ignore */ }
    if (moved) commit();
    else onBlankClick();  // click on empty canvas (no drag) -> cancel focus
  };
  root.addEventListener('pointerup', endPan);
  root.addEventListener('pointercancel', endPan);

  // Compute the view (k,tx,ty) that centers a content-space box in the canvas.
  function viewForBox(minX, minY, maxX, maxY) {
    const rect = root.getBoundingClientRect();
    const cw = rect.width;
    const ch = rect.height;
    if (!cw || !ch) return null;
    const bw = Math.max(1, maxX - minX);
    const bh = Math.max(1, maxY - minY);
    const k = clamp(
      Math.min((cw - 2 * FIT_MARGIN) / bw, (ch - 2 * FIT_MARGIN) / bh),
      MIN_K, MAX_K,
    );
    return {
      k,
      tx: (cw - (minX + maxX) * k) / 2,
      ty: (ch - (minY + maxY) * k) / 2,
    };
  }

  function applyTarget(target, animate) {
    if (!target) return;
    if (animate) { animateTo(target); return; }
    stopAnim();
    v.k = target.k; v.tx = target.tx; v.ty = target.ty;
    apply();
    commit();
  }

  function fit(animate = false) {
    applyTarget(viewForBox(0, 0, layout.width, layout.height), animate);
  }

  // Fit to the current focus node's related sub-tree (ancestors + descendants).
  function fitRelated(animate = false) {
    if (!related || !related.size) { fit(animate); return; }
    let minX = Infinity; let minY = Infinity; let maxX = -Infinity; let maxY = -Infinity;
    for (const id of related) {
      const p = layout.positions[id];
      if (!p) continue;
      minX = Math.min(minX, p.x);
      minY = Math.min(minY, p.y);
      maxX = Math.max(maxX, p.x + NODE_W);
      maxY = Math.max(maxY, p.y + NODE_H);
    }
    if (minX === Infinity) { fit(animate); return; }
    applyTarget(viewForBox(minX, minY, maxX, maxY), animate);
  }

  root.__mtrace = {
    zoomBy(factor) {
      const rect = root.getBoundingClientRect();
      zoomAround(rect.width / 2, rect.height / 2, factor);
    },
    reset() { stopAnim(); v.k = 1; v.tx = 0; v.ty = 0; apply(); commit(); },
    fit,
    fitRelated,
    getView() { return { k: v.k, tx: v.tx, ty: v.ty }; },
    contentSize() { return { width: layout.width, height: layout.height }; },
    // perf/clarity LOD telemetry (workspace shows a hint when >0 are hidden).
    heuristicTotal: heuristicCount,
    hiddenHeuristic,
    focused: !!related,
  };

  return root;
}
