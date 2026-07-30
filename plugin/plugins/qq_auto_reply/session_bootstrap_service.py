from __future__ import annotations

import asyncio
import time
from typing import Any, Optional

from main_logic.omni_offline_client import OmniOfflineClient
from utils.config_manager import get_config_manager

from .pipeline_models import QQReplyContext


def generation_session_is_reusable(
    entry: Optional[dict[str, Any]], *, login_self_id: Any, her_name: Any,
) -> bool:
    """Whether this turn keeps an existing session instead of rebuilding it.

    Shared with the context node's region-wait prediction on purpose: a turn
    that rebuilds must await region resolution *before* the persona is
    assembled, and that prediction is only correct while it enumerates the
    same triggers as the rebuild below. Keeping two copies is how the wait
    silently stopped covering the character-switch and retry paths."""
    if not entry:
        return False
    if entry.get("login_self_id") != login_self_id:
        return False
    if her_name is not None and entry.get("her_name") != her_name:
        return False
    if entry.get("pending_identity_discard"):
        return False
    return True


class QQSessionBootstrapService:
    def __init__(self, plugin: Any):
        self.plugin = plugin

    async def ensure_generation_session(self, context: QQReplyContext, session_key: str) -> Optional[dict[str, Any]]:
        if not hasattr(self.plugin, "_user_sessions"):
            self.plugin._user_sessions = {}

        existing_session = None if context.ephemeral_session else self.plugin._user_sessions.get(session_key)
        if existing_session and not generation_session_is_reusable(
            existing_session,
            login_self_id=context.login_self_id,
            her_name=getattr(context, "her_name", None),
        ):
            # her_name 失配=活跃角色切换：旧会话的 scoped 缓冲仍属旧角色，
            # discard 内的集中抢救会以旧 her_name 结算——新角色的对话绝不
            # 能入旧角色的记忆库。
            character_changed = existing_session.get("her_name") != getattr(
                context, "her_name", existing_session.get("her_name"),
            )
            discarded = await self.plugin.session_runtime_service.discard_session(session_key, reason="登录身份变化")
            if discarded is False:
                # 粘性标记：prime 会把 login_self_id 刷成新值，若只靠 id
                # 不匹配做重试条件，下一轮就再也进不来这里了。
                existing_session["pending_identity_discard"] = True
                if character_changed:
                    # 角色切换 + 抢救失败：绝不能拿旧角色的会话生成——
                    # 新轮的 human/ai 行会挂在 her_name 仍是旧角色的
                    # user_data 上，之后的重试结算会把它们写进旧角色的
                    # 记忆库。本轮放弃生成，等下轮重试抢救。
                    self.plugin.logger.warning(
                        f"角色已切换但旧会话结算失败，跳过本轮生成待重试 "
                        f"({session_key})"
                    )
                    return None
                # 结算失败被有意保留：覆盖 key 会销毁缓冲唯一副本并泄漏
                # 旧 client。本轮沿用旧会话，身份行至多滞后一轮，下次重试。
                return existing_session
            existing_session = None
        if existing_session:
            return existing_session

        try:
            # 会话的线路会连 base_url 一起冻进 OmniOfflineClient 并缓存整场，所以先给
            # 仍在飞的区域探测一个收尾窗口（与 core/lifecycle、游戏会话池对偶）。已落定时
            # 零开销；自配 API 用户不会因此发起探测。fail-open：插件不该因区域探测出错而
            # 起不了会话。
            try:
                await get_config_manager().aensure_region_resolved()
            except Exception as _geo_err:
                self.plugin.logger.warning(f"[GeoIP] 插件会话区域落定失败，退化到当前配置继续: {_geo_err}")

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
                # 一轮只允许一次召回：与旧的每轮同步召回同预算，也压住
                # 工具轮的最坏超时（每多一次迭代就多一整段 LLM 流，而这里
                # 超时的代价是丢弃整个共享群会话）。封顶后 forced-finalize
                # 会摘掉 tools 逼出最终文本，召回结果不会白拿。
                max_tool_iterations=1,
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

            # 会话的线路会连 base_url 一起冻进 OmniOfflineClient 并缓存整场，所以先给
            # 仍在飞的区域探测一个收尾窗口（与 core/lifecycle、游戏会话池对偶）。已落定时
            # 零开销；自配 API 用户不会因此发起探测。fail-open：插件不该因区域探测出错而
            # 起不了会话。
            # 必须在下面读角色数据**之前**等：等待期间用户可能切换当前角色，等完再读
            # 才不会把切换前的人格冻进整场缓存会话（与 bilibili_dm、游戏会话池对偶）。
            try:
                await config_manager.aensure_region_resolved()
            except Exception as _geo_err:
                self.plugin.logger.warning(f"[GeoIP] 插件会话区域落定失败，退化到当前配置继续: {_geo_err}")

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
                # 与 ensure_generation_session 对偶：主动会话与回复会话共用
                # _user_sessions 缓存，回复轮可能复用这里建的 client。
                max_tool_iterations=1,
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
