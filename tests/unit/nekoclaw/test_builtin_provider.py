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

"""The built-in tools as the host sees them: listed, and carried out one call at a time.

The provider is given a fake desktop and fake browser tools in every test.
On top of that an autouse fixture makes the real mouse, keyboard, clipboard
and screen unreachable, as in ``test_builtin_desktop.py``.
"""

from __future__ import annotations

import asyncio
import base64
import sys
import threading
import types
from collections.abc import Callable
from typing import Any

import pytest

from config.prompts.prompts_nekoclaw_builtin import (
    BROWSER_SCREENSHOT_TOOL_NAME,
    BROWSER_TOOL_NAMES,
    BROWSER_TOOLS,
    COMPUTER_TOOL,
)
from nekoclaw.builtin import desktop as desktop_module
from nekoclaw.builtin import provider as provider_module
from nekoclaw.builtin.browser import tools as tools_module
from nekoclaw.builtin.desktop import DesktopActionError, DesktopResult, DesktopStopped
from nekoclaw.builtin.provider import BuiltinToolProvider
from nekoclaw.host.capabilities import (
    BUILTIN_GROUP_BROWSER,
    BUILTIN_GROUP_KEYBOARD_MOUSE,
    KIND_BUILTIN,
    BuiltinImage,
    BuiltinOutcome,
    Capability,
)

REACHED = "a unit test reached the real pyautogui"
JPEG = b"\xff\xd8\xff\xe0 a screenshot"


def _reached(*args: Any, **kwargs: Any) -> Any:
    raise AssertionError(REACHED)


@pytest.fixture(autouse=True)
def no_real_desktop(monkeypatch: Any) -> None:
    """Make the real mouse, keyboard, clipboard and screen unreachable from every test of this file."""
    import utils.desktop_capture as native

    clipboard = types.ModuleType("pyperclip")
    clipboard.copy = _reached  # type: ignore[attr-defined]
    clipboard.paste = _reached  # type: ignore[attr-defined]
    monkeypatch.setattr(desktop_module, "load_pyautogui", _reached)
    monkeypatch.setattr(desktop_module, "capture_frame", _reached)
    monkeypatch.setattr(desktop_module, "_pyautogui", None)
    monkeypatch.setattr(native, "capture_desktop_screenshot", _reached)
    monkeypatch.setitem(sys.modules, "pyautogui", None)
    monkeypatch.setitem(sys.modules, "pyperclip", clipboard)
    if sys.platform == "win32":
        # The last line of defence: the calls of Windows itself that send input, whoever makes them.
        import ctypes

        for name in ("SendInput", "mouse_event", "keybd_event", "SetCursorPos"):
            monkeypatch.setattr(ctypes.windll.user32, name, _reached, raising=False)


class Switches:
    """What the embedding process knows: whether each group is switched on and ready."""

    def __init__(self) -> None:
        self.states: dict[str, tuple[bool, str]] = {
            BUILTIN_GROUP_KEYBOARD_MOUSE: (True, ""),
            BUILTIN_GROUP_BROWSER: (True, ""),
        }
        self.asked: list[str] = []

    def __call__(self, group: str) -> tuple[bool, str]:
        self.asked.append(group)
        return self.states.get(group, (False, "unknown"))


class FakeDesktop:
    """Stands in for ``Desktop``: records its calls and the threads they ran in."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any], Any]] = []
        self.threads: list[threading.Thread] = []
        self.release_threads: list[threading.Thread] = []
        self.release_error: Exception | None = None
        # Called in the worker thread with (action, arguments, stop event); returns the result or raises.
        self.behaviour: Callable[[str, dict[str, Any], Any], DesktopResult] | None = None
        self._count = threading.Lock()
        self.active = 0
        self.most_at_once = 0

    def act(self, action: str, arguments: dict[str, Any], cancel_event: Any = None) -> DesktopResult:
        self.calls.append((action, arguments, cancel_event))
        self.threads.append(threading.current_thread())
        with self._count:
            self.active += 1
            self.most_at_once = max(self.most_at_once, self.active)
        try:
            if self.behaviour is not None:
                return self.behaviour(action, arguments, cancel_event)
            return DesktopResult(text=f"Did {action}. Screenshot after it: 64x36 px.", screenshot=JPEG)
        finally:
            with self._count:
                self.active -= 1

    def release_held(self) -> bool:
        self.release_threads.append(threading.current_thread())
        if self.release_error is not None:
            raise self.release_error
        return True


class FakeBrowserTools:
    """Stands in for ``BrowserTools``."""

    def __init__(self, session: Any = None) -> None:
        self.session = session
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.outcome = BuiltinOutcome(True, "Successfully clicked on the element")
        self.error: Exception | None = None
        self.gate: asyncio.Event | None = None
        self.entered = asyncio.Event()
        self.released_calls = 0
        self.closed = 0
        self.window_info: dict[str, Any] | None = None
        # Called while the call waits its turn, before it is carried out.
        self.while_waiting: Any = None

    async def call(self, tool_name: str, arguments: dict[str, Any], *, refused: Any = None) -> BuiltinOutcome:
        if self.while_waiting is not None:
            self.while_waiting()
        # As the real ones: asked when the call's turn has come.
        why = refused() if refused is not None else None
        if why:
            return BuiltinOutcome(False, why)
        self.calls.append((tool_name, arguments))
        self.entered.set()
        if self.gate is not None:
            await self.gate.wait()
        if self.error is not None:
            raise self.error
        return self.outcome

    async def released(self) -> None:
        self.released_calls += 1

    def window(self) -> dict[str, Any] | None:
        return dict(self.window_info) if self.window_info else None

    async def aclose(self) -> None:
        self.closed += 1


class Rig:
    def __init__(self, **options: Any) -> None:
        self.state = Switches()
        self.desktop = FakeDesktop()
        self.browser = FakeBrowserTools()
        self.provider = BuiltinToolProvider(state=self.state, desktop=self.desktop, browser=self.browser, **options)

    def tool(self, name: str) -> Capability:
        (found,) = [item for item in self.provider.available() if item.tool_name == name]
        return found

    async def computer(self, commission_id: str = "commission-1", **arguments: Any) -> BuiltinOutcome:
        return await self.provider.run(self.tool("computer"), commission_id=commission_id, arguments=arguments)

    async def browse(self, name: str = "browser_click", commission_id: str = "commission-1", **arguments: Any) -> BuiltinOutcome:
        return await self.provider.run(self.tool(name), commission_id=commission_id, arguments=arguments)


@pytest.fixture
def rig() -> Rig:
    return Rig()


async def until(predicate: Callable[[], Any], what: str) -> None:
    deadline = asyncio.get_running_loop().time() + 10.0
    while not predicate():
        if asyncio.get_running_loop().time() > deadline:
            raise AssertionError(f"{what} did not happen")
        await asyncio.sleep(0.005)


# ---------------------------------------------------------------------------
# The list of tools
# ---------------------------------------------------------------------------


def test_one_computer_tool_and_fifteen_browser_tools_are_listed(rig: Rig) -> None:
    listed = rig.provider.available()
    assert [item.tool_name for item in listed] == ["computer", *BROWSER_TOOL_NAMES]
    assert len(listed) == 16
    assert all(item.kind == KIND_BUILTIN and item.available and item.unavailable_reason == "" for item in listed)
    computer = listed[0]
    assert computer.group == computer.permission == BUILTIN_GROUP_KEYBOARD_MOUSE
    assert computer.id == "builtin:keyboard_mouse:computer"
    assert computer.description == COMPUTER_TOOL["description"]
    assert computer.input_schema == COMPUTER_TOOL["parameters"]
    assert computer.one_step is False
    browser = listed[1:]
    assert {item.group for item in browser} == {BUILTIN_GROUP_BROWSER}
    assert {item.permission for item in browser} == {BUILTIN_GROUP_BROWSER}
    assert [item.id for item in browser] == [f"builtin:browser:{name}" for name in BROWSER_TOOL_NAMES]
    assert [item.input_schema for item in browser] == [definition["parameters"] for definition in BROWSER_TOOLS]
    assert [item.description for item in browser] == [definition["description"] for definition in BROWSER_TOOLS]


def test_the_computer_tool_and_the_browser_screenshot_are_the_tools_that_need_a_model_that_sees_pictures(rig: Rig) -> None:
    needing = [item.tool_name for item in rig.provider.available() if item.needs_images]
    assert needing == ["computer", BROWSER_SCREENSHOT_TOOL_NAME]
    assert BROWSER_SCREENSHOT_TOOL_NAME == "browser_take_screenshot"
    # The other fourteen work from text and are of use to any model.
    assert len([item for item in rig.provider.available() if not item.needs_images]) == 14


def test_every_tool_carries_the_state_of_its_group_as_of_now(rig: Rig) -> None:
    rig.state.states[BUILTIN_GROUP_KEYBOARD_MOUSE] = (False, "switched off")
    listed = rig.provider.available()
    assert (listed[0].available, listed[0].unavailable_reason) == (False, "switched off")
    assert all(item.available and item.unavailable_reason == "" for item in listed[1:])
    rig.state.states[BUILTIN_GROUP_KEYBOARD_MOUSE] = (True, "")
    rig.state.states[BUILTIN_GROUP_BROWSER] = (False, "not ready (AGENT_BROWSER_NOT_FOUND)")
    listed = rig.provider.available()
    assert (listed[0].available, listed[0].unavailable_reason) == (True, "")
    assert all(not item.available and item.unavailable_reason == "not ready (AGENT_BROWSER_NOT_FOUND)" for item in listed[1:])
    assert len(listed) == 16


def test_listing_asks_each_group_once_and_touches_neither_the_desktop_nor_the_browser(rig: Rig) -> None:
    rig.provider.available()
    assert rig.state.asked == [BUILTIN_GROUP_KEYBOARD_MOUSE, BUILTIN_GROUP_BROWSER]
    assert rig.desktop.calls == [] and rig.browser.calls == []
    # Nothing is made for listing either.
    bare = BuiltinToolProvider(state=Switches())
    assert len(bare.available()) == 16
    assert bare._desktop is None and bare._browser is None
    assert bare.browser_window() is None


def test_the_parts_are_made_on_first_use_and_kept(monkeypatch: Any) -> None:
    monkeypatch.setattr(desktop_module, "Desktop", FakeDesktop)
    monkeypatch.setattr(tools_module, "BrowserTools", FakeBrowserTools)
    bare = BuiltinToolProvider(state=Switches())
    made = bare.desktop()
    assert isinstance(made, FakeDesktop) and bare.desktop() is made
    tools = bare.browser()
    assert isinstance(tools, FakeBrowserTools) and bare.browser() is tools


def test_parts_that_were_given_are_the_ones_used(rig: Rig) -> None:
    assert rig.provider.desktop() is rig.desktop
    assert rig.provider.browser() is rig.browser


# ---------------------------------------------------------------------------
# A group that is off
# ---------------------------------------------------------------------------


async def test_a_call_to_a_group_that_is_off_is_refused_and_nothing_is_done(rig: Rig) -> None:
    computer, click = rig.tool("computer"), rig.tool("browser_click")
    rig.state.states[BUILTIN_GROUP_KEYBOARD_MOUSE] = (False, "switched off")
    rig.state.states[BUILTIN_GROUP_BROWSER] = (False, "not ready (AGENT_BROWSER_NOT_FOUND)")
    outcome = await rig.provider.run(computer, commission_id="c1", arguments={"action": "click", "x": 1, "y": 1})
    assert outcome == BuiltinOutcome(False, "computer is not available: switched off")
    outcome = await rig.provider.run(click, commission_id="c1", arguments={"uid": "1_2"})
    assert outcome == BuiltinOutcome(False, "browser_click is not available: not ready (AGENT_BROWSER_NOT_FOUND)")
    assert rig.desktop.calls == [] and rig.browser.calls == []
    assert not rig.provider.is_running()


async def test_a_group_that_is_off_without_a_reason_is_called_switched_off(rig: Rig) -> None:
    click = rig.tool("browser_click")
    rig.state.states[BUILTIN_GROUP_BROWSER] = (False, "")
    assert await rig.provider.run(click, commission_id="c1", arguments={}) == BuiltinOutcome(
        False, "browser_click is not available: switched off"
    )


async def test_a_tool_of_no_group_the_provider_has_is_refused(rig: Rig) -> None:
    def stranger(group: str | None) -> Capability:
        return Capability(
            id="builtin:other:teleport",
            kind=KIND_BUILTIN,
            title="teleport",
            description="",
            input_schema={"type": "object", "properties": {}},
            permission="other",
            permission_key=None,
            one_step=False,
            available=True,
            tool_name="teleport",
            group=group,
        )

    assert await rig.provider.run(stranger(None), commission_id="c1", arguments={}) == BuiltinOutcome(
        False, "teleport is not available: unknown"
    )
    # Even a state that calls the group ready does not make the tool exist.
    rig.state.states["other"] = (True, "")
    assert await rig.provider.run(stranger("other"), commission_id="c1", arguments={}) == BuiltinOutcome(
        False, "unknown tool teleport"
    )
    assert rig.desktop.calls == [] and rig.browser.calls == []


# ---------------------------------------------------------------------------
# Keyboard and mouse calls
# ---------------------------------------------------------------------------


async def test_a_desktop_call_runs_in_a_worker_thread_and_hands_the_screenshot_back_as_a_picture(rig: Rig) -> None:
    arguments = {"action": "click", "x": 250, "y": 750}
    outcome = await rig.provider.run(rig.tool("computer"), commission_id="c1", arguments=arguments)
    assert outcome == BuiltinOutcome(
        True,
        "Did click. Screenshot after it: 64x36 px.",
        (BuiltinImage(base64.b64encode(JPEG).decode("ascii"), "image/jpeg"),),
    )
    ((action, passed, stop),) = rig.desktop.calls
    assert action == "click" and passed == arguments
    # The call gets a copy: what the tool does with it does not reach the caller's.
    assert passed is not arguments
    assert isinstance(stop, threading.Event) and not stop.is_set()
    # Sending input and grabbing the screen block, so they are kept off the event loop.
    assert rig.desktop.threads[0] is not threading.main_thread()
    assert rig.desktop.threads[0] is not threading.current_thread()


async def test_a_result_without_a_screenshot_has_no_picture_and_no_action_is_an_empty_one(rig: Rig) -> None:
    rig.desktop.behaviour = lambda action, arguments, stop: DesktopResult(text="Nothing to see.")
    assert await rig.computer() == BuiltinOutcome(True, "Nothing to see.")
    assert rig.desktop.calls[0][0] == ""
    await rig.provider.run(rig.tool("computer"), commission_id="c1", arguments=None)  # type: ignore[arg-type]
    assert rig.desktop.calls[1][:2] == ("", {})


async def test_two_desktop_calls_never_overlap(rig: Rig) -> None:
    let_go = threading.Event()

    def slow(action: str, arguments: dict[str, Any], stop: Any) -> DesktopResult:
        if action == "click":
            assert let_go.wait(10)
        return DesktopResult(text=f"Did {action}.", screenshot=JPEG)

    rig.desktop.behaviour = slow
    first = asyncio.ensure_future(rig.computer("c1", action="click", x=1, y=1))
    await until(lambda: rig.desktop.active == 1, "the first call reaching the desktop")
    second = asyncio.ensure_future(rig.computer("c2", action="type", text="hello"))
    # The second call gets every chance to start, and does not: the first has the mouse.
    for _ in range(50):
        await asyncio.sleep(0)
    await asyncio.sleep(0.02)
    assert len(rig.desktop.calls) == 1 and not second.done()
    let_go.set()
    one, two = await asyncio.wait_for(asyncio.gather(first, second), 10.0)
    assert (one.text, two.text) == ("Did click.", "Did type.")
    assert [call[0] for call in rig.desktop.calls] == ["click", "type"]
    assert rig.desktop.most_at_once == 1


async def test_a_call_that_waited_its_turn_is_refused_when_the_group_was_switched_off_meanwhile(rig: Rig) -> None:
    let_go = threading.Event()
    rig.desktop.behaviour = lambda action, arguments, stop: (let_go.wait(10), DesktopResult(text="Did it."))[1]
    first = asyncio.ensure_future(rig.computer("c1", action="click", x=1, y=1))
    await until(lambda: rig.desktop.active == 1, "the first call reaching the desktop")
    second = asyncio.ensure_future(rig.computer("c2", action="type", text="hello"))
    for _ in range(20):
        await asyncio.sleep(0)
    rig.state.states[BUILTIN_GROUP_KEYBOARD_MOUSE] = (False, "switched off")
    let_go.set()
    one, two = await asyncio.wait_for(asyncio.gather(first, second), 10.0)
    assert one.success
    assert two == BuiltinOutcome(False, "keyboard and mouse control is switched off")
    assert len(rig.desktop.calls) == 1


async def test_cancelling_a_desktop_call_sets_its_stop_event_and_waits_for_the_worker(rig: Rig) -> None:
    worker_done = threading.Event()

    def until_stopped(action: str, arguments: dict[str, Any], stop: threading.Event) -> DesktopResult:
        try:
            assert stop.wait(10), "the stop event was never set"
            raise DesktopStopped("stopped")
        finally:
            worker_done.set()

    rig.desktop.behaviour = until_stopped
    call = asyncio.ensure_future(rig.computer(action="wait", seconds=30))
    await until(lambda: rig.desktop.active == 1, "the call reaching the desktop")
    assert rig.provider.is_running(BUILTIN_GROUP_KEYBOARD_MOUSE)
    call.cancel()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(call, 10.0)
    stop = rig.desktop.calls[0][2]
    assert stop.is_set()
    # The cancellation was only passed on once the worker had let go of the mouse.
    assert worker_done.is_set() and rig.desktop.active == 0
    assert not rig.provider.is_running()


async def test_the_next_desktop_call_starts_only_after_a_stopped_worker_has_let_go(rig: Rig) -> None:
    let_go = threading.Event()

    def slow_to_stop(action: str, arguments: dict[str, Any], stop: threading.Event) -> DesktopResult:
        if action == "drag":
            assert stop.wait(10)
            # It is stopped, and still has a button to release.
            assert let_go.wait(10)
            raise DesktopStopped("stopped")
        return DesktopResult(text="Did type.")

    rig.desktop.behaviour = slow_to_stop
    first = asyncio.ensure_future(rig.computer("c1", action="drag", x=1, y=1, to_x=5, to_y=5))
    await until(lambda: rig.desktop.active == 1, "the first call reaching the desktop")
    first.cancel()
    second = asyncio.ensure_future(rig.computer("c2", action="type", text="hello"))
    await until(lambda: rig.desktop.calls[0][2].is_set(), "the stop event being set")
    await asyncio.sleep(0.05)
    # The stopped worker still drives the pointer: neither call is over, and the second has not begun.
    assert not first.done() and not second.done()
    assert len(rig.desktop.calls) == 1
    let_go.set()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(first, 10.0)
    assert (await asyncio.wait_for(second, 10.0)).text == "Did type."
    assert rig.desktop.most_at_once == 1


async def test_the_wait_for_a_stopped_worker_is_bounded(rig: Rig, monkeypatch: Any, caplog: Any) -> None:
    assert provider_module._DESKTOP_STOP_WAIT_SECONDS == 15.0
    monkeypatch.setattr(provider_module, "_DESKTOP_STOP_WAIT_SECONDS", 0.05)
    let_go = threading.Event()

    def deaf(action: str, arguments: dict[str, Any], stop: threading.Event) -> DesktopResult:
        if action == "wait":
            assert let_go.wait(10)
        return DesktopResult(text=f"Did {action}.")

    rig.desktop.behaviour = deaf
    try:
        call = asyncio.ensure_future(rig.computer(action="wait"))
        await until(lambda: rig.desktop.active == 1, "the call reaching the desktop")
        call.cancel()
        with caplog.at_level("WARNING", logger=provider_module.__name__):
            with pytest.raises(asyncio.CancelledError):
                await asyncio.wait_for(call, 5.0)
        assert "did not stop within" in caplog.text
        assert rig.desktop.calls[0][2].is_set()
        assert not rig.provider.is_running()
        # After that long the next call may start.
        assert (await asyncio.wait_for(rig.computer(action="screenshot"), 5.0)).text == "Did screenshot."
    finally:
        let_go.set()
        await until(lambda: rig.desktop.active == 0, "the worker ending")


async def test_an_action_that_cannot_be_carried_out_is_a_failed_outcome_with_its_text(rig: Rig) -> None:
    def refuses(action: str, arguments: dict[str, Any], stop: Any) -> DesktopResult:
        raise DesktopActionError("x=1280 is not a coordinate: both axes run from 0 to 999")

    rig.desktop.behaviour = refuses
    assert await rig.computer(action="click", x=1280, y=5) == BuiltinOutcome(
        False, "x=1280 is not a coordinate: both axes run from 0 to 999"
    )
    assert not rig.provider.is_running()


async def test_a_call_the_desktop_reports_as_stopped_is_a_failed_outcome(rig: Rig) -> None:
    def stopped(action: str, arguments: dict[str, Any], stop: Any) -> DesktopResult:
        raise DesktopStopped("stopped")

    rig.desktop.behaviour = stopped
    assert await rig.computer(action="wait") == BuiltinOutcome(False, "stopped")


async def test_any_other_failure_is_told_by_its_type_only(rig: Rig, caplog: Any) -> None:
    def breaks(action: str, arguments: dict[str, Any], stop: Any) -> DesktopResult:
        raise RuntimeError("could not read the window titled 'Payroll 2026 - secret.xlsx'")

    rig.desktop.behaviour = breaks
    with caplog.at_level("DEBUG", logger=provider_module.__name__):
        outcome = await rig.computer(action="screenshot")
    assert outcome == BuiltinOutcome(False, "the action failed: RuntimeError")
    # What it raised can quote the screen: neither the task agent nor the log gets it.
    assert "Payroll" not in outcome.text and "Payroll" not in caplog.text
    assert "exc_type=RuntimeError" in caplog.text
    assert not rig.provider.is_running()
    # The next call is not held up by the failure.
    rig.desktop.behaviour = None
    assert (await rig.computer(action="screenshot")).success


# ---------------------------------------------------------------------------
# What is being carried out right now
# ---------------------------------------------------------------------------


async def test_a_running_desktop_call_is_reported_while_it_runs(rig: Rig) -> None:
    let_go = threading.Event()
    rig.desktop.behaviour = lambda action, arguments, stop: (let_go.wait(10), DesktopResult(text="Did it."))[1]
    assert not rig.provider.is_running() and not rig.provider.is_running(BUILTIN_GROUP_KEYBOARD_MOUSE)
    call = asyncio.ensure_future(rig.computer(action="click", x=1, y=1))
    await until(lambda: rig.desktop.active == 1, "the call reaching the desktop")
    assert rig.provider.is_running()
    assert rig.provider.is_running(BUILTIN_GROUP_KEYBOARD_MOUSE)
    assert not rig.provider.is_running(BUILTIN_GROUP_BROWSER)
    let_go.set()
    await asyncio.wait_for(call, 10.0)
    assert not rig.provider.is_running() and not rig.provider.is_running(BUILTIN_GROUP_KEYBOARD_MOUSE)


async def test_a_running_browser_call_is_reported_while_it_runs(rig: Rig) -> None:
    rig.browser.gate = asyncio.Event()
    call = asyncio.ensure_future(rig.browse("browser_click", uid="1_2"))
    await asyncio.wait_for(rig.browser.entered.wait(), 5.0)
    assert rig.provider.is_running()
    assert rig.provider.is_running(BUILTIN_GROUP_BROWSER)
    assert not rig.provider.is_running(BUILTIN_GROUP_KEYBOARD_MOUSE)
    rig.browser.gate.set()
    await asyncio.wait_for(call, 5.0)
    assert not rig.provider.is_running() and not rig.provider.is_running(BUILTIN_GROUP_BROWSER)


async def test_a_browser_call_that_raises_or_is_cancelled_is_no_longer_reported_as_running(rig: Rig) -> None:
    rig.browser.error = KeyError("frame")
    with pytest.raises(KeyError):
        await rig.browse("browser_take_snapshot")
    assert not rig.provider.is_running()
    rig.browser.error = None
    rig.browser.gate = asyncio.Event()
    rig.browser.entered.clear()
    call = asyncio.ensure_future(rig.browse("browser_wait_for", text=["x"]))
    await asyncio.wait_for(rig.browser.entered.wait(), 5.0)
    call.cancel()
    with pytest.raises(asyncio.CancelledError):
        await call
    assert not rig.provider.is_running()


# ---------------------------------------------------------------------------
# Browser calls
# ---------------------------------------------------------------------------


async def test_a_browser_call_goes_to_the_browser_tools_by_its_name_with_a_copy_of_its_arguments(rig: Rig) -> None:
    arguments = {"uid": "1_9", "includeSnapshot": True}
    rig.browser.outcome = BuiltinOutcome(True, "Successfully clicked on the element", (BuiltinImage("QUJD", "image/jpeg"),))
    outcome = await rig.provider.run(rig.tool("browser_click"), commission_id="c1", arguments=arguments)
    assert outcome is rig.browser.outcome
    assert rig.browser.calls == [("browser_click", arguments)]
    assert rig.browser.calls[0][1] is not arguments
    assert rig.desktop.calls == []
    await rig.provider.run(rig.tool("browser_list_pages"), commission_id="c1", arguments=None)  # type: ignore[arg-type]
    assert rig.browser.calls[1] == ("browser_list_pages", {})


async def test_a_failed_browser_outcome_is_passed_on_as_it_is(rig: Rig) -> None:
    rig.browser.outcome = BuiltinOutcome(False, "No snapshot found for page 1. Use browser_take_snapshot to capture one.")
    assert await rig.browse("browser_click", uid="1_2") is rig.browser.outcome


# ---------------------------------------------------------------------------
# The window of the browser
# ---------------------------------------------------------------------------


async def test_the_listener_is_told_where_the_browser_window_is_when_that_is_first_known_or_changed() -> None:
    told: list[Any] = []
    rig = Rig(on_browser_window=told.append)
    assert rig.provider.browser_window() is None
    await rig.browse()
    # Not known yet: nothing to tell.
    assert told == []
    rig.browser.window_info = {"browser": "Google Chrome", "window_id": 7, "bounds": {"left": 100, "top": 50}}
    await rig.browse()
    assert told == [{"browser": "Google Chrome", "window_id": 7, "bounds": {"left": 100, "top": 50}}]
    assert rig.provider.browser_window() == told[0]
    # The same window in the same place is not told again.
    await rig.browse()
    await rig.browse()
    assert len(told) == 1
    rig.browser.window_info = {"browser": "Google Chrome", "window_id": 7, "bounds": {"left": 400, "top": 50}}
    await rig.browse()
    assert len(told) == 2 and told[1]["bounds"] == {"left": 400, "top": 50}
    # Gone (the browser was closed): told once more, with nothing.
    rig.browser.window_info = None
    await rig.browse()
    assert told[2:] == [None]


async def test_the_next_commission_is_told_about_the_window_afresh() -> None:
    told: list[Any] = []
    rig = Rig(on_browser_window=told.append)
    rig.browser.window_info = {"window_id": 7}
    await rig.browse(commission_id="c1")
    await rig.provider.released(BUILTIN_GROUP_BROWSER, "c1")
    await rig.browse(commission_id="c2")
    assert told == [{"window_id": 7}, {"window_id": 7}]
    # Letting go of the keyboard and mouse does not concern the window.
    await rig.provider.released(BUILTIN_GROUP_KEYBOARD_MOUSE, "c2")
    await rig.browse(commission_id="c2")
    assert len(told) == 2


async def test_a_listener_that_fails_does_not_fail_the_call_and_none_is_needed() -> None:
    def broken(window: Any) -> None:
        raise RuntimeError("the banner is gone")

    rig = Rig(on_browser_window=broken)
    rig.browser.window_info = {"window_id": 7}
    assert (await rig.browse()).success
    plain = Rig()
    plain.browser.window_info = {"window_id": 7}
    assert (await plain.browse()).success
    # It can be set later, as the embedding process does.
    told: list[Any] = []
    plain.provider.on_browser_window = told.append
    plain.browser.window_info = {"window_id": 8}
    await plain.browse()
    assert told == [{"window_id": 8}]


async def test_the_window_is_not_told_after_a_desktop_call(rig: Rig) -> None:
    told: list[Any] = []
    rig.provider.on_browser_window = told.append
    rig.browser.window_info = {"window_id": 7}
    await rig.computer(action="screenshot")
    assert told == []


# ---------------------------------------------------------------------------
# Letting go
# ---------------------------------------------------------------------------


async def test_letting_go_of_the_keyboard_and_mouse_releases_held_input_in_a_worker_thread(rig: Rig) -> None:
    await rig.provider.released(BUILTIN_GROUP_KEYBOARD_MOUSE, "c1")
    assert len(rig.desktop.release_threads) == 1
    assert rig.desktop.release_threads[0] is not threading.current_thread()
    assert rig.browser.released_calls == 0


async def test_letting_go_of_the_browser_forgets_its_snapshots(rig: Rig) -> None:
    await rig.provider.released(BUILTIN_GROUP_BROWSER, "c1")
    assert rig.browser.released_calls == 1
    assert rig.desktop.release_threads == []


async def test_letting_go_of_what_was_never_used_or_is_not_a_group_does_nothing(rig: Rig) -> None:
    await rig.provider.released("teleport", "c1")
    assert rig.desktop.release_threads == [] and rig.browser.released_calls == 0
    bare = BuiltinToolProvider(state=Switches())
    await bare.released(BUILTIN_GROUP_KEYBOARD_MOUSE, "c1")
    await bare.released(BUILTIN_GROUP_BROWSER, "c1")
    await bare.close_browser()
    await bare.aclose()
    # Nothing was made just to be let go of.
    assert bare._desktop is None and bare._browser is None


async def test_closing_the_browser_closes_the_browser_tools_only(rig: Rig) -> None:
    await rig.provider.close_browser()
    assert rig.browser.closed == 1
    assert rig.desktop.release_threads == []


async def test_shutting_down_releases_held_input_and_closes_the_browser(rig: Rig) -> None:
    await rig.provider.aclose()
    assert len(rig.desktop.release_threads) == 1
    assert rig.desktop.release_threads[0] is not threading.current_thread()
    assert rig.browser.closed == 1


async def test_shutting_down_closes_the_browser_even_when_held_input_cannot_be_released(rig: Rig) -> None:
    rig.desktop.release_error = RuntimeError("the keyboard is gone")
    await rig.provider.aclose()
    assert rig.browser.closed == 1


async def test_a_browser_call_that_waited_its_turn_is_not_carried_out_once_the_browser_is_switched_off(rig: Rig) -> None:
    # Switched off while the call queued behind another one.
    rig.browser.while_waiting = lambda: rig.state.states.__setitem__(BUILTIN_GROUP_BROWSER, (False, "switched off"))
    outcome = await rig.provider.run(rig.tool("browser_click"), commission_id="c1", arguments={"uid": "1_1"})
    assert outcome == BuiltinOutcome(False, "browser control is switched off")
    assert rig.browser.calls == [] and not rig.provider.is_running(BUILTIN_GROUP_BROWSER)


def test_the_browser_the_tools_drive_is_told_which_ports_are_this_applications_own(monkeypatch: Any) -> None:
    ports = lambda: {48911, 48915}  # noqa: E731 - the very object is looked for below
    monkeypatch.setattr(tools_module, "BrowserTools", FakeBrowserTools)
    provider = BuiltinToolProvider(state=Switches(), own_ports=ports)
    session = provider.browser().session
    assert session._own_ports is ports
    assert BuiltinToolProvider(state=Switches()).browser().session._own_ports is None


async def test_killing_the_browser_is_passed_on_and_does_nothing_where_no_browser_tools_were_made() -> None:
    bare = BuiltinToolProvider(state=Switches())
    bare.kill_browser()
    assert bare._browser is None

    class Killable:
        def __init__(self) -> None:
            self.kills = 0

        def kill(self) -> None:
            self.kills += 1

    tools = Killable()
    bare._browser = tools  # type: ignore[assignment]
    bare.kill_browser()
    assert tools.kills == 1


async def test_the_keys_can_be_let_go_of_without_closing_the_browser(rig: Rig) -> None:
    await rig.provider.release_keys()
    # For a shutdown that has dealt with the browser already.
    assert len(rig.desktop.release_threads) == 1 and rig.browser.closed == 0
    await rig.provider.aclose()
    assert len(rig.desktop.release_threads) == 2 and rig.browser.closed == 1


async def test_a_browser_counts_as_unclosed_only_while_the_browser_tools_still_hold_one() -> None:
    bare = BuiltinToolProvider(state=Switches())
    # No browser tools were made: nothing can have been left unclosed.
    assert bare.browser_unclosed() is False and bare._browser is None

    class Holding:
        held = True

        def holds_a_browser(self) -> bool:
            return self.held

    tools = Holding()
    bare._browser = tools  # type: ignore[assignment]
    assert bare.browser_unclosed() is True
    tools.held = False
    assert bare.browser_unclosed() is False


async def test_browser_calls_that_overlap_are_reported_until_the_last_of_them_is_over(rig: Rig) -> None:
    rig.browser.gate = asyncio.Event()
    # Two calls of one commission, as a task agent makes them side by side.
    first = asyncio.ensure_future(rig.browse("browser_click", uid="1_1"))
    second = asyncio.ensure_future(rig.browse("browser_hover", uid="1_2"))
    await until(lambda: len(rig.browser.calls) == 2, "both calls reaching the browser tools")
    assert rig.provider.is_running(BUILTIN_GROUP_BROWSER)

    # One of them is stopped; the other is still at it.
    second.cancel()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(second, 10.0)
    assert rig.provider.is_running(BUILTIN_GROUP_BROWSER) and rig.provider.is_running()

    rig.browser.gate.set()
    assert (await asyncio.wait_for(first, 10.0)).success
    assert not rig.provider.is_running(BUILTIN_GROUP_BROWSER) and not rig.provider.is_running()
    assert rig.provider._running == {}


class KillableBrowserTools(FakeBrowserTools):
    """Browser tools whose kill takes until the test lets it finish."""

    def __init__(self) -> None:
        super().__init__()
        self.kill_began = threading.Event()
        self.let_kill_finish = threading.Event()
        self.kills = 0
        self.kill_error: Exception | None = None

    def kill(self) -> None:
        self.kill_began.set()
        self.let_kill_finish.wait(30)
        self.kills += 1
        if self.kill_error is not None:
            raise self.kill_error


async def test_a_browser_call_waits_for_a_kill_nobody_waited_for(rig: Rig) -> None:
    tools = KillableBrowserTools()
    rig.provider._browser = tools  # type: ignore[assignment]
    try:
        # Asked for and not waited for: the caller goes on at once.
        rig.provider.kill_browser_unwaited()
        assert len(rig.provider._killing) == 1
        # A task that comes after it must not have its browser be what the kill finds.
        call = asyncio.ensure_future(rig.browse("browser_click", "commission-2", uid="1_1"))
        assert await asyncio.to_thread(tools.kill_began.wait, 10.0)
        await asyncio.sleep(0.05)
        assert tools.calls == [] and not call.done()
        # Waiting its turn, it counts as under way.
        assert rig.provider.is_running(BUILTIN_GROUP_BROWSER)
    finally:
        tools.let_kill_finish.set()

    assert (await asyncio.wait_for(call, 10.0)).success
    assert tools.kills == 1 and len(tools.calls) == 1
    assert rig.provider._killing == set()


async def test_a_kill_that_fails_does_not_keep_browser_calls_waiting_or_fail_them(rig: Rig) -> None:
    tools = KillableBrowserTools()
    tools.kill_error = OSError("access denied")
    tools.let_kill_finish.set()
    rig.provider._browser = tools  # type: ignore[assignment]

    rig.provider.kill_browser_unwaited()
    outcome = await asyncio.wait_for(rig.browse("browser_click", "commission-2", uid="1_1"), 10.0)

    assert outcome.success and tools.kills == 1
    assert rig.provider._killing == set()


async def test_a_call_stopped_while_it_waits_for_a_kill_leaves_the_kill_to_finish(rig: Rig) -> None:
    tools = KillableBrowserTools()
    rig.provider._browser = tools  # type: ignore[assignment]
    try:
        rig.provider.kill_browser_unwaited()
        call = asyncio.ensure_future(rig.browse("browser_click", "commission-2", uid="1_1"))
        assert await asyncio.to_thread(tools.kill_began.wait, 10.0)
        await asyncio.sleep(0.02)
        call.cancel()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(call, 10.0)
        # The call is gone, the kill is not.
        assert not rig.provider.is_running() and len(rig.provider._killing) == 1
        (killing,) = rig.provider._killing
        assert not killing.cancelled()
    finally:
        tools.let_kill_finish.set()
    await asyncio.wait_for(asyncio.shield(killing), 10.0)
    assert tools.kills == 1 and tools.calls == []


class _OverAndNotTakenDown(asyncio.Future):  # type: ignore[type-arg]
    """A kill that is over; waited for again and again, it says so instead of letting the test hang."""

    waits = 0

    def __await__(self) -> Any:
        type(self).waits += 1
        if type(self).waits > 100:
            raise AssertionError("the call waits again and again for a kill that is over")
        return super().__await__()


async def test_a_kill_that_is_over_but_not_taken_down_yet_does_not_keep_a_browser_call_spinning(rig: Rig) -> None:
    # The moment between the kill ending on its thread and the event loop taking note of it.
    over = _OverAndNotTakenDown()
    over.set_result(None)
    rig.provider._killing = {over}

    outcome = await asyncio.wait_for(rig.browse("browser_click", "commission-2", uid="1_1"), 3.0)

    # Waiting for it would never give the event loop the turn in which it is taken down.
    assert outcome.success and len(rig.browser.calls) == 1


async def test_a_browser_call_waits_for_every_kill_that_is_under_way_and_not_only_the_last(rig: Rig) -> None:
    first, second = threading.Event(), threading.Event()
    began: list[str] = []
    ended: list[str] = []
    tools = FakeBrowserTools()
    turn = iter(("first", "second"))
    gates = {"first": first, "second": second}

    def kill() -> None:
        which = next(turn)
        began.append(which)
        gates[which].wait(30)
        ended.append(which)

    tools.kill = kill  # type: ignore[attr-defined]
    rig.provider._browser = tools  # type: ignore[assignment]
    try:
        # The page gave up on the first request and the user stopped everything once more.
        rig.provider.kill_browser_unwaited()
        rig.provider.kill_browser_unwaited()
        await until(lambda: len(began) == 2, "both kills beginning")
        call = asyncio.ensure_future(rig.browse("browser_click", "commission-2", uid="1_1"))
        # The second ends first.
        second.set()
        await until(lambda: ended == ["second"] and len(rig.provider._killing) == 1, "the second kill ending")
        await asyncio.sleep(0.05)
        # The first is still looking for what runs on the profile: a browser started now would be it.
        assert tools.calls == [] and not call.done()
    finally:
        first.set()
        second.set()

    assert (await asyncio.wait_for(call, 10.0)).success
    assert ended == ["second", "first"] and rig.provider._killing == set()


async def test_a_browser_call_whose_turn_comes_while_a_kill_is_under_way_is_refused(rig: Rig) -> None:
    tools = KillableBrowserTools()
    rig.provider._browser = tools  # type: ignore[assignment]
    # The call was waiting its turn (the browser was being closed) when the kill was asked for.
    tools.while_waiting = rig.provider.kill_browser_unwaited
    try:
        outcome = await asyncio.wait_for(rig.browse("browser_click", "commission-2", uid="1_1"), 10.0)
        # Carried out, it would start a browser for the kill to find.
        assert outcome == BuiltinOutcome(False, "the browser is being closed; call the tool again in a moment")
        assert tools.calls == []
    finally:
        tools.let_kill_finish.set()
    await until(lambda: rig.provider._killing == set(), "the kill ending")
    # Once it is over, the next call is carried out.
    tools.while_waiting = None
    assert (await asyncio.wait_for(rig.browse("browser_click", "commission-2", uid="1_1"), 10.0)).success


# ---------------------------------------------------------------------------
# A browser that is being stopped, and a process that shuts down
# ---------------------------------------------------------------------------

BEING_CLOSED = BuiltinOutcome(False, "the browser is being closed; call the tool again in a moment")


async def test_a_browser_call_whose_turn_comes_while_the_browser_is_being_stopped_is_refused(rig: Rig) -> None:
    # Whoever stops the browser asks for the kill only once the closing has run out of time. A call that
    # waited its turn behind the closing gets it before that, with no kill under way to hold it off.
    with rig.provider.stopping_browser():
        assert await rig.browse("browser_click", "commission-2", uid="1_1") == BEING_CLOSED
        # Stopped twice at once (the page gave up on the first request and the user pressed again): the one
        # that ends first does not end it for the other.
        with rig.provider.stopping_browser():
            pass
        assert await rig.browse("browser_click", "commission-2", uid="1_1") == BEING_CLOSED
        assert rig.browser.calls == []
        # The keyboard and the mouse have nothing to do with it.
        assert (await rig.computer(action="click", x=1, y=1)).success
    # Once it is over the next call is carried out.
    assert (await rig.browse("browser_click", "commission-2", uid="1_1")).success
    assert rig.browser.calls == [("browser_click", {"uid": "1_1"})]


async def test_a_stopping_that_fails_is_over_all_the_same(rig: Rig) -> None:
    with pytest.raises(RuntimeError, match="the closing failed"):
        with rig.provider.stopping_browser():
            raise RuntimeError("the closing failed")
    assert (await rig.browse("browser_click", "commission-2", uid="1_1")).success


async def test_a_call_that_began_to_wait_before_the_stopping_is_refused_when_its_turn_comes_within_it(rig: Rig) -> None:
    # As it goes on Python 3.11: the closing is given up, the call that waited behind it takes its turn,
    # and only then does the one who stops the browser get round to asking for the kill.
    stopping = rig.provider.stopping_browser()
    rig.browser.while_waiting = stopping.__enter__
    try:
        assert await rig.browse("browser_click", "commission-2", uid="1_1") == BEING_CLOSED
        assert rig.browser.calls == []
    finally:
        stopping.__exit__(None, None, None)


async def test_once_the_process_shuts_down_no_call_is_carried_out(rig: Rig) -> None:
    listed = rig.provider.available()
    rig.provider.shut_down()
    outcome = await rig.browse("browser_click", "commission-2", uid="1_1")
    assert outcome == BuiltinOutcome(False, "browser_click is not available: shutting down with the application")
    outcome = await rig.computer(action="click", x=1, y=1)
    assert outcome == BuiltinOutcome(False, "computer is not available: shutting down with the application")
    assert rig.browser.calls == [] and rig.desktop.calls == []
    # What the list of tools says is the switches' to say, as before.
    assert rig.provider.available() == listed and all(item.available for item in listed)


async def test_a_browser_call_whose_turn_comes_after_the_shutdown_began_is_refused(rig: Rig) -> None:
    # It waited behind the closing of the browser; carried out, it would start one nobody is left to close.
    rig.browser.while_waiting = rig.provider.shut_down
    outcome = await rig.browse("browser_click", "commission-2", uid="1_1")
    assert outcome == BuiltinOutcome(False, "browser control is shutting down with the application")
    assert rig.browser.calls == []


async def test_a_keyboard_call_whose_turn_comes_after_the_shutdown_began_is_refused(rig: Rig) -> None:
    let_go = threading.Event()
    rig.desktop.behaviour = lambda action, arguments, stop: (let_go.wait(10), DesktopResult(text="Did it."))[1]
    first = asyncio.ensure_future(rig.computer("c1", action="click", x=1, y=1))
    await until(lambda: rig.desktop.active == 1, "the first call reaching the desktop")
    second = asyncio.ensure_future(rig.computer("c2", action="type", text="hello"))
    for _ in range(20):
        await asyncio.sleep(0)
    rig.provider.shut_down()
    let_go.set()
    one, two = await asyncio.wait_for(asyncio.gather(first, second), 10.0)
    # The one under way goes on to its end; the keys are let go of after it.
    assert one.success
    assert two == BuiltinOutcome(False, "keyboard and mouse control is shutting down with the application")
    assert len(rig.desktop.calls) == 1
