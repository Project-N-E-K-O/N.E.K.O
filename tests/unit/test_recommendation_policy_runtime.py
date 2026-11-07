"""Publication/identity fences for committed local recommendation feedback."""
from contextlib import contextmanager
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock
import sqlite3

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from main_routers import recommendation_router as routes
from main_routers.system_router import _shared
from main_routers.system_router import proactive_chat_flow as flow
from services.proactive_feedback.policy import PolicyRegistry
from utils.config_manager.reserved_schema import set_reserved


OWNER = {"key": "a", "user_id": "a", "community": "https://community.example"}
UID = "a" * 32


def state(revision=1, *, count=1, deadline=1300, updated=100):
    return dict(revision=revision, decline_count=count, cooldown_until=deadline, updated_at=updated)


def registry(*, roll=lambda: 0):
    clock = SimpleNamespace(wall=100.0, tick=1.0)
    value = PolicyRegistry(wall_clock=lambda: clock.wall, clock=lambda: clock.tick, roll=roll)
    return value, clock


def test_new_refusal_invalidates_existing_views_and_ignores_stale_reads():
    value, _ = registry()
    view = value.publish("db", OWNER, UID, {})
    allowed = view.guard(["news"])
    music_allowed = view.guard(["music"])
    assert allowed()
    value.publish("db", OWNER, UID, {"news": state()})
    assert not allowed() and music_allowed()
    value.publish("db", OWNER, UID, {})
    value.publish("db", OWNER, UID, {"news": state(0, deadline=0)})
    assert not allowed()
    assert view.filter_modes(["news", "music"]) == (["music"], None)


def test_same_revision_reload_and_wall_clock_change_cannot_extend_cooldown():
    value, clock = registry()
    view = value.publish("db", OWNER, UID, {"news": state()})
    clock.wall -= 3600
    clock.tick += 1200
    value.publish("db", OWNER, UID, {"news": state()})
    assert view.guard(["news"])()


def test_repeated_refusals_reduce_selection_once_per_category_and_recover():
    rolls = []
    value, clock = registry(roll=lambda: rolls.append(True) or 0.9)
    view = value.publish("db", OWNER, UID, {"web": state(count=3, deadline=0)})
    assert view.filter_modes(["home", "web"]) == ([], "PASS_USER_PREFERENCE")
    assert len(rolls) == 1
    assert view.filter_modes(["home"]) == ([], "PASS_USER_PREFERENCE")
    assert len(rolls) == 1
    clock.wall += 3 * 86400
    assert view.filter_modes(["home"]) == (["home"], None)


def test_first_decline_does_not_add_soft_penalty_after_cooldown():
    value, _ = registry(roll=lambda: 0.999)
    view = value.publish("db", OWNER, UID, {"news": state(deadline=0)})
    assert view.filter_modes(["news"]) == (["news"], None)


def test_retired_owner_response_cannot_reactivate_old_identity():
    value, _ = registry()
    ticket = value.observe("db", OWNER)
    old = value.publish("db", OWNER, UID, {}, identity=ticket)
    other = {**OWNER, "key": "b"}
    current = value.publish("db", other, UID, {})
    value.publish("db", OWNER, UID, {}, identity=ticket)
    assert not old.guard(["news"])()
    assert current.guard(["news"])()


def test_verified_incarnation_change_retires_old_views_but_first_discovery_does_not():
    value, _ = registry()
    old = value.publish("db", OWNER, UID, {"news": state()})
    value.publish("db", OWNER, UID, {"news": state()}, incarnation="first")
    assert old.guard(["music"])()
    current = value.publish("db", OWNER, UID, {}, incarnation="replacement")
    assert not old.guard(["music"])()
    assert current.guard(["news"])()


def test_role_database_and_bounded_eviction_are_isolated():
    value, _ = registry()
    value.MAX_SCOPES = 2
    old = value.publish("db", OWNER, UID, {"news": state()})
    other = value.publish("db", OWNER, "b" * 32, {})
    assert other.guard(["news"])()
    value.publish("other-db", OWNER, UID, {})
    assert not old.guard(["music"])()  # An evicted in-flight scope fails closed.


@pytest.mark.asyncio
@pytest.mark.parametrize("protocol", [1, 2])
async def test_actual_http_decision_is_visible_before_success_and_survives_registry_restart(tmp_path, monkeypatch, protocol):
    monkeypatch.setattr(routes, "STORE_PROTOCOL_VERSION", protocol)
    monkeypatch.setattr(routes, "policy_registry", PolicyRegistry())
    snapshot = {"local_user_id": "a", "access_token": "synthetic", "base_url": "https://community.example"}
    @contextmanager
    def fence():
        yield
    monkeypatch.setattr(routes.host, "saved_session_writer_fence", fence)
    monkeypatch.setattr(routes.host, "saved_session_snapshot", lambda: dict(snapshot))
    profile = {}
    set_reserved(profile, "character_uid", UID)
    config = SimpleNamespace(
        get_runtime_config_path=lambda name: tmp_path / name,
        load_characters=lambda: {"猫娘": {"Neko": profile}},
    )
    monkeypatch.setattr(routes, "get_config_manager", lambda: config)
    monkeypatch.setattr(routes, "load_global_conversation_settings", lambda: {"proactiveMusicEnabled": True})
    monkeypatch.setattr(_shared, "AUTOSTART_CSRF_TOKEN", "test")
    owner = await routes.capture_owner()
    view, uid = await routes.capture_policy("Neko", owner)
    guard = view.guard(["music"])
    assert uid == UID and guard()
    body = {"success": True, "action": "chat", "source_mode": "music", "turn_id": "turn",
            "source_links": [{"title": "Song", "artist": "Artist", "url": "https://example.com/a.mp3"}]}
    await routes.attach_music_recommendation(body, owner, "Neko", character_uid=uid)
    app = FastAPI()
    app.include_router(routes.router, prefix="/api/proactive")
    headers = {"Origin": "http://127.0.0.1:48911", "X-CSRF-Token": "test"}
    with TestClient(app, base_url="http://127.0.0.1:48911", headers=headers) as client:
        endpoint = f"/api/proactive/recommendations/{body['music_recommendation']['id']}/events"
        choice = {"event_id": "explicit-no", "kind": "skipped"}
        response = client.post(endpoint, json=choice)
        assert response.status_code == 200 and response.json()["applied"]
        assert not guard()
        revision = response.json()["policy"]["music"]["revision"]
        replay = client.post(endpoint, json=choice)
        assert not replay.json()["applied"]
        assert replay.json()["policy"]["music"]["revision"] == revision
    monkeypatch.setattr(routes, "policy_registry", PolicyRegistry())
    restarted, _ = await routes.capture_policy("Neko", owner)
    assert restarted.filter_modes(["music"]) == ([], "PASS_USER_COOLDOWN")


@pytest.mark.parametrize("failure", ["identity", "policy"])
def test_http_policy_read_failure_cannot_bypass_a_saved_refusal(monkeypatch, failure):
    monkeypatch.setattr(_shared, "AUTOSTART_CSRF_TOKEN", "test")
    monkeypatch.setattr(flow, "get_config_manager", lambda: SimpleNamespace(
        aget_character_data=AsyncMock(return_value=("Master", "Neko", None, None, None, {}, None, None, None))))
    monkeypatch.setattr(flow, "get_session_manager", lambda: {})
    monkeypatch.setattr(flow, "is_theater_active", lambda _: False)
    monkeypatch.setattr(flow, "capture_owner", AsyncMock(
        side_effect=RuntimeError("identity unreadable") if failure == "identity" else None,
        return_value=OWNER))
    monkeypatch.setattr(flow, "capture_policy", AsyncMock(side_effect=sqlite3.DatabaseError("policy unreadable")))
    generation = AsyncMock()
    monkeypatch.setattr(flow.proactive_service, "handle_proactive_chat", generation)
    app = FastAPI()
    app.add_api_route("/api/proactive_chat", flow.proactive_chat, methods=["POST"])
    with TestClient(app, base_url="http://127.0.0.1:48911", headers={
        "Origin": "http://127.0.0.1:48911", "X-CSRF-Token": "test",
    }) as client:
        response = client.post("/api/proactive_chat", json={"enabled_modes": ["news"]})
    assert response.status_code == 503
    assert response.json()["error"] == "recommendation_policy_unavailable"
    assert response.json()["attempt_consumed"] is False
    generation.assert_not_called()


@pytest.mark.asyncio
async def test_real_capture_policy_uses_persisted_identity_while_live_control_bootstraps(tmp_path, monkeypatch):
    from tests.unit.test_recommendation_policy import _control

    monkeypatch.setattr(routes, "STORE_PROTOCOL_VERSION", 2)
    monkeypatch.setattr(routes, "policy_registry", PolicyRegistry())
    monkeypatch.setattr(routes, "_control", lambda _owner: None)
    monkeypatch.setattr(routes.host, "saved_session_snapshot", lambda: {
        "local_user_id": "synthetic", "access_token": "synthetic", "base_url": "https://example.invalid",
    })
    @contextmanager
    def fence():
        yield
    monkeypatch.setattr(routes.host, "saved_session_writer_fence", fence)
    profile = {}
    set_reserved(profile, "character_uid", UID)
    monkeypatch.setattr(routes, "get_config_manager", lambda: SimpleNamespace(
        get_runtime_config_path=lambda name: tmp_path / name,
        load_characters=lambda: {"猫娘": {"Neko": profile}},
    ))
    owner = await routes.capture_owner()
    original = {**_control(incarnation="old-account"), "owner_key": owner["key"]}
    replacement = {**_control(incarnation="new-account", revision=2), "owner_key": owner["key"]}

    def seed(store, actual_owner):
        store.apply_control(actual_owner, original)
        card = store.create(actual_owner, "Neko", "turn", {
            "name": "Synthetic", "artist": "Synthetic", "url": "https://example.invalid/story",
        }, {}, category="news", character_uid=UID, control=original)
        store.record(actual_owner, card["id"], {"event_id": "no", "kind": "skipped"}, control=original)

    await asyncio.to_thread(routes._operate, seed)
    old_view, _ = await routes.capture_policy("Neko", owner)
    assert not old_view.guard(["news"])()
    await asyncio.to_thread(routes._operate, lambda store, actual_owner: store.apply_control(actual_owner, replacement))
    # Keep the live provider unavailable: the fresh DB context is sufficient.
    current, _ = await routes.capture_policy("Neko", owner)
    assert current.guard(["news"])()
    assert not old_view.guard(["music"])()
