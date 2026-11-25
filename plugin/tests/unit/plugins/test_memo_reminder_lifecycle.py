from __future__ import annotations

import copy
import threading
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

from plugin.plugins.memo_reminder import MemoReminderPlugin, _STORE_KEY


class _Logger:
    def info(self, *args, **kwargs) -> None:
        pass

    def warning(self, *args, **kwargs) -> None:
        pass

    def exception(self, *args, **kwargs) -> None:
        pass

    def error(self, *args, **kwargs) -> None:
        pass


class _Store:
    enabled = True
    _db_path = ":memory:"

    def __init__(self) -> None:
        self.data: dict[str, object] = {}

    def _read_value(self, key: str, default: object = None) -> object:
        return copy.deepcopy(self.data.get(key, default))

    def _write_value(self, key: str, value: object) -> None:
        self.data[key] = copy.deepcopy(value)


class _Context:
    _current_lanlan = "Lanlan"

    def __init__(self) -> None:
        self.pushed: list[dict[str, object]] = []

    def push_message(self, **kwargs: object) -> dict[str, object]:
        self.pushed.append(kwargs)
        return {"ok": True}


def _plugin() -> MemoReminderPlugin:
    plugin = MemoReminderPlugin.__new__(MemoReminderPlugin)
    plugin.ctx = _Context()
    plugin.store = _Store()
    plugin.logger = _Logger()
    plugin._reminders_lock = threading.Lock()
    plugin._wake_event = threading.Event()
    plugin._stop_event = threading.Event()
    plugin._checker_thread = None
    plugin._tz = ZoneInfo("Asia/Shanghai")
    return plugin


def _due_iso(seconds_ago: int = 1) -> str:
    return (datetime.now(timezone.utc) - timedelta(seconds=seconds_ago)).isoformat()


def _legacy_keys() -> set[str]:
    return {
        "agent_task_id",
        "deferred_bind_pending",
        "callback_pending",
        "callback_error",
        "callback_retry_count",
    }


@pytest.mark.plugin_unit
@pytest.mark.asyncio
async def test_add_reminder_completes_scheduling_without_deferred_task_fields() -> None:
    plugin = _plugin()

    result = await plugin.add_reminder(time="30m", message="喝水")

    assert result.is_ok()
    payload = result.value
    assert payload["status"] == "scheduled"
    assert "deferred" not in payload

    reminders = plugin.store.data[_STORE_KEY]
    assert isinstance(reminders, list)
    assert len(reminders) == 1
    reminder = reminders[0]
    assert isinstance(reminder, dict)
    assert reminder["message"] == "喝水"
    assert _legacy_keys().isdisjoint(reminder)


@pytest.mark.plugin_unit
def test_due_legacy_deferred_record_pushes_reminder_without_task_callback() -> None:
    plugin = _plugin()
    due = _due_iso()
    plugin._save_reminders([
        {
            "id": "legacy123456",
            "message": "喝水",
            "trigger_at": due,
            "created_at": due,
            "repeat": "once",
            "agent_task_id": "task-1",
            "deferred_bind_pending": False,
            "callback_pending": True,
            "callback_error": "old failure",
            "callback_retry_count": 2,
        }
    ])

    plugin._fire_due_reminders()

    assert len(plugin.ctx.pushed) == 1
    pushed = plugin.ctx.pushed[0]
    assert pushed["parts"] == [{"type": "text", "text": "⏰ 之前设置的提醒时间到了：喝水"}]
    assert pushed["metadata"]["event_type"] == "reminder_fired"
    assert plugin.store.data[_STORE_KEY] == []


@pytest.mark.plugin_unit
def test_delivered_legacy_callback_record_is_removed_without_duplicate_push() -> None:
    plugin = _plugin()
    due = _due_iso()
    plugin._save_reminders([
        {
            "id": "delivered123",
            "message": "喝水",
            "trigger_at": due,
            "created_at": due,
            "repeat": "once",
            "delivered": True,
            "agent_task_id": "task-1",
            "callback_pending": True,
            "callback_retry_count": 3,
        }
    ])

    plugin._fire_due_reminders()

    assert plugin.ctx.pushed == []
    assert plugin.store.data[_STORE_KEY] == []


@pytest.mark.plugin_unit
def test_repeating_due_reminder_reschedules_and_strips_legacy_deferred_fields() -> None:
    plugin = _plugin()
    due = _due_iso()
    plugin._save_reminders([
        {
            "id": "repeat123456",
            "message": "站起来",
            "trigger_at": due,
            "created_at": due,
            "repeat": "10s",
            "max_count": 2,
            "fire_count": 0,
            "agent_task_id": "task-1",
            "deferred_bind_pending": False,
            "callback_pending": True,
        }
    ])

    plugin._fire_due_reminders()

    assert len(plugin.ctx.pushed) == 1
    reminders = plugin.store.data[_STORE_KEY]
    assert isinstance(reminders, list)
    assert len(reminders) == 1
    reminder = reminders[0]
    assert isinstance(reminder, dict)
    assert reminder["id"] == "repeat123456"
    assert reminder["fire_count"] == 1
    assert "delivered" not in reminder
    assert _legacy_keys().isdisjoint(reminder)
    assert datetime.fromisoformat(reminder["trigger_at"]) > datetime.now(timezone.utc)


@pytest.mark.plugin_unit
def test_load_reminders_drops_non_dict_dirty_records() -> None:
    plugin = _plugin()
    future = (datetime.now(timezone.utc) + timedelta(minutes=10)).isoformat()
    plugin.store.data[_STORE_KEY] = [
        "bad-record",
        None,
        42,
        {
            "id": "valid123456",
            "message": "喝水",
            "trigger_at": future,
            "created_at": future,
            "repeat": "once",
            "agent_task_id": "task-1",
            "callback_pending": True,
        },
    ]

    reminders = plugin._load_reminders()

    assert reminders == [
        {
            "id": "valid123456",
            "message": "喝水",
            "trigger_at": future,
            "created_at": future,
            "repeat": "once",
        }
    ]


@pytest.mark.plugin_unit
@pytest.mark.asyncio
async def test_bind_task_is_kept_as_compatibility_noop() -> None:
    plugin = _plugin()
    future = (datetime.now(timezone.utc) + timedelta(minutes=10)).isoformat()
    plugin._save_reminders([
        {
            "id": "compat123456",
            "message": "喝水",
            "trigger_at": future,
            "created_at": future,
            "repeat": "once",
            "agent_task_id": "old-task",
            "deferred_bind_pending": True,
            "callback_pending": True,
            "callback_error": "old failure",
            "callback_retry_count": 2,
        }
    ])

    result = await plugin.bind_task(reminder_id="compat123456", agent_task_id="task-1")

    assert result.is_ok()
    assert result.value == {"bound": True, "ignored": True}
    reminder = plugin.store.data[_STORE_KEY][0]
    assert _legacy_keys().isdisjoint(reminder)


@pytest.mark.plugin_unit
def test_failed_push_submission_retains_oneshot_reminder_with_backoff_and_recovers() -> None:
    plugin = _plugin()
    due = _due_iso()
    plugin._save_reminders([
        {
            "id": "oneshot123",
            "message": "喝水",
            "trigger_at": due,
            "created_at": due,
            "repeat": "once",
        }
    ])

    receipts = iter([
        {"ok": False, "submitted": False, "reason": "backpressure"},
        {"submitted": True},
    ])
    plugin.ctx.push_message = lambda **kwargs: (plugin.ctx.pushed.append(kwargs) or next(receipts))  # type: ignore[method-assign]

    plugin._fire_due_reminders()
    assert len(plugin.ctx.pushed) == 1

    reminders = plugin.store.data[_STORE_KEY]
    assert len(reminders) == 1
    rem = reminders[0]
    assert rem.get("delivered") is not True
    assert rem["delivery_retry_count"] == 1
    assert datetime.fromisoformat(rem["next_retry_at"]) > datetime.now(timezone.utc)

    # _next_trigger_seconds must respect backoff instead of spinning at <= 0s.
    wait_sec = plugin._next_trigger_seconds()
    assert wait_sec is not None and wait_sec > 0.5

    # Calling before next_retry_at must NOT push again.
    plugin._fire_due_reminders()
    assert len(plugin.ctx.pushed) == 1

    # Advance past next_retry_at and verify recovery deletes one-shot reminder.
    rem["next_retry_at"] = _due_iso(1)
    plugin._save_reminders([rem])
    plugin._fire_due_reminders()
    assert len(plugin.ctx.pushed) == 2
    assert plugin.store.data[_STORE_KEY] == []


@pytest.mark.plugin_unit
def test_failed_push_does_not_advance_repeating_reminder_until_submitted() -> None:
    plugin = _plugin()
    due = _due_iso()
    plugin._save_reminders([
        {
            "id": "repeatfail1",
            "message": "休息",
            "trigger_at": due,
            "created_at": due,
            "repeat": "30s",
            "max_count": 3,
            "fire_count": 0,
        }
    ])
    receipts = iter([
        {"ok": False, "submitted": False, "reason": "transport_unavailable"},
        {"submitted": True},
    ])
    plugin.ctx.push_message = lambda **kwargs: (plugin.ctx.pushed.append(kwargs) or next(receipts))  # type: ignore[method-assign]

    plugin._fire_due_reminders()
    rem = plugin.store.data[_STORE_KEY][0]
    assert rem["fire_count"] == 0
    assert rem["trigger_at"] == due
    assert rem["delivery_retry_count"] == 1

    rem["next_retry_at"] = _due_iso(1)
    plugin._save_reminders([rem])
    plugin._fire_due_reminders()
    rem_after = plugin.store.data[_STORE_KEY][0]
    assert rem_after["fire_count"] == 1
    assert "delivery_retry_count" not in rem_after
    assert "next_retry_at" not in rem_after
    assert "delivered" not in rem_after


@pytest.mark.plugin_unit
def test_phase2_persists_delivered_immediately_and_phase3_respects_concurrent_delete(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plugin = _plugin()
    due = _due_iso()
    plugin._save_reminders([
        {
            "id": "crash1",
            "message": "喝水",
            "trigger_at": due,
            "created_at": due,
            "repeat": "once",
        }
    ])
    plugin.ctx.push_message = lambda **kwargs: (plugin.ctx.pushed.append(kwargs) or {"submitted": True})  # type: ignore[method-assign]

    real_reschedule = plugin._reschedule

    def _boom_reschedule(r, now):
        raise RuntimeError("phase3 crash")

    monkeypatch.setattr(plugin, "_reschedule", _boom_reschedule)
    with pytest.raises(RuntimeError, match="phase3 crash"):
        plugin._fire_due_reminders()
    assert plugin.store.data[_STORE_KEY][0]["delivered"] is True

    # Next tick must NOT push duplicate message and cleans up cleanly.
    monkeypatch.setattr(plugin, "_reschedule", real_reschedule)
    plugin._fire_due_reminders()
    assert len(plugin.ctx.pushed) == 1
    assert plugin.store.data[_STORE_KEY] == []

    # Concurrent delete during Phase 2 must not be resurrected in Phase 3.
    plugin._save_reminders([
        {
            "id": "conc1",
            "message": "测试",
            "trigger_at": due,
            "created_at": due,
            "repeat": "30s",
        }
    ])

    def _push_and_delete(**kwargs):
        plugin.ctx.pushed.append(kwargs)
        plugin.store.data[_STORE_KEY] = []
        return {"submitted": True}

    plugin.ctx.push_message = _push_and_delete  # type: ignore[method-assign]
    plugin._fire_due_reminders()
    assert plugin.store.data[_STORE_KEY] == []


@pytest.mark.plugin_unit
def test_reschedule_handles_naive_iso_trigger_at_and_unrecoverable_rejection() -> None:
    plugin = _plugin()
    naive_due = (datetime.now(timezone.utc) - timedelta(hours=1)).replace(tzinfo=None).isoformat()
    plugin._save_reminders([
        {
            "id": "naive-daily",
            "message": "喝水",
            "trigger_at": naive_due,
            "created_at": naive_due,
            "repeat": "daily",
        }
    ])
    plugin.ctx.push_message = lambda **kwargs: (plugin.ctx.pushed.append(kwargs) or {"submitted": True})  # type: ignore[method-assign]
    plugin._fire_due_reminders()
    rem = plugin.store.data[_STORE_KEY][0]
    assert datetime.fromisoformat(rem["trigger_at"]) > datetime.now(timezone.utc)

    # Unrecoverable push rejection should advance repeating reminder instead of retrying forever.
    due2 = _due_iso()
    plugin._save_reminders([
        {
            "id": "too-large",
            "message": "x" * 100,
            "trigger_at": due2,
            "created_at": due2,
            "repeat": "daily",
        }
    ])
    plugin.ctx.push_message = lambda **kwargs: {"submitted": False, "reason": "payload_too_large"}  # type: ignore[method-assign]
    plugin._fire_due_reminders()
    rem2 = plugin.store.data[_STORE_KEY][0]
    assert rem2["trigger_at"] != due2
    assert "delivery_retry_count" not in rem2


@pytest.mark.plugin_unit
def test_phase2_save_error_does_not_skip_phase3(monkeypatch: pytest.MonkeyPatch) -> None:
    plugin = _plugin()
    due = _due_iso()
    plugin._save_reminders([
        {
            "id": "p2err",
            "message": "喝水",
            "trigger_at": due,
            "created_at": due,
            "repeat": "once",
        }
    ])
    plugin.ctx.push_message = lambda **kwargs: {"submitted": True}  # type: ignore[method-assign]

    real_save = plugin._save_reminders_unlocked
    calls = {"n": 0}

    def _fail_phase2_save(items):
        calls["n"] += 1
        if calls["n"] == 2:
            raise RuntimeError("transient phase2 store error")
        real_save(items)

    monkeypatch.setattr(plugin, "_save_reminders_unlocked", _fail_phase2_save)
    plugin._fire_due_reminders()
    assert plugin.store.data[_STORE_KEY] == []
