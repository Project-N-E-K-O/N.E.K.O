"""Version 1 display-only attachments for a host-issued original reply."""
from __future__ import annotations

import math
from typing import Any

import httpx
from .push_message_schema import _normalize_part


class ReplyTailClient:
    version = 1

    def __init__(self, host_ctx: Any) -> None:
        self._host_ctx = host_ctx

    async def _request(self, operation: str, context: dict, registration_id: str,
                       timeout: float, **kwargs: Any) -> dict:
        plugin_id = str(self._host_ctx.plugin_id)
        if context.get("version") != self.version or context.get("source") != "plugin:" + plugin_id:
            return {"accepted": False, "status": "failed", "reason": "invalid_context"}
        timeout = float(timeout)
        if not math.isfinite(timeout) or timeout <= 0:
            raise ValueError("timeout must be finite and positive")
        from config import MAIN_SERVER_PORT

        payload = {
            "context": dict(context), "plugin_id": plugin_id,
            "registration_id": registration_id, **kwargs,
        }
        # Per-call clients also support plugins whose handlers use distinct loops.
        async with httpx.AsyncClient(trust_env=False, timeout=min(timeout, 30.0)) as client:
            response = await client.post(
                f"http://127.0.0.1:{int(MAIN_SERVER_PORT)}/api/tools/reply-tail/{operation}",
                json=payload,
            )
            response.raise_for_status()
            result = response.json()
        if not isinstance(result, dict) or "status" not in result:
            raise ValueError("invalid reply-tail receipt")
        return result

    async def register(self, context: dict, *, registration_id: str, parts: list,
                       ai_behavior: str = "blind", timeout: float = 5.0) -> dict:
        return await self._request(
            "register", context, registration_id, timeout,
            parts=[_normalize_part(part) for part in parts], ai_behavior=ai_behavior,
        )

    async def status(self, context: dict, *, registration_id: str, timeout: float = 5.0) -> dict:
        return await self._request("status", context, registration_id, timeout)

    async def cancel(self, context: dict, *, registration_id: str, timeout: float = 5.0) -> dict:
        return await self._request("cancel", context, registration_id, timeout)
