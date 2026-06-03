import { useEffect, useState } from '@neko/plugin-ui';
import type { PluginSurfaceProps } from '@neko/plugin-ui';
import { callPlugin, errorMessage, text } from './memory_shared';
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
  const [notebookName, setNotebookName] = useState('');
  const [status, setStatus] = useState('');
  const [busy, setBusy] = useState(false);

  async function refresh(signal?: AbortSignal, notebookId = selectedNotebookId, searchQuery = query) {
    const [notebookPayload, notePayload] = await Promise.all([
      callPlugin<NotebookListPayload>('study_notebook_list', { limit: 100 }, signal),
      callPlugin<NoteListPayload>('study_note_list', {
        notebook_id: notebookId,
        search_query: searchQuery,
        limit: 100,
      }, signal),
    ]);
    setNotebooks(Array.isArray(notebookPayload.notebooks) ? notebookPayload.notebooks : []);
    const nextNotes = Array.isArray(notePayload.notes) ? notePayload.notes : [];
    setNotes(nextNotes);
    setSelectedNote((current) => {
      if (!current) {
        return nextNotes[0] || null;
      }
      return nextNotes.find((note) => note.id === current.id) || nextNotes[0] || null;
    });
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
      if (payload.notebook?.id) {
        setSelectedNotebookId(payload.notebook.id);
        setQuery('');
      } else {
        await refresh();
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
    const controller = new AbortController();
    refresh(controller.signal, selectedNotebookId, query).catch((error) => {
      if (!controller.signal.aborted) {
        setStatus(errorMessage(error));
      }
    });
    return () => controller.abort();
  }, [selectedNotebookId, query]);

  return (
    <div className="study-panel study-notebook-shell">
      <header className="study-panel__header">
        <div>
          <h1>{text(props, 'ui.surface.notebook_panel', 'Study Notebook')}</h1>
          <span>{status || `${notes.length}`}</span>
        </div>
        <div className="study-panel__toolbar">
          <button type="button" disabled={busy} onClick={createNote}>
            {text(props, 'ui.notebook.new_note', 'New note')}
          </button>
          <button
            type="button"
            disabled={busy}
            onClick={() => {
              refresh().catch((error) => setStatus(errorMessage(error)));
            }}
          >
            {text(props, 'ui.button.refresh', 'Refresh')}
          </button>
        </div>
      </header>
      <main className="study-notebook-layout">
        <aside className="study-notebook-sidebar">
          <div className="study-inline-form">
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
            className={`study-notebook-folder${selectedNotebookId === '' ? ' study-notebook-folder--selected' : ''}`}
            onClick={() => setSelectedNotebookId('')}
          >
            <span>{text(props, 'ui.notebook.all_notes', 'All notes')}</span>
            <strong>{notes.length}</strong>
          </button>
          {notebooks.map((notebook) => (
            <button
              type="button"
              key={notebook.id}
              className={`study-notebook-folder${selectedNotebookId === notebook.id ? ' study-notebook-folder--selected' : ''}`}
              onClick={() => setSelectedNotebookId(notebook.id)}
            >
              <span>{notebook.name}</span>
              <strong>{notebook.note_count || 0}</strong>
            </button>
          ))}
        </aside>
        <section className="study-notebook-list">
          <div className="study-search-row">
            <input
              value={query}
              placeholder={text(props, 'ui.notebook.search_placeholder', 'Search notes')}
              onChange={(event) => setQuery(event.target.value)}
            />
          </div>
          <div className="study-note-list">
            {notes.map((note) => (
              <NoteCard
                key={note.id}
                note={note}
                selected={selectedNote?.id === note.id}
                onSelect={setSelectedNote}
              />
            ))}
            {notes.length === 0 ? (
              <div className="study-empty">{text(props, 'ui.notebook.empty', 'No notes yet')}</div>
            ) : null}
          </div>
        </section>
        <section className="study-notebook-detail">
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
            <div className="study-empty">{text(props, 'ui.notebook.select_note', 'Select a note')}</div>
          )}
        </section>
      </main>
    </div>
  );
}
