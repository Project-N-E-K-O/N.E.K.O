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


def await_(awaitable):
    import asyncio

    return asyncio.run(_identity(awaitable))


async def _identity(awaitable):
    return await awaitable


@pytest.fixture(autouse=True)
def _reset_unrecorded_hold():
    hold._set_unrecorded_hold(False)
    yield
    hold._set_unrecorded_hold(False)


def _refuse_then_break_writes(monkeypatch):
    """Cloud answers that another account owns the device; every later write fails."""

    async def refused(*_args, **_kwargs):
        monkeypatch.setattr(hold, "_write", lambda *_a: False)
        return dict(CONFLICT)

    monkeypatch.setattr(O, "_oauth_guest_bind", refused)


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
                "base_url": "https://community.example",
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


def test_rebind_holds_ownership_and_login_locks(oauth_app, monkeypatch):  # noqa: F811
    """Both facts uploads and login changes wait while a rebind is in flight."""
    client, auth, social, _pending = oauth_app
    _save_login(auth, social, CONFLICT)
    _logged_in(monkeypatch)
    observed: list[tuple[bool, bool]] = []

    async def bind(_base, _access, *, rebind=False):
        observed.append((hold.ownership_lock.locked(), O._oauth_start_lock.locked()))
        return {"bound": True, "error": None}

    monkeypatch.setattr(O, "_oauth_guest_bind", bind)
    response = client.post("/api/card-drop/oauth/rebind-client", json={"confirm": True})

    assert response.status_code == 200
    assert observed == [(True, True)]


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

    monkeypatch.setattr(hold, "ownership_lock", asyncio.Lock())
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
        async with hold.ownership_lock:
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
    # Electron's logout leaves a tokenless social file: that is signed out.
    social.write_text(json.dumps({"token": ""}), encoding="utf-8")
    auth.unlink()
    assert C.signed_in_account_state() == ("signed_out", None)


def test_unreadable_social_session_never_falls_back_to_older_files(oauth_app, monkeypatch):  # noqa: F811
    """B's social file is corrupt; A's auth mirror and legacy file must not decide."""
    _client, auth, social, _pending = oauth_app
    legacy = social.with_name("legacy_social_session.json")
    monkeypatch.setattr(C, "_legacy_social_session_path", lambda: legacy)
    for path, key in ((auth, "access_token"), (legacy, "token")):
        path.write_text(json.dumps({key: "a-token", "local_user_id": OWNER_A}), encoding="utf-8")
    social.write_text("{half-written", encoding="utf-8")

    assert C.signed_in_account_state() == ("unknown", None)
    hold.record_bind_outcome(auth.parent, dict(BOUND), OWNER_A)
    assert hold.device_held(auth.parent, hold.UNREADABLE_ACCOUNT)


def test_pre_v2_session_without_account_id_keeps_uploading(oauth_app, tmp_path, monkeypatch):  # noqa: F811
    """A single-account install from before local_user_id was saved is not held."""
    import asyncio

    from services.chat_sync import host as chat_sync_host

    _client, auth, social, _pending = oauth_app
    social.write_text(json.dumps({"token": "old-token"}), encoding="utf-8")
    assert C.signed_in_account_state() == ("signed_in", "")
    real_signed_in_account = sync_worker._signed_in_account
    _facts_sync_env(tmp_path, monkeypatch)
    monkeypatch.setattr(sync_worker, "_signed_in_account", real_signed_in_account)
    monkeypatch.setattr(chat_sync_host, "signed_in_account_state", C.signed_in_account_state)
    monkeypatch.setattr(hold, "default_directory", lambda: auth.parent)

    assert not await_(asyncio.to_thread(sync_worker._device_bound_to_other_account))
    # Once an owner is on file, an account that cannot be compared holds.
    hold.record_bind_outcome(auth.parent, dict(BOUND), OWNER_A)
    assert await_(asyncio.to_thread(sync_worker._device_bound_to_other_account))


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
    _refuse_then_break_writes(monkeypatch)
    response = client.post("/api/card-drop/oauth/rebind-client", json={"confirm": True})

    assert response.status_code == 409
    saved_bind = json.loads(auth.read_text(encoding="utf-8"))["bind"]
    # The conflict also lands on the login, a second copy of the hold on disk.
    assert saved_bind == CONFLICT
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



async def test_persistent_write_failure_survives_relogin_and_logout(tmp_path, monkeypatch):
    """Conflict login, writes keep failing, same account re-logs in (flaky), then logs out."""
    _pending_login(tmp_path, monkeypatch)
    monkeypatch.setattr(hold, "_write", lambda *_args: False)

    async def refused(_social_base, _access_token, *, rebind=False):
        return dict(CONFLICT)

    monkeypatch.setattr(O, "_oauth_guest_bind", refused)
    await O._handle_oauth_callback("auth-code", "expected-state")

    _pending_login(tmp_path, monkeypatch)

    async def flaky(_social_base, _access_token, *, rebind=False):
        return {"bound": False, "error": "cloud_unreachable"}

    monkeypatch.setattr(O, "_oauth_guest_bind", flaky)
    await O._handle_oauth_callback("auth-code", "expected-state")
    saved_bind = json.loads((tmp_path / "community_auth.json").read_text(encoding="utf-8"))["bind"]
    # The inconclusive re-login carries the unwritten conflict instead of erasing it.
    assert saved_bind["error"] == "client_already_bound_to_other_user"
    assert not (tmp_path / hold.HOLD_FILENAME).exists()
    assert hold.device_held(tmp_path, USER_ID, saved_bind=saved_bind)

    assert C._clear_auth()
    assert hold.device_held(tmp_path, None)


async def test_conflict_reaches_disk_once_writes_recover_before_logout(tmp_path, monkeypatch):
    """First hold write fails, disk recovers, logout, restart: still held."""
    _pending_login(tmp_path, monkeypatch)
    real_write = hold._write
    monkeypatch.setattr(hold, "_write", lambda *_args: False)

    async def refused(_social_base, _access_token, *, rebind=False):
        return dict(CONFLICT)

    monkeypatch.setattr(O, "_oauth_guest_bind", refused)
    await O._handle_oauth_callback("auth-code", "expected-state")
    assert not (tmp_path / hold.HOLD_FILENAME).exists()
    assert hold.device_held(tmp_path, None)  # latched in memory

    monkeypatch.setattr(hold, "_write", real_write)  # disk recovers
    assert C._clear_auth()  # logout rewrites the conflict before deleting the login
    assert not (tmp_path / "community_auth.json").exists()

    hold._set_unrecorded_hold(False)  # restart: the in-memory latch is gone
    assert hold.device_held(tmp_path, None)
    assert hold.device_held(tmp_path, USER_ID)



@pytest.mark.parametrize(
    "saved_bind,owner,held",
    [
        (None, None, False),  # pre-v2 single-account install keeps uploading
        (BOUND, None, False),
        ({"bound": False, "error": None, "skipped": "native_session_sync"}, None, True),
        (None, OWNER_A, True),  # cannot compare with an owner on file
    ],
)
def test_pre_v2_session_follows_its_own_bind_without_an_owner(tmp_path, saved_bind, owner, held):
    if owner:
        hold.record_bind_outcome(tmp_path, dict(BOUND), owner)
    assert hold.device_held(tmp_path, hold.LEGACY_ACCOUNT, saved_bind=saved_bind) is held


@pytest.mark.parametrize("saved_bind", [None, BOUND, {"bound": False, "error": None, "skipped": "native_session_sync"}])
def test_unreadable_credentials_hold_even_without_an_owner(tmp_path, saved_bind):
    """A corrupt social session next to a never-bound mirror must not upload."""
    assert not (tmp_path / hold.HOLD_FILENAME).exists()
    assert hold.device_held(tmp_path, hold.UNREADABLE_ACCOUNT, saved_bind=saved_bind)



def test_crash_between_saving_login_and_recording_use_still_holds_after_logout(oauth_app):  # noqa: F811
    """B's sync-session login was saved, then the process stopped before the device record."""
    _client, auth, social, _pending = oauth_app
    hold.record_bind_outcome(auth.parent, dict(BOUND), OWNER_A)
    # What _store_session leaves on disk if it stops right after saving B.
    auth.write_text(
        json.dumps({
            "access_token": "b-token",
            "local_user_id": USER_ID,
            "auth_source": "legacy",
            "bind": {"bound": False, "error": None, "skipped": "native_session_sync"},
        }),
        encoding="utf-8",
    )
    social.write_text(
        json.dumps({"token": "b-token", "local_user_id": USER_ID, "auth_source": "legacy"}),
        encoding="utf-8",
    )
    assert hold.device_held(auth.parent, USER_ID)  # B signed in: held by owner comparison

    assert C._clear_auth()

    assert hold.device_held(auth.parent, None)



async def test_persistent_write_failure_then_logout_then_restart_still_holds(tmp_path, monkeypatch):
    """Hold writes keep failing, the account logs out, the process restarts: still held."""
    import asyncio

    _pending_login(tmp_path, monkeypatch)
    monkeypatch.setattr(hold, "_write", lambda *_args: False)

    async def refused(_social_base, _access_token, *, rebind=False):
        return dict(CONFLICT)

    monkeypatch.setattr(O, "_oauth_guest_bind", refused)
    await O._handle_oauth_callback("auth-code", "expected-state")

    assert C._clear_auth()
    auth = json.loads((tmp_path / "community_auth.json").read_text(encoding="utf-8"))
    assert "access_token" not in auth and "refresh_token" not in auth
    assert auth[C.DEVICE_HOLD_PENDING] is True
    assert C.signed_in_account_state() == ("signed_out", None)

    hold._set_unrecorded_hold(False)  # restart
    real_signed_in_account = sync_worker._signed_in_account
    _facts_sync_env(tmp_path, monkeypatch)
    monkeypatch.setattr(sync_worker, "_signed_in_account", real_signed_in_account)
    assert await asyncio.to_thread(sync_worker._device_bound_to_other_account)


def test_logout_reports_failure_when_the_hold_cannot_be_kept_anywhere(oauth_app, monkeypatch):  # noqa: F811
    _client, auth, social, _pending = oauth_app
    _save_login(auth, social, CONFLICT)
    monkeypatch.setattr(hold, "_write", lambda *_args: False)
    real_write_private = C._write_private_json

    def failing_private_write(path, data):
        if path == auth:
            raise OSError("disk full")
        real_write_private(path, data)

    monkeypatch.setattr(C, "_write_private_json", failing_private_write)

    assert not C._clear_auth()
    # The mirror, and with it the conflict, stays on disk.
    assert json.loads(auth.read_text(encoding="utf-8"))["bind"] == CONFLICT


async def test_rebind_conflict_with_failed_write_holds_after_logout_and_restart(oauth_app, tmp_path, monkeypatch):  # noqa: F811
    """Login bind was cloud_unreachable, rebind finds a conflict, writes fail, logout, restart."""
    import asyncio

    client, auth, social, _pending = oauth_app
    _save_login(auth, social, {"bound": False, "error": "cloud_unreachable"})
    _logged_in(monkeypatch)
    _refuse_then_break_writes(monkeypatch)
    assert client.post("/api/card-drop/oauth/rebind-client", json={"confirm": True}).status_code == 409

    assert C._clear_auth()
    tombstone = json.loads(auth.read_text(encoding="utf-8"))
    assert tombstone[C.DEVICE_HOLD_PENDING] is True
    assert "access_token" not in tombstone

    hold._set_unrecorded_hold(False)  # restart
    real_signed_in_account = sync_worker._signed_in_account
    _facts_sync_env(tmp_path, monkeypatch)
    monkeypatch.setattr(hold, "default_directory", lambda: auth.parent)
    monkeypatch.setattr(sync_worker, "_signed_in_account", real_signed_in_account)
    assert await asyncio.to_thread(sync_worker._device_bound_to_other_account)



async def test_bound_login_rebind_conflict_with_failed_write_holds_after_logout_and_restart(
    oauth_app, tmp_path, monkeypatch,  # noqa: F811
):
    """Login says bound, rebind finds a conflict, every write to the device record fails."""
    import asyncio

    client, auth, social, _pending = oauth_app
    _save_login(auth, social, dict(BOUND))
    hold.record_bind_outcome(auth.parent, dict(BOUND), USER_ID)
    _logged_in(monkeypatch)
    _refuse_then_break_writes(monkeypatch)
    # Also lose the login copy, so only the in-memory latch knows.
    monkeypatch.setattr(C, "_persist_repaired_bind", lambda *_args, **_kwargs: False)
    assert client.post("/api/card-drop/oauth/rebind-client", json={"confirm": True}).status_code == 409
    assert hold.hold_latched()

    assert C._clear_auth()
    assert json.loads(auth.read_text(encoding="utf-8"))[C.DEVICE_HOLD_PENDING] is True

    hold._set_unrecorded_hold(False)  # restart
    real_signed_in_account = sync_worker._signed_in_account
    _facts_sync_env(tmp_path, monkeypatch)
    monkeypatch.setattr(hold, "default_directory", lambda: auth.parent)
    monkeypatch.setattr(sync_worker, "_signed_in_account", real_signed_in_account)
    assert await asyncio.to_thread(sync_worker._device_bound_to_other_account)


def test_latched_hold_is_written_once_disk_recovers_before_logout(oauth_app):  # noqa: F811
    _client, auth, social, _pending = oauth_app
    _save_login(auth, social, dict(BOUND))
    hold._set_unrecorded_hold(True)
    assert C._clear_auth()
    assert not auth.exists()  # no tombstone needed: the hold reached the device record
    hold._set_unrecorded_hold(False)  # restart
    assert hold.device_held(auth.parent, None)


def test_rejected_session_cleanup_keeps_an_unwritten_conflict(oauth_app, monkeypatch):  # noqa: F811
    """Token rejection deletes the login automatically; its conflict must survive."""
    _client, auth, social, _pending = oauth_app
    _save_login(auth, social, CONFLICT)
    monkeypatch.setattr(hold, "_write", lambda *_args: False)

    assert O._clear_rejected_oauth_snapshot({"access_token": "account-b-access"})

    assert not social.exists()
    tombstone = json.loads(auth.read_text(encoding="utf-8"))
    assert "access_token" not in tombstone
    assert tombstone[C.DEVICE_HOLD_PENDING] is True


def test_rejected_session_cleanup_writes_the_conflict_when_it_can(oauth_app):  # noqa: F811
    _client, auth, social, _pending = oauth_app
    _save_login(auth, social, CONFLICT)
    assert O._clear_rejected_oauth_snapshot({"access_token": "account-b-access"})
    assert not auth.exists() and not social.exists()
    assert hold.device_held(auth.parent, None)


def test_rejected_social_session_cleanup_preserves_the_mirror_and_device_hold(oauth_app, monkeypatch):  # noqa: F811
    _client, auth, social, _pending = oauth_app
    hold.record_bind_outcome(auth.parent, dict(BOUND), OWNER_A)
    mirror = {"access_token": "a-token", "local_user_id": OWNER_A, "auth_source": "oauth", "bind": BOUND}
    auth.write_text(json.dumps(mirror), encoding="utf-8")
    session = {"token": "b-token", "local_user_id": USER_ID, "auth_source": "oauth"}
    social.write_text(json.dumps(session), encoding="utf-8")
    rejected = {"access_token": session["token"]}

    # Retain both records until the hold can survive a restart.
    with monkeypatch.context() as m:
        m.setattr(hold, "_write", lambda *_args: False)
        assert not O._clear_rejected_oauth_snapshot(rejected)
    assert json.loads(social.read_text(encoding="utf-8")) == session
    assert json.loads(auth.read_text(encoding="utf-8")) == mirror

    hold._set_unrecorded_hold(False)
    assert O._clear_rejected_oauth_snapshot(rejected)
    assert not social.exists()
    assert json.loads(auth.read_text(encoding="utf-8")) == mirror
    hold._set_unrecorded_hold(False)
    assert hold.device_held(auth.parent, OWNER_A)


def test_logout_tombstone_is_written_inside_the_credential_lock(oauth_app, monkeypatch):  # noqa: F811
    _client, auth, social, _pending = oauth_app
    _save_login(auth, social, CONFLICT)
    monkeypatch.setattr(hold, "_write", lambda *_args: False)
    in_lock = {"depth": 0, "tombstone_inside": None}
    real_locks = C._social_session_locks
    real_tombstone = C._tombstone_auth_mirror

    from contextlib import contextmanager

    @contextmanager
    def tracking_locks(paths):
        with real_locks(paths):
            in_lock["depth"] += 1
            try:
                yield
            finally:
                in_lock["depth"] -= 1

    def tracking_tombstone(path):
        in_lock["tombstone_inside"] = in_lock["depth"] > 0
        return real_tombstone(path)

    monkeypatch.setattr(C, "_social_session_locks", tracking_locks)
    monkeypatch.setattr(C, "_tombstone_auth_mirror", tracking_tombstone)
    assert C._clear_auth()
    assert in_lock["tombstone_inside"] is True


async def test_unreadable_auth_mirror_holds_uploads(tmp_path, monkeypatch):
    import asyncio

    _facts_sync_env(tmp_path, monkeypatch)
    (tmp_path / "community_auth.json").write_text("{half-written", encoding="utf-8")
    assert await asyncio.to_thread(sync_worker._device_bound_to_other_account)


async def test_auth_mirror_of_another_login_is_not_trusted(tmp_path, monkeypatch):
    """No owner on file; the mirror says bound but belongs to another account."""
    import asyncio

    _facts_sync_env(tmp_path, monkeypatch)  # signed in as USER_ID
    (tmp_path / "community_auth.json").write_text(
        json.dumps({"local_user_id": OWNER_A, "bind": BOUND}), encoding="utf-8",
    )
    assert await asyncio.to_thread(sync_worker._device_bound_to_other_account)


async def test_failed_login_save_leaves_no_hold(tmp_path, monkeypatch):
    """Both credential writes fail: the login never happened, so no hold for the owner."""
    hold.record_bind_outcome(tmp_path, dict(BOUND), OWNER_A)
    monkeypatch.setattr(C, "_auth_path", lambda: tmp_path / "community_auth.json")
    monkeypatch.setattr(C, "_save_auth", lambda _payload: False)
    monkeypatch.setattr(C, "_save_social_session", lambda *_args, **_kwargs: False)

    await C._store_session(
        "https://community.example",
        "b-token",
        None,
        {"id": USER_ID, "display_name": "B"},
        auth_source="legacy",
        bind_client=False,
    )

    assert not hold.device_held(tmp_path, OWNER_A)
    assert not hold.device_held(tmp_path, None)


def test_rebind_reports_unsaved_device_record(oauth_app, monkeypatch):  # noqa: F811
    """Cloud rebind succeeded but the device record could not be written: retryable 503."""
    client, auth, social, _pending = oauth_app
    _save_login(auth, social, CONFLICT)
    _logged_in(monkeypatch)
    monkeypatch.setattr(hold, "_write", lambda *_args: False)

    async def bound(*_args, **_kwargs):
        return dict(BOUND)

    monkeypatch.setattr(O, "_oauth_guest_bind", bound)
    response = client.post("/api/card-drop/oauth/rebind-client", json={"confirm": True})
    assert response.status_code == 503
    assert response.json()["detail"] == "local_bind_state_not_saved"


def test_rebind_does_not_report_success_after_desktop_switched_accounts(oauth_app, monkeypatch):  # noqa: F811
    """Electron switched the social session mid-rebind; the stale auth mirror still has the token."""
    client, auth, social, _pending = oauth_app
    _save_login(auth, social, CONFLICT)
    _logged_in(monkeypatch)

    async def bind_while_desktop_switches(*_args, **_kwargs):
        social.write_text(
            json.dumps({"token": "c-token", "local_user_id": OWNER_A, "auth_source": "oauth"}),
            encoding="utf-8",
        )
        return dict(BOUND)

    monkeypatch.setattr(O, "_oauth_guest_bind", bind_while_desktop_switches)
    response = client.post("/api/card-drop/oauth/rebind-client", json={"confirm": True})

    assert response.status_code == 409
    assert response.json()["detail"] == "desktop_login_required"
    assert json.loads(auth.read_text(encoding="utf-8"))["bind"] == CONFLICT
    # The device record names USER_ID as owner, so OWNER_A's uploads stay held.
    assert hold.device_held(auth.parent, OWNER_A)


async def _conflict_then_flaky_relogin(tmp_path, monkeypatch):
    """Owner USER_ID on file; a later login finds a conflict but cannot record it; the same
    account logs in again and its bind only reaches cloud_unreachable."""
    hold.record_bind_outcome(tmp_path, dict(BOUND), USER_ID)
    _pending_login(tmp_path, monkeypatch)

    async def refused(_social_base, _access_token, *, rebind=False):
        monkeypatch.setattr(hold, "_write", lambda *_args: False)
        return dict(CONFLICT)

    monkeypatch.setattr(O, "_oauth_guest_bind", refused)
    await O._handle_oauth_callback("auth-code", "expected-state")

    _pending_login(tmp_path, monkeypatch)

    async def flaky(_social_base, _access_token, *, rebind=False):
        return {"bound": False, "error": "cloud_unreachable"}

    monkeypatch.setattr(O, "_oauth_guest_bind", flaky)
    await O._handle_oauth_callback("auth-code", "expected-state")


async def _uploads_held_after_restart(tmp_path, monkeypatch) -> bool:
    import asyncio

    hold._set_unrecorded_hold(False)  # restart
    real_signed_in_account = sync_worker._signed_in_account
    _facts_sync_env(tmp_path, monkeypatch)
    monkeypatch.setattr(sync_worker, "_signed_in_account", real_signed_in_account)
    return await asyncio.to_thread(sync_worker._device_bound_to_other_account)


async def test_owner_on_file_conflict_unwritten_relogin_then_restart_still_holds(tmp_path, monkeypatch):
    await _conflict_then_flaky_relogin(tmp_path, monkeypatch)
    assert await _uploads_held_after_restart(tmp_path, monkeypatch)


async def test_owner_on_file_conflict_unwritten_relogin_logout_then_restart_still_holds(tmp_path, monkeypatch):
    await _conflict_then_flaky_relogin(tmp_path, monkeypatch)
    assert C._clear_auth()
    assert await _uploads_held_after_restart(tmp_path, monkeypatch)



async def test_successful_relogin_bind_is_not_overwritten_by_an_unwritten_hold(tmp_path, monkeypatch):
    """A bind that proves ownership is kept, so recording it can clear the hold."""
    _pending_login(tmp_path, monkeypatch)
    hold._set_unrecorded_hold(True)

    async def bound(_social_base, _access_token, *, rebind=False):
        return dict(BOUND)

    monkeypatch.setattr(O, "_oauth_guest_bind", bound)
    await O._handle_oauth_callback("auth-code", "expected-state")

    saved_bind = json.loads((tmp_path / "community_auth.json").read_text(encoding="utf-8"))["bind"]
    assert saved_bind == BOUND
    assert not hold.hold_latched()
    assert not hold.device_held(tmp_path, USER_ID, saved_bind=saved_bind)


def test_rejected_cleanup_skips_a_login_that_already_replaced_it(oauth_app):  # noqa: F811
    """Cleanup for A's rejected token runs after B saved a successful login."""
    _client, auth, social, _pending = oauth_app
    # B's login is current and owns the device.
    auth.write_text(
        json.dumps({"access_token": "b-token", "local_user_id": USER_ID, "auth_source": "oauth", "bind": BOUND}),
        encoding="utf-8",
    )
    social.write_text(json.dumps({"token": "b-token", "local_user_id": USER_ID}), encoding="utf-8")
    hold.record_bind_outcome(auth.parent, dict(BOUND), USER_ID)

    assert O._clear_rejected_oauth_snapshot({"access_token": "a-token"})

    assert json.loads(auth.read_text(encoding="utf-8"))["access_token"] == "b-token"
    assert not hold.device_held(auth.parent, USER_ID)


def test_stale_cleanup_cannot_restore_a_conflict_a_newer_login_cleared(oauth_app, monkeypatch):  # noqa: F811
    """Cleanup reads A's conflicted login; B's successful bind tries to land mid-way."""
    import threading

    _client, auth, social, _pending = oauth_app
    _save_login(auth, social, CONFLICT)  # A's login, still saved
    read_done = threading.Event()
    release = threading.Event()
    real_remember = C._remember_saved_login

    def slow_remember(directory):
        result = real_remember(directory)
        read_done.set()
        assert release.wait(5)
        return result

    monkeypatch.setattr(C, "_remember_saved_login", slow_remember)
    cleanup = threading.Thread(target=C._remember_confirmed_owner)
    cleanup.start()
    assert read_done.wait(5)

    # B's login finishes with a successful bind while cleanup still runs.
    newer = threading.Thread(target=C._record_device_hold, args=(dict(BOUND), OWNER_A))
    newer.start()
    newer.join(0.2)
    assert newer.is_alive(), "the newer login must wait for the stale cleanup"
    release.set()
    cleanup.join(5)
    newer.join(5)

    # B's success landed last: B owns the device and is not held.
    assert not hold.device_held(auth.parent, OWNER_A)


async def test_tombstone_hold_survives_restart_then_second_logout(oauth_app, tmp_path, monkeypatch):  # noqa: F811
    """Tombstone from a bound login whose rebind conflict was never written; restart; log out again."""
    client, auth, social, _pending = oauth_app
    _save_login(auth, social, dict(BOUND))
    hold.record_bind_outcome(auth.parent, dict(BOUND), USER_ID)
    _logged_in(monkeypatch)
    real_write = hold._write
    _refuse_then_break_writes(monkeypatch)
    monkeypatch.setattr(C, "_persist_repaired_bind", lambda *_args, **_kwargs: False)
    assert client.post("/api/card-drop/oauth/rebind-client", json={"confirm": True}).status_code == 409
    assert C._clear_auth()
    assert json.loads(auth.read_text(encoding="utf-8"))[C.DEVICE_HOLD_PENDING] is True

    hold._set_unrecorded_hold(False)  # restart
    monkeypatch.setattr(hold, "_write", real_write)  # disk recovers
    assert C._clear_auth()  # logout again

    assert not auth.exists()  # the hold moved into the device record
    assert hold.device_held(auth.parent, None)
    assert await _uploads_held_after_restart(auth.parent, monkeypatch)


def test_tombstone_survives_a_second_logout_while_writes_still_fail(oauth_app, monkeypatch):  # noqa: F811
    _client, auth, social, _pending = oauth_app
    auth.write_text(
        json.dumps({"local_user_id": USER_ID, "bind": BOUND, C.DEVICE_HOLD_PENDING: True}), encoding="utf-8",
    )
    monkeypatch.setattr(hold, "_write", lambda *_args: False)
    assert C._clear_auth()
    assert json.loads(auth.read_text(encoding="utf-8"))[C.DEVICE_HOLD_PENDING] is True


def test_tombstone_hold_survives_a_sync_session_login_replacing_it(tmp_path, monkeypatch):
    """The tombstone is the only copy of the hold (no device record); the same account
    signs in again through a browser sync-session, which never binds."""
    import asyncio

    auth = tmp_path / "community_auth.json"
    social = tmp_path / "social_session.json"
    monkeypatch.setattr(C, "_auth_path", lambda: auth)
    monkeypatch.setattr(C, "_social_session_path", lambda: social)
    monkeypatch.setattr(C, "_legacy_social_session_path", lambda: social)
    auth.write_text(
        json.dumps({"local_user_id": USER_ID, "bind": BOUND, C.DEVICE_HOLD_PENDING: True}), encoding="utf-8",
    )
    assert not (tmp_path / hold.HOLD_FILENAME).exists()

    asyncio.run(
        C._store_session(
            "https://community.example",
            "same-account-legacy-token",
            None,
            {"id": USER_ID, "display_name": "Same"},
            auth_source="legacy",
            bind_client=False,
        )
    )

    assert hold.device_held(tmp_path, USER_ID)
    assert C._clear_auth()
    assert hold.device_held(tmp_path, None)


def test_logout_records_use_by_the_social_session_when_the_mirror_lags(oauth_app):  # noqa: F811
    """Mirror write failed (still A, bound); social session saved B; crash before the device record."""
    _client, auth, social, _pending = oauth_app
    hold.record_bind_outcome(auth.parent, dict(BOUND), OWNER_A)
    auth.write_text(
        json.dumps({"access_token": "a-token", "local_user_id": OWNER_A, "auth_source": "oauth", "bind": BOUND}),
        encoding="utf-8",
    )
    social.write_text(
        json.dumps({"token": "b-token", "local_user_id": USER_ID, "auth_source": "legacy"}),
        encoding="utf-8",
    )

    assert C._clear_auth()

    assert hold.device_held(auth.parent, None)


async def test_rebind_conflict_with_every_write_failing_holds_after_direct_restart(
    oauth_app, monkeypatch,  # noqa: F811
):
    """Owner on file is the signed-in account; the rebind learns another account owns the
    device; the hold and the login copy both fail; the process restarts without a logout."""
    client, auth, social, _pending = oauth_app
    _save_login(auth, social, dict(BOUND))
    hold.record_bind_outcome(auth.parent, dict(BOUND), USER_ID)
    _logged_in(monkeypatch)
    _refuse_then_break_writes(monkeypatch)
    monkeypatch.setattr(C, "_persist_repaired_bind", lambda *_args, **_kwargs: False)

    assert client.post("/api/card-drop/oauth/rebind-client", json={"confirm": True}).status_code == 409
    assert json.loads(auth.read_text(encoding="utf-8"))["bind"] == BOUND  # login copy never written

    assert await _uploads_held_after_restart(auth.parent, monkeypatch)


def test_rebind_refuses_to_start_when_the_hold_cannot_be_written_first(oauth_app, monkeypatch):  # noqa: F811
    client, auth, social, _pending = oauth_app
    _save_login(auth, social, dict(BOUND))
    _logged_in(monkeypatch)
    monkeypatch.setattr(hold, "_write", lambda *_args: False)

    async def unexpected(*_args, **_kwargs):
        raise AssertionError("the cloud must not be asked without a hold on disk")

    monkeypatch.setattr(O, "_oauth_guest_bind", unexpected)
    response = client.post("/api/card-drop/oauth/rebind-client", json={"confirm": True})

    assert response.status_code == 503
    assert response.json()["detail"] == "local_bind_state_not_saved"


def test_successful_rebind_releases_the_hold_written_before_the_check(oauth_app, monkeypatch):  # noqa: F811
    client, auth, social, _pending = oauth_app
    _save_login(auth, social, CONFLICT)
    _logged_in(monkeypatch)

    async def bound(*_args, **_kwargs):
        assert hold.device_held(auth.parent, USER_ID)  # held while the cloud is asked
        return dict(BOUND)

    monkeypatch.setattr(O, "_oauth_guest_bind", bound)
    assert client.post("/api/card-drop/oauth/rebind-client", json={"confirm": True}).status_code == 200
    assert not hold.device_held(auth.parent, USER_ID)


async def test_unsaved_oauth_login_refused_as_the_recorded_owner_holds(tmp_path, monkeypatch):
    """The cloud refuses the recorded owner, but its new login cannot be saved:
    the old login of that same account stays in place and must not upload."""
    hold.record_bind_outcome(tmp_path, dict(BOUND), USER_ID)
    _pending_login(tmp_path, monkeypatch)

    async def refused(_social_base, _access_token, *, rebind=False):
        return dict(CONFLICT)

    monkeypatch.setattr(O, "_oauth_guest_bind", refused)
    monkeypatch.setattr(O, "_persist_oauth_credentials", lambda *_args, **_kwargs: False)
    response = await O._handle_oauth_callback("auth-code", "expected-state")

    assert response.status_code == 400
    assert hold.device_held(tmp_path, USER_ID, saved_bind=dict(BOUND))


def _refusing_bind_client(monkeypatch):
    monkeypatch.setattr(C, "_get_client_id", lambda: "client-1")
    transport = httpx.MockTransport(
        lambda _request: httpx.Response(409, json={"detail": CONFLICT["error"]})
    )
    real_client = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: real_client(transport=transport, **kwargs))


async def test_unsaved_password_login_refused_as_the_recorded_owner_holds(tmp_path, monkeypatch):
    hold.record_bind_outcome(tmp_path, dict(BOUND), USER_ID)
    monkeypatch.setattr(C, "_auth_path", lambda: tmp_path / "community_auth.json")
    monkeypatch.setattr(C, "_save_auth", lambda _payload: False)
    monkeypatch.setattr(C, "_save_social_session", lambda *_args, **_kwargs: False)
    _refusing_bind_client(monkeypatch)

    bind = await C._store_session(
        "https://community.example", "a-token", None, {"id": USER_ID}, auth_source="legacy",
    )

    assert bind["error"] == CONFLICT["error"]
    assert hold.device_held(tmp_path, USER_ID, saved_bind=dict(BOUND))


async def test_unsaved_login_refused_as_another_account_leaves_the_owner_uploading(tmp_path, monkeypatch):
    hold.record_bind_outcome(tmp_path, dict(BOUND), OWNER_A)
    monkeypatch.setattr(C, "_auth_path", lambda: tmp_path / "community_auth.json")
    monkeypatch.setattr(C, "_save_auth", lambda _payload: False)
    monkeypatch.setattr(C, "_save_social_session", lambda *_args, **_kwargs: False)
    _refusing_bind_client(monkeypatch)

    await C._store_session(
        "https://community.example", "b-token", None, {"id": USER_ID}, auth_source="legacy",
    )

    assert not hold.device_held(tmp_path, OWNER_A)


def test_undecodable_hold_record_fails_closed(tmp_path):
    hold.record_bind_outcome(tmp_path, dict(BOUND), USER_ID)
    (tmp_path / hold.HOLD_FILENAME).write_bytes(b"\xff\xfe{")
    assert hold.device_held(tmp_path, USER_ID)
    assert hold.record_session_account(tmp_path, OWNER_A)


def test_latched_hold_is_not_dropped_by_an_unreadable_record(tmp_path, monkeypatch):
    """A read error is no proof the hold is on disk; it may pass and leave none."""
    path = tmp_path / hold.HOLD_FILENAME
    path.write_text("{not json", encoding="utf-8")
    hold._set_unrecorded_hold(True)
    with monkeypatch.context() as m:
        m.setattr(hold, "_write", lambda *_args: False)
        assert not hold.persist_latched_hold(tmp_path)
        assert hold.hold_latched()
    assert hold.persist_latched_hold(tmp_path)
    assert not hold.hold_latched()
    assert json.loads(path.read_text(encoding="utf-8"))["conflict"] is True


def test_session_account_on_an_unreadable_record_writes_or_latches_the_hold(tmp_path, monkeypatch):
    path = tmp_path / hold.HOLD_FILENAME
    path.write_text("{not json", encoding="utf-8")
    with monkeypatch.context() as m:
        m.setattr(hold, "_write", lambda *_args: False)
        assert not hold.record_session_account(tmp_path, USER_ID)
        assert hold.hold_latched()
    hold._set_unrecorded_hold(False)
    assert hold.record_session_account(tmp_path, USER_ID)
    assert hold.device_held(tmp_path, None)


async def test_unsaved_oauth_login_bound_elsewhere_holds_through_logout_and_restart(tmp_path, monkeypatch):
    """The cloud now binds the device to the new account, whose login cannot be
    saved: the owner on file is out of date, so its old login must not upload."""
    hold.record_bind_outcome(tmp_path, dict(BOUND), OWNER_A)
    _pending_login(tmp_path, monkeypatch)

    async def bound(_social_base, _access_token, *, rebind=False):
        return dict(BOUND)

    monkeypatch.setattr(O, "_oauth_guest_bind", bound)
    monkeypatch.setattr(O, "_persist_oauth_credentials", lambda *_args, **_kwargs: False)
    response = await O._handle_oauth_callback("auth-code", "expected-state")

    assert response.status_code == 400
    assert hold.device_held(tmp_path, OWNER_A, saved_bind=dict(BOUND))
    assert C._clear_auth()
    assert await _uploads_held_after_restart(tmp_path, monkeypatch)
    assert hold.device_held(tmp_path, OWNER_A, saved_bind=dict(BOUND))


async def test_unsaved_refused_login_with_every_write_failing_holds_after_restart(tmp_path, monkeypatch):
    hold.record_bind_outcome(tmp_path, dict(BOUND), USER_ID)
    _pending_login(tmp_path, monkeypatch)
    _refuse_then_break_writes(monkeypatch)
    monkeypatch.setattr(O, "_persist_oauth_credentials", lambda *_args, **_kwargs: False)
    await O._handle_oauth_callback("auth-code", "expected-state")

    hold._set_unrecorded_hold(False)  # restart
    assert hold.device_held(tmp_path, USER_ID, saved_bind=dict(BOUND))


async def test_login_skips_the_bind_when_its_hold_cannot_be_written(tmp_path, monkeypatch):
    """The login still completes; only the cloud is not asked."""
    _pending_login(tmp_path, monkeypatch)
    monkeypatch.setattr(hold, "_write", lambda *_args: False)

    async def unexpected(*_args, **_kwargs):
        raise AssertionError("the cloud must not be asked without a hold on disk")

    monkeypatch.setattr(O, "_oauth_guest_bind", unexpected)
    response = await O._handle_oauth_callback("auth-code", "expected-state")

    assert response.status_code == 200
    saved_auth = json.loads((tmp_path / "community_auth.json").read_text(encoding="utf-8"))
    assert saved_auth["access_token"] == "account-b-access"
    assert saved_auth["bind"]["error"] == "local_bind_state_not_saved"


async def test_recorded_login_answer_releases_its_check(tmp_path, monkeypatch):
    _pending_login(tmp_path, monkeypatch)

    async def bound(_social_base, _access_token, *, rebind=False):
        assert hold.device_held(tmp_path, None)  # held while the cloud is asked
        return dict(BOUND)

    monkeypatch.setattr(O, "_oauth_guest_bind", bound)
    assert (await O._handle_oauth_callback("auth-code", "expected-state")).status_code == 200

    assert "checking" not in json.loads((tmp_path / hold.HOLD_FILENAME).read_text(encoding="utf-8"))
    assert not hold.device_held(tmp_path, USER_ID)


def test_unfinished_check_holds_until_a_recorded_bind(tmp_path):
    hold.record_bind_outcome(tmp_path, dict(BOUND), OWNER_A)
    assert hold.begin_bind_check(tmp_path) is True
    # The process stops before the answer is recorded.
    assert hold.device_held(tmp_path, OWNER_A)
    assert hold.begin_bind_check(tmp_path) is False  # a later check must not end it
    assert hold.device_held(tmp_path, OWNER_A)
    hold.record_bind_outcome(tmp_path, dict(BOUND), OWNER_A)
    assert not hold.device_held(tmp_path, OWNER_A)


@pytest.mark.parametrize("owner,bind,held", [
    (OWNER_A, BOUND, True),  # the cloud moved the device to USER_ID
    (USER_ID, BOUND, False),
    (USER_ID, CONFLICT, True),  # the cloud refused the owner on file
    (OWNER_A, CONFLICT, False),
    (OWNER_A, {"bound": False, "error": "cloud_unreachable"}, False),
])
def test_unsaved_bind_holds_only_when_it_outdates_the_record(tmp_path, owner, bind, held):
    hold.record_bind_outcome(tmp_path, dict(BOUND), owner)
    assert hold.record_unsaved_bind(tmp_path, dict(bind), USER_ID)
    assert hold.device_held(tmp_path, owner, saved_bind=dict(BOUND)) is held


def test_owner_is_seeded_while_a_check_is_on_disk(tmp_path):
    assert hold.begin_bind_check(tmp_path) is True
    hold.remember_owner(tmp_path, USER_ID)
    assert hold.end_bind_check(tmp_path, True, {"bound": False, "error": "client_not_registered"})
    assert not hold.device_held(tmp_path, USER_ID)
    assert hold.device_held(tmp_path, OWNER_A)


def test_latched_hold_survives_a_read_error_at_logout_and_restart(oauth_app, monkeypatch):  # noqa: F811
    """Write fails, then logout cannot read the record, then reads recover."""
    _client, auth, social, _pending = oauth_app
    hold.record_bind_outcome(auth.parent, dict(BOUND), OWNER_A)
    _save_login(auth, social, {"bound": False, "error": None, "skipped": "native_session_sync"})
    hold._set_unrecorded_hold(True)  # B's use of A's device could not be written
    with monkeypatch.context() as m:
        m.setattr(hold, "_read", lambda *_args: hold._UNREADABLE)
        m.setattr(hold, "_write", lambda *_args: False)
        C._clear_auth()
    assert json.loads(auth.read_text(encoding="utf-8"))[hold.DEVICE_HOLD_PENDING] is True
    hold._set_unrecorded_hold(False)  # restart
    monkeypatch.setattr(hold, "default_directory", lambda: auth.parent)
    monkeypatch.setattr(sync_worker, "_signed_in_account", lambda: None)
    assert sync_worker._device_bound_to_other_account()


def _signed_in_as(monkeypatch, account, token, *, base_url="https://community.example", auth_source="oauth"):
    async def resolve():
        snapshot = {"access_token": token, "auth_source": auth_source, "local_user_id": account, "base_url": base_url}
        return {"logged_in": True, "snapshot": snapshot, "auth": {}}

    monkeypatch.setattr(O, "resolve_saved_oauth_status", resolve)


def _answer(monkeypatch, bind):
    asked = []

    async def answer(_social_base, access_token, *, rebind=False):
        asked.append(access_token)
        return dict(bind)

    monkeypatch.setattr(O, "_oauth_guest_bind", answer)
    return asked


async def test_check_left_by_a_stopped_login_is_settled_by_the_signed_in_owner(oauth_app, monkeypatch):  # noqa: F811
    """The bound owner is signed in; another login stops mid-check; after a
    restart the owner's login asks the cloud again and uploads resume."""
    _client, auth, social, _pending = oauth_app
    _save_login(auth, social, dict(BOUND))
    hold.record_bind_outcome(auth.parent, dict(BOUND), USER_ID)
    assert hold.begin_bind_check(auth.parent) is True  # the process stops here
    hold._set_unrecorded_hold(False)  # restart
    assert hold.device_held(auth.parent, USER_ID, saved_bind=dict(BOUND))

    _logged_in(monkeypatch)
    asked = _answer(monkeypatch, BOUND)
    await O.reconfirm_device_owner()

    assert asked == ["account-b-access"]
    assert not hold.check_pending(auth.parent)
    assert not hold.device_held(auth.parent, USER_ID, saved_bind=dict(BOUND))
    await O.reconfirm_device_owner()
    assert asked == ["account-b-access"]  # nothing left to ask about


async def test_unsaved_login_with_an_unknown_answer_holds_until_reconfirmed(tmp_path, monkeypatch):
    """The bind request failed after it may have reached the cloud, and the
    login was not saved: the old login must not upload until the cloud answers."""
    hold.record_bind_outcome(tmp_path, dict(BOUND), OWNER_A)
    _pending_login(tmp_path, monkeypatch)
    _answer(monkeypatch, {"bound": False, "error": hold.CLOUD_UNREACHABLE})
    monkeypatch.setattr(O, "_persist_oauth_credentials", lambda *_args, **_kwargs: False)
    await O._handle_oauth_callback("auth-code", "expected-state")

    hold._set_unrecorded_hold(False)  # restart
    assert hold.device_held(tmp_path, OWNER_A, saved_bind=dict(BOUND))
    _signed_in_as(monkeypatch, OWNER_A, "a-token")
    _answer(monkeypatch, {"bound": False, "error": hold.CLOUD_UNREACHABLE})
    await O.reconfirm_device_owner()
    assert hold.device_held(tmp_path, OWNER_A, saved_bind=dict(BOUND))

    # The cloud moved the device to B after all: A is refused and stays held.
    _answer(monkeypatch, CONFLICT)
    await O.reconfirm_device_owner()
    assert not hold.check_pending(tmp_path)
    assert hold.device_held(tmp_path, OWNER_A, saved_bind=dict(BOUND))


def test_taken_over_check_ends_only_on_an_answer_that_settles_the_owner(tmp_path):
    hold.record_bind_outcome(tmp_path, dict(BOUND), OWNER_A)
    assert hold.begin_bind_check(tmp_path) is True
    assert hold.begin_bind_check(tmp_path) is False  # left unfinished, taken over
    # A refusal for another account says nothing about who the lost answer
    # bound the device to.
    hold.record_unsaved_bind(tmp_path, dict(CONFLICT), USER_ID)
    assert hold.end_bind_check(tmp_path, False, dict(CONFLICT))
    assert hold.check_pending(tmp_path)
    assert hold.end_bind_check(tmp_path, False, {"bound": False, "error": "http_401"})
    assert hold.check_pending(tmp_path)
    assert hold.end_bind_check(tmp_path, False, dict(BOUND))
    assert not hold.check_pending(tmp_path)


async def test_facts_sync_asks_again_before_its_upload_gate(tmp_path, monkeypatch):
    _facts_sync_env(tmp_path, monkeypatch)
    order = []

    async def resolver():
        order.append("resolve")

    monkeypatch.setattr(hold, "_check_resolver", resolver)
    monkeypatch.setattr(
        sync_worker, "_device_bound_to_other_account", lambda: order.append("gate") or True,
    )
    hold.begin_bind_check(tmp_path)
    await sync_worker._sweep_once()
    assert order == ["resolve", "gate"]


@pytest.mark.parametrize("session_base,auth_source", [
    ("https://old-community.example", "oauth"),
    ("", "oauth"),
    ("https://community.example", "legacy"),
])
async def test_recovery_requires_an_oauth_session_for_the_current_community(
    oauth_app, monkeypatch, session_base, auth_source,  # noqa: F811
):
    _client, auth, social, _pending = oauth_app
    _save_login(auth, social, dict(BOUND))
    hold.record_bind_outcome(auth.parent, dict(BOUND), USER_ID)
    hold.begin_bind_check(auth.parent)
    _signed_in_as(monkeypatch, USER_ID, "account-b-access", base_url=session_base, auth_source=auth_source)

    asked = _answer(monkeypatch, BOUND)
    await O.reconfirm_device_owner()

    assert asked == []
    assert hold.check_pending(auth.parent)
    assert hold.device_held(auth.parent, USER_ID, saved_bind=dict(BOUND))


async def test_login_can_start_during_an_owner_recheck(oauth_app, monkeypatch):  # noqa: F811
    import asyncio

    client, auth, social, pending = oauth_app
    _save_login(auth, social, dict(BOUND))
    hold.begin_bind_check(auth.parent)
    _logged_in(monkeypatch)
    checking, finish = asyncio.Event(), asyncio.Event()

    async def bind(*_args):
        checking.set()
        await finish.wait()
        return dict(BOUND)

    monkeypatch.setattr(O, "_oauth_guest_bind", bind)
    recovery = asyncio.create_task(O.reconfirm_device_owner())
    try:
        await asyncio.wait_for(checking.wait(), timeout=3)
        transport = httpx.ASGITransport(app=client.app, client=("127.0.0.1", 50000))
        async with httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1") as http:
            response = await asyncio.wait_for(http.post("/api/card-drop/oauth/start"), timeout=3)
        assert response.status_code == 200
        assert pending.exists()
        assert hold.check_pending(auth.parent)
    finally:
        finish.set()
        await recovery
    assert not hold.check_pending(auth.parent)


async def test_recovery_skips_a_signed_out_or_rejected_session(oauth_app, monkeypatch):  # noqa: F811
    _client, auth, _social, _pending = oauth_app
    hold.begin_bind_check(auth.parent)

    async def rejected():
        return {"logged_in": False, "snapshot": None, "auth": {}}

    monkeypatch.setattr(O, "resolve_saved_oauth_status", rejected)
    asked = _answer(monkeypatch, BOUND)
    await O.reconfirm_device_owner()
    assert asked == []
    assert hold.check_pending(auth.parent)


@pytest.mark.parametrize("status_code", [500, 502, 504])
async def test_bind_server_error_is_an_unknown_answer(tmp_path, monkeypatch, status_code):
    """A proxy can fail after the cloud committed the bind: keep the check."""
    monkeypatch.setattr(C, "_get_client_credentials", lambda: ("local-client", "local-proof"))

    def handle(request):
        path = request.url.path
        if path == "/api/auth/bind-client/challenge":
            return httpx.Response(200, json={"binding_challenge": "C" * 43})
        if path == "/api/clients/bind-approval":
            return httpx.Response(204)
        if path == "/api/auth/bind-client":
            return httpx.Response(status_code, json={"detail": "upstream"})
        raise AssertionError(f"unexpected {request.method} {request.url}")

    transport = httpx.MockTransport(handle)
    real_client = httpx.AsyncClient
    monkeypatch.setattr(O.httpx, "AsyncClient", lambda **kwargs: real_client(transport=transport))

    bind = await O._oauth_guest_bind("https://community.example", "account-b-access")

    assert bind == {"bound": False, "error": hold.CLOUD_UNREACHABLE}
    assert hold.begin_bind_check(tmp_path) is True
    assert hold.end_bind_check(tmp_path, True, bind)
    assert hold.check_pending(tmp_path)
