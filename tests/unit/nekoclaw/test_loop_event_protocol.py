# Ported from huggingface/tau (https://github.com/huggingface/tau),
# commit 0d256d5729b76ad5dae2fef453b04da9b8241208, file tests/test_pi_event_protocol.py.
# Copyright (c) 2026 Alejandro AO. MIT License: see nekoclaw/runtime/loop/LICENSE.
# Adapted to this repository: pytest-asyncio auto mode instead of anyio marks,
# imports point at nekoclaw.runtime.loop, and the fake provider lives in
# tests/unit/nekoclaw/loop_event_helpers.py.

"""Focused contract tests for the canonical Pi-shaped event stream."""

import ast
import sys
from pathlib import Path

from nekoclaw.runtime.loop import (
    AgentEndEvent,
    AgentHarness,
    AgentHarnessConfig,
    AssistantMessage,
    MessageEndEvent,
    MessageUpdateEvent,
    TextContent,
    ToolCall,
    ToolExecutionEndEvent,
    ToolExecutionStartEvent,
    ToolResultMessage,
)
from nekoclaw.runtime.loop.provider_events import (
    AssistantDoneEvent,
    AssistantStartEvent,
    TextDeltaEvent,
    TextEndEvent,
    TextStartEvent,
    ToolCallEndEvent,
    ToolCallStartEvent,
)
from tests.unit.nekoclaw.loop_event_helpers import FakeProvider


_LOOP_ROOT = Path(__file__).resolve().parents[3] / "nekoclaw" / "runtime" / "loop"
_ALLOWED_IMPORT_ROOTS = {"nekoclaw", "pydantic", "typing_extensions"}


def test_loop_package_stays_self_contained() -> None:
    # Upstream asserts that tau_agent never imports its model layer tau_ai. That
    # model layer is not ported, so the equivalent contract here is that the loop
    # package imports only itself, pydantic, typing_extensions and the stdlib.
    # (Upstream's second test, the tau_ai re-export check, has no counterpart.)
    files = sorted(_LOOP_ROOT.rglob("*.py"))
    assert len(files) == 17
    for path in files:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                modules = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                assert node.level == 0, path
                modules = [node.module or ""]
            else:
                continue
            for module in modules:
                root = module.split(".")[0]
                if root == "nekoclaw":
                    assert module.startswith("nekoclaw.runtime.loop"), (path, module)
                else:
                    assert root in _ALLOWED_IMPORT_ROOTS or root in sys.stdlib_module_names, (
                        path,
                        module,
                    )


async def test_text_stream_has_nested_updates_and_terminal_messages() -> None:
    empty = AssistantMessage(model="fake")
    hello = AssistantMessage(model="fake", content=[TextContent(text="hello")])
    provider = FakeProvider(
        [
            [
                AssistantStartEvent(partial=empty),
                TextStartEvent(content_index=0, partial=empty),
                TextDeltaEvent(content_index=0, delta="hello", partial=hello),
                TextEndEvent(content_index=0, content="hello", partial=hello),
                AssistantDoneEvent(reason="stop", message=hello),
            ]
        ]
    )
    harness = AgentHarness(AgentHarnessConfig(provider=provider, model="fake", system="test"))

    events = [event async for event in harness.prompt("hi")]

    assert [event.type for event in events] == [
        "agent_start",
        "turn_start",
        "message_start",
        "message_end",
        "message_start",
        "message_update",
        "message_update",
        "message_update",
        "message_end",
        "turn_end",
        "agent_end",
    ]
    updates = [event for event in events if isinstance(event, MessageUpdateEvent)]
    assert [event.assistant_message_event.type for event in updates] == [
        "text_start",
        "text_delta",
        "text_end",
    ]
    assert isinstance(events[-1], AgentEndEvent)
    assert events[-1].messages[-1] == hello


async def test_tool_result_gets_execution_and_message_lifecycle_events() -> None:
    call = ToolCall(id="call-1", name="missing", arguments={})
    partial = AssistantMessage(model="fake", content=[call])
    provider = FakeProvider(
        [
            [
                AssistantStartEvent(partial=AssistantMessage(model="fake")),
                ToolCallStartEvent(content_index=0, partial=partial),
                ToolCallEndEvent(content_index=0, tool_call=call, partial=partial),
                AssistantDoneEvent(reason="toolUse", message=partial),
            ],
            [
                AssistantStartEvent(partial=AssistantMessage(model="fake")),
                AssistantDoneEvent(
                    reason="stop",
                    message=AssistantMessage(model="fake", content=[TextContent(text="done")]),
                ),
            ],
        ]
    )
    harness = AgentHarness(AgentHarnessConfig(provider=provider, model="fake", system="test"))

    events = [event async for event in harness.prompt("use it")]

    start = next(event for event in events if isinstance(event, ToolExecutionStartEvent))
    end = next(event for event in events if isinstance(event, ToolExecutionEndEvent))
    result_end = next(
        event
        for event in events
        if isinstance(event, MessageEndEvent) and isinstance(event.message, ToolResultMessage)
    )
    assert start.tool_call_id == end.tool_call_id == result_end.message.tool_call_id
    assert end.is_error is True
