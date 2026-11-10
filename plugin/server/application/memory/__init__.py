"""Adapter for the memory server's search and private persona endpoints."""

from __future__ import annotations

import asyncio
import json
import threading
from dataclasses import asdict
from urllib.parse import quote
from typing import Any

from plugin.core.rpc import RpcContext

import httpx

from plugin.sdk.shared.contracts.memory import (
    MAX_WIRE_BYTES,
    MemoryTarget,
    MemorySubject,
    decode_search,
    decode_persona,
)
from plugin.server.domain.errors import ServerDomainError
from utils.http.internal_client import get_internal_http_client

_MAX_MEMORY_HTTP_REQUESTS = 8
_memory_request_lock = threading.Lock()
_active_memory_requests = 0


def _error(
    code: str, message: str, status: int = 502, details: dict[str, object] | None = None
) -> ServerDomainError:
    return ServerDomainError(
        code=code, message=message, status_code=status, details=details or {}
    )


def _target(request: dict[str, Any]) -> MemoryTarget:
    try:
        target = MemoryTarget.from_wire(request.get("target"))
        # Match the native route's own name rules before URL routing: encoded
        # slashes otherwise turn an invalid name into a misleading 404.
        from utils.character_name import validate_character_name
        if validate_character_name(target.character, allow_dots=True, max_length=50).code is not None:
            raise ValueError("Invalid character name")
        return target
    except (ValueError, TypeError, KeyError) as exc:
        raise _error("INVALID_ARGUMENT", str(exc), 400) from exc


def _check_size(value: object, code: str) -> None:
    try:
        size = len(
            json.dumps(value, ensure_ascii=False, allow_nan=False).encode("utf-8")
        )
    except (TypeError, ValueError) as exc:
        raise _error("MEMORY_PROTOCOL_ERROR", "Invalid memory payload") from exc
    if size > MAX_WIRE_BYTES:
        raise _error(code, "Memory payload exceeds size limit", 413)


async def _fetch(
    ctx: RpcContext, method: str, path: str, body: dict | None = None
) -> dict:
    global _active_memory_requests
    from config import MEMORY_SERVER_PORT

    remaining = ctx.remaining()
    if remaining <= 0:
        raise _error("MEMORY_TIMEOUT", "Memory request timed out", 504)
    # Reject rather than queue behind a slow memory server. Only the short
    # counter update is locked; HTTP clients remain owned by their own loops.
    with _memory_request_lock:
        if _active_memory_requests >= _MAX_MEMORY_HTTP_REQUESTS:
            raise _error("BUSY", "Memory request capacity exhausted", 429)
        _active_memory_requests += 1
    try:
        async with asyncio.timeout(remaining):
            client = get_internal_http_client()
            too_large = False
            async with client.stream(
                method,
                f"http://127.0.0.1:{MEMORY_SERVER_PORT}{path}",
                json=body,
                timeout=ctx.remaining(),
            ) as response:
                data = bytearray()
                status = response.status_code
                async for chunk in response.aiter_bytes():
                    if len(chunk) > MAX_WIRE_BYTES - len(data):
                        too_large = True
                        break
                    data.extend(chunk)
            # ServerDomainError is frozen; raise only after leaving httpx's
            # generator context manager, which assigns exception tracebacks.
            if too_large:
                raise _error(
                    "MEMORY_RESPONSE_TOO_LARGE",
                    "Memory response exceeds size limit",
                    413,
                )
            try:
                value = json.loads(data)
            except (ValueError, UnicodeDecodeError) as exc:
                raise _error(
                    "MEMORY_PROTOCOL_ERROR", "Invalid memory response"
                ) from exc
            if status >= 400:
                detail = value.get("detail") if isinstance(value, dict) else None
                if isinstance(detail, dict) and detail.get("code") in (
                    "MEMORY_PROTOCOL_ERROR",
                    "MEMORY_RESPONSE_TOO_LARGE",
                ):
                    raise _error(
                        detail["code"],
                        str(detail.get("message") or detail["code"]),
                        status,
                    )
                if method == "GET" and status == 404 and detail == "Not Found":
                    raise _error(
                        "CAPABILITY_UNAVAILABLE", "Persona endpoint is unavailable", 404
                    )
                codes = {
                    400: "INVALID_ARGUMENT",
                    409: "MEMORY_STORAGE_RESTRICTED",
                    503: "MEMORY_NOT_READY",
                    422: "INVALID_ARGUMENT",
                }
                raise _error(
                    codes.get(status, "MEMORY_UPSTREAM_ERROR"),
                    "Memory server returned an error",
                    status,
                    {"upstream_status": status, "detail": detail},
                )
            if not isinstance(value, dict):
                raise _error("MEMORY_PROTOCOL_ERROR", "Invalid memory response")
            if value.get("error_code") is not None:
                if not isinstance(value["error_code"], str) or not value["error_code"].strip():
                    raise _error("MEMORY_PROTOCOL_ERROR", "Invalid memory error")
                raise _error(
                    "MEMORY_UPSTREAM_ERROR",
                    "Memory server reported a failure",
                    details={"upstream_code": value["error_code"]},
                )
            return value
    except (TimeoutError, httpx.TimeoutException) as exc:
        raise _error("MEMORY_TIMEOUT", "Memory request timed out", 504) from exc
    except httpx.RequestError as exc:
        raise _error(
            "MEMORY_UNAVAILABLE",
            "Memory server is unavailable",
            503,
            {"error_type": type(exc).__name__},
        ) from exc
    finally:
        with _memory_request_lock:
            _active_memory_requests -= 1


async def search(ctx: RpcContext, request: dict[str, Any]) -> dict:
    _check_size(request, "MEMORY_REQUEST_TOO_LARGE")
    target = _target(request)
    body = {}
    for key in ("query", "time", "language"):
        value = request.get(key)
        if value is not None:
            if not isinstance(value, str):
                raise _error("INVALID_ARGUMENT", f"Invalid {key}", 400)
            if value.strip():
                body[key] = value.strip()
    if not body.get("query") and not body.get("time"):
        raise _error("INVALID_ARGUMENT", "query or time is required", 400)
    budget = request.get("budget_ms")
    if budget is not None:
        if isinstance(budget, bool) or not isinstance(budget, int) or budget < 0:
            raise _error("INVALID_ARGUMENT", "Invalid budget_ms", 400)
        body["budget_ms"] = budget
    if target.kind == "scoped":
        body["subjects"] = [s.to_wire() for s in target.subjects]
    raw = await _fetch(
        ctx, "POST", f"/query_memory/{quote(target.character, safe='')}", body
    )
    try:
        if not isinstance(raw.get("results"), list):
            raise ValueError("Invalid results")
        items = []
        for row in raw["results"]:
            if not isinstance(row, dict):
                raise ValueError("Invalid item")
            kind, sid, scope = (
                row.get("subject_kind"),
                row.get("subject_id"),
                row.get("scope"),
            )
            if kind is None and sid is None and scope in (None, "legacy_private"):
                subject = None
            elif kind is not None and sid is not None and scope is not None:
                subject = MemorySubject(kind, sid, scope).to_wire()
            else:
                raise ValueError("Invalid result subject")
            items.append(
                {
                    "id": row.get("id"),
                    "text": row.get("text"),
                    "kind": row.get("tier"),
                    "entity": row.get("entity"),
                    "subject": subject,
                    "retrieval_score": row.get("score"),
                    "created_at": row.get("created_at"),
                    "event_start_at": row.get("event_start_at"),
                    "event_end_at": row.get("event_end_at"),
                }
            )
        value = {
            "target": target.to_wire(),
            "query": raw.get("query"),
            "requested_time": body.get("time"),
            "items": items,
            "candidates_total": raw.get("candidates_total"),
            "elapsed_ms": raw.get("elapsed_ms"),
            "diagnostics": None,
        }
        decode_search(value)
    except (ValueError, TypeError, KeyError) as exc:
        raise _error(
            "MEMORY_PROTOCOL_ERROR", "Malformed memory search response"
        ) from exc
    _check_size(value, "MEMORY_RESPONSE_TOO_LARGE")
    if ctx.remaining() <= 0:
        raise _error("MEMORY_TIMEOUT", "Memory request timed out", 504)
    return value


async def get_persona(ctx: RpcContext, request: dict[str, Any]) -> dict:
    _check_size(request, "MEMORY_REQUEST_TOO_LARGE")
    target = _target(request)
    if target.kind != "private":
        raise _error("CAPABILITY_UNAVAILABLE", "Scoped persona is not supported", 400)
    raw = await _fetch(
        ctx, "GET", f"/internal/memory/{quote(target.character, safe='')}/persona"
    )
    value = {"target": target.to_wire(), "sections": raw.get("sections")}
    try:
        persona = decode_persona(value)
        value["sections"] = [asdict(section) for section in persona.sections]
    except (ValueError, TypeError, KeyError) as exc:
        raise _error("MEMORY_PROTOCOL_ERROR", "Malformed persona response") from exc
    _check_size(value, "MEMORY_RESPONSE_TOO_LARGE")
    if ctx.remaining() <= 0:
        raise _error("MEMORY_TIMEOUT", "Memory request timed out", 504)
    return value
