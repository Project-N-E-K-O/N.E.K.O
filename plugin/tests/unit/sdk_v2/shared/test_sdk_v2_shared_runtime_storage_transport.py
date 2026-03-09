from __future__ import annotations

from dataclasses import fields
from datetime import date, datetime, timedelta
from pathlib import Path

import pytest

from plugin.sdk_v2.shared import runtime, storage, transport
from plugin.sdk_v2.shared.runtime import call_chain
from plugin.sdk_v2.shared.runtime import memory as runtime_memory
from plugin.sdk_v2.shared.runtime import system_info
from plugin.sdk_v2.shared.storage import database
from plugin.sdk_v2.shared.storage import state
from plugin.sdk_v2.shared.storage import store
from plugin.sdk_v2.shared.transport import message_plane


class _Ctx:
    plugin_id = "demo"
    logger = None
    config_path = Path("/tmp/demo/plugin.toml")
    _effective_config = {
        "plugin": {"store": {"enabled": True}, "database": {"enabled": True, "name": "data.db"}},
        "plugin_state": {"backend": "file"},
    }


def test_runtime_storage_transport_exports() -> None:
    for module in (runtime, storage, transport):
        for name in module.__all__:
            assert hasattr(module, name)


def test_runtime_call_chain_models_and_errors() -> None:
    frame = call_chain.CallChainFrame(plugin_id="p", event_type="entry", event_id="run")
    assert frame.event_id == "run"
    assert [f.name for f in fields(call_chain.CallChainFrame)] == ["plugin_id", "event_type", "event_id"]
    assert isinstance(call_chain.CircularCallError("e"), RuntimeError)
    assert isinstance(call_chain.CallChainTooDeepError("e"), RuntimeError)


def test_storage_extended_types_contains_supported_types() -> None:
    expected = (datetime, date, timedelta, set, frozenset, Path)
    for item in expected:
        assert item in state.EXTENDED_TYPES


def test_runtime_contract_inits_construct() -> None:
    assert runtime_memory.MemoryClient(_ctx=object()) is not None
    assert system_info.SystemInfo(_ctx=object()) is not None
    assert message_plane.MessagePlaneTransport() is not None


@pytest.mark.asyncio
async def test_runtime_storage_transport_facade_methods() -> None:
    async_chain = object.__new__(call_chain.AsyncCallChain)
    with pytest.raises(NotImplementedError):
        await async_chain.get()
    with pytest.raises(NotImplementedError):
        await async_chain.depth()
    with pytest.raises(NotImplementedError):
        await async_chain.contains("p", "run")
    with pytest.raises(NotImplementedError):
        await call_chain.get_call_chain()
    with pytest.raises(NotImplementedError):
        await call_chain.get_call_depth()
    with pytest.raises(NotImplementedError):
        await call_chain.is_in_call_chain("p", "run")

    mem = runtime_memory.MemoryClient(object())
    assert (await mem.query("bucket", "q")).is_err()
    assert (await mem.get("bucket")).is_err()

    sys_info_client = system_info.SystemInfo(object())
    assert (await sys_info_client.get_system_config()).is_err()
    assert (await sys_info_client.get_python_env()).is_ok()

    plane = message_plane.MessagePlaneTransport()
    assert (await plane.request("topic", {})).is_err()
    assert (await plane.notify("topic", {})).is_ok()
    assert (await plane.publish("topic", {})).is_ok()
    assert (await plane.subscribe("topic", handler=lambda payload: payload)).is_ok()
    assert (await plane.unsubscribe("topic")).is_ok()


def test_runtime_contract_placeholder_classes() -> None:
    assert call_chain.CallChain.__name__ == "CallChain"
    assert database.AsyncSessionProtocol.__name__ == "AsyncSessionProtocol"


@pytest.mark.asyncio
async def test_shared_storage_facades_work(tmp_path) -> None:
    plugin_dir = tmp_path / "plugin"
    plugin_dir.mkdir()

    kv_store = store.PluginStore(plugin_id="demo", plugin_dir=plugin_dir, enabled=True)
    assert (await kv_store.get("missing", "x")).unwrap() == "x"
    assert (await kv_store.set("k", {"v": 1})).is_ok()
    assert (await kv_store.exists("k")).unwrap() is True
    assert (await kv_store.get("k")).unwrap() == {"v": 1}
    assert (await kv_store.keys()).unwrap() == ["k"]
    assert (await kv_store.delete("k")).unwrap() is True
    assert (await kv_store.clear()).unwrap() == 0
    assert await kv_store.get_async("missing", "d") == "d"
    await kv_store.set_async("a", 1)
    assert await kv_store.exists_async("a") is True
    assert await kv_store.keys_async() == ["a"]
    assert await kv_store.delete_async("a") is True
    assert await kv_store.clear_async() == 0

    db = database.PluginDatabase(plugin_id="demo", plugin_dir=plugin_dir, enabled=True, db_name="plugin.db")
    assert (await db.create_all()).is_ok()
    session = (await db.session()).unwrap()
    cursor = await session.execute("SELECT 1")
    assert cursor.fetchone()[0] == 1
    kv = db.kv
    assert (await kv.set("k", [1, 2])).is_ok()
    assert (await kv.get("k")).unwrap() == [1, 2]
    assert (await kv.delete("k")).unwrap() is True
    assert await kv.get_async("missing", "z") == "z"
    await kv.set_async("x", {"v": True})
    assert await kv.delete_async("x") is True
    assert (await db.drop_all()).is_ok()

    class _StateObj:
        __freezable__ = ["counter", "when"]

        def __init__(self) -> None:
            self.counter = 2
            self.when = datetime(2024, 1, 1, 1, 1, 1)

    obj = _StateObj()
    persistence = state.PluginStatePersistence(plugin_id="demo", plugin_dir=plugin_dir, backend="file")
    assert (await persistence.save(obj)).unwrap() is True
    snapshot = (await persistence.snapshot()).unwrap()
    assert snapshot["counter"] == 2
    obj.counter = 0
    assert (await persistence.load(obj)).unwrap() is True
    assert obj.counter == 2
    assert (await persistence.clear()).unwrap() is True
    assert await persistence.save_async(obj) is True
    assert await persistence.load_async(obj) is True
    assert await persistence.clear_async() is True
    assert await persistence.snapshot_async() == {}
