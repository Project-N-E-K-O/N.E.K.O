/* Knowledge-map fallback rendering. Loaded before main.js; function bodies run after bootstrap. */
function knowledgeMapActiveStage() {
  return knowledgeMapStage || normalizeLearningStage(learningProfile.stage) || 'all';
}

function subjectValueFromNode(node = {}) {
  return String(node.subject || node.subject_id || '').trim();
}

function knowledgeSubjectLabel(subject) {
  const normalized = String(subject || '').trim();
  if (!normalized) return t('ui.knowledge.subject_uncategorized', 'Uncategorized subject');
  return t(`ui.knowledge.subject.${normalized}`, normalized.replaceAll('_', ' '));
}

function knowledgeMapActiveSubject(nodes = []) {
  const selected = String(knowledgeMapSubject || '').trim();
  if (!selected) return 'all';
  return nodes.some((node) => subjectValueFromNode(node) === selected) ? selected : 'all';
}

function knowledgeMapRangeLabel(stage = knowledgeMapActiveStage()) {
  return stage === 'all'
    ? t('ui.knowledge.scope_all', 'All stages')
    : knowledgeStageLabel(stage);
}

function knowledgeMapSubjectLabel(subject = 'all') {
  return subject === 'all'
    ? t('ui.knowledge.subject_all', 'All subjects')
    : knowledgeSubjectLabel(subject);
}

function visibleKnowledgeNodes(nodes = [], stage = knowledgeMapActiveStage(), subject = 'all') {
  return nodes.filter((node) => {
    const nodeStage = stageValueFromNode(node);
    const stageVisible = stage === 'all' || nodeStage === stage || !nodeStage;
    const subjectVisible = subject === 'all' || subjectValueFromNode(node) === subject;
    return stageVisible && subjectVisible;
  });
}

function visibleKnowledgeEdges(edges = [], nodes = [], stage = knowledgeMapActiveStage()) {
  void stage;
  const visibleIds = new Set(nodes.map((node) => String(node.id || node.topic_id || '')));
  return edges.filter((edge) => visibleIds.has(String(edge.from || '')) && visibleIds.has(String(edge.to || '')));
}

function renderKnowledgeSubjectSelector(nodes = [], stage = knowledgeMapActiveStage()) {
  const stageNodes = visibleKnowledgeNodes(nodes, stage, 'all');
  const activeSubject = knowledgeMapActiveSubject(stageNodes);
  const root = drawerElement('section', 'knowledge-stage-selector knowledge-subject-selector');
  root.appendChild(drawerElement('span', '', t('ui.knowledge.subject_label', 'Subject')));
  const actions = drawerElement('div', 'knowledge-stage-selector__actions');
  const counts = new Map();
  stageNodes.forEach((node) => {
    const subject = subjectValueFromNode(node);
    counts.set(subject, (counts.get(subject) || 0) + 1);
  });
  const knownSubjects = KNOWLEDGE_SUBJECT_OPTIONS.filter((subject) => counts.has(subject));
  const dynamicSubjects = [...counts.keys()].filter((subject) => subject && !KNOWLEDGE_SUBJECT_OPTIONS.includes(subject));
  const subjects = ['all', ...knownSubjects, ...dynamicSubjects.sort((left, right) => (
    knowledgeSubjectLabel(left).localeCompare(knowledgeSubjectLabel(right))
  ))];
  if (counts.has('')) subjects.push('');
  subjects.forEach((subject) => {
    const label = knowledgeMapSubjectLabel(subject);
    const count = subject === 'all' ? stageNodes.length : (counts.get(subject) || 0);
    const button = drawerElement('button', 'knowledge-stage-option', count ? `${label} ${count}` : label);
    button.type = 'button';
    button.dataset.subject = subject || 'uncategorized';
    button.setAttribute('aria-pressed', subject === activeSubject ? 'true' : 'false');
    button.addEventListener('click', () => {
      knowledgeMapSubject = subject === 'all' ? '' : subject;
      if (surfaceDrawerBody) {
        surfaceDrawerBody.replaceChildren(renderKnowledgePanel(lastKnowledgeMapPayload || lastStatusPayload));
      }
    });
    actions.appendChild(button);
  });
  root.appendChild(actions);
  return root;
}

function renderKnowledgeLoadingPanel(subject = knowledgeMapSubject) {
  const label = subject ? knowledgeSubjectLabel(subject) : t('ui.knowledge.subject_all', 'All subjects');
  const root = surfacePanel('knowledge-map', label);
  root.appendChild(drawerElement('pre', '', tf('ui.knowledge.loading_subject', 'Loading {subject} knowledge map...', { subject: label })));
  return root;
}

function renderKnowledgeStageSelector(nodes = []) {
  const root = drawerElement('section', 'knowledge-stage-selector');
  root.appendChild(drawerElement('span', '', t('ui.knowledge.scope_label', 'Graph range')));
  const actions = drawerElement('div', 'knowledge-stage-selector__actions');
  const stages = [...LEARNING_STAGE_OPTIONS.filter((stage) => stage !== 'custom'), 'all'];
  const activeStage = knowledgeMapActiveStage();
  const counts = new Map();
  nodes.forEach((node) => {
    const stage = stageValueFromNode(node) || '';
    counts.set(stage, (counts.get(stage) || 0) + 1);
  });
  stages.forEach((stage) => {
    const label = stage === 'all' ? t('ui.knowledge.scope_all', 'All stages') : learningStageLabel(stage);
    const count = stage === 'all' ? nodes.length : (counts.get(stage) || 0);
    const button = drawerElement('button', 'knowledge-stage-option', count ? `${label} ${count}` : label);
    button.type = 'button';
    button.dataset.stage = stage;
    button.setAttribute('aria-pressed', stage === activeStage ? 'true' : 'false');
    button.addEventListener('click', () => {
      knowledgeMapStage = stage === normalizeLearningStage(learningProfile.stage) ? '' : stage;
      knowledgeMapSubject = '';
      if (surfaceDrawerBody) {
        const requestId=mapRequestId += 1;
        surfaceDrawerBody.replaceChildren(renderKnowledgeLoadingPanel(knowledgeMapSubject));
        loadKnowledgeMapIntoDrawer('knowledge-map', requestId);
      }
    });
    actions.appendChild(button);
  });
  root.appendChild(actions);
  return root;
}

function knowledgeEdgeMeta(edge = {}) {
  const parts = [
    String(edge.priority || '').trim(),
    String(edge.context || '').trim(),
  ].filter(Boolean);
  if (Number.isFinite(Number(edge.confidence))) {
    parts.push(`${Math.round(Number(edge.confidence) * 100)}%`);
  }
  return parts.join(' / ');
}

function renderKnowledgeNodeDetail(node = {}, edges = [], labelById = new Map()) {
  const detail = drawerElement('article', 'knowledge-node-detail');
  detail.dataset.topicId = String(node.id || node.topic_id || '');
  detail.appendChild(drawerElement('h3', '', knowledgeNodeLabel(node)));
  const facts = [
    subjectValueFromNode(node) ? knowledgeSubjectLabel(subjectValueFromNode(node)) : '',
    String(node.chapter || '').trim(),
    String(node.unit || '').trim(),
  ].filter(Boolean).join(' / ');
  if (facts) detail.appendChild(drawerElement('p', 'knowledge-node-detail__meta', facts));
  const nodeId = String(node.id || node.topic_id || '').trim();
  const relatedEdges = edges.filter((edge) => String(edge.from || '') === nodeId || String(edge.to || '') === nodeId);
  const addSection = (key, fallback, items) => {
    const section = drawerElement('section', 'knowledge-node-detail__section');
    section.appendChild(drawerElement('h4', '', t(key, fallback)));
    const list = drawerElement('ul', 'knowledge-node-detail__list');
    items.slice(0, 4).forEach((item) => list.appendChild(drawerElement('li', '', item)));
    if (!list.childElementCount) {
      list.appendChild(drawerElement('li', '', t('ui.knowledge.node_detail.empty', 'Keep studying this topic to unlock more graph context.')));
    }
    section.appendChild(list);
    detail.appendChild(section);
  };
  addSection('ui.knowledge.node_detail.why', 'Why connected', relatedEdges.map((edge) => {
    const otherId = String(edge.from || '') === nodeId ? String(edge.to || '') : String(edge.from || '');
    const other = labelById.get(otherId) || otherId || '-';
    const relation = knowledgeRelationLabel(edge.relation);
    const reason = String(edge.reason || '').trim();
    const meta = knowledgeEdgeMeta(edge);
    const prefix = meta ? `${relation} (${meta}): ${other}` : `${relation}: ${other}`;
    return reason ? `${prefix} - ${reason}` : prefix;
  }));
  addSection('ui.knowledge.node_detail.next', 'Recommended next step', relatedEdges.filter((edge) => String(edge.from || '') === nodeId && ['application', 'procedure_step', 'extends'].includes(String(edge.relation || '').trim().toLowerCase())).map((edge) => {
    const target = labelById.get(String(edge.to || '')) || String(edge.to || '') || '-';
    return `${knowledgeRelationLabel(edge.relation)}: ${target}`;
  }));
  addSection('ui.knowledge.node_detail.practice', 'Practice type', (Array.isArray(node.question_types) ? node.question_types : []).map((item) => String(item || '').trim()).filter(Boolean));
  addSection('ui.knowledge.node_detail.misconceptions', 'Common misconceptions', (Array.isArray(node.typical_misconceptions) ? node.typical_misconceptions : []).map((item) => String(item || '').trim()).filter(Boolean));
  return detail;
}

function renderKnowledgeNodeDetailDialog(node = {}, edges = [], labelById = new Map(), onClose = () => {}) {
  const dialog = drawerElement('div', 'knowledge-node-detail-dialog');
  dialog.setAttribute('role', 'dialog');
  dialog.setAttribute('aria-modal', 'true');
  dialog.setAttribute('aria-label', knowledgeNodeLabel(node));
  const panel = drawerElement('div', 'knowledge-node-detail-dialog__panel');
  const header = drawerElement('header', 'knowledge-node-detail-dialog__header');
  header.appendChild(drawerElement('strong', '', knowledgeNodeLabel(node)));
  const closeButton = drawerElement('button', 'button button-secondary knowledge-node-detail-dialog__close', t('ui.button.close', 'Close'));
  closeButton.type = 'button';
  closeButton.addEventListener('click', onClose);
  header.appendChild(closeButton);
  panel.append(header, renderKnowledgeNodeDetail(node, edges, labelById));
  dialog.appendChild(panel);
  dialog.addEventListener('click', (event) => {
    if (event.target === dialog) onClose();
  });
  dialog.addEventListener('keydown', (event) => {
    if (event.key === 'Escape') {
      event.preventDefault();
      onClose();
    }
  });
  window.setTimeout(() => closeButton.focus?.(), 0);
  return dialog;
}

function renderKnowledgeNodes(nodes = [], edges = []) {
  const root = drawerElement('div', 'knowledge-stage-groups');
  const labelById = new Map(nodes.map((node) => [String(node.id || node.topic_id || ''), knowledgeNodeLabel(node)]));
  const detailMount = drawerElement('div', 'knowledge-node-detail-mount');
  const groups = new Map();
  nodes.slice(0, 80).forEach((node) => {
    const stage = stageValueFromNode(node);
    groups.set(stage, [...(groups.get(stage) || []), node]);
  });
  const valueLabel = (value, fallback) => {
    const text = String(value || '').trim();
    return text || fallback;
  };
  const pushGrouped = (map, key, node) => {
    map.set(key, [...(map.get(key) || []), node]);
  };
  const collapsibleGroup = (className, label, open = false) => {
    const group = drawerElement('details', className);
    group.open = Boolean(open);
    group.appendChild(drawerElement('summary', 'knowledge-group-summary', label));
    return group;
  };
  const renderNodeButton = (node) => {
    const item = drawerElement('button', 'knowledge-node');
    item.type = 'button';
    item.dataset.mastery = masteryLevelForPanel(node);
    const mastery = Number(node.mastery);
    const masteryText = Number.isFinite(mastery) ? ` ${Math.round(mastery * 100)}%` : '';
    item.textContent = `${node.label || node.name || node.topic_name || node.topic_id || node.id || '-'}${masteryText}`;
    item.title = [
      subjectValueFromNode(node) ? knowledgeSubjectLabel(subjectValueFromNode(node)) : '',
      valueLabel(node.chapter, ''),
      valueLabel(node.unit, ''),
    ].filter(Boolean).join(' / ');
    item.addEventListener('click', () => {
      const close = () => detailMount.replaceChildren();
      detailMount.replaceChildren(renderKnowledgeNodeDetailDialog(node, edges, labelById, close));
    });
    return item;
  };
  const selectedStage = normalizeLearningStage(learningProfile.stage);
  [...groups.entries()].sort(([stageA], [stageB]) => (
    stageA === selectedStage ? -1 : stageB === selectedStage ? 1 : [...LEARNING_STAGE_OPTIONS, ''].indexOf(stageA) - [...LEARNING_STAGE_OPTIONS, ''].indexOf(stageB)
  )).forEach(([stage, items]) => {
    const section = collapsibleGroup('knowledge-stage-group', `${knowledgeStageLabel(stage)} / ${items.length}`, stage === selectedStage || groups.size === 1);
    section.dataset.stage = stage || 'uncategorized';
    if (stage === selectedStage) section.dataset.selected = 'true';
    const subjectGroups = new Map();
    items.forEach((node) => {
      pushGrouped(subjectGroups, subjectValueFromNode(node), node);
    });
    [...subjectGroups.entries()].sort(([left], [right]) => knowledgeSubjectLabel(left).localeCompare(knowledgeSubjectLabel(right))).forEach(([subject, subjectItems]) => {
      const subjectSection = collapsibleGroup('knowledge-subject-group', `${knowledgeSubjectLabel(subject)} / ${subjectItems.length}`, subjectGroups.size === 1);
      const chapterGroups = new Map();
      subjectItems.forEach((node) => {
        pushGrouped(chapterGroups, valueLabel(node.chapter, t('ui.knowledge.chapter_uncategorized', 'Uncategorized chapter')), node);
      });
      [...chapterGroups.entries()].sort(([left], [right]) => left.localeCompare(right)).forEach(([chapter, chapterItems]) => {
        const chapterSection = collapsibleGroup('knowledge-chapter-group', `${chapter} / ${chapterItems.length}`, chapterGroups.size === 1 && chapterItems.length <= 12);
        const unitGroups = new Map();
        chapterItems.forEach((node) => {
          pushGrouped(unitGroups, valueLabel(node.unit, chapter), node);
        });
        [...unitGroups.entries()].sort(([left], [right]) => left.localeCompare(right)).forEach(([unit, unitItems]) => {
          const unitSection = collapsibleGroup('knowledge-unit-group', `${unit} / ${unitItems.length}`, unitGroups.size === 1 && unitItems.length <= 8);
          const list = drawerElement('div', 'study-panel__actions');
          unitItems.slice(0, 24).forEach((node) => {
            list.appendChild(renderNodeButton(node));
          });
          if (unitItems.length > 24) {
            list.appendChild(drawerElement('span', 'knowledge-edge-more', tf('ui.knowledge.edge_more', '+ {count} more', { count: unitItems.length - 24 })));
          }
          unitSection.appendChild(list);
          chapterSection.appendChild(unitSection);
        });
        subjectSection.appendChild(chapterSection);
      });
      section.appendChild(subjectSection);
    });
    root.appendChild(section);
  });
  root.appendChild(detailMount);
  return root;
}
function knowledgeNodeLabel(node) {
  return String(node?.label || node?.name || node?.topic_name || node?.topic_id || node?.id || '-');
}

function knowledgeRelationLabel(relation) {
  const normalized = String(relation || 'related').trim().toLowerCase();
  if (normalized === 'prerequisite') return t('ui.knowledge.edge_relation.prerequisite', 'Prerequisite');
  if (normalized === 'application') return t('ui.knowledge.edge_relation.application', 'Application');
  if (normalized === 'procedure_step') return t('ui.knowledge.edge_relation.procedure_step', 'Procedure Step');
  if (normalized === 'confusable') return t('ui.knowledge.edge_relation.confusable', 'Confusable');
  if (normalized === 'related') return t('ui.knowledge.edge_relation.related', 'Related');
  if (normalized === 'co_occurs') return t('ui.knowledge.edge_relation.co_occurs', 'Co-occurs');
  if (normalized === 'supports') return t('ui.knowledge.edge_relation.supports', 'Supports');
  if (normalized === 'analogy') return t('ui.knowledge.edge_relation.analogy', 'Analogy');
  if (normalized === 'similar') return t('ui.knowledge.edge_relation.similar', 'Similar');
  if (normalized === 'extends') return t('ui.knowledge.edge_relation.extends', 'Extends');
  if (normalized === 'next') return t('ui.knowledge.edge_relation.next', 'Next');
  if (normalized === 'nearby') return t('ui.knowledge.edge_relation.nearby', 'Nearby');
  return normalized || t('ui.knowledge.edge_relation.related', 'Related');
}

function knowledgeEdgeColor(relation) {
  const normalized = String(relation || 'related').trim().toLowerCase();
  if (normalized === 'prerequisite') return '#b7791f';
  if (normalized === 'confusable') return '#c44747';
  if (normalized === 'application') return '#2f7d57';
  if (normalized === 'procedure_step') return '#6d5cc5';
  if (normalized === 'extends' || normalized === 'co_occurs') return '#5f6f82';
  return '#6b8f7b';
}

function renderKnowledgeEdgeGraph(edgeGroups = []) {
  const graphEdges = edgeGroups
    .flatMap((group) => (group.items || []).slice(0, 6).map((item) => ({
      from: String(group.fromId || '').trim(),
      to: String(item.toId || '').trim(),
      fromLabel: String(group.from || '').trim(),
      toLabel: String(item.to || '').trim(),
      relation: String(item.rawRelation || 'related').trim().toLowerCase(),
      label: item.relation,
    })))
    .filter((edge) => edge.from && edge.to)
    .slice(0, 30);
  if (!graphEdges.length) return null;
  const labelById = new Map();
  graphEdges.forEach((edge) => {
    labelById.set(edge.from, edge.fromLabel || edge.from);
    labelById.set(edge.to, edge.toLabel || edge.to);
  });
  const graphIds = [];
  graphEdges.forEach((edge) => {
    if (!graphIds.includes(edge.from)) graphIds.push(edge.from);
    if (!graphIds.includes(edge.to)) graphIds.push(edge.to);
  });
  const shownIds = graphIds.slice(0, 18);
  const shownIdSet = new Set(shownIds);
  const shownEdges = graphEdges.filter((edge) => shownIdSet.has(edge.from) && shownIdSet.has(edge.to));
  const columnCount = shownIds.length > 10 ? 3 : 2;
  const rowCount = Math.max(1, Math.ceil(shownIds.length / columnCount));
  const width = 920;
  const height = Math.max(240, 88 + rowCount * 82);
  const xStep = width / columnCount;
  const positions = new Map();
  shownIds.forEach((id, index) => {
    const column = index % columnCount;
    const row = Math.floor(index / columnCount);
    positions.set(id, {
      x: Math.round(xStep * column + xStep / 2),
      y: 58 + row * 82,
    });
  });
  const svgNs = 'http://www.w3.org/2000/svg';
  const svg = document.createElementNS(svgNs, 'svg');
  svg.setAttribute('class', 'knowledge-edge-graph__svg');
  svg.setAttribute('viewBox', `0 0 ${width} ${height}`);
  svg.setAttribute('role', 'img');
  svg.setAttribute('aria-label', t('ui.knowledge.edge_graph_label', 'Relationship graph'));
  const defs = document.createElementNS(svgNs, 'defs');
  const marker = document.createElementNS(svgNs, 'marker');
  marker.setAttribute('id', 'knowledge-edge-arrow');
  marker.setAttribute('viewBox', '0 0 10 10');
  marker.setAttribute('refX', '9');
  marker.setAttribute('refY', '5');
  marker.setAttribute('markerWidth', '7');
  marker.setAttribute('markerHeight', '7');
  marker.setAttribute('orient', 'auto-start-reverse');
  const markerPath = document.createElementNS(svgNs, 'path');
  markerPath.setAttribute('d', 'M 0 0 L 10 5 L 0 10 z');
  markerPath.setAttribute('fill', 'currentColor');
  marker.appendChild(markerPath);
  defs.appendChild(marker);
  svg.appendChild(defs);
  const edgeLayer = document.createElementNS(svgNs, 'g');
  edgeLayer.setAttribute('class', 'knowledge-edge-graph__edges');
  shownEdges.forEach((edge) => {
    const from = positions.get(edge.from);
    const to = positions.get(edge.to);
    if (!from || !to) return;
    const color = knowledgeEdgeColor(edge.relation);
    const dx = Math.max(70, Math.abs(to.x - from.x) * 0.5);
    const controlX1 = from.x + (to.x >= from.x ? dx : -dx);
    const controlX2 = to.x - (to.x >= from.x ? dx : -dx);
    const path = document.createElementNS(svgNs, 'path');
    path.setAttribute('class', 'knowledge-edge-graph__edge');
    path.setAttribute('data-relation', edge.relation || 'related');
    path.setAttribute('d', `M ${from.x} ${from.y} C ${controlX1} ${from.y}, ${controlX2} ${to.y}, ${to.x} ${to.y}`);
    path.setAttribute('stroke', color);
    path.setAttribute('color', color);
    path.setAttribute('marker-end', 'url(#knowledge-edge-arrow)');
    path.appendChild(document.createElementNS(svgNs, 'title')).textContent = `${labelById.get(edge.from) || edge.from} -> ${labelById.get(edge.to) || edge.to}: ${edge.label}`;
    edgeLayer.appendChild(path);
  });
  svg.appendChild(edgeLayer);
  const nodeLayer = document.createElementNS(svgNs, 'g');
  nodeLayer.setAttribute('class', 'knowledge-edge-graph__nodes');
  shownIds.forEach((id) => {
    const position = positions.get(id);
    if (!position) return;
    const group = document.createElementNS(svgNs, 'g');
    group.setAttribute('class', 'knowledge-edge-graph__node');
    group.setAttribute('transform', `translate(${position.x - 88} ${position.y - 22})`);
    const rect = document.createElementNS(svgNs, 'rect');
    rect.setAttribute('width', '176');
    rect.setAttribute('height', '44');
    rect.setAttribute('rx', '8');
    const textNode = document.createElementNS(svgNs, 'text');
    textNode.setAttribute('x', '88');
    textNode.setAttribute('y', '27');
    textNode.setAttribute('text-anchor', 'middle');
    const label = labelById.get(id) || id;
    textNode.textContent = label.length > 14 ? `${label.slice(0, 13)}...` : label;
    group.appendChild(document.createElementNS(svgNs, 'title')).textContent = label;
    group.append(rect, textNode);
    nodeLayer.appendChild(group);
  });
  svg.appendChild(nodeLayer);
  const graph = drawerElement('div', 'knowledge-edge-graph');
  graph.appendChild(svg);
  return graph;
}

function renderKnowledgeEdges(nodes = [], edges = [], edgeCount = 0, topicCount = 0) {
  const labelById = new Map(nodes.map((node) => [String(node.id || ''), knowledgeNodeLabel(node)]));
  const groups = new Map();
  edges.slice(0, 80).forEach((edge) => {
    const fromId = String(edge.from || '').trim();
    const toId = String(edge.to || '').trim();
    if (!fromId && !toId) return;
    const groupKey = fromId || '-';
    const group = groups.get(groupKey) || {
      from: labelById.get(groupKey) || groupKey,
      fromId: groupKey,
      items: [],
    };
    group.items.push({
      to: labelById.get(toId) || toId || '-',
      toId,
      relation: knowledgeRelationLabel(edge.relation),
      rawRelation: String(edge.relation || 'related').trim().toLowerCase(),
      reason: String(edge.reason || '').trim(),
      priority: String(edge.priority || '').trim(),
      context: String(edge.context || '').trim(),
      confidence: Number.isFinite(Number(edge.confidence)) ? `${Math.round(Number(edge.confidence) * 100)}%` : '',
    });
    groups.set(groupKey, group);
  });

  const root = drawerElement('div', 'knowledge-edge-section');
  if (!groups.size) {
    root.appendChild(drawerElement(
      'pre',
      '',
      topicCount
        ? tf('ui.settings.knowledge.loaded_summary', '{topics} topics and {edges} edges loaded.', { topics: topicCount, edges: edgeCount })
        : t('ui.settings.knowledge.empty_summary', 'Knowledge map has no loaded topics yet.'),
    ));
    return root;
  }
  const graph = renderKnowledgeEdgeGraph(Array.from(groups.values()).slice(0, 12));
  if (graph) root.appendChild(graph);
  const cardList = drawerElement('div', 'knowledge-edge-list');

  Array.from(groups.values()).slice(0, 12).forEach((group) => {
    const card = drawerElement('article', 'knowledge-edge-card');
    card.appendChild(drawerElement('h3', '', group.from));
    const list = drawerElement('div', 'knowledge-edge-card__items');
    group.items.slice(0, 6).forEach((item) => {
      const row = drawerElement('div', 'knowledge-edge-row');
      row.setAttribute('data-relation', item.rawRelation || 'related');
      row.setAttribute('data-priority', item.priority || 'optional');
      row.setAttribute('data-context', item.context || 'review');
      row.appendChild(drawerElement('span', 'knowledge-edge-row__relation', item.relation));
      const target = drawerElement('span', 'knowledge-edge-row__target', item.to);
      if (item.reason) {
        target.title = item.reason;
        target.appendChild(drawerElement('small', 'knowledge-edge-row__reason', item.reason));
      }
      const meta = [item.priority, item.context, item.confidence].filter(Boolean).join(' / ');
      if (meta) {
        target.appendChild(drawerElement('small', 'knowledge-edge-row__meta', meta));
      }
      row.appendChild(target);
      list.appendChild(row);
    });
    if (group.items.length > 6) {
      list.appendChild(drawerElement('span', 'knowledge-edge-more', tf('ui.knowledge.edge_more', '+ {count} more', { count: group.items.length - 6 })));
    }
    card.appendChild(list);
    cardList.appendChild(card);
  });
  if (edges.length > 80 || groups.size > 12) {
    const hidden = Math.max(0, edgeCount - Math.min(edgeCount, 80));
    cardList.appendChild(drawerElement('span', 'knowledge-edge-more', hidden
      ? tf('ui.knowledge.edge_more', '+ {count} more', { count: hidden })
      : t('ui.knowledge.edge_more_groups', 'More relationship groups are hidden.')));
  }
  root.appendChild(cardList);
  return root;
}

function renderKnowledgePanel(payload = null) {
  const data = payload && typeof payload === 'object' ? payload : (lastStatusPayload || {});
  const summary = data.summary || data.knowledge_summary || {};
  const nodes=Array.isArray(data.nodes) ? data.nodes : [];
  const edges=Array.isArray(data.edges) ? data.edges : [];
  const activeStage = knowledgeMapActiveStage();
  const stageNodes = visibleKnowledgeNodes(nodes, activeStage, 'all');
  const activeSubject = knowledgeMapActiveSubject(stageNodes);
  const shownNodes = visibleKnowledgeNodes(nodes, activeStage, activeSubject);
  const shownEdges = visibleKnowledgeEdges(edges, shownNodes, activeStage);
  const topicCount = countFromSummary(summary, ['topic_count', 'topics', 'node_count', 'nodes']) || nodes.length;
  const edgeCount = countFromSummary(summary, ['edge_count', 'edges']) || edges.length;
  const weakTopics = shownNodes.filter((node) => masteryLevelForPanel(node) === 'weak').length;
  const root = surfacePanel('knowledge-map', `${shownNodes.length}/${topicCount}`);
  const state = drawerElement('section', 'study-panel__state');
  appendPanelState(state, t('ui.profile.stage_label', 'Stage'), learningStageLabel());
  appendPanelState(state, t('ui.knowledge.scope_label', 'Graph range'), knowledgeMapRangeLabel(activeStage));
  appendPanelState(state, t('ui.knowledge.subject_label', 'Subject'), knowledgeMapSubjectLabel(activeSubject));
  appendPanelState(state, t('ui.label.topics', 'Topics'), `${shownNodes.length}/${topicCount}`);
  appendPanelState(state, t('ui.label.edges', 'Edges'), `${shownEdges.length}/${edgeCount}`);
  appendPanelState(state, t('ui.label.weak_topics', 'Weak Topics'), String(weakTopics));
  root.appendChild(state);
  root.appendChild(renderKnowledgeStageSelector(nodes));
  root.appendChild(renderKnowledgeSubjectSelector(nodes, activeStage));

  if (shownNodes.length) {
    root.appendChild(renderKnowledgeNodes(shownNodes, shownEdges));
  } else {
    root.appendChild(drawerElement('pre', '', t('ui.knowledge.scope_empty', 'No topics in this graph range yet. Switch to all stages or keep studying to build it.')));
  }
  root.appendChild(drawerElement('div', 'study-panel__reply-label', t('ui.knowledge.edge_section', 'Relationships')));
  root.appendChild(renderKnowledgeEdges(shownNodes, shownEdges, shownEdges.length, shownNodes.length));
  return root;
}
