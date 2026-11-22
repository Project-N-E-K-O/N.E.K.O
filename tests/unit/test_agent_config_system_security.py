# -*- coding: utf-8 -*-
"""Security tests for agent, config, proxy, files, and steam proxy routes (F0014, F0199, F0201, B11-005, F0023)."""

from __future__ import annotations

import os
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from config import AUTOSTART_CSRF_TOKEN


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
            headers={"Origin": "https://evil.example", "X-CSRF-Token": AUTOSTART_CSRF_TOKEN},
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
                    "X-CSRF-Token": AUTOSTART_CSRF_TOKEN,
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
                "X-CSRF-Token": AUTOSTART_CSRF_TOKEN,
            },
        )
    assert resp.status_code == 400
    assert resp.json()["success"] is False


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
                headers={"Origin": "https://evil.example", "X-CSRF-Token": AUTOSTART_CSRF_TOKEN},
            )
            assert r_evil.status_code == 403

            r_plain = client.post(
                path,
                content='{"direct": true}',
                headers={
                    "Content-Type": "text/plain",
                    "Origin": "http://testserver",
                    "X-CSRF-Token": AUTOSTART_CSRF_TOKEN,
                },
            )
            assert r_plain.status_code == 415

            r_bad_json = client.post(
                path,
                content=b"{bad",
                headers={
                    "Content-Type": "application/json",
                    "Origin": "http://testserver",
                    "X-CSRF-Token": AUTOSTART_CSRF_TOKEN,
                },
            )
            assert r_bad_json.status_code == 400

        # Valid Origin + CSRF succeeds on /set_proxy_mode.
        r_ok = client.post(
            "/api/config/set_proxy_mode",
            json={"direct": False},
            headers={"Origin": "http://testserver", "X-CSRF-Token": AUTOSTART_CSRF_TOKEN},
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
                    "X-CSRF-Token": AUTOSTART_CSRF_TOKEN,
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
    symlink_escape.symlink_to(outside_file)

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
        r_sym = client.get("/api/file-exists", params={"path": str(symlink_escape)})
        assert r_sym.status_code == 403
        assert r_sym.json() == {"exists": False}

        # Null byte -> 400
        r_null = client.get("/api/file-exists", params={"path": f"{static_dir}/ok.txt\x00.png"})
        assert r_null.status_code == 400


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
    symlink_img.symlink_to(outside_secret)

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
        r_atk5 = client.get("/api/steam/proxy-image", params={"image_path": str(symlink_img)})
        assert r_atk5.status_code == 404

        # Non-image inside trusted workshop dir -> 400
        r_txt = client.get("/api/steam/proxy-image", params={"image_path": str(txt_file)})
        assert r_txt.status_code == 400
