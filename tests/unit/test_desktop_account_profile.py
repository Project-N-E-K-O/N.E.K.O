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
REAL_STATUS_RESOLVER = O.resolve_saved_oauth_status
REAL_SESSION_SNAPSHOT = C._desktop_session_snapshot


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


@pytest.fixture
def validated_profile_state(profile_state, monkeypatch):
    state, lookup = profile_state
    monkeypatch.setattr(O, "resolve_saved_oauth_status", REAL_STATUS_RESOLVER)
    monkeypatch.setattr(O, "_load_oauth_status_records", lambda: (dict(state), {}))
    monkeypatch.setattr(O, "_oauth_status_tasks", {})
    monkeypatch.setattr(O, "_oauth_status_lock", asyncio.Lock())
    return state, lookup


@pytest.mark.parametrize("avatar", ["/static/avatars/current.webp", None, {"invalid": "type"}])
async def test_profile_reuses_validated_avatar_without_a_second_cloud_request(validated_profile_state, avatar):
    _, lookup = validated_profile_state
    lookup.side_effect = [
        C._CloudIdentityLookup(C._CloudIdentity(USER_ID, "oauth", {
            "avatar_url": avatar, "email": "private@example.com", "phone": "private-phone",
        }), 200),
        # A second request could fail even though validation already succeeded.
        C._CloudIdentityLookup(None, 503, "unavailable"),
    ]
    response = await O.oauth_profile_endpoint(SimpleNamespace())
    assert response.status_code == 200
    assert json.loads(response.body) == {
        "local_user_id": USER_ID,
        "avatar_url": avatar if isinstance(avatar, str) else None,
        "community_base_url": "https://community.example",
    }
    lookup.assert_awaited_once_with("https://community.example", "private-token")


@pytest.mark.parametrize("change", ["logout", "switch", "refresh"])
async def test_reused_profile_still_rejects_a_session_replaced_during_validation(validated_profile_state, change):
    state, lookup = validated_profile_state
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
        assert lookup.await_count == 1
    finally:
        release.set()
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        await asyncio.gather(*O._oauth_status_tasks.values(), return_exceptions=True)


async def test_reused_profile_rejects_a_different_verified_cloud_user(validated_profile_state):
    _, lookup = validated_profile_state
    lookup.side_effect = [
        C._CloudIdentityLookup(C._CloudIdentity("other", "oauth", {"avatar_url": "secret"}), 200),
        C._CloudIdentityLookup(C._CloudIdentity(USER_ID, "oauth", {"avatar_url": "wrong-retry"}), 200),
    ]
    response = await O.oauth_profile_endpoint(SimpleNamespace())
    assert response.status_code == 503
    assert b"avatar_url" not in response.body
    assert lookup.await_count == 1


@pytest.mark.parametrize("available", [True, False])
async def test_refreshed_session_fetches_profile_with_the_new_token(validated_profile_state, monkeypatch, available):
    state, lookup = validated_profile_state
    state.update(refresh_token="old-refresh", auth_public_url="https://auth.example", client_id="desktop")
    refresh = AsyncMock(return_value=("ok", {"access_token": "fresh-token", "refresh_token": "fresh-refresh"}))
    monkeypatch.setattr(O, "_refresh_oauth_token", refresh)

    def persist(expected, access, refresh_token):
        assert expected["access_token"] == "private-token"
        state.update(access_token=access, refresh_token=refresh_token)
        return True

    monkeypatch.setattr(O, "_persist_refreshed_oauth_tokens", persist)
    lookup.side_effect = [
        C._CloudIdentityLookup(None, 401, "rejected"),
        lookup.return_value if available else C._CloudIdentityLookup(None, 503, "unavailable"),
    ]
    response = await O.oauth_profile_endpoint(SimpleNamespace())
    assert response.status_code == (200 if available else 503)
    assert json.loads(response.body) == (
        {"local_user_id": USER_ID, "avatar_url": "/static/avatars/me.webp",
         "community_base_url": "https://community.example"}
        if available else {"detail": "profile_unavailable"}
    )
    assert [call.args for call in lookup.await_args_list] == [
        ("https://community.example", "private-token"),
        ("https://community.example", "fresh-token"),
    ]
    refresh.assert_awaited_once()


async def test_status_endpoints_do_not_publish_the_internal_verified_profile(validated_profile_state, monkeypatch):
    _, lookup = validated_profile_state
    monkeypatch.setattr(O, "_account_request_identity", AsyncMock(return_value="remote-instance"))
    for endpoint in (O.oauth_status_endpoint, C.auth_status_endpoint):
        response = await endpoint(SimpleNamespace())
        assert response["logged_in"] is True
        assert response["user"] == {"display_name": None, "email": None}
        assert "verified_profile" not in response
        serialized = json.dumps(response)
        assert "private-token" not in serialized
        assert "private@example.com" not in serialized
        assert "/static/avatars/me.webp" not in serialized
    assert lookup.await_count == 2


@pytest.mark.parametrize("user_id", [None, "", "absent"])
async def test_legacy_profile_uses_only_the_cloud_verified_user_id(validated_profile_state, user_id):
    state, lookup = validated_profile_state
    if user_id == "absent":
        state.pop("local_user_id")
    else:
        state["local_user_id"] = user_id
    state["auth_source"] = ""
    response = await O.oauth_profile_endpoint(SimpleNamespace())
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert json.loads(response.body) == {
        "local_user_id": USER_ID, "avatar_url": "/static/avatars/me.webp",
        "community_base_url": "https://community.example",
    }
    assert not state.get("local_user_id")
    lookup.assert_awaited_once_with("https://community.example", "private-token")


@pytest.mark.parametrize("change", ["logout", "token", "base", "identity", "source"])
async def test_legacy_profile_rejects_session_changes_after_validation(validated_profile_state, change):
    state, lookup = validated_profile_state
    state.pop("local_user_id")
    state["auth_source"] = ""
    result = lookup.return_value

    async def changed(*_):
        # The real resolver captured the old session before this cloud await.
        if change == "logout":
            state.clear()
        else:
            field, value = {
                "token": ("access_token", "replacement-token"),
                "base": ("base_url", "https://other.example"),
                "identity": ("local_user_id", USER_ID),
                "source": ("auth_source", "oauth"),
            }[change]
            state[field] = value
        return result

    lookup.side_effect = changed
    response = await O.oauth_profile_endpoint(SimpleNamespace())
    assert response.status_code == 409
    assert json.loads(response.body) == {"detail": "session_changed"}
    assert lookup.await_count == 1


@pytest.mark.parametrize("failure,expected", [("unavailable", 503), ("rejected", 200)])
async def test_legacy_profile_does_not_infer_identity_from_unverified_credentials(
    validated_profile_state, monkeypatch, failure, expected,
):
    state, lookup = validated_profile_state
    state.pop("local_user_id")
    lookup.return_value = C._CloudIdentityLookup(None, 503 if failure == "unavailable" else 401, failure)
    monkeypatch.setattr(O, "_clear_rejected_oauth_snapshot", lambda _: False)
    response = await O.oauth_profile_endpoint(SimpleNamespace())
    assert response.status_code == expected
    assert json.loads(response.body) == (
        {"detail": "profile_unavailable"} if failure == "unavailable"
        else {"local_user_id": None, "avatar_url": None}
    )
    assert lookup.await_count == 1


@pytest.mark.parametrize("storage", ["social", "auth"])
async def test_legacy_profile_from_real_records_and_cloud_protocol(monkeypatch, storage):
    social = {"baseUrl": "https://community.example", "token": "legacy-private-token"}
    auth = {"access_token": "legacy-private-token"}
    monkeypatch.setattr(C, "_load_social_session", lambda: social if storage == "social" else None)
    monkeypatch.setattr(C, "_load_auth", lambda: auth if storage == "auth" else None)
    monkeypatch.setattr(C, "_social_base_url", lambda: "https://community.example")
    monkeypatch.setattr(C, "_local_request_source_allowed", lambda _: True)
    monkeypatch.setattr(O, "_account_request_identity", AsyncMock(return_value="local"))
    monkeypatch.setattr(O, "_oauth_status_tasks", {})
    monkeypatch.setattr(O, "_oauth_status_lock", asyncio.Lock())
    requests = []

    def cloud(request):
        requests.append(request)
        return httpx.Response(200, json={
            "auth_source": "oauth", "user": {"id": USER_ID, "avatar_url": "hue:210", "email": "private@example.com"},
        })

    client_factory = httpx.AsyncClient
    monkeypatch.setattr(C.httpx, "AsyncClient", lambda **kwargs: client_factory(
        transport=httpx.MockTransport(cloud), **kwargs))
    assert not REAL_SESSION_SNAPSHOT()["local_user_id"]
    response = await O.oauth_profile_endpoint(SimpleNamespace())
    assert response.status_code == 200
    assert json.loads(response.body) == {
        "local_user_id": USER_ID, "avatar_url": "hue:210",
        "community_base_url": "https://community.example",
    }
    assert len(requests) == 1
    assert str(requests[0].url) == "https://community.example/api/users/me"
    assert requests[0].headers["Authorization"] == "Bearer legacy-private-token"
    # Profile reads do not migrate or write legacy credential records.
    assert social == {"baseUrl": "https://community.example", "token": "legacy-private-token"}
    assert auth == {"access_token": "legacy-private-token"}
