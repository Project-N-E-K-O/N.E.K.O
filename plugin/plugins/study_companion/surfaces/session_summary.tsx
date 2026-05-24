import { useEffect, useState } from '@neko/plugin-ui';
import type { PluginSurfaceProps } from '@neko/plugin-ui';

import { callPlugin, formatError, text } from './study_surface_utils';

export default function SessionSummary(props: PluginSurfaceProps) {
  const [summary, setSummary] = useState<any>({});
  const [error, setError] = useState('');

  useEffect(() => {
    callPlugin('study_session_summary')
      .then(setSummary)
      .catch((err) => setError(formatError(err)));
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
