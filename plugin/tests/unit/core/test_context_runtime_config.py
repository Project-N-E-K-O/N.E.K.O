from __future__ import annotations

from pathlib import Path

import pytest

from plugin.core.context import PluginContext


class _Logger:
    def warning(self, *args, **kwargs) -> None:
        self.last_warning = (args, kwargs)

    def debug(self, *args, **kwargs) -> None:
        self.last_debug = (args, kwargs)


class _Instance:
    def __init__(self) -> None:
        self.refreshed: list[dict[str, object]] = []

    def refresh_runtime_config(self, effective_config: dict[str, object]) -> None:
        self.refreshed.append(effective_config)


@pytest.mark.plugin_unit
@pytest.mark.asyncio
async def test_context_effective_config_refreshes_plugin_runtime_helpers(tmp_path: Path) -> None:
    ctx = PluginContext(
        plugin_id="demo",
        config_path=tmp_path / "demo" / "plugin.toml",
        logger=_Logger(),  # type: ignore[arg-type]
        status_queue=None,
    )
    instance = _Instance()
    ctx._instance = instance

    async def _local_payload(**kwargs: object) -> dict[str, object]:
        return {"config": {"plugin": {"store": {"enabled": True}}}}

    ctx._get_local_config_payload = _local_payload  # type: ignore[method-assign]

    payload = await ctx.get_own_effective_config()

    assert payload == {"config": {"plugin": {"store": {"enabled": True}}}}
    assert ctx._effective_config == {"plugin": {"store": {"enabled": True}}}
    assert instance.refreshed == [{"plugin": {"store": {"enabled": True}}}]
