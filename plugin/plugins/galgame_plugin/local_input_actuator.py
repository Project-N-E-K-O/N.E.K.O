from __future__ import annotations

import sys
import types as _types
from typing import Any

from .models import DATA_SOURCE_MEMORY_READER, DATA_SOURCE_OCR_READER, SharedStatePayload
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
    PROCESS_QUERY_LIMITED_INFORMATION,
    SW_RESTORE,
    TOKEN_QUERY,
    TokenElevation,
    VK_DOWN,
    VK_ESCAPE,
    VK_RETURN,
    VK_SPACE,
    VK_UP,
)
from ._window_manager import (
    INPUT,
    INPUT_UNION,
    KEYBDINPUT,
    MOUSEINPUT,
    RECT,
    TOKEN_ELEVATION,
    _LAST_FOCUS_WINDOW_DIAGNOSTIC,
    _LAST_FOCUS_WINDOW_DIAGNOSTIC_LOCK,
    _LOGGER,
    _WAIT_EVENT,
    _find_window_for_pid,
    _focus_window,
    _foreground_matches_target_window,
    _get_last_focus_window_diagnostic,
    _is_current_process_elevated,
    _is_process_elevated,
    _root_window_handle,
    _runtime_target,
    _set_last_focus_window_diagnostic,
    _wait_seconds,
    _warn_input_exception,
    _window_process_id,
    _window_text,
)
from ._input_primitives import (
    INPUT_SAFETY_DENY_MARKERS,
    VIRTUAL_MOUSE_FORBIDDEN_ZONES,
    _click,
    _client_screen_rect,
    _coerce_rect,
    _coerce_source_size,
    _input_safety_policy_block_reason,
    _matching_input_safety_deny_marker,
    _rect_payload,
    _relative_point_forbidden_zone,
    _tap_key,
)
from ._choice_resolver import (
    SYSTEM_MENU_MARKERS,
    VIRTUAL_MOUSE_DIALOGUE_CANDIDATES,
    _choose_bounds,
    _choose_choice,
    _choose_index,
    _looks_like_system_menu,
    _recover_should_press_escape,
    _resolve_choice_bounds_click_target,
    _resolve_virtual_mouse_dialogue_target,
    _snapshot_has_visible_choices,
    _snapshot_screen_type,
    _snapshot_text,
)

# Pre-split, all symbols below lived in this one module. After the split, tests
# that monkeypatch ``local_input_actuator.<name>`` (or assign directly) must keep
# affecting the call sites in the submodules where the name now actually lives.
# This proxy reroutes assignments to the owning submodule so the old test-time
# semantics survive the split unchanged.
_PROXY_TO_WINDOW_MANAGER = frozenset(
    {
        "_LAST_FOCUS_WINDOW_DIAGNOSTIC",
        "_LAST_FOCUS_WINDOW_DIAGNOSTIC_LOCK",
        "_LOGGER",
        "_WAIT_EVENT",
    }
)
_PROXY_TO_INPUT_PRIMITIVES = frozenset(
    {
        "_is_current_process_elevated",
        "_is_process_elevated",
    }
)


class _ShimModule(_types.ModuleType):
    def __setattr__(self, name: str, value: Any) -> None:
        super().__setattr__(name, value)
        if name in _PROXY_TO_WINDOW_MANAGER:
            from . import _window_manager

            setattr(_window_manager, name, value)
        elif name in _PROXY_TO_INPUT_PRIMITIVES:
            from . import _input_primitives

            setattr(_input_primitives, name, value)


sys.modules[__name__].__class__ = _ShimModule


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
