from __future__ import annotations

import asyncio
import time
from typing import Any


class QQDisplayNameService:
    """The backend `group_id -> group_name` map for scoped memory writes.

    此前群名只到过前端一次性刷新（dashboard 的 refresh_actual_contacts，
    结果不落任何后端状态），scoped 写入只能拿纯数字 id。这里维护一份进程
    内映射：写入路径查它给 display_name，拿不到就退化成不带名字（persona
    section 会保留上一次成功盖上的名字，属自愈路径，所以不需要落盘）。

    刷新时机挂在**已有的** session_housekeeping_loop 周期上（30s 一轮，
    TTL 门到 10 分钟才真正发一次 get_group_list），不为它单开定时器。
    开放平台侧 get_group_list 恒返回空表——映射保持为空，写入自然退化，
    这正是该通道的预期形态（group_id 是 openid，拿不到人类可读群名）。
    """

    GROUP_NAME_REFRESH_INTERVAL_SECONDS = 600.0
    # display_name 的长度契约与 speaker_label / 路由校验同口径。
    DISPLAY_NAME_MAX_CHARS = 64

    def __init__(self, plugin: Any):
        self.plugin = plugin
        self._group_names: dict[str, str] = {}
        self._refreshed_at = 0.0
        self._refresh_task: asyncio.Task | None = None

    def group_display_name(self, group_id: object) -> str | None:
        gid = str(group_id or "").strip()
        if not gid:
            return None
        name = self._group_names.get(gid)
        return name or None

    def maybe_schedule_refresh(self) -> None:
        """TTL 门 + 单飞：到期才起一个后台刷新任务，绝不并发第二个。"""
        if self._refresh_task is not None and not self._refresh_task.done():
            return
        if time.monotonic() - self._refreshed_at < (
            self.GROUP_NAME_REFRESH_INTERVAL_SECONDS
        ):
            return
        client = getattr(self.plugin, "qq_client", None)
        if client is None:
            return
        self._refresh_task = asyncio.create_task(self._refresh_once())

    async def _refresh_once(self) -> None:
        try:
            await self.refresh_group_names()
        except Exception as exc:
            # 失败保留旧映射：名字是装饰性元数据，绝不让它的刷新失败
            # 打扰任何主流程；下一个 TTL 周期自然重试。
            self.plugin.logger.warning(f"[DisplayName] 群名刷新失败（保留旧映射）: {exc}")
        finally:
            # TTL applies to attempts, not only successes. Otherwise a
            # persistent NapCat failure is retried on every 30-second sweep.
            self._refreshed_at = time.monotonic()

    async def refresh_group_names(self) -> int:
        """Fetch the group list and rebuild the name map.

        成功即重建（含合法的空表）；异常向上抛，由调用方决定日志级别。
        群名超长按 64 字截断——与写入侧 display_name 的长度契约同口径，
        截断发生在源头，路由侧的 422 留给真正的契约 bug。
        """
        client = getattr(self.plugin, "qq_client", None)
        if client is None:
            return 0
        groups = await client.get_group_list()
        names: dict[str, str] = {}
        for item in groups or []:
            if not isinstance(item, dict):
                continue
            gid = str(item.get("group_id") or "").strip()
            name = str(item.get("group_name") or "").strip()
            if gid and name:
                names[gid] = name[: self.DISPLAY_NAME_MAX_CHARS]
        self._group_names = names
        self._refreshed_at = time.monotonic()
        return len(names)

    @staticmethod
    def display_name_from_label(label: object, sender_id: object) -> str | None:
        """Derive the bare nickname from a `nickname(sender_id)` speaker label.

        成员/好友的 speaker_label 恒带 "(sender_id)" 可追溯后缀（见
        record_group_member_turn 的截断规则），display_name 只要昵称本体
        ——subject_id 已在渲染标题里，重复一遍 id 是纯噪音。label 退化成
        纯 id（无昵称）时返回 None，让标题回退到裸 id 形态。
        """
        text = str(label or "").strip()
        suffix = f"({str(sender_id or '').strip()})"
        if len(suffix) > 2 and text.endswith(suffix):
            bare = text[: -len(suffix)].strip()
            return bare or None
        return None
