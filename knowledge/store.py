# Copyright 2025-2026 Project N.E.K.O. Team
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""SQLite index of installed packs (``knowledge.db``).

Everything in this database is derived from ``registry.json`` and the raw
files under ``packs/``; losing it costs a rebuild, never user data. All methods
are synchronous and meant to run in a worker thread. Each call opens its own
connection, so readers never share a connection with the single writer.
"""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Collection, Iterable, Iterator, Mapping, Sequence

import numpy as np

from .chunking import derive_chunks
from .models import KnowledgeEntry, KnowledgePack
from .text import fts_match_expression, loose_surface, search_tokens, strict_surface, title_key

# Tags compare like titles: width- and case-insensitive.
tag_key = title_key


SCHEMA_VERSION = 3
MAX_CHUNKS_PER_PACK = 10_000
MAX_TOTAL_CHUNKS = 20_000
MAX_EMBED_ATTEMPTS = 3
_BUSY_TIMEOUT_SECONDS = 5.0

_SCHEMA = (
    "CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)",
    "CREATE TABLE IF NOT EXISTS packs ("
    " pack_id TEXT PRIMARY KEY, pack_sha256 TEXT NOT NULL,"
    # How many search rows the import wrote, to tell a complete index from
    # one that lost rows (see ``search_row_counts``).
    " surfaces INTEGER NOT NULL DEFAULT 0)",
    "CREATE TABLE IF NOT EXISTS entries ("
    # AUTOINCREMENT: ids are never reused, so a vector snapshot loaded before
    # a pack update can only miss rows, never point at an unrelated entry.
    " id INTEGER PRIMARY KEY AUTOINCREMENT,"
    " pack_id TEXT NOT NULL,"
    " title TEXT NOT NULL,"
    " title_key TEXT NOT NULL,"
    " terms_json TEXT NOT NULL,"
    " tags_json TEXT NOT NULL,"
    # Tags as compared (see tag_key), for exact tag lookups in SQL.
    " tag_keys_json TEXT NOT NULL,"
    " summary TEXT NOT NULL,"
    " content TEXT NOT NULL,"
    " disabled INTEGER NOT NULL DEFAULT 0,"
    " UNIQUE (pack_id, title_key))",
    "CREATE INDEX IF NOT EXISTS entries_pack_idx ON entries (pack_id, title_key)",
    "CREATE TABLE IF NOT EXISTS surfaces ("
    " surface TEXT NOT NULL,"
    " entry_id INTEGER NOT NULL REFERENCES entries(id) ON DELETE CASCADE)",
    "CREATE INDEX IF NOT EXISTS surfaces_idx ON surfaces (surface)",
    "CREATE INDEX IF NOT EXISTS surfaces_entry_idx ON surfaces (entry_id)",
    "CREATE VIRTUAL TABLE IF NOT EXISTS entries_fts USING fts5("
    " tokens, tokenize='unicode61 remove_diacritics 2')",
    "CREATE TABLE IF NOT EXISTS chunks ("
    " id INTEGER PRIMARY KEY,"
    " entry_id INTEGER NOT NULL REFERENCES entries(id) ON DELETE CASCADE,"
    " pack_id TEXT NOT NULL,"
    " chunk_index INTEGER NOT NULL,"
    " text_hash TEXT NOT NULL,"
    " embed_text TEXT NOT NULL,"
    " model_id TEXT,"
    " vector BLOB,"
    " attempts INTEGER NOT NULL DEFAULT 0)",
    "CREATE INDEX IF NOT EXISTS chunks_pack_idx ON chunks (pack_id, model_id)",
    "CREATE INDEX IF NOT EXISTS chunks_entry_idx ON chunks (entry_id)",
)


_REQUIRED_TABLES = frozenset({"meta", "packs", "entries", "surfaces", "entries_fts", "chunks"})


class KnowledgeStoreError(Exception):
    """The index could not be used as it is; the caller rebuilds it."""


@dataclass(frozen=True, slots=True)
class StoredEntry:
    entry_id: int
    pack_id: str
    title: str
    terms: dict[str, list[str]]
    tags: list[str]
    summary: str
    content: str
    disabled: bool


@dataclass(frozen=True, slots=True)
class VectorSnapshot:
    model_id: str
    entry_ids: np.ndarray
    pack_ids: tuple[str, ...]
    chunk_pack_index: np.ndarray
    matrix: np.ndarray
    chunk_indexes: np.ndarray | None = None


def _row_to_entry(row: sqlite3.Row) -> StoredEntry:
    return StoredEntry(
        entry_id=int(row["id"]),
        pack_id=str(row["pack_id"]),
        title=str(row["title"]),
        terms=json.loads(row["terms_json"]),
        tags=json.loads(row["tags_json"]),
        summary=str(row["summary"]),
        content=str(row["content"]),
        disabled=bool(row["disabled"]),
    )


def _entry_tokens(entry: KnowledgeEntry) -> str:
    parts = [entry.title, entry.summary, entry.content]
    for values in entry.terms.values():
        parts.extend(values)
    parts.extend(entry.tags)
    return " ".join(search_tokens("\n".join(parts)))


def _entry_surfaces(entry: KnowledgeEntry) -> set[str]:
    """Strict (``s:``) and loose (``l:``) exact-match keys of an entry."""
    values = [entry.title, *entry.terms.get("alias", ()), *entry.terms.get("recognition", ())]
    surfaces = {f"s:{strict}" for strict in map(strict_surface, values) if strict}
    surfaces |= {f"l:{loose}" for loose in map(loose_surface, values) if loose}
    return surfaces


def normalize_vector(values: Sequence[float]) -> bytes | None:
    vector = np.asarray(values, dtype=np.float32)
    if vector.ndim != 1 or vector.size == 0 or not np.all(np.isfinite(vector)):
        return None
    norm = float(np.linalg.norm(vector))
    if norm <= 0.0:
        return None
    return (vector / norm).astype("<f4").tobytes()


def count_pack_chunks(pack: KnowledgePack) -> int:
    return sum(len(derive_chunks(entry)) for entry in pack.entries)


class KnowledgeStore:
    def __init__(self, database_path: Path) -> None:
        self.database_path = Path(database_path)

    # ── connections ─────────────────────────────────────────────────

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(
            str(self.database_path),
            timeout=_BUSY_TIMEOUT_SECONDS,
            isolation_level=None,
            check_same_thread=True,
        )
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    @contextmanager
    def _read(self) -> Iterator[sqlite3.Connection]:
        conn = self._connect()
        try:
            yield conn
        finally:
            conn.close()

    @contextmanager
    def _write(self) -> Iterator[sqlite3.Connection]:
        conn = self._connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            try:
                yield conn
            except BaseException:
                conn.execute("ROLLBACK")
                raise
            conn.execute("COMMIT")
        finally:
            conn.close()

    # ── lifecycle ───────────────────────────────────────────────────

    def initialize(self) -> None:
        """Create or verify the schema; raise ``KnowledgeStoreError`` if unusable."""
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            with self._read() as conn:
                conn.execute("PRAGMA journal_mode=WAL")
                if conn.execute("PRAGMA quick_check").fetchone()[0] != "ok":
                    raise KnowledgeStoreError("integrity_check_failed")
                tables = {
                    row[0]
                    for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
                }
                if "meta" in tables:
                    row = conn.execute(
                        "SELECT value FROM meta WHERE key='schema_version'"
                    ).fetchone()
                    if row is None or row[0] != str(SCHEMA_VERSION):
                        raise KnowledgeStoreError("schema_version_mismatch")
                    # quick_check passes after a table is dropped; a derived
                    # database missing any part is rebuilt rather than trusted.
                    if not _REQUIRED_TABLES <= tables:
                        raise KnowledgeStoreError("schema_incomplete")
                    return
                if tables:
                    raise KnowledgeStoreError("unknown_database")
            with self._write() as conn:
                for statement in _SCHEMA:
                    conn.execute(statement)
                conn.execute(
                    "INSERT INTO meta (key, value) VALUES ('schema_version', ?)",
                    (str(SCHEMA_VERSION),),
                )
        except sqlite3.DatabaseError as exc:
            raise KnowledgeStoreError(type(exc).__name__) from exc

    def remove_files(self) -> None:
        """Delete the database and its WAL side files so it can be rebuilt."""
        for suffix in ("", "-wal", "-shm", "-journal"):
            path = self.database_path.with_name(self.database_path.name + suffix)
            path.unlink(missing_ok=True)

    # ── pack writes ─────────────────────────────────────────────────

    def search_row_counts(self) -> dict[str, tuple[int, int, int]]:
        """Per pack: (full-text rows, surface rows, surface rows written at import)."""
        with self._read() as conn:
            fts = dict(
                conn.execute(
                    "SELECT e.pack_id, COUNT(*) FROM entries_fts f JOIN entries e ON e.id=f.rowid"
                    " GROUP BY e.pack_id"
                ).fetchall()
            )
            surfaces = dict(
                conn.execute(
                    "SELECT e.pack_id, COUNT(*) FROM surfaces s JOIN entries e ON e.id=s.entry_id"
                    " GROUP BY e.pack_id"
                ).fetchall()
            )
            expected = dict(conn.execute("SELECT pack_id, surfaces FROM packs").fetchall())
        return {
            str(pack_id): (int(fts.get(pack_id, 0)), int(surfaces.get(pack_id, 0)), int(count))
            for pack_id, count in expected.items()
        }

    def entry_ids_by_title(
        self, titles: Mapping[str, Iterable[str]], *, enabled_only: bool = False
    ) -> set[int]:
        """Ids of the entries named by ``{pack_id: title keys}``.

        ``enabled_only`` keeps only entries not disabled in the index, which
        are the few a stale registry snapshot still disables.
        """
        flag = " AND disabled=0" if enabled_only else ""
        ids: set[int] = set()
        with self._read() as conn:
            for pack_id, keys in titles.items():
                keys = list(keys)
                if not keys:
                    continue
                ids.update(
                    int(row[0])
                    for row in conn.execute(
                        "SELECT id FROM entries WHERE pack_id=?"
                        f" AND title_key IN (SELECT value FROM json_each(?)){flag}",
                        (pack_id, json.dumps(keys, ensure_ascii=False)),
                    )
                )
        return ids

    def pack_versions(self) -> dict[str, str]:
        with self._read() as conn:
            return {
                str(row["pack_id"]): str(row["pack_sha256"])
                for row in conn.execute("SELECT pack_id, pack_sha256 FROM packs")
            }

    def replace_pack(
        self,
        pack: KnowledgePack,
        *,
        pack_sha256: str,
        disabled_keys: Iterable[str] = (),
        should_cancel: Callable[[], bool] | None = None,
        commit_gate: Callable[[], bool] | None = None,
    ) -> int:
        """Replace one pack's rows atomically; returns the number of chunks.

        ``commit_gate`` is asked once, right before COMMIT; ``False`` rolls the
        replacement back, ``True`` means it is committed from the caller's view.

        Vectors of chunks whose embedding text did not change are carried over,
        so updating a pack only re-embeds what actually changed.
        """
        disabled = set(disabled_keys)
        prepared = []
        for entry in pack.entries:
            if should_cancel is not None and should_cancel():
                raise InterruptedError("cancelled")
            prepared.append((entry, _entry_tokens(entry), _entry_surfaces(entry), derive_chunks(entry)))
        if should_cancel is not None and should_cancel():
            raise InterruptedError("cancelled")
        with self._write() as conn:
            reusable: dict[str, tuple[str, bytes]] = {}
            for row in conn.execute(
                "SELECT text_hash, model_id, vector FROM chunks"
                " WHERE pack_id=? AND vector IS NOT NULL AND model_id IS NOT NULL",
                (pack.pack_id,),
            ):
                reusable.setdefault(str(row[0]), (str(row[1]), bytes(row[2])))
            self._delete_pack_rows(conn, pack.pack_id)
            chunk_total = 0
            surface_total = 0
            for entry, tokens, surfaces, chunks in prepared:
                # Raising here rolls the whole transaction back.
                if should_cancel is not None and should_cancel():
                    raise InterruptedError("cancelled")
                cursor = conn.execute(
                    "INSERT INTO entries (pack_id, title, title_key, terms_json, tags_json,"
                    " tag_keys_json, summary, content, disabled) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        pack.pack_id,
                        entry.title,
                        entry.key,
                        json.dumps({k: list(v) for k, v in entry.terms.items()}, ensure_ascii=False),
                        json.dumps(list(entry.tags), ensure_ascii=False),
                        json.dumps(sorted({tag_key(tag) for tag in entry.tags}), ensure_ascii=False),
                        entry.summary,
                        entry.content,
                        1 if entry.key in disabled else 0,
                    ),
                )
                entry_id = int(cursor.lastrowid)
                conn.execute(
                    "INSERT INTO entries_fts (rowid, tokens) VALUES (?, ?)", (entry_id, tokens)
                )
                conn.executemany(
                    "INSERT INTO surfaces (surface, entry_id) VALUES (?, ?)",
                    [(surface, entry_id) for surface in surfaces],
                )
                surface_total += len(surfaces)
                for chunk in chunks:
                    carried = reusable.get(chunk.text_hash, (None, None))
                    conn.execute(
                        "INSERT INTO chunks (entry_id, pack_id, chunk_index, text_hash,"
                        " embed_text, model_id, vector) VALUES (?, ?, ?, ?, ?, ?, ?)",
                        (
                            entry_id,
                            pack.pack_id,
                            chunk.chunk_index,
                            chunk.text_hash,
                            chunk.embed_text,
                            carried[0],
                            carried[1],
                        ),
                    )
                chunk_total += len(chunks)
            conn.execute(
                "INSERT INTO packs (pack_id, pack_sha256, surfaces) VALUES (?, ?, ?)"
                " ON CONFLICT(pack_id) DO UPDATE SET"
                " pack_sha256=excluded.pack_sha256, surfaces=excluded.surfaces",
                (pack.pack_id, pack_sha256, surface_total),
            )
            # Last chance: a cancel that arrived during the final writes still
            # rolls the whole replacement back.
            if should_cancel is not None and should_cancel():
                raise InterruptedError("cancelled")
            if commit_gate is not None and not commit_gate():
                raise InterruptedError("cancelled")
        return chunk_total

    @staticmethod
    def _delete_pack_rows(conn: sqlite3.Connection, pack_id: str) -> None:
        conn.execute(
            "DELETE FROM entries_fts WHERE rowid IN (SELECT id FROM entries WHERE pack_id=?)",
            (pack_id,),
        )
        conn.execute("DELETE FROM entries WHERE pack_id=?", (pack_id,))
        conn.execute("DELETE FROM packs WHERE pack_id=?", (pack_id,))

    def delete_pack(self, pack_id: str) -> None:
        with self._write() as conn:
            self._delete_pack_rows(conn, pack_id)

    def set_disabled(self, pack_id: str, title: str, disabled: bool) -> bool:
        with self._write() as conn:
            cursor = conn.execute(
                "UPDATE entries SET disabled=? WHERE pack_id=? AND title_key=?",
                (1 if disabled else 0, pack_id, title_key(title)),
            )
            return cursor.rowcount > 0

    def sync_disabled(self, pack_id: str, disabled_keys: Iterable[str]) -> int:
        """Make the pack's disabled flags exactly ``disabled_keys``; return rows changed."""
        keys = sorted(set(disabled_keys))
        with self._write() as conn:
            conn.execute("CREATE TEMP TABLE IF NOT EXISTS wanted_disabled (title_key TEXT PRIMARY KEY)")
            conn.execute("DELETE FROM wanted_disabled")
            conn.executemany("INSERT INTO wanted_disabled (title_key) VALUES (?)", [(k,) for k in keys])
            cursor = conn.execute(
                "UPDATE entries SET disabled ="
                " CASE WHEN title_key IN (SELECT title_key FROM wanted_disabled) THEN 1 ELSE 0 END"
                " WHERE pack_id=? AND disabled !="
                " CASE WHEN title_key IN (SELECT title_key FROM wanted_disabled) THEN 1 ELSE 0 END",
                (pack_id,),
            )
            return cursor.rowcount

    def disabled_entry_ids(self, pack_ids: Sequence[str]) -> set[int]:
        """Ids of disabled entries in ``pack_ids``."""
        if not pack_ids:
            return set()
        placeholders = ",".join("?" for _ in pack_ids)
        with self._read() as conn:
            return {
                int(row[0])
                for row in conn.execute(
                    f"SELECT id FROM entries WHERE disabled=1 AND pack_id IN ({placeholders})",
                    tuple(pack_ids),
                )
            }

    # ── management reads ────────────────────────────────────────────

    def entry_counts(self) -> dict[str, tuple[int, int]]:
        """Per pack: (entries, disabled entries)."""
        with self._read() as conn:
            return {
                str(row[0]): (int(row[1]), int(row[2] or 0))
                for row in conn.execute(
                    "SELECT pack_id, COUNT(*), SUM(disabled) FROM entries GROUP BY pack_id"
                )
            }

    def count_entries(self, *, pack_ids: Sequence[str] | None = None) -> int:
        """Entries of ``pack_ids`` (all packs when ``None``)."""
        if pack_ids is not None and not pack_ids:
            return 0
        clause, args = self._pack_clause(pack_ids, include_disabled=True)
        with self._read() as conn:
            row = conn.execute(f"SELECT COUNT(*) FROM entries e WHERE 1=1{clause}", args).fetchone()
            return int(row[0])

    def list_entries(
        self, *, pack_ids: Sequence[str] | None = None, limit: int, offset: int
    ) -> list[StoredEntry]:
        if pack_ids is not None and not pack_ids:
            return []
        clause, args = self._pack_clause(pack_ids, include_disabled=True)
        with self._read() as conn:
            rows = conn.execute(
                f"SELECT * FROM entries e WHERE 1=1{clause}"
                " ORDER BY e.pack_id, e.title_key LIMIT ? OFFSET ?",
                (*args, limit, offset),
            )
            return [_row_to_entry(row) for row in rows]

    def search_entries(
        self, query: str, *, pack_ids: Sequence[str] | None = None, limit: int, offset: int
    ) -> list[StoredEntry]:
        """Management search: exact surface hits first, then BM25 order."""
        with self._read() as conn:
            exact = self._exact_ids(
                conn, query, pack_ids=pack_ids, include_disabled=True, limit=offset + limit
            )
            ranked = self._ranked_ids(
                conn,
                query,
                pack_ids=pack_ids,
                include_disabled=True,
                limit=offset + limit + len(exact),
            )
            ordered = list(dict.fromkeys([*exact, *ranked]))
            rows = self._fetch(conn, ordered)
        entries = [rows[i] for i in ordered if i in rows]
        return entries[offset:offset + limit]

    def get_entry(self, pack_id: str, title: str) -> StoredEntry | None:
        with self._read() as conn:
            row = conn.execute(
                "SELECT * FROM entries WHERE pack_id=? AND title_key=?",
                (pack_id, title_key(title)),
            ).fetchone()
        return _row_to_entry(row) if row is not None else None

    # ── retrieval reads ─────────────────────────────────────────────

    @staticmethod
    def _fetch(conn: sqlite3.Connection, entry_ids: Sequence[int]) -> dict[int, StoredEntry]:
        result: dict[int, StoredEntry] = {}
        ids = list(entry_ids)
        for start in range(0, len(ids), 500):
            batch = ids[start:start + 500]
            placeholders = ",".join("?" for _ in batch)
            for row in conn.execute(f"SELECT * FROM entries WHERE id IN ({placeholders})", batch):
                entry = _row_to_entry(row)
                result[entry.entry_id] = entry
        return result

    def fetch_entries(self, entry_ids: Sequence[int]) -> dict[int, StoredEntry]:
        with self._read() as conn:
            return self._fetch(conn, entry_ids)

    @staticmethod
    def _pack_clause(
        pack_ids: Sequence[str] | None,
        include_disabled: bool,
        exclude_ids: Collection[int] = (),
    ) -> tuple[str, tuple]:
        clauses: list[str] = []
        args: tuple = ()
        if not include_disabled:
            clauses.append("e.disabled=0")
        if pack_ids is not None:
            clauses.append(f"e.pack_id IN ({','.join('?' for _ in pack_ids)})")
            args = tuple(pack_ids)
        if exclude_ids:
            # One JSON parameter, however many ids: no SQL variable limit.
            clauses.append("e.id NOT IN (SELECT value FROM json_each(?))")
            args = (*args, json.dumps(sorted(exclude_ids)))
        return "".join(f" AND {clause}" for clause in clauses), args

    def _exact_ids(
        self,
        conn: sqlite3.Connection,
        query: str,
        *,
        pack_ids: Sequence[str] | None,
        include_disabled: bool,
        limit: int = -1,
        exclude_ids: Collection[int] = (),
    ) -> list[int]:
        if pack_ids is not None and not pack_ids:
            return []
        clause, args = self._pack_clause(pack_ids, include_disabled, exclude_ids)
        # Strict first, so "C" does not pull in "C++"; the loose form (inner
        # separators dropped) only answers when nothing matches strictly, e.g.
        # a recognition phrase typed with different punctuation. Names that
        # begin or end with a symbol have no loose form at all.
        for key in (f"s:{strict_surface(query)}", f"l:{loose_surface(query)}"):
            if len(key) <= 2:
                continue
            ids = [
                int(row[0])
                for row in conn.execute(
                    "SELECT DISTINCT s.entry_id FROM surfaces s JOIN entries e ON e.id=s.entry_id"
                    f" WHERE s.surface=?{clause} LIMIT ?",
                    (key, *args, limit),
                )
            ]
            if ids:
                return ids
        return []

    def _ranked_ids(
        self,
        conn: sqlite3.Connection,
        query: str,
        *,
        pack_ids: Sequence[str] | None,
        include_disabled: bool,
        limit: int,
        exclude_ids: Collection[int] = (),
    ) -> list[int]:
        expression = fts_match_expression(query)
        if not expression or (pack_ids is not None and not pack_ids):
            return []
        clause, args = self._pack_clause(pack_ids, include_disabled, exclude_ids)
        # Filter before LIMIT: otherwise matches from other packs (or disabled
        # entries) fill the window and push the wanted ones out.
        return [
            int(row[0])
            for row in conn.execute(
                "SELECT f.rowid FROM entries_fts f JOIN entries e ON e.id=f.rowid"
                f" WHERE entries_fts MATCH ?{clause} ORDER BY bm25(entries_fts) LIMIT ?",
                (expression, *args, limit),
            )
        ]

    def lexical_candidates(
        self,
        query: str,
        *,
        pack_ids: Sequence[str],
        limit: int,
        exclude_ids: Collection[int] = (),
    ) -> tuple[list[int], list[int]]:
        """Return (exact surface hits, BM25-ranked hits) among enabled entries of ``pack_ids``.

        ``exclude_ids`` are left out before ``limit`` applies.
        """
        with self._read() as conn:
            exact = self._exact_ids(
                conn, query, pack_ids=pack_ids, include_disabled=False, limit=limit,
                exclude_ids=exclude_ids,
            )
            ranked = self._ranked_ids(
                conn, query, pack_ids=pack_ids, include_disabled=False, limit=limit,
                exclude_ids=exclude_ids,
            )
        return exact, ranked

    def entries_with_tag(self, tag: str, pack_ids: Sequence[str]) -> list[tuple[int, str, str]]:
        """Enabled entries carrying ``tag``: (id, pack_id, title)."""
        if not pack_ids:
            return []
        placeholders = ",".join("?" for _ in pack_ids)
        # A whole tag, compared like titles (width- and case-insensitive): a
        # substring of the JSON text would also hit longer tags, or other
        # tags through a quote.
        with self._read() as conn:
            return [
                (int(row[0]), str(row[1]), str(row[2]))
                for row in conn.execute(
                    f"SELECT id, pack_id, title FROM entries WHERE disabled=0 AND pack_id IN ({placeholders})"
                    " AND EXISTS (SELECT 1 FROM json_each(tag_keys_json) WHERE value = ?)",
                    (*pack_ids, tag_key(tag)),
                )
            ]

    # ── vectors ─────────────────────────────────────────────────────

    def pending_chunks(
        self, *, model_id: str, pack_ids: Sequence[str], limit: int
    ) -> list[tuple[int, str, str]]:
        """Chunks that lack a vector for ``model_id``: (id, text_hash, text)."""
        if not pack_ids:
            return []
        placeholders = ",".join("?" for _ in pack_ids)
        with self._read() as conn:
            return [
                (int(row[0]), str(row[1]), str(row[2]))
                for row in conn.execute(
                    f"SELECT id, text_hash, embed_text FROM chunks WHERE pack_id IN ({placeholders})"
                    " AND (model_id IS NOT ? OR vector IS NULL) AND attempts < ?"
                    " ORDER BY id LIMIT ?",
                    (*pack_ids, model_id, MAX_EMBED_ATTEMPTS, limit),
                )
            ]

    def store_vectors(
        self,
        *,
        model_id: str,
        rows: Sequence[tuple[int, str, bytes | None]],
        pack_ids: Sequence[str] | None = None,
    ) -> tuple[int, int]:
        """Write vectors for unchanged chunks; return (stored, failed).

        With ``pack_ids``, chunks of other packs are left untouched (their
        pack stopped allowing vectors while the batch was embedding).
        """
        if pack_ids is not None and not pack_ids:
            return 0, 0
        clause = f" AND pack_id IN ({','.join('?' for _ in pack_ids)})" if pack_ids is not None else ""
        extra = tuple(pack_ids) if pack_ids is not None else ()
        stored = failed = 0
        with self._write() as conn:
            for chunk_id, text_hash, blob in rows:
                if blob is None:
                    cursor = conn.execute(
                        f"UPDATE chunks SET attempts=attempts+1 WHERE id=? AND text_hash=?{clause}",
                        (chunk_id, text_hash, *extra),
                    )
                    failed += cursor.rowcount
                    continue
                cursor = conn.execute(
                    f"UPDATE chunks SET model_id=?, vector=?, attempts=0 WHERE id=? AND text_hash=?{clause}",
                    (model_id, blob, chunk_id, text_hash, *extra),
                )
                stored += cursor.rowcount
        return stored, failed

    def reset_attempts(self, pack_id: str = "") -> int:
        """Give chunks that hit ``MAX_EMBED_ATTEMPTS`` another chance."""
        with self._write() as conn:
            if pack_id:
                cursor = conn.execute(
                    "UPDATE chunks SET attempts=0 WHERE attempts > 0 AND pack_id=?", (pack_id,)
                )
            else:
                cursor = conn.execute("UPDATE chunks SET attempts=0 WHERE attempts > 0")
            return cursor.rowcount

    def chunk_stats(self, model_id: str | None) -> dict[str, dict[str, int]]:
        """Per pack: total chunks, chunks ready for ``model_id``, failed chunks."""
        with self._read() as conn:
            rows = conn.execute(
                "SELECT pack_id, COUNT(*),"
                " SUM(CASE WHEN vector IS NOT NULL AND model_id IS ? THEN 1 ELSE 0 END),"
                " SUM(CASE WHEN attempts >= ? THEN 1 ELSE 0 END)"
                " FROM chunks GROUP BY pack_id",
                (model_id, MAX_EMBED_ATTEMPTS),
            )
            return {
                str(row[0]): {
                    "total": int(row[1]),
                    "ready": int(row[2] or 0) if model_id else 0,
                    "failed": int(row[3] or 0),
                }
                for row in rows
            }

    def load_vectors(self, model_id: str) -> VectorSnapshot | None:
        """Load every ready vector of ``model_id`` into one normalized matrix."""
        with self._read() as conn:
            rows = conn.execute(
                "SELECT entry_id, pack_id, vector, chunk_index FROM chunks"
                " WHERE model_id=? AND vector IS NOT NULL ORDER BY id",
                (model_id,),
            ).fetchall()
        if not rows:
            return None
        dim = len(rows[0][2]) // 4
        pack_ids: list[str] = []
        pack_index: dict[str, int] = {}
        entry_ids: list[int] = []
        chunk_pack: list[int] = []
        chunk_indexes: list[int] = []
        blobs: list[bytes] = []
        for entry_id, pack_id, blob, chunk_index in rows:
            if len(blob) != dim * 4:
                continue
            if pack_id not in pack_index:
                pack_index[pack_id] = len(pack_ids)
                pack_ids.append(pack_id)
            entry_ids.append(int(entry_id))
            chunk_pack.append(pack_index[pack_id])
            chunk_indexes.append(int(chunk_index))
            blobs.append(blob)
        if not blobs:
            return None
        matrix = np.frombuffer(b"".join(blobs), dtype="<f4").reshape(len(blobs), dim)
        return VectorSnapshot(
            model_id=model_id,
            entry_ids=np.asarray(entry_ids, dtype=np.int64),
            pack_ids=tuple(pack_ids),
            chunk_pack_index=np.asarray(chunk_pack, dtype=np.int32),
            matrix=np.ascontiguousarray(matrix, dtype=np.float32),
            chunk_indexes=np.asarray(chunk_indexes, dtype=np.int32),
        )
