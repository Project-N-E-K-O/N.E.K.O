from __future__ import annotations

import re
from typing import Any

from .pipeline_models import QQDeliveryPlan, QQReplyContext, QQReplyOutcome, QQModelResult

# 解析 LLM 回复中的 XML 控制标签
_RE_REPLY_TAG = re.compile(r"<reply>(.*?)</reply>", re.IGNORECASE)
_RE_AT_TAG = re.compile(r"<at>(.*?)</at>", re.IGNORECASE)
_RE_POKE_TAG = re.compile(r"<poke>(.*?)</poke>", re.IGNORECASE)
_RE_STICKER_TAG = re.compile(r"<sticker>(.*?)</sticker>", re.IGNORECASE)
_RE_KEYBOARD_TAG = re.compile(r"<keyboard>(.*?)</keyboard>", re.IGNORECASE)
_RE_ARK_TAG = re.compile(r"<ark\b([^>]*)>(.*?)</ark>", re.IGNORECASE)
_RE_ARK_ATTR = re.compile(r'(title|desc|pic|btn|url)\s*=\s*"([^"]*)"', re.IGNORECASE)


class QQReplyPostprocessNode:
    def __init__(self, plugin: Any):
        self.plugin = plugin

    @staticmethod
    def _parse_xml_tags(raw_text: str) -> tuple[str, str, str, str, str, str, dict]:
        """从 LLM 回复中提取 XML 控制标签

        Returns:
            (clean_text, reply_message_id, at_user_id, poke_user, sticker_id, keyboard, ark_attrs)
        """
        reply_id = ""
        at_id = ""
        poke_user = ""

        match = _RE_REPLY_TAG.search(raw_text)
        if match:
            reply_id = match.group(1).strip()
            raw_text = _RE_REPLY_TAG.sub("", raw_text, count=1)

        match = _RE_AT_TAG.search(raw_text)
        if match:
            at_id = match.group(1).strip()
            raw_text = _RE_AT_TAG.sub("", raw_text, count=1)

        match = _RE_POKE_TAG.search(raw_text)
        if match:
            poke_user = match.group(1).strip()
            raw_text = _RE_POKE_TAG.sub("", raw_text, count=1)

        sticker_id = ""
        match = _RE_STICKER_TAG.search(raw_text)
        if match:
            sticker_id = match.group(1).strip()
            raw_text = _RE_STICKER_TAG.sub("", raw_text, count=1)

        keyboard = ""
        match = _RE_KEYBOARD_TAG.search(raw_text)
        if match:
            keyboard = match.group(1).strip()
            raw_text = _RE_KEYBOARD_TAG.sub("", raw_text, count=1)

        ark_attrs: dict[str, str] = {}
        match = _RE_ARK_TAG.search(raw_text)
        if match:
            attr_str = match.group(1)
            for am in _RE_ARK_ATTR.finditer(attr_str):
                ark_attrs[am.group(1)] = am.group(2)
            ark_attrs["_body"] = match.group(2).strip()
            raw_text = _RE_ARK_TAG.sub("", raw_text, count=1)

        return raw_text.strip(), reply_id, at_id, poke_user, sticker_id, keyboard, ark_attrs

    def finalize(self, context: QQReplyContext, model_result: QQModelResult) -> QQReplyOutcome:
        raw_reply_text = model_result.reply_text or ""
        reply_text = self.plugin._sanitize_generated_reply(raw_reply_text)
        if raw_reply_text and not reply_text:
            self.plugin._emit_log("INFO", f"[Sanitize] {len(raw_reply_text)}字被清除: {raw_reply_text[:100]}")

        # neko_dynamic: 解析 XML 控制标签
        at_user_id = ""
        reply_message_id = ""
        poke_user = ""
        sticker_id = ""
        keyboard = ""
        ark_attrs: dict[str, str] = {}
        strategy_mode = getattr(self.plugin, "_strategy_mode", "neko_dynamic")
        if strategy_mode == "neko_dynamic" and reply_text:
            before_parse = reply_text
            reply_text, reply_message_id, at_user_id, poke_user, sticker_id, keyboard, ark_attrs = self._parse_xml_tags(reply_text)
            if before_parse and not reply_text:
                self.plugin._emit_log("INFO", f"[ParseTags] {len(before_parse)}字全被标签解析清除: {before_parse[:100]}")

        if reply_text or sticker_id or poke_user or ark_attrs:
            action = "ark" if ark_attrs else ("poke" if poke_user else "reply")
            return QQReplyOutcome(
                action=action,
                reply_text=reply_text,
                raw_reply_text=raw_reply_text,
                postprocess_reason="reply_xml" if strategy_mode == "neko_dynamic" else "reply",
                parsed_reply_message_id=reply_message_id,
                parsed_at_user_id=at_user_id,
                parsed_poke_user=poke_user,
                parsed_sticker_id=sticker_id,
                parsed_keyboard=keyboard,
                parsed_ark=ark_attrs,
            )
        if context.ephemeral_session:
            return QQReplyOutcome(
                action="reply",
                reply_text=None,
                raw_reply_text=raw_reply_text,
                postprocess_reason="empty",
            )
        # neko_dynamic 自判模式：非强制回复时，LLM 不回复就跳过
        strategy_mode = getattr(self.plugin, "_strategy_mode", "neko_dynamic")
        is_forced = getattr(context, "force_reply", False) or context.permission_level == "admin"
        if strategy_mode == "neko_dynamic" and not is_forced:
            return QQReplyOutcome(
                action="reply",
                reply_text=None,
                raw_reply_text=raw_reply_text,
                postprocess_reason="llm_skip",
            )
        return QQReplyOutcome(
            action="reply",
            reply_text=self.plugin.i18n.t("messages.default_no_reply", default="嗯嗯~"),
            used_default_message=True,
            raw_reply_text=raw_reply_text,
            postprocess_reason="default",
        )

    def build_delivery_plan(self, request: Any, outcome: QQReplyOutcome) -> QQDeliveryPlan | None:
        if not outcome.reply_text:
            return None
        if request.is_group:
            strategy_mode = getattr(self.plugin, "_strategy_mode", "neko_dynamic")
            if strategy_mode == "neko_dynamic":
                # Kira 风格：回复和 @ 目标由 LLM 的 XML 标签决定
                reply_message_id = str(outcome.parsed_reply_message_id or "")
                at_user_id = str(outcome.parsed_at_user_id or "")
            else:
                # N.E.K.O 风格：@ 目标由外层场景模式决定
                reply_message_id = request.reply_message_id if request.group_scene_mode == "directed_user" else ""
                at_user_id = request.at_user_id if request.group_scene_mode == "directed_user" else ""
            return QQDeliveryPlan(
                target_type="group",
                target_id=str(request.group_id or ""),
                reply_text=outcome.reply_text,
                reply_message_id=reply_message_id,
                at_user_id=at_user_id,
                keyboard=str(outcome.parsed_keyboard or ""),
                fallback_to_text_on_voice_failure=request.fallback_to_text_on_voice_failure,
            )
        return QQDeliveryPlan(
            target_type="private",
            target_id=request.sender_id,
            reply_text=outcome.reply_text,
            fallback_to_text_on_voice_failure=request.fallback_to_text_on_voice_failure,
        )
