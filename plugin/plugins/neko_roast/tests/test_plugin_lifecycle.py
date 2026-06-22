from types import SimpleNamespace

import pytest

from plugin.plugins.neko_roast import NekoRoastPlugin


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
