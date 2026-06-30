"""Build avatar-and-ID roast requests."""

from __future__ import annotations

from typing import Any

from ...core.contracts import InteractionRequest, ViewerEvent, ViewerIdentity, ViewerProfile
from .._base import BaseModule
from .._prompt_context import (
    anti_repeat_rules,
    live_output_quality_rules,
    recent_context_block,
    short_reply_rules,
    sustained_charm_rules,
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
        prompt_text = (
            self._build_idle_hosting_prompt(event, strength, activity_level, recent_context_block(self.ctx))
            if event.source == "idle_hosting"
            else self._build_prompt(
                event,
                identity,
                strength,
                recent_context_block(self.ctx),
                viewer_session_context_block(self.ctx, identity.uid),
            )
        )
        return InteractionRequest(
            event=event,
            identity=identity,
            profile=profile,
            prompt_text=prompt_text,
            live_mode=event.live_mode,
            strength=strength,
            dry_run=bool(self.ctx.config.dry_run) if self.ctx else False,
            allow_avatar_image=event.source != "idle_hosting",
        )

    def _build_idle_hosting_prompt(
        self,
        event: ViewerEvent,
        strength: str,
        activity_level: str = "standard",
        recent_context: str = "",
    ) -> str:
        strength_hint = {
            "gentle": "warm and soft",
            "sharp": "playfully sharp, but still easy to answer",
            "normal": "balanced and lightly playful",
        }.get(strength, "balanced and lightly playful")
        pacing_hint = {
            "quiet": "Prefer a soft observation over a direct question.",
            "active": "You may ask one specific, low-pressure question.",
            "standard": "Use a balanced host beat: one small observation or one easy question.",
        }.get(str(activity_level), "Use a balanced host beat: one small observation or one easy question.")
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
            "Use the host beat material as direction, but make the final line sound natural.",
            "If a NEKO live column is provided, use it as the tiny host format without announcing a formal segment.",
            "Add a low-pressure reply hook: one concrete choice, tiny stance, or small playful prompt.",
            "Use the host beat reply_affordance as the only reply hook; do not add a second question.",
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
            + recent_context
            + host_beat_block
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

    def _build_prompt(
        self,
        event: ViewerEvent,
        identity: ViewerIdentity,
        strength: str,
        recent_context: str = "",
        viewer_context: str = "",
    ) -> str:
        nickname = identity.nickname or identity.uid or "this viewer"
        danmaku = (event.danmaku_text or "").strip()
        avatar_line, avatar_rule = self._avatar_guidance(identity)
        mode_contract = self._mode_contract(event.live_mode)
        pace = (
            "solo_stream: NEKO is the only host on stage; answer the current danmaku first, then stop after one compact line."
            if event.live_mode == "solo_stream"
            else "co_stream: NEKO is a low-interrupt partner; catch one point and leave room for the human streamer."
        )
        strength_hint = {
            "gentle": "soft and warm",
            "sharp": "playfully sharp, but never hostile",
            "normal": "natural, lightly playful, and concise",
        }.get(strength, "natural, lightly playful, and concise")

        facts = [f"viewer: {nickname} (UID {identity.uid})"]
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
            "Make one evidence-based small judgment from a concrete detail; do not vaguely say cute, cool, abstract, or repeat the facts back.",
            *solo_danmaku_priority_rules,
            avatar_rule,
            *short_reply_rules(),
            "Do not use the same opening, sentence shape, punchline, or host beat as recent live replies.",
            *anti_repeat_rules(),
            "Current danmaku wins over any previous reply.",
            "Do not invent or hard-code streamer relationship labels; use profile memory if available, otherwise avoid naming the streamer.",
            f"Tone: {strength_hint}. Pacing: {pace}",
            *live_output_quality_rules(),
            *sustained_charm_rules(),
            "Output only NEKO's one-line first-appearance roast. No explanation, no prefix, no suffix, no rule recap.",
        ]
        return (
            "[NEKO Live first-appearance roast]\n"
            + "\n".join(facts)
            + "\n\n"
            + recent_context
            + viewer_context
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
            "do not steal the human streamer's host role."
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
