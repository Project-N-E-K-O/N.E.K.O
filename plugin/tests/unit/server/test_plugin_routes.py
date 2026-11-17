from __future__ import annotations

import pytest

from plugin.server.routes import plugins as module


pytestmark = pytest.mark.plugin_unit


@pytest.mark.asyncio
async def test_start_plugin_endpoint_ensures_messaging_before_start(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []

    async def _ensure_messaging() -> None:
        calls.append("ensure")

    async def _start_plugin(plugin_id: str, *, persist_user_intent: bool = False) -> dict[str, object]:
        calls.append(f"start:{plugin_id}:{persist_user_intent}")
        return {"success": True, "plugin_id": plugin_id}

    monkeypatch.setattr(module, "ensure_plugin_messaging_started", _ensure_messaging, raising=False)
    monkeypatch.setattr(module.lifecycle_service, "start_plugin", _start_plugin)

    result = await module.start_plugin_endpoint("sample_plugin", _="test")

    assert result == {"success": True, "plugin_id": "sample_plugin"}
    assert calls == ["ensure", "start:sample_plugin:True"]
