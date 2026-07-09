"""Runtime compatibility API for idle and warmup hosting helpers."""

from __future__ import annotations

from typing import Any

from .contracts import InteractionResult, ViewerEvent
from .live_hosting_director import LiveHostingDirector


class RuntimeHostingApiMixin:
    async def trigger_idle_hosting(self) -> InteractionResult:
        return await self.live_hosting_director.trigger_idle_hosting()

    async def maybe_trigger_idle_hosting(self) -> InteractionResult | None:
        return await self.live_hosting_director.maybe_trigger_idle_hosting()

    def _idle_hosting_event(self, live_state: dict[str, Any]) -> ViewerEvent:
        return self.live_hosting_director.idle_hosting_event(live_state)

    def _next_idle_hosting_beat(self) -> dict[str, Any]:
        return self.live_hosting_director.next_idle_hosting_beat()

    @staticmethod
    def _idle_hosting_beat_candidates() -> list[dict[str, Any]]:
        return LiveHostingDirector.idle_hosting_beat_candidates()

    def _idle_hosting_preferred_stage(self) -> str:
        return self.live_hosting_director.idle_hosting_preferred_stage()

    def _idle_hosting_stage_ordered_candidates(
        self,
        candidates: list[dict[str, Any]],
        preferred_stage: str,
    ) -> list[dict[str, Any]]:
        return self.live_hosting_director.idle_hosting_stage_ordered_candidates(candidates, preferred_stage)

    @staticmethod
    def _idle_hosting_material_stage(material: dict[str, Any] | None) -> str:
        return LiveHostingDirector.idle_hosting_material_stage(material)

    def _is_similar_idle_hosting_beat_title(self, title: str) -> bool:
        return self.live_hosting_director.is_similar_idle_hosting_beat_title(title)

    def _record_idle_hosting_skip(self, event: ViewerEvent, reason: str) -> InteractionResult:
        return self.live_hosting_director.record_idle_hosting_skip(event, reason)

    async def trigger_warmup_hosting(self) -> InteractionResult:
        return await self.live_hosting_director.trigger_warmup_hosting()

    async def maybe_trigger_warmup_hosting(self) -> InteractionResult | None:
        return await self.live_hosting_director.maybe_trigger_warmup_hosting()

    def _warmup_hosting_event(self, live_state: dict[str, Any]) -> ViewerEvent:
        return self.live_hosting_director.warmup_hosting_event(live_state)

    def _record_warmup_hosting_skip(self, event: ViewerEvent, reason: str) -> InteractionResult:
        return self.live_hosting_director.record_warmup_hosting_skip(event, reason)

    def _start_idle_hosting_loop(self) -> None:
        self.live_hosting_director.start_loop()

    async def _stop_idle_hosting_loop(self) -> None:
        await self.live_hosting_director.stop_loop()

    async def _idle_hosting_loop(self) -> None:
        await self.live_hosting_director.idle_hosting_loop()
