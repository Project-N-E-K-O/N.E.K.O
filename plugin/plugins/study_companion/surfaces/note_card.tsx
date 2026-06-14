// Mirrors plugin.plugins.study_companion.models.NoteItem JSON for hosted surfaces.
export type NoteItem = {
  id: string;
  notebook_id?: string | null;
  title: string;
  content?: string;
  snippet?: string;
  source_type?: string;
  source_ref?: string;
  topic_ids?: string[];
  tags?: string[];
  updated_at?: string;
  edited_at?: string;
  word_count?: number;
  is_ai_generated?: boolean;
};

type NoteCardProps = {
  // Not React's reserved key: hosted surfaces use a custom `h` JSX pragma, so
  // `key` passed by list callers is a regular prop and must be typed here.
  key?: string;
  note: NoteItem;
  selected?: boolean;
  onSelect?: (note: NoteItem) => void;
};

export default function NoteCard({ note, selected = false, onSelect }: NoteCardProps) {
  const topics = Array.isArray(note.topic_ids) ? note.topic_ids : [];
  const tags = Array.isArray(note.tags) ? note.tags : [];
  return (
    <button
      type="button"
      className={`study-panel__note-card${selected ? ' is-selected' : ''}`}
      onClick={() => onSelect?.(note)}
    >
      <span className="study-panel__note-title">{note.title || 'Untitled Note'}</span>
      <span className="study-panel__note-meta">
        {note.source_type || 'manual'} / {note.word_count || 0}
      </span>
      <span className="study-panel__note-snippet">{note.snippet || 'Empty note'}</span>
      <span className="study-panel__chips">
        {topics.slice(0, 3).map((topic) => (
          <span key={`topic-${topic}`} className="study-panel__chip is-topic">
            {topic}
          </span>
        ))}
        {tags.slice(0, 3).map((tag) => (
          <span key={`tag-${tag}`} className="study-panel__chip">
            #{tag}
          </span>
        ))}
      </span>
    </button>
  );
}
