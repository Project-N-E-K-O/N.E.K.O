from __future__ import annotations

import asyncio
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
from plugin.sdk_v2.shared.models.exceptions import InvalidArgumentError, TransportError


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


def test_call_chain_guard_and_fallback_paths() -> None:
    class _Logger:
        def __init__(self) -> None:
            self.debug_messages: list[str] = []

        def warning(self, message: str, chain: str) -> None:
            raise RuntimeError(f"{message}:{chain}")

        def debug(self, message: str) -> None:
            self.debug_messages.append(message)

    class _LoggerDebugRaises:
        def warning(self, message: str, chain: str) -> None:
            raise RuntimeError(f"{message}:{chain}")

        def debug(self, message: str) -> None:
            raise RuntimeError(message)

    call_chain.CallChain.clear()
    assert call_chain.AsyncCallChain.is_available() is True

    split = call_chain._split_call_id("plugin")
    assert split.plugin_id == "plugin"
    assert split.event_type == "entry"
    assert split.event_id == "plugin"

    with call_chain.CallChain.track("p.entry:root", metadata={"depth": 0}) as root:
        assert call_chain.CallChain.get_current_call() is root
        assert call_chain.CallChain.get_root_call() is root
        assert call_chain.CallChain.is_in_call("p.entry:root") is True

        with pytest.raises(call_chain.CircularCallError, match="Circular call detected"):
            with call_chain.CallChain.track("p.entry:root"):
                pass

        with pytest.raises(call_chain.CallChainTooDeepError, match="Call chain too deep"):
            with call_chain.CallChain.track("p.entry:child", max_depth=1):
                pass

    logger = _Logger()
    with call_chain.CallChain.track("p.entry:root"):
        with call_chain.CallChain.track("p.entry:child", warn_depth=1, logger=logger):
            pass
    assert logger.debug_messages and "Failed to log call-chain warning" in logger.debug_messages[0]

    with call_chain.CallChain.track("p.entry:root"):
        with call_chain.CallChain.track("p.entry:child", warn_depth=1, logger=_LoggerDebugRaises()):
            pass


def test_storage_extended_types_contains_supported_types() -> None:
    expected = (datetime, date, timedelta, set, frozenset, Path)
    for item in expected:
        assert item in state.EXTENDED_TYPES


def test_runtime_contract_inits_construct() -> None:
    assert runtime_memory.MemoryClient(plugin_ctx=object()) is not None
    assert system_info.SystemInfo(plugin_ctx=object()) is not None
    assert message_plane.MessagePlaneTransport() is not None


@pytest.mark.asyncio
async def test_runtime_storage_transport_facade_methods() -> None:
    call_chain.CallChain.clear()
    async_chain = call_chain.AsyncCallChain()
    assert (await async_chain.get()).unwrap() == []
    assert (await async_chain.depth()).unwrap() == 0
    assert (await async_chain.contains("p", "run")).unwrap() is False
    async with async_chain.track("p", "entry", "async-run") as tracked:
        assert tracked.plugin_id == "p"
        assert tracked.event_type == "entry"
        assert tracked.event_id == "async-run"
        assert (await async_chain.depth()).unwrap() == 1
        assert (await async_chain.get_depth()).unwrap() == 1
        assert (await async_chain.contains("p", "async-run")).unwrap() is True
        assert (await async_chain.get_current_chain()).unwrap()[0].event_id == "async-run"
        assert (await async_chain.format_chain()).unwrap() == "p.entry:async-run"
    assert (await async_chain.depth()).unwrap() == 0
    with call_chain.CallChain.track("p.entry:run"):
        assert (await call_chain.get_call_chain()).unwrap()[0].event_id == "run"
        assert (await call_chain.get_call_depth()).unwrap() == 1
        assert (await call_chain.is_in_call_chain("p", "run")).unwrap() is True
    with call_chain.CallChain.track("p.entry:123"):
        assert (await call_chain.is_in_call_chain("p", "23")).unwrap() is False


@pytest.mark.asyncio
async def test_async_call_chain_isolated_per_task() -> None:
    call_chain.CallChain.clear()
    async_chain = call_chain.AsyncCallChain()
    seen: dict[str, list[str]] = {}

    async def _worker(plugin_id: str) -> None:
        async with async_chain.track(plugin_id, "entry", "run"):
            seen[f"{plugin_id}:inside"] = [
                f"{frame.plugin_id}.{frame.event_type}:{frame.event_id}"
                for frame in (await async_chain.get_current_chain()).unwrap()
            ]
            await asyncio.sleep(0)
            seen[f"{plugin_id}:after_yield"] = [
                f"{frame.plugin_id}.{frame.event_type}:{frame.event_id}"
                for frame in (await async_chain.get_current_chain()).unwrap()
            ]

    await asyncio.gather(_worker("p1"), _worker("p2"))

    assert seen["p1:inside"] == ["p1.entry:run"]
    assert seen["p1:after_yield"] == ["p1.entry:run"]
    assert seen["p2:inside"] == ["p2.entry:run"]
    assert seen["p2:after_yield"] == ["p2.entry:run"]
    assert (await async_chain.get_current_chain()).unwrap() == []

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
    assert (await kv_store.count()).unwrap() == 1
    assert (await kv_store.dump()).unwrap() == {"k": {"v": 1}}
    assert (await kv_store.delete("k")).unwrap() is True
    assert (await kv_store.clear()).unwrap() == 0
    assert (await kv_store.get("missing", "d")).unwrap() == "d"
    (await kv_store.set("a", 1)).unwrap()
    assert (await kv_store.exists("a")).unwrap() is True
    assert (await kv_store.keys()).unwrap() == ["a"]
    assert (await kv_store.count()).unwrap() == 1
    assert (await kv_store.dump()).unwrap() == {"a": 1}
    assert (await kv_store.delete("a")).unwrap() is True
    assert (await kv_store.clear()).unwrap() == 0
    (await kv_store.close()).unwrap()

    db = database.PluginDatabase(plugin_id="demo", plugin_dir=plugin_dir, enabled=True, db_name="plugin.db")
    assert (await db.create_all()).is_ok()
    session = (await db.session()).unwrap()
    cursor = await session.execute("SELECT 1")
    assert cursor.fetchone()[0] == 1
    kv = db.kv
    assert (await kv.set("k", [1, 2])).is_ok()
    assert (await kv.get("k")).unwrap() == [1, 2]
    assert (await kv.exists("k")).unwrap() is True
    assert (await kv.keys()).unwrap() == ["k"]
    assert (await kv.count()).unwrap() == 1
    assert (await kv.clear()).unwrap() == 1
    assert (await kv.set("k", [1, 2])).is_ok()
    assert (await kv.delete("k")).unwrap() is True
    assert (await kv.get("missing", "z")).unwrap() == "z"
    (await kv.set("x", {"v": True})).unwrap()
    assert (await kv.exists("x")).unwrap() is True
    assert (await kv.keys()).unwrap() == ["x"]
    assert (await kv.count()).unwrap() == 1
    assert (await kv.clear()).unwrap() == 1
    (await kv.set("x", {"v": True})).unwrap()
    assert (await kv.delete("x")).unwrap() is True
    (await db.create_all()).unwrap()
    (await db.close()).unwrap()
    (await db.drop_all()).unwrap()

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
    assert (await persistence.save(obj)).unwrap() is True
    assert (await persistence.load(obj)).unwrap() is True
    assert (await persistence.clear()).unwrap() is True
    assert (await persistence.snapshot()).unwrap() == {}
    assert (await persistence.collect_attrs(obj)).unwrap() == {"counter": 2, "when": {"__neko_type__": "datetime", "__neko_value__": "2024-01-01T01:01:01"}}
    assert (await persistence.restore_attrs(obj, {"counter": 9})).unwrap() == 1
    assert (await persistence.has_saved_state()).unwrap() is False
    assert (await persistence.get_state_info()).unwrap() is None


@pytest.mark.asyncio
async def test_shared_facade_validation_paths(tmp_path) -> None:
    plugin_dir = tmp_path / "facade_valid"
    plugin_dir.mkdir()

    kv_store = store.PluginStore(plugin_id="demo", plugin_dir=plugin_dir, enabled=True)
    assert (await kv_store.get("", None)).is_err()
    assert (await kv_store.set("", 1)).is_err()
    assert (await kv_store.delete("",)).is_err()
    assert (await kv_store.exists("",)).is_err()

    db = database.PluginDatabase(plugin_id="demo", plugin_dir=plugin_dir, enabled=True)
    assert (await db.kv.get("", None)).is_err()
    assert (await db.kv.set("", 1)).is_err()
    assert (await db.kv.delete("",)).is_err()

    mem = runtime_memory.MemoryClient(object())
    assert (await mem.query("", "q")).is_err()
    assert (await mem.query("bucket", "", timeout=1)).is_err()
    assert (await mem.query("bucket", "q", timeout=0)).is_err()
    assert (await mem.get("", limit=1)).is_err()
    assert (await mem.get("bucket", limit=0)).is_err()
    assert (await mem.get("bucket", timeout=0)).is_err()

    sys_client = system_info.SystemInfo(object())
    assert (await sys_client.get_system_config(timeout=0)).is_err()

    plane = message_plane.MessagePlaneTransport()
    assert (await plane.request("", {}, timeout=1)).is_err()
    assert (await plane.request("t", {}, timeout=0)).is_err()
    assert (await plane.publish("", {}, timeout=1)).is_err()
    assert (await plane.notify("", {}, timeout=1)).is_err()
    assert (await plane.subscribe("", lambda payload: payload)).is_err()
    assert (await plane.subscribe("t", object())).is_err()
    assert (await plane.unsubscribe("", None)).is_err()
    assert (await plane.unsubscribe("t", object())).is_err()

    with pytest.raises(state.InvalidArgumentError):
        state.PluginStatePersistence(plugin_id="demo", plugin_dir=plugin_dir, backend="weird")


@pytest.mark.asyncio
async def test_shared_memory_timeout_bool_and_impl_error_normalization() -> None:
    mem = runtime_memory.MemoryClient(object())
    timeout_error = await mem.query("bucket", "q", timeout=True)  # type: ignore[arg-type]
    assert timeout_error.is_err()
    assert isinstance(timeout_error.error, InvalidArgumentError)

    class _CustomSdkError(InvalidArgumentError):
        pass

    class _BoomCtx:
        plugin_id = "demo"
        _host_ctx = None

        async def query_memory(self, *args, **kwargs):
            raise _CustomSdkError("boom")

        class bus:
            class memory:
                @staticmethod
                async def get(*args, **kwargs):
                    raise _CustomSdkError("boom")

    _BoomCtx._host_ctx = _BoomCtx()  # type: ignore[attr-defined]
    mem_err = runtime_memory.MemoryClient(_BoomCtx())

    query_error = await mem_err.query("bucket", "q")
    assert query_error.is_err()
    assert isinstance(query_error.error, TransportError)
    assert query_error.error.context["op_name"] == "memory.query"

    get_error = await mem_err.get("bucket")
    assert get_error.is_err()
    assert isinstance(get_error.error, TransportError)
    assert get_error.error.context["op_name"] == "memory.get"
