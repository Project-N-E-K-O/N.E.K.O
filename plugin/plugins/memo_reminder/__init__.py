"""
备忘提醒插件 (Memo Reminder)

在指定时间通过 push_message 提醒主人。
支持一次性提醒、每日重复、自定义间隔重复。
数据通过 PluginStore (SQLite KV) 持久化，重启不丢失。
"""

from __future__ import annotations

import re
import threading
import time
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple
from zoneinfo import ZoneInfo

from plugin.sdk.plugin import (
    NekoPluginBase,
    neko_plugin,
    plugin_entry,
    lifecycle,
    Ok,
    Err,
    SdkError,
)

_STORE_KEY = "reminders"

_TZ_UTC = timezone.utc
_DEFAULT_TZ = "Asia/Shanghai"
DEFERRED_WINDOW_SECONDS = 3600  # 与 agent_server 的 DEFERRED_TASK_TIMEOUT 对齐（1小时）

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

_IMMEDIATE_KEYWORDS = frozenset({
    "now", "immediately", "right now", "asap",
    "立即", "马上", "现在", "即刻", "立刻",
})


def _now(tz: timezone | ZoneInfo) -> datetime:
    return datetime.now(tz)


def _parse_time(raw: str, tz: timezone | ZoneInfo) -> Optional[Tuple[datetime, bool]]:
    """解析时间字符串，返回 (aware datetime, 是否含日期)。

    时间字符串先按用户时区解释，再转为 UTC 存储。
    """
    raw = raw.strip()

    if raw.lower() in _IMMEDIATE_KEYWORDS:
        return _now(_TZ_UTC) + timedelta(seconds=2), True

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


_RE_NORMALIZE_REPEAT = re.compile(
    r"^(?:every|per|each|每)\s*"
    r"(\d+(?:\.\d+)?)\s*"
    r"(seconds?|secs?|s|minutes?|mins?|m|hours?|hrs?|h|days?|d)$",
    re.IGNORECASE,
)
_UNIT_MAP = {
    "s": "s", "sec": "s", "secs": "s", "second": "s", "seconds": "s",
    "m": "m", "min": "m", "mins": "m", "minute": "m", "minutes": "m",
    "h": "h", "hr": "h", "hrs": "h", "hour": "h", "hours": "h",
    "d": "d", "day": "d", "days": "d",
}


def _normalize_repeat(raw: str) -> str:
    """Best-effort normalisation of natural-language repeat values.

    "every 10s"        -> "10s"
    "every 2 minutes"  -> "2m"
    "每 30 seconds"    -> "30s"
    Already-clean values pass through unchanged.
    """
    raw = raw.strip().lower()
    m = _RE_NORMALIZE_REPEAT.match(raw)
    if m:
        return f"{m.group(1)}{_UNIT_MAP[m.group(2).rstrip('s') or m.group(2)]}"
    raw = re.sub(r"^(?:every|per|each|每)\s+", "", raw)
    return raw


@neko_plugin
class MemoReminderPlugin(NekoPluginBase):

    def __init__(self, ctx):
        super().__init__(ctx)
        self.file_logger = self.enable_file_logging(log_level="INFO")
        self.logger = self.file_logger
        self._stop_event = threading.Event()
        self._wake_event = threading.Event()
        self._checker_thread: Optional[threading.Thread] = None
        self._reminders_lock = threading.Lock()

    def _store_get_sync(self, key: str, default: Any = None) -> Any:
        try:
            return self.store._read_value(key, default)
        except Exception as exc:
            self.logger.warning("MemoReminder store get failed for key {!r}: {}", key, exc)
            if key == _STORE_KEY:
                raise SdkError(f"Failed to load reminder store key '{key}': {exc}") from exc
            return default

    def _store_set_sync(self, key: str, value: Any) -> None:
        try:
            self.store._write_value(key, value)
        except Exception as exc:
            self.logger.warning("MemoReminder store set failed for key {!r}: {}", key, exc)
            raise SdkError(f"Failed to persist reminder store key '{key}': {exc}") from exc

    def _load_reminders_unlocked(self) -> List[Dict[str, Any]]:
        data = self._store_get_sync(_STORE_KEY, [])
        return data if isinstance(data, list) else []

    def _save_reminders_unlocked(self, reminders: List[Dict[str, Any]]) -> None:
        self._store_set_sync(_STORE_KEY, reminders)

    def _load_reminders(self) -> List[Dict[str, Any]]:
        with self._reminders_lock:
            return self._load_reminders_unlocked()

    def _save_reminders(self, reminders: List[Dict[str, Any]]) -> None:
        with self._reminders_lock:
            self._save_reminders_unlocked(reminders)

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
        except Exception as e:
            self._tz = ZoneInfo(_DEFAULT_TZ)
            self.logger.warning("Invalid timezone {!r}, falling back to {} ({})", tz_name, _DEFAULT_TZ, e)

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
        return Ok({"status": "running", "pending": count, "mode": "event-driven", "timezone": str(self._tz)})

    @lifecycle(id="shutdown")
    def shutdown(self, **_):
        self._stop_event.set()
        self._wake_event.set()
        if self._checker_thread and self._checker_thread.is_alive():
            self._checker_thread.join(timeout=3.0)
        self.logger.info("MemoReminder shutdown")
        return Ok({"status": "shutdown"})

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

            self._wake_event.clear()
            if self._stop_event.is_set():
                break
            wait_sec = self._next_trigger_seconds()
            if wait_sec is not None:
                wait_sec = max(wait_sec, 0.1)
            else:
                wait_sec = 86400.0

            self._wake_event.wait(timeout=wait_sec)
            if self._stop_event.is_set():
                break

    def _fire_due_reminders(self) -> None:
        with self._reminders_lock:
            reminders = self._load_reminders_unlocked()
            if not reminders:
                return

            now = _now(_TZ_UTC)
            fired_ids: List[str] = []
            kept: List[Dict[str, Any]] = []

            for r in reminders:
                trigger_iso = r.get("trigger_at", "")
                try:
                    trigger_dt = datetime.fromisoformat(trigger_iso)
                except (ValueError, TypeError):
                    kept.append(r)
                    continue

                # 检查是否有待重试的回调（消息已发送但回调失败）
                if r.get("delivered") and r.get("callback_pending"):
                    # 只重试回调，不重复发送消息
                    agent_task_id = r.get("agent_task_id")
                    rid = r.get("id", "?")
                    if agent_task_id:
                        try:
                            import httpx as _httpx
                            from config import TOOL_SERVER_PORT
                            with _httpx.Client(timeout=2.0, proxy=None, trust_env=False) as c:
                                resp = c.post(f"http://127.0.0.1:{TOOL_SERVER_PORT}/api/agent/tasks/{agent_task_id}/complete")
                                if resp.is_success:
                                    # 回调成功，清除待重试标志
                                    r["callback_pending"] = False
                                    r["callback_error"] = None
                                    self.logger.info("Retry callback succeeded for reminder {}", rid)
                                    fired_ids.append(rid)
                                    updated = self._reschedule(r, now)
                                    if updated:
                                        kept.append(updated)
                                    continue
                                elif resp.status_code == 404:
                                    # 任务不存在（可能服务重启后丢失），放弃重试
                                    self.logger.warning("Retry callback abandoned for reminder {}: task not found (404)", rid)
                                    r["callback_pending"] = False
                                    r["callback_error"] = "Task not found (404)"
                                    fired_ids.append(rid)
                                    updated = self._reschedule(r, now)
                                    if updated:
                                        kept.append(updated)
                                    continue
                                else:
                                    self.logger.warning("Retry callback failed for reminder {}: HTTP {}", rid, resp.status_code)
                        except Exception as e:
                            self.logger.warning("Retry callback exception for reminder {}: {}", rid, e)
                        # 回调仍失败（非404），保留提醒以便下次重试（限制重试次数）
                        retry_count = r.get("callback_retry_count", 0) + 1
                        if retry_count >= 5:
                            # 超过重试次数，放弃
                            self.logger.warning("Retry callback abandoned for reminder {}: max retries exceeded", rid)
                            r["callback_pending"] = False
                            r["callback_error"] = "Max retries exceeded"
                            fired_ids.append(rid)
                            updated = self._reschedule(r, now)
                            if updated:
                                kept.append(updated)
                            continue
                        r["callback_retry_count"] = retry_count
                        kept.append(r)
                        continue
                    else:
                        # 没有 agent_task_id，不需要回调
                        fired_ids.append(rid)
                        updated = self._reschedule(r, now)
                        if updated:
                            kept.append(updated)
                        continue

                if trigger_dt <= now:
                    # 如果是 deferred 提醒且刚创建（<5秒），等待 bind_task 完成
                    # 只对有 deferred_bind_pending 标志的提醒应用缓冲（避免影响非 deferred 提醒）
                    if r.get("deferred_bind_pending") and r.get("agent_task_id") is None:
                        created_at_str = r.get("created_at", "")
                        try:
                            created_dt = datetime.fromisoformat(created_at_str.replace("Z", "+00:00"))
                            age = (now - created_dt).total_seconds()
                            if age < 5.0:  # 5 秒缓冲，等待 bind_task
                                kept.append(r)
                                continue
                        except (ValueError, TypeError):
                            pass  # 解析失败，正常处理

                    try:
                        self._push_reminder(r)
                    except Exception:
                        self.logger.exception("Failed to push reminder {}", r.get("id", "?"))
                        kept.append(r)
                        continue

                    # 检查是否需要保留（回调失败或需要重调度）
                    if r.get("callback_pending"):
                        # 回调失败，保留以便重试
                        kept.append(r)
                    else:
                        fired_ids.append(r.get("id", "?"))
                        updated = self._reschedule(r, now)
                        if updated:
                            kept.append(updated)
                else:
                    kept.append(r)

            if fired_ids:
                self._save_reminders_unlocked(kept)
                self.logger.info("Fired reminders: {}", fired_ids)
            elif kept:
                # 即使没有新触发的提醒，也需要保存更新后的状态（如 callback_pending 清除）
                self._save_reminders_unlocked(kept)

    def _push_reminder(self, r: Dict[str, Any]) -> None:
        msg = r.get("message", "提醒时间到了！")
        rid = r.get("id", "?")
        repeat_label = r.get("repeat", "once")

        # 只有未发送过消息的提醒才发送（防止重试时重复发送）
        if not r.get("delivered"):
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
            # 标记消息已发送
            r["delivered"] = True

        # 通知 agent_server deferred 任务已完成
        agent_task_id = r.get("agent_task_id")
        if agent_task_id:
            try:
                import httpx as _httpx
                from config import TOOL_SERVER_PORT
                with _httpx.Client(timeout=2.0, proxy=None, trust_env=False) as c:
                    resp = c.post(f"http://127.0.0.1:{TOOL_SERVER_PORT}/api/agent/tasks/{agent_task_id}/complete")
                    if not resp.is_success:
                        self.logger.warning(
                            "Failed to notify agent task completion for reminder {}: HTTP {} - {}",
                            rid, resp.status_code, resp.text
                        )
                        # 标记回调待重试（但不抛出异常，避免消息重复发送）
                        r["callback_pending"] = True
                        r["callback_error"] = f"HTTP {resp.status_code}: {resp.text}"
            except Exception as e:
                self.logger.warning("Failed to notify agent task completion for reminder {}: {}", rid, e)
                # 标记回调待重试
                r["callback_pending"] = True
                r["callback_error"] = str(e)

    @staticmethod
    def _reschedule(r: Dict[str, Any], now: datetime) -> Optional[Dict[str, Any]]:
        repeat = str(r.get("repeat", "once")).strip().lower()
        if repeat == "once":
            return None

        max_count = r.get("max_count")
        if max_count is not None:
            fire_count = r.get("fire_count", 0) + 1
            if fire_count >= max_count:
                return None
            r = dict(r)
            r["fire_count"] = fire_count

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
            if val <= 0:
                return None
            delta_map = {"s": "seconds", "m": "minutes", "h": "hours", "d": "days"}
            delta = timedelta(**{delta_map.get(unit, "minutes"): val})
            if delta.total_seconds() <= 0:
                return None
            next_dt = trigger_dt + delta
            while next_dt <= now:
                next_dt += delta
        else:
            return None

        r = dict(r)
        r["trigger_at"] = next_dt.isoformat()
        # 清除 transient 状态，以便下次触发时重新发送消息
        r.pop("delivered", None)
        r.pop("callback_pending", None)
        r.pop("callback_error", None)
        r.pop("callback_retry_count", None)
        # 清除旧的任务关联，下次触发时会生成新的 agent_task_id
        r.pop("agent_task_id", None)
        r.pop("deferred_bind_pending", None)
        return r

    @plugin_entry(
        id="add_reminder",
        name="添加提醒",
        description=(
            "排期一个备忘提醒（异步，不会立即触发）。"
            "调用成功仅表示排期完成，到时间后系统会自动推送提醒消息，请勿在排期完成时就提醒用户。\n"
            "time 格式: 'now'(立即) / 绝对时间 '2026-03-07 09:00' / 仅时间 '07:00' / 相对偏移 '15s' '30m' '2h' '1d'（纯数字+单位，不要加 'every' 'in' 等词）。\n"
            "repeat 格式: 'once' | 'daily' | 'weekly' | 'hourly' | 自定义间隔如 '10s' '90m' '2h'（纯数字+单位）。"
            "错误示例: 'every 10s'、'10 seconds'。正确示例: '10s'。\n"
            "max_count: 重复触发的最大次数。例如 repeat='15s' + max_count=5 表示每15秒触发一次，共触发5次后自动删除。"
        ),
        input_schema={
            "type": "object",
            "properties": {
                "time": {
                    "type": "string",
                    "description": (
                        "触发时间。格式: 'now'(立即) | 绝对时间 'YYYY-MM-DD HH:MM' | 仅时间 'HH:MM' | "
                        "相对偏移 '<数字><单位>' 如 '30m' '2h' '1d'。"
                        "单位: s=秒 m=分 h=时 d=天。不要加 'in' 'after' 等前缀。"
                    ),
                },
                "message": {
                    "type": "string",
                    "description": "提醒内容",
                },
                "repeat": {
                    "type": "string",
                    "description": (
                        "重复模式(默认 once)。"
                        "可选值: 'once' | 'daily' | 'weekly' | 'hourly' | '<数字><单位>' 如 '10s' '90m' '2h'。"
                        "只传纯数字+单位，不要加 'every' 等前缀。"
                    ),
                    "default": "once",
                },
                "max_count": {
                    "type": "integer",
                    "description": (
                        "重复触发的最大次数（仅对 repeat 非 'once' 有效）。"
                        "达到次数后自动删除。"
                    ),
                },
            },
            "required": ["time", "message"],
        },
        llm_result_fields=["message", "trigger_at_local"],
    )
    async def add_reminder(self, time: str, message: str, repeat: str = "once", max_count: int = 20, **kwargs):
        try:
            max_count = int(max_count)
        except (TypeError, ValueError):
            return Err(SdkError(f"max_count 必须为正整数: {max_count}"))
        if max_count <= 0:
            return Err(SdkError(f"max_count 必须为正整数: {max_count}"))

        tz = getattr(self, "_tz", ZoneInfo(_DEFAULT_TZ))
        parsed = _parse_time(time, tz)
        if parsed is None:
            return Err(SdkError(f"无法解析时间: {time}"))

        trigger_dt, has_date = parsed
        repeat = _normalize_repeat(repeat)
        if repeat not in ("once", "daily", "weekly", "hourly"):
            if repeat.endswith(("m", "h", "s", "d")):
                try:
                    val = float(repeat[:-1])
                    if val <= 0:
                        return Err(SdkError(f"重复间隔必须为正数: {repeat}"))
                except ValueError:
                    return Err(SdkError(f"无法解析重复模式: {repeat}"))
            else:
                return Err(SdkError(f"不支持的重复模式: {repeat}"))
        now = _now(_TZ_UTC)

        if trigger_dt <= now:
            if repeat == "once":
                if has_date:
                    local_str = trigger_dt.astimezone(tz).strftime("%Y-%m-%d %H:%M:%S")
                    return Err(SdkError(f"指定时间 {local_str} 已过去"))
                trigger_dt += timedelta(days=1)
            else:
                placeholder = {
                    "trigger_at": trigger_dt.isoformat(),
                    "repeat": repeat,
                }
                advanced = self._reschedule(placeholder, now)
                if advanced is None:
                    return Err(SdkError(f"无法调度重复模式: {repeat}"))
                trigger_dt = datetime.fromisoformat(advanced["trigger_at"])

        ctx_obj = kwargs.get("_ctx")
        lanlan_name = None
        if isinstance(ctx_obj, dict):
            lanlan_name = ctx_obj.get("lanlan_name")
        if not lanlan_name:
            lanlan_name = getattr(self.ctx, "_current_lanlan", None)

        # 只有当触发时间在 1 小时内时才需要 deferred 机制（与 agent_server 的 DEFERRED_TASK_TIMEOUT 对齐）
        trigger_delay = (trigger_dt - now).total_seconds()
        needs_deferred = trigger_delay < DEFERRED_WINDOW_SECONDS

        rid = uuid.uuid4().hex[:12]
        reminder = {
            "id": rid,
            "message": message,
            "trigger_at": trigger_dt.isoformat(),
            "created_at": now.isoformat(),
            "repeat": repeat,
            "lanlan_name": lanlan_name,
            "agent_task_id": None,
            "deferred_bind_pending": needs_deferred,
        }
        if max_count is not None and repeat != "once":
            reminder["max_count"] = int(max_count)
            reminder["fire_count"] = 0

        with self._reminders_lock:
            reminders = self._load_reminders_unlocked()
            cfg = self._store_get_sync("_memo_cfg", {})
            max_r = int(cfg.get("max_reminders", 200)) if isinstance(cfg, dict) else 200
            if len(reminders) >= max_r:
                return Err(SdkError(f"提醒数量已达上限 ({max_r})"))
            reminders.append(reminder)
            self._save_reminders_unlocked(reminders)
        self._notify_change()

        local_str = trigger_dt.astimezone(tz).strftime("%Y-%m-%d %H:%M:%S")
        self.logger.info("Reminder added: id={} trigger_at={} ({}) repeat={}", rid, local_str, tz, repeat)

        repeat_desc = {
            "once": "will fire once (no repeat)",
            "daily": "repeats every day",
            "weekly": "repeats every week",
            "hourly": "repeats every hour",
        }.get(repeat, f"repeats every {repeat}")
        if max_count is not None and repeat != "once":
            repeat_desc += f" (max {max_count} times)"

        return Ok({
            "status": "scheduled",
            "deferred": needs_deferred,
            "reminder_id": rid,
            "trigger_at_local": local_str,
            "repeat": repeat,
            "message": message,
            "instruction": (
                f"Reminder scheduled: content=\"{message}\", "
                f"first fire at {local_str}, {repeat_desc}. "
                "This is ONLY a scheduling confirmation — the reminder has NOT fired yet. "
                "Do NOT deliver the reminder content to the user now; "
                "the system will push it automatically when the time comes."
            ),
        })

    @plugin_entry(
        id="bind_task",
        name="绑定Agent任务ID",
        description="内部接口：将 agent_task_id 关联到提醒记录，用于 deferred 完成通知",
    )
    async def bind_task(self, reminder_id: str, agent_task_id: str, **kwargs):
        """将 agent_task_id 写回到对应的提醒记录，供 daemon 触发时回调使用"""
        with self._reminders_lock:
            reminders = self._load_reminders_unlocked()
            for r in reminders:
                if r.get("id") == reminder_id:
                    r["agent_task_id"] = agent_task_id
                    r["deferred_bind_pending"] = False  # 清除标志，表示绑定完成
                    self._save_reminders_unlocked(reminders)
                    self.logger.info("Bound agent_task_id={} to reminder={}", agent_task_id, reminder_id)
                    return Ok({"bound": True})
        return Err(SdkError(f"Reminder {reminder_id} not found"))

    @plugin_entry(
        id="list_reminders",
        name="查看提醒列表",
        description="列出所有待触发的提醒",
        llm_result_fields=["count"],
    )
    async def list_reminders(self, **_):
        reminders = self._load_reminders()
        reminders_sorted = sorted(reminders, key=lambda r: r.get("trigger_at", ""))
        return Ok({"count": len(reminders_sorted), "reminders": reminders_sorted})

    @plugin_entry(
        id="delete_reminder",
        name="删除提醒",
        description="根据 reminder_id 删除一个提醒",
        llm_result_fields=["deleted"],
        input_schema={
            "type": "object",
            "properties": {
                "reminder_id": {"type": "string", "description": "要删除的提醒 ID"},
            },
            "required": ["reminder_id"],
        },
    )
    async def delete_reminder(self, reminder_id: str, **_):
        with self._reminders_lock:
            reminders = self._load_reminders_unlocked()
            original_len = len(reminders)
            reminders = [r for r in reminders if r.get("id") != reminder_id]
            if len(reminders) == original_len:
                return Err(SdkError(f"未找到提醒: {reminder_id}"))
            self._save_reminders_unlocked(reminders)
        self._notify_change()
        self.logger.info("Reminder deleted: {}", reminder_id)
        return Ok({"deleted": reminder_id, "remaining": len(reminders)})

    @plugin_entry(
        id="clear_reminders",
        name="清空所有提醒",
        description="删除所有待触发的提醒",
        llm_result_fields=["cleared"],
    )
    async def clear_reminders(self, **_):
        with self._reminders_lock:
            count = len(self._load_reminders_unlocked())
            self._save_reminders_unlocked([])
        self._notify_change()
        self.logger.info("All {} reminders cleared", count)
        return Ok({"cleared": count})
