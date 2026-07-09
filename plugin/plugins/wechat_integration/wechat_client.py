from __future__ import annotations

import json
import secrets
import base64
from typing import Any, cast

import httpx


class WechatClient:
    """微信 OpenClaw API 客户端"""

    def __init__(
        self,
        *,
        base_url: str = "https://ilinkai.weixin.qq.com",
        cdn_base_url: str = "https://novac2c.cdn.weixin.qq.com/c2c",
        api_timeout_ms: int = 15000,
        token: str | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.cdn_base_url = cdn_base_url.rstrip("/")
        self.api_timeout_ms = api_timeout_ms
        self.token = token
        self._http_client: httpx.AsyncClient | None = None

    async def ensure_http_client(self) -> None:
        if self._http_client is None or self._http_client.is_closed:
            self._http_client = httpx.AsyncClient(
                timeout=self.api_timeout_ms / 1000,
                follow_redirects=True,
            )

    async def close(self) -> None:
        if self._http_client is not None and not self._http_client.is_closed:
            await self._http_client.aclose()
            self._http_client = None

    def _build_base_headers(self, token_required: bool = False) -> dict[str, str]:
        headers = {
            "Content-Type": "application/json",
            "AuthorizationType": "ilink_bot_token",
            "X-WECHAT-UIN": base64.b64encode(
                str(secrets.randbits(32)).encode("utf-8")
            ).decode("utf-8"),
        }
        if token_required and self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        return headers

    def _resolve_url(self, endpoint: str) -> str:
        return f"{self.base_url}/{endpoint.lstrip('/')}"

    async def request_json(
        self,
        method: str,
        endpoint: str,
        *,
        params: dict[str, Any] | None = None,
        payload: dict[str, Any] | None = None,
        token_required: bool = False,
        timeout_ms: int | None = None,
        headers: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        await self.ensure_http_client()
        assert self._http_client is not None
        req_timeout = (timeout_ms if timeout_ms is not None else self.api_timeout_ms) / 1000
        merged_headers = self._build_base_headers(token_required=token_required)
        if headers:
            merged_headers.update(headers)

        resp = await self._http_client.request(
            method,
            self._resolve_url(endpoint),
            params=params,
            json=payload,
            headers=merged_headers,
            timeout=req_timeout,
        )
        text = resp.text
        if resp.status_code >= 400:
            raise RuntimeError(f"{method} {endpoint} failed: {resp.status_code} {text}")
        if not text:
            return {}
        return cast(dict[str, Any], json.loads(text))

    async def get_qrcode(self, bot_type: str = "3") -> dict[str, Any]:
        """获取登录二维码"""
        return await self.request_json(
            "GET",
            "ilink/bot/get_bot_qrcode",
            params={"bot_type": bot_type},
            token_required=False,
            timeout_ms=15000,
        )

    async def poll_qrcode_status(self, qrcode: str) -> dict[str, Any]:
        """轮询扫码状态"""
        return await self.request_json(
            "GET",
            "ilink/bot/get_qrcode_status",
            params={"qrcode": qrcode},
            token_required=False,
            timeout_ms=35000,
            headers={"iLink-App-ClientVersion": "1"},
        )

    async def get_updates(self, sync_buf: str = "") -> dict[str, Any]:
        """长轮询拉取新消息"""
        return await self.request_json(
            "POST",
            "ilink/bot/getupdates",
            payload={
                "base_info": {"channel_version": "kiraai"},
                "get_updates_buf": sync_buf,
            },
            token_required=True,
            timeout_ms=35000,
        )

    async def send_message_payload(self, payload: dict[str, Any]) -> dict[str, Any]:
        """发送消息"""
        return await self.request_json(
            "POST",
            "ilink/bot/sendmessage",
            payload=payload,
            token_required=True,
        )
