from __future__ import annotations

from typing import Any

import httpx
import pytest

from plugin.server import market_protocol_handler
from plugin.server.routes import market_bridge


def test_protocol_install_poll_timeout_covers_bridge_download_timeout() -> None:
    assert market_protocol_handler._INSTALL_POLL_TIMEOUT_SECONDS > market_bridge._DOWNLOAD_TIMEOUT


@pytest.mark.asyncio
async def test_protocol_install_handoff_stops_at_local_confirmation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    notifications: list[tuple[str, str]] = []

    class FakeResponse:
        status_code = 200
        text = ""

        @staticmethod
        def json() -> dict[str, str]:
            return {
                "task_id": "handoff-task",
                "status": "awaiting_confirmation",
            }

    class FakeClient:
        async def __aenter__(self) -> "FakeClient":
            return self

        async def __aexit__(self, *_args: object) -> None:
            return None

        async def post(self, *_args: object, **_kwargs: object) -> FakeResponse:
            return FakeResponse()

        async def get(self, *_args: object, **_kwargs: object) -> Any:
            raise AssertionError("an unconfirmed URI handoff must not poll an install worker")

    monkeypatch.setattr(
        market_protocol_handler,
        "_load_bridge_info",
        lambda: {"token": "bridge-token", "port": 48911},
    )
    monkeypatch.setattr(httpx, "AsyncClient", lambda **_kwargs: FakeClient())
    monkeypatch.setattr(
        market_protocol_handler,
        "_show_notification",
        lambda message, title: notifications.append((message, title)),
    )
    monkeypatch.setattr(market_protocol_handler, "_INSTALL_POLL_INTERVAL_SECONDS", 0)

    result = await market_protocol_handler._call_local_install(
        package_url="https://downloads.example/demo.neko-plugin",
        package_sha256="a" * 64,
        plugin_id="demo",
        version="1.0.0",
        payload_hash=None,
        channel="stable",
        published_at=None,
        expected_plugin_toml_id="demo",
        mode="install",
        on_conflict="fail",
    )

    assert result == 0
    assert notifications == [("请在 N.E.K.O 插件中心确认安装 demo", "N.E.K.O")]
