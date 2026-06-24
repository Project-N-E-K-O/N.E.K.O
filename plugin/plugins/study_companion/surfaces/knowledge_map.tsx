import { useEffect, useState } from '@neko/plugin-ui';
import type { PluginSurfaceProps } from '@neko/plugin-ui';

import { callPlugin, ensureBrandCSS } from './study_surface_utils';

type KnowledgeNode = {
  id: string;
  label: string;
  subject?: string;
  chapter?: string;
  mastery?: number;
  level?: string;
  weak?: boolean;
};

type KnowledgeEdge = {
  from: string;
  to: string;
  relation?: string;
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
  if (normalized === 'related') return text(props, 'ui.knowledge.edge_relation.related', 'Related');
  if (normalized === 'similar') return text(props, 'ui.knowledge.edge_relation.similar', 'Similar');
  if (normalized === 'extends') return text(props, 'ui.knowledge.edge_relation.extends', 'Extends');
  if (normalized === 'next') return text(props, 'ui.knowledge.edge_relation.next', 'Next');
  if (normalized === 'nearby') return text(props, 'ui.knowledge.edge_relation.nearby', 'Nearby');
  return normalized || text(props, 'ui.knowledge.edge_relation.related', 'Related');
}

function edgeGroups(props: PluginSurfaceProps, nodes: KnowledgeNode[], edges: KnowledgeEdge[]) {
  const labels = new Map(nodes.map((node) => [String(node.id || ''), nodeLabel(node)]));
  const groups = new Map<string, { from: string; fromId: string; items: Array<{ relation: string; rawRelation: string; to: string }> }>();
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
    });
    groups.set(key, group);
  });
  return Array.from(groups.values());
}

export default function KnowledgeMap(props: PluginSurfaceProps) {
  const [nodes, setNodes] = useState<KnowledgeNode[]>([]);
  const [edges, setEdges] = useState<KnowledgeEdge[]>([]);
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
        setNodes(Array.isArray(payload.nodes) ? payload.nodes : []);
        setEdges(Array.isArray(payload.edges) ? payload.edges : []);
        setSummary(payload.summary || {});
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
      <div className="study-panel__actions">
        {nodes.slice(0, 60).map((node) => {
          const mastery = Number(node.mastery);
          const masteryText = Number.isFinite(mastery) ? ` ${Math.round(mastery * 100)}%` : '';
          return (
            <button key={node.id} type="button" className="knowledge-node" data-mastery={nodeMasteryLevel(node)}>
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
                <div key={`${item.rawRelation}:${item.to}:${index}`} className="knowledge-edge-row" data-relation={item.rawRelation || 'related'}>
                  <span className="knowledge-edge-row__relation">{item.relation}</span>
                  <span className="knowledge-edge-row__target">{item.to}</span>
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
