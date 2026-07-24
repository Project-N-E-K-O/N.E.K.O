from __future__ import annotations

import asyncio
from typing import Any


class QQHandlerRuntimeService:
    def __init__(self, plugin: Any):
        self.plugin = plugin

    def track_handler_task(self, task: asyncio.Task) -> None:
        self.plugin._handler_tasks.add(task)
        task.add_done_callback(self.on_handler_task_done)

    def on_handler_task_done(self, task: asyncio.Task) -> None:
        self.plugin._handler_tasks.discard(task)
        try:
            task.result()
        except asyncio.CancelledError:
            pass
        except Exception as exc:
            self.plugin.logger.exception(f"Message handler task failed: {exc}")

    async def run_message_handler(self, message: dict[str, Any]) -> None:
        session_key = self.plugin._message_session_key(message)
        async with self.plugin._message_concurrency:
            if not session_key:
                await self.plugin._handle_message(message)
                return

            async def _handle_current_message() -> None:
                await self.plugin._handle_message(message)

            await self.plugin._run_with_session_lock(session_key, _handle_current_message)
