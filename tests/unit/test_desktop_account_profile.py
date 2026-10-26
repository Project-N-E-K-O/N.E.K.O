"""The settings avatar follows the active account without publishing credentials."""

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
import httpx
from fastapi import FastAPI

from main_routers import card_drop_router as C
from main_routers import community_oauth as O


pytestmark = [pytest.mark.unit, pytest.mark.asyncio]
USER_ID = "11111111-1111-4111-8111-111111111111"


@pytest.fixture
def profile_state(monkeypatch):
    state = {"local_user_id": USER_ID, "access_token": "private-token",
             "base_url": "https://community.example", "auth_source": "oauth"}
    monkeypatch.setattr(C, "_local_request_source_allowed", lambda _: True)
    monkeypatch.setattr(O, "_account_request_identity", AsyncMock(return_value="local"))
    monkeypatch.setattr(O, "resolve_saved_oauth_status", AsyncMock(
        return_value={"logged_in": True, "snapshot": dict(state)}))
    monkeypatch.setattr(C, "_desktop_session_snapshot", lambda: dict(state))
    lookup = AsyncMock(return_value=C._CloudIdentityLookup(C._CloudIdentity(
        USER_ID, "oauth", {"avatar_url": "/static/avatars/me.webp", "email": "private@example.com"}), 200))
    monkeypatch.setattr(C, "_lookup_cloud_identity", lookup)
    return state, lookup


async def test_profile_reads_current_cloud_avatar_and_omits_private_fields(profile_state):
    _, lookup = profile_state
    response = await O.oauth_profile_endpoint(SimpleNamespace())
    assert response.status_code == 200
    assert json.loads(response.body) == {
        "local_user_id": USER_ID, "avatar_url": "/static/avatars/me.webp",
        "community_base_url": "https://community.example",
    }
    assert response.headers["cache-control"] == "no-store"
    lookup.assert_awaited_once_with("https://community.example", "private-token")


@pytest.mark.parametrize("change", ["logout", "switch", "refresh"])
async def test_profile_rejects_session_changes_during_lookup(profile_state, change):
    state, lookup = profile_state
    started, release = asyncio.Event(), asyncio.Event()
    result = lookup.return_value

    async def delayed(*_):
        started.set()
        await release.wait()
        return result

    lookup.side_effect = delayed
    task = asyncio.create_task(O.oauth_profile_endpoint(SimpleNamespace()))
    try:
        await asyncio.wait_for(started.wait(), 1)
        if change == "logout":
            state.clear()
        elif change == "switch":
            state["local_user_id"] = "other"
        else:
            state["access_token"] = "new-token"
        release.set()
        response = await asyncio.wait_for(task, 1)
        assert response.status_code == 409
        assert b"avatar_url" not in response.body
    finally:
        release.set()
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)


@pytest.mark.parametrize("result", [
    C._CloudIdentityLookup(None, 503, "unavailable"),
    C._CloudIdentityLookup(C._CloudIdentity("other", "oauth", {"avatar_url": "secret"}), 200),
])
async def test_unavailable_or_mismatched_cloud_identity_never_returns_an_avatar(profile_state, result):
    _, lookup = profile_state
    lookup.return_value = result
    response = await O.oauth_profile_endpoint(SimpleNamespace())
    assert response.status_code == 503
    assert b"avatar_url" not in response.body


async def test_profile_timeout_is_bounded(profile_state):
    _, lookup = profile_state
    lookup.side_effect = TimeoutError()
    assert (await O.oauth_profile_endpoint(SimpleNamespace())).status_code == 503


async def test_profile_releases_a_stalled_cloud_lookup_at_its_deadline(profile_state):
    _, lookup = profile_state
    cancelled = asyncio.Event()

    async def stalled(*_):
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()

    lookup.side_effect = stalled
    response = await asyncio.wait_for(O.oauth_profile_endpoint(SimpleNamespace()), 7)
    assert response.status_code == 503
    assert cancelled.is_set()


async def test_signed_out_profile_does_not_contact_cloud(profile_state, monkeypatch):
    _, lookup = profile_state
    monkeypatch.setattr(O, "resolve_saved_oauth_status", AsyncMock(return_value={"logged_in": False}))
    response = await O.oauth_profile_endpoint(SimpleNamespace())
    assert json.loads(response.body) == {"local_user_id": None, "avatar_url": None}
    lookup.assert_not_awaited()


@pytest.mark.parametrize("failure,expected", [("unavailable", 503), ("rejected", 200)])
async def test_profile_distinguishes_validation_outage_from_rejected_credentials(
    profile_state, monkeypatch, failure, expected,
):
    state, lookup = profile_state
    # Exercise the real status resolver: an outage retains credentials, while a
    # definite rejection can also leave them on disk if conditional cleanup fails.
    monkeypatch.setattr(O, "resolve_saved_oauth_status", O._resolve_saved_oauth_status)
    monkeypatch.setattr(O, "_load_oauth_status_records", lambda: (dict(state), {}))
    monkeypatch.setattr(O, "_clear_rejected_oauth_snapshot", lambda _: False)
    lookup.return_value = C._CloudIdentityLookup(None, 503 if failure == "unavailable" else 401, failure)
    response = await O.oauth_profile_endpoint(SimpleNamespace())
    assert response.status_code == expected
    assert response.headers["cache-control"] == "no-store"
    assert json.loads(response.body) == (
        {"detail": "profile_unavailable"} if expected == 503
        else {"local_user_id": None, "avatar_url": None}
    )
    lookup.assert_awaited_once_with("https://community.example", "private-token")
    assert state["access_token"] == "private-token"


@pytest.mark.parametrize("origin_allowed", [False, True])
async def test_profile_requires_allowed_source_and_account_authorization(profile_state, monkeypatch, origin_allowed):
    _, lookup = profile_state
    monkeypatch.setattr(C, "_local_request_source_allowed", lambda _: origin_allowed)
    monkeypatch.setattr(O, "_account_request_identity", AsyncMock(return_value=None))
    assert (await O.oauth_profile_endpoint(SimpleNamespace())).status_code == 403
    lookup.assert_not_awaited()


@pytest.mark.parametrize("peer,origin,expected", [
    ("127.0.0.1", None, 200),
    ("127.0.0.1", "https://untrusted.example", 403),
    ("203.0.113.1", None, 403),
])
async def test_profile_route_uses_real_source_guards(monkeypatch, peer, origin, expected):
    monkeypatch.setattr(O, "resolve_saved_oauth_status", AsyncMock(return_value={"logged_in": False}))
    app = FastAPI()
    app.include_router(O.router)
    transport = httpx.ASGITransport(app=app, client=(peer, 50000))
    async with httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1:48911") as client:
        response = await client.get("/api/card-drop/oauth/profile", headers={"Origin": origin} if origin else {})
    assert response.status_code == expected
    assert response.headers["cache-control"] == "no-store"
