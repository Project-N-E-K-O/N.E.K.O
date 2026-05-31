from __future__ import annotations

import math
from collections.abc import Mapping
from typing import Any

VOICE_TRANSCRIPT_EVENT_TYPE = "voice_transcript"
VOICE_TRANSCRIPT_EVENT_ID = "handle_transcript"

VOICE_TRANSCRIPT_ACTION_NOOP = "noop"
VOICE_TRANSCRIPT_ACTION_CANCEL_RESPONSE = "cancel_response"
VOICE_TRANSCRIPT_ACTION_PRIME_CONTEXT = "prime_context"
VOICE_TRANSCRIPT_ACTIONS = {
    VOICE_TRANSCRIPT_ACTION_NOOP,
    VOICE_TRANSCRIPT_ACTION_CANCEL_RESPONSE,
    VOICE_TRANSCRIPT_ACTION_PRIME_CONTEXT,
}
VOICE_TRANSCRIPT_ACTION_RANK = {
    VOICE_TRANSCRIPT_ACTION_NOOP: 0,
    VOICE_TRANSCRIPT_ACTION_PRIME_CONTEXT: 1,
    VOICE_TRANSCRIPT_ACTION_CANCEL_RESPONSE: 2,
}


def voice_transcript_noop(reason: str, **extra: object) -> dict[str, object]:
    payload = dict(extra)
    payload.update(
        {
            "action": VOICE_TRANSCRIPT_ACTION_NOOP,
            "reason": str(reason or "noop"),
        }
    )
    return payload


def voice_transcript_cancel_response(
    *,
    filter_payload: Mapping[str, object] | None = None,
    **extra: object,
) -> dict[str, object]:
    payload: dict[str, object] = dict(extra)
    payload["action"] = VOICE_TRANSCRIPT_ACTION_CANCEL_RESPONSE
    if filter_payload is not None:
        payload["filter"] = dict(filter_payload)
    return payload


def voice_transcript_prime_context(
    context: str,
    *,
    skipped: bool = False,
    filter_payload: Mapping[str, object] | None = None,
    **extra: object,
) -> dict[str, object]:
    payload: dict[str, object] = dict(extra)
    payload.update(
        {
            "action": VOICE_TRANSCRIPT_ACTION_PRIME_CONTEXT,
            "context": str(context or "").strip(),
            "skipped": bool(skipped),
        }
    )
    if filter_payload is not None:
        payload["filter"] = dict(filter_payload)
    return payload


def _coerce_priority(value: object) -> float:
    if isinstance(value, bool):
        return 0.0
    if isinstance(value, (int, float)):
        priority = float(value)
        return priority if math.isfinite(priority) else 0.0
    if isinstance(value, str):
        try:
            priority = float(value.strip())
        except ValueError:
            return 0.0
        return priority if math.isfinite(priority) else 0.0
    return 0.0


def _normalize_voice_transcript_candidate(
    item: Mapping[str, object],
    *,
    fallback_reason: str = "",
) -> dict[str, Any] | None:
    if not bool(item.get("success")):
        return None
    raw_result = item.get("result")
    if not isinstance(raw_result, Mapping):
        return None

    candidate = dict(raw_result)
    action = str(candidate.get("action") or VOICE_TRANSCRIPT_ACTION_NOOP).strip()
    if action not in VOICE_TRANSCRIPT_ACTIONS:
        action = VOICE_TRANSCRIPT_ACTION_NOOP
        fallback_reason = "invalid_action"

    priority = _coerce_priority(candidate.get("priority", 0))
    reason = str(candidate.get("reason") or fallback_reason)
    skipped = bool(candidate.get("skipped", False))
    plugin_id = str(item.get("plugin_id") or "").strip()

    if action == VOICE_TRANSCRIPT_ACTION_PRIME_CONTEXT:
        context_text = str(candidate.get("context") or "").strip()
        if not context_text:
            action = VOICE_TRANSCRIPT_ACTION_NOOP
            reason = "empty_context"
        else:
            candidate["context"] = context_text

    candidate["action"] = action
    candidate["priority"] = priority
    candidate["reason"] = reason
    candidate["skipped"] = skipped
    if plugin_id:
        candidate["source_plugin"] = plugin_id
    event_id = str(item.get("event_id") or "").strip()
    if event_id:
        candidate.setdefault("source_event_id", event_id)
    return candidate


def arbitrate_voice_transcript_results(dispatch_results: object) -> dict[str, Any]:
    if not isinstance(dispatch_results, list) or not dispatch_results:
        return voice_transcript_noop(
            "no_subscribers",
            priority=0.0,
            skipped=False,
        )

    selected: tuple[int, float, int, dict[str, Any]] | None = None
    noop_count = 0
    failure_count = 0
    for index, item in enumerate(dispatch_results):
        if not isinstance(item, Mapping):
            continue
        if not bool(item.get("success")):
            failure_count += 1
            continue
        candidate = _normalize_voice_transcript_candidate(item)
        if candidate is None:
            continue
        action = str(candidate.get("action") or VOICE_TRANSCRIPT_ACTION_NOOP)
        if action == VOICE_TRANSCRIPT_ACTION_NOOP:
            noop_count += 1
            continue
        priority = _coerce_priority(candidate.get("priority", 0))
        rank = VOICE_TRANSCRIPT_ACTION_RANK.get(action, 0)
        contender = (rank, priority, -index, candidate)
        if selected is None or contender[:3] > selected[:3]:
            selected = contender

    if selected is not None:
        return selected[3]
    if noop_count:
        return voice_transcript_noop(
            "all_noop",
            priority=0.0,
            skipped=False,
            handlers=noop_count,
            failures=failure_count,
        )
    return voice_transcript_noop(
        "no_handler_result",
        priority=0.0,
        skipped=False,
        failures=failure_count,
    )


__all__ = [
    "VOICE_TRANSCRIPT_ACTION_CANCEL_RESPONSE",
    "VOICE_TRANSCRIPT_ACTION_NOOP",
    "VOICE_TRANSCRIPT_ACTION_PRIME_CONTEXT",
    "VOICE_TRANSCRIPT_EVENT_ID",
    "VOICE_TRANSCRIPT_EVENT_TYPE",
    "arbitrate_voice_transcript_results",
    "voice_transcript_cancel_response",
    "voice_transcript_noop",
    "voice_transcript_prime_context",
]
