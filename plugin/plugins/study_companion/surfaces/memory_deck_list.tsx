import { useEffect, useState } from '@neko/plugin-ui';
import type { PluginSurfaceProps } from '@neko/plugin-ui';
import { callPlugin, errorMessage, text } from './memory_shared';

type MemoryDeck = {
  id: string;
  name: string;
  deck_type: string;
  item_count?: number;
};

export default function MemoryDeckList(props: PluginSurfaceProps) {
  const [decks, setDecks] = useState<MemoryDeck[]>([]);
  const [name, setName] = useState('');
  const [deckType, setDeckType] = useState('word');
  const [status, setStatus] = useState('');
  const [busy, setBusy] = useState(false);

  async function refresh(signal?: AbortSignal) {
    const payload = await callPlugin<{ decks?: MemoryDeck[] }>('study_memory_list_decks', { limit: 100 }, signal);
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
      setStatus(errorMessage(error));
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
      setStatus(errorMessage(error));
    } finally {
      setBusy(false);
    }
  }

  useEffect(() => {
    const controller = new AbortController();
    refresh(controller.signal).catch((error) => {
      if (!controller.signal.aborted) {
        setStatus(errorMessage(error));
      }
    });
    return () => controller.abort();
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
