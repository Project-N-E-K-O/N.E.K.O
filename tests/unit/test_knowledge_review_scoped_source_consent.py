"""A source acknowledgement covers only this task's document version."""

import asyncio
import json

import httpx
import pytest
from fastapi import FastAPI

from app.memory_server import knowledge_routes
from knowledge.service import KnowledgeService
from tests.unit.test_knowledge_review_removal_retry_consent import _document
from tests.unit.test_knowledge_unified_core import _finished


pytestmark = pytest.mark.unit_fast
PRIVATE = "amberlighthousesecret"
TARGET = "violetgardentarget"


async def _failed_change(service, change, monkeypatch, cancelled):
    if cancelled:
        runner = service._tasks[0]
        runner.cancel()
        await asyncio.gather(runner, return_exceptions=True)
        queued = await change()
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
            queued = await change()
            assert (await _finished(service, queued))["state"] == "failed"
    assert (await _finished(service, queued))["state"] == ("cancelled" if cancelled else "failed")
    return queued


async def _prepared_change(service, monkeypatch, operation, *, cancelled=False, initial_enabled=False):
    pack_id, private = await _document(service, "private.txt", PRIVATE + " is private reference X.")
    await _document(service, "public.txt", "Ordinary public reference Y.", pack_id=pack_id)
    if operation == "restore":
        _, target = await _document(service, "target.txt", TARGET + " is reference Z.", pack_id=pack_id)
        assert (await _finished(service, await service.remove_source(pack_id, target)))["state"] == "active"

        async def change():
            return await service.restore_source(pack_id, target, 1, enabled_confirmed=True)
    else:
        preview = (await service.preview_source("target.txt", (TARGET + " is reference Z.").encode(), pack_id=pack_id))["preview"]
        target = preview["doc_id"]

        async def change():
            return await service.commit_source(preview["preview_id"], preview["sha256"],
                                               enabled=initial_enabled, enabled_confirmed=True)
    await service.set_pack_enabled(pack_id, True)
    await service.set_pack_auto_context(pack_id, True)
    queued = await _failed_change(service, change, monkeypatch, cancelled)
    return pack_id, private, target, queued


@pytest.mark.parametrize("operation", ["append", "restore"])
@pytest.mark.parametrize("cancelled", [False, True])
@pytest.mark.parametrize("restart", [False, True])
async def test_confirmed_retry_does_not_restore_other_sources_deleted_after_confirmation(
    tmp_path, monkeypatch, operation, cancelled, restart,
):
    service = KnowledgeService(tmp_path)
    await service.start()
    try:
        pack_id, private, target, queued = await _prepared_change(service, monkeypatch, operation, cancelled=cancelled)
        assert (await _finished(service, await service.remove_source(pack_id, private)))["state"] == "active"
        assert (await service.query(query=PRIVATE))["result"] == "miss"
        if restart:
            await service.stop()
            service = KnowledgeService(tmp_path)
            await service.start()
        before = service._registry.packs[pack_id]
        sources = {path: path.read_bytes() for path in (tmp_path / "sources").rglob("*") if path.is_file()}
        frozen = tmp_path / "jobs" / (queued["job_id"] + ".pack.json")
        frozen_bytes = frozen.read_bytes()
        monkeypatch.setattr(knowledge_routes, "_service", service)
        app = FastAPI()
        app.include_router(knowledge_routes.router)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as http:
            response = await http.post("/internal/knowledge/packs/jobs/retry", json={"job_id": queued["job_id"]})
        assert response.status_code == 200 and response.json()["ok"]
        outcome = await _finished(service, response.json())
        assert (outcome["state"], outcome["reason"]) == ("failed", "source_confirmation_required")
        assert service._registry.packs[pack_id] == before
        assert (await service.query(query=PRIVATE))["result"] == "miss"
        assert (await service.query(query=TARGET))["result"] == "miss"
        assert all(path.read_bytes() == raw for path, raw in sources.items())
        assert frozen.read_bytes() == frozen_bytes
        saved = json.loads((tmp_path / "jobs" / (queued["job_id"] + ".json")).read_text())["frozen_input"]
        assert saved["source_target"] == {"doc_id": target, "version": 1}
        # Restoring a frozen selection while disabled remains possible, without
        # authorizing its old contents for retrieval or automatic context.
        await service.set_pack_enabled(pack_id, False)
        assert (await _finished(service, await service.retry_job(queued["job_id"])))["state"] == "active"
        assert not service._registry.packs[pack_id].enabled
        assert (await service.query(query=PRIVATE))["result"] == "miss"
    finally:
        await service.stop()


@pytest.mark.parametrize("operation", ["append", "restore"])
@pytest.mark.parametrize("legacy", [False, True])
async def test_confirmed_retry_only_authorizes_its_target_version(tmp_path, monkeypatch, operation, legacy):
    service = KnowledgeService(tmp_path)
    await service.start()
    try:
        pack_id, _private, target, queued = await _prepared_change(service, monkeypatch, operation)
        if legacy:
            # Represent a previously persisted confirmation without the new
            # scope field; no task or registry migration may fabricate consent.
            old = service._jobs[queued["job_id"]]
            old.frozen_input.pop("source_target", None)
            await service._persist_job(old)
            await service.stop()
            service = KnowledgeService(tmp_path)
            await service.start()
            loaded = service._jobs[queued["job_id"]].frozen_input
            assert loaded["enabled_confirmed"] is True and "source_target" not in loaded
        outcome = await _finished(service, await service.retry_job(queued["job_id"]))
        if legacy:
            assert (outcome["state"], outcome["reason"]) == ("failed", "source_confirmation_required")
            assert (await service.query(query=TARGET))["result"] == "miss"
        else:
            assert outcome["state"] == "active" and service._registry.packs[pack_id].enabled
            assert (await service.query(query=TARGET))["result"] == "matched"
            assert (await service.source_versions(pack_id, target))["versions"][0]["current"]
    finally:
        await service.stop()


@pytest.mark.parametrize("legacy,initial_enabled", [(False, True), (True, True), (True, None)])
async def test_retry_cannot_reenable_a_removed_pack_with_unconfirmed_other_versions(tmp_path, monkeypatch, legacy, initial_enabled):
    service = KnowledgeService(tmp_path)
    await service.start()
    try:
        pack_id, _private, _target, queued = await _prepared_change(service, monkeypatch, "append", initial_enabled=True)
        assert service._jobs[queued["job_id"]].initial_enabled is True
        if legacy:
            service._jobs[queued["job_id"]].frozen_input.pop("source_target", None)
            # Older missing initial_enabled values mean the registry's normal
            # enabled default, so absence is not a reason to skip the scope check.
            service._jobs[queued["job_id"]].initial_enabled = initial_enabled
        await service.remove_pack(pack_id)
        assert pack_id not in service._registry.packs
        outcome = await _finished(service, await service.retry_job(queued["job_id"]))
        assert (outcome["state"], outcome["reason"]) == ("failed", "source_confirmation_required")
        assert pack_id not in service._registry.packs
        assert (await service.query(query=PRIVATE))["result"] == "miss"
    finally:
        await service.stop()


async def test_confirmed_retry_cannot_restore_an_older_version_of_another_source(tmp_path, monkeypatch):
    service = KnowledgeService(tmp_path)
    await service.start()
    try:
        pack_id, private, _target, queued = await _prepared_change(service, monkeypatch, "append")
        changed = (await service.preview_source("private.txt", b"A new public version of reference X.",
                                                pack_id=pack_id, doc_id=private))["preview"]
        assert (await _finished(service, await service.commit_source(changed["preview_id"], changed["sha256"],
                                                                     enabled_confirmed=True)))["state"] == "active"
        before = service._registry.packs[pack_id]
        assert (await service.query(query=PRIVATE))["result"] == "miss"
        outcome = await _finished(service, await service.retry_job(queued["job_id"]))
        assert (outcome["state"], outcome["reason"]) == ("failed", "source_confirmation_required")
        assert service._registry.packs[pack_id] == before
        assert (await service.query(query=PRIVATE))["result"] == "miss"
        versions = (await service.source_versions(pack_id, private))["versions"]
        assert [row["version"] for row in versions if row["current"]] == [2]
    finally:
        await service.stop()


@pytest.mark.parametrize("confirmed", [False, True])
async def test_new_enabled_source_pack_requires_and_uses_its_own_confirmation(tmp_path, confirmed):
    service = KnowledgeService(tmp_path)
    await service.start()
    try:
        preview = (await service.preview_source("target.txt", (TARGET + " is reference Z.").encode()))["preview"]
        queued = await service.commit_source(preview["preview_id"], preview["sha256"], enabled=True, enabled_confirmed=confirmed)
        outcome = await _finished(service, queued)
        assert outcome["state"] == ("active" if confirmed else "failed")
        assert (await service.query(query=TARGET))["result"] == ("matched" if confirmed else "miss")
    finally:
        await service.stop()
