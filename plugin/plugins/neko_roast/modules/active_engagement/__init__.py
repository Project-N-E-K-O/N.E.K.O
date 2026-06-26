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
            prompt_text=self._build_prompt(
                strength,
                activity_level,
                recent_context_block(self.ctx),
                event.raw.get("topic_material") if isinstance(event.raw, dict) else None,
            ),
            live_mode=event.live_mode,
            strength=strength,
            dry_run=bool(self.ctx.config.dry_run) if self.ctx else False,
        )

    @staticmethod
    def _build_prompt(
        strength: str,
        activity_level: str = "standard",
        recent_context: str = "",
        topic_material: object | None = None,
    ) -> str:
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
        topic_block = ActiveEngagementModule._topic_material_block(topic_material)
        rules = [
            "NEKO is the only host on stage in solo_stream.",
            "Create exactly one small live-room engagement beat as NEKO.",
            "Make it specific enough that a viewer can naturally reply, without begging for comments.",
            "Use the topic material as raw material only; transform it into NEKO's own live-room line.",
            "Follow the requested topic shape when present: either_or, light_stance, tiny_tease, or small_challenge.",
            "Every active engagement line must give viewers one concrete reply handle.",
            "The reply handle must be an A/B choice, one-word answer, tiny stance, or playful yes/no-with-a-side.",
            "Prefer one tiny observation over a plan, segment, or open-ended topic survey.",
            "Do not use generic host slogans like 'everyone interact' or 'say something in chat'.",
            "Never address the whole room with broad audience-bait openings like everyone, anyone, chat, 大家, or 你们.",
            "Do not use generic Chinese host lines equivalent to 'everyone interact', 'start sending danmaku', or 'come chat'.",
            "Do not say special plan, everyone look, next let's, what should we talk about, or tell me what you want.",
            "Do not say get the chat moving, keep the chat alive, or keep the chat going.",
            "Do not say \u5927\u5bb6\u5feb\u6765\u4e92\u52a8, \u5f39\u5e55\u5237\u8d77\u6765, \u63a5\u4e0b\u6765\u6211\u4eec, or \u7279\u522b\u4f01\u5212.",
            "Do not ask viewers what they want to hear.",
            "Do not ask viewers to choose the stream topic for NEKO.",
            "Do not mention silence, metrics, cooldowns, queues, dry_run, or system state.",
            "Do not pretend a viewer sent a message.",
            "Do not invent or hard-code streamer relationship labels; use profile memory if available, otherwise avoid naming the streamer.",
            "Keep one short TTS-friendly line.",
            *short_reply_rules(kind="host"),
            "Output only NEKO's line.",
        ]
        return (
            "[NEKO Live active engagement]\n"
            "scene: solo_stream quiet moment\n"
            f"tone: {strength_hint}\n"
            f"pacing: {activity_level}\n"
            f"pacing rule: {activity_hint}\n\n"
            + topic_block
            + recent_context
            + "\nRules:\n"
            + "\n".join(f"- {rule}" for rule in rules)
        )

    @staticmethod
    def _topic_material_block(topic_material: object | None) -> str:
        if not isinstance(topic_material, dict):
            return ""
        source = str(topic_material.get("source") or "fallback").strip()
        shape = str(topic_material.get("shape") or "").strip()
        title = str(topic_material.get("title") or "").strip()
        hook = str(topic_material.get("hook") or "").strip()
        pattern = str(topic_material.get("pattern") or "").strip()
        intent = str(topic_material.get("intent") or "").strip()
        reply_affordance = str(topic_material.get("reply_affordance") or "").strip()
        hint = str(topic_material.get("hint") or "").strip()
        lines = [
            "Topic material:",
            f"- source: {source}",
        ]
        if shape:
            lines.append(f"- shape: {shape}")
            lines.append(f"- shape task: {ActiveEngagementModule._shape_task_text(shape)}")
            lines.append(f"- example pattern: {pattern or ActiveEngagementModule._shape_example_text(shape)}")
        if title:
            lines.append(f"- title: {title}")
        if hook:
            lines.append(f"- hook: {hook}")
        if intent:
            lines.append(f"- intent: {intent}")
        if reply_affordance:
            lines.append(f"- viewer reply path: {reply_affordance}")
        if hint:
            lines.append(f"- hint: {hint}")
        return "\n".join(lines) + "\n\n"

    @staticmethod
    def _shape_task_text(shape: str) -> str:
        return {
            "either_or": "turn the title into one A/B choice; make both options concrete and avoid yes/no questions.",
            "light_stance": "give one small NEKO-flavored stance that viewers can agree or disagree with quickly.",
            "tiny_tease": "make one tiny playful tease about the topic without attacking viewers or sounding hostile.",
            "small_challenge": "offer one tiny low-pressure challenge that viewers can answer in a few words.",
        }.get(shape, "make one specific, low-pressure hook that viewers can answer quickly.")

    @staticmethod
    def _shape_example_text(shape: str) -> str:
        return {
            "either_or": "turn the title into two concrete sides, then let viewers pick one.",
            "light_stance": "state one tiny NEKO opinion, then leave room for viewers to push back.",
            "tiny_tease": "make one small playful jab about the topic, then stop before it becomes a bit.",
            "small_challenge": "ask for one tiny answer viewers can type in a few words.",
        }.get(shape, "make one concrete reply point viewers can answer quickly.")
