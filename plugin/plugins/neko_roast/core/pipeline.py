"""Unified live/sandbox pipeline."""

from __future__ import annotations

from typing import Any

from .contracts import (
    InteractionResult,
    PipelineStep,
    SafetyDecision,
    ViewerEvent,
)
from . import pipeline_flow
from .active_hook_answers import is_active_hook_answer_event
from .pipeline_routing import (
    PipelineRoute,
    is_live_danmaku_with_text,
    is_transient_event,
    route_for_event,
)
from .pipeline_results import (
    reject_missing_uid,
    skip_before_event,
    skip_permission,
)
from .pipeline_session import PipelineSessionTracker
from .runtime_timeline import ensure_trace_id, record_timeline

AVATAR_ROAST_VIEWER_COUNT_LIMIT = 200
AVATAR_ROAST_RECENT_REPLY_WINDOW_SECONDS = 60.0
BATCH_WELCOME_INTENT = "\u76f4\u64ad\u95f4\u4e00\u4e0b\u70ed\u95f9\u8d77\u6765\u4e86\uff0c\u9700\u8981\u81ea\u7136\u548c\u5192\u6ce1\u7684\u5927\u5bb6\u6253\u4e2a\u62db\u547c\u3002"
LIVE_STATUS_GATED_SOURCES = {
    "live_danmaku",
    "warmup_hosting",
    "idle_hosting",
    "active_engagement",
}
LIVE_STATUS_HOSTING_SOURCES = {
    "warmup_hosting",
    "idle_hosting",
    "active_engagement",
}
LIVE_STATUS_OPEN_VALUES = {"live"}
LIVE_STATUS_BLOCK_REASONS = {
    "room_not_configured",
    "live_disabled",
    "live_ingest_disconnected",
    "live_room_offline",
    "output_channel_unavailable",
}


class RoastPipeline:
    def __init__(self, ctx: Any) -> None:
        self.ctx = ctx
        self.session = PipelineSessionTracker()

    def clear_dry_run_session_state(self) -> None:
        self.session.clear()

    def _now(self) -> float:
        return self.session._now()

    def _entrance_pacing_active(self) -> bool:
        return self.session.entrance_pacing_active(
            getattr(self.ctx, "config", None),
            now=self._now(),
        )

    def _record_avatar_roast_sent(self) -> None:
        self.session.record_avatar_roast_sent(now=self._now())

    def _avatar_roast_allowed(self, event: ViewerEvent) -> bool:
        if not is_live_danmaku_with_text(event):
            return True
        if self._live_speaker_burst_active():
            return False
        if self._live_viewer_count() >= AVATAR_ROAST_VIEWER_COUNT_LIMIT:
            return False
        if self._safety_queue_near_limit():
            return False
        if self._recent_live_reply_count() >= self._recent_reply_limit():
            return False
        return True

    def _live_events_module(self) -> Any:
        return getattr(self.ctx, "live_events", None)

    def _live_speaker_burst_active(self) -> bool:
        module = self._live_events_module()
        probe = getattr(module, "new_viewer_burst_active", None)
        if callable(probe):
            try:
                return bool(probe())
            except Exception:
                return False
        return False

    def _batch_welcome_available(self) -> bool:
        module = self._live_events_module()
        probe = getattr(module, "batch_welcome_available", None)
        if callable(probe):
            try:
                return bool(probe())
            except Exception:
                return False
        return False

    def _reserve_batch_welcome(self) -> None:
        module = self._live_events_module()
        reserve = getattr(module, "reserve_batch_welcome", None)
        if callable(reserve):
            try:
                reserve()
            except Exception:
                return

    def _live_speaker_burst_count(self) -> int:
        module = self._live_events_module()
        count = getattr(module, "new_viewer_burst_count", None)
        if callable(count):
            try:
                return max(0, int(count() or 0))
            except Exception:
                return 0
        return 0

    def _mark_batch_welcome_event(self, event: ViewerEvent) -> None:
        raw = event.raw if isinstance(event.raw, dict) else {}
        if not isinstance(event.raw, dict):
            event.raw = raw
        original = str(event.danmaku_text or "").strip()
        if original:
            raw["original_danmaku_text"] = original
        event.danmaku_text = BATCH_WELCOME_INTENT
        raw["live_batch_welcome"] = "new_viewer_burst"
        raw["suppress_avatar_roast"] = True
        raw["new_viewer_burst_count"] = self._live_speaker_burst_count()
        self._reserve_batch_welcome()

    def _live_viewer_count(self) -> int:
        provider = getattr(self.ctx, "live_provider", None)
        state = {}
        listener_state = getattr(provider, "listener_state", None)
        if callable(listener_state):
            try:
                state = listener_state()
            except Exception:
                state = {}
        value = state.get("viewer_count") if isinstance(state, dict) else 0
        try:
            return max(0, int(value or 0))
        except (TypeError, ValueError):
            return 0

    def _safety_queue_near_limit(self) -> bool:
        guard = getattr(self.ctx, "safety_guard", None)
        config = getattr(self.ctx, "config", None)
        try:
            queue_size = int(getattr(guard, "queue_size", 0) or 0)
            queue_limit = int(getattr(config, "queue_limit", 0) or 0)
        except (TypeError, ValueError):
            return False
        if queue_limit <= 0:
            return False
        return queue_size >= max(1, queue_limit - 1)

    def _recent_reply_limit(self) -> int:
        config = getattr(self.ctx, "config", None)
        try:
            queue_limit = int(getattr(config, "queue_limit", 0) or 0)
        except (TypeError, ValueError):
            queue_limit = 0
        return max(1, queue_limit - 1)

    def _recent_live_reply_count(self) -> int:
        recent_results = getattr(self.ctx, "recent_results", []) or []
        count = 0
        for result in reversed(list(recent_results)):
            if not isinstance(result, dict):
                continue
            age = self._recent_result_age_sec(result)
            if age is None:
                continue
            if age > AVATAR_ROAST_RECENT_REPLY_WINDOW_SECONDS:
                break
            if str(result.get("status") or "") not in {"pushed", "dry_run"}:
                continue
            event = result.get("event") if isinstance(result.get("event"), dict) else {}
            if str(event.get("source") or "") != "live_danmaku":
                continue
            module = str(result.get("response_module") or "")
            if module and module not in {"danmaku_response", "avatar_roast"}:
                continue
            count += 1
        return count

    def _recent_result_age_sec(self, result: dict[str, Any]) -> float | None:
        created_at = result.get("created_at")
        if not created_at:
            return None
        age_fn = getattr(self.ctx, "_iso_age_sec", None)
        if not callable(age_fn):
            return None
        try:
            age = float(age_fn(created_at))
        except Exception:
            return None
        return age if age >= 0 else None

    def _live_status_gate_decision(self, event: ViewerEvent) -> SafetyDecision | None:
        if event.source not in LIVE_STATUS_GATED_SOURCES:
            return None
        summary = getattr(self.ctx, "live_status_summary", None)
        snapshot = getattr(self.ctx, "live_connection_snapshot", None)
        if not callable(summary) or not callable(snapshot):
            return None
        try:
            status = summary(snapshot())
        except Exception:
            return None
        if not isinstance(status, dict):
            return None
        reason = str(status.get("reason") or "").strip()
        if reason in {"room_not_configured", "live_room_offline"} and self._is_support_signal_event(event):
            return None
        if reason in LIVE_STATUS_BLOCK_REASONS or (
            status.get("can_output") is False and reason != "dry_run"
        ):
            return SafetyDecision(
                False,
                "paused",
                f"live_status.{reason}",
            )
        room_status = str(status.get("live_status") or "").strip().casefold()
        summary_text = str(status.get("summary") or "").strip()
        if (
            event.source in LIVE_STATUS_HOSTING_SOURCES
            and reason != "dry_run"
            and summary_text != "test_only"
            and room_status not in LIVE_STATUS_OPEN_VALUES
        ):
            return SafetyDecision(
                False,
                "paused",
                f"live_status.not_live:{room_status or 'unknown'}",
            )
        return None

    @staticmethod
    def _is_support_signal_event(event: ViewerEvent) -> bool:
        raw = event.raw if isinstance(event.raw, dict) else {}
        event_type = str(raw.get("event_type") or "").strip().casefold()
        return event_type in {"gift", "guard", "sc", "super_chat"}

    @staticmethod
    def _is_live_danmaku_with_text(event: ViewerEvent) -> bool:
        return is_live_danmaku_with_text(event)

    @staticmethod
    def _is_transient_event(event: ViewerEvent) -> bool:
        return is_transient_event(event)

    def _route_for_event(
        self,
        event: ViewerEvent,
        *,
        is_transient_event: bool,
        has_uid_lock: bool,
        already_roasted: bool,
    ) -> PipelineRoute:
        active_hook_answer = is_active_hook_answer_event(
            getattr(self.ctx, "recent_results", None),
            event,
        )
        if active_hook_answer and isinstance(event.raw, dict):
            event.raw["danmaku_context_hint"] = "active_hook_answer"
        route = route_for_event(
            event,
            is_transient_event_result=is_transient_event,
            has_uid_lock=has_uid_lock,
            already_roasted=already_roasted,
            entrance_pacing_active=self._entrance_pacing_active(),
            active_hook_answer=active_hook_answer,
            avatar_roast_allowed=self._avatar_roast_allowed(event),
            avatar_roast_burst_active=self._live_speaker_burst_active(),
            avatar_roast_batch_welcome=self._batch_welcome_available(),
        )
        if route.viewer_gate_reason == "batch_welcome":
            self._mark_batch_welcome_event(event)
        return route

    async def handle_event(self, event: ViewerEvent) -> InteractionResult:
        steps: list[PipelineStep] = []
        ensure_trace_id(event)
        record_timeline(
            self.ctx,
            event,
            stage="pipeline.received",
            status="ok",
            route=event.source,
        )
        if not event.uid:
            record_timeline(
                self.ctx,
                event,
                stage="pipeline.uid",
                status="skipped",
                reason="missing_uid",
            )
            return reject_missing_uid(self.ctx, event, steps)

        allowed, reason = self.ctx.permission_gate.allows_source(event.source)
        if not allowed:
            record_timeline(
                self.ctx,
                event,
                stage="permission_gate",
                status="skipped",
                reason=reason,
            )
            return skip_permission(self.ctx, event, steps, reason)
        steps.append(PipelineStep("permission_gate", "ok"))
        record_timeline(self.ctx, event, stage="permission_gate", status="ok")

        live_status_decision = self._live_status_gate_decision(event)
        if live_status_decision is not None:
            record_timeline(
                self.ctx,
                event,
                stage="live_status_gate",
                status="skipped",
                reason=live_status_decision.reason,
            )
            return skip_before_event(
                self.ctx,
                event,
                steps,
                live_status_decision,
                step_id="live_status_gate",
            )

        decision = self.ctx.safety_guard.before_event(event)
        if not decision.allowed:
            record_timeline(
                self.ctx,
                event,
                stage="safety_guard.before_event",
                status="skipped",
                reason=decision.status,
            )
            return skip_before_event(self.ctx, event, steps, decision)
        steps.append(PipelineStep("safety_guard.before_event", "ok", decision.status))
        record_timeline(
            self.ctx,
            event,
            stage="safety_guard.before_event",
            status="ok",
            reason=decision.status,
        )

        return await pipeline_flow.run_event_flow(self, event, steps)
