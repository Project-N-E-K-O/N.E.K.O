"""HTTP/WebSocket regressions for instance ownership and remote OAuth."""

import re

import pytest
from fastapi import FastAPI, WebSocket
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect
from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware

import main_routers.card_drop_router as C
import main_routers.community_oauth as O
from utils.instance_access import COOKIE, InstanceAccessMiddleware

KEY = "test-only-instance-key-" + "x" * 40


@pytest.fixture
def remote_app(monkeypatch, tmp_path):
    monkeypatch.setattr(C, "_facts_cloud_budget", {"tokens": 12.0, "updated": C.time.monotonic(), "active": 0})
    monkeypatch.setenv("NEKO_INSTANCE_ACCESS_KEY", KEY)
    monkeypatch.setenv("NEKO_BEHIND_PROXY", "true")
    monkeypatch.setenv("NEKO_STORAGE_SELECTED_ROOT", str(tmp_path))
    monkeypatch.setenv("NEKO_COMMUNITY_WEB_CLIENT_ID", "registered-web-client")
    monkeypatch.setenv("NEKO_COMMUNITY_WEB_REDIRECT_URI", "https://neko.example/oauth/callback")
    monkeypatch.setenv("NEKO_AUTH_URL", "https://auth.example")
    monkeypatch.setattr(O, "_oauth_pending_path", lambda: tmp_path / "pending.json")
    monkeypatch.setattr(C, "_auth_path", lambda: tmp_path / "auth.json")
    app = FastAPI()
    app.include_router(C.router)
    app.include_router(O.router)
    app.include_router(O.callback_router)

    @app.post("/private")
    async def mutation():
        return {"ok": True}

    @app.websocket("/socket")
    async def websocket(socket: WebSocket):
        await socket.accept()
        await socket.send_json({"ok": True})
        await socket.close()

    app.add_middleware(InstanceAccessMiddleware, community_handoff_authorizer=C.authorize_community_handoff)
    wrapped = ProxyHeadersMiddleware(app, trusted_hosts="127.0.0.1,::1")
    return TestClient(wrapped, base_url="https://neko.example", client=("127.0.0.1", 50000))


def pair(client):
    page = client.get("/", headers={"Accept": "text/html"})
    assert page.status_code == 401
    assert "Content-Security-Policy" in page.headers
    challenge = re.search(r'name="challenge" value="([^"]+)"', page.text).group(1)
    response = client.post("/instance-access/login", data={"key": KEY, "challenge": challenge},
                           headers={"Origin": "https://neko.example"}, follow_redirects=False)
    assert response.status_code == 303
    assert "HttpOnly" in response.headers["set-cookie"]
    assert "Secure" in response.headers["set-cookie"]
    return challenge


@pytest.mark.parametrize("route", ["/api/card-drop/oauth/status", "/api/card-drop/auth-status", "/private"])
def test_anonymous_cannot_query_or_mutate(remote_app, monkeypatch, route):
    async def forbidden():
        pytest.fail("Anonymous traffic must not resolve/refresh the owner's account")

    monkeypatch.setattr(O, "resolve_saved_oauth_status", forbidden)
    response = remote_app.request("POST" if route == "/private" else "GET", route,
                                  headers={"X-Forwarded-For": "127.0.0.1", "Sec-Fetch-Site": "same-origin"})
    assert response.status_code == 401
    assert response.headers["cache-control"] == "no-store"


def test_paired_account_queries_omit_backend_paths(remote_app, monkeypatch):
    pair(remote_app)

    async def status():
        return {"logged_in": True, "snapshot": {"local_user_id": "owner"},
                "auth": {"user": {"email": "owner@example.com", "phone": "secret"}}}

    monkeypatch.setattr(O, "resolve_saved_oauth_status", status)
    monkeypatch.setattr(O, "_desktop_session_paths_for_host", lambda: pytest.fail("Remote path discovery"))
    for route in ("/api/card-drop/oauth/status", "/api/card-drop/auth-status"):
        response = remote_app.get(route)
        assert response.status_code == 200
        assert response.json()["user"]["email"] == "owner@example.com"
        assert not {"session_path", "session_paths", "access_token", "refresh_token"} & response.json().keys()
        assert "phone" not in response.json()["user"]
        assert response.headers["cache-control"] == "no-store"
    assert remote_app.post("/private", headers={"Origin": "https://evil.example"}).status_code == 403


def test_cookie_reuse_and_key_rotation(remote_app, monkeypatch):
    pair(remote_app)
    assert remote_app.post("/private").status_code == 200
    cookie = remote_app.cookies.get(COOKIE)
    remote_app.cookies.set(COOKIE, cookie[:-1] + ("0" if cookie[-1] != "0" else "1"))
    assert remote_app.post("/private").status_code == 401
    remote_app.cookies.clear()
    pair(remote_app)
    monkeypatch.setenv("NEKO_INSTANCE_ACCESS_KEY", "different-instance-key-" + "z" * 40)
    assert remote_app.post("/private").status_code == 401


def test_pairing_requires_challenge_and_origin(remote_app):
    assert remote_app.post("/instance-access/login", data={"key": KEY}).status_code == 401
    assert remote_app.post("/instance-access/login", data={"key": KEY}, headers={"Origin": "https://evil.example"}).status_code == 403
    # Plaintext is allowed by default, but the cross-origin guard still applies.
    response = remote_app.post("http://neko.example/instance-access/login", data={"key": KEY},
                               headers={"Origin": "https://neko.example:8443"})
    assert response.status_code == 403


def _http_pair(client, origin="http://neko.example"):
    page = client.get(origin + "/", headers={"Accept": "text/html"})
    assert page.status_code == 401
    challenge = re.search(r'name="challenge" value="([^"]+)"', page.text).group(1)
    return page, client.post(origin + "/instance-access/login", data={"key": KEY, "challenge": challenge},
                             headers={"Origin": origin}, follow_redirects=False)


def test_plain_http_pairing_is_allowed_by_default_with_warning(remote_app):
    page, response = _http_pair(remote_app)
    assert 'role="alert"' in page.text and "disabled" not in page.text
    assert "neko_instance_challenge_http=" in page.headers["set-cookie"]
    assert "Secure" not in page.headers["set-cookie"]
    assert response.status_code == 303
    session_cookie = response.headers["set-cookie"]
    assert session_cookie.startswith("neko_instance_access_http=")
    assert "HttpOnly" in session_cookie and "Secure" not in session_cookie
    assert remote_app.post("http://neko.example/private", headers={"Origin": "http://neko.example"}).status_code == 200
    with remote_app.websocket_connect("ws://neko.example/socket", headers={"Origin": "http://neko.example"}) as socket:
        assert socket.receive_json() == {"ok": True}


def test_https_pairing_never_shown_insecure_warning(remote_app):
    page = remote_app.get("/", headers={"Accept": "text/html"})
    assert 'role="alert"' not in page.text
    pair(remote_app)


def test_http_pairing_is_independent_of_existing_secure_session(remote_app):
    """A Secure cookie for the same host must not shadow plaintext pairing."""
    pair(remote_app)
    secure_session = remote_app.cookies.get(COOKIE)
    _page, response = _http_pair(remote_app)
    assert response.status_code == 303
    assert remote_app.cookies.get(COOKIE) == secure_session
    assert remote_app.cookies.get(COOKIE + "_http")


def test_require_https_restores_strict_pairing(remote_app, monkeypatch):
    monkeypatch.setenv("NEKO_REQUIRE_HTTPS", "1")
    page = remote_app.get("http://neko.example/", headers={"Accept": "text/html"})
    assert "disabled" in page.text and 'role="alert"' not in page.text
    challenge = re.search(r'name="challenge" value="([^"]+)"', page.text).group(1)
    response = remote_app.post("http://neko.example/instance-access/login", data={"key": KEY, "challenge": challenge},
                               headers={"Origin": "http://neko.example"})
    assert response.status_code == 403
    # Existing plaintext credentials and bearer keys stop working too.
    assert remote_app.post("http://neko.example/private", headers={"Authorization": f"Bearer {KEY}"}).status_code == 401
    with pytest.raises(WebSocketDisconnect):
        with remote_app.websocket_connect("ws://neko.example/socket", headers={"Authorization": f"Bearer {KEY}"}):
            pass
    pair(remote_app)


def test_require_https_revokes_plaintext_session_even_over_https(remote_app, monkeypatch):
    _page, response = _http_pair(remote_app)
    assert response.status_code == 303
    plaintext_session = remote_app.cookies.get(COOKIE + "_http")
    assert remote_app.post("/private").status_code == 200  # Plaintext cookie also reaches HTTPS.
    monkeypatch.setenv("NEKO_REQUIRE_HTTPS", "1")
    remote_app.cookies.clear()
    remote_app.cookies.set(COOKIE + "_http", plaintext_session)
    assert remote_app.post("/private").status_code == 401
    # Cookie names are client-controlled: renaming the captured token or
    # replaying it as a Bearer credential must not launder it into HTTPS.
    remote_app.cookies.clear()
    remote_app.cookies.set(COOKIE, plaintext_session)
    assert remote_app.post("/private").status_code == 401
    remote_app.cookies.clear()
    assert remote_app.post("/private", headers={"Authorization": f"Bearer {plaintext_session}"}).status_code == 401
    # Sessions minted over HTTPS keep working in strict mode.
    pair(remote_app)
    assert remote_app.post("/private").status_code == 200


def test_websocket_needs_instance_credential(remote_app):
    with pytest.raises(WebSocketDisconnect):
        with remote_app.websocket_connect("wss://neko.example/socket"):
            pass
    with remote_app.websocket_connect("wss://neko.example/socket", headers={"Authorization": f"Bearer {KEY}"}) as socket:
        assert socket.receive_json() == {"ok": True}


def test_remote_start_uses_registered_https_callback(remote_app):
    pair(remote_app)
    response = remote_app.post("/api/card-drop/oauth/start", headers={"Origin": "https://neko.example"})
    assert response.status_code == 200
    from urllib.parse import parse_qs, urlparse

    query = parse_qs(urlparse(response.json()["auth_url"]).query)
    assert query["redirect_uri"] == ["https://neko.example/oauth/callback"]
    assert query["client_id"] == ["registered-web-client"]


def test_completion_is_bound_to_attempt_and_browser(remote_app, monkeypatch):
    pair(remote_app)
    first = remote_app.post("/api/card-drop/oauth/start").json()
    pending = O.C._read_json_dict(O._oauth_pending_path())

    async def status():
        import hashlib
        return {"logged_in": True, "auth": {
            "oauth_attempt_state": hashlib.sha256(first["state"].encode()).hexdigest(),
            "oauth_attempt_identity": pending["instance_identity"],
        }}

    monkeypatch.setattr(O, "resolve_saved_oauth_status", status)
    path = "/api/card-drop/oauth/completion"
    assert remote_app.get(path, params={"state": first["state"]}).json() == {"logged_in": True}
    assert remote_app.get(path, params={"state": "different"}).json() == {"logged_in": False}
    remote_app.cookies.clear()
    pair(remote_app)
    assert remote_app.get(path, params={"state": first["state"]}).json() == {"logged_in": False}


def test_remote_default_uses_one_fixed_platform_relay(remote_app, monkeypatch):
    from urllib.parse import parse_qs, urlparse
    import base64
    import json

    monkeypatch.delenv("NEKO_COMMUNITY_WEB_REDIRECT_URI")
    monkeypatch.delenv("NEKO_COMMUNITY_WEB_CLIENT_ID")
    pair(remote_app)
    result = remote_app.post("/api/card-drop/oauth/start").json()
    query = parse_qs(urlparse(result["auth_url"]).query)
    assert query["redirect_uri"] == ["https://auth.example/oauth/callback"]
    assert query["client_id"] == ["neko-servers-web-prod"]
    assert result["relay_origin"] == "https://auth.example"
    state = result["state"]
    payload = json.loads(base64.urlsafe_b64decode(state + "=" * (-len(state) % 4)))
    assert payload["origin"] == "https://neko.example"
    assert len(payload["nonce"]) >= 40


@pytest.mark.asyncio
@pytest.mark.parametrize("socket", [False, True])
async def test_rotating_key_revokes_live_stream_before_next_account_data(monkeypatch, socket):
    monkeypatch.setenv("NEKO_INSTANCE_ACCESS_KEY", KEY)
    monkeypatch.setenv("NEKO_BEHIND_PROXY", "true")
    sent = []
    scope = {"type": "websocket" if socket else "http", "scheme": "wss" if socket else "https",
             "path": "/stream", "raw_path": b"/stream", "query_string": b"",
             "root_path": "", "method": "GET", "server": ("neko.example", 443),
             "client": ("203.0.113.1", 50000),
             "headers": [(b"host", b"neko.example"), (b"authorization", ("Bearer " + KEY).encode())]}

    async def application(scope, receive, send):
        await send({"type": "websocket.accept"} if socket else
                   {"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "websocket.send", "text": "before"} if socket else
                   {"type": "http.response.body", "body": b"before", "more_body": True})
        monkeypatch.setenv("NEKO_INSTANCE_ACCESS_KEY", "rotated-key-" + "z" * 40)
        await send({"type": "websocket.send", "text": "owner-secret"} if socket else
                   {"type": "http.response.body", "body": b"owner-secret", "more_body": True})
        pytest.fail("Revoked producer must stop")

    async def receive():
        return {"type": "websocket.connect"} if socket else {"type": "http.request", "body": b""}

    async def send(message):
        sent.append(message)

    await InstanceAccessMiddleware(application)(scope, receive, send)
    assert "owner-secret" not in str(sent)
    assert sent[-1] == ({"type": "websocket.close", "code": 4401} if socket else
                        {"type": "http.response.body", "body": b"", "more_body": False})


def test_anonymous_api_does_not_touch_private_credential_storage(remote_app, monkeypatch):
    import utils.instance_access as access

    monkeypatch.setattr(access, "instance_key", lambda: pytest.fail("Anonymous credential file IO"))
    assert remote_app.post("/private", content=b"not-json").status_code == 401


def test_real_streaming_response_closes_on_key_rotation(monkeypatch):
    from starlette.responses import StreamingResponse

    monkeypatch.setenv("NEKO_INSTANCE_ACCESS_KEY", KEY)
    monkeypatch.setenv("NEKO_BEHIND_PROXY", "true")
    app = FastAPI()

    @app.get("/stream")
    async def stream():
        async def chunks():
            yield b"before"
            monkeypatch.setenv("NEKO_INSTANCE_ACCESS_KEY", "new-key-" + "x" * 40)
            yield b"secret"
        return StreamingResponse(chunks())

    app.add_middleware(InstanceAccessMiddleware)
    client = TestClient(app, base_url="https://instance.example")
    response = client.get("/stream", headers={"Authorization": "Bearer " + KEY})
    assert response.text == "before"


def test_docker_host_browser_pairs_even_without_forwarding_headers(monkeypatch):
    monkeypatch.setenv("NEKO_BEHIND_PROXY", "true")
    monkeypatch.setenv("NEKO_INSTANCE_ACCESS_KEY", KEY)
    app = FastAPI()

    @app.get("/")
    async def home():
        return {"ok": True}

    app.add_middleware(InstanceAccessMiddleware)
    client = TestClient(app, base_url="https://127.0.0.1", client=("127.0.0.1", 50000))
    page = client.get("/", headers={"Accept": "text/html"})
    assert page.status_code == 401
    challenge = re.search(r'name="challenge" value="([^"]+)"', page.text).group(1)
    connected = client.post("/instance-access/login", data={"key": KEY, "challenge": challenge},
                            headers={"Origin": "https://127.0.0.1"}, follow_redirects=False)
    assert connected.status_code == 303
    assert client.get("/", headers={"Sec-Fetch-Site": "same-origin"}).json() == {"ok": True}


@pytest.mark.parametrize("alias", ["/oauth/callback", "/api/card-drop/oauth/callback"])
@pytest.mark.parametrize("rotate", [False, True])
def test_direct_callback_rechecks_authorization_before_saving(remote_app, monkeypatch, tmp_path, alias, rotate):
    pair(remote_app)
    state = remote_app.post("/api/card-drop/oauth/start").json()["state"]
    monkeypatch.setattr(C, "_social_session_path", lambda: tmp_path / "social.json")
    monkeypatch.setattr(C, "_legacy_social_session_path", lambda: tmp_path / "social.json")

    async def exchange(**_kwargs):
        if rotate:
            monkeypatch.setenv("NEKO_INSTANCE_ACCESS_KEY", "revoked-key-" + "z" * 40)
        return {"access_token": "cloud-token", "refresh_token": "cloud-refresh"}

    async def bootstrap(_base, _token):
        return {"user": {"id": "11111111-1111-4111-8111-111111111111"}}

    async def bind(_base, _token):
        return {"bound": False}

    monkeypatch.setattr(O, "_exchange_oauth_code", exchange)
    monkeypatch.setattr(O, "_bootstrap_session", bootstrap)
    monkeypatch.setattr(O, "_oauth_guest_bind", bind)
    response = remote_app.get(alias, params={"state": state, "code": "one-time"})
    assert response.status_code == (401 if rotate else 200)
    assert C._auth_path().exists() is not rotate
    assert C._social_session_path().exists() is not rotate


def test_generated_key_is_complete_under_concurrent_creation_and_repairs_empty(monkeypatch, tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    from utils.instance_access import instance_key

    monkeypatch.delenv("NEKO_INSTANCE_ACCESS_KEY", raising=False)
    monkeypatch.setenv("NEKO_STORAGE_SELECTED_ROOT", str(tmp_path))
    path = tmp_path / "instance_access.key"
    path.write_text("")  # A legacy interrupted creation must not strand deployment.
    with ThreadPoolExecutor(max_workers=8) as workers:
        keys = list(workers.map(lambda _index: instance_key(), range(16)))
    assert len(set(keys)) == 1
    assert len(keys[0]) >= 32
    assert path.read_text() == keys[0]


@pytest.mark.parametrize("prefix", ["static", "user_vrm", "user_live2d", "user_mmd", "workshop"])
def test_authenticated_assets_keep_private_cache_policy(monkeypatch, prefix):
    from starlette.responses import Response

    monkeypatch.setenv("NEKO_INSTANCE_ACCESS_KEY", KEY)
    app = FastAPI()

    @app.get(f"/{prefix}/model.bin")
    async def model():
        return Response(b"model", headers={"Cache-Control": "public, max-age=3600", "ETag": '"model-1"'})

    app.add_middleware(InstanceAccessMiddleware)
    client = TestClient(app, base_url="https://instance.example")
    response = client.get(f"/{prefix}/model.bin", headers={"Authorization": "Bearer " + KEY})
    assert response.headers["cache-control"] == "private, max-age=3600"
    assert response.headers["etag"] == '"model-1"'
    assert "Cookie" in response.headers["vary"]


@pytest.mark.asyncio
async def test_voice_frames_do_not_dispatch_a_key_read_per_message(monkeypatch):
    import utils.instance_access as access

    monkeypatch.delenv("NEKO_INSTANCE_ACCESS_KEY", raising=False)
    reads = []
    monkeypatch.setattr(access, "instance_key", lambda: reads.append(1) or KEY)
    from tests.fake_clock import patch_module_clock

    patch_module_clock(monkeypatch, access, monotonic=lambda: 100)
    scope = {"type": "websocket", "scheme": "wss", "path": "/voice", "query_string": b"",
             "root_path": "", "server": ("instance.example", 443), "client": ("203.0.113.1", 4000),
             "headers": [(b"host", b"instance.example"), (b"authorization", ("Bearer " + KEY).encode())]}

    async def app(_scope, receive, send):
        await send({"type": "websocket.accept"})
        for _index in range(100):
            await receive()
            await send({"type": "websocket.send", "bytes": b"audio"})
        await send({"type": "websocket.close", "code": 1000})

    async def receive():
        return {"type": "websocket.receive", "bytes": b"audio"}

    async def send(_message):
        pass

    await InstanceAccessMiddleware(app)(scope, receive, send)
    assert len(reads) == 1


def test_full_peer_rate_table_does_not_lock_out_new_owner(monkeypatch):
    import time

    monkeypatch.setenv("NEKO_INSTANCE_ACCESS_KEY", KEY)
    gate = InstanceAccessMiddleware(FastAPI())
    gate.attempts = {str(index): (time.time(), 1) for index in range(1024)}
    client = TestClient(gate, base_url="https://neko.example", client=("203.0.113.1", 1234))
    page = client.get("/", headers={"Accept": "text/html"})
    challenge = re.search(r'name="challenge" value="([^"]+)"', page.text).group(1)
    response = client.post("/instance-access/login", data={"key": KEY, "challenge": challenge},
                           headers={"Origin": "https://neko.example"}, follow_redirects=False)
    assert response.status_code == 303
    assert len(gate.attempts) <= 1024


def test_shared_gateway_failures_do_not_block_correct_owner(remote_app, monkeypatch):
    import utils.instance_access as access

    pauses = []
    original_equal = access._equal

    async def pause(seconds):
        pauses.append(seconds)

    def compare(left, right):
        if left == KEY and right == KEY:
            assert pauses[-1] == 2.0, "Even a correct guess is delayed before key comparison"
        return original_equal(left, right)

    monkeypatch.setattr(access, "_login_verification_pause", pause)
    monkeypatch.setattr(access, "_equal", compare)
    page = remote_app.get("/", headers={"Accept": "text/html"})
    challenge = re.search(r'name="challenge" value="([^"]+)"', page.text).group(1)
    for index in range(11):
        response = remote_app.post("/instance-access/login", data={"key": "wrong", "challenge": challenge},
                                   headers={"Origin": "https://neko.example"})
        assert response.status_code == (401 if index < 10 else 429)
        if index < 10:
            challenge = re.search(r'name="challenge" value="([^"]+)"', response.text).group(1)
    response = remote_app.post("/instance-access/login", data={"key": KEY, "challenge": challenge},
                               headers={"Origin": "https://neko.example"}, follow_redirects=False)
    assert response.status_code == 303
    assert pauses and all(0 < seconds <= 2 for seconds in pauses)


def test_external_document_navigation_keeps_api_and_write_origin_guard(remote_app):
    pair(remote_app)
    headers = {"Sec-Fetch-Site": "cross-site", "Sec-Fetch-Mode": "navigate", "Sec-Fetch-Dest": "document"}
    # The fixture has no entry page; reaching its 404 proves the gate passed.
    assert remote_app.get("/", headers=headers).status_code == 404
    assert remote_app.get("/api/card-drop/auth-status", headers=headers).status_code == 403
    assert remote_app.post("/private", headers=headers).status_code == 403
    assert remote_app.get("/", headers={**headers, "Sec-Fetch-Dest": "iframe"}).status_code == 403


def test_pairing_locale_reads_are_cached(monkeypatch):
    import utils.instance_access as access
    from pathlib import Path

    access._locale_strings.cache_clear()
    original = Path.read_text
    reads = []

    def read(path, *args, **kwargs):
        reads.append(path.name)
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", read)
    try:
        for locale in ("en", "en", "ja", "ja"):
            assert access._locale_strings(locale)["title"]
        assert reads == ["en.json", "ja.json"]
    finally:
        access._locale_strings.cache_clear()


def test_pairing_other_tab_preserves_challenge_and_return_query(remote_app):
    from urllib.parse import parse_qs, urlsplit

    page = remote_app.get("/chat?character=one&mode=compact", headers={"Accept": "text/html"})
    challenge = re.search(r'name="challenge" value="([^"]+)"', page.text).group(1)
    second = remote_app.get("/subtitle", headers={"Accept": "text/html"})
    assert challenge in second.text
    response = remote_app.post("/instance-access/login", data={
        "challenge": challenge, "key": KEY, "return_path": "/chat?character=one&mode=compact",
    }, headers={"Origin": "https://neko.example"}, follow_redirects=False)
    assert response.status_code == 303
    assert parse_qs(urlsplit(response.headers["location"]).query) == {"character": ["one"], "mode": ["compact"]}
    assert "character=one&amp;mode=compact" in page.text


def test_existing_instance_key_does_not_acquire_creation_lock(monkeypatch, tmp_path):
    import utils.instance_access as access

    monkeypatch.delenv("NEKO_INSTANCE_ACCESS_KEY", raising=False)
    monkeypatch.setenv("NEKO_STORAGE_SELECTED_ROOT", str(tmp_path))
    (tmp_path / "instance_access.key").write_text(KEY)
    monkeypatch.setattr(access, "FileLock", lambda *_args, **_kwargs: pytest.fail("Existing key needs no creation lock"))
    assert access.instance_key() == KEY
    (tmp_path / "instance_access.key").write_text("rotated-key-" + "r" * 40)
    assert access.instance_key() == "rotated-key-" + "r" * 40


def test_remote_relay_landing_requires_cookie_and_has_no_opener(remote_app):
    assert remote_app.get("/oauth/relay").status_code == 401
    pair(remote_app)
    response = remote_app.get("/oauth/relay", headers={"Sec-Fetch-Site": "cross-site"})
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["referrer-policy"] == "no-referrer"
    assert "sha256-" in response.headers["content-security-policy"]
    assert "window.opener" not in response.text
    assert "history.replaceState" in response.text
    assert "BroadcastChannel" in response.text


def test_remote_community_preflight_ticket_and_delegate_handoff(remote_app, monkeypatch, tmp_path):
    community = "https://community.example"
    user = "11111111-1111-4111-8111-111111111111"
    snapshot = {"base_url": community, "access_token": "short-lived-community-access",
                "local_user_id": user, "auth_source": "oauth", "refresh_token": "linux-only-refresh"}
    monkeypatch.setattr(C, "_social_base_url", lambda: community)
    monkeypatch.setattr(C, "_desktop_session_snapshot", lambda: snapshot)
    monkeypatch.setattr(C, "_social_session_paths", lambda: [tmp_path / "session.json"])

    async def session():
        return snapshot, None

    async def facts(**_kwargs):
        return {"facts": []}

    monkeypatch.setattr(C, "_native_delegate_session_snapshot", session)
    monkeypatch.setattr(C, "_build_local_forge_facts", facts)
    pair(remote_app)
    ticket = remote_app.get("/api/card-drop/sync-ticket").json()["sync_ticket"]
    delegate = remote_app.get("/api/card-drop/native-delegate").json()["native_delegate"]
    remote_app.cookies.clear()  # Lax cookies are absent from cross-site fetch.
    headers = {"Origin": community, "Sec-Fetch-Site": "cross-site"}
    paths = ["social-session-init", "sync-session", "sync-session/status", "bind-client/approve", "capabilities", "facts/query"]
    for path in paths:
        response = remote_app.options("/api/card-drop/" + path, headers=headers)
        assert response.status_code == 200
        assert response.headers["access-control-allow-origin"] == community
    assert remote_app.get("/api/card-drop/capabilities", headers=headers).status_code == 200
    assert remote_app.get("/api/card-drop/oauth/status", headers=headers).status_code == 401
    assert remote_app.post("/api/card-drop/social-session-init", headers=headers, json={"sync_ticket": "invalid"}).status_code == 401
    handoff = remote_app.post("/api/card-drop/social-session-init", headers=headers, json={"sync_ticket": ticket})
    assert handoff.status_code == 200
    assert handoff.json()["access_token"] == snapshot["access_token"]
    assert "linux-only-refresh" not in handoff.text
    assert remote_app.post("/api/card-drop/social-session-init", headers=headers, json={"sync_ticket": ticket}).status_code == 401
    assert remote_app.post("/api/card-drop/facts/query", headers=headers, json={}).status_code == 401
    facts_headers = {**headers, "Authorization": "Bearer " + delegate, "X-Neko-Local-User-Id": user}
    assert remote_app.post("/api/card-drop/facts/query", headers=facts_headers, json={}).status_code == 200
    assert remote_app.post("/api/card-drop/facts/query", headers={**facts_headers, "Origin": "https://evil.example"}, json={}).status_code == 401

    async def unavailable(_request):
        return "unavailable"

    monkeypatch.setattr(C, "_facts_request_auth_state", unavailable)
    unavailable_response = remote_app.post("/api/card-drop/facts/query", headers=facts_headers, json={})
    assert unavailable_response.status_code == 503
    assert unavailable_response.headers["access-control-allow-origin"] == community


def test_community_cloud_bearer_budget_cannot_be_bypassed_by_rotating_tokens(remote_app, monkeypatch):
    from tests.fake_clock import patch_module_clock

    community = "https://community.example"
    now = [1000.0]
    patch_module_clock(monkeypatch, C, monotonic=lambda: now[0])
    monkeypatch.setattr(C, "_facts_cloud_budget", {"tokens": 12.0, "updated": now[0], "active": 0})
    monkeypatch.setattr(C, "_social_base_url", lambda: community)
    monkeypatch.setattr(C, "_desktop_session_snapshot", lambda: None)
    calls = []

    async def reject(base, token):
        calls.append(token)
        return C._CloudIdentityLookup(None, 401, "rejected")

    monkeypatch.setattr(C, "_lookup_cloud_identity", reject)
    for index in range(12):
        response = remote_app.get("/api/card-drop/facts", headers={"Origin": community, "Authorization": f"Bearer fake-{index}", "X-Forwarded-For": f"203.0.113.{index // 3 + 1}"})
        assert response.status_code == 401
    limited = remote_app.get("/api/card-drop/active-character", headers={"Origin": community, "Authorization": "Bearer another-fake"})
    assert limited.status_code == 429
    assert limited.headers["access-control-allow-origin"] == community
    assert len(calls) == 12
    now[0] += 5
    assert remote_app.get("/api/card-drop/facts", headers={"Origin": community, "Authorization": "Bearer retry"}).status_code == 401
    assert len(calls) == 13


def test_one_peer_cannot_monopolize_cloud_budget_or_block_scoped_delegates(remote_app, monkeypatch):
    community = "https://community.example"
    user = "11111111-1111-4111-8111-111111111111"
    snapshot = {"base_url": community, "access_token": "desktop-token", "local_user_id": user, "auth_source": "oauth"}
    monkeypatch.setattr(C, "_social_base_url", lambda: community)
    monkeypatch.setattr(C, "_desktop_session_snapshot", lambda: snapshot)
    calls = []

    async def lookup(base, token):
        calls.append(token)
        if token == "valid-other-peer":
            return C._CloudIdentityLookup(C._CloudIdentity(user, "oauth", {}), 200)
        return C._CloudIdentityLookup(None, 401, "rejected")

    async def facts(**kwargs):
        return {"facts": []}

    monkeypatch.setattr(C, "_lookup_cloud_identity", lookup)
    monkeypatch.setattr(C, "_build_local_forge_facts", facts)
    for index in range(5):
        response = remote_app.get("/api/card-drop/facts", headers={"Origin": community, "Authorization": f"Bearer random-{index}"})
        assert response.status_code == (401 if index < 3 else 429)
    headers = {"Origin": community, "Authorization": "Bearer valid-other-peer", "X-Forwarded-For": "203.0.113.20"}
    assert remote_app.get("/api/card-drop/facts", headers=headers).status_code == 200
    delegate = C._issue_native_delegate(local_user_id=user, audience=community, session_fingerprint=C._desktop_session_fingerprint(snapshot), scopes=frozenset({"facts:read"}))
    C._facts_cloud_budget["tokens"] = 0
    C._facts_cloud_budget["updated"] = C.time.monotonic()
    assert remote_app.get("/api/card-drop/facts", headers={"Origin": community, "Authorization": "Bearer " + delegate, "X-Neko-Local-User-Id": user}).status_code == 200
    assert len(calls) == 4


@pytest.mark.parametrize("switch_account", [False, True])
def test_community_cloud_read_reuses_proof_and_rechecks_local_session(remote_app, monkeypatch, switch_account):
    community = "https://community.example"
    user = "11111111-1111-4111-8111-111111111111"
    snapshot = {"base_url": community, "access_token": "desktop-token", "local_user_id": user, "auth_source": "oauth"}
    monkeypatch.setattr(C, "_social_base_url", lambda: community)
    monkeypatch.setattr(C, "_desktop_session_snapshot", lambda: snapshot.copy())
    calls = []

    async def lookup(base, token):
        calls.append(token)
        return C._CloudIdentityLookup(C._CloudIdentity(user, "oauth", {}), 200)

    async def facts(**kwargs):
        return {"facts": []}

    original_authorizer = C.authorize_community_handoff

    async def authorizer(request):
        result = await original_authorizer(request)
        if switch_account:
            snapshot["access_token"] = "new-session"
        return result

    remote_app.app.app.user_middleware[0].kwargs["community_handoff_authorizer"] = authorizer
    monkeypatch.setattr(C, "_lookup_cloud_identity", lookup)
    monkeypatch.setattr(C, "_build_local_forge_facts", facts)
    response = remote_app.post("/api/card-drop/facts/query", headers={"Origin": community, "Authorization": "Bearer valid-cloud"}, json={})
    assert response.status_code == (401 if switch_account else 200)
    assert calls == ["valid-cloud"]


def test_successful_cloud_reads_under_same_nat_preserve_peer_allowance(remote_app, monkeypatch):
    community = "https://community.example"
    user = "11111111-1111-4111-8111-111111111111"
    snapshot = {"base_url": community, "access_token": "desktop-token", "local_user_id": user, "auth_source": "oauth"}
    monkeypatch.setattr(C, "_social_base_url", lambda: community)
    monkeypatch.setattr(C, "_desktop_session_snapshot", lambda: snapshot)
    calls = []

    async def lookup(base, token):
        calls.append(token)
        return C._CloudIdentityLookup(C._CloudIdentity(user, "oauth", {}), 200)

    async def facts(**kwargs):
        return {"facts": []}

    monkeypatch.setattr(C, "_lookup_cloud_identity", lookup)
    monkeypatch.setattr(C, "_build_local_forge_facts", facts)
    # Different valid cloud credentials behind the same trusted-proxy peer
    # must not exhaust its three-failure allowance, but still cost cloud work.
    for index in range(12):
        headers = {"Origin": community, "Authorization": f"Bearer valid-client-{index}"}
        assert remote_app.get("/api/card-drop/facts", headers=headers).status_code == 200
    assert len(calls) == 12
    assert remote_app.get("/api/card-drop/facts", headers=headers).status_code == 429
    assert len(calls) == 12


def test_internal_market_proof_is_bound_to_loopback_route_and_method(monkeypatch):
    from utils.instance_access import market_internal_proof

    monkeypatch.setenv("NEKO_INSTANCE_ACCESS_KEY", KEY)
    monkeypatch.setenv("NEKO_BEHIND_PROXY", "true")
    app = FastAPI()

    @app.get("/market/test")
    async def market():
        return {"ok": True}

    app.add_middleware(InstanceAccessMiddleware)
    headers = {"Origin": "https://public.example", "Authorization": "Bearer market-oauth-token",
               "X-Neko-Market-Internal": market_internal_proof(KEY, "GET", "/market/test")}
    local = TestClient(app, base_url="http://127.0.0.1:48916", client=("127.0.0.1", 1234))
    assert local.get("/market/test", headers=headers).status_code == 200
    assert local.post("/market/test", headers=headers).status_code == 401
    assert local.get("/private", headers=headers).status_code == 401
    remote = TestClient(app, base_url="http://127.0.0.1:48916", client=("203.0.113.1", 1234))
    assert remote.get("/market/test", headers=headers).status_code == 401
    assert local.get("/market/test", headers={**headers, "X-Neko-Market-Public-Origin": "https://attacker.example"}).status_code == 401


def test_internal_market_proof_accepts_plain_http_public_origin_unless_https_required(monkeypatch):
    from utils.instance_access import market_internal_proof

    monkeypatch.setenv("NEKO_INSTANCE_ACCESS_KEY", KEY)
    monkeypatch.setenv("NEKO_BEHIND_PROXY", "true")
    app = FastAPI()

    @app.get("/market/test")
    async def market():
        return {"ok": True}

    app.add_middleware(InstanceAccessMiddleware)
    origin = "http://192.168.1.10:48911"
    # A browser Origin keeps this out of the headerless native-loopback exemption.
    headers = {"Origin": origin, "X-Neko-Market-Public-Origin": origin,
               "X-Neko-Market-Internal": market_internal_proof(KEY, "GET", "/market/test", origin)}
    local = TestClient(app, base_url="http://127.0.0.1:48916", client=("127.0.0.1", 1234))
    assert local.get("/market/test", headers=headers).status_code == 200
    for bad in ("ftp://192.168.1.10", "http://user@192.168.1.10", "http://192.168.1.10/path"):
        forged = {"Origin": origin, "X-Neko-Market-Public-Origin": bad,
                  "X-Neko-Market-Internal": market_internal_proof(KEY, "GET", "/market/test", bad)}
        assert local.get("/market/test", headers=forged).status_code == 401
    monkeypatch.setenv("NEKO_REQUIRE_HTTPS", "true")
    assert local.get("/market/test", headers=headers).status_code == 401


@pytest.mark.parametrize("header", ["X-Forwarded", "X-Forwarded-Host", "X-Forwarded-Proto", "X-REAL-IP", "Forwarded"])
def test_forwarding_metadata_covers_proxy_header_family(header):
    from utils.deployment import has_forwarding_metadata

    assert has_forwarding_metadata({header: "proxy"})
    assert not has_forwarding_metadata({"X-Client-Name": "native"})


@pytest.mark.parametrize("name", ["NEKO_ACTIVITY_TRACKER_REMOTE", "ACTIVITY_TRACKER_REMOTE"])
def test_instance_and_os_features_share_remote_on_flag(monkeypatch, name):
    from utils.instance_access import _local_native
    from main_logic.activity.system_signals import is_remote_backend_deployment
    from starlette.requests import Request

    monkeypatch.setenv(name, " on ")
    request = Request({"type": "http", "scheme": "https", "method": "GET", "path": "/",
                       "query_string": b"", "headers": [(b"host", b"127.0.0.1"), (b"accept", b"text/html")],
                       "server": ("127.0.0.1", 443), "client": ("127.0.0.1", 2000)})
    assert is_remote_backend_deployment()
    assert not _local_native(request)


@pytest.mark.parametrize("blocked_attempts", [1, 8])
def test_empty_key_windows_reader_conflict_is_bounded(monkeypatch, tmp_path, blocked_attempts):
    """Retry transient Windows readers without publishing partial key content."""
    import utils.instance_access as access

    monkeypatch.delenv("NEKO_INSTANCE_ACCESS_KEY", raising=False)
    monkeypatch.setenv("NEKO_STORAGE_SELECTED_ROOT", str(tmp_path))
    path = tmp_path / "instance_access.key"
    path.write_text("")
    replace = access.os.replace
    attempts = []
    sleeps = []

    def reader_conflict(source, target):
        attempts.append(1)
        assert path.read_text() == ""
        if len(attempts) <= blocked_attempts:
            error = PermissionError("Windows reader sharing conflict")
            error.winerror = 5
            raise error
        replace(source, target)

    monkeypatch.setattr(access.os, "replace", reader_conflict)
    from tests.fake_clock import patch_module_clock

    patch_module_clock(monkeypatch, access, sleep=sleeps.append)
    if blocked_attempts == 8:
        with pytest.raises(PermissionError):
            access.instance_key()
        assert path.read_text() == ""
        assert len(attempts) == 8
    else:
        key = access.instance_key()
        assert len(key) >= 32 and path.read_text() == key
        assert len(attempts) == 2
    assert len(sleeps) == min(blocked_attempts, 7)
    assert not list(tmp_path.glob(".instance-key-*"))


@pytest.mark.parametrize("rotate", [False, True])
async def test_market_hop_expiry_limits_admission_but_not_live_response(monkeypatch, rotate):
    import utils.instance_access as access
    from tests.fake_clock import patch_module_clock

    now = [1000.0]
    patch_module_clock(monkeypatch, access, time=lambda: now[0])
    monkeypatch.setenv("NEKO_INSTANCE_ACCESS_KEY", KEY)
    monkeypatch.setenv("NEKO_BEHIND_PROXY", "true")
    proof = access.market_internal_proof(KEY, "GET", "/market/test")
    scope = {"type": "http", "scheme": "http", "method": "GET",
             "path": "/market/test", "query_string": b"", "root_path": "",
             "server": ("127.0.0.1", 48916), "client": ("127.0.0.1", 1234),
             "headers": [(b"host", b"127.0.0.1:48916"),
                         (b"origin", b"https://public.example"),
                         (b"x-neko-market-internal", proof.encode())]}
    sent = []

    async def application(scope, receive, send):
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"before", "more_body": True})
        now[0] += 61
        if rotate:
            monkeypatch.setenv("NEKO_INSTANCE_ACCESS_KEY", "new-key-" + "z" * 40)
        await send({"type": "http.response.body", "body": b"after", "more_body": False})

    async def receive():
        return {"type": "http.request", "body": b""}

    async def send(message):
        sent.append(message)

    await access.InstanceAccessMiddleware(application)(scope, receive, send)
    assert (b"after" in [m.get("body") for m in sent]) is not rotate
    # An expired proof must never admit a new request, even with unchanged key.
    sent.clear()
    await access.InstanceAccessMiddleware(application)(scope, receive, send)
    assert sent[0]["status"] == 401


def test_market_callback_cross_site_requires_instance_auth_and_valid_state(monkeypatch, tmp_path):
    from plugin.server.routes import market_bridge as market

    monkeypatch.setenv("NEKO_INSTANCE_ACCESS_KEY", KEY)
    monkeypatch.setenv("NEKO_BEHIND_PROXY", "true")
    monkeypatch.setattr(market, "_OAUTH_PENDING_FILE", tmp_path / "pending.json")
    monkeypatch.setattr(market, "_OAUTH_CALLBACK_FILE", tmp_path / "callback.json")
    import time
    market._write_private_json(market._OAUTH_PENDING_FILE,
                               {"state": "expected", "expires_at": time.time() + 120})
    app = FastAPI()
    app.include_router(market.router)
    app.add_middleware(InstanceAccessMiddleware)
    client = TestClient(app, base_url="https://neko.example", client=("203.0.113.1", 1234))
    headers = {"Sec-Fetch-Site": "cross-site", "Sec-Fetch-Mode": "navigate",
               "Sec-Fetch-Dest": "document"}
    path = "/market/oauth/callback?code=once&state=expected"
    assert client.get(path, headers=headers).status_code == 401
    headers["Authorization"] = "Bearer " + KEY
    assert client.get(path.replace("state=expected", "state=wrong"), headers=headers).status_code == 400
    assert not market._OAUTH_CALLBACK_FILE.exists()
    assert client.get(path, headers=headers).status_code == 200
    assert market._read_json_file(market._OAUTH_CALLBACK_FILE)["code"] == "once"


@pytest.mark.parametrize("origin", ["http://192.168.1.10:48911", "http://neko.example:48911"])
def test_http_pairing_works_alongside_pinned_https_public_origin(remote_app, monkeypatch, origin):
    """A pinned TLS gateway origin must not reject the deployment's own HTTP entry."""
    monkeypatch.setenv("NEKO_INSTANCE_PUBLIC_ORIGIN", "https://neko.example:48912")
    _page, response = _http_pair(remote_app, origin)
    assert response.status_code == 303
    assert remote_app.post(origin + "/private", headers={"Origin": origin}).status_code == 200
    # Cross-site writes stay rejected on the HTTP entry.
    assert remote_app.post(origin + "/private", headers={"Origin": "http://evil.example"}).status_code == 403


def test_outer_tls_gateway_without_forwarded_proto_can_pair(remote_app, monkeypatch):
    """TLS ended outside, no pinned origin, no trusted X-Forwarded-Proto."""
    page = remote_app.get("http://neko.example/", headers={"Accept": "text/html"})
    challenge = re.search(r'name="challenge" value="([^"]+)"', page.text).group(1)
    login = {"data": {"key": KEY, "challenge": challenge}, "headers": {"Origin": "https://neko.example"},
             "follow_redirects": False}
    monkeypatch.delenv("NEKO_BEHIND_PROXY")
    # Without a proxy in front, nothing can serve https:// on this Host.
    assert remote_app.post("http://neko.example/instance-access/login", **login).status_code == 403
    monkeypatch.setenv("NEKO_BEHIND_PROXY", "true")
    assert remote_app.post("http://neko.example/instance-access/login", **login).status_code == 303


def test_outer_tls_fallback_treats_explicit_443_as_default_port(remote_app):
    """A proxy may forward Host ":443" while the browser omits the default port."""
    page = remote_app.get("http://neko.example:443/", headers={"Accept": "text/html"})
    challenge = re.search(r'name="challenge" value="([^"]+)"', page.text).group(1)
    response = remote_app.post("http://neko.example:443/instance-access/login",
                               data={"key": KEY, "challenge": challenge},
                               headers={"Origin": "https://neko.example"}, follow_redirects=False)
    assert response.status_code == 303
    assert remote_app.post("http://neko.example:443/private", headers={"Origin": "https://neko.example"}).status_code == 200
    assert remote_app.post("http://neko.example:443/private", headers={"Origin": "https://neko.example:8443"}).status_code == 403


@pytest.mark.parametrize("send_origin", [True, False])
def test_oauth_state_returns_to_the_entry_the_browser_paired_on(remote_app, monkeypatch, send_origin):
    import base64
    import json

    monkeypatch.setenv("NEKO_INSTANCE_PUBLIC_ORIGIN", "https://neko.example:48912")
    monkeypatch.delenv("NEKO_COMMUNITY_WEB_REDIRECT_URI")
    monkeypatch.delenv("NEKO_COMMUNITY_WEB_CLIENT_ID")
    lan = "http://192.168.1.10:48911"
    _page, response = _http_pair(remote_app, lan)
    assert response.status_code == 303
    result = remote_app.post(lan + "/api/card-drop/oauth/start", headers={"Origin": lan} if send_origin else {})
    state = result.json()["state"]
    assert json.loads(base64.urlsafe_b64decode(state + "=" * (-len(state) % 4)))["origin"] == lan


def test_public_origin_prefers_verified_browser_origin_then_pinned_gateway(monkeypatch):
    from starlette.requests import Request
    from utils.instance_access import request_public_origin

    def request(host, origin=None, scheme="http"):
        headers = [(b"host", host.encode())] + ([(b"origin", origin.encode())] if origin else [])
        return Request({"type": "http", "scheme": scheme, "method": "GET", "path": "/", "query_string": b"",
                        "server": (host.split(":")[0], 80), "headers": headers})

    monkeypatch.setenv("NEKO_BEHIND_PROXY", "true")
    monkeypatch.setenv("NEKO_INSTANCE_PUBLIC_ORIGIN", "https://neko.example:48912")
    assert request_public_origin(request("neko.example:48912")) == "https://neko.example:48912"
    assert request_public_origin(request("192.168.1.10:48911")) == "http://192.168.1.10:48911"
    # A gateway that rewrites Host still reports the browser's real origin.
    assert request_public_origin(request("127.0.0.1:48911", "https://neko.example:48912")) == "https://neko.example:48912"
    # A foreign Origin never becomes the callback target.
    assert request_public_origin(request("192.168.1.10:48911", "https://evil.example")) == "http://192.168.1.10:48911"


def test_oauth_retry_from_another_entry_does_not_reuse_first_entry_state(remote_app, monkeypatch):
    import base64
    import json

    monkeypatch.delenv("NEKO_COMMUNITY_WEB_REDIRECT_URI")
    monkeypatch.delenv("NEKO_COMMUNITY_WEB_CLIENT_ID")

    def state_origin(result):
        state = result.json()["state"]
        return json.loads(base64.urlsafe_b64decode(state + "=" * (-len(state) % 4)))["origin"]

    http_entry = "http://neko.example:48911"
    _page, response = _http_pair(remote_app, http_entry)
    assert response.status_code == 303
    first = remote_app.post(http_entry + "/api/card-drop/oauth/start", headers={"Origin": http_entry})
    assert state_origin(first) == http_entry
    # Same browser and identity (host-bound cookie spans ports), other entry.
    second = remote_app.post("/api/card-drop/oauth/start", headers={"Origin": "https://neko.example"})
    assert state_origin(second) == "https://neko.example"
    # Retrying from the same entry still reuses the pending attempt.
    again = remote_app.post("/api/card-drop/oauth/start", headers={"Origin": "https://neko.example"})
    assert again.json()["state"] == second.json()["state"]


def test_pinned_gateway_host_with_explicit_default_port_is_secure_and_public(monkeypatch):
    from starlette.requests import Request
    from utils.instance_access import _pinned_origin, _secure_transport, request_public_origin

    def request(host):
        return Request({"type": "http", "scheme": "http", "method": "GET", "path": "/", "query_string": b"",
                        "server": (host.split(":")[0], 80), "headers": [(b"host", host.encode())]})

    monkeypatch.setenv("NEKO_BEHIND_PROXY", "true")
    monkeypatch.setenv("NEKO_INSTANCE_PUBLIC_ORIGIN", "https://neko.example/")
    assert _pinned_origin() == "https://neko.example"
    for host in ("neko.example", "neko.example:443"):
        assert _secure_transport(request(host)) is True
        assert request_public_origin(request(host)) == "https://neko.example"
    assert _secure_transport(request("neko.example:80")) is False
    assert request_public_origin(request("neko.example:80")) == "http://neko.example:80"
    for invalid in ("neko.example", "https://user@neko.example", "https://neko.example/path", "https://neko.example:x"):
        monkeypatch.setenv("NEKO_INSTANCE_PUBLIC_ORIGIN", invalid)
        assert _pinned_origin() == ""


def test_websocket_handler_sees_transport_decisions_on_its_own_scope(monkeypatch):
    monkeypatch.setenv("NEKO_INSTANCE_ACCESS_KEY", KEY)
    monkeypatch.setenv("NEKO_BEHIND_PROXY", "true")
    app = FastAPI()

    @app.websocket("/socket")
    async def websocket(socket: WebSocket):
        await socket.accept()
        await socket.send_json({key: socket.scope.get(key) for key in ("neko.require_https", "neko.secure_transport")})
        await socket.close()

    app.add_middleware(InstanceAccessMiddleware)
    client = TestClient(app, base_url="https://neko.example", client=("203.0.113.1", 1234))
    with client.websocket_connect("wss://neko.example/socket", headers={"Authorization": f"Bearer {KEY}"}) as socket:
        assert socket.receive_json() == {"neko.require_https": False, "neko.secure_transport": True}


def test_outer_tls_pairing_logs_configuration_hint_once(remote_app, caplog):
    for _ in range(2):
        remote_app.cookies.clear()
        page = remote_app.get("http://neko.example/", headers={"Accept": "text/html"})
        challenge = re.search(r'name="challenge" value="([^"]+)"', page.text).group(1)
        with caplog.at_level("WARNING", logger="utils.instance_access"):
            response = remote_app.post("http://neko.example/instance-access/login",
                                       data={"key": KEY, "challenge": challenge},
                                       headers={"Origin": "https://neko.example"}, follow_redirects=False)
        assert response.status_code == 303
    hints = [r for r in caplog.records if "NEKO_INSTANCE_PUBLIC_ORIGIN" in r.getMessage()]
    assert len(hints) == 1


def test_outer_tls_hint_is_logged_when_strict_mode_refuses_pairing(remote_app, monkeypatch, caplog):
    monkeypatch.setenv("NEKO_REQUIRE_HTTPS", "1")
    page = remote_app.get("http://neko.example/", headers={"Accept": "text/html"})
    challenge = re.search(r'name="challenge" value="([^"]+)"', page.text).group(1)
    with caplog.at_level("WARNING", logger="utils.instance_access"):
        # A cross-site https Origin is refused without spending the hint.
        assert remote_app.post("http://neko.example/instance-access/login", data={"key": KEY, "challenge": challenge},
                               headers={"Origin": "https://evil.example"}).status_code == 403
        assert not [r for r in caplog.records if "NEKO_INSTANCE_PUBLIC_ORIGIN" in r.getMessage()]
        response = remote_app.post("http://neko.example/instance-access/login", data={"key": KEY, "challenge": challenge},
                                   headers={"Origin": "https://neko.example"})
    assert response.status_code == 403
    assert [r for r in caplog.records if "NEKO_INSTANCE_PUBLIC_ORIGIN" in r.getMessage()]


def test_outer_tls_hint_repeats_after_an_hour(monkeypatch, caplog):
    import utils.instance_access as access
    from tests.fake_clock import patch_module_clock

    middleware = InstanceAccessMiddleware(app=None)
    clock = iter([1000.0, 1000.0 + 1800, 1000.0 + 3600])
    patch_module_clock(monkeypatch, access, monotonic=lambda: next(clock))
    with caplog.at_level("WARNING", logger="utils.instance_access"):
        for _ in range(3):
            middleware._warn_unlabelled_tls()
    # An early (even unauthenticated) trigger cannot silence it for good.
    assert len([r for r in caplog.records if "NEKO_INSTANCE_PUBLIC_ORIGIN" in r.getMessage()]) == 2
