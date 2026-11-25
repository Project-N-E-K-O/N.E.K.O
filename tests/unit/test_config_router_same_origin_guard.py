"""State-changing ``/api/config/*`` requests must be same-origin JSON.

The guard runs in the route class of ``config_router.router``. These tests
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
    body = response.json()
    assert body["success"] is False
    assert isinstance(body["error"], str) and body["error"]
    assert body["error_code"] == (
        "cross_origin_request_rejected" if status == 403 else "unsupported_content_type"
    )
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
    assert cross_site.json()["success"] is False
    assert not_json.status_code == 415
    assert not_json.json()["success"] is False


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "path", ["/api/config/test_connectivity", "/api/config/list_models"]
)
async def test_guard_runs_before_body_validation(app, path):
    """Routes with a Pydantic body must reject before parsing (no 422 first)."""
    async with _client(app) as client:
        cross_site = await client.post(
            path,
            content=b"not json",
            headers={"Content-Type": "text/plain", "Origin": "https://evil.example", "Sec-Fetch-Site": "cross-site"},
        )
        same_origin_bad_json = await client.post(
            path,
            content=b"not json",
            headers={**_JSON, "Origin": BASE_URL, "Sec-Fetch-Site": "same-origin"},
        )
    assert cross_site.status_code == 403
    assert same_origin_bad_json.status_code == 422


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
        # Local client naming the backend by a loopback alias (localhost vs
        # 127.0.0.1); no Fetch Metadata, as a browser would report cross-site.
        {**_JSON, "Origin": "http://127.0.0.1:48911"},
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
async def test_non_loopback_host_accepts_its_own_origin_only(app, save_recorder, monkeypatch):
    """On a non-loopback Host, Origin must equal the Host-derived origin."""
    monkeypatch.delenv("NEKO_INSTANCE_PUBLIC_ORIGIN", raising=False)
    base = "http://neko.example"
    async with _client(app, base_url=base) as client:
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
async def test_pinned_public_origin_is_same_origin(app, save_recorder, monkeypatch):
    """Behind a gateway the page origin is the pinned public origin, not the Host."""
    monkeypatch.setenv("NEKO_INSTANCE_PUBLIC_ORIGIN", "https://public.example")
    async with _client(app, base_url="http://neko.internal") as client:
        response = await client.post(
            SETTINGS_PATH,
            content=_BODY,
            headers={**_JSON, "Origin": "https://public.example", "Sec-Fetch-Site": "same-origin"},
        )
        # Windows that omit Origin still send Referer from the same page.
        referer_only = await client.post(
            SETTINGS_PATH,
            content=_BODY,
            headers={**_JSON, "Referer": "https://public.example/chat", "Sec-Fetch-Site": "same-origin"},
        )
        foreign_referer = await client.post(
            SETTINGS_PATH,
            content=_BODY,
            headers={**_JSON, "Referer": "https://evil.example/page"},
        )
        # An opaque Origin is never replaced by a same-origin Referer.
        null_origin = await client.post(
            SETTINGS_PATH,
            content=_BODY,
            headers={**_JSON, "Origin": "null", "Referer": "https://public.example/chat"},
        )
    assert response.status_code == 200, response.text
    assert referer_only.status_code == 200, referer_only.text
    assert foreign_referer.status_code == 403
    assert null_origin.status_code == 403
    assert len(save_recorder.calls) == 2


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
        # Electron windows with webSecurity disabled send Referer, not Origin.
        referer_only = await client.post(
            SETTINGS_PATH,
            content=_BODY,
            headers={**_JSON, "Referer": "https://neko.example/chat", "Sec-Fetch-Site": "same-origin"},
        )
    assert ok.status_code == 200, ok.text
    assert foreign.status_code == 403
    assert referer_only.status_code == 200, referer_only.text
    assert len(save_recorder.calls) == 2


@pytest.fixture
def csrf_token(monkeypatch):
    from main_routers.system_router import _shared as system_shared

    monkeypatch.setattr(system_shared, "AUTOSTART_CSRF_TOKEN", "csrf-test-token")
    monkeypatch.setattr(
        system_shared, "AUTOSTART_ALLOWED_ORIGINS", ["http://desktop-shell.example:9000"]
    )
    return "csrf-test-token"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "origin",
    [
        # Docker port mapping: same hostname, browser port differs from Host.
        "http://neko.lan:1081",
        # Extra origin configured in AUTOSTART_ALLOWED_ORIGINS.
        "http://desktop-shell.example:9000",
    ],
)
async def test_valid_csrf_keeps_local_mutation_origin_rules(app, save_recorder, csrf_token, origin):
    headers = {**_JSON, "Origin": origin}
    async with _client(app, base_url="http://neko.lan:48911") as client:
        without_token = await client.post(SETTINGS_PATH, content=_BODY, headers=headers)
        wrong_token = await client.post(
            SETTINGS_PATH, content=_BODY, headers={**headers, "X-CSRF-Token": "wrong"}
        )
        with_token = await client.post(
            SETTINGS_PATH, content=_BODY, headers={**headers, "X-CSRF-Token": csrf_token}
        )
        token_not_json = await client.post(
            SETTINGS_PATH,
            content=_BODY,
            headers={"Content-Type": "text/plain", "Origin": origin, "X-CSRF-Token": csrf_token},
        )
    assert without_token.status_code == 403
    assert wrong_token.status_code == 403
    assert with_token.status_code == 200, with_token.text
    assert token_not_json.status_code == 415
    assert len(save_recorder.calls) == 1


@pytest.mark.asyncio
async def test_valid_csrf_without_origin_logs_no_rejection(app, save_recorder, csrf_token, monkeypatch):
    """A token holder without Origin passes on the general rule, silently."""
    from main_routers.system_router import _shared as system_shared

    warnings = []
    for module_logger in (system_shared.logger, config_shared.logger):
        monkeypatch.setattr(module_logger, "warning", lambda *a, **k: warnings.append(a))
    async with _client(app) as client:
        response = await client.post(
            SETTINGS_PATH, content=_BODY, headers={**_JSON, "X-CSRF-Token": csrf_token}
        )
        # Loopback alias: accepted by the general rule, while the CSRF rule
        # alone would reject it (and warn) — it must not be consulted.
        alias = await client.post(
            SETTINGS_PATH,
            content=_BODY,
            headers={**_JSON, "X-CSRF-Token": csrf_token, "Origin": "http://127.0.0.1:48911"},
        )
        # Rejected without Origin: answered at debug level only.
        no_origin_cross_site = await client.post(
            SETTINGS_PATH,
            content=_BODY,
            headers={**_JSON, "X-CSRF-Token": csrf_token, "Sec-Fetch-Site": "cross-site"},
        )
    assert response.status_code == 200, response.text
    assert alias.status_code == 200, alias.text
    assert no_origin_cross_site.status_code == 403
    assert warnings == []
    assert len(save_recorder.calls) == 2


@pytest.mark.asyncio
async def test_non_ascii_csrf_token_is_rejected_not_500(app, save_recorder, csrf_token):
    async with _client(app, base_url="http://neko.lan:48911") as client:
        response = await client.post(
            SETTINGS_PATH,
            content=_BODY,
            headers=[
                ("Content-Type", "application/json"),
                ("Origin", "http://neko.lan:1081"),
                ("X-CSRF-Token", "tok\u00e9n".encode("latin-1")),
            ],
        )
    assert response.status_code == 403
    assert response.json()["success"] is False
    assert save_recorder.calls == []


@pytest.mark.asyncio
async def test_non_ascii_configured_csrf_token_is_not_500(app, save_recorder, monkeypatch):
    from main_routers.system_router import _shared as system_shared

    monkeypatch.setattr(system_shared, "AUTOSTART_CSRF_TOKEN", "t\u00f6ken")
    async with _client(app, base_url="http://neko.lan:48911") as client:
        response = await client.post(
            SETTINGS_PATH,
            content=_BODY,
            headers={**_JSON, "Origin": "http://neko.lan:1081", "X-CSRF-Token": "token"},
        )
    assert response.status_code == 403
    assert save_recorder.calls == []


@pytest.mark.asyncio
async def test_matching_non_ascii_csrf_token_is_not_500(app, save_recorder, monkeypatch):
    """Equal non-ASCII tokens must not reach a str compare_digest (TypeError)."""
    from main_routers.system_router import _shared as system_shared

    monkeypatch.setattr(system_shared, "AUTOSTART_CSRF_TOKEN", "t\u00f6ken")
    async with _client(app, base_url="http://neko.lan:48911") as client:
        response = await client.post(
            SETTINGS_PATH,
            content=_BODY,
            headers=[
                ("Content-Type", "application/json"),
                ("Origin", "http://neko.lan:1081"),
                ("X-CSRF-Token", "t\u00f6ken".encode("latin-1")),
            ],
        )
    assert response.status_code == 403
    assert response.json()["success"] is False
    assert save_recorder.calls == []


@pytest.mark.asyncio
async def test_valid_csrf_does_not_let_referer_replace_opaque_origin(app, save_recorder, csrf_token):
    """Origin "null" plus a Referer the CSRF rule would accept is still rejected."""
    async with _client(app, base_url="http://neko.lan:48911") as client:
        response = await client.post(
            SETTINGS_PATH,
            content=_BODY,
            headers={
                **_JSON,
                "Origin": "null",
                "Referer": "http://neko.lan:1081/page",
                "X-CSRF-Token": csrf_token,
            },
        )
    assert response.status_code == 403
    assert save_recorder.calls == []


@pytest.mark.asyncio
async def test_valid_csrf_does_not_admit_foreign_origin(app, save_recorder, csrf_token):
    async with _client(app, base_url="http://neko.lan:48911") as client:
        response = await client.post(
            SETTINGS_PATH,
            content=_BODY,
            headers={**_JSON, "Origin": "https://evil.example", "X-CSRF-Token": csrf_token},
        )
    assert response.status_code == 403
    assert save_recorder.calls == []


def test_json_content_type_parser():
    assert config_shared._is_json_content_type("application/json")
    assert config_shared._is_json_content_type("Application/JSON; charset=UTF-8")
    assert not config_shared._is_json_content_type("text/plain")
    assert not config_shared._is_json_content_type("application/json-patch+json")
    assert not config_shared._is_json_content_type("")
    assert not config_shared._is_json_content_type(None)
