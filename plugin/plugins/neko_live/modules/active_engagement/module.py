"""Build solo-stream active engagement requests."""

from __future__ import annotations

from ...core.contracts import InteractionRequest, ViewerEvent, ViewerIdentity, ViewerProfile
from ...core.contracts_public import public_text
from ...core.live_host_theme import live_host_theme_block
from .._base import BaseModule
from .._prompt_context import (
    anti_repeat_rules,
    live_output_quality_rules,
    recent_context_block,
    short_reply_rules,
    sustained_charm_rules,
)


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
        topic_material = event.raw.get("topic_material") if isinstance(event.raw, dict) else None
        return InteractionRequest(
            event=event,
            identity=identity,
            profile=profile,
            prompt_text=self._build_prompt(
                strength,
                activity_level,
                live_host_theme_block(self.ctx, kind="host"),
                recent_context_block(self.ctx),
                topic_material,
            ),
            live_mode=event.live_mode,
            strength=strength,
            dry_run=bool(self.ctx.config.dry_run) if self.ctx else False,
            metadata=self._metadata_for_topic(topic_material),
        )

    @staticmethod
    def _build_prompt(
        strength: str,
        activity_level: str = "standard",
        host_theme_context: str = "",
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
            "active": "You may ask one concrete, low-pressure question, but answers must be words in danmaku, not numbers.",
            "standard": "Use one small observation or one concrete easy question.",
        }.get(str(activity_level), "Use one small observation or one concrete easy question.")
        topic_block = ActiveEngagementModule._topic_material_block(topic_material)
        rules = [
            "NEKO is the only host on stage in solo_stream.",
            "Create exactly one small live-room engagement beat as NEKO.",
            "Make it specific enough that a viewer can naturally reply, without begging for comments.",
            "Do not address an unseen human host, owner, or operator; do not ask them to give NEKO a topic.",
            "Solo-stream agency: NEKO is the only on-stage host and must perform all hosting actions herself.",
            "Never tell or ask an unseen streamer, operator, or current viewer to greet viewers, warm up the room, carry chat, or provide content.",
            "Never tell or ask an unseen streamer, operator, or current viewer to greet the room, warm up the stream, carry the chat, provide topics, or help NEKO host.",
            "Do not mention owner, master, backstage human, carbon-based human, private chat, or pre-stream relationship memory.",
            "In solo_stream, 'you' means the viewers or room, never an unseen operator.",
            "Output spoken live speech only; never include parenthesized stage directions, action narration, or roleplay asides.",
            "Start from a clear anchor: mention the concrete thing NEKO noticed before asking anything.",
            "Use the topic material as raw material only; transform it into NEKO's own live-room line.",
            "If recent-thread evidence is provided, continue that shared room thread instead of starting a new generic topic.",
            "If the topic is technical, game-specific, or unfamiliar, do not pretend expertise; make a light surface reaction instead.",
            "If a NEKO live column is provided, use it as the tiny engagement format without announcing a formal segment.",
            "Follow the requested topic shape when present: either_or, light_stance, tiny_tease, or small_challenge.",
            "Every active engagement line may give viewers one concrete non-numeric danmaku cue.",
            "Use the provided viewer reply path as the only reply cue; do not add a second question.",
            "Use the provided viewer reply path as the only reply handle; do not add a second question.",
            "Use the provided fun axis as the line's purpose; do not drift into generic hosting.",
            "The reply handle must be an A/B choice by words, one-word answer, tiny stance, or playful yes/no-with-a-side.",
            "Only use A/B when both sides are obvious, ordinary, and complete; otherwise make one tiny stance instead.",
            "Prefer one tiny observation over a plan, segment, or open-ended topic survey.",
            "The final line must be a complete sentence; never end with an unfinished word or dangling 'or'.",
            "Do not use punishment, public-shaming, trial, labor-camp, or real-person judgment language.",
            "Do not say \u516c\u5f00\u793a\u4f17, \u52b3\u6539, \u5ba1\u5224, \u5904\u5211, or \u60e9\u7f5a.",
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
            *live_output_quality_rules(kind="host"),
            *sustained_charm_rules(kind="host"),
            "Keep one short TTS-friendly line.",
            *anti_repeat_rules(kind="host"),
            *short_reply_rules(kind="host"),
            "Output only NEKO's line.",
        ]
        return (
            "[NEKO Live active engagement]\n"
            "scene: solo_stream quiet moment\n"
            f"tone: {strength_hint}\n"
            f"pacing: {activity_level}\n"
            f"pacing rule: {activity_hint}\n\n"
            + host_theme_context
            + topic_block
            + recent_context
            + "\nRules:\n"
            + "\n".join(f"- {rule}" for rule in rules)
        )

    @staticmethod
    def _topic_material_block(topic_material: object | None) -> str:
        if not isinstance(topic_material, dict):
            return ""
        def topic_text(key: str, *, max_len: int = 120) -> str:
            value = topic_material.get(key)
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                value = str(value)
            return public_text(value, max_len=max_len)

        source = topic_text("source") or "fallback"
        shape = topic_text("shape")
        title = topic_text("title")
        fun_axis = topic_text("fun_axis")
        family = topic_text("family")
        hook = topic_text("hook")
        pattern = topic_text("pattern")
        intent = topic_text("intent")
        live_column = topic_text("live_column")
        topic_pack = topic_text("topic_pack")
        reply_affordance = topic_text("reply_affordance")
        interest = topic_text("interest")
        relevance = topic_text("relevance", max_len=40)
        risk = topic_text("risk", max_len=40)
        hint = topic_text("hint")
        evidence = topic_material.get("evidence")
        lines = [
            "Topic material:",
            f"- source: {source}",
        ]
        if interest:
            lines.append(f"- interest: {interest}")
        if relevance:
            lines.append(f"- relevance: {relevance}")
        if risk:
            lines.append(f"- risk: {risk}")
        if shape:
            lines.append(f"- shape: {shape}")
            lines.append(f"- shape task: {ActiveEngagementModule._shape_task_text(shape)}")
            lines.append(f"- example pattern: {pattern or ActiveEngagementModule._shape_example_text(shape)}")
        if title:
            lines.append(f"- title: {title}")
        if fun_axis:
            lines.append(f"- fun axis: {fun_axis}")
        if family:
            lines.append(f"- content family: {family}")
        if hook:
            lines.append(f"- hook: {hook}")
        if intent:
            if intent == "quick_vote":
                lines.append("- intent: word_choice_in_danmaku (internal quick_vote; never use numeric voting)")
            else:
                lines.append(f"- intent: {intent}")
        if live_column:
            lines.append(f"- NEKO live column: {live_column}")
        if topic_pack:
            lines.append(f"- topic pack: {topic_pack}")
        if reply_affordance:
            lines.append(f"- viewer reply path: {reply_affordance}")
        if isinstance(evidence, list):
            safe_evidence = [
                public_text(item, max_len=120)
                for item in evidence[:3]
                if public_text(item, max_len=120)
            ]
            if safe_evidence:
                lines.append("- recent thread evidence:")
                lines.extend(f"  - {item}" for item in safe_evidence)
        if hint:
            lines.append(f"- hint: {hint}")
        return "\n".join(lines) + "\n\n"

    @staticmethod
    def _metadata_for_topic(topic_material: object | None) -> dict[str, str]:
        if not isinstance(topic_material, dict):
            return {}
        mapping = {
            "source": "topic_source",
            "shape": "topic_shape",
            "key": "topic_key",
            "family": "topic_family",
            "fun_axis": "topic_fun_axis",
            "intent": "topic_intent",
            "reply_affordance": "topic_reply_affordance",
            "recent_topic_skip_reason": "topic_recent_skip_reason",
            "shape_guard_reason": "topic_shape_guard_reason",
        }
        metadata: dict[str, str] = {}
        for source_key, target_key in mapping.items():
            value = public_text(str(topic_material.get(source_key) or ""), max_len=120)
            if value:
                metadata[target_key] = value
        return metadata

    @staticmethod
    def _shape_task_text(shape: str) -> str:
        return {
            "either_or": "turn the title into one A/B choice by words only if both options are obvious and ordinary; otherwise use a tiny stance.",
            "light_stance": "give one small NEKO-flavored stance that viewers can agree or disagree with quickly.",
            "tiny_tease": "make one tiny playful tease about the topic without attacking viewers or sounding hostile.",
            "small_challenge": "offer one tiny low-pressure challenge that viewers can answer in a few words.",
        }.get(shape, "make one specific, low-pressure hook that viewers can answer quickly.")

    @staticmethod
    def _shape_example_text(shape: str) -> str:
        return {
            "either_or": "turn the title into two concrete word sides, then let viewers answer with one side in words.",
            "light_stance": "state one tiny NEKO opinion, then leave room for viewers to push back.",
            "tiny_tease": "make one small playful jab about the topic, then stop before it becomes a bit.",
            "small_challenge": "ask for one tiny answer viewers can type in a few words.",
        }.get(shape, "make one concrete reply point viewers can answer quickly.")
