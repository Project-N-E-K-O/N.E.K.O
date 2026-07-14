from __future__ import annotations

from typing import Optional


_DISPLAY_UNAVAILABLE_TOKENS = (
    "displayconnectionerror",
    "can't connect to display",
    "cannot connect to display",
    "authorization required",
    "no authorization protocol",
    "display",
    "xlib",
)

_MACOS_PYOBJC_MISSING_TOKENS = (
    "you must first install pyobjc-core and pyobjc",
    "pyobjc-core",
    "pyobjc",
    "quartz",
    "appkit",
    "corefoundation",
    "pyobjctools",
)

_MACOS_PYOBJC_MODULE_NAMES = frozenset({
    "appkit",
    "quartz",
    "corefoundation",
    "foundation",
    "objc",
    "pyobjctools",
})


def classify_pyautogui_import_error(
    exc: BaseException | None,
    *,
    platform_name: Optional[str] = None,
) -> str:
    text = str(exc or "").strip()
    lower = text.lower()
    lower_platform = str(platform_name or "").lower()

    if isinstance(exc, ModuleNotFoundError):
        missing_name = str(getattr(exc, "name", "") or "").lower()
        if missing_name == "pyautogui" or "no module named 'pyautogui'" in lower or 'no module named "pyautogui"' in lower:
            return "AGENT_PYAUTOGUI_NOT_INSTALLED"
        if lower_platform == "darwin" and missing_name in _MACOS_PYOBJC_MODULE_NAMES:
            return "AGENT_PYAUTOGUI_MACOS_PYOBJC_MISSING"

    if any(token in lower for token in _DISPLAY_UNAVAILABLE_TOKENS):
        return "AGENT_PYAUTOGUI_DISPLAY_UNAVAILABLE"

    if any(token in lower for token in _MACOS_PYOBJC_MISSING_TOKENS):
        return "AGENT_PYAUTOGUI_MACOS_PYOBJC_MISSING"

    if "no module named" in lower and "pyautogui" in lower:
        return "AGENT_PYAUTOGUI_NOT_INSTALLED"

    return "AGENT_PYAUTOGUI_IMPORT_FAILED"


def format_pyautogui_import_error(
    exc: BaseException | None,
    *,
    platform_name: Optional[str] = None,
) -> str:
    reason = classify_pyautogui_import_error(exc, platform_name=platform_name)
    text = str(exc or "").strip()

    if reason == "AGENT_PYAUTOGUI_NOT_INSTALLED":
        return "pyautogui not installed"

    if reason == "AGENT_PYAUTOGUI_DISPLAY_UNAVAILABLE":
        return text or "desktop display session unavailable"

    if reason == "AGENT_PYAUTOGUI_MACOS_PYOBJC_MISSING":
        prefix = "pyautogui macOS bridge unavailable (PyObjC/Quartz/AppKit missing from the environment or packaged app)"
        return f"{prefix}: {text}" if text else prefix

    if text:
        return f"pyautogui import failed: {text}"
    return "pyautogui import failed"
