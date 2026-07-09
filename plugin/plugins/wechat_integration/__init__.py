from __future__ import annotations

import asyncio
import time
from typing import Any, Optional
from urllib.parse import quote

from plugin.sdk.plugin import NekoPluginBase, lifecycle, neko_plugin, plugin_entry, Ok, Err, SdkError, tr, ui

from .config_store import WechatConfigStore
from .wechat_client import WechatClient


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

    async def _persist_config(self) -> bool:
        try:
            self._settings = await self.config_store.save(self._settings)
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
            qrcode_url = (
                f"https://api.qrserver.com/v1/create-qr-code/?size=300x300&data="
                f"{quote(login_session.qrcode_img_content)}"
            )

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
                {"id": "save_settings", "entry_id": "save_settings"},
                {"id": "get_dashboard_state", "entry_id": "get_dashboard_state"},
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

        if not self._login_session:
            return Ok({
                **self._build_dashboard_state(),
                "message": self.i18n.t("messages.no_qrcode", default="没有活跃的登录会话，请先获取二维码"),
            })

        try:
            data = await self.wechat_client.poll_qrcode_status(self._login_session.qrcode)
        except asyncio.TimeoutError:
            return Ok(self._build_dashboard_state())
        except Exception as e:
            self.logger.error(f"轮询微信扫码状态失败: {e}")
            self._login_session.status = "error"
            self._login_session.error = str(e)
            return Ok(self._build_dashboard_state())

        status = str(data.get("status") or "wait").strip()
        self._login_session.status = status

        if status == "expired":
            self._qr_expired_count += 1
            if self._qr_expired_count > 3:
                self._login_session.error = self.i18n.t("errors.qr_max_retry", default="二维码已过期，超过重试次数，请刷新二维码")
                return Ok(self._build_dashboard_state())
            # Auto-refresh
            try:
                bot_type = str(self._settings.get("bot_type") or "3")
                new_data = await self.wechat_client.get_qrcode(bot_type=bot_type)
                new_qrcode = str(new_data.get("qrcode") or "").strip()
                new_img = str(new_data.get("qrcode_img_content") or "").strip()
                if new_qrcode and new_img:
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
                self._login_session.error = self.i18n.t("errors.no_token", default="登录确认但未返回凭证")
                self._login_session.status = "error"
                return Ok(self._build_dashboard_state())

            self._login_session.bot_token = str(bot_token)
            self._login_session.account_id = str(account_id) if account_id else None
            self._login_session.base_url = str(base_url) if base_url else None
            self._login_session.user_id = str(user_id) if user_id else None

            # Save credentials
            self._settings["token"] = self._login_session.bot_token
            if self._login_session.account_id:
                self._settings["account_id"] = self._login_session.account_id
            if self._login_session.user_id:
                self._settings["user_id"] = self._login_session.user_id
            if self._login_session.base_url:
                self._settings["base_url"] = self._login_session.base_url.rstrip("/")

            await self._persist_config()
            self._sync_client_from_settings()

            self.logger.info(
                f"[wechat_integration] 登录成功: account_id={self._login_session.account_id} user_id={self._login_session.user_id}"
            )

        if status == "error":
            self._login_session.error = str(data.get("error") or data.get("errmsg") or "未知错误")

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
        if base_url is not None:
            self._settings["base_url"] = str(base_url or "https://ilinkai.weixin.qq.com").strip()
        if bot_type is not None:
            self._settings["bot_type"] = str(bot_type or "3").strip()
        if show_onboarding is not None:
            self._settings["show_onboarding"] = bool(show_onboarding)

        success = await self._persist_config()
        self._sync_client_from_settings()

        payload = self._build_dashboard_state()
        payload["persisted"] = success
        return Ok(payload)
