from __future__ import annotations

import asyncio
import time
from typing import Any

from .pipeline_models import QQReplyContext


class QQSessionRuntimeService:
    def __init__(self, plugin: Any):
        self.plugin = plugin

    def message_session_key(self, message: dict[str, Any]) -> str | None:
        message_type = str(message.get("message_type") or "").strip()
        sender_id = str(message.get("user_id") or "").strip()
        if not sender_id:
            return None
        if message_type == "private":
            return self.plugin._build_session_key(sender_id=sender_id, is_group=False)
        if message_type == "group":
            group_id = str(message.get("group_id") or "").strip()
            if not group_id:
                return None
            return self.plugin._build_session_key(sender_id=sender_id, is_group=True, group_id=group_id)
        return None

    def build_generation_session_key(self, context: QQReplyContext) -> str:
        session_key = self.plugin._build_session_key(
            sender_id=context.sender_id,
            is_group=context.is_group,
            group_id=context.group_id,
        )
        if context.ephemeral_session:
            return f"{session_key}:ephemeral:{time.time_ns()}"
        return session_key

    def prime_generation_session_state(
        self,
        user_data: dict[str, Any],
        *,
        session_key: str,
        context: QQReplyContext,
    ) -> tuple[Any, list[str]]:
        user_session = user_data["session"]
        reply_chunks = user_data["reply_chunks"]
        user_data["last_activity_at"] = time.time()
        user_data.setdefault("lock", asyncio.Lock())
        user_data["session_key"] = session_key
        user_data["sender_id"] = context.sender_id
        user_data["permission_level"] = context.permission_level
        user_data["is_group"] = context.is_group
        user_data["group_id"] = context.group_id
        user_data["user_title"] = context.user_title
        user_data["user_nickname"] = context.user_nickname
        user_data["memory_enabled"] = context.persist_memory
        user_data["memory_context_used"] = context.memory_context_used
        user_data["ephemeral_session"] = context.ephemeral_session
        user_data["login_status"] = context.login_status
        user_data["login_self_id"] = context.login_self_id
        user_data["login_nickname"] = context.login_nickname
        return user_session, reply_chunks

    async def get_session_lock(self, session_key: str) -> asyncio.Lock:
        async with self.plugin._session_locks_guard:
            lock = self.plugin._session_locks.get(session_key)
            if lock is None:
                lock = asyncio.Lock()
                self.plugin._session_locks[session_key] = lock
            return lock

    async def run_with_session_lock(self, session_key: str, coro_factory) -> Any:
        session_lock = await self.get_session_lock(session_key)
        async with session_lock:
            return await coro_factory()

    async def discard_session(self, session_key: str, *, reason: str) -> None:
        user_data = self.plugin._user_sessions.pop(session_key, None)
        session = user_data.get("session") if user_data else None
        if session:
            try:
                await session.close()
            except Exception as close_error:
                self.plugin.logger.warning(f"[{reason}] 关闭会话失败: {close_error}")

    async def session_housekeeping_loop(self) -> None:
        try:
            while True:
                await asyncio.sleep(self.plugin.SESSION_SWEEP_INTERVAL_SECONDS)
                await self.plugin._flush_idle_memory_sessions()
                # 注意力衰减由 attention_service._decay_loop(5s) 独立驱动，此处不重复
        except asyncio.CancelledError:
            raise
