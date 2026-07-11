from types import SimpleNamespace

import pytest

from plugin.plugins.neko_roast import NekoRoastPlugin
from plugin.plugins.neko_roast.core import runtime as runtime_module


@pytest.mark.asyncio
async def test_config_change_without_runtime_stays_pending():
    plugin = NekoRoastPlugin(SimpleNamespace(logger=None))

    result = await plugin.on_config_change()

    assert result.is_ok() is True
    assert result.value == {"status": "ready", "runtime": "pending"}


@pytest.mark.asyncio
async def test_startup_syncs_prompt_context_without_forcing_empty_restores(monkeypatch):
    calls = []

    class FakeRuntime:
        def __init__(self, _plugin):
            pass

        async def start(self):
            calls.append(("start",))

        async def sync_live_instructions(self, *, force=False):
            calls.append(("live", force))

        async def sync_developer_mode(self, *, announce=False, force=False):
            calls.append(("developer", announce, force))

    monkeypatch.setattr(runtime_module, "RoastRuntime", FakeRuntime)
    plugin = NekoRoastPlugin(SimpleNamespace(logger=None))
    monkeypatch.setattr(plugin, "register_dynamic_entry", lambda *args, **kwargs: None)
    monkeypatch.setattr(plugin, "_sync_developer_entries", lambda: None)

    result = await plugin.startup()

    assert result.is_ok() is True
    assert calls == [("start",), ("live", False), ("developer", False, False)]


@pytest.mark.asyncio
async def test_update_config_second_developer_sync_only_announces(monkeypatch):
    sync_calls = []
    injections = 0
    config = SimpleNamespace(
        developer_tools_enabled=False,
        to_dict=lambda: {"developer_tools_enabled": True},
    )

    class FakeRuntime:
        def __init__(self):
            self.config = config

        async def update_config(self, _updates):
            self.config.developer_tools_enabled = True
            await self.sync_developer_mode(announce=False, force=True)
            return self.config

        async def sync_developer_mode(self, *, announce=False, force=False):
            nonlocal injections
            sync_calls.append((announce, force))
            if force or injections == 0:
                injections += 1

    plugin = NekoRoastPlugin(SimpleNamespace(logger=None))
    plugin.runtime = FakeRuntime()
    monkeypatch.setattr(plugin, "_sync_developer_entries", lambda: None)

    result = await plugin.update_config_entry(developer_tools_enabled=True)

    assert result.is_ok() is True
    assert sync_calls == [(False, True), (True, False)]
    assert injections == 1


@pytest.mark.asyncio
async def test_clear_sandbox_data_stays_available_outside_developer_mode():
    class FakeRuntime:
        config = SimpleNamespace(developer_tools_enabled=False)
        clear_calls = 0

        def clear_sandbox_data(self):
            self.clear_calls += 1
            return {"records": 2, "preview_files": 1}

    plugin = NekoRoastPlugin(SimpleNamespace(logger=None))
    runtime = FakeRuntime()
    plugin.runtime = runtime

    result = await plugin.clear_sandbox_data()

    assert result.is_ok() is True
    assert result.value == {"cleared": {"records": 2, "preview_files": 1}}
    assert runtime.clear_calls == 1
