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

"""Changing the preset of a session: who may, what is put on record, and what its task agent starts with next.

A member of a project and a session of one commission can be given
another preset (``NekoPowerHost.change_duty``). What its task agent may
do and is told of its role stays in its system prompt and its tools; the
head of its requests is put together anew with its next start, once, and
it is told of the change with the first message of that start.
"""

from __future__ import annotations

import asyncio
import types
from pathlib import Path
from typing import Any

import pytest

from config.prompts import prompts_nekopower_presets as presets
from nekopower.host.permissions import OPEN_SESSION
from nekopower.runtime.snapshot import REASON_PRESET, read_snapshot
from tests.unit.nekopower.fakes import ScriptedLLM, call, finish
from tests.unit.nekopower.host_fakes import (
    FakeDecisionLLM,
    fake_model,
    let_go,
    make_host,
    scripted_runtime,
    wait_status,
    wait_until,
)

pytestmark = pytest.mark.unit

CHANGED = presets.PRESET_CHANGED.format(was="general", now="scout")


@pytest.fixture(autouse=True)
def _one_after_the_other(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("config.nekopower_settings.TASK_AGENT_FORK_CONTINUE_ENABLED", False)
    monkeypatch.setattr("config.nekopower_settings.TASK_AGENT_REVIEW_ENABLED", False)


@pytest.fixture
async def hosts() -> Any:
    made: list[Any] = []
    yield made
    for host in made:
        await host.shutdown()


def _host(hosts: list[Any], tmp_path: Path, llm: Any, specs: list[Any] | None = None) -> Any:
    """A host with no plugins, whose every session talks to ``llm``; ``specs`` gets what each start was opened with."""

    def pick(spec: Any) -> Any:
        if specs is not None:
            specs.append(spec)
        return llm

    host = make_host(
        tmp_path, None, runtime_factory=scripted_runtime(pick), task_agent_model=fake_model, decision_llm=FakeDecisionLLM()
    )

    async def nowhere(_directory: str) -> bool:
        return False

    host.task_agents.families._probe = nowhere
    hosts.append(host)
    return host


async def _shop(hosts: list[Any], tmp_path: Path, llm: Any, specs: list[Any] | None = None, preset: str = "general") -> Any:
    """A host with the project "Shop site" and the member the user opened it with."""
    host = _host(hosts, tmp_path, llm, specs)
    workdir = tmp_path / "Shop site-files"
    workdir.mkdir()
    opened = await host.open_session("Lan", "Shop site", preset=preset, workdir=str(workdir))
    host.member, host.workdir = opened["session_id"], workdir
    return host


async def _dispatch(host: Any, text: str) -> Any:
    receipt = await host.dispatch(text, lanlan_name="Lan", lang="en")
    await host.brain.settle()
    return receipt


async def _done(host: Any, text: str) -> Any:
    commission = await wait_status(host, (await _dispatch(host, text)).commission_id, "done")
    await let_go(host)
    return commission


def _report(summary: str = "done") -> list[Any]:
    return [call("report", {"status": "ready", "summary": summary}), finish("tool_calls")]


def _write(name: str, content: str = "x") -> list[Any]:
    return [call("write", {"file_path": name, "content": content}), finish("tool_calls")]


def _system(request: list[dict[str, Any]]) -> str:
    assert request[0]["role"] == "system"
    return str(request[0]["content"])


def _said(request: list[dict[str, Any]]) -> str:
    return "\n".join(str(message["content"]) for message in request if message.get("role") == "user")


def _tools(overrides: dict[str, Any]) -> set[str]:
    return {tool["function"]["name"] for tool in overrides["tools"]}


async def _events(host: Any, commission_id: str, kind: str) -> list[dict[str, Any]]:
    return [event.payload for event in await host.ledger.events(commission_id) if event.type == kind]


def _no_question(host: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    """Fail the test should the user be asked whether a task agent that changes files may be there."""
    permissions = host.task_agents._permissions
    checked = permissions.check

    async def check(commission_id: str, category: str, *args: Any, **kwargs: Any) -> Any:
        assert category != OPEN_SESSION, "the user is not asked"
        return await checked(commission_id, category, *args, **kwargs)

    monkeypatch.setattr(permissions, "check", check)


# ---------------------------------------------------------------------------
# The ledger
# ---------------------------------------------------------------------------


async def test_a_change_of_preset_is_written_with_an_event_of_the_commission_its_task_agent_is_at(hosts, tmp_path):
    ledger = _host(hosts, tmp_path, ScriptedLLM([])).ledger
    session = await ledger.create_session(lanlan_name="Lan", name="photos", kind="single")

    async def changes(commission_id: str) -> list[dict[str, Any]]:
        return [event.payload for event in await ledger.events(commission_id) if event.type == "duty_changed"]

    # No commission was worked on in it: the preset, and nothing else.
    kept, was, on = await ledger.change_preset(session.id, "scout", by="user")
    assert (kept.preset, was, on) == ("scout", "general", None)

    async def filed(title: str, *statuses: str) -> Any:
        commission, _created = await ledger.create(
            lanlan_name="Lan", title=title, dispatch_text=title, source="dispatch", route="task_agent", session_id=session.id
        )
        for step in statuses:
            moved = await ledger.transition(commission.id, step, reason="test", result_summary=title)
            assert moved.applied, moved.refusal
        return commission

    await filed("one", "working", "done")
    waits = await filed("two")
    await filed("three", "working", "cancelled")

    kept, was, on = await ledger.change_preset(session.id, "worker", by="user", was="scout")
    # With the one that waits for its turn there, not the one filed last, which is over.
    assert (kept.preset, was, on) == ("worker", "scout", waits.id)
    assert await changes(waits.id) == [{"session": session.id, "from": "scout", "to": "worker", "by": "user"}]

    # With the one its task agent is at, though another was filed after it and waits.
    at_work = await filed("four", "working")
    later = await filed("five")
    _kept, _was, on = await ledger.change_preset(session.id, "planner", by="session")
    assert on == at_work.id and await changes(later.id) == []
    moved = await ledger.transition(at_work.id, "waiting_user", reason="test")
    assert moved.applied, moved.refusal
    _kept, _was, on = await ledger.change_preset(session.id, "scout", by="host")
    assert on == at_work.id
    assert [item["to"] for item in await changes(at_work.id)] == ["planner", "scout"]

    # What its task agent was last started under is no business of a change: a start writes that.
    assert (await ledger.get_session(session.id)).preset_told == ""
    recorded = await changes(at_work.id)
    # The preset it has already: nothing is written.
    kept, was, on = await ledger.change_preset(session.id, "scout", by="user")
    assert (kept.preset, was, on) == ("scout", "scout", None)
    # Looked at a while before, and another by now.
    with pytest.raises(ValueError, match="changed_meanwhile"):
        await ledger.change_preset(session.id, "worker", by="user", was="general")
    with pytest.raises(ValueError, match="unknown changer"):
        await ledger.change_preset(session.id, "worker", by="somebody")
    with pytest.raises(ValueError, match="short word"):
        await ledger.change_preset(session.id, " worker", by="user")
    assert await changes(at_work.id) == recorded
    assert (await ledger.get_session(session.id)).preset == "scout"
    assert await ledger.change_preset("no-such-session", "worker", by="user") == (None, "", None)
    # Neither is written with the other columns of a session.
    for column in ("preset", "preset_told"):
        with pytest.raises(ValueError, match="cannot be updated"):
            await ledger.update_session(session.id, **{column: "worker"})


@pytest.mark.parametrize("kind", ["project", "child", "fork"])
async def test_only_a_member_and_a_session_of_one_commission_have_their_preset_changed(hosts, tmp_path, kind):
    ledger = _host(hosts, tmp_path, ScriptedLLM([])).ledger
    session = await ledger.create_session(lanlan_name="Lan", name="it", kind=kind, project="Shop site")

    with pytest.raises(ValueError, match="not_changeable"):
        await ledger.change_preset(session.id, "scout", by="user")

    assert (await ledger.get_session(session.id)).preset == session.preset


async def test_what_a_session_was_started_under_is_what_a_start_says_and_a_change_leaves_it(hosts, tmp_path):
    ledger = _host(hosts, tmp_path, ScriptedLLM([])).ledger
    session = await ledger.create_session(lanlan_name="Lan", name="photos", kind="line")
    assert session.preset_told == ""

    await ledger.preset_was_told(session.id, "general")
    assert (await ledger.get_session(session.id)).preset_told == "general"
    # Changed, and changed again: what it was last started under stands until a start says otherwise.
    await ledger.change_preset(session.id, "scout", by="user")
    await ledger.change_preset(session.id, "planner", by="user")
    assert (await ledger.get_session(session.id)).preset_told == "general"
    # A start that opened it as one preset says that one, whatever it has by the time it says so.
    await ledger.preset_was_told(session.id, "scout")
    kept = await ledger.get_session(session.id)
    assert (kept.preset, kept.preset_told) == ("planner", "scout")


# ---------------------------------------------------------------------------
# What the task agent starts with next
# ---------------------------------------------------------------------------


async def test_after_a_change_the_next_start_puts_the_head_of_its_requests_together_anew_and_tells_it_once(
    hosts, tmp_path, monkeypatch
):
    llm = ScriptedLLM([_write("a.txt"), _report("header fixed"), _report("looked"), _report("looked again")])
    specs: list[Any] = []
    host = await _shop(hosts, tmp_path, llm, specs)
    _no_question(host, monkeypatch)
    first = await _done(host, "fix the header of the shop site")
    assert first.session_id == host.member and (host.workdir / "a.txt").exists()
    # What it was started under is on record from its first start.
    assert (await host.ledger.get_session(host.member)).preset_told == "general"

    changed = await host.change_duty(host.member, "scout")

    assert changed == {
        "session_id": host.member, "preset": "scout", "was": "general", "seat": "",
        "changed": True, "widened": False, "at_work": False,
    }
    kept = await host.ledger.get_session(host.member)
    assert (kept.preset, kept.preset_told) == ("scout", "general")
    (card,) = (await host.board())["projects"][0]["members"]
    assert (card["preset"], card["preset_was"]) == ("scout", "general")
    # On record with the commission it worked on last, and on the timeline.
    assert await _events(host, first.id, "duty_changed") == [
        {"session": host.member, "from": "general", "to": "scout", "by": "user"}
    ]
    (entry,) = [item for item in (await host.timeline(project="Shop site"))["entries"] if item["kind"] == "duty_changed"]
    assert (entry["session_id"], entry["commission_id"], entry["peer"]) == (host.member, first.id, None)
    assert entry["detail"] == {"from": "general", "to": "scout", "by": "user"}
    # No longer one the project's other members wait for.
    assert host.member not in host.task_agents._writes_in

    second = await _done(host, "look at the footer of the shop site")

    assert second.session_id == host.member
    assert [spec.head_of for spec in specs] == ["general", "scout"]
    assert {"write", "edit", "pwsh", "bash", "capability_call", "subagent"} <= set(specs[1].disabled_tools)
    before, after = llm.requests[1], llm.requests[2]
    # Its system prompt and its tools are the new preset's, in the conversation it had.
    assert "Your role: scout." in _system(after) and "Your role:" not in _system(before)
    assert presets.PRESET_ROSTER_INTRO in _system(before) and presets.PRESET_ROSTER_INTRO not in _system(after)
    assert "write" in _tools(llm.overrides[1]) and not {"write", "edit", "subagent"} & _tools(llm.overrides[2])
    assert "header" in _said(after) and _said(after).count(CHANGED) == 1 and CHANGED not in _said(before)
    kept = await host.ledger.get_session(host.member)
    made = read_snapshot(Path(kept.session_dir))
    assert (made.reason, made.head_of) == (REASON_PRESET, "scout")
    # Told, and its requests begin as its preset has them: nothing more to tell.
    assert kept.preset_told == "scout"
    assert (await host.board())["projects"][0]["members"][0]["preset_was"] is None

    await _done(host, "look at the fonts of the shop site")

    # Opened as the preset its head was put together for: that is used as it is.
    assert [spec.head_of for spec in specs] == ["general", "scout", "scout"]
    assert read_snapshot(Path(kept.session_dir)) == made
    assert _system(llm.requests[3]) == _system(after) and llm.overrides[3]["tools"] == llm.overrides[2]["tools"]
    assert _said(llm.requests[3]).count(CHANGED) == 1


async def test_a_run_under_way_goes_on_as_it_was_opened_and_the_change_holds_from_the_next_start(hosts, tmp_path):
    release = asyncio.Event()

    async def wait() -> None:
        await release.wait()

    llm = ScriptedLLM([[wait, *_write("a.txt")], _report("header fixed"), _report("looked")])
    specs: list[Any] = []
    host = await _shop(hosts, tmp_path, llm, specs)
    receipt = await _dispatch(host, "fix the header of the shop site")
    await wait_until(lambda: len(llm.requests) >= 1, what="the member being at work")

    changed = await host.change_duty(host.member, "scout")

    # Its run has the tools it was opened with, and the project's other members wait for it still.
    assert (changed["changed"], changed["at_work"]) == (True, True)
    assert host.task_agents._writes_in.get(host.member) == ("Shop site", host.member)
    release.set()
    await wait_status(host, receipt.commission_id, "done")
    await let_go(host)
    assert (host.workdir / "a.txt").exists() and CHANGED not in _said(llm.requests[-1])

    await _done(host, "look at the footer of the shop site")

    assert [spec.head_of for spec in specs] == ["general", "scout"] and "write" in specs[1].disabled_tools
    assert "Your role: scout." in _system(llm.requests[2]) and _said(llm.requests[2]).count(CHANGED) == 1
    assert host.member not in host.task_agents._writes_in


async def test_a_session_that_was_never_opened_is_told_of_nothing(hosts, tmp_path):
    llm = ScriptedLLM([_report("looked")])
    specs: list[Any] = []
    host = await _shop(hosts, tmp_path, llm, specs)

    changed = await host.change_duty(host.member, "scout")
    assert changed["changed"] and (await host.ledger.get_session(host.member)).preset_told == ""
    assert (await host.board())["projects"][0]["members"][0]["preset_was"] is None

    await _done(host, "look at the footer of the shop site")

    # Opened as what it is, for the first time: nothing was said under another preset.
    assert [spec.head_of for spec in specs] == ["scout"]
    assert "Your role: scout." in _system(llm.requests[0]) and "preset was changed" not in _said(llm.requests[0])
    assert (await host.ledger.get_session(host.member)).preset_told == "scout"


def _changed_while_starting(host: Any, monkeypatch: pytest.MonkeyPatch, preset: str) -> None:
    """Have the session's preset changed while its next start is under way: after it read the preset, before it is opened."""
    manager = host.task_agents
    spec_of = manager._spec
    starts = 0

    async def spec(*args: Any, **kwargs: Any) -> Any:
        nonlocal starts
        starts += 1
        if starts == 1:
            await host.ledger.change_preset(host.member, preset, by="user")
        return await spec_of(*args, **kwargs)

    monkeypatch.setattr(manager, "_spec", spec)


async def test_a_change_made_while_its_first_start_is_under_way_is_told_with_the_next(hosts, tmp_path, monkeypatch):
    llm = ScriptedLLM([_write("a.txt"), _report("header fixed"), _report("looked")])
    specs: list[Any] = []
    host = await _shop(hosts, tmp_path, llm, specs)
    _changed_while_starting(host, monkeypatch, "scout")

    await _done(host, "fix the header of the shop site")

    # That start went on as it began: opened as the preset it read, with the tools of that one.
    assert "write" not in specs[0].disabled_tools and (host.workdir / "a.txt").exists()
    kept = await host.ledger.get_session(host.member)
    assert (kept.preset, kept.preset_told) == ("scout", "general")

    await _done(host, "look at the footer of the shop site")

    assert [spec.head_of for spec in specs] == ["general", "scout"] and "write" in specs[1].disabled_tools
    assert "Your role: scout." in _system(llm.requests[2]) and _said(llm.requests[2]).count(CHANGED) == 1
    assert (await host.ledger.get_session(host.member)).preset_told == "scout"


async def test_changed_back_while_the_start_that_opens_it_anew_is_under_way_it_is_opened_anew_once_more(
    hosts, tmp_path, monkeypatch
):
    llm = ScriptedLLM([_report("header fixed"), _report("looked"), _write("b.txt"), _report("footer fixed")])
    specs: list[Any] = []
    host = await _shop(hosts, tmp_path, llm, specs)
    await _done(host, "fix the header of the shop site")
    await host.change_duty(host.member, "scout")
    # Back at the preset it was last started under, while the start that opens it as the other is under way.
    _changed_while_starting(host, monkeypatch, "general")

    await _done(host, "look at the footer of the shop site")

    assert [spec.head_of for spec in specs] == ["general", "scout"] and "write" in specs[1].disabled_tools
    kept = await host.ledger.get_session(host.member)
    # What that start opened it as is on record, and not the preset it has by now.
    assert (kept.preset, kept.preset_told) == ("general", "scout")

    await _done(host, "fix the footer of the shop site")

    assert [spec.head_of for spec in specs] == ["general", "scout", "general"] and "write" not in specs[2].disabled_tools
    assert "Your role:" not in _system(llm.requests[2]) and (host.workdir / "b.txt").exists()
    back = presets.PRESET_CHANGED.format(was="scout", now="general")
    assert _said(llm.requests[2]).count(back) == 1 and _said(llm.requests[2]).count(CHANGED) == 1
    assert (await host.ledger.get_session(host.member)).preset_told == "general"


async def test_it_is_on_record_as_told_of_a_change_only_once_its_session_took_the_message(hosts, tmp_path, monkeypatch):
    llm = ScriptedLLM([_report("header fixed"), _report("looked")])
    specs: list[Any] = []
    host = await _shop(hosts, tmp_path, llm, specs)
    await _done(host, "fix the header of the shop site")
    await host.change_duty(host.member, "scout")
    runtime = host.task_agents._runtimes.builtin()
    sent: list[str] = []

    async def never_arrives(_session_id: str, text: str, **_how: Any) -> None:
        sent.append(text)

    with pytest.MonkeyPatch.context() as patched:
        # The first message of its next start is sent, and the session never takes it.
        patched.setattr(runtime, "prompt", never_arrives)
        receipt = await _dispatch(host, "look at the footer of the shop site")
        await wait_until(lambda: bool(sent), what="the first message being sent")
        await asyncio.sleep(0.2)
        assert CHANGED in sent[0] and specs[-1].head_of == "scout"
        # Not on record as knowing itself as the preset it was not told of.
        kept = await host.ledger.get_session(host.member)
        assert kept.preset_told == "general"
        # The head of its requests is that preset's by now, and says so itself.
        made = read_snapshot(Path(kept.session_dir))
        assert (made.reason, made.head_of) == (REASON_PRESET, "scout")
        await host.abort(receipt.commission_id, reason="test")
        await let_go(host)
    assert (await host.ledger.get_session(host.member)).preset_told == "general"

    await _done(host, "look at the fonts of the shop site")

    # Said again, now that it is taken; the head was put together for this preset already, and is used.
    assert read_snapshot(Path(kept.session_dir)) == made
    assert "Your role: scout." in _system(llm.requests[1]) and _said(llm.requests[1]).count(CHANGED) == 1
    assert (await host.ledger.get_session(host.member)).preset_told == "scout"


async def test_changed_back_before_it_took_a_word_of_the_change_it_begins_as_it_was_and_is_told_nothing(
    hosts, tmp_path
):
    llm = ScriptedLLM([_report("header fixed"), _write("b.txt"), _report("footer fixed")])
    specs: list[Any] = []
    host = await _shop(hosts, tmp_path, llm, specs)
    await _done(host, "fix the header of the shop site")
    before = _system(llm.requests[0])
    await host.change_duty(host.member, "scout")
    runtime = host.task_agents._runtimes.builtin()
    sent: list[str] = []

    async def never_arrives(_session_id: str, text: str, **_how: Any) -> None:
        sent.append(text)

    with pytest.MonkeyPatch.context() as patched:
        # Opened as the other preset, with the head of its requests put together for that one; the
        # message that tells it so never arrives, and its preset is changed back before the start is over.
        patched.setattr(runtime, "prompt", never_arrives)
        receipt = await _dispatch(host, "look at the footer of the shop site")
        await wait_until(lambda: bool(sent), what="the first message being sent")
        kept = await host.ledger.get_session(host.member)
        assert read_snapshot(Path(kept.session_dir)).head_of == "scout"
        await host.change_duty(host.member, "general")
        await host.abort(receipt.commission_id, reason="test")
        await let_go(host)
    kept = await host.ledger.get_session(host.member)
    assert (kept.preset, kept.preset_told) == ("general", "general")

    await _done(host, "fix the footer of the shop site")

    # The head kept with it was the other preset's: not used, though nothing on record says it was changed.
    assert [spec.head_of for spec in specs] == ["general", "scout", "general"]
    made = read_snapshot(Path(kept.session_dir))
    assert (made.reason, made.head_of) == (REASON_PRESET, "general")
    assert _system(llm.requests[1]) == before and "write" in _tools(llm.overrides[1])
    assert (host.workdir / "b.txt").exists()
    # It never knew itself as anything else: there is nothing to tell it.
    assert "preset was changed" not in _said(llm.requests[1])


# ---------------------------------------------------------------------------
# Who may, and where it works afterwards
# ---------------------------------------------------------------------------


async def test_made_one_that_changes_files_by_the_user_it_gets_its_place_as_a_new_one_would_and_nobody_is_asked(
    hosts, tmp_path, monkeypatch
):
    host = await _shop(hosts, tmp_path, ScriptedLLM([]))
    _no_question(host, monkeypatch)
    reader = (await host.open_member("Lan", "Shop site", preset="scout"))["session_id"]

    # Beside the member that works in the project's directory: in a git worktree of its own.
    changed = await host.change_duty(reader, "worker")
    assert (changed["seat"], changed["widened"], changed["was"]) == ("tree", True, "scout")
    assert (await host.ledger.get_session(reader)).seat == "tree"
    # One that reads again works wherever the project does, like any that reads.
    changed = await host.change_duty(reader, "planner")
    assert (changed["seat"], changed["widened"]) == ("", False)
    # From one preset that only reads to another: where it works is not looked at.
    await host.ledger.update_session(reader, seat="reads")
    assert (await host.change_duty(reader, "scout"))["seat"] == "reads"

    # The only one of its project that changes files works in the project's directory.
    alone = tmp_path / "Notes-files"
    alone.mkdir()
    only = (await host.open_session("Lan", "Notes", preset="scout", workdir=str(alone)))["session_id"]
    assert (await host.change_duty(only, "general"))["seat"] == ""
    # A directory of its own from before it was the project's stays its own.
    await host.ledger.update_session(reader, seat="own")
    assert (await host.change_duty(reader, "worker"))["seat"] == "own"
    assert (await host.change_duty(reader, "scout"))["seat"] == "own"


async def test_anyone_but_the_user_has_the_user_asked_before_a_session_is_made_one_that_changes_files(
    hosts, tmp_path, monkeypatch
):
    host = await _shop(hosts, tmp_path, ScriptedLLM([]), preset="scout")
    manager = host.task_agents
    commission, _created = await host.ledger.create(
        lanlan_name="Lan", title="fix it", dispatch_text="fix it", source="dispatch", route="task_agent"
    )
    answers = [False, True]
    asked: list[tuple[str, str, dict[str, Any]]] = []

    async def check(commission_id: str, category: str, detail: Any = None) -> Any:
        asked.append((commission_id, category, detail))
        return types.SimpleNamespace(allow=answers.pop(0), reason="")

    monkeypatch.setattr(manager._permissions, "check", check)

    # Nothing to ask the user under.
    with pytest.raises(ValueError, match="no_commission"):
        await manager.change_duty(host.member, "worker", by="session")
    with pytest.raises(PermissionError, match="did not allow"):
        await manager.change_duty(host.member, "worker", by="session", commission_id=commission.id)
    assert (await host.ledger.get_session(host.member)).preset == "scout"

    changed = await manager.change_duty(host.member, "worker", by="host", commission_id=commission.id)

    assert (changed["preset"], changed["widened"]) == ("worker", True)
    assert [(item[0], item[1], item[2]["preset"], item[2]["tool"]) for item in asked] == [
        (commission.id, OPEN_SESSION, "worker", "change_duty")
    ] * 2
    # To one that changes no files, and between two that do, nobody is asked.
    await manager.change_duty(host.member, "general", by="session")
    await manager.change_duty(host.member, "planner", by="session")
    assert len(asked) == 2


async def test_what_is_not_changed_and_why(hosts, tmp_path, monkeypatch):
    host = await _shop(hosts, tmp_path, ScriptedLLM([]))
    ledger = host.ledger
    leader = (await host.derive_leader("Shop site", "Lan"))["session_id"]
    child = await ledger.create_session(lanlan_name="Lan", name="part", kind="child", parent_id=host.member)

    for session_id in (leader, child.id):
        with pytest.raises(ValueError, match="not_changeable"):
            await host.change_duty(session_id, "scout")
    # Refused before the user is asked anything: a question about what is not done is no question to put.
    reader = await ledger.create_session(lanlan_name="Lan", name="look", kind="child", parent_id=host.member, preset="scout")
    commission, _created = await ledger.create(
        lanlan_name="Lan", title="fix it", dispatch_text="fix it", source="dispatch", route="task_agent"
    )
    _no_question(host, monkeypatch)
    with pytest.raises(ValueError, match="not_changeable"):
        await host.task_agents.change_duty(reader.id, "worker", by="session", commission_id=commission.id)
    # The leader's is no preset a member is given, nor is a copy's; nor one that is not there.
    for preset in ("boss", "shadow", "no-such-preset", " "):
        with pytest.raises(ValueError, match="bad_preset"):
            await host.change_duty(host.member, preset)
    with pytest.raises(LookupError):
        await host.change_duty("no-such-session", "scout")
    # The one it has: nothing happens.
    same = await host.change_duty(host.member, "general")
    assert (same["changed"], same["preset"], same["was"]) == (False, "general", "general")

    async def over(_session: Any, _now: Any = None) -> bool:
        return False

    monkeypatch.setattr(ledger, "session_alive", over)
    with pytest.raises(ValueError, match="ended"):
        await host.change_duty(host.member, "scout")
    assert (await ledger.get_session(host.member)).preset == "general"


async def test_one_with_work_in_a_worktree_that_is_not_landed_is_not_made_one_that_cannot_land_it(hosts, tmp_path):
    host = await _shop(hosts, tmp_path, ScriptedLLM([]))
    path = host.paths.worktrees_dir() / "left"
    await host.ledger.add_tree(
        id="left", session_id=host.member, project="Shop site", project_dir=str(host.workdir), path=str(path),
        workdir=str(path), state="unlanded",
    )

    with pytest.raises(ValueError, match="tree_not_landed"):
        await host.change_duty(host.member, "scout")
    assert (await host.ledger.get_session(host.member)).preset == "general"
    # To another that changes files it is changed all the same.
    assert (await host.change_duty(host.member, "worker"))["changed"]

    # Nor when the preset it has cannot be read just now (its template is gone, or wrong):
    # what it cannot do as it stands says nothing of the worktree it has from before.
    manager = host.task_agents
    template_of = manager._template_of

    async def unreadable(session: Any) -> Any:
        if session.preset == "worker":
            raise RuntimeError("the preset 'worker' cannot be used")
        return await template_of(session)

    with pytest.MonkeyPatch.context() as patched:
        patched.setattr(manager, "_template_of", unreadable)
        with pytest.raises(ValueError, match="tree_not_landed"):
            await host.change_duty(host.member, "scout")
    assert (await host.ledger.get_session(host.member)).preset == "worker"

    # One the user sees to personally is in nobody's way.
    await host.ledger.update_tree("left", state="kept")
    assert (await host.change_duty(host.member, "scout"))["changed"]


async def test_no_worktree_is_made_for_a_member_once_it_was_given_a_preset_that_cannot_change_files(hosts, tmp_path):
    from nekopower.host.ledger import SeatChanged

    host = await _shop(hosts, tmp_path, ScriptedLLM([]))
    ledger = host.ledger
    second = (await host.open_member("Lan", "Shop site", preset="general"))["session_id"]
    assert (await ledger.get_session(second)).seat == "tree"

    def tree(name: str) -> dict[str, Any]:
        path = host.paths.worktrees_dir() / name
        return {
            "id": name, "session_id": second, "project": "Shop site", "project_dir": str(host.workdir),
            "path": str(path), "workdir": str(path),
        }

    # A start of it that read its seat a while ago puts a worktree on record first: it is not
    # given a preset that could not land what is worked on in there. Looked for as the preset
    # is written, whatever was seen a moment before.
    await ledger.add_tree(seat="tree", **tree("first"))
    with pytest.raises(ValueError, match="tree_not_landed"):
        await ledger.change_preset(second, "scout", by="user", seat="", without_trees=True)
    with pytest.raises(ValueError, match="tree_not_landed"):
        await host.change_duty(second, "scout")
    # Also where it was put on record after the host looked for one, and before the preset is written.
    listed = ledger.trees

    async def none_yet(**wanted: Any) -> list[Any]:
        return [] if wanted.get("session_id") == second else await listed(**wanted)

    with pytest.MonkeyPatch.context() as patched:
        patched.setattr(ledger, "trees", none_yet)
        with pytest.raises(ValueError, match="tree_not_landed"):
            await host.change_duty(second, "scout")
    kept = await ledger.get_session(second)
    assert (kept.preset, kept.seat) == ("general", "tree")
    await ledger.drop_tree("first")

    # Given that preset first, whatever it could do before (here its own cannot be read):
    # the start that read its seat before finds it has that seat no more, and makes none.
    manager = host.task_agents
    template_of = manager._template_of

    async def unreadable(session: Any) -> Any:
        if session.preset == "general" and session.id == second:
            raise RuntimeError("the preset 'general' cannot be used")
        return await template_of(session)

    with pytest.MonkeyPatch.context() as patched:
        patched.setattr(manager, "_template_of", unreadable)
        changed = await host.change_duty(second, "scout")
    assert (changed["preset"], changed["seat"]) == ("scout", "")
    with pytest.raises(SeatChanged):
        await ledger.add_tree(seat="tree", **tree("second"))
    assert await ledger.trees(session_id=second) == []
    # (One that is made for no seat in particular, as what an earlier process left is put on record, is.)
    await ledger.add_tree(**tree("found"))


# ---------------------------------------------------------------------------
# What it costs
# ---------------------------------------------------------------------------


def test_what_a_session_is_told_of_a_change_stays_small():
    from utils.tokenize import count_tokens

    # Once for a change, with the first message of the start after it. Measured 54, and in
    # dsh, with the longest role a session there can have (the worker's), 136.
    assert count_tokens(presets.PRESET_CHANGED.format(was="inspector", now="planner")) <= 56
    with_role = presets.PRESET_CHANGED_IN_DSH.format(was="inspector", now="worker", role=presets.PRESET_BODIES["worker"])
    assert count_tokens(with_role) <= 138
    without = presets.PRESET_CHANGED_IN_DSH.format(was="worker", now="general", role=presets.PRESET_CHANGED_NO_ROLE)
    assert count_tokens(without) <= 56
