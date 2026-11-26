"""Bug-condition exploration property test for PR #1480 review item 1.6.

Bug 1.6 — Bridge token only accepted via ``?token=...`` query string
====================================================================

The four ``/market/oauth/{status,start,complete,logout}`` endpoints in
``plugin/server/routes/market_bridge.py`` declare bridge token as a
required query parameter::

    token: str = Query(..., description="Bridge token")

The frontend (``frontend/plugin-manager/src/composables/useMarketAuth.ts``)
appends the bridge token to the URL as ``?token=<good>``. That leaks the
token into:

* browser history;
* HTTP ``Referer`` headers when the page later navigates;
* server / reverse-proxy access logs (which routinely capture the full
  request line including query string).

The expected fix is to make the backend accept the token via
``Authorization: Bearer <token>`` header (the new path) while keeping
the ``?token=`` query branch alive for one release as a backward-
compatibility window. See bugfix.md §1.6 / §2.6 and design.md
"Phase 3 — Bridge token via Authorization: Bearer header".

Property under test (Bug Condition C(X) — exploration form)
-----------------------------------------------------------

For *every* endpoint in
``{'/market/oauth/status', '/market/oauth/start', '/market/oauth/complete',
'/market/oauth/logout'}``, a request that

1. carries ``Authorization: Bearer <_BRIDGE_TOKEN>`` (the live process
   token), AND
2. does NOT include ``?token=...`` in its URL,

SHOULD succeed (HTTP 200) once the dual-accept fix lands. Specifically,
``/oauth/start`` returns 200 with an ``auth_url`` (because ``NEKO_AUTH_URL``
and ``MARKET_API_URL`` are populated in this test), ``/oauth/status``
and ``/oauth/complete`` and ``/oauth/logout`` all return 200 with
"unauthenticated / no pending session / logged out" payloads.

This file ASSERTS ``response.status_code == 200`` (the "or non-403"
form from tasks.md 1.3 — see note below). On the **unfixed** code path
the assertion FAILS because the missing required ``?token=`` query
parameter trips FastAPI's Pydantic validation BEFORE ``_verify_token``
even runs, returning ``422 Unprocessable Entity``.

A note on the failure status code
---------------------------------

bugfix.md §1.6 anticipated ``403`` (``_verify_token`` raising
``HTTPException(403, "无效的 bridge token")``). The actual observed
counterexample on unfixed code is ``422`` — FastAPI rejects the
request at the validation layer because the required ``Query(...)``
field is missing, so ``_verify_token`` is never called and there is no
opportunity to fall back to the ``Authorization`` header. The
distinction does not change the bug: the bridge token *cannot* be
delivered via header today, regardless of whether the rejection
materialises as 403 or 422. Asserting ``== 200`` (i.e. "the
authenticated happy path") captures both flavors uniformly.

Documented counterexample
-------------------------

``POST /market/oauth/start`` carrying ``Authorization: Bearer <good>``
and no ``?token=`` returns ``422`` on unfixed code (Pydantic complains
the ``token`` query parameter is required) — proof that the header
channel is not wired in. The dedicated
``test_oauth_start_with_authorization_header_documented_counterexample``
test below pins this exact case for the regression suite.

**Validates: Requirements 1.6**
"""

from __future__ import annotations

import asyncio
import tempfile
from pathlib import Path
from typing import Any

import httpx
import pytest
from hypothesis import HealthCheck, given, settings, strategies as st


# Method assignment: the design and current code declare
# ``/oauth/status`` as GET and the other three as POST. Sending the
# wrong method would itself produce 405 and mask the bug. Keep this in
# sync with ``plugin/server/routes/market_bridge.py``.
_ENDPOINT_METHOD: dict[str, str] = {
    "/market/oauth/status": "GET",
    "/market/oauth/start": "POST",
    "/market/oauth/complete": "POST",
    "/market/oauth/logout": "POST",
}

_ENDPOINTS = sorted(_ENDPOINT_METHOD.keys())


@settings(
    deadline=None,
    max_examples=10,
    # Hypothesis can't take pytest fixtures cleanly with the @given
    # decorator on a sync function; we side-step fixtures entirely and
    # do per-example monkeypatching inside ``_run_one``.
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)
@given(endpoint=st.sampled_from(_ENDPOINTS))
def test_oauth_endpoints_accept_authorization_header_only(endpoint: str) -> None:
    """For each ``/market/oauth/*`` endpoint, sending the bridge token
    via ``Authorization: Bearer ...`` (no ``?token=`` query) SHOULD
    succeed (HTTP 200). On the unfixed code path FastAPI's required
    ``Query(...)`` validation fires first and returns 422 — the bug.
    """

    # Run the async coroutine in a private event loop and tear it down
    # cleanly. We deliberately don't use ``asyncio.run`` because it
    # blanks the thread's "current loop" via ``set_event_loop(None)``
    # on close — Python 3.11+ no longer auto-creates a fresh one for the
    # next ``asyncio.get_event_loop()`` caller, so a downstream pytest
    # fixture that still uses the deprecated ``get_event_loop()`` API
    # would error with "There is no current event loop in thread
    # 'MainThread'." after this Hypothesis test ran.
    prev_loop: asyncio.AbstractEventLoop | None
    try:
        prev_loop = asyncio.get_event_loop_policy().get_event_loop()
    except RuntimeError:
        prev_loop = None
    loop = asyncio.new_event_loop()
    try:
        asyncio.set_event_loop(loop)
        loop.run_until_complete(_run_one(endpoint))
    finally:
        loop.close()
        asyncio.set_event_loop(prev_loop)


async def _run_one(endpoint: str) -> None:
    method = _ENDPOINT_METHOD[endpoint]

    # Defer the import: importing market_bridge has light side effects
    # (logger setup, ``_BRIDGE_TOKEN`` minting at first import) and we
    # want module collection to stay cheap.
    from plugin.server.routes import market_bridge as market_bridge_module
    from plugin.server.routes.market_bridge import (
        _BRIDGE_TOKEN,
        router as market_bridge_router,
    )
    from fastapi import FastAPI

    # Build a fresh FastAPI app with only the bridge router mounted.
    # This isolates the test from app.main_server's heavier dependency
    # graph and matches the precedent in
    # plugin/tests/integration/test_market_bridge_e2e.py (its
    # ``bridge_app`` fixture also uses ``FastAPI() + include_router``).
    app = FastAPI(title="oauth-header-exploration")
    app.include_router(market_bridge_router)

    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)

        # Capture and override the module-level state files so we don't
        # touch ``~/.neko/`` on the developer machine. Using setattr +
        # try/finally for hypothesis-safety (each example restores
        # state independently).
        saved_pending = market_bridge_module._OAUTH_PENDING_FILE
        saved_callback = market_bridge_module._OAUTH_CALLBACK_FILE
        saved_token = market_bridge_module._OAUTH_TOKEN_FILE
        saved_auth_url = market_bridge_module.NEKO_AUTH_URL
        saved_market_api_url = market_bridge_module.MARKET_API_URL
        saved_market_web_url = market_bridge_module.MARKET_WEB_URL

        market_bridge_module._OAUTH_PENDING_FILE = tmp / "market_oauth_pending.json"
        market_bridge_module._OAUTH_CALLBACK_FILE = tmp / "oauth_callback.json"
        market_bridge_module._OAUTH_TOKEN_FILE = tmp / "market_auth.json"
        # ``/oauth/start`` raises 400 if either NEKO_AUTH_URL or
        # MARKET_API_URL is empty. Pin both to known non-empty values
        # so the unfixed-vs-fixed distinction is bridged solely on
        # token transport.
        market_bridge_module.NEKO_AUTH_URL = "https://auth.test"
        market_bridge_module.MARKET_API_URL = "https://market.test"
        market_bridge_module.MARKET_WEB_URL = "https://web.market.test"

        try:
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(
                transport=transport, base_url="http://testserver"
            ) as client:
                # The whole point of this test: token only in header,
                # NEVER in URL query. ``client.request`` does not append
                # any params unless told to.
                response = await client.request(
                    method,
                    endpoint,
                    headers={"Authorization": f"Bearer {_BRIDGE_TOKEN}"},
                )
        finally:
            market_bridge_module._OAUTH_PENDING_FILE = saved_pending
            market_bridge_module._OAUTH_CALLBACK_FILE = saved_callback
            market_bridge_module._OAUTH_TOKEN_FILE = saved_token
            market_bridge_module.NEKO_AUTH_URL = saved_auth_url
            market_bridge_module.MARKET_API_URL = saved_market_api_url
            market_bridge_module.MARKET_WEB_URL = saved_market_web_url

    # ── The bug assertion ─────────────────────────────────────────────
    # Post-fix, the bridge accepts ``Authorization: Bearer`` and each
    # endpoint takes its happy path:
    #   /oauth/status   → 200 {authenticated: false, ...}
    #   /oauth/start    → 200 {auth_url: ..., state: ..., expires_in: ...}
    #   /oauth/complete → 200 {completed: false, authenticated: false, ...}
    #   /oauth/logout   → 200 {message: "..."}
    # On the unfixed code, FastAPI's required ``Query(...)`` validation
    # fires before ``_verify_token`` and returns 422 — the bug.
    body_preview: Any
    try:
        body_preview = response.json()
    except Exception:
        body_preview = response.text[:200]
    assert response.status_code == 200, (
        f"BUG 1.6 reproduced: {method} {endpoint} with Authorization: Bearer"
        f" header (and no ?token= query) returned status="
        f"{response.status_code}, body={body_preview!r}. The bridge token"
        " cannot be delivered via the Authorization header today;"
        " _verify_token only reads ?token=... from the query string,"
        " and FastAPI's required Query(...) validator rejects the"
        " request at the validation layer (HTTP 422) before"
        " _verify_token is even called."
    )


# ---------------------------------------------------------------------------
# Documented counterexample (also runnable as a standalone unit test so the
# specific failing input from bugfix.md §1.6 is checked into the regression
# suite, not just discovered via Hypothesis).
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_oauth_start_with_authorization_header_documented_counterexample() -> None:
    """Anchor counterexample: ``POST /market/oauth/start`` with
    ``Authorization: Bearer <good>`` and no ``?token=`` query exposes
    the bridge token transport gap.

    On the unfixed code this test FAILS (status 422, not 200) because
    FastAPI rejects the missing required query parameter before
    ``_verify_token`` runs. After the dual-accept fix it returns 200
    with an ``auth_url`` payload.
    """

    await _run_one("/market/oauth/start")


def test_write_private_json_is_atomic_and_sets_0600_permissions(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import json
    import os
    import stat

    from plugin.server.routes import market_bridge as market_bridge_module
    import utils.file_utils as file_utils_mod

    target = tmp_path / "market_auth.json"
    market_bridge_module._write_private_json(
        target, {"access_token": "at-1", "refresh_token": "rt-1"}
    )
    assert json.loads(target.read_text(encoding="utf-8"))["refresh_token"] == "rt-1"
    if os.name == "posix":
        assert stat.S_IMODE(target.stat().st_mode) == 0o600

    # Failure during atomic replace must preserve prior token file intact with no leftover tmp files.
    def _boom_replace(_src, _dst):
        raise OSError("disk full")

    monkeypatch.setattr(file_utils_mod.os, "replace", _boom_replace)
    with pytest.raises(OSError, match="disk full"):
        market_bridge_module._write_private_json(
            target, {"access_token": "at-2", "refresh_token": "rt-2"}
        )

    assert json.loads(target.read_text(encoding="utf-8"))["refresh_token"] == "rt-1"
    assert not [p for p in tmp_path.iterdir() if p.name != "market_auth.json"]


@pytest.mark.asyncio
async def test_market_oauth_status_writes_token_off_event_loop_and_recovers_from_winerror5(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import json
    import threading
    import time

    from fastapi import FastAPI

    import utils.file_utils as file_utils_mod
    from plugin.server.routes import market_bridge as market_bridge_module

    token_file = tmp_path / "market_auth.json"
    monkeypatch.setattr(market_bridge_module, "_OAUTH_TOKEN_FILE", token_file)
    monkeypatch.setattr(
        market_bridge_module, "_OAUTH_PENDING_FILE", tmp_path / "pending.json"
    )
    monkeypatch.setattr(
        market_bridge_module, "_OAUTH_CALLBACK_FILE", tmp_path / "callback.json"
    )
    monkeypatch.setattr(market_bridge_module, "NEKO_AUTH_URL", "https://auth.test")
    monkeypatch.setattr(market_bridge_module, "MARKET_API_URL", "https://market.test")
    monkeypatch.setattr(
        market_bridge_module, "MARKET_WEB_URL", "https://web.market.test"
    )

    initial_token = {
        "access_token": "at-status-1",
        "refresh_token": "rt-status-1",
        "token_type": "bearer",
        "scope": market_bridge_module._OAUTH_SCOPE,
        "expires_at": time.time() + 3600,
        "auth_url": "https://auth.test",
        "issuer": "https://auth.test/",
        "client_id": market_bridge_module._OAUTH_CLIENT_ID,
        "subject": "sub-status-1",
        "subject_pending": False,
        "refresh_generation": 0,
        "state_revision": 0,
    }
    market_bridge_module._write_private_json(token_file, initial_token)

    async def _fake_probe_market_user(_access_token: Any) -> Any:
        return market_bridge_module._MarketUserProbe(
            state="ready", user={"username": "neko"}
        )

    monkeypatch.setattr(
        market_bridge_module, "_probe_market_user", _fake_probe_market_user
    )

    loop_tid = threading.get_ident()
    write_observations: list[tuple[bool, int]] = []
    real_write_private = market_bridge_module._write_private_json

    def _spy_write_private(path: Path, payload: dict[str, Any]) -> None:
        write_observations.append(
            (file_utils_mod.running_on_event_loop(), threading.get_ident())
        )
        real_write_private(path, payload)

    monkeypatch.setattr(market_bridge_module, "_write_private_json", _spy_write_private)

    real_replace = file_utils_mod.os.replace
    replace_attempts = 0

    def _transient_winerror5_replace(src: Any, dst: Any) -> None:
        nonlocal replace_attempts
        replace_attempts += 1
        if replace_attempts == 1:
            exc = PermissionError("Access is denied")
            exc.winerror = 5  # type: ignore[attr-defined]
            raise exc
        real_replace(src, dst)

    monkeypatch.setattr(file_utils_mod.os, "replace", _transient_winerror5_replace)

    app = FastAPI(title="oauth-status-off-loop")
    app.include_router(market_bridge_module.router)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport, base_url="http://testserver"
    ) as client:
        response = await client.get(
            "/market/oauth/status",
            headers={"Authorization": f"Bearer {market_bridge_module._BRIDGE_TOKEN}"},
        )

    assert response.status_code == 200
    body = response.json()
    assert body["authenticated"] is True
    assert body["market_state"] == "ready"
    assert write_observations
    assert all(on_loop is False and tid != loop_tid for on_loop, tid in write_observations)
    assert replace_attempts >= 2
    saved = json.loads(token_file.read_text(encoding="utf-8"))
    assert saved["state_revision"] == 1
    assert saved["market_state"] == "ready"


@pytest.mark.asyncio
async def test_ensure_valid_oauth_token_refreshes_off_event_loop_and_recovers_from_winerror5(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import json
    import threading
    import time

    import utils.file_utils as file_utils_mod
    from plugin.server.routes import market_bridge as market_bridge_module

    token_file = tmp_path / "market_auth.json"
    monkeypatch.setattr(market_bridge_module, "_OAUTH_TOKEN_FILE", token_file)
    monkeypatch.setattr(market_bridge_module, "NEKO_AUTH_URL", "https://auth.test")
    monkeypatch.setattr(market_bridge_module, "MARKET_API_URL", "https://market.test")
    monkeypatch.setattr(
        market_bridge_module, "MARKET_WEB_URL", "https://web.market.test"
    )

    expiring_token = {
        "access_token": "at-old",
        "refresh_token": "rt-old",
        "token_type": "bearer",
        "scope": market_bridge_module._OAUTH_SCOPE,
        "expires_at": time.time() - 30,
        "auth_url": "https://auth.test",
        "issuer": "https://auth.test/",
        "client_id": market_bridge_module._OAUTH_CLIENT_ID,
        "subject": "sub-refresh-1",
        "subject_pending": False,
        "refresh_generation": 0,
        "state_revision": 0,
    }
    market_bridge_module._write_private_json(token_file, expiring_token)

    async def _fake_refresh_oauth_token(token_data: dict[str, Any]) -> dict[str, Any]:
        refreshed = dict(token_data)
        refreshed.update(
            {
                "access_token": "at-new",
                "refresh_token": "rt-new",
                "expires_at": time.time() + 3600,
                "refresh_generation": 1,
                "updated_at": time.time(),
            }
        )
        return refreshed

    monkeypatch.setattr(
        market_bridge_module, "_refresh_oauth_token", _fake_refresh_oauth_token
    )

    loop_tid = threading.get_ident()
    write_observations: list[tuple[bool, int]] = []
    real_write_private = market_bridge_module._write_private_json

    def _spy_write_private(path: Path, payload: dict[str, Any]) -> None:
        write_observations.append(
            (file_utils_mod.running_on_event_loop(), threading.get_ident())
        )
        real_write_private(path, payload)

    monkeypatch.setattr(market_bridge_module, "_write_private_json", _spy_write_private)

    real_replace = file_utils_mod.os.replace
    replace_attempts = 0

    def _transient_winerror5_replace(src: Any, dst: Any) -> None:
        nonlocal replace_attempts
        replace_attempts += 1
        if replace_attempts == 1:
            exc = PermissionError("Access is denied")
            exc.winerror = 5  # type: ignore[attr-defined]
            raise exc
        real_replace(src, dst)

    monkeypatch.setattr(file_utils_mod.os, "replace", _transient_winerror5_replace)

    result = await market_bridge_module._ensure_valid_oauth_token()

    assert result is not None
    assert result["access_token"] == "at-new"
    assert result["refresh_token"] == "rt-new"
    assert write_observations
    assert all(on_loop is False and tid != loop_tid for on_loop, tid in write_observations)
    assert replace_attempts >= 2
    saved = json.loads(token_file.read_text(encoding="utf-8"))
    assert saved["refresh_token"] == "rt-new"
    assert saved["state_revision"] == 1


@pytest.mark.asyncio
async def test_oauth_complete_does_not_undo_concurrent_logout_or_clobber_new_session(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import asyncio
    import json
    import time

    from fastapi import FastAPI
    import httpx

    from plugin.server.routes import market_bridge as market_bridge_module

    pending_file = tmp_path / "market_oauth_pending.json"
    callback_file = tmp_path / "oauth_callback.json"
    token_file = tmp_path / "market_auth.json"
    monkeypatch.setattr(market_bridge_module, "_OAUTH_PENDING_FILE", pending_file)
    monkeypatch.setattr(market_bridge_module, "_OAUTH_CALLBACK_FILE", callback_file)
    monkeypatch.setattr(market_bridge_module, "_OAUTH_TOKEN_FILE", token_file)
    monkeypatch.setattr(market_bridge_module, "NEKO_AUTH_URL", "https://auth.test")
    monkeypatch.setattr(market_bridge_module, "MARKET_API_URL", "https://market.test")
    monkeypatch.setattr(market_bridge_module, "MARKET_WEB_URL", "https://web.market.test")

    market_bridge_module._write_private_json(
        pending_file,
        {
            "state": "state-A",
            "code_verifier": "verifier-A",
            "redirect_uri": "http://127.0.0.1:48916/market/oauth/callback",
            "expires_at": time.time() + 300,
        },
    )
    market_bridge_module._write_private_json(
        callback_file,
        {"code": "code-A", "state": "state-A", "timestamp": time.time()},
    )

    revoked: list[dict[str, Any]] = []
    exchange_entered = asyncio.Event()
    exchange_release = asyncio.Event()

    async def _paused_exchange(_code: str, _verifier: str, _redirect: str) -> dict[str, Any]:
        exchange_entered.set()
        await exchange_release.wait()
        return {"access_token": "at-A", "refresh_token": "rt-A", "expires_in": 3600}

    async def _fake_userinfo(_access_token: str | None) -> dict[str, Any]:
        return {"sub": "user-A"}

    async def _fake_probe(_access_token: str | None) -> Any:
        return market_bridge_module._MarketUserProbe(
            state="ready",
            user={"id": "u1"},
            retryable=False,
        )

    async def _fake_revoke(payload: dict[str, Any]) -> None:
        revoked.append(dict(payload))

    monkeypatch.setattr(market_bridge_module, "_exchange_oauth_code", _paused_exchange)
    monkeypatch.setattr(market_bridge_module, "_fetch_auth_userinfo", _fake_userinfo)
    monkeypatch.setattr(market_bridge_module, "_probe_market_user", _fake_probe)
    monkeypatch.setattr(market_bridge_module, "_revoke_oauth_token_best_effort", _fake_revoke)

    app = FastAPI(title="oauth-race")
    app.include_router(market_bridge_module.router)
    transport = httpx.ASGITransport(app=app)
    headers = {"Authorization": f"Bearer {market_bridge_module._BRIDGE_TOKEN}"}

    async def _wait_for_stage(event: asyncio.Event, task: asyncio.Task[Any]) -> None:
        done, _ = await asyncio.wait(
            {asyncio.create_task(event.wait()), task},
            timeout=5.0,
            return_when=asyncio.FIRST_COMPLETED,
        )
        if task in done and not event.is_set():
            raise AssertionError(f"/oauth/complete exited early: {task.result().status_code}")
        assert event.is_set()

    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        # Case 1: Logout runs before complete's atomic check-and-write -> complete returns 409, no token on disk.
        complete_task = asyncio.create_task(client.post("/market/oauth/complete", headers=headers))
        try:
            await _wait_for_stage(exchange_entered, complete_task)
            logout_resp = await client.post("/market/oauth/logout", headers=headers)
            assert logout_resp.status_code == 200
        finally:
            exchange_release.set()
        complete_resp = await complete_task
        assert complete_resp.status_code == 409
        assert not token_file.exists()
        assert revoked and revoked[-1]["access_token"] == "at-A"

        # Case 2: Replacement login B starts while A is completing -> B's pending file survives!
        market_bridge_module._write_private_json(
            pending_file,
            {
                "state": "state-A",
                "code_verifier": "verifier-A",
                "redirect_uri": "http://127.0.0.1:48916/market/oauth/callback",
                "expires_at": time.time() + 300,
            },
        )
        market_bridge_module._write_private_json(
            callback_file,
            {"code": "code-A", "state": "state-A", "timestamp": time.time()},
        )
        exchange_entered.clear()
        exchange_release.clear()
        complete_task2 = asyncio.create_task(client.post("/market/oauth/complete", headers=headers))
        try:
            await _wait_for_stage(exchange_entered, complete_task2)
            start_resp = await client.post("/market/oauth/start", headers=headers)
            assert start_resp.status_code == 200
            state_b = start_resp.json()["state"]
        finally:
            exchange_release.set()
        complete_resp2 = await complete_task2
        assert complete_resp2.status_code == 409
        assert not token_file.exists()
        assert json.loads(pending_file.read_text(encoding="utf-8"))["state"] == state_b

        # Case 3: Stale callback for session A cannot overwrite session B's callback after /oauth/start.
        cb_b = await client.get(
            "/market/oauth/callback",
            params={"code": "code-B", "state": state_b},
        )
        assert cb_b.status_code == 200
        cb_stale = await client.get(
            "/market/oauth/callback",
            params={"code": "code-A", "state": "state-A"},
        )
        assert cb_stale.status_code == 400
        assert json.loads(pending_file.read_text(encoding="utf-8"))["state"] == state_b
        assert json.loads(callback_file.read_text(encoding="utf-8"))["state"] == state_b

        # Case 4: Logout right before final _commit_completed_oauth_session_sync (during probe after userinfo) -> 409 & revoked.
        market_bridge_module._write_private_json(
            pending_file,
            {
                "state": "state-C",
                "code_verifier": "verifier-C",
                "redirect_uri": "http://127.0.0.1:48916/market/oauth/callback",
                "expires_at": time.time() + 300,
            },
        )
        market_bridge_module._write_private_json(
            callback_file,
            {"code": "code-C", "state": "state-C", "timestamp": time.time()},
        )
        probe_entered = asyncio.Event()
        probe_release = asyncio.Event()

        async def _paused_probe(_access_token: str | None) -> Any:
            probe_entered.set()
            await probe_release.wait()
            return await _fake_probe(_access_token)

        monkeypatch.setattr(market_bridge_module, "_probe_market_user", _paused_probe)
        complete_task3 = asyncio.create_task(client.post("/market/oauth/complete", headers=headers))
        try:
            await _wait_for_stage(probe_entered, complete_task3)
            logout_resp3 = await client.post("/market/oauth/logout", headers=headers)
            assert logout_resp3.status_code == 200
        finally:
            probe_release.set()
        complete_resp3 = await complete_task3
        assert complete_resp3.status_code == 409
        assert not token_file.exists()
        assert len(revoked) >= 3


@pytest.mark.asyncio
async def test_oauth_write_lock_serializes_status_write_against_logout_and_refresh(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import asyncio
    import json
    import threading
    import time

    from plugin.server.routes import market_bridge as market_bridge_module

    token_file = tmp_path / "market_auth.json"
    pending_file = tmp_path / "market_oauth_pending.json"
    callback_file = tmp_path / "oauth_callback.json"
    monkeypatch.setattr(market_bridge_module, "_OAUTH_TOKEN_FILE", token_file)
    monkeypatch.setattr(market_bridge_module, "_OAUTH_PENDING_FILE", pending_file)
    monkeypatch.setattr(market_bridge_module, "_OAUTH_CALLBACK_FILE", callback_file)
    monkeypatch.setattr(market_bridge_module, "NEKO_AUTH_URL", "https://auth.test")
    monkeypatch.setattr(market_bridge_module, "MARKET_API_URL", "https://market.test")

    snapshot = {
        "access_token": "at-1",
        "refresh_token": "rt-1",
        "expires_at": time.time() + 3600,
        "auth_url": "https://auth.test",
        "issuer": "https://auth.test/",
        "client_id": market_bridge_module._OAUTH_CLIENT_ID,
        "session_id": "sess-1",
        "refresh_generation": 0,
        "state_revision": 0,
    }
    market_bridge_module._write_private_json(token_file, snapshot)

    entered_lock = threading.Event()
    release_lock = threading.Event()
    logout_queued = threading.Event()
    real_write_private = market_bridge_module._write_private_json
    real_logout = market_bridge_module._logout_oauth_sync

    def _slow_first_write(path: Path, payload: dict[str, Any]) -> None:
        if payload.get("state_revision") == 1:
            entered_lock.set()
            release_lock.wait(timeout=5.0)
        real_write_private(path, payload)

    def _signaling_logout() -> dict[str, Any] | None:
        logout_queued.set()
        return real_logout()

    monkeypatch.setattr(market_bridge_module, "_write_private_json", _slow_first_write)
    monkeypatch.setattr(market_bridge_module, "_logout_oauth_sync", _signaling_logout)

    updated = dict(snapshot, market_state="ready")
    writer_task = asyncio.create_task(
        asyncio.to_thread(
            market_bridge_module._write_oauth_token_if_matches, snapshot, updated
        )
    )
    try:
        assert await asyncio.to_thread(entered_lock.wait, 5.0) is True
        # Logout queued behind _OAUTH_STATE_FILE_LOCK must not run until writer releases lock,
        # and once released, logout deletes the file instead of writer resurrecting it.
        logout_task = asyncio.create_task(
            asyncio.to_thread(market_bridge_module._logout_oauth_sync)
        )
        assert await asyncio.to_thread(logout_queued.wait, 5.0) is True
        assert not logout_task.done()
    finally:
        release_lock.set()
    assert await writer_task is True
    await logout_task
    assert not token_file.exists()

    # Now verify stale snapshot cannot overwrite newer revision.
    market_bridge_module._write_private_json(
        token_file, dict(snapshot, access_token="at-2", refresh_generation=1, state_revision=0)
    )
    assert (
        await asyncio.to_thread(
            market_bridge_module._write_oauth_token_if_matches, snapshot, dict(snapshot, market_state="ready")
        )
        is False
    )
    assert json.loads(token_file.read_text(encoding="utf-8"))["access_token"] == "at-2"


def test_write_private_json_scrubs_legacy_nan_in_user_only(tmp_path: Path) -> None:
    import json
    from plugin.server.routes import market_bridge as market_bridge_module

    token_file = tmp_path / "market_auth.json"
    market_bridge_module._write_private_json(
        token_file,
        {
            "access_token": "at-1",
            "user": {"id": "u1", "score": float("nan")},
        },
    )
    saved = json.loads(token_file.read_text(encoding="utf-8"))
    assert saved["user"] == {"id": "u1"}

    with pytest.raises(ValueError):
        market_bridge_module._write_private_json(
            token_file,
            {"access_token": "at-1", "expires_at": float("nan")},
        )


def test_commit_refreshed_oauth_token_sync_merges_concurrent_revision_atomically(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import json
    import time
    from plugin.server.routes import market_bridge as market_bridge_module

    token_file = tmp_path / "market_auth.json"
    monkeypatch.setattr(market_bridge_module, "_OAUTH_TOKEN_FILE", token_file)
    monkeypatch.setattr(market_bridge_module, "NEKO_AUTH_URL", "https://auth.test")
    monkeypatch.setattr(market_bridge_module, "MARKET_API_URL", "https://market.test")

    current = {
        "access_token": "at-old",
        "refresh_token": "rt-old",
        "expires_at": time.time() + 300,
        "auth_url": "https://auth.test",
        "issuer": "https://auth.test/",
        "client_id": market_bridge_module._OAUTH_CLIENT_ID,
        "session_id": "sess-1",
        "subject": "sub-1",
        "subject_pending": False,
        "refresh_generation": 0,
        "state_revision": 0,
        "scope": market_bridge_module._OAUTH_SCOPE,
    }
    disk_later = dict(current, market_state="ready", state_revision=2, user={"id": "u1"})
    market_bridge_module._write_private_json(token_file, disk_later)

    refreshed = dict(
        current,
        access_token="at-new",
        refresh_token="rt-new",
        refresh_generation=1,
    )
    committed = market_bridge_module._commit_refreshed_oauth_token_sync(current, refreshed)
    assert committed is not None
    assert committed["access_token"] == "at-new"
    assert committed["refresh_token"] == "rt-new"
    assert committed["market_state"] == "ready"
    assert committed["state_revision"] == 3
    saved = json.loads(token_file.read_text(encoding="utf-8"))
    assert saved["access_token"] == "at-new"
    assert saved["state_revision"] == 3


def test_store_account_summary_if_snapshot_matches_rejects_replaced_session(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import time
    from plugin.server.routes import market_bridge as market_bridge_module

    token_file = tmp_path / "market_auth.json"
    monkeypatch.setattr(market_bridge_module, "_OAUTH_TOKEN_FILE", token_file)
    market_bridge_module._clear_account_summary_cache()

    old_snap = {
        "access_token": "at-old",
        "session_id": "sess-old",
        "subject": "sub-1",
        "refresh_generation": 0,
        "state_revision": 0,
    }
    new_snap = dict(old_snap, access_token="at-new", session_id="sess-new")
    market_bridge_module._write_private_json(token_file, new_snap)

    summary = market_bridge_module.MarketOAuthAccountSummaryResponse(
        authenticated=True,
        auth_state="ready",
        market_state="ready",
        subject="sub-1",
        expires_at=time.time() + 3600,
        sources={
            "auth": market_bridge_module.MarketOAuthAccountSource(status="unavailable"),
            "market": market_bridge_module.MarketOAuthAccountSource(status="unavailable"),
            "community": market_bridge_module.MarketOAuthAccountSource(status="unavailable"),
        },
    )
    key_old = market_bridge_module._account_summary_cache_key(old_snap)
    key_new = market_bridge_module._account_summary_cache_key(new_snap)
    assert key_old != key_new
    assert (
        market_bridge_module._store_account_summary_if_snapshot_matches_sync(
            old_snap, key_old, summary
        )
        is False
    )
    assert market_bridge_module._fresh_account_summary(key_old) is None


@pytest.mark.asyncio
async def test_ensure_valid_oauth_token_shields_commit_against_task_cancellation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import asyncio
    import json
    import threading
    import time
    from plugin.server.routes import market_bridge as market_bridge_module

    token_file = tmp_path / "market_auth.json"
    monkeypatch.setattr(market_bridge_module, "_OAUTH_TOKEN_FILE", token_file)
    monkeypatch.setattr(market_bridge_module, "NEKO_AUTH_URL", "https://auth.test")
    monkeypatch.setattr(market_bridge_module, "MARKET_API_URL", "https://market.test")

    current = {
        "access_token": "at-old",
        "refresh_token": "rt-old",
        "expires_at": time.time() - 60,
        "auth_url": "https://auth.test",
        "issuer": "https://auth.test/",
        "client_id": market_bridge_module._OAUTH_CLIENT_ID,
        "session_id": "sess-1",
        "subject": "sub-1",
        "subject_pending": False,
        "refresh_generation": 0,
        "state_revision": 0,
        "scope": market_bridge_module._OAUTH_SCOPE,
    }
    market_bridge_module._write_private_json(token_file, current)

    commit_entered = threading.Event()
    commit_release = threading.Event()
    commit_done = threading.Event()
    real_commit = market_bridge_module._commit_refreshed_oauth_token_sync

    async def _fake_refresh(_tok: dict[str, Any]) -> dict[str, Any]:
        return dict(
            current,
            access_token="at-rotated",
            refresh_token="rt-rotated",
            expires_at=time.time() + 3600,
            refresh_generation=1,
        )

    def _blocked_commit(cur: dict[str, Any], ref: dict[str, Any]) -> dict[str, Any] | None:
        commit_entered.set()
        commit_release.wait(timeout=5.0)
        try:
            return real_commit(cur, ref)
        finally:
            commit_done.set()

    monkeypatch.setattr(market_bridge_module, "_refresh_oauth_token", _fake_refresh)
    monkeypatch.setattr(
        market_bridge_module, "_commit_refreshed_oauth_token_sync", _blocked_commit
    )

    task = asyncio.create_task(market_bridge_module._ensure_valid_oauth_token())
    try:
        assert await asyncio.to_thread(commit_entered.wait, 5.0) is True
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    finally:
        commit_release.set()

    assert await asyncio.to_thread(commit_done.wait, 5.0) is True
    saved = json.loads(token_file.read_text(encoding="utf-8"))
    assert saved["refresh_token"] == "rt-rotated"


@pytest.mark.asyncio
async def test_ensure_valid_oauth_token_holds_refresh_lock_and_shields_queued_commit_on_cancel(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import asyncio
    from concurrent.futures import ThreadPoolExecutor
    import json
    import threading
    import time
    from plugin.server.routes import market_bridge as market_bridge_module

    token_file = tmp_path / "market_auth.json"
    monkeypatch.setattr(market_bridge_module, "_OAUTH_TOKEN_FILE", token_file)
    monkeypatch.setattr(market_bridge_module, "NEKO_AUTH_URL", "https://auth.test")
    monkeypatch.setattr(market_bridge_module, "MARKET_API_URL", "https://market.test")

    current = {
        "access_token": "at-old",
        "refresh_token": "rt-old",
        "expires_at": time.time() - 60,
        "auth_url": "https://auth.test",
        "issuer": "https://auth.test/",
        "client_id": market_bridge_module._OAUTH_CLIENT_ID,
        "session_id": "sess-1",
        "subject": "sub-1",
        "subject_pending": False,
        "refresh_generation": 0,
        "state_revision": 0,
        "scope": market_bridge_module._OAUTH_SCOPE,
    }
    market_bridge_module._write_private_json(token_file, current)

    worker_busy = threading.Event()
    worker_release = threading.Event()
    commit_queued = asyncio.Event()
    commit_started = threading.Event()
    refresh_calls = 0

    single_executor = ThreadPoolExecutor(max_workers=1)
    loop = asyncio.get_running_loop()
    real_to_thread = asyncio.to_thread
    real_commit = market_bridge_module._commit_refreshed_oauth_token_sync

    def _occupy_worker() -> None:
        worker_busy.set()
        worker_release.wait(timeout=5.0)

    blocker_future = loop.run_in_executor(single_executor, _occupy_worker)
    assert await real_to_thread(worker_busy.wait, 5.0) is True

    async def _fake_refresh(_tok: dict[str, Any]) -> dict[str, Any]:
        nonlocal refresh_calls
        refresh_calls += 1
        return dict(
            current,
            access_token="at-rotated",
            refresh_token="rt-rotated",
            expires_at=time.time() + 3600,
            refresh_generation=1,
        )

    def _tracked_commit(cur: dict[str, Any], ref: dict[str, Any]) -> dict[str, Any] | None:
        commit_started.set()
        return real_commit(cur, ref)

    async def _gated_to_thread(fn, /, *args, **kwargs):
        if fn is _tracked_commit:
            commit_queued.set()
            return await loop.run_in_executor(single_executor, lambda: fn(*args, **kwargs))
        return await real_to_thread(fn, *args, **kwargs)

    monkeypatch.setattr(market_bridge_module, "_refresh_oauth_token", _fake_refresh)
    monkeypatch.setattr(
        market_bridge_module, "_commit_refreshed_oauth_token_sync", _tracked_commit
    )
    monkeypatch.setattr(asyncio, "to_thread", _gated_to_thread)

    real_refresh_lock = market_bridge_module._OAUTH_REFRESH_LOCK
    second_contender_waiting = asyncio.Event()
    task2: asyncio.Task[Any] | None = None

    class _ObservedRefreshLock:
        async def __aenter__(self) -> Any:
            if asyncio.current_task() is task2:
                second_contender_waiting.set()
            return await real_refresh_lock.__aenter__()

        async def __aexit__(self, exc_type: Any, exc: Any, tb: Any) -> Any:
            return await real_refresh_lock.__aexit__(exc_type, exc, tb)

    monkeypatch.setattr(market_bridge_module, "_OAUTH_REFRESH_LOCK", _ObservedRefreshLock())

    task1 = asyncio.create_task(market_bridge_module._ensure_valid_oauth_token())
    try:
        await asyncio.wait_for(commit_queued.wait(), timeout=5.0)
        await asyncio.sleep(0)
        assert commit_started.is_set() is False

        # Cancel while commit is still queued behind the busy 1-worker executor.
        task1.cancel()
        task2 = asyncio.create_task(market_bridge_module._ensure_valid_oauth_token())
        await asyncio.wait_for(second_contender_waiting.wait(), timeout=5.0)
        await asyncio.sleep(0)
        assert refresh_calls == 1
        assert not task2.done()
    finally:
        worker_release.set()

    await blocker_future
    try:
        with pytest.raises(asyncio.CancelledError):
            await task1
        assert task2 is not None
        res2 = await task2
    finally:
        single_executor.shutdown(wait=True)

    assert res2 is not None
    assert res2["refresh_token"] == "rt-rotated"
    assert refresh_calls == 1
    assert commit_started.is_set() is True
    saved = json.loads(token_file.read_text(encoding="utf-8"))
    assert saved["refresh_token"] == "rt-rotated"


@pytest.mark.asyncio
async def test_ensure_valid_oauth_token_revokes_refreshed_token_when_cancelled_after_logout(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import asyncio
    from concurrent.futures import ThreadPoolExecutor
    import json
    import threading
    import time
    from plugin.server.routes import market_bridge as market_bridge_module

    token_file = tmp_path / "oauth.json"
    monkeypatch.setattr(market_bridge_module, "_OAUTH_TOKEN_FILE", token_file)
    monkeypatch.setattr(
        market_bridge_module, "NEKO_AUTH_URL", "https://auth.test", raising=False
    )
    current = {
        "access_token": "at-old",
        "refresh_token": "rt-old",
        "expires_at": time.time() - 60,
        "auth_url": "https://auth.test",
        "issuer": "https://auth.test/",
        "client_id": market_bridge_module._OAUTH_CLIENT_ID,
        "session_id": "sess-1",
        "subject": "sub-1",
        "subject_pending": False,
        "refresh_generation": 0,
        "state_revision": 0,
        "scope": market_bridge_module._OAUTH_SCOPE,
    }
    market_bridge_module._write_private_json(token_file, current)

    worker_busy = threading.Event()
    worker_release = threading.Event()
    commit_queued = asyncio.Event()
    revoked_tokens: list[dict[str, Any]] = []
    revoke_done = asyncio.Event()

    single_executor = ThreadPoolExecutor(max_workers=1)
    loop = asyncio.get_running_loop()
    real_to_thread = asyncio.to_thread

    def _occupy_worker() -> None:
        worker_busy.set()
        worker_release.wait(timeout=5.0)

    blocker_future = loop.run_in_executor(single_executor, _occupy_worker)
    assert await real_to_thread(worker_busy.wait, 5.0) is True

    async def _fake_refresh(_tok: dict[str, Any]) -> dict[str, Any]:
        return dict(
            current,
            access_token="at-rotated",
            refresh_token="rt-rotated",
            expires_at=time.time() + 3600,
            refresh_generation=1,
        )

    def _logout_wins_commit(_cur: dict[str, Any], _ref: dict[str, Any]) -> None:
        return None

    async def _gated_to_thread(fn, /, *args, **kwargs):
        if fn is _logout_wins_commit:
            commit_queued.set()
            return await loop.run_in_executor(single_executor, lambda: fn(*args, **kwargs))
        return await real_to_thread(fn, *args, **kwargs)

    async def _fake_revoke(tok: dict[str, Any]) -> None:
        await asyncio.sleep(0)
        revoked_tokens.append(dict(tok))
        revoke_done.set()

    monkeypatch.setattr(market_bridge_module, "_refresh_oauth_token", _fake_refresh)
    monkeypatch.setattr(
        market_bridge_module, "_commit_refreshed_oauth_token_sync", _logout_wins_commit
    )
    monkeypatch.setattr(asyncio, "to_thread", _gated_to_thread)
    monkeypatch.setattr(
        market_bridge_module, "_revoke_oauth_token_best_effort", _fake_revoke
    )

    task1 = asyncio.create_task(market_bridge_module._ensure_valid_oauth_token())
    try:
        await asyncio.wait_for(commit_queued.wait(), timeout=5.0)
        await asyncio.sleep(0)
        task1.cancel()
    finally:
        worker_release.set()

    await blocker_future
    try:
        with pytest.raises(asyncio.CancelledError):
            await task1
    finally:
        single_executor.shutdown(wait=True)

    assert revoke_done.is_set() is True
    assert len(revoked_tokens) == 1
    assert revoked_tokens[0]["refresh_token"] == "rt-rotated"
