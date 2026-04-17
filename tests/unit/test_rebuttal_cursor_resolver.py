# -*- coding: utf-8 -*-
"""
Unit tests for memory_server._resolve_rebuttal_start_time.

Covers the three decision branches of the rebuttal loop's start-time resolver:
  1. cursor is None        → fallback to now - LOOKBACK_HOURS
  2. cursor in the past    → return cursor
  3. cursor in the future  → fallback + self-heal (overwrite cursor to now)
"""
from __future__ import annotations

import os
import tempfile
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
    """Cursor greater than now (clock rollback) → fallback returned AND cursor overwritten to now."""
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

    # Side effect: cursor has been healed to `now` (no longer in the future)
    healed = await store.aget_cursor("小天", CURSOR_REBUTTAL_CHECKED_UNTIL)
    assert healed == now


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

    healed = await fresh_store.aget_cursor("小天", CURSOR_REBUTTAL_CHECKED_UNTIL)
    assert healed == now
