from __future__ import annotations

from datetime import date, datetime, timedelta
from enum import Enum
from pathlib import Path

import pytest

from plugin.sdk_v2.public.storage import database, state, store


class _StateTopEnum(Enum):
    A = "a"


@pytest.mark.asyncio
async def test_public_store_database_and_state_behaviors(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    plugin_dir = tmp_path / "public_storage"
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
    with pytest.raises(RuntimeError):
        disabled_store._get_conn()
    assert disabled_store._set_sync("x", 1) is None
    assert disabled_store._delete_sync("x") is False
    assert disabled_store._exists_sync("x") is False
    assert disabled_store._keys_sync() == []
    assert disabled_store._clear_sync() == 0

    kv_store._get_conn().execute("INSERT OR REPLACE INTO kv_store (key, value, created_at, updated_at) VALUES (?, ?, 0, 0)", ("bad", store._pack(b"x")))
    kv_store._get_conn().commit()
    assert (await kv_store.get("bad", "d")).unwrap() == "d"

    db = database.PluginDatabase(plugin_id="demo", plugin_dir=plugin_dir, enabled=True, db_name="plugin.db")
    assert (await db.create_all()).is_ok()
    session = (await db.session()).unwrap()
    cursor = await session.execute("SELECT 1")
    assert cursor.fetchone()[0] == 1
    await session.commit()
    await session.rollback()
    await session.close()

    kv = db.kv
    assert db.kv is kv
    assert (await kv.get("missing", "d")).unwrap() == "d"
    assert (await kv.set("k", [1, 2])).is_ok()
    assert (await kv.get("k")).unwrap() == [1, 2]
    assert (await kv.delete("k")).unwrap() is True
    assert await kv.get_async("missing", "z") == "z"
    await kv.set_async("x", {"v": True})
    assert await kv.delete_async("x") is True
    kv._ensure_table()
    db._get_conn().execute(f"INSERT OR REPLACE INTO {kv._TABLE_NAME} (key, value, created_at, updated_at) VALUES (?, ?, 0, 0)", ("bad2", database._pack(b"x")))
    db._get_conn().commit()
    assert (await kv.get("bad2", "d")).unwrap() == "d"
    assert (await db.drop_all()).is_ok()

    disabled_db = database.PluginDatabase(plugin_id="demo", plugin_dir=plugin_dir, enabled=False)
    assert (await disabled_db.create_all()).is_ok()
    assert (await disabled_db.drop_all()).is_ok()
    assert (await disabled_db.session()).is_err()
    disabled_kv = database.PluginKVStore(database=disabled_db)
    assert (await disabled_kv.get("x", "d")).unwrap() == "d"
    assert (await disabled_kv.set("x", 1)).is_ok()
    assert (await disabled_kv.delete("x")).unwrap() is False

    class _Obj:
        __freezable__ = ["counter", "when", "items", "fitems", "path", "tuple_val", "custom"]

        def __init__(self) -> None:
            self.counter = 2
            self.when = datetime(2024, 1, 1, 1, 1, 1)
            self.items = {"a"}
            self.fitems = frozenset({"b"})
            self.path = plugin_dir
            self.tuple_val = (1, 2)
            self.custom = 1

        def __freeze_serialize__(self, key: str, value: object):
            if key == "custom":
                return {"custom": 2}
            return None

        def __freeze_deserialize__(self, key: str, value: object):
            if key == "custom":
                return 99
            return None

    persistence = state.PluginStatePersistence(plugin_id="demo", plugin_dir=plugin_dir, backend="file")
    obj = _Obj()
    assert (await persistence.save(obj)).unwrap() is True
    snapshot = (await persistence.snapshot()).unwrap()
    assert snapshot["counter"] == 2
    obj.counter = 0
    assert (await persistence.load(obj)).unwrap() is True
    assert obj.counter == 2
    assert (await persistence.clear()).unwrap() is True

    memory_state = state.PluginStatePersistence(plugin_id="demo", plugin_dir=plugin_dir, backend="memory")
    assert await memory_state.save_async(obj) is True
    obj.counter = 0
    assert await memory_state.load_async(obj) is True
    assert obj.counter == 2
    assert await memory_state.clear_async() is True
    assert await memory_state.snapshot_async() == {}

    off_state = state.PluginStatePersistence(plugin_id="demo", plugin_dir=plugin_dir, backend="off")
    assert (await off_state.save(obj)).unwrap() is False
    assert (await off_state.load(obj)).unwrap() is False

    assert state._serialize_extended(datetime(2024, 1, 1, 0, 0, 0)) is not None
    assert state._serialize_extended(date(2024, 1, 1)) is not None
    assert state._serialize_extended(timedelta(seconds=3)) is not None
    assert state._serialize_extended(_StateTopEnum.A) is not None
    assert state._serialize_extended({"x"}) is not None
    assert state._serialize_extended(frozenset({"x"})) is not None
    assert state._serialize_extended(Path("/tmp/x")) is not None
    assert state._serialize_extended(object()) is None

    assert state._deserialize_extended({"__neko_type__": "enum", "enum_class": f"{_StateTopEnum.__module__}.{_StateTopEnum.__name__}", "__neko_value__": "a"}) == _StateTopEnum.A
    assert state._deserialize_extended({"__neko_type__": "enum", "enum_class": "missing.Enum", "__neko_value__": "a"}) == "a"
    assert state._deserialize_extended({"__neko_type__": "unknown", "__neko_value__": 1}) is None

    bad = state.PluginStatePersistence(plugin_id="demo", plugin_dir=plugin_dir, backend="memory")
    bad._memory_state = b"bad"
    assert (await bad.load(obj)).unwrap() is False
    assert (await bad.snapshot()).unwrap() == {}

    file_state = state.PluginStatePersistence(plugin_id="demo", plugin_dir=plugin_dir, backend="file")
    file_state._state_path.write_bytes(b"bad")
    assert (await file_state.load(obj)).unwrap() is False
    assert (await file_state.snapshot()).unwrap() == {}

    original_store_connect = store.sqlite3.connect
    kv_store._local.conn = None
    monkeypatch.setattr(store.sqlite3, "connect", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    assert (await kv_store.get("x")).is_err()
    assert (await kv_store.set("x", 1)).is_err()
    assert (await kv_store.delete("x")).is_err()
    assert (await kv_store.exists("x")).is_err()
    assert (await kv_store.keys()).is_err()
    assert (await kv_store.clear()).is_err()
    monkeypatch.setattr(store.sqlite3, "connect", original_store_connect)

    original_db_connect = database.sqlite3.connect
    db2 = database.PluginDatabase(plugin_id="demo", plugin_dir=plugin_dir, enabled=True)
    db2._local.conn = None
    monkeypatch.setattr(database.sqlite3, "connect", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    assert (await db2.create_all()).is_err()
    assert (await db2.session()).is_err()
    monkeypatch.setattr(database.sqlite3, "connect", original_db_connect)

    failing_db = database.PluginDatabase(plugin_id="demo", plugin_dir=plugin_dir, enabled=True)
    monkeypatch.setattr(type(failing_db._db_path), "unlink", lambda self: (_ for _ in ()).throw(RuntimeError("boom")))
    assert (await failing_db.drop_all()).is_err()

    original_pack = state._pack
    monkeypatch.setattr(state, "_pack", lambda _value: (_ for _ in ()).throw(RuntimeError("boom")))
    assert (await memory_state.save(obj)).is_err()
    monkeypatch.setattr(state, "_pack", original_pack)

    original_unpack = state._unpack
    monkeypatch.setattr(state, "_unpack", lambda _data: (_ for _ in ()).throw(RuntimeError("boom")))
    assert (await bad.load(obj)).is_err()
    assert (await bad.snapshot()).is_err()
    monkeypatch.setattr(state, "_unpack", original_unpack)


@pytest.mark.asyncio
async def test_public_storage_internal_branch_coverage(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    plugin_dir = tmp_path / "public_storage_cov"
    plugin_dir.mkdir()

    # store: unpack exception + prefix branch already via direct internals
    kv_store = store.PluginStore(plugin_id="demo", plugin_dir=plugin_dir, enabled=True)
    await kv_store.set("pref_one", 1)
    await kv_store.set("pref_two", 2)
    assert kv_store._keys_sync("pref_") == ["pref_one", "pref_two"]
    original_unpack = store._unpack
    monkeypatch.setattr(store, "_unpack", lambda _data: (_ for _ in ()).throw(RuntimeError("boom")))
    assert (await kv_store.get("pref_one", "d")).unwrap() == "d"
    monkeypatch.setattr(store, "_unpack", original_unpack)

    # database kv: unpack exception and async error wrappers
    db = database.PluginDatabase(plugin_id="demo", plugin_dir=plugin_dir, enabled=True)
    kv = db.kv
    await kv.set("k", 1)
    original_db_unpack = database._unpack
    monkeypatch.setattr(database, "_unpack", lambda _data: (_ for _ in ()).throw(RuntimeError("boom")))
    assert (await kv.get("k", "d")).unwrap() == "d"
    monkeypatch.setattr(database, "_unpack", original_db_unpack)

    original_db_connect = database.sqlite3.connect
    db._local.conn = None
    monkeypatch.setattr(database.sqlite3, "connect", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    assert (await kv.get("x")).is_err()
    assert (await kv.set("x", 1)).is_err()
    assert (await kv.delete("x")).is_err()
    monkeypatch.setattr(database.sqlite3, "connect", original_db_connect)

    # state: cover remaining internal branches
    class _Obj:
        __freezable__ = ["date_val", "delta_val", "dict_val", "list_val", "tuple_val", "tagged"]

        def __init__(self) -> None:
            self.date_val = date(2024, 1, 1)
            self.delta_val = timedelta(seconds=5)
            self.dict_val = {"x": [1]}
            self.list_val = [1, 2]
            self.tuple_val = (3, 4)
            self.tagged = datetime(2024, 1, 1, 0, 0, 0)

        def __freeze_deserialize__(self, key: str, value: object):
            if key == "tagged":
                return "custom-tagged"
            return None

    obj = _Obj()
    persistence = state.PluginStatePersistence(plugin_id="demo", plugin_dir=plugin_dir, backend="memory")
    assert state._deserialize_extended({"__neko_type__": "date", "__neko_value__": "2024-01-01"}) == date(2024, 1, 1)
    assert state._deserialize_extended({"__neko_type__": "timedelta", "__neko_value__": 5}).total_seconds() == 5
    snap = persistence._collect_snapshot(obj)
    assert isinstance(snap["dict_val"], dict)
    assert isinstance(snap["list_val"], list)
    assert isinstance(snap["tuple_val"], list)
    tagged = state._serialize_extended(obj.tagged)
    assert persistence._deserialize_value("tagged", tagged, obj) == "custom-tagged"
    assert persistence._deserialize_value("dict_val", {"x": [1]}, obj) == {"x": [1]}
    assert persistence._deserialize_value("list_val", [1, 2], obj) == [1, 2]
    assert persistence._load_sync(obj) is False
    assert persistence._clear_sync() is False
    assert persistence._snapshot_sync() == {}

    file_state = state.PluginStatePersistence(plugin_id="demo", plugin_dir=plugin_dir, backend="file")
    assert file_state._load_sync(obj) is False
    assert file_state._snapshot_sync() == {}
    file_state._state_path.write_bytes(state._pack({"data": []}))
    assert file_state._load_sync(obj) is False
    file_state._state_path.write_bytes(state._pack([]))
    assert file_state._snapshot_sync() == {}

    original_state_pack = state._pack
    monkeypatch.setattr(state, "_pack", lambda _value: (_ for _ in ()).throw(RuntimeError("boom")))
    assert (await persistence.save(obj)).is_err()
    monkeypatch.setattr(state, "_pack", original_state_pack)

    original_state_unpack = state._unpack
    monkeypatch.setattr(state, "_unpack", lambda _data: (_ for _ in ()).throw(RuntimeError("boom")))
    persistence._memory_state = b"x"
    assert (await persistence.load(obj)).is_err()
    assert (await persistence.clear()).is_ok()
    persistence._memory_state = b"x"
    assert (await persistence.snapshot()).is_err()
    monkeypatch.setattr(state, "_unpack", original_state_unpack)


@pytest.mark.asyncio
async def test_public_state_clear_remaining_branches(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    plugin_dir = tmp_path / "public_state_clear"
    plugin_dir.mkdir()
    file_state = state.PluginStatePersistence(plugin_id="demo", plugin_dir=plugin_dir, backend="file")
    assert file_state._clear_sync() is False
    monkeypatch.setattr(file_state, "_clear_sync", lambda: (_ for _ in ()).throw(RuntimeError("boom")))
    assert (await file_state.clear()).is_err()
