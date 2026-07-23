from __future__ import annotations

from typing import Any, Optional

from plugin.sdk.plugin import Err, Ok, SdkError


def _calc_service_done(settings: dict[str, Any]) -> bool:
    mode = str(settings.get("qq_connection_mode", "napcat") or "napcat").strip()
    if mode == "open_platform":
        return bool(settings.get("qq_open_app_id")) and bool(settings.get("qq_open_client_secret"))
    return bool(settings.get("onebot_url")) and bool(settings.get("token"))


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
                "proactive_topics": list(settings.get("proactive_topics") or []),
                "enable_group_attention": bool(settings.get("enable_group_attention", True)),
                "retroactive_review_max_messages": int(settings.get("retroactive_review_max_messages", 30) or 30),
                "retroactive_review_max_reply": int(settings.get("retroactive_review_max_reply", 5) or 5),
                "sticker_cooldown_messages": int(settings.get("sticker_cooldown_messages", 5) or 5),
            },
            "guide": {
                "step_napcat_done": (
                    bool(settings.get("guide_step_napcat_done", False))
                    or bool(runtime["napcat_managed"] and runtime["napcat_running"])
                    or bool(runtime.get("onebot_connected"))
                ),
                "step_service_done": _calc_service_done(settings),
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
        retroactive_review_max_messages: Optional[int] = None,
        retroactive_review_max_reply: Optional[int] = None,
        strategy_mode: Optional[str] = None,
        qq_connection_mode: Optional[str] = None,
        qq_open_app_id: Optional[str] = None,
        qq_open_client_secret: Optional[str] = None,
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
                retroactive_review_max_messages=retroactive_review_max_messages,
                retroactive_review_max_reply=retroactive_review_max_reply,
                strategy_mode=strategy_mode,
                qq_connection_mode=qq_connection_mode,
                qq_open_app_id=qq_open_app_id,
                qq_open_client_secret=qq_open_client_secret,
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
        self.plugin.permission_mgr.add_user(qq_number, level, normalized_nickname, normal_relay_probability=normal_relay_probability)
        self.plugin._refresh_admin_qq()
        await self.plugin._invalidate_private_session(qq_number)
        success = await self.plugin.settings_service.persist_business_config()
        payload = await self.build_dashboard_state()
        payload["persisted"] = success
        return Ok(payload)

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
        # 强制清理 backlog 中的群数据（含未审阅消息）
        await self.plugin.backlog_store.remove_group_placeholder(group_id, force=True)
        # 清理 attention 缓存
        if self.plugin.attention_service:
            self.plugin.attention_service._cache.pop(str(group_id), None)
        # 清理会话和疲劳状态
        session_key = f"group:{group_id}"
        if self.plugin.session_runtime_service:
            await self.plugin.session_runtime_service.discard_session(session_key, reason="group_removed")
        if self.plugin.fatigue_service:
            self.plugin.fatigue_service._sleeping.pop(session_key, None)
            self.plugin.fatigue_service._last_active.pop(session_key, None)
            self.plugin.fatigue_service._session_fatigue_values.pop(session_key, None)
            self.plugin.fatigue_service._wake_penalty.pop(session_key, None)
        success = await self.plugin.settings_service.persist_business_config()
        payload = await self.build_dashboard_state()
        payload["persisted"] = success
        return Ok(payload)

    async def sync_qrcode(self):
        await self.plugin.napcat_service.sync_napcat_qrcode_into_static()
        return Ok(await self.build_dashboard_state())
