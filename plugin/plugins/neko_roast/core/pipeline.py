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

            is_sandbox_event = event.source == "developer_sandbox"
            if is_sandbox_event:
                profile = ViewerProfile(uid=identity.uid, nickname=identity.nickname, avatar_url=identity.avatar_url)
                steps.append(PipelineStep("viewer_profile", "skipped", "developer sandbox uses transient profile"))
            else:
                profile = await self.ctx.viewer_profile.upsert(identity)
                steps.append(PipelineStep("viewer_profile", "ok"))

            uid_lock: asyncio.Lock | None = None
            if self.ctx.config.roast_once_per_uid and not is_sandbox_event:
                uid_lock = self._uid_locks.setdefault(identity.uid, asyncio.Lock())
                await uid_lock.acquire()
            try:
                if uid_lock is not None and await self.ctx.viewer_profile.has_roasted(identity.uid):
                    reason = "uid already roasted"
                    steps.append(PipelineStep("viewer_gate", "skipped", reason))
                    result = InteractionResult(False, "skipped", event, identity=identity, profile=profile, reason=reason, steps=steps)
                    self.ctx.audit.record("pipeline_skipped", reason, level="info", detail={"uid": identity.uid})
                    return result
                steps.append(PipelineStep("viewer_gate", "ok"))

                request = self.ctx.avatar_roast.build_request(event, identity, profile)
                steps.append(PipelineStep("avatar_roast", "ok"))

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

                steps.append(PipelineStep("neko_dispatcher", "ok"))
                if not is_sandbox_event:
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
