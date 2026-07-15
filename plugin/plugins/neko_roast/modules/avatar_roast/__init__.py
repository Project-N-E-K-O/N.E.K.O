"""Build avatar-and-ID roast requests."""

from __future__ import annotations

from typing import Any

from ...core.contracts import InteractionRequest, ViewerEvent, ViewerIdentity, ViewerProfile
from ...core.live_host_theme import live_host_theme_block
from ...core.meme_knowledge import meme_knowledge_metadata, render_meme_knowledge_block, retrieve_meme_knowledge
from ...core.viewer_addressing import viewer_address_name
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


class AvatarRoastModule(BaseModule):
    id = "avatar_roast"
    title = "首次出场锐评"
    domain = "interaction"

    def config_schema(self) -> list[dict[str, Any]]:
        # Module-level controls for first-appearance roasting. Platform-level
        # controls such as pacing and pause stay in the Settings tab.
        return [
            {
                "name": "roast_strength",
                "type": "select",
                "label": "panel.fields.strength",
                "default": "normal",
                "options": [
                    {"value": "gentle", "label": "panel.strength.gentle"},
                    {"value": "normal", "label": "panel.strength.normal"},
                    {"value": "sharp", "label": "panel.strength.sharp"},
                ],
            },
            {
                "name": "roast_once_per_uid",
                "type": "boolean",
                "label": "panel.fields.oncePerUid",
                "hint": "panel.fields.oncePerUidHint",
                "default": True,
            },
        ]

    def build_request(
        self,
        event: ViewerEvent,
        identity: ViewerIdentity,
        profile: ViewerProfile,
    ) -> InteractionRequest:
        strength = self.ctx.config.roast_strength if self.ctx else "normal"
        activity_level = self.ctx.config.activity_level if self.ctx else "standard"
        metadata: dict[str, Any] = {}
        if event.source == "idle_hosting":
            recent_context = recent_context_block(self.ctx)
            host_beat = event.raw.get("host_beat") if isinstance(event.raw, dict) else None
            meme_entries = self._idle_hosting_meme_entries(host_beat)
            prompt_text = self._build_idle_hosting_prompt(
                event,
                strength,
                activity_level,
                live_host_theme_block(self.ctx, kind="host"),
                recent_context,
                render_meme_knowledge_block(meme_entries),
            )
            metadata.update(meme_knowledge_metadata(meme_entries))
        else:
            prompt_text = self._build_prompt(
                event,
                identity,
                profile,
                strength,
                live_host_theme_block(self.ctx, kind="reply"),
                recent_context_block(self.ctx),
                viewer_session_context_block(self.ctx, identity.uid),
                viewer_preference_context_block(self.ctx, profile),
                live_events_context_block(self.ctx, event),
            )
        return InteractionRequest(
            event=event,
            identity=identity,
            profile=profile,
            prompt_text=prompt_text,
            live_mode=event.live_mode,
            strength=strength,
            dry_run=bool(self.ctx.config.dry_run) if self.ctx else False,
            allow_avatar_image=(
                event.source != "idle_hosting"
                and bool(getattr(self.ctx.config, "avatar_analysis_enabled", True))
            ) if self.ctx else False,
            metadata=metadata,
        )

    def _build_idle_hosting_prompt(
        self,
        event: ViewerEvent,
        strength: str,
        activity_level: str = "standard",
        host_theme_context: str = "",
        recent_context: str = "",
        meme_context: str = "",
    ) -> str:
        strength_hint = {
            "gentle": "warm and soft",
            "sharp": "playfully sharp, but still easy to answer",
            "normal": "balanced and lightly playful",
        }.get(strength, "balanced and lightly playful")
        pacing_hint = {
            "quiet": "Prefer a soft observation over a direct question.",
            "active": "You may ask one specific, low-pressure question, but never as a numeric vote.",
            "standard": "Use a balanced host beat: one small observation or one non-numeric danmaku cue.",
        }.get(str(activity_level), "Use a balanced host beat: one small observation or one non-numeric danmaku cue.")
        host_beat_block = self._idle_hosting_beat_block(event.raw.get("host_beat") if isinstance(event.raw, dict) else None)
        facts = [
            "scene: NEKO is the only host on stage in solo_stream",
            "task: solo idle hosting",
            "goal: sound like NEKO hosting the room, not a system filler",
            f"tone: {strength_hint}",
            f"pacing: {activity_level}",
            f"pacing rule: {pacing_hint}",
        ]
        rules = [
            "Say exactly one short live-host line as NEKO.",
            "Create one tiny live-room topic: a small observation, a light tease, or an easy question that a quiet viewer can answer.",
            "Treat the idle gap as normal stage time; do not announce that the room is quiet, empty, cold, or waiting.",
            "Do not start with filler like while we wait, since nobody is talking, or to warm things up.",
            "Use the host beat material as direction, but make the final line sound natural.",
            "If a meme hint is present, treat it as optional seasoning for the host beat, not as the reason to speak.",
            "Do not change the host beat just to use a meme.",
            "If a NEKO live column is provided, use it as the tiny host format without announcing a formal segment.",
            "Add at most one low-pressure non-numeric danmaku cue: one concrete choice, tiny stance, or small playful prompt.",
            "Use the host beat reply_affordance as the only reply cue; do not add a second question.",
            "Do not ask viewers to type, drop, reply, vote, or press 1/2/3/4; use 'put it in danmaku' wording instead.",
            "Use the host beat fun_axis as the line's purpose; do not drift into generic hosting.",
            "Make it feel like a spontaneous host beat, with a little NEKO personality and no formal opening.",
            "The final line must be a complete sentence; never end with an unfinished word or dangling choice.",
            "Do not use punishment, public-shaming, trial, labor-camp, or real-person judgment language.",
            "Do not say \u516c\u5f00\u793a\u4f17, \u52b3\u6539, \u5ba1\u5224, \u5904\u5211, or \u60e9\u7f5a.",
            "Do not pretend a viewer sent a message.",
            "Do not announce that nobody is talking or that the room is silent.",
            "Do not use generic welcome slogans, direct interaction requests, or attendance-check lines.",
            "Do not mention viewer absence, silence metrics, queues, timing controls, dry_run, or system state.",
            "Do not invent or hard-code streamer relationship labels; use profile memory if available, otherwise avoid naming the streamer.",
            "Do not mention owner, master, backstage human, carbon-based human, private chat, or pre-stream relationship memory.",
            "Solo-stream agency: NEKO is the only on-stage host and must perform all hosting actions herself.",
            "Never tell or ask an unseen streamer, operator, or current viewer to greet the room, warm up the stream, carry the chat, provide topics, or help NEKO host.",
            "In solo_stream, 'you' means the viewers or room, never an unseen operator.",
            "Output spoken live speech only; never include parenthesized stage directions, action narration, or roleplay asides.",
            "Keep it natural, low-pressure, and specific enough to avoid template-hosting.",
            *live_output_quality_rules(kind="host"),
            *sustained_charm_rules(kind="host"),
            *anti_repeat_rules(kind="host"),
            *short_reply_rules(kind="host"),
            "Output only the line NEKO should say.",
        ]
        return (
            "[NEKO Live solo idle hosting]\n"
            + "\n".join(facts)
            + "\n\n"
            + host_theme_context
            + recent_context
            + host_beat_block
            + meme_context
            + "\nRules:\n"
            + "\n".join(f"- {rule}" for rule in rules)
        )

    @staticmethod
    def _idle_hosting_beat_block(host_beat: object | None) -> str:
        if not isinstance(host_beat, dict):
            return ""
        shape = str(host_beat.get("shape") or "").strip()
        fun_axis = str(host_beat.get("fun_axis") or "").strip()
        family = str(host_beat.get("family") or "").strip()
        title = str(host_beat.get("title") or "").strip()
        hint = str(host_beat.get("hint") or "").strip()
        live_column = str(host_beat.get("live_column") or "").strip()
        idle_stage = str(host_beat.get("idle_stage") or "").strip()
        reply_affordance = str(host_beat.get("reply_affordance") or "").strip()
        if not any((shape, fun_axis, family, title, hint, live_column, idle_stage, reply_affordance)):
            return ""
        lines = ["Host beat material:"]
        if shape:
            lines.append(f"- shape: {shape}")
        if fun_axis:
            lines.append(f"- fun_axis: {fun_axis}")
        if family:
            lines.append(f"- content_family: {family}")
        if title:
            lines.append(f"- title: {title}")
        if hint:
            lines.append(f"- hint: {hint}")
        if live_column:
            lines.append(f"- NEKO live column: {live_column}")
        if idle_stage:
            lines.append(f"- idle_stage: {idle_stage}")
        if reply_affordance:
            lines.append(f"- reply_affordance: {reply_affordance}")
        return "\n" + "\n".join(lines) + "\n\n"

    @staticmethod
    def _idle_hosting_meme_entries(host_beat: object | None) -> list[Any]:
        if not isinstance(host_beat, dict):
            return []
        query_parts = [
            str(host_beat.get("meme_query") or ""),
            str(host_beat.get("title") or ""),
            str(host_beat.get("hint") or ""),
            str(host_beat.get("live_column") or ""),
            str(host_beat.get("reply_affordance") or ""),
        ]
        return retrieve_meme_knowledge(*query_parts, limit=1)

    def _build_prompt(
        self,
        event: ViewerEvent,
        identity: ViewerIdentity,
        profile: ViewerProfile,
        strength: str,
        host_theme_context: str = "",
        recent_context: str = "",
        viewer_context: str = "",
        viewer_preference_context: str = "",
        danmaku_context: str = "",
    ) -> str:
        raw_nickname = identity.nickname or identity.uid or "this viewer"
        nickname = viewer_address_name(raw_nickname, profile) or raw_nickname
        danmaku = (event.danmaku_text or "").strip()
        avatar_line, avatar_rule = self._avatar_guidance(identity)
        mode_contract = self._mode_contract(event.live_mode)
        pace = (
            "solo_stream: NEKO is the only host on stage; answer the current danmaku first, then stop after one compact line."
            if event.live_mode == "solo_stream"
            else "co_stream: NEKO is a low-interrupt partner; catch one point, never direct the streamer/operator/current viewer to host for NEKO, and leave room for the human streamer."
        )
        strength_hint = {
            "gentle": "soft and warm",
            "sharp": "playfully sharp, but never hostile",
            "normal": "natural, lightly playful, and concise",
        }.get(strength, "natural, lightly playful, and concise")

        facts = [f"viewer: {nickname} (UID {identity.uid})"]
        if nickname != raw_nickname:
            facts.append(f"viewer_full_nickname: {raw_nickname}")
        facts.append(f"mode_contract: {mode_contract}")
        if danmaku:
            facts.append(f"current danmaku: {danmaku}")
        facts.append(f"avatar: {avatar_line}")
        if identity.pendant:
            facts.append(f"avatar pendant / decoration: {identity.pendant}")

        solo_danmaku_priority_rules = (
            [
                "solo_stream first-appearance priority: current danmaku first.",
                "Use avatar and nickname only as accents after answering the current danmaku.",
                "Do not turn a first appearance into a pure avatar or ID roast when the viewer sent a danmaku.",
            ]
            if event.live_mode == "solo_stream" and danmaku
            else []
        )
        rules = [
            "Adapt the focus: nickname, avatar, or current danmaku can be the hook; use whichever has the clearest live-room material.",
            "If the viewer sent danmaku, answer that line first, then optionally add one tiny avatar or nickname accent.",
            "If this is a delayed chance to roast because an earlier first-appearance roast did not actually reach NEKO, make it feel like spontaneous live banter; never say this is a makeup, retry, missed first roast, or system correction.",
            "If the current danmaku asks NEKO to roast or evaluate the viewer/avatar/name, satisfy it directly with a natural witty line rather than announcing a delayed retry.",
            "Treat UID as a routing identity, not default roast material; do not quote raw UID digits unless the nickname itself is missing or the viewer explicitly makes the ID relevant.",
            "Make one evidence-based small judgment from a concrete detail; do not vaguely say cute, cool, abstract, or repeat the facts back.",
            *solo_danmaku_priority_rules,
            avatar_rule,
            *short_reply_rules(),
            "Do not use the same opening, sentence shape, punchline, or host beat as recent live replies.",
            "Never invent pinyin initials, Latin initials, or all-letter abbreviations for a Chinese nickname; use preferred short Chinese address when present, otherwise use the full nickname.",
            *anti_repeat_rules(),
            "Current danmaku wins over any previous reply.",
            "Do not invent or hard-code streamer relationship labels; use profile memory if available, otherwise avoid naming the streamer.",
            "Do not mention owner, master, backstage human, carbon-based human, private chat, or pre-stream relationship memory.",
            "In solo_stream, 'you' means the current viewer or room, never an unseen operator.",
            "Output spoken live speech only; never include parenthesized stage directions, action narration, or roleplay asides.",
            f"Tone: {strength_hint}. Pacing: {pace}",
            *live_output_quality_rules(),
            *sustained_charm_rules(),
            "Output only NEKO's one-line first-appearance roast. No explanation, no prefix, no suffix, no rule recap.",
        ]
        return (
            "[NEKO Live first-appearance roast]\n"
            + "\n".join(facts)
            + "\n\n"
            + host_theme_context
            + recent_context
            + viewer_context
            + viewer_preference_context
            + danmaku_context
            + "Rules:\n"
            + "\n".join(f"- {rule}" for rule in rules)
        )

    @staticmethod
    def _mode_contract(live_mode: str) -> str:
        if live_mode == "solo_stream":
            return (
                "solo_stream first-appearance contract - NEKO is carrying the room alone; "
                "make one compact host reaction, then stop."
            )
        return (
            "co_stream first-appearance contract - NEKO is a low-interrupt partner; "
            "do not steal the human streamer's host role, and do not direct the streamer/operator/current viewer to greet viewers, warm up the room, carry chat, provide topics, or help NEKO host."
        )

    @staticmethod
    def _avatar_guidance(identity: ViewerIdentity) -> tuple[str, str]:
        """Return avatar facts and guidance. Never invent details for unseen avatars."""
        if not identity.avatar_vision_ok:
            extra = " (animated or special avatar suspected)" if identity.is_animated_avatar else ""
            return (
                f"not fetched or not visible{extra}",
                "You cannot see this avatar image; never invent visual details. Only use the fact that the avatar was not available, may be animated, has a pendant, or use the nickname/current danmaku instead.",
            )
        if identity.is_default_avatar:
            return (
                "Bilibili default avatar",
                "This is the default avatar; do not pretend to see specific visual details. You may tease the default-avatar choice or pivot to nickname/current danmaku.",
            )
        kind = "animated avatar image" if identity.is_animated_avatar else "visible avatar image"
        return (
            f"{kind} (image will be provided to the model)",
            "You may roast concrete details that are actually visible in the avatar, but never invent details.",
        )
