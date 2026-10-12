"""Plugin-market subscriptions in the knowledge subsystem (#2951 split, step 4).

Covers the subscription metadata contract, the identity rules (a pack is local
or subscribed for good; one market package installs as one pack), installing
a verified market artifact, identity-checked removal with a pollable
operation outcome, and the routes / proxy allowlists that carry them.
"""

from __future__ import annotations

import asyncio
import hashlib
import json

import httpx
import pytest
from fastapi import FastAPI

import knowledge.service as service_module
from app.memory_server import knowledge_routes
from knowledge.registry import REGISTRY_FILE, KnowledgeRegistryError, load_registry
from knowledge.subscriptions import (
    KnowledgeSubscription,
    SubscriptionError,
    identity_conflict,
    parse_expected_identity,
    parse_subscription,
)
from utils.http import knowledge_proxy


pytestmark = pytest.mark.unit


def _pack(pack_id: str = "market-memes", *, material_type: str = "knowledge", content: str = "A kotatsu is a heated table.") -> dict:
    return {
        "schema_version": 1,
        "pack_id": pack_id,
        "material_type": material_type,
        "source": {"name": "Market", "homepage": "", "license": "CC0-1.0"},
        "entries": [{"title": "Kotatsu", "summary": "A heated table.", "content": content}],
    }


def _raw(payload: dict) -> bytes:
    return json.dumps(payload, ensure_ascii=False).encode("utf-8")


def _subscription(raw: bytes, *, pack_id: str = "market-memes", package_id: str = "42",
                  remote_id: str = "knowledge/market-memes", version: str = "1.0.0",
                  material_type: str = "knowledge") -> KnowledgeSubscription:
    return parse_subscription(
        {
            "provider": "plugin-market",
            "provider_package_id": package_id,
            "remote_id": remote_id,
            "pack_id": pack_id,
            "version": version,
            "channel": "stable",
            "artifact_sha256": hashlib.sha256(raw).hexdigest(),
            "material_type": material_type,
        }
    )


async def _started(root) -> service_module.KnowledgeService:
    service = service_module.KnowledgeService(root)
    await service.start()
    return service


async def _settle(service, result: dict) -> dict:
    assert result["ok"] is True, result
    if result.get("unchanged"):
        return result
    for _ in range(300):
        job = next(job for job in service.list_jobs() if job["job_id"] == result["job_id"])
        if job["state"] not in ("queued", "building"):
            return job
        await asyncio.sleep(0.01)
    raise AssertionError("import job did not finish")


async def _subscribe(service, payload: dict | None = None, **identity) -> dict:
    raw = _raw(payload or _pack())
    return await _settle(service, await service.import_pack(raw, subscription=_subscription(raw, **identity)))


# ── metadata contract ───────────────────────────────────────────────


def test_subscription_requires_exactly_the_known_fields():
    raw = _raw(_pack())
    good = _subscription(raw).to_json()
    for broken in (
        {**good, "trust": "trusted_market"},
        {key: value for key, value in good.items() if key != "remote_id"},
        {**good, "provider_package_id": "042"},
        {**good, "artifact_sha256": "A" * 64},
        {**good, "remote_id": "plugins/x"},
        {**good, "channel": "nightly"},
        {**good, "version": "1.0\n"},
    ):
        with pytest.raises(SubscriptionError):
            parse_subscription(broken)
    with pytest.raises(SubscriptionError) as excinfo:
        parse_subscription({**good, "provider": "somewhere-else"})
    assert excinfo.value.reason == "untrusted_provider"


def test_expected_identity_names_provider_package_and_remote_id():
    assert parse_expected_identity(
        {"provider": "plugin-market", "provider_package_id": "7", "remote_id": "knowledge/a-b"}
    ) == ("plugin-market", "7", "knowledge/a-b")
    with pytest.raises(SubscriptionError):
        parse_expected_identity({"provider": "plugin-market", "provider_package_id": "7"})


def test_identity_rules():
    raw = _raw(_pack())
    sub = _subscription(raw)
    other_package = _subscription(raw, package_id="43")
    assert not identity_conflict("market-memes", sub, {})
    # Local and subscribed never overwrite each other.
    assert identity_conflict("market-memes", sub, {"market-memes": None})
    assert identity_conflict("market-memes", None, {"market-memes": sub})
    # A subscribed pack keeps its package; another pack cannot take it.
    assert identity_conflict("market-memes", other_package, {"market-memes": sub})
    assert identity_conflict("another-pack", _subscription(raw, pack_id="another-pack"), {"market-memes": sub})
    # Same package, new version: allowed.
    assert not identity_conflict("market-memes", _subscription(raw, version="2.0.0"), {"market-memes": sub})
    # ...but not a change of what the pack is used for.
    assert identity_conflict("market-memes", _subscription(raw, material_type="corpus"), {"market-memes": sub})


# ── service ─────────────────────────────────────────────────────────


async def test_subscription_install_is_recorded_and_survives_restart(tmp_path):
    service = await _started(tmp_path)
    try:
        job = await _subscribe(service)
        assert job["state"] == "active"
        assert job["subscription"]["provider_package_id"] == "42"
        (pack,) = await service.list_packs()
        assert pack["subscription"]["remote_id"] == "knowledge/market-memes"
    finally:
        await service.stop()
    registry = json.loads((tmp_path / REGISTRY_FILE).read_text(encoding="utf-8"))
    assert registry["packs"]["market-memes"]["subscription"]["version"] == "1.0.0"
    restarted = await _started(tmp_path)
    try:
        (pack,) = await restarted.list_packs()
        assert pack["subscription"]["provider_package_id"] == "42"
    finally:
        await restarted.stop()


async def test_local_pack_record_has_no_subscription_key(tmp_path):
    service = await _started(tmp_path)
    try:
        await _settle(service, await service.import_pack(_raw(_pack("local-pack"))))
    finally:
        await service.stop()
    registry = json.loads((tmp_path / REGISTRY_FILE).read_text(encoding="utf-8"))
    assert "subscription" not in registry["packs"]["local-pack"]


@pytest.mark.parametrize(
    "identity, reason",
    [
        ({"pack_id": "other-pack"}, "package_identity_mismatch"),
        ({"material_type": "corpus"}, "material_type_mismatch"),
    ],
)
async def test_artifact_must_match_its_subscription(tmp_path, identity, reason):
    service = await _started(tmp_path)
    try:
        raw = _raw(_pack())
        result = await service.import_pack(raw, subscription=_subscription(raw, **identity))
        assert result == {"ok": False, "reason": reason}
        tampered = raw.replace(b"kotatsu", b"KOTATSU")
        result = await service.import_pack(tampered, subscription=_subscription(raw))
        assert result == {"ok": False, "reason": "artifact_hash_mismatch"}
        assert service.list_jobs() == []
    finally:
        await service.stop()


async def test_local_and_subscribed_packs_never_overwrite_each_other(tmp_path):
    service = await _started(tmp_path)
    try:
        await _subscribe(service)
        local = await service.import_pack(_raw(_pack(content="Changed locally.")))
        assert local == {"ok": False, "reason": "subscription_identity_mismatch"}

        await _settle(service, await service.import_pack(_raw(_pack("local-pack"))))
        raw = _raw(_pack("local-pack"))
        result = await service.import_pack(raw, subscription=_subscription(raw, pack_id="local-pack", package_id="9"))
        assert result == {"ok": False, "reason": "subscription_identity_mismatch"}

        # Another pack cannot claim the package already installed.
        raw = _raw(_pack("second-pack"))
        result = await service.import_pack(raw, subscription=_subscription(raw, pack_id="second-pack"))
        assert result == {"ok": False, "reason": "subscription_identity_mismatch"}
    finally:
        await service.stop()


async def test_update_and_republished_metadata_replace_the_record(tmp_path):
    service = await _started(tmp_path)
    try:
        await _subscribe(service)
        same = await service.import_pack(_raw(_pack()), subscription=_subscription(_raw(_pack())))
        assert same["unchanged"] is True

        # Same bytes published as a new version: not "unchanged", the record moves on.
        job = await _subscribe(service, version="1.0.1")
        assert job["state"] == "active"
        (pack,) = await service.list_packs()
        assert pack["subscription"]["version"] == "1.0.1"

        updated = _pack(content="A kotatsu is a low heated table with a blanket.")
        job = await _subscribe(service, updated, version="2.0.0")
        assert job["state"] == "active"
        (pack,) = await service.list_packs()
        assert pack["subscription"]["artifact_sha256"] == hashlib.sha256(_raw(updated)).hexdigest()
    finally:
        await service.stop()


async def test_an_update_cannot_change_the_material_type(tmp_path):
    service = await _started(tmp_path)
    try:
        await _subscribe(service)
        corpus = _pack(material_type="corpus", content="Now a corpus.")
        raw = _raw(corpus)
        result = await service.import_pack(raw, subscription=_subscription(raw, material_type="corpus", version="2.0.0"))
        assert result == {"ok": False, "reason": "subscription_identity_mismatch"}
        (pack,) = await service.list_packs()
        assert pack["subscription"]["material_type"] == "knowledge"
    finally:
        await service.stop()


async def test_identity_is_rechecked_under_the_lock_at_commit(tmp_path, monkeypatch):
    service = await _started(tmp_path)
    try:
        await _subscribe(service)
        # Admission is bypassed; the commit must still refuse the overwrite.
        monkeypatch.setattr(service, "_identity_conflict", lambda *_args: False)
        job = await _settle(service, await service.import_pack(_raw(_pack(content="Local overwrite."))))
        assert job["state"] == "failed"
        assert job["reason"] == "subscription_identity_mismatch"
        (pack,) = await service.list_packs()
        assert pack["subscription"] is not None
    finally:
        await service.stop()


async def test_a_subscription_import_is_visible_before_it_becomes_a_job(tmp_path):
    service = await _started(tmp_path)
    try:
        await service._admission_lock.acquire()  # hold admission before the job exists
        raw = _raw(_pack())
        caller = asyncio.create_task(service.import_pack(raw, subscription=_subscription(raw)))
        local = asyncio.create_task(service.import_pack(_raw(_pack("local-pack"))))
        for _ in range(200):
            # Both admissions started (parsed, waiting on the held lock).
            if len(service._admission_owned) == 2:
                break
            await asyncio.sleep(0.01)
        (incoming,) = service.admitting_subscriptions()  # the local import is not listed
        assert incoming["provider_package_id"] == "42" and service.list_jobs() == []
        # The caller going away does not hide an admission that still runs.
        caller.cancel()
        await asyncio.gather(caller, return_exceptions=True)
        assert service.admitting_subscriptions() == [incoming]
        service._admission_lock.release()
        for _ in range(200):
            if not service.admitting_subscriptions() and len(service.list_jobs()) == 2:
                break
            await asyncio.sleep(0.01)
        assert service.admitting_subscriptions() == []
        assert any(job["subscription"] for job in service.list_jobs())
        assert (await local)["ok"] is True
    finally:
        await service.stop()


async def test_a_refused_subscription_import_leaves_nothing_incoming(tmp_path):
    service = await _started(tmp_path)
    try:
        raw = _raw(_pack())
        result = await service.import_pack(raw, subscription=_subscription(raw, pack_id="other-pack"))
        assert result["ok"] is False
        assert service.admitting_subscriptions() == []
    finally:
        await service.stop()


async def test_queued_install_of_a_package_blocks_another_pack_claiming_it(tmp_path):
    service = await _started(tmp_path)
    try:
        async with service._write_lock:  # keep the first job queued
            raw = _raw(_pack())
            first = await service.import_pack(raw, subscription=_subscription(raw))
            assert first["state"] == "queued"
            other = _raw(_pack("second-pack"))
            result = await service.import_pack(other, subscription=_subscription(other, pack_id="second-pack"))
            assert result == {"ok": False, "reason": "subscription_identity_mismatch"}
        await _settle(service, first)
    finally:
        await service.stop()


def test_registry_refuses_a_subscription_naming_another_pack(tmp_path):
    raw = _raw(_pack())
    record = {
        "pack_sha256": "0" * 64,
        "source": {"name": "x", "homepage": "", "license": ""},
        "declared_material_type": "knowledge",
        "material_type_override": None,
        "entries": 1,
        "chunks": 1,
        "auto_context": False,
        "local_embedding": True,
        "disabled_titles": [],
        "subscription": _subscription(raw, pack_id="someone-else").to_json(),
    }
    (tmp_path / REGISTRY_FILE).write_text(
        json.dumps({"schema_version": 1, "enabled": True, "packs": {"market-memes": record}}), encoding="utf-8"
    )
    with pytest.raises(KnowledgeRegistryError):
        load_registry(tmp_path)


# ── removal ─────────────────────────────────────────────────────────

IDENTITY = ("plugin-market", "42", "knowledge/market-memes")


async def test_removal_with_an_expected_identity_removes_only_that_subscription(tmp_path):
    service = await _started(tmp_path)
    try:
        await _subscribe(service)
        await _settle(service, await service.import_pack(_raw(_pack("local-pack"))))
        for pack_id, expected in (
            ("market-memes", ("plugin-market", "43", "knowledge/market-memes")),
            ("market-memes", ("plugin-market", "42", "knowledge/elsewhere")),
            ("local-pack", IDENTITY),
        ):
            with pytest.raises(service_module.KnowledgeUnavailable) as excinfo:
                await service.remove_pack(pack_id, expected_identity=expected)
            assert excinfo.value.reason == "subscription_identity_mismatch"
        assert {pack["pack_id"] for pack in await service.list_packs()} == {"market-memes", "local-pack"}
        removed = await service.remove_pack("market-memes", expected_identity=IDENTITY)
        assert removed["removed_entries"] == 1
    finally:
        await service.stop()


async def test_removal_outcome_is_tracked_and_never_repeated(tmp_path):
    service = await _started(tmp_path)
    try:
        await _subscribe(service)
        assert service.removal_status("op-never-sent-0000")["operation_status"] == "unknown"
        result = await service.remove_pack("market-memes", expected_identity=IDENTITY, operation_id="op-remove-000001")
        assert result["operation_status"] == "committed"
        status = service.removal_status("op-remove-000001")
        assert status["operation_status"] == "committed" and status["ok"] is True
        # A retry of the same operation answers from the record.
        retry = await service.remove_pack("market-memes", expected_identity=IDENTITY, operation_id="op-remove-000001")
        assert retry["ok"] is True and retry["operation_status"] == "committed"
        # The same id reused for another removal is refused, not answered with this outcome.
        for other in (("another-pack", IDENTITY), ("market-memes", None)):
            with pytest.raises(service_module.KnowledgeUnavailable) as excinfo:
                await service.remove_pack(other[0], expected_identity=other[1], operation_id="op-remove-000001")
            assert excinfo.value.reason == "operation_id_conflict"

        with pytest.raises(service_module.KnowledgeUnavailable):
            await service.remove_pack("market-memes", operation_id="op-remove-000002")
        failed = service.removal_status("op-remove-000002")
        assert failed["operation_status"] == "failed" and failed["reason"] == "not_found"
    finally:
        await service.stop()


async def test_a_tracked_removal_takes_its_place_in_the_order_when_it_arrives(tmp_path):
    """An update that arrives after the unsubscribe is the newer request and must stand."""
    service = await _started(tmp_path)
    try:
        await _subscribe(service)
        await service._write_lock.acquire()  # the removal waits for the lock
        removal = asyncio.create_task(
            service.remove_pack("market-memes", expected_identity=IDENTITY, operation_id="op-order-000000001")
        )
        updated = _raw(_pack(content="A newer note about the kotatsu."))
        importing = asyncio.create_task(
            service.import_pack(updated, subscription=_subscription(updated, version="2.0.0"))
        )
        await asyncio.sleep(0.05)
        service._write_lock.release()
        assert (await removal)["operation_status"] == "committed"
        job = await _settle(service, await importing)
        assert job["state"] == "active"
        (pack,) = await service.list_packs()
        assert pack["subscription"]["version"] == "2.0.0"
    finally:
        await service.stop()


async def test_tracked_removal_settles_even_when_the_caller_stops_waiting(tmp_path):
    service = await _started(tmp_path)
    try:
        await _subscribe(service)
        await service._write_lock.acquire()
        caller = asyncio.create_task(
            service.remove_pack("market-memes", expected_identity=IDENTITY, operation_id="op-detached-0001")
        )
        await asyncio.sleep(0.05)
        assert service.removal_status("op-detached-0001")["operation_status"] == "pending"
        caller.cancel()
        await asyncio.gather(caller, return_exceptions=True)
        service._write_lock.release()
        for _ in range(200):
            if service.removal_status("op-detached-0001")["operation_status"] != "pending":
                break
            await asyncio.sleep(0.01)
        assert service.removal_status("op-detached-0001")["operation_status"] == "committed"
        assert await service.list_packs() == []
    finally:
        await service.stop()


async def test_pending_removals_are_never_evicted(tmp_path, monkeypatch):
    monkeypatch.setattr(service_module, "MAX_REMOVAL_OPERATIONS", 2)
    service = await _started(tmp_path)
    try:
        service._note_removal("pending-operation-1", "pending")
        for index in range(4):
            service._note_removal(f"done-operation-{index:04d}", "failed", {"ok": False, "reason": "not_found"})
        assert service.removal_status("pending-operation-1")["operation_status"] == "pending"
        assert service.removal_status("done-operation-0000")["operation_status"] == "unknown"
    finally:
        await service.stop()


async def test_tracked_removals_are_refused_while_every_slot_is_pending(tmp_path, monkeypatch):
    monkeypatch.setattr(service_module, "MAX_REMOVAL_OPERATIONS", 2)
    service = await _started(tmp_path)
    try:
        await _subscribe(service)
        service._note_removal("pending-operation-1", "pending")
        service._note_removal("pending-operation-2", "pending")
        with pytest.raises(service_module.KnowledgeUnavailable) as excinfo:
            await service.remove_pack("market-memes", operation_id="op-over-capacity-1")
        assert excinfo.value.reason == "knowledge_busy"
        assert service.removal_status("op-over-capacity-1")["operation_status"] == "unknown"
        assert {pack["pack_id"] for pack in await service.list_packs()} == {"market-memes"}

        # Once one has settled there is room: the settled one makes way, the new one is kept.
        service._note_removal("pending-operation-1", "failed", {"ok": False, "reason": "not_found"})
        result = await service.remove_pack("market-memes", operation_id="op-with-room-0001")
        assert result["operation_status"] == "committed"
        assert service.removal_status("op-with-room-0001")["operation_status"] == "committed"
    finally:
        await service.stop()


# ── routes ──────────────────────────────────────────────────────────


@pytest.fixture
async def client(tmp_path, monkeypatch):
    service = await _started(tmp_path)
    monkeypatch.setattr(knowledge_routes, "_service", service)
    app = FastAPI()
    app.include_router(knowledge_routes.router)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as http:
        yield http, service
    await service.stop()


def _apply_form(raw: bytes, subscription: dict, *, protocol: str = "1"):
    return {
        "data": {"protocol_version": protocol, "subscription": json.dumps(subscription)},
        "files": {"pack": ("pack.neko-knowledge.json", raw, "application/json")},
    }


async def test_jobs_route_lists_incoming_subscription_imports(client, monkeypatch):
    http, service = client
    monkeypatch.setattr(service, "admitting_subscriptions", lambda: [{"provider_package_id": "42"}])
    body = (await http.get("/internal/knowledge/packs/jobs")).json()
    assert body["admitting"] == [{"provider_package_id": "42"}] and body["jobs"] == []


async def test_apply_route_installs_a_subscription(client):
    http, service = client
    raw = _raw(_pack())
    response = await http.post("/internal/knowledge/subscriptions/apply", **_apply_form(raw, _subscription(raw).to_json()))
    body = response.json()
    assert body["ok"] is True and body["state"] == "queued"
    job = await _settle(service, body)
    assert job["state"] == "active"


@pytest.mark.parametrize(
    "data, file_field, reason",
    [
        ({"protocol_version": "2"}, "pack", "unsupported_protocol"),
        ({"subscription": "{"}, "pack", "invalid_subscription"),
        ({}, "other", "invalid_request"),
    ],
)
async def test_apply_route_refuses_malformed_requests(client, data, file_field, reason):
    http, service = client
    raw = _raw(_pack())
    form = _apply_form(raw, _subscription(raw).to_json())
    form["data"].update(data)
    form["files"] = {file_field: form["files"]["pack"]}
    response = await http.post("/internal/knowledge/subscriptions/apply", **form)
    assert response.status_code == 400
    assert response.json()["reason"] == reason
    assert service.list_jobs() == []


async def test_apply_route_takes_only_multipart(client):
    http, _service = client
    response = await http.post("/internal/knowledge/subscriptions/apply", json={"pack": {}})
    assert response.status_code == 400


async def test_remove_route_checks_identity_and_reports_status(client):
    http, service = client
    await _subscribe(service)
    expected = {"provider": "plugin-market", "provider_package_id": "42", "remote_id": "knowledge/market-memes"}
    wrong = await http.post(
        "/internal/knowledge/packs/remove",
        json={"pack_id": "market-memes", "expected_subscription": {**expected, "provider_package_id": "1"},
              "operation_id": "route-operation-01"},
    )
    assert wrong.json()["reason"] == "subscription_identity_mismatch"
    removed = await http.post(
        "/internal/knowledge/packs/remove",
        json={"pack_id": "market-memes", "expected_subscription": expected, "operation_id": "route-operation-02"},
    )
    assert removed.json()["ok"] is True and removed.json()["operation_status"] == "committed"
    status = await http.get("/internal/knowledge/packs/remove/status", params={"operation_id": "route-operation-02"})
    assert status.json()["operation_status"] == "committed"
    failed = await http.get("/internal/knowledge/packs/remove/status", params={"operation_id": "route-operation-01"})
    assert failed.json()["operation_status"] == "failed"
    bad = await http.post("/internal/knowledge/packs/remove", json={"pack_id": "x1", "operation_id": "short"})
    assert bad.status_code == 400


# ── proxy allowlists ────────────────────────────────────────────────


def test_apply_is_a_main_only_write():
    assert "subscriptions/apply" in knowledge_proxy.SERVICE_WRITE_PATHS
    assert "subscriptions/apply" not in knowledge_proxy.WRITE_PATHS
    assert "packs/remove/status" in knowledge_proxy.READ_PATHS
