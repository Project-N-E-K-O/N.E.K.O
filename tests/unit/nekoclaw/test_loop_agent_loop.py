# Ported from huggingface/tau (https://github.com/huggingface/tau),
# commit 0d256d5729b76ad5dae2fef453b04da9b8241208, file tests/test_agent_loop.py.
# Copyright (c) 2026 Alejandro AO. MIT License: see nekoclaw/runtime/loop/LICENSE.
# Adapted to this repository: pytest-asyncio auto mode instead of anyio marks,
# imports point at nekoclaw.runtime.loop, and the fake provider lives in
# tests/unit/nekoclaw/loop_event_helpers.py.

import asyncio
from collections.abc import AsyncIterator, Mapping
from itertools import chain, repeat

import pytest

import nekoclaw.runtime.loop.loop as loop_module
from tests.unit.nekoclaw.loop_event_helpers import (
    assistant_done,
    assistant_error,
    assistant_start,
    text_delta,
    thinking_delta,
    tool_call_end,
)
from nekoclaw.runtime.loop import (
    AgentEvent,
    AgentMessage,
    AgentTool,
    AgentToolResult,
    AssistantMessage,
    MessageEndEvent,
    MessageUpdateEvent,
    SimpleCancellationToken,
    TextContent,
    ToolCall,
    ToolExecutionEndEvent,
    ToolExecutionUpdateEvent,
    ToolResultMessage,
    UserMessage,
)
from nekoclaw.runtime.loop.loop import run_agent_loop
from nekoclaw.runtime.loop.provider_events import ThinkingDeltaEvent
from nekoclaw.runtime.loop.types import JSONValue
from nekoclaw.runtime.loop.provider import CancellationToken
from tests.unit.nekoclaw.loop_event_helpers import FakeProvider


async def _collect(stream: AsyncIterator[AgentEvent]) -> list[AgentEvent]:
    return [event async for event in stream]


def _tool(
    name: str,
    execute_fn,
) -> AgentTool:  # noqa: ANN001
    return AgentTool(
        name=name,
        label=name.title(),
        description=f"Run {name}.",
        parameters={"type": "object"},
        execute_fn=execute_fn,
    )


async def test_agent_loop_streams_canonical_nested_events() -> None:
    messages: list[AgentMessage] = [UserMessage(content="Say hello")]
    assistant = AssistantMessage(content="Hello", model="fake")
    provider = FakeProvider(
        [[assistant_start(), text_delta("Hel"), text_delta("lo"), assistant_done(assistant)]]
    )

    events = await _collect(
        run_agent_loop(
            provider=provider,
            model="fake",
            system="You are Tau.",
            messages=messages,
            tools=[],
        )
    )

    assert [event.type for event in events] == [
        "agent_start",
        "turn_start",
        "message_start",
        "message_update",
        "message_update",
        "message_end",
        "turn_end",
        "agent_end",
    ]
    updates = [event for event in events if isinstance(event, MessageUpdateEvent)]
    assert [event.assistant_message_event.delta for event in updates] == ["Hel", "lo"]  # type: ignore[union-attr]
    assert messages == [messages[0], assistant]


async def test_agent_loop_records_only_monotonic_provider_wait_time(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ticks = chain(
        [
            0,
            100_000_000,
            1_000_000_000,
            2_150_000_000,
            10_000_000_000,
            13_750_000_000,
        ],
        repeat(13_750_000_000),
    )
    monkeypatch.setattr(loop_module, "monotonic_ns", lambda: next(ticks))
    messages: list[AgentMessage] = [UserMessage(content="Say hello")]
    assistant = AssistantMessage(content="Hello", model="fake")
    assistant.usage.output = 100
    provider = FakeProvider([[assistant_start(), text_delta("Hello"), assistant_done(assistant)]])

    await _collect(
        run_agent_loop(
            provider=provider,
            model="fake",
            system="You are Tau.",
            messages=messages,
            tools=[],
        )
    )

    assert assistant.timing is not None
    assert assistant.timing.time_to_first_output_ms == 1250
    assert assistant.timing.total_duration_ms == 5000


async def test_agent_loop_does_not_invent_ttft_without_an_output_event(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ticks = chain(
        [0, 100_000_000, 1_000_000_000, 2_000_000_000],
        repeat(2_000_000_000),
    )
    monkeypatch.setattr(loop_module, "monotonic_ns", lambda: next(ticks))
    assistant = AssistantMessage(content="Non-streamed", model="fake")
    provider = FakeProvider([[assistant_start(), assistant_done(assistant)]])

    await _collect(
        run_agent_loop(
            provider=provider,
            model="fake",
            system="You are Tau.",
            messages=[UserMessage(content="Say hello")],
            tools=[],
        )
    )

    assert assistant.timing is not None
    assert assistant.timing.time_to_first_output_ms is None
    assert assistant.timing.total_duration_ms == 1100


async def test_agent_loop_nests_thinking_events_without_losing_final_message() -> None:
    messages: list[AgentMessage] = [UserMessage(content="Think briefly")]
    assistant = AssistantMessage(content="Done", model="fake")
    provider = FakeProvider(
        [
            [
                assistant_start(),
                thinking_delta("hidden "),
                thinking_delta("reasoning"),
                text_delta("Done"),
                assistant_done(assistant),
            ]
        ]
    )

    events = await _collect(
        run_agent_loop(
            provider=provider,
            model="fake",
            system="You are Tau.",
            messages=messages,
            tools=[],
        )
    )

    nested = [
        event.assistant_message_event
        for event in events
        if isinstance(event, MessageUpdateEvent)
        and isinstance(event.assistant_message_event, ThinkingDeltaEvent)
    ]
    assert [event.delta for event in nested] == ["hidden ", "reasoning"]
    assert messages[-1] == assistant
    # The final provider message is the canonical persistence boundary.
    assert isinstance(messages[-1], AssistantMessage)


async def test_agent_loop_executes_tool_and_emits_tool_result_message_lifecycle() -> None:
    async def execute(
        tool_call_id: str,
        arguments: Mapping[str, JSONValue],
        signal: CancellationToken | None = None,
        on_update=None,  # noqa: ANN001
    ) -> AgentToolResult:
        del tool_call_id, signal, on_update
        return AgentToolResult(
            content=[TextContent(text=f"contents of {arguments['path']}")],
            details={"path": arguments["path"]},
        )

    tool_call = ToolCall(id="call-1", name="read", arguments={"path": "README.md"})
    first = AssistantMessage(content=[TextContent(text="Reading."), tool_call], model="fake")
    final = AssistantMessage(content="Done.", model="fake")
    provider = FakeProvider(
        [
            [assistant_start(), tool_call_end(tool_call), assistant_done(first, "toolUse")],
            [assistant_start(), text_delta("Done."), assistant_done(final)],
        ]
    )
    messages: list[AgentMessage] = [UserMessage(content="Read README.md")]

    events = await _collect(
        run_agent_loop(
            provider=provider,
            model="fake",
            system="You are Tau.",
            messages=messages,
            tools=[_tool("read", execute)],
        )
    )

    result = next(message for message in messages if isinstance(message, ToolResultMessage))
    assert result.role == "toolResult"
    assert result.tool_name == "read"
    assert result.text == "contents of README.md"
    assert result.details == {"path": "README.md"}
    result_lifecycle = [
        event.type
        for event in events
        if isinstance(event, (MessageEndEvent,)) and event.message is result
    ]
    assert result_lifecycle == ["message_end"]
    assert [event.type for event in events].count("message_start") == 3
    assert provider.calls[1][2] == messages[:3]


async def test_hook_that_stops_the_run_issues_no_further_request() -> None:
    async def execute(
        tool_call_id: str,
        arguments: Mapping[str, JSONValue],
        signal: CancellationToken | None = None,
        on_update=None,  # noqa: ANN001
    ) -> AgentToolResult:
        raise AssertionError("a blocked call must not run")

    signal = SimpleCancellationToken()

    async def stop_run(call: ToolCall) -> tuple[bool, str | None]:
        del call
        signal.cancel()
        return True, "Stopped by hook."

    tool_call = ToolCall(id="call-1", name="read", arguments={"path": "README.md"})
    first = AssistantMessage(content=[tool_call], model="fake")
    unreached = AssistantMessage(content="Should not be requested.", model="fake")
    provider = FakeProvider(
        [
            [assistant_start(), tool_call_end(tool_call), assistant_done(first, "toolUse")],
            [assistant_start(), assistant_done(unreached)],
        ]
    )
    messages: list[AgentMessage] = [UserMessage(content="Read README.md")]

    events = await _collect(
        run_agent_loop(
            provider=provider,
            model="fake",
            system="You are Tau.",
            messages=messages,
            tools=[_tool("read", execute)],
            signal=signal,
            before_tool_call=stop_run,
        )
    )

    assert len(provider.calls) == 1
    final = messages[-1]
    assert isinstance(final, AssistantMessage)
    assert final.stop_reason == "aborted"
    assert final.error_message == "Stopped by hook."
    assert events[-1].type == "agent_end"


async def test_cancel_while_a_tool_runs_issues_no_further_request() -> None:
    signal = SimpleCancellationToken()

    async def execute(
        tool_call_id: str,
        arguments: Mapping[str, JSONValue],
        signal_arg: CancellationToken | None = None,
        on_update=None,  # noqa: ANN001
    ) -> AgentToolResult:
        # Like bash when the user cancels a running command.
        signal.cancel()
        return AgentToolResult(content=[TextContent(text="Command cancelled")])

    tool_call = ToolCall(id="call-1", name="bash", arguments={"command": "sleep 60"})
    first = AssistantMessage(content=[tool_call], model="fake")
    unreached = AssistantMessage(content="Should not be requested.", model="fake")
    provider = FakeProvider(
        [
            [assistant_start(), tool_call_end(tool_call), assistant_done(first, "toolUse")],
            [assistant_start(), assistant_done(unreached)],
        ]
    )
    messages: list[AgentMessage] = [UserMessage(content="Wait a minute")]

    await _collect(
        run_agent_loop(
            provider=provider,
            model="fake",
            system="You are Tau.",
            messages=messages,
            tools=[_tool("bash", execute)],
            signal=signal,
        )
    )

    assert len(provider.calls) == 1
    final = messages[-1]
    assert isinstance(final, AssistantMessage)
    assert final.stop_reason == "aborted"
    assert final.error_message == "Operation aborted"


async def test_agent_loop_passes_call_id_signal_and_progress_to_tool() -> None:
    observed: list[tuple[str, CancellationToken | None]] = []

    async def execute(
        tool_call_id: str,
        arguments: Mapping[str, JSONValue],
        signal: CancellationToken | None = None,
        on_update=None,  # noqa: ANN001
    ) -> AgentToolResult:
        del arguments
        observed.append((tool_call_id, signal))
        assert on_update is not None
        on_update(AgentToolResult(content="working"))
        await asyncio.sleep(0)
        return AgentToolResult(content="done")

    call = ToolCall(id="call-1", name="work", arguments={})
    first = AssistantMessage(content=[call], model="fake")
    final = AssistantMessage(content="finished", model="fake")
    provider = FakeProvider(
        [
            [assistant_start(), tool_call_end(call), assistant_done(first, "toolUse")],
            [assistant_start(), assistant_done(final)],
        ]
    )
    signal = SimpleCancellationToken()

    events = await _collect(
        run_agent_loop(
            provider=provider,
            model="fake",
            system="You are Tau.",
            messages=[UserMessage(content="work")],
            tools=[_tool("work", execute)],
            signal=signal,
        )
    )

    assert observed == [("call-1", signal)]
    updates = [event for event in events if isinstance(event, ToolExecutionUpdateEvent)]
    assert [event.partial_result.text for event in updates] == ["working"]


async def test_agent_loop_records_unknown_tool_as_canonical_error_result() -> None:
    call = ToolCall(id="call-1", name="missing", arguments={})
    assistant = AssistantMessage(content=[call], model="fake")
    messages: list[AgentMessage] = [UserMessage(content="Use it")]
    provider = FakeProvider(
        [[assistant_start(), tool_call_end(call), assistant_done(assistant, "toolUse")]]
    )

    events = await _collect(
        run_agent_loop(
            provider=provider,
            model="fake",
            system="You are Tau.",
            messages=messages,
            tools=[],
            max_turns=1,
        )
    )

    end = next(event for event in events if isinstance(event, ToolExecutionEndEvent))
    assert end.is_error is True
    assert end.result.text == "Tool missing not found"
    result = next(message for message in messages if isinstance(message, ToolResultMessage))
    assert result.is_error is True
    assert result.text == "Tool missing not found"


async def test_agent_loop_converts_provider_error_to_assistant_error_message() -> None:
    messages: list[AgentMessage] = [UserMessage(content="hello")]
    provider = FakeProvider([[assistant_error("provider failed")]])

    events = await _collect(
        run_agent_loop(
            provider=provider,
            model="fake",
            system="You are Tau.",
            messages=messages,
            tools=[],
        )
    )

    assert [event.type for event in events] == [
        "agent_start",
        "turn_start",
        "message_start",
        "message_end",
        "turn_end",
        "agent_end",
    ]
    error = messages[-1]
    assert isinstance(error, AssistantMessage)
    assert error.stop_reason == "error"
    assert error.error_message == "provider failed"


async def test_agent_loop_excludes_empty_failed_assistant_from_next_provider_call() -> None:
    messages: list[AgentMessage] = []
    recovered = AssistantMessage(content="recovered", model="fake")
    provider = FakeProvider(
        [
            [assistant_error("provider failed")],
            [assistant_start(), assistant_done(recovered)],
        ]
    )

    await _collect(
        run_agent_loop(
            provider=provider,
            model="fake",
            system="You are Tau.",
            messages=messages,
            tools=[],
            prompts=[UserMessage(content="hello")],
        )
    )
    failed = messages[-1]
    assert isinstance(failed, AssistantMessage)
    assert failed.stop_reason == "error"

    await _collect(
        run_agent_loop(
            provider=provider,
            model="fake",
            system="You are Tau.",
            messages=messages,
            tools=[],
            prompts=[UserMessage(content="continue")],
        )
    )

    assert failed in messages
    replayed = provider.calls[1][2]
    assert [message.text for message in replayed] == ["hello", "continue"]
    assert failed not in replayed
    assert messages[-1] is recovered


async def test_agent_loop_repairs_malformed_tool_history_before_provider_call() -> None:
    call = ToolCall(id="call-1", name="read", arguments={"path": "README.md"})
    assistant = AssistantMessage(content=[call])
    late_user = UserMessage(content="continue")
    orphan = ToolResultMessage(tool_call_id="call-missing", tool_name="bash", content="orphan")
    recovered = AssistantMessage(content="recovered", model="fake")
    provider = FakeProvider([[assistant_start(), assistant_done(recovered)]])
    messages: list[AgentMessage] = [assistant, late_user, orphan]

    await _collect(
        run_agent_loop(
            provider=provider,
            model="fake",
            system="You are Tau.",
            messages=messages,
            tools=[],
        )
    )

    replayed = provider.calls[0][2]
    assert replayed[0] is assistant
    repair = replayed[1]
    assert isinstance(repair, ToolResultMessage)
    assert repair.tool_call_id == "call-1"
    assert repair.is_error is True
    assert replayed[2] is late_user
    assert orphan not in replayed
    assert orphan in messages


async def test_agent_loop_injects_steering_and_follow_up_messages() -> None:
    call = ToolCall(id="call-1", name="work", arguments={})

    async def execute(
        tool_call_id: str,
        arguments: Mapping[str, JSONValue],
        signal=None,  # noqa: ANN001
        on_update=None,  # noqa: ANN001
    ) -> AgentToolResult:
        del tool_call_id, arguments, signal, on_update
        return AgentToolResult(content="ok")

    first = AssistantMessage(content=[call], model="fake")
    second = AssistantMessage(content="second", model="fake")
    third = AssistantMessage(content="third", model="fake")
    provider = FakeProvider(
        [
            [assistant_start(), tool_call_end(call), assistant_done(first, "toolUse")],
            [assistant_start(), assistant_done(second)],
            [assistant_start(), assistant_done(third)],
        ]
    )
    steering = [UserMessage(content="steer")]
    follow_up = [UserMessage(content="follow up")]

    def pop(queue: list[UserMessage]) -> tuple[UserMessage, ...]:
        return (queue.pop(0),) if queue else ()

    messages: list[AgentMessage] = [UserMessage(content="start")]
    await _collect(
        run_agent_loop(
            provider=provider,
            model="fake",
            system="You are Tau.",
            messages=messages,
            tools=[_tool("work", execute)],
            get_steering_messages=lambda: pop(steering),
            get_follow_up_messages=lambda: pop(follow_up),
        )
    )

    assert [message.text for message in messages if isinstance(message, UserMessage)] == [
        "start",
        "steer",
        "follow up",
    ]
    assert len(provider.calls) == 3


async def test_agent_loop_stops_with_assistant_error_after_max_turns() -> None:
    call = ToolCall(id="call-1", name="missing", arguments={})
    assistant = AssistantMessage(content=[call], model="fake")
    provider = FakeProvider(
        [[assistant_start(), tool_call_end(call), assistant_done(assistant, "toolUse")]]
    )
    messages: list[AgentMessage] = [UserMessage(content="loop")]

    await _collect(
        run_agent_loop(
            provider=provider,
            model="fake",
            system="You are Tau.",
            messages=messages,
            tools=[],
            max_turns=1,
        )
    )

    error = messages[-1]
    assert isinstance(error, AssistantMessage)
    assert error.stop_reason == "error"
    assert error.error_message == "Agent stopped after max_turns=1"
    assert len(provider.calls) == 1
