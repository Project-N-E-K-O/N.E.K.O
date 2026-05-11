from __future__ import annotations

import asyncio
import time
from typing import Optional


class QQAutoReplyPromptingMixin:
    async def _build_qq_session_instructions(
        self,
        her_name: str,
        master_name: str,
        character_prompt: str,
        character_card_fields: dict,
        permission_level: str,
        sender_id: str,
        user_title: str,
        is_group: bool = False,
        group_id: Optional[str] = None,
        use_memory_context: Optional[bool] = None,
        address_user_by_name: bool = True,
        group_facing: bool = False,
    ) -> tuple[str, bool]:
        from config.prompts.prompts_sys import CONTEXT_SUMMARY_READY, SESSION_INIT_PROMPT
        from utils.language_utils import get_global_language

        try:
            from utils.i18n_utils import normalize_language_code
        except Exception:
            normalize_language_code = None

        user_language = get_global_language()
        short_language = (
            normalize_language_code(user_language, format="short")
            if normalize_language_code else user_language
        )

        init_prompt_template = SESSION_INIT_PROMPT.get(
            short_language,
            SESSION_INIT_PROMPT.get(user_language, SESSION_INIT_PROMPT["en"]),
        )
        context_ready_template = CONTEXT_SUMMARY_READY.get(
            short_language,
            CONTEXT_SUMMARY_READY.get(user_language, CONTEXT_SUMMARY_READY["en"]),
        )

        system_prompt_parts = [
            init_prompt_template.format(name=her_name),
            character_prompt,
        ]
        master_title = master_name if master_name else self.i18n.t("prompts.default_master", default="主人")

        should_use_memory_context = (
            (not is_group and permission_level == "admin")
            if use_memory_context is None else bool(use_memory_context)
        )
        if should_use_memory_context:
            try:
                import httpx
                from config import MEMORY_SERVER_PORT

                async with httpx.AsyncClient(timeout=5.0, proxy=None, trust_env=False) as client:
                    response = await client.get(f"http://127.0.0.1:{MEMORY_SERVER_PORT}/new_dialog/{her_name}")
                    if response.is_success:
                        memory_context = response.text.strip()
                        if memory_context:
                            system_prompt_parts.append(
                                memory_context + context_ready_template.format(name=her_name, master=master_name)
                            )
                    else:
                        self.logger.warning(f"读取 Memory Server 上下文失败: {response.status_code}")
            except Exception as e:
                self.logger.warning(f"读取 Memory Server 上下文失败: {e}")

        if character_card_fields:
            system_prompt_parts.append("\n" + self.i18n.t("prompts.card.extra_start", default="======角色卡额外设定======"))
            for field_name, field_value in character_card_fields.items():
                system_prompt_parts.append(f"{field_name}: {field_value}")
            system_prompt_parts.append(self.i18n.t("prompts.card.extra_end", default="======角色卡设定结束======"))

        if is_group:
            if group_facing:
                system_prompt_parts.append(self.i18n.t(
                    "prompts.group.collective",
                    default="\n======身份定义======\n- 你自己：{her_name}，你是当前回复者\n- 主人/管理员：{master_name}，是固定身份，不等于群内任意成员\n- 当前发言场景：QQ群 {group_id} 的群发消息，面向整个群体\n- 当前消息对象是群内成员整体，不是某一个单独用户\n- 即使群号、QQ号、用户昵称、主人名字、你的名字或角色设定中的人物名称相同，也必须按上述身份定义区分，绝不能混淆角色\n======身份定义结束======\n\n======QQ 群聊环境======\n- 你正在 QQ 群 {group_id} 中向群内成员发言\n- 这是群聊环境，有多个用户在场\n- 这次回复应面向整个群体，而不是某个单独用户\n- 默认使用“大家”“各位”“群友们”等集体称呼\n- 不要把群号、QQ号或单个用户当成人名来称呼\n- 除非消息内容明确需要，否则不要点名某个具体用户\n- 请保持角色设定，用简短自然的话回复（不超过50字）\n- 不要使用 Markdown 格式，不要使用表情符号\n- 记住你是 {her_name}，始终以 {her_name} 的身份回复\n- 注意不要重复之前的发言\n======环境说明结束======",
                    her_name=her_name,
                    master_name=master_title,
                    group_id=group_id or "",
                ))
            else:
                naming_instruction = (
                    self.i18n.t("prompts.group.naming_with_title", default='- 在回复中自然地称呼对方为"{user_title}"', user_title=user_title)
                    if address_user_by_name else
                    self.i18n.t("prompts.group.naming_without_title", default='- 不要直接称呼对方名字、昵称或QQ号，只针对当前话题自然回应')
                )
                title_line = self.i18n.t("prompts.group.title_line", default='- 当前发言人的称呼是：{user_title}\n', user_title=user_title) if address_user_by_name else ""
                system_prompt_parts.append(self.i18n.t(
                    "prompts.group.directed",
                    default="\n======身份定义======\n- 你自己：{her_name}，你是当前回复者\n- 主人/管理员：{master_name}，是固定身份，不等于当前发言人\n- 当前发言人：{user_title}（QQ: {sender_id}），是本轮群聊中正在对话的对象\n- 当前发言人不是你自己，也不是主人/管理员，除非系统另有明确说明\n- 即使当前发言人的名字、QQ昵称、主人名字、你的名字或角色设定中的人物名称相同，也必须按上述身份定义区分，绝不能混淆角色\n======身份定义结束======\n\n======QQ 群聊环境======\n- 你正在 QQ 群 {group_id} 中与用户 {sender_id} 对话\n{title_line}- 这是群聊环境，有多个用户在场\n- 请保持角色设定，用简短自然的话回复（不超过50字）\n- 不要使用 Markdown 格式，不要使用表情符号\n- 记住你是 {her_name}，始终以 {her_name} 的身份回复\n{naming_instruction}\n- 注意不要重复之前的发言\n======环境说明结束======",
                    her_name=her_name,
                    master_name=master_title,
                    user_title=user_title,
                    sender_id=sender_id,
                    group_id=group_id or "",
                    title_line=title_line,
                    naming_instruction=naming_instruction,
                ))
        else:
            friend_note = (
                self.i18n.t("prompts.private.friend_note", default="- 当前对话对象是{master_name}的朋友，不是主人本人\n", master_name=master_title)
                if permission_level != "admin" else ""
            )
            private_identity_target = (
                self.i18n.t("prompts.private.target_user", default="- 当前对话对象：{user_title}（QQ: {sender_id}），这是当前私聊对象\n", user_title=user_title, sender_id=sender_id)
                if permission_level != "admin" else
                self.i18n.t("prompts.private.target_admin", default="- 当前对话对象：{user_title}（QQ: {sender_id}），这就是主人/管理员本人\n", user_title=user_title, sender_id=sender_id)
            )
            system_prompt_parts.append(self.i18n.t(
                "prompts.private.body",
                default="\n======身份定义======\n- 你自己：{her_name}，你是当前回复者\n- 主人/管理员：{master_name}，是固定身份\n{private_identity_target}{friend_note}- 即使当前对话对象的名字、QQ昵称、主人名字、你的名字或角色设定中的人物名称相同，也必须按上述身份定义区分，绝不能混淆角色\n======身份定义结束======\n\n======QQ 私聊环境======\n- 你正在通过 QQ 与用户 {sender_id} 私聊\n- 对方的称呼是：{user_title}\n- 请保持角色设定，用简短自然的话回复（不超过50字）\n- 不要使用 Markdown 格式，不要使用表情符号\n- 记住你是 {her_name}，始终以 {her_name} 的身份回复\n- 在回复中自然地称呼对方为\"{user_title}\"\n- 注意不要重复之前的发言\n======环境说明结束======",
                her_name=her_name,
                master_name=master_title,
                private_identity_target=private_identity_target,
                friend_note=friend_note,
                sender_id=sender_id,
                user_title=user_title,
            ))

        system_prompt = "\n".join(system_prompt_parts)
        self.logger.info(f"系统提示词长度: {len(system_prompt)} 字符")
        self.logger.info(f"使用语言: {user_language}, init_prompt_len={len(init_prompt_template or '')}")
        print(f"[QQ Auto] 初始提示: {(init_prompt_template or '')[:50]}...")
        return system_prompt, should_use_memory_context

    async def _ensure_session_for_user(self, user_data: dict[str, object]) -> Optional[dict[str, object]]:
        session_key = user_data.get("session_key")
        if not session_key:
            return None

        existing = self._user_sessions.get(session_key)
        if existing:
            if "lock" not in existing:
                existing["lock"] = asyncio.Lock()
            if not existing.get("sender_id"):
                existing["sender_id"] = user_data.get("sender_id")
            if "is_group" not in existing:
                existing["is_group"] = bool(user_data.get("is_group"))
            if "group_id" not in existing:
                existing["group_id"] = user_data.get("group_id")
            if not existing.get("user_title"):
                existing["user_title"] = user_data.get("user_title") or self.i18n.t("prompts.default_qq_user", default="QQ用户{sender_id}", sender_id=user_data.get('sender_id') or "")
            if "permission_level" not in existing:
                existing["permission_level"] = user_data.get("permission_level")
            return existing

        try:
            from main_logic.omni_offline_client import OmniOfflineClient
            from utils.config_manager import get_config_manager

            config_manager = get_config_manager()
            master_name, her_name, _, catgirl_data, _, lanlan_prompt_map, _, _, _ = config_manager.get_character_data()
            current_character = catgirl_data.get(her_name, {})
            character_prompt = lanlan_prompt_map.get(her_name, self.i18n.t("prompts.default_ai_assistant", default="你是一个友好的AI助手"))
            character_card_fields = {}
            for key, value in current_character.items():
                if key not in ["_reserved", "voice_id", "system_prompt", "model_type",
                               "live2d", "vrm", "vrm_animation", "lighting", "vrm_rotation",
                               "live2d_item_id", "item_id", "idleAnimation"]:
                    if isinstance(value, (str, int, float, bool)) and value:
                        character_card_fields[key] = value

            conversation_config = config_manager.get_model_api_config("conversation")
            base_url = conversation_config.get("base_url", "")
            api_key = conversation_config.get("api_key", "")
            model = conversation_config.get("model", "")

            reply_chunks = []

            async def on_text_delta(text: str, is_first: bool):
                reply_chunks.append(text)

            user_session = OmniOfflineClient(
                base_url=base_url,
                api_key=api_key,
                model=model,
                on_text_delta=on_text_delta,
            )

            system_prompt, memory_enabled = await self._build_qq_session_instructions(
                her_name=her_name,
                master_name=master_name,
                character_prompt=character_prompt,
                character_card_fields=character_card_fields,
                permission_level=str(user_data.get("permission_level") or "trusted"),
                sender_id=str(user_data.get("sender_id") or ""),
                user_title=str(user_data.get("user_title") or self.i18n.t("prompts.default_qq_user", default="QQ用户{sender_id}", sender_id=user_data.get('sender_id') or "")),
                is_group=bool(user_data.get("is_group")),
                group_id=user_data.get("group_id"),
            )
            await asyncio.wait_for(
                user_session.connect(instructions=system_prompt),
                timeout=self._ai_connect_timeout_seconds,
            )

            created = {
                "session": user_session,
                "reply_chunks": reply_chunks,
                "her_name": her_name,
                "character_fields": character_card_fields,
                "last_synced_index": 0,
                "last_activity_at": time.time(),
                "memory_enabled": memory_enabled,
                "has_cached_memory": False,
                "session_key": session_key,
                "sender_id": str(user_data.get("sender_id") or ""),
                "permission_level": str(user_data.get("permission_level") or "trusted"),
                "is_group": bool(user_data.get("is_group")),
                "group_id": user_data.get("group_id"),
                "user_title": str(user_data.get("user_title") or f"QQ用户{user_data.get('sender_id') or ''}"),
                "user_nickname": user_data.get("user_nickname"),
                "lock": asyncio.Lock(),
                "last_proactive_at": 0.0,
            }
            self._user_sessions[session_key] = created
            return created
        except Exception as e:
            self.logger.error(f"创建主动对话会话失败: {e}")
            return None

    async def _generate_reply(
        self,
        message: str,
        permission_level: str,
        sender_id: str,
        is_group: bool = False,
        group_id: str = None,
        user_nickname: Optional[str] = None,
        use_memory_context: Optional[bool] = None,
        persist_memory: Optional[bool] = None,
        ephemeral_session: bool = False,
        group_facing: bool = False,
    ) -> Optional[str]:
        if not is_group and permission_level not in ["admin", "trusted"]:
            return None

        try:
            from main_logic.omni_offline_client import OmniOfflineClient
            from utils.config_manager import get_config_manager

            config_manager = get_config_manager()
            master_name, her_name, _, catgirl_data, _, lanlan_prompt_map, _, _, _ = config_manager.get_character_data()

            custom_nickname = self.permission_mgr.get_nickname(sender_id)

            if is_group:
                if custom_nickname:
                    user_title = custom_nickname
                elif user_nickname:
                    user_title = user_nickname
                else:
                    user_title = self.i18n.t("prompts.default_qq_user", default="QQ用户{sender_id}", sender_id=sender_id)
            else:
                if permission_level == "admin":
                    user_title = master_name if master_name else self.i18n.t("prompts.default_master", default="主人")
                else:
                    if custom_nickname:
                        user_title = custom_nickname
                    elif user_nickname:
                        user_title = user_nickname
                    else:
                        user_title = self.i18n.t("prompts.default_qq_user", default="QQ用户{sender_id}", sender_id=sender_id)

            current_character = catgirl_data.get(her_name, {})
            character_prompt = lanlan_prompt_map.get(her_name, self.i18n.t("prompts.default_ai_assistant", default="你是一个友好的AI助手"))

            character_card_fields = {}
            for key, value in current_character.items():
                if key not in ["_reserved", "voice_id", "system_prompt", "model_type",
                               "live2d", "vrm", "vrm_animation", "lighting", "vrm_rotation",
                               "live2d_item_id", "item_id", "idleAnimation"]:
                    if isinstance(value, (str, int, float, bool)) and value:
                        character_card_fields[key] = value

            self.logger.info(f"使用角色: {her_name}, 额外字段: {list(character_card_fields.keys())}")

            conversation_config = config_manager.get_model_api_config("conversation")
            base_url = conversation_config.get("base_url", "")
            api_key = conversation_config.get("api_key", "")
            model = conversation_config.get("model", "")

            should_use_memory_context = (
                (not is_group and permission_level == "admin")
                if use_memory_context is None else bool(use_memory_context)
            )
            should_persist_memory = (
                should_use_memory_context
                if persist_memory is None else bool(persist_memory)
            )

            if not hasattr(self, "_user_sessions"):
                self._user_sessions = {}

            session_key = self._build_session_key(sender_id=sender_id, is_group=is_group, group_id=group_id)
            if ephemeral_session:
                session_key = f"{session_key}:ephemeral:{time.time_ns()}"

            if session_key not in self._user_sessions:
                self.logger.info(f"为会话 {session_key} 创建新的对话 session")

                reply_chunks = []

                async def on_text_delta(text: str, is_first: bool):
                    reply_chunks.append(text)

                user_session = OmniOfflineClient(
                    base_url=base_url,
                    api_key=api_key,
                    model=model,
                    on_text_delta=on_text_delta,
                )

                system_prompt, memory_context_used = await self._build_qq_session_instructions(
                    her_name=her_name,
                    master_name=master_name,
                    character_prompt=character_prompt,
                    character_card_fields=character_card_fields,
                    permission_level=permission_level,
                    sender_id=sender_id,
                    user_title=user_title,
                    is_group=is_group,
                    group_id=group_id,
                    use_memory_context=should_use_memory_context,
                    address_user_by_name=not (is_group and permission_level == "open"),
                    group_facing=group_facing,
                )

                await asyncio.wait_for(
                    user_session.connect(instructions=system_prompt),
                    timeout=self._ai_connect_timeout_seconds,
                )

                self._user_sessions[session_key] = {
                    "session": user_session,
                    "reply_chunks": reply_chunks,
                    "her_name": her_name,
                    "character_fields": character_card_fields,
                    "last_synced_index": 0,
                    "last_activity_at": time.time(),
                    "memory_enabled": should_persist_memory,
                    "memory_context_used": memory_context_used,
                    "has_cached_memory": False,
                    "session_key": session_key,
                    "sender_id": sender_id,
                    "permission_level": permission_level,
                    "is_group": is_group,
                    "group_id": group_id,
                    "user_title": user_title,
                    "user_nickname": user_nickname,
                    "lock": asyncio.Lock(),
                    "last_proactive_at": 0.0,
                    "ephemeral_session": ephemeral_session,
                }

            user_data = self._user_sessions[session_key]
            user_session = user_data["session"]
            reply_chunks = user_data["reply_chunks"]
            user_data["last_activity_at"] = time.time()
            user_data.setdefault("lock", asyncio.Lock())
            user_data["session_key"] = session_key
            user_data["sender_id"] = sender_id
            user_data["permission_level"] = permission_level
            user_data["is_group"] = is_group
            user_data["group_id"] = group_id
            user_data["user_title"] = user_title
            user_data["user_nickname"] = user_nickname
            user_data["memory_enabled"] = should_persist_memory
            user_data["memory_context_used"] = should_use_memory_context
            user_data["ephemeral_session"] = ephemeral_session

            async with user_data["lock"]:
                reply_chunks.clear()

                self.logger.info(f"发送消息到 AI (会话: {session_key}, length: {len(message)})")
                await asyncio.wait_for(
                    user_session.stream_text(message),
                    timeout=self._ai_turn_timeout_seconds,
                )

                completed = await self._wait_session_response_complete(user_session)
                if not completed:
                    self.logger.warning(f"会话 {session_key} 响应超时，关闭并丢弃该会话")
                    await user_session.close()
                    self._user_sessions.pop(session_key, None)
                    return None

                ai_reply = "".join(reply_chunks).strip()

            if ai_reply:
                if user_data.get("memory_enabled"):
                    try:
                        count = await self._cache_session_delta(session_key, user_data)
                        if count:
                            self.logger.info(f"[管理员] 成功同步 {count} 条消息到 Memory Server (会话: {session_key})")
                    except Exception as e:
                        self.logger.error(f"记忆同步失败: {e}")
                else:
                    if user_data.get("memory_context_used"):
                        self.logger.info(f"[临时发送] 已使用记忆上下文但跳过记忆同步 (会话: {session_key})")
                    elif is_group:
                        self.logger.info(f"[群聊] 跳过记忆同步 (群: {group_id}, 用户: {sender_id})")
                    else:
                        self.logger.info(f"[非管理员] 跳过记忆同步 (用户: {sender_id}, 权限: {permission_level})")

                self.logger.info(f"AI 生成回复完成 (会话: {session_key}, length: {len(ai_reply)})")
                return ai_reply

            self.logger.warning("AI 未生成回复")
            return f"收到你的消息: {message}"

        except asyncio.TimeoutError:
            self.logger.warning(f"会话 {session_key} 处理超时，关闭并丢弃该会话")
            user_data = self._user_sessions.pop(session_key, None)
            session = user_data.get("session") if user_data else None
            if session:
                try:
                    await session.close()
                except Exception as close_error:
                    self.logger.warning(f"关闭超时会话失败: {close_error}")
            return None
        except Exception as e:
            self.logger.exception(f"AI 生成回复失败: {e}")
            return f"收到你的消息: {message}"
        finally:
            if ephemeral_session:
                user_data = self._user_sessions.pop(session_key, None)
                session = user_data.get("session") if user_data else None
                if session:
                    try:
                        await session.close()
                    except Exception as close_error:
                        self.logger.warning(f"关闭临时会话失败: {close_error}")
