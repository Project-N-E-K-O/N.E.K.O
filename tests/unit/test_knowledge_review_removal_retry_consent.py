"""An old removal projection cannot disclose source versions absent today."""

import asyncio

import httpx
import pytest
from fastapi import FastAPI

from app.memory_server import knowledge_routes
from knowledge.service import KnowledgeService
from tests.unit.test_knowledge_unified_core import _finished


pytestmark = pytest.mark.unit_fast
PRIVATE = "amberlighthousesecret"


async def _document(service, name, content, *, pack_id="", doc_id=""):
    preview = (await service.preview_source(name, content.encode(), pack_id=pack_id, doc_id=doc_id))["preview"]
    queued = await service.commit_source(preview["preview_id"], preview["sha256"])
    assert (await _finished(service, queued))["state"] == "active"
    return queued["pack_id"], preview["doc_id"]


async def _failed_removal(service, doc_id, pack_id, monkeypatch, *, cancelled=False):
    if cancelled:
        # Pause only the consumer; removal admission and cancellation run through
        # their real APIs before the worker sees the cancelled queued record.
        runner = service._tasks[0]
        runner.cancel()
        await asyncio.gather(runner, return_exceptions=True)
        queued = await service.remove_source(pack_id, doc_id)
        assert await service.cancel_job(queued["job_id"])
        service._tasks[0] = asyncio.create_task(service._job_loop(), name="knowledge-import-jobs")
    else:
        replace = service._store.replace_pack
        failed = False

        def fail_once(*args, **kwargs):
            nonlocal failed
            if not failed:
                failed = True
                raise OSError("injected index write failure")
            return replace(*args, **kwargs)

        with monkeypatch.context() as patch:
            patch.setattr(service._store, "replace_pack", fail_once)
            queued = await service.remove_source(pack_id, doc_id)
            assert (await _finished(service, queued))["state"] == "failed"
    assert (await _finished(service, queued))["state"] == ("cancelled" if cancelled else "failed")
    return queued


@pytest.mark.parametrize("cancelled", [False, True])
@pytest.mark.parametrize("restart", [False, True])
async def test_old_removal_retry_cannot_restore_private_sources_into_an_enabled_pack(tmp_path, monkeypatch, cancelled, restart):
    service = KnowledgeService(tmp_path)
    await service.start()
    try:
        pack_id, private = await _document(service, "private.txt", PRIVATE + " is an unapproved private reference.")
        _, public = await _document(service, "public.txt", "Ordinary public reference.", pack_id=pack_id)
        old = await _failed_removal(service, public, pack_id, monkeypatch, cancelled=cancelled)
        assert (await _finished(service, await service.remove_source(pack_id, private)))["state"] == "active"
        await service.set_pack_enabled(pack_id, True)
        await service.set_pack_auto_context(pack_id, True)
        # These are real legacy-compatible removal jobs: their frozen payload
        # has no source-confirmation marker, including after startup reload.
        assert service._jobs[old["job_id"]].frozen_input == {}
        if restart:
            await service.stop()
            service = KnowledgeService(tmp_path)
            await service.start()
        before = service._registry.packs[pack_id]
        sources = {path: path.read_bytes() for path in (tmp_path / "sources").rglob("*") if path.is_file()}
        assert (await service.query(query=PRIVATE))["result"] == "miss"
        monkeypatch.setattr(knowledge_routes, "_service", service)
        app = FastAPI()
        app.include_router(knowledge_routes.router)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as client:
            response = await client.post("/internal/knowledge/packs/jobs/retry", json={"job_id": old["job_id"]})
        assert response.status_code == 200 and response.json()["ok"]
        outcome = await _finished(service, response.json())
        assert (outcome["state"], outcome["reason"]) == ("failed", "source_confirmation_required")
        assert service._registry.packs[pack_id] == before
        assert (await service.query(query=PRIVATE))["result"] == "miss"
        assert all(path.read_bytes() == raw for path, raw in sources.items())
        assert (tmp_path / "jobs" / (old["job_id"] + ".pack.json")).is_file()
        # Retrying after explicitly disabling the pack may recover the frozen
        # selection; that is separate from authorizing it for conversations.
        await service.set_pack_enabled(pack_id, False)
        assert (await _finished(service, await service.retry_job(old["job_id"])))["state"] == "active"
        assert not service._registry.packs[pack_id].enabled
        assert (await service.query(query=PRIVATE))["result"] == "miss"
    finally:
        await service.stop()


async def test_enabled_pack_can_remove_sources_without_expanding_disclosure(tmp_path):
    service = KnowledgeService(tmp_path)
    await service.start()
    try:
        pack_id, first = await _document(service, "a.txt", "Lighthouse reference A.")
        _, second = await _document(service, "b.txt", "Garden reference B.", pack_id=pack_id)
        await service.set_pack_enabled(pack_id, True)
        await service.set_pack_auto_context(pack_id, True)
        removed = await _finished(service, await service.remove_source(pack_id, first))
        assert removed["state"] == "active"
        assert service._registry.packs[pack_id].enabled and service._registry.packs[pack_id].auto_context
        selected = {row["doc_id"] for row in (await service.list_sources(pack_id))["sources"] if not row["removed"]}
        assert selected == {second}
        assert (await service.query(query="garden"))["result"] == "matched"
        assert (await service.query(query="lighthouse"))["result"] == "miss"
    finally:
        await service.stop()


@pytest.mark.parametrize("enabled", [False, True])
async def test_removal_retry_rechecks_policy_after_queue_admission(tmp_path, monkeypatch, enabled):
    service = KnowledgeService(tmp_path)
    await service.start()
    release = asyncio.Event()
    try:
        pack_id, private = await _document(service, "private.txt", PRIVATE + " is private.")
        _, public = await _document(service, "public.txt", "Ordinary public reference.", pack_id=pack_id)
        old = await _failed_removal(service, public, pack_id, monkeypatch)
        assert (await _finished(service, await service.remove_source(pack_id, private)))["state"] == "active"
        runner = service._tasks[0]
        runner.cancel()
        await asyncio.gather(runner, return_exceptions=True)

        async def consume_later():
            await release.wait()
            await service._job_loop()

        service._tasks[0] = asyncio.create_task(consume_later(), name="knowledge-import-jobs")
        queued = await service.retry_job(old["job_id"])
        assert queued["state"] == "queued"
        # Policy must be evaluated under the activation lock, not when retry
        # reads its frozen input or reserves admission against the disabled pack.
        await service.set_pack_enabled(pack_id, enabled)
        before = service._registry.packs[pack_id]
        release.set()
        outcome = await _finished(service, queued)
        if enabled:
            assert (outcome["state"], outcome["reason"]) == ("failed", "source_confirmation_required")
            assert service._registry.packs[pack_id] == before
        else:
            assert outcome["state"] == "active" and not service._registry.packs[pack_id].enabled
        assert (await service.query(query=PRIVATE))["result"] == "miss"
    finally:
        release.set()
        await service.stop()


async def test_removal_retry_cannot_replace_the_current_version_of_the_same_document(tmp_path, monkeypatch):
    service = KnowledgeService(tmp_path)
    await service.start()
    try:
        pack_id, private = await _document(service, "private.txt", PRIVATE + " is the old private version.")
        _, public = await _document(service, "public.txt", "Ordinary public reference.", pack_id=pack_id)
        old = await _failed_removal(service, public, pack_id, monkeypatch)
        await _document(service, "private.txt", "A new approved lighthouse reference.", pack_id=pack_id, doc_id=private)
        await service.set_pack_enabled(pack_id, True)
        before = service._registry.packs[pack_id]
        outcome = await _finished(service, await service.retry_job(old["job_id"]))
        assert (outcome["state"], outcome["reason"]) == ("failed", "source_confirmation_required")
        assert service._registry.packs[pack_id] == before
        assert (await service.query(query=PRIVATE))["result"] == "miss"
    finally:
        await service.stop()


async def test_removal_retry_can_still_reduce_an_unchanged_enabled_selection(tmp_path, monkeypatch):
    service = KnowledgeService(tmp_path)
    await service.start()
    try:
        pack_id, first = await _document(service, "a.txt", "Lighthouse reference A.")
        _, second = await _document(service, "b.txt", "Garden reference B.", pack_id=pack_id)
        old = await _failed_removal(service, second, pack_id, monkeypatch)
        await service.set_pack_enabled(pack_id, True)
        outcome = await _finished(service, await service.retry_job(old["job_id"]))
        assert outcome["state"] == "active" and service._registry.packs[pack_id].enabled
        selected = {row["doc_id"] for row in (await service.list_sources(pack_id))["sources"] if not row["removed"]}
        assert selected == {first}
        assert (await service.query(query="lighthouse"))["result"] == "matched"
    finally:
        await service.stop()


async def test_same_digest_source_repair_does_not_need_fresh_disclosure(tmp_path):
    service = KnowledgeService(tmp_path)
    await service.start()
    try:
        pack_id, doc_id = await _document(service, "a.txt", "Lighthouse reference A.")
        await service.set_pack_enabled(pack_id, True)
        before = service._registry.packs[pack_id]
        frozen = [service._sources.version(pack_id, doc_id, 1)]
        (tmp_path / "packs" / before.file_name).unlink()
        outcome = await _finished(service, await service._project_sources(pack_id, frozen[0]["pack_name"], frozen))
        assert outcome["state"] == "active"
        assert service._registry.packs[pack_id].pack_sha256 == before.pack_sha256
        assert service._registry.packs[pack_id].enabled
        assert (await service.query(query="lighthouse"))["result"] == "matched"
    finally:
        await service.stop()
