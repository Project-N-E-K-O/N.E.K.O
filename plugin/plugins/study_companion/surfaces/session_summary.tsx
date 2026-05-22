import { useEffect, useState } from '@neko/plugin-ui';
import type { PluginSurfaceProps } from '@neko/plugin-ui';

async function readJsonResponse(response: Response, label: string) {
  if (!response.ok) throw new Error(`${label} failed: HTTP ${response.status}`);
  return await response.json();
}

async function callPlugin(entryId: string, args: Record<string, unknown> = {}) {
  const created = await readJsonResponse(await fetch('/runs', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ plugin_id: 'study_companion', entry_id: entryId, args }),
  }), 'Run create');
  const runId = created.run_id || created.id;
  for (let attempt = 0; attempt < 30; attempt += 1) {
    await new Promise((resolve) => window.setTimeout(resolve, 250));
    const run = await readJsonResponse(await fetch(`/runs/${runId}`), 'Run poll');
    if (run.status === 'succeeded') {
      const exported = await readJsonResponse(await fetch(`/runs/${runId}/export`), 'Run export');
      const item = (exported.items || []).find((candidate: any) => candidate.type === 'json' && candidate.json);
      if (!item) throw new Error('Run export missing JSON result');
      if (item.json.success === false || item.json.error) throw new Error(item.json.error?.message || 'Plugin call failed');
      return item.json.data || {};
    }
  }
  throw new Error('Plugin call timed out');
}

function text(props: PluginSurfaceProps, key: string, fallback: string) {
  const value = props.t?.(key);
  return value && value !== key ? value : fallback;
}

export default function SessionSummary(props: PluginSurfaceProps) {
  const [summary, setSummary] = useState<any>({});
  const [error, setError] = useState('');

  useEffect(() => {
    callPlugin('study_session_summary')
      .then(setSummary)
      .catch((err) => setError(err instanceof Error ? err.message : String(err)));
  }, []);

  const completed = Array.isArray(summary.completed_goals) ? summary.completed_goals : [];
  const incomplete = Array.isArray(summary.incomplete_goals) ? summary.incomplete_goals : [];
  return (
    <div className="study-panel">
      <header className="study-panel__header">
        <div>
          <h1>{text(props, 'ui.surface.session_summary', 'Session Summary')}</h1>
          <span>{summary.date || ''}</span>
        </div>
      </header>
      {error ? <pre>{error}</pre> : null}
      <section className="study-panel__state">
        <div><span>{text(props, 'ui.label.focus_minutes', 'Focus')}</span><strong>{summary.total_focus_minutes || 0}</strong></div>
        <div><span>{text(props, 'ui.label.completed', 'Completed')}</span><strong>{completed.length}</strong></div>
        <div><span>{text(props, 'ui.label.incomplete', 'Open')}</span><strong>{incomplete.length}</strong></div>
      </section>
      <pre>{[...completed, ...incomplete].map((goal: any) => `${goal.subject || goal.target_type}: ${goal.progress_amount}/${goal.target_amount} ${goal.unit}`).join('\n')}</pre>
    </div>
  );
}
