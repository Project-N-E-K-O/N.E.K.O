from __future__ import annotations

import asyncio
import inspect
import logging
import math
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from plugin.sdk.shared.transport.message_plane import MessagePlaneTransport


_logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class StudyEvent:
    name: str
    payload: dict[str, Any]
    timestamp: float = field(default_factory=time.monotonic)


VISIBILITY_MAP: dict[str, list[str]] = {
    "screen_context_changed": [],
    "answer_evaluated": ["chat"],
    "mastery_updated": [],
    "review_due": ["chat"],
    "session_summarized": ["chat"],
}

BEHAVIOR_MAP: dict[str, str] = {
    "screen_context_changed": "read",
    "answer_evaluated": "read",
    "mastery_updated": "read",
    "review_due": "respond",
    "session_summarized": "read",
}

PRIORITY_MAP: dict[str, int] = {
    "screen_context_changed": 0,
    "answer_evaluated": 5,
    "mastery_updated": 2,
    "review_due": 3,
    "session_summarized": 1,
}


class StudyEventBus:
    """Throttle study events and forward them through push_message v2."""

    _THROTTLE_TTL = 3600.0
    _RESPOND_COOLDOWN = 30.0

    def __init__(
        self,
        *,
        plugin_ctx: Any,
        transport: MessagePlaneTransport | None = None,
    ) -> None:
        self._ctx = plugin_ctx
        self._transport = transport
        self._lock = asyncio.Lock()
        self._throttle: dict[str, float] = {}
        self._last_respond_at = -self._RESPOND_COOLDOWN
        self._screen_buf: list[tuple[str, float]] = []
        self._emit_count = 0
        self._block_count = 0

    @property
    def emit_count(self) -> int:
        return self._emit_count

    @property
    def block_count(self) -> int:
        return self._block_count

    def schedule_emit(self, event: StudyEvent) -> None:
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            _logger.warning("StudyEventBus.schedule_emit() called outside event loop")
            return
        task = loop.create_task(self.emit(event))
        task.add_done_callback(_on_emit_done)

    async def emit(self, event: StudyEvent) -> None:
        async with self._lock:
            if not self._should_emit(event):
                self._block_count += 1
                return
            self._emit_count += 1
            behavior = self._resolve_behavior(event)
            text = self._format(event)
            visibility = VISIBILITY_MAP.get(event.name, [])
            priority = PRIORITY_MAP.get(event.name, 2)
        result = self._ctx.push_message(
            visibility=visibility,
            ai_behavior=behavior,
            priority=priority,
            parts=[{"type": "text", "text": text}],
            source="study_companion",
        )
        if inspect.isawaitable(result):
            await result

    def _should_emit(self, event: StudyEvent) -> bool:
        now = time.monotonic()
        self._prune_throttle(now)
        if event.name == "screen_context_changed":
            return self._throttle_screen_context(event, now)
        if event.name == "answer_evaluated":
            return self._throttle_answer_evaluated(event, now)
        if event.name == "mastery_updated":
            return self._throttle_mastery_updated(event, now)
        if event.name == "review_due":
            return self._throttle_review_due(event, now)
        return True

    def _prune_throttle(self, now: float) -> None:
        stale = [
            key for key, emitted_at in self._throttle.items()
            if now - emitted_at > self._THROTTLE_TTL
        ]
        for key in stale:
            del self._throttle[key]

    def _throttle_screen_context(self, event: StudyEvent, now: float) -> bool:
        payload = event.payload
        screen_type = str(payload.get("screen_type") or "").strip()
        confidence = _safe_float(payload.get("confidence"), 0.0)
        if not screen_type or confidence < 0.6:
            return False

        self._screen_buf.append((screen_type, confidence))
        if len(self._screen_buf) > 8:
            self._screen_buf = self._screen_buf[-8:]
        recent_same = sum(
            1 for item, _ in self._screen_buf[-3:] if item == screen_type
        )
        if recent_same < 3:
            return False

        key = f"screen:{screen_type}"
        last = self._throttle.get(key)
        if last is not None and now - last < 300.0:
            return False
        self._throttle[key] = now
        return True

    def _throttle_answer_evaluated(self, event: StudyEvent, now: float) -> bool:
        return True

    def _throttle_mastery_updated(self, event: StudyEvent, now: float) -> bool:
        topic = str(event.payload.get("topic") or "").strip()
        mastery = _safe_float(event.payload.get("mastery"), 0.0)
        previous = _safe_float(event.payload.get("mastery_before"), 0.0)
        if not topic or abs(mastery - previous) < 0.05:
            return False

        key = f"mastery:{topic}"
        last = self._throttle.get(key)
        if last is not None and now - last < 600.0:
            return False
        self._throttle[key] = now
        return True

    def _throttle_review_due(self, event: StudyEvent, now: float) -> bool:
        key = "review_due"
        last = self._throttle.get(key)
        if last is not None and now - last < 1800.0:
            return False
        self._throttle[key] = now
        return True

    def _resolve_behavior(self, event: StudyEvent) -> str:
        behavior = BEHAVIOR_MAP.get(event.name, "read")
        if event.name != "answer_evaluated":
            return behavior
        verdict = str(event.payload.get("verdict") or "").strip().lower()
        if verdict not in {"incorrect", "partial", "wrong", "dont_know"}:
            return behavior
        now = time.monotonic()
        if now - self._last_respond_at < self._RESPOND_COOLDOWN:
            return behavior
        self._last_respond_at = now
        return "respond"

    def _format(self, event: StudyEvent) -> str:
        formatter = _FORMATTERS.get(event.name)
        if formatter is not None:
            return formatter(event.payload)
        return str(event.payload)


def _on_emit_done(task: asyncio.Task[None]) -> None:
    try:
        task.result()
    except asyncio.CancelledError:
        return
    except Exception:
        _logger.exception("StudyEventBus.schedule_emit() task failed")


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return default
    return number if math.isfinite(number) else default


def _ratio(value: Any) -> float:
    number = _safe_float(value, 0.0)
    if number > 1.0:
        number /= 100.0
    return max(0.0, min(1.0, number))


def _fmt_screen_context(payload: dict[str, Any]) -> str:
    screen_type = str(payload.get("screen_type") or "unknown")
    summary = str(payload.get("ocr_summary") or "").strip()
    previous = str(payload.get("previous_type") or "").strip()
    prefix = f"[Screen Context Changed] {previous or 'unknown'} -> {screen_type}"
    return f"{prefix}\n{summary}" if summary else prefix


def _fmt_answer_evaluated(payload: dict[str, Any]) -> str:
    verdict_map = {
        "correct": "correct",
        "partial": "partially correct",
        "incorrect": "incorrect",
        "wrong": "incorrect",
        "dont_know": "not answered",
    }
    verdict = verdict_map.get(
        str(payload.get("verdict") or "").strip().lower(), "evaluated"
    )
    score = _ratio(payload.get("score"))
    question = str(payload.get("question_summary") or "").strip()
    answer = str(payload.get("user_answer_summary") or "").strip()
    hint = str(payload.get("correction_hint") or "").strip()
    topic = str(payload.get("topic") or "").strip()
    before = _safe_float(payload.get("mastery_before"), -1.0)
    after = _safe_float(payload.get("mastery_after"), -1.0)

    lines = [
        f"[Answer Evaluated] {verdict} (score: {score:.0%})",
        f"Question: {question}",
        f"Answer: {answer}",
    ]
    if hint:
        lines.append(f"Hint: {hint}")
    if topic:
        if before >= 0.0 and after >= 0.0:
            lines.append(f"Topic: {topic} (mastery {before:.0%} -> {after:.0%})")
        else:
            lines.append(f"Topic: {topic}")
    return "\n".join(lines)


def _fmt_mastery_updated(payload: dict[str, Any]) -> str:
    direction = "up" if payload.get("direction") == "up" else "down"
    topic = str(payload.get("topic") or "").strip()
    mastery = _ratio(payload.get("mastery"))
    threshold = str(payload.get("crossed_threshold") or "").strip()
    count = int(_safe_float(payload.get("evidence_count"), 0.0))
    return (
        f"[Mastery Updated] {topic}: {direction} to {mastery:.0%}\n"
        f"Crossed threshold: {threshold} | evidence: {count}"
    )


def _fmt_review_due(payload: dict[str, Any]) -> str:
    due = int(_safe_float(payload.get("due_count"), 0.0))
    urgent = int(_safe_float(payload.get("urgent_count"), 0.0))
    topics = ", ".join(str(item) for item in payload.get("topics") or [] if item)
    suggestion = str(payload.get("suggestion") or "").strip()
    return (
        f"[Review Due] {due} card(s) due ({urgent} overdue)\n"
        f"Topics: {topics}\n{suggestion}"
    ).strip()


def _fmt_session_summarized(payload: dict[str, Any]) -> str:
    duration = int(_safe_float(payload.get("duration_minutes"), 0.0))
    questions = int(_safe_float(payload.get("questions_attempted"), 0.0))
    rate = _ratio(payload.get("correct_rate"))
    insight = str(payload.get("key_insight") or "").strip()
    return (
        f"[Session Summarized] {duration} min | {questions} question(s) | "
        f"correct rate {rate:.0%}\n{insight}"
    ).strip()


_FORMATTERS: dict[str, Callable[[dict[str, Any]], str]] = {
    "screen_context_changed": _fmt_screen_context,
    "answer_evaluated": _fmt_answer_evaluated,
    "mastery_updated": _fmt_mastery_updated,
    "review_due": _fmt_review_due,
    "session_summarized": _fmt_session_summarized,
}


__all__ = [
    "StudyEvent",
    "StudyEventBus",
]
