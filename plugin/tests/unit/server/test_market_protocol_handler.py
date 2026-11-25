import json
import os

import httpx
from fastapi import FastAPI, Request

import config as cfg
from plugin.server import market_protocol_handler
from plugin.server.infrastructure import package_download
from utils.instance_access import INTERNAL_SERVICE_HEADER, InstanceAccessMiddleware


def test_protocol_install_poll_timeout_covers_a_stalled_download_read() -> None:
    assert market_protocol_handler._INSTALL_POLL_TIMEOUT_SECONDS > package_download.REQUEST_TIMEOUT


def test_protocol_install_requires_market_id_and_version(monkeypatch) -> None:
    notices: list[str] = []

    async def forbidden(**kwargs):
        raise AssertionError("install without a catalogue identity reached the bridge")

    monkeypatch.setattr(market_protocol_handler, "_call_local_install", forbidden)
    monkeypatch.setattr(
        market_protocol_handler,
        "_show_notification",
        lambda message, title: notices.append(message),
    )
    base = {"url": "https://example.test/p.neko-plugin", "sha256": "a" * 64}
    for params in (base, {**base, "id": "42"}, {**base, "version": "1.0.0"}):
        assert market_protocol_handler._handle_install(params) == 1
    assert len(notices) == 3


def _clear_proxy_env(monkeypatch) -> None:
    """Ensure monkeypatch restores proxy/remote/instance env vars even when originally unset."""
    for env_name in (
        "NEKO_BEHIND_PROXY",
        "NEKO_ACTIVITY_TRACKER_REMOTE",
        "ACTIVITY_TRACKER_REMOTE",
        "NEKO_INSTANCE_ACCESS_KEY",
    ):
        monkeypatch.setenv(env_name, "")
        monkeypatch.delenv(env_name)


def test_protocol_handler_main_loads_project_dotenv_and_signs_market_install(tmp_path, monkeypatch) -> None:
    """Standalone `main()` loads project `.env` with `override=False` so proxy/remote flags sign `/market/install`."""
    dotenv_key = "protocol-dotenv-instance-key-" + "x" * 40
    _clear_proxy_env(monkeypatch)

    dotenv_path = tmp_path / ".env"
    dotenv_path.write_text(
        f"NEKO_BEHIND_PROXY=1\nNEKO_INSTANCE_ACCESS_KEY={dotenv_key}\n",
        encoding="utf-8",
    )
    main_port = int(cfg.MAIN_SERVER_PORT)
    bridge_path = tmp_path / "bridge.json"
    bridge_path.write_text(
        json.dumps({"token": "bridge-tok", "port": main_port}),
        encoding="utf-8",
    )

    monkeypatch.setattr(market_protocol_handler, "_project_dotenv_path", lambda: dotenv_path)
    monkeypatch.setattr(market_protocol_handler, "_BRIDGE_FILE", bridge_path)
    monkeypatch.setattr(market_protocol_handler, "_INSTALL_POLL_INTERVAL_SECONDS", 0.0)
    monkeypatch.setattr(market_protocol_handler, "_show_notification", lambda *_args: None)

    captured: list[tuple[str, dict[str, str], object]] = []
    app = FastAPI()

    @app.post("/market/install")
    async def market_install(request: Request):
        captured.append((
            request.url.path,
            {k.lower(): v for k, v in request.headers.items()},
            request.scope.get("neko.instance_identity"),
        ))
        return {"success": True, "task_id": "t1"}

    @app.get("/market/tasks/{task_id}")
    async def market_task_status(request: Request, task_id: str):
        captured.append((
            request.url.path,
            {k.lower(): v for k, v in request.headers.items()},
            request.scope.get("neko.instance_identity"),
        ))
        return {"status": "completed", "plugin_id": "demo", "task_id": task_id}

    app.add_middleware(InstanceAccessMiddleware)

    real_client = httpx.AsyncClient

    def transport_client(*_args, **kwargs):
        return real_client(
            transport=httpx.ASGITransport(app=app, client=("127.0.0.1", 50000)),
            **kwargs,
        )

    monkeypatch.setattr(httpx, "AsyncClient", transport_client)

    uri = f"neko://install?url=https://example.test/p.neko-plugin&sha256={'a' * 64}&id=demo&version=1.0.0"
    rc = market_protocol_handler.main([uri])

    assert rc == 0
    assert len(captured) == 2
    install_path, install_headers, install_identity = captured[0]
    assert install_path == "/market/install"
    assert INTERNAL_SERVICE_HEADER in install_headers
    assert install_identity == "internal"
    assert captured[1][0] == "/market/tasks/t1"
    assert INTERNAL_SERVICE_HEADER in captured[1][1]
    assert captured[1][2] == "internal"

    # Explicit process environment takes precedence over .env (override=False).
    captured.clear()
    monkeypatch.setenv("NEKO_BEHIND_PROXY", "0")
    rc_override = market_protocol_handler.main([uri])

    assert rc_override == 0
    assert os.environ["NEKO_BEHIND_PROXY"] == "0"
    assert len(captured) == 2
    assert INTERNAL_SERVICE_HEADER not in captured[0][1]
    assert captured[0][2] is None


async def test_protocol_handler_signs_live_bridge_fallback_port_without_broadening_default_hooks(
    tmp_path, monkeypatch
) -> None:
    """Fallback Main port from bridge.json is signed per-client only; default hooks still reject that port."""
    from utils.instance_access import _verified
    from utils.internal_http_client import internal_service_event_hooks

    dotenv_key = "protocol-bridge-fallback-key-" + "y" * 40
    _clear_proxy_env(monkeypatch)
    monkeypatch.setenv("NEKO_USER_PLUGIN_SERVER_PORT", "")
    monkeypatch.delenv("NEKO_USER_PLUGIN_SERVER_PORT")

    monkeypatch.setattr(cfg, "MAIN_SERVER_PORT", 48911)
    monkeypatch.setattr(cfg, "MEMORY_SERVER_PORT", 48912)
    monkeypatch.setattr(cfg, "TOOL_SERVER_PORT", 48915)
    monkeypatch.setattr(cfg, "USER_PLUGIN_SERVER_PORT", 48916)
    monkeypatch.setattr(cfg, "USER_PLUGIN_BASE", "http://127.0.0.1:48916")

    dotenv_path = tmp_path / ".env"
    dotenv_path.write_text(
        f"NEKO_BEHIND_PROXY=1\nNEKO_INSTANCE_ACCESS_KEY={dotenv_key}\n",
        encoding="utf-8",
    )
    fallback_port = 48919
    bridge_path = tmp_path / "bridge.json"
    bridge_path.write_text(
        json.dumps({"token": "bridge-tok", "port": fallback_port}),
        encoding="utf-8",
    )

    monkeypatch.setattr(market_protocol_handler, "_project_dotenv_path", lambda: dotenv_path)
    monkeypatch.setattr(market_protocol_handler, "_BRIDGE_FILE", bridge_path)
    monkeypatch.setattr(market_protocol_handler, "_INSTALL_POLL_INTERVAL_SECONDS", 0.0)
    monkeypatch.setattr(market_protocol_handler, "_show_notification", lambda *_args: None)

    captured: list[tuple[str, dict[str, str], object]] = []
    app = FastAPI()

    @app.post("/market/install")
    async def market_install(request: Request):
        captured.append((
            request.url.path,
            {k.lower(): v for k, v in request.headers.items()},
            request.scope.get("neko.instance_identity"),
        ))
        return {"success": True, "task_id": "t-fb"}

    @app.get("/market/tasks/{task_id}")
    async def market_task_status(request: Request, task_id: str):
        captured.append((
            request.url.path,
            {k.lower(): v for k, v in request.headers.items()},
            request.scope.get("neko.instance_identity"),
        ))
        return {"status": "completed", "plugin_id": "demo", "task_id": task_id}

    app.add_middleware(InstanceAccessMiddleware)

    real_client = httpx.AsyncClient

    def transport_client(*_args, **kwargs):
        return real_client(
            transport=httpx.ASGITransport(app=app, client=("127.0.0.1", 50000)),
            **kwargs,
        )

    monkeypatch.setattr(httpx, "AsyncClient", transport_client)

    rc = await market_protocol_handler._call_local_install(
        package_url="https://example.test/p.neko-plugin",
        package_sha256="b" * 64,
        plugin_id="demo",
        version="1.0.0",
        payload_hash=None,
        channel=None,
        published_at=None,
        expected_plugin_toml_id=None,
        mode="install",
        on_conflict="fail",
    )

    assert rc == 0
    assert len(captured) == 2
    install_proof = captured[0][1][INTERNAL_SERVICE_HEADER]
    assert captured[0][2] == "internal"
    assert _verified(dotenv_key, "internal-service", f"POST:{fallback_port}:/market/install", install_proof) == "internal"
    assert _verified(dotenv_key, "internal-service", "POST:48911:/market/install", install_proof) is None
    poll_proof = captured[1][1][INTERNAL_SERVICE_HEADER]
    assert captured[1][2] == "internal"
    assert _verified(dotenv_key, "internal-service", f"GET:{fallback_port}:/market/tasks/t-fb", poll_proof) == "internal"

    # Negative control: a separate client using default hooks does not sign fallback_port.
    captured.clear()
    async with real_client(
        transport=httpx.ASGITransport(app=app, client=("127.0.0.1", 50000)),
        event_hooks=internal_service_event_hooks(),
    ) as unprivileged_client:
        denied = await unprivileged_client.post(f"http://127.0.0.1:{fallback_port}/market/install", params={"token": "bridge-tok"})
    assert denied.status_code == 401
    assert denied.json()["detail"] == "instance_authorization_required"
    assert captured == []


def test_protocol_handler_malformed_dotenv_allows_open_but_fails_closed_for_install(
    tmp_path, monkeypatch
) -> None:
    """Malformed non-UTF-8 .env must not break neko://open, and must abort neko://install fail-closed without HTTP."""
    _clear_proxy_env(monkeypatch)
    bad_dotenv = tmp_path / ".env"
    bad_dotenv.write_bytes(b"\xff\xfe\x00invalid-utf8")

    bridge_path = tmp_path / "bridge.json"
    bridge_path.write_text(
        json.dumps({"token": "bridge-tok", "port": int(cfg.MAIN_SERVER_PORT)}),
        encoding="utf-8",
    )

    notices: list[str] = []
    monkeypatch.setattr(market_protocol_handler, "_project_dotenv_path", lambda: bad_dotenv)
    monkeypatch.setattr(market_protocol_handler, "_BRIDGE_FILE", bridge_path)
    monkeypatch.setattr(market_protocol_handler, "_show_notification", lambda msg, _title: notices.append(msg))

    assert market_protocol_handler.main(["neko://open?plugin=demo"]) == 0

    def forbid_http(*_args, **_kwargs):
        raise AssertionError("HTTP must not be attempted when .env cannot be read")

    monkeypatch.setattr(httpx, "AsyncClient", forbid_http)
    install_uri = f"neko://install?url=https://example.test/p.neko-plugin&sha256={'c' * 64}&id=demo&version=1.0.0"
    assert market_protocol_handler.main([install_uri]) == 1
    assert any(".env" in msg for msg in notices)
