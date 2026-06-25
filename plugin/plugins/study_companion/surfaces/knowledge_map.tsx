import { useEffect, useState } from '@neko/plugin-ui';
import type { PluginSurfaceProps } from '@neko/plugin-ui';

import { callPlugin, ensureBrandCSS } from './study_surface_utils';

type KnowledgeNode = {
  id: string;
  label: string;
  subject?: string;
  chapter?: string;
  unit?: string;
  mastery?: number;
  level?: string;
  weak?: boolean;
  question_types?: string[];
  typical_misconceptions?: string[];
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

function edgeGroups(props: PluginSurfaceProps, nodes: KnowledgeNode[], edges: KnowledgeEdge[]) {
  const labels = new Map(nodes.map((node) => [String(node.id || ''), nodeLabel(node)]));
  const groups = new Map<string, { from: string; fromId: string; items: Array<{ relation: string; rawRelation: string; to: string; reason: string; priority: string; context: string; confidence: string }> }>();
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
      reason: String(edge.reason || '').trim(),
      priority: String(edge.priority || '').trim(),
      context: String(edge.context || '').trim(),
      confidence: Number.isFinite(Number(edge.confidence)) ? `${Math.round(Number(edge.confidence) * 100)}%` : '',
    });
    groups.set(key, group);
  });
  return Array.from(groups.values());
}

export default function KnowledgeMap(props: PluginSurfaceProps) {
  const [nodes, setNodes] = useState<KnowledgeNode[]>([]);
  const [edges, setEdges] = useState<KnowledgeEdge[]>([]);
  const [selectedNode, setSelectedNode] = useState<KnowledgeNode | null>(null);
  const [summary, setSummary] = useState<Record<string, number>>({});
  const [error, setError] = useState('');

  useEffect(() => {
    ensureBrandCSS();
    let mounted = true;
    callPlugin(props.api, 'study_knowledge_map', { limit: 200 })
      .then((payload: any) => {
        if (!mounted) {
          return;
        }
        const nextNodes = Array.isArray(payload.nodes) ? payload.nodes : [];
        setNodes(nextNodes);
        setEdges(Array.isArray(payload.edges) ? payload.edges : []);
        setSummary(payload.summary || {});
        setSelectedNode(nextNodes[0] || null);
      })
      .catch((err) => mounted && setError(err instanceof Error ? err.message : String(err)));
    return () => {
      mounted = false;
    };
  }, []);

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
          <strong>{summary.topic_count || nodes.length}</strong>
        </div>
        <div>
          <span>{text(props, 'ui.label.edges', 'Edges')}</span>
          <strong>{summary.edge_count || edges.length}</strong>
        </div>
        <div>
          <span>{text(props, 'ui.label.weak_topics', 'Weak Topics')}</span>
          <strong>{summary.weak_topic_count || 0}</strong>
        </div>
      </section>
      {selectedNode ? (
        <article className="knowledge-node-detail">
          <h3>{nodeLabel(selectedNode)}</h3>
          <p className="knowledge-node-detail__meta">
            {[selectedNode.subject, selectedNode.chapter, selectedNode.unit].filter(Boolean).join(' / ')}
          </p>
          <section className="knowledge-node-detail__section">
            <h4>{text(props, 'ui.knowledge.node_detail.why', 'Why connected')}</h4>
            <ul className="knowledge-node-detail__list">
              {edges
                .filter((edge) => edge.from === selectedNode.id || edge.to === selectedNode.id)
                .slice(0, 4)
                .map((edge, index) => {
                  const otherId = edge.from === selectedNode.id ? edge.to : edge.from;
                  const otherNode = nodes.find((node) => node.id === otherId);
                  return (
                    <li key={`${edge.from}:${edge.to}:${index}`}>
                      {relationLabel(props, edge.relation)}: {nodeLabel(otherNode || { id: otherId })}{edge.reason ? ` - ${edge.reason}` : ''}
                    </li>
                  );
                })}
            </ul>
          </section>
          <section className="knowledge-node-detail__section">
            <h4>{text(props, 'ui.knowledge.node_detail.next', 'Recommended next step')}</h4>
            <ul className="knowledge-node-detail__list">
              {edges
                .filter((edge) => edge.from === selectedNode.id && ['application', 'procedure_step', 'extends'].includes(String(edge.relation || '').toLowerCase()))
                .slice(0, 3)
                .map((edge, index) => {
                  const target = nodes.find((node) => node.id === edge.to);
                  return <li key={`${edge.to}:${index}`}>{relationLabel(props, edge.relation)}: {nodeLabel(target || { id: edge.to })}</li>;
                })}
            </ul>
          </section>
          <section className="knowledge-node-detail__section">
            <h4>{text(props, 'ui.knowledge.node_detail.practice', 'Practice type')}</h4>
            <ul className="knowledge-node-detail__list">
              {(selectedNode.question_types || []).slice(0, 3).map((item) => <li key={item}>{item}</li>)}
            </ul>
          </section>
          <section className="knowledge-node-detail__section">
            <h4>{text(props, 'ui.knowledge.node_detail.misconceptions', 'Common misconceptions')}</h4>
            <ul className="knowledge-node-detail__list">
              {(selectedNode.typical_misconceptions || []).slice(0, 3).map((item) => <li key={item}>{item}</li>)}
            </ul>
          </section>
        </article>
      ) : null}
      <div className="study-panel__actions">
        {nodes.slice(0, 60).map((node) => {
          const mastery = Number(node.mastery);
          const masteryText = Number.isFinite(mastery) ? ` ${Math.round(mastery * 100)}%` : '';
          return (
            <button
              key={node.id}
              type="button"
              className="knowledge-node"
              data-mastery={nodeMasteryLevel(node)}
              aria-pressed={selectedNode?.id === node.id ? 'true' : 'false'}
              onClick={() => setSelectedNode(node)}
            >
              {node.label}
              {masteryText}
            </button>
          );
        })}
      </div>
      <div className="study-panel__reply-label">{text(props, 'ui.knowledge.edge_section', 'Relationships')}</div>
      <div className="knowledge-edge-list">
        {edgeGroups(props, nodes, edges).slice(0, 12).map((group) => (
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
        ))}
        {!edges.length ? (
          <pre>{summary.topic_count || nodes.length
            ? text(props, 'ui.knowledge.edge_empty', 'No relationships to show yet.')
            : text(props, 'ui.settings.knowledge.empty_summary', 'Knowledge map has no loaded topics yet.')}</pre>
        ) : null}
      </div>
    </div>
  );
}
