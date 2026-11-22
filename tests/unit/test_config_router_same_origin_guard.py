"""State-changing ``/api/config/*`` requests must be same-origin JSON.

The guard is a router-level dependency on ``config_router.router``. These tests
drive the real router through an ASGI client so every write route is covered,
and stub the persistence call so "rejected" can be checked as "nothing saved".
"""

from __future__ import annotations

from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI
from fastapi.routing import APIRoute

from main_routers import config_router
from main_routers.config_router import _shared as config_shared
from main_routers.config_router import language as language_router
from main_routers.config_router import preferences as preferences_router


BASE_URL = "http://localhost:48911"
SETTINGS_PATH = "/api/config/conversation-settings"


def _snapshot(revision: int, settings: dict | None = None):
    return SimpleNamespace(
        settings=dict(settings or {}),
        revision=revision,
        asr_decision=None,
        reset=False,
    )


class _SaveRecorder:
    """Stand-in for save_global_conversation_settings_versioned."""

    def __init__(self, conflicts: int = 0):
        self.calls: list[dict] = []
        self._conflicts = conflicts
        self.revision = 1

    def __call__(self, data, *, expected_revision=None, asr_decision=None, full_snapshot=False):
        self.calls.append({"data": dict(data), "expected_revision": expected_revision})
        if self._conflicts > 0:
            self._conflicts -= 1
            return SimpleNamespace(
                snapshot=_snapshot(self.revision),
                previous=None,
                conflict=True,
                success=False,
            )
        previous = _snapshot(self.revision)
        self.revision += 1
        return SimpleNamespace(
            snapshot=_snapshot(self.revision, data),
            previous=previous,
            conflict=False,
            success=True,
        )


@pytest.fixture
def save_recorder(monkeypatch):
    recorder = _SaveRecorder()
    monkeypatch.setattr(
        preferences_router, "save_global_conversation_settings_versioned", recorder
    )
    return recorder


@pytest.fixture
def app():
    application = FastAPI()
    application.include_router(config_router.router)
    return application


def _client(app, base_url: str = BASE_URL) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=base_url)


_JSON = {"Content-Type": "application/json"}
_BODY = b'{"proactiveChatEnabled": true}'


# --- rejected requests -----------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "headers, status",
    [
        # Cross-site simple request: text/plain body, foreign Origin.
        ({"Content-Type": "text/plain", "Origin": "https://evil.example", "Sec-Fetch-Site": "cross-site"}, 403),
        # Foreign Origin even with a JSON content type.
        ({**_JSON, "Origin": "https://evil.example"}, 403),
        # Origin "null" (sandboxed frame / opaque origin).
        ({**_JSON, "Origin": "null"}, 403),
        # Origin omitted, but the browser reports the request as cross-site.
        ({**_JSON, "Sec-Fetch-Site": "cross-site"}, 403),
        ({**_JSON, "Sec-Fetch-Site": "same-site"}, 403),
        # Origin omitted, Referer points at a foreign page.
        ({**_JSON, "Referer": "https://evil.example/page"}, 403),
        # Same-origin page, but not a JSON body (simple-request content types).
        ({"Content-Type": "text/plain", "Origin": BASE_URL, "Sec-Fetch-Site": "same-origin"}, 415),
        ({"Content-Type": "application/x-www-form-urlencoded", "Sec-Fetch-Site": "same-origin"}, 415),
        ({"Content-Type": "multipart/form-data; boundary=x", "Sec-Fetch-Site": "same-origin"}, 415),
        # No Content-Type at all (e.g. a Blob without a type).
        ({"Sec-Fetch-Site": "same-origin"}, 415),
        ({}, 415),
        # A JSON-looking but different media type.
        ({"Content-Type": "application/jsonp", "Sec-Fetch-Site": "same-origin"}, 415),
    ],
)
async def test_conversation_settings_rejects_cross_site_or_non_json(app, save_recorder, headers, status):
    async with _client(app) as client:
        response = await client.post(SETTINGS_PATH, content=_BODY, headers=headers)
    assert response.status_code == status
    assert save_recorder.calls == []


def _mutating_routes():
    routes = []
    for route in config_router.router.routes:
        if not isinstance(route, APIRoute):
            continue
        for method in sorted(route.methods - {"GET", "HEAD", "OPTIONS"}):
            routes.append((method, route.path))
    return routes


def test_every_config_write_route_is_guarded():
    routes = _mutating_routes()
    # Sanity check so the parametrized test below cannot silently cover nothing.
    assert ("POST", SETTINGS_PATH) in routes
    assert ("PUT", "/api/config/ui-language") in routes
    assert ("POST", "/api/config/core_api") in routes


@pytest.mark.asyncio
@pytest.mark.parametrize("method, path", _mutating_routes())
async def test_every_config_write_route_rejects_cross_site(app, method, path):
    async with _client(app) as client:
        cross_site = await client.request(
            method,
            path,
            content=b"{}",
            headers={"Content-Type": "text/plain", "Origin": "https://evil.example", "Sec-Fetch-Site": "cross-site"},
        )
        not_json = await client.request(
            method,
            path,
            content=b"{}",
            headers={"Content-Type": "text/plain", "Sec-Fetch-Site": "same-origin"},
        )
    assert cross_site.status_code == 403
    assert not_json.status_code == 415


@pytest.mark.asyncio
async def test_reads_are_not_guarded(app, monkeypatch):
    async def fake_load():
        return {"ok": True}

    monkeypatch.setattr(preferences_router, "aload_user_preferences", fake_load)
    async with _client(app) as client:
        response = await client.get(
            "/api/config/preferences",
            headers={"Origin": "https://evil.example", "Sec-Fetch-Site": "cross-site"},
        )
    assert response.status_code == 200


# --- legitimate callers ------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "headers",
    [
        # Web UI on the default port: same-origin fetch.
        {**_JSON, "Origin": BASE_URL, "Sec-Fetch-Site": "same-origin"},
        {"Content-Type": "application/json; charset=utf-8", "Origin": BASE_URL, "Sec-Fetch-Site": "same-origin"},
        # Electron windows with webSecurity disabled omit Origin on POST.
        {**_JSON, "Sec-Fetch-Site": "same-origin", "Referer": f"{BASE_URL}/chat"},
        {**_JSON, "Sec-Fetch-Site": "same-origin"},
        # Loopback alias of the same backend (localhost vs 127.0.0.1).
        {**_JSON, "Origin": "http://127.0.0.1:48911", "Sec-Fetch-Site": "same-origin"},
        # Native local clients (Electron main process, scripts): no browser headers.
        dict(_JSON),
    ],
)
async def test_conversation_settings_accepts_same_origin_callers(app, save_recorder, headers):
    async with _client(app) as client:
        response = await client.post(SETTINGS_PATH, content=_BODY, headers=headers)
    assert response.status_code == 200, response.text
    assert response.json()["success"] is True
    assert len(save_recorder.calls) == 1
    assert save_recorder.calls[0]["data"] == {"proactiveChatEnabled": True}


@pytest.mark.asyncio
async def test_conversation_settings_legacy_client_without_if_match(app, save_recorder):
    async with _client(app) as client:
        response = await client.post(
            SETTINGS_PATH,
            json={"proactiveChatEnabled": False},
            headers={"Origin": BASE_URL, "Sec-Fetch-Site": "same-origin"},
        )
    assert response.status_code == 200
    assert save_recorder.calls[0]["expected_revision"] is None


@pytest.mark.asyncio
async def test_conversation_settings_cas_conflict_then_retry(app, monkeypatch):
    recorder = _SaveRecorder(conflicts=1)
    monkeypatch.setattr(
        preferences_router, "save_global_conversation_settings_versioned", recorder
    )
    headers = {**_JSON, "Origin": BASE_URL, "Sec-Fetch-Site": "same-origin"}
    async with _client(app) as client:
        first = await client.post(
            SETTINGS_PATH,
            content=_BODY,
            headers={**headers, "If-Match": '"conversation-settings-0"'},
        )
        assert first.status_code == 412
        etag = first.headers["ETag"]
        retry = await client.post(SETTINGS_PATH, content=_BODY, headers={**headers, "If-Match": etag})
    assert retry.status_code == 200
    assert [call["expected_revision"] for call in recorder.calls] == [0, 1]


@pytest.mark.asyncio
async def test_desktop_shell_ui_language_put_passes_guard(app, monkeypatch):
    """The desktop main process sends Origin = backend origin plus a CSRF token."""
    from main_routers.system_router import _shared as system_shared

    monkeypatch.setattr(system_shared, "AUTOSTART_CSRF_TOKEN", "csrf-test-token")
    persisted = []

    async def fake_persist(normalized):
        persisted.append(normalized)
        return {"success": True, "language": normalized}

    monkeypatch.setattr(language_router, "_persist_ui_language_and_sync", fake_persist)
    async with _client(app) as client:
        response = await client.put(
            "/api/config/ui-language",
            json={"language": "en"},
            headers={"Origin": BASE_URL, "X-CSRF-Token": "csrf-test-token"},
        )
    assert response.status_code == 200, response.text
    assert persisted == ["en"]


@pytest.mark.asyncio
async def test_remote_instance_session_uses_instance_same_origin(app, save_recorder, monkeypatch):
    """Authorized remote sessions compare Origin with the instance's own origin."""
    monkeypatch.delenv("NEKO_INSTANCE_PUBLIC_ORIGIN", raising=False)

    class _MarkInstance:
        def __init__(self, inner):
            self.inner = inner

        async def __call__(self, scope, receive, send):
            if scope["type"] == "http":
                scope["neko.instance_identity"] = "test-identity"
            await self.inner(scope, receive, send)

    wrapped = _MarkInstance(app)
    base = "http://neko.example"
    async with _client(wrapped, base_url=base) as client:
        ok = await client.post(
            SETTINGS_PATH,
            content=_BODY,
            headers={**_JSON, "Origin": base, "Sec-Fetch-Site": "same-origin"},
        )
        foreign = await client.post(
            SETTINGS_PATH,
            content=_BODY,
            headers={**_JSON, "Origin": "http://other.example", "Sec-Fetch-Site": "cross-site"},
        )
        foreign_no_fetch_metadata = await client.post(
            SETTINGS_PATH,
            content=_BODY,
            headers={**_JSON, "Origin": "http://other.example"},
        )
    assert ok.status_code == 200
    assert foreign.status_code == 403
    assert foreign_no_fetch_metadata.status_code == 403
    assert len(save_recorder.calls) == 1


@pytest.mark.asyncio
async def test_remote_instance_session_accepts_pinned_public_origin(app, save_recorder, monkeypatch):
    """Behind a gateway the page origin is the pinned public origin, not the Host."""
    monkeypatch.setenv("NEKO_INSTANCE_PUBLIC_ORIGIN", "https://public.example")

    class _MarkInstance:
        def __init__(self, inner):
            self.inner = inner

        async def __call__(self, scope, receive, send):
            if scope["type"] == "http":
                scope["neko.instance_identity"] = "test-identity"
            await self.inner(scope, receive, send)

    async with _client(_MarkInstance(app), base_url="http://neko.internal") as client:
        response = await client.post(
            SETTINGS_PATH,
            content=_BODY,
            headers={**_JSON, "Origin": "https://public.example", "Sec-Fetch-Site": "same-origin"},
        )
    assert response.status_code == 200, response.text
    assert len(save_recorder.calls) == 1


@pytest.mark.asyncio
async def test_https_page_behind_tls_proxy_is_same_origin(app, save_recorder, monkeypatch):
    """TLS ends at a declared proxy that forwards plain HTTP with the same Host."""
    monkeypatch.setenv("NEKO_BEHIND_PROXY", "1")
    monkeypatch.delenv("NEKO_INSTANCE_PUBLIC_ORIGIN", raising=False)
    async with _client(app, base_url="http://neko.example") as client:
        ok = await client.post(
            SETTINGS_PATH,
            content=_BODY,
            headers={**_JSON, "Origin": "https://neko.example", "Sec-Fetch-Site": "same-origin"},
        )
        foreign = await client.post(
            SETTINGS_PATH,
            content=_BODY,
            headers={**_JSON, "Origin": "https://other.example"},
        )
    assert ok.status_code == 200, ok.text
    assert foreign.status_code == 403
    assert len(save_recorder.calls) == 1


def test_json_content_type_parser():
    assert config_shared._is_json_content_type("application/json")
    assert config_shared._is_json_content_type("Application/JSON; charset=UTF-8")
    assert not config_shared._is_json_content_type("text/plain")
    assert not config_shared._is_json_content_type("application/json-patch+json")
    assert not config_shared._is_json_content_type("")
    assert not config_shared._is_json_content_type(None)
