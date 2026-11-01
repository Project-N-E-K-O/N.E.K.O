"""Version 1 display-only attachments for a host-issued original reply."""
from __future__ import annotations

import math
import os
from typing import Any
from urllib.parse import quote

import httpx
from .push_message_schema import _normalize_part


class ReplyTailClient:
    version = 1

    def __init__(self, host_ctx: Any) -> None:
        self._host_ctx = host_ctx

    async def _post(self, operation: str, payload: dict, timeout: float) -> dict:
        timeout = float(timeout)
        if not math.isfinite(timeout) or timeout <= 0:
            raise ValueError("timeout must be finite and positive")
        from config import MAIN_SERVER_PORT

        async with httpx.AsyncClient(trust_env=False, timeout=min(timeout, 30.0)) as client:
            response = await client.post(
                f"http://127.0.0.1:{int(MAIN_SERVER_PORT)}/api/tools/reply-tail/{operation}",
                json={"plugin_id": str(self._host_ctx.plugin_id), **payload},
            )
            response.raise_for_status()
            result = response.json()
        if not isinstance(result, dict):
            raise ValueError("invalid reply-tail response")
        return result

    async def subscribe(self, entry_id: str, *, role: str | None = None,
                        handler_timeout: float = 15.0, timeout: float = 5.0) -> dict:
        """Invoke a plugin entry after each successful ordinary text reply.

        The entry receives text, user_text, has_attachments and _ctx.host_reply.
        Register images before returning; the host submits them independently
        of the model's tool choices. Subscribe during startup and reload.
        """
        from config import USER_PLUGIN_SERVER_PORT

        port = int(os.getenv("NEKO_USER_PLUGIN_SERVER_PORT") or USER_PLUGIN_SERVER_PORT)
        plugin_id = quote(str(self._host_ctx.plugin_id), safe="")
        entry = quote(entry_id, safe="")
        return await self._post("subscribe", {
            "entry_id": entry_id, "role": role, "timeout_seconds": handler_timeout,
            "callback_url": f"http://127.0.0.1:{port}/api/llm-tools/reply-completed/{plugin_id}/{entry}",
        }, timeout)

    async def unsubscribe(self, entry_id: str, *, timeout: float = 5.0) -> dict:
        return await self._post("unsubscribe", {"entry_id": entry_id}, timeout)

    async def _request(self, operation: str, context: dict, registration_id: str,
                       timeout: float, **kwargs: Any) -> dict:
        plugin_id = str(self._host_ctx.plugin_id)
        if (
            not isinstance(context, dict) or context.get("version") != self.version
            or context.get("source") != "plugin:" + plugin_id
        ):
            return {"accepted": False, "status": "failed", "reason": "invalid_context"}
        payload = {
            "context": dict(context), "plugin_id": plugin_id,
            "registration_id": registration_id, **kwargs,
        }
        # Per-call clients also support plugins whose handlers use distinct loops.
        result = await self._post(operation, payload, timeout)
        if "status" not in result:
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
