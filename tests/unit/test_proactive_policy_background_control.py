"""Control refresh must retire live policy leases after its SQLite commit."""
import asyncio
import copy
import sqlite3
from contextlib import closing
from threading import Event, RLock
from types import SimpleNamespace
import uuid

import pytest

from main_routers import recommendation_router as routes
from services.proactive_feedback import capture, policy
import main_logic.recommendation_feedback as feedback_module
from tests.unit.test_proactive_feedback_runtime_v2 import env  # noqa: F401
from utils.config_manager.reserved_schema import set_reserved


async def seed_policy(env, registry):
    uid = "a" * 32
    assert await env.runtime._refresh_control(env.owner, env.saved["access_token"])
    card = await asyncio.to_thread(env.store.create, env.owner, "Fixture", "fixture-turn", {
        "name": "Fixture", "artist": "Fixture", "url": "https://music.example/fixture",
    }, {}, category="news", character_uid=uid, control=env.runtime.control_snapshot(env.owner))
    result = await asyncio.to_thread(env.store.record, env.owner, card["id"], {
        "event_id": "explicit-no", "kind": "skipped", "offer_context": card["offer_context"],
    })
    ticket = registry.observe(env.store.path, env.owner, incarnation=env.state["account_incarnation"])
    view = registry.publish(env.store.path, env.owner, uid, result["policy"], identity=ticket)
    return uid, result, ticket, view


@pytest.mark.asyncio
async def test_background_control_replacement_retires_existing_view_without_new_policy_request(env, monkeypatch):
    registry = policy.PolicyRegistry()
    monkeypatch.setattr(policy, "registry", registry)
    try:
        uid, result, ticket, view = await seed_policy(env, registry)
        assert view.guard(["music"])()
        assert not view.guard(["news"])()

        # The supported background refresh owns the real control transaction.
        # No recommendation request or credential write runs after this change.
        env.state["account_incarnation"] = str(uuid.uuid4())
        assert await env.runtime._refresh_control(env.owner, env.saved["access_token"])
        assert await asyncio.to_thread(env.store.get_policy, env.owner, uid) == {}
        assert not view.guard(["music"])(), "A result prepared under the retired account instance must not publish"

        context = await asyncio.to_thread(env.store.get_policy_context, env.owner, uid)
        current = registry.publish(env.store.path, env.owner, uid, context["policy"],
                                   incarnation=context["account_incarnation"])
        assert current.guard(["news"])()
        registry.publish(env.store.path, env.owner, uid, result["policy"], identity=ticket)
        assert current.guard(["news"])(), "A delayed retired worker must not restore the old projection"
    finally:
        await env.client.aclose()


@pytest.mark.asyncio
async def test_same_incarnation_refresh_preserves_live_lease_and_monotonic_deadline(env, monkeypatch):
    clock = SimpleNamespace(wall=1_000_000.0, tick=100.0)
    monkeypatch.setattr(feedback_module, "time", SimpleNamespace(time=lambda: clock.wall))
    registry = policy.PolicyRegistry(wall_clock=lambda: clock.wall, clock=lambda: clock.tick)
    monkeypatch.setattr(policy, "registry", registry)
    try:
        uid, _, _, view = await seed_policy(env, registry)
        clock.tick += 900
        clock.wall -= 3600
        env.state["clear_generation"] += 1
        assert await env.runtime._refresh_control(env.owner, env.saved["access_token"])
        assert view.guard(["music"])(), "A statistics refresh must not retire an otherwise valid delivery"
        assert not view.guard(["news"])()
        context = await asyncio.to_thread(env.store.get_policy_context, env.owner, uid)
        refreshed = registry.publish(env.store.path, env.owner, uid, context["policy"],
                                     incarnation=context["account_incarnation"])
        clock.tick += 300
        assert refreshed.guard(["news"])(), "Same-incarnation control refresh cannot restart the hard deadline"
    finally:
        await env.client.aclose()


@pytest.mark.asyncio
async def test_stale_and_failed_control_transactions_do_not_retire_valid_lease(env, monkeypatch):
    registry = policy.PolicyRegistry()
    monkeypatch.setattr(policy, "registry", registry)
    try:
        uid, saved, _, view = await seed_policy(env, registry)
        control = copy.deepcopy(env.runtime._control)
        control.update(account_incarnation=str(uuid.uuid4()), binding_revision=0)
        assert await asyncio.to_thread(env.runtime._apply_control, env.owner, control) is False
        assert view.guard(["music"])()

        def reject_policy_delete():
            with closing(sqlite3.connect(env.store.path)) as connection:
                connection.execute("CREATE TRIGGER reject_policy_delete BEFORE DELETE ON recommendation_policy BEGIN SELECT RAISE(ABORT,'synthetic failure'); END")
                connection.commit()

        await asyncio.to_thread(reject_policy_delete)
        control["binding_revision"] = env.runtime._control["binding_revision"] + 1
        with pytest.raises(sqlite3.IntegrityError, match="synthetic failure"):
            await asyncio.to_thread(env.runtime._apply_control, env.owner, control)
        assert await asyncio.to_thread(env.store.get_policy, env.owner, uid) == saved["policy"]
        assert view.guard(["music"])()
        assert not view.guard(["news"])()
    finally:
        await env.client.aclose()


@pytest.mark.asyncio
async def test_cancelled_refresh_still_retires_committed_replacement_and_stale_provider_cannot_restore_it(env, monkeypatch):
    registry = policy.PolicyRegistry()
    monkeypatch.setattr(policy, "registry", registry)
    committed, release, finished = Event(), Event(), Event()
    task = None
    try:
        uid, saved_policy, old_ticket, view = await seed_policy(env, registry)
        original_store_apply = env.store.apply_control
        original_worker = env.runtime._apply_control

        def paused_after_commit(owner, control):
            result = original_store_apply(owner, control)
            committed.set()
            if not release.wait(timeout=5):
                raise AssertionError("fixture did not release the committed control worker")
            return result

        def tracked_worker(owner, control):
            try:
                return original_worker(owner, control)
            finally:
                finished.set()

        monkeypatch.setattr(env.store, "apply_control", paused_after_commit)
        monkeypatch.setattr(env.runtime, "_apply_control", tracked_worker)
        replacement = str(uuid.uuid4())
        env.state["account_incarnation"] = replacement
        task = asyncio.create_task(env.runtime._refresh_control(env.owner, env.saved["access_token"]))
        assert await asyncio.to_thread(committed.wait, 5)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        release.set()
        assert await asyncio.to_thread(finished.wait, 5)
        assert not view.guard(["music"])()
        assert await asyncio.to_thread(env.store.get_policy, env.owner, uid) == {}

        # Cancellation left the live provider behind the committed SQL binding.
        stale = env.runtime.control_snapshot(env.owner)
        assert stale["account_incarnation"] != replacement
        current = registry.publish(env.store.path, env.owner, uid, {}, incarnation=replacement)
        context = await asyncio.to_thread(env.store.get_policy_context, env.owner, uid, control=stale)
        assert context["account_incarnation"] == replacement, "An older live provider cannot undo a committed account replacement"
        registry.publish(env.store.path, env.owner, uid, context["policy"], incarnation=context["account_incarnation"])
        assert current.guard(["news"])(), "Reading with an older live provider must preserve the current delivery lease"

        profile = {}
        set_reserved(profile, "character_uid", uid)
        monkeypatch.setattr(routes, "STORE_PROTOCOL_VERSION", 2)
        monkeypatch.setattr(routes, "policy_registry", registry)
        monkeypatch.setattr(routes.host, "saved_session_snapshot", lambda: dict(env.saved))
        credential_lock = RLock()
        monkeypatch.setattr(routes.host, "saved_session_writer_fence", lambda: credential_lock)
        monkeypatch.setattr(routes, "get_config_manager", lambda: SimpleNamespace(
            get_runtime_config_path=lambda name: env.store.path,
            load_characters=lambda: {"猫娘": {"Fixture": profile}},
        ))
        monkeypatch.setattr(capture, "_provider", env.runtime.control_snapshot)
        captured, captured_uid = await routes.capture_policy("Fixture", env.owner)
        assert captured_uid == uid and captured.guard(["news"])()
        assert current.guard(["news"])(), "The real HTTP adapter cannot retire B using its stale live provider A"
        registry.publish(env.store.path, env.owner, uid, saved_policy["policy"], identity=old_ticket)
        assert captured.guard(["news"])() and current.guard(["news"])()
        assert not view.guard(["music"])()
    finally:
        release.set()
        if task is not None and not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        if committed.is_set():
            assert await asyncio.to_thread(finished.wait, 5)
        await env.client.aclose()
