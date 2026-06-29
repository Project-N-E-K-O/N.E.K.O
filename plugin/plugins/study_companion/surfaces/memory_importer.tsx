import { useEffect, useState } from '@neko/plugin-ui';
import type { PluginSurfaceProps } from '@neko/plugin-ui';
import { callPlugin, errorMessage, text } from './memory_shared';
import { deckTypeLabel, ensureBrandCSS, exportFormatLabel } from './study_surface_utils';

type MemoryDeck = {
  id: string;
  name: string;
  deck_type: string;
};

export default function MemoryImporter(props: PluginSurfaceProps) {
  const [decks, setDecks] = useState<MemoryDeck[]>([]);
  const [deckId, setDeckId] = useState('');
  const [fmt, setFmt] = useState('csv');
  const [content, setContent] = useState('word,meaning,example_sentence,tags\n');
  const [result, setResult] = useState('');
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    ensureBrandCSS();
    const controller = new AbortController();
    callPlugin<{ decks?: MemoryDeck[] }>(props.api, 'study_memory_list_decks', { limit: 100 }, controller.signal)
      .then((payload) => {
        const nextDecks = Array.isArray(payload.decks) ? payload.decks : [];
        setDecks(nextDecks);
        setDeckId(nextDecks[0]?.id || '');
      })
      .catch((error) => {
        if (!controller.signal.aborted) {
          setResult(errorMessage(error));
        }
      });
    return () => controller.abort();
  }, []);

  async function importWords() {
    if (!deckId) {
      setResult(text(props, 'ui.memory.error_missing_deck', 'Choose a deck first'));
      return;
    }
    setBusy(true);
    try {
      const payload = await callPlugin<Record<string, unknown>>(props.api, 'study_memory_import_words', { deck_id: deckId, content, fmt });
      setResult(JSON.stringify(payload, null, 2));
    } catch (error) {
      setResult(errorMessage(error));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="study-panel surface-shell">
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
            {decks.map((deck) => <option key={deck.id} value={deck.id}>{deck.name} / {deckTypeLabel(props, deck.deck_type)}</option>)}
          </select>
        </label>
        <label>
          <span>{text(props, 'ui.label.format', 'Format')}</span>
          <select value={fmt} disabled={busy} onChange={(event) => setFmt(event.target.value)}>
            <option value="csv">{exportFormatLabel(props, 'csv')}</option>
            <option value="json">{exportFormatLabel(props, 'json')}</option>
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
