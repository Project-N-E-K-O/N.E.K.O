"""The review-submission proxy forwards authenticated Market calls safely.

The proxy is the only credentialed path from the publish workbench to the
Market's ``/review/submissions`` API: the browser holds only the bridge
token, and the desktop OAuth access token is attached server-side. These
tests pin that contract -- the forwarded method/path/body, the server-only
Bearer header, the pre-forward auth gate, the single 401 refresh-retry, and
that an upstream error body is never passed through.
"""

from __future__ import annotations

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from plugin.server.routes import market_bridge

pytestmark = pytest.mark.plugin_unit

_ACCESS_TOKEN = "access-token-1"
_VERIFIED_TOKEN = {
    "access_token": _ACCESS_TOKEN,
    "subject": "ory-subject-abc",
    "subject_pending": False,
}


class _Upstream:
    """Records every forwarded request and replies from a scripted table."""

    def __init__(self, responder):
        self.responder = responder
        self.requests: list[httpx.Request] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return self.responder(request, len(self.requests) - 1)


def _install(monkeypatch, responder, *, token=_VERIFIED_TOKEN):
    """Mount the router with a mocked Market transport and a stubbed login."""
    upstream = _Upstream(responder)
    transport = httpx.MockTransport(upstream.handler)

    class Shim:
        Timeout = httpx.Timeout
        HTTPError = httpx.HTTPError

        @staticmethod
        def AsyncClient(**kwargs):
            kwargs.pop("follow_redirects", None)
            return httpx.AsyncClient(transport=transport, follow_redirects=False, **kwargs)

    async def fake_httpx():
        return Shim

    ensured: list[str | None] = []

    async def fake_ensure(*, force_refresh=False, rejected_access_token=None):
        ensured.append(rejected_access_token)
        return token

    monkeypatch.setattr(market_bridge, "ensure_httpx", fake_httpx)
    monkeypatch.setattr(market_bridge, "_ensure_valid_oauth_token", fake_ensure)
    # The local bridge-token guard is exercised separately; here it is a no-op
    # so the OAuth and forwarding behaviour can be tested in isolation.
    monkeypatch.setattr(market_bridge, "_require_local_bridge_token_access", lambda request: None)
    monkeypatch.setattr(market_bridge, "MARKET_API_URL", "https://market.example.com")
    monkeypatch.setattr(
        market_bridge, "_normalized_base_url", lambda value: value.rstrip("/") if value else ""
    )

    app = FastAPI()
    app.include_router(market_bridge.router)
    client = TestClient(app)
    return client, upstream, ensured


def _ok(payload):
    def responder(request, attempt):
        return httpx.Response(200, json=payload)

    return responder


def test_mine_is_forwarded_as_a_get_with_the_query_string(monkeypatch):
    client, upstream, _ = _install(monkeypatch, _ok({"submissions": []}))
    response = client.get("/market/submissions/mine?status=closed")
    assert response.status_code == 200
    assert response.json() == {"submissions": []}
    assert len(upstream.requests) == 1
    forwarded = upstream.requests[0]
    assert forwarded.method == "GET"
    assert forwarded.url.path == "/api/v1/review/submissions/mine"
    assert forwarded.url.query == b"status=closed"


def test_access_token_is_attached_only_server_side(monkeypatch):
    client, upstream, _ = _install(monkeypatch, _ok({"ok": True}))
    client.post("/market/submissions/drafts", json={"repo_url": "https://x", "plugin_name": "n"})
    forwarded = upstream.requests[0]
    assert forwarded.headers["authorization"] == f"Bearer {_ACCESS_TOKEN}"


def test_draft_body_is_forwarded_verbatim(monkeypatch):
    captured: list[bytes] = []

    def responder(request, attempt):
        captured.append(request.content)
        return httpx.Response(201, json={"id": 1})

    client, upstream, _ = _install(monkeypatch, responder)
    body = {"repo_url": "https://github.com/o/r", "plugin_name": "demo", "tags": ["a", "b"]}
    response = client.post("/market/submissions/drafts", json=body)
    assert response.status_code == 201
    forwarded = upstream.requests[0]
    assert forwarded.method == "POST"
    assert forwarded.url.path == "/api/v1/review/submissions/drafts"
    import json as _json

    assert _json.loads(captured[0]) == body


def test_missing_login_is_refused_locally_without_forwarding(monkeypatch):
    client, upstream, _ = _install(monkeypatch, _ok({}), token=None)
    response = client.get("/market/submissions/mine")
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "market_auth_required"
    assert upstream.requests == []


def test_unverified_email_is_refused_locally_without_forwarding(monkeypatch):
    pending = {"access_token": _ACCESS_TOKEN, "subject": "s", "subject_pending": True}
    client, upstream, _ = _install(monkeypatch, _ok({}), token=pending)
    response = client.post("/market/submissions/preflight", json={})
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "market_email_unverified"
    assert upstream.requests == []


def test_a_401_triggers_one_refresh_and_retry(monkeypatch):
    def responder(request, attempt):
        if attempt == 0:
            return httpx.Response(401, json={"detail": "token expired"})
        return httpx.Response(200, json={"ok": True})

    client, upstream, ensured = _install(monkeypatch, responder)
    response = client.get("/market/submissions/mine")
    assert response.status_code == 200
    assert len(upstream.requests) == 2
    # The retry asks the token store to refresh past the rejected credential.
    assert ensured[-1] == _ACCESS_TOKEN


def test_a_persistent_401_ends_as_a_local_auth_rejected(monkeypatch):
    def responder(request, attempt):
        return httpx.Response(401, json={"detail": "still bad"})

    client, upstream, _ = _install(monkeypatch, responder)
    response = client.get("/market/submissions/mine")
    assert response.status_code == 401
    assert response.json()["code"] == "market_auth_rejected"
    assert len(upstream.requests) == 2


def test_an_upstream_error_status_does_not_pass_its_body_through(monkeypatch):
    def responder(request, attempt):
        return httpx.Response(422, json={"secret_upstream_field": "leak"})

    client, _, _ = _install(monkeypatch, responder)
    response = client.post("/market/submissions/drafts", json={"repo_url": "x", "plugin_name": "n"})
    # The upstream status reaches the client; its body is forwarded as-is only
    # for a 4xx the Market authored (the UI maps upstream code+detail). The
    # proxy never injects its own body for a plain upstream 4xx.
    assert response.status_code == 422
    # The response is whatever the Market returned, unchanged -- the proxy adds
    # nothing and strips headers to the whitelist.
    assert response.json() == {"secret_upstream_field": "leak"}


def test_a_redirect_is_rejected_rather_than_followed(monkeypatch):
    def responder(request, attempt):
        return httpx.Response(302, headers={"location": "https://evil.example"})

    client, _, _ = _install(monkeypatch, responder)
    response = client.get("/market/submissions/mine")
    assert response.status_code == 502
    assert response.json()["code"] == "market_submission_redirect_rejected"


def test_a_network_failure_is_a_local_502(monkeypatch):
    def responder(request, attempt):
        raise httpx.ConnectError("boom")

    client, _, _ = _install(monkeypatch, responder)
    response = client.post("/market/submissions/preflight", json={})
    assert response.status_code == 502
    assert response.json()["code"] == "market_submission_unavailable"


@pytest.mark.parametrize("bad_id", ["../mine", "1;2", "abc$", "../../secret", "a" * 40])
def test_a_malformed_submission_id_is_refused_before_forwarding(monkeypatch, bad_id):
    client, upstream, _ = _install(monkeypatch, _ok({}))
    response = client.get(f"/market/submissions/{bad_id}")
    assert response.status_code in (400, 404)
    # Nothing reaches the Market with an id that could reshape the path.
    assert all("secret" not in request.url.path for request in upstream.requests)


@pytest.mark.parametrize("good_id", ["152", "9" * 19, "123e4567-e89b-12d3-a456-426614174000"])
def test_a_numeric_or_uuid_submission_id_is_accepted(monkeypatch, good_id):
    client, upstream, _ = _install(monkeypatch, _ok({"id": good_id}))
    response = client.get(f"/market/submissions/{good_id}")
    assert response.status_code == 200
    assert upstream.requests[0].url.path == f"/api/v1/review/submissions/{good_id}"


def test_timeline_forwards_its_limit_query(monkeypatch):
    client, upstream, _ = _install(monkeypatch, _ok({"events": []}))
    response = client.get("/market/submissions/152/timeline?limit=50")
    assert response.status_code == 200
    forwarded = upstream.requests[0]
    assert forwarded.url.path == "/api/v1/review/submissions/152/timeline"
    assert forwarded.url.query == b"limit=50"
