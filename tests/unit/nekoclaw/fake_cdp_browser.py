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

"""A browser made of Python objects, for the tests of the browser session and the browser tools.

``FakeBrowser`` models a few pages, each with a small tree of elements. It
answers the protocol commands ``BrowserSession`` sends from that model,
records every command it is sent, and emits the events a browser would.
``FakeConnection`` has the interface of ``CdpConnection`` and is what the
session gets when it connects. ``BrowserWorld`` puts all of it in place of
the launcher and of the real connection, so no process is started and no
socket is opened.

What the model keeps to, because the session depends on it:

* a document that is loaded numbers its elements from 1, so after a
  navigation the same DOM node number stands for another element (which is
  why a uid of an older document must be refused, not resolved);
* events of a page are only sent once its session enabled the ``Page``
  domain;
* while a JavaScript dialog is open the page answers nothing, and the command
  that opened it is answered only when the dialog has been handled;
* the scripts the session runs in a page cannot be executed here: the fake
  recognizes which of the session's constants it was sent and answers from
  its model.
"""

from __future__ import annotations

import asyncio
import base64
import itertools
import json
from collections.abc import Callable
from dataclasses import dataclass, field
from io import BytesIO
from pathlib import Path
from typing import Any

from nekoclaw.builtin.browser import launcher
from nekoclaw.builtin.browser import session as session_module
from nekoclaw.builtin.browser.cdp import CdpClosed, CdpConnection, CdpError, CdpTimeout
from nekoclaw.builtin.browser.launcher import BrowserInstall, RunningBrowser
from nekoclaw.builtin.browser.session import (
    _DOM_QUIET,
    _ELEMENT_OF_NODE,
    _FIND_TEXT,
    _PREPARE_FILL,
    _REACHES,
    _SELECT_OPTION,
    BrowserSession,
)
from nekoclaw.builtin.browser.settings import BrowserSettings, BrowserSettingsStore

_KIND_OF_ROLE = {
    "textbox": "text",
    "searchbox": "text",
    "checkbox": "toggle",
    "switch": "toggle",
    "radio": "toggle",
    "combobox": "select",
    "listbox": "select",
    "option": "option",
}
_TAKES_FOCUS = frozenset({"RootWebArea", "textbox", "searchbox", "checkbox", "switch", "radio", "combobox", "listbox", "option", "button", "link"})


class Element:
    """One element of a page. ``kind`` says how it behaves when filled in or clicked."""

    def __init__(
        self,
        role: str,
        name: str = "",
        *children: "Element",
        kind: str | None = None,
        value: str = "",
        checked: bool = False,
        visible: bool = True,
        disabled: bool = False,
        readonly: bool = False,
        on_click: Callable[["FakeBrowser", "FakePage"], None] | None = None,
        on_press: Callable[["FakeBrowser", "FakePage"], None] | None = None,
        on_input: Callable[["FakeBrowser", "FakePage"], None] | None = None,
        on_change: Callable[["FakeBrowser", "FakePage"], None] | None = None,
        frame: "Document | None" = None,
        quad: list[float] | None = None,
        script_error: str | None = None,
        **properties: Any,
    ) -> None:
        self.role = role
        self.name = name
        self.children = list(children)
        self.kind = kind or _KIND_OF_ROLE.get(role, "other")
        self.value = value
        self.checked = checked
        self.visible = visible
        self.disabled = disabled
        self.readonly = readonly
        self.on_click = on_click
        # Called when the mouse button goes down on the element, before it is released.
        self.on_press = on_press
        self.on_input = on_input
        # Called when a script sets the value of a select (the page's own ``change`` handler).
        self.on_change = on_change
        # What lies over the element (a banner, a dialog of the page), and
        # where: None for all of it, else ``(left, top, right, bottom)``.
        self.cover: Element | None = None
        self.cover_box: tuple[float, float, float, float] | None = None
        # The frame of the document the element is in.
        self.frame_id = ""
        # For an ``Iframe`` element: the document inside it.
        self.frame = frame
        self.content_frame: Frame | None = None
        self.fixed_quad = quad
        self.script_error = script_error
        self.properties = properties
        self.parent: Element | None = None
        self.backend_id = 0
        # In the document (False: taken out of it, though the browser still knows the node).
        self.connected = True
        # Gone for good: the browser no longer resolves its number.
        self.removed = False
        self.all_selected = False
        self.clicks = 0
        for child in self.children:
            child.parent = self

    def quad(self) -> list[float]:
        if self.fixed_quad is not None:
            return list(self.fixed_quad)
        # Rows of twenty, column after column, so that every element is in the window.
        column, row = divmod(self.backend_id - 1, 20)
        left, top = 10.0 + 150.0 * column, 30.0 * (row + 1)
        return [left, top, left + 100.0, top, left + 100.0, top + 20.0, left, top + 20.0]

    def box(self) -> tuple[float, float, float, float]:
        quad = self.quad()
        return min(quad[0::2]), min(quad[1::2]), max(quad[0::2]), max(quad[1::2])

    def point(self) -> tuple[float, float]:
        """Return the point a click on the element lands on."""
        quad = self.quad()
        return sum(quad[0::2]) / 4, sum(quad[1::2]) / 4

    def walk(self) -> list["Element"]:
        found = [self]
        for child in self.children:
            found.extend(child.walk())
        return found

    def __repr__(self) -> str:
        return f"<{self.role} {self.name!r} #{self.backend_id}>"


def el(role: str, name: str = "", *children: Element, **options: Any) -> Element:
    return Element(role, name, *children, **options)


class Document:
    """What a URL serves: a title and the elements of the page."""

    def __init__(
        self,
        title: str,
        *elements: Element,
        text: str | None = None,
        finishes: bool = True,
        dialog_on_load: tuple[str, str] | None = None,
        asks_before_unload: bool = False,
    ) -> None:
        self.title = title
        self.root = Element("RootWebArea", title, *elements)
        self.text = text
        # False: the load never ends (a page that keeps a request open).
        self.finishes = finishes
        # ``(kind, message)`` of a dialog the page opens while it loads.
        self.dialog_on_load = dialog_on_load
        # True: leaving the page for another address opens the browser's "leave site?" dialog first.
        self.asks_before_unload = asks_before_unload

    def inner_text(self) -> str:
        if self.text is not None:
            return self.text
        return " ".join(item.name for item in self.root.walk()[1:] if item.connected and not item.removed and item.name)


@dataclass
class Frame:
    frame_id: str
    loader_id: str
    url: str
    document: Document
    parent_id: str | None = None


@dataclass
class OpenDialog:
    kind: str
    message: str
    default_prompt: str = ""
    after: Callable[[bool], None] | None = None


@dataclass(frozen=True)
class Sent:
    """One command the browser was sent."""

    method: str
    params: dict[str, Any]
    session_id: str | None


class Gate:
    """Holds the commands that match it until it is released."""

    def __init__(self, method: str, match: dict[str, Any]) -> None:
        self.method = method
        self.match = match
        self.reached = asyncio.Event()
        self.opened = asyncio.Event()

    def matches(self, command: Sent) -> bool:
        return command.method == self.method and all(command.params.get(key) == value for key, value in self.match.items())

    def release(self) -> None:
        self.opened.set()


class FakePage:
    """One tab of the fake browser."""

    def __init__(self, target_id: str, url: str, title: str = "") -> None:
        self.target_id = target_id
        self.url = url
        self.title = title
        self.main_frame_id = f"frame-{target_id}"
        self.frames: dict[str, Frame] = {}
        self.elements: dict[int, Element] = {}
        self.objects: dict[str, Element] = {}
        self.history: list[str] = [url]
        self.history_at = 0
        self.sessions: set[str] = set()
        self.page_enabled = False
        self.focus_emulated = False
        self.dialog: OpenDialog | None = None
        self.unblocked = asyncio.Event()
        self.unblocked.set()
        self.focused: Element | None = None
        self.hovered: Element | None = None
        self.scroll_y = 0.0
        self.content_size = (1280.0, 2400.0)
        # The size of the window's visible part.
        self.viewport = (1280.0, 720.0)
        # What reached the page as input, in order.
        self.mouse: list[dict[str, Any]] = []
        self.keys: list[dict[str, Any]] = []
        self.inserted: list[str] = []
        self.scrolled_to: list[Element] = []
        self.screenshots: list[dict[str, Any]] = []
        self.handled_dialogs: list[dict[str, Any]] = []
        self.brought_to_front = 0
        # The tab whose page opened this one, when one did.
        self.opener_id: str | None = None
        self.text_polls = 0
        # Called on every poll for text, before it is answered.
        self.on_poll: Callable[["FakePage"], None] | None = None
        self.on_key: Callable[["FakeBrowser", "FakePage", dict[str, Any]], None] | None = None

    @property
    def main(self) -> Frame:
        return self.frames[self.main_frame_id]

    def find(self, name: str, role: str | None = None) -> Element:
        """Return the element of the documents loaded right now that has this name."""
        for element in self.elements.values():
            if element.name == name and (role is None or element.role == role) and element.role != "RootWebArea":
                return element
        raise AssertionError(f"the fake page has no element named {name!r}")

    def inputs(self) -> list[str]:
        """Return the kinds of mouse events the page got, for short assertions."""
        return [event["type"] for event in self.mouse]


class FakeConnection:
    """What the session holds instead of a ``CdpConnection``: same interface, no socket."""

    def __init__(self, browser: "FakeBrowser", url: str, open_timeout: float) -> None:
        self.browser = browser
        self.url = url
        self.open_timeout = open_timeout
        self._listeners: list[Any] = []
        self._close_listeners: list[Callable[[], None]] = []
        self._closed = False
        self.gone = asyncio.Event()
        self.close_calls = 0
        # The sessions whose waiting commands the session asked to fail, in order.
        self.failed_sessions: list[str] = []
        self._in_flight: dict[asyncio.Future[Any], str | None] = {}
        self._failed: set[asyncio.Future[Any]] = set()

    @property
    def closed(self) -> bool:
        return self._closed

    def on_event(self, listener: Any) -> Callable[[], None]:
        self._listeners.append(listener)

        def remove() -> None:
            if listener in self._listeners:
                self._listeners.remove(listener)

        return remove

    def on_close(self, listener: Callable[[], None]) -> None:
        if self._closed:
            listener()
        else:
            self._close_listeners.append(listener)

    def deliver(self, method: str, params: dict[str, Any], session_id: str | None) -> None:
        if self._closed:
            return
        for listener in list(self._listeners):
            listener(method, params, session_id)

    async def send(
        self,
        method: str,
        params: dict[str, Any] | None = None,
        *,
        session_id: str | None = None,
        timeout: float | None = 30.0,
    ) -> dict[str, Any]:
        if self._closed:
            raise CdpClosed("the connection to the browser is closed")
        command = Sent(method, dict(params or {}), session_id)
        self.browser.sent.append(command)
        work = asyncio.ensure_future(self.browser.handle(self, command))
        self._in_flight[work] = session_id
        try:
            return await asyncio.wait_for(work, timeout)
        except asyncio.TimeoutError as exc:
            raise CdpTimeout(f"{method} got no answer within {timeout:.0f}s") from exc
        except asyncio.CancelledError:
            if work in self._failed:
                raise CdpError(method, -32001, "Session with given id not found.") from None
            raise
        finally:
            self._in_flight.pop(work, None)
            self._failed.discard(work)

    def fail_session(self, session_id: str) -> None:
        """As ``CdpConnection.fail_session``: what still waits for an answer from the session fails."""
        self.failed_sessions.append(session_id)
        for work, owner in list(self._in_flight.items()):
            if owner == session_id and not work.done():
                self._failed.add(work)
                work.cancel()

    def end(self) -> None:
        """The connection is gone: what waits fails and the close listeners are told."""
        if self._closed:
            return
        self._closed = True
        self.gone.set()
        listeners, self._close_listeners = self._close_listeners, []
        for listener in listeners:
            listener()

    async def close(self) -> None:
        self.close_calls += 1
        self.end()


class FakeBrowser:
    """The browser: pages, what URLs serve, and answers to the commands of the session."""

    def __init__(self) -> None:
        self.pages: dict[str, FakePage] = {}
        self.sites: dict[str, Callable[[], Document]] = {}
        # url -> the error of a navigation that never commits (no document is loaded).
        self.unreachable: dict[str, str] = {}
        # url -> the error text of a navigation that ends on the browser's error page.
        self.error_pages: dict[str, str] = {}
        # Targets that are not pages, listed among the targets as a browser lists them.
        self.other_targets: list[dict[str, Any]] = [
            {"targetId": "worker-1", "type": "service_worker", "url": "https://shop.test/sw.js", "title": "sw"},
        ]
        self.sent: list[Sent] = []
        self.connections: list[FakeConnection] = []
        self.gates: list[Gate] = []
        # method -> an exception to raise instead of answering.
        self.failures: dict[str, Exception] = {}
        self.can_create_tabs = True
        self.close_requests = 0
        self.picture: bytes | None = _tiny_jpeg()
        self.window = {"windowId": 7, "bounds": {"left": 100, "top": 50, "width": 1280, "height": 800, "windowState": "normal"}}
        self._targets = itertools.count(1)
        self._loaders = itertools.count(1)
        self._sessions = itertools.count(1)
        self._objects = itertools.count(1)
        self._session_pages: dict[str, FakePage] = {}
        self.add_page("about:blank")

    # -- setting the scene ---------------------------------------------------

    def serve(self, url: str, build: Callable[[], Document]) -> None:
        """Make ``url`` load the document ``build`` returns, built anew for every load."""
        self.sites[url] = build

    def add_page(self, url: str, title: str = "", *, opener: FakePage | None = None) -> FakePage:
        """Open a tab, as the user or a link (of the page ``opener``) would; the session is told through an event."""
        page = FakePage(f"target-{next(self._targets)}", url, title)
        page.opener_id = opener.target_id if opener is not None else None
        self.pages[page.target_id] = page
        self._load(page, url)
        self._emit("Target.targetCreated", {"targetInfo": self.info(page)})
        return page

    def remove_page(self, page: FakePage) -> None:
        """Close a tab, as the user would."""
        if self.pages.pop(page.target_id, None) is None:
            return
        for session_id in list(page.sessions):
            self._session_pages.pop(session_id, None)
            self._emit("Target.detachedFromTarget", {"sessionId": session_id, "targetId": page.target_id})
        self._emit("Target.targetDestroyed", {"targetId": page.target_id})

    def page(self, target_id: str) -> FakePage:
        return self.pages[target_id]

    def hold(self, method: str, **match: Any) -> Gate:
        """Keep the commands of ``method`` whose parameters match from being answered until released."""
        gate = Gate(method, match)
        self.gates.append(gate)
        return gate

    def refuse(self, method: str, message: str = "Not supported", code: int = -32000) -> None:
        self.failures[method] = CdpError(method, code, message)

    def disconnect(self) -> None:
        """The browser went away (its window was closed): every connection ends."""
        for connection in list(self.connections):
            connection.end()

    # -- what was sent -------------------------------------------------------

    def mark(self) -> int:
        return len(self.sent)

    def since(self, mark: int = 0) -> list[Sent]:
        return self.sent[mark:]

    def methods(self, mark: int = 0) -> list[str]:
        return [command.method for command in self.sent[mark:]]

    def of(self, method: str, mark: int = 0) -> list[Sent]:
        return [command for command in self.sent[mark:] if command.method == method]

    def input_events(self, mark: int = 0) -> list[Sent]:
        return [command for command in self.sent[mark:] if command.method.startswith("Input.")]

    # -- events --------------------------------------------------------------

    def _emit(self, method: str, params: dict[str, Any], session_id: str | None = None) -> None:
        for connection in list(self.connections):
            connection.deliver(method, params, session_id)

    def emit_to_page(self, page: FakePage, method: str, params: dict[str, Any]) -> None:
        """Send an event of a page to the sessions attached to it, if they enabled the ``Page`` domain."""
        if not page.page_enabled:
            return
        for session_id in sorted(page.sessions):
            self._emit(method, params, session_id)

    def info(self, page: FakePage) -> dict[str, Any]:
        return {
            "targetId": page.target_id,
            "type": "page",
            "title": page.title,
            "url": page.url,
            **({"openerId": page.opener_id} if page.opener_id else {}),
            "attached": bool(page.sessions),
            "browserContextId": "context-1",
        }

    # -- documents -----------------------------------------------------------

    def _load(self, page: FakePage, url: str) -> Frame:
        build = self.sites.get(url.split("#", 1)[0])
        document = build() if build is not None else Document("")
        page.elements.clear()
        page.objects.clear()
        page.frames.clear()
        page.focused = None
        page.hovered = None
        counter = itertools.count(1)
        main = Frame(page.main_frame_id, f"loader-{next(self._loaders)}", url, document)
        page.frames[main.frame_id] = main
        self._number(page, main, counter)
        page.url = url
        page.title = document.title
        return main

    def _number(self, page: FakePage, frame: Frame, counter: Any) -> None:
        children = itertools.count(1)
        for element in frame.document.root.walk():
            element.backend_id = next(counter)
            element.frame_id = frame.frame_id
            page.elements[element.backend_id] = element
        for element in frame.document.root.walk():
            if element.role == "Iframe" and element.frame is not None:
                inner = Frame(
                    f"{frame.frame_id}-child{next(children)}", f"loader-{next(self._loaders)}", "about:srcdoc", element.frame, frame.frame_id
                )
                element.content_frame = inner
                page.frames[inner.frame_id] = inner
                self._number(page, inner, counter)

    def go(self, page: FakePage, url: str, *, push: bool = True) -> Frame:
        """Load ``url`` in a page, as a link, a script or the user would, with the events of a navigation."""
        self.emit_to_page(page, "Page.frameStartedLoading", {"frameId": page.main_frame_id})
        frame = self._load(page, url)
        if push:
            del page.history[page.history_at + 1 :]
            page.history.append(url)
            page.history_at = len(page.history) - 1
        self.emit_to_page(page, "Page.frameNavigated", {"frame": {"id": frame.frame_id, "loaderId": frame.loader_id, "url": url}})
        self._emit("Target.targetInfoChanged", {"targetInfo": self.info(page)})
        if frame.document.dialog_on_load is not None:
            kind, message = frame.document.dialog_on_load
            # The page is committed first; the dialog comes while it loads.
            asyncio.get_running_loop().call_soon(self.open_dialog, page, kind, message)
        elif frame.document.finishes:
            self.emit_to_page(page, "Page.loadEventFired", {"timestamp": 1.0})
            self.emit_to_page(page, "Page.frameStoppedLoading", {"frameId": page.main_frame_id})
        return frame

    def reload_frame(self, page: FakePage, iframe: Element, document: Document) -> Frame:
        """Load another document into an embedded frame; the page around it stays."""
        old = iframe.content_frame
        assert old is not None
        for element in old.document.root.walk():
            page.elements.pop(element.backend_id, None)
        first = min(item.backend_id for item in old.document.root.walk())
        iframe.frame = document
        inner = Frame(old.frame_id, f"loader-{next(self._loaders)}", "about:srcdoc", document, old.parent_id)
        iframe.content_frame = inner
        page.frames[inner.frame_id] = inner
        # The new document reuses the numbers of the one it replaces.
        self._number(page, inner, itertools.count(first))
        self.emit_to_page(
            page,
            "Page.frameNavigated",
            {"frame": {"id": inner.frame_id, "parentId": inner.parent_id, "loaderId": inner.loader_id, "url": inner.url}},
        )
        return inner

    def detach_frame(self, page: FakePage, iframe: Element) -> None:
        """Take an embedded frame out of the page."""
        inner = iframe.content_frame
        assert inner is not None
        page.frames.pop(inner.frame_id, None)
        for element in inner.document.root.walk():
            page.elements.pop(element.backend_id, None)
        iframe.content_frame = None
        self.emit_to_page(page, "Page.frameDetached", {"frameId": inner.frame_id, "reason": "remove"})

    # -- dialogs -------------------------------------------------------------

    def open_dialog(
        self, page: FakePage, kind: str, message: str, default_prompt: str = "", after: Callable[[bool], None] | None = None
    ) -> None:
        page.dialog = OpenDialog(kind, message, default_prompt, after)
        page.unblocked.clear()
        self.emit_to_page(
            page,
            "Page.javascriptDialogOpening",
            {"url": page.url, "message": message, "type": kind, "hasBrowserHandler": False, "defaultPrompt": default_prompt},
        )

    def close_dialog(self, page: FakePage, accept: bool, user_input: str = "") -> None:
        """Close the open dialog, as the user clicking one of its buttons would."""
        dialog = page.dialog
        assert dialog is not None
        page.dialog = None
        page.unblocked.set()
        self.emit_to_page(page, "Page.javascriptDialogClosed", {"result": accept, "userInput": user_input})
        if dialog.after is not None:
            dialog.after(accept)

    # -- commands ------------------------------------------------------------

    async def _wait(self, connection: FakeConnection, event: asyncio.Event) -> None:
        waiter = asyncio.ensure_future(event.wait())
        gone = asyncio.ensure_future(connection.gone.wait())
        try:
            await asyncio.wait({waiter, gone}, return_when=asyncio.FIRST_COMPLETED)
        finally:
            waiter.cancel()
            gone.cancel()
        if connection.closed:
            raise CdpClosed("the connection to the browser is closed")

    async def handle(self, connection: FakeConnection, command: Sent) -> dict[str, Any]:
        for gate in self.gates:
            if gate.matches(command) and not gate.opened.is_set():
                gate.reached.set()
                await self._wait(connection, gate.opened)
        failure = self.failures.get(command.method)
        if failure is not None:
            raise failure
        page: FakePage | None = None
        if command.session_id is not None:
            page = self._session_pages.get(command.session_id)
            if page is None:
                raise CdpError(command.method, -32001, "Session with given id not found.")
        handles_dialog = command.method == "Page.handleJavaScriptDialog"
        if page is not None and page.dialog is not None and not handles_dialog:
            await self._wait(connection, page.unblocked)
        handler = getattr(self, "_do_" + command.method.replace(".", "_"), None)
        if handler is None:
            raise AssertionError(f"the fake browser was sent a command it does not model: {command.method}")
        result = handler(command, page)
        if page is not None and page.dialog is not None and not handles_dialog:
            # The command opened a dialog: it is answered once the dialog has been handled.
            await self._wait(connection, page.unblocked)
        return result

    def _element_of(self, command: Sent, page: FakePage) -> Element:
        element = page.objects.get(str(command.params.get("objectId")))
        if element is None:
            raise CdpError(command.method, -32000, "Could not find object with given id")
        return element

    def _handle_on(self, page: FakePage, element: Element) -> str:
        object_id = f"object-{next(self._objects)}"
        page.objects[object_id] = element
        return object_id

    # Target

    def _do_Target_setDiscoverTargets(self, command: Sent, page: None) -> dict[str, Any]:
        assert command.params == {"discover": True}
        for known in list(self.pages.values()):
            self._emit("Target.targetCreated", {"targetInfo": self.info(known)})
        for other in self.other_targets:
            self._emit("Target.targetCreated", {"targetInfo": other})
        return {}

    def _do_Target_getTargets(self, command: Sent, page: None) -> dict[str, Any]:
        return {"targetInfos": [*(self.info(known) for known in self.pages.values()), *self.other_targets]}

    def _do_Target_attachToTarget(self, command: Sent, page: None) -> dict[str, Any]:
        assert command.params.get("flatten") is True, "the session speaks to pages through flat sessions"
        target = self.pages.get(str(command.params.get("targetId")))
        if target is None:
            raise CdpError(command.method, -32602, "No target with given id found")
        session_id = f"session-{next(self._sessions)}"
        target.sessions.add(session_id)
        self._session_pages[session_id] = target
        return {"sessionId": session_id}

    def _do_Target_createTarget(self, command: Sent, page: None) -> dict[str, Any]:
        if not self.can_create_tabs:
            raise CdpError(command.method, -32000, "Not supported")
        created = self.add_page(str(command.params.get("url") or "about:blank"))
        return {"targetId": created.target_id}

    def _do_Target_closeTarget(self, command: Sent, page: None) -> dict[str, Any]:
        target = self.pages.get(str(command.params.get("targetId")))
        if target is None:
            raise CdpError(command.method, -32602, "No target with given id found")
        self.remove_page(target)
        return {"success": True}

    # Browser

    def _do_Browser_getWindowForTarget(self, command: Sent, page: None) -> dict[str, Any]:
        if str(command.params.get("targetId")) not in self.pages:
            raise CdpError(command.method, -32602, "No target with given id found")
        return dict(self.window)

    def _do_Browser_close(self, command: Sent, page: None) -> dict[str, Any]:
        self.close_requests += 1
        return {}

    # Page

    def _do_Page_enable(self, command: Sent, page: FakePage) -> dict[str, Any]:
        page.page_enabled = True
        return {}

    def _do_Emulation_setFocusEmulationEnabled(self, command: Sent, page: FakePage) -> dict[str, Any]:
        page.focus_emulated = bool(command.params.get("enabled"))
        return {}

    def _do_Page_getFrameTree(self, command: Sent, page: FakePage) -> dict[str, Any]:
        def tree(frame: Frame) -> dict[str, Any]:
            item: dict[str, Any] = {"frame": {"id": frame.frame_id, "loaderId": frame.loader_id, "url": frame.url}}
            children = [tree(child) for child in page.frames.values() if child.parent_id == frame.frame_id]
            if children:
                item["childFrames"] = children
            return item

        return {"frameTree": tree(page.main)}

    def _do_Page_navigate(self, command: Sent, page: FakePage) -> dict[str, Any]:
        url = str(command.params["url"])
        if url in self.unreachable:
            return {"frameId": page.main_frame_id, "errorText": self.unreachable[url]}
        if "#" in url and url.split("#", 1)[0] == page.url.split("#", 1)[0]:
            # Only the fragment changed: the document stays and nothing is loaded.
            page.url = url
            page.main.url = url
            return {"frameId": page.main_frame_id}
        result: dict[str, Any] = {"frameId": page.main_frame_id}

        def leave(accepted: bool = True) -> None:
            if not accepted:
                result["errorText"] = "net::ERR_ABORTED"
                return
            result["loaderId"] = self.go(page, url).loader_id
            if url in self.error_pages:
                result["errorText"] = self.error_pages[url]

        if page.main.document.asks_before_unload:
            # The command is answered once the dialog has been handled, with what the answer led to.
            self.open_dialog(page, "beforeunload", "", after=leave)
        else:
            leave()
        return result

    def _do_Page_getNavigationHistory(self, command: Sent, page: FakePage) -> dict[str, Any]:
        entries = [{"id": index + 1, "url": url, "title": ""} for index, url in enumerate(page.history)]
        return {"currentIndex": page.history_at, "entries": entries}

    def _do_Page_navigateToHistoryEntry(self, command: Sent, page: FakePage) -> dict[str, Any]:
        index = int(command.params["entryId"]) - 1
        if not 0 <= index < len(page.history):
            raise CdpError(command.method, -32000, "No entry with passed id")
        page.history_at = index
        self.go(page, page.history[index], push=False)
        return {}

    def _do_Page_reload(self, command: Sent, page: FakePage) -> dict[str, Any]:
        self.go(page, page.url, push=False)
        return {}

    def _do_Page_bringToFront(self, command: Sent, page: FakePage) -> dict[str, Any]:
        page.brought_to_front += 1
        return {}

    def _do_Page_captureScreenshot(self, command: Sent, page: FakePage) -> dict[str, Any]:
        page.screenshots.append(command.params)
        if self.picture is None:
            return {}
        return {"data": base64.b64encode(self.picture).decode("ascii")}

    def _do_Page_getLayoutMetrics(self, command: Sent, page: FakePage) -> dict[str, Any]:
        width, height = page.content_size
        seen_width, seen_height = page.viewport
        return {
            "cssLayoutViewport": {"pageX": 0, "pageY": page.scroll_y, "clientWidth": seen_width, "clientHeight": seen_height},
            "cssVisualViewport": {"pageX": 0, "pageY": page.scroll_y, "clientWidth": seen_width, "clientHeight": seen_height},
            "cssContentSize": {"x": 0, "y": 0, "width": width, "height": height},
        }

    def _do_Page_handleJavaScriptDialog(self, command: Sent, page: FakePage) -> dict[str, Any]:
        if page.dialog is None:
            raise CdpError(command.method, -32602, "No dialog is showing")
        page.handled_dialogs.append(command.params)
        self.close_dialog(page, bool(command.params.get("accept")), str(command.params.get("promptText") or ""))
        return {}

    # Accessibility and DOM

    def _do_Accessibility_getFullAXTree(self, command: Sent, page: FakePage) -> dict[str, Any]:
        frame_id = command.params.get("frameId") or page.main_frame_id
        frame = page.frames.get(str(frame_id))
        if frame is None:
            raise CdpError(command.method, -32602, "Frame with the given frameId is not found.")
        nodes: list[dict[str, Any]] = []

        def walk(element: Element) -> str:
            node_id = str(len(nodes) + 1)
            properties = dict(element.properties)
            if element.role in _TAKES_FOCUS:
                properties.setdefault("focusable", True)
            if element.kind == "toggle":
                properties["checked"] = "true" if element.checked else "false"
            if element.disabled:
                properties["disabled"] = True
            payload: dict[str, Any] = {
                "nodeId": node_id,
                "ignored": False,
                "role": {"type": "role", "value": element.role},
                "name": {"type": "computedString", "value": element.name},
                "backendDOMNodeId": element.backend_id,
                "properties": [{"name": key, "value": {"type": "computed", "value": item}} for key, item in properties.items()],
            }
            if element.kind in ("text", "select") and element.value:
                payload["value"] = {"type": "string", "value": element.value}
            nodes.append(payload)
            payload["childIds"] = [walk(child) for child in element.children if child.connected and not child.removed]
            return node_id

        walk(frame.document.root)
        return {"nodes": nodes}

    def _do_DOM_describeNode(self, command: Sent, page: FakePage) -> dict[str, Any]:
        element = page.elements.get(int(command.params.get("backendNodeId") or 0))
        if element is None or element.removed:
            raise CdpError(command.method, -32000, "Could not find node with given id")
        node: dict[str, Any] = {"backendNodeId": element.backend_id, "nodeName": element.role.upper()}
        if element.content_frame is not None:
            node["frameId"] = element.content_frame.frame_id
        return {"node": node}

    def _do_DOM_resolveNode(self, command: Sent, page: FakePage) -> dict[str, Any]:
        element = page.elements.get(int(command.params.get("backendNodeId") or 0))
        if element is None or element.removed:
            raise CdpError(command.method, -32000, "No node with given id found")
        return {"object": {"type": "object", "subtype": "node", "objectId": self._handle_on(page, element)}}

    def _do_DOM_scrollIntoViewIfNeeded(self, command: Sent, page: FakePage) -> dict[str, Any]:
        element = self._element_of(command, page)
        if not element.visible:
            raise CdpError(command.method, -32000, "Node does not have a layout object")
        page.scrolled_to.append(element)
        return {}

    def _do_DOM_getNodeForLocation(self, command: Sent, page: FakePage) -> dict[str, Any]:
        assert set(command.params) == {"x", "y"}, "pointer-events: none must keep its meaning"
        assert isinstance(command.params["x"], int) and isinstance(command.params["y"], int)
        element = self._at(page, command.params["x"], command.params["y"])
        if element is None:
            raise CdpError(command.method, -32000, "No node found at given location")
        return {"backendNodeId": element.backend_id, "frameId": element.frame_id}

    def _do_DOM_getContentQuads(self, command: Sent, page: FakePage) -> dict[str, Any]:
        element = self._element_of(command, page)
        if not element.visible:
            raise CdpError(command.method, -32000, "Could not compute content quads.")
        return {"quads": [element.quad()]}

    # Runtime

    def _do_Runtime_callFunctionOn(self, command: Sent, page: FakePage) -> dict[str, Any]:
        element = self._element_of(command, page)
        function = command.params.get("functionDeclaration")
        if function == _REACHES:
            assert command.params.get("returnByValue") is True
            (argument,) = command.params.get("arguments") or ()
            hit = page.objects.get(str(argument.get("objectId")))
            if hit is None:
                raise CdpError(command.method, -32000, "Could not find object with given id")
            if hit.frame_id != element.frame_id:
                raise CdpError(command.method, -32000, "Argument should belong to the same JavaScript world as target object")
            related = hit in element.walk() or element in hit.walk()
            return {"result": {"type": "boolean", "value": True} if related else {"type": "string", "value": f'{hit.role} "{hit.name}"'}}
        arguments = [item.get("value") for item in command.params.get("arguments") or ()]
        if function == _ELEMENT_OF_NODE:
            assert command.params.get("returnByValue") is False
            if not element.connected:
                return {"result": {"type": "object", "subtype": "null", "value": None}}
            return {"result": {"type": "object", "subtype": "node", "objectId": self._handle_on(page, element)}}
        assert command.params.get("returnByValue") is True
        if element.script_error is not None:
            return {
                "result": {"type": "object", "subtype": "error"},
                "exceptionDetails": {"text": "Uncaught", "exception": {"description": element.script_error}},
            }
        select = element.parent if element.kind == "option" else element
        before = select.value if select is not None else None
        if function == _SELECT_OPTION:
            assert arguments == []
            chosen = self._select_option(element)
            self._changed(page, select, before)
            return {"result": {"type": "boolean", "value": chosen}}
        if function == _PREPARE_FILL:
            assert len(arguments) == 1 and isinstance(arguments[0], str)
            plan = self._prepare_fill(page, element, arguments[0])
            self._changed(page, select, before)
            return {"result": {"type": "object", "value": plan}}
        raise AssertionError(f"the fake browser was sent a script it does not know: {str(function)[:60]!r}")

    def _changed(self, page: FakePage, select: Element | None, before: Any) -> None:
        """Run the ``change`` handler of a select whose value a script has just set."""
        if select is not None and select.kind == "select" and select.value != before and select.on_change is not None:
            select.on_change(self, page)

    @staticmethod
    def _select_option(element: Element) -> bool:
        select = element.parent
        if element.kind != "option" or select is None or select.kind != "select" or select.disabled or element.disabled:
            return False
        select.value = element.name
        return True

    @staticmethod
    def _prepare_fill(page: FakePage, element: Element, value: str) -> dict[str, Any]:
        target = element
        if element.kind == "option" and element.parent is not None and element.parent.kind == "select":
            target = element.parent
        if target.disabled:
            return {"kind": "error", "message": "the element is disabled"}
        if target.kind == "select":
            options = [child.name for child in target.children if child.kind == "option"]
            if value not in options:
                return {"kind": "error", "message": f'it has no option "{value}"; its options are: {", ".join(options)}'}
            target.value = value
            return {"kind": "done"}
        if target.kind == "toggle":
            if value not in ("true", "false"):
                return {"kind": "error", "message": 'the value for a checkbox, toggle or radio button is "true" or "false"'}
            return {"kind": "done" if target.checked == (value == "true") else "click"}
        if target.kind == "text":
            if target.readonly:
                return {"kind": "error", "message": "the element is read-only"}
            page.focused = target
            target.all_selected = True
            return {"kind": "type"}
        return {"kind": "error", "message": f"it is not an element that can be filled in ({target.role})"}

    def _do_Runtime_evaluate(self, command: Sent, page: FakePage) -> dict[str, Any]:
        expression = str(command.params.get("expression"))
        assert command.params.get("returnByValue") is True
        if expression == _DOM_QUIET:
            assert command.params.get("awaitPromise") is True
            return {"result": {"type": "boolean", "value": True}}
        if expression.startswith(_FIND_TEXT + "(") and expression.endswith(")"):
            texts = json.loads(expression[len(_FIND_TEXT) + 1 : -1])
            page.text_polls += 1
            if page.on_poll is not None:
                page.on_poll(page)
            shown = page.main.document.inner_text()
            found = next((text for text in texts if text in shown), None)
            if found is None:
                return {"result": {"type": "object", "subtype": "null", "value": None}}
            return {"result": {"type": "string", "value": found}}
        raise AssertionError(f"the fake browser was sent an expression it does not know: {expression[:60]!r}")

    # Input

    def _at(self, page: FakePage, x: Any, y: Any) -> Element | None:
        """Return what is at a point: the smallest element whose box holds it, or what lies over that there."""
        found: Element | None = None
        smallest = float("inf")
        for element in page.elements.values():
            if not (element.visible and element.connected and not element.removed) or element.role == "RootWebArea":
                continue
            left, top, right, bottom = element.box()
            area = (right - left) * (bottom - top)
            if left <= x <= right and top <= y <= bottom and area < smallest:
                found, smallest = element, area
        if found is not None and found.cover is not None:
            left, top, right, bottom = found.cover_box or found.box()
            if left <= x <= right and top <= y <= bottom:
                return found.cover
        return found

    def _do_Input_dispatchMouseEvent(self, command: Sent, page: FakePage) -> dict[str, Any]:
        event = command.params
        page.mouse.append(event)
        element = self._at(page, event.get("x"), event.get("y"))
        if event.get("type") == "mouseMoved":
            page.hovered = element
        elif event.get("type") == "mousePressed" and element is not None and element.on_press is not None:
            element.on_press(self, page)
        elif event.get("type") == "mouseReleased" and element is not None and not element.disabled:
            element.clicks += 1
            if element.kind == "toggle":
                element.checked = not element.checked
            if element.kind == "text":
                page.focused = element
            if element.on_click is not None:
                element.on_click(self, page)
        return {}

    def _do_Input_dispatchKeyEvent(self, command: Sent, page: FakePage) -> dict[str, Any]:
        event = command.params
        page.keys.append(event)
        if event.get("type") in ("keyDown", "rawKeyDown"):
            focused = page.focused
            if focused is not None and event.get("key") in ("Delete", "Backspace") and focused.all_selected:
                focused.value = ""
                focused.all_selected = False
            if page.on_key is not None:
                page.on_key(self, page, event)
        return {}

    def _do_Input_insertText(self, command: Sent, page: FakePage) -> dict[str, Any]:
        text = str(command.params.get("text"))
        page.inserted.append(text)
        focused = page.focused
        if focused is not None:
            focused.value = text if focused.all_selected else focused.value + text
            focused.all_selected = False
            if focused.on_input is not None:
                focused.on_input(self, page)
        return {}


# ---------------------------------------------------------------------------
# A small shop, the site most tests browse
# ---------------------------------------------------------------------------

SHOP = "http://shop.test/"
ORDER = "http://shop.test/order"
THANKS = "http://shop.test/thanks"
TERMS = "http://shop.test/terms"
CHECKOUT = "http://shop.test/checkout"
SLOW = "http://shop.test/slow"


def order_form() -> Document:
    return Document(
        "Order form",
        el("heading", "Order a part", level=1),
        el("textbox", "Your name"),
        el("textbox", "Email", value="old@example.com"),
        el("checkbox", "Gift wrap"),
        el("checkbox", "Newsletter", checked=True),
        el("combobox", "Product", el("option", "Brass Hinge"), el("option", "Steel Bolt"), value="Brass Hinge"),
        el("button", "Send", on_click=lambda browser, page: browser.go(page, THANKS)),
        el("button", "Save draft", on_click=lambda browser, page: browser.open_dialog(page, "alert", "Draft saved")),
        el("link", "Terms", on_click=lambda browser, page: browser.add_page(TERMS, opener=page)),
        el("button", "Hidden", visible=False),
    )


def thanks() -> Document:
    # Fewer elements than the order form, so most of its DOM node numbers
    # stand for something else here and some for nothing.
    return Document(
        "Thanks",
        el("heading", "Thank you", level=1),
        el("button", "Print"),
        el("link", "Back to the shop", on_click=lambda browser, page: browser.go(page, SHOP)),
        text="Thank you. Confirmation code: QX-7",
    )


def checkout() -> Document:
    return Document(
        "Checkout",
        el("button", "Pay"),
        el("Iframe", "", frame=Document("Card", el("textbox", "Card number"), el("button", "Confirm card"))),
    )


def terms() -> Document:
    return Document(
        "Terms",
        el("heading", "Terms of sale", level=1),
        # The wrapper is only listed in a verbose snapshot.
        el("generic", "", el("StaticText", "Returns within 30 days.")),
    )


def serve_shop(browser: "FakeBrowser") -> None:
    browser.serve(SHOP, lambda: Document("Shop", el("link", "Order"), el("link", "Terms")))
    browser.serve(ORDER, order_form)
    browser.serve(THANKS, thanks)
    browser.serve(TERMS, terms)
    browser.serve(CHECKOUT, checkout)
    browser.serve(SLOW, lambda: Document("Slow", el("heading", "Loading", level=1), finishes=False))


def _tiny_jpeg() -> bytes:
    from PIL import Image

    buffer = BytesIO()
    Image.new("RGB", (48, 27), "teal").save(buffer, format="JPEG")
    return buffer.getvalue()


def _refuse(*args: Any, **kwargs: Any) -> Any:
    raise AssertionError("a unit test reached a real browser process")


class _NoRealSleep:
    """Stands in for ``asyncio`` inside the session: its sleeps only give way to other tasks."""

    def __init__(self) -> None:
        self.slept: list[float] = []

    async def sleep(self, seconds: float, result: Any = None) -> Any:
        self.slept.append(seconds)
        await asyncio.sleep(0)
        return result

    def __getattr__(self, name: str) -> Any:
        return getattr(asyncio, name)


OWN_ENDPOINT = "ws://127.0.0.1:9222/devtools/browser/started-for-the-work"
THEIR_ENDPOINT = "ws://127.0.0.1:9333/devtools/browser/the-users-own"
CHROME = BrowserInstall("chrome", "Google Chrome", "/fake/chrome", "/fake/everyday-profile")


@dataclass
class BrowserWorld:
    """Everything around a ``BrowserSession``: the browser, the launcher, the settings.

    Building one replaces ``launcher.pick_browser`` / ``launch`` / ``attach`` /
    ``close`` / ``display_available`` and ``CdpConnection.connect``, and makes
    the functions that start or kill processes raise.
    """

    monkeypatch: Any
    root: Path
    browser: FakeBrowser = field(default_factory=FakeBrowser)
    # The browser found on this computer; None for none.
    install: BrowserInstall | None = CHROME
    display: bool = True
    # Set to keep the start of the browser, or the connection to it, from
    # finishing until the test sets the event.
    launch_gate: asyncio.Event | None = None
    connect_gate: asyncio.Event | None = None
    launch_error: Exception | None = None
    attach_error: Exception | None = None
    connect_error: Exception | None = None
    close_error: Exception | None = None
    picked: list[Any] = field(default_factory=list)
    launched: list[tuple[BrowserInstall, str, bool]] = field(default_factory=list)
    attached: list[BrowserInstall] = field(default_factory=list)
    closed: list[tuple[RunningBrowser, Any]] = field(default_factory=list)
    connected: list[tuple[str, float]] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.store = BrowserSettingsStore(self.root / "nekoclaw")
        self.clock = _NoRealSleep()
        patch = self.monkeypatch
        patch.setattr(launcher, "pick_browser", self._pick)
        patch.setattr(launcher, "launch", self._launch)
        patch.setattr(launcher, "attach", self._attach)
        patch.setattr(launcher, "close", self._close)
        patch.setattr(launcher, "display_available", lambda platform=None: self.display)
        for name in ("_start", "kill_tree", "browser_process_on", "running_endpoint", "_port_open"):
            patch.setattr(launcher, name, _refuse)
        patch.setattr(CdpConnection, "connect", staticmethod(self._connect))
        patch.setattr(session_module, "asyncio", self.clock)

    def settings(self, **values: Any) -> None:
        self.store.save(BrowserSettings(**values))

    def session(self, **options: Any) -> BrowserSession:
        return BrowserSession(settings=self.store, **options)

    @property
    def connection(self) -> FakeConnection:
        return self.browser.connections[-1]

    def page_of(self, page: Any) -> FakePage:
        """Return the fake page behind a page of the session."""
        return self.browser.pages[page.target_id]

    def _pick(self, executable: str | None = None, platform: str | None = None) -> BrowserInstall:
        self.picked.append(executable)
        if self.install is None:
            raise launcher.NoBrowserError("no Chromium-based browser (Chrome, Edge, Brave, Chromium) was found on this computer")
        return self.install

    async def _launch(self, install: BrowserInstall, user_data_dir: str, *, headless: bool = False, extra: tuple[str, ...] = ()) -> RunningBrowser:
        self.launched.append((install, user_data_dir, headless))
        if self.launch_gate is not None:
            await self.launch_gate.wait()
        if self.launch_error is not None:
            raise self.launch_error
        return RunningBrowser(install=install, endpoint=OWN_ENDPOINT, process=None, owned=True, user_data_dir=user_data_dir)

    async def _attach(self, install: BrowserInstall) -> RunningBrowser:
        self.attached.append(install)
        if self.attach_error is not None:
            raise self.attach_error
        return RunningBrowser(install=install, endpoint=THEIR_ENDPOINT, process=None, owned=False, user_data_dir=install.user_data_dir)

    async def _close(self, browser: RunningBrowser, connection: Any = None) -> None:
        self.closed.append((browser, connection))
        if self.close_error is not None:
            raise self.close_error

    async def _connect(self, url: str, *, open_timeout: float = 10.0) -> FakeConnection:
        self.connected.append((url, open_timeout))
        if self.connect_gate is not None:
            await self.connect_gate.wait()
        if self.connect_error is not None:
            raise self.connect_error
        connection = FakeConnection(self.browser, url, open_timeout)
        self.browser.connections.append(connection)
        return connection
