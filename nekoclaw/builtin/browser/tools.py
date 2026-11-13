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

"""The browser tools a task agent calls (``BROWSER_TOOLS`` in the prompts).

Each call is one small step on the browser of ``session.py``. Calls are
carried out one after the other in the order they arrive: there is one
browser and one selected page, and a click that ran while a navigation was
still under way would act on a page nobody has looked at.

The answer to a call is text in the layout Chrome DevTools MCP uses: what
was done, an open dialog if there is one, the list of pages when it matters,
and the snapshot of the page when one was asked for. Two things are added to
keep a task to few steps: navigating, opening and selecting a page always
answer with the page's snapshot, and so does an action after which the page
turned out to be another document (the old uids are of no use then).

A snapshot has an upper size (``BROWSER_SNAPSHOT_MAX_TOKENS``). A longer one
is cut at the end of a line and says so, with the offset to read on from.
The screenshot tool hands its picture back as a picture, never as text.

What was done and what the page then looked like are told apart: an action
that was carried out is answered as done even when the page could not be
read afterwards, so that it is not carried out a second time.
"""

from __future__ import annotations

import asyncio
import base64
import logging
from collections.abc import Awaitable, Callable
from typing import Any

from nekoclaw.builtin.browser.cdp import CdpClosed, CdpError, CdpTimeout
from nekoclaw.builtin.browser.session import MAX_TIMEOUT_SECONDS, BrowserError, BrowserSession, Page, bounded
from nekoclaw.builtin.browser.snapshot import Snapshot, format_lines
from nekoclaw.host.capabilities import BuiltinImage, BuiltinOutcome

logger = logging.getLogger(__name__)

TOOL_PREFIX = "browser_"
# How many open pages the list of pages names.
PAGES_LISTED = 12
# Byte ceiling of one screenshot handed to a model: the same as a screenshot
# of the desktop (``nekoclaw.builtin.desktop.SCREENSHOT_MAX_BYTES``). Three
# screenshots stay in a task agent's context, whichever tools took them.
SCREENSHOT_MAX_BYTES = 200 * 1024


def cap_lines(lines: list[str], max_tokens: int) -> tuple[str, int]:
    """Return as many whole lines as fit in ``max_tokens``, and how many that is. Blocking (tokenizes)."""
    from utils.tokenize import truncate_to_tokens

    text = "\n".join(lines)
    cut = truncate_to_tokens(text, max_tokens)
    if len(cut) >= len(text):
        return text, len(lines)
    # The whole entries that fit, by their lengths. Not by the line breaks in
    # what was cut: an entry may hold line breaks of its own.
    shown, used = 0, 0
    for line in lines:
        used += len(line) + (1 if shown else 0)
        if used > len(cut):
            break
        shown += 1
    if shown:
        return "\n".join(lines[:shown]), shown
    # One line longer than everything allowed (a page that is one block of
    # text): its beginning, so that the note about where to read on, which
    # comes after it, is not cut off further down the way.
    left = max(0, len(lines[0]) - len(cut))
    return f"{cut} [... {left} more characters of this line are not shown]", 1


def _seconds(arguments: dict[str, Any]) -> float | None:
    value = arguments.get("timeout")
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value <= 0:
        return None
    # However long the model asks for: the calls behind this one wait with it.
    return min(float(value) / 1000.0, MAX_TIMEOUT_SECONDS)


def _text(arguments: dict[str, Any], name: str) -> str:
    value = arguments.get(name)
    if not isinstance(value, str) or not value:
        raise BrowserError(f"{name} is required")
    return value


class BrowserTools:
    """Carries out the calls of the browser tools, one at a time."""

    def __init__(self, session: BrowserSession | None = None) -> None:
        self._session = session or BrowserSession()
        self._lock = asyncio.Lock()
        self._handlers: dict[str, Callable[[dict[str, Any]], Awaitable[BuiltinOutcome]]] = {
            "navigate_page": self._navigate_page,
            "take_snapshot": self._take_snapshot,
            "click": self._click,
            "hover": self._hover,
            "fill": self._fill,
            "fill_form": self._fill_form,
            "type_text": self._type_text,
            "press_key": self._press_key,
            "take_screenshot": self._take_screenshot,
            "list_pages": self._list_pages,
            "select_page": self._select_page,
            "new_page": self._new_page,
            "close_page": self._close_page,
            "wait_for": self._wait_for,
            "handle_dialog": self._handle_dialog,
        }

    @property
    def session(self) -> BrowserSession:
        return self._session

    async def call(
        self, tool_name: str, arguments: dict[str, Any], *, refused: Callable[[], str | None] | None = None
    ) -> BuiltinOutcome:
        """Carry out one call and return its answer; failures come back as text for the model.

        ``refused`` is asked once the call's turn has come and returns why
        it is not to be carried out after all, or None.
        """
        handler = self._handlers.get(tool_name[len(TOOL_PREFIX) :] if tool_name.startswith(TOOL_PREFIX) else "")
        if handler is None:
            return BuiltinOutcome(False, f"unknown tool {tool_name}")
        async with self._lock:
            why = refused() if refused is not None else None
            if why:
                return BuiltinOutcome(False, why)
            try:
                await self._session.start()
                outcome = await handler(arguments)
                await self._session.refresh_window()
                return outcome
            except BrowserError as exc:
                return BuiltinOutcome(False, str(exc))
            except CdpClosed:
                return BuiltinOutcome(False, "The browser was closed. Call the tool again to start it anew.")
            except CdpTimeout as exc:
                return BuiltinOutcome(False, f"The browser did not answer in time ({exc}). The page may be busy; try again.")
            except CdpError as exc:
                return BuiltinOutcome(False, f"The browser refused the action: {exc.message}")

    async def released(self) -> None:
        """Another commission may use the browser next: the uids handed out so far stop working."""
        async with self._lock:
            self._session.forget_snapshots()

    def window(self) -> dict[str, Any] | None:
        return self._session.window()

    async def aclose(self) -> None:
        async with self._lock:
            await self._session.close()

    # -- building answers --------------------------------------------------

    def _pages_section(self) -> str:
        from config.nekoclaw_settings import BROWSER_PAGE_URL_MAX_TOKENS

        selected = self._session.current()
        pages = self._session.pages()
        # The first ones and the selected one; a browser the user is using may have dozens open.
        shown = [page for index, page in enumerate(pages) if index < PAGES_LISTED or page is selected]
        lines = ["## Pages"]
        for page in shown:
            mark = " [selected]" if page is selected else ""
            # An address is as long as a page makes it.
            url = bounded((page.url or "about:blank").replace("\n", " ").replace("\r", " "), BROWSER_PAGE_URL_MAX_TOKENS)
            lines.append(f"{page.id}: {url}{mark}")
        if len(shown) < len(pages):
            lines.append(f"[{len(pages) - len(shown)} more pages are open and not listed here.]")
        return "\n".join(lines)

    async def _snapshot_section(self, snapshot: Snapshot, offset: int = 0, max_tokens: int | None = None) -> str:
        from config.nekoclaw_settings import BROWSER_SNAPSHOT_MAX_TOKENS

        max_tokens = BROWSER_SNAPSHOT_MAX_TOKENS if max_tokens is None else max_tokens
        # Laid out in a worker thread: a large page is tens of thousands of lines.
        lines = await asyncio.to_thread(format_lines, snapshot.root, verbose=snapshot.verbose)
        total = len(lines)
        offset = max(0, offset)
        parts = ["## Latest page snapshot"]
        unlisted = ""
        if snapshot.unlisted_frames:
            count = snapshot.unlisted_frames
            unlisted = (
                f"[{count} embedded page{'s' if count != 1 else ''} (iframe) on this page "
                f"{'are' if count != 1 else 'is'} not listed: of another site, or more than are read. "
                "What is in them cannot be read or acted on by uid.]"
            )
        if offset >= total:
            parts.append(f"[The snapshot has {total} lines; there is nothing after line {total}.]")
            if unlisted:
                parts.append(unlisted)
            return "\n".join(parts)
        text, shown = await asyncio.to_thread(cap_lines, lines[offset:], max_tokens)
        if offset:
            parts.append(f"[Lines {offset + 1} to {offset + shown} of {total}.]")
        parts.append(text)
        end = offset + shown
        if end < total:
            parts.append(
                f"[The snapshot is cut off here: {end} of {total} lines shown. To read on, call "
                f"browser_take_snapshot with offset={end}.]"
            )
        if unlisted:
            parts.append(unlisted)
        return "\n".join(parts)

    async def _answer(
        self,
        page: Page | None,
        lines: list[str],
        *,
        snapshot: bool = False,
        pages: bool = False,
        offset: int = 0,
        verbose: bool = False,
        images: tuple[BuiltinImage, ...] = (),
        acted: bool | None = None,
    ) -> BuiltinOutcome:
        """Put an answer together. ``acted``: something was done to the page (by default, when ``lines`` say so)."""
        acted = bool(lines) if acted is None else acted
        parts = list(lines)
        if page is not None and page.dialog is not None:
            parts.append(self._session.dialog_notice(page))
            snapshot = False  # The page answers nothing while the dialog is open.
        if pages:
            parts.append(self._pages_section())
        if snapshot and page is not None:
            try:
                taken = await self._session.take_snapshot(page, verbose=verbose)
            except (BrowserError, CdpError, CdpTimeout) as exc:
                if not acted:
                    raise
                # What was done is done. Answered as a failure, it would be
                # done again (a form sent twice); the page is read separately.
                reason = str(exc) if isinstance(exc, BrowserError) else f"{type(exc).__name__}: {exc}"
                parts.append(
                    "The page could not be read afterwards, so no snapshot is shown. What is said above was done: "
                    f"do not repeat it. Call browser_take_snapshot to read the page. ({reason})"
                )
            else:
                parts.append(await self._snapshot_section(taken, offset, await self._room_for_snapshot(parts)))
        return BuiltinOutcome(True, "\n".join(part for part in parts if part), images)

    @staticmethod
    async def _room_for_snapshot(parts: list[str]) -> int:
        """Return how many tokens the snapshot may take, after what the answer already says.

        The gateway cuts a whole result at ``GATEWAY_RESULT_MAX_TOKENS``,
        from its end: a snapshot that ran past it would lose its last lines
        and the note about where to read on.
        """
        from config.nekoclaw_settings import BROWSER_SNAPSHOT_MAX_TOKENS, GATEWAY_RESULT_MAX_TOKENS
        from utils.tokenize import count_tokens

        said = "\n".join(part for part in parts if part)
        used = await asyncio.to_thread(count_tokens, said) if said else 0
        # The notes that come with a snapshot (where it was cut, what is not listed) take about this much.
        return min(BROWSER_SNAPSHOT_MAX_TOKENS, max(200, GATEWAY_RESULT_MAX_TOKENS - used - 200))

    async def _after(self, page: Page, done: str, arguments: dict[str, Any], navigations: int, pages: int) -> BuiltinOutcome:
        """Answer an action: with a snapshot when asked for one, or when the page became another document."""
        if not self._session.is_open(page):
            # A button that closes its own window (a sign-in or consent popup).
            return await self._answer(None, [done, "The page it was on has closed."], pages=True)
        lines = [done]
        moved = page.navigations != navigations
        if moved and not arguments.get("includeSnapshot"):
            lines.append("The page changed to another document; its snapshot follows.")
        return await self._answer(
            page,
            lines,
            snapshot=bool(arguments.get("includeSnapshot")) or moved,
            pages=len(self._session.pages()) != pages,
        )

    # -- the tools -----------------------------------------------------------

    async def _navigate_page(self, arguments: dict[str, Any]) -> BuiltinOutcome:
        page = self._session.selected()
        kind = arguments.get("type") or ("url" if arguments.get("url") else None)
        if kind is None:
            raise BrowserError("Either URL or a type is required.")
        done = await self._session.navigate(page, str(kind), arguments.get("url"), _seconds(arguments))
        return await self._answer(page, [done], snapshot=True, pages=True)

    async def _take_snapshot(self, arguments: dict[str, Any]) -> BuiltinOutcome:
        page = self._session.selected()
        offset = arguments.get("offset")
        return await self._answer(
            page,
            [],
            snapshot=True,
            offset=int(offset) if isinstance(offset, (int, float)) and not isinstance(offset, bool) else 0,
            verbose=arguments.get("verbose") is True,
        )

    async def _click(self, arguments: dict[str, Any]) -> BuiltinOutcome:
        page = self._session.selected()
        uid = _text(arguments, "uid")
        before, pages = page.navigations, len(self._session.pages())
        double = arguments.get("dblClick") is True
        await self._session.click(page, uid, double=double)
        done = f"Successfully {'double clicked' if double else 'clicked'} on the element"
        return await self._after(page, done, arguments, before, pages)

    async def _hover(self, arguments: dict[str, Any]) -> BuiltinOutcome:
        page = self._session.selected()
        before, pages = page.navigations, len(self._session.pages())
        await self._session.hover(page, _text(arguments, "uid"))
        return await self._after(page, "Successfully hovered over the element", arguments, before, pages)

    async def _fill(self, arguments: dict[str, Any]) -> BuiltinOutcome:
        page = self._session.selected()
        value = arguments.get("value")
        if not isinstance(value, str):
            raise BrowserError("value is required")
        before, pages = page.navigations, len(self._session.pages())
        await self._session.fill_many(page, [(_text(arguments, "uid"), value)])
        return await self._after(page, "Successfully filled out the element", arguments, before, pages)

    async def _fill_form(self, arguments: dict[str, Any]) -> BuiltinOutcome:
        page = self._session.selected()
        elements = arguments.get("elements")
        if not isinstance(elements, list) or not elements:
            raise BrowserError("elements is required: a list of {uid, value}")
        wanted: list[tuple[str, str]] = []
        for item in elements:
            if not isinstance(item, dict) or not isinstance(item.get("uid"), str) or not isinstance(item.get("value"), str):
                raise BrowserError("every element needs a uid and a value, both text")
            wanted.append((item["uid"], item["value"]))
        before, pages = page.navigations, len(self._session.pages())
        await self._session.fill_many(page, wanted)
        return await self._after(page, "Successfully filled out the form", arguments, before, pages)

    async def _type_text(self, arguments: dict[str, Any]) -> BuiltinOutcome:
        page = self._session.selected()
        text = arguments.get("text")
        if not isinstance(text, str):
            raise BrowserError("text is required")
        submit = arguments.get("submitKey")
        before, pages = page.navigations, len(self._session.pages())
        await self._session.type_text(page, text, submit if isinstance(submit, str) and submit else None)
        done = f"Typed text{f' + {submit}' if isinstance(submit, str) and submit else ''}"
        return await self._after(page, done, arguments, before, pages)

    async def _press_key(self, arguments: dict[str, Any]) -> BuiltinOutcome:
        page = self._session.selected()
        key = _text(arguments, "key")
        before, pages = page.navigations, len(self._session.pages())
        await self._session.press_key(page, key)
        return await self._after(page, f"Successfully pressed key: {key}", arguments, before, pages)

    async def _take_screenshot(self, arguments: dict[str, Any]) -> BuiltinOutcome:
        from nekoclaw.builtin.desktop import fit_jpeg

        page = self._session.selected()
        uid = arguments.get("uid")
        full_page = arguments.get("fullPage") is True
        raw = await self._session.screenshot(page, uid=uid if isinstance(uid, str) and uid else None, full_page=full_page)
        encoded = await asyncio.to_thread(fit_jpeg, raw, SCREENSHOT_MAX_BYTES)
        if uid:
            what = f"Took a screenshot of node with uid \"{uid}\"."
        elif full_page:
            what = "Took a screenshot of the full current page."
        else:
            what = "Took a screenshot of the current page's viewport."
        image = BuiltinImage(base64.b64encode(encoded).decode("ascii"), "image/jpeg")
        return await self._answer(page, [what], images=(image,))

    async def _list_pages(self, arguments: dict[str, Any]) -> BuiltinOutcome:
        del arguments
        return await self._answer(self._session.current(), [], pages=True)

    async def _select_page(self, arguments: dict[str, Any]) -> BuiltinOutcome:
        page = await self._session.select_page(arguments.get("pageId"), bring_to_front=arguments.get("bringToFront") is True)
        return await self._answer(page, [], snapshot=True, pages=True)

    async def _new_page(self, arguments: dict[str, Any]) -> BuiltinOutcome:
        page = await self._session.new_page(
            _text(arguments, "url"), background=arguments.get("background") is True, timeout=_seconds(arguments)
        )
        # The tab is open whether or not its page can be read.
        return await self._answer(page, [], snapshot=True, pages=True, acted=True)

    async def _close_page(self, arguments: dict[str, Any]) -> BuiltinOutcome:
        await self._session.close_page(arguments.get("pageId"))
        return await self._answer(self._session.current(), [], pages=True)

    async def _wait_for(self, arguments: dict[str, Any]) -> BuiltinOutcome:
        import json

        page = self._session.selected()
        texts = arguments.get("text")
        if isinstance(texts, str):
            texts = [texts]
        if not isinstance(texts, list) or not texts or not all(isinstance(item, str) and item for item in texts):
            raise BrowserError("text is required: a non-empty list of texts")
        await self._session.wait_for(page, texts, _seconds(arguments))
        return await self._answer(
            page, [f"Element matching one of {json.dumps(texts, ensure_ascii=False)} found."], snapshot=True
        )

    async def _handle_dialog(self, arguments: dict[str, Any]) -> BuiltinOutcome:
        page = self._session.selected()
        action = arguments.get("action")
        if action not in ("accept", "dismiss"):
            raise BrowserError("action must be accept or dismiss")
        prompt = arguments.get("promptText")
        done = await self._session.handle_dialog(page, action == "accept", prompt if isinstance(prompt, str) else None)
        return await self._answer(page, [done])
