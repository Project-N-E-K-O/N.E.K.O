from __future__ import annotations

import asyncio
from typing import Any, Optional

from .feedback_classifier import QQFeedbackClassifier
from .pipeline_models import QQReplyRequest

#: 开放平台通道的观测值。真相在 ``QQOpenPlatformConnection.CHANNEL``，这里抄一
#: 份而不是 import，是为了不把 websockets / httpx 拖进本模块的导入链；两者相
#: 等由测试钉死。
_OPEN_PLATFORM_CHANNEL = "open"


class QQMessageDispatcher:
    #: 一个群里连续出现多少个**互不相同**的说话人、且无一对得上名册，才认为
    #: 「这个群从来认不出任何已登记用户」值得报一条。1 个陌生人本来就该认不
    #: 出，那是正常的；一连几个都认不出、而名册里明明有管理员，才是信号。
    OPEN_PLATFORM_SCOPE_ALARM_SPEAKERS = 3
    #: 最多跟踪多少个群的告警状态，避免长期运行时无界增长。
    OPEN_PLATFORM_SCOPE_ALARM_MAX_GROUPS = 128

    def __init__(self, plugin: Any):
        self.plugin = plugin
        self._open_platform_bootstrap_lock = asyncio.Lock()

    async def _maybe_reserve_open_platform_admin(
        self, message: dict[str, Any],
    ) -> None:
        if message.get("message_type") != "private":
            return
        qq_client = getattr(self.plugin, "qq_client", None)
        permission_mgr = getattr(self.plugin, "permission_mgr", None)
        sender_id = str(message.get("user_id") or "").strip()
        if (
            qq_client is None
            or qq_client.needs_attention
            or permission_mgr is None
            or not sender_id
        ):
            return
        async with self._open_platform_bootstrap_lock:
            # Another queued message from the same first user may have been
            # receipt-stamped before this dispatcher promoted the winner.  Do
            # not broaden this to arbitrary later permission changes: only the
            # admin reserved by this process's bootstrap may inherit that
            # bootstrap receipt.
            if getattr(self, "_open_platform_bootstrap_admin_id", None) == sender_id:
                if permission_mgr.get_permission_level(sender_id) == "admin":
                    message["_private_permission_level_at_receipt"] = "admin"
                    return
                # Permission changes are authoritative.  Expire the bootstrap
                # shortcut immediately so a removed/demoted first user can
                # never read owner memory through a stale receipt override.
                self._open_platform_bootstrap_admin_id = None
            if permission_mgr.list_users():
                return
            permission_mgr.add_user(
                sender_id,
                "admin",
                message.get("user_nickname") or "管理员",
            )
            self.plugin._refresh_admin_qq()
            self._open_platform_bootstrap_admin_id = sender_id
            message["_private_permission_level_at_receipt"] = "admin"
            message["_open_platform_admin_promoted_at_receipt"] = True

    def _note_open_platform_identity_scope(
        self,
        message: dict[str, Any],
        permission_level: Any,
    ) -> None:
        """开放平台身份作用域的**纯观测**告警（R11）。

        设计出处：``docs/design/speaker-trust-entity-semantics.md`` §2.15.4。

        怀疑的是：开放平台在私聊里下发的 author.id 与在群里下发的可能不是同
        一个作用域（``user_openid`` vs ``member_openid``）。若真如此，
        ``_maybe_reserve_open_platform_admin`` 通过**第一条私聊**授权的主人，
        在**所有群**里都匹配不上名册——档位解析成 ``none``、
        ``speaker_is_owner`` 恒假、信赖度的 confirmation/correction 来源直接
        断供。

        这个怀疑**尚未取证**（取证插桩见 ``qq_open_plat.py`` 顶部的 R11 一
        节）。所以这里只报，不修：

        - 不改任何权限判定，不写名册，不碰 message 的任何既有键；
        - 若 id 本来就同作用域（R11 不成立），本方法永远走不到告警那一步，
          是彻底的 no-op。

        **不要**顺手把 ``_maybe_reserve_open_platform_admin`` 里那句全局
        ``if permission_mgr.list_users(): return`` 改成「按当前通道过滤后判
        空」。那不是疏漏，是让通道切换 fail-closed 的门：按通道过滤在刚切到
        open_platform 时恒为空，等于让切换后第一个私聊 bot 的陌生人自动拿到
        admin。
        """
        try:
            channel = str(message.get("channel") or "").strip().lower()
            if channel != _OPEN_PLATFORM_CHANNEL:
                return
            group_id = str(message.get("group_id") or "").strip()
            sender_id = str(message.get("user_id") or "").strip()
            if not group_id or not sender_id:
                return
            permission_mgr = getattr(self.plugin, "permission_mgr", None)
            if permission_mgr is None:
                return
            roster = permission_mgr.list_users() or []
            admins = [
                user for user in roster
                if str((user or {}).get("level") or "") == "admin"
            ]
            # 名册里没有管理员 ⇒ 群里认不出人是理所当然的，不是信号。
            if not admins:
                return

            states = getattr(self, "_open_platform_scope_alarm_state", None)
            if states is None:
                states = {}
                self._open_platform_scope_alarm_state = states
            state = states.get(group_id)
            if state is None:
                if len(states) >= self.OPEN_PLATFORM_SCOPE_ALARM_MAX_GROUPS:
                    return
                state = {"unmatched": set(), "matched": False, "warned": False}
                states[group_id] = state
            # 这个群里但凡有过一个人对上名册，就证明群侧 id 与名册同作用域，
            # 此后永久闭嘴。
            if state["matched"]:
                return
            if str(permission_level or "none") != "none":
                state["matched"] = True
                state["unmatched"].clear()
                return
            if state["warned"]:
                return
            state["unmatched"].add(sender_id)
            if len(state["unmatched"]) < self.OPEN_PLATFORM_SCOPE_ALARM_SPEAKERS:
                return
            state["warned"] = True
            state["unmatched"].clear()
            text = (
                f"[R11] 开放平台身份作用域告警: 群 {group_id} 已出现 "
                f"{self.OPEN_PLATFORM_SCOPE_ALARM_SPEAKERS} 个不同说话人，"
                f"无一匹配已登记用户（名册中有 {len(admins)} 个管理员）。"
                "若主人是靠「第一条私聊自动授权」拿到的管理员，那个 id 可能"
                "只在私聊作用域有效，需要在本群单独把群内 id 加进信任用户。"
                "本条仅为诊断，不改变任何权限判定。"
            )
            logger = getattr(self.plugin, "logger", None)
            if logger is not None:
                logger.warning(text)
            emit_log = getattr(self.plugin, "_emit_log", None)
            if callable(emit_log):
                emit_log("WARN", text)
        except Exception:
            # 观测绝不允许把消息管线带下去。
            pass

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
                    if isinstance(message, dict):
                        # 接收时刻的群记忆政策快照：handler 在全局并发闸/
                        # 会话锁上可能排队数秒，处理侧任何晚读都会把 OFF
                        # 时代收到的消息标成已授权。真正的接收边界在这里
                        # （task 创建之前），随消息本体传递。
                        settings_now = getattr(self.plugin, "_qq_settings", {}) or {}
                        message["_group_memory_at_receipt"] = bool(
                            settings_now.get("group_memory_enabled", False)
                        )
                        # 成员记忆是群记忆的子开关：两个都开才算收到时有
                        # 授权（后端已钳制，这里是收口处的对偶判据）。
                        message["_member_memory_at_receipt"] = bool(
                            settings_now.get("group_member_memory_enabled", False)
                        ) and bool(settings_now.get("group_memory_enabled", False))
                        # 私聊 participant 记忆政策的接收边界章（对偶上面
                        # 两枚；群消息不消费它）。
                        message["_participant_memory_at_receipt"] = bool(
                            settings_now.get(
                                "private_participant_memory_enabled", False,
                            )
                        )
                        # 通道观测的接收边界快照（对偶上面几枚）：会话缓冲
                        # 可能跨越一次模式切换，flush 时读实时配置会把旧通道
                        # 的消息记成新通道。纯诊断字段，不参与任何判定。
                        message["_speaker_channel_at_receipt"] = str(
                            message.get("channel") or ""
                        ).strip().lower() or None
                        if message.get("message_type") == "group":
                            sender_at_receipt = str(
                                message.get("user_id") or ""
                            ).strip()
                            permission_at_receipt = None
                            permission_mgr = getattr(
                                self.plugin, "permission_mgr", None,
                            )
                            if permission_mgr is not None:
                                permission_at_receipt = (
                                    permission_mgr.get_permission_level(
                                        sender_at_receipt
                                    )
                                )
                            message[
                                "_group_speaker_permission_level_at_receipt"
                            ] = permission_at_receipt
                            # 纯观测，挂在刚算出的那个档位后面：告警读的必须
                            # 是管线真正用的那个值，不能自己再查一次。
                            self._note_open_platform_identity_scope(
                                message, permission_at_receipt,
                            )
                        if message.get("message_type") == "private":
                            sender_at_receipt = str(
                                message.get("user_id") or ""
                            ).strip()
                            permission_at_receipt = None
                            permission_mgr = getattr(
                                self.plugin, "permission_mgr", None,
                            )
                            if permission_mgr is not None:
                                permission_at_receipt = (
                                    permission_mgr.get_permission_level(
                                        sender_at_receipt
                                    )
                                )
                            message["_private_permission_level_at_receipt"] = (
                                permission_at_receipt
                            )
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
                # 合成控制指令，不是入群成员的发言：标记 source 让成员
                # bucket 排除、prompt 行进 digest 排除名单。
                message["_synthetic_source"] = "group_join_notice"
            # 不 return，走正常 pipeline
        # 黑名单优先：命中负优先级标签 → 不记录、不处理
        label_defs = list((self.plugin._qq_settings or {}).get("backlog_labels") or [])
        raw_content = str(message.get("content") or "").strip()
        if raw_content and QQFeedbackClassifier.is_blacklisted(raw_content, label_defs):
            self.plugin._emit_log("INFO", f"黑名单过滤: text={raw_content[:40]}")
            return
        # Open-platform bootstrap is serialized here, after all private-message
        # eligibility filters but before backlog/context work.  A filtered
        # first sender must never acquire owner-memory privileges.
        await self._maybe_reserve_open_platform_admin(message)

        # 后台拉取引用/转发/语音内容（在独立 handler task 中 await，避免 WS handler 死锁）
        if self.plugin.qq_client and self.plugin.qq_client.needs_attention:
            reply_ids = message.get("_pending_reply_ids")
            if isinstance(reply_ids, list) and reply_ids:
                await self.plugin.qq_client._fetch_reply_content(message, reply_ids)
                message.pop("_pending_reply_ids", None)
            forward_ids = message.get("_pending_forward_ids")
            if isinstance(forward_ids, list) and forward_ids:
                await self.plugin.qq_client._fetch_forward_content(message, forward_ids)
                message.pop("_pending_forward_ids", None)
            record_files = message.get("_pending_record_files")
            if isinstance(record_files, list) and record_files:
                try:
                    await asyncio.wait_for(
                        self.plugin.qq_client._fetch_record_content(message, record_files),
                        timeout=60.0,
                    )
                except (asyncio.TimeoutError, Exception) as e:
                    self.plugin._emit_log("DEBUG", f"[Voice] 语音转录超时或失败: {type(e).__name__}")
                message.pop("_pending_record_files", None)
            # 语音/引用/转发可能丰富了消息内容 → 复检黑名单
            enriched_content = str(message.get("content") or "").strip()
            if enriched_content != raw_content and QQFeedbackClassifier.is_blacklisted(enriched_content, label_defs):
                self.plugin._emit_log("INFO", f"黑名单过滤(转录后): text={enriched_content[:40]}")
                return
            # 主消息图片：调用 VLM 获取描述
            if self.plugin.qq_client._image_describer:
                import asyncio as _asyncio
                from .qq_client import QQClient
                # 从 raw message 数组或 raw_message 字符串中提取图片 URL
                raw_msg = message.get("raw") or {}
                segments = raw_msg.get("message") or message.get("message") or []
                if isinstance(segments, list):
                    img_urls = []
                    for seg in segments:
                        if isinstance(seg, dict) and seg.get("type") == "image":
                            sd = seg.get("data") or {}
                            u = str(sd.get("url") or sd.get("file") or "").strip()
                            if u:
                                img_urls.append(u)
                    if img_urls:
                        self.plugin._emit_log("DEBUG", f"[VLM] 检测到 {len(img_urls)} 张主消息图片, 开始描述...")
                    for img_url in img_urls:
                        try:
                            desc = await _asyncio.wait_for(
                                self.plugin.qq_client._image_describer(img_url),
                                timeout=8.0,
                            )
                            if desc:
                                # 替换 content 中第一个未处理的图片 CQ 码
                                import re as _re
                                raw = str(message.get("raw_message") or message.get("content") or "")
                                new_raw = _re.sub(r'\[CQ:image,[^\]]*\]', f"[Image {desc}]", raw, count=1)
                                message["raw_message"] = new_raw
                                message["content"] = new_raw
                                self.plugin._emit_log("INFO", f"[VLM] 图片描述: {desc[:40]}")
                            else:
                                self.plugin._emit_log("DEBUG", "[VLM] 图片描述返回为空")
                        except Exception as e:
                            self.plugin._emit_log("DEBUG", f"[VLM] 图片描述失败: {type(e).__name__}")
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
        # 引用上下文：仅注入 LLM prompt，不混入 message_text 以防污染会话历史
        reply_context = str(message.get("_reply_context", "") or "").strip()
        if message_type == "private":
            session_key = self.plugin._build_session_key(sender_id=sender_id, is_group=False)
            if session_key in self.plugin._user_sessions:
                self.plugin._user_sessions[session_key]["last_activity_at"] = __import__("time").time()
            fwd_count = int(message.get("_forward_sub_count", 0) or 0) if isinstance(message, dict) else 0
            current_message_id = str(message.get("message_id") or message.get("msg_id") or "").strip()
            await self.handle_private_message(
                sender_id, message_text, attachments=attachments,
                user_nickname=user_nickname, forward_sub_count=fwd_count,
                current_message_id=current_message_id,
                reply_context=reply_context,
                participant_memory_at_receipt=(
                    message.get("_participant_memory_at_receipt")
                    if isinstance(message, dict) else None
                ),
                private_permission_level_at_receipt=(
                    message.get("_private_permission_level_at_receipt")
                    if isinstance(message, dict) else None
                ),
                open_platform_admin_promoted_at_receipt=bool(
                    message.get("_open_platform_admin_promoted_at_receipt")
                    if isinstance(message, dict) else False
                ),
            )
        elif message_type == "group":
            group_id = str(message.get("group_id") or "").strip()
            is_at_bot = message.get("is_at_bot", False)
            is_reply_to_bot = message.get("is_reply_to_bot", False)
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
            # ── 禁言检查：bot 在该群被禁言 → 只记录不入 pipeline ──
            if self.plugin.qq_client and self.plugin.qq_client.is_group_muted(group_id):
                self.plugin._emit_log("INFO", f"[Mute] 群{group_id} 禁言中，跳过消息处理")
                return
            await self.handle_group_message(
                group_id,
                sender_id,
                message_text,
                is_at_bot,
                group_memory_at_receipt=(
                    message.get("_group_memory_at_receipt")
                    if isinstance(message, dict) else None
                ),
                member_memory_at_receipt=(
                    message.get("_member_memory_at_receipt")
                    if isinstance(message, dict) else None
                ),
                group_speaker_permission_level_at_receipt=(
                    message.get(
                        "_group_speaker_permission_level_at_receipt"
                    )
                    if isinstance(message, dict) else None
                ),
                speaker_channel_at_receipt=(
                    message.get("_speaker_channel_at_receipt")
                    if isinstance(message, dict) else None
                ),
                synthetic_source=(
                    str(message.get("_synthetic_source") or "")
                    if isinstance(message, dict) else ""
                ),
                attachments=attachments,
                user_nickname=user_nickname,
                current_message_id=current_message_id,
                quoted_message_id=quoted_message_id,
                mentioned_user_ids=mentioned_user_ids,
                mentions_other_user=mentions_other_user,
                mentions_all=mentions_all,
                message_timestamp=message_timestamp,
                forward_sub_count=fwd_count,
                reply_context=reply_context,
                is_reply_to_bot=is_reply_to_bot,
            )
            await self.plugin._maybe_notify_backlog_summary(group_id=group_id)

    async def handle_private_message(
        self, sender_id: str, message_text: str,
        attachments: Optional[list[dict[str, Any]]] = None,
        user_nickname: Optional[str] = None, forward_sub_count: int = 0,
        current_message_id: str = "",
        reply_context: str = "",
        participant_memory_at_receipt: bool | None = None,
        private_permission_level_at_receipt: str | None = None,
        open_platform_admin_promoted_at_receipt: bool = False,
    ):
        # 开放平台：第一个私聊用户自动成为管理员，之后可在前端配置
        if open_platform_admin_promoted_at_receipt:
            self.plugin._emit_log(
                "INFO", f"开放平台自动设置管理员: {sender_id}",
            )
            try:
                await self.plugin.settings_service.persist_business_config()
            except Exception:
                # The in-memory reservation already protects this process;
                # startup persistence remains best-effort, matching the
                # pre-existing fallback path below.
                pass
        elif self.plugin.qq_client and not self.plugin.qq_client.needs_attention:
            if self.plugin.permission_mgr and not self.plugin.permission_mgr.list_users():
                self.plugin.permission_mgr.add_user(sender_id, "admin", user_nickname or "管理员")
                self.plugin._refresh_admin_qq()
                self.plugin._emit_log("INFO", f"开放平台自动设置管理员: {sender_id}")
                try: await self.plugin.settings_service.persist_business_config()
                except Exception: pass
        # LLM 生成前预缓冲：如果已有等待中的回复，跳过 pipeline
        if getattr(self.plugin, "reply_buffer_service", None):
            session_key = self.plugin._build_session_key(sender_id=sender_id, is_group=False)
            if self.plugin.reply_buffer_service.pre_buffer(
                session_key,
                message_text,
                sender_id,
                False,
                "",
                participant_memory_at_receipt=participant_memory_at_receipt,
                private_permission_level_at_receipt=(
                    private_permission_level_at_receipt
                ),
            ):
                return
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
            reply_context=reply_context,
            # 接收边界的 participant 记忆政策章（None=旁路调用者，build
            # 内回退实时读）：排队期间 OFF→ON 不得让收到时无授权的私聊
            # 被收集。
            participant_memory_at_receipt=participant_memory_at_receipt,
            private_permission_level_at_receipt=(
                private_permission_level_at_receipt
            ),
        )
        outcome = await self.plugin.reply_pipeline.run(request)
        if outcome.action == "reply" and outcome.reply_text and current_message_id:
            await self.plugin.backlog_store.mark_message_reviewed(current_message_id)
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
        reply_context: str = "",
        is_reply_to_bot: bool = False,
        group_memory_at_receipt: bool | None = None,
        member_memory_at_receipt: bool | None = None,
        group_speaker_permission_level_at_receipt: str | None = None,
        speaker_channel_at_receipt: str | None = None,
        synthetic_source: str = "",
    ):
        # 群记忆政策快照优先取消息接收边界（process_messages 在 task 创建
        # 前打在消息上——handler 可能在全局并发闸/会话锁上排队数秒）；旁路
        # 调用者无消息级快照时至少在本函数第一个 await 前定格。OFF 时代
        # 收到的发言不得因处理期间切 ON 获得入库授权——对偶 backlog 行的
        # group_memory_enabled_at_receipt。反向（处理期间切 OFF）由 prime
        # 门控与读点复检兜住。
        if group_memory_at_receipt is None:
            group_memory_at_receipt = bool(
                (getattr(self.plugin, "_qq_settings", {}) or {}).get(
                    "group_memory_enabled", False,
                )
            )
        group_memory_at_receipt = bool(group_memory_at_receipt)
        if group_speaker_permission_level_at_receipt is None:
            permission_mgr = getattr(self.plugin, "permission_mgr", None)
            if permission_mgr is not None:
                group_speaker_permission_level_at_receipt = (
                    permission_mgr.get_permission_level(str(sender_id))
                )
        strategy_mode = getattr(self.plugin, "_strategy_mode", "neko_dynamic")
        force_reply = False
        # 新人入群：绕过门控，必定让猫娘欢迎
        if synthetic_source == "group_join_notice":
            force_reply = True
        elif strategy_mode == "neko_dynamic" and hasattr(self.plugin, "attention_gate_service") and self.plugin.attention_gate_service is not None:
            gate_decision = await self.plugin.attention_gate_service.evaluate(
                group_id=group_id,
                sender_id=sender_id,
                is_at_bot=is_at_bot,
                message_text=message_text,
                message_id=current_message_id,
                quoted_message_id=quoted_message_id,
                sender_nickname=user_nickname or "",
                timestamp=message_timestamp,
            )
            if gate_decision.action == "ignore":
                self.plugin.logger.info(
                    f"[AttentionGate] 群 {group_id} 消息被忽略 (sender={sender_id}, reason={gate_decision.reason})"
                )
                return
            force_reply = gate_decision.force_reply
            # 焦点群消息猫娘已看过，从 backlog 清除
            if gate_decision.reason == "focus_group" and current_message_id:
                if hasattr(self.plugin, "backlog_store") and self.plugin.backlog_store:
                    await self.plugin.backlog_store.mark_message_reviewed(current_message_id)

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
        group_memory_enabled = group_memory_at_receipt
        request = QQReplyRequest(
            message_text=message_text,
            sender_id=sender_id,
            attachments=attachments,
            is_group=True,
            group_id=group_id,
            user_nickname=user_nickname,
            is_at_bot=is_at_bot,
            is_reply_to_bot=is_reply_to_bot,
            source_kind=synthetic_source or "incoming_group",
            forward_sub_count=forward_sub_count,
            group_scene_mode=group_scene_mode,
            current_message_id=current_message_id,
            quoted_message_id=quoted_message_id,
            mentioned_user_ids=list(mentioned_user_ids or []),
            mentions_other_user=mentions_other_user,
            mentions_all=mentions_all,
            reply_context=reply_context,
            reply_message_id=current_message_id if (strategy_mode != "neko_dynamic" and group_scene_mode == "directed_user") else "",
            at_user_id=sender_id if (strategy_mode != "neko_dynamic" and group_scene_mode == "directed_user") else "",
            fallback_to_text_on_voice_failure=True,
            suppression_reason=suppression_reason,
            force_reply=force_reply,
            use_memory_context=group_memory_enabled,
            persist_memory=group_memory_enabled,
            member_memory_at_receipt=member_memory_at_receipt,
            group_speaker_permission_level_at_receipt=(
                group_speaker_permission_level_at_receipt
            ),
            speaker_channel_at_receipt=speaker_channel_at_receipt,
        )
        if synthetic_source:
            # 合成控制轮（入群欢迎等）：prompt 行不是任何参与者的发言，
            # pipeline 跑完后记入排除名单（对偶 proactive/rapid-fire；
            # 本 handler 已持会话锁，before 在锁内取）。
            svc = self.plugin.session_memory_service
            hist_before = svc.session_history_len(f"group:{group_id}")
            try:
                outcome = await self.plugin.reply_pipeline.run(request)
            finally:
                svc.record_synthetic_prompt_rows(f"group:{group_id}", hist_before)
        else:
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
                gate = self.plugin.attention_gate_service
                retro_tasks = getattr(gate, "_retro_tasks", None)
                if retro_tasks is None:
                    retro_tasks = set()
                    gate._retro_tasks = retro_tasks
                retro_task = asyncio.create_task(
                    gate.run_retroactive_review(shift.new_focus_group)
                )
                # 强引用+关机 join：回溯任务在会话锁内改历史/排除名单，
                # stop 清锁表前必须等它收尾。完成回调消费异常——否则失败
                # 静默丢弃，只留延迟的未取回异常告警。
                retro_tasks.add(retro_task)

                def _on_retro_done(task: "asyncio.Task") -> None:
                    retro_tasks.discard(task)
                    if task.cancelled():
                        return
                    exc = task.exception()
                    if exc is not None:
                        self.plugin.logger.warning(
                            f"[RetroReview] 回溯补回任务失败: {exc}"
                        )

                retro_task.add_done_callback(_on_retro_done)
