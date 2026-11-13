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

"""The definitions of the built-in tools as they are shipped: their names and what they cost.

The keyboard-and-mouse tool and the browser tools are sent with every request
of a task agent, so their size is a fixed cost, and the gateway leaves out
whatever does not fit its budget for them. These tests serve the real
definitions through a gateway and keep them inside that budget, callable, and
matched by the guidance paragraphs written for them.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import pytest

from config.nekoclaw_settings import GATEWAY_BUILTIN_TOOL_LIST_MAX_TOKENS
from config.prompts import prompts_nekoclaw as texts
from config.prompts.prompts_nekoclaw_builtin import (
    BROWSER_SCREENSHOT_TOOL_NAME,
    BROWSER_SNAPSHOT_TOOL_NAME,
    BROWSER_TOOL_NAMES,
    BROWSER_TOOLS,
    COMPUTER_TOOL,
    COMPUTER_TOOL_NAME,
)
from config.prompts.prompts_nekoclaw_tools import DSH_TOOL_SCHEMAS, NEKOCLAW_TOOL_SCHEMAS
from nekoclaw.host.capabilities import BUILTIN_GROUP_BROWSER, BUILTIN_GROUP_KEYBOARD_MOUSE, builtin_tool
from nekoclaw.host.gateway import CALL_TOOL, SEARCH_TOOL
from nekoclaw.runtime.host import HOST_CALLS
from nekoclaw.runtime.prompt import build_system_prompt
from nekoclaw.runtime.protocol import RuntimeLimits
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
# The two paragraphs written for these tools, by the names they have in
# ``TOOL_GUIDANCE_SECTIONS``.
GUIDANCE = {name: (triggers, text) for name, triggers, text in texts.TOOL_GUIDANCE_SECTIONS}
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


async def _listed(host: Any, *, images: bool) -> list[dict[str, Any]]:
    """What the gateway offers a task agent up front."""
    commission, _created = await host.ledger.create(
        lanlan_name="Lan", title="book a table", dispatch_text="book a table", source="dispatch", route="task_agent"
    )
    await host.ledger.transition(commission.id, "working")
    grant = host.gateway._grant(host.gateway.issue(commission.id, supports_images=images))
    return await host.gateway.tool_definitions(grant)


def _runtime_tool_names() -> set[str]:
    """Every tool name the runtime can have itself, whatever the platform, the model and the host."""
    names = set(DSH_TOOL_SCHEMAS) | set(NEKOCLAW_TOOL_SCHEMAS)

    class Host:
        capabilities = frozenset(HOST_CALLS)

        async def call(self, session_id: str, name: str, payload: dict[str, Any]) -> dict[str, Any]:
            raise AssertionError("no host call is made while building tools")

    for platform, workdir in (("win32", "C:\\work"), ("linux", "/work")):
        context = ToolContext(
            session_id="names",
            workdir=Path(workdir),
            spill=SpillStore(Path(workdir) / "outputs"),
            host=Host(),
            limits=RuntimeLimits(),
            supports_images=True,
            platform=platform,
            inbox=SessionInbox(),
            jobs=JobRegistry(lambda job: None),
            todos=TodoState(),
            goals=GoalState(default_rounds=20, max_rounds=100),
            emit_report=lambda payload: None,
        )
        names |= {tool.name for tool in build_tools(context)}
    return names


# ---------------------------------------------------------------------------
# The budget
# ---------------------------------------------------------------------------


async def test_every_shipped_tool_is_listed_within_the_budget_and_none_is_left_out(host):
    listed = await _listed(host, images=True)

    # All sixteen, in the order they are defined, exactly as written. Nothing
    # was left out, so the search tools are not offered either.
    assert [item["name"] for item in listed] == NAMES
    assert listed == [
        {
            "name": definition["name"],
            "description": definition["description"],
            "inputSchema": definition["parameters"],
        }
        for definition in DEFINITIONS
    ]


async def test_the_shipped_definitions_cost_no_more_than_their_budget(host):
    listed = await _listed(host, images=True)

    # Counted the way the gateway counts: each definition as it is put on the wire.
    costs = {item["name"]: count_tokens(json.dumps(item, ensure_ascii=False)) for item in listed}
    total = sum(costs.values())

    assert total <= GATEWAY_BUILTIN_TOOL_LIST_MAX_TOKENS, (
        f"the built-in tool definitions cost {total} tokens in every request of a task agent, over the "
        f"budget of {GATEWAY_BUILTIN_TOOL_LIST_MAX_TOKENS}; the largest: "
        f"{sorted(costs.items(), key=lambda pair: -pair[1])[:3]}"
    )


def test_the_two_guidance_paragraphs_stay_under_their_ceiling():
    cost = count_tokens(GUIDANCE["computer"][1]) + count_tokens(GUIDANCE["browser"][1])

    assert cost < GUIDANCE_MAX_TOKENS, (
        f"the guidance for the built-in tools costs {cost} tokens in every request of a task agent that "
        f"is offered them; keep it under {GUIDANCE_MAX_TOKENS}"
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
    taken = _runtime_tool_names() | {SEARCH_TOOL, CALL_TOOL}
    assert {"read", "write", "bash", "pwsh", "read_image", "report", "subagent"} <= taken

    assert not taken & set(NAMES)
    # The runtime drops a gateway tool whose name it has itself: none is dropped.
    wrapped = build_gateway_tools(
        GatewayClient("http://127.0.0.1:1/mcp", "token"), await _listed(host, images=True), reserved=taken
    )
    assert [tool.name for tool in wrapped] == NAMES


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
# The guidance that goes with them
# ---------------------------------------------------------------------------


def test_each_guidance_paragraph_is_triggered_by_a_tool_that_is_shipped():
    computer_triggers, _text = GUIDANCE["computer"]
    browser_triggers, _text = GUIDANCE["browser"]

    assert COMPUTER_TOOL_NAME in computer_triggers
    assert set(browser_triggers) & set(BROWSER_TOOL_NAMES)
    # Neither appears for the other group's tools.
    assert not set(computer_triggers) & set(BROWSER_TOOL_NAMES)
    assert COMPUTER_TOOL_NAME not in browser_triggers
    for _triggers, text in (GUIDANCE["computer"], GUIDANCE["browser"]):
        for banned in ("skill", "master", "deepseek"):
            assert banned not in text.lower()


async def test_a_task_agent_is_told_how_to_use_exactly_the_groups_it_is_offered(host):
    def prompt(listed: list[dict[str, Any]]) -> str:
        return build_system_prompt(
            tool_names=["read", *(item["name"] for item in listed)], workdir="/work", platform="linux"
        )

    seeing = await _listed(host, images=True)
    blind = await _listed(host, images=False)

    # A model that sees no pictures gets no keyboard-and-mouse tool and no
    # screenshots, but the rest of the browser, and the paragraph for it.
    assert [item["name"] for item in blind] == [
        name for name in BROWSER_TOOL_NAMES if name != BROWSER_SCREENSHOT_TOOL_NAME
    ]
    assert GUIDANCE["computer"][1] in prompt(seeing) and GUIDANCE["browser"][1] in prompt(seeing)
    assert GUIDANCE["computer"][1] not in prompt(blind) and GUIDANCE["browser"][1] in prompt(blind)
    assert GUIDANCE["computer"][1] not in prompt([]) and GUIDANCE["browser"][1] not in prompt([])
