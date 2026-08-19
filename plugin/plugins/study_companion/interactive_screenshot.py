"""Plugin-local client for the N.E.K.O interactive screenshot API."""

from __future__ import annotations

import asyncio
import base64
import binascii
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
import io
import ipaddress
import os
from typing import Any
from urllib.parse import urlparse

import httpx


_DEFAULT_HOST = "127.0.0.1"
_DEFAULT_PORT = 48911
_INTERACTIVE_SCREENSHOT_PATH = "/api/screenshot/interactive"
_ACTIVATION_DELAY_SECONDS = 2.0
_REQUEST_TIMEOUT_SECONDS = 75.0
_SESSION_TIMEOUT_MS = 45_000
_MAX_ENCODED_IMAGE_CHARS = 12 * 1024 * 1024
_MAX_DECODED_IMAGE_BYTES = 10 * 1024 * 1024
_SUPPORTED_IMAGE_MIME_TYPES = frozenset({"image/jpeg", "image/png"})
_INTERACTIVE_UNAVAILABLE_ERRORS = frozenset(
    {
        "interactive screenshot is only supported on macOS or Windows",
        "backend is configured as remote (NEKO_ACTIVITY_TRACKER_REMOTE); local interactive screenshot disabled",
    }
)


class InteractiveCaptureError(RuntimeError):
    """Safe failure from the local interactive screenshot boundary."""


@dataclass(slots=True, frozen=True)
class InteractiveCaptureResult:
    image: Any | None = None
    canceled: bool = False


def _resolve_default_base_url() -> str:
    port = os.environ.get("NEKO_MAIN_SERVER_PORT") or os.environ.get(
        "MAIN_SERVER_PORT"
    )
    try:
        port_int = int(port) if port else _DEFAULT_PORT
    except (TypeError, ValueError):
        port_int = _DEFAULT_PORT
    if not 1 <= port_int <= 65535:
        port_int = _DEFAULT_PORT
    return f"http://{_DEFAULT_HOST}:{port_int}"


def _is_loopback_host(hostname: str | None) -> bool:
    if not hostname:
        return False
    if hostname.lower() == "localhost":
        return True
    try:
        return ipaddress.ip_address(hostname).is_loopback
    except ValueError:
        return False


def _normalize_loopback_base_url(base_url: str) -> str:
    parsed = urlparse(base_url)
    if parsed.scheme not in {"http", "https"} or not _is_loopback_host(
        parsed.hostname
    ):
        raise ValueError("interactive screenshot base_url must use a loopback host")
    return base_url.rstrip("/")


def _decode_image_data_url(data_url: str) -> Any:
    if not isinstance(data_url, str) or not data_url.startswith("data:"):
        raise InteractiveCaptureError("interactive_capture: missing_image_data")
    header, separator, encoded = data_url.partition(",")
    mime_type = header[5:].split(";", 1)[0].lower()
    if (
        not separator
        or ";base64" not in header.lower()
        or mime_type not in _SUPPORTED_IMAGE_MIME_TYPES
    ):
        raise InteractiveCaptureError("interactive_capture: unsupported_image_data")
    if not encoded or len(encoded) > _MAX_ENCODED_IMAGE_CHARS:
        raise InteractiveCaptureError("interactive_capture: image_too_large")
    try:
        raw = base64.b64decode(encoded, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise InteractiveCaptureError(
            "interactive_capture: invalid_image_base64"
        ) from exc
    if not raw or len(raw) > _MAX_DECODED_IMAGE_BYTES:
        raise InteractiveCaptureError("interactive_capture: image_too_large")

    try:
        from PIL import Image  # type: ignore[import-not-found]  # noqa: PLC0415

        with Image.open(io.BytesIO(raw)) as source:
            if source.format not in {"JPEG", "PNG"}:
                raise InteractiveCaptureError(
                    "interactive_capture: unsupported_image_format"
                )
            source.load()
            return source.convert("RGB")
    except InteractiveCaptureError:
        raise
    except Exception as exc:
        raise InteractiveCaptureError("interactive_capture: invalid_image") from exc


class InteractiveScreenshotClient:
    """Async loopback client that returns only a confirmed screen selection."""

    def __init__(
        self,
        *,
        base_url: str | None = None,
        activation_delay_seconds: float = _ACTIVATION_DELAY_SECONDS,
        request_timeout_seconds: float = _REQUEST_TIMEOUT_SECONDS,
        session_timeout_ms: int = _SESSION_TIMEOUT_MS,
        lanlan_name: str | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self._base_url = _normalize_loopback_base_url(
            base_url or _resolve_default_base_url()
        )
        self._activation_delay_seconds = max(0.0, float(activation_delay_seconds))
        self._request_timeout_seconds = float(request_timeout_seconds)
        self._session_timeout_ms = int(session_timeout_ms)
        self._lanlan_name = str(lanlan_name or "").strip() or None
        self._transport = transport
        self._sleep = sleep

    async def capture_region(self) -> InteractiveCaptureResult:
        if self._activation_delay_seconds:
            await self._sleep(self._activation_delay_seconds)

        try:
            async with httpx.AsyncClient(
                timeout=self._request_timeout_seconds,
                transport=self._transport,
                trust_env=False,
            ) as client:
                request_payload: dict[str, Any] = {
                    "selection_only": True,
                    "copy_to_clipboard": False,
                    "session_timeout_ms": self._session_timeout_ms,
                }
                if self._lanlan_name is not None:
                    request_payload["lanlan_name"] = self._lanlan_name
                response = await client.post(
                    f"{self._base_url}{_INTERACTIVE_SCREENSHOT_PATH}",
                    json=request_payload,
                )
        except httpx.TimeoutException as exc:
            raise InteractiveCaptureError(
                "interactive_capture: request_timeout"
            ) from exc
        except httpx.HTTPError as exc:
            raise InteractiveCaptureError(
                "interactive_capture: main_server_unavailable"
            ) from exc

        try:
            payload = response.json()
        except ValueError as exc:
            raise InteractiveCaptureError(
                "interactive_capture: invalid_json_response"
            ) from exc
        if not isinstance(payload, dict):
            raise InteractiveCaptureError(
                "interactive_capture: invalid_json_response"
            )
        if response.status_code == 200 and payload.get("canceled") is True:
            return InteractiveCaptureResult(canceled=True)
        if response.status_code != 200 or payload.get("success") is False:
            error_code = str(payload.get("error") or f"http_status_{response.status_code}")
            if error_code == "bridge_error":
                error_code = "no_renderer"
            elif error_code in _INTERACTIVE_UNAVAILABLE_ERRORS:
                error_code = "interactive_unavailable"
            raise InteractiveCaptureError(f"interactive_capture: {error_code}")

        data_url = payload.get("data")
        if not isinstance(data_url, str) or not data_url:
            raise InteractiveCaptureError("interactive_capture: missing_image_data")
        image = await asyncio.to_thread(_decode_image_data_url, data_url)
        return InteractiveCaptureResult(image=image)


async def capture_interactive_region(
    *, lanlan_name: str | None = None
) -> InteractiveCaptureResult:
    return await InteractiveScreenshotClient(lanlan_name=lanlan_name).capture_region()
