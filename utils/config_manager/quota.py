# -*- coding: utf-8 -*-
# Copyright 2025-2026 Project N.E.K.O. Team
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Free agent daily quota mixin.

Local counting of the free agent model daily quota plus the throttled
quota-exceeded frontend notifier.

The quota lock, notifier slot and limits are class attributes on the
assembled ``ConfigManager`` (single owner); methods below resolve them
late through the package facade.
"""
import asyncio
import json
import time
from datetime import date
from pathlib import Path

from utils.file_utils import atomic_write_json

from ._shared import logger


class QuotaMixin:
    """Free agent daily quota counting and notification."""

    def _get_agent_quota_path(self) -> Path:
        """Local Agent trial quota counter file path."""
        return self.config_dir / "agent_quota.json"

    @classmethod
    def register_quota_exceeded_notifier(cls, notifier) -> None:
        """Register the "free Agent quota exhausted" notification callback (process-level, registered by agent_server at startup).

        notifier(used:int, limit:int) is invoked when the quota is exhausted, **at most once
        every 10 seconds** (see the throttling in ``_maybe_notify_quota_exceeded``). The
        callback itself must be non-blocking — it is invoked inside the critical section
        holding ``_agent_quota_lock`` and should normally just do one cross-thread schedule.
        """
        cls._quota_exceeded_notifier = notifier

    def _maybe_notify_quota_exceeded(self, used: int, limit: int) -> None:
        """On quota exhaustion, fire the registered frontend notification callback with throttling (at most once per _quota_notify_interval_s seconds)."""
        # Late-bound: class-level shared state (single owner) lives on the
        # assembled ConfigManager; resolve it through the package facade.
        from utils.config_manager import ConfigManager

        notifier = ConfigManager._quota_exceeded_notifier
        if notifier is None:
            return
        now = time.monotonic()
        with ConfigManager._quota_notify_lock:
            last = ConfigManager._quota_notify_last_monotonic
            if last and (now - last) < ConfigManager._quota_notify_interval_s:
                return
            ConfigManager._quota_notify_last_monotonic = now
        try:
            notifier(used, limit)
        except Exception as e:
            logger.debug("配额耗尽通知回调失败: %s", e)

    def consume_agent_daily_quota(self, source: str = "", units: int = 1) -> tuple[bool, dict]:
        """Consume the Agent model daily quota (only effective when the actual Agent model is free-agent-model). The quota is not enforced locally alone; local counting just reduces useless requests and saves network bandwidth.

        Returns:
            (ok, info)
            info:
              - limited: bool
              - date: YYYY-MM-DD
              - used: int
              - limit: int | None
              - remaining: int | None
              - source: str
        """
        # Late-bound: class-level shared state (single owner) lives on the
        # assembled ConfigManager; resolve it through the package facade.
        from utils.config_manager import ConfigManager

        if units <= 0:
            units = 1

        # 只对真正的免费 Agent 模型(free-agent-model)本地计数：用户换用自费/自定义 agent
        # model 后不该再被这条免费试用配额挡。判定收口在 is_agent_free()。analyzer/deduper
        # 这类判定器走的是 summary/emotion 模型而非 agent model，已不再调用本函数。
        is_metered = self.is_agent_free()
        today = date.today().isoformat()
        limit = int(self._free_agent_daily_limit)

        if not is_metered:
            return True, {
                "limited": False,
                "date": today,
                "used": 0,
                "limit": None,
                "remaining": None,
                "source": source or "",
            }

        self.ensure_config_directory()
        quota_path = self._get_agent_quota_path()

        with ConfigManager._agent_quota_lock:
            data = {"date": today, "used": 0}
            try:
                if quota_path.exists():
                    with open(quota_path, "r", encoding="utf-8") as f:
                        loaded = json.load(f)
                    if isinstance(loaded, dict):
                        loaded_date = str(loaded.get("date") or today)
                        loaded_used = int(loaded.get("used", 0) or 0)
                        if loaded_date == today:
                            data = {"date": today, "used": max(0, loaded_used)}
            except Exception:
                data = {"date": today, "used": 0}

            used = int(data.get("used", 0))
            if used + units > limit:
                # 配额耗尽：节流通知前端弹提示（最多每 10 秒一次）。回调非阻塞，
                # 在临界区里只做一次跨线程 schedule，不展开网络 IO。
                self._maybe_notify_quota_exceeded(used, limit)
                return False, {
                    "limited": True,
                    "date": today,
                    "used": used,
                    "limit": limit,
                    "remaining": max(0, limit - used),
                    "source": source or "",
                }

            used += units
            data = {"date": today, "used": used}
            try:
                atomic_write_json(quota_path, data, ensure_ascii=False, indent=2)
            except Exception as e:
                logger.warning("保存 Agent 配额计数失败: %s", e)

            return True, {
                "limited": True,
                "date": today,
                "used": used,
                "limit": limit,
                "remaining": max(0, limit - used),
                "source": source or "",
            }

    async def aconsume_agent_daily_quota(self, source: str = "", units: int = 1) -> tuple[bool, dict]:
        """Async wrapper of ``consume_agent_daily_quota``.

        The sync version must not run directly on the event loop (open+fsync blocks).
        """
        return await asyncio.to_thread(self.consume_agent_daily_quota, source, units)
