from __future__ import annotations

import sqlite3

import pytest

from knowledge.chunking import (
    CHUNKER_VERSION,
    EMBEDDING_INPUT_VERSION,
    MAX_CHUNKS_PER_ENTRY,
    MAX_EMBEDDING_CHARS,
    derive_knowledge_chunks,
    knowledge_query_embedding_text,
)
from knowledge import store as store_module
from knowledge.models import KnowledgeEntry
from knowledge.store import KnowledgeStore


def _entry(
    *,
    content: str,
    tags=("source:test",),
    summary="summary",
    title="Hybrid retrieval",
):
    return KnowledgeEntry(
        title=title,
        terms={"alias": ("hybrid",), "recognition": ("RAG",)},
        tags=tags,
        summary=summary,
        content=content,
    )


def test_chunking_is_deterministic_and_tracks_markdown_heading():
    entry = _entry(content="# Origin\n\nFirst paragraph.\n\nSecond paragraph.")
    first = derive_knowledge_chunks(entry, entry_key="source:test:Hybrid retrieval")
    second = derive_knowledge_chunks(entry, entry_key="source:test:Hybrid retrieval")

    assert first == second
    assert first[0].heading == "Origin"
    assert first[0].embedding_text.startswith("Document:\n")
    assert "Title: Hybrid retrieval" in first[0].embedding_text
    assert "Aliases: hybrid" in first[0].embedding_text


def test_query_and_document_embedding_inputs_have_independent_prefixes():
    chunks = derive_knowledge_chunks(
        _entry(content="Document body"),
        entry_key="source:test:Hybrid retrieval",
    )

    assert knowledge_query_embedding_text("  how does hybrid RAG work?  ") == (
        "Query: how does hybrid RAG work?"
    )
    assert chunks[0].embedding_text.splitlines()[0] == "Document:"
    assert "Query:" not in chunks[0].embedding_text


def test_chunking_is_bounded_for_long_unbroken_content():
    chunks = derive_knowledge_chunks(
        _entry(content="x" * 50_000),
        entry_key="source:test:Hybrid retrieval",
    )
    assert len(chunks) == MAX_CHUNKS_PER_ENTRY
    assert all(len(chunk.chunk_text) <= 1_200 for chunk in chunks)
    assert all(len(chunk.embedding_text) <= MAX_EMBEDDING_CHARS for chunk in chunks)
    assert chunks[0].chunk_text[-120:] == chunks[1].chunk_text[:120]


def test_chunking_balances_a_one_character_tail():
    chunks = derive_knowledge_chunks(
        _entry(content="x" * 1_201),
        entry_key="source:test:Hybrid retrieval",
    )

    assert len(chunks) == 2
    assert min(len(chunk.chunk_text) for chunk in chunks) > 600
    assert chunks[0].chunk_text[-120:] == chunks[1].chunk_text[:120]


def test_schema_v6_migration_keeps_fts_and_backfills_lazily(tmp_path):
    path = tmp_path / "knowledge.db"
    connection = sqlite3.connect(path)
    connection.executescript("""
        CREATE TABLE metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL);
        INSERT INTO metadata VALUES ('schema_version', '5');
        CREATE TABLE entries (
            title TEXT NOT NULL,
            terms TEXT NOT NULL,
            tags TEXT NOT NULL,
            summary TEXT NOT NULL,
            content TEXT NOT NULL
        );
        CREATE VIRTUAL TABLE entries_fts USING fts5(
            entry_rowid UNINDEXED, title, terms, tags, summary, content,
            tokenize='unicode61'
        );
        INSERT INTO entries VALUES (
            'Legacy title', '{"alias":["old"],"recognition":[]}',
            '["source:test"]', 'Legacy summary', 'Legacy semantic content'
        );
        INSERT INTO entries_fts VALUES (
            1, 'Legacy title', 'old', 'source:test',
            'Legacy summary', 'Legacy semantic content'
        );
    """)
    connection.commit()
    connection.close()

    store = KnowledgeStore(path)
    assert store.count() == 1
    assert store.query_fts('"Legacy"', limit=1)
    assert store.chunk_status()["entries_missing_chunks"] == 1
    assert store.backfill_missing_chunks(limit=1) == 1
    assert store.chunk_status()["chunks_pending"] == 1


def test_schema_v6_to_v7_preserves_fts_and_current_vectors(tmp_path):
    path = tmp_path / "knowledge.db"
    connection = sqlite3.connect(path)
    connection.executescript("""
        CREATE TABLE metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL);
        INSERT INTO metadata VALUES ('schema_version', '6');
        INSERT INTO metadata VALUES ('embedding_input_version', '1');
        INSERT INTO metadata VALUES ('chunks_revision', '3');
        CREATE TABLE entries (
            title TEXT NOT NULL, terms TEXT NOT NULL, tags TEXT NOT NULL,
            summary TEXT NOT NULL, content TEXT NOT NULL
        );
        CREATE VIRTUAL TABLE entries_fts USING fts5(
            entry_rowid UNINDEXED, title, terms, tags, summary, content,
            tokenize='unicode61'
        );
        INSERT INTO entries VALUES (
            'V6 title', '{"alias":[],"recognition":[]}',
            '["source:test"]', '', 'kept body'
        );
        INSERT INTO entries_fts VALUES (1, 'V6 title', '', 'source:test', '', 'kept body');
        CREATE TABLE knowledge_chunks (
            chunk_id TEXT PRIMARY KEY, entry_rowid INTEGER NOT NULL,
            chunk_index INTEGER NOT NULL, heading TEXT NOT NULL DEFAULT '',
            chunk_text TEXT NOT NULL, content_hash TEXT NOT NULL,
            embedding_model_id TEXT, embedding_dimensions INTEGER, embedding BLOB,
            embedding_status TEXT NOT NULL DEFAULT 'pending',
            embedding_attempts INTEGER NOT NULL DEFAULT 0,
            next_retry_at INTEGER NOT NULL DEFAULT 0,
            last_error_code TEXT NOT NULL DEFAULT '',
            UNIQUE(entry_rowid, chunk_index)
        );
        INSERT INTO knowledge_chunks VALUES (
            'v6-chunk', 1, 0, '', 'kept body', 'hash', 'fixture', 2,
            X'00000000', 'ready', 0, 0, ''
        );
    """)
    connection.execute(
        "INSERT INTO metadata VALUES ('chunker_version', ?)",
        (str(CHUNKER_VERSION),),
    )
    connection.commit()
    connection.close()

    store = KnowledgeStore(path)

    assert store.query_fts('"kept"', limit=1)
    with store._connection() as connection:
        row = connection.execute("SELECT * FROM knowledge_chunks").fetchone()
        assert row["embedding_policy"] == "local"
        assert row["embedding_status"] == "ready"
        assert row["embedding"] == b"\x00\x00\x00\x00"
        assert (
            connection.execute(
                "SELECT value FROM metadata WHERE key='schema_version'"
            ).fetchone()[0]
            == "7"
        )


def test_missing_embedding_input_version_only_clears_derived_chunks(tmp_path):
    path = tmp_path / "knowledge.db"
    store = KnowledgeStore(path)
    store.upsert(_entry(content="The answer remains searchable."))
    with store._connection(writable=True) as connection:
        connection.execute(
            "UPDATE knowledge_chunks SET embedding_status='ready', "
            "embedding_model_id='old-contract', embedding_dimensions=2, embedding=?",
            (b"\x00\x00\x00\x00",),
        )
        connection.execute("DELETE FROM metadata WHERE key='embedding_input_version'")
        old_revision = int(
            connection.execute(
                "SELECT value FROM metadata WHERE key='chunks_revision'"
            ).fetchone()[0]
        )

    # Force a fresh database-open initialization, as an existing v6 database
    # from before the input contract version key would experience on upgrade.
    store_module._INITIALIZED_DATABASES.pop(str(path.resolve()), None)
    reopened = KnowledgeStore(path)

    assert reopened.count() == 1
    assert reopened.query_fts('"answer"', limit=1)
    with reopened._connection() as connection:
        assert (
            connection.execute("SELECT COUNT(*) FROM knowledge_chunks").fetchone()[0]
            == 0
        )
        assert connection.execute(
            "SELECT value FROM metadata WHERE key='embedding_input_version'"
        ).fetchone()[0] == str(EMBEDDING_INPUT_VERSION)
        assert (
            int(
                connection.execute(
                    "SELECT value FROM metadata WHERE key='chunks_revision'"
                ).fetchone()[0]
            )
            == old_revision + 1
        )
        assert connection.execute("SELECT COUNT(*) FROM entries_fts").fetchone()[0] == 1

    assert reopened.chunk_status()["entries_missing_chunks"] == 1


def test_current_embedding_input_version_keeps_derived_chunks_on_reopen(tmp_path):
    path = tmp_path / "knowledge.db"
    store = KnowledgeStore(path)
    store.upsert(_entry(content="Current input contract."))
    before = store.chunk_status()

    store_module._INITIALIZED_DATABASES.pop(str(path.resolve()), None)
    reopened = KnowledgeStore(path)
    after = reopened.chunk_status()

    assert after["chunks_total"] == before["chunks_total"] == 1
    assert after["chunks_revision"] == before["chunks_revision"]


def test_changed_chunker_version_clears_derived_chunks_once(tmp_path):
    path = tmp_path / "knowledge.db"
    store = KnowledgeStore(path)
    store.upsert(_entry(content="Chunker contract fixture."))
    with store._connection(writable=True) as connection:
        connection.execute(
            "UPDATE knowledge_chunks SET embedding_status='ready', "
            "embedding_model_id='fixture', embedding_dimensions=2, embedding=?",
            (b"\x00\x00\x00\x00",),
        )
        connection.execute(
            "UPDATE metadata SET value='0' WHERE key='chunker_version'"
        )
        old_revision = int(
            connection.execute(
                "SELECT value FROM metadata WHERE key='chunks_revision'"
            ).fetchone()[0]
        )

    store_module._INITIALIZED_DATABASES.pop(str(path.resolve()), None)
    reopened = KnowledgeStore(path)

    with reopened._connection() as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM knowledge_chunks"
        ).fetchone()[0] == 0
        assert connection.execute(
            "SELECT value FROM metadata WHERE key='chunker_version'"
        ).fetchone()[0] == str(CHUNKER_VERSION)
        assert int(
            connection.execute(
                "SELECT value FROM metadata WHERE key='chunks_revision'"
            ).fetchone()[0]
        ) == old_revision + 1
    assert reopened.query_fts('"Chunker"', limit=1)


def test_backfill_skips_malformed_entry_and_processes_next_valid_row(tmp_path):
    store = KnowledgeStore(tmp_path / "knowledge.db")
    store.upsert_many(
        (
            _entry(content="broken", title="Broken entry"),
            _entry(content="valid", title="Valid entry"),
        )
    )
    with store._connection(writable=True) as connection:
        connection.execute(
            "UPDATE entries SET terms='{' WHERE title='Broken entry'"
        )
        connection.execute("DELETE FROM knowledge_chunks")

    assert store.backfill_missing_chunks(limit=1) == 1
    with store._connection() as connection:
        chunk_titles = {
            str(row["title"])
            for row in connection.execute(
                "SELECT entries.title FROM knowledge_chunks JOIN entries "
                "ON entries.rowid=knowledge_chunks.entry_rowid"
            )
        }
    assert chunk_titles == {"Valid entry"}
    assert store.chunk_status()["entries_missing_chunks"] == 1


def test_backfill_pages_past_more_than_one_page_of_malformed_entries(tmp_path):
    store = KnowledgeStore(tmp_path / "knowledge.db")
    broken = tuple(
        _entry(content=f"broken {index}", title=f"Broken entry {index:03d}")
        for index in range(65)
    )
    store.upsert_many((*broken, _entry(content="valid", title="Valid entry")))
    with store._connection(writable=True) as connection:
        connection.execute("UPDATE entries SET terms='{' WHERE title LIKE 'Broken entry %'")
        connection.execute("DELETE FROM knowledge_chunks")

    assert store.backfill_missing_chunks(limit=1) == 1
    with store._connection() as connection:
        chunk_titles = {
            str(row["title"])
            for row in connection.execute(
                "SELECT entries.title FROM knowledge_chunks JOIN entries "
                "ON entries.rowid=knowledge_chunks.entry_rowid"
            )
        }
    assert chunk_titles == {"Valid entry"}
    assert store.chunk_status()["entries_missing_chunks"] == len(broken)


def test_tag_only_update_preserves_ready_embedding(tmp_path):
    store = KnowledgeStore(tmp_path / "knowledge.db")
    original = _entry(content="same content")
    store.upsert(original)
    with store._connection(writable=True) as connection:
        connection.execute(
            "UPDATE knowledge_chunks SET embedding_status='ready', "
            "embedding_model_id='fixture', embedding_dimensions=2, embedding=?",
            (b"\x00\x00\x00\x00",),
        )

    store.upsert(_entry(content="same content", tags=("source:test", "topic:new")))
    with store._connection() as connection:
        row = connection.execute("SELECT * FROM knowledge_chunks").fetchone()
    assert row["embedding_status"] == "ready"
    assert row["embedding"] == b"\x00\x00\x00\x00"


def test_content_update_reuses_unchanged_chunks_and_deletes_orphans(tmp_path):
    store = KnowledgeStore(tmp_path / "knowledge.db")
    store.upsert(_entry(content="# A\n\n" + "a" * 1_100 + "\n\n# B\n\n" + "b" * 1_100))
    with store._connection(writable=True) as connection:
        rows = connection.execute(
            "SELECT chunk_id FROM knowledge_chunks ORDER BY chunk_index"
        ).fetchall()
        connection.execute(
            "UPDATE knowledge_chunks SET embedding_status='ready', embedding_model_id='fixture', "
            "embedding_dimensions=2, embedding=?",
            (b"\x00\x00\x00\x00",),
        )
    original_ids = {row["chunk_id"] for row in rows}

    store.upsert(
        _entry(
            content="# New\n\nnew\n\n# A\n\n"
            + "a" * 1_100
            + "\n\n# B\n\n"
            + "b" * 1_100
        )
    )
    with store._connection() as connection:
        updated = connection.execute("SELECT * FROM knowledge_chunks").fetchall()
    reused = [row for row in updated if row["chunk_id"] in original_ids]
    assert reused
    assert all(row["embedding_status"] == "ready" for row in reused)

    store.replace_source("source:test", ())
    with store._connection() as connection:
        assert (
            connection.execute("SELECT COUNT(*) FROM knowledge_chunks").fetchone()[0]
            == 0
        )


def test_source_deletion_invalidates_ready_vector_cache_revision(tmp_path):
    store = KnowledgeStore(tmp_path / "knowledge.db")
    store.upsert(_entry(content="ready content"))
    before = store.chunks_revision()

    store.replace_source("source:test", ())

    assert store.chunks_revision() > before


def test_embedding_result_cannot_overwrite_a_changed_chunk(tmp_path):
    store = KnowledgeStore(tmp_path / "knowledge.db")
    store.upsert(_entry(content="old text"))
    pending = store.pending_embedding_chunks(model_id="fixture", limit=1)[0]

    store.upsert(_entry(content="new text"))

    assert (
        store.store_chunk_embedding(
            chunk_id=str(pending["chunk_id"]),
            content_hash=str(pending["content_hash"]),
            model_id="fixture",
            dimensions=2,
            embedding=b"\x00\x00\x00\x00",
        )
        is False
    )
    assert store.chunk_status()["chunks_ready"] == 0


def test_model_change_marks_only_other_ready_vectors_stale(tmp_path):
    store = KnowledgeStore(tmp_path / "knowledge.db")
    store.upsert(_entry(content="same content"))
    pending = store.pending_embedding_chunks(model_id="old-model", limit=1)[0]
    assert store.store_chunk_embedding(
        chunk_id=str(pending["chunk_id"]),
        content_hash=str(pending["content_hash"]),
        model_id="old-model",
        dimensions=2,
        embedding=b"\x00\x00\x00\x00",
    )

    assert store.mark_other_models_stale("new-model") == 1
    status = store.chunk_status()
    assert status["chunks_ready"] == 0
    assert status["chunks_stale"] == 1


def test_model_change_stales_local_and_prebuilt_ready_vectors_together(tmp_path):
    store = KnowledgeStore(tmp_path / "knowledge.db")
    store.upsert(_entry(content="local model content"))
    store.upsert(
        _entry(content="prebuilt model content", tags=("source:prebuilt",))
    )
    store.set_source_embedding_policy("source:prebuilt", "prebuilt_only")
    local_pending = store.pending_embedding_chunks(
        model_id="old-model",
        limit=1,
    )
    prebuilt_pending = store.pending_embedding_chunks(
        model_id="old-model", limit=1, embedding_policy="prebuilt_only"
    )
    assert len(local_pending) == len(prebuilt_pending) == 1
    for chunk, policy in (
        (local_pending[0], "local"),
        (prebuilt_pending[0], "prebuilt_only"),
    ):
        assert store.store_chunk_embedding(
            chunk_id=str(chunk["chunk_id"]),
            content_hash=str(chunk["content_hash"]),
            model_id="old-model",
            dimensions=2,
            embedding=b"\x00\x00\x00\x00",
            embedding_policy=policy,
        )

    assert store.mark_incompatible_models_stale("new-model") == 2
    status = store.chunk_status()
    assert status["chunks_ready"] == 0
    assert status["chunks_stale"] == 2


def test_exhausted_embedding_failure_is_not_retried_forever(tmp_path):
    store = KnowledgeStore(tmp_path / "knowledge.db")
    store.upsert(_entry(content="failure boundary"))
    pending = store.pending_embedding_chunks(model_id="fixture", limit=1)[0]
    with store._connection(writable=True) as connection:
        connection.execute(
            "UPDATE knowledge_chunks SET embedding_status='failed', "
            "embedding_attempts=8, next_retry_at=0 WHERE chunk_id=?",
            (pending["chunk_id"],),
        )

    assert store.pending_embedding_chunks(model_id="fixture", limit=1) == ()
    status = store.chunk_status()
    assert status["chunks_failed"] == 1
    assert status["chunks_failed_retryable_now"] == 0
    assert status["chunks_failed_waiting"] == 0
    assert status["chunks_failed_exhausted"] == 1


def test_failed_embedding_status_distinguishes_ready_and_waiting_retries(tmp_path):
    store = KnowledgeStore(tmp_path / "knowledge.db")
    store.upsert(_entry(content="# A\n\n" + "a" * 1_000 + "\n\n# B\n\n" + "b" * 1_000))
    with store._connection(writable=True) as connection:
        rows = connection.execute(
            "SELECT chunk_id FROM knowledge_chunks ORDER BY chunk_index"
        ).fetchall()
        assert len(rows) >= 2
        connection.execute(
            "UPDATE knowledge_chunks SET embedding_status='failed', "
            "embedding_attempts=1, next_retry_at=0 WHERE chunk_id=?",
            (rows[0]["chunk_id"],),
        )
        connection.execute(
            "UPDATE knowledge_chunks SET embedding_status='failed', "
            "embedding_attempts=2, next_retry_at=32503680000 WHERE chunk_id=?",
            (rows[1]["chunk_id"],),
        )

    status = store.chunk_status()
    assert status["chunks_failed_retryable_now"] == 1
    assert status["chunks_failed_waiting"] == 1
    assert status["chunks_failed_exhausted"] == 0


def test_source_replacement_reuses_unchanged_vectors(tmp_path):
    store = KnowledgeStore(tmp_path / "knowledge.db")
    original = _entry(content="same packaged content")
    store.replace_source("source:test", (original,))
    pending = store.pending_embedding_chunks(model_id="fixture", limit=1)[0]
    assert store.store_chunk_embedding(
        chunk_id=str(pending["chunk_id"]),
        content_hash=str(pending["content_hash"]),
        model_id="fixture",
        dimensions=2,
        embedding=b"\x00\x00\x00\x00",
    )

    result = store.replace_source("source:test", (original,))

    assert result[0].unchanged is True
    assert store.chunk_status()["chunks_ready"] == 1


def test_prebuilt_only_chunks_are_not_local_embedding_work(tmp_path):
    store = KnowledgeStore(tmp_path / "knowledge.db")
    entry = _entry(content="prebuilt content")

    store.replace_source(
        "source:test",
        (entry,),
        embedding_policy="prebuilt_only",
    )

    assert store.pending_embedding_chunks(model_id="fixture", limit=8) == ()
    assert store.has_embedding_work() is False
    assert store.embedding_policy_counts(source_tag="source:test") == {
        "local": 0,
        "prebuilt_only": 1,
    }
    assert store.source_chunk_status("source:test") == {
        "chunks_total": 1,
        "chunks_ready": 0,
        "chunks_prebuilt_only": 1,
    }

    assert store.set_source_embedding_policy("source:test", "local") == 1
    assert store.has_embedding_work() is True
    assert len(store.pending_embedding_chunks(model_id="fixture", limit=8)) == 1


def test_unregistered_community_backfill_fails_closed_to_prebuilt_only(tmp_path):
    store = KnowledgeStore(tmp_path / "knowledge.db")
    source_tag = "source:community.unregistered"
    entry = _entry(content="community content", tags=(source_tag,))
    store.replace_source(source_tag, (entry,), embedding_policy="prebuilt_only")
    with store._connection(writable=True) as connection:
        connection.execute("DELETE FROM knowledge_chunks")

    assert store.backfill_missing_chunks(limit=1) == 1
    assert store.embedding_policy_counts(source_tag=source_tag) == {
        "local": 0,
        "prebuilt_only": 1,
    }
    assert store.has_embedding_work() is False


def test_strict_embedding_batch_rolls_back_when_one_chunk_is_stale(tmp_path):
    store = KnowledgeStore(tmp_path / "knowledge.db")
    store.upsert(_entry(content="# A\n\n" + "a" * 1_000 + "\n\n# B\n\n" + "b" * 1_000))
    with store._connection() as connection:
        rows = connection.execute(
            "SELECT chunk_id, content_hash FROM knowledge_chunks ORDER BY chunk_index"
        ).fetchall()
    assert len(rows) >= 2
    records = [
        {
            "chunk_id": str(row["chunk_id"]),
            "content_hash": str(row["content_hash"]),
            "model_id": "fixture",
            "dimensions": 2,
            "embedding": b"\x00\x00\x00\x00",
        }
        for row in rows[:2]
    ]
    records[1]["content_hash"] = "stale-hash"

    with pytest.raises(ValueError):
        store.store_chunk_embeddings_strict(records)

    with store._connection() as connection:
        statuses = connection.execute(
            "SELECT embedding_status FROM knowledge_chunks ORDER BY chunk_index"
        ).fetchall()
    assert all(row["embedding_status"] == "pending" for row in statuses)


def test_background_embedding_batch_publishes_matching_vectors_once(tmp_path):
    store = KnowledgeStore(tmp_path / "knowledge.db")
    store.upsert(_entry(content="# A\n\n" + "a" * 1_000 + "\n\n# B\n\n" + "b" * 1_000))
    pending = store.pending_embedding_chunks(model_id="fixture", limit=2)
    assert len(pending) == 2
    records = [
        {
            "chunk_id": str(chunk["chunk_id"]),
            "content_hash": str(chunk["content_hash"]),
            "model_id": "fixture",
            "dimensions": 2,
            "embedding": b"\x00\x00\x00\x00",
        }
        for chunk in pending
    ]
    records[1]["content_hash"] = "stale-hash"
    before_revision = store.chunks_revision()

    assert store.store_chunk_embedding_batch(records) == 1

    status = store.chunk_status()
    assert status["chunks_ready"] == 1
    assert status["chunks_pending"] >= 1
    assert store.chunks_revision() == before_revision + 1
