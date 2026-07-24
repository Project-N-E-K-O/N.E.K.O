"""Detect viewer answers to recent NEKO active-engagement hooks."""

from __future__ import annotations

from typing import Any

from .contracts import ViewerEvent
from .contracts_public import public_text


ACTIVE_HOOK_RESULT_SCAN_LIMIT = 8
ACTIVE_HOOK_ANSWER_MAX_LEN = 8
_OPTION_ANSWER_TOKENS = {
    "a",
    "b",
    "c",
    "d",
    "选a",
    "选b",
    "选c",
    "选d",
    "我选a",
    "我选b",
    "我选c",
    "我选d",
    "a选项",
    "b选项",
    "c选项",
    "d选项",
}


def is_active_hook_answer_event(recent_results: Any, event: ViewerEvent) -> bool:
    if not _is_live_danmaku(event):
        return False
    if not _looks_like_short_hook_answer(event.danmaku_text):
        return False
    return _has_recent_active_hook(recent_results)


def _is_live_danmaku(event: ViewerEvent) -> bool:
    return event.source in {"live_danmaku", "manual_live_simulation"} and bool(
        str(event.danmaku_text or "").strip()
    )


def _looks_like_short_hook_answer(text: str) -> bool:
    dense = _dense_text(text)
    if not dense or len(dense) > ACTIVE_HOOK_ANSWER_MAX_LEN:
        return False
    if dense in _OPTION_ANSWER_TOKENS:
        return True
    if len(dense) == 1 and dense in {"a", "b", "c", "d", "1", "2", "3", "4"}:
        return True
    if len(dense) <= 4 and any(marker in dense for marker in ("选", "要", "投")):
        return True
    return False


def _has_recent_active_hook(recent_results: Any) -> bool:
    try:
        results = list(recent_results or [])
    except TypeError:
        return False
    scanned = 0
    for result in reversed(results):
        if not isinstance(result, dict):
            continue
        status = str(result.get("status") or "")
        if status not in {"pushed", "dry_run"}:
            continue
        event = result.get("event") if isinstance(result.get("event"), dict) else {}
        source = public_text(event.get("source"))
        if source == "active_engagement":
            request = result.get("request") if isinstance(result.get("request"), dict) else {}
            metadata = (
                request.get("metadata")
                if isinstance(request.get("metadata"), dict)
                else {}
            )
            return any(
                _active_event_has_reply_hook(candidate)
                for candidate in (event, result, metadata)
            )
        scanned += 1
        if scanned >= ACTIVE_HOOK_RESULT_SCAN_LIMIT:
            break
    return False


def _active_event_has_reply_hook(event: dict[str, Any]) -> bool:
    fields = (
        event.get("topic_reply_affordance"),
        event.get("topic_shape"),
        event.get("topic_intent"),
        event.get("topic_fun_axis"),
        event.get("topic_pack"),
    )
    rendered = " ".join(public_text(value, max_len=80).casefold() for value in fields)
    return any(
        marker in rendered
        for marker in (
            "answer",
            "choice",
            "either_or",
            "tiny_answer",
            "viewer can",
            "one side",
            "micro_poll",
        )
    )


def _dense_text(text: str) -> str:
    return "".join(ch for ch in str(text or "").casefold() if ch.isalnum() or "\u4e00" <= ch <= "\u9fff")
