"""Exercise personal-library data and lifecycle boundaries in isolated roots."""

import io
import asyncio
import json
import threading
from contextlib import contextmanager
from copy import deepcopy
from types import SimpleNamespace
import zipfile

import pytest

from services.knowledge_library.documents import parse_source
from services.knowledge_library.models import LibraryError, LibraryLimits
from services.knowledge_library.paths import library_root
from services.knowledge_library.service import LibraryService


pytestmark = pytest.mark.unit_fast


def test_text_decoding_and_complete_output():
    text = "第一段中文资料。\n\n第二段保留。\n\n第一段中文资料。"
    parsed = parse_source("notes.md", text.encode("utf-16"), LibraryLimits())
    assert parsed["content"] == text
    assert [part["location"]["paragraph"] for part in parsed["sections"]] == [1, 2, 3]
    assert parse_source("old.txt", text.encode("gb18030"), LibraryLimits())["content"] == text


def test_docx_preserves_repeated_paragraphs_and_reports_real_positions():
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as archive:
        archive.writestr("[Content_Types].xml", "<Types/>")
        archive.writestr(
            "word/document.xml",
            '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
            '<w:body><w:p><w:r><w:t>Repeated</w:t></w:r></w:p>'
            '<w:p><w:r><w:t>Repeated</w:t></w:r></w:p></w:body></w:document>',
        )
    parsed = parse_source("draft.docx", output.getvalue(), LibraryLimits())
    assert parsed["content"] == "Repeated\n\nRepeated"
    assert parsed["sections"][1]["location"] == {"member": "word/document.xml", "paragraph": 2}


@pytest.mark.parametrize("filename,data,limits,reason", [
    ("empty.txt", b"", LibraryLimits(), "empty_file"),
    ("large.txt", b"1234", LibraryLimits(max_document_bytes=3), "document_too_large"),
    ("long.txt", b"1234", LibraryLimits(max_document_chars=3), "document_text_too_large"),
    ("binary.txt", b"\x00\x00\x00\x01", LibraryLimits(), "invalid_text_encoding"),
    ("table.xlsx", b"123", LibraryLimits(), "unsupported_document"),
    ("wrong.pdf", b"123", LibraryLimits(), "invalid_pdf"),
])
def test_document_failures_never_report_partial_success(filename, data, limits, reason):
    with pytest.raises(LibraryError, match=reason):
        parse_source(filename, data, limits)


def test_root_resolution_has_no_checkout_fallback(tmp_path):
    assert library_root(SimpleNamespace(app_docs_dir=tmp_path)) == tmp_path / "knowledge_library"
    with pytest.raises(LibraryError, match="storage_unavailable"):
        library_root(SimpleNamespace())


class FakeEmbedder:
    """Deterministic test computation; never load models or contact providers."""

    def __init__(self):
        self.calls = 0
        self.forbidden = False

    async def specification(self):
        if self.forbidden:
            raise AssertionError("unexpected computation")
        return {"dimension": 2, "model_id": "isolated-test-model"}

    async def plan_inputs(self, inputs, **_kwargs):
        """Keep short deterministic fixtures inside an explicit fake token plan."""
        return [[{"start": 0, "end": len(row["body"])}] for row in inputs]

    async def embed_batch(self, texts, **_kwargs):
        self.calls += len(texts)
        if self.forbidden:
            raise AssertionError("unexpected computation")
        return [[1.0, 0.0] for _text in texts]


@pytest.fixture
async def library(tmp_path):
    service = LibraryService(tmp_path, embedder=FakeEmbedder())
    await service.start()
    try:
        yield service
    finally:
        await service.close()


async def finished(service, job):
    """Wait for the owned runner, rather than assuming a timer implies completion."""
    await asyncio.wait_for(service.jobs.queue.join(), 5)
    return await service.get_job(job["job_id"])


async def add_document(service, library_id, content, *, document_id=None):
    preview_job = await service.preview_document(library_id, "notes.txt", content.encode(), document_id)
    preview_job = await finished(service, preview_job)
    assert preview_job["state"] == "completed", preview_job
    preview = await service.get_preview(preview_job["result"]["preview_id"])
    job = await service.confirm_preview(preview["preview_id"], preview["sha256"])
    result = await finished(service, job)
    assert result["state"] == "completed", result
    return result["result"]


def _precomputed_install_snapshot(service, snapshot):
    """Supply saved vectors explicitly; author snapshots now freeze source only."""
    documents = service.store.documents(service.store.manifest(snapshot["library_id"]))
    vectors = {row["chunk_id"]: row["vector"] for document in documents for row in document["chunks"]}
    return {**snapshot, "chunks": [{**row, "vector": vectors[row["chunk_id"]]} for row in snapshot["chunks"]]}


async def test_preview_confirmation_versions_and_frozen_sources(library):
    created = await library.create_library("My references")
    identifier = created["library_id"]
    job = await library.preview_document(identifier, "notes.txt", b"Original astronomy material")
    done = await finished(library, job)
    preview = await library.get_preview(done["result"]["preview_id"])
    assert (await library.search(identifier, "astronomy"))["hits"] == []
    with pytest.raises(LibraryError, match="preview_changed"):
        await library.confirm_preview(preview["preview_id"], "wrong-hash")
    done = await finished(library, await library.confirm_preview(preview["preview_id"], preview["sha256"]))
    document_id = done["result"]["document_id"]
    frozen = await library.freeze_library(identifier, version="1.0.0")
    before = await library.get_document(identifier, document_id)
    await add_document(library, identifier, "Updated mineralogy material", document_id=document_id)
    after = await library.get_document(identifier, document_id)
    assert before["version_id"] != after["version_id"]
    assert frozen["documents"][0]["content"] == "Original astronomy material"
    assert (await library.search(identifier, "mineralogy"))["hits"][0]["document_id"] == document_id
    await library.delete_document(identifier, document_id)
    assert (await library.get_library(identifier))["documents"] == []
    assert frozen["chunks"][0]["text"] == "Original astronomy material"
    assert json.loads((library.store.root / "jobs/snapshots" / f"{frozen['snapshot_id']}.json").read_text())["package_id"] == created["work_id"]


async def test_duplicate_content_is_reported_without_overwriting(library):
    identifier = (await library.create_library("References"))["library_id"]
    first = await add_document(library, identifier, "Same content")
    second = await add_document(library, identifier, "Same content")
    assert second == {"document_id": first["document_id"], "unchanged": True}
    assert len((await library.get_library(identifier))["documents"]) == 1


async def test_failed_manifest_update_keeps_original_text_vectors_and_index(library, monkeypatch):
    identifier = (await library.create_library("Atomic versions"))["library_id"]
    first = await add_document(library, identifier, "Old searchable astronomy facts")
    previous = await library.get_document(identifier, first["document_id"])
    preview_job = await finished(library, await library.preview_document(identifier, "notes.txt", b"New geology facts", first["document_id"]))
    preview = await library.get_preview(preview_job["result"]["preview_id"])

    def full_disk(_manifest):
        raise OSError("simulated full disk")

    monkeypatch.setattr(library.store, "_write_manifest", full_disk)
    job = await finished(library, await library.confirm_preview(preview["preview_id"], preview["sha256"]))
    assert job["state"] == "failed"
    assert await library.get_document(identifier, first["document_id"]) == previous
    assert (await library.search(identifier, "astronomy"))["hits"]
    assert not (await library.search(identifier, "geology"))["hits"][0]["match_type"] == "keyword"


async def test_derived_database_loss_recovers_without_document_reembedding(library):
    identifier = (await library.create_library("Rebuild"))["library_id"]
    first = await add_document(library, identifier, "Durable original sources")
    saved = await library.get_document(identifier, first["document_id"])
    calls = library.embedder.calls
    library.embedder = None  # No computation capability at all during index recovery.
    manifest = library.store.manifest(identifier)
    library.store._index_path(manifest).write_bytes(b"damaged sqlite")
    result = await library.search(identifier, "Durable")
    assert result["hits"][0]["text"] == "Durable original sources"
    assert result["semantic_state"] == "unavailable"
    assert await library.get_document(identifier, first["document_id"]) == saved
    assert calls > 0


async def test_cancelled_parser_retains_worker_slot_and_deleted_library_stays_deleted(tmp_path):
    started, release = threading.Event(), threading.Event()
    entered = []

    def blocked_parser(filename, data, limits):
        entered.append(filename)
        started.set()
        assert release.wait(5)
        return parse_source(filename, data, limits)

    service = LibraryService(tmp_path, parser=blocked_parser, limits=LibraryLimits(max_pending_jobs=2))
    await service.start()
    try:
        identifier = (await service.create_library("Cancelled"))["library_id"]
        first = await service.preview_document(identifier, "first.txt", b"First text")
        assert await asyncio.to_thread(started.wait, 2)
        await service.cancel_job(first["job_id"])
        second = await service.preview_document(identifier, "second.txt", b"Second text")
        with pytest.raises(LibraryError, match="jobs_busy"):
            await service.preview_document(identifier, "third.txt", b"Third")
        assert entered == ["first.txt"]
        await service.delete_library(identifier)
        release.set()
        assert (await finished(service, first))["state"] == "cancelled"
        assert (await service.get_job(second["job_id"]))["state"] == "cancelled"
        assert await service.list_libraries() == []
        assert json.loads(service.store._path(identifier).read_text())["deleted"] is True
    finally:
        release.set()
        await service.close()


async def test_restart_reports_interrupted_and_explicit_retry_uses_original_input(tmp_path):
    service = LibraryService(tmp_path)
    await service.start()
    identifier = (await service.create_library("Restart"))["library_id"]
    job = await finished(service, await service.preview_document(identifier, "frozen.txt", b"Frozen original"))
    record = deepcopy(service.jobs.records[job["job_id"]])
    await service.close()
    # Simulate process exit after queued state was saved, without real user paths.
    record["state"] = "queued"
    (tmp_path / "jobs" / f"{job['job_id']}.json").write_text(json.dumps(record))
    restarted = LibraryService(tmp_path)
    await restarted.start()
    try:
        assert (await restarted.get_job(job["job_id"]))["state"] == "interrupted"
        retried = await finished(restarted, await restarted.retry_job(job["job_id"]))
        assert retried["state"] == "completed"
        assert (await restarted.get_preview(retried["result"]["preview_id"]))["content"] == "Frozen original"
    finally:
        await restarted.close()


async def test_install_precomputed_snapshot_is_atomic_idempotent_and_never_computes(library):
    identifier = (await library.create_library("Author"))["library_id"]
    await add_document(library, identifier, "Shared complete sources")
    snapshot = await library.freeze_library(identifier, version="1.0.0")
    snapshot = _precomputed_install_snapshot(library, snapshot)
    library.embedder.forbidden = True
    metadata = {"work_id": snapshot["package_id"], "version": "1.0.0", "artifact_sha256": "a" * 64,
                "artifact_path": "artifacts/test-package.zip"}
    installed = await library.install_snapshot(snapshot, installation=metadata)
    assert installed["kind"] == "installed"
    assert installed["library_id"] != identifier
    assert await library.install_snapshot(snapshot, installation=metadata) == installed
    result = await library.search(installed["library_id"], "Shared")
    assert result["hits"][0]["text"] == "Shared complete sources"
    with pytest.raises(LibraryError, match="immutable_version_conflict"):
        await library.install_snapshot(snapshot, installation={**metadata, "artifact_sha256": "b" * 64})
    assert len(await library.list_libraries()) == 2


async def test_freeze_selection_is_checked_and_persisted_under_same_lock(library):
    identifier = (await library.create_library("Selected files"))["library_id"]
    first = await add_document(library, identifier, "Public selected material")
    second = await add_document(library, identifier, "Private unselected material")
    frozen = await library.freeze_library(identifier, document_ids=[first["document_id"]])
    assert [document["document_id"] for document in frozen["documents"]] == [first["document_id"]]
    assert {chunk["document_id"] for chunk in frozen["chunks"]} == {first["document_id"]}
    saved = (library.store.root / "jobs/snapshots" / f"{frozen['snapshot_id']}.json").read_text()
    assert "Private unselected" not in saved
    assert json.loads(saved) == frozen
    for invalid in ([], ["unknown-document"], [second["document_id"], second["document_id"]]):
        with pytest.raises(LibraryError, match="invalid_document_selection"):
            await library.freeze_library(identifier, document_ids=invalid)


async def test_write_guard_failure_cannot_kill_owned_job_queue(tmp_path):
    denied = False

    @contextmanager
    def guard():
        if denied:
            raise LibraryError("storage_root_changed")
        yield

    service = LibraryService(tmp_path, write_guard=guard)
    await service.start()
    release = asyncio.Event()

    async def runner(_context):
        nonlocal denied
        await release.wait()
        denied = True
        return {"finished": True}

    try:
        first = await service.submit_job("first", None, runner, frozen_input={})
        second = await service.submit_job("second", None, runner, frozen_input={})
        release.set()
        await asyncio.wait_for(service.jobs.queue.join(), 3)
        assert (await service.get_job(first["job_id"]))["state"] == "completed"
        assert (await service.get_job(second["job_id"]))["reason"] == "storage_root_changed"
        assert not service.jobs.worker.done()
    finally:
        release.set()
        await service.close()


async def test_incompatible_install_reports_unavailable_and_late_update_cannot_revive_delete(library):
    identifier = (await library.create_library("Author"))["library_id"]
    await add_document(library, identifier, "Package content")
    snapshot = await library.freeze_library(identifier)
    snapshot = _precomputed_install_snapshot(library, snapshot)
    metadata = {"work_id": snapshot["package_id"], "version": "1", "artifact_sha256": "a" * 64,
                "artifact_path": "artifacts/test.zip", "semantic_available": False}
    installed = await library.install_snapshot(snapshot, installation=metadata)
    assert installed["semantic_state"] == "unavailable"
    release = asyncio.Event()

    async def update(context):
        await release.wait()
        return await library.install_snapshot(snapshot, installation=metadata, context=context)

    job = await library.submit_job("install", None, update, frozen_input={"snapshot_id": snapshot["snapshot_id"]})
    await library.delete_library(installed["library_id"])
    release.set()
    assert (await finished(library, job))["reason"] == "library_deleted"
    assert [value["kind"] for value in await library.list_libraries()] == ["local"]


async def test_32_failed_previews_can_be_discarded_after_their_library_is_deleted(library):
    """Failed inputs remain reachable and reclaim global capacity independently of a library."""
    identifier = (await library.create_library("Damaged uploads"))["library_id"]
    jobs = []
    for _ in range(32):
        job = await finished(library, await library.preview_document(identifier, "damaged.txt", b"\0\0\0\1"))
        assert job["state"] == "failed"
        assert job["preview_id"] and not job["input_discarded"]
        assert "frozen_input" not in job
        jobs.append(job)
    assert len(list((library.store.root / "staging/previews").glob("*.json"))) == 32
    await library.delete_library(identifier)
    replacement = (await library.create_library("New library"))["library_id"]
    with pytest.raises(LibraryError, match="preview_capacity"):
        await library.preview_document(replacement, "new.txt", b"Usable text")
    await library.discard_preview(jobs[0]["preview_id"])
    assert (await library.get_job(jobs[0]["job_id"]))["input_discarded"]
    assert (await finished(library, await library.preview_document(replacement, "new.txt", b"Usable text")))["state"] == "completed"


async def test_discarded_input_flags_survive_restart_and_block_retry(tmp_path):
    """A durable discard remains explicit after restart, including older job projections."""
    service = LibraryService(tmp_path)
    await service.start()
    try:
        identifier = (await service.create_library("Discard"))["library_id"]
        job = await finished(service, await service.preview_document(identifier, "bad.txt", b"\0\0\0\1"))
        await service.discard_preview(job["preview_id"])
        saved = json.loads((tmp_path / "jobs" / f"{job['job_id']}.json").read_text())
        assert saved["input_discarded"] is True
    finally:
        await service.close()
    # A pre-contract job need not already contain public projection defaults.
    legacy = deepcopy(saved)
    legacy.pop("preview_id", None)
    legacy.pop("input_discarded", None)
    (tmp_path / "jobs" / f"{job['job_id']}.json").write_text(json.dumps(legacy))
    restarted = LibraryService(tmp_path)
    await restarted.start()
    try:
        restored = await restarted.get_job(job["job_id"])
        assert restored["preview_id"] == job["preview_id"] and restored["input_discarded"]
        with pytest.raises(LibraryError, match="preview_input_discarded"):
            await restarted.retry_job(job["job_id"])
        unrelated = await finished(restarted, await restarted.submit_job("other", None, _empty_runner, frozen_input={}))
        assert unrelated["preview_id"] is None and unrelated["input_discarded"] is False
    finally:
        await restarted.close()


async def _empty_runner(_context):
    """Return a tiny result without borrowing any managed preview input."""
    return {}


async def test_successful_index_consumes_all_related_preview_tasks_without_deadlock(library):
    """The current index runner reclaims input without awaiting its own completion."""
    identifier = (await library.create_library("Consumed"))["library_id"]
    parsed = await finished(library, await library.preview_document(identifier, "notes.txt", b"Kept committed text"))
    preview = await library.get_preview(parsed["preview_id"])
    indexed = await finished(library, await library.confirm_preview(preview["preview_id"], preview["sha256"]))
    for job in (parsed, indexed):
        public = await library.get_job(job["job_id"])
        assert public["preview_id"] == preview["preview_id"] and public["input_discarded"]
    with pytest.raises(LibraryError, match="preview_input_discarded"):
        await library.get_preview(preview["preview_id"])
    assert (await library.get_document(identifier, indexed["result"]["document_id"]))["content"] == "Kept committed text"
    assert list((library.store.root / "staging/previews").glob("*")) == []


@pytest.mark.parametrize("cancel_caller", [False, True])
async def test_discard_retains_real_parser_ownership_and_prevents_retry(tmp_path, cancel_caller):
    """Discard waits for the native parser even when its initiating caller leaves."""
    entered, release = threading.Event(), threading.Event()
    def parser(filename, data, limits):
        entered.set()
        assert release.wait(5)
        return parse_source(filename, data, limits)
    service = LibraryService(tmp_path, parser=parser)
    await service.start()
    discard = None
    try:
        identifier = (await service.create_library("Real worker"))["library_id"]
        job = await service.preview_document(identifier, "notes.txt", b"Owned original")
        assert await asyncio.to_thread(entered.wait, 2)
        context = service.jobs.contexts[job["job_id"]]
        discard = asyncio.create_task(service.discard_preview(job["preview_id"]))
        assert await asyncio.to_thread(context.cancelled.wait, 2)
        assert not discard.done()
        assert (tmp_path / "staging/previews" / f"{job['preview_id']}.original").exists()
        if cancel_caller:
            discard.cancel()
            with pytest.raises(asyncio.CancelledError):
                await discard
        with pytest.raises(LibraryError, match="preview_input_discarded"):
            await service.retry_job(job["job_id"])
        release.set()
        if not cancel_caller:
            await asyncio.wait_for(discard, 3)
        await service.close()  # Shutdown also drains a caller-less owned reclaim.
        assert service.jobs.records[job["job_id"]]["input_discarded"]
        assert list((tmp_path / "staging/previews").glob("*")) == []
    finally:
        release.set()
        if discard is not None and not discard.done():
            await discard
        await service.close()


async def test_install_update_requires_explicit_target_but_keeps_hash_idempotency(library):
    """A new upload cannot implicitly replace a previously installed work."""
    identifier = (await library.create_library("Author"))["library_id"]
    await add_document(library, identifier, "Complete frozen source")
    snapshot = await library.freeze_library(identifier, version="1")
    snapshot = _precomputed_install_snapshot(library, snapshot)
    metadata = {"work_id": snapshot["package_id"], "version": "1", "artifact_sha256": "a" * 64}
    installed = await library.install_snapshot(snapshot, installation=metadata)
    updated = {**snapshot, "version": "2", "name": "Second published version"}
    version2 = {**metadata, "version": "2", "artifact_sha256": "b" * 64}
    with pytest.raises(LibraryError, match="package_update_requires_target"):
        await library.install_snapshot(updated, installation=version2)
    assert (await library.get_library(installed["library_id"]))["library"] == installed
    result = await library.install_snapshot(updated, installation=version2, library_id=installed["library_id"])
    assert result["library_id"] == installed["library_id"]
    assert await library.install_snapshot(updated, installation=version2) == result
    with pytest.raises(LibraryError, match="immutable_version_conflict"):
        await library.install_snapshot(snapshot, installation={**metadata, "artifact_sha256": "c" * 64})


async def test_cancelled_stage_caller_still_registers_a_reachable_preview(library, monkeypatch):
    """Cancelling the initiating await cannot strand an already saved original."""
    identifier = (await library.create_library("Retained admission"))["library_id"]
    entered, release = asyncio.Event(), threading.Event()
    loop, original_stage = asyncio.get_running_loop(), library.store.stage
    def stage(*args):
        preview = original_stage(*args)
        loop.call_soon_threadsafe(entered.set)
        assert release.wait(5)
        return preview
    monkeypatch.setattr(library.store, "stage", stage)
    caller = asyncio.create_task(library.preview_document(identifier, "notes.txt", b"Saved original"))
    try:
        await asyncio.wait_for(entered.wait(), 2)
        caller.cancel()
        done, _ = await asyncio.wait({caller}, timeout=0.05)
        assert done, "HTTP cancellation waited on the stage instead of detaching its owned admission"
        with pytest.raises(asyncio.CancelledError):
            await caller
    finally:
        release.set()
        await asyncio.gather(caller, return_exceptions=True)
        await asyncio.gather(*tuple(getattr(library, "_preview_admissions", ())), return_exceptions=True)
    await library.jobs.queue.join()
    jobs = await library.list_jobs()
    assert len(jobs) == 1 and jobs[0]["preview_id"] and not jobs[0]["input_discarded"]
    await library.discard_preview(jobs[0]["preview_id"])
    assert list((library.store.root / "staging/previews").glob("*")) == []


async def test_preview_submit_failure_reclaims_its_own_staged_input(library, monkeypatch):
    """A rejected durable registration leaves neither unreachable staging nor a job."""
    identifier = (await library.create_library("Failed admission"))["library_id"]
    original_save = library.jobs._save
    def unavailable(_record):
        raise OSError("simulated job persistence failure")
    monkeypatch.setattr(library.jobs, "_save", unavailable)
    with pytest.raises(OSError, match="simulated job persistence failure"):
        await library.preview_document(identifier, "notes.txt", b"Saved before submission")
    assert list((library.store.root / "staging/previews").glob("*")) == []
    assert await library.list_jobs() == []
    monkeypatch.setattr(library.jobs, "_save", original_save)
    assert (await finished(library, await library.preview_document(identifier, "valid.txt", b"New usable original")))["state"] == "completed"


async def test_preview_stage_admissions_are_bounded_before_saving_bytes(library, monkeypatch):
    """Waiting to stage must not create an unbounded parallel admission queue."""
    from dataclasses import replace

    library.limits = replace(library.limits, max_pending_jobs=1)
    identifier = (await library.create_library("Bounded admission"))["library_id"]
    entered, release = asyncio.Event(), threading.Event()
    loop, original_stage = asyncio.get_running_loop(), library.store.stage
    def stage(*args):
        loop.call_soon_threadsafe(entered.set)
        assert release.wait(5)
        return original_stage(*args)
    monkeypatch.setattr(library.store, "stage", stage)
    first = asyncio.create_task(library.preview_document(identifier, "first.txt", b"First original"))
    second = None
    try:
        await asyncio.wait_for(entered.wait(), 2)
        second = asyncio.create_task(library.preview_document(identifier, "second.txt", b"Excess original"))
        done, _ = await asyncio.wait({second}, timeout=0.05)
        assert done, "excess stage admission waited instead of being rejected"
        with pytest.raises(LibraryError, match="jobs_busy"):
            await second
    finally:
        release.set()
        await asyncio.gather(first, *([second] if second is not None else []), return_exceptions=True)
    await library.jobs.queue.join()
    assert len(await library.list_jobs()) == 1


async def test_close_drains_stage_admissions_before_stopping_jobs(library, monkeypatch):
    """Root ownership cannot be released while a saved original still needs registration."""
    identifier = (await library.create_library("Closing admission"))["library_id"]
    entered, release = asyncio.Event(), threading.Event()
    loop, original_stage = asyncio.get_running_loop(), library.store.stage
    def stage(*args):
        preview = original_stage(*args)
        loop.call_soon_threadsafe(entered.set)
        assert release.wait(5)
        return preview
    monkeypatch.setattr(library.store, "stage", stage)
    caller = asyncio.create_task(library.preview_document(identifier, "notes.txt", b"Owned until registered"))
    closing = None
    try:
        await asyncio.wait_for(entered.wait(), 2)
        closing = asyncio.create_task(library.close())
        done, _ = await asyncio.wait({closing}, timeout=0.05)
        assert not done and not library.jobs.closed
        with pytest.raises(LibraryError, match="service_unavailable"):
            await library.preview_document(identifier, "late.txt", b"No late root writes")
    finally:
        release.set()
        await asyncio.gather(caller, *([closing] if closing is not None else []), return_exceptions=True)
    job = caller.result()
    saved = json.loads((library.store.root / "jobs" / f"{job['job_id']}.json").read_text())
    assert saved["preview_id"] == job["preview_id"]
    assert (library.store.root / "staging/previews" / f"{job['preview_id']}.json").is_file()
