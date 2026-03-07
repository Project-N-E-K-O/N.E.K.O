"""
备忘提醒插件 (Memo Reminder)

在指定时间通过 push_message 提醒主人。
支持一次性提醒、每日重复、自定义间隔重复。
数据通过 PluginStore (SQLite KV) 持久化，重启不丢失。
"""

from __future__ import annotations

import threading
import time
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple
from zoneinfo import ZoneInfo

from plugin.sdk.base import NekoPluginBase
from plugin.sdk.decorators import lifecycle, neko_plugin, plugin_entry
from plugin.sdk import ok, fail

_STORE_KEY = "reminders"

_TZ_UTC = timezone.utc
_DEFAULT_TZ = "Asia/Shanghai"

_FORMATS_WITH_DATE = (
    ("%Y-%m-%d %H:%M:%S", True),
    ("%Y-%m-%d %H:%M", True),
    ("%Y-%m-%dT%H:%M:%S", True),
    ("%Y-%m-%dT%H:%M", True),
    ("%Y/%m/%d %H:%M:%S", True),
    ("%Y/%m/%d %H:%M", True),
    ("%m-%d %H:%M", True),
    ("%H:%M:%S", False),
    ("%H:%M", False),
)


def _now(tz: timezone | ZoneInfo) -> datetime:
    return datetime.now(tz)


def _parse_time(raw: str, tz: timezone | ZoneInfo) -> Optional[Tuple[datetime, bool]]:
    """解析时间字符串，返回 (aware datetime, 是否含日期)。

    时间字符串先按用户时区解释，再转为 UTC 存储。
    """
    raw = raw.strip()

    for fmt, has_date in _FORMATS_WITH_DATE:
        try:
            dt = datetime.strptime(raw, fmt)
        except ValueError:
            continue

        now = _now(tz)

        if dt.year == 1900:
            dt = dt.replace(
                year=now.year,
                month=now.month if "%m" not in fmt else dt.month,
                day=now.day if "%d" not in fmt else dt.day,
            )

        dt = dt.replace(tzinfo=tz)
        return dt.astimezone(_TZ_UTC), has_date

    if raw.endswith(("m", "h", "s", "d")):
        try:
            unit = raw[-1]
            val = float(raw[:-1])
            delta_map = {"s": "seconds", "m": "minutes", "h": "hours", "d": "days"}
            return _now(_TZ_UTC) + timedelta(**{delta_map[unit]: val}), True
        except (ValueError, KeyError):
            pass

    return None


@neko_plugin
class MemoReminderPlugin(NekoPluginBase):

    def __init__(self, ctx):
        super().__init__(ctx)
        self.file_logger = self.enable_file_logging(log_level="INFO")
        self.logger = self.file_logger
        self._stop_event = threading.Event()
        self._wake_event = threading.Event()
        self._checker_thread: Optional[threading.Thread] = None

    def _load_reminders(self) -> List[Dict[str, Any]]:
        data = self.store.get(_STORE_KEY, [])
        return data if isinstance(data, list) else []

    def _save_reminders(self, reminders: List[Dict[str, Any]]) -> None:
        self.store.set(_STORE_KEY, reminders)

    def _notify_change(self) -> None:
        """唤醒 checker 线程，使其立即重新计算下次触发时间。"""
        self._wake_event.set()

    @lifecycle(id="startup")
    async def startup(self, **_):
        cfg = await self.config.dump(timeout=5.0)
        cfg = cfg if isinstance(cfg, dict) else {}
        memo_cfg = cfg.get("memo") if isinstance(cfg.get("memo"), dict) else {}

        tz_name = str(memo_cfg.get("timezone", _DEFAULT_TZ)).strip()
        try:
            self._tz: timezone | ZoneInfo = ZoneInfo(tz_name)
        except (KeyError, Exception):
            self._tz = ZoneInfo(_DEFAULT_TZ)
            self.logger.warning("Invalid timezone {!r}, falling back to {}", tz_name, _DEFAULT_TZ)

        self._stop_event.clear()
        self._wake_event.clear()
        self._checker_thread = threading.Thread(
            target=self._checker_loop,
            daemon=True,
            name="memo-checker",
        )
        self._checker_thread.start()

        count = len(self._load_reminders())
        self.logger.info("MemoReminder started, {} pending reminders, tz={} (event-driven)", count, self._tz)
        return ok(data={"status": "running", "pending": count, "mode": "event-driven", "timezone": str(self._tz)})

    @lifecycle(id="shutdown")
    def shutdown(self, **_):
        self._stop_event.set()
        self._wake_event.set()
        if self._checker_thread and self._checker_thread.is_alive():
            self._checker_thread.join(timeout=3.0)
        self.logger.info("MemoReminder shutdown")
        return ok(data={"status": "shutdown"})

    def _next_trigger_seconds(self) -> Optional[float]:
        """计算距离最近一条提醒的剩余秒数，无提醒返回 None。"""
        reminders = self._load_reminders()
        if not reminders:
            return None
        now = _now(_TZ_UTC)
        earliest: Optional[float] = None
        for r in reminders:
            try:
                dt = datetime.fromisoformat(r.get("trigger_at", ""))
            except (ValueError, TypeError):
                continue
            delta = (dt - now).total_seconds()
            if earliest is None or delta < earliest:
                earliest = delta
        return earliest

    def _checker_loop(self) -> None:
        time.sleep(0.5)
        while not self._stop_event.is_set():
            try:
                self._fire_due_reminders()
            except Exception:
                self.logger.exception("Error in reminder checker")

            wait_sec = self._next_trigger_seconds()
            if wait_sec is not None:
                wait_sec = max(wait_sec, 0.1)
            else:
                wait_sec = 86400.0

            self._wake_event.clear()
            # 精确等到下条提醒时间，或被 _notify_change 唤醒
            self._wake_event.wait(timeout=wait_sec)
            if self._stop_event.is_set():
                break

    def _fire_due_reminders(self) -> None:
        reminders = self._load_reminders()
        if not reminders:
            return

        now = _now(_TZ_UTC)
        fired_ids: list[str] = []
        kept: list[Dict[str, Any]] = []

        for r in reminders:
            trigger_iso = r.get("trigger_at", "")
            try:
                trigger_dt = datetime.fromisoformat(trigger_iso)
            except (ValueError, TypeError):
                kept.append(r)
                continue

            if trigger_dt <= now:
                fired_ids.append(r.get("id", "?"))
                self._push_reminder(r)
                updated = self._reschedule(r, now)
                if updated:
                    kept.append(updated)
            else:
                kept.append(r)

        if fired_ids:
            self._save_reminders(kept)
            self.logger.info("Fired reminders: {}", fired_ids)

    def _push_reminder(self, r: Dict[str, Any]) -> None:
        msg = r.get("message", "提醒时间到了！")
        rid = r.get("id", "?")
        repeat_label = r.get("repeat", "once")
        self.ctx.push_message(
            source="memo_reminder",
            message_type="proactive_notification",
            description=f"⏰ 备忘提醒 [{rid[:8]}]",
            priority=8,
            content=f"⏰ 提醒: {msg}",
            metadata={
                "task_id": rid,
                "reminder_id": rid,
                "repeat": repeat_label,
                "trigger_at": r.get("trigger_at"),
                "created_at": r.get("created_at"),
            },
            target_lanlan=r.get("lanlan_name"),
        )

    @staticmethod
    def _reschedule(r: Dict[str, Any], now: datetime) -> Optional[Dict[str, Any]]:
        repeat = str(r.get("repeat", "once")).strip().lower()
        if repeat == "once":
            return None

        trigger_dt = datetime.fromisoformat(r["trigger_at"])

        if repeat == "daily":
            next_dt = trigger_dt + timedelta(days=1)
            while next_dt <= now:
                next_dt += timedelta(days=1)
        elif repeat == "weekly":
            next_dt = trigger_dt + timedelta(weeks=1)
            while next_dt <= now:
                next_dt += timedelta(weeks=1)
        elif repeat == "hourly":
            next_dt = trigger_dt + timedelta(hours=1)
            while next_dt <= now:
                next_dt += timedelta(hours=1)
        elif repeat.endswith(("m", "h", "s", "d")):
            unit = repeat[-1]
            try:
                val = float(repeat[:-1])
            except ValueError:
                return None
            delta_map = {"s": "seconds", "m": "minutes", "h": "hours", "d": "days"}
            delta = timedelta(**{delta_map.get(unit, "minutes"): val})
            next_dt = trigger_dt + delta
            while next_dt <= now:
                next_dt += delta
        else:
            return None

        r = dict(r)
        r["trigger_at"] = next_dt.isoformat()
        return r

    @plugin_entry(
        id="add_reminder",
        name="添加提醒",
        description="排期一个备忘提醒（异步，不会立即触发）。调用成功仅表示排期完成，到时间后系统会自动推送提醒消息，请勿在排期完成时就提醒用户。time 可以是绝对时间 (2026-03-07 09:00)、仅时间 (07:00)、或相对时间 (15s / 30m / 2h / 1d)。repeat 可选: once, daily, weekly, hourly, 或自定义间隔如 90m。",
        input_schema={
            "type": "object",
            "properties": {
                "time": {
                    "type": "string",
                    "description": "触发时间：绝对时间 (YYYY-MM-DD HH:MM) 或相对时间 (30m, 2h, 1d)",
                },
                "message": {
                    "type": "string",
                    "description": "提醒内容",
                },
                "repeat": {
                    "type": "string",
                    "description": "重复模式: once(默认), daily, weekly, hourly, 或自定义间隔如 90m",
                    "default": "once",
                },
            },
            "required": ["time", "message"],
        },
    )
    async def add_reminder(self, time: str, message: str, repeat: str = "once", **kwargs):
        tz = getattr(self, "_tz", ZoneInfo(_DEFAULT_TZ))
        parsed = _parse_time(time, tz)
        if parsed is None:
            return fail("INVALID_TIME", f"无法解析时间: {time}")

        trigger_dt, has_date = parsed
        repeat = repeat.strip().lower()
        now = _now(_TZ_UTC)

        if trigger_dt <= now:
            if repeat == "once":
                if has_date:
                    local_str = trigger_dt.astimezone(tz).strftime("%Y-%m-%d %H:%M:%S")
                    return fail("TIME_PAST", f"指定时间 {local_str} 已过去")
                trigger_dt += timedelta(days=1)
            else:
                placeholder = {
                    "trigger_at": trigger_dt.isoformat(),
                    "repeat": repeat,
                }
                advanced = self._reschedule(placeholder, now)
                if advanced is None:
                    return fail("INVALID_REPEAT", f"无法调度重复模式: {repeat}")
                trigger_dt = datetime.fromisoformat(advanced["trigger_at"])

        ctx_obj = kwargs.get("_ctx")
        lanlan_name = None
        if isinstance(ctx_obj, dict):
            lanlan_name = ctx_obj.get("lanlan_name")
        if not lanlan_name:
            lanlan_name = getattr(self.ctx, "_current_lanlan", None)

        rid = uuid.uuid4().hex[:12]
        reminder = {
            "id": rid,
            "message": message,
            "trigger_at": trigger_dt.isoformat(),
            "created_at": now.isoformat(),
            "repeat": repeat,
            "lanlan_name": lanlan_name,
        }

        reminders = self._load_reminders()
        cfg = self.store.get("_memo_cfg", {})
        max_r = int(cfg.get("max_reminders", 200)) if isinstance(cfg, dict) else 200
        if len(reminders) >= max_r:
            return fail("LIMIT_REACHED", f"提醒数量已达上限 ({max_r})")

        reminders.append(reminder)
        self._save_reminders(reminders)
        self._notify_change()

        local_str = trigger_dt.astimezone(tz).strftime("%Y-%m-%d %H:%M:%S")
        self.logger.info("Reminder added: id={} trigger_at={} ({}) repeat={}", rid, local_str, tz, repeat)

        return ok(data={
            "status": "scheduled",
            "reminder_id": rid,
            "trigger_at_local": local_str,
            "repeat": repeat,
            "instruction": (
                f"Reminder scheduled for {local_str}. "
                "IMPORTANT: This is only a scheduling confirmation — the reminder has NOT fired yet. "
                "Do NOT deliver the reminder content now. "
            ),
        })

    @plugin_entry(
        id="list_reminders",
        name="查看提醒列表",
        description="列出所有待触发的提醒",
    )
    async def list_reminders(self, **_):
        reminders = self._load_reminders()
        reminders_sorted = sorted(reminders, key=lambda r: r.get("trigger_at", ""))
        return ok(data={"count": len(reminders_sorted), "reminders": reminders_sorted})

    @plugin_entry(
        id="delete_reminder",
        name="删除提醒",
        description="根据 reminder_id 删除一个提醒",
        input_schema={
            "type": "object",
            "properties": {
                "reminder_id": {"type": "string", "description": "要删除的提醒 ID"},
            },
            "required": ["reminder_id"],
        },
    )
    async def delete_reminder(self, reminder_id: str, **_):
        reminders = self._load_reminders()
        original_len = len(reminders)
        reminders = [r for r in reminders if r.get("id") != reminder_id]
        if len(reminders) == original_len:
            return fail("NOT_FOUND", f"未找到提醒: {reminder_id}")
        self._save_reminders(reminders)
        self._notify_change()
        self.logger.info("Reminder deleted: {}", reminder_id)
        return ok(data={"deleted": reminder_id, "remaining": len(reminders)})

    @plugin_entry(
        id="clear_reminders",
        name="清空所有提醒",
        description="删除所有待触发的提醒",
    )
    async def clear_reminders(self, **_):
        count = len(self._load_reminders())
        self._save_reminders([])
        self._notify_change()
        self.logger.info("All {} reminders cleared", count)
        return ok(data={"cleared": count})
