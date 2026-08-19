from __future__ import annotations

import json
import logging
import sqlite3

from knowledge.collection_overrides import load_auto_context_overrides
from knowledge.community_collections import load_community_collections
from knowledge.engine.catalog_overrides import load_disabled_entries
from knowledge.engine.models import KnowledgeEntry
from knowledge.engine.retrieval import KnowledgeRetriever
from knowledge.engine.source_registry import resolve_source
from knowledge.engine.store import KnowledgeStore
from knowledge.packs import list_installed_packs


def _entry(title: str) -> KnowledgeEntry:
    return KnowledgeEntry(
        title=title,
        terms={"alias": (), "recognition": ()},
        tags=("source:fixture",),
        summary=f"Summary for {title}",
        content=f"Content for {title}",
    )


def test_legacy_migration_backs_up_wal_and_backfills_fts(tmp_path) -> None:
    database_path = tmp_path / "knowledge.db"
    writer = sqlite3.connect(database_path)
    writer.execute("PRAGMA journal_mode=WAL")
    writer.execute(
        "CREATE TABLE entries (title TEXT, aliases TEXT, tags TEXT, "
        "summary TEXT, content TEXT)"
    )
    writer.commit()
    writer.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    writer.execute(
        "INSERT INTO entries VALUES (?, ?, ?, ?, ?)",
        (
            "legacy phrase",
            json.dumps(["old alias"]),
            json.dumps(["source:fixture"]),
            "legacy summary",
            "legacy content",
        ),
    )
    writer.commit()
    assert database_path.with_name("knowledge.db-wal").is_file()

    store = KnowledgeStore(database_path)
    assert [
        hit.entry.title for hit in KnowledgeRetriever(store).search("legacy phrase")
    ] == ["legacy phrase"]

    backup = database_path.with_suffix(".db.legacy.bak")
    connection = sqlite3.connect(backup)
    try:
        assert connection.execute("SELECT title FROM entries").fetchone()[0] == (
            "legacy phrase"
        )
    finally:
        connection.close()
    writer.close()


def test_legacy_migration_skips_bad_fts_rows_and_keeps_valid_rows(
    tmp_path,
    caplog,
) -> None:
    database_path = tmp_path / "knowledge.db"
    connection = sqlite3.connect(database_path)
    try:
        connection.execute(
            "CREATE TABLE entries (title TEXT, aliases TEXT, tags TEXT, "
            "summary TEXT, content TEXT)"
        )
        connection.executemany(
            "INSERT INTO entries VALUES (?, ?, ?, ?, ?)",
            (
                ("invalid legacy", "[]", "[]", "private summary", "private content"),
                (
                    "valid legacy",
                    "[]",
                    '["source:fixture"]',
                    "valid summary",
                    "valid content",
                ),
            ),
        )
        connection.commit()
    finally:
        connection.close()

    caplog.set_level(logging.WARNING, logger="knowledge.engine.store")
    store = KnowledgeStore(database_path)

    assert [
        hit.entry.title for hit in KnowledgeRetriever(store).search("valid legacy")
    ] == ["valid legacy"]
    assert [entry.title for entry in store.list_active_entries()] == ["valid legacy"]
    assert "operation=migrate_legacy_entries" in caplog.text
    assert "private content" not in caplog.text


def test_upsert_converges_historical_duplicate_rows(tmp_path) -> None:
    store = KnowledgeStore(tmp_path / "knowledge.db")
    store.upsert(_entry("duplicate"))
    with sqlite3.connect(store.database_path) as connection:
        connection.execute(
            "INSERT INTO entries(title, terms, tags, summary, content) "
            "SELECT title, terms, tags, summary, content FROM entries WHERE title = ?",
            ("duplicate",),
        )

    updated = KnowledgeEntry(
        title="duplicate",
        terms={"alias": (), "recognition": ()},
        tags=("source:fixture",),
        summary="Updated summary",
        content="Updated content",
    )
    result = store.upsert(updated)

    assert result.updated is True
    assert store.count() == 1
    assert store.get_entry("source:fixture", "duplicate") == updated


def test_entry_lists_skip_only_bad_rows_and_log_no_content(tmp_path, caplog) -> None:
    store = KnowledgeStore(tmp_path / "knowledge.db")
    store.upsert_many((_entry("alpha"), _entry("private row"), _entry("omega")))
    with sqlite3.connect(store.database_path) as connection:
        connection.execute(
            "UPDATE entries SET terms = ? WHERE title = ?",
            ('{"secret entry content":', "private row"),
        )
        connection.commit()

    caplog.set_level(logging.WARNING, logger="knowledge.engine.store")
    assert [entry.title for entry in store.list_active_entries()] == ["alpha", "omega"]
    assert [entry.title for entry in store.list_entries(source_tag="source:fixture")] == [
        "alpha",
        "omega",
    ]
    assert "operation=list_active_entries" in caplog.text
    assert "operation=list_entries" in caplog.text
    assert "secret entry content" not in caplog.text


def test_query_like_uses_python_normalization_and_literal_wildcards(tmp_path) -> None:
    store = KnowledgeStore(tmp_path / "knowledge.db")
    store.upsert_many((_entry("Ｆｏｏ—Bar"), _entry("a_b"), _entry("axb")))

    normalized = store.query_like("foo bar", limit=10)
    literal_underscore = store.query_like("a_b", limit=10)

    assert [row["title"] for row in normalized] == ["Foo—Bar"]
    assert [row["title"] for row in literal_underscore] == ["a_b"]


def test_local_json_loaders_handle_invalid_utf8_with_safe_logs(tmp_path, caplog) -> None:
    invalid = b'{"private content":"\xff"}'
    collection_overrides = tmp_path / "collection.overrides.json"
    catalog_overrides = tmp_path / "catalog.override.json"
    collections = tmp_path / "collections.json"
    packs = tmp_path / "packs.json"
    for path in (collection_overrides, catalog_overrides, collections, packs):
        path.write_bytes(invalid)

    caplog.set_level(logging.WARNING)
    assert load_auto_context_overrides(collection_overrides) == {}
    assert load_disabled_entries(catalog_overrides) == frozenset()
    assert load_community_collections(tmp_path) == {}
    assert list_installed_packs(tmp_path / "knowledge.db") == ()
    assert resolve_source("source:fixture", database_path=tmp_path / "knowledge.db").name == (
        "fixture"
    )
    assert "UnicodeDecodeError" in caplog.text
    assert "private content" not in caplog.text


def test_malformed_pack_registry_emits_warning(tmp_path, caplog) -> None:
    (tmp_path / "packs.json").write_text("[]", encoding="utf-8")
    caplog.set_level(logging.WARNING, logger="knowledge.packs")

    assert list_installed_packs(tmp_path / "knowledge.db") == ()
    assert "invalid pack registry structure" in caplog.text
