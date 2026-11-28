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

"""A task agent that finds the browser, the keyboard and mouse or the plugins switched off asks the user to turn them on.

The host is real (ledger, permissions, gateway); the built-in tools are the
recording provider of ``test_host_gateway`` and what turns a switch on is a
fake that records what it was asked. Nothing touches a desktop or a browser.
"""

from __future__ import annotations

from typing import Any

import pytest

from nekopower.host.capabilities import KIND_BUILTIN
from nekopower.host.gateway import _APPROVAL_STALE, CALL_TOOL, ENABLE_TOOL, SEARCH_TOOL
from nekopower.host.permissions import DENIED_BY_SETTINGS, GrantScope
from tests.unit.nekopower.host_fakes import FakeDecisionLLM, make_host
from tests.unit.nekopower.plugin_server_fake import FakePluginServer
from tests.unit.nekopower.test_host_gateway import (
    BUILTIN_NAMES,
    PLAIN_SEARCH_TOOL,
    Asker,
    Builtins,
    _call,
    _catalog,
    _commission,
    _found,
    _grant,
    _rpc,
    _search_tool,
    _text,
)

BROWSER_TOOLS = ("browser_navigate_page", "browser_take_screenshot")


class Enabler:
    """What turns the switches on: records what it was asked, and does it to the fakes."""

    def __init__(self, provider: Builtins, state: dict[str, bool]) -> None:
        self.provider = provider
        self.state = state
        self.can: dict[str, tuple[bool, str]] = {}
        self.result: dict[str, tuple[bool, str]] = {}
        self.can_calls: list[str] = []
        self.enable_calls: list[tuple[str, str | None]] = []
        # Run while ``enable`` is at work (the user's answer is in, the switch is not yet on): a hook.
        self.during_enable: Any = None
        # Run first thing in ``enable``, as a check of this computer would be: the switch is not yet written.
        self.before_commit: Any = None
        # What the process says may not be asked for here (a screen the user must share first).
        self.blocked: set[str] = set()

    def may_request(self, capability: str) -> bool:
        return capability not in self.blocked

    async def can_enable(self, capability: str) -> tuple[bool, str]:
        self.can_calls.append(capability)
        return self.can.get(capability, (True, ""))

    async def enable(
        self, capability: str, *, lanlan_name: str | None = None, still_valid: Any = None
    ) -> tuple[bool, str]:
        self.enable_calls.append((capability, lanlan_name))
        if self.before_commit is not None:
            await self.before_commit()
        if callable(still_valid) and not still_valid():
            # What the real enabler does at the moment it writes a switch.
            return False, "switched off"
        if self.during_enable is not None:
            await self.during_enable()
        done, why = self.result.get(capability, (True, ""))
        if done:
            if capability == "browser":
                self.provider.switched_off.difference_update(BROWSER_TOOLS)
            elif capability == "keyboard_mouse":
                self.provider.switched_off.discard("computer")
            else:
                self.state["plugins"] = True
        return done, why


@pytest.fixture
def server(monkeypatch: Any) -> FakePluginServer:
    return FakePluginServer.install(monkeypatch)


@pytest.fixture
async def world(server: FakePluginServer, tmp_path: Any) -> Any:
    """A host with the browser, the keyboard and mouse and the plugins all switched off, and who may turn them on."""
    _catalog(server)
    provider = Builtins()
    provider.switched_off.update(BUILTIN_NAMES)
    state = {"plugins": False}
    enabler = Enabler(provider, state)
    built = make_host(
        tmp_path,
        server,
        decision_llm=FakeDecisionLLM(),
        builtins=provider,
        plugins_enabled=lambda: state["plugins"],
        capability_enabler=enabler,
    )
    built.provider, built.state, built.enabler = provider, state, enabler
    yield built
    await built.shutdown()


@pytest.fixture
async def world_without_enabler(server: FakePluginServer, tmp_path: Any) -> Any:
    provider = Builtins()
    provider.switched_off.update(BUILTIN_NAMES)
    built = make_host(
        tmp_path, server, decision_llm=FakeDecisionLLM(), builtins=provider, plugins_enabled=lambda: False
    )
    built.provider = provider
    yield built
    await built.shutdown()


async def _grant_for(host: Any, *, images: bool = True, reads_only: bool = False) -> Any:
    commission = await _commission(host)
    if reads_only:
        token = host.gateway.issue(commission.id, supports_images=images, reads_only=True)
        return host.gateway._grant(token)
    return _grant(host, commission, images=images, loaded=())


def _enable(capability: str) -> dict[str, Any]:
    return {"name": ENABLE_TOOL, "arguments": {"capability": capability}}


def _hint(capability: str) -> str:
    return f'call {CALL_TOOL} with name "{ENABLE_TOOL}" and arguments {{"capability": "{capability}"}}'


async def _event_types(host: Any, commission_id: str) -> list[str]:
    return [event.type for event in await host.ledger.events(commission_id)]


# ---------------------------------------------------------------------------
# tools/list: one line, and only where there is something to ask for
# ---------------------------------------------------------------------------


async def test_the_search_tool_names_what_is_switched_off_and_may_be_asked_for(world):
    grant = await _grant_for(world)

    search = await _search_tool(world, grant)

    last = search["description"].splitlines()[-1]
    assert last.startswith("Switched off; to ask the user to turn one on, ")
    assert _hint("<name>") in last and last.endswith(": browser, desktop, plugins.")
    # The two tools, as ever.
    tools = (await _rpc(world, grant, "tools/list"))["result"]["tools"]
    assert [tool["name"] for tool in tools] == [SEARCH_TOOL, CALL_TOOL]


async def test_the_desktop_is_not_offered_to_a_model_that_cannot_see(world):
    grant = await _grant_for(world, images=False)

    search = await _search_tool(world, grant)

    assert search["description"].endswith(": browser, plugins.")


async def test_what_the_settings_forbid_is_not_offered(world):
    await world.permission_store.set_mode("browser", "deny")
    await world.permission_store.set_mode("plugin_entry", "deny")
    grant = await _grant_for(world)

    search = await _search_tool(world, grant)

    assert search["description"].endswith(": desktop.")


async def test_a_copy_that_only_reads_and_a_host_without_an_enabler_offer_nothing(world, world_without_enabler):
    reads_only = await _grant_for(world, reads_only=True)
    without = await _grant_for(world_without_enabler)

    assert await _search_tool(world, reads_only) == PLAIN_SEARCH_TOOL
    assert await _search_tool(world_without_enabler, without) == PLAIN_SEARCH_TOOL


async def test_with_everything_on_the_search_tool_says_nothing_of_it(world):
    world.provider.switched_off.clear()
    world.state["plugins"] = True
    grant = await _grant_for(world)

    search = await _search_tool(world, grant)

    assert "Switched off" not in search["description"] and ENABLE_TOOL not in search["description"]


async def test_what_cannot_be_used_on_this_computer_is_not_asked_for(world):
    world.provider.reason = "no input can be sent on this computer"
    grant = await _grant_for(world)

    search = await _search_tool(world, grant)

    # Only the plugins are left to ask for: the browser and the desktop are out for another reason.
    assert search["description"].endswith(": plugins.")


# ---------------------------------------------------------------------------
# Where a task agent finds out
# ---------------------------------------------------------------------------


async def test_a_search_that_finds_nothing_says_what_is_switched_off(world):
    grant = await _grant_for(world)

    answer = _text(await _call(world, grant, SEARCH_TOOL, {"query": "look at a page"}))

    assert "Switched off right now: browser, desktop, plugins." in answer and _hint("<name>") in answer


async def test_a_search_with_something_on_still_tells_when_the_query_is_about_what_is_off(world):
    world.provider.switched_off.difference_update(BROWSER_TOOLS)
    grant = await _grant_for(world)

    answer = _text(await _call(world, grant, SEARCH_TOOL, {"query": "plugin extensions"}))

    assert _hint("plugins") in answer or _hint("<name>") in answer


@pytest.mark.parametrize("group", ["browser", "desktop", "plugins"])
async def test_loading_a_group_that_is_switched_off_says_how_to_ask(world, group):
    grant = await _grant_for(world)

    answer = await _call(world, grant, SEARCH_TOOL, {"group": group})

    assert answer["isError"] is True and _hint(group) in _text(answer)


async def test_no_hint_for_what_is_forbidden_cannot_be_seen_or_is_not_offered(world, world_without_enabler):
    await world.permission_store.set_mode("plugin_entry", "deny")
    blind = await _grant_for(world, images=False)
    without = await _grant_for(world_without_enabler)

    plugins = _text(await _call(world, blind, SEARCH_TOOL, {"group": "plugins"}))
    desktop = _text(await _call(world, blind, SEARCH_TOOL, {"group": "desktop"}))
    nothing = _text(await _call(world_without_enabler, without, SEARCH_TOOL, {"group": "browser"}))

    assert ENABLE_TOOL not in plugins + desktop + nothing


@pytest.mark.parametrize("tool, group", [("browser_navigate_page", "browser"), ("computer", "desktop")])
async def test_calling_a_tool_that_is_switched_off_says_how_to_ask(world, tool, group):
    grant = await _grant_for(world)

    through = await _call(world, grant, CALL_TOOL, {"name": tool, "arguments": {}})
    direct = await _call(world, grant, tool, {})

    for answer in (through, direct):
        assert answer["isError"] is True and _hint(group) in _text(answer)
    assert world.provider.calls == [] and world.enabler.enable_calls == []


async def test_a_search_of_the_web_while_the_plugins_are_off_says_how_to_ask(world, world_without_enabler):
    commission = await _commission(world)
    token = world.gateway.issue(commission.id, supports_images=True)
    reads_only = await _commission(world)
    world.gateway.issue(reads_only.id, reads_only=True)
    other = await _commission(world_without_enabler)

    said = await world.task_agents._web_search_off_reason(commission.id)
    decision = await world.task_agents._search_permission(commission)
    copy = await world.task_agents._web_search_off_reason(reads_only.id)
    plain = await world_without_enabler.task_agents._web_search_off_reason(other.id)
    await world.permission_store.set_mode("plugin_entry", "deny")
    forbidden = await world.task_agents._web_search_off_reason(commission.id)

    assert token and _hint("plugins") in said and decision.allow is False and _hint("plugins") in decision.reason
    assert ENABLE_TOOL not in copy and ENABLE_TOOL not in plain and ENABLE_TOOL not in forbidden
    assert copy == plain == forbidden == "web search is not available: plugins are switched off"


# ---------------------------------------------------------------------------
# Turning on the browser and the keyboard and mouse
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "asked, canonical, category, group, tool",
    [
        ("browser", "browser", "enable_browser", "browser", "browser_navigate_page"),
        ("desktop", "keyboard_mouse", "enable_keyboard_mouse", "desktop", "computer"),
    ],
)
async def test_an_approved_request_turns_it_on_and_hands_its_tools_over(world, asked, canonical, category, group, tool):
    world.permissions.set_asker(asker := Asker("once"))
    await world.permission_store.set_mode(canonical, "first_ask")
    grant = await _grant_for(world)

    answer = await _call(world, grant, CALL_TOOL, _enable(asked))

    assert answer["isError"] is False
    text = _text(answer)
    assert text.startswith(f"The {group} is on now.") and tool in text
    [(commission_id, ask)] = asker.asked
    assert (ask.category, tuple(ask.options), ask.detail) == (category, ("once", "deny"), {})
    assert world.enabler.enable_calls == [(canonical, "Lan")]
    assert "capability_enabled" in await _event_types(world, commission_id)
    assert grant.loaded_groups == {canonical}
    # The user said yes to this very group: its first call is not asked about again.
    arguments = {"url": "https://example.com"} if group == "browser" else {"action": "screenshot"}
    used = await _call(world, grant, CALL_TOOL, {"name": tool, "arguments": arguments})
    assert used["isError"] is False and len(asker.asked) == 1 and len(world.provider.calls) == 1


async def test_a_setting_that_asks_every_time_still_asks_for_the_call_after_the_switch(world):
    world.permissions.set_asker(asker := Asker("once"))
    await world.permission_store.set_mode("browser", "always_ask")
    grant = await _grant_for(world)

    enabled = await _call(world, grant, CALL_TOOL, _enable("browser"))
    used = await _call(world, grant, CALL_TOOL, {"name": "browser_navigate_page", "arguments": {"url": "https://a.example"}})

    assert enabled["isError"] is False and used["isError"] is False
    assert [ask.category for _cid, ask in asker.asked] == ["enable_browser", "browser"]


async def test_a_declined_request_changes_nothing(world):
    world.permissions.set_asker(asker := Asker("deny"))
    grant = await _grant_for(world)

    answer = await _call(world, grant, CALL_TOOL, _enable("browser"))

    assert answer["isError"] is True and "declined" in _text(answer)
    assert world.enabler.enable_calls == [] and len(asker.asked) == 1
    assert world.provider.switched_off == set(BUILTIN_NAMES)
    assert "capability_denied" in await _event_types(world, grant.commission_id)


@pytest.mark.parametrize("asked, canonical", [("browser", "browser"), ("desktop", "keyboard_mouse"), ("plugins", "plugin_entry")])
async def test_what_the_settings_forbid_is_neither_asked_nor_done(world, asked, canonical):
    world.permissions.set_asker(asker := Asker("once"))
    await world.permission_store.set_mode(canonical, "deny")
    grant = await _grant_for(world)

    answer = await _call(world, grant, CALL_TOOL, _enable(asked))

    assert answer["isError"] is True and _text(answer) == DENIED_BY_SETTINGS
    assert asker.asked == [] and world.enabler.enable_calls == [] and world.enabler.can_calls == []


@pytest.mark.parametrize("asked", ["browser", "desktop", "plugins"])
async def test_unreadable_settings_forbid_everything_and_nothing_is_asked(world, tmp_path, asked):
    # The settings file is read on first use: a broken one counts as deny for every category.
    (tmp_path / "nekopower").mkdir(parents=True, exist_ok=True)
    (tmp_path / "nekopower" / "permissions.json").write_text("{", encoding="utf-8")
    world.permissions.set_asker(asker := Asker("once"))
    grant = await _grant_for(world)

    search = await _search_tool(world, grant)
    answer = await _call(world, grant, CALL_TOOL, _enable(asked))

    assert "Switched off" not in search["description"]
    assert answer["isError"] is True and _text(answer) == DENIED_BY_SETTINGS
    assert asker.asked == [] and world.enabler.enable_calls == [] and world.enabler.can_calls == []


async def test_what_cannot_be_turned_on_is_not_asked_about(world):
    world.permissions.set_asker(asker := Asker("once"))
    world.enabler.can["browser"] = (False, "no usable browser")
    grant = await _grant_for(world)

    answer = await _call(world, grant, CALL_TOOL, _enable("browser"))

    assert answer["isError"] is True and "cannot be turned on: no usable browser" in _text(answer)
    assert asker.asked == [] and world.enabler.enable_calls == []


async def test_the_desktop_is_not_asked_for_by_a_model_that_cannot_see(world):
    world.permissions.set_asker(asker := Asker("once"))
    grant = await _grant_for(world, images=False)

    answer = await _call(world, grant, CALL_TOOL, _enable("desktop"))

    assert answer["isError"] is True and "work from pictures" in _text(answer)
    assert asker.asked == [] and world.enabler.enable_calls == []


async def test_a_copy_that_only_reads_cannot_ask(world):
    world.permissions.set_asker(asker := Asker("once"))
    grant = await _grant_for(world, reads_only=True)

    answer = await _call(world, grant, CALL_TOOL, _enable("browser"))

    assert answer["isError"] is True and "only reads" in _text(answer)
    assert asker.asked == [] and world.enabler.enable_calls == []


async def test_an_unknown_name_is_answered_with_the_ones_that_can_be_asked_for(world):
    world.permissions.set_asker(asker := Asker("once"))
    grant = await _grant_for(world)

    answer = await _call(world, grant, CALL_TOOL, _enable("files"))

    assert answer["isError"] is True and "nothing named" in _text(answer) and "browser, desktop, plugins" in _text(answer)
    assert asker.asked == [] and world.enabler.enable_calls == []


async def test_what_is_on_already_is_handed_over_without_asking(world):
    world.permissions.set_asker(asker := Asker("once"))
    world.provider.switched_off.difference_update(BROWSER_TOOLS)
    world.state["plugins"] = True
    grant = await _grant_for(world)

    browser = await _call(world, grant, CALL_TOOL, _enable("browser"))
    plugins = await _call(world, grant, CALL_TOOL, _enable("plugins"))

    assert browser["isError"] is False and "browser_navigate_page" in _text(browser)
    assert plugins["isError"] is False and "on already" in _text(plugins)
    assert asker.asked == [] and world.enabler.enable_calls == []


# ---------------------------------------------------------------------------
# Turning on the plugins
# ---------------------------------------------------------------------------


async def test_an_approved_request_for_the_plugins_turns_them_on_and_lists_them(world):
    world.permissions.set_asker(asker := Asker("once"))
    grant = await _grant_for(world)

    answer = await _call(world, grant, CALL_TOOL, _enable("plugins"))

    assert answer["isError"] is False
    text = _text(answer)
    assert text.startswith("The plugins are on now.") and "weather" in text.lower()
    [(_cid, ask)] = asker.asked
    assert (ask.category, tuple(ask.options), ask.detail) == ("enable_plugins", ("once", "deny"), {})
    assert world.enabler.enable_calls == [("plugins", "Lan")]
    found = _found(_text(await _call(world, grant, SEARCH_TOOL, {"query": "weather for a city"})))
    assert found


async def test_a_switch_that_could_not_be_turned_on_is_told_as_failed(world):
    world.permissions.set_asker(Asker("once"))
    world.enabler.result["plugins"] = (False, "the plugin server did not respond in time")
    grant = await _grant_for(world)

    answer = await _call(world, grant, CALL_TOOL, _enable("plugins"))

    assert answer["isError"] is True and "could not be turned on: the plugin server did not respond in time" in _text(answer)
    assert "capability_enabled" not in await _event_types(world, grant.commission_id)
    assert world.state["plugins"] is False


# ---------------------------------------------------------------------------
# dsh asks ahead of the call
# ---------------------------------------------------------------------------


async def test_a_request_ruled_on_ahead_is_not_asked_again_when_it_comes(world):
    world.permissions.set_asker(asker := Asker("once"))
    grant = await _grant_for(world)

    await world.gateway.clear_call(grant.commission_id, CALL_TOOL, _enable("browser"))
    assert len(asker.asked) == 1 and world.enabler.enable_calls == []
    answer = await _call(world, grant, CALL_TOOL, _enable("browser"))

    assert answer["isError"] is False and len(asker.asked) == 1
    assert world.enabler.enable_calls == [("browser", "Lan")]
    # What was approved is checked once more when it comes: the computer may have changed meanwhile.
    assert world.enabler.can_calls == ["browser", "browser"]


async def test_a_refusal_ruled_on_ahead_is_given_without_asking_or_looking_again(world):
    world.permissions.set_asker(asker := Asker("deny"))
    grant = await _grant_for(world)

    await world.gateway.clear_call(grant.commission_id, ENABLE_TOOL, {"capability": "browser"})
    answer = await _call(world, grant, ENABLE_TOOL, {"capability": "browser"})

    assert answer["isError"] is True and "declined" in _text(answer)
    assert len(asker.asked) == 1 and world.enabler.enable_calls == []
    assert world.enabler.can_calls == ["browser"]


async def test_a_setting_that_turned_to_deny_after_the_answer_wins(world):
    world.permissions.set_asker(Asker("once"))
    grant = await _grant_for(world)

    await world.gateway.clear_call(grant.commission_id, CALL_TOOL, _enable("browser"))
    await world.permission_store.set_mode("browser", "deny")
    answer = await _call(world, grant, CALL_TOOL, _enable("browser"))

    assert answer["isError"] is True and _text(answer) == DENIED_BY_SETTINGS
    assert world.enabler.enable_calls == []


async def test_nothing_is_ruled_on_ahead_for_a_copy_that_only_reads(world):
    world.permissions.set_asker(asker := Asker("once"))
    grant = await _grant_for(world, reads_only=True)

    await world.gateway.clear_call(grant.commission_id, CALL_TOOL, _enable("browser"))

    assert asker.asked == []


# ---------------------------------------------------------------------------
# The words of the question, in every language
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("category", ["enable_browser", "enable_keyboard_mouse", "enable_plugins"])
def test_the_question_is_worded_in_every_language(category):
    from config.prompts.prompts_nekopower_brain import PERMISSION_CATEGORY_TEXTS
    from nekopower.host.reports import describe_permission, page_question, permission_kind, render_question

    question = {"kind": "permission", "category": category, "detail": {}, "options": ["once", "deny"]}

    assert len(PERMISSION_CATEGORY_TEXTS) == 8
    for lang, texts in PERMISSION_CATEGORY_TEXTS.items():
        assert texts[category].strip() and "{" not in texts[category], (lang, category)
        assert describe_permission(question, lang).strip(), (lang, category)
        assert render_question("title", question, lang).strip(), (lang, category)
        page = page_question(question, lang)
        assert page is not None and page["line"].strip() and not page["line_cut"], (lang, category)
    kind = permission_kind(question)
    assert kind and "{" not in kind


# ---------------------------------------------------------------------------
# What an answer stands on: the run it was given in, and the settings it was given under (#4169 review)
# ---------------------------------------------------------------------------


def _denied_events(events: list[Any], capability: str) -> list[Any]:
    return [
        event
        for event in events
        if event.type == "capability_denied" and event.payload.get("capability") == capability
    ]


class _TightensAndRestores:
    """An asker that says yes after the setting was tightened and put back while the question stood."""

    def __init__(self, store: Any, category: str, restored: str = "first_ask") -> None:
        self.store, self.category, self.restored = store, category, restored
        self.asked: list[Any] = []

    async def __call__(self, commission_id: str, ask: Any) -> str:
        self.asked.append((commission_id, ask))
        await self.store.set_mode(self.category, "deny")
        await self.store.set_mode(self.category, self.restored)
        return "once"


async def test_an_approved_request_covers_the_next_call_while_nothing_has_changed(world):
    world.permissions.set_asker(Asker("once"))
    await world.permission_store.set_mode("browser", "first_ask")
    grant = await _grant_for(world)

    await _call(world, grant, CALL_TOOL, _enable("browser"))

    # The baseline of what follows: the loan is there when nothing changed under the question.
    assert world.permissions.run_grants(grant.commission_id)


async def test_text_another_session_adds_while_the_switch_is_turned_on_ends_what_the_answer_covers(world):
    world.permissions.set_asker(asker := Asker("once"))
    await world.permission_store.set_mode("browser", "first_ask")
    grant = await _grant_for(world)

    async def text_arrives() -> None:
        world.permissions.received_external_input(grant.commission_id)

    world.enabler.during_enable = text_arrives

    enabled = await _call(world, grant, CALL_TOOL, _enable("browser"))
    covered_after_enabling = world.permissions.run_grants(grant.commission_id)
    used = await _call(world, grant, CALL_TOOL, {"name": "browser_navigate_page", "arguments": {"url": "https://a.example"}})

    # The switch is on (the user said yes to that); the old yes covers nothing after the new text.
    assert enabled["isError"] is False and world.enabler.enable_calls == [("browser", "Lan")]
    assert not covered_after_enabling and used["isError"] is False
    assert [ask.category for _cid, ask in asker.asked] == ["enable_browser", "browser"]


async def test_text_added_between_the_answer_ahead_of_the_call_and_the_call_turns_nothing_on(world):
    world.permissions.set_asker(asker := Asker("once"))
    await world.permission_store.set_mode("browser", "first_ask")
    grant = await _grant_for(world)

    await world.gateway.clear_call(grant.commission_id, CALL_TOOL, _enable("browser"))
    world.permissions.received_external_input(grant.commission_id)
    answer = await _call(world, grant, CALL_TOOL, _enable("browser"))

    assert answer["isError"] is True and _text(answer) == _APPROVAL_STALE
    assert world.enabler.enable_calls == [] and len(asker.asked) == 1
    assert not world.permissions.run_grants(grant.commission_id)
    assert len(_denied_events(await world.ledger.events(grant.commission_id), "host:enable:browser")) == 1


async def test_a_setting_tightened_and_put_back_while_the_user_was_asked_turns_nothing_on(world):
    await world.permission_store.set_mode("browser", "first_ask")
    world.permissions.set_asker(asker := _TightensAndRestores(world.permission_store, "browser"))
    grant = await _grant_for(world)

    answer = await _call(world, grant, CALL_TOOL, _enable("browser"))

    assert answer["isError"] is True and _text(answer) == _APPROVAL_STALE
    assert world.enabler.enable_calls == [] and len(asker.asked) == 1
    assert not world.permissions.run_grants(grant.commission_id)


async def test_a_setting_tightened_and_put_back_while_the_switch_is_turned_on_covers_no_call(world):
    world.permissions.set_asker(asker := Asker("once"))
    await world.permission_store.set_mode("browser", "first_ask")
    grant = await _grant_for(world)

    async def tightened_then_restored() -> None:
        await world.permission_store.set_mode("browser", "deny")
        await world.permission_store.set_mode("browser", "first_ask")

    world.enabler.during_enable = tightened_then_restored

    enabled = await _call(world, grant, CALL_TOOL, _enable("browser"))
    used = await _call(world, grant, CALL_TOOL, {"name": "browser_navigate_page", "arguments": {"url": "https://a.example"}})

    assert enabled["isError"] is False and used["isError"] is False
    assert [ask.category for _cid, ask in asker.asked] == ["enable_browser", "browser"]


async def test_a_setting_tightened_and_put_back_while_the_lineage_is_read_grants_no_loan(world):
    await world.permission_store.set_mode("browser", "first_ask")
    grant = await _grant_for(world)
    scope = GrantScope(KIND_BUILTIN, "browser")
    asked: list[str] = []

    async def parent_of(commission_id: str) -> None:
        asked.append(commission_id)
        await world.permission_store.set_mode("browser", "deny")
        await world.permission_store.set_mode("browser", "first_ask")
        return None

    world.permissions.set_parent_lookup(parent_of)

    granted = await world.permissions.grant_run(grant.commission_id, "browser", scope=scope)

    assert asked and granted is False
    assert not world.permissions.run_grants(grant.commission_id)
    # Without the tightening in between it is granted.
    world.permissions.set_parent_lookup(None)
    assert await world.permissions.grant_run(grant.commission_id, "browser", scope=scope) is True


async def test_an_answer_that_no_longer_stands_grants_no_loan_when_the_caller_says_so(world):
    await world.permission_store.set_mode("browser", "first_ask")
    grant = await _grant_for(world)

    granted = await world.permissions.grant_run(
        grant.commission_id, "browser", scope=GrantScope(KIND_BUILTIN, "browser"), still_valid=lambda: False
    )

    assert granted is False and not world.permissions.run_grants(grant.commission_id)


async def test_a_refusal_ruled_on_ahead_by_the_settings_is_recorded_once(world):
    await world.permission_store.set_mode("browser", "first_ask")
    # Yes is answered, and the setting is denied while the question stands: refused at once, on record once.
    world.permissions.set_asker(_DeniesWhileAsked(world.permission_store))
    grant = await _grant_for(world)

    await world.gateway.clear_call(grant.commission_id, CALL_TOOL, _enable("browser"))
    answer = await _call(world, grant, CALL_TOOL, _enable("browser"))

    assert answer["isError"] is True and _text(answer) == DENIED_BY_SETTINGS
    assert world.enabler.enable_calls == []
    assert len(_denied_events(await world.ledger.events(grant.commission_id), "host:enable:browser")) == 1


class _DeniesWhileAsked:
    def __init__(self, store: Any) -> None:
        self.store = store

    async def __call__(self, commission_id: str, ask: Any) -> str:
        await self.store.set_mode("browser", "deny")
        return "once"


# ---------------------------------------------------------------------------
# What is said of what may be asked for (#4169 review)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "off, query, expected",
    [
        (["desktop", "plugins"], "the a to", False),
        (["desktop", "plugins"], "navigate to a url", False),
        (["desktop", "plugins"], "take a screenshot", True),
        (["desktop"], "click the mouse", True),
        (["plugins"], "do a web search", True),
        (["plugins"], "an extension for mcp", True),
        (["browser"], "open the website", True),
        (["browser"], "something about the plugin", False),
        (["browser"], "打开网页", True),
        (["desktop"], "点击桌面", True),
        (["plugins"], "用个插件", True),
        ([], "screenshot", False),
        (["browser"], "browser_navigate_page", True),
        (["desktop"], "computer_screenshot", True),
        (["desktop"], "typing into a form", True),
        (["desktop"], "use the computer", True),
    ],
)
async def test_a_query_is_about_what_is_off_by_a_few_words_not_by_common_ones(world, off, query, expected):
    assert world.gateway._off_matches(off, query) is expected


async def test_what_the_process_says_may_not_be_asked_for_is_not_offered(world):
    world.enabler.blocked = {"keyboard_mouse"}
    grant = await _grant_for(world)

    search = await _search_tool(world, grant)
    loaded = _text(await _call(world, grant, SEARCH_TOOL, {"group": "desktop"}))
    direct = _text(await _call(world, grant, "computer", {}))
    nothing_found = _text(await _call(world, grant, SEARCH_TOOL, {"query": "look at a page"}))

    assert search["description"].endswith(": browser, plugins.")
    assert _hint("desktop") not in loaded + direct and "no capability named computer" in direct
    assert "Switched off right now: browser, plugins." in nothing_found

    world.enabler.blocked = {"plugins"}
    plugins = _text(await _call(world, grant, SEARCH_TOOL, {"group": "plugins"}))
    assert ENABLE_TOOL not in plugins


async def test_a_commission_with_no_token_here_is_not_told_to_ask(world):
    # No call tool to ask with: the hint would send it to one it does not have.
    stranger = await _commission(world)
    covered = await _commission(world)
    world.gateway.issue(covered.id)

    assert world.gateway.can_enable_for(stranger.id) is False
    assert world.gateway.can_enable_for(covered.id) is True
    assert world.gateway.can_enable_for("no-such-commission") is False
    assert ENABLE_TOOL not in await world.task_agents._web_search_off_reason(stranger.id)
    assert ENABLE_TOOL in await world.task_agents._web_search_off_reason(covered.id)


async def test_plugins_that_are_switched_on_but_not_ready_are_not_told_as_not_turned_on(world):
    world.permissions.set_asker(Asker("once"))
    world.enabler.result["plugins"] = (False, "the plugin server did not respond in time")

    async def switch_stays_on() -> None:
        world.state["plugins"] = True

    world.enabler.during_enable = switch_stays_on
    grant = await _grant_for(world)

    answer = await _call(world, grant, CALL_TOOL, _enable("plugins"))

    text = _text(answer)
    assert answer["isError"] is True
    assert "switch is on, but they are not ready yet: the plugin server did not respond in time" in text
    assert "could not be turned on" not in text


async def test_what_was_turned_on_meanwhile_by_someone_else_is_not_turned_on_again(world):
    world.permissions.set_asker(asker := Asker("once"))
    grant = await _grant_for(world)

    async def asked_then_the_user_turned_it_on_at_the_panel(commission_id: str, ask: Any) -> str:
        asker.asked.append((commission_id, ask))
        world.provider.switched_off.difference_update(BROWSER_TOOLS)
        return "once"

    world.permissions.set_asker(asked_then_the_user_turned_it_on_at_the_panel)

    answer = await _call(world, grant, CALL_TOOL, _enable("browser"))

    assert answer["isError"] is False and "browser_navigate_page" in _text(answer)
    assert world.enabler.enable_calls == []


async def test_text_added_while_the_registry_is_read_after_the_answer_turns_nothing_on(world):
    # The registry may ask the plugin server (a cached list gone stale): what is awaited there is no
    # reason to look at what the answer stands on any earlier than just before turning on.
    armed: list[bool] = []

    async def asked(commission_id: str, ask: Any) -> str:
        armed.append(True)
        return "once"

    world.permissions.set_asker(asked)
    grant = await _grant_for(world)
    read = world.registry.callable_by_task_agents

    async def read_then_text_arrives() -> Any:
        found = await read()
        if armed:
            world.permissions.received_external_input(grant.commission_id)
        return found

    world.registry.callable_by_task_agents = read_then_text_arrives

    answer = await _call(world, grant, CALL_TOOL, _enable("browser"))

    assert answer["isError"] is True and _text(answer) == _APPROVAL_STALE
    assert world.enabler.enable_calls == [] and not world.permissions.run_grants(grant.commission_id)


async def test_text_added_while_the_computer_is_checked_turns_nothing_on(world):
    # The enabler checks this computer before it writes the switch; what the answer stood on is
    # looked at again there (#4182 review), not only before the enabler is called.
    world.permissions.set_asker(asker := Asker("once"))
    await world.permission_store.set_mode("browser", "first_ask")
    grant = await _grant_for(world)

    async def text_arrives() -> None:
        world.permissions.received_external_input(grant.commission_id)

    world.enabler.before_commit = text_arrives

    answer = await _call(world, grant, CALL_TOOL, _enable("browser"))

    assert answer["isError"] is True and _text(answer) == _APPROVAL_STALE
    assert world.provider.switched_off == set(BUILTIN_NAMES) and len(asker.asked) == 1
    assert not world.permissions.run_grants(grant.commission_id)
    events = await world.ledger.events(grant.commission_id)
    assert "capability_enabled" not in [event.type for event in events]
    assert len(_denied_events(events, "host:enable:browser")) == 1


async def test_a_setting_tightened_and_put_back_while_the_computer_is_checked_turns_nothing_on(world):
    world.permissions.set_asker(Asker("once"))
    await world.permission_store.set_mode("browser", "first_ask")
    grant = await _grant_for(world)

    async def tightened_then_restored() -> None:
        await world.permission_store.set_mode("browser", "deny")
        await world.permission_store.set_mode("browser", "first_ask")

    world.enabler.before_commit = tightened_then_restored

    answer = await _call(world, grant, CALL_TOOL, _enable("browser"))

    assert answer["isError"] is True and _text(answer) == _APPROVAL_STALE
    assert world.provider.switched_off == set(BUILTIN_NAMES)
    assert not world.permissions.run_grants(grant.commission_id)
    events = await world.ledger.events(grant.commission_id)
    assert "capability_enabled" not in [event.type for event in events]


async def test_nothing_changing_while_the_computer_is_checked_turns_it_on_as_before(world):
    world.permissions.set_asker(Asker("once"))
    grant = await _grant_for(world)
    seen: list[bool] = []

    async def nothing_happens() -> None:
        seen.append(True)

    world.enabler.before_commit = nothing_happens

    answer = await _call(world, grant, CALL_TOOL, _enable("browser"))

    assert answer["isError"] is False and seen == [True]
    assert world.provider.switched_off.isdisjoint(BROWSER_TOOLS)


async def test_a_setting_turned_to_deny_while_the_computer_is_checked_is_told_as_the_settings_forbidding_it(world):
    world.permissions.set_asker(Asker("once"))
    await world.permission_store.set_mode("browser", "first_ask")
    grant = await _grant_for(world)

    async def forbidden_meanwhile() -> None:
        await world.permission_store.set_mode("browser", "deny")

    world.enabler.before_commit = forbidden_meanwhile

    answer = await _call(world, grant, CALL_TOOL, _enable("browser"))

    assert answer["isError"] is True and _text(answer) == DENIED_BY_SETTINGS
    assert world.provider.switched_off == set(BUILTIN_NAMES)
    assert not world.permissions.run_grants(grant.commission_id)


async def test_plugins_that_failed_for_good_with_the_switch_on_are_told_as_not_turned_on(world):
    world.permissions.set_asker(Asker("once"))
    world.enabler.result["plugins"] = (False, "starting the plugin server was cancelled")

    async def switch_stays_on() -> None:
        world.state["plugins"] = True

    world.enabler.during_enable = switch_stays_on
    grant = await _grant_for(world)

    text = _text(await _call(world, grant, CALL_TOOL, _enable("plugins")))

    assert "could not be turned on: starting the plugin server was cancelled" in text
    assert "not ready yet" not in text


async def test_a_refusal_by_the_user_does_not_keep_a_later_refusal_by_the_settings_off_the_record(world):
    world.permissions.set_asker(Asker("deny"))
    grant = await _grant_for(world)

    await world.gateway.clear_call(grant.commission_id, ENABLE_TOOL, {"capability": "browser"})
    await world.permission_store.set_mode("browser", "deny")
    answer = await _call(world, grant, ENABLE_TOOL, {"capability": "browser"})

    assert answer["isError"] is True
    reasons = [
        event.payload.get("reason")
        for event in await world.ledger.events(grant.commission_id)
        if event.type == "capability_denied" and event.payload.get("capability") == "host:enable:browser"
    ]
    assert DENIED_BY_SETTINGS in reasons and len(reasons) == 2
