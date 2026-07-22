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
    """基于注意力的多群门控 + 回溯补回"""

    _RETROACTIVE_PICK_PROMPT = (
        "你刚才没有太关注这个群，以下是这段时间群友们聊天的消息摘要：\n\n"
        "{summary}\n\n"
        "请判断：哪些消息是你需要回复的？只返回需要回复的消息编号列表，"
        "用 JSON 数组格式如 [1, 3, 5]。"
        "如果都不需要回复，返回空数组 []。"
        "不要返回任何其他内容。"
    )

    def __init__(self, plugin: Any):
        self.plugin = plugin
        self._last_focus_group: str = ""
        self._focus_shifting: bool = False
        self._retroactive_lock = asyncio.Lock()
        self._digest_tasks: set[asyncio.Task] = set()
        self._logger = plugin.logger

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
            return GateDecision("ignore", reason="attention_disabled")

        normalized_group_id = str(group_id or "").strip()

        # 1. 消息更新注意力
        await attention.update_on_message({
            "group_id": normalized_group_id,
            "user_id": sender_id,
            "content": message_text,
            "message_id": message_id,
            "timestamp": timestamp or attention._current_time(),
            "is_at_bot": is_at_bot,
        })

        # 2. @bot → 必定回复（强制唤醒）
        if is_at_bot:
            attention.mark_focus(normalized_group_id)
            return GateDecision("reply", reason="at_bot", force_reply=True)

        # 4. 黑名单 → 不处理
        label_defs = list((self.plugin._qq_settings or {}).get("backlog_labels") or [])
        if QQFeedbackClassifier.is_blacklisted(message_text, label_defs):
            return GateDecision("ignore", reason="blacklist")

        # 4. 关键词 → 必定回复（注意力已在 update_on_message 内完成加成，此处只标记焦点）
        category = QQFeedbackClassifier.classify(message_text, label_defs)
        # mention 分类仅对真正 @bot（或 @全体/引用 bot）生效，避免 @其他人触发回复
        if category == "mention" and not is_at_bot:
            category = "chat"
        if category and category != "chat":
            attention.mark_focus(normalized_group_id)
            return GateDecision("reply", reason=f"keyword:{category}", force_reply=True)

        # 4. 当前焦点群 → 回复（LLM 自行判断）
        focus_group = attention.get_focus_group()
        if focus_group == normalized_group_id:
            self.plugin._emit_log("INFO", f"[Attention] 焦点群 {normalized_group_id} 消息, LLM自行判断是否回复")
            return GateDecision("reply", reason="focus_group")

        # 5. 注意力接近焦点群 → 回复
        if focus_group:
            focus_score = attention.get_focus_score()
            state = attention.get_state(normalized_group_id)
            decayed = attention._apply_decay(state, attention._current_time(), is_focus=False)
            group_score = float(decayed.attention_score)
            gap = max(0.0, focus_score - group_score)
            if gap < attention._focus_threshold():
                return GateDecision("reply", reason="near_focus")

        # 6. 注意力太低 → 忽略，记录供回溯
        attention.record_ignored_message(
            normalized_group_id,
            message_id=message_id,
            message_text=message_text,
            sender_id=sender_id,
            sender_nickname=sender_nickname,
            timestamp=timestamp,
        )
        return GateDecision("ignore", reason="low_attention")

    # ==========================================
    # 回复后消耗 + 焦点切换检测
    # ==========================================

    async def on_reply_sent(self, group_id: str) -> None:
        """回复已发送 → 消耗注意力"""
        attention = self.plugin.attention_service
        if not attention:
            return
        await attention.update_on_reply(group_id)

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
            # 原焦点群进入冷却期，防止立即抢回焦点
            if previous:
                attention.set_focus_cooldown(previous)
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

        # 1. 取出上次 focus 以来的被忽略消息
        since = attention.get_last_focus_at(group_id)
        ignored = attention.get_ignored_messages_since(group_id, since_timestamp=since)
        if not ignored:
            self._logger.info(f"[RetroReview] 群 {group_id} 无忽略消息，跳过回溯")
            try:
                await self.plugin.backlog_service.mark_group_reviewed_payload(group_id)
            except Exception:
                pass
            return []

        max_messages = int((self.plugin._qq_settings or {}).get("retroactive_review_max_messages", 30) or 30)
        ignored = ignored[-max_messages:]
        self._logger.info(f"[RetroReview] 群 {group_id} 有 {len(ignored)} 条忽略消息，开始回溯")

        # 2. 生成摘要 → LLM 挑选
        summary = self._build_ignored_summary(ignored)
        pick_indices = await self._ask_llm_pick_messages(summary, len(ignored))
        if not pick_indices:
            self._logger.info(f"[RetroReview] LLM 判定无需补回任何消息")
            attention.mark_focus(group_id)
            attention.clear_ignored_messages(group_id)
            try:
                await self.plugin.backlog_service.mark_group_reviewed_payload(group_id)
            except Exception:
                pass
            return []

        max_reply = int((self.plugin._qq_settings or {}).get("retroactive_review_max_reply", 5) or 5)
        pick_indices = pick_indices[:max_reply]

        # 3. 逐条补回
        replied_ids: list[str] = []
        for idx in pick_indices:
            if idx < 1 or idx > len(ignored):
                continue
            msg = ignored[idx - 1]
            try:
                did_reply = await self._reply_to_ignored_message(group_id, msg)
                if did_reply:
                    replied_ids.append(str(msg.get("message_id") or ""))
                    await attention.consume_attention(group_id, attention._reply_penalty(), reason="retro_reply")
            except Exception as e:
                self._logger.warning(f"[RetroReview] 补回消息 #{idx} 失败: {e}")

        # 4. 清理 + 标记已读（猫娘已看过摘要，相当于已审阅）
        attention.mark_focus(group_id)
        attention.clear_ignored_messages(group_id)
        try:
            await self.plugin.backlog_service.mark_group_reviewed_payload(group_id)
            self._logger.info(f"[RetroReview] 群 {group_id} 已标记为已审阅")
        except Exception as e:
            self._logger.warning(f"[RetroReview] 标记已审阅失败: {e}")
        self._logger.info(f"[RetroReview] 群 {group_id} 回溯完成，共补回 {len(replied_ids)} 条")
        return replied_ids

    # ==========================================
    # 回溯辅助方法
    # ==========================================

    @staticmethod
    def _build_ignored_summary(messages: list[dict[str, Any]]) -> str:
        """把被忽略的消息列表生成 LLM 可读的摘要"""
        lines: list[str] = []
        for i, msg in enumerate(messages, 1):
            nickname = str(msg.get("sender_nickname") or msg.get("sender_id") or "未知")
            text = str(msg.get("message_text") or "").strip()
            if len(text) > 100:
                text = text[:97] + "..."
            lines.append(f"[{i}] {nickname}: {text}")
        return "\n".join(lines)

    async def _ask_llm_pick_messages(self, summary: str, total_count: int) -> list[int]:
        """让 LLM 从摘要中挑选需要回复的消息编号"""
        import json

        prompt = self._RETROACTIVE_PICK_PROMPT.format(summary=summary)
        try:
            from utils.config_manager import get_config_manager
            from utils.llm_client import create_chat_llm_async
            from utils.token_tracker import set_call_type

            model_config = get_config_manager().get_model_api_config("conversation")
            base_url = str(model_config.get("base_url") or "").strip()
            model = str(model_config.get("model") or "").strip()
            api_key = str(model_config.get("api_key") or "").strip()
            if not base_url or not model:
                self._logger.warning("[RetroReview] agent 模型未配置，跳过回溯挑选")
                return []

            llm = await create_chat_llm_async(
                model=model,
                base_url=base_url,
                api_key=api_key,
                max_completion_tokens=200,
                timeout=15.0,
                provider_type=model_config.get("provider_type"),
            )
            try:
                set_call_type("conversation")
                response = await llm.ainvoke([
                    {"role": "user", "content": prompt},
                ])
                raw = str(getattr(response, "content", "") or "").strip()
                # 提取 JSON 数组
                json_str = raw
                if "[" in raw and "]" in raw:
                    json_str = raw[raw.find("["):raw.rfind("]") + 1]
                picks = json.loads(json_str)
                if isinstance(picks, list):
                    return [int(p) for p in picks if isinstance(p, (int, float)) and 1 <= int(p) <= total_count]
                return []
            finally:
                aclose = getattr(llm, "aclose", None)
                if callable(aclose):
                    try:
                        await aclose()
                    except Exception:
                        pass
        except Exception as e:
            self._logger.warning(f"[RetroReview] LLM 挑选失败: {e}")
            return []

    async def _reply_to_ignored_message(self, group_id: str, msg: dict[str, Any]) -> bool:
        """对单条被忽略的消息生成回复"""
        message_text = str(msg.get("message_text") or "").strip()
        sender_id = str(msg.get("sender_id") or "").strip()
        sender_nickname = str(msg.get("sender_nickname") or "").strip()
        if not message_text:
            return False

        request = QQReplyRequest(
            message_text=f"[回溯补回] {sender_nickname} 之前说：{message_text}",
            sender_id=sender_id,
            is_group=True,
            group_id=group_id,
            user_nickname=sender_nickname or None,
            is_at_bot=False,
            source_kind="retroactive_review",
            group_scene_mode="shared_context",
            fallback_to_text_on_voice_failure=True,
        )
        try:
            outcome = await self.plugin.reply_pipeline.run(request)
            self.plugin.runtime_service.record_pipeline_outcome(
                source=request.source_kind, request=request, outcome=outcome,
            )
            if outcome.action == "reply" and outcome.reply_text:
                self._logger.info(f"[RetroReview] 补回群 {group_id} 一条消息: {outcome.reply_text[:50]}...")
                return True
            return False
        except Exception as e:
            self._logger.warning(f"[RetroReview] 补回请求失败: {e}")
            return False

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
            session = s.get("session")
            if not session or not hasattr(session, "_conversation_history"):
                return
            history = getattr(session, "_conversation_history", []) or []
            if len(history) < 4:
                return
            her_name = str(s.get("her_name") or "neko")
            messages = self.plugin.session_memory_service.conversation_slice_to_memory_messages(
                history, 0,
            )[-self.plugin.session_memory_service.GROUP_HISTORY_MAX_MESSAGES:]
            if not messages:
                return
            await self.plugin.memory_bridge.post_scoped_memory_history(
                her_name,
                messages,
                subject=self.plugin.memory_bridge.group_subject(group_id),
                timeout=30.0,
            )
            self._logger.info(f"[Digest] 群 {group_id} 完整会话已推送 Memory Server ({len(messages)}条)")
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
        self._logger.info("[AttentionGate] 已关闭")
