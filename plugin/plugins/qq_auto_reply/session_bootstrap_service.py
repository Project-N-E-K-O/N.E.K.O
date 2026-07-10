from __future__ import annotations

import asyncio
import time
from typing import Any, Optional

from main_logic.omni_offline_client import OmniOfflineClient
from utils.config_manager import get_config_manager

from .pipeline_models import QQReplyContext


class QQSessionBootstrapService:
    def __init__(self, plugin: Any):
        self.plugin = plugin

    async def ensure_generation_session(self, context: QQReplyContext, session_key: str) -> Optional[dict[str, Any]]:
        if not hasattr(self.plugin, "_user_sessions"):
            self.plugin._user_sessions = {}

        existing_session = None if context.ephemeral_session else self.plugin._user_sessions.get(session_key)
        if existing_session and existing_session.get("login_self_id") != context.login_self_id:
            await self.plugin.session_runtime_service.discard_session(session_key, reason="登录身份变化")
            existing_session = None
        if existing_session:
            return existing_session

        try:
            conversation_config = get_config_manager().get_model_api_config("conversation")
            base_url = conversation_config.get("base_url", "")
            api_key = conversation_config.get("api_key", "")
            model = conversation_config.get("model", "")

            reply_chunks: list[str] = []

            async def on_text_delta(text: str, is_first: bool):
                reply_chunks.append(text)

            user_session = OmniOfflineClient(
                base_url=base_url,
                api_key=api_key,
                model=model,
                on_text_delta=on_text_delta,
            )
            await asyncio.wait_for(
                user_session.connect(instructions=context.system_prompt),
                timeout=self.plugin._ai_connect_timeout_seconds,
            )

            created = {
                "session": user_session,
                "reply_chunks": reply_chunks,
                "her_name": context.her_name,
                "character_fields": context.character_card_fields,
                "last_synced_index": 0,
                "last_activity_at": time.time(),
                "memory_enabled": context.persist_memory,
                "memory_context_used": context.memory_context_used,
                "has_cached_memory": False,
                "session_key": session_key,
                "sender_id": context.sender_id,
                "permission_level": context.permission_level,
                "is_group": context.is_group,
                "group_id": context.group_id,
                "user_title": context.user_title,
                "user_nickname": context.user_nickname,
                "login_status": context.login_status,
                "login_self_id": context.login_self_id,
                "login_nickname": context.login_nickname,
                "lock": asyncio.Lock(),
                "last_proactive_at": 0.0,
                "ephemeral_session": context.ephemeral_session,
            }
            self.plugin._user_sessions[session_key] = created
            return created
        except Exception as e:
            self.plugin.logger.error(f"创建回复会话失败: {e}")
            return None

    async def ensure_session_for_user(self, user_data: dict[str, object]) -> Optional[dict[str, object]]:
        session_key = user_data.get("session_key")
        if not session_key:
            return None

        existing = self.plugin._user_sessions.get(session_key)
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
                existing["user_title"] = user_data.get("user_title") or self.plugin.i18n.t(
                    "prompts.default_qq_user",
                    default="QQ用户{sender_id}",
                    sender_id=user_data.get("sender_id") or "",
                )
            if "permission_level" not in existing:
                existing["permission_level"] = user_data.get("permission_level")
            current_login_status, current_login_self_id, current_login_nickname = self.plugin._normalize_login_identity(
                await self.plugin._fetch_login_status_payload()
            )
            if existing.get("login_self_id") != current_login_self_id:
                session = existing.get("session")
                self.plugin._user_sessions.pop(session_key, None)
                if session:
                    try:
                        await session.close()
                    except Exception as close_error:
                        self.plugin.logger.warning(f"关闭登录身份已变化的主动会话失败: {close_error}")
                existing = None
            else:
                existing["login_status"] = current_login_status
                existing["login_self_id"] = current_login_self_id
                existing["login_nickname"] = current_login_nickname
                return existing

        try:
            config_manager = get_config_manager()
            master_name, her_name, _, catgirl_data, _, lanlan_prompt_map, _, _, _ = config_manager.get_character_data()
            current_character = catgirl_data.get(her_name, {})
            character_prompt = lanlan_prompt_map.get(
                her_name,
                self.plugin.i18n.t("prompts.default_ai_assistant", default="你是一个友好的AI助手"),
            )
            character_card_fields = self.plugin._build_character_card_fields(current_character)

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

            login_status, login_self_id, login_nickname = self.plugin._normalize_login_identity(
                await self.plugin._fetch_login_status_payload()
            )
            instruction_bundle = await self.plugin._build_qq_session_instructions(
                her_name=her_name,
                master_name=master_name,
                character_prompt=character_prompt,
                character_card_fields=character_card_fields,
                permission_level=str(user_data.get("permission_level") or "trusted"),
                sender_id=str(user_data.get("sender_id") or ""),
                user_title=str(
                    user_data.get("user_title")
                    or self.plugin.i18n.t(
                        "prompts.default_qq_user",
                        default="QQ用户{sender_id}",
                        sender_id=user_data.get("sender_id") or "",
                    )
                ),
                is_group=bool(user_data.get("is_group")),
                group_id=user_data.get("group_id"),
                shared_group_session=bool(user_data.get("is_group")),
                login_status=login_status,
                login_self_id=login_self_id,
                login_nickname=login_nickname,
            )
            system_prompt = instruction_bundle.system_prompt
            memory_enabled = instruction_bundle.memory_context_used
            await asyncio.wait_for(
                user_session.connect(instructions=system_prompt),
                timeout=self.plugin._ai_connect_timeout_seconds,
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
                "user_title": str(
                    user_data.get("user_title")
                    or self.plugin.i18n.t(
                        "prompts.default_qq_user",
                        default="QQ用户{sender_id}",
                        sender_id=user_data.get("sender_id") or "",
                    )
                ),
                "user_nickname": user_data.get("user_nickname"),
                "login_status": login_status,
                "login_self_id": login_self_id,
                "login_nickname": login_nickname,
                "lock": asyncio.Lock(),
                "last_proactive_at": 0.0,
            }
            self.plugin._user_sessions[session_key] = created
            return created
        except Exception as e:
            self.plugin.logger.error(f"创建主动对话会话失败: {e}")
            return None
