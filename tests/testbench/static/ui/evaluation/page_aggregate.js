/**
 * page_aggregate.js — Evaluation → Aggregate 子页 (P17).
 *
 * 把 session.eval_results 聚合成一屏"看得见趋势"的视图, 配合 Results
 * 子页的 drill-down. 数据全部走 GET /api/judge/aggregate, 由后端
 * `judge_export.aggregate_results` 负责数学和分组; 本页只负责画图.
 *
 * 过滤同 Results 子页共享 localStorage 键 `testbench:evaluation:results:filter:v1`,
 * 所以"在 Results 收窄过滤 → 切到 Aggregate 看汇总"是自然流程.
 *
 * 布局 (单列):
 *   1. Header + intro (+ 空态)
 *   2. 总览卡片 (runs / successful / errored / pass rate / avg overall /
 *      avg gap / avg duration + verdict 分布)
 *   3. 按 Schema 分组 (每组: 维度雷达 SVG + comparative 时才出现 gap 折线
 *      + problem pattern 词频)
 *
 * 设计要点
 * --------
 * *   **纯 SVG, 零依赖**. 避免引入 Chart.js 等依赖; 评分只是测试工具,
 *     没必要装一套图表库. 雷达 / 折线都是手搓坐标点. 读者想知道值直接看
 *     table 即可.
 * *   **"radar 不够画" 场景**. 如果维度 < 3 或者没有有效结果, 雷达会退化成
 *     文字提示, 不画空图.
 * *   **Comparative gap 趋势**: 后端已经按 created_at 升序排好, x 轴就是
 *     "这批结果里的第 i 次", y 轴是 gap; 画一条折线 + 零线参考 + 每个点
 *     hover 显示 created_at. 不做缩放 / pan — 测试范围内几十个点够用.
 * *   **Problem pattern 词频**: 后端返回 `{pattern: count}` 已 most_common(25),
 *     这里只做字号映射 + 布局.
 * *   **共享过滤**: 本页不提供自己的过滤 UI (避免两份副本发散); 只读
 *     LS_FILTER_KEY, 并给用户一个 "去 Results 调整过滤" 的跳转 CTA.
 */

import { api } from '../../core/api.js';
import { i18n } from '../../core/i18n.js';
import { store, on } from '../../core/state.js';
import { el } from '../_dom.js';
import { openSessionExportModal } from '../session_export_modal.js';

const LS_FILTER_KEY = 'testbench:evaluation:results:filter:v1';

function loadPersistedFilter() {
  try {
    const raw = localStorage.getItem(LS_FILTER_KEY);
    if (!raw) return {};
    return JSON.parse(raw) || {};
  } catch {
    return {};
  }
}

function filterToQs(filter) {
  const usp = new URLSearchParams();
  for (const [k, v] of Object.entries(filter || {})) {
    if (v === '' || v == null) continue;
    usp.append(k, String(v));
  }
  const s = usp.toString();
  return s ? `?${s}` : '';
}

function defaultState() {
  return {
    sessionId: null,
    loading: false,
    filter: loadPersistedFilter(),
    aggregate: null,
    matched: 0,
  };
}

//
// ── main entry ────────────────────────────────────────────────────
//

export async function renderAggregatePage(host) {
  for (const k of ['__offSession', '__offJudgeResults']) {
    if (typeof host[k] === 'function') {
      try { host[k](); } catch { /* ignore */ }
      host[k] = null;
    }
  }

  host.innerHTML = '';
  const root = el('div', { className: 'eval-aggregate' });
  host.append(root);

  const state = defaultState();
  state.sessionId = store.session?.id || null;

  host.__offSession = on('session:change', () => {
    renderAggregatePage(host).catch((err) => {
      console.error('[evaluation/aggregate] remount failed:', err);
    });
  });
  // 新增评分完成后自动重算; 和 Results 子页一致的语义.
  host.__offJudgeResults = on('judge:results_changed', async () => {
    if (!state.sessionId) return;
    try {
      await loadAggregate(state);
      renderAll(root, state);
    } catch (err) {
      console.error('[evaluation/aggregate] refresh on judge change failed:', err);
    }
  });

  if (!state.sessionId) {
    root.append(renderEmptyState());
    return;
  }

  await loadAggregate(state);
  renderAll(root, state);
}

// P24 §14.4 M3: abort prev on rapid filter / schema-switch clicks.
let _aggregateLoadController = null;

async function loadAggregate(state) {
  if (_aggregateLoadController) {
    try { _aggregateLoadController.abort(); } catch { /* ignore */ }
  }
  const controller = new AbortController();
  _aggregateLoadController = controller;
  state.loading = true;
  const qs = filterToQs(state.filter);
  const resp = await api.get(`/api/judge/aggregate${qs}`, {
    expectedStatuses: [404],
    signal: controller.signal,
  });
  if (resp.error?.type === 'aborted') { state.loading = false; return; }
  if (_aggregateLoadController === controller) _aggregateLoadController = null;
  if (resp.ok) {
    state.aggregate = resp.data?.aggregate || null;
    state.matched = resp.data?.matched || 0;
  } else {
    state.aggregate = null;
    state.matched = 0;
  }
  state.loading = false;
}

function renderEmptyState() {
  return el('div', { className: 'empty-state' },
    el('h3', {}, i18n('evaluation.aggregate.no_session.heading')),
    el('p', {}, i18n('evaluation.aggregate.no_session.body')),
  );
}

//
// ── render ────────────────────────────────────────────────────────
//

function renderAll(root, state) {
  root.innerHTML = '';
  root.append(renderHeader());
  root.append(renderIntro(state));
  if (state.loading) {
    root.append(el('div', { className: 'eval-results-loading' },
      i18n('evaluation.aggregate.loading')));
    return;
  }
  if (!state.aggregate || state.matched === 0) {
    root.append(renderEmpty(state));
    return;
  }
  root.append(renderOverviewSection(state));
  root.append(renderSchemaBreakdownSection(state));
}

function renderHeader() {
  return el('h2', {}, i18n('evaluation.aggregate.heading'));
}

function renderIntro(state) {
  const activeKeys = Object.entries(state.filter || {})
    .filter(([, v]) => v !== '' && v != null)
    .map(([k]) => k);
  const wrap = el('div', { className: 'eval-aggregate-intro' });
  // Intro row: description on the left, P23 session-report export
  // button on the right. The button is scoped to conversation+evaluations
  // + markdown by default — the most common "I want a reviewer-shareable
  // record" flow. Advanced users can tweak scope/format in the modal.
  const topRow = el('div', { className: 'eval-aggregate-intro-row' });
  topRow.append(el('p', { className: 'intro' }, i18n('evaluation.aggregate.intro')));
  topRow.append(el('button', {
    className: 'small',
    title: i18n('evaluation.aggregate.export_btn_hint'),
    onClick: () => openSessionExportModal({
      scope: 'conversation_evaluations',
      format: 'markdown',
      subtitle: i18n('evaluation.aggregate.export_modal_subtitle'),
    }),
  }, i18n('evaluation.aggregate.export_btn')));
  wrap.append(topRow);
  if (activeKeys.length) {
    const chips = el('div', { className: 'eval-results-filter-chips' });
    for (const k of activeKeys) {
      chips.append(el('span', { className: 'badge subtle' },
        `${k}: ${state.filter[k]}`));
    }
    wrap.append(chips);
  }
  return wrap;
}

function renderEmpty(state) {
  const wrap = el('div', { className: 'empty-state' },
    el('h3', {}, i18n('evaluation.aggregate.empty')),
  );
  return wrap;
}

//
// ── overview ─────────────────────────────────────────────────────
//

function renderOverviewSection(state) {
  const agg = state.aggregate;
  const wrap = el('div', { className: 'eval-aggregate-section' });
  wrap.append(el('h3', {}, i18n('evaluation.aggregate.section.overview')));

  const cards = el('div', { className: 'eval-aggregate-cards' });
  const avgOverall = agg.avg_overall || {};
  // 每张卡片都配一句中文释义 (cards_hint.*), 因为这些指标名里混了若干
  // 英文术语 (absolute / comparative / gap / verdict), 不解释容易让新人
  // 对着数字发呆. 释义放在 value 下方小字, 始终可见而不是 tooltip.
  const items = [
    { key: 'total_runs',      label: i18n('evaluation.aggregate.cards.total_runs'),
      hint: i18n('evaluation.aggregate.cards_hint.total_runs'),
      value: agg.total },
    { key: 'successful',      label: i18n('evaluation.aggregate.cards.successful'),
      hint: i18n('evaluation.aggregate.cards_hint.successful'),
      value: agg.effective },
    { key: 'errored',         label: i18n('evaluation.aggregate.cards.errored'),
      hint: i18n('evaluation.aggregate.cards_hint.errored'),
      value: agg.errored, danger: (agg.errored || 0) > 0 },
    { key: 'pass_rate',       label: i18n('evaluation.aggregate.cards.pass_rate'),
      hint: i18n('evaluation.aggregate.cards_hint.pass_rate'),
      value: agg.pass_rate != null ? `${(agg.pass_rate * 100).toFixed(1)}%` : '-' },
    { key: 'avg_overall_abs', label: i18n('evaluation.aggregate.cards.avg_overall_abs'),
      hint: i18n('evaluation.aggregate.cards_hint.avg_overall_abs'),
      value: avgOverall.absolute != null ? `${avgOverall.absolute.toFixed(1)}/100` : '-' },
    { key: 'avg_overall_cmp', label: i18n('evaluation.aggregate.cards.avg_overall_cmp_a'),
      hint: i18n('evaluation.aggregate.cards_hint.avg_overall_cmp_a'),
      value: avgOverall.comparative_a != null ? `${avgOverall.comparative_a.toFixed(1)}/100` : '-' },
    { key: 'avg_gap',         label: i18n('evaluation.aggregate.cards.avg_gap'),
      hint: i18n('evaluation.aggregate.cards_hint.avg_gap'),
      value: agg.avg_gap != null
        ? `${agg.avg_gap >= 0 ? '+' : ''}${agg.avg_gap.toFixed(2)}` : '-' },
    { key: 'avg_duration',    label: i18n('evaluation.aggregate.cards.avg_duration'),
      hint: i18n('evaluation.aggregate.cards_hint.avg_duration'),
      value: `${agg.avg_duration_ms || 0} ms` },
  ];
  for (const it of items) {
    cards.append(el('div', {
      className: 'eval-aggregate-card' + (it.danger ? ' eval-aggregate-card--danger' : ''),
    },
      el('span', { className: 'label' }, it.label),
      el('span', { className: 'value' }, String(it.value ?? '-')),
      it.hint ? el('span', { className: 'hint' }, it.hint) : null,
    ));
  }
  wrap.append(cards);

  const vd = agg.verdict_distribution || {};
  const vdEntries = Object.entries(vd);
  if (vdEntries.length) {
    const vdWrap = el('div', { className: 'eval-aggregate-verdicts' });
    vdWrap.append(el('strong', {}, i18n('evaluation.aggregate.verdict_heading')));
    for (const [verdict, count] of vdEntries) {
      vdWrap.append(el('span', {
        className: 'badge verdict-' + String(verdict).replace(/[^a-z_]/gi, '_'),
      }, `${verdict} · ${count}`));
    }
    wrap.append(vdWrap);
    wrap.append(el('p', { className: 'eval-aggregate-section-hint' },
      i18n('evaluation.aggregate.verdict_hint')));
  }

  return wrap;
}

//
// ── per-schema breakdown ─────────────────────────────────────────
//

function renderSchemaBreakdownSection(state) {
  const agg = state.aggregate;
  const by = agg.by_schema || {};
  const wrap = el('div', { className: 'eval-aggregate-section' });
  wrap.append(el('h3', {}, i18n('evaluation.aggregate.section.schema_breakdown')));
  wrap.append(el('p', { className: 'eval-aggregate-section-hint' },
    i18n('evaluation.aggregate.section_hint.schema_breakdown')));
  const ids = Object.keys(by);
  if (!ids.length) {
    wrap.append(el('div', { className: 'empty-inline' },
      i18n('evaluation.aggregate.empty')));
    return wrap;
  }
  for (const sid of ids) {
    wrap.append(renderSchemaBlock(sid, by[sid]));
  }
  return wrap;
}

function renderSchemaBlock(sid, block) {
  const wrap = el('div', { className: 'eval-aggregate-schema' });
  const head = el('div', { className: 'eval-aggregate-schema-head' });
  head.append(el('strong', {}, sid));
  head.append(el('span', { className: 'badge subtle' }, block.mode || '-'));
  head.append(el('span', { className: 'badge subtle' }, block.granularity || '-'));
  const erroredSuffix = block.errored
    ? ` (${i18n('evaluation.aggregate.schema_meta.errored_fmt', block.errored)})`
    : '';
  head.append(el('span', { className: 'meta' },
    `n=${block.count}${erroredSuffix}`));
  if (block.pass_rate != null) {
    head.append(el('span', { className: 'meta' },
      i18n('evaluation.aggregate.schema_meta.pass_fmt',
        `${(block.pass_rate * 100).toFixed(1)}%`)));
  }
  if (block.avg_overall != null) {
    head.append(el('span', { className: 'meta' },
      i18n('evaluation.aggregate.schema_meta.avg_overall_fmt',
        block.avg_overall.toFixed(1))));
  }
  if (block.mode === 'comparative' && block.avg_gap != null) {
    head.append(el('span', { className: 'meta' },
      i18n('evaluation.aggregate.schema_meta.avg_gap_fmt',
        `${block.avg_gap >= 0 ? '+' : ''}${block.avg_gap.toFixed(2)}`)));
  }
  wrap.append(head);

  // Radar + (if comparative) gap line, side by side when room.
  const charts = el('div', { className: 'eval-aggregate-charts' });
  charts.append(renderRadar(block));
  if (block.mode === 'comparative') {
    charts.append(renderGapLine(block));
  }
  wrap.append(charts);

  const dims = block.dimensions || [];
  if (dims.length) {
    wrap.append(renderDimTable(dims));
  }

  const pp = block.problem_patterns || {};
  if (Object.keys(pp).length) {
    wrap.append(renderProblemPatterns(pp));
  }

  const vd = block.verdict_distribution || {};
  if (Object.keys(vd).length) {
    const vdWrap = el('div', { className: 'eval-aggregate-verdicts' });
    for (const [verdict, count] of Object.entries(vd)) {
      vdWrap.append(el('span', {
        className: 'badge verdict-' + String(verdict).replace(/[^a-z_]/gi, '_'),
      }, `${verdict} · ${count}`));
    }
    wrap.append(vdWrap);
  }

  return wrap;
}

//
// ── SVG radar ────────────────────────────────────────────────────
//

const SVG_NS = 'http://www.w3.org/2000/svg';

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

function renderRadar(block) {
  const dims = block.dimensions || [];
  const usable = dims.filter((d) => d.avg != null);
  const wrap = el('div', { className: 'eval-aggregate-chart' });
  wrap.append(el('h4', {}, i18n('evaluation.aggregate.section.radar')));
  wrap.append(el('div', { className: 'hint' },
    i18n('evaluation.aggregate.section_hint.radar')));

  if (usable.length < 3) {
    wrap.append(el('div', { className: 'empty-inline' },
      i18n('evaluation.aggregate.radar_empty')));
    return wrap;
  }

  // Auto-detect scale: pull the max label from `/<max>` patterns or
  // infer from schema snapshot max_raw_score (not available here, so we
  // use the ceiling of the observed max, rounded up to nearest 10).
  const observedMax = Math.max(...usable.map((d) => d.avg));
  const scaleMax = observedMax <= 10 ? 10
    : observedMax <= 20 ? 20
    : observedMax <= 50 ? 50 : 100;

  const size = 280;
  const pad = 38;
  const cx = size / 2;
  const cy = size / 2;
  const radius = (size - pad * 2) / 2;

  const n = usable.length;
  // Angles start at top (−90°) and go clockwise.
  const angleFor = (i) => -Math.PI / 2 + (i * 2 * Math.PI) / n;
  const pointFor = (i, value) => {
    const r = radius * (value / scaleMax);
    const a = angleFor(i);
    return [cx + r * Math.cos(a), cy + r * Math.sin(a)];
  };

  const root = svg('svg', {
    width: size, height: size, viewBox: `0 0 ${size} ${size}`,
    class: 'eval-aggregate-radar-svg',
  });

  // Grid rings (4 concentric polygons at 25/50/75/100 % of scale).
  for (const frac of [0.25, 0.5, 0.75, 1.0]) {
    const points = [];
    for (let i = 0; i < n; i++) {
      const a = angleFor(i);
      const r = radius * frac;
      points.push(`${cx + r * Math.cos(a)},${cy + r * Math.sin(a)}`);
    }
    root.append(svg('polygon', {
      points: points.join(' '),
      fill: 'none',
      stroke: 'var(--border)',
      'stroke-width': 1,
      'stroke-dasharray': frac === 1.0 ? '' : '2 3',
    }));
  }
  // Axes.
  for (let i = 0; i < n; i++) {
    const [x, y] = pointFor(i, scaleMax);
    root.append(svg('line', {
      x1: cx, y1: cy, x2: x, y2: y,
      stroke: 'var(--border)', 'stroke-width': 1,
    }));
  }
  // Data polygon.
  const dataPoints = usable.map((d, i) => {
    const [x, y] = pointFor(i, d.avg);
    return `${x},${y}`;
  }).join(' ');
  root.append(svg('polygon', {
    points: dataPoints,
    fill: 'rgba(110, 168, 254, 0.25)',
    stroke: 'var(--link)',
    'stroke-width': 2,
  }));
  // Data dots.
  for (let i = 0; i < n; i++) {
    const [x, y] = pointFor(i, usable[i].avg);
    root.append(svg('circle', {
      cx: x, cy: y, r: 3,
      fill: 'var(--link)',
    }));
  }
  // Labels (outside the axes).
  for (let i = 0; i < n; i++) {
    const a = angleFor(i);
    const labelR = radius + 16;
    const lx = cx + labelR * Math.cos(a);
    const ly = cy + labelR * Math.sin(a);
    const anchor = Math.abs(Math.cos(a)) < 0.2
      ? 'middle' : (Math.cos(a) > 0 ? 'start' : 'end');
    const d = usable[i];
    const label = svg('text', {
      x: lx, y: ly,
      'text-anchor': anchor,
      'dominant-baseline': 'middle',
      'font-size': 11,
      fill: 'var(--text-secondary)',
    }, d.label || d.key);
    root.append(label);
    // Value chip under label.
    const valueLabel = svg('text', {
      x: lx, y: ly + 12,
      'text-anchor': anchor,
      'dominant-baseline': 'middle',
      'font-size': 10,
      fill: 'var(--text-tertiary)',
    }, `${d.avg.toFixed(1)}/${scaleMax}`);
    root.append(valueLabel);
  }
  wrap.append(root);

  if (block.dimensions && block.dimensions.length > usable.length) {
    const missing = block.dimensions.length - usable.length;
    wrap.append(el('div', { className: 'hint' },
      `(${missing} dim w/o samples)`));
  }
  return wrap;
}

//
// ── SVG gap line ─────────────────────────────────────────────────
//

function renderGapLine(block) {
  const traj = block.gap_trajectory || [];
  const wrap = el('div', { className: 'eval-aggregate-chart' });
  wrap.append(el('h4', {}, i18n('evaluation.aggregate.section.gap_line')));
  wrap.append(el('div', { className: 'hint' },
    i18n('evaluation.aggregate.section_hint.gap_line')));
  if (!traj.length) {
    wrap.append(el('div', { className: 'empty-inline' },
      i18n('evaluation.aggregate.gap_line_empty')));
    return wrap;
  }
  const width = 380;
  const height = 180;
  const pad = { top: 12, right: 16, bottom: 28, left: 34 };
  const plotW = width - pad.left - pad.right;
  const plotH = height - pad.top - pad.bottom;
  const gaps = traj.map((t) => Number(t.gap));
  // Symmetric y-range around 0 so the zero line is always visible and
  // positive/negative swings read fairly.
  const absMax = Math.max(1, ...gaps.map((g) => Math.abs(g)));
  const yMax = Math.ceil(absMax / 5) * 5; // round up to nearest 5
  const xFor = (i) => pad.left + (traj.length > 1
    ? (i / (traj.length - 1)) * plotW
    : plotW / 2);
  const yFor = (g) => pad.top + plotH / 2 - (g / yMax) * (plotH / 2);

  const root = svg('svg', {
    width, height, viewBox: `0 0 ${width} ${height}`,
    class: 'eval-aggregate-line-svg',
  });
  // Zero line.
  const y0 = yFor(0);
  root.append(svg('line', {
    x1: pad.left, x2: pad.left + plotW, y1: y0, y2: y0,
    stroke: 'var(--border-strong)', 'stroke-width': 1,
    'stroke-dasharray': '3 3',
  }));
  // Y-axis ticks at ±yMax, ±yMax/2, 0.
  for (const tick of [yMax, yMax / 2, 0, -yMax / 2, -yMax]) {
    const y = yFor(tick);
    root.append(svg('line', {
      x1: pad.left - 3, x2: pad.left, y1: y, y2: y,
      stroke: 'var(--border-strong)', 'stroke-width': 1,
    }));
    root.append(svg('text', {
      x: pad.left - 6, y,
      'text-anchor': 'end',
      'dominant-baseline': 'middle',
      'font-size': 10,
      fill: 'var(--text-tertiary)',
    }, `${tick > 0 ? '+' : ''}${tick}`));
  }
  // Line path.
  if (traj.length === 1) {
    // Single point: just draw a dot.
    const [x] = [xFor(0)];
    const y = yFor(traj[0].gap);
    root.append(svg('circle', {
      cx: x, cy: y, r: 4, fill: 'var(--link)',
    }));
  } else {
    const pts = traj.map((t, i) => `${xFor(i)},${yFor(t.gap)}`).join(' ');
    root.append(svg('polyline', {
      points: pts,
      fill: 'none', stroke: 'var(--link)', 'stroke-width': 2,
    }));
    for (let i = 0; i < traj.length; i++) {
      const t = traj[i];
      const x = xFor(i);
      const y = yFor(t.gap);
      const color = t.gap > 0 ? 'var(--ok)' : t.gap < 0 ? 'var(--warn)' : 'var(--text-tertiary)';
      const c = svg('circle', {
        cx: x, cy: y, r: 3.5,
        fill: color,
      });
      // Native SVG <title> for hover tooltip; good enough for testbench.
      c.append(svg('title', {},
        `${(t.t || '').replace('T', ' ').slice(0, 19)}\n` +
        `gap: ${t.gap >= 0 ? '+' : ''}${Number(t.gap).toFixed(2)}\n` +
        `verdict: ${t.verdict || '-'}`));
      root.append(c);
    }
  }
  // X-axis (first/last time labels only — avoids cramping).
  if (traj.length >= 1) {
    const firstT = (traj[0].t || '').slice(11, 19);
    const lastT = (traj[traj.length - 1].t || '').slice(11, 19);
    root.append(svg('text', {
      x: pad.left, y: height - 8,
      'text-anchor': 'start',
      'font-size': 10,
      fill: 'var(--text-tertiary)',
    }, firstT));
    if (traj.length > 1) {
      root.append(svg('text', {
        x: pad.left + plotW, y: height - 8,
        'text-anchor': 'end',
        'font-size': 10,
        fill: 'var(--text-tertiary)',
      }, lastT));
    }
  }
  wrap.append(root);
  return wrap;
}

//
// ── per-dim table ───────────────────────────────────────────────
//

function renderDimTable(dims) {
  const table = el('table', { className: 'eval-results-cmp-table' });
  table.append(el('thead', {}, el('tr', {},
    el('th', {}, i18n('evaluation.aggregate.dim_table.dimension')),
    el('th', {}, i18n('evaluation.aggregate.dim_table.avg')),
    el('th', {}, i18n('evaluation.aggregate.dim_table.samples')),
  )));
  const tbody = el('tbody', {});
  for (const d of dims) {
    tbody.append(el('tr', {},
      el('td', {}, d.label ? `${d.label} (${d.key})` : d.key),
      el('td', { className: 'mono' },
        d.avg == null ? '-' : Number(d.avg).toFixed(2)),
      el('td', { className: 'mono' }, String(d.samples || 0)),
    ));
  }
  table.append(tbody);
  return table;
}

//
// ── problem pattern cloud ──────────────────────────────────────
//

function renderProblemPatterns(counts) {
  const wrap = el('div', { className: 'eval-aggregate-cloud' });
  wrap.append(el('h4', {}, i18n('evaluation.aggregate.section.problem_patterns')));
  wrap.append(el('div', { className: 'hint' },
    i18n('evaluation.aggregate.section_hint.problem_patterns')));
  const entries = Object.entries(counts);
  if (!entries.length) {
    wrap.append(el('div', { className: 'empty-inline' },
      i18n('evaluation.aggregate.problem_empty')));
    return wrap;
  }
  const maxCount = Math.max(...entries.map(([, n]) => n));
  const minCount = Math.min(...entries.map(([, n]) => n));
  const sizeFor = (n) => {
    if (maxCount === minCount) return 14;
    const t = (n - minCount) / (maxCount - minCount);
    return Math.round(11 + t * 10); // 11..21 px
  };
  const cloud = el('div', { className: 'eval-aggregate-cloud-items' });
  for (const [pattern, count] of entries) {
    cloud.append(el('span', {
      className: 'eval-aggregate-cloud-item',
      style: { fontSize: `${sizeFor(count)}px` },
      title: `${pattern} · ${count}`,
    }, `${pattern} (${count})`));
  }
  wrap.append(cloud);
  return wrap;
}
