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
