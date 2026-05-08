# -*- coding: utf-8 -*-
"""
Unit tests for memory.cursors.CursorStore.

Focus: P0 修复——持久化游标文件，使 rebuttal / extract 循环在进程重启后
能够从上次处理点继续，而不是每次回退到 now - 1h（丢失关机期间的消息）。
"""
from __future__ import annotations

import asyncio
import json
import os
from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest


def _make_mock_cm(tmpdir: str):
    mock = MagicMock()
    mock.memory_dir = tmpdir
    return mock


def _fresh_store(tmpdir: str):
    """返回一个全新的 CursorStore，模拟进程重启（no in-memory cache）。

    注意：cursors.py 里用的是 `from utils.config_manager import get_config_manager`，
    import 发生在模块级，所以要 patch `memory.cursors.get_config_manager` 才
    命中构造函数里的调用（不是 patch 原模块）。
    """
    from memory.cursors import CursorStore
    mock_cm = _make_mock_cm(tmpdir)
    with patch("memory.cursors.get_config_manager", return_value=mock_cm):
        store = CursorStore()
    # 双保险：即便 patch 漏了，直接替换实例字段也能让测试独立运行
    store._config_manager = mock_cm
    return store


def test_get_cursor_returns_none_for_missing_key(tmp_path):
    store = _fresh_store(str(tmp_path))
    assert store.get_cursor("小天", "rebuttal_checked_until") is None


def test_set_get_roundtrip_same_instance(tmp_path):
    store = _fresh_store(str(tmp_path))
    ts = datetime(2026, 4, 17, 5, 23, 0)
    store.set_cursor("小天", "rebuttal_checked_until", ts)
    assert store.get_cursor("小天", "rebuttal_checked_until") == ts


def test_cursor_persists_across_fresh_instances(tmp_path):
    """核心用例：消灭致命点 2——关机重启后能从磁盘恢复游标。"""
    ts = datetime(2026, 4, 14, 12, 0, 0)  # "3 天前"
    store_before = _fresh_store(str(tmp_path))
    store_before.set_cursor("小天", "rebuttal_checked_until", ts)

    # 全新实例模拟进程重启（没有共享 _cache）
    store_after = _fresh_store(str(tmp_path))
    assert store_after.get_cursor("小天", "rebuttal_checked_until") == ts


def test_multiple_keys_independent(tmp_path):
    """为 P1 预留：一个 cursors.json 要能同时容纳多种游标且互不覆盖。"""
    store = _fresh_store(str(tmp_path))
    t1 = datetime(2026, 4, 10, 0, 0, 0)
    t2 = datetime(2026, 4, 11, 0, 0, 0)
    store.set_cursor("小天", "rebuttal_checked_until", t1)
    store.set_cursor("小天", "extracted_until", t2)

    fresh = _fresh_store(str(tmp_path))
    assert fresh.get_cursor("小天", "rebuttal_checked_until") == t1
    assert fresh.get_cursor("小天", "extracted_until") == t2


def test_per_character_isolation(tmp_path):
    store = _fresh_store(str(tmp_path))
    t_a = datetime(2026, 4, 10, 0, 0, 0)
    t_b = datetime(2026, 4, 11, 0, 0, 0)
    store.set_cursor("小天", "rebuttal_checked_until", t_a)
    store.set_cursor("小雪", "rebuttal_checked_until", t_b)

    fresh = _fresh_store(str(tmp_path))
    assert fresh.get_cursor("小天", "rebuttal_checked_until") == t_a
    assert fresh.get_cursor("小雪", "rebuttal_checked_until") == t_b


def test_set_preserves_existing_keys_in_file(tmp_path):
    """连续 set 不同 key 时，先写的 key 不应被抹掉。"""
    store = _fresh_store(str(tmp_path))
    t1 = datetime(2026, 4, 10, 0, 0, 0)
    t2 = datetime(2026, 4, 11, 0, 0, 0)
    store.set_cursor("小天", "rebuttal_checked_until", t1)
    store.set_cursor("小天", "extracted_until", t2)

    # 直接读盘验证内容
    path = os.path.join(str(tmp_path), "小天", "cursors.json")
    assert os.path.exists(path)
    with open(path, encoding="utf-8") as f:
        on_disk = json.load(f)
    assert on_disk["rebuttal_checked_until"] == t1.isoformat()
    assert on_disk["extracted_until"] == t2.isoformat()


def test_corrupt_cursor_file_returns_none(tmp_path):
    """损坏的 cursors.json 不应让 loop 崩溃，静默 fallback 到 None。"""
    char_dir = os.path.join(str(tmp_path), "小天")
    os.makedirs(char_dir, exist_ok=True)
    with open(os.path.join(char_dir, "cursors.json"), "w", encoding="utf-8") as f:
        f.write("this is not json {{{{")

    store = _fresh_store(str(tmp_path))
    assert store.get_cursor("小天", "rebuttal_checked_until") is None


def test_unparsable_value_falls_back_to_none(tmp_path):
    """单个 key 的值不是 ISO8601 → 该 key 读到 None，其它 key 正常。"""
    char_dir = os.path.join(str(tmp_path), "小天")
    os.makedirs(char_dir, exist_ok=True)
    ok_ts = datetime(2026, 4, 10, 0, 0, 0)
    with open(os.path.join(char_dir, "cursors.json"), "w", encoding="utf-8") as f:
        json.dump({
            "rebuttal_checked_until": "garbage-not-iso",
            "extracted_until": ok_ts.isoformat(),
        }, f)

    store = _fresh_store(str(tmp_path))
    assert store.get_cursor("小天", "rebuttal_checked_until") is None
    assert store.get_cursor("小天", "extracted_until") == ok_ts


@pytest.mark.asyncio
async def test_async_get_set_roundtrip(tmp_path):
    """验证 sync/async 对偶性：aget/aset 应与 get/set 行为一致。"""
    store = _fresh_store(str(tmp_path))
    ts = datetime(2026, 4, 17, 5, 23, 0)
    await store.aset_cursor("小天", "rebuttal_checked_until", ts)
    assert await store.aget_cursor("小天", "rebuttal_checked_until") == ts


@pytest.mark.asyncio
async def test_async_persistence_simulates_restart(tmp_path):
    """异步 API 同样保证持久化（关机重启场景）。"""
    ts_old = datetime(2026, 4, 14, 12, 0, 0)
    store_a = _fresh_store(str(tmp_path))
    await store_a.aset_cursor("小天", "rebuttal_checked_until", ts_old)

    store_b = _fresh_store(str(tmp_path))
    assert await store_b.aget_cursor("小天", "rebuttal_checked_until") == ts_old


@pytest.mark.asyncio
async def test_concurrent_async_sets_do_not_corrupt_file(tmp_path):
    """并发 aset 不同 key 不应互相覆盖（atomic_write_json + per-char lock 保障）。"""
    store = _fresh_store(str(tmp_path))
    base = datetime(2026, 4, 10, 0, 0, 0)
    keys = [f"key_{i}" for i in range(20)]
    await asyncio.gather(*(
        store.aset_cursor("小天", k, base + timedelta(minutes=i))
        for i, k in enumerate(keys)
    ))
    fresh = _fresh_store(str(tmp_path))
    for i, k in enumerate(keys):
        assert await fresh.aget_cursor("小天", k) == base + timedelta(minutes=i)
