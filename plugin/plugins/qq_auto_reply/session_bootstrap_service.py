from __future__ import annotations

import asyncio
import time
from typing import Any, Optional

from main_logic.omni_offline_client import OmniOfflineClient
from utils.config_manager import get_config_manager

from .pipeline_models import QQReplyContext


def generation_session_is_reusable(
    entry: Optional[dict[str, Any]],
    *,
    login_self_id: Any,
    her_name: Any,
    conversation_route: tuple[str, str] | None = None,
    is_group: bool | None = None,
    private_memory_mode: str | None = None,
    permission_level: str | None = None,
) -> bool:
    """Whether this turn keeps an existing session instead of rebuilding it.

    Shared with the context node's region-wait prediction on purpose: a turn
    that rebuilds must await region resolution *before* the persona is
    assembled, and that prediction is only correct while it enumerates the
    same triggers as the rebuild below. Keeping two copies is how the wait
    silently stopped covering the character-switch and retry paths.

    ``conversation_route`` is the CURRENT config's ``(base_url, model)``.
    It is compared against the route the entry was CREATED with (stored on
    the entry — never read off the live client, whose model may have been
    legitimately vision-switched mid-session). A stale-route session would
    keep answering on the retired provider indefinitely in a busy group,
    and after a free→tool-capable switch it would leave the turn with NO
    recall channel at all: the context skips the synchronous recall per
    the new config while the arm step refuses the old client's route.
    Entries without a stored route (pre-upgrade / lightweight callers)
    skip the comparison.

    Private sessions additionally bind one permission/memory-domain contract.
    A receipt-stamped handler may create an old-domain session after settings
    invalidation already finished, so pending-discard markers alone cannot
    prove that the cached prompt is safe for the current turn."""
    if not entry:
        return False
    if entry.get("login_self_id") != login_self_id:
        return False
    if her_name is not None and entry.get("her_name") != her_name:
        return False
    if entry.get("pending_identity_discard"):
        return False
    if entry.get("pending_permission_discard"):
        return False
    if is_group is False and (
        entry.get("private_memory_mode") != private_memory_mode
        or entry.get("permission_level") != permission_level
    ):
        return False
    stored_route = entry.get("conversation_route")
    if (
        conversation_route is not None
        and stored_route is not None
        and tuple(stored_route) != tuple(conversation_route)
    ):
        return False
    return True


class QQSessionBootstrapService:
    def __init__(self, plugin: Any):
        self.plugin = plugin

    async def ensure_generation_session(self, context: QQReplyContext, session_key: str) -> Optional[dict[str, Any]]:
        if not hasattr(self.plugin, "_user_sessions"):
            self.plugin._user_sessions = {}

        existing_session = None if context.ephemeral_session else self.plugin._user_sessions.get(session_key)
        current_route: tuple[str, str] | None = None
        if existing_session is not None:
            # 线路指纹核对要用当前配置：先落定区域再读（免费线路的 URL 会被
            # 区域改写，未落定就比对会拿 pre-rewrite 快照造出假错配）。已落定
            # 零开销；fail-open，落定失败按"线路未知"跳过核对。
            try:
                await get_config_manager().aensure_region_resolved()
            except Exception as _geo_err:
                self.plugin.logger.warning(f"[GeoIP] 线路核对前区域落定失败，跳过错配检查: {_geo_err}")
            try:
                _route_config = get_config_manager().get_model_api_config("conversation")
                current_route = (
                    str(_route_config.get("base_url") or ""),
                    str(_route_config.get("model") or ""),
                )
            except Exception:
                current_route = None
        context_is_group = getattr(context, "is_group", None)
        context_private_memory_mode = getattr(context, "private_memory_mode", None)
        context_permission_level = getattr(context, "permission_level", None)
        private_contract_changed = bool(
            existing_session
            and context_is_group is False
            and (
                existing_session.get("private_memory_mode")
                != context_private_memory_mode
                or existing_session.get("permission_level")
                != context_permission_level
            )
        )
        if existing_session and not generation_session_is_reusable(
            existing_session,
            login_self_id=context.login_self_id,
            her_name=getattr(context, "her_name", None),
            conversation_route=current_route,
            is_group=context_is_group,
            private_memory_mode=context_private_memory_mode,
            permission_level=context_permission_level,
        ):
            # her_name 失配=活跃角色切换：旧会话的 scoped 缓冲仍属旧角色，
            # discard 内的集中抢救会以旧 her_name 结算——新角色的对话绝不
            # 能入旧角色的记忆库。
            character_changed = existing_session.get("her_name") != getattr(
                context, "her_name", existing_session.get("her_name"),
            )
            permission_changed = bool(
                existing_session.get("pending_permission_discard")
            ) or private_contract_changed
            discarded = await self.plugin.session_runtime_service.discard_session(
                session_key, reason="登录身份/角色/线路/私聊权限变化",
            )
            if discarded is False:
                # 粘性标记：prime 会把 login_self_id 刷成新值，若只靠 id
                # 不匹配做重试条件，下一轮就再也进不来这里了。
                existing_session["pending_identity_discard"] = True
                if character_changed or permission_changed:
                    # 角色切换 + 抢救失败：绝不能拿旧角色的会话生成——
                    # 新轮的 human/ai 行会挂在 her_name 仍是旧角色的
                    # user_data 上，之后的重试结算会把它们写进旧角色的
                    # 记忆库。本轮放弃生成，等下轮重试抢救。
                    self.plugin.logger.warning(
                        f"角色/权限已切换但旧会话结算失败，跳过本轮生成待重试 "
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
                # 创建时刻的会话线路指纹：复用判据拿它与当前配置比对（绝不
                # 读 client 现值——图片轮会把 client 合法地切到 vision 模型，
                # 按现值比对会让每个看过图的会话每轮都被误重建）。
                "conversation_route": (str(base_url or ""), str(model or "")),
                "character_fields": context.character_card_fields,
                "last_synced_index": 0,
                "last_activity_at": time.time(),
                "memory_enabled": context.persist_memory,
                # 私聊记忆模式在创建时刻定格（"participant"=以对方为主体
                # 的 scoped 结算；"legacy"=admin 主人语料）：结算目标绝不
                # 随 per-turn 的权限变化漂移——漂移的代价是把一个人的历史
                # 写进另一个语料库。群会话恒 None。
                "private_memory_mode": (
                    None if context.is_group or not context.persist_memory
                    else getattr(context, "private_memory_mode", None) or (
                        "legacy" if context.permission_level == "admin"
                        else "participant"
                    )
                ),
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
