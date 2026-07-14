"""Bug-condition exploration property test for PR #1480 review item 1.1.

Bug 1.1 — Proxy double Content-Encoding
=======================================

``proxy_user_plugin_market_bridge`` in ``app/main_server/__init__.py`` (around
line 1604) forwards the client's ``Accept-Encoding`` upstream, lets
httpx auto-decompress the upstream response body, and then forwards the
upstream ``Content-Encoding: gzip`` header back to the browser. The
browser then sees ``Content-Encoding: gzip`` together with already-
decompressed bytes and fails with ``ERR_CONTENT_DECODING_FAILED``.

Property under test (Bug Condition C(X) — exploration form)
-----------------------------------------------------------

For *every* binary upstream payload ``body`` returned with
``Content-Encoding: gzip``, the response surfaced by the proxy SHOULD
NOT carry a ``Content-Encoding`` header (because the body has already
been decompressed by httpx). The desired post-fix invariant is

    'content-encoding' NOT IN response.headers

This file ASSERTS that invariant. On the **unfixed** code path the
assertion FAILS — the response still carries ``Content-Encoding: gzip``
alongside the decompressed body — which is exactly the observation
that proves the bug exists.

**Documented counterexample**: ``body=b'{"ok":true}'`` round-trips
through the proxy as decompressed JSON bytes that ALSO carry
``Content-Encoding: gzip``, reproducing the browser-visible double
encoding.

**Validates: Requirements 1.1**
"""

from __future__ import annotations

import asyncio
import gzip
from typing import Any

import httpx
import pytest
from hypothesis import HealthCheck, given, settings, strategies as st


@settings(
    deadline=None,
    max_examples=25,
    # The test reuses module-level monkey-patched state on httpx and the
    # FastAPI app; both are deterministic per call but `function_scoped_fixture`
    # would complain about pytest fixtures crossing examples. We don't take any
    # function-scoped fixtures, but suppress the warning class for clarity.
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)
@given(body=st.binary(min_size=1, max_size=4096))
def test_proxy_forwards_decompressed_body_with_stale_content_encoding(body: bytes) -> None:
    """Document the double-Content-Encoding observable on the unfixed proxy.

    For each generated ``body``:

    1. Mock the upstream ``USER_PLUGIN_BASE`` so that it returns the
       gzip-compressed bytes of ``body`` together with the headers
       ``Content-Encoding: gzip`` + ``Content-Type: application/json``.
       The proxy's internal ``httpx.AsyncClient`` will auto-decompress
       the body on access, so ``upstream.content == body`` while
       ``upstream.headers['content-encoding'] == 'gzip'``.
    2. Drive a request through the real FastAPI app's
       ``proxy_user_plugin_market_bridge`` handler via ASGI.
    3. Assert the post-fix invariant: the response observed by the
       client must NOT carry ``Content-Encoding`` while the body bytes
       equal the original (decompressed) ``body``.

    On the unfixed code, step 3 FAILS — the proxy forwards the upstream
    ``Content-Encoding: gzip`` verbatim. That failure is the bug
    confirmation we want for this exploration test.
    """

    # Run the async coroutine in a private event loop and tear it down
    # cleanly. We deliberately don't use ``asyncio.run`` because it
    # blanks the thread's "current loop" via ``set_event_loop(None)``
    # on close — Python 3.11+ no longer auto-creates a fresh one for the
    # next ``asyncio.get_event_loop()`` caller, so a downstream pytest
    # fixture (e.g. the bridge e2e fixture in
    # ``plugin/tests/integration/test_market_bridge_e2e.py`` which still
    # uses the deprecated ``get_event_loop()`` API) would error with
    # "There is no current event loop in thread 'MainThread'." after
    # this Hypothesis test ran.
    prev_loop: asyncio.AbstractEventLoop | None
    try:
        prev_loop = asyncio.get_event_loop_policy().get_event_loop()
    except RuntimeError:
        prev_loop = None
    loop = asyncio.new_event_loop()
    try:
        asyncio.set_event_loop(loop)
        loop.run_until_complete(_run_one(body))
    finally:
        loop.close()
        asyncio.set_event_loop(prev_loop)


async def _run_one(body: bytes) -> None:
    # Importing the app has heavyweight side effects (logging setup, SSL
    # precheck, ...). Defer the import so module collection is cheap and
    # so the import only happens when the test actually runs.
    from app import main_server
    from app.main_server import app

    gzipped_body = gzip.compress(body)

    def _handler(request: httpx.Request) -> httpx.Response:
        # Upstream advertises gzip and ships gzip bytes — httpx will
        # transparently decompress when the proxy reads
        # ``upstream.content``. The headers stay untouched so the proxy
        # then forwards a stale ``Content-Encoding: gzip`` alongside an
        # already-decompressed body.
        return httpx.Response(
            200,
            content=gzipped_body,
            headers={
                "Content-Encoding": "gzip",
                "Content-Type": "application/json",
                "Cache-Control": "no-store",
            },
        )

    # Capture the unpatched class so the *test* ASGI client can talk to
    # the FastAPI app without going through MockTransport. The patch we
    # install below would otherwise also override our own client's
    # transport.
    original_async_client = httpx.AsyncClient

    class _MockUpstreamAsyncClient(original_async_client):
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            kwargs["transport"] = httpx.MockTransport(_handler)
            super().__init__(*args, **kwargs)

    # ``main_server.httpx`` IS the httpx module, so this patch is
    # process-global for the duration of the request. Restore in a
    # finally clause to avoid leaking into adjacent hypothesis examples.
    saved_async_client = main_server.httpx.AsyncClient
    main_server.httpx.AsyncClient = _MockUpstreamAsyncClient
    try:
        async with original_async_client(
            transport=httpx.ASGITransport(app=app),
            base_url="http://testserver",
        ) as client:
            # Stream the response so we can inspect headers AND read
            # raw bytes via ``aiter_raw`` without httpx auto-decoding
            # the body. If we let httpx auto-decode, the unfixed proxy
            # would crash this test client with the same
            # ``DecodingError`` (httpx-level equivalent of the
            # browser's ``ERR_CONTENT_DECODING_FAILED``) before we can
            # surface a clear assertion message — which would still
            # prove the bug, just less legibly.
            request = client.build_request("GET", "/market/exploration-probe")
            response = await client.send(request, stream=True)
            try:
                status_code = response.status_code
                response_headers = dict(response.headers)
                raw_chunks: list[bytes] = []
                async for chunk in response.aiter_raw():
                    raw_chunks.append(chunk)
                raw_body = b"".join(raw_chunks)
            finally:
                await response.aclose()
    finally:
        main_server.httpx.AsyncClient = saved_async_client

    # Sanity: the proxy ran end-to-end and forwarded the decompressed
    # body verbatim. Without this, an empty 502 response would let the
    # bug-asserting line below "pass" for the wrong reason.
    assert status_code == 200, (
        f"proxy returned non-200: status={status_code!r} body={raw_body!r}"
    )
    assert raw_body == body, (
        "proxy did not forward the decompressed body unchanged: "
        f"expected={body!r} got={raw_body!r}"
    )

    # ── The bug assertion ─────────────────────────────────────────────
    # Post-fix, the proxy must strip ``Content-Encoding`` so the body
    # (already decompressed by httpx) and the headers stay consistent.
    # On the unfixed code, the upstream ``Content-Encoding: gzip``
    # header survives the response-side hop-by-hop filter and lands in
    # the client response — that is the bug we want to surface.
    header_names_lower = {key.lower() for key in response_headers.keys()}
    assert "content-encoding" not in header_names_lower, (
        "BUG 1.1 reproduced: proxy forwarded Content-Encoding="
        f"{response_headers.get('content-encoding')!r} alongside an "
        f"already-decompressed body (body={body!r}). The browser would "
        "see this as double encoding and raise ERR_CONTENT_DECODING_FAILED."
    )


@pytest.mark.asyncio
async def test_proxy_uses_runtime_user_plugin_server_port(monkeypatch: pytest.MonkeyPatch) -> None:
    from app import main_server
    from app.main_server import app

    seen_urls: list[str] = []
    original_async_client = httpx.AsyncClient

    def _handler(request: httpx.Request) -> httpx.Response:
        seen_urls.append(str(request.url))
        return httpx.Response(200, json={"ok": True})

    class _MockUpstreamAsyncClient(original_async_client):
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            kwargs["transport"] = httpx.MockTransport(_handler)
            super().__init__(*args, **kwargs)

    saved_async_client = main_server.httpx.AsyncClient
    monkeypatch.setenv("NEKO_USER_PLUGIN_SERVER_PORT", "49123")
    main_server.httpx.AsyncClient = _MockUpstreamAsyncClient
    try:
        async with original_async_client(
            transport=httpx.ASGITransport(app=app),
            base_url="http://testserver",
        ) as client:
            response = await client.get("/market/status?x=1")
    finally:
        main_server.httpx.AsyncClient = saved_async_client

    assert response.status_code == 200
    assert seen_urls == ["http://127.0.0.1:49123/market/status?x=1"]


# ---------------------------------------------------------------------------
# Documented counterexample (also runnable as a standalone unit test so the
# specific failing input from bugfix.md §1.1 is checked into the regression
# suite, not just discovered via Hypothesis).
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_proxy_double_encoding_documented_counterexample() -> None:
    """Anchor counterexample: ``b'{"ok":true}'`` exposes the double
    Content-Encoding header verbatim. This is the same observation that
    motivated bugfix.md §1.1; on the unfixed proxy this test FAILS for
    the same reason as the property test above.
    """

    await _run_one(b'{"ok":true}')
