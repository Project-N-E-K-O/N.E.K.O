/**
 * memory_trace/detail_panel.js — right-rail node detail for the trace graph.
 *
 * Pure render from the snapshot + a selected node id. Shows the node's full
 * content plus its upstream (incoming edges) and downstream (outgoing edges)
 * with the relation + confidence label, so a tester can read "this reflection
 * came from facts X,Y and was promoted into persona Z". Read-only.
 */

import { i18n } from '../../core/i18n.js';
import { el } from '../_dom.js';

function _nodeIndex(snapshot) {
  const map = new Map();
  for (const n of snapshot.nodes) map.set(n.id, n);
  return map;
}

function _relationLabel(edge) {
  const rel = i18n(`memory_trace.relation.${edge.relation}`);
  const conf = i18n(`memory_trace.confidence.${edge.confidence || 'persisted'}`);
  return `${rel} · ${conf}`;
}

function _linkRow(otherNode, edge, onSelect) {
  const typeLabel = i18n(`memory_trace.node_type.${otherNode.type}`);
  return el('li', { className: 'mtrace-link' },
    el('button', {
      className: 'mtrace-link-btn',
      onClick: () => onSelect(otherNode.id),
    }, `${typeLabel}: ${otherNode.label || otherNode.id}`),
    el('span', { className: 'mtrace-link-rel' }, _relationLabel(edge)),
  );
}

const _ATTRIBUTABLE = new Set(['fact', 'reflection', 'persona_entry']);

/**
 * @param {HTMLElement} host  container to fill
 * @param {object} snapshot
 * @param {string|null} selectedId
 * @param {object} opts  { onSelect, onAttribute, attributing, attributionMsg,
 *                         attributionFallback }
 */
export function renderDetailPanel(host, snapshot, selectedId, opts = {}) {
  const {
    onSelect = () => {},
    onAttribute = null, attributing = false, attributionMsg = '',
    attributionFallback = false,
  } = opts;
  host.innerHTML = '';
  host.append(el('h3', { className: 'mtrace-detail-title' },
    i18n('memory_trace.detail.heading')));

  if (!selectedId) {
    host.append(el('div', { className: 'empty-state' },
      i18n('memory_trace.detail.empty')));
    return;
  }
  const index = _nodeIndex(snapshot);
  const node = index.get(selectedId);
  if (!node) {
    host.append(el('div', { className: 'empty-state' },
      i18n('memory_trace.detail.empty')));
    return;
  }

  const meta = node.meta || {};
  const content = meta.text || meta.content || meta.new_text || node.label || '';

  const rows = el('dl', { className: 'mtrace-detail-rows' });
  const addRow = (label, value) => {
    if (value == null || value === '') return;
    rows.append(
      el('dt', {}, label),
      el('dd', {}, String(value)),
    );
  };
  addRow(i18n('memory_trace.detail.field_type'),
    i18n(`memory_trace.node_type.${node.type}`));
  addRow(i18n('memory_trace.detail.field_status'), node.status);
  addRow(i18n('memory_trace.detail.field_entity'), node.entity);
  addRow(i18n('memory_trace.detail.field_created'), node.created_at);
  if (meta.source) addRow(i18n('memory_trace.detail.field_source'), meta.source);
  if (meta.origin) {
    addRow(i18n('memory_trace.detail.field_origin'),
      i18n(`memory_trace.origin.${meta.origin}`));
  }
  host.append(rows);

  if (content) {
    host.append(el('div', { className: 'mtrace-detail-content-label' },
      i18n('memory_trace.detail.field_content')));
    host.append(el('div', { className: 'mtrace-detail-content' }, content));
  }

  // upstream = incoming edges (someone -> node); downstream = outgoing.
  const upstream = [];
  const downstream = [];
  for (const e of snapshot.edges) {
    if (e.target === selectedId) {
      const other = index.get(e.source);
      if (other) upstream.push([other, e]);
    } else if (e.source === selectedId) {
      const other = index.get(e.target);
      if (other) downstream.push([other, e]);
    }
  }

  host.append(el('h4', { className: 'mtrace-detail-subhead' },
    i18n('memory_trace.detail.upstream')));
  if (upstream.length) {
    host.append(el('ul', { className: 'mtrace-link-list' },
      upstream.map(([o, e]) => _linkRow(o, e, onSelect))));
  } else {
    host.append(el('div', { className: 'mtrace-detail-none' },
      i18n('memory_trace.detail.none')));
  }

  host.append(el('h4', { className: 'mtrace-detail-subhead' },
    i18n('memory_trace.detail.downstream')));
  if (downstream.length) {
    host.append(el('ul', { className: 'mtrace-link-list' },
      downstream.map(([o, e]) => _linkRow(o, e, onSelect))));
  } else {
    host.append(el('div', { className: 'mtrace-detail-none' },
      i18n('memory_trace.detail.none')));
  }

  // Tier C reverse attribution (only for fact/reflection/persona memory nodes).
  if (onAttribute && _ATTRIBUTABLE.has(node.type)) {
    const attrBox = el('div', { className: 'mtrace-attr' });
    attrBox.append(el('div', { className: 'mtrace-attr-hint hint' },
      i18n('memory_trace.detail.attribute_hint')));
    const btnRow = el('div', { className: 'mtrace-attr-btns' });
    btnRow.append(el('button', {
      className: 'btn tiny',
      disabled: attributing,
      onClick: () => onAttribute(selectedId, false),
    }, i18n('memory_trace.detail.attribute_btn')));
    btnRow.append(el('button', {
      className: 'btn tiny',
      disabled: attributing,
      onClick: () => onAttribute(selectedId, true),
    }, i18n('memory_trace.detail.attribute_llm_btn')));
    attrBox.append(btnRow);
    if (attributing) {
      attrBox.append(el('div', { className: 'mtrace-attr-status hint' },
        i18n('memory_trace.detail.attributing')));
    } else if (attributionMsg) {
      attrBox.append(el('div', {
        className: 'mtrace-attr-status' + (attributionFallback ? ' fallback' : ''),
      }, attributionMsg));
    }
    host.append(attrBox);
  }
}
