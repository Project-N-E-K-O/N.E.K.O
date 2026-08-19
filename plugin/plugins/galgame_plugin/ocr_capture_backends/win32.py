from __future__ import annotations
import ctypes
import ctypes.wintypes
import threading
import time
from typing import Any, Callable
from ..ocr_runtime_types import DetectedGameWindow, OcrCaptureProfile, _CAPTURE_BACKEND_SMART, _CAPTURE_BACKEND_DXCAM, _CAPTURE_BACKEND_MSS, _CAPTURE_BACKEND_PRINTWINDOW, _CAPTURE_BACKEND_PYAUTOGUI, _CAPTURE_BACKEND_IMAGEGRAB, _CAPTURE_BACKEND_AUTO, _LOGGER
from ._helpers import _require_visible_capture_target, _require_visible_capture_target_win32, _target_screen_capture_rect, _crop_window_image
from .mss import MssCaptureBackend
from .pyautogui import PyAutoGuiCaptureBackend
from .printwindow import PrintWindowCaptureBackend
from .dxcam import DxcamCaptureBackend
def _rects_intersect(left: tuple[int, int, int, int], right: tuple[int, int, int, int]) -> bool:
    return max(left[0], right[0]) < min(left[2], right[2]) and max(
        left[1], right[1]
    ) < min(left[3], right[3])


def _capture_region_rect(
    target: DetectedGameWindow,
    profile: OcrCaptureProfile,
) -> tuple[int, int, int, int] | None:
    try:
        left, top, right, bottom = _target_screen_capture_rect(target)
    except Exception:
        return None
    width = max(0, int(right) - int(left))
    height = max(0, int(bottom) - int(top))
    if width <= 0 or height <= 0:
        return None
    crop_left = int(left) + round(width * float(profile.left_inset_ratio))
    crop_right = int(right) - round(width * float(profile.right_inset_ratio))
    crop_top = int(top) + round(height * float(profile.top_ratio))
    crop_bottom = int(bottom) - round(height * float(profile.bottom_inset_ratio))
    if crop_right <= crop_left or crop_bottom <= crop_top:
        return None
    return crop_left, crop_top, crop_right, crop_bottom


def _configure_user32_occlusion_signatures(user32: Any) -> None:
    signatures = (
        ("IsWindow", [ctypes.wintypes.HWND], ctypes.wintypes.BOOL),
        (
            "GetWindowThreadProcessId",
            [ctypes.wintypes.HWND, ctypes.POINTER(ctypes.wintypes.DWORD)],
            ctypes.wintypes.DWORD,
        ),
        (
            "GetWindow",
            [ctypes.wintypes.HWND, ctypes.wintypes.UINT],
            ctypes.wintypes.HWND,
        ),
        ("IsWindowVisible", [ctypes.wintypes.HWND], ctypes.wintypes.BOOL),
        ("IsIconic", [ctypes.wintypes.HWND], ctypes.wintypes.BOOL),
        (
            "GetWindowRect",
            [ctypes.wintypes.HWND, ctypes.POINTER(ctypes.wintypes.RECT)],
            ctypes.wintypes.BOOL,
        ),
    )
    for name, argtypes, restype in signatures:
        function = getattr(user32, name)
        function.argtypes = argtypes
        function.restype = restype


def _win32_capture_region_occluded(
    target: DetectedGameWindow,
    profile: OcrCaptureProfile,
) -> bool:
    """Return whether another visible process is above the target OCR region."""
    hwnd = int(getattr(target, "hwnd", 0) or 0)
    capture_rect = _capture_region_rect(target, profile)
    if hwnd <= 0 or capture_rect is None or not hasattr(ctypes, "windll"):
        return False
    user32 = ctypes.windll.user32
    try:
        _configure_user32_occlusion_signatures(user32)
        if not bool(user32.IsWindow(hwnd)):
            return False
    except Exception:
        return True

    target_pid = ctypes.wintypes.DWORD()
    try:
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(target_pid))
    except Exception:
        target_pid.value = int(getattr(target, "pid", 0) or 0)

    GW_HWNDPREV = 3
    candidate = int(user32.GetWindow(hwnd, GW_HWNDPREV) or 0)
    visited = 0
    while candidate and visited < 512:
        visited += 1
        try:
            if bool(user32.IsWindowVisible(candidate)) and not bool(user32.IsIconic(candidate)):
                candidate_pid = ctypes.wintypes.DWORD()
                user32.GetWindowThreadProcessId(candidate, ctypes.byref(candidate_pid))
                rect = ctypes.wintypes.RECT()
                if (
                    int(candidate_pid.value or 0) != int(target_pid.value or 0)
                    and bool(user32.GetWindowRect(candidate, ctypes.byref(rect)))
                    and _rects_intersect(
                        capture_rect,
                        (int(rect.left), int(rect.top), int(rect.right), int(rect.bottom)),
                    )
                ):
                    return True
            candidate = int(user32.GetWindow(candidate, GW_HWNDPREV) or 0)
        except Exception:
            return True
    return False


class Win32CaptureBackend:
    def __init__(
        self,
        *,
        logger=None,
        selection: str = _CAPTURE_BACKEND_AUTO,
        occlusion_checker: Callable[[DetectedGameWindow, OcrCaptureProfile], bool] | None = None,
    ) -> None:
        self._logger = logger
        self.selection = str(selection or _CAPTURE_BACKEND_AUTO).strip().lower()
        # Legacy "imagegrab" selection migrates to MSS (same GDI capability, faster + cross-platform).
        if self.selection == _CAPTURE_BACKEND_IMAGEGRAB:
            self.selection = _CAPTURE_BACKEND_MSS
        if self.selection not in {
            _CAPTURE_BACKEND_AUTO,
            _CAPTURE_BACKEND_SMART,
            _CAPTURE_BACKEND_DXCAM,
            _CAPTURE_BACKEND_MSS,
            _CAPTURE_BACKEND_PYAUTOGUI,
            _CAPTURE_BACKEND_PRINTWINDOW,
        }:
            self.selection = _CAPTURE_BACKEND_AUTO
        self._mss_backend = MssCaptureBackend(logger=self._logger)
        self._pyautogui_backend = PyAutoGuiCaptureBackend(logger=self._logger)
        self._printwindow_backend = PrintWindowCaptureBackend(logger=self._logger)
        self._dxcam_backend = DxcamCaptureBackend(logger=self._logger)
        # Linux-only: HTTP bridge to Electron desktopCapturer for Wayland
        # (and as XWayland/X11 fallback). Lazy import to avoid pulling
        # httpx on platforms that don't need it.
        from ..capture_platform import is_linux  # noqa: PLC0415

        if is_linux():
            from ..electron_capture import ElectronCaptureBackend  # noqa: PLC0415

            self._electron_backend: CaptureBackend | None = ElectronCaptureBackend(
                logger=self._logger
            )
        else:
            self._electron_backend = None
        self._backends = self._build_backends()
        self._last_backend_lock = threading.RLock()
        self._last_backend_kind = ""
        self._last_backend_detail = ""
        self._last_capture_region_occluded = False
        self._last_capture_content_trusted = True
        self._last_capture_untrusted_reason = ""
        self._occlusion_checker = occlusion_checker or _win32_capture_region_occluded
        self._logged_fallback_details: set[str] = set()

    @property
    def last_backend_kind(self) -> str:
        with self._last_backend_lock:
            return self._last_backend_kind

    @property
    def last_backend_detail(self) -> str:
        with self._last_backend_lock:
            return self._last_backend_detail

    @property
    def last_capture_region_occluded(self) -> bool:
        with self._last_backend_lock:
            return self._last_capture_region_occluded

    @property
    def last_capture_content_trusted(self) -> bool:
        with self._last_backend_lock:
            return self._last_capture_content_trusted

    @property
    def last_capture_untrusted_reason(self) -> str:
        with self._last_backend_lock:
            return self._last_capture_untrusted_reason

    def _set_last_backend(self, *, kind: str, detail: str) -> None:
        with self._last_backend_lock:
            self._last_backend_kind = kind
            self._last_backend_detail = detail

    def _set_last_capture_trust(
        self,
        *,
        occluded: bool,
        trusted: bool,
        reason: str = "",
    ) -> None:
        with self._last_backend_lock:
            self._last_capture_region_occluded = bool(occluded)
            self._last_capture_content_trusted = bool(trusted)
            self._last_capture_untrusted_reason = str(reason or "")

    def _build_backends(self) -> list[CaptureBackend]:
        # Default fallback chain: dxcam → mss → pyautogui (cross-platform GDI
        # progression). PrintWindow is intentionally NOT in the default chain
        # because it's a "render to DC" mechanism that often produces stale
        # frames on DirectX/Unity games and is slower than BitBlt-based
        # backends. It's still reachable as an explicit user selection
        # (mainly for capturing occluded windows) and as the Smart-mode
        # background-target backend.
        from ..capture_platform import (  # noqa: PLC0415
            is_linux,
            is_linux_wayland_session,
            is_win32_only_backend_kind,
            is_windows,
        )

        def _filter(backends: list[CaptureBackend]) -> list[CaptureBackend]:
            """Remove backends that require Win32 APIs on non-Windows hosts,
            and append the Electron HTTP bridge as a last-resort entry on Linux
            (needed for pure Wayland where MSS/PyAutoGUI return blank frames).
            """
            if is_windows():
                return list(backends)
            cross_platform = [
                b
                for b in backends
                if not is_win32_only_backend_kind(str(getattr(b, "kind", "")))
            ]
            if is_linux() and self._electron_backend is not None:
                if (
                    is_linux_wayland_session()
                    and self.selection
                    in {_CAPTURE_BACKEND_AUTO, _CAPTURE_BACKEND_SMART}
                ):
                    # On Wayland, MSS/PyAutoGUI can import successfully yet
                    # return black frames. Prefer Electron's portal-backed
                    # path for automatic selections instead of treating those
                    # import probes as capture viability.
                    return [self._electron_backend]
                # X11/XWayland: MSS/PyAutoGUI work; Electron is a tail fallback.
                cross_platform.append(self._electron_backend)
            return cross_platform

        if self.selection == _CAPTURE_BACKEND_DXCAM:
            return _filter([self._dxcam_backend, self._mss_backend, self._pyautogui_backend])
        if self.selection == _CAPTURE_BACKEND_MSS:
            return _filter([self._mss_backend, self._dxcam_backend, self._pyautogui_backend])
        if self.selection == _CAPTURE_BACKEND_PYAUTOGUI:
            return _filter([self._pyautogui_backend, self._dxcam_backend, self._mss_backend])
        if self.selection == _CAPTURE_BACKEND_PRINTWINDOW:
            return _filter(
                [
                    self._printwindow_backend,
                    self._dxcam_backend,
                    self._mss_backend,
                    self._pyautogui_backend,
                ]
            )
        if self.selection == _CAPTURE_BACKEND_SMART:
            return _filter(
                [
                    self._dxcam_backend,
                    self._mss_backend,
                    self._pyautogui_backend,
                    self._printwindow_backend,
                ]
            )
        return _filter([self._dxcam_backend, self._mss_backend, self._pyautogui_backend])

    def _ordered_backends_for_target(
        self,
        target: DetectedGameWindow,
        *,
        capture_region_occluded: bool = False,
    ) -> list[CaptureBackend]:
        from ..capture_platform import is_linux, is_linux_wayland_session, is_windows  # noqa: PLC0415

        is_windows_host = is_windows()
        is_linux_host = is_linux()
        is_wayland_host = is_linux_wayland_session() if is_linux_host else False
        window_level_backends = [
            backend
            for backend in self._backends
            if str(getattr(backend, "kind", "")) == "electron"
        ]
        pixel_backends = [
            backend
            for backend in self._backends
            if str(getattr(backend, "kind", "")) not in {"electron", _CAPTURE_BACKEND_PRINTWINDOW}
        ]
        if self.selection == _CAPTURE_BACKEND_PRINTWINDOW:
            if not (
                is_windows_host and self._printwindow_backend in self._backends
            ):
                return list(self._backends)
            # Explicit PrintWindow selection: user is opting into the only
            # backend that can capture occluded / background windows. If we
            # silently fell through to dxcam/mss/pyautogui on a background
            # target after PrintWindow failed, those backends read whatever
            # is on screen — usually the occluding window — and OCR would
            # produce confident garbage from the wrong source. Match Smart
            # mode's strictness for background targets here.
            if bool(getattr(target, "is_minimized", False)):
                raise RuntimeError("printwindow: target_window_minimized_for_capture")
            if bool(getattr(target, "is_foreground", False)):
                if capture_region_occluded:
                    return [self._printwindow_backend]
                # Foreground: other backends would also see the right window,
                # so falling through after PrintWindow failure is safe only
                # when the requested capture region is not visibly occluded.
                return list(self._backends)
            return [self._printwindow_backend]
        if self.selection != _CAPTURE_BACKEND_SMART:
            return list(self._backends)
        if bool(getattr(target, "is_minimized", False)):
            raise RuntimeError("smart: target_window_minimized_for_capture")
        if not is_windows_host:
            if not is_linux_host or not is_wayland_host:
                return list(self._backends)
            if window_level_backends:
                return window_level_backends
            raise RuntimeError("smart: background_capture_requires_window_backend")
        if bool(getattr(target, "is_foreground", False)) and not capture_region_occluded:
            foreground_backends = {
                id(self._dxcam_backend),
                id(self._mss_backend),
                id(self._pyautogui_backend),
            }
            return [
                backend
                for backend in self._backends
                if id(backend) in foreground_backends
            ]
        # Background or visibly occluded target: PrintWindow is the only backend that can plausibly
        # capture occluded windows (others read screen pixels and would grab
        # the overlapping window). Quality is unreliable; ocr_reader emits
        # `backend_not_suitable_for_background` warning when it returns empty.
        return [self._printwindow_backend]

    def is_available(self) -> bool:
        return any(backend.is_available() for backend in self._backends)

    def describe_target(self, target: DetectedGameWindow) -> str:
        return f"{target.process_name}({target.pid}) {target.title}"

    def capture_frame(self, target: DetectedGameWindow, profile: OcrCaptureProfile) -> Any:
        from ..capture_platform import is_windows  # noqa: PLC0415

        errors: list[str] = []
        capture_region_occluded = False
        pixel_only_selection = self.selection in {
            _CAPTURE_BACKEND_AUTO,
            _CAPTURE_BACKEND_DXCAM,
            _CAPTURE_BACKEND_MSS,
            _CAPTURE_BACKEND_PYAUTOGUI,
        }
        selection_may_fallback_to_pixels = (
            pixel_only_selection or self.selection == _CAPTURE_BACKEND_PRINTWINDOW
        )
        should_check_occlusion = bool(getattr(target, "is_foreground", False)) and (
            self.selection == _CAPTURE_BACKEND_SMART
            or (is_windows() and selection_may_fallback_to_pixels)
        )
        if should_check_occlusion:
            try:
                capture_region_occluded = bool(self._occlusion_checker(target, profile))
            except Exception:
                capture_region_occluded = True
        if capture_region_occluded and pixel_only_selection:
            untrusted_reason = "capture_region_occluded_by_other_window"
            self._set_last_backend(kind=self.selection, detail=untrusted_reason)
            self._set_last_capture_trust(
                occluded=True,
                trusted=False,
                reason=untrusted_reason,
            )
            raise RuntimeError(f"{self.selection}: {untrusted_reason}")
        backends = self._ordered_backends_for_target(
            target,
            capture_region_occluded=capture_region_occluded,
        )
        selected_kind = (
            str(getattr(backends[0], "kind", self.selection))
            if backends
            else self.selection
        )
        pending_backends = list(backends)
        while pending_backends:
            backend = pending_backends.pop(0)
            kind = str(getattr(backend, "kind", backend.__class__.__name__))
            if not backend.is_available():
                errors.append(f"{kind}_unavailable")
                continue
            try:
                frame = backend.capture_frame(target, profile)
                frame_info = getattr(frame, "info", None)
                window_level_capture = kind in {_CAPTURE_BACKEND_PRINTWINDOW, "electron"}
                if should_check_occlusion and not window_level_capture:
                    try:
                        capture_region_occluded = bool(self._occlusion_checker(target, profile))
                    except Exception:
                        capture_region_occluded = True
                    if capture_region_occluded:
                        untrusted_reason = "capture_region_occluded_by_other_window"
                        self._set_last_backend(kind=kind, detail=untrusted_reason)
                        self._set_last_capture_trust(
                            occluded=True,
                            trusted=False,
                            reason=untrusted_reason,
                        )
                        raise RuntimeError(f"{kind}: {untrusted_reason}")
                frame_backend_detail = (
                    str(frame_info.get("galgame_capture_backend_detail") or "")
                    if isinstance(frame_info, dict)
                    else ""
                )
                fallback_detail = (
                    f"{selected_kind}_unavailable_fallback"
                    if kind != selected_kind and f"{selected_kind}_unavailable" in errors
                    else f"{selected_kind}_failed_fallback"
                    if kind != selected_kind
                    and any(error.startswith(f"{selected_kind}_failed:") for error in errors)
                    else ""
                )
                last_backend_detail = fallback_detail or frame_backend_detail or (
                    "dxcam_unavailable_fallback"
                    if kind != _CAPTURE_BACKEND_DXCAM and "dxcam_unavailable" in errors
                    else "dxcam_failed_fallback"
                    if kind != _CAPTURE_BACKEND_DXCAM
                    and any(error.startswith("dxcam_failed:") for error in errors)
                    else "selected"
                )
                self._set_last_backend(kind=kind, detail=last_backend_detail)
                capture_content_trusted = not capture_region_occluded or window_level_capture
                capture_untrusted_reason = (
                    "capture_region_occluded_by_other_window"
                    if not capture_content_trusted
                    else ""
                )
                self._set_last_capture_trust(
                    occluded=capture_region_occluded,
                    trusted=capture_content_trusted,
                    reason=capture_untrusted_reason,
                )
                if isinstance(frame_info, dict):
                    frame_info["galgame_capture_backend_kind"] = kind
                    frame_info["galgame_capture_backend_detail"] = last_backend_detail
                    frame_info["galgame_target_foreground"] = bool(
                        getattr(target, "is_foreground", False)
                    )
                    frame_info["galgame_capture_region_occluded"] = capture_region_occluded
                    frame_info["galgame_capture_content_trusted"] = capture_content_trusted
                    frame_info["galgame_capture_untrusted_reason"] = capture_untrusted_reason
                if fallback_detail:
                    self._warn_fallback_once(selected_kind, kind, fallback_detail)
                return frame
            except Exception as exc:
                errors.append(f"{kind}_failed:{exc}")
                fatal_target_failure = any(
                    marker in str(exc)
                    for marker in (
                        "target_window_not_resolved_for_capture",
                        "target_window_invalid_for_capture",
                        "target_window_not_visible_for_capture",
                        "target_window_minimized_for_capture",
                    )
                )
                screen_foreground_failure = any(
                    marker in str(exc)
                    for marker in (
                        "target_not_foreground_for_screen_capture",
                        "foreground_changed_during_screen_capture",
                        "capture_region_occluded_by_other_window",
                    )
                )
                if screen_foreground_failure:
                    if (
                        self.selection == _CAPTURE_BACKEND_SMART
                        and backend is not self._printwindow_backend
                        and self._printwindow_backend in self._backends
                    ):
                        pending_backends = [self._printwindow_backend]
                        continue
                    raise
                if fatal_target_failure:
                    raise
                continue
        if self.selection == _CAPTURE_BACKEND_SMART and not bool(
            getattr(target, "is_foreground", False)
        ):
            self._set_last_backend(kind=_CAPTURE_BACKEND_SMART, detail="background_requires_printwindow")
            raise RuntimeError(
                "smart: background_capture_requires_printwindow"
                + (f": {'; '.join(errors)}" if errors else "")
            )
        self._set_last_capture_trust(
            occluded=capture_region_occluded,
            trusted=False,
            reason="capture_backend_unavailable",
        )
        if self.selection != _CAPTURE_BACKEND_AUTO:
            raise RuntimeError(
                f"{self.selection}: capture_backend_unavailable"
                + (f": {'; '.join(errors)}" if errors else "")
            )
        raise RuntimeError("; ".join(errors) or "capture_backend_unavailable")

    def _warn_fallback_once(self, selected_kind: str, actual_kind: str, detail: str) -> None:
        if detail in self._logged_fallback_details:
            return
        self._logged_fallback_details.add(detail)
        if self._logger is None:
            return
        try:
            self._logger.warning(
                "ocr_reader capture backend {} unavailable/failed; falling back to {} ({})",
                selected_kind,
                actual_kind,
                detail,
            )
        except Exception:
            pass
