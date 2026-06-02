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


@dataclass(frozen=True)
class _EmitDecision:
    allowed: bool
    throttle_key: str = ""
    screen_context_type: str = ""


@dataclass(frozen=True)
class _PreparedEmit:
    decision: _EmitDecision
    mark_respond: bool
    message: dict[str, Any]


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
    _MAX_IN_FLIGHT_EMITS = 8
    _MAX_QUEUE_SIZE = 64
    _MAX_WORKER_FAILURES = 3
    _WORKER_FAILURE_BACKOFF_BASE_SECONDS = 0.05
    _WORKER_FAILURE_BACKOFF_MAX_SECONDS = 1.0

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
        self._pending_throttle: set[str] = set()
        self._pending_screen_context_types: set[str] = set()
        self._pending_respond_count = 0
        self._scheduled_emit_count = 0
        self._dropped_emit_count = 0
        self._emit_semaphore = asyncio.Semaphore(self._MAX_IN_FLIGHT_EMITS)
        self._queue: asyncio.Queue[StudyEvent] = asyncio.Queue(
            maxsize=self._MAX_QUEUE_SIZE
        )
        self._worker_task: asyncio.Task[None] | None = None
        self._worker_failure_count = 0
        self._last_respond_at = -self._RESPOND_COOLDOWN
        self._last_screen_context_type = ""
        self._screen_buf: list[tuple[str, float]] = []
        self._emit_count = 0
        self._block_count = 0

    @property
    def emit_count(self) -> int:
        return self._emit_count

    @property
    def block_count(self) -> int:
        return self._block_count

    @property
    def scheduled_emit_count(self) -> int:
        return self._scheduled_emit_count

    @property
    def dropped_emit_count(self) -> int:
        return self._dropped_emit_count

    def schedule_emit(self, event: StudyEvent) -> asyncio.Task[None] | None:
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            _logger.warning("StudyEventBus.schedule_emit() called outside event loop")
            return None
        if self._worker_task is None or self._worker_task.done():
            self._worker_failure_count = 0
            self._worker_task = loop.create_task(self._consume_queue())
        if self._queue.full():
            try:
                dropped = self._queue.get_nowait()
                self._safe_task_done()
                self._scheduled_emit_count = max(0, self._scheduled_emit_count - 1)
                self._dropped_emit_count += 1
                _logger.warning(
                    "StudyEventBus.schedule_emit() dropped oldest event due to backlog: %s",
                    dropped.name,
                )
            except asyncio.QueueEmpty:
                pass
        try:
            self._queue.put_nowait(event)
            self._scheduled_emit_count += 1
        except asyncio.QueueFull:
            self._dropped_emit_count += 1
            _logger.warning(
                "StudyEventBus.schedule_emit() dropped event due to backlog: %s",
                event.name,
            )
            return None
        return self._worker_task

    async def stop_worker(self) -> None:
        task = self._worker_task
        if task is None:
            return
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        finally:
            if self._worker_task is task:
                self._worker_task = None

    async def _consume_queue(self) -> None:
        task = asyncio.current_task()
        try:
            while True:
                event = await self._queue.get()
                should_stop = False
                backoff_seconds = 0.0
                try:
                    async with self._emit_semaphore:
                        await self.emit(event)
                    self._worker_failure_count = 0
                except asyncio.CancelledError:
                    raise
                except Exception:
                    self._worker_failure_count += 1
                    _logger.exception("StudyEventBus worker emit failed")
                    if self._worker_failure_count >= self._MAX_WORKER_FAILURES:
                        _logger.error(
                            "StudyEventBus worker stopped after %s consecutive failures",
                            self._worker_failure_count,
                        )
                        should_stop = True
                    else:
                        backoff_seconds = min(
                            self._WORKER_FAILURE_BACKOFF_MAX_SECONDS,
                            self._WORKER_FAILURE_BACKOFF_BASE_SECONDS
                            * (2 ** (self._worker_failure_count - 1)),
                        )
                finally:
                    self._scheduled_emit_count = max(
                        0, self._scheduled_emit_count - 1
                    )
                    self._safe_task_done()
                if should_stop:
                    self._drop_queued_events_after_worker_stop()
                    return
                if backoff_seconds > 0:
                    await asyncio.sleep(backoff_seconds)
        finally:
            if self._worker_task is task:
                self._worker_task = None

    def _drop_queued_events_after_worker_stop(self) -> None:
        dropped = 0
        while True:
            try:
                self._queue.get_nowait()
            except asyncio.QueueEmpty:
                break
            dropped += 1
            self._scheduled_emit_count = max(0, self._scheduled_emit_count - 1)
            self._dropped_emit_count += 1
            self._safe_task_done()
        if dropped:
            _logger.error(
                "StudyEventBus worker dropped %s queued event(s) after stopping",
                dropped,
            )

    def _safe_task_done(self) -> None:
        try:
            self._queue.task_done()
        except ValueError:
            _logger.exception("StudyEventBus queue task_done underflow")

    def should_schedule_screen_context(
        self, screen_type: str, previous_type: str = ""
    ) -> bool:
        normalized = str(screen_type or "").strip()
        previous = str(previous_type or "").strip()
        if not normalized:
            return False
        return not (
            self._last_screen_context_type == normalized and previous == normalized
        )

    async def emit(self, event: StudyEvent) -> None:
        async with self._lock:
            now = time.monotonic()
            self._prune_throttle(now)
            decision = self._emit_decision(event, now)
            if not decision.allowed:
                self._block_count += 1
                return
            behavior, mark_respond = self._resolve_behavior(event, now)
            text = self._format(event)
            visibility = VISIBILITY_MAP.get(event.name, [])
            priority = PRIORITY_MAP.get(event.name, 2)
            prepared = _PreparedEmit(
                decision=decision,
                mark_respond=mark_respond,
                message={
                    "visibility": visibility,
                    "ai_behavior": behavior,
                    "priority": priority,
                    "parts": [{"type": "text", "text": text}],
                    "source": "study_companion",
                },
            )
            self._reserve_emit(decision, mark_respond=mark_respond)

        try:
            result = self._ctx.push_message(**prepared.message)
            if inspect.isawaitable(result):
                result = await result
            if isinstance(result, dict) and result.get("ok") is False:
                raise RuntimeError("study event push_message returned ok=false")
        except asyncio.CancelledError:
            async with self._lock:
                self._release_emit_reservation(
                    prepared.decision,
                    mark_respond=prepared.mark_respond,
                )
            raise
        except Exception:
            async with self._lock:
                self._release_emit_reservation(
                    prepared.decision,
                    mark_respond=prepared.mark_respond,
                )
            raise
        else:
            async with self._lock:
                self._commit_emit(decision, mark_respond=mark_respond)
                self._release_emit_reservation(
                    prepared.decision,
                    mark_respond=prepared.mark_respond,
                )

    def _reserve_emit(
        self, decision: _EmitDecision, *, mark_respond: bool = False
    ) -> None:
        if decision.throttle_key:
            self._pending_throttle.add(decision.throttle_key)
        if decision.screen_context_type:
            self._pending_screen_context_types.add(decision.screen_context_type)
        if mark_respond:
            self._pending_respond_count += 1

    def _release_emit_reservation(
        self, decision: _EmitDecision, *, mark_respond: bool = False
    ) -> None:
        if decision.throttle_key:
            self._pending_throttle.discard(decision.throttle_key)
        if decision.screen_context_type:
            self._pending_screen_context_types.discard(decision.screen_context_type)
        if mark_respond:
            self._pending_respond_count = max(0, self._pending_respond_count - 1)

    def _emit_decision(self, event: StudyEvent, now: float) -> _EmitDecision:
        if event.name == "screen_context_changed":
            return self._throttle_screen_context(event, now)
        if event.name == "answer_evaluated":
            return self._throttle_answer_evaluated(event, now)
        if event.name == "mastery_updated":
            return self._throttle_mastery_updated(event, now)
        if event.name == "review_due":
            return self._throttle_review_due(event, now)
        return _EmitDecision(allowed=True)

    def _commit_emit(
        self, decision: _EmitDecision, *, mark_respond: bool = False
    ) -> None:
        committed_at = time.monotonic()
        if decision.throttle_key:
            self._throttle[decision.throttle_key] = committed_at
        if decision.screen_context_type:
            self._last_screen_context_type = decision.screen_context_type
        if mark_respond:
            self._last_respond_at = committed_at
        self._emit_count += 1

    def _prune_throttle(self, now: float) -> None:
        stale = [
            key
            for key, emitted_at in self._throttle.items()
            if now - emitted_at > self._THROTTLE_TTL
        ]
        for key in stale:
            del self._throttle[key]

    def _throttle_screen_context(self, event: StudyEvent, now: float) -> _EmitDecision:
        payload = event.payload
        screen_type = str(payload.get("screen_type") or "").strip()
        confidence = _safe_float(payload.get("confidence"), 0.0)
        if not screen_type or confidence < 0.6:
            return _EmitDecision(allowed=False)

        self._screen_buf.append((screen_type, confidence))
        if len(self._screen_buf) > 8:
            self._screen_buf = self._screen_buf[-8:]
        recent_same = sum(1 for item, _ in self._screen_buf[-3:] if item == screen_type)
        if recent_same < 3:
            return _EmitDecision(allowed=False)

        key = f"screen:{screen_type}"
        previous_type = str(payload.get("previous_type") or "").strip()
        if (
            self._last_screen_context_type == screen_type
            and previous_type == screen_type
        ):
            return _EmitDecision(allowed=False)
        if screen_type in self._pending_screen_context_types:
            return _EmitDecision(allowed=False)
        if key in self._pending_throttle:
            return _EmitDecision(allowed=False)
        last = self._throttle.get(key)
        if last is not None and now - last < 300.0:
            return _EmitDecision(allowed=False)
        return _EmitDecision(
            allowed=True,
            throttle_key=key,
            screen_context_type=screen_type,
        )

    def _throttle_answer_evaluated(
        self, event: StudyEvent, now: float
    ) -> _EmitDecision:
        return _EmitDecision(allowed=True)

    def _throttle_mastery_updated(self, event: StudyEvent, now: float) -> _EmitDecision:
        topic = str(event.payload.get("topic") or "").strip()
        mastery = _safe_float(event.payload.get("mastery"), 0.0)
        previous = _safe_float(event.payload.get("mastery_before"), 0.0)
        crossed_threshold = str(event.payload.get("crossed_threshold") or "").strip()
        if not topic or (not crossed_threshold and abs(mastery - previous) < 0.05):
            return _EmitDecision(allowed=False)

        key = f"mastery:{topic}"
        if key in self._pending_throttle:
            return _EmitDecision(allowed=False)
        last = self._throttle.get(key)
        if last is not None and now - last < 600.0:
            return _EmitDecision(allowed=False)
        return _EmitDecision(allowed=True, throttle_key=key)

    def _throttle_review_due(self, event: StudyEvent, now: float) -> _EmitDecision:
        key = "review_due"
        if key in self._pending_throttle:
            return _EmitDecision(allowed=False)
        last = self._throttle.get(key)
        if last is not None and now - last < 1800.0:
            return _EmitDecision(allowed=False)
        return _EmitDecision(allowed=True, throttle_key=key)

    def _resolve_behavior(self, event: StudyEvent, now: float) -> tuple[str, bool]:
        behavior = BEHAVIOR_MAP.get(event.name, "read")
        if event.name != "answer_evaluated":
            return behavior, False
        verdict = str(event.payload.get("verdict") or "").strip().lower()
        if verdict not in {"incorrect", "partial", "wrong", "dont_know"}:
            return behavior, False
        if self._pending_respond_count > 0:
            return behavior, False
        if now - self._last_respond_at < self._RESPOND_COOLDOWN:
            return behavior, False
        return "respond", True

    def _format(self, event: StudyEvent) -> str:
        formatter = _FORMATTERS.get(event.name)
        if formatter is not None:
            return formatter(event.payload)
        return str(event.payload)


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
