from __future__ import annotations

import importlib.util
import asyncio
import json
import sqlite3
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest


SCRIPT_PATH = (
    Path(__file__).resolve().parents[2] / "scripts" / "rebuild_knowledge_index.py"
)
SPEC = importlib.util.spec_from_file_location("rebuild_knowledge_index", SCRIPT_PATH)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


def _write_staged_job_fixture(
    root: Path,
    *,
    pack_id: str,
    suffix: str = "0123456789ab",
    created_at: int = 1,
    state_created_at: object = 1,
    state: str = "queued",
) -> Path:
    job_id = f"{pack_id}-{suffix}"
    job_dir = root / ".staging" / job_id
    job_dir.mkdir(parents=True)
    (job_dir / "identity.json").write_text(
        json.dumps(
            {
                "job_id": job_id,
                "pack_id": pack_id,
                "created_at": created_at,
                "entries_total": 1,
                "chunks_total": 1,
                "content_bytes": 1,
                "pack_sha256": "0" * 64,
                "has_subscription": False,
                "subscription_sha256": "",
            }
        ),
        encoding="utf-8",
    )
    (job_dir / "state.json").write_text(
        json.dumps(
            {
                "job_id": job_id,
                "pack_id": pack_id,
                "state": state,
                "created_at": state_created_at,
                "updated_at": created_at,
            }
        ),
        encoding="utf-8",
    )
    return job_dir


def _write_v5_database(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(path) as connection:
        connection.executescript(
            """
            CREATE TABLE metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL);
            INSERT INTO metadata(key, value) VALUES ('schema_version', '5');
            CREATE TABLE entries (
                title TEXT NOT NULL,
                terms TEXT NOT NULL,
                tags TEXT NOT NULL,
                summary TEXT NOT NULL,
                content TEXT NOT NULL
            );
            """
        )
        connection.execute(
            "INSERT INTO entries(title, terms, tags, summary, content) "
            "VALUES (?, ?, ?, ?, ?)",
            (
                "Test card",
                json.dumps({"alias": ["test"], "recognition": []}),
                json.dumps(["source:test"]),
                "A summary",
                "A paragraph that can be indexed.",
            ),
        )


def _write_v6_chunks(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(path) as connection:
        connection.executescript(
            """
            CREATE TABLE metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL);
            INSERT INTO metadata(key, value) VALUES ('schema_version', '6');
            CREATE TABLE entries (
                title TEXT NOT NULL,
                terms TEXT NOT NULL,
                tags TEXT NOT NULL,
                summary TEXT NOT NULL,
                content TEXT NOT NULL
            );
            INSERT INTO entries VALUES ('Test', '{}', '[]', '', 'body');
            CREATE TABLE knowledge_chunks (
                chunk_id TEXT PRIMARY KEY,
                entry_rowid INTEGER NOT NULL,
                chunk_index INTEGER NOT NULL,
                heading TEXT NOT NULL DEFAULT '',
                chunk_text TEXT NOT NULL,
                content_hash TEXT NOT NULL,
                embedding_model_id TEXT,
                embedding_dimensions INTEGER,
                embedding BLOB,
                embedding_status TEXT NOT NULL,
                embedding_attempts INTEGER NOT NULL DEFAULT 0,
                next_retry_at INTEGER NOT NULL DEFAULT 0,
                last_error_code TEXT NOT NULL DEFAULT ''
            );
            """
        )


def _insert_failed_chunk(
    database: Path,
    *,
    chunk_id: str,
    attempts: int,
    next_retry_at: int,
) -> None:
    with sqlite3.connect(database) as connection:
        connection.execute(
            "INSERT INTO knowledge_chunks("
            "chunk_id, entry_rowid, chunk_index, chunk_text, content_hash, "
            "embedding_status, embedding_attempts, next_retry_at"
            ") VALUES (?, 1, ?, 'body', ?, 'failed', ?, ?)",
            (chunk_id, attempts, chunk_id, attempts, next_retry_at),
        )


def test_status_is_read_only_and_does_not_migrate_v5(tmp_path: Path) -> None:
    database = tmp_path / "knowledge.db"
    _write_v5_database(database)

    status = MODULE.inspect_database(database)

    assert status["schema_version"] == 5
    assert status["entries_total"] == 1
    assert status["entries_missing_chunks"] == 1
    with sqlite3.connect(database) as connection:
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
    assert "knowledge_chunks" not in tables


def test_status_lists_staging_jobs_without_opening_their_database(
    tmp_path: Path,
) -> None:
    job_dir = _write_staged_job_fixture(
        tmp_path,
        pack_id="fixture-pack",
        state="embedding",
    )
    staging_database = job_dir / "knowledge.db"

    jobs = MODULE.inspect_pack_jobs(tmp_path)

    assert jobs[0]["state"] == "embedding"
    assert not staging_database.exists()


def test_status_sorts_jobs_safely_when_created_at_is_damaged(tmp_path: Path) -> None:
    created_values = {
        "valid-new": "10",
        "valid-old": 5,
        "invalid-bool": True,
        "invalid-float": 2.5,
        "invalid-list": [],
        "invalid-negative": -1,
        "invalid-unicode": "１２",
    }
    job_ids: dict[str, str] = {}
    for pack_id, state_created_at in created_values.items():
        trusted_created_at = (
            int(state_created_at) if pack_id.startswith("valid-") else 0
        )
        job_dir = _write_staged_job_fixture(
            tmp_path,
            pack_id=pack_id,
            created_at=trusted_created_at,
            state_created_at=state_created_at,
        )
        job_ids[pack_id] = job_dir.name

    jobs = MODULE.inspect_pack_jobs(tmp_path)

    assert [item["job_id"] for item in jobs] == [
        job_ids["valid-new"],
        job_ids["valid-old"],
        job_ids["invalid-bool"],
        job_ids["invalid-float"],
        job_ids["invalid-list"],
        job_ids["invalid-negative"],
        job_ids["invalid-unicode"],
    ]
    assert all(
        item["state"] == "degraded"
        and item["reason"] == "invalid_job_timestamps"
        and item["created_at"] == 0
        for item in jobs
        if item["job_id"].startswith("invalid-")
    )


def test_full_dry_run_counts_derived_chunks_without_writing(tmp_path: Path) -> None:
    database = tmp_path / "knowledge.db"
    _write_v5_database(database)
    target = MODULE.KnowledgeTarget(database)

    plan = MODULE.dry_run_plan(target, full=True)

    assert plan["valid_entries"] == 1
    assert plan["derived_chunks_after_rebuild"] == 1
    assert plan["affected_entries"] == 1
    assert plan["affected_chunks"] == 1
    with sqlite3.connect(database) as connection:
        schema_version = connection.execute(
            "SELECT value FROM metadata WHERE key='schema_version'"
        ).fetchone()[0]
    assert schema_version == "5"


@pytest.mark.parametrize("value", ("0", "129", "not-a-number"))
def test_batch_size_rejects_values_outside_bounds(value: str) -> None:
    with pytest.raises(Exception):
        MODULE._batch_size(value)


@pytest.mark.parametrize("value", ("1", "32", "128"))
def test_batch_size_accepts_documented_bounds(value: str) -> None:
    assert MODULE._batch_size(value) == int(value)


def test_default_batch_size_is_safe_microbatch() -> None:
    args = MODULE._build_parser().parse_args(["--rebuild"])

    assert MODULE.DEFAULT_BATCH_SIZE == 4
    assert args.batch_size == 4


@pytest.mark.parametrize(
    ("ready", "batch_size", "expected"),
    ((19_999, 4, 1), (20_000, 4, 0), (19_990, 4, 4)),
)
def test_ready_vector_work_budget_never_crosses_global_cap(
    ready: int,
    batch_size: int,
    expected: int,
) -> None:
    assert MODULE._ready_vector_work_budget(
        {"chunks_ready": ready},
        batch_size=batch_size,
        max_ready_vectors=20_000,
    ) == expected


def test_enable_local_pack_requires_rebuild_action(tmp_path: Path) -> None:
    args = MODULE._build_parser().parse_args(
        [
            "--status",
            "--enable-local-pack",
            "fixture-pack",
            "--knowledge-root",
            str(tmp_path),
        ]
    )

    assert asyncio.run(MODULE._run(args)) == 2


def test_enable_local_pack_dry_run_locates_registry_without_mutating_it(
    tmp_path: Path,
) -> None:
    database = tmp_path / "knowledge.db"
    registry = {
        "schema_version": 1,
        "packs": {
            "fixture-pack": {
                "pack_id": "fixture-pack",
                "source_tag": "source:community.fixture-pack",
                "local_embedding_enabled": False,
            }
        },
    }
    registry_path = database.with_name("packs.json")
    registry_path.write_text(json.dumps(registry), encoding="utf-8")
    args = MODULE._build_parser().parse_args(
        [
            "--rebuild",
            "--dry-run",
            "--enable-local-pack",
            "fixture-pack",
            "--knowledge-root",
            str(tmp_path),
        ]
    )

    assert asyncio.run(MODULE._run(args)) == 0
    assert json.loads(registry_path.read_text(encoding="utf-8")) == registry


def test_preflight_pack_reports_work_without_staging(tmp_path: Path, capsys) -> None:
    pack_path = tmp_path / "pack.json"
    pack_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "pack_id": "preflight-fixture",
                "material_type": "knowledge",
                "source": {"name": "Fixture", "homepage": "", "license": "CC0"},
                "entries": [
                    {
                        "title": "Fixture",
                        "terms": {"alias": [], "recognition": []},
                        "tags": [],
                        "summary": "",
                        "content": "Fixture body",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    args = MODULE._build_parser().parse_args(
        [
            "--preflight-pack",
            str(pack_path),
            "--knowledge-root",
            str(tmp_path),
        ]
    )

    result = asyncio.run(MODULE._run(args))
    payload = json.loads(capsys.readouterr().out)

    assert result == 0
    assert payload["projected_chunks"] == 1
    assert not (tmp_path / ".staging").exists()


def test_cancel_job_action_removes_staged_payload(tmp_path: Path, capsys) -> None:
    job_dir = _write_staged_job_fixture(tmp_path, pack_id="cancel-pack")
    job_id = job_dir.name
    (job_dir / "pack.json").write_text("{}", encoding="utf-8")
    args = MODULE._build_parser().parse_args(
        [
            "--cancel-job",
            job_id,
            "--knowledge-root",
            str(tmp_path),
        ]
    )

    result = asyncio.run(MODULE._run(args))
    payload = json.loads(capsys.readouterr().out)

    assert result == 0
    assert payload["ok"] is True
    assert not (job_dir / "pack.json").exists()


def test_discard_job_action_only_removes_quarantined_job(
    tmp_path: Path,
    capsys,
) -> None:
    job_id = "degraded-pack-0123456789ab"
    job_dir = tmp_path / ".staging" / job_id
    job_dir.mkdir(parents=True)
    (job_dir / "state.json").write_text("[]", encoding="utf-8")
    args = MODULE._build_parser().parse_args(
        [
            "--discard-job",
            job_id,
            "--knowledge-root",
            str(tmp_path),
        ]
    )

    result = asyncio.run(MODULE._run(args))
    payload = json.loads(capsys.readouterr().out)

    assert result == 0
    assert payload["ok"] is True
    assert not job_dir.exists()


def test_discard_job_action_removes_crashed_creation_directory(
    tmp_path: Path,
    capsys,
) -> None:
    job_id = f".creating-{'a' * 32}"
    job_dir = tmp_path / ".staging" / job_id
    job_dir.mkdir(parents=True)
    (job_dir / "partial").write_bytes(b"partial")
    args = MODULE._build_parser().parse_args(
        [
            "--discard-job",
            job_id,
            "--knowledge-root",
            str(tmp_path),
        ]
    )

    result = asyncio.run(MODULE._run(args))
    payload = json.loads(capsys.readouterr().out)

    assert result == 0
    assert payload["ok"] is True
    assert not job_dir.exists()


def test_status_splits_failed_retry_boundaries(tmp_path: Path) -> None:
    database = tmp_path / "knowledge.db"
    _write_v6_chunks(database)
    now = int(MODULE.time.time())
    _insert_failed_chunk(
        database,
        chunk_id="retry-now",
        attempts=7,
        next_retry_at=now,
    )
    _insert_failed_chunk(
        database,
        chunk_id="waiting",
        attempts=7,
        next_retry_at=now + 60,
    )
    _insert_failed_chunk(
        database,
        chunk_id="exhausted",
        attempts=8,
        next_retry_at=now,
    )

    status = MODULE.inspect_database(database)

    assert status["chunks_failed"] == 3
    assert status["chunks_failed_retryable_now"] == 1
    assert status["chunks_failed_waiting"] == 1
    assert status["chunks_failed_exhausted"] == 1


def test_status_treats_v6_chunks_as_local_policy(tmp_path: Path) -> None:
    database = tmp_path / "knowledge.db"
    _write_v6_chunks(database)
    _insert_failed_chunk(
        database,
        chunk_id="legacy-local",
        attempts=1,
        next_retry_at=0,
    )

    status = MODULE.inspect_database(database)

    assert status["chunks_local"] == 1
    assert status["chunks_prebuilt_only"] == 0
    assert status["chunks_local_failed_retryable_now"] == 1


def test_maintenance_counts_only_local_policy_work(tmp_path: Path) -> None:
    database = tmp_path / "knowledge.db"
    _write_v6_chunks(database)
    with sqlite3.connect(database) as connection:
        connection.execute(
            "ALTER TABLE knowledge_chunks ADD COLUMN embedding_policy TEXT "
            "NOT NULL DEFAULT 'local'"
        )
        connection.execute("UPDATE metadata SET value='7' WHERE key='schema_version'")
        connection.execute(
            "INSERT INTO knowledge_chunks("
            "chunk_id, entry_rowid, chunk_index, chunk_text, content_hash, "
            "embedding_status, embedding_policy) "
            "VALUES ('prebuilt', 1, 0, 'body', 'hash', 'pending', 'prebuilt_only')"
        )

    status = MODULE.inspect_database(database)

    assert status["chunks_pending"] == 1
    assert status["chunks_local_pending"] == 0
    assert status["chunks_prebuilt_only"] == 1
    assert MODULE._eligible_chunk_count(status) == 0
    assert MODULE._completion_state(status) == "complete"


@pytest.mark.parametrize(
    ("overrides", "last_batch_state", "expected"),
    (
        ({}, "ready", "complete"),
        ({"chunks_failed": 1, "chunks_failed_waiting": 1}, "ready", "retry_scheduled"),
        (
            {"chunks_failed": 1, "chunks_failed_exhausted": 1},
            "ready",
            "failed_exhausted",
        ),
        ({"chunks_pending": 1}, "not_ready", "embedding_unavailable"),
        ({"chunks_stale": 1}, "ready", "processing_incomplete"),
        ({"entries_missing_chunks": 1}, "ready", "backfill_incomplete"),
        (
            {"entries_missing_chunks": 1, "chunks_failed": 1},
            "ready",
            "backfill_incomplete",
        ),
    ),
)
def test_completion_state_is_explicit(
    overrides: dict[str, int],
    last_batch_state: str,
    expected: str,
) -> None:
    status = {
        "chunks_pending": 0,
        "chunks_stale": 0,
        "chunks_failed": 0,
        "chunks_failed_retryable_now": 0,
        "chunks_failed_waiting": 0,
        "chunks_failed_exhausted": 0,
        "entries_missing_chunks": 0,
        **overrides,
    }

    assert (
        MODULE._completion_state(status, last_batch_state=last_batch_state) == expected
    )


@pytest.mark.asyncio
async def test_work_budget_is_split_into_four_item_microbatches(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import knowledge.vector_index as vector_index

    calls: list[int] = []

    async def _fake_index_embedding_batch(
        store: object,
        *,
        batch_size: int,
        load_model: bool,
    ) -> SimpleNamespace:
        del store
        assert load_model is True
        calls.append(batch_size)
        return SimpleNamespace(
            selected=batch_size,
            stored=batch_size,
            failed=0,
            stale_writebacks=0,
            state="ready",
        )

    monkeypatch.setattr(
        vector_index, "index_embedding_batch", _fake_index_embedding_batch
    )

    result = await MODULE._run_embedding_work_round(object(), work_budget=9)

    assert calls == [4, 4, 1]
    assert result == (9, 9, 0, 0, "ready")


@pytest.mark.asyncio
async def test_rebuild_reports_capacity_limited_without_embedding(monkeypatch, tmp_path):
    import knowledge.pack_jobs as pack_jobs
    import knowledge.store as store_module
    import utils.local_embedding_runtime as embedding_runtime

    database = tmp_path / "knowledge.db"
    database.write_bytes(b"fixture")
    status = {
        "chunks_ready": 20_000,
        "chunks_pending": 1,
        "chunks_stale": 0,
        "chunks_failed": 0,
        "chunks_failed_retryable_now": 0,
        "chunks_failed_waiting": 0,
        "chunks_failed_exhausted": 0,
        "chunks_local_pending": 1,
        "chunks_local_stale": 0,
        "chunks_local_failed": 0,
        "chunks_local_failed_retryable_now": 0,
        "chunks_local_failed_waiting": 0,
        "chunks_local_failed_exhausted": 0,
    }

    class FakeStore:
        def __init__(self, _path):
            self.database_path = database

        def reset_chunk_index(self, *, full):
            assert full is True
            return 0

    async def unexpected_embedding(*_args, **_kwargs):
        raise AssertionError("embedding must not run at the ready-vector cap")

    monkeypatch.setattr(pack_jobs, "MAX_READY_VECTOR_CHUNKS", 20_000)
    monkeypatch.setattr(store_module, "KnowledgeStore", FakeStore)
    monkeypatch.setattr(MODULE, "inspect_database", lambda _path: dict(status))
    monkeypatch.setattr(MODULE, "_backfill_all", lambda *_args, **_kwargs: 0)
    monkeypatch.setattr(MODULE, "_run_embedding_work_round", unexpected_embedding)
    monkeypatch.setattr(
        embedding_runtime,
        "get_local_embedding_status",
        lambda: SimpleNamespace(state="ready", model_id="fixture", dimensions=2),
    )

    result, complete = await MODULE.rebuild_target(
        MODULE.KnowledgeTarget(database),
        full=True,
        batch_size=4,
    )

    assert result["result_state"] == "capacity_limited"
    assert result["eligible_chunks_remaining"] == 1
    assert result["vector_budget_remaining"] == 0
    assert complete is False


@pytest.mark.asyncio
async def test_rebuild_stops_before_mutation_when_initial_inspection_fails(
    monkeypatch,
    tmp_path,
):
    import knowledge.store as store_module

    database = tmp_path / "knowledge.db"
    database.write_bytes(b"fixture")

    class UnexpectedStore:
        def __init__(self, _path):
            raise AssertionError("store must not open after an inspection failure")

    monkeypatch.setattr(store_module, "KnowledgeStore", UnexpectedStore)
    monkeypatch.setattr(
        MODULE,
        "inspect_database",
        lambda _path: {
            "database": str(database),
            "database_exists": True,
            "error_type": "OperationalError",
        },
    )

    result, complete = await MODULE.rebuild_target(
        MODULE.KnowledgeTarget(database),
        full=False,
        batch_size=4,
    )

    assert result["result_state"] == "inspection_unavailable"
    assert result["error_type"] == "OperationalError"
    assert complete is False


@pytest.mark.asyncio
async def test_rebuild_does_not_report_complete_when_final_inspection_fails(
    monkeypatch,
    tmp_path,
):
    import knowledge.store as store_module

    database = tmp_path / "knowledge.db"
    database.write_bytes(b"fixture")
    status = {
        "chunks_ready": 1,
        "chunks_pending": 0,
        "chunks_stale": 0,
        "chunks_failed": 0,
        "chunks_failed_retryable_now": 0,
        "chunks_failed_waiting": 0,
        "chunks_failed_exhausted": 0,
        "chunks_local_pending": 0,
        "chunks_local_stale": 0,
        "chunks_local_failed": 0,
        "chunks_local_failed_retryable_now": 0,
        "chunks_local_failed_waiting": 0,
        "chunks_local_failed_exhausted": 0,
    }
    inspections = iter((dict(status), dict(status), {**status, "error_type": "DatabaseError"}))

    class FakeStore:
        def __init__(self, _path):
            self.database_path = database

        def reset_chunk_index(self, *, full):
            assert full is False
            return 0

    monkeypatch.setattr(store_module, "KnowledgeStore", FakeStore)
    monkeypatch.setattr(MODULE, "inspect_database", lambda _path: next(inspections))
    monkeypatch.setattr(MODULE, "_backfill_all", lambda *_args, **_kwargs: 0)

    result, complete = await MODULE.rebuild_target(
        MODULE.KnowledgeTarget(database),
        full=False,
        batch_size=4,
    )

    assert result["result_state"] == "inspection_unavailable"
    assert result["error_type"] == "DatabaseError"
    assert complete is False
