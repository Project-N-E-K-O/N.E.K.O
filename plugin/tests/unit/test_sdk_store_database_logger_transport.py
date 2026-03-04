from __future__ import annotations

from pathlib import Path

import pytest

from plugin.sdk import database as db_module
from plugin.sdk.database import PluginDatabase
from plugin.sdk.logger import PluginFileLogger, enable_plugin_file_logging
from plugin.sdk.message_plane_transport import format_rpc_error
from plugin.sdk.store import PluginStore


@pytest.mark.plugin_unit
def test_plugin_store_crud_and_dump(tmp_path: Path) -> None:
    store = PluginStore(plugin_id="demo", plugin_dir=tmp_path, enabled=True)

    store.set("k1", {"v": 1})
    assert store.get("k1") == {"v": 1}
    assert store.exists("k1") is True
    assert "k1" in store.keys()
    assert store.count() >= 1
    assert store.dump()["k1"] == {"v": 1}

    assert store.delete("k1") is True
    assert store.get("k1", default="x") == "x"

    store["k2"] = [1, 2]
    assert store["k2"] == [1, 2]
    del store["k2"]
    with pytest.raises(KeyError):
        _ = store["k2"]

    store.set("a", 1)
    store.set("b", 2)
    assert store.clear() >= 2
    store.close()


@pytest.mark.plugin_unit
def test_plugin_store_disabled_mode(tmp_path: Path) -> None:
    store = PluginStore(plugin_id="demo", plugin_dir=tmp_path, enabled=False)
    store.set("k", 1)
    assert store.get("k") is None
    assert store.count() == 0
    assert store.keys() == []


@pytest.mark.plugin_unit
def test_plugin_database_disabled_mode_and_properties(tmp_path: Path) -> None:
    db = PluginDatabase(plugin_id="demo", plugin_dir=tmp_path, enabled=False)
    assert db.db_exists is False
    assert db.db_path.name == "demo.db"

    with pytest.raises(RuntimeError):
        _ = db.engine
    with pytest.raises(RuntimeError):
        _ = db.async_engine
    with pytest.raises(RuntimeError):
        db.get_session()

    db.create_all_sync()
    db.drop_all_sync()
    db.close()


@pytest.mark.plugin_unit
def test_plugin_database_kv_store_enabled(tmp_path: Path) -> None:
    class _DummyAsyncEngine:
        async def dispose(self) -> None:
            return None

    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(db_module, "create_async_engine", lambda *args, **kwargs: _DummyAsyncEngine())
    monkeypatch.setattr(db_module, "async_sessionmaker", lambda **kwargs: (lambda: None))
    db = PluginDatabase(plugin_id="demo", plugin_dir=tmp_path, enabled=True)
    kv = db.kv

    kv.set("k", {"x": 1})
    assert kv.get("k") == {"x": 1}
    assert kv.exists("k") is True
    assert "k" in kv.keys()
    assert kv.count() >= 1
    assert kv.delete("k") is True
    assert kv.get("k", default="d") == "d"
    db.close()
    monkeypatch.undo()


@pytest.mark.plugin_unit
@pytest.mark.asyncio
async def test_plugin_database_async_close(tmp_path: Path) -> None:
    class _DummyAsyncEngine:
        async def dispose(self) -> None:
            return None

    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(db_module, "create_async_engine", lambda *args, **kwargs: _DummyAsyncEngine())
    monkeypatch.setattr(db_module, "async_sessionmaker", lambda **kwargs: (lambda: None))
    db = PluginDatabase(plugin_id="demo", plugin_dir=tmp_path, enabled=True)
    await db.close_async()
    monkeypatch.undo()


@pytest.mark.plugin_unit
def test_plugin_file_logger_setup_and_paths(tmp_path: Path) -> None:
    pfl = PluginFileLogger(
        plugin_id="demo",
        plugin_dir=tmp_path,
        log_level="INFO",
        max_bytes=1024,
        backup_count=1,
        max_files=2,
    )
    lg = pfl.setup()
    assert lg is not None
    assert pfl.get_log_directory().exists()
    assert pfl.get_log_file_path().name.startswith("demo_")
    assert pfl.get_logger() is not None
    pfl.cleanup()
    assert pfl.get_logger() is None


@pytest.mark.plugin_unit
def test_enable_plugin_file_logging_helper(tmp_path: Path) -> None:
    lg = enable_plugin_file_logging(plugin_id="demo2", plugin_dir=tmp_path)
    assert lg is not None


@pytest.mark.plugin_unit
@pytest.mark.parametrize(
    ("err", "expected"),
    [
        (None, "message_plane error"),
        ("x", "x"),
        ({"code": "E", "message": "bad"}, "E: bad"),
        ({"message": "bad"}, "bad"),
    ],
)
def test_format_rpc_error(err: object, expected: str) -> None:
    assert format_rpc_error(err) == expected
