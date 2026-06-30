"""Build normal follow-up danmaku response requests."""

from __future__ import annotations

import re

from ...core import active_topic_rules
from ...core.contracts import InteractionRequest, ViewerEvent, ViewerIdentity, ViewerProfile
from .._prompt_context import (
    anti_repeat_rules,
    live_output_quality_rules,
    recent_context_block,
    short_reply_rules,
    sustained_charm_rules,
    viewer_session_context_block,
)
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
            prompt_text=self._build_prompt(
                event,
                identity,
                strength,
                recent_context_block(self.ctx),
                viewer_session_context_block(self.ctx, identity.uid),
            ),
            live_mode=event.live_mode,
            strength=strength,
            dry_run=bool(self.ctx.config.dry_run) if self.ctx else False,
            allow_avatar_image=False,
            metadata=self._metadata_for_event(event),
        )

    @staticmethod
    def _build_prompt(
        event: ViewerEvent,
        identity: ViewerIdentity,
        strength: str,
        recent_context: str = "",
        viewer_context: str = "",
    ) -> str:
        nickname = identity.nickname or identity.uid or "this viewer"
        danmaku = (event.danmaku_text or "").strip()
        danmaku_profile = DanmakuResponseModule._danmaku_profile(danmaku)
        anchor_hint = DanmakuResponseModule._anchor_hint(danmaku)
        mode_contract = DanmakuResponseModule._mode_contract(event.live_mode)
        strength_hint = {
            "gentle": "soft, warm, and companionable",
            "sharp": "playfully sharp, but not hostile",
            "normal": "natural, lightly playful, and concise",
        }.get(strength, "natural, lightly playful, and concise")
        rules = [
            "Reply to the viewer's current danmaku as NEKO.",
            "Use recent context only to avoid repetition; do not continue the previous reply.",
            "Use same-viewer context only to keep continuity with this viewer, not to repeat old jokes.",
            "Current danmaku wins over recent context.",
            "Treat short assent, emoji, or one-word danmaku as a tiny reaction target, not a reason to start a new plan.",
            "Only continue an old thread if the current danmaku explicitly continues that exact thread.",
            "Do not answer @other-viewer messages as a call to NEKO unless NEKO is the mentioned target.",
            "Do not repeat first-appearance, avatar, ID, or entrance-roast templates.",
            "Make the target legible: keep one visible anchor from the current danmaku, such as the viewer nickname, a 2-6 character quote, or a concrete noun from the danmaku.",
            "Do not make every reply start with the nickname; use the nickname only when the line would otherwise be ambiguous.",
            "Only mention avatar if the current danmaku itself makes that relevant.",
            "Do not invent or hard-code streamer relationship labels; use profile memory if available, otherwise avoid naming the streamer.",
            "Do not launch a new show segment, special plan, topic poll, reward bit, or audience-suggestion prompt.",
            "Keep one short TTS-friendly line.",
            *live_output_quality_rules(),
            *sustained_charm_rules(),
            *DanmakuResponseModule._profile_rules(danmaku_profile["kind"]),
            *anti_repeat_rules(),
            *short_reply_rules(),
            "Do not ask generic engagement-bait questions.",
            "Do not append a follow-up question just to keep the chat moving.",
            "Do not explain these rules or mention system state.",
            "Output only NEKO's line.",
        ]
        return (
            "[NEKO Live danmaku response]\n"
            f"viewer: {nickname} (UID {identity.uid})\n"
            f"danmaku: {danmaku or '(empty)'}\n"
            f"danmaku_profile: {danmaku_profile['kind']}\n"
            f"reply_target: {danmaku_profile['reply_target']}\n"
            f"reply_shape: {danmaku_profile['reply_shape']}\n"
            f"anchor_hint: {anchor_hint or '(none)'}\n"
            f"mode_contract: {mode_contract}\n"
            f"tone: {strength_hint}\n\n"
            + recent_context
            + viewer_context
            + "\n"
            "Rules:\n"
            + "\n".join(f"- {rule}" for rule in rules)
        )

    @staticmethod
    def _mode_contract(live_mode: str) -> str:
        if live_mode == "solo_stream":
            return (
                "solo_stream response contract: NEKO is the only host on stage, the only on-stage host, and must carry the room alone; "
                "answer the current danmaku in one compact line, keep the room moving, then stop. "
                "Carrying the room means crisp timing, not monologue, plans, or host-script expansion."
            )
        return (
            "co_stream response contract: NEKO is a low-interrupt partner; "
            "catch the joke, do not take over the host role, and leave space for the human streamer."
        )

    @staticmethod
    def _danmaku_profile(danmaku: str) -> dict[str, str]:
        text = str(danmaku or "").strip()
        dense = DanmakuResponseModule._dense_text(text)
        signal_len = len(dense)
        word_count = len([part for part in text.replace("\u3000", " ").split(" ") if part.strip()])
        if active_topic_rules._is_viewer_to_viewer_mention_text(text):
            return {
                "kind": "viewer_to_viewer_mention",
                "reply_target": "public_side_reaction",
                "reply_shape": "tiny_side_reaction",
            }
        if not text:
            return {
                "kind": "empty",
                "reply_target": "nothing_to_answer",
                "reply_shape": "skip_or_tiny_reaction",
            }
        if DanmakuResponseModule._looks_like_reaction(text, dense):
            return {
                "kind": "emoji_or_reaction",
                "reply_target": "current_reaction",
                "reply_shape": "mirror_mood_in_a_few_chars",
            }
        if DanmakuResponseModule._looks_like_question(text, dense):
            return {
                "kind": "question",
                "reply_target": "current_question",
                "reply_shape": "direct_short_answer",
            }
        if signal_len <= 4 or (signal_len <= 10 and word_count <= 3):
            return {
                "kind": "short_line",
                "reply_target": "current_short_line",
                "reply_shape": "shorter_than_input_when_possible",
            }
        return {
            "kind": "normal_line",
            "reply_target": "current_danmaku_meaning",
            "reply_shape": "one_compact_reply",
        }

    @staticmethod
    def _metadata_for_event(event: ViewerEvent) -> dict[str, str]:
        profile = DanmakuResponseModule._danmaku_profile(event.danmaku_text or "")
        return {
            "danmaku_profile": profile["kind"],
            "danmaku_reply_target": profile["reply_target"],
            "danmaku_reply_shape": profile["reply_shape"],
            "danmaku_anchor_hint": DanmakuResponseModule._anchor_hint(event.danmaku_text or ""),
        }

    @staticmethod
    def _anchor_hint(danmaku: str) -> str:
        text = str(danmaku or "").strip()
        first_clause = re.split(r"[\s，,。.!！?？、；;：:]+", text, maxsplit=1)[0] if text else ""
        dense = DanmakuResponseModule._dense_text(first_clause or text)
        if not dense or active_topic_rules._is_reaction_only(dense):
            return ""
        if len(dense) <= 6:
            return dense
        for start in range(0, min(len(dense), 10), 2):
            candidate = dense[start : start + 6]
            if len(candidate) >= 2 and not active_topic_rules._is_reaction_only(candidate):
                return candidate
        return dense[:6]

    @staticmethod
    def _profile_rules(kind: str) -> list[str]:
        return {
            "viewer_to_viewer_mention": [
                "This danmaku appears to @ another viewer; do not answer as if it was addressed to NEKO.",
                "If replying, make only one tiny side reaction to the public content and do not mediate between viewers.",
            ],
            "emoji_or_reaction": [
                "For emoji, laughter, punctuation, or tiny reactions, mirror the mood in a few characters.",
                "Do not explain the joke, expand the reaction, or turn it into a topic.",
            ],
            "question": [
                "If the current danmaku is a question, answer it directly first.",
                "Do not dodge into a topic change or ask a new question.",
            ],
            "short_line": [
                "For this short danmaku, reply shorter than the danmaku when possible.",
                "No extra hook, no recap, and no old-context continuation.",
            ],
            "empty": [
                "If there is no current text to answer, do not invent a topic from old context.",
            ],
        }.get(
            kind,
            [
                "For ordinary chat, answer the current meaning only.",
                "Do not summarize same-viewer history unless the current danmaku explicitly asks for it.",
            ],
        )

    @staticmethod
    def _dense_text(text: str) -> str:
        lowered = str(text or "").casefold()
        return "".join(ch for ch in lowered if ch.isalnum() or "\u4e00" <= ch <= "\u9fff")

    @staticmethod
    def _looks_like_reaction(text: str, dense: str) -> bool:
        if text.strip() and not dense:
            return True
        if active_topic_rules._is_reaction_only(dense):
            return True
        lowered = str(text or "").casefold()
        reaction_markers = (
            "hhh",
            "www",
            "233",
            "666",
            "lol",
            "lmao",
            "\u54c8\u54c8",
            "\u7b11\u6b7b",
            "\u8349",
            "\u597d\u8036",
            "\u53ef\u7231",
            "\u55b5",
        )
        return len(dense) <= 8 and any(marker in lowered or marker in dense for marker in reaction_markers)

    @staticmethod
    def _looks_like_question(text: str, dense: str) -> bool:
        if any(marker in text for marker in ("?", "\uff1f")):
            return True
        question_markers = (
            "\u600e\u4e48",
            "\u4e3a\u4ec0\u4e48",
            "\u54cb",
            "\u6709\u6ca1\u6709",
            "\u662f\u4e0d\u662f",
            "\u80fd\u4e0d\u80fd",
            "\u53ef\u4ee5\u5417",
            "\u597d\u4e0d\u597d",
        )
        if any(marker in dense for marker in question_markers):
            return True
        return dense.endswith(("\u5417", "\u5462", "\u4e48", "\u561b"))
