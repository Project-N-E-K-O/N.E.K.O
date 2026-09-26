"""Unit tests for NekoPluginBase store convenience methods (Proposal 3)."""

from __future__ import annotations

from typing import Any, Dict, List

import pytest

from plugin.sdk.shared.core.base import NekoPluginBase


# ── Test doubles ──────────────────────────────────────────────────────


class _FakeLogger:
    def debug(self, *args: Any, **kwargs: Any) -> None:
        pass


class _FakeStoreOk:
    """Store returning Result[T, E] with Ok (official PluginStore shape)."""

    class _Ok:
        def __init__(self, value: Any) -> None:
            self._value = value

        def is_ok(self) -> bool:
            return True

        def is_err(self) -> bool:
            return False

        @property
        def value(self) -> Any:
            return self._value

    class _Err:
        def is_ok(self) -> bool:
            return False

        def is_err(self) -> bool:
            return True

    def __init__(self) -> None:
        self._data: Dict[str, Any] = {}
        self.get_calls: List[tuple[str, Any]] = []
        self.set_calls: List[tuple[str, Any]] = []
        self.delete_calls: List[str] = []

    async def get(self, key: str, default: Any = None):
        self.get_calls.append((key, default))
        if key in self._data:
            return self._Ok(self._data[key])
        return self._Ok(default)

    async def set(self, key: str, value: Any):
        self.set_calls.append((key, value))
        self._data[key] = value
        return self._Ok(None)

    async def delete(self, key: str):
        self.delete_calls.append(key)
        if key in self._data:
            del self._data[key]
            return self._Ok(True)
        return self._Ok(False)


class _FakeStoreRaw:
    """Store returning raw values directly (community plugin shape)."""

    def __init__(self) -> None:
        self._data: Dict[str, Any] = {}

    async def get(self, key: str, default: Any = None) -> Any:
        return self._data.get(key, default)

    async def set(self, key: str, value: Any) -> None:
        self._data[key] = value

    async def delete(self, key: str) -> bool:
        if key in self._data:
            del self._data[key]
            return True
        return False


class _FailingStore:
    """Store that raises on every call."""

    async def get(self, *args: Any, **kwargs: Any) -> Any:
        raise RuntimeError("store down")

    async def set(self, *args: Any, **kwargs: Any) -> Any:
        raise RuntimeError("store down")

    async def delete(self, *args: Any, **kwargs: Any) -> Any:
        raise RuntimeError("store down")


def _make_plugin(store: Any) -> NekoPluginBase:
    """构造一个最小化的 NekoPluginBase 实例用于测试 store 便捷方法。"""
    plugin = NekoPluginBase.__new__(NekoPluginBase)
    plugin.store = store
    plugin.logger = _FakeLogger()
    plugin.sdk_logger = _FakeLogger()
    return plugin


# ── store_get ────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_store_get_ok_mode() -> None:
    """官方 PluginStore（Result[T, E]）模式下正确解包。"""
    store = _FakeStoreOk()
    store._data["count"] = 42
    plugin = _make_plugin(store)

    assert await plugin.store_get("count") == 42
    assert await plugin.store_get("missing", default=99) == 99


@pytest.mark.asyncio
async def test_store_get_raw_mode() -> None:
    """裸值 store 模式下直接返回值。"""
    store = _FakeStoreRaw()
    store._data["name"] = "lanlan"
    plugin = _make_plugin(store)

    assert await plugin.store_get("name") == "lanlan"
    assert await plugin.store_get("missing", default="default") == "default"


@pytest.mark.asyncio
async def test_store_get_no_store() -> None:
    """store=None 时 graceful 返回 default，不抛异常。"""
    plugin = _make_plugin(store=None)
    assert await plugin.store_get("anything") is None
    assert await plugin.store_get("anything", default="fallback") == "fallback"


@pytest.mark.asyncio
async def test_store_get_failing_store() -> None:
    """store 抛异常时 graceful 返回 default。"""
    plugin = _make_plugin(store=_FailingStore())
    assert await plugin.store_get("anything") is None
    assert await plugin.store_get("anything", default="safe") == "safe"


# ── store_set ────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_store_set_ok_mode() -> None:
    store = _FakeStoreOk()
    plugin = _make_plugin(store)

    assert await plugin.store_set("key", "value") is True
    assert store._data["key"] == "value"


@pytest.mark.asyncio
async def test_store_set_raw_mode() -> None:
    store = _FakeStoreRaw()
    plugin = _make_plugin(store)

    assert await plugin.store_set("key", "value") is True
    assert store._data["key"] == "value"


@pytest.mark.asyncio
async def test_store_set_no_store() -> None:
    plugin = _make_plugin(store=None)
    assert await plugin.store_set("key", "value") is False


@pytest.mark.asyncio
async def test_store_set_failing_store() -> None:
    plugin = _make_plugin(store=_FailingStore())
    assert await plugin.store_set("key", "value") is False


# ── store_delete ──────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_store_delete_ok_mode() -> None:
    store = _FakeStoreOk()
    store._data["temp"] = "bye"
    plugin = _make_plugin(store)

    assert await plugin.store_delete("temp") is True
    assert "temp" not in store._data
    # 再删一次 → key 不存在
    assert await plugin.store_delete("temp") is False


@pytest.mark.asyncio
async def test_store_delete_raw_mode() -> None:
    store = _FakeStoreRaw()
    store._data["temp"] = "bye"
    plugin = _make_plugin(store)

    assert await plugin.store_delete("temp") is True
    assert await plugin.store_delete("temp") is False


@pytest.mark.asyncio
async def test_store_delete_no_store() -> None:
    plugin = _make_plugin(store=None)
    assert await plugin.store_delete("anything") is False


@pytest.mark.asyncio
async def test_store_delete_failing_store() -> None:
    plugin = _make_plugin(store=_FailingStore())
    assert await plugin.store_delete("anything") is False


# ── _unwrap_store_result 独立测试 ─────────────────────────────────────


def test_unwrap_ok() -> None:
    store = _FakeStoreOk()
    plugin = _make_plugin(store)
    ok = _FakeStoreOk._Ok("hello")
    assert plugin._unwrap_store_result(ok, "default") == "hello"


def test_unwrap_err() -> None:
    store = _FakeStoreOk()
    plugin = _make_plugin(store)
    err = _FakeStoreOk._Err()
    assert plugin._unwrap_store_result(err, "default") == "default"


def test_unwrap_none() -> None:
    store = _FakeStoreOk()
    plugin = _make_plugin(store)
    assert plugin._unwrap_store_result(None, "default") == "default"


def test_unwrap_raw_value() -> None:
    store = _FakeStoreOk()
    plugin = _make_plugin(store)
    # 裸值（不是 Result 包装）直接返回
    assert plugin._unwrap_store_result("raw_string", "default") == "raw_string"
    assert plugin._unwrap_store_result(42, 0) == 42
