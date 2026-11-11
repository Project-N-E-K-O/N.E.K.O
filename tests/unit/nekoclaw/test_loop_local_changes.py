"""The two changes this repository made to the ported loop.

1. Tool progress reaches the consumer while the tool is still running.
2. Adjacent calls to parallel-safe tools run together (at most ten at a time),
   and their results still enter the transcript in call order.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Mapping

import pytest

from nekoclaw.runtime.loop import (
    AgentEvent,
    AgentTool,
    AgentToolResult,
    AssistantMessage,
    MessageEndEvent,
    ToolCall,
    ToolExecutionEndEvent,
    ToolExecutionStartEvent,
    ToolExecutionUpdateEvent,
    ToolResultMessage,
    UserMessage,
)
from nekoclaw.runtime.loop.loop import MAX_PARALLEL_TOOL_CALLS, _tool_batches, run_agent_loop
from nekoclaw.runtime.loop.types import JSONValue
from tests.unit.nekoclaw.loop_event_helpers import (
    FakeProvider,
    assistant_done,
    assistant_start,
    tool_call_end,
)


def _tool(name: str, execute_fn, *, parallel: bool = False) -> AgentTool:  # noqa: ANN001
    return AgentTool(
        name=name,
        label=name,
        description=f"Run {name}.",
        parameters={"type": "object"},
        execute_fn=execute_fn,
        execution_mode="parallel" if parallel else "sequential",
    )


def _provider(*call_batches: list[ToolCall]) -> FakeProvider:
    """One assistant response per batch of calls, then a plain final answer."""
    streams = []
    for calls in call_batches:
        message = AssistantMessage(content=list(calls))
        streams.append(
            [assistant_start(), *(tool_call_end(call) for call in calls), assistant_done(message)]
        )
    streams.append([assistant_start(), assistant_done(AssistantMessage(content="Done"))])
    return FakeProvider(streams)


def _run(provider: FakeProvider, tools: list[AgentTool], messages: list | None = None) -> AsyncIterator[AgentEvent]:
    return run_agent_loop(
        provider=provider,
        model="fake",
        system="",
        messages=messages if messages is not None else [],
        tools=tools,
        prompts=[UserMessage(content="go")],
    )


def test_execution_mode_defaults_to_sequential() -> None:
    async def execute(tool_call_id, arguments, signal=None, on_update=None):  # noqa: ANN001, ANN202
        return AgentToolResult(content="x")

    tool = AgentTool(
        name="t", label="t", description="", parameters={"type": "object"}, execute_fn=execute
    )
    assert tool.execution_mode == "sequential"


async def test_progress_is_delivered_while_the_tool_is_still_running() -> None:
    consumer_saw_progress = asyncio.Event()
    finished: list[str] = []

    async def slow(
        tool_call_id: str,
        arguments: Mapping[str, JSONValue],
        signal=None,  # noqa: ANN001
        on_update=None,  # noqa: ANN001
    ) -> AgentToolResult:
        del tool_call_id, arguments, signal
        on_update(AgentToolResult(content="step 1"))
        # The tool cannot finish until the consumer has received that update.
        # Upstream only emitted updates after the tool returned, which would
        # leave this waiting forever.
        await asyncio.wait_for(consumer_saw_progress.wait(), 10)
        on_update(AgentToolResult(content="step 2"))
        finished.append("tool")
        return AgentToolResult(content="result")

    call = ToolCall(id="c1", name="slow", arguments={})
    order: list[str] = []
    async for event in _run(_provider([call]), [_tool("slow", slow)]):
        if isinstance(event, ToolExecutionUpdateEvent):
            order.append(f"update:{event.partial_result.text}")
            if event.partial_result.text == "step 1":
                assert finished == []
                consumer_saw_progress.set()
        elif isinstance(event, ToolExecutionEndEvent):
            order.append(f"end:{event.result.text}")

    assert order == ["update:step 1", "update:step 2", "end:result"]


async def test_progress_reported_from_a_worker_thread_is_delivered() -> None:
    async def threaded(tool_call_id, arguments, signal=None, on_update=None):  # noqa: ANN001, ANN202
        await asyncio.to_thread(on_update, AgentToolResult(content="from thread"))
        return AgentToolResult(content="ok")

    call = ToolCall(id="c1", name="threaded", arguments={})
    events = [event async for event in _run(_provider([call]), [_tool("threaded", threaded)])]
    updates = [e.partial_result.text for e in events if isinstance(e, ToolExecutionUpdateEvent)]
    assert updates == ["from thread"]


async def test_updates_after_the_tool_returned_are_dropped() -> None:
    late: list = []

    async def leaky(tool_call_id, arguments, signal=None, on_update=None):  # noqa: ANN001, ANN202
        late.append(on_update)
        return AgentToolResult(content="ok")

    call = ToolCall(id="c1", name="leaky", arguments={})
    events = []
    async for event in _run(_provider([call]), [_tool("leaky", leaky)]):
        events.append(event)
        if isinstance(event, ToolExecutionEndEvent):
            late[0](AgentToolResult(content="too late"))
    assert not [e for e in events if isinstance(e, ToolExecutionUpdateEvent)]


async def test_closing_the_stream_mid_tool_cancels_the_tool() -> None:
    started = asyncio.Event()
    cancelled = asyncio.Event()

    async def hang(tool_call_id, arguments, signal=None, on_update=None):  # noqa: ANN001, ANN202
        on_update(AgentToolResult(content="working"))
        started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            cancelled.set()
            raise
        return AgentToolResult(content="never")

    call = ToolCall(id="c1", name="hang", arguments={})
    stream = _run(_provider([call]), [_tool("hang", hang)])
    async for event in stream:
        if isinstance(event, ToolExecutionUpdateEvent):
            break
    await stream.aclose()
    assert started.is_set()
    await asyncio.wait_for(cancelled.wait(), 5)


async def test_parallel_tools_run_together_and_results_keep_call_order() -> None:
    both_started = asyncio.Barrier(2)
    release_first = asyncio.Event()
    log: list[str] = []

    async def read(tool_call_id, arguments, signal=None, on_update=None):  # noqa: ANN001, ANN202
        log.append(f"start:{tool_call_id}")
        # Only passes if both calls are in flight at the same time.
        await asyncio.wait_for(both_started.wait(), 10)
        if tool_call_id == "first":
            # The first call finishes last.
            await asyncio.wait_for(release_first.wait(), 10)
        else:
            release_first.set()
        log.append(f"end:{tool_call_id}")
        return AgentToolResult(content=f"result of {tool_call_id}")

    calls = [
        ToolCall(id="first", name="read", arguments={"n": 1}),
        ToolCall(id="second", name="read", arguments={"n": 2}),
    ]
    messages: list = []
    events = [
        event
        async for event in _run(_provider(calls), [_tool("read", read, parallel=True)], messages)
    ]

    assert log == ["start:first", "start:second", "end:second", "end:first"]
    ends = [e.tool_call_id for e in events if isinstance(e, ToolExecutionEndEvent)]
    assert ends == ["second", "first"]
    # The transcript, and the events that carry it, follow the call order.
    emitted = [
        e.message.tool_call_id
        for e in events
        if isinstance(e, MessageEndEvent) and isinstance(e.message, ToolResultMessage)
    ]
    stored = [m.tool_call_id for m in messages if isinstance(m, ToolResultMessage)]
    assert emitted == stored == ["first", "second"]
    assert [m.text for m in messages if isinstance(m, ToolResultMessage)] == [
        "result of first",
        "result of second",
    ]


async def test_a_sequential_tool_separates_parallel_batches() -> None:
    active = 0
    peak_during_write = 0
    log: list[str] = []

    async def read(tool_call_id, arguments, signal=None, on_update=None):  # noqa: ANN001, ANN202
        nonlocal active
        active += 1
        log.append(f"start:{tool_call_id}")
        await asyncio.sleep(0.01)
        log.append(f"end:{tool_call_id}")
        active -= 1
        return AgentToolResult(content=tool_call_id)

    async def write(tool_call_id, arguments, signal=None, on_update=None):  # noqa: ANN001, ANN202
        nonlocal peak_during_write
        log.append(f"start:{tool_call_id}")
        await asyncio.sleep(0.01)
        peak_during_write = max(peak_during_write, active)
        log.append(f"end:{tool_call_id}")
        return AgentToolResult(content=tool_call_id)

    tools = [_tool("read", read, parallel=True), _tool("write", write)]
    calls = [
        ToolCall(id="r1", name="read", arguments={}),
        ToolCall(id="r2", name="read", arguments={}),
        ToolCall(id="w1", name="write", arguments={}),
        ToolCall(id="r3", name="read", arguments={}),
        ToolCall(id="missing", name="unknown", arguments={}),
        ToolCall(id="r4", name="read", arguments={}),
    ]
    by_name = {tool.name: tool for tool in tools}
    assert [[call.id for call in batch] for batch in _tool_batches(calls, by_name)] == [
        ["r1", "r2"],
        ["w1"],
        ["r3"],
        ["missing"],
        ["r4"],
    ]

    messages: list = []
    _ = [event async for event in _run(_provider(calls), tools, messages)]

    # Nothing overlapped the write, and it ran strictly between the reads.
    assert peak_during_write == 0
    assert log.index("end:r1") < log.index("start:w1")
    assert log.index("end:r2") < log.index("start:w1")
    assert log.index("end:w1") < log.index("start:r3")
    results = [m for m in messages if isinstance(m, ToolResultMessage)]
    assert [m.tool_call_id for m in results] == ["r1", "r2", "w1", "r3", "missing", "r4"]
    assert results[4].is_error and results[4].text == "Tool unknown not found"


async def test_at_most_ten_parallel_tools_run_at_once() -> None:
    active = 0
    peak = 0

    async def read(tool_call_id, arguments, signal=None, on_update=None):  # noqa: ANN001, ANN202
        nonlocal active, peak
        active += 1
        peak = max(peak, active)
        await asyncio.sleep(0.02)
        active -= 1
        return AgentToolResult(content=tool_call_id)

    calls = [ToolCall(id=f"c{index}", name="read", arguments={"i": index}) for index in range(25)]
    messages: list = []
    events = [
        event
        async for event in _run(_provider(calls), [_tool("read", read, parallel=True)], messages)
    ]

    assert MAX_PARALLEL_TOOL_CALLS == 10
    assert peak == 10
    stored = [m.tool_call_id for m in messages if isinstance(m, ToolResultMessage)]
    assert stored == [call.id for call in calls]
    assert len([e for e in events if isinstance(e, ToolExecutionStartEvent)]) == 25


async def test_progress_of_parallel_tools_is_interleaved_live() -> None:
    a_progress_seen = asyncio.Event()
    b_progress_seen = asyncio.Event()

    async def read(tool_call_id, arguments, signal=None, on_update=None):  # noqa: ANN001, ANN202
        on_update(AgentToolResult(content=f"{tool_call_id} working"))
        # Each call finishes only after the consumer saw the other one's progress.
        other = b_progress_seen if tool_call_id == "a" else a_progress_seen
        await asyncio.wait_for(other.wait(), 10)
        return AgentToolResult(content=tool_call_id)

    calls = [ToolCall(id="a", name="read", arguments={}), ToolCall(id="b", name="read", arguments={})]
    seen: list[str] = []
    async for event in _run(_provider(calls), [_tool("read", read, parallel=True)]):
        if isinstance(event, ToolExecutionUpdateEvent):
            seen.append(event.tool_call_id)
            (a_progress_seen if event.tool_call_id == "a" else b_progress_seen).set()
    assert sorted(seen) == ["a", "b"]


async def test_a_failing_hook_in_a_parallel_batch_propagates_and_stops_the_others() -> None:
    cancelled = asyncio.Event()

    async def read(tool_call_id, arguments, signal=None, on_update=None):  # noqa: ANN001, ANN202
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            cancelled.set()
            raise
        return AgentToolResult(content="never")

    async def before(call: ToolCall) -> tuple[bool, str | None]:
        if call.id == "bad":
            await asyncio.sleep(0.01)
            raise RuntimeError("hook exploded")
        return False, None

    calls = [ToolCall(id="ok", name="read", arguments={}), ToolCall(id="bad", name="read", arguments={})]
    stream = run_agent_loop(
        provider=_provider(calls),
        model="fake",
        system="",
        messages=[],
        tools=[_tool("read", read, parallel=True)],
        prompts=[UserMessage(content="go")],
        before_tool_call=before,
    )
    with pytest.raises(RuntimeError, match="hook exploded"):
        _ = [event async for event in stream]
    await asyncio.wait_for(cancelled.wait(), 5)
