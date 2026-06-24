"""Build solo-stream active engagement requests."""

from __future__ import annotations

from ...core.contracts import InteractionRequest, ViewerEvent, ViewerIdentity, ViewerProfile
from .._base import BaseModule
from .._prompt_context import recent_context_block, short_reply_rules


class ActiveEngagementModule(BaseModule):
    id = "active_engagement"
    title = "Active Engagement"
    domain = "interaction"

    def build_request(
        self,
        event: ViewerEvent,
        identity: ViewerIdentity,
        profile: ViewerProfile,
    ) -> InteractionRequest:
        strength = self.ctx.config.roast_strength if self.ctx else "normal"
        activity_level = self.ctx.config.activity_level if self.ctx else "standard"
        return InteractionRequest(
            event=event,
            identity=identity,
            profile=profile,
            prompt_text=self._build_prompt(strength, activity_level, recent_context_block(self.ctx)),
            live_mode=event.live_mode,
            strength=strength,
            dry_run=bool(self.ctx.config.dry_run) if self.ctx else False,
        )

    @staticmethod
    def _build_prompt(strength: str, activity_level: str = "standard", recent_context: str = "") -> str:
        strength_hint = {
            "gentle": "warm, soft, and easy to answer",
            "sharp": "playfully sharp, but never hostile or pushy",
            "normal": "natural, lightly playful, and concise",
        }.get(strength, "natural, lightly playful, and concise")
        activity_hint = {
            "quiet": "Make it softer than usual: one observation is better than a direct question.",
            "active": "You may ask one concrete, low-pressure question that viewers can answer quickly.",
            "standard": "Use one small observation or one concrete easy question.",
        }.get(str(activity_level), "Use one small observation or one concrete easy question.")
        rules = [
            "NEKO is the only host on stage in solo_stream.",
            "Create exactly one small live-room engagement beat as NEKO.",
            "Make it specific enough that a viewer can naturally reply, without begging for comments.",
            "Prefer one tiny observation over a plan, segment, or open-ended topic survey.",
            "Do not use generic host slogans like 'everyone interact' or 'say something in chat'.",
            "Do not say special plan, everyone look, next let's, what should we talk about, or tell me what you want.",
            "Do not mention silence, metrics, cooldowns, queues, dry_run, or system state.",
            "Do not pretend a viewer sent a message.",
            "Do not invent or hard-code streamer relationship labels; use profile memory if available, otherwise avoid naming the streamer.",
            "Keep one short TTS-friendly line.",
            *short_reply_rules(),
            "Output only NEKO's line.",
        ]
        return (
            "[NEKO Live active engagement]\n"
            "scene: solo_stream quiet moment\n"
            f"tone: {strength_hint}\n"
            f"pacing: {activity_level}\n"
            f"pacing rule: {activity_hint}\n\n"
            + recent_context
            + "\nRules:\n"
            + "\n".join(f"- {rule}" for rule in rules)
        )
