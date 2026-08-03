"""注意力门控服务 — 基于多群注意力竞争的消息分发决策

职责：
1. 每条群消息到达时，更新该群注意力、判定是否回复
2. 检测焦点群切换，触发回溯补回流程
3. 回溯补回：摘要 → LLM 挑选需回复的消息 → 逐条补回
4. 全局休眠判定

底层依赖 QQAttentionService 提供注意力分数、衰减、focus 判定。
"""

from __future__ import annotations

import asyncio
from typing import Any, Optional

from .feedback_classifier import QQFeedbackClassifier
from .pipeline_models import QQReplyRequest


class GateDecision:
    """门控决策结果"""
    __slots__ = ("action", "reason", "force_reply")

    def __init__(self, action: str, reason: str = "", force_reply: bool = False):
        self.action = action      # "reply" | "ignore"
        self.reason = reason
        self.force_reply = force_reply


class FocusShiftResult:
    """焦点切换结果"""
    __slots__ = ("previous_focus_group", "new_focus_group", "triggered_at")

    def __init__(self, previous_focus_group: str = "", new_focus_group: str = "", triggered_at: int = 0):
        self.previous_focus_group = previous_focus_group
        self.new_focus_group = new_focus_group
        self.triggered_at = triggered_at


class QQAttentionGateService:
    """基于注意力的多群门控 + 回溯补回（含疲劳睡眠）"""

    async def start_proactive_loop(self) -> None:
        """保留接口兼容性——破冰由焦点切换冷场计数触发。"""
        pass

    async def stop_proactive_loop(self) -> None:
        """保留接口兼容性。"""
        pass

    def _touch_group(self, group_id: str) -> None:
        """保留接口兼容性——冷场检测已改为焦点切换计数。"""
        pass

    def _mark_active(self, group_id: str) -> None:
        """标记群活跃（回复后调用，更新疲劳计时）。"""
        fatigue = getattr(self.plugin, "fatigue_service", None)
        if fatigue:
            fatigue.mark_active(f"group:{group_id}")

    # ── 冷场破冰：焦点反复落到同一群但无人发言时触发 ──

    _DEFAULT_PROACTIVE_TOPICS = [
        "群聊已经安静了一段时间，你可以主动在群里说点什么来活跃气氛。分享一个想法、提一个有趣的问题、或者聊聊你最近经历的事。注意保持自然，不要像系统消息一样说话。",
        "群里好像冷场了，你可以随便聊点轻松的——比如最近看到的有趣的事、一个冷知识、或者问问大家最近都在忙什么。",
        "你是这个群的活跃分子，看到没人说话，可以抛出一个话题暖暖场。不用很正式，像朋友闲聊一样自然开头就好。",
    ]

    def _pick_proactive_topic(self) -> str:
        """从用户配置的 proactive_topics 中随机选一个，避免连续重复。"""
        import random as _random
        topics = list((self.plugin._qq_settings or {}).get("proactive_topics") or [])
        if not topics:
            topics = list(self._DEFAULT_PROACTIVE_TOPICS)
        if not topics:
            return ""
        topic = _random.choice(topics)
        if len(topics) > 1:
            last = getattr(self, "_last_proactive_topic_idx", -1)
            tries = 0
            while topics.index(topic) == last and tries < 10:
                topic = _random.choice(topics)
                tries += 1
        self._last_proactive_topic_idx = topics.index(topic)
        return topic

    async def _try_icebreaker(self, group_id: str) -> bool:
        """焦点反复切到此群但无人发言 → 用主动话题破冰。"""
        # 有缓冲回复待交付时跳过
        if getattr(self.plugin, "reply_buffer_service", None):
            gkey = self.plugin._build_session_key(sender_id=group_id, is_group=True, group_id=group_id)
            if self.plugin.reply_buffer_service.has_pending(gkey):
                self._logger.info("[Icebreaker] 群有缓冲回复待交付，跳过")
                return False
        # 疲劳检查（过高则跳过破冰）
        fatigue = getattr(self.plugin, "fatigue_service", None)
        if fatigue and fatigue.calculate_fatigue(f"group:{group_id}") > 60:
            self._logger.info("[Icebreaker] 疲劳过高，跳过")
            return False
        topic = self._pick_proactive_topic()
        if not topic:
            return False
        self._logger.info(f"[Icebreaker] 群 {group_id} 尝试破冰话题: {topic[:40]}")
        try:
            from .pipeline_models import QQReplyRequest
            request = QQReplyRequest(
                message_text=f"[系统] {topic}",
                sender_id=self.plugin._admin_qq or "0",
                is_group=True,
                group_id=group_id,
                is_at_bot=True,
                source_kind="proactive_speech",
                group_scene_mode="group_collective",
                fallback_to_text_on_voice_failure=True,
                use_memory_context=False,
                ephemeral_session=False,
            )
            async def _run_icebreaker():
                svc = self.plugin.session_memory_service
                before = svc.session_history_len(f"group:{group_id}")
                try:
                    return await self.plugin.reply_pipeline.run(request)
                finally:
                    svc.record_synthetic_prompt_rows(f"group:{group_id}", before)
            outcome = await self.plugin._run_with_session_lock(
                f"group:{group_id}", _run_icebreaker,
            )
            if outcome.action == "reply" and outcome.reply_text:
                self._logger.info(f"[Icebreaker] 破冰消息已发送: {outcome.reply_text[:50]}...")
                self.plugin.runtime_service.record_pipeline_outcome(
                    source="proactive_speech", request=request, outcome=outcome,
                )
                # 更新活跃时间并持久化，防止重复触发
                attn = getattr(self.plugin, "attention_service", None)
                if attn:
                    state = attn.get_state(group_id)
                    state.last_reply_at = attn._current_time()
                    attn._write_state(state)
                return True
            else:
                self._logger.info("[Icebreaker] AI 决定不回应破冰话题")
        except Exception:
            self._logger.warning("[Icebreaker] 破冰话题发送失败", exc_info=True)
        return False

    def __init__(self, plugin: Any):
        self.plugin = plugin
        self._last_focus_group: str = ""
        self._focus_shifting: bool = False
        self._retroactive_lock = asyncio.Lock()
        self._digest_tasks: set[asyncio.Task] = set()
        self._cold_focus_count: dict[str, int] = {}  # 群 → 连续冷场切换次数
        self._reply_timestamps: dict[str, list[int]] = {}  # 群 → 最近回复时间戳列表
        self._logger = plugin.logger

    def _check_reply_burst(self, group_id: str, now: int) -> bool:
        """检查最近是否回复过于频繁：60秒内超过3条 → 强制静默。"""
        timestamps = self._reply_timestamps.get(group_id, [])
        window = 60  # 60 秒窗口
        max_replies = 3  # 最多 3 条
        # 清理过期记录
        timestamps[:] = [t for t in timestamps if now - t < window]
        return len(timestamps) >= max_replies

    def _record_reply(self, group_id: str, now: int) -> None:
        """记录一次回复时间戳。"""
        ts = self._reply_timestamps.setdefault(group_id, [])
        ts.append(now)
        # 只保留最近 10 条
        if len(ts) > 10:
            ts[:] = ts[-10:]

    # ==========================================
    # 消息评估
    # ==========================================

    async def evaluate(
        self,
        *,
        group_id: str,
        sender_id: str,
        is_at_bot: bool = False,
        message_text: str = "",
        message_id: str = "",
        quoted_message_id: str = "",
        sender_nickname: str = "",
        timestamp: int = 0,
    ) -> GateDecision:
        """评估群聊消息：更新注意力 → 判定是否回复"""
        # 无需注意力的连接（如 QQ 开放平台）：直接回复
        if self.plugin.qq_client and not self.plugin.qq_client.needs_attention:
            return GateDecision("reply", reason="no_attention_needed", force_reply=is_at_bot)

        attention = self.plugin.attention_service
        if not attention or not attention._enabled():
            if self.plugin.permission_mgr and self.plugin.permission_mgr.get_permission_level(sender_id) == "admin":
                return GateDecision("reply", reason="admin_priority")
            if is_at_bot:
                return GateDecision("reply", reason="at_bot_fallback")
            self.plugin._emit_log("INFO", f"[Gate] 群{group_id} 忽略: 注意力未启用")
            return GateDecision("ignore", reason="attention_disabled")

        normalized_group_id = str(group_id or "").strip()

        # 0. 记录消息时间（用于主动发言检测）
        self._touch_group(normalized_group_id)

        # 1. 消息更新注意力
        await attention.update_on_message({
            "group_id": normalized_group_id,
            "user_id": sender_id,
            "content": message_text,
            "message_id": message_id,
            "timestamp": timestamp or attention._current_time(),
            "is_at_bot": is_at_bot,
        })

        # 2. @bot → 必定回复（抢焦点 + 注意力 boost）
        if is_at_bot:
            attention.mark_focus(normalized_group_id)
            attention.wake_boost(normalized_group_id)
            return GateDecision("reply", reason="at_bot", force_reply=True)

        # 3. 黑名单 → 不处理
        label_defs = list((self.plugin._qq_settings or {}).get("backlog_labels") or [])
        if QQFeedbackClassifier.is_blacklisted(message_text, label_defs):
            return GateDecision("ignore", reason="blacklist")

        # 4. 关键词 → 必定回复（抢焦点 + 注意力 boost）
        category = QQFeedbackClassifier.classify(message_text, label_defs)
        if category == "mention" and not is_at_bot:
            category = "chat"
        if category and category != "chat":
            attention.mark_focus(normalized_group_id)
            attention.wake_boost(normalized_group_id)
            return GateDecision("reply", reason=f"keyword:{category}", force_reply=True)

        # 5. 回复 bot 的消息 → 等同于被点名，强制回复
        is_reply_to_bot = bool(
            quoted_message_id and self.plugin.qq_client
            and quoted_message_id in getattr(self.plugin.qq_client, "_sent_message_ids", {})
        )
        if is_reply_to_bot:
            attention.mark_focus(normalized_group_id)
            attention.wake_boost(normalized_group_id)
            return GateDecision("reply", reason="reply_to_bot", force_reply=True)

        # 6. 回复频率门控：60秒内超过3条回复 → 强制静默
        now_ts = attention._current_time()
        if not is_at_bot and self._check_reply_burst(normalized_group_id, now_ts):
            self.plugin._emit_log("INFO", f"[Gate] 群{normalized_group_id} 回复过于频繁，强制静默")
            return GateDecision("ignore", reason="reply_burst_limit")

        # 7. 当前焦点群 → 检查注意力是否足够
        focus_group = attention.get_focus_group()
        if focus_group == normalized_group_id:
            current_score = float(attention.get_state(normalized_group_id).attention_score)
            min_threshold = attention._minimum_threshold()
            if current_score < min_threshold:
                self.plugin._emit_log("INFO", f"[Gate] 焦点群{normalized_group_id} 注意力过低({current_score:.1f}<{min_threshold}), 忽略")
                return GateDecision("ignore", reason=f"focus_low_attention({current_score:.1f})")
            self._mark_active(normalized_group_id)
            self.plugin._emit_log("INFO", f"[Attention] 焦点群 {normalized_group_id} 消息, LLM自行判断是否回复")
            return GateDecision("reply", reason="focus_group")

        # 8. 非焦点群 → 忽略，消息记入 backlog_store 未审阅，
        #    等该群成为焦点时由回溯补回统一处理。
        self.plugin._emit_log("INFO", f"[Gate] 群{normalized_group_id} 忽略: 非焦点群 (score={attention.get_state(normalized_group_id).attention_score:.1f})")
        return GateDecision("ignore", reason="non_focus")

    # ==========================================
    # 回复后消耗 + 焦点切换检测
    # ==========================================

    async def on_reply_sent(self, group_id: str) -> None:
        """回复已发送 → 消耗注意力 + 记录活跃 + 频率计数"""
        attention = self.plugin.attention_service
        if attention:
            now = attention._current_time()
            await attention.update_on_reply(group_id)
        else:
            now = int(__import__("time").time())
        self._mark_active(group_id)
        self._record_reply(str(group_id or "").strip(), now)

    async def check_focus_shift(self) -> FocusShiftResult | None:
        """检测焦点群是否切换"""
        attention = self.plugin.attention_service
        if not attention:
            return None

        new_focus = attention.get_focus_group()
        previous = self._last_focus_group
        if new_focus and new_focus != previous:
            self._last_focus_group = new_focus
            self._logger.info(f"[AttentionGate] 焦点切换: {previous or '无'} → {new_focus}")
            if previous:
                digest_task = asyncio.create_task(self._push_group_digest(previous))
                self._digest_tasks.add(digest_task)
                self.plugin._group_digest_task = digest_task

                def _clear_digest_task(done_task: asyncio.Task) -> None:
                    self._digest_tasks.discard(done_task)
                    if self.plugin._group_digest_task is done_task:
                        self.plugin._group_digest_task = None

                digest_task.add_done_callback(_clear_digest_task)
            return FocusShiftResult(
                previous_focus_group=previous or "",
                new_focus_group=new_focus,
                triggered_at=attention._current_time(),
            )
        if previous and not new_focus:
            # 全局休眠
            self._last_focus_group = ""
            self._logger.info("[AttentionGate] 全局休眠：所有群注意力过低")
        return None

    # ==========================================
    # 回溯补回流程
    # ==========================================

    async def run_retroactive_review(self, group_id: str) -> list[str]:
        """焦点切换到 group_id 后，对忽略消息做回溯补回"""
        async with self._retroactive_lock:
            return await self._run_retroactive_review_locked(group_id)

    async def _run_retroactive_review_locked(self, group_id: str) -> list[str]:
        attention = self.plugin.attention_service
        if not attention:
            return []

        # 1. 从统一 backlog_store 取出上次 focus 以来的未审核消息
        since = attention.get_last_focus_at(group_id)
        if not hasattr(self.plugin, "backlog_store") or not self.plugin.backlog_store:
            self._logger.warning(f"[RetroReview] backlog_store 不可用，跳过回溯")
            return []
        max_messages = int((self.plugin._qq_settings or {}).get("retroactive_review_max_messages", 30) or 30)
        unreviewed = await self.plugin.backlog_store.get_unreviewed_messages_since(group_id, since_timestamp=since, limit=max_messages)
        if not unreviewed:
            self._logger.info(f"[RetroReview] 群 {group_id} 无未审核消息，跳过回溯")
            count = self._cold_focus_count.get(group_id, 0) + 1
            self._cold_focus_count[group_id] = count
            threshold = int((self.plugin._qq_settings or {}).get("icebreaker_cold_threshold", 3) or 3)
            if threshold > 0 and count >= threshold:
                self._logger.info(f"[Icebreaker] 群 {group_id} 连续 {count} 次冷场切换，尝试破冰")
                await self._try_icebreaker(group_id)
                self._cold_focus_count[group_id] = 0
            try:
                await self.plugin.backlog_service.mark_group_reviewed_payload(group_id)
            except Exception:
                pass
            return []

        # 有未审消息 → 重置冷场计数
        self._cold_focus_count.pop(group_id, None)
        self._logger.info(f"[RetroReview] 群 {group_id} 有 {len(unreviewed)} 条未审核消息，开始回溯")

        # 2. 复用缓冲链路：构造总结 prompt，针对 1-2 条消息用 <reply> 回应
        summary, id_map = self._build_ignored_summary(unreviewed)
        id_hints = "\n".join(f"  · 编号[{n}]的 message_id = {mid}" for n, mid in sorted(id_map.items()))
        try:
            from .pipeline_models import QQReplyRequest
            request = QQReplyRequest(
                message_text=(
                    f"[系统] 你刚才没有太关注这个群，以下是这段时间群友们聊天的消息摘要。\n"
                    f"请针对其中 1-2 条你最感兴趣的，用 `<reply>消息ID</reply>` 引用后自然回应。不要逐条点评，不要超过两条。\n\n"
                    f"消息ID映射：\n{id_hints}\n\n"
                    f"摘要：\n{summary}"
                ),
                sender_id=self.plugin._admin_qq or "0",
                is_group=True,
                group_id=group_id,
                is_at_bot=True,
                source_kind="retroactive_review",
                group_scene_mode="group_collective",
                fallback_to_text_on_voice_failure=True,
                use_memory_context=False,
                ephemeral_session=False,
            )
            async def _run_retro():
                svc = self.plugin.session_memory_service
                before = svc.session_history_len(f"group:{group_id}")
                try:
                    return await self.plugin.reply_pipeline.run(request)
                finally:
                    svc.record_synthetic_prompt_rows(f"group:{group_id}", before)
            outcome = await self.plugin._run_with_session_lock(
                f"group:{group_id}", _run_retro,
            )
            self.plugin.runtime_service.record_pipeline_outcome(
                source=request.source_kind, request=request, outcome=outcome,
            )
            if outcome.action == "reply" and outcome.reply_text:
                self._logger.info(f"[RetroReview] 回溯回复已发送: {outcome.reply_text[:50]}...")
            else:
                self._logger.info("[RetroReview] LLM 决定不回复回溯摘要")
        except Exception as e:
            self._logger.warning(f"[RetroReview] 回溯总结失败: {e}")

        # 3. 标记已读
        attention.mark_focus(group_id)
        try:
            await self.plugin.backlog_service.mark_group_reviewed_payload(group_id)
            self._logger.info(f"[RetroReview] 群 {group_id} 已标记为已审阅")
        except Exception as e:
            self._logger.warning(f"[RetroReview] 标记已审阅失败: {e}")
        return []

    # ==========================================
    # 回溯辅助方法
    # ==========================================

    @staticmethod
    def _build_ignored_summary(messages: list[dict[str, Any]]) -> tuple[str, dict[int, str]]:
        """把被忽略的消息列表生成 LLM 可读的摘要，返回 (摘要文本, {编号: message_id})"""
        lines: list[str] = []
        id_map: dict[int, str] = {}
        for i, msg in enumerate(messages, 1):
            nickname = str(msg.get("sender_nickname") or msg.get("sender_id") or "未知")
            text = str(msg.get("message_text") or "").strip()
            if len(text) > 100:
                text = text[:97] + "..."
            msg_id = str(msg.get("message_id") or "")
            lines.append(f"[{i}] {nickname}: {text}")
            if msg_id:
                id_map[i] = msg_id
        return "\n".join(lines), id_map

    async def _push_group_digest(self, group_id: str) -> None:
        """焦点切换时将旧焦点群的完整会话摘要推送到 Memory Server"""
        try:
            if not bool((getattr(self.plugin, "_qq_settings", {}) or {}).get(
                "group_memory_enabled", False,
            )):
                return
            session_key = f"group:{group_id}"
            sessions = getattr(self.plugin, "_user_sessions", {}) or {}
            s = sessions.get(session_key)
            if not isinstance(s, dict):
                return
            async def _push_delta() -> int:
                # 锁内复检：外层 setting 检查通过后可能排队等锁，期间用户
                # 关掉群记忆——opt-out 之后不得再推送 digest。
                if not bool((getattr(self.plugin, "_qq_settings", {}) or {}).get(
                    "group_memory_enabled", False,
                )):
                    return 0
                if (getattr(self.plugin, "_user_sessions", {}) or {}).get(
                    session_key
                ) is not s:
                    # 等锁期间 finalizer/discard 可能已结算并弹出会话：
                    # 陈旧引用继续推会重发已结算历史、推进无主游标。
                    return 0
                if s.get("pending_disable_settle"):
                    # opt-out 结算未完成（快速 re-enable 会让上面的 setting
                    # 检查重新通过）：digest 不碰，交转变任务按 cutoff 结算。
                    return 0
                if s.get("pending_enable_rebase") is not None:
                    # retain 结算后、ON rebase 前的 limbo：游标还停在
                    # opt-out 区间之前，此处推送只剩 nonconsent floor 一道
                    # 防线兜着未授权行。交 rebase 任务先把游标规整过界。
                    return 0
                session = s.get("session")
                if not session or not hasattr(session, "_conversation_history"):
                    return 0
                history = getattr(session, "_conversation_history", []) or []
                if len(history) < 4:
                    return 0
                # 先旧后新分批 + 精确游标（对偶 finalize 的同名修复）：旧写法
                # `[-200:]` 会把超窗中段永久跳过、游标却跳到 len(history)，
                # 之后 finalize 也无从补救。失败即停，游标停在最后一个成功
                # 批，剩余留给下一次 digest/finalize。
                svc = self.plugin.session_memory_service
                start_index = max(0, int(s.get("last_group_digest_index", 0)))
                start_index = max(
                    start_index,
                    int(s.get("nonconsent_history_end", 0) or 0),
                )
                if start_index > len(history):
                    # 历史被重复守卫重置/收缩：钳游标，防新增轮次被
                    # 永久跳过（对偶 finalize 的同名钳制）。
                    start_index = len(history)
                    s["last_group_digest_index"] = start_index
                total_sent = 0
                # 限批：focus-shift 推送持有会话锁，慢 memory server 下
                # 无界批次会让 3 个排水群占满全局 Semaphore(3) 冻结全部
                # 消息处理。每次最多 3 批，游标精确，剩余留给下一次
                # digest / finalize（结算 of record 在 finalize，不丢）。
                remaining_batches = 3
                while remaining_batches > 0:
                    remaining_batches -= 1
                    messages, next_index = svc._slice_group_history_batch(
                        history, start_index, svc.GROUP_HISTORY_MAX_MESSAGES,
                        user_data=s, stop_at_provisional=True,
                    )
                    if not messages:
                        if next_index > start_index:
                            s["last_group_digest_index"] = next_index
                        break
                    # 拿不到群名就不带参（对偶 finalize 的 digest 调用）。
                    digest_extra = {}
                    group_display_name = svc._group_display_name(group_id)
                    if group_display_name:
                        digest_extra["display_name"] = group_display_name
                    await self.plugin.memory_bridge.post_scoped_memory_history(
                        str(s.get("her_name") or "neko"),
                        messages,
                        subject=self.plugin.memory_bridge.group_subject(group_id),
                        timeout=30.0,
                        **digest_extra,
                    )
                    s["last_group_digest_index"] = next_index
                    start_index = next_index
                    total_sent += len(messages)
                return total_sent

            sent_messages = await self.plugin._run_with_session_lock(session_key, _push_delta)
            if sent_messages:
                self._logger.info(f"[Digest] 群 {group_id} 已推送摘要到 Memory Server ({sent_messages}条)")
        except Exception as e:
            self._logger.warning(f"[Digest] 推送失败: {e}")

    # ==========================================
    # 公共查询
    # ==========================================

    def is_global_sleep(self) -> bool:
        attention = self.plugin.attention_service
        if not attention:
            return False
        return attention.is_global_sleep()

    def get_focus_group(self) -> str | None:
        attention = self.plugin.attention_service
        if not attention:
            return None
        return attention.get_focus_group()

    # ==========================================
    # 生命周期
    # ==========================================

    async def shutdown(self) -> None:
        self._last_focus_group = ""
        self._focus_shifting = False
        digest_tasks = list(self._digest_tasks)
        for task in digest_tasks:
            if not task.done():
                task.cancel()
        if digest_tasks:
            await asyncio.gather(*digest_tasks, return_exceptions=True)
        self._digest_tasks.clear()
        self.plugin._group_digest_task = None
        if self.plugin.attention_service:
            await self.plugin.attention_service.stop_decay_loop()
        self._cold_focus_count.clear()
        self._logger.info("[AttentionGate] 已关闭")
