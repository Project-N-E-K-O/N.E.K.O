"""Build normal follow-up danmaku response requests."""

from __future__ import annotations

import re

from ...core import danmaku_text_rules
from ...core.contracts import InteractionRequest, ViewerEvent, ViewerIdentity, ViewerProfile
from ...core.live_host_theme import live_host_theme_block
from ...core.live_reply_contract import DANMAKU_ROOM_BRIDGE_REPLY_CHARS, ROOM_BRIDGE_REPLY_MODE
from ...core.live_text_guards import (
    context_mentions_idiom_chain,
    looks_like_idiom_chain_start,
    looks_like_idiom_chain_turn,
    looks_like_support_claim_text,
)
from ...core.meme_knowledge import meme_knowledge_metadata, retrieve_meme_knowledge
from ...core.viewer_addressing import viewer_address_name
from ...core.viewer_preferences import viewer_preference_prompt_block
from .._prompt_context import (
    anti_repeat_rules,
    live_events_context_block,
    live_output_quality_rules,
    meme_knowledge_context_block,
    recent_context_block,
    room_danmaku_context_block,
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
        room_context = room_danmaku_context_block(self.ctx, event)
        recent_context = recent_context_block(self.ctx)
        viewer_context = viewer_session_context_block(self.ctx, identity.uid)
        meme_context = meme_knowledge_context_block(event.danmaku_text or "", room_context)
        return InteractionRequest(
            event=event,
            identity=identity,
            profile=profile,
            prompt_text=self._build_prompt(
                event,
                identity,
                profile,
                strength,
                live_host_theme_block(self.ctx, kind="reply"),
                recent_context,
                viewer_context,
                viewer_preference_prompt_block(profile),
                room_context,
                live_events_context_block(self.ctx, event),
                meme_context,
            ),
            live_mode=event.live_mode,
            strength=strength,
            dry_run=bool(self.ctx.config.dry_run) if self.ctx else False,
            allow_avatar_image=False,
            metadata=self._metadata_for_event(
                event,
                identity,
                profile,
                room_context=room_context,
                recent_context=recent_context,
                viewer_context=viewer_context,
            ),
        )

    @staticmethod
    def _build_prompt(
        event: ViewerEvent,
        identity: ViewerIdentity,
        profile: ViewerProfile,
        strength: str,
        host_theme_context: str = "",
        recent_context: str = "",
        viewer_context: str = "",
        viewer_preference_context: str = "",
        room_context: str = "",
        danmaku_context: str = "",
        meme_context: str = "",
    ) -> str:
        raw_nickname = identity.nickname or identity.uid or "this viewer"
        nickname = viewer_address_name(raw_nickname, profile) or raw_nickname
        danmaku = (event.danmaku_text or "").strip()
        danmaku_profile = DanmakuResponseModule._danmaku_profile(
            danmaku,
            raw=event.raw if isinstance(event.raw, dict) else None,
            context_text="\n".join((recent_context, viewer_context, room_context)),
        )
        target_roast_viewer = str(danmaku_profile.get("target_viewer") or "").strip()
        anchor_hint = DanmakuResponseModule._anchor_hint(danmaku)
        mode_contract = DanmakuResponseModule._mode_contract(event.live_mode)
        support_claim_contract = (
            "unverified_danmaku_claim_no_thanks"
            if looks_like_support_claim_text(danmaku)
            else "none"
        )
        strength_hint = {
            "gentle": "soft, warm, and companionable",
            "sharp": "playfully sharp, but not hostile",
            "normal": "natural, lightly playful, and concise",
        }.get(strength, "natural, lightly playful, and concise")
        room_bridge = DanmakuResponseModule._allows_room_bridge_length(danmaku_profile["kind"], room_context)
        rules = [
            "Reply to the viewer's current danmaku as NEKO.",
            f"Target lock: this reply is for the current danmaku from {nickname}; do not answer the previous viewer unless the current danmaku names them.",
            "Use recent context only to avoid repetition; do not continue the previous reply.",
            "Use same-viewer context only to keep continuity with this viewer, not to repeat old jokes.",
            "Current danmaku wins over recent context.",
            "If several recent danmaku share a theme, use that theme only as a quiet bridge after answering the current viewer.",
            "If NEKO recently promised or opened a pending thread, satisfy that pending thread now when the current danmaku continues it.",
            "When a room theme exists, synthesize the theme briefly; do not reply to each message separately.",
            "Do not make the reply sound like it belongs to an unnamed crowd when one viewer just spoke.",
            "Mention at most one viewer nickname, and only when it helps target clarity.",
            "Never list, greet, or reassure multiple viewers in one line.",
            "Do not list multiple viewer names; one current target or a natural room-facing phrase is enough.",
            "For regular or familiar viewers, prefer the natural short address over the full formal nickname when preferred_viewer_address is present.",
            "Never invent pinyin initials, Latin initials, or all-letter abbreviations for a Chinese nickname; use preferred_viewer_address when present, otherwise keep the full nickname.",
            "Solo live isolation: do not mention the owner, master, operator, backstage human, carbon-based human, private chat, or pre-stream relationship memory.",
            "In solo_stream, the word 'you' must mean the current danmaku viewer, not the unseen operator or streamer.",
            "Do not direct the streamer/operator/current viewer to greet viewers, warm up the room, carry chat, provide topics, or help NEKO host.",
            "Output spoken live speech only; never output parenthesized stage directions, action narration, or roleplay asides.",
            "If the current danmaku is a greeting, greet back first; do not pivot to avatar, nickname, ID, or profile memory.",
            "Treat short assent, emoji, or one-word danmaku as a tiny reaction target, not a reason to start a new plan.",
            "If the current danmaku only claims a gift, Super Chat, guard, or support but event_type is danmaku, treat it as a joke/claim, not a real support event; do not thank it as real.",
            "For an unverified support claim, a brief startled or mildly indignant reaction is allowed, but do not say thanks, received, or confirm a real gift.",
            "Only continue an old thread if the current danmaku explicitly continues that exact thread.",
            "Do not answer @other-viewer messages as a call to NEKO unless NEKO is the mentioned target.",
            "Do not repeat first-appearance, avatar, ID, or entrance-roast templates.",
            "Make the target legible like a live streamer without sounding like roll call: for direct danmaku replies, use the viewer nickname, a short address, 'this danmaku', or a clear quote cue in the first clause unless the danmaku is only emoji/reaction, empty, viewer-to-viewer @ chatter, or a target-roast request.",
            "For ordinary replies, ordinary replies may use the danmaku anchor instead of a name when the anchor is clearer and more natural.",
            "For ordinary replies, ordinary replies may use a natural room-facing phrase instead of a full nickname.",
            "For ordinary replies, do not force the viewer's full nickname into the first clause when a natural address or danmaku anchor is clearer.",
            "If the reply would otherwise be ambiguous, use the shortest natural address that identifies the current viewer.",
            "Use the nickname naturally, not as a label: e.g. '方块km，...' or '悠怡这个...' rather than '回复方块km：...'.",
            "When anchor_hint is present, keep that anchor or the viewer nickname visible in the first clause so viewers can tell which danmaku NEKO is answering.",
            "The anchor_hint is for target clarity only; do not treat it as text that must be repeated verbatim.",
            "Do not parrot the current danmaku; answer or twist the anchor instead of repeating it back.",
            "Only mention avatar if the current danmaku itself makes that relevant.",
            "Do not invent or hard-code streamer relationship labels; use profile memory if available, otherwise avoid naming the streamer.",
            "Do not use stale comparison templates like 'NEKO thinks X is better than master/viewer'.",
            "Do not compare the current viewer, students, or the room to master/viewer as a generic punchline unless the current danmaku explicitly asks for that comparison.",
            "Avoid opening with 'NEKO thinks' or 'cat thinks' when it only leads into a generic comparison.",
            "Do not launch a new show segment, special plan, topic poll, reward bit, or audience-suggestion prompt.",
            "Keep one short TTS-friendly line.",
            *live_output_quality_rules(),
            *sustained_charm_rules(),
            *DanmakuResponseModule._profile_rules(danmaku_profile["kind"]),
            *anti_repeat_rules(),
            *DanmakuResponseModule._length_rules(danmaku_profile["kind"]),
            *DanmakuResponseModule._room_bridge_rules(room_bridge),
            "Do not ask generic engagement-bait questions.",
            "Do not append a follow-up question just to keep the chat moving.",
            "Do not explain these rules or mention system state.",
            "Output only NEKO's line.",
        ]
        current_turn_contract = (
            f"answer {nickname}'s current request by roasting {target_roast_viewer}; direct replies should visibly address {target_roast_viewer}"
            if target_roast_viewer
            else f"answer the current danmaku from {nickname} first; ordinary replies may use a natural room-facing phrase instead of a full nickname"
        )
        target_roast_line = f"target_roast_viewer: {target_roast_viewer}\n" if target_roast_viewer else ""
        viewer_address_line = ""
        if nickname and raw_nickname and nickname != raw_nickname:
            viewer_address_line = f"preferred_viewer_address: {nickname}\nviewer_full_nickname: {raw_nickname}\n"
        return (
            "[NEKO Live danmaku response]\n"
            f"viewer: {nickname} (UID {identity.uid})\n"
            f"{viewer_address_line}"
            f"danmaku: {danmaku or '(empty)'}\n"
            f"danmaku_profile: {danmaku_profile['kind']}\n"
            f"reply_target: {danmaku_profile['reply_target']}\n"
            f"reply_shape: {danmaku_profile['reply_shape']}\n"
            f"visible_reply_target: {target_roast_viewer or nickname}\n"
            f"{target_roast_line}"
            f"reply_length_mode: {ROOM_BRIDGE_REPLY_MODE if room_bridge else 'default'}\n"
            f"support_claim_contract: {support_claim_contract}\n"
            f"anchor_hint: {anchor_hint or '(none)'}\n"
            f"current_turn_contract: {current_turn_contract}\n"
            f"mode_contract: {mode_contract}\n"
            f"tone: {strength_hint}\n\n"
            + host_theme_context
            + recent_context
            + viewer_context
            + viewer_preference_context
            + room_context
            + danmaku_context
            + meme_context
            + "\n"
            "Rules:\n"
            + "\n".join(f"- {rule}" for rule in rules)
        )

    @staticmethod
    def _mode_contract(live_mode: str) -> str:
        if live_mode == "solo_stream":
            return (
                "solo_stream response contract: NEKO is the only host on stage, the only on-stage host, and must carry the room alone; "
                "answer the current danmaku in one compact line, leave one tiny natural reply handle when it helps continuity, then stop. "
                "Carrying the room means crisp timing, not monologue, plans, or host-script expansion."
            )
        return (
            "co_stream response contract: NEKO is a low-interrupt partner; "
            "catch the joke, do not take over the host role, and leave space for the human streamer."
        )

    @staticmethod
    def _danmaku_profile(
        danmaku: str,
        raw: dict[str, object] | None = None,
        context_text: str = "",
    ) -> dict[str, str]:
        text = str(danmaku or "").strip()
        dense = DanmakuResponseModule._dense_text(text)
        signal_len = len(dense)
        word_count = len([part for part in text.replace("\u3000", " ").split(" ") if part.strip()])
        if raw and raw.get("live_batch_welcome") == "new_viewer_burst":
            return {
                "kind": "batch_welcome",
                "reply_target": "surfaced_viewers_as_group",
                "reply_shape": "one_group_welcome_no_template",
            }
        if looks_like_idiom_chain_start(text):
            return {
                "kind": "idiom_chain_start",
                "reply_target": "start_idiom_chain",
                "reply_shape": "accept_game_and_give_first_or_ask_first_word",
            }
        if looks_like_idiom_chain_turn(text) and context_mentions_idiom_chain(context_text):
            return {
                "kind": "idiom_chain_turn",
                "reply_target": "current_idiom_chain_turn",
                "reply_shape": "continue_idiom_chain_now",
            }
        if DanmakuResponseModule._looks_like_active_hook_answer(text, dense, raw):
            return {
                "kind": "active_hook_answer",
                "reply_target": "recent_active_hook_answer",
                "reply_shape": "acknowledge_viewer_answer",
            }
        target_roast = DanmakuResponseModule._target_roast_nickname(text)
        if target_roast and DanmakuResponseModule._looks_like_target_roast_request(text, dense):
            return {
                "kind": "target_roast_request",
                "reply_target": "target_viewer_public_roast",
                "reply_shape": "deliver_safe_roast_now",
                "target_viewer": target_roast,
            }
        if danmaku_text_rules.is_viewer_to_viewer_mention_text(text):
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
        if DanmakuResponseModule._looks_like_greeting(text, dense):
            return {
                "kind": "greeting",
                "reply_target": "current_greeting",
                "reply_shape": "greet_back_briefly",
            }
        if DanmakuResponseModule._looks_like_reaction(text, dense):
            return {
                "kind": "emoji_or_reaction",
                "reply_target": "current_reaction",
                "reply_shape": "mirror_mood_in_a_few_chars",
            }
        if DanmakuResponseModule._looks_like_external_action_request(text, dense):
            return {
                "kind": "external_action_request",
                "reply_target": "viewer_requested_external_action",
                "reply_shape": "state_boundary_then_live_pivot",
            }
        if DanmakuResponseModule._looks_like_content_request(text, dense):
            return {
                "kind": "content_request",
                "reply_target": "requested_content",
                "reply_shape": "deliver_tiny_content_now",
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
    def _metadata_for_event(
        event: ViewerEvent,
        identity: ViewerIdentity,
        profile: ViewerProfile,
        *,
        room_context: str = "",
        recent_context: str = "",
        viewer_context: str = "",
    ) -> dict[str, str | int]:
        danmaku_profile = DanmakuResponseModule._danmaku_profile(
            event.danmaku_text or "",
            raw=event.raw if isinstance(event.raw, dict) else None,
            context_text="\n".join((recent_context, viewer_context, room_context)),
        )
        nickname = (identity.nickname or event.nickname or "").strip()
        metadata: dict[str, str | int] = {
            "danmaku_profile": danmaku_profile["kind"],
            "danmaku_reply_target": danmaku_profile["reply_target"],
            "danmaku_reply_shape": danmaku_profile["reply_shape"],
            "danmaku_anchor_hint": DanmakuResponseModule._anchor_hint(event.danmaku_text or ""),
        }
        if nickname:
            address = viewer_address_name(nickname, profile)
            metadata["danmaku_viewer_nickname"] = (address or nickname)[:24]
            if address and address != nickname:
                metadata["danmaku_viewer_raw_nickname"] = nickname[:24]
        target_viewer = danmaku_profile.get("target_viewer")
        if isinstance(target_viewer, str) and target_viewer.strip():
            metadata["danmaku_target_viewer_nickname"] = " ".join(target_viewer.strip().split())[:24]
        room_theme = DanmakuResponseModule._room_theme_from_context(room_context)
        if room_theme:
            metadata["room_theme"] = room_theme
        if looks_like_support_claim_text(event.danmaku_text or ""):
            metadata["viewer_claimed_support"] = "unverified_danmaku_claim"
        if DanmakuResponseModule._allows_room_bridge_length(danmaku_profile["kind"], room_context):
            metadata["reply_length_mode"] = ROOM_BRIDGE_REPLY_MODE
            metadata["max_reply_chars"] = DANMAKU_ROOM_BRIDGE_REPLY_CHARS
        metadata.update(meme_knowledge_metadata(retrieve_meme_knowledge(event.danmaku_text or "", room_context)))
        return metadata

    @staticmethod
    def _anchor_hint(danmaku: str) -> str:
        text = str(danmaku or "").strip()
        first_clause = re.split(r"[\s，,。.!！?？、；;：:]+", text, maxsplit=1)[0] if text else ""
        dense = DanmakuResponseModule._dense_text(first_clause or text)
        if not dense or danmaku_text_rules.is_reaction_only(dense):
            return ""
        if len(dense) <= 6:
            return dense
        for start in range(0, min(len(dense), 10), 2):
            candidate = dense[start : start + 6]
            if len(candidate) >= 2 and not danmaku_text_rules.is_reaction_only(candidate):
                return candidate
        return dense[:6]

    @staticmethod
    def _profile_rules(kind: str) -> list[str]:
        return {
            "idiom_chain_start": [
                "This starts an idiom-chain mini game; accept it and either give one valid first idiom or ask the viewer for the first idiom.",
                "Keep it compact and make the next expected ending character or first move clear.",
                "Do not treat the game invitation as a random topic change.",
            ],
            "idiom_chain_turn": [
                "This is a turn in an idiom-chain mini game; do not ask why the viewer said it.",
                "Continue immediately with one idiom-like answer that starts from the previous idiom's last character, then show the next character to continue.",
                "If the exact chain is tricky, gracefully say the chain is tricky and offer one close continuation; do not leave the game state.",
            ],
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
            "greeting": [
                "For greetings, greet the viewer back first in one short live-friendly line.",
                "Do not turn a greeting into an avatar, ID, first-appearance, or profile-memory comment.",
            ],
            "content_request": [
                "This danmaku asks NEKO to produce concrete content; deliver the content in this reply.",
                "Do not merely acknowledge, promise, or announce that NEKO will do it next.",
                "The visible reply itself must contain the requested result; a promise-only line is a failed reply.",
                "If asked for a joke, include the tiny joke and punchline now.",
                "For a joke request, use a complete tiny setup-and-turn such as one compact 'X thought Y, but Z' bit.",
                "If asked to explain, give the smallest useful answer now before any tease.",
                "If asked to name or invent something, provide one concrete result now.",
            ],
            "external_action_request": [
                "This danmaku asks NEKO to do an external action such as searching, opening, watching, listening, checking, or operating something.",
                "Do not pretend NEKO is performing that action; no lines like I am searching, I will search, I found it, watch after I finish, or give me a moment.",
                "If no actual tool result is present in the prompt, say the boundary once in a live-friendly way and pivot to what can be said now.",
                "Do not negotiate rewards, promises, or deadlines for the action.",
                "Keep the requester name visible, but do not repeat the same refusal if recent context already said it.",
            ],
            "active_hook_answer": [
                "This short danmaku is a viewer answer to NEKO's recent active-engagement hook.",
                "Acknowledge the viewer's answer first; do not ignore it as a low-value tiny reaction.",
                "Do not repeat the old question, do not ask for another vote, and do not launch a new prompt.",
                "Make one compact callback to the answer and stop.",
            ],
            "batch_welcome": [
                "This is a busy-room batch welcome replacing a single viewer avatar/ID roast.",
                "Welcome surfaced viewers as a group in one spontaneous line; do not reuse a fixed welcome phrase or use a fixed welcome template.",
                "Do not name, roast, rank, or describe any individual viewer.",
                "Do not name one viewer, do not mention avatar or ID, and do not imply every newcomer has been individually reviewed.",
            ],
            "target_roast_request": [
                "This danmaku asks NEKO to lightly roast another named viewer; deliver the safe public roast in this reply.",
                "Do not say NEKO does not know, has not met, or cannot judge the target viewer.",
                "Use only the target nickname and current public live-room context; do not invent private facts, history, identity, or relationships.",
                "Make it playful and harmless: tease the name, timing, or live-room moment, not protected traits or real-world status.",
                "Name the target viewer in the first clause so the room can tell who is being roasted.",
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
    def _length_rules(kind: str) -> list[str]:
        if kind in {"content_request", "target_roast_request", "external_action_request", "active_hook_answer"}:
            return [
                "Expanded request length: one or two short TTS-friendly sentences are allowed.",
                "Stay under one small live beat; no paragraph, no setup, no explanation after the punchline.",
                "Do not use a bare preface like 好呀, 那我给你讲, or I can do that unless the same reply also contains the requested content.",
                "Avoid opening with 好呀, 可以, 安排, or 来了 unless the requested content immediately follows in the same sentence.",
                "If the answer can be short and complete, keep it short; expanded length is permission, not a target.",
            ]
        return short_reply_rules()

    @staticmethod
    def _room_bridge_rules(enabled: bool) -> list[str]:
        if not enabled:
            return []
        return [
            "Room bridge length: one compact sentence is preferred, but two short TTS-friendly sentences are allowed when the second sentence bridges the shared room theme.",
            "The first sentence or clause must answer the current viewer; the bridge may only add a tiny room-facing echo.",
            "Do not use the extra length for a new poll, host plan, recap, or generic engagement question.",
        ]

    @staticmethod
    def _allows_room_bridge_length(kind: str, room_context: str) -> bool:
        if kind in {
            "empty",
            "emoji_or_reaction",
            "greeting",
            "viewer_to_viewer_mention",
            "content_request",
            "target_roast_request",
            "external_action_request",
            "active_hook_answer",
        }:
            return False
        return "room_theme=" in str(room_context or "")

    @staticmethod
    def _room_theme_from_context(room_context: str) -> str:
        match = re.search(r"room_theme=([^\r\n]+)", str(room_context or ""))
        if not match:
            return ""
        value = match.group(1).strip()
        value = re.sub(r"\s*\(\d+\s+signals?\)\s*$", "", value).strip()
        return value[:80]

    @staticmethod
    def _dense_text(text: str) -> str:
        lowered = str(text or "").casefold()
        return "".join(ch for ch in lowered if ch.isalnum() or "\u4e00" <= ch <= "\u9fff")

    @staticmethod
    def _looks_like_reaction(text: str, dense: str) -> bool:
        if text.strip() and not dense:
            return True
        if danmaku_text_rules.is_reaction_only(dense):
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
    def _looks_like_content_request(text: str, dense: str) -> bool:
        lowered = str(text or "").casefold()
        markers = (
            "\u8bb2\u4e2a\u7b11\u8bdd",
            "\u8bb2\u7b11\u8bdd",
            "\u7b11\u8bdd",
            "\u6765\u4e2a\u7b11\u8bdd",
            "\u6765\u4e00\u4e2a",
            "\u6765\u4e00\u6bb5",
            "\u7f16\u4e00\u4e2a",
            "\u8bb2\u8bb2",
            "\u8bf4\u8bf4",
            "\u89e3\u91ca",
            "\u8be6\u7ec6",
            "\u5c55\u5f00",
            "\u8d77\u4e2a\u5916\u53f7",
            "\u8d77\u5916\u53f7",
            "tell me a joke",
            "joke",
            "explain",
            "say more",
        )
        return any(marker in lowered or marker in dense for marker in markers)

    @staticmethod
    def _looks_like_external_action_request(text: str, dense: str) -> bool:
        lowered = str(text or "").casefold()
        markers = (
            "\u641c\u4e00\u4e0b",
            "\u641c\u641c",
            "\u641c\u7d22",
            "\u53bb\u641c",
            "\u67e5\u4e00\u4e0b",
            "\u67e5\u67e5",
            "\u53bb\u542c",
            "\u542c\u4e00\u4e0b",
            "\u53bb\u770b",
            "\u770b\u4e00\u4e0b",
            "\u6253\u5f00",
            "\u5e2e\u6211\u627e",
            "\u5e2e\u5fd9\u627e",
            "search",
            "google",
            "look up",
            "listen to",
            "watch",
            "open",
        )
        return any(marker in lowered or marker in dense for marker in markers)

    @staticmethod
    def _looks_like_active_hook_answer(text: str, dense: str, raw: dict[str, object] | None) -> bool:
        if not isinstance(raw, dict) or raw.get("danmaku_context_hint") != "active_hook_answer":
            return False
        return bool(str(text or "").strip()) and 0 < len(dense) <= 8

    @staticmethod
    def _looks_like_target_roast_request(text: str, dense: str) -> bool:
        lowered = str(text or "").casefold()
        markers = (
            "\u5410\u69fd",
            "\u9510\u8bc4",
            "\u8bc4\u4ef7",
            "\u635f\u4e00\u4e0b",
            "\u635f\u635f",
            "\u8c03\u4f83",
            "roast",
            "rate",
        )
        return any(marker in lowered or marker in dense for marker in markers)

    @staticmethod
    def _target_roast_nickname(text: str) -> str:
        cleaned = " ".join(str(text or "").replace("\uff20", "@").strip().split())
        aliases = {"neko", "\u732b\u732b", "\u5c0f\u5929", "\u732b\u5a18"}
        blocked = {
            "",
            "\u6211",
            "\u4f60",
            "\u4ed6",
            "\u5979",
            "\u5b83",
            "\u8fd9\u4e2a",
            "\u90a3\u4e2a",
            "\u732b\u732b",
            "\u732b\u5a18",
            "\u4e3b\u64ad",
            "\u67d0\u4eba",
            "\u67d0\u4f4d",
            "\u8fd9\u4f4d",
            "\u90a3\u4f4d",
            "\u90a3\u8c01",
            "\u5927\u5bb6",
            "\u6240\u6709\u4eba",
            "neko",
            "guy",
            "person",
            "viewer",
            "user",
            "someone",
            "somebody",
            "everyone",
            "everybody",
            "anyone",
            "anybody",
            "them",
            "him",
            "her",
        }
        for part in cleaned.split("@")[1:]:
            target = []
            for ch in part.strip():
                if ch.isspace() or ch in ":：,，。.!！?？、；;|[]()（）<>《》":
                    break
                target.append(ch)
            name = "".join(target).strip("@ \t\r\n")
            if name and name.casefold() not in aliases and name.casefold() not in blocked:
                return name[:24]
        pattern = re.compile(
            r"(?:\u5410\u69fd\u4e00\u4e0b|\u5410\u69fd|\u9510\u8bc4\u4e00\u4e0b|\u9510\u8bc4|\u8bc4\u4ef7\u4e00\u4e0b|\u8bc4\u4ef7|\u635f\u4e00\u4e0b|\u635f\u635f|\u8c03\u4f83\u4e00\u4e0b|\u8c03\u4f83|roast|rate)\s*(?:\u4e00\u4e0b|this|that)?\s*([\w\u4e00-\u9fff][\w\u4e00-\u9fff_-]{0,23})",
            re.IGNORECASE,
        )
        match = pattern.search(cleaned)
        if not match:
            return ""
        name = match.group(1).strip("@ \t\r\n")
        return "" if name.casefold() in blocked else name[:24]

    @staticmethod
    def _looks_like_greeting(text: str, dense: str) -> bool:
        lowered = str(text or "").casefold().strip()
        if not lowered or not dense:
            return False
        greetings = (
            "hi",
            "hello",
            "hey",
            "\u4f60\u597d",
            "\u665a\u4e0a\u597d",
            "\u665a\u597d",
            "\u65e9\u4e0a\u597d",
            "\u65e9\u597d",
            "\u4e2d\u5348\u597d",
            "\u4e0b\u5348\u597d",
            "\u55e8",
            "\u54c8\u55bd",
        )
        return len(dense) <= 8 and any(marker in lowered or marker in dense for marker in greetings)

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
