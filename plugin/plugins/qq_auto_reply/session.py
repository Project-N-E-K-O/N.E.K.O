from __future__ import annotations

import asyncio
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
        return self.session_runtime_service.message_session_key(message)

    async def _get_session_lock(self, session_key: str) -> asyncio.Lock:
        return await self.session_runtime_service.get_session_lock(session_key)

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
        return await self.session_runtime_service.run_with_session_lock(session_key, coro_factory)

    async def _wait_session_response_complete(self, session: Any, timeout: float = 30.0) -> bool:
        return await self.session_memory_service.wait_session_response_complete(session, timeout=timeout)

    async def _session_housekeeping_loop(self):
        await self.session_runtime_service.session_housekeeping_loop()

    async def _flush_idle_memory_sessions(self):
        await self.session_memory_service.flush_idle_memory_sessions()

    async def _flush_all_memory_sessions(self, reason: str):
        await self.session_memory_service.flush_all_memory_sessions(reason)

    def _conversation_slice_to_memory_messages(self, conversation_history: list, start_index: int = 0) -> list[dict[str, Any]]:
        return self.session_memory_service.conversation_slice_to_memory_messages(conversation_history, start_index)

    async def _post_memory_history(self, endpoint: str, her_name: str, messages: list[dict[str, Any]], timeout: float = 5.0) -> dict[str, Any]:
        return await self.session_memory_service.post_memory_history(endpoint, her_name, messages, timeout=timeout)

    async def _cache_session_delta(self, session_key: str, user_data: dict[str, Any]) -> int:
        return await self.session_memory_service.cache_session_delta(session_key, user_data)

    async def _finalize_user_memory_session(self, session_key: str, reason: str) -> bool:
        return await self.session_memory_service.finalize_user_memory_session(session_key, reason)

    async def _invalidate_private_session(self, qq_number: str) -> None:
        await self.session_memory_service.invalidate_private_session(qq_number)
