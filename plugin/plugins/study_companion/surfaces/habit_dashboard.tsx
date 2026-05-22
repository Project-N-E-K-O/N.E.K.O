import { useEffect, useState } from '@neko/plugin-ui';
import type { PluginSurfaceProps } from '@neko/plugin-ui';

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
    if (['failed', 'canceled', 'timeout'].includes(run.status)) throw new Error(run.status);
  }
  throw new Error('Plugin call timed out');
}

function text(props: PluginSurfaceProps, key: string, fallback: string) {
  const value = props.t?.(key);
  return value && value !== key ? value : fallback;
}

export default function HabitDashboard(props: PluginSurfaceProps) {
  const [payload, setPayload] = useState<any>({});
  const [error, setError] = useState('');

  async function refresh() {
    const [status, goals, checkin, summary, supervision] = await Promise.all([
      callPlugin('study_pomodoro_status'),
      callPlugin('study_goals'),
      callPlugin('study_checkin_status'),
      callPlugin('study_session_summary'),
      callPlugin('study_supervision_status'),
    ]);
    setPayload({ status, goals: goals.goals || [], checkin, summary, supervision });
  }

  useEffect(() => {
    refresh().catch((err) => setError(err instanceof Error ? err.message : String(err)));
    const id = window.setInterval(() => refresh().catch(() => undefined), 5000);
    return () => window.clearInterval(id);
  }, []);

  const goals = Array.isArray(payload.goals) ? payload.goals : [];
  return (
    <div className="study-panel">
      <header className="study-panel__header">
        <div>
          <h1>{text(props, 'ui.surface.habit_dashboard', 'Habit Dashboard')}</h1>
          <span>{payload.status?.state || 'idle'}</span>
        </div>
      </header>
      {error ? <pre>{error}</pre> : null}
      <section className="study-panel__state">
        <div><span>{text(props, 'ui.label.streak', 'Streak')}</span><strong>{payload.checkin?.streak_days || 0}</strong></div>
        <div><span>{text(props, 'ui.label.focus_minutes', 'Focus')}</span><strong>{payload.summary?.total_focus_minutes || 0}</strong></div>
        <div><span>{text(props, 'ui.label.goals', 'Goals')}</span><strong>{goals.length}</strong></div>
      </section>
      <div className="study-panel__actions">
        <button type="button" onClick={() => callPlugin('study_checkin_manual').then(refresh)}>
          {text(props, 'ui.button.checkin', 'Check in')}
        </button>
        <button type="button" onClick={() => callPlugin('study_supervision_toggle', { enabled: !payload.supervision?.enabled }).then(refresh)}>
          {payload.supervision?.enabled ? text(props, 'ui.button.quiet', 'Quiet') : text(props, 'ui.button.supervise', 'Supervise')}
        </button>
      </div>
    </div>
  );
}
