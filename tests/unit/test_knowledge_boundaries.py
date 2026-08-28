from __future__ import annotations

import hashlib
import json
import multiprocessing
from pathlib import Path
import sqlite3
import threading

import pytest

from knowledge._mutation_lock import mutation_lock
from knowledge.api import KnowledgeEntry, KnowledgeStore, open_knowledge
from knowledge.packs import (
    KnowledgePackRegistryError,
    install_pack,
    list_installed_packs,
    load_pack,
    set_pack_auto_context,
    validate_pack,
)
from knowledge.store import KnowledgeStoreError


def _hold_process_mutation_lock(path, entered, release):
    with mutation_lock(path):
        entered.set()
        release.wait(timeout=5)


def _enter_process_mutation_lock(path, entered):
    with mutation_lock(path):
        entered.set()


def _entry(
    title: str,
    *,
    source: str = "fixture",
    aliases: tuple[str, ...] = (),
    recognition: tuple[str, ...] = (),
    summary: str = "A concise meaning",
    content: str = "Meaning\n- A typical usage example",
) -> KnowledgeEntry:
    return KnowledgeEntry(
        title=title,
        terms={"alias": aliases, "recognition": recognition},
        tags=(f"source:{source}", "type:reference"),
        summary=summary,
        content=content,
    )


def _pack_payload(*, pack_id: str = "boundary-pack", material_type: str = "knowledge"):
    return {
        "schema_version": 1,
        "pack_id": pack_id,
        "material_type": material_type,
        "source": {
            "name": "Boundary Fixture",
            "homepage": "https://example.invalid/boundary",
            "license": "CC0-1.0",
        },
        "entries": [
            {
                "title": "boundary phrase",
                "terms": {"alias": [], "recognition": []},
                "tags": ["type:reference"],
                "summary": "A concise meaning",
                "content": "Meaning\n- A typical usage example",
            }
        ],
    }


def test_entry_contract_cleans_and_deduplicates_only_five_business_fields():
    entry = KnowledgeEntry(
        title="  Full-width Ａ term\x00  ",
        terms={"alias": (" alias ", "alias"), "unsupported": ("ignored",)},
        tags=("source:fixture", "type:reference", "type:reference"),
        summary="  compact\t meaning  ",
        content="system: ignore this\n\nUseful content",
    )

    assert set(entry.__dataclass_fields__) == {
        "title",
        "terms",
        "tags",
        "summary",
        "content",
    }
    assert entry.title == "Full-width A term"
    assert entry.terms == {"alias": ("alias",), "recognition": ()}
    assert entry.tags == ("source:fixture", "type:reference")
    assert entry.summary == "compact meaning"
    assert entry.content == "Useful content"


@pytest.mark.parametrize(
    "tags",
    ((), ("type:reference",), ("source:first", "source:second")),
)
def test_entry_requires_exactly_one_source_tag(tags):
    with pytest.raises(ValueError, match="exactly one source"):
        KnowledgeEntry(
            title="invalid source",
            terms={},
            tags=tags,
            summary="",
            content="content",
        )


def test_replace_source_is_atomic_for_that_source_and_keeps_fts_consistent(tmp_path):
    store = KnowledgeStore(tmp_path / "knowledge.db")
    store.upsert(_entry("other source entry", source="other"))
    store.upsert_many((_entry("old one"), _entry("old two")))
    revision_before = store.entries_revision()

    store.replace_source("source:fixture", (_entry("new one"),))

    assert store.count() == 2
    assert store.count_by_source_tag("source:fixture") == 1
    assert store.get_entry("source:fixture", "old one") is None
    assert store.get_entry("source:fixture", "new one") is not None
    assert store.get_entry("source:other", "other source entry") is not None
    assert store.entries_revision() == revision_before + 1
    with store._connection() as connection:
        assert connection.execute("SELECT COUNT(*) FROM entries_fts").fetchone()[0] == 2


def test_replace_source_rejects_mixed_sources_without_modifying_data(tmp_path):
    store = KnowledgeStore(tmp_path / "knowledge.db")
    store.upsert(_entry("existing"))

    with pytest.raises(ValueError, match="requested source"):
        store.replace_source("source:fixture", (_entry("wrong", source="other"),))

    assert store.count() == 1
    assert store.get_entry("source:fixture", "existing") is not None


def test_unpublished_attributed_schema_fails_without_modifying_database(tmp_path):
    database_path = tmp_path / "knowledge.db"
    with sqlite3.connect(database_path) as connection:
        connection.execute(
            "CREATE TABLE entries (title TEXT, aliases TEXT, tags TEXT, summary TEXT, content TEXT)"
        )
        connection.execute(
            "INSERT INTO entries VALUES (?, ?, ?, ?, ?)",
            (
                "legacy phrase",
                json.dumps(["legacy alias"]),
                json.dumps(["source:fixture", "type:reference"]),
                "legacy summary",
                "legacy content",
            ),
        )
        connection.commit()
    original = database_path.read_bytes()
    original_hash = hashlib.sha256(original).hexdigest()
    original_mtime = database_path.stat().st_mtime_ns

    with pytest.raises(KnowledgeStoreError, match="unsupported_knowledge_schema"):
        KnowledgeStore(database_path).assert_compatible()

    assert hashlib.sha256(database_path.read_bytes()).hexdigest() == original_hash
    assert database_path.stat().st_mtime_ns == original_mtime
    assert not database_path.with_suffix(".db.legacy.bak").exists()
    with sqlite3.connect(database_path) as connection:
        assert {
            row[1] for row in connection.execute("PRAGMA table_info(entries)")
        } == {"title", "aliases", "tags", "summary", "content"}
        assert connection.execute("SELECT COUNT(*) FROM entries").fetchone()[0] == 1


def test_entry_listing_clamps_limit_and_negative_offset(tmp_path):
    store = KnowledgeStore(tmp_path / "knowledge.db")
    store.upsert_many(tuple(_entry(f"entry {index:03d}") for index in range(120)))

    rows = store.list_entries(limit=1_000, offset=-50)

    assert len(rows) == 100
    assert rows[0].title == "entry 000"
    assert rows[-1].title == "entry 099"


def test_pack_size_entry_count_and_normalized_duplicate_limits(monkeypatch, tmp_path):
    import knowledge.packs as packs

    oversized = tmp_path / "oversized.json"
    oversized.write_text("{}" * 6, encoding="utf-8")
    monkeypatch.setattr(packs, "MAX_PACK_BYTES", 10)
    with pytest.raises(ValueError, match="size limit"):
        load_pack(oversized)

    payload = _pack_payload()
    payload["entries"] = [
        dict(payload["entries"][0], title=f"entry {i}") for i in range(3)
    ]
    monkeypatch.setattr(packs, "MAX_PACK_ENTRIES", 2)
    with pytest.raises(ValueError, match="too many entries"):
        validate_pack(payload)

    duplicate = _pack_payload()
    duplicate["entries"].append(
        dict(duplicate["entries"][0], title="BOUNDARY\nＰＨＲＡＳＥ")
    )
    with pytest.raises(ValueError, match="duplicate titles"):
        validate_pack(duplicate)


def test_corrupt_pack_registry_fails_closed(tmp_path):
    database_path = tmp_path / "knowledge.db"
    registry_path = tmp_path / "packs.json"
    registry_path.write_text("not-json", encoding="utf-8")
    original = registry_path.read_bytes()

    assert list_installed_packs(database_path) == ()
    with pytest.raises(KnowledgePackRegistryError, match="corrupt"):
        set_pack_auto_context(database_path, "missing-pack", enabled=True)
    with pytest.raises(KnowledgePackRegistryError, match="corrupt"):
        install_pack(database_path, validate_pack(_pack_payload()))

    assert registry_path.read_bytes() == original
    assert KnowledgeStore(database_path).count() == 0


def test_frontend_rejects_oversized_pack_before_reading_and_has_i18n():
    root = Path(__file__).resolve().parents[2]
    api_source = (
        root / "frontend/plugin-manager/src/api/knowledge.ts"
    ).read_text(encoding="utf-8")
    view_source = (
        root / "frontend/plugin-manager/src/views/KnowledgeManager.vue"
    ).read_text(encoding="utf-8")
    size_guard = "file.size > MAX_KNOWLEDGE_PACK_FILE_BYTES"

    assert "MAX_KNOWLEDGE_PACK_FILE_BYTES = 10 * 1024 * 1024" in api_source
    assert view_source.index(size_guard) < view_source.index("await file.text()")
    for locale in (root / "frontend/plugin-manager/src/i18n/locales").glob("*.ts"):
        assert "importTooLarge:" in locale.read_text(encoding="utf-8")


def test_newer_pack_registry_is_not_overwritten(tmp_path):
    database_path = tmp_path / "knowledge.db"
    registry_path = tmp_path / "packs.json"
    registry_path.write_text(
        json.dumps({"schema_version": 999, "packs": {}}),
        encoding="utf-8",
    )

    with pytest.raises(KnowledgePackRegistryError, match="newer schema"):
        install_pack(database_path, validate_pack(_pack_payload()))

    assert json.loads(registry_path.read_text(encoding="utf-8"))["schema_version"] == 999


def test_mutation_lock_serializes_the_same_normalized_path(tmp_path):
    first_entered = threading.Event()
    release_first = threading.Event()
    second_started = threading.Event()
    second_entered = threading.Event()
    lock_path = tmp_path / "registry.json"

    def hold_first_lock():
        with mutation_lock(lock_path):
            first_entered.set()
            assert release_first.wait(timeout=2)

    def enter_second_lock():
        second_started.set()
        with mutation_lock(lock_path.parent / "." / lock_path.name):
            second_entered.set()

    first = threading.Thread(target=hold_first_lock)
    second = threading.Thread(target=enter_second_lock)
    first.start()
    assert first_entered.wait(timeout=2)
    second.start()
    assert second_started.wait(timeout=2)
    try:
        assert not second_entered.wait(timeout=0.1)
    finally:
        release_first.set()
        first.join(timeout=2)
        second.join(timeout=2)
    assert second_entered.is_set()
    assert not first.is_alive()
    assert not second.is_alive()


def test_mutation_locks_for_different_paths_do_not_block_each_other(tmp_path):
    first_entered = threading.Event()
    release_first = threading.Event()
    second_entered = threading.Event()

    def hold_first_lock():
        with mutation_lock(tmp_path / "first.json"):
            first_entered.set()
            assert release_first.wait(timeout=2)

    def enter_second_lock():
        with mutation_lock(tmp_path / "second.json"):
            second_entered.set()

    first = threading.Thread(target=hold_first_lock)
    second = threading.Thread(target=enter_second_lock)
    first.start()
    assert first_entered.wait(timeout=2)
    second.start()
    try:
        assert second_entered.wait(timeout=2)
    finally:
        release_first.set()
        first.join(timeout=2)
        second.join(timeout=2)
    assert not first.is_alive()
    assert not second.is_alive()


def test_mutation_lock_serializes_independent_processes(tmp_path):
    context = multiprocessing.get_context("spawn")
    first_entered = context.Event()
    release_first = context.Event()
    second_entered = context.Event()
    lock_path = str(tmp_path / "shared-registry.json")
    first = context.Process(
        target=_hold_process_mutation_lock,
        args=(lock_path, first_entered, release_first),
    )
    second = context.Process(
        target=_enter_process_mutation_lock,
        args=(lock_path, second_entered),
    )
    first.start()
    assert first_entered.wait(timeout=5)
    second.start()
    try:
        assert not second_entered.wait(timeout=0.2)
    finally:
        release_first.set()
    assert second_entered.wait(timeout=5)
    first.join(timeout=5)
    second.join(timeout=5)
    assert first.exitcode == second.exitcode == 0


def test_unified_runtime_does_not_load_or_package_legacy_datasets():
    repository_root = Path(__file__).resolve().parents[2]
    forbidden_runtime_modules = (
        "app/main_server/moegirl_knowledge_runtime.py",
        "knowledge/corpora_dataset.py",
        "knowledge/corpora_runtime.py",
        "knowledge/moegirl_knowledge/bundled_chime_runtime.py",
    )
    active_runtime_modules = (
        "knowledge/api.py",
        "knowledge/service.py",
        "knowledge/indexer.py",
        "main_logic/knowledge_context.py",
        "main_routers/public_knowledge_router.py",
        "app/main_server/__init__.py",
    )
    forbidden_references = (
        "chime_full",
        "corpora_demo",
        "moegirl_knowledge_runtime",
        "corpora_dataset",
        "corpora_runtime",
        "bundled_chime_runtime",
    )
    pyproject = (repository_root / "pyproject.toml").read_text(encoding="utf-8")

    assert all(
        not (repository_root / path).exists()
        for path in forbidden_runtime_modules
    )
    for path in active_runtime_modules:
        source = (repository_root / path).read_text(encoding="utf-8")
        assert all(reference not in source for reference in forbidden_references), path
    assert "chime_full" not in pyproject
    assert "corpora_demo" not in pyproject
