import { useEffect, useRef, useState } from '@neko/plugin-ui';
import type { PluginSurfaceProps } from '@neko/plugin-ui';
import { callPlugin, errorMessage, text } from './memory_shared';
import type { NoteItem } from './note_card';

type NoteGetPayload = {
  note?: NoteItem;
};

type NoteSavePayload = {
  note?: NoteItem;
};

const AI_EXPAND_POLL_TIMEOUT_MS = 90000;

type NoteDraftSnapshot = {
  noteId: string;
  notebookId: string;
  title: string;
  content: string;
  topics: string;
  tags: string;
};

function csvToList(value: string): string[] {
  return value
    .split(/[,，\s]+/)
    .map((item) => item.trim())
    .filter(Boolean);
}

function listToCsv(value: string[] | undefined): string {
  return Array.isArray(value) ? value.join(', ') : '';
}

function insertWrap(textarea: HTMLTextAreaElement, value: string, before: string, after = before) {
  const start = textarea.selectionStart;
  const end = textarea.selectionEnd;
  const selected = value.slice(start, end) || 'text';
  const next = `${value.slice(0, start)}${before}${selected}${after}${value.slice(end)}`;
  const cursor = start + before.length + selected.length + after.length;
  return { next, cursor };
}

function markdownPreview(markdown: string) {
  const lines = markdown.split(/\r?\n/);
  return lines.map((line, index) => {
    const key = `${index}-${line.slice(0, 12)}`;
    if (line.startsWith('# ')) {
      return <h1 key={key}>{line.slice(2)}</h1>;
    }
    if (line.startsWith('## ')) {
      return <h2 key={key}>{line.slice(3)}</h2>;
    }
    if (line.startsWith('### ')) {
      return <h3 key={key}>{line.slice(4)}</h3>;
    }
    if (line.startsWith('> ')) {
      return <blockquote key={key}>{line.slice(2)}</blockquote>;
    }
    if (line.startsWith('- ')) {
      return <li key={key}>{line.slice(2)}</li>;
    }
    if (!line.trim()) {
      return <br key={key} />;
    }
    return <p key={key}>{line}</p>;
  });
}

export default function NoteEditor(props: PluginSurfaceProps) {
  const initialNoteId = String((props.state?.note_id as string | undefined) || '');
  const [noteId, setNoteId] = useState(initialNoteId);
  const [notebookId, setNotebookId] = useState('');
  const [title, setTitle] = useState('');
  const [content, setContent] = useState('');
  const [topics, setTopics] = useState('');
  const [tags, setTags] = useState('');
  const [preview, setPreview] = useState(false);
  const [status, setStatus] = useState('');
  const [busy, setBusy] = useState(false);
  const latestDraft = useRef({ noteId: '', notebookId: '', title: '', content: '', topics: '', tags: '' });
  const savedSnapshot = useRef<NoteDraftSnapshot>({
    noteId: '',
    notebookId: '',
    title: '',
    content: '',
    topics: '',
    tags: '',
  });

  async function loadNote(id: string, signal?: AbortSignal) {
    if (!id.trim()) {
      return;
    }
    const payload = await callPlugin<NoteGetPayload>('study_note_get', { note_id: id.trim() }, signal);
    const note = payload.note;
    if (!note) {
      return;
    }
    const loaded: NoteDraftSnapshot = {
      noteId: note.id,
      notebookId: note.notebook_id || '',
      title: note.title || '',
      content: note.content || '',
      topics: listToCsv(note.topic_ids),
      tags: listToCsv(note.tags),
    };
    latestDraft.current = loaded;
    savedSnapshot.current = loaded;
    setNoteId(loaded.noteId);
    setNotebookId(loaded.notebookId);
    setTitle(loaded.title);
    setContent(loaded.content);
    setTopics(loaded.topics);
    setTags(loaded.tags);
  }

  async function saveNote() {
    const draft = latestDraft.current;
    setBusy(true);
    try {
      const payload = await callPlugin<NoteSavePayload>('study_note_upsert', {
        note_id: draft.noteId,
        notebook_id: draft.notebookId,
        title: draft.title,
        content: draft.content,
        topic_ids: csvToList(draft.topics),
        tags: csvToList(draft.tags),
      });
      if (payload.note?.id) {
        setNoteId(payload.note.id);
      }
      savedSnapshot.current = {
        ...draft,
        noteId: payload.note?.id || draft.noteId,
      };
      latestDraft.current = {
        ...latestDraft.current,
        noteId: payload.note?.id || latestDraft.current.noteId,
      };
      setStatus(text(props, 'ui.notebook.saved', 'Saved'));
    } catch (error) {
      setStatus(errorMessage(error));
    } finally {
      setBusy(false);
    }
  }

  async function expandWithAi() {
    setBusy(true);
    setStatus(text(props, 'ui.notebook.ai_working', 'AI working...'));
    try {
      const payload = await callPlugin<{ content?: string }>(
        'study_note_ai_expand',
        {
          note_id: noteId,
          content,
          topic_context: topics,
        },
        undefined,
        AI_EXPAND_POLL_TIMEOUT_MS,
      );
      if (payload.content) {
        setContent(payload.content);
      }
      setStatus(text(props, 'ui.status.reply_ready', 'Reply ready'));
    } catch (error) {
      setStatus(errorMessage(error));
    } finally {
      setBusy(false);
    }
  }

  function handleKeyDown(event: any) {
    const textarea = event.currentTarget as HTMLTextAreaElement;
    if (!event.ctrlKey && !event.metaKey) {
      return;
    }
    const key = String(event.key || '').toLowerCase();
    if (key === 's') {
      event.preventDefault();
      void saveNote();
      return;
    }
    if (key !== 'b' && key !== 'i' && key !== 'k') {
      return;
    }
    event.preventDefault();
    const wrap = key === 'b' ? ['**', '**'] : key === 'i' ? ['_', '_'] : ['[', '](url)'];
    const { next, cursor } = insertWrap(textarea, content, wrap[0], wrap[1]);
    setContent(next);
    window.setTimeout(() => {
      textarea.focus();
      textarea.setSelectionRange(cursor, cursor);
    }, 0);
  }

  useEffect(() => {
    latestDraft.current = { noteId, notebookId, title, content, topics, tags };
  }, [noteId, notebookId, title, content, topics, tags]);

  useEffect(() => {
    const controller = new AbortController();
    loadNote(initialNoteId, controller.signal).catch((error) => {
      if (!controller.signal.aborted) {
        setStatus(errorMessage(error));
      }
    });
    return () => {
      controller.abort();
      const draft = latestDraft.current;
      const snap = savedSnapshot.current;
      const dirty =
        draft.notebookId !== snap.notebookId ||
        draft.title !== snap.title ||
        draft.content !== snap.content ||
        draft.topics !== snap.topics ||
        draft.tags !== snap.tags;
      if (dirty && (draft.title.trim() || draft.content.trim())) {
        void callPlugin('study_note_upsert', {
          note_id: draft.noteId,
          notebook_id: draft.notebookId,
          title: draft.title,
          content: draft.content,
          topic_ids: csvToList(draft.topics),
          tags: csvToList(draft.tags),
        }).catch((error) => {
          console.error('[NoteEditor] Auto-save on unmount failed', {
            error,
            noteId: draft.noteId,
            notebookId: draft.notebookId,
          });
        });
      }
    };
  }, []);

  return (
    <div className="study-panel study-notebook-editor">
      <header className="study-panel__header">
        <div>
          <h1>{text(props, 'ui.surface.note_editor', 'Note Editor')}</h1>
          <span>{status || (noteId ? noteId : text(props, 'ui.notebook.new_note', 'New note'))}</span>
        </div>
        <div className="study-panel__toolbar">
          <button type="button" disabled={busy} onClick={() => setPreview(!preview)}>
            {preview ? text(props, 'ui.notebook.edit', 'Edit') : text(props, 'ui.notebook.preview', 'Preview')}
          </button>
          <button type="button" disabled={busy} onClick={expandWithAi}>
            {text(props, 'ui.notebook.ai_expand', 'AI Expand')}
          </button>
          <button type="button" disabled={busy} onClick={saveNote}>
            {text(props, 'ui.button.save', 'Save')}
          </button>
        </div>
      </header>
      <section className="study-panel__state study-notebook-editor__meta">
        <label>
          <span>{text(props, 'ui.label.title', 'Title')}</span>
          <input value={title} disabled={busy} onChange={(event) => setTitle(event.target.value)} />
        </label>
        <label>
          <span>{text(props, 'ui.notebook.notebook_id', 'Notebook')}</span>
          <input value={notebookId} disabled={busy} onChange={(event) => setNotebookId(event.target.value)} />
        </label>
        <label>
          <span>{text(props, 'ui.notebook.topics', 'Topics')}</span>
          <input value={topics} disabled={busy} onChange={(event) => setTopics(event.target.value)} />
        </label>
        <label>
          <span>{text(props, 'ui.notebook.tags', 'Tags')}</span>
          <input value={tags} disabled={busy} onChange={(event) => setTags(event.target.value)} />
        </label>
      </section>
      {preview ? (
        <main className="study-note-preview">{markdownPreview(content)}</main>
      ) : (
        <textarea
          className="study-note-textarea"
          value={content}
          disabled={busy}
          onChange={(event) => setContent(event.target.value)}
          onKeyDown={handleKeyDown}
        />
      )}
    </div>
  );
}
