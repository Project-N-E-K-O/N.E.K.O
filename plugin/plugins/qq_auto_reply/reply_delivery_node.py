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
        all_text_sent = True
        any_text_attempted = False
        for i, block in enumerate(blocks):
            if i > 0:
                # 块间延迟：模拟真人打字间隔
                await asyncio.sleep(random.uniform(2.0, 5.0))

            if block.poke:
                # poke 只是装饰：冷却窗口内/私聊目标下会被有意跳过，那不是
                # 投递失败——模板本来就让 poke 单独成块后跟正文块，把跳过
                # 算失败会让整条（正文已送达的）回复被判未投递、真回复被
                # 排除出记忆。只有"尝试了但发送失败"才算未确认。
                sent, attempted = await self._send_poke(plan, block)
                if attempted:
                    any_text_attempted = True
                    if not sent:
                        all_text_sent = False
                continue

            if block.record:
                any_text_attempted = True
                if not await self._send_record(plan, block):
                    all_text_sent = False
                continue

            if block.sticker:
                any_text_attempted = True
                if not await self._send_sticker(plan, block):
                    all_text_sent = False
                continue

            # 文本块（可含 emoji + at + reply + keyboard）
            text = self._compose_text(block)
            if not text and block.keyboard and plan.target_type != "group":
                # 官方按钮只有群聊承载：私聊的 keyboard-only 块无处安放，
                # 必须按未投递处理——与 ark 同一类判据，不能"什么都没发"
                # 却清掉未投递标、把 mention 记进 scoped 提取。
                any_text_attempted = True
                all_text_sent = False
                self.plugin.logger.warning(
                    "keyboard-only 块不支持私聊投递，未发送（记忆按未投递处理）"
                )
                continue
            if not text and block.keyboard and plan.target_type == "group":
                # keyboard-only 块必须真发出点什么，否则既没送出去又被算成
                # 已投递。官方按钮只有开放平台能渲染（NapCat/OneBot 协议
                # 无此字段，其 send_group_message_segments 收下 keyword 但
                # 不读）——NapCat 侧把按钮文案降级成可读文本，别只发一个
                # 空格。
                any_text_attempted = True
                labels = " / ".join(
                    part.strip()
                    for part in str(block.keyboard).split("|")
                    if part.strip()
                ) or str(block.keyboard)
                if self._supports_keyboard():
                    # 内容不能是空白：开放平台 sender 会 strip 后判空直接
                    # 返回 None，连带按钮 payload 都不构造——既没按钮也没
                    # 文本，还被当成发送失败。用选项文案当正文。
                    sent = await self.plugin.qq_client.send_group_message_segments(
                        plan.target_id,
                        [{"type": "text", "data": {"text": labels}}],
                        keyboard=block.keyboard,
                    )
                else:
                    sent = await self.plugin.qq_client.send_group_message(
                        plan.target_id, labels,
                    )
                if not self._confirm_platform_result(sent):
                    all_text_sent = False
                continue
            if not text:
                if block.ark:
                    # Ark 卡片目前没有投递实现（_send_ark 自 #2429 起无
                    # 调用方，属本 PR 之外的既有缺陷）：这里只保证记忆侧
                    # 不把"什么都没发"记成已投递——草稿保持排除、不记
                    # mention。真正的卡片发送要另行接回。
                    any_text_attempted = True
                    all_text_sent = False
                    self.plugin.logger.warning(
                        "Ark 卡片块没有投递实现，未发送（记忆按未投递处理）"
                    )
                continue
            if i == 0:
                first_text = text
            any_text_attempted = True
            if not await self._send_text(plan, block, text, keyboard=block.keyboard):
                all_text_sent = False

        # 开放平台单条发送失败返回 None（不抛异常）：只要有文本块未确认
        # 就不得报 delivered=True——buffer 会据此清未投递标并记 mention，
        # 而排除名单是整行粒度的，部分未发出的内容也会进 scoped 提取。
        # 纯 poke/sticker 计划保持旧语义（其发送无结果通道，失败靠异常）。
        return QQDeliveryResult(
            delivered=all_text_sent or not any_text_attempted,
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

    async def _send_text(
        self, plan: QQDeliveryPlan, block: QQMessageBlock, text: str,
        *, keyboard: str = "",
    ) -> bool:
        """Returns True when the send is confirmed or fire-and-forget.

        NapCat sends return None by design (failure surfaces as an
        exception); the Open Platform client returns the message id, or
        None on a swallowed failure - only that explicit None means the
        message was not delivered."""
        if not text:
            return False
        explicit_result = bool(
            self.plugin.qq_client
            and not self.plugin.qq_client.needs_attention
        )
        mode = self.plugin._get_reply_mode()
        if mode == "voice":
            # voice-only 模式：走 TTS 发送语音——确认结果一路传播（开放
            # 平台失败吞异常返回 None，语音回复也不得凭空算已投递）。
            # 按钮无法在语音里交互，但选项文案要念出来，否则用户听到的
            # 回复缺了它在问的那几个选项。
            voice_text = text
            if keyboard:
                labels = " / ".join(
                    part.strip() for part in str(keyboard).split("|")
                    if part.strip()
                )
                if labels:
                    voice_text = voice_text + "\n" + labels
            if plan.target_type == "group":
                return bool(await self.plugin._deliver_group_reply(plan.target_id, voice_text, fallback_to_text_on_voice_failure=plan.fallback_to_text_on_voice_failure))
            return bool(await self.plugin._deliver_private_reply(plan.target_id, voice_text, fallback_to_text_on_voice_failure=plan.fallback_to_text_on_voice_failure))
        if plan.target_type != "group" and keyboard:
            # 官方按钮只有群聊承载：私聊带 keyboard 的文本块若原样发出，
            # "想看哪个？" 会到达用户手里却一个选项都没有。和 NapCat 群
            # 路径同样处理——把选项文案降级成可读正文。
            labels = " / ".join(
                part.strip() for part in str(keyboard).split("|")
                if part.strip()
            )
            if labels:
                text = text + "\n" + labels
        if plan.target_type == "group":
            if keyboard and not self._supports_keyboard():
                # NapCat 渲染不了官方按钮：把选项文案追加进正文，别让
                # "要看看哪个？<keyboard>A|B|C</keyboard>" 变成一句没有
                # 任何可选项的话。
                labels = " / ".join(
                    part.strip() for part in str(keyboard).split("|")
                    if part.strip()
                )
                if labels:
                    text = text + "\n" + labels
            if keyboard and self._supports_keyboard():
                # keyboard 只有开放平台的 segments 接口承载：带按钮的文本
                # 块走它。NapCat 不支持按钮，走普通文本（内容照发，按钮
                # 能力缺失是协议限制，不是静默丢弃逻辑）。
                result = await self.plugin.qq_client.send_group_message_segments(
                    plan.target_id,
                    [{"type": "text", "data": {"text": text}}],
                    keyboard=keyboard,
                )
            else:
                result = await self.plugin.qq_client.send_group_message(plan.target_id, text)
        else:
            result = await self.plugin.qq_client.send_message(plan.target_id, text)
        if explicit_result:
            return result is not None
        return True

    async def _send_sticker(self, plan: QQDeliveryPlan, block: QQMessageBlock) -> bool:
        if plan.target_type != "group":
            return False
        sticker_path = self.plugin._resolve_sticker_path(block.sticker)
        if not sticker_path:
            return False
        return self._confirm_platform_result(
            await self.plugin.qq_client.send_group_image(plan.target_id, sticker_path),
            has_result_channel=True,
        )

    async def _send_poke(
        self, plan: QQDeliveryPlan, block: QQMessageBlock,
    ) -> tuple[bool, bool]:
        """Returns (confirmed, attempted).

        A poke that is deliberately skipped (private target, cooldown) was
        never attempted, so it must not drag the whole plan's delivery
        verdict down — the accompanying text block usually did reach the
        user."""
        if plan.target_type != "group" or not block.poke:
            return False, False
        # 冷却：同一群每 30 秒最多戳一次，避免刷屏
        now = __import__("time").time()
        key = f"poke_out:{plan.target_id}"
        last = getattr(self, "_last_poke_out", {}).get(key, 0)
        if now - last < 30:
            self.plugin._emit_log("INFO", f"戳一戳冷却中，跳过 (群{plan.target_id})")
            return False, False
        if not hasattr(self, "_last_poke_out"):
            self._last_poke_out = {}
        self._last_poke_out[key] = now
        return (
            self._confirm_platform_result(
                await self.plugin.qq_client.send_group_poke(plan.target_id, block.poke),
                has_result_channel=True,
            ),
            True,
        )

    def _supports_keyboard(self) -> bool:
        """Only the Open Platform renders official keyboard buttons.

        NapCat/OneBot has no such field — its send_group_message_segments
        accepts the kwarg for interface parity but never reads it."""
        client = self.plugin.qq_client
        return bool(client and not client.needs_attention)

    def _confirm_platform_result(self, result, *, has_result_channel: bool = False) -> bool:
        """开放平台失败吞异常返回 None——只有那个显式 None 判未投递。

        NapCat 的纯文本发送是 fire-and-forget（无返回通道，失败走异常），
        返回值一律视为确认；但 send_group_poke / send_group_image 这类
        **有**返回通道的接口会把失败表达成 False/None，调用方传
        has_result_channel=True 时要如实判未投递。"""
        if self.plugin.qq_client and not self.plugin.qq_client.needs_attention:
            return result is not None
        if has_result_channel:
            return bool(result)
        return True

    async def _send_record(self, plan: QQDeliveryPlan, block: QQMessageBlock) -> bool:
        if not block.record:
            return False
        try:
            file_uri, _ = await self.plugin.voice_reply_service.synthesize_reply_voice_file(block.record)
            if plan.target_type == "group":
                result = await self.plugin.qq_client.send_group_record(plan.target_id, file_uri)
            else:
                result = await self.plugin.qq_client.send_private_record(plan.target_id, file_uri)
            if self._confirm_platform_result(result):
                return True
            if plan.fallback_to_text_on_voice_failure:
                # 未确认（开放平台吞异常返回 None）与异常同等对待：按请求
                # 回退文本，而不是直接判未投递。
                if plan.target_type == "group":
                    fb = await self.plugin.qq_client.send_group_message(plan.target_id, block.record)
                else:
                    fb = await self.plugin.qq_client.send_message(plan.target_id, block.record)
                return self._confirm_platform_result(fb)
            return False
        except Exception:
            self.plugin.logger.warning("语音发送失败", exc_info=True)
            if plan.fallback_to_text_on_voice_failure and block.record:
                text = block.record
                if plan.target_type == "group":
                    result = await self.plugin.qq_client.send_group_message(plan.target_id, text)
                else:
                    result = await self.plugin.qq_client.send_message(plan.target_id, text)
                return self._confirm_platform_result(result)
            return False
