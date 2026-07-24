"""Unit tests for Desktop community OAuth (neko-servers-desktop PKCE)."""

from __future__ import annotations

import base64
import hashlib
import json
import threading
import time
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
    monkeypatch.setattr(O, "_oauth_pending_path", lambda: pending)
    monkeypatch.setattr(C, "_social_base_url", lambda: "https://community.example")

    async def fake_exchange(**_kwargs):
        return {"access_token": "access", "refresh_token": "refresh"}

    async def fake_bootstrap(_social_base, _access_token):
        return {"user": {"id": USER_ID}}

    async def fake_bind(_social_base, _access_token):
        return {"bound": True, "error": None}

    worker_threads = []

    def save_auth(_payload):
        worker_threads.append(threading.get_ident())
        return True

    def save_social(*_args, **_kwargs):
        worker_threads.append(threading.get_ident())
        return True

    monkeypatch.setattr(O, "_exchange_oauth_code", fake_exchange)
    monkeypatch.setattr(O, "_bootstrap_session", fake_bootstrap)
    monkeypatch.setattr(O, "_oauth_guest_bind", fake_bind)
    monkeypatch.setattr(C, "_save_auth", save_auth)
    monkeypatch.setattr(C, "_save_social_session", save_social)

    event_loop_thread = threading.get_ident()
    response = await O._handle_oauth_callback("auth-code", "expected-state")

    assert response.status_code == 200
    assert len(worker_threads) == 2
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

    monkeypatch.setattr(O.httpx, "AsyncClient", _FakeAsyncClient)
    monkeypatch.setattr(C, "_get_client_credentials", lambda: ("local-client", "local-proof"))

    bind = await O._oauth_guest_bind("https://community.example", "platform-access")

    assert bind == {"bound": False, "error": "invalid_client_binding_challenge"}


@pytest.mark.unit
def test_legacy_login_returns_410(oauth_app):
    client, _auth, _social, _pending = oauth_app
    response = client.post(
        "/api/card-drop/login",
        json={"email": "a@example.com", "password": "secret"},
    )
    assert response.status_code == 410
    assert response.json() == {"detail": "legacy_community_login_removed"}
