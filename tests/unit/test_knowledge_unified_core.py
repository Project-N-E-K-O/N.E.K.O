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
        assert (await _finished(service, await service.commit_source(updated["preview_id"], updated["sha256"])))["state"] == "active"
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


async def _manual_index(service):
    """Leave the real import runner alive while controlling index rounds."""
    service._tasks[1].cancel()
    await asyncio.gather(service._tasks[1], return_exceptions=True)


async def test_background_busy_defers_without_attempts_or_failure(tmp_path):
    service = KnowledgeService(tmp_path)
    await service.start()
    await _manual_index(service)
    try:
        await _import(service, _pack())
        service.embedder = _PackageEmbedder()
        assert await service._index_round() == 0
        with sqlite3.connect(service._store.database_path) as conn:
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
        exported = await service.export_pack("imp.example", format_version=2, version="2")
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


async def test_text_export_works_without_encoder_and_post_commit_failure_stays_active(tmp_path, monkeypatch):
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
        exported = await service.export_pack("demo-memes", format_version=1)
        finished = await _finished(service, exported)
        assert finished["state"] == "active" and finished["result"]["format_version"] == 1
    finally:
        await service.stop()


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
        queued = await service.export_pack("demo-memes", format_version=1)
        assert await asyncio.to_thread(reached.wait, 3)
        assert await service.cancel_job(queued["job_id"]) is (phase == "sync")
        proceed.set()
        finished = await _finished(service, queued)
        assert finished["state"] == ("cancelled" if phase == "sync" else "active")
        artifacts = list((tmp_path / "artifacts").glob("*.neko-knowledge.json"))
        assert len(artifacts) == (0 if phase == "sync" else 1)
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
