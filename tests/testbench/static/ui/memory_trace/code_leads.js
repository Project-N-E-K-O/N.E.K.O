/**
 * memory_trace/code_leads.js — 代码线索 (开发者) sub-page (P32).
 *
 * 4th sub-page of 记忆系统分析. Turns the mechanical-invariant findings the P29
 * overview already computed (+ two extra deterministic checks, ID-DUP / EVT-DUP)
 * into *navigational leads* pointing at which main-program memory module is worth
 * inspecting. Single backend chokepoint: GET /api/memory/code_leads.
 *
 * THIS PAGE'S JOB IS TO LOWER CONFIDENCE, NOT RAISE IT (blueprint P32 §3):
 *  - UI-1: an un-dismissible, non-collapsible red danger notice pinned at the top
 *          spelling out that these are directions, not bug reports.
 *  - UI-2: wording discipline — lead cards never assert "bug/缺陷/确认存在";
 *          they use imperative "建议排查…" phrasing. All human text lives in i18n.
 *  - UI-3: a restrained strength chip + persistent "(仍需人工确认)".
 *  - UI-4: each lead card carries suspect modules + what runtime evidence is still
 *          missing to confirm it.
 *  - UI-5: an excluded-区 (content-quality findings we deliberately DON'T infer to
 *          code) + honest status rows (embedding / evt "checked vs not checked").
 *
 * Read-only. The frontend renders the aggregator verbatim; findings carry a stable
 * `code`, and the human invariant / action text is looked up by code in i18n.
 */

import { api } from '../../core/api.js';
import { i18n } from '../../core/i18n.js';
import { store, on } from '../../core/state.js';
import { el } from '../_dom.js';

const T = (k, ...a) => i18n(`memory_trace.code_leads.${k}`, ...a);

export function mountCodeLeadsPage(host, ctx) {
  host.classList.add('memory-code-leads');
  host.innerHTML = '';

  const state = {
    phase: 'loading',   // loading|ready|no_session|no_character|error
    errorMsg: '',
    data: null,         // {leads, excluded_content_findings, embedding_status, evt_status, warnings}
  };

  // Monotonic token so a slow response from a previous session/character can't
  // overwrite newer state (session switch / teardown bumps it — LR-10 §7.19).
  let reloadSeq = 0;

  async function reload() {
    const seq = ++reloadSeq;
    if (!store.session) { state.phase = 'no_session'; renderAll(); return; }
    state.phase = 'loading';
    renderAll();
    const res = await api.get('/api/memory/code_leads', { expectedStatuses: [404, 409] });
    if (seq !== reloadSeq) return;  // superseded by a newer reload
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

  // ── renderers ──

  function renderToolbar() {
    const bar = el('div', { className: 'mov-toolbar' });
    bar.append(el('h2', { className: 'mov-title' }, T('title')));
    const right = el('div', { className: 'mov-toolbar-right' });
    right.append(el('button', {
      className: 'btn mcl-reload-btn', onClick: () => reload(),
    }, T('reload')));
    bar.append(right);
    return bar;
  }

  // UI-1 — always visible, non-collapsible red warning (6 要素).
  function renderNotice() {
    const box = el('div', { className: 'code-leads-notice code-leads-notice--danger' });
    box.append(el('div', { className: 'code-leads-notice-head' }, T('notice.head')));
    const ul = el('ul', { className: 'code-leads-notice-list' });
    for (const k of ['p1', 'p2', 'p3', 'p4', 'p5']) {
      ul.append(el('li', {}, T(`notice.${k}`)));
    }
    const last = el('li', {});
    last.append(document.createTextNode(T('notice.p6') + ' '));
    // Link to the tester-facing guide served at /docs/code_leads_guide (opens in
    // a new tab). The internal 裁决 doc stays out of /docs by design.
    last.append(el('a', {
      className: 'code-leads-doc',
      href: '/docs/code_leads_guide',
      target: '_blank',
      rel: 'noopener',
    }, T('notice.link')));
    ul.append(last);
    box.append(ul);
    return box;
  }

  function strengthChip(strength) {
    const chip = el('span', {
      className: `code-leads-strength strength-${strength}`,
      title: T('strength.hint'),
    }, T(`strength.${strength}`));
    return chip;
  }

  function renderLead(lead) {
    const card = el('div', {
      className: `code-leads-card strength-${lead.strength}`,
      'data-code': lead.code,
    });

    const head = el('div', { className: 'code-leads-card-head' });
    head.append(el('span', { className: 'code-leads-badge' }, lead.code));
    head.append(strengthChip(lead.strength));
    head.append(el('span', { className: 'code-leads-confirm hint' }, T('needs_confirm')));
    card.append(head);

    // Violated invariant + count.
    card.append(el('p', { className: 'code-leads-invariant' }, T(`invariant.${lead.code}`)));
    card.append(el('p', { className: 'code-leads-count hint' }, T('count_fmt', lead.count || 0)));

    // Imperative suggested action (never a verdict).
    card.append(el('p', { className: 'code-leads-action' }, T(`action.${lead.code}`)));

    // Suspect modules (a direction, not a locator).
    const suspects = (lead.suspect_modules || []).filter(Boolean);
    if (suspects.length) {
      const s = el('div', { className: 'code-leads-suspect' });
      s.append(el('div', { className: 'code-leads-subhead' }, T('suspect_head')));
      s.append(el('ul', {}, suspects.map((m) => el('li', { className: 'code-leads-module' }, m))));
      card.append(s);
    }

    // What runtime evidence is still missing to confirm.
    const missing = (lead.missing_evidence || []).filter(Boolean);
    if (missing.length) {
      const m = el('div', { className: 'code-leads-missing' });
      m.append(el('div', { className: 'code-leads-subhead' }, T('missing_head')));
      m.append(el('ul', {}, missing.map((x) => el('li', { className: 'hint' }, x))));
      card.append(m);
    }

    // A few concrete examples (id/label only), compact.
    const exs = (lead.examples || []).filter((e) => e && (e.id || e.label));
    if (exs.length) {
      const ex = el('div', { className: 'code-leads-examples' });
      ex.append(el('div', { className: 'code-leads-subhead hint' }, T('examples_head')));
      ex.append(el('ul', {}, exs.slice(0, 8).map(
        (e) => el('li', { className: 'hint' }, e.label ? `${e.id} — ${e.label}` : e.id))));
      card.append(ex);
    }
    return card;
  }

  function renderLeads() {
    const wrap = el('div', { className: 'code-leads-list' });
    wrap.append(el('h3', { className: 'code-leads-head' }, T('leads_head')));
    const leads = (state.data.leads || []).filter(Boolean);
    if (!leads.length) {
      // UI-5 honest empty: no lead ≠ code is fine.
      wrap.append(el('div', { className: 'empty-state code-leads-empty' },
        el('h3', {}, T('empty_leads.head')),
        el('p', {}, T('empty_leads.body'))));
      return wrap;
    }
    for (const lead of leads) wrap.append(renderLead(lead));
    return wrap;
  }

  // UI-5 — status rows (checked vs not checked, never silent).
  function renderStatus() {
    const box = el('div', { className: 'code-leads-status' });
    box.append(el('div', { className: 'code-leads-subhead' }, T('status.head')));
    const ul = el('ul', {});
    const emb = state.data.embedding_status;
    ul.append(el('li', { className: emb === 'ran' ? 'is-on' : 'is-off' },
      emb === 'ran' ? T('status.embedding_ran') : T('status.embedding_unavailable')));
    const evt = state.data.evt_status;
    let evtKey = 'status.evt_unavailable';
    if (evt === 'ran') evtKey = 'status.evt_ran';
    else if (evt === 'truncated') evtKey = 'status.evt_truncated';
    ul.append(el('li', { className: evt === 'ran' ? 'is-on' : 'is-off' }, T(evtKey)));
    box.append(ul);

    const warns = (state.data.warnings || []).filter(Boolean);
    if (warns.length) {
      const wbox = el('div', { className: 'code-leads-warnings' });
      wbox.append(el('div', { className: 'code-leads-subhead' }, T('warnings_head')));
      wbox.append(el('ul', {}, warns.map((w) => el('li', { className: 'hint warn' }, w))));
      box.append(wbox);
    }
    return box;
  }

  // UI-5 — excluded content-quality findings (deliberately NOT inferred to code).
  function renderExcluded() {
    const box = el('div', { className: 'code-leads-excluded' });
    box.append(el('div', { className: 'code-leads-subhead' }, T('excluded.head')));
    box.append(el('p', { className: 'hint' }, T('excluded.intro')));
    const items = (state.data.excluded_content_findings || []).filter((x) => x && x.code);
    if (items.length) {
      box.append(el('ul', { className: 'code-leads-excluded-list' },
        items.map((x) => el('li', { className: 'hint' }, T('excluded.item_fmt', x.code, x.count || 0)))));
    }
    return box;
  }

  function emptyState(prefix) {
    return el('div', { className: 'empty-state mcl-empty' },
      el('h3', {}, T(`${prefix}.heading`)),
      el('p', {}, T(`${prefix}.body`)));
  }

  function renderAll() {
    host.innerHTML = '';
    host.append(renderToolbar());
    host.append(el('p', { className: 'mov-intro hint' }, T('intro')));
    // The danger notice is ALWAYS shown, in every phase (never hidden behind a
    // loading/empty state) so the caveat can't be missed.
    host.append(renderNotice());
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
    host.append(renderLeads());
    host.append(renderStatus());
    host.append(renderExcluded());
  }

  // Test hook (jsdom smoke drives the flow without real clicks).
  host.__codeLeads = {
    reload,
    get state() { return state; },
  };

  // ── subscriptions ──
  const offSession = on('session:change', () => { reload(); });
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
