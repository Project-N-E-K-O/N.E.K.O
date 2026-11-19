"""The review-submission proxy forwards authenticated Market calls safely.

The proxy is the only credentialed path from the publish workbench to the
Market's ``/review/submissions`` API: the browser holds only the bridge
token, and the desktop OAuth access token is attached server-side. These
tests pin that contract -- the bridge-token check, the forwarded
method/path/query/body, the server-only Bearer header, the pre-forward auth
gate, the single 401 refresh-retry, the email-gate 403 mapping, and that a
business 4xx body the Market authored is passed through unchanged.
"""

from __future__ import annotations

import json

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
_BRIDGE = {"token": market_bridge.get_bridge_token()}
_FEEDBACK_HEADER = "X-Market-Feedback-Session"
_BRIDGE_HEADERS = {"Authorization": f"Bearer {_BRIDGE['token']}"}
_EMPTY_PAGE = {"items": [], "total": 0, "page": 1, "page_size": 20, "total_pages": 0}


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
        if force_refresh:
            ensured.append(rejected_access_token)
        if force_refresh and rejected_access_token is not None and token:
            return {**token, "access_token": "refreshed-access-token"}
        return token

    monkeypatch.setattr(market_bridge, "ensure_httpx", fake_httpx)
    monkeypatch.setattr(market_bridge, "_ensure_valid_oauth_token", fake_ensure)
    # The local-origin guard is exercised separately; here it is a no-op so the
    # token check, OAuth and forwarding behaviour can be tested in isolation.
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


def _install_feedback(monkeypatch, responder):
    token = {**_VERIFIED_TOKEN, "session_id": "feedback-login-a"}
    client, upstream, _ = _install(monkeypatch, responder, token=token)
    monkeypatch.setattr(market_bridge, "_read_json_file", lambda path: token)
    monkeypatch.setattr(market_bridge, "MARKET_WEB_URL", "https://web.market.example.com")
    return client, upstream, token


def test_feedback_scopes_query_and_marks_the_message_seen_by_the_client(monkeypatch):
    def responder(request, attempt):
        if request.method == "POST":
            return httpx.Response(204)
        return httpx.Response(200, json={"items": [], "by_plugin": []})

    client, upstream, _ = _install_feedback(monkeypatch, responder)
    denied = client.get("/market/feedback", params=_BRIDGE)
    assert denied.status_code == 403
    response = client.get(
        "/market/feedback",
        params={"kind": "issue", "status": "unread", "cursor": "next", "extra": "drop"},
        headers=_BRIDGE_HEADERS,
    )
    assert response.status_code == 200 and response.headers["Cache-Control"] == "no-store"
    assert response.headers["X-Market-Web-Url"] == "https://web.market.example.com"
    assert upstream.requests[0].url.path == "/api/v1/me/plugin-feedback"
    assert dict(upstream.requests[0].url.params) == {
        "kind": "issue", "status": "unread", "cursor": "next",
    }
    assert response.headers[_FEEDBACK_HEADER] != "feedback-login-a"
    payload = {"thread_kind": "issue", "thread_id": 17, "through_message_id": 31}
    marked = client.post(
        "/market/feedback/mark-read",
        headers={**_BRIDGE_HEADERS, _FEEDBACK_HEADER: response.headers[_FEEDBACK_HEADER]},
        json=payload,
    )
    assert marked.status_code == 204
    assert upstream.requests[1].url.path == "/api/v1/me/plugin-feedback/mark-read"
    assert upstream.requests[1].headers["Authorization"] == f"Bearer {_ACCESS_TOKEN}"
    assert _FEEDBACK_HEADER not in upstream.requests[1].headers
    assert json.loads(upstream.requests[1].content) == payload


def test_feedback_bulk_read_rejects_a_missing_or_replaced_session(monkeypatch):
    client, upstream, token = _install_feedback(monkeypatch, _ok({"items": []}))
    initial = client.get("/market/feedback", headers=_BRIDGE_HEADERS)
    assert initial.status_code == 200
    old_session = initial.headers[_FEEDBACK_HEADER]
    token.update(session_id="feedback-login-b", subject="another-account")
    for headers in (_BRIDGE_HEADERS, {**_BRIDGE_HEADERS, _FEEDBACK_HEADER: old_session}):
        marked = client.post("/market/feedback/mark-read", headers=headers, json={"all": True})
        assert marked.status_code == 409
        assert marked.json()["detail"]["code"] == "market_feedback_session_changed"
    assert len(upstream.requests) == 1


@pytest.mark.parametrize("switch_account", [False, True])
def test_feedback_refresh_retries_only_inside_the_original_session(monkeypatch, switch_account):
    def responder(request, attempt):
        if request.method == "POST":
            return httpx.Response(401 if attempt == 1 else 204)
        return httpx.Response(200, json={"items": []})

    client, upstream, token = _install_feedback(monkeypatch, responder)
    initial = client.get("/market/feedback", headers=_BRIDGE_HEADERS)
    session = initial.headers[_FEEDBACK_HEADER]

    async def refresh(*, force_refresh=False, rejected_access_token=None):
        if force_refresh:
            assert rejected_access_token == _ACCESS_TOKEN
            token["access_token"] = "new-access-token"
            if switch_account:
                token.update(session_id="feedback-login-b", subject="another-account")
        return dict(token)

    monkeypatch.setattr(market_bridge, "_ensure_valid_oauth_token", refresh)
    marked = client.post(
        "/market/feedback/mark-read",
        headers={**_BRIDGE_HEADERS, _FEEDBACK_HEADER: session},
        json={"all": True},
    )
    if switch_account:
        assert marked.status_code == 409 and len(upstream.requests) == 2
    else:
        assert marked.status_code == 204 and len(upstream.requests) == 3
        assert upstream.requests[2].headers["Authorization"] == "Bearer new-access-token"
        assert marked.headers[_FEEDBACK_HEADER] == session


def _switch_account_during(status, payload=None):
    """Let the first read succeed, then answer ``status`` while switching the login mid-flight."""

    def responder(request, attempt):
        body = payload if payload is not None else {}
        if attempt == 0:
            return httpx.Response(200, json=body)
        responder.token.update(session_id="feedback-login-b", subject="another-account")
        return httpx.Response(status, json=body)

    return responder


def test_a_write_the_market_applied_is_not_reported_failed_after_an_account_switch(monkeypatch):
    message = {"id": 31, "body": "Reply text"}
    responder = _switch_account_during(200, message)
    client, upstream, token = _install_feedback(monkeypatch, responder)
    responder.token = token
    base = "/market/conversations/plugins/8"
    login = client.get(f"{base}/issues/10", headers=_BRIDGE_HEADERS)
    assert login.status_code == 200
    headers = {**_BRIDGE_HEADERS, _FEEDBACK_HEADER: login.headers[_FEEDBACK_HEADER]}
    draft = {"body": "Reply text", "reply_to_id": None, "client_message_id": "test-message-id"}
    sent = client.post(f"{base}/issues/10/messages", headers=headers, json=draft)
    assert sent.status_code == 200 and sent.json() == message
    assert sent.headers["X-Market-Feedback-Session-Changed"] == "1"
    # The old session is not handed back as if it were still current.
    assert _FEEDBACK_HEADER not in sent.headers
    assert len(upstream.requests) == 2
    # The pre-flight check is unchanged: the stale session cannot write again.
    again = client.post(f"{base}/issues/10/messages", headers=headers, json=draft)
    assert again.status_code == 409 and len(upstream.requests) == 2


@pytest.mark.parametrize(("method", "status"), [("GET", 200), ("POST", 422)])
def test_a_read_or_refused_write_is_still_rejected_after_an_account_switch(monkeypatch, method, status):
    responder = _switch_account_during(status, {"items": []})
    client, upstream, token = _install_feedback(monkeypatch, responder)
    responder.token = token
    initial = client.get("/market/feedback", headers=_BRIDGE_HEADERS)
    assert initial.status_code == 200
    headers = {**_BRIDGE_HEADERS, _FEEDBACK_HEADER: initial.headers[_FEEDBACK_HEADER]}
    if method == "GET":
        response = client.get("/market/feedback", headers=headers)
    else:
        response = client.post("/market/feedback/mark-read", headers=headers, json={"all": True})
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "market_feedback_session_changed"
    assert len(upstream.requests) == 2


def test_a_write_5xx_after_an_account_switch_stays_uncertain_not_refused(monkeypatch):
    # The Market may have applied a write that answered 5xx. Reporting it as the
    # 409 refusal would tell the UI it is safe to post again under the new account.
    responder = _switch_account_during(503, {"detail": "upstream unavailable"})
    client, upstream, token = _install_feedback(monkeypatch, responder)
    responder.token = token
    base = "/market/conversations/plugins/8"
    login = client.get(f"{base}/issues/10", headers=_BRIDGE_HEADERS)
    assert login.status_code == 200
    headers = {**_BRIDGE_HEADERS, _FEEDBACK_HEADER: login.headers[_FEEDBACK_HEADER]}
    draft = {"body": "Reply text", "reply_to_id": None, "client_message_id": "test-message-id"}
    response = client.post(f"{base}/issues/10/messages", headers=headers, json=draft)
    assert response.status_code == 503
    assert response.json() == {"detail": "upstream unavailable"}
    assert _FEEDBACK_HEADER not in response.headers
    assert "X-Market-Feedback-Session-Changed" not in response.headers
    assert len(upstream.requests) == 2


def test_feedback_session_survives_an_access_token_refresh_without_a_session_id():
    legacy = {"access_token": "access-1", "subject": "ory-subject-abc", "subject_pending": False}
    refreshed = {**legacy, "access_token": "access-2"}
    switched = {**refreshed, "subject": "another-account"}
    session = market_bridge._feedback_session(legacy)
    assert session and session == market_bridge._feedback_session(refreshed)
    assert session != market_bridge._feedback_session(switched)


def test_feedback_avatars_refuse_plain_http_urls_and_oversized_responses(monkeypatch):
    user = "123e4567-e89b-42d3-a456-426614174000"
    profiles = {user: {"avatar": {"kind": "image", "url": "http://cdn.community.example.com/a.png"}}}
    client, _, _ = _install_feedback(monkeypatch, _ok({"profiles": profiles}))
    monkeypatch.setattr(market_bridge, "social_base_url", lambda: "https://community.example.com")
    query = {"headers": _BRIDGE_HEADERS, "json": {"user_ids": [user]}}
    result = client.post("/market/feedback/avatars", **query)
    assert result.status_code == 200 and result.json() == {}
    monkeypatch.setattr(market_bridge, "_AVATAR_QUERY_MAX_BYTES", 16)
    oversized = client.post("/market/feedback/avatars", **query)
    assert oversized.status_code == 502 and oversized.json() == {}


def test_feedback_avatars_only_returns_requested_public_avatars_without_credentials(monkeypatch):
    requested = "123e4567-e89b-42d3-a456-426614174000"
    missing = "123e4567-e89b-42d3-a456-426614174001"
    hue = "123e4567-e89b-42d3-a456-426614174002"
    unsupported = "123e4567-e89b-42d3-a456-426614174003"
    avatar = "https://cdn.community.example.com/avatar.png"
    client, upstream, _ = _install_feedback(monkeypatch, _ok({
        "profiles": {
            requested: {"avatar": {"kind": "image", "url": avatar}, "display_name": "Author"},
            hue: {"avatar": {"kind": "hue", "hue": 27, "url": None}},
            unsupported: {"avatar": {"kind": "image", "url": "file:///local/avatar.png"}},
            "unrequested": {"avatar": {"kind": "image", "url": avatar}},
        },
        "missing": [missing],
    }))
    monkeypatch.setattr(market_bridge, "social_base_url", lambda: "https://community.example.com")
    result = client.post(
        "/market/feedback/avatars",
        headers=_BRIDGE_HEADERS,
        json={"user_ids": [requested, missing, hue, unsupported, requested]},
    )
    assert result.status_code == 200 and result.json() == {requested: avatar}
    request = upstream.requests[0]
    assert str(request.url) == "https://community.example.com/api/v1/public-profiles/query"
    assert json.loads(request.content) == {"user_ids": [requested, missing, hue, unsupported]}
    assert "authorization" not in request.headers and "cookie" not in request.headers


@pytest.mark.parametrize(
    ("route", "query", "allowed", "page"),
    [
        ("comments", "after_id=4", {"after_id": "4"}, {"messages": [], "next_cursor": None}),
        (
            "issues",
            "before_id=4&state=open",
            {"before_id": "4", "state": "open"},
            {"items": [], "next_cursor": None},
        ),
    ],
)
def test_market_conversation_reads_are_public_and_only_forward_allowed_queries(
    monkeypatch, route, query, allowed, page,
):
    client, upstream, _ = _install(monkeypatch, _ok(page), token=None)
    for headers in ({}, _BRIDGE_HEADERS):
        response = client.get(
            f"/market/conversations/plugins/8/{route}?{query}&extra=drop",
            headers=headers,
        )
        assert response.status_code == 200 and _FEEDBACK_HEADER not in response.headers
        assert response.json() == page
        forwarded = upstream.requests[-1]
        assert forwarded.url.path == f"/api/v1/plugins/8/{route}"
        assert dict(forwarded.url.params) == allowed
        assert "Authorization" not in forwarded.headers


def test_market_conversation_writes_use_the_original_login_and_reject_unlisted_actions(monkeypatch):
    client, upstream, token = _install_feedback(monkeypatch, _ok({"id": 10, "state": "closed"}))
    base = "/market/conversations/plugins/8"
    login = client.get(f"{base}/issues/10", headers=_BRIDGE_HEADERS)
    headers = {**_BRIDGE_HEADERS, _FEEDBACK_HEADER: login.headers[_FEEDBACK_HEADER]}
    draft = {"body": "Reply text", "reply_to_id": 12, "client_message_id": "test-message-id"}
    assert client.post(f"{base}/comments/messages", headers=headers, json=draft).status_code == 200
    assert client.post(f"{base}/issues/10/messages", headers=headers, json=draft).status_code == 200
    assert client.post(f"{base}/issues/10/close", headers=headers).status_code == 200
    assert client.post(f"{base}/issues/10/hide", headers=headers).status_code == 422
    assert client.post(f"{base}/issues/10/reopen", headers=_BRIDGE_HEADERS).status_code == 409
    assert [request.url.path for request in upstream.requests] == [
        "/api/v1/plugins/8/issues/10",
        "/api/v1/plugins/8/comments/messages",
        "/api/v1/plugins/8/issues/10/messages",
        "/api/v1/plugins/8/issues/10/close",
    ]
    assert json.loads(upstream.requests[1].content) == draft
    assert upstream.requests[1].headers["Authorization"] == f"Bearer {_ACCESS_TOKEN}"
    assert _FEEDBACK_HEADER not in upstream.requests[1].headers
    assert not upstream.requests[3].content
    token.update(session_id="new-feedback-session", subject="another-account")
    rejected = client.post(f"{base}/comments/messages", headers=headers, json=draft)
    assert rejected.status_code == 409 and len(upstream.requests) == 4


def _forbidden_with_user(user):
    """Reply 403 to every submission call and ``user`` to ``/auth/me``."""

    def responder(request, attempt):
        if request.url.path == "/api/v1/auth/me":
            return httpx.Response(200, json=user)
        return httpx.Response(403, json={"detail": "请先验证邮箱"})

    return responder


def test_mine_returns_the_market_page_unchanged(monkeypatch):
    page = {
        "items": [{"id": 7, "status": "draft", "current_snapshot": {"repo_name": "r"}}],
        "total": 1,
        "page": 1,
        "page_size": 20,
        "total_pages": 1,
    }
    client, upstream, _ = _install(monkeypatch, _ok(page))
    response = client.get("/market/submissions/mine", params=_BRIDGE)
    assert response.status_code == 200
    assert response.json() == page
    forwarded = upstream.requests[0]
    assert forwarded.method == "GET"
    assert forwarded.url.path == "/api/v1/review/submissions/mine"


def test_mine_forwards_only_the_market_query_params(monkeypatch):
    client, upstream, _ = _install(monkeypatch, _ok(_EMPTY_PAGE))
    response = client.get(
        "/market/submissions/mine",
        params={
            **_BRIDGE,
            "q": "owner/repo",
            "status": "closed",
            "decision": "approved",
            "page": "2",
            "page_size": "100",
            "extra": "1",
        },
    )
    assert response.status_code == 200
    forwarded = dict(upstream.requests[0].url.params)
    assert forwarded == {
        "q": "owner/repo",
        "status": "closed",
        "decision": "approved",
        "page": "2",
        "page_size": "100",
    }
    assert _BRIDGE["token"] not in str(upstream.requests[0].url)


@pytest.mark.parametrize("bridge", [{}, {"token": "wrong"}])
def test_a_missing_or_wrong_bridge_token_is_refused_without_forwarding(monkeypatch, bridge):
    client, upstream, _ = _install(monkeypatch, _ok(_EMPTY_PAGE))
    response = client.get("/market/submissions/mine", params=bridge)
    assert response.status_code == 403
    assert upstream.requests == []


def test_the_bridge_token_is_accepted_as_a_bearer_header(monkeypatch):
    client, upstream, _ = _install(monkeypatch, _ok(_EMPTY_PAGE))
    response = client.get(
        "/market/submissions/mine",
        headers={"Authorization": f"Bearer {_BRIDGE['token']}"},
    )
    assert response.status_code == 200
    # The Market gets the desktop access token, never the bridge token.
    assert upstream.requests[0].headers["authorization"] == f"Bearer {_ACCESS_TOKEN}"


def test_access_token_is_attached_only_server_side(monkeypatch):
    client, upstream, _ = _install(monkeypatch, _ok({"ok": True}))
    client.post(
        "/market/submissions/drafts",
        params=_BRIDGE,
        json={"repo_url": "https://x", "plugin_name": "n"},
    )
    forwarded = upstream.requests[0]
    assert forwarded.headers["authorization"] == f"Bearer {_ACCESS_TOKEN}"
    assert forwarded.url.query == b""


def test_draft_body_is_forwarded_verbatim(monkeypatch):
    client, upstream, _ = _install(monkeypatch, _ok({"id": 1}))
    body = {"repo_url": "https://github.com/o/r", "plugin_name": "demo", "tags": ["a", "b"]}
    response = client.post("/market/submissions/drafts", params=_BRIDGE, json=body)
    assert response.status_code == 200
    forwarded = upstream.requests[0]
    assert forwarded.method == "POST"
    assert forwarded.url.path == "/api/v1/review/submissions/drafts"
    assert json.loads(forwarded.content) == body


def test_draft_update_is_forwarded_as_a_put_with_its_body(monkeypatch):
    client, upstream, _ = _install(monkeypatch, _ok({"id": 7}))
    body = {"submitted_ref": None, "resolved_commit": "a" * 40}
    response = client.put("/market/submissions/7/draft", params=_BRIDGE, json=body)
    assert response.status_code == 200
    forwarded = upstream.requests[0]
    assert forwarded.method == "PUT"
    assert forwarded.url.path == "/api/v1/review/submissions/7/draft"
    assert forwarded.url.query == b""
    assert json.loads(forwarded.content) == body


@pytest.mark.parametrize("body", [{}, {"note": "replacing it"}])
def test_withdraw_is_forwarded_as_a_post_with_its_body(monkeypatch, body):
    client, upstream, _ = _install(monkeypatch, _ok({"id": 7, "status": "closed"}))
    response = client.post("/market/submissions/7/withdraw", params=_BRIDGE, json=body)
    assert response.status_code == 200
    forwarded = upstream.requests[0]
    assert forwarded.method == "POST"
    assert forwarded.url.path == "/api/v1/review/submissions/7/withdraw"
    assert json.loads(forwarded.content) == body


def test_missing_login_is_refused_locally_without_forwarding(monkeypatch):
    client, upstream, _ = _install(monkeypatch, _ok({}), token=None)
    response = client.get("/market/submissions/mine", params=_BRIDGE)
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "market_auth_required"
    assert upstream.requests == []


def test_a_pending_subject_is_refused_locally_without_forwarding(monkeypatch):
    pending = {"access_token": _ACCESS_TOKEN, "subject": "s", "subject_pending": True}
    client, upstream, _ = _install(monkeypatch, _ok({}), token=pending)
    response = client.post("/market/submissions/repository-preview", params=_BRIDGE, json={})
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "market_auth_pending"
    assert upstream.requests == []


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("POST", "/market/submissions/repository-preview"),
        ("POST", "/market/submissions/drafts"),
        ("PUT", "/market/submissions/7/draft"),
        ("POST", "/market/submissions/7/submit"),
    ],
)
def test_an_email_gate_403_becomes_market_email_unverified(monkeypatch, method, path):
    client, upstream, _ = _install(
        monkeypatch, _forbidden_with_user({"id": 3, "email_verified": False})
    )
    response = client.request(method, path, params=_BRIDGE, json={})
    assert response.status_code == 409
    assert response.json()["code"] == "market_email_unverified"
    assert upstream.requests[-1].url.path == "/api/v1/auth/me"
    assert upstream.requests[-1].headers["authorization"] == f"Bearer {_ACCESS_TOKEN}"


def test_a_403_for_a_verified_user_passes_through(monkeypatch):
    client, _, _ = _install(monkeypatch, _forbidden_with_user({"id": 3, "email_verified": True}))
    response = client.post("/market/submissions/7/submit", params=_BRIDGE, json={})
    assert response.status_code == 403
    assert response.json() == {"detail": "请先验证邮箱"}


def test_a_403_is_passed_through_when_the_market_user_is_unreadable(monkeypatch):
    def responder(request, attempt):
        return httpx.Response(403, json={"detail": "user disabled"})

    client, _, _ = _install(monkeypatch, responder)
    response = client.post("/market/submissions/drafts", params=_BRIDGE, json={})
    assert response.status_code == 403
    assert response.json() == {"detail": "user disabled"}


@pytest.mark.parametrize(
    ("method", "path"),
    [("GET", "/market/submissions/mine"), ("POST", "/market/submissions/7/withdraw")],
)
def test_a_403_on_a_login_only_route_is_never_remapped(monkeypatch, method, path):
    client, upstream, _ = _install(
        monkeypatch, _forbidden_with_user({"id": 3, "email_verified": False})
    )
    response = client.request(method, path, params=_BRIDGE, json={} if method == "POST" else None)
    assert response.status_code == 403
    assert response.json() == {"detail": "请先验证邮箱"}
    assert all(request.url.path != "/api/v1/auth/me" for request in upstream.requests)


def test_a_401_triggers_one_refresh_and_retry(monkeypatch):
    def responder(request, attempt):
        if attempt == 0:
            return httpx.Response(401, json={"detail": "token expired"})
        return httpx.Response(200, json=_EMPTY_PAGE)

    client, upstream, ensured = _install(monkeypatch, responder)
    response = client.get("/market/submissions/mine", params=_BRIDGE)
    assert response.status_code == 200
    assert len(upstream.requests) == 2
    # The retry asks the token store to refresh past the rejected credential.
    assert ensured == [_ACCESS_TOKEN]
    assert upstream.requests[0].headers["authorization"] == f"Bearer {_ACCESS_TOKEN}"
    assert upstream.requests[1].headers["authorization"] == "Bearer refreshed-access-token"


def test_a_persistent_401_ends_as_a_local_auth_rejected(monkeypatch):
    def responder(request, attempt):
        return httpx.Response(401, json={"detail": "still bad"})

    client, upstream, _ = _install(monkeypatch, responder)
    response = client.get("/market/submissions/mine", params=_BRIDGE)
    assert response.status_code == 401
    assert response.json()["code"] == "market_auth_rejected"
    assert len(upstream.requests) == 2


def test_an_upstream_4xx_body_is_passed_through_unchanged(monkeypatch):
    def responder(request, attempt):
        return httpx.Response(422, json={"secret_upstream_field": "leak"})

    client, _, _ = _install(monkeypatch, responder)
    response = client.post(
        "/market/submissions/drafts", params=_BRIDGE, json={"repo_url": "x", "plugin_name": "n"}
    )
    # The upstream status and body reach the client unchanged: the UI maps the
    # Market's own code and detail, and the proxy adds nothing.
    assert response.status_code == 422
    assert response.json() == {"secret_upstream_field": "leak"}


def test_a_redirect_is_rejected_rather_than_followed(monkeypatch):
    def responder(request, attempt):
        return httpx.Response(302, headers={"location": "https://evil.example"})

    client, _, _ = _install(monkeypatch, responder)
    response = client.get("/market/submissions/mine", params=_BRIDGE)
    assert response.status_code == 502
    assert response.json()["code"] == "market_submission_redirect_rejected"


def test_a_network_failure_is_a_local_502(monkeypatch):
    def responder(request, attempt):
        raise httpx.ConnectError("boom")

    client, _, _ = _install(monkeypatch, responder)
    response = client.post("/market/submissions/repository-preview", params=_BRIDGE, json={})
    assert response.status_code == 502
    assert response.json()["code"] == "market_submission_unavailable"


def test_an_unconfigured_market_is_a_local_503(monkeypatch):
    client, upstream, _ = _install(monkeypatch, _ok({}))
    monkeypatch.setattr(market_bridge, "MARKET_API_URL", "")
    response = client.get("/market/submissions/mine", params=_BRIDGE)
    assert response.status_code == 503
    assert response.json()["code"] == "market_submission_not_configured"
    assert upstream.requests == []


@pytest.mark.parametrize("action", ["submit", "withdraw"])
@pytest.mark.parametrize("bad_id", ["../mine", "1;2", "abc$", "../../secret", "a" * 40])
def test_a_malformed_submission_id_is_refused_before_forwarding(monkeypatch, bad_id, action):
    client, upstream, _ = _install(monkeypatch, _ok({}))
    response = client.post(f"/market/submissions/{bad_id}/{action}", params=_BRIDGE, json={})
    assert response.status_code in (400, 404)
    # Nothing reaches the Market with an id that could reshape the path.
    assert all("secret" not in request.url.path for request in upstream.requests)


def test_a_malformed_draft_id_is_refused_with_its_code(monkeypatch):
    client, upstream, _ = _install(monkeypatch, _ok({}))
    response = client.put("/market/submissions/abc$/draft", params=_BRIDGE, json={})
    assert response.status_code == 400
    assert response.json()["detail"]["code"] == "market_submission_id_invalid"
    assert upstream.requests == []


@pytest.mark.parametrize("good_id", ["152", "9" * 19, "123e4567-e89b-12d3-a456-426614174000"])
def test_a_numeric_or_uuid_submission_id_is_accepted(monkeypatch, good_id):
    client, upstream, _ = _install(monkeypatch, _ok({"id": good_id}))
    response = client.post(f"/market/submissions/{good_id}/submit", params=_BRIDGE, json={})
    assert response.status_code == 200
    assert upstream.requests[0].url.path == f"/api/v1/review/submissions/{good_id}/submit"



def test_preflight_forwards_its_untyped_body_verbatim(monkeypatch):
    # Shaped like the Market's preflight answer (publication_preflight.py:168-173).
    result = {"ready": False, "issues": [], "preview": None, "normalized_icon_url": None}
    client, upstream, _ = _install(monkeypatch, _ok(result))
    body = {"repo_url": "https://github.com/o/r", "plugin_slug": "demo", "unknown": [1, {"x": 2}]}
    response = client.post("/market/submissions/preflight", params=_BRIDGE, json=body)
    assert response.status_code == 200
    assert response.json() == result
    forwarded = upstream.requests[0]
    assert forwarded.method == "POST"
    assert forwarded.url.path == "/api/v1/review/submissions/preflight"
    assert forwarded.url.query == b""
    assert json.loads(forwarded.content) == body


def test_revision_is_forwarded_as_a_post_with_its_body(monkeypatch):
    client, upstream, _ = _install(monkeypatch, _ok({"id": 7, "status": "submitted"}))
    body = {"base_snapshot_id": 31, "note": "new commit", "submitted_ref": "b" * 40, "resolved_commit": "b" * 40}
    response = client.post("/market/submissions/7/revision", params=_BRIDGE, json=body)
    assert response.status_code == 200
    forwarded = upstream.requests[0]
    assert forwarded.method == "POST"
    assert forwarded.url.path == "/api/v1/review/submissions/7/revision"
    assert forwarded.url.query == b""
    assert json.loads(forwarded.content) == body


def test_workspace_is_a_get_that_forwards_no_query(monkeypatch):
    client, upstream, _ = _install(monkeypatch, _ok({"submission": {"id": 7}}))
    response = client.get(
        "/market/submissions/7/workspace", params={**_BRIDGE, "activity_cursor": "c1"}
    )
    assert response.status_code == 200
    forwarded = upstream.requests[0]
    assert forwarded.method == "GET"
    assert forwarded.url.path == "/api/v1/review/submissions/7/workspace"
    assert forwarded.url.query == b""
    assert forwarded.content == b""


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("POST", "/market/submissions/preflight"),
        ("POST", "/market/submissions/7/revision"),
        ("GET", "/market/submissions/7/workspace"),
    ],
)
def test_new_routes_never_forward_the_bridge_token(monkeypatch, method, path):
    client, upstream, _ = _install(monkeypatch, _ok({}))
    response = client.request(
        method,
        path,
        params=_BRIDGE,
        headers={"Authorization": f"Bearer {_BRIDGE['token']}"},
        json={} if method == "POST" else None,
    )
    assert response.status_code == 200
    forwarded = upstream.requests[0]
    assert _BRIDGE["token"] not in str(forwarded.url)
    assert _BRIDGE["token"] not in forwarded.headers["authorization"]
    assert forwarded.headers["authorization"] == f"Bearer {_ACCESS_TOKEN}"


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("POST", "/market/submissions/preflight"),
        ("POST", "/market/submissions/7/revision"),
        ("GET", "/market/submissions/7/workspace"),
    ],
)
def test_new_routes_refuse_a_wrong_bridge_token(monkeypatch, method, path):
    client, upstream, _ = _install(monkeypatch, _ok({}))
    response = client.request(
        method, path, params={"token": "wrong"}, json={} if method == "POST" else None
    )
    assert response.status_code == 403
    assert upstream.requests == []


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("POST", "/market/submissions/preflight"),
        ("POST", "/market/submissions/7/revision"),
        ("GET", "/market/submissions/7/workspace"),
    ],
)
def test_new_routes_refresh_once_on_a_401(monkeypatch, method, path):
    def responder(request, attempt):
        if attempt == 0:
            return httpx.Response(401, json={"detail": "token expired"})
        return httpx.Response(200, json={"ok": True})

    client, upstream, ensured = _install(monkeypatch, responder)
    response = client.request(method, path, params=_BRIDGE, json={} if method == "POST" else None)
    assert response.status_code == 200
    assert len(upstream.requests) == 2
    assert ensured == [_ACCESS_TOKEN]
    assert upstream.requests[0].headers["authorization"] == f"Bearer {_ACCESS_TOKEN}"
    assert upstream.requests[1].headers["authorization"] == "Bearer refreshed-access-token"


@pytest.mark.parametrize(
    ("method", "path"),
    [("POST", "/market/submissions/preflight"), ("POST", "/market/submissions/7/revision")],
)
def test_new_verified_email_routes_map_the_email_gate(monkeypatch, method, path):
    client, _, _ = _install(monkeypatch, _forbidden_with_user({"id": 3, "email_verified": False}))
    response = client.request(method, path, params=_BRIDGE, json={})
    assert response.status_code == 409
    assert response.json()["code"] == "market_email_unverified"


def test_a_workspace_403_is_never_remapped(monkeypatch):
    client, upstream, _ = _install(
        monkeypatch, _forbidden_with_user({"id": 3, "email_verified": False})
    )
    response = client.get("/market/submissions/7/workspace", params=_BRIDGE)
    assert response.status_code == 403
    assert all(request.url.path != "/api/v1/auth/me" for request in upstream.requests)


@pytest.mark.parametrize(
    ("method", "action"), [("POST", "revision"), ("GET", "workspace")]
)
def test_new_id_routes_refuse_a_malformed_id(monkeypatch, method, action):
    client, upstream, _ = _install(monkeypatch, _ok({}))
    response = client.request(
        method,
        f"/market/submissions/abc$/{action}",
        params=_BRIDGE,
        json={} if method == "POST" else None,
    )
    assert response.status_code == 400
    assert response.json()["detail"]["code"] == "market_submission_id_invalid"
    assert upstream.requests == []


@pytest.mark.parametrize("language", ["zh-CN", "zh-TW", "en-US", "ja", "ko", "ru", "es", "pt"])
@pytest.mark.parametrize("method,path", [("GET", "/mine"), ("POST", "/preflight")])
def test_submission_forwards_the_selected_language_on_initial_request_and_retry(monkeypatch, language, method, path):
    client, upstream, _ = _install(
        monkeypatch,
        lambda request, attempt: httpx.Response(401 if attempt == 0 else 200, json=_EMPTY_PAGE),
    )
    response = client.request(
        method, f"/market/submissions{path}", params=_BRIDGE,
        headers={"Accept-Language": language},
        **({"json": {"plugin_slug": "demo"}} if method == "POST" else {}),
    )
    assert response.status_code == 200
    assert len(upstream.requests) == 2
    assert all(request.headers["accept-language"] == language for request in upstream.requests)
    assert upstream.requests[1].headers["authorization"] == "Bearer refreshed-access-token"
    if method == "POST":
        assert upstream.requests[1].headers["content-type"] == "application/json"
        assert json.loads(upstream.requests[1].content) == {"plugin_slug": "demo"}


@pytest.mark.parametrize(
    "language,expected", [(b"en-US,en;q=0.8", "en-US,en;q=0.8"), (b"en-US,\xff;q=0.5", None)]
)
def test_submission_language_header_preserves_ascii_lists_and_skips_non_ascii(monkeypatch, language, expected):
    client, upstream, _ = _install(monkeypatch, _ok(_EMPTY_PAGE))
    response = client.get(
        "/market/submissions/mine", params=_BRIDGE, headers=[(b"Accept-Language", language)]
    )
    assert response.status_code == 200
    assert upstream.requests[0].headers.get("accept-language") == expected


def test_a_failed_refresh_after_401_stops_without_replaying_the_request(monkeypatch):
    client, upstream, _ = _install(
        monkeypatch, lambda request, attempt: httpx.Response(401, json={"detail": "token expired"})
    )
    refreshes = []

    async def ensure(*, force_refresh=False, rejected_access_token=None):
        if force_refresh:
            refreshes.append(rejected_access_token)
            return None
        return _VERIFIED_TOKEN

    monkeypatch.setattr(market_bridge, "_ensure_valid_oauth_token", ensure)
    response = client.get("/market/submissions/mine", params=_BRIDGE)
    assert response.status_code == 401
    assert response.json()["code"] == "market_auth_rejected"
    assert refreshes == [_ACCESS_TOKEN]
    assert len(upstream.requests) == 1
    assert upstream.requests[0].headers["authorization"] == f"Bearer {_ACCESS_TOKEN}"


def test_publication_requests_forward_the_plugin_path_and_request_id(monkeypatch):
    receipts = [{"id": 12, "status": "verifying", "release_url": "https://x"}]
    client, upstream, _ = _install(monkeypatch, _ok(receipts))
    response = client.get(
        "/market/publication-requests/42",
        params={**_BRIDGE, "request_id": "12", "extra": "1"},
    )
    assert response.status_code == 200
    assert response.json() == receipts
    forwarded = upstream.requests[0]
    assert forwarded.method == "GET"
    assert forwarded.url.path == "/api/v1/plugins/42/versions/publication-requests"
    assert dict(forwarded.url.params) == {"request_id": "12"}
    assert forwarded.headers["authorization"] == f"Bearer {_ACCESS_TOKEN}"


@pytest.mark.parametrize("plugin_id", [0, -1, 2**31])
def test_publication_requests_reject_an_invalid_plugin_id(monkeypatch, plugin_id):
    client, upstream, _ = _install(monkeypatch, _ok(_EMPTY_PAGE))
    response = client.get(f"/market/publication-requests/{plugin_id}", params=_BRIDGE)
    assert response.status_code == 422
    assert upstream.requests == []


def test_publication_requests_map_the_email_gate(monkeypatch):
    client, _, _ = _install(
        monkeypatch, _forbidden_with_user({"id": 3, "email_verified": False})
    )
    response = client.get("/market/publication-requests/42", params=_BRIDGE)
    assert response.status_code == 409
    assert response.json()["code"] == "market_email_unverified"
