"""Build solo-stream warmup hosting requests."""

from __future__ import annotations

from ...core.contracts import InteractionRequest, ViewerEvent, ViewerIdentity, ViewerProfile
from .._base import BaseModule
from .._prompt_context import anti_repeat_rules, recent_context_block, short_reply_rules


class WarmupHostingModule(BaseModule):
    id = "warmup_hosting"
    title = "Warmup Hosting"
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
            "gentle": "warm, soft, and welcoming",
            "sharp": "playfully sharp, but not aggressive",
            "normal": "natural, lightly playful, and welcoming",
        }.get(strength, "natural, lightly playful, and welcoming")
        activity_hint = {
            "quiet": "Keep it soft and calm; do not immediately ask viewers to talk.",
            "active": "You may add one small, easy hook for viewers to answer later.",
            "standard": "Welcome the room and leave one small opening for conversation.",
        }.get(str(activity_level), "Welcome the room and leave one small opening for conversation.")
        rules = [
            "NEKO is opening a solo_stream as the only host on stage.",
            "Say exactly one short opening host line as NEKO.",
            "Make it sound like a live opening, not a cold-room filler.",
            "Do not pretend a viewer sent a message.",
            "Do not use generic slogans, attendance checks, or customer-service wording.",
            "Do not mention silence, metrics, cooldowns, queues, dry_run, or system state.",
            "Do not invent or hard-code streamer relationship labels; use profile memory if available, otherwise avoid naming the streamer.",
            "Keep it TTS-friendly and easy to continue from.",
            *anti_repeat_rules(kind="host"),
            *short_reply_rules(kind="host"),
            "Output only NEKO's line.",
        ]
        return (
            "[NEKO Live solo warmup hosting]\n"
            "scene: solo_stream opening moment\n"
            f"tone: {strength_hint}\n"
            f"pacing: {activity_level}\n"
            f"pacing rule: {activity_hint}\n\n"
            + recent_context
            + "\n"
            "Rules:\n"
            + "\n".join(f"- {rule}" for rule in rules)
        )
