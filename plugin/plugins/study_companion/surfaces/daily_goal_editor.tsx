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

export default function DailyGoalEditor(props: PluginSurfaceProps) {
  const [goals, setGoals] = useState<any[]>([]);
  const [subject, setSubject] = useState('study');
  const [targetAmount, setTargetAmount] = useState(25);
  const [error, setError] = useState('');

  async function refresh() {
    const payload = await callPlugin('study_goals');
    setGoals(Array.isArray(payload.goals) ? payload.goals : []);
  }

  async function createGoal() {
    await callPlugin('study_goal_create', { target_type: 'subject', subject, target_amount: targetAmount, unit: 'minute' });
    await refresh();
  }

  useEffect(() => {
    refresh().catch((err) => setError(err instanceof Error ? err.message : String(err)));
  }, []);

  return (
    <div className="study-panel">
      <header className="study-panel__header">
        <div>
          <h1>{text(props, 'ui.surface.daily_goal_editor', 'Daily Goals')}</h1>
          <span>{goals.length}</span>
        </div>
      </header>
      {error ? <pre>{error}</pre> : null}
      <section className="study-panel__state">
        <label>
          <span>{text(props, 'ui.label.subject', 'Subject')}</span>
          <input value={subject} onChange={(event: any) => setSubject(event.target.value)} />
        </label>
        <label>
          <span>{text(props, 'ui.label.target', 'Target')}</span>
          <input type="number" min="1" value={targetAmount} onChange={(event: any) => setTargetAmount(Number(event.target.value) || 1)} />
        </label>
        <button type="button" onClick={createGoal}>{text(props, 'ui.button.create_goal', 'Create')}</button>
      </section>
      <div className="study-panel__actions">
        {goals.map((goal) => (
          <button key={goal.id} type="button" onClick={() => callPlugin('study_goal_delete', { goal_id: goal.id }).then(refresh)}>
            {goal.subject || goal.target_type}: {goal.progress_amount}/{goal.target_amount} {goal.unit}
          </button>
        ))}
      </div>
    </div>
  );
}
