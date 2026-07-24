"""
缓冲：消息到达 → 等待正态分布随机间隔 → 单条直接发 / 多条合并发
"""

from __future__ import annotations

import asyncio
import time
from collections import Counter
from typing import Any

import random as _random

_DELAY_MU = 9.0
_DELAY_SIGMA = 1.8
_MAX_BUFFER_COUNT = 17

# n-gram 话题抽取的静态停用词基础表，运行时会额外并入 backlog_labels 中配置的关键词
_STOP_WORDS: set[str] = {
    "这个", "那个", "什么", "怎么", "为什么", "是不是", "有没有",
    "我觉得", "就是说", "然后", "所以", "但是", "不过", "其实",
    "哈哈哈", "确实", "是的", "对的", "嗯嗯", "好的", "可以",
    "啊", "吧", "呢", "吗", "哦", "嗯", "哈",
}


def _random_delay() -> float:
    return max(0.0, _random.gauss(_DELAY_MU, _DELAY_SIGMA))


class PendingReply:
    __slots__ = ("entries", "wait_until", "task", "sender_id", "is_group", "group_id", "task_gen", "bot_blocks", "_no_reply_retries", "bucket_id")

    def __init__(self, sender_id: str, is_group: bool, group_id: str):
        self.entries: list[tuple[str, str]] = []  # [(sender_id, message_text), ...]
        self.wait_until = 0.0
        self.task: asyncio.Task | None = None
        self.task_gen: int = 0
        self.sender_id = sender_id
        self.is_group = is_group
        self.group_id = group_id
        self.bot_blocks: list | None = None  # None = pipeline 未完成；有值 = 回复就绪
        self.bucket_id: int = 0  # 单调递增的身份标识，store_reply 用于校验桶未被替换

    def _new_task(self, coro) -> asyncio.Task:
        self.task_gen += 1
        self.task = asyncio.create_task(coro)
        return self.task


class QQReplyBufferService:
    def __init__(self, plugin: Any):
        self.plugin = plugin
        self._pending: dict[str, PendingReply] = {}
        # 复合键 "session_key:bucket_id" —— 同一会话可同时存在多个 detached 桶
        self._detached: dict[str, PendingReply] = {}
        self._bucket_id_seq: int = 0

    def _next_bucket_id(self) -> int:
        self._bucket_id_seq += 1
        return self._bucket_id_seq

    @staticmethod
    def _detached_key(session_key: str, bucket_id: int) -> str:
        return f"{session_key}:{bucket_id}"

    # ------------------------------------------------------------------
    # 公共接口
    # ------------------------------------------------------------------

    def store_reply(self, session_key: str, reply_text: str, blocks: list, *, expected_bucket_id: int = 0) -> bool:
        """pipeline 生成的回复存入缓冲桶，等计时器到期再发。返回 True 表示已存储。

        expected_bucket_id: 触发本次 pipeline 的桶 ID。先查 _pending（当前活跃桶），
        不匹配再按复合键查 _detached（已摘除但仍在等回复的桶）。"""
        existing = self._pending.get(session_key)
        if existing is None or (expected_bucket_id and existing.bucket_id != expected_bucket_id):
            if expected_bucket_id:
                existing = self._detached.get(self._detached_key(session_key, expected_bucket_id))
            else:
                existing = None
        if existing is None:
            return False
        if expected_bucket_id and existing.bucket_id != expected_bucket_id:
            self.plugin._emit_log("DEBUG", f"[Buffer] 桶身份不匹配 key={session_key} expected_id={expected_bucket_id} actual_id={existing.bucket_id} → 丢弃旧回复")
            return False
        existing.bot_blocks = blocks
        self.plugin._emit_log("DEBUG", f"[Buffer] 存储回复 key={session_key} reply={reply_text[:30]}")
        return True

    def has_pending(self, session_key: str) -> bool:
        p = self._pending.get(session_key)
        if p is not None and (p.task is None or not p.task.done()):
            return True
        prefix = f"{session_key}:"
        return any(
            (v.task is None or not v.task.done())
            for k, v in self._detached.items() if k.startswith(prefix)
        )

    def get_state(self) -> dict[str, Any]:
        count = len(self._pending) + len(self._detached)
        entries = []
        for k, p in self._pending.items():
            entries.append({"session_key": k, "count": len(p.entries), "wait_until": p.wait_until})
        for k, p in self._detached.items():
            # k is "session_key:bucket_id", strip the :bucket_id suffix for display
            display_key = k.rsplit(":", 1)[0]
            entries.append({"session_key": display_key, "count": len(p.entries), "wait_until": p.wait_until})
        return {"pending": entries, "count": count}

    async def buffer(self, session_key: str, message_text: str, sender_id: str, is_group: bool, group_id: str) -> bool:
        """消息到达时调用。返回 True 表示跳过 pipeline，返回 False 表示需要走 pipeline（首次）。"""
        existing = self._pending.get(session_key)
        if existing and (existing.task is None or not existing.task.done()):
            # 话题偏离检测：新消息和桶内已有消息是否同一话题
            if len(existing.entries) >= 2 and await self._topic_shift(existing, message_text):
                old_topic = self._bucket_topic(existing.entries)
                self.plugin._emit_log("INFO", f"[Buffer] 话题偏离 key={session_key} topic=「{old_topic}」→ 先交付旧桶，新消息: {message_text[:30]}")
                if existing.task:
                    existing.task.cancel()
                    existing.task_gen += 1  # 旧任务 gen 不匹配，取消分支不会 pop
                # 立即交付旧桶（不等计时器），然后新消息开新桶
                existing.wait_until = 0
                await self._flush(session_key, existing, abandon_on_no_reply=True)
                pending = PendingReply(sender_id, is_group, group_id)
                pending.bucket_id = self._next_bucket_id()
                pending.entries.append((sender_id, message_text))
                delay = _random_delay()
                pending.wait_until = time.time() + delay
                pending.task = pending._new_task(self._flush(session_key, pending))
                self._pending[session_key] = pending
                self.plugin._emit_log("DEBUG", f"[Buffer] 新建(话题偏移) key={session_key} bucket_id={pending.bucket_id} delay={delay:.1f}s")
                return False
            if existing.task:
                existing.task.cancel()
            existing.entries.append((sender_id, message_text))
            if len(existing.entries) >= _MAX_BUFFER_COUNT:
                existing.wait_until = 0
                self._pending.pop(session_key, None)  # 先摘桶，避免新消息 cancel 掉本次交付
                self._detached[self._detached_key(session_key, existing.bucket_id)] = existing
                existing._new_task(self._flush_detached(session_key, existing))
                self.plugin._emit_log("INFO", f"[Buffer] 达到上限 key={session_key} count={len(existing.entries)} → 立即交付")
            else:
                delay = _random_delay()
                existing.wait_until = time.time() + delay
                existing._new_task(self._flush(session_key, existing))
                self.plugin._emit_log("DEBUG", f"[Buffer] 追加 key={session_key} count={len(existing.entries)} delay={delay:.1f}s")
            return True

        # 首条消息：创建缓冲桶，起计时器。pipeline 也照常走——LLM 生成的回复会在计时器到期时才发出
        pending = PendingReply(sender_id, is_group, group_id)
        pending.bucket_id = self._next_bucket_id()
        pending.entries.append((sender_id, message_text))
        delay = _random_delay()
        pending.wait_until = time.time() + delay
        pending.task = pending._new_task(self._flush(session_key, pending))
        self._pending[session_key] = pending
        self.plugin._emit_log("DEBUG", f"[Buffer] 新建 key={session_key} bucket_id={pending.bucket_id} delay={delay:.1f}s")
        return False

    # ------------------------------------------------------------------
    # 话题抽取
    # ------------------------------------------------------------------

    def _bucket_topic(self, entries: list[tuple[str, str]]) -> str:
        """从桶内消息抽取高频 2-4 字短语作为话题标签。

        停用词 = 静态通用词 + 用户配置的 backlog_labels 关键词。
        关键词被配置本身就说明它在对话中高频出现，作为话题标签缺乏区分度。
        """
        stop: set[str] = set(_STOP_WORDS)
        try:
            for label in (self.plugin._qq_settings or {}).get("backlog_labels") or []:
                for kw in label.get("keywords") or []:
                    kw = str(kw).strip()
                    if kw:
                        stop.add(kw)
        except Exception:
            pass

        grams: Counter[str] = Counter()
        for _sid, text in entries[-5:]:
            for n in (2, 3, 4):
                for i in range(len(text) - n + 1):
                    gram = text[i:i + n]
                    if gram not in stop:
                        grams[gram] += 1
        if not grams:
            return ""
        return grams.most_common(1)[0][0]

    # ------------------------------------------------------------------
    # 话题偏移检测
    # ------------------------------------------------------------------

    async def _topic_shift(self, existing: PendingReply, new_text: str) -> bool:
        """判断新消息是否和桶内已有消息话题不一致。返回 True 表示需要分桶。"""
        # 快速路径：消息数太少不检测；或新消息很短（大概率不是话题转换）
        if len(existing.entries) < 2:
            return False
        if len(new_text) <= 3:
            return False
        # 提取桶内用户消息
        buf_texts = [t for _sid, t in existing.entries[-5:]][:200]
        if not buf_texts:
            return False
        # 快速预判：新消息和桶内消息有明显重叠 → 省掉 LLM 调用
        if any(new_text.startswith(t) or t.startswith(new_text) for t in buf_texts):
            return False

        old_topic = self._bucket_topic(existing.entries)
        new_topic = self._bucket_topic([("", new_text)])

        # n-gram 话题词直接命中 → 同一话题，跳过 LLM
        if old_topic and new_topic and (old_topic in new_topic or new_topic in old_topic):
            return False

        from utils.config_manager import get_config_manager
        cfg = get_config_manager().get_model_api_config("conversation")
        base_url = str(cfg.get("base_url", ""))
        model = str(cfg.get("model", ""))
        api_key = str(cfg.get("api_key", ""))
        if not base_url or not model:
            return self._topic_shift_heuristic(buf_texts, new_text, old_topic, new_topic)

        prompt = (
            "判断新消息是否和已有消息属于同一话题。只回答\"是\"或\"否\"。\n\n"
            f"已有话题：{old_topic or '（未知）'}\n"
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
            is_shift = "否" in answer
            if is_shift:
                self.plugin._emit_log("INFO", f"[Buffer] LLM 判定话题偏离 old=「{old_topic}」new=「{new_topic}」")
            return is_shift
        except Exception:
            pass

        # LLM 失败 → 启发式规则兜底
        result = self._topic_shift_heuristic(buf_texts, new_text, old_topic, new_topic)
        if result:
            self.plugin._emit_log("INFO", f"[Buffer] 启发式判定话题偏离 old=「{old_topic}」new=「{new_topic}」")
        return result

    @staticmethod
    def _topic_shift_heuristic(buf_texts: list[str], new_text: str,
                               old_topic: str = "", new_topic: str = "") -> bool:
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

    # ------------------------------------------------------------------
    # 交付
    # ------------------------------------------------------------------

    async def _flush_detached(self, session_key: str, pending: PendingReply) -> None:
        """已从 _pending 摘除的桶的交付逻辑——复合键保证不误删其他桶。"""
        try:
            await self._flush_impl(session_key, pending, check_pending=False)
        finally:
            self._detached.pop(self._detached_key(session_key, pending.bucket_id), None)

    async def _flush(self, session_key: str, pending: PendingReply, *, abandon_on_no_reply: bool = False) -> None:
        await self._flush_impl(session_key, pending, check_pending=True, abandon_on_no_reply=abandon_on_no_reply)

    async def _flush_impl(self, session_key: str, pending: PendingReply, *, check_pending: bool, abandon_on_no_reply: bool = False) -> None:
        delay = max(0.0, pending.wait_until - time.time())
        gen = pending.task_gen
        self.plugin._emit_log("DEBUG", f"[Buffer] 等待中 key={session_key} gen={gen} delay={delay:.1f}s count={len(pending.entries)}")
        try:
            await asyncio.sleep(delay)
        except asyncio.CancelledError:
            if check_pending and self._pending.get(session_key) is pending and pending.task_gen == gen:
                self._pending.pop(session_key, None)
            if not check_pending:
                self._detached.pop(self._detached_key(session_key, pending.bucket_id), None)
            return

        if check_pending and self._pending.get(session_key) is not pending:
            return

        user_entries = pending.entries
        has_bot_reply = pending.bot_blocks is not None

        if not has_bot_reply:
            if abandon_on_no_reply:
                # 话题偏移：旧桶移入 _detached 等 pipeline 回复，再从 _pending 摘除
                self._detached[self._detached_key(session_key, pending.bucket_id)] = pending
                self._pending.pop(session_key, None)
                self.plugin._emit_log("INFO", f"[Buffer] key={session_key} 话题偏移，旧桶移入 _detached 等 pipeline 回复")
                return
            # 多条消息（≥2）→ 首条 pipeline 回复会被 summary 覆盖，无需等待
            if len(user_entries) > 1:
                self.plugin._emit_log("INFO", f"[Buffer] key={session_key} {len(user_entries)}条消息直接汇总，不等待首条 pipeline")
            else:
                # 单条消息：LLM 还没生成完 → 等 1 秒重试，最多 30 次（30 秒）
                # 不递增 task_gen——gate cancel 才能对上号取消掉
                retries = getattr(pending, "_no_reply_retries", 0) + 1
                pending._no_reply_retries = retries
                if retries > 30:
                    self.plugin._emit_log("WARN", f"[Buffer] key={session_key} 等待回复超时，跳过交付")
                    if check_pending:
                        self._pending.pop(session_key, None)
                    else:
                        self._detached.pop(self._detached_key(session_key, pending.bucket_id), None)
                    return
                pending.wait_until = time.time() + 1.0
                pending.task = asyncio.create_task(
                    self._flush(session_key, pending) if check_pending else self._flush_detached(session_key, pending)
                )
                return

        # pop 再交付——交付期间新消息进来会建新桶，不会污染当前桶
        if check_pending:
            self._pending.pop(session_key, None)

        if len(user_entries) == 1:
            # 只有首条用户消息 + bot 回复：直接交付 bot 回复
            from .pipeline_models import QQMessageBlock, QQDeliveryPlan
            blocks = pending.bot_blocks if pending.bot_blocks else [QQMessageBlock(text=user_entries[0][1])]
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
