import { useEffect, useState } from '@neko/plugin-ui';
import type { PluginSurfaceProps } from '@neko/plugin-ui';
import { callPlugin, errorMessage, text } from './memory_shared';
import NoteCard from './note_card';
import type { NoteItem } from './note_card';

type SearchPayload = {
  notes?: NoteItem[];
  topics?: Array<Record<string, unknown>>;
  sessions?: Array<Record<string, unknown>>;
  wrong_questions?: Array<Record<string, unknown>>;
};

type TabKey = 'notes' | 'topics' | 'sessions' | 'wrong_questions';

const TABS: TabKey[] = ['notes', 'topics', 'sessions', 'wrong_questions'];

function itemTitle(item: Record<string, unknown>) {
  return String(item.name || item.title || item.id || item.topic_id || 'Result');
}

function itemSubtitle(item: Record<string, unknown>) {
  return String(item.summary_markdown || item.subject || item.error_type || item.mode || '').slice(0, 220);
}

export default function NoteSearch(props: PluginSurfaceProps) {
  const [query, setQuery] = useState('');
  const [activeTab, setActiveTab] = useState<TabKey>('notes');
  const [payload, setPayload] = useState<SearchPayload>({});
  const [status, setStatus] = useState('');
  const [busy, setBusy] = useState(false);

  async function runSearch(signal?: AbortSignal) {
    const trimmed = query.trim();
    if (!trimmed) {
      setPayload({});
      setStatus('');
      setBusy(false);
      return;
    }
    setBusy(true);
    try {
      const result = await callPlugin<SearchPayload>('study_note_search_all', {
        query: trimmed,
        limit: 50,
      }, signal);
      setPayload(result);
      setStatus(trimmed);
    } catch (error) {
      if (!signal?.aborted) {
        setStatus(errorMessage(error));
      }
    } finally {
      if (!signal?.aborted) {
        setBusy(false);
      }
    }
  }

  useEffect(() => {
    if (!query.trim()) {
      setPayload({});
      setStatus('');
      setBusy(false);
      return;
    }
    const controller = new AbortController();
    const timeoutId = window.setTimeout(() => {
      runSearch(controller.signal).catch((error) => {
        if (!controller.signal.aborted) {
          setStatus(errorMessage(error));
        }
      });
    }, 250);
    return () => {
      window.clearTimeout(timeoutId);
      controller.abort();
    };
  }, [query]);

  const notes = Array.isArray(payload.notes) ? payload.notes : [];
  const records = Array.isArray(payload[activeTab]) ? payload[activeTab] as Array<Record<string, unknown>> : [];

  return (
    <div className="study-panel study-search-surface">
      <header className="study-panel__header">
        <div>
          <h1>{text(props, 'ui.surface.note_search', 'Study Search')}</h1>
          <span>{busy ? text(props, 'ui.status.searching', 'Searching...') : status}</span>
        </div>
      </header>
      <section className="study-search-row">
        <input
          value={query}
          placeholder={text(props, 'ui.notebook.search_all_placeholder', 'Search notes, topics, sessions, wrong questions')}
          onChange={(event) => setQuery(event.target.value)}
        />
        <button type="button" disabled={busy || !query.trim()} onClick={() => runSearch()}>
          {text(props, 'ui.button.search', 'Search')}
        </button>
      </section>
      <nav className="study-tabs">
        {TABS.map((tab) => (
          <button
            type="button"
            key={tab}
            className={activeTab === tab ? 'study-tab study-tab--active' : 'study-tab'}
            onClick={() => setActiveTab(tab)}
          >
            {text(props, `ui.notebook.tab_${tab}`, tab.replace('_', ' '))} ({Array.isArray(payload[tab]) ? payload[tab]?.length || 0 : 0})
          </button>
        ))}
      </nav>
      {activeTab === 'notes' ? (
        <div className="study-note-list study-search-results">
          {notes.map((note) => (
            <NoteCard key={note.id} note={note} />
          ))}
          {notes.length === 0 ? <div className="study-empty">{text(props, 'ui.notebook.no_results', 'No results')}</div> : null}
        </div>
      ) : (
        <div className="study-search-results">
          {records.map((item, index) => (
            <article key={`${activeTab}-${index}`} className="study-result-row">
              <strong>{itemTitle(item)}</strong>
              <span>{itemSubtitle(item)}</span>
            </article>
          ))}
          {records.length === 0 ? <div className="study-empty">{text(props, 'ui.notebook.no_results', 'No results')}</div> : null}
        </div>
      )}
    </div>
  );
}
