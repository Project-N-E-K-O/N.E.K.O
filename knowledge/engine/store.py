"""SQLite persistence for five-field public knowledge cards."""

from __future__ import annotations

import json
import logging
import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Callable, Iterator, Sequence

from .filters import normalize_search_text
from .models import KnowledgeEntry, UpsertResult


logger = logging.getLogger(__name__)

SCHEMA_VERSION = 5
_INITIALIZED_DATABASES: dict[str, tuple[int, int] | None] = {}
_INITIALIZE_LOCK = threading.Lock()
_DATABASE_CHANGE_LISTENERS: list[Callable[[Path], None]] = []
_DATABASE_CHANGE_LISTENERS_LOCK = threading.Lock()


def register_database_change_listener(listener: Callable[[Path], None]) -> None:
    """Register one process-local callback for committed database changes."""
    with _DATABASE_CHANGE_LISTENERS_LOCK:
        if listener not in _DATABASE_CHANGE_LISTENERS:
            _DATABASE_CHANGE_LISTENERS.append(listener)


def unregister_database_change_listener(listener: Callable[[Path], None]) -> None:
    """Remove a previously registered database-change callback."""
    with _DATABASE_CHANGE_LISTENERS_LOCK:
        try:
            _DATABASE_CHANGE_LISTENERS.remove(listener)
        except ValueError:
            pass


def publish_database_changed(database_path: str | Path) -> None:
    """Notify a stable snapshot of listeners after a successful write."""
    with _DATABASE_CHANGE_LISTENERS_LOCK:
        listeners = tuple(_DATABASE_CHANGE_LISTENERS)
    resolved_path = Path(database_path)
    for listener in listeners:
        try:
            listener(resolved_path)
        except Exception:
            # A listener failure must not stop later listeners or turn a
            # committed write into a reported failure.
            logger.exception("[knowledge] database change listener failed: %s", resolved_path)


class KnowledgeStoreError(RuntimeError):
    pass


class KnowledgeStore:
    """Own a small rebuildable database without touching character memory."""

    def __init__(self, database_path: str | Path) -> None:
        self.database_path = Path(database_path)

    @contextmanager
    def _connection(self, *, writable: bool = False) -> Iterator[sqlite3.Connection]:
        if writable:
            self.database_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            cache_key = str(self.database_path.resolve())
            identity = _database_identity(self.database_path)
            if (
                cache_key not in _INITIALIZED_DATABASES
                or _INITIALIZED_DATABASES[cache_key] != identity
            ):
                with _INITIALIZE_LOCK:
                    identity = _database_identity(self.database_path)
                    if (
                        cache_key not in _INITIALIZED_DATABASES
                        or _INITIALIZED_DATABASES[cache_key] != identity
                    ):
                        connection = self._open_connection(writable=writable)
                        self._initialize(connection)
                        # Migration and compatibility writes must persist before
                        # any caller observes the initialized database.
                        connection.commit()
                        _INITIALIZED_DATABASES[cache_key] = _database_identity(
                            self.database_path
                        )
                    else:
                        connection = self._open_connection(writable=writable)
            else:
                connection = self._open_connection(writable=writable)
            yield connection
            if writable:
                connection.commit()
        except sqlite3.DatabaseError as exc:
            raise KnowledgeStoreError(f"knowledge database is unavailable: {exc}") from exc
        finally:
            if "connection" in locals():
                connection.close()

    def _open_connection(self, *, writable: bool) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_path)
        connection.row_factory = sqlite3.Row
        connection.create_function(
            "normalize_search_text",
            1,
            normalize_search_text,
            deterministic=True,
        )
        connection.execute("PRAGMA busy_timeout=5000")
        if writable:
            connection.execute("PRAGMA journal_mode=WAL")
        return connection

    def _initialize(self, connection: sqlite3.Connection) -> None:
        columns = {
            row["name"] for row in connection.execute("PRAGMA table_info(entries)").fetchall()
        }
        if columns and "terms" not in columns:
            self._migrate_legacy_entries(connection, columns)
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS metadata (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS entries (
                title TEXT NOT NULL,
                terms TEXT NOT NULL DEFAULT '{"alias":[],"recognition":[]}',
                tags TEXT NOT NULL DEFAULT '[]',
                summary TEXT NOT NULL DEFAULT '',
                content TEXT NOT NULL
            );
            CREATE VIRTUAL TABLE IF NOT EXISTS entries_fts USING fts5(
                entry_rowid UNINDEXED,
                title,
                terms,
                tags,
                summary,
                content,
                tokenize='unicode61'
            );
            """
        )
        connection.execute(
            "INSERT OR REPLACE INTO metadata(key, value) VALUES ('schema_version', ?)",
            (str(SCHEMA_VERSION),),
        )

    def _migrate_legacy_entries(
        self,
        connection: sqlite3.Connection,
        columns: set[str],
    ) -> None:
        """Copy the old attributed schema into a five-field table once.

        The external backup is intentionally retained so callers can recover
        legacy data if a later domain importer chooses a different mapping.
        """
        backup = self.database_path.with_suffix(self.database_path.suffix + ".legacy.bak")
        if self.database_path.exists() and not backup.exists():
            backup_connection = sqlite3.connect(backup)
            try:
                connection.backup(backup_connection)
                backup_connection.commit()
            except Exception:
                backup_connection.close()
                backup.unlink(missing_ok=True)
                raise
            else:
                backup_connection.close()
        rows = connection.execute("SELECT * FROM entries").fetchall()
        connection.execute("DROP TABLE IF EXISTS entries_fts")
        connection.execute("ALTER TABLE entries RENAME TO entries_legacy")
        connection.execute(
            "CREATE TABLE entries (title TEXT NOT NULL, terms TEXT NOT NULL, tags TEXT NOT NULL, summary TEXT NOT NULL, content TEXT NOT NULL)"
        )
        for row in rows:
            tags = _json_values(row["tags"]) if "tags" in columns else ()
            aliases = _json_values(row["aliases"]) if "aliases" in columns else ()
            terms = {"alias": list(aliases), "recognition": []}
            connection.execute(
                "INSERT INTO entries(title, terms, tags, summary, content) VALUES (?, ?, ?, ?, ?)",
                (
                    row["title"], json.dumps(terms, ensure_ascii=False),
                    json.dumps(tags, ensure_ascii=False),
                    row["summary"] if "summary" in columns else "",
                    row["content"],
                ),
            )
        connection.execute(
            "CREATE VIRTUAL TABLE entries_fts USING fts5("
            "entry_rowid UNINDEXED, title, terms, tags, summary, content, "
            "tokenize='unicode61')"
        )
        for row in connection.execute(
            "SELECT rowid, * FROM entries ORDER BY rowid"
        ).fetchall():
            try:
                entry = entry_from_row(row)
            except (TypeError, ValueError, json.JSONDecodeError) as exc:
                logger.warning(
                    "[public-knowledge] skipped invalid entry operation=%s rowid=%s type=%s",
                    "migrate_legacy_entries",
                    row["rowid"],
                    type(exc).__name__,
                )
                continue
            self._replace_fts(connection, int(row["rowid"]), entry)
        connection.execute("DROP TABLE entries_legacy")

    def upsert(self, entry: KnowledgeEntry) -> UpsertResult:
        with self._connection(writable=True) as connection:
            result = self._upsert_with_connection(connection, entry)
            if not result.unchanged:
                self._increment_entries_revision(connection)
        if not result.unchanged:
            self._notify_routing_changed()
        return result

    def upsert_many(self, entries: Sequence[KnowledgeEntry]) -> tuple[UpsertResult, ...]:
        with self._connection(writable=True) as connection:
            results = tuple(self._upsert_with_connection(connection, entry) for entry in entries)
            if any(not result.unchanged for result in results):
                self._increment_entries_revision(connection)
        if any(not result.unchanged for result in results):
            self._notify_routing_changed()
        return results

    def replace_source(self, source_tag: str, entries: Sequence[KnowledgeEntry]) -> tuple[UpsertResult, ...]:
        """Atomically replace a fixed bundled/imported source namespace."""
        if not source_tag.startswith("source:") or any(entry.source_tag != source_tag for entry in entries):
            raise ValueError("entries must all belong to the requested source")
        with self._connection(writable=True) as connection:
            rowids = [row[0] for row in connection.execute(
                "SELECT entries.rowid FROM entries JOIN json_each(entries.tags) tag WHERE tag.value = ?", (source_tag,)
            )]
            if rowids:
                connection.executemany("DELETE FROM entries_fts WHERE entry_rowid = ?", ((value,) for value in rowids))
                connection.executemany("DELETE FROM entries WHERE rowid = ?", ((value,) for value in rowids))
            results = tuple(self._insert_with_connection(connection, entry) for entry in entries)
            self._increment_entries_revision(connection)
        self._notify_routing_changed()
        return results

    def _notify_routing_changed(self) -> None:
        publish_database_changed(self.database_path)

    @staticmethod
    def _entry_key(entry: KnowledgeEntry) -> str:
        return f"{entry.source_tag}:{entry.title}"

    def _upsert_with_connection(self, connection: sqlite3.Connection, entry: KnowledgeEntry) -> UpsertResult:
        rows = connection.execute(
            "SELECT rowid, * FROM entries WHERE title = ? AND EXISTS (SELECT 1 FROM json_each(entries.tags) tag WHERE tag.value = ?)",
            (entry.title, entry.source_tag),
        ).fetchall()
        if len(rows) == 1:
            existing = entry_from_row(rows[0])
            if existing.content_hash == entry.content_hash:
                return UpsertResult(self._entry_key(entry), unchanged=True)
            rowid = rows[0]["rowid"]
            connection.execute(
                "UPDATE entries SET terms=?, tags=?, summary=?, content=? WHERE rowid=?",
                (_terms_json(entry), _values_json(entry.tags), entry.summary, entry.content, rowid),
            )
            self._replace_fts(connection, rowid, entry)
            return UpsertResult(self._entry_key(entry), updated=True)
        if len(rows) > 1:
            # Converge damaged duplicate rows: keep and update the first, drop
            # the rest so repeated upserts heal instead of growing.
            rowid = rows[0]["rowid"]
            for stale in rows[1:]:
                stale_rowid = stale["rowid"]
                connection.execute(
                    "DELETE FROM entries_fts WHERE entry_rowid = ?",
                    (stale_rowid,),
                )
                connection.execute(
                    "DELETE FROM entries WHERE rowid = ?",
                    (stale_rowid,),
                )
            connection.execute(
                "UPDATE entries SET terms=?, tags=?, summary=?, content=? WHERE rowid=?",
                (_terms_json(entry), _values_json(entry.tags), entry.summary, entry.content, rowid),
            )
            self._replace_fts(connection, rowid, entry)
            return UpsertResult(self._entry_key(entry), updated=True)
        return self._insert_with_connection(connection, entry)

    def _insert_with_connection(self, connection: sqlite3.Connection, entry: KnowledgeEntry) -> UpsertResult:
        cursor = connection.execute(
            "INSERT INTO entries(title, terms, tags, summary, content) VALUES (?, ?, ?, ?, ?)",
            (entry.title, _terms_json(entry), _values_json(entry.tags), entry.summary, entry.content),
        )
        self._replace_fts(connection, int(cursor.lastrowid), entry)
        return UpsertResult(self._entry_key(entry), created=True)

    @staticmethod
    def _replace_fts(connection: sqlite3.Connection, rowid: int, entry: KnowledgeEntry) -> None:
        connection.execute("DELETE FROM entries_fts WHERE entry_rowid = ?", (rowid,))
        connection.execute(
            "INSERT INTO entries_fts(entry_rowid, title, terms, tags, summary, content) VALUES (?, ?, ?, ?, ?, ?)",
            (rowid, entry.title, _terms_search_text(entry), " ".join(entry.tags), entry.summary, entry.content),
        )

    @staticmethod
    def _increment_entries_revision(connection: sqlite3.Connection) -> None:
        connection.execute("INSERT OR IGNORE INTO metadata(key, value) VALUES ('entries_revision', '0')")
        connection.execute("UPDATE metadata SET value = CAST(value AS INTEGER) + 1 WHERE key = 'entries_revision'")

    def count(self) -> int:
        try:
            with self._connection() as connection:
                return int(connection.execute("SELECT COUNT(*) FROM entries").fetchone()[0])
        except KnowledgeStoreError:
            return 0

    def count_by_source_tag(self, source_tag: str) -> int:
        try:
            with self._connection() as connection:
                return int(connection.execute(
                    "SELECT COUNT(*) FROM entries WHERE EXISTS (SELECT 1 FROM json_each(entries.tags) tag WHERE tag.value = ?)",
                    (source_tag,),
                ).fetchone()[0])
        except KnowledgeStoreError:
            return 0

    def count_by_source_tags(self) -> tuple[dict[str, object], ...]:
        """Return compact source counts without materializing entry rows."""
        try:
            with self._connection() as connection:
                rows = connection.execute(
                    "SELECT tag.value source_tag, COUNT(*) entry_count "
                    "FROM entries JOIN json_each(entries.tags) tag "
                    "WHERE tag.value LIKE 'source:%' "
                    "GROUP BY tag.value ORDER BY tag.value"
                ).fetchall()
                return tuple({
                    "tag": str(row["source_tag"]),
                    "entries": int(row["entry_count"]),
                } for row in rows)
        except KnowledgeStoreError:
            return ()

    def entries_revision(self) -> int:
        try:
            with self._connection() as connection:
                row = connection.execute("SELECT value FROM metadata WHERE key = 'entries_revision'").fetchone()
                return int(row["value"]) if row else 0
        except (KnowledgeStoreError, TypeError, ValueError):
            return 0

    def load_routing_entries(self) -> tuple[int, tuple[KnowledgeEntry, ...]]:
        """Read one collection revision and its routeable cards in one transaction."""
        try:
            with self._connection() as connection:
                revision_row = connection.execute(
                    "SELECT value FROM metadata WHERE key = 'entries_revision'"
                ).fetchone()
                revision = int(revision_row["value"]) if revision_row else 0
                entries: list[KnowledgeEntry] = []
                for row in connection.execute(
                    "SELECT rowid, * FROM entries ORDER BY rowid"
                ).fetchall():
                    try:
                        entries.append(entry_from_row(row))
                    except (TypeError, ValueError, json.JSONDecodeError):
                        continue
                return revision, tuple(entries)
        except (KnowledgeStoreError, TypeError, ValueError):
            return 0, ()

    def integrity_ok(self) -> bool:
        try:
            with self._connection() as connection:
                return connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        except KnowledgeStoreError:
            return False

    def list_active_entries(self) -> tuple[KnowledgeEntry, ...]:
        try:
            with self._connection() as connection:
                rows = connection.execute("SELECT rowid, * FROM entries ORDER BY rowid").fetchall()
                return _valid_entries(rows, operation="list_active_entries")
        except KnowledgeStoreError:
            return ()

    def get_entry(self, source_tag: str, title: str) -> KnowledgeEntry | None:
        try:
            with self._connection() as connection:
                row = connection.execute(
                    "SELECT rowid, * FROM entries WHERE title = ? AND EXISTS "
                    "(SELECT 1 FROM json_each(entries.tags) tag WHERE tag.value = ?) LIMIT 1",
                    (title, source_tag),
                ).fetchone()
                return entry_from_row(row) if row is not None else None
        except (KnowledgeStoreError, TypeError, ValueError, json.JSONDecodeError):
            return None

    def list_entries(
        self,
        *,
        source_tag: str = "",
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[KnowledgeEntry, ...]:
        limit = min(max(int(limit), 1), 100)
        offset = max(int(offset), 0)
        try:
            with self._connection() as connection:
                if source_tag:
                    rows = connection.execute(
                        "SELECT rowid, * FROM entries WHERE CASE WHEN json_valid(tags) "
                        "THEN EXISTS (SELECT 1 FROM json_each(entries.tags) tag "
                        "WHERE tag.value = ?) ELSE 0 END "
                        "ORDER BY title LIMIT ? OFFSET ?",
                        (source_tag, limit, offset),
                    ).fetchall()
                else:
                    rows = connection.execute(
                        "SELECT rowid, * FROM entries ORDER BY title LIMIT ? OFFSET ?",
                        (limit, offset),
                    ).fetchall()
                return _valid_entries(rows, operation="list_entries")
        except KnowledgeStoreError:
            return ()

    def query_fts(
        self,
        fts_query: str,
        *,
        limit: int,
        allowed_source_tags: tuple[str, ...] | None = None,
    ):
        if allowed_source_tags is not None and not allowed_source_tags:
            return ()
        source_clause = ""
        source_parameters: tuple[str, ...] = ()
        if allowed_source_tags is not None:
            placeholders = ", ".join("?" for _ in allowed_source_tags)
            source_clause = (
                " AND EXISTS (SELECT 1 FROM json_each(entries.tags) source_filter "
                f"WHERE source_filter.value IN ({placeholders}))"
            )
            source_parameters = allowed_source_tags
        try:
            with self._connection() as connection:
                return connection.execute(
                    "SELECT entries.rowid, entries.*, bm25(entries_fts) rank "
                    "FROM entries_fts JOIN entries "
                    "ON entries.rowid = entries_fts.entry_rowid "
                    "WHERE entries_fts MATCH ?"
                    f"{source_clause} ORDER BY rank LIMIT ?",
                    (fts_query, *source_parameters, limit),
                ).fetchall()
        except (KnowledgeStoreError, sqlite3.OperationalError):
            return ()

    def query_like(
        self,
        normalized_query: str,
        *,
        limit: int,
        allowed_source_tags: tuple[str, ...] | None = None,
    ):
        normalized_query = normalize_search_text(normalized_query)
        if not normalized_query:
            return ()
        if allowed_source_tags is not None and not allowed_source_tags:
            return ()
        source_clause = ""
        source_parameters: tuple[str, ...] = ()
        if allowed_source_tags is not None:
            placeholders = ", ".join("?" for _ in allowed_source_tags)
            source_clause = (
                " AND EXISTS (SELECT 1 FROM json_each(entries.tags) source_filter "
                f"WHERE source_filter.value IN ({placeholders}))"
            )
            source_parameters = allowed_source_tags
        escaped_query = (
            normalized_query.replace("\\", "\\\\")
            .replace("%", "\\%")
            .replace("_", "\\_")
        )
        pattern = f"%{escaped_query}%"
        try:
            with self._connection() as connection:
                return connection.execute(
                    "SELECT rowid, * FROM entries WHERE ("
                    "normalize_search_text(title) LIKE ? ESCAPE '\\' "
                    "OR normalize_search_text(terms) LIKE ? ESCAPE '\\' "
                    "OR normalize_search_text(tags) LIKE ? ESCAPE '\\' "
                    "OR normalize_search_text(content) LIKE ? ESCAPE '\\' "
                    "OR normalize_search_text(summary) LIKE ? ESCAPE '\\')"
                    f"{source_clause} LIMIT ?",
                    (
                        pattern,
                        pattern,
                        pattern,
                        pattern,
                        pattern,
                        *source_parameters,
                        limit,
                    ),
                ).fetchall()
        except (KnowledgeStoreError, sqlite3.OperationalError):
            return ()


def _terms_json(entry: KnowledgeEntry) -> str:
    return json.dumps({role: list(entry.terms[role]) for role in entry.terms}, ensure_ascii=False)


def _values_json(values: Sequence[str]) -> str:
    return json.dumps(tuple(values), ensure_ascii=False)


def _terms_search_text(entry: KnowledgeEntry) -> str:
    return " ".join(value for role in entry.terms.values() for value in role)


def _json_values(value: str) -> tuple[str, ...]:
    raw = json.loads(value or "[]")
    return tuple(item for item in raw if isinstance(item, str)) if isinstance(raw, list) else ()


def entry_from_row(row: sqlite3.Row) -> KnowledgeEntry:
    raw_terms = json.loads(row["terms"])
    return KnowledgeEntry(
        title=row["title"], terms=raw_terms if isinstance(raw_terms, dict) else {},
        tags=_json_values(row["tags"]), summary=row["summary"], content=row["content"],
    )


def _valid_entries(
    rows: Sequence[sqlite3.Row],
    *,
    operation: str,
) -> tuple[KnowledgeEntry, ...]:
    entries: list[KnowledgeEntry] = []
    for row in rows:
        try:
            entries.append(entry_from_row(row))
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            logger.warning(
                "[public-knowledge] skipped invalid entry operation=%s rowid=%s type=%s",
                operation,
                row["rowid"],
                type(exc).__name__,
            )
    return tuple(entries)


def _database_identity(path: Path) -> tuple[int, int] | None:
    try:
        stat = path.stat()
    except OSError:
        return None
    return int(stat.st_dev), int(stat.st_ino)
