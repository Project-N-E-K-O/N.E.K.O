from __future__ import annotations

import math

import httpx

from plugin.logging_config import get_logger

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

    async def get_live_vision(
        self,
        *,
        role: object = "",
        include_frame: object = False,
        timeout: object = None,
    ) -> dict[str, object]:
        normalized_timeout = _coerce_timeout(timeout)
        params: dict[str, str] = {}
        if isinstance(role, str) and role.strip():
            params["role"] = role.strip()
        if include_frame:
            params["include_frame"] = "true"

        url = f"{_main_server_base_url()}/api/system/live-vision"
        try:
            async with httpx.AsyncClient(
                timeout=normalized_timeout, proxy=None, trust_env=False
            ) as client:
                response = await client.get(url, params=params)
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
