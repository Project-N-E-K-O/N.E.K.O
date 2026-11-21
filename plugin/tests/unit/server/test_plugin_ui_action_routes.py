from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest
from fastapi import HTTPException
from fastapi import FastAPI
from fastapi.testclient import TestClient
from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware

from plugin.server.routes import plugin_ui


pytestmark = pytest.mark.unit


@pytest.mark.parametrize("bundled", [False, True])
def test_static_display_sdk_is_served_without_a_running_plugin(bundled, tmp_path, monkeypatch):
    if bundled:
        source = Path(plugin_ui.__file__).resolve().parents[3] / "static" / "plugin-ui-sdk" / "display.js"
        target = tmp_path / "static" / "plugin-ui-sdk" / "display.js"
        target.parent.mkdir(parents=True)
        target.write_bytes(source.read_bytes())
        monkeypatch.setattr(plugin_ui, "__file__", str(tmp_path / "plugin" / "server" / "routes" / "plugin_ui.py"))
    app = FastAPI()
    app.include_router(plugin_ui.router)
    with TestClient(app) as client:
        response = client.get("/plugin-ui-sdk/display.js")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/javascript")
    assert response.headers["cache-control"] == "no-cache"
    assert "NekoPluginDisplay" in response.text
    assert "display.requestMode" in response.text


@pytest.mark.parametrize("forwarded", [False, True])
def test_native_push_rejects_loopback_proxy_but_preserves_direct_backend_calls(forwarded):
    app = FastAPI()
    # Test the push boundary independently of the mutation router's CSRF gate.
    app.add_api_route("/push", plugin_ui.plugin_ui_push, methods=["POST"])
    queue = asyncio.Queue()
    plugin_ui._sse_clients["proxy-test"] = [queue]
    try:
        with TestClient(ProxyHeadersMiddleware(app, trusted_hosts="*"),
                        client=("127.0.0.1", 50000)) as client:
            headers = {"X-Forwarded-For": "127.0.0.1"} if forwarded else {}
            response = client.post("/push?plugin_id=proxy-test", headers=headers, json={"text": "test"})
        assert response.status_code == (403 if forwarded else 200)
        assert queue.qsize() == (0 if forwarded else 1)
    finally:
        plugin_ui._sse_clients.pop("proxy-test", None)


def test_hosted_action_cancels_plugin_call_after_client_disconnect(
    monkeypatch,
) -> None:
    async def run() -> bool:
        started = asyncio.Event()
        cancelled = asyncio.Event()

        async def call_surface_action(*_args, **_kwargs):
            started.set()
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                cancelled.set()
                raise

        class DisconnectAfterStart:
            async def is_disconnected(self) -> bool:
                await started.wait()
                return True

        monkeypatch.setattr(
            plugin_ui.plugin_ui_query_service,
            "call_surface_action",
            call_surface_action,
        )

        with pytest.raises(HTTPException) as raised:
            await plugin_ui.plugin_hosted_ui_action(
                "demo",
                "slow",
                DisconnectAfterStart(),
                plugin_ui.HostedUiActionRequest(),
            )

        assert raised.value.status_code == 499
        return cancelled.is_set()

    assert asyncio.run(run())


def test_hosted_action_returns_normally_before_client_disconnect(
    monkeypatch,
) -> None:
    async def run():
        never_disconnected = asyncio.Event()

        async def call_surface_action(*_args, **_kwargs):
            return {
                "plugin_id": "demo",
                "action_id": "status",
                "result": {"ok": True},
            }

        class ConnectedRequest:
            async def is_disconnected(self) -> bool:
                await never_disconnected.wait()
                return False

        monkeypatch.setattr(
            plugin_ui.plugin_ui_query_service,
            "call_surface_action",
            call_surface_action,
        )

        return await plugin_ui.plugin_hosted_ui_action(
            "demo",
            "status",
            ConnectedRequest(),
            plugin_ui.HostedUiActionRequest(),
        )

    response = asyncio.run(run())

    assert response.status_code == 200
    assert json.loads(response.body) == {
        "plugin_id": "demo",
        "action_id": "status",
        "result": {"ok": True},
    }
