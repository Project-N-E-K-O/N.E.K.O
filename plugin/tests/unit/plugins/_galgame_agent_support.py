from __future__ import annotations

import asyncio

from _galgame_bridge_support import (
    _BlockingSummaryGateway,
    _drain_agent_summary_tasks,
    _FakeHostAdapter,
    _FakeLLMGateway,
    _run_in_new_loop,
    _summary_test_line,
    _summary_test_line_event,
)


class _SerialProbeLLMGateway(_FakeLLMGateway):
    def __init__(self) -> None:
        super().__init__(reply_payload={"degraded": False, "reply": "ok", "diagnostic": ""})
        self.active_replies = 0
        self.max_active_replies = 0

    async def agent_reply(self, context: dict[str, object]):
        self.reply_calls.append(dict(context))
        self.active_replies += 1
        self.max_active_replies = max(self.max_active_replies, self.active_replies)
        try:
            await asyncio.sleep(0.02)
            return {"degraded": False, "reply": str(context.get("prompt") or "ok"), "diagnostic": ""}
        finally:
            self.active_replies -= 1


__all__ = [name for name in globals() if not name.startswith("__")]
