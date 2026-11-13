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

"""The pages of the browser and the actions on a page.

``BrowserSession`` owns one connection to one browser. It is started on the
first call that needs it and started again when the browser went away (the
user closed its window). It keeps the list of open pages up to date from the
browser's own events, knows which page is selected, and carries out one
action at a time on it; ``tools.py`` queues the calls.

How elements are named: a snapshot (``snapshot.py``) gives every element a
uid. An action takes a uid, finds the DOM node it stands for and acts on it.
A uid of an older document is refused instead of being acted on: after a
navigation, or when the element is gone, the caller is told to take a new
snapshot. The user may use the same browser meanwhile, so nothing is assumed
to be as the last snapshot showed it.

Input is sent the way a user's would arrive (``Input.dispatchMouseEvent``,
``Input.dispatchKeyEvent``, ``Input.insertText``), so pages see ordinary
events. A JavaScript dialog (``alert``, ``confirm``) blocks the page and
every command to it; an action that opens one returns at once and says so,
and nothing else works on that page until the dialog is handled.

Nothing here depends on the browser being able to open tabs
(``Target.createTarget``): the window it starts with is enough, which is what
a window provided by the desktop shell would be.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from nekoclaw.builtin.browser import launcher
from nekoclaw.builtin.browser.cdp import SESSION_GONE_CODE, CdpClosed, CdpConnection, CdpError, CdpTimeout
from nekoclaw.builtin.browser.keys import KeyDefinition, UnknownKey, modifier_mask, parse_key, typed_text
from nekoclaw.builtin.browser.settings import MODE_ATTACH, BrowserSettingsStore
from nekoclaw.builtin.browser.snapshot import (
    Snapshot,
    SnapshotNode,
    assign_uids,
    build_ax_tree,
    iframe_nodes,
    serialize_tree,
)

logger = logging.getLogger(__name__)

NAVIGATION_TIMEOUT_SECONDS = 20.0
ACTION_NAVIGATION_TIMEOUT_SECONDS = 10.0
WAIT_FOR_DEFAULT_SECONDS = 10.0
SNAPSHOT_TIMEOUT_SECONDS = 10.0
# Connecting to a browser the user is using waits for them to allow it there.
ATTACH_CONNECT_TIMEOUT_SECONDS = 60.0
MAX_IFRAME_DEPTH = 3
MAX_IFRAMES = 12
FULL_PAGE_MAX_HEIGHT = 6000
ALLOWED_URL_SCHEMES = ("http://", "https://")
# Pages of the browser itself (its settings, its sign-in and sync dialogs, its
# developer tools) and of its extensions. They are not pages of the web: none
# of them is listed to the model or driven.
BROWSER_OWN_SCHEMES = (
    "devtools://",
    "chrome-extension://",
    "extension://",
    "chrome://",
    "chrome-untrusted://",
    "chrome-search://",
    "edge://",
    "brave://",
    "opera://",
    "vivaldi://",
)

# Returns the debugging endpoint of a window the desktop shell provides when
# no browser is installed, or None when it provides none.
FallbackEndpoint = Callable[[], Awaitable["str | None"]]


class BrowserError(Exception):
    """An action that cannot be carried out; the text is for the model."""


class PageClosed(BrowserError):
    """The page an action was meant for is no longer open."""

    def __init__(self) -> None:
        super().__init__(
            "That page is no longer open (it was closed, or it crashed). Call browser_list_pages to see the open pages."
        )


@dataclass(slots=True)
class Dialog:
    kind: str
    message: str
    default_prompt: str = ""


@dataclass(slots=True)
class Page:
    """One open page (a tab)."""

    # The number the model knows the page by. It stays with the page.
    id: int
    target_id: str
    url: str = ""
    title: str = ""
    session_id: str | None = None
    snapshot: Snapshot | None = None
    # node (``<loader id>_<backend node id>``) -> the uid it was given.
    known_uids: dict[str, str] = field(default_factory=dict)
    # frame id -> the id of the document loaded in it right now.
    frame_loaders: dict[str, str] = field(default_factory=dict)
    main_frame_id: str = ""
    dialog: Dialog | None = None
    loading: bool = False
    navigations: int = 0
    load_event: asyncio.Event = field(default_factory=asyncio.Event)
    dialog_event: asyncio.Event = field(default_factory=asyncio.Event)
    # Opened for the work, or by a page that was; not a tab the user had open.
    own: bool = False


_ELEMENT_OF_NODE = """function() {
  let node = this;
  if (node.nodeType === Node.TEXT_NODE) {
    node = node.parentElement ?? (node.parentNode && node.parentNode.host) ?? null;
  }
  return node && node.isConnected ? node : null;
}"""

_SELECT_OPTION = """function() {
  if (!(this instanceof HTMLOptionElement)) return false;
  const select = this.closest('select');
  if (!select || select.multiple || select.disabled || this.disabled) return false;
  const group = this.parentElement;
  if (group instanceof HTMLOptGroupElement && group.disabled) return false;
  if (select.value !== this.value) {
    select.value = this.value;
    select.dispatchEvent(new Event('input', {bubbles: true}));
    select.dispatchEvent(new Event('change', {bubbles: true}));
  }
  return true;
}"""

_PREPARE_FILL = """function(value) {
  let el = this;
  if (el instanceof HTMLOptionElement && el.closest('select')) el = el.closest('select');
  const tag = el.tagName;
  const type = (el.getAttribute('type') || '').toLowerCase();
  const role = el.getAttribute('role');
  if (el.disabled) return {kind: 'error', message: 'the element is disabled'};
  if (tag === 'SELECT') {
    const options = Array.from(el.options);
    const wanted = options.find(o => o.value === value) || options.find(o => o.label === value)
      || options.find(o => o.text.trim() === value.trim());
    if (!wanted) {
      return {kind: 'error', message: 'it has no option "' + value + '"; its options are: '
        + options.map(o => o.text.trim()).slice(0, 30).join(', ')};
    }
    if (el.value !== wanted.value) {
      el.value = wanted.value;
      el.dispatchEvent(new Event('input', {bubbles: true}));
      el.dispatchEvent(new Event('change', {bubbles: true}));
    }
    return {kind: 'done'};
  }
  const toggle = (tag === 'INPUT' && (type === 'checkbox' || type === 'radio'))
    || role === 'checkbox' || role === 'switch' || role === 'radio';
  if (toggle) {
    if (value !== 'true' && value !== 'false') {
      return {kind: 'error', message: 'the value for a checkbox, toggle or radio button is "true" or "false"'};
    }
    const checked = tag === 'INPUT' ? el.checked : el.getAttribute('aria-checked') === 'true';
    return {kind: checked === (value === 'true') ? 'done' : 'click'};
  }
  if (tag === 'INPUT' && type === 'file') return {kind: 'error', message: 'a file input cannot be filled in'};
  if (tag === 'INPUT' && ['date', 'time', 'datetime-local', 'month', 'week', 'color', 'range'].includes(type)) {
    const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set;
    setter.call(el, value);
    el.dispatchEvent(new Event('input', {bubbles: true}));
    el.dispatchEvent(new Event('change', {bubbles: true}));
    return {kind: 'done'};
  }
  if (tag === 'INPUT' || tag === 'TEXTAREA') {
    if (el.readOnly) return {kind: 'error', message: 'the element is read-only'};
    el.focus();
    if (typeof el.select === 'function') el.select();
    return {kind: 'type'};
  }
  if (el.isContentEditable) {
    el.focus();
    const range = document.createRange();
    range.selectNodeContents(el);
    const selection = window.getSelection();
    selection.removeAllRanges();
    selection.addRange(range);
    return {kind: 'type'};
  }
  return {kind: 'error', message: 'it is not an element that can be filled in (' + tag.toLowerCase() + ')'};
}"""

_DOM_QUIET = """new Promise(resolve => {
  let timer;
  const done = () => { observer.disconnect(); resolve(true); };
  const observer = new MutationObserver(() => { clearTimeout(timer); timer = setTimeout(done, 100); });
  observer.observe(document, {childList: true, subtree: true, attributes: true, characterData: true});
  timer = setTimeout(done, 100);
  setTimeout(done, 1500);
})"""

_FIND_TEXT = """(texts => {
  const text = document.body ? document.body.innerText : '';
  return texts.find(item => text.includes(item)) ?? null;
})"""


def _drop_outcome(task: "asyncio.Future[Any]") -> None:
    if not task.cancelled():
        task.exception()


class BrowserSession:
    """One browser and its pages. See the module docstring."""

    def __init__(
        self,
        *,
        settings: BrowserSettingsStore | None = None,
        fallback_endpoint: FallbackEndpoint | None = None,
    ) -> None:
        self._settings = settings or BrowserSettingsStore()
        self._fallback_endpoint = fallback_endpoint
        self._connection: CdpConnection | None = None
        self._browser: launcher.RunningBrowser | None = None
        self._pages: dict[str, Page] = {}  # by target id, in the order they appeared
        self._selected: str | None = None
        self._next_page_id = 1
        self._next_snapshot_id = 1
        self._can_create_tabs = True
        # The browser is the one the user is using (attach mode): tabs that
        # were not opened for the work are theirs.
        self._user_browser = False
        self._window: dict[str, Any] | None = None

    # -- connection -------------------------------------------------------

    @property
    def connected(self) -> bool:
        return self._connection is not None and not self._connection.closed

    @property
    def browser_name(self) -> str:
        install = self._browser.install if self._browser is not None else None
        return install.name if install is not None else "browser"

    async def start(self) -> None:
        """Connect to a browser, starting one when needed. Does nothing while connected."""
        if self.connected:
            return
        await self._drop_connection()
        settings = await asyncio.to_thread(self._settings.load)
        attach = settings.mode == MODE_ATTACH
        try:
            install = await asyncio.to_thread(launcher.pick_browser, settings.executable)
        except launcher.NoBrowserError as exc:
            endpoint = await self._fallback_endpoint() if self._fallback_endpoint is not None else None
            if not endpoint:
                raise BrowserError(
                    f"There is no usable browser: {exc}. Install one (Chrome or Edge, for instance) and try again."
                ) from exc
            browser = launcher.RunningBrowser(install=None, endpoint=endpoint, owned=False)
        else:
            try:
                if attach:
                    browser = await launcher.attach(install)
                else:
                    profile = await asyncio.to_thread(self._settings.profile_dir)
                    # Without a display a window cannot be shown; the browser
                    # is then started without one whatever the settings say.
                    headless = settings.headless or not launcher.display_available()
                    browser = await launcher.launch(install, str(profile), headless=headless)
            except launcher.BrowserStartError as exc:
                raise BrowserError(str(exc)) from exc
        try:
            connection = await CdpConnection.connect(
                browser.endpoint,
                open_timeout=ATTACH_CONNECT_TIMEOUT_SECONDS if attach else 10.0,
            )
        except Exception as exc:  # noqa: BLE001 - whatever went wrong, there is no browser to drive
            await launcher.close(browser)
            hint = " Allow the connection in the browser's prompt and try again." if attach else ""
            raise BrowserError(f"The browser could not be reached ({type(exc).__name__}).{hint}") from exc
        self._connection = connection
        self._browser = browser
        # What the browser before this one could not do says nothing about this one.
        self._can_create_tabs = True
        self._user_browser = attach
        connection.on_event(self._on_event)
        try:
            await connection.send("Target.setDiscoverTargets", {"discover": True})
            targets = await connection.send("Target.getTargets")
            for info in targets.get("targetInfos") or ():
                self._note_target(info)
            if attach or not self._pages:
                # A browser the user is using: their own tabs are left as they
                # are, and the work gets a tab of its own where that can be had.
                await self._open_blank_page()
            if self._selected is None and self._pages:
                self._selected = next(iter(self._pages))
        except (CdpError, CdpClosed, CdpTimeout) as exc:
            await self._drop_connection()
            raise BrowserError(f"The browser did not answer ({exc}).") from exc

    async def _open_blank_page(self) -> None:
        if not self._can_create_tabs or self._connection is None:
            return
        try:
            created = await self._connection.send("Target.createTarget", {"url": "about:blank"})
        except CdpError:
            # A window that cannot open tabs (one provided by the desktop
            # shell): the page it has is the one that is worked on.
            self._can_create_tabs = False
            return
        target_id = str(created.get("targetId") or "")
        if target_id:
            self._note_target({"targetId": target_id, "type": "page", "url": "about:blank"}, own=True)
            self._selected = target_id

    async def _drop_connection(self) -> None:
        connection, self._connection = self._connection, None
        self._pages.clear()
        self._selected = None
        if connection is not None:
            await connection.close()

    async def close(self) -> None:
        """Drop the connection and close the browser if this process started it."""
        browser, self._browser = self._browser, None
        connection = self._connection
        if browser is not None:
            try:
                await launcher.close(browser, connection)
            except Exception:  # noqa: BLE001 - the connection is dropped either way
                logger.warning("closing the browser failed", exc_info=True)
        await self._drop_connection()
        self._window = None

    def forget_snapshots(self) -> None:
        """Make every uid handed out so far invalid; for when another commission takes over."""
        for page in self._pages.values():
            page.snapshot = None
            page.known_uids.clear()

    # -- events -----------------------------------------------------------

    def _note_target(self, info: dict[str, Any], *, own: bool = False) -> None:
        if info.get("type") != "page":
            return
        target_id = str(info.get("targetId") or "")
        if not target_id:
            return
        url = str(info.get("url") or "")
        if url.lower().startswith(BROWSER_OWN_SCHEMES):
            # Also a page that was listed and has gone to one of these since.
            self._forget_page(target_id)
            return
        page = self._pages.get(target_id)
        if page is None:
            opener = self._pages.get(str(info.get("openerId") or ""))
            page = Page(id=self._next_page_id, target_id=target_id, own=own or (opener is not None and opener.own))
            self._next_page_id += 1
            self._pages[target_id] = page
        elif own:
            # The event about a tab can arrive before the answer that says it was opened for the work.
            page.own = True
        page.url = url
        page.title = str(info.get("title") or "")

    def _forget_page(self, target_id: str) -> None:
        """Take a page that is gone off the list; what still waits for it fails."""
        page = self._pages.pop(target_id, None)
        if page is None:
            return
        if page.session_id and self._connection is not None:
            self._connection.fail_session(page.session_id)
        if self._selected == target_id:
            fallback = self._fallback()
            self._selected = fallback.target_id if fallback is not None else None

    def _fallback(self) -> Page | None:
        """Return the page to go on with when the selected one is gone, or None.

        In the browser the user is using that is never one of their tabs: a
        tab of theirs is only worked on when the model selected it. The
        exception is a browser that cannot open tabs, where the page there
        is is the one to work on.
        """
        for page in self._pages.values():
            if page.own or not (self._user_browser and self._can_create_tabs):
                return page
        return None

    def is_open(self, page: Page) -> bool:
        return self._pages.get(page.target_id) is page

    def _on_event(self, method: str, params: dict[str, Any], session_id: str | None) -> None:
        if method in ("Target.targetCreated", "Target.targetInfoChanged"):
            self._note_target(params.get("targetInfo") or {})
            return
        if method == "Target.targetDestroyed":
            self._forget_page(str(params.get("targetId") or ""))
            return
        if method == "Target.detachedFromTarget":
            detached = params.get("sessionId")
            for page in self._pages.values():
                if page.session_id == detached:
                    page.session_id = None
            if isinstance(detached, str) and detached and self._connection is not None:
                self._connection.fail_session(detached)
            return
        if session_id is None:
            return
        page = next((item for item in self._pages.values() if item.session_id == session_id), None)
        if page is None:
            return
        if method == "Page.javascriptDialogOpening":
            page.dialog = Dialog(
                kind=str(params.get("type") or "alert"),
                message=str(params.get("message") or ""),
                default_prompt=str(params.get("defaultPrompt") or ""),
            )
            page.dialog_event.set()
        elif method == "Page.javascriptDialogClosed":
            page.dialog = None
            page.dialog_event.clear()
        elif method == "Page.frameNavigated":
            frame = params.get("frame") or {}
            frame_id = str(frame.get("id") or "")
            if frame_id:
                page.frame_loaders[frame_id] = str(frame.get("loaderId") or "")
            if not frame.get("parentId"):
                page.main_frame_id = frame_id
                page.navigations += 1
                page.url = str(frame.get("url") or page.url)
        elif method == "Page.frameDetached":
            page.frame_loaders.pop(str(params.get("frameId") or ""), None)
        elif method == "Page.frameStartedLoading":
            if str(params.get("frameId") or "") == page.main_frame_id or not page.main_frame_id:
                page.loading = True
                page.load_event.clear()
        elif method == "Page.frameStoppedLoading":
            if str(params.get("frameId") or "") == page.main_frame_id:
                page.loading = False
                page.load_event.set()
        elif method == "Page.loadEventFired":
            page.load_event.set()

    # -- pages ------------------------------------------------------------

    def pages(self) -> list[Page]:
        return list(self._pages.values())

    def current(self) -> Page | None:
        """Return the page the next action is meant for, or None when there is none."""
        page = self._pages.get(self._selected or "")
        if page is None:
            page = self._fallback()
            self._selected = page.target_id if page is not None else None
        return page

    def selected(self) -> Page:
        page = self.current()
        if page is None:
            if self._pages:
                raise BrowserError(
                    "The tab this work was done in is closed, and the tabs that are left are the user's own. "
                    "Open a tab with browser_new_page; select one of theirs with browser_select_page only "
                    "when the task is about that very tab."
                )
            raise BrowserError("The browser has no open page. Open one with browser_new_page.")
        return page

    def page_by_id(self, page_id: Any) -> Page:
        for page in self._pages.values():
            if page.id == page_id:
                return page
        raise BrowserError(f"There is no page {page_id}. Call browser_list_pages to see the open pages.")

    async def _attached(self, page: Page) -> str:
        """Return the session of a page, attaching to it on first use."""
        if page.session_id is not None:
            return page.session_id
        connection = self._require_connection()
        attached = await connection.send("Target.attachToTarget", {"targetId": page.target_id, "flatten": True})
        page.session_id = str(attached["sessionId"])
        await connection.send("Page.enable", session_id=page.session_id)
        try:
            # A window nobody looks at still behaves as the focused one.
            await connection.send("Emulation.setFocusEmulationEnabled", {"enabled": True}, session_id=page.session_id)
        except CdpError:
            logger.debug("focus emulation is not available", exc_info=True)
        await self._refresh_frames(page)
        return page.session_id

    def _require_connection(self) -> CdpConnection:
        if self._connection is None or self._connection.closed:
            raise BrowserError("The browser was closed. Call the tool again to start it anew.")
        return self._connection

    async def _send(self, page: Page, method: str, params: dict[str, Any] | None = None, *, timeout: float = 30.0) -> dict[str, Any]:
        """Send a command to a page. ``PageClosed`` when the page is gone, before or while it is sent."""
        if not self.is_open(page):
            raise PageClosed()
        try:
            session_id = await self._attached(page)
        except CdpError as exc:
            if exc.method != "Target.attachToTarget" and exc.code != SESSION_GONE_CODE:
                raise
            # A page on the list that cannot be attached to has closed, and
            # the news of it has not arrived yet.
            self._forget_page(page.target_id)
            raise PageClosed() from exc
        try:
            return await self._require_connection().send(method, params, session_id=session_id, timeout=timeout)
        except CdpClosed as exc:
            raise BrowserError("The browser was closed. Call the tool again to start it anew.") from exc
        except CdpTimeout as exc:
            if page.dialog is not None:
                raise BrowserError(self.dialog_notice(page)) from exc
            raise BrowserError(f"The page did not respond ({exc}). It may be busy or frozen; try reloading it.") from exc
        except CdpError as exc:
            if exc.code == SESSION_GONE_CODE or not self.is_open(page):
                raise PageClosed() from exc
            raise

    async def _answer_unless_dialog(
        self, page: Page, method: str, params: dict[str, Any], *, timeout: float = 30.0
    ) -> dict[str, Any] | None:
        """Send a command that a dialog may hold up; return None when a dialog opened instead of an answer."""
        if page.dialog is not None:
            return None
        work = asyncio.ensure_future(self._send(page, method, params, timeout=timeout))
        opened = asyncio.ensure_future(page.dialog_event.wait())
        try:
            await asyncio.wait({work, opened}, return_when=asyncio.FIRST_COMPLETED)
            if work.done():
                return work.result()
            # The command is answered once the dialog is handled; nobody waits for it.
            work.add_done_callback(_drop_outcome)
            return None
        finally:
            opened.cancel()
            if not work.done() and page.dialog is None:
                work.cancel()

    async def _send_unless_dialog(self, page: Page, method: str, params: dict[str, Any], *, timeout: float = 30.0) -> bool:
        """Send one step of an action; return False when nothing more is to be sent to the page.

        That is when a dialog opened instead of an answer, and when the page
        closed (a button that closes its own window): the steps before it
        were carried out, and the rest has nowhere to go.
        """
        try:
            return await self._answer_unless_dialog(page, method, params, timeout=timeout) is not None
        except PageClosed:
            return False

    async def _refresh_frames(self, page: Page) -> None:
        tree = await self._send(page, "Page.getFrameTree")
        loaders: dict[str, str] = {}

        def walk(item: dict[str, Any]) -> None:
            frame = item.get("frame") or {}
            if frame.get("id"):
                loaders[str(frame["id"])] = str(frame.get("loaderId") or "")
            for child in item.get("childFrames") or ():
                walk(child)

        root = tree.get("frameTree") or {}
        walk(root)
        page.frame_loaders = loaders
        page.main_frame_id = str((root.get("frame") or {}).get("id") or "")
        page.url = str((root.get("frame") or {}).get("url") or page.url)

    def dialog_notice(self, page: Page) -> str:
        dialog = page.dialog
        if dialog is None:
            return ""
        return (
            f"# Open dialog\n{dialog.kind}: {dialog.message}\n"
            "Call browser_handle_dialog to handle it before continuing."
        )

    def require_no_dialog(self, page: Page) -> None:
        if page.dialog is not None:
            raise BrowserError(self.dialog_notice(page))

    async def select_page(self, page_id: Any, *, bring_to_front: bool = False) -> Page:
        page = self.page_by_id(page_id)
        self._selected = page.target_id
        if bring_to_front:
            await self._send(page, "Page.bringToFront")
        return page

    async def new_page(self, url: str, *, background: bool = False, timeout: float | None = None) -> Page:
        check_url(url)
        connection = self._require_connection()
        if not self._can_create_tabs:
            raise BrowserError("This browser cannot open new tabs. Use browser_navigate_page on the current page instead.")
        try:
            created = await connection.send("Target.createTarget", {"url": "about:blank", "background": bool(background)})
        except CdpError as exc:
            self._can_create_tabs = False
            raise BrowserError(
                "This browser cannot open new tabs. Use browser_navigate_page on the current page instead."
            ) from exc
        target_id = str(created.get("targetId") or "")
        self._note_target({"targetId": target_id, "type": "page", "url": "about:blank"}, own=True)
        self._selected = target_id
        page = self.selected()
        await self.navigate(page, "url", url, timeout)
        return page

    async def close_page(self, page_id: Any) -> None:
        page = self.page_by_id(page_id)
        if len(self._pages) <= 1:
            raise BrowserError("The last open page cannot be closed.")
        await self._require_connection().send("Target.closeTarget", {"targetId": page.target_id})
        self._forget_page(page.target_id)

    # -- navigation ---------------------------------------------------------

    async def _wait_load(self, page: Page, timeout: float) -> bool:
        """Wait for the page to finish loading or for a dialog; False when the time ran out."""
        loaded = asyncio.ensure_future(page.load_event.wait())
        dialog = asyncio.ensure_future(page.dialog_event.wait())
        try:
            done, _pending = await asyncio.wait({loaded, dialog}, timeout=timeout, return_when=asyncio.FIRST_COMPLETED)
            return bool(done)
        finally:
            loaded.cancel()
            dialog.cancel()

    async def navigate(self, page: Page, kind: str, url: str | None, timeout: float | None = None) -> str:
        """Go to an address, back, forward, or reload; return a sentence about what happened."""
        limit = timeout if timeout and timeout > 0 else NAVIGATION_TIMEOUT_SECONDS
        # A request that is refused leaves the page, and the uids of it, as they are.
        if kind == "url":
            if not url:
                raise BrowserError("A URL is required to navigate.")
            check_url(url)
        elif kind not in ("back", "forward", "reload"):
            raise BrowserError("type must be one of: url, back, forward, reload")
        page.load_event.clear()
        page.snapshot = None
        if kind == "url":
            assert url is not None
            if page.dialog is None:
                # A page may ask before it is left ("Leave site?"). The browser
                # answers the command only once that is decided, so the dialog
                # is what comes back, not the wait for the answer.
                answered = await self._answer_unless_dialog(page, "Page.navigate", {"url": url}, timeout=limit + 5)
                if answered is None:
                    return f"The page asks for confirmation before it is left; {url} is not loaded yet."
                result = answered
            else:
                # An alert that is open is closed by the navigation.
                result = await self._send(page, "Page.navigate", {"url": url}, timeout=limit + 5)
            if result.get("errorText") and not result.get("loaderId"):
                raise BrowserError(f"Unable to navigate to {url}: {result['errorText']}")
            done = f"Successfully navigated to {url}."
            if not result.get("loaderId"):
                # The same document (only its fragment changed): no load to wait for.
                return done
            if result.get("errorText"):
                done = f"Navigated to {url}, which loaded an error page: {result['errorText']}."
        elif kind in ("back", "forward"):
            history = await self._send(page, "Page.getNavigationHistory")
            entries = history.get("entries") or []
            index = int(history.get("currentIndex") or 0) + (-1 if kind == "back" else 1)
            if not 0 <= index < len(entries):
                raise BrowserError(f"Unable to navigate {kind}: there is no page to go {kind} to.")
            await self._send(page, "Page.navigateToHistoryEntry", {"entryId": entries[index]["id"]})
            done = f"Successfully navigated {kind}."
        else:
            await self._send(page, "Page.reload")
            done = "Successfully reloaded the page."
        if not await self._wait_load(page, limit):
            done += f" The page was still loading after {limit:.0f}s; what has loaded so far is shown."
        await self._quiet(page)
        return done

    async def _quiet(self, page: Page) -> None:
        """Wait a moment for the page to stop changing; never fails."""
        if page.dialog is not None:
            return
        try:
            await self._send_unless_dialog(
                page, "Runtime.evaluate", {"expression": _DOM_QUIET, "awaitPromise": True, "returnByValue": True}, timeout=4.0
            )
        except BrowserError:
            logger.debug("waiting for the page to settle failed", exc_info=True)

    async def after_action(self, page: Page, navigations_before: int) -> None:
        """Let what an action set off run its course: a navigation, then the page settling."""
        await asyncio.sleep(0.1)
        # A dialog holds everything up until it is handled; a page the action
        # closed (a button that closes its own window) has nothing to settle.
        if page.dialog is not None or not self.is_open(page):
            return
        if page.loading or page.navigations != navigations_before:
            page.snapshot = None
            await self._wait_load(page, ACTION_NAVIGATION_TIMEOUT_SECONDS)
        await self._quiet(page)

    # -- snapshots ------------------------------------------------------------

    async def take_snapshot(self, page: Page, *, verbose: bool = False) -> Snapshot:
        """Take a snapshot of a page and make it the one its uids are read from."""
        self.require_no_dialog(page)
        await self._refresh_frames(page)
        try:
            answer = await self._send(page, "Accessibility.getFullAXTree", timeout=SNAPSHOT_TIMEOUT_SECONDS)
        except CdpError as exc:
            raise BrowserError(f"The page could not be read ({exc.message}). Reload it or select another page.") from exc
        root = build_ax_tree(answer.get("nodes") or [])
        if root is None:
            raise BrowserError("The page has no content to take a snapshot of yet. Wait a moment and try again.")
        budget = [MAX_IFRAMES]
        await self._attach_frames(page, root, verbose, depth=0, budget=budget)
        tree = serialize_tree(
            root, verbose=verbose, loader_id=page.frame_loaders.get(page.main_frame_id, ""), frame_id=page.main_frame_id
        )
        if tree is None:
            raise BrowserError("The page has nothing to list. Wait a moment and try again.")
        snapshot_id = self._next_snapshot_id
        self._next_snapshot_id += 1
        by_uid = assign_uids(tree, snapshot_id, page.known_uids)
        page.snapshot = Snapshot(snapshot_id=snapshot_id, root=tree, verbose=verbose, by_uid=by_uid)
        return page.snapshot

    async def _attach_frames(self, page: Page, root: Any, verbose: bool, *, depth: int, budget: list[int]) -> None:
        """Give every embedded document of the same process its own tree, a few levels deep."""
        if depth >= MAX_IFRAME_DEPTH:
            return
        for node in iframe_nodes(root):
            if budget[0] <= 0:
                return
            budget[0] -= 1
            try:
                described = await self._send(page, "DOM.describeNode", {"backendNodeId": node.backend_node_id}, timeout=5.0)
                frame_id = str((described.get("node") or {}).get("frameId") or "")
                if not frame_id or frame_id not in page.frame_loaders:
                    continue
                answer = await self._send(
                    page, "Accessibility.getFullAXTree", {"frameId": frame_id}, timeout=SNAPSHOT_TIMEOUT_SECONDS
                )
            except (CdpError, BrowserError):
                # A frame of another site lives in another process and is not
                # reached this way; frames also come and go at any time.
                continue
            inner = build_ax_tree(answer.get("nodes") or [])
            if inner is None:
                continue
            await self._attach_frames(page, inner, verbose, depth=depth + 1, budget=budget)
            loader_id = page.frame_loaders.get(frame_id)
            if loader_id is None:
                continue  # Taken out of the page while it was read.
            node.frame = serialize_tree(inner, verbose=verbose, loader_id=loader_id, frame_id=frame_id)

    # -- elements -------------------------------------------------------------

    async def element(self, page: Page, uid: str) -> tuple[SnapshotNode, str]:
        """Return the snapshot node of a uid and a handle on its element.

        Raises ``BrowserError`` when the uid is not of the latest snapshot or
        its element is gone; nothing is ever acted on from an older state.
        """
        if page.snapshot is None:
            raise BrowserError(f"No snapshot found for page {page.id}. Use browser_take_snapshot to capture one.")
        node = page.snapshot.by_uid.get(str(uid))
        if node is None or node.backend_node_id is None:
            raise BrowserError(
                f'Element uid "{uid}" not found on page {page.id}. Use a uid of the latest snapshot; '
                "take a new one with browser_take_snapshot."
            )
        gone = BrowserError(
            f"Element with uid {uid} no longer exists on the page. The page has changed; take a new snapshot "
            "with browser_take_snapshot and use a uid from it."
        )
        if page.frame_loaders.get(node.frame_id) != node.loader_id:
            raise gone
        try:
            resolved = await self._send(page, "DOM.resolveNode", {"backendNodeId": node.backend_node_id})
            object_id = (resolved.get("object") or {}).get("objectId")
            if not object_id:
                raise gone
            element = await self._send(
                page,
                "Runtime.callFunctionOn",
                {"objectId": object_id, "functionDeclaration": _ELEMENT_OF_NODE, "returnByValue": False},
            )
        except CdpError as exc:
            raise gone from exc
        element_id = (element.get("result") or {}).get("objectId")
        if not element_id:
            raise gone
        return node, str(element_id)

    async def _call(self, page: Page, object_id: str, function: str, *arguments: Any) -> Any:
        answer = await self._send(
            page,
            "Runtime.callFunctionOn",
            {
                "objectId": object_id,
                "functionDeclaration": function,
                "arguments": [{"value": argument} for argument in arguments],
                "returnByValue": True,
            },
        )
        if answer.get("exceptionDetails"):
            detail = answer["exceptionDetails"]
            text = (detail.get("exception") or {}).get("description") or detail.get("text") or "script error"
            raise BrowserError(f"The page refused the action: {str(text)[:200]}")
        return (answer.get("result") or {}).get("value")

    async def _point(self, page: Page, uid: str, object_id: str) -> tuple[float, float]:
        """Return a point of the element inside the visible part of the page, scrolling it into view first."""
        try:
            await self._send(page, "DOM.scrollIntoViewIfNeeded", {"objectId": object_id}, timeout=5.0)
        except CdpError:
            logger.debug("scrolling the element into view failed", exc_info=True)
        try:
            answer = await self._send(page, "DOM.getContentQuads", {"objectId": object_id}, timeout=5.0)
        except CdpError as exc:
            raise BrowserError(f"The element with uid {uid} is not visible, so it cannot be acted on.") from exc
        for quad in answer.get("quads") or ():
            if len(quad) != 8:
                continue
            xs, ys = quad[0::2], quad[1::2]
            if (max(xs) - min(xs)) * (max(ys) - min(ys)) > 1:
                return sum(xs) / 4, sum(ys) / 4
        raise BrowserError(f"The element with uid {uid} is not visible, so it cannot be acted on.")

    async def _mouse(self, page: Page, kind: str, x: float, y: float, **extra: Any) -> bool:
        return await self._send_unless_dialog(page, "Input.dispatchMouseEvent", {"type": kind, "x": x, "y": y, **extra})

    async def _click_at(self, page: Page, x: float, y: float, *, count: int = 1) -> None:
        await self._mouse(page, "mouseMoved", x, y)
        for number in range(1, count + 1):
            pressed = False
            try:
                pressed = await self._mouse(page, "mousePressed", x, y, button="left", buttons=1, clickCount=number)
            finally:
                # Sent even when the call is stopped in between, so the button
                # is never left down. (With a dialog open nothing is sent.)
                released = await asyncio.shield(
                    self._mouse(page, "mouseReleased", x, y, button="left", buttons=0, clickCount=number)
                )
            if not (pressed and released):
                return

    async def click(self, page: Page, uid: str, *, double: bool = False) -> None:
        self.require_no_dialog(page)
        _node, object_id = await self.element(page, uid)
        before = page.navigations
        if not await self._call(page, object_id, _SELECT_OPTION):
            x, y = await self._point(page, uid, object_id)
            await self._click_at(page, x, y, count=2 if double else 1)
        await self.after_action(page, before)

    async def hover(self, page: Page, uid: str) -> None:
        self.require_no_dialog(page)
        _node, object_id = await self.element(page, uid)
        before = page.navigations
        x, y = await self._point(page, uid, object_id)
        await self._mouse(page, "mouseMoved", x, y)
        await self.after_action(page, before)

    async def fill(self, page: Page, uid: str, value: str) -> None:
        """Fill one element in; the page is not waited for (see ``fill_many``)."""
        self.require_no_dialog(page)
        _node, object_id = await self.element(page, uid)
        plan = await self._call(page, object_id, _PREPARE_FILL, value)
        kind = plan.get("kind") if isinstance(plan, dict) else None
        if kind == "error":
            raise BrowserError(f"The element with uid {uid} could not be filled in: {plan.get('message')}.")
        if kind == "click":
            x, y = await self._point(page, uid, object_id)
            await self._click_at(page, x, y)
        elif kind == "type":
            if value:
                await self._send_unless_dialog(page, "Input.insertText", {"text": value})
            else:
                await self._press(page, *parse_key("Delete"))

    async def fill_many(self, page: Page, elements: list[tuple[str, str]]) -> list[str]:
        """Fill several elements in, in order; return the uids that were done before one failed.

        Raises the failure of the first element that cannot be filled in,
        with the uids done so far named in it.
        """
        before = page.navigations
        done: list[str] = []
        try:
            for uid, value in elements:
                try:
                    await self.fill(page, uid, value)
                except BrowserError as exc:
                    if done:
                        raise BrowserError(f"{exc} Filled in before it: {', '.join(done)}.") from exc
                    raise
                done.append(uid)
        finally:
            if done:
                await self.after_action(page, before)
        return done

    # -- keyboard -------------------------------------------------------------

    async def _key_event(self, page: Page, kind: str, key: KeyDefinition, mask: int) -> bool:
        typed = typed_text(key, mask)
        text = typed if kind == "keyDown" else None
        # The same name going down and coming up ("A" with Shift held, both times).
        shown = typed if typed and len(key.key) == 1 else key.key
        params: dict[str, Any] = {
            "type": ("keyDown" if text else "rawKeyDown") if kind == "keyDown" else "keyUp",
            "modifiers": mask,
            "key": shown,
            "code": key.code,
            "windowsVirtualKeyCode": key.key_code,
        }
        if text:
            params["text"] = text
            params["unmodifiedText"] = key.text or text
        return await self._send_unless_dialog(page, "Input.dispatchKeyEvent", params)

    async def _press(self, page: Page, key: KeyDefinition, modifiers: list[KeyDefinition]) -> None:
        mask = 0
        down: list[KeyDefinition] = []
        try:
            for modifier in modifiers:
                mask |= modifier_mask([modifier])
                if not await self._key_event(page, "keyDown", modifier, mask):
                    return
                down.append(modifier)
            if await self._key_event(page, "keyDown", key, mask):
                await self._key_event(page, "keyUp", key, mask)
        finally:
            for modifier in reversed(down):
                mask &= ~modifier_mask([modifier])
                await asyncio.shield(self._key_event(page, "keyUp", modifier, mask))

    async def press_key(self, page: Page, combination: str) -> None:
        self.require_no_dialog(page)
        try:
            key, modifiers = parse_key(combination)
        except UnknownKey as exc:
            raise BrowserError(str(exc)) from exc
        before = page.navigations
        await self._press(page, key, modifiers)
        await self.after_action(page, before)

    async def type_text(self, page: Page, text: str, submit_key: str | None = None) -> None:
        self.require_no_dialog(page)
        submit = None
        if submit_key:
            try:
                submit = parse_key(submit_key)
            except UnknownKey as exc:
                raise BrowserError(str(exc)) from exc
        before = page.navigations
        if text:
            await self._send_unless_dialog(page, "Input.insertText", {"text": text})
        if submit is not None:
            await self._press(page, *submit)
        await self.after_action(page, before)

    # -- the rest ---------------------------------------------------------------

    async def screenshot(self, page: Page, *, uid: str | None = None, full_page: bool = False) -> bytes:
        """Return a JPEG of the visible part of the page, of the whole page, or of one element."""
        import base64

        self.require_no_dialog(page)
        if uid and full_page:
            raise BrowserError('Providing both "uid" and "fullPage" is not allowed.')
        params: dict[str, Any] = {"format": "jpeg", "quality": 80}
        if uid:
            _node, object_id = await self.element(page, uid)
            await self._point(page, uid, object_id)
            quads = (await self._send(page, "DOM.getContentQuads", {"objectId": object_id})).get("quads") or []
            if not quads:
                raise BrowserError(f"The element with uid {uid} is not visible, so there is nothing to take a screenshot of.")
            xs = [value for quad in quads for value in quad[0::2]]
            ys = [value for quad in quads for value in quad[1::2]]
            metrics = await self._send(page, "Page.getLayoutMetrics")
            viewport = metrics.get("cssVisualViewport") or {}
            params["clip"] = {
                "x": min(xs) + float(viewport.get("pageX") or 0),
                "y": min(ys) + float(viewport.get("pageY") or 0),
                "width": max(1.0, max(xs) - min(xs)),
                "height": max(1.0, max(ys) - min(ys)),
                "scale": 1,
            }
            params["captureBeyondViewport"] = True
        elif full_page:
            metrics = await self._send(page, "Page.getLayoutMetrics")
            size = metrics.get("cssContentSize") or metrics.get("contentSize") or {}
            params["clip"] = {
                "x": 0,
                "y": 0,
                "width": max(1.0, float(size.get("width") or 1280)),
                "height": max(1.0, min(float(size.get("height") or 720), FULL_PAGE_MAX_HEIGHT)),
                "scale": 1,
            }
            params["captureBeyondViewport"] = True
        answer = await self._send(page, "Page.captureScreenshot", params, timeout=20.0)
        data = answer.get("data")
        if not isinstance(data, str) or not data:
            raise BrowserError("The browser returned no screenshot.")
        return base64.b64decode(data)

    async def wait_for(self, page: Page, texts: list[str], timeout: float | None = None) -> str:
        """Wait until one of the texts is on the page; return the one found."""
        import json

        self.require_no_dialog(page)
        limit = timeout if timeout and timeout > 0 else WAIT_FOR_DEFAULT_SECONDS
        loop = asyncio.get_running_loop()
        deadline = loop.time() + limit
        expression = f"{_FIND_TEXT}({json.dumps(texts, ensure_ascii=False)})"
        while True:
            self.require_no_dialog(page)
            try:
                answer = await self._send(page, "Runtime.evaluate", {"expression": expression, "returnByValue": True}, timeout=5.0)
                found = (answer.get("result") or {}).get("value")
            except PageClosed:
                raise
            except (CdpError, BrowserError):
                found = None  # The page is between two documents; looked at again in a moment.
            if isinstance(found, str):
                return found
            if loop.time() >= deadline:
                raise BrowserError(
                    f"None of {json.dumps(texts, ensure_ascii=False)} appeared within {limit:.0f}s. Matching is "
                    "case-sensitive; try a shorter distinctive part of the text."
                )
            await asyncio.sleep(0.25)

    async def handle_dialog(self, page: Page, accept: bool, prompt_text: str | None = None) -> str:
        dialog = page.dialog
        if dialog is None:
            raise BrowserError("No open dialog found.")
        params: dict[str, Any] = {"accept": bool(accept)}
        if prompt_text is not None and accept:
            params["promptText"] = prompt_text
        before = page.navigations
        try:
            await self._send(page, "Page.handleJavaScriptDialog", params, timeout=10.0)
        except CdpError as exc:
            # Handled meanwhile by the user.
            logger.debug("handling the dialog failed: %s", exc.message)
        page.dialog = None
        page.dialog_event.clear()
        await self.after_action(page, before)
        return f"Successfully {'accepted' if accept else 'dismissed'} the dialog."

    async def refresh_window(self) -> dict[str, Any] | None:
        """Find out where the window of the selected page is on the screen; for the banner."""
        connection = self._connection
        page = self._pages.get(self._selected or "")
        if connection is None or connection.closed or page is None:
            self._window = None
            return None
        info: dict[str, Any] = {"browser": self.browser_name, "pid": self._browser.pid if self._browser else None}
        try:
            answer = await connection.send("Browser.getWindowForTarget", {"targetId": page.target_id}, timeout=3.0)
            info["window_id"] = answer.get("windowId")
            info["bounds"] = answer.get("bounds")
        except (CdpError, CdpClosed, CdpTimeout):
            logger.debug("the browser window could not be located", exc_info=True)
        self._window = info
        return info

    def window(self) -> dict[str, Any] | None:
        return dict(self._window) if self._window else None


def check_url(url: str) -> None:
    """Refuse addresses a page is not to be sent to: anything but http, https and the blank page."""
    lowered = url.strip().lower()
    if lowered == "about:blank" or lowered.startswith(ALLOWED_URL_SCHEMES):
        return
    raise BrowserError(
        f"{url!r} cannot be opened: only http:// and https:// addresses are. Local files are read with the "
        "file tools, not the browser."
    )
