"""Unit tests for :mod:`plugin.sdk.cua.cache`."""

from __future__ import annotations

import asyncio
import json
from datetime import date, datetime, timedelta
from typing import Any, Dict, List

import pytest

from plugin.sdk.cua.cache import (
    CuaCache,
    CuaCacheEntry,
    CuaPathCache,
)


# ── 测试用 stub ──────────────────────────────────────────────────────


class _StubStore:
    """简单的内存 store stub，支持直接值读写（不包装 Result）。"""

    def __init__(self) -> None:
        self._data: Dict[str, Any] = {}
        self.get_calls: List[str] = []
        self.set_calls: List[tuple[str, Any]] = []

    async def get(self, key: str) -> Any:
        self.get_calls.append(key)
        return self._data.get(key)

    async def set(self, key: str, value: Any) -> bool:
        self.set_calls.append((key, value))
        self._data[key] = value
        return True


class _StubStoreWithResult:
    """返回 Result 包装的 store stub（模拟官方 PluginStore）。"""

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

    async def get(self, key: str):
        value = self._data.get(key)
        return self._Ok(value) if key in self._data else self._Err()

    async def set(self, key: str, value: Any):
        self._data[key] = value
        return self._Ok(None)


class _StubCua:
    """CUA adapter stub，提供 .cots 和 .run_instruction()。"""

    def __init__(self, cots: List[Dict[str, str]]) -> None:
        self.cots = cots
        self.received_instruction: str = ""

    def run_instruction(self, instruction: str) -> Dict[str, Any]:
        self.received_instruction = instruction
        return {"success": True, "result": "ok", "steps": len(self.cots)}


# ── CuaCache / CuaCacheEntry 数据结构 ────────────────────────────────


def test_cache_entry_frozen() -> None:
    entry = CuaCacheEntry(x=42, y=88)
    assert entry.x == 42
    assert entry.y == 88


def test_cache_round_trip() -> None:
    cache = CuaCache(
        platform="weibo_web",
        task_type="post",
        key_coords=[CuaCacheEntry(x=100, y=200), CuaCacheEntry(x=300, y=400)],
        last_success_at="2026-09-22",
        success_count=3,
        last_steps=15,
    )
    d = cache.to_dict()
    assert d["platform"] == "weibo_web"
    assert d["success_count"] == 3
    assert len(d["key_coords"]) == 2

    restored = CuaCache.from_dict(d)
    assert restored.platform == cache.platform
    assert restored.success_count == cache.success_count
    assert len(restored.key_coords) == 2
    assert restored.key_coords[0].x == 100


def test_cache_from_dict_handles_bad_input() -> None:
    # 空 dict
    c = CuaCache.from_dict({})
    assert c.success_count == 0
    assert c.key_coords == []

    # key_coords 里混入坏数据
    c = CuaCache.from_dict(
        {
            "key_coords": [
                {"x": 10, "y": 20},
                {"x": "bad", "y": 30},
                {"y": 40},  # 缺 x
                "not a dict",
            ]
        }
    )
    assert len(c.key_coords) == 1
    assert c.key_coords[0].x == 10


# ── 坐标提取 ────────────────────────────────────────────────────────


def test_extract_coords_basic() -> None:
    cots = [
        {"code": "pyautogui.click(400, 300)", "thought": "click"},
        {"code": "pyautogui.doubleClick(500, 600)", "thought": "double"},
        {"code": "pyautogui.moveTo(100, 200)", "thought": "move"},
    ]
    coords = CuaPathCache._extract_coords_from_cots(cots)
    assert len(coords) == 3
    assert coords[0].x == 400
    assert coords[1].x == 500
    assert coords[2].y == 200


def test_extract_coords_dedup() -> None:
    cots = [
        {"code": "pyautogui.click(400, 300)"},
        {"code": "pyautogui.click(400, 300)"},  # 重复
        {"code": "pyautogui.click(500, 600)"},
    ]
    coords = CuaPathCache._extract_coords_from_cots(cots)
    assert len(coords) == 2


def test_extract_coords_skips_bad_code() -> None:
    cots = [
        {"code": "time.sleep(3)"},  # 无坐标
        {"code": "pyautogui.click(abc, def)"},  # 非数字
        {"code": "pyautogui.click(50, 60)"},  # 合法
    ]
    coords = CuaPathCache._extract_coords_from_cots(cots)
    assert len(coords) == 1
    assert coords[0].x == 50


def test_extract_coords_max_limit() -> None:
    cots = [{"code": f"pyautogui.click({i*10}, {i*20})"} for i in range(30)]
    coords = CuaPathCache._extract_coords_from_cots(cots, max_coords=5)
    assert len(coords) == 5


def test_extract_coords_empty_cots() -> None:
    assert CuaPathCache._extract_coords_from_cots([]) == []


# ── build_hint_text ──────────────────────────────────────────────────


def test_build_hint_text_none_cache() -> None:
    assert CuaPathCache.build_hint_text(None) == ""


def test_build_hint_text_empty_coords() -> None:
    cache = CuaCache(
        platform="x", task_type="y", key_coords=[],
        last_success_at="2026-09-22", success_count=1,
    )
    hint = CuaPathCache.build_hint_text(cache)
    assert "已成功 1 次" in hint
    assert "坐标" not in hint  # 空坐标不出现列表


def test_build_hint_text_full() -> None:
    cache = CuaCache(
        platform="weibo_web", task_type="post",
        key_coords=[CuaCacheEntry(x=100, y=200), CuaCacheEntry(x=300, y=400)],
        last_success_at="2026-09-22", success_count=5,
    )
    hint = CuaPathCache.build_hint_text(cache)
    assert "已成功 5 次" in hint
    assert "(100, 200)" in hint
    assert "(300, 400)" in hint
    assert hint.endswith("\n\n")


# ── 完整 CuaPathCache 流程 ──────────────────────────────────────────


@pytest.mark.asyncio
async def test_load_no_store() -> None:
    cache = CuaPathCache(store=None, key="weibo_web:post")
    assert await cache.load() is None


@pytest.mark.asyncio
async def test_load_not_found() -> None:
    store = _StubStore()
    cache = CuaPathCache(store=store, key="weibo_web:post")
    assert await cache.load() is None


@pytest.mark.asyncio
async def test_load_found() -> None:
    store = _StubStore()
    store._data["cua_path_cache:weibo_web:post"] = {
        "platform": "weibo_web",
        "task_type": "post",
        "key_coords": [{"x": 100, "y": 200}],
        "last_success_at": date.today().isoformat(),
        "success_count": 1,
    }
    cache = CuaPathCache(store=store, key="weibo_web:post")
    loaded = await cache.load()
    assert loaded is not None
    assert loaded.platform == "weibo_web"
    assert loaded.success_count == 1
    assert loaded.key_coords[0].x == 100


@pytest.mark.asyncio
async def test_load_expired() -> None:
    store = _StubStore()
    expired_date = (date.today() - timedelta(days=10)).isoformat()
    store._data["cua_path_cache:weibo_web:post"] = {
        "platform": "weibo_web",
        "task_type": "post",
        "key_coords": [{"x": 100, "y": 200}],
        "last_success_at": expired_date,
        "success_count": 5,
    }
    cache = CuaPathCache(store=store, key="weibo_web:post", expiry_days=7)
    # 过期 → 返回 None
    assert await cache.load() is None


@pytest.mark.asyncio
async def test_load_bad_json() -> None:
    store = _StubStore()
    store._data["cua_path_cache:weibo_web:post"] = "not json"
    cache = CuaPathCache(store=store, key="weibo_web:post")
    assert await cache.load() is None


@pytest.mark.asyncio
async def test_record_success() -> None:
    store = _StubStore()
    cots = [
        {"code": "pyautogui.click(400, 300)"},
        {"code": "pyautogui.click(500, 600)"},
    ]
    cua = _StubCua(cots)
    cache = CuaPathCache(store=store, key="weibo_web:post")

    recorded = await cache.record(cua, {"success": True, "steps": 2})
    assert recorded is not None
    assert recorded.success_count == 1
    assert len(recorded.key_coords) == 2

    # store 里有数据
    assert "cua_path_cache:weibo_web:post" in store._data


@pytest.mark.asyncio
async def test_record_failure_skips() -> None:
    store = _StubStore()
    cots = [{"code": "pyautogui.click(400, 300)"}]
    cua = _StubCua(cots)
    cache = CuaPathCache(store=store, key="weibo_web:post")

    # success=False → 不记录
    recorded = await cache.record(cua, {"success": False, "steps": 5})
    assert recorded is None
    assert not store._data  # store 没被写


@pytest.mark.asyncio
async def test_record_empty_cots_skips() -> None:
    store = _StubStore()
    cua = _StubCua([])  # 空 cots
    cache = CuaPathCache(store=store, key="weibo_web:post")

    recorded = await cache.record(cua, {"success": True, "steps": 0})
    assert recorded is None


@pytest.mark.asyncio
async def test_record_accumulates_count() -> None:
    store = _StubStore()
    # 先存一次
    store._data["cua_path_cache:weibo_web:post"] = {
        "platform": "weibo_web",
        "task_type": "post",
        "key_coords": [{"x": 100, "y": 200}],
        "last_success_at": (date.today() - timedelta(days=1)).isoformat(),
        "success_count": 3,
    }
    cots = [{"code": "pyautogui.click(500, 600)"}]
    cua = _StubCua(cots)
    cache = CuaPathCache(store=store, key="weibo_web:post")

    recorded = await cache.record(cua, {"success": True, "steps": 1})
    assert recorded is not None
    assert recorded.success_count == 4  # 3 + 1


@pytest.mark.asyncio
async def test_record_no_store_graceful_degradation() -> None:
    cots = [{"code": "pyautogui.click(400, 300)"}]
    cua = _StubCua(cots)
    cache = CuaPathCache(store=None, key="weibo_web:post")

    # store=None → 静默跳过，不抛异常
    recorded = await cache.record(cua, {"success": True, "steps": 1})
    assert recorded is None


@pytest.mark.asyncio
async def test_run_injects_hint_and_records() -> None:
    store = _StubStore()
    # 预存缓存
    store._data["cua_path_cache:weibo_web:post"] = {
        "platform": "weibo_web",
        "task_type": "post",
        "key_coords": [{"x": 400, "y": 300}],
        "last_success_at": date.today().isoformat(),
        "success_count": 2,
    }
    cots = [{"code": "pyautogui.click(400, 300)"}]
    cua = _StubCua(cots)
    cache = CuaPathCache(store=store, key="weibo_web:post")

    result = await cache.run(cua, "去微博发动态")

    # 1. instruction 前注入了缓存提示
    assert "【路径缓存提示】" in cua.received_instruction
    assert "去微博发动态" in cua.received_instruction

    # 2. 返回了 CUA 结果
    assert result["success"] is True

    # 3. store 被更新（success_count += 1）
    assert store._data["cua_path_cache:weibo_web:post"]["success_count"] == 3


@pytest.mark.asyncio
async def test_run_no_cache_direct_pass() -> None:
    store = _StubStore()  # 空 store，无缓存
    cots = [{"code": "pyautogui.click(100, 200)"}]
    cua = _StubCua(cots)
    cache = CuaPathCache(store=store, key="weibo_web:post")

    result = await cache.run(cua, "去微博发动态")

    # 无缓存 → 直接 pass-through
    assert cua.received_instruction == "去微博发动态"
    # 首次执行后写入缓存
    assert "cua_path_cache:weibo_web:post" in store._data


# ── Result 包装兼容 ─────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_store_with_result_wrapper() -> None:
    """验证 CuaPathCache 能处理官方 PluginStore 的 Result[T, E] 返回格式。"""
    store = _StubStoreWithResult()
    cots = [{"code": "pyautogui.click(700, 500)"}]
    cua = _StubCua(cots)
    cache = CuaPathCache(store=store, key="twitter_web:post")

    # record → Result 模式下 set 成功
    recorded = await cache.record(cua, {"success": True, "steps": 1})
    assert recorded is not None

    # load → Result 模式下 get 成功
    loaded = await cache.load()
    assert loaded is not None
    assert loaded.platform == "twitter_web"


# ── store_key 格式 ──────────────────────────────────────────────────


def test_store_key_format() -> None:
    cache = CuaPathCache(store=None, key="weibo_web:post")
    assert cache._store_key == "cua_path_cache:weibo_web:post"


def test_platform_task_type_from_key() -> None:
    cache = CuaPathCache(store=None, key="weibo_web:post")
    assert cache._platform == "weibo_web"
    assert cache._task_type == "post"


def test_platform_task_type_override() -> None:
    cache = CuaPathCache(
        store=None, key="custom",
        platform="twitter_web", task_type="reply",
    )
    assert cache._platform == "twitter_web"
    assert cache._task_type == "reply"


def test_platform_task_type_single_key_no_colon() -> None:
    cache = CuaPathCache(store=None, key="weibo_post")
    assert cache._platform == ""
    assert cache._task_type == "weibo_post"
