"""Opt-in, request-local traces for theater prose; never part of story state."""

from __future__ import annotations

import asyncio
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import asdict, is_dataclass
from datetime import datetime, timezone
import json
import logging
import os
from pathlib import Path
import time
from typing import Any
from uuid import uuid4


logger = logging.getLogger(__name__)
_current_trace: ContextVar[_TextTrace | None] = ContextVar("numeric_v2_text_trace", default=None)


def _json_default(value: Any):
    if is_dataclass(value) and not isinstance(value, type):
        return asdict(value)
    raise TypeError("unsupported_trace_value")


class _TextTrace:
    def __init__(self, directory: str):
        self.trace_id = uuid4().hex
        self.started_at = time.monotonic()
        self.sequence = 0
        self.calls = 0
        self.file = None
        try:
            root = Path(directory).expanduser()
            root.mkdir(parents=True, exist_ok=True)
            stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
            fd = os.open(root / f"{stamp}-{self.trace_id}.jsonl", os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            self.file = os.fdopen(fd, "w", encoding="utf-8", buffering=1)
        except Exception as exc:
            self.disable(exc)

    def disable(self, error: Exception):
        self.close()
        # Provider exceptions and narrative payloads must not spill into ordinary logs.
        logger.warning("Numeric v2 text trace disabled: error_type=%s", type(error).__name__)

    def close(self):
        stream, self.file = self.file, None
        if stream is not None:
            try:
                stream.close()
            except Exception:
                logger.warning("Numeric v2 text trace close failed")

    def emit(self, event: str, data: dict[str, Any]):
        if self.file is None:
            return
        try:
            self.sequence += 1
            row = {"schema": "neko.theater.text_trace.v1", "trace_id": self.trace_id,
                   "sequence": self.sequence, "time": datetime.now(timezone.utc).isoformat(),
                   "elapsed_ms": round((time.monotonic() - self.started_at) * 1000, 3),
                   "event": event, "data": data}
            self.file.write(json.dumps(row, ensure_ascii=False, default=_json_default, separators=(",", ":")) + "\n")
        except Exception as exc:
            self.disable(exc)


@contextmanager
def text_trace_scope(operation: str, **context: Any):
    """Unset/empty NEKO_THEATER_TRACE_DIR means no serialization or files."""
    directory = os.environ.get("NEKO_THEATER_TRACE_DIR", "").strip()
    trace = _TextTrace(directory) if directory else None
    token = _current_trace.set(trace)
    status, error_type = "returned", None
    try:
        trace_event("trace.started", operation=operation, **context)
        yield
    except BaseException as exc:
        status = "cancelled" if isinstance(exc, asyncio.CancelledError) else "error"
        error_type = type(exc).__name__
        raise
    finally:
        trace_event("trace.closed", status=status, error_type=error_type)
        if trace is not None:
            trace.close()
        _current_trace.reset(token)


def trace_event(event: str, **data: Any):
    trace = _current_trace.get()
    if trace is not None:
        trace.emit(event, data)


def trace_state(session: Any) -> dict[str, Any]:
    """Only state relevant to prose and routing; full history is already in packed requests."""
    return {key: getattr(session, key, None) for key in (
        "session_id", "revision", "story_package_id", "story_package_revision", "story_package_hash",
        "current_node_id", "node_turn_count", "status", "metrics", "dialogue_policy",
        "transition_offered", "player_address_known", "actor_budget_profile",
    )}


async def invoke_with_trace(client: Any, messages: list[Any], *, stage: str):
    trace = _current_trace.get()
    if trace is None or trace.file is None:
        return await client.ainvoke(messages)  # noqa: LLM_INPUT_BUDGET # Caller has packed and checked these exact messages.
    trace.calls += 1
    call_id = trace.calls
    started_at = time.monotonic()
    try:
        trace_event("model.request", call_id=call_id, stage=stage, model=getattr(client, "model", None),
                    max_completion_tokens=getattr(client, "max_completion_tokens", None),
                    temperature=getattr(client, "temperature", None),
                    messages=[{"role": m.role, "content": m.content} if not isinstance(m, dict)
                              else {"role": m.get("role"), "content": m.get("content")} for m in messages])
    except Exception as exc:
        trace.disable(exc)
    try:
        response = await client.ainvoke(messages)  # noqa: LLM_INPUT_BUDGET # Observe without modifying the packed request.
    except BaseException as exc:
        trace_event("model.failed", call_id=call_id, stage=stage, error_type=type(exc).__name__,
                    cancelled=isinstance(exc, asyncio.CancelledError),
                    duration_ms=round((time.monotonic() - started_at) * 1000, 3))
        raise
    if trace.file is not None:
        try:
            metadata = getattr(response, "response_metadata", None) or {}
            usage = metadata.get("token_usage") or {}
            trace_event("model.response", call_id=call_id, stage=stage, content=getattr(response, "content", None),
                        finish_reason=metadata.get("finish_reason"),
                        usage={key: value for key in ("prompt_tokens", "completion_tokens", "total_tokens",
                               "input_tokens", "output_tokens", "cache_read_input_tokens", "cache_creation_input_tokens")
                               if type(value := usage.get(key)) is int},
                        duration_ms=round((time.monotonic() - started_at) * 1000, 3))
        except Exception as exc:
            trace.disable(exc)
    return response
