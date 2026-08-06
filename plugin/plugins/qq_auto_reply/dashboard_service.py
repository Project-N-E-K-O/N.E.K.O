from __future__ import annotations

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

    async def list_identity_claims(self):
        """待认领的群内 ID + 合并候选（设计文档 §2.15.4.3 第 1 级）。

        候选**只按账本权重排序**（``|adjustment| + message_count``），且
        **不预选任何一项**。这不是 UI 品味问题：按昵称相似度排序等于把一个
        被硬约束否决的启发式（自动身份合并）塞给用户当默认答案，而合错两个
        人会污染账本且不可回退。排序规则要改，先去改设计文档。
        """
        dispatcher = getattr(self.plugin, "message_dispatcher", None)
        claims = (
            dispatcher.list_open_platform_pending_claims()
            if dispatcher is not None else []
        )
        candidates: list[dict[str, Any]] = []
        permission_mgr = self.plugin.permission_mgr
        bridge = getattr(self.plugin, "memory_bridge", None)
        if permission_mgr is not None and bridge is not None:
            for user in permission_mgr.list_users()[:self.IDENTITY_CANDIDATE_MAX]:
                account_id = bridge.speaker_account_id(user.get("qq"))
                try:
                    profile = await bridge.fetch_speaker_profile(account_id)
                except Exception:
                    # 服务端没起来时照样要能列出名册；权重缺失只影响排序，
                    # 不影响用户认得出「这是我私聊授权的那个自己」。
                    profile = {}
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
        """当前连接模式下标识符的协议语义，给 UI 显示降级提示用。

        读的是本地那张协议表而不是服务端已登记的值：提示要不要显示只取决于
        现在跑的是哪个模式，不该因为 memory_server 还没起来就少提示一句。
        """
        settings = self.plugin._qq_settings or {}
        mode = str(settings.get("qq_connection_mode") or "napcat").strip()
        table = self.plugin.settings_service.IDENTITY_SCOPE_BY_MODE
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

    async def bind_identity_account(
        self, *, user_id: str, entity_id: str,
    ):
        """把一个群内 ID 并入已有身份。**只能由人在 UI 上触发。**

        合并的是**信赖度账本**（entity←account），不是权限名册：名册按裸
        actor id 索引，所以「让主人在这个群里也算主人」仍然要单独把这个 ID
        加进信任用户。两件事分开做是对的——把 bind 顺手当成提权会让信赖度
        这一层变成权限升级的通道。
        """
        bridge = getattr(self.plugin, "memory_bridge", None)
        if bridge is None:
            return Err(SdkError(
                "NOT_INITIALIZED: "
                + self.plugin.i18n.t(
                    "errors.memory_bridge_not_initialized",
                    default="记忆桥未初始化",
                )
            ))
        actor = str(user_id or "").strip()
        target_entity = str(entity_id or "").strip()
        if not actor or not target_entity:
            return Err(SdkError(
                "INVALID_ARGUMENT: "
                + self.plugin.i18n.t(
                    "errors.identity_bind_missing_args",
                    default="user_id 与 entity_id 都不能为空",
                )
            ))
        # 平台前缀只在 memory_bridge 里拼一次，调用侧（含前端）不许自己拼。
        target_account = bridge.speaker_account_id(actor)
        try:
            result = await bridge.bind_speaker_account(
                account_id=target_account,
                entity_id=target_entity,
                bound_by="qq_auto_reply.dashboard",
            )
        except Exception as exc:
            return Err(SdkError(
                "BIND_FAILED: "
                + self.plugin.i18n.t(
                    "errors.identity_bind_failed",
                    default="合并身份失败: {error}", error=str(exc),
                )
            ))
        return Ok({"bind": result})

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
