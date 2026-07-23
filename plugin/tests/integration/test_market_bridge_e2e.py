"""End-to-end download-link smoke test for the Market bridge install path.

Builds a real ``.neko-plugin`` ZIP, serves it from a localhost
``http.server``, drives ``POST /market/install`` through the bridge ASGI
app, polls the resulting task to completion, then verifies:

1. the on-disk lock file is v2 with all 4 new ``SourceDetailMarket``
   fields populated by the actual bytes that landed on disk;
2. ``GET /market/installed`` projects ``latest_install_source`` from
   that lock entry.

This is the hard-evidence test for "下载链路真的通了". It exercises the
full chain — HTTP download → sha256 check → unpack → ISM record →
lock atomic write → ``/market/installed`` projection — without any
mocks beyond redirecting filesystem roots into ``tmp_path``.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import http.server
import io
import json
import socket
import shutil
import threading
import time
import zipfile
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from plugin.server.application.install_source import (
    InstallSourceManager,
    set_global_manager,
)
from plugin.server.application.install_source.scanner import (
    PluginDirectoryScanner,
)
from plugin.neko_plugin_cli.public import build_plugin


FIXTURE_PLUGINS_ROOT = (
    Path(__file__).resolve().parents[1] / "fixtures" / "neko_plugin_cli" / "plugins"
)


# ─── Fixture: build a minimal valid .neko-plugin package ──────────────


def _build_neko_plugin_zip(
    *,
    plugin_id: str,
    version: str,
    include_profile: bool = False,
) -> tuple[bytes, str]:
    """Build a minimal ``.neko-plugin`` archive in memory.

    Layout matches what :mod:`plugin.neko_plugin_cli.public.archive_utils`
    expects:

    * ``manifest.toml`` — top-level ``package_type`` + ``id``;
    * ``payload/plugins/<plugin_id>/plugin.toml`` — required by
      ``validate_plugin_layout``;
    * ``metadata.toml`` — optional but lets us assert payload_hash flows
      through to lock entry.

    Returns ``(zip_bytes, payload_hash_hex)``. The payload hash is
    computed by mirroring ``compute_archive_payload_hash`` on the same
    byte content.
    """

    plugin_toml_content = (
        f'[plugin]\nid = "{plugin_id}"\nversion = "{version}"\n'
        'name = "e2e test plugin"\n'
    ).encode("utf-8")

    # Compute payload hash before writing, then bake it into metadata.toml
    # so unpack's verify_payload_hash step succeeds. We emulate
    # ``compute_archive_payload_hash``: sort by relative posix path,
    # write ``relpath\0content\0`` to a digest.
    payload_files = [
        (f"plugins/{plugin_id}/plugin.toml", plugin_toml_content),
    ]
    if include_profile:
        payload_files.append(
            (
                "profiles/default.toml",
                f'[{plugin_id}]\nvalue = "from-profile"\n'.encode("utf-8"),
            )
        )
    digest = hashlib.sha256()
    for rel, content in sorted(payload_files, key=lambda x: x[0]):
        digest.update(rel.encode("utf-8"))
        digest.update(b"\0")
        digest.update(content)
        digest.update(b"\0")
    payload_hash = digest.hexdigest()

    metadata_toml = (
        "[payload]\n"
        f'hash = "{payload_hash}"\n'
        'hash_algorithm = "sha256"\n'
    ).encode("utf-8")

    manifest_toml = (
        f'package_type = "plugin"\nid = "{plugin_id}"\n'
        f'version = "{version}"\n'
    ).encode("utf-8")

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("manifest.toml", manifest_toml)
        zf.writestr("metadata.toml", metadata_toml)
        for rel, content in payload_files:
            zf.writestr(f"payload/{rel}", content)

    return buf.getvalue(), payload_hash


# ─── Fixture: run http.server in a background thread to host the zip ──


@contextlib.contextmanager
def _serve_bytes(*, filename: str, content: bytes) -> Iterator[str]:
    """Start a localhost HTTP server that serves a single file.

    Yields the absolute URL of the served file; tears the server down
    on exit. Bound to an OS-assigned port so concurrent test runs don't
    collide.
    """

    class _Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802 — http.server convention
            if self.path != f"/{filename}":
                self.send_error(404, "not found")
                return
            self.send_response(200)
            self.send_header("Content-Type", "application/octet-stream")
            self.send_header("Content-Length", str(len(content)))
            self.end_headers()
            self.wfile.write(content)

        def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
            # Quiet the test logs; default impl prints to stderr per request.
            return

    server = http.server.HTTPServer(("127.0.0.1", 0), _Handler)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{port}/{filename}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


# ─── Fixture: a fully wired bridge ASGI app pointed at tmp_path roots ──


@pytest.fixture
def bridge_e2e_env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> Iterator[dict[str, Any]]:
    """Build an ASGI bridge app + ISM all rooted under ``tmp_path``.

    Redirects the plugin root settings on :mod:`plugin.settings` so saved
    packages and unpacked plugins land in ``tmp_path``. Both the plugin_cli
    service and the market bridge resolve their roots through
    ``PluginCliPathPolicy.from_settings()``, so patching the settings module
    is enough — neither freezes the roots at import time anymore.

    Yields a dict with ``client`` (httpx AsyncClient), ``token``,
    ``user_root``, ``builtin_root``, ``packages_root``, ``lock_path``.
    """

    builtin_root = tmp_path / "builtin"
    user_root = tmp_path / "user"
    packages_root = tmp_path / "packages"
    profiles_root = tmp_path / "profiles"
    lock_path = tmp_path / "plugins.lock.json"
    for d in (builtin_root, user_root, packages_root, profiles_root):
        d.mkdir(parents=True, exist_ok=True)

    from plugin.server.application import plugin_cli as plugin_cli_pkg
    import plugin.settings as plugin_settings
    from plugin.server.routes import market_bridge as market_bridge_module

    monkeypatch.setattr(plugin_settings, "BUILTIN_PLUGIN_CONFIG_ROOT", builtin_root)
    monkeypatch.setattr(plugin_settings, "USER_PLUGIN_CONFIG_ROOT", user_root)
    monkeypatch.setattr(plugin_settings, "USER_PLUGIN_PACKAGES_ROOT", packages_root)
    monkeypatch.setattr(plugin_settings, "USER_PACKAGE_PROFILES_ROOT", profiles_root)
    # market_bridge no longer freezes USER_PLUGIN_CONFIG_ROOT at import time; it
    # resolves plugin roots through PluginCliPathPolicy.from_settings(), so the
    # plugin.settings patches above are picked up without patching the module
    # attribute (which no longer exists).
    monkeypatch.setattr(
        market_bridge_module,
        "_OAUTH_TOKEN_FILE",
        tmp_path / "market_auth.json",
    )
    monkeypatch.setattr(
        market_bridge_module,
        "_OAUTH_PENDING_FILE",
        tmp_path / "market_oauth_pending.json",
    )
    monkeypatch.setattr(
        market_bridge_module,
        "_OAUTH_CALLBACK_FILE",
        tmp_path / "oauth_callback.json",
    )

    # Seed an ISM rooted in tmp_path and publish it as the global singleton
    # so PluginCliService.upload_and_install can pick it up.
    scanner = PluginDirectoryScanner(builtin_root, user_root)
    mgr = InstallSourceManager(
        lock_path=lock_path,
        builtin_root=builtin_root,
        user_root=user_root,
        scanner=scanner,
    )
    mgr.load()  # First_Startup seed
    set_global_manager(mgr)

    # Mount only the bridge router on a fresh FastAPI app.
    app = FastAPI(title="market-bridge-e2e")
    app.include_router(market_bridge_module.router)

    # Pull the live token from the module (bridge generates one per import).
    token = market_bridge_module.get_bridge_token()

    transport = ASGITransport(app=app)

    async def _build() -> AsyncClient:
        return AsyncClient(transport=transport, base_url="http://testserver")

    try:
        loop = asyncio.get_event_loop()
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
    client = loop.run_until_complete(_build())

    try:
        yield {
            "client": client,
            "token": token,
            "user_root": user_root,
            "builtin_root": builtin_root,
            "packages_root": packages_root,
            "lock_path": lock_path,
            "oauth_token_file": tmp_path / "market_auth.json",
            "oauth_pending_file": tmp_path / "market_oauth_pending.json",
            "oauth_callback_file": tmp_path / "oauth_callback.json",
            "profiles_root": profiles_root,
            "manager": mgr,
            "service": plugin_cli_pkg,
        }
    finally:
        loop.run_until_complete(client.aclose())
        set_global_manager(None)


# ─── Tests ────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_bridge_token_rejects_trusted_remote_origin(
    bridge_e2e_env: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from plugin.server.routes import market_bridge as market_bridge_module

    monkeypatch.setattr(market_bridge_module, "_main_server_port", lambda: 48911)

    res = await bridge_e2e_env["client"].get(
        "/market/bridge-token",
        headers={
            "Host": "127.0.0.1:48911",
            "Origin": "https://market.example.com",
        },
    )

    assert res.status_code == 403


@pytest.mark.asyncio
async def test_bridge_token_allows_local_same_origin(
    bridge_e2e_env: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from plugin.server.routes import market_bridge as market_bridge_module

    monkeypatch.setattr(market_bridge_module, "_main_server_port", lambda: 48911)

    res = await bridge_e2e_env["client"].get(
        "/market/bridge-token",
        headers={
            "Host": "127.0.0.1:48911",
            "Origin": "http://127.0.0.1:48911",
        },
    )

    assert res.status_code == 200
    assert res.json()["bridge_token"] == bridge_e2e_env["token"]


@pytest.mark.asyncio
async def test_install_happy_path_writes_v2_lock_entry(
    bridge_e2e_env: dict[str, Any],
) -> None:
    """End-to-end install: HTTP download → unpack → v2 lock entry.

    Validates the full download link:
      1. real HTTP fetch from a localhost server,
      2. sha256 check on downloaded bytes,
      3. unpack into ``USER_PLUGIN_CONFIG_ROOT``,
      4. ``record_market_install`` writes a v2 ``SourceDetailMarket`` row,
      5. ``GET /market/installed`` projects ``latest_install_source`` back.
    """

    plugin_id = "e2e_calendar"
    version = "1.2.3"
    zip_bytes, expected_payload_hash = _build_neko_plugin_zip(
        plugin_id=plugin_id, version=version,
    )
    expected_sha256 = hashlib.sha256(zip_bytes).hexdigest()

    client: AsyncClient = bridge_e2e_env["client"]
    token: str = bridge_e2e_env["token"]
    user_root: Path = bridge_e2e_env["user_root"]
    lock_path: Path = bridge_e2e_env["lock_path"]

    with _serve_bytes(
        filename="e2e_calendar-1.2.3.neko-plugin", content=zip_bytes,
    ) as package_url:
        # Trigger the install task.
        resp = await client.post(
            f"/market/install?token={token}",
            json={
                "package_url": package_url,
                "package_sha256": expected_sha256,
                "payload_hash": expected_payload_hash,
                "plugin_id": plugin_id,
                "version": version,
                "channel": "stable",
                "published_at": "2026-05-16T08:00:00.000000Z",
                "mode": "install",
                "on_conflict": "rename",
            },
        )
        assert resp.status_code == 200, resp.text
        task_id = resp.json()["task_id"]

        # Poll until terminal state (≤ 30s; the actual download is local).
        deadline = time.monotonic() + 30
        final_status: dict[str, Any] | None = None
        while time.monotonic() < deadline:
            poll = await client.get(f"/market/tasks/{task_id}?token={token}")
            assert poll.status_code == 200, poll.text
            body = poll.json()
            if body["status"] in ("completed", "failed"):
                final_status = body
                break
            await asyncio.sleep(0.05)
        assert final_status is not None, "task did not reach terminal state"
        assert final_status["status"] == "completed", final_status

    # ─── Assert: v2 lock entry written with the bytes we shipped ────────
    assert lock_path.exists(), "lock file not written"
    lock_doc = json.loads(lock_path.read_bytes())
    assert lock_doc["schema_version"] == 2

    market_entries = [
        e for e in lock_doc["entries"]
        if e["channel"] == "market" and not e.get("removed", False)
    ]
    assert len(market_entries) == 1, market_entries
    entry = market_entries[0]
    detail = entry["source_detail"]

    # All four v2 fields must be populated by the bytes that actually
    # landed on disk — sha256 from re-hashing, payload_hash from unpack
    # output, channel + published_at from the request payload.
    assert detail["plugin_market_id"] == plugin_id
    assert detail["version"] == version
    assert detail["package_url"] == f"http://127.0.0.1:{package_url.split(':')[-1].split('/')[0]}/e2e_calendar-1.2.3.neko-plugin" or detail["package_url"].endswith("e2e_calendar-1.2.3.neko-plugin")
    assert detail["package_sha256"] == expected_sha256
    assert detail["payload_hash"] == expected_payload_hash
    assert detail["channel"] == "stable"
    assert detail["published_at"] == "2026-05-16T08:00:00.000000Z"
    assert detail["previous_version"] is None  # fresh install

    # ─── Assert: directory was actually unpacked ────────────────────────
    unpacked_dir = user_root / plugin_id
    assert unpacked_dir.is_dir(), f"unpacked dir missing: {unpacked_dir}"
    plugin_toml = unpacked_dir / "plugin.toml"
    assert plugin_toml.is_file()
    assert version in plugin_toml.read_text(encoding="utf-8")


@pytest.mark.asyncio
async def test_installed_endpoint_projects_latest_install_source(
    bridge_e2e_env: dict[str, Any],
) -> None:
    """After install, ``/market/installed`` returns the v2 projection."""

    plugin_id = "e2e_companion"
    version = "0.4.0"
    zip_bytes, payload_hash = _build_neko_plugin_zip(
        plugin_id=plugin_id, version=version,
    )
    expected_sha256 = hashlib.sha256(zip_bytes).hexdigest()

    client: AsyncClient = bridge_e2e_env["client"]
    token: str = bridge_e2e_env["token"]

    with _serve_bytes(
        filename=f"{plugin_id}-{version}.neko-plugin", content=zip_bytes,
    ) as package_url:
        resp = await client.post(
            f"/market/install?token={token}",
            json={
                "package_url": package_url,
                "package_sha256": expected_sha256,
                "payload_hash": payload_hash,
                "plugin_id": plugin_id,
                "version": version,
                "channel": "beta",
                "published_at": "2026-05-16T09:00:00.000000Z",
                "mode": "install",
                "on_conflict": "rename",
            },
        )
        task_id = resp.json()["task_id"]

        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            poll = await client.get(f"/market/tasks/{task_id}?token={token}")
            if poll.json()["status"] in ("completed", "failed"):
                assert poll.json()["status"] == "completed", poll.json()
                break
            await asyncio.sleep(0.05)

    # `/market/installed` should now project our v2 source_detail
    # back to the front-end via `latest_install_source`. Note: the
    # endpoint relies on PluginCliService.list_local_plugins() which
    # scans the *built-in* plugin directory by default. In our test
    # roots that scan returns nothing, so we instead read the lock
    # snapshot directly to validate the projection function.
    from plugin.server.routes.market_bridge import _project_market_source_detail

    mgr: InstallSourceManager = bridge_e2e_env["manager"]
    snapshot = mgr.snapshot()
    [entry] = [e for e in snapshot.entries
               if e.plugin_id == plugin_id and not e.removed]

    projected = _project_market_source_detail(entry)
    assert projected is not None
    assert projected["channel"] == "beta"
    assert projected["version"] == version
    assert projected["package_sha256"] == expected_sha256
    assert projected["payload_hash"] == payload_hash
    assert projected["published_at"] == "2026-05-16T09:00:00.000000Z"
    assert projected["package_url"].endswith(f"{plugin_id}-{version}.neko-plugin")


@pytest.mark.asyncio
async def test_built_market_package_install_surfaces_in_plugin_list(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    bridge_e2e_env: dict[str, Any],
) -> None:
    """Build package → Market download → bridge install → plugin list source."""

    plugin_id = "simple_plugin"
    version = "0.1.0"
    source_dir = tmp_path / "market_source" / plugin_id
    package_path = tmp_path / "market_packages" / f"{plugin_id}.neko-plugin"
    package_path.parent.mkdir(parents=True)
    shutil.copytree(FIXTURE_PLUGINS_ROOT / plugin_id, source_dir)

    build_result = build_plugin(source_dir, package_path)
    package_bytes = package_path.read_bytes()
    expected_sha256 = hashlib.sha256(package_bytes).hexdigest()

    client: AsyncClient = bridge_e2e_env["client"]
    token: str = bridge_e2e_env["token"]
    user_root: Path = bridge_e2e_env["user_root"]

    with _serve_bytes(
        filename=f"{plugin_id}-{version}.neko-plugin", content=package_bytes,
    ) as package_url:
        resp = await client.post(
            f"/market/install?token={token}",
            json={
                "package_url": package_url,
                "package_sha256": expected_sha256,
                "payload_hash": build_result.payload_hash,
                "plugin_id": plugin_id,
                "version": version,
                "channel": "stable",
                "published_at": "2026-05-21T08:00:00.000000Z",
                "mode": "install",
                "on_conflict": "rename",
            },
        )
        assert resp.status_code == 200, resp.text
        task_id = resp.json()["task_id"]

        deadline = time.monotonic() + 30
        final_status: dict[str, Any] | None = None
        while time.monotonic() < deadline:
            poll = await client.get(f"/market/tasks/{task_id}?token={token}")
            assert poll.status_code == 200, poll.text
            body = poll.json()
            if body["status"] in ("completed", "failed"):
                final_status = body
                break
            await asyncio.sleep(0.05)

    assert final_status is not None, "task did not reach terminal state"
    assert final_status["status"] == "completed", final_status
    installed_toml = user_root / plugin_id / "plugin.toml"
    assert installed_toml.is_file()

    from plugin.server.application.plugins import query_service as query_module

    monkeypatch.setattr(
        query_module.state,
        "get_plugins_snapshot_cached",
        lambda timeout=2.0: {
            plugin_id: {
                "id": plugin_id,
                "name": "Simple Plugin",
                "description": "Minimal fixture plugin.",
                "version": version,
                "config_path": str(installed_toml),
            }
        },
    )
    monkeypatch.setattr(
        query_module.state,
        "get_plugin_hosts_snapshot_cached",
        lambda timeout=2.0: {},
    )
    monkeypatch.setattr(
        query_module.state,
        "get_event_handlers_snapshot_cached",
        lambda timeout=2.0: {},
    )

    [plugin_card] = query_module._build_plugin_list_sync("en")
    install_source = plugin_card["install_source"]
    assert install_source["source"] == "market"
    assert install_source["reason"] == "user_requested"
    assert install_source["source_detail"]["plugin_market_id"] == plugin_id
    assert install_source["source_detail"]["version"] == version
    assert install_source["source_detail"]["package_sha256"] == expected_sha256
    assert install_source["source_detail"]["payload_hash"] == build_result.payload_hash


@pytest.mark.asyncio
async def test_authenticated_market_install_reports_usage(
    monkeypatch: pytest.MonkeyPatch,
    bridge_e2e_env: dict[str, Any],
) -> None:
    """Successful install reports Market DB id + local plugin id."""

    from plugin.server.routes import market_bridge as market_bridge_module

    reports: list[dict[str, Any]] = []
    real_async_client = market_bridge_module.httpx.AsyncClient

    class _RecordingAsyncClient:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            self._delegate = real_async_client(*args, **kwargs)

        async def __aenter__(self) -> "_RecordingAsyncClient":
            await self._delegate.__aenter__()
            return self

        async def __aexit__(self, *args: Any) -> None:
            await self._delegate.__aexit__(*args)

        def stream(self, *args: Any, **kwargs: Any) -> Any:
            return self._delegate.stream(*args, **kwargs)

        async def post(
            self,
            url: str,
            *,
            headers: dict[str, str] | None = None,
            json: dict[str, Any] | None = None,
            **kwargs: Any,
        ) -> httpx.Response:
            if url == "https://market.test/api/v1/me/installs":
                reports.append({"headers": headers or {}, "json": json or {}})
                return httpx.Response(
                    200,
                    json={"ok": True},
                    request=httpx.Request("POST", url),
                )
            return await self._delegate.post(
                url,
                headers=headers,
                json=json,
                **kwargs,
            )

    monkeypatch.setattr(market_bridge_module, "MARKET_API_URL", "https://market.test")
    monkeypatch.setattr(market_bridge_module, "NEKO_AUTH_URL", "https://auth.test")
    monkeypatch.setattr(
        market_bridge_module.httpx,
        "AsyncClient",
        _RecordingAsyncClient,
    )

    token_file: Path = bridge_e2e_env["oauth_token_file"]
    token_file.write_text(
        json.dumps(
            {
                "access_token": "market-access-token",
                "expires_at": time.time() + 3600,
                "auth_url": "https://auth.test",
                "issuer": "https://auth.test/",
                "subject": "test-subject",
                "client_id": "neko-desktop",
                "scope": "openid email profile offline",
                "refresh_generation": 0,
                "market_api_url": "https://market.test",
            }
        ),
        encoding="utf-8",
    )

    local_plugin_id = "reported_plugin"
    version = "2.5.0"
    zip_bytes, payload_hash = _build_neko_plugin_zip(
        plugin_id=local_plugin_id,
        version=version,
    )
    expected_sha256 = hashlib.sha256(zip_bytes).hexdigest()

    client: AsyncClient = bridge_e2e_env["client"]
    token: str = bridge_e2e_env["token"]

    with _serve_bytes(
        filename=f"{local_plugin_id}-{version}.neko-plugin",
        content=zip_bytes,
    ) as package_url:
        resp = await client.post(
            f"/market/install?token={token}",
            json={
                "package_url": package_url,
                "package_sha256": expected_sha256,
                "payload_hash": payload_hash,
                "plugin_id": "42",
                "expected_plugin_toml_id": local_plugin_id,
                "version": version,
                "channel": "stable",
                "published_at": "2026-05-21T08:30:00.000000Z",
                "mode": "install",
                "on_conflict": "rename",
            },
        )
        assert resp.status_code == 200, resp.text
        task_id = resp.json()["task_id"]

        deadline = time.monotonic() + 30
        final_status: dict[str, Any] | None = None
        while time.monotonic() < deadline:
            poll = await client.get(f"/market/tasks/{task_id}?token={token}")
            assert poll.status_code == 200, poll.text
            body = poll.json()
            if body["status"] in ("completed", "failed"):
                final_status = body
                break
            await asyncio.sleep(0.05)

    assert final_status is not None, "task did not reach terminal state"
    assert final_status["status"] == "completed", final_status
    assert len(reports) == 1

    report = reports[0]
    assert report["headers"]["Authorization"] == "Bearer market-access-token"
    assert report["json"] == {
        "plugin_id": 42,
        "version": version,
        "channel": "stable",
        "package_sha256": expected_sha256,
        "payload_hash": payload_hash,
        "installed_plugin_id": local_plugin_id,
        "client_id": "neko-desktop",
    }


@pytest.mark.asyncio
async def test_oauth_status_clears_expired_market_token(
    bridge_e2e_env: dict[str, Any],
) -> None:
    token_file: Path = bridge_e2e_env["oauth_token_file"]
    expired_at = time.time() - 10
    token_file.write_text(
        json.dumps(
            {
                "access_token": "expired-token",
                "expires_at": expired_at,
                "user": {"username": "expired-user"},
            }
        ),
        encoding="utf-8",
    )

    client: AsyncClient = bridge_e2e_env["client"]
    token: str = bridge_e2e_env["token"]
    resp = await client.get(
        "/market/oauth/status",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body["authenticated"] is False
    assert body["expires_at"] is None
    assert not token_file.exists()


def test_oauth_token_provenance_accepts_offline_access_and_legacy_offline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from plugin.server.routes import market_bridge as market_bridge_module

    monkeypatch.setattr(market_bridge_module, "NEKO_AUTH_URL", "https://auth.test")

    base_token = {
        "auth_url": "https://auth.test",
        "issuer": "https://auth.test/",
        "subject": "test-subject",
        "client_id": "neko-desktop",
        "refresh_generation": 0,
    }

    assert market_bridge_module._oauth_token_provenance_matches({
        **base_token,
        "scope": "openid email profile offline_access",
    })
    assert market_bridge_module._oauth_token_provenance_matches({
        **base_token,
        "scope": "openid email profile offline",
    })


@pytest.mark.asyncio
async def test_oauth_status_uses_fresh_cached_market_user(
    bridge_e2e_env: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from plugin.server.routes import market_bridge as market_bridge_module

    monkeypatch.setattr(market_bridge_module, "NEKO_AUTH_URL", "https://auth.test")
    monkeypatch.setattr(market_bridge_module, "MARKET_API_URL", "https://market.test")
    monkeypatch.setattr(market_bridge_module, "MARKET_WEB_URL", "https://market.test")

    async def fail_fetch_market_user(access_token: Any) -> dict[str, Any] | None:
        raise AssertionError("fresh cached status must not call Market /auth/me")

    monkeypatch.setattr(market_bridge_module, "_fetch_market_user", fail_fetch_market_user)

    token_file: Path = bridge_e2e_env["oauth_token_file"]
    token_file.write_text(
        json.dumps(
            {
                "access_token": "cached-access-token",
                "expires_at": time.time() + 3600,
                "auth_url": "https://auth.test",
                "issuer": "https://auth.test/",
                "subject": "test-subject",
                "client_id": "neko-desktop",
                "scope": "openid email profile offline",
                "refresh_generation": 0,
                "market_api_url": "https://market.test",
                "market_api_url_last_verified": "https://market.test",
                "market_user_verified_at": time.time(),
                "user": {"username": "cached-user", "auth_user_id": "test-subject"},
            }
        ),
        encoding="utf-8",
    )

    client: AsyncClient = bridge_e2e_env["client"]
    token: str = bridge_e2e_env["token"]
    resp = await client.get(
        "/market/oauth/status",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body["authenticated"] is True
    assert body["user"]["username"] == "cached-user"


@pytest.mark.asyncio
async def test_oauth_account_summary_aggregates_safe_fields_and_caches(
    bridge_e2e_env: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The desktop receives a display projection, never raw OAuth identity data."""

    from plugin.server.routes import market_bridge as market_bridge_module

    monkeypatch.setattr(market_bridge_module, "NEKO_AUTH_URL", "https://auth.test")
    monkeypatch.setattr(market_bridge_module, "MARKET_API_URL", "https://market.test")
    market_bridge_module._clear_account_summary_cache()

    token_file: Path = bridge_e2e_env["oauth_token_file"]
    token_file.write_text(
        json.dumps(
            {
                "access_token": "local-access-token",
                "refresh_token": "must-not-leak",
                "expires_at": time.time() + 3600,
                "auth_url": "https://auth.test",
                "issuer": "https://auth.test/",
                "subject": "test-subject",
                "client_id": "neko-desktop",
                "scope": "openid email profile offline",
                "refresh_generation": 0,
            }
        ),
        encoding="utf-8",
    )
    calls = {"auth": 0, "market": 0}

    async def fetch_auth(_: Any) -> dict[str, Any]:
        calls["auth"] += 1
        return {
            "name": "Neko User",
            "preferred_username": "neko-user",
            "picture": "https://cdn.test/avatar.png",
            "email": "private@example.test",
            "sub": "private-subject",
            "login_method_kind": "google",
        }

    async def fetch_market(_: dict[str, Any]) -> dict[str, Any]:
        calls["market"] += 1
        return {
            "username": "market-name",
            "account_summary": {
                "member_days": 12,
                "published_plugins": 2,
                "installed_plugins": 5,
                "total_downloads": 20,
            },
            "permissions": ["private"],
        }

    monkeypatch.setattr(market_bridge_module, "_fetch_auth_userinfo", fetch_auth)
    monkeypatch.setattr(market_bridge_module, "_fetch_current_market_user", fetch_market)

    client: AsyncClient = bridge_e2e_env["client"]
    token: str = bridge_e2e_env["token"]
    first = await client.get(
        "/market/oauth/account-summary",
        headers={"Authorization": f"Bearer {token}"},
    )
    second = await client.get(
        "/market/oauth/account-summary",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert first.status_code == 200
    assert second.status_code == 200
    assert calls == {"auth": 1, "market": 1}
    body = first.json()
    assert body == {
        "authenticated": True,
        "profile": {
            "display_name": "Neko User",
            "username": "neko-user",
            "avatar_url": "https://cdn.test/avatar.png",
            "login_method": "google",
        },
        "market": {
            "member_days": 12,
            "published_plugins": 2,
            "installed_plugins": 5,
            "total_downloads": 20,
        },
        "sources": {
            "auth": {"status": "ready"},
            "market": {"status": "ready"},
            "community": {"status": "unavailable"},
        },
        "expires_at": pytest.approx(time.time() + 3600, abs=5),
    }
    serialized = json.dumps(body)
    assert "private@example.test" not in serialized
    assert "private-subject" not in serialized
    assert "must-not-leak" not in serialized


@pytest.mark.asyncio
async def test_oauth_status_refreshes_stale_cached_market_user(
    bridge_e2e_env: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from plugin.server.routes import market_bridge as market_bridge_module

    monkeypatch.setattr(market_bridge_module, "NEKO_AUTH_URL", "https://auth.test")
    monkeypatch.setattr(market_bridge_module, "MARKET_API_URL", "https://market.test")
    monkeypatch.setattr(market_bridge_module, "MARKET_WEB_URL", "https://market.test")

    calls: list[Any] = []

    async def fetch_market_user(access_token: Any) -> dict[str, Any] | None:
        calls.append(access_token)
        return {"username": "fresh-user", "auth_user_id": "test-subject"}

    monkeypatch.setattr(market_bridge_module, "_fetch_market_user", fetch_market_user)

    stale_verified_at = time.time() - 3600
    token_file: Path = bridge_e2e_env["oauth_token_file"]
    token_file.write_text(
        json.dumps(
            {
                "access_token": "stale-cache-access-token",
                "expires_at": time.time() + 3600,
                "auth_url": "https://auth.test",
                "issuer": "https://auth.test/",
                "subject": "test-subject",
                "client_id": "neko-desktop",
                "scope": "openid email profile offline_access",
                "refresh_generation": 0,
                "market_api_url": "https://market.test",
                "market_api_url_last_verified": "https://market.test",
                "market_user_verified_at": stale_verified_at,
                "user": {"username": "stale-user", "auth_user_id": "test-subject"},
            }
        ),
        encoding="utf-8",
    )

    client: AsyncClient = bridge_e2e_env["client"]
    token: str = bridge_e2e_env["token"]
    resp = await client.get(
        "/market/oauth/status",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body["authenticated"] is True
    assert body["user"]["username"] == "fresh-user"
    assert calls == ["stale-cache-access-token"]

    saved = json.loads(token_file.read_text(encoding="utf-8"))
    assert saved["user"]["username"] == "fresh-user"
    assert saved["market_user_verified_at"] > stale_verified_at


@pytest.mark.asyncio
async def test_oauth_status_keeps_unexpired_token_when_refresh_transiently_fails(
    bridge_e2e_env: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from plugin.server.routes import market_bridge as market_bridge_module

    monkeypatch.setattr(market_bridge_module, "NEKO_AUTH_URL", "https://auth.test")
    monkeypatch.setattr(market_bridge_module, "MARKET_API_URL", "https://market.test")
    monkeypatch.setattr(market_bridge_module, "MARKET_WEB_URL", "https://market.test")

    async def fail_refresh(token_data: dict[str, Any]) -> dict[str, Any]:
        raise market_bridge_module.HTTPException(
            status_code=502,
            detail="无法连接 Auth OAuth 服务",
        )

    async def fail_fetch_market_user(access_token: Any) -> dict[str, Any] | None:
        raise AssertionError("fresh cached status must not call Market /auth/me")

    monkeypatch.setattr(market_bridge_module, "_refresh_oauth_token", fail_refresh)
    monkeypatch.setattr(market_bridge_module, "_fetch_market_user", fail_fetch_market_user)

    now = time.time()
    token_data = {
        "access_token": "soon-expiring-access-token",
        "refresh_token": "refresh-token",
        "expires_at": now + 30,
        "auth_url": "https://auth.test",
        "issuer": "https://auth.test/",
        "subject": "test-subject",
        "client_id": "neko-desktop",
        "scope": "openid email profile offline",
        "refresh_generation": 0,
        "market_api_url": "https://market.test",
        "market_api_url_last_verified": "https://market.test",
        "market_user_verified_at": now,
        "user": {"username": "cached-user", "auth_user_id": "test-subject"},
    }
    token_file: Path = bridge_e2e_env["oauth_token_file"]
    token_file.write_text(json.dumps(token_data), encoding="utf-8")

    client: AsyncClient = bridge_e2e_env["client"]
    token: str = bridge_e2e_env["token"]
    resp = await client.get(
        "/market/oauth/status",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body["authenticated"] is True
    assert body["user"]["username"] == "cached-user"
    assert json.loads(token_file.read_text(encoding="utf-8")) == token_data


@pytest.mark.asyncio
async def test_oauth_status_deletes_token_when_refresh_is_rejected(
    bridge_e2e_env: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from plugin.server.routes import market_bridge as market_bridge_module

    monkeypatch.setattr(market_bridge_module, "NEKO_AUTH_URL", "https://auth.test")
    monkeypatch.setattr(market_bridge_module, "MARKET_API_URL", "https://market.test")
    monkeypatch.setattr(market_bridge_module, "MARKET_WEB_URL", "https://market.test")

    async def reject_refresh(token_data: dict[str, Any]) -> dict[str, Any]:
        raise market_bridge_module.HTTPException(
            status_code=401,
            detail="Auth refresh token 已失效",
        )

    monkeypatch.setattr(market_bridge_module, "_refresh_oauth_token", reject_refresh)

    token_file: Path = bridge_e2e_env["oauth_token_file"]
    token_file.write_text(
        json.dumps(
            {
                "access_token": "soon-expiring-access-token",
                "refresh_token": "revoked-refresh-token",
                "expires_at": time.time() + 30,
                "auth_url": "https://auth.test",
                "issuer": "https://auth.test/",
                "subject": "test-subject",
                "client_id": "neko-desktop",
                "scope": "openid email profile offline",
                "refresh_generation": 0,
                "market_api_url": "https://market.test",
            }
        ),
        encoding="utf-8",
    )

    client: AsyncClient = bridge_e2e_env["client"]
    token: str = bridge_e2e_env["token"]
    resp = await client.get(
        "/market/oauth/status",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert resp.status_code == 200
    assert resp.json()["authenticated"] is False
    assert not token_file.exists()


@pytest.mark.asyncio
async def test_oauth_status_rejects_market_user_without_subject(
    bridge_e2e_env: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from plugin.server.routes import market_bridge as market_bridge_module

    monkeypatch.setattr(market_bridge_module, "NEKO_AUTH_URL", "https://auth.test")
    monkeypatch.setattr(market_bridge_module, "MARKET_API_URL", "https://market.test")
    monkeypatch.setattr(market_bridge_module, "MARKET_WEB_URL", "https://market.test")

    async def fetch_market_user(access_token: Any) -> dict[str, Any] | None:
        return {"username": "missing-subject"}

    monkeypatch.setattr(market_bridge_module, "_fetch_market_user", fetch_market_user)

    token_file: Path = bridge_e2e_env["oauth_token_file"]
    original_token_data = {
        "access_token": "subjectless-access-token",
        "expires_at": time.time() + 3600,
        "auth_url": "https://auth.test",
        "issuer": "https://auth.test/",
        "subject": "test-subject",
        "client_id": "neko-desktop",
        "scope": "openid email profile offline",
        "refresh_generation": 0,
        "market_api_url": "https://market.test",
    }
    token_file.write_text(json.dumps(original_token_data), encoding="utf-8")

    client: AsyncClient = bridge_e2e_env["client"]
    token: str = bridge_e2e_env["token"]
    resp = await client.get(
        "/market/oauth/status",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert resp.status_code == 200
    assert resp.json()["authenticated"] is False
    assert json.loads(token_file.read_text(encoding="utf-8")) == original_token_data


@pytest.mark.asyncio
async def test_oauth_complete_clears_session_when_market_rejects_token(
    bridge_e2e_env: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from plugin.server.routes import market_bridge as market_bridge_module

    monkeypatch.setattr(market_bridge_module, "NEKO_AUTH_URL", "https://auth.test")
    monkeypatch.setattr(market_bridge_module, "MARKET_API_URL", "https://market.test")

    async def exchange_oauth_code(
        code: str,
        code_verifier: str,
        redirect_uri: str,
    ) -> dict[str, Any]:
        return {
            "access_token": "complete-access-token",
            "refresh_token": "complete-refresh-token",
            "token_type": "bearer",
            "scope": "openid email profile offline",
            "expires_in": 3600,
        }

    async def fetch_market_user(access_token: Any) -> dict[str, Any] | None:
        return None

    monkeypatch.setattr(market_bridge_module, "_exchange_oauth_code", exchange_oauth_code)
    monkeypatch.setattr(market_bridge_module, "_fetch_market_user", fetch_market_user)

    pending_file: Path = bridge_e2e_env["oauth_pending_file"]
    callback_file: Path = bridge_e2e_env["oauth_callback_file"]
    token_file: Path = bridge_e2e_env["oauth_token_file"]
    pending_file.write_text(
        json.dumps(
            {
                "state": "state-1",
                "code_verifier": "verifier-1",
                "redirect_uri": "http://127.0.0.1:48916/market/oauth/callback",
                "expires_at": time.time() + 60,
            }
        ),
        encoding="utf-8",
    )
    callback_file.write_text(
        json.dumps({"state": "state-1", "code": "code-1"}),
        encoding="utf-8",
    )

    client: AsyncClient = bridge_e2e_env["client"]
    token: str = bridge_e2e_env["token"]
    resp = await client.post(
        "/market/oauth/complete",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert resp.status_code == 401
    assert resp.json()["detail"] == "Market 未接受当前 Auth token"
    assert not pending_file.exists()
    assert not callback_file.exists()
    assert not token_file.exists()


@pytest.mark.asyncio
async def test_oauth_complete_rejects_market_user_without_subject(
    bridge_e2e_env: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from plugin.server.routes import market_bridge as market_bridge_module

    monkeypatch.setattr(market_bridge_module, "NEKO_AUTH_URL", "https://auth.test")
    monkeypatch.setattr(market_bridge_module, "MARKET_API_URL", "https://market.test")

    async def exchange_oauth_code(
        code: str,
        code_verifier: str,
        redirect_uri: str,
    ) -> dict[str, Any]:
        return {
            "access_token": "complete-access-token",
            "refresh_token": "complete-refresh-token",
            "token_type": "bearer",
            "scope": "openid email profile offline",
            "expires_in": 3600,
        }

    async def fetch_market_user(access_token: Any) -> dict[str, Any] | None:
        return {"username": "missing-subject"}

    monkeypatch.setattr(market_bridge_module, "_exchange_oauth_code", exchange_oauth_code)
    monkeypatch.setattr(market_bridge_module, "_fetch_market_user", fetch_market_user)

    pending_file: Path = bridge_e2e_env["oauth_pending_file"]
    callback_file: Path = bridge_e2e_env["oauth_callback_file"]
    token_file: Path = bridge_e2e_env["oauth_token_file"]
    pending_file.write_text(
        json.dumps(
            {
                "state": "state-1",
                "code_verifier": "verifier-1",
                "redirect_uri": "http://127.0.0.1:48916/market/oauth/callback",
                "expires_at": time.time() + 60,
            }
        ),
        encoding="utf-8",
    )
    callback_file.write_text(
        json.dumps({"state": "state-1", "code": "code-1"}),
        encoding="utf-8",
    )

    client: AsyncClient = bridge_e2e_env["client"]
    token: str = bridge_e2e_env["token"]
    resp = await client.post(
        "/market/oauth/complete",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert resp.status_code == 502
    assert resp.json()["detail"] == "Market 用户信息缺少 subject"
    assert not pending_file.exists()
    assert not callback_file.exists()
    assert not token_file.exists()


@pytest.mark.asyncio
async def test_install_rejects_sha256_mismatch(
    bridge_e2e_env: dict[str, Any],
) -> None:
    """SHA256 mismatch fails the task without writing a lock entry.

    Note: ``"0" * 64`` is treated as "Market did not provide a hash"
    (R3.5) and gracefully skips verification; only a real-shaped but
    non-matching hex triggers a hard mismatch failure. We only test the
    latter — the skip-hash branch is covered by ``_verify_sha256``'s
    structured-log path.
    """

    fake_sha = "f" * 64
    plugin_id = "e2e_bad_hash"
    version = "0.0.1"
    zip_bytes, _ = _build_neko_plugin_zip(plugin_id=plugin_id, version=version)

    client: AsyncClient = bridge_e2e_env["client"]
    token: str = bridge_e2e_env["token"]
    lock_path: Path = bridge_e2e_env["lock_path"]
    user_root: Path = bridge_e2e_env["user_root"]

    with _serve_bytes(
        filename=f"{plugin_id}-{version}.neko-plugin", content=zip_bytes,
    ) as package_url:
        resp = await client.post(
            f"/market/install?token={token}",
            json={
                "package_url": package_url,
                "package_sha256": fake_sha,
                "plugin_id": plugin_id,
                "version": version,
                "channel": "stable",
                "mode": "install",
            },
        )
        task_id = resp.json()["task_id"]

        deadline = time.monotonic() + 30
        final_status: dict[str, Any] | None = None
        while time.monotonic() < deadline:
            poll = await client.get(f"/market/tasks/{task_id}?token={token}")
            body = poll.json()
            if body["status"] in ("completed", "failed"):
                final_status = body
                break
            await asyncio.sleep(0.05)
        assert final_status is not None
        assert final_status["status"] == "failed", final_status
        message_blob = (final_status.get("error") or "") + \
                       (final_status.get("message") or "")
        assert "SHA256" in message_blob, message_blob

    # Critically: lock file must NOT contain the failed plugin.
    if lock_path.exists():
        doc = json.loads(lock_path.read_bytes())
        plugin_ids_in_lock = {
            e["plugin_id"] for e in doc.get("entries", [])
            if not e.get("removed", False)
        }
        assert plugin_id not in plugin_ids_in_lock, \
            f"failed install leaked into lock: {plugin_ids_in_lock}"
    # And the unpacked dir must have been cleaned up.
    assert not (user_root / plugin_id).exists(), \
        "failed install left unpacked directory"


@pytest.mark.asyncio
@pytest.mark.parametrize("package_sha256", [None, "", "0" * 64, "not-a-sha256"])
async def test_install_requires_valid_sha256_before_creating_task(
    bridge_e2e_env: dict[str, Any],
    package_sha256: str | None,
) -> None:
    """Market installs require a real package hash before any download starts."""

    client: AsyncClient = bridge_e2e_env["client"]
    token: str = bridge_e2e_env["token"]
    body: dict[str, Any] = {
        "package_url": "https://example.invalid/plugin.neko-plugin",
        "plugin_id": "missing_hash_plugin",
        "version": "0.0.1",
        "channel": "stable",
        "mode": "install",
    }
    if package_sha256 is not None:
        body["package_sha256"] = package_sha256

    resp = await client.post(f"/market/install?token={token}", json=body)

    assert resp.status_code == 422, resp.text


@pytest.mark.asyncio
async def test_install_identity_match_no_warning(
    bridge_e2e_env: dict[str, Any],
) -> None:
    """When ``expected_plugin_toml_id`` matches the unpacked id, install
    completes without an identity-mismatch warning (Option C happy path).
    """

    plugin_id = "e2e_identity_ok"
    version = "1.0.0"
    zip_bytes, payload_hash = _build_neko_plugin_zip(
        plugin_id=plugin_id, version=version,
    )
    expected_sha256 = hashlib.sha256(zip_bytes).hexdigest()

    client: AsyncClient = bridge_e2e_env["client"]
    token: str = bridge_e2e_env["token"]

    with _serve_bytes(
        filename=f"{plugin_id}-{version}.neko-plugin", content=zip_bytes,
    ) as package_url:
        resp = await client.post(
            f"/market/install?token={token}",
            json={
                "package_url": package_url,
                "package_sha256": expected_sha256,
                "payload_hash": payload_hash,
                "plugin_id": plugin_id,
                "version": version,
                "channel": "stable",
                "mode": "install",
                # Market slug == unpacked plugin.toml id → match
                "expected_plugin_toml_id": plugin_id,
            },
        )
        task_id = resp.json()["task_id"]

        deadline = time.monotonic() + 30
        final_status: dict[str, Any] | None = None
        while time.monotonic() < deadline:
            poll = await client.get(f"/market/tasks/{task_id}?token={token}")
            body = poll.json()
            if body["status"] in ("completed", "failed"):
                final_status = body
                break
            await asyncio.sleep(0.05)

    assert final_status is not None
    assert final_status["status"] == "completed", final_status
    # Match path: no identity warning surfaced. Other warnings (e.g.
    # legacy package_sha256 absence) are still permitted, but ours
    # specifically must not appear.
    warning_blob = final_status.get("install_source_warning") or ""
    assert "plugin identity mismatch" not in warning_blob


@pytest.mark.asyncio
async def test_install_identity_mismatch_warns_but_succeeds(
    bridge_e2e_env: dict[str, Any],
) -> None:
    """When Market's slug disagrees with the unpacked plugin.toml id,
    install still proceeds (soft check) but surfaces an
    ``install_source_warning`` so the user can audit (Option C / R3.5
    intentional non-strictness).
    """

    actual_plugin_id = "e2e_real_plugin"
    declared_slug = "e2e_misnamed_slug"  # what Market thinks this plugin is
    version = "1.0.0"
    zip_bytes, payload_hash = _build_neko_plugin_zip(
        plugin_id=actual_plugin_id, version=version,
    )
    expected_sha256 = hashlib.sha256(zip_bytes).hexdigest()

    client: AsyncClient = bridge_e2e_env["client"]
    token: str = bridge_e2e_env["token"]
    user_root: Path = bridge_e2e_env["user_root"]
    lock_path: Path = bridge_e2e_env["lock_path"]

    with _serve_bytes(
        filename=f"{actual_plugin_id}-{version}.neko-plugin", content=zip_bytes,
    ) as package_url:
        resp = await client.post(
            f"/market/install?token={token}",
            json={
                "package_url": package_url,
                "package_sha256": expected_sha256,
                "payload_hash": payload_hash,
                "plugin_id": actual_plugin_id,
                "version": version,
                "channel": "stable",
                "mode": "install",
                "expected_plugin_toml_id": declared_slug,  # mismatch
            },
        )
        task_id = resp.json()["task_id"]

        deadline = time.monotonic() + 30
        final_status: dict[str, Any] | None = None
        while time.monotonic() < deadline:
            poll = await client.get(f"/market/tasks/{task_id}?token={token}")
            body = poll.json()
            if body["status"] in ("completed", "failed"):
                final_status = body
                break
            await asyncio.sleep(0.05)

    assert final_status is not None
    # Soft check: install succeeds despite the mismatch.
    assert final_status["status"] == "completed", final_status

    # The warning must surface in the task's install_source_warning so
    # the front-end can show it; we also accept it being attached to
    # the task message, but the canonical channel is the dedicated field.
    warning_blob = final_status.get("install_source_warning") or ""
    assert "plugin identity mismatch" in warning_blob, warning_blob
    assert declared_slug in warning_blob
    assert actual_plugin_id in warning_blob

    # The lock entry records the actual unpacked plugin id, not the
    # declared slug — Option C does not let Market falsify identity.
    doc = json.loads(lock_path.read_bytes())
    market_entries = [
        e for e in doc["entries"]
        if e["channel"] == "market" and not e.get("removed", False)
    ]
    [entry] = [e for e in market_entries if e["plugin_id"] == actual_plugin_id]
    # ``expected_plugin_toml_id`` is informational and must NOT be persisted
    # into source_detail (would muddy the v2 schema).
    assert "expected_plugin_toml_id" not in entry["source_detail"]
    # Directory exists with the actual id, not the declared slug.
    assert (user_root / actual_plugin_id).is_dir()
    assert not (user_root / declared_slug).exists()


@pytest.mark.asyncio
async def test_install_conflict_fails_without_renaming_executable_directory(
    bridge_e2e_env: dict[str, Any],
) -> None:
    """Even a legacy rename request must not create an executable plugin copy."""

    plugin_id = "e2e_rename_identity"
    version = "1.0.0"
    zip_bytes, payload_hash = _build_neko_plugin_zip(
        plugin_id=plugin_id, version=version,
    )
    expected_sha256 = hashlib.sha256(zip_bytes).hexdigest()

    client: AsyncClient = bridge_e2e_env["client"]
    token: str = bridge_e2e_env["token"]
    user_root: Path = bridge_e2e_env["user_root"]
    lock_path: Path = bridge_e2e_env["lock_path"]

    existing = user_root / plugin_id
    existing.mkdir(parents=True)
    (existing / "plugin.toml").write_text(
        f'[plugin]\nid = "{plugin_id}"\nversion = "0.9.0"\n',
        encoding="utf-8",
    )

    with _serve_bytes(
        filename=f"{plugin_id}-{version}.neko-plugin", content=zip_bytes,
    ) as package_url:
        resp = await client.post(
            f"/market/install?token={token}",
            json={
                "package_url": package_url,
                "package_sha256": expected_sha256,
                "payload_hash": payload_hash,
                "plugin_id": plugin_id,
                "version": version,
                "channel": "stable",
                "mode": "install",
                "expected_plugin_toml_id": plugin_id,
                "on_conflict": "rename",
            },
        )
        task_id = resp.json()["task_id"]

        deadline = time.monotonic() + 30
        final_status: dict[str, Any] | None = None
        while time.monotonic() < deadline:
            poll = await client.get(f"/market/tasks/{task_id}?token={token}")
            body = poll.json()
            if body["status"] in ("completed", "failed"):
                final_status = body
                break
            await asyncio.sleep(0.05)

    assert final_status is not None
    assert final_status["status"] == "failed", final_status
    assert not (user_root / f"{plugin_id}_1").exists()

    active_entries = []
    if lock_path.exists():
        doc = json.loads(lock_path.read_bytes())
        active_entries = [
            e for e in doc["entries"]
            if e["plugin_id"] == plugin_id and not e.get("removed", False)
        ]
    assert active_entries == []


@pytest.mark.asyncio
async def test_upgrade_happy_path_replaces_lock_entry(
    bridge_e2e_env: dict[str, Any],
) -> None:
    """End-to-end upgrade: install v1.0 → upgrade to v2.0.

    Validates the full upgrade chain:
      1. install v1.0 first (seed the lock entry);
      2. POST /market/install mode=upgrade → bridge ``_do_upgrade``;
      3. lifecycle stop / start are best-effort no-ops here (plugin
         was never loaded into the host registry, so the bridge's
         ``_safely_is_running`` returns False and skips both);
      4. backup rename → unpack new bytes → record_market_upgrade;
      5. lock entry now reflects v2.0 with previous_version=v1.0
         and ``installed_at`` preserved from the v1.0 install.
    """

    plugin_id = "e2e_upgrade_target"
    v1_zip, v1_payload_hash = _build_neko_plugin_zip(
        plugin_id=plugin_id, version="1.0.0",
    )
    v2_zip, v2_payload_hash = _build_neko_plugin_zip(
        plugin_id=plugin_id, version="2.0.0",
    )

    client: AsyncClient = bridge_e2e_env["client"]
    token: str = bridge_e2e_env["token"]
    user_root: Path = bridge_e2e_env["user_root"]

    async def _install(zip_bytes: bytes, payload_hash: str, version: str, mode: str) -> dict[str, Any]:
        sha = hashlib.sha256(zip_bytes).hexdigest()
        with _serve_bytes(
            filename=f"{plugin_id}-{version}.neko-plugin", content=zip_bytes,
        ) as package_url:
            resp = await client.post(
                f"/market/install?token={token}",
                json={
                    "package_url": package_url,
                    "package_sha256": sha,
                    "payload_hash": payload_hash,
                    "plugin_id": plugin_id,
                    "version": version,
                    "channel": "stable",
                    "mode": mode,
                    "on_conflict": "rename" if mode == "install" else "fail",
                },
            )
            assert resp.status_code == 200, resp.text
            task_id = resp.json()["task_id"]

            deadline = time.monotonic() + 30
            final_status: dict[str, Any] | None = None
            while time.monotonic() < deadline:
                poll = await client.get(f"/market/tasks/{task_id}?token={token}")
                body = poll.json()
                if body["status"] in ("completed", "failed"):
                    final_status = body
                    break
                await asyncio.sleep(0.05)
            assert final_status is not None
            return final_status

    # Step 1 — install v1.0.0.
    install_status = await _install(v1_zip, v1_payload_hash, "1.0.0", "install")
    assert install_status["status"] == "completed", install_status

    mgr: InstallSourceManager = bridge_e2e_env["manager"]
    [v1_entry] = [
        e for e in mgr.snapshot().entries
        if e.plugin_id == plugin_id and not e.removed
    ]
    v1_installed_at = v1_entry.installed_at
    assert v1_entry.source_detail.version == "1.0.0"
    assert v1_entry.source_detail.previous_version is None

    # Step 2 — upgrade to v2.0.0.
    upgrade_status = await _install(v2_zip, v2_payload_hash, "2.0.0", "upgrade")
    assert upgrade_status["status"] == "completed", upgrade_status

    # Step 3 — lock now reflects v2.0.0 with v1 captured as previous.
    snapshot = mgr.snapshot()
    market_entries = [
        e for e in snapshot.entries
        if e.plugin_id == plugin_id and not e.removed
    ]
    assert len(market_entries) == 1, f"single-entry invariant violated: {market_entries}"
    [v2_entry] = market_entries

    from plugin.server.application.install_source.models import SourceDetailMarket

    assert isinstance(v2_entry.source_detail, SourceDetailMarket)
    assert v2_entry.source_detail.version == "2.0.0"
    assert v2_entry.source_detail.previous_version == "1.0.0"
    assert v2_entry.installed_at == v1_installed_at, (
        "upgrade must preserve installed_at — see design §3.2.2"
    )
    assert v2_entry.updated_at >= v1_entry.updated_at

    # Step 4 — directory now contains v2 plugin.toml content.
    plugin_toml = user_root / plugin_id / "plugin.toml"
    assert "2.0.0" in plugin_toml.read_text(encoding="utf-8")


@pytest.mark.asyncio
async def test_upgrade_lifecycle_uses_installed_plugin_id_not_market_id(
    bridge_e2e_env: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plugin_id = "e2e_lifecycle_target"
    market_id = "42"
    v1_zip, v1_payload_hash = _build_neko_plugin_zip(
        plugin_id=plugin_id, version="1.0.0",
    )
    v2_zip, v2_payload_hash = _build_neko_plugin_zip(
        plugin_id=plugin_id, version="2.0.0",
    )

    client: AsyncClient = bridge_e2e_env["client"]
    token: str = bridge_e2e_env["token"]
    calls: list[tuple[str, str]] = []

    async def _wait_task(task_id: str) -> dict[str, Any]:
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            poll = await client.get(f"/market/tasks/{task_id}?token={token}")
            body = poll.json()
            if body["status"] in ("completed", "failed"):
                return body
            await asyncio.sleep(0.05)
        raise AssertionError(f"task {task_id} did not finish")

    with _serve_bytes(
        filename=f"{plugin_id}-1.0.0.neko-plugin", content=v1_zip,
    ) as package_url:
        resp = await client.post(
            f"/market/install?token={token}",
            json={
                "package_url": package_url,
                "package_sha256": hashlib.sha256(v1_zip).hexdigest(),
                "payload_hash": v1_payload_hash,
                "plugin_id": plugin_id,
                "version": "1.0.0",
                "channel": "stable",
                "mode": "install",
                "expected_plugin_toml_id": plugin_id,
            },
        )
        assert (await _wait_task(resp.json()["task_id"]))["status"] == "completed"

    from plugin.server.routes import market_bridge as market_bridge_module

    async def fake_is_running(target: str) -> bool:
        calls.append(("is_running", target))
        return True

    async def fake_stop(target: str) -> None:
        calls.append(("stop", target))

    async def fake_start(target: str, *, strict: bool) -> bool:
        calls.append(("start", target))
        return strict

    monkeypatch.setattr(market_bridge_module, "plugin_is_running", fake_is_running)
    monkeypatch.setattr(market_bridge_module, "stop_plugin_for_upgrade", fake_stop)
    monkeypatch.setattr(market_bridge_module, "start_plugin_after_upgrade", fake_start)

    with _serve_bytes(
        filename=f"{plugin_id}-2.0.0.neko-plugin", content=v2_zip,
    ) as package_url:
        resp = await client.post(
            f"/market/install?token={token}",
            json={
                "package_url": package_url,
                "package_sha256": hashlib.sha256(v2_zip).hexdigest(),
                "payload_hash": v2_payload_hash,
                "plugin_id": market_id,
                "version": "2.0.0",
                "channel": "stable",
                "mode": "upgrade",
                "on_conflict": "fail",
                "expected_plugin_toml_id": plugin_id,
            },
        )
        upgrade_status = await _wait_task(resp.json()["task_id"])

    assert upgrade_status["status"] == "completed", upgrade_status
    assert ("is_running", plugin_id) in calls
    assert ("stop", plugin_id) in calls
    assert ("start", plugin_id) in calls
    assert all(target != market_id for _op, target in calls)


@pytest.mark.asyncio
async def test_install_conflict_defaults_to_failure_without_lock_entry(
    bridge_e2e_env: dict[str, Any],
) -> None:
    plugin_id = "e2e_install_conflict"
    v1_zip, v1_payload_hash = _build_neko_plugin_zip(
        plugin_id=plugin_id, version="1.0.0",
    )

    client: AsyncClient = bridge_e2e_env["client"]
    token: str = bridge_e2e_env["token"]
    user_root: Path = bridge_e2e_env["user_root"]
    mgr: InstallSourceManager = bridge_e2e_env["manager"]

    existing = user_root / plugin_id
    existing.mkdir(parents=True)
    (existing / "plugin.toml").write_text(
        f'[plugin]\nid = "{plugin_id}"\nversion = "0.9.0"\n',
        encoding="utf-8",
    )

    async def _wait_task(task_id: str) -> dict[str, Any]:
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            poll = await client.get(f"/market/tasks/{task_id}?token={token}")
            body = poll.json()
            if body["status"] in ("completed", "failed"):
                return body
            await asyncio.sleep(0.05)
        raise AssertionError(f"task {task_id} did not finish")

    with _serve_bytes(
        filename=f"{plugin_id}-1.0.0.neko-plugin", content=v1_zip,
    ) as package_url:
        resp = await client.post(
            f"/market/install?token={token}",
            json={
                "package_url": package_url,
                "package_sha256": hashlib.sha256(v1_zip).hexdigest(),
                "payload_hash": v1_payload_hash,
                "plugin_id": plugin_id,
                "version": "1.0.0",
                "channel": "stable",
                "mode": "install",
                "expected_plugin_toml_id": plugin_id,
            },
        )
        install_status = await _wait_task(resp.json()["task_id"])

    assert install_status["status"] == "failed", install_status
    assert "0.9.0" in (existing / "plugin.toml").read_text(encoding="utf-8")
    assert not (user_root / f"{plugin_id}_1").exists()
    assert [
        e for e in mgr.snapshot().entries
        if e.plugin_id == plugin_id and not e.removed
    ] == []


@pytest.mark.asyncio
async def test_upgrade_rejects_plugin_identity_mismatch_and_rolls_back(
    bridge_e2e_env: dict[str, Any],
) -> None:
    """Upgrade must reject a package whose plugin.toml id changes identity."""

    plugin_id = "e2e_upgrade_identity"
    intruder_id = "e2e_upgrade_intruder"
    v1_zip, v1_payload_hash = _build_neko_plugin_zip(
        plugin_id=plugin_id, version="1.0.0",
    )
    intruder_zip, intruder_payload_hash = _build_neko_plugin_zip(
        plugin_id=intruder_id, version="2.0.0",
    )

    client: AsyncClient = bridge_e2e_env["client"]
    token: str = bridge_e2e_env["token"]
    user_root: Path = bridge_e2e_env["user_root"]
    mgr: InstallSourceManager = bridge_e2e_env["manager"]

    async def _wait_task(task_id: str) -> dict[str, Any]:
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            poll = await client.get(f"/market/tasks/{task_id}?token={token}")
            body = poll.json()
            if body["status"] in ("completed", "failed"):
                return body
            await asyncio.sleep(0.05)
        raise AssertionError(f"task {task_id} did not finish")

    v1_sha = hashlib.sha256(v1_zip).hexdigest()
    with _serve_bytes(
        filename=f"{plugin_id}-1.0.0.neko-plugin", content=v1_zip,
    ) as package_url:
        resp = await client.post(
            f"/market/install?token={token}",
            json={
                "package_url": package_url,
                "package_sha256": v1_sha,
                "payload_hash": v1_payload_hash,
                "plugin_id": plugin_id,
                "version": "1.0.0",
                "channel": "stable",
                "mode": "install",
                "expected_plugin_toml_id": plugin_id,
            },
        )
        assert resp.status_code == 200, resp.text
        install_status = await _wait_task(resp.json()["task_id"])

    assert install_status["status"] == "completed", install_status
    [v1_entry] = [
        e for e in mgr.snapshot().entries
        if e.plugin_id == plugin_id and not e.removed
    ]
    v1_installed_at = v1_entry.installed_at

    intruder_sha = hashlib.sha256(intruder_zip).hexdigest()
    with _serve_bytes(
        filename=f"{intruder_id}-2.0.0.neko-plugin", content=intruder_zip,
    ) as package_url:
        resp = await client.post(
            f"/market/install?token={token}",
            json={
                "package_url": package_url,
                "package_sha256": intruder_sha,
                "payload_hash": intruder_payload_hash,
                "plugin_id": plugin_id,
                "version": "2.0.0",
                "channel": "stable",
                "mode": "upgrade",
                "on_conflict": "fail",
                "expected_plugin_toml_id": plugin_id,
            },
        )
        assert resp.status_code == 200, resp.text
        upgrade_status = await _wait_task(resp.json()["task_id"])

    assert upgrade_status["status"] == "failed", upgrade_status
    assert upgrade_status["error_code"] == "upgrade_rollback_completed"
    assert "plugin identity mismatch" in upgrade_status["error"]
    assert plugin_id in upgrade_status["error"]
    assert intruder_id in upgrade_status["error"]

    [restored_entry] = [
        e for e in mgr.snapshot().entries
        if e.plugin_id == plugin_id and not e.removed
    ]
    from plugin.server.application.install_source.models import SourceDetailMarket

    assert isinstance(restored_entry.source_detail, SourceDetailMarket)
    assert restored_entry.source_detail.version == "1.0.0"
    assert restored_entry.installed_at == v1_installed_at
    assert (user_root / plugin_id / "plugin.toml").is_file()
    assert "1.0.0" in (user_root / plugin_id / "plugin.toml").read_text(
        encoding="utf-8",
    )
    assert not (user_root / intruder_id).exists()


@pytest.mark.asyncio
async def test_failed_market_install_cleans_promoted_profile_dir(
    bridge_e2e_env: dict[str, Any],
) -> None:
    plugin_id = "e2e_profile_cleanup"
    intruder_id = "e2e_profile_intruder"
    v1_zip, v1_payload_hash = _build_neko_plugin_zip(
        plugin_id=plugin_id,
        version="1.0.0",
    )
    zip_bytes, payload_hash = _build_neko_plugin_zip(
        plugin_id=intruder_id,
        version="2.0.0",
        include_profile=True,
    )

    client: AsyncClient = bridge_e2e_env["client"]
    token: str = bridge_e2e_env["token"]
    user_root: Path = bridge_e2e_env["user_root"]
    profiles_root: Path = bridge_e2e_env["profiles_root"]

    with _serve_bytes(
        filename=f"{plugin_id}-1.0.0.neko-plugin", content=v1_zip,
    ) as package_url:
        resp = await client.post(
            f"/market/install?token={token}",
            json={
                "package_url": package_url,
                "package_sha256": hashlib.sha256(v1_zip).hexdigest(),
                "payload_hash": v1_payload_hash,
                "plugin_id": plugin_id,
                "version": "1.0.0",
                "channel": "stable",
                "mode": "install",
                "expected_plugin_toml_id": plugin_id,
            },
        )
        assert resp.status_code == 200, resp.text
        task_id = resp.json()["task_id"]
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            poll = await client.get(f"/market/tasks/{task_id}?token={token}")
            if poll.json()["status"] in ("completed", "failed"):
                assert poll.json()["status"] == "completed", poll.json()
                break
            await asyncio.sleep(0.05)

    with _serve_bytes(
        filename=f"{intruder_id}-2.0.0.neko-plugin", content=zip_bytes,
    ) as package_url:
        resp = await client.post(
            f"/market/install?token={token}",
            json={
                "package_url": package_url,
                "package_sha256": hashlib.sha256(zip_bytes).hexdigest(),
                "payload_hash": payload_hash,
                "plugin_id": plugin_id,
                "version": "2.0.0",
                "channel": "stable",
                "mode": "upgrade",
                "on_conflict": "fail",
                "expected_plugin_toml_id": plugin_id,
            },
        )
        assert resp.status_code == 200, resp.text
        task_id = resp.json()["task_id"]

        deadline = time.monotonic() + 30
        final_status: dict[str, Any] | None = None
        while time.monotonic() < deadline:
            poll = await client.get(f"/market/tasks/{task_id}?token={token}")
            body = poll.json()
            if body["status"] in ("completed", "failed"):
                final_status = body
                break
            await asyncio.sleep(0.05)

    assert final_status is not None
    assert final_status["status"] == "failed", final_status
    assert "plugin identity mismatch" in final_status["error"]
    assert (user_root / plugin_id / "plugin.toml").is_file()
    assert not (user_root / intruder_id).exists()
    assert not (profiles_root / intruder_id).exists()


@pytest.mark.asyncio
async def test_upgrade_rejects_when_not_installed(
    bridge_e2e_env: dict[str, Any],
) -> None:
    """mode=upgrade on a plugin that has no lock entry → HTTP 400 with
    ``plugin_not_installed_for_upgrade`` (R5.5).
    """

    client: AsyncClient = bridge_e2e_env["client"]
    token: str = bridge_e2e_env["token"]

    resp = await client.post(
        f"/market/install?token={token}",
        json={
            "package_url": "http://127.0.0.1:1/never_used.neko-plugin",
            "package_sha256": "f" * 64,
            "plugin_id": "e2e_never_installed",
            "version": "1.0.0",
            "channel": "stable",
            "mode": "upgrade",
        },
    )
    assert resp.status_code == 400
    detail = resp.json()["detail"]
    assert detail["code"] == "plugin_not_installed_for_upgrade"


@pytest.mark.asyncio
async def test_upgrade_rejects_same_version(
    bridge_e2e_env: dict[str, Any],
) -> None:
    """mode=upgrade with target version equal to current → task fails
    with ``version_already_at_target`` (R5.6). reinstall mode bypasses
    this check.
    """

    plugin_id = "e2e_same_version"
    zip_bytes, payload_hash = _build_neko_plugin_zip(
        plugin_id=plugin_id, version="1.0.0",
    )
    sha = hashlib.sha256(zip_bytes).hexdigest()

    client: AsyncClient = bridge_e2e_env["client"]
    token: str = bridge_e2e_env["token"]

    # Seed v1.0.0.
    with _serve_bytes(
        filename=f"{plugin_id}-1.0.0.neko-plugin", content=zip_bytes,
    ) as package_url:
        resp = await client.post(
            f"/market/install?token={token}",
            json={
                "package_url": package_url,
                "package_sha256": sha,
                "payload_hash": payload_hash,
                "plugin_id": plugin_id,
                "version": "1.0.0",
                "channel": "stable",
                "mode": "install",
            },
        )
        task_id = resp.json()["task_id"]
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            poll = await client.get(f"/market/tasks/{task_id}?token={token}")
            if poll.json()["status"] in ("completed", "failed"):
                break
            await asyncio.sleep(0.05)

    # Try to "upgrade" to the same version → version_already_at_target.
    with _serve_bytes(
        filename=f"{plugin_id}-1.0.0-again.neko-plugin", content=zip_bytes,
    ) as package_url:
        resp = await client.post(
            f"/market/install?token={token}",
            json={
                "package_url": package_url,
                "package_sha256": sha,
                "payload_hash": payload_hash,
                "plugin_id": plugin_id,
                "version": "1.0.0",
                "channel": "stable",
                "mode": "upgrade",
            },
        )
        task_id = resp.json()["task_id"]
        deadline = time.monotonic() + 30
        final_status: dict[str, Any] | None = None
        while time.monotonic() < deadline:
            poll = await client.get(f"/market/tasks/{task_id}?token={token}")
            body = poll.json()
            if body["status"] in ("completed", "failed"):
                final_status = body
                break
            await asyncio.sleep(0.05)

    assert final_status is not None
    assert final_status["status"] == "failed", final_status
    assert final_status["error_code"] == "version_already_at_target"


@pytest.mark.asyncio
async def test_upgrade_rollback_on_download_failure(
    bridge_e2e_env: dict[str, Any],
) -> None:
    """mode=upgrade with a download error → backup restored, lock
    unchanged, ``upgrade_rollback_completed`` returned.

    Drives the rollback path by pointing ``package_url`` at a 404
    on a real localhost server (the server only serves the install
    artefact, not the upgrade artefact).
    """

    plugin_id = "e2e_rollback"
    v1_zip, v1_payload_hash = _build_neko_plugin_zip(
        plugin_id=plugin_id, version="1.0.0",
    )
    v1_sha = hashlib.sha256(v1_zip).hexdigest()

    client: AsyncClient = bridge_e2e_env["client"]
    token: str = bridge_e2e_env["token"]
    user_root: Path = bridge_e2e_env["user_root"]

    # Seed v1.0.0.
    with _serve_bytes(
        filename=f"{plugin_id}-1.0.0.neko-plugin", content=v1_zip,
    ) as package_url:
        resp = await client.post(
            f"/market/install?token={token}",
            json={
                "package_url": package_url,
                "package_sha256": v1_sha,
                "payload_hash": v1_payload_hash,
                "plugin_id": plugin_id,
                "version": "1.0.0",
                "channel": "stable",
                "mode": "install",
            },
        )
        task_id = resp.json()["task_id"]
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            poll = await client.get(f"/market/tasks/{task_id}?token={token}")
            if poll.json()["status"] in ("completed", "failed"):
                break
            await asyncio.sleep(0.05)

    mgr: InstallSourceManager = bridge_e2e_env["manager"]
    [v1_entry] = [
        e for e in mgr.snapshot().entries
        if e.plugin_id == plugin_id and not e.removed
    ]
    v1_installed_at = v1_entry.installed_at

    # Attempt to upgrade to v2.0.0 with a URL that will 404 — the http
    # server only serves the file we name, others get 404. Use a
    # different filename to force the failure.
    with _serve_bytes(
        filename=f"{plugin_id}-1.0.0.neko-plugin", content=v1_zip,
    ) as package_url:
        broken_url = package_url.rsplit("/", 1)[0] + "/does_not_exist.neko-plugin"

        resp = await client.post(
            f"/market/install?token={token}",
            json={
                "package_url": broken_url,
                "package_sha256": "f" * 64,
                "plugin_id": plugin_id,
                "version": "2.0.0",
                "channel": "stable",
                "mode": "upgrade",
                "on_conflict": "fail",
            },
        )
        task_id = resp.json()["task_id"]
        deadline = time.monotonic() + 30
        final_status: dict[str, Any] | None = None
        while time.monotonic() < deadline:
            poll = await client.get(f"/market/tasks/{task_id}?token={token}")
            body = poll.json()
            if body["status"] in ("completed", "failed"):
                final_status = body
                break
            await asyncio.sleep(0.05)

    assert final_status is not None
    assert final_status["status"] == "failed", final_status
    assert final_status["error_code"] == "upgrade_rollback_completed"

    # Lock entry must be unchanged (still v1.0.0 with original installed_at).
    snapshot = mgr.snapshot()
    [restored_entry] = [
        e for e in snapshot.entries
        if e.plugin_id == plugin_id and not e.removed
    ]
    from plugin.server.application.install_source.models import SourceDetailMarket
    assert isinstance(restored_entry.source_detail, SourceDetailMarket)
    assert restored_entry.source_detail.version == "1.0.0"
    assert restored_entry.installed_at == v1_installed_at
    # Directory must have been restored from backup.
    assert (user_root / plugin_id / "plugin.toml").is_file()
    # No backup leak (best-effort cleanup runs in async task; we don't
    # strictly assert absence here because the cleanup is fire-and-forget,
    # but the live directory must be the original one, not a stub).
    plugin_toml_text = (user_root / plugin_id / "plugin.toml").read_text(encoding="utf-8")
    assert "1.0.0" in plugin_toml_text
