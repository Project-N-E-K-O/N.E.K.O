"""Synchronous SQLite store for imported tactical documents.

Deliberately not the SDK's `self.db`: that API is async, and the retrieval query
runs inside `_evaluate` on the transport thread, which cannot await. A plain
`sqlite3` connection guarded by a lock is the same pattern
`plugin/plugins/study_companion/store.py` uses, and it keeps the hot path
synchronous.

Nothing here records where a document came from. Only a generated id, the title
and a content hash are stored, so importing from a private folder does not leak
that path into the database or the panel.
"""

from __future__ import annotations

import sqlite3
import threading
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from .tokenize import term_frequencies

# Front matter keys that may become retrieval tags. Anything else is rejected at
# import time rather than stored and ignored.
TAG_KINDS = ("maps", "ships", "classes", "modes", "topics")

_SCHEMA = (
    """
    CREATE TABLE IF NOT EXISTS documents (
        doc_id      TEXT PRIMARY KEY,
        title       TEXT NOT NULL,
        sha256      TEXT NOT NULL UNIQUE,
        size_bytes  INTEGER NOT NULL,
        chunk_count INTEGER NOT NULL DEFAULT 0,
        imported_at REAL NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS doc_tags (
        doc_id TEXT NOT NULL,
        kind   TEXT NOT NULL,
        value  TEXT NOT NULL,
        PRIMARY KEY (doc_id, kind, value)
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_doc_tags_lookup ON doc_tags(kind, value)",
    """
    CREATE TABLE IF NOT EXISTS chunks (
        chunk_id    INTEGER PRIMARY KEY AUTOINCREMENT,
        doc_id      TEXT NOT NULL,
        ordinal     INTEGER NOT NULL,
        heading     TEXT NOT NULL DEFAULT '',
        text        TEXT NOT NULL,
        token_count INTEGER NOT NULL DEFAULT 0,
        indexed     INTEGER NOT NULL DEFAULT 0
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_chunks_doc ON chunks(doc_id)",
    "CREATE INDEX IF NOT EXISTS idx_chunks_indexed ON chunks(indexed)",
    """
    CREATE TABLE IF NOT EXISTS chunk_terms (
        term     TEXT NOT NULL,
        chunk_id INTEGER NOT NULL,
        tf       INTEGER NOT NULL
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_chunk_terms_term ON chunk_terms(term)",
    "CREATE INDEX IF NOT EXISTS idx_chunk_terms_chunk ON chunk_terms(chunk_id)",
    """
    CREATE TABLE IF NOT EXISTS prompt_revisions (
        revision_id TEXT PRIMARY KEY,
        created_at  REAL NOT NULL,
        base        TEXT NOT NULL,
        urgent      TEXT NOT NULL,
        normal      TEXT NOT NULL,
        active      INTEGER NOT NULL DEFAULT 0,
        note        TEXT NOT NULL DEFAULT ''
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_prompt_revisions_created ON prompt_revisions(created_at)",
)

# SQLite caps host parameters per statement; chunk large IN () lists.
_PARAM_BATCH = 400


def new_id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:12]}"


class KnowledgeQuotaExceeded(Exception):
    """Raised inside the atomic insert transaction when a corpus limit wins."""

    def __init__(self, kind: str, limit: int) -> None:
        self.kind = kind
        self.limit = int(limit)
        super().__init__(f"{kind} quota exceeded ({limit})")


@dataclass(frozen=True)
class AddDocumentResult:
    doc_id: str
    inserted: bool
    indexed_chunks: int


class KnowledgeStore:
    """All tactical-document and prompt-revision persistence, in one file."""

    def __init__(self, db_path: Path, *, logger=None) -> None:
        self.db_path = Path(db_path)
        self.logger = logger
        self._lock = threading.RLock()
        self._conn: sqlite3.Connection | None = None

    # ------------------------------------------------------------------
    def open(self) -> None:
        with self._lock:
            if self._conn is not None:
                return
            self.db_path.parent.mkdir(parents=True, exist_ok=True)
            conn = sqlite3.connect(
                str(self.db_path), check_same_thread=False, timeout=10.0)
            conn.row_factory = sqlite3.Row
            try:
                conn.execute("PRAGMA journal_mode = WAL")
            except sqlite3.Error:
                # A read-only or network volume may refuse WAL; the default
                # journal still works, just with coarser locking.
                pass
            for statement in _SCHEMA:
                conn.execute(statement)
            conn.commit()
            self._conn = conn

    def close(self) -> None:
        with self._lock:
            if self._conn is not None:
                try:
                    self._conn.close()
                finally:
                    self._conn = None

    def _require(self) -> sqlite3.Connection:
        if self._conn is None:
            self.open()
        assert self._conn is not None
        return self._conn

    # ------------------------------------------------------------------ 文档
    def has_hash(self, sha256: str) -> str | None:
        """Returns the existing `doc_id` for this content, if any."""
        with self._lock:
            row = self._require().execute(
                "SELECT doc_id FROM documents WHERE sha256 = ?", (sha256,)
            ).fetchone()
        return str(row["doc_id"]) if row is not None else None

    def add_document(
        self,
        *,
        title: str,
        sha256: str,
        size_bytes: int,
        tags: Mapping[str, Sequence[str]],
        chunks: Sequence[Mapping[str, Any]],
        index_chunk_cap: int,
        max_documents: int,
        max_total_bytes: int,
    ) -> AddDocumentResult:
        """Atomically deduplicate, enforce quotas, and allocate index capacity."""
        doc_id = new_id("doc")
        now = time.time()
        with self._lock:
            conn = self._require()
            try:
                # This also serializes quota decisions with other connections,
                # not only threads sharing this KnowledgeStore instance.
                conn.execute("BEGIN IMMEDIATE")
                existing = conn.execute(
                    "SELECT doc_id FROM documents WHERE sha256 = ?", (sha256,)
                ).fetchone()
                if existing is not None:
                    conn.commit()
                    return AddDocumentResult(
                        doc_id=str(existing["doc_id"]),
                        inserted=False,
                        indexed_chunks=0,
                    )

                usage = conn.execute(
                    "SELECT COUNT(*) AS documents, "
                    "COALESCE(SUM(size_bytes), 0) AS total_bytes FROM documents"
                ).fetchone()
                if int(usage["documents"] or 0) >= int(max_documents):
                    raise KnowledgeQuotaExceeded("documents", max_documents)
                if int(usage["total_bytes"] or 0) + int(size_bytes) > int(max_total_bytes):
                    raise KnowledgeQuotaExceeded("total_bytes", max_total_bytes)

                indexed_row = conn.execute(
                    "SELECT COALESCE(SUM(indexed), 0) AS n FROM chunks"
                ).fetchone()
                index_budget = max(
                    0, int(index_chunk_cap) - int(indexed_row["n"] or 0))
                conn.execute(
                    "INSERT INTO documents (doc_id, title, sha256, size_bytes, "
                    "chunk_count, imported_at) VALUES (?, ?, ?, ?, ?, ?)",
                    (doc_id, title, sha256, int(size_bytes), len(chunks), now),
                )
                for kind, values in tags.items():
                    if kind not in TAG_KINDS:
                        continue
                    for value in values:
                        conn.execute(
                            "INSERT OR IGNORE INTO doc_tags (doc_id, kind, value) "
                            "VALUES (?, ?, ?)",
                            (doc_id, kind, value),
                        )
                remaining = max(0, int(index_budget))
                for ordinal, chunk in enumerate(chunks):
                    terms = dict(chunk.get("terms") or {})
                    indexed = 1 if remaining > 0 else 0
                    cursor = conn.execute(
                        "INSERT INTO chunks (doc_id, ordinal, heading, text, "
                        "token_count, indexed) VALUES (?, ?, ?, ?, ?, ?)",
                        (
                            doc_id,
                            ordinal,
                            str(chunk.get("heading") or ""),
                            str(chunk.get("text") or ""),
                            sum(terms.values()),
                            indexed,
                        ),
                    )
                    if not indexed:
                        continue
                    remaining -= 1
                    chunk_id = int(cursor.lastrowid)
                    conn.executemany(
                        "INSERT INTO chunk_terms (term, chunk_id, tf) VALUES (?, ?, ?)",
                        [(term, chunk_id, int(tf)) for term, tf in terms.items()],
                    )
                conn.commit()
            except Exception:
                conn.rollback()
                raise
        return AddDocumentResult(
            doc_id=doc_id,
            inserted=True,
            indexed_chunks=min(index_budget, len(chunks)),
        )

    def delete_document(
        self, doc_id: str, *, index_chunk_cap: int | None = None,
    ) -> bool:
        with self._lock:
            conn = self._require()
            try:
                conn.execute("BEGIN IMMEDIATE")
                chunk_ids = [
                    int(row["chunk_id"]) for row in conn.execute(
                        "SELECT chunk_id FROM chunks WHERE doc_id = ?", (doc_id,))
                ]
                for batch in _batched(chunk_ids, _PARAM_BATCH):
                    marks = ",".join("?" * len(batch))
                    conn.execute(
                        f"DELETE FROM chunk_terms WHERE chunk_id IN ({marks})", batch)
                conn.execute("DELETE FROM chunks WHERE doc_id = ?", (doc_id,))
                conn.execute("DELETE FROM doc_tags WHERE doc_id = ?", (doc_id,))
                cursor = conn.execute(
                    "DELETE FROM documents WHERE doc_id = ?", (doc_id,))
                if cursor.rowcount > 0 and index_chunk_cap is not None:
                    self._backfill_index(conn, index_chunk_cap)
                conn.commit()
            except Exception:
                conn.rollback()
                raise
        return cursor.rowcount > 0

    @staticmethod
    def _backfill_index(conn: sqlite3.Connection, index_chunk_cap: int) -> None:
        used_row = conn.execute(
            "SELECT COALESCE(SUM(indexed), 0) AS n FROM chunks"
        ).fetchone()
        remaining = max(0, int(index_chunk_cap) - int(used_row["n"] or 0))
        if remaining <= 0:
            return

        rows = conn.execute(
            "SELECT c.chunk_id, c.heading, c.text "
            "FROM chunks c JOIN documents d ON d.doc_id = c.doc_id "
            "WHERE c.indexed = 0 "
            "ORDER BY d.imported_at, c.ordinal, c.chunk_id LIMIT ?",
            (remaining,),
        ).fetchall()
        for row in rows:
            chunk_id = int(row["chunk_id"])
            terms = dict(term_frequencies(
                f"{row['heading']}\n{row['text']}"))
            if terms:
                conn.executemany(
                    "INSERT INTO chunk_terms (term, chunk_id, tf) VALUES (?, ?, ?)",
                    [(term, chunk_id, int(tf)) for term, tf in terms.items()],
                )
            conn.execute(
                "UPDATE chunks SET indexed = 1, token_count = ? "
                "WHERE chunk_id = ?",
                (sum(terms.values()), chunk_id),
            )

    def clear_documents(self) -> int:
        with self._lock:
            conn = self._require()
            try:
                removed = conn.execute("SELECT COUNT(*) AS n FROM documents").fetchone()
                conn.execute("DELETE FROM chunk_terms")
                conn.execute("DELETE FROM chunks")
                conn.execute("DELETE FROM doc_tags")
                conn.execute("DELETE FROM documents")
                conn.commit()
            except Exception:
                conn.rollback()
                raise
        return int(removed["n"]) if removed is not None else 0

    def list_documents(self) -> list[dict[str, Any]]:
        with self._lock:
            conn = self._require()
            rows = conn.execute(
                "SELECT doc_id, title, size_bytes, chunk_count, imported_at "
                "FROM documents ORDER BY imported_at DESC"
            ).fetchall()
            tag_rows = conn.execute(
                "SELECT doc_id, kind, value FROM doc_tags").fetchall()
            indexed_rows = conn.execute(
                "SELECT doc_id, SUM(indexed) AS indexed FROM chunks GROUP BY doc_id"
            ).fetchall()

        tags: dict[str, list[str]] = {}
        for row in tag_rows:
            tags.setdefault(str(row["doc_id"]), []).append(
                f"{row['kind']}:{row['value']}")
        indexed = {str(row["doc_id"]): int(row["indexed"] or 0) for row in indexed_rows}
        return [
            {
                "doc_id": str(row["doc_id"]),
                "title": str(row["title"]),
                "size_bytes": int(row["size_bytes"]),
                "chunk_count": int(row["chunk_count"]),
                "indexed_chunks": indexed.get(str(row["doc_id"]), 0),
                "imported_at": float(row["imported_at"]),
                "tags": sorted(tags.get(str(row["doc_id"]), [])),
            }
            for row in rows
        ]

    def stats(self) -> dict[str, Any]:
        with self._lock:
            conn = self._require()
            docs = conn.execute(
                "SELECT COUNT(*) AS n, COALESCE(SUM(size_bytes), 0) AS bytes "
                "FROM documents").fetchone()
            chunks = conn.execute(
                "SELECT COUNT(*) AS n, COALESCE(SUM(indexed), 0) AS indexed, "
                "COALESCE(SUM(token_count), 0) AS tokens, "
                "COALESCE(SUM(CASE WHEN indexed = 1 THEN token_count ELSE 0 END), 0) "
                "AS indexed_tokens FROM chunks").fetchone()
            postings = conn.execute(
                "SELECT COUNT(*) AS n FROM chunk_terms").fetchone()
        indexed_chunks = int(chunks["indexed"] or 0)
        return {
            "documents": int(docs["n"] or 0),
            "total_bytes": int(docs["bytes"] or 0),
            "chunks": int(chunks["n"] or 0),
            "indexed_chunks": indexed_chunks,
            "postings": int(postings["n"] or 0),
            "total_tokens": int(chunks["tokens"] or 0),
            "indexed_tokens": int(chunks["indexed_tokens"] or 0),
        }

    def index_capacity_used(self) -> int:
        with self._lock:
            row = self._require().execute(
                "SELECT COALESCE(SUM(indexed), 0) AS n FROM chunks").fetchone()
        return int(row["n"] or 0)

    # ------------------------------------------------------------------ 检索
    def chunk_ids_for_tags(self, tags: Sequence[tuple[str, str]]) -> dict[int, int]:
        """Chunk id -> number of distinct tags its document matched."""
        if not tags:
            return {}
        hits: dict[str, int] = {}
        with self._lock:
            conn = self._require()
            for kind, value in tags:
                rows = conn.execute(
                    "SELECT doc_id FROM doc_tags WHERE kind = ? AND value = ?",
                    (kind, value),
                ).fetchall()
                for row in rows:
                    doc_id = str(row["doc_id"])
                    hits[doc_id] = hits.get(doc_id, 0) + 1
            if not hits:
                return {}
            out: dict[int, int] = {}
            doc_ids = list(hits)
            for batch in _batched(doc_ids, _PARAM_BATCH):
                marks = ",".join("?" * len(batch))
                rows = conn.execute(
                    f"SELECT chunk_id, doc_id FROM chunks WHERE doc_id IN ({marks})",
                    batch,
                ).fetchall()
                for row in rows:
                    out[int(row["chunk_id"])] = hits[str(row["doc_id"])]
        return out

    def postings_for_terms(
        self, terms: Sequence[str]
    ) -> tuple[dict[int, dict[str, int]], dict[str, int]]:
        """Returns (chunk_id -> {term: tf}, term -> document frequency)."""
        if not terms:
            return {}, {}
        unique = list(dict.fromkeys(terms))
        postings: dict[int, dict[str, int]] = {}
        document_frequency: dict[str, int] = {}
        with self._lock:
            conn = self._require()
            for batch in _batched(unique, _PARAM_BATCH):
                marks = ",".join("?" * len(batch))
                rows = conn.execute(
                    f"SELECT term, chunk_id, tf FROM chunk_terms "
                    f"WHERE term IN ({marks})",
                    batch,
                ).fetchall()
                for row in rows:
                    term = str(row["term"])
                    chunk_id = int(row["chunk_id"])
                    postings.setdefault(chunk_id, {})[term] = int(row["tf"])
                    document_frequency[term] = document_frequency.get(term, 0) + 1
        return postings, document_frequency

    def load_chunks(self, chunk_ids: Iterable[int]) -> dict[int, dict[str, Any]]:
        ids = [int(value) for value in chunk_ids]
        if not ids:
            return {}
        out: dict[int, dict[str, Any]] = {}
        with self._lock:
            conn = self._require()
            for batch in _batched(ids, _PARAM_BATCH):
                marks = ",".join("?" * len(batch))
                rows = conn.execute(
                    f"SELECT c.chunk_id, c.doc_id, c.heading, c.text, c.token_count, "
                    f"d.title FROM chunks c JOIN documents d ON d.doc_id = c.doc_id "
                    f"WHERE c.chunk_id IN ({marks})",
                    batch,
                ).fetchall()
                for row in rows:
                    out[int(row["chunk_id"])] = {
                        "chunk_id": int(row["chunk_id"]),
                        "doc_id": str(row["doc_id"]),
                        "title": str(row["title"]),
                        "heading": str(row["heading"]),
                        "text": str(row["text"]),
                        "token_count": int(row["token_count"] or 0),
                    }
        return out

    def tags_for_documents(self, doc_ids: Sequence[str]) -> dict[str, list[str]]:
        if not doc_ids:
            return {}
        out: dict[str, list[str]] = {}
        with self._lock:
            conn = self._require()
            for batch in _batched(list(dict.fromkeys(doc_ids)), _PARAM_BATCH):
                marks = ",".join("?" * len(batch))
                rows = conn.execute(
                    f"SELECT doc_id, kind, value FROM doc_tags "
                    f"WHERE doc_id IN ({marks})",
                    batch,
                ).fetchall()
                for row in rows:
                    out.setdefault(str(row["doc_id"]), []).append(
                        f"{row['kind']}:{row['value']}")
        return {key: sorted(values) for key, values in out.items()}

    # ------------------------------------------------------------------ 提示词
    def list_revisions(self) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._require().execute(
                "SELECT revision_id, created_at, active, note, "
                "LENGTH(base) AS base_len, LENGTH(urgent) AS urgent_len, "
                "LENGTH(normal) AS normal_len FROM prompt_revisions "
                "ORDER BY created_at DESC"
            ).fetchall()
        return [
            {
                "revision_id": str(row["revision_id"]),
                "created_at": float(row["created_at"]),
                "active": bool(row["active"]),
                "note": str(row["note"]),
                "lengths": {
                    "base": int(row["base_len"] or 0),
                    "urgent": int(row["urgent_len"] or 0),
                    "normal": int(row["normal_len"] or 0),
                },
            }
            for row in rows
        ]

    def get_revision(self, revision_id: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._require().execute(
                "SELECT revision_id, created_at, base, urgent, normal, active, note "
                "FROM prompt_revisions WHERE revision_id = ?",
                (revision_id,),
            ).fetchone()
        return _revision_row(row)

    def get_active_revision(self) -> dict[str, Any] | None:
        with self._lock:
            row = self._require().execute(
                "SELECT revision_id, created_at, base, urgent, normal, active, note "
                "FROM prompt_revisions WHERE active = 1 "
                "ORDER BY created_at DESC LIMIT 1"
            ).fetchone()
        return _revision_row(row)

    def save_revision(
        self,
        *,
        base: str,
        urgent: str,
        normal: str,
        note: str = "",
        keep: int = 20,
    ) -> dict[str, Any]:
        """Store a new revision, make it active, and prune the oldest."""
        revision_id = new_id("rev")
        now = time.time()
        with self._lock:
            conn = self._require()
            try:
                conn.execute("UPDATE prompt_revisions SET active = 0")
                conn.execute(
                    "INSERT INTO prompt_revisions (revision_id, created_at, base, "
                    "urgent, normal, active, note) VALUES (?, ?, ?, ?, ?, 1, ?)",
                    (revision_id, now, base, urgent, normal, note),
                )
                self._prune_revisions(conn, keep)
                conn.commit()
            except Exception:
                conn.rollback()
                raise
        return {
            "revision_id": revision_id,
            "created_at": now,
            "base": base,
            "urgent": urgent,
            "normal": normal,
            "active": True,
            "note": note,
        }

    def activate_revision(self, revision_id: str) -> dict[str, Any] | None:
        with self._lock:
            conn = self._require()
            existing = conn.execute(
                "SELECT revision_id FROM prompt_revisions WHERE revision_id = ?",
                (revision_id,),
            ).fetchone()
            if existing is None:
                return None
            try:
                conn.execute("UPDATE prompt_revisions SET active = 0")
                conn.execute(
                    "UPDATE prompt_revisions SET active = 1 WHERE revision_id = ?",
                    (revision_id,),
                )
                conn.commit()
            except Exception:
                conn.rollback()
                raise
        return self.get_revision(revision_id)

    def reset_revisions(self) -> None:
        """Drop every stored revision so the built-in defaults take over."""
        with self._lock:
            conn = self._require()
            try:
                conn.execute("DELETE FROM prompt_revisions")
                conn.commit()
            except Exception:
                conn.rollback()
                raise

    @staticmethod
    def _prune_revisions(conn: sqlite3.Connection, keep: int) -> None:
        limit = max(1, int(keep))
        stale = conn.execute(
            "SELECT revision_id FROM prompt_revisions "
            "ORDER BY created_at DESC LIMIT -1 OFFSET ?",
            (limit,),
        ).fetchall()
        for row in stale:
            conn.execute(
                "DELETE FROM prompt_revisions WHERE revision_id = ?",
                (str(row["revision_id"]),),
            )


def _revision_row(row) -> dict[str, Any] | None:
    if row is None:
        return None
    return {
        "revision_id": str(row["revision_id"]),
        "created_at": float(row["created_at"]),
        "base": str(row["base"]),
        "urgent": str(row["urgent"]),
        "normal": str(row["normal"]),
        "active": bool(row["active"]),
        "note": str(row["note"]),
    }


def _batched(values: Sequence[Any], size: int):
    for start in range(0, len(values), size):
        yield list(values[start:start + size])


__all__ = ["TAG_KINDS", "KnowledgeStore", "new_id"]
