from __future__ import annotations

from typing import Any

from .permission import PermissionManager
from .group_permission import GroupPermissionManager


class QQSettingsService:
    def __init__(self, plugin: Any):
        self.plugin = plugin

    async def load_business_config(self) -> dict[str, Any]:
        self.plugin._qq_settings = await self.plugin.config_store.load()
        self.plugin.backlog_store = self.plugin._create_backlog_store_from_settings(self.plugin._qq_settings)
        self._enforce_attention_for_dynamic_mode()
        return dict(self.plugin._qq_settings)

    async def ensure_business_config_initialized(self) -> dict[str, Any]:
        if not await self.plugin.config_store.exists():
            return self.plugin.config_store.default_config()
        return await self.load_business_config()

    async def create_business_config(self) -> dict[str, Any]:
        self.plugin._qq_settings = await self.plugin.config_store.create_empty()
        return dict(self.plugin._qq_settings)

    async def persist_business_config(self) -> bool:
        try:
            self.plugin._qq_settings["trusted_users"] = self.plugin.permission_mgr.list_users() if self.plugin.permission_mgr else []
            self.plugin._qq_settings["trusted_groups"] = self.plugin.group_permission_mgr.list_groups() if self.plugin.group_permission_mgr else []
            self.plugin._qq_settings = await self.plugin.config_store.save(self.plugin._qq_settings)
            self.plugin.backlog_store = self.plugin._create_backlog_store_from_settings(self.plugin._qq_settings)
            return True
        except Exception as e:
            self.plugin.logger.error(f"持久化 QQ 配置失败: {e}")
            return False

    def apply_runtime_settings(self, settings: dict[str, Any]) -> None:
        self.plugin._normal_relay_probability = float(settings.get("normal_relay_probability", 0.1) or 0.1)
        self.plugin._truth_reply_probability = float(settings.get("open_reply_probability", settings.get("truth_reply_probability", 0.1)) or 0.1)
        self.plugin._max_concurrent_messages = max(1, int(settings.get("max_concurrent_messages", 3) or 3))
        self.plugin._message_concurrency = __import__("asyncio").Semaphore(self.plugin._max_concurrent_messages)
        self.plugin._ai_connect_timeout_seconds = max(1.0, float(settings.get("ai_connect_timeout_seconds", 10.0) or 10.0))
        self.plugin._ai_turn_timeout_seconds = max(5.0, float(settings.get("ai_turn_timeout_seconds", 60.0) or 60.0))
        self.plugin._handler_shutdown_timeout_seconds = max(1.0, float(settings.get("handler_shutdown_timeout_seconds", 10.0) or 10.0))
        self.plugin._backlog_summary_threshold = max(1, int(settings.get("backlog_summary_threshold", 10) or 10))
        self.plugin._backlog_notify_cooldown_seconds = max(60, int(settings.get("backlog_notify_cooldown_seconds", 900) or 900))
        self.plugin._backlog_issue_notify_threshold = max(1, int(settings.get("backlog_issue_notify_threshold", 1) or 1))
        self.plugin._sticker_cooldown_messages = max(0, int(settings.get("sticker_cooldown_messages", 5) or 5))
        # 猫娘动态注意力策略配置
        self.plugin._strategy_mode = self.plugin.config_store._normalize_strategy_mode(settings.get("strategy_mode"))
        self._enforce_attention_for_dynamic_mode()
        # 前端日志：显示当前连接配置（token 脱敏），方便用户排查浏览器自动回填等问题
        url = str(settings.get("onebot_url") or "").strip()
        masked = self.plugin._mask_token(str(settings.get("token") or ""))
        mode = str(settings.get("qq_connection_mode") or "napcat").strip()
        self.plugin._emit_log("INFO", f"连接模式: {mode} | 地址: {url or '(未配置)'} | Token: {masked}{' (空)' if not settings.get('token') else ''} | 策略: {self.plugin._strategy_mode}")

    def _enforce_attention_for_dynamic_mode(self) -> None:
        """neko_dynamic 模式下强制启用多群注意力，确保磁盘配置与运行时一致。"""
        strategy_mode = self.plugin.config_store._normalize_strategy_mode(
            self.plugin._qq_settings.get("strategy_mode")
        )
        if strategy_mode == "neko_dynamic":
            self.plugin._qq_settings["enable_group_attention"] = True

    def rebuild_permission_managers(self, config: dict[str, Any]) -> None:
        self.plugin.permission_mgr = PermissionManager(config.get("trusted_users", []))
        self.plugin.group_permission_mgr = GroupPermissionManager(config.get("trusted_groups", []))
        self.plugin._refresh_admin_qq()

    async def save_settings(self, **kwargs: Any) -> dict[str, Any]:
        onebot_url = kwargs.get("onebot_url")
        token = kwargs.get("token")
        napcat_directory = kwargs.get("napcat_directory")
        show_napcat_window = kwargs.get("show_napcat_window")
        reply_mode = kwargs.get("reply_mode")
        show_onboarding = kwargs.get("show_onboarding")
        guide_step_napcat_done = kwargs.get("guide_step_napcat_done")
        guide_step_config_done = kwargs.get("guide_step_config_done")
        guide_step_runtime_done = kwargs.get("guide_step_runtime_done")
        normal_relay_probability = kwargs.get("normal_relay_probability")
        truth_reply_probability = kwargs.get("truth_reply_probability")
        backlog_labels = kwargs.get("backlog_labels")

        if onebot_url is not None:
            self.plugin._qq_settings["onebot_url"] = str(onebot_url or "").strip()
            self.plugin._emit_log("INFO", f"OneBot 地址已更新: {self.plugin._qq_settings['onebot_url'] or '(空)'}")
        if token is not None:
            self.plugin._qq_settings["token"] = str(token or "")
            masked = self.plugin._mask_token(self.plugin._qq_settings["token"])
            self.plugin._emit_log("INFO", f"Token 已更新: {masked}{' (空)' if not self.plugin._qq_settings['token'] else ''}")
        qq_connection_mode = kwargs.get("qq_connection_mode")
        qq_open_app_id = kwargs.get("qq_open_app_id")
        qq_open_client_secret = kwargs.get("qq_open_client_secret")
        if qq_connection_mode is not None:
            self.plugin._qq_settings["qq_connection_mode"] = str(qq_connection_mode or "napcat").strip()
            self.plugin._emit_log("INFO", f"连接模式已切换: {self.plugin._qq_settings['qq_connection_mode']}")
        if qq_open_app_id is not None:
            self.plugin._qq_settings["qq_open_app_id"] = str(qq_open_app_id or "").strip()
        if qq_open_client_secret is not None:
            self.plugin._qq_settings["qq_open_client_secret"] = str(qq_open_client_secret or "").strip()
        if napcat_directory is not None:
            self.plugin._qq_settings["napcat_directory"] = str(napcat_directory or "").strip()
        if show_napcat_window is not None:
            self.plugin._qq_settings["show_napcat_window"] = bool(show_napcat_window)
        if reply_mode is not None:
            self.plugin._qq_settings["reply_mode"] = self.plugin.config_store.normalize_reply_mode(reply_mode)
            self.plugin._emit_log("INFO", f"回复模式已切换: {self.plugin._qq_settings['reply_mode']}")
        if show_onboarding is not None:
            self.plugin._qq_settings["show_onboarding"] = bool(show_onboarding)
        if guide_step_napcat_done is not None:
            self.plugin._qq_settings["guide_step_napcat_done"] = bool(guide_step_napcat_done)
        if guide_step_config_done is not None:
            self.plugin._qq_settings["guide_step_config_done"] = bool(guide_step_config_done)
        if guide_step_runtime_done is not None:
            self.plugin._qq_settings["guide_step_runtime_done"] = bool(guide_step_runtime_done)
        if normal_relay_probability is not None:
            value = float(normal_relay_probability)
            if value < 0.0 or value > 1.0:
                raise ValueError("normal_relay_probability 必须在 0 到 1 之间")
            self.plugin._qq_settings["normal_relay_probability"] = value
            self.plugin._normal_relay_probability = value
        if truth_reply_probability is not None:
            value = float(truth_reply_probability)
            if value < 0.0 or value > 1.0:
                raise ValueError("truth_reply_probability 必须在 0 到 1 之间")
            self.plugin._qq_settings["open_reply_probability"] = value
            self.plugin._qq_settings["truth_reply_probability"] = value
            self.plugin._truth_reply_probability = value
        if backlog_labels is not None:
            self.plugin._qq_settings["backlog_labels"] = self.plugin.config_store.normalize_backlog_labels(backlog_labels)
        sticker_cooldown_messages = kwargs.get("sticker_cooldown_messages")
        if sticker_cooldown_messages is not None:
            self.plugin._qq_settings["sticker_cooldown_messages"] = max(0, int(sticker_cooldown_messages))
            self.plugin._sticker_cooldown_messages = max(0, int(sticker_cooldown_messages))
        retroactive_review_max_messages = kwargs.get("retroactive_review_max_messages")
        if retroactive_review_max_messages is not None:
            self.plugin._qq_settings["retroactive_review_max_messages"] = max(1, int(retroactive_review_max_messages))
        retroactive_review_max_reply = kwargs.get("retroactive_review_max_reply")
        if retroactive_review_max_reply is not None:
            self.plugin._qq_settings["retroactive_review_max_reply"] = max(1, int(retroactive_review_max_reply))
        # 猫娘动态策略配置
        strategy_mode = kwargs.get("strategy_mode")
        if strategy_mode is not None:
            self.plugin._qq_settings["strategy_mode"] = self.plugin.config_store._normalize_strategy_mode(strategy_mode)
            self.plugin._emit_log("INFO", f"策略模式已切换: {self.plugin._qq_settings['strategy_mode']}")
        self._enforce_attention_for_dynamic_mode()
        self.plugin._qq_settings.pop("guide_step_settings_done", None)
        self.plugin._ensure_qq_client_initialized()
        success = await self.persist_business_config()
        if success:
            self.plugin._emit_log("INFO", "设置已保存到磁盘" + (" (需重启自动回复以应用新连接)" if self.plugin._running else ""))
        if self.plugin.qq_client:
            self.plugin.qq_client.onebot_url = self.plugin._qq_settings.get("onebot_url", self.plugin.qq_client.onebot_url)
            self.plugin.qq_client.token = self.plugin._qq_settings.get("token", self.plugin.qq_client.token)
        if onebot_url is not None or token is not None or napcat_directory is not None or show_napcat_window is not None or qq_connection_mode is not None or qq_open_app_id is not None or qq_open_client_secret is not None:
            self.plugin._startup_error = None
        return {
            "persisted": success,
            "reconnect_required": bool(self.plugin._running),
        }
