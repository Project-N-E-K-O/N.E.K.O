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
    <optgroup label="No longer made" disabled>
      <option value="iron">Iron Nail</option>
      <option value="steel">Steel Bolt</option>
    </optgroup>
    <option value="steel">Steel Bolt</option>
    <option value="zinc" disabled>Zinc Nut</option>
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

# A banner fixed over the top of the page, with a button under it; a select
# that asks before it changes; a panel taller than the window; a button that
# opens a window which closes itself.
COVERED = """<!doctype html><html><head><title>Covered</title></head><body style="margin:0">
<div id="banner" style="position:fixed;top:0;left:0;right:0;height:120px;background:#eee;z-index:10">
  We use cookies. <button type="button" onclick="document.getElementById('banner').remove()">Dismiss</button>
</div>
<button type="button" style="position:absolute;top:40px;left:300px"
  onclick="document.getElementById('out').textContent = 'Subscribed'">Subscribe</button>
<div style="margin-top:140px">
  <select aria-label="Product" onchange="if (!confirm('Change the product?')) this.value = 'brass'">
    <option value="brass">Brass Hinge</option>
    <option value="steel">Steel Bolt</option>
  </select>
  <button type="button" onclick="window.open('/popup')">Sign in</button>
  <p id="out"></p>
  <div role="button" tabindex="0" style="height:3000px;background:#ddf"
    onclick="document.getElementById('out').textContent = 'Panel pressed'">Tall panel</div>
</div>
</body></html>"""

POPUP = """<!doctype html><html><head><title>Sign in</title></head><body>
<button type="button" onclick="window.close()">Allow</button>
</body></html>"""

# A code block of many lines, a date nobody may change, and a page of another
# site embedded in this one (``localhost`` and ``127.0.0.1`` are two sites).
REPORT = """<!doctype html><html><head><title>Report</title></head><body>
<pre>__LOG__</pre>
<label>Delivery date <input type="date" name="delivery" value="2026-11-20" readonly></label>
<label>Note <input name="note" onfocus="alert('Sign in to leave a note')"></label>
<button type="button">Download</button>
<iframe src="__OTHER_SITE__/card" title="Card of another site"></iframe>
</body></html>"""

# Reaches for a port of this application in every way a page can.
REACHING = """<!doctype html><html><head><title>Reaching</title></head><body>
<p id="out">waiting</p>
<a href="__OWN__/by-link">Settings of the application</a>
<script>
fetch('__OWN__/by-fetch', {mode: 'no-cors'}).then(function () { return 'reached'; }, function () { return 'failed'; })
  .then(function (how) { document.getElementById('out').textContent = 'fetch ' + how; });
new Image().src = '__OWN__/by-image';
</script>
<iframe src="__OTHER_SITE__/reaching-frame" title="Frame of another site"></iframe>
</body></html>"""

REACHING_FRAME = """<!doctype html><html><head><title>Frame</title></head><body>
<script>fetch('__OWN__/by-frame-of-another-site', {mode: 'no-cors'}).catch(function () {});</script>
</body></html>"""

DRAFT = """<!doctype html><html><head><title>Draft</title></head><body>
<label>Message <input name="message"></label>
<script>
window.addEventListener('beforeunload', function (event) { event.preventDefault(); event.returnValue = ''; });
</script>
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
        elif url.path == "/covered":
            body = COVERED
        elif url.path == "/popup":
            body = POPUP
        elif url.path == "/draft":
            body = DRAFT
        elif url.path in ("/report", "/reaching", "/reaching-frame"):
            port = self.server.server_address[1]
            body = {"/report": REPORT, "/reaching": REACHING, "/reaching-frame": REACHING_FRAME}[url.path]
            body = (
                body.replace("__LOG__", "\n".join(f"line {number} of the log" for number in range(400)))
                .replace("__OTHER_SITE__", f"http://localhost:{port}")
                .replace("__OWN__", f"http://127.0.0.1:{getattr(self.server, 'own_port', 9)}")
            )
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


async def _chosen_product(tools: BrowserTools) -> str:
    """Say which option of the product select is chosen, and whether a person could have chosen it."""
    answer = await tools._session._send(
        tools._session.selected(),
        "Runtime.evaluate",
        {
            "expression": (
                "(() => { const option = document.querySelector('[name=product]').selectedOptions[0];"
                " const off = option.disabled || option.parentElement.disabled;"
                " return option.text.trim() + (off ? ', disabled' : ', can be chosen'); })()"
            ),
            "returnByValue": True,
        },
    )
    return answer["result"]["value"]


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

    # An option that cannot be chosen by hand (disabled itself, or by its group) is not chosen by filling in either.
    for unavailable in ("Zinc Nut", "Iron Nail"):
        refused = await call(tools, "fill", uid=uid_of(opened.text, "combobox", "Product"), value=unavailable)
        assert not refused.success and f'its option "{unavailable}" is disabled' in refused.text, refused.text
    # Of the two options by the name "Steel Bolt", which also have the same value, the one that can be
    # chosen was, not the first: that one is disabled, and a form sent with it leaves the field out.
    assert await _chosen_product(tools) == "Steel Bolt, can be chosen"
    # The same when the option itself is clicked.
    assert (await call(tools, "fill", uid=uid_of(opened.text, "combobox", "Product"), value="Brass Hinge")).success
    assert await _chosen_product(tools) == "Brass Hinge, can be chosen"
    # (The snapshot lists the one that can be chosen; the options of the disabled group are not in it.)
    assert (await call(tools, "click", uid=uid_of(filled.text, "option", "Steel Bolt"))).success
    assert await _chosen_product(tools) == "Steel Bolt, can be chosen"

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
    assert clicked.success and '# Open dialog\nThe page opened an alert dialog. Its text, written by the page:\n"Draft saved"\n' in clicked.text
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


async def test_what_lies_over_an_element_is_not_clicked_in_its_place(tools: BrowserTools, site: str) -> None:
    opened = await call(tools, "navigate_page", url=site + "/covered")
    assert opened.success, opened.text
    subscribe = uid_of(opened.text, "button", "Subscribe")
    covered = await call(tools, "click", uid=subscribe)
    assert not covered.success
    assert f"The element with uid {subscribe} is covered by div#banner" in covered.text, covered.text
    # The banner did not get the click either: it is still there, and nothing was subscribed to.
    still = await call(tools, "take_snapshot")
    assert 'button "Dismiss"' in still.text and "Subscribed" not in still.text

    assert (await call(tools, "click", uid=uid_of(opened.text, "button", "Dismiss"))).success
    clicked = await call(tools, "click", uid=subscribe, includeSnapshot=True)
    assert clicked.success, clicked.text
    assert "Subscribed" in clicked.text and 'button "Dismiss"' not in clicked.text

    # Taller than the window: the click goes to the part of it that can be seen.
    pressed = await call(tools, "click", uid=uid_of(opened.text, "button", "Tall panel"), includeSnapshot=True)
    assert pressed.success, pressed.text
    assert "Panel pressed" in pressed.text


async def test_a_select_that_asks_before_it_changes_and_a_window_that_closes_itself(tools: BrowserTools, site: str) -> None:
    opened = await call(tools, "navigate_page", url=site + "/covered")
    assert (await call(tools, "click", uid=uid_of(opened.text, "button", "Dismiss"))).success
    started = time.monotonic()
    filled = await call(tools, "fill", uid=uid_of(opened.text, "combobox", "Product"), value="Steel Bolt")
    # Not after the thirty seconds a command to the page is given.
    assert time.monotonic() - started < 10.0
    assert filled.success, filled.text
    assert filled.text.startswith(
        "Successfully filled out the element\n# Open dialog\nThe page opened a confirm dialog. "
        'Its text, written by the page:\n"Change the product?"\n'
    )
    assert (await call(tools, "handle_dialog", action="accept")).success
    shown = await call(tools, "take_snapshot")
    assert re.search(r'combobox "Product".*value="Steel Bolt"', shown.text)

    popped = await call(tools, "click", uid=uid_of(opened.text, "button", "Sign in"))
    assert popped.success, popped.text
    popup = re.search(rf"\n(\d+): {re.escape(site)}/popup", popped.text)
    assert popup is not None, popped.text
    inside = await call(tools, "select_page", pageId=int(popup.group(1)))
    assert inside.success, inside.text
    started = time.monotonic()
    allowed = await call(tools, "click", uid=uid_of(inside.text, "button", "Allow"))
    assert time.monotonic() - started < 10.0
    assert allowed.success, allowed.text
    assert allowed.text.startswith("Successfully clicked on the element\nThe page it was on has closed.\n## Pages\n")
    assert "/popup" not in allowed.text and f"{site}/covered [selected]" in allowed.text


async def test_a_page_that_asks_before_it_is_left_answers_at_once(tools: BrowserTools, site: str) -> None:
    opened = await call(tools, "navigate_page", url=site + "/draft")
    message = uid_of(opened.text, "textbox", "Message")
    # The browser only asks once the user has done something on the page.
    assert (await call(tools, "click", uid=message)).success
    assert (await call(tools, "type_text", text="half a letter")).success
    started = time.monotonic()
    leaving = await call(tools, "navigate_page", url=site + "/terms")
    assert time.monotonic() - started < 10.0
    assert leaving.success, leaving.text
    assert leaving.text.startswith(
        f"The page asks for confirmation before it is left; {site}/terms is not loaded yet.\n"
        "# Open dialog\nThe page opened a beforeunload dialog"
    )
    assert (await call(tools, "handle_dialog", action="accept")).success
    found = await call(tools, "wait_for", text=["Returns within 30 days."], timeout=10000)
    assert found.success and 'heading "Terms of sale"' in found.text


class _Own(http.server.BaseHTTPRequestHandler):
    """Stands in for one of this application's own services: records who reached it."""

    def do_GET(self) -> None:  # noqa: N802 - the name the base class calls
        self.server.reached.append(self.path)  # type: ignore[attr-defined]
        body = b"<!doctype html><title>Own</title><h1>Settings of the application</h1>"
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    do_POST = do_GET  # noqa: N815

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002 - the signature of the base class
        return None


async def test_a_code_block_a_locked_date_a_field_that_asks_on_focus_and_a_frame_of_another_site(
    tools: BrowserTools, site: str, monkeypatch: Any
) -> None:
    import config.nekoclaw_settings as settings

    monkeypatch.setattr(settings, "BROWSER_SNAPSHOT_MAX_TOKENS", 300)
    opened = await call(tools, "navigate_page", url=site + "/report")
    assert opened.success, opened.text
    # The code block is hundreds of lines of text in one element: the snapshot
    # is cut all the same, and says where to read on.
    assert re.search(r"\[The snapshot is cut off here: \d+ of \d+ lines shown\. To read on, call browser_take_snapshot with offset=\d+\.\]", opened.text)
    # The page of the other site is not in the snapshot, and the snapshot says so.
    assert "[1 embedded page (iframe) on this page is not listed:" in opened.text
    assert "Card number" not in opened.text

    monkeypatch.setattr(settings, "BROWSER_SNAPSHOT_MAX_TOKENS", 3000)
    whole = await call(tools, "take_snapshot")
    assert "line 0 of the log\\nline 1 of the log" in whole.text or "line 399 of the log" in whole.text
    date = uid_of(whole.text, "Date", "Delivery date") if 'Date "Delivery date"' in whole.text else None
    if date is None:
        found = re.search(r'uid=(\S+) \S+ "Delivery date"', whole.text)
        assert found is not None, whole.text
        date = found.group(1)
    locked = await call(tools, "fill", uid=date, value="2027-01-01")
    assert not locked.success and "the element is read-only" in locked.text
    session = tools.session
    value = await session._send(
        session.selected(), "Runtime.evaluate", {"expression": "document.querySelector('[name=delivery]').value", "returnByValue": True}
    )
    assert value["result"]["value"] == "2026-11-20"

    # A field that opens a dialog when it gets the focus: nothing was typed, and that is what is said.
    note = uid_of(whole.text, "textbox", "Note")
    started = time.monotonic()
    asked = await call(tools, "fill", uid=note, value="leave at the door")
    assert time.monotonic() - started < 10.0
    assert not asked.success
    assert f"A dialog opened when the element with uid {note} was given the focus, before anything was typed into it." in asked.text
    assert '"Sign in to leave a note"' in asked.text
    assert (await call(tools, "handle_dialog", action="accept")).success
    typed = await session._send(
        session.selected(), "Runtime.evaluate", {"expression": "document.querySelector('[name=note]').value", "returnByValue": True}
    )
    assert typed["result"]["value"] == ""


async def test_the_browser_is_kept_from_the_ports_of_this_application(tmp_path: Path) -> None:
    own = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Own)
    own.reached = []  # type: ignore[attr-defined]
    served = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Site)
    served.own_port = own.server_address[1]  # type: ignore[attr-defined]
    threads = [threading.Thread(target=server.serve_forever, daemon=True) for server in (own, served)]
    for thread in threads:
        thread.start()
    own_url, site = f"http://127.0.0.1:{own.server_address[1]}", f"http://127.0.0.1:{served.server_address[1]}"
    store = BrowserSettingsStore(tmp_path / "nekoclaw")
    store.save(BrowserSettings(headless=True))
    made = BrowserTools(BrowserSession(settings=store, own_ports=lambda: {own.server_address[1]}))
    profile = store.profile_dir()
    try:
        # The task agent cannot send a page there.
        direct = await call(made, "navigate_page", url=own_url + "/config")
        assert not direct.success and "that port belongs to this application's own services" in direct.text
        other_name = await call(made, "new_page", url=f"http://localhost:{own.server_address[1]}/config")
        assert not other_name.success and "this application's own services" in other_name.text
        # Nor with more slashes than two, or backslashes, after the scheme.
        for written in ("http:///127.0.0.1:{port}/config", "http://\\\\127.0.0.1:{port}/config"):
            more = await call(made, "navigate_page", url=written.format(port=own.server_address[1]))
            assert not more.success and "this application's own services" in more.text, more.text

        # A page cannot reach it either: by a request of its own, by a picture,
        # from a frame of another site, or by a link that is clicked. (The
        # address is written with a slash too many: a browser opens it as it
        # opens the one with two, which is why the check above reads it so.)
        opened = await call(made, "navigate_page", url=site.replace("http://", "http:///") + "/reaching")
        assert opened.success, opened.text
        found = await call(made, "wait_for", text=["fetch failed", "fetch reached"], timeout=15000)
        assert found.success and "fetch failed" in found.text, found.text
        await asyncio.sleep(1.0)
        clicked = await call(made, "click", uid=uid_of(found.text, "link", "Settings of the application"), includeSnapshot=True)
        # The click was made; where it led was failed by the browser, not loaded.
        assert clicked.success, clicked.text
        assert "<h1>Settings of the application</h1>" not in clicked.text and 'heading "Settings of the application"' not in clicked.text
        await asyncio.sleep(1.0)
        assert own.reached == [], own.reached
        # A tab that shows one of its pages is not among the pages listed.
        listed = await call(made, "list_pages")
        assert own_url not in listed.text
    finally:
        try:
            await made.aclose()
            deadline = time.monotonic() + 15.0
            while not await asyncio.to_thread(_browser_gone, profile) and time.monotonic() < deadline:
                await asyncio.sleep(0.2)
        finally:
            await asyncio.to_thread(launcher.kill_tree, await asyncio.to_thread(launcher.browser_process_on, profile))
            for server in (own, served):
                server.shutdown()
                server.server_close()
            for thread in threads:
                thread.join(5)
