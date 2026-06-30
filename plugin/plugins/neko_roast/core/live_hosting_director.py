"""Idle/warmup hosting director for NEKO Live solo stream."""

from __future__ import annotations

import asyncio
from typing import Any

from . import active_topic_rules
from .contracts import InteractionResult, PipelineStep, ViewerEvent
from .live_content import idle_hosting_beat_candidates


class LiveHostingDirector:
    """Owns warmup/idle hosting gates, beat rotation, and the auto loop."""

    def __init__(self, runtime: Any) -> None:
        self.runtime = runtime

    async def trigger_idle_hosting(self) -> InteractionResult:
        live_connection = self.runtime.live_connection_snapshot()
        live_status = self.runtime.live_status_summary(live_connection)
        health_rows = self.runtime.runtime_health_rows()
        live_state = self.runtime.live_state_summary(live_status, health_rows)
        event = self.idle_hosting_event(live_state)

        if self.runtime.config.live_mode != "solo_stream":
            return self.record_idle_hosting_skip(event, "idle_hosting.not_solo_stream")
        state = str(live_state.get("state") or "")
        if state == "paused":
            return self.record_idle_hosting_skip(event, "idle_hosting.paused")
        if state == "blocked":
            return self.record_idle_hosting_skip(event, "idle_hosting.blocked")
        if state != "idle":
            return self.record_idle_hosting_skip(event, "idle_hosting.not_idle")
        if not bool(live_state.get("idle_hosting_candidate")):
            return self.record_idle_hosting_skip(event, "idle_hosting.not_candidate")
        return await self.runtime.pipeline.handle_event(event)

    async def maybe_trigger_idle_hosting(self) -> InteractionResult | None:
        if self.runtime._idle_hosting_consecutive_failures >= self.runtime._IDLE_HOSTING_FAILURE_LIMIT:
            return None
        now = float(self.runtime._idle_hosting_now())
        if now - self.runtime._idle_hosting_last_attempt_at < self.runtime._idle_hosting_min_interval_seconds():
            return None
        live_connection = self.runtime.live_connection_snapshot()
        live_status = self.runtime.live_status_summary(live_connection)
        health_rows = self.runtime.runtime_health_rows()
        live_state = self.runtime.live_state_summary(live_status, health_rows)
        if not bool(live_state.get("idle_hosting_candidate")):
            return None

        self.runtime._idle_hosting_last_attempt_at = now
        result = await self.trigger_idle_hosting()
        if result.status == "failed":
            self.runtime._idle_hosting_consecutive_failures += 1
            if self.runtime._idle_hosting_consecutive_failures >= self.runtime._IDLE_HOSTING_FAILURE_LIMIT:
                self.runtime.audit.record(
                    "idle_hosting_auto_disabled",
                    "idle hosting disabled after repeated failures",
                    level="warning",
                )
        elif result.status in {"dry_run", "pushed"}:
            self.runtime._idle_hosting_consecutive_failures = 0
        return result

    def idle_hosting_event(self, live_state: dict[str, Any]) -> ViewerEvent:
        host_beat = self.next_idle_hosting_beat()
        return ViewerEvent(
            uid="__neko_idle__",
            nickname="NEKO",
            danmaku_text="",
            source="idle_hosting",
            live_mode=self.runtime.config.live_mode,
            raw={
                "trigger": "manual_idle_hosting",
                "live_state": dict(live_state),
                "host_beat": host_beat,
            },
        )

    def next_idle_hosting_beat(self) -> dict[str, Any]:
        candidates = self.runtime._idle_hosting_beat_candidates()
        fallback = candidates[0]
        chosen = fallback
        chosen_offset: int | None = None
        preferred_stage = self.runtime._idle_hosting_preferred_stage()
        ordered_candidates = self.runtime._idle_hosting_stage_ordered_candidates(candidates, preferred_stage)
        recent_spent_families = self.runtime._recent_spent_output_families()
        for offset, candidate in enumerate(ordered_candidates):
            key = str(candidate.get("key") or "").strip()
            axis = str(candidate.get("fun_axis") or "").strip()
            title = str(candidate.get("title") or "").strip()
            family = self.runtime._host_material_family(candidate)
            reply_affordance = str(candidate.get("reply_affordance") or "").strip()
            if (
                key
                and key not in self.runtime._idle_hosting_recent_beat_keys
                and axis
                and axis not in self.runtime._idle_hosting_recent_beat_axes
                and family
                and family not in self.runtime._recent_host_material_families
                and family not in recent_spent_families
                and (
                    not reply_affordance
                    or reply_affordance not in self.runtime._idle_hosting_recent_reply_affordances
                )
                and not self.runtime._is_similar_idle_hosting_beat_title(title)
            ):
                chosen = candidate
                chosen_offset = offset
                break
        else:
            for offset, candidate in enumerate(ordered_candidates):
                key = str(candidate.get("key") or "").strip()
                title = str(candidate.get("title") or "").strip()
                family = self.runtime._host_material_family(candidate)
                if (
                    key
                    and key not in self.runtime._idle_hosting_recent_beat_keys
                    and family
                    and family not in self.runtime._recent_host_material_families
                    and family not in recent_spent_families
                    and not self.runtime._is_similar_idle_hosting_beat_title(title)
                ):
                    chosen = candidate
                    chosen_offset = offset
                    break
            else:
                for offset, candidate in enumerate(ordered_candidates):
                    key = str(candidate.get("key") or "").strip()
                    if key and key not in self.runtime._idle_hosting_recent_beat_keys:
                        chosen = candidate
                        chosen_offset = offset
                        break
        if chosen_offset is None:
            self.runtime._idle_hosting_beat_index = (self.runtime._idle_hosting_beat_index + 1) % len(candidates)
        else:
            self.runtime._idle_hosting_beat_index = (self.runtime._idle_hosting_beat_index + chosen_offset + 1) % len(candidates)
        key = str(chosen.get("key") or fallback["key"]).strip()
        axis = str(chosen.get("fun_axis") or "").strip()
        title = str(chosen.get("title") or "").strip()
        family = self.runtime._host_material_family(chosen)
        reply_affordance = str(chosen.get("reply_affordance") or "").strip()
        self.runtime._idle_hosting_recent_beat_keys.append(key)
        if axis:
            self.runtime._idle_hosting_recent_beat_axes.append(axis)
        if title:
            self.runtime._idle_hosting_recent_beat_titles.append(title)
        if family:
            self.runtime._recent_host_material_families.append(family)
        if reply_affordance:
            self.runtime._idle_hosting_recent_reply_affordances.append(reply_affordance)
        payload = dict(chosen)
        if family:
            payload["family"] = family
        payload["idle_stage"] = self.idle_hosting_material_stage(payload)
        return payload

    @staticmethod
    def idle_hosting_beat_candidates() -> list[dict[str, Any]]:
        candidates = [
            candidate
            for candidate in idle_hosting_beat_candidates()
            if active_topic_rules._is_clean_live_material(candidate)
        ]
        return candidates or idle_hosting_beat_candidates()[:1]

    def idle_hosting_preferred_stage(self) -> str:
        streak = self.runtime._recent_actual_route_streak_since_viewer_activity("idle_hosting")
        if streak <= 0:
            return "settle"
        if streak == 1:
            return "column"
        return "callback"

    def idle_hosting_stage_ordered_candidates(
        self,
        candidates: list[dict[str, Any]],
        preferred_stage: str,
    ) -> list[dict[str, Any]]:
        if not candidates:
            return []
        rotated = [
            candidates[(self.runtime._idle_hosting_beat_index + offset) % len(candidates)]
            for offset in range(len(candidates))
        ]
        preferred = [candidate for candidate in rotated if self.idle_hosting_material_stage(candidate) == preferred_stage]
        rest = [candidate for candidate in rotated if candidate not in preferred]
        return [*preferred, *rest]

    @staticmethod
    def idle_hosting_material_stage(material: dict[str, Any] | None) -> str:
        if not isinstance(material, dict):
            return "settle"
        explicit = str(material.get("idle_stage") or "").strip()
        if explicit:
            return explicit
        shape = str(material.get("shape") or "").strip()
        axis = str(material.get("fun_axis") or "").strip()
        if shape in {"one_word_call", "micro_challenge"} or axis in {"viewer_callback", "micro_challenge"}:
            return "callback"
        if shape in {"tiny_choice", "light_tease"} or axis in {"choice", "tease"}:
            return "column"
        return "settle"

    def is_similar_idle_hosting_beat_title(self, title: str) -> bool:
        return bool(title) and active_topic_rules._is_similar_active_topic_title(
            title,
            self.runtime._idle_hosting_recent_beat_titles,
        )

    def record_idle_hosting_skip(self, event: ViewerEvent, reason: str) -> InteractionResult:
        result = InteractionResult(
            accepted=False,
            status="skipped",
            event=event,
            reason=reason,
            steps=[PipelineStep("idle_hosting_gate", "skipped", reason)],
        )
        self.runtime.audit.record("idle_hosting_skipped", reason, level="info", detail={"mode": self.runtime.config.live_mode})
        self.runtime.record_result(result)
        return result

    async def trigger_warmup_hosting(self) -> InteractionResult:
        live_connection = self.runtime.live_connection_snapshot()
        live_status = self.runtime.live_status_summary(live_connection)
        health_rows = self.runtime.runtime_health_rows()
        live_state = self.runtime.live_state_summary(live_status, health_rows)
        event = self.warmup_hosting_event(live_state)

        if self.runtime.config.live_mode != "solo_stream":
            return self.record_warmup_hosting_skip(event, "warmup_hosting.not_solo_stream")
        state = str(live_state.get("state") or "")
        if state == "paused":
            return self.record_warmup_hosting_skip(event, "warmup_hosting.paused")
        if state == "blocked":
            return self.record_warmup_hosting_skip(event, "warmup_hosting.blocked")
        if state != "warmup":
            return self.record_warmup_hosting_skip(event, "warmup_hosting.not_warmup")
        if not bool(live_state.get("warmup_hosting_candidate")):
            return self.record_warmup_hosting_skip(event, "warmup_hosting.not_candidate")
        return await self.runtime.pipeline.handle_event(event)

    async def maybe_trigger_warmup_hosting(self) -> InteractionResult | None:
        now = float(self.runtime._idle_hosting_now())
        if now - self.runtime._warmup_hosting_last_attempt_at < self.runtime._idle_hosting_min_interval_seconds():
            return None
        live_connection = self.runtime.live_connection_snapshot()
        live_status = self.runtime.live_status_summary(live_connection)
        health_rows = self.runtime.runtime_health_rows()
        live_state = self.runtime.live_state_summary(live_status, health_rows)
        if not bool(live_state.get("warmup_hosting_candidate")):
            return None
        self.runtime._warmup_hosting_last_attempt_at = now
        return await self.trigger_warmup_hosting()

    def warmup_hosting_event(self, live_state: dict[str, Any]) -> ViewerEvent:
        return ViewerEvent(
            uid="__neko_warmup__",
            nickname="NEKO",
            danmaku_text="",
            source="warmup_hosting",
            live_mode=self.runtime.config.live_mode,
            raw={
                "trigger": "auto_warmup_hosting",
                "live_state": dict(live_state),
            },
        )

    def record_warmup_hosting_skip(self, event: ViewerEvent, reason: str) -> InteractionResult:
        result = InteractionResult(
            accepted=False,
            status="skipped",
            event=event,
            reason=reason,
            steps=[PipelineStep("warmup_hosting_gate", "skipped", reason)],
        )
        self.runtime.audit.record("warmup_hosting_skipped", reason, level="info", detail={"mode": self.runtime.config.live_mode})
        self.runtime.record_result(result)
        return result

    def start_loop(self) -> None:
        task = self.runtime._idle_hosting_task
        if task is not None and not task.done():
            return
        self.runtime._idle_hosting_task = asyncio.create_task(self.idle_hosting_loop())

    async def stop_loop(self) -> None:
        task = self.runtime._idle_hosting_task
        self.runtime._idle_hosting_task = None
        if task is None or task.done():
            return
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    async def idle_hosting_loop(self) -> None:
        while True:
            await self.runtime._idle_hosting_sleep(self.runtime._IDLE_HOSTING_CHECK_INTERVAL_SECONDS)
            try:
                await self.maybe_trigger_warmup_hosting()
                await self.runtime.maybe_trigger_active_engagement()
                await self.maybe_trigger_idle_hosting()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                message = f"idle_hosting_loop_failed: {type(exc).__name__}"
                self.runtime.audit.record("idle_hosting_loop_failed", message, level="warning")
