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
      <div className="study-panel__reply-label">{text(props, 'ui.label.edges', 'Edges')}</div>
      <pre>{edges.slice(0, 30).map((edge) => `${edge.from} -> ${edge.to}${edge.relation ? ` (${edge.relation})` : ''}`).join('\n')}</pre>
    </div>
  );
}
