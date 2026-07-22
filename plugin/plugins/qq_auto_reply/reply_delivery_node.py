from __future__ import annotations

import asyncio
import random
from typing import Any

from .pipeline_models import QQDeliveryPlan, QQDeliveryResult, QQMessageBlock


class QQReplyDeliveryNode:
    def __init__(self, plugin: Any):
        self.plugin = plugin

    async def deliver(self, plan: QQDeliveryPlan | None) -> QQDeliveryResult | None:
        if not plan or not plan.blocks:
            return None

        blocks = plan.blocks
        first_text = ""
        for i, block in enumerate(blocks):
            if i > 0:
                # 块间延迟：模拟真人打字间隔
                await asyncio.sleep(random.uniform(2.0, 5.0))

            if block.poke:
                await self._send_poke(plan, block)
                continue

            if block.record:
                await self._send_record(plan, block)
                continue

            if block.sticker:
                await self._send_sticker(plan, block)
                continue

            # 文本块（可含 emoji + at + reply）
            text = self._compose_text(block)
            if not text:
                continue
            if i == 0:
                first_text = text
            await self._send_text(plan, block, text)

        return QQDeliveryResult(
            delivered=True,
            target_type=plan.target_type,
            target_id=plan.target_id,
            reply_text=first_text,
        )

    @staticmethod
    def _compose_text(block: QQMessageBlock) -> str:
        """组合文字 + emoji + at + reply 为最终文本。"""
        parts: list[str] = []
        if block.reply_to:
            parts.append(f"[CQ:reply,id={block.reply_to}]")
        if block.at_user:
            parts.append(f"[CQ:at,qq={block.at_user}]")
        if block.text:
            parts.append(block.text)
        if block.emoji:
            # QQ 系统表情：使用 OneBot CQ 码
            parts.append(f"[CQ:face,id={block.emoji}]")
        return "".join(parts)

    async def _send_text(self, plan: QQDeliveryPlan, block: QQMessageBlock, text: str) -> None:
        if not text:
            return
        mode = self.plugin._get_reply_mode()
        if mode == "voice":
            # voice-only 模式：走 TTS 发送语音
            if plan.target_type == "group":
                await self.plugin._deliver_group_reply(plan.target_id, text, fallback_to_text_on_voice_failure=plan.fallback_to_text_on_voice_failure)
            else:
                await self.plugin._deliver_private_reply(plan.target_id, text, fallback_to_text_on_voice_failure=plan.fallback_to_text_on_voice_failure)
        elif plan.target_type == "group":
            await self.plugin.qq_client.send_group_message(plan.target_id, text)
        else:
            await self.plugin.qq_client.send_message(plan.target_id, text)

    async def _send_sticker(self, plan: QQDeliveryPlan, block: QQMessageBlock) -> None:
        if plan.target_type != "group":
            return
        sticker_path = self.plugin._resolve_sticker_path(block.sticker)
        if sticker_path:
            await self.plugin.qq_client.send_group_image(plan.target_id, sticker_path)

    async def _send_poke(self, plan: QQDeliveryPlan, block: QQMessageBlock) -> None:
        if plan.target_type != "group" or not block.poke:
            return
        # 冷却：同一群每 30 秒最多戳一次，避免刷屏
        now = __import__("time").time()
        key = f"poke_out:{plan.target_id}"
        last = getattr(self, "_last_poke_out", {}).get(key, 0)
        if now - last < 30:
            self.plugin._emit_log("INFO", f"戳一戳冷却中，跳过 (群{plan.target_id})")
            return
        if not hasattr(self, "_last_poke_out"):
            self._last_poke_out = {}
        self._last_poke_out[key] = now
        await self.plugin.qq_client.send_group_poke(plan.target_id, block.poke)

    async def _send_record(self, plan: QQDeliveryPlan, block: QQMessageBlock) -> None:
        if not block.record:
            return
        try:
            file_uri, _ = await self.plugin.voice_reply_service.synthesize_reply_voice_file(block.record)
            if plan.target_type == "group":
                await self.plugin.qq_client.send_group_record(plan.target_id, file_uri)
            else:
                await self.plugin.qq_client.send_private_record(plan.target_id, file_uri)
        except Exception:
            self.plugin.logger.warning("语音发送失败", exc_info=True)
            if plan.fallback_to_text_on_voice_failure and block.record:
                text = block.record
                if plan.target_type == "group":
                    await self.plugin.qq_client.send_group_message(plan.target_id, text)
                else:
                    await self.plugin.qq_client.send_message(plan.target_id, text)
