"""Keep refused inputs, terminal vector buffers and per-pack failures isolated."""

import asyncio
import hashlib
import json
from pathlib import Path

import pytest

from knowledge import service as module
from knowledge.models import KnowledgePackError
from knowledge.service import ImportJob, KnowledgeService
from tests.unit.test_knowledge_archive_v2 import make_archive, specification
from tests.unit.test_knowledge_subsystem import FakeEmbedder, _import, _pack
from tests.unit.test_knowledge_unified_core import _finished


pytestmark = pytest.mark.unit


async def _stop_automatic_indexer(service):
    """Let each fixture explicitly schedule the index round it is observing."""
    indexer = next(task for task in service._tasks if task.get_name() == "knowledge-indexer")
    indexer.cancel()
    await asyncio.gather(indexer, return_exceptions=True)


@pytest.mark.parametrize("owner", ["none", "preexisting", "memory_task", "disk_task"])
async def test_refused_archive_only_cleans_its_new_unreferenced_copy(tmp_path, monkeypatch, owner):
    """Admission may refuse after validation without reclaiming another owner's file."""
    source = make_archive(tmp_path / "upload.zip")
    raw = source.read_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    root = tmp_path / "library"
    service = KnowledgeService(root)
    await service.start()
    try:
        artifact = root / "artifacts" / (digest + ".neko-knowledge.zip")
        artifact.parent.mkdir(exist_ok=True)
        unrelated = artifact.parent / ("f" * 64 + ".neko-knowledge.zip")
        unrelated.write_bytes(b"unrelated retained export")
        if owner == "preexisting":
            artifact.write_bytes(raw)
        elif owner == "memory_task":
            reference = ImportJob("1" * 32, "imp.previous", "failed", "", "", archive_sha256=digest)
            service._remember_job(reference)
            await service._persist_job(reference)
        elif owner == "disk_task":
            # Evicted terminal tasks still own their retained retry input.
            reference = ImportJob("2" * 32, "imp.previous", "failed", "", "", archive_sha256=digest)
            (root / "jobs").mkdir(exist_ok=True)
            (root / "jobs" / (reference.job_id + ".json")).write_text(json.dumps(service._job_record(reference)))
        monkeypatch.setattr(module, "MAX_PACKS", 0)
        result = await service.import_pack(source)
        assert result == {"ok": False, "reason": "capacity_packs"}
        assert artifact.exists() == (owner != "none")
        if artifact.exists():
            assert artifact.read_bytes() == raw
        assert source.read_bytes() == raw
        assert unrelated.read_bytes() == b"unrelated retained export"
        assert not service._registry.packs
    finally:
        await service.stop()


async def test_refused_archive_cleanup_failure_does_not_replace_the_refusal(tmp_path, monkeypatch):
    source = make_archive(tmp_path / "upload.zip")
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    service = KnowledgeService(tmp_path / "library")
    await service.start()
    original_unlink = Path.unlink
    artifact = service.root / "artifacts" / (digest + ".neko-knowledge.zip")

    def fail_cleanup(path, *args, **kwargs):
        if path == artifact:
            raise OSError("synthetic cleanup failure")
        return original_unlink(path, *args, **kwargs)

    try:
        monkeypatch.setattr(module, "MAX_PACKS", 0)
        monkeypatch.setattr(Path, "unlink", fail_cleanup)
        assert await service.import_pack(source) == {"ok": False, "reason": "capacity_packs"}
        assert artifact.is_file()
    finally:
        await service.stop()


@pytest.mark.parametrize("outcome", ["active", "failed", "cancelled"])
async def test_terminal_archive_job_releases_its_transient_seed_vectors(tmp_path, monkeypatch, outcome):
    """Every terminal worker path releases RAM while keeping exact archive recovery."""
    class CompatibleArchiveProvider:
        def state(self):
            return "disabled"

        def model_id(self):
            return None

        def spec(self):
            return specification()

    source = make_archive(tmp_path / "upload.zip")
    service = KnowledgeService(tmp_path / "library", embedder=CompatibleArchiveProvider())
    await service.start()
    entered, release = asyncio.Event(), asyncio.Event()
    commit = service._commit_job

    async def paused(job):
        entered.set()
        await release.wait()
        if outcome == "failed":
            raise KnowledgePackError("capacity_chunks")
        await commit(job)

    try:
        monkeypatch.setattr(service, "_commit_job", paused)
        result = await service.import_pack(source)
        assert await asyncio.wait_for(entered.wait(), 2)
        job = service._jobs[result["job_id"]]
        assert job.seed_vectors
        archive_id = job.archive_sha256
        if outcome == "cancelled":
            assert await service.cancel_job(job.job_id)
        release.set()
        finished = await _finished(service, result)
        assert finished["state"] == outcome
        assert job.seed_vectors == []
        assert service.artifact_path(archive_id).read_bytes() == source.read_bytes()
    finally:
        release.set()
        await service.stop()


@pytest.mark.parametrize("changed", ["manifest", "version", "directory"])
async def test_source_capability_cache_hits_and_invalidates_on_local_changes(tmp_path, monkeypatch, changed):
    """Listing avoids reparsing unchanged histories but never trusts stale provenance."""
    service = KnowledgeService(tmp_path)
    await service.start()
    try:
        preview = (await service.preview_source("notes.txt", b"The original complete reference."))["preview"]
        result = await service.commit_source(preview["preview_id"], preview["sha256"])
        assert (await _finished(service, result))["state"] == "active"
        pack_id = result["pack_id"]
        calls = []
        verify = service._sources_managed

        def counted(pack_id):
            calls.append(pack_id)
            return verify(pack_id)

        monkeypatch.setattr(service, "_sources_managed", counted)
        assert (await service.list_packs())[0]["sources_managed"] is True
        assert (await service.list_packs())[0]["sources_managed"] is True
        assert calls == [pack_id]
        folder = tmp_path / "sources" / pack_id
        if changed == "manifest":
            manifest = folder / "manifest.json"
            manifest.write_text(manifest.read_text() + "\n")
        elif changed == "version":
            version, = (folder / "versions").glob("*.json")
            value = json.loads(version.read_text())
            value["content"] = "A different source must not authorize this installed pack."
            version.write_text(json.dumps(value))
        else:
            (folder / "versions" / "pending.tmp").write_bytes(b"new directory entry")
        assert (await service.list_packs())[0]["sources_managed"] is (changed != "version")
        assert calls == [pack_id, pack_id]
        assert (await service.list_packs())[0]["sources_managed"] is (changed != "version")
        assert calls == [pack_id, pack_id]
    finally:
        await service.stop()


async def test_one_overcapacity_replan_does_not_starve_a_normal_pack(tmp_path, monkeypatch):
    """Only rows owned by the oversized pack are deferred for this index round."""
    class TokenProvider(FakeEmbedder):
        def __init__(self):
            super().__init__(model_id=specification()["model_id"])
            self.texts = []

        def spec(self):
            return specification()

        def count_tokens(self, texts):
            return [len(text) * 8 for text in texts]

        async def embed_batch(self, texts):
            self.texts.extend(texts)
            return [[1.0] + [0.0] * 63 for _ in texts]

        embed_documents = embed_batch

    service = KnowledgeService(tmp_path)
    await service.start()
    try:
        await _stop_automatic_indexer(service)
        assert (await _import(service, _pack("imp.oversized", entries=[{"title": "Oversized", "content": "a" * 2500}])))["state"] == "active"
        assert (await _import(service, _pack("imp.normal", entries=[{"title": "Normal", "content": "Short searchable reference."}])))["state"] == "active"
        provider = TokenProvider()
        service.embedder = provider
        monkeypatch.setattr(module, "MAX_CHUNKS_PER_PACK", 3)
        monkeypatch.setattr(module, "INDEX_BATCH_PAUSE_SECONDS", 0.0)
        replanned, mapped = [], []
        replan = service._replan_vector_inputs
        owners = service._store.chunk_pack_ids

        async def observed_replan(pack_ids, **kwargs):
            replanned.append(pack_ids)
            return await replan(pack_ids, **kwargs)

        def observed_owners(chunk_ids):
            result = owners(chunk_ids)
            mapped.append(set(result.values()))
            return result

        monkeypatch.setattr(service, "_replan_vector_inputs", observed_replan)
        monkeypatch.setattr(service._store, "chunk_pack_ids", observed_owners)
        processed = await service._index_round()
        assert replanned == [["imp.oversized"]]
        assert mapped == [{"imp.oversized"}]
        assert len(provider.texts) == 1 and provider.texts[0].startswith("Normal\n")
        stats = service._store.chunk_stats(provider.model_id(), specification=provider.spec())
        assert stats["imp.normal"]["ready"] == 1
        # The refused rows stay keyword-only and finish as failed rather than pending forever.
        assert stats["imp.oversized"]["ready"] == 0
        assert stats["imp.oversized"]["failed"] == stats["imp.oversized"]["total"] > 0
        # The round counts the one encoded row and the rows marked as failed.
        assert processed == 1 + stats["imp.oversized"]["failed"]
        # A later round neither re-reads them nor re-tokenizes the whole pack.
        assert await service._index_round() == 0
        assert replanned == [["imp.oversized"]]
    finally:
        await service.stop()


async def test_refused_replan_is_retried_only_when_its_inputs_change(tmp_path, monkeypatch):
    """Rows still over the limit trigger no whole-pack replan until the pack, spec or capacity changes."""
    class TokenProvider(FakeEmbedder):
        def __init__(self):
            super().__init__(model_id=specification()["model_id"])
            self.counted = 0

        def spec(self):
            return specification()

        def count_tokens(self, texts):
            self.counted += 1
            return [len(text) * 8 for text in texts]

        async def embed_batch(self, texts):
            return [[1.0] + [0.0] * 63 for _ in texts]

        embed_documents = embed_batch

    service = KnowledgeService(tmp_path)
    await service.start()
    try:
        await _stop_automatic_indexer(service)
        assert (await _import(service, _pack("imp.oversized", entries=[{"title": "Oversized", "content": "a" * 2500}])))["state"] == "active"
        provider = TokenProvider()
        service.embedder = provider
        monkeypatch.setattr(module, "MAX_CHUNKS_PER_PACK", 3)
        monkeypatch.setattr(module, "INDEX_BATCH_PAUSE_SECONDS", 0.0)
        replanned = []
        replan = service._replan_vector_inputs

        async def observed_replan(pack_ids, **kwargs):
            replanned.append(pack_ids)
            return await replan(pack_ids, **kwargs)

        monkeypatch.setattr(service, "_replan_vector_inputs", observed_replan)
        await service._index_round()
        assert replanned == [["imp.oversized"]]
        # A new process/model resets attempts; the remembered refusal still
        # skips the whole-pack tokenize and only re-marks the rows.
        service._store.reset_attempts()
        await service._index_round()
        assert replanned == [["imp.oversized"]]
        stats = service._store.chunk_stats(provider.model_id(), specification=provider.spec())
        assert stats["imp.oversized"]["failed"] == stats["imp.oversized"]["total"]
        # A changed key (here: a new pack revision digest) reopens the rows
        # and allows one new attempt, without a restart.
        key, reason = service._refused_replans["imp.oversized"]
        assert reason == "too_many_chunks"
        service._refused_replans["imp.oversized"] = (("other-digest",) + key[1:], reason)
        await service._index_round()
        assert replanned == [["imp.oversized"], ["imp.oversized"]]
    finally:
        await service.stop()


async def test_refused_pack_marks_every_over_long_row_and_embeds_its_short_rows_in_one_round(tmp_path, monkeypatch):
    """More over-long rows than one batch must not take one idle round per batch."""
    class TokenProvider(FakeEmbedder):
        def __init__(self):
            super().__init__(model_id=specification()["model_id"])
            self.texts = []

        def spec(self):
            return specification()

        def count_tokens(self, texts):
            return [len(text) * 8 for text in texts]

        async def embed_batch(self, texts):
            self.texts.extend(texts)
            return [[1.0] + [0.0] * 63 for _ in texts]

        embed_documents = embed_batch

    entries = [{"title": f"Oversized {index}", "content": "a" * 2500} for index in range(3 * module.INDEX_BATCH_SIZE)]
    entries += [{"title": "Short note", "content": "Short searchable reference."}]
    service = KnowledgeService(tmp_path)
    await service.start()
    try:
        await _stop_automatic_indexer(service)
        assert (await _import(service, _pack("imp.mixed", entries=entries)))["state"] == "active"
        provider = TokenProvider()
        service.embedder = provider
        monkeypatch.setattr(module, "MAX_CHUNKS_PER_PACK", service._registry.packs["imp.mixed"].chunks)
        monkeypatch.setattr(module, "INDEX_BATCH_PAUSE_SECONDS", 0.0)
        await service._index_round()
        stats = service._store.chunk_stats(provider.model_id(), specification=provider.spec())["imp.mixed"]
        assert stats["ready"] == 1 and stats["ready"] + stats["failed"] == stats["total"]
        assert stats["failed"] > module.INDEX_BATCH_SIZE
        assert [text.split("\n", 1)[0] for text in provider.texts] == ["Short note"]
    finally:
        await service.stop()


async def test_capacity_refused_replan_reopens_when_other_packs_free_capacity(tmp_path, monkeypatch):
    """Waiting for total capacity is not a permanent encoding failure."""
    class TokenProvider(FakeEmbedder):
        def __init__(self):
            super().__init__(model_id=specification()["model_id"])

        def spec(self):
            return specification()

        def count_tokens(self, texts):
            return [len(text) * 8 for text in texts]

        async def embed_batch(self, texts):
            return [[1.0] + [0.0] * 63 for _ in texts]

        embed_documents = embed_batch

    service = KnowledgeService(tmp_path)
    await service.start()
    try:
        await _stop_automatic_indexer(service)
        assert (await _import(service, _pack("imp.oversized", entries=[{"title": "Oversized", "content": "a" * 2500}])))["state"] == "active"
        assert (await _import(service, _pack("imp.other", entries=[{"title": "Other", "content": "Short reference."}])))["state"] == "active"
        provider = TokenProvider()
        service.embedder = provider
        registry = service._registry.packs
        planned = module.count_pack_chunks(module.decode_pack_bytes(
            (tmp_path / "packs" / registry["imp.oversized"].file_name).read_bytes()), fit=service._chunk_fit())
        # Room for the replanned pack only once the other pack is gone.
        monkeypatch.setattr(module, "MAX_TOTAL_CHUNKS", planned)
        monkeypatch.setattr(module, "INDEX_BATCH_PAUSE_SECONDS", 0.0)
        service._vector_pack_ids = lambda: ["imp.oversized"]
        await service._index_round()
        assert service._refused_replans["imp.oversized"][1] == "capacity_chunks"
        stats = service._store.chunk_stats(provider.model_id(), specification=provider.spec())["imp.oversized"]
        assert stats["failed"] == stats["total"] > 0
        await service.remove_pack("imp.other")
        await service._index_round()
        assert "imp.oversized" not in service._refused_replans
        stats = service._store.chunk_stats(provider.model_id(), specification=provider.spec())["imp.oversized"]
        assert stats["failed"] == 0 and stats["total"] == planned
    finally:
        await service.stop()


async def test_exhausting_attempts_ignores_rows_replaced_since_they_were_read(tmp_path):
    service = KnowledgeService(tmp_path)
    await service.start()
    try:
        await _stop_automatic_indexer(service)
        assert (await _import(service, _pack("imp.one", entries=[{"title": "One", "content": "First reference."}])))["state"] == "active"
        store = service._store
        (chunk_id, text_hash, _text), = store.pending_chunks(model_id="m", pack_ids=["imp.one"], limit=10)
        assert store.exhaust_attempts([(chunk_id, "0" * 64, "imp.one")]) == 0
        assert store.exhaust_attempts([(chunk_id, text_hash, "imp.other")]) == 0
        assert store.chunk_stats(None)["imp.one"]["failed"] == 0
        assert store.exhaust_attempts([(chunk_id, text_hash, "imp.one")]) == 1
        assert store.chunk_stats(None)["imp.one"]["failed"] == 1
    finally:
        await service.stop()


@pytest.mark.parametrize("over_budget", [False, True])
async def test_rollback_restore_keeps_capacity_counts_consistent(tmp_path, monkeypatch, over_budget):
    """Restoring the old version with a newer tokenizer cannot silently change its counted chunks."""
    service = KnowledgeService(tmp_path)
    await service.start()
    try:
        await _stop_automatic_indexer(service)
        entries = [{"title": "Long", "content": "word " * 400}]
        assert (await _import(service, _pack("imp.old", entries=entries)))["state"] == "active"
        previous = service._registry.packs["imp.old"]
        monkeypatch.setattr(service, "_chunk_fit", lambda: (lambda text: len(text) <= 120))
        if over_budget:
            monkeypatch.setattr(module, "MAX_CHUNKS_PER_PACK", previous.chunks)
        assert not service._pending_chunk_reconcile
        await asyncio.to_thread(service._restore_index_blocking, "imp.old", previous)
        actual = service._store.chunk_stats(None)["imp.old"]["total"]
        if over_budget:
            # Kept the admitted layout: nothing to reconcile.
            assert actual == previous.chunks and not service._pending_chunk_reconcile
        else:
            # A different layout is flagged so admission and encoding repair the count first.
            assert actual != previous.chunks and service._pending_chunk_reconcile
            await service._index_round()
            assert service._registry.packs["imp.old"].chunks == actual
    finally:
        await service.stop()


async def test_rollback_compares_with_the_registry_count_at_restore_time(tmp_path, monkeypatch):
    from dataclasses import replace

    service = KnowledgeService(tmp_path)
    await service.start()
    try:
        await _stop_automatic_indexer(service)
        assert (await _import(service, _pack("imp.old", entries=[{"title": "Old", "content": "Short reference."}])))["state"] == "active"
        previous = service._registry.packs["imp.old"]
        # A queued commit reconciled the registry to another count before this rollback.
        service._registry = service._registry.with_pack(replace(previous, chunks=previous.chunks + 5))
        await asyncio.to_thread(service._restore_index_blocking, "imp.old", previous)
        assert service._pending_chunk_reconcile
        # Conversely, a stale captured count does not flag a registry that already matches.
        service._pending_chunk_reconcile = False
        service._registry = service._registry.with_pack(previous)
        await asyncio.to_thread(service._restore_index_blocking, "imp.old", replace(previous, chunks=previous.chunks + 5))
        assert not service._pending_chunk_reconcile
    finally:
        await service.stop()


async def test_refused_replan_record_survives_a_failed_reset(tmp_path, monkeypatch):
    service = KnowledgeService(tmp_path)
    await service.start()
    try:
        await _stop_automatic_indexer(service)
        assert (await _import(service, _pack("imp.one", entries=[{"title": "One", "content": "Short reference."}])))["state"] == "active"
        service._refused_replans["imp.one"] = (("other-digest", "spec", 0), "too_many_chunks")
        reset = service._store.reset_attempts
        calls = []

        def flaky(pack_id=""):
            calls.append(pack_id)
            if len(calls) == 1:
                raise OSError("synthetic write failure")
            return reset(pack_id)

        monkeypatch.setattr(service._store, "reset_attempts", flaky)
        with pytest.raises(OSError):
            await service._reopen_refused_replans(specification())
        assert "imp.one" in service._refused_replans
        await service._reopen_refused_replans(specification())
        assert "imp.one" not in service._refused_replans and calls == ["imp.one", "imp.one"]
    finally:
        await service.stop()
