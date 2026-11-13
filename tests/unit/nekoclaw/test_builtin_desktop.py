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

"""The keyboard-and-mouse tools: one action on the desktop per call.

No test here can reach the real mouse, keyboard, clipboard or screen. An
autouse fixture makes ``load_pyautogui`` and ``capture_frame`` raise, blocks
the import of ``pyautogui``, puts a fake ``pyperclip`` in its place, makes
the native screen grab raise and, on Windows, makes the system's own calls
that send input raise. Every ``Desktop`` and ``ScaledInput`` is built
on a fake backend that records what it is sent. The tests of
``load_pyautogui`` and ``capture_frame`` themselves put the real functions
back over fakes of what lies underneath (the import, the HTTP client, the
native grab).

The tests of the capture bridge and of the held-key release were first
written for ``brain/computer_use.py``, which ``desktop.py`` replaces; the
bridge tests each stand for an incident. The checks of the page that answers
the bridge (at the end) moved here with them.
"""

from __future__ import annotations

import base64
import importlib.abc
import importlib.machinery
import json
import random
import sys
import threading
import time
import types
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import httpx
import pytest
from PIL import Image

from nekoclaw.builtin import desktop
from nekoclaw.builtin.desktop import (
    ACTIONS,
    COORDINATE_MAX,
    EXECUTION_LOCK,
    SCREENSHOT_HEIGHT,
    SCREENSHOT_MAX_BYTES,
    TYPE_MAX_CHARACTERS,
    Desktop,
    DesktopActionError,
    DesktopStopped,
    ScaledInput,
    fit_jpeg,
    parse_keys,
)
from tests.fake_clock import patch_module_clock

ROOT = Path(__file__).resolve().parents[3]
REACHED = "a unit test reached the real pyautogui"

_REAL_LOAD_PYAUTOGUI = desktop.load_pyautogui
_REAL_CAPTURE_FRAME = desktop.capture_frame


_WINDOWS_INPUT_CALLS = ("SendInput", "mouse_event", "keybd_event", "SetCursorPos")


def _reached(*args: Any, **kwargs: Any) -> Any:
    raise AssertionError(REACHED)


class FakeClipboard(types.ModuleType):
    """Stands in for ``pyperclip``: the user's clipboard is never written to."""

    def __init__(self) -> None:
        super().__init__("pyperclip")
        self.copied: list[str] = []
        self.error: Exception | None = None
        # What the user had on the clipboard.
        self.content: Any = "what the user had copied"
        self.read_error: Exception | None = None

    def copy(self, text: str) -> None:
        if self.error is not None:
            raise self.error
        self.copied.append(text)
        self.content = text

    def paste(self) -> Any:
        if self.read_error is not None:
            raise self.read_error
        return self.content


@pytest.fixture(autouse=True)
def no_real_desktop(monkeypatch: Any) -> Any:
    """Make the real mouse, keyboard, clipboard and screen unreachable from every test of this file."""
    import utils.desktop_capture as native

    clipboard = FakeClipboard()
    sleeps: list[float] = []
    monkeypatch.setattr(desktop, "load_pyautogui", _reached)
    monkeypatch.setattr(desktop, "capture_frame", _reached)
    monkeypatch.setattr(desktop, "_pyautogui", None)
    monkeypatch.setattr(desktop, "_import_error", None)
    monkeypatch.setattr(native, "capture_desktop_screenshot", _reached)
    # ``None`` here makes ``import pyautogui`` fail, should anything get as far as importing it.
    monkeypatch.setitem(sys.modules, "pyautogui", None)
    monkeypatch.setitem(sys.modules, "pyperclip", clipboard)
    if sys.platform == "win32":
        # The last line of defence: the calls of Windows itself that send input, whoever makes them.
        import ctypes

        for name in _WINDOWS_INPUT_CALLS:
            monkeypatch.setattr(ctypes.windll.user32, name, _reached, raising=False)
    # The module's own sleeps are recorded instead of slept.
    patch_module_clock(monkeypatch, desktop, sleep=sleeps.append)
    yield SimpleNamespace(clipboard=clipboard, sleeps=sleeps)
    if EXECUTION_LOCK.locked():
        # A test that failed half way must not make the ones after it fail too.
        EXECUTION_LOCK.release()


# ---------------------------------------------------------------------------
# A fake pyautogui and a fake screen
# ---------------------------------------------------------------------------

_KEY_NAMES = (
    "enter return esc escape tab space backspace delete del insert up down left right home end pageup pagedown "
    "pgup pgdn ctrl ctrlleft ctrlright alt altleft altright shift shiftleft shiftright win winleft winright "
    "command option capslock printscreen f1 f2 f3 f4 f5 f6 f7 f8 f9 f10 f11 f12"
).split()


class FakeBackend:
    """Records what pyautogui would be asked to do. Nothing reaches a real device."""

    KEYBOARD_KEYS = [*"abcdefghijklmnopqrstuvwxyz0123456789", *"!\"#$%&'()*+,-./:;<=>?@[\\]^_`{|}~", " ", *_KEY_NAMES]

    def __init__(self, width: int = 1920, height: int = 1080) -> None:
        self.FAILSAFE = True
        self.width = width
        self.height = height
        self.calls: list[tuple[str, tuple[Any, ...], dict[str, Any]]] = []
        # method name -> called with the same arguments after the call was recorded; may raise.
        self.hooks: dict[str, Any] = {}

    @staticmethod
    def easeOutQuad(position: float) -> float:
        return position

    def size(self) -> tuple[int, int]:
        return (self.width, self.height)

    def _sent(self, name: str, args: tuple[Any, ...], kwargs: dict[str, Any]) -> None:
        self.calls.append((name, args, dict(kwargs)))
        hook = self.hooks.get(name)
        if hook is not None:
            hook(*args, **kwargs)

    def moveTo(self, *args: Any, **kwargs: Any) -> None:
        self._sent("moveTo", args, kwargs)

    def click(self, *args: Any, **kwargs: Any) -> None:
        self._sent("click", args, kwargs)

    def mouseDown(self, *args: Any, **kwargs: Any) -> None:
        self._sent("mouseDown", args, kwargs)

    def mouseUp(self, *args: Any, **kwargs: Any) -> None:
        self._sent("mouseUp", args, kwargs)

    def scroll(self, *args: Any, **kwargs: Any) -> None:
        self._sent("scroll", args, kwargs)

    def hscroll(self, *args: Any, **kwargs: Any) -> None:
        self._sent("hscroll", args, kwargs)

    def write(self, *args: Any, **kwargs: Any) -> None:
        self._sent("write", args, kwargs)

    def press(self, *args: Any, **kwargs: Any) -> None:
        self._sent("press", args, kwargs)

    def hotkey(self, *args: Any, **kwargs: Any) -> None:
        self._sent("hotkey", args, kwargs)

    def keyDown(self, *args: Any, **kwargs: Any) -> None:
        self._sent("keyDown", args, kwargs)

    def keyUp(self, *args: Any, **kwargs: Any) -> None:
        self._sent("keyUp", args, kwargs)


class FakeScreen:
    """Stands in for ``capture_frame``; its calls go to the same log as the backend's."""

    def __init__(self, log: list[tuple[str, tuple[Any, ...], dict[str, Any]]], size: tuple[int, int] = (64, 36)) -> None:
        self.log = log
        self.size = size
        self.image: Any = None
        self.error: Exception | None = None
        self.cancel_events: list[Any] = []

    def __call__(self, cancel_event: Any = None) -> Any:
        self.log.append(("capture", (), {}))
        self.cancel_events.append(cancel_event)
        if self.error is not None:
            raise self.error
        return self.image if self.image is not None else Image.new("RGB", self.size, "navy")


class Rig:
    """A ``Desktop`` on a fake backend and a fake screen, with one log of everything it did."""

    def __init__(self, monkeypatch: Any, *, settle_seconds: float = 0, width: int = 1920, height: int = 1080) -> None:
        self.backend = FakeBackend(width, height)
        self.screen = FakeScreen(self.backend.calls)
        self.desktop = Desktop(backend=self.backend, capture=self.screen, settle_seconds=settle_seconds)
        patch_module_clock(monkeypatch, desktop, sleep=lambda seconds: self.backend.calls.append(("sleep", (seconds,), {})))
        self.system = "Linux"
        monkeypatch.setattr(desktop, "platform", SimpleNamespace(system=lambda: self.system))

    def act(self, action: str, arguments: dict[str, Any] | None = None, cancel_event: Any = None) -> Any:
        return self.desktop.act(action, arguments or {}, cancel_event)

    def log(self) -> list[tuple[str, tuple[Any, ...], dict[str, Any]]]:
        return list(self.backend.calls)

    def inputs(self) -> list[tuple[str, tuple[Any, ...], dict[str, Any]]]:
        """Return what was sent to the mouse and the keyboard."""
        return [call for call in self.backend.calls if call[0] not in ("capture", "sleep")]

    def names(self) -> list[str]:
        return [call[0] for call in self.backend.calls]


@pytest.fixture
def rig(monkeypatch: Any) -> Rig:
    return Rig(monkeypatch)


def glide(px: int, py: int, duration: float = 0.3) -> tuple[str, tuple[Any, ...], dict[str, Any]]:
    """The eased move of the pointer that comes before a click."""
    return ("moveTo", (px, py), {"duration": duration, "_pause": False, "tween": FakeBackend.easeOutQuad})


SETTLE = ("sleep", (0,), {})
CAPTURE = ("capture", (), {})
AFTERWARDS = " Screenshot after it: 64x36 px. Coordinates are 0 to 999 on both axes, not pixels."


# ---------------------------------------------------------------------------
# Coordinates
# ---------------------------------------------------------------------------


def test_coordinates_from_0_to_999_are_projected_onto_the_screen() -> None:
    gui = ScaledInput(FakeBackend(), 1920, 1080)
    assert gui.to_pixels(500, 500) == (960, 540)
    assert gui.to_pixels(250, 750) == (480, 810)
    assert gui.to_pixels(100, 200) == (192, 216)
    wide = ScaledInput(FakeBackend(), 3840, 1600)
    assert wide.to_pixels(500, 500) == (1921, 800)
    assert wide.to_pixels(250, 750) == (961, 1200)


def test_the_corners_are_kept_a_few_pixels_off_the_edges_of_the_screen() -> None:
    gui = ScaledInput(FakeBackend(), 1920, 1080)
    assert gui.to_pixels(0, 0) == (4, 4)
    assert gui.to_pixels(999, 999) == (1915, 1075)
    assert gui.to_pixels(0, 999) == (4, 1075)
    assert gui.to_pixels(1, 998) == (4, 1075)
    # Just inside the margin nothing is moved.
    assert gui.to_pixels(3, 5) == (6, 5)


def test_a_screen_too_small_for_the_margin_is_used_to_its_edges() -> None:
    tiny = ScaledInput(FakeBackend(), 8, 6)
    assert tiny.to_pixels(0, 0) == (0, 0)
    assert tiny.to_pixels(999, 999) == (7, 5)


def test_the_screen_size_is_asked_of_the_backend_for_every_action(monkeypatch: Any) -> None:
    rig = Rig(monkeypatch, width=2560, height=1440)
    rig.act("move", {"x": 999, "y": 999})
    assert rig.inputs() == [glide(2555, 1435)]
    rig.backend.calls.clear()
    # The user changed the resolution between two calls.
    rig.backend.width, rig.backend.height = 1280, 720
    rig.act("move", {"x": 999, "y": 999})
    assert rig.inputs() == [glide(1275, 715)]


# ---------------------------------------------------------------------------
# The eight actions
# ---------------------------------------------------------------------------


def test_the_actions_are_the_eight_of_the_tool_definition() -> None:
    from config.prompts.prompts_nekoclaw_builtin import COMPUTER_TOOL

    assert list(ACTIONS) == COMPUTER_TOOL["parameters"]["properties"]["action"]["enum"]
    assert len(ACTIONS) == 8
    assert COORDINATE_MAX == 999


def test_screenshot_sends_nothing_and_looks_at_the_screen(rig: Rig) -> None:
    result = rig.act("screenshot")
    assert rig.log() == [CAPTURE]
    assert result.text == "Looked at the screen." + AFTERWARDS


def test_click_moves_the_pointer_there_and_clicks_the_left_button_once(rig: Rig) -> None:
    result = rig.act("click", {"x": 250, "y": 750})
    assert rig.log() == [
        glide(480, 810),
        ("click", (480, 810), {"clicks": 1, "interval": 0.0, "button": "left"}),
        SETTLE,
        CAPTURE,
    ]
    assert result.text == "Clicked the left button at (250, 750)." + AFTERWARDS


@pytest.mark.parametrize(
    ("arguments", "sent", "said"),
    [
        ({"button": "right"}, {"clicks": 1, "interval": 0.0, "button": "right"}, "Clicked the right button"),
        ({"button": "middle"}, {"clicks": 1, "interval": 0.0, "button": "middle"}, "Clicked the middle button"),
        ({"clicks": 2}, {"clicks": 2, "interval": 0.08, "button": "left"}, "Double-clicked the left button"),
        ({"clicks": 3, "button": "left"}, {"clicks": 3, "interval": 0.08, "button": "left"}, "Triple-clicked the left button"),
        ({"clicks": 2.0, "button": None}, {"clicks": 2, "interval": 0.08, "button": "left"}, "Double-clicked the left button"),
        ({"clicks": None}, {"clicks": 1, "interval": 0.0, "button": "left"}, "Clicked the left button"),
    ],
)
def test_click_takes_a_button_and_a_number_of_clicks(rig: Rig, arguments: dict[str, Any], sent: dict[str, Any], said: str) -> None:
    result = rig.act("click", {"x": 100, "y": 200, **arguments})
    assert rig.inputs() == [glide(192, 216), ("click", (192, 216), sent)]
    assert result.text == f"{said} at (100, 200)." + AFTERWARDS


def test_a_whole_number_written_as_a_float_is_a_coordinate(rig: Rig) -> None:
    rig.act("click", {"x": 100.0, "y": 200.0})
    assert rig.inputs()[-1] == ("click", (192, 216), {"clicks": 1, "interval": 0.0, "button": "left"})


def test_the_first_and_the_last_coordinate_are_accepted(rig: Rig) -> None:
    rig.act("click", {"x": 0, "y": 999})
    assert rig.inputs()[-1][1] == (4, 1075)


def test_move_only_moves_the_pointer(rig: Rig) -> None:
    result = rig.act("move", {"x": 800, "y": 600})
    assert rig.log() == [glide(1537, 648), SETTLE, CAPTURE]
    assert result.text == "Moved the pointer to (800, 600)." + AFTERWARDS


def test_the_pointer_is_moved_plainly_when_the_eased_move_fails(rig: Rig) -> None:
    def fails_with_a_tween(*args: Any, **kwargs: Any) -> None:
        if "tween" in kwargs:
            raise TypeError("this backend knows no tween")

    rig.backend.hooks["moveTo"] = fails_with_a_tween
    rig.act("click", {"x": 500, "y": 500})
    assert rig.inputs() == [
        glide(960, 540),
        ("moveTo", (960, 540), {"_pause": False}),
        ("click", (960, 540), {"clicks": 1, "interval": 0.0, "button": "left"}),
    ]


def test_a_pointer_that_cannot_be_moved_at_all_does_not_stop_the_click(rig: Rig) -> None:
    def broken(*args: Any, **kwargs: Any) -> None:
        raise RuntimeError("no pointer today")

    rig.backend.hooks["moveTo"] = broken
    rig.act("click", {"x": 500, "y": 500})
    assert rig.inputs()[-1] == ("click", (960, 540), {"clicks": 1, "interval": 0.0, "button": "left"})


def test_drag_presses_at_the_start_moves_and_releases_at_the_end(rig: Rig) -> None:
    result = rig.act("drag", {"x": 100, "y": 200, "to_x": 800, "to_y": 600})
    assert rig.log() == [
        glide(192, 216),
        ("mouseDown", (192, 216), {"button": "left"}),
        ("sleep", (0.1,), {}),
        ("moveTo", (1537, 648), {"duration": 0.5}),
        ("sleep", (0.1,), {}),
        ("mouseUp", (1537, 648), {"button": "left"}),
        SETTLE,
        CAPTURE,
    ]
    assert result.text == "Dragged from (100, 200) to (800, 600)." + AFTERWARDS
    assert rig.desktop._held_buttons == []


def test_drag_releases_the_button_even_when_the_move_raises(rig: Rig) -> None:
    def breaks_half_way(*args: Any, **kwargs: Any) -> None:
        if kwargs.get("duration") == 0.5:
            raise RuntimeError("the pointer hit something")

    rig.backend.hooks["moveTo"] = breaks_half_way
    with pytest.raises(RuntimeError, match="the pointer hit something"):
        rig.act("drag", {"x": 100, "y": 200, "to_x": 800, "to_y": 600})
    # Let go where the pointer is: let go at the end point, the drag would be carried out after all.
    assert rig.inputs()[-2:] == [("moveTo", (1537, 648), {"duration": 0.5}), ("mouseUp", (), {"button": "left"})]
    assert rig.desktop._held_buttons == []
    # Nothing is left for a later release, and no screenshot of a half-done drag is handed out.
    assert "capture" not in rig.names()
    assert not EXECUTION_LOCK.locked()


@pytest.mark.parametrize(
    ("system", "arguments", "sent", "said"),
    [
        ("Linux", {}, ("scroll", (-3,), {}), "Scrolled down 3 notch(es)."),
        ("Linux", {"direction": "up", "amount": 5}, ("scroll", (5,), {}), "Scrolled up 5 notch(es)."),
        ("Linux", {"direction": "left", "amount": 2}, ("hscroll", (-2,), {}), "Scrolled left 2 notch(es)."),
        ("Linux", {"direction": "right", "amount": 1}, ("hscroll", (1,), {}), "Scrolled right 1 notch(es)."),
        ("Darwin", {"direction": "down", "amount": 4}, ("scroll", (-4,), {}), "Scrolled down 4 notch(es)."),
        # Windows takes the number as it is, and one notch of the wheel is 120 there.
        ("Windows", {}, ("scroll", (-360,), {}), "Scrolled down 3 notch(es)."),
        ("Windows", {"direction": "up", "amount": 1}, ("scroll", (120,), {}), "Scrolled up 1 notch(es)."),
    ],
)
def test_scroll_turns_the_wheel_in_the_unit_of_the_platform(
    rig: Rig, system: str, arguments: dict[str, Any], sent: tuple[Any, ...], said: str
) -> None:
    rig.system = system
    result = rig.act("scroll", arguments)
    assert rig.log() == [sent, SETTLE, CAPTURE]
    assert result.text == said + AFTERWARDS


@pytest.mark.parametrize(("direction", "amount"), [("right", 240), ("left", -240)])
def test_on_windows_a_horizontal_scroll_turns_the_horizontal_wheel_and_not_through_pyautogui(
    rig: Rig, monkeypatch: Any, direction: str, amount: int
) -> None:
    # pyautogui 0.9.54 turns the vertical wheel for ``hscroll`` on Windows
    # (``_pyautogui_win._hscroll`` calls ``_scroll``): right would scroll up.
    rig.system = "Windows"
    monkeypatch.setattr(
        desktop, "turn_wheel_sideways_on_windows", lambda turned: rig.backend.calls.append(("hwheel", (turned,), {}))
    )
    result = rig.act("scroll", {"direction": direction, "amount": 2})
    assert rig.inputs() == [("hwheel", (amount,), {})]
    assert result.text == f"Scrolled {direction} 2 notch(es)." + AFTERWARDS


def test_the_horizontal_wheel_of_windows_is_turned_by_the_call_of_windows_for_it(monkeypatch: Any) -> None:
    import ctypes

    sent: list[tuple[Any, ...]] = []
    user32 = SimpleNamespace(mouse_event=lambda *arguments: sent.append(arguments))
    monkeypatch.setattr(ctypes, "windll", SimpleNamespace(user32=user32), raising=False)
    desktop.turn_wheel_sideways_on_windows(240)
    desktop.turn_wheel_sideways_on_windows(-120)
    # MOUSEEVENTF_HWHEEL, no movement, the amount (120 a notch, positive to the right), no extra information.
    assert sent == [(0x01000, 0, 0, 240, 0), (0x01000, 0, 0, -120, 0)]


def test_scroll_at_a_point_moves_the_pointer_there_first(rig: Rig) -> None:
    result = rig.act("scroll", {"direction": "down", "amount": 2, "x": 500, "y": 500})
    assert rig.inputs() == [glide(960, 540, duration=0.15), ("scroll", (-2,), {})]
    assert result.text == "Scrolled down 2 notch(es) at (500, 500)." + AFTERWARDS


@pytest.mark.parametrize(("amount", "notches"), [(0, 1), (-4, 1), (2.9, 2), (50, 50), (500, 50), (None, 3)])
def test_the_number_of_notches_is_kept_between_one_and_fifty(rig: Rig, amount: Any, notches: int) -> None:
    rig.act("scroll", {"direction": "up", "amount": amount})
    assert rig.inputs() == [("scroll", (notches,), {})]


def test_plain_text_is_typed_through_the_backend(rig: Rig, no_real_desktop: Any) -> None:
    result = rig.act("type", {"text": "Hello, world! 42"})
    assert rig.log() == [("write", ("Hello, world! 42",), {}), SETTLE, CAPTURE]
    assert result.text == "Typed 16 character(s)." + AFTERWARDS
    assert no_real_desktop.clipboard.copied == []


@pytest.mark.parametrize(("system", "modifier"), [("Windows", "ctrl"), ("Linux", "ctrl"), ("Darwin", "command")])
def test_text_beyond_ascii_goes_through_the_clipboard_and_is_pasted(
    rig: Rig, no_real_desktop: Any, system: str, modifier: str
) -> None:
    rig.system = system
    result = rig.act("type", {"text": "你好, café"})
    # Pasted, and then what the user had on the clipboard is put back.
    assert no_real_desktop.clipboard.copied == ["你好, café", "what the user had copied"]
    assert no_real_desktop.clipboard.content == "what the user had copied"
    # Not before the application had time to read the clipboard for the paste.
    assert rig.log() == [("hotkey", (modifier, "v"), {}), ("sleep", (desktop.PASTE_SETTLE_SECONDS,), {}), SETTLE, CAPTURE]
    assert desktop.PASTE_SETTLE_SECONDS >= 0.2
    assert result.text == "Typed 8 character(s)." + AFTERWARDS


def test_the_clipboard_is_put_back_when_pasting_fails_and_left_alone_when_it_could_not_be_read(
    rig: Rig, no_real_desktop: Any
) -> None:
    def keys_fail(*_args: Any, **_kwargs: Any) -> None:
        raise RuntimeError("the keys could not be sent")

    rig.backend.hooks["hotkey"] = keys_fail
    rig.act("type", {"text": "你好"})
    # Typed key by key instead; the clipboard is as the user left it.
    assert no_real_desktop.clipboard.content == "what the user had copied"
    assert ("write", ("你好",), {}) in rig.inputs()

    rig.backend.hooks.pop("hotkey")
    no_real_desktop.clipboard.copied.clear()
    no_real_desktop.clipboard.read_error = RuntimeError("the clipboard holds a picture")
    rig.act("type", {"text": "再见"})
    # Nothing was read, so there is nothing to put back.
    assert no_real_desktop.clipboard.copied == ["再见"]


@pytest.mark.parametrize(
    ("action", "arguments", "done"),
    [
        ("click", {"x": 500, "y": 500}, "Clicked"),
        ("type", {"text": "hello"}, "Typed 5 character(s)."),
        ("key", {"keys": "enter"}, "Pressed"),
    ],
)
def test_an_action_that_was_carried_out_is_told_as_done_when_the_screenshot_after_it_fails(
    rig: Rig, action: str, arguments: dict[str, Any], done: str
) -> None:
    rig.screen.error = RuntimeError("the screen could not be grabbed")
    result = rig.act(action, arguments)
    # Told as a failure, it would be done a second time: a second click, the text typed twice.
    assert result.text.startswith(done)
    assert result.text.endswith(
        "The screenshot after it could not be taken (RuntimeError). The action was carried out: do not repeat it. "
        "Call computer with action screenshot to see the screen."
    )
    assert result.screenshot is None
    assert [name for name in rig.names() if name not in ("sleep", "capture")]
    assert not EXECUTION_LOCK.locked()


def test_a_screenshot_that_was_asked_for_and_fails_is_a_failure(rig: Rig) -> None:
    rig.screen.error = RuntimeError("the screen could not be grabbed")
    with pytest.raises(RuntimeError, match="the screen could not be grabbed"):
        rig.act("screenshot", {})
    assert not EXECUTION_LOCK.locked()


def test_text_beyond_ascii_is_typed_key_by_key_when_the_clipboard_fails(rig: Rig, no_real_desktop: Any) -> None:
    no_real_desktop.clipboard.error = RuntimeError("no clipboard on this display")
    rig.act("type", {"text": "naïve"})
    assert rig.inputs() == [("write", ("naïve",), {})]
    assert no_real_desktop.clipboard.copied == []


def test_text_at_the_limit_is_typed(rig: Rig) -> None:
    text = "a" * TYPE_MAX_CHARACTERS
    result = rig.act("type", {"text": text})
    assert rig.inputs() == [("write", (text,), {})]
    assert result.text.startswith("Typed 4000 character(s).")


def test_one_key_is_pressed(rig: Rig) -> None:
    result = rig.act("key", {"keys": "enter"})
    assert rig.log() == [("press", ("enter",), {}), SETTLE, CAPTURE]
    assert result.text == "Pressed enter." + AFTERWARDS


@pytest.mark.parametrize(
    ("keys", "sent", "said"),
    [
        ("ctrl+c", ("ctrl", "c"), "ctrl+c"),
        ("alt+f4", ("alt", "f4"), "alt+f4"),
        ("win+r", ("win", "r"), "win+r"),
        ("Control+Shift+Escape", ("ctrl", "shift", "esc"), "ctrl+shift+esc"),
        ("ctrl++", ("ctrl", "+"), "ctrl++"),
        ("cmd+space", ("command", "space"), "command+space"),
    ],
)
def test_a_combination_is_pressed_as_a_hotkey(rig: Rig, keys: str, sent: tuple[str, ...], said: str) -> None:
    result = rig.act("key", {"keys": keys})
    assert rig.inputs() == [("hotkey", sent, {})]
    assert result.text == f"Pressed {said}." + AFTERWARDS


def test_a_key_held_is_pressed_kept_down_and_released_in_reverse_order(rig: Rig) -> None:
    result = rig.act("key", {"keys": "ctrl+shift+w", "hold_seconds": 1.5})
    assert rig.log() == [
        ("keyDown", ("ctrl",), {}),
        ("keyDown", ("shift",), {}),
        ("keyDown", ("w",), {}),
        ("sleep", (1.5,), {}),
        ("keyUp", ("w",), {}),
        ("keyUp", ("shift",), {}),
        ("keyUp", ("ctrl",), {}),
        SETTLE,
        CAPTURE,
    ]
    assert result.text == "Pressed ctrl+shift+w and held it 1.5 s." + AFTERWARDS
    assert rig.desktop._held_keys == []


@pytest.mark.parametrize(("asked", "held"), [(60, 10.0), (10, 10.0), (0.25, 0.25)])
def test_a_key_is_held_ten_seconds_at_most(rig: Rig, asked: float, held: float) -> None:
    rig.act("key", {"keys": "w", "hold_seconds": asked})
    assert rig.log()[:3] == [("keyDown", ("w",), {}), ("sleep", (held,), {}), ("keyUp", ("w",), {})]


@pytest.mark.parametrize("hold", [0, -3, None])
def test_no_hold_time_means_an_ordinary_press(rig: Rig, hold: Any) -> None:
    rig.act("key", {"keys": "w", "hold_seconds": hold})
    assert rig.inputs() == [("press", ("w",), {})]


@pytest.mark.parametrize(
    ("arguments", "slept", "said"),
    [({}, 1.0, "Waited 1 s."), ({"seconds": 2.5}, 2.5, "Waited 2.5 s."), ({"seconds": 500}, 30.0, "Waited 30 s."), ({"seconds": -5}, 0.0, "Waited 0 s.")],
)
def test_wait_waits_and_sends_nothing(rig: Rig, arguments: dict[str, Any], slept: float, said: str) -> None:
    result = rig.act("wait", arguments)
    assert rig.log() == [("sleep", (slept,), {}), SETTLE, CAPTURE]
    assert result.text == said + AFTERWARDS


ONE_OF_EACH: list[tuple[str, dict[str, Any]]] = [
    ("screenshot", {}),
    ("click", {"x": 10, "y": 10}),
    ("move", {"x": 10, "y": 10}),
    ("drag", {"x": 10, "y": 10, "to_x": 20, "to_y": 20}),
    ("scroll", {"direction": "down"}),
    ("type", {"text": "abc"}),
    ("key", {"keys": "enter"}),
    ("wait", {"seconds": 0.5}),
]


def test_there_is_a_case_for_every_action() -> None:
    assert [action for action, _arguments in ONE_OF_EACH] == list(ACTIONS)


@pytest.mark.parametrize(("action", "arguments"), ONE_OF_EACH)
def test_every_action_returns_a_screenshot_taken_after_it_and_says_how_to_read_it(
    monkeypatch: Any, action: str, arguments: dict[str, Any]
) -> None:
    rig = Rig(monkeypatch, settle_seconds=0.6)
    rig.screen.size = (320, 180)
    result = rig.act(action, arguments)
    names = rig.names()
    # One screenshot, the last thing done; after an action the screen is given time to settle first.
    assert names.count("capture") == 1 and names[-1] == "capture"
    if action == "screenshot":
        assert names == ["capture"]
    else:
        assert rig.log()[-2] == ("sleep", (0.6,), {})
    assert result.mime_type == "image/jpeg"
    assert result.screenshot is not None and result.screenshot[:3] == b"\xff\xd8\xff"
    with Image.open(BytesIO(result.screenshot)) as picture:
        assert picture.size == (320, 180)
    assert "Screenshot after it: 320x180 px." in result.text
    assert "Coordinates are 0 to 999 on both axes, not pixels." in result.text
    assert not EXECUTION_LOCK.locked()


def test_the_screen_settles_for_the_default_time_unless_told_otherwise() -> None:
    assert desktop.SETTLE_SECONDS == 0.6
    built = Desktop(backend=FakeBackend(), capture=_reached)
    assert built._settle_seconds == desktop.SETTLE_SECONDS


# ---------------------------------------------------------------------------
# What is refused
# ---------------------------------------------------------------------------

REFUSED: list[tuple[str, dict[str, Any], str]] = [
    ("fly", {}, "unknown action 'fly'"),
    ("", {}, "unknown action ''"),
    ("Click", {"x": 1, "y": 1}, "unknown action 'Click'"),
    ("click", {"y": 5}, "x is required"),
    ("click", {"x": 5}, "y is required"),
    ("click", {"x": 1000, "y": 5}, "x=1000 is not a coordinate"),
    ("click", {"x": -1, "y": 5}, "x=-1 is not a coordinate"),
    ("click", {"x": 5, "y": 1920}, "y=1920 is not a coordinate"),
    ("click", {"x": 10.5, "y": 5}, "x=10.5 is not a coordinate"),
    ("click", {"x": "10", "y": 5}, "x must be an integer from 0 to 999"),
    ("click", {"x": True, "y": 5}, "x must be an integer from 0 to 999"),
    ("click", {"x": [10], "y": 5}, "x must be an integer from 0 to 999"),
    ("click", {"x": 1, "y": 1, "button": "side"}, "button must be one of: left, right, middle"),
    ("click", {"x": 1, "y": 1, "button": "LEFT"}, "button must be one of"),
    ("click", {"x": 1, "y": 1, "clicks": 4}, "clicks must be 1, 2 or 3"),
    ("click", {"x": 1, "y": 1, "clicks": -1}, "clicks must be 1, 2 or 3"),
    ("click", {"x": 1, "y": 1, "clicks": 1.5}, "clicks must be 1, 2 or 3"),
    ("click", {"x": 1, "y": 1, "clicks": "2"}, "clicks must be 1, 2 or 3"),
    ("click", {"x": 1, "y": 1, "clicks": True}, "clicks must be 1, 2 or 3"),
    ("move", {}, "x is required"),
    ("move", {"x": 5, "y": 999.5}, "y=999.5 is not a coordinate"),
    ("drag", {"to_x": 5, "to_y": 5}, "x is required"),
    ("drag", {"x": 1, "y": 1, "to_x": 5}, "to_y is required"),
    ("drag", {"x": 1, "y": 1, "to_x": 5, "to_y": 1200}, "to_y=1200 is not a coordinate"),
    ("drag", {"x": 1, "y": 1, "to_x": "5", "to_y": 5}, "to_x must be an integer"),
    ("scroll", {"direction": "diagonal"}, "direction must be one of: up, down, left, right"),
    ("scroll", {"x": 5}, "give both x and y"),
    ("scroll", {"y": 5}, "give both x and y"),
    ("scroll", {"x": 1500, "y": 5}, "x=1500 is not a coordinate"),
    ("scroll", {"amount": "many"}, "amount must be a number"),
    ("scroll", {"amount": True}, "amount must be a number"),
    ("type", {}, "text is required"),
    ("type", {"text": ""}, "text is required"),
    ("type", {"text": 5}, "text is required"),
    ("type", {"text": "a" * (TYPE_MAX_CHARACTERS + 1)}, "text is longer than 4000 characters"),
    ("key", {}, "keys is required"),
    ("key", {"keys": ""}, "keys is required"),
    ("key", {"keys": "   "}, "keys is required"),
    ("key", {"keys": 13}, "keys is required"),
    ("key", {"keys": "ctrl+"}, "ends with a + that joins nothing"),
    ("key", {"keys": "ctrl++c"}, "has an empty part"),
    ("key", {"keys": "hyperkey"}, "'hyperkey' is not a key name"),
    ("key", {"keys": "ctrl+nokey"}, "'nokey' is not a key name"),
    ("key", {"keys": "a", "hold_seconds": "long"}, "hold_seconds must be a number"),
    ("wait", {"seconds": "long"}, "seconds must be a number"),
    ("wait", {"seconds": True}, "seconds must be a number"),
]


@pytest.mark.parametrize(("action", "arguments", "reason"), REFUSED)
def test_an_action_that_cannot_be_carried_out_as_asked_is_refused_and_nothing_is_sent(
    rig: Rig, action: str, arguments: dict[str, Any], reason: str
) -> None:
    with pytest.raises(DesktopActionError) as caught:
        rig.act(action, arguments)
    assert reason in str(caught.value)
    assert rig.log() == []
    assert not EXECUTION_LOCK.locked()


def test_a_coordinate_out_of_range_says_what_the_range_is(rig: Rig) -> None:
    with pytest.raises(DesktopActionError) as caught:
        rig.act("click", {"x": 1280, "y": 400})
    message = str(caught.value)
    assert "both axes run from 0 to 999" in message
    assert "Do not use pixels of the screenshot" in message


# ---------------------------------------------------------------------------
# Key names
# ---------------------------------------------------------------------------

KNOWN = set(FakeBackend.KEYBOARD_KEYS)


@pytest.mark.parametrize(
    ("written", "names"),
    [
        ("enter", ["enter"]),
        ("ctrl+c", ["ctrl", "c"]),
        ("ctrl+shift+esc", ["ctrl", "shift", "esc"]),
        ("ctrl++", ["ctrl", "+"]),
        ("+", ["+"]),
        ("-", ["-"]),
        (" ctrl + c ", ["ctrl", "c"]),
        ("f5", ["f5"]),
    ],
)
def test_a_key_or_a_combination_joined_with_plus_is_read(written: str, names: list[str]) -> None:
    assert parse_keys(written, KNOWN) == names
    assert parse_keys(written) == names


@pytest.mark.parametrize(
    ("written", "name"),
    [
        ("control", "ctrl"),
        ("ctl", "ctrl"),
        ("cmd", "command"),
        ("meta", "win"),
        ("super", "win"),
        ("windows", "win"),
        ("option", "alt"),
        ("return", "enter"),
        ("escape", "esc"),
        ("del", "delete"),
        ("ins", "insert"),
        ("pgup", "pageup"),
        ("pgdn", "pagedown"),
        ("arrowup", "up"),
        ("arrowdown", "down"),
        ("arrowleft", "left"),
        ("arrowright", "right"),
        ("spacebar", "space"),
    ],
)
def test_other_names_of_a_key_become_the_one_the_backend_knows(written: str, name: str) -> None:
    assert parse_keys(written, KNOWN) == [name]
    assert parse_keys(f"shift+{written.upper()}", KNOWN) == ["shift", name]


def test_names_are_read_whatever_their_case() -> None:
    assert parse_keys("CTRL+C", KNOWN) == ["ctrl", "c"]
    assert parse_keys("Enter", KNOWN) == ["enter"]
    assert parse_keys("Ctrl+Shift+T", KNOWN) == ["ctrl", "shift", "t"]
    # Without the backend's list a single character is left as it was written.
    assert parse_keys("Ctrl+A") == ["ctrl", "A"]
    assert parse_keys("A", KNOWN) == ["a"]


@pytest.mark.parametrize("written", ["ctrl+", "ctrl+c+", "a+"])
def test_a_trailing_plus_joins_nothing(written: str) -> None:
    with pytest.raises(DesktopActionError, match="ends with a \\+ that joins nothing"):
        parse_keys(written, KNOWN)


@pytest.mark.parametrize("written", ["+a", "ctrl++c", "++"])
def test_an_empty_part_is_refused(written: str) -> None:
    with pytest.raises(DesktopActionError, match="has an empty part"):
        parse_keys(written, KNOWN)


@pytest.mark.parametrize("written", ["hyperkey", "ctrl+nokey", "entr", "ctrl+é"])
def test_a_name_the_backend_does_not_know_is_refused_since_it_would_be_ignored(written: str) -> None:
    with pytest.raises(DesktopActionError) as caught:
        parse_keys(written, KNOWN)
    assert "is not a key name" in str(caught.value)
    assert "enter, tab, esc" in str(caught.value)


def test_without_the_list_of_the_backend_any_name_passes() -> None:
    assert parse_keys("hyperkey") == ["hyperkey"]
    assert parse_keys("ctrl+NoKey") == ["ctrl", "nokey"]


@pytest.mark.parametrize("nothing", [None, "", "   ", 13, ["enter"]])
def test_no_key_at_all_is_refused(nothing: Any) -> None:
    with pytest.raises(DesktopActionError, match="keys is required"):
        parse_keys(nothing, KNOWN)


def test_a_backend_without_a_list_of_keys_accepts_any_name(rig: Rig, monkeypatch: Any) -> None:
    monkeypatch.setattr(rig.backend, "KEYBOARD_KEYS", [], raising=False)
    rig.act("key", {"keys": "hyperkey"})
    assert rig.inputs() == [("press", ("hyperkey",), {})]


# ---------------------------------------------------------------------------
# The size of a screenshot
# ---------------------------------------------------------------------------


def _noise(width: int, height: int) -> Any:
    return Image.frombytes("RGB", (width, height), random.Random(7).randbytes(width * height * 3))


def _detailed(width: int, height: int) -> Any:
    """A picture that compresses badly but not hopelessly, like a screen full of small text."""
    small = _noise(width // 16, height // 16)
    return small.resize((width, height), Image.Resampling.BICUBIC)


def _jpeg(image: Any, quality: int = 95) -> bytes:
    buffer = BytesIO()
    image.save(buffer, format="JPEG", quality=quality)
    return buffer.getvalue()


def test_a_jpeg_within_the_ceiling_is_returned_as_it_is() -> None:
    small = _jpeg(Image.new("RGB", (640, 360), "navy"))
    assert fit_jpeg(small, SCREENSHOT_MAX_BYTES) is small
    assert fit_jpeg(small, len(small)) is small


def test_a_large_jpeg_is_brought_under_the_byte_ceiling_and_is_still_a_picture() -> None:
    large = _jpeg(_detailed(1920, 1080))
    assert len(large) > 2 * SCREENSHOT_MAX_BYTES
    fitted = fit_jpeg(large, SCREENSHOT_MAX_BYTES)
    assert len(fitted) <= SCREENSHOT_MAX_BYTES
    with Image.open(BytesIO(fitted)) as picture:
        picture.load()
        assert picture.format == "JPEG"
        # Quality is given up before size: this one fits without being made smaller.
        assert picture.size == (1920, 1080)


def test_a_jpeg_that_lower_quality_cannot_fit_is_made_smaller() -> None:
    # Noise is the worst a screen can show: ten times the ceiling at full quality.
    large = _jpeg(_noise(1920, 1080))
    assert len(large) > 10 * SCREENSHOT_MAX_BYTES
    fitted = fit_jpeg(large, SCREENSHOT_MAX_BYTES)
    assert len(fitted) <= SCREENSHOT_MAX_BYTES
    with Image.open(BytesIO(fitted)) as picture:
        picture.load()
        assert picture.size[0] < 1920 and picture.size[1] < 1080
        # The proportions are kept.
        assert abs(picture.size[0] / picture.size[1] - 1920 / 1080) < 0.01


def test_bytes_that_are_not_a_picture_are_returned_as_they_are() -> None:
    garbage = b"not a jpeg " * 100
    assert fit_jpeg(garbage, 10) is garbage


def test_the_screenshot_of_a_large_busy_screen_is_bounded_in_height_and_in_bytes(rig: Rig) -> None:
    from utils.screenshot_utils import compress_screenshot

    rig.screen.image = _noise(3840, 2160)
    # Bounding the height alone leaves this one far above the ceiling.
    assert len(compress_screenshot(rig.screen.image, target_h=SCREENSHOT_HEIGHT)) > 2 * SCREENSHOT_MAX_BYTES
    result = rig.act("screenshot")
    assert result.screenshot is not None
    assert len(result.screenshot) <= SCREENSHOT_MAX_BYTES
    with Image.open(BytesIO(result.screenshot)) as picture:
        assert picture.size[1] <= SCREENSHOT_HEIGHT
        # The text names the size of the picture that is handed out, not that of the screen.
        assert f"Screenshot after it: {picture.size[0]}x{picture.size[1]} px." in result.text
    assert "3840" not in result.text


def test_the_screenshot_is_asked_for_with_the_stop_event_of_the_call(rig: Rig) -> None:
    stop = threading.Event()
    rig.act("screenshot", {}, stop)
    assert rig.screen.cancel_events == [stop]
    encoded, size = rig.desktop.screenshot()
    assert size == (64, 36) and encoded[:3] == b"\xff\xd8\xff"
    assert rig.screen.cancel_events == [stop, None]


def test_the_limits_of_a_screenshot() -> None:
    assert SCREENSHOT_HEIGHT == 1080
    assert SCREENSHOT_MAX_BYTES == 200 * 1024


# ---------------------------------------------------------------------------
# One call at a time
# ---------------------------------------------------------------------------


def test_a_call_while_another_holds_the_desktop_is_refused_at_once_and_sends_nothing(rig: Rig) -> None:
    assert EXECUTION_LOCK.acquire(blocking=False)
    try:
        started = time.monotonic()
        with pytest.raises(DesktopActionError, match="busy with an earlier call"):
            rig.act("click", {"x": 1, "y": 1})
        # It does not wait for its turn: the caller is told, and decides.
        assert time.monotonic() - started < 1.0
        assert rig.log() == []
        # The refused call did not take the lock away from the one that holds it.
        assert EXECUTION_LOCK.locked()
    finally:
        EXECUTION_LOCK.release()
    assert rig.act("screenshot").screenshot


def test_the_desktop_stays_taken_until_the_worker_of_the_first_call_is_done(rig: Rig, monkeypatch: Any) -> None:
    entered = threading.Event()
    finish = threading.Event()
    results: list[Any] = []

    def slow_click(*args: Any, **kwargs: Any) -> None:
        entered.set()
        assert finish.wait(10)

    rig.backend.hooks["click"] = slow_click
    other = Desktop(backend=FakeBackend(), capture=FakeScreen([]), settle_seconds=0)
    worker = threading.Thread(target=lambda: results.append(rig.act("click", {"x": 1, "y": 1})))
    worker.start()
    try:
        assert entered.wait(10)
        with pytest.raises(DesktopActionError, match="busy"):
            other.act("screenshot", {})
        with pytest.raises(DesktopActionError, match="busy"):
            rig.desktop.act("screenshot", {})
    finally:
        finish.set()
        worker.join(10)
    assert len(results) == 1 and results[0].screenshot
    assert other.act("screenshot", {}).screenshot


@pytest.mark.parametrize("failure", [RuntimeError("the backend broke"), DesktopActionError("refused")])
def test_the_desktop_is_free_again_after_a_call_that_failed(rig: Rig, failure: Exception) -> None:
    def fail(*args: Any, **kwargs: Any) -> None:
        raise failure

    rig.backend.hooks["click"] = fail
    with pytest.raises(type(failure)):
        rig.act("click", {"x": 1, "y": 1})
    assert not EXECUTION_LOCK.locked()
    rig.backend.hooks.clear()
    assert rig.act("click", {"x": 1, "y": 1}).screenshot


def test_held_input_is_let_go_before_the_desktop_is_given_up(rig: Rig) -> None:
    locked_at_release: list[bool] = []
    attempts: list[int] = []

    def stuck_once(*args: Any, **kwargs: Any) -> None:
        attempts.append(1)
        locked_at_release.append(EXECUTION_LOCK.locked())
        if len(attempts) == 1:
            raise RuntimeError("the key is stuck")

    rig.backend.hooks["keyUp"] = stuck_once
    rig.act("key", {"keys": "w", "hold_seconds": 0.5})
    assert locked_at_release == [True, True]
    assert rig.desktop._held_keys == []


# ---------------------------------------------------------------------------
# Stopping
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("action", "arguments"),
    [
        ("click", {"x": 10, "y": 10}),
        ("move", {"x": 10, "y": 10}),
        ("drag", {"x": 10, "y": 10, "to_x": 20, "to_y": 20}),
        ("scroll", {"direction": "down"}),
        ("type", {"text": "abc"}),
        ("key", {"keys": "enter"}),
        ("key", {"keys": "w", "hold_seconds": 5}),
        ("wait", {"seconds": 30}),
    ],
)
def test_a_call_stopped_before_it_began_sends_nothing(rig: Rig, action: str, arguments: dict[str, Any]) -> None:
    stop = threading.Event()
    stop.set()
    with pytest.raises(DesktopStopped):
        rig.act(action, arguments, stop)
    assert rig.log() == []
    assert not EXECUTION_LOCK.locked()


def test_a_click_stopped_while_the_pointer_moves_is_not_clicked(rig: Rig) -> None:
    stop = threading.Event()
    rig.backend.hooks["moveTo"] = lambda *args, **kwargs: stop.set()
    with pytest.raises(DesktopStopped):
        rig.act("click", {"x": 500, "y": 500}, stop)
    assert rig.names() == ["moveTo"]


def test_a_drag_stopped_with_the_button_down_releases_it(rig: Rig) -> None:
    stop = threading.Event()
    rig.backend.hooks["mouseDown"] = lambda *args, **kwargs: stop.set()
    with pytest.raises(DesktopStopped):
        rig.act("drag", {"x": 100, "y": 200, "to_x": 800, "to_y": 600}, stop)
    # The button comes up where the pointer is, not at the end point: the
    # user stopped the drag, and it must not be carried out by being let go.
    assert rig.inputs() == [
        glide(192, 216),
        ("mouseDown", (192, 216), {"button": "left"}),
        ("mouseUp", (), {"button": "left"}),
    ]
    assert rig.desktop._held_buttons == []
    assert "capture" not in rig.names()


def test_a_hold_stopped_half_way_releases_the_keys_in_reverse_order(rig: Rig) -> None:
    stop = threading.Event()

    def stop_after_the_last_key(key: str, **kwargs: Any) -> None:
        if key == "w":
            stop.set()

    rig.backend.hooks["keyDown"] = stop_after_the_last_key
    started = time.monotonic()
    with pytest.raises(DesktopStopped):
        rig.act("key", {"keys": "ctrl+shift+w", "hold_seconds": 10}, stop)
    assert time.monotonic() - started < 5
    assert rig.inputs() == [
        ("keyDown", ("ctrl",), {}),
        ("keyDown", ("shift",), {}),
        ("keyDown", ("w",), {}),
        ("keyUp", ("w",), {}),
        ("keyUp", ("shift",), {}),
        ("keyUp", ("ctrl",), {}),
    ]
    assert rig.desktop._held_keys == []
    assert "capture" not in rig.names()


def test_a_wait_that_is_stopped_ends_at_once(rig: Rig) -> None:
    stop = threading.Event()
    timer = threading.Timer(0.05, stop.set)
    timer.start()
    started = time.monotonic()
    try:
        with pytest.raises(DesktopStopped):
            rig.act("wait", {"seconds": 30}, stop)
    finally:
        timer.cancel()
    assert time.monotonic() - started < 10
    assert rig.log() == []


def test_a_call_stopped_while_the_screenshot_is_taken_is_stopped(rig: Rig) -> None:
    rig.screen.error = InterruptedError("stopped")
    with pytest.raises(DesktopStopped):
        rig.act("click", {"x": 1, "y": 1}, threading.Event())
    assert not EXECUTION_LOCK.locked()


def test_keys_that_could_not_be_released_when_a_call_was_stopped_are_released_with_the_fail_safe_off(rig: Rig) -> None:
    stop = threading.Event()
    fail_safe_seen: list[tuple[str, bool]] = []

    def stop_after_the_last_key(key: str, **kwargs: Any) -> None:
        if key == "w":
            stop.set()

    def refused_by_the_fail_safe(key: str, **kwargs: Any) -> None:
        fail_safe_seen.append((key, rig.backend.FAILSAFE))
        if rig.backend.FAILSAFE:
            # The pointer is in a corner of the screen: pyautogui refuses everything.
            raise RuntimeError("fail-safe triggered")

    rig.backend.hooks["keyDown"] = stop_after_the_last_key
    rig.backend.hooks["keyUp"] = refused_by_the_fail_safe
    with pytest.raises(DesktopStopped):
        rig.act("key", {"keys": "shift+w", "hold_seconds": 10}, stop)
    assert fail_safe_seen == [("w", True), ("shift", True), ("shift", False), ("w", False)]
    assert rig.inputs()[-2:] == [("keyUp", ("shift",), {"_pause": False}), ("keyUp", ("w",), {"_pause": False})]
    assert rig.desktop._held_keys == []
    # The fail-safe is the user's way to stop pyautogui and is on again afterwards.
    assert rig.backend.FAILSAFE is True


def test_release_keeps_what_failed_for_the_next_try_and_restores_the_fail_safe() -> None:
    calls: list[str] = []
    failures = {"shift"}

    class Backend:
        FAILSAFE = True

        def keyUp(self, key: str, **kwargs: Any) -> None:
            assert self.FAILSAFE is False
            assert kwargs == {"_pause": False}
            calls.append(key)
            if key in failures:
                raise RuntimeError("backend failure")

        def mouseUp(self, **kwargs: Any) -> None:
            assert self.FAILSAFE is False
            assert kwargs["_pause"] is False
            calls.append(kwargs["button"])

    keys = ["shift", "w"]
    buttons = ["left"]
    backend = Backend()
    gui = ScaledInput(backend, held_keys=keys, held_buttons=buttons)
    assert not gui.release_held()
    assert keys == ["shift"] and buttons == []
    assert calls == ["shift", "w", "left"]
    assert backend.FAILSAFE is True
    failures.clear()
    assert gui.release_held()
    assert keys == []
    assert calls == ["shift", "w", "left", "shift"]


def test_a_backend_without_a_fail_safe_flag_gets_none() -> None:
    class Backend:
        def keyUp(self, key: str, **kwargs: Any) -> None:
            return None

    backend = Backend()
    assert ScaledInput(backend, held_keys=["w"], held_buttons=[]).release_held()
    assert not hasattr(backend, "FAILSAFE")


def test_what_an_earlier_call_left_held_is_let_go_before_the_next_action(rig: Rig) -> None:
    rig.desktop._held_keys.extend(["shift", "w"])
    rig.desktop._held_buttons.append("left")
    rig.act("click", {"x": 500, "y": 500})
    assert rig.inputs() == [
        ("keyUp", ("shift",), {"_pause": False}),
        ("keyUp", ("w",), {"_pause": False}),
        ("mouseUp", (), {"button": "left", "_pause": False}),
        glide(960, 540),
        ("click", (960, 540), {"clicks": 1, "interval": 0.0, "button": "left"}),
    ]
    assert rig.desktop._held_keys == [] and rig.desktop._held_buttons == []


def test_no_action_is_carried_out_while_held_input_cannot_be_let_go(rig: Rig) -> None:
    def stuck(*args: Any, **kwargs: Any) -> None:
        raise RuntimeError("the key is stuck")

    rig.desktop._held_keys.append("shift")
    rig.backend.hooks["keyUp"] = stuck
    with pytest.raises(DesktopActionError, match="left held by an earlier call could not be released"):
        rig.act("click", {"x": 500, "y": 500})
    assert {name for name in rig.names()} == {"keyUp"}
    assert rig.desktop._held_keys == ["shift"]
    assert not EXECUTION_LOCK.locked()


def test_releasing_by_hand_waits_for_a_call_that_has_the_keyboard_and_does_nothing_while_it_is_at_it(
    rig: Rig, monkeypatch: Any
) -> None:
    # A call that was stopped and whose worker is still at it (stuck taking
    # its screenshot) holds a key down. Letting go from another thread now
    # would release a key that call is holding, and both would switch the
    # fail-safe off and on again, which can leave it off for good.
    monkeypatch.setattr(desktop, "RELEASE_WAIT_SECONDS", 0.01)
    rig.desktop._held_keys.append("shift")
    assert EXECUTION_LOCK.acquire(blocking=False)
    try:
        assert rig.desktop.release_held() is False
        assert rig.log() == [] and rig.desktop._held_keys == ["shift"]
        assert rig.backend.FAILSAFE is True
    finally:
        EXECUTION_LOCK.release()
    # Once the call has let go of the keyboard, the key is released.
    assert rig.desktop.release_held() is True
    assert rig.inputs() == [("keyUp", ("shift",), {"_pause": False})]
    assert rig.backend.FAILSAFE is True and not EXECUTION_LOCK.locked()


def test_a_call_lets_go_of_what_it_held_without_waiting_for_itself(rig: Rig, monkeypatch: Any) -> None:
    # The call has the keyboard while it lets go at its end: it must not wait for itself to give it up.
    monkeypatch.setattr(desktop, "RELEASE_WAIT_SECONDS", 3.0)
    tries: list[int] = []

    def stuck_the_first_time(*_args: Any, **_kwargs: Any) -> None:
        tries.append(1)
        if len(tries) == 1:
            raise RuntimeError("the key is stuck")

    rig.desktop._held_keys.append("shift")
    rig.backend.hooks["keyUp"] = stuck_the_first_time
    started = time.monotonic()
    with pytest.raises(DesktopActionError, match="left held by an earlier call could not be released"):
        rig.act("click", {"x": 500, "y": 500})
    assert time.monotonic() - started < 1.0
    # Tried once more as the call ended, and let go of then.
    assert len(tries) == 2 and rig.desktop._held_keys == []
    assert not EXECUTION_LOCK.locked()


def test_releasing_by_hand_lets_go_of_keys_and_buttons(rig: Rig) -> None:
    # Nothing held: nothing is sent.
    assert rig.desktop.release_held() is True
    assert rig.log() == []
    rig.desktop._held_keys.append("ctrl")
    rig.desktop._held_buttons.append("right")
    assert rig.desktop.release_held() is True
    assert rig.inputs() == [("keyUp", ("ctrl",), {"_pause": False}), ("mouseUp", (), {"button": "right", "_pause": False})]
    assert rig.desktop._held_keys == [] and rig.desktop._held_buttons == []


# ---------------------------------------------------------------------------
# No backend
# ---------------------------------------------------------------------------


def test_a_desktop_with_a_backend_is_available(rig: Rig) -> None:
    assert rig.desktop.available() == (True, "")
    assert rig.desktop.backend() is rig.backend


@pytest.mark.parametrize(
    ("system", "error", "code"),
    [
        ("Windows", ModuleNotFoundError("No module named 'pyautogui'", name="pyautogui"), "AGENT_PYAUTOGUI_NOT_INSTALLED"),
        ("Linux", RuntimeError('Can\'t connect to display ":0": Authorization required'), "AGENT_PYAUTOGUI_DISPLAY_UNAVAILABLE"),
        ("Darwin", ModuleNotFoundError("No module named 'Quartz'", name="Quartz"), "AGENT_PYAUTOGUI_MACOS_PYOBJC_MISSING"),
        ("Windows", OSError("something else"), "AGENT_PYAUTOGUI_IMPORT_FAILED"),
    ],
)
def test_without_a_backend_the_reason_code_is_reported_and_actions_are_refused(
    monkeypatch: Any, system: str, error: Exception, code: str
) -> None:
    # pyautogui cannot be loaded here: the loader gives nothing and has noted why.
    monkeypatch.setattr(desktop, "load_pyautogui", lambda: None)
    monkeypatch.setattr(desktop, "_import_error", error)
    monkeypatch.setattr(desktop, "platform", SimpleNamespace(system=lambda: system))
    screen = FakeScreen([])
    without = Desktop(capture=screen, settle_seconds=0)
    assert without.available() == (False, code)
    with pytest.raises(DesktopActionError, match=f"cannot be used here \\({code}\\)"):
        without.act("screenshot", {})
    assert screen.log == []
    assert not EXECUTION_LOCK.locked()
    # Nothing held: fine. Something held: it cannot be let go without a backend.
    assert without.release_held() is True
    without._held_keys.append("shift")
    assert without.release_held() is False


# ---------------------------------------------------------------------------
# Loading pyautogui (the real loader over a fake import)
# ---------------------------------------------------------------------------


class _PyautoguiImport(importlib.abc.MetaPathFinder, importlib.abc.Loader):
    """Serves ``import pyautogui`` with an empty module and notes when it happened."""

    def __init__(self, events: list[str]) -> None:
        self.events = events
        self.error: Exception | None = None

    def find_spec(self, fullname: str, path: Any = None, target: Any = None) -> Any:
        if fullname != "pyautogui":
            return None
        return importlib.machinery.ModuleSpec(fullname, self)

    def create_module(self, spec: Any) -> Any:
        return None

    def exec_module(self, module: Any) -> None:
        self.events.append("import pyautogui")
        if self.error is not None:
            raise self.error
        module.FAKE = True


@pytest.fixture
def loader(monkeypatch: Any, no_real_desktop: Any) -> Any:
    """Put the real ``load_pyautogui`` back, over a fake import and a fake ``ctypes``."""
    events: list[str] = []
    state = SimpleNamespace(events=events, finder=_PyautoguiImport(events), system="Windows", shcore_fails=False, user32_fails=False)

    def set_awareness(level: int) -> None:
        events.append(f"shcore awareness {level}")
        if state.shcore_fails:
            raise OSError("not on this Windows")

    def set_aware() -> None:
        events.append("user32 aware")
        if state.user32_fails:
            raise OSError("not here either")

    fake_ctypes = types.ModuleType("ctypes")
    fake_ctypes.windll = SimpleNamespace(  # type: ignore[attr-defined]
        shcore=SimpleNamespace(SetProcessDpiAwareness=set_awareness),
        user32=SimpleNamespace(SetProcessDPIAware=set_aware),
    )
    monkeypatch.setitem(sys.modules, "ctypes", fake_ctypes)
    # The fake import takes the place of the block the autouse fixture put there.
    monkeypatch.delitem(sys.modules, "pyautogui")
    monkeypatch.setattr(sys, "meta_path", [state.finder, *sys.meta_path])
    monkeypatch.setattr(desktop, "_dpi_awareness_set", False)
    monkeypatch.setattr(desktop, "platform", SimpleNamespace(system=lambda: state.system))
    monkeypatch.setattr(desktop, "load_pyautogui", _REAL_LOAD_PYAUTOGUI)
    return state


def test_on_windows_the_dpi_awareness_is_set_before_pyautogui_is_imported(loader: Any) -> None:
    module = desktop.load_pyautogui()
    assert getattr(module, "FAKE", False) is True
    assert loader.events == ["shcore awareness 2", "import pyautogui"]


def test_an_older_windows_gets_the_older_awareness_call(loader: Any) -> None:
    loader.shcore_fails = True
    assert desktop.load_pyautogui() is not None
    assert loader.events == ["shcore awareness 2", "user32 aware", "import pyautogui"]


def test_pyautogui_is_still_loaded_when_the_awareness_cannot_be_set(loader: Any) -> None:
    loader.shcore_fails = loader.user32_fails = True
    assert desktop.load_pyautogui() is not None
    assert loader.events[-1] == "import pyautogui"


@pytest.mark.parametrize("system", ["Linux", "Darwin"])
def test_elsewhere_no_awareness_is_set(loader: Any, system: str) -> None:
    loader.system = system
    assert desktop.load_pyautogui() is not None
    assert loader.events == ["import pyautogui"]


def test_pyautogui_is_imported_once_and_kept(loader: Any) -> None:
    first = desktop.load_pyautogui()
    assert desktop.load_pyautogui() is first
    assert desktop.load_pyautogui() is first
    assert loader.events == ["shcore awareness 2", "import pyautogui"]


def test_an_import_that_failed_is_tried_again_on_the_next_call(loader: Any) -> None:
    loader.system = "Linux"
    loader.finder.error = RuntimeError('Can\'t connect to display ":0": Authorization required')
    assert desktop.load_pyautogui() is None
    assert desktop.pyautogui_unavailable_reason() == "AGENT_PYAUTOGUI_DISPLAY_UNAVAILABLE"
    assert desktop.load_pyautogui() is None
    assert loader.events == ["import pyautogui", "import pyautogui"]
    # The display is authorized meanwhile.
    loader.finder.error = None
    module = desktop.load_pyautogui()
    assert getattr(module, "FAKE", False) is True
    assert desktop._import_error is None
    assert loader.events == ["import pyautogui", "import pyautogui", "import pyautogui"]


def test_the_awareness_is_set_once_however_often_the_import_is_tried(loader: Any) -> None:
    loader.finder.error = OSError("broken install")
    assert desktop.load_pyautogui() is None
    assert desktop.load_pyautogui() is None
    assert loader.events == ["shcore awareness 2", "import pyautogui", "import pyautogui"]
    assert desktop.pyautogui_unavailable_reason() == "AGENT_PYAUTOGUI_IMPORT_FAILED"


def test_a_desktop_without_a_backend_of_its_own_uses_the_loaded_pyautogui(loader: Any) -> None:
    # The module that gets loaded here is the fake one of this fixture.
    built = Desktop(capture=_reached, settle_seconds=0)
    assert getattr(built.backend(), "FAKE", False) is True
    assert built.available() == (True, "")


# ---------------------------------------------------------------------------
# Taking the screenshot (the real ``capture_frame`` over a fake HTTP client and a fake native grab)
# ---------------------------------------------------------------------------


class _Response:
    def __init__(self, status_code: int, payload: Any) -> None:
        self.status_code = status_code
        self._payload = payload

    def json(self) -> Any:
        if isinstance(self._payload, Exception):
            raise self._payload
        return self._payload


class _Client:
    def __init__(self, response: _Response, posts: list[str] | None = None) -> None:
        self.response = response
        self.posts = posts if posts is not None else []

    def __enter__(self) -> "_Client":
        return self

    def __exit__(self, *_args: Any) -> None:
        return None

    def post(self, url: str) -> _Response:
        self.posts.append(url)
        return self.response

    def close(self) -> None:
        return None


def _data_url(image: Any, kind: str = "PNG") -> str:
    buffer = BytesIO()
    image.save(buffer, format=kind)
    return f"data:image/{kind.lower()};base64," + base64.b64encode(buffer.getvalue()).decode()


@pytest.fixture
def bridge(monkeypatch: Any, no_real_desktop: Any) -> Any:
    """Put the real ``capture_frame`` back, over a fake HTTP client and a fake native grab."""
    import utils.desktop_capture as native

    state = SimpleNamespace(system="Linux", client=None, client_options=[], native=None, native_calls=0)

    def client(**options: Any) -> Any:
        state.client_options.append(options)
        if state.client is None:
            raise AssertionError("this test did not expect the shell to be asked for a screenshot")
        return state.client

    def native_grab() -> Any:
        state.native_calls += 1
        if state.native is None:
            raise AssertionError("this test did not expect the native screenshot")
        if isinstance(state.native, Exception):
            raise state.native
        return state.native

    monkeypatch.setattr(httpx, "Client", client)
    monkeypatch.setattr(native, "capture_desktop_screenshot", native_grab)
    monkeypatch.setattr(desktop, "platform", SimpleNamespace(system=lambda: state.system))
    monkeypatch.setattr(desktop, "_bridge_backoff_until", 0.0)
    monkeypatch.setattr(desktop, "capture_frame", _REAL_CAPTURE_FRAME)
    return state


def test_the_frame_of_the_shell_is_decoded_and_the_native_screenshot_is_not_taken(bridge: Any) -> None:
    from config import MAIN_SERVER_PORT

    bridge.client = _Client(_Response(200, {"image": _data_url(Image.new("RGB", (32, 24), "red"))}))
    image = desktop.capture_frame()
    assert image.size == (32, 24)
    assert image.getpixel((0, 0)) == (255, 0, 0)
    assert bridge.native_calls == 0
    assert bridge.client.posts == [f"http://127.0.0.1:{MAIN_SERVER_PORT}/api/capture/computer-use"]
    # The shell is on this computer: no proxy from the environment is used to reach it.
    (options,) = bridge.client_options
    assert options["proxy"] is None and options["trust_env"] is False


def test_a_jpeg_frame_of_the_shell_is_accepted_too(bridge: Any) -> None:
    bridge.client = _Client(_Response(200, {"image": _data_url(Image.new("RGB", (40, 30), "white"), "JPEG")}))
    assert desktop.capture_frame().size == (40, 30)


def test_the_native_screenshot_is_taken_when_the_shell_has_none(bridge: Any) -> None:
    bridge.native = Image.new("RGB", (16, 12), "blue")
    bridge.client = _Client(_Response(503, {"error": "no_renderer"}))
    assert desktop.capture_frame() is bridge.native
    assert bridge.client.posts and bridge.native_calls == 1
    # No stall: the shell is asked again next time.
    assert desktop.capture_frame() is bridge.native
    assert len(bridge.client.posts) == 2


@pytest.mark.parametrize(
    "payload",
    [
        {"image": "https://example.com/screen.png"},
        {"image": "data:image/gif;base64,R0lGODlhAQABAAAAACw="},
        {"image": "data:image/png;base64,not base64 at all!"},
        {"image": "data:image/png;base64," + base64.b64encode(b"not a picture").decode()},
        {"image": 7},
        {},
        ["not", "an", "object"],
        ValueError("the body is not JSON"),
    ],
)
def test_an_answer_of_the_shell_that_is_not_a_picture_falls_back_to_the_native_screenshot(bridge: Any, payload: Any) -> None:
    bridge.native = Image.new("RGB", (16, 12), "blue")
    bridge.client = _Client(_Response(200, payload))
    assert desktop.capture_frame() is bridge.native


def test_a_timeout_of_the_shell_makes_the_next_calls_skip_it_for_a_while(bridge: Any) -> None:
    bridge.native = Image.new("RGB", (16, 12), "blue")
    calls: list[str] = []

    class _TimeoutClient(_Client):
        def post(self, url: str) -> _Response:
            calls.append("post")
            raise httpx.ReadTimeout("renderer did not answer")

    bridge.client = _TimeoutClient(_Response(200, {}))
    assert desktop.capture_frame() is bridge.native
    assert desktop._bridge_backoff_until > time.monotonic()
    assert desktop._bridge_backoff_until <= time.monotonic() + desktop._BRIDGE_BACKOFF_SECONDS
    assert desktop.capture_frame() is bridge.native
    assert desktop.capture_frame() is bridge.native
    assert calls == ["post"]


def test_a_timeout_answer_of_the_shell_also_makes_the_next_calls_skip_it(bridge: Any) -> None:
    # The bridge of the main server gives up (25 s) before this client does
    # (28 s), so a stalled renderer normally shows up as a 504 answer.
    bridge.native = Image.new("RGB", (16, 12), "blue")
    bridge.client = _Client(_Response(504, {"success": False, "error": "renderer response timeout"}))
    assert desktop.capture_frame() is bridge.native
    assert desktop.capture_frame() is bridge.native
    assert len(bridge.client.posts) == 1


def test_the_shell_is_asked_again_once_the_pause_is_over(bridge: Any, monkeypatch: Any) -> None:
    bridge.native = Image.new("RGB", (16, 12), "blue")
    bridge.client = _Client(_Response(504, {"error": "renderer response timeout"}))
    assert desktop.capture_frame() is bridge.native
    # The pause has run out.
    monkeypatch.setattr(desktop, "_bridge_backoff_until", time.monotonic() - 1.0)
    bridge.client = _Client(_Response(200, {"image": _data_url(Image.new("RGB", (32, 24), "red"))}))
    assert desktop.capture_frame().size == (32, 24)
    # A good answer clears the pause.
    assert desktop._bridge_backoff_until == 0.0


def test_an_error_that_is_not_a_stall_does_not_pause_the_shell(bridge: Any) -> None:
    bridge.native = Image.new("RGB", (16, 12), "blue")

    class _RefusedClient(_Client):
        def post(self, url: str) -> _Response:
            self.posts.append(url)
            raise httpx.ConnectError("connection refused")

    bridge.client = _RefusedClient(_Response(200, {}))
    assert desktop.capture_frame() is bridge.native
    assert desktop.capture_frame() is bridge.native
    assert len(bridge.client.posts) == 2
    assert desktop._bridge_backoff_until == 0.0


def test_stopping_while_the_shell_stalls_does_not_wait_for_the_request_to_time_out(bridge: Any) -> None:
    entered = threading.Event()
    release = threading.Event()
    closed = threading.Event()
    cancel = threading.Event()

    class _StalledClient(_Client):
        def post(self, url: str) -> _Response:
            entered.set()
            release.wait(timeout=5)
            return _Response(503, {"error": "no_renderer"})

        def close(self) -> None:
            closed.set()
            release.set()

    bridge.client = _StalledClient(_Response(200, {}))
    # ``bridge.native`` stays unset: a stopped capture must not go on to the native screenshot.

    def cancel_after_the_request_started() -> None:
        assert entered.wait(timeout=2)
        cancel.set()

    canceller = threading.Thread(target=cancel_after_the_request_started)
    canceller.start()
    started = time.monotonic()
    try:
        with pytest.raises(InterruptedError, match="stopped"):
            desktop.capture_frame(cancel)
        assert time.monotonic() - started < 2
        # The client is closed, which is what ends the request that hangs.
        assert closed.is_set()
        assert bridge.native_calls == 0
    finally:
        release.set()
        canceller.join(timeout=2)


def test_a_capture_stopped_before_it_began_asks_nobody(bridge: Any) -> None:
    cancel = threading.Event()
    cancel.set()
    with pytest.raises(InterruptedError, match="stopped"):
        desktop.capture_frame(cancel)
    assert bridge.client_options == []
    assert bridge.native_calls == 0


def test_with_a_stop_event_that_is_not_set_the_frame_of_the_shell_is_returned(bridge: Any) -> None:
    bridge.client = _Client(_Response(200, {"image": _data_url(Image.new("RGB", (32, 24), "red"))}))
    assert desktop.capture_frame(threading.Event()).size == (32, 24)


@pytest.mark.parametrize("system", ["Windows", "Darwin"])
def test_only_linux_asks_the_shell(bridge: Any, system: str) -> None:
    bridge.system = system
    bridge.native = Image.new("RGB", (16, 12), "blue")
    assert desktop.capture_frame() is bridge.native
    assert bridge.client_options == []


def test_when_both_ways_fail_the_error_names_both(bridge: Any) -> None:
    from utils.desktop_capture import DesktopCaptureError

    bridge.client = _Client(_Response(503, {"error": "no_renderer"}))
    bridge.native = RuntimeError("no display to grab")
    with pytest.raises(DesktopCaptureError) as caught:
        desktop.capture_frame()
    assert "both failed" in str(caught.value)
    assert "no_renderer" in str(caught.value) and "no display to grab" in str(caught.value)


def test_a_native_failure_alone_is_raised_as_it_is(bridge: Any) -> None:
    bridge.system = "Windows"
    bridge.native = RuntimeError("no display to grab")
    with pytest.raises(RuntimeError, match="no display to grab"):
        desktop.capture_frame()


def test_a_desktop_takes_its_screenshots_with_capture_frame_unless_given_another_way(bridge: Any) -> None:
    bridge.system = "Windows"
    bridge.native = Image.new("RGB", (48, 27), "green")
    built = Desktop(backend=FakeBackend(), settle_seconds=0)
    result = built.act("screenshot", {})
    assert "Screenshot after it: 48x27 px." in result.text
    assert bridge.native_calls == 1


# ---------------------------------------------------------------------------
# The page that answers the capture bridge, and what goes with it in the pages
# ---------------------------------------------------------------------------


def _source(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8")


def test_wayland_agent_never_reopens_portal_for_each_frame() -> None:
    source = _source("static/app/app-websocket.js")
    request = source.split("response.type === 'capture_bridge_computer_use_request'", 1)[1].split(
        "response.type === 'capture_bridge_region_request'", 1
    )[0]
    portal_guard = request.index("if (dc.sourceEnumerationMayPrompt === true")
    one_shot = request.index("dc, 'captureComputerUseScreen'")
    assert portal_guard < one_shot
    assert "error: 'SCREEN_STREAM_REQUIRED'" in request


def test_agent_toggle_waits_for_capture_permission_before_enabling() -> None:
    modern = _source("static/js/agent_ui_v2.js")
    assert modern.index("await capturePreparation") < modern.index("await sendCommand('set_flag'")
    # agent_ui_v2 is the only toggle controller: app-agent.js hands over to it
    # and writes no flags itself, so no toggle can skip the wait above.
    app_agent = _source("static/app/app-agent.js")
    assert "window.initAgentUiV2();" in app_agent
    assert "/api/agent/flags" not in app_agent
    assert "/api/agent/command" not in app_agent


def test_reopening_capture_does_not_reuse_invalid_pending_permission() -> None:
    source = _source("static/app/app-websocket.js")
    release = source.split("function releaseComputerUseCapture()", 1)[1].split(
        "async function captureComputerUseLiveStream(provider)", 1
    )[0]
    prepare = source.split("window.prepareComputerUseCapture = function ()", 1)[1].split(
        "window.releaseComputerUseCapture", 1
    )[0]
    assert "_computerUseStreamPending = null" in release
    assert "if (_computerUseStreamPending === pending) _computerUseStreamPending = null" in prepare


def test_computer_use_stream_owner_is_brokered_across_chat_and_pet() -> None:
    source = _source("static/app/app-websocket.js")
    assert "onComputerUseFrameRequest(async function ()" in source
    assert "setComputerUseStreamOwner(true, ownerToken)" in source
    assert "setComputerUseStreamOwner(false, _computerUseStreamOwnerToken)" in source
    assert "dc.computerUseSharedStreamBroker === true" in source
    assert "dc.computerUseNeedsStream === true" in source
    assert "COMPUTER_USE_STREAM_IDLE_MS" not in source


def test_screen_share_chooser_timeout_can_use_native_fallback_without_hanging_toggle() -> None:
    source = _source("static/app/app-websocket.js")
    modern = _source("static/js/agent_ui_v2.js")
    assert "requestTimedOut = true" in source
    assert "stream.getTracks().forEach(function (track) { track.stop(); });" in source
    assert "window.computerUseNativeCaptureAvailable = async function ()" in source
    assert "await window.computerUseNativeCaptureAvailable()" in modern


def test_active_task_cards_reconcile_with_backend_terminal_state() -> None:
    source = _source("static/app/app-websocket.js")
    reconcile = source.split("function scheduleAgentTaskReconciliation()", 1)[1].split(
        "window.computerUseNeedsCaptureStream", 1
    )[0]
    assert "fetch('/api/agent/tasks', { cache: 'no-store' })" in reconcile
    # blocked has ended, like failed; a task waiting for the user's reply is
    # still under way and keeps the reconciliation running.
    assert "['completed', 'failed', 'cancelled', 'blocked']" in reconcile
    assert "['completed', 'failed', 'cancelled']" not in reconcile
    still_under_way = "return task.status === 'running' || task.status === 'queued' || task.status === 'waiting_user';"
    assert reconcile.count(still_under_way) == 2
    assert "task.status !== 'running' && task.status !== 'queued' && task.status !== 'waiting_user'" in reconcile
    assert "terminal_at: terminalAt" in reconcile
    assert "taskMap = window._agentTaskMap" in reconcile


def test_screen_share_required_message_exists_in_every_locale() -> None:
    for locale in ("en", "ja", "ko", "zh-CN", "zh-TW", "ru", "pt", "es"):
        data = json.loads(_source(f"static/locales/{locale}.json"))
        assert data["agent"]["status"]["screenShareRequired"]
