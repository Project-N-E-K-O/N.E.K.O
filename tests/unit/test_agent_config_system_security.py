# -*- coding: utf-8 -*-
"""Security tests for agent, config, proxy, files, and steam proxy routes (F0014, F0199, F0201, B11-005, F0023)."""

from __future__ import annotations

import os
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
import httpx
from fastapi import FastAPI
from fastapi.testclient import TestClient


def _csrf_token() -> str:
    from main_routers.system_router import _shared as system_shared

    return system_shared.AUTOSTART_CSRF_TOKEN


pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# F0014: main_routers/agent_router.py
# ---------------------------------------------------------------------------

_AGENT_MUTATION_ROUTES = [
    ("/api/agent/flags", {"flags": {}}),
    ("/api/agent/command", {"command": "set_agent_enabled", "enabled": False}),
    ("/api/agent/tasks/task-1/cancel", None),
    ("/api/agent/builtin/stop", {"group": "browser"}),
    ("/api/agent/builtin/stop", None),
    ("/api/agent/builtin/browser/settings", {"mode": "own_profile"}),
    ("/api/agent/admin/control", {"action": "end_all"}),
]


@pytest.mark.parametrize("path,body", _AGENT_MUTATION_ROUTES)
def test_agent_mutations_reject_missing_csrf_and_cross_origin(monkeypatch, path, body):
    from main_routers import agent_router

    monkeypatch.delenv("NEKO_ACTIVITY_TRACKER_REMOTE", raising=False)
    monkeypatch.delenv("ACTIVITY_TRACKER_REMOTE", raising=False)
    app = FastAPI()
    app.include_router(agent_router.router)
    with TestClient(app) as client:
        r_no_auth = client.post(path, json=body) if body is not None else client.post(path)
        assert r_no_auth.status_code == 403
        assert r_no_auth.json()["error_code"] == "csrf_validation_failed"

        r_bad_origin = client.post(
            path,
            json=body,
            headers={"Origin": "https://evil.example", "X-CSRF-Token": _csrf_token()},
        )
        assert r_bad_origin.status_code == 403
        assert r_bad_origin.json()["error_code"] == "csrf_validation_failed"

        r_cross_port = client.post(
            path,
            json=body,
            headers={"Origin": "http://127.0.0.1:3000"},
        )
        assert r_cross_port.status_code == 403


def test_agent_mutations_reject_text_plain_cors_simple_request(monkeypatch):
    from main_routers import agent_router

    monkeypatch.delenv("NEKO_ACTIVITY_TRACKER_REMOTE", raising=False)
    app = FastAPI()
    app.include_router(agent_router.router)
    with TestClient(app) as client:
        for path in (
            "/api/agent/flags",
            "/api/agent/command",
            "/api/agent/builtin/stop",
            "/api/agent/builtin/browser/settings",
            "/api/agent/admin/control",
        ):
            resp = client.post(
                path,
                content='{"command": "set_agent_enabled"}',
                headers={
                    "Content-Type": "text/plain",
                    "Origin": "http://testserver",
                    "X-CSRF-Token": _csrf_token(),
                },
            )
            assert resp.status_code == 415


@pytest.mark.parametrize(
    "path",
    [
        "/api/agent/flags",
        "/api/agent/command",
        "/api/agent/tasks/task-1/cancel",
        "/api/agent/builtin/stop",
        "/api/agent/builtin/browser/settings",
        "/api/agent/admin/control",
    ],
)
def test_agent_mutations_reject_deeply_nested_json_without_crash(monkeypatch, path):
    from main_routers import agent_router

    monkeypatch.delenv("NEKO_ACTIVITY_TRACKER_REMOTE", raising=False)
    monkeypatch.delenv("ACTIVITY_TRACKER_REMOTE", raising=False)
    deep_json = ('{"a":' * 1500) + "1" + ("}" * 1500)

    app = FastAPI()
    app.include_router(agent_router.router)
    with TestClient(app) as client:
        resp = client.post(
            path,
            content=deep_json,
            headers={
                "Content-Type": "application/json",
                "Origin": "http://testserver",
                "X-CSRF-Token": _csrf_token(),
            },
        )
    assert resp.status_code == 400
    assert resp.json()["success"] is False


@pytest.mark.asyncio
async def test_agent_mutations_reject_multipart_and_unsupported_media_types_before_reading_body(monkeypatch):
    from types import SimpleNamespace
    from main_routers import agent_router

    monkeypatch.delenv("NEKO_ACTIVITY_TRACKER_REMOTE", raising=False)
    monkeypatch.delenv("ACTIVITY_TRACKER_REMOTE", raising=False)

    app = FastAPI()
    app.include_router(agent_router.router)

    async def _send_raw_asgi(path: str, headers: list[tuple[bytes, bytes]]) -> tuple[int, bool]:
        body_read_called = False
        status_code = 0

        async def _bomb_receive():
            nonlocal body_read_called
            body_read_called = True
            return {"type": "http.request", "body": b"should-never-be-read", "more_body": False}

        async def _capture_send(message):
            nonlocal status_code
            if message["type"] == "http.response.start":
                status_code = message["status"]

        scope = {
            "type": "http",
            "asgi": {"version": "3.0"},
            "http_version": "1.1",
            "method": "POST",
            "scheme": "http",
            "path": path,
            "raw_path": path.encode("ascii"),
            "query_string": b"",
            "headers": headers,
            "client": ("127.0.0.1", 50000),
            "server": ("127.0.0.1", 48911),
        }
        await app(scope, _bomb_receive, _capture_send)
        return status_code, body_read_called

    base_headers = [
        (b"host", b"127.0.0.1:48911"),
        (b"origin", b"http://127.0.0.1:48911"),
        (b"x-csrf-token", _csrf_token().encode("ascii")),
    ]
    for path in (
        "/api/agent/tasks/task-1/cancel",
        "/api/agent/builtin/stop",
        "/api/agent/command",
    ):
        status, read_called = await _send_raw_asgi(
            path,
            base_headers + [(b"content-type", b"multipart/form-data; boundary=---")],
        )
        assert status == 415
        assert read_called is False

    status_plain, read_plain = await _send_raw_asgi(
        "/api/agent/tasks/task-1/cancel",
        base_headers + [(b"content-type", b"text/plain")],
    )
    assert status_plain == 415
    assert read_plain is False

    status_cl, read_cl = await _send_raw_asgi(
        "/api/agent/tasks/task-1/cancel",
        base_headers + [(b"content-length", b"1024")],
    )
    assert status_cl == 415
    assert read_cl is False

    # Bodyless POST to /cancel with valid same-origin + CSRF passes the guard.
    fake_client = SimpleNamespace(
        post=AsyncMock(return_value=SimpleNamespace(is_success=True, status_code=200, json=lambda: {"success": True}))
    )
    monkeypatch.setattr(agent_router, "_get_http_client", lambda: fake_client)
    status_ok, read_ok = await _send_raw_asgi("/api/agent/tasks/task-1/cancel", base_headers)
    assert status_ok == 200
    assert read_ok is False
    assert fake_client.post.await_count == 1


@pytest.mark.asyncio
async def test_console_proxy_set_switch_passes_real_agent_router_csrf_guard(monkeypatch):
    """End-to-end unmocked CSRF check from plugin console_proxy to main agent_router."""
    from config.network import USER_PLUGIN_SERVER_PORT
    from main_routers import agent_router
    from main_routers.system_router import _shared as system_shared
    from plugin.server.infrastructure import mutation_auth
    from plugin.server.routes import console_proxy

    monkeypatch.delenv("NEKO_ACTIVITY_TRACKER_REMOTE", raising=False)
    monkeypatch.delenv("ACTIVITY_TRACKER_REMOTE", raising=False)
    # Keep both routers aligned to the active session token in case an earlier test reloaded config.
    monkeypatch.setattr(mutation_auth, "AUTOSTART_CSRF_TOKEN", system_shared.AUTOSTART_CSRF_TOKEN)

    forwarded_commands: list[dict] = []

    class _FakeToolClient:
        async def post(self, url, *, json=None, timeout=None):
            forwarded_commands.append({"url": url, "json": json, "timeout": timeout})
            return httpx.Response(200, json={"success": True, "request_id": json.get("request_id")})

    monkeypatch.setattr(agent_router, "get_session_manager", lambda: {})
    monkeypatch.setattr(
        agent_router,
        "get_config_manager",
        lambda: SimpleNamespace(
            get_character_data=lambda: (None, "Lan", None, None, None, None, None, None, None),
            is_agent_free=lambda: False,
        ),
    )
    monkeypatch.setattr(agent_router, "_get_http_client", lambda: _FakeToolClient())

    main_app = FastAPI()
    main_app.include_router(agent_router.router)
    plugin_app = FastAPI()
    plugin_app.include_router(console_proxy.router)

    plugin_origin = f"http://127.0.0.1:{USER_PLUGIN_SERVER_PORT}"
    headers = {
        "Origin": plugin_origin,
        "X-CSRF-Token": mutation_auth.AUTOSTART_CSRF_TOKEN,
    }

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=main_app)) as internal_client:
        monkeypatch.setattr(console_proxy, "_http", lambda: internal_client)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=plugin_app, client=("127.0.0.1", 1234)),
            base_url=plugin_origin,
        ) as ui_client:
            r1 = await ui_client.post("/console/switch", json={"enabled": False}, headers=headers)
            assert r1.status_code == 200 and r1.json() == {"success": True}

            r2 = await ui_client.post(
                "/console/switch",
                json={"key": "browser_use_enabled", "value": True},
                headers=headers,
            )
            assert r2.status_code == 200 and r2.json() == {"success": True}

    assert [cmd["json"]["command"] for cmd in forwarded_commands] == ["set_agent_enabled", "set_flag"]
    assert forwarded_commands[0]["json"]["enabled"] is False
    assert forwarded_commands[1]["json"]["key"] == "browser_use_enabled"
    assert forwarded_commands[1]["json"]["value"] is True


# ---------------------------------------------------------------------------
# F0199: core_config.py & proxy.py CSRF + Content-Type
# ---------------------------------------------------------------------------

def test_core_api_and_proxy_mode_enforce_csrf_and_json_content_type(monkeypatch):
    from main_routers.config_router import core_config, proxy
    from main_routers import config_router

    app = FastAPI()
    app.include_router(config_router.router)

    # Spy on lock acquire to ensure unauthenticated calls reject before acquiring.
    acquired = False
    orig_acquire = core_config._core_api_save_lock.acquire

    async def spy_acquire():
        nonlocal acquired
        acquired = True
        return await orig_acquire()

    monkeypatch.setattr(core_config._core_api_save_lock, "acquire", spy_acquire)

    env_before = dict(os.environ)
    with TestClient(app) as client:
        for path, payload in (
            ("/api/config/core_api", {"coreApi": "free"}),
            ("/api/config/set_proxy_mode", {"direct": True}),
        ):
            r_unauth = client.post(path, json=payload)
            assert r_unauth.status_code == 403
            assert r_unauth.json()["error_code"] == "csrf_validation_failed"

            r_evil = client.post(
                path,
                json=payload,
                headers={"Origin": "https://evil.example", "X-CSRF-Token": _csrf_token()},
            )
            assert r_evil.status_code == 403

            r_plain = client.post(
                path,
                content='{"direct": true}',
                headers={
                    "Content-Type": "text/plain",
                    "Origin": "http://testserver",
                    "X-CSRF-Token": _csrf_token(),
                },
            )
            assert r_plain.status_code == 415

            r_bad_json = client.post(
                path,
                content=b"{bad",
                headers={
                    "Content-Type": "application/json",
                    "Origin": "http://testserver",
                    "X-CSRF-Token": _csrf_token(),
                },
            )
            assert r_bad_json.status_code == 400

        # Valid Origin + CSRF succeeds on /set_proxy_mode.
        r_ok = client.post(
            "/api/config/set_proxy_mode",
            json={"direct": False},
            headers={"Origin": "http://testserver", "X-CSRF-Token": _csrf_token()},
        )
        assert r_ok.status_code == 200
        assert r_ok.json()["success"] is True

    assert acquired is False
    assert proxy._proxy_snapshot == {} or isinstance(proxy._proxy_snapshot, dict)
    for k in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "NO_PROXY"):
        assert os.environ.get(k) == env_before.get(k)


def test_core_api_and_proxy_mode_reject_deeply_nested_json_with_400(monkeypatch):
    from main_routers.config_router import core_config
    from main_routers import config_router

    app = FastAPI()
    app.include_router(config_router.router)

    acquired = False
    orig_acquire = core_config._core_api_save_lock.acquire

    async def spy_acquire():
        nonlocal acquired
        acquired = True
        return await orig_acquire()

    monkeypatch.setattr(core_config._core_api_save_lock, "acquire", spy_acquire)
    env_before = dict(os.environ)
    deep_json = ('{"a":' * 1500) + "1" + ("}" * 1500)

    with TestClient(app) as client:
        for path in ("/api/config/core_api", "/api/config/set_proxy_mode"):
            resp = client.post(
                path,
                content=deep_json,
                headers={
                    "Content-Type": "application/json",
                    "Origin": "http://testserver",
                    "X-CSRF-Token": _csrf_token(),
                },
            )
            assert resp.status_code == 400
            assert resp.json()["success"] is False

    assert acquired is False
    for k in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "NO_PROXY"):
        assert os.environ.get(k) == env_before.get(k)


# ---------------------------------------------------------------------------
# F0201: _sanitize_proxies credential redaction
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "raw_url,expected,forbidden",
    [
        ("http://user:pass@proxy.example:8080", "http://proxy.example:8080", ("user", "pass", "@")),
        ("user:pass@proxy.example:8080", "proxy.example:8080", ("user", "pass", "@")),
        ("user@proxy.example:8080", "proxy.example:8080", ("user", "@")),
        ("http://:secret@proxy.example:8080", "http://proxy.example:8080", ("secret", "@")),
        ("http://:@proxy.example:8080", "http://proxy.example:8080", ("@",)),
        ("socks5://user:pass@[::1]:1080", "socks5://[::1]:1080", ("user", "pass", "@")),
        ("http://proxy.example:8080", "http://proxy.example:8080", ("@",)),
        ("not a valid @@@ url/[::1", "<redacted>", ("not a valid", "@")),
    ],
)
def test_sanitize_proxies_redacts_credentials_including_schemeless(raw_url, expected, forbidden):
    from main_routers.config_router.proxy import _sanitize_proxies

    sanitized = _sanitize_proxies({"http": raw_url})
    assert sanitized["http"] == expected
    for token in forbidden:
        assert token not in sanitized["http"]


def test_sanitize_proxies_handles_non_string_values():
    from main_routers.config_router.proxy import _sanitize_proxies

    assert _sanitize_proxies({"http": 12345}) == {"http": "<redacted>"}


# ---------------------------------------------------------------------------
# B11-005: main_routers/system_router/files.py (/api/file-exists)
# ---------------------------------------------------------------------------

def test_file_exists_enforces_trusted_roots_and_blocks_traversal(tmp_path, monkeypatch):
    from main_routers import system_router
    from main_routers.system_router import files as files_mod

    app_root = tmp_path / "app"
    static_dir = app_root / "static"
    assets_dir = app_root / "assets"
    docs_dir = tmp_path / "docs"
    workshop_dir = tmp_path / "workshop"
    secondary_workshop_dir = tmp_path / "secondary_workshop"
    outside_dir = tmp_path / "outside"
    sibling_dir = tmp_path / "static-evil"
    for d in (static_dir, assets_dir, docs_dir, workshop_dir, secondary_workshop_dir, outside_dir, sibling_dir):
        d.mkdir(parents=True)

    valid_file = static_dir / "ok.txt"
    valid_file.write_text("ok", encoding="utf-8")
    docs_file = docs_dir / "card.json"
    docs_file.write_text("{}", encoding="utf-8")
    secondary_workshop_file = secondary_workshop_dir / "12345" / "model.json"
    secondary_workshop_file.parent.mkdir(parents=True)
    secondary_workshop_file.write_text("{}", encoding="utf-8")
    outside_file = outside_dir / "secret.txt"
    outside_file.write_text("secret", encoding="utf-8")
    sibling_file = sibling_dir / "evil.txt"
    sibling_file.write_text("evil", encoding="utf-8")

    symlink_escape = static_dir / "escape.txt"
    symlink_created = False
    try:
        symlink_escape.symlink_to(outside_file)
        symlink_created = True
    except (OSError, NotImplementedError):
        pass

    monkeypatch.setattr(files_mod, "_get_app_root", lambda: str(app_root))
    monkeypatch.setattr(files_mod, "get_workshop_path_async", AsyncMock(return_value=str(workshop_dir)))
    monkeypatch.setattr(files_mod, "get_workshop_library_roots", lambda: [str(secondary_workshop_dir)])
    monkeypatch.setattr(
        files_mod,
        "_get_system_config_manager",
        lambda: SimpleNamespace(docs_dir=docs_dir, app_docs_dir=None, original_docs_dir=None),
    )

    app = FastAPI()
    app.include_router(system_router.router)
    with TestClient(app) as client:
        assert client.get("/api/file-exists", params={"path": str(valid_file)}).json() == {"exists": True}
        assert client.get("/api/file-exists", params={"path": str(docs_file)}).json() == {"exists": True}
        assert client.get("/api/file-exists", params={"path": str(secondary_workshop_file)}).json() == {"exists": True}
        assert client.get("/api/file-exists", params={"path": str(static_dir / "missing.txt")}).json() == {"exists": False}

        # Outside root -> 403 oracle blocked
        r_outside = client.get("/api/file-exists", params={"path": str(outside_file)})
        assert r_outside.status_code == 403
        assert r_outside.json() == {"exists": False}

        # .. traversal -> 400
        r_trav = client.get("/api/file-exists", params={"path": f"{static_dir}/../outside/secret.txt"})
        assert r_trav.status_code == 400
        assert r_trav.json() == {"exists": False}

        # URL-encoded traversal -> 400
        r_enc = client.get("/api/file-exists", params={"path": f"{static_dir}/%2e%2e/outside/secret.txt"})
        assert r_enc.status_code == 400
        assert r_enc.json() == {"exists": False}

        # Sibling prefix -> 403
        r_sib = client.get("/api/file-exists", params={"path": str(sibling_file)})
        assert r_sib.status_code == 403
        assert r_sib.json() == {"exists": False}

        # Symlink escaping static -> 403
        if symlink_created:
            r_sym = client.get("/api/file-exists", params={"path": str(symlink_escape)})
            assert r_sym.status_code == 403
            assert r_sym.json() == {"exists": False}

        # Null byte -> 400
        r_null = client.get("/api/file-exists", params={"path": f"{static_dir}/ok.txt\x00.png"})
        assert r_null.status_code == 400


@pytest.mark.parametrize(
    "unc_or_device_path",
    [
        r"\\server\share\a.png",
        "//server/share/a.png",
        r"\/server\share\a.png",
        r"/\server/share/a.png",
        r"\\?\C:\Windows\win.ini",
        r"\??\C:\Windows\win.ini",
    ],
)
def test_file_exists_rejects_unc_and_device_paths_before_realpath(tmp_path, monkeypatch, unc_or_device_path):
    from main_routers import system_router
    from main_routers.system_router import files as files_mod

    realpath_calls: list[str] = []
    real_realpath = os.path.realpath

    def _spy_realpath(p):
        realpath_calls.append(str(p))
        return real_realpath(p)

    monkeypatch.setattr(files_mod, "_get_file_exists_allowed_dirs", AsyncMock(return_value=[str(tmp_path)]))
    monkeypatch.setattr(files_mod.os.path, "realpath", _spy_realpath)
    app = FastAPI()
    app.include_router(system_router.router)
    with TestClient(app) as client:
        resp = client.get("/api/file-exists", params={"path": unc_or_device_path})
    assert resp.status_code == 403
    assert resp.json() == {"exists": False}
    assert realpath_calls == []


@pytest.mark.parametrize(
    "device_path",
    [
        r"\\?\C:\Windows\win.ini",
        r"\\.\PhysicalDrive0",
        r"\??\C:\Windows\win.ini",
    ],
)
def test_file_exists_allows_unc_when_allowed_dir_is_unc_but_still_blocks_device_paths(monkeypatch, device_path):
    from main_routers import system_router
    from main_routers.system_router import files as files_mod

    unc_root = r"\\fileserver\share\docs"
    realpath_calls: list[str] = []
    real_realpath = os.path.realpath

    def _spy_realpath(p):
        realpath_calls.append(str(p))
        return real_realpath(p)

    monkeypatch.setattr(files_mod, "_get_file_exists_allowed_dirs", AsyncMock(return_value=[unc_root]))
    monkeypatch.setattr(files_mod.os.path, "realpath", _spy_realpath)
    app = FastAPI()
    app.include_router(system_router.router)
    with TestClient(app) as client:
        r_device = client.get("/api/file-exists", params={"path": device_path})
        assert r_device.status_code == 403
        assert realpath_calls == []

        r_foreign_unc = client.get("/api/file-exists", params={"path": r"\\attacker\other\secret.txt"})
        assert r_foreign_unc.status_code == 403
        assert realpath_calls == []

        r_unc = client.get("/api/file-exists", params={"path": r"\\fileserver\share\docs\ok.txt"})
        assert realpath_calls and "fileserver" in realpath_calls[0], (
            "UNC path under an allowed UNC root should not be blocked before realpath"
        )
        assert r_unc.status_code in (200, 403)


# ---------------------------------------------------------------------------
# F0023: main_routers/system_router/steam.py (/api/steam/proxy-image)
# ---------------------------------------------------------------------------

def test_steam_proxy_image_blocks_workshop_substring_bypass_and_traversal(tmp_path, monkeypatch):
    from main_routers import system_router
    from main_routers.system_router import steam as steam_mod

    app_root = tmp_path / "app"
    static_dir = app_root / "static"
    assets_dir = app_root / "assets"
    workshop_dir = tmp_path / "trusted_workshop"
    secondary_workshop_dir = tmp_path / "secondary_workshop"
    outside_root = tmp_path / "outside"
    fake_workshop = outside_root / "steamapps" / "workshop" / "content"
    sibling_workshop = tmp_path / "trusted_workshop-evil"

    for d in (static_dir, assets_dir, workshop_dir, secondary_workshop_dir, fake_workshop, sibling_workshop):
        d.mkdir(parents=True)

    valid_img = workshop_dir / "preview.png"
    valid_img.write_bytes(b"\x89PNG\r\n\x1a\nok")
    secondary_img = secondary_workshop_dir / "item_2" / "thumb.png"
    secondary_img.parent.mkdir(parents=True)
    secondary_img.write_bytes(b"\x89PNG\r\n\x1a\nsecondary")
    static_img = static_dir / "logo.png"
    static_img.write_bytes(b"\x89PNG\r\n\x1a\nstatic")
    txt_file = workshop_dir / "note.txt"
    txt_file.write_text("not-an-image", encoding="utf-8")

    outside_secret = tmp_path / "secret.png"
    outside_secret.write_bytes(b"\x89PNG\r\n\x1a\nSECRET")
    outside_workshop_img = fake_workshop / "trap.png"
    outside_workshop_img.write_bytes(b"\x89PNG\r\n\x1a\nTRAP")
    sibling_img = sibling_workshop / "pic.png"
    sibling_img.write_bytes(b"\x89PNG\r\n\x1a\nSIB")

    symlink_img = workshop_dir / "link.png"
    symlink_created = False
    try:
        symlink_img.symlink_to(outside_secret)
        symlink_created = True
    except (OSError, NotImplementedError):
        pass

    monkeypatch.setattr(steam_mod, "_get_app_root", lambda: str(app_root))
    monkeypatch.setattr(steam_mod, "get_workshop_path_async", AsyncMock(return_value=str(workshop_dir)))
    monkeypatch.setattr(steam_mod, "get_workshop_library_roots", lambda: [str(secondary_workshop_dir)])

    app = FastAPI()
    app.include_router(system_router.router)
    with TestClient(app) as client:
        # Allowed paths work
        r1 = client.get("/api/steam/proxy-image", params={"image_path": str(valid_img)})
        assert r1.status_code == 200 and r1.content == valid_img.read_bytes()

        r_sec = client.get("/api/steam/proxy-image", params={"image_path": str(secondary_img)})
        assert r_sec.status_code == 200 and r_sec.content == secondary_img.read_bytes()

        r2 = client.get("/api/steam/proxy-image", params={"image_path": "preview.png"})
        assert r2.status_code == 200 and r2.content == valid_img.read_bytes()

        r3 = client.get("/api/steam/proxy-image", params={"image_path": "../static/logo.png"})
        assert r3.status_code == 200 and r3.content == static_img.read_bytes()

        # Attack: steamapps/workshop substring in untrusted dir + .. traversal
        r_atk1 = client.get(
            "/api/steam/proxy-image",
            params={"image_path": f"{fake_workshop}/../../../secret.png"},
        )
        assert r_atk1.status_code == 404
        assert str(tmp_path) not in r_atk1.text

        # Attack: direct file inside untrusted steamapps/workshop path
        r_atk2 = client.get("/api/steam/proxy-image", params={"image_path": str(outside_workshop_img)})
        assert r_atk2.status_code == 404

        # Attack: .. traversal from trusted workshop dir
        r_atk3 = client.get("/api/steam/proxy-image", params={"image_path": f"{workshop_dir}/../secret.png"})
        assert r_atk3.status_code == 404

        # Attack: sibling prefix dir
        r_atk4 = client.get("/api/steam/proxy-image", params={"image_path": str(sibling_img)})
        assert r_atk4.status_code == 404

        # Attack: symlink escaping workshop dir
        if symlink_created:
            r_atk5 = client.get("/api/steam/proxy-image", params={"image_path": str(symlink_img)})
            assert r_atk5.status_code == 404

        # Non-image inside trusted workshop dir -> 400
        r_txt = client.get("/api/steam/proxy-image", params={"image_path": str(txt_file)})
        assert r_txt.status_code == 400


@pytest.mark.parametrize(
    "unsafe_path",
    [
        r"\\server\share\a.png",
        "//server/share/a.png",
        r"\/server\share\a.png",
        r"/\server/share/a.png",
        r"\\?\C:\Windows\win.ini",
        r"\??\C:\Windows\win.ini",
        "../static///server/share/a.png",
        r"../static/\/server/share/a.png",
        r"../static/C:\Windows\win.ini",
    ],
)
def test_steam_proxy_image_rejects_unc_and_rooted_static_suffix_before_realpath(tmp_path, monkeypatch, unsafe_path):
    from main_routers import system_router
    from main_routers.system_router import steam as steam_mod

    app_root = tmp_path / "app"
    static_dir = app_root / "static"
    assets_dir = app_root / "assets"
    workshop_dir = tmp_path / "workshop"
    for d in (static_dir, assets_dir, workshop_dir):
        d.mkdir(parents=True)

    realpath_calls: list[str] = []
    real_realpath = os.path.realpath

    def _spy_realpath(p):
        realpath_calls.append(str(p))
        return real_realpath(p)

    monkeypatch.setattr(steam_mod, "_get_app_root", lambda: str(app_root))
    monkeypatch.setattr(steam_mod, "get_workshop_path_async", AsyncMock(return_value=str(workshop_dir)))
    monkeypatch.setattr(steam_mod, "get_workshop_library_roots", lambda: [])
    monkeypatch.setattr(steam_mod.os.path, "realpath", _spy_realpath)

    app = FastAPI()
    app.include_router(system_router.router)
    with TestClient(app) as client:
        resp = client.get("/api/steam/proxy-image", params={"image_path": unsafe_path})

    assert resp.status_code == 404
    allowed_resolved = {
        str(real_realpath(static_dir)),
        str(real_realpath(assets_dir)),
        str(real_realpath(workshop_dir)),
    }
    assert all(call in allowed_resolved for call in realpath_calls), realpath_calls


def test_steam_proxy_image_enforces_bounded_read_on_post_stat_growth(tmp_path, monkeypatch):
    from main_routers import system_router
    from main_routers.system_router import steam as steam_mod

    bounded_file = tmp_path / "sample.bin"
    bounded_file.write_bytes(b"123456")
    assert steam_mod._read_binary_file(str(bounded_file), max_size=6) == b"123456"
    assert steam_mod._read_binary_file(str(bounded_file), max_size=5) is None

    app_root = tmp_path / "app"
    static_dir = app_root / "static"
    assets_dir = app_root / "assets"
    workshop_dir = tmp_path / "workshop"
    for d in (static_dir, assets_dir, workshop_dir):
        d.mkdir(parents=True)
    img_path = workshop_dir / "growing.png"
    img_path.write_bytes(b"\x89PNG\r\n\x1a\nok")

    monkeypatch.setattr(steam_mod, "_get_app_root", lambda: str(app_root))
    monkeypatch.setattr(steam_mod, "get_workshop_path_async", AsyncMock(return_value=str(workshop_dir)))
    monkeypatch.setattr(steam_mod, "get_workshop_library_roots", lambda: [])
    monkeypatch.setattr(steam_mod.os.path, "getsize", lambda _p: 100)
    monkeypatch.setattr(steam_mod, "_read_binary_file", lambda _p, max_size=None: None)

    app = FastAPI()
    app.include_router(system_router.router)
    with TestClient(app) as client:
        resp = client.get("/api/steam/proxy-image", params={"image_path": str(img_path)})
    assert resp.status_code == 413
    assert resp.json()["success"] is False


@pytest.mark.asyncio
async def test_plugin_console_switch_passes_real_main_server_middleware_stack_behind_proxy(monkeypatch):
    import json

    import config
    from app import main_server
    from fastapi import FastAPI
    from main_routers import agent_router
    from main_routers.system_router import _shared as system_shared
    from plugin.server.infrastructure import mutation_auth
    from plugin.server.routes import console_proxy
    from utils.host_origin_guard import HostOriginGuardMiddleware

    monkeypatch.setenv("NEKO_BEHIND_PROXY", "true")
    monkeypatch.delenv("NEKO_ACTIVITY_TRACKER_REMOTE", raising=False)
    monkeypatch.delenv("ACTIVITY_TRACKER_REMOTE", raising=False)
    monkeypatch.setattr(system_shared, "AUTOSTART_CSRF_TOKEN", mutation_auth.AUTOSTART_CSRF_TOKEN)
    monkeypatch.setattr(console_proxy, "_remote", lambda: False)
    monkeypatch.setattr(agent_router, "get_session_manager", dict)

    class _StubCfg:
        @staticmethod
        def get_character_data():
            return ("", "", "", "", "", "", "", "", "")

        @staticmethod
        def is_agent_free():
            return False

    monkeypatch.setattr(agent_router, "get_config_manager", _StubCfg)

    tool_requests: list[httpx.Request] = []

    def _tool_handler(request: httpx.Request) -> httpx.Response:
        tool_requests.append(request)
        return httpx.Response(200, json={"success": True})

    tool_client = httpx.AsyncClient(transport=httpx.MockTransport(_tool_handler))
    monkeypatch.setattr(agent_router, "_get_http_client", lambda: tool_client)

    main_base = f"http://127.0.0.1:{config.MAIN_SERVER_PORT}"
    # Exercise real app.main_server.app without entering lifespan context to avoid leaking global hooks.
    main_client = httpx.AsyncClient(
        transport=httpx.ASGITransport(app=main_server.app, client=("127.0.0.1", 54321)),
        base_url=main_base,
    )
    monkeypatch.setattr(console_proxy, "_http", lambda: main_client)

    plugin_app = FastAPI()
    plugin_app.include_router(console_proxy.router)
    plugin_app.add_middleware(HostOriginGuardMiddleware)
    plugin_page = "http://127.0.0.1:48916"
    plugin_client = httpx.AsyncClient(
        transport=httpx.ASGITransport(app=plugin_app, client=("127.0.0.1", 1234)),
        base_url=plugin_page,
        headers={"Origin": plugin_page, "X-CSRF-Token": mutation_auth.AUTOSTART_CSRF_TOKEN},
    )

    try:
        resp = await plugin_client.post("/console/switch", json={"enabled": True})
        assert resp.status_code == 200 and resp.json() == {"success": True}
        assert len(tool_requests) == 1
        assert json.loads(tool_requests[0].content) == {
            "command": "set_agent_enabled",
            "enabled": True,
            "lanlan_name": "",
        }

        # Control check: Origin triggers InstanceAccessMiddleware 401 when NEKO_BEHIND_PROXY=true.
        rejected = await main_client.post(
            "/api/agent/command",
            json={"command": "set_agent_enabled", "enabled": True},
            headers={"Origin": main_base, "X-CSRF-Token": mutation_auth.AUTOSTART_CSRF_TOKEN},
        )
        assert rejected.status_code == 401
        assert "instance_authorization_required" in rejected.text
    finally:
        await plugin_client.aclose()
        await main_client.aclose()
        await tool_client.aclose()
