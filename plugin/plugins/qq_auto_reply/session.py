from __future__ import annotations

import asyncio
import json
import time
from typing import Any


class QQAutoReplySessionMixin:
    @staticmethod
    def _build_session_key(*, sender_id: str, is_group: bool, group_id: str | None = None) -> str:
        sender = str(sender_id or "").strip()
        if is_group:
            return f"group:{str(group_id or '').strip()}"
        return f"private:{sender}"

    @staticmethod
    def _build_backlog_conversation_key(*, sender_id: str, is_group: bool, group_id: str | None = None) -> str:
        sender = str(sender_id or "").strip()
        if is_group:
            return f"group:{str(group_id or '').strip()}:{sender}"
        return f"private:{sender}"

    def _message_session_key(self, message: dict[str, Any]) -> str | None:
        message_type = str(message.get("message_type") or "").strip()
        sender_id = str(message.get("user_id") or "").strip()
        if not sender_id:
            return None
        if message_type == "private":
            return self._build_session_key(sender_id=sender_id, is_group=False)
        if message_type == "group":
            group_id = str(message.get("group_id") or "").strip()
            if not group_id:
                return None
            return self._build_session_key(sender_id=sender_id, is_group=True, group_id=group_id)
        return None

    async def _get_session_lock(self, session_key: str) -> asyncio.Lock:
        async with self._session_locks_guard:
            lock = self._session_locks.get(session_key)
            if lock is None:
                lock = asyncio.Lock()
                self._session_locks[session_key] = lock
            return lock

    def _track_handler_task(self, task: asyncio.Task) -> None:
        self._handler_tasks.add(task)
        task.add_done_callback(self._on_handler_task_done)

    def _on_handler_task_done(self, task: asyncio.Task) -> None:
        self._handler_tasks.discard(task)
        try:
            task.result()
        except asyncio.CancelledError:
            pass
        except Exception as exc:
            self.logger.error(f"Message handler task failed: {exc}")

    async def _run_with_session_lock(self, session_key: str, coro_factory) -> Any:
        session_lock = await self._get_session_lock(session_key)
        async with session_lock:
            return await coro_factory()

    async def _wait_session_response_complete(self, session: Any, timeout: float = 30.0) -> bool:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            await asyncio.sleep(0.5)
            if not getattr(session, "_is_responding", False):
                return True
        return False

    async def _session_housekeeping_loop(self):
        try:
            while True:
                await asyncio.sleep(self.SESSION_SWEEP_INTERVAL_SECONDS)
                await self._flush_idle_memory_sessions()
        except asyncio.CancelledError:
            raise

    async def _flush_idle_memory_sessions(self):
        now = time.time()
        idle_sessions = []
        for session_key, user_data in list(self._user_sessions.items()):
            if not user_data.get("memory_enabled"):
                continue
            last_activity_at = user_data.get("last_activity_at") or now
            if now - last_activity_at >= self.SESSION_IDLE_TIMEOUT_SECONDS:
                idle_sessions.append(session_key)

        for session_key in idle_sessions:
            async def _finalize_if_still_idle() -> bool:
                current = self._user_sessions.get(session_key)
                if not current or not current.get("memory_enabled"):
                    return False
                current_last_activity = current.get("last_activity_at") or now
                if time.time() - current_last_activity < self.SESSION_IDLE_TIMEOUT_SECONDS:
                    return False
                return await self._finalize_user_memory_session(session_key, reason="idle_timeout")

            await self._run_with_session_lock(session_key, _finalize_if_still_idle)

    async def _flush_all_memory_sessions(self, reason: str):
        for session_key, user_data in list(self._user_sessions.items()):
            if not user_data.get("memory_enabled"):
                continue

            async def _finalize_existing() -> bool:
                current = self._user_sessions.get(session_key)
                if not current or not current.get("memory_enabled"):
                    return False
                return await self._finalize_user_memory_session(session_key, reason=reason)

            await self._run_with_session_lock(session_key, _finalize_existing)

    def _conversation_slice_to_memory_messages(self, conversation_history: list, start_index: int = 0) -> list[dict[str, Any]]:
        memory_messages = []
        for msg in conversation_history[start_index:]:
            msg_type = getattr(msg, "type", "")
            if msg_type not in ("human", "ai"):
                continue
            role = "user" if msg_type == "human" else "assistant"
            content = getattr(msg, "content", "")
            if isinstance(content, str):
                text = content
            elif isinstance(content, list):
                parts = []
                for item in content:
                    if isinstance(item, dict) and item.get("type") == "text":
                        parts.append(item.get("text", ""))
                    elif isinstance(item, str):
                        parts.append(item)
                text = "".join(parts)
            else:
                text = str(content)
            if not text:
                continue
            memory_messages.append({
                "role": role,
                "content": [{"type": "text", "text": text}],
            })
        return memory_messages

    async def _post_memory_history(self, endpoint: str, her_name: str, messages: list[dict[str, Any]], timeout: float = 5.0) -> dict[str, Any]:
        import httpx
        from config import MEMORY_SERVER_PORT

        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"http://localhost:{MEMORY_SERVER_PORT}/{endpoint}/{her_name}",
                json={"input_history": json.dumps(messages, ensure_ascii=False)},
                timeout=timeout,
            )
            response.raise_for_status()
            return response.json()

    async def _cache_session_delta(self, session_key: str, user_data: dict[str, Any]) -> int:
        session = user_data.get("session")
        her_name = user_data.get("her_name")
        if not session or not her_name:
            return 0
        conversation_history = getattr(session, "_conversation_history", []) or []
        start_index = int(user_data.get("last_synced_index", 0))
        delta_messages = self._conversation_slice_to_memory_messages(conversation_history, start_index)
        if not delta_messages:
            return 0
        result = await self._post_memory_history("cache", her_name, delta_messages, timeout=5.0)
        if result.get("status") == "error":
            raise RuntimeError(result.get("message", "cache failed"))
        user_data["last_synced_index"] = len(conversation_history)
        user_data["has_cached_memory"] = True
        return len(delta_messages)

    async def _finalize_user_memory_session(self, session_key: str, reason: str) -> bool:
        user_data = self._user_sessions.get(session_key)
        if not user_data or not user_data.get("memory_enabled"):
            return False

        session = user_data.get("session")
        her_name = user_data.get("her_name")
        if not session or not her_name:
            self._user_sessions.pop(session_key, None)
            return False

        try:
            conversation_history = getattr(session, "_conversation_history", []) or []
            last_synced_index = int(user_data.get("last_synced_index", 0))
            remaining_messages = self._conversation_slice_to_memory_messages(conversation_history, last_synced_index)

            if remaining_messages:
                result = await self._post_memory_history("process", her_name, remaining_messages, timeout=30.0)
                if result.get("status") == "error":
                    raise RuntimeError(result.get("message", "process failed"))
                self.logger.info(f"[{reason}] 已为用户 {session_key} 完成正式记忆结算，消息数: {len(remaining_messages)}")
            elif user_data.get("has_cached_memory"):
                settled_messages = self._conversation_slice_to_memory_messages(conversation_history, 0)
                result = await self._post_memory_history("settle", her_name, settled_messages, timeout=30.0)
                if result.get("status") == "error":
                    raise RuntimeError(result.get("message", "settle failed"))
                self.logger.info(f"[{reason}] 已为用户 {session_key} 完成缓存记忆结算")
        except Exception as e:
            self.logger.error(f"[{reason}] 用户 {session_key} 的记忆结算失败: {e}")
            return False

        self._user_sessions.pop(session_key, None)
        try:
            await session.close()
        except Exception as e:
            self.logger.warning(f"[{reason}] 用户 {session_key} 的本地会话关闭失败: {e}")
        return True

    async def _invalidate_private_session(self, qq_number: str) -> None:
        session_key = self._build_session_key(sender_id=qq_number, is_group=False)

        async def _invalidate() -> None:
            user_data = self._user_sessions.get(session_key)
            if user_data and user_data.get("memory_enabled"):
                finalized = await self._finalize_user_memory_session(session_key, reason="permission_change")
                if finalized:
                    return

            user_data = self._user_sessions.pop(session_key, None)
            session = user_data.get("session") if user_data else None
            if session:
                await session.close()

        await self._run_with_session_lock(session_key, _invalidate)
