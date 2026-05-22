import { useState } from '@neko/plugin-ui';
import type { PluginSurfaceProps } from '@neko/plugin-ui';

async function readJsonResponse(response: Response, label: string) {
  if (!response.ok) {
    throw new Error(`${label} failed: HTTP ${response.status}`);
  }
  return await response.json();
}

async function callPlugin(entryId: string, args: Record<string, unknown> = {}) {
  const created = await readJsonResponse(await fetch('/runs', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ plugin_id: 'study_companion', entry_id: entryId, args }),
  }), 'Run create');
  const runId = created.run_id || created.id;
  if (!runId) {
    throw new Error('Run id missing');
  }
  for (let attempt = 0; attempt < 40; attempt += 1) {
    await new Promise((resolve) => window.setTimeout(resolve, 350));
    const run = await readJsonResponse(await fetch(`/runs/${runId}`), 'Run poll');
    if (run.status === 'succeeded') {
      const exported = await readJsonResponse(await fetch(`/runs/${runId}/export`), 'Run export');
      const item = (exported.items || []).find((candidate: any) => candidate.type === 'json' && candidate.json);
      if (!item) {
        throw new Error('Run export missing JSON result');
      }
      if (item.json.success === false || item.json.error) {
        throw new Error(item.json.error?.message || item.json.message || 'Plugin call failed');
      }
      return item.json.data || {};
    }
    if (['failed', 'canceled', 'timeout'].includes(run.status)) {
      throw new Error(run.error?.message || run.message || run.status);
    }
  }
  throw new Error('Plugin call timed out');
}

function text(props: PluginSurfaceProps, key: string, fallback: string) {
  const value = props.t?.(key);
  return value && value !== key ? value : fallback;
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
      const payload = await callPlugin('study_memory_recitation_attempt', {
        item_id: itemId,
        user_input_text: userInput,
        hint_count: hintCount,
      });
      setResult(JSON.stringify(payload.diff || payload, null, 2));
    } catch (error) {
      setResult(error instanceof Error ? error.message : String(error));
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
          <input type="number" value={hintCount} disabled={busy} onChange={(event) => setHintCount(Number(event.target.value) || 0)} />
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
