"""Build solo-stream warmup hosting requests."""

from __future__ import annotations

from ...core.contracts import InteractionRequest, ViewerEvent, ViewerIdentity, ViewerProfile
from ...core.live_host_theme import live_host_theme_block
from .._base import BaseModule
from .._prompt_context import (
    anti_repeat_rules,
    live_output_quality_rules,
    recent_context_block,
    short_reply_rules,
    sustained_charm_rules,
)


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
            prompt_text=self._build_prompt(
                strength,
                activity_level,
                live_host_theme_block(self.ctx, kind="host"),
                recent_context_block(self.ctx),
            ),
            live_mode=event.live_mode,
            strength=strength,
            dry_run=bool(self.ctx.config.dry_run) if self.ctx else False,
        )

    @staticmethod
    def _build_prompt(
        strength: str,
        activity_level: str = "standard",
        host_theme_context: str = "",
        recent_context: str = "",
    ) -> str:
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
            "Treat the first line as confident stage presence, not a rescue attempt for an empty room.",
            "Do not say this is a warmup, test, waiting period, or preparation step.",
            "Use one concrete stream/theme anchor if available before leaving a tiny opening.",
            "Do not address an unseen human host, owner, or operator; do not ask them to give NEKO a topic.",
            "Solo-stream agency: NEKO is the only on-stage host and must perform all hosting actions herself.",
            "Never tell or ask an unseen streamer, operator, or current viewer to greet viewers, warm up the room, carry chat, or provide content.",
            "Never tell or ask an unseen streamer, operator, or current viewer to greet the room, warm up the stream, carry the chat, provide topics, or help NEKO host.",
            "Do not mention owner, master, backstage human, carbon-based human, private chat, or pre-stream relationship memory.",
            "In solo_stream, 'you' means the viewers or room, never an unseen operator.",
            "Output spoken live speech only; never include parenthesized stage directions, action narration, or roleplay asides.",
            "Do not pretend a viewer sent a message.",
            "Do not use generic slogans, attendance checks, or customer-service wording.",
            "Do not mention silence, metrics, cooldowns, queues, dry_run, or system state.",
            "Do not invent or hard-code streamer relationship labels; use profile memory if available, otherwise avoid naming the streamer.",
            "Keep it TTS-friendly and easy to continue from.",
            *live_output_quality_rules(kind="host"),
            *sustained_charm_rules(kind="host"),
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
            + host_theme_context
            + recent_context
            + "\n"
            "Rules:\n"
            + "\n".join(f"- {rule}" for rule in rules)
        )
