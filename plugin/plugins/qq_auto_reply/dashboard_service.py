from __future__ import annotations

import asyncio
from typing import Any, Optional

from plugin.sdk.plugin import Err, Ok, SdkError


class QQDashboardService:
    def __init__(self, plugin: Any):
        self.plugin = plugin

    def _build_open_ui_payload(self, *, available: bool) -> dict[str, Any]:
        path = f"/plugin/{self.plugin.plugin_id}/ui/" if available else ""
        message_key = "ui.open_path.message" if available else "ui.unavailable.message"
        default_message = "UI 已注册" if available else "UI 未注册"
        message = self.plugin.i18n.t(message_key, default=default_message)
        return {
            "available": available,
            "path": path,
            "message": message,
        }

    def _inject_business_permissions(self, payload: dict[str, Any]) -> dict[str, Any]:
        payload["business_config"]["trusted_users"] = list(payload.get("permissions", {}).get("trusted_users", []))
        payload["business_config"]["trusted_groups"] = list(payload.get("permissions", {}).get("trusted_groups", []))
        return payload

    async def build_dashboard_state(self) -> dict[str, Any]:
        login = await self.plugin.runtime_service.fetch_login_status_payload()
        settings = dict(self.plugin._qq_settings or {})
        napcat_dir = self.plugin.napcat_service.get_napcat_directory()
        runtime = self.plugin.runtime_service.build_runtime_status()
        return {
            "runtime": runtime,
            "recent_pipeline_traces": runtime.get("recent_pipeline_traces", []),
            "recent_pipeline_trace_summaries": [
                item.get("summary", {})
                for item in runtime.get("recent_pipeline_traces", [])
            ],
            "recent_pipeline_trace_overview": {
                "total": len(runtime.get("recent_pipeline_traces", [])),
                "delivered": len([item for item in runtime.get("recent_pipeline_traces", []) if item.get("summary", {}).get("result_kind") == "delivered"]),
                "relayed": len([item for item in runtime.get("recent_pipeline_traces", []) if item.get("summary", {}).get("result_kind") == "relayed"]),
                "ignored": len([item for item in runtime.get("recent_pipeline_traces", []) if item.get("summary", {}).get("result_kind") == "ignored"]),
                "manual_reply": len([item for item in runtime.get("recent_pipeline_traces", []) if item.get("summary", {}).get("delivery_mode") == "manual_reply"]),
            },
            "settings": {
                "qq_connection_mode": str(settings.get("qq_connection_mode", "napcat") or "napcat").strip(),
                "onebot_url": settings.get("onebot_url", ""),
                "token": str(settings.get("token") or ""),
                "qq_open_app_id": str(settings.get("qq_open_app_id") or ""),
                "qq_open_client_secret": str(settings.get("qq_open_client_secret") or ""),
                "qq_open_identity_probe_enabled": bool(
                    settings.get("qq_open_identity_probe_enabled", False)
                ),
                "token_configured": bool(settings.get("token")),
                "token_masked": self.plugin._mask_token(str(settings.get("token") or "")),
                "napcat_directory": str(napcat_dir),
                "napcat_directory_exists": napcat_dir.exists(),
                "show_napcat_window": bool(settings.get("show_napcat_window", True)),
                "reply_mode": self.plugin.config_store.normalize_reply_mode(settings.get("reply_mode")),
                "show_onboarding": bool(settings.get("show_onboarding", True)),
                "guide_step_napcat_done": bool(settings.get("guide_step_napcat_done", False)),
                "guide_step_config_done": bool(settings.get("guide_step_config_done", False)),
                "guide_step_runtime_done": bool(settings.get("guide_step_runtime_done", False)),
                "normal_relay_probability": float(self.plugin._normal_relay_probability),
                "truth_reply_probability": float(self.plugin._truth_reply_probability),
                "backlog_labels": list(settings.get("backlog_labels") or []),
                "strategy_mode": self.plugin.config_store._normalize_strategy_mode(settings.get("strategy_mode")),
                "enable_group_attention": bool(settings.get("enable_group_attention", True)),
                "retroactive_review_max_messages": int(settings.get("retroactive_review_max_messages", 30) or 30),
                "retroactive_review_max_reply": int(settings.get("retroactive_review_max_reply", 5) or 5),
                "sticker_cooldown_messages": int(settings.get("sticker_cooldown_messages", 5) or 5),
                "group_memory_enabled": bool(settings.get("group_memory_enabled", False)),
                "group_member_memory_enabled": bool(settings.get("group_member_memory_enabled", False)),
                "private_participant_memory_enabled": bool(settings.get("private_participant_memory_enabled", False)),
                "allow_cross_group_context": bool(settings.get("allow_cross_group_context", False)),
                "group_attention_focus_rise_seconds": int(settings.get("group_attention_focus_rise_seconds", 30) or 30),
                "group_attention_focus_cooldown_seconds": int(settings.get("group_attention_focus_cooldown_seconds", 60) or 60),
                "group_attention_decay_per_second": float(settings.get("group_attention_decay_per_second", 0.02) or 0.02),
                "group_attention_message_recovery": float(settings.get("group_attention_message_recovery", 0.6) or 0.6),
                "group_attention_reply_penalty": float(settings.get("group_attention_reply_penalty", 1.3) or 1.3),
                "group_attention_keyword_boost_scale": float(settings.get("group_attention_keyword_boost_scale", 2.5) or 2.5),
                "group_attention_focus_lock_seconds": int(settings.get("group_attention_focus_lock_seconds", 120) or 120),
                "group_attention_max_score": float(settings.get("group_attention_max_score", 10.0) or 10.0),
                "group_attention_focus_threshold": float(settings.get("group_attention_focus_threshold", 4.0) or 4.0),
                "group_attention_min_threshold": float(settings.get("group_attention_min_threshold", 1.0) or 1.0),
                "group_attention_message_gain": float(settings.get("group_attention_message_gain", 0.25) or 0.25),
                "icebreaker_cold_threshold": int(settings.get("icebreaker_cold_threshold", 3) or 3),
            },
            "guide": {
                "step_napcat_done": bool(settings.get("guide_step_napcat_done", False)) or bool(runtime["napcat_managed"] and runtime["napcat_running"]),
                "step_service_done": bool(settings.get("onebot_url")) and bool(settings.get("token")),
                "step_contacts_done": bool(self.plugin.permission_mgr and self.plugin.permission_mgr.list_users()),
                "step_auto_reply_done": bool(settings.get("guide_step_runtime_done", False)) and self.plugin._running,
            },
            "business_config": dict(settings),
            "login": login,
            "permissions": {
                "trusted_users": self.plugin.permission_mgr.list_users() if self.plugin.permission_mgr else [],
                "trusted_groups": self.plugin.group_permission_mgr.list_groups() if self.plugin.group_permission_mgr else [],
                "guide_step_contacts_done": bool(self.plugin.permission_mgr and self.plugin.permission_mgr.list_users()),
            },
            "actual": {
                "friends": [],
                "groups": [],
                "refreshed_at": 0,
                "stale": True,
            },
            "backlog_items": list(self.plugin._relay_backlog_items),
            "config_ready": await self.plugin.config_store.exists(),
            # 降级必须可见，不得假装成功（设计文档 §2.15.4.3）：开放平台上
            # 一个人在每个群是一个不同的 ID，信赖度不跨群累计、主人档位只在
            # 配置过的那个群生效。UI 照这个字段决定要不要说出来。
            "identity_scope": self._identity_scope_payload(),
            "ui": self._build_open_ui_payload(available=True),
        }

    async def build_dashboard_context(self) -> dict[str, Any]:
        state = await self.build_dashboard_state()
        return {
            **state,
            "actions": [
                {"id": "init_config", "entry_id": "init_config"},
                {"id": "save_settings", "entry_id": "save_settings"},
                {"id": "refresh_actual_contacts", "entry_id": "refresh_actual_contacts"},
                {"id": "add_trusted_user", "entry_id": "add_trusted_user"},
                {"id": "list_identity_claims", "entry_id": "list_identity_claims"},
                {"id": "bind_identity_account", "entry_id": "bind_identity_account"},
                {"id": "unbind_identity_account", "entry_id": "unbind_identity_account"},
                {"id": "remove_trusted_user", "entry_id": "remove_trusted_user"},
                {"id": "set_user_nickname", "entry_id": "set_user_nickname"},
                {"id": "add_trusted_group", "entry_id": "add_trusted_group"},
                {"id": "remove_trusted_group", "entry_id": "remove_trusted_group"},
                {"id": "start_auto_reply", "entry_id": "start_auto_reply"},
                {"id": "stop_auto_reply", "entry_id": "stop_auto_reply"},
            ],
        }

    async def open_ui(self):
        return Ok(self._build_open_ui_payload(available=True))

    async def init_config(self, *, guide_step_config_done: Optional[bool] = None):
        async with self.plugin.settings_service.permission_manager_rebuild_guard():
            if await self.plugin.config_store.exists():
                config = await self.plugin.settings_service.load_business_config()
            else:
                config = await self.plugin.settings_service.create_business_config()
            if guide_step_config_done is not None:
                config["guide_step_config_done"] = bool(guide_step_config_done)
                self.plugin._qq_settings = await self.plugin.config_store.save(config)
                config = dict(self.plugin._qq_settings)
            self.plugin.settings_service.rebuild_permission_managers(config)
            self.plugin.settings_service.apply_runtime_settings(config)
        return Ok(await self.build_dashboard_state())

    async def get_dashboard_state(self):
        return Ok(await self.build_dashboard_state())

    async def refresh_actual_contacts(self):
        try:
            contacts = await self.plugin.runtime_service.refresh_actual_contacts_cache()
            payload = await self.build_dashboard_state()
            payload["actual"] = {
                **payload.get("actual", {}),
                **contacts,
                "stale": False,
            }
            return Ok(self._inject_business_permissions(payload))
        except RuntimeError as e:
            return Err(SdkError(f"REFRESH_NOT_READY: {self.plugin.i18n.t('errors.refresh_not_ready', default='{error}', error=str(e))}"))
        except Exception as e:
            self.plugin.logger.error(f"刷新实际联系人列表失败: {e}")
            return Err(SdkError(f"REFRESH_FAILED: {self.plugin.i18n.t('errors.refresh_failed', default='{error}', error=str(e))}"))

    async def save_settings(
        self,
        *,
        onebot_url: Optional[str] = None,
        token: Optional[str] = None,
        napcat_directory: Optional[str] = None,
        show_napcat_window: Optional[bool] = None,
        reply_mode: Optional[str] = None,
        show_onboarding: Optional[bool] = None,
        guide_step_napcat_done: Optional[bool] = None,
        guide_step_config_done: Optional[bool] = None,
        guide_step_runtime_done: Optional[bool] = None,
        normal_relay_probability: Optional[float] = None,
        truth_reply_probability: Optional[float] = None,
        backlog_labels: Optional[list[dict[str, Any]]] = None,
        sticker_cooldown_messages: Optional[int] = None,
        group_attention_decay_per_second: Optional[float] = None,
        group_attention_message_recovery: Optional[float] = None,
        group_attention_reply_penalty: Optional[float] = None,
        group_attention_keyword_boost_scale: Optional[float] = None,
        group_attention_focus_lock_seconds: Optional[int] = None,
        group_attention_focus_rise_seconds: Optional[int] = None,
        group_attention_focus_cooldown_seconds: Optional[int] = None,
        group_attention_max_score: Optional[float] = None,
        group_attention_focus_threshold: Optional[float] = None,
        group_attention_min_threshold: Optional[float] = None,
        group_attention_message_gain: Optional[float] = None,
        icebreaker_cold_threshold: Optional[int] = None,
        retroactive_review_max_messages: Optional[int] = None,
        retroactive_review_max_reply: Optional[int] = None,
        group_memory_enabled: Optional[bool] = None,
        group_member_memory_enabled: Optional[bool] = None,
        private_participant_memory_enabled: Optional[bool] = None,
        allow_cross_group_context: Optional[bool] = None,
        strategy_mode: Optional[str] = None,
        qq_connection_mode: Optional[str] = None,
        qq_open_app_id: Optional[str] = None,
        qq_open_client_secret: Optional[str] = None,
        qq_open_identity_probe_enabled: Optional[bool] = None,
        local_stt_url: Optional[str] = None,
    ):
        try:
            result = await self.plugin.settings_service.save_settings(
                onebot_url=onebot_url,
                token=token,
                napcat_directory=napcat_directory,
                show_napcat_window=show_napcat_window,
                reply_mode=reply_mode,
                show_onboarding=show_onboarding,
                guide_step_napcat_done=guide_step_napcat_done,
                guide_step_config_done=guide_step_config_done,
                guide_step_runtime_done=guide_step_runtime_done,
                normal_relay_probability=normal_relay_probability,
                truth_reply_probability=truth_reply_probability,
                backlog_labels=backlog_labels,
                sticker_cooldown_messages=sticker_cooldown_messages,
                group_attention_decay_per_second=group_attention_decay_per_second,
                group_attention_message_recovery=group_attention_message_recovery,
                group_attention_reply_penalty=group_attention_reply_penalty,
                group_attention_keyword_boost_scale=group_attention_keyword_boost_scale,
                group_attention_focus_lock_seconds=group_attention_focus_lock_seconds,
                group_attention_focus_rise_seconds=group_attention_focus_rise_seconds,
                group_attention_focus_cooldown_seconds=group_attention_focus_cooldown_seconds,
                group_attention_max_score=group_attention_max_score,
                group_attention_focus_threshold=group_attention_focus_threshold,
                group_attention_min_threshold=group_attention_min_threshold,
                group_attention_message_gain=group_attention_message_gain,
                icebreaker_cold_threshold=icebreaker_cold_threshold,
                retroactive_review_max_messages=retroactive_review_max_messages,
                retroactive_review_max_reply=retroactive_review_max_reply,
                group_memory_enabled=group_memory_enabled,
                group_member_memory_enabled=group_member_memory_enabled,
                private_participant_memory_enabled=private_participant_memory_enabled,
                allow_cross_group_context=allow_cross_group_context,
                strategy_mode=strategy_mode,
                qq_connection_mode=qq_connection_mode,
                qq_open_app_id=qq_open_app_id,
                qq_open_client_secret=qq_open_client_secret,
                qq_open_identity_probe_enabled=qq_open_identity_probe_enabled,
                local_stt_url=local_stt_url,
            )
        except ValueError as exc:
            message = str(exc)
            if "truth_reply_probability" in message:
                field = "truth_reply_probability"
            else:
                field = "normal_relay_probability"
            return Err(SdkError(f"INVALID_ARGUMENT: {self.plugin.i18n.t('errors.invalid_probability', default=field + ' 必须在 0 到 1 之间')}"))
        payload = await self.build_dashboard_state()
        payload.update(result)
        return Ok(self._inject_business_permissions(payload))

    async def add_trusted_user(
        self,
        *,
        qq_number: str,
        level: str = "trusted",
        nickname: str = "",
        normal_relay_probability: Optional[float] = None,
    ):
        if not self.plugin.permission_mgr:
            return Err(SdkError(f"NOT_INITIALIZED: {self.plugin.i18n.t('errors.permission_manager_not_initialized', default='权限管理器未初始化')}"))
        normalized_nickname = "" if level == "admin" else nickname
        if normal_relay_probability is not None:
            value = float(normal_relay_probability)
            if value < 0.0 or value > 1.0:
                return Err(SdkError(f"INVALID_ARGUMENT: {self.plugin.i18n.t('errors.invalid_probability', default='normal_relay_probability 必须在 0 到 1 之间')}"))
        if not self.plugin.permission_mgr.add_user(
            qq_number,
            level,
            normalized_nickname,
            normal_relay_probability=normal_relay_probability,
        ):
            return Err(SdkError(f"SET_FAILED: {self.plugin.i18n.t('errors.set_nickname_failed', default='设置昵称失败')}"))
        self.plugin._refresh_admin_qq()
        await self.plugin._invalidate_private_session(qq_number)
        success = await self.plugin.settings_service.persist_business_config()
        payload = await self.build_dashboard_state()
        payload["persisted"] = success
        return Ok(payload)

    #: 查多少个名册用户的账本权重就停。名册通常只有几个人，这个上限存在的
    #: 意义是别让一个被塞了几百人的名册在每次打开页面时发几百个请求。
    IDENTITY_CANDIDATE_MAX = 50
    #: 单个权重查询的超时。刻意远小于 bridge 的默认 10s：前端 ``call()`` 的
    #: 死线是固定 20s，而权重只用来排序——为了排序把整个页面拖到超时，等于
    #: 用一个装饰性字段换掉了唯一的修复入口。
    IDENTITY_CANDIDATE_TIMEOUT = 3.0

    async def list_identity_claims(self):
        """待认领的群内 ID + 合并候选（设计文档 §2.15.4.3 第 1 级）。

        候选**只按账本权重排序**（``|adjustment| + message_count``），且
        **不预选任何一项**。这不是 UI 品味问题：按昵称相似度排序等于把一个
        被硬约束否决的启发式（自动身份合并）塞给用户当默认答案，而合错两个
        人会污染账本且不可回退。排序规则要改，先去改设计文档。
        """
        permission_mgr = self.plugin.permission_mgr
        bridge = getattr(self.plugin, "memory_bridge", None)
        dispatcher = getattr(self.plugin, "message_dispatcher", None)
        # 已经加进名册的 ID 当场出清单。只靠 `_note_open_platform_pending_claim`
        # 移除的话，得等那个人**再发一次言**才消失——而他刚被认领，最可能的
        # 下一步就是操作者刷新页面，看见同一行还在、于是重复点一次。
        claims = (
            dispatcher.list_open_platform_pending_claims(
                is_claimed=(
                    (lambda actor:
                     permission_mgr.get_permission_level(actor) != "none")
                    if permission_mgr is not None else None
                ),
            )
            if dispatcher is not None else []
        )
        candidates: list[dict[str, Any]] = []
        if permission_mgr is not None and bridge is not None:
            roster = permission_mgr.list_users()[:self.IDENTITY_CANDIDATE_MAX]

            async def _weight(account_id: str) -> dict[str, Any]:
                try:
                    return await bridge.fetch_speaker_profile(
                        account_id, timeout=self.IDENTITY_CANDIDATE_TIMEOUT,
                    )
                except Exception:
                    # 服务端没起来时照样要能列出名册；权重缺失只影响排序，
                    # 不影响用户认得出「这是我私聊授权的那个自己」。
                    return {}

            account_ids = [
                bridge.speaker_account_id(user.get("qq")) for user in roster
            ]
            # 并发而不是串行：串行时最坏情况是 N × 超时，轻易越过前端 20s 的
            # 死线，于是上面那个「服务端不可达也要能列出」的兜底反而失效。
            profiles = await asyncio.gather(
                *(_weight(account_id) for account_id in account_ids)
            )
            for user, account_id, profile in zip(roster, account_ids, profiles):
                candidates.append({
                    "account_id": account_id,
                    "qq": str(user.get("qq") or ""),
                    "level": str(user.get("level") or ""),
                    "nickname": str(user.get("nickname") or ""),
                    "entity_id": profile.get("entity_id"),
                    "adjustment_sum": float(profile.get("adjustment_sum") or 0.0),
                    "message_count": int(
                        profile.get("account_message_count") or 0
                    ),
                })
        candidates.sort(
            key=lambda row: (
                abs(row["adjustment_sum"]) + row["message_count"]
            ),
            reverse=True,
        )
        return Ok({
            "claims": claims,
            "candidates": candidates,
            "identity_scope": self._identity_scope_payload(),
        })

    def _identity_scope_payload(self) -> dict[str, Any]:
        """当前**在跑的**通道下标识符的协议语义，给 UI 显示降级提示用。

        读的是本地那张协议表而不是服务端已登记的值：提示要不要显示只取决于
        跑的是哪个通道，不该因为 memory_server 还没起来就少提示一句。

        以**运行中的连接**为准而不是配置：改了 `qq_connection_mode` 之后旧
        连接还在跑（save 的响应自己会报 ``reconnect_required``），这段时间
        里按配置显示，等于在开放平台消息还在进来的时候把认领 UI 藏起来。
        没有在跑时才回落到配置——那时配置就是下次连上的样子。

        「在跑」看的是 ``_running`` 而不是 ``qq_client`` 是否为 None：
        ``stop_runtime`` 只断开连接、把对象留在原地，而 ``CHANNEL`` 是类属
        性，光看对象会把一个已经停掉的通道当成活的。
        """
        settings = self.plugin._qq_settings or {}
        mode = str(settings.get("qq_connection_mode") or "napcat").strip()
        table = self.plugin.settings_service.IDENTITY_SCOPE_BY_MODE
        client = (
            getattr(self.plugin, "qq_client", None)
            if getattr(self.plugin, "_running", False) else None
        )
        live_channel = str(getattr(client, "CHANNEL", "") or "").strip()
        if live_channel:
            mode = next(
                (
                    key for key, entry in table.items()
                    if entry[0] == live_channel
                ),
                mode,
            )
        entry = table.get(mode)
        if entry is None:
            return {
                "mode": mode, "channel": "",
                "actor_scope": "unknown", "conversation_scope": "unknown",
            }
        channel, actor_scope, conversation_scope = entry
        return {
            "mode": mode,
            "channel": channel,
            "actor_scope": actor_scope,
            "conversation_scope": conversation_scope,
        }

    async def _identity_is_attached(self, bridge, account_id: str) -> bool:
        """这个账号现在**是不是已经有归属**了（bind 的前置判据）。

        并集：entity 下不止它一个账号，或者它带着 ``bound_by`` 落款。前者
        认得出「已经并进某个身份」，后者认得出「并进去之后对方又被拆走、
        只剩落款」——对 bind 来说两种都不能再绑，只看其一会漏。

        这只是**前置**判据，真正的把关在服务端临界区里（``require_unbound``）：
        两个页签并发绑同一个源时，两次前置检查可以都答「没绑」。这里先问一
        次是为了给出人话错误，不是为了保证正确性。
        """
        profile = await bridge.fetch_speaker_profile(
            account_id, timeout=self.IDENTITY_CANDIDATE_TIMEOUT,
        )
        accounts = list(profile.get("entity_accounts") or [])
        return len(accounts) > 1 or bool(profile.get("bound_by"))

    async def _identity_has_bind_provenance(self, bridge, account_id: str) -> bool:
        """这个账号是不是**我方 bind 出来的那一侧**（unbind 的前置判据）。

        和上面那个判据**必须分开**，虽然看着像。落款只落在被绑的那一侧：
        把 B 并进 A 之后，A 的 entity 下有两个账号但没有 ``bound_by``。用
        「entity 下不止一个」去判 A 可 unbind，拆掉的是**原目标 A**而不是
        B——A 的账本被搬走，按旧 entity 解析过的行留在原地。而合并入口现在
        也挂在名册行上，A 就在那儿，点得到。

        所以回滚的合法对象只有带落款的那一侧。
        """
        profile = await bridge.fetch_speaker_profile(
            account_id, timeout=self.IDENTITY_CANDIDATE_TIMEOUT,
        )
        return bool(profile.get("bound_by"))

    def _identity_bridge(self):
        """记忆桥，缺席时返回 ``(None, Err)``。"""
        bridge = getattr(self.plugin, "memory_bridge", None)
        if bridge is not None:
            return bridge, None
        return None, Err(SdkError(
            "NOT_INITIALIZED: "
            + self.plugin.i18n.t(
                "errors.memory_bridge_not_initialized",
                default="记忆桥未初始化",
            )
        ))

    async def bind_identity_account(
        self, *, user_id: str, target_user_id: str,
    ):
        """把一个群内 ID 并入某个**名册用户**的身份。只能由人在 UI 上触发。

        参数是目标**账号**而不是目标 entity，因为 entity 只从账本活动里
        诞生：新装机器上、或记忆开关关着时，靠第一条私聊自动授权的那个主人
        一个 entity 都没有——而他恰恰是所有群内 ID 要并进去的那一个。按
        entity 收参会让这个主用例在 UI 上根本选不中。所以这里先 ensure 出
        目标的种子 entity，再 bind。

        ensure 出来的种子 entity 不是一条边（它把账号连到自己），真正的人身
        断言是随后那次 bind。

        合并的是**信赖度账本**（entity←account），不是权限名册：名册按裸
        actor id 索引，所以「让主人在这个群里也算主人」仍然要单独把这个 ID
        加进信任用户。两件事分开做是对的——把 bind 顺手当成提权会让信赖度
        这一层变成权限升级的通道。
        """
        bridge, error = self._identity_bridge()
        if error is not None:
            return error
        actor = str(user_id or "").strip()
        target_actor = str(target_user_id or "").strip()
        if not actor or not target_actor:
            return Err(SdkError(
                "INVALID_ARGUMENT: "
                + self.plugin.i18n.t(
                    "errors.identity_bind_missing_args",
                    default="user_id 与 target_user_id 都不能为空",
                )
            ))
        if actor == target_actor:
            return Err(SdkError(
                "INVALID_ARGUMENT: "
                + self.plugin.i18n.t(
                    "errors.identity_bind_same_account",
                    default="不能把一个 ID 合并到它自己",
                )
            ))
        # 目标必须此刻仍在名册里。UI 给的候选就是名册，但页签可能是旧的
        # （另一个页签刚把那个人移除），而这个 entry 也能被通用表单直接调、
        # 手输一个错字。`ensure_speaker_account` 对任何字符串都会建 entity，
        # 于是源账本会被搬进一个凭空捏出来的身份，且成功返回。
        permission_mgr = self.plugin.permission_mgr
        if (
            permission_mgr is None
            or permission_mgr.get_permission_level(target_actor) == "none"
        ):
            return Err(SdkError(
                "UNKNOWN_TARGET: "
                + self.plugin.i18n.t(
                    "errors.identity_target_not_in_roster",
                    default="合并目标不在信任用户名册里，请刷新后重试",
                )
            ))
        # 平台前缀只在 memory_bridge 里拼一次，调用侧（含前端）不许自己拼。
        source_account = bridge.speaker_account_id(actor)
        target_account = bridge.speaker_account_id(target_actor)
        try:
            # 已经绑过的必须先撤销才能改绑。直接改绑不是「换个目标」：
            # `_bind_locked` 在源账号已有归属时走的是 merge，把**第一个目标
            # 和第二个目标**并成一个身份——两个不同的真人。而 unbind 只拆得
            # 回源账号，那两个目标仍然合着，操作者没有任何办法退回去。
            if await self._identity_is_attached(bridge, source_account):
                return Err(SdkError(
                    "ALREADY_BOUND: "
                    + self.plugin.i18n.t(
                        "errors.identity_already_bound",
                        default=(
                            "这个 ID 已经合并过了。要改绑到别人，"
                            "请先撤销当前的合并。"
                        ),
                    )
                ))
            ensured = await bridge.ensure_speaker_account(
                account_id=target_account,
            )
            entity_id = str(ensured.get("entity_id") or "")
            if not entity_id:
                raise RuntimeError("ensure returned no entity_id")
            if ensured.get("persisted") is False:
                # 种子 entity 的 draft 被丢弃了 ⇒ 它并不存在，随后的 bind 会
                # 以「unknown entity」404 收场，把操作者指向身份图而不是那次
                # 失败的写盘。
                return Err(SdkError(
                    "NOT_PERSISTED: "
                    + self.plugin.i18n.t(
                        "errors.identity_not_persisted",
                        default="写盘失败，身份没有改动，请重试",
                    )
                ))
            result = await bridge.bind_speaker_account(
                account_id=source_account,
                entity_id=entity_id,
                bound_by="qq_auto_reply.dashboard",
                # 真正的把关：上面那次预检会被并发绕过（两个页签同时绑同一
                # 个源，两次预检都答「没绑」，第二次就走进 merge 分支融合
                # 两个候选）。只有临界区里的这一次判断不会失效。
                require_unbound=True,
            )
        except Exception as exc:
            return Err(SdkError(
                "BIND_FAILED: "
                + self.plugin.i18n.t(
                    "errors.identity_bind_failed",
                    default="合并身份失败: {error}", error=str(exc),
                )
            ))
        if result.get("persisted") is False:
            # 写盘失败时 `_with_pool_write` 丢弃整份 draft，什么都没变。
            # 这时弹「已合并」会让操作者以为做完了，然后去检查一个根本不
            # 存在的合并。
            return Err(SdkError(
                "NOT_PERSISTED: "
                + self.plugin.i18n.t(
                    "errors.identity_not_persisted",
                    default="写盘失败，身份没有改动，请重试",
                )
            ))
        return Ok({"bind": result})

    async def unbind_identity_account(self, *, user_id: str):
        """把一个群内 ID 拆回独立身份。**误绑的唯一回滚。**

        必须和 bind 出现在同一个界面上：bind 立刻把两个账号的信赖度合到一
        起，操作者选错一项就需要当场能退回来，而不是去翻文档找一个内部端
        点。

        **先确认它真的是被绑的那一侧再动手。** ``aunbind_account`` 对一个
        「有账本但从没合并过」的独立账号并不是无操作：``_unbind_locked``
        认得的是「这个账号已注册」，于是照样把它搬进一个 generation+1 的新
        entity——已经按旧 entity 解析过的行会被留在原地，而反复按就反复造新
        entity。所以判据在这一层挡，服务端那句 ``changed=false`` 只覆盖
        「完全没注册过」这一种。

        判据只看 ``bound_by``，**不看 entity 下有几个账号**：落款只落在被
        绑的那一侧，所以「B 并进 A」之后 A 也满足「entity 下不止一个」。用
        那个判据会让操作者在 A 的名册行上点撤销、拆走**原目标 A**——而合并
        入口现在正挂在名册行上，A 就在那儿。

        ``ledger_delta`` 与 ``effective_delta`` 通常是两个不同的数字，这不
        是 bug：夹紧的聚合下「这个账号带走了多少」没有唯一答案。两个都原样
        透传给 UI。
        """
        bridge, error = self._identity_bridge()
        if error is not None:
            return error
        actor = str(user_id or "").strip()
        if not actor:
            # 这个接口只收 user_id，不能借用 bind 那条文案——它会报一个本
            # 接口根本没有的字段名，操作者会去界面上找一个不存在的东西。
            return Err(SdkError(
                "INVALID_ARGUMENT: "
                + self.plugin.i18n.t(
                    "errors.identity_unbind_missing_args",
                    default="user_id 不能为空",
                )
            ))
        account_id = bridge.speaker_account_id(actor)
        try:
            if not await self._identity_has_bind_provenance(bridge, account_id):
                return Ok({"unbind": {"changed": False, "reason": "not_bound"}})
            result = await bridge.unbind_speaker_account(
                account_id=account_id,
                # 真正的把关在临界区里：上面那次预检读到的 `bound_by` 会被
                # 并发的第一次撤销清掉，第二次就把一个**已经独立**的账号又
                # 搬进一个新 entity。
                require_provenance=True,
            )
            if result.get("changed") is False:
                return Ok({"unbind": result})
        except Exception as exc:
            return Err(SdkError(
                "UNBIND_FAILED: "
                + self.plugin.i18n.t(
                    "errors.identity_unbind_failed",
                    default="撤销合并失败: {error}", error=str(exc),
                )
            ))
        if result.get("persisted") is False:
            return Err(SdkError(
                "NOT_PERSISTED: "
                + self.plugin.i18n.t(
                    "errors.identity_not_persisted",
                    default="写盘失败，身份没有改动，请重试",
                )
            ))
        return Ok({"unbind": result})

    async def remove_trusted_user(self, *, qq_number: str):
        if not self.plugin.permission_mgr:
            return Err(SdkError(f"NOT_INITIALIZED: {self.plugin.i18n.t('errors.permission_manager_not_initialized', default='权限管理器未初始化')}"))
        self.plugin.permission_mgr.remove_user(qq_number)
        self.plugin._refresh_admin_qq()
        await self.plugin._invalidate_private_session(qq_number)
        success = await self.plugin.settings_service.persist_business_config()
        payload = await self.build_dashboard_state()
        payload["persisted"] = success
        return Ok(payload)

    async def set_user_nickname(self, *, qq_number: str, nickname: str = ""):
        if not self.plugin.permission_mgr:
            return Err(SdkError(f"NOT_INITIALIZED: {self.plugin.i18n.t('errors.permission_manager_not_initialized', default='权限管理器未初始化')}"))
        permission_level = self.plugin.permission_mgr.get_permission_level(qq_number)
        if permission_level == "none":
            return Err(SdkError(f"USER_NOT_FOUND: {self.plugin.i18n.t('errors.user_not_found', default='用户 {qq_number} 不在信任列表中', qq_number=qq_number)}"))
        if permission_level == "admin":
            return Err(SdkError(f"ADMIN_NO_NICKNAME: {self.plugin.i18n.t('errors.admin_no_nickname', default='管理员始终被称为主人，无法设置昵称')}"))
        success = self.plugin.permission_mgr.set_nickname(qq_number, nickname)
        if not success:
            return Err(SdkError(f"SET_FAILED: {self.plugin.i18n.t('errors.set_nickname_failed', default='设置昵称失败')}"))
        persisted = await self.plugin.settings_service.persist_business_config()
        payload = await self.build_dashboard_state()
        payload["persisted"] = persisted
        return Ok(payload)

    async def add_trusted_group(
        self,
        *,
        group_id: str,
        level: str = "normal",
        normal_relay_probability: Optional[float] = None,
        open_reply_probability: Optional[float] = None,
    ):
        if not self.plugin.group_permission_mgr:
            return Err(SdkError(f"NOT_INITIALIZED: {self.plugin.i18n.t('errors.group_permission_manager_not_initialized', default='群聊权限管理器未初始化')}"))
        if normal_relay_probability is not None:
            value = float(normal_relay_probability)
            if value < 0.0 or value > 1.0:
                return Err(SdkError(f"INVALID_ARGUMENT: {self.plugin.i18n.t('errors.invalid_probability', default='normal_relay_probability 必须在 0 到 1 之间')}"))
        if open_reply_probability is not None:
            value = float(open_reply_probability)
            if value < 0.0 or value > 1.0:
                return Err(SdkError(f"INVALID_ARGUMENT: {self.plugin.i18n.t('errors.invalid_probability', default='open_reply_probability 必须在 0 到 1 之间')}"))
        self.plugin.group_permission_mgr.add_group(group_id, level, normal_relay_probability=normal_relay_probability, open_reply_probability=open_reply_probability)
        await self.plugin.backlog_store.ensure_group_placeholder(group_id, group_display_name=f"QQ群 {group_id}")
        success = await self.plugin.settings_service.persist_business_config()
        payload = await self.build_dashboard_state()
        payload["persisted"] = success
        return Ok(payload)

    async def remove_trusted_group(self, *, group_id: str):
        if not self.plugin.group_permission_mgr:
            return Err(SdkError(f"NOT_INITIALIZED: {self.plugin.i18n.t('errors.group_permission_manager_not_initialized', default='群聊权限管理器未初始化')}"))
        self.plugin.group_permission_mgr.remove_group(group_id)
        await self.plugin.backlog_store.remove_group_placeholder(group_id)
        success = await self.plugin.settings_service.persist_business_config()
        payload = await self.build_dashboard_state()
        payload["persisted"] = success
        return Ok(payload)

    async def sync_qrcode(self):
        await self.plugin.napcat_service.sync_napcat_qrcode_into_static()
        return Ok(await self.build_dashboard_state())
