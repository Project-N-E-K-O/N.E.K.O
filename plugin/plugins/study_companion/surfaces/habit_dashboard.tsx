import { useEffect, useState } from '@neko/plugin-ui';
import type { PluginSurfaceProps } from '@neko/plugin-ui';

import { callPlugin, ensureBrandCSS, formatError, pomodoroStateLabel, text } from './study_surface_utils';

export default function HabitDashboard(props: PluginSurfaceProps) {
  const [payload, setPayload] = useState<any>({});
  const [error, setError] = useState('');

  async function refresh() {
    const [status, goals, checkin, summary, supervision] = await Promise.all([
      callPlugin(props.api, 'study_pomodoro_status'),
      callPlugin(props.api, 'study_goals'),
      callPlugin(props.api, 'study_checkin_status'),
      callPlugin(props.api, 'study_session_summary'),
      callPlugin(props.api, 'study_supervision_status'),
    ]);
    const goalPayload = goals as { goals?: unknown };
    setPayload({ status, goals: Array.isArray(goalPayload.goals) ? goalPayload.goals : [], checkin, summary, supervision });
  }

  async function act(entryId: string, args: Record<string, unknown> = {}) {
    try {
      await callPlugin(props.api, entryId, args);
      await refresh();
      setError('');
    } catch (err) {
      setError(formatError(err));
    }
  }

  useEffect(() => {
    ensureBrandCSS();
    let disposed = false;
    let inFlight = false;
    const tick = async () => {
      if (disposed || inFlight) return;
      inFlight = true;
      try {
        await refresh();
        if (!disposed) setError('');
      } catch (err) {
        if (!disposed) setError(formatError(err));
      } finally {
        inFlight = false;
      }
    };
    void tick();
    const id = window.setInterval(() => void tick(), 5000);
    return () => {
      disposed = true;
      window.clearInterval(id);
    };
  }, []);

  const goals = Array.isArray(payload.goals) ? payload.goals : [];
  return (
    <div className="study-panel surface-shell">
      <header className="study-panel__header">
        <div>
          <h1>{text(props, 'ui.surface.habit_dashboard', 'Habit Dashboard')}</h1>
          <span>{pomodoroStateLabel(props, payload.status?.state)}</span>
        </div>
      </header>
      {error ? <pre>{error}</pre> : null}
      <section className="study-panel__state">
        <div><span>{text(props, 'ui.label.streak', 'Streak')}</span><strong>{payload.checkin?.streak_days || 0}</strong></div>
        <div><span>{text(props, 'ui.label.focus_minutes', 'Focus')}</span><strong>{payload.summary?.total_focus_minutes || 0}</strong></div>
        <div><span>{text(props, 'ui.label.goals', 'Goals')}</span><strong>{goals.length}</strong></div>
      </section>
      <div className="study-panel__actions">
        <button type="button" onClick={() => act('study_checkin_manual')}>
          {text(props, 'ui.button.checkin', 'Check in')}
        </button>
        <button type="button" onClick={() => act('study_supervision_toggle', { enabled: !payload.supervision?.enabled })}>
          {payload.supervision?.enabled ? text(props, 'ui.button.quiet', 'Quiet') : text(props, 'ui.button.supervise', 'Supervise')}
        </button>
      </div>
    </div>
  );
}
