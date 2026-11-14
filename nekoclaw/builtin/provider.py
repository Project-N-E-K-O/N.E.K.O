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

``BuiltinToolProvider`` implements ``nekoclaw.host.capabilities.BuiltinCapabilities``.
Whoever embeds it says, through ``state``, whether each group is switched on
and ready right now; the provider adds what only it can know (whether input
can be sent at all, whether a browser can be found).

Which commission may use a group is decided by the host (it occupies the
group, ``nekoclaw/host/leases.py``). Here only single calls are kept apart:
the commissions of one team share an occupation, and two calls must never
drive the mouse, or the same browser page, at the same time.
"""

from __future__ import annotations

import asyncio
import base64
import contextlib
import logging
import threading
from collections.abc import Callable, Iterable, Iterator
from typing import Any

from nekoclaw.host.capabilities import (
    BUILTIN_GROUP_BROWSER,
    BUILTIN_GROUP_KEYBOARD_MOUSE,
    BuiltinImage,
    BuiltinOutcome,
    Capability,
    builtin_tool,
)

logger = logging.getLogger(__name__)

# Returns whether a group is switched on and ready, and if not, why.
StateReader = Callable[[str], "tuple[bool, str]"]
# Told where the browser window being driven is, whenever that changed.
WindowListener = Callable[["dict[str, Any] | None"], None]

# How long a stopped keyboard-and-mouse call waits for its worker thread to
# let go of the mouse before the next call may start.
_DESKTOP_STOP_WAIT_SECONDS = 15.0

# Why no call is carried out any more once the process shuts down; put after
# "... is not available:" and "... control is".
_SHUTTING_DOWN = "shutting down with the application"


def _drop_outcome(future: "asyncio.Future[Any]") -> None:
    """Take a stopped call's late result or failure, which is of no use to anyone by now."""
    if not future.cancelled():
        future.exception()


class BuiltinToolProvider:
    """Lists the keyboard-and-mouse and browser tools and carries their calls out."""

    def __init__(
        self,
        *,
        state: StateReader,
        desktop: Any = None,
        browser: Any = None,
        on_browser_window: WindowListener | None = None,
        own_ports: Callable[[], Iterable[int]] | None = None,
    ) -> None:
        self._state = state
        # Returns the ports of this application's own services; the browser
        # is kept from reaching them (see ``BrowserSession``).
        self._own_ports = own_ports
        # Told when the window of the browser being driven is first known, or
        # has moved: the browser is only started by the first call, after the
        # commission began to occupy it.
        self.on_browser_window = on_browser_window
        self._last_window: dict[str, Any] | None = None
        # ``nekoclaw.builtin.desktop.Desktop``; made on first use.
        self._desktop = desktop
        # ``nekoclaw.builtin.browser.tools.BrowserTools``; made on first use.
        self._browser = browser
        self._desktop_lock: asyncio.Lock | None = None
        # group -> how many of its calls are under way right now, those that
        # wait their turn included. Counted, not marked: the calls of one
        # group overlap (the browser tools queue them), and one that ends or
        # is stopped must not hide the ones still at it.
        self._running: dict[str, int] = {}
        # The kills of the browser that nobody waits for (``kill_browser_unwaited``)
        # and that are not over yet; browser calls wait for all of them.
        self._killing: set[asyncio.Future[None]] = set()
        # How many callers are closing the browser right now and will have it
        # killed should that fail (``stopping_browser``). A browser call
        # whose turn comes meanwhile is refused.
        self._stopping_browser = 0
        # Set once the process shuts down (``shut_down``): no call of any
        # tool is carried out from then on.
        self._shut_down = False

    # -- availability -----------------------------------------------------

    def available(self) -> list[Capability]:
        """Return every built-in tool with its availability as of now. Does not block."""
        from config.prompts.prompts_nekoclaw_builtin import (
            BROWSER_SCREENSHOT_TOOL_NAME,
            BROWSER_TOOLS,
            COMPUTER_TOOL,
        )

        items: list[Capability] = []
        ready, reason = self._state(BUILTIN_GROUP_KEYBOARD_MOUSE)
        # It works from screenshots: of no use to a model that cannot see them.
        items.append(
            builtin_tool(
                BUILTIN_GROUP_KEYBOARD_MOUSE,
                COMPUTER_TOOL,
                available=ready,
                unavailable_reason=reason,
                needs_images=True,
            )
        )
        ready, reason = self._state(BUILTIN_GROUP_BROWSER)
        for definition in BROWSER_TOOLS:
            items.append(
                builtin_tool(
                    BUILTIN_GROUP_BROWSER,
                    definition,
                    available=ready,
                    unavailable_reason=reason,
                    needs_images=definition["name"] == BROWSER_SCREENSHOT_TOOL_NAME,
                )
            )
        return items

    def _ready(self, group: str) -> tuple[bool, str | None]:
        """Whether a call of a group may be carried out now, and why not."""
        if self._shut_down:
            return False, _SHUTTING_DOWN
        return self._state(group)

    def shut_down(self) -> None:
        """No call is carried out from now on; for when the process shuts down. Not undone.

        The shutdown closes the browser and lets go of the keys once. A task
        agent whose runtime has not been stopped yet (its start outlasted
        the time the shutdown waits for the host) goes on calling tools
        meanwhile: the next browser call would start a browser nobody is left
        to close, the next keyboard call press a key nobody is left to
        release.
        """
        self._shut_down = True

    def is_running(self, group: str | None = None) -> bool:
        """Whether a call is under way right now (of one group, or of any)."""
        if group is None:
            return bool(self._running)
        return group in self._running

    def _begin(self, group: str) -> None:
        self._running[group] = self._running.get(group, 0) + 1

    def _end(self, group: str) -> None:
        left = self._running.get(group, 0) - 1
        if left > 0:
            self._running[group] = left
        else:
            self._running.pop(group, None)

    # -- the parts, made on first use --------------------------------------

    def desktop(self) -> Any:
        if self._desktop is None:
            from nekoclaw.builtin.desktop import Desktop

            self._desktop = Desktop()
        return self._desktop

    def browser(self) -> Any:
        if self._browser is None:
            from nekoclaw.builtin.browser.session import BrowserSession
            from nekoclaw.builtin.browser.tools import BrowserTools

            self._browser = BrowserTools(BrowserSession(own_ports=self._own_ports))
        return self._browser

    # -- calls --------------------------------------------------------------

    async def run(self, capability: Capability, *, commission_id: str, arguments: dict[str, Any]) -> BuiltinOutcome:
        """Carry out one call. Cancelling it stops what it started."""
        group = capability.group
        ready, reason = self._ready(group or "")
        if not ready:
            return BuiltinOutcome(False, f"{capability.tool_name} is not available: {reason or 'switched off'}")
        if group == BUILTIN_GROUP_KEYBOARD_MOUSE:
            return await self._run_desktop(dict(arguments or {}))
        if group == BUILTIN_GROUP_BROWSER:
            return await self._run_browser(capability.tool_name, dict(arguments or {}))
        return BuiltinOutcome(False, f"unknown tool {capability.tool_name}")

    async def _run_desktop(self, arguments: dict[str, Any]) -> BuiltinOutcome:
        from nekoclaw.builtin.desktop import DesktopActionError, DesktopStopped

        if self._desktop_lock is None:
            self._desktop_lock = asyncio.Lock()
        action = str(arguments.get("action") or "")
        async with self._desktop_lock:
            # Checked again: it may have been switched off while this waited its turn.
            ready, reason = self._ready(BUILTIN_GROUP_KEYBOARD_MOUSE)
            if not ready:
                return BuiltinOutcome(False, f"keyboard and mouse control is {reason or 'switched off'}")
            loop = asyncio.get_running_loop()
            stop = threading.Event()
            # Sending input and grabbing the screen block; they run in a worker thread.
            work = loop.run_in_executor(None, self.desktop().act, action, arguments, stop)
            self._begin(BUILTIN_GROUP_KEYBOARD_MOUSE)
            try:
                result = await asyncio.shield(work)
            except asyncio.CancelledError:
                stop.set()
                work.add_done_callback(_drop_outcome)
                # The worker thread may still have the mouse. The lock is kept
                # until it has let go, for a bounded time: the next call must
                # not start while it drives the pointer.
                try:
                    await asyncio.wait({work}, timeout=_DESKTOP_STOP_WAIT_SECONDS)
                    if not work.done():
                        logger.warning(
                            "the keyboard-and-mouse worker did not stop within %.0fs", _DESKTOP_STOP_WAIT_SECONDS
                        )
                except asyncio.CancelledError:
                    pass  # Cancelled again while waiting; the cancellation is raised just below.
                raise
            except DesktopActionError as exc:
                return BuiltinOutcome(False, str(exc))
            except DesktopStopped:
                return BuiltinOutcome(False, "stopped")
            except Exception as exc:  # noqa: BLE001 - told to the task agent by its type only
                # What it raises can quote the screen; only its type is logged.
                logger.error("keyboard and mouse call failed (exc_type=%s)", type(exc).__name__)
                return BuiltinOutcome(False, f"the action failed: {type(exc).__name__}")
            finally:
                self._end(BUILTIN_GROUP_KEYBOARD_MOUSE)
        images: tuple[BuiltinImage, ...] = ()
        if result.screenshot:
            images = (BuiltinImage(base64.b64encode(result.screenshot).decode("ascii"), result.mime_type),)
        return BuiltinOutcome(True, result.text, images)

    async def _run_browser(self, tool_name: str, arguments: dict[str, Any]) -> BuiltinOutcome:
        def still_on() -> str | None:
            # Asked when the call's turn has come: the browser may have been
            # switched off while it waited (or the process begun to shut
            # down), and must not be started again.
            ready, reason = self._ready(BUILTIN_GROUP_BROWSER)
            if not ready:
                return f"browser control is {reason or 'switched off'}"
            # Nor while a kill is under way that was asked for after this
            # call began to wait its turn: it cannot be waited for here, and
            # the browser this call started would be what the kill finds.
            # Nor while the browser is being closed by someone who will have
            # it killed if it does not close: this call's turn may come the
            # moment that closing lets go, before the kill is on record.
            if self._stopping_browser or self._kills_under_way():
                return "the browser is being closed; call the tool again in a moment"
            return None

        self._begin(BUILTIN_GROUP_BROWSER)
        try:
            # A kill that was asked for earlier and is not over yet takes what
            # it finds when its turn on the worker threads comes: started
            # before that, the browser of this call would be what it finds.
            while under_way := self._kills_under_way():
                # Not stopped along with this call, should it be stopped.
                await asyncio.wait(under_way)
            outcome = await self.browser().call(tool_name, arguments, refused=still_on)
        finally:
            self._end(BUILTIN_GROUP_BROWSER)
        self._tell_window()
        return outcome

    def _tell_window(self) -> None:
        window = self.browser_window()
        if window == self._last_window:
            return
        self._last_window = window
        listener = self.on_browser_window
        if listener is None:
            return
        try:
            listener(window)
        except Exception:  # noqa: BLE001 - a display that fails must not fail the call
            logger.debug("telling where the browser window is failed", exc_info=True)

    # -- letting go ----------------------------------------------------------

    async def released(self, group: str, commission_id: str) -> None:
        """A team no longer occupies a group: drop what was kept for it."""
        del commission_id
        if group == BUILTIN_GROUP_KEYBOARD_MOUSE:
            if self._desktop is not None:
                # Keys or buttons a stopped call left held must not stay down.
                await asyncio.to_thread(self._desktop.release_held)
        elif group == BUILTIN_GROUP_BROWSER:
            # The next commission is told about the window afresh.
            self._last_window = None
            if self._browser is not None:
                await self._browser.released()

    def browser_window(self) -> dict[str, Any] | None:
        """Return what is known about the browser window being driven, for the banner."""
        if self._browser is None:
            return None
        return self._browser.window()

    async def close_browser(self) -> None:
        """Close the browser this process started and drop the connection; for switching off."""
        if self._browser is not None:
            await self._browser.aclose()

    def kill_browser(self) -> None:
        """Kill the browser this process started without waiting for anything. Blocking.

        For when ``close_browser`` did not finish within the time a
        shutdown has, or finished without the browser being closed.
        """
        if self._browser is not None:
            self._browser.kill()

    def kill_browser_unwaited(self) -> None:
        """Have the browser this process started killed on a worker thread, and return at once.

        For a caller that cannot wait for it (the request that stops every
        task is given up on by the page after a few seconds, and killing
        waits for the processes to be gone). Browser calls made from now on
        wait until the kill is over, so that it never takes a browser that
        was started after it was asked for.
        """

        def kill() -> None:
            try:
                self.kill_browser()
            except Exception:  # noqa: BLE001 - nobody waits for it: said in the log
                logger.warning("killing the browser failed", exc_info=True)

        # One more, not the one: asked for twice (the page gave up on the
        # first request and the user pressed again), both are under way, and
        # the one that ends first must not hide the other.
        killing = asyncio.get_running_loop().run_in_executor(None, kill)
        self._killing.add(killing)
        killing.add_done_callback(self._killing.discard)

    @contextlib.contextmanager
    def stopping_browser(self) -> Iterator[None]:
        """For as long as a caller closes the browser and, should that fail, has it killed.

        The kill is only asked for once the closing has failed or run out of
        time. A browser call that waited its turn behind the closing gets it
        the moment the closing lets go, which is before its caller has got
        round to asking for the kill: it would start a browser, and the kill
        would take that one. Such a call is refused for as long as this
        lasts. Left once the browser is closed or the kill is on record
        (``kill_browser_unwaited``), with nothing awaited in between.
        """
        self._stopping_browser += 1
        try:
            yield
        finally:
            self._stopping_browser -= 1

    def _kills_under_way(self) -> list["asyncio.Future[None]"]:
        # (One that is over may not have been taken out yet: that is done in
        # a turn of the event loop of its own.)
        return [killing for killing in self._killing if not killing.done()]

    def browser_unclosed(self) -> bool:
        """Whether a browser is still held after ``close_browser``: closing it failed, and it is kept to be closed later."""
        return self._browser is not None and self._browser.holds_a_browser()

    async def release_keys(self) -> None:
        """Let go of keys and buttons a stopped call left held; never raises."""
        if self._desktop is not None:
            try:
                await asyncio.to_thread(self._desktop.release_held)
            except Exception:  # noqa: BLE001 - shutting down goes on
                logger.debug("releasing held keys failed", exc_info=True)

    async def aclose(self) -> None:
        """Let go of everything; for shutting down."""
        await self.release_keys()
        await self.close_browser()
