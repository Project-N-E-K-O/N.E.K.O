"""Build avatar-and-ID roast requests."""

from __future__ import annotations

from typing import Any

from ...core.contracts import InteractionRequest, ViewerEvent, ViewerIdentity, ViewerProfile
from .._base import BaseModule
from .._prompt_context import recent_context_block, short_reply_rules


class AvatarRoastModule(BaseModule):
    id = "avatar_roast"
    title = "弹幕锐评"
    domain = "interaction"

    def config_schema(self) -> list[dict[str, Any]]:
        # 弹幕锐评这个功能自己的参数（功能级，跟功能走；平台级如节奏/队列/急停在「设置」）。
        # name 直接绑现有 RoastConfig 顶层字段（锐评是核心切片，参数本就是顶层）；
        # 未来新功能模块用 config.<id>.* 命名空间，见 docs/ui-architecture.md。
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
            else self._build_prompt(event, identity, strength)
        )
        return InteractionRequest(
            event=event,
            identity=identity,
            profile=profile,
            prompt_text=prompt_text,
            live_mode=event.live_mode,
            strength=strength,
            dry_run=bool(self.ctx.config.dry_run) if self.ctx else False,
        )

    def _build_idle_hosting_prompt(self, event: ViewerEvent, strength: str, activity_level: str = "standard", recent_context: str = "") -> str:
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
            "Make it feel like a spontaneous host beat, with a little NEKO personality and no formal opening.",
            "Do not pretend a viewer sent a message.",
            "Do not use generic welcome slogans, direct interaction requests, or attendance-check lines.",
            "Do not mention viewer absence, silence metrics, queues, timing controls, dry_run, or system state.",
            "Do not invent or hard-code streamer relationship labels; use profile memory if available, otherwise avoid naming the streamer.",
            "Keep it natural, low-pressure, and specific enough to avoid template-hosting.",
            *short_reply_rules(),
            "Output only the line NEKO should say.",
        ]
        return (
            "[NEKO Live solo idle hosting]\n"
            + "\n".join(facts)
            + "\n\n"
            + recent_context
            + "\nRules:\n"
            + "\n".join(f"- {rule}" for rule in rules)
        )

    def _build_prompt(self, event: ViewerEvent, identity: ViewerIdentity, strength: str) -> str:
        nickname = identity.nickname or identity.uid or "这位观众"
        danmaku = (event.danmaku_text or "").strip()
        avatar_line, avatar_rule = self._avatar_guidance(identity)
        mode_contract = self._mode_contract(event.live_mode)
        pace = (
            "你在独播、由你撑全场，可以更主动鲜活，别冷场"
            if event.live_mode == "solo_stream"
            else "人猫同播、低打断，自然接一句就好"
        )
        strength_zh = {"gentle": "轻一点、偏宠溺", "sharp": "可以犀利", "normal": "适中"}.get(strength, "适中")

        facts = [f"观众昵称：{nickname}（UID {identity.uid}）"]
        facts.append(f"mode_contract: {mode_contract}")
        if danmaku:
            facts.append(f"刚发的弹幕：{danmaku}")
        facts.append(f"头像：{avatar_line}")
        if identity.pendant:
            facts.append(f"头像挂件/装扮：{identity.pendant}")

        rules = [
            "自适应焦点：昵称和头像哪个更有梗就主打哪个；两个都有料就抓它们之间的反差或呼应；都平淡就拿这条弹幕、进场时机或当前直播节奏发挥，别硬尬夸。",
            "抓一个具体细节切入并给个有依据的小判断，别泛泛说“好可爱”，别逐字复述上面的字段。",
            avatar_rule,
            *short_reply_rules(),
            "别和你最近几条锐评用同样的开头和句式。",
            "Current danmaku wins over any previous reply.",
            "Do not invent or hard-code streamer relationship labels; use profile memory if available, otherwise avoid naming the streamer.",
            f"一句话，短、有包袱、能直接 TTS 播出；强度{strength_zh}；{pace}。",
            "只输出这一句锐评本身，不要解释、不要加前后缀、不要复述这些规则。",
        ]
        return (
            "【直播观众锐评】请按当前人设，对这位新观众即兴说一句直播锐评。\n"
            + "\n".join(facts)
            + "\n\n要求：\n"
            + "\n".join(f"- {r}" for r in rules)
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
        """返回 (头像情况描述, 头像锐评规则)。看不到的一律禁止脑补。"""
        if not identity.avatar_vision_ok:
            extra = "（疑似动图/特殊头像）" if identity.is_animated_avatar else ""
            return (
                f"没取到或识别不了{extra}",
                "你看不到这张头像的画面，绝对不要脑补描述它；只能就“头像没换/会动/带挂件”这类事实或昵称发挥。",
            )
        if identity.is_default_avatar:
            return (
                "B站默认头像（根本没换过）",
                "这是默认头像、没有可锐评的画面，别假装看到了什么；从“懒得换头像”或昵称切入。",
            )
        kind = "会动的动图头像" if identity.is_animated_avatar else "能看到的头像图"
        return (
            f"{kind}（图片会一起发给你看）",
            "你能看到这张头像，可以锐评它的具体内容；但只评你真看到的，别编。",
        )
