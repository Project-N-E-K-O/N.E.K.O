"""Unified live/sandbox pipeline."""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from typing import Any

from .contracts import InteractionRequest, InteractionResult, PipelineStep, ViewerEvent, ViewerIdentity, ViewerProfile

ENTRANCE_ROAST_MIN_INTERVAL_SECONDS = 45.0
ENTRANCE_ROAST_INTERVAL_BY_ACTIVITY = {
    "quiet": 75.0,
    "standard": ENTRANCE_ROAST_MIN_INTERVAL_SECONDS,
    "active": 30.0,
}


@dataclass(frozen=True)
class PipelineRoute:
    response_module_id: str
    viewer_gate_reason: str
    should_mark_roasted: bool


class RoastPipeline:
    def __init__(self, ctx: Any) -> None:
        self.ctx = ctx
        self._uid_locks: dict[str, asyncio.Lock] = {}
        self._dry_run_roasted_uids: set[str] = set()
        self._session_roasted_uids: set[str] = set()
        self._last_avatar_roast_at: float | None = None

    def clear_dry_run_session_state(self) -> None:
        self._dry_run_roasted_uids.clear()
        self._session_roasted_uids.clear()
        self._last_avatar_roast_at = None

    def _now(self) -> float:
        return time.monotonic()

    def _entrance_pacing_interval_seconds(self) -> float:
        config = getattr(self.ctx, "config", None)
        level = str(getattr(config, "activity_level", "standard") or "standard")
        return ENTRANCE_ROAST_INTERVAL_BY_ACTIVITY.get(level, ENTRANCE_ROAST_MIN_INTERVAL_SECONDS)

    def _entrance_pacing_active(self) -> bool:
        if self._last_avatar_roast_at is None:
            return False
        return (self._now() - self._last_avatar_roast_at) < self._entrance_pacing_interval_seconds()

    def _record_avatar_roast_sent(self) -> None:
        self._last_avatar_roast_at = self._now()

    @staticmethod
    def _is_live_danmaku_with_text(event: ViewerEvent) -> bool:
        return event.source in {"live_danmaku", "manual_live_simulation"} and bool((event.danmaku_text or "").strip())

    @staticmethod
    def _is_transient_event(event: ViewerEvent) -> bool:
        return event.source in {"developer_sandbox", "idle_hosting", "active_engagement", "warmup_hosting"}

    @staticmethod
    def _is_repeat_live_danmaku(event: ViewerEvent, *, has_uid_lock: bool, already_roasted: bool) -> bool:
        return has_uid_lock and RoastPipeline._is_live_danmaku_with_text(event) and already_roasted

    def _is_entrance_paced_live_danmaku(
        self,
        event: ViewerEvent,
        *,
        has_uid_lock: bool,
        already_roasted: bool,
    ) -> bool:
        return (
            has_uid_lock
            and self._is_live_danmaku_with_text(event)
            and event.live_mode == "solo_stream"
            and not already_roasted
            and self._entrance_pacing_active()
        )

    def _route_for_event(
        self,
        event: ViewerEvent,
        *,
        is_transient_event: bool,
        has_uid_lock: bool,
        already_roasted: bool,
    ) -> PipelineRoute:
        if event.source == "warmup_hosting":
            return PipelineRoute("warmup_hosting", "warmup_hosting", False)
        if event.source == "active_engagement":
            return PipelineRoute("active_engagement", "active_engagement", False)
        if event.source == "idle_hosting":
            return PipelineRoute("idle_hosting", "idle_hosting", False)
        if self._is_repeat_live_danmaku(event, has_uid_lock=has_uid_lock, already_roasted=already_roasted):
            return PipelineRoute("danmaku_response", "repeat_danmaku", False)
        if self._is_entrance_paced_live_danmaku(event, has_uid_lock=has_uid_lock, already_roasted=already_roasted):
            return PipelineRoute("danmaku_response", "entrance_pacing", True)
        return PipelineRoute("avatar_roast", "", not is_transient_event)

    def _build_request_for_route(
        self,
        route: PipelineRoute,
        event: ViewerEvent,
        identity: ViewerIdentity,
        profile: ViewerProfile,
    ) -> InteractionRequest:
        if route.response_module_id == "warmup_hosting":
            return self.ctx.warmup_hosting.build_request(event, identity, profile)
        if route.response_module_id == "active_engagement":
            return self.ctx.active_engagement.build_request(event, identity, profile)
        if route.response_module_id == "danmaku_response":
            return self.ctx.danmaku_response.build_request(event, identity, profile)
        return self.ctx.avatar_roast.build_request(event, identity, profile)

    async def handle_event(self, event: ViewerEvent) -> InteractionResult:
        steps: list[PipelineStep] = []
        if not event.uid:
            steps.append(PipelineStep("input", "failed", "uid is required"))
            result = InteractionResult(False, "failed", event, reason="uid is required", steps=steps)
            self.ctx.audit.record("pipeline_rejected", "uid is required", level="warning")
            return result

        allowed, reason = self.ctx.permission_gate.allows_source(event.source)
        if not allowed:
            steps.append(PipelineStep("permission_gate", "skipped", reason))
            result = InteractionResult(False, "skipped", event, reason=reason, steps=steps)
            self.ctx.audit.record("pipeline_skipped", reason, level="info", detail={"source": event.source})
            return result
        steps.append(PipelineStep("permission_gate", "ok"))

        decision = self.ctx.safety_guard.before_event(event)
        if not decision.allowed:
            steps.append(PipelineStep("safety_guard.before_event", "skipped", decision.reason))
            result = InteractionResult(False, "skipped", event, reason=decision.reason, steps=steps)
            self.ctx.audit.record("pipeline_safety_skip", decision.reason, level="warning", detail={"status": decision.status})
            return result
        steps.append(PipelineStep("safety_guard.before_event", "ok", decision.status))

        try:
            identity = await self.ctx.bili_identity.resolve(event)
            steps.append(PipelineStep("bili_identity", "ok" if not identity.error else "failed", identity.error))

            is_transient_event = self._is_transient_event(event)
            if is_transient_event:
                profile = ViewerProfile(uid=identity.uid, nickname=identity.nickname, avatar_url=identity.avatar_url)
                steps.append(PipelineStep("viewer_profile", "skipped", f"{event.source} uses transient profile"))
            else:
                profile = await self.ctx.viewer_profile.upsert(identity)
                steps.append(PipelineStep("viewer_profile", "ok"))

            uid_lock: asyncio.Lock | None = None
            needs_session_gate = self._is_live_danmaku_with_text(event)
            if (self.ctx.config.roast_once_per_uid or needs_session_gate) and not is_transient_event:
                uid_lock = self._uid_locks.setdefault(identity.uid, asyncio.Lock())
                await uid_lock.acquire()
            try:
                already_roasted = False
                if uid_lock is not None:
                    already_roasted = identity.uid in self._session_roasted_uids
                    if self.ctx.config.roast_once_per_uid and not already_roasted:
                        already_roasted = await self.ctx.viewer_profile.has_roasted(identity.uid)
                    if not already_roasted:
                        already_roasted = identity.uid in self._session_roasted_uids
                    if not already_roasted and self.ctx.config.dry_run and event.source == "live_danmaku":
                        already_roasted = identity.uid in self._dry_run_roasted_uids
                has_uid_lock = uid_lock is not None
                route = self._route_for_event(
                    event,
                    is_transient_event=is_transient_event,
                    has_uid_lock=has_uid_lock,
                    already_roasted=already_roasted,
                )
                if uid_lock is not None and already_roasted and route.response_module_id != "danmaku_response":
                    reason = "uid already roasted"
                    steps.append(PipelineStep("viewer_gate", "skipped", reason))
                    result = InteractionResult(False, "skipped", event, identity=identity, profile=profile, reason=reason, steps=steps)
                    self.ctx.audit.record("pipeline_skipped", reason, level="info", detail={"uid": identity.uid})
                    return result

                if route.viewer_gate_reason:
                    steps.append(PipelineStep("viewer_gate", "ok", route.viewer_gate_reason))
                else:
                    steps.append(PipelineStep("viewer_gate", "ok"))
                request = self._build_request_for_route(route, event, identity, profile)
                should_mark_roasted = route.should_mark_roasted
                response_module_id = route.response_module_id
                steps.append(PipelineStep(response_module_id, "ok"))
                if should_mark_roasted and uid_lock is not None:
                    self._session_roasted_uids.add(identity.uid)
                    steps.append(PipelineStep("viewer_gate.session_claim", "ok", response_module_id))

                output_decision = self.ctx.safety_guard.before_output(event)
                if not output_decision.allowed:
                    steps.append(PipelineStep("safety_guard.before_output", "skipped", output_decision.reason))
                    result = InteractionResult(False, "skipped", event, identity=identity, profile=profile, request=request, reason=output_decision.reason, steps=steps)
                    self.ctx.audit.record("pipeline_output_skipped", output_decision.reason, level="warning", detail={"uid": identity.uid})
                    return result
                steps.append(PipelineStep("safety_guard.before_output", "ok", output_decision.status))

                try:
                    output = await self.ctx.dispatcher.push_roast(request)
                except Exception as exc:
                    raw_message = str(exc).strip()
                    message = raw_message or f"output_failed: {type(exc).__name__}"
                    self.ctx.safety_guard.record_failure("output", message)
                    steps.append(PipelineStep("neko_dispatcher", "failed", message))
                    result = InteractionResult(False, "failed", event, identity=identity, profile=profile, request=request, reason=message, steps=steps)
                    self.ctx.record_result(result)
                    return result

                if request.dry_run:
                    steps.append(PipelineStep("neko_dispatcher", "dry_run", output))
                    if should_mark_roasted:
                        self._dry_run_roasted_uids.add(identity.uid)
                    result = InteractionResult(
                        False,
                        "dry_run",
                        event,
                        identity=identity,
                        profile=profile,
                        request=request,
                        output=output,
                        reason="dispatcher.dry_run",
                        steps=steps,
                    )
                    self.ctx.audit.record("dispatcher_dry_run", "roast request completed as dry_run", detail={"uid": identity.uid, "source": event.source})
                    self.ctx.record_result(result)
                    if response_module_id == "avatar_roast":
                        self._record_avatar_roast_sent()
                    return result

                if not request.should_push or str(output).startswith("skipped_to_neko"):
                    reason = request.reason or "dispatcher.skipped"
                    steps.append(PipelineStep("neko_dispatcher", "skipped", output or reason))
                    result = InteractionResult(
                        False,
                        "skipped",
                        event,
                        identity=identity,
                        profile=profile,
                        request=request,
                        output=output,
                        reason=reason,
                        steps=steps,
                    )
                    self.ctx.audit.record("dispatcher_skipped", reason, level="info", detail={"uid": identity.uid, "source": event.source})
                    self.ctx.record_result(result)
                    return result

                steps.append(PipelineStep("neko_dispatcher", "ok", output))
                if response_module_id == "avatar_roast":
                    self._record_avatar_roast_sent()
                if should_mark_roasted:
                    self._session_roasted_uids.add(identity.uid)
                    try:
                        await self.ctx.viewer_profile.mark_roasted(identity.uid, output)
                        profile.roast_count = int(profile.roast_count or 0) + 1
                        profile.last_result = output
                        steps.append(PipelineStep("viewer_profile.mark_roasted", "ok"))
                    except Exception as exc:
                        mark_message = f"mark_roasted_failed: {type(exc).__name__}"
                        steps.append(PipelineStep("viewer_profile.mark_roasted", "failed", mark_message))
                        self.ctx.audit.record(
                            "viewer_profile_mark_failed",
                            mark_message,
                            level="error",
                            detail={"uid": identity.uid},
                        )
                result = InteractionResult(True, "pushed", event, identity=identity, profile=profile, request=request, output=output, steps=steps)
                self.ctx.audit.record("pipeline_pushed", "roast request pushed", detail={"uid": identity.uid, "source": event.source})
                self.ctx.record_result(result)
                return result
            finally:
                if uid_lock is not None:
                    uid_lock.release()
        except Exception as exc:
            message = f"pipeline_failed: {type(exc).__name__}"
            self.ctx.safety_guard.record_failure("pipeline", message)
            steps.append(PipelineStep("pipeline", "failed", message))
            result = InteractionResult(False, "failed", event, reason=message, steps=steps)
            self.ctx.audit.record("pipeline_failed", message, level="error")
            self.ctx.record_result(result)
            return result
        finally:
            self.ctx.safety_guard.after_event()
