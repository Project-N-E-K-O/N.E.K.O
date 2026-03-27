"""Finish envelope helpers for SDK v2 plugin entries.

This module keeps the low-level envelope construction separate from the
plugin-facing context facade so higher-level APIs can stay small and explicit.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Mapping
from datetime import datetime, timezone
from typing import Any


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _normalize_meta(
    *,
    reply: bool,
    meta: Mapping[str, object] | None,
) -> dict[str, object]:
    normalized: dict[str, object] = {}
    if isinstance(meta, Mapping):
        for key_obj, value in meta.items():
            if isinstance(key_obj, str):
                normalized[key_obj] = value

    raw_agent_meta = normalized.get("agent")
    agent_meta: dict[str, object] = {}
    if isinstance(raw_agent_meta, Mapping):
        for key_obj, value in raw_agent_meta.items():
            if isinstance(key_obj, str):
                agent_meta[key_obj] = value
    agent_meta["reply"] = bool(reply)
    if reply and "include" not in agent_meta:
        agent_meta["include"] = True
    normalized["agent"] = agent_meta
    return normalized


def normalize_structured_data(data: Any) -> Any:
    """Convert model-like payloads into plain Python data for transport."""

    model_dump = getattr(data, "model_dump", None)
    if callable(model_dump):
        try:
            dumped = model_dump(mode="python")
        except TypeError:
            dumped = model_dump()
        return normalize_structured_data(dumped)

    dict_fn = getattr(data, "dict", None)
    if callable(dict_fn):
        return normalize_structured_data(dict_fn())

    if isinstance(data, Mapping):
        return {
            str(key): normalize_structured_data(value)
            for key, value in data.items()
        }

    if isinstance(data, list):
        return [normalize_structured_data(item) for item in data]

    if isinstance(data, tuple):
        return [normalize_structured_data(item) for item in data]

    if dataclasses.is_dataclass(data):
        return normalize_structured_data(dataclasses.asdict(data))

    return data


def build_finish_envelope(
    *,
    data: Any = None,
    reply: bool = True,
    message: str = "",
    trace_id: str | None = None,
    meta: Mapping[str, object] | None = None,
) -> dict[str, object]:
    payload: dict[str, object] = {
        "success": True,
        "code": 0,
        "data": normalize_structured_data(data),
        "message": message,
        "error": None,
        "time": _now_iso(),
        "trace_id": trace_id,
    }
    normalized_meta = _normalize_meta(reply=reply, meta=meta)
    payload["meta"] = normalized_meta
    return payload

__all__ = ["build_finish_envelope", "normalize_structured_data"]
