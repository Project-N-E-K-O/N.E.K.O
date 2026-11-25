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


def test_protocol_handler_main_loads_project_dotenv_and_signs_market_install(tmp_path, monkeypatch) -> None:
    """Standalone `main()` loads project `.env` with `override=False` so proxy/remote flags sign `/market/install`."""
    dotenv_key = "protocol-dotenv-instance-key-" + "x" * 40
    for env_name in (
        "NEKO_BEHIND_PROXY",
        "NEKO_ACTIVITY_TRACKER_REMOTE",
        "ACTIVITY_TRACKER_REMOTE",
        "NEKO_INSTANCE_ACCESS_KEY",
    ):
        monkeypatch.delenv(env_name, raising=False)

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
