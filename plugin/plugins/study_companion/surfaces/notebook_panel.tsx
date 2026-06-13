import { useEffect, useState } from '@neko/plugin-ui';
import type { PluginSurfaceProps } from '@neko/plugin-ui';
import { callPlugin, errorMessage, text } from './memory_shared';
import { ensureBrandCSS } from './study_surface_utils';
import NoteCard from './note_card';
import type { NoteItem } from './note_card';

type NotebookMeta = {
  id: string;
  name: string;
  description?: string;
  note_count?: number;
};

type NotebookListPayload = {
  notebooks?: NotebookMeta[];
};

type NoteListPayload = {
  notes?: NoteItem[];
};

type NotebookCreatePayload = {
  notebook?: NotebookMeta;
};

type NoteSavePayload = {
  note?: NoteItem;
};

export default function NotebookPanel(props: PluginSurfaceProps) {
  const [notebooks, setNotebooks] = useState<NotebookMeta[]>([]);
  const [notes, setNotes] = useState<NoteItem[]>([]);
  const [selectedNotebookId, setSelectedNotebookId] = useState('');
  const [selectedNote, setSelectedNote] = useState<NoteItem | null>(null);
  const [query, setQuery] = useState('');
  const [debouncedQuery, setDebouncedQuery] = useState('');
  const [notebookName, setNotebookName] = useState('');
  const [status, setStatus] = useState('');
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    ensureBrandCSS();
  }, []);

  async function loadNotebooks(signal?: AbortSignal) {
    const notebookPayload = await callPlugin<NotebookListPayload>('study_notebook_list', { limit: 100 }, signal);
    setNotebooks(Array.isArray(notebookPayload.notebooks) ? notebookPayload.notebooks : []);
  }

  async function loadNotes(signal?: AbortSignal, notebookId = selectedNotebookId, searchQuery = debouncedQuery) {
    const notePayload = await callPlugin<NoteListPayload>('study_note_list', {
      notebook_id: notebookId,
      search_query: searchQuery,
      limit: 100,
    }, signal);
    const nextNotes = Array.isArray(notePayload.notes) ? notePayload.notes : [];
    setNotes(nextNotes);
    setSelectedNote((current) => {
      if (!current) {
        return nextNotes[0] || null;
      }
      return nextNotes.find((note) => note.id === current.id) || nextNotes[0] || null;
    });
  }

  async function refresh(signal?: AbortSignal, notebookId = selectedNotebookId, searchQuery = debouncedQuery) {
    await Promise.all([
      loadNotebooks(signal),
      loadNotes(signal, notebookId, searchQuery),
    ]);
  }

  async function createNotebook() {
    const name = notebookName.trim();
    if (!name) {
      setStatus(text(props, 'ui.notebook.name_required', 'Notebook name is required'));
      return;
    }
    setBusy(true);
    try {
      const payload = await callPlugin<NotebookCreatePayload>('study_notebook_create', { name });
      setNotebookName('');
      await loadNotebooks();
      if (payload.notebook?.id) {
        setSelectedNotebookId(payload.notebook.id);
        setQuery('');
        setDebouncedQuery('');
      } else {
        await loadNotes();
      }
    } catch (error) {
      setStatus(errorMessage(error));
    } finally {
      setBusy(false);
    }
  }

  async function createNote() {
    setBusy(true);
    try {
      const payload = await callPlugin<NoteSavePayload>('study_note_upsert', {
        notebook_id: selectedNotebookId,
        title: text(props, 'ui.notebook.new_note', 'New note'),
        content: '',
      });
      await refresh();
      if (payload.note) {
        setSelectedNote(payload.note);
      }
      setStatus(text(props, 'ui.notebook.saved', 'Saved'));
    } catch (error) {
      setStatus(errorMessage(error));
    } finally {
      setBusy(false);
    }
  }

  async function deleteNote(noteId: string) {
    setBusy(true);
    try {
      await callPlugin('study_note_delete', { note_id: noteId });
      await refresh();
    } catch (error) {
      setStatus(errorMessage(error));
    } finally {
      setBusy(false);
    }
  }

  useEffect(() => {
    const timeoutId = window.setTimeout(() => {
      setDebouncedQuery(query);
    }, 250);
    return () => window.clearTimeout(timeoutId);
  }, [query]);

  useEffect(() => {
    const controller = new AbortController();
    loadNotebooks(controller.signal).catch((error) => {
      if (!controller.signal.aborted) {
        setStatus(errorMessage(error));
      }
    });
    return () => controller.abort();
  }, []);

  useEffect(() => {
    const controller = new AbortController();
    loadNotes(controller.signal, selectedNotebookId, debouncedQuery).catch((error) => {
      if (!controller.signal.aborted) {
        setStatus(errorMessage(error));
      }
    });
    return () => controller.abort();
  }, [selectedNotebookId, debouncedQuery]);

  return (
    <div className="study-panel surface-shell">
      <header className="study-panel__header">
        <div>
          <h1>{text(props, 'ui.surface.notebook_panel', 'Study Notebook')}</h1>
          <span>{status || `${notes.length}`}</span>
        </div>
        <div className="study-panel__toolbar">
          <button type="button" disabled={busy} onClick={createNote}>
            {text(props, 'ui.notebook.new_note', 'New note')}
          </button>
          <button type="button" disabled={busy} onClick={() => refresh()}>
            {text(props, 'ui.button.refresh', 'Refresh')}
          </button>
        </div>
      </header>
      <main className="study-panel__layout">
        <aside className="study-panel__sidebar">
          <div className="study-panel__inline-form">
            <input
              value={notebookName}
              disabled={busy}
              placeholder={text(props, 'ui.notebook.new_notebook', 'New notebook')}
              onChange={(event) => setNotebookName(event.target.value)}
            />
            <button type="button" disabled={busy} onClick={createNotebook}>
              {text(props, 'ui.button.create', 'Create')}
            </button>
          </div>
          <button
            type="button"
            className={`study-panel__folder${selectedNotebookId === '' ? ' is-selected' : ''}`}
            onClick={() => setSelectedNotebookId('')}
          >
            <span>{text(props, 'ui.notebook.all_notes', 'All notes')}</span>
            <strong>{notes.length}</strong>
          </button>
          {notebooks.map((notebook) => (
            <button
              type="button"
              key={notebook.id}
              className={`study-panel__folder${selectedNotebookId === notebook.id ? ' is-selected' : ''}`}
              onClick={() => setSelectedNotebookId(notebook.id)}
            >
              <span>{notebook.name}</span>
              <strong>{notebook.note_count || 0}</strong>
            </button>
          ))}
        </aside>
        <section className="study-panel__column">
          <div className="study-panel__search-row">
            <input
              value={query}
              placeholder={text(props, 'ui.notebook.search_placeholder', 'Search notes')}
              onChange={(event) => setQuery(event.target.value)}
            />
          </div>
          <div className="study-panel__note-list">
            {notes.map((note) => (
              <NoteCard
                key={note.id}
                note={note}
                selected={selectedNote?.id === note.id}
                onSelect={setSelectedNote}
              />
            ))}
            {notes.length === 0 ? (
              <div className="study-panel__empty">{text(props, 'ui.notebook.empty', 'No notes yet')}</div>
            ) : null}
          </div>
        </section>
        <section className="study-panel__detail">
          {selectedNote ? (
            <>
              <div>
                <h2>{selectedNote.title}</h2>
                <span>{selectedNote.updated_at || selectedNote.edited_at || ''}</span>
              </div>
              <p>{selectedNote.snippet || text(props, 'ui.notebook.empty_note', 'Empty note')}</p>
              <div className="study-panel__toolbar">
                <button type="button" disabled={busy} onClick={() => deleteNote(selectedNote.id)}>
                  {text(props, 'ui.button.delete', 'Delete')}
                </button>
              </div>
            </>
          ) : (
            <div className="study-panel__empty">{text(props, 'ui.notebook.select_note', 'Select a note')}</div>
          )}
        </section>
      </main>
    </div>
  );
}
