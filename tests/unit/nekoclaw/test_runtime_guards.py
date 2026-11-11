"""Repeated-call reminders and automatic goal rounds."""

from __future__ import annotations

import json
from pathlib import Path

from config.prompts import prompts_nekoclaw as texts
from nekoclaw.runtime.guards import RepeatToolGuard, canonical_arguments
from nekoclaw.runtime.protocol import GoalSpec, RuntimeLimits
from tests.unit.nekoclaw.fakes import ScriptedLLM, call, finish, make_spec, open_session, say


def _texts_of(request: list[dict]) -> list[str]:
    return [str(message.get("content")) for message in request]


# ---------------------------------------------------------------------------
# Repeated tool calls
# ---------------------------------------------------------------------------


def test_reminders_fire_on_the_third_fifth_and_eighth_identical_call() -> None:
    guard = RepeatToolGuard()
    arguments = {"pattern": "TODO", "path": "src"}
    outcomes = [guard.observe("grep", arguments) for _ in range(9)]
    assert [index + 1 for index, outcome in enumerate(outcomes) if outcome] == [3, 5, 8]
    assert outcomes[2] == texts.REPEAT_TOOL_GENTLE_REMINDER
    assert outcomes[4] == (
        "Repeated tool call detected:\n"
        "- tool: grep\n"
        "- consecutive_calls: 5\n"
        '- arguments: {"path":"src","pattern":"TODO"}\n'
        "The repeated calls are not making progress. Do not call this tool with these exact "
        "arguments again. Inspect the latest result and choose a different action, different "
        "arguments, or finish the task if enough evidence has been gathered."
    )
    assert "- consecutive_calls: 8\n" in outcomes[7]


def test_a_different_call_or_user_text_breaks_the_chain() -> None:
    guard = RepeatToolGuard()
    guard.observe("read", {"file_path": "a"})
    guard.observe("read", {"file_path": "a"})
    assert guard.observe("read", {"file_path": "b"}) is None  # different arguments
    assert guard.observe("read", {"file_path": "a"}) is None  # the run starts over
    guard.observe("read", {"file_path": "a"})
    guard.reset()  # the user said something
    assert guard.observe("read", {"file_path": "a"}) is None
    # Key order does not make two calls different.
    guard = RepeatToolGuard()
    guard.observe("grep", {"a": 1, "b": {"y": 1, "x": 2}})
    guard.observe("grep", {"b": {"x": 2, "y": 1}, "a": 1})
    assert guard.observe("grep", {"a": 1, "b": {"x": 2, "y": 1}}) is not None
    assert canonical_arguments({"b": 1, "a": "é"}) == '{"a":"é","b":1}'


def test_long_arguments_are_previewed_in_the_detailed_reminder() -> None:
    guard = RepeatToolGuard()
    arguments = {"content": "x" * 2000}
    reminder = None
    for _ in range(5):
        reminder = guard.observe("write", arguments)
    assert reminder is not None
    line = next(line for line in reminder.splitlines() if line.startswith("- arguments: "))
    canonical = canonical_arguments(arguments)
    assert line == f"- arguments: {canonical[:500]}… (+{len(canonical) - 500} more chars)"


async def test_the_session_reminds_but_does_not_block_a_repeating_model(tmp_path: Path) -> None:
    repeated = {"todos": [{"content": "same", "status": "in_progress"}]}
    llm = ScriptedLLM(
        [[call("todo_write", repeated, f"c{n}"), finish("tool_calls")] for n in range(3)]
        + [[say("moving on"), finish()]]
    )
    session, events = await open_session(make_spec(tmp_path), llm)
    try:
        session.prompt("go")
        await events.wait_ended()
        # All three calls ran: the guard never refuses a call.
        assert [event["is_error"] for event in events.of("tool_end")] == [False, False, False]
        assert texts.REPEAT_TOOL_GENTLE_REMINDER not in _texts_of(llm.requests[2])
        fourth = llm.requests[3]
        assert fourth[-1] == {"role": "user", "content": texts.REPEAT_TOOL_GENTLE_REMINDER}
        assert fourth[-2]["role"] == "tool"
    finally:
        await session.close()


# ---------------------------------------------------------------------------
# Goal rounds
# ---------------------------------------------------------------------------


def _goal_spec(tmp_path: Path, max_rounds: int = 5, **overrides):  # noqa: ANN003, ANN202
    return make_spec(tmp_path, goal=GoalSpec(objective='Ship "v2" of the parser', max_rounds=max_rounds), **overrides)


async def test_an_unfinished_goal_starts_another_round_until_it_is_completed(tmp_path: Path) -> None:
    holder: dict = {}

    def complete_goal() -> list:
        goal = holder["session"].goals.current
        return [
            call("update_goal", {"goal_id": goal.id, "revision": goal.revision, "action": "complete"}),
            finish("tool_calls"),
        ]

    llm = ScriptedLLM(
        [
            [say("Made a start."), finish()],
            [say("Round one: more work done."), finish()],
            complete_goal,
            [say("Closing: v2 is shipped and verified."), finish()],
        ]
    )
    session, events = await open_session(_goal_spec(tmp_path), llm)
    holder["session"] = session
    try:
        session.prompt("Get going on the goal.")
        ended = await events.wait_ended()

        assert ended["reason"] == "completed"
        assert ended["text"] == "Closing: v2 is shipped and verified."
        # One stretch of work for the host: a single `ended`, however many rounds.
        assert len(events.of("ended")) == 1
        round_one = llm.requests[1][-1]["content"]
        assert round_one == texts.goal_round_prompt(json.dumps('Ship "v2" of the parser'), 1, 5)
        assert round_one.startswith('<goal_round>\nObjective: "Ship \\"v2\\" of the parser"\nRound: 1/5\n\n')
        assert llm.requests[2][-1]["content"].startswith("<goal_round>") and "Round: 2/5" in llm.requests[2][-1]["content"]
        # Completing inside the round hands the model the closing instruction.
        wrapup = llm.requests[3][-1]["content"]
        assert wrapup == texts.goal_complete_wrapup(json.dumps('Ship "v2" of the parser'))
        view = json.loads(events.of("tool_end")[0]["text"])
        assert view["goal"]["phase"] == "complete" and view["goal"]["roundsStarted"] == 2
        assert view["activation"] == "disarmed"
        assert len(llm.requests) == 4  # a completed goal starts no further round
    finally:
        await session.close()


async def test_rounds_stop_at_the_limit_and_the_goal_is_marked_blocked(tmp_path: Path) -> None:
    llm = ScriptedLLM([[say(f"answer {n}"), finish()] for n in range(6)])
    session, events = await open_session(_goal_spec(tmp_path, max_rounds=2), llm)
    try:
        session.prompt("Work on it.")
        await events.wait_ended()
        assert len(llm.requests) == 3  # the prompt plus exactly two rounds
        goal = session.goals.current
        assert goal.phase == "blocked"
        assert (goal.blocked_code, goal.blocked_message) == (
            "round-limit",
            "Goal reached its configured limit of 2 rounds.",
        )
    finally:
        await session.close()


async def test_blocked_is_refused_before_the_third_consecutive_round(tmp_path: Path) -> None:
    holder: dict = {}

    def try_blocked() -> list:
        goal = holder["session"].goals.current
        return [
            call(
                "update_goal",
                {"goal_id": goal.id, "revision": goal.revision, "action": "blocked", "blocked_reason": "no network"},
            ),
            finish("tool_calls"),
        ]

    llm = ScriptedLLM(
        [
            [say("start"), finish()],
            try_blocked,  # round 1: too early
            [say("still trying"), finish()],
            [say("round 2"), finish()],
            try_blocked,  # round 3: allowed
            [say("I am blocked: no network."), finish()],
        ]
    )
    session, events = await open_session(_goal_spec(tmp_path), llm)
    holder["session"] = session
    try:
        session.prompt("Begin.")
        await events.wait_ended()
        early, late = events.of("tool_end")
        assert early["is_error"] is True
        assert early["text"] == (
            "Error: blocked requires at least 3 consecutive goal rounds; current round is 1"
        )
        assert late["is_error"] is False
        assert json.loads(late["text"])["goal"]["blockedReason"] == {
            "code": "model-reported",
            "message": "no network",
        }
        assert llm.requests[5][-1]["content"] == texts.goal_blocked_wrapup(
            json.dumps('Ship "v2" of the parser'), json.dumps("no network")
        )
        assert session.goals.current.phase == "blocked"
    finally:
        await session.close()


async def test_creating_a_goal_needs_text_from_the_user_in_that_run(tmp_path: Path) -> None:
    create = [call("create_goal", {"objective": "Migrate the database", "max_goal_rounds": 3}), finish("tool_calls")]
    llm = ScriptedLLM(
        [
            create,  # in a run the user started: allowed
            [say("Goal created."), finish()],
            [say("round 1"), finish()],
            [say("round 2"), finish()],
            [say("round 3"), finish()],
        ]
    )
    session, events = await open_session(make_spec(tmp_path), llm)
    try:
        session.prompt("Please migrate the database, however long it takes.")
        await events.wait_ended()
        created = json.loads(events.of("tool_end")[0]["text"])
        assert created["goal"]["objective"] == "Migrate the database"
        assert created["goal"]["maxGoalRounds"] == 3 and created["activation"] == "armed"
        assert len(llm.requests) == 5
        assert session.goals.current.blocked_code == "round-limit"
    finally:
        await session.close()

    # A run started by a runtime notice carries no such authority.
    llm = ScriptedLLM([[call("create_goal", {"objective": "Sneak in a goal"}), finish("tool_calls")], [say("ok"), finish()]])
    session, events = await open_session(make_spec(tmp_path, "s2"), llm)
    try:
        session.deliver_notice("job", "background job job-9 (bash: x) finished [status: completed]. No new output.")
        await events.wait_ended()
        refused = events.of("tool_end")[0]
        assert refused["is_error"] is True
        assert refused["text"] == "Error: this goal operation requires a direct human turn on a top-level agent"
        assert session.goals.current is None
    finally:
        await session.close()

    # Neither does a familiar that another familiar started.
    llm = ScriptedLLM([[call("create_goal", {"objective": "Child goal"}), finish("tool_calls")], [say("ok"), finish()]])
    session, events = await open_session(make_spec(tmp_path, "s3", top_level=False), llm)
    try:
        session.prompt("Do this part.")
        await events.wait_ended()
        assert events.of("tool_end")[0]["is_error"] is True
    finally:
        await session.close()


async def test_abort_disarms_the_goal_and_resume_rearms_it(tmp_path: Path) -> None:
    import asyncio

    release = asyncio.Event()

    async def hang() -> None:
        await release.wait()

    holder: dict = {}

    def resume_goal() -> list:
        goal = holder["session"].goals.current
        return [
            call("update_goal", {"goal_id": goal.id, "revision": goal.revision, "action": "resume"}),
            finish("tool_calls"),
        ]

    spec = _goal_spec(tmp_path, max_rounds=3, limits=RuntimeLimits(goal_default_rounds=3))
    llm = ScriptedLLM([[say("working"), hang]])
    session, events = await open_session(spec, llm)
    holder["session"] = session
    try:
        session.prompt("Start the goal.")
        await events.wait_for(lambda event: event["type"] == "text")
        session.abort()
        await events.wait_ended()
        goal = session.goals.current
        assert (goal.phase, goal.activation) == ("active", "disarmed")
        assert len(llm.requests) == 1  # no round followed the abort

        # A plain follow-up does not restart the rounds by itself ...
        llm.add(say("Noted."), finish())
        mark = len(events.events)
        session.prompt("Just a question.")
        await events.wait_ended(start=mark)
        assert len(llm.requests) == 2

        # ... but the user asking to continue lets the model rearm the goal.
        llm.turns.extend([resume_goal, [say("Resumed."), finish()], [say("round 1"), finish()], [say("r2"), finish()], [say("r3"), finish()]])
        mark = len(events.events)
        session.prompt("Please continue with the goal.")
        await events.wait_ended(start=mark)
        assert session.goals.current.rounds_started == 3
        assert any("Round: 1/3" in text for text in _texts_of(llm.requests[4]))
    finally:
        release.set()
        await session.close()


async def test_a_goal_loaded_from_a_transcript_is_disarmed(tmp_path: Path) -> None:
    from nekoclaw.runtime.tools.goal import GoalState

    # The stored activation is ignored on purpose: a loaded goal never runs by itself.
    state = GoalState(default_rounds=5, max_rounds=10)
    armed = state.create("Stored objective", 4)
    saved = state.to_json()
    assert saved["goal"]["activation"] == "armed"
    restored = GoalState(default_rounds=5, max_rounds=10)
    restored.restore(saved)
    assert restored.current.id == armed.id
    assert (restored.current.phase, restored.current.activation) == ("active", "disarmed")

    spec = make_spec(tmp_path, goal=GoalSpec(objective="Long job", max_rounds=9))
    first = ScriptedLLM([[say("first"), finish()], [say("round 1"), finish()], [say("round 2"), finish()]])
    session, events = await open_session(spec, first)
    session.prompt("Go.")
    await events.wait_for(lambda event: event["type"] == "text" and event["delta"] == "round 2")
    await session.close()
    assert len(first.requests) == 3  # rounds were running when the session went away

    # Reopened with a different goal in the spec: the stored one wins, disarmed.
    reopened_llm = ScriptedLLM([[say("hello again"), finish()]])
    other = spec.model_copy(update={"goal": GoalSpec(objective="A different goal", max_rounds=2)})
    session, events = await open_session(other, reopened_llm)
    try:
        goal = session.goals.current
        assert goal.objective == "Long job"
        assert (goal.phase, goal.activation) == ("active", "disarmed")
        session.prompt("Hi.")
        await events.wait_ended()
        assert len(reopened_llm.requests) == 1  # no round started on its own
    finally:
        await session.close()
