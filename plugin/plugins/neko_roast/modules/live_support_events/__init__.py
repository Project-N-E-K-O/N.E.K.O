"""Build support-event response requests for Gift / Super Chat / Guard."""

from __future__ import annotations

import asyncio
import time
from typing import Any

from ...core.contracts import InteractionRequest, ViewerEvent, ViewerIdentity, ViewerProfile
from ...core.runtime_timeline import record_payload_timeline
from ...core.viewer_preferences import safe_int, safe_text, viewer_preference_prompt_block
from .._base import BaseModule
from .._prompt_context import (
    anti_repeat_rules,
    live_events_context_block,
    live_output_quality_rules,
    recent_context_block,
    short_reply_rules,
    sustained_charm_rules,
    viewer_session_context_block,
)
from ..live_events.provider_event import (
    event_avatar_url,
    event_nickname,
    event_room_id,
    event_room_ref,
    event_signal_fields,
    event_text,
    event_type,
    event_uid,
    is_signal_only,
)


class LiveSupportEventsModule(BaseModule):
    id = "live_support_events"
    title = "Live Support Events"
    domain = "interaction"

    def __init__(self) -> None:
        super().__init__()
        self._unsubscribes: list[Any] = []
        self._tasks: set[asyncio.Task[Any]] = set()
        self._last_event_at: float = 0.0
        self._last_event_type: str = ""

    async def setup(self, ctx: Any) -> None:
        await super().setup(ctx)
        bus = getattr(ctx, "event_bus", None)
        if bus is not None:
            for event_name in ("gift", "super_chat", "guard"):
                self._unsubscribes.append(bus.subscribe(event_name, self._on_bus_event, owner=self.id))

    async def teardown(self) -> None:
        for unsubscribe in self._unsubscribes:
            if callable(unsubscribe):
                unsubscribe()
        self._unsubscribes = []
        pending = [task for task in list(self._tasks) if not task.done()]
        for task in pending:
            task.cancel()
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)
        self._tasks.clear()
        await super().teardown()

    def status(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "subscribed": bool(self._unsubscribes),
            "pending": len([task for task in self._tasks if not task.done()]),
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
        if not is_signal_only(support_event):
            return
        payload = self._payload_for_event(support_event)
        if not payload.get("uid"):
            return
        self._last_event_at = time.time()
        self._last_event_type = str(payload.get("event_type") or "")
        record_payload_timeline(
            self.ctx,
            payload,
            stage="live_support_events.receive",
            status="ok",
            reason=f"support {self._last_event_type}",
            route=self.id,
        )
        task = asyncio.create_task(self._handle_payload(payload))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    def _payload_for_event(self, event: Any) -> dict[str, Any]:
        selected_event_type = event_type(event)
        payload = {
            "uid": event_uid(event),
            "nickname": event_nickname(event),
            "danmaku_text": event_text(event),
            "avatar_url": event_avatar_url(event),
            "room_id": event_room_id(event),
            "event_type": selected_event_type,
        }
        room_ref = event_room_ref(event)
        if room_ref:
            payload["room_ref"] = room_ref
        payload.update(event_signal_fields(event))
        if "gift_count" in payload and "gift_num" not in payload:
            payload["gift_num"] = payload["gift_count"]
        if "gift_value" in payload and "gift_total_coin" not in payload:
            payload["gift_total_coin"] = payload["gift_value"]
        return payload

    async def _handle_payload(self, payload: dict[str, Any]) -> None:
        if self.ctx is None:
            return
        try:
            await self.ctx.handle_live_payload(payload)
        except Exception as exc:
            self.ctx.audit.record("live_support_event_failed", type(exc).__name__, level="warning")

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
                viewer_preference_prompt_block(profile),
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
