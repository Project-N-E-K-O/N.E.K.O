from __future__ import annotations

import asyncio
import time
import uuid
from typing import Any, Optional
import base64
import io

from plugin.sdk.plugin import NekoPluginBase, lifecycle, neko_plugin, plugin_entry, Ok, Err, SdkError, tr, ui

from .config_store import WechatConfigStore
from .wechat_client import WechatClient

# 滑动窗口上限：最近 20 轮（每轮 2 条 = user + assistant），防止单次活跃会话历史撑爆
MAX_HISTORY_TURNS = 20
MAX_HISTORY_MESSAGES = MAX_HISTORY_TURNS * 2


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


class LoginSession:
    """微信扫码登录会话"""

    def __init__(self, qrcode: str, qrcode_img_content: str):
        self.qrcode = qrcode
        self.qrcode_img_content = qrcode_img_content
        self.started_at = time.time()
        self.status = "wait"  # wait / confirmed / expired / error
        self.bot_token: Optional[str] = None
        self.account_id: Optional[str] = None
        self.base_url: Optional[str] = None
        self.user_id: Optional[str] = None
        self.error: Optional[str] = None


@neko_plugin
class WechatIntegrationPlugin(NekoPluginBase):
    def __init__(self, ctx):
        super().__init__(ctx)
        self.file_logger = self.enable_file_logging(log_level="INFO")
        self.logger = self.file_logger
        self.config_store = WechatConfigStore(self.data_path())
        self._settings: dict[str, Any] = self.config_store.default_config()
        self.wechat_client: Optional[WechatClient] = None
        self._login_session: Optional[LoginSession] = None
        self._qr_expired_count = 0
        self._sync_buf: str = ""
        self._context_tokens: dict[str, str] = {}
        self._running: bool = False
        self._message_task: Optional[asyncio.Task] = None
        self._settle_memory_tasks: set[asyncio.Task] = set()
        self._shutdown_event = asyncio.Event()
        self._auth_state_lock = asyncio.Lock()
        self._wechat_sessions: dict[str, dict[str, Any]] = {}  # user_id → {history, last_activity}

    # ------------------------------------------------------------------ config
    async def _load_config(self) -> dict[str, Any]:
        self._settings = await self.config_store.load()
        return dict(self._settings)

    async def _ensure_config_initialized(self) -> dict[str, Any]:
        if not await self.config_store.exists():
            return self.config_store.default_config()
        return await self._load_config()

    async def _create_config(self) -> dict[str, Any]:
        self._settings = await self.config_store.create_empty()
        return dict(self._settings)

    async def _persist_config(self, settings: Optional[dict[str, Any]] = None) -> bool:
        try:
            candidate = self._settings if settings is None else settings
            self._settings = await self.config_store.save(candidate)
            return True
        except Exception as e:
            self.logger.error(f"持久化微信配置失败: {e}")
            return False

    def _sync_client_from_settings(self) -> None:
        if self.wechat_client:
            self.wechat_client.base_url = str(self._settings.get("base_url") or "https://ilinkai.weixin.qq.com").rstrip("/")
            self.wechat_client.token = self._settings.get("token") or None

    # --------------------------------------------------------------- lifecycle
    @lifecycle(id="startup")
    async def startup(self, **_):
        if not await self.config_store.exists():
            await self._create_config()
        settings = await self._ensure_config_initialized()
        self.logger.info(f"[wechat_integration] startup settings loaded")

        self.wechat_client = WechatClient(
            base_url=str(settings.get("base_url") or "https://ilinkai.weixin.qq.com"),
            cdn_base_url=str(settings.get("cdn_base_url") or "https://novac2c.cdn.weixin.qq.com/c2c"),
            api_timeout_ms=int(settings.get("api_timeout_ms") or 15000),
            token=settings.get("token") or None,
        )

        self._sync_buf = str(settings.get("sync_buf") or "")

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
        return Ok({"status": "ready"})

    @lifecycle(id="shutdown")
    async def shutdown(self, **_):
        self._shutdown_event.set()
        if self._message_task and not self._message_task.done():
            self._message_task.cancel()
            try:
                await self._message_task
            except asyncio.CancelledError:
                pass
            self._message_task = None
        self._running = False

        # Every turn has already gone through /cache.  Flush the cached turns
        # before the plugin disappears so an otherwise-active conversation is
        # summarized/reviewed too.  Do not send ``history`` to /process here:
        # that endpoint persists its input again and would duplicate the turns
        # that /cache has already written.
        active_sessions = list(self._wechat_sessions.values())
        self._wechat_sessions.clear()
        if active_sessions:
            results = await asyncio.gather(
                *(
                    self._settle_memory_session(
                        str(session.get("her_name") or ""), reason="plugin_shutdown"
                    )
                    for session in active_sessions
                    if session.get("memory_enabled") and session.get("history")
                ),
                return_exceptions=True,
            )
            failures = sum(result is not True for result in results)
            if failures:
                self.logger.warning(
                    "[wechat_integration] failed to settle %d active memory session(s) on shutdown",
                    failures,
                )
        if self._settle_memory_tasks:
            results = await asyncio.gather(
                *list(self._settle_memory_tasks),
                return_exceptions=True,
            )
            failures = sum(result is not True for result in results)
            if failures:
                self.logger.warning(
                    "[wechat_integration] failed to settle %d pending memory session(s) on shutdown",
                    failures,
                )
        if self.wechat_client:
            await self.wechat_client.close()
            self.wechat_client = None
        return Ok({"status": "shutdown"})

    # --------------------------------------------------------- login helpers
    @staticmethod
    def _mask_token(token: str) -> str:
        normalized = str(token or "")
        if not normalized:
            return ""
        if len(normalized) <= 6:
            return "*" * len(normalized)
        return f"{normalized[:3]}***{normalized[-3:]}"

    def _is_logged_in(self) -> bool:
        return bool(self._settings.get("token"))

    def _is_login_session_valid(self) -> bool:
        if not self._login_session:
            return False
        elapsed_ms = (time.time() - self._login_session.started_at) * 1000
        return elapsed_ms < 5 * 60_000  # 5 minutes

    # --------------------------------------------------------- build state
    def _build_dashboard_state(self) -> dict[str, Any]:
        settings = dict(self._settings or {})
        is_logged_in = self._is_logged_in()
        login_session = self._login_session

        qrcode_url = ""
        if login_session and login_session.status == "wait":
            try:
                import qrcode as qrcode_lib
                img = qrcode_lib.make(
                    login_session.qrcode_img_content,
                    error_correction=qrcode_lib.constants.ERROR_CORRECT_L,
                    box_size=10,
                    border=2,
                )
                buf = io.BytesIO()
                img.save(buf, format="PNG")
                qrcode_url = "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode("ascii")
            except Exception:
                qrcode_url = ""

        return {
            "login": {
                "logged_in": is_logged_in,
                "account_id": settings.get("account_id") or None,
                "user_id": settings.get("user_id") or None,
                "status": login_session.status if login_session else ("logged_in" if is_logged_in else "idle"),
                "error": login_session.error if login_session else None,
            },
            "qrcode": {
                "url": qrcode_url,
                "has_session": login_session is not None,
                "status": login_session.status if login_session else "idle",
                "expired_count": self._qr_expired_count,
            },
            "settings": {
                "base_url": settings.get("base_url", "https://ilinkai.weixin.qq.com"),
                "token_configured": is_logged_in,
                "token_masked": self._mask_token(str(settings.get("token") or "")),
                "bot_type": settings.get("bot_type", "3"),
                "show_onboarding": bool(settings.get("show_onboarding", True)),
                "auto_reply_running": self._running,
            },
            "config_ready": True,
            "ui": build_open_ui_payload(plugin_id=self.plugin_id, available=True, i18n=self.i18n),
        }

    # ------------------------------------------------------------ ui context
    @ui.context(id="wechat_integration")
    async def get_dashboard_context(self):
        state = self._build_dashboard_state()
        return {
            **state,
            "actions": [
                {"id": "start_login", "entry_id": "start_login"},
                {"id": "poll_login_status", "entry_id": "poll_login_status"},
                {"id": "refresh_qrcode", "entry_id": "refresh_qrcode"},
                {"id": "logout", "entry_id": "logout"},
                {"id": "save_settings", "entry_id": "save_settings"},
                {"id": "get_dashboard_state", "entry_id": "get_dashboard_state"},
                {"id": "start_auto_reply", "entry_id": "start_auto_reply"},
                {"id": "stop_auto_reply", "entry_id": "stop_auto_reply"},
                {"id": "send_message", "entry_id": "send_message"},
            ],
        }

    async def open_ui(self, **_):
        return Ok(build_open_ui_payload(plugin_id=self.plugin_id, available=True, i18n=self.i18n))

    # ------------------------------------------------------- plugin entries
    @plugin_entry(
        id="get_dashboard_state",
        name=tr("entries.get_dashboard_state.name", default="获取控制面板状态"),
        description=tr("entries.get_dashboard_state.description", default="读取微信插件当前的登录状态、二维码信息和配置。"),
        input_schema={"type": "object", "properties": {}},
    )
    async def get_dashboard_state(self, **_):
        return Ok(self._build_dashboard_state())

    @ui.action(id="start_login", label=tr("ui.qrcode.start", default="开始扫码登录"), refresh_context=True)
    @plugin_entry(
        id="start_login",
        name=tr("entries.start_login.name", default="开始扫码登录"),
        description=tr("entries.start_login.description", default="向微信 OpenClaw API 请求一个新的登录二维码，开始扫码登录流程。"),
        input_schema={"type": "object", "properties": {}},
    )
    async def start_login(self, **_):
        if not self.wechat_client:
            return Err(SdkError("微信客户端未初始化"))

        if self._is_logged_in():
            return Ok({
                **self._build_dashboard_state(),
                "message": self.i18n.t("messages.already_logged_in", default="已登录，无需重新扫码"),
            })

        try:
            bot_type = str(self._settings.get("bot_type") or "3")
            data = await self.wechat_client.get_qrcode(bot_type=bot_type)
        except Exception as e:
            self.logger.error(f"获取微信二维码失败: {e}")
            return Err(SdkError(f"获取二维码失败: {e}"))

        qrcode = str(data.get("qrcode") or "").strip()
        qrcode_img_content = str(data.get("qrcode_img_content") or "").strip()

        if not qrcode or not qrcode_img_content:
            return Err(SdkError("微信 API 未返回有效的二维码数据"))

        self._login_session = LoginSession(qrcode=qrcode, qrcode_img_content=qrcode_img_content)
        self._qr_expired_count = 0
        self.logger.info(f"[wechat_integration] 二维码已生成，等待扫码")

        return Ok(self._build_dashboard_state())

    @ui.action(id="poll_login_status", label=tr("ui.qrcode.poll", default="刷新登录状态"), refresh_context=True)
    @plugin_entry(
        id="poll_login_status",
        name=tr("entries.poll_login_status.name", default="查询扫码状态"),
        description=tr("entries.poll_login_status.description", default="轮询当前二维码的扫码状态，检查是否已被扫描或确认。"),
        input_schema={"type": "object", "properties": {}},
    )
    async def poll_login_status(self, **_):
        if not self.wechat_client:
            return Err(SdkError("微信客户端未初始化"))

        login_session = self._login_session
        if not login_session:
            return Ok({
                **self._build_dashboard_state(),
                "message": self.i18n.t("messages.no_qrcode", default="没有活跃的登录会话，请先获取二维码"),
            })

        try:
            data = await self.wechat_client.poll_qrcode_status(login_session.qrcode)
        except asyncio.TimeoutError:
            return Ok(self._build_dashboard_state())
        except Exception as e:
            self.logger.error(f"轮询微信扫码状态失败: {e}")
            if self._login_session is login_session:
                login_session.status = "error"
                login_session.error = str(e)
            return Ok(self._build_dashboard_state())

        # Logout or QR refresh may replace the session while the API call is in flight.
        if self._login_session is not login_session:
            return Ok(self._build_dashboard_state())

        status = str(data.get("status") or "wait").strip()
        login_session.status = status

        if status == "expired":
            self._qr_expired_count += 1
            if self._qr_expired_count > 3:
                login_session.error = self.i18n.t("errors.qr_max_retry", default="二维码已过期，超过重试次数，请刷新二维码")
                return Ok(self._build_dashboard_state())
            # Auto-refresh
            try:
                bot_type = str(self._settings.get("bot_type") or "3")
                new_data = await self.wechat_client.get_qrcode(bot_type=bot_type)
                new_qrcode = str(new_data.get("qrcode") or "").strip()
                new_img = str(new_data.get("qrcode_img_content") or "").strip()
                if new_qrcode and new_img and self._login_session is login_session:
                    self._login_session = LoginSession(qrcode=new_qrcode, qrcode_img_content=new_img)
                    self.logger.info(f"[wechat_integration] 二维码已过期，已自动刷新 ({self._qr_expired_count}/3)")
            except Exception as e:
                self.logger.warning(f"自动刷新二维码失败: {e}")
            return Ok(self._build_dashboard_state())

        if status == "confirmed":
            bot_token = data.get("bot_token")
            account_id = data.get("ilink_bot_id")
            base_url = data.get("baseurl")
            user_id = data.get("ilink_user_id")

            if not bot_token:
                login_session.error = self.i18n.t("errors.no_token", default="登录确认但未返回凭证")
                login_session.status = "error"
                return Ok(self._build_dashboard_state())

            async with self._auth_state_lock:
                if self._login_session is not login_session:
                    return Ok(self._build_dashboard_state())

                login_session.bot_token = str(bot_token)
                login_session.account_id = str(account_id) if account_id else None
                login_session.base_url = str(base_url) if base_url else None
                login_session.user_id = str(user_id) if user_id else None

                # Persist a copy so a failed write cannot partially mutate runtime auth state.
                logged_in_settings = dict(self._settings)
                logged_in_settings["token"] = login_session.bot_token
                if login_session.account_id:
                    logged_in_settings["account_id"] = login_session.account_id
                if login_session.user_id:
                    logged_in_settings["user_id"] = login_session.user_id
                if login_session.base_url:
                    logged_in_settings["base_url"] = login_session.base_url.rstrip("/")

                if not await self._persist_config(logged_in_settings):
                    login_session.status = "error"
                    login_session.error = self.i18n.t("errors.login_persist_failed", default="无法保存登录凭证")
                    return Ok(self._build_dashboard_state())
                self._sync_client_from_settings()

            self.logger.info(
                f"[wechat_integration] 登录成功: account_id={login_session.account_id} user_id={login_session.user_id}"
            )

        if status == "error":
            login_session.error = str(data.get("error") or data.get("errmsg") or "未知错误")

        return Ok(self._build_dashboard_state())

    @ui.action(id="refresh_qrcode", label=tr("ui.qrcode.refresh", default="刷新二维码"), refresh_context=True)
    @plugin_entry(
        id="refresh_qrcode",
        name=tr("entries.refresh_qrcode.name", default="刷新二维码"),
        description=tr("entries.refresh_qrcode.description", default="重新向微信 OpenClaw API 请求一个新的登录二维码。"),
        input_schema={"type": "object", "properties": {}},
    )
    async def refresh_qrcode(self, **_):
        # Reuse start_login logic
        return await self.start_login()

    @ui.action(
        id="logout",
        label=tr("ui.actions.logout", default="退出登录"),
        tone="danger",
        refresh_context=True,
    )
    @plugin_entry(
        id="logout",
        name=tr("entries.logout.name", default="退出登录"),
        description=tr(
            "entries.logout.description",
            default="停止微信消息监听并清除本机保存的登录凭证。",
        ),
        input_schema={"type": "object", "properties": {}},
    )
    async def logout(self, **_):
        # Commit the disk state first. If this fails, runtime stays logged in and
        # remains consistent with what the next plugin start would restore.
        async with self._auth_state_lock:
            logged_out_settings = dict(self._settings)
            for key in ("token", "account_id", "user_id", "sync_buf"):
                logged_out_settings[key] = ""

            if not await self._persist_config(logged_out_settings):
                return Err(SdkError(
                    self.i18n.t("errors.logout_failed", default="退出登录失败：无法清除本地登录凭证")
                ))
            self._sync_client_from_settings()
            self._login_session = None

        await self.stop_auto_reply()
        self._shutdown_event.set()
        self._running = False
        self._message_task = None

        self._qr_expired_count = 0
        self._sync_buf = ""
        self._context_tokens.clear()

        settle_candidates = [
            (user_id, session)
            for user_id, session in self._wechat_sessions.items()
            if session.get("memory_enabled") and session.get("history")
        ]
        failed_sessions = {}
        if settle_candidates:
            results = await asyncio.gather(
                *(
                    self._settle_memory_session(
                        str(session.get("her_name") or ""), reason="logout"
                    )
                    for _, session in settle_candidates
                ),
                return_exceptions=True,
            )
            failed_sessions = {
                user_id: session
                for (user_id, session), result in zip(settle_candidates, results)
                if result is not True
            }
            if failed_sessions:
                self.logger.warning(
                    "[wechat_integration] failed to settle %d active memory session(s) on logout",
                    len(failed_sessions),
                )
        self._wechat_sessions.clear()
        self._wechat_sessions.update(failed_sessions)

        self.logger.info("[wechat_integration] logged out and cleared local credentials")
        return Ok(self._build_dashboard_state())

    @ui.action(id="save_settings", label=tr("entries.save_settings.name", default="保存设置"), refresh_context=True)
    @plugin_entry(
        id="save_settings",
        name=tr("entries.save_settings.name", default="保存微信设置"),
        description=tr("entries.save_settings.description", default="保存微信插件当前的 API 地址、Bot 类型等设置。"),
        input_schema={
            "type": "object",
            "properties": {
                "base_url": {"type": "string"},
                "bot_type": {"type": "string"},
                "show_onboarding": {"type": "boolean"},
            },
            "additionalProperties": False,
        },
    )
    async def save_settings(
        self,
        base_url: Optional[str] = None,
        bot_type: Optional[str] = None,
        show_onboarding: Optional[bool] = None,
        **_,
    ):
        async with self._auth_state_lock:
            updated_settings = dict(self._settings)
            if base_url is not None:
                updated_settings["base_url"] = str(base_url or "https://ilinkai.weixin.qq.com").strip()
            if bot_type is not None:
                updated_settings["bot_type"] = str(bot_type or "3").strip()
            if show_onboarding is not None:
                updated_settings["show_onboarding"] = bool(show_onboarding)

            success = await self._persist_config(updated_settings)
            if success:
                self._sync_client_from_settings()

        payload = self._build_dashboard_state()
        payload["persisted"] = success
        return Ok(payload)

    # ------------------------------------------------------ message handling
    @staticmethod
    def _extract_text_from_item_list(item_list: list[dict[str, Any]] | None) -> str:
        if not item_list:
            return ""
        text_parts: list[str] = []
        for item in item_list:
            item_type = int(item.get("type") or 0)
            if item_type == 1:
                t = str(item.get("text_item", {}).get("text", "")).strip()
                if t:
                    text_parts.append(t)
            elif item_type == 2:
                text_parts.append("[图片]")
            elif item_type == 3:
                voice_text = str(item.get("voice_item", {}).get("text", "")).strip()
                text_parts.append(voice_text or "[语音]")
            elif item_type == 4:
                text_parts.append("[文件]")
            elif item_type == 5:
                text_parts.append("[视频]")
        return "\n".join(text_parts).strip()

    async def _generate_wechat_reply(self, user_id: str, message: str) -> str | None:
        """生成微信回复。微信是主人专用通道，对话对象始终是主人。"""
        try:
            from config.prompts.prompts_sys import SESSION_INIT_PROMPT
            from main_logic.core import apply_role_placeholders
            from utils.config_manager import get_config_manager
            from utils.llm_client import create_chat_llm_async
            from utils.language_utils import get_global_language

            config_manager = get_config_manager()
            master_name, her_name, _, catgirl_data, _, lanlan_prompt_map, _, _, _ = config_manager.get_character_data()
            model_config = config_manager.get_model_api_config("agent")
            base_url = str(model_config.get("base_url") or "").strip()
            model = str(model_config.get("model") or "").strip()
            api_key = str(model_config.get("api_key") or "").strip()

            if not base_url or not model:
                self.logger.warning("[wechat_integration] agent model not configured, skip reply")
                return None

            master_title = master_name if master_name else "主人"
            character_prompt = lanlan_prompt_map.get(her_name, "你是一个友好的AI助手")

            # 角色卡额外字段
            current_character = catgirl_data.get(her_name, {})
            character_card_fields = {}
            for key, value in current_character.items():
                if key not in ["_reserved", "voice_id", "system_prompt", "model_type",
                               "live2d", "vrm", "vrm_animation", "lighting", "vrm_rotation",
                               "live2d_item_id", "item_id", "idleAnimation"]:
                    if isinstance(value, (str, int, float, bool)) and value:
                        character_card_fields[key] = value

            # 语言
            user_language = get_global_language()
            try:
                from utils.i18n_utils import normalize_language_code
                short_language = normalize_language_code(user_language, format="short") if normalize_language_code else user_language
            except Exception:
                short_language = user_language
            init_prompt_template = SESSION_INIT_PROMPT.get(
                short_language,
                SESSION_INIT_PROMPT.get(user_language, SESSION_INIT_PROMPT["zh"]),
            )

            # 构建 system prompt
            system_prompt_parts = [
                init_prompt_template.format(name=her_name),
                apply_role_placeholders(character_prompt, lanlan_name=her_name, master_name=master_title),
            ]
            if character_card_fields:
                system_prompt_parts.append("\n======角色卡额外设定======")
                for field_name, field_value in character_card_fields.items():
                    rendered_value = apply_role_placeholders(
                        str(field_value), lanlan_name=her_name, master_name=master_title,
                    )
                    system_prompt_parts.append(f"{field_name}: {rendered_value}")
                system_prompt_parts.append("======角色卡设定结束======")
            system_prompt_parts.append(
                f"\n======身份定义======\n"
                f"- 你自己：{her_name}，你是当前回复者\n"
                f"- 主人：{master_title}，是固定身份\n"
                f"- 当前对话对象：{master_title}（微信ID: {user_id}），这就是{master_title}本人\n"
                f"- 微信是主人专用通道，通过微信联系你的人就是{master_title}本人\n"
                f"======身份定义结束======\n"
                f"\n======微信私聊环境（主人专用）======\n"
                f"- 你正在通过微信与{master_title}私聊\n"
                f"- 请保持角色设定，用简短自然的话回复（不超过50字）\n"
                f"- 不要使用 Markdown 格式，不要使用表情符号\n"
                f"- 记住你是 {her_name}，始终以 {her_name} 的身份回复\n"
                f"- 在回复中自然地称呼对方为\"{master_title}\"\n"
                f"- 注意不要重复之前的发言\n"
                f"======环境说明结束======"
            )
            system_prompt = "\n".join(system_prompt_parts)

            # 获取或创建会话历史
            now = time.time()
            self._cleanup_wechat_sessions(now)
            session = self._wechat_sessions.get(user_id)
            if session is None:
                # 新建会话：加载记忆上下文
                memory_context = await self._fetch_memory_context(her_name)
                session = {
                    "history": [],
                    "last_activity": now,
                    "her_name": her_name,
                    "memory_enabled": True,
                }
                self._wechat_sessions[user_id] = session
                self.logger.info(
                    f"[wechat_integration] new session for {user_id}, memory_ctx_len={len(memory_context or '')}"
                )
            else:
                memory_context = None
            session["last_activity"] = now

            # 构建 system prompt（含记忆上下文）
            final_system_prompt = system_prompt
            if memory_context:
                final_system_prompt = system_prompt + "\n\n" + memory_context

            # 构建完整消息列表: system + history (裁剪到窗口上限) + new_user_msg
            messages = [{"role": "system", "content": final_system_prompt}]
            # 防御性裁剪：仅取最近 MAX_HISTORY_MESSAGES 条
            recent_history = session["history"][-MAX_HISTORY_MESSAGES:]
            messages.extend(recent_history)
            messages.append({"role": "user", "content": message})

            llm = await create_chat_llm_async(
                model=model,
                base_url=base_url,
                api_key=api_key,
                max_completion_tokens=300,
                timeout=30.0,
                provider_type=model_config.get("provider_type"),
            )
            try:
                response = await llm.ainvoke(messages)
                reply = (getattr(response, "content", "") or "").strip()
                if reply:
                    # 保存到对话历史
                    session["history"].append({"role": "user", "content": message})
                    session["history"].append({"role": "assistant", "content": reply})
                    # 滑动窗口裁剪：只保留最近 N 轮，防止历史无限增长撑爆 token
                    if len(session["history"]) > MAX_HISTORY_MESSAGES:
                        trimmed = len(session["history"]) - MAX_HISTORY_MESSAGES
                        del session["history"][:trimmed]
                        self.logger.debug(
                            f"[wechat_integration] trimmed {trimmed} old history messages for {user_id}"
                        )
                    self.logger.info(
                        f"[wechat_integration] LLM reply len={len(reply)} history_turns={len(session['history']) // 2}"
                    )
                    # 缓存增量到 Memory Server
                    await self._cache_memory_delta(her_name, [
                        {"role": "user", "content": message},
                        {"role": "assistant", "content": reply},
                    ])
                    return reply
                self.logger.warning("[wechat_integration] LLM returned empty reply")
                return None
            finally:
                aclose = getattr(llm, "aclose", None)
                if callable(aclose):
                    try:
                        await aclose()
                    except Exception:
                        pass
        except Exception as e:
            self.logger.error(f"[wechat_integration] generate reply failed: {e}")
            return None

    def _cleanup_wechat_sessions(self, now: float | None = None) -> None:
        """清理超过 5 分钟无活动的微信会话，先结算记忆再删除"""
        if now is None:
            now = time.time()
        stale = [
            uid for uid, s in self._wechat_sessions.items()
            if now - s.get("last_activity", 0) > 300
        ]
        for uid in stale:
            session = self._wechat_sessions.get(uid)
            if session and session.get("memory_enabled") and session.get("history"):
                task = asyncio.ensure_future(
                    self._settle_memory_session(
                        session["her_name"], reason="idle_timeout"
                    )
                )
                self._settle_memory_tasks.add(task)
                task.add_done_callback(self._settle_memory_tasks.discard)
            del self._wechat_sessions[uid]
            self.logger.info(f"[wechat_integration] cleaned up stale session: {uid}")

    # ------------------------------------------------ Memory Server helpers
    @staticmethod
    async def _fetch_memory_context(her_name: str) -> str | None:
        """从 Memory Server 加载对话记忆"""
        try:
            import httpx
            from config import MEMORY_SERVER_PORT
            async with httpx.AsyncClient(timeout=5.0, proxy=None, trust_env=False) as client:
                response = await client.get(
                    f"http://127.0.0.1:{MEMORY_SERVER_PORT}/new_dialog/{her_name}"
                )
                if response.is_success:
                    memory = response.text.strip()
                    if memory:
                        return memory
        except Exception:
            pass
        return None

    @staticmethod
    async def _cache_memory_delta(her_name: str, messages: list[dict[str, Any]]) -> None:
        """缓存增量对话到 Memory Server"""
        try:
            import json
            import httpx
            from config import MEMORY_SERVER_PORT
            payload = {"input_history": json.dumps(messages, ensure_ascii=False)}
            async with httpx.AsyncClient(timeout=5.0, proxy=None, trust_env=False) as client:
                response = await client.post(
                    f"http://127.0.0.1:{MEMORY_SERVER_PORT}/cache/{her_name}",
                    json=payload,
                )
                if response.is_success:
                    return
        except Exception:
            pass

    @staticmethod
    async def _settle_memory_session(her_name: str, reason: str) -> bool:
        """结算已通过 ``/cache`` 持久化的会话，不重复写入对话消息。"""
        if not her_name:
            return False
        try:
            import json
            import httpx
            from config import MEMORY_SERVER_PORT
            # /settle with an empty increment performs compression/review for
            # cached turns.  Passing the in-memory session history to /process
            # would append the same messages to recent history and SQLite again.
            payload = {"input_history": json.dumps([], ensure_ascii=False)}
            async with httpx.AsyncClient(timeout=30.0, proxy=None, trust_env=False) as client:
                response = await client.post(
                    f"http://127.0.0.1:{MEMORY_SERVER_PORT}/settle/{her_name}",
                    json=payload,
                )
                if response.is_success:
                    return True
        except Exception:
            # Keep the failure non-fatal, matching the cache path's behavior.
            pass
        return False

    async def _handle_inbound_message(self, msg: dict[str, Any]) -> None:
        from_user_id = str(msg.get("from_user_id") or "").strip()
        if not from_user_id:
            return

        context_token = str(msg.get("context_token") or "").strip()
        if context_token:
            self._context_tokens[from_user_id] = context_token

        item_list: list[dict[str, Any]] = msg.get("item_list", []) or []
        text = self._extract_text_from_item_list(item_list)
        if not text:
            return

        self.logger.info(
            f"[wechat_integration] received msg from={from_user_id} text={text[:50]}"
        )

        # 生成回复
        reply = await self._generate_wechat_reply(user_id=from_user_id, message=text)
        if not reply:
            self.logger.warning("[wechat_integration] no reply generated, skip send")
            return

        # 发送回复
        success = await self._send_text_message(from_user_id, reply)
        if not success:
            self.logger.warning("[wechat_integration] failed to send reply to WeChat")

    async def _poll_inbound_updates(self) -> None:
        if not self.wechat_client:
            return

        data = await self.wechat_client.get_updates(sync_buf=self._sync_buf)

        ret = int(data.get("ret") or 0)
        errcode = data.get("errcode", 0)
        if ret != 0 and ret is not None:
            errmsg = str(data.get("errmsg", ""))
            self.logger.warning(
                f"[wechat_integration] getupdates error: ret={ret} errcode={errcode} errmsg={errmsg}"
            )
            return
        if errcode and int(errcode) != 0:
            errmsg = str(data.get("errmsg", ""))
            # Session timeout: clear token, force re-login
            if int(errcode) == -14:
                self.logger.warning(
                    "[wechat_integration] session timeout, clearing token for re-login"
                )
                self._settings["token"] = ""
                self._sync_buf = ""
                self._context_tokens.clear()
                self._login_session = None
                if self.wechat_client:
                    self.wechat_client.token = None
                await self._persist_config()
                return
            self.logger.warning(
                f"[wechat_integration] getupdates error: ret={ret} errcode={errcode} errmsg={errmsg}"
            )
            return

        if data.get("get_updates_buf"):
            self._sync_buf = str(data["get_updates_buf"])

        for msg in data.get("msgs", []) if isinstance(data.get("msgs"), list) else []:
            if self._shutdown_event.is_set():
                return
            if not isinstance(msg, dict):
                continue
            await self._handle_inbound_message(msg)

    async def _send_text_message(self, user_id: str, text: str) -> bool:
        if not self.wechat_client or not self._is_logged_in():
            self.logger.warning("[wechat_integration] cannot send: not logged in")
            return False
        if not text or not text.strip():
            return False

        context_token = self._context_tokens.get(user_id)
        if not context_token:
            self.logger.warning(
                f"[wechat_integration] no context_token for {user_id}, cannot send"
            )
            return False

        payload = {
            "base_info": {"channel_version": "kiraai"},
            "msg": {
                "from_user_id": "",
                "to_user_id": user_id,
                "client_id": uuid.uuid4().hex,
                "message_type": 2,
                "message_state": 2,
                "context_token": context_token,
                "item_list": [
                    {"type": 1, "text_item": {"text": text.strip()}}
                ],
            },
        }

        try:
            await self.wechat_client.send_message_payload(payload)
            self.logger.info(f"[wechat_integration] send_message ok: to={user_id} len={len(text)}")
            return True
        except Exception as e:
            self.logger.error(f"[wechat_integration] send failed: {e}")
            return False

    async def _run_message_loop(self) -> None:
        while not self._shutdown_event.is_set():
            if not self._is_logged_in():
                await asyncio.sleep(2)
                continue
            try:
                await self._poll_inbound_updates()
                if not self._shutdown_event.is_set():
                    await asyncio.sleep(1)
            except asyncio.TimeoutError:
                continue
            except asyncio.CancelledError:
                raise
            except Exception as e:
                self.logger.error(f"[wechat_integration] poll inbound failed: {e}")
                await asyncio.sleep(5)

    # --------------------------------------------------- plugin entries (msg)
    @plugin_entry(
        id="start_auto_reply",
        name=tr("entries.start_auto_reply.name", default="开始消息监听"),
        description=tr(
            "entries.start_auto_reply.description",
            default="开始接收微信消息并自动推送到 AI 对话，AI 可以直接回复。",
        ),
        input_schema={"type": "object", "properties": {}},
    )
    async def start_auto_reply(self, **_):
        if not self._is_logged_in():
            return Err(SdkError(
                self.i18n.t("errors.not_logged_in", default="未登录，请先扫码登录后再开始消息监听")
            ))
        if self._running:
            return Ok({"status": "already_running"})

        self._shutdown_event.clear()
        self._running = True
        self._message_task = asyncio.create_task(self._run_message_loop())
        self.logger.info("[wechat_integration] auto-reply message loop started")
        return Ok(self._build_dashboard_state())

    @plugin_entry(
        id="stop_auto_reply",
        name=tr("entries.stop_auto_reply.name", default="停止消息监听"),
        description=tr(
            "entries.stop_auto_reply.description",
            default="停止接收微信消息，不再自动推送到 AI 对话。",
        ),
        input_schema={"type": "object", "properties": {}},
    )
    async def stop_auto_reply(self, **_):
        if not self._running and not self._message_task:
            return Ok({"status": "not_running"})

        self._shutdown_event.set()
        if self._message_task and not self._message_task.done():
            self._message_task.cancel()
            try:
                await self._message_task
            except asyncio.CancelledError:
                pass
            self._message_task = None
        self._running = False
        self.logger.info("[wechat_integration] auto-reply message loop stopped")
        return Ok(self._build_dashboard_state())

    @plugin_entry(
        id="send_message",
        name=tr("entries.send_message.name", default="发送微信消息"),
        description=tr(
            "entries.send_message.description",
            default="向指定微信用户发送一条文本消息。需要先在消息监听中收到过该用户的消息。",
        ),
        input_schema={
            "type": "object",
            "properties": {
                "user_id": {"type": "string", "description": "目标微信用户 ID"},
                "text": {"type": "string", "description": "要发送的文本内容"},
            },
            "required": ["user_id", "text"],
            "additionalProperties": False,
        },
    )
    async def send_message(self, user_id: str, text: str, **_):
        if not self._is_logged_in():
            return Err(SdkError(
                self.i18n.t("errors.not_logged_in", default="未登录，请先扫码登录")
            ))
        self.logger.info(f"[wechat_integration] send_message called: user={user_id} text={text[:50]}")
        success = await self._send_text_message(str(user_id).strip(), str(text).strip())
        if success:
            return Ok({"status": "sent", "user_id": str(user_id).strip()})
        return Err(SdkError(
            self.i18n.t(
                "errors.send_failed",
                default="发送失败：未找到该用户的 context_token，请先通过消息监听接收一条该用户的消息。",
            )
        ))
