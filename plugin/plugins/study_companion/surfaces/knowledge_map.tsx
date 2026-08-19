import { useEffect, useRef, useState } from '@neko/plugin-ui';
import type { PluginSurfaceProps } from '@neko/plugin-ui';

import {
  callPlugin,
  ensureBrandCSS,
  postStudySurfaceMessage,
  STUDY_SURFACE_MESSAGE_TYPES,
} from './study_surface_utils';

type KnowledgeNode = {
  id: string;
  label: string;
  stage?: string;
  subject?: string;
  course_family?: string;
  chapter?: string;
  unit?: string;
  mastery?: number;
  level?: string;
  weak?: boolean;
  question_types?: string[];
  typical_misconceptions?: string[];
};

type PracticeScope = {
  schema_version?: number;
  mode?: 'explicit_scope' | 'explicit_topic';
  stage?: string;
  subject?: string;
  course_family?: string;
  chapter?: string;
  unit?: string;
  topic_id?: string;
  scope_key?: string;
  scope_revision?: number;
  display_path?: string[];
};

type KnowledgeEdge = {
  from: string;
  to: string;
  relation?: string;
  reason?: string;
  priority?: string;
  context?: string;
  confidence?: number;
};

const KNOWLEDGE_SUBJECT_OPTIONS = ['math', 'english', 'chinese', 'physics', 'chemistry', 'biology', 'history', 'geography', 'politics', 'computer_science', 'economics'];

function text(props: PluginSurfaceProps, key: string, fallback: string) {
  const value = props.t?.(key);
  return value && value !== key ? value : fallback;
}

function nodeMasteryLevel(node: KnowledgeNode) {
  if (node.weak) {
    return 'weak';
  }
  const mastery = Number(node.mastery);
  if (!Number.isFinite(mastery)) {
    return 'new';
  }
  if (mastery >= 0.85) {
    return 'mastered';
  }
  if (mastery >= 0.6) {
    return 'good';
  }
  if (mastery >= 0.3) {
    return 'progress';
  }
  return 'weak';
}

function nodeLabel(node?: Partial<KnowledgeNode>) {
  return String(node?.label || node?.id || '-');
}

function nodeSubject(node?: Partial<KnowledgeNode>) {
  return String(node?.subject || '').trim();
}

function subjectLabel(props: PluginSurfaceProps, subject: string) {
  const normalized = String(subject || '').trim();
  if (!normalized) return text(props, 'ui.knowledge.subject_uncategorized', 'Uncategorized subject');
  return text(props, `ui.knowledge.subject.${normalized}`, normalized.replace(/_/g, ' '));
}

function stageLabel(props: PluginSurfaceProps, stage: string) {
  const normalized = String(stage || '').trim();
  if (!normalized) return text(props, 'ui.knowledge.stage_uncategorized', 'Uncategorized stage');
  return text(props, `ui.knowledge.stage.${normalized}`, normalized.replace(/_/g, ' '));
}

function valueLabel(value: string) {
  return String(value || '').trim().replace(/_/g, ' ');
}

function uniqueValues(nodes: KnowledgeNode[], field: keyof KnowledgeNode) {
  return Array.from(new Set(nodes.map((node) => String(node[field] || '').trim()).filter(Boolean)))
    .sort((left, right) => left.localeCompare(right));
}

function relationLabel(props: PluginSurfaceProps, relation?: string) {
  const normalized = String(relation || 'related').trim().toLowerCase();
  if (normalized === 'prerequisite') return text(props, 'ui.knowledge.edge_relation.prerequisite', 'Prerequisite');
  if (normalized === 'application') return text(props, 'ui.knowledge.edge_relation.application', 'Application');
  if (normalized === 'procedure_step') return text(props, 'ui.knowledge.edge_relation.procedure_step', 'Procedure Step');
  if (normalized === 'confusable') return text(props, 'ui.knowledge.edge_relation.confusable', 'Confusable');
  if (normalized === 'co_occurs') return text(props, 'ui.knowledge.edge_relation.co_occurs', 'Co-occurs');
  if (normalized === 'supports') return text(props, 'ui.knowledge.edge_relation.supports', 'Supports');
  if (normalized === 'analogy') return text(props, 'ui.knowledge.edge_relation.analogy', 'Analogy');
  if (normalized === 'related') return text(props, 'ui.knowledge.edge_relation.related', 'Related');
  if (normalized === 'similar') return text(props, 'ui.knowledge.edge_relation.similar', 'Similar');
  if (normalized === 'extends') return text(props, 'ui.knowledge.edge_relation.extends', 'Extends');
  if (normalized === 'next') return text(props, 'ui.knowledge.edge_relation.next', 'Next');
  if (normalized === 'nearby') return text(props, 'ui.knowledge.edge_relation.nearby', 'Nearby');
  return normalized || text(props, 'ui.knowledge.edge_relation.related', 'Related');
}

function relationColor(relation?: string) {
  const normalized = String(relation || 'related').trim().toLowerCase();
  if (normalized === 'prerequisite') return '#b7791f';
  if (normalized === 'confusable') return '#c44747';
  if (normalized === 'application') return '#2f7d57';
  if (normalized === 'procedure_step') return '#6d5cc5';
  if (normalized === 'extends' || normalized === 'co_occurs') return '#5f6f82';
  return '#6b8f7b';
}

function edgeGroups(props: PluginSurfaceProps, nodes: KnowledgeNode[], edges: KnowledgeEdge[]) {
  const labels = new Map(nodes.map((node) => [String(node.id || ''), nodeLabel(node)]));
  const groups = new Map<string, { from: string; fromId: string; items: Array<{ relation: string; rawRelation: string; to: string; toId: string; reason: string; priority: string; context: string; confidence: string }> }>();
  edges.slice(0, 80).forEach((edge) => {
    const fromId = String(edge.from || '').trim();
    const toId = String(edge.to || '').trim();
    if (!fromId && !toId) return;
    const key = fromId || '-';
    const group = groups.get(key) || { from: labels.get(key) || key, fromId: key, items: [] };
    const rawRelation = String(edge.relation || 'related').trim().toLowerCase();
    group.items.push({
      relation: relationLabel(props, edge.relation),
      rawRelation,
      to: labels.get(toId) || toId || '-',
      toId,
      reason: String(edge.reason || '').trim(),
      priority: String(edge.priority || '').trim(),
      context: String(edge.context || '').trim(),
      confidence: Number.isFinite(Number(edge.confidence)) ? `${Math.round(Number(edge.confidence) * 100)}%` : '',
    });
    groups.set(key, group);
  });
  return Array.from(groups.values());
}

function edgeGraph(props: PluginSurfaceProps, nodes: KnowledgeNode[], edges: KnowledgeEdge[]) {
  const labels = new Map(nodes.map((node) => [String(node.id || ''), nodeLabel(node)]));
  const graphEdges = edgeGroups(props, nodes, edges)
    .slice(0, 12)
    .flatMap((group) => group.items.slice(0, 6).map((item) => ({
      from: String(group.fromId || '').trim(),
      to: String(item.toId || '').trim(),
      relation: String(item.rawRelation || 'related').trim().toLowerCase(),
      label: item.relation,
    })))
    .filter((edge) => edge.from && edge.to)
    .slice(0, 30);
  if (!graphEdges.length) return null;
  const graphIds: string[] = [];
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
  const positions = new Map<string, { x: number; y: number }>();
  shownIds.forEach((id, index) => {
    const column = index % columnCount;
    const row = Math.floor(index / columnCount);
    positions.set(id, {
      x: Math.round(xStep * column + xStep / 2),
      y: 58 + row * 82,
    });
  });
  return (
    <div className="knowledge-edge-graph">
      <svg className="knowledge-edge-graph__svg" viewBox={`0 0 ${width} ${height}`} role="img" aria-label={text(props, 'ui.knowledge.edge_graph_label', 'Relationship graph')}>
        <defs>
          <marker id="knowledge-edge-arrow-surface" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse">
            <path d="M 0 0 L 10 5 L 0 10 z" fill="currentColor" />
          </marker>
        </defs>
        <g className="knowledge-edge-graph__edges">
          {shownEdges.map((edge, index) => {
            const from = positions.get(edge.from);
            const to = positions.get(edge.to);
            if (!from || !to) return null;
            const dx = Math.max(70, Math.abs(to.x - from.x) * 0.5);
            const controlX1 = from.x + (to.x >= from.x ? dx : -dx);
            const controlX2 = to.x - (to.x >= from.x ? dx : -dx);
            const color = relationColor(edge.relation);
            return (
              <path
                key={`${edge.from}:${edge.to}:${edge.relation}:${index}`}
                className="knowledge-edge-graph__edge"
                data-relation={edge.relation || 'related'}
                d={`M ${from.x} ${from.y} C ${controlX1} ${from.y}, ${controlX2} ${to.y}, ${to.x} ${to.y}`}
                stroke={color}
                color={color}
                markerEnd="url(#knowledge-edge-arrow-surface)"
              >
                <title>{labels.get(edge.from) || edge.from} -&gt; {labels.get(edge.to) || edge.to}: {edge.label}</title>
              </path>
            );
          })}
        </g>
        <g className="knowledge-edge-graph__nodes">
          {shownIds.map((id) => {
            const position = positions.get(id);
            if (!position) return null;
            const label = labels.get(id) || id;
            return (
              <g key={id} className="knowledge-edge-graph__node" transform={`translate(${position.x - 88} ${position.y - 22})`}>
                <title>{label}</title>
                <rect width="176" height="44" rx="8" />
                <text x="88" y="27" textAnchor="middle">{label.length > 14 ? `${label.slice(0, 13)}...` : label}</text>
              </g>
            );
          })}
        </g>
      </svg>
    </div>
  );
}

export default function KnowledgeMap(props: PluginSurfaceProps) {
  const [nodes, setNodes] = useState<KnowledgeNode[]>([]);
  const [edges, setEdges] = useState<KnowledgeEdge[]>([]);
  const [selectedNode, setSelectedNode] = useState<KnowledgeNode | null>(null);
  const [selectedStage, setSelectedStage] = useState('all');
  const [selectedSubject, setSelectedSubject] = useState('all');
  const [selectedChapter, setSelectedChapter] = useState('all');
  const [selectedUnit, setSelectedUnit] = useState('all');
  const [canonicalScope, setCanonicalScope] = useState<PracticeScope | null>(null);
  const [scopeRecoveryFailed, setScopeRecoveryFailed] = useState(false);
  const [scopeBusy, setScopeBusy] = useState(false);
  const [summary, setSummary] = useState<Record<string, number>>({});
  const [error, setError] = useState('');
  const [isLoading, setIsLoading] = useState(false);
  const dialogRef = useRef<HTMLDivElement | null>(null);
  const closeButtonRef = useRef<HTMLButtonElement | null>(null);
  const nodeTriggerRef = useRef<HTMLButtonElement | null>(null);

  function closeNodeDetail() {
    setSelectedNode(null);
    window.setTimeout(() => {
      if (nodeTriggerRef.current?.isConnected) nodeTriggerRef.current?.focus();
    }, 0);
  }

  useEffect(() => {
    ensureBrandCSS();
    let mounted = true;
    setIsLoading(true);
    callPlugin(props.api, 'study_knowledge_map', { limit: 1000 })
      .then((payload: any) => {
        if (!mounted) {
          return;
        }
        const nextNodes = Array.isArray(payload.nodes) ? payload.nodes : [];
        setNodes(nextNodes);
        setEdges(Array.isArray(payload.edges) ? payload.edges : []);
        setSummary(payload.summary || {});
        setSelectedNode(null);
        setError('');
      })
      .catch((err) => mounted && setError(err instanceof Error ? err.message : String(err)))
      .finally(() => mounted && setIsLoading(false));
    callPlugin(props.api, 'study_get_practice_scope')
      .then((scopePayload: any) => {
        if (!mounted) return;
        const nextScope = scopePayload?.scope && typeof scopePayload.scope === 'object'
          ? scopePayload.scope as PracticeScope
          : null;
        setCanonicalScope(nextScope?.display_path?.length ? nextScope : null);
        setScopeRecoveryFailed(false);
      })
      .catch(() => {
        if (!mounted) return;
        setCanonicalScope(null);
        setScopeRecoveryFailed(true);
      });
    return () => {
      mounted = false;
    };
  }, [props.api]);

  useEffect(() => {
    if (!selectedNode) return undefined;
    closeButtonRef.current?.focus();
    const closeNodeDialog = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        event.preventDefault();
        event.stopPropagation();
        closeNodeDetail();
        return;
      }
      if (event.key === 'Tab') {
        const focusableElements = Array.from(dialogRef.current?.querySelectorAll<HTMLElement>('button, [href], input, select, textarea, [tabindex]:not([tabindex="-1"])') || []);
        const first = focusableElements[0];
        const last = focusableElements[focusableElements.length - 1];
        if (!first || !last) {
          event.preventDefault();
        } else if (event.shiftKey && document.activeElement === first) {
          event.preventDefault();
          last.focus();
        } else if (!event.shiftKey && document.activeElement === last) {
          event.preventDefault();
          first.focus();
        }
      }
    };
    document.addEventListener('keydown', closeNodeDialog);
    return () => document.removeEventListener('keydown', closeNodeDialog);
  }, [selectedNode]);

  async function activatePracticeScope(mode: 'explicit_scope' | 'explicit_topic') {
    if (scopeBusy) return;
    const topicNode = mode === 'explicit_topic' ? selectedNode : null;
    const requestedStage = topicNode
      ? String(topicNode.stage || '').trim()
      : selectedStage;
    const requestedModule = topicNode
      ? (requestedStage === 'college'
        ? String(topicNode.course_family || '').trim()
        : String(topicNode.subject || '').trim())
      : selectedSubject;
    if (!requestedStage || requestedStage === 'all' || !requestedModule || requestedModule === 'all') return;
    const scopeNode = topicNode || nodes.find((node) => {
      if (String(node.stage || '').trim() !== requestedStage) return false;
      const module = requestedStage === 'college'
        ? String(node.course_family || '').trim()
        : String(node.subject || '').trim();
      return module === requestedModule;
    });
    if (!scopeNode || (mode === 'explicit_topic' && !topicNode)) return;
    const scope: Record<string, string | number> = {
      schema_version: 1,
      mode,
      stage: requestedStage,
      subject: String(scopeNode.subject || '').trim(),
    };
    if (requestedStage === 'college') scope.course_family = requestedModule;
    const requestedChapter = topicNode
      ? String(topicNode.chapter || '').trim()
      : (selectedChapter === 'all' ? '' : selectedChapter);
    const requestedUnit = topicNode
      ? String(topicNode.unit || '').trim()
      : (selectedUnit === 'all' ? '' : selectedUnit);
    if (requestedChapter) scope.chapter = requestedChapter;
    if (requestedUnit) scope.unit = requestedUnit;
    if (topicNode) scope.topic_id = topicNode.id;

    setScopeBusy(true);
    try {
      const payload = await callPlugin(props.api, 'study_set_practice_scope', { scope }) as {
        scope?: PracticeScope;
        scope_revision?: number;
      };
      const nextScope = payload?.scope && typeof payload.scope === 'object' ? payload.scope : {};
      setCanonicalScope(nextScope);
      setScopeRecoveryFailed(false);
      setError('');
      const rawRevision = payload?.scope_revision ?? nextScope.scope_revision ?? 0;
      const activationRevision = typeof rawRevision === 'number'
        && Number.isSafeInteger(rawRevision)
        && rawRevision >= 0
        ? rawRevision
        : 0;
      postStudySurfaceMessage({
        type: STUDY_SURFACE_MESSAGE_TYPES.openSurface,
        payload: {
          surfaceId: 'study-panel',
          activationRevision,
        },
      }, props.host?.origin);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setScopeBusy(false);
    }
  }

  async function clearPracticeScope() {
    if (scopeBusy) return;
    setScopeBusy(true);
    try {
      await callPlugin(props.api, 'study_clear_practice_scope');
      setCanonicalScope(null);
      setScopeRecoveryFailed(false);
      setError('');
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setScopeBusy(false);
    }
  }

  const subjectCounts = new Map<string, number>();
  nodes.forEach((node) => {
    const subject = nodeSubject(node);
    subjectCounts.set(subject, (subjectCounts.get(subject) || 0) + 1);
  });
  const knownSubjects = KNOWLEDGE_SUBJECT_OPTIONS.filter((subject) => subjectCounts.has(subject));
  const dynamicSubjects = Array.from(subjectCounts.keys()).filter((subject) => subject && !KNOWLEDGE_SUBJECT_OPTIONS.includes(subject));
  const subjects = ['all', ...knownSubjects, ...dynamicSubjects.sort((left, right) => (
    subjectLabel(props, left).localeCompare(subjectLabel(props, right))
  ))];
  if (subjectCounts.has('')) {
    subjects.push('');
  }
  const stages = uniqueValues(nodes, 'stage');
  const activeStage = selectedStage !== 'all' && stages.includes(selectedStage)
    ? selectedStage
    : 'all';
  const stageNodes = activeStage === 'all'
    ? nodes
    : nodes.filter((node) => String(node.stage || '').trim() === activeStage);
  const moduleField: keyof KnowledgeNode = activeStage === 'college' ? 'course_family' : 'subject';
  const modules = uniqueValues(stageNodes, moduleField);
  const moduleCounts = new Map<string, number>();
  stageNodes.forEach((node) => {
    const module = String(node[moduleField] || '').trim();
    if (module) moduleCounts.set(module, (moduleCounts.get(module) || 0) + 1);
  });
  const selectableModules = activeStage === 'college'
    ? modules
    : subjects.filter((subject) => subject !== 'all' && moduleCounts.has(subject));
  const activeSubject = selectedSubject !== 'all' && moduleCounts.has(selectedSubject)
    ? selectedSubject
    : 'all';
  const moduleNodes = activeSubject === 'all'
    ? stageNodes
    : stageNodes.filter((node) => String(node[moduleField] || '').trim() === activeSubject);
  const chapters = uniqueValues(moduleNodes, 'chapter');
  const chapterCounts = new Map(chapters.map((chapter) => [
    chapter,
    moduleNodes.filter((node) => String(node.chapter || '').trim() === chapter).length,
  ]));
  const activeChapter = selectedChapter !== 'all' && chapters.includes(selectedChapter)
    ? selectedChapter
    : 'all';
  const chapterNodes = activeChapter === 'all'
    ? moduleNodes
    : moduleNodes.filter((node) => String(node.chapter || '').trim() === activeChapter);
  const units = uniqueValues(chapterNodes, 'unit');
  const unitCounts = new Map(units.map((unit) => [
    unit,
    chapterNodes.filter((node) => String(node.unit || '').trim() === unit).length,
  ]));
  const activeUnit = selectedUnit !== 'all' && units.includes(selectedUnit)
    ? selectedUnit
    : 'all';
  const visibleNodes = activeUnit === 'all'
    ? chapterNodes
    : chapterNodes.filter((node) => String(node.unit || '').trim() === activeUnit);
  const visibleIds = new Set(visibleNodes.map((node) => String(node.id || '')));
  const visibleEdges = edges.filter((edge) => (
    visibleIds.has(String(edge.from || '')) && visibleIds.has(String(edge.to || ''))
  ));
  const currentNode = selectedNode && visibleIds.has(String(selectedNode.id || ''))
    ? selectedNode
    : null;
  const activeSubjectLabel = activeSubject === 'all'
    ? text(props, 'ui.knowledge.module_all', 'All course modules')
    : activeStage === 'college'
      ? valueLabel(activeSubject)
      : subjectLabel(props, activeSubject);
  const loadingSubjectText = text(props, 'ui.knowledge.loading_subject', 'Loading {subject} knowledge map...')
    .replace('{subject}', activeSubjectLabel);
  const emptyDetailItem = text(props, 'ui.knowledge.node_detail.empty', 'Keep studying this topic to unlock more graph context.');

  return (
    <div className="study-panel surface-shell">
      <header className="study-panel__header">
        <div>
          <h1>{text(props, 'ui.surface.knowledge_map', 'Knowledge Map')}</h1>
          <span>{summary.topic_count || nodes.length} / {summary.weak_topic_count || 0}</span>
        </div>
      </header>
      {error ? <pre>{error}</pre> : null}
      <section className="study-panel__state">
        <div>
          <span>{text(props, 'ui.label.topics', 'Topics')}</span>
          <strong>{visibleNodes.length} / {summary.topic_count || nodes.length}</strong>
        </div>
        <div>
          <span>{text(props, 'ui.label.edges', 'Edges')}</span>
          <strong>{visibleEdges.length} / {summary.edge_count || edges.length}</strong>
        </div>
        <div>
          <span>{text(props, 'ui.label.weak_topics', 'Weak Topics')}</span>
          <strong>{visibleNodes.filter((node) => nodeMasteryLevel(node) === 'weak').length} / {summary.weak_topic_count || 0}</strong>
        </div>
        <div>
          <span>{text(props, 'ui.knowledge.module_label', 'Course module')}</span>
          <strong>{activeSubjectLabel}</strong>
        </div>
      </section>
      {canonicalScope?.display_path?.length || scopeRecoveryFailed ? (
        <section className="study-panel__state" aria-live="polite">
          {canonicalScope?.display_path?.length ? <div>
            <span>{text(props, 'ui.practice.scope_label', 'Practice scope')}</span>
            <strong>{canonicalScope.display_path.join(' / ')}</strong>
          </div> : null}
          <button type="button" disabled={scopeBusy} onClick={() => void clearPracticeScope()}>
            {text(props, 'ui.button.clear_practice_scope', 'Clear scope')}
          </button>
        </section>
      ) : null}
      <section className="knowledge-stage-selector">
        <span>{text(props, 'ui.knowledge.stage_label', 'Learning stage')}</span>
        <div className="knowledge-stage-selector__actions">
          {['all', ...stages].map((stage) => {
            const label = stage === 'all'
              ? text(props, 'ui.knowledge.scope_all', 'All stages')
              : stageLabel(props, stage);
            const count = stage === 'all'
              ? nodes.length
              : nodes.filter((node) => String(node.stage || '').trim() === stage).length;
            return (
              <button
                key={stage || 'uncategorized'}
                type="button"
                className="knowledge-stage-option"
                data-stage={stage || 'uncategorized'}
                aria-pressed={stage === activeStage ? 'true' : 'false'}
                onClick={() => {
                  setSelectedStage(stage);
                  setSelectedSubject('all');
                  setSelectedChapter('all');
                  setSelectedUnit('all');
                  setSelectedNode(null);
                }}
              >
                {count ? `${label} ${count}` : label}
              </button>
            );
          })}
        </div>
      </section>
      {activeStage !== 'all' ? (
        <section className="knowledge-stage-selector knowledge-subject-selector">
          <span>{text(props, 'ui.knowledge.module_label', 'Course module')}</span>
          <div className="knowledge-stage-selector__actions">
            {selectableModules.map((module) => {
              const label = activeStage === 'college' ? valueLabel(module) : subjectLabel(props, module);
              const count = moduleCounts.get(module) || 0;
              return (
                <button
                  key={module}
                  type="button"
                  className="knowledge-stage-option"
                  data-module={module}
                  aria-pressed={module === activeSubject ? 'true' : 'false'}
                  onClick={() => {
                    setSelectedSubject(module);
                    setSelectedChapter('all');
                    setSelectedUnit('all');
                    setSelectedNode(null);
                  }}
                >
                  {`${label} ${count}`}
                </button>
              );
            })}
          </div>
        </section>
      ) : null}
      {activeSubject !== 'all' ? (
        <section className="knowledge-stage-selector knowledge-chapter-selector">
          <span>{text(props, 'ui.knowledge.chapter_label', 'Chapter')}</span>
          <div className="knowledge-stage-selector__actions">
            <button
              type="button"
              className="knowledge-stage-option"
              aria-pressed={activeChapter === 'all' ? 'true' : 'false'}
              onClick={() => {
                setSelectedChapter('all');
                setSelectedUnit('all');
                setSelectedNode(null);
              }}
            >
              {text(props, 'ui.knowledge.chapter_all', 'All chapters')}
            </button>
            {chapters.map((chapter) => (
              <button
                key={chapter}
                type="button"
                className="knowledge-stage-option"
                data-chapter={chapter}
                aria-pressed={chapter === activeChapter ? 'true' : 'false'}
                onClick={() => {
                  setSelectedChapter(chapter);
                  setSelectedUnit('all');
                  setSelectedNode(null);
                }}
              >
                {`${chapter} ${chapterCounts.get(chapter) || 0}`}
              </button>
            ))}
          </div>
        </section>
      ) : null}
      {activeChapter !== 'all' ? (
        <section className="knowledge-stage-selector knowledge-unit-selector">
          <span>{text(props, 'ui.knowledge.unit_label', 'Unit')}</span>
          <div className="knowledge-stage-selector__actions">
            <button
              type="button"
              className="knowledge-stage-option"
              aria-pressed={activeUnit === 'all' ? 'true' : 'false'}
              onClick={() => {
                setSelectedUnit('all');
                setSelectedNode(null);
              }}
            >
              {text(props, 'ui.knowledge.unit_all', 'All units')}
            </button>
            {units.map((unit) => (
              <button
                key={unit}
                type="button"
                className="knowledge-stage-option"
                data-unit={unit}
                aria-pressed={unit === activeUnit ? 'true' : 'false'}
                onClick={() => {
                  setSelectedUnit(unit);
                  setSelectedNode(null);
                }}
              >
                {`${unit} ${unitCounts.get(unit) || 0}`}
              </button>
            ))}
          </div>
        </section>
      ) : null}
      {isLoading ? (
        <pre>{loadingSubjectText}</pre>
      ) : null}
      {!isLoading ? <div className="study-panel__actions">
        {visibleNodes.slice(0, 60).map((node) => {
          const mastery = Number(node.mastery);
          const masteryText = Number.isFinite(mastery) ? ` ${Math.round(mastery * 100)}%` : '';
          return (
            <button
              key={node.id}
              type="button"
              className="knowledge-node"
              data-mastery={nodeMasteryLevel(node)}
              aria-pressed={currentNode?.id === node.id ? 'true' : 'false'}
              onClick={(event: any) => {
                nodeTriggerRef.current = event.currentTarget;
                setSelectedNode(node);
              }}
            >
              {node.label}
              {masteryText}
            </button>
          );
        })}
        {visibleNodes.length > 60 ? (
          <span className="knowledge-edge-more">+ {visibleNodes.length - 60} {text(props, 'ui.knowledge.edge_more_suffix', 'more')}</span>
        ) : null}
      </div> : null}
      <div className="study-panel__reply-label">{text(props, 'ui.knowledge.edge_section', 'Relationships')}</div>
      {!isLoading && visibleEdges.length ? edgeGraph(props, visibleNodes, visibleEdges) : null}
      <div className="knowledge-edge-list">
        {!isLoading ? edgeGroups(props, visibleNodes, visibleEdges).slice(0, 12).map((group) => (
          <article key={group.fromId} className="knowledge-edge-card">
            <h3>{group.from}</h3>
            <div className="knowledge-edge-card__items">
              {group.items.slice(0, 6).map((item, index) => (
                <div
                  key={`${item.rawRelation}:${item.to}:${index}`}
                  className="knowledge-edge-row"
                  data-relation={item.rawRelation || 'related'}
                  data-priority={item.priority || 'optional'}
                  data-context={item.context || 'review'}
                >
                  <span className="knowledge-edge-row__relation">{item.relation}</span>
                  <span className="knowledge-edge-row__target">
                    {item.to}
                    {item.reason ? <small className="knowledge-edge-row__reason">{item.reason}</small> : null}
                    {item.priority || item.context || item.confidence ? (
                      <small className="knowledge-edge-row__meta">
                        {[item.priority, item.context, item.confidence].filter(Boolean).join(' / ')}
                      </small>
                    ) : null}
                  </span>
                </div>
              ))}
              {group.items.length > 6 ? (
                <span className="knowledge-edge-more">+ {group.items.length - 6} {text(props, 'ui.knowledge.edge_more_suffix', 'more')}</span>
              ) : null}
            </div>
          </article>
        )) : null}
        {!isLoading && !visibleEdges.length ? (
          <pre>{summary.topic_count || nodes.length
            ? text(props, 'ui.knowledge.edge_empty', 'No relationships to show yet.')
            : text(props, 'ui.settings.knowledge.empty_summary', 'Knowledge map has no loaded topics yet.')}</pre>
        ) : null}
      </div>
      {!isLoading && currentNode ? (
        <div
          ref={dialogRef}
          className="knowledge-node-detail-dialog"
          role="dialog"
          aria-modal="true"
          aria-label={nodeLabel(currentNode)}
          onClick={(event: any) => {
            if (event.target === event.currentTarget) closeNodeDetail();
          }}
        >
          <div className="knowledge-node-detail-dialog__panel">
            <header className="knowledge-node-detail-dialog__header">
              <strong>{nodeLabel(currentNode)}</strong>
              <button ref={closeButtonRef} type="button" className="button button-secondary knowledge-node-detail-dialog__close" onClick={closeNodeDetail}>
                {text(props, 'ui.button.close', 'Close')}
              </button>
            </header>
            <article className="knowledge-node-detail">
              <h3>{nodeLabel(currentNode)}</h3>
              <p className="knowledge-node-detail__meta">
                {[currentNode.subject ? subjectLabel(props, currentNode.subject) : '', currentNode.chapter, currentNode.unit].filter(Boolean).join(' / ')}
              </p>
              <section className="knowledge-node-detail__section">
                <h4>{text(props, 'ui.knowledge.node_detail.why', 'Why connected')}</h4>
                <ul className="knowledge-node-detail__list">
                  {visibleEdges
                    .filter((edge) => edge.from === currentNode.id || edge.to === currentNode.id)
                    .slice(0, 4)
                    .map((edge, index) => {
                      const otherId = edge.from === currentNode.id ? edge.to : edge.from;
                      const otherNode = visibleNodes.find((node) => node.id === otherId);
                      return (
                        <li key={`${edge.from}:${edge.to}:${index}`}>
                          {relationLabel(props, edge.relation)}: {nodeLabel(otherNode || { id: otherId })}{edge.reason ? ` - ${edge.reason}` : ''}
                        </li>
                      );
                    })}
                  {!visibleEdges.some((edge) => edge.from === currentNode.id || edge.to === currentNode.id) ? <li>{emptyDetailItem}</li> : null}
                </ul>
              </section>
              <section className="knowledge-node-detail__section">
                <h4>{text(props, 'ui.knowledge.node_detail.next', 'Recommended next step')}</h4>
                <ul className="knowledge-node-detail__list">
                  {visibleEdges
                    .filter((edge) => edge.from === currentNode.id && ['application', 'procedure_step', 'extends'].includes(String(edge.relation || '').trim().toLowerCase()))
                    .slice(0, 3)
                    .map((edge, index) => {
                      const target = visibleNodes.find((node) => node.id === edge.to);
                      return <li key={`${edge.to}:${index}`}>{relationLabel(props, edge.relation)}: {nodeLabel(target || { id: edge.to })}</li>;
                    })}
                  {!visibleEdges.some((edge) => edge.from === currentNode.id && ['application', 'procedure_step', 'extends'].includes(String(edge.relation || '').trim().toLowerCase())) ? <li>{emptyDetailItem}</li> : null}
                </ul>
              </section>
              <section className="knowledge-node-detail__section">
                <h4>{text(props, 'ui.knowledge.node_detail.practice', 'Practice type')}</h4>
                <ul className="knowledge-node-detail__list">
                  {(currentNode.question_types || []).slice(0, 3).map((item) => <li key={item}>{item}</li>)}
                  {!(currentNode.question_types || []).length ? <li>{emptyDetailItem}</li> : null}
                </ul>
              </section>
              <section className="knowledge-node-detail__section">
                <h4>{text(props, 'ui.knowledge.node_detail.misconceptions', 'Common misconceptions')}</h4>
                <ul className="knowledge-node-detail__list">
                  {(currentNode.typical_misconceptions || []).slice(0, 3).map((item) => <li key={item}>{item}</li>)}
                  {!(currentNode.typical_misconceptions || []).length ? <li>{emptyDetailItem}</li> : null}
                </ul>
              </section>
              <div className="study-panel__actions">
                <button
                  type="button"
                  disabled={scopeBusy}
                  onClick={() => void activatePracticeScope('explicit_topic')}
                >
                  {scopeBusy
                    ? text(props, 'ui.practice.scope_setting', 'Setting practice scope...')
                    : text(props, 'ui.knowledge.practice_topic', 'Practice this topic')}
                </button>
                <button
                  type="button"
                  disabled={scopeBusy || activeStage === 'all' || activeSubject === 'all'}
                  onClick={() => void activatePracticeScope('explicit_scope')}
                >
                  {text(props, 'ui.knowledge.practice_current_scope', 'Start practice in current scope')}
                </button>
              </div>
            </article>
          </div>
        </div>
      ) : null}
    </div>
  );
}
