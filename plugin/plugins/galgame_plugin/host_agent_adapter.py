from __future__ import annotations

import asyncio
import json
from typing import Any
from urllib.parse import urlparse

import httpx

try:
    from config import TOOL_SERVER_PORT as _TOOL_SERVER_PORT
except Exception:
    _TOOL_SERVER_PORT = 48915

_LOCAL_TOOL_SERVER_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})


def _tls_verify_for_base_url(base_url: str) -> bool:
    parsed = urlparse(str(base_url or ""))
    host = str(parsed.hostname or "").strip().lower()
    if host in _LOCAL_TOOL_SERVER_HOSTS:
        return False
    return True


class HostAgentError(RuntimeError):
    pass


class HostAgentAdapter:
    def __init__(self, logger, *, tool_server_port: int | None = None) -> None:
        self._logger = logger
        self._tool_server_port = int(tool_server_port or _TOOL_SERVER_PORT)
        self._client: httpx.AsyncClient | None = None
        self._client_loop: asyncio.AbstractEventLoop | None = None

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self._tool_server_port}"

    def _build_client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            timeout=5.0,
            proxy=None,
            trust_env=False,
            transport=httpx.AsyncHTTPTransport(
                verify=_tls_verify_for_base_url(self.base_url),
                retries=0,
            ),
        )

    async def _get_client(self) -> httpx.AsyncClient:
        current_loop = asyncio.get_running_loop()
        if self._client is not None and self._client_loop is not current_loop:
            previous_loop = self._client_loop
            await self._reset_client(
                reason=(
                    "loop switch "
                    f"{id(previous_loop) if previous_loop is not None else 0}"
                    f"->{id(current_loop)}"
                )
            )
        if self._client is None or self._client.is_closed:
            self._client = self._build_client()
            self._client_loop = current_loop
        return self._client

    async def shutdown(self) -> None:
        await self._reset_client(reason="shutdown")

    async def _reset_client(self, *, reason: str) -> None:
        client = self._client
        self._client = None
        self._client_loop = None
        if client is None or client.is_closed:
            return
        try:
            await client.aclose()
        except Exception as exc:
            self._logger.debug(
                "HostAgentAdapter client close skipped during {}: {}",
                reason,
                exc,
            )

    async def _request_json(
        self,
        method: str,
        path: str,
        *,
        payload: dict[str, Any] | None = None,
        timeout: float,
    ) -> dict[str, Any]:
        url = f"{self.base_url}{path}"
        last_exc: Exception | None = None
        for attempt in range(2):
            client = await self._get_client()
            try:
                response = await client.request(
                    method=method,
                    url=url,
                    json=payload,
                    timeout=timeout,
                )
                break
            except Exception as exc:
                last_exc = exc
                if attempt == 0 and self._is_loop_affinity_error(exc):
                    self._logger.warning(
                        "HostAgentAdapter rebuilding AsyncClient after loop-affinity error on {} {}: {}",
                        method,
                        path,
                        exc,
                    )
                    await self._reset_client(reason=f"loop-affinity error on {method} {path}")
                    continue
                raise HostAgentError(f"{method} {path} failed: {exc}") from exc
        else:
            assert last_exc is not None
            raise HostAgentError(f"{method} {path} failed: {last_exc}") from last_exc

        try:
            data = response.json()
        except json.JSONDecodeError as exc:
            raise HostAgentError(
                f"{method} {path} returned non-json payload: HTTP {response.status_code}"
            ) from exc

        if not response.is_success:
            raise HostAgentError(
                f"{method} {path} responded {response.status_code}: "
                f"{data.get('detail') or data.get('error') or data}"
            )
        if not isinstance(data, dict):
            raise HostAgentError(
                f"{method} {path} returned invalid payload type: {type(data)!r}"
            )
        return data

    @staticmethod
    def _is_loop_affinity_error(exc: Exception) -> bool:
        message = str(exc or "")
        return any(
            token in message
            for token in (
                "Event loop is closed",
                "bound to a different event loop",
                "attached to a different loop",
            )
        )

    async def get_computer_use_availability(self, *, timeout: float = 1.5) -> dict[str, Any]:
        return await self._request_json(
            "GET",
            "/computer_use/availability",
            timeout=timeout,
        )

    async def run_computer_use_instruction(
        self,
        instruction: str,
        *,
        lanlan_name: str = "",
        timeout: float = 5.0,
    ) -> dict[str, Any]:
        payload = {"instruction": instruction.strip()}
        if lanlan_name:
            payload["lanlan_name"] = lanlan_name
        return await self._request_json(
            "POST",
            "/computer_use/run",
            payload=payload,
            timeout=timeout,
        )

    async def get_task(self, task_id: str, *, timeout: float = 2.0) -> dict[str, Any]:
        return await self._request_json(
            "GET",
            f"/tasks/{task_id}",
            timeout=timeout,
        )

    async def cancel_task(self, task_id: str, *, timeout: float = 5.0) -> dict[str, Any]:
        return await self._request_json(
            "POST",
            f"/tasks/{task_id}/cancel",
            timeout=timeout,
        )
