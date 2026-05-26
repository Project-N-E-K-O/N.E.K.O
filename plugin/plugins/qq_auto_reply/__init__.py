
from __future__ import annotations

# 加载本地依赖
import sys as _sys, pathlib as _pathlib
_lib_dir = _pathlib.Path(__file__).parent / "lib"
if _lib_dir.exists() and str(_lib_dir) not in _sys.path:
    _sys.path.insert(0, str(_lib_dir))
del _sys, _pathlib, _lib_dir

import asyncio
import random
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any, Dict, Optional
from urllib.parse import urlparse

from plugin.sdk.plugin import NekoPluginBase, lifecycle, neko_plugin, plugin_entry, Ok, Err, SdkError, tr, ui

from .feedback_classifier import QQFeedbackClassifier
from .backlog_models import QQBacklogMessage
from .backlog_store import QQBacklogStore
from .config_store import QQAutoReplyConfigStore
from .group_permission import GroupPermissionManager
from .permission import PermissionManager
from .prompting import QQAutoReplyPromptingMixin
from .qq_client import QQClient
from .session import QQAutoReplySessionMixin
from .summary_builder import QQSummaryBuilder
from .targets import QQAutoReplyTargetsMixin, QQAutoReplyValidationError


def build_open_ui_payload(*, plugin_id: str, available: bool, i18n=None) -> dict[str, Any]:
    path = f"/plugin/{plugin_id}/ui/" if available else ""
    message_key = "ui.open_path.message" if available else "ui.unavailable.message"
    default_message = "UI 已注册" if available else "UI 未注册"
    message = i18n.t(message_key, default=default_message) if i18n else default_message
    return {
        "available": available,
        "path": path,
        "message": message,
    }


@neko_plugin
class QQAutoReplyPlugin(QQAutoReplySessionMixin, QQAutoReplyPromptingMixin, QQAutoReplyTargetsMixin, NekoPluginBase):
    SESSION_IDLE_TIMEOUT_SECONDS = 300
    SESSION_SWEEP_INTERVAL_SECONDS = 30

    def __init__(self, ctx):
        super().__init__(ctx)
        self.file_logger = self.enable_file_logging(log_level="INFO")
        self.logger = self.file_logger
        self.config_store = QQAutoReplyConfigStore(self.data_path())
        self._qq_settings: dict[str, Any] = self.config_store.default_config()
        self.backlog_store = QQBacklogStore(
            self.data_path(),
            retention_limit=int(self._qq_settings.get("backlog_retention_limit", 200) or 200),
        )
        self.qq_client: Optional[QQClient] = None
        self.permission_mgr: Optional[PermissionManager] = None
        self.group_permission_mgr: Optional[GroupPermissionManager] = None
        self._running = False
        self._message_task: Optional[asyncio.Task] = None
        self._session_housekeeping_task: Optional[asyncio.Task] = None
        self._handler_tasks: set[asyncio.Task] = set()
        self._user_sessions: dict[str, dict[str, Any]] = {}
        self._session_locks: dict[str, asyncio.Lock] = {}
        self._session_locks_guard = asyncio.Lock()
        self._message_concurrency = asyncio.Semaphore(3)
        self._max_concurrent_messages = 3
        self._ai_connect_timeout_seconds = 10.0
        self._ai_turn_timeout_seconds = 60.0
        self._handler_shutdown_timeout_seconds = 10.0
        self._normal_relay_probability = 0.1
        self._truth_reply_probability = 0.1
        self._admin_qq: Optional[str] = None
        self._napcat_process: Optional[asyncio.subprocess.Process] = None
        self._manages_napcat_process = False
        self._proactive_task: Optional[asyncio.Task] = None
        self._last_proactive_enabled = False
        self._last_proactive_send_at = 0.0
        self._last_proactive_greeting_at = 0.0
        self._backlog_summary_threshold = 10
        self._backlog_notify_cooldown_seconds = 900
        self._backlog_issue_notify_threshold = 1
        self._relay_backlog_items: list[dict[str, Any]] = []

    def _refresh_admin_qq(self) -> None:
        self._admin_qq = None
        if not self.permission_mgr:
            return
        for user in self.permission_mgr.list_users():
            if user.get("level") == "admin":
                qq = str(user.get("qq") or "").strip()
                if qq:
                    self._admin_qq = qq
                    return

    async def _load_business_config(self) -> dict[str, Any]:
        self._qq_settings = await self.config_store.load()
        self.backlog_store = QQBacklogStore(
            self.data_path(),
            retention_limit=int(self._qq_settings.get("backlog_retention_limit", 200) or 200),
        )
        return dict(self._qq_settings)

    async def _ensure_business_config_initialized(self) -> dict[str, Any]:
        if not await self.config_store.exists():
            return self.config_store.default_config()
        return await self._load_business_config()

    async def _create_business_config(self) -> dict[str, Any]:
        self._qq_settings = await self.config_store.create_empty()
        return dict(self._qq_settings)

    async def _persist_business_config(self) -> bool:
        try:
            self._qq_settings["trusted_users"] = self.permission_mgr.list_users() if self.permission_mgr else []
            self._qq_settings["trusted_groups"] = self.group_permission_mgr.list_groups() if self.group_permission_mgr else []
            self._qq_settings = await self.config_store.save(self._qq_settings)
            return True
        except Exception as e:
            self.logger.error(f"持久化 QQ 配置失败: {e}")
            return False

    @lifecycle(id="startup")
    async def startup(self, **_):
        if not await self.config_store.exists():
            await self._create_business_config()
        settings = await self._ensure_business_config_initialized()
        self.logger.info(f"[qq_auto_reply debug] startup settings loaded: {settings}")
        self.permission_mgr = PermissionManager(settings.get("trusted_users", []))
        self.group_permission_mgr = GroupPermissionManager(settings.get("trusted_groups", []))
        self._refresh_admin_qq()
        self._normal_relay_probability = float(settings.get("normal_relay_probability", 0.1) or 0.1)
        self._truth_reply_probability = float(settings.get("open_reply_probability", settings.get("truth_reply_probability", 0.1)) or 0.1)
        self._max_concurrent_messages = max(1, int(settings.get("max_concurrent_messages", 3) or 3))
        self._message_concurrency = asyncio.Semaphore(self._max_concurrent_messages)
        self._ai_connect_timeout_seconds = max(1.0, float(settings.get("ai_connect_timeout_seconds", 10.0) or 10.0))
        self._ai_turn_timeout_seconds = max(5.0, float(settings.get("ai_turn_timeout_seconds", 60.0) or 60.0))
        self._handler_shutdown_timeout_seconds = max(1.0, float(settings.get("handler_shutdown_timeout_seconds", 10.0) or 10.0))
        self._backlog_summary_threshold = max(1, int(settings.get("backlog_summary_threshold", 10) or 10))
        self._backlog_notify_cooldown_seconds = max(60, int(settings.get("backlog_notify_cooldown_seconds", 900) or 900))
        self._backlog_issue_notify_threshold = max(1, int(settings.get("backlog_issue_notify_threshold", 1) or 1))
        self.qq_client = QQClient(
            onebot_url=str(settings.get("onebot_url") or "ws://127.0.0.1:3001"),
            token=str(settings.get("token") or ""),
            logger=self.logger,
        )
        await self._ensure_napcat_started()
        self.register_static_ui("static")
        self.set_list_actions([
            {
                "id": "open_ui",
                "label": self.i18n.t("ui.actions.open", default="打开 UI"),
                "kind": "ui",
                "target": f"/plugin/{self.plugin_id}/ui/",
                "open_in": "new_tab",
            }
        ])
        if self._session_housekeeping_task is None or self._session_housekeeping_task.done():
            self._session_housekeeping_task = asyncio.create_task(self._session_housekeeping_loop())
        return Ok({"status": "ready"})

    @lifecycle(id="shutdown")
    async def shutdown(self, **_):
        await self._stop_auto_reply_runtime(stop_napcat=True)
        await self._flush_all_memory_sessions(reason="shutdown")
        if self._session_housekeeping_task:
            self._session_housekeeping_task.cancel()
            try:
                await self._session_housekeeping_task
            except asyncio.CancelledError:
                pass
            self._session_housekeeping_task = None
        return Ok({"status": "shutdown"})

    def _mask_token(self, token: str) -> str:
        normalized = str(token or "")
        if not normalized:
            return ""
        if len(normalized) <= 6:
            return "*" * len(normalized)
        return f"{normalized[:3]}***{normalized[-3:]}"

    def _get_napcat_directory(self) -> Path:
        configured = str((self._qq_settings or {}).get("napcat_directory") or "").strip()
        if configured:
            return Path(configured)
        return Path(__file__).parent / "NapCat.Shell"

    def _get_napcat_qrcode_path(self) -> Path:
        return self._get_napcat_directory() / "cache" / "qrcode.png"

    async def _sync_napcat_qrcode_into_static(self) -> bool:
        source = self._get_napcat_qrcode_path()
        target = self.config_dir / "static" / "cache" / "qrcode.png"
        if not source.is_file():
            return False
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            await asyncio.to_thread(shutil.copy2, source, target)
            return True
        except Exception as e:
            self.logger.warning(f"Failed to copy NapCat QR code into static cache: {e}")
            return False

    def _find_napcat_launcher(self) -> Path | None:
        root = self._get_napcat_directory()
        candidates = [
            root / "launcher-user.bat",
            root / "launcher.bat",
        ]
        for candidate in candidates:
            if candidate.is_file():
                return candidate
        return None

    async def _ensure_napcat_started(self) -> None:
        if self._napcat_process and self._napcat_process.returncode is None:
            return
        launcher = self._find_napcat_launcher()
        if launcher is None:
            return
        try:
            show_window = bool(self._qq_settings.get("show_napcat_window", True))
            creationflags = 0
            if show_window:
                command = ["cmd.exe", "/c", "start", "", str(launcher)]
            else:
                command = ["cmd.exe", "/c", str(launcher)]
                if hasattr(subprocess, "CREATE_NO_WINDOW"):
                    creationflags = subprocess.CREATE_NO_WINDOW
            self._napcat_process = await asyncio.create_subprocess_exec(
                *command,
                cwd=str(launcher.parent),
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
                creationflags=creationflags,
            )
            self._manages_napcat_process = True
            self.logger.info(f"Started NapCat launcher via cmd: {launcher} (pid={self._napcat_process.pid}, show_window={show_window})")
            async def _delayed_sync_qrcode():
                await asyncio.sleep(1.5)
                await self._sync_napcat_qrcode_into_static()
            asyncio.create_task(_delayed_sync_qrcode())
        except Exception as e:
            self.logger.warning(f"Failed to start NapCat launcher {launcher}: {e}")

    async def _stop_managed_napcat(self) -> None:
        if not self._manages_napcat_process:
            return
        process = self._napcat_process
        self._napcat_process = None
        self._manages_napcat_process = False
        if not process or process.returncode is not None:
            return
        try:
            process.terminate()
        except ProcessLookupError:
            return
        except Exception as e:
            self.logger.warning(f"Failed to terminate managed NapCat process: {e}")
            return
        try:
            await asyncio.wait_for(process.wait(), timeout=5.0)
        except asyncio.TimeoutError:
            self.logger.warning("Timed out waiting for managed NapCat process to stop; killing it")
            try:
                process.kill()
            except ProcessLookupError:
                return
            await process.wait()

    def _build_runtime_status(self) -> dict[str, Any]:
        qrcode_path = self.config_dir / "static" / "cache" / "qrcode.png"
        return {
            "plugin_running": True,
            "auto_reply_running": self._running,
            "onebot_connected": bool(self.qq_client and self.qq_client.ws),
            "napcat_managed": self._manages_napcat_process,
            "napcat_running": bool(self._napcat_process and self._napcat_process.returncode is None),
            "napcat_pid": int(self._napcat_process.pid) if self._napcat_process and self._napcat_process.returncode is None and self._napcat_process.pid else None,
            "qrcode_url": f"/plugin/{self.plugin_id}/ui/cache/qrcode.png" if qrcode_path.is_file() else "",
            "show_napcat_window": bool((self._qq_settings or {}).get("show_napcat_window", True)),
            "startup_error": None,
        }

    async def _fetch_login_status_payload(self) -> dict[str, Any]:
        payload: dict[str, Any] = {"status": "offline", "self_id": None, "nickname": None, "last_error": None}
        if not self.qq_client or not self.qq_client.ws:
            return payload
        try:
            login_info = await self.qq_client.get_login_info()
            payload["status"] = "online"
            payload["self_id"] = str(login_info.get("user_id") or "") or None
            payload["nickname"] = login_info.get("nickname") or None
            return payload
        except Exception as e:
            payload["status"] = "error"
            payload["last_error"] = str(e)
            return payload

    async def _refresh_actual_contacts_cache(self) -> dict[str, Any]:
        if not self.qq_client:
            raise RuntimeError(self.i18n.t("errors.qq_client_not_initialized", default="QQ 客户端未初始化"))
        parsed = urlparse(str(self.qq_client.onebot_url or "").strip())
        if parsed.scheme not in {"ws", "wss"}:
            raise RuntimeError(self.i18n.t("errors.invalid_onebot_url", default="请先填写合法的 OneBot 地址，必须以 ws:// 或 wss:// 开头"))
        if not parsed.netloc:
            raise RuntimeError(self.i18n.t("errors.invalid_onebot_url", default="请先填写合法的 OneBot 地址，必须以 ws:// 或 wss:// 开头"))
        if not self.qq_client.ws:
            await self.qq_client.connect()
        return {
            "friends": await self.qq_client.get_friend_list(),
            "groups": await self.qq_client.get_group_list(),
            "refreshed_at": int(time.time()),
        }

    async def _build_dashboard_state(self) -> dict[str, Any]:
        login = await self._fetch_login_status_payload()
        settings = dict(self._qq_settings or {})
        self.logger.info(f"[qq_auto_reply debug] build_dashboard_state source settings: {settings}")
        napcat_dir = self._get_napcat_directory()
        return {
            "runtime": self._build_runtime_status(),
            "settings": {
                "onebot_url": settings.get("onebot_url", ""),
                "token": str(settings.get("token") or ""),
                "token_configured": bool(settings.get("token")),
                "token_masked": self._mask_token(str(settings.get("token") or "")),
                "napcat_directory": str(napcat_dir),
                "napcat_directory_exists": napcat_dir.exists(),
                "show_napcat_window": bool(settings.get("show_napcat_window", True)),
                "show_onboarding": bool(settings.get("show_onboarding", True)),
                "guide_step_config_done": bool(settings.get("guide_step_config_done", False)),
                "guide_step_runtime_done": bool(settings.get("guide_step_runtime_done", False)),
                "normal_relay_probability": float(self._normal_relay_probability),
                "truth_reply_probability": float(self._truth_reply_probability),
                "backlog_labels": list(settings.get("backlog_labels") or []),
            },
            "guide": {
                "step_napcat_done": bool(settings.get("napcat_directory")) and bool(self._napcat_process and self._napcat_process.returncode is None),
                "step_service_done": bool(settings.get("onebot_url")) and bool(settings.get("token")),
                "step_contacts_done": bool(self.permission_mgr and self.permission_mgr.list_users()),
                "step_auto_reply_done": bool(settings.get("guide_step_runtime_done", False)) and self._running,
            },
            "business_config": dict(settings),
            "login": login,
            "permissions": {
                "trusted_users": self.permission_mgr.list_users() if self.permission_mgr else [],
                "trusted_groups": self.group_permission_mgr.list_groups() if self.group_permission_mgr else [],
                "guide_step_contacts_done": bool(self.permission_mgr and self.permission_mgr.list_users()),
            },
            "actual": {
                "friends": [],
                "groups": [],
                "refreshed_at": 0,
                "stale": True,
            },
            "backlog_items": list(self._relay_backlog_items),
            "config_ready": await self.config_store.exists(),
            "ui": build_open_ui_payload(plugin_id=self.plugin_id, available=True, i18n=self.i18n),
        }

    @ui.context(id="qq_auto_reply")
    async def get_dashboard_context(self):
        state = await self._build_dashboard_state()
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
    async def open_ui(self, **_):
        return Ok(build_open_ui_payload(plugin_id=self.plugin_id, available=True, i18n=self.i18n))

    @ui.action(label=tr("ui.onboarding.step3.init"), refresh_context=True)
    @plugin_entry(
        id="init_config",
        name=tr("entries.init_config.name", default="新建 QQ 配置"),
        description=tr("entries.init_config.description", default="在第一次使用 QQ 插件、完成引导或缺少配置文件时，创建一份新的 QQ 配置。"),
        input_schema={"type": "object", "properties": {"guide_step_config_done": {"type": "boolean"}}, "additionalProperties": False},
    )
    async def init_config(self, guide_step_config_done: Optional[bool] = None, **_):
        if await self.config_store.exists():
            config = await self._load_business_config()
        else:
            config = await self._create_business_config()
        if guide_step_config_done is not None:
            config["guide_step_config_done"] = bool(guide_step_config_done)
            self._qq_settings = await self.config_store.save(config)
            config = dict(self._qq_settings)
        self.permission_mgr = PermissionManager(config.get("trusted_users", []))
        self.group_permission_mgr = GroupPermissionManager(config.get("trusted_groups", []))
        self._refresh_admin_qq()
        return Ok(await self._build_dashboard_state())

    @plugin_entry(id="get_dashboard_state", name=tr("entries.get_dashboard_state.name", default="获取控制面板状态"), description=tr("entries.get_dashboard_state.description", default="读取 QQ 插件当前的运行状态、登录状态、联系人数量、配置项和引导进度。"), input_schema={"type": "object", "properties": {}})
    async def get_dashboard_state(self, **_):
        return Ok(await self._build_dashboard_state())

    @ui.action(id="refresh_actual_contacts", label=tr("entries.refresh_actual_contacts.name", default="刷新实际联系人列表"), refresh_context=True)
    @plugin_entry(id="refresh_actual_contacts", name=tr("entries.refresh_actual_contacts.name", default="刷新实际联系人列表"), description=tr("entries.refresh_actual_contacts.description", default="重新从 OneBot 拉取 QQ 好友和群聊列表，用于更新联系人显示。"), input_schema={"type": "object", "properties": {}})
    async def refresh_actual_contacts(self, **_):
        try:
            contacts = await self._refresh_actual_contacts_cache()
            payload = await self._build_dashboard_state()
            payload["actual"] = {
                **payload.get("actual", {}),
                **contacts,
                "stale": False,
            }
            payload["business_config"]["trusted_users"] = list(payload.get("permissions", {}).get("trusted_users", []))
            payload["business_config"]["trusted_groups"] = list(payload.get("permissions", {}).get("trusted_groups", []))
            return Ok(payload)
        except RuntimeError as e:
            return Err(SdkError(f"REFRESH_NOT_READY: {self.i18n.t('errors.refresh_not_ready', default='{error}', error=str(e))}"))
        except Exception as e:
            self.logger.error(f"刷新实际联系人列表失败: {e}")
            return Err(SdkError(f"REFRESH_FAILED: {self.i18n.t('errors.refresh_failed', default='{error}', error=str(e))}"))

    @ui.action(id="save_settings", label=tr("entries.save_settings.name", default="保存 QQ 自动回复设置"), refresh_context=True)
    @plugin_entry(id="save_settings", name=tr("entries.save_settings.name", default="保存 QQ 自动回复设置"), description=tr("entries.save_settings.description", default="保存 QQ 插件当前的 OneBot 地址、Token、NapCat 路径、回复概率和 backlog 标签等设置。"), input_schema={"type": "object", "properties": {"onebot_url": {"type": "string"}, "token": {"type": "string"}, "napcat_directory": {"type": "string"}, "show_napcat_window": {"type": "boolean"}, "show_onboarding": {"type": "boolean"}, "guide_step_config_done": {"type": "boolean"}, "guide_step_runtime_done": {"type": "boolean"}, "normal_relay_probability": {"type": "number"}, "truth_reply_probability": {"type": "number"}, "backlog_labels": {"type": "array", "items": {"type": "object"}}}, "additionalProperties": False})
    async def save_settings(self, onebot_url: Optional[str] = None, token: Optional[str] = None, napcat_directory: Optional[str] = None, show_napcat_window: Optional[bool] = None, show_onboarding: Optional[bool] = None, guide_step_config_done: Optional[bool] = None, guide_step_runtime_done: Optional[bool] = None, normal_relay_probability: Optional[float] = None, truth_reply_probability: Optional[float] = None, backlog_labels: Optional[list[dict[str, Any]]] = None, **_):
        if onebot_url is not None:
            self._qq_settings["onebot_url"] = str(onebot_url or "").strip()
        if token is not None:
            self._qq_settings["token"] = str(token or "")
        if napcat_directory is not None:
            self._qq_settings["napcat_directory"] = str(napcat_directory or "").strip()
        if show_napcat_window is not None:
            self._qq_settings["show_napcat_window"] = bool(show_napcat_window)
        if show_onboarding is not None:
            self._qq_settings["show_onboarding"] = bool(show_onboarding)
        if guide_step_config_done is not None:
            self._qq_settings["guide_step_config_done"] = bool(guide_step_config_done)
        if guide_step_runtime_done is not None:
            self._qq_settings["guide_step_runtime_done"] = bool(guide_step_runtime_done)
        if normal_relay_probability is not None:
            value = float(normal_relay_probability)
            if value < 0.0 or value > 1.0:
                return Err(SdkError(f"INVALID_ARGUMENT: {self.i18n.t('errors.invalid_probability', default='normal_relay_probability 必须在 0 到 1 之间')}"))
            self._qq_settings["normal_relay_probability"] = value
            self._normal_relay_probability = value
        if truth_reply_probability is not None:
            value = float(truth_reply_probability)
            if value < 0.0 or value > 1.0:
                return Err(SdkError(f"INVALID_ARGUMENT: {self.i18n.t('errors.invalid_probability', default='truth_reply_probability 必须在 0 到 1 之间')}"))
            self._qq_settings["open_reply_probability"] = value
            self._qq_settings["truth_reply_probability"] = value
            self._truth_reply_probability = value
        if backlog_labels is not None:
            self._qq_settings["backlog_labels"] = self.config_store.normalize_backlog_labels(backlog_labels)
        self._qq_settings.pop("guide_step_settings_done", None)
        success = await self._persist_business_config()
        if self.qq_client:
            self.qq_client.onebot_url = self._qq_settings.get("onebot_url", self.qq_client.onebot_url)
            self.qq_client.token = self._qq_settings.get("token", self.qq_client.token)
        payload = await self._build_dashboard_state()
        payload["persisted"] = success
        payload["reconnect_required"] = bool(self._running)
        payload["business_config"]["trusted_users"] = list(payload.get("permissions", {}).get("trusted_users", []))
        payload["business_config"]["trusted_groups"] = list(payload.get("permissions", {}).get("trusted_groups", []))
        self.logger.info(f"[qq_auto_reply debug] save_settings result payload: {payload}")
        return Ok(payload)

    @ui.action(id="add_trusted_user", label=tr("entries.add_trusted_user.name", default="添加信任用户"), refresh_context=True)
    @plugin_entry(id="add_trusted_user", name=tr("entries.add_trusted_user.name", default="添加信任用户"), description=tr("entries.add_trusted_user.description", default="把一个 QQ 号加入信任用户列表，并可设置权限、昵称和转发概率。"), input_schema={"type": "object", "properties": {"qq_number": {"type": "string"}, "level": {"type": "string", "default": "trusted"}, "nickname": {"type": "string", "default": ""}, "normal_relay_probability": {"type": "number"}}, "required": ["qq_number"]})
    async def add_trusted_user(self, qq_number: str, level: str = "trusted", nickname: str = "", normal_relay_probability: Optional[float] = None, **_):
        if not self.permission_mgr:
            return Err(SdkError(f"NOT_INITIALIZED: {self.i18n.t('errors.permission_manager_not_initialized', default='权限管理器未初始化')}"))
        normalized_nickname = "" if level == "admin" else nickname
        if normal_relay_probability is not None:
            value = float(normal_relay_probability)
            if value < 0.0 or value > 1.0:
                return Err(SdkError(f"INVALID_ARGUMENT: {self.i18n.t('errors.invalid_probability', default='normal_relay_probability 必须在 0 到 1 之间')}"))
        self.permission_mgr.add_user(qq_number, level, normalized_nickname, normal_relay_probability=normal_relay_probability)
        self._refresh_admin_qq()
        await self._invalidate_private_session(qq_number)
        success = await self._persist_business_config()
        payload = await self._build_dashboard_state()
        payload["persisted"] = success
        return Ok(payload)

    @ui.action(id="remove_trusted_user", label=tr("entries.remove_trusted_user.name", default="移除信任用户"), refresh_context=True)
    @plugin_entry(id="remove_trusted_user", name=tr("entries.remove_trusted_user.name", default="移除信任用户"), description=tr("entries.remove_trusted_user.description", default="把一个 QQ 号从信任用户列表中移除，不再按信任用户处理。"), input_schema={"type": "object", "properties": {"qq_number": {"type": "string"}}, "required": ["qq_number"]})
    async def remove_trusted_user(self, qq_number: str, **_):
        if not self.permission_mgr:
            return Err(SdkError(f"NOT_INITIALIZED: {self.i18n.t('errors.permission_manager_not_initialized', default='权限管理器未初始化')}"))
        self.permission_mgr.remove_user(qq_number)
        self._refresh_admin_qq()
        await self._invalidate_private_session(qq_number)
        success = await self._persist_business_config()
        payload = await self._build_dashboard_state()
        payload["persisted"] = success
        return Ok(payload)

    @ui.action(id="set_user_nickname", label=tr("entries.set_user_nickname.name", default="设置用户昵称"), refresh_context=True)
    @plugin_entry(id="set_user_nickname", name=tr("entries.set_user_nickname.name", default="设置用户昵称"), description=tr("entries.set_user_nickname.description", default="修改这个信任用户在回复里显示的昵称或称呼。"), input_schema={"type": "object", "properties": {"qq_number": {"type": "string"}, "nickname": {"type": "string", "default": ""}}, "required": ["qq_number"]})
    async def set_user_nickname(self, qq_number: str, nickname: str = "", **_):
        if not self.permission_mgr:
            return Err(SdkError(f"NOT_INITIALIZED: {self.i18n.t('errors.permission_manager_not_initialized', default='权限管理器未初始化')}"))
        permission_level = self.permission_mgr.get_permission_level(qq_number)
        if permission_level == "none":
            return Err(SdkError(f"USER_NOT_FOUND: {self.i18n.t('errors.user_not_found', default='用户 {qq_number} 不在信任列表中', qq_number=qq_number)}"))
        if permission_level == "admin":
            return Err(SdkError(f"ADMIN_NO_NICKNAME: {self.i18n.t('errors.admin_no_nickname', default='管理员始终被称为主人，无法设置昵称')}"))
        success = self.permission_mgr.set_nickname(qq_number, nickname)
        if not success:
            return Err(SdkError(f"SET_FAILED: {self.i18n.t('errors.set_nickname_failed', default='设置昵称失败')}"))
        persisted = await self._persist_business_config()
        payload = await self._build_dashboard_state()
        payload["persisted"] = persisted
        return Ok(payload)

    @ui.action(id="add_trusted_group", label=tr("entries.add_trusted_group.name", default="添加信任群聊"), refresh_context=True)
    @plugin_entry(id="add_trusted_group", name=tr("entries.add_trusted_group.name", default="添加信任群聊"), description=tr("entries.add_trusted_group.description", default="把一个 QQ 群加入信任群聊列表，并可设置群等级和回复概率。"), input_schema={"type": "object", "properties": {"group_id": {"type": "string"}, "level": {"type": "string", "default": "normal"}, "normal_relay_probability": {"type": "number"}, "open_reply_probability": {"type": "number"}}, "required": ["group_id"]})
    async def add_trusted_group(self, group_id: str, level: str = "normal", normal_relay_probability: Optional[float] = None, open_reply_probability: Optional[float] = None, **_):
        if not self.group_permission_mgr:
            return Err(SdkError(f"NOT_INITIALIZED: {self.i18n.t('errors.group_permission_manager_not_initialized', default='群聊权限管理器未初始化')}"))
        if normal_relay_probability is not None:
            value = float(normal_relay_probability)
            if value < 0.0 or value > 1.0:
                return Err(SdkError(f"INVALID_ARGUMENT: {self.i18n.t('errors.invalid_probability', default='normal_relay_probability 必须在 0 到 1 之间')}"))
        if open_reply_probability is not None:
            value = float(open_reply_probability)
            if value < 0.0 or value > 1.0:
                return Err(SdkError(f"INVALID_ARGUMENT: {self.i18n.t('errors.invalid_probability', default='open_reply_probability 必须在 0 到 1 之间')}"))
        self.group_permission_mgr.add_group(group_id, level, normal_relay_probability=normal_relay_probability, open_reply_probability=open_reply_probability)
        await self.backlog_store.ensure_group_placeholder(group_id, group_display_name=f"QQ群 {group_id}")
        success = await self._persist_business_config()
        payload = await self._build_dashboard_state()
        payload["persisted"] = success
        return Ok(payload)

    @ui.action(id="remove_trusted_group", label=tr("entries.remove_trusted_group.name", default="移除信任群聊"), refresh_context=True)
    @plugin_entry(id="remove_trusted_group", name=tr("entries.remove_trusted_group.name", default="移除信任群聊"), description=tr("entries.remove_trusted_group.description", default="把一个 QQ 群从信任群聊列表中移除，不再按信任群聊处理。"), input_schema={"type": "object", "properties": {"group_id": {"type": "string"}}, "required": ["group_id"]})
    async def remove_trusted_group(self, group_id: str, **_):
        if not self.group_permission_mgr:
            return Err(SdkError(f"NOT_INITIALIZED: {self.i18n.t('errors.group_permission_manager_not_initialized', default='群聊权限管理器未初始化')}"))
        self.group_permission_mgr.remove_group(group_id)
        await self.backlog_store.remove_group_placeholder(group_id)
        success = await self._persist_business_config()
        payload = await self._build_dashboard_state()
        payload["persisted"] = success
        return Ok(payload)

    @plugin_entry(id="send_backlog_reply_direct", name=tr("entries.send_backlog_reply_direct.name", default="发送这条回复"), description=tr("entries.send_backlog_reply_direct.description", default="把你填写的内容直接回复到这条 QQ 消息，并在发送后把对应群聊标记为已处理。"), input_schema={"type": "object", "properties": {"source_type": {"type": "string"}, "target_id": {"type": "string"}, "sender_id": {"type": "string"}, "message_id": {"type": "string"}, "original_message": {"type": "string"}, "reply_text": {"type": "string"}}, "required": ["source_type", "target_id", "original_message", "reply_text"], "additionalProperties": False})
    async def send_backlog_reply_direct(self, source_type: str, target_id: str, original_message: str, reply_text: str, sender_id: str = "", message_id: str = "", **_):
        try:
            self._ensure_qq_client_connected()
            normalized_source_type = str(source_type or "").strip().lower()
            normalized_target_id = str(target_id or "").strip()
            normalized_original_message = self._validate_outbound_message(original_message)
            normalized_reply_text = self._validate_outbound_message(reply_text)
            normalized_message_id = str(message_id or "").strip()
            if normalized_source_type not in {"group", "private"}:
                return Err(SdkError("INVALID_SOURCE_TYPE: source_type 必须是 group 或 private"))
            if not normalized_target_id:
                return Err(SdkError("INVALID_TARGET: target_id 不能为空"))
            if normalized_source_type == "group":
                segments = []
                if normalized_message_id:
                    segments.append({"type": "reply", "data": {"id": normalized_message_id}})
                if sender_id:
                    segments.append({"type": "at", "data": {"qq": str(sender_id)}})
                segments.append({"type": "text", "data": {"text": f" {normalized_reply_text}" if sender_id else normalized_reply_text}})
                await self.qq_client.send_group_message_segments(normalized_target_id, segments)
                self._relay_backlog_items = [
                    item for item in self._relay_backlog_items
                    if not (
                        str(item.get("source_type") or "") == normalized_source_type
                        and str(item.get("target_id") or "") == normalized_target_id
                        and str(item.get("sender_id") or "") == str(sender_id or "")
                        and str(item.get("original_message") or "") == normalized_original_message
                    )
                ]
                await self.backlog_store.mark_group_reviewed(normalized_target_id)
            else:
                await self.qq_client.send_message(normalized_target_id, normalized_reply_text)
            self._relay_backlog_items = [
                item for item in self._relay_backlog_items
                if not (
                    str(item.get("source_type") or "") == normalized_source_type
                    and str(item.get("target_id") or "") == normalized_target_id
                    and str(item.get("sender_id") or "") == str(sender_id or "")
                    and str(item.get("original_message") or "") == normalized_original_message
                )
            ]
            return Ok({"status": "sent", "source_type": normalized_source_type, "target_id": normalized_target_id})
        except QQAutoReplyValidationError as e:
            return Err(SdkError(f"INVALID_ARGUMENT: {str(e)}"))
        except RuntimeError as e:
            return Err(SdkError(f"NOT_READY: {self.i18n.t('errors.proactive_not_ready', default='{error}', error=str(e))}"))
        except Exception as e:
            self.logger.exception("Failed to send direct backlog reply")
            return Err(SdkError(f"SEND_FAILED: {self.i18n.t('errors.proactive_send_failed', default='{error}', error=str(e))}"))

    @plugin_entry(id="sync_qrcode", name=tr("entries.sync_qrcode.name", default="刷新登录二维码"), description=tr("entries.sync_qrcode.description", default="重新读取 NapCat 当前生成的 QQ 登录二维码，并更新到插件界面。"), input_schema={"type": "object", "properties": {}})
    async def sync_qrcode(self, **_):
        await self._sync_napcat_qrcode_into_static()
        return Ok(await self._build_dashboard_state())

    @plugin_entry(id="start_auto_reply", name=tr("entries.start_auto_reply.name", default="开始自动回复"), description=tr("entries.start_auto_reply.description", default="开始监听 QQ 消息，并按当前配置自动回复或转发。"), input_schema={"type": "object", "properties": {}})
    async def start_auto_reply(self, **_):
        if self._running:
            return Ok({"status": "already_running"})
        if not self.qq_client:
            return Err(SdkError(f"NOT_INITIALIZED: {self.i18n.t('errors.qq_client_not_initialized', default='QQ 客户端未初始化')}"))
        try:
            await self.qq_client.connect()
            self._running = True
            self._message_task = asyncio.create_task(self._process_messages())
            return Ok({"status": "started"})
        except Exception as e:
            self.logger.exception("Failed to start auto reply")
            return Err(SdkError(f"START_ERROR: {self.i18n.t('errors.start_connect_failed', default='无法连接到 OneBot 服务 {url}，请先启动外部 NapCat/OneBot: {error}', url=self.qq_client.onebot_url, error=str(e))}"))

    @plugin_entry(id="stop_auto_reply", name=tr("entries.stop_auto_reply.name", default="停止自动回复"), description=tr("entries.stop_auto_reply.description", default="停止监听 QQ 消息，不再继续自动回复或转发。"), input_schema={"type": "object", "properties": {}})
    async def stop_auto_reply(self, **_):
        if not self._running and not self._message_task:
            return Ok({"status": "not_running"})
        await self._stop_auto_reply_runtime(stop_napcat=False)
        return Ok({"status": "stopped"})

    @plugin_entry(id="send_private_proactive_message", name=tr("entries.send_private_proactive_message.name", default="发送私聊消息"), description=tr("entries.send_private_proactive_message.description", default="根据你提供的内容生成一条新的 QQ 私聊消息，并直接发送给指定用户。"), input_schema={"type": "object", "properties": {"target": {"type": "string"}, "message": {"type": "string"}}, "required": ["target", "message"], "additionalProperties": False}, metadata={"timeout": 90})
    async def send_private_proactive_message(self, target: str, message: str, **_):
        try:
            self._ensure_qq_client_connected()
            resolved_qq, matched_nickname = self._resolve_private_message_target(target)
            prompt_message = self._validate_outbound_message(message)
            permission_level = "admin" if resolved_qq == self._admin_qq else (self.permission_mgr.get_permission_level(resolved_qq) if self.permission_mgr else "trusted")
            if permission_level == "none":
                permission_level = "trusted"
            reply_text = await self._generate_reply(
                prompt_message,
                permission_level,
                resolved_qq,
                is_group=False,
                user_nickname=matched_nickname,
                use_memory_context=permission_level == "admin",
                persist_memory=False,
                ephemeral_session=True,
            )
            if not reply_text:
                return Err(SdkError(f"GENERATE_FAILED: {self.i18n.t('errors.proactive_private_generate_failed', default='AI 未生成可发送的私聊内容')}"))
            await self.qq_client.send_message(resolved_qq, reply_text)
            return Ok({
                "status": "sent",
                "target": str(target or "").strip(),
                "resolved_qq": resolved_qq,
                "resolved_nickname": matched_nickname,
                "message_prompt": prompt_message,
                "generated_message": reply_text,
            })
        except QQAutoReplyValidationError as e:
            code = e.code
            message_text = str(e)
            if code in ("NICKNAME_NOT_FOUND", "NICKNAME_AMBIGUOUS"):
                return Err(SdkError(f"{code}: {message_text}"))
            if code == "INVALID_TARGET":
                return Err(SdkError(f"INVALID_TARGET: {self.i18n.t('errors.proactive_invalid_target', default=message_text)}"))
            if code == "INVALID_MESSAGE":
                return Err(SdkError(f"INVALID_MESSAGE: {self.i18n.t('errors.proactive_invalid_message', default=message_text)}"))
            return Err(SdkError(f"INVALID_TARGET: {message_text}"))
        except RuntimeError as e:
            return Err(SdkError(f"NOT_READY: {self.i18n.t('errors.proactive_not_ready', default='{error}', error=str(e))}"))
        except Exception as e:
            self.logger.exception("Failed to send proactive private QQ message")
            return Err(SdkError(f"SEND_FAILED: {self.i18n.t('errors.proactive_send_failed', default='{error}', error=str(e))}"))

    @plugin_entry(id="send_group_proactive_message", name=tr("entries.send_group_proactive_message.name", default="发送群消息"), description=tr("entries.send_group_proactive_message.description", default="根据你提供的内容生成一条新的 QQ 群消息，并直接发送到指定群聊。"), input_schema={"type": "object", "properties": {"group_id": {"type": "string"}, "message": {"type": "string"}}, "required": ["group_id", "message"], "additionalProperties": False}, metadata={"timeout": 90})
    async def send_group_proactive_message(self, group_id: str, message: str, **_):
        try:
            self._ensure_qq_client_connected()
            normalized_group_id = self._validate_group_id(group_id)
            prompt_message = self._validate_outbound_message(message)
            reply_text = await self._generate_reply(
                prompt_message,
                "open",
                self._admin_qq or "0",
                is_group=True,
                group_id=normalized_group_id,
                use_memory_context=False,
                persist_memory=False,
                ephemeral_session=True,
                group_facing=True,
            )
            if not reply_text:
                return Err(SdkError(f"GENERATE_FAILED: {self.i18n.t('errors.proactive_group_generate_failed', default='AI 未生成可发送的群聊内容')}"))
            await self.qq_client.send_group_message(normalized_group_id, reply_text)
            return Ok({
                "status": "sent",
                "group_id": normalized_group_id,
                "message_prompt": prompt_message,
                "generated_message": reply_text,
            })
        except QQAutoReplyValidationError as e:
            code = e.code
            message_text = str(e)
            if code == "INVALID_GROUP_ID":
                return Err(SdkError(f"INVALID_GROUP_ID: {self.i18n.t('errors.proactive_invalid_group_id', default=message_text)}"))
            if code == "INVALID_MESSAGE":
                return Err(SdkError(f"INVALID_MESSAGE: {self.i18n.t('errors.proactive_invalid_message', default=message_text)}"))
            return Err(SdkError(f"INVALID_GROUP_ID: {message_text}"))
        except RuntimeError as e:
            return Err(SdkError(f"NOT_READY: {self.i18n.t('errors.proactive_not_ready', default='{error}', error=str(e))}"))
        except Exception as e:
            self.logger.exception("Failed to send proactive group QQ message")
            return Err(SdkError(f"SEND_FAILED: {self.i18n.t('errors.proactive_send_failed', default='{error}', error=str(e))}"))

    async def _stop_auto_reply_runtime(self, *, stop_napcat: bool):
        self._running = False
        if self._message_task:
            self._message_task.cancel()
            try:
                await self._message_task
            except asyncio.CancelledError:
                pass
            self._message_task = None
        if self._handler_tasks:
            handler_tasks = list(self._handler_tasks)
            for task in handler_tasks:
                task.cancel()
            try:
                await asyncio.wait_for(asyncio.gather(*handler_tasks, return_exceptions=True), timeout=self._handler_shutdown_timeout_seconds)
            except asyncio.TimeoutError:
                self.logger.warning(f"Timed out waiting for {len(handler_tasks)} message handler tasks to stop")
            self._handler_tasks.clear()
        if self.qq_client:
            await self.qq_client.disconnect()
        if stop_napcat:
            await self._stop_managed_napcat()
        self._session_locks.clear()

    def _track_handler_task(self, task: asyncio.Task) -> None:
        self._handler_tasks.add(task)
        task.add_done_callback(self._on_handler_task_done)

    def _on_handler_task_done(self, task: asyncio.Task) -> None:
        self._handler_tasks.discard(task)
        try:
            task.result()
        except asyncio.CancelledError:
            pass
        except Exception as exc:
            self.logger.error(f"Message handler task failed: {exc}")

    async def _record_backlog_message(self, message: Dict[str, Any]) -> None:
        message_type = str(message.get("message_type") or "").strip()
        sender_id = str(message.get("user_id") or "").strip()
        if not sender_id:
            return
        message_text = self._sanitize_message_text(str(message.get("content") or "").strip())
        if not message_text:
            return
        sender_name = str(message.get("user_nickname") or sender_id).strip() or sender_id
        message_id = str(message.get("message_id") or "")
        timestamp = int(message.get("timestamp") or 0)
        backlog_labels = list((self._qq_settings or {}).get("backlog_labels") or [])
        category = QQFeedbackClassifier.classify(message_text, backlog_labels)

        if message_type == "private":
            permission_level = self.permission_mgr.get_permission_level(sender_id) if self.permission_mgr else "none"
            if permission_level == "none":
                return
            conversation_key = self._build_session_key(sender_id=sender_id, is_group=False)
            backlog_message = QQBacklogMessage(
                conversation_key=conversation_key,
                conversation_type="private",
                source_id=sender_id,
                sender_id=sender_id,
                sender_name=sender_name,
                text=message_text,
                message_id=message_id,
                timestamp=timestamp,
                permission_level=permission_level,
                category=category,
                raw=dict(message.get("raw") or {}),
            )
            display_name = self.permission_mgr.get_nickname(sender_id) if self.permission_mgr else None
            await self.backlog_store.append_message(
                backlog_message,
                conversation_display_name=display_name or sender_name or sender_id,
            )
            return

        if message_type != "group":
            return
        group_id = str(message.get("group_id") or "").strip()
        if not group_id:
            return
        group_level = self.group_permission_mgr.get_group_level(group_id) if self.group_permission_mgr else "none"
        if group_level == "none":
            return
        conversation_key = self._build_session_key(sender_id=sender_id, is_group=True, group_id=group_id)
        backlog_message = QQBacklogMessage(
            conversation_key=conversation_key,
            conversation_type="group",
            source_id=group_id,
            sender_id=sender_id,
            sender_name=sender_name,
            text=message_text,
            message_id=message_id,
            timestamp=timestamp,
            group_id=group_id,
            group_level=group_level,
            is_at_bot=bool(message.get("is_at_bot")),
            category=category,
            raw=dict(message.get("raw") or {}),
        )
        display_name = self.permission_mgr.get_nickname(sender_id) if self.permission_mgr else None
        await self.backlog_store.append_message(
            backlog_message,
            conversation_display_name=display_name or sender_name or sender_id,
            group_display_name=f"QQ群 {group_id}",
        )

    @plugin_entry(id="get_backlog_summary", name=tr("entries.get_backlog_summary.name", default="读取待审阅摘要"), description=tr("entries.get_backlog_summary.description", default="查看当前哪些群还有待处理消息，以及每个群的大致积压情况。"), input_schema={"type": "object", "properties": {}})
    async def get_backlog_summary(self, **_):
        state = await self.backlog_store.load()
        label_defs = list((self._qq_settings or {}).get("backlog_labels") or [])
        label_map = {str(item.get("id") or "").strip(): str(item.get("label") or item.get("id") or "").strip() for item in label_defs if isinstance(item, dict) and str(item.get("id") or "").strip()}
        configured_groups = self.group_permission_mgr.list_groups() if self.group_permission_mgr else []
        summaries = QQSummaryBuilder.build_all_group_summaries(state, label_map=label_map, configured_groups=configured_groups)
        label_counts: dict[str, int] = {}
        for item in summaries:
            for label_id, count in dict(item.get("label_counts") or {}).items():
                normalized_label_id = str(label_id or "").strip()
                if not normalized_label_id:
                    continue
                label_counts[normalized_label_id] = label_counts.get(normalized_label_id, 0) + int(count or 0)
        return Ok({
            "groups": summaries,
            "group_count": len(summaries),
            "unread_count": sum(int(item.get("unread_count") or 0) for item in summaries),
            "label_counts": label_counts,
            "labels": [{
                "id": str(item.get("id") or "").strip(),
                "label": str(item.get("label") or item.get("id") or "").strip(),
                "priority": int(item.get("priority") or 0),
            } for item in label_defs if isinstance(item, dict) and str(item.get("id") or "").strip()],
        })

    @plugin_entry(id="get_group_backlog_detail", name=tr("entries.get_group_backlog_detail.name", default="读取群聊待审阅详情"), description=tr("entries.get_group_backlog_detail.description", default="查看这个群当前每条待处理消息的详细内容，方便逐条回复或处理。"), input_schema={"type": "object", "properties": {"group_id": {"type": "string"}}, "required": ["group_id"]})
    async def get_group_backlog_detail(self, group_id: str, **_):
        normalized_group_id = self._validate_group_id(group_id)
        detail = await self.backlog_store.get_group_detail(normalized_group_id)
        label_defs = list((self._qq_settings or {}).get("backlog_labels") or [])
        detail["labels"] = [{
            "id": str(item.get("id") or "").strip(),
            "label": str(item.get("label") or item.get("id") or "").strip(),
            "priority": int(item.get("priority") or 0),
        } for item in label_defs if isinstance(item, dict) and str(item.get("id") or "").strip()]
        return Ok(detail)

    @plugin_entry(id="mark_group_backlog_reviewed", name=tr("entries.mark_group_backlog_reviewed.name", default="标记群聊已处理"), description=tr("entries.mark_group_backlog_reviewed.description", default="把这个群当前所有待处理消息标记为已处理，不再继续显示为未审阅。"), input_schema={"type": "object", "properties": {"group_id": {"type": "string"}}, "required": ["group_id"]})
    async def mark_group_backlog_reviewed(self, group_id: str, **_):
        normalized_group_id = self._validate_group_id(group_id)
        state = await self.backlog_store.mark_group_reviewed(normalized_group_id)
        configured_groups = self.group_permission_mgr.list_groups() if self.group_permission_mgr else []
        summaries = QQSummaryBuilder.build_all_group_summaries(state, configured_groups=configured_groups)
        return Ok({
            "status": "reviewed",
            "group_id": normalized_group_id,
            "groups": summaries,
        })

    async def _maybe_notify_backlog_summary(self, *, group_id: str) -> None:
        if not self._admin_qq:
            return
        state = await self.backlog_store.load()
        groups = dict(state.get("groups") or {})
        conversations = dict(state.get("conversations") or {})
        group = groups.get(group_id)
        if not isinstance(group, dict):
            return
        unread_count = int(group.get("unread_count") or 0)
        label_counts = dict(group.get("label_counts") or {})
        label_map = {
            str(item.get("id") or "").strip(): str(item.get("label") or item.get("id") or "").strip()
            for item in list((self._qq_settings or {}).get("backlog_labels") or [])
            if isinstance(item, dict) and str(item.get("id") or "").strip()
        }
        issue_count = sum(
            int(count or 0)
            for label_id, count in label_counts.items()
            if str(label_id or "").strip() and str(label_id or "").strip() != "chat"
        )
        if unread_count < self._backlog_summary_threshold and issue_count < self._backlog_issue_notify_threshold:
            return
        last_notified_at = int(group.get("last_notified_at") or 0)
        now = int(time.time())
        if now - last_notified_at < self._backlog_notify_cooldown_seconds:
            return
        summary = QQSummaryBuilder.build_group_summary(group, conversations, label_map=label_map)
        highlights = list(summary.get("highlights") or [])[:3]
        highlight_text = "；".join(highlights) if highlights else "暂无具体摘要"
        label_summary_parts = [
            f"{label_map.get(label_id, label_id)} {int(count or 0)} 条"
            for label_id, count in label_counts.items()
            if int(count or 0) > 0
        ]
        label_summary_text = "，".join(label_summary_parts) if label_summary_parts else "已分类消息 0 条"
        notify_text = (
            f"[QQ backlog 提醒] {summary.get('display_name') or ('QQ群 ' + group_id)}："
            f"未审阅 {unread_count} 条，{label_summary_text}。"
            f"重点：{highlight_text}"
        )
        self.push_message(
            visibility=[],
            ai_behavior="respond",
            parts=[{"type": "text", "text": notify_text}],
            source=self.plugin_id,
            metadata={
                "delivery_semantics": "passive",
                "kind": "qq_backlog_summary",
                "group_id": group_id,
                "unread_count": unread_count,
                "label_counts": label_counts,
            },
        )
        await self.backlog_store.update_group_last_notified_at(group_id, now)

    async def _process_messages(self):
        while self._running:
            try:
                message = await self.qq_client.receive_message()
                if message:
                    task = asyncio.create_task(self._run_message_handler(message))
                    self._track_handler_task(task)
            except asyncio.CancelledError:
                break
            except Exception as e:
                self.logger.error(f"Error processing message: {e}")
                await asyncio.sleep(1)

    async def _handle_message(self, message: Dict[str, Any]):
        await self._record_backlog_message(message)
        message_type = message.get("message_type")
        sender_id = str(message.get("user_id") or "").strip()
        message_text = message.get("content", "")
        user_nickname = message.get("user_nickname")
        if message_type == "private":
            session_key = self._build_session_key(sender_id=sender_id, is_group=False)
            if session_key in self._user_sessions:
                self._user_sessions[session_key]["last_activity_at"] = time.time()
            await self._handle_private_message(sender_id, message_text, user_nickname)
        elif message_type == "group":
            group_id = str(message.get("group_id") or "").strip()
            is_at_bot = message.get("is_at_bot", False)
            session_key = self._build_session_key(sender_id=sender_id, is_group=True, group_id=group_id)
            if session_key in self._user_sessions:
                self._user_sessions[session_key]["last_activity_at"] = time.time()
            await self._handle_group_message(group_id, sender_id, message_text, is_at_bot, user_nickname)
            await self._maybe_notify_backlog_summary(group_id=group_id)

    async def _handle_private_message(self, sender_id: str, message_text: str, user_nickname: Optional[str] = None):
        permission_level = self.permission_mgr.get_permission_level(sender_id)
        if permission_level == "none":
            return
        if permission_level == "normal":
            relay_probability = self.permission_mgr.get_normal_relay_probability(sender_id) if self.permission_mgr else None
            await self._handle_normal_relay(message_text, sender_id, source_type="private", source_id=sender_id, relay_probability=relay_probability)
            return
        reply_text = await self._generate_reply(message_text, permission_level, sender_id, is_group=False, user_nickname=user_nickname)
        if reply_text:
            await self.qq_client.send_message(sender_id, reply_text)

    async def _handle_group_message(self, group_id: str, sender_id: str, message_text: str, is_at_bot: bool, user_nickname: Optional[str] = None):
        group_level = self.group_permission_mgr.get_group_level(group_id)
        if group_level == "none":
            return
        if group_level == "normal":
            relay_probability = self.group_permission_mgr.get_normal_relay_probability(group_id) if self.group_permission_mgr else None
            await self._handle_normal_relay(message_text, sender_id, source_type="group", source_id=group_id, relay_probability=relay_probability)
            return
        if group_level == "trusted" and not is_at_bot:
            return
        if group_level == "open" and not is_at_bot:
            reply_probability = self.group_permission_mgr.get_open_reply_probability(group_id) if self.group_permission_mgr else None
            effective_reply_probability = self._truth_reply_probability if reply_probability is None else reply_probability
            if effective_reply_probability <= 0.0 or random.random() >= effective_reply_probability:
                return
        reply_text = await self._generate_reply(message_text, group_level, sender_id, is_group=True, group_id=group_id, user_nickname=user_nickname)
        if reply_text:
            await self.qq_client.send_group_message(group_id, reply_text)

    @staticmethod
    def _sanitize_message_text(text: str) -> str:
        import re
        text = re.sub(r"\[CQ:at,qq=all\]", "@全体成员", text)
        text = re.sub(r"\[CQ:at,qq=(\d+)\]", r"@用户\1", text)
        return text

    async def _handle_normal_relay(self, message_text: str, sender_id: str, source_type: str, source_id: str, relay_probability: Optional[float] = None):
        if not self.qq_client or not self._admin_qq or sender_id == self._admin_qq:
            return None
        effective_probability = self._normal_relay_probability if relay_probability is None else float(relay_probability)
        if effective_probability <= 0.0 or random.random() >= effective_probability:
            return None
        message_text = self._sanitize_message_text(message_text)
        if source_type == "group":
            relay_text = f"[QQ群转发] 群 {source_id} / 用户 {sender_id}: {message_text}"
        else:
            relay_text = f"[QQ私聊转发] 来自 {sender_id}: {message_text}"
        self._relay_backlog_items = ([{
            "id": f"{source_type}:{source_id}:{sender_id}:{int(time.time() * 1000)}",
            "source_type": source_type,
            "target_id": str(source_id or ""),
            "sender_id": str(sender_id or ""),
            "target_label": f"QQ群 {source_id}" if source_type == "group" else f"私聊 {source_id}",
            "original_message": message_text,
            "relay_preview": relay_text,
            "timestamp": int(time.time()),
        }] + list(self._relay_backlog_items))[:50]
        await self.qq_client.send_message(self._admin_qq, relay_text)
        return None

    async def _run_message_handler(self, message: Dict[str, Any]) -> None:
        session_key = self._message_session_key(message)
        async with self._message_concurrency:
            if not session_key:
                await self._handle_message(message)
                return
            async def _handle_current_message() -> None:
                await self._handle_message(message)
            await self._run_with_session_lock(session_key, _handle_current_message)
