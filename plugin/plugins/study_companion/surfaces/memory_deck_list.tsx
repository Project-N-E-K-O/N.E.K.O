import { useEffect, useState } from '@neko/plugin-ui';
import type { PluginSurfaceProps } from '@neko/plugin-ui';

type MemoryDeck = {
  id: string;
  name: string;
  deck_type: string;
  item_count?: number;
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

export default function MemoryDeckList(props: PluginSurfaceProps) {
  const [decks, setDecks] = useState<MemoryDeck[]>([]);
  const [name, setName] = useState('');
  const [deckType, setDeckType] = useState('word');
  const [status, setStatus] = useState('');
  const [busy, setBusy] = useState(false);

  async function refresh() {
    const payload = await callPlugin('study_memory_list_decks', { limit: 100 });
    setDecks(Array.isArray(payload.decks) ? payload.decks : []);
  }

  async function createDeck() {
    if (!name.trim()) {
      setStatus(text(props, 'ui.memory.error_missing_deck_name', 'Deck name is required'));
      return;
    }
    setBusy(true);
    try {
      await callPlugin('study_memory_create_deck', { name, deck_type: deckType });
      setName('');
      await refresh();
      setStatus(text(props, 'ui.status.reply_ready', 'Reply ready'));
    } catch (error) {
      setStatus(error instanceof Error ? error.message : String(error));
    } finally {
      setBusy(false);
    }
  }

  async function deleteDeck(deckId: string) {
    setBusy(true);
    try {
      await callPlugin('study_memory_delete_deck', { deck_id: deckId });
      await refresh();
    } catch (error) {
      setStatus(error instanceof Error ? error.message : String(error));
    } finally {
      setBusy(false);
    }
  }

  useEffect(() => {
    refresh().catch((error) => setStatus(error instanceof Error ? error.message : String(error)));
  }, []);

  return (
    <div className="study-panel">
      <header className="study-panel__header">
        <div>
          <h1>{text(props, 'ui.surface.memory_deck_list', 'Memory Decks')}</h1>
          <span>{status || `${decks.length}`}</span>
        </div>
      </header>
      <section className="study-panel__state">
        <label>
          <span>{text(props, 'ui.label.name', 'Name')}</span>
          <input value={name} disabled={busy} onChange={(event) => setName(event.target.value)} />
        </label>
        <label>
          <span>{text(props, 'ui.memory.deck_type', 'Deck Type')}</span>
          <select value={deckType} disabled={busy} onChange={(event) => setDeckType(event.target.value)}>
            <option value="word">word</option>
            <option value="passage">passage</option>
            <option value="formula">formula</option>
            <option value="custom">custom</option>
          </select>
        </label>
        <button type="button" disabled={busy} onClick={createDeck}>
          {text(props, 'ui.button.create', 'Create')}
        </button>
      </section>
      <div className="study-panel__actions">
        {decks.map((deck) => (
          <button key={deck.id} type="button" disabled={busy} onClick={() => deleteDeck(deck.id)}>
            {deck.name} / {deck.deck_type} / {deck.item_count || 0}
          </button>
        ))}
      </div>
    </div>
  );
}
