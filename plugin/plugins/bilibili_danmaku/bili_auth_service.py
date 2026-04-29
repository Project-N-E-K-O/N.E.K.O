from __future__ import annotations

import base64
from typing import Any, Awaitable, Callable, Dict, Optional

CredentialProvider = Callable[[], Awaitable[Optional[object]]]
CredentialSaver = Callable[[Dict[str, str]], Awaitable[bool]]
CredentialReloader = Callable[[], Awaitable[None]]
QrCleanup = Callable[[], None]


class BiliAuthService:
    def __init__(
        self,
        *,
        logger,
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

        QrCodeLogin, _ = self._require_login_sdk()
        self._login_session = QrCodeLogin()
        await self._login_session.generate_qrcode()
        pic = self._login_session.get_qrcode_picture()
        png_bytes = pic.content
        img_base64 = base64.b64encode(png_bytes).decode("utf-8")
        terminal_qr = self._login_session.get_qrcode_terminal()
        qr_url = getattr(self._login_session, "_QrCodeLogin__qr_link", "")
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
            return {
                "status": "timeout",
                "message": "二维码已过期，请重新调用 bili_login 生成新二维码",
            }
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
                raise RuntimeError("登录成功，但保存加密凭据失败。")
            await self._credential_reloader()
            self.clear_qr_session()
            return {
                "status": "done",
                "message": "登录成功！凭据已加密保存，现在可以使用所有B站功能了",
                "uid": payload["DedeUserID"],
                "has_buvid3": bool(payload["buvid3"]),
            }
        return {
            "status": "unknown",
            "message": f"未知状态: {state}",
        }

    async def check_credential(self) -> Dict[str, Any]:
        credential = await self._credential_provider()
        if not credential:
            return {
                "logged_in": False,
                "message": "未登录，请调用 bili_login 进行扫码登录",
            }
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
        except Exception as exc:
            return {
                "logged_in": False,
                "message": f"凭证可能已过期: {exc}，请重新调用 bili_login 登录",
            }
