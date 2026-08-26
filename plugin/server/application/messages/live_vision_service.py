from __future__ import annotations

import asyncio
import math

import httpx

from plugin.logging_config import get_logger
from utils.plugin_host_auth import LIVE_FRAME_TOKEN_HEADER, plugin_host_auth_headers

logger = get_logger("server.application.messages.live_vision")

DEFAULT_TIMEOUT_SECONDS = 3.0
MAX_TIMEOUT_SECONDS = 15.0

# What a plugin sees when nobody is sharing, main_server is down, or the probe
# misbehaves. Every failure collapses to this on purpose: the caller polls on a
# timer, so raising would turn a transient hiccup into a log flood, and the
# honest answer to "can you see my screen" during an outage is "no".
_INACTIVE: dict[str, object] = {
    "active": False,
    "source": "",
    "age_seconds": None,
    "native_vision": False,
    "role": "",
}


def _coerce_timeout(value: object) -> float:
    if isinstance(value, bool):
        return DEFAULT_TIMEOUT_SECONDS
    if isinstance(value, (int, float)):
        timeout = float(value)
        if math.isfinite(timeout) and timeout > 0:
            return min(timeout, MAX_TIMEOUT_SECONDS)
    return DEFAULT_TIMEOUT_SECONDS


def _main_server_base_url() -> str:
    from config import MAIN_SERVER_PORT

    return f"http://127.0.0.1:{int(MAIN_SERVER_PORT)}"


class LiveVisionQueryService:
    """Ask main_server whether a screen share is feeding the conversation."""

    def __init__(self) -> None:
        self._permission_lock = asyncio.Lock()
        self._active_permissions: dict[
            tuple[str, str],
            dict[str, object],
        ] = {}
        self._revoked_host_generations: set[tuple[str, str]] = set()

    async def _post_permission_revoke(
        self,
        *,
        source_name: str,
        host_generation: str,
        timeout: float,
    ) -> dict[str, object]:
        url = f"{_main_server_base_url()}/api/system/plugin-permissions/revoke"
        try:
            async with httpx.AsyncClient(
                timeout=timeout, proxy=None, trust_env=False
            ) as client:
                response = await client.post(
                    url,
                    headers=plugin_host_auth_headers(),
                    json={
                        "source_name": source_name,
                        "host_generation": host_generation,
                    },
                )
                response.raise_for_status()
                payload = response.json()
        except (httpx.HTTPError, ValueError, OSError, RuntimeError) as exc:
            logger.debug(
                "plugin permission revoke unavailable: err_type={}, err={}",
                type(exc).__name__,
                str(exc),
            )
            raise RuntimeError("plugin permission revoke unavailable") from exc

        if not isinstance(payload, dict) or not payload.get("ok"):
            raise RuntimeError("plugin permission revoke rejected")
        return payload

    async def _set_permission(
        self,
        *,
        kind: str,
        path: str,
        source_name: object,
        host_generation: object,
        token: object,
        enabled: object,
        timeout: object,
        remember: bool,
    ) -> dict[str, object]:
        normalized_timeout = _coerce_timeout(timeout)
        source = str(source_name or "")
        generation = str(host_generation or "")
        permission_token = str(token or "")
        allowed = bool(enabled)
        url = f"{_main_server_base_url()}{path}"
        try:
            async with httpx.AsyncClient(
                timeout=normalized_timeout, proxy=None, trust_env=False
            ) as client:
                response = await client.post(
                    url,
                    headers=plugin_host_auth_headers(),
                    json={
                        "source_name": source,
                        "host_generation": generation,
                        "token": permission_token,
                        "enabled": allowed,
                    },
                )
                response.raise_for_status()
                payload = response.json()
        except (httpx.HTTPError, ValueError, OSError, RuntimeError) as exc:
            logger.debug(
                "{} permission update unavailable: err_type={}, err={}",
                kind,
                type(exc).__name__,
                str(exc),
            )
            raise RuntimeError(f"{kind} permission update unavailable") from exc

        if not isinstance(payload, dict) or not payload.get("ok"):
            raise RuntimeError(f"{kind} permission update rejected")
        result = {
            "ok": True,
            "source_name": str(payload.get("source_name") or source),
            "token": str(payload.get("token") or permission_token),
            "enabled": bool(payload.get("enabled")),
            "applied": bool(payload.get("applied")),
        }
        if remember and result["applied"]:
            key = (kind, source)
            if allowed and source and generation and permission_token:
                self._active_permissions[key] = {
                    "kind": kind,
                    "path": path,
                    "source_name": source,
                    "host_generation": generation,
                    "token": permission_token,
                    "enabled": True,
                }
            else:
                current = self._active_permissions.get(key)
                if (
                    current is not None
                    and current.get("host_generation") == generation
                    and current.get("token") == permission_token
                ):
                    self._active_permissions.pop(key, None)
        return result

    async def rehydrate_active_permissions(
        self,
        *,
        timeout: object = 1.0,
    ) -> int:
        """Replay revocations and active grants after a main_server restart."""
        restored = 0
        normalized_timeout = _coerce_timeout(timeout)
        async with self._permission_lock:
            for source, generation in tuple(self._revoked_host_generations):
                try:
                    await self._post_permission_revoke(
                        source_name=source,
                        host_generation=generation,
                        timeout=normalized_timeout,
                    )
                except RuntimeError:
                    continue
            for grant in tuple(self._active_permissions.values()):
                try:
                    result = await self._set_permission(
                        **grant,
                        timeout=normalized_timeout,
                        remember=False,
                    )
                except RuntimeError:
                    continue
                if result.get("applied"):
                    restored += 1
        return restored

    async def revoke_inactive_permissions(
        self,
        active_host_generations: dict[str, str],
        *,
        timeout: object = 1.0,
    ) -> int:
        """Drop cached grants whose exact plugin host is no longer alive."""
        normalized_active = {
            str(source or "").strip(): str(generation or "").strip()
            for source, generation in active_host_generations.items()
            if str(source or "").strip() and str(generation or "").strip()
        }
        normalized_timeout = _coerce_timeout(timeout)
        async with self._permission_lock:
            stale_hosts = {
                (
                    str(grant.get("source_name") or "").strip(),
                    str(grant.get("host_generation") or "").strip(),
                )
                for grant in self._active_permissions.values()
                if normalized_active.get(
                    str(grant.get("source_name") or "").strip()
                )
                != str(grant.get("host_generation") or "").strip()
            }
            stale_hosts.discard(("", ""))
            for source, generation in stale_hosts:
                self._revoked_host_generations.add((source, generation))
                for key, grant in tuple(self._active_permissions.items()):
                    if (
                        grant.get("source_name") == source
                        and grant.get("host_generation") == generation
                    ):
                        self._active_permissions.pop(key, None)
                try:
                    await self._post_permission_revoke(
                        source_name=source,
                        host_generation=generation,
                        timeout=normalized_timeout,
                    )
                except RuntimeError:
                    continue
        return len(stale_hosts)

    async def get_live_vision(
        self,
        *,
        source_name: object = "",
        host_generation: object = "",
        token: object = "",
        role: object = "",
        include_frame: object = False,
        timeout: object = None,
    ) -> dict[str, object]:
        normalized_timeout = _coerce_timeout(timeout)
        params: dict[str, str] = {}
        headers: dict[str, str] = {}
        if isinstance(role, str) and role.strip():
            params["role"] = role.strip()
        if include_frame:
            params["include_frame"] = "true"
            params["source_name"] = str(source_name or "").strip()
            params["host_generation"] = str(host_generation or "").strip()
            headers[LIVE_FRAME_TOKEN_HEADER] = str(token or "").strip()

        url = f"{_main_server_base_url()}/api/system/live-vision"
        try:
            async with httpx.AsyncClient(
                timeout=normalized_timeout, proxy=None, trust_env=False
            ) as client:
                response = await client.get(url, params=params, headers=headers)
                response.raise_for_status()
                payload = response.json()
        except (httpx.HTTPError, ValueError, OSError, RuntimeError) as exc:
            logger.debug(
                "live vision probe unavailable: err_type={}, err={}",
                type(exc).__name__,
                str(exc),
            )
            return dict(_INACTIVE)

        if not isinstance(payload, dict) or not payload.get("ok"):
            return dict(_INACTIVE)

        result: dict[str, object] = {
            "active": bool(payload.get("active")),
            "source": str(payload.get("source") or ""),
            "age_seconds": payload.get("age_seconds"),
            "native_vision": bool(payload.get("native_vision")),
            "role": str(payload.get("role") or ""),
        }
        frame = payload.get("frame_b64")
        if isinstance(frame, str) and frame:
            result["frame_b64"] = frame
            result["frame_mime"] = str(payload.get("frame_mime") or "image/jpeg")
        return result

    async def set_live_frame_permission(
        self,
        *,
        source_name: object = "",
        host_generation: object = "",
        token: object = "",
        enabled: object = False,
        timeout: object = None,
    ) -> dict[str, object]:
        async with self._permission_lock:
            return await self._set_permission(
                kind="live frame",
                path="/api/system/live-vision/attachment-permission",
                source_name=source_name,
                host_generation=host_generation,
                token=token,
                enabled=enabled,
                timeout=timeout,
                remember=True,
            )

    async def set_plugin_delivery_permission(
        self,
        *,
        source_name: object = "",
        host_generation: object = "",
        token: object = "",
        enabled: object = False,
        timeout: object = None,
    ) -> dict[str, object]:
        async with self._permission_lock:
            return await self._set_permission(
                kind="plugin delivery",
                path="/api/system/plugin-callbacks/delivery-permission",
                source_name=source_name,
                host_generation=host_generation,
                token=token,
                enabled=enabled,
                timeout=timeout,
                remember=True,
            )

    async def revoke_plugin_permissions(
        self,
        *,
        source_name: object = "",
        host_generation: object = "",
        timeout: object = None,
    ) -> dict[str, object]:
        normalized_timeout = _coerce_timeout(timeout)
        source = str(source_name or "")
        generation = str(host_generation or "")
        async with self._permission_lock:
            if source and generation:
                self._revoked_host_generations.add((source, generation))
            for key, grant in tuple(self._active_permissions.items()):
                if key[1] != source:
                    continue
                if generation and grant.get("host_generation") != generation:
                    continue
                self._active_permissions.pop(key, None)
            return await self._post_permission_revoke(
                source_name=source,
                host_generation=generation,
                timeout=normalized_timeout,
            )


live_vision_query_service = LiveVisionQueryService()
