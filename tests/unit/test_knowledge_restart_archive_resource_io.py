"""Cover restart writeback, archive staging and deferred encoder resources."""

import hashlib
import json
import os
from pathlib import Path
import uuid

import pytest

from services.knowledge import service as module
from services.knowledge.models import KnowledgePackError
from services.knowledge.service import KnowledgeService
from tests.knowledge_archive_fixtures import make_archive, specification
from tests.knowledge_fixtures import FakeEmbedder, _import, _manual_index, _pack


pytestmark = pytest.mark.unit


def test_interrupted_job_is_visible_when_startup_writeback_fails(tmp_path, monkeypatch, caplog):
    jobs = tmp_path / "jobs"
    jobs.mkdir()
    job_id = uuid.uuid4().hex
    path = jobs / (job_id + ".json")
    original = {"job_id": job_id, "pack_id": "imp.retry", "state": "queued", "kind": "pack_import",
                "created_at": "2026-11-22T00:00:00Z", "updated_at": "2026-11-22T00:00:00Z"}
    path.write_text(json.dumps(original))
    # Unreadable records remain skippable independently of writeback failures.
    (jobs / (uuid.uuid4().hex + ".json")).write_text("{broken")
    service = KnowledgeService(tmp_path)

    def fail_writeback(target, _data):
        assert target == path
        raise OSError("private filesystem details")

    monkeypatch.setattr(module, "atomic_write_json", fail_writeback)
    with caplog.at_level("WARNING", logger="N.E.K.O.Knowledge"):
        service._load_jobs_blocking()
    assert [(job["job_id"], job["state"], job["reason"]) for job in service.list_jobs()] == [
        (job_id, "failed", "process_interrupted")]
    assert json.loads(path.read_text()) == original
    assert job_id in caplog.text and "private filesystem details" not in caplog.text


@pytest.mark.parametrize("failure", ["copy", "flush", "fsync", "hash", "replace"])
@pytest.mark.parametrize("existing", [False, True])
async def test_failed_archive_retention_cleans_only_its_stage(tmp_path, monkeypatch, failure, existing):
    path = make_archive(tmp_path / "input.zip")
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    service = KnowledgeService(tmp_path / "library")
    await service.start()
    output = service.root / "artifacts" / (digest + ".neko-knowledge.zip")
    stage = output.with_suffix(".tmp")
    output.parent.mkdir(parents=True, exist_ok=True)
    if existing:
        output.write_bytes(b"retained artifact must survive")
    error = OSError("injected retention failure")
    copy = module.shutil.copyfileobj
    fsync, replace, open_file = os.fsync, Path.replace, Path.open
    stage_fd = None

    def fail_copy(source, target, length):
        nonlocal stage_fd
        if Path(target.name) != stage:
            return copy(source, target, length)
        stage_fd = target.fileno()
        if failure == "copy":
            target.write(b"partial copy")
            raise error
        copy(source, target, length)
        if failure == "hash":
            target.seek(0)
            target.write(b"damaged")

    class FailingFlush:
        """Fail the explicit flush while letting the real file close normally."""

        def __init__(self, stream):
            self.stream = stream

        def __enter__(self):
            self.stream.__enter__()
            return self

        def __exit__(self, *args):
            return self.stream.__exit__(*args)

        def __getattr__(self, key):
            return getattr(self.stream, key)

        def flush(self):
            raise error

    def fail_flush(target, *args, **kwargs):
        stream = open_file(target, *args, **kwargs)
        if target == stage and args == ("wb",) and failure == "flush":
            return FailingFlush(stream)
        return stream

    def fail_fsync(fd):
        if failure == "fsync" and fd == stage_fd:
            raise error
        return fsync(fd)

    def fail_replace(target, destination):
        if target == stage and failure == "replace":
            raise error
        return replace(target, destination)

    monkeypatch.setattr(module.shutil, "copyfileobj", fail_copy)
    monkeypatch.setattr(Path, "open", fail_flush)
    monkeypatch.setattr(os, "fsync", fail_fsync)
    monkeypatch.setattr(Path, "replace", fail_replace)
    try:
        if failure == "hash":
            with pytest.raises(KnowledgePackError, match="artifact_hash_mismatch"):
                await service.import_pack(path)
        else:
            with pytest.raises(OSError) as caught:
                await service.import_pack(path)
            assert caught.value is error
        assert not stage.exists()
        assert output.exists() is existing
        if existing:
            assert output.read_bytes() == b"retained artifact must survive"
    finally:
        await service.stop()


async def test_retention_cleanup_failure_keeps_the_original_error(tmp_path, monkeypatch):
    path = make_archive(tmp_path / "input.zip")
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    service = KnowledgeService(tmp_path / "library")
    await service.start()
    output = service.root / "artifacts" / (digest + ".neko-knowledge.zip")
    stage = output.with_suffix(".tmp")
    error = OSError("original copy failure")
    unlink, cleanup_calls = Path.unlink, []

    def fail_copy(_source, target, _length):
        target.write(b"partial")
        raise error

    def fail_cleanup(target, *args, **kwargs):
        if target == stage:
            cleanup_calls.append(target)
            raise PermissionError("temporary cleanup unavailable")
        return unlink(target, *args, **kwargs)

    monkeypatch.setattr(module.shutil, "copyfileobj", fail_copy)
    monkeypatch.setattr(Path, "unlink", fail_cleanup)
    try:
        with pytest.raises(OSError) as caught:
            await service.import_pack(path)
        assert caught.value is error and cleanup_calls == [stage]
        assert not output.exists() and stage.read_bytes() == b"partial"
    finally:
        await service.stop()


async def test_failure_before_stage_creation_keeps_an_existing_stage(tmp_path, monkeypatch):
    from services.knowledge import archive

    path = make_archive(tmp_path / "input.zip")
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    service = KnowledgeService(tmp_path / "library")
    await service.start()
    stage = service.root / "artifacts" / (digest + ".neko-knowledge.tmp")
    stage.parent.mkdir(parents=True, exist_ok=True)
    stage.write_bytes(b"preexisting temporary copy")
    original_read, open_file = archive.read_archive, Path.open
    verified = False
    error = OSError("source no longer readable")

    def checked(*args, **kwargs):
        nonlocal verified
        result = original_read(*args, **kwargs)
        verified = True
        return result

    def fail_source(target, *args, **kwargs):
        if target == path and verified:
            raise error
        return open_file(target, *args, **kwargs)

    monkeypatch.setattr(archive, "read_archive", checked)
    monkeypatch.setattr(Path, "open", fail_source)
    try:
        with pytest.raises(OSError) as caught:
            await service.import_pack(path)
        assert caught.value is error
        assert stage.read_bytes() == b"preexisting temporary copy"
    finally:
        await service.stop()


class _DeferredSpec(FakeEmbedder):
    """Expose a loaded profile before its full resource identity is available."""

    def __init__(self):
        super().__init__(model_id=specification()["model_id"])
        self.current_spec = None

    def spec(self):
        return self.current_spec


@pytest.mark.parametrize("modern", [False, True])
async def test_model_retry_reset_waits_for_modern_spec_and_happens_once(tmp_path, monkeypatch, modern):
    service = KnowledgeService(tmp_path)
    await service.start()
    try:
        await _manual_index(service)
        await _import(service, _pack("imp.failed"))
        rows = service._store.pending_chunks(model_id="previous", pack_ids=["imp.failed"], limit=100)
        service._store.exhaust_attempts([(row_id, text_hash, "imp.failed") for row_id, text_hash, _ in rows])
        failed = service._store.chunk_stats("previous")["imp.failed"]["failed"]
        assert failed == len(rows) > 0
        service._index_model_id = "previous"
        embedder = _DeferredSpec() if modern else FakeEmbedder(model_id="legacy-next")
        service.embedder = embedder
        # Real attempt counters are checked; no inference is needed to test
        # the model transition once a valid spec finally becomes available.
        monkeypatch.setattr(service._store, "pending_chunks", lambda **_kwargs: [])
        reset, resets = service._store.reset_attempts, []

        def track_reset(*args, **kwargs):
            resets.append(True)
            return reset(*args, **kwargs)

        monkeypatch.setattr(service._store, "reset_attempts", track_reset)
        if modern:
            for _ in range(2):
                assert await service._indexer._index_round() == 0
                assert service._index_model_id == "previous"
                assert service._store.chunk_stats("previous")["imp.failed"]["failed"] == failed
            assert resets == []
            embedder.current_spec = specification()
        assert await service._indexer._index_round() == 0
        assert service._index_model_id == embedder.model_id()
        assert service._store.chunk_stats("previous")["imp.failed"]["failed"] == 0
        assert await service._indexer._index_round() == 0
        assert resets == [True]
    finally:
        await service.stop()
