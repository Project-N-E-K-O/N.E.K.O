"""Unit tests for Desktop community OAuth (neko-servers-desktop PKCE)."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import threading
import time
from types import SimpleNamespace
from urllib.parse import parse_qs, urlparse

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import main_routers.card_drop_router as C
import main_routers.community_oauth as O


USER_ID = "11111111-1111-4111-8111-111111111111"


@pytest.fixture
def oauth_app(tmp_path, monkeypatch):
    auth = tmp_path / "community_auth.json"
    social = tmp_path / "social_session.json"
    pending = tmp_path / "community_oauth_pending.json"
    monkeypatch.setenv("NEKO_SOCIAL_BASE_URL", "https://community.example")
    monkeypatch.setenv("NEKO_AUTH_URL", "https://auth.example")
    monkeypatch.setenv("NEKO_SERVERS_DESKTOP_CLIENT_ID", "neko-servers-desktop-dev")
    monkeypatch.setattr(C, "_auth_path", lambda: auth)
    monkeypatch.setattr(C, "_social_session_path", lambda: social)
    monkeypatch.setattr(C, "_legacy_social_session_path", lambda: social)
    monkeypatch.setattr(O, "_oauth_pending_path", lambda: pending)
    monkeypatch.setattr(O, "_main_server_port", lambda: 48911)
    monkeypatch.setattr(C, "_get_client_credentials", lambda: ("local-client", "local-proof"))

    app = FastAPI()
    app.include_router(C.router)
    app.include_router(O.router)
    app.include_router(O.callback_router)
    return TestClient(app), auth, social, pending


@pytest.mark.unit
def test_desktop_client_id_is_owned_here_and_rejects_plugin_market_client(monkeypatch):
    """This module owns the community PKCE client id and rejects the Market one."""
    # plugin/settings.py 的 NEKO_AUTH_CLIENT_ID（默认 neko-desktop）是插件市场的
    # public client，在 neko-auth-platform 上与社区桌面端是两个不同的注册。误把它
    # 配到 NEKO_SERVERS_DESKTOP_CLIENT_ID 上会让授权请求指向错误的 client，所以
    # 这里必须回落到 servers-desktop 默认值而不是照用。
    #
    # 单一真相源：main_routers 是 L3、plugin 是 L4，前者不能 import 后者，所以这个
    # 值不在 plugin/settings.py 里另立常量（那份曾经存在但无人消费）。
    monkeypatch.setenv("NEKO_SERVERS_DESKTOP_CLIENT_ID", "neko-desktop")
    assert O._desktop_client_id() == "neko-servers-desktop-prod"

    monkeypatch.setenv("NEKO_SERVERS_DESKTOP_CLIENT_ID", "  ")
    assert O._desktop_client_id() == "neko-servers-desktop-prod"

    monkeypatch.delenv("NEKO_SERVERS_DESKTOP_CLIENT_ID", raising=False)
    assert O._desktop_client_id() == "neko-servers-desktop-prod"

    monkeypatch.setenv("NEKO_SERVERS_DESKTOP_CLIENT_ID", "neko-servers-desktop-prod")
    assert O._desktop_client_id() == "neko-servers-desktop-prod"


@pytest.mark.unit
def test_oauth_start_returns_desktop_pkce_auth_url(oauth_app):
    client, _auth, _social, pending = oauth_app
    response = client.post("/api/card-drop/oauth/start")
    assert response.status_code == 200
    body = response.json()
    assert body["expires_in"] == O._OAUTH_PENDING_TTL_SEC
    assert body["state"]
    assert pending.exists()

    parsed = urlparse(body["auth_url"])
    assert parsed.scheme == "https"
    assert parsed.netloc == "auth.example"
    assert parsed.path == "/oauth2/auth"
    query = parse_qs(parsed.query)
    assert query["client_id"] == ["neko-servers-desktop-dev"]
    assert query["code_challenge_method"] == ["S256"]
    pending_data = json.loads(pending.read_text(encoding="utf-8"))
    expected_challenge = base64.urlsafe_b64encode(
        hashlib.sha256(pending_data["code_verifier"].encode("ascii")).digest()
    ).rstrip(b"=").decode("ascii")
    assert query["code_challenge"] == [expected_challenge]
    assert query["response_type"] == ["code"]
    assert "openid" in query["scope"][0]
    redirect_uri = query["redirect_uri"][0]
    assert "127.0.0.1" in redirect_uri
    assert redirect_uri.endswith("/oauth/callback")
    assert "neko-desktop" not in body["auth_url"]
    assert "/market/oauth/callback" not in body["auth_url"]


@pytest.mark.unit
def test_oauth_start_reuses_live_pending_pkce_attempt(oauth_app):
    client, _auth, _social, pending = oauth_app

    first = client.post("/api/card-drop/oauth/start")
    pending_before = pending.read_text(encoding="utf-8")
    second = client.post("/api/card-drop/oauth/start")

    assert first.status_code == 200
    assert second.status_code == 200
    assert second.json()["state"] == first.json()["state"]
    assert second.json()["auth_url"] == first.json()["auth_url"]
    assert 0 < second.json()["expires_in"] <= O._OAUTH_PENDING_TTL_SEC
    assert pending.read_text(encoding="utf-8") == pending_before


@pytest.mark.unit
async def test_oauth_start_offloads_pending_write(tmp_path, monkeypatch):
    pending = tmp_path / "community_oauth_pending.json"
    worker_threads: list[int] = []

    def write_pending(path, payload):
        worker_threads.append(threading.get_ident())
        assert path == pending
        assert payload["state"]

    monkeypatch.setattr(C, "_local_request_source_allowed", lambda _request: True)
    monkeypatch.setattr(O, "_oauth_pending_path", lambda: pending)
    monkeypatch.setattr(C, "_write_private_json", write_pending)

    event_loop_thread = threading.get_ident()
    result = await O.oauth_start_endpoint(
        SimpleNamespace(url=SimpleNamespace(port=48911))
    )

    assert result["auth_url"]
    assert worker_threads
    assert all(thread_id != event_loop_thread for thread_id in worker_threads)


@pytest.mark.unit
async def test_oauth_status_offloads_session_reads(monkeypatch):
    worker_threads: list[int] = []

    def load_records():
        worker_threads.append(threading.get_ident())
        return (
            {"access_token": "access", "auth_source": "oauth"},
            {"user": {"display_name": "User", "email": "user@example.com"}},
        )

    monkeypatch.setattr(C, "_local_request_source_allowed", lambda _request: True)
    monkeypatch.setattr(O, "_load_oauth_status_records", load_records)

    async def lookup_identity(_base, _access):
        return C._CloudIdentityLookup(
            C._CloudIdentity(USER_ID, "oauth", {}),
            200,
        )

    monkeypatch.setattr(C, "_lookup_cloud_identity", lookup_identity)

    event_loop_thread = threading.get_ident()
    result = await O.oauth_status_endpoint(object())

    assert result["logged_in"] is True
    assert worker_threads
    assert all(thread_id != event_loop_thread for thread_id in worker_threads)


@pytest.mark.unit
async def test_oauth_status_refreshes_rejected_access_token(monkeypatch):
    old_snapshot = {
        "base_url": "https://community.example",
        "access_token": "expired-access",
        "refresh_token": "refresh-token",
        "local_user_id": USER_ID,
        "auth_source": "oauth",
        "auth_public_url": "https://auth.example",
        "client_id": "desktop-client",
    }
    refreshed_snapshot = {
        **old_snapshot,
        "access_token": "fresh-access",
        "refresh_token": "rotated-refresh",
    }
    reads = iter([
        (old_snapshot, {"access_token": "expired-access"}),
        (refreshed_snapshot, {"access_token": "fresh-access"}),
    ])

    async def rejected_identity(_base, _access):
        return C._CloudIdentityLookup(None, 401, "rejected")

    async def refresh_token(**kwargs):
        assert kwargs == {
            "refresh_token": "refresh-token",
            "client_id": "desktop-client",
            "auth_public_url": "https://auth.example",
        }
        return "ok", {
            "access_token": "fresh-access",
            "refresh_token": "rotated-refresh",
        }

    saved: list[tuple[dict, str, str]] = []

    def persist(expected, access, refresh):
        saved.append((expected, access, refresh))
        return True

    monkeypatch.setattr(O, "_load_oauth_status_records", lambda: next(reads))
    monkeypatch.setattr(C, "_lookup_cloud_identity", rejected_identity)
    monkeypatch.setattr(O, "_refresh_oauth_token", refresh_token)
    monkeypatch.setattr(O, "_persist_refreshed_oauth_tokens", persist)

    status = await O.resolve_saved_oauth_status()

    assert status["logged_in"] is True
    assert status["snapshot"]["access_token"] == "fresh-access"
    assert saved == [(old_snapshot, "fresh-access", "rotated-refresh")]


@pytest.mark.unit
async def test_oauth_status_serializes_concurrent_rotating_refreshes(monkeypatch):
    old_snapshot = {
        "base_url": "https://community.example",
        "access_token": "expired-access",
        "refresh_token": "refresh-token",
        "local_user_id": USER_ID,
        "auth_source": "oauth",
        "auth_public_url": "https://auth.example",
        "client_id": "desktop-client",
    }
    refreshed_snapshot = {
        **old_snapshot,
        "access_token": "fresh-access",
        "refresh_token": "rotated-refresh",
    }
    current = {
        "snapshot": old_snapshot,
        "auth": {"access_token": "expired-access"},
    }
    refresh_calls = 0

    def load_records():
        return current["snapshot"], current["auth"]

    async def lookup_identity(_base, access):
        await asyncio.sleep(0)
        if access == "fresh-access":
            return C._CloudIdentityLookup(
                C._CloudIdentity(USER_ID, "oauth", {}),
                200,
            )
        return C._CloudIdentityLookup(None, 401, "rejected")

    async def refresh_token(**_kwargs):
        nonlocal refresh_calls
        refresh_calls += 1
        await asyncio.sleep(0)
        return "ok", {
            "access_token": "fresh-access",
            "refresh_token": "rotated-refresh",
        }

    def persist(_expected, _access, _refresh):
        current["snapshot"] = refreshed_snapshot
        current["auth"] = {"access_token": "fresh-access"}
        return True

    monkeypatch.setattr(O, "_load_oauth_status_records", load_records)
    monkeypatch.setattr(C, "_lookup_cloud_identity", lookup_identity)
    monkeypatch.setattr(O, "_refresh_oauth_token", refresh_token)
    monkeypatch.setattr(O, "_persist_refreshed_oauth_tokens", persist)

    first, second = await asyncio.gather(
        O.resolve_saved_oauth_status(),
        O.resolve_saved_oauth_status(),
    )

    assert first["logged_in"] is True
    assert second["logged_in"] is True
    assert refresh_calls == 1


@pytest.mark.unit
@pytest.mark.parametrize(
    ("status_code", "payload", "expected_outcome"),
    [
        (408, {"error": "invalid_grant"}, "unavailable"),
        (425, {"error": "temporarily_unavailable"}, "unavailable"),
        (429, {"error": "invalid_grant"}, "unavailable"),
        (400, {"error": "temporarily_unavailable"}, "unavailable"),
        (400, {"error": "invalid_grant"}, "rejected"),
    ],
)
async def test_oauth_refresh_rejects_only_definitive_invalid_grant(
    monkeypatch,
    status_code,
    payload,
    expected_outcome,
):
    class FakeResponse:
        def json(self):
            return payload

    class FakeAsyncClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def post(self, *_args, **_kwargs):
            response = FakeResponse()
            response.status_code = status_code
            return response

    monkeypatch.setattr(O.httpx, "AsyncClient", FakeAsyncClient)

    outcome, response_payload = await O._refresh_oauth_token(
        refresh_token="refresh-token",
        client_id="desktop-client",
        auth_public_url="https://auth.example",
    )

    assert outcome == expected_outcome
    assert response_payload == (payload if expected_outcome == "rejected" else None)


@pytest.mark.unit
async def test_oauth_status_clears_only_rejected_unrefreshable_snapshot(monkeypatch):
    snapshot = {
        "base_url": "https://community.example",
        "access_token": "expired-access",
        "refresh_token": None,
        "local_user_id": USER_ID,
        "auth_source": "oauth",
    }

    async def rejected_identity(_base, _access):
        return C._CloudIdentityLookup(None, 401, "rejected")

    cleared: list[dict] = []
    monkeypatch.setattr(
        O,
        "_load_oauth_status_records",
        lambda: (snapshot, {"access_token": "expired-access"}),
    )
    monkeypatch.setattr(C, "_lookup_cloud_identity", rejected_identity)
    monkeypatch.setattr(
        O,
        "_clear_rejected_oauth_snapshot",
        lambda expected: cleared.append(expected) or True,
    )

    status = await O.resolve_saved_oauth_status()

    assert status["logged_in"] is False
    assert cleared == [snapshot]


@pytest.mark.unit
async def test_oauth_status_reports_rejected_snapshot_when_cleanup_fails(monkeypatch):
    snapshot = {
        "base_url": "https://community.example",
        "access_token": "expired-access",
        "refresh_token": None,
        "local_user_id": USER_ID,
        "auth_source": "oauth",
    }
    auth = {"access_token": "expired-access"}

    async def rejected_identity(_base, _access):
        return C._CloudIdentityLookup(None, 401, "rejected")

    monkeypatch.setattr(O, "_load_oauth_status_records", lambda: (snapshot, auth))
    monkeypatch.setattr(C, "_lookup_cloud_identity", rejected_identity)
    monkeypatch.setattr(O, "_clear_rejected_oauth_snapshot", lambda _expected: False)

    status = await O.resolve_saved_oauth_status()

    assert status == {
        "logged_in": False,
        "snapshot": snapshot,
        "auth": auth,
    }


@pytest.mark.unit
async def test_oauth_logout_offloads_local_file_operations(monkeypatch):
    worker_threads: list[int] = []

    def record(value):
        def operation(*_args, **_kwargs):
            worker_threads.append(threading.get_ident())
            return value

        return operation

    async def no_revoke(**_kwargs):
        return None

    monkeypatch.setattr(C, "_local_request_source_allowed", lambda _request: True)
    monkeypatch.setattr(
        O,
        "_load_oauth_logout_records",
        record(({"access_token": "access"}, {}, {})),
    )
    monkeypatch.setattr(O, "_revoke_tokens_best_effort", no_revoke)
    monkeypatch.setattr(O, "_unlink_pending", record(None))
    monkeypatch.setattr(C, "_clear_auth", record(True))

    event_loop_thread = threading.get_ident()
    result = await O.oauth_logout_endpoint(object())

    assert result == {"ok": True}
    assert len(worker_threads) == 3
    assert all(thread_id != event_loop_thread for thread_id in worker_threads)


@pytest.mark.unit
@pytest.mark.parametrize(
    ("snapshot_url", "auth_url", "social_url", "expected_url"),
    [
        (
            "https://snapshot-auth.example/",
            "https://auth-mirror.example",
            "https://social-auth.example",
            "https://snapshot-auth.example",
        ),
        (
            "",
            "https://auth-mirror.example/",
            "https://social-auth.example",
            "https://auth-mirror.example",
        ),
        (
            "",
            "",
            "https://social-auth.example/",
            "https://social-auth.example",
        ),
        ("", "", "", "https://current-auth.example"),
    ],
)
async def test_oauth_logout_revokes_against_saved_issuer(
    monkeypatch,
    snapshot_url,
    auth_url,
    social_url,
    expected_url,
):
    revoked: list[dict] = []

    async def capture_revoke(**kwargs):
        revoked.append(kwargs)

    monkeypatch.setattr(C, "_local_request_source_allowed", lambda _request: True)
    monkeypatch.setattr(
        O,
        "_load_oauth_logout_records",
        lambda: (
            {
                "access_token": "access",
                "refresh_token": "refresh",
                "auth_public_url": snapshot_url,
            },
            {
                "client_id": "desktop-client",
                "auth_public_url": auth_url,
            },
            {"auth_public_url": social_url},
        ),
    )
    monkeypatch.setattr(O, "_auth_public_url", lambda: "https://current-auth.example")
    monkeypatch.setattr(O, "_revoke_tokens_best_effort", capture_revoke)
    monkeypatch.setattr(O, "_unlink_pending", lambda: None)
    monkeypatch.setattr(C, "_clear_auth", lambda: True)

    result = await O.oauth_logout_endpoint(object())

    assert result == {"ok": True}
    assert revoked == [
        {
            "access_token": "access",
            "refresh_token": "refresh",
            "client_id": "desktop-client",
            "auth_public_url": expected_url,
        }
    ]


@pytest.mark.unit
def test_oauth_callback_rejects_bad_state(oauth_app):
    client, _auth, _social, pending = oauth_app
    start = client.post("/api/card-drop/oauth/start")
    assert start.status_code == 200
    assert pending.exists()

    response = client.get(
        "/oauth/callback",
        params={"code": "auth-code", "state": "not-the-real-state"},
    )
    assert response.status_code == 400
    assert "state" in response.text.lower() or "校验" in response.text
    assert pending.exists()


@pytest.mark.unit
@pytest.mark.parametrize(
    "callback_path",
    ["/oauth/callback", "/api/card-drop/oauth/callback"],
)
def test_oauth_callback_access_denied_returns_html_and_clears_pending(
    oauth_app, callback_path
):
    client, _auth, _social, pending = oauth_app
    start = client.post("/api/card-drop/oauth/start")
    assert start.status_code == 200

    response = client.get(
        callback_path,
        params={"error": "access_denied", "state": start.json()["state"]},
    )

    assert response.status_code == 400
    assert response.headers["content-type"].startswith("text/html")
    assert "登录已取消" in response.text
    assert not pending.exists()


@pytest.mark.unit
def test_oauth_logout_reports_local_credential_clear_failure(oauth_app, monkeypatch):
    client, _auth, _social, pending = oauth_app
    pending.write_text("{}", encoding="utf-8")

    async def no_revoke(**_kwargs):
        return None

    monkeypatch.setattr(O, "_revoke_tokens_best_effort", no_revoke)
    monkeypatch.setattr(C, "_clear_auth", lambda: False)

    response = client.post("/api/card-drop/oauth/logout")

    assert response.status_code == 500
    assert response.json() == {"detail": "local_clear_failed"}
    assert not pending.exists()


@pytest.mark.unit
def test_oauth_callback_success_persists_social_session(oauth_app, monkeypatch):
    client, auth, social, pending = oauth_app
    start = client.post("/api/card-drop/oauth/start")
    assert start.status_code == 200
    state = start.json()["state"]

    class _FakeResponse:
        def __init__(self, status_code: int, payload: dict | None = None):
            self.status_code = status_code
            self._payload = payload or {}

        def json(self):
            return self._payload

    class _FakeAsyncClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def post(self, url, **kwargs):
            if url.endswith("/oauth2/token"):
                data = kwargs.get("data") or {}
                assert data["grant_type"] == "authorization_code"
                assert data["client_id"] == "neko-servers-desktop-dev"
                assert data["code"] == "auth-code"
                assert data["code_verifier"]
                assert "127.0.0.1" in data["redirect_uri"]
                assert data["redirect_uri"].endswith("/oauth/callback")
                return _FakeResponse(
                    200,
                    {
                        "access_token": "platform-access",
                        "refresh_token": "platform-refresh",
                        "expires_in": 3600,
                    },
                )
            if url.endswith("/api/auth/session/bootstrap"):
                headers = kwargs.get("headers") or {}
                assert headers.get("Authorization") == "Bearer platform-access"
                return _FakeResponse(
                    200,
                    {
                        "created": True,
                        "user": {
                            "id": USER_ID,
                            "display_name": "OAuth User",
                            "email": "oauth@example.com",
                            "auth_source": "oauth",
                        },
                    },
                )
            if url.endswith("/api/auth/bind-client/challenge"):
                return _FakeResponse(
                    200,
                    {"binding_challenge": "C" * 43, "expires_in": 120},
                )
            if url.endswith("/api/clients/bind-approval"):
                return _FakeResponse(204)
            if url.endswith("/api/auth/bind-client"):
                return _FakeResponse(200, {"client_id": "local-client"})
            raise AssertionError(f"unexpected POST {url}")

    monkeypatch.setattr(O.httpx, "AsyncClient", _FakeAsyncClient)

    response = client.get(
        "/oauth/callback",
        params={"code": "auth-code", "state": state},
    )
    assert response.status_code == 200
    assert "社区登录已完成" in response.text
    assert not pending.exists()

    saved_social = json.loads(social.read_text(encoding="utf-8"))
    assert saved_social["schema_version"] == 2
    assert saved_social["token"] == "platform-access"
    assert saved_social["access_token"] == "platform-access"
    assert saved_social["refresh_token"] == "platform-refresh"
    assert saved_social["auth_source"] == "oauth"
    assert saved_social["auth_public_url"] == "https://auth.example"
    assert saved_social["client_id"] == "neko-servers-desktop-dev"
    assert saved_social["local_user_id"] == USER_ID
    assert saved_social["baseUrl"] == "https://community.example"

    saved_auth = json.loads(auth.read_text(encoding="utf-8"))
    assert saved_auth["auth_source"] == "oauth"
    assert saved_auth["client_id"] == "neko-servers-desktop-dev"
    assert saved_auth["local_user_id"] == USER_ID
    assert saved_auth["bind"]["bound"] is True


@pytest.mark.unit
async def test_oauth_callback_offloads_credential_writes(tmp_path, monkeypatch):
    pending = tmp_path / "community_oauth_pending.json"
    pending.write_text(
        json.dumps(
            {
                "state": "expected-state",
                "code_verifier": "verifier",
                "redirect_uri": "http://127.0.0.1:48911/oauth/callback",
                "client_id": "neko-servers-desktop-dev",
                "auth_public_url": "https://auth.example",
                "expires_at": time.time() + 60,
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(C, "_social_base_url", lambda: "https://community.example")

    async def fake_exchange(**_kwargs):
        return {"access_token": "access", "refresh_token": "refresh"}

    async def fake_bootstrap(_social_base, _access_token):
        return {"user": {"id": USER_ID}}

    async def fake_bind(_social_base, _access_token):
        return {"bound": True, "error": None}

    worker_threads = []

    def load_pending():
        worker_threads.append(threading.get_ident())
        return pending, json.loads(pending.read_text(encoding="utf-8"))

    def unlink_pending():
        worker_threads.append(threading.get_ident())
        pending.unlink(missing_ok=True)

    def save_auth(_payload):
        worker_threads.append(threading.get_ident())
        return True

    def save_social(*_args, **_kwargs):
        worker_threads.append(threading.get_ident())
        return True

    monkeypatch.setattr(O, "_exchange_oauth_code", fake_exchange)
    monkeypatch.setattr(O, "_bootstrap_session", fake_bootstrap)
    monkeypatch.setattr(O, "_oauth_guest_bind", fake_bind)
    monkeypatch.setattr(O, "_load_oauth_pending", load_pending)
    monkeypatch.setattr(O, "_unlink_pending", unlink_pending)
    monkeypatch.setattr(C, "_save_auth", save_auth)
    monkeypatch.setattr(C, "_save_social_session", save_social)

    event_loop_thread = threading.get_ident()
    response = await O._handle_oauth_callback("auth-code", "expected-state")

    assert response.status_code == 200
    assert len(worker_threads) == 4
    assert all(thread_id != event_loop_thread for thread_id in worker_threads)


@pytest.mark.unit
@pytest.mark.parametrize("with_existing_session", [False, True])
async def test_oauth_callback_rolls_back_partial_credential_write(
    tmp_path,
    monkeypatch,
    with_existing_session,
):
    auth = tmp_path / "community_auth.json"
    social = tmp_path / "social_session.json"
    pending = tmp_path / "community_oauth_pending.json"
    old_auth = {"access_token": "old-access", "local_user_id": USER_ID}
    old_social = {
        "token": "old-access",
        "access_token": "old-access",
        "local_user_id": USER_ID,
        "auth_source": "oauth",
    }
    if with_existing_session:
        auth.write_text(json.dumps(old_auth), encoding="utf-8")
        social.write_text(json.dumps(old_social), encoding="utf-8")
    pending.write_text(
        json.dumps(
            {
                "state": "expected-state",
                "code_verifier": "verifier",
                "redirect_uri": "http://127.0.0.1:48911/oauth/callback",
                "client_id": "neko-servers-desktop-dev",
                "auth_public_url": "https://auth.example",
                "expires_at": time.time() + 60,
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(O, "_oauth_pending_path", lambda: pending)
    monkeypatch.setattr(C, "_auth_path", lambda: auth)
    monkeypatch.setattr(C, "_social_session_path", lambda: social)
    monkeypatch.setattr(C, "_social_base_url", lambda: "https://community.example")

    async def fake_exchange(**_kwargs):
        return {"access_token": "new-access", "refresh_token": "new-refresh"}

    async def fake_bootstrap(_social_base, _access_token):
        return {"user": {"id": USER_ID}}

    async def fake_bind(_social_base, _access_token):
        return {"bound": True, "error": None}

    def save_auth(payload):
        C._write_private_json(auth, payload)
        return True

    monkeypatch.setattr(O, "_exchange_oauth_code", fake_exchange)
    monkeypatch.setattr(O, "_bootstrap_session", fake_bootstrap)
    monkeypatch.setattr(O, "_oauth_guest_bind", fake_bind)
    monkeypatch.setattr(C, "_save_auth", save_auth)
    monkeypatch.setattr(C, "_save_social_session", lambda *_args, **_kwargs: False)

    response = await O._handle_oauth_callback("auth-code", "expected-state")

    assert response.status_code == 400
    assert not pending.exists()
    if with_existing_session:
        assert json.loads(auth.read_text(encoding="utf-8")) == old_auth
        assert json.loads(social.read_text(encoding="utf-8")) == old_social
    else:
        assert not auth.exists()
        assert not social.exists()


@pytest.mark.unit
@pytest.mark.parametrize("challenge_payload", [ValueError("invalid json"), []])
async def test_oauth_guest_bind_treats_malformed_challenge_as_best_effort(
    monkeypatch,
    challenge_payload,
):
    class _FakeResponse:
        status_code = 200

        def json(self):
            if isinstance(challenge_payload, Exception):
                raise challenge_payload
            return challenge_payload

    class _FakeAsyncClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def post(self, url, **kwargs):
            assert url.endswith("/api/auth/bind-client/challenge")
            return _FakeResponse()

    worker_threads: list[int] = []

    def load_client_credentials():
        worker_threads.append(threading.get_ident())
        return "local-client", "local-proof"

    monkeypatch.setattr(O.httpx, "AsyncClient", _FakeAsyncClient)
    monkeypatch.setattr(C, "_get_client_credentials", load_client_credentials)

    event_loop_thread = threading.get_ident()
    bind = await O._oauth_guest_bind("https://community.example", "platform-access")

    assert bind == {"bound": False, "error": "invalid_client_binding_challenge"}
    assert worker_threads
    assert all(thread_id != event_loop_thread for thread_id in worker_threads)


@pytest.mark.unit
def test_legacy_login_returns_410(oauth_app):
    client, _auth, _social, _pending = oauth_app
    response = client.post(
        "/api/card-drop/login",
        json={"email": "a@example.com", "password": "secret"},
    )
    assert response.status_code == 410
    assert response.json() == {"detail": "legacy_community_login_removed"}
