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
import json
import logging
import re
from collections.abc import Awaitable, Callable, Collection, Iterable
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlsplit

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
# How long the release of a key is waited for when the call it belongs to has
# been stopped or has failed: stopping must not take as long as a page takes
# to be given up on.
KEY_RELEASE_WAIT_SECONDS = 5.0
WAIT_FOR_DEFAULT_SECONDS = 10.0
SNAPSHOT_TIMEOUT_SECONDS = 10.0
# Connecting to a browser the user is using waits for them to allow it there.
ATTACH_CONNECT_TIMEOUT_SECONDS = 60.0
MAX_IFRAME_DEPTH = 3
MAX_IFRAMES = 12
FULL_PAGE_MAX_HEIGHT = 6000
ALLOWED_URL_SCHEMES = ("http://", "https://")


def is_web_page(url: str) -> bool:
    """Whether a tab shows a page of the web (or none yet), going by its address.

    Only those are listed to the model and driven. Not the browser's own
    pages (its settings, its sign-in and sync dialogs, its developer tools:
    ``chrome://``, ``edge://``, ``devtools://``), not those of extensions, and
    not a file of this computer the user has open in a tab (``file://``):
    reading files has its own permission, which the browser is no way round.
    """
    lowered = url.strip().lower()
    return not lowered or lowered == "about:blank" or lowered.startswith(ALLOWED_URL_SCHEMES)

# Returns the debugging endpoint of a window the desktop shell provides when
# no browser is installed, or None when it provides none.
FallbackEndpoint = Callable[[], Awaitable["str | None"]]


class BrowserError(Exception):
    """An action that cannot be carried out; the text is for the model."""


class _DialogOpened(Exception):
    """A script run in the page opened a dialog before it returned; what it did up to there is done."""


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
    # frame id -> the frame it is embedded in.
    frame_parents: dict[str, str] = field(default_factory=dict)
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

# Called on the element with what the browser finds at a point: true when a
# click there reaches the element, else a few words on what is there instead.
_REACHES = """function(hit) {
  for (let node = hit; node; node = node.parentNode || node.host) {
    if (node === this) return true;
  }
  // The element has no box of its own there (text inside a link): what holds it is hit.
  for (let node = this; node; node = node.parentNode || node.host) {
    if (node === hit) return true;
  }
  // A label passes the click on to its control.
  const label = hit.closest ? hit.closest('label') : null;
  if (label && label.control === this) return true;
  const said = (hit.getAttribute && hit.getAttribute('aria-label')) || hit.textContent || '';
  const name = said.trim().replace(/\\s+/g, ' ').slice(0, 40);
  const id = hit.id ? '#' + String(hit.id).slice(0, 40) : '';
  return (hit.nodeName || 'node').toLowerCase().slice(0, 40) + id + (name ? ' "' + name + '"' : '');
}"""

# The points of an element's box that are tried, as fractions of its visible
# part: the middle first.
_POINTS_TRIED = ((0.5, 0.5), (0.25, 0.25), (0.75, 0.25), (0.25, 0.75), (0.75, 0.75))

_SELECT_OPTION = """function() {
  if (!(this instanceof HTMLOptionElement)) return false;
  const select = this.closest('select');
  if (!select || select.multiple || select.disabled || this.disabled) return false;
  const group = this.parentElement;
  if (group instanceof HTMLOptGroupElement && group.disabled) return false;
  // Chosen by its place, not by its value: assigning a value chooses the first
  // option that has it, which may be another one, one that cannot be chosen.
  if (select.selectedIndex !== this.index) {
    select.selectedIndex = this.index;
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
    const named = among => among.find(o => o.value === value) || among.find(o => o.label === value)
      || among.find(o => o.text.trim() === value.trim());
    // Of several options by that name (one sold out, one not), the one a person could choose.
    const wanted = named(options.filter(o => !o.disabled
      && !(o.parentElement instanceof HTMLOptGroupElement && o.parentElement.disabled)));
    if (!wanted) {
      if (named(options)) return {kind: 'error', message: 'its option "' + value + '" is disabled'};
      return {kind: 'error', message: 'it has no option "' + value + '"; its options are: '
        + options.map(o => o.text.trim()).slice(0, 30).join(', ')};
    }
    // By its place, as above: a disabled option before it may have the same value.
    if (el.selectedIndex !== wanted.index) {
      el.selectedIndex = wanted.index;
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
    if (el.readOnly) return {kind: 'error', message: 'the element is read-only'};
    const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set;
    setter.call(el, value);
    el.dispatchEvent(new Event('input', {bubbles: true}));
    el.dispatchEvent(new Event('change', {bubbles: true}));
    return {kind: 'done'};
  }
  if (tag === 'INPUT' || tag === 'TEXTAREA') {
    if (el.readOnly) return {kind: 'error', message: 'the element is read-only'};
    return {kind: 'type'};
  }
  if (el.isContentEditable) return {kind: 'type'};
  return {kind: 'error', message: 'it is not an element that can be filled in (' + tag.toLowerCase() + ')'};
}"""

# Run before text is typed into an element: gives it the focus and selects
# what it holds, so that the text takes its place. A step of its own, apart
# from ``_PREPARE_FILL``: a page may open a dialog when the focus moves, and
# then nothing has been typed yet.
_FOCUS_FOR_TYPING = """function() {
  let el = this;
  el.focus();
  if (el.isContentEditable) {
    const range = document.createRange();
    range.selectNodeContents(el);
    const selection = window.getSelection();
    selection.removeAllRanges();
    selection.addRange(range);
  } else if (typeof el.select === 'function') {
    el.select();
  }
  return true;
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
        own_ports: Callable[[], Iterable[int]] | None = None,
    ) -> None:
        self._settings = settings or BrowserSettingsStore()
        self._fallback_endpoint = fallback_endpoint
        # Returns the ports this application's own services listen on. The
        # browser is kept from reaching them: see ``_keep_from_own_ports``.
        self._own_ports = own_ports
        self._blocked_ports: frozenset[int] = frozenset()
        # True when the whole browser is kept from them, not only the pages worked on.
        self._blocked_everywhere = False
        self._answers: set[asyncio.Future[Any]] = set()
        self._connection: CdpConnection | None = None
        self._browser: launcher.RunningBrowser | None = None
        self._pages: dict[str, Page] = {}  # by target id, in the order they appeared
        # The number of every tab opened for the work, by target id, for as
        # long as the tab is open. A tab that goes somewhere that is not
        # listed (a ``blob:`` address, a page of this application) is off the
        # list of pages while it is there; back on a page of the web it is
        # the same page to the model, and still one that may be closed.
        self._own_pages: dict[str, int] = {}
        self._selected: str | None = None
        self._next_page_id = 1
        self._next_snapshot_id = 1
        self._can_create_tabs = True
        # The browser is the one the user is using (attach mode): tabs that
        # were not opened for the work are theirs.
        self._user_browser = False
        self._window: dict[str, Any] | None = None
        # Closing a browser whose start was cancelled; the next start and
        # ``close`` wait for it.
        self._abandoned: asyncio.Future[None] | None = None
        # Browsers of ours that could not be closed when another took their
        # place; ``close`` tries again.
        self._unclosed: list[launcher.RunningBrowser] = []

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
        await self._settle_abandoned()
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
                    starting = asyncio.ensure_future(launcher.launch(install, str(profile), headless=headless))
                    try:
                        browser = await asyncio.shield(starting)
                    except asyncio.CancelledError:
                        # The start goes on in its thread and cannot be
                        # stopped. The browser it brings up would run on
                        # with nobody to close it.
                        self._abandoned = asyncio.ensure_future(self._close_once_started(starting))
                        raise
            except launcher.BrowserStartError as exc:
                raise BrowserError(str(exc)) from exc
        held = self._browser
        if held is not None and held.owned and held.user_data_dir != browser.user_data_dir:
            # A browser of ours is still held (its connection was lost, or its
            # closing was stopped half way) and the one to drive now is
            # another: the settings were changed in between. On the same
            # profile it would be that very browser, found running there by
            # the launcher, and is gone on with. This one would run on with
            # nobody to close it.
            try:
                await launcher.close(held)
            except asyncio.CancelledError:
                # The one that was just reached is held by nobody yet; the
                # one before it stays held, and is closed when asked again.
                self._abandoned = asyncio.ensure_future(self._close_unheld(browser))
                raise
            except Exception:  # noqa: BLE001 - the new one is driven all the same
                logger.warning("closing the browser that was driven before failed", exc_info=True)
                # Not let go of for good: ``close`` tries again.
                self._unclosed.append(held)
            self._browser = None
        try:
            connection = await CdpConnection.connect(
                browser.endpoint,
                open_timeout=ATTACH_CONNECT_TIMEOUT_SECONDS if attach else 10.0,
            )
        except asyncio.CancelledError:
            # Nothing holds the browser yet.
            self._abandoned = asyncio.ensure_future(self._close_unheld(browser))
            raise
        except Exception as exc:  # noqa: BLE001 - whatever went wrong, there is no browser to drive
            await launcher.close(browser)
            hint = " Allow the connection in the browser's prompt and try again." if attach else ""
            raise BrowserError(f"The browser could not be reached ({type(exc).__name__}).{hint}") from exc
        self._connection = connection
        self._browser = browser
        # One that could not be closed earlier and is driven again (found
        # running on its profile) is this one now, and closed as this one.
        self._unclosed = [left for left in self._unclosed if left.user_data_dir != browser.user_data_dir]
        # What the browser before this one could not do says nothing about this one.
        self._can_create_tabs = True
        self._user_browser = attach
        connection.on_event(self._on_event)
        try:
            await self._keep_from_own_ports(connection, browser)
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
        except asyncio.CancelledError:
            # Half set up: the next call connects anew. The browser stays
            # held, so ``close`` closes it.
            await self._drop_connection()
            raise

    def _block_patterns(self) -> list[dict[str, str]]:
        # By port alone, whatever the host: a name that resolves to this
        # computer reaches the same service as 127.0.0.1 does.
        return [{"urlPattern": f"*://*:{port}/*"} for port in sorted(self._blocked_ports)]

    async def _keep_from_own_ports(self, connection: CdpConnection, browser: launcher.RunningBrowser) -> None:
        """Have requests to this application's own ports failed, wherever in the browser they come from.

        None of those services is of use to a task agent, and some answer a
        request from this computer without asking who sent it; a page must
        not be able to reach them, by a link, a redirect or a request of its
        own. In a browser this process started, the whole browser is kept
        from them (every page, embedded page of another site and worker). A
        browser that is the user's, or a window of the desktop shell, is
        somebody else's as a whole (the shell's own windows talk to those
        very ports): there only the pages that are worked on are kept from
        them, when they are attached to. WebSocket connections are not
        covered by either; the browser's protocol has no way to fail them.
        """
        self._blocked_everywhere = False
        ports: set[int] = set()
        if self._own_ports is not None:
            try:
                ports = {int(port) for port in self._own_ports() if 0 < int(port) < 65536}
            except Exception:  # noqa: BLE001 - told loudly; the browser is still usable
                logger.warning("the ports of this application's own services are not known; none is kept from the browser", exc_info=True)
        self._blocked_ports = frozenset(ports)
        if not self._blocked_ports or not browser.owned:
            return
        try:
            await connection.send("Fetch.enable", {"patterns": self._block_patterns()})
            self._blocked_everywhere = True
        except CdpError:
            logger.warning("the whole browser cannot be kept from this application's own ports; each page is instead", exc_info=True)

    def _answer_paused(self, params: dict[str, Any], session_id: str | None) -> None:
        """Answer a request the browser holds for us: failed when it goes to one of the ports, else let on.

        Every held request is answered, or the page would wait for it for ever.
        """
        connection = self._connection
        request_id = params.get("requestId")
        if connection is None or not isinstance(request_id, str):
            return
        port = port_of(str((params.get("request") or {}).get("url") or ""))
        if port in self._blocked_ports:
            # The port only: an address may carry a token.
            logger.info("kept the browser from reaching port %s of this application", port)
            method, payload = "Fetch.failRequest", {"requestId": request_id, "errorReason": "BlockedByClient"}
        else:
            method, payload = "Fetch.continueRequest", {"requestId": request_id}
        answer = asyncio.ensure_future(self._send_quietly(connection, method, payload, session_id))
        self._answers.add(answer)
        answer.add_done_callback(self._answers.discard)

    @staticmethod
    async def _send_quietly(connection: CdpConnection, method: str, params: dict[str, Any], session_id: str | None) -> None:
        try:
            await connection.send(method, params, session_id=session_id, timeout=10.0)
        except (CdpError, CdpClosed, CdpTimeout):
            logger.debug("%s was not taken", method, exc_info=True)

    async def _close_once_started(self, starting: "asyncio.Future[launcher.RunningBrowser]") -> None:
        """Close the browser a start brings up after its caller has gone; never raises."""
        try:
            browser = await starting
        except Exception:  # noqa: BLE001 - it did not start: there is nothing to close
            return
        await self._close_unheld(browser)

    async def _close_unheld(self, browser: launcher.RunningBrowser) -> None:
        """Close a browser that was started and is held by nobody; never raises."""
        try:
            await launcher.close(browser)
        except Exception:  # noqa: BLE001 - nothing more can be done about it
            logger.warning("closing a browser whose start was cancelled failed", exc_info=True)

    async def _settle_abandoned(self) -> None:
        """Wait until a browser left by a start that was cancelled has been closed."""
        abandoned = self._abandoned
        if abandoned is None:
            return
        await asyncio.shield(abandoned)
        if self._abandoned is abandoned:
            self._abandoned = None

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
        self._own_pages.clear()
        self._selected = None
        if connection is not None:
            await connection.close()

    async def close(self) -> None:
        """Drop the connection and close the browser if this process started it."""
        await self._settle_abandoned()
        browser = self._browser
        connection = self._connection
        if browser is not None:
            try:
                await launcher.close(browser, connection)
            except Exception:  # noqa: BLE001 - the connection is dropped either way
                logger.warning("closing the browser failed", exc_info=True)
            # Let go of only now: a close that was cancelled half way (the
            # user stopping everything) still knows which browser to close
            # when it is asked for again.
            if self._browser is browser:
                self._browser = None
        await self._drop_connection()
        self._window = None
        # After the connection is dropped: stopped in here, nothing of the
        # browser that was just closed is left behind.
        for leftover in list(self._unclosed):
            try:
                await launcher.close(leftover)
            except Exception:  # noqa: BLE001 - tried again the next time
                logger.warning("closing a browser that was driven earlier failed again", exc_info=True)
                continue
            self._unclosed.remove(leftover)

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
        if not is_web_page(url) or (self._blocked_ports and port_of(url) in self._blocked_ports):
            # Not a page of the web, or one of this application's own pages
            # (which the user may have open in the browser that is attached
            # to). Also a page that was listed and has gone there since.
            self._forget_page(target_id)
            return
        page = self._pages.get(target_id)
        if page is None:
            opener = self._pages.get(str(info.get("openerId") or ""))
            number = self._own_pages.get(target_id)
            own = own or number is not None or (opener is not None and opener.own)
            if number is None:
                number = self._next_page_id
                self._next_page_id += 1
            page = Page(id=number, target_id=target_id, own=own)
            self._pages[target_id] = page
        elif own:
            # The event about a tab can arrive before the answer that says it was opened for the work.
            page.own = True
        if page.own:
            self._own_pages[target_id] = page.id
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
        if method == "Fetch.requestPaused":
            self._answer_paused(params, session_id)
            return
        if method in ("Target.targetCreated", "Target.targetInfoChanged"):
            self._note_target(params.get("targetInfo") or {})
            return
        if method == "Target.targetDestroyed":
            self._own_pages.pop(str(params.get("targetId") or ""), None)
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
            from config.nekoclaw_settings import BROWSER_DIALOG_TEXT_MAX_TOKENS

            page.dialog = Dialog(
                kind=str(params.get("type") or "alert")[:40],
                # Written by the page, and as long as the page likes.
                message=bounded(str(params.get("message") or ""), BROWSER_DIALOG_TEXT_MAX_TOKENS),
                default_prompt=bounded(str(params.get("defaultPrompt") or ""), BROWSER_DIALOG_TEXT_MAX_TOKENS),
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
                if frame.get("parentId"):
                    page.frame_parents[frame_id] = str(frame["parentId"])
            if not frame.get("parentId"):
                page.main_frame_id = frame_id
                page.navigations += 1
                page.url = str(frame.get("url") or page.url)
        elif method == "Page.frameDetached":
            page.frame_loaders.pop(str(params.get("frameId") or ""), None)
            page.frame_parents.pop(str(params.get("frameId") or ""), None)
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
        if self._blocked_ports and not self._blocked_everywhere:
            try:
                await connection.send("Fetch.enable", {"patterns": self._block_patterns()}, session_id=page.session_id)
            except CdpError:
                logger.warning("a page cannot be kept from this application's own ports", exc_info=True)
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
        parents: dict[str, str] = {}

        def walk(item: dict[str, Any], parent: str) -> None:
            frame = item.get("frame") or {}
            frame_id = str(frame.get("id") or "")
            if frame_id:
                loaders[frame_id] = str(frame.get("loaderId") or "")
                if parent:
                    parents[frame_id] = parent
            for child in item.get("childFrames") or ():
                walk(child, frame_id)

        root = tree.get("frameTree") or {}
        walk(root, "")
        page.frame_loaders = loaders
        page.frame_parents = parents
        page.main_frame_id = str((root.get("frame") or {}).get("id") or "")
        page.url = str((root.get("frame") or {}).get("url") or page.url)

    def dialog_notice(self, page: Page) -> str:
        dialog = page.dialog
        if dialog is None:
            return ""
        kind = f"{'an' if dialog.kind[:1] in 'aeiou' else 'a'} {dialog.kind}"
        if dialog.message:
            # Quoted on a line of its own and named as the page's: what a page
            # says in a dialog is its text, not a word of this tool.
            said = (
                f"The page opened {kind} dialog. Its text, written by the page:\n"
                f"{json.dumps(dialog.message, ensure_ascii=False)}"
            )
        else:
            said = f"The page opened {kind} dialog; it has no text of its own."
        return f"# Open dialog\n{said}\nCall browser_handle_dialog to handle it before continuing."

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
        check_url(url, self._blocked_ports)
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
        if self._user_browser and not page.own:
            raise BrowserError(f"Page {page.id} is a tab of the user's own, not one opened for this work: it is not closed.")
        await self._require_connection().send("Target.closeTarget", {"targetId": page.target_id})
        self._own_pages.pop(page.target_id, None)
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
            check_url(url, self._blocked_ports)
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
            # The history of a tab may hold what the user opened in it: a
            # file of this computer, one of this application's own pages.
            check_url(str(entries[index].get("url") or "about:blank"), self._blocked_ports)
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
        except (BrowserError, CdpError):
            # Also what the browser itself answers when the page goes
            # elsewhere under the wait and takes the script with it. The
            # action before this wait was carried out: a wait that fails must
            # not turn it into a failure, or it is done a second time.
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
        # The tree of a large page is tens of thousands of nodes: built and
        # laid out in a worker thread, not on the event loop.
        root = await asyncio.to_thread(build_ax_tree, answer.get("nodes") or [])
        if root is None:
            raise BrowserError("The page has no content to take a snapshot of yet. Wait a moment and try again.")
        budget, unlisted = [MAX_IFRAMES], [0]
        await self._attach_frames(page, root, verbose, depth=0, budget=budget, unlisted=unlisted)
        tree = await asyncio.to_thread(
            serialize_tree,
            root,
            verbose=verbose,
            loader_id=page.frame_loaders.get(page.main_frame_id, ""),
            frame_id=page.main_frame_id,
        )
        if tree is None:
            raise BrowserError("The page has nothing to list. Wait a moment and try again.")
        snapshot_id = self._next_snapshot_id
        self._next_snapshot_id += 1
        by_uid = await asyncio.to_thread(assign_uids, tree, snapshot_id, page.known_uids)
        page.snapshot = Snapshot(
            snapshot_id=snapshot_id, root=tree, verbose=verbose, by_uid=by_uid, unlisted_frames=unlisted[0]
        )
        return page.snapshot

    async def _attach_frames(
        self, page: Page, root: Any, verbose: bool, *, depth: int, budget: list[int], unlisted: list[int]
    ) -> None:
        """Give every embedded document of the same process its own tree, a few levels deep.

        ``unlisted`` counts the embedded pages that can be seen on the page
        and whose content is not read: the model is told how many there are.
        """
        # Not the ones nobody sees (tracking frames, of which a page may have dozens).
        nodes = [node for node in iframe_nodes(root) if not (node.ignored or node.hidden)]
        if depth >= MAX_IFRAME_DEPTH:
            unlisted[0] += len(nodes)
            return
        for index, node in enumerate(nodes):
            if budget[0] <= 0:
                unlisted[0] += len(nodes) - index
                return
            budget[0] -= 1
            try:
                described = await self._send(page, "DOM.describeNode", {"backendNodeId": node.backend_node_id}, timeout=5.0)
                frame_id = str((described.get("node") or {}).get("frameId") or "")
                if not frame_id or frame_id not in page.frame_loaders:
                    # A frame of another site lives in another process and is not reached this way.
                    unlisted[0] += 1
                    continue
                answer = await self._send(
                    page, "Accessibility.getFullAXTree", {"frameId": frame_id}, timeout=SNAPSHOT_TIMEOUT_SECONDS
                )
            except (CdpError, BrowserError):
                # Frames also come and go at any time.
                unlisted[0] += 1
                continue
            inner = await asyncio.to_thread(build_ax_tree, answer.get("nodes") or [])
            if inner is None:
                unlisted[0] += 1
                continue
            await self._attach_frames(page, inner, verbose, depth=depth + 1, budget=budget, unlisted=unlisted)
            loader_id = page.frame_loaders.get(frame_id)
            if loader_id is None:
                continue  # Taken out of the page while it was read.
            node.frame = await asyncio.to_thread(serialize_tree, inner, verbose=verbose, loader_id=loader_id, frame_id=frame_id)

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
        """Run one of the scripts above on an element and return what it returns.

        Raises ``_DialogOpened`` when the script opened a dialog (a handler
        of the page that asks before it goes on): the browser answers the
        call only once the dialog is handled, which the model cannot do
        while this call waits.
        """
        answer = await self._answer_unless_dialog(
            page,
            "Runtime.callFunctionOn",
            {
                "objectId": object_id,
                "functionDeclaration": function,
                "arguments": [{"value": argument} for argument in arguments],
                "returnByValue": True,
            },
        )
        if answer is None:
            raise _DialogOpened()
        if answer.get("exceptionDetails"):
            detail = answer["exceptionDetails"]
            text = (detail.get("exception") or {}).get("description") or detail.get("text") or "script error"
            raise BrowserError(f"The page refused the action: {str(text)[:200]}")
        return (answer.get("result") or {}).get("value")

    async def _scroll_into_view(self, page: Page, object_id: str) -> None:
        try:
            await self._send(page, "DOM.scrollIntoViewIfNeeded", {"objectId": object_id}, timeout=5.0)
        except CdpError:
            logger.debug("scrolling the element into view failed", exc_info=True)

    async def _viewport(self, page: Page) -> tuple[float, float]:
        """Return the width and height of the visible part of the page, in the units mouse events are sent in."""
        try:
            metrics = await self._send(page, "Page.getLayoutMetrics", timeout=5.0)
        except CdpError:
            return float("inf"), float("inf")
        for name in ("cssLayoutViewport", "cssVisualViewport", "layoutViewport"):
            viewport = metrics.get(name) or {}
            width, height = viewport.get("clientWidth"), viewport.get("clientHeight")
            if isinstance(width, (int, float)) and isinstance(height, (int, float)) and width > 0 and height > 0:
                return float(width), float(height)
        return float("inf"), float("inf")

    def _frame_inside(self, page: Page, frame_id: str, outer: str) -> bool:
        """Whether a frame is embedded, at any depth, in the frame ``outer``."""
        seen: set[str] = set()
        while frame_id and frame_id not in seen:
            seen.add(frame_id)
            frame_id = page.frame_parents.get(frame_id, "")
            if frame_id == outer:
                return True
        return False

    async def _reaches(self, page: Page, node: SnapshotNode, object_id: str, x: float, y: float) -> bool | str:
        """Whether the mouse at a point reaches the element; else a few words on what is there instead."""
        try:
            hit = await self._send(page, "DOM.getNodeForLocation", {"x": int(x), "y": int(y)}, timeout=5.0)
            hit_frame = str(hit.get("frameId") or "")
            if node.frame_id and hit_frame and hit_frame != node.frame_id:
                # Another document: one embedded in the element (the element
                # is a frame, or holds one) is the element's own; any other
                # lies over the frame the element is in.
                if self._frame_inside(page, hit_frame, node.frame_id):
                    return True
                if hit_frame in page.frame_loaders:
                    return "something outside the embedded page the element is in"
                return True
            resolved = await self._send(page, "DOM.resolveNode", {"backendNodeId": hit.get("backendNodeId")}, timeout=5.0)
            answer = await self._send(
                page,
                "Runtime.callFunctionOn",
                {
                    "objectId": object_id,
                    "functionDeclaration": _REACHES,
                    "arguments": [{"objectId": (resolved.get("object") or {}).get("objectId")}],
                    "returnByValue": True,
                },
                timeout=5.0,
            )
        except CdpError:
            # The check cannot be made (nothing is found at the point, or the
            # browser does not offer it): the mouse goes there as it would
            # have without the check.
            return True
        value = (answer.get("result") or {}).get("value")
        # Written by the page: kept short.
        return value[:160] if isinstance(value, str) and value else True

    async def _point(self, page: Page, uid: str, node: SnapshotNode, object_id: str) -> tuple[float, float]:
        """Return the point of the element the mouse is sent to, scrolling the element into view first.

        The point lies in the visible part of the page, and what the browser
        finds there is the element (or something inside it). The middle of
        an element taller than the window may be off the screen, and a
        banner or a dialog may lie over it: the mouse would then go to
        nothing, or to that.
        """
        await self._scroll_into_view(page, object_id)
        try:
            answer = await self._send(page, "DOM.getContentQuads", {"objectId": object_id}, timeout=5.0)
        except CdpError as exc:
            raise BrowserError(f"The element with uid {uid} is not visible, so it cannot be acted on.") from exc
        width, height = await self._viewport(page)
        covering = ""
        for quad in answer.get("quads") or ():
            if len(quad) != 8:
                continue
            xs = [min(max(float(value), 0.0), width) for value in quad[0::2]]
            ys = [min(max(float(value), 0.0), height) for value in quad[1::2]]
            left, right, top, bottom = min(xs), max(xs), min(ys), max(ys)
            if (right - left) * (bottom - top) <= 1:
                continue
            for across, down in _POINTS_TRIED:
                x, y = left + (right - left) * across, top + (bottom - top) * down
                reached = await self._reaches(page, node, object_id, x, y)
                if reached is True:
                    return x, y
                covering = str(reached)
            break
        if covering:
            raise BrowserError(
                f"The element with uid {uid} is covered by {covering} where the mouse would go, so nothing was "
                "sent to the page. Close or scroll away what covers it, or act on that instead; a new snapshot shows it."
            )
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
        node, object_id = await self.element(page, uid)
        before = page.navigations
        try:
            chosen = await self._call(page, object_id, _SELECT_OPTION)
        except _DialogOpened:
            # The option was chosen, and what the page does on that change asks first.
            chosen = True
        if not chosen:
            x, y = await self._point(page, uid, node, object_id)
            await self._click_at(page, x, y, count=2 if double else 1)
        await self.after_action(page, before)

    async def hover(self, page: Page, uid: str) -> None:
        self.require_no_dialog(page)
        node, object_id = await self.element(page, uid)
        before = page.navigations
        x, y = await self._point(page, uid, node, object_id)
        await self._mouse(page, "mouseMoved", x, y)
        await self.after_action(page, before)

    async def fill(self, page: Page, uid: str, value: str) -> None:
        """Fill one element in; the page is not waited for (see ``fill_many``)."""
        self.require_no_dialog(page)
        node, object_id = await self.element(page, uid)
        try:
            plan = await self._call(page, object_id, _PREPARE_FILL, value)
        except _DialogOpened:
            # The value was set, and what the page does on that change asks
            # first. The dialog is told with the answer; an element after
            # this one is refused until it is handled.
            return
        kind = plan.get("kind") if isinstance(plan, dict) else None
        if kind == "error":
            raise BrowserError(f"The element with uid {uid} could not be filled in: {plan.get('message')}.")
        if kind == "click":
            x, y = await self._point(page, uid, node, object_id)
            await self._click_at(page, x, y)
        elif kind == "type":
            try:
                await self._call(page, object_id, _FOCUS_FOR_TYPING)
            except _DialogOpened:
                # Opened by the page when the focus moved: nothing was typed.
                raise BrowserError(
                    f"A dialog opened when the element with uid {uid} was given the focus, before anything was "
                    f"typed into it. Handle the dialog, then fill the element in again.\n{self.dialog_notice(page)}"
                ) from None
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

    async def _key_event(self, page: Page, kind: str, key: KeyDefinition, mask: int, *, timeout: float = 30.0) -> bool:
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
        return await self._send_unless_dialog(page, "Input.dispatchKeyEvent", params, timeout=timeout)

    async def _press(self, page: Page, key: KeyDefinition, modifiers: list[KeyDefinition]) -> None:
        mask = 0
        down: list[KeyDefinition] = []
        key_is_down = False
        failed = False
        try:
            for modifier in modifiers:
                mask |= modifier_mask([modifier])
                # Down from the moment the event is sent, answered or not:
                # stopped before the answer, it still has to come up.
                down.append(modifier)
                if not await self._key_event(page, "keyDown", modifier, mask):
                    return
            # The same for the key itself.
            key_is_down = True
            if await self._key_event(page, "keyDown", key, mask):
                await self._key_event(page, "keyUp", key, mask)
            key_is_down = False
        except BaseException:
            failed = True
            raise
        finally:
            # Every key that is down comes up, whatever becomes of the others
            # on the way: the key itself when the call was stopped between
            # its going down and coming up, then the modifiers. A release
            # that fails (a page that no longer answers) or is stopped does
            # not leave the rest held: they are sent all the same, only not
            # waited for any more, one wait for such a page being enough. The
            # failure is raised once they are all sent, unless the call is
            # failing already.
            releases = ([(key, False)] if key_is_down else []) + [(item, True) for item in reversed(down)]
            wait = KEY_RELEASE_WAIT_SECONDS if failed else 30.0
            late: BaseException | None = None
            answered = True
            for released, is_modifier in releases:
                if is_modifier:
                    mask &= ~modifier_mask([released])
                release = asyncio.ensure_future(self._key_event(page, "keyUp", released, mask, timeout=wait))
                if answered:
                    try:
                        await asyncio.shield(release)
                    except BaseException as exc:  # noqa: BLE001 - raised below, once the rest is sent
                        # An error the browser itself answered with came back
                        # at once: the page still answers, and the rest is
                        # waited for as before.
                        answered = isinstance(exc, CdpError)
                        if late is None or isinstance(exc, asyncio.CancelledError):
                            late = exc
                if not release.done():
                    # Goes on by itself; kept so that it is not collected half way.
                    self._answers.add(release)
                    release.add_done_callback(self._answers.discard)
                    release.add_done_callback(_drop_outcome)
            if late is not None and (not failed or isinstance(late, asyncio.CancelledError)):
                raise late

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
            await self._scroll_into_view(page, object_id)
            try:
                quads = (await self._send(page, "DOM.getContentQuads", {"objectId": object_id})).get("quads") or []
            except CdpError as exc:
                raise BrowserError(f"The element with uid {uid} is not visible, so it cannot be acted on.") from exc
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


# What a browser takes off the ends of an address, and what it drops from
# anywhere in it.
_ENDS_OF_AN_ADDRESS = "".join(map(chr, range(0x21)))
_DROPPED_FROM_AN_ADDRESS = {ord("\t"): None, ord("\n"): None, ord("\r"): None}
_SLASHES_AFTER_SCHEME = re.compile(r"^(https?|wss?):/*", re.IGNORECASE)


def port_of(url: str) -> int | None:
    """Return the port an address goes to, as a browser reads the address; None when it cannot be told."""
    # A browser drops tabs and line breaks wherever they are, reads a
    # backslash as a slash, and after ``http:`` and the like takes any number
    # of slashes (none, too) for the two the host comes after:
    # ``http:///host:1/`` goes to ``host:1``. ``urlsplit`` reads an empty
    # host there, and with it the port every address has by default. The
    # tabs go first: one between the slashes would hide those behind it.
    cleaned = url.strip(_ENDS_OF_AN_ADDRESS).translate(_DROPPED_FROM_AN_ADDRESS).replace("\\", "/")
    cleaned = _SLASHES_AFTER_SCHEME.sub(lambda found: f"{found.group(1)}://", cleaned)
    try:
        parts = urlsplit(cleaned)
        port = parts.port
    except ValueError:
        return None
    if port is not None:
        return port
    return {"http": 80, "ws": 80, "https": 443, "wss": 443}.get(parts.scheme.lower())


def check_url(url: str, blocked_ports: Collection[int] = ()) -> None:
    """Refuse addresses a page is not to be sent to.

    Anything but http, https and the blank page; and the ports of this
    application's own services, on whatever host.
    """
    lowered = url.strip().lower()
    if lowered == "about:blank":
        return
    if not lowered.startswith(ALLOWED_URL_SCHEMES):
        raise BrowserError(
            f"{url!r} cannot be opened: only http:// and https:// addresses are. Local files are read with the "
            "file tools, not the browser."
        )
    if not blocked_ports:
        return
    port = port_of(url)
    if port is None:
        # Where it goes cannot be told here; a browser may read it all the same.
        raise BrowserError(f"{url!r} cannot be opened: it is not an address that can be read.")
    if port in blocked_ports:
        raise BrowserError(
            f"{url!r} cannot be opened: that port belongs to this application's own services, which the browser "
            "is kept away from."
        )


def bounded(text: str, max_tokens: int) -> str:
    """Return text a page wrote, cut to a number of tokens; what was left out is counted at its end."""
    # Cut by characters first: counting the tokens of megabytes takes seconds.
    head = text[: max_tokens * 16]
    if len(head.encode("utf-8", errors="replace")) <= max_tokens and len(head) == len(text):
        return text  # A token is at least one byte: it fits without counting.
    from utils.tokenize import truncate_to_tokens

    cut = truncate_to_tokens(head, max_tokens)
    if len(cut) >= len(text):
        return text
    return f"{cut}... [{len(text) - len(cut)} more characters not shown]"
