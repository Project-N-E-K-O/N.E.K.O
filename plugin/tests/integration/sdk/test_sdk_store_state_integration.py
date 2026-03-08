from __future__ import annotations

import asyncio
import sys
from types import ModuleType

import pytest

from plugin.sdk.state import PluginStatePersistence
from plugin.sdk.store import PluginStore


class _FakeGlobalState:
    def __init__(self) -> None:
        self.mem: dict[str, bytes] = {}

    def save_frozen_state_memory(self, plugin_id: str, data: bytes) -> None:
        self.mem[plugin_id] = data

    def get_frozen_state_memory(self, plugin_id: str):
        return self.mem.get(plugin_id)

    def clear_frozen_state_memory(self, plugin_id: str) -> None:
        self.mem.pop(plugin_id, None)

    def has_frozen_state_memory(self, plugin_id: str) -> bool:
        return plugin_id in self.mem


@pytest.mark.plugin_integration
@pytest.mark.asyncio
@pytest.mark.parametrize("backend", ["memory", "file"])
async def test_store_state_async_combined_flow(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
    backend: str,
) -> None:
    if backend == "memory":
        fake_state = _FakeGlobalState()
        core_state_module = ModuleType("plugin.core.state")
        core_state_module.state = fake_state
        monkeypatch.setitem(sys.modules, "plugin.core.state", core_state_module)

    class _Plugin:
        counter = 0

    plugin = _Plugin()
    state = PluginStatePersistence("demo", tmp_path, backend=backend)
    store = PluginStore("demo", tmp_path, enabled=True)

    async def _writer(i: int) -> None:
        await store.set_async(f"k{i}", {"v": i})

    await asyncio.gather(*[_writer(i) for i in range(20)])
    assert await store.count_async() >= 20
    assert (await store.get_async("k3")) == {"v": 3}

    plugin.counter = 7
    assert await state.save_async(plugin, ["counter"], reason="integration") is True
    plugin.counter = -1
    assert await state.load_async(plugin) is True
    assert plugin.counter == 7

    info = await state.get_state_info_async()
    assert info and info["plugin_id"] == "demo"
    assert await state.clear_async() is True
    assert await state.has_saved_state_async() is False

    await store.clear_async()
    await store.close_async()
