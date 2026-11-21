"""Reproduce review findings at the existing import and indexing boundaries."""

import asyncio
from contextlib import closing
import hashlib
import json
import os
import sqlite3
import threading
import zipfile

from pathlib import Path

import pytest

from knowledge import archive, service as module
from knowledge.models import KnowledgePackError, pack_sha256
from knowledge.service import KnowledgeService
from tests.unit.test_knowledge_archive_v2 import make_archive, specification
from tests.unit.test_knowledge_subscriptions import _subscription
from tests.unit.test_knowledge_subsystem import FakeEmbedder, _import, _pack
from tests.unit.test_knowledge_unified_core import _finished, _manual_index, _prepare_export_vectors


pytestmark = pytest.mark.unit


@pytest.mark.parametrize("state,expected", [("current", 2), ("changed", 0), ("unavailable", 0)])
async def test_pack_list_readiness_uses_the_current_resource_identity(tmp_path, state, expected):
    service = KnowledgeService(tmp_path)
    await service.start()
    await _manual_index(service)
    try:
        await _import(service, _pack())
        _prepare_export_vectors(service, "demo-memes")
        original = service.embedder.spec()

        class Provider:
            def state(self):
                return "ready"

            def model_id(self):
                return original["model_id"]

            def spec(self):
                if state == "unavailable":
                    return None
                if state == "changed":
                    return {**original, "weights_sha256": {key: "b" * 64 for key in original["weights_sha256"]}}
                return original

        service.embedder = Provider()
        pack, = await service.list_packs()
        assert pack["chunks_ready"] == expected
        assert (pack["vector_state"] == "complete") == (state == "current")
    finally:
        await service.stop()


async def test_repeat_subscribed_zip_path_reuses_the_queued_job_when_slots_are_full(tmp_path, monkeypatch):
    path = make_archive(tmp_path / "package.zip")
    subscription = _subscription(path.read_bytes(), pack_id="imp.example")
    service = KnowledgeService(tmp_path / "library")
    async def paused_runner():
        await asyncio.Event().wait()

    monkeypatch.setattr(service, "_job_loop", paused_runner)
    await service.start()
    try:
        first = await service.import_pack(path, subscription=subscription)
        assert first["ok"]
        monkeypatch.setattr(module, "MAX_PENDING_IMPORTS", 1)
        assert service.import_busy()
        repeated = await service.import_pack(path, subscription=subscription)
        assert repeated.get("existing") is True, repeated
        assert repeated["job_id"] == first["job_id"]
    finally:
        await service.stop()


async def test_same_text_zip_still_installs_its_archive_identity(tmp_path):
    path = make_archive(tmp_path / "package.zip")
    with zipfile.ZipFile(path) as source:
        pack = json.loads(source.read("pack.json"))
    service = KnowledgeService(tmp_path / "library")
    await service.start()
    try:
        assert (await _import(service, pack))["state"] == "active"
        result = await service.import_pack(path)
        assert not result.get("unchanged"), result
        assert (await _finished(service, result))["state"] == "active"
        assert service._registry.packs[pack["pack_id"]].archive_sha256 == hashlib.sha256(path.read_bytes()).hexdigest()
    finally:
        await service.stop()


async def test_unsubscribe_overrides_a_zip_already_being_validated(tmp_path, monkeypatch):
    path = make_archive(tmp_path / "package.zip")
    subscription = _subscription(path.read_bytes(), pack_id="imp.example")
    entered, release = threading.Event(), threading.Event()
    original = archive.read_archive

    def paused(*args, **kwargs):
        entered.set()
        assert release.wait(5)
        return original(*args, **kwargs)

    service = KnowledgeService(tmp_path / "library")
    await service.start()
    pending = None
    try:
        monkeypatch.setattr(archive, "read_archive", paused)
        pending = asyncio.create_task(service.import_pack(path, subscription=subscription))
        assert await asyncio.to_thread(entered.wait, 2)
        await service.remove_subscription(subscription.pack_id, subscription.identity, frozenset({subscription.release}))
        release.set()
        result = await pending
        assert (await _finished(service, result))["state"] == "cancelled"
        assert not service._registry.packs
    finally:
        release.set()
        if pending is not None:
            await asyncio.gather(pending, return_exceptions=True)
        await service.stop()


@pytest.mark.parametrize("limit", [None, "pack", "total"])
async def test_commit_counts_the_actual_token_plan_and_rechecks_capacity(tmp_path, monkeypatch, limit):
    service = KnowledgeService(tmp_path)
    await service.start()
    locked = False
    try:
        await service._write_lock.acquire()
        locked = True
        result = await service.import_pack(json.dumps(_pack(entries=[{"title": "Example", "content": "abcdef " * 40}])).encode())
        # Tokenizer resources can arrive after admission while the runner waits.
        monkeypatch.setattr(service, "_chunk_fit", lambda: lambda text: len(text) <= 24)
        if limit:
            monkeypatch.setattr(module, "MAX_CHUNKS_PER_PACK" if limit == "pack" else "MAX_TOTAL_CHUNKS", 1)
        service._write_lock.release()
        locked = False
        job = await _finished(service, result)
        if limit:
            assert job["state"] == "failed"
            assert job["reason"] == ("too_many_chunks" if limit == "pack" else "capacity_chunks")
            assert not service._registry.packs
        else:
            assert job["state"] == "active"
            actual = service._store.chunk_stats(None)["demo-memes"]["total"]
            assert actual > result["chunks_total"]
            assert service._registry.packs["demo-memes"].chunks == job["chunks_total"] == actual
    finally:
        if locked:
            service._write_lock.release()
        await service.stop()


async def test_new_revision_repairs_a_pack_whose_old_file_is_missing(tmp_path):
    service = KnowledgeService(tmp_path)
    await service.start()
    try:
        assert (await _import(service, _pack()))["state"] == "active"
        record = service._registry.packs["demo-memes"]
        (tmp_path / "packs" / record.file_name).unlink()
        changed = _pack(entries=[{"title": "Repaired", "content": "The replacement remains searchable."}])
        job = await _import(service, changed)
        assert job["state"] == "active", job
        assert service._registry.packs["demo-memes"].pack_sha256 != record.pack_sha256
    finally:
        await service.stop()


async def test_unverifiable_spec_defers_without_encoding_or_burning_attempts(tmp_path):
    class InvalidSpec(FakeEmbedder):
        def spec(self):
            return None

    service = KnowledgeService(tmp_path)
    await service.start()
    try:
        assert (await _import(service, _pack()))["state"] == "active"
        embedder = InvalidSpec()
        service.embedder = embedder
        assert await service._index_round() == 0
        assert embedder.batches == []
        assert service._store.chunk_stats(embedder.model_id())["demo-memes"]["failed"] == 0
        with closing(sqlite3.connect(service._store.database_path)) as conn:
            assert conn.execute("SELECT MAX(attempts) FROM chunks WHERE pack_id='demo-memes'").fetchone()[0] == 0
    finally:
        await service.stop()


async def test_discarded_job_cannot_be_recreated_by_a_late_flush(tmp_path):
    service = KnowledgeService(tmp_path)
    await service.start()
    try:
        result = await _import(service, _pack())
        job = service._jobs[result["job_id"]]
        assert await service.discard_job(job.job_id)
        await asyncio.gather(*tuple(service._detached))
        await service._persist_job(job)
        assert not (tmp_path / "jobs" / (job.job_id + ".json")).exists()
        assert not service.list_jobs()
    finally:
        await service.stop()


@pytest.mark.parametrize("unlink_fails", [False, True])
async def test_discarding_a_job_deletes_only_its_own_frozen_retry_input(tmp_path, monkeypatch, unlink_fails):
    service = KnowledgeService(tmp_path)
    await service.start()
    try:
        discarded = service._jobs[(await _import(service, _pack()))["job_id"]]
        kept = service._jobs[(await _import(service, _pack("other-pack")))["job_id"]]
        jobs = tmp_path / "jobs"
        assert (jobs / (discarded.job_id + ".pack.json")).exists()
        assert (jobs / (kept.job_id + ".pack.json")).exists()
        if unlink_fails:
            original = Path.unlink

            def refuse(self, *args, **kwargs):
                if self.name.endswith(".pack.json"):
                    raise OSError("synthetic delete failure")
                return original(self, *args, **kwargs)

            monkeypatch.setattr(Path, "unlink", refuse)
        assert await service.discard_job(discarded.job_id) is True
        await asyncio.gather(*tuple(service._detached))
        assert not (jobs / (discarded.job_id + ".json")).exists()
        # A failed delete is logged, never turned into a failed discard.
        assert (jobs / (discarded.job_id + ".pack.json")).exists() is unlink_fails
        assert (jobs / (kept.job_id + ".pack.json")).exists()
        assert [job["job_id"] for job in service.list_jobs()] == [kept.job_id]
    finally:
        await service.stop()


async def test_retry_whose_frozen_input_was_discarded_is_not_retryable(tmp_path):
    from knowledge.service import ImportJob

    service = KnowledgeService(tmp_path)
    await service.start()
    try:
        stamp = "2026-01-01T00:00:00Z"
        job = ImportJob("c" * 32, "demo-memes", "failed", stamp, stamp, staged_sha256="0" * 64)
        service._remember_job(job)
        with pytest.raises(KnowledgePackError) as error:
            await service.retry_job(job.job_id)
        assert error.value.reason == "job_not_retryable"
    finally:
        await service.stop()


async def test_failed_retry_copy_write_does_not_leave_counted_staging(tmp_path, monkeypatch):
    original = module.atomic_write_bytes

    def fail_copy(path, raw):
        if path.name.endswith(".pack.json"):
            raise OSError("synthetic recovery copy failure")
        return original(path, raw)

    service = KnowledgeService(tmp_path)
    await service.start()
    try:
        monkeypatch.setattr(module, "atomic_write_bytes", fail_copy)
        with pytest.raises(OSError, match="recovery copy"):
            await service.import_pack(json.dumps(_pack()).encode())
        assert not list((tmp_path / module.STAGING_DIR).glob("*.json"))
        assert not service.list_jobs()
    finally:
        await service.stop()


async def test_noncanonical_json_subscription_retries_exact_artifact_bytes(tmp_path):
    service = KnowledgeService(tmp_path)
    await service.start()
    locked = False
    try:
        raw = json.dumps(_pack(), ensure_ascii=False, indent=4).encode()
        subscription = _subscription(raw, pack_id="demo-memes")
        await service._write_lock.acquire()
        locked = True
        queued = await service.import_pack(raw, subscription=subscription)
        assert await service.cancel_job(queued["job_id"])
        service._write_lock.release()
        locked = False
        assert (await _finished(service, queued))["state"] == "cancelled"
        retried = await service.retry_job(queued["job_id"])
        assert retried.get("ok") is True, retried
        assert (await _finished(service, retried))["state"] == "active"
        assert service._registry.packs["demo-memes"].subscription.artifact_sha256 == pack_sha256(raw)
    finally:
        if locked:
            service._write_lock.release()
        await service.stop()


@pytest.mark.parametrize("confirmed", [False, True])
async def test_source_consent_is_checked_against_activation_time_enabled_state(tmp_path, monkeypatch, confirmed):
    service = KnowledgeService(tmp_path)
    gate = asyncio.Event()

    async def paused_runner():
        await gate.wait()

    monkeypatch.setattr(service, "_job_loop", paused_runner)
    await service.start()
    try:
        preview = (await service.preview_source("first.txt", b"Original private reference."))["preview"]
        first = await service.commit_source(preview["preview_id"], preview["sha256"])
        await service._locked(lambda: service._commit_job(service._jobs[first["job_id"]]))
        service._finish_job(service._jobs[first["job_id"]], "active")
        record = service._registry.packs[first["pack_id"]]
        assert not record.enabled
        next_preview = (await service.preview_source("next.txt", b"New private reference.", pack_id=record.pack_id))["preview"]
        queued = await service.commit_source(next_preview["preview_id"], next_preview["sha256"], enabled_confirmed=confirmed)
        await service.set_pack_enabled(record.pack_id, True)
        if confirmed:
            await service._locked(lambda: service._commit_job(service._jobs[queued["job_id"]]))
            assert service._registry.packs[record.pack_id].pack_sha256 != record.pack_sha256
        else:
            with pytest.raises(KnowledgePackError) as error:
                await service._locked(lambda: service._commit_job(service._jobs[queued["job_id"]]))
            assert error.value.reason == "source_confirmation_required"
            assert service._registry.packs[record.pack_id].pack_sha256 == record.pack_sha256
        assert service._registry.packs[record.pack_id].enabled
    finally:
        await service.stop()


async def test_cancelled_export_admission_still_finishes_persistence_and_queueing(tmp_path, monkeypatch):
    service = KnowledgeService(tmp_path)
    await service.start()
    entered, release = asyncio.Event(), asyncio.Event()
    request = None
    try:
        await _import(service, _pack())
        _prepare_export_vectors(service, "demo-memes")
        persist = service._persist_job

        async def paused(job):
            if job.kind == "pack_export" and job.state == "queued":
                entered.set()
                await release.wait()
            return await persist(job)

        monkeypatch.setattr(service, "_persist_job", paused)
        request = asyncio.create_task(service.export_pack("demo-memes"))
        await asyncio.wait_for(entered.wait(), 2)
        job = next(job for job in service.list_jobs() if job["kind"] == "pack_export")
        request.cancel()
        # A broken cancellation path must fail and reach finally's release,
        # instead of waiting forever on the deliberately blocked owner.
        done, _pending = await asyncio.wait({request}, timeout=2)
        assert request in done and request.cancelled()
        release.set()
        assert (await _finished(service, job))["state"] == "active"
    finally:
        release.set()
        if request is not None:
            await asyncio.gather(request, return_exceptions=True)
        await service.stop()


async def test_stopping_runner_does_not_overwrite_a_successful_commit_owner(tmp_path, monkeypatch):
    service = KnowledgeService(tmp_path)
    await service.start()
    entered, release = threading.Event(), threading.Event()
    original = module.save_registry
    stopped = None

    def paused(*args):
        entered.set()
        assert release.wait(5)
        return original(*args)

    try:
        monkeypatch.setattr(module, "save_registry", paused)
        result = await service.import_pack(json.dumps(_pack()).encode())
        assert await asyncio.to_thread(entered.wait, 2)
        runner = next(task for task in service._tasks if task.get_name() == "knowledge-import-jobs")
        stopped = asyncio.create_task(service.stop())
        await asyncio.gather(runner, return_exceptions=True)
        release.set()
        await stopped
        job = service._jobs[result["job_id"]]
        assert job.published and job.state == "active" and not job.reason
        record = json.loads((tmp_path / "jobs" / (job.job_id + ".json")).read_text())
        assert record["state"] == "active" and not record["reason"]
    finally:
        release.set()
        if stopped is not None:
            await stopped
        await service.stop()


async def test_failed_replan_flush_is_reconciled_before_encoding_or_capacity(tmp_path, monkeypatch):
    service = KnowledgeService(tmp_path)
    await service.start()
    try:
        await _import(service, _pack(entries=[{"title": "Example", "content": "abcdef " * 40}]))
        old = service._registry.packs["demo-memes"].chunks
        monkeypatch.setattr(service, "_chunk_fit", lambda: lambda text: len(text) <= 24)
        original = module.save_registry

        def fail(*_args):
            raise OSError("synthetic replan flush failure")

        monkeypatch.setattr(module, "save_registry", fail)
        with pytest.raises(OSError, match="replan flush"):
            await service._replan_vector_inputs(["demo-memes"])
        actual = service._store.chunk_stats(None)["demo-memes"]["total"]
        assert actual > old and service._vectors is None
        assert service._pending_chunk_reconcile
        refused = await service.import_pack(json.dumps(_pack("another", entries=[{"title": "Tiny", "content": "Content."}])).encode())
        assert refused == {"ok": False, "reason": "knowledge_busy"}
        monkeypatch.setattr(module, "save_registry", original)
        assert await service._index_round() == 0
        assert not service._pending_chunk_reconcile
        assert service._registry.packs["demo-memes"].chunks == actual
        assert json.loads((tmp_path / "registry.json").read_text())["packs"]["demo-memes"]["chunks"] == actual
    finally:
        await service.stop()


async def test_seed_attempt_retries_when_the_policy_record_changed(tmp_path, monkeypatch):
    service = KnowledgeService(tmp_path / "library")
    await service.start()
    entered, release = threading.Event(), threading.Event()
    pending = None
    try:
        result = await service.import_pack(make_archive(tmp_path / "package.zip"))
        assert (await _finished(service, result))["state"] == "active"
        original = archive.read_archive

        def paused(*args, **kwargs):
            entered.set()
            assert release.wait(5)
            return original(*args, **kwargs)

        monkeypatch.setattr(archive, "read_archive", paused)
        pending = asyncio.create_task(service._seed_retained_packages(["imp.example"], specification()))
        assert await asyncio.to_thread(entered.wait, 2)
        await service.set_pack_enabled("imp.example", False)
        release.set()
        await pending
        assert not service._seeded_artifacts
        await service._seed_retained_packages(["imp.example"], specification())
        assert service._seeded_artifacts
        assert service._store.chunk_stats(specification()["model_id"])["imp.example"]["ready"] == 1
    finally:
        release.set()
        if pending is not None:
            await asyncio.gather(pending, return_exceptions=True)
        await service.stop()


async def test_damaged_retained_archive_is_not_reread_until_the_file_changes(tmp_path, monkeypatch):
    service = KnowledgeService(tmp_path / "library")
    await service.start()
    try:
        result = await service.import_pack(make_archive(tmp_path / "package.zip"))
        assert (await _finished(service, result))["state"] == "active"
        record = service._registry.packs["imp.example"]
        path = service.artifact_path(record.archive_sha256)
        good = path.read_bytes()
        path.write_bytes(b"damaged retained artifact")
        reads = []
        original = archive.read_archive

        def counted(*args, **kwargs):
            reads.append(args[0])
            return original(*args, **kwargs)

        monkeypatch.setattr(archive, "read_archive", counted)
        for _ in range(3):
            await service._seed_retained_packages(["imp.example"], specification())
        assert len(reads) == 1 and not service._seeded_artifacts
        # Repairing the file changes its identity, so seeding is tried again.
        path.write_bytes(good)
        os.utime(path, ns=(path.stat().st_atime_ns, path.stat().st_mtime_ns + 1_000_000_000))
        await service._seed_retained_packages(["imp.example"], specification())
        assert len(reads) == 2 and service._seeded_artifacts
    finally:
        await service.stop()



@pytest.mark.parametrize("broken", [False, True])
async def test_restart_does_not_report_an_unrepaired_broken_pack_as_a_successful_import(tmp_path, broken):
    from knowledge.service import ImportJob

    service = KnowledgeService(tmp_path)
    await service.start()
    try:
        await _import(service, _pack())
        record = service._registry.packs["demo-memes"]
        stamp = "2026-01-01T00:00:00Z"
        job = ImportJob("d" * 32, "demo-memes", "building", stamp, stamp, staged_sha256=record.pack_sha256)
        (tmp_path / "jobs" / (job.job_id + ".json")).write_text(json.dumps(service._job_record(job)))
    finally:
        await service.stop()
    if broken:
        (tmp_path / "packs" / record.file_name).write_bytes(b"{}")
    service = KnowledgeService(tmp_path)
    await service.start()
    try:
        assert ("demo-memes" in service._broken_packs) is broken
        recovered = next(row for row in service.list_jobs() if row["job_id"] == "d" * 32)
        assert recovered["state"] == ("failed" if broken else "active")
        if broken:
            assert recovered["reason"] == "process_interrupted"
    finally:
        await service.stop()


async def test_a_retained_archive_restored_with_its_old_mtime_is_tried_again(tmp_path, monkeypatch):
    """Restoring a same-size ZIP with a preserved mtime (cp -p / rsync -a) still changes its identity."""
    import shutil

    service = KnowledgeService(tmp_path / "library")
    await service.start()
    try:
        result = await service.import_pack(make_archive(tmp_path / "package.zip"))
        assert (await _finished(service, result))["state"] == "active"
        record = service._registry.packs["imp.example"]
        path = service.artifact_path(record.archive_sha256)
        backup = tmp_path / "backup.zip"
        shutil.copy2(path, backup)
        good = path.read_bytes()
        damaged = bytearray(good)
        damaged[len(damaged) // 2] ^= 0xFF
        stamp = path.stat()
        path.write_bytes(bytes(damaged))
        os.utime(path, ns=(stamp.st_atime_ns, stamp.st_mtime_ns))
        reads = []
        original = archive.read_archive

        def counted(*args, **kwargs):
            reads.append(args[0])
            return original(*args, **kwargs)

        monkeypatch.setattr(archive, "read_archive", counted)
        await service._seed_retained_packages(["imp.example"], specification())
        await service._seed_retained_packages(["imp.example"], specification())
        assert len(reads) == 1 and not service._seeded_artifacts
        restored = path.with_name(path.name + ".restore")
        shutil.copy2(backup, restored)  # same size and mtime, written as a new file
        os.replace(restored, path)
        await service._seed_retained_packages(["imp.example"], specification())
        assert len(reads) == 2 and service._seeded_artifacts
    finally:
        await service.stop()
