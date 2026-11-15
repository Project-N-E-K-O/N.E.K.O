"""Plugin-market subscriptions in the knowledge subsystem (#2951 split, step 4).

Covers the subscription metadata contract, the identity rules (a pack is local
or subscribed for good; one market package installs as one pack), installing
a verified market artifact, the unsubscribe that settles a package's imports
and installed pack in one request (safe to repeat, naming the releases it
removes), the state the market page reconciles against, and the routes /
proxy allowlists that carry them.
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
    parse_releases,
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
        (incoming,) = _imports(service, "admitting")  # the local import is not listed
        assert incoming["subscription"]["provider_package_id"] == "42" and service.list_jobs() == []
        # The caller going away does not hide an admission that still runs.
        caller.cancel()
        await asyncio.gather(caller, return_exceptions=True)
        assert _imports(service, "admitting") == [incoming]
        service._admission_lock.release()
        for _ in range(200):
            if not _imports(service, "admitting") and len(service.list_jobs()) == 2:
                break
            await asyncio.sleep(0.01)
        assert _imports(service, "admitting") == []
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
        assert _imports(service) == []
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


def test_registry_reads_a_subscription_with_fields_it_does_not_know(tmp_path):
    """A registry written by a later version stays readable; an apply stays strict."""
    raw = _raw(_pack())
    subscription = {**_subscription(raw).to_json(), "added_later": "x"}
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
        "subscription": subscription,
    }
    (tmp_path / REGISTRY_FILE).write_text(
        json.dumps({"schema_version": 1, "enabled": True, "packs": {"market-memes": record}}), encoding="utf-8"
    )
    loaded = load_registry(tmp_path).packs["market-memes"].subscription
    assert loaded == _subscription(raw)
    with pytest.raises(SubscriptionError):
        parse_subscription(subscription)
    # A missing field is still a broken record.
    del record["subscription"]["remote_id"]
    (tmp_path / REGISTRY_FILE).write_text(
        json.dumps({"schema_version": 1, "enabled": True, "packs": {"market-memes": record}}), encoding="utf-8"
    )
    with pytest.raises(KnowledgeRegistryError):
        load_registry(tmp_path)


def test_plugin_server_copies_of_the_subscription_rules_agree():
    """The plugin server does not import ``knowledge``; its copies must not drift."""
    import re

    from knowledge import models, subscriptions
    from plugin.server.routes import knowledge_market as km

    assert km.MARKET_PROVIDER == subscriptions.MARKET_PROVIDER
    assert km.SUBSCRIPTION_PROTOCOL_VERSION == subscriptions.SUBSCRIPTION_PROTOCOL_VERSION
    assert km.MAX_ARTIFACT_BYTES == models.MAX_PACK_BYTES
    assert km._PACK_ID_PATTERN == models._PACK_ID_RE.pattern
    assert km._VERSION_PATTERN == subscriptions._VERSION_RE.pattern
    assert km._SHA256_RE.pattern == subscriptions._SHA256_RE.pattern
    remote_id = km.KnowledgeVersionDescriptor.model_fields["remote_id"].metadata
    assert any(getattr(item, "pattern", None) == subscriptions._REMOTE_ID_RE.pattern for item in remote_id)
    assert km._PACKAGE_ID_MAX == int("9" * 19)
    assert re.fullmatch(subscriptions._PACKAGE_ID_RE, str(km._PACKAGE_ID_MAX))
    channel = km.KnowledgeSubscribeRequest.model_fields["channel"].annotation
    assert set(channel.__args__) == set(subscriptions.CHANNELS)
    material = km.KnowledgeVersionDescriptor.model_fields["material_type"].annotation
    assert set(material.__args__) == set(models.MATERIAL_TYPES)


# ── unsubscribe ─────────────────────────────────────────────────────

IDENTITY = ("plugin-market", "42", "knowledge/market-memes")


def _release(raw: bytes, version: str = "1.0.0") -> tuple[str, str]:
    return (version, hashlib.sha256(raw).hexdigest())


def _imports(service, state: str | None = None) -> list[dict]:
    items = service.subscription_state()["imports"]
    return [item for item in items if state is None or item["state"] == state]


async def _wait_for(predicate, message: str) -> None:
    for _ in range(300):
        if predicate():
            return
        await asyncio.sleep(0.01)
    raise AssertionError(message)


def test_a_removal_overrides_only_imports_of_the_releases_it_names(tmp_path):
    service = service_module.KnowledgeService(tmp_path)
    raw = _raw(_pack())
    subscription = _subscription(raw)
    other = ("9.9.9", subscription.artifact_sha256)
    job = service_module.ImportJob(
        job_id="j", pack_id="market-memes", state="building", created_at="", updated_at="",
        arrived_at=5, subscription=subscription,
    )
    service._pending_removals["market-memes"] = frozenset({(6, frozenset({other}))})
    assert service._superseded(job) is False
    service._pending_removals["market-memes"] = frozenset({(6, frozenset({subscription.release}))})
    assert service._superseded(job) is True
    service._pending_removals.clear()
    for marks, superseded in (
        ({other: 6}, False),
        ({subscription.release: 4}, False),
        ({subscription.release: 6}, True),
        ({None: 6}, True),  # a local removal covers every release
    ):
        service._removed_at["market-memes"] = marks
        assert service._superseded(job) is superseded
    # A local import is never covered by an unsubscribe.
    local = service_module.ImportJob(
        job_id="l", pack_id="market-memes", state="building", created_at="", updated_at="", arrived_at=5
    )
    service._removed_at["market-memes"] = {subscription.release: 6}
    assert service._superseded(local) is False


async def test_unsubscribe_removes_the_named_release_of_that_subscription_only(tmp_path):
    service = await _started(tmp_path)
    try:
        await _subscribe(service)
        await _settle(service, await service.import_pack(_raw(_pack("local-pack"))))
        release = frozenset({_release(_raw(_pack()))})
        for pack_id, identity in (
            ("market-memes", ("plugin-market", "43", "knowledge/market-memes")),
            ("market-memes", ("plugin-market", "42", "knowledge/elsewhere")),
            # A pack that is there under no subscription is never "already gone".
            ("local-pack", IDENTITY),
            # The package is installed, but as another pack.
            ("another-pack", IDENTITY),
        ):
            with pytest.raises(service_module.KnowledgeUnavailable) as excinfo:
                await service.remove_subscription(pack_id, identity, release)
            assert excinfo.value.reason == "subscription_identity_mismatch"
        assert {pack["pack_id"] for pack in await service.list_packs()} == {"market-memes", "local-pack"}
        removed = await service.remove_subscription("market-memes", IDENTITY, release)
        assert removed["removed"] is True and removed["removed_entries"] == 1
        assert {pack["pack_id"] for pack in await service.list_packs()} == {"local-pack"}
    finally:
        await service.stop()


async def test_unsubscribe_is_safe_to_repeat(tmp_path):
    service = await _started(tmp_path)
    try:
        await _subscribe(service)
        release = frozenset({_release(_raw(_pack()))})
        assert (await service.remove_subscription("market-memes", IDENTITY, release))["removed"] is True
        again = await service.remove_subscription("market-memes", IDENTITY, release)
        assert again["removed"] is False and again["kept"] == "not_installed"
        # Gone, then a local pack took the id: that is not "already removed".
        await _settle(service, await service.import_pack(_raw(_pack(content="Now local."))))
        with pytest.raises(service_module.KnowledgeUnavailable) as excinfo:
            await service.remove_subscription("market-memes", IDENTITY, release)
        assert excinfo.value.reason == "subscription_identity_mismatch"
    finally:
        await service.stop()


async def test_unsubscribe_leaves_a_release_it_does_not_name(tmp_path):
    """A repeated or late unsubscribe never removes a later subscription of the package."""
    service = await _started(tmp_path)
    try:
        updated = _raw(_pack(content="A newer note about the kotatsu."))
        await _subscribe(service, _pack(content="A newer note about the kotatsu."), version="2.0.0")
        kept = await service.remove_subscription("market-memes", IDENTITY, frozenset({_release(_raw(_pack()))}))
        assert kept["removed"] is False and kept["kept"] == "other_release"
        # Same version, other bytes: another release too.
        kept = await service.remove_subscription(
            "market-memes", IDENTITY, frozenset({("2.0.0", hashlib.sha256(_raw(_pack())).hexdigest())})
        )
        assert kept["kept"] == "other_release"
        (pack,) = await service.list_packs()
        assert pack["subscription"]["version"] == "2.0.0"
        removed = await service.remove_subscription("market-memes", IDENTITY, frozenset({_release(updated, "2.0.0")}))
        assert removed["removed"] is True
    finally:
        await service.stop()


async def test_unsubscribe_cancels_a_queued_and_a_building_import_of_its_releases(tmp_path):
    service = await _started(tmp_path)
    try:
        raw = _raw(_pack())
        await service._write_lock.acquire()  # the job cannot commit yet
        staged = await service.import_pack(raw, subscription=_subscription(raw))
        assert staged["state"] == "queued"
        await _wait_for(lambda: service._jobs[staged["job_id"]].state == "building", "job not taken")
        removal = asyncio.create_task(service.remove_subscription("market-memes", IDENTITY, frozenset({_release(raw)})))
        await asyncio.sleep(0.05)
        service._write_lock.release()
        result = await removal
        # Not installed yet, so nothing removed; the import that was on its way stood down.
        assert result["removed"] is False and result["kept"] == "not_installed"
        job = await _settle(service, staged)
        assert job["state"] == "cancelled"
        assert await service.list_packs() == []

        # Queued behind the lock (the runner busy with another import).
        await service._write_lock.acquire()
        blocker = await service.import_pack(_raw(_pack("local-pack")))
        await _wait_for(lambda: service._jobs[blocker["job_id"]].state == "building", "blocker not taken")
        queued = await service.import_pack(raw, subscription=_subscription(raw))
        removal = asyncio.create_task(service.remove_subscription("market-memes", IDENTITY, frozenset({_release(raw)})))
        await asyncio.sleep(0.05)
        service._write_lock.release()
        assert (await removal)["kept"] == "not_installed"
        assert (await _settle(service, queued))["state"] == "cancelled"
        assert (await _settle(service, blocker))["state"] == "active"
        assert {pack["pack_id"] for pack in await service.list_packs()} == {"local-pack"}
    finally:
        await service.stop()


async def test_unsubscribe_cancels_a_queued_import_at_once(tmp_path):
    """Not when the runner reaches it: its staged file and slot are freed with the answer."""
    service = await _started(tmp_path)
    try:
        runner = next(task for task in service._tasks if task.get_name() == "knowledge-import-jobs")
        runner.cancel()
        await asyncio.gather(runner, return_exceptions=True)
        raw = _raw(_pack())
        queued = await service.import_pack(raw, subscription=_subscription(raw))
        assert service._staging_path(queued["job_id"]).exists()
        await service.remove_subscription("market-memes", IDENTITY, frozenset({_release(raw)}))
        assert service._jobs[queued["job_id"]].state == "cancelled"
        assert not service._staging_path(queued["job_id"]).exists()
    finally:
        await service.stop()


async def test_identity_is_checked_again_when_the_unsubscribe_gets_the_lock(tmp_path):
    service = await _started(tmp_path)
    try:
        raw = _raw(_pack())
        await service._write_lock.acquire()
        local = await service.import_pack(_raw(_pack(content="A local note.")))
        await _wait_for(lambda: service._jobs[local["job_id"]].state == "building", "job not taken")
        # Nothing installed yet when it arrives; the local import lands first.
        removal = asyncio.create_task(service.remove_subscription("market-memes", IDENTITY, frozenset({_release(raw)})))
        await asyncio.sleep(0.05)
        service._write_lock.release()
        with pytest.raises(service_module.KnowledgeUnavailable) as excinfo:
            _ = await removal
        assert excinfo.value.reason == "subscription_identity_mismatch"
        assert (await _settle(service, local))["state"] == "active"
    finally:
        await service.stop()


async def test_unsubscribe_overrides_an_import_that_is_not_a_job_yet(tmp_path):
    service = await _started(tmp_path)
    try:
        raw = _raw(_pack())
        await service._admission_lock.acquire()  # parsed, waiting to be staged
        importing = asyncio.create_task(service.import_pack(raw, subscription=_subscription(raw)))
        await _wait_for(lambda: _imports(service, "admitting"), "import not admitting")
        result = await service.remove_subscription("market-memes", IDENTITY, frozenset({_release(raw)}))
        assert result["kept"] == "not_installed"
        listed: list[list[str]] = []
        discard = service._discard_staging

        async def discard_and_look(job_id):
            # Once it is a job, it is listed once, as that job.
            listed.append([item["state"] for item in _imports(service)])
            await discard(job_id)

        service._discard_staging = discard_and_look
        service._admission_lock.release()
        job = await importing
        assert job["ok"] is True and job["state"] == "cancelled"
        assert listed and listed[0] == ["cancelled"]
        await asyncio.sleep(0.05)
        assert await service.list_packs() == []
    finally:
        await service.stop()


async def test_unsubscribe_leaves_imports_of_releases_it_does_not_name(tmp_path):
    service = await _started(tmp_path)
    try:
        raw = _raw(_pack())
        newer = _raw(_pack(content="A newer note about the kotatsu."))
        await service._admission_lock.acquire()
        importing = asyncio.create_task(service.import_pack(newer, subscription=_subscription(newer, version="2.0.0")))
        await _wait_for(lambda: _imports(service, "admitting"), "import not admitting")
        result = await service.remove_subscription("market-memes", IDENTITY, frozenset({_release(raw)}))
        assert result["kept"] == "not_installed"
        service._admission_lock.release()
        job = await _settle(service, await importing)
        assert job["state"] == "active"
        (pack,) = await service.list_packs()
        assert pack["subscription"]["version"] == "2.0.0"
    finally:
        await service.stop()


async def test_an_update_arriving_after_the_unsubscribe_stands(tmp_path):
    """The unsubscribe takes its place in the order when it arrives, not when it gets the lock."""
    service = await _started(tmp_path)
    try:
        raw = _raw(_pack())
        await _subscribe(service)
        updated = _raw(_pack(content="A newer note about the kotatsu."))
        await service._write_lock.acquire()  # the removal waits for the lock
        removal = asyncio.create_task(
            service.remove_subscription(
                "market-memes", IDENTITY, frozenset({_release(raw), _release(updated, "2.0.0")})
            )
        )
        await asyncio.sleep(0)
        importing = asyncio.create_task(
            service.import_pack(updated, subscription=_subscription(updated, version="2.0.0"))
        )
        await asyncio.sleep(0.05)
        service._write_lock.release()
        assert (await removal)["removed"] is True
        job = await _settle(service, await importing)
        assert job["state"] == "active"
        (pack,) = await service.list_packs()
        assert pack["subscription"]["version"] == "2.0.0"
    finally:
        await service.stop()


async def test_unsubscribe_refuses_an_import_of_the_package_under_another_pack(tmp_path):
    service = await _started(tmp_path)
    try:
        other = _raw(_pack("second-pack"))
        async with service._write_lock:
            staged = await service.import_pack(other, subscription=_subscription(other, pack_id="second-pack"))
            with pytest.raises(service_module.KnowledgeUnavailable) as excinfo:
                await service.remove_subscription("market-memes", IDENTITY, frozenset({_release(other)}))
            assert excinfo.value.reason == "subscription_identity_mismatch"
        assert (await _settle(service, staged))["state"] == "active"
    finally:
        await service.stop()


async def test_unsubscribe_compares_the_whole_identity_of_an_import_on_its_way(tmp_path):
    service = await _started(tmp_path)
    try:
        raw = _raw(_pack())
        async with service._write_lock:
            staged = await service.import_pack(raw, subscription=_subscription(raw, remote_id="knowledge/renamed"))
            # Same package and pack, but a removal for the old remote id.
            with pytest.raises(service_module.KnowledgeUnavailable) as excinfo:
                await service.remove_subscription("market-memes", IDENTITY, frozenset({_release(raw)}))
            assert excinfo.value.reason == "subscription_identity_mismatch"
        assert (await _settle(service, staged))["state"] == "active"
    finally:
        await service.stop()


async def test_unsubscribe_answers_busy_when_the_lock_stays_held(tmp_path, monkeypatch):
    monkeypatch.setattr(service_module, "WRITE_LOCK_TIMEOUT_SECONDS", 0.05)
    service = await _started(tmp_path)
    try:
        await _subscribe(service)
        release = frozenset({_release(_raw(_pack()))})
        async with service._write_lock:
            with pytest.raises(service_module.KnowledgeUnavailable) as excinfo:
                await service.remove_subscription("market-memes", IDENTITY, release)
            assert excinfo.value.reason == "knowledge_busy"
        # Nothing half-done: no removal left pending, the pack is still there,
        # and the same request goes through now.
        assert service._pending_removals == {}
        assert len(await service.list_packs()) == 1
        assert (await service.remove_subscription("market-memes", IDENTITY, release))["removed"] is True
    finally:
        await service.stop()


async def test_a_repeated_apply_is_answered_with_its_staged_job(tmp_path):
    service = await _started(tmp_path)
    try:
        raw = _raw(_pack())
        async with service._write_lock:  # keep the job queued
            first = await service.import_pack(raw, subscription=_subscription(raw))
            again = await service.import_pack(raw, subscription=_subscription(raw))
            assert again["ok"] is True and again["job_id"] == first["job_id"] and again["existing"] is True
            # Another release of the same pack is a different request.
            newer = _raw(_pack(content="A newer note about the kotatsu."))
            other = await service.import_pack(newer, subscription=_subscription(newer, version="2.0.0"))
            assert other == {"ok": False, "reason": "job_in_progress"}
        assert (await _settle(service, first))["state"] == "active"
        assert len(service.list_jobs()) == 1
    finally:
        await service.stop()


async def test_subscription_state_shows_installed_imports_and_failures(tmp_path, monkeypatch):
    service = await _started(tmp_path)
    try:
        await _subscribe(service)
        await _settle(service, await service.import_pack(_raw(_pack("local-pack"))))
        # A subscription import that fails after it was staged (at commit).
        monkeypatch.setattr(service, "_identity_conflict", lambda *_args: False)
        other = _raw(_pack())
        failed = await _settle(service, await service.import_pack(other, subscription=_subscription(other, package_id="43")))
        assert failed["state"] == "failed"
        service._broken_packs = ("market-memes",)
        state = service.subscription_state()
        (installed,) = state["installed"]  # the local pack is not listed
        assert installed["pack_id"] == "market-memes" and installed["broken"] is True
        assert installed["subscription"]["provider_package_id"] == "42"
        latest = state["imports"][0]
        assert latest["state"] == "failed" and latest["reason"] == "subscription_identity_mismatch"
        assert latest["subscription"]["provider_package_id"] == "43"
        assert all(item["subscription"] for item in state["imports"])
    finally:
        await service.stop()


async def test_a_repeated_apply_is_answered_even_while_imports_are_full(tmp_path, monkeypatch):
    monkeypatch.setattr(service_module, "MAX_PENDING_IMPORTS", 1)
    service = await _started(tmp_path)
    try:
        raw = _raw(_pack())
        async with service._write_lock:
            first = await service.import_pack(raw, subscription=_subscription(raw))
            assert service.import_busy()
            again = await service.import_pack(raw, subscription=_subscription(raw))
            assert again["ok"] is True and again["job_id"] == first["job_id"] and again["existing"] is True
            # Anything else is still refused before parsing.
            other = _raw(_pack("other-pack"))
            busy = await service.import_pack(other, subscription=_subscription(other, pack_id="other-pack", package_id="43"))
            assert busy == {"ok": False, "reason": "knowledge_busy"}
            tampered = await service.import_pack(raw + b" ", subscription=_subscription(raw))
            assert tampered == {"ok": False, "reason": "knowledge_busy"}
        assert (await _settle(service, first))["state"] == "active"
    finally:
        await service.stop()


def test_releases_contract():
    sha = "a" * 64
    assert parse_releases([{"version": "1.0.0", "artifact_sha256": sha}] * 2) == frozenset({("1.0.0", sha)})
    for broken in (
        [],
        {"version": "1.0.0", "artifact_sha256": sha},
        [{"version": "1.0.0"}],
        [{"version": "1.0.0", "artifact_sha256": sha, "pack_id": "x1"}],
        [{"version": "1.0.0", "artifact_sha256": "A" * 64}],
        [{"version": "1.0.0", "artifact_sha256": sha}] * 17,
    ):
        with pytest.raises(SubscriptionError):
            parse_releases(broken)


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


async def test_apply_route_installs_a_subscription(client):
    http, service = client
    raw = _raw(_pack())
    response = await http.post("/internal/knowledge/subscriptions/apply", **_apply_form(raw, _subscription(raw).to_json()))
    body = response.json()
    assert body["ok"] is True and body["state"] == "queued"
    job = await _settle(service, body)
    assert job["state"] == "active"
    state = (await http.get("/internal/knowledge/subscriptions")).json()
    assert state["ok"] is True and state["installed"][0]["pack_id"] == "market-memes"
    assert state["imports"][0]["state"] == "active"


async def test_apply_route_lets_a_repeat_through_while_imports_are_full(client, monkeypatch):
    http, service = client
    monkeypatch.setattr(service_module, "MAX_PENDING_IMPORTS", 1)
    raw = _raw(_pack())
    form = _apply_form(raw, _subscription(raw).to_json())
    async with service._write_lock:
        first = (await http.post("/internal/knowledge/subscriptions/apply", **form)).json()
        again = (await http.post("/internal/knowledge/subscriptions/apply", **form)).json()
    assert again["ok"] is True and again["job_id"] == first["job_id"]
    await _settle(service, first)


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


async def test_remove_route_checks_its_request_and_is_safe_to_repeat(client):
    http, service = client
    await _subscribe(service)
    expected = {"provider": "plugin-market", "provider_package_id": "42", "remote_id": "knowledge/market-memes"}
    request = {
        "protocol_version": 1,
        "pack_id": "market-memes",
        "expected_subscription": expected,
        "releases": [{"version": "1.0.0", "artifact_sha256": hashlib.sha256(_raw(_pack())).hexdigest()}],
    }
    url = "/internal/knowledge/subscriptions/remove"
    for broken, reason in (
        ({**request, "protocol_version": True}, "unsupported_protocol"),
        ({**request, "releases": []}, "invalid_subscription"),
        ({**request, "expected_subscription": {**expected, "provider": "elsewhere"}}, "untrusted_provider"),
        ({**request, "pack_id": ""}, "invalid_request"),
    ):
        response = await http.post(url, json=broken)
        assert response.status_code == 400 and response.json()["reason"] == reason
    wrong = await http.post(url, json={**request, "expected_subscription": {**expected, "provider_package_id": "1"}})
    assert wrong.json()["reason"] == "subscription_identity_mismatch"
    removed = (await http.post(url, json=request)).json()
    assert removed["ok"] is True and removed["removed"] is True
    again = (await http.post(url, json=request)).json()
    assert again["ok"] is True and again["removed"] is False and again["kept"] == "not_installed"


# ── proxy allowlists ────────────────────────────────────────────────


def test_subscription_writes_are_main_only():
    for path in ("subscriptions/apply", "subscriptions/remove"):
        assert path in knowledge_proxy.SERVICE_WRITE_PATHS
        assert path not in knowledge_proxy.WRITE_PATHS
    assert "subscriptions" in knowledge_proxy.READ_PATHS
    assert "packs/remove/status" not in knowledge_proxy.READ_PATHS
