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

"""The browser tools a task agent calls: what each call answers, against a browser made of Python objects."""

from __future__ import annotations

import asyncio
import base64
import random
import re
from io import BytesIO
from typing import Any

import pytest
from PIL import Image

from config.prompts.prompts_nekoclaw_builtin import BROWSER_TOOL_NAMES, BROWSER_TOOLS
from nekoclaw.builtin.browser import tools as tools_module
from nekoclaw.builtin.browser.cdp import CdpClosed, CdpError, CdpTimeout
from nekoclaw.builtin.browser.tools import SCREENSHOT_MAX_BYTES, TOOL_PREFIX, BrowserTools, cap_lines
from nekoclaw.host.capabilities import BuiltinOutcome
from tests.unit.nekoclaw.fake_cdp_browser import (
    ORDER,
    SHOP,
    SLOW,
    TERMS,
    THANKS,
    BrowserWorld,
    Document,
    el,
    serve_shop,
)
from utils.tokenize import count_tokens

ORDER_SNAPSHOT = [
    'uid=1_0 RootWebArea "Order form"',
    '  uid=1_1 heading "Order a part" level="1"',
    '  uid=1_2 textbox "Your name"',
    '  uid=1_3 textbox "Email" value="old@example.com"',
    '  uid=1_4 checkbox "Gift wrap"',
    '  uid=1_5 checkbox "Newsletter" checked',
    '  uid=1_6 combobox "Product" value="Brass Hinge"',
    '    uid=1_7 option "Brass Hinge"',
    '    uid=1_8 option "Steel Bolt"',
    '  uid=1_9 button "Send"',
    '  uid=1_10 button "Save draft"',
    '  uid=1_11 link "Terms"',
    '  uid=1_12 button "Hidden"',
]
SNAPSHOT_HEADER = "## Latest page snapshot"
NOTICE = (
    "# Open dialog\nThe page opened an alert dialog. Its text, written by the page:\n"
    '"Draft saved"\nCall browser_handle_dialog to handle it before continuing.'
)
CUT_OFF = re.compile(
    r"\[The snapshot is cut off here: (\d+) of (\d+) lines shown\. To read on, call browser_take_snapshot with offset=(\d+)\.\]"
)


@pytest.fixture
def world(monkeypatch: Any, tmp_path: Any) -> BrowserWorld:
    made = BrowserWorld(monkeypatch, tmp_path)
    serve_shop(made.browser)
    return made


@pytest.fixture
async def tools(world: BrowserWorld) -> Any:
    made = BrowserTools(world.session())
    yield made
    await made.aclose()
    # What was left waiting on the closed connection ends now.
    for _ in range(5):
        await asyncio.sleep(0)


async def call(tools: BrowserTools, name: str, **arguments: Any) -> BuiltinOutcome:
    """Call one browser tool by its short name; bounded, so a call that hangs fails soon."""
    return await asyncio.wait_for(tools.call(TOOL_PREFIX + name, arguments), 10.0)


async def on_the_order_form(tools: BrowserTools) -> None:
    outcome = await call(tools, "navigate_page", url=ORDER)
    assert outcome.success, outcome.text


def uid_of(name: str) -> str:
    """Return the uid the order form's snapshot gives the element of that name."""
    (line,) = [item for item in ORDER_SNAPSHOT if f'"{name}"' in item]
    return line.split()[0].removeprefix("uid=")


def snapshot_of(text: str) -> list[str]:
    """Return the lines below the snapshot header of an answer."""
    assert SNAPSHOT_HEADER in text, text
    return text.split(SNAPSHOT_HEADER + "\n", 1)[1].split("\n")


# ---------------------------------------------------------------------------
# Which tools there are
# ---------------------------------------------------------------------------


def test_every_defined_tool_has_a_handler_and_every_handler_a_definition(world: BrowserWorld) -> None:
    built = BrowserTools(world.session())
    handled = {TOOL_PREFIX + name for name in built._handlers}
    assert handled == set(BROWSER_TOOL_NAMES)
    assert len(BROWSER_TOOLS) == len(BROWSER_TOOL_NAMES) == len(set(BROWSER_TOOL_NAMES)) == 15
    assert all(name.startswith(TOOL_PREFIX) for name in BROWSER_TOOL_NAMES)
    assert TOOL_PREFIX == "browser_"


@pytest.mark.parametrize("name", ["browser_fly", "navigate_page", "browser_", "", "computer", "browser_navigate_page "])
async def test_an_unknown_tool_is_a_failure_and_starts_no_browser(tools: BrowserTools, world: BrowserWorld, name: str) -> None:
    outcome = await tools.call(name, {"url": ORDER})
    assert outcome == BuiltinOutcome(False, f"unknown tool {name}")
    assert world.launched == [] and world.connected == []


async def test_the_browser_is_started_by_the_first_call_and_kept_for_the_next(tools: BrowserTools, world: BrowserWorld) -> None:
    assert world.launched == []
    assert (await call(tools, "list_pages")).success
    assert (await call(tools, "navigate_page", url=ORDER)).success
    assert (await call(tools, "take_snapshot")).success
    assert len(world.launched) == 1 and len(world.connected) == 1


# ---------------------------------------------------------------------------
# One call at a time
# ---------------------------------------------------------------------------


async def test_calls_are_carried_out_one_after_the_other(tools: BrowserTools, world: BrowserWorld) -> None:
    await on_the_order_form(tools)
    held = world.browser.hold("Page.navigate")
    order: list[str] = []
    first = asyncio.ensure_future(tools.call("browser_navigate_page", {"url": THANKS}))
    first.add_done_callback(lambda _task: order.append("first"))
    await asyncio.wait_for(held.reached.wait(), 5.0)
    sent = world.browser.mark()
    second = asyncio.ensure_future(tools.call("browser_take_snapshot", {}))
    second.add_done_callback(lambda _task: order.append("second"))
    # The second call gets every chance to run, and does not: the first is still under way.
    for _ in range(50):
        await asyncio.sleep(0)
    assert not first.done() and not second.done()
    assert world.browser.since(sent) == []
    held.release()
    one, two = await asyncio.wait_for(asyncio.gather(first, second), 10.0)
    assert order == ["first", "second"]
    # The second began after the first had ended, and saw the page the first left behind.
    methods = world.browser.methods(sent)
    read = [index for index, method in enumerate(methods) if method == "Accessibility.getFullAXTree"]
    located = [index for index, method in enumerate(methods) if method == "Browser.getWindowForTarget"]
    assert len(read) == len(located) == 2
    assert read[0] < located[0] < read[1] < located[1]
    assert snapshot_of(one.text)[0] == snapshot_of(two.text)[0] == 'uid=2_0 RootWebArea "Thanks"'


async def test_letting_go_of_the_browser_waits_for_the_call_under_way(tools: BrowserTools, world: BrowserWorld) -> None:
    await on_the_order_form(tools)
    held = world.browser.hold("Input.dispatchMouseEvent", type="mouseReleased")
    click = asyncio.ensure_future(tools.call("browser_click", {"uid": uid_of("Gift wrap")}))
    await asyncio.wait_for(held.reached.wait(), 5.0)
    released = asyncio.ensure_future(tools.released())
    for _ in range(50):
        await asyncio.sleep(0)
    assert not released.done()
    held.release()
    outcome = await asyncio.wait_for(click, 10.0)
    await asyncio.wait_for(released, 10.0)
    assert outcome == BuiltinOutcome(True, "Successfully clicked on the element")


# ---------------------------------------------------------------------------
# The layout of an answer
# ---------------------------------------------------------------------------


async def test_an_answer_is_the_sentence_the_pages_and_the_snapshot(tools: BrowserTools, world: BrowserWorld) -> None:
    outcome = await call(tools, "navigate_page", url=ORDER)
    assert outcome.success and outcome.images == ()
    assert outcome.text == "\n".join(
        [f"Successfully navigated to {ORDER}.", "## Pages", f"1: {ORDER} [selected]", SNAPSHOT_HEADER, *ORDER_SNAPSHOT]
    )


async def test_the_page_list_marks_the_selected_page_and_shows_a_blank_one_as_such(tools: BrowserTools, world: BrowserWorld) -> None:
    outcome = await call(tools, "list_pages")
    assert outcome == BuiltinOutcome(True, "## Pages\n1: about:blank [selected]")
    world.browser.add_page(TERMS)
    await call(tools, "navigate_page", url=ORDER)
    outcome = await call(tools, "list_pages")
    assert outcome.text == f"## Pages\n1: {ORDER} [selected]\n2: {TERMS}"
    # A page whose address is not known yet is listed as the blank page.
    world.browser.add_page("")
    assert (await call(tools, "list_pages")).text.endswith("\n3: about:blank")


async def test_navigating_goes_back_forward_and_reloads_and_always_answers_with_the_snapshot(
    tools: BrowserTools, world: BrowserWorld
) -> None:
    await call(tools, "navigate_page", url=ORDER)
    there = await call(tools, "navigate_page", type="url", url=THANKS)
    assert there.text.startswith(f"Successfully navigated to {THANKS}.\n## Pages\n1: {THANKS} [selected]\n{SNAPSHOT_HEADER}\n")
    back = await call(tools, "navigate_page", type="back")
    assert back.text.startswith(f"Successfully navigated back.\n## Pages\n1: {ORDER} [selected]\n{SNAPSHOT_HEADER}\n")
    assert 'RootWebArea "Order form"' in snapshot_of(back.text)[0]
    forward = await call(tools, "navigate_page", type="forward")
    assert forward.text.startswith("Successfully navigated forward.\n")
    assert 'RootWebArea "Thanks"' in snapshot_of(forward.text)[0]
    again = await call(tools, "navigate_page", type="reload")
    assert again.text.startswith("Successfully reloaded the page.\n")
    assert 'RootWebArea "Thanks"' in snapshot_of(again.text)[0]


async def test_navigating_needs_an_address_or_a_kind(tools: BrowserTools, world: BrowserWorld) -> None:
    assert await call(tools, "navigate_page") == BuiltinOutcome(False, "Either URL or a type is required.")
    assert await call(tools, "navigate_page", type="url") == BuiltinOutcome(False, "A URL is required to navigate.")
    outcome = await call(tools, "navigate_page", url="file:///etc/passwd")
    assert not outcome.success and "cannot be opened: only http:// and https://" in outcome.text
    assert world.browser.of("Page.navigate") == []


async def test_the_timeout_of_a_tool_is_given_in_milliseconds(tools: BrowserTools, world: BrowserWorld) -> None:
    outcome = await call(tools, "navigate_page", url=SLOW, timeout=50)
    assert outcome.success
    assert outcome.text.startswith(f"Successfully navigated to {SLOW}. The page was still loading after 0s;")
    assert 'heading "Loading"' in outcome.text


async def test_a_new_page_answers_with_the_pages_and_its_snapshot(tools: BrowserTools, world: BrowserWorld) -> None:
    await on_the_order_form(tools)
    outcome = await call(tools, "new_page", url=TERMS)
    assert outcome.success
    assert outcome.text == "\n".join(
        [
            "## Pages",
            f"1: {ORDER}",
            f"2: {TERMS} [selected]",
            SNAPSHOT_HEADER,
            'uid=2_0 RootWebArea "Terms"',
            '  uid=2_1 heading "Terms of sale" level="1"',
            '  uid=2_2 "Returns within 30 days."',
        ]
    )
    assert await call(tools, "new_page") == BuiltinOutcome(False, "url is required")
    assert world.browser.of("Target.createTarget")[-1].params == {"url": "about:blank", "background": False}
    await call(tools, "new_page", url=SHOP, background=True)
    assert world.browser.of("Target.createTarget")[-1].params == {"url": "about:blank", "background": True}


async def test_selecting_a_page_answers_with_the_pages_and_the_snapshot_of_that_page(tools: BrowserTools, world: BrowserWorld) -> None:
    await on_the_order_form(tools)
    await call(tools, "new_page", url=TERMS)
    outcome = await call(tools, "select_page", pageId=1)
    assert outcome.success
    assert outcome.text.startswith(f"## Pages\n1: {ORDER} [selected]\n2: {TERMS}\n{SNAPSHOT_HEADER}\n")
    assert snapshot_of(outcome.text) == ORDER_SNAPSHOT
    # What is done next is done on that page.
    await call(tools, "click", uid=uid_of("Gift wrap"))
    assert world.browser.page("target-1").find("Gift wrap").checked is True
    front = await call(tools, "select_page", pageId=2, bringToFront=True)
    assert front.success and world.browser.page("target-2").brought_to_front == 1
    missing = await call(tools, "select_page", pageId=9)
    assert missing == BuiltinOutcome(False, "There is no page 9. Call browser_list_pages to see the open pages.")


async def test_closing_a_page_answers_with_the_pages_that_are_left(tools: BrowserTools, world: BrowserWorld) -> None:
    await on_the_order_form(tools)
    assert await call(tools, "close_page", pageId=1) == BuiltinOutcome(False, "The last open page cannot be closed.")
    await call(tools, "new_page", url=TERMS)
    outcome = await call(tools, "close_page", pageId=2)
    assert outcome == BuiltinOutcome(True, f"## Pages\n1: {ORDER} [selected]")
    assert list(world.browser.pages) == ["target-1"]


async def test_a_snapshot_on_request_is_the_snapshot_alone(tools: BrowserTools, world: BrowserWorld) -> None:
    await on_the_order_form(tools)
    outcome = await call(tools, "take_snapshot")
    assert outcome == BuiltinOutcome(True, "\n".join([SNAPSHOT_HEADER, *ORDER_SNAPSHOT]))


async def test_a_verbose_snapshot_lists_what_the_brief_one_leaves_out(tools: BrowserTools, world: BrowserWorld) -> None:
    await call(tools, "navigate_page", url=TERMS)
    brief = snapshot_of((await call(tools, "take_snapshot")).text)
    verbose = snapshot_of((await call(tools, "take_snapshot", verbose=True)).text)
    assert brief == ['uid=1_0 RootWebArea "Terms"', '  uid=1_1 heading "Terms of sale" level="1"', '  uid=1_2 "Returns within 30 days."']
    # The wrapper is new to the page's uids, and numbered after the third snapshot taken here.
    assert verbose == [
        'uid=1_0 RootWebArea "Terms"',
        '  uid=1_1 heading "Terms of sale" level="1"',
        "  uid=3_0 generic",
        '    uid=1_2 "Returns within 30 days."',
    ]
    # Anything but a real true is the brief one.
    assert snapshot_of((await call(tools, "take_snapshot", verbose="yes")).text) == brief


# ---------------------------------------------------------------------------
# Actions: when the answer carries a snapshot
# ---------------------------------------------------------------------------


async def test_an_action_answers_with_its_sentence_alone_while_the_page_stays_the_same_document(
    tools: BrowserTools, world: BrowserWorld
) -> None:
    await on_the_order_form(tools)
    fake = world.browser.page("target-1")
    assert await call(tools, "click", uid=uid_of("Gift wrap")) == BuiltinOutcome(True, "Successfully clicked on the element")
    assert await call(tools, "click", uid=uid_of("Order a part"), dblClick=True) == BuiltinOutcome(
        True, "Successfully double clicked on the element"
    )
    assert await call(tools, "hover", uid=uid_of("Terms")) == BuiltinOutcome(True, "Successfully hovered over the element")
    assert await call(tools, "fill", uid=uid_of("Your name"), value="Ada") == BuiltinOutcome(
        True, "Successfully filled out the element"
    )
    assert await call(tools, "type_text", text=" Lovelace") == BuiltinOutcome(True, "Typed text")
    assert await call(tools, "type_text", text="!", submitKey="Tab") == BuiltinOutcome(True, "Typed text + Tab")
    assert await call(tools, "press_key", key="PageDown") == BuiltinOutcome(True, "Successfully pressed key: PageDown")
    form = [{"uid": uid_of("Email"), "value": "ada@example.com"}, {"uid": uid_of("Product"), "value": "Steel Bolt"}]
    assert await call(tools, "fill_form", elements=form) == BuiltinOutcome(True, "Successfully filled out the form")
    assert fake.find("Gift wrap").checked is True and fake.find("Order a part").clicks == 2
    assert fake.hovered is fake.find("Terms")
    assert fake.find("Your name").value == "Ada Lovelace!"
    assert fake.find("Email").value == "ada@example.com" and fake.find("Product").value == "Steel Bolt"
    # Not one snapshot was taken for all of these.
    assert len(world.browser.of("Accessibility.getFullAXTree")) == 1


@pytest.mark.parametrize(
    ("name", "arguments", "sentence"),
    [
        ("click", {"uid": "1_4"}, "Successfully clicked on the element"),
        ("hover", {"uid": "1_11"}, "Successfully hovered over the element"),
        ("fill", {"uid": "1_2", "value": "Ada"}, "Successfully filled out the element"),
        ("fill_form", {"elements": [{"uid": "1_2", "value": "Ada"}]}, "Successfully filled out the form"),
        ("press_key", {"key": "Tab"}, "Successfully pressed key: Tab"),
    ],
)
async def test_an_action_answers_with_a_snapshot_when_asked_for_one(
    tools: BrowserTools, world: BrowserWorld, name: str, arguments: dict[str, Any], sentence: str
) -> None:
    await on_the_order_form(tools)
    outcome = await call(tools, name, includeSnapshot=True, **arguments)
    assert outcome.success
    lines = outcome.text.split("\n")
    assert lines[:2] == [sentence, SNAPSHOT_HEADER]
    assert lines[2] == ORDER_SNAPSHOT[0] and len(lines) == 2 + len(ORDER_SNAPSHOT)
    assert "another document" not in outcome.text
    # Anything but a real true is not a request.
    plain = await call(tools, name, includeSnapshot=False, **arguments)
    assert plain == BuiltinOutcome(True, sentence)


async def test_an_action_after_which_the_page_is_another_document_says_so_and_answers_with_its_snapshot(
    tools: BrowserTools, world: BrowserWorld
) -> None:
    await on_the_order_form(tools)
    outcome = await call(tools, "click", uid=uid_of("Send"))
    assert outcome.success
    assert outcome.text == "\n".join(
        [
            "Successfully clicked on the element",
            "The page changed to another document; its snapshot follows.",
            SNAPSHOT_HEADER,
            'uid=2_0 RootWebArea "Thanks"',
            '  uid=2_1 heading "Thank you" level="1"',
            '  uid=2_2 button "Print"',
            '  uid=2_3 link "Back to the shop"',
        ]
    )
    # The uids of that snapshot are good at once.
    assert (await call(tools, "click", uid="2_2")) == BuiltinOutcome(True, "Successfully clicked on the element")
    assert world.browser.page("target-1").find("Print").clicks == 1


async def test_when_a_snapshot_was_asked_for_anyway_the_sentence_about_the_new_document_is_left_out(
    tools: BrowserTools, world: BrowserWorld
) -> None:
    await on_the_order_form(tools)
    outcome = await call(tools, "click", uid=uid_of("Send"), includeSnapshot=True)
    lines = outcome.text.split("\n")
    assert lines[:3] == ["Successfully clicked on the element", SNAPSHOT_HEADER, 'uid=2_0 RootWebArea "Thanks"']


async def test_typing_a_submit_key_that_loads_another_document_answers_with_its_snapshot(
    tools: BrowserTools, world: BrowserWorld
) -> None:
    await on_the_order_form(tools)
    fake = world.browser.page("target-1")
    fake.on_key = lambda browser, page, event: browser.go(page, THANKS) if event["key"] == "Enter" else None
    outcome = await call(tools, "type_text", text="brass hinge", submitKey="Enter")
    assert outcome.text.startswith(
        f"Typed text + Enter\nThe page changed to another document; its snapshot follows.\n{SNAPSHOT_HEADER}\n"
    )
    assert 'RootWebArea "Thanks"' in outcome.text


async def test_filling_a_select_whose_change_asks_first_answers_at_once_with_the_dialog(
    tools: BrowserTools, world: BrowserWorld
) -> None:
    await on_the_order_form(tools)
    product = world.browser.page("target-1").find("Product")
    product.on_change = lambda browser, page: browser.open_dialog(page, "confirm", "Change the product?")
    outcome = await asyncio.wait_for(tools.call(TOOL_PREFIX + "fill", {"uid": uid_of("Product"), "value": "Steel Bolt"}), 1.0)
    assert outcome == BuiltinOutcome(
        True,
        "Successfully filled out the element\n# Open dialog\nThe page opened a confirm dialog. Its text, "
        'written by the page:\n"Change the product?"\nCall browser_handle_dialog to handle it before continuing.',
    )
    assert product.value == "Steel Bolt"
    handled = await call(tools, "handle_dialog", action="accept")
    assert handled.success, handled.text


async def test_a_click_on_an_element_that_something_lies_over_is_refused_with_what_covers_it(
    tools: BrowserTools, world: BrowserWorld
) -> None:
    await on_the_order_form(tools)
    fake = world.browser.page("target-1")
    fake.find("Gift wrap").cover = fake.find("Save draft")
    outcome = await call(tools, "click", uid=uid_of("Gift wrap"))
    assert not outcome.success
    assert outcome.text.startswith(f'The element with uid {uid_of("Gift wrap")} is covered by button "Save draft"')
    assert fake.mouse == []


async def test_an_action_that_opens_a_tab_answers_with_the_page_list(tools: BrowserTools, world: BrowserWorld) -> None:
    await on_the_order_form(tools)
    outcome = await call(tools, "click", uid=uid_of("Terms"))
    assert outcome == BuiltinOutcome(True, f"Successfully clicked on the element\n## Pages\n1: {ORDER} [selected]\n2: {TERMS}")


async def test_a_click_that_closes_its_own_tab_is_answered_as_done_with_the_pages_that_are_left(
    tools: BrowserTools, world: BrowserWorld
) -> None:
    world.browser.serve(
        "http://shop.test/consent",
        lambda: Document("Consent", el("button", "Allow", on_click=lambda browser, page: browser.remove_page(page))),
    )
    await on_the_order_form(tools)
    opened = await call(tools, "new_page", url="http://shop.test/consent")
    assert 'uid=2_1 button "Allow"' in opened.text
    outcome = await call(tools, "click", uid="2_1")
    assert outcome == BuiltinOutcome(
        True, f"Successfully clicked on the element\nThe page it was on has closed.\n## Pages\n1: {ORDER} [selected]"
    )
    # The next call goes to the page that is left.
    after = await call(tools, "take_snapshot")
    assert after.success and 'RootWebArea "Order form"' in after.text


async def test_in_the_users_browser_the_pages_can_still_be_listed_when_the_tab_of_the_work_is_closed(
    tools: BrowserTools, world: BrowserWorld
) -> None:
    world.settings(mode="attach")
    mail = world.browser.add_page("https://mail.example/inbox", "Inbox")
    await on_the_order_form(tools)
    (work,) = [page for page in world.browser.pages.values() if page.url == ORDER]
    world.browser.remove_page(work)
    listed = await call(tools, "list_pages")
    # None of the user's tabs is marked as the one being worked on.
    assert listed == BuiltinOutcome(True, "## Pages\n1: about:blank\n2: https://mail.example/inbox")
    refused = await call(tools, "take_snapshot")
    assert not refused.success and "Open a tab with browser_new_page" in refused.text
    assert mail.sessions == set()
    opened = await call(tools, "new_page", url=THANKS)
    assert opened.success and f"4: {THANKS} [selected]" in opened.text


# ---------------------------------------------------------------------------
# The size of a snapshot
# ---------------------------------------------------------------------------


def test_lines_that_fit_are_all_shown() -> None:
    lines = [f'uid=1_{number} button "Button number {number}"' for number in range(40)]
    assert cap_lines(lines, 100_000) == ("\n".join(lines), 40)
    assert cap_lines([], 50) == ("", 0)
    assert cap_lines(["one line"], 50) == ("one line", 1)


def test_lines_over_the_cap_are_cut_at_the_end_of_a_line() -> None:
    lines = [f'uid=1_{number} button "Button number {number}"' for number in range(40)]
    text, shown = cap_lines(lines, 80)
    assert 0 < shown < 40
    assert text == "\n".join(lines[:shown])
    assert count_tokens(text) <= 80
    # The cap is used: what is left out would not have fitted.
    assert count_tokens("\n".join(lines[: shown + 2])) > 80


def test_a_single_line_longer_than_the_cap_is_cut_to_it_and_reading_on_still_moves() -> None:
    # A page that is one block of text. Shown whole it would be cut further
    # down the way, together with the note about where to read on.
    long = "word " * 400
    text, shown = cap_lines([long, "next"], 20)
    assert shown == 1
    head, note = text.split(" [... ")
    assert long.startswith(head) and 0 < len(head) < len(long)
    assert count_tokens(head) <= 20
    assert note == f"{len(long) - len(head)} more characters of this line are not shown]"


async def test_a_page_that_is_one_long_line_stays_within_the_cap_and_can_be_read_on(
    tools: BrowserTools, world: BrowserWorld, monkeypatch: Any
) -> None:
    import config.nekoclaw_settings as settings

    monkeypatch.setattr(settings, "BROWSER_SNAPSHOT_MAX_TOKENS", 60)
    world.browser.serve("http://shop.test/log", lambda: Document("Log", el("StaticText", "entry " * 600)))
    first = await call(tools, "navigate_page", url="http://shop.test/log")
    lines = snapshot_of(first.text)
    assert CUT_OFF.fullmatch(lines[-1]) is not None, lines[-1]
    rest = await call(tools, "take_snapshot", offset=int(CUT_OFF.fullmatch(lines[-1]).group(3)))
    shown = snapshot_of(rest.text)
    assert shown[-1].endswith("more characters of this line are not shown]")
    assert count_tokens("\n".join(shown)) < 120


async def test_a_snapshot_over_the_cap_is_cut_at_a_line_end_and_says_where_to_read_on(
    tools: BrowserTools, world: BrowserWorld, monkeypatch: Any
) -> None:
    import config.nekoclaw_settings as settings

    monkeypatch.setattr(settings, "BROWSER_SNAPSHOT_MAX_TOKENS", 60)
    outcome = await call(tools, "navigate_page", url=ORDER)
    assert outcome.success
    lines = snapshot_of(outcome.text)
    note = CUT_OFF.fullmatch(lines[-1])
    assert note is not None, lines[-1]
    shown, total, offset = (int(group) for group in note.groups())
    assert total == len(ORDER_SNAPSHOT) and offset == shown
    assert 0 < shown < total
    # Whole lines, the first ones, and within the cap.
    assert lines[:-1] == ORDER_SNAPSHOT[:shown]
    assert count_tokens("\n".join(lines[:-1])) <= 60
    # Reading on from the offset the note names gives the rest, piece by piece, and nothing twice.
    collected = lines[:-1]
    for _ in range(total):
        answer = await call(tools, "take_snapshot", offset=offset)
        body = answer.text.split("\n")
        assert body[0] == SNAPSHOT_HEADER
        span = re.fullmatch(r"\[Lines (\d+) to (\d+) of (\d+)\.\]", body[1])
        assert span is not None, body[1]
        assert (int(span.group(1)), int(span.group(3))) == (offset + 1, total)
        note = CUT_OFF.fullmatch(body[-1])
        piece = body[2:-1] if note else body[2:]
        assert piece, "reading on must show at least one line"
        collected.extend(piece)
        assert int(span.group(2)) == len(collected)
        if note is None:
            break
        assert int(note.group(1)) == int(note.group(3)) == len(collected)
        offset = int(note.group(3))
    assert collected == ORDER_SNAPSHOT
    # The elements beyond the cut are in the snapshot all the same: their uids work.
    assert await call(tools, "click", uid=uid_of("Hidden")) == BuiltinOutcome(
        False, "The element with uid 1_12 is not visible, so it cannot be acted on."
    )


async def test_a_snapshot_within_the_cap_has_no_note(tools: BrowserTools, world: BrowserWorld) -> None:
    import config.nekoclaw_settings as settings

    assert settings.BROWSER_SNAPSHOT_MAX_TOKENS >= 1000
    outcome = await call(tools, "navigate_page", url=ORDER)
    assert "cut off" not in outcome.text and "[Lines" not in outcome.text


@pytest.mark.parametrize("offset", [-5, "3", None, True])
async def test_an_offset_that_is_not_a_line_number_reads_from_the_start(tools: BrowserTools, world: BrowserWorld, offset: Any) -> None:
    await on_the_order_form(tools)
    outcome = await call(tools, "take_snapshot", offset=offset)
    assert outcome.text == "\n".join([SNAPSHOT_HEADER, *ORDER_SNAPSHOT])


async def test_an_offset_reads_from_that_line(tools: BrowserTools, world: BrowserWorld) -> None:
    await on_the_order_form(tools)
    outcome = await call(tools, "take_snapshot", offset=9)
    assert outcome.text == "\n".join([SNAPSHOT_HEADER, "[Lines 10 to 13 of 13.]", *ORDER_SNAPSHOT[9:]])


@pytest.mark.parametrize("offset", [13, 14, 500])
async def test_an_offset_at_or_past_the_end_says_how_long_the_snapshot_is(
    tools: BrowserTools, world: BrowserWorld, offset: int
) -> None:
    await on_the_order_form(tools)
    outcome = await call(tools, "take_snapshot", offset=offset)
    assert outcome.success
    assert outcome.text == "\n".join([SNAPSHOT_HEADER, "[The snapshot has 13 lines; there is nothing after line 13.]"])


# ---------------------------------------------------------------------------
# Screenshots
# ---------------------------------------------------------------------------


async def test_a_screenshot_comes_back_as_a_picture_and_never_as_text(tools: BrowserTools, world: BrowserWorld) -> None:
    await on_the_order_form(tools)
    outcome = await call(tools, "take_screenshot")
    assert outcome.success
    assert outcome.text == "Took a screenshot of the current page's viewport."
    (image,) = outcome.images
    assert image.mime_type == "image/jpeg"
    assert base64.b64decode(image.data) == world.browser.picture
    assert image.data not in outcome.text and "base64" not in outcome.text


async def test_a_screenshot_of_the_whole_page_or_of_one_element_says_which(tools: BrowserTools, world: BrowserWorld) -> None:
    await on_the_order_form(tools)
    fake = world.browser.page("target-1")
    whole = await call(tools, "take_screenshot", fullPage=True)
    assert whole.text == "Took a screenshot of the full current page." and len(whole.images) == 1
    part = await call(tools, "take_screenshot", uid=uid_of("Send"))
    assert part.text == 'Took a screenshot of node with uid "1_9".' and len(part.images) == 1
    assert "clip" in fake.screenshots[0] and "clip" in fake.screenshots[1]
    assert fake.screenshots[0]["clip"]["height"] == 2400.0
    assert fake.screenshots[1]["clip"]["width"] == 100.0
    both = await call(tools, "take_screenshot", uid=uid_of("Send"), fullPage=True)
    assert both == BuiltinOutcome(False, 'Providing both "uid" and "fullPage" is not allowed.')
    assert len(fake.screenshots) == 2


async def test_a_large_screenshot_is_brought_under_the_byte_ceiling(tools: BrowserTools, world: BrowserWorld) -> None:
    noise = Image.frombytes("RGB", (1280, 720), random.Random(11).randbytes(1280 * 720 * 3))
    buffer = BytesIO()
    noise.save(buffer, format="JPEG", quality=95)
    world.browser.picture = buffer.getvalue()
    assert len(world.browser.picture) > 2 * SCREENSHOT_MAX_BYTES
    await on_the_order_form(tools)
    outcome = await call(tools, "take_screenshot")
    (image,) = outcome.images
    encoded = base64.b64decode(image.data)
    assert len(encoded) <= SCREENSHOT_MAX_BYTES == 200 * 1024
    with Image.open(BytesIO(encoded)) as picture:
        assert picture.format == "JPEG"


# ---------------------------------------------------------------------------
# Waiting and dialogs
# ---------------------------------------------------------------------------


async def test_waiting_for_text_answers_with_what_was_found_and_the_snapshot(tools: BrowserTools, world: BrowserWorld) -> None:
    await call(tools, "navigate_page", url=THANKS)
    outcome = await call(tools, "wait_for", text=["Sorry", "QX-7"])
    assert outcome.success
    assert outcome.text.startswith(f'Element matching one of ["Sorry", "QX-7"] found.\n{SNAPSHOT_HEADER}\n')
    # One text may be given as it is.
    single = await call(tools, "wait_for", text="Thank you")
    assert single.text.startswith('Element matching one of ["Thank you"] found.\n')
    missing = await call(tools, "wait_for", text=["Refunded"], timeout=50)
    assert not missing.success and missing.text.startswith('None of ["Refunded"] appeared within 0s.')


@pytest.mark.parametrize("text", [None, [], [""], ["ok", 5], "", 7])
async def test_waiting_needs_texts_to_wait_for(tools: BrowserTools, world: BrowserWorld, text: Any) -> None:
    await on_the_order_form(tools)
    sent = world.browser.mark()
    assert await call(tools, "wait_for", text=text) == BuiltinOutcome(False, "text is required: a non-empty list of texts")
    assert world.browser.of("Runtime.evaluate", sent) == []


async def test_an_action_that_opens_a_dialog_answers_with_the_notice_and_no_snapshot(tools: BrowserTools, world: BrowserWorld) -> None:
    await on_the_order_form(tools)
    read = len(world.browser.of("Accessibility.getFullAXTree"))
    outcome = await call(tools, "click", uid=uid_of("Save draft"), includeSnapshot=True)
    assert outcome == BuiltinOutcome(True, "Successfully clicked on the element\n" + NOTICE)
    # The page answers nothing while the dialog is open, so it was not asked for a snapshot.
    assert len(world.browser.of("Accessibility.getFullAXTree")) == read


async def test_until_the_dialog_is_handled_every_action_on_the_page_is_refused_with_the_notice(
    tools: BrowserTools, world: BrowserWorld
) -> None:
    await on_the_order_form(tools)
    await call(tools, "click", uid=uid_of("Save draft"))
    fake = world.browser.page("target-1")
    sent = world.browser.mark()
    for name, arguments in [
        ("click", {"uid": uid_of("Gift wrap")}),
        ("hover", {"uid": uid_of("Terms")}),
        ("fill", {"uid": uid_of("Your name"), "value": "Ada"}),
        ("fill_form", {"elements": [{"uid": uid_of("Your name"), "value": "Ada"}]}),
        ("type_text", {"text": "Ada"}),
        ("press_key", {"key": "Enter"}),
        ("take_screenshot", {}),
        ("wait_for", {"text": ["Order"]}),
    ]:
        assert await call(tools, name, **arguments) == BuiltinOutcome(False, NOTICE), name
    assert [command for command in world.browser.since(sent) if command.session_id is not None] == []
    assert fake.find("Gift wrap").checked is False and fake.inserted == []
    # Asking for a snapshot or for the pages answers with the notice instead.
    assert await call(tools, "take_snapshot") == BuiltinOutcome(True, NOTICE)
    assert await call(tools, "list_pages") == BuiltinOutcome(True, f"{NOTICE}\n## Pages\n1: {ORDER} [selected]")
    handled = await call(tools, "handle_dialog", action="accept")
    assert handled == BuiltinOutcome(True, "Successfully accepted the dialog.")
    assert fake.handled_dialogs == [{"accept": True}]
    assert await call(tools, "click", uid=uid_of("Gift wrap")) == BuiltinOutcome(True, "Successfully clicked on the element")
    assert fake.find("Gift wrap").checked is True


async def test_a_dialog_is_dismissed_or_accepted_with_a_text(tools: BrowserTools, world: BrowserWorld) -> None:
    await on_the_order_form(tools)
    fake = world.browser.page("target-1")
    world.browser.open_dialog(fake, "prompt", "Coupon code?")
    assert await call(tools, "handle_dialog", action="accept", promptText="SPRING") == BuiltinOutcome(
        True, "Successfully accepted the dialog."
    )
    world.browser.open_dialog(fake, "confirm", "Leave?")
    assert await call(tools, "handle_dialog", action="dismiss") == BuiltinOutcome(True, "Successfully dismissed the dialog.")
    assert fake.handled_dialogs == [{"accept": True, "promptText": "SPRING"}, {"accept": False}]
    assert await call(tools, "handle_dialog", action="accept") == BuiltinOutcome(False, "No open dialog found.")
    for action in (None, "ok", "ACCEPT", True):
        assert await call(tools, "handle_dialog", action=action) == BuiltinOutcome(False, "action must be accept or dismiss")


# ---------------------------------------------------------------------------
# Arguments
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("name", "arguments", "message"),
    [
        ("click", {}, "uid is required"),
        ("click", {"uid": ""}, "uid is required"),
        ("click", {"uid": 19}, "uid is required"),
        ("hover", {}, "uid is required"),
        ("fill", {"uid": "1_2"}, "value is required"),
        ("fill", {"uid": "1_2", "value": 5}, "value is required"),
        ("fill", {"value": "Ada"}, "uid is required"),
        ("fill_form", {}, "elements is required: a list of {uid, value}"),
        ("fill_form", {"elements": []}, "elements is required: a list of {uid, value}"),
        ("fill_form", {"elements": {"uid": "1_2", "value": "Ada"}}, "elements is required: a list of {uid, value}"),
        ("fill_form", {"elements": [{"uid": "1_2"}]}, "every element needs a uid and a value, both text"),
        ("fill_form", {"elements": [{"uid": "1_2", "value": "Ada"}, "1_3"]}, "every element needs a uid and a value, both text"),
        ("fill_form", {"elements": [{"uid": 12, "value": "Ada"}]}, "every element needs a uid and a value, both text"),
        ("type_text", {}, "text is required"),
        ("type_text", {"text": 5}, "text is required"),
        ("press_key", {}, "key is required"),
        ("press_key", {"key": ""}, "key is required"),
    ],
)
async def test_a_call_without_what_it_needs_is_a_failure_and_nothing_is_done(
    tools: BrowserTools, world: BrowserWorld, name: str, arguments: dict[str, Any], message: str
) -> None:
    await on_the_order_form(tools)
    fake = world.browser.page("target-1")
    sent = world.browser.mark()
    assert await call(tools, name, **arguments) == BuiltinOutcome(False, message)
    assert world.browser.since(sent) == []
    assert fake.mouse == [] and fake.inserted == [] and fake.keys == []
    assert fake.find("Your name").value == ""


async def test_an_empty_value_is_a_value(tools: BrowserTools, world: BrowserWorld) -> None:
    await on_the_order_form(tools)
    assert (await call(tools, "fill", uid=uid_of("Email"), value="")).success
    assert world.browser.page("target-1").find("Email").value == ""
    # So is typing nothing before a key.
    assert await call(tools, "type_text", text="", submitKey="Tab") == BuiltinOutcome(True, "Typed text + Tab")


async def test_a_form_that_fails_half_way_says_what_was_filled_in(tools: BrowserTools, world: BrowserWorld) -> None:
    await on_the_order_form(tools)
    form = [
        {"uid": uid_of("Your name"), "value": "Ada"},
        {"uid": uid_of("Product"), "value": "Zinc Nut"},
        {"uid": uid_of("Email"), "value": "ada@example.com"},
    ]
    outcome = await call(tools, "fill_form", elements=form)
    assert not outcome.success
    assert outcome.text == (
        'The element with uid 1_6 could not be filled in: it has no option "Zinc Nut"; its options are: '
        "Brass Hinge, Steel Bolt. Filled in before it: 1_2."
    )
    fake = world.browser.page("target-1")
    assert fake.find("Your name").value == "Ada" and fake.find("Email").value == "old@example.com"


# ---------------------------------------------------------------------------
# Failures are answers
# ---------------------------------------------------------------------------


async def test_a_refusal_of_the_session_is_a_failed_outcome_with_its_sentence(tools: BrowserTools, world: BrowserWorld) -> None:
    await on_the_order_form(tools)
    outcome = await call(tools, "click", uid="9_9")
    assert outcome == BuiltinOutcome(
        False, 'Element uid "9_9" not found on page 1. Use a uid of the latest snapshot; take a new one with browser_take_snapshot.'
    )
    assert world.browser.page("target-1").mouse == []


async def test_no_usable_browser_is_a_failed_outcome(tools: BrowserTools, world: BrowserWorld) -> None:
    world.install = None
    outcome = await call(tools, "navigate_page", url=ORDER)
    assert not outcome.success
    assert outcome.text.startswith("There is no usable browser: ")


async def test_an_error_answer_of_the_browser_is_a_failed_outcome(tools: BrowserTools, world: BrowserWorld) -> None:
    await on_the_order_form(tools)
    world.browser.failures["Page.getNavigationHistory"] = CdpError("Page.getNavigationHistory", -32000, "Not allowed in this state")
    outcome = await call(tools, "navigate_page", type="back")
    assert outcome == BuiltinOutcome(False, "The browser refused the action: Not allowed in this state")


async def test_a_connection_that_is_gone_is_a_failed_outcome(tools: BrowserTools, world: BrowserWorld) -> None:
    await on_the_order_form(tools)
    world.browser.add_page(TERMS)
    world.browser.failures["Target.closeTarget"] = CdpClosed("the connection to the browser is closed")
    outcome = await call(tools, "close_page", pageId=2)
    assert outcome == BuiltinOutcome(False, "The browser was closed. Call the tool again to start it anew.")


async def test_a_browser_that_does_not_answer_in_time_is_a_failed_outcome(tools: BrowserTools, world: BrowserWorld) -> None:
    await on_the_order_form(tools)
    world.browser.failures["Target.createTarget"] = CdpTimeout("Target.createTarget got no answer within 30s")
    outcome = await call(tools, "new_page", url=TERMS)
    assert outcome == BuiltinOutcome(
        False,
        "The browser did not answer in time (Target.createTarget got no answer within 30s). The page may be busy; try again.",
    )


async def test_after_the_browser_went_away_the_next_call_says_so_and_the_one_after_starts_it_anew(
    tools: BrowserTools, world: BrowserWorld
) -> None:
    await on_the_order_form(tools)
    world.browser.disconnect()
    # The session notices at the start of the next call and connects again by itself.
    outcome = await call(tools, "take_snapshot")
    assert outcome.success and len(world.connected) == 2
    assert snapshot_of(outcome.text)[0].endswith('RootWebArea "Order form"')
    # The uids of before the break are of the old connection's snapshot.
    stale = await call(tools, "click", uid="1_4")
    assert not stale.success and "not found on page 2" in stale.text


# ---------------------------------------------------------------------------
# Letting go, the window, closing
# ---------------------------------------------------------------------------


async def test_when_the_browser_is_let_go_the_uids_handed_out_so_far_stop_working(tools: BrowserTools, world: BrowserWorld) -> None:
    await on_the_order_form(tools)
    assert (await call(tools, "click", uid=uid_of("Gift wrap"))).success
    await tools.released()
    fake = world.browser.page("target-1")
    sent = world.browser.mark()
    outcome = await call(tools, "click", uid=uid_of("Gift wrap"))
    assert outcome == BuiltinOutcome(False, "No snapshot found for page 1. Use browser_take_snapshot to capture one.")
    assert [command for command in world.browser.since(sent) if command.session_id is not None] == []
    assert fake.find("Gift wrap").clicks == 1
    # The page itself is as it was left; a new snapshot numbers its elements anew.
    fresh = await call(tools, "take_snapshot")
    assert snapshot_of(fresh.text)[0] == 'uid=2_0 RootWebArea "Order form"'
    assert not (await call(tools, "click", uid=uid_of("Gift wrap"))).success
    assert (await call(tools, "click", uid="2_4")).success
    assert fake.find("Gift wrap").clicks == 2


async def test_the_window_of_the_browser_is_known_after_a_call(tools: BrowserTools, world: BrowserWorld) -> None:
    assert tools.window() is None
    await on_the_order_form(tools)
    assert tools.window() == {
        "browser": "Google Chrome",
        "pid": None,
        "window_id": 7,
        "bounds": world.browser.window["bounds"],
    }
    assert tools.session.window() == tools.window()


async def test_closing_closes_the_browser_and_a_later_call_starts_it_again(tools: BrowserTools, world: BrowserWorld) -> None:
    await on_the_order_form(tools)
    await tools.aclose()
    assert len(world.closed) == 1 and world.closed[0][0].owned is True
    assert tools.window() is None
    assert (await call(tools, "list_pages")).success
    assert len(world.launched) == 2


def test_the_tools_keep_to_the_screenshot_ceiling_and_the_prefix() -> None:
    assert tools_module.SCREENSHOT_MAX_BYTES == 200 * 1024
    assert tools_module.TOOL_PREFIX == "browser_"


# ---------------------------------------------------------------------------
# What was done and what the page then looked like are told apart
# ---------------------------------------------------------------------------


async def test_an_action_that_was_carried_out_is_answered_as_done_when_the_page_cannot_be_read_after_it(
    tools: BrowserTools, world: BrowserWorld
) -> None:
    def send(browser: Any, page: Any) -> None:
        # The page the form leads to is so heavy that its tree cannot be had.
        browser.refuse("Accessibility.getFullAXTree", "Timed out building the tree")
        browser.go(page, THANKS)

    world.browser.serve("http://shop.test/pay", lambda: Document("Pay", el("button", "Pay now", on_click=send)))
    opened = await call(tools, "navigate_page", url="http://shop.test/pay")
    fake = world.browser.page("target-1")
    button = fake.find("Pay now")
    outcome = await call(tools, "click", uid="1_1")
    # A failure here would be read as "not paid", and paid a second time.
    assert outcome.success, outcome.text
    assert outcome.text.startswith(
        "Successfully clicked on the element\nThe page changed to another document; its snapshot follows.\n"
        "The page could not be read afterwards, so no snapshot is shown. What is said above was done: do not repeat it."
    )
    assert "Timed out building the tree" in outcome.text and button.clicks == 1
    assert opened.success
    # Reading the page is all the next call was asked for: that fails as itself.
    alone = await call(tools, "take_snapshot")
    assert not alone.success and "Timed out building the tree" in alone.text


async def test_a_tab_that_was_opened_is_answered_as_opened_when_its_page_cannot_be_read(
    tools: BrowserTools, world: BrowserWorld
) -> None:
    await on_the_order_form(tools)
    world.browser.refuse("Accessibility.getFullAXTree", "Timed out building the tree")
    outcome = await call(tools, "new_page", url=TERMS)
    assert outcome.success, outcome.text
    assert outcome.text.startswith(f"## Pages\n1: {ORDER}\n2: {TERMS} [selected]\nThe page could not be read afterwards")
    assert len(world.browser.pages) == 2


async def test_a_page_whose_text_holds_line_breaks_is_cut_by_its_entries_and_says_where_to_read_on(
    tools: BrowserTools, world: BrowserWorld, monkeypatch: Any
) -> None:
    import config.nekoclaw_settings as settings

    monkeypatch.setattr(settings, "BROWSER_SNAPSHOT_MAX_TOKENS", 60)
    # A code block, a log, a JSON document: one piece of text with hundreds of line breaks of its own.
    log = "\n".join(f"line {number} of the log" for number in range(300))
    world.browser.serve("http://shop.test/log", lambda: Document("Log", el("StaticText", log), el("button", "Download")))
    first = await call(tools, "navigate_page", url="http://shop.test/log")
    shown = snapshot_of(first.text)
    note = CUT_OFF.fullmatch(shown[-1])
    assert note is not None, shown[-1]
    # Three entries in all, however many line breaks the text holds; one is shown.
    assert [int(group) for group in note.groups()] == [1, 3, 1]
    assert shown[:-1] == ['uid=1_0 RootWebArea "Log"']
    assert count_tokens(first.text) < 200
    # The text is one line of the snapshot: its line breaks are written out.
    rest = await call(tools, "take_snapshot", offset=1)
    body = snapshot_of(rest.text)
    assert body[1].startswith('  uid=1_1 "line 0 of the log\\nline 1 of the log\\nline 2') and "\n" not in body[1]


def test_entries_that_hold_line_breaks_of_their_own_are_still_counted_as_entries() -> None:
    lines = ["first " * 10, "a\nb\nc\nd\ne\nf " * 30, "last"]
    text, shown = cap_lines(lines, 14)
    # The line breaks inside the second entry are not taken for three more entries.
    assert (text, shown) == (lines[0], 1)
    assert cap_lines(lines, 100_000) == ("\n".join(lines), 3)


@pytest.mark.parametrize(("asked", "seconds"), [(5000, 5.0), (120_000, 120.0), (10**9, 120.0), (0, None), (-5, None), ("60", None), (True, None)])
def test_a_wait_is_as_long_as_asked_up_to_the_longest_allowed(asked: Any, seconds: float | None) -> None:
    from nekoclaw.builtin.browser.tools import _seconds

    assert _seconds({"timeout": asked}) == seconds
    assert _seconds({}) is None


async def test_the_list_of_pages_names_the_first_ones_and_the_selected_one_and_cuts_long_addresses(
    tools: BrowserTools, world: BrowserWorld
) -> None:
    from nekoclaw.builtin.browser.tools import PAGES_LISTED

    await on_the_order_form(tools)
    for number in range(30):
        world.browser.add_page(f"http://shop.test/item/{number}?" + "tracking=" + "x" * 3000)
    selected = await call(tools, "select_page", pageId=25)
    assert selected.success, selected.text
    listed = (await call(tools, "list_pages")).text.split("\n")
    assert listed[0] == "## Pages" and listed[1] == f"1: {ORDER}"
    assert len(listed) == 1 + PAGES_LISTED + 1 + 1
    assert listed[-2].startswith("25: http://shop.test/item/23?tracking=") and listed[-2].endswith("more characters not shown] [selected]")
    assert listed[-1] == f"[{31 - PAGES_LISTED - 1} more pages are open and not listed here.]"
    assert count_tokens("\n".join(listed)) < 900


async def test_the_snapshot_is_given_what_the_rest_of_the_answer_leaves_of_a_results_size(
    tools: BrowserTools, monkeypatch: Any
) -> None:
    import config.nekoclaw_settings as settings

    assert await tools._room_for_snapshot([]) == settings.BROWSER_SNAPSHOT_MAX_TOKENS
    assert await tools._room_for_snapshot(["Successfully clicked on the element"]) == settings.BROWSER_SNAPSHOT_MAX_TOKENS
    long = "word " * 2000
    used = count_tokens(long)
    room = await tools._room_for_snapshot([long])
    assert room == settings.GATEWAY_RESULT_MAX_TOKENS - used - 200 < settings.BROWSER_SNAPSHOT_MAX_TOKENS
    # Never nothing at all, and never more than a snapshot may be.
    assert await tools._room_for_snapshot(["word " * 20000]) == 200
    monkeypatch.setattr(settings, "BROWSER_SNAPSHOT_MAX_TOKENS", 60)
    assert await tools._room_for_snapshot([]) == 60


async def test_the_snapshot_says_how_many_embedded_pages_it_does_not_list(tools: BrowserTools, world: BrowserWorld) -> None:
    world.browser.serve(
        "http://shop.test/checkout-elsewhere",
        lambda: Document("Checkout", el("button", "Back"), el("Iframe", "Payment"), el("Iframe", "Captcha")),
    )
    world.browser.serve("http://shop.test/one-frame", lambda: Document("Checkout", el("button", "Back"), el("Iframe", "Payment")))
    outcome = await call(tools, "navigate_page", url="http://shop.test/checkout-elsewhere")
    assert snapshot_of(outcome.text)[-1] == (
        "[2 embedded pages (iframe) on this page are not listed: of another site, or more than are read. "
        "What is in them cannot be read or acted on by uid.]"
    )
    outcome = await call(tools, "navigate_page", url="http://shop.test/one-frame")
    assert snapshot_of(outcome.text)[-1].startswith("[1 embedded page (iframe) on this page is not listed:")
    # A page without any says nothing of the kind.
    assert "not listed" not in (await call(tools, "navigate_page", url=ORDER)).text


async def test_a_call_whose_turn_comes_after_the_browser_was_switched_off_is_not_carried_out(
    tools: BrowserTools, world: BrowserWorld
) -> None:
    outcome = await tools.call(TOOL_PREFIX + "navigate_page", {"url": ORDER}, refused=lambda: "browser control is switched off")
    assert outcome == BuiltinOutcome(False, "browser control is switched off")
    # The browser was not started for it.
    assert world.launched == [] and world.connected == []
    assert (await tools.call(TOOL_PREFIX + "navigate_page", {"url": ORDER}, refused=lambda: None)).success


def test_a_screenshot_of_a_page_may_weigh_what_one_of_the_desktop_may() -> None:
    from nekoclaw.builtin import desktop

    # Three screenshots stay in a context, of the desktop and of pages together:
    # the request stays as small as the loop this replaces kept it (600 KB of pictures).
    assert SCREENSHOT_MAX_BYTES == desktop.SCREENSHOT_MAX_BYTES == 200 * 1024


async def test_a_click_is_told_as_done_when_the_wait_for_the_page_to_settle_fails(
    tools: BrowserTools, world: BrowserWorld
) -> None:
    await on_the_order_form(tools)
    world.browser.refuse("Runtime.evaluate", "Execution context was destroyed.")

    outcome = await call(tools, "click", uid=uid_of("Gift wrap"))

    # Told as failed, the model would click again: a form would be sent twice.
    assert outcome == BuiltinOutcome(True, "Successfully clicked on the element")
    assert world.browser.page("target-1").find("Gift wrap").checked is True


async def test_killing_is_passed_on_to_the_session_without_waiting_for_a_call_under_way(
    tools: BrowserTools, world: BrowserWorld, monkeypatch: Any
) -> None:
    killed: list[int] = []
    monkeypatch.setattr(tools._session, "kill", lambda: killed.append(1))
    # A call holds the tools (a browser that does not answer): killing does not wait its turn.
    async with tools._lock:
        tools.kill()
    assert killed == [1]


async def test_closing_does_not_wait_for_a_browser_that_a_call_is_still_bringing_up(
    tools: BrowserTools, world: BrowserWorld
) -> None:
    world.launch_gate = asyncio.Event()
    # The call holds the tools while the browser comes up, which it never does here.
    calling = asyncio.ensure_future(tools.call(TOOL_PREFIX + "navigate_page", {"url": ORDER}))
    deadline = asyncio.get_running_loop().time() + 5.0
    while not world.launched and asyncio.get_running_loop().time() < deadline:
        await asyncio.sleep(0.005)
    assert world.launched and not calling.done()

    try:
        await asyncio.wait_for(tools.aclose(), 5.0)
        outcome = await asyncio.wait_for(calling, 5.0)
    finally:
        # Whatever went wrong above, the call is not left holding the tools behind this test.
        world.launch_gate.set()
    assert not outcome.success and "the start was stopped" in outcome.text
    assert world.launch_stops[0].is_set() and world.closed == []
