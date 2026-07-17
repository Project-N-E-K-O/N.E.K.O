/**
 * memory_trace/overview.js — 系统概况 (Memory System Overview) sub-page.
 *
 * First / default sub-page of 记忆系统分析 (P29). The entry dashboard: one screen
 * of "what does this character's memory system look like + what's wrong with it",
 * with one-click drill-down into 记忆溯源 (lineage) or 向量空间 (embedding).
 *
 * Read-only. The frontend renders the backend aggregator verbatim (blueprint
 * §3.1): findings carry a stable `code` + numeric `data`; ALL human text lives
 * in i18n here (no Chinese literals in this file). Two optional LLM actions:
 *   POST /api/memory/overview/ai_report      → narrative health report
 *   POST /api/memory/overview/contradictions → L2 NLI over L1 candidates
 * Both degrade gracefully (the backend never 500s); their `warnings` name the
 * actionable reason (e.g. which API to configure) and are surfaced to the user.
 *
 * Honest contradiction framing (blueprint §6): B1/N1 are recorded contradictions
 * (real); B2 is "same-topic candidates to review" (a retrieval, NOT a verdict) —
 * only the LLM 裁决 action can label a pair contradiction.
 */

import { api } from '../../core/api.js';
import { i18n } from '../../core/i18n.js';
import { store, on } from '../../core/state.js';
import { toast } from '../../core/toast.js';
import { el } from '../_dom.js';
import { openMemoryExportModal } from './memory_export_modal.js';

const T = (k, ...a) => i18n(`memory_trace.overview.${k}`, ...a);

export function mountOverviewPage(host, ctx) {
  host.classList.add('memory-overview');
  host.innerHTML = '';

  const state = {
    phase: 'loading',     // loading|ready|no_session|no_character|error
    errorMsg: '',
    data: null,           // {cards, findings, attention_count, meta}
    showInfo: false,      // expand the low-severity (info) findings
    aiLoading: false,
    aiReport: null,       // {method, report, warnings}
    contraLoading: false,
    contra: null,         // {method, verdicts, candidates, warnings}
  };

  const goTo = (drill) => {
    if (!drill || !ctx || typeof ctx.goTo !== 'function') return;
    ctx.goTo(drill.page, drill.opts || {});
  };

  // Per-flow monotonic tokens so a slow response from a previous session can't
  // overwrite newer state (session switch / teardown bumps these). Each async
  // flow drops its result if its token was superseded while it was in flight.
  let overviewSeq = 0;
  let aiSeq = 0;
  let contraSeq = 0;

  async function reload() {
    const seq = ++overviewSeq;
    if (!store.session) { state.phase = 'no_session'; renderAll(); return; }
    state.phase = 'loading';
    state.aiReport = null; state.contra = null;
    renderAll();
    const res = await api.get('/api/memory/overview', { expectedStatuses: [404, 409] });
    if (seq !== overviewSeq) return;  // superseded by a newer reload
    if (res.ok) {
      state.data = res.data;
      state.phase = 'ready';
    } else if (res.status === 409 && res.error?.type === 'NoCharacterSelected') {
      state.phase = 'no_character';
    } else if (res.status === 404 && res.error?.type === 'NoActiveSession') {
      state.phase = 'no_session';
    } else {
      state.phase = 'error';
      state.errorMsg = res.error?.message || `HTTP ${res.status}`;
    }
    renderAll();
  }

  async function runAiReport() {
    if (state.aiLoading) return;
    const seq = ++aiSeq;
    state.aiLoading = true; renderAll();
    const res = await api.post('/api/memory/overview/ai_report', {},
      { expectedStatuses: [404, 409] });
    if (seq !== aiSeq) return;  // session changed / superseded while in flight
    state.aiLoading = false;
    if (res.ok) {
      state.aiReport = res.data;
      for (const w of (res.data.warnings || [])) toast.warn(w);
    } else {
      toast.err(T('ai.unavailable'), { message: res.error?.message || '' });
    }
    renderAll();
  }

  async function runContradictions() {
    if (state.contraLoading) return;
    const seq = ++contraSeq;
    state.contraLoading = true; renderAll();
    const res = await api.post('/api/memory/overview/contradictions', {},
      { expectedStatuses: [404, 409] });
    if (seq !== contraSeq) return;  // session changed / superseded while in flight
    state.contraLoading = false;
    if (res.ok) {
      state.contra = res.data;
      for (const w of (res.data.warnings || [])) toast.warn(w);
    } else {
      toast.err(T('contra.head'), { message: res.error?.message || '' });
    }
    renderAll();
  }

  // ── renderers ──

  function renderToolbar() {
    const bar = el('div', { className: 'mov-toolbar' });
    bar.append(el('h2', { className: 'mov-title' }, T('title')));
    const right = el('div', { className: 'mov-toolbar-right' });
    // Export is only meaningful once a character's memory has loaded.
    if (state.phase === 'ready') {
      right.append(el('button', {
        className: 'btn mov-export-btn',
        title: T('export.button_hint'),
        onClick: () => openMemoryExportModal({ characterName: (state.data || {}).character }),
      }, T('export.button')));
    }
    right.append(el('button', {
      className: 'btn mov-reload-btn', onClick: () => reload(),
    }, T('reload')));
    bar.append(right);
    return bar;
  }

  function card(headKey, mainText, extras) {
    const c = el('div', { className: 'mov-card' });
    c.append(el('div', { className: 'mov-card-head' }, T(`cards.${headKey}.head`)));
    c.append(el('div', { className: 'mov-card-main' }, mainText));
    for (const ex of (extras || [])) {
      if (ex) c.append(el('div', { className: 'mov-card-sub hint' }, ex));
    }
    return c;
  }

  function renderCards() {
    const cards = state.data.cards || {};
    const wrap = el('div', { className: 'mov-cards' });
    wrap.append(card('composition', T('cards.composition.fmt', cards.composition || {})));
    wrap.append(card('coverage',
      T('cards.coverage.fmt', cards.coverage || {}),
      [T('cards.coverage.detail_fmt', cards.coverage || {})]));
    const space = cards.space || {};
    wrap.append(card('space', T('cards.space.fmt', space),
      [space.other_space_count ? T('cards.space.other_fmt', space.other_space_count) : null]));
    wrap.append(card('clusters', T('cards.clusters.fmt', cards.clusters || {})));
    wrap.append(card('pipeline', T('cards.pipeline.fmt', cards.pipeline || {})));
    // credibility card — annotate with the localized level label + notes.
    const conf = (state.data.meta || {}).confidence || {};
    const confView = { ...conf, _levelLabel: T(`confidence.level.${conf.level || 'low'}`) };
    const credExtras = (conf.notes || []).map((n) => T(`confidence.note.${n}`));
    wrap.append(card('credibility', T('cards.credibility.fmt', confView), credExtras));
    return wrap;
  }

  function renderAttention() {
    const n = state.data.attention_count || 0;
    const banner = el('div', {
      className: 'mov-attention ' + (n > 0 ? 'has-issues' : 'all-clear'),
    });
    banner.append(el('span', { className: 'mov-attention-text' },
      n > 0 ? T('attention.some_fmt', n) : T('attention.none')));
    return banner;
  }

  function renderFinding(f) {
    const row = el('div', { className: `mov-finding sev-${f.severity}`, 'data-code': f.code });
    const head = el('div', { className: 'mov-finding-head' });
    head.append(el('span', { className: `mov-sev sev-${f.severity}` },
      T(`severity.${f.severity}`)));
    head.append(el('span', { className: 'mov-finding-cat' }, T(`category.${f.category}`)));
    head.append(el('span', { className: 'mov-finding-stage' }, T(`stage.${f.stage}`)));
    head.append(el('span', { className: 'mov-finding-title' },
      T(`finding.${f.code}.title`)));
    row.append(head);
    row.append(el('p', { className: 'mov-finding-detail' }, T(`finding.${f.code}.detail`, f)));
    // a few concrete examples (labels), kept compact.
    const exs = (f.examples || []).filter((e) => e && (e.label || e.a_label || e.old_text));
    if (exs.length) {
      const ul = el('ul', { className: 'mov-finding-examples' });
      for (const e of exs.slice(0, 5)) {
        const txt = e.label || e.a_label || e.old_text || '';
        ul.append(el('li', {}, txt));
      }
      row.append(ul);
    }
    if (f.drill && f.drill.page) {
      row.append(el('button', {
        className: 'btn mov-drill-btn',
        onClick: () => goTo(f.drill),
      }, T(`drill.${f.drill.page}`)));
    }
    return row;
  }

  function renderFindings() {
    const wrap = el('div', { className: 'mov-findings' });
    wrap.append(el('h3', { className: 'mov-findings-head' }, T('findings_head')));
    const all = state.data.findings || [];
    if (!all.length) {
      wrap.append(el('p', { className: 'hint' }, T('no_findings')));
      return wrap;
    }
    const important = all.filter((f) => f.severity !== 'info');
    const info = all.filter((f) => f.severity === 'info');
    for (const f of important) wrap.append(renderFinding(f));
    if (info.length) {
      wrap.append(el('button', {
        className: 'btn mov-info-toggle',
        onClick: () => { state.showInfo = !state.showInfo; renderAll(); },
      }, state.showInfo ? T('info_hide') : T('info_show_fmt', info.length)));
      if (state.showInfo) {
        for (const f of info) wrap.append(renderFinding(f));
      }
    }
    return wrap;
  }

  function renderLlm() {
    const box = el('div', { className: 'mov-llm' });

    // AI health report.
    const ai = el('div', { className: 'mov-ai' });
    ai.append(el('div', { className: 'mov-llm-head' }, T('ai.head')));
    ai.append(el('p', { className: 'hint' }, T('ai.hint')));
    ai.append(el('button', {
      className: 'btn mov-ai-btn', disabled: state.aiLoading,
      onClick: () => runAiReport(),
    }, state.aiLoading ? T('ai.running') : T('ai.btn')));
    if (state.aiReport) {
      if (state.aiReport.method === 'llm' && state.aiReport.report) {
        ai.append(el('div', { className: 'mov-ai-report' }, state.aiReport.report));
      } else {
        ai.append(el('p', { className: 'hint warn' }, T('ai.unavailable')));
        for (const w of (state.aiReport.warnings || [])) {
          ai.append(el('p', { className: 'mov-llm-reason hint warn' }, w));
        }
      }
    }
    box.append(ai);

    // Contradiction NLI judgement.
    const cn = el('div', { className: 'mov-contra' });
    cn.append(el('div', { className: 'mov-llm-head' }, T('contra.head')));
    cn.append(el('p', { className: 'hint' }, T('contra.hint')));
    cn.append(el('button', {
      className: 'btn mov-contra-btn', disabled: state.contraLoading,
      onClick: () => runContradictions(),
    }, state.contraLoading ? T('contra.running') : T('contra.btn')));
    if (state.contra) {
      const c = state.contra;
      if (c.method === 'none') {
        cn.append(el('p', { className: 'hint' }, T('contra.none')));
      } else if (c.method === 'llm' && (c.verdicts || []).length) {
        const ul = el('ul', { className: 'mov-contra-list' });
        for (const v of c.verdicts) {
          const view = { ...v, _relLabel: T(`contra.relation.${v.relation}`) };
          const li = el('li', { className: `mov-verdict rel-${v.relation}` });
          li.append(el('span', { className: 'mov-verdict-rel' },
            T('contra.verdict_fmt', view)));
          li.append(el('span', { className: 'mov-verdict-pair hint' },
            `A: ${v.a_label || ''} | B: ${v.b_label || ''}`));
          ul.append(li);
        }
        cn.append(ul);
      } else {
        cn.append(el('p', { className: 'hint warn' }, T('contra.empty_verdicts')));
        for (const w of (c.warnings || [])) {
          cn.append(el('p', { className: 'mov-llm-reason hint warn' }, w));
        }
        const ul = el('ul', { className: 'mov-contra-list' });
        for (const cand of (c.candidates || []).slice(0, 10)) {
          ul.append(el('li', { className: 'mov-verdict' },
            `A: ${cand.a_label || ''} | B: ${cand.b_label || ''}`));
        }
        cn.append(ul);
      }
    }
    box.append(cn);
    return box;
  }

  function emptyState(prefix) {
    return el('div', { className: 'empty-state mov-empty' },
      el('h3', {}, T(`${prefix}.heading`)),
      el('p', {}, T(`${prefix}.body`)));
  }

  function renderAll() {
    host.innerHTML = '';
    host.append(renderToolbar());
    host.append(el('p', { className: 'mov-intro hint' }, T('intro')));
    if (state.phase === 'loading') {
      host.append(el('div', { className: 'empty-state' }, T('loading')));
      return;
    }
    if (state.phase === 'no_session') { host.append(emptyState('no_session')); return; }
    if (state.phase === 'no_character') { host.append(emptyState('no_character')); return; }
    if (state.phase === 'error') {
      host.append(el('div', { className: 'empty-state' },
        `${T('load_failed')}: ${state.errorMsg}`));
      return;
    }
    // ready.
    const data = state.data || {};
    const cards = data.cards || {};
    const comp = cards.composition || {};
    const nothing = !comp.facts && !comp.reflections && !comp.persona && !comp.corrections;
    if (nothing) { host.append(emptyState('empty')); return; }
    host.append(renderAttention());
    host.append(renderCards());
    host.append(renderFindings());
    host.append(renderLlm());
  }

  // Test hook (jsdom smoke drives the flows without real clicks).
  host.__overview = {
    reload,
    runAiReport,
    runContradictions,
    get state() { return state; },
  };

  // ── subscriptions ──
  const offSession = on('session:change', () => {
    // Invalidate any in-flight AI/contradiction request so a slow response from
    // the previous session can't write onto the new one (reload bumps its own).
    aiSeq++; contraSeq++;
    state.aiReport = null; state.contra = null; state.showInfo = false;
    reload();
  });
  const offActive = on('active_workspace:change', (id) => {
    if (id === 'memory_trace') reload();
  });

  renderAll();
  reload();

  return () => {
    overviewSeq++; aiSeq++; contraSeq++;  // drop any in-flight writes post-teardown
    try { offSession(); } catch { /* ignore */ }
    try { offActive(); } catch { /* ignore */ }
  };
}
