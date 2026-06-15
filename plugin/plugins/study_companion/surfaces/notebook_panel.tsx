import { useEffect, useRef, useState } from '@neko/plugin-ui';
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

function listToCsv(value: string[] | undefined): string {
  return Array.isArray(value) ? value.join(', ') : '';
}

function csvToList(value: string): string[] {
  return value
    .split(',')
    .map((item) => item.trim())
    .filter((item) => item.length > 0);
}

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
  const [editing, setEditing] = useState(false);
  const [editTitle, setEditTitle] = useState('');
  const [editContent, setEditContent] = useState('');
  const [editTopics, setEditTopics] = useState('');
  const [editTags, setEditTags] = useState('');
  // Tracks the currently selected note id for async edit guards. State closures
  // capture the value at call time, so an in-flight fetch/save compares against
  // this ref to detect the user switching notes mid-request.
  const selectedNoteIdRef = useRef('');
  // Latest list filter, so an in-flight loadNotes (e.g. a refresh fired from an
  // edit) can drop its result if the user changed notebook/search meanwhile.
  const selectedNotebookIdRef = useRef('');
  const debouncedQueryRef = useRef('');

  useEffect(() => {
    ensureBrandCSS();
  }, []);

  async function loadNotebooks(signal?: AbortSignal) {
    const notebookPayload = await callPlugin<NotebookListPayload>(props.api, 'study_notebook_list', { limit: 100 }, signal);
    setNotebooks(Array.isArray(notebookPayload.notebooks) ? notebookPayload.notebooks : []);
  }

  async function loadNotes(signal?: AbortSignal, notebookId = selectedNotebookId, searchQuery = debouncedQuery) {
    const notePayload = await callPlugin<NoteListPayload>(props.api, 'study_note_list', {
      notebook_id: notebookId,
      search_query: searchQuery,
      limit: 100,
    }, signal);
    // Drop stale results: the user may have switched notebook/search while this
    // request was in flight (e.g. a refresh() fired from saveEdit), in which case
    // applying it would overwrite the current view with the previous filter.
    if (notebookId !== selectedNotebookIdRef.current || searchQuery !== debouncedQueryRef.current) {
      return;
    }
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
      const payload = await callPlugin<NotebookCreatePayload>(props.api, 'study_notebook_create', { name });
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
      const payload = await callPlugin<NoteSavePayload>(props.api, 'study_note_upsert', {
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
      await callPlugin(props.api, 'study_note_delete', { note_id: noteId });
      await refresh();
    } catch (error) {
      setStatus(errorMessage(error));
    } finally {
      setBusy(false);
    }
  }

  async function startEdit() {
    if (!selectedNote) {
      return;
    }
    const noteId = selectedNote.id;
    setBusy(true);
    try {
      // The list payload only carries a snippet, so fetch the full note before
      // editing to avoid overwriting the body with a truncated preview.
      const payload = await callPlugin<NoteSavePayload>(props.api, 'study_note_get', { note_id: noteId });
      // The user may have switched notes while the fetch was in flight; drop the
      // result so we never load note A's body into note B's editor.
      if (selectedNoteIdRef.current !== noteId) {
        return;
      }
      const note = payload.note || selectedNote;
      setEditTitle(note.title || '');
      setEditContent(note.content || '');
      setEditTopics(listToCsv(note.topic_ids));
      setEditTags(listToCsv(note.tags));
      setEditing(true);
    } catch (error) {
      setStatus(errorMessage(error));
    } finally {
      setBusy(false);
    }
  }

  function cancelEdit() {
    setEditing(false);
  }

  async function saveEdit() {
    if (!selectedNote) {
      return;
    }
    // Pin the note being edited so the save always targets it, even if the
    // selection changes before the request resolves.
    const noteId = selectedNote.id;
    setBusy(true);
    try {
      const payload = await callPlugin<NoteSavePayload>(props.api, 'study_note_upsert', {
        note_id: noteId,
        title: editTitle,
        content: editContent,
        topic_ids: csvToList(editTopics),
        tags: csvToList(editTags),
      });
      setEditing(false);
      await refresh();
      // Only re-select the saved note if the user is still on it; otherwise
      // respect their newer selection.
      if (payload.note && selectedNoteIdRef.current === noteId) {
        setSelectedNote(payload.note);
      }
      setStatus(text(props, 'ui.notebook.saved', 'Saved'));
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
    // Keep the filter refs current so in-flight loadNotes calls can detect a
    // newer selection and drop their stale results.
    selectedNotebookIdRef.current = selectedNotebookId;
    debouncedQueryRef.current = debouncedQuery;
    const controller = new AbortController();
    loadNotes(controller.signal, selectedNotebookId, debouncedQuery).catch((error) => {
      if (!controller.signal.aborted) {
        setStatus(errorMessage(error));
      }
    });
    return () => controller.abort();
  }, [selectedNotebookId, debouncedQuery]);

  // Selecting a different note (or clearing the selection) leaves edit mode so
  // the form never shows stale fields from the previously edited note.
  useEffect(() => {
    selectedNoteIdRef.current = selectedNote?.id || '';
    setEditing(false);
  }, [selectedNote?.id]);

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
            editing ? (
              <>
                <label>
                  <span>{text(props, 'ui.label.title', 'Title')}</span>
                  <input value={editTitle} disabled={busy} onChange={(event) => setEditTitle(event.target.value)} />
                </label>
                <label>
                  <span>{text(props, 'ui.notebook.topics', 'Topics')}</span>
                  <input value={editTopics} disabled={busy} onChange={(event) => setEditTopics(event.target.value)} />
                </label>
                <label>
                  <span>{text(props, 'ui.notebook.tags', 'Tags')}</span>
                  <input value={editTags} disabled={busy} onChange={(event) => setEditTags(event.target.value)} />
                </label>
                <label>
                  <span>{text(props, 'ui.notebook.content', 'Content')}</span>
                  <textarea value={editContent} disabled={busy} onChange={(event) => setEditContent(event.target.value)} />
                </label>
                <div className="study-panel__toolbar">
                  <button type="button" disabled={busy} onClick={saveEdit}>
                    {text(props, 'ui.button.save', 'Save')}
                  </button>
                  <button type="button" disabled={busy} onClick={cancelEdit}>
                    {text(props, 'ui.button.cancel', 'Cancel')}
                  </button>
                </div>
              </>
            ) : (
              <>
                <div>
                  <h2>{selectedNote.title}</h2>
                  <span>{selectedNote.updated_at || selectedNote.edited_at || ''}</span>
                </div>
                <p>{selectedNote.snippet || text(props, 'ui.notebook.empty_note', 'Empty note')}</p>
                <div className="study-panel__toolbar">
                  <button type="button" disabled={busy} onClick={startEdit}>
                    {text(props, 'ui.button.edit', 'Edit')}
                  </button>
                  <button type="button" disabled={busy} onClick={() => deleteNote(selectedNote.id)}>
                    {text(props, 'ui.button.delete', 'Delete')}
                  </button>
                </div>
              </>
            )
          ) : (
            <div className="study-panel__empty">{text(props, 'ui.notebook.select_note', 'Select a note')}</div>
          )}
        </section>
      </main>
    </div>
  );
}
