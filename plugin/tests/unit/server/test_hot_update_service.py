from __future__ import annotations

import pytest

from plugin.core.state import state
from plugin.server.application.config import hot_update_service as module
from plugin.server.domain.errors import ServerDomainError


class _QuarantinedHost:
    _neko_startup_quarantined = True

    def __init__(self) -> None:
        self.update_calls = 0

    async def send_config_update(self, **_kwargs: object) -> dict[str, object]:
        self.update_calls += 1
        return {"handler_called": True}


@pytest.mark.plugin_unit
@pytest.mark.asyncio
async def test_temporary_update_rejects_quarantined_host(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    host = _QuarantinedHost()
    monkeypatch.setattr(state, "plugin_hosts", {"demo": host})

    with pytest.raises(ServerDomainError) as exc_info:
        await module.hot_update_plugin_config(
            plugin_id="demo",
            updates={"runtime": {"enabled": True}},
            mode="temporary",
        )

    assert exc_info.value.code == "PLUGIN_NOT_RUNNING"
    assert host.update_calls == 0


@pytest.mark.plugin_unit
@pytest.mark.asyncio
async def test_permanent_update_persists_without_reloading_quarantined_host(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    host = _QuarantinedHost()
    persisted_calls: list[tuple[str, dict[str, object]]] = []
    monkeypatch.setattr(state, "plugin_hosts", {"demo": host})
    monkeypatch.setattr(
        module,
        "update_plugin_config",
        lambda plugin_id, updates: (
            persisted_calls.append((plugin_id, updates))
            or {"success": True, "plugin_id": plugin_id}
        ),
    )
    monkeypatch.setattr(
        module,
        "load_plugin_config",
        lambda _plugin_id: {"config": {"runtime": {"enabled": True}}},
    )

    result = await module.hot_update_plugin_config(
        plugin_id="demo",
        updates={"runtime": {"enabled": True}},
        mode="permanent",
    )

    assert result["hot_reloaded"] is False
    assert persisted_calls == [("demo", {"runtime": {"enabled": True}})]
    assert host.update_calls == 0
