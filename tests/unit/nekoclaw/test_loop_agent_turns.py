# Ported from huggingface/tau (https://github.com/huggingface/tau),
# commit 0d256d5729b76ad5dae2fef453b04da9b8241208, file tests/test_agent_turns.py.
# Copyright (c) 2026 Alejandro AO. MIT License: see nekoclaw/runtime/loop/LICENSE.
# Adapted to this repository: pytest-asyncio auto mode instead of anyio marks,
# imports point at nekoclaw.runtime.loop, and the fake provider lives in
# tests/unit/nekoclaw/loop_event_helpers.py.

"""Pi-compatible tool termination and completed-turn scheduling."""

import asyncio
from collections.abc import Mapping

import pytest

from tests.unit.nekoclaw.loop_event_helpers import assistant_done
from nekoclaw.runtime.loop import (
    AgentHarness,
    AgentHarnessConfig,
    AgentTool,
    AgentToolResult,
    AgentTurnContext,
    AgentTurnDecision,
    AssistantMessage,
    MessageEndEvent,
    SimpleCancellationToken,
    ToolCall,
    ToolExecutionEndEvent,
    ToolResultMessage,
    UserMessage,
)
from nekoclaw.runtime.loop.loop import run_agent_loop
from nekoclaw.runtime.loop.provider_events import AssistantErrorEvent
from nekoclaw.runtime.loop.types import JSONValue
from tests.unit.nekoclaw.loop_event_helpers import FakeProvider




def _response(*calls: str) -> AssistantMessage:
    return AssistantMessage(
        content=[
            ToolCall(id=f"call-{index}", name=name, arguments={})
            for index, name in enumerate(calls)
        ]
        if calls
        else "Done"
    )


def _provider(*responses: AssistantMessage) -> FakeProvider:
    return FakeProvider([[assistant_done(response)] for response in responses])


def _tool(name: str, terminate: bool | None, executed: list[str]) -> AgentTool:
    async def execute(tool_call_id, arguments, signal=None, on_update=None):  # noqa: ANN001, ANN202
        del tool_call_id, arguments, signal
        executed.append(name)
        # Partial flags must never affect final scheduling.
        if on_update:
            on_update(AgentToolResult(content="Working", terminate=not terminate))
        return AgentToolResult(content=f"Result from {name}", terminate=terminate)

    return AgentTool(
        name=name, label=name, description=name, parameters={"type": "object"}, execute_fn=execute
    )


@pytest.mark.parametrize(
    ("flags", "requests"),
    [
        ([True], 1),
        ([True, True], 1),
        ([True, False], 2),
        ([True, None], 2),
        ([False], 2),
        ([None], 2),
    ],
)
async def test_only_all_final_results_terminate(flags: list[bool | None], requests: int) -> None:
    executed: list[str] = []
    names = [f"tool{index}" for index in range(len(flags))]
    provider = _provider(_response(*names), _response())
    harness = AgentHarness(
        AgentHarnessConfig(
            provider=provider,
            model="fake",
            system="",
            tools=[_tool(name, flag, executed) for name, flag in zip(names, flags, strict=True)],
        )
    )
    events = [event async for event in harness.prompt("Go")]
    assert executed == names
    assert len(provider.calls) == requests
    assert events[-1].type == "agent_end"
    assert sum(event.type == "turn_end" for event in events) == requests
    results = [message for message in harness.messages if isinstance(message, ToolResultMessage)]
    assert len(results) == len(flags)
    assert all("terminate" not in result.model_dump() for result in results)
    assert all(not result.is_error for result in results)
    assert len([event for event in events if isinstance(event, ToolExecutionEndEvent)]) == len(
        flags
    )


@pytest.mark.parametrize("override", [True, False, None])
async def test_termination_uses_post_hook_result(override: bool | None) -> None:
    async def after(call, result, is_error):  # noqa: ANN001, ANN202
        del call
        return result.model_copy(update={"terminate": override}), is_error

    provider = _provider(_response("submit"), _response())
    harness = AgentHarness(
        AgentHarnessConfig(
            provider=provider,
            model="fake",
            system="",
            tools=[_tool("submit", True, [])],
            after_tool_call=after,
        )
    )
    _ = [event async for event in harness.prompt("Go")]
    assert len(provider.calls) == (1 if override is True else 2)


@pytest.mark.parametrize("queue", ["steering", "follow_up"])
async def test_terminating_tool_still_honors_queued_messages(queue: str) -> None:
    provider = _provider(_response("submit"), _response())
    harness = AgentHarness(
        AgentHarnessConfig(
            provider=provider, model="fake", system="", tools=[_tool("submit", True, [])]
        )
    )
    async for event in harness.prompt("Go"):
        if isinstance(event, ToolExecutionEndEvent):
            (harness.steer if queue == "steering" else harness.follow_up)("Next job")
    assert len(provider.calls) == 2
    assert any(
        isinstance(message, UserMessage) and message.text == "Next job"
        for message in provider.calls[1][2]
    )
    assert not harness.has_queued_messages()


@pytest.mark.parametrize("asynchronous", [False, True])
async def test_finish_turn_order_context_and_signal(asynchronous: bool) -> None:
    old = UserMessage(content="Earlier")
    response = _response("submit")
    provider = _provider(response)
    seen: list[str] = []
    contexts: list[AgentTurnContext] = []

    def finish(turn, signal):  # noqa: ANN001, ANN202
        contexts.append(turn)
        seen.append("finish_turn")
        assert signal is not None and not signal.is_cancelled()
        assert turn.message is response
        assert len(turn.tool_results) == 1
        assert turn.messages == harness.messages
        assert turn.messages[0] == old
        assert turn.new_messages == turn.messages[1:]
        assert turn.messages[-1] == turn.tool_results[-1]
        return AgentTurnDecision(action="end")

    async def async_finish(turn, signal):  # noqa: ANN001, ANN202
        await asyncio.sleep(0)
        return finish(turn, signal)

    harness = AgentHarness(
        AgentHarnessConfig(
            provider=provider,
            model="fake",
            system="",
            tools=[_tool("submit", False, [])],
            finish_turn=async_finish if asynchronous else finish,
        ),
        messages=[old],
    )
    harness.subscribe(
        lambda event: seen.append(
            f"message_end:{event.message.role}"
            if isinstance(event, MessageEndEvent)
            else event.type
        )
    )
    _ = [event async for event in harness.prompt("Go")]
    assert seen[-4:] == ["message_end:toolResult", "finish_turn", "turn_end", "agent_end"]
    assert len(contexts) == len(provider.calls) == 1
    assert not harness.is_running


async def test_end_preserves_queues_and_continue_can_resume() -> None:
    provider = _provider(_response("submit"), _response())

    def finish(turn, signal):  # noqa: ANN001, ANN202
        del turn, signal
        return AgentTurnDecision(action="end")

    harness = AgentHarness(
        AgentHarnessConfig(
            provider=provider,
            model="fake",
            system="",
            tools=[_tool("submit", False, [])],
            finish_turn=finish,
        )
    )
    async for event in harness.prompt("Go"):
        if isinstance(event, ToolExecutionEndEvent):
            harness.steer("Steering")
            harness.follow_up("Follow-up")
    assert len(provider.calls) == 1
    assert harness.queued_messages.steering[0].text == "Steering"
    assert harness.queued_messages.follow_up[0].text == "Follow-up"
    # End does not persist as a permanent session stop.
    _ = [event async for event in harness.continue_()]
    assert len(provider.calls) == 2
    assert not harness.queued_messages.steering
    assert harness.queued_messages.follow_up[0].text == "Follow-up"


@pytest.mark.parametrize("natural", ["none", "tool", "steering", "follow_up", "terminating_tool"])
async def test_continue_selects_one_request_without_duplicates(natural: str) -> None:
    first = _response("submit") if natural in {"tool", "terminating_tool"} else _response()
    provider = _provider(first, _response())
    turns = 0

    def finish(turn, signal):  # noqa: ANN001, ANN202
        nonlocal turns
        del turn, signal
        turns += 1
        if turns == 1:
            if natural == "steering":
                harness.steer("Next")
            elif natural == "follow_up":
                harness.follow_up("Next")
            return AgentTurnDecision(action="continue")
        return None

    harness = AgentHarness(
        AgentHarnessConfig(
            provider=provider,
            model="fake",
            system="",
            tools=[_tool("submit", natural == "terminating_tool", [])],
            finish_turn=finish,
        )
    )
    _ = [event async for event in harness.prompt("Go")]
    assert turns == len(provider.calls) == 2
    assert not harness.has_queued_messages()


@pytest.mark.parametrize("reason", ["error", "aborted"])
@pytest.mark.parametrize("action", ["end", "continue"])
async def test_failed_provider_turn_calls_callback_but_remains_hard_exit(
    reason: str, action: str
) -> None:
    failure = AssistantMessage(stop_reason=reason, error_message="Stopped")
    provider = FakeProvider([[AssistantErrorEvent(reason=reason, error=failure)]])
    seen = []
    polls = []

    def finish(turn, signal):  # noqa: ANN001, ANN202
        del signal
        seen.append(turn)
        return AgentTurnDecision(action=action)

    def steering():  # noqa: ANN202
        polls.append("steering")
        return ()

    events = [
        event
        async for event in run_agent_loop(
            provider=provider,
            model="fake",
            system="",
            messages=[],
            tools=[],
            finish_turn=finish,
            get_steering_messages=steering,
            get_follow_up_messages=lambda: pytest.fail("Must not poll follow-ups"),
        )
    ]
    assert len(seen) == len(provider.calls) == 1
    assert seen[0].message is failure and seen[0].tool_results == ()
    assert polls == ["steering"]
    assert [event.type for event in events][-2:] == ["turn_end", "agent_end"]


@pytest.mark.parametrize("max_turns", [0, 1])
async def test_explicit_continuation_cannot_bypass_turn_limit(max_turns: int) -> None:
    provider = _provider(_response())
    contexts = []

    def finish(turn, signal):  # noqa: ANN001, ANN202
        del signal
        contexts.append(turn)
        return AgentTurnDecision(action="continue")

    harness = AgentHarness(
        AgentHarnessConfig(
            provider=provider,
            model="fake",
            system="",
            max_turns=max_turns,
            finish_turn=finish,
        )
    )
    _ = [event async for event in harness.prompt("Go")]
    assert len(provider.calls) == max_turns
    assert contexts[-1].message.stop_reason == "error"
    assert len(contexts) == max_turns + 1


async def test_cancellation_not_swallowed_by_terminating_result() -> None:
    provider = _provider(_response("submit"))
    contexts = []
    signal = SimpleCancellationToken()

    async def execute(tool_call_id, arguments, token=None, on_update=None):  # noqa: ANN001, ANN202
        del tool_call_id, arguments, token, on_update
        signal.cancel()
        return AgentToolResult(content="Submitted", terminate=True)

    tool = AgentTool(
        name="submit", label="Submit", description="Submit", parameters={}, execute_fn=execute
    )

    def finish(turn, token):  # noqa: ANN001, ANN202
        del token
        contexts.append(turn)
        return AgentTurnDecision(action="continue")

    events = [
        event
        async for event in run_agent_loop(
            provider=provider,
            model="fake",
            system="",
            messages=[],
            tools=[tool],
            signal=signal,
            finish_turn=finish,
        )
    ]
    assert len(provider.calls) == 1
    assert contexts[-1].message.stop_reason == "aborted"
    assert events[-1].type == "agent_end"


@pytest.mark.parametrize("exception", [RuntimeError, asyncio.CancelledError])
async def test_callback_failure_propagates_and_harness_can_run_again(exception) -> None:  # noqa: ANN001
    async def finish(turn, signal):  # noqa: ANN001, ANN202
        del turn, signal
        raise exception("Stop")

    provider = _provider(_response(), _response())
    harness = AgentHarness(
        AgentHarnessConfig(
            provider=provider,
            model="fake",
            system="",
            finish_turn=finish,
        )
    )
    with pytest.raises(exception):
        _ = [event async for event in harness.prompt("Go")]
    assert not harness.is_running
    harness.config.finish_turn = None
    _ = [event async for event in harness.prompt("Try again")]
    assert len(provider.calls) == 2


async def test_tool_exception_is_not_terminating() -> None:
    async def execute(
        tool_call_id: str, arguments: Mapping[str, JSONValue], signal=None, on_update=None
    ):  # noqa: ANN001, ANN202
        del tool_call_id, arguments, signal
        on_update(AgentToolResult(content="Almost submitted", terminate=True))
        raise ValueError("Submission failed")

    tool = AgentTool(
        name="submit", label="Submit", description="Submit", parameters={}, execute_fn=execute
    )
    provider = _provider(_response("submit"), _response())
    harness = AgentHarness(
        AgentHarnessConfig(provider=provider, model="fake", system="", tools=[tool])
    )
    events = [event async for event in harness.prompt("Go")]
    result = next(event for event in events if isinstance(event, ToolExecutionEndEvent))
    assert result.is_error and result.result.terminate is None
    assert len(provider.calls) == 2
