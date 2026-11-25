from __future__ import annotations

import asyncio
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


class _Store:
    enabled = True
    _db_path = ":memory:"

    def __init__(self) -> None:
        self.data: dict[str, object] = {}

    def _read_value(self, key: str, default: object = None) -> object:
        return self.data.get(key, default)

    def _write_value(self, key: str, value: object) -> None:
        self.data[key] = value


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
@pytest.mark.asyncio
async def test_memo_reminder_enforces_configured_max_reminders_and_config_change() -> None:
    plugin = _plugin()
    cfg_state: dict[str, object] = {"memo": {"max_reminders": 0, "timezone": "UTC"}}

    class _FakeConfig:
        async def dump(self, **_kwargs: object) -> dict[str, object]:
            return cfg_state

    plugin.config = _FakeConfig()
    try:
        start_res = await plugin.startup()
        assert start_res.is_ok()
        assert str(plugin._tz) == "UTC"

        # max_reminders = 0 prevents adding even the first reminder.
        rejected_zero = await plugin.add_reminder(time="30m", message="m0")
        assert rejected_zero.is_err()

        # Hot reload via config_change updates limit to 2 and timezone to Asia/Tokyo.
        cfg_state = {"memo": {"max_reminders": 2, "timezone": "Asia/Tokyo"}}
        change_res = await plugin.on_config_change()
        assert change_res.is_ok()
        assert str(plugin._tz) == "Asia/Tokyo"

        assert (await plugin.add_reminder(time="30m", message="m1")).is_ok()
        assert (await plugin.add_reminder(time="30m", message="m2")).is_ok()
        rejected_third = await plugin.add_reminder(time="30m", message="m3")
        assert rejected_third.is_err()

        # Increasing limit beyond default 200 works too.
        cfg_state = {"memo": {"max_reminders": 250, "timezone": "Asia/Shanghai"}}
        await plugin.on_config_change()
        assert plugin._max_reminders == 250

        # Invalid max_reminders falls back to 200.
        for bad in (-1, "abc", True, 1.5):
            cfg_state = {"memo": {"max_reminders": bad}}
            await plugin.on_config_change()
            assert plugin._max_reminders == 200
    finally:
        await asyncio.to_thread(plugin.shutdown)


@pytest.mark.plugin_unit
@pytest.mark.asyncio
async def test_memo_reminder_temporary_config_change_uses_new_config_over_stale_dump() -> None:
    plugin = _plugin()

    class _StaleDiskConfig:
        async def dump(self, **_kwargs: object) -> dict[str, object]:
            return {"memo": {"max_reminders": 200, "timezone": "UTC"}}

    plugin.config = _StaleDiskConfig()
    try:
        start_res = await plugin.startup()
        assert start_res.is_ok()
        assert plugin._max_reminders == 200
        assert str(plugin._tz) == "UTC"

        # Temporary hot update provides new_config directly even while config.dump() is stale.
        change_res = await plugin.on_config_change(
            new_config={"memo": {"max_reminders": 1, "timezone": "America/New_York"}},
            mode="temporary",
        )
        assert change_res.is_ok()
        assert plugin._max_reminders == 1
        assert str(plugin._tz) == "America/New_York"

        assert (await plugin.add_reminder(time="30m", message="only-one")).is_ok()
        blocked = await plugin.add_reminder(time="30m", message="over-temp-limit")
        assert blocked.is_err()

        # Unrelated temporary config patch without [memo] preserves current limit & timezone.
        unrelated_res = await plugin.on_config_change(
            new_config={"other": {"flag": True}},
            mode="temporary",
        )
        assert unrelated_res.is_ok()
        assert plugin._max_reminders == 1
        assert str(plugin._tz) == "America/New_York"

        # Deleting [memo] keys in full snapshot resets them to defaults instead of keeping stale values.
        reset_res = await plugin.on_config_change(
            new_config={"memo": {}},
            mode="temporary",
        )
        assert reset_res.is_ok()
        assert plugin._max_reminders == 200
        assert str(plugin._tz) == "Asia/Shanghai"

        # Host full-snapshot replacement where [memo] table was removed completely resets to defaults,
        # while direct partial calls without old_config keep preserving existing memo settings.
        custom_res = await plugin.on_config_change(
            old_config={"memo": {}},
            new_config={"memo": {"max_reminders": 3, "timezone": "UTC"}},
            mode="temporary",
        )
        assert custom_res.is_ok()
        assert plugin._max_reminders == 3
        assert str(plugin._tz) == "UTC"

        partial_no_memo = await plugin.on_config_change(
            new_config={"other": {"flag": False}},
            mode="temporary",
        )
        assert partial_no_memo.is_ok()
        assert plugin._max_reminders == 3
        assert str(plugin._tz) == "UTC"

        full_without_memo = await plugin.on_config_change(
            old_config={"memo": {"max_reminders": 3, "timezone": "UTC"}},
            new_config={"other": {"flag": False}},
            mode="temporary",
        )
        assert full_without_memo.is_ok()
        assert plugin._max_reminders == 200
        assert str(plugin._tz) == "Asia/Shanghai"
    finally:
        await asyncio.to_thread(plugin.shutdown)
