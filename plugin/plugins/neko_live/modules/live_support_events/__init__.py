"""Build support-event response requests for Gift / Super Chat / Guard."""

from __future__ import annotations

import asyncio
import time
from typing import Any

from ...core.contracts import InteractionRequest, ViewerEvent, ViewerIdentity, ViewerProfile
from ...core.runtime_timeline import record_payload_timeline
from ...core.viewer_preferences import safe_int, safe_text
from .._base import BaseModule
from .._prompt_context import (
    anti_repeat_rules,
    live_events_context_block,
    live_output_quality_rules,
    recent_context_block,
    short_reply_rules,
    sustained_charm_rules,
    viewer_preference_context_block,
    viewer_session_context_block,
)
from ..live_events.provider_event import (
    event_avatar_url,
    event_nickname,
    event_room_id,
    event_room_ref,
    event_session_generation,
    event_signal_fields,
    event_support_fields,
    event_text,
    event_type,
    event_uid,
    is_signal_only,
)
from .scheduler import SupportEventScheduler


class LiveSupportEventsModule(BaseModule):
    id = "live_support_events"
    title = "Live Support Events"
    domain = "interaction"

    def __init__(self) -> None:
        super().__init__()
        self._unsubscribes: list[Any] = []
        self._tasks: set[asyncio.Task[Any]] = set()
        self._scheduler: SupportEventScheduler | None = None
        self._last_event_at: float = 0.0
        self._last_event_type: str = ""

    async def setup(self, ctx: Any) -> None:
        await super().setup(ctx)
        self._scheduler = SupportEventScheduler(
            dispatch=self._handle_payload,
            audit=getattr(ctx, "audit", None),
            queue_limit=max(8, int(getattr(ctx.config, "queue_limit", 64) or 64)),
        )
        bus = getattr(ctx, "event_bus", None)
        if bus is not None:
            for event_name in ("gift", "super_chat", "guard"):
                self._unsubscribes.append(bus.subscribe(event_name, self._on_bus_event, owner=self.id))

    async def teardown(self) -> None:
        for unsubscribe in self._unsubscribes:
            if callable(unsubscribe):
                unsubscribe()
        self._unsubscribes = []
        scheduler = self._scheduler
        self._scheduler = None
        self.reset()
        if scheduler is not None:
            await scheduler.close()
        pending = [task for task in list(self._tasks) if not task.done()]
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)
        self._tasks.clear()
        await super().teardown()

    def reset(self) -> None:
        if self._scheduler is not None:
            self._scheduler.reset()
        for task in list(self._tasks):
            if not task.done():
                task.cancel()
        self._last_event_at = 0.0
        self._last_event_type = ""

    def status(self) -> dict[str, Any]:
        scheduler_status = self._scheduler.status() if self._scheduler is not None else {}
        return {
            "enabled": self.enabled,
            "subscribed": bool(self._unsubscribes),
            "pending": scheduler_status.get("pending_count", 0),
            "active_combos": scheduler_status.get("active_combo_count", 0),
            "queue_overflow_count": scheduler_status.get("overflow_count", 0),
            "queue_dropped_count": scheduler_status.get("dropped_count", 0),
            "queue_aggregated_count": scheduler_status.get("aggregated_count", 0),
            "last_event_at": self._last_event_at,
            "last_event_type": self._last_event_type,
        }

    def _on_bus_event(self, event: Any) -> None:
        if (
            not self.enabled
            or self.ctx is None
            or not bool(getattr(self.ctx.config, "live_support_events_enabled", True))
        ):
            return
        raw = getattr(event, "raw", None)
        support_event = raw if raw is not None else event
        if is_signal_only(event):
            selected_event_type = event_type(event)
        elif is_signal_only(support_event):
            selected_event_type = event_type(support_event)
        else:
            return
        payload = self._payload_for_event(
            support_event,
            event_type_hint=selected_event_type,
            fallback_event=event,
        )
        if not payload.get("uid") or payload.get("support_verified") is not True:
            return
        self._last_event_at = time.time()
        self._last_event_type = str(payload.get("event_type") or "")
        record_payload_timeline(
            self.ctx,
            payload,
            stage="live_support_events.receive",
            status="received",
            reason=f"support {self._last_event_type}",
            route=self.id,
        )
        if self._scheduler is not None:
            self._scheduler.submit(payload)

    def _payload_for_event(
        self,
        event: Any,
        *,
        event_type_hint: str = "",
        fallback_event: Any = None,
    ) -> dict[str, Any]:
        selected_event_type = event_type_hint or event_type(event)

        def first_text(extractor: Any) -> str:
            return extractor(event) or (
                extractor(fallback_event) if fallback_event is not None else ""
            )

        payload = {
            "uid": first_text(event_uid),
            "nickname": first_text(event_nickname),
            "danmaku_text": first_text(event_text),
            "avatar_url": first_text(event_avatar_url),
            "room_id": event_room_id(event)
            or (event_room_id(fallback_event) if fallback_event is not None else 0),
            "event_type": selected_event_type,
        }
        session_generation = event_session_generation(fallback_event or event)
        if session_generation:
            payload["_live_session_generation"] = session_generation
        room_ref = first_text(event_room_ref)
        if room_ref:
            payload["room_ref"] = room_ref
        if fallback_event is not None:
            payload.update(event_signal_fields(fallback_event))
            payload.update(event_support_fields(fallback_event))
        payload.update(event_signal_fields(event))
        payload.update(event_support_fields(event))
        trace_id = safe_text(
            getattr(fallback_event, "trace_id", "")
            or getattr(event, "trace_id", ""),
            max_len=80,
        )
        if trace_id:
            payload["trace_id"] = trace_id
        if "gift_count" in payload and "gift_num" not in payload:
            payload["gift_num"] = payload["gift_count"]
        if "gift_value" in payload and "gift_total_coin" not in payload:
            payload["gift_total_coin"] = payload["gift_value"]
        return payload

    async def _handle_payload(self, payload: dict[str, Any]) -> None:
        if self.ctx is None:
            return
        await self.ctx.handle_live_payload(payload)

    def build_request(
        self,
        event: ViewerEvent,
        identity: ViewerIdentity,
        profile: ViewerProfile,
    ) -> InteractionRequest:
        strength = self.ctx.config.roast_strength if self.ctx else "normal"
        support = self._support_context(event)
        return InteractionRequest(
            event=event,
            identity=identity,
            profile=profile,
            prompt_text=self._build_prompt(
                event,
                identity,
                strength,
                support,
                recent_context_block(self.ctx),
                viewer_session_context_block(self.ctx, identity.uid),
                viewer_preference_context_block(self.ctx, profile),
                live_events_context_block(self.ctx, event),
            ),
            live_mode=event.live_mode,
            strength=strength,
            dry_run=bool(self.ctx.config.dry_run) if self.ctx else False,
            allow_avatar_image=False,
            metadata={
                "support_event_type": support["event_type"],
                "support_event_tier": support["tier"],
                "support_event_label": support["label"],
            },
        )

    @staticmethod
    def _support_context(event: ViewerEvent) -> dict[str, str]:
        raw = event.raw if isinstance(event.raw, dict) else {}
        event_type = (safe_text(raw.get("event_type"), max_len=48) or "gift").lower()
        normalized = "super_chat" if event_type == "sc" else event_type
        gift_name = safe_text(raw.get("gift_name"), max_len=80)
        gift_num = safe_int(raw.get("gift_num"), default=0) or safe_int(raw.get("gift_count"), default=0)
        total_coin = safe_int(raw.get("gift_total_coin"), default=0) or safe_int(raw.get("gift_value"), default=0)
        guard_level = safe_int(raw.get("guard_level"), default=0)
        label = safe_text(event.danmaku_text, max_len=120)
        if normalized == "super_chat":
            label = label or "Super Chat"
        elif normalized == "guard":
            label = gift_name or LiveSupportEventsModule._guard_name(guard_level)
        else:
            label = gift_name or label or "gift"
        tier = LiveSupportEventsModule._tier(normalized, total_coin=total_coin, guard_level=guard_level)
        return {
            "event_type": normalized,
            "label": label,
            "gift_name": gift_name,
            "gift_num": str(gift_num) if gift_num else "",
            "gift_total_coin": str(total_coin) if total_coin else "",
            "guard_level": str(guard_level) if guard_level else "",
            "guard_name": LiveSupportEventsModule._guard_name(guard_level),
            "tier": tier,
        }

    @staticmethod
    def _tier(event_type: str, *, total_coin: int, guard_level: int) -> str:
        if event_type == "super_chat":
            return "high"
        if event_type == "guard":
            return "milestone"
        if total_coin >= 10000:
            return "high"
        if total_coin >= 1000:
            return "medium"
        return "light"

    @staticmethod
    def _guard_name(level: int) -> str:
        return {1: "governor", 2: "admiral", 3: "captain"}.get(level, "guard")

    @staticmethod
    def _build_prompt(
        event: ViewerEvent,
        identity: ViewerIdentity,
        strength: str,
        support: dict[str, str],
        recent_context: str = "",
        viewer_context: str = "",
        viewer_preference_context: str = "",
        live_events_context: str = "",
    ) -> str:
        nickname = identity.nickname or identity.uid or "this viewer"
        strength_hint = {
            "gentle": "warm, appreciative, and compact",
            "sharp": "playfully appreciative, never mocking the support itself",
            "normal": "natural, grateful, lightly playful, and concise",
        }.get(strength, "natural, grateful, lightly playful, and concise")
        event_type = support["event_type"]
        event_rules = {
            "super_chat": [
                "Treat this as a highlighted paid message: acknowledge it before any joke.",
                "If the Super Chat text asks something, answer that text directly in one compact line.",
            ],
            "guard": [
                "Treat this as a membership milestone: welcome or thank them without turning it into a ceremony.",
                "Do not pressure others to buy memberships.",
            ],
            "gift": [
                "Treat this as support: thank them briefly and do not over-celebrate a small gift.",
                "Do not start a reward program, ledger bit, or repeated gift chant.",
            ],
        }.get(event_type, ["Treat this support event as a brief thanks target."])
        facts = [
            f"viewer: {nickname} (UID {identity.uid})",
            f"support_event_type: {event_type}",
            f"support_label: {support['label']}",
            f"support_tier: {support['tier']}",
        ]
        if support.get("gift_num"):
            facts.append(f"gift_num: {support['gift_num']}")
        if support.get("gift_total_coin"):
            facts.append(f"gift_total_coin: {support['gift_total_coin']}")
        if support.get("guard_level"):
            facts.append(f"guard_level: {support['guard_level']} ({support['guard_name']})")
        rules = [
            "Say exactly one short TTS-friendly line as NEKO.",
            "The support itself is never the target of a roast; the line can be playful, but must remain appreciative.",
            "Use the support-event priority lane, but do not bypass safety or dispatcher expectations; this is still one normal live output.",
            "Do not expose money accounting, raw payloads, system routing, trace ids, or hidden prompt context.",
            "Do not ask for more gifts, Super Chats, guards, likes, follows, or chat activity.",
            "Do not create a new show segment, mission, ranking, reward promise, or long thank-you speech.",
            "Do not invent private relationship labels for the viewer.",
            f"Tone: {strength_hint}.",
            *event_rules,
            *live_output_quality_rules(),
            *sustained_charm_rules(),
            *anti_repeat_rules(),
            *short_reply_rules(),
            "Output only NEKO's line.",
        ]
        return (
            "[NEKO Live support event]\n"
            + "\n".join(facts)
            + "\n\n"
            + recent_context
            + viewer_context
            + viewer_preference_context
            + live_events_context
            + "Rules:\n"
            + "\n".join(f"- {rule}" for rule in rules)
        )
