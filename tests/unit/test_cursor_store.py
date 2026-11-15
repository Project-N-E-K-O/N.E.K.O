# -*- coding: utf-8 -*-
"""
Unit tests for memory.cursors.CursorStore.

Focus: P0 fix persists cursor files so rebuttal / extract loops resume after process restart
from their previous position, rather than falling back to now - 1h and losing shutdown-period messages.
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
    """Return a fresh CursorStore simulating restart, with no in-memory cache.

    cursors.py uses `from utils.config_manager import get_config_manager`
    at module scope, so patch `memory.cursors.get_config_manager` to affect
    the constructor call rather than patching the original module.
    """
    from memory.cursors import CursorStore
    mock_cm = _make_mock_cm(tmpdir)
    with patch("memory.cursors.get_config_manager", return_value=mock_cm):
        store = CursorStore()
    # Extra protection: directly replace the instance field too, keeping tests isolated even if patching misses.
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
    """Core case: eliminate critical issue 2 by restoring cursors from disk after restart."""
    ts = datetime(2026, 4, 14, 12, 0, 0)  # Three days ago.
    store_before = _fresh_store(str(tmp_path))
    store_before.set_cursor("小天", "rebuttal_checked_until", ts)

    # A fresh instance simulates process restart without shared _cache.
    store_after = _fresh_store(str(tmp_path))
    assert store_after.get_cursor("小天", "rebuttal_checked_until") == ts


def test_multiple_keys_independent(tmp_path):
    """Reserve P1 behavior: one cursors.json stores multiple cursor types without overwriting."""
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
    """Consecutive sets of different keys must preserve earlier keys."""
    store = _fresh_store(str(tmp_path))
    t1 = datetime(2026, 4, 10, 0, 0, 0)
    t2 = datetime(2026, 4, 11, 0, 0, 0)
    store.set_cursor("小天", "rebuttal_checked_until", t1)
    store.set_cursor("小天", "extracted_until", t2)

    # Read disk directly to verify content.
    path = os.path.join(str(tmp_path), "小天", "cursors.json")
    assert os.path.exists(path)
    with open(path, encoding="utf-8") as f:
        on_disk = json.load(f)
    assert on_disk["rebuttal_checked_until"] == t1.isoformat()
    assert on_disk["extracted_until"] == t2.isoformat()


def test_corrupt_cursor_file_returns_none(tmp_path):
    """Corrupt cursors.json must not crash loops; silently fall back to None."""
    char_dir = os.path.join(str(tmp_path), "小天")
    os.makedirs(char_dir, exist_ok=True)
    with open(os.path.join(char_dir, "cursors.json"), "w", encoding="utf-8") as f:
        f.write("this is not json {{{{")

    store = _fresh_store(str(tmp_path))
    assert store.get_cursor("小天", "rebuttal_checked_until") is None


def test_unparsable_value_falls_back_to_none(tmp_path):
    """A non-ISO8601 value yields None for its key while other keys remain valid."""
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
    """Verify sync/async symmetry: aget/aset must behave like get/set."""
    store = _fresh_store(str(tmp_path))
    ts = datetime(2026, 4, 17, 5, 23, 0)
    await store.aset_cursor("小天", "rebuttal_checked_until", ts)
    assert await store.aget_cursor("小天", "rebuttal_checked_until") == ts


@pytest.mark.asyncio
async def test_async_persistence_simulates_restart(tmp_path):
    """Async APIs also guarantee persistence across restart."""
    ts_old = datetime(2026, 4, 14, 12, 0, 0)
    store_a = _fresh_store(str(tmp_path))
    await store_a.aset_cursor("小天", "rebuttal_checked_until", ts_old)

    store_b = _fresh_store(str(tmp_path))
    assert await store_b.aget_cursor("小天", "rebuttal_checked_until") == ts_old


@pytest.mark.asyncio
async def test_concurrent_async_sets_do_not_corrupt_file(tmp_path):
    """Concurrent aset calls for different keys must not overwrite each other (atomic_write_json + per-character lock)."""
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
