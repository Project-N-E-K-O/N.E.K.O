from __future__ import annotations

import ctypes
from ctypes import wintypes
from typing import Any

from ._win32_input_types import (
    INPUT_KEYBOARD,
    INPUT_MOUSE,
    KEYEVENTF_EXTENDEDKEY,
    KEYEVENTF_KEYUP,
    KEYEVENTF_SCANCODE,
    MAPVK_VK_TO_VSC,
    MOUSEEVENTF_ABSOLUTE,
    MOUSEEVENTF_LEFTDOWN,
    MOUSEEVENTF_LEFTUP,
    MOUSEEVENTF_MOVE,
    VK_DOWN,
    VK_UP,
)
from ._window_manager import (
    INPUT,
    INPUT_UNION,
    KEYBDINPUT,
    MOUSEINPUT,
    RECT,
    _is_current_process_elevated,
    _is_process_elevated,
    _wait_seconds,
)


INPUT_SAFETY_DENY_MARKERS = (
    "anti-cheat",
    "anticheat",
    "easy anti-cheat",
    "easyanticheat",
    "battleye",
    "battl-eye",
    "vanguard",
    "ricochet",
    "xigncode",
    "gameguard",
    "faceit",
    "equ8",
    "ace anti",
)
VIRTUAL_MOUSE_FORBIDDEN_ZONES = (
    {"zone_id": "bottom_toolbar", "min_x": 0.58, "max_x": 1.0, "min_y": 0.78, "max_y": 1.0},
    {"zone_id": "top_edge", "min_x": 0.0, "max_x": 1.0, "min_y": 0.0, "max_y": 0.04},
    {"zone_id": "right_edge_buttons", "min_x": 0.85, "max_x": 1.0, "min_y": 0.0, "max_y": 0.15},
)


def _matching_input_safety_deny_marker(*values: str) -> str:
    text = "\n".join(str(value or "") for value in values).lower()
    for marker in INPUT_SAFETY_DENY_MARKERS:
        if marker in text:
            return marker
    return ""


def _input_safety_policy_block_reason(
    *,
    target: dict[str, Any],
    hwnd: int,
    window_title: str,
) -> str:
    pid = int(target.get("pid") or 0)
    process_name = str(target.get("process_name") or "").strip()
    runtime_title = str(target.get("window_title") or "").strip()
    if pid <= 0 or not hwnd:
        return "blocked_by_input_safety_policy: missing target window"
    if not process_name:
        return "blocked_by_input_safety_policy: missing runtime process name"
    deny_marker = _matching_input_safety_deny_marker(process_name, runtime_title, window_title)
    if deny_marker:
        return f"blocked_by_input_safety_policy: deny marker {deny_marker}"
    current_elevated = _is_current_process_elevated()
    target_elevated = _is_process_elevated(pid)
    if target_elevated is True and current_elevated is False:
        return "blocked_by_input_safety_policy: target process is elevated"
    return ""


def _tap_key(hwnd: int, vk: int, *, count: int = 1, delay: float = 0.05) -> None:
    user32 = ctypes.windll.user32
    scan = int(user32.MapVirtualKeyW(int(vk), MAPVK_VK_TO_VSC))
    extended = KEYEVENTF_EXTENDEDKEY if int(vk) in {VK_UP, VK_DOWN} else 0
    for _ in range(max(1, int(count))):
        if scan:
            inputs = (INPUT * 2)(
                INPUT(
                    INPUT_KEYBOARD,
                    INPUT_UNION(
                        ki=KEYBDINPUT(
                            0,
                            scan,
                            KEYEVENTF_SCANCODE | extended,
                            0,
                            None,
                        )
                    ),
                ),
                INPUT(
                    INPUT_KEYBOARD,
                    INPUT_UNION(
                        ki=KEYBDINPUT(
                            0,
                            scan,
                            KEYEVENTF_SCANCODE | KEYEVENTF_KEYUP | extended,
                            0,
                            None,
                        )
                    ),
                ),
            )
            user32.SendInput(len(inputs), ctypes.byref(inputs), ctypes.sizeof(INPUT))
        else:
            user32.keybd_event(vk, 0, 0, 0)
            _wait_seconds(0.025)
            user32.keybd_event(vk, 0, KEYEVENTF_KEYUP, 0)
        _wait_seconds(delay)


def _click(hwnd: int, x: int, y: int) -> None:
    user32 = ctypes.windll.user32
    user32.SetCursorPos(int(x), int(y))
    _wait_seconds(0.04)
    virt_x = user32.GetSystemMetrics(76)  # SM_XVIRTUALSCREEN
    virt_y = user32.GetSystemMetrics(77)  # SM_YVIRTUALSCREEN
    virt_w = max(user32.GetSystemMetrics(78) - 1, 1)  # SM_CXVIRTUALSCREEN
    virt_h = max(user32.GetSystemMetrics(79) - 1, 1)  # SM_CYVIRTUALSCREEN
    abs_x = int((int(x) - virt_x) * 65535 / virt_w)
    abs_y = int((int(y) - virt_y) * 65535 / virt_h)
    inputs = (INPUT * 3)(
        INPUT(INPUT_MOUSE, INPUT_UNION(mi=MOUSEINPUT(abs_x, abs_y, 0, MOUSEEVENTF_MOVE | MOUSEEVENTF_ABSOLUTE, 0, None))),
        INPUT(INPUT_MOUSE, INPUT_UNION(mi=MOUSEINPUT(abs_x, abs_y, 0, MOUSEEVENTF_LEFTDOWN, 0, None))),
        INPUT(INPUT_MOUSE, INPUT_UNION(mi=MOUSEINPUT(abs_x, abs_y, 0, MOUSEEVENTF_LEFTUP, 0, None))),
    )
    user32.SendInput(len(inputs), ctypes.byref(inputs), ctypes.sizeof(INPUT))
    _wait_seconds(0.08)


def _client_screen_rect(hwnd: int) -> tuple[int, int, int, int]:
    user32 = ctypes.windll.user32
    rect = RECT()
    if not user32.GetClientRect(hwnd, ctypes.byref(rect)):
        return (0, 0, 0, 0)
    origin = wintypes.POINT(0, 0)
    if not user32.ClientToScreen(hwnd, ctypes.byref(origin)):
        return (0, 0, 0, 0)
    width = int(rect.right - rect.left)
    height = int(rect.bottom - rect.top)
    if width <= 0 or height <= 0:
        return (0, 0, 0, 0)
    return (
        int(origin.x),
        int(origin.y),
        int(origin.x + width),
        int(origin.y + height),
    )


def _rect_payload(rect: tuple[int, int, int, int]) -> dict[str, int]:
    left, top, right, bottom = rect
    return {"left": int(left), "top": int(top), "right": int(right), "bottom": int(bottom)}


def _coerce_rect(value: Any) -> tuple[int, int, int, int]:
    if isinstance(value, dict):
        try:
            left = int(float(value.get("left")))
            top = int(float(value.get("top")))
            right = int(float(value.get("right")))
            bottom = int(float(value.get("bottom")))
        except (TypeError, ValueError):
            return (0, 0, 0, 0)
    elif isinstance(value, (list, tuple)) and len(value) >= 4:
        try:
            left = int(float(value[0]))
            top = int(float(value[1]))
            right = int(float(value[2]))
            bottom = int(float(value[3]))
        except (TypeError, ValueError):
            return (0, 0, 0, 0)
    else:
        return (0, 0, 0, 0)
    if right <= left or bottom <= top:
        return (0, 0, 0, 0)
    return (left, top, right, bottom)


def _coerce_source_size(value: Any) -> tuple[float, float]:
    if isinstance(value, dict):
        try:
            width = float(value.get("width"))
            height = float(value.get("height"))
        except (TypeError, ValueError):
            return (0.0, 0.0)
    elif isinstance(value, (list, tuple)) and len(value) >= 2:
        try:
            width = float(value[0])
            height = float(value[1])
        except (TypeError, ValueError):
            return (0.0, 0.0)
    else:
        return (0.0, 0.0)
    if width <= 0.0 or height <= 0.0:
        return (0.0, 0.0)
    return (width, height)


def _relative_point_forbidden_zone(relative_x: float, relative_y: float) -> str:
    for zone in VIRTUAL_MOUSE_FORBIDDEN_ZONES:
        if (
            float(zone["min_x"]) <= relative_x <= float(zone["max_x"])
            and float(zone["min_y"]) <= relative_y <= float(zone["max_y"])
        ):
            return str(zone["zone_id"])
    return ""
