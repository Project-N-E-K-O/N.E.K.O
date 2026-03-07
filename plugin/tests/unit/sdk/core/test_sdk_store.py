from __future__ import annotations

import importlib
import sys
from pathlib import Path
from types import SimpleNamespace

import ormsgpack
import pytest


class _Logger:
    def __init__(self) -> None:
        self.debug_calls: list[str] = []
        self.warning_calls: list[str] = []

    def debug(self, msg: str) -> None:
        self.debug_calls.append(msg)

    def warning(self, msg: str) -> None:
        self.warning_calls.append(msg)


@pytest.mark.plugin_unit
def test_store_fallback_msgpack_import_branch(monkeypatch: pytest.MonkeyPatch) -> None:
    import builtins
    import plugin.sdk.store as store_module

    orig_import = builtins.__import__

    def _fake_import(name, *args, **kwargs):  # type: ignore[no-untyped-def]
        if name == "ormsgpack":
            raise ImportError("blocked")
        return orig_import(name, *args, **kwargs)

    fake_msgpack = SimpleNamespace(
        packb=lambda v, **kwargs: ormsgpack.packb(v),
        unpackb=lambda b, **kwargs: ormsgpack.unpackb(b),
    )
    monkeypatch.setitem(sys.modules, "msgpack", fake_msgpack)
    monkeypatch.setattr(builtins, "__import__", _fake_import)
    reloaded = importlib.reload(store_module)
    assert reloaded._USE_ORMSGPACK is False

    # restore module state for other tests
    monkeypatch.setattr(builtins, "__import__", orig_import)
    importlib.reload(store_module)


@pytest.mark.plugin_unit
def test_store_disabled_runtime_error_and_logs(tmp_path: Path) -> None:
    from plugin.sdk.store import PluginStore

    logger = _Logger()
    store = PluginStore(plugin_id="x", plugin_dir=tmp_path, logger=logger, enabled=False)
    assert any("disabled" in msg for msg in logger.debug_calls)
    with pytest.raises(RuntimeError):
        store._get_conn()
    store.set("k", 1)
    assert any("Attempted to set key 'k'" in msg for msg in logger.warning_calls)
    assert store.delete("x") is False
    assert store.exists("x") is False
    assert store.keys() == []
    assert store.clear() == 0
    assert store.count() == 0
    assert store.dump() == {}
    assert store.get("missing", default="d") == "d"


@pytest.mark.plugin_unit
def test_store_deserialize_failures_and_magic_methods(tmp_path: Path) -> None:
    from plugin.sdk.store import PluginStore

    logger = _Logger()
    store = PluginStore(plugin_id="demo", plugin_dir=tmp_path, logger=logger, enabled=True)
    store.set("ok", {"a": 1})
    assert "ok" in store
    assert len(store) >= 1

    # Force decode failure path in get()/dump()
    conn = store._get_conn()
    conn.execute(
        "INSERT OR REPLACE INTO kv_store (key, value, created_at, updated_at) VALUES (?, ?, ?, ?)",
        ("bad", b"broken", 1.0, 1.0),
    )
    conn.commit()
    orig_deser = store._deserialize
    store._deserialize = lambda d: (_ for _ in ()).throw(ValueError("decode"))  # type: ignore[assignment]
    assert store.get("bad", default="fallback") == "fallback"
    assert any("Failed to deserialize key 'bad'" in msg for msg in logger.warning_calls)

    # Force decode failure path in dump()
    dumped = store.dump()
    store._deserialize = orig_deser  # type: ignore[assignment]
    assert dumped == {}
    dumped2 = store.dump()
    assert dumped2["ok"] == {"a": 1}
    assert "bad" not in dumped

    # __getitem__ missing key path
    with pytest.raises(KeyError):
        _ = store["missing"]

    # __delitem__ missing key path
    with pytest.raises(KeyError):
        del store["missing"]

    # keys(prefix=...) branch
    store.set("pref:1", 1)
    store.set("pref:2", 2)
    assert sorted(store.keys(prefix="pref:")) == ["pref:1", "pref:2"]
    assert set(store.keys()) >= {"ok", "bad", "pref:1", "pref:2"}
    assert store.clear() >= 1

    store["via_setitem"] = 1
    assert store["via_setitem"] == 1


@pytest.mark.plugin_unit
def test_store_close_exception_path(tmp_path: Path) -> None:
    from plugin.sdk.store import PluginStore

    store = PluginStore(plugin_id="demo", plugin_dir=tmp_path, enabled=True)

    class _BadConn:
        def close(self) -> None:
            raise RuntimeError("close failed")

    store._local.conn = _BadConn()
    store.close()  # should not raise
    assert store._local.conn is None


@pytest.mark.plugin_unit
def test_store_msgpack_non_orm_branch_direct(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import plugin.sdk.store as store_module

    store = store_module.PluginStore(plugin_id="demo", plugin_dir=tmp_path, enabled=True)
    monkeypatch.setattr(store_module, "_USE_ORMSGPACK", False)
    monkeypatch.setattr(
        store_module,
        "msgpack",
        SimpleNamespace(
            packb=lambda v, **kwargs: b"ok",
            unpackb=lambda b, **kwargs: {"decoded": True},
        ),
    )
    assert store._serialize({"x": 1}) == b"ok"
    assert store._deserialize(b"ok") == {"decoded": True}
