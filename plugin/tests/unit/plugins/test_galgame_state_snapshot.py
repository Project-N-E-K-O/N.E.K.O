"""Tests for GalgamePlugin state snapshot caching (P0-1/P0-3)."""

import threading
from unittest.mock import MagicMock, patch

import pytest

from plugin.plugins.galgame_plugin.state import GalgameSharedState, build_initial_state
from plugin.plugins.galgame_plugin.models import MODE_COMPANION, ADVANCE_SPEED_MEDIUM


class _FakePlugin:
    """Minimal stub that mirrors GalgamePlugin's snapshot/commit/dirty logic."""

    def __init__(self):
        self._state = build_initial_state(
            mode=MODE_COMPANION,
            push_notifications=True,
            advance_speed=ADVANCE_SPEED_MEDIUM,
        )
        self._state_lock = threading.Lock()
        self._state_dirty = True
        self._cached_snapshot = None

    def _snapshot_state(self, *, fresh=False):
        from plugin.plugins.galgame_plugin.models import json_copy
        with self._state_lock:
            if not fresh and not self._state_dirty and self._cached_snapshot is not None:
                return self._cached_snapshot
            state = self._state
            snap = {
                "bound_game_id": state.bound_game_id,
                "available_game_ids": list(state.available_game_ids),
                "mode": state.mode,
                "push_notifications": state.push_notifications,
                "advance_speed": state.advance_speed,
                "active_game_id": state.active_game_id,
                "active_session_id": state.active_session_id,
                "active_session_meta": json_copy(state.active_session_meta),
                "active_data_source": state.active_data_source,
                "latest_snapshot": json_copy(state.latest_snapshot),
                "history_events": json_copy(state.history_events),
                "history_lines": json_copy(state.history_lines),
                "history_observed_lines": json_copy(state.history_observed_lines),
                "history_choices": json_copy(state.history_choices),
                "dedupe_window": json_copy(state.dedupe_window),
                "line_buffer": state.line_buffer,
                "stream_reset_pending": state.stream_reset_pending,
                "last_error": json_copy(state.last_error),
                "next_poll_at_monotonic": state.next_poll_at_monotonic,
                "current_connection_state": state.current_connection_state,
                "events_byte_offset": state.events_byte_offset,
                "events_file_size": state.events_file_size,
                "last_seq": state.last_seq,
                "last_seen_data_monotonic": state.last_seen_data_monotonic,
                "warmup_session_id": state.warmup_session_id,
                "memory_reader_runtime": json_copy(state.memory_reader_runtime),
                "ocr_reader_runtime": json_copy(state.ocr_reader_runtime),
                "ocr_capture_profiles": json_copy(state.ocr_capture_profiles),
                "ocr_window_target": json_copy(state.ocr_window_target),
                "plugin_error": state.plugin_error,
            }
            self._cached_snapshot = snap
            self._state_dirty = False
            return snap

    def _mark_state_dirty(self):
        with self._state_lock:
            self._state_dirty = True
            self._cached_snapshot = None

    def _commit_state(self, payload):
        from plugin.plugins.galgame_plugin.models import json_copy
        with self._state_lock:
            state = self._state
            state.bound_game_id = str(payload["bound_game_id"])
            state.available_game_ids = list(payload["available_game_ids"])
            state.mode = str(payload["mode"])
            state.push_notifications = bool(payload["push_notifications"])
            state.last_error = json_copy(payload["last_error"])
            state.next_poll_at_monotonic = float(payload["next_poll_at_monotonic"])
            state.current_connection_state = str(payload["current_connection_state"])
            state.plugin_error = str(payload["plugin_error"])
            self._state_dirty = True
            self._cached_snapshot = None

    def _record_error(self, error):
        from plugin.plugins.galgame_plugin.models import json_copy
        with self._state_lock:
            self._state.last_error = json_copy(error)
            self._state_dirty = True
            self._cached_snapshot = None


class TestSnapshotIsolation:
    """Snapshot returns an independent copy; modifying it does not affect state."""

    def test_modify_snapshot_dict_does_not_affect_state(self):
        p = _FakePlugin()
        snap = p._snapshot_state()
        snap["bound_game_id"] = "MODIFIED"
        assert p._state.bound_game_id == ""

    def test_modify_nested_dict_does_not_affect_state(self):
        p = _FakePlugin()
        snap = p._snapshot_state()
        snap["last_error"]["injected"] = True
        assert "injected" not in p._state.last_error

    def test_modify_history_list_does_not_affect_state(self):
        p = _FakePlugin()
        p._state.history_lines = [{"text": "original"}]
        p._mark_state_dirty()
        snap = p._snapshot_state()
        snap["history_lines"].append({"text": "injected"})
        assert len(p._state.history_lines) == 1


class TestSnapshotCache:
    """Cache returns the same object when state has not changed."""

    def test_consecutive_calls_return_same_object(self):
        p = _FakePlugin()
        snap1 = p._snapshot_state()
        snap2 = p._snapshot_state()
        assert snap1 is snap2

    def test_cache_hit_skips_deepcopy(self):
        p = _FakePlugin()
        snap1 = p._snapshot_state()
        # After caching, state is not dirty
        assert not p._state_dirty
        assert p._cached_snapshot is snap1

    def test_mark_dirty_invalidates_cache(self):
        p = _FakePlugin()
        snap1 = p._snapshot_state()
        p._mark_state_dirty()
        snap2 = p._snapshot_state()
        assert snap1 is not snap2

    def test_fresh_true_bypasses_cache(self):
        p = _FakePlugin()
        snap1 = p._snapshot_state()
        snap2 = p._snapshot_state(fresh=True)
        assert snap1 is not snap2

    def test_initial_state_is_dirty(self):
        p = _FakePlugin()
        assert p._state_dirty is True
        assert p._cached_snapshot is None


class TestCommitInvalidatesCache:
    """_commit_state must invalidate the snapshot cache."""

    def test_commit_invalidates_cache(self):
        p = _FakePlugin()
        snap1 = p._snapshot_state()
        snap1["bound_game_id"] = "game1"
        snap1["mode"] = MODE_COMPANION
        snap1["push_notifications"] = True
        snap1["last_error"] = {}
        snap1["next_poll_at_monotonic"] = 0.0
        snap1["current_connection_state"] = "idle"
        snap1["plugin_error"] = ""
        snap1["available_game_ids"] = []
        p._commit_state(snap1)
        snap2 = p._snapshot_state()
        assert snap1 is not snap2
        assert snap2["bound_game_id"] == "game1"


class TestRecordErrorInvalidatesCache:
    """_record_error must invalidate the snapshot cache."""

    def test_record_error_invalidates_cache(self):
        p = _FakePlugin()
        snap1 = p._snapshot_state()
        p._record_error({"kind": "error", "message": "test"})
        snap2 = p._snapshot_state()
        assert snap1 is not snap2
        assert snap2["last_error"]["kind"] == "error"


class TestDirectWriteInvalidatesCache:
    """Direct state writes via _mark_state_dirty must invalidate cache."""

    def test_direct_write_to_bound_game_id(self):
        p = _FakePlugin()
        snap1 = p._snapshot_state()
        p._state.bound_game_id = "direct_write"
        p._mark_state_dirty()
        snap2 = p._snapshot_state()
        assert snap2["bound_game_id"] == "direct_write"
        assert snap1 is not snap2


class TestConcurrentAccess:
    """Snapshot + commit from multiple threads does not deadlock or crash."""

    def test_concurrent_snapshot_and_commit(self):
        p = _FakePlugin()
        errors = []

        def snapshot_loop():
            try:
                for _ in range(50):
                    snap = p._snapshot_state()
                    assert isinstance(snap, dict)
            except Exception as exc:
                errors.append(exc)

        def commit_loop():
            try:
                for i in range(50):
                    payload = {
                        "bound_game_id": f"game_{i}",
                        "available_game_ids": [],
                        "mode": MODE_COMPANION,
                        "push_notifications": True,
                        "last_error": {},
                        "next_poll_at_monotonic": 0.0,
                        "current_connection_state": "idle",
                        "plugin_error": "",
                    }
                    p._commit_state(payload)
            except Exception as exc:
                errors.append(exc)

        t1 = threading.Thread(target=snapshot_loop)
        t2 = threading.Thread(target=commit_loop)
        t1.start()
        t2.start()
        t1.join(timeout=10)
        t2.join(timeout=10)
        assert not errors
