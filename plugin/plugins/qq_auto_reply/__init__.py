
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

from .config_store import QQAutoReplyConfigStore
from .group_permission import GroupPermissionManager
from .permission import PermissionManager
from .prompting import QQAutoReplyPromptingMixin
from .qq_client import QQClient
from .session import QQAutoReplySessionMixin
from .targets import QQAutoReplyTargetsMixin


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
                "guide_step_settings_done": bool(settings.get("onebot_url")) and bool(settings.get("token")),
                "guide_step_runtime_done": bool(settings.get("guide_step_runtime_done", False)),
                "normal_relay_probability": float(self._normal_relay_probability),
                "truth_reply_probability": float(self._truth_reply_probability),
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
        name=tr("entries.init_config.name", default="初始化 QQ 配置"),
        description=tr("entries.init_config.description", default="完成引导后创建空的 QQ JSON 配置。"),
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

    @plugin_entry(id="qq_auto_reply_get_dashboard_state", name=tr("entries.get_dashboard_state.name", default="获取控制面板状态"), description=tr("entries.get_dashboard_state.description", default="返回 QQ 自动回复前端控制面板所需的完整状态。"), input_schema={"type": "object", "properties": {}})
    async def get_dashboard_state(self, **_):
        return Ok(await self._build_dashboard_state())

    @ui.action(id="refresh_actual_contacts", label=tr("entries.refresh_actual_contacts.name", default="刷新实际联系人列表"), refresh_context=True)
    @plugin_entry(id="refresh_actual_contacts", name=tr("entries.refresh_actual_contacts.name", default="刷新实际联系人列表"), description=tr("entries.refresh_actual_contacts.description", default="从 OneBot 拉取好友列表和群聊列表并刷新缓存。"), input_schema={"type": "object", "properties": {}})
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
    @plugin_entry(id="save_settings", name=tr("entries.save_settings.name", default="保存 QQ 自动回复设置"), description=tr("entries.save_settings.description", default="保存 OneBot、NapCat 与 UI 相关设置到 JSON 配置。"), input_schema={"type": "object", "properties": {"onebot_url": {"type": "string"}, "token": {"type": "string"}, "napcat_directory": {"type": "string"}, "show_napcat_window": {"type": "boolean"}, "show_onboarding": {"type": "boolean"}, "guide_step_config_done": {"type": "boolean"}, "guide_step_settings_done": {"type": "boolean"}, "guide_step_runtime_done": {"type": "boolean"}, "normal_relay_probability": {"type": "number"}, "truth_reply_probability": {"type": "number"}}, "additionalProperties": False})
    async def save_settings(self, onebot_url: Optional[str] = None, token: Optional[str] = None, napcat_directory: Optional[str] = None, show_napcat_window: Optional[bool] = None, show_onboarding: Optional[bool] = None, guide_step_config_done: Optional[bool] = None, guide_step_settings_done: Optional[bool] = None, guide_step_runtime_done: Optional[bool] = None, normal_relay_probability: Optional[float] = None, truth_reply_probability: Optional[float] = None, **_):
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
        if guide_step_settings_done is not None:
            self._qq_settings["guide_step_settings_done"] = bool(guide_step_settings_done)
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
    @plugin_entry(id="add_trusted_user", name=tr("entries.add_trusted_user.name", default="添加信任用户"), description=tr("entries.add_trusted_user.description", default="添加一个信任的 QQ 号到白名单。"), input_schema={"type": "object", "properties": {"qq_number": {"type": "string"}, "level": {"type": "string", "default": "trusted"}, "nickname": {"type": "string", "default": ""}}, "required": ["qq_number"]})
    async def add_trusted_user(self, qq_number: str, level: str = "trusted", nickname: str = "", **_):
        if not self.permission_mgr:
            return Err(SdkError(f"NOT_INITIALIZED: {self.i18n.t('errors.permission_manager_not_initialized', default='权限管理器未初始化')}"))
        normalized_nickname = "" if level == "admin" else nickname
        self.permission_mgr.add_user(qq_number, level, normalized_nickname)
        self._refresh_admin_qq()
        await self._invalidate_private_session(qq_number)
        success = await self._persist_business_config()
        payload = await self._build_dashboard_state()
        payload["persisted"] = success
        return Ok(payload)

    @ui.action(id="remove_trusted_user", label=tr("entries.remove_trusted_user.name", default="移除信任用户"), refresh_context=True)
    @plugin_entry(id="remove_trusted_user", name=tr("entries.remove_trusted_user.name", default="移除信任用户"), description=tr("entries.remove_trusted_user.description", default="从白名单中移除一个 QQ 号。"), input_schema={"type": "object", "properties": {"qq_number": {"type": "string"}}, "required": ["qq_number"]})
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
    @plugin_entry(id="set_user_nickname", name=tr("entries.set_user_nickname.name", default="设置用户昵称"), description=tr("entries.set_user_nickname.description", default="为信任用户设置专属称呼。"), input_schema={"type": "object", "properties": {"qq_number": {"type": "string"}, "nickname": {"type": "string", "default": ""}}, "required": ["qq_number"]})
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
    @plugin_entry(id="add_trusted_group", name=tr("entries.add_trusted_group.name", default="添加信任群聊"), description=tr("entries.add_trusted_group.description", default="添加一个信任的 QQ 群到白名单。"), input_schema={"type": "object", "properties": {"group_id": {"type": "string"}, "level": {"type": "string", "default": "normal"}}, "required": ["group_id"]})
    async def add_trusted_group(self, group_id: str, level: str = "normal", **_):
        if not self.group_permission_mgr:
            return Err(SdkError(f"NOT_INITIALIZED: {self.i18n.t('errors.group_permission_manager_not_initialized', default='群聊权限管理器未初始化')}"))
        self.group_permission_mgr.add_group(group_id, level)
        success = await self._persist_business_config()
        payload = await self._build_dashboard_state()
        payload["persisted"] = success
        return Ok(payload)

    @ui.action(id="remove_trusted_group", label=tr("entries.remove_trusted_group.name", default="移除信任群聊"), refresh_context=True)
    @plugin_entry(id="remove_trusted_group", name=tr("entries.remove_trusted_group.name", default="移除信任群聊"), description=tr("entries.remove_trusted_group.description", default="从白名单中移除一个 QQ 群。"), input_schema={"type": "object", "properties": {"group_id": {"type": "string"}}, "required": ["group_id"]})
    async def remove_trusted_group(self, group_id: str, **_):
        if not self.group_permission_mgr:
            return Err(SdkError(f"NOT_INITIALIZED: {self.i18n.t('errors.group_permission_manager_not_initialized', default='群聊权限管理器未初始化')}"))
        self.group_permission_mgr.remove_group(group_id)
        success = await self._persist_business_config()
        payload = await self._build_dashboard_state()
        payload["persisted"] = success
        return Ok(payload)

    @plugin_entry(id="qq_auto_reply_sync_qrcode", name=tr("entries.sync_qrcode.name", default="刷新二维码"), description=tr("entries.sync_qrcode.description", default="重新复制 NapCat 登录二维码到插件静态目录并返回最新状态。"), input_schema={"type": "object", "properties": {}})
    async def sync_qrcode(self, **_):
        await self._sync_napcat_qrcode_into_static()
        return Ok(await self._build_dashboard_state())

    @plugin_entry(id="start_auto_reply", name=tr("entries.start_auto_reply.name", default="启动自动回复"), description=tr("entries.start_auto_reply.description", default="开始监听 QQ 消息并自动回复。"), input_schema={"type": "object", "properties": {}})
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

    @plugin_entry(id="stop_auto_reply", name=tr("entries.stop_auto_reply.name", default="停止自动回复"), description=tr("entries.stop_auto_reply.description", default="停止监听 QQ 消息。"), input_schema={"type": "object", "properties": {}})
    async def stop_auto_reply(self, **_):
        if not self._running and not self._message_task:
            return Ok({"status": "not_running"})
        await self._stop_auto_reply_runtime(stop_napcat=False)
        return Ok({"status": "stopped"})

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

    async def _handle_private_message(self, sender_id: str, message_text: str, user_nickname: Optional[str] = None):
        permission_level = self.permission_mgr.get_permission_level(sender_id)
        if permission_level == "none":
            return
        if permission_level == "normal":
            await self._handle_normal_relay(message_text, sender_id, source_type="private", source_id=sender_id)
            return
        reply_text = await self._generate_reply(message_text, permission_level, sender_id, is_group=False, user_nickname=user_nickname)
        if reply_text:
            await self.qq_client.send_message(sender_id, reply_text)

    async def _handle_group_message(self, group_id: str, sender_id: str, message_text: str, is_at_bot: bool, user_nickname: Optional[str] = None):
        group_level = self.group_permission_mgr.get_group_level(group_id)
        if group_level == "none":
            return
        if group_level == "normal":
            await self._handle_normal_relay(message_text, sender_id, source_type="group", source_id=group_id)
            return
        if group_level == "trusted" and not is_at_bot:
            return
        if group_level == "open" and not is_at_bot and random.random() >= self._truth_reply_probability:
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

    async def _handle_normal_relay(self, message_text: str, sender_id: str, source_type: str, source_id: str):
        if not self.qq_client or not self._admin_qq or sender_id == self._admin_qq:
            return None
        if self._normal_relay_probability <= 0.0 or random.random() >= self._normal_relay_probability:
            return None
        message_text = self._sanitize_message_text(message_text)
        if source_type == "group":
            relay_text = f"[QQ群转发] 群 {source_id} / 用户 {sender_id}: {message_text}"
        else:
            relay_text = f"[QQ私聊转发] 来自 {sender_id}: {message_text}"
        await self.qq_client.send_message(self._admin_qq, relay_text)
        return None

    def _track_handler_task(self, task: asyncio.Task) -> None:
        self._handler_tasks.add(task)
        task.add_done_callback(self._handler_tasks.discard)

    async def _run_message_handler(self, message: Dict[str, Any]) -> None:
        session_key = self._message_session_key(message)
        async with self._message_concurrency:
            if not session_key:
                await self._handle_message(message)
                return
            async def _handle_current_message() -> None:
                await self._handle_message(message)
            await self._run_with_session_lock(session_key, _handle_current_message)
