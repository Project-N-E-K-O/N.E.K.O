"""Protect shared import budgets, durable bytes and index rollback on IO failure.

These regressions retain the original isolated service, fault injection and
cleanup contracts. Shared factories live outside collected test modules.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
import sqlite3
import threading

import pytest

from services.knowledge.models import canonical_pack_bytes, parse_pack
from services.knowledge.registry import load_registry
import services.knowledge.service as service_module
from tests.knowledge_fixtures import (
    _entries,
    _import,
    _pack,
    _raw,
    _started,
    _updated_pack,
    _wait_for_last_job,
)

pytestmark = pytest.mark.unit


async def test_capacity_is_rechecked_when_racing_imports_commit(tmp_path, monkeypatch):
    service = await _started(tmp_path)
    try:
        monkeypatch.setattr(service_module, "MAX_TOTAL_ENTRIES", 3)
        async with service._write_lock:  # both pass admission before either commits
            first = await service.import_pack(_raw(_pack("pack-one")))
            second = await service.import_pack(_raw(_pack("pack-two")))
        assert first["ok"] is True and second["ok"] is True
        for _ in range(200):
            states = {job["pack_id"]: job for job in service.list_jobs()}
            if all(job["state"] not in ("queued", "building") for job in states.values()):
                break
            await asyncio.sleep(0.01)
        assert states["pack-one"]["state"] == "active"
        assert states["pack-two"]["state"] == "failed"
        assert states["pack-two"]["reason"] == "capacity_entries"
        assert set(load_registry(tmp_path).packs) == {"pack-one"}
        assert len(list((tmp_path / "packs").iterdir())) == 1
    finally:
        await service.stop()


async def test_staged_imports_count_toward_byte_capacity(tmp_path, monkeypatch):
    service = await _started(tmp_path)
    try:
        one = _raw(_pack("pack-one"))
        monkeypatch.setattr(service_module, "MAX_TOTAL_PACK_BYTES", int(len(one) * 1.5))
        async with service._write_lock:  # pack-one stays staged, not installed
            assert (await service.import_pack(one))["ok"] is True
            second = await service.import_pack(_raw(_pack("pack-two")))
        assert second == {"ok": False, "reason": "capacity_bytes"}
        assert len(list((tmp_path / ".staging").iterdir())) <= 1
    finally:
        await service.stop()


async def test_pending_imports_are_bounded_and_same_pack_is_serialized(tmp_path):
    service = await _started(tmp_path)
    try:
        async with service._write_lock:
            results = await asyncio.gather(
                service.import_pack(_raw(_pack("same-pack"))),
                service.import_pack(_raw(_pack("same-pack", entries=_entries("x", 2)))),
            )
            assert sorted(r["ok"] for r in results) == [False, True]
            assert [r["reason"] for r in results if not r["ok"]] == ["job_in_progress"]
            for name in ("p-two", "p-three"):
                assert (await service.import_pack(_raw(_pack(name))))["ok"] is True
            over = await service.import_pack(_raw(_pack("p-four")))
            assert over == {"ok": False, "reason": "knowledge_busy"}
    finally:
        await service.stop()


async def test_failed_cleanup_of_old_file_does_not_fail_a_committed_update(tmp_path, monkeypatch):
    service = await _started(tmp_path)
    try:
        await _import(service, _pack())
        (old_file,) = (tmp_path / "packs").iterdir()
        real_unlink = Path.unlink

        def unlink(self, *args, **kwargs):
            if self == old_file:
                raise PermissionError("locked")
            return real_unlink(self, *args, **kwargs)

        monkeypatch.setattr(Path, "unlink", unlink)
        updated = _pack()
        updated["entries"][0]["summary"] = "NEW SUMMARY"
        job = await _import(service, updated)
        assert job["state"] == "active"
        assert "NEW SUMMARY" in (await service.query(query="绝绝子"))["context"]
    finally:
        await service.stop()


async def test_failed_registry_write_restores_the_previous_index(tmp_path, monkeypatch):
    service = await _started(tmp_path)
    try:
        await _import(service, _pack())
        updated = _pack()
        updated["entries"][0]["summary"] = "UPDATED SUMMARY"

        def fail(*_args, **_kwargs):
            raise OSError("disk full")

        monkeypatch.setattr(service_module, "save_registry", fail)
        job = await _import(service, updated)
        assert job["state"] == "failed"
        hit = await service.query(query="绝绝子")
        assert "UPDATED SUMMARY" not in hit["context"]
        assert "表示极好的网络用语" in hit["context"]
    finally:
        await service.stop()


async def test_failed_registry_write_reverts_a_disabled_flag(tmp_path, monkeypatch):
    service = await _started(tmp_path)
    try:
        await _import(service, _pack())

        def fail(*_args, **_kwargs):
            raise OSError("disk full")

        monkeypatch.setattr(service_module, "save_registry", fail)
        with pytest.raises(OSError):
            await service.set_entry_disabled("demo-memes", "绝绝子", True)
        assert (await service.query(query="绝绝子"))["result"] == "matched"
    finally:
        await service.stop()


async def test_removal_is_published_even_if_index_cleanup_fails(tmp_path, monkeypatch):
    service = await _started(tmp_path)
    try:
        await _import(service, _pack())

        def fail(*_args, **_kwargs):
            raise sqlite3.OperationalError("database is locked")

        monkeypatch.setattr(service._store, "delete_pack", fail)
        await service.remove_pack("demo-memes")
        assert service.availability()["tool_available"] is False
        assert (await service.query(query="绝绝子"))["result"] == "miss"
    finally:
        await service.stop()


async def test_unregistered_pack_files_are_kept_but_do_not_count_toward_byte_capacity(tmp_path, monkeypatch):
    service = await _started(tmp_path)
    try:
        one = _raw(_pack("pack-one"))
        monkeypatch.setattr(service_module, "MAX_TOTAL_PACK_BYTES", int(len(one) * 1.5))
        # An old revision whose move failed, and an interrupted atomic write,
        # are retained across restarts without consuming the active budget.
        leftovers = [tmp_path / "packs" / "pack-old.0000000000000000.json", tmp_path / "packs" / "pack-old.json.tmp"]
        for path in leftovers:
            path.write_bytes(b"x" * len(one))
        await service.stop()
        service = await _started(tmp_path)
        result = await service.import_pack(one)
        assert result["ok"] is True
        assert (await _wait_for_last_job(service))[0] == "active"
        assert all(path.read_bytes() == b"x" * len(one) for path in leftovers)
        # Registered current revisions still count.
        second = await service.import_pack(_raw(_pack("pack-two")))
        assert second == {"ok": False, "reason": "capacity_bytes"}
    finally:
        await service.stop()


async def test_undeleted_staged_files_count_toward_byte_capacity(tmp_path, monkeypatch):
    service = await _started(tmp_path)
    try:
        one = _raw(_pack("pack-one"))
        monkeypatch.setattr(service_module, "MAX_TOTAL_PACK_BYTES", int(len(one) * 1.5))
        # A finished job whose staged file could not be deleted.
        (tmp_path / ".staging").mkdir(exist_ok=True)
        (tmp_path / ".staging" / "deadbeef.json").write_bytes(b"x" * len(one))
        result = await service.import_pack(one)
        assert result == {"ok": False, "reason": "capacity_bytes"}
    finally:
        await service.stop()


async def test_the_staged_file_is_read_with_a_bound(tmp_path, monkeypatch):
    service = await _started(tmp_path)
    try:
        read = []
        real = service_module._read_bounded

        def recording(path):
            read.append(Path(path).parent.name)
            return real(path)

        monkeypatch.setattr(service_module, "_read_bounded", recording)
        await _import(service, _pack())
        assert service_module.STAGING_DIR in read
    finally:
        await service.stop()


async def test_a_failed_update_does_not_restore_an_altered_old_file(tmp_path, monkeypatch):
    service = await _started(tmp_path)
    try:
        await _import(service, _pack())
        (raw_file,) = (tmp_path / "packs").iterdir()
        # Valid pack JSON, but not the version the registry describes.
        altered = _pack(entries=[{"title": "Altered", "content": "zanzibar smuggled text"}])
        raw_file.write_bytes(canonical_pack_bytes(parse_pack(altered)))

        def fail(*_args, **_kwargs):
            raise OSError("disk full")

        monkeypatch.setattr(service_module, "save_registry", fail)
        await service.import_pack(_raw(_updated_pack()))
        assert (await _wait_for_last_job(service))[0] == "failed"
        assert (await service.query(query="zanzibar"))["result"] == "miss"
    finally:
        await service.stop()


async def test_parsing_is_bounded_before_it_starts(tmp_path, monkeypatch):
    import threading
    import time

    service = await _started(tmp_path)
    try:
        lock = threading.Lock()
        running = {"now": 0, "max": 0, "calls": 0}
        real_prepare = service._prepare_import

        def slow_prepare(raw):
            with lock:
                running["calls"] += 1
                running["now"] += 1
                running["max"] = max(running["max"], running["now"])
            time.sleep(0.2)
            with lock:
                running["now"] -= 1
            return real_prepare(raw)

        monkeypatch.setattr(service, "_prepare_import", slow_prepare)
        async with service._write_lock:  # nothing gets installed meanwhile
            results = await asyncio.gather(
                *(service.import_pack(_raw(_pack(f"pack-{i}"))) for i in range(8))
            )
        limit = service_module.MAX_PENDING_IMPORTS
        assert running["max"] <= limit
        assert running["calls"] <= limit  # the rest were refused before parsing
        assert sum(1 for r in results if r.get("reason") == "knowledge_busy") >= 8 - limit
    finally:
        await service.stop()


async def test_a_swapped_staging_file_is_not_committed(tmp_path):
    service = await _started(tmp_path)
    try:
        async with service._write_lock:  # the job waits in the queue
            job = await service.import_pack(_raw(_pack()))
            other = canonical_pack_bytes(parse_pack(_pack("other-pack")))
            service._staging_path(job["job_id"]).write_bytes(other)
        states = {}
        for _ in range(200):
            states = {item["job_id"]: item for item in service.list_jobs()}
            if states[job["job_id"]]["state"] not in ("queued", "building"):
                break
            await asyncio.sleep(0.01)
        assert states[job["job_id"]]["state"] == "failed"
        assert states[job["job_id"]]["reason"] == "knowledge_error"
        assert load_registry(tmp_path).packs == {}
    finally:
        await service.stop()


async def test_the_parse_reservation_covers_the_unchanged_file_check(tmp_path, monkeypatch):
    import time

    service = await _started(tmp_path)
    try:
        await _import(service, _pack())
        seen = []
        real_intact = service._raw_file_intact

        def slow_intact(record):
            seen.append(service._parsing)
            time.sleep(0.1)
            return real_intact(record)

        monkeypatch.setattr(service, "_raw_file_intact", slow_intact)
        result = await service.import_pack(_raw(_pack()))
        assert result.get("unchanged") is True
        assert seen == [1]
        assert service._parsing == 0
    finally:
        await service.stop()


async def test_leftover_write_temporaries_do_not_count_toward_byte_capacity(tmp_path, monkeypatch):
    # Interrupted atomic writes are swept by later writes to the same
    # directory; until then they are not current revisions.
    service = await _started(tmp_path)
    try:
        one = _raw(_pack("pack-one"))
        monkeypatch.setattr(service_module, "MAX_TOTAL_PACK_BYTES", int(len(one) * 1.5))
        (tmp_path / "packs" / ".neko-deadbeef.tmp").write_bytes(b"x" * len(one))
        result = await service.import_pack(one)
        assert result["ok"] is True
        assert (await _wait_for_last_job(service))[0] == "active"
    finally:
        await service.stop()


async def test_concurrent_admissions_count_each_staged_file_once(tmp_path, monkeypatch):
    import threading

    service = await _started(tmp_path)
    try:
        one = _raw(_pack("pack-one"))
        two = _raw(_pack("pack-two"))
        sizes = [len(canonical_pack_bytes(parse_pack(json.loads(raw)))) for raw in (one, two)]
        monkeypatch.setattr(service_module, "MAX_TOTAL_PACK_BYTES", sum(sizes) + 16)  # both fit
        real_write = service_module.atomic_write_bytes
        on_disk = threading.Event()
        release = threading.Event()

        def slow_write(path, data):
            real_write(path, data)
            if not on_disk.is_set():
                on_disk.set()
                release.wait(5)  # the file is on disk, its job not yet known

        monkeypatch.setattr(service_module, "atomic_write_bytes", slow_write)
        async with service._write_lock:  # nothing gets installed meanwhile
            first = asyncio.create_task(service.import_pack(one))
            assert await asyncio.to_thread(on_disk.wait, 5)
            second = asyncio.create_task(service.import_pack(two))
            await asyncio.sleep(0.2)  # the second admission gets as far as it can
            release.set()
            results = await asyncio.wait_for(asyncio.gather(first, second), 10)
        assert [result["ok"] for result in results] == [True, True], results
    finally:
        release.set()
        await service.stop()


async def test_an_installing_jobs_pack_file_is_counted_once(tmp_path, monkeypatch):
    import threading

    service = await _started(tmp_path)
    indexing = threading.Event()
    release = threading.Event()
    try:
        one = _raw(_pack("pack-one"))
        two = _raw(_pack("pack-two"))
        sizes = [len(canonical_pack_bytes(parse_pack(json.loads(raw)))) for raw in (one, two)]
        monkeypatch.setattr(service_module, "MAX_TOTAL_PACK_BYTES", sum(sizes) + 16)  # both fit
        real_replace = service._store.replace_pack

        def slow_replace(*args, **kwargs):
            indexing.set()  # the pack file is written, the job still building
            release.wait(5)
            return real_replace(*args, **kwargs)

        monkeypatch.setattr(service._store, "replace_pack", slow_replace)
        first = await service.import_pack(one)
        assert first["ok"] is True
        assert await asyncio.to_thread(indexing.wait, 5)
        assert list((tmp_path / "packs").glob("pack-pack-one.*.json"))
        second = await service.import_pack(two)
        assert second["ok"] is True, second
    finally:
        release.set()
        await service.stop()


async def test_an_active_replacement_does_not_count_the_file_it_replaces(tmp_path, monkeypatch):
    import threading

    service = await _started(tmp_path)
    indexing = threading.Event()
    release = threading.Event()
    try:
        old = _pack("pack-one")
        await _import(service, old)
        new = _pack("pack-one", entries=_entries("n", 2))
        other = _pack("pack-two")
        sizes = [len(canonical_pack_bytes(parse_pack(payload))) for payload in (new, other)]
        # The new version and the other pack fit; adding the old version would not.
        monkeypatch.setattr(service_module, "MAX_TOTAL_PACK_BYTES", sum(sizes) + 16)
        real_replace = service._store.replace_pack

        def slow_replace(*args, **kwargs):
            indexing.set()
            release.wait(5)
            return real_replace(*args, **kwargs)

        monkeypatch.setattr(service._store, "replace_pack", slow_replace)
        replacing = await service.import_pack(_raw(new))
        assert replacing["ok"] is True
        assert await asyncio.to_thread(indexing.wait, 5)
        second = await service.import_pack(_raw(other))
        assert second["ok"] is True, second
    finally:
        release.set()
        await service.stop()
