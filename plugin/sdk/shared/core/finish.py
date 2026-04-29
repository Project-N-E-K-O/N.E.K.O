"""Finish envelope helpers for SDK v2 plugin entries.

This module keeps the low-level envelope construction separate from the
plugin-facing context facade so higher-level APIs can stay small and explicit.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Mapping
from datetime import datetime, timezone
from typing import Any


# 任务结果如何送达主 AI 的三档枚举。
#   - "proactive"：默认。立即起一轮，让角色主动汇报。
#   - "passive" ：写入上下文但不打断用户；下一次用户发言时被自然带入 prompt。
#   - "silent"  ：完全跳过 LLM 通道（仅前端 HUD/task_update）。
DELIVERY_MODES = ("proactive", "passive", "silent")
DEFAULT_DELIVERY = "proactive"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def normalize_delivery(
    delivery: str | bool | None,
    reply: bool | None = None,
) -> str:
    """Resolve the effective delivery mode from possibly-overlapping inputs.

    Accepts ``delivery`` (preferred, three-state string) and ``reply``
    (deprecated bool alias kept for backward compatibility):
        - ``delivery="proactive"|"passive"|"silent"`` → used as-is
        - ``delivery=True`` → ``"proactive"``  (very old call sites)
        - ``delivery=False`` → ``"silent"``
        - ``reply=True``  → ``"proactive"``
        - ``reply=False`` → ``"silent"``
    Priority: when ``delivery`` is provided (even if invalid) it owns the
    decision — invalid values fall back to :data:`DEFAULT_DELIVERY` rather
    than letting ``reply`` quietly override. This avoids
    ``delivery="typo", reply=False`` silently flipping to ``"silent"``.
    Only when ``delivery is None`` do we consult ``reply``.
    """
    if delivery is not None:
        if isinstance(delivery, str) and delivery in DELIVERY_MODES:
            return delivery
        if isinstance(delivery, bool):
            return "proactive" if delivery else "silent"
        # delivery was specified but invalid type/value → don't fall through
        # to reply (the caller intended to set delivery, not reply).
        return DEFAULT_DELIVERY
    if isinstance(reply, bool):
        return "proactive" if reply else "silent"
    return DEFAULT_DELIVERY


def _normalize_meta(
    *,
    delivery: str,
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
    # Canonical field is ``delivery``; ``reply`` is kept as a bool alias for
    # downstream consumers that haven't migrated yet (silent → False, both
    # other modes → True).
    agent_meta["delivery"] = delivery
    agent_meta["reply"] = delivery != "silent"
    if delivery != "silent" and "include" not in agent_meta:
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
    delivery: str | bool | None = None,
    reply: bool | None = None,
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
    resolved = normalize_delivery(delivery, reply)
    normalized_meta = _normalize_meta(delivery=resolved, meta=meta)
    payload["meta"] = normalized_meta
    return payload


__all__ = [
    "build_finish_envelope",
    "normalize_structured_data",
    "normalize_delivery",
    "DELIVERY_MODES",
    "DEFAULT_DELIVERY",
]
