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
from utils import community_device_hold as hold
from tests.unit.test_community_oauth import USER_ID, _local_source_request, oauth_app  # noqa: F401

pytestmark = pytest.mark.unit

CONFLICT = {"bound": False, "error": "client_already_bound_to_other_user"}
BOUND = {"bound": True, "error": None}
OWNER_A = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"


@pytest.fixture(autouse=True)
def _reset_unrecorded_hold():
    hold._set_unrecorded_hold(False)
    yield
    hold._set_unrecorded_hold(False)


def _mark_conflict(directory) -> None:
    hold.record_bind_outcome(directory, dict(CONFLICT), USER_ID)


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
    assert hold.device_held(tmp_path, USER_ID)


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
    for body in (None, {}, {"confirm": False}, {"confirm": "false"}, {"confirm": "true"}, {"confirm": 1}):
        response = client.post("/api/card-drop/oauth/rebind-client", json=body)
        assert response.status_code == 400, body
        assert response.json() == {"detail": "confirmation_required"}
    # A non-object body is rejected by request validation before the handler.
    assert client.post("/api/card-drop/oauth/rebind-client", json=[True]).status_code == 422


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
    monkeypatch.setattr(C, "_auth_path", lambda: _auth)

    async def unexpected(*_args, **_kwargs):
        raise AssertionError("no login means nothing to rebind to")

    monkeypatch.setattr(O, "_oauth_guest_bind", unexpected)

    async def signed_out():
        return {"logged_in": False, "snapshot": {}, "auth": {}}

    monkeypatch.setattr(O, "resolve_saved_oauth_status", signed_out)
    response = client.post("/api/card-drop/oauth/rebind-client", json={"confirm": True})
    assert response.status_code == 409
    assert response.json() == {"detail": "desktop_login_required"}

    _save_login(_auth, _social, CONFLICT)
    _logged_in(monkeypatch, auth_source="legacy")
    response = client.post("/api/card-drop/oauth/rebind-client", json={"confirm": True})
    assert response.status_code == 409
    assert response.json()["detail"] == "oauth_session_required"
    assert "统一账号" in response.json()["hint"]


def test_rebind_runs_local_proof_flow_and_updates_saved_bind(oauth_app, monkeypatch):  # noqa: F811
    client, auth, social, _pending = oauth_app
    _save_login(auth, social, CONFLICT)
    _mark_conflict(auth.parent)
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
    assert not hold.device_held(auth.parent, USER_ID)


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
    _mark_conflict(auth.parent)
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
    assert hold.device_held(auth.parent, USER_ID)


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


def _facts_sync_env(tmp_path, monkeypatch, *, lanlans=("Neko",)):
    memory_dir = tmp_path / "memory"
    for name in lanlans:
        (memory_dir / name).mkdir(parents=True)
    monkeypatch.setattr(
        sync_worker, "get_config_manager", lambda: SimpleNamespace(memory_dir=str(memory_dir)),
    )
    monkeypatch.setattr(sync_worker, "_social_base_url", lambda: "https://community.example")
    monkeypatch.setattr(sync_worker, "_get_client_credentials", lambda: ("local-client", "P" * 43))
    monkeypatch.setattr(hold, "default_directory", lambda: tmp_path)
    monkeypatch.setattr(sync_worker, "_signed_in_account", lambda: USER_ID)
    return memory_dir


@pytest.mark.parametrize("held", [True, False])
async def test_facts_sync_holds_while_device_belongs_to_another_account(tmp_path, monkeypatch, held):
    _facts_sync_env(tmp_path, monkeypatch)
    if held:
        _mark_conflict(tmp_path)
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

    assert registered == ([] if held else ["register"])
    assert swept == ([] if held else ["Neko"])


def test_hold_follows_only_definite_ownership_outcomes(tmp_path):
    """Logout, browser syncs and transient bind failures never release a conflict."""
    _mark_conflict(tmp_path)
    assert hold.device_held(tmp_path, USER_ID)
    for uncertain in (
        {"bound": False, "error": "cloud_unreachable"},
        {"bound": False, "error": "invalid_client_proof"},
        {"bound": False, "error": None, "skipped": "native_session_sync"},
        None,
    ):
        hold.record_bind_outcome(tmp_path, uncertain, USER_ID)
        assert hold.device_held(tmp_path, USER_ID), uncertain
    hold.record_bind_outcome(tmp_path, dict(BOUND), USER_ID)
    assert not hold.device_held(tmp_path, USER_ID)
    # Unreadable records fail closed.
    (tmp_path / hold.HOLD_FILENAME).write_text("{not json", encoding="utf-8")
    assert hold.device_held(tmp_path, USER_ID)


def test_hold_compares_signed_in_account_with_confirmed_owner(tmp_path):
    assert not hold.device_held(tmp_path, USER_ID)  # nothing known yet
    hold.record_bind_outcome(tmp_path, dict(BOUND), OWNER_A)
    assert not hold.device_held(tmp_path, OWNER_A)
    # Another account signed in without ever binding (browser session sync).
    assert hold.device_held(tmp_path, USER_ID)
    # Signed out: uploads land with the owner, which is where they belong.
    assert not hold.device_held(tmp_path, None)
    # remember_owner never overrides a recorded owner.
    hold.remember_owner(tmp_path, USER_ID)
    assert hold.device_held(tmp_path, USER_ID)


def test_sync_session_login_on_owned_device_holds_uploads(tmp_path, monkeypatch):
    """A legacy browser sync replaces the login without binding; the owner stays A."""
    auth = tmp_path / "community_auth.json"
    social = tmp_path / "social_session.json"
    monkeypatch.setattr(C, "_auth_path", lambda: auth)
    monkeypatch.setattr(C, "_social_session_path", lambda: social)
    monkeypatch.setattr(C, "_legacy_social_session_path", lambda: social)
    auth.write_text(
        json.dumps({"access_token": "a-token", "local_user_id": OWNER_A, "auth_source": "oauth", "bind": BOUND}),
        encoding="utf-8",
    )
    social.write_text(
        json.dumps({"token": "a-token", "local_user_id": OWNER_A, "auth_source": "oauth"}),
        encoding="utf-8",
    )

    import asyncio as _asyncio

    bind = _asyncio.run(
        C._store_session(
            "https://community.example",
            "b-legacy-token",
            None,
            {"id": USER_ID, "display_name": "B"},
            auth_source="legacy",
            bind_client=False,
        )
    )

    assert bind.get("skipped") == "native_session_sync"
    assert json.loads(auth.read_text(encoding="utf-8"))["local_user_id"] == USER_ID
    assert hold.device_held(tmp_path, USER_ID)
    # B's unsynced memories stay on the device, so neither a logout nor A
    # signing back in releases the hold; only a bind that confirms the owner.
    assert hold.device_held(tmp_path, None)
    assert hold.device_held(tmp_path, OWNER_A)
    hold.record_bind_outcome(tmp_path, dict(BOUND), OWNER_A)
    assert not hold.device_held(tmp_path, OWNER_A)


def test_logout_keeps_the_device_hold(oauth_app):  # noqa: F811
    _client, auth, social, _pending = oauth_app
    _save_login(auth, social, CONFLICT)
    _mark_conflict(auth.parent)

    assert C._clear_auth()

    assert not auth.exists()
    assert hold.device_held(auth.parent, None)


async def test_relogin_with_transient_bind_failure_keeps_hold(tmp_path, monkeypatch):
    _pending_login(tmp_path, monkeypatch)
    _mark_conflict(tmp_path)

    async def flaky_bind(_social_base, _access_token, *, rebind=False):
        return {"bound": False, "error": "cloud_unreachable"}

    monkeypatch.setattr(O, "_oauth_guest_bind", flaky_bind)
    response = await O._handle_oauth_callback("auth-code", "expected-state")

    assert response.status_code == 200
    assert hold.device_held(tmp_path, USER_ID)


async def test_facts_sync_stops_between_batches_once_held(tmp_path, monkeypatch):
    memory_dir = _facts_sync_env(tmp_path, monkeypatch)
    lanlan = memory_dir / "Neko"
    facts = [
        {"hash": f"fact-{index:04d}-hash", "text": f"fact {index}", "importance": 8}
        for index in range(sync_worker.BATCH_SIZE * 3)
    ]
    (lanlan / "facts.json").write_text(json.dumps(facts), encoding="utf-8")
    posted: list[int] = []

    async def post_batch(_base, _client_id, _lanlan, batch):
        posted.append(len(batch))
        # A login saves an ownership conflict while this sweep is running.
        _mark_conflict(tmp_path)
        return True, {}

    monkeypatch.setattr(sync_worker, "_post_facts_batch", post_batch)

    await sync_worker._sync_one_lanlan(memory_dir, lanlan, "local-client", "https://community.example")

    assert posted == [sync_worker.BATCH_SIZE]
    state = json.loads((lanlan / "facts_sync_state.json").read_text(encoding="utf-8"))
    assert len(state["synced"]) == sync_worker.BATCH_SIZE


# ------------------------------------------------------------ rebind races


def test_rebind_waits_for_login_and_logout_lock(oauth_app, monkeypatch):  # noqa: F811
    """Reading the session, binding, and saving happen under the login lock."""
    client, auth, social, _pending = oauth_app
    _save_login(auth, social, CONFLICT)
    _logged_in(monkeypatch)
    observed: list[bool] = []

    async def bind(_base, _access, *, rebind=False):
        observed.append(O._oauth_start_lock.locked())
        return {"bound": True, "error": None}

    monkeypatch.setattr(O, "_oauth_guest_bind", bind)
    response = client.post("/api/card-drop/oauth/rebind-client", json={"confirm": True})

    assert response.status_code == 200
    assert observed == [True]


def test_rebind_refuses_when_signed_in_account_changed(oauth_app, monkeypatch):  # noqa: F811
    client, auth, social, _pending = oauth_app
    _save_login(auth, social, CONFLICT)

    async def other_account():
        return {
            "logged_in": True,
            "snapshot": {
                "access_token": "account-c-access",
                "auth_source": "oauth",
                "local_user_id": "33333333-3333-4333-8333-333333333333",
            },
            "auth": {},
        }

    async def unexpected(*_args, **_kwargs):
        raise AssertionError("a different account must not be rebound")

    monkeypatch.setattr(O, "resolve_saved_oauth_status", other_account)
    monkeypatch.setattr(O, "_oauth_guest_bind", unexpected)
    response = client.post("/api/card-drop/oauth/rebind-client", json={"confirm": True})

    assert response.status_code == 409
    assert response.json() == {"detail": "desktop_login_required"}


def test_rebind_records_bind_after_same_account_token_refresh(oauth_app, monkeypatch):  # noqa: F811
    client, auth, social, _pending = oauth_app
    _save_login(auth, social, CONFLICT)
    _mark_conflict(auth.parent)
    _logged_in(monkeypatch)

    async def bind_then_refresh(_base, _access, *, rebind=False):
        # The same account's token rotates while the cloud call runs.
        _save_login(auth, social, CONFLICT)
        for path, key in ((auth, "access_token"), (social, "token")):
            data = json.loads(path.read_text(encoding="utf-8"))
            data[key] = "account-b-refreshed"
            path.write_text(json.dumps(data), encoding="utf-8")
        return {"bound": True, "error": None}

    monkeypatch.setattr(O, "_oauth_guest_bind", bind_then_refresh)
    response = client.post("/api/card-drop/oauth/rebind-client", json={"confirm": True})

    assert response.status_code == 200
    saved = json.loads(auth.read_text(encoding="utf-8"))
    assert saved["access_token"] == "account-b-refreshed"
    assert saved["bind"] == {"bound": True, "error": None}
    assert not hold.device_held(auth.parent, USER_ID)


def test_rebind_reports_unsaved_local_state(oauth_app, monkeypatch):  # noqa: F811
    client, auth, social, _pending = oauth_app
    _save_login(auth, social, CONFLICT)
    _logged_in(monkeypatch)

    async def bound(*_args, **_kwargs):
        return {"bound": True, "error": None}

    monkeypatch.setattr(O, "_oauth_guest_bind", bound)
    monkeypatch.setattr(C, "_persist_repaired_bind", lambda *_args, **_kwargs: False)
    response = client.post("/api/card-drop/oauth/rebind-client", json={"confirm": True})

    assert response.status_code == 503
    assert response.json()["detail"] == "local_bind_state_not_saved"


def test_conflict_page_script_matches_its_csp_hash():
    page = O._ownership_conflict_callback_html().body.decode("utf-8")
    scripts = re.findall(r"<script>(.*?)</script>", page, re.S)
    assert len(scripts) == 1
    digest = base64.b64encode(hashlib.sha256(scripts[0].encode()).digest()).decode()
    assert digest == O._REBIND_SCRIPT_HASH


async def test_rebind_of_proofless_old_device_reports_owner_must_unbind(monkeypatch):
    paths: list[str] = []

    def handle(request):
        paths.append(request.url.path)
        if request.url.path == "/api/auth/bind-client/challenge":
            return httpx.Response(200, json={"binding_challenge": "C" * 43})
        if request.url.path == "/api/clients/bind-approval":
            return httpx.Response(403, json={"detail": "legacy_client_rebind_required"})
        if request.url.path == "/api/clients/adopt-proof":
            return httpx.Response(409, json={"detail": "client_already_bound_to_other_user"})
        raise AssertionError(f"unexpected {request.method} {request.url}")

    transport = httpx.MockTransport(handle)
    real_client = httpx.AsyncClient
    monkeypatch.setattr(O.httpx, "AsyncClient", lambda **kwargs: real_client(transport=transport))
    monkeypatch.setattr(C, "_get_client_credentials", lambda: ("local-client", "P" * 43))
    rebind = await O._oauth_guest_bind("https://community.example", "platform-access", rebind=True)
    login = await O._oauth_guest_bind("https://community.example", "platform-access")

    assert rebind == {"bound": False, "error": O.LEGACY_DEVICE_OWNED_ELSEWHERE}
    # Login keeps reporting the ordinary conflict so the device is held.
    assert login == CONFLICT
    assert "/api/auth/bind-client" not in paths
    assert "legacy_client_owned_by_other_user" in O._REBIND_SCRIPT


def test_rebind_refuses_when_community_url_changed(oauth_app, monkeypatch):  # noqa: F811
    client, auth, social, _pending = oauth_app
    _save_login(auth, social, CONFLICT)

    async def resolve():
        return {
            "logged_in": True,
            "snapshot": {
                "access_token": "account-b-access",
                "auth_source": "oauth",
                "local_user_id": USER_ID,
                "base_url": "https://other-community.example",
            },
            "auth": {},
        }

    async def unexpected(*_args, **_kwargs):
        raise AssertionError("the token must not go to a different community")

    monkeypatch.setattr(O, "resolve_saved_oauth_status", resolve)
    monkeypatch.setattr(O, "_oauth_guest_bind", unexpected)
    response = client.post("/api/card-drop/oauth/rebind-client", json={"confirm": True})

    assert response.status_code == 409
    assert response.json() == {"detail": "community_base_url_changed"}


def test_saved_conflict_still_holds_when_hold_record_is_missing(tmp_path):
    """A failed hold write cannot open uploads: the login's own bind still holds."""
    assert not (tmp_path / hold.HOLD_FILENAME).exists()
    assert hold.device_held(tmp_path, USER_ID, saved_bind=dict(CONFLICT))
    assert not hold.device_held(tmp_path, USER_ID, saved_bind=dict(BOUND))


async def test_facts_sync_uses_saved_conflict_when_hold_write_failed(tmp_path, monkeypatch):
    _facts_sync_env(tmp_path, monkeypatch)
    (tmp_path / "community_auth.json").write_text(json.dumps({"bind": CONFLICT}), encoding="utf-8")
    monkeypatch.setattr(hold, "_write", lambda *_args: False)
    assert not hold.record_bind_outcome(tmp_path, dict(CONFLICT), USER_ID)

    assert await __import__("asyncio").to_thread(sync_worker._device_bound_to_other_account)


def test_rebind_that_finds_a_conflict_records_the_hold(oauth_app, monkeypatch):  # noqa: F811
    client, auth, social, _pending = oauth_app
    _save_login(auth, social, {"bound": False, "error": "cloud_unreachable"})
    _logged_in(monkeypatch)
    # Nothing recorded yet: the login's bind never reached the cloud.
    assert not (auth.parent / hold.HOLD_FILENAME).exists()

    async def refused(*_args, **_kwargs):
        return {"bound": False, "error": O.LEGACY_DEVICE_OWNED_ELSEWHERE}

    monkeypatch.setattr(O, "_oauth_guest_bind", refused)
    response = client.post("/api/card-drop/oauth/rebind-client", json={"confirm": True})

    assert response.status_code == 409
    assert response.json()["detail"] == "legacy_client_owned_by_other_user"
    assert hold.device_held(auth.parent, USER_ID)


async def test_rebind_cannot_land_between_another_logins_bind_and_save(tmp_path, monkeypatch):
    """The login callback holds the device-bind lock from its bind through its save."""
    import asyncio

    _pending_login(tmp_path, monkeypatch)
    order: list[str] = []
    bind_started = asyncio.Event()
    release_bind = asyncio.Event()

    async def slow_login_bind(_social_base, _access_token, *, rebind=False):
        order.append("login-bind")
        bind_started.set()
        await release_bind.wait()
        return dict(BOUND)

    monkeypatch.setattr(O, "_oauth_guest_bind", slow_login_bind)
    callback = asyncio.create_task(O._handle_oauth_callback("auth-code", "expected-state"))
    await bind_started.wait()

    async def rebind_attempt():
        async with O._device_bind_lock:
            order.append("rebind")

    rebind = asyncio.create_task(rebind_attempt())
    await asyncio.sleep(0.05)
    assert order == ["login-bind"]  # the rebind is waiting
    release_bind.set()
    await asyncio.wait_for(asyncio.gather(callback, rebind), 5)

    assert order == ["login-bind", "rebind"]
    # The login's record was written before the rebind could run.
    assert not hold.device_held(tmp_path, USER_ID)


async def test_failed_hold_write_is_recovered_before_next_login(tmp_path, monkeypatch):
    """A conflict saved only in the login survives that login being replaced."""
    _pending_login(tmp_path, monkeypatch)
    real_write = hold._write
    monkeypatch.setattr(hold, "_write", lambda *_args: False)

    async def refused(_social_base, _access_token, *, rebind=False):
        return dict(CONFLICT)

    monkeypatch.setattr(O, "_oauth_guest_bind", refused)
    await O._handle_oauth_callback("auth-code", "expected-state")
    assert not (tmp_path / hold.HOLD_FILENAME).exists()
    # Still held through the saved login while the record is missing.
    saved_bind = json.loads((tmp_path / "community_auth.json").read_text(encoding="utf-8"))["bind"]
    assert hold.device_held(tmp_path, USER_ID, saved_bind=saved_bind)

    # Disk recovers; the same account logs in again and the bind is inconclusive.
    monkeypatch.setattr(hold, "_write", real_write)
    _pending_login(tmp_path, monkeypatch)

    async def flaky(_social_base, _access_token, *, rebind=False):
        return {"bound": False, "error": "cloud_unreachable"}

    monkeypatch.setattr(O, "_oauth_guest_bind", flaky)
    response = await O._handle_oauth_callback("auth-code", "expected-state")

    assert response.status_code == 200
    assert hold.device_held(tmp_path, USER_ID)



def test_signed_in_account_state_tells_unreadable_from_signed_out(oauth_app):  # noqa: F811
    _client, auth, social, _pending = oauth_app
    assert C.signed_in_account_state() == ("signed_out", None)
    _save_login(auth, social, CONFLICT)
    assert C.signed_in_account_state() == ("signed_in", USER_ID)
    auth.write_text("{half-written", encoding="utf-8")
    social.write_text("{half-written", encoding="utf-8")
    assert C.signed_in_account_state() == ("unknown", None)


async def test_facts_sync_holds_when_credentials_are_unreadable(tmp_path, monkeypatch):
    """B signed in through sync-session on A's device; then both credential reads fail."""
    import asyncio

    from services.chat_sync import host as chat_sync_host

    real_signed_in_account = sync_worker._signed_in_account
    _facts_sync_env(tmp_path, monkeypatch)
    # Read the account through the real hook, not the environment's stub.
    monkeypatch.setattr(sync_worker, "_signed_in_account", real_signed_in_account)
    hold.record_bind_outcome(tmp_path, dict(BOUND), OWNER_A)
    monkeypatch.setattr(chat_sync_host, "signed_in_account_state", lambda: ("unknown", None))
    assert await asyncio.to_thread(sync_worker._device_bound_to_other_account)

    # A real logout (no credential files) is not a hold: uploads go to the owner.
    monkeypatch.setattr(chat_sync_host, "signed_in_account_state", lambda: ("signed_out", None))
    assert not await asyncio.to_thread(sync_worker._device_bound_to_other_account)



def test_logout_after_foreign_account_use_keeps_hold(tmp_path):
    """Owner A on file, B signs in without binding, B logs out: still held."""
    hold.record_bind_outcome(tmp_path, dict(BOUND), OWNER_A)
    assert hold.record_session_account(tmp_path, USER_ID)
    assert hold.device_held(tmp_path, None)
    # A plain owner record with nobody else ever signed in does not hold.
    other = tmp_path / "other"
    hold.record_bind_outcome(other, dict(BOUND), OWNER_A)
    assert hold.record_session_account(other, OWNER_A)
    assert not hold.device_held(other, None)


@pytest.mark.parametrize("saved_bind,held", [(None, False), (BOUND, False), ({"bound": False, "error": None, "skipped": "native_session_sync"}, True)])
def test_no_owner_on_file_follows_the_login_bind(tmp_path, saved_bind, held):
    """Pre-bind sessions count as bound; a login that never bound holds."""
    assert hold.device_held(tmp_path, USER_ID, saved_bind=saved_bind) is held


def test_unbound_legacy_session_seeds_the_owner_before_replacement(oauth_app):  # noqa: F811
    _client, auth, social, _pending = oauth_app
    auth.write_text(
        json.dumps({"access_token": "old-token", "local_user_id": OWNER_A, "auth_source": "legacy"}),
        encoding="utf-8",
    )
    C._remember_confirmed_owner()
    assert not hold.device_held(auth.parent, OWNER_A)
    assert hold.device_held(auth.parent, USER_ID)


def test_failed_hold_writes_latch_until_a_written_bind(tmp_path, monkeypatch):
    """Persistent write failures hold uploads for the rest of the process."""
    monkeypatch.setattr(hold, "_write", lambda *_args: False)
    assert not hold.record_bind_outcome(tmp_path, dict(CONFLICT), USER_ID)
    # The login that carried the conflict is gone (re-login or logout).
    assert hold.device_held(tmp_path, USER_ID, saved_bind={"bound": False, "error": "cloud_unreachable"})
    assert hold.device_held(tmp_path, None)
    # A successful bind that cannot be written does not release it either.
    assert not hold.record_bind_outcome(tmp_path, dict(BOUND), USER_ID)
    assert hold.device_held(tmp_path, USER_ID)
    monkeypatch.undo()
    assert hold.record_bind_outcome(tmp_path, dict(BOUND), USER_ID)
    assert not hold.device_held(tmp_path, USER_ID)


def test_rebind_conflict_after_flaky_login_holds_even_if_write_fails(oauth_app, monkeypatch):  # noqa: F811
    """Login bind was cloud_unreachable, rebind finds a conflict, hold write fails."""
    client, auth, social, _pending = oauth_app
    _save_login(auth, social, {"bound": False, "error": "cloud_unreachable"})
    _logged_in(monkeypatch)
    monkeypatch.setattr(hold, "_write", lambda *_args: False)

    async def refused(*_args, **_kwargs):
        return dict(CONFLICT)

    monkeypatch.setattr(O, "_oauth_guest_bind", refused)
    response = client.post("/api/card-drop/oauth/rebind-client", json={"confirm": True})

    assert response.status_code == 409
    saved_bind = json.loads(auth.read_text(encoding="utf-8"))["bind"]
    assert saved_bind["error"] == "cloud_unreachable"
    assert hold.device_held(auth.parent, USER_ID, saved_bind=saved_bind)


def test_rebind_compares_community_endpoints_strictly(oauth_app, monkeypatch):  # noqa: F811
    """127.0.0.1 and ::1 can be different services; never send the bearer across."""
    client, auth, social, _pending = oauth_app
    _save_login(auth, social, CONFLICT)
    monkeypatch.setattr(C, "_social_base_url", lambda: "http://[::1]:8080")

    async def resolve():
        return {
            "logged_in": True,
            "snapshot": {
                "access_token": "account-b-access",
                "auth_source": "oauth",
                "local_user_id": USER_ID,
                "base_url": "http://127.0.0.1:8080",
            },
            "auth": {},
        }

    async def unexpected(*_args, **_kwargs):
        raise AssertionError("the bearer must stay on its own endpoint")

    monkeypatch.setattr(O, "resolve_saved_oauth_status", resolve)
    monkeypatch.setattr(O, "_oauth_guest_bind", unexpected)
    response = client.post("/api/card-drop/oauth/rebind-client", json={"confirm": True})

    assert response.status_code == 409
    assert response.json() == {"detail": "community_base_url_changed"}
