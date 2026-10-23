"""Local management, baseline admission and upgrade recovery with synthetic data."""
import copy
import uuid

import httpx
import pytest
from fastapi import FastAPI

from services.proactive_feedback.runtime import SyncFailure
from tests.unit.test_proactive_feedback_runtime_v2 import env


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
    calls = install_snapshot(env)
    env.options["lose_response"] = True
    try:
        assert (await env.runtime.sync_once())["state"] == "retry_wait"
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
        await env.runtime.activate_device(previous, "My desktop", env.owner["key"])
        assert not env.state["registrations"][previous]["active"]
        assert env.state["registrations"][env.registration]["display_name"] == "My desktop"
        assert sum(row["active"] for row in env.state["registrations"].values()) == 5
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
