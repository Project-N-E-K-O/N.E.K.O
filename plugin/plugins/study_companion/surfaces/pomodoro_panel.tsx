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

function formatSeconds(value: number) {
  const seconds = Math.max(0, Number(value) || 0);
  const minutes = Math.floor(seconds / 60);
  return `${minutes}:${String(seconds % 60).padStart(2, '0')}`;
}

export default function PomodoroPanel(props: PluginSurfaceProps) {
  const [status, setStatus] = useState<any>({});
  const [error, setError] = useState('');

  async function refresh() {
    setStatus(await callPlugin('study_pomodoro_status'));
  }
  async function act(entryId: string) {
    setStatus(await callPlugin(entryId));
  }

  useEffect(() => {
    refresh().catch((err) => setError(err instanceof Error ? err.message : String(err)));
    const id = window.setInterval(() => refresh().catch(() => undefined), 1000);
    return () => window.clearInterval(id);
  }, []);

  return (
    <div className="study-panel">
      <header className="study-panel__header">
        <div>
          <h1>{text(props, 'ui.surface.pomodoro_panel', 'Pomodoro')}</h1>
          <span>{status.state || 'idle'}</span>
        </div>
      </header>
      {error ? <pre>{error}</pre> : null}
      <section className="study-panel__state">
        <div><span>{text(props, 'ui.label.remaining', 'Remaining')}</span><strong>{formatSeconds(status.remaining_seconds)}</strong></div>
        <div><span>{text(props, 'ui.label.sessions', 'Sessions')}</span><strong>{status.session_count || 0}</strong></div>
        <div><span>{text(props, 'ui.label.mode', 'Mode')}</span><strong>{status.mode || 'focus'}</strong></div>
      </section>
      <div className="study-panel__actions">
        <button type="button" onClick={() => act('study_pomodoro_start')}>{text(props, 'ui.button.start', 'Start')}</button>
        <button type="button" onClick={() => act('study_pomodoro_pause')}>{text(props, 'ui.button.pause', 'Pause')}</button>
        <button type="button" onClick={() => act('study_pomodoro_resume')}>{text(props, 'ui.button.resume', 'Resume')}</button>
        <button type="button" onClick={() => act('study_pomodoro_stop')}>{text(props, 'ui.button.stop', 'Stop')}</button>
        <button type="button" onClick={() => act('study_pomodoro_skip_break')}>{text(props, 'ui.button.skip_break', 'Skip break')}</button>
      </div>
    </div>
  );
}
