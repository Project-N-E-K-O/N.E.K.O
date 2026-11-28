# Copyright 2025-2026 Project N.E.K.O. Team
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Windows UI Automation control tree snapshots and uid resolution for desktop actions.

On Windows, task agents can inspect the foreground window (or a named top-level
window) as a compact numbered tree of UI controls (``uid=1_0``, ``uid=1_1``, ...)
and operate on controls by ``uid`` instead of guessing 0..999 screenshot
coordinates. Non-Windows platforms report unsupported and fall back to
screenshot coordinates.

COM access uses ``comtypes`` against ``UIAutomationCore.dll`` directly inside
worker threads with per-thread ``CoInitializeEx(COINIT_MULTITHREADED)``, never
importing ``pywinauto`` at module level or mutating ``sys.coinit_flags``.
"""

from __future__ import annotations

import ctypes
import logging
import platform
import threading
import time
from dataclasses import dataclass, replace
from typing import Any, Protocol

logger = logging.getLogger(__name__)

COORDINATE_MAX = 999
DEFAULT_MAX_DEPTH = 6
DEFAULT_MAX_NODES = 250
DEFAULT_WALK_TIMEOUT_SECONDS = 2.5
MAX_NAME_CHARS = 120
MAX_VALUE_CHARS = 80
MAX_LISTED_WINDOWS = 10

# UIA ControlTypeId -> compact lowercase role name shown to models.
CONTROL_TYPE_ROLES: dict[int, str] = {
    50000: "button",
    50001: "calendar",
    50002: "checkbox",
    50003: "combobox",
    50004: "edit",
    50005: "hyperlink",
    50006: "image",
    50007: "listitem",
    50008: "list",
    50009: "menu",
    50010: "menubar",
    50011: "menuitem",
    50012: "progressbar",
    50013: "radio",
    50014: "scrollbar",
    50015: "slider",
    50016: "spinner",
    50017: "statusbar",
    50018: "tab",
    50019: "tabitem",
    50020: "text",
    50021: "toolbar",
    50022: "tooltip",
    50023: "tree",
    50024: "treeitem",
    50025: "custom",
    50026: "group",
    50027: "thumb",
    50028: "datagrid",
    50029: "dataitem",
    50030: "document",
    50031: "splitbutton",
    50032: "window",
    50033: "pane",
    50034: "header",
    50035: "headeritem",
    50036: "table",
    50037: "titlebar",
    50038: "separator",
    50039: "semanticzoom",
    50040: "appbar",
}

# Interactive roles kept even when their accessible name is empty.
INTERACTIVE_ROLES: frozenset[str] = frozenset(
    {
        "button",
        "checkbox",
        "combobox",
        "dataitem",
        "document",
        "edit",
        "hyperlink",
        "listitem",
        "menubar",
        "menuitem",
        "radio",
        "slider",
        "spinner",
        "splitbutton",
        "tabitem",
        "treeitem",
    }
)

# Structural/semantic roles kept when they carry a non-empty name or value.
NAMED_ROLES: frozenset[str] = frozenset(
    {
        "calendar",
        "custom",
        "datagrid",
        "group",
        "header",
        "headeritem",
        "image",
        "list",
        "menu",
        "pane",
        "progressbar",
        "statusbar",
        "tab",
        "table",
        "text",
        "titlebar",
        "toolbar",
        "tree",
        "window",
    }
)

_COINIT_MULTITHREADED = 0x0
_RPC_E_CHANGED_MODE = -2147417850
_UIA_CLSID = "{ff48dba4-60ef-4201-aa87-54103eef594e}"
_UIA_E_ELEMENTNOTAVAILABLE = -2147220991
_RPC_E_WRONG_THREAD = -2147417842

_thread_state = threading.local()


class UiaUnavailableError(Exception):
    """UI Automation cannot be used on this host or the requested window was not found."""


@dataclass(frozen=True, slots=True)
class RawControl:
    """One visible UI control discovered by a UIA backend walk."""

    role: str
    name: str
    depth: int
    rect: tuple[int, int, int, int]
    enabled: bool = True
    focused: bool = False
    value: str = ""
    is_password: bool = False
    checked: bool | None = None
    selected: bool | None = None
    expanded: bool | None = None
    automation_id: str = ""
    hwnd: int = 0
    element: Any = None
    owner_thread: int = 0


@dataclass(frozen=True, slots=True)
class UiControl:
    """One numbered UI control stored inside a snapshot."""

    uid: str
    role: str
    name: str
    depth: int
    rect: tuple[int, int, int, int]
    center_px: tuple[int, int]
    center_scaled: tuple[int, int]
    enabled: bool = True
    focused: bool = False
    value: str = ""
    is_password: bool = False
    checked: bool | None = None
    selected: bool | None = None
    expanded: bool | None = None
    automation_id: str = ""
    hwnd: int = 0
    element: Any = None
    owner_thread: int = 0


@dataclass(frozen=True, slots=True)
class UiTreeSnapshot:
    """Numbered snapshot of controls in one desktop window."""

    generation: int
    window_title: str
    window_rect: tuple[int, int, int, int]
    controls: tuple[UiControl, ...]
    by_uid: dict[str, UiControl]
    truncated_nodes: int = 0


class UiaBackend(Protocol):
    """Platform-specific UI Automation backend (injected as a fake in unit tests)."""

    def is_supported(self) -> bool:
        """Return whether UI Automation is available in this environment."""

    def capture_tree(
        self,
        *,
        window: str | None = None,
        max_depth: int = DEFAULT_MAX_DEPTH,
        max_nodes: int = DEFAULT_MAX_NODES,
        timeout_seconds: float = DEFAULT_WALK_TIMEOUT_SECONDS,
        cancel_event: threading.Event | None = None,
    ) -> tuple[str, tuple[int, int, int, int], list[RawControl], int]:
        """Walk the target window and return ``(title, rect, raw_controls, truncated_count)``."""

    def locate(self, control: UiControl) -> tuple[tuple[int, int, int, int], bool] | None:
        """Return fresh ``(rect, enabled)`` for ``control``, or ``None`` if it is gone or offscreen."""


def _clean_text(raw: Any, limit: int) -> str:
    if not isinstance(raw, str):
        return ""
    flat = " ".join(raw.split())
    if len(flat) <= limit:
        return flat
    return flat[: limit - 3] + "..."


def _quote(text: str) -> str:
    escaped = text.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def _intersect_screen(rect: tuple[int, int, int, int], screen_w: int, screen_h: int) -> tuple[int, int, int, int] | None:
    left, top, right, bottom = rect
    if right <= left or bottom <= top:
        return None
    clamped_left = max(0, left)
    clamped_top = max(0, top)
    clamped_right = min(max(1, screen_w), right)
    clamped_bottom = min(max(1, screen_h), bottom)
    if clamped_right <= clamped_left or clamped_bottom <= clamped_top:
        return None
    return clamped_left, clamped_top, clamped_right, clamped_bottom


def _center_of(rect: tuple[int, int, int, int], screen_w: int, screen_h: int) -> tuple[tuple[int, int], tuple[int, int]] | None:
    visible = _intersect_screen(rect, screen_w, screen_h)
    if visible is None:
        return None
    left, top, right, bottom = visible
    cx = (left + right) // 2
    cy = (top + bottom) // 2
    max_x = max(1, screen_w - 1)
    max_y = max(1, screen_h - 1)
    sx = max(0, min(COORDINATE_MAX, int(round(cx * COORDINATE_MAX / max_x))))
    sy = max(0, min(COORDINATE_MAX, int(round(cy * COORDINATE_MAX / max_y))))
    return (cx, cy), (sx, sy)


def _scale_rect(rect: tuple[int, int, int, int], screen_w: int, screen_h: int) -> tuple[int, int, int, int]:
    max_x = max(1, screen_w - 1)
    max_y = max(1, screen_h - 1)
    left, top, right, bottom = rect
    return (
        max(0, min(COORDINATE_MAX, int(round(left * COORDINATE_MAX / max_x)))),
        max(0, min(COORDINATE_MAX, int(round(top * COORDINATE_MAX / max_y)))),
        max(0, min(COORDINATE_MAX, int(round(right * COORDINATE_MAX / max_x)))),
        max(0, min(COORDINATE_MAX, int(round(bottom * COORDINATE_MAX / max_y)))),
    )


def _cap_lines(lines: list[str], max_tokens: int) -> tuple[str, int]:
    """Return as many whole lines as fit in ``max_tokens``, and how many that is."""
    from utils.tokenize import truncate_to_tokens

    text = "\n".join(lines)
    cut = truncate_to_tokens(text, max_tokens)
    if len(cut) >= len(text):
        return text, len(lines)
    shown, used = 0, 0
    for line in lines:
        used += len(line) + (1 if shown else 0)
        if used > len(cut):
            break
        shown += 1
    if shown:
        return "\n".join(lines[:shown]), shown
    left = max(0, len(lines[0]) - len(cut))
    return f"{cut} [... {left} more characters of this line are not shown]", 1


def format_control_line(control: UiControl) -> str:
    """Format one ``UiControl`` as an indented snapshot line."""
    indent = "  " * min(control.depth, 6)
    parts: list[str] = [f"uid={control.uid}", control.role]
    if control.name:
        parts.append(_quote(control.name))
    if control.is_password:
        parts.append("password")
    elif control.value and control.value != control.name:
        parts.append(f"value={_quote(control.value)}")
    if not control.enabled:
        parts.append("disabled")
    if control.focused:
        parts.append("focused")
    if control.checked is not None:
        parts.append("checked" if control.checked else "unchecked")
    if control.selected is True:
        parts.append("selected")
    if control.expanded is not None:
        parts.append("expanded" if control.expanded else "collapsed")
    sx, sy = control.center_scaled
    parts.append(f"@({sx},{sy})")
    return f"{indent}{' '.join(parts)}"


class DesktopUiTree:
    """Captures, renders, caches, and resolves Windows UI Automation control trees."""

    def __init__(self, backend: UiaBackend | None = None) -> None:
        self._backend: UiaBackend = backend if backend is not None else WindowsUiaBackend()
        self._generation = 0
        self._latest: UiTreeSnapshot | None = None

    def is_supported(self) -> bool:
        return bool(self._backend.is_supported())

    @property
    def latest(self) -> UiTreeSnapshot | None:
        return self._latest

    def invalidate(self) -> None:
        """Drop any cached UI tree snapshot so stale uids cannot be reused."""
        self._latest = None

    def snapshot(
        self,
        screen_w: int,
        screen_h: int,
        *,
        window: str | None = None,
        offset: int = 0,
        max_tokens: int | None = None,
        max_depth: int = DEFAULT_MAX_DEPTH,
        max_nodes: int = DEFAULT_MAX_NODES,
        timeout_seconds: float = DEFAULT_WALK_TIMEOUT_SECONDS,
        cancel_event: threading.Event | None = None,
    ) -> tuple[UiTreeSnapshot, str]:
        """Capture the UI control tree, store it as the latest snapshot, and render it."""
        from config.nekopower_settings import DESKTOP_UI_TREE_MAX_TOKENS

        if not self.is_supported():
            raise UiaUnavailableError(
                "UI Automation control trees are only available on Windows; use action screenshot with 0 to 999 coordinates."
            )
        if offset < 0:
            offset = 0
        cap = DESKTOP_UI_TREE_MAX_TOKENS if max_tokens is None else max(100, int(max_tokens))

        # Reuse the current snapshot when paginating with offset > 0 and matching window filter.
        if offset > 0 and self._latest is not None and (
            window is None or window.casefold() in self._latest.window_title.casefold()
        ):
            snap = self._latest
        else:
            title, win_rect, raw_controls, truncated = self._backend.capture_tree(
                window=window,
                max_depth=max_depth,
                max_nodes=max_nodes,
                timeout_seconds=timeout_seconds,
                cancel_event=cancel_event,
            )
            self._generation += 1
            generation = self._generation
            controls: list[UiControl] = []
            by_uid: dict[str, UiControl] = {}
            for raw in raw_controls:
                centers = _center_of(raw.rect, screen_w, screen_h)
                if centers is None:
                    continue
                center_px, center_scaled = centers
                uid = f"{generation}_{len(controls)}"
                control = UiControl(
                    uid=uid,
                    role=raw.role,
                    name=_clean_text(raw.name, MAX_NAME_CHARS),
                    depth=max(0, raw.depth),
                    rect=raw.rect,
                    center_px=center_px,
                    center_scaled=center_scaled,
                    enabled=raw.enabled,
                    focused=raw.focused,
                    value="" if raw.is_password else _clean_text(raw.value, MAX_VALUE_CHARS),
                    is_password=raw.is_password,
                    checked=raw.checked,
                    selected=raw.selected,
                    expanded=raw.expanded,
                    automation_id=raw.automation_id,
                    hwnd=raw.hwnd,
                    element=raw.element,
                    owner_thread=raw.owner_thread,
                )
                controls.append(control)
                by_uid[uid] = control
            snap = UiTreeSnapshot(
                generation=generation,
                window_title=_clean_text(title, MAX_NAME_CHARS) or "(untitled window)",
                window_rect=win_rect,
                controls=tuple(controls),
                by_uid=by_uid,
                truncated_nodes=truncated,
            )
            self._latest = snap
        rendered = self.render(snap, screen_w=screen_w, screen_h=screen_h, offset=offset, max_tokens=cap)
        return snap, rendered

    def render(
        self,
        snap: UiTreeSnapshot,
        *,
        screen_w: int,
        screen_h: int,
        offset: int = 0,
        max_tokens: int,
    ) -> str:
        """Render ``snap`` into bounded text with pagination notes when cut."""
        scaled_box = _scale_rect(snap.window_rect, screen_w, screen_h)
        header = (
            f"## Desktop UI Controls — Window: {_quote(snap.window_title)} "
            f"({scaled_box[0]},{scaled_box[1]})-({scaled_box[2]},{scaled_box[3]})"
        )
        all_lines = [format_control_line(ctrl) for ctrl in snap.controls]
        if not all_lines:
            return f"{header}\n[No visible UI Automation controls found in this window; use screenshot coordinates.]"
        if offset >= len(all_lines):
            return (
                f"{header}\n[Offset {offset} is past the end of the UI tree ({len(all_lines)} controls total). "
                "Call ui_tree without offset to read from the beginning.]"
            )
        page_lines = all_lines[offset:]
        # Reserve room for header + optional pagination footer.
        body_budget = max(80, max_tokens - 60)
        body, shown = _cap_lines(page_lines, body_budget)
        notes: list[str] = []
        end_index = offset + shown
        if end_index < len(all_lines):
            notes.append(
                f"[Showing controls {offset + 1}-{end_index} of {len(all_lines)}. "
                f"Call ui_tree with offset={end_index} to see the rest.]"
            )
        elif offset > 0:
            notes.append(f"[Showing controls {offset + 1}-{end_index} of {len(all_lines)}.]")
        if snap.truncated_nodes > 0:
            notes.append(
                f"[{snap.truncated_nodes} deeper or extra controls were not walked to keep the tree compact.]"
            )
        if notes:
            return f"{header}\n{body}\n" + "\n".join(notes)
        return f"{header}\n{body}"

    def resolve(self, uid: str, *, screen_w: int, screen_h: int) -> UiControl:
        """Look up ``uid`` in the latest snapshot and refresh its bounding rect when possible."""
        snap = self._latest
        if snap is None:
            raise UiaUnavailableError(
                f"No desktop UI tree snapshot is available for uid={uid!r}. "
                "Call computer with action ui_tree first."
            )
        control = snap.by_uid.get(uid)
        if control is None:
            raise UiaUnavailableError(
                f"Control uid={uid!r} is not in the latest desktop UI tree (generation {snap.generation}). "
                "Call computer with action ui_tree to get fresh control uids."
            )
        located = self._backend.locate(control)
        if located is None:
            raise UiaUnavailableError(
                f"Control uid={uid!r} ({control.role} {control.name!r}) is no longer visible on screen. "
                "Call computer with action ui_tree to refresh the control list."
            )
        rect, enabled = located
        if not enabled:
            raise UiaUnavailableError(
                f"Control uid={uid!r} ({control.role} {control.name!r}) is currently disabled and cannot be operated."
            )
        centers = _center_of(rect, screen_w, screen_h)
        if centers is None:
            raise UiaUnavailableError(
                f"Control uid={uid!r} ({control.role} {control.name!r}) is outside the visible screen. "
                "Call computer with action ui_tree to refresh."
            )
        center_px, center_scaled = centers
        return replace(control, rect=rect, center_px=center_px, center_scaled=center_scaled, enabled=enabled)


# ---------------------------------------------------------------------------
# Real Windows UI Automation backend (comtypes + UIAutomationCore.dll)
# ---------------------------------------------------------------------------


def _ensure_com_for_thread() -> tuple[Any, Any]:
    """Return ``(uia_module, IUIAutomation)`` initialized for the current worker thread."""
    cached = getattr(_thread_state, "uia", None)
    if cached is not None:
        return cached
    # Import comtypes before calling CoInitializeEx: comtypes' own module init calls
    # CoInitializeEx(COINIT_APARTMENTTHREADED) on first import and raises RPC_E_CHANGED_MODE
    # if the importing thread was already initialized as MTA.
    import comtypes.client

    ole32 = ctypes.windll.ole32
    hr = ole32.CoInitializeEx(None, _COINIT_MULTITHREADED)
    if hr < 0 and hr != _RPC_E_CHANGED_MODE:
        logger.debug("CoInitializeEx returned HRESULT 0x%x", hr & 0xFFFFFFFF)

    uia_mod = comtypes.client.GetModule("UIAutomationCore.dll")
    automation = comtypes.client.CreateObject(_UIA_CLSID, interface=uia_mod.IUIAutomation)
    pair = (uia_mod, automation)
    _thread_state.uia = pair
    return pair


def _read_rect(element: Any) -> tuple[int, int, int, int] | None:
    try:
        rect = element.CurrentBoundingRectangle
        left, top, right, bottom = int(rect.left), int(rect.top), int(rect.right), int(rect.bottom)
    except Exception:  # noqa: BLE001 - COM transient error on closing windows
        return None
    if right <= left or bottom <= top:
        return None
    return left, top, right, bottom


def _read_patterns(element: Any, uia_mod: Any, role: str, is_password: bool) -> tuple[str, bool | None, bool | None, bool | None]:
    value = ""
    checked: bool | None = None
    selected: bool | None = None
    expanded: bool | None = None
    if not is_password and role in ("edit", "combobox", "document", "spinner", "slider"):
        try:
            pat = element.GetCurrentPatternAs(uia_mod.UIA_ValuePatternId, uia_mod.IUIAutomationValuePattern)
            if pat:
                raw_val = pat.CurrentValue
                if isinstance(raw_val, str):
                    value = raw_val
        except Exception:  # noqa: BLE001 - pattern not supported
            pass
    if role in ("checkbox", "button", "menuitem", "splitbutton"):
        try:
            pat = element.GetCurrentPatternAs(uia_mod.UIA_TogglePatternId, uia_mod.IUIAutomationTogglePattern)
            if pat:
                state = int(pat.CurrentToggleState)
                if state in (0, 1):
                    checked = state == 1
        except Exception:  # noqa: BLE001 - pattern not supported
            pass
    if role in ("radio", "tabitem", "listitem", "treeitem", "dataitem"):
        try:
            pat = element.GetCurrentPatternAs(
                uia_mod.UIA_SelectionItemPatternId, uia_mod.IUIAutomationSelectionItemPattern
            )
            if pat:
                selected = bool(pat.CurrentIsSelected)
        except Exception:  # noqa: BLE001 - pattern not supported
            pass
    if role in ("combobox", "menuitem", "treeitem", "splitbutton"):
        try:
            pat = element.GetCurrentPatternAs(
                uia_mod.UIA_ExpandCollapsePatternId, uia_mod.IUIAutomationExpandCollapsePattern
            )
            if pat:
                state = int(pat.CurrentExpandCollapseState)
                if state in (0, 1, 2):
                    expanded = state != 0
        except Exception:  # noqa: BLE001 - pattern not supported
            pass
    return value, checked, selected, expanded


class WindowsUiaBackend:
    """Real Windows UI Automation backend backed by ``UIAutomationCore.dll`` via ``comtypes``."""

    def is_supported(self) -> bool:
        if platform.system().lower() != "windows":
            return False
        try:
            import importlib.util

            return importlib.util.find_spec("comtypes") is not None
        except Exception:  # noqa: BLE001
            return False

    def _find_target_window(self, automation: Any, walker: Any, window: str | None) -> tuple[Any, str, tuple[int, int, int, int], int]:
        if window:
            needle = window.strip().casefold()
            root = automation.GetRootElement()
            child = walker.GetFirstChildElement(root)
            candidates: list[str] = []
            while child is not None:
                try:
                    name = _clean_text(child.CurrentName, MAX_NAME_CHARS)
                    rect = _read_rect(child)
                    offscreen = bool(child.CurrentIsOffscreen)
                    hwnd = int(child.CurrentNativeWindowHandle or 0)
                except Exception:  # noqa: BLE001
                    name, rect, offscreen, hwnd = "", None, True, 0
                if name and rect is not None and not offscreen:
                    if needle in name.casefold():
                        return child, name, rect, hwnd
                    if len(candidates) < MAX_LISTED_WINDOWS:
                        candidates.append(name)
                try:
                    child = walker.GetNextSiblingElement(child)
                except Exception:  # noqa: BLE001
                    break
            available = ", ".join(_quote(title) for title in candidates) or "(none)"
            raise UiaUnavailableError(
                f"No visible window matching {window!r} was found. Open windows: {available}."
            )
        hwnd = int(ctypes.windll.user32.GetForegroundWindow() or 0)
        element = automation.ElementFromHandle(hwnd) if hwnd else automation.GetRootElement()
        if element is None:
            raise UiaUnavailableError("Could not read the foreground window via UI Automation.")
        try:
            name = _clean_text(element.CurrentName, MAX_NAME_CHARS)
        except Exception:  # noqa: BLE001
            name = ""
        rect = _read_rect(element) or (0, 0, 1, 1)
        return element, name, rect, hwnd

    def capture_tree(
        self,
        *,
        window: str | None = None,
        max_depth: int = DEFAULT_MAX_DEPTH,
        max_nodes: int = DEFAULT_MAX_NODES,
        timeout_seconds: float = DEFAULT_WALK_TIMEOUT_SECONDS,
        cancel_event: threading.Event | None = None,
    ) -> tuple[str, tuple[int, int, int, int], list[RawControl], int]:
        if cancel_event is not None and cancel_event.is_set():
            raise InterruptedError("stopped")
        uia_mod, automation = _ensure_com_for_thread()
        walker = automation.ControlViewWalker
        root_el, title, win_rect, root_hwnd = self._find_target_window(automation, walker, window)
        deadline = time.monotonic() + max(0.2, float(timeout_seconds))
        thread_id = threading.get_ident()
        collected: list[RawControl] = []
        truncated = 0

        # Depth-first traversal keeps parent-child ordering natural for models.
        stack: list[tuple[Any, int]] = [(root_el, 0)]
        visited = 0
        visit_limit = max_nodes * 4

        while stack:
            if cancel_event is not None and cancel_event.is_set():
                raise InterruptedError("stopped")
            if time.monotonic() >= deadline or len(collected) >= max_nodes or visited >= visit_limit:
                truncated += len(stack)
                break
            el, depth = stack.pop()
            visited += 1
            try:
                offscreen = bool(el.CurrentIsOffscreen)
                rect = _read_rect(el)
                if offscreen or rect is None:
                    continue
                ctrl_type = int(el.CurrentControlType)
                role = CONTROL_TYPE_ROLES.get(ctrl_type, "custom")
                name = _clean_text(el.CurrentName, MAX_NAME_CHARS)
                enabled = bool(el.CurrentIsEnabled)
                focused = bool(el.CurrentHasKeyboardFocus)
                is_password = bool(el.CurrentIsPassword)
                automation_id = _clean_text(el.CurrentAutomationId, 60)
                hwnd = int(el.CurrentNativeWindowHandle or 0) or root_hwnd
            except Exception:  # noqa: BLE001 - volatile UI element disappeared mid-walk
                continue

            value, checked, selected, expanded = _read_patterns(el, uia_mod, role, is_password)
            include = (
                depth == 0
                or role in INTERACTIVE_ROLES
                or (role in NAMED_ROLES and bool(name or value))
            )
            child_depth = depth + 1 if include else depth
            if include:
                collected.append(
                    RawControl(
                        role=role,
                        name=name,
                        depth=depth,
                        rect=rect,
                        enabled=enabled,
                        focused=focused,
                        value=value,
                        is_password=is_password,
                        checked=checked,
                        selected=selected,
                        expanded=expanded,
                        automation_id=automation_id,
                        hwnd=hwnd,
                        element=el,
                        owner_thread=thread_id,
                    )
                )
            if child_depth > max_depth:
                continue
            children: list[Any] = []
            try:
                child = walker.GetFirstChildElement(el)
                while child is not None:
                    children.append(child)
                    if len(children) >= max_nodes:
                        truncated += 1
                        break
                    child = walker.GetNextSiblingElement(child)
            except Exception:  # noqa: BLE001
                pass
            for child in reversed(children):
                stack.append((child, child_depth))

        return title, win_rect, collected, truncated

    def locate(self, control: UiControl) -> tuple[tuple[int, int, int, int], bool] | None:
        if control.hwnd:
            try:
                user32 = ctypes.windll.user32
                if not user32.IsWindow(control.hwnd) or not user32.IsWindowVisible(control.hwnd):
                    return None
            except Exception:  # noqa: BLE001
                pass
        el = control.element
        if el is None:
            return control.rect, control.enabled
        try:
            _ensure_com_for_thread()
            if bool(el.CurrentIsOffscreen):
                return None
            rect = _read_rect(el)
            if rect is None:
                return None
            enabled = bool(el.CurrentIsEnabled)
            return rect, enabled
        except OSError as exc:
            hresult = getattr(exc, "hresult", None) or (exc.args[0] if exc.args and isinstance(exc.args[0], int) else None)
            if hresult == _UIA_E_ELEMENTNOTAVAILABLE:
                return None
            if hresult == _RPC_E_WRONG_THREAD:
                return control.rect, control.enabled
            return None
        except Exception:  # noqa: BLE001
            # If the element came from a different COM apartment, fall back to its captured rect.
            if control.owner_thread and control.owner_thread != threading.get_ident():
                return control.rect, control.enabled
            return None
