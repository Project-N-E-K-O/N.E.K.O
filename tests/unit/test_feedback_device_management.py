"""Local management, baseline admission and upgrade recovery with synthetic data."""
import copy
import json
import uuid

import httpx
import pytest
from fastapi import FastAPI

from services.proactive_feedback.runtime import SyncFailure
from tests.unit.test_proactive_feedback_runtime_v2 import env


@pytest.mark.asyncio
@pytest.mark.parametrize("escaped", [False, True], ids=["utf8", "json-escaped"])
async def test_local_activation_accepts_maximum_context_and_optional_fields(env, monkeypatch, escaped):
    from main_routers import proactive_feedback_router as routes
    monkeypatch.setattr(routes, "_runtime", env.runtime)
    monkeypatch.setattr(routes, "_validate", lambda request: None)
    env.options["activation_context"] = "A" * 2048
    target = str(uuid.uuid4())
    env.state["registrations"][target] = {"installation_id": str(uuid.uuid4()), "active": True, "version": 7}
    command = {"activation_context": env.options["activation_context"], "expected_owner_key": env.owner["key"],
               "replace_registration_id": target, "expected_replace_registration_version": 7,
               "display_name": "🎵" * 64}
    raw = json.dumps(command, ensure_ascii=escaped).encode()
    if escaped:
        raw = raw.replace(b"A", b"\\u0041")
    assert 2048 < len(raw) < 16 * 1024
    app = FastAPI()
    app.include_router(routes.router)
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://local") as client:
            response = await client.post("/api/proactive/feedback/devices/activate", content=raw,
                                         headers={"Content-Type": "application/json"})
        assert response.status_code == 200, response.text
        assert not env.state["registrations"][target]["active"]
        assert env.state["registrations"][env.registration]["display_name"] == command["display_name"]
    finally:
        await env.client.aclose()


@pytest.mark.asyncio
async def test_local_activation_body_limit_accepts_exact_boundary_and_stops_oversized_stream(env, monkeypatch):
    from main_routers import proactive_feedback_router as routes
    monkeypatch.setattr(routes, "_runtime", env.runtime)
    monkeypatch.setattr(routes, "_validate", lambda request: None)
    raw = json.dumps({"activation_context": "synthetic-activation-context", "expected_owner_key": env.owner["key"]}).encode()
    raw = raw.ljust(16 * 1024, b" ")
    app = FastAPI()
    app.include_router(routes.router)
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://local") as client:
            headers = {"Content-Type": "application/json"}
            path = "/api/proactive/feedback/devices/activate"
            response = await client.post(path, content=raw, headers=headers)
            assert response.status_code == 200, response.text
            before = list(env.requests)

            async def oversized():
                yield raw[:8192]
                yield raw[8192:]
                yield b" "
                pytest.fail("oversized request must stop consuming the stream")

            for content in (raw + b" ", oversized()):
                response = await client.post(path, content=content, headers=headers)
                assert response.status_code == 413
                assert response.json()["error_code"] == "invalid_feedback_control"
                assert env.requests == before
            response = await client.post(path, json={"activation_context": "A" * 2049})
            assert response.status_code == 422
            assert env.requests == before
            response = await client.post(path, content=b"[" * 3000 + b"]" * 3000, headers=headers)
            assert response.status_code == 422
            assert env.requests == before
    finally:
        await env.client.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize("suffix", ["/capture-context", "/devices/fixture/remove"])
async def test_other_control_commands_keep_original_body_limit(env, monkeypatch, suffix):
    from main_routers import proactive_feedback_router as routes
    monkeypatch.setattr(routes, "_runtime", env.runtime)
    monkeypatch.setattr(routes, "_validate", lambda request: None)
    app = FastAPI()
    app.include_router(routes.router)
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://local") as client:
            response = await client.post("/api/proactive/feedback" + suffix, content=b" " * 2049,
                                         headers={"Content-Type": "application/json"})
        assert response.status_code == 413
        assert not env.requests
    finally:
        await env.client.aclose()


def install_snapshot(env):
    calls = []
    def snapshot(owner):
        calls.append(owner.copy())
        settings = {"proactiveMusicEnabled": True}
        return {"context": env.runtime.control_snapshot(owner), "payload": {
            "revision": len(calls), "origin": "baseline", "occurred_at": 100 + len(calls),
            "previous": settings, "settings": settings}}
    env.runtime.current_settings_provider = snapshot
    return calls


@pytest.mark.asyncio
async def test_first_current_snapshot_is_once_per_registration_not_per_clear(env):
    calls = install_snapshot(env)
    try:
        assert (await env.runtime.sync_once())["state"] == "synced"
        first = copy.deepcopy(env.uploads[0])
        assert first["items"][0]["kind"] == "settings"
        assert first["items"][0]["payload"]["origin"] == "baseline"
        assert (await env.runtime.sync_once())["state"] == "idle"
        env.state["clear_generation"] += 1
        assert (await env.runtime.sync_once())["state"] == "idle"
        assert len(calls) == len(env.uploads) == 1
        await env.runtime.remove_device(env.registration, 1, env.owner["key"])
        assert (await env.runtime.sync_once())["state"] == "registration_removed"
        await env.runtime.activate_device(display_name="Fixture laptop", expected_owner_key=env.owner["key"])
        assert (await env.runtime.sync_once())["state"] == "synced"
        assert len(calls) == len(env.uploads) == 2
        assert env.uploads[1]["binding"]["registration_version"] > first["binding"]["registration_version"]
    finally:
        await env.client.aclose()


@pytest.mark.asyncio
async def test_snapshot_capacity_skips_old_attempt_then_captures_current_again(env):
    calls = install_snapshot(env)
    env.store.MAX_PENDING_UPLOADS = 0
    try:
        assert (await env.runtime.sync_once())["state"] == "idle"
        assert env.store.current_settings_baseline_needed(env.owner)
        env.store.MAX_PENDING_UPLOADS = 100
        assert (await env.runtime.sync_once())["state"] == "synced"
        assert len(calls) == 2
        assert env.uploads[0]["items"][0]["payload"]["revision"] == 2
        assert not env.store.current_settings_baseline_needed(env.owner)
    finally:
        await env.client.aclose()


@pytest.mark.asyncio
async def test_lost_baseline_reply_uses_same_frozen_body_without_new_snapshot(env):
    clock = [1000.0]
    env.runtime.clock = lambda: clock[0]
    calls = install_snapshot(env)
    env.options["lose_response"] = True
    try:
        assert (await env.runtime.sync_once())["state"] == "retry_wait"
        assert (await env.runtime.sync_once())["state"] == "retry_wait"
        assert len(env.uploads) == 1
        clock[0] += 31
        assert (await env.runtime.sync_once())["state"] == "synced"
        assert len(calls) == 1 and env.uploads[0] == env.uploads[1]
    finally:
        await env.client.aclose()


@pytest.mark.asyncio
async def test_sixth_device_requires_explicit_choice_and_stale_owner_cannot_activate(env):
    env.state["registrations"].clear()
    for _ in range(5):
        env.state["registrations"][str(uuid.uuid4())] = {"version": 1, "installation_id": str(uuid.uuid4()), "active": True}
    try:
        listed = await env.runtime.list_devices()
        assert len(listed["devices"]) == 5 and not any(row["is_current"] for row in listed["devices"])
        with pytest.raises(SyncFailure, match="cloud_conflict"):
            await env.runtime.activate_device()
        assert len(env.state["registrations"]) == 5
        with pytest.raises(SyncFailure, match="session_changed"):
            await env.runtime.activate_device(expected_owner_key="old-owner")
        previous = listed["devices"][0]["registration_id"]
        await env.runtime.activate_device(previous, "My desktop", env.owner["key"],
                                          expected_replace_registration_version=1)
        assert not env.state["registrations"][previous]["active"]
        assert env.state["registrations"][env.registration]["display_name"] == "My desktop"
        assert sum(row["active"] for row in env.state["registrations"].values()) == 5
    finally:
        await env.client.aclose()


@pytest.mark.asyncio
async def test_local_replacement_version_is_preserved_through_both_http_hops(env, monkeypatch):
    from main_routers import proactive_feedback_router as routes
    monkeypatch.setattr(routes, "_runtime", env.runtime)
    monkeypatch.setattr(routes, "_validate", lambda request: None)
    env.state["registrations"].clear()
    target = str(uuid.uuid4())
    env.state["registrations"][target] = {"installation_id": str(uuid.uuid4()), "active": True, "version": 3}
    app = FastAPI()
    app.include_router(routes.router)
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://local") as client:
            command = {"replace_registration_id": target, "expected_owner_key": env.owner["key"],
                       "expected_replace_registration_version": 1}
            response = await client.post("/api/proactive/feedback/devices/activate", json=command)
            assert response.status_code == 409 and response.json()["error_code"] == "cloud_conflict"
            assert env.state["registrations"][target]["active"]
            command["expected_replace_registration_version"] = 3
            response = await client.post("/api/proactive/feedback/devices/activate", json=command)
            assert response.status_code == 200, response.text
            assert not env.state["registrations"][target]["active"]
    finally:
        await env.client.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize("version", [None, 0, -1, True, 1.5, "1", 2**53])
async def test_runtime_invalid_replacement_view_never_sends_a_command(env, version):
    before = list(env.requests)
    try:
        with pytest.raises(SyncFailure, match="cloud_conflict"):
            await env.runtime.activate_device(str(uuid.uuid4()), expected_replace_registration_version=version)
        assert env.requests == before
    finally:
        await env.client.aclose()


@pytest.mark.asyncio
async def test_426_is_visible_and_successful_handshake_recovers(env):
    try:
        env.options["upgrade"] = True
        assert (await env.runtime.sync_once())["state"] == "upgrade_required"
        status = await env.runtime.status()
        assert status["upgrade_required"] and not status["fully_synced"]
        assert env.runtime.control_snapshot(env.owner) is None
        env.options["upgrade"] = False
        assert (await env.runtime.sync_once())["state"] == "idle"
        assert not (await env.runtime.status())["upgrade_required"]
    finally:
        await env.client.aclose()


@pytest.mark.asyncio
async def test_local_management_routes_proxy_explicit_actions_and_render_page(env, monkeypatch):
    from main_routers import proactive_feedback_router as routes
    monkeypatch.setattr(routes, "_runtime", env.runtime)
    monkeypatch.setattr(routes, "_validate", lambda request: None)
    app = FastAPI()
    app.include_router(routes.router)
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://local") as client:
            root = "/api/proactive/feedback"
            assert (await client.get(root + "/manage")).status_code == 200
            response = await client.get(root + "/devices")
            assert response.json()["devices"][0]["is_current"]
            invalid = await client.post(root + "/devices/activate", json={"display_name": "x" * 65})
            assert invalid.status_code == 422
            response = await client.post(root + f"/devices/{env.registration}/remove", json={
                "expected_registration_version": 1, "expected_owner_key": env.owner["key"]})
            assert response.status_code == 200 and response.json()["removed"]
            assert not env.state["registrations"][env.registration]["active"]
    finally:
        await env.client.aclose()
