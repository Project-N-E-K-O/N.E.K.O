"""Idle/warmup hosting director for NEKO Live solo stream."""

from __future__ import annotations

from typing import Any

from .contracts import InteractionResult, ViewerEvent
from . import (
    live_hosting_beats,
    live_hosting_events,
    live_hosting_gates,
    live_hosting_loop,
)


class LiveHostingDirector:
    """Coordinates warmup/idle hosting actions and compatibility helpers."""

    def __init__(self, runtime: Any) -> None:
        self.runtime = runtime

    async def trigger_idle_hosting(self, *, automatic: bool = False) -> InteractionResult:
        live_state = live_hosting_gates.hosting_live_state(self.runtime)
        event = live_hosting_events.idle_hosting_event(
            self.runtime,
            live_state,
            {},
            automatic=automatic,
        )
        reason = (
            "idle_hosting.disabled"
            if not bool(getattr(self.runtime.config, "idle_hosting_enabled", True))
            else live_hosting_gates.idle_hosting_skip_reason(
            self.runtime.config.live_mode,
            live_state,
            )
        )
        if not reason:
            reason = live_hosting_gates.idle_hosting_material_skip_reason(
                self.runtime,
                automatic=automatic,
            )
        if reason:
            return self.record_idle_hosting_skip(event, reason)
        host_beat = self.next_idle_hosting_beat()
        if not host_beat:
            return self.record_idle_hosting_skip(event, "idle_hosting.no_material")
        event = live_hosting_events.idle_hosting_event(
            self.runtime,
            live_state,
            host_beat,
            automatic=automatic,
        )
        return await self.runtime.pipeline.handle_event(event)

    async def maybe_trigger_idle_hosting(self) -> InteractionResult | None:
        if not bool(getattr(self.runtime.config, "idle_hosting_enabled", True)):
            return None
        now = float(self.runtime._idle_hosting_now())
        if not live_hosting_gates.idle_hosting_auto_ready(
            consecutive_failures=int(self.runtime._idle_hosting_consecutive_failures),
            failure_limit=int(self.runtime._IDLE_HOSTING_FAILURE_LIMIT),
            now=now,
            last_attempt_at=float(self.runtime._idle_hosting_last_attempt_at),
            min_interval_seconds=self.runtime._idle_hosting_min_interval_seconds(),
        ):
            return None
        live_state = live_hosting_gates.hosting_live_state(self.runtime)
        idle_status = self.runtime.idle_hosting_status(live_state)
        if not bool(idle_status.get("eligible")):
            return None

        self.runtime._idle_hosting_last_attempt_at = now
        result = await self.trigger_idle_hosting(automatic=True)
        if result.status == "failed":
            self.runtime._idle_hosting_consecutive_failures += 1
            if (
                self.runtime._idle_hosting_consecutive_failures
                >= self.runtime._IDLE_HOSTING_FAILURE_LIMIT
            ):
                self.runtime.audit.record(
                    "idle_hosting_auto_disabled",
                    "idle hosting disabled after repeated failures",
                    level="warning",
                )
        elif result.status in {"dry_run", "pushed"}:
            self.runtime._idle_hosting_consecutive_failures = 0
        return result

    def idle_hosting_event(self, live_state: dict[str, Any]) -> ViewerEvent:
        return live_hosting_events.idle_hosting_event(
            self.runtime,
            live_state,
            self.next_idle_hosting_beat(),
        )

    def next_idle_hosting_beat(self) -> dict[str, Any]:
        return live_hosting_beats.next_idle_hosting_beat(self.runtime)

    @staticmethod
    def idle_hosting_beat_candidates() -> list[dict[str, Any]]:
        return live_hosting_beats.idle_hosting_beat_candidates()

    def idle_hosting_preferred_stage(self) -> str:
        return live_hosting_beats.idle_hosting_preferred_stage(
            self.runtime._recent_actual_route_streak_since_viewer_activity(
                "idle_hosting"
            )
        )

    def idle_hosting_stage_ordered_candidates(
        self,
        candidates: list[dict[str, Any]],
        preferred_stage: str,
    ) -> list[dict[str, Any]]:
        return live_hosting_beats.idle_hosting_stage_ordered_candidates(
            candidates,
            preferred_stage=preferred_stage,
            start_index=self.runtime._idle_hosting_beat_index,
        )

    @staticmethod
    def idle_hosting_material_stage(material: dict[str, Any] | None) -> str:
        return live_hosting_beats.idle_hosting_material_stage(material)

    def is_similar_idle_hosting_beat_title(self, title: str) -> bool:
        return live_hosting_beats.is_similar_idle_hosting_beat_title(
            title,
            self.runtime._idle_hosting_recent_beat_titles,
        )

    def record_idle_hosting_skip(
        self, event: ViewerEvent, reason: str
    ) -> InteractionResult:
        return live_hosting_events.record_idle_hosting_skip(
            self.runtime,
            event,
            reason,
        )

    async def trigger_warmup_hosting(self) -> InteractionResult:
        live_state = live_hosting_gates.hosting_live_state(self.runtime)
        event = self.warmup_hosting_event(live_state)
        reason = (
            "warmup_hosting.disabled"
            if not bool(getattr(self.runtime.config, "warmup_hosting_enabled", True))
            else live_hosting_gates.warmup_hosting_skip_reason(
            self.runtime.config.live_mode,
            live_state,
            )
        )
        if reason:
            return self.record_warmup_hosting_skip(event, reason)
        return await self.runtime.pipeline.handle_event(event)

    async def maybe_trigger_warmup_hosting(self) -> InteractionResult | None:
        if not bool(getattr(self.runtime.config, "warmup_hosting_enabled", True)):
            return None
        live_state = live_hosting_gates.hosting_live_state(self.runtime)
        if not live_hosting_gates.warmup_hosting_candidate(live_state):
            return None
        return await self.trigger_warmup_hosting()

    def warmup_hosting_event(self, live_state: dict[str, Any]) -> ViewerEvent:
        return live_hosting_events.warmup_hosting_event(self.runtime, live_state)

    def record_warmup_hosting_skip(
        self, event: ViewerEvent, reason: str
    ) -> InteractionResult:
        return live_hosting_events.record_warmup_hosting_skip(
            self.runtime,
            event,
            reason,
        )

    def start_loop(self) -> None:
        live_hosting_loop.start_idle_hosting_loop(self)

    async def stop_loop(self) -> None:
        await live_hosting_loop.stop_idle_hosting_loop(self.runtime)

    async def idle_hosting_loop(self) -> None:
        await live_hosting_loop.idle_hosting_loop(self)
