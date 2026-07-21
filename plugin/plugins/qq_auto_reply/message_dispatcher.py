from __future__ import annotations

from typing import Any, Optional

from .feedback_classifier import QQFeedbackClassifier
from .pipeline_models import QQReplyRequest


class QQMessageDispatcher:
    def __init__(self, plugin: Any):
        self.plugin = plugin

    def _resolve_poke_nickname(self, user_id: str, raw_msg: dict[str, Any]) -> str:
        """从戳一戳事件中获取用户昵称"""
        uid = str(user_id or "").strip()
        if not uid:
            return "未知用户"
        # 优先用 sender 中的 nickname/card
        sender = raw_msg.get("sender") or {}
        if isinstance(sender, dict):
            nick = sender.get("card") or sender.get("nickname") or ""
            if str(nick).strip():
                return str(nick).strip()
        # 其次查权限管理器中的昵称
        if self.plugin.permission_mgr:
            nick = self.plugin.permission_mgr.get_nickname(uid)
            if nick:
                return nick
        return f"QQ用户{uid}"

    def _has_waking_keyword(self, message_text: str) -> bool:
        """检查消息是否包含唤醒关键词。"""
        text = str(message_text or "").strip()
        if not text:
            return False
        for label in (self.plugin._qq_settings or {}).get("backlog_labels") or []:
            if not isinstance(label, dict):
                continue
            priority = int(label.get("priority") or 0)
            if priority <= 0:
                continue
            for kw in label.get("keywords") or []:
                word = str(kw).strip()
                if word and word in text:
                    return True
        return False

    @staticmethod
    def _looks_like_human_followup(message_text: str) -> bool:
        normalized = str(message_text or "").strip()
        if not normalized:
            return False
        compact = "".join(normalized.split())
        if len(compact) >= 36:
            return False
        followup_prefixes = (
            "不是", "对", "行", "那", "所以", "为啥", "为什么", "你这", "他这", "她这", "这样", "那你", "那他", "那她", "可是", "但是",
        )
        if compact.startswith(followup_prefixes):
            return True
        if compact.endswith(("?", "？", "!", "！")) and len(compact) <= 24:
            return True
        return len(compact) <= 12

    async def _detect_group_interjection_suppression(
        self,
        *,
        group_id: str,
        sender_id: str,
        message_text: str,
        is_at_bot: bool,
        current_message_id: str,
        quoted_message_id: str,
        mentions_other_user: bool,
        message_timestamp: int,
    ) -> str:
        if is_at_bot:
            return ""
        if quoted_message_id:
            return "reply_other_user"
        if mentions_other_user:
            return "mention_other_user"
        if not self._looks_like_human_followup(message_text):
            return ""
        recent_messages = await self.plugin.backlog_store.get_recent_group_messages(
            group_id,
            limit=4,
            exclude_message_id=current_message_id,
        )
        for recent in reversed(recent_messages):
            recent_sender_id = str(recent.get("sender_id") or "").strip()
            if not recent_sender_id or recent_sender_id == sender_id:
                continue
            if bool(recent.get("is_at_bot")):
                continue
            recent_timestamp = int(recent.get("timestamp") or 0)
            if message_timestamp and recent_timestamp and message_timestamp - recent_timestamp > 60:
                return ""
            return "recent_human_followup"
        return ""

    async def process_messages(self):
        while self.plugin._running:
            try:
                message = await self.plugin.qq_client.receive_message()
                if message:
                    task = __import__("asyncio").create_task(self.plugin._run_message_handler(message))
                    self.plugin.handler_runtime_service.track_handler_task(task)
            except __import__("asyncio").CancelledError:
                break
            except Exception as e:
                self.plugin.logger.error(f"Error processing message: {e}")
                await __import__("asyncio").sleep(1)

    async def handle_message(self, message: dict[str, Any]):
        # 戳一戳通知：少量 → 回戳不说话；大量 → 说话不回戳；戳别人 → LLM 决定是否也戳
        if message.get("message_type") == "notice" and message.get("notice_type") == "poke":
            group_id = str(message.get("group_id") or "").strip()
            poker_id = str(message.get("user_id") or "").strip()
            target_id = str(message.get("target_id") or "").strip()
            self_id = str(getattr(self.plugin.qq_client, "_self_id", "") or "")
            if not group_id or not poker_id:
                return
            is_poke_me = bool(self_id and target_id == self_id)
            now = __import__("time").time()
            poker_name = self._resolve_poke_nickname(poker_id, message)
            target_name = self._resolve_poke_nickname(target_id, message) if target_id and not is_poke_me else ""

            if is_poke_me:
                # 统计短时间窗内戳猫娘的人数
                storm = self.plugin._poke_storm.setdefault(group_id, [])
                storm[:] = [(t, p) for t, p in storm if now - t < 30]
                if not any(p == poker_id for p in (p for _, p in storm)):
                    storm.append((now, poker_id))
                storm_count = len(storm)

                # 人数少 → 逐个回戳，不进入 LLM
                if storm_count < 2:
                    timestamps = self.plugin._poke_timestamps.setdefault(poker_id, [])
                    timestamps[:] = [t for t in timestamps if t > now - 300]
                    if len(timestamps) < 2:
                        timestamps.append(now)
                        try:
                            await self.plugin.qq_client.send_group_poke(group_id, poker_id)
                        except Exception as e:
                            self.plugin._emit_log("INFO", f"回戳失败: {e}")
                    return  # 不回话
                # 人数多 → 不回戳，注入 LLM 让猫娘在群里反应（60秒冷却，避免反复刷屏）
                last_storm_key = f"poke_storm_text_{group_id}"
                now_ts = __import__("time").time()
                if now_ts - getattr(self, "_last_poke_storm_text", {}).get(last_storm_key, 0) < 60:
                    return
                if not hasattr(self, "_last_poke_storm_text"):
                    self._last_poke_storm_text = {}
                self._last_poke_storm_text[last_storm_key] = now_ts
                self.plugin._emit_log("INFO", f"戳一戳风暴: group={group_id} {storm_count}人戳猫娘 → 会话模式")
                poke_text = f"[戳一戳] {storm_count}个人戳了戳你，包括 {poker_name}"
                message["is_at_bot"] = True
            else:
                # 戳别人 → LLM 决定是否也戳一下
                if target_name:
                    poke_text = f"[戳一戳] {poker_name} 戳了戳 {target_name}"
                else:
                    poke_text = f"[戳一戳] {poker_name} 戳了戳某人"
                message["is_at_bot"] = False

            message["message_type"] = "group"
            message["group_id"] = group_id
            message["user_id"] = poker_id
            message["content"] = poke_text
            message["raw_message"] = poke_text
            message["message_id"] = f"poke_{group_id}_{poker_id}_{int(now)}"
            # 不 return，继续走正常的注意力门控 + LLM 管道
        # 新人入群通知 → 注入欢迎提示
        if message.get("notice_type") == "group_increase":
            group_id = str(message.get("group_id") or "").strip()
            user_id = str(message.get("user_id") or "").strip()
            if group_id and user_id:
                self.plugin._emit_log("INFO", f"新人入群: group={group_id} user={user_id}")
                message["message_type"] = "group"
                message["group_id"] = group_id
                message["user_id"] = user_id
                message["is_at_bot"] = False
                message["content"] = f"[系统] 新成员 {user_id} 加入了群聊，你可以欢迎一下。注意：要像真人一样自然地欢迎，不要用模板化的欢迎语。"
                message["raw_message"] = message["content"]
                message["message_id"] = f"welcome_{group_id}_{user_id}_{int(__import__('time').time())}"
            # 不 return，走正常 pipeline
        # 黑名单优先：命中负优先级标签 → 不记录、不处理
        label_defs = list((self.plugin._qq_settings or {}).get("backlog_labels") or [])
        raw_content = str(message.get("content") or "").strip()
        if raw_content and QQFeedbackClassifier.is_blacklisted(raw_content, label_defs):
            self.plugin._emit_log("INFO", f"黑名单过滤: text={raw_content[:40]}")
            return
        await self.plugin._record_backlog_message(message)
        if str(message.get("message_type") or "").strip() == "group" and getattr(self.plugin, "attention_service", None):
            if self.plugin.qq_client and self.plugin.qq_client.needs_attention:
                # neko_dynamic 下由 attention_gate_service.evaluate() 统一更新注意力，此处跳过避免双倍计数
                if self.plugin._strategy_mode != "neko_dynamic":
                    await self.plugin.attention_service.update_on_message(message)
        self.plugin._emit_log("INFO", f"收到消息: type={message.get('message_type')} from={message.get('user_id')} text={str(message.get('content',''))[:40]}")
        # ── 疲劳全局消息计数（睡眠判断已移入 attention_gate_service）──
        if getattr(self.plugin, "fatigue_service", None):
            self.plugin.fatigue_service.record_incoming_message()
        message_type = message.get("message_type")
        sender_id = str(message.get("user_id") or "").strip()
        message_text = self.plugin._sanitize_message_text(
            message.get("content", ""),
            is_reply_to_bot=bool(message.get("is_reply_to_bot")),
        )
        attachments = list(message.get("attachments") or [])
        user_nickname = message.get("user_nickname")
        if message_type == "private":
            session_key = self.plugin._build_session_key(sender_id=sender_id, is_group=False)
            if session_key in self.plugin._user_sessions:
                self.plugin._user_sessions[session_key]["last_activity_at"] = __import__("time").time()
            fwd_count = int(message.get("_forward_sub_count", 0) or 0) if isinstance(message, dict) else 0
            await self.handle_private_message(sender_id, message_text, attachments=attachments, user_nickname=user_nickname, forward_sub_count=fwd_count)
        elif message_type == "group":
            group_id = str(message.get("group_id") or "").strip()
            is_at_bot = message.get("is_at_bot", False)
            current_message_id = str(message.get("message_id") or message.get("msg_id") or "").strip()
            quoted_message_id = str(message.get("quoted_message_id") or "").strip()
            mentioned_user_ids = [
                str(user_id or "").strip()
                for user_id in list(message.get("mentioned_user_ids") or [])
                if str(user_id or "").strip()
            ]
            mentions_other_user = bool(message.get("mentions_other_user", False))
            mentions_all = bool(message.get("mentions_all", False))
            message_timestamp = int(message.get("timestamp") or 0)
            session_key = self.plugin._build_session_key(sender_id=sender_id, is_group=True, group_id=group_id)
            if session_key in self.plugin._user_sessions:
                self.plugin._user_sessions[session_key]["last_activity_at"] = __import__("time").time()
            fwd_count = int(message.get("_forward_sub_count", 0) or 0) if isinstance(message, dict) else 0
            await self.handle_group_message(
                group_id,
                sender_id,
                message_text,
                is_at_bot,
                attachments=attachments,
                user_nickname=user_nickname,
                current_message_id=current_message_id,
                quoted_message_id=quoted_message_id,
                mentioned_user_ids=mentioned_user_ids,
                mentions_other_user=mentions_other_user,
                mentions_all=mentions_all,
                message_timestamp=message_timestamp,
                forward_sub_count=fwd_count,
            )
            await self.plugin._maybe_notify_backlog_summary(group_id=group_id)

    async def handle_private_message(self, sender_id: str, message_text: str, attachments: Optional[list[dict[str, Any]]] = None, user_nickname: Optional[str] = None, forward_sub_count: int = 0):
        # 开放平台：第一个私聊用户自动成为管理员，之后可在前端配置
        if self.plugin.qq_client and not self.plugin.qq_client.needs_attention:
            if self.plugin.permission_mgr and not self.plugin.permission_mgr.list_users():
                self.plugin.permission_mgr.add_user(sender_id, "admin", user_nickname or "管理员")
                self.plugin._refresh_admin_qq()
                self.plugin._emit_log("INFO", f"开放平台自动设置管理员: {sender_id}")
                try: await self.plugin.settings_service.persist_business_config()
                except Exception: pass
        self.plugin._emit_log("INFO", f"私聊 pipeline 开始: from={sender_id} text={message_text[:40]}")
        request = QQReplyRequest(
            message_text=message_text,
            sender_id=sender_id,
            attachments=attachments,
            is_group=False,
            user_nickname=user_nickname,
            fallback_to_text_on_voice_failure=True,
            source_kind="incoming_private",
            forward_sub_count=forward_sub_count,
        )
        outcome = await self.plugin.reply_pipeline.run(request)
        if outcome.action == "reply" and outcome.reply_text:
            mid = request.current_message_id if hasattr(request, "current_message_id") else ""
            if mid and hasattr(self.plugin, "backlog_service") and self.plugin.backlog_service:
                await self.plugin.backlog_store.mark_message_reviewed(mid)
        self.plugin._emit_log("INFO", f"私聊 pipeline 结果: action={outcome.action} text={'有' if outcome.reply_text else '空'}")
        self.plugin.runtime_service.record_pipeline_outcome(source=request.source_kind, request=request, outcome=outcome)

    async def handle_group_message(
        self,
        group_id: str,
        sender_id: str,
        message_text: str,
        is_at_bot: bool,
        attachments: Optional[list[dict[str, Any]]] = None,
        user_nickname: Optional[str] = None,
        current_message_id: str = "",
        quoted_message_id: str = "",
        mentioned_user_ids: Optional[list[str]] = None,
        mentions_other_user: bool = False,
        mentions_all: bool = False,
        message_timestamp: int = 0,
        forward_sub_count: int = 0,
    ):
        strategy_mode = getattr(self.plugin, "_strategy_mode", "neko_dynamic")
        force_reply = False
        if strategy_mode == "neko_dynamic" and hasattr(self.plugin, "attention_gate_service") and self.plugin.attention_gate_service is not None:
            gate_decision = await self.plugin.attention_gate_service.evaluate(
                group_id=group_id,
                sender_id=sender_id,
                is_at_bot=is_at_bot,
                message_text=message_text,
                message_id=current_message_id,
                sender_nickname=user_nickname or "",
                timestamp=message_timestamp,
            )
            if gate_decision.action == "ignore":
                self.plugin.logger.info(
                    f"[AttentionGate] 群 {group_id} 消息被忽略 (sender={sender_id}, reason={gate_decision.reason})"
                )
                return
            force_reply = gate_decision.force_reply

        group_scene_mode = "directed_user" if is_at_bot else "shared_context"
        # 猫娘动态模式下跳过插话抑制检测（由注意力门控替代）
        suppression_reason = ""
        if strategy_mode != "neko_dynamic":
            suppression_reason = await self._detect_group_interjection_suppression(
                group_id=group_id,
                sender_id=sender_id,
                message_text=message_text,
                is_at_bot=is_at_bot,
                current_message_id=current_message_id,
                quoted_message_id=quoted_message_id,
                mentions_other_user=mentions_other_user,
                message_timestamp=message_timestamp,
            )
        request = QQReplyRequest(
            message_text=message_text,
            sender_id=sender_id,
            attachments=attachments,
            is_group=True,
            group_id=group_id,
            user_nickname=user_nickname,
            is_at_bot=is_at_bot,
            source_kind="incoming_group",
            forward_sub_count=forward_sub_count,
            group_scene_mode=group_scene_mode,
            current_message_id=current_message_id,
            quoted_message_id=quoted_message_id,
            mentioned_user_ids=list(mentioned_user_ids or []),
            mentions_other_user=mentions_other_user,
            mentions_all=mentions_all,
            reply_message_id=current_message_id if (strategy_mode != "neko_dynamic" and group_scene_mode == "directed_user") else "",
            at_user_id=sender_id if (strategy_mode != "neko_dynamic" and group_scene_mode == "directed_user") else "",
            fallback_to_text_on_voice_failure=True,
            suppression_reason=suppression_reason,
            force_reply=force_reply,
        )
        outcome = await self.plugin.reply_pipeline.run(request)
        # 回复后即时标 reviewed，统一 backlog 管道
        if outcome.action == "reply" and outcome.reply_text and current_message_id:
            if hasattr(self.plugin, "backlog_service") and self.plugin.backlog_service:
                await self.plugin.backlog_store.mark_message_reviewed(current_message_id)

        # 焦点群/近焦点群：输出 LLM 自行判断的结果
        if strategy_mode == "neko_dynamic" and not is_at_bot:
            if outcome.action == "reply" and outcome.reply_text:
                self.plugin._emit_log("INFO", f"[LLM自判] 决定回复: {outcome.reply_text[:40]}")
            else:
                self.plugin._emit_log("INFO", "[LLM自判] 决定不回复")

        # neko_dynamic + NapCat: 回复后消耗注意力
        if strategy_mode == "neko_dynamic" and outcome.action == "reply" and outcome.reply_text:
            if self.plugin.qq_client and self.plugin.qq_client.needs_attention:
                if hasattr(self.plugin, "attention_gate_service") and self.plugin.attention_gate_service:
                    await self.plugin.attention_gate_service.on_reply_sent(group_id)

        # neko_scene: 原有 attention 更新逻辑
        if strategy_mode != "neko_dynamic":
            if getattr(self.plugin, "attention_service", None) and outcome.action == "reply" and outcome.reply_text:
                await self.plugin.attention_service.update_on_reply(
                    group_id,
                    reply_message_id=str(request.reply_message_id or request.current_message_id or ""),
                    at_user_id=str(request.at_user_id or ""),
                )

        self.plugin.runtime_service.record_pipeline_outcome(source=request.source_kind, request=request, outcome=outcome)

        # neko_dynamic: 检查焦点切换，触发回溯补回
        if strategy_mode == "neko_dynamic" and hasattr(self.plugin, "attention_gate_service"):
            shift = await self.plugin.attention_gate_service.check_focus_shift()
            if shift and shift.new_focus_group:
                import asyncio
                asyncio.create_task(
                    self.plugin.attention_gate_service.run_retroactive_review(shift.new_focus_group)
                )
