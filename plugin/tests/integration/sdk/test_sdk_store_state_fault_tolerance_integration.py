from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import ormsgpack
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
async def test_store_async_corrupted_blob_fallback(tmp_path: Path) -> None:
    store = PluginStore("demo", tmp_path, enabled=True)
    await store.set_async("ok", {"x": 1})
    await store.set_async("bad", {"x": 2})

    # Corrupt one row manually to simulate on-disk damage.
    conn = store._get_conn()  # noqa: SLF001 - intentional integration probe
    conn.execute("UPDATE kv_store SET value = ? WHERE key = ?", (b"\xc1", "bad"))
    conn.commit()

    assert await store.get_async("ok") == {"x": 1}
    assert await store.get_async("bad", default="fallback") == "fallback"

    dumped = await store.dump_async()
    assert dumped["ok"] == {"x": 1}
    assert "bad" not in dumped

    await store.close_async()


@pytest.mark.plugin_integration
@pytest.mark.asyncio
async def test_store_async_concurrent_set_delete_consistency(tmp_path: Path) -> None:
    store = PluginStore("demo", tmp_path, enabled=True)

    async def _set_key(i: int) -> None:
        await store.set_async(f"k{i}", {"i": i})

    async def _del_key(i: int) -> None:
        await store.delete_async(f"k{i}")

    await asyncio.gather(*[_set_key(i) for i in range(120)])
    await asyncio.gather(*[_del_key(i) for i in range(0, 120, 2)])

    assert await store.count_async() == 60
    assert await store.exists_async("k2") is False
    assert await store.exists_async("k3") is True
    assert await store.get_async("k3") == {"i": 3}

    await store.clear_async()
    await store.close_async()


@pytest.mark.plugin_integration
@pytest.mark.asyncio
async def test_state_file_backend_unknown_version_and_corrupted_payload(tmp_path: Path) -> None:
    class _Plugin:
        v = 1

    plugin = _Plugin()
    state = PluginStatePersistence("demo", tmp_path, backend="file")
    assert await state.save_async(plugin, ["v"], reason="ok") is True

    # Unknown version should be rejected.
    unknown = {
        "version": 999,
        "plugin_id": "demo",
        "saved_at": 1.0,
        "reason": "test",
        "data": {"v": 9},
    }
    state._state_path.write_bytes(ormsgpack.packb(unknown))  # noqa: SLF001 - integration corruption injection
    plugin.v = -1
    assert await state.load_async(plugin) is False
    assert plugin.v == -1

    # Totally corrupted payload should be rejected and info probe returns None.
    state._state_path.write_bytes(b"not-msgpack")  # noqa: SLF001
    assert await state.load_async(plugin) is False
    assert await state.get_state_info_async() is None
    assert await state.clear_async() is True


@pytest.mark.plugin_integration
@pytest.mark.asyncio
async def test_state_memory_backend_async_roundtrip_and_clear(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fake_state = _FakeGlobalState()
    core_state_module = ModuleType("plugin.core.state")
    core_state_module.state = fake_state
    monkeypatch.setitem(sys.modules, "plugin.core.state", core_state_module)

    class _Plugin:
        counter = 0

    plugin = _Plugin()
    state = PluginStatePersistence("demo", tmp_path, backend="memory")

    assert await state.has_saved_state_async() is False
    assert await state.load_async(plugin) is False
    assert await state.get_state_info_async() is None

    plugin.counter = 42
    assert await state.save_async(plugin, ["counter"], reason="integration") is True
    plugin.counter = -1
    assert await state.load_async(plugin) is True
    assert plugin.counter == 42

    info = await state.get_state_info_async()
    assert isinstance(info, dict) and info["plugin_id"] == "demo"
    assert "counter" in info["data_keys"]

    assert await state.clear_async() is True
    assert await state.has_saved_state_async() is False
    assert await state.load_async(plugin) is False
