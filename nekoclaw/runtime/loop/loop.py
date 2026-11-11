# Ported from huggingface/tau (https://github.com/huggingface/tau),
# commit 0d256d5729b76ad5dae2fef453b04da9b8241208, file src/tau_agent/loop.py.
# Copyright (c) 2026 Alejandro AO. MIT License: see LICENSE in the loop package root.
# Local changes are listed in nekoclaw/runtime/loop/__init__.py.

"""Pure Pi-compatible provider/tool agent loop."""

from __future__ import annotations

import asyncio
import threading
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping, Sequence
from contextlib import aclosing
from dataclasses import dataclass
from inspect import isawaitable
from time import monotonic_ns
from typing import Final

from nekoclaw.runtime.loop.events import (
    AgentEndEvent,
    AgentEvent,
    AgentStartEvent,
    MessageEndEvent,
    MessageStartEvent,
    MessageUpdateEvent,
    ToolExecutionEndEvent,
    ToolExecutionStartEvent,
    ToolExecutionUpdateEvent,
    TurnEndEvent,
    TurnStartEvent,
)
from nekoclaw.runtime.loop.messages import (
    AgentMessage,
    AssistantMessage,
    ResponseTiming,
    TextContent,
    ToolCall,
    ToolResultMessage,
)
from nekoclaw.runtime.loop.provider import CancellationToken, ModelProvider
from nekoclaw.runtime.loop.provider_events import (
    AssistantDoneEvent,
    AssistantErrorEvent,
    AssistantMessageEvent,
    AssistantStartEvent,
    TextDeltaEvent,
    ThinkingDeltaEvent,
    ToolCallDeltaEvent,
    ToolCallEndEvent,
    ToolCallStartEvent,
)
from nekoclaw.runtime.loop.tool_history import repair_tool_history
from nekoclaw.runtime.loop.tools import AgentTool, AgentToolResult
from nekoclaw.runtime.loop.turns import AgentTurnContext, AgentTurnDecision, FinishTurn

BeforeToolCall = Callable[[ToolCall], Awaitable[tuple[bool, str | None]]]
AfterToolCall = Callable[
    [ToolCall, AgentToolResult, bool],
    Awaitable[tuple[AgentToolResult, bool]],
]

# Local change: adjacent calls to tools declared ``execution_mode="parallel"``
# run together, at most this many at a time.
MAX_PARALLEL_TOOL_CALLS: Final = 10


async def run_agent_loop(
    *,
    provider: ModelProvider,
    model: str,
    system: str,
    messages: list[AgentMessage],
    tools: list[AgentTool],
    prompts: Sequence[AgentMessage] = (),
    prelude_messages: Sequence[AgentMessage] = (),
    max_turns: int | None = None,
    signal: CancellationToken | None = None,
    session_id: str | None = None,
    get_steering_messages: Callable[[], Sequence[AgentMessage]] | None = None,
    get_follow_up_messages: Callable[[], Sequence[AgentMessage]] | None = None,
    before_tool_call: BeforeToolCall | None = None,
    after_tool_call: AfterToolCall | None = None,
    finish_turn: FinishTurn | None = None,
) -> AsyncIterator[AgentEvent]:
    """Run the provider/tool loop and emit Pi-compatible agent events."""
    new_messages = list(prompts)
    if prompts:
        messages.extend(prompts)

    yield AgentStartEvent()
    yield TurnStartEvent()
    for message in prelude_messages:
        yield MessageStartEvent(message=message)
        yield MessageEndEvent(message=message)
    for prompt in prompts:
        yield MessageStartEvent(message=prompt)
        yield MessageEndEvent(message=prompt)

    if max_turns is not None and max_turns < 1:
        error = _error_message(model, "max_turns must be at least 1")
        messages.append(error)
        new_messages.append(error)
        yield MessageStartEvent(message=error)
        yield MessageEndEvent(message=error)
        await _finish_turn(finish_turn, error, (), messages, new_messages, signal)
        yield TurnEndEvent(message=error)
        yield AgentEndEvent(messages=new_messages)
        return

    tool_by_name = {tool.name: tool for tool in tools}
    turn = 1
    first_turn = True
    pending = tuple(get_steering_messages() if get_steering_messages else ())
    # Error text of the tool call that cancelled the run, e.g. a hook's reason.
    stop_note: str | None = None
    explicit_continuation = False

    while True:
        has_more_tools = True
        while has_more_tools or pending:
            if not first_turn:
                yield TurnStartEvent()
            first_turn = False

            for message in pending:
                messages.append(message)
                new_messages.append(message)
                yield MessageStartEvent(message=message)
                yield MessageEndEvent(message=message)
            pending = ()

            if max_turns is not None and turn > max_turns:
                error = _error_message(model, f"Agent stopped after max_turns={max_turns}")
                messages.append(error)
                new_messages.append(error)
                yield MessageStartEvent(message=error)
                yield MessageEndEvent(message=error)
                await _finish_turn(finish_turn, error, (), messages, new_messages, signal)
                yield TurnEndEvent(message=error)
                yield AgentEndEvent(messages=new_messages)
                return

            if signal is not None and signal.is_cancelled():
                # A request started after a cancel would fail as a provider error.
                aborted = _aborted_message(model, stop_note)
                messages.append(aborted)
                new_messages.append(aborted)
                yield MessageStartEvent(message=aborted)
                yield MessageEndEvent(message=aborted)
                await _finish_turn(finish_turn, aborted, (), messages, new_messages, signal)
                yield TurnEndEvent(message=aborted)
                yield AgentEndEvent(messages=new_messages)
                return

            # Python async generators cannot pass a yielding callback through a
            # normal await cleanly, so consume the assistant sub-generator and
            # retain its final message through the terminal event.
            assistant = None
            async for event in _assistant_events(
                provider=provider,
                model=model,
                system=system,
                messages=_provider_context(messages),
                tools=tools,
                signal=signal,
                session_id=session_id,
            ):
                yield event
                if isinstance(event, MessageEndEvent) and isinstance(
                    event.message, AssistantMessage
                ):
                    assistant = event.message

            if assistant is None:  # defensive: _assistant_events always terminates
                assistant = _error_message(model, "Provider produced no assistant message")
                yield MessageStartEvent(message=assistant)
                yield MessageEndEvent(message=assistant)

            messages.append(assistant)
            new_messages.append(assistant)
            if assistant.stop_reason in {"error", "aborted"}:
                await _finish_turn(finish_turn, assistant, (), messages, new_messages, signal)
                yield TurnEndEvent(message=assistant)
                yield AgentEndEvent(messages=new_messages)
                return

            tool_results: list[ToolResultMessage] = []
            calls = list(assistant.tool_calls)
            all_tools_terminate = True
            # Local change: calls are grouped into batches. A batch is either one
            # call or a run of adjacent parallel-safe calls executed together;
            # results still reach the transcript in call order.
            for batch in _tool_batches(calls, tool_by_name):
                cancelled_before = signal is not None and signal.is_cancelled()
                async with aclosing(
                    _execute_tool_batch(
                        batch,
                        tool_by_name,
                        signal,
                        before_tool_call,
                        after_tool_call,
                    )
                ) as batch_events:
                    async for event in batch_events:
                        if isinstance(event, ToolExecutionEndEvent):
                            all_tools_terminate = (
                                all_tools_terminate and event.result.terminate is True
                            )
                        yield event
                        if isinstance(event, MessageEndEvent) and isinstance(
                            event.message, ToolResultMessage
                        ):
                            tool_results.append(event.message)
                            messages.append(event.message)
                            new_messages.append(event.message)
                            if (
                                stop_note is None
                                and not cancelled_before
                                and signal is not None
                                and signal.is_cancelled()
                                and event.message.is_error
                            ):
                                stop_note = event.message.text or None

            # Termination is a batch scheduling hint, not an interruption of
            # sibling tools or cancellation. Only final, post-hook results count.
            has_more_tools = bool(calls) and (
                not all_tools_terminate or (signal is not None and signal.is_cancelled())
            )
            decision = await _finish_turn(
                finish_turn, assistant, tool_results, messages, new_messages, signal
            )
            yield TurnEndEvent(message=assistant, tool_results=tool_results)
            if decision is not None and decision.action == "end":
                yield AgentEndEvent(messages=new_messages)
                return

            turn += 1
            explicit_continuation = decision is not None and decision.action == "continue"
            pending = tuple(get_steering_messages() if get_steering_messages else ())
            if has_more_tools or pending:
                explicit_continuation = False

        follow_ups = tuple(get_follow_up_messages() if get_follow_up_messages else ())
        if follow_ups:
            pending = follow_ups
            explicit_continuation = False
            continue
        if explicit_continuation:
            explicit_continuation = False
            continue
        break

    yield AgentEndEvent(messages=new_messages)


async def _finish_turn(
    callback: FinishTurn | None,
    message: AssistantMessage,
    tool_results: Sequence[ToolResultMessage],
    messages: Sequence[AgentMessage],
    new_messages: Sequence[AgentMessage],
    signal: CancellationToken | None,
) -> AgentTurnDecision | None:
    if callback is None:
        return None
    decision = callback(
        AgentTurnContext(
            message=message,
            tool_results=tuple(tool_results),
            messages=tuple(messages),
            new_messages=tuple(new_messages),
        ),
        signal,
    )
    return await decision if isawaitable(decision) else decision


def _provider_context(messages: list[AgentMessage]) -> list[AgentMessage]:
    """Return replayable messages while retaining failures in durable history.

    Providers cannot consistently accept an assistant turn with no content. Tau
    persists terminal failures for diagnostics, but an empty failed or aborted
    turn is not model context and must not poison the next request.
    """
    replayable = tuple(
        message
        for message in messages
        if not (
            isinstance(message, AssistantMessage)
            and message.stop_reason in {"error", "aborted"}
            and not message.content
        )
    )
    return list(repair_tool_history(replayable).messages)


async def _assistant_events(
    *,
    provider: ModelProvider,
    model: str,
    system: str,
    messages: list[AgentMessage],
    tools: list[AgentTool],
    signal: CancellationToken | None,
    session_id: str | None,
) -> AsyncIterator[AgentEvent]:
    source: AsyncIterator[AssistantMessageEvent] = provider.stream_response(
        model=model,
        system=system,
        messages=messages,
        tools=tools,
        signal=signal,
        session_id=session_id,
    )
    started = False
    provider_elapsed_ns = 0
    first_output_elapsed_ns: int | None = None
    source_iterator = source.__aiter__()
    while True:
        wait_started_ns = monotonic_ns()
        try:
            event = await anext(source_iterator)
        except StopAsyncIteration:
            break
        provider_elapsed_ns += max(0, monotonic_ns() - wait_started_ns)
        if first_output_elapsed_ns is None and isinstance(
            event,
            (
                TextDeltaEvent,
                ThinkingDeltaEvent,
                ToolCallStartEvent,
                ToolCallDeltaEvent,
                ToolCallEndEvent,
            ),
        ):
            first_output_elapsed_ns = provider_elapsed_ns
        if isinstance(event, AssistantStartEvent):
            started = True
            yield MessageStartEvent(message=event.partial)
        elif isinstance(event, AssistantDoneEvent):
            event.message.timing = _response_timing(
                first_output_elapsed_ns,
                provider_elapsed_ns,
            )
            if not started:
                yield MessageStartEvent(message=event.message)
            yield MessageEndEvent(message=event.message)
        elif isinstance(event, AssistantErrorEvent):
            event.error.timing = _response_timing(
                first_output_elapsed_ns,
                provider_elapsed_ns,
            )
            if not started:
                yield MessageStartEvent(message=event.error)
            yield MessageEndEvent(message=event.error)
        else:
            yield MessageUpdateEvent(
                message=event.partial,
                assistant_message_event=event,
            )


def _response_timing(
    first_output_elapsed_ns: int | None,
    total_elapsed_ns: int,
) -> ResponseTiming:
    """Build persistable durations from time spent awaiting provider events."""
    return ResponseTiming(
        time_to_first_output_ms=(
            first_output_elapsed_ns // 1_000_000 if first_output_elapsed_ns is not None else None
        ),
        total_duration_ms=total_elapsed_ns // 1_000_000,
    )


def _tool_batches(
    calls: Sequence[ToolCall],
    tools: Mapping[str, AgentTool],
) -> list[list[ToolCall]]:
    """Split one assistant message's calls into execution batches (local change).

    Adjacent calls whose tools are declared ``execution_mode="parallel"`` share
    a batch. Every other call, including one to an unknown tool, is a batch of
    its own, so a call never overtakes a sequential call that precedes it.
    """
    batches: list[list[ToolCall]] = []
    parallel_run = False
    for call in calls:
        tool = tools.get(call.name)
        parallel = tool is not None and tool.execution_mode == "parallel"
        if parallel and parallel_run:
            batches[-1].append(call)
        else:
            batches.append([call])
        parallel_run = parallel
    return batches


async def _execute_tool_batch(
    batch: Sequence[ToolCall],
    tools: Mapping[str, AgentTool],
    signal: CancellationToken | None,
    before_tool_call: BeforeToolCall | None,
    after_tool_call: AfterToolCall | None,
) -> AsyncIterator[AgentEvent]:
    """Run one batch, emitting each call's transcript messages in call order (local change).

    Execution events (start, update, end) are forwarded as they happen. The
    result messages are held back until every earlier call of the batch has
    delivered its own, so the transcript order matches the call order.
    """
    if len(batch) == 1:
        async with aclosing(
            _execute_tool_call(batch[0], tools, signal, before_tool_call, after_tool_call)
        ) as events:
            async for event in events:
                yield event
        return

    merged: asyncio.Queue[tuple[int, AgentEvent | None, BaseException | None]] = asyncio.Queue()
    limiter = asyncio.Semaphore(MAX_PARALLEL_TOOL_CALLS)

    async def pump(index: int, call: ToolCall) -> None:
        try:
            async with limiter:
                async with aclosing(
                    _execute_tool_call(call, tools, signal, before_tool_call, after_tool_call)
                ) as events:
                    async for event in events:
                        merged.put_nowait((index, event, None))
            merged.put_nowait((index, None, None))
        except BaseException as exc:  # noqa: BLE001 - handed to the consumer, which re-raises it
            merged.put_nowait((index, None, exc))

    pumps = [asyncio.ensure_future(pump(index, call)) for index, call in enumerate(batch)]
    held: dict[int, list[AgentEvent]] = {}
    finished: set[int] = set()
    next_index = 0
    try:
        while next_index < len(batch):
            index, event, error = await merged.get()
            if error is not None:
                raise error
            if event is None:
                finished.add(index)
            elif isinstance(event, (MessageStartEvent, MessageEndEvent)):
                held.setdefault(index, []).append(event)
                continue
            else:
                yield event
                continue
            while next_index in finished:
                for held_event in held.pop(next_index, ()):
                    yield held_event
                next_index += 1
    finally:
        for task in pumps:
            task.cancel()
        await asyncio.gather(*pumps, return_exceptions=True)


async def _execute_tool_call(
    call: ToolCall,
    tools: Mapping[str, AgentTool],
    signal: CancellationToken | None,
    before_tool_call: BeforeToolCall | None,
    after_tool_call: AfterToolCall | None,
) -> AsyncIterator[AgentEvent]:
    yield ToolExecutionStartEvent(
        tool_call_id=call.id,
        tool_name=call.name,
        args=call.arguments,
    )

    blocked = False
    block_reason: str | None = None
    if before_tool_call is not None:
        blocked, block_reason = await before_tool_call(call)

    if blocked:
        result = _error_result(block_reason or "Tool execution was blocked")
        is_error = True
    elif signal is not None and signal.is_cancelled():
        result = _error_result("Operation aborted")
        is_error = True
    else:
        tool = tools.get(call.name)
        if tool is None:
            result = _error_result(f"Tool {call.name} not found")
            is_error = True
        else:
            # Local change: progress is forwarded while the tool is still running
            # instead of being collected and emitted after it returns.
            result = _error_result("Tool produced no result")
            is_error = True
            async with aclosing(_run_tool(tool, call, signal)) as progress:
                async for item in progress:
                    if isinstance(item, _ToolOutcome):
                        result, is_error = item.result, item.is_error
                    else:
                        yield ToolExecutionUpdateEvent(
                            tool_call_id=call.id,
                            tool_name=call.name,
                            args=call.arguments,
                            partial_result=item,
                        )

    if after_tool_call is not None:
        result, is_error = await after_tool_call(call, result, is_error)

    yield ToolExecutionEndEvent(
        tool_call_id=call.id,
        tool_name=call.name,
        result=result,
        is_error=is_error,
    )
    message = ToolResultMessage(
        tool_call_id=call.id,
        tool_name=call.name,
        content=result.content,
        details=result.details,
        added_tool_names=result.added_tool_names,
        is_error=is_error,
    )
    yield MessageStartEvent(message=message)
    yield MessageEndEvent(message=message)


@dataclass(frozen=True, slots=True)
class _ToolOutcome:
    """Final result of one tool run; the last item ``_run_tool`` yields."""

    result: AgentToolResult
    is_error: bool


_TOOL_FINISHED: Final = object()


async def _run_tool(
    tool: AgentTool,
    call: ToolCall,
    signal: CancellationToken | None,
) -> AsyncIterator[AgentToolResult | _ToolOutcome]:
    """Run one tool, yielding progress as it is reported and then the outcome.

    Local change. Upstream awaited the tool and returned the collected updates
    afterwards, so a long command showed no output until it ended. The tool now
    runs in its own task and its ``on_update`` calls are yielded as they arrive.
    Updates reported after the tool returned are still dropped, as upstream does.
    """
    updates: asyncio.Queue[object] = asyncio.Queue()
    loop = asyncio.get_running_loop()
    owner_thread = threading.get_ident()
    accepting = True

    def on_update(partial: AgentToolResult) -> None:
        if not accepting:
            return
        snapshot = partial.model_copy(deep=True)
        if threading.get_ident() == owner_thread:
            updates.put_nowait(snapshot)
        else:
            # A tool may report from a worker thread; the queue is loop-bound.
            loop.call_soon_threadsafe(updates.put_nowait, snapshot)

    async def run() -> _ToolOutcome:
        try:
            return _ToolOutcome(
                await tool.execute(call.id, call.arguments, signal, on_update), False
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - tools are an isolation boundary
            return _ToolOutcome(_error_result(str(exc)), True)

    task = asyncio.ensure_future(run())
    task.add_done_callback(lambda _done: updates.put_nowait(_TOOL_FINISHED))
    try:
        while True:
            item = await updates.get()
            if item is _TOOL_FINISHED:
                break
            if isinstance(item, AgentToolResult):
                yield item
        # Re-raises a cancellation raised by the tool itself, as upstream does.
        outcome = task.result()
    finally:
        accepting = False
        if not task.done():
            # The consumer went away or was cancelled: stop the tool and wait for
            # its cleanup before unwinding.
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
    yield outcome


def _error_result(message: str) -> AgentToolResult:
    return AgentToolResult(content=[TextContent(text=message)], details={})


def _error_message(model: str, message: str) -> AssistantMessage:
    return AssistantMessage(
        model=model,
        content=[],
        stop_reason="error",
        error_message=message,
    )


def _aborted_message(model: str, reason: str | None) -> AssistantMessage:
    return AssistantMessage(
        model=model,
        content=[],
        stop_reason="aborted",
        error_message=reason or "Operation aborted",
    )
