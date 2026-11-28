"""Protect request arrival order when import and removal overlap or fail.

These regressions retain the original isolated service, fault injection and
cleanup contracts. Shared factories live outside collected test modules.
"""

from __future__ import annotations

import asyncio
import hashlib

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


async def test_removal_supersedes_an_import_still_in_admission(tmp_path, monkeypatch):
    service = await _started(tmp_path)
    try:
        await _import(service, _pack())
        real_write = service_module.atomic_write_bytes

        def slow_write(path, data):
            import time

            time.sleep(0.2)
            real_write(path, data)

        monkeypatch.setattr(service_module, "atomic_write_bytes", slow_write)
        updated = _pack()
        updated["entries"][0]["summary"] = "REINSTALLED"
        importing = asyncio.create_task(service.import_pack(_raw(updated)))
        await asyncio.sleep(0.05)  # admission is writing its staged file
        await service.remove_pack("demo-memes")
        result = await importing
        assert result["state"] == "cancelled"
        await asyncio.sleep(0.2)
        assert "demo-memes" not in load_registry(tmp_path).packs
        assert (await service.query(query="绝绝子"))["result"] == "miss"
    finally:
        await service.stop()


async def test_removal_during_parsing_supersedes_the_import(tmp_path, monkeypatch):
    service = await _started(tmp_path)
    try:
        await _import(service, _pack())
        real_prepare = service._prepare_import

        def slow_prepare(raw):
            import time

            time.sleep(0.2)
            return real_prepare(raw)

        monkeypatch.setattr(service, "_prepare_import", slow_prepare)
        importing = asyncio.create_task(service.import_pack(_raw(_pack(entries=_entries("z", 2)))))
        await asyncio.sleep(0.05)  # the request is still parsing
        await service.remove_pack("demo-memes")
        result = await importing
        assert result["state"] == "cancelled"
        await asyncio.sleep(0.2)
        assert "demo-memes" not in load_registry(tmp_path).packs
    finally:
        await service.stop()


async def test_a_failed_removal_leaves_a_pending_import_alone(tmp_path, monkeypatch):
    monkeypatch.setattr(service_module, "WRITE_LOCK_TIMEOUT_SECONDS", 0.05)
    service = await _started(tmp_path)
    try:
        await _import(service, _pack())
        async with service._write_lock:  # keeps the re-import queued
            result = await service.import_pack(_raw(_updated_pack()))
            assert result["ok"] is True
            with pytest.raises(service_module.KnowledgeUnavailable) as excinfo:
                await service.remove_pack("demo-memes")  # the lock is busy
            assert excinfo.value.reason == "knowledge_busy"
        assert (await _wait_for_last_job(service))[0] == "active"
        assert load_registry(tmp_path).packs["demo-memes"].pack_sha256 == hashlib.sha256(
            canonical_pack_bytes(parse_pack(_updated_pack()))
        ).hexdigest()
    finally:
        await service.stop()


async def test_removing_a_pack_that_is_not_installed_leaves_its_first_import_alone(tmp_path):
    service = await _started(tmp_path)
    try:
        async with service._write_lock:  # keeps the first import queued
            assert (await service.import_pack(_raw(_pack("brand-new"))))["ok"] is True
            with pytest.raises(service_module.KnowledgeUnavailable) as excinfo:
                await service.remove_pack("brand-new")
            assert excinfo.value.reason == "not_found"
        assert await _wait_for_last_job(service) == ["active"]
        assert "brand-new" in load_registry(tmp_path).packs
    finally:
        await service.stop()


async def test_overlapping_removals_where_only_the_later_succeeds(tmp_path, monkeypatch):
    monkeypatch.setattr(service_module, "WRITE_LOCK_TIMEOUT_SECONDS", 0.2)
    service = await _started(tmp_path)
    try:
        await _import(service, _pack())
        async with service._write_lock:
            first = asyncio.create_task(service.remove_pack("demo-memes"))
            await asyncio.sleep(0.1)
            later = asyncio.create_task(service.remove_pack("demo-memes"))
            (outcome,) = await asyncio.gather(first, return_exceptions=True)
            assert isinstance(outcome, service_module.KnowledgeUnavailable)  # timed out first
        removed = await later
        assert removed["pack_id"] == "demo-memes"
        assert "demo-memes" not in load_registry(tmp_path).packs
    finally:
        await service.stop()


async def test_overlapping_removals_that_all_fail_leave_the_import_alone(tmp_path, monkeypatch):
    monkeypatch.setattr(service_module, "WRITE_LOCK_TIMEOUT_SECONDS", 0.1)
    service = await _started(tmp_path)
    try:
        await _import(service, _pack())
        async with service._write_lock:  # keeps the import waiting and both removals busy
            assert (await service.import_pack(_raw(_updated_pack())))["ok"] is True
            first = asyncio.create_task(service.remove_pack("demo-memes"))
            await asyncio.sleep(0.05)
            second = asyncio.create_task(service.remove_pack("demo-memes"))
            outcomes = await asyncio.gather(first, second, return_exceptions=True)
            assert all(
                isinstance(o, service_module.KnowledgeUnavailable) and o.reason == "knowledge_busy"
                for o in outcomes
            )
        assert (await _wait_for_last_job(service))[0] == "active"
        assert "demo-memes" in load_registry(tmp_path).packs
    finally:
        await service.stop()


async def test_a_building_import_yields_to_a_pending_removal_of_its_pack(tmp_path, monkeypatch):
    monkeypatch.setattr(service_module, "WRITE_LOCK_TIMEOUT_SECONDS", 2.0)
    service = await _started(tmp_path)
    try:
        await _import(service, _pack())
        real_replace = service._store.replace_pack
        building = asyncio.Event()
        loop = asyncio.get_running_loop()

        def slow_replace(pack, *, pack_sha256, disabled_keys=(), should_cancel=None, commit_gate=None):
            import time

            loop.call_soon_threadsafe(building.set)
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline:
                if should_cancel is not None and should_cancel():
                    raise InterruptedError("cancelled")
                time.sleep(0.02)
            return real_replace(pack, pack_sha256=pack_sha256, disabled_keys=disabled_keys)

        monkeypatch.setattr(service._store, "replace_pack", slow_replace)
        updated = _pack()
        updated["entries"][0]["summary"] = "NEWER"
        await service.import_pack(_raw(updated))
        await asyncio.wait_for(building.wait(), 2)
        await service.remove_pack("demo-memes")  # would time out if the import held on
        assert "demo-memes" not in load_registry(tmp_path).packs
        assert (await _wait_for_last_job(service))[0] == "cancelled"
    finally:
        await service.stop()


async def test_an_import_that_arrives_after_a_pending_removal_still_lands(tmp_path, monkeypatch):
    monkeypatch.setattr(service_module, "WRITE_LOCK_TIMEOUT_SECONDS", 5.0)
    service = await _started(tmp_path)
    try:
        await _import(service, _pack())
        async with service._write_lock:  # the removal waits for the lock
            removing = asyncio.create_task(service.remove_pack("demo-memes"))
            await asyncio.sleep(0.05)
            # The job runner then waits behind the removal with another pack,
            # so the re-import below is still queued when the removal commits.
            await service.import_pack(_raw(_pack("other-pack")))
            await asyncio.sleep(0.05)
            job = await service.import_pack(_raw(_updated_pack()))  # asked for after the removal
            assert job["state"] == "queued"
        assert (await removing)["pack_id"] == "demo-memes"
        for _ in range(200):
            states = {item["job_id"]: item["state"] for item in service.list_jobs()}
            if states[job["job_id"]] not in ("queued", "building"):
                break
            await asyncio.sleep(0.01)
        assert states[job["job_id"]] == "active"
        record = load_registry(tmp_path).packs["demo-memes"]
        assert record.pack_sha256 == hashlib.sha256(
            canonical_pack_bytes(parse_pack(_updated_pack()))
        ).hexdigest()
    finally:
        await service.stop()


def test_only_later_removals_supersede_an_import(tmp_path):
    service = service_module.KnowledgeService(tmp_path)
    job = service_module.ImportJob(
        job_id="j", pack_id="demo-memes", state="building", created_at="", updated_at="", arrived_at=5
    )
    # (arrival number, releases covered); None: a local removal, every release.
    service._pending_removals["demo-memes"] = frozenset({(3, None)})
    service._removed_at["demo-memes"] = {None: 4}
    assert service._superseded(job) is False
    service._pending_removals["demo-memes"] = frozenset({(3, None), (6, None)})
    assert service._superseded(job) is True
    service._pending_removals.clear()
    service._removed_at["demo-memes"] = {None: 6}
    assert service._superseded(job) is True


async def test_an_unchanged_import_behind_a_pending_removal_lands_after_it(tmp_path, monkeypatch):
    monkeypatch.setattr(service_module, "WRITE_LOCK_TIMEOUT_SECONDS", 5.0)
    service = await _started(tmp_path)
    try:
        await _import(service, _pack())
        async with service._write_lock:  # the removal waits for the lock
            removing = asyncio.create_task(service.remove_pack("demo-memes"))
            await asyncio.sleep(0.05)
            job = await service.import_pack(_raw(_pack()))  # same bytes, asked for later
            assert job.get("unchanged") is not True
        assert (await removing)["pack_id"] == "demo-memes"
        for _ in range(200):
            states = {item["job_id"]: item["state"] for item in service.list_jobs()}
            if states[job["job_id"]] not in ("queued", "building"):
                break
            await asyncio.sleep(0.01)
        assert states[job["job_id"]] == "active"
        assert "demo-memes" in load_registry(tmp_path).packs
    finally:
        await service.stop()


async def test_an_import_that_yielded_to_a_failed_removal_goes_ahead(tmp_path, monkeypatch):
    monkeypatch.setattr(service_module, "WRITE_LOCK_TIMEOUT_SECONDS", 2.0)
    service = await _started(tmp_path)
    try:
        await _import(service, _pack())
        real_replace = service._store.replace_pack
        building = asyncio.Event()
        loop = asyncio.get_running_loop()
        calls = {"replace": 0, "save": 0}

        def slow_first_replace(pack, *, pack_sha256, disabled_keys=(), should_cancel=None, commit_gate=None):
            import time

            calls["replace"] += 1
            if calls["replace"] == 1:  # only the first attempt waits to be told to yield
                loop.call_soon_threadsafe(building.set)
                deadline = time.monotonic() + 5
                while time.monotonic() < deadline:
                    if should_cancel is not None and should_cancel():
                        raise InterruptedError("cancelled")
                    time.sleep(0.02)
            return real_replace(
                pack, pack_sha256=pack_sha256, disabled_keys=disabled_keys,
                should_cancel=should_cancel, commit_gate=commit_gate,
            )

        real_save = service_module.save_registry

        def save_failing_once(root, registry):
            calls["save"] += 1
            if calls["save"] == 1:  # the removal's write
                raise OSError("disk full")
            real_save(root, registry)

        monkeypatch.setattr(service._store, "replace_pack", slow_first_replace)
        monkeypatch.setattr(service_module, "save_registry", save_failing_once)
        job = await service.import_pack(_raw(_updated_pack()))
        await asyncio.wait_for(building.wait(), 2)
        with pytest.raises(OSError):
            await service.remove_pack("demo-memes")
        for _ in range(200):
            states = {item["job_id"]: item["state"] for item in service.list_jobs()}
            if states[job["job_id"]] not in ("queued", "building"):
                break
            await asyncio.sleep(0.01)
        assert states[job["job_id"]] == "active"
        assert calls["replace"] == 2  # it yielded once, then went ahead
        assert load_registry(tmp_path).packs["demo-memes"].pack_sha256 == hashlib.sha256(
            canonical_pack_bytes(parse_pack(_updated_pack()))
        ).hexdigest()
    finally:
        await service.stop()


async def test_no_unchanged_answer_when_a_removal_starts_during_the_file_check(tmp_path, monkeypatch):
    import time

    monkeypatch.setattr(service_module, "WRITE_LOCK_TIMEOUT_SECONDS", 5.0)
    service = await _started(tmp_path)
    try:
        await _import(service, _pack())
        loop = asyncio.get_running_loop()
        tasks = []
        real_intact = service._raw_file_intact

        def intact_while_a_removal_starts(record):
            loop.call_soon_threadsafe(
                lambda: tasks.append(asyncio.ensure_future(service.remove_pack("demo-memes")))
            )
            time.sleep(0.2)  # the removal registers and waits for the lock
            return real_intact(record)

        monkeypatch.setattr(service, "_raw_file_intact", intact_while_a_removal_starts)
        async with service._write_lock:
            result = await service.import_pack(_raw(_pack()))
            assert result.get("unchanged") is not True
        assert (await tasks[0])["pack_id"] == "demo-memes"
        assert (await _wait_for_last_job(service))[0] == "cancelled"  # it came before the removal
        assert "demo-memes" not in load_registry(tmp_path).packs
    finally:
        await service.stop()


async def test_pending_removal_sets_are_immutable(tmp_path, monkeypatch):
    monkeypatch.setattr(service_module, "WRITE_LOCK_TIMEOUT_SECONDS", 5.0)
    service = await _started(tmp_path)
    try:
        await _import(service, _pack())
        async with service._write_lock:
            removing = asyncio.create_task(service.remove_pack("demo-memes"))
            await asyncio.sleep(0.05)
            # Read by the import's cancellation check on a worker thread.
            assert isinstance(service._pending_removals["demo-memes"], frozenset)
        assert (await removing)["pack_id"] == "demo-memes"
        assert "demo-memes" not in service._pending_removals
    finally:
        await service.stop()
