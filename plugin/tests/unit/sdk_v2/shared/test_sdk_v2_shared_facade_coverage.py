from __future__ import annotations

from pathlib import Path

import pytest

from plugin.sdk_v2.shared.runtime import memory as shared_memory
from plugin.sdk_v2.shared.runtime import system_info as shared_system_info
from plugin.sdk_v2.shared.storage import database as shared_database
from plugin.sdk_v2.shared.storage import state as shared_state
from plugin.sdk_v2.shared.storage import store as shared_store
from plugin.sdk_v2.shared.transport import message_plane as shared_plane


@pytest.mark.asyncio
async def test_shared_memory_facade_validation_and_error_wrapping() -> None:
    mem = shared_memory.MemoryClient(object())
    assert (await mem.query("", "q")).is_err()
    assert (await mem.query("bucket", "", timeout=1)).is_err()
    assert (await mem.query("bucket", "q", timeout=0)).is_err()
    assert (await mem.get("", limit=1)).is_err()
    assert (await mem.get("bucket", limit=0)).is_err()
    assert (await mem.get("bucket", timeout=0)).is_err()

    async def boom_query(*args, **kwargs):
        raise RuntimeError("boom")

    async def boom_get(*args, **kwargs):
        raise RuntimeError("boom")

    mem._impl.query = boom_query  # type: ignore[method-assign]
    mem._impl.get = boom_get  # type: ignore[method-assign]
    assert (await mem.query("bucket", "q")).is_err()
    assert (await mem.get("bucket")).is_err()


@pytest.mark.asyncio
async def test_shared_system_info_facade_validation_and_error_wrapping() -> None:
    sys_info = shared_system_info.SystemInfo(object())
    assert (await sys_info.get_system_config(timeout=0)).is_err()
    assert (await sys_info.get_server_settings(timeout=0)).is_err()

    async def boom_config(*args, **kwargs):
        raise RuntimeError("boom")

    async def boom_env(*args, **kwargs):
        raise RuntimeError("boom")

    sys_info._impl.get_system_config = boom_config  # type: ignore[method-assign]
    sys_info._impl.get_server_settings = boom_config  # type: ignore[method-assign]
    sys_info._impl.get_python_env = boom_env  # type: ignore[method-assign]
    assert (await sys_info.get_system_config(timeout=1)).is_err()
    assert (await sys_info.get_server_settings(timeout=1)).is_err()
    assert (await sys_info.get_python_env()).is_err()


@pytest.mark.asyncio
async def test_shared_store_facade_validation_and_async_helpers(tmp_path) -> None:
    plugin_dir = tmp_path / "shared_store"
    plugin_dir.mkdir()
    kv = shared_store.PluginStore(plugin_id="demo", plugin_dir=plugin_dir, enabled=True)
    assert (await kv.get("", None)).is_err()
    assert (await kv.set("", 1)).is_err()
    assert (await kv.delete("")).is_err()
    assert (await kv.exists("")).is_err()

    await kv.set_async("a", 1)
    assert await kv.get_async("a") == 1
    assert await kv.exists_async("a") is True
    assert await kv.keys_async() == ["a"]
    assert await kv.delete_async("a") is True
    assert await kv.clear_async() == 0

    async def boom(*args, **kwargs):
        raise RuntimeError("boom")

    kv._impl.get = boom  # type: ignore[method-assign]
    kv._impl.set = boom  # type: ignore[method-assign]
    kv._impl.delete = boom  # type: ignore[method-assign]
    kv._impl.exists = boom  # type: ignore[method-assign]
    kv._impl.keys = boom  # type: ignore[method-assign]
    kv._impl.clear = boom  # type: ignore[method-assign]
    assert (await kv.get("a")).is_err()
    assert (await kv.set("a", 1)).is_err()
    assert (await kv.delete("a")).is_err()
    assert (await kv.exists("a")).is_err()
    assert (await kv.keys()).is_err()
    assert (await kv.clear()).is_err()


@pytest.mark.asyncio
async def test_shared_database_and_state_facades(tmp_path) -> None:
    plugin_dir = tmp_path / "shared_db"
    plugin_dir.mkdir()
    db = shared_database.PluginDatabase(plugin_id="demo", plugin_dir=plugin_dir, enabled=True)
    assert db.kv is db.kv
    session = (await db.session()).unwrap()
    cursor = await session.execute("SELECT 1")
    assert cursor.fetchone()[0] == 1

    kv = db.kv
    assert (await kv.get("", None)).is_err()
    assert (await kv.set("", 1)).is_err()
    assert (await kv.delete("")).is_err()
    await kv.set_async("a", 1)
    assert await kv.get_async("a") == 1
    assert await kv.delete_async("a") is True

    async def boom(*args, **kwargs):
        raise RuntimeError("boom")

    kv._impl.get = boom  # type: ignore[method-assign]
    kv._impl.set = boom  # type: ignore[method-assign]
    kv._impl.delete = boom  # type: ignore[method-assign]
    assert (await kv.get("a")).is_err()
    assert (await kv.set("a", 1)).is_err()
    assert (await kv.delete("a")).is_err()

    db._impl.create_all = boom  # type: ignore[method-assign]
    db._impl.drop_all = boom  # type: ignore[method-assign]
    db._impl.session = boom  # type: ignore[method-assign]
    assert (await db.create_all()).is_err()
    assert (await db.drop_all()).is_err()
    assert (await db.session()).is_err()

    persistence = shared_state.PluginStatePersistence(plugin_id="demo", plugin_dir=plugin_dir, backend="weird")
    assert persistence.backend == "file"

    class _Obj:
        __freezable__ = ["counter"]
        def __init__(self) -> None:
            self.counter = 1

    obj = _Obj()
    assert await persistence.save_async(obj) in {True, False}
    assert isinstance(await persistence.snapshot_async(), dict)

    persistence._impl.save = boom  # type: ignore[method-assign]
    persistence._impl.load = boom  # type: ignore[method-assign]
    persistence._impl.clear = boom  # type: ignore[method-assign]
    persistence._impl.snapshot = boom  # type: ignore[method-assign]
    assert (await persistence.save(obj)).is_err()
    assert (await persistence.load(obj)).is_err()
    assert (await persistence.clear()).is_err()
    assert (await persistence.snapshot()).is_err()
    assert await persistence.save_async(obj) is False
    assert await persistence.load_async(obj) is False
    assert await persistence.clear_async() is False
    assert await persistence.snapshot_async() == {}


@pytest.mark.asyncio
async def test_shared_message_plane_facade_validation_and_errors() -> None:
    plane = shared_plane.MessagePlaneTransport()
    assert (await plane.request("", {}, timeout=1)).is_err()
    assert (await plane.request("t", {}, timeout=0)).is_err()
    assert (await plane.notify("", {}, timeout=1)).is_err()
    assert (await plane.notify("t", {}, timeout=0)).is_err()
    assert (await plane.publish("", {}, timeout=1)).is_err()
    assert (await plane.publish("t", {}, timeout=0)).is_err()
    assert (await plane.subscribe("", lambda payload: payload)).is_err()
    assert (await plane.subscribe("t", object())).is_err()
    assert (await plane.unsubscribe("", None)).is_err()
    assert (await plane.unsubscribe("t", object())).is_err()

    async def boom(*args, **kwargs):
        raise RuntimeError("boom")

    plane._impl.request = boom  # type: ignore[method-assign]
    plane._impl.notify = boom  # type: ignore[method-assign]
    plane._impl.publish = boom  # type: ignore[method-assign]
    plane._impl.subscribe = boom  # type: ignore[method-assign]
    plane._impl.unsubscribe = boom  # type: ignore[method-assign]
    assert (await plane.request("t", {})).is_err()
    assert (await plane.notify("t", {})).is_err()
    assert (await plane.publish("t", {})).is_err()
    assert (await plane.subscribe("t", lambda payload: payload)).is_err()
    assert (await plane.unsubscribe("t", None)).is_err()


@pytest.mark.asyncio
async def test_shared_store_and_db_new_async_helpers(tmp_path) -> None:
    plugin_dir = tmp_path / "shared_store_extra"
    plugin_dir.mkdir()

    kv = shared_store.PluginStore(plugin_id="demo", plugin_dir=plugin_dir, enabled=True)
    await kv.set_async("a", 1)
    assert (await kv.count()).unwrap() == 1
    assert (await kv.dump()).unwrap() == {"a": 1}
    assert await kv.count_async() == 1
    assert await kv.dump_async() == {"a": 1}
    await kv.close_async()

    db = shared_database.PluginDatabase(plugin_id="demo", plugin_dir=plugin_dir, enabled=True)
    await db.create_all_async()
    kvdb = db.kv
    await kvdb.set_async("x", 1)
    assert (await kvdb.exists("x")).unwrap() is True
    assert (await kvdb.keys()).unwrap() == ["x"]
    assert (await kvdb.count()).unwrap() == 1
    assert (await kvdb.clear()).unwrap() == 1
    await kvdb.set_async("x", 1)
    assert await kvdb.exists_async("x") is True
    assert await kvdb.keys_async() == ["x"]
    assert await kvdb.count_async() == 1
    assert await kvdb.clear_async() == 1
    await db.close_async()
    await db.drop_all_async()

    persistence = shared_state.PluginStatePersistence(plugin_id="demo", plugin_dir=plugin_dir, backend="file")
    class _Obj:
        __freezable__ = ["counter"]
        def __init__(self):
            self.counter = 1
    obj = _Obj()
    assert await persistence.collect_attrs_async(obj) == {"counter": 1}
    assert await persistence.restore_attrs_async(obj, {"counter": 2}) == 1
    assert await persistence.has_saved_state_async() is False
    assert await persistence.get_state_info_async() is None


@pytest.mark.asyncio
async def test_shared_storage_new_method_branch_coverage(tmp_path) -> None:
    plugin_dir = tmp_path / "shared_storage_methods"
    plugin_dir.mkdir()

    kv = shared_store.PluginStore(plugin_id="demo", plugin_dir=plugin_dir, enabled=True)
    await kv.set_async("a", 1)
    assert await kv.count_async() == 1
    assert await kv.dump_async() == {"a": 1}
    await kv.close_async()

    db = shared_database.PluginDatabase(plugin_id="demo", plugin_dir=plugin_dir, enabled=True)
    await db.create_all_async()
    kvdb = db.kv
    await kvdb.set_async("x", 1)
    assert await kvdb.exists_async("x") is True
    assert await kvdb.keys_async() == ["x"]
    assert await kvdb.count_async() == 1
    assert await kvdb.clear_async() == 1
    await db.close_async()
    await db.drop_all_async()

    persistence = shared_state.PluginStatePersistence(plugin_id="demo", plugin_dir=plugin_dir, backend="file")
    class _Obj:
        __freezable__ = ["counter"]
        def __init__(self):
            self.counter = 1
    obj = _Obj()
    assert await persistence.collect_attrs_async(obj) == {"counter": 1}
    assert await persistence.restore_attrs_async(obj, {"counter": 2}) == 1
    assert await persistence.has_saved_state_async() is False
    assert await persistence.get_state_info_async() is None


@pytest.mark.asyncio
async def test_shared_storage_facade_error_wrappers_extra(tmp_path) -> None:
    plugin_dir = tmp_path / "shared_facade_remaining"
    plugin_dir.mkdir()

    kv = shared_store.PluginStore(plugin_id="demo", plugin_dir=plugin_dir, enabled=True)
    async def boom(*args, **kwargs):
        raise RuntimeError("boom")
    kv._impl.count = boom  # type: ignore[method-assign]
    kv._impl.dump = boom  # type: ignore[method-assign]
    kv._impl.close = boom  # type: ignore[method-assign]
    assert (await kv.count()).is_err()
    assert (await kv.dump()).is_err()
    assert (await kv.close()).is_err()

    db = shared_database.PluginDatabase(plugin_id="demo", plugin_dir=plugin_dir, enabled=True)
    kvdb = db.kv
    assert (await kvdb.exists("",)).is_err()
    kvdb._impl.exists = boom  # type: ignore[method-assign]
    kvdb._impl.keys = boom  # type: ignore[method-assign]
    kvdb._impl.clear = boom  # type: ignore[method-assign]
    kvdb._impl.count = boom  # type: ignore[method-assign]
    assert (await kvdb.exists("x")).is_err()
    assert (await kvdb.keys()).is_err()
    assert (await kvdb.clear()).is_err()
    assert (await kvdb.count()).is_err()

    db._impl.close = boom  # type: ignore[method-assign]
    assert (await db.close()).is_err()
