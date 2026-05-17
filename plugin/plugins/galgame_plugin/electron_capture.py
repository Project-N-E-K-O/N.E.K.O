"""Electron desktopCapturer HTTP bridge backend (Linux Wayland path).

On pure Wayland, MSS/PyAutoGUI return blank frames because Wayland forbids
cross-application screen reads. Electron's `desktopCapturer` is the only
legitimate path because Chromium negotiates with PipeWire +
xdg-desktop-portal, which both GNOME and KDE support. This backend asks
the Electron renderer (via the N.E.K.O main API) to capture and returns
the decoded PNG bytes.

X11/XWayland callers should keep MSS/PyAutoGUI as the primary path; this
backend appears in the chain only as a tail fallback there.
"""

from __future__ import annotations

import base64
import ipaddress
import io
import os
import re
from typing import TYPE_CHECKING, Any
from urllib.parse import urlparse

if TYPE_CHECKING:
    from .ocr_reader import DetectedGameWindow, OcrCaptureProfile


_DEFAULT_HOST = "127.0.0.1"
_DEFAULT_PORT_FALLBACK = 48911  # MAIN_SERVER_PORT default
_DEFAULT_HEALTH_PATH = "/api/capture/health"
_DEFAULT_SCREENSHOT_PATH = "/api/capture/screenshot"
_MAX_ERROR_BODY_CHARS = 120
_TARGET_TITLE_MAX_CHARS = 512
_SAFE_TITLE_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")


def _resolve_default_base_url() -> str:
    """Pick the local N.E.K.O main server URL.

    Respects NEKO_MAIN_SERVER_PORT (launcher.DEFAULT_PORTS key) so the
    backend follows whichever port the running session chose, with a
    fallback to the documented default 48911.
    """
    port = os.environ.get("NEKO_MAIN_SERVER_PORT") or os.environ.get(
        "MAIN_SERVER_PORT"
    )
    try:
        port_int = int(port) if port else _DEFAULT_PORT_FALLBACK
    except (TypeError, ValueError):
        port_int = _DEFAULT_PORT_FALLBACK
    if not 1 <= port_int <= 65535:
        port_int = _DEFAULT_PORT_FALLBACK
    # API URLs intentionally have no trailing slash per project convention.
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
    if parsed.scheme not in {"http", "https"} or not _is_loopback_host(parsed.hostname):
        raise ValueError("electron capture base_url must use a loopback host")
    return base_url.rstrip("/")


class ElectronCaptureBackend:
    """CaptureBackend protocol implementation backed by Electron renderer."""

    kind = "electron"

    def __init__(
        self,
        *,
        logger=None,
        base_url: str | None = None,
        request_timeout: float = 5.0,
        health_timeout: float = 2.0,
    ) -> None:
        self._logger = logger
        self._base_url = _normalize_loopback_base_url(
            base_url or _resolve_default_base_url()
        )
        self._request_timeout = float(request_timeout)
        self._health_timeout = float(health_timeout)
        self._available_cached: bool | None = None

    def _validated_base_url(self) -> str:
        self._base_url = _normalize_loopback_base_url(self._base_url)
        return self._base_url

    def is_available(self) -> bool:
        """Probe the Electron bridge endpoint with a short timeout.

        Caching the negative result for the lifetime of the backend would
        starve recovery when the Electron app comes online later, so we
        re-probe on every call. The probe itself is cheap (HTTP HEAD-like
        GET to /api/capture/health with a 2s timeout).
        """
        try:
            import httpx  # type: ignore[import-not-found]  # noqa: PLC0415
        except ImportError:
            return False
        try:
            resp = httpx.request(
                "GET",
                f"{self._validated_base_url()}{_DEFAULT_HEALTH_PATH}",
                timeout=self._health_timeout,
            )
            return resp.status_code == 200
        except Exception:
            return False

    def describe_target(self, target: "DetectedGameWindow") -> str:
        return f"{target.process_name}({target.pid}) {target.title}"

    @staticmethod
    def _target_payload(target: "DetectedGameWindow") -> dict[str, Any]:
        target_id = int(getattr(target, "hwnd", 0) or 0)
        pid = int(getattr(target, "pid", 0) or 0)
        if target_id <= 0:
            raise RuntimeError("electron: invalid_target_id")
        if pid < 0:
            raise RuntimeError("electron: invalid_target_pid")
        title = _SAFE_TITLE_RE.sub("", str(getattr(target, "title", "") or ""))
        title = title[:_TARGET_TITLE_MAX_CHARS]
        return {"target_id": target_id, "pid": pid, "title": title}

    def capture_frame(
        self,
        target: "DetectedGameWindow",
        profile: "OcrCaptureProfile",
    ) -> Any:
        """Request a screenshot from Electron and return a PIL Image.

        Uses target.hwnd (CGWindowID on macOS / X11 Window ID / Windows hWnd)
        as the cross-platform window identifier. The Electron side maps it
        back to a desktopCapturer source. We deliberately do NOT pass a
        rect — Electron returns the full window image, and the caller
        crops by OcrCaptureProfile ratios. This sidesteps the HiDPI /
        multi-monitor coordinate mismatch between Chromium physical pixels
        and X11 logical coordinates.
        """
        try:
            import httpx  # type: ignore[import-not-found]  # noqa: PLC0415
        except ImportError as exc:
            raise RuntimeError("electron: httpx_not_installed") from exc
        try:
            from PIL import Image  # type: ignore[import-not-found]  # noqa: PLC0415
        except ImportError as exc:
            raise RuntimeError("electron: pillow_not_installed") from exc

        payload = self._target_payload(target)
        base_url = self._validated_base_url()
        try:
            resp = httpx.request(
                "POST",
                f"{base_url}{_DEFAULT_SCREENSHOT_PATH}",
                json=payload,
                timeout=self._request_timeout,
            )
        except Exception as exc:
            raise RuntimeError(f"electron: http_request_failed: {exc}") from exc

        if resp.status_code != 200:
            detail = (resp.text or "").strip().replace("\n", " ")
            detail = _SAFE_TITLE_RE.sub("", detail)[:_MAX_ERROR_BODY_CHARS]
            suffix = f": {detail}" if detail else ""
            raise RuntimeError(f"electron: http_status_{resp.status_code}{suffix}")

        try:
            data = resp.json()
        except Exception as exc:
            raise RuntimeError(f"electron: invalid_json_response: {exc}") from exc

        image_b64 = data.get("image") if isinstance(data, dict) else None
        if not image_b64:
            raise RuntimeError("electron: missing_image_field")
        if isinstance(image_b64, str) and image_b64.startswith("data:"):
            # Strip "data:image/png;base64," prefix if the frontend sent
            # the raw data URL rather than just the payload.
            _, _, image_b64 = image_b64.partition(",")
        try:
            png_bytes = base64.b64decode(image_b64)
        except Exception as exc:
            raise RuntimeError(f"electron: base64_decode_failed: {exc}") from exc

        try:
            return Image.open(io.BytesIO(png_bytes)).convert("RGB")
        except Exception as exc:
            raise RuntimeError(f"electron: pil_decode_failed: {exc}") from exc
