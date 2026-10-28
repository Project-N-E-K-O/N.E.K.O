"""Remote desktop API relay keeps cloud credentials and account streams local."""

import asyncio
import httpx
from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from main_routers import community_remote_proxy as P
from utils.instance_access import InstanceAccessMiddleware


@pytest.fixture
def proxy(monkeypatch):
    key = "test-instance-key-" + "k" * 40
    monkeypatch.setenv("NEKO_INSTANCE_ACCESS_KEY", key)
    monkeypatch.setenv("NEKO_BEHIND_PROXY", "true")
    app = FastAPI()
    app.include_router(P.router)
    app.add_middleware(InstanceAccessMiddleware)
    client = TestClient(app, base_url="https://instance.example")
    client.headers["Authorization"] = "Bearer " + key
    return client


def test_anonymous_never_opens_upstream(proxy, monkeypatch):
    monkeypatch.setattr(P.O, "resolve_saved_oauth_status", lambda: pytest.fail("Anonymous account read"))
    proxy.headers.pop("Authorization")
    assert proxy.post("/api/forge/credits/grant", content="invalid").status_code == 401


def test_desktop_simple_cross_site_post_cannot_use_cloud_account(monkeypatch):
    monkeypatch.setenv("NEKO_BEHIND_PROXY", "false")
    monkeypatch.delenv("NEKO_ACTIVITY_TRACKER_REMOTE", raising=False)
    monkeypatch.delenv("ACTIVITY_TRACKER_REMOTE", raising=False)
    app = FastAPI()
    app.include_router(P.router)
    app.add_middleware(InstanceAccessMiddleware)
    client = TestClient(app, base_url="http://127.0.0.1", client=("127.0.0.1", 50000))

    async def forbidden():
        pytest.fail("Cross-site request must not read credentials or contact cloud")

    monkeypatch.setattr(P.O, "resolve_saved_oauth_status", forbidden)
    for path in ("grant", "drop-events/claim", "drop-events/event/ack"):
        response = client.post("/api/forge/credits/" + path, content="{}",
                               headers={"Origin": "https://evil.example", "Content-Type": "text/plain"})
        assert response.status_code == 403


def test_relay_uses_saved_cloud_token_and_fixed_origin(proxy, monkeypatch):
    seen = []
    snapshot = {"access_token": "cloud-only-secret", "local_user_id": "owner"}

    async def status():
        return {"snapshot": snapshot}

    async def upstream(request):
        seen.append(request)
        return httpx.Response(200, json={"credits": 2})

    real_client = httpx.AsyncClient
    monkeypatch.setattr(P.O, "resolve_saved_oauth_status", status)
    monkeypatch.setattr(P.C, "_desktop_session_snapshot", lambda: snapshot)
    monkeypatch.setattr(P.C, "_social_base_url", lambda: "https://community.example")
    monkeypatch.setattr(P.httpx, "AsyncClient", lambda **kwargs: real_client(transport=httpx.MockTransport(upstream), **kwargs))
    response = proxy.get("/api/forge/credits?url=https://evil.example")
    assert response.json() == {"credits": 2}
    assert seen[0].url.host == "community.example"
    assert seen[0].url.path == "/api/forge/credits"
    assert seen[0].headers["Authorization"] == "Bearer cloud-only-secret"
    assert "cloud-only-secret" not in response.text
    assert proxy.get("/api/arbitrary-proxy").status_code == 404
    assert proxy.post("/api/forge/credits/drop-events/a.b/ack").status_code == 400


def test_account_switch_stops_old_notification_stream(proxy, monkeypatch):
    current = {"access_token": "old-secret", "local_user_id": "old-owner"}
    snapshot = dict(current)

    async def status():
        return {"snapshot": snapshot}

    class Events(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield b"data: before\n\n"
            current.update(local_user_id="new-owner", access_token="new-secret")
            await asyncio.sleep(1.05)  # Account revocation is checked at most once/second.
            yield b"data: old-owner-private\n\n"

    async def upstream(_request):
        return httpx.Response(200, stream=Events())

    real_client = httpx.AsyncClient
    monkeypatch.setattr(P.O, "resolve_saved_oauth_status", status)
    monkeypatch.setattr(P.C, "_desktop_session_snapshot", lambda: current)
    monkeypatch.setattr(P.httpx, "AsyncClient", lambda **kwargs: real_client(transport=httpx.MockTransport(upstream), **kwargs))
    response = proxy.get("/api/notifications/stream")
    assert response.text == "data: before\n\n"


def test_visit_invite_response_relays_only_the_boolean_decision(proxy, monkeypatch):
    notification_id = "22222222-2222-4222-8222-222222222222"
    seen = []

    async def status():
        return {"logged_in": True, "snapshot": {"access_token": "cloud-secret", "local_user_id": "owner"}}

    async def upstream(request):
        seen.append(request)
        return httpx.Response(200, json={"ok": True})

    real_client = httpx.AsyncClient
    monkeypatch.setattr(P.O, "resolve_saved_oauth_status", status)
    monkeypatch.setattr(P.C, "_social_base_url", lambda: "https://community.example")
    monkeypatch.setattr(
        P.httpx,
        "AsyncClient",
        lambda **kwargs: real_client(transport=httpx.MockTransport(upstream), **kwargs),
    )
    response = proxy.post(
        f"/api/community/visit-invitations/{notification_id}/respond",
        json={"accepted": False, "actor_id": "forged"},
    )
    assert response.status_code == 200
    assert seen[0].url.path == f"/api/notifications/{notification_id}/visit-response"
    assert seen[0].headers["Authorization"] == "Bearer cloud-secret"
    assert seen[0].read().decode() == '{"accepted":false}'
    assert proxy.post(
        f"/api/community/visit-invitations/{notification_id}/respond", json={"accepted": "no"},
    ).status_code == 400


def test_visit_hub_preserves_real_mutual_presence(proxy, monkeypatch):
    owner_id = "11111111-1111-4111-8111-111111111111"
    online_id = "22222222-2222-4222-8222-222222222222"
    offline_id = "33333333-3333-4333-8333-333333333333"

    async def status():
        return {"logged_in": True, "snapshot": {"access_token": "cloud-secret", "local_user_id": owner_id}}

    async def upstream(request):
        if request.url.path == "/api/users/me":
            return httpx.Response(200, json={
                "id": owner_id,
                "display_name": "Owner",
                "username": "owner",
                "avatar_url": "/static/avatars/owner.webp",
            })
        if request.url.path == "/api/users/me/presence":
            return httpx.Response(200, json={"status": "away"})
        if request.url.path == f"/api/users/{owner_id}/following":
            return httpx.Response(200, json={"items": [
                {"id": online_id, "display_name": "在线喵友", "is_mutual": True,
                 "online": True, "presence_status": "away",
                 "last_seen_at": "2026-10-26T08:00:00Z"},
                {"id": offline_id, "display_name": "离线喵友", "is_mutual": True,
                 "online": False, "last_seen_at": "2026-10-20T08:00:00Z"},
                {"id": "44444444-4444-4444-8444-444444444444", "display_name": "单向关注",
                 "is_mutual": False, "online": True},
            ], "total": 3})
        if request.url.path == "/api/notifications":
            return httpx.Response(200, json={"items": []})
        return httpx.Response(404, json={"detail": "not_found"})

    real_client = httpx.AsyncClient
    monkeypatch.setattr(P.O, "resolve_saved_oauth_status", status)
    monkeypatch.setattr(P.C, "_social_base_url", lambda: "https://community.example")
    monkeypatch.setattr(
        P.httpx,
        "AsyncClient",
        lambda **kwargs: real_client(transport=httpx.MockTransport(upstream), **kwargs),
    )

    response = proxy.get("/api/community/visit-hub")
    assert response.status_code == 200
    assert response.json()["account"] == {
        "id": owner_id,
        "display_name": "Owner",
        "username": "owner",
        "avatar_url": "https://community.example/static/avatars/owner.webp",
        "presence_status": "away",
    }
    assert response.json()["friends"] == [
        {"id": online_id, "display_name": "在线喵友", "username": None, "avatar_url": None,
         "online": True, "presence_status": "away",
         "last_seen_at": "2026-10-26T08:00:00Z"},
        {"id": offline_id, "display_name": "离线喵友", "username": None, "avatar_url": None,
         "online": False, "presence_status": "offline",
         "last_seen_at": "2026-10-20T08:00:00Z"},
    ]


def test_visit_hub_reads_mutual_friends_after_the_first_page(proxy, monkeypatch):
    owner_id = "11111111-1111-4111-8111-111111111111"
    later_id = "22222222-2222-4222-8222-222222222222"
    offsets = []

    async def status():
        return {"logged_in": True, "snapshot": {"access_token": "cloud-secret"}}

    async def upstream(request):
        if request.url.path == "/api/users/me":
            return httpx.Response(200, json={"id": owner_id, "display_name": "Owner"})
        if request.url.path == "/api/users/me/presence":
            return httpx.Response(200, json={"status": "online"})
        if request.url.path == "/api/notifications":
            return httpx.Response(200, json={"items": []})
        if request.url.path == f"/api/users/{owner_id}/following":
            offset = int(request.url.params["offset"])
            offsets.append(offset)
            if offset == 0:
                return httpx.Response(200, json={
                    "items": [
                        {"id": f"00000000-0000-4000-8000-{index:012d}", "is_mutual": False}
                        for index in range(100)
                    ],
                    "total": 101,
                })
            return httpx.Response(200, json={
                "items": [{"id": later_id, "display_name": "Later", "is_mutual": True}],
                "total": 101,
            })
        return httpx.Response(404, json={"detail": "not_found"})

    real_client = httpx.AsyncClient
    monkeypatch.setattr(P.O, "resolve_saved_oauth_status", status)
    monkeypatch.setattr(P.C, "_social_base_url", lambda: "https://community.example")
    monkeypatch.setattr(
        P.httpx,
        "AsyncClient",
        lambda **kwargs: real_client(transport=httpx.MockTransport(upstream), **kwargs),
    )

    response = proxy.get("/api/community/visit-hub")
    assert response.status_code == 200
    assert offsets == [0, 100]
    assert [friend["id"] for friend in response.json()["friends"]] == [later_id]


@pytest.mark.asyncio
async def test_following_rejects_a_non_object_success_body():
    async def upstream(_request):
        return httpx.Response(200, json=[])

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(upstream),
    ) as client:
        status, body = await P._community_following(
            client,
            {"base_url": "https://community.example", "access": "cloud-secret"},
            "11111111-1111-4111-8111-111111111111",
        )

    assert status == 502
    assert body == {"detail": "community_invalid_response"}


def test_visit_hub_does_not_claim_notifications_are_authoritative_on_failure(proxy, monkeypatch):
    owner_id = "11111111-1111-4111-8111-111111111111"

    async def status():
        return {"logged_in": True, "snapshot": {"access_token": "cloud-secret"}}

    async def upstream(request):
        if request.url.path == "/api/users/me":
            return httpx.Response(200, json={"id": owner_id})
        if request.url.path == f"/api/users/{owner_id}/following":
            return httpx.Response(200, json={"items": []})
        if request.url.path == "/api/users/me/presence":
            return httpx.Response(200, json={"status": "online"})
        if request.url.path == "/api/notifications":
            return httpx.Response(503, json={"detail": "unavailable"})
        return httpx.Response(404, json={"detail": "not_found"})

    real_client = httpx.AsyncClient
    monkeypatch.setattr(P.O, "resolve_saved_oauth_status", status)
    monkeypatch.setattr(P.C, "_social_base_url", lambda: "https://community.example")
    monkeypatch.setattr(
        P.httpx,
        "AsyncClient",
        lambda **kwargs: real_client(transport=httpx.MockTransport(upstream), **kwargs),
    )

    body = proxy.get("/api/community/visit-hub").json()
    assert body["notifications_ok"] is False
    assert body["incoming_invite_ids"] == []
    assert body["incoming_invite"] is None


def test_visit_hub_pages_all_unread_invitation_ids(proxy, monkeypatch):
    owner_id = "11111111-1111-4111-8111-111111111111"
    notification_ids = [f"00000000-0000-4000-8000-{index:012d}" for index in range(101)]

    async def status():
        return {"logged_in": True, "snapshot": {"access_token": "cloud-secret"}}

    async def upstream(request):
        if request.url.path == "/api/users/me":
            return httpx.Response(200, json={"id": owner_id})
        if request.url.path == f"/api/users/{owner_id}/following":
            return httpx.Response(200, json={"items": []})
        if request.url.path == "/api/users/me/presence":
            return httpx.Response(200, json={"status": "online"})
        if request.url.path == "/api/notifications":
            offset = int(request.url.params["offset"])
            ids = notification_ids[offset:offset + 100]
            return httpx.Response(200, json={
                "items": [{
                    "id": notification_id,
                    "payload": {"type": "visit_invite", "visit_id": "A" * 22},
                } for notification_id in ids],
                "total": len(notification_ids),
            })
        return httpx.Response(404, json={"detail": "not_found"})

    real_client = httpx.AsyncClient
    monkeypatch.setattr(P.O, "resolve_saved_oauth_status", status)
    monkeypatch.setattr(P.C, "_social_base_url", lambda: "https://community.example")
    monkeypatch.setattr(
        P.httpx,
        "AsyncClient",
        lambda **kwargs: real_client(transport=httpx.MockTransport(upstream), **kwargs),
    )

    body = proxy.get("/api/community/visit-hub").json()
    assert body["notifications_ok"] is True
    assert body["incoming_invite_ids"] == notification_ids
    assert body["incoming_invite"]["id"] == notification_ids[0]


def test_presence_update_relays_only_supported_status(proxy, monkeypatch):
    seen = []

    async def status():
        return {"logged_in": True, "snapshot": {"access_token": "cloud-secret", "local_user_id": "owner"}}

    async def upstream(request):
        seen.append(request)
        return httpx.Response(200, json={"status": "invisible"})

    real_client = httpx.AsyncClient
    monkeypatch.setattr(P.O, "resolve_saved_oauth_status", status)
    monkeypatch.setattr(P.C, "_social_base_url", lambda: "https://community.example")
    monkeypatch.setattr(
        P.httpx,
        "AsyncClient",
        lambda **kwargs: real_client(transport=httpx.MockTransport(upstream), **kwargs),
    )

    response = proxy.put("/api/community/presence", json={"status": "invisible", "extra": "ignored"})
    assert response.status_code == 200
    assert response.json() == {"status": "invisible"}
    assert seen[0].url.path == "/api/users/me/presence"
    assert seen[0].read().decode() == '{"status":"invisible"}'
    assert proxy.put("/api/community/presence", json={"status": "offline"}).status_code == 400
