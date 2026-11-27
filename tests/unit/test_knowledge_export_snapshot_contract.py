"""Freeze validated export bytes and version identity through cancellation and retry.

These regressions retain the original isolated service, fault injection and
cleanup contracts. Shared factories live outside collected test modules.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
import threading
import zipfile

import pytest

from knowledge.models import KnowledgePackError
from knowledge.service import KnowledgeService
from tests.knowledge_archive_fixtures import _PackageEmbedder, _prepare_export_vectors
from tests.knowledge_fixtures import _finished, _import, _manual_index, _pack

pytestmark = pytest.mark.unit


@pytest.mark.parametrize("retry", [False, True])
@pytest.mark.parametrize("changed", ["pack_id", "source", "tags", "alias", "invalid_json"])
async def test_export_rejects_changed_pack_bytes_before_decoding(tmp_path, changed, retry):
    """Direct and retried exports must retain the registered pack identity."""
    service = KnowledgeService(tmp_path)
    await service.start()
    await _manual_index(service)
    try:
        assert (await _import(service, _pack()))["state"] == "active"
        if retry:
            # A real failed job exercises the existing export retry admission.
            failed = await _finished(service, await service.export_pack("demo-memes"))
            assert failed["state"] == "failed" and failed["reason"] == "embedding_unavailable"
        _prepare_export_vectors(service, "demo-memes")
        record = service._registry.packs["demo-memes"]
        path = tmp_path / "packs" / record.file_name
        original = path.read_bytes()
        payload = json.loads(original)
        if changed == "pack_id":
            payload["pack_id"] = "changed-pack"
        elif changed == "source":
            payload["source"]["name"] = "Changed source"
        elif changed == "tags":
            payload["entries"][0]["tags"].append("changed-tag")
        elif changed == "alias":
            payload["entries"][0]["terms"]["alias"].append("changed alias")
        # Even malformed replacement bytes must report the digest mismatch.
        changed_raw = b"not-json" if changed == "invalid_json" else json.dumps(payload).encode()
        path.write_bytes(changed_raw)
        queued = await service.retry_job(failed["job_id"]) if retry else await service.export_pack("demo-memes")
        finished = await _finished(service, queued)
        assert finished["state"] == "failed" and finished["reason"] == "pack_file_mismatch"
        assert not finished.get("result")
        assert not list((tmp_path / "artifacts").glob("*.zip"))
        assert path.read_bytes() == changed_raw
        assert service._registry.packs["demo-memes"] == record
        assert service._store.pack_versions()["demo-memes"] == record.pack_sha256
        # Restoring exactly the registered bytes permits the normal retry.
        path.write_bytes(original)
        restored = await _finished(service, await service.retry_job(finished["job_id"]))
        assert restored["state"] == "active"
        assert restored["result"]["version"] == service._jobs[finished["job_id"]].export_version
    finally:
        await service.stop()


async def test_export_decodes_the_same_bytes_it_checked(tmp_path, monkeypatch):
    """A disk replacement after the bounded read cannot change this snapshot."""
    from knowledge import exports as module

    service = KnowledgeService(tmp_path)
    await service.start()
    await _manual_index(service)
    try:
        await _import(service, _pack())
        _prepare_export_vectors(service, "demo-memes")
        path = tmp_path / "packs" / service._registry.packs["demo-memes"].file_name
        read = module.read_pack_bytes
        reads = []

        def replace_after_read(file_path):
            raw = read(file_path)
            if file_path == path:
                reads.append(raw)
                payload = json.loads(raw)
                payload["source"]["name"] = "Replaced after snapshot"
                path.write_bytes(json.dumps(payload).encode())
            return raw

        monkeypatch.setattr(module, "read_pack_bytes", replace_after_read)
        finished = await _finished(service, await service.export_pack("demo-memes"))
        assert finished["state"] == "active" and len(reads) == 1
        with zipfile.ZipFile(service.artifact_path(finished["result"]["artifact_id"])) as archive:
            assert json.loads(archive.read("pack.json"))["source"]["name"] == "Demo"
        assert json.loads(path.read_bytes())["source"]["name"] == "Replaced after snapshot"
    finally:
        await service.stop()


async def test_export_versions_are_unique_and_survive_restart_before_retry(tmp_path):
    """Fresh exports receive unique versions; persisted retries keep their release."""
    service = KnowledgeService(tmp_path)
    await service.start()
    try:
        await _import(service, _pack())
        _prepare_export_vectors(service, "demo-memes")
        versions = []
        for _ in range(2):
            finished = await _finished(service, await service.export_pack("demo-memes"))
            assert finished["state"] == "active"
            result = finished["result"]
            versions.append(result["version"])
            assert result["filename"].startswith("Demo_")
            assert result["version"] not in result["filename"]
            with zipfile.ZipFile(service.artifact_path(result["artifact_id"])) as archive:
                assert json.loads(archive.read("manifest.json"))["version"] == result["version"]
        assert versions[0] != versions[1]
        # Cancel before the worker takes the task, then cross a real restart.
        queued = await service.export_pack("demo-memes")
        assert await service.cancel_job(queued["job_id"])
        version = service._jobs[queued["job_id"]].export_version
        record = json.loads((tmp_path / "jobs" / (queued["job_id"] + ".json")).read_text())
        assert record["export_version"] == version and version not in versions
    finally:
        await service.stop()
    reopened = KnowledgeService(tmp_path, embedder=_PackageEmbedder())
    await reopened.start()
    try:
        assert reopened._jobs[queued["job_id"]].export_version == version
        retried = await _finished(reopened, await reopened.retry_job(queued["job_id"]))
        assert retried["state"] == "active" and retried["job_id"] != queued["job_id"]
        assert retried["result"]["version"] == version
        with zipfile.ZipFile(reopened.artifact_path(retried["result"]["artifact_id"])) as archive:
            assert json.loads(archive.read("manifest.json"))["version"] == version
    finally:
        await reopened.stop()


async def test_incomplete_export_fails_without_losing_a_committed_text_import(tmp_path, monkeypatch):
    service = KnowledgeService(tmp_path)
    await service.start()
    try:
        async def failed_refresh():
            raise OSError("private document details must not become task errors")

        monkeypatch.setattr(service._indexer, "_refresh_vectors_now", failed_refresh)
        imported = await service.import_pack(json.dumps(_pack()).encode())
        finished = await _finished(service, imported)
        assert finished["state"] == "active" and not finished["reason"]
        assert (await service.query(query="rubber duck debugging"))["result"] == "matched"
        # JSON imports remain usable without a model, but their incomplete
        # vectors cannot become a silently downgraded text-only export.
        exported = await service.export_pack("demo-memes")
        finished = await _finished(service, exported)
        assert finished["state"] == "failed" and finished["reason"] == "embedding_unavailable"
        assert not list((tmp_path / "artifacts").iterdir())
        version = service._jobs[exported["job_id"]].export_version
        _prepare_export_vectors(service, "demo-memes")
        retried = await _finished(service, await service.retry_job(exported["job_id"]))
        assert retried["state"] == "active" and retried["result"]["version"] == version
    finally:
        await service.stop()


@pytest.mark.parametrize("format_version", [1, True, 2.0, "2", None])
async def test_service_refuses_retired_or_invalid_export_formats_before_queueing(tmp_path, format_version):
    """Direct callers cannot recover the retired text-only export path."""
    from knowledge.models import KnowledgePackError

    service = KnowledgeService(tmp_path)
    await service.start()
    try:
        await _import(service, _pack())
        before = service.list_jobs()
        with pytest.raises(KnowledgePackError, match="invalid_request"):
            await service.export_pack("demo-memes", format_version=format_version)
        assert service.list_jobs() == before
        assert not list((tmp_path / "artifacts").glob("*"))
    finally:
        await service.stop()


async def test_retired_persisted_export_cannot_resume_or_retry_as_another_format(tmp_path):
    """A retained v1 task stays inspectable but cannot create a new artifact."""
    from knowledge.models import KnowledgePackError
    from knowledge.registry import utc_now
    from knowledge.service import ImportJob

    service = KnowledgeService(tmp_path)
    await service.start()
    try:
        await _import(service, _pack())
        stamp = utc_now()
        legacy = ImportJob("e" * 32, "demo-memes", "cancelled", stamp, stamp, kind="pack_export", export_format=1,
                           staged_sha256=service._registry.packs["demo-memes"].pack_sha256)
        service._remember_job(legacy)
        await service._persist_job(legacy)
    finally:
        await service.stop()
    reopened = KnowledgeService(tmp_path)
    await reopened.start()
    try:
        assert any(row["job_id"] == legacy.job_id for row in reopened.list_jobs())
        with pytest.raises(KnowledgePackError, match="invalid_request"):
            await reopened.retry_job(legacy.job_id)
        with pytest.raises(KnowledgePackError, match="invalid_request"):
            await reopened._exports.run(reopened._jobs[legacy.job_id])
        assert not list((tmp_path / "artifacts").glob("*"))
    finally:
        await reopened.stop()


@pytest.mark.parametrize("phase", ["sync", "replace"])
async def test_export_cancel_is_exclusive_with_artifact_publication(tmp_path, monkeypatch, phase):
    """Exercise both sides of the gate with the real queued export worker."""
    from knowledge import archive

    service = KnowledgeService(tmp_path)
    await service.start()
    reached, proceed = threading.Event(), threading.Event()
    try:
        await _import(service, _pack())
        _prepare_export_vectors(service, "demo-memes")
        sync, replace = archive._sync, archive.os.replace

        def pause():
            reached.set()
            if not proceed.wait(5):
                raise TimeoutError("export coordination timed out")

        def during_sync(path):
            sync(path)
            if phase == "sync":
                pause()

        def during_replace(source, target):
            # os.replace is shared with atomic JSON writes. Only intercept the
            # final artifact rename, after the service's gate has committed.
            if phase == "replace" and Path(target).parent == tmp_path / "artifacts":
                pause()
            return replace(source, target)

        monkeypatch.setattr(archive, "_sync", during_sync)
        monkeypatch.setattr(archive.os, "replace", during_replace)
        queued = await service.export_pack("demo-memes")
        version = service._jobs[queued["job_id"]].export_version
        assert await asyncio.to_thread(reached.wait, 3)
        assert await service.cancel_job(queued["job_id"]) is (phase == "sync")
        proceed.set()
        finished = await _finished(service, queued)
        assert finished["state"] == ("cancelled" if phase == "sync" else "active")
        artifacts = list((tmp_path / "artifacts").glob("*.neko-knowledge.zip"))
        assert len(artifacts) == (0 if phase == "sync" else 1)
        if phase == "sync":
            # Retrying an admitted v2 task retains the sole complete format.
            retried = await _finished(service, await service.retry_job(queued["job_id"]))
            assert retried["state"] == "active" and retried["result"]["format_version"] == 2
            assert retried["result"]["version"] == version
    finally:
        proceed.set()
        await service.stop()
