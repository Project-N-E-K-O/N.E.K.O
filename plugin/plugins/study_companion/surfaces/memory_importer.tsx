import { useEffect, useState } from '@neko/plugin-ui';
import type { PluginSurfaceProps } from '@neko/plugin-ui';

type MemoryDeck = {
  id: string;
  name: string;
  deck_type: string;
};

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

export default function MemoryImporter(props: PluginSurfaceProps) {
  const [decks, setDecks] = useState<MemoryDeck[]>([]);
  const [deckId, setDeckId] = useState('');
  const [fmt, setFmt] = useState('csv');
  const [content, setContent] = useState('word,meaning,example_sentence,tags\n');
  const [result, setResult] = useState('');
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    callPlugin('study_memory_list_decks', { limit: 100 })
      .then((payload: any) => {
        const nextDecks = Array.isArray(payload.decks) ? payload.decks : [];
        setDecks(nextDecks);
        setDeckId(nextDecks[0]?.id || '');
      })
      .catch((error) => setResult(error instanceof Error ? error.message : String(error)));
  }, []);

  async function importWords() {
    if (!deckId) {
      setResult(text(props, 'ui.memory.error_missing_deck', 'Choose a deck first'));
      return;
    }
    setBusy(true);
    try {
      const payload = await callPlugin('study_memory_import_words', { deck_id: deckId, content, fmt });
      setResult(JSON.stringify(payload, null, 2));
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
          <h1>{text(props, 'ui.surface.memory_importer', 'Memory Importer')}</h1>
          <span>{text(props, 'ui.memory.import_hint', 'CSV columns: word, meaning, example_sentence, tags')}</span>
        </div>
      </header>
      <section className="study-panel__state">
        <label>
          <span>{text(props, 'ui.memory.deck', 'Deck')}</span>
          <select value={deckId} disabled={busy} onChange={(event) => setDeckId(event.target.value)}>
            {decks.map((deck) => <option key={deck.id} value={deck.id}>{deck.name} / {deck.deck_type}</option>)}
          </select>
        </label>
        <label>
          <span>{text(props, 'ui.label.format', 'Format')}</span>
          <select value={fmt} disabled={busy} onChange={(event) => setFmt(event.target.value)}>
            <option value="csv">CSV</option>
            <option value="json">JSON</option>
          </select>
        </label>
      </section>
      <textarea value={content} disabled={busy} onChange={(event) => setContent(event.target.value)} />
      <div className="study-panel__actions">
        <button type="button" disabled={busy} onClick={importWords}>
          {text(props, 'ui.button.import', 'Import')}
        </button>
      </div>
      <pre>{result}</pre>
    </div>
  );
}
