"""B站集成插件的二维码登录状态机。

插件 UI 由独立服务托管，不能调用主程序的媒体凭证路由；因此在插件进程内
直接使用 ``bilibili_api.login_v2`` 完成二维码登录，并只向前端返回二维码和状态。
"""

from __future__ import annotations

import base64
import secrets
import time
from typing import Any, Awaitable, Callable


CredentialSaver = Callable[[dict[str, str]], Awaitable[bool]]


class BiliDMQrLogin:
    """Small wrapper around bilibili-api's QR login session."""

    def __init__(self, *, credential_saver: CredentialSaver) -> None:
        self._credential_saver = credential_saver
        self._session: Any | None = None
        self._session_id = ""
        self._generated_at = 0.0

    @staticmethod
    def _require_sdk() -> tuple[Any, Any]:
        try:
            from bilibili_api.login_v2 import QrCodeLogin, QrCodeLoginEvents
        except ImportError as exc:
            raise RuntimeError("缺少 bilibili_api 依赖，无法使用扫码登录。") from exc
        return QrCodeLogin, QrCodeLoginEvents

    def clear(self, *, session_id: str | None = None) -> bool:
        """Clear only the active QR session, optionally matched by its ID."""
        if session_id is not None and session_id != self._session_id:
            return False
        self._session = None
        self._session_id = ""
        self._generated_at = 0.0
        return True

    async def start(self) -> dict[str, Any]:
        QrCodeLogin, _ = self._require_sdk()
        session = QrCodeLogin()
        await session.generate_qrcode()
        picture = session.get_qrcode_picture()
        image = base64.b64encode(picture.content).decode("ascii")
        # Publish only a fully generated session so failed/stale starts never
        # expose a half-initialized shared session to poll/cancel requests.
        self._session = session
        self._session_id = secrets.token_urlsafe(18)
        self._generated_at = time.time()
        return {
            "status": "qrcode_ready",
            "message": "请用B站 App 扫描二维码登录（180秒内有效）",
            "qrcode_image": f"data:image/png;base64,{image}",
            "session_id": self._session_id,
            "timeout": 180,
        }

    async def poll(self) -> dict[str, Any]:
        return await self.poll_session(self._session_id)

    async def poll_session(self, session_id: str) -> dict[str, Any]:
        """Poll one specific QR session without touching a newer session."""
        session = self._session
        if session is None or not session_id or session_id != self._session_id:
            return {
                "status": "no_session",
                "message": "没有进行中的登录，请重新获取二维码",
            }

        _, events = self._require_sdk()
        state = await session.check_state()
        if self._session is not session or self._session_id != session_id:
            return {
                "status": "no_session",
                "message": "扫码会话已更新，请重新获取二维码",
            }
        none_event = getattr(events, "NONE", None)
        if none_event is not None and state == none_event:
            return {"status": "waiting", "message": "等待扫码…"}
        if state == events.SCAN:
            return {"status": "waiting", "message": "等待扫码…"}
        if state == events.CONF:
            return {"status": "scanned", "message": "已扫码，请在手机上确认…"}
        if state == events.TIMEOUT:
            self.clear(session_id=session_id)
            return {"status": "expired", "message": "二维码已过期，请刷新二维码"}
        if state != events.DONE:
            return {"status": "waiting", "message": "等待扫码…"}

        credential = session.get_credential()
        values = {
            "sesdata": str(getattr(credential, "sessdata", "") or ""),
            "bili_jct": str(getattr(credential, "bili_jct", "") or ""),
            "buvid3": str(getattr(credential, "buvid3", "") or ""),
            "dedeuserid": str(getattr(credential, "dedeuserid", "") or ""),
            # Always include the refresh token: an account switch must replace
            # an older value, and an SDK result without one must clear it.
            "ac_time_value": str(getattr(credential, "ac_time_value", "") or ""),
        }
        if not values["sesdata"] or not values["bili_jct"]:
            self.clear(session_id=session_id)
            raise RuntimeError("登录成功但未获取到完整的 B站凭据，请重试。")
        try:
            saved = await self._credential_saver(values)
        finally:
            self.clear(session_id=session_id)
        if not saved:
            raise RuntimeError("登录成功，但保存插件配置失败。")
        return {
            "status": "done",
            "message": "登录成功，配置已自动保存",
            "has_buvid3": bool(values["buvid3"]),
        }
