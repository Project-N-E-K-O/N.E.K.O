"""B站私信插件的二维码登录状态机。

插件 UI 由独立服务托管，不能调用主程序的媒体凭证路由；因此在插件进程内
直接使用 ``bilibili_api.login_v2`` 完成二维码登录，并只向前端返回二维码和状态。
"""

from __future__ import annotations

import base64
import time
from typing import Any, Awaitable, Callable


CredentialSaver = Callable[[dict[str, str]], Awaitable[bool]]


class BiliDMQrLogin:
    """Small wrapper around bilibili-api's QR login session."""

    def __init__(self, *, credential_saver: CredentialSaver) -> None:
        self._credential_saver = credential_saver
        self._session: Any | None = None
        self._generated_at = 0.0

    @staticmethod
    def _require_sdk() -> tuple[Any, Any]:
        try:
            from bilibili_api.login_v2 import QrCodeLogin, QrCodeLoginEvents
        except ImportError as exc:
            raise RuntimeError("缺少 bilibili_api 依赖，无法使用扫码登录。") from exc
        return QrCodeLogin, QrCodeLoginEvents

    def clear(self) -> None:
        self._session = None
        self._generated_at = 0.0

    async def start(self) -> dict[str, Any]:
        QrCodeLogin, _ = self._require_sdk()
        self._session = QrCodeLogin()
        await self._session.generate_qrcode()
        self._generated_at = time.time()
        picture = self._session.get_qrcode_picture()
        image = base64.b64encode(picture.content).decode("ascii")
        return {
            "status": "qrcode_ready",
            "message": "请用B站 App 扫描二维码登录（180秒内有效）",
            "qrcode_image": f"data:image/png;base64,{image}",
            "timeout": 180,
        }

    async def poll(self) -> dict[str, Any]:
        if self._session is None:
            return {"status": "no_session", "message": "没有进行中的登录，请重新获取二维码"}

        _, events = self._require_sdk()
        state = await self._session.check_state()
        none_event = getattr(events, "NONE", None)
        if none_event is not None and state == none_event:
            return {"status": "waiting", "message": "等待扫码…"}
        if state == events.SCAN:
            # 部分旧版 SDK 把 SCAN（值为 0）同时作为初始状态，避免刚生成即误报。
            if none_event is None or time.time() - self._generated_at < 1.0:
                return {"status": "waiting", "message": "等待扫码…"}
            return {"status": "scanned", "message": "已扫码，请在手机上确认…"}
        if state == events.CONF:
            return {"status": "confirming", "message": "已确认，正在获取配置…"}
        if state == events.TIMEOUT:
            self.clear()
            return {"status": "expired", "message": "二维码已过期，请刷新二维码"}
        if state != events.DONE:
            return {"status": "waiting", "message": "等待扫码…"}

        credential = self._session.get_credential()
        values = {
            "sesdata": str(getattr(credential, "sessdata", "") or ""),
            "bili_jct": str(getattr(credential, "bili_jct", "") or ""),
            "buvid3": str(getattr(credential, "buvid3", "") or ""),
            "dedeuserid": str(getattr(credential, "dedeuserid", "") or ""),
        }
        if not values["sesdata"] or not values["bili_jct"]:
            self.clear()
            raise RuntimeError("登录成功但未获取到完整的 B站凭据，请重试。")
        if not await self._credential_saver(values):
            raise RuntimeError("登录成功，但保存插件配置失败。")
        self.clear()
        return {
            "status": "done",
            "message": "登录成功，配置已自动保存",
            "has_buvid3": bool(values["buvid3"]),
        }
