from __future__ import annotations

from typing import Any, Dict, Optional

import httpx


class STS2ClientError(RuntimeError):
    pass


class STS2ApiClient:
    def __init__(self, base_url: str, *, connect_timeout: float = 5.0, request_timeout: float = 15.0) -> None:
        self.base_url = base_url.rstrip("/")
        self.connect_timeout = connect_timeout
        self.request_timeout = request_timeout
        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(connect=request_timeout if connect_timeout <= 0 else connect_timeout, read=request_timeout, write=request_timeout, pool=request_timeout),
            follow_redirects=False,
        )

    async def close(self) -> None:
        await self._client.aclose()

    async def health(self) -> Dict[str, Any]:
        return await self._request("GET", "/health")

    async def get_state(self) -> Dict[str, Any]:
        return await self._request("GET", "/state")

    async def get_available_actions(self) -> Dict[str, Any]:
        return await self._request("GET", "/actions/available")

    async def execute_action(self, action: str, **kwargs: Any) -> Dict[str, Any]:
        return await self._request("POST", "/action", json=self._build_action_payload(action, **kwargs))

    def _build_action_payload(self, action_name: str, **kwargs: Any) -> Dict[str, Any]:
        payload: Dict[str, Any] = {"action": action_name}
        for key, value in kwargs.items():
            if value is None or key in {"type", "action"}:
                continue
            payload[key] = value
        return payload

    async def _request(self, method: str, path: str, **kwargs: Any) -> Dict[str, Any]:
        url = f"{self.base_url}{path}"
        try:
            response = await self._client.request(method, url, **kwargs)
        except httpx.HTTPError as exc:
            raise STS2ClientError(f"无法连接 STS2-Agent: {exc}") from exc

        try:
            payload = response.json()
        except ValueError as exc:
            raise STS2ClientError(f"STS2-Agent 返回了无效 JSON: {url}") from exc

        if not isinstance(payload, dict):
            raise STS2ClientError(f"STS2-Agent 返回了非对象 JSON: {url}")

        if response.status_code >= 400 or payload.get("ok") is False:
            error = payload.get("error")
            if isinstance(error, dict):
                raise STS2ClientError(str(error.get("message") or error.get("code") or f"HTTP {response.status_code}"))
            raise STS2ClientError(f"STS2-Agent 请求失败: HTTP {response.status_code}")

        data = payload.get("data")
        return data if isinstance(data, dict) else {"value": data}
