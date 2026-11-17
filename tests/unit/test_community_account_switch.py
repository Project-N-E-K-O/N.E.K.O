"""Switching community accounts on a device bound to another account (#3747)."""

from __future__ import annotations

import base64
import hashlib
import json
import re
import time
from types import SimpleNamespace

import httpx
import pytest

import main_routers.card_drop_router as C
import main_routers.community_oauth as O
from main_logic.facts_sync import sync_worker
from tests.unit.test_community_oauth import USER_ID, _local_source_request, oauth_app  # noqa: F401

pytestmark = pytest.mark.unit

CONFLICT = {"bound": False, "error": "client_already_bound_to_other_user"}


def _pending_login(tmp_path, monkeypatch):
    pending = tmp_path / "community_oauth_pending.json"
    auth = tmp_path / "community_auth.json"
    social = tmp_path / "social_session.json"
    monkeypatch.setattr(O, "_oauth_pending_path", lambda: pending)
    monkeypatch.setattr(C, "_auth_path", lambda: auth)
    monkeypatch.setattr(C, "_social_session_path", lambda: social)
    monkeypatch.setattr(C, "_legacy_social_session_path", lambda: social)
    monkeypatch.setattr(C, "_social_base_url", lambda: "https://community.example")
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

    async def fake_exchange(**_kwargs):
        return {"access_token": "account-b-access", "refresh_token": "account-b-refresh"}

    async def fake_bootstrap(_social_base, _access_token):
        return {"user": {"id": USER_ID, "display_name": "Account B"}}

    monkeypatch.setattr(O, "_exchange_oauth_code", fake_exchange)
    monkeypatch.setattr(O, "_bootstrap_session", fake_bootstrap)
    return pending, auth, social


async def test_callback_saves_login_when_device_is_bound_to_another_account(tmp_path, monkeypatch):
    pending, auth, social = _pending_login(tmp_path, monkeypatch)
    rebind_flags: list[bool] = []

    async def fake_bind(_social_base, _access_token, *, rebind=False):
        rebind_flags.append(rebind)
        return dict(CONFLICT)

    async def unexpected_revoke(**_kwargs):
        raise AssertionError("a completed login must keep its tokens")

    monkeypatch.setattr(O, "_oauth_guest_bind", fake_bind)
    monkeypatch.setattr(O, "_revoke_tokens_best_effort", unexpected_revoke)

    response = await O._handle_oauth_callback("auth-code", "expected-state")

    assert response.status_code == 200
    text = response.body.decode("utf-8")
    assert "社区登录已完成" in text
    assert "登录冲突" not in text
    assert 'id="rebind"' in text
    assert "/api/card-drop/oauth/rebind-client" in text
    assert "window.confirm(" in text
    assert "confirm:true" in text
    # The page's only script is the hashed rebind action; tokens never reach it.
    csp = response.headers["content-security-policy"]
    assert f"script-src 'sha256-{O._REBIND_SCRIPT_HASH}'" in csp
    assert "connect-src 'self'" in csp
    assert "account-b-access" not in text
    # Login never moves the device on its own.
    assert rebind_flags == [False]
    assert not pending.exists()
    saved_auth = json.loads(auth.read_text(encoding="utf-8"))
    assert saved_auth["access_token"] == "account-b-access"
    assert saved_auth["local_user_id"] == USER_ID
    assert saved_auth["bind"] == CONFLICT
    saved_social = json.loads(social.read_text(encoding="utf-8"))
    assert saved_social["token"] == "account-b-access"
    assert saved_social["local_user_id"] == USER_ID


@pytest.mark.parametrize(
    "auth,expected",
    [
        ({"bind": CONFLICT}, CONFLICT),
        ({"bind": {"bound": True, "error": None}}, {"bound": True, "error": None}),
        # Older sessions predate the bind field.
        ({}, {"bound": True, "error": None}),
        # Internal fields never leave the backend.
        (
            {"bind": {"bound": False, "error": "cloud_unreachable", "skipped": "x", "token": "t"}},
            {"bound": False, "error": "cloud_unreachable"},
        ),
    ],
)
async def test_oauth_status_and_auth_status_report_the_same_bind(monkeypatch, auth, expected):
    monkeypatch.setattr(C, "_local_request_source_allowed", lambda _request: True)
    monkeypatch.setattr(C, "_social_session_paths", lambda: [])

    async def resolve_logged_in():
        return {
            "logged_in": True,
            "snapshot": {"access_token": "a", "auth_source": "oauth", "local_user_id": USER_ID},
            "auth": {"user": {"display_name": "B"}, **auth},
        }

    monkeypatch.setattr(O, "resolve_saved_oauth_status", resolve_logged_in)

    oauth_status = await O.oauth_status_endpoint(_local_source_request())
    auth_status = await C.auth_status_endpoint(_local_source_request())

    assert oauth_status["bind"] == expected
    assert auth_status["bind"] == expected


async def test_oauth_status_reports_no_bind_when_signed_out(monkeypatch):
    monkeypatch.setattr(C, "_local_request_source_allowed", lambda _request: True)
    monkeypatch.setattr(C, "_social_session_paths", lambda: [])

    async def resolve_not_logged_in():
        return {"logged_in": False, "snapshot": {}, "auth": {}}

    monkeypatch.setattr(O, "resolve_saved_oauth_status", resolve_not_logged_in)

    result = await O.oauth_status_endpoint(_local_source_request())
    assert result["logged_in"] is False
    assert result["bind"] is None


# ------------------------------------------------------------ explicit rebind


def _save_login(auth, social, bind):
    auth.write_text(
        json.dumps(
            {
                "access_token": "account-b-access",
                "local_user_id": USER_ID,
                "auth_source": "oauth",
                "bind": bind,
            }
        ),
        encoding="utf-8",
    )
    social.write_text(
        json.dumps({"token": "account-b-access", "local_user_id": USER_ID, "auth_source": "oauth"}),
        encoding="utf-8",
    )


def _logged_in(monkeypatch, *, auth_source="oauth"):
    async def resolve():
        return {
            "logged_in": True,
            "snapshot": {
                "access_token": "account-b-access",
                "auth_source": auth_source,
                "local_user_id": USER_ID,
            },
            "auth": {},
        }

    monkeypatch.setattr(O, "resolve_saved_oauth_status", resolve)


def test_rebind_requires_explicit_confirmation(oauth_app, monkeypatch):  # noqa: F811
    client, _auth, _social, _pending = oauth_app

    async def unexpected(*_args, **_kwargs):
        raise AssertionError("an unconfirmed request must not contact the cloud")

    monkeypatch.setattr(O, "_oauth_guest_bind", unexpected)
    for body in (None, {}, {"confirm": False}):
        response = client.post("/api/card-drop/oauth/rebind-client", json=body)
        assert response.status_code == 400
        assert response.json() == {"detail": "confirmation_required"}


def test_rebind_rejects_cross_site_browser_requests(oauth_app, monkeypatch):  # noqa: F811
    client, _auth, _social, _pending = oauth_app

    async def unexpected(*_args, **_kwargs):
        raise AssertionError("cross-site requests must not reach the cloud")

    monkeypatch.setattr(O, "_oauth_guest_bind", unexpected)
    response = client.post(
        "/api/card-drop/oauth/rebind-client",
        json={"confirm": True},
        headers={"Origin": "https://evil.example", "Sec-Fetch-Site": "cross-site"},
    )
    assert response.status_code == 403


def test_rebind_needs_a_desktop_oauth_login(oauth_app, monkeypatch):  # noqa: F811
    client, _auth, _social, _pending = oauth_app

    async def unexpected(*_args, **_kwargs):
        raise AssertionError("no login means nothing to rebind to")

    monkeypatch.setattr(O, "_oauth_guest_bind", unexpected)

    async def signed_out():
        return {"logged_in": False, "snapshot": {}, "auth": {}}

    monkeypatch.setattr(O, "resolve_saved_oauth_status", signed_out)
    response = client.post("/api/card-drop/oauth/rebind-client", json={"confirm": True})
    assert response.status_code == 409
    assert response.json() == {"detail": "desktop_login_required"}

    _logged_in(monkeypatch, auth_source="legacy")
    response = client.post("/api/card-drop/oauth/rebind-client", json={"confirm": True})
    assert response.status_code == 409
    assert response.json() == {"detail": "oauth_session_required"}


def test_rebind_runs_local_proof_flow_and_updates_saved_bind(oauth_app, monkeypatch):  # noqa: F811
    client, auth, social, _pending = oauth_app
    _save_login(auth, social, CONFLICT)
    _logged_in(monkeypatch)
    requests: list[httpx.Request] = []

    def handle(request):
        requests.append(request)
        path = request.url.path
        if path == "/api/auth/bind-client/challenge":
            return httpx.Response(200, json={"binding_challenge": "C" * 43})
        if path == "/api/clients/bind-approval":
            return httpx.Response(204)
        if path == "/api/auth/bind-client":
            return httpx.Response(200, json={"client_id": "local-client"})
        raise AssertionError(f"unexpected {request.method} {request.url}")

    transport = httpx.MockTransport(handle)
    real_client = httpx.AsyncClient
    monkeypatch.setattr(O.httpx, "AsyncClient", lambda **kwargs: real_client(transport=transport))

    response = client.post("/api/card-drop/oauth/rebind-client", json={"confirm": True})

    assert response.status_code == 200
    assert response.json() == {"ok": True, "bind": {"bound": True, "error": None}}
    assert response.headers["cache-control"] == "no-store"
    paths = [r.url.path for r in requests]
    assert paths == [
        "/api/auth/bind-client/challenge",
        "/api/clients/bind-approval",
        "/api/auth/bind-client",
    ]
    approval = json.loads(requests[1].content)
    assert approval == {
        "client_id": "local-client",
        "client_proof": "local-proof",
        "binding_challenge": "C" * 43,
    }
    # The bearer only goes to the cloud; the approval call carries no account token.
    assert requests[0].headers["Authorization"] == "Bearer account-b-access"
    assert "Authorization" not in requests[1].headers
    assert json.loads(requests[2].content) == {
        "client_id": "local-client",
        "binding_challenge": "C" * 43,
        "rebind": True,
    }
    saved = json.loads(auth.read_text(encoding="utf-8"))
    assert saved["bind"] == {"bound": True, "error": None}
    assert saved["access_token"] == "account-b-access"


@pytest.mark.parametrize(
    "cloud_status,cloud_detail,expected_status",
    [
        (403, "invalid_client_binding_challenge", 409),
        (409, "client_binding_limit_reached", 409),
    ],
)
def test_rebind_failure_keeps_saved_bind_and_reports_reason(
    oauth_app, monkeypatch, cloud_status, cloud_detail, expected_status,  # noqa: F811
):
    client, auth, social, _pending = oauth_app
    _save_login(auth, social, CONFLICT)
    _logged_in(monkeypatch)

    def handle(request):
        path = request.url.path
        if path == "/api/auth/bind-client/challenge":
            return httpx.Response(200, json={"binding_challenge": "C" * 43})
        if path == "/api/clients/bind-approval":
            return httpx.Response(204)
        if path == "/api/auth/bind-client":
            return httpx.Response(cloud_status, json={"detail": cloud_detail})
        raise AssertionError(f"unexpected {request.method} {request.url}")

    transport = httpx.MockTransport(handle)
    real_client = httpx.AsyncClient
    monkeypatch.setattr(O.httpx, "AsyncClient", lambda **kwargs: real_client(transport=transport))

    response = client.post("/api/card-drop/oauth/rebind-client", json={"confirm": True})

    assert response.status_code == expected_status
    assert response.json() == {
        "detail": cloud_detail,
        "bind": {"bound": False, "error": cloud_detail},
    }
    assert json.loads(auth.read_text(encoding="utf-8"))["bind"] == CONFLICT


async def test_login_bind_never_sends_rebind(monkeypatch):
    bodies: list[dict] = []

    def handle(request):
        path = request.url.path
        if path == "/api/auth/bind-client/challenge":
            return httpx.Response(200, json={"binding_challenge": "C" * 43})
        if path == "/api/clients/bind-approval":
            return httpx.Response(204)
        if path == "/api/auth/bind-client":
            bodies.append(json.loads(request.content))
            return httpx.Response(409, json={"detail": "client_already_bound_to_other_user"})
        raise AssertionError(f"unexpected {request.method} {request.url}")

    client = httpx.AsyncClient(transport=httpx.MockTransport(handle))
    try:
        monkeypatch.setattr(O.httpx, "AsyncClient", lambda **kwargs: client)
        monkeypatch.setattr(C, "_get_client_credentials", lambda: ("local-client", "P" * 43))
        bind = await O._oauth_guest_bind("https://community.example", "platform-access")
    finally:
        await client.aclose()

    assert bind == CONFLICT
    assert bodies == [{"client_id": "local-client", "binding_challenge": "C" * 43}]


# ------------------------------------------------------------ facts sync guard


@pytest.mark.parametrize(
    "bind,holds",
    [
        (CONFLICT, True),
        ({"bound": True, "error": None}, False),
        ({"bound": False, "error": "cloud_unreachable"}, False),
        (None, False),
    ],
)
async def test_facts_sync_holds_while_device_belongs_to_another_account(
    tmp_path, monkeypatch, bind, holds,
):
    memory_dir = tmp_path / "memory"
    (memory_dir / "Neko").mkdir(parents=True)
    auth_file = tmp_path / "community_auth.json"
    if bind is not None:
        auth_file.write_text(json.dumps({"bind": bind}), encoding="utf-8")
    monkeypatch.setattr(
        sync_worker, "get_config_manager", lambda: SimpleNamespace(memory_dir=str(memory_dir)),
    )
    monkeypatch.setattr(sync_worker, "_social_base_url", lambda: "https://community.example")
    monkeypatch.setattr(sync_worker, "_get_client_credentials", lambda: ("local-client", "P" * 43))
    registered: list[str] = []
    swept: list[str] = []

    async def ensure_registered(*_args):
        registered.append("register")
        return True

    async def sync_one(_memory_dir, lanlan_dir, *_args):
        swept.append(lanlan_dir.name)

    monkeypatch.setattr(sync_worker, "_ensure_client_registered", ensure_registered)
    monkeypatch.setattr(sync_worker, "_sync_one_lanlan", sync_one)

    await sync_worker._sweep_once()

    if holds:
        assert registered == []
        assert swept == []
    else:
        assert registered == ["register"]
        assert swept == ["Neko"]


def test_conflict_page_script_matches_its_csp_hash():
    page = O._ownership_conflict_callback_html().body.decode("utf-8")
    scripts = re.findall(r"<script>(.*?)</script>", page, re.S)
    assert len(scripts) == 1
    digest = base64.b64encode(hashlib.sha256(scripts[0].encode()).digest()).decode()
    assert digest == O._REBIND_SCRIPT_HASH
