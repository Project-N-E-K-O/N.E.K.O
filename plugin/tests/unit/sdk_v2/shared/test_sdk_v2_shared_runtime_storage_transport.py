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


def test_contract_inits_raise() -> None:
    with pytest.raises(NotImplementedError):
        runtime_memory.MemoryClient(_ctx=object())
    with pytest.raises(NotImplementedError):
        system_info.SystemInfo(_ctx=object())

    with pytest.raises(NotImplementedError):
        store.PluginStore()
    with pytest.raises(NotImplementedError):
        state.PluginStatePersistence()
    with pytest.raises(NotImplementedError):
        database.PluginDatabase()
    with pytest.raises(NotImplementedError):
        database.PluginKVStore()
    with pytest.raises(NotImplementedError):
        message_plane.MessagePlaneTransport()


@pytest.mark.asyncio
async def test_runtime_storage_transport_contract_methods_raise() -> None:
    async_chain = object.__new__(call_chain.AsyncCallChain)
    mem = object.__new__(runtime_memory.MemoryClient)
    sys_info = object.__new__(system_info.SystemInfo)
    kv_store = object.__new__(store.PluginStore)
    persistence = object.__new__(state.PluginStatePersistence)
    db = object.__new__(database.PluginDatabase)
    db_kv = object.__new__(database.PluginKVStore)
    plane = object.__new__(message_plane.MessagePlaneTransport)

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

    with pytest.raises(NotImplementedError):
        await mem.query("bucket", "q")
    with pytest.raises(NotImplementedError):
        await mem.get("bucket")

    with pytest.raises(NotImplementedError):
        await sys_info.get_system_config()
    with pytest.raises(NotImplementedError):
        await sys_info.get_python_env()

    with pytest.raises(NotImplementedError):
        await kv_store.get("k")
    with pytest.raises(NotImplementedError):
        await kv_store.set("k", "v")
    with pytest.raises(NotImplementedError):
        await kv_store.delete("k")
    with pytest.raises(NotImplementedError):
        await kv_store.exists("k")
    with pytest.raises(NotImplementedError):
        await kv_store.keys()
    with pytest.raises(NotImplementedError):
        await kv_store.clear()

    with pytest.raises(NotImplementedError):
        await persistence.save(instance=object())
    with pytest.raises(NotImplementedError):
        await persistence.load(instance=object())
    with pytest.raises(NotImplementedError):
        await persistence.clear()
    with pytest.raises(NotImplementedError):
        await persistence.snapshot()

    with pytest.raises(NotImplementedError):
        await db.create_all()
    with pytest.raises(NotImplementedError):
        await db.drop_all()
    with pytest.raises(NotImplementedError):
        await db.session()

    with pytest.raises(NotImplementedError):
        await db_kv.get("k")
    with pytest.raises(NotImplementedError):
        await db_kv.set("k", "v")
    with pytest.raises(NotImplementedError):
        await db_kv.delete("k")

    with pytest.raises(NotImplementedError):
        await plane.request("topic", {})
    with pytest.raises(NotImplementedError):
        await plane.notify("topic", {})
    with pytest.raises(NotImplementedError):
        await plane.publish("topic", {})
    with pytest.raises(NotImplementedError):
        await plane.subscribe("topic", handler=lambda payload: payload)
    with pytest.raises(NotImplementedError):
        await plane.unsubscribe("topic")


def test_runtime_contract_placeholder_classes() -> None:
    assert call_chain.CallChain.__name__ == "CallChain"
    assert database.AsyncSessionProtocol.__name__ == "AsyncSessionProtocol"
