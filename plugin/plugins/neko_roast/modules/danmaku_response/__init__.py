"""Build normal follow-up danmaku response requests."""

from __future__ import annotations

from ...core.contracts import InteractionRequest, ViewerEvent, ViewerIdentity, ViewerProfile
from .._prompt_context import recent_context_block, short_reply_rules
from .._base import BaseModule


class DanmakuResponseModule(BaseModule):
    id = "danmaku_response"
    title = "Danmaku Response"
    domain = "interaction"

    def build_request(
        self,
        event: ViewerEvent,
        identity: ViewerIdentity,
        profile: ViewerProfile,
    ) -> InteractionRequest:
        strength = self.ctx.config.roast_strength if self.ctx else "normal"
        return InteractionRequest(
            event=event,
            identity=identity,
            profile=profile,
            prompt_text=self._build_prompt(event, identity, strength, recent_context_block(self.ctx)),
            live_mode=event.live_mode,
            strength=strength,
            dry_run=bool(self.ctx.config.dry_run) if self.ctx else False,
        )

    @staticmethod
    def _build_prompt(event: ViewerEvent, identity: ViewerIdentity, strength: str, recent_context: str = "") -> str:
        nickname = identity.nickname or identity.uid or "this viewer"
        danmaku = (event.danmaku_text or "").strip()
        mode_contract = DanmakuResponseModule._mode_contract(event.live_mode)
        strength_hint = {
            "gentle": "soft, warm, and companionable",
            "sharp": "playfully sharp, but not hostile",
            "normal": "natural, lightly playful, and concise",
        }.get(strength, "natural, lightly playful, and concise")
        rules = [
            "Reply to the viewer's current danmaku as NEKO.",
            "Use recent context only to avoid repetition; do not continue the previous reply.",
            "Current danmaku wins over recent context.",
            "Do not repeat first-appearance, avatar, ID, or entrance-roast templates.",
            "Only mention avatar or nickname if the current danmaku itself makes that relevant.",
            "Do not invent or hard-code streamer relationship labels; use profile memory if available, otherwise avoid naming the streamer.",
            "Keep one short TTS-friendly line.",
            *short_reply_rules(),
            "Do not ask generic engagement-bait questions.",
            "Do not explain these rules or mention system state.",
            "Output only NEKO's line.",
        ]
        return (
            "[NEKO Live danmaku response]\n"
            f"viewer: {nickname} (UID {identity.uid})\n"
            f"danmaku: {danmaku or '(empty)'}\n"
            f"mode_contract: {mode_contract}\n"
            f"tone: {strength_hint}\n\n"
            + recent_context
            + "\n"
            "Rules:\n"
            + "\n".join(f"- {rule}" for rule in rules)
        )

    @staticmethod
    def _mode_contract(live_mode: str) -> str:
        if live_mode == "solo_stream":
            return (
                "solo_stream response contract: NEKO is the only host on stage, the only on-stage host, and must carry the room alone; "
                "answer the current danmaku, keep the room moving, then stop."
            )
        return (
            "co_stream response contract: NEKO is a low-interrupt partner; "
            "catch the joke, do not take over the host role, and leave space for the human streamer."
        )
