import { useEffect, useState } from '@neko/plugin-ui';
import type { PluginSurfaceProps } from '@neko/plugin-ui';

import { callPlugin, formatError, text } from './study_surface_utils';

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
    refresh().catch((err) => setError(formatError(err)));
    const id = window.setInterval(() => refresh().catch((err) => setError(formatError(err))), 5000);
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
