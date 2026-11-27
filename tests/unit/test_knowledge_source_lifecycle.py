"""Preserve complete source extraction, consent, version history and removal semantics.

These regressions retain the original isolated service, fault injection and
cleanup contracts. Shared factories live outside collected test modules.
"""

from __future__ import annotations

import asyncio
import io
import json
from pathlib import Path
import subprocess
import sys
import zipfile

import pytest

from knowledge.models import KnowledgePackError
from knowledge.registry import load_registry
from knowledge.service import KnowledgeService
from tests.knowledge_archive_fixtures import _prepare_export_vectors
from tests.knowledge_fixtures import _finished, _import, _pack
from tests.process_isolation import child_process_env
from utils.document_parser import DocumentLimits, parse_document, parse_document_sections

pytestmark = pytest.mark.unit


def _docx(body):
    """Use an isolated minimal OOXML document with checked required members."""
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w") as archive:
        archive.writestr("[Content_Types].xml", "<Types/>")
        archive.writestr("word/document.xml", '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body>' + body + '</w:body></w:document>')
    return stream.getvalue()


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
        restored = await service.restore_source(pack["pack_id"], value["doc_id"], 1, enabled_confirmed=True)
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


async def test_last_source_removal_rechecks_an_append_admitted_before_its_lock(tmp_path):
    """The check and the registry removal happen under one lock acquisition."""
    from knowledge.models import KnowledgePackError
    from knowledge.service import ImportJob

    service = KnowledgeService(tmp_path)
    await service.start()
    try:
        value = (await service.preview_source("notes.txt", b"Lighthouse reference."))["preview"]
        await _finished(service, await service.commit_source(value["preview_id"], value["sha256"]))
        pack_id = (await service.list_packs())[0]["pack_id"]
        remove = service._remove

        async def append_slips_in(*args, **kwargs):
            # An append of B wins the lock after the first check released it.
            stamp = "2026-01-01T00:00:00Z"
            service._jobs["append-b"] = ImportJob("append-b", pack_id, "queued", stamp, stamp, arrived_at=10**9)
            return await remove(*args, **kwargs)

        service._remove = append_slips_in
        with pytest.raises(KnowledgePackError) as error:
            await service.remove_source(pack_id, value["doc_id"])
        assert error.value.reason == "job_in_progress"
        assert [row["pack_id"] for row in await service.list_packs()] == [pack_id]
        assert service._jobs["append-b"].state == "queued"
    finally:
        service._jobs.pop("append-b", None)
        await service.stop()


async def test_last_source_removal_that_finds_a_committed_append_removes_only_that_document(tmp_path):
    service = KnowledgeService(tmp_path)
    await service.start()
    try:
        first = (await service.preview_source("a.txt", b"Lighthouse reference A.", name="References"))["preview"]
        await _finished(service, await service.commit_source(first["preview_id"], first["sha256"]))
        pack_id = (await service.list_packs())[0]["pack_id"]
        remove = service._remove
        calls = []

        async def append_lands_first(*args, **kwargs):
            if not calls:
                # B is appended and committed after the first check saw only A.
                second = (await service.preview_source("b.txt", b"Garden reference B.", pack_id=pack_id))["preview"]
                await _finished(service, await service.commit_source(second["preview_id"], second["sha256"]))
            calls.append(args)
            return await remove(*args, **kwargs)

        service._remove = append_lands_first
        result = await service.remove_source(pack_id, first["doc_id"])
        assert (await _finished(service, result))["state"] == "active"
        sources = {row["name"]: row for row in (await service.list_sources(pack_id))["sources"]}
        assert sources["a.txt"]["removed"] is True and sources["b.txt"]["removed"] is False
        assert [row["pack_id"] for row in await service.list_packs()] == [pack_id]
    finally:
        await service.stop()


async def test_frozen_source_input_with_the_registered_digest_repairs_a_missing_pack_file(tmp_path):
    service = KnowledgeService(tmp_path)
    await service.start()
    try:
        value = (await service.preview_source("notes.txt", b"Lighthouse reference."))["preview"]
        await _finished(service, await service.commit_source(value["preview_id"], value["sha256"]))
        pack_id = (await service.list_packs())[0]["pack_id"]
        record = service._registry.packs[pack_id]
        frozen = [service._sources.version(pack_id, value["doc_id"], 1)]
        (tmp_path / "packs" / record.file_name).unlink()
        assert not service._sources_managed(pack_id)
        repaired = await _finished(service, await service._project_sources(pack_id, frozen[0]["pack_name"], frozen))
        assert repaired["state"] == "active"
        assert service._registry.packs[pack_id].pack_sha256 == record.pack_sha256
        assert service._sources_managed(pack_id)
    finally:
        await service.stop()


async def test_source_task_does_not_replace_an_unrelated_pack_that_took_its_id(tmp_path):
    service = KnowledgeService(tmp_path)
    await service.start()
    try:
        value = (await service.preview_source("notes.txt", b"Lighthouse reference."))["preview"]
        await _finished(service, await service.commit_source(value["preview_id"], value["sha256"]))
        pack_id = (await service.list_packs())[0]["pack_id"]
        frozen = [service._sources.version(pack_id, value["doc_id"], 1)]
        # An unrelated local pack now holds the id; the old source history stays on disk.
        assert (await _import(service, _pack(pack_id)))["state"] == "active"
        unrelated = service._registry.packs[pack_id].pack_sha256
        # The same path a retry of the frozen source selection takes.
        retried = await _finished(service, await service._project_sources(pack_id, "References", frozen))
        assert retried["state"] == "failed" and retried["reason"] == "pack_changed"
        assert service._registry.packs[pack_id].pack_sha256 == unrelated
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


def test_complete_document_mode_reuses_limits_and_never_duplicates_nested_paragraphs():
    text = "A" * 33000
    data = _docx(f'<w:p><w:r><w:t>{text}</w:t></w:r></w:p>')
    assert parse_document("notes.docx", "", data)["truncated"]
    extended = parse_document("notes.docx", "", data, limits=DocumentLimits(max_extracted_chars=256000))
    assert not extended["truncated"] and extended["chars"] > 32000
    nested = _docx('<w:p><w:r><w:t>outer</w:t><w:p><w:r><w:t>nested</w:t></w:r></w:p></w:r></w:p>')
    sections = parse_document_sections("notes.docx", nested, limits=DocumentLimits(max_extracted_chars=256000))
    assert [value["text"] for value in sections] == ["outer", "nested"]


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
