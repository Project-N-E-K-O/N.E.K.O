from __future__ import annotations

import xml.etree.ElementTree as ET
from typing import Any

from .pipeline_models import QQDeliveryPlan, QQMessageBlock, QQReplyContext, QQReplyOutcome, QQModelResult


class QQReplyPostprocessNode:
    def __init__(self, plugin: Any):
        self.plugin = plugin

    @staticmethod
    def _parse_blocks(raw_text: str) -> list[QQMessageBlock]:
        """KiraAI-style `<msg>` 块解析器。将 LLM 输出解析为消息块列表。

        支持格式:
          <msg><text>文字</text><emoji>277</emoji></msg>
          <msg><sticker>5</sticker></msg>
          <msg><poke>123456</poke></msg>
          <msg><record>语音文本</record></msg>
        纯文本（无 <msg> 标签）回退为单块纯文本。
        """
        text = (raw_text or "").strip()
        if not text:
            return []

        # 检查是否包含 <msg> 标签 → XML 解析
        if "<msg>" not in text and "<msg " not in text:
            # 检查是否使用旧式标签（<reply> <at> <sticker> <poke> <record> <keyboard>）
            if any(tag in text for tag in ("<reply>", "<at>", "<sticker>", "<poke>", "<record>", "<keyboard>")):
                return QQReplyPostprocessNode._parse_legacy_tags(text)
            # 纯文本当作一个块
            block = QQMessageBlock(text=text)
            return [block] if text else []

        # XML 解析
        try:
            root = ET.fromstring(f"<root>{text}</root>")
        except ET.ParseError:
            # 解析失败 → 回退纯文本（去除 XML 标签）
            import re as _re
            clean = _re.sub(r"<[^>]+>", "", text).strip()
            block = QQMessageBlock(text=clean or text)
            return [block] if (clean or text) else []

        blocks: list[QQMessageBlock] = []
        for msg_el in root.findall("msg"):
            block = QQMessageBlock()

            # <text>
            text_el = msg_el.find("text")
            if text_el is not None and text_el.text:
                block.text = text_el.text.strip()

            # <emoji>
            emoji_el = msg_el.find("emoji")
            if emoji_el is not None and emoji_el.text:
                block.emoji = emoji_el.text.strip()

            # <at>
            at_el = msg_el.find("at")
            if at_el is not None and at_el.text:
                block.at_user = at_el.text.strip()

            # <reply>
            reply_el = msg_el.find("reply")
            if reply_el is not None and reply_el.text:
                block.reply_to = reply_el.text.strip()

            # <sticker>
            sticker_el = msg_el.find("sticker")
            if sticker_el is not None and sticker_el.text:
                block.sticker = sticker_el.text.strip()

            # <poke>
            poke_el = msg_el.find("poke")
            if poke_el is not None and poke_el.text:
                block.poke = poke_el.text.strip()

            # <record>
            record_el = msg_el.find("record")
            if record_el is not None and record_el.text:
                block.record = record_el.text.strip()

            # <keyboard>
            kb_el = msg_el.find("keyboard")
            if kb_el is not None and kb_el.text:
                block.keyboard = kb_el.text.strip()

            # <ark> with attrs
            ark_el = msg_el.find("ark")
            if ark_el is not None:
                for k, v in ark_el.attrib.items():
                    block.ark[k] = str(v)
                if ark_el.text:
                    block.ark["_body"] = ark_el.text.strip()

            # 如果没有任何子元素但有直接文本（裸 <msg>text</msg>）
            if not any([
                block.text, block.emoji, block.at_user, block.reply_to,
                block.sticker, block.poke, block.record, block.keyboard, block.ark,
            ]) and msg_el.text:
                block.text = msg_el.text.strip()

            blocks.append(block)

        # 空块列表 → 回退
        if not blocks:
            return [QQMessageBlock(text=text)]

        return blocks

    @classmethod
    def _parse_legacy_tags(cls, raw_text: str) -> list[QQMessageBlock]:
        """向后兼容：解析旧式散落标签（<reply> <at> <sticker> <poke> <record> <keyboard>）。
        将其转换为单个 QQMessageBlock（含标签属性）+ 可能的 sticker/poke 独立块。
        """
        import re
        text = raw_text

        reply_id = ""; at_id = ""; poke_user = ""; sticker_id = ""; voice_text = ""; keyboard = ""

        m = re.search(r"<reply>(.*?)</reply>", text, re.IGNORECASE)
        if m: reply_id = m.group(1).strip(); text = re.sub(r"<reply>.*?</reply>", "", text, count=1, flags=re.IGNORECASE)

        m = re.search(r"<at>(.*?)</at>", text, re.IGNORECASE)
        if m: at_id = m.group(1).strip(); text = re.sub(r"<at>.*?</at>", "", text, count=1, flags=re.IGNORECASE)

        m = re.search(r"<poke>(.*?)</poke>", text, re.IGNORECASE)
        if m: poke_user = m.group(1).strip(); text = re.sub(r"<poke>.*?</poke>", "", text, count=1, flags=re.IGNORECASE)

        m = re.search(r"<sticker>(.*?)</sticker>", text, re.IGNORECASE)
        if m: sticker_id = m.group(1).strip(); text = re.sub(r"<sticker>.*?</sticker>", "", text, count=1, flags=re.IGNORECASE)

        m = re.search(r"<record>(.*?)</record>", text, re.IGNORECASE)
        if m: voice_text = m.group(1).strip(); text = re.sub(r"<record>.*?</record>", "", text, count=1, flags=re.IGNORECASE)

        m = re.search(r"<keyboard>(.*?)</keyboard>", text, re.IGNORECASE)
        if m: keyboard = m.group(1).strip(); text = re.sub(r"<keyboard>.*?</keyboard>", "", text, count=1, flags=re.IGNORECASE)

        clean = text.strip()
        blocks: list[QQMessageBlock] = []

        if poke_user:
            blocks.append(QQMessageBlock(poke=poke_user))
        if clean or reply_id or at_id or voice_text or keyboard:
            blocks.append(QQMessageBlock(
                text=clean, reply_to=reply_id, at_user=at_id,
                record=voice_text, keyboard=keyboard,
            ))
        if sticker_id:
            blocks.append(QQMessageBlock(sticker=sticker_id))
        return blocks if blocks else [QQMessageBlock(text=raw_text)]

    async def _repair_xml(self, broken: str) -> str | None:
        """用 LLM 修复格式错误的 XML 输出（30秒超时，失败则放弃）。"""
        import asyncio
        try:
            from utils.config_manager import get_config_manager
            from utils.llm_client import create_chat_llm_async
            model_config = get_config_manager().get_model_api_config("conversation")
            if not model_config.get("base_url") or not model_config.get("model"):
                return None
            llm = await create_chat_llm_async(
                model=str(model_config["model"]),
                base_url=str(model_config["base_url"]),
                api_key=str(model_config.get("api_key", "")),
                max_completion_tokens=500,
                timeout=15.0,
                provider_type=model_config.get("provider_type"),
            )
            prompt = (
                "以下是一段格式错误的 XML，请修复它使其成为合法的 XML，不要改变任何内容和标签：\n\n"
                f"{broken}\n\n只返回修复后的 XML。"
            )
            response = await asyncio.wait_for(
                llm.ainvoke([{"role": "user", "content": prompt}]),
                timeout=15.0,
            )
            fixed = str(getattr(response, "content", "") or "").strip()
            if fixed and ("<msg>" in fixed or "<msg " in fixed):
                self.plugin._emit_log("INFO", "[XML修复] 成功修复格式错误")
                return fixed
        except Exception:
            pass
        return None

    async def finalize(self, context: QQReplyContext, model_result: QQModelResult) -> QQReplyOutcome:
        raw_reply_text = model_result.reply_text or ""
        reply_text = self.plugin._sanitize_generated_reply(raw_reply_text)
        if raw_reply_text and not reply_text:
            self.plugin._emit_log("INFO", f"[Sanitize] {len(raw_reply_text)}字被清除: {raw_reply_text[:100]}")

        strategy_mode = getattr(self.plugin, "_strategy_mode", "neko_dynamic")
        blocks: list[QQMessageBlock] = []

        if strategy_mode == "neko_dynamic" and reply_text:
            # 先提取 <wait> 标签（XML 解析会忽略它），保存到 raw_reply_text 供 buffer 读取
            import re
            wm = re.search(r"<wait>(\d+(?:\.\d+)?)</wait>", reply_text, re.IGNORECASE)
            if wm:
                # 保留 raw_reply_text 中的 <wait> 标签（不清理），让 buffer 能读到
                pass  # raw_reply_text 未被 sanitize 处理，保留原始标签
            blocks = self._parse_blocks(reply_text)
            # 如果解析失败（只得到一个纯文本块且原文字含 XML 标签），尝试 LLM 修复
            if len(blocks) == 1 and blocks[0].text and ("<msg>" in reply_text or "</msg>" in reply_text):
                repaired = await self._repair_xml(reply_text)
                if repaired:
                    blocks = self._parse_blocks(repaired)
                    if blocks:
                        reply_text = repaired
            # 构建人类可读的 reply_text（首个块的文本）
            first_text = blocks[0].text if blocks else ""
            reply_text = first_text or reply_text

        if blocks or reply_text:
            return QQReplyOutcome(
                action="reply",
                reply_text=reply_text,
                raw_reply_text=raw_reply_text,
                postprocess_reason="reply_xml" if strategy_mode == "neko_dynamic" else "reply",
                blocks=blocks,
            )
        if context.ephemeral_session:
            return QQReplyOutcome(
                action="reply",
                reply_text=None,
                raw_reply_text=raw_reply_text,
                postprocess_reason="empty",
            )
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
        if not outcome.blocks and not outcome.reply_text:
            return None
        # 如果没有 blocks（旧格式回退），用 reply_text 构造一个块
        blocks = outcome.blocks if outcome.blocks else [QQMessageBlock(text=outcome.reply_text or "")]
        if request.is_group:
            return QQDeliveryPlan(
                target_type="group",
                target_id=str(request.group_id or ""),
                blocks=blocks,
                fallback_to_text_on_voice_failure=request.fallback_to_text_on_voice_failure,
            )
        return QQDeliveryPlan(
            target_type="private",
            target_id=request.sender_id,
            blocks=blocks,
            fallback_to_text_on_voice_failure=request.fallback_to_text_on_voice_failure,
        )
