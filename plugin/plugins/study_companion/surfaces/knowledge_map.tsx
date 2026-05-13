import { useEffect, useState } from '@neko/plugin-ui';
import type { PluginSurfaceProps } from '@neko/plugin-ui';

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

async function readJsonResponse(response: Response, label: string) {
  if (!response.ok) {
    throw new Error(`${label} failed: HTTP ${response.status}`);
  }
  return await response.json();
}

async function callPlugin(entryId: string, args: Record<string, unknown> = {}) {
  const createResp = await fetch('/runs', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ plugin_id: 'study_companion', entry_id: entryId, args }),
  });
  const created = await readJsonResponse(createResp, 'Run create');
  const runId = created.run_id || created.id;
  if (!runId) {
    throw new Error('Run id missing');
  }
  for (let attempt = 0; attempt < 40; attempt += 1) {
    await new Promise((resolve) => window.setTimeout(resolve, 350));
    const run = await readJsonResponse(await fetch(`/runs/${runId}`), 'Run poll');
    if (run.status === 'succeeded') {
      const exported = await readJsonResponse(await fetch(`/runs/${runId}/export`), 'Run export');
      const item = (exported.items || []).find((candidate: any) => candidate.type === 'json' && candidate.json);
      if (!item) {
        throw new Error('Run export missing JSON result');
      }
      if (item.json.success === false || item.json.error) {
        throw new Error(item.json.error?.message || item.json.message || 'Plugin call failed');
      }
      return item.json.data || {};
    }
    if (['failed', 'canceled', 'timeout'].includes(run.status)) {
      throw new Error(run.error?.message || run.message || run.status);
    }
  }
  throw new Error('Plugin call timed out');
}

function text(props: PluginSurfaceProps, key: string, fallback: string) {
  const value = props.t?.(key);
  return value && value !== key ? value : fallback;
}

export default function KnowledgeMap(props: PluginSurfaceProps) {
  const [nodes, setNodes] = useState<KnowledgeNode[]>([]);
  const [edges, setEdges] = useState<KnowledgeEdge[]>([]);
  const [summary, setSummary] = useState<Record<string, number>>({});
  const [error, setError] = useState('');

  useEffect(() => {
    let mounted = true;
    callPlugin('study_knowledge_map', { limit: 200 })
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
    <div className="study-panel">
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
        {nodes.slice(0, 60).map((node) => (
          <button key={node.id} type="button" className={node.weak ? 'is-active' : ''}>
            {node.label} {node.mastery !== undefined && node.mastery !== null ? `${Math.round(node.mastery * 100)}%` : ''}
          </button>
        ))}
      </div>
      <div className="study-panel__reply-label">{text(props, 'ui.label.edges', 'Edges')}</div>
      <pre>{edges.slice(0, 30).map((edge) => `${edge.from} -> ${edge.to}${edge.relation ? ` (${edge.relation})` : ''}`).join('\n')}</pre>
    </div>
  );
}
