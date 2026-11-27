"""Policy responses and listings identify the registry publication they saw."""

import asyncio
import json
import threading
from dataclasses import replace

import httpx
import pytest
from fastapi import FastAPI

from app.memory_server import knowledge_routes
from knowledge import service as module
from knowledge.service import KnowledgeService
from tests.unit.test_knowledge_subsystem import _import, _pack


pytestmark = pytest.mark.unit_fast
PACK_ID = "policy.reference"


@pytest.fixture
async def client(tmp_path, monkeypatch):
    service = KnowledgeService(tmp_path)
    await service.start()
    await _import(service, _pack(PACK_ID))
    await service.set_pack_enabled(PACK_ID, False)
    monkeypatch.setattr(knowledge_routes, "_service", service)
    app = FastAPI()
    app.include_router(knowledge_routes.router)
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as http:
            yield http, service
    finally:
        await service.stop()


async def _row(http):
    response = await http.get("/internal/knowledge/packs")
    assert response.status_code == 200 and response.json()["ok"]
    return next(row for row in response.json()["packs"] if row["pack_id"] == PACK_ID)


async def _toggle(http, enabled, digest):
    payload = {"pack_id": PACK_ID, "enabled": enabled}
    if enabled:
        payload["expected_pack_sha256"] = digest
    response = await http.post("/internal/knowledge/packs/enabled", json=payload)
    assert response.status_code == 200 and response.json()["ok"]
    return response.json()


async def test_same_body_policy_revisions_do_not_depend_on_wall_clock(client, monkeypatch):
    http, service = client
    monkeypatch.setattr(module, "utc_now", lambda: "2026-11-23T00:00:00Z")
    first = await _row(http)
    observed = [first["policy_revision"]]
    for enabled in (True, False, True, True):
        saved = await _toggle(http, enabled, first["pack_sha256"])
        row = await _row(http)
        assert row["pack_sha256"] == first["pack_sha256"]
        assert row["enabled"] is saved["pack_enabled"] is enabled
        assert row["policy_revision"] == saved["policy_revision"]
        assert row["updated_at"] == "2026-11-23T00:00:00Z"
        observed.append(saved["policy_revision"])
    assert len({stamp["epoch"] for stamp in observed}) == 1
    assert all(left["counter"] < right["counter"] for left, right in zip(observed, observed[1:]))
    assert (await _row(http))["policy_revision"] == observed[-1]
    # Publications for other policies also make an older row response stale;
    # this sequence is in-memory metadata, never a registry schema migration.
    await service.set_pack_auto_context(PACK_ID, True)
    after = (await _row(http))["policy_revision"]
    assert after["epoch"] == observed[-1]["epoch"] and after["counter"] > observed[-1]["counter"]
    registry = json.loads((service.root / "registry.json").read_text())
    assert "policy_revision" not in registry and "policy_revision" not in registry["packs"][PACK_ID]


async def test_delayed_response_keeps_its_own_revision_after_the_lock_is_released(client, monkeypatch):
    http, service = client
    digest = (await _row(http))["pack_sha256"]
    locked = service._locked
    entered, release = asyncio.Event(), asyncio.Event()
    pause_next = True

    async def held_response(*args, **kwargs):
        nonlocal pause_next
        result = await locked(*args, **kwargs)
        if pause_next:
            pause_next = False
            entered.set()
            await release.wait()
        return result

    monkeypatch.setattr(service, "_locked", held_response)
    pending = asyncio.create_task(_toggle(http, False, digest))
    try:
        await asyncio.wait_for(entered.wait(), 3)
        # A's registry update finished; B may change the same body before A's
        # outer response resumes. A must not borrow B's publication number.
        newer = await _toggle(http, True, digest)
        current = await _row(http)
        release.set()
        older = await pending
        assert older["pack_enabled"] is False and newer["pack_enabled"] is True
        assert older["policy_revision"]["epoch"] == newer["policy_revision"]["epoch"]
        assert older["policy_revision"]["counter"] < newer["policy_revision"]["counter"]
        assert current["enabled"] is True and current["policy_revision"] == newer["policy_revision"]
    finally:
        release.set()
        await asyncio.gather(pending, return_exceptions=True)


async def test_listing_never_pairs_a_new_registry_with_an_old_publication_counter(client, monkeypatch):
    http, service = client
    before = await _row(http)
    registry, counter = service._registry_snapshot
    changed = registry.with_pack(replace(registry.packs[PACK_ID], enabled=True))
    entered, release = threading.Event(), threading.Event()
    set_attribute = KnowledgeService.__setattr__

    def publish_paused(instance, name, value):
        set_attribute(instance, name, value)
        if instance is service and name == "_registry" and value is changed:
            entered.set()
            assert release.wait(3), "publication was not released"

    monkeypatch.setattr(KnowledgeService, "__setattr__", publish_paused)
    publishing = asyncio.create_task(asyncio.to_thread(service._publish_registry, changed))
    try:
        assert await asyncio.to_thread(entered.wait, 3)
        # Worker publication has replaced _registry but not its immutable
        # pair yet. A listing must take both policy and number from one pair.
        during = await _row(http)
        assert during["enabled"] is before["enabled"] is False
        assert during["policy_revision"] == before["policy_revision"]
        release.set()
        await publishing
        after = await _row(http)
        assert after["enabled"] is True
        assert after["policy_revision"]["counter"] == counter + 1
    finally:
        release.set()
        await asyncio.gather(publishing, return_exceptions=True)


async def test_registry_reload_keeps_order_but_a_new_service_gets_a_new_epoch(client):
    http, service = client
    before = await _row(http)
    # The real startup loader is also a registry publication. Reusing the
    # service instance must not reset its order to an earlier counter.
    await service._locked(lambda: service._thread(service._open_blocking))
    reloaded = await _row(http)
    assert reloaded["policy_revision"]["epoch"] == before["policy_revision"]["epoch"]
    assert reloaded["policy_revision"]["counter"] > before["policy_revision"]["counter"]
    await service.stop()
    reopened = KnowledgeService(service.root)
    await reopened.start()
    try:
        current = next(row for row in await reopened.list_packs() if row["pack_id"] == PACK_ID)
        assert current["pack_sha256"] == before["pack_sha256"]
        assert current["policy_revision"]["epoch"] != reloaded["policy_revision"]["epoch"]
        assert current["policy_revision"]["counter"] > 0
    finally:
        await reopened.stop()
