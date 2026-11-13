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

"""The browser tools against a real browser. Not run unless asked for.

Set ``NEKO_TEST_REAL_BROWSER=1`` on a computer with Chrome, Edge, Brave or
Chromium installed. The browser is started without a window, on a profile
directory below the test's temporary folder, and browses a small site served
from this process on 127.0.0.1. Every test checks at its end that the
browser process is gone.
"""

from __future__ import annotations

import asyncio
import base64
import http.server
import os
import re
import threading
import time
import urllib.parse
from io import BytesIO
from pathlib import Path
from typing import Any

import pytest

from nekoclaw.builtin.browser import launcher
from nekoclaw.builtin.browser.session import BrowserError, BrowserSession
from nekoclaw.builtin.browser.settings import BrowserSettings, BrowserSettingsStore
from nekoclaw.builtin.browser.tools import BrowserTools
from nekoclaw.host.capabilities import BuiltinOutcome

_ASKED_FOR = os.environ.get("NEKO_TEST_REAL_BROWSER") == "1"

pytestmark = pytest.mark.skipif(
    not (_ASKED_FOR and launcher.find_browsers()),
    reason="drives a real browser: set NEKO_TEST_REAL_BROWSER=1 on a computer that has one installed",
)

FORM = """<!doctype html><html><head><title>Order form</title></head><body>
<h1>Order a part</h1>
<form action="/submit" method="get">
  <label>Your name <input name="name"></label>
  <label>Email <input name="email" value="old@example.com"></label>
  <select name="product" aria-label="Product">
    <option value="brass">Brass Hinge</option>
    <option value="steel">Steel Bolt</option>
  </select>
  <input type="checkbox" name="gift" id="gift"><label for="gift">Gift wrap</label>
  <button type="submit">Send order</button>
</form>
<button type="button" onclick="alert('Draft saved')">Save draft</button>
<button type="button" onclick="setTimeout(function () { document.getElementById('late').textContent = 'Stock checked: 12 left'; }, 300)">Check stock</button>
<p id="late"></p>
<a href="/terms" target="_blank">Terms</a>
<iframe src="/card" title="Card"></iframe>
</body></html>"""

CARD = """<!doctype html><html><head><title>Card</title></head><body>
<label>Card number <input name="card"></label>
</body></html>"""

TERMS = """<!doctype html><html><head><title>Terms</title></head><body>
<h1>Terms of sale</h1><p>Returns within 30 days.</p>
</body></html>"""


class _Site(http.server.BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802 - the name the base class calls
        url = urllib.parse.urlsplit(self.path)
        if url.path == "/":
            body = FORM
        elif url.path == "/card":
            body = CARD
        elif url.path == "/terms":
            body = TERMS
        elif url.path == "/submit":
            query = urllib.parse.parse_qs(url.query)
            name = (query.get("name") or [""])[0]
            product = (query.get("product") or [""])[0]
            gift = "with" if query.get("gift") else "without"
            email = (query.get("email") or [""])[0]
            body = (
                "<!doctype html><html><head><title>Thanks</title></head><body><h1>Order received</h1>"
                f"<p>{name} ({email}) ordered {product} {gift} gift wrap.</p><a href='/'>Back to the form</a></body></html>"
            )
        else:
            self.send_error(404)
            return
        encoded = body.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002 - the signature of the base class
        return None


@pytest.fixture
def site() -> Any:
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Site)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(5)


def _browser_gone(profile: Path) -> bool:
    return launcher.browser_process_on(profile) is None and launcher.running_endpoint(profile) is None


@pytest.fixture
async def tools(tmp_path: Path) -> Any:
    store = BrowserSettingsStore(tmp_path / "nekoclaw")
    store.save(BrowserSettings(headless=True))
    made = BrowserTools(BrowserSession(settings=store))
    profile = store.profile_dir()
    try:
        yield made
    finally:
        try:
            await made.aclose()
            deadline = time.monotonic() + 15.0
            while not await asyncio.to_thread(_browser_gone, profile) and time.monotonic() < deadline:
                await asyncio.sleep(0.2)
            gone = await asyncio.to_thread(_browser_gone, profile)
        finally:
            # Whatever went wrong above, no browser of this test stays behind.
            await asyncio.to_thread(launcher.kill_tree, await asyncio.to_thread(launcher.browser_process_on, profile))
        assert gone, "the browser the test started was still running after it was closed"


async def call(tools: BrowserTools, name: str, **arguments: Any) -> BuiltinOutcome:
    return await asyncio.wait_for(tools.call("browser_" + name, arguments), 60.0)


def uid_of(text: str, role: str, name: str) -> str:
    found = re.search(rf'uid=(\S+) {re.escape(role)} "{re.escape(name)}"', text)
    assert found is not None, f"no {role} named {name!r} in:\n{text}"
    return found.group(1)


async def test_a_form_is_filled_in_sent_and_its_result_read(tools: BrowserTools, site: str) -> None:
    opened = await call(tools, "navigate_page", url=site + "/")
    assert opened.success, opened.text
    assert opened.text.startswith(f"Successfully navigated to {site}/.\n## Pages\n")
    assert "## Latest page snapshot" in opened.text and 'RootWebArea "Order form"' in opened.text
    name = uid_of(opened.text, "textbox", "Your name")
    email = uid_of(opened.text, "textbox", "Email")
    form = [
        {"uid": name, "value": "Ada Lovelace"},
        {"uid": email, "value": "ada@example.com"},
        {"uid": uid_of(opened.text, "combobox", "Product"), "value": "Steel Bolt"},
        {"uid": uid_of(opened.text, "checkbox", "Gift wrap"), "value": "true"},
    ]
    filled = await call(tools, "fill_form", elements=form, includeSnapshot=True)
    assert filled.success, filled.text
    assert f'uid={name} textbox "Your name"' in filled.text and 'value="Ada Lovelace"' in filled.text
    assert 'value="ada@example.com"' in filled.text and 'value="Steel Bolt"' in filled.text
    assert re.search(r'checkbox "Gift wrap".* checked', filled.text)

    sent = await call(tools, "click", uid=uid_of(opened.text, "button", "Send order"))
    assert sent.success, sent.text
    assert "The page changed to another document; its snapshot follows." in sent.text
    assert "Ada Lovelace (ada@example.com) ordered steel with gift wrap." in sent.text

    picture = await call(tools, "take_screenshot")
    assert picture.success and picture.text == "Took a screenshot of the current page's viewport."
    (image,) = picture.images
    from PIL import Image

    with Image.open(BytesIO(base64.b64decode(image.data))) as decoded:
        assert decoded.format == "JPEG" and decoded.width > 100 and decoded.height > 100

    # A uid of the form, which is no longer the document shown.
    stale = await call(tools, "click", uid=name)
    assert not stale.success
    assert "browser_take_snapshot" in stale.text


async def test_a_uid_of_a_document_the_page_left_on_its_own_is_refused(tools: BrowserTools, site: str) -> None:
    opened = await call(tools, "navigate_page", url=site + "/")
    name = uid_of(opened.text, "textbox", "Your name")
    session = tools.session
    page = session.selected()
    before = page.navigations
    # The page goes elsewhere without any tool having asked for it.
    await session._send(page, "Runtime.evaluate", {"expression": "location.href = '/terms'", "returnByValue": True})
    deadline = time.monotonic() + 15.0
    while (page.navigations == before or page.loading) and time.monotonic() < deadline:
        await asyncio.sleep(0.05)
    assert page.navigations == before + 1
    with pytest.raises(BrowserError, match="no longer exists on the page"):
        await session.fill(page, name, "Ada")
    fresh = await call(tools, "take_snapshot")
    assert 'heading "Terms of sale"' in fresh.text and "Your name" not in fresh.text


async def test_a_dialog_is_reported_and_handled_and_text_that_comes_later_is_waited_for(tools: BrowserTools, site: str) -> None:
    opened = await call(tools, "navigate_page", url=site + "/")
    started = time.monotonic()
    clicked = await call(tools, "click", uid=uid_of(opened.text, "button", "Save draft"))
    # The page is blocked by its dialog; the call came back all the same.
    assert time.monotonic() - started < 15.0
    assert clicked.success and "# Open dialog\nalert: Draft saved\n" in clicked.text
    blocked = await call(tools, "take_screenshot")
    assert not blocked.success and "browser_handle_dialog" in blocked.text
    handled = await call(tools, "handle_dialog", action="accept")
    assert handled == BuiltinOutcome(True, "Successfully accepted the dialog.")

    assert (await call(tools, "click", uid=uid_of(opened.text, "button", "Check stock"))).success
    found = await call(tools, "wait_for", text=["Stock checked"], timeout=10000)
    assert found.success and "Stock checked: 12 left" in found.text


async def test_history_tabs_keys_and_an_embedded_document(tools: BrowserTools, site: str) -> None:
    opened = await call(tools, "navigate_page", url=site + "/")
    # The document inside the iframe is listed, and can be typed into.
    card = uid_of(opened.text, "textbox", "Card number")
    assert (await call(tools, "fill", uid=card, value="4111")).success
    assert (await call(tools, "type_text", text=" 1111")).success
    shown = await call(tools, "take_snapshot")
    assert re.search(r'textbox "Card number".*value="4111 1111"', shown.text)

    # Another address and back again; neither waits out the navigation timeout.
    there = await call(tools, "navigate_page", url=site + "/terms")
    assert 'heading "Terms of sale"' in there.text
    started = time.monotonic()
    back = await call(tools, "navigate_page", type="back")
    forward = await call(tools, "navigate_page", type="forward")
    assert time.monotonic() - started < 15.0
    assert back.text.startswith("Successfully navigated back.\n") and 'RootWebArea "Order form"' in back.text
    assert forward.text.startswith("Successfully navigated forward.\n") and 'RootWebArea "Terms"' in forward.text
    assert "still loading" not in back.text + forward.text

    # A second tab, selected, listed and closed again.
    tab = await call(tools, "new_page", url=site + "/")
    assert tab.success, tab.text
    # Its number is whatever comes next: a browser may list pages of its own besides the tabs.
    selected = re.search(rf"\n(\d+): {re.escape(site)}/ \[selected\]\n", tab.text)
    assert selected is not None, tab.text
    number = int(selected.group(1))
    assert number > 1
    assert (await call(tools, "press_key", key="Tab")).success
    closed = await call(tools, "close_page", pageId=number)
    assert closed.success and f"\n{number}: " not in closed.text
    assert f"1: {site}/terms [selected]" in closed.text
    whole = await call(tools, "take_screenshot", fullPage=True)
    assert whole.success and len(whole.images) == 1
