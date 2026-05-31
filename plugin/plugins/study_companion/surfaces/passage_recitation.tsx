import { useState } from '@neko/plugin-ui';
import type { PluginSurfaceProps } from '@neko/plugin-ui';
import { callPlugin, errorMessage, text } from './memory_shared';

type RecitationPayload = {
  diff?: unknown;
  habit_progress?: {
    applied?: number;
  };
};

function sanitizeHintCount(value: number): number {
  return Number.isFinite(value) ? Math.max(0, Math.floor(value)) : 0;
}

export default function PassageRecitation(props: PluginSurfaceProps) {
  const [itemId, setItemId] = useState('');
  const [userInput, setUserInput] = useState('');
  const [hintCount, setHintCount] = useState(0);
  const [result, setResult] = useState('');
  const [busy, setBusy] = useState(false);

  async function submit() {
    if (!itemId.trim() || !userInput.trim()) {
      setResult(text(props, 'ui.memory.error_missing_recitation', 'Item id and recitation text are required'));
      return;
    }
    setBusy(true);
    try {
      const sanitizedHintCount = sanitizeHintCount(hintCount);
      const payload = await callPlugin<RecitationPayload>('study_memory_recitation_attempt', {
        item_id: itemId,
        user_input_text: userInput,
        hint_count: sanitizedHintCount,
      });
      const resultText = JSON.stringify(payload.diff || payload, null, 2);
      setResult(payload.habit_progress?.applied ? `${text(props, 'ui.memory.review_goal_updated', 'Goal updated')}\n${resultText}` : resultText);
    } catch (error) {
      setResult(errorMessage(error));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="study-panel">
      <header className="study-panel__header">
        <div>
          <h1>{text(props, 'ui.surface.passage_recitation', 'Passage Recitation')}</h1>
          <span>{text(props, 'ui.memory.recitation_hint', 'Submit a passage item id and your recall text')}</span>
        </div>
      </header>
      <section className="study-panel__state">
        <label>
          <span>{text(props, 'ui.memory.item_id', 'Item ID')}</span>
          <input value={itemId} disabled={busy} onChange={(event) => setItemId(event.target.value)} />
        </label>
        <label>
          <span>{text(props, 'ui.memory.hint_count', 'Hints')}</span>
          <input
            type="number"
            min={0}
            step={1}
            value={hintCount}
            disabled={busy}
            onChange={(event) => setHintCount(sanitizeHintCount(Number(event.target.value)))}
          />
        </label>
      </section>
      <textarea value={userInput} disabled={busy} onChange={(event) => setUserInput(event.target.value)} />
      <div className="study-panel__actions">
        <button type="button" disabled={busy} onClick={submit}>
          {text(props, 'ui.button.submit', 'Submit')}
        </button>
      </div>
      <pre>{result}</pre>
    </div>
  );
}
