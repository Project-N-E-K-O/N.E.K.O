from __future__ import annotations

import asyncio
import json
import math
import time as clock

from ..contracts.memory import (
    MAX_WIRE_BYTES,
    MemoryError,
    MemoryPersona,
    MemorySearchResult,
    MemoryTarget,
    decode_persona,
    decode_search,
)
from ..models.exceptions import SdkError
from ..models.result import Ok, Err, Result


class MemoryClient:
    def __init__(self, ctx: object) -> None:
        self._ctx = ctx

    async def search(
        self,
        *,
        target: MemoryTarget,
        query: str | None = None,
        time: str | None = None,
        language: str | None = None,
        budget_ms: int | None = None,
        timeout: float = 5.0,
    ) -> Result[MemorySearchResult, MemoryError]:
        data = {"target": target}
        for name, value in (("query", query), ("time", time), ("language", language)):
            if value is not None:
                if not isinstance(value, str):
                    return Err(SdkError(f"Invalid {name}", code="INVALID_ARGUMENT"))
                if value.strip():
                    data[name] = value.strip()
        if "query" not in data and "time" not in data:
            return Err(SdkError("query or time is required", code="INVALID_ARGUMENT"))
        if budget_ms is not None:
            if (
                isinstance(budget_ms, bool)
                or not isinstance(budget_ms, int)
                or budget_ms < 0
            ):
                return Err(SdkError("Invalid budget_ms", code="INVALID_ARGUMENT"))
            data["budget_ms"] = budget_ms
        return await self._call("MEMORY_SEARCH", data, timeout, decode_search)

    async def get_persona(
        self, *, target: MemoryTarget, timeout: float = 5.0
    ) -> Result[MemoryPersona, MemoryError]:
        return await self._call(
            "MEMORY_PERSONA_GET", {"target": target}, timeout, decode_persona
        )

    async def _call(self, operation, data, timeout, decode):
        if (
            isinstance(timeout, bool)
            or not isinstance(timeout, (int, float))
            or not math.isfinite(timeout)
            or timeout <= 0
        ):
            return Err(SdkError("Invalid timeout", code="INVALID_ARGUMENT"))
        deadline = clock.monotonic() + timeout
        try:
            target = data["target"]
            if not isinstance(target, MemoryTarget):
                raise ValueError("Invalid target")
            data["target"] = target.to_wire()
            if operation == "MEMORY_PERSONA_GET" and target.kind != "private":
                return Err(
                    SdkError(
                        "Scoped persona is not supported", code="CAPABILITY_UNAVAILABLE"
                    )
                )
            if (
                len(json.dumps(data, ensure_ascii=False).encode("utf-8"))
                > MAX_WIRE_BYTES
            ):
                return Err(
                    SdkError(
                        "Memory request is too large", code="MEMORY_REQUEST_TOO_LARGE"
                    )
                )
        except (ValueError, TypeError) as exc:
            return Err(SdkError(str(exc), code="INVALID_ARGUMENT"))
        sender = getattr(self._ctx, "_send_request_and_wait_async", None)
        if not callable(sender):
            return Err(
                SdkError(
                    "Memory transport is unavailable", code="CAPABILITY_UNAVAILABLE"
                )
            )
        try:
            remaining = deadline - clock.monotonic()
            if remaining <= 0:
                raise TimeoutError()
            async with asyncio.timeout(remaining):
                value = await sender(
                    method_name=operation.lower(),
                    request_type=operation,
                    request_data=data,
                    timeout=remaining,
                    wrap_result=False,
                )
                if (
                    len(json.dumps(value, ensure_ascii=False).encode("utf-8"))
                    > MAX_WIRE_BYTES
                ):
                    return Err(
                        SdkError(
                            "Memory response is too large",
                            code="MEMORY_RESPONSE_TOO_LARGE",
                        )
                    )
                decoded = decode(value)
                if decoded.target != target:
                    raise ValueError("Response target does not match request")
                if clock.monotonic() >= deadline:
                    raise TimeoutError()
                return Ok(decoded)
        except TimeoutError:
            return Err(SdkError("Memory request timed out", code="MEMORY_TIMEOUT"))
        except (ValueError, TypeError, KeyError) as exc:
            return Err(SdkError(str(exc), code="MEMORY_PROTOCOL_ERROR"))
        except Exception as exc:
            code = getattr(exc, "code", None) or "MEMORY_TRANSPORT_ERROR"
            if code == "UNKNOWN_OPERATION":
                code = "CAPABILITY_UNAVAILABLE"
            return Err(
                SdkError(str(exc), code=code, details=getattr(exc, "details", None))
            )
