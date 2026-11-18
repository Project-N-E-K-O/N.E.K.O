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

"""The built-in tools as they are shipped: their names and what they cost a task agent.

The keyboard-and-mouse tool and the browser tools are not listed to a task
agent. Every request carries one line about each of the two groups, which is
a fixed cost; a task agent that needs a group loads it, and what it is handed
then stays in its conversation. These tests serve the real definitions
through a gateway and keep both costs under their ceilings, every shipped
tool loaded whole and callable, and the text written for the groups in step
with them.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import pytest

from config.nekoclaw_settings import GATEWAY_RESULT_MAX_TOKENS, GATEWAY_TOOL_GROUP_MAX_TOKENS
from config.prompts import prompts_nekoclaw as texts
from config.prompts.prompts_nekoclaw_builtin import (
    BROWSER_SCREENSHOT_TOOL_NAME,
    BROWSER_SNAPSHOT_TOOL_NAME,
    BROWSER_TOOL_NAMES,
    BROWSER_TOOLS,
    BUILTIN_GROUP_GUIDANCE,
    BUILTIN_GROUP_NAMES,
    BUILTIN_GROUP_SUMMARIES,
    COMPUTER_TOOL,
    COMPUTER_TOOL_NAME,
    BUILTIN_GROUP_FIRST_CALLS,
    TOOL_GROUP_ARGUMENT,
    TOOL_GROUP_FIRST_CALL,
    TOOL_GROUP_FIRST_RESULT,
    TOOL_GROUP_LOADED_HEADER,
    TOOL_GROUP_NOT_LOADED,
    TOOL_GROUP_NOT_READ,
    TOOL_GROUP_POINTER,
    TOOL_GROUPS_INTRO,
)
from config.prompts.prompts_nekoclaw_tools import DSH_TOOL_SCHEMAS, NEKOCLAW_TOOL_SCHEMAS
from nekoclaw.host.capabilities import (
    BUILTIN_GROUP_BROWSER,
    BUILTIN_GROUP_KEYBOARD_MOUSE,
    BUILTIN_GROUPS,
    builtin_tool,
    gateway_tool_name,
)
from nekoclaw.host.gateway import CALL_TOOL, SEARCH_TOOL
from nekoclaw.runtime.context import ContextPolicy
from nekoclaw.runtime.host import HOST_CALLS
from nekoclaw.runtime.prompt import build_system_prompt
from nekoclaw.runtime.protocol import ModelConfig, RuntimeLimits
from nekoclaw.runtime.tools import build_tools
from nekoclaw.runtime.tools.base import SessionInbox, SpillStore, ToolContext
from nekoclaw.runtime.tools.gateway import GatewayClient, build_gateway_tools
from nekoclaw.runtime.tools.goal import GoalState
from nekoclaw.runtime.tools.jobs import JobRegistry
from nekoclaw.runtime.tools.todo import TodoState
from tests.unit.nekoclaw.host_fakes import FakeDecisionLLM, make_host
from tests.unit.nekoclaw.plugin_server_fake import FakePluginServer
from utils.tokenize import count_tokens

DEFINITIONS: tuple[dict[str, Any], ...] = (COMPUTER_TOOL, *BROWSER_TOOLS)
NAMES: list[str] = [definition["name"] for definition in DEFINITIONS]
# The shipped tools of each group, by the name a model is given for the group.
SHIPPED = {"browser": BROWSER_TOOLS, "desktop": (COMPUTER_TOOL,)}
# What every request of a task agent may carry of the two groups: the search
# and the call tool, with the line about each group. Measured at 329 when
# this was written; before the groups were loaded on demand the definitions
# and the guidance took 2227 in every request.
UP_FRONT_MAX_TOKENS = 350
# Of that, the part the groups add to a search tool that is offered anyway
# (a task agent with plugin entries it was not all listed has it already).
# Measured at 171.
GROUP_LINES_MAX_TOKENS = 190
GUIDANCE_MAX_TOKENS = 360


class ShippedTools:
    """Lists the shipped definitions the way the provider of the built-in tools does."""

    def available(self) -> list[Any]:
        return [
            builtin_tool(BUILTIN_GROUP_KEYBOARD_MOUSE, COMPUTER_TOOL, needs_images=True),
            *(
                builtin_tool(
                    BUILTIN_GROUP_BROWSER,
                    definition,
                    needs_images=definition["name"] == BROWSER_SCREENSHOT_TOOL_NAME,
                )
                for definition in BROWSER_TOOLS
            ),
        ]

    async def run(self, capability: Any, *, commission_id: str, arguments: dict[str, Any]) -> Any:
        raise AssertionError("no tool is called in these tests")

    async def released(self, group: str, commission_id: str) -> None:
        return None


@pytest.fixture
async def host(monkeypatch: Any, tmp_path: Path) -> Any:
    server = FakePluginServer.install(monkeypatch)
    built = make_host(tmp_path, server, decision_llm=FakeDecisionLLM(), builtins=ShippedTools())
    yield built
    await built.shutdown()


@pytest.fixture
async def bare_host(monkeypatch: Any, tmp_path: Path) -> Any:
    """A host without built-in tools, with one plugin entry that is not listed up front."""
    from tests.unit.nekoclaw.plugin_server_fake import entry, plugin

    server = FakePluginServer.install(monkeypatch)
    server.add_plugin(plugin("lamp", "Controls the desk lamp", [entry("switch", "Turn the lamp on or off")]))
    built = make_host(tmp_path, server, decision_llm=FakeDecisionLLM())
    yield built
    await built.shutdown()


async def _grant(host: Any, *, images: bool) -> Any:
    commission, _created = await host.ledger.create(
        lanlan_name="Lan", title="book a table", dispatch_text="book a table", source="dispatch", route="task_agent"
    )
    await host.ledger.transition(commission.id, "working")
    return host.gateway._grant(host.gateway.issue(commission.id, supports_images=images))


async def _listed(host: Any, *, images: bool) -> list[dict[str, Any]]:
    """What the gateway offers a task agent up front."""
    return await host.gateway.tool_definitions(await _grant(host, images=images))


async def _loaded(host: Any, group: str, *, images: bool) -> str:
    """What loading a group hands a task agent."""
    result = await host.gateway.call_tool(await _grant(host, images=images), SEARCH_TOOL, {"group": group})
    assert result["isError"] is False, result
    (block,) = result["content"]
    return block["text"]


def _served(definition: dict[str, Any]) -> dict[str, Any]:
    return {
        "name": definition["name"],
        "description": definition["description"],
        "inputSchema": definition["parameters"],
    }


def _as_sent(tools: list[dict[str, Any]]) -> str:
    """Tool definitions the way a session counts them as part of every request."""
    return json.dumps([[tool["name"], tool["description"], tool["inputSchema"]] for tool in tools], ensure_ascii=False)


PLATFORMS = (("win32", "C:\\work"), ("linux", "/work"))


def _own_tools(platform: str, workdir: str, *, images: bool = True) -> list[Any]:
    """The runtime's own tools as a task agent started by the host has them: every host call on offer."""

    class Host:
        capabilities = frozenset(HOST_CALLS)

        async def call(self, session_id: str, name: str, payload: dict[str, Any]) -> dict[str, Any]:
            raise AssertionError("no host call is made while building tools")

    context = ToolContext(
        session_id="names",
        workdir=Path(workdir),
        spill=SpillStore(Path(workdir) / "outputs"),
        host=Host(),
        limits=RuntimeLimits(),
        supports_images=images,
        platform=platform,
        inbox=SessionInbox(),
        jobs=JobRegistry(lambda job: None),
        todos=TodoState(),
        goals=GoalState(default_rounds=20, max_rounds=100),
        emit_report=lambda payload: None,
    )
    return build_tools(context)


def _runtime_tool_names() -> set[str]:
    """Every tool name the runtime can have itself, whatever the platform, the model and the host."""
    names = set(DSH_TOOL_SCHEMAS) | set(NEKOCLAW_TOOL_SCHEMAS)
    for platform, workdir in PLATFORMS:
        names |= {tool.name for tool in _own_tools(platform, workdir)}
    return names


# ---------------------------------------------------------------------------
# What every request carries
# ---------------------------------------------------------------------------


async def test_no_shipped_tool_is_listed_and_every_request_carries_one_line_about_each_group(host):
    listed = await _listed(host, images=True)

    assert [item["name"] for item in listed] == [SEARCH_TOOL, CALL_TOOL]
    description = listed[0]["description"].splitlines()
    assert description[1:] == [
        "Tool groups, not among your tools until used. To use one, make its first call below through "
        "capability_call: the answer also brings the group's other tools and how to use them.",
        f"- browser: {BUILTIN_GROUP_SUMMARIES['browser']} First call: browser_navigate_page, "
        'arguments {"url": "..."}.',
        f"- desktop: {BUILTIN_GROUP_SUMMARIES['keyboard_mouse']} First call: computer, "
        'arguments {"action": "screenshot"}.',
    ]
    assert listed[0]["inputSchema"]["properties"]["group"] == {
        "type": "string",
        "enum": ["browser", "desktop"],
        "description": TOOL_GROUP_ARGUMENT,
    }
    # Not one shipped tool is defined or described in it, nor is there a
    # word of how a group is used. The one browser tool that is named is
    # the call the group is started with.
    sent = json.dumps(listed)
    assert not [name for name in NAMES if f'"name": "{name}"' in sent]
    assert not [item["name"] for item in DEFINITIONS if item["description"] in sent]
    assert [name for name in BROWSER_TOOL_NAMES if name in sent] == ["browser_navigate_page"]
    for guidance in BUILTIN_GROUP_GUIDANCE.values():
        assert guidance[:60] not in sent


async def test_what_every_request_carries_of_the_groups_stays_under_its_ceiling(host, bare_host):
    with_groups = await _listed(host, images=True)
    without = await _listed(bare_host, images=True)
    assert [item["name"] for item in without] == [SEARCH_TOOL, CALL_TOOL]

    up_front = count_tokens(_as_sent(with_groups))
    lines = up_front - count_tokens(_as_sent(without))

    assert up_front <= UP_FRONT_MAX_TOKENS, (
        f"the two tools the built-in groups are loaded and called through cost {up_front} tokens in every request "
        f"of a task agent; keep them under {UP_FRONT_MAX_TOKENS}"
    )
    assert 0 < lines <= GROUP_LINES_MAX_TOKENS, (
        f"the lines about the built-in groups cost {lines} tokens in every request of a task agent; keep them "
        f"under {GROUP_LINES_MAX_TOKENS}"
    )


# ---------------------------------------------------------------------------
# What loading a group hands over
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("group", sorted(SHIPPED))
async def test_a_shipped_group_is_loaded_whole_within_its_cap(host, group):
    text = await _loaded(host, group, images=True)

    header, guidance, definitions = text.split("\n\n")
    key = next(key for key, name in BUILTIN_GROUP_NAMES.items() if name == group)
    assert header == TOOL_GROUP_LOADED_HEADER.format(name=group, call_tool=CALL_TOOL)
    assert guidance == BUILTIN_GROUP_GUIDANCE[key]
    # Every tool of the group, in the order they are defined, exactly as
    # written: nothing was cut to make it fit.
    assert [json.loads(line) for line in definitions.splitlines()] == [_served(item) for item in SHIPPED[group]]
    cost = count_tokens(text)
    assert cost <= GATEWAY_TOOL_GROUP_MAX_TOKENS, (
        f"loading the {group} tools hands over {cost} tokens, over the cap of {GATEWAY_TOOL_GROUP_MAX_TOKENS}: "
        "the end of it would be cut off"
    )


@pytest.mark.parametrize(("group", "tool"), [("browser", "browser_click"), ("desktop", COMPUTER_TOOL_NAME)])
async def test_a_group_handed_over_in_place_of_a_call_is_whole_too(host, group, tool):
    grant = await _grant(host, images=True)

    refused = await host.gateway.call_tool(grant, CALL_TOOL, {"name": tool, "arguments": {}})

    assert refused["isError"] is True
    said, handed_over = refused["content"][0]["text"].split("\n\n", 1)
    assert said == TOOL_GROUP_NOT_LOADED.format(tool=tool, name=group)
    assert handed_over == await _loaded(host, group, images=True)
    # The gateway's own cap on a result leaves it alone.
    assert count_tokens(refused["content"][0]["text"]) <= GATEWAY_RESULT_MAX_TOKENS


async def test_a_loaded_group_is_small_enough_never_to_lose_its_middle_to_the_runtime(host):
    # The runtime cuts old tool results above this size to their head and
    # tail once a conversation grows long, and caps a single result at
    # another: a group a task agent still works from must stay whole.
    model = ModelConfig(model="any", api_key="key")
    policy = ContextPolicy(window=model.context_window, max_output=model.max_output_tokens)

    assert GATEWAY_TOOL_GROUP_MAX_TOKENS <= policy.prune_threshold_tokens
    assert GATEWAY_TOOL_GROUP_MAX_TOKENS <= policy.result_cap
    for group, tool in (("browser", "browser_click"), ("desktop", COMPUTER_TOOL_NAME)):
        refused = await host.gateway.call_tool(await _grant(host, images=True), tool, {})
        assert count_tokens(refused["content"][0]["text"]) <= policy.prune_threshold_tokens, group


async def test_a_group_arrives_whole_in_the_smallest_window_there_is_however_it_is_handed_over(host, tmp_path):
    from nekoclaw.runtime.context import ContextManager
    from nekoclaw.runtime.loop.tools import AgentToolResult

    async def no_summary(messages: Any, max_tokens: int) -> Any:
        raise AssertionError("nothing is summarized here")

    # The smallest window a model may have, and the least it may be given
    # to answer in. The cap on one result is a quarter of the window: far
    # less than a group. A group loaded by its name, or handed over in
    # place of a call, is one text, and a text small enough never to be
    # pruned is not cut by the cap either.
    model = ModelConfig(model="any", api_key="key", context_window=4_000, max_output_tokens=256)
    policy = ContextPolicy(window=model.context_window, max_output=model.max_output_tokens)
    manager = ContextManager(
        policy=policy, spill=SpillStore(tmp_path / "outputs"), summarize=no_summary, new_entry_id=lambda: "entry"
    )
    assert policy.result_cap == 1_000
    # What is said in front of a group handed over in place of a call,
    # whichever shipped tool was called. With it, a group at its largest is
    # still a text the runtime never cuts: an answer that is an error is
    # one text to the runtime, so the group cannot be a block of its own
    # there, and this is what keeps it whole.
    said = 4 + max(
        count_tokens(TOOL_GROUP_NOT_LOADED.format(tool=definition["name"], name=group))
        for group, definitions in SHIPPED.items()
        for definition in definitions
    )
    assert GATEWAY_TOOL_GROUP_MAX_TOKENS + said <= policy.prune_threshold_tokens

    for group, tool in (("browser", "browser_click"), ("desktop", COMPUTER_TOOL_NAME)):
        loaded = await _loaded(host, group, images=True)
        refused = await host.gateway.call_tool(await _grant(host, images=True), tool, {})
        for text in (loaded, refused["content"][0]["text"]):
            result = AgentToolResult(content=text)
            assert await manager.cap_tool_result(CALL_TOOL, "call-1", result) is result, group
    # The browser's is over the cap, so it is the rule above that kept it whole.
    assert count_tokens(await _loaded(host, "browser", images=True)) > policy.result_cap
    # Nothing was cut, so nothing was saved.
    assert not (tmp_path / "outputs").exists() or not list((tmp_path / "outputs").iterdir())


def test_how_the_two_groups_are_used_stays_under_its_ceiling():
    cost = sum(count_tokens(text) for text in BUILTIN_GROUP_GUIDANCE.values())

    assert cost < GUIDANCE_MAX_TOKENS, (
        f"how the built-in groups are used costs {cost} tokens in the conversation of a task agent that loaded "
        f"them; keep it under {GUIDANCE_MAX_TOKENS}"
    )


# ---------------------------------------------------------------------------
# The names
# ---------------------------------------------------------------------------


def test_names_are_ones_a_model_may_call_and_no_two_are_alike():
    for name in NAMES:
        assert re.fullmatch(r"^[A-Za-z0-9_-]{1,64}$", name), name
    assert len(set(NAMES)) == len(NAMES)
    assert COMPUTER_TOOL["name"] == COMPUTER_TOOL_NAME
    assert BROWSER_TOOL_NAMES == tuple(definition["name"] for definition in BROWSER_TOOLS)
    # The browser tools are told from everything else by their prefix.
    assert all(name.startswith("browser_") for name in BROWSER_TOOL_NAMES)
    assert {BROWSER_SNAPSHOT_TOOL_NAME, BROWSER_SCREENSHOT_TOOL_NAME} <= set(BROWSER_TOOL_NAMES)


async def test_no_shipped_tool_has_the_name_of_a_tool_the_runtime_or_the_gateway_has_itself(host):
    own = _runtime_tool_names()
    taken = own | {SEARCH_TOOL, CALL_TOOL}
    assert {"read", "write", "bash", "pwsh", "read_image", "report", "subagent"} <= taken

    # Called by name through the call tool, a shipped tool can be mistaken for none of them.
    assert not taken & set(NAMES)
    # Nor for a plugin entry or a tool of a connected service, the only
    # other tools a gateway lists: their names begin with what they are.
    # So no session is ever offered a tool by a shipped tool's name, and
    # how a group is used has no place in a session's system prompt.
    assert not [name for name in NAMES if name.startswith(("plugin__", "mcp__"))]
    assert gateway_tool_name("plugin", "browser", "click").startswith("plugin__")
    assert gateway_tool_name("mcp", "computer", "computer").startswith("mcp__")
    # The runtime drops a gateway tool whose name it has itself: neither of
    # the two the groups are reached through is dropped.
    wrapped = build_gateway_tools(
        GatewayClient("http://127.0.0.1:1/mcp", "token"), await _listed(host, images=True), reserved=own
    )
    assert [tool.name for tool in wrapped] == [SEARCH_TOOL, CALL_TOOL]


@pytest.mark.parametrize("definition", DEFINITIONS, ids=NAMES)
def test_a_definition_says_what_the_tool_does_and_what_it_takes(definition):
    assert set(definition) == {"name", "description", "parameters"}
    assert definition["description"].strip()
    parameters = definition["parameters"]
    assert parameters["type"] == "object"
    assert isinstance(parameters["properties"], dict)
    assert set(parameters.get("required", [])) <= set(parameters["properties"])
    for banned in ("skill", "master", "deepseek"):
        assert banned not in json.dumps(definition).lower()


# ---------------------------------------------------------------------------
# The text written for the groups
# ---------------------------------------------------------------------------


def test_every_group_has_a_name_a_line_and_how_it_is_used():
    assert set(BUILTIN_GROUP_NAMES) == set(BUILTIN_GROUPS)
    assert set(BUILTIN_GROUP_SUMMARIES) == set(BUILTIN_GROUPS)
    assert set(BUILTIN_GROUP_GUIDANCE) == set(BUILTIN_GROUPS)
    # A model's name for a group is one word it can pass as an argument,
    # and no two groups share one.
    names = list(BUILTIN_GROUP_NAMES.values())
    assert len(set(names)) == len(names)
    assert all(re.fullmatch(r"[a-z]+", name) for name in names)
    for text in (*BUILTIN_GROUP_SUMMARIES.values(), *BUILTIN_GROUP_GUIDANCE.values()):
        # One paragraph each: what is handed over is split at blank lines.
        assert text.strip() == text and "\n" not in text
    written = (
        *BUILTIN_GROUP_SUMMARIES.values(),
        *BUILTIN_GROUP_GUIDANCE.values(),
        TOOL_GROUP_ARGUMENT,
        TOOL_GROUP_FIRST_CALL,
        TOOL_GROUP_FIRST_RESULT,
        *(first["example"] for first in BUILTIN_GROUP_FIRST_CALLS.values()),
        TOOL_GROUPS_INTRO,
        TOOL_GROUP_LOADED_HEADER,
        TOOL_GROUP_NOT_LOADED,
        TOOL_GROUP_NOT_READ,
        TOOL_GROUP_POINTER,
    )
    for text in written:
        for banned in ("skill", "master", "deepseek"):
            assert banned not in text.lower()


def test_the_first_call_of_each_group_is_a_shipped_tool_called_the_way_it_is_defined():
    shipped = {BUILTIN_GROUP_BROWSER: BROWSER_TOOLS, BUILTIN_GROUP_KEYBOARD_MOUSE: (COMPUTER_TOOL,)}

    assert set(BUILTIN_GROUP_FIRST_CALLS) == set(BUILTIN_GROUPS)
    for group, first in BUILTIN_GROUP_FIRST_CALLS.items():
        assert set(first) == {"name", "arguments", "given", "example"}
        definition = next(item for item in shipped[group] if item["name"] == first["name"])
        parameters = definition["parameters"]
        # What a model is shown is a call the tool takes: arguments it
        # knows, the required ones among them, each a value it allows.
        example = json.loads(first["example"])
        assert set(example) <= set(parameters["properties"])
        assert set(parameters.get("required", [])) <= set(example)
        for key, value in example.items():
            assert value in parameters["properties"][key].get("enum", [value])
        # What makes a call that step is said of arguments the tool has,
        # in values it allows (``None``: the argument left out).
        assert set(first["arguments"]) | set(first["given"]) <= set(parameters["properties"])
        for key, allowed in first["arguments"].items():
            assert isinstance(allowed, tuple) and allowed
            assert {value for value in allowed if value is not None} <= set(parameters["properties"][key]["enum"])
        for key in first["given"]:
            assert parameters["properties"][key]["type"] == "string"
        # And the call a model is shown is that step.
        assert all(example.get(key) in allowed for key, allowed in first["arguments"].items())
        assert all(isinstance(example.get(key), str) and example[key].strip() for key in first["given"])
    # The browser's tool does more under the same name than go to an
    # address, and none of it is the first call.
    navigate = BUILTIN_GROUP_FIRST_CALLS[BUILTIN_GROUP_BROWSER]
    kinds = set(BROWSER_TOOLS[0]["parameters"]["properties"]["type"]["enum"])
    assert BROWSER_TOOLS[0]["name"] == navigate["name"] and kinds == {"url", "back", "forward", "reload"}
    assert navigate["arguments"] == {"type": ("url", None)} and navigate["given"] == ("url",)


def test_how_a_group_is_used_is_no_longer_a_paragraph_of_the_system_prompt():
    sections = {name for name, _triggers, _text in texts.TOOL_GUIDANCE_SECTIONS}
    triggers = {trigger for _name, names, _text in texts.TOOL_GUIDANCE_SECTIONS for trigger in names}

    assert not sections & {"computer", "browser"}
    assert not triggers & set(NAMES)
    # Whatever tools a session is offered, the shipped ones included by
    # mistake, its system prompt says nothing of how a group is used.
    prompt = build_system_prompt(tool_names=["read", SEARCH_TOOL, CALL_TOOL, *NAMES], workdir="/work", platform="linux")
    for guidance in BUILTIN_GROUP_GUIDANCE.values():
        assert guidance not in prompt and guidance[:60] not in prompt


async def test_a_task_agent_is_told_of_and_handed_exactly_what_its_model_can_use(host):
    seeing = await _listed(host, images=True)
    blind = await _listed(host, images=False)

    # A model that sees no pictures is not told of the keyboard and the
    # mouse, whose only tool works from screenshots, and gets the browser
    # without its screenshot tool.
    assert seeing[0]["inputSchema"]["properties"]["group"]["enum"] == ["browser", "desktop"]
    assert blind[0]["inputSchema"]["properties"]["group"]["enum"] == ["browser"]
    assert "- desktop:" in seeing[0]["description"] and "- desktop:" not in blind[0]["description"]
    handed = await _loaded(host, "browser", images=False)
    assert [json.loads(line)["name"] for line in handed.split("\n\n")[2].splitlines()] == [
        name for name in BROWSER_TOOL_NAMES if name != BROWSER_SCREENSHOT_TOOL_NAME
    ]
    assert BUILTIN_GROUP_GUIDANCE["browser"] in handed
    refused = await host.gateway.call_tool(await _grant(host, images=False), SEARCH_TOOL, {"group": "desktop"})
    assert refused["isError"] is True and BUILTIN_GROUP_GUIDANCE["keyboard_mouse"] not in refused["content"][0]["text"]
