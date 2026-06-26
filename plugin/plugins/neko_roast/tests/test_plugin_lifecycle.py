from types import SimpleNamespace

import pytest

from plugin.plugins.neko_roast import NekoRoastPlugin


@pytest.mark.asyncio
async def test_startup_syncs_live_instructions_instead_of_unconditional_inject(monkeypatch):
    calls: list[tuple[str, bool | None]] = []

    class Runtime:
        def __init__(self, _plugin) -> None:
            self.config = SimpleNamespace(developer_tools_enabled=False)

        async def start(self) -> None:
            calls.append(("start", None))

        async def inject_instructions(self, *, force: bool = False) -> str:
            calls.append(("inject", force))
            return "injected"

        async def sync_live_instructions(self, *, force: bool = False) -> str:
            calls.append(("sync_live", force))
            return "not_injected"

        async def sync_developer_mode(self, *, announce: bool = False) -> str:
            calls.append(("sync_developer", announce))
            return "developer_not_injected"

    monkeypatch.setattr("plugin.plugins.neko_roast.core.runtime.RoastRuntime", Runtime)
    plugin = NekoRoastPlugin(SimpleNamespace(logger=None))

    result = await plugin.startup()

    assert result.is_ok() is True
    assert ("sync_live", False) in calls
    assert not any(name == "inject" for name, _ in calls)


@pytest.mark.asyncio
async def test_config_change_syncs_live_instructions_instead_of_unconditional_inject():
    calls: list[tuple[str, bool | None]] = []

    class Runtime:
        def __init__(self) -> None:
            self.config = SimpleNamespace(developer_tools_enabled=False)

        async def reload_config(self) -> None:
            calls.append(("reload", None))

        async def inject_instructions(self, *, force: bool = False) -> str:
            calls.append(("inject", force))
            return "injected"

        async def sync_live_instructions(self, *, force: bool = False) -> str:
            calls.append(("sync_live", force))
            return "not_injected"

        async def sync_developer_mode(self, *, announce: bool = False) -> str:
            calls.append(("sync_developer", announce))
            return "developer_not_injected"

    plugin = NekoRoastPlugin(SimpleNamespace(logger=None))
    plugin.runtime = Runtime()

    result = await plugin.on_config_change()

    assert result.is_ok() is True
    assert ("sync_live", True) in calls
    assert not any(name == "inject" for name, _ in calls)


@pytest.mark.asyncio
async def test_config_change_without_runtime_stays_pending():
    plugin = NekoRoastPlugin(SimpleNamespace(logger=None))

    result = await plugin.on_config_change()

    assert result.is_ok() is True
    assert result.value == {"status": "ready", "runtime": "pending"}


@pytest.mark.asyncio
async def test_command_loop_start_restarts_idle_hosting_loop():
    class Runtime:
        def __init__(self) -> None:
            self.starts = 0

        def _start_idle_hosting_loop(self) -> None:
            self.starts += 1

    plugin = NekoRoastPlugin(SimpleNamespace(logger=None))
    runtime = Runtime()
    plugin.runtime = runtime

    await plugin._on_command_loop_start()

    assert runtime.starts == 1
