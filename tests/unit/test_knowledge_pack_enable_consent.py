"""Enable acknowledgements refer to the pack body the user saw."""

import asyncio
import json

import httpx
import pytest
from fastapi import FastAPI

from app.memory_server import knowledge_routes
from knowledge.service import KnowledgeService, KnowledgeUnavailable
from tests.knowledge_fixtures import _finished


pytestmark = pytest.mark.unit_fast
PACK_ID = "consent.reference"
PRIVATE = "amberlighthousesecret"


@pytest.fixture
async def client(tmp_path, monkeypatch):
    service = KnowledgeService(tmp_path)
    await service.start()
    monkeypatch.setattr(knowledge_routes, "_service", service)
    app = FastAPI()
    app.include_router(knowledge_routes.router)
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as http:
            yield http, service
    finally:
        await service.stop()


async def _install(http, service, content):
    payload = {
        "schema_version": 1, "pack_id": PACK_ID, "material_type": "knowledge",
        "source": {"name": "Reference", "homepage": "", "license": "CC0"},
        "entries": [{"title": "Notes", "summary": "Reference notes", "content": content}],
    }
    response = await http.post("/internal/knowledge/packs/import", content=json.dumps(payload),
                               headers={"Content-Type": "application/json"})
    queued = response.json()
    assert response.status_code == 200 and queued["ok"]
    assert (await _finished(service, queued))["state"] == "active"
    return service._registry.packs[PACK_ID]


async def test_stale_enable_confirmation_cannot_disclose_a_replacement_body(client):
    http, service = client
    original = await _install(http, service, "Ordinary public reference.")
    before = (await http.get("/internal/knowledge/packs")).json()["packs"][0]
    assert not before["enabled"]
    # Window A's disclosure is still open when window B finishes a real local
    # import with the same ID. The replacement must keep its own opt-in gate.
    replacement = await _install(http, service, PRIVATE + " is a new private reference.")
    assert replacement.pack_sha256 != original.pack_sha256
    assert not replacement.enabled and not replacement.auto_context
    assert (await service.query(query=PRIVATE))["result"] == "miss"
    stale = await http.post("/internal/knowledge/packs/enabled", json={
        "pack_id": PACK_ID, "enabled": True, "expected_pack_sha256": original.pack_sha256,
    })
    assert stale.status_code == 200
    assert stale.json()["ok"] is False and stale.json()["reason"] == "pack_content_changed"
    assert service._registry.packs[PACK_ID] == replacement
    assert (await service.query(query=PRIVATE))["result"] == "miss"
    current = (await http.get("/internal/knowledge/packs")).json()["packs"][0]
    assert current["pack_sha256"] == replacement.pack_sha256
    confirmed = await http.post("/internal/knowledge/packs/enabled", json={
        "pack_id": PACK_ID, "enabled": True, "expected_pack_sha256": current["pack_sha256"],
    })
    assert confirmed.json()["ok"] and confirmed.json()["pack_enabled"] is True
    assert (await service.query(query=PRIVATE))["result"] == "matched"


@pytest.mark.parametrize("options", [
    {}, {"expected_pack_sha256": None}, {"expected_pack_sha256": True},
    {"expected_pack_sha256": []}, {"expected_pack_sha256": ""},
    {"expected_pack_sha256": "A" * 64}, {"expected_pack_sha256": "a" * 63},
    {"expected_pack_sha256": "a" * 65}, {"expected_pack_sha256": "g" * 64},
])
async def test_http_enable_requires_a_real_lowercase_sha256(client, options):
    http, _service = client
    response = await http.post("/internal/knowledge/packs/enabled", json={
        "pack_id": PACK_ID, "enabled": True, **options,
    })
    assert response.status_code == 400
    assert response.json()["reason"] == "invalid_request"


async def test_matching_body_and_digest_free_disable_preserve_other_policy(client):
    http, service = client
    original = await _install(http, service, "Ordinary public reference.")
    await service.set_pack_auto_context(PACK_ID, True)
    before = service._registry.packs[PACK_ID]
    # Existing trusted internal callers retain their direct-toggle behavior.
    assert (await service.set_pack_enabled(PACK_ID, True))["pack_enabled"] is True
    for enabled in (False, True, True, False):
        payload = {"pack_id": PACK_ID, "enabled": enabled}
        if enabled:
            payload["expected_pack_sha256"] = original.pack_sha256
        response = await http.post("/internal/knowledge/packs/enabled", json=payload)
        assert response.status_code == 200 and response.json()["pack_enabled"] is enabled
        record = service._registry.packs[PACK_ID]
        assert record.pack_sha256 == original.pack_sha256
        assert record.auto_context == before.auto_context and record.local_embedding == before.local_embedding
        assert service._registry.enabled is True


@pytest.mark.parametrize("enabled", [False, True])
async def test_valid_confirmation_keeps_the_missing_pack_reason(client, enabled):
    http, _service = client
    response = await http.post("/internal/knowledge/packs/enabled", json={
        "pack_id": PACK_ID, "enabled": enabled, "expected_pack_sha256": "a" * 64,
    })
    assert response.json()["ok"] is False and response.json()["reason"] == "not_found"


async def test_body_is_checked_after_an_import_releases_the_mutation_lock(client, monkeypatch):
    http, service = client
    original = await _install(http, service, "Ordinary public reference.")
    entered, release, waiting = asyncio.Event(), asyncio.Event(), asyncio.Event()
    commit = service._commit_job

    async def delayed_commit(job):
        entered.set()
        await release.wait()
        await commit(job)

    monkeypatch.setattr(service, "_commit_job", delayed_commit)
    replacing = asyncio.create_task(_install(http, service, PRIVATE + " is the replacement."))
    enabling = None
    try:
        await asyncio.wait_for(entered.wait(), 3)
        acquire = service._write_lock.acquire

        async def observed_acquire():
            waiting.set()
            return await acquire()

        monkeypatch.setattr(service._write_lock, "acquire", observed_acquire)
        enabling = asyncio.create_task(service.set_pack_enabled(
            PACK_ID, True, expected_pack_sha256=original.pack_sha256,
        ))
        # Wait for the real lock acquisition, not a scheduling sleep: checking
        # the old registry before this wait would allow the replacement through.
        await asyncio.wait_for(waiting.wait(), 3)
        assert service._write_lock.locked() and not enabling.done()
        release.set()
        replacement = await replacing
        with pytest.raises(KnowledgeUnavailable, match="pack_content_changed"):
            await enabling
        assert service._registry.packs[PACK_ID] == replacement and not replacement.enabled
        assert (await service.query(query=PRIVATE))["result"] == "miss"
    finally:
        release.set()
        await asyncio.gather(*(task for task in (replacing, enabling) if task is not None), return_exceptions=True)
