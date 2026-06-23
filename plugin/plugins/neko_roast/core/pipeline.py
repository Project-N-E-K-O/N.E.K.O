"""Unified live/sandbox pipeline."""

from __future__ import annotations

import asyncio
from typing import Any

from .contracts import InteractionResult, PipelineStep, ViewerEvent, ViewerProfile


class RoastPipeline:
    def __init__(self, ctx: Any) -> None:
        self.ctx = ctx
        self._uid_locks: dict[str, asyncio.Lock] = {}

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

            is_transient_event = event.source in {"developer_sandbox", "idle_hosting", "active_engagement", "warmup_hosting"}
            if is_transient_event:
                profile = ViewerProfile(uid=identity.uid, nickname=identity.nickname, avatar_url=identity.avatar_url)
                steps.append(PipelineStep("viewer_profile", "skipped", f"{event.source} uses transient profile"))
            else:
                profile = await self.ctx.viewer_profile.upsert(identity)
                steps.append(PipelineStep("viewer_profile", "ok"))

            uid_lock: asyncio.Lock | None = None
            if self.ctx.config.roast_once_per_uid and not is_transient_event:
                uid_lock = self._uid_locks.setdefault(identity.uid, asyncio.Lock())
                await uid_lock.acquire()
            try:
                already_roasted = False
                if uid_lock is not None:
                    already_roasted = await self.ctx.viewer_profile.has_roasted(identity.uid)
                is_repeat_live_danmaku = (
                    uid_lock is not None
                    and event.source == "live_danmaku"
                    and bool((event.danmaku_text or "").strip())
                    and already_roasted
                )
                if uid_lock is not None and already_roasted and not is_repeat_live_danmaku:
                    reason = "uid already roasted"
                    steps.append(PipelineStep("viewer_gate", "skipped", reason))
                    result = InteractionResult(False, "skipped", event, identity=identity, profile=profile, reason=reason, steps=steps)
                    self.ctx.audit.record("pipeline_skipped", reason, level="info", detail={"uid": identity.uid})
                    return result

                if event.source == "warmup_hosting":
                    steps.append(PipelineStep("viewer_gate", "ok", "warmup_hosting"))
                    request = self.ctx.warmup_hosting.build_request(event, identity, profile)
                    should_mark_roasted = False
                    response_module_id = "warmup_hosting"
                elif event.source == "active_engagement":
                    steps.append(PipelineStep("viewer_gate", "ok", "active_engagement"))
                    request = self.ctx.active_engagement.build_request(event, identity, profile)
                    should_mark_roasted = False
                    response_module_id = "active_engagement"
                elif is_repeat_live_danmaku:
                    steps.append(PipelineStep("viewer_gate", "ok", "repeat_danmaku"))
                    request = self.ctx.danmaku_response.build_request(event, identity, profile)
                    should_mark_roasted = False
                    response_module_id = "danmaku_response"
                else:
                    steps.append(PipelineStep("viewer_gate", "ok"))
                    request = self.ctx.avatar_roast.build_request(event, identity, profile)
                    should_mark_roasted = not is_transient_event
                    response_module_id = "avatar_roast"
                steps.append(PipelineStep(response_module_id, "ok"))

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
                if should_mark_roasted:
                    try:
                        await self.ctx.viewer_profile.mark_roasted(identity.uid, output)
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
