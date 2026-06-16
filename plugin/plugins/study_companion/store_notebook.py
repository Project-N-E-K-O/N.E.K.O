from __future__ import annotations

from dataclasses import asdict
import re
import uuid
from typing import Any, Literal

from .models import NotebookMeta, NoteItem, NoteSearchResult, utc_now_iso

try:
    import jieba
except Exception:  # pragma: no cover - optional during partial dev installs.
    jieba = None  # type: ignore[assignment]


NotebookFilter = Literal["all", "unfiled", "specific"]
_MARKDOWN_FENCE_RE = re.compile(r"```[^\n]*\n?(.*?)```", re.DOTALL)
_MARKDOWN_INLINE_CODE_RE = re.compile(r"`([^`]*)`")
_MARKDOWN_IMAGE_RE = re.compile(r"!\[[^\]]*\]\([^)]+\)")
_MARKDOWN_LINK_RE = re.compile(r"\[([^\]]+)\]\([^)]+\)")
# Block markers (blockquote / list / heading) are only stripped at the start of
# a line so inline math like ``x > 0`` or ``a - b`` stays searchable. Inline
# ``*`` / ``~`` emphasis runs are stripped anywhere, but ``_`` only at word
# boundaries so identifiers like ``widget_factory`` keep their underscores.
_MARKDOWN_MARKUP_RE = re.compile(
    r"^[ \t]*[>#*+\-]{1,3}\s+|[*~]{1,3}|(?<![0-9A-Za-z])_{1,3}|_{1,3}(?![0-9A-Za-z])|^#{1,6}\s*",
    re.MULTILINE,
)
_SEARCH_TOKEN_RE = re.compile(r"[\w\u4e00-\u9fff\u3040-\u30ff\uac00-\ud7af]+", re.UNICODE)
_SNIPPET_CHARS = 200
_UNSET = object()


def _strip_markdown(content: str) -> str:
    text = str(content or "")
    # Keep the inner code text searchable; only drop the ``` fences + lang tag.
    text = _MARKDOWN_FENCE_RE.sub(lambda m: " " + m.group(1) + " ", text)
    text = _MARKDOWN_IMAGE_RE.sub(" ", text)
    text = _MARKDOWN_LINK_RE.sub(r"\1", text)
    text = _MARKDOWN_INLINE_CODE_RE.sub(r"\1", text)
    text = _MARKDOWN_MARKUP_RE.sub(" ", text)
    # Only strip things that look like real HTML/XML tags, so common note
    # content like ``a < b``, ``x > 0`` stays searchable instead of being eaten.
    text = re.sub(r"</?[A-Za-z][^>]*>", " ", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def _snippet(content_plain: str) -> str:
    return str(content_plain or "").strip()[:_SNIPPET_CHARS]


def _word_count(content_plain: str) -> int:
    text = str(content_plain or "").strip()
    if not text:
        return 0
    words = re.findall(r"[A-Za-z0-9_]+|[\u4e00-\u9fff\u3040-\u30ff\uac00-\ud7af]", text)
    return len(words)


def _normalize_string_list(value: object, *, limit: int = 50) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        raw_items = re.split(r"[,，\s]+", value)
    elif isinstance(value, (list, tuple, set)):
        raw_items = list(value)
    else:
        raw_items = [value]
    result: list[str] = []
    seen: set[str] = set()
    for item in raw_items:
        text = str(item or "").strip()
        if not text or text in seen:
            continue
        result.append(text)
        seen.add(text)
        if len(result) >= limit:
            break
    return result


def _normalize_note_title(title: str, content_plain: str = "") -> str:
    text = str(title or "").strip()
    if text:
        return text[:160]
    first_line = next(
        (line.strip() for line in str(content_plain or "").splitlines() if line.strip()),
        "",
    )
    return (first_line or "Untitled Note")[:160]


def _nullable_text(value: object) -> str | None:
    text = str(value or "").strip()
    return text or None


def _search_terms(query: str) -> list[str]:
    text = str(query or "").strip()
    if not text:
        return []
    terms = _SEARCH_TOKEN_RE.findall(text)
    if jieba is not None:
        try:
            terms.extend(str(item).strip() for item in jieba.cut(text) if str(item).strip())
        except Exception:
            # Segmentation is best-effort; on failure keep the regex tokens
            # (and the `terms or [text]` fallback below) so search still works.
            pass
    result: list[str] = []
    seen: set[str] = set()
    for term in terms or [text]:
        if term and term not in seen:
            result.append(term)
            seen.add(term)
    return result[:12]


def _fts_query(query: str) -> str:
    terms = _search_terms(query)
    if not terms:
        return ""
    return " OR ".join('"' + term.replace('"', '""') + '"' for term in terms)


def _like_pattern(query: str) -> str:
    text = str(query or "").strip()
    escaped = text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


class NotebookStore:
    def __init__(self, store: Any) -> None:
        self.store = store

    def _warn(self, message: str, *args: Any) -> None:
        warning = getattr(getattr(self.store, "_logger", None), "warning", None)
        if callable(warning):
            try:
                warning(message, *args)
            except Exception:
                # Diagnostics must never break the notebook store's main flow.
                pass

    def create_notebook(
        self, *, name: str, description: str = "", sort_order: int = 0
    ) -> NotebookMeta:
        name_text = str(name or "").strip()
        if not name_text:
            raise ValueError("notebook name is required")
        notebook_id = str(uuid.uuid4())
        with self.store._lock:
            conn = self.store._require_conn()
            conn.execute(
                """
                INSERT INTO notebooks (id, name, description, sort_order, created_at, updated_at)
                VALUES (?, ?, ?, ?, datetime('now'), datetime('now'))
                """,
                (
                    notebook_id,
                    name_text,
                    str(description or "").strip(),
                    int(sort_order or 0),
                ),
            )
            conn.commit()
        notebook = self.get_notebook(notebook_id)
        if notebook is None:
            raise RuntimeError("notebook create failed")
        return notebook

    def get_notebook(self, notebook_id: str) -> NotebookMeta | None:
        with self.store._lock:
            row = (
                self.store._require_conn()
                .execute(
                    """
                    SELECT n.*,
                           COUNT(notes.id) AS note_count
                    FROM notebooks n
                    LEFT JOIN notes ON notes.notebook_id = n.id
                    WHERE n.id = ?
                    GROUP BY n.id
                    """,
                    (str(notebook_id or ""),),
                )
                .fetchone()
            )
        return self._notebook_from_row(row)

    def list_notebooks(self, *, limit: int = 100) -> list[NotebookMeta]:
        with self.store._lock:
            rows = (
                self.store._require_conn()
                .execute(
                    """
                    SELECT n.*,
                           COUNT(notes.id) AS note_count
                    FROM notebooks n
                    LEFT JOIN notes ON notes.notebook_id = n.id
                    GROUP BY n.id
                    ORDER BY n.sort_order ASC, n.updated_at DESC, n.name ASC
                    LIMIT ?
                    """,
                    (max(1, int(limit or 100)),),
                )
                .fetchall()
            )
        return [item for item in (self._notebook_from_row(row) for row in rows) if item]

    def update_notebook(
        self,
        notebook_id: str,
        *,
        name: str | None = None,
        description: str | None = None,
        sort_order: int | None = None,
    ) -> NotebookMeta:
        assignments: list[str] = []
        params: list[Any] = []
        if name is not None:
            name_text = str(name or "").strip()
            if not name_text:
                raise ValueError("notebook name is required")
            assignments.append("name = ?")
            params.append(name_text)
        if description is not None:
            assignments.append("description = ?")
            params.append(str(description or "").strip())
        if sort_order is not None:
            assignments.append("sort_order = ?")
            params.append(int(sort_order))
        if assignments:
            assignments.append("updated_at = datetime('now')")
            with self.store._lock:
                conn = self.store._require_conn()
                conn.execute(
                    f"UPDATE notebooks SET {', '.join(assignments)} WHERE id = ?",
                    (*params, str(notebook_id or "")),
                )
                conn.commit()
        notebook = self.get_notebook(notebook_id)
        if notebook is None:
            raise ValueError("notebook not found")
        return notebook

    def delete_notebook(self, notebook_id: str) -> dict[str, int]:
        key = str(notebook_id or "").strip()
        if not key:
            return {"deleted": 0, "notes_unlinked": 0}
        with self.store._lock:
            conn = self.store._require_conn()
            before = conn.execute(
                "SELECT COUNT(*) AS count FROM notes WHERE notebook_id = ?", (key,)
            ).fetchone()
            conn.execute("UPDATE notes SET notebook_id = NULL WHERE notebook_id = ?", (key,))
            cursor = conn.execute("DELETE FROM notebooks WHERE id = ?", (key,))
            conn.commit()
        return {
            "deleted": int(cursor.rowcount or 0),
            "notes_unlinked": int(before["count"] if before is not None else 0),
        }

    def create_note(
        self,
        *,
        notebook_id: str | None = None,
        title: str = "",
        content: str = "",
        is_ai_generated: bool = False,
        source_type: str = "manual",
        source_ref: str = "",
        topic_ids: object = None,
        tags: object = None,
        note_id: str | None = None,
    ) -> NoteItem:
        content_text = str(content or "")
        content_plain = _strip_markdown(content_text)
        title_text = _normalize_note_title(title, content_plain)
        note_key = str(note_id or uuid.uuid4()).strip()
        if not note_key:
            note_key = str(uuid.uuid4())
        with self.store._lock:
            conn = self.store._require_conn()
            conn.execute(
                """
                INSERT INTO notes (
                    id, notebook_id, title, content, content_plain, snippet,
                    is_ai_generated, source_type, source_ref, topic_ids, tags,
                    word_count, created_at, updated_at, edited_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'), datetime('now'), datetime('now'))
                """,
                (
                    note_key,
                    _nullable_text(notebook_id),
                    title_text,
                    content_text,
                    content_plain,
                    _snippet(content_plain),
                    1 if is_ai_generated else 0,
                    str(source_type or "manual").strip() or "manual",
                    str(source_ref or "").strip(),
                    self.store._json_dumps(_normalize_string_list(topic_ids)),
                    self.store._json_dumps(_normalize_string_list(tags)),
                    _word_count(content_plain),
                ),
            )
            conn.commit()
        note = self.get_note(note_key)
        if note is None:
            raise RuntimeError("note create failed")
        return note

    def get_note(self, note_id: str) -> NoteItem | None:
        with self.store._lock:
            row = (
                self.store._require_conn()
                .execute("SELECT * FROM notes WHERE id = ?", (str(note_id or ""),))
                .fetchone()
            )
        return self._note_from_row(row)

    def get_notes_by_ids(self, note_ids: object) -> list[NoteItem]:
        ids = _normalize_string_list(note_ids, limit=200)
        if not ids:
            return []
        placeholders = ",".join("?" for _ in ids)
        with self.store._lock:
            rows = (
                self.store._require_conn()
                .execute(f"SELECT * FROM notes WHERE id IN ({placeholders})", ids)
                .fetchall()
            )
        notes_by_id = {
            note.id: note
            for note in (self._note_from_row(row) for row in rows)
            if note is not None
        }
        return [notes_by_id[note_id] for note_id in ids if note_id in notes_by_id]

    def upsert_note(
        self,
        *,
        note_id: str | None = None,
        notebook_id: object = _UNSET,
        title: str | None = None,
        content: str | None = None,
        topic_ids: object = _UNSET,
        tags: object = _UNSET,
        is_ai_generated: bool | None = None,
        source_type: str | None = None,
        source_ref: str | None = None,
    ) -> NoteItem:
        note_key = str(note_id or "").strip()
        existing = self.get_note(note_key) if note_key else None
        if existing is None:
            return self.create_note(
                notebook_id=None if notebook_id is _UNSET else _nullable_text(notebook_id),
                title=title or "",
                content=content or "",
                is_ai_generated=bool(is_ai_generated),
                source_type=source_type or "manual",
                source_ref=source_ref or "",
                topic_ids=None if topic_ids is _UNSET else topic_ids,
                tags=None if tags is _UNSET else tags,
                note_id=note_key or None,
            )
        if source_type is not None and str(source_type or "").strip():
            requested_source_type = str(source_type or "").strip()
            if requested_source_type != existing.source_type:
                self._warn(
                    "study note source_type update ignored: {} -> {}",
                    existing.source_type,
                    requested_source_type,
                )
        if source_ref is not None and str(source_ref or "").strip():
            requested_source_ref = str(source_ref or "").strip()
            if requested_source_ref != existing.source_ref:
                self._warn(
                    "study note source_ref update ignored: {} -> {}",
                    existing.source_ref,
                    requested_source_ref,
                )
        next_notebook_id = (
            existing.notebook_id if notebook_id is _UNSET else _nullable_text(notebook_id)
        )
        content_text = str(content if content is not None else existing.content)
        content_plain = _strip_markdown(content_text)
        title_text = _normalize_note_title(
            existing.title if title is None else title,
            content_plain,
        )
        next_is_ai_generated = (
            existing.is_ai_generated
            if is_ai_generated is None
            else bool(is_ai_generated)
        )
        topic_values = existing.topic_ids if topic_ids is _UNSET else topic_ids
        tag_values = existing.tags if tags is _UNSET else tags
        with self.store._lock:
            conn = self.store._require_conn()
            conn.execute(
                """
                UPDATE notes
                SET notebook_id = ?,
                    title = ?,
                    content = ?,
                    content_plain = ?,
                    snippet = ?,
                    topic_ids = ?,
                    tags = ?,
                    is_ai_generated = ?,
                    word_count = ?,
                    updated_at = datetime('now'),
                    edited_at = datetime('now')
                WHERE id = ?
                """,
                (
                    next_notebook_id,
                    title_text,
                    content_text,
                    content_plain,
                    _snippet(content_plain),
                    self.store._json_dumps(_normalize_string_list(topic_values)),
                    self.store._json_dumps(_normalize_string_list(tag_values)),
                    1 if next_is_ai_generated else 0,
                    _word_count(content_plain),
                    note_key,
                ),
            )
            conn.commit()
        updated = self.get_note(note_key)
        if updated is None:
            raise RuntimeError("note update failed")
        return updated

    def delete_note(self, note_id: str) -> dict[str, int]:
        with self.store._lock:
            conn = self.store._require_conn()
            cursor = conn.execute("DELETE FROM notes WHERE id = ?", (str(note_id or ""),))
            conn.commit()
        return {"deleted": int(cursor.rowcount or 0)}

    def move_note(self, note_id: str, notebook_id: str | None) -> NoteItem:
        with self.store._lock:
            conn = self.store._require_conn()
            conn.execute(
                """
                UPDATE notes
                SET notebook_id = ?, updated_at = datetime('now')
                WHERE id = ?
                """,
                (_nullable_text(notebook_id), str(note_id or "")),
            )
            conn.commit()
        note = self.get_note(note_id)
        if note is None:
            raise ValueError("note not found")
        return note

    def list_notes(
        self,
        *,
        notebook_filter: NotebookFilter = "all",
        notebook_id: str | None = None,
        topic_id: str | None = None,
        tag: str | None = None,
        search_query: str = "",
        limit: int = 50,
        include_content: bool = False,
    ) -> list[NoteItem]:
        """List notes.

        include_content defaults to False: list/search rows ship only a snippet
        (the UI fetches full bodies via get_note). Callers that need the full
        body for every row — e.g. JSON backups via export_json — pass True.

        notebook_filter controls notebook scoping explicitly:
        - "all": no notebook filter; notebook_id is ignored unless non-empty, which
          is treated as "specific" for compatibility.
        - "unfiled": only notes with notebook_id IS NULL.
        - "specific": notes under notebook_id.
        """
        safe_limit = max(1, min(5000, int(limit or 50)))
        query = str(search_query or "").strip()
        normalized_filter = self._normalize_notebook_filter(
            notebook_filter, notebook_id
        )
        if query:
            fts_notes = self._list_notes_fts(
                notebook_filter=normalized_filter,
                notebook_id=notebook_id,
                topic_id=topic_id,
                tag=tag,
                search_query=query,
                limit=safe_limit,
            )
            like_notes = self._list_notes_like(
                notebook_filter=normalized_filter,
                notebook_id=notebook_id,
                topic_id=topic_id,
                tag=tag,
                search_query=query,
                limit=safe_limit,
            )
            seen: set[str] = set()
            merged: list[NoteItem] = []
            for note in [*fts_notes, *like_notes]:
                if note.id in seen:
                    continue
                merged.append(note)
                seen.add(note.id)
                if len(merged) >= safe_limit:
                    break
            return merged
        where, params = self._filter_clauses(
            notebook_filter=normalized_filter,
            notebook_id=notebook_id,
            topic_id=topic_id,
            tag=tag,
        )
        with self.store._lock:
            rows = (
                self.store._require_conn()
                .execute(
                    f"""
                    SELECT *
                    FROM notes
                    {where}
                    ORDER BY updated_at DESC, rowid DESC
                    LIMIT ?
                    """,
                    (*params, safe_limit),
                )
                .fetchall()
            )
        return [
            item
            for item in (self._note_from_row(row, include_content=include_content) for row in rows)
            if item
        ]

    def search_all(self, query: str, *, limit: int = 20) -> NoteSearchResult:
        text = str(query or "").strip()
        safe_limit = max(1, min(100, int(limit or 20)))
        if not text:
            return {"query": "", "notes": [], "topics": [], "sessions": [], "wrong_questions": []}
        lowered = text.lower()
        notes = [asdict(note) for note in self.list_notes(search_query=text, limit=safe_limit)]
        topics = [
            topic
            for topic in self.store.list_topics(limit=500)
            # Cross-module search is intentionally capped and filtered locally.
            # If these tables grow beyond personal-library scale, replace this
            # with SQL-backed search per source.
            if lowered in " ".join(
                str(topic.get(key) or "") for key in ("id", "name", "subject", "chapter")
            ).lower()
        ][:safe_limit]
        sessions = self._search_sessions(text, limit=safe_limit)
        wrong_questions = [
            item
            for item in self.store.list_wrong_questions(limit=500)
            if lowered
            in " ".join(
                [
                    str(item.get("id") or ""),
                    str(item.get("topic_id") or ""),
                    str(item.get("question") or ""),
                    str(item.get("user_answer") or ""),
                    str(item.get("expected_answer") or ""),
                    str(item.get("error_type") or ""),
                ]
            ).lower()
        ][:safe_limit]
        return {
            "query": text,
            "notes": notes,
            "topics": topics,
            "sessions": sessions,
            "wrong_questions": wrong_questions,
        }

    def get_notes_by_topic(self, topic_id: str, *, limit: int = 50) -> list[NoteItem]:
        return self.list_notes(topic_id=topic_id, limit=limit)

    def get_notes_by_session(
        self, session_id: str, *, limit: int = 50
    ) -> list[NoteItem]:
        with self.store._lock:
            rows = (
                self.store._require_conn()
                .execute(
                    """
                    SELECT *
                    FROM notes
                    WHERE source_type = 'session' AND source_ref = ?
                    ORDER BY updated_at DESC, rowid DESC
                    LIMIT ?
                    """,
                    (str(session_id or ""), max(1, int(limit or 50))),
                )
                .fetchall()
            )
        return [item for item in (self._note_from_row(row) for row in rows) if item]

    def get_recent_notes(self, *, limit: int = 10) -> list[NoteItem]:
        return self.list_notes(limit=limit)

    def count_notes_by_topic(self) -> dict[str, int]:
        with self.store._lock:
            rows = (
                self.store._require_conn()
                .execute(
                    """
                    SELECT json_each.value AS topic_id, COUNT(*) AS count
                    FROM notes, json_each(notes.topic_ids)
                    WHERE json_each.value IS NOT NULL AND json_each.value != ''
                    GROUP BY json_each.value
                    """
                )
                .fetchall()
            )
        return {str(row["topic_id"]): int(row["count"] or 0) for row in rows}

    def source_text_for_session(self, session_id: str, *, limit: int = 20) -> str:
        session_key = str(session_id or "").strip()
        if not session_key:
            return ""
        with self.store._lock:
            row = (
                self.store._require_conn()
                .execute("SELECT * FROM sessions WHERE id = ?", (session_key,))
                .fetchone()
            )
        if row is None:
            return ""
        parts = [
            f"Session: {row['id']}",
            f"Mode: {row['mode']}",
            str(row["summary_markdown"] or "").strip(),
        ]
        safe_limit = max(1, int(limit or 20))
        with self.store._lock:
            rows = (
                self.store._require_conn()
                .execute(
                    """
                    SELECT *
                    FROM qa_records
                    WHERE session_id = ?
                    ORDER BY id DESC
                    LIMIT ?
                    """,
                    (session_key, safe_limit),
                )
                .fetchall()
            )
        records = [
            item
            for item in (self.store._qa_record_from_row(row) for row in reversed(rows))
            if item is not None
        ]
        for item in records:
            question = item.get("question")
            question_text = (
                question.get("question") if isinstance(question, dict) else str(question or "")
            )
            parts.append(f"Q: {question_text}\nA: {item.get('user_answer') or ''}")
        return "\n\n".join(part for part in parts if str(part or "").strip())

    def source_text_for_topic(self, topic_id: str, *, limit: int = 12) -> str:
        topic_key = str(topic_id or "").strip()
        if not topic_key:
            return ""
        topic = self.store.get_topic(topic_key)
        if not isinstance(topic, dict):
            return ""
        parts = [
            f"# {topic.get('name') or topic.get('id')}",
            f"Subject: {topic.get('subject') or ''}",
            f"Chapter: {topic.get('chapter') or ''}",
        ]
        for item in self.store.list_qa_records_for_topic(topic_key, limit=limit):
            question = item.get("question")
            question_text = (
                question.get("question") if isinstance(question, dict) else str(question or "")
            )
            parts.append(f"Q: {question_text}\nA: {item.get('user_answer') or ''}")
        return "\n\n".join(part for part in parts if str(part or "").strip())

    def build_notes_markdown(
        self, note_ids: object, *, title: str | None = None
    ) -> str:
        # Lazy import avoids a circular dependency (doc_exporter imports this
        # module). Escape the metadata bits the same way the regular export path
        # does so a title/tag with #, backticks, [], or newlines can't break the
        # heading/list structure; note.content is emitted verbatim.
        from .doc_exporter import escape_markdown

        notes = self.get_notes_by_ids(note_ids)
        heading = str(title or "Study Notes").strip() or "Study Notes"
        lines = [
            f"# {escape_markdown(heading)}",
            "",
            f"- Exported at: `{utc_now_iso()}`",
            f"- Notes included: {len(notes)}",
            "",
        ]
        for note in notes:
            source = f"`{escape_markdown(note.source_type)}`"
            if note.source_ref:
                source += f" / `{escape_markdown(note.source_ref)}`"
            topics = ", ".join(escape_markdown(t) for t in note.topic_ids) if note.topic_ids else "-"
            tags = ", ".join(escape_markdown(t) for t in note.tags) if note.tags else "-"
            lines.extend(
                [
                    f"## {escape_markdown(note.title)}",
                    "",
                    f"- Source: {source}",
                    f"- Topics: {topics}",
                    f"- Tags: {tags}",
                    "",
                    note.content.strip() or note.snippet or "_Empty note._",
                    "",
                ]
            )
        return "\n".join(lines).strip() + "\n"

    def _list_notes_fts(
        self,
        *,
        notebook_filter: NotebookFilter,
        notebook_id: object,
        topic_id: str | None,
        tag: str | None,
        search_query: str,
        limit: int,
    ) -> list[NoteItem]:
        match_query = _fts_query(search_query)
        if not match_query:
            return []
        where, params = self._filter_clauses(
            notebook_filter=notebook_filter,
            notebook_id=notebook_id,
            topic_id=topic_id,
            tag=tag,
            prefix="n",
        )
        fts_where = "WHERE notes_fts MATCH ?"
        if where:
            fts_where += " AND " + where.removeprefix("WHERE ")
        try:
            with self.store._lock:
                rows = (
                    self.store._require_conn()
                    .execute(
                        f"""
                        SELECT n.*
                        FROM notes n
                        JOIN notes_fts ON notes_fts.rowid = n.rowid
                        {fts_where}
                        ORDER BY bm25(notes_fts), n.updated_at DESC, n.rowid DESC
                        LIMIT ?
                        """,
                        (match_query, *params, limit),
                    )
                    .fetchall()
                )
        except Exception as exc:
            self._warn("study notebook FTS search failed; falling back to LIKE: {}", exc)
            return []
        return [
            item
            for item in (self._note_from_row(row, include_content=False) for row in rows)
            if item
        ]

    def _list_notes_like(
        self,
        *,
        notebook_filter: NotebookFilter,
        notebook_id: object,
        topic_id: str | None,
        tag: str | None,
        search_query: str,
        limit: int,
    ) -> list[NoteItem]:
        where, params = self._filter_clauses(
            notebook_filter=notebook_filter,
            notebook_id=notebook_id,
            topic_id=topic_id,
            tag=tag,
        )
        pattern = _like_pattern(search_query)
        text_clause = (
            "(title LIKE ? ESCAPE '\\' "
            "OR content_plain LIKE ? ESCAPE '\\' "
            "OR tags LIKE ? ESCAPE '\\')"
        )
        if where:
            where = f"{where} AND {text_clause}"
        else:
            where = f"WHERE {text_clause}"
        with self.store._lock:
            rows = (
                self.store._require_conn()
                .execute(
                    f"""
                    SELECT *
                    FROM notes
                    {where}
                    ORDER BY updated_at DESC, rowid DESC
                    LIMIT ?
                    """,
                    (*params, pattern, pattern, pattern, limit),
                )
                .fetchall()
            )
        return [
            item
            for item in (self._note_from_row(row, include_content=False) for row in rows)
            if item
        ]

    def _filter_clauses(
        self,
        *,
        notebook_filter: NotebookFilter = "all",
        notebook_id: object = None,
        topic_id: str | None = None,
        tag: str | None = None,
        prefix: str = "",
    ) -> tuple[str, list[Any]]:
        column_prefix = f"{prefix}." if prefix else ""
        clauses: list[str] = []
        params: list[Any] = []
        normalized_filter = self._normalize_notebook_filter(
            notebook_filter, notebook_id
        )
        if normalized_filter == "unfiled":
            clauses.append(f"{column_prefix}notebook_id IS NULL")
        elif normalized_filter == "specific":
            notebook_key = _nullable_text(notebook_id)
            if notebook_key is not None:
                clauses.append(f"{column_prefix}notebook_id = ?")
                params.append(notebook_key)
            else:
                clauses.append("1 = 0")
        topic_key = str(topic_id or "").strip()
        if topic_key:
            clauses.append(
                f"EXISTS (SELECT 1 FROM json_each({column_prefix}topic_ids) WHERE value = ?)"
            )
            params.append(topic_key)
        tag_key = str(tag or "").strip()
        if tag_key:
            clauses.append(
                f"EXISTS (SELECT 1 FROM json_each({column_prefix}tags) WHERE value = ?)"
            )
            params.append(tag_key)
        return ("WHERE " + " AND ".join(clauses) if clauses else "", params)

    @staticmethod
    def _normalize_notebook_filter(
        notebook_filter: NotebookFilter, notebook_id: object
    ) -> NotebookFilter:
        candidate = str(notebook_filter or "all").strip()
        if candidate not in {"all", "unfiled", "specific"}:
            candidate = "all"
        if candidate == "all" and _nullable_text(notebook_id) is not None:
            return "specific"
        return candidate  # type: ignore[return-value]

    def _search_sessions(self, query: str, *, limit: int) -> list[dict[str, Any]]:
        pattern = _like_pattern(query)
        with self.store._lock:
            rows = (
                self.store._require_conn()
                .execute(
                    """
                    SELECT *
                    FROM sessions
                    WHERE id LIKE ? ESCAPE '\\'
                       OR mode LIKE ? ESCAPE '\\'
                       OR summary_markdown LIKE ? ESCAPE '\\'
                    ORDER BY started_at DESC, id DESC
                    LIMIT ?
                    """,
                    (pattern, pattern, pattern, limit),
                )
                .fetchall()
            )
        return [
            {
                "id": str(row["id"]),
                "mode": str(row["mode"] or ""),
                "started_at": str(row["started_at"] or ""),
                "ended_at": str(row["ended_at"] or ""),
                "duration_minutes": float(row["duration_minutes"] or 0.0),
                "question_count": int(row["question_count"] or 0),
                "topics_touched": self.store._json_loads(row["topics_touched"], []),
                "summary_markdown": str(row["summary_markdown"] or ""),
            }
            for row in rows
        ]

    def _notebook_from_row(self, row: Any) -> NotebookMeta | None:
        if row is None:
            return None
        keys = set(row.keys())
        return NotebookMeta(
            id=str(row["id"]),
            name=str(row["name"]),
            description=str(row["description"] or ""),
            sort_order=int(row["sort_order"] or 0),
            created_at=str(row["created_at"] or ""),
            updated_at=str(row["updated_at"] or ""),
            note_count=int(row["note_count"] or 0) if "note_count" in keys else 0,
        )

    def _note_from_row(self, row: Any, *, include_content: bool = True) -> NoteItem | None:
        if row is None:
            return None
        # List/search results render only snippets and fetch the full body via
        # get_note before editing, so drop content/content_plain there to avoid
        # shipping every note body (up to the row limit) across the UI bridge.
        return NoteItem(
            id=str(row["id"]),
            notebook_id=_nullable_text(row["notebook_id"]),
            title=str(row["title"] or ""),
            content=str(row["content"] or "") if include_content else "",
            content_plain=str(row["content_plain"] or "") if include_content else "",
            snippet=str(row["snippet"] or ""),
            is_ai_generated=bool(row["is_ai_generated"]),
            source_type=str(row["source_type"] or "manual"),
            source_ref=str(row["source_ref"] or ""),
            topic_ids=_normalize_string_list(self.store._json_loads(row["topic_ids"], [])),
            tags=_normalize_string_list(self.store._json_loads(row["tags"], [])),
            word_count=int(row["word_count"] or 0),
            created_at=str(row["created_at"] or ""),
            updated_at=str(row["updated_at"] or ""),
            edited_at=str(row["edited_at"] or ""),
        )
