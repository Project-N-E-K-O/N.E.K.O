# -*- coding: utf-8 -*-
"""
Unit tests for memory_server._resolve_rebuttal_start_time.

Covers the three decision branches of the rebuttal loop's start-time resolver:
  1. cursor is None        → fallback to now - LOOKBACK_HOURS
  2. cursor in the past    → return cursor
  3. cursor in the future  → fallback + self-heal (overwrite cursor to now)
"""
from __future__ import annotations

from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest


def _install_fresh_cursor_store(tmpdir: str):
    """Replace memory_server.cursor_store with one backed by tmpdir."""
    from memory.cursors import CursorStore
    import memory_server

    mock_cm = MagicMock()
    mock_cm.memory_dir = tmpdir
    with patch("memory.cursors.get_config_manager", return_value=mock_cm):
        store = CursorStore()
    store._config_manager = mock_cm
    memory_server.cursor_store = store
    return store


@pytest.mark.asyncio
async def test_resolve_returns_fallback_when_cursor_missing(tmp_path):
    _install_fresh_cursor_store(str(tmp_path))
    import memory_server

    now = datetime(2026, 4, 17, 12, 0, 0)
    start = await memory_server._resolve_rebuttal_start_time("小天", now)
    expected = now - timedelta(hours=memory_server.REBUTTAL_FIRST_RUN_LOOKBACK_HOURS)
    assert start == expected


@pytest.mark.asyncio
async def test_resolve_anchors_fallback_on_first_call(tmp_path):
    """首次启动 cursor=None 时必须把 fallback 落盘锚定，否则 LLM 连续失败
    会让 fallback 随 now 滑动、最早段消息被永久跳过。"""
    store = _install_fresh_cursor_store(str(tmp_path))
    import memory_server
    from memory.cursors import CURSOR_REBUTTAL_CHECKED_UNTIL

    now = datetime(2026, 4, 17, 12, 0, 0)
    expected_fallback = now - timedelta(hours=memory_server.REBUTTAL_FIRST_RUN_LOOKBACK_HOURS)

    start = await memory_server._resolve_rebuttal_start_time("小天", now)
    assert start == expected_fallback

    # 关键：fallback 已被持久化，下轮即便 now 推进、cursor 也不再为 None
    persisted = await store.aget_cursor("小天", CURSOR_REBUTTAL_CHECKED_UNTIL)
    assert persisted == expected_fallback

    # 模拟下轮：5 分钟后再调，应走 in-past 分支返回上轮锚定的 fallback，
    # 而不是用新 now 重算一个滑动后的 fallback。
    later = now + timedelta(minutes=5)
    start_2 = await memory_server._resolve_rebuttal_start_time("小天", later)
    assert start_2 == expected_fallback  # 锚点未漂移


@pytest.mark.asyncio
async def test_resolve_returns_persisted_cursor_when_in_past(tmp_path):
    """Normal path: cursor from 2 hours ago should be returned as-is."""
    store = _install_fresh_cursor_store(str(tmp_path))
    import memory_server
    from memory.cursors import CURSOR_REBUTTAL_CHECKED_UNTIL

    now = datetime(2026, 4, 17, 12, 0, 0)
    persisted = now - timedelta(hours=2)
    await store.aset_cursor("小天", CURSOR_REBUTTAL_CHECKED_UNTIL, persisted)

    start = await memory_server._resolve_rebuttal_start_time("小天", now)
    assert start == persisted


@pytest.mark.asyncio
async def test_resolve_fallback_and_self_heal_on_clock_rollback(tmp_path):
    """Cursor greater than now (clock rollback) → fallback returned AND cursor
    healed to `fallback`（非 now，保留本轮 [fallback, now] 的 LLM 重试语义）."""
    store = _install_fresh_cursor_store(str(tmp_path))
    import memory_server
    from memory.cursors import CURSOR_REBUTTAL_CHECKED_UNTIL

    now = datetime(2026, 4, 17, 12, 0, 0)
    # Simulate: yesterday's cursor says "future" relative to current (rolled-back) clock
    future_cursor = now + timedelta(days=1)
    await store.aset_cursor("小天", CURSOR_REBUTTAL_CHECKED_UNTIL, future_cursor)

    start = await memory_server._resolve_rebuttal_start_time("小天", now)

    # Return value: fallback window
    expected_fallback = now - timedelta(hours=memory_server.REBUTTAL_FIRST_RUN_LOOKBACK_HOURS)
    assert start == expected_fallback

    # Side effect: cursor healed to `fallback` — below `now`, so下轮不再命中
    # rollback 分支；若本轮 LLM 失败，下轮仍能覆盖 [fallback, new_now] 重试
    healed = await store.aget_cursor("小天", CURSOR_REBUTTAL_CHECKED_UNTIL)
    assert healed == expected_fallback


@pytest.mark.asyncio
async def test_resolve_persists_healed_cursor_across_instances(tmp_path):
    """Self-heal must survive process restart — the overwrite is on disk, not just memory."""
    store = _install_fresh_cursor_store(str(tmp_path))
    import memory_server
    from memory.cursors import CURSOR_REBUTTAL_CHECKED_UNTIL, CursorStore

    now = datetime(2026, 4, 17, 12, 0, 0)
    await store.aset_cursor("小天", CURSOR_REBUTTAL_CHECKED_UNTIL, now + timedelta(days=1))
    await memory_server._resolve_rebuttal_start_time("小天", now)

    # Spawn a brand-new CursorStore pointed at the same dir — simulates restart
    fresh_cm = MagicMock()
    fresh_cm.memory_dir = str(tmp_path)
    with patch("memory.cursors.get_config_manager", return_value=fresh_cm):
        fresh_store = CursorStore()
    fresh_store._config_manager = fresh_cm

    expected_fallback = now - timedelta(hours=memory_server.REBUTTAL_FIRST_RUN_LOOKBACK_HOURS)
    healed = await fresh_store.aget_cursor("小天", CURSOR_REBUTTAL_CHECKED_UNTIL)
    assert healed == expected_fallback
