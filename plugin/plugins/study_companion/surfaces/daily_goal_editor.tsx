import { useEffect, useState } from '@neko/plugin-ui';
import type { PluginSurfaceProps } from '@neko/plugin-ui';

import { callPlugin, ensureBrandCSS, formatError, goalUnitLabel, targetTypeLabel, text } from './study_surface_utils';

export default function DailyGoalEditor(props: PluginSurfaceProps) {
  const [goals, setGoals] = useState<any[]>([]);
  const [subject, setSubject] = useState('study');
  const [targetAmount, setTargetAmount] = useState(25);
  const [error, setError] = useState('');

  async function refresh() {
    const payload = await callPlugin(props.api, 'study_goals');
    const goalPayload = payload as { goals?: unknown };
    setGoals(Array.isArray(goalPayload.goals) ? goalPayload.goals : []);
  }

  async function createGoal() {
    try {
      await callPlugin(props.api, 'study_goal_create', { target_type: 'subject', subject, target_amount: targetAmount, unit: 'minute' });
      await refresh();
      setError('');
    } catch (err) {
      setError(formatError(err));
    }
  }

  async function deleteGoal(goalId: string) {
    try {
      await callPlugin(props.api, 'study_goal_delete', { goal_id: goalId });
      await refresh();
      setError('');
    } catch (err) {
      setError(formatError(err));
    }
  }

  useEffect(() => {
    ensureBrandCSS();
    refresh().catch((err) => setError(formatError(err)));
  }, []);

  return (
    <div className="study-panel surface-shell">
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
          <button key={goal.id} type="button" onClick={() => deleteGoal(goal.id)}>
            {goal.subject || targetTypeLabel(props, goal.target_type)}: {goal.progress_amount}/{goal.target_amount} {goalUnitLabel(props, goal.unit)}
          </button>
        ))}
      </div>
    </div>
  );
}
