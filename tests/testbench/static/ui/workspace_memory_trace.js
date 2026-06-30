/**
 * workspace_memory_trace.js — 记忆系统分析 (Memory Analysis) workspace (P27).
 *
 * Top-level workspace is a two-col shell: a left sub-nav (子页菜单) + a right
 * pane. 记忆溯源 (memory lineage) is the first sub-page; future memory-analysis
 * sub-pages slot into PAGES below. Keeping the workspace id `memory_trace`
 * preserves active_workspace persistence and the existing reload-on-activate
 * wiring while the visible title becomes 记忆系统分析.
 *
 * 记忆溯源 sub-page — full-height, read-only node-pipeline view of the active
 * character's memory lineage. Single backend chokepoint: GET /api/memory/lineage
 * (P27.1). The frontend never re-derives graph structure from the raw memory
 * JSON — it renders the aggregator's {nodes, edges, meta} verbatim
 * (blueprint §3 #1).
 *
 * Lineage page state (owned per-page, drives full re-render — B1):
 *   - snapshot   last fetched {nodes, edges, meta} (or null)
 *   - mode       'overview' | 'focus'
 *   - focusId    node id whose ancestors/descendants are highlighted
 *   - selectedId node id shown in the detail rail
 *   - phase      'loading' | 'ready' | 'no_session' | 'no_character' | 'error'
 *
 * Refresh triggers: session switch, becoming the active workspace (so memory
 * ops run in Setup are picked up), and a manual Reload button. Memory commit
 * has no global frontend event, so re-entry + manual reload cover it.
 *
 * Teardown: each sub-page mount returns its teardown fn; the workspace runs it
 * before re-selecting a page and stores it on host.__offMemoryAnalysis so an
 * idempotent re-mount can detach prior subscriptions (L18 explicit teardown).
 */

import { api } from '../core/api.js';
import { i18n } from '../core/i18n.js';
import { store, on } from '../core/state.js';
import { toast } from '../core/toast.js';
import { el } from './_dom.js';
import { renderLineageGraph, countIsolated } from './memory_trace/lineage_graph.js';
import { renderDetailPanel } from './memory_trace/detail_panel.js';
import { mountEmbeddingSpacePage } from './memory_trace/embedding_space.js';
import { mountOverviewPage } from './memory_trace/overview.js';

// Sub-pages of 记忆系统分析. Each mount(container, ctx) returns a teardown.
// ctx.goTo(pageId, opts) lets a sub-page jump to a sibling (e.g. the 系统概况
// overview → 记忆溯源 / 向量空间 drill-down in P29, opts.focusNodeId / mode).
// 系统概况 (overview) is the default entry sub-page (blueprint P29 §4).
const PAGES = [
  { id: 'overview', navKey: 'memory_trace.nav.overview', mount: mountOverviewPage },
  { id: 'lineage', navKey: 'memory_trace.nav.lineage', mount: mountLineagePage },
  { id: 'embedding', navKey: 'memory_trace.nav.embedding_space', mount: mountEmbeddingSpacePage },
];
const LS_KEY = 'testbench:memory_analysis:active_subpage';

export function mountMemoryTraceWorkspace(host) {
  // Detach any prior sub-page subscriptions (idempotent re-mount).
  if (typeof host.__offMemoryAnalysis === 'function') {
    try { host.__offMemoryAnalysis(); } catch { /* ignore */ }
    host.__offMemoryAnalysis = null;
  }
  host.classList.add('two-col', 'memory-analysis');
  host.innerHTML = '';

  const nav = el('div', { className: 'subnav' });
  const pane = el('div', { className: 'memory-analysis-pane' });
  host.append(nav, pane);

  const stored = localStorage.getItem(LS_KEY);
  const initial = PAGES.some((p) => p.id === stored) ? stored : PAGES[0].id;

  const buttons = {};
  for (const page of PAGES) {
    const btn = el('button', {
      className: 'subnav-item',
      onClick: () => selectPage(page.id),
    }, i18n(page.navKey));
    buttons[page.id] = btn;
    nav.append(btn);
  }

  let pageTeardown = null;
  function selectPage(id, opts) {
    const page = PAGES.find((p) => p.id === id) || PAGES[0];
    for (const [bid, btn] of Object.entries(buttons)) {
      btn.classList.toggle('active', bid === page.id);
    }
    localStorage.setItem(LS_KEY, page.id);
    if (typeof pageTeardown === 'function') {
      try { pageTeardown(); } catch { /* ignore */ }
      pageTeardown = null;
    }
    pane.innerHTML = '';
    const subpage = el('div', { className: 'subpage active', 'data-subpage': page.id });
    pane.append(subpage);
    const ctx = {
      goTo: (pid, o) => selectPage(pid, o),
      opts: opts || {},
    };
    pageTeardown = page.mount(subpage, ctx) || null;
  }

  host.__offMemoryAnalysis = () => {
    if (typeof pageTeardown === 'function') {
      try { pageTeardown(); } catch { /* ignore */ }
      pageTeardown = null;
    }
  };

  selectPage(initial);
}

/**
 * 记忆溯源 sub-page. Mounts into `host` (the .subpage container) and returns a
 * teardown fn that detaches its store subscriptions.
 */
function mountLineagePage(host, ctx) {
  host.classList.add('memory-trace');
  host.innerHTML = '';

  const state = {
    snapshot: null,
    mode: 'overview',
    focusId: null,
    selectedId: null,
    // Cross-link from the 向量空间 sub-page: focus this node once loaded (P28).
    pendingFocusId: (ctx && ctx.opts && ctx.opts.focusNodeId) || null,
    phase: 'loading',
    errorMsg: '',
    attributing: false,
    attributionMsg: '',
    attributionFallback: false,  // last attribute fell back from LLM → text
    // Reveal the parked grid of isolated (no-edge) nodes. Default off so the
    // first view is just the clean, sparse lineage flow.
    showIsolated: false,
    // pan/zoom of the graph viewport; null = "auto-fit on first paint".
    view: null,
  };

  // Live handle to the currently mounted graph (for zoom-control buttons +
  // first-paint auto-fit). Reset every renderAll.
  let graphEl = null;

  // Monotonic token so a slow /api/memory/lineage response from a previous
  // session/character can't overwrite newer state. Bumped on every reload start
  // and on session change / teardown; a stale response (seq mismatch) is dropped.
  let reloadSeq = 0;

  // Run after the next paint so the freshly mounted SVG has real dimensions
  // (getBoundingClientRect) before we fit/animate. jsdom has no rAF.
  const scheduleFit = (fn) => {
    const r = typeof requestAnimationFrame === 'function'
      ? requestAnimationFrame : (f) => setTimeout(f, 0);
    r(fn);
  };

  async function reload() {
    const seq = ++reloadSeq;
    const session = store.session;
    if (!session) {
      state.phase = 'no_session';
      renderAll();
      return;
    }
    state.phase = 'loading';
    renderAll();
    const res = await api.get('/api/memory/lineage',
      { expectedStatuses: [404, 409] });
    if (seq !== reloadSeq) return;  // a newer reload superseded this one
    if (res.ok) {
      state.snapshot = res.data;
      state.phase = 'ready';
      // Drop a stale selection that no longer exists in the new snapshot.
      const ids = new Set((res.data.nodes || []).map((n) => n.id));
      if (state.selectedId && !ids.has(state.selectedId)) state.selectedId = null;
      if (state.focusId && !ids.has(state.focusId)) {
        state.focusId = null;
        state.mode = 'overview';
      }
      // Honor a cross-link focus request once the snapshot is in.
      if (state.pendingFocusId) {
        const wanted = state.pendingFocusId;
        state.pendingFocusId = null;
        if (ids.has(wanted)) {
          selectNode(wanted);
          return;
        }
      }
    } else if (res.status === 409 && res.error?.type === 'NoCharacterSelected') {
      state.phase = 'no_character';
    } else if (res.status === 404 && res.error?.type === 'NoActiveSession') {
      state.phase = 'no_session';
    } else {
      // Any other failure (incl. a bare 404 "Not Found" — e.g. a missing
      // backend route on a not-yet-restarted server) is a real error, NOT
      // "no session". Honest surfacing avoids the misleading "请新建会话".
      state.phase = 'error';
      state.errorMsg = res.error?.message || `HTTP ${res.status}`;
    }
    renderAll();
  }

  function selectNode(id) {
    if (id !== state.selectedId) {
      state.attributionMsg = '';
      state.attributionFallback = false;
    }
    state.selectedId = id;
    // Auto-focus: selecting a node highlights its whole sub-tree and smoothly
    // zooms the viewport to fit exactly that sub-tree.
    state.focusId = id;
    state.mode = 'focus';
    renderAll();
    scheduleFit(() => {
      if (graphEl && graphEl.__mtrace) graphEl.__mtrace.fitRelated(true);
    });
  }

  // Click on empty canvas -> cancel focus and smoothly zoom back to the
  // whole-graph overview.
  function onBlankClick() {
    if (state.mode !== 'focus' && !state.selectedId) return;
    state.focusId = null;
    state.mode = 'overview';
    state.selectedId = null;
    state.attributionMsg = '';
    state.attributionFallback = false;
    renderAll();
    scheduleFit(() => {
      if (graphEl && graphEl.__mtrace) graphEl.__mtrace.fit(true);
    });
  }

  function _mergeAttribution(result) {
    const snap = state.snapshot;
    if (!snap) return;
    const existingIds = new Set(snap.nodes.map((n) => n.id));
    for (const cand of result.candidates || []) {
      if (!existingIds.has(cand.id)) {
        snap.nodes.push(cand);
        existingIds.add(cand.id);
      }
    }
    // De-dup edges by (source,target,relation).
    const edgeKey = (e) => `${e.source}\u0000${e.target}\u0000${e.relation}`;
    const have = new Set(snap.edges.map(edgeKey));
    for (const e of result.edges || []) {
      if (!have.has(edgeKey(e))) {
        snap.edges.push(e);
        have.add(edgeKey(e));
      }
    }
  }

  async function attributeNode(id, useLlm) {
    if (state.attributing) return;
    state.attributing = true;
    state.attributionMsg = '';
    renderAll();
    const res = await api.post('/api/memory/lineage/attribute',
      { node_id: id, use_llm: !!useLlm },
      { expectedStatuses: [404, 409, 422] });
    state.attributing = false;
    if (res.ok) {
      const data = res.data || {};
      _mergeAttribution(data);
      const n = (data.edges || []).length;
      const fb = data.llm_fallback;
      if (fb) {
        // LLM was requested but degraded — surface it persistently (not just a
        // transient toast) with the reason, so the user knows the dashed edges
        // came from text similarity, not the LLM precision pass.
        state.attributionFallback = true;
        state.attributionMsg = i18n(
          'memory_trace.detail.attribute_fallback_fmt', n, fb.reason || '');
      } else {
        state.attributionFallback = false;
        state.attributionMsg = n > 0
          ? i18n('memory_trace.detail.attribute_done_fmt', n, data.method)
          : i18n('memory_trace.detail.attribute_none');
      }
      for (const w of data.warnings || []) toast.warn(w);
    } else {
      state.attributionMsg = '';
      state.attributionFallback = false;
      toast.err(i18n('memory_trace.detail.attribute_failed'),
        { message: res.error?.message || '' });
    }
    renderAll();
  }
  async function attributeAll() {
    if (state.attributing) return;
    state.attributing = true;
    renderAll();
    const res = await api.post('/api/memory/lineage/attribute_all', {},
      { expectedStatuses: [404, 409] });
    state.attributing = false;
    if (res.ok) {
      const data = res.data || {};
      _mergeAttribution(data);
      const n = (data.edges || []).length;
      if (n > 0) {
        toast.ok(i18n('memory_trace.attribute_all_done_fmt',
          n, data.attributed_nodes || 0, data.target_total || 0));
      } else {
        toast.warn(i18n('memory_trace.attribute_all_none'));
      }
      for (const w of data.warnings || []) toast.warn(w);
    } else {
      toast.err(i18n('memory_trace.detail.attribute_failed'),
        { message: res.error?.message || '' });
    }
    renderAll();
  }
  function renderToolbar() {
    const bar = el('div', { className: 'mtrace-toolbar' });
    bar.append(el('h2', { className: 'mtrace-title' }, i18n('memory_trace.title')));
    const right = el('div', { className: 'mtrace-toolbar-right' });
    // Focus is driven entirely by clicking nodes (auto-focus) / blank canvas
    // (cancel) — no mode chips. Only the data actions live in the toolbar.
    if (state.phase === 'ready') {
      right.append(el('button', {
        className: 'btn mtrace-attrall-btn',
        disabled: state.attributing,
        title: i18n('memory_trace.attribute_all_hint'),
        onClick: () => attributeAll(),
      }, state.attributing
        ? i18n('memory_trace.attribute_all_running')
        : i18n('memory_trace.attribute_all_btn')));
      const isoCount = countIsolated(state.snapshot);
      if (isoCount > 0) {
        right.append(el('button', {
          className: 'btn mtrace-isolated-btn' + (state.showIsolated ? ' active' : ''),
          title: i18n('memory_trace.isolated_toggle_hint'),
          onClick: () => { state.showIsolated = !state.showIsolated; renderAll(); },
        }, state.showIsolated
          ? i18n('memory_trace.isolated_hide')
          : i18n('memory_trace.isolated_show_fmt', isoCount)));
      }
    }
    right.append(el('button', {
      className: 'btn mtrace-reload-btn',
      onClick: () => reload(),
    }, i18n('memory_trace.reload')));
    bar.append(right);
    return bar;
  }

  function renderSidebar() {
    const side = el('div', { className: 'mtrace-sidebar' });

    // sources + counts summary.
    if (state.phase === 'ready' && state.snapshot) {
      const meta = state.snapshot.meta || {};
      const sources = meta.sources_present || {};
      const summary = el('div', { className: 'mtrace-summary' });
      summary.append(el('div', { className: 'mtrace-counts' },
        i18n('memory_trace.counts_fmt', meta.counts || {})));
      if (meta.node_budget && meta.node_budget.truncated) {
        summary.append(el('div', { className: 'mtrace-budget hint' },
          i18n('memory_trace.budget_fmt',
            meta.node_budget.shown, meta.node_budget.total)));
      }
      const srcList = el('ul', { className: 'mtrace-sources' });
      srcList.append(el('li', { className: sources.time_indexed_db ? 'is-on' : 'is-off' },
        sources.time_indexed_db
          ? i18n('memory_trace.sources.time_indexed_db_present')
          : i18n('memory_trace.sources.time_indexed_db_absent')));
      if (!sources.time_indexed_db) {
        srcList.append(el('li', { className: 'hint' },
          i18n('memory_trace.sources.time_indexed_db_hint')));
      }
      srcList.append(el('li', { className: sources.events_ndjson ? 'is-on' : 'is-off' },
        sources.events_ndjson
          ? i18n('memory_trace.sources.events_present')
          : i18n('memory_trace.sources.events_absent')));
      if (sources.trace_provenance) {
        srcList.append(el('li', { className: 'is-on' },
          i18n('memory_trace.sources.trace_present')));
      }
      summary.append(srcList);

      // read warnings (soft errors) if any.
      const warns = [
        ...(meta.file_warnings || []),
        ...(meta.corpus_warnings || []),
      ];
      if (warns.length) {
        const wbox = el('div', { className: 'mtrace-warnings' });
        wbox.append(el('div', { className: 'mtrace-warnings-head' },
          i18n('memory_trace.warnings_heading')));
        wbox.append(el('ul', {}, warns.map((w) => el('li', {}, w))));
        summary.append(wbox);
      }

      // legend.
      const legend = el('div', { className: 'mtrace-legend' });
      legend.append(el('span', { className: 'mtrace-legend-item' },
        el('span', { className: 'mtrace-legend-swatch solid' }),
        i18n('memory_trace.legend.solid')));
      legend.append(el('span', { className: 'mtrace-legend-item' },
        el('span', { className: 'mtrace-legend-swatch dashed' }),
        i18n('memory_trace.legend.dashed')));
      summary.append(legend);
      side.append(summary);
    }

    // detail panel.
    const detail = el('div', { className: 'mtrace-detail' });
    if (state.phase === 'ready' && state.snapshot) {
      renderDetailPanel(detail, state.snapshot, state.selectedId, {
        onSelect: selectNode,
        onAttribute: attributeNode,
        attributing: state.attributing,
        attributionMsg: state.attributionMsg,
        attributionFallback: state.attributionFallback,
      });
    }
    side.append(detail);
    return side;
  }

  function renderMain() {
    const main = el('div', { className: 'mtrace-canvas-wrap' });
    if (state.phase === 'loading') {
      main.append(el('div', { className: 'empty-state' }, i18n('memory_trace.loading')));
      return main;
    }
    if (state.phase === 'no_session') {
      main.append(emptyState('memory_trace.no_session'));
      return main;
    }
    if (state.phase === 'no_character') {
      main.append(emptyState('memory_trace.no_character'));
      return main;
    }
    if (state.phase === 'error') {
      main.append(el('div', { className: 'empty-state' },
        `${i18n('memory_trace.load_failed')}: ${state.errorMsg}`));
      return main;
    }
    // ready.
    const snap = state.snapshot;
    if (!snap || !snap.nodes || snap.nodes.length === 0) {
      main.append(emptyState('memory_trace.empty'));
      return main;
    }
    const graph = renderLineageGraph(snap, {
      focusId: state.mode === 'focus' ? state.focusId : null,
      selectedId: state.selectedId,
      onSelect: selectNode,
      onBlankClick,
      showIsolated: state.showIsolated,
      view: state.view,
      // Persist pan/zoom WITHOUT a re-render (keeps interaction smooth and
      // survives the next full renderAll, e.g. after selecting a node).
      onViewChange: (v) => { state.view = v; },
    });
    graphEl = graph;
    main.append(graph);
    // LOD hint: when the overview hides a large batch of heuristic (dashed)
    // edges for clarity/perf, tell the user how to see them (focus a node).
    const lod = graph.__mtrace || {};
    if (!lod.focused && lod.hiddenHeuristic > 0) {
      main.append(el('div', { className: 'mtrace-lod-hint' },
        i18n('memory_trace.heuristic_hidden_fmt', lod.hiddenHeuristic)));
    }
    main.append(renderZoomControls());
    return main;
  }

  function renderZoomControls() {
    const z = (factor) => () => graphEl && graphEl.__mtrace && graphEl.__mtrace.zoomBy(factor);
    const box = el('div', { className: 'mtrace-zoom-controls' });
    box.append(
      el('button', {
        className: 'mtrace-zoom-btn', title: i18n('memory_trace.zoom.in'),
        onClick: z(1.25),
      }, '+'),
      el('button', {
        className: 'mtrace-zoom-btn', title: i18n('memory_trace.zoom.out'),
        onClick: z(1 / 1.25),
      }, '\u2212'),
      el('button', {
        className: 'mtrace-zoom-btn', title: i18n('memory_trace.zoom.fit'),
        onClick: () => graphEl && graphEl.__mtrace && graphEl.__mtrace.fit(),
      }, i18n('memory_trace.zoom.fit_label')),
      el('button', {
        className: 'mtrace-zoom-btn', title: i18n('memory_trace.zoom.reset'),
        onClick: () => graphEl && graphEl.__mtrace && graphEl.__mtrace.reset(),
      }, '1:1'),
    );
    return box;
  }

  function emptyState(prefix) {
    return el('div', { className: 'empty-state mtrace-empty' },
      el('h3', {}, i18n(`${prefix}.heading`)),
      el('p', {}, i18n(`${prefix}.body`)));
  }

  function renderAll() {
    graphEl = null;
    host.innerHTML = '';
    host.append(renderToolbar());
    host.append(el('p', { className: 'mtrace-intro hint' }, i18n('memory_trace.intro')));
    if (state.phase === 'ready' && state.snapshot
        && (state.snapshot.nodes || []).length) {
      host.append(el('p', { className: 'mtrace-focus-tip hint' },
        i18n('memory_trace.focus_tip')));
    }
    const body = el('div', { className: 'mtrace-body' });
    body.append(renderMain());
    body.append(renderSidebar());
    host.append(body);
    // First time a graph appears (view still null), auto-fit it to the canvas
    // once it has real layout dimensions. fit() persists into state.view via
    // onViewChange, so this runs exactly once until the next session switch.
    if (graphEl && state.view === null) {
      const schedule = typeof requestAnimationFrame === 'function'
        ? requestAnimationFrame
        : (fn) => setTimeout(fn, 0);
      schedule(() => {
        if (graphEl && state.view === null && graphEl.__mtrace) graphEl.__mtrace.fit();
      });
    }
  }

  // ── subscriptions ──
  const offSession = on('session:change', () => {
    state.selectedId = null;
    state.focusId = null;
    state.mode = 'overview';
    state.view = null;  // re-fit the new character's graph
    reload();
  });
  const offActive = on('active_workspace:change', (id) => {
    if (id === 'memory_trace') reload();
  });

  renderAll();
  reload();

  return () => {
    reloadSeq++;  // invalidate any in-flight reload so it can't write post-teardown
    try { offSession(); } catch { /* ignore */ }
    try { offActive(); } catch { /* ignore */ }
  };
}
