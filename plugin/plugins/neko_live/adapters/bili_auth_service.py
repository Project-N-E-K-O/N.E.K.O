"""B站 扫码登录服务（P5 登录态）。

移植自旧插件 `bilibili_danmaku/bili_auth_service.py`（依赖注入式、与 neko_live 解耦）：
只编排 `bilibili_api.login_v2` 的二维码登录状态机，凭据的加载/加密保存/重载由注入的三个
回调负责（在 neko_live 里由 `stores/credential_store.py` + runtime 提供）。返回状态字典。
"""

from __future__ import annotations

import base64
import time
from typing import Any, Awaitable, Callable, Dict, Optional

CredentialProvider = Callable[[], Awaitable[Optional[object]]]
CredentialSaver = Callable[[Dict[str, str]], Awaitable[bool]]
CredentialReloader = Callable[[], Awaitable[None]]
QrCleanup = Callable[[], None]


class BiliAuthService:
    def __init__(
        self,
        *,
        logger: Any = None,
        credential_provider: CredentialProvider,
        credential_saver: CredentialSaver,
        credential_reloader: CredentialReloader,
        cleanup_callback: Optional[QrCleanup] = None,
    ) -> None:
        self.logger = logger
        self._credential_provider = credential_provider
        self._credential_saver = credential_saver
        self._credential_reloader = credential_reloader
        self._cleanup_callback = cleanup_callback
        self._login_session = None
        self._login_generated_at: float = 0.0

    def _require_login_sdk(self):
        try:
            from bilibili_api.login_v2 import QrCodeLogin, QrCodeLoginEvents
        except ImportError as exc:
            raise RuntimeError("缺少 bilibili_api 依赖，无法使用扫码登录。") from exc
        return QrCodeLogin, QrCodeLoginEvents

    def clear_qr_session(self) -> None:
        self._login_session = None
        if self._cleanup_callback:
            try:
                self._cleanup_callback()
            except Exception:
                if self.logger:
                    self.logger.debug("清理二维码会话失败", exc_info=True)

    async def _check_existing_login(self) -> Optional[Dict[str, Any]]:
        credential = await self._credential_provider()
        if not credential:
            return None
        try:
            from bilibili_api import user

            uid = int(getattr(credential, "dedeuserid", 0) or 0)
            if uid <= 0:
                return {
                    "status": "already_logged_in",
                    "message": "已存在 B站 登录凭据。",
                    "uid": str(getattr(credential, "dedeuserid", "") or ""),
                    "username": "",
                }
            info = await user.User(uid=uid, credential=credential).get_user_info()
            return {
                "status": "already_logged_in",
                "message": "已登录B站，无需重复登录",
                "uid": str(getattr(credential, "dedeuserid", "") or ""),
                "username": info.get("name", ""),
            }
        except Exception:
            return None

    async def login(self) -> Dict[str, Any]:
        existing = await self._check_existing_login()
        if existing:
            return existing

        if self._login_session is not None:
            self.clear_qr_session()

        QrCodeLogin, _ = self._require_login_sdk()
        self._login_session = QrCodeLogin()
        await self._login_session.generate_qrcode()
        self._login_generated_at = time.time()
        pic = self._login_session.get_qrcode_picture()
        png_bytes = pic.content
        img_base64 = base64.b64encode(png_bytes).decode("utf-8")
        terminal_qr = self._login_session.get_qrcode_terminal()
        qr_url = str(getattr(self._login_session, "qr_link", "") or getattr(self._login_session, "qrcode_url", "") or "")
        return {
            "status": "qrcode_ready",
            "message": "请用B站App扫描二维码登录（180秒内有效）",
            "qrcode_image": f"data:image/png;base64,{img_base64}",
            "qrcode_terminal": terminal_qr,
            "qrcode_url": qr_url,
            "next_step": "用户扫码后，请调用 bili_login_check 检查登录状态",
        }

    async def login_check(self) -> Dict[str, Any]:
        if not self._login_session:
            existing = await self._check_existing_login()
            if existing:
                return existing
            return {
                "status": "no_session",
                "message": "没有进行中的登录，请先调用 bili_login 生成二维码",
            }

        _, QrCodeLoginEvents = self._require_login_sdk()
        state = await self._login_session.check_state()
        none_state = getattr(QrCodeLoginEvents, "NONE", None)
        if none_state is not None and state == none_state:
            return {"status": "waiting", "message": "等待扫码...", "next_step": "请用B站App扫描二维码"}
        # 防误报：刚生成二维码时 check_state() 可能立刻返回 SCAN
        #（bilibili_api 某些版本中 SCAN 枚举值为 0，与"无事件"混淆）
        if state == QrCodeLoginEvents.SCAN and time.time() - self._login_generated_at < 1.0:
            return {"status": "waiting", "message": "等待扫码...", "next_step": "请用B站App扫描二维码"}
        if state == QrCodeLoginEvents.SCAN:
            return {
                "status": "scanning",
                "message": "已扫码，等待用户在手机上确认...",
                "next_step": "请等待3秒后再次调用 bili_login_check",
            }
        if state == QrCodeLoginEvents.CONF:
            return {
                "status": "confirming",
                "message": "用户已确认，正在处理...",
                "next_step": "请等待2秒后再次调用 bili_login_check",
            }
        if state == QrCodeLoginEvents.TIMEOUT:
            self.clear_qr_session()
            return {"status": "timeout", "message": "二维码已过期，请重新调用 bili_login 生成新二维码"}
        if state == QrCodeLoginEvents.DONE:
            cred = self._login_session.get_credential()
            payload = {
                "SESSDATA": getattr(cred, "sessdata", "") or "",
                "bili_jct": getattr(cred, "bili_jct", "") or "",
                "DedeUserID": str(getattr(cred, "dedeuserid", "") or ""),
                "buvid3": getattr(cred, "buvid3", "") or "",
            }
            ok = await self._credential_saver(payload)
            if not ok:
                self.clear_qr_session()
                raise RuntimeError("登录成功，但保存加密凭据失败。")
            await self._credential_reloader()
            self.clear_qr_session()

            username = ""
            try:
                from bilibili_api import user as user_module

                uid = int(getattr(cred, "dedeuserid", 0) or 0)
                if uid > 0:
                    fresh_cred = await self._credential_provider()
                    user_info = await user_module.User(uid=uid, credential=fresh_cred).get_user_info()
                    username = user_info.get("name", "")
            except Exception:
                if self.logger:
                    self.logger.warning("login user info fetch failed")

            return {
                "status": "done",
                "message": "登录成功！凭据已加密保存，现在可以使用所有B站功能了",
                "uid": payload["DedeUserID"],
                "username": username,
                "has_buvid3": bool(payload["buvid3"]),
            }
        return {"status": "unknown", "message": f"未知状态: {state}"}

    async def check_credential(self) -> Dict[str, Any]:
        credential = await self._credential_provider()
        if not credential:
            return {"logged_in": False, "message": "未登录，请调用 bili_login 进行扫码登录"}
        try:
            from bilibili_api import user

            uid = int(getattr(credential, "dedeuserid", 0) or 0)
            info = await user.User(uid=uid, credential=credential).get_user_info()
            return {
                "logged_in": True,
                "uid": str(getattr(credential, "dedeuserid", "") or ""),
                "username": info.get("name", ""),
                "message": "凭证有效",
            }
        except Exception:
            return {"logged_in": False, "message": "credential may be invalid; please login again"}
