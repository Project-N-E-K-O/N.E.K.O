"""
LLM 驱动的回复缓冲与发送延迟

消息到达 → LLM 生成回复 + 等待时间 → 异步等待 → 发送
等待期间新消息到达 → LLM 决定合并/替换/丢弃 → 重置计时

LLM 通过 <wait>N</wait> 标签指定等待秒数（默认 0，立即发送）。
"""

from __future__ import annotations

import asyncio
import random
import time
from typing import Any, Optional


class PendingReply:
    """待发送的回复（缓冲模式：收消息时不合成，等暂停后统一生成回复）"""
    __slots__ = ("buffered_texts", "wait_until", "task", "topic_hint", "message_count",
                 "sender_id", "is_group", "group_id", "_acked")

    def __init__(self, first_text: str, wait_seconds: float, sender_id: str, is_group: bool, group_id: str):
        self.buffered_texts: list[str] = [first_text]  # 缓冲的消息文本
        self.wait_until = time.time() + wait_seconds
        self.task: Optional[asyncio.Task] = None
        self.topic_hint: str = ""
        self.message_count: int = 1
        self.sender_id = sender_id
        self.is_group = is_group
        self.group_id = group_id
        self._acked = False


class QQReplyBufferService:
    """LLM 驱动的异步回复缓冲"""

    DEFAULT_WAIT_SECONDS = 3.0      # 群聊默认等待 3 秒
    DEFAULT_WAIT_PRIVATE = 6.0      # 私聊默认等待 6 秒（对方往往在连续输出）
    MAX_WAIT_SECONDS = 10.0         # 最多等 10 秒

    def __init__(self, plugin: Any):
        self.plugin = plugin
        self._pending: dict[str, PendingReply] = {}  # session_key → PendingReply

    # ── 提取 LLM 指定的等待时间 ──

    @classmethod
    def extract_wait_seconds(cls, raw_text: str) -> tuple[str, float]:
        """从 LLM 输出中提取 <wait>N</wait> 标签，返回 (清理后文本, 等待秒数)。"""
        import re
        match = re.search(r"<wait>(\d+(?:\.\d+)?)</wait>", raw_text, re.IGNORECASE)
        if match:
            try:
                secs = float(match.group(1))
                secs = max(0.0, min(cls.MAX_WAIT_SECONDS, secs))
                clean = re.sub(r"<wait>\d+(?:\.\d+)?</wait>", "", raw_text, count=1, flags=re.IGNORECASE)
                return clean.strip(), secs
            except ValueError:
                pass
        return raw_text, cls.DEFAULT_WAIT_SECONDS

    # ── 话题摘要 ──

    @staticmethod
    def _topic_hint(text: str) -> str:
        """从文本中提取简短话题摘要（前 30 字）。"""
        t = str(text or "").strip()
        return t[:30] if t else ""

    def has_pending(self, session_key: str) -> bool:
        """检查是否有等待中的缓冲（含 LLM 生成中未完成的）。"""
        p = self._pending.get(session_key)
        return p is not None and (p.task is None or not p.task.done())

    def pre_buffer(self, session_key: str, message_text: str, sender_id: str, is_group: bool, group_id: str) -> bool:
        """消息到达时调用（LLM 生成前）：创建/追加缓冲，返回 True 表示跳过 pipeline。"""
        now = time.time()
        existing = self._pending.get(session_key)

        if existing and (existing.task is None or not existing.task.done()):
            # 已有缓冲 → 追加
            if existing.task:
                existing.task.cancel()
            existing.buffered_texts.append(message_text)
            existing.message_count += 1
            n = existing.message_count
            if n <= 2:       extra = random.uniform(6.0, 10.0)
            elif n <= 4:     extra = random.uniform(10.0, 16.0)
            elif n <= 7:     extra = random.uniform(13.0, 19.0)
            elif n <= 16:    extra = random.uniform(6.0, 11.0)
            else:            extra = 0.0
            existing.wait_until = now + extra
            existing.task = asyncio.create_task(self._deliver_after_wait(session_key, existing))
            self.plugin._emit_log("DEBUG", f"[Buffer] 预缓冲追加（共{n}条），等待 {extra:.1f}s，跳过 LLM 生成")
            return True

        # 无缓冲 → 创建新缓冲，等 pipeline 完成后 schedule_reply 会填充回复
        pending = PendingReply(
            first_text=message_text,
            wait_seconds=6.0,
            sender_id=sender_id,
            is_group=is_group,
            group_id=group_id,
        )
        pending.task = None  # 尚未启动等待（等 schedule_reply 来启动）
        self._pending[session_key] = pending
        return False  # 首次消息，走 pipeline

    def get_state(self) -> dict:
        """返回当前缓冲状态（供前端展示）。"""
        now = time.time()
        items = []
        for key, p in self._pending.items():
            remaining = max(0.0, p.wait_until - now)
            items.append({
                "session": key,
                "messages": p.message_count,
                "wait_remaining": round(remaining, 1),
                "is_group": p.is_group,
            })
        return {"pending": items, "count": len(items)}

    # ── 调度回复 ──

    async def schedule_reply(
        self,
        session_key: str,
        reply_text: str,
        raw_text: str,
        blocks: list,
        wait_seconds: float,
        sender_id: str,
        is_group: bool,
        group_id: str = "",
        extra_count: int = 0,
    ) -> None:
        """缓冲一条消息。如果已有等待中的缓冲，追加消息并重置等待计时。"""
        # 存入缓冲前去除 XML 标签（raw_text 可能含 <msg><text> 等）
        import re
        clean_text = re.sub(r"<[^>]+>", "", str(reply_text or raw_text or "")).strip()
        if not clean_text:
            clean_text = str(reply_text or raw_text or "").strip()
        existing = self._pending.get(session_key)

        if existing and existing.task and not existing.task.done():
            # 已有缓冲 → 追加消息，转发子条数计入
            existing.task.cancel()
            existing.buffered_texts.append(clean_text)
            existing.message_count += 1 + max(0, extra_count)
            # 动态等待：6~20s 正态分布，中间最长（峰值 ~16s），两头短
            n = existing.message_count
            if n <= 2:
                extra = random.uniform(6.0, 10.0)
            elif n <= 4:
                extra = random.uniform(10.0, 16.0)
            elif n <= 7:
                extra = random.uniform(13.0, 19.0)
            elif n <= 16:
                extra = random.uniform(6.0, 11.0)
            else:
                extra = 0.0
            existing.wait_until = time.time() + extra
            self.plugin._emit_log("DEBUG", f"缓冲追加（共{n}条），等待 {extra:.1f}s")

            # 10-16 条 → 走 pipeline 发简短确认
            if 10 <= n < 17 and not getattr(existing, "_acked", False):
                existing._acked = True
                try:
                    from .pipeline_models import QQReplyRequest
                    combined = "\n".join(f"[{i+1}] {t[:100]}" for i, t in enumerate(existing.buffered_texts[-5:]))
                    request = QQReplyRequest(
                        message_text=f"[系统] 对方连续发了多条消息，你需要发一句简短的话表示\"我在听\"吗？如果需要，只回复那句话（不超过10个字，要自然，符合人设）；如果不需要，回复空内容。以下是最近内容：\n{combined}",
                        sender_id=existing.sender_id or "0",
                        is_group=existing.is_group,
                        group_id=existing.group_id if existing.is_group else None,
                        is_at_bot=True,
                        source_kind="rapid_fire_flush",
                        fallback_to_text_on_voice_failure=True,
                    )
                    await self.plugin.reply_pipeline.run(request)
                except Exception:
                    pass

            # 17+ 条 → 走 pipeline 强制总结 + 清空缓冲
            if n >= 17:
                existing.task.cancel()
                self._pending.pop(session_key, None)
                try:
                    from .pipeline_models import QQReplyRequest
                    combined = "\n".join(f"[{i+1}] {t[:150]}" for i, t in enumerate(existing.buffered_texts))
                    request = QQReplyRequest(
                        message_text=f"[系统] 对方连续发了以下消息，请用一两句话自然总结回复：\n{combined}",
                        sender_id=existing.sender_id or "0",
                        is_group=existing.is_group,
                        group_id=existing.group_id if existing.is_group else None,
                        is_at_bot=True,
                        source_kind="rapid_fire_flush",
                        fallback_to_text_on_voice_failure=True,
                    )
                    await self.plugin.reply_pipeline.run(request)
                except Exception:
                    pass
                return
        else:
            # 新缓冲：pre_buffer 可能已创建了占位 pending
            existing = self._pending.get(session_key)
            if existing and existing.task is None:
                # pre_buffer 占位 → 填充回复文本，启动等待
                existing.buffered_texts[0] = clean_text  # 替换占位文本为 LLM 回复
                existing.wait_until = time.time() + wait_seconds
                existing.sender_id = sender_id
                existing.is_group = is_group
                existing.group_id = group_id
                existing.topic_hint = self._topic_hint(raw_text or reply_text)
            else:
                # 完全新缓冲
                existing = PendingReply(
                    first_text=clean_text,
                    wait_seconds=wait_seconds,
                    sender_id=sender_id,
                    is_group=is_group,
                    group_id=group_id,
                )
                existing.message_count += max(0, extra_count)
                existing.topic_hint = self._topic_hint(raw_text or reply_text)
                self._pending[session_key] = existing

        # 启动等待任务
        existing.sender_id = sender_id  # 更新（可能变化）
        existing.is_group = is_group
        existing.group_id = group_id
        existing.task = asyncio.create_task(self._deliver_after_wait(session_key, existing))

    async def _deliver_after_wait(self, session_key: str, pending: PendingReply) -> None:
        """等待暂停后，汇总缓冲消息让 LLM 生成最终回复并发送。"""
        now = time.time()
        delay = max(0.0, pending.wait_until - now)
        try:
            await asyncio.sleep(delay)
        except asyncio.CancelledError:
            return  # 新消息打断了等待

        if self._pending.get(session_key) is not pending:
            return

        self._pending.pop(session_key, None)

        # 汇总缓冲内容
        texts = pending.buffered_texts
        if pending.message_count == 1:
            # 只有一条 → 直接发送（texts[0] 含 LLM 原始标签，需要清理）
            from .pipeline_models import QQMessageBlock, QQDeliveryPlan
            import re
            clean_text = re.sub(r"<[^>]+>", "", texts[0]).strip() or texts[0]
            plan = QQDeliveryPlan(
                target_type="group" if pending.is_group else "private",
                target_id=pending.group_id if pending.is_group else pending.sender_id,
                blocks=[QQMessageBlock(text=clean_text)],
                fallback_to_text_on_voice_failure=True,
            )
            await self.plugin.reply_delivery_node.deliver(plan)
            return

        # 多条缓冲 → 走 pipeline 生成总结（兼容 Lanlan）
        self.plugin._emit_log("INFO", f"缓冲{pending.message_count}条消息，走 pipeline 生成总结...")
        try:
            from .pipeline_models import QQReplyRequest
            combined = "\n".join(f"[{i+1}] {t[:150]}" for i, t in enumerate(texts))
            request = QQReplyRequest(
                message_text=f"[系统] 对方连续发了 {len(texts)} 条消息，请用一两句话自然总结回复：\n{combined}",
                sender_id=pending.sender_id or "0",
                is_group=pending.is_group,
                group_id=pending.group_id if pending.is_group else None,
                is_at_bot=True,
                source_kind="rapid_fire_flush",
                fallback_to_text_on_voice_failure=True,
            )
            await self.plugin.reply_pipeline.run(request)
        except Exception:
            pass

    # ── LLM 合并决策 ──

    async def _generate_ack(self, texts: list[str]) -> str:
        """让 LLM 决定是否发简短确认，以及确认内容。返回空字符串表示不发。"""
        try:
            from utils.config_manager import get_config_manager
            from utils.llm_client import create_chat_llm_async
            model_config = get_config_manager().get_model_api_config("conversation")
            if not model_config.get("base_url") or not model_config.get("model"):
                return ""

            recent = "\n".join(f"[{i+1}] {t[:100]}" for i, t in enumerate(texts[-5:]))
            llm = await create_chat_llm_async(
                model=str(model_config["model"]),
                base_url=str(model_config["base_url"]),
                api_key=str(model_config.get("api_key", "")),
                max_completion_tokens=50,
                timeout=5.0,
                provider_type=model_config.get("provider_type"),
            )
            from utils.token_tracker import set_call_type
            set_call_type("conversation")
            prompt = (
                "对方连续发了多条消息，以下是最近的内容：\n\n"
                f"{recent}\n\n"
                "你需要发一句简短的话表示\"我在听\"吗？如果需要，只输出那句话（不超过10个字，要自然，比如\"嗯嗯\"\"继续\"\"听着呢\"等，要符合你的人设）；"
                "如果不需要，只输出 SKIP。\n"
                "只输出确认语或 SKIP，不要输出其他内容。"
            )
            resp = await asyncio.wait_for(
                llm.ainvoke([{"role": "user", "content": prompt}]),
                timeout=5.0,
            )
            result = str(getattr(resp, "content", "") or "").strip()
            if result and result.upper() != "SKIP":
                return result[:20]
        except Exception:
            pass
        return ""

    async def _summarize_buffered(self, texts: list[str], is_group: bool) -> str:
        """缓冲结束后，让 LLM 看所有缓冲消息生成一条总结回复。"""
        try:
            combined = "\n".join(f"[{i+1}] {t[:150]}" for i, t in enumerate(texts))
            prompt = (
                f"对方连续发了 {len(texts)} 条消息，内容如下：\n\n"
                f"{combined}\n\n"
                "请用一两句话自然回复，总结或回应对方的要点。不要逐条回复，像真人在听对方讲完一堆话之后的自然反应。"
            )

            # 通过 OmniOfflineClient 调 LLM（兼容 Lanlan API）
            from main_logic.omni_offline_client import OmniOfflineClient
            from utils.config_manager import get_config_manager as _gcm
            import asyncio as _asyncio
            _cm = _gcm()
            _mc = _cm.get_model_api_config("conversation")
            resp_text = ""
            async def _on_text(t: str, _first: bool = False) -> None:
                nonlocal resp_text
                resp_text += t
            client = OmniOfflineClient(
                base_url=str(_mc.get("base_url", "")),
                api_key=str(_mc.get("api_key", "")),
                model=str(_mc.get("model", "")),
                on_text_delta=_on_text,
            )
            await _asyncio.wait_for(client.stream_text(prompt), timeout=10.0)
            result = resp_text.strip()
            if result:
                return result

            # 回退：raw LLM
            from utils.config_manager import get_config_manager
            from utils.llm_client import create_chat_llm_async
            model_config = get_config_manager().get_model_api_config("conversation")
            if not model_config.get("base_url") or not model_config.get("model"):
                return ""
            llm = await create_chat_llm_async(
                model=str(model_config["model"]), base_url=str(model_config["base_url"]),
                api_key=str(model_config.get("api_key", "")),
                max_completion_tokens=300, timeout=10.0,
                provider_type=model_config.get("provider_type"),
            )
            resp = await _asyncio.wait_for(llm.ainvoke([{"role": "user", "content": prompt}]), timeout=10.0)
            result = str(getattr(resp, "content", "") or "").strip()
            return result if result else ""
        except Exception as e:
            self.plugin._emit_log("WARN", f"[Buffer] 总结LLM调用失败: {e}")
            return ""
