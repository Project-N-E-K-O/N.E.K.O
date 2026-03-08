from __future__ import annotations

import importlib
import sys
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from enum import Enum
from pathlib import Path
from types import ModuleType, SimpleNamespace

import ormsgpack
import pytest


class _Logger:
    def __init__(self) -> None:
        self.debug_calls: list[str] = []
        self.warning_calls: list[str] = []
        self.info_calls: list[str] = []
        self.exception_calls: list[str] = []

    def debug(self, msg: str) -> None:
        self.debug_calls.append(msg)

    def warning(self, msg: str) -> None:
        self.warning_calls.append(msg)

    def info(self, msg: str) -> None:
        self.info_calls.append(msg)

    def exception(self, msg: str) -> None:
        self.exception_calls.append(msg)


class _C(Enum):
    A = "a"


class _FakeGlobalState:
    def __init__(self) -> None:
        self.mem: dict[str, bytes] = {}

    def save_frozen_state_memory(self, plugin_id: str, data: bytes) -> None:
        self.mem[plugin_id] = data

    def get_frozen_state_memory(self, plugin_id: str) -> bytes | None:
        return self.mem.get(plugin_id)

    def clear_frozen_state_memory(self, plugin_id: str) -> None:
        self.mem.pop(plugin_id, None)

    def has_frozen_state_memory(self, plugin_id: str) -> bool:
        return plugin_id in self.mem


def _inject_fake_core_state(monkeypatch: pytest.MonkeyPatch, fake_state: _FakeGlobalState) -> None:
    core_state_module = ModuleType("plugin.core.state")
    core_state_module.state = fake_state
    monkeypatch.setitem(sys.modules, "plugin.core.state", core_state_module)


@pytest.mark.plugin_unit
def test_state_fallback_msgpack_import_branch(monkeypatch: pytest.MonkeyPatch) -> None:
    import builtins
    import plugin.sdk.state as state_module

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
    reloaded = importlib.reload(state_module)
    assert reloaded._USE_ORMSGPACK is False

    monkeypatch.setattr(builtins, "__import__", orig_import)
    importlib.reload(state_module)


@pytest.mark.plugin_unit
def test_extended_type_helpers_and_unknown_paths() -> None:
    from plugin.sdk.state import _deserialize_extended_type, _serialize_extended_type

    assert _serialize_extended_type(datetime(2026, 1, 1)) is not None
    assert _serialize_extended_type(date(2026, 1, 1)) is not None
    assert _serialize_extended_type(timedelta(seconds=1)) is not None
    assert _serialize_extended_type(_C.A) is not None
    assert _serialize_extended_type(frozenset({"a"})) is not None
    assert _serialize_extended_type({"x": 1}) is None

    assert _deserialize_extended_type({}) is None
    assert _deserialize_extended_type({"__neko_type__": "datetime", "__neko_value__": "2026-01-01T00:00:00"}) is not None
    assert _deserialize_extended_type({"__neko_type__": "timedelta", "__neko_value__": 2}) == timedelta(seconds=2)
    assert _deserialize_extended_type({"__neko_type__": "set", "__neko_value__": [1]}) == {1}
    assert _deserialize_extended_type({"__neko_type__": "frozenset", "__neko_value__": [1]}) == frozenset({1})
    assert _deserialize_extended_type({"__neko_type__": "path", "__neko_value__": "/tmp/x"}) == Path("/tmp/x")
    ok_enum = _deserialize_extended_type(
        {"__neko_type__": "enum", "enum_class": "plugin.tests.unit.sdk.core.test_sdk_state._C", "__neko_value__": "a"}
    )
    assert getattr(ok_enum, "value", None) == _C.A.value
    assert _deserialize_extended_type({"__neko_type__": "unknown", "__neko_value__": 1}) is None
    # enum import failure fallback
    out = _deserialize_extended_type(
        {"__neko_type__": "enum", "enum_class": "nope.missing", "__neko_value__": "v"}
    )
    assert out == "v"


@pytest.mark.plugin_unit
def test_state_collect_restore_and_custom_serialization(tmp_path: Path) -> None:
    from plugin.sdk.state import PluginStatePersistence

    logger = _Logger()
    sp = PluginStatePersistence("p1", tmp_path, logger=logger, backend="off")

    class P:
        ok = 1
        nested = {"a": [1, 2], "b": {3, 4}, "c": Path("/tmp/a")}
        bad = object()

        def __freeze_serialize__(self, key, value):  # type: ignore[no-untyped-def]
            if key == "ok":
                return {"wrapped": value}
            return None

        def __freeze_deserialize__(self, key, value):  # type: ignore[no-untyped-def]
            if key == "ok" and isinstance(value, dict):
                return value["wrapped"]
            return None

    p = P()
    snap = sp.collect_attrs(p, ["ok", "nested", "missing", "bad"])
    assert "ok" in snap and "nested" in snap and "bad" in snap  # custom serialize allows bad
    restored = sp.restore_attrs(p, {"ok": {"wrapped": 9}, "x": 1})
    assert restored == 2
    assert p.ok == 9

    class Plain:
        bad = object()

    plain = Plain()
    assert sp._is_serializable(plain.bad, plain) is False

    class RaisesSerialize:
        def __freeze_serialize__(self, key, value):  # type: ignore[no-untyped-def]
            raise RuntimeError("x")

    rs = RaisesSerialize()
    assert sp._serialize_value("k", 1, rs) == 1

    class RaisesDeserialize:
        def __freeze_deserialize__(self, key, value):  # type: ignore[no-untyped-def]
            raise RuntimeError("x")

    rd = RaisesDeserialize()
    ext = {"__neko_type__": "date", "__neko_value__": "2026-01-01"}
    assert str(sp._deserialize_value("k", ext, rd)) == "2026-01-01"
    assert sp._deserialize_value("k", {"x": [1]}, rd)["x"] == [1]

    class ReturnCustom:
        def __freeze_deserialize__(self, key, value):  # type: ignore[no-untyped-def]
            return "custom"

    rc = ReturnCustom()
    assert sp._deserialize_value("k", ext, rc) == "custom"

    class NoSer:
        bad = object()

    nos = NoSer()
    snap2 = sp.collect_attrs(nos, ["bad"])
    assert snap2 == {}
    assert any("not serializable" in msg for msg in logger.warning_calls)

    class FailSet:
        @property
        def x(self):  # noqa: ANN201
            return 1

    fs = FailSet()
    assert sp.restore_attrs(fs, {"x": 2}) == 0


@pytest.mark.plugin_unit
def test_state_save_load_clear_memory_file_and_info(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from plugin.sdk.state import PluginStatePersistence

    fake_state = _FakeGlobalState()
    _inject_fake_core_state(monkeypatch, fake_state)
    logger = _Logger()

    class P:
        n = 1

    p = P()

    # off backend
    sp_off = PluginStatePersistence("p-off", tmp_path, logger=logger, backend="off")
    assert sp_off.save(p, ["n"]) is True
    assert sp_off.load(p) is False
    assert sp_off.has_saved_state() is False
    assert sp_off.get_state_info() is None

    # memory backend
    sp_mem = PluginStatePersistence("p-mem", tmp_path, logger=logger, backend="memory")
    assert sp_mem.load(p) is False  # no memory saved yet
    assert sp_mem.save(p, ["n"], reason="auto") is True
    assert sp_mem.has_saved_state() is True
    p.n = 2
    assert sp_mem.load(p) is True
    assert p.n == 1
    info = sp_mem.get_state_info()
    assert info and info["plugin_id"] == "p-mem"
    assert sp_mem.clear() is True
    assert sp_mem.has_saved_state() is False

    # file backend
    sp_file = PluginStatePersistence("p-file", tmp_path, logger=logger, backend="file")
    assert sp_file.load(p) is False  # no file yet
    assert sp_file.save(p, ["n"], reason="freeze") is True
    assert sp_file.has_saved_state() is True
    p.n = 99
    assert sp_file.load(p) is True
    assert p.n == 1
    assert sp_file.clear() is True
    assert sp_file.has_saved_state() is False
    assert sp_file.get_state_info() is None

    # empty snapshot short-circuit
    assert sp_file.save(p, ["missing"]) is True


@pytest.mark.plugin_unit
def test_state_error_paths_and_invalid_version(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from plugin.sdk.state import PluginStatePersistence

    fake_state = _FakeGlobalState()
    _inject_fake_core_state(monkeypatch, fake_state)
    logger = _Logger()

    class P:
        n = 1

    p = P()
    sp = PluginStatePersistence("p", tmp_path, logger=logger, backend="file")

    # invalid version -> load False
    bad_payload = sp._serialize({"version": 999, "plugin_id": "p", "data": {"n": 1}})
    sp._state_path.write_bytes(bad_payload)
    assert sp.load(p) is False
    assert any("Unknown state version" in msg for msg in logger.warning_calls)

    # deserialize exception on load/get_state_info
    sp._state_path.write_bytes(b"\xff\xff")
    assert sp.load(p) is False
    assert sp.get_state_info() is None

    # clear failure
    sp._state_path.write_bytes(b"x")
    orig_unlink = Path.unlink

    def _bad_unlink(path_obj: Path, *args, **kwargs):  # type: ignore[no-untyped-def]
        if path_obj == sp._state_path:
            raise RuntimeError("x")
        return orig_unlink(path_obj, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", _bad_unlink)
    assert sp.clear() is False


@pytest.mark.plugin_unit
def test_state_msgpack_non_orm_branch_and_save_failure(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import plugin.sdk.state as state_module
    from plugin.sdk.state import PluginStatePersistence

    logger = _Logger()
    sp = PluginStatePersistence("p", tmp_path, logger=logger, backend="file")
    monkeypatch.setattr(state_module, "_USE_ORMSGPACK", False)
    monkeypatch.setattr(
        state_module,
        "msgpack",
        SimpleNamespace(
            packb=lambda v, **kwargs: b"ok",
            unpackb=lambda b, **kwargs: {"v": 1},
        ),
    )
    assert sp._serialize({"a": 1}) == b"ok"
    assert sp._deserialize(b"ok") == {"v": 1}

    monkeypatch.setattr(sp, "_serialize", lambda d: (_ for _ in ()).throw(RuntimeError("boom")))
    class P:
        n = 1
    assert sp.save(P(), ["n"]) is False
    assert logger.exception_calls


@pytest.mark.plugin_unit
@pytest.mark.asyncio
async def test_state_async_methods(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from plugin.sdk.state import PluginStatePersistence

    fake_state = _FakeGlobalState()
    _inject_fake_core_state(monkeypatch, fake_state)

    class P:
        n = 1

    p = P()
    sp = PluginStatePersistence("p-async", tmp_path, backend="memory")

    snap = await sp.collect_attrs_async(p, ["n"])
    assert snap == {"n": 1}
    restored = await sp.restore_attrs_async(p, {"n": 2})
    assert restored == 1 and p.n == 2

    assert await sp.has_saved_state_async() is False
    assert await sp.save_async(p, ["n"], reason="auto") is True
    assert await sp.has_saved_state_async() is True
    p.n = 9
    assert await sp.load_async(p) is True
    assert p.n == 2
    info = await sp.get_state_info_async()
    assert info and info["plugin_id"] == "p-async"
    assert await sp.clear_async() is True
    assert await sp.has_saved_state_async() is False
