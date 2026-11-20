"""Exercise unified pack policy, source history and deferred vector backfill."""

import asyncio
import json
import io
import zipfile
import sqlite3
import subprocess
import sys
import logging
import threading
import struct
from contextlib import closing
from pathlib import Path

import pytest

from knowledge.registry import load_registry
from knowledge.service import KnowledgeService
from tests.unit.test_knowledge_subsystem import _import, _pack
from utils.document_parser import DocumentLimits, parse_document, parse_document_sections
from utils.embedding_contract import EmbedDeferred
from tests.unit.test_knowledge_archive_v2 import make_archive, specification
from tests.process_isolation import child_process_env


pytestmark = pytest.mark.unit


async def test_pack_enabled_filters_tool_query_and_passive_context_without_losing_vectors(tmp_path):
    service = KnowledgeService(tmp_path)
    await service.start()
    try:
        await _import(service, _pack())
        assert service.availability()["tool_available"]
        await service.set_pack_auto_context("demo-memes", True)
        await service.set_pack_enabled("demo-memes", False)
        assert not service.availability()["tool_available"]
        assert (await service.query(query="rubber duck debugging"))["result"] == "miss"
        assert (await service.turn_context(text="rubber duck debugging"))["result"] == "off"
        assert service._vector_pack_ids() == ["demo-memes"]
        assert (await service.list_packs())[0]["enabled"] is False
        await service.set_pack_enabled("demo-memes", True)
        assert service.availability()["tool_available"]
        raw = json.loads((tmp_path / "registry.json").read_text())
        del raw["packs"]["demo-memes"]["enabled"]
        (tmp_path / "registry.json").write_text(json.dumps(raw))
        assert load_registry(tmp_path).packs["demo-memes"].enabled is True
    finally:
        await service.stop()


async def _finished(service, result):
    """Wait for the existing import runner's observable terminal state."""
    for _ in range(300):
        job = next(item for item in service.list_jobs() if item["job_id"] == result["job_id"])
        if job["state"] not in {"queued", "building"}:
            return job
        await asyncio.sleep(0.01)
    raise AssertionError("import did not finish")


async def test_sources_share_existing_pack_search_and_retain_all_versions(tmp_path):
    service = KnowledgeService(tmp_path)
    await service.start()
    try:
        preview = await service.preview_source("notes.txt", b"Lighthouses guide ships along the coast.", name="My references")
        value = preview["preview"]
        first = await service.commit_source(value["preview_id"], value["sha256"])
        assert (await _finished(service, first))["state"] == "active"
        pack = (await service.list_packs())[0]
        assert pack["origin"] == "imported" and pack["enabled"] is False and pack["sources_managed"] is True
        await service.set_pack_enabled(pack["pack_id"], True)
        assert (await service.query(query="lighthouses"))["result"] == "matched"
        changed = await service.preview_source("notes.txt", b"Gardens need sunlight.", pack_id=pack["pack_id"], doc_id=value["doc_id"])
        updated = changed["preview"]
        assert (await _finished(service, await service.commit_source(updated["preview_id"], updated["sha256"], enabled_confirmed=True)))["state"] == "active"
        versions = (await service.source_versions(pack["pack_id"], value["doc_id"]))["versions"]
        assert [row["version"] for row in versions] == [1, 2]
        restored = await service.restore_source(pack["pack_id"], value["doc_id"], 1)
        assert (await _finished(service, restored))["state"] == "active"
        assert (await service.query(query="lighthouses"))["result"] == "matched"
        assert len(list((tmp_path / "sources").rglob("original"))) == 2
        removed = await service.remove_source(pack["pack_id"], value["doc_id"])
        assert removed["state"] == "active"
        retained = (await service.list_packs())[0]
        assert retained["retained"] is True and retained["enabled"] is False and retained["sources_managed"] is True
        source = (await service.list_sources(pack["pack_id"]))["sources"][0]
        assert source["removed"] is True and source["current_version"] is None
        restored = await service.restore_source(pack["pack_id"], value["doc_id"], 2)
        assert (await _finished(service, restored))["state"] == "active"
        assert (await service.list_packs())[0]["retained"] is False
        assert (await service.list_packs())[0]["enabled"] is False
    finally:
        await service.stop()


@pytest.mark.parametrize("restart", [False, True])
async def test_finished_exports_outlive_the_bounded_task_window(tmp_path, restart):
    from knowledge.service import MAX_TRACKED_JOBS, ImportJob

    service = KnowledgeService(tmp_path)
    await service.start()
    try:
        export = ImportJob("e" * 32, "demo-memes", "active", "2026-01-01T00:00:00Z", "2026-01-01T00:00:00Z",
                           kind="pack_export", result={"artifact_id": "b" * 64, "format_version": 2})
        failed_export = ImportJob("f" * 32, "demo-memes", "failed", "2026-01-01T00:00:01Z",
                                  "2026-01-01T00:00:01Z", kind="pack_export")
        for job in (export, failed_export):
            service._remember_job(job)
            await service._persist_job(job)
        for index in range(MAX_TRACKED_JOBS + 5):
            stamp = f"2026-01-02T00:00:{index:02d}Z"
            job = ImportJob(f"{index:032x}", "demo-memes", "active", stamp, stamp)
            service._remember_job(job)
            await service._persist_job(job)
        if restart:
            await service.stop()
            service = KnowledgeService(tmp_path)
            await service.start()
        ids = [job["job_id"] for job in service.list_jobs()]
        assert export.job_id in ids and failed_export.job_id not in ids
        assert len(ids) == MAX_TRACKED_JOBS
    finally:
        await service.stop()


@pytest.mark.parametrize("pending", ["queued", "admitting", "export"])
async def test_removing_the_last_source_does_not_cancel_a_waiting_append(tmp_path, pending):
    from knowledge.models import KnowledgePackError
    from knowledge.service import ImportJob

    service = KnowledgeService(tmp_path)
    await service.start()
    try:
        value = (await service.preview_source("notes.txt", b"Lighthouse reference."))["preview"]
        await _finished(service, await service.commit_source(value["preview_id"], value["sha256"]))
        pack_id = (await service.list_packs())[0]["pack_id"]
        if pending == "export":
            stamp = "2026-01-01T00:00:00Z"
            service._jobs["export"] = ImportJob("export", pack_id, "queued", stamp, stamp, kind="pack_export")
            try:
                # An export does not install anything the removal could cancel.
                assert (await service.remove_source(pack_id, value["doc_id"]))["state"] == "active"
            finally:
                service._jobs.pop("export")
            return
        if pending == "queued":
            stamp = "2026-01-01T00:00:00Z"
            service._jobs["waiting-append"] = ImportJob("waiting-append", pack_id, "queued", stamp, stamp)
        else:
            service._admitting[pack_id] = 1
        with pytest.raises(KnowledgePackError) as error:
            await service.remove_source(pack_id, value["doc_id"])
        assert error.value.reason == "job_in_progress"
        assert [row["pack_id"] for row in await service.list_packs()] == [pack_id]
        if pending == "queued":
            assert service._jobs["waiting-append"].state == "queued"
            service._jobs.pop("waiting-append")
        else:
            service._admitting.pop(pack_id)
        assert (await service.remove_source(pack_id, value["doc_id"]))["state"] == "active"
    finally:
        await service.stop()


@pytest.mark.parametrize("restore_last_source_first", [False, True])
async def test_removed_source_pack_stays_absent_after_refresh_and_restart_without_deleting_history(tmp_path, restore_last_source_first):
    """Pack removal hides its row; last-document history remains a separate choice."""
    service = KnowledgeService(tmp_path)
    await service.start()
    try:
        value = (await service.preview_source("notes.txt", b"First lighthouse reference.", name="References"))["preview"]
        await _finished(service, await service.commit_source(value["preview_id"], value["sha256"]))
        pack_id = (await service.list_packs())[0]["pack_id"]
        changed = (await service.preview_source("notes.txt", b"Second lighthouse reference.",
                                               pack_id=pack_id, doc_id=value["doc_id"]))["preview"]
        await _finished(service, await service.commit_source(changed["preview_id"], changed["sha256"]))
        _prepare_export_vectors(service, pack_id)
        exported = await _finished(service, await service.export_pack(pack_id))
        assert exported["state"] == "active"
        artifact = service.artifact_path(exported["result"]["artifact_id"])
        preserved = {path: path.read_bytes() for path in (tmp_path / "sources" / pack_id).rglob("*")
                     if path.is_file() and ("originals" in path.parts or "versions" in path.parts)}
        preserved[artifact] = artifact.read_bytes()
        if restore_last_source_first:
            await service.remove_source(pack_id, value["doc_id"])
            assert (await service.list_packs())[0]["retained"] is True
            await service.stop()
            service = KnowledgeService(tmp_path)
            await service.start()
            assert (await service.list_packs())[0]["retained"] is True
            await _finished(service, await service.restore_source(pack_id, value["doc_id"], 1))
        await service.remove_pack(pack_id)
        assert not any(row["pack_id"] == pack_id for row in await service.list_packs())
        assert all(path.read_bytes() == raw for path, raw in preserved.items())
        assert list((tmp_path / "history" / pack_id).glob("*.json"))
        # Pre-fix removals left a source manifest without a visibility flag.
        # Retained originals alone must not resurrect those browser rows.
        manifest = tmp_path / "sources" / pack_id / "manifest.json"
        data = json.loads(manifest.read_text())
        data.pop("retained", None)
        manifest.write_text(json.dumps(data))
    finally:
        await service.stop()
    reopened = KnowledgeService(tmp_path)
    await reopened.start()
    try:
        assert not any(row["pack_id"] == pack_id for row in await reopened.list_packs())
        assert all(path.read_bytes() == raw for path, raw in preserved.items())
    finally:
        await reopened.stop()


@pytest.mark.parametrize("failed_write", ["sidecar", "registry"])
@pytest.mark.parametrize("remove_last_source", [False, True])
async def test_failed_source_pack_removal_keeps_the_active_registry_row(tmp_path, monkeypatch, failed_write, remove_last_source):
    """Visibility metadata cannot hide an active pack when removal does not commit."""
    from knowledge import service as module

    service = KnowledgeService(tmp_path)
    await service.start()
    try:
        value = (await service.preview_source("notes.txt", b"Lighthouse reference."))["preview"]
        await _finished(service, await service.commit_source(value["preview_id"], value["sha256"]))
        pack_id = (await service.list_packs())[0]["pack_id"]
        original = next((tmp_path / "sources" / pack_id / "originals").rglob("original"))

        def refuse(*_args):
            raise OSError("synthetic write failure")

        if failed_write == "sidecar":
            monkeypatch.setattr(service._sources, "set_retained", refuse)
        else:
            monkeypatch.setattr(module, "save_registry", refuse)
        with pytest.raises(OSError, match="synthetic write failure"):
            if remove_last_source:
                await service.remove_source(pack_id, value["doc_id"])
            else:
                await service.remove_pack(pack_id)
        rows = await service.list_packs()
        assert len(rows) == 1 and rows[0]["pack_id"] == pack_id and not rows[0]["retained"]
        assert pack_id in load_registry(tmp_path).packs
        assert original.read_bytes() == b"Lighthouse reference."
    finally:
        await service.stop()


def _docx(body):
    """Use an isolated minimal OOXML document with checked required members."""
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w") as archive:
        archive.writestr("[Content_Types].xml", "<Types/>")
        archive.writestr("word/document.xml", '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body>' + body + '</w:body></w:document>')
    return stream.getvalue()


def test_complete_document_mode_reuses_limits_and_never_duplicates_nested_paragraphs():
    text = "A" * 33000
    data = _docx(f'<w:p><w:r><w:t>{text}</w:t></w:r></w:p>')
    assert parse_document("notes.docx", "", data)["truncated"]
    extended = parse_document("notes.docx", "", data, limits=DocumentLimits(max_extracted_chars=256000))
    assert not extended["truncated"] and extended["chars"] > 32000
    nested = _docx('<w:p><w:r><w:t>outer</w:t><w:p><w:r><w:t>nested</w:t></w:r></w:p></w:r></w:p>')
    sections = parse_document_sections("notes.docx", nested, limits=DocumentLimits(max_extracted_chars=256000))
    assert [value["text"] for value in sections] == ["outer", "nested"]


class _PackageEmbedder:
    """Expose a certified resource identity without loading a native model."""

    def state(self):
        return "ready"

    def model_id(self):
        return self.spec()["model_id"]

    def spec(self):
        return specification()

    async def embed(self, text):
        raise AssertionError("install/export must not compute vectors")

    async def embed_batch(self, texts):
        raise AssertionError("install/export must not compute vectors")

    async def embed_documents(self, texts):
        raise EmbedDeferred(retry_after_ms=1)


def _prepare_export_vectors(service, pack_id):
    """Seed checked synthetic vectors so export tests never load an encoder."""
    service.embedder = _PackageEmbedder()
    spec = service.embedder.spec()
    pending = service._store.pending_chunks(model_id=spec["model_id"], pack_ids=[pack_id], limit=10000)
    vector = struct.pack("<64f", 1.0, *([0.0] * 63))
    stored, failed = service._store.store_vectors(
        model_id=spec["model_id"], rows=[(chunk_id, text_hash, vector) for chunk_id, text_hash, _ in pending],
        specification=spec,
    )
    assert stored == len(pending) and failed == 0


async def _manual_index(service):
    """Leave the real import runner alive while controlling index rounds."""
    service._tasks[1].cancel()
    await asyncio.gather(service._tasks[1], return_exceptions=True)


async def test_background_busy_defers_without_attempts_or_failure(tmp_path):
    class BusyEmbedder(_PackageEmbedder):
        def __init__(self):
            self.calls = []

        async def embed_documents(self, texts):
            self.calls.append(list(texts))
            return await super().embed_documents(texts)

    service = KnowledgeService(tmp_path)
    await service.start()
    await _manual_index(service)
    try:
        await _import(service, _pack())
        service.embedder = BusyEmbedder()
        assert await service._index_round() == 0
        assert len(service.embedder.calls) == 1 and service.embedder.calls[0]
        with closing(sqlite3.connect(service._store.database_path)) as conn:
            assert conn.execute("SELECT MAX(attempts) FROM chunks").fetchone()[0] == 0
        assert service._embedding_state() == "ready"
        assert service._store.pending_chunks(model_id=service.embedder.model_id(), pack_ids=["demo-memes"], limit=100)
    finally:
        await service.stop()


async def test_portable_vectors_survive_export_and_deleted_index_without_encoding(tmp_path):
    root = tmp_path / "installed"
    service = KnowledgeService(root, embedder=_PackageEmbedder())
    await service.start()
    await _manual_index(service)
    try:
        imported = await service.import_pack(make_archive(tmp_path / "source.zip"))
        assert (await _finished(service, imported))["state"] == "active"
        assert len(service._store.vectors_for_pack("imp.example", service.embedder.model_id())) == 1
        exported = await service.export_pack("imp.example", format_version=2)
        finished = await _finished(service, exported)
        assert finished["state"] == "active"
        assert service.artifact_path(finished["result"]["artifact_id"]).suffix == ".zip"
        assert await service._index_round() == 0
    finally:
        await service.stop()
    # Only the disposable SQLite files disappear; exact ZIP/original pack
    # bytes remain available to the normal startup reconciliation path.
    service._store.remove_files()
    reopened = KnowledgeService(root, embedder=_PackageEmbedder())
    await reopened.start()
    try:
        assert reopened.availability()["ready"]
        assert len(reopened._store.vectors_for_pack("imp.example", reopened.embedder.model_id())) == 1
    finally:
        await reopened.stop()


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


async def test_delete_export_removes_its_record_and_zip_without_losing_the_library(tmp_path):
    """The explicit delete cleans one output, never its source pack or history."""
    service = KnowledgeService(tmp_path)
    await service.start()
    try:
        value = (await service.preview_source("notes.txt", b"Lighthouse reference.", name="References"))["preview"]
        await _finished(service, await service.commit_source(value["preview_id"], value["sha256"]))
        pack_id = (await service.list_packs())[0]["pack_id"]
        _prepare_export_vectors(service, pack_id)
        exported = await _finished(service, await service.export_pack(pack_id))
        path = service.artifact_path(exported["result"]["artifact_id"])
        sources = {item: item.read_bytes() for item in (tmp_path / "sources").rglob("*") if item.is_file()}
        registry = (tmp_path / "registry.json").read_bytes()
        response = await service.delete_export(exported["job_id"])
        assert response == {"ok": True, "job_id": exported["job_id"], "artifact_retained": False}
        assert not path.exists() and not (tmp_path / "jobs" / (exported["job_id"] + ".json")).exists()
        assert not any(job["job_id"] == exported["job_id"] for job in service.list_jobs())
        assert (tmp_path / "registry.json").read_bytes() == registry
        assert all(item.read_bytes() == raw for item, raw in sources.items())
    finally:
        await service.stop()
    reopened = KnowledgeService(tmp_path)
    await reopened.start()
    try:
        assert not any(job["job_id"] == exported["job_id"] for job in reopened.list_jobs())
        assert any(pack["pack_id"] == pack_id for pack in await reopened.list_packs())
    finally:
        await reopened.stop()


@pytest.mark.parametrize("owner", ["registry", "other_export", "evicted_import"])
async def test_delete_export_keeps_zip_referenced_by_library_or_retained_tasks(tmp_path, owner):
    """Even a disk-only recovery task protects its immutable input archive."""
    from dataclasses import replace
    from knowledge.service import ImportJob
    from knowledge.registry import save_registry, utc_now

    service = KnowledgeService(tmp_path)
    await service.start()
    try:
        await _import(service, _pack())
        _prepare_export_vectors(service, "demo-memes")
        exported = await _finished(service, await service.export_pack("demo-memes"))
        digest = exported["result"]["artifact_id"]
        path = service.artifact_path(digest)
        before = path.read_bytes()
        if owner == "registry":
            service._registry = service._registry.with_pack(replace(service._registry.packs["demo-memes"], archive_sha256=digest))
            save_registry(tmp_path, service._registry)
        else:
            stamp = utc_now()
            other = ImportJob("d" * 32, "demo-memes", "failed", stamp, stamp)
            if owner == "other_export":
                other.kind, other.state, other.result = "pack_export", "active", dict(exported["result"])
            else:
                other.archive_sha256 = digest
            service._remember_job(other)
            await service._persist_job(other)
            if owner == "evicted_import":
                service._jobs.pop(other.job_id)
        assert (await service.delete_export(exported["job_id"]))["artifact_retained"] is True
        assert path.read_bytes() == before
        assert not (tmp_path / "jobs" / (exported["job_id"] + ".json")).exists()
    finally:
        await service.stop()


@pytest.mark.parametrize("failed_part", ["artifact", "record"])
async def test_failed_export_delete_stays_visible_and_can_be_retried(tmp_path, monkeypatch, failed_part):
    """No I/O failure may be reported as successful durable deletion."""
    service = KnowledgeService(tmp_path)
    await service.start()
    try:
        await _import(service, _pack())
        _prepare_export_vectors(service, "demo-memes")
        exported = await _finished(service, await service.export_pack("demo-memes"))
        artifact = service.artifact_path(exported["result"]["artifact_id"])
        record = tmp_path / "jobs" / (exported["job_id"] + ".json")
        failed = artifact if failed_part == "artifact" else record
        unlink = Path.unlink

        def refuse(path, *args, **kwargs):
            if path == failed:
                raise OSError("synthetic deletion failure")
            return unlink(path, *args, **kwargs)

        with monkeypatch.context() as patch:
            patch.setattr(Path, "unlink", refuse)
            with pytest.raises(OSError, match="synthetic deletion failure"):
                await service.delete_export(exported["job_id"])
        assert any(job["job_id"] == exported["job_id"] for job in service.list_jobs()) and record.exists()
        assert artifact.exists() is (failed_part == "artifact")
        assert (await service.delete_export(exported["job_id"]))["ok"]
        assert not artifact.exists() and not record.exists()
    finally:
        await service.stop()


async def test_export_delete_cannot_be_revived_by_a_late_worker_flush(tmp_path, monkeypatch):
    """Delete after active is visible but before the worker persists its end."""
    service = KnowledgeService(tmp_path)
    await service.start()
    reached, resume, flushed = asyncio.Event(), asyncio.Event(), asyncio.Event()
    original = service._persist_job

    async def pause(job):
        if job.kind == "pack_export" and job.state == "active":
            reached.set()
            await resume.wait()
        await original(job)
        if job.kind == "pack_export" and job.state == "active":
            flushed.set()

    try:
        await _import(service, _pack())
        _prepare_export_vectors(service, "demo-memes")
        monkeypatch.setattr(service, "_persist_job", pause)
        exported = await service.export_pack("demo-memes")
        await asyncio.wait_for(reached.wait(), 3)
        await service.delete_export(exported["job_id"])
        resume.set()
        await asyncio.wait_for(flushed.wait(), 3)
        assert not (tmp_path / "jobs" / (exported["job_id"] + ".json")).exists()
    finally:
        resume.set()
        await service.stop()
    assert not (tmp_path / "jobs" / (exported["job_id"] + ".json")).exists()


async def test_export_delete_waits_until_archive_import_owns_its_recovery_reference(tmp_path, monkeypatch):
    """Deletion cannot unlink between retaining a ZIP and admitting its import."""
    from knowledge import service as module

    service = KnowledgeService(tmp_path)
    await service.start()
    reached, resume = threading.Event(), threading.Event()
    original = module.canonical_pack_bytes
    importing = deleting = None

    def pause(pack):
        reached.set()
        if not resume.wait(5):
            raise TimeoutError("archive admission coordination timed out")
        return original(pack)

    try:
        await _import(service, _pack())
        _prepare_export_vectors(service, "demo-memes")
        exported = await _finished(service, await service.export_pack("demo-memes"))
        artifact = service.artifact_path(exported["result"]["artifact_id"])
        # Force the re-import through a job instead of the unchanged-pack fast path.
        await service.remove_pack("demo-memes")
        monkeypatch.setattr(module, "canonical_pack_bytes", pause)
        importing = asyncio.create_task(service.import_pack(artifact))
        assert await asyncio.to_thread(reached.wait, 3)
        deleting = asyncio.create_task(service.delete_export(exported["job_id"]))
        await asyncio.sleep(0)
        assert not deleting.done() and artifact.exists()
        resume.set()
        imported = await importing
        assert (await deleting)["artifact_retained"] is True
        assert (await _finished(service, imported))["state"] == "active"
        assert artifact.exists()
        assert service._registry.packs["demo-memes"].archive_sha256 == exported["result"]["artifact_id"]
    finally:
        resume.set()
        for task in (importing, deleting):
            if task is not None:
                await asyncio.gather(task, return_exceptions=True)
        await service.stop()


@pytest.mark.parametrize("case", ["invalid_id", "missing", "import", "queued", "failed", "no_result", "invalid_artifact"])
async def test_delete_export_refuses_anything_except_a_completed_output(tmp_path, case):
    """Untrusted identifiers and ordinary import tasks cannot delete files."""
    from knowledge.models import KnowledgePackError
    from knowledge.registry import utc_now
    from knowledge.service import ImportJob

    service = KnowledgeService(tmp_path)
    await service.start()
    try:
        job_id, digest = "a" * 32, "b" * 64
        stamp = utc_now()
        job = ImportJob(job_id, "demo-memes", "active", stamp, stamp,
                        kind="pack_export", result={"artifact_id": digest})
        if case == "import":
            job.kind = "pack_import"
        elif case in {"queued", "failed"}:
            job.state = case
        elif case == "no_result":
            job.result = None
        elif case == "invalid_artifact":
            job.result["artifact_id"] = "../../private"
        if case != "missing":
            service._remember_job(job)
            await service._persist_job(job)
        artifact = tmp_path / "artifacts" / (digest + ".neko-knowledge.zip")
        artifact.parent.mkdir(exist_ok=True)
        artifact.write_bytes(b"untouched")
        with pytest.raises(KnowledgePackError, match="invalid_request"):
            await service.delete_export("../../private" if case == "invalid_id" else job_id)
        assert artifact.read_bytes() == b"untouched"
        assert case == "missing" or job_id in service._jobs
    finally:
        await service.stop()


async def test_incomplete_export_fails_without_losing_a_committed_text_import(tmp_path, monkeypatch):
    service = KnowledgeService(tmp_path)
    await service.start()
    try:
        async def failed_refresh():
            raise OSError("private document details must not become task errors")

        monkeypatch.setattr(service, "_refresh_vectors_now", failed_refresh)
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
            await reopened._export_job(reopened._jobs[legacy.job_id])
        assert not list((tmp_path / "artifacts").glob("*"))
    finally:
        await reopened.stop()


async def test_killed_source_commit_retries_frozen_input_without_new_version(tmp_path):
    script = '''
import asyncio,json,os,sys
from pathlib import Path
from knowledge.service import KnowledgeService
async def main():
    service = KnowledgeService(Path(sys.argv[1]))
    await service.start()
    async def hold(job):
        await asyncio.Event().wait()
    service._commit_job = hold
    value = (await service.preview_source("private.txt", b"Frozen lighthouse coast."))["preview"]
    result = await service.commit_source(value["preview_id"], value["sha256"])
    print(json.dumps(result), flush=True)
    os._exit(41)
asyncio.run(main())
'''
    completed = await asyncio.to_thread(subprocess.run, [sys.executable, "-c", script, str(tmp_path)],
                                        cwd=Path(__file__).resolve().parents[2], capture_output=True, timeout=30,
                                        env=child_process_env(tmp_path / "child-runtime"))
    assert completed.returncode == 41, completed.stderr.decode()
    old = json.loads(completed.stdout.decode().splitlines()[-1])
    service = KnowledgeService(tmp_path)
    await service.start()
    try:
        interrupted = next(row for row in service.list_jobs() if row["job_id"] == old["job_id"])
        assert interrupted["state"] == "failed" and interrupted["reason"] == "process_interrupted"
        result = await service.retry_job(old["job_id"])
        assert (await _finished(service, result))["state"] == "active"
        pack = (await service.list_packs())[0]
        assert not pack["enabled"]
        source = (await service.list_sources(pack["pack_id"]))["sources"][0]
        assert source["current_version"] == 1 and source["versions_count"] == 1
        assert len(list((tmp_path / "sources").rglob("original"))) == 1
    finally:
        await service.stop()


async def test_new_tokenizer_replans_complete_inputs_before_any_encoding(tmp_path, monkeypatch):
    from knowledge import service as module

    class SmallInputs(_PackageEmbedder):
        def __init__(self):
            self.inputs = []

        def spec(self):
            return {**specification(), "max_length": 100, "model_id": "small-test"}

        def count_tokens(self, texts):
            return [len(text) for text in texts]

        async def embed_documents(self, texts):
            assert all(len(text) <= 100 for text in texts)
            self.inputs.extend(texts)
            return [[1.0] + [0.0] * 63 for _text in texts]

    service = KnowledgeService(tmp_path)
    await service.start()
    await _manual_index(service)
    try:
        value = (await service.preview_source("n.txt", ("Lighthouse coast. " * 80 + "TAILMARK").encode()))["preview"]
        assert (await _finished(service, await service.commit_source(value["preview_id"], value["sha256"])))["state"] == "active"
        embedder = SmallInputs()
        service.embedder = embedder
        monkeypatch.setattr(module, "_batch_pause", lambda _: 0)
        assert await service._index_round() > 0
        assert any("TAILMARK" in text for text in embedder.inputs)
        assert service._store.pending_chunks(model_id=embedder.model_id(), pack_ids=[next(iter(service._registry.packs))], limit=100) == []
        from knowledge.retrieval import RankedHit

        pack_id = next(iter(service._registry.packs))
        await service.set_pack_enabled(pack_id, True)
        with sqlite3.connect(service._store.database_path) as conn:
            entry_id, last = conn.execute("SELECT entry_id,MAX(chunk_index) FROM chunks").fetchone()
        ranked = RankedHit(entry_id=entry_id, score=1.0, exact=False, lexical_rank=None,
                           semantic_score=1.0, semantic_chunk=last)
        _hits, context = service._render([ranked], "unrelated semantic wording", None, service._registry)
        assert "TAILMARK" in context
    finally:
        await service.stop()


async def test_package_imported_before_model_assets_seeds_later_without_encoding(tmp_path):
    service = KnowledgeService(tmp_path / "installed")
    await service.start()
    await _manual_index(service)
    try:
        imported = await service.import_pack(make_archive(tmp_path / "source.zip"))
        assert (await _finished(service, imported))["state"] == "active"
        assert service._store.vectors_for_pack("imp.example", specification()["model_id"]) == []
        service.embedder = _PackageEmbedder()
        assert await service._index_round() == 0
        assert len(service._store.vectors_for_pack("imp.example", service.embedder.model_id())) == 1
    finally:
        await service.stop()


async def test_same_profile_with_different_weights_never_queries_as_current(tmp_path):
    class ChangedWeights(_PackageEmbedder):
        def __init__(self):
            self.changed = False
            self.calls = 0

        def spec(self):
            value = specification()
            if self.changed:
                value["weights_sha256"]["model"] = "b" * 64
            return value

        async def embed_query(self, text):
            self.calls += 1
            return [1.0] + [0.0] * 63

    embedder = ChangedWeights()
    service = KnowledgeService(tmp_path / "installed", embedder=embedder)
    await service.start()
    await _manual_index(service)
    try:
        imported = await service.import_pack(make_archive(tmp_path / "source.zip"))
        assert (await _finished(service, imported))["state"] == "active"
        assert service._vectors is not None
        embedder.changed = True
        result = await service.query(query="coast")
        assert result["result"] == "matched" and result["retrieval_mode"] == "bm25"
        assert embedder.calls == 0
        assert service._store.load_vectors(embedder.model_id(), specification=embedder.spec()) is None
        pending = service._store.pending_chunks(model_id=embedder.model_id(), specification=embedder.spec(), pack_ids=["imp.example"], limit=10)
        assert len(pending) == 1
    finally:
        await service.stop()


async def test_private_exception_message_and_cause_never_reach_logs_or_api(tmp_path, monkeypatch):
    from app.memory_server import knowledge_routes
    from knowledge import service as module

    service = KnowledgeService(tmp_path)
    await service.start()
    output = io.StringIO()
    handler = logging.StreamHandler(output)
    # Product loggers deliberately do not propagate to pytest's root handler.
    # Capture their real formatted records, including any accidental traceback.
    monkeypatch.setattr(module.logger, "handlers", [handler])
    monkeypatch.setattr(knowledge_routes.logger, "handlers", [handler])
    secret = "private-filename-secret-query-secret-body"
    try:
        await _import(service, _pack())
        await service.set_pack_auto_context("demo-memes", True)

        def explode(*args, **kwargs):
            try:
                raise ValueError(secret)
            except ValueError as cause:
                raise RuntimeError(secret) from cause

        monkeypatch.setattr(service, "_current_packs", explode)
        assert (await service.query(query=secret))["result"] == "error"
        assert (await service.turn_context(text=secret))["result"] == "error"
        monkeypatch.setattr(knowledge_routes, "_service", service)

        async def rejected(svc):
            explode()

        response = await knowledge_routes._call(rejected)
        assert response.status_code == 500
        assert secret not in response.body.decode()
        assert secret not in output.getvalue()
        assert "RuntimeError" in output.getvalue()
        assert secret not in json.dumps(service.diagnostics.snapshot())
    finally:
        await service.stop()


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


async def test_offline_imported_pack_has_no_managed_sources_and_cannot_be_reprojected(tmp_path):
    from knowledge.models import KnowledgePackError

    service = KnowledgeService(tmp_path)
    await service.start()
    try:
        payload = _pack(entries=[{"title": "Offline entry", "content": "Keep this full offline content.",
                                  "tags": ["doc:" + "a" * 32, "ver:1"]}])
        result = await service.import_pack(json.dumps(payload).encode(), _options={"origin": "imported", "enabled": False})
        assert (await _finished(service, result))["state"] == "active"
        pack = (await service.list_packs())[0]
        assert pack["sources_managed"] is False and not pack["enabled"]
        assert (await service.list_sources(pack["pack_id"]))["sources"] == []
        original_sha = service._registry.packs[pack["pack_id"]].pack_sha256
        with pytest.raises(KnowledgePackError):
            await service.preview_source("new.txt", b"Never replace offline content.", pack_id=pack["pack_id"])
        with pytest.raises(KnowledgePackError):
            await service.restore_source(pack["pack_id"], "a" * 32, 1)
        assert service._registry.packs[pack["pack_id"]].pack_sha256 == original_sha
        assert (await service.get_entry(pack["pack_id"], "Offline entry"))["entry"]["content"] == "Keep this full offline content."
    finally:
        await service.stop()


async def test_retained_history_does_not_authorize_overwriting_an_unrelated_same_id_pack(tmp_path):
    from knowledge.models import KnowledgePackError

    service = KnowledgeService(tmp_path)
    await service.start()
    try:
        value = (await service.preview_source("original.txt", b"Retain this historical original."))["preview"]
        assert (await _finished(service, await service.commit_source(value["preview_id"], value["sha256"])))["state"] == "active"
        pack_id = (await service.list_packs())[0]["pack_id"]
        foreign = _pack(entries=[{"title": "Offline", "content": "Unrelated complete package contents."}])
        foreign["pack_id"] = pack_id
        result = await service.import_pack(json.dumps(foreign).encode(), _options={"origin": "imported", "enabled": False})
        assert (await _finished(service, result))["state"] == "active"
        assert service._sources.has_history(pack_id)
        assert (await service.list_packs())[0]["sources_managed"] is False
        assert (await service.list_sources(pack_id))["sources"] == []
        with pytest.raises(KnowledgePackError):
            await service.restore_source(pack_id, value["doc_id"], 1)
        assert (await service.get_entry(pack_id, "Offline"))["entry"]["content"] == foreign["entries"][0]["content"]
    finally:
        await service.stop()
