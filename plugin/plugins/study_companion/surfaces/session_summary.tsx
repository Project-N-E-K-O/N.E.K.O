import { useEffect, useState } from '@neko/plugin-ui';
import type { PluginSurfaceProps } from '@neko/plugin-ui';

import { callPlugin, ensureBrandCSS, formatError, goalUnitLabel, targetTypeLabel, text } from './study_surface_utils';

export default function SessionSummary(props: PluginSurfaceProps) {
  const [summary, setSummary] = useState<any>({});
  const [error, setError] = useState('');

  useEffect(() => {
    ensureBrandCSS();
    callPlugin(props.api, 'study_session_summary')
      .then(setSummary)
      .catch((err) => setError(formatError(err)));
  }, []);

  const completed = Array.isArray(summary.completed_goals) ? summary.completed_goals : [];
  const incomplete = Array.isArray(summary.incomplete_goals) ? summary.incomplete_goals : [];
  const memory = summary.memory_summary && summary.memory_summary.available ? summary.memory_summary : null;
  const memoryCorrectRate = Number(memory?.correct_rate);
  const memoryCorrectPercent = Number.isFinite(memoryCorrectRate) ? Math.round(memoryCorrectRate * 100) : 0;
  return (
    <div className="study-panel surface-shell">
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
      {memory ? (
        <section className="study-panel__state">
          <div><span>{text(props, 'ui.summary.memory_block_title', 'Memory')}</span><strong>{memory.deck_count || 0}</strong></div>
          <div><span>{text(props, 'ui.summary.memory_reviewed', 'Reviewed')}</span><strong>{memory.reviewed_items || 0}</strong></div>
          <div><span>{text(props, 'ui.summary.memory_correct_rate', 'Correct')}</span><strong>{memoryCorrectPercent}%</strong></div>
          <div><span>{text(props, 'ui.summary.memory_recitation', 'Recitation')}</span><strong>{memory.recitation_attempts || 0}</strong></div>
          <div><span>{text(props, 'ui.summary.memory_focus_minutes', 'Focus minutes')}</span><strong>{memory.focus_minutes || 0}</strong></div>
          <div><span>{text(props, 'ui.summary.memory_due_remaining', 'Due')}</span><strong>{memory.due_remaining || 0}</strong></div>
        </section>
      ) : null}
      <pre>{[...completed, ...incomplete].map((goal: any) => `${goal.subject || targetTypeLabel(props, goal.target_type)}: ${goal.progress_amount}/${goal.target_amount} ${goalUnitLabel(props, goal.unit)}`).join('\n')}</pre>
    </div>
  );
}
