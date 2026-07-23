"""
缓冲：消息到达 → 等待正态分布随机间隔 → 单条直接发 / 多条合并发
"""

from __future__ import annotations

import asyncio
import time
from typing import Any

import random as _random

_DELAY_MU = 9.0
_DELAY_SIGMA = 1.8
_MAX_BUFFER_COUNT = 17


def _random_delay() -> float:
    return max(0.0, _random.gauss(_DELAY_MU, _DELAY_SIGMA))


class PendingReply:
    __slots__ = ("entries", "wait_until", "task", "sender_id", "is_group", "group_id", "task_gen", "_cached_blocks")

    def __init__(self, sender_id: str, is_group: bool, group_id: str):
        self.entries: list[tuple[str, str]] = []  # [(sender_id, message_text), ...]; bot 回复用 "__bot__"
        self.wait_until = 0.0
        self.task: asyncio.Task | None = None
        self.task_gen: int = 0
        self.sender_id = sender_id
        self.is_group = is_group
        self.group_id = group_id
        self._cached_blocks: list = []  # bot 回复的消息块，单条时直接交付

    def _new_task(self, coro) -> asyncio.Task:
        self.task_gen += 1
        self.task = asyncio.create_task(coro)
        return self.task


class QQReplyBufferService:
    def __init__(self, plugin: Any):
        self.plugin = plugin
        self._pending: dict[str, PendingReply] = {}

    def store_reply(self, session_key: str, reply_text: str, blocks: list) -> bool:
        """pipeline 生成的回复存入缓冲桶，等计时器到期再发。返回 True 表示已存储。"""
        existing = self._pending.get(session_key)
        if existing is None:
            return False
        # 把 bot 回复当一条 "[bot]" 记录存进 entries，同时存 blocks 供单条时直接交付
        existing.entries.insert(0, ("__bot__", reply_text))
        existing._cached_blocks = blocks
        self.plugin._emit_log("DEBUG", f"[Buffer] 存储回复 key={session_key} reply={reply_text[:30]}")
        return True

    def has_pending(self, session_key: str) -> bool:
        p = self._pending.get(session_key)
        return p is not None and (p.task is None or not p.task.done())

    def get_state(self) -> dict[str, Any]:
        return {
            "pending": [
                {"session_key": k, "count": len(p.entries), "wait_until": p.wait_until}
                for k, p in self._pending.items()
            ],
            "count": len(self._pending),
        }

    async def buffer(self, session_key: str, message_text: str, sender_id: str, is_group: bool, group_id: str) -> bool:
        """消息到达时调用。返回 True 表示跳过 pipeline，返回 False 表示需要走 pipeline（首次）。"""
        existing = self._pending.get(session_key)
        if existing and (existing.task is None or not existing.task.done()):
            # 话题偏离检测：新消息和桶内已有消息是否同一话题
            if len(existing.entries) >= 2 and await self._topic_shift(existing, message_text):
                self.plugin._emit_log("INFO", f"[Buffer] 话题偏离 key={session_key} → 先交付旧桶，新消息另开: {message_text[:30]}")
                if existing.task:
                    existing.task.cancel()
                # 同步交付旧桶（不等计时器），然后新消息开新桶
                await self._flush(session_key, existing)
                pending = PendingReply(sender_id, is_group, group_id)
                pending.entries.append((sender_id, message_text))
                delay = _random_delay()
                pending.wait_until = time.time() + delay
                pending.task = pending._new_task(self._flush(session_key, pending))
                self._pending[session_key] = pending
                self.plugin._emit_log("DEBUG", f"[Buffer] 新建(话题偏移) key={session_key} delay={delay:.1f}s")
                return False
            if existing.task:
                existing.task.cancel()
            existing.entries.append((sender_id, message_text))
            if len(existing.entries) >= _MAX_BUFFER_COUNT:
                existing.wait_until = 0
                existing._new_task(self._flush(session_key, existing))
                self.plugin._emit_log("INFO", f"[Buffer] 达到上限 key={session_key} count={len(existing.entries)} → 立即交付")
            else:
                delay = _random_delay()
                existing.wait_until = time.time() + delay
                existing._new_task(self._flush(session_key, existing))
                self.plugin._emit_log("DEBUG", f"[Buffer] 追加 key={session_key} count={len(existing.entries)} delay={delay:.1f}s")
            return True

        # 首条消息：创建缓冲桶，起计时器。pipeline 也照常走——LLM 生成的回复会在计时器到期时才发出
        pending = PendingReply(sender_id, is_group, group_id)
        pending.entries.append((sender_id, message_text))
        delay = _random_delay()
        pending.wait_until = time.time() + delay
        pending.task = pending._new_task(self._flush(session_key, pending))
        self._pending[session_key] = pending
        self.plugin._emit_log("DEBUG", f"[Buffer] 新建 key={session_key} delay={delay:.1f}s")
        return False

    async def _topic_shift(self, existing: PendingReply, new_text: str) -> bool:
        """判断新消息是否和桶内已有消息话题不一致。返回 True 表示需要分桶。"""
        # 快速路径：消息数太少不检测；或新消息很短（大概率不是话题转换）
        if len(existing.entries) < 2:
            return False
        if len(new_text) <= 3:
            return False
        # 提取桶内用户消息
        buf_texts = [t for s, t in existing.entries[-5:] if s != "__bot__"][:200]
        if not buf_texts:
            return False
        # 快速预判：新消息和桶内消息有明显重叠 → 省掉 LLM 调用
        if any(new_text.startswith(t) or t.startswith(new_text) for t in buf_texts):
            return False

        from utils.config_manager import get_config_manager
        cfg = get_config_manager().get_model_api_config("conversation")
        base_url = str(cfg.get("base_url", ""))
        model = str(cfg.get("model", ""))
        api_key = str(cfg.get("api_key", ""))
        if not base_url or not model:
            return self._topic_shift_heuristic(buf_texts, new_text)

        prompt = (
            "判断新消息是否和已有消息属于同一话题。只回答\"是\"或\"否\"。\n\n"
            f"已有消息：\n" + "\n".join(f"- {t[:100]}" for t in buf_texts) +
            f"\n\n新消息：{new_text[:100]}"
        )

        # 链路1: create_chat_llm_async
        try:
            from utils.llm_client import create_chat_llm_async
            llm = await create_chat_llm_async(
                model=model, base_url=base_url, api_key=api_key,
                max_completion_tokens=5, timeout=5.0, provider_type=cfg.get("provider_type"),
            )
            resp = await asyncio.wait_for(llm.ainvoke([{"role": "user", "content": prompt}]), timeout=5.0)
            answer = str(getattr(resp, "content", "") or "").strip()
            return "否" in answer
        except Exception:
            pass

        # LLM 失败 → 启发式规则兜底
        return self._topic_shift_heuristic(buf_texts, new_text)

    @staticmethod
    def _topic_shift_heuristic(buf_texts: list[str], new_text: str) -> bool:
        """规则兜底：新消息和桶内消息明显不同时返回 True。"""
        avg_len = sum(len(t) for t in buf_texts) / max(len(buf_texts), 1)
        # 桶内全是短消息（≤4字），新消息是完整句子 → 话题偏移
        if avg_len <= 4 and len(new_text) >= 10:
            return True
        # 新消息有问号/疑问词，桶内没有 → 提问式话题转换
        q_words = ("?", "？", "什么", "怎么", "为什么", "哪", "谁", "几点", "多少", "吗", "呢", "吧")
        has_q = any(w in new_text for w in q_words)
        buf_has_q = any(any(w in t for w in q_words) for t in buf_texts)
        if has_q and not buf_has_q:
            return True
        return False

    async def _flush(self, session_key: str, pending: PendingReply) -> None:
        delay = max(0.0, pending.wait_until - time.time())
        gen = pending.task_gen
        self.plugin._emit_log("DEBUG", f"[Buffer] 等待中 key={session_key} gen={gen} delay={delay:.1f}s count={len(pending.entries)}")
        try:
            await asyncio.sleep(delay)
        except asyncio.CancelledError:
            if self._pending.get(session_key) is pending and pending.task_gen == gen:
                self._pending.pop(session_key, None)
            return

        if self._pending.get(session_key) is not pending:
            return

        # 先 pop 再交付——交付期间新消息进来会建新桶，不会污染当前桶
        self._pending.pop(session_key, None)

        entries = pending.entries
        user_entries = [(s, t) for s, t in entries if s != "__bot__"]
        has_bot_reply = any(s == "__bot__" for s, _ in entries)

        if not has_bot_reply:
            self.plugin._emit_log("WARN", f"[Buffer] key={session_key} 无 bot 回复，跳过交付")
            return

        if len(user_entries) == 1:
            # 只有首条用户消息 + bot 回复：直接交付 bot 回复
            from .pipeline_models import QQMessageBlock, QQDeliveryPlan
            blocks = pending._cached_blocks if pending._cached_blocks else [QQMessageBlock(text=entries[0][1])]
            plan = QQDeliveryPlan(
                target_type="group" if pending.is_group else "private",
                target_id=pending.group_id if pending.is_group else pending.sender_id,
                blocks=blocks,
                fallback_to_text_on_voice_failure=True,
            )
            result = await self.plugin.reply_delivery_node.deliver(plan)
            self.plugin._emit_log("DEBUG", f"[Buffer] 单条交付 key={session_key} delivered={result.delivered if result else False}")
        else:
            # 多条用户消息：合并总结（bot 回复不入桶，不干扰总结）
            self.plugin._emit_log("INFO", f"[Buffer] {len(user_entries)}条用户消息，走 pipeline 总结...")
            lines = []
            for sid, t in user_entries:
                name = self.plugin.permission_mgr.get_nickname(sid) if self.plugin.permission_mgr else sid
                lines.append(f"{name or sid}: {t[:150]}")
            combined = "\n".join(lines)
            from .pipeline_models import QQReplyRequest
            request = QQReplyRequest(
                message_text=f"[系统] 下面是你和群友的对话，请自然接话总结回复（不要复述，用一两句话）：\n{combined}",
                sender_id=pending.sender_id or "0",
                is_group=pending.is_group,
                group_id=pending.group_id if pending.is_group else None,
                is_at_bot=True,
                source_kind="rapid_fire_flush",
                fallback_to_text_on_voice_failure=True,
            )
            await self.plugin.reply_pipeline.run(request)
