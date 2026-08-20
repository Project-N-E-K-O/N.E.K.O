from __future__ import annotations

import asyncio
import json

import pytest
from fastapi import HTTPException

from plugin.server.routes import plugin_ui


pytestmark = pytest.mark.unit


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
