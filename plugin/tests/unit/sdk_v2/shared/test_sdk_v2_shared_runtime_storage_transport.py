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


class _StateTopEnum(state.Enum):
    A = "a"


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


def test_runtime_contract_inits_raise() -> None:
    with pytest.raises(NotImplementedError):
        runtime_memory.MemoryClient(_ctx=object())
    with pytest.raises(NotImplementedError):
        system_info.SystemInfo(_ctx=object())
    with pytest.raises(NotImplementedError):
        message_plane.MessagePlaneTransport()


@pytest.mark.asyncio
async def test_runtime_storage_transport_contract_methods_raise() -> None:
    async_chain = object.__new__(call_chain.AsyncCallChain)
    mem = object.__new__(runtime_memory.MemoryClient)
    sys_info = object.__new__(system_info.SystemInfo)
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


@pytest.mark.asyncio
async def test_store_database_and_state_are_working(tmp_path) -> None:
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

    disabled_store = store.PluginStore(plugin_id="demo", plugin_dir=plugin_dir, enabled=False)
    assert (await disabled_store.get("x", "d")).unwrap() == "d"
    assert (await disabled_store.exists("x")).unwrap() is False

    db = database.PluginDatabase(plugin_id="demo", plugin_dir=plugin_dir, enabled=True, db_name="plugin.db")
    assert (await db.create_all()).is_ok()
    session = (await db.session()).unwrap()
    cursor = await session.execute("SELECT 1")
    assert cursor.fetchone()[0] == 1
    await session.commit()
    await session.rollback()
    await session.close()

    kv = db.kv
    assert (await kv.get("missing", "d")).unwrap() == "d"
    assert (await kv.set("k", [1, 2])).is_ok()
    assert (await kv.get("k")).unwrap() == [1, 2]
    assert (await kv.delete("k")).unwrap() is True
    assert await kv.get_async("missing", "z") == "z"
    await kv.set_async("x", {"v": True})
    assert await kv.delete_async("x") is True
    assert (await db.drop_all()).is_ok()

    disabled_db = database.PluginDatabase(plugin_id="demo", plugin_dir=plugin_dir, enabled=False)
    assert (await disabled_db.create_all()).is_ok()
    assert (await disabled_db.drop_all()).is_ok()
    assert (await disabled_db.session()).is_err()

    class _StateObj:
        __freezable__ = ["counter", "when", "tags", "path"]

        def __init__(self) -> None:
            self.counter = 2
            self.when = datetime(2024, 1, 1, 1, 1, 1)
            self.tags = {"a", "b"}
            self.path = plugin_dir

    obj = _StateObj()
    persistence = state.PluginStatePersistence(plugin_id="demo", plugin_dir=plugin_dir, backend="file")
    assert (await persistence.save(obj)).unwrap() is True
    snapshot = (await persistence.snapshot()).unwrap()
    assert snapshot["counter"] == 2
    obj.counter = 0
    assert (await persistence.load(obj)).unwrap() is True
    assert obj.counter == 2
    assert (await persistence.clear()).unwrap() is True

    mem_obj = _StateObj()
    memory_state = state.PluginStatePersistence(plugin_id="demo", plugin_dir=plugin_dir, backend="memory")
    assert await memory_state.save_async(mem_obj) is True
    mem_obj.counter = 0
    assert await memory_state.load_async(mem_obj) is True
    assert mem_obj.counter == 2
    assert await memory_state.clear_async() is True
    assert await memory_state.snapshot_async() == {}

    off_state = state.PluginStatePersistence(plugin_id="demo", plugin_dir=plugin_dir, backend="off")
    assert (await off_state.save(obj)).unwrap() is False
    assert (await off_state.load(obj)).unwrap() is False


def test_state_extended_type_helpers() -> None:
    class _Enum(state.Enum):
        A = "a"

    assert state._serialize_extended(datetime(2024, 1, 1, 0, 0, 0)) is not None
    assert state._serialize_extended(date(2024, 1, 1)) is not None
    assert state._serialize_extended(timedelta(seconds=3)) is not None
    assert state._serialize_extended(_Enum.A) is not None
    assert state._serialize_extended({"a"}) is not None
    assert state._serialize_extended(frozenset({"a"})) is not None
    assert state._serialize_extended(Path("/tmp/x")) is not None
    assert state._serialize_extended(object()) is None

    assert state._deserialize_extended({"__neko_type__": "datetime", "__neko_value__": "2024-01-01T00:00:00"}).year == 2024
    assert state._deserialize_extended({"__neko_type__": "date", "__neko_value__": "2024-01-01"}).year == 2024
    assert state._deserialize_extended({"__neko_type__": "timedelta", "__neko_value__": 3}).total_seconds() == 3
    assert state._deserialize_extended({"__neko_type__": "set", "__neko_value__": ["a"]}) == {"a"}
    assert state._deserialize_extended({"__neko_type__": "frozenset", "__neko_value__": ["a"]}) == frozenset({"a"})
    assert state._deserialize_extended({"__neko_type__": "path", "__neko_value__": "/tmp/x"}) == Path("/tmp/x")
    assert state._deserialize_extended({"__neko_type__": "enum", "enum_class": "missing.Enum", "__neko_value__": "a"}) == "a"
    assert state._deserialize_extended({"__neko_type__": "unknown", "__neko_value__": 1}) is None


@pytest.mark.asyncio
async def test_store_database_state_error_paths(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    plugin_dir = tmp_path / "plugin_err"
    plugin_dir.mkdir()

    kv_store = store.PluginStore(plugin_id="demo", plugin_dir=plugin_dir, enabled=True)
    kv_store._get_conn().execute("INSERT OR REPLACE INTO kv_store (key, value, created_at, updated_at) VALUES (?, ?, 0, 0)", ("bad", store._pack(b"x")))
    kv_store._get_conn().commit()
    assert (await kv_store.get("bad", "d")).unwrap() == "d"
    kv_store._local.conn = None
    original_store_connect = store.sqlite3.connect
    monkeypatch.setattr(store.sqlite3, "connect", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    assert (await kv_store.get("x")).is_err()
    assert (await kv_store.set("x", 1)).is_err()
    assert (await kv_store.delete("x")).is_err()
    assert (await kv_store.exists("x")).is_err()
    assert (await kv_store.keys()).is_err()
    assert (await kv_store.clear()).is_err()
    monkeypatch.setattr(store.sqlite3, "connect", original_store_connect)

    db = database.PluginDatabase(plugin_id="demo", plugin_dir=plugin_dir, enabled=True)
    db._local.conn = None
    original_db_connect = database.sqlite3.connect
    monkeypatch.setattr(database.sqlite3, "connect", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    assert (await db.create_all()).is_err()
    assert (await db.session()).is_err()
    monkeypatch.setattr(database.sqlite3, "connect", original_db_connect)

    failing_db = database.PluginDatabase(plugin_id="demo", plugin_dir=plugin_dir, enabled=True)
    monkeypatch.setattr(type(failing_db._db_path), "unlink", lambda self: (_ for _ in ()).throw(RuntimeError("boom")))
    assert (await failing_db.drop_all()).is_err()

    kv = database.PluginKVStore(database=database.PluginDatabase(plugin_id="demo", plugin_dir=plugin_dir, enabled=False))
    assert (await kv.get("x", "d")).unwrap() == "d"
    assert (await kv.set("x", 1)).is_ok()
    assert (await kv.delete("x")).unwrap() is False

    working_db = database.PluginDatabase(plugin_id="demo", plugin_dir=plugin_dir, enabled=True)
    kv2 = working_db.kv
    kv2._ensure_table()
    working_db._get_conn().execute(f"INSERT OR REPLACE INTO {kv2._TABLE_NAME} (key, value, created_at, updated_at) VALUES (?, ?, 0, 0)", ("bad", database._pack(b"x")))
    working_db._get_conn().commit()
    assert (await kv2.get("bad", "d")).unwrap() == "d"
    kv2._db._local.conn = None
    monkeypatch.setattr(database.sqlite3, "connect", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom2")))
    assert (await kv2.get("x")).is_err()
    assert (await kv2.set("x", 1)).is_err()
    assert (await kv2.delete("x")).is_err()

    class _Obj:
        __freezable__ = ["value", "enum_val", "items", "fitems", "path", "custom"]

        def __init__(self) -> None:
            class _Enum(state.Enum):
                A = "a"
            self.value = {"nested": (1, 2)}
            self.enum_val = _Enum.A
            self.items = {"x"}
            self.fitems = frozenset({"y"})
            self.path = plugin_dir
            self.custom = 1

        def __freeze_serialize__(self, key: str, value: object):
            if key == "custom":
                return {"custom": 2}
            return None

        def __freeze_deserialize__(self, key: str, value: object):
            if key == "custom":
                return 99
            return None

    persistence = state.PluginStatePersistence(plugin_id="demo", plugin_dir=plugin_dir, backend="memory")
    obj = _Obj()
    payload = persistence._collect_snapshot(obj)
    assert payload["custom"] == {"custom": 2}
    assert isinstance(payload["value"]["nested"], list)
    assert persistence._deserialize_value("custom", {"custom": 2}, obj) == 99
    assert (await persistence.save(obj)).unwrap() is True
    obj.custom = 0
    assert (await persistence.load(obj)).unwrap() is True
    assert obj.custom == 99

    bad = state.PluginStatePersistence(plugin_id="demo", plugin_dir=plugin_dir, backend="memory")
    bad._memory_state = b"bad"
    assert (await bad.load(obj)).unwrap() is False
    assert (await bad.snapshot()).unwrap() == {}

    file_state = state.PluginStatePersistence(plugin_id="demo", plugin_dir=plugin_dir, backend="file")
    file_state._state_path.write_bytes(b"bad")
    assert (await file_state.load(obj)).unwrap() is False
    assert (await file_state.snapshot()).unwrap() == {}

    original_unpack = state._unpack
    monkeypatch.setattr(state, "_unpack", lambda _data: (_ for _ in ()).throw(RuntimeError("boom")))
    assert (await bad.load(obj)).is_err()
    assert (await bad.snapshot()).is_err()
    monkeypatch.setattr(state, "_unpack", original_unpack)


def test_store_internal_disabled_and_prefix_paths(tmp_path) -> None:
    plugin_dir = tmp_path / "store_internal"
    disabled = store.PluginStore(plugin_id="demo", plugin_dir=plugin_dir, enabled=False)
    with pytest.raises(RuntimeError):
        disabled._get_conn()
    assert disabled._set_sync("k", 1) is None
    assert disabled._delete_sync("k") is False
    assert disabled._exists_sync("k") is False
    assert disabled._keys_sync("p") == []
    assert disabled._clear_sync() == 0

    enabled = store.PluginStore(plugin_id="demo", plugin_dir=plugin_dir, enabled=True)
    enabled._set_sync("pre_one", 1)
    enabled._set_sync("pre_two", 2)
    assert enabled._keys_sync("pre_") == ["pre_one", "pre_two"]


@pytest.mark.asyncio
async def test_database_kv_additional_internal_paths(tmp_path) -> None:
    plugin_dir = tmp_path / "db_internal"
    plugin_dir.mkdir()
    db = database.PluginDatabase(plugin_id="demo", plugin_dir=plugin_dir, enabled=True)
    kv = db.kv
    assert db.kv is kv
    kv._ensure_table()
    db._get_conn().execute(f"INSERT OR REPLACE INTO {kv._TABLE_NAME} (key, value, created_at, updated_at) VALUES (?, ?, 0, 0)", ("bad2", database._pack(b"x")))
    db._get_conn().commit()
    assert kv._get_sync("bad2", "d") == "d"


@pytest.mark.asyncio
async def test_state_additional_internal_paths(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    plugin_dir = tmp_path / "state_internal"
    plugin_dir.mkdir()

    enum_payload = {"__neko_type__": "enum", "enum_class": f"{_StateTopEnum.__module__}.{_StateTopEnum.__name__}", "__neko_value__": "a"}
    assert state._deserialize_extended(enum_payload) == _StateTopEnum.A

    class _Obj:
        __freezable__ = ["list_val", "tuple_val", "plain", "ext_custom"]

        def __init__(self) -> None:
            self.list_val = [1, 2]
            self.tuple_val = (3, 4)
            self.plain = {"a": [1, 2]}
            self.ext_custom = datetime(2024, 1, 1, 0, 0, 0)

        def __freeze_deserialize__(self, key: str, value: object):
            if key == "ext_custom":
                return "customized"
            return None

    persistence = state.PluginStatePersistence(plugin_id="demo", plugin_dir=plugin_dir, backend="memory")
    obj = _Obj()
    snapshot = persistence._collect_snapshot(obj)
    assert isinstance(snapshot["list_val"], list)
    assert isinstance(snapshot["tuple_val"], list)
    restored = persistence._deserialize_value("ext_custom", state._serialize_extended(obj.ext_custom), obj)
    assert restored == "customized"
    assert persistence._load_sync(obj) is False
    assert persistence._snapshot_sync() == {}

    file_state = state.PluginStatePersistence(plugin_id="demo", plugin_dir=plugin_dir, backend="file")
    assert file_state._load_sync(obj) is False
    assert file_state._snapshot_sync() == {}
    assert file_state._clear_sync() is False

    file_state._state_path.write_bytes(state._pack({"data": []}))
    assert file_state._load_sync(obj) is False
    file_state._state_path.write_bytes(state._pack([]))
    assert file_state._snapshot_sync() == {}

    monkeypatch.setattr(state, "_pack", lambda _value: (_ for _ in ()).throw(RuntimeError("boom")))
    assert (await persistence.save(obj)).is_err()
    from importlib import reload
    reload(state)


@pytest.mark.asyncio
async def test_store_and_db_unpack_error_and_state_clear_error(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    plugin_dir = tmp_path / "final_cov"
    plugin_dir.mkdir()

    kv_store = store.PluginStore(plugin_id="demo", plugin_dir=plugin_dir, enabled=True)
    await kv_store.set("k", {"v": 1})
    monkeypatch.setattr(store, "_unpack", lambda _data: (_ for _ in ()).throw(RuntimeError("boom")))
    assert (await kv_store.get("k", "d")).unwrap() == "d"

    db = database.PluginDatabase(plugin_id="demo", plugin_dir=plugin_dir, enabled=True)
    kv = db.kv
    await kv.set("k", {"v": 1})
    monkeypatch.setattr(database, "_unpack", lambda _data: (_ for _ in ()).throw(RuntimeError("boom")))
    assert (await kv.get("k", "d")).unwrap() == "d"

    persistence = state.PluginStatePersistence(plugin_id="demo", plugin_dir=plugin_dir, backend="memory")
    monkeypatch.setattr(persistence, "_clear_sync", lambda: (_ for _ in ()).throw(RuntimeError("boom")))
    assert (await persistence.clear()).is_err()
