from __future__ import annotations

import ctypes
import logging
import sys
import threading
from ctypes import wintypes
from typing import Any

from .models import SharedStatePayload


VK_RETURN = 0x0D
VK_ESCAPE = 0x1B
VK_SPACE = 0x20
VK_UP = 0x26
VK_DOWN = 0x28

KEYEVENTF_KEYUP = 0x0002
KEYEVENTF_SCANCODE = 0x0008
KEYEVENTF_EXTENDEDKEY = 0x0001
MOUSEEVENTF_LEFTDOWN = 0x0002
MOUSEEVENTF_LEFTUP = 0x0004
MOUSEEVENTF_MOVE = 0x0001
MOUSEEVENTF_ABSOLUTE = 0x8000
SW_RESTORE = 9
INPUT_MOUSE = 0
INPUT_KEYBOARD = 1
MAPVK_VK_TO_VSC = 0
SYSTEM_MENU_MARKERS = (
    "SYSTEM",
    "重置选项",
    "语言设置",
    "画面设置",
    "选项设置",
    "回到标题",
    "返回",
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
VIRTUAL_MOUSE_DIALOGUE_CANDIDATES = (
    {"target_id": "dialogue_continue_primary", "relative_x": 0.23, "relative_y": 0.75},
    {"target_id": "dialogue_text_left", "relative_x": 0.18, "relative_y": 0.74},
    {"target_id": "dialogue_text_mid", "relative_x": 0.30, "relative_y": 0.76},
)
VIRTUAL_MOUSE_FORBIDDEN_ZONES = (
    {"zone_id": "bottom_toolbar", "min_x": 0.58, "max_x": 1.0, "min_y": 0.78, "max_y": 1.0},
    {"zone_id": "top_edge", "min_x": 0.0, "max_x": 1.0, "min_y": 0.0, "max_y": 0.04},
)
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
TOKEN_QUERY = 0x0008
TokenElevation = 20
_LAST_FOCUS_WINDOW_DIAGNOSTIC = ""
_LAST_FOCUS_WINDOW_DIAGNOSTIC_LOCK = threading.Lock()
_WAIT_EVENT = threading.Event()
_LOGGER = logging.getLogger(__name__)


def _warn_input_exception(message: str, exc: Exception) -> None:
    _LOGGER.warning("%s: %s", message, exc, exc_info=True)


def _wait_seconds(delay: float) -> None:
    _WAIT_EVENT.wait(max(0.0, float(delay or 0.0)))


def _set_last_focus_window_diagnostic(value: str) -> None:
    global _LAST_FOCUS_WINDOW_DIAGNOSTIC
    with _LAST_FOCUS_WINDOW_DIAGNOSTIC_LOCK:
        _LAST_FOCUS_WINDOW_DIAGNOSTIC = str(value or "")


def _get_last_focus_window_diagnostic() -> str:
    with _LAST_FOCUS_WINDOW_DIAGNOSTIC_LOCK:
        return str(_LAST_FOCUS_WINDOW_DIAGNOSTIC or "")


class TOKEN_ELEVATION(ctypes.Structure):
    _fields_ = (("TokenIsElevated", wintypes.DWORD),)


class RECT(ctypes.Structure):
    _fields_ = (
        ("left", ctypes.c_long),
        ("top", ctypes.c_long),
        ("right", ctypes.c_long),
        ("bottom", ctypes.c_long),
    )


class MOUSEINPUT(ctypes.Structure):
    _fields_ = (
        ("dx", wintypes.LONG),
        ("dy", wintypes.LONG),
        ("mouseData", wintypes.DWORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong)),
    )


class KEYBDINPUT(ctypes.Structure):
    _fields_ = (
        ("wVk", wintypes.WORD),
        ("wScan", wintypes.WORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong)),
    )


class INPUT_UNION(ctypes.Union):
    _fields_ = (
        ("mi", MOUSEINPUT),
        ("ki", KEYBDINPUT),
    )


class INPUT(ctypes.Structure):
    _fields_ = (
        ("type", wintypes.DWORD),
        ("union", INPUT_UNION),
    )


def _runtime_target(shared: SharedStatePayload) -> dict[str, Any]:
    fallback: dict[str, Any] = {}
    for key in ("ocr_reader_runtime", "memory_reader_runtime"):
        runtime = shared.get(key)
        if isinstance(runtime, dict):
            pid = int(runtime.get("pid") or 0)
            process_name = str(
                runtime.get("effective_process_name") or runtime.get("process_name") or ""
            ).strip()
            if pid > 0 or process_name:
                target = {
                    "pid": pid,
                    "process_name": process_name,
                    "window_title": str(
                        runtime.get("effective_window_title") or runtime.get("window_title") or ""
                    ).strip(),
                }
                if pid > 0 and process_name:
                    return target
                if not fallback:
                    fallback = target
    return fallback or {"pid": 0, "process_name": "", "window_title": ""}


def _find_window_for_pid(pid: int) -> tuple[int, tuple[int, int, int, int]]:
    try:
        import win32gui
        import win32process
    except ImportError:
        win32gui = None
        win32process = None

    if win32gui is not None and win32process is not None:
        matches: list[tuple[int, int, tuple[int, int, int, int]]] = []

        def _pywin_callback(hwnd: int, _lparam: int) -> None:
            if not win32gui.IsWindowVisible(hwnd):
                return
            if win32gui.IsIconic(hwnd):
                return
            _, window_pid = win32process.GetWindowThreadProcessId(hwnd)
            if int(window_pid) != int(pid):
                return
            left, top, right, bottom = win32gui.GetWindowRect(hwnd)
            width = int(right - left)
            height = int(bottom - top)
            if width < 160 or height < 120:
                return
            matches.append((width * height, int(hwnd), (int(left), int(top), int(right), int(bottom))))

        win32gui.EnumWindows(_pywin_callback, None)
        if matches:
            matches.sort(reverse=True)
            _, hwnd, rect = matches[0]
            return hwnd, rect

    user32 = ctypes.windll.user32
    matches: list[tuple[int, int, tuple[int, int, int, int]]] = []

    enum_proc_type = ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)

    def _callback(hwnd: int, _lparam: int) -> bool:
        if not user32.IsWindowVisible(hwnd):
            return True
        if user32.IsIconic(hwnd):
            return True
        window_pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(window_pid))
        if int(window_pid.value) != int(pid):
            return True
        rect = RECT()
        if not user32.GetWindowRect(hwnd, ctypes.byref(rect)):
            return True
        width = int(rect.right - rect.left)
        height = int(rect.bottom - rect.top)
        if width < 160 or height < 120:
            return True
        area = width * height
        matches.append((area, int(hwnd), (int(rect.left), int(rect.top), int(rect.right), int(rect.bottom))))
        return True

    user32.EnumWindows(enum_proc_type(_callback), 0)
    if not matches:
        return 0, (0, 0, 0, 0)
    matches.sort(reverse=True)
    _, hwnd, rect = matches[0]
    return hwnd, rect


def _window_text(hwnd: int) -> str:
    try:
        import win32gui
    except ImportError:
        win32gui = None
    if win32gui is not None:
        try:
            return str(win32gui.GetWindowText(hwnd) or "")
        except Exception as exc:
            _warn_input_exception("local input window text lookup failed via pywin32", exc)
            return ""
    user32 = ctypes.windll.user32
    try:
        length = int(user32.GetWindowTextLengthW(hwnd))
        if length <= 0:
            return ""
        buffer = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(hwnd, buffer, length + 1)
        return str(buffer.value or "")
    except Exception as exc:
        _warn_input_exception("local input window text lookup failed via user32", exc)
        return ""


def _root_window_handle(hwnd: int) -> int:
    if not hwnd:
        return 0
    try:
        root = int(ctypes.windll.user32.GetAncestor(int(hwnd), 2))
        return root or int(hwnd)
    except Exception as exc:
        _warn_input_exception("local input root window lookup failed", exc)
        return int(hwnd)


def _window_process_id(hwnd: int) -> int:
    if not hwnd:
        return 0
    try:
        pid = wintypes.DWORD()
        ctypes.windll.user32.GetWindowThreadProcessId(int(hwnd), ctypes.byref(pid))
        return int(pid.value or 0)
    except Exception as exc:
        _warn_input_exception("local input window process lookup failed", exc)
        return 0


def _foreground_matches_target_window(foreground_hwnd: int, target_hwnd: int, target_pid: int) -> bool:
    if not foreground_hwnd or not target_hwnd:
        return False
    if int(foreground_hwnd) == int(target_hwnd):
        return True
    foreground_root = _root_window_handle(int(foreground_hwnd))
    target_root = _root_window_handle(int(target_hwnd))
    if foreground_root and target_root and foreground_root == target_root:
        return True
    foreground_pid = _window_process_id(int(foreground_hwnd)) or _window_process_id(foreground_root)
    return bool(foreground_pid and target_pid and foreground_pid == int(target_pid))


def _focus_window(hwnd: int) -> bool:
    _set_last_focus_window_diagnostic("")
    user32 = ctypes.windll.user32
    target_pid = _window_process_id(hwnd)
    if user32.IsIconic(hwnd):
        user32.ShowWindow(hwnd, SW_RESTORE)
    try:
        user32.AllowSetForegroundWindow(-1)
    except Exception as exc:
        _warn_input_exception("local input AllowSetForegroundWindow failed", exc)
        _set_last_focus_window_diagnostic(f"AllowSetForegroundWindow failed: {exc}")
    foreground = user32.GetForegroundWindow()
    current_thread = ctypes.windll.kernel32.GetCurrentThreadId()
    foreground_thread = user32.GetWindowThreadProcessId(foreground, None) if foreground else 0
    target_thread = user32.GetWindowThreadProcessId(hwnd, None)
    attached_foreground = False
    attached_target = False
    try:
        if foreground_thread and foreground_thread != current_thread:
            attached_foreground = bool(user32.AttachThreadInput(current_thread, foreground_thread, True))
        if target_thread and target_thread != current_thread:
            attached_target = bool(user32.AttachThreadInput(current_thread, target_thread, True))
        user32.BringWindowToTop(hwnd)
        user32.SetForegroundWindow(hwnd)
        user32.SetActiveWindow(hwnd)
        user32.SetFocus(hwnd)
    except Exception as exc:
        _warn_input_exception("local input SetForegroundWindow sequence failed", exc)
        _set_last_focus_window_diagnostic(f"SetForegroundWindow failed: {exc}")
    finally:
        if attached_target:
            user32.AttachThreadInput(current_thread, target_thread, False)
        if attached_foreground:
            user32.AttachThreadInput(current_thread, foreground_thread, False)
    _wait_seconds(0.12)
    try:
        foreground_hwnd = int(user32.GetForegroundWindow())
        focused = _foreground_matches_target_window(
            foreground_hwnd,
            int(hwnd),
            int(target_pid),
        )
        if focused:
            _set_last_focus_window_diagnostic("")
            return True
        if foreground_hwnd != int(hwnd):
            if not _get_last_focus_window_diagnostic():
                fg_pid = _window_process_id(foreground_hwnd) or 0
                _set_last_focus_window_diagnostic(
                    f"foreground_mismatch: fg_hwnd={foreground_hwnd} fg_pid={fg_pid} "
                    f"target_hwnd={hwnd} target_pid={target_pid}"
                )
        elif not _get_last_focus_window_diagnostic():
            _set_last_focus_window_diagnostic("foreground window did not match target")
        return False
    except Exception as exc:
        _warn_input_exception("local input foreground verification failed", exc)
        _set_last_focus_window_diagnostic(f"foreground verification failed: {exc}")
        return False


def _is_current_process_elevated() -> bool | None:
    if sys.platform != "win32":
        return None
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception as exc:
        _warn_input_exception("local input current elevation lookup failed", exc)
        return None


def _is_process_elevated(pid: int) -> bool | None:
    if sys.platform != "win32":
        return None
    kernel32 = ctypes.windll.kernel32
    advapi32 = ctypes.windll.advapi32
    process = None
    token = wintypes.HANDLE()
    try:
        process = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, int(pid))
        if not process:
            return None
        if not advapi32.OpenProcessToken(process, TOKEN_QUERY, ctypes.byref(token)):
            return None
        elevation = TOKEN_ELEVATION()
        returned = wintypes.DWORD()
        if not advapi32.GetTokenInformation(
            token,
            TokenElevation,
            ctypes.byref(elevation),
            ctypes.sizeof(elevation),
            ctypes.byref(returned),
        ):
            return None
        return bool(elevation.TokenIsElevated)
    except Exception as exc:
        _warn_input_exception("local input target elevation lookup failed", exc)
        return None
    finally:
        try:
            if token:
                kernel32.CloseHandle(token)
        except Exception as exc:
            _warn_input_exception("local input token handle close failed", exc)
        try:
            if process:
                kernel32.CloseHandle(process)
        except Exception as exc:
            _warn_input_exception("local input process handle close failed", exc)


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


def _snapshot_has_visible_choices(shared: SharedStatePayload) -> bool:
    snapshot = shared.get("latest_snapshot")
    if not isinstance(snapshot, dict):
        return False
    return bool(snapshot.get("is_menu_open")) or bool(list(snapshot.get("choices") or []))


def _resolve_virtual_mouse_dialogue_target(
    actuation: dict[str, Any],
    client_rect: tuple[int, int, int, int],
    *,
    candidates: tuple[dict[str, float | str], ...] = VIRTUAL_MOUSE_DIALOGUE_CANDIDATES,
) -> dict[str, Any]:
    left, top, right, bottom = client_rect
    width = max(int(right - left), 1)
    height = max(int(bottom - top), 1)
    start_index = max(0, int(actuation.get("instruction_variant") or 0))
    requested_target_id = str(actuation.get("virtual_mouse_target_id") or "").strip()
    requested_indices = [
        index
        for index, candidate in enumerate(candidates)
        if str(candidate.get("target_id") or "") == requested_target_id
    ]
    fallback_indices = [
        (start_index + offset) % len(candidates)
        for offset in range(len(candidates))
    ]
    ordered_indices: list[int] = []
    for candidate_index in [*requested_indices, *fallback_indices]:
        if candidate_index not in ordered_indices:
            ordered_indices.append(candidate_index)
    skipped: list[dict[str, Any]] = []
    for candidate_index in ordered_indices:
        candidate = candidates[candidate_index]
        relative_x = float(candidate.get("relative_x") or 0.0)
        relative_y = float(candidate.get("relative_y") or 0.0)
        zone_id = _relative_point_forbidden_zone(relative_x, relative_y)
        if zone_id:
            skipped.append(
                {
                    "target_id": str(candidate.get("target_id") or ""),
                    "candidate_index": candidate_index,
                    "relative_x": relative_x,
                    "relative_y": relative_y,
                    "forbidden_zone": zone_id,
                }
            )
            continue
        screen_x = left + int(max(0.0, min(relative_x, 1.0)) * width)
        screen_y = top + int(max(0.0, min(relative_y, 1.0)) * height)
        return {
            "success": True,
            "target_id": str(candidate.get("target_id") or ""),
            "candidate_index": candidate_index,
            "relative_x": relative_x,
            "relative_y": relative_y,
            "screen_x": int(screen_x),
            "screen_y": int(screen_y),
            "client_rect": _rect_payload(client_rect),
            "forbidden_zone_hit": False,
            "requested_target_id": requested_target_id,
            "skipped_candidates": skipped,
        }
    return {
        "success": False,
        "reason": "virtual_mouse_candidates_blocked_by_forbidden_zones",
        "client_rect": _rect_payload(client_rect),
        "forbidden_zone_hit": True,
        "requested_target_id": requested_target_id,
        "skipped_candidates": skipped,
    }


def _choose_index(actuation: dict[str, Any]) -> int:
    choices = list(actuation.get("candidate_choices") or [])
    candidate_index = max(0, int(actuation.get("candidate_index") or 0))
    if candidate_index < len(choices):
        return max(0, int(dict(choices[candidate_index]).get("index") or 0))
    return candidate_index


def _choose_choice(actuation: dict[str, Any]) -> dict[str, Any]:
    choices = list(actuation.get("candidate_choices") or [])
    candidate_index = max(0, int(actuation.get("candidate_index") or 0))
    if candidate_index >= len(choices):
        return {}
    return dict(choices[candidate_index] or {})


def _choose_bounds(actuation: dict[str, Any]) -> dict[str, float]:
    bounds = dict(_choose_choice(actuation).get("bounds") or {})
    try:
        left = float(bounds.get("left"))
        top = float(bounds.get("top"))
        right = float(bounds.get("right"))
        bottom = float(bounds.get("bottom"))
    except (TypeError, ValueError):
        return {}
    if right <= left or bottom <= top:
        return {}
    return {"left": left, "top": top, "right": right, "bottom": bottom}


def _snapshot_screen_type(shared: SharedStatePayload) -> str:
    snapshot = shared.get("latest_snapshot")
    if isinstance(snapshot, dict):
        screen_type = str(snapshot.get("screen_type") or "").strip()
        if screen_type:
            return screen_type
    return str(shared.get("screen_type") or "").strip()


def _recover_should_press_escape(shared: SharedStatePayload, actuation: dict[str, Any]) -> bool:
    strategy_id = str(actuation.get("strategy_id") or "")
    if strategy_id in {"save_load_escape", "config_escape", "gallery_escape", "game_over_escape"}:
        return True
    return _snapshot_screen_type(shared) in {
        "save_load_stage",
        "config_stage",
        "gallery_stage",
        "game_over_stage",
    }


def _resolve_choice_bounds_click_target(
    actuation: dict[str, Any],
    bounds: dict[str, float],
    *,
    window_rect: tuple[int, int, int, int],
    client_rect: tuple[int, int, int, int],
) -> dict[str, Any]:
    choice = _choose_choice(actuation)
    bounds_payload = dict(choice.get("bounds") or {})
    coordinate_space = str(
        choice.get("bounds_coordinate_space")
        or bounds_payload.get("bounds_coordinate_space")
        or ""
    ).strip().lower()
    capture_rect = _coerce_rect(choice.get("capture_rect") or bounds_payload.get("capture_rect"))
    metadata_window_rect = _coerce_rect(
        choice.get("window_rect") or bounds_payload.get("window_rect")
    )
    source_width, source_height = _coerce_source_size(
        choice.get("source_size") or bounds_payload.get("source_size")
    )

    if coordinate_space == "capture" and capture_rect != (0, 0, 0, 0):
        target_rect = capture_rect
        resolved_space = "capture"
    elif not coordinate_space and capture_rect != (0, 0, 0, 0):
        target_rect = capture_rect
        resolved_space = "capture"
    elif coordinate_space == "client" and client_rect != (0, 0, 0, 0):
        target_rect = client_rect
        resolved_space = "client"
    else:
        target_rect = metadata_window_rect if metadata_window_rect != (0, 0, 0, 0) else window_rect
        resolved_space = "window"

    left, top, right, bottom = target_rect
    width = max(right - left, 1)
    height = max(bottom - top, 1)
    if source_width <= 0.0:
        source_width = float(width)
    if source_height <= 0.0:
        source_height = float(height)

    center_x = float(bounds["left"] + bounds["right"]) / 2.0
    center_y = float(bounds["top"] + bounds["bottom"]) / 2.0
    text_left_x = float(bounds["left"]) + min(
        16.0,
        max(4.0, (float(bounds["right"]) - float(bounds["left"])) * 0.12),
    )
    screen_points: list[dict[str, int]] = []
    for raw_x, raw_y in ((center_x, center_y), (text_left_x, center_y)):
        x = left + int(max(0.0, min(raw_x / source_width, 1.0)) * width)
        y = top + int(max(0.0, min(raw_y / source_height, 1.0)) * height)
        screen_points.append({"x": int(x), "y": int(y)})

    return {
        "coordinate_space": resolved_space,
        "source_size": {"width": float(source_width), "height": float(source_height)},
        "target_rect": _rect_payload(target_rect),
        "window_rect": _rect_payload(window_rect),
        "client_rect": _rect_payload(client_rect),
        "capture_rect": _rect_payload(capture_rect) if capture_rect != (0, 0, 0, 0) else {},
        "bounds": dict(bounds),
        "screen_points": screen_points,
    }


def _snapshot_text(shared: SharedStatePayload) -> str:
    snapshot = shared.get("latest_snapshot")
    if not isinstance(snapshot, dict):
        return ""
    return "\n".join(
        str(snapshot.get(key) or "").strip()
        for key in ("speaker", "text")
        if str(snapshot.get(key) or "").strip()
    )


def _looks_like_system_menu(shared: SharedStatePayload) -> bool:
    text = _snapshot_text(shared)
    if not text:
        return False
    return sum(1 for marker in SYSTEM_MENU_MARKERS if marker in text) >= 2


def perform_local_input_actuation(
    shared: SharedStatePayload,
    actuation: dict[str, Any],
) -> dict[str, Any]:
    if sys.platform != "win32":
        return {"success": False, "reason": "local input fallback only supports Windows"}

    target = _runtime_target(shared)
    pid = int(target.get("pid") or 0)
    if pid <= 0:
        return {"success": False, "reason": "no target pid for local input fallback"}

    hwnd, rect = _find_window_for_pid(pid)
    if not hwnd:
        return {"success": False, "reason": f"no visible target window for pid={pid}"}

    window_title = _window_text(hwnd)
    safety_block = _input_safety_policy_block_reason(
        target=target,
        hwnd=hwnd,
        window_title=window_title,
    )
    if safety_block:
        return {
            "success": False,
            "reason": "blocked_by_input_safety_policy",
            "safety_policy": {
                "blocked": True,
                "detail": safety_block,
                "pid": pid,
                "process_name": str(target.get("process_name") or ""),
                "runtime_window_title": str(target.get("window_title") or ""),
                "window_title": window_title,
            },
            "kind": str(actuation.get("kind") or ""),
            "strategy_id": str(actuation.get("strategy_id") or ""),
            "pid": pid,
            "hwnd": hwnd,
        }

    _set_last_focus_window_diagnostic("")
    if not _focus_window(hwnd):
        focus_diagnostic = _get_last_focus_window_diagnostic() or "target window could not be focused"
        return {
            "success": False,
            "reason": "blocked_by_input_safety_policy",
            "safety_policy": {
                "blocked": True,
                "detail": "blocked_by_input_safety_policy: target window could not be focused",
                "focus_diagnostic": focus_diagnostic,
                "pid": pid,
                "process_name": str(target.get("process_name") or ""),
                "runtime_window_title": str(target.get("window_title") or ""),
                "window_title": window_title,
            },
            "kind": str(actuation.get("kind") or ""),
            "strategy_id": str(actuation.get("strategy_id") or ""),
            "pid": pid,
            "hwnd": hwnd,
        }

    kind = str(actuation.get("kind") or "")
    strategy_id = str(actuation.get("strategy_id") or "")
    if kind == "probe":
        _tap_key(hwnd, VK_RETURN if strategy_id == "probe_enter" else VK_SPACE)
    elif kind == "advance":
        if strategy_id == "advance_enter":
            _tap_key(hwnd, VK_RETURN)
        elif strategy_id == "advance_click":
            if _snapshot_has_visible_choices(shared):
                return {
                    "success": False,
                    "reason": "advance_click_blocked_by_visible_choices",
                    "kind": kind,
                    "strategy_id": strategy_id,
                    "pid": pid,
                    "hwnd": hwnd,
                    "virtual_mouse": {
                        "blocked": True,
                        "detail": "visible choices are present; ordinary advance click is disabled",
                    },
                }
            client_rect = _client_screen_rect(hwnd)
            left, top, right, bottom = client_rect if client_rect != (0, 0, 0, 0) else rect
            active_rect = (left, top, right, bottom)
            virtual_mouse = _resolve_virtual_mouse_dialogue_target(actuation, active_rect)
            if not bool(virtual_mouse.get("success")):
                return {
                    "success": False,
                    "reason": str(virtual_mouse.get("reason") or "virtual_mouse_target_unavailable"),
                    "kind": kind,
                    "strategy_id": strategy_id,
                    "pid": pid,
                    "hwnd": hwnd,
                    "virtual_mouse": virtual_mouse,
                }
            _click(hwnd, int(virtual_mouse["screen_x"]), int(virtual_mouse["screen_y"]))
            return {
                "success": True,
                "reason": "",
                "kind": kind,
                "strategy_id": strategy_id,
                "pid": pid,
                "hwnd": hwnd,
                "method": "virtual_mouse_dialogue_click",
                "virtual_mouse": {
                    **virtual_mouse,
                    "coordinate_space": "client" if client_rect != (0, 0, 0, 0) else "window",
                    "safety_policy": {"blocked": False},
                },
            }
        else:
            _tap_key(hwnd, VK_SPACE)
    elif kind == "recover":
        if _recover_should_press_escape(shared, actuation) or _looks_like_system_menu(shared):
            _tap_key(hwnd, VK_ESCAPE)
    elif kind == "choose":
        choice_index = _choose_index(actuation)
        candidate_choices = list(actuation.get("candidate_choices") or [])
        bounds = _choose_bounds(actuation)
        if bounds:
            client_rect = _client_screen_rect(hwnd)
            choice_target = _resolve_choice_bounds_click_target(
                actuation,
                bounds,
                window_rect=rect,
                client_rect=client_rect,
            )
            for point in choice_target["screen_points"]:
                _click(hwnd, int(point["x"]), int(point["y"]))
            _tap_key(hwnd, VK_RETURN)
            return {
                "success": True,
                "reason": "",
                "kind": kind,
                "strategy_id": strategy_id,
                "pid": pid,
                "hwnd": hwnd,
                "method": "choice_bounds_click",
                **choice_target,
            }
        reset_count = max(len(candidate_choices), choice_index + 1, 1)
        _tap_key(hwnd, VK_UP, count=reset_count, delay=0.02)
        if choice_index > 0:
            _tap_key(hwnd, VK_DOWN, count=choice_index, delay=0.035)
        _tap_key(hwnd, VK_RETURN)
    else:
        return {"success": False, "reason": f"unsupported local actuation kind: {kind}"}

    return {
        "success": True,
        "reason": "",
        "kind": kind,
        "strategy_id": strategy_id,
        "pid": pid,
        "hwnd": hwnd,
    }


def try_focus_target_window(shared: SharedStatePayload) -> dict[str, Any]:
    if sys.platform != "win32":
        return {"success": False, "reason": "local input fallback only supports Windows"}

    target = _runtime_target(shared)
    pid = int(target.get("pid") or 0)
    if pid <= 0:
        return {"success": False, "reason": "no target pid for local input fallback"}

    hwnd, rect = _find_window_for_pid(pid)
    if not hwnd:
        return {"success": False, "reason": f"no visible target window for pid={pid}"}

    window_title = _window_text(hwnd)
    safety_block = _input_safety_policy_block_reason(
        target=target,
        hwnd=hwnd,
        window_title=window_title,
    )
    if safety_block:
        return {
            "success": False,
            "reason": "blocked_by_input_safety_policy",
            "safety_policy": {
                "blocked": True,
                "detail": safety_block,
                "pid": pid,
                "process_name": str(target.get("process_name") or ""),
                "window_title": window_title,
            },
            "pid": pid,
            "hwnd": hwnd,
        }

    _set_last_focus_window_diagnostic("")
    focused = _focus_window(hwnd)
    if not focused:
        focus_diagnostic = _get_last_focus_window_diagnostic() or "target window could not be focused"
        return {
            "success": False,
            "reason": "focus_failed",
            "focus_diagnostic": focus_diagnostic,
            "pid": pid,
            "hwnd": hwnd,
        }

    return {
        "success": True,
        "reason": "",
        "pid": pid,
        "hwnd": hwnd,
    }
