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


async def test_a_change_of_preset_is_written_with_what_the_conversation_was_under_and_an_event_of_a_commission(
    hosts, tmp_path
):
    ledger = _host(hosts, tmp_path, ScriptedLLM([])).ledger
    session = await ledger.create_session(lanlan_name="Lan", name="photos", kind="single")

    # Never opened, and no commission was worked on in it: the preset, and nothing else.
    kept, was, on = await ledger.change_preset(session.id, "scout", by="user")
    assert (kept.preset, was, on, kept.preset_told) == ("scout", "general", None, "")

    async def filed(title: str, status: str | None = None) -> Any:
        commission, _created = await ledger.create(
            lanlan_name="Lan", title=title, dispatch_text=title, source="dispatch", route="task_agent", session_id=session.id
        )
        if status is not None:
            for step in ("working", status):
                moved = await ledger.transition(commission.id, step, reason="test", result_summary=title)
                assert moved.applied, moved.refusal
        return commission

    await filed("one", "done")
    under_way = await filed("two")
    await filed("three", "cancelled")
    await ledger.update_session(session.id, session_dir="D:/sessions/photos")

    kept, was, on = await ledger.change_preset(session.id, "worker", by="user", was="scout")
    # With the commission under way in it, not the one filed last, which is over.
    assert (kept.preset, was, on, kept.preset_told) == ("worker", "scout", under_way.id, "scout")
    assert [event.payload for event in await ledger.events(under_way.id) if event.type == "duty_changed"] == [
        {"session": session.id, "from": "scout", "to": "worker", "by": "user"}
    ]
    # Changed again before its task agent was started: what it was last under stays.
    kept, _was, _on = await ledger.change_preset(session.id, "planner", by="session")
    assert (kept.preset, kept.preset_told) == ("planner", "scout")
    # Back at that one: nothing to be told of.
    kept, _was, _on = await ledger.change_preset(session.id, "scout", by="host")
    assert (kept.preset, kept.preset_told) == ("scout", "")

    recorded = [event for event in await ledger.events(under_way.id) if event.type == "duty_changed"]
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
    assert [event for event in await ledger.events(under_way.id) if event.type == "duty_changed"] == recorded
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


async def test_what_a_session_was_told_is_noted_and_a_change_since_is_still_to_be_told(hosts, tmp_path):
    ledger = _host(hosts, tmp_path, ScriptedLLM([])).ledger
    session = await ledger.create_session(lanlan_name="Lan", name="photos", kind="line", session_dir="D:/sessions/photos")
    await ledger.change_preset(session.id, "scout", by="user")

    await ledger.preset_was_told(session.id, "scout")
    assert (await ledger.get_session(session.id)).preset_told == ""

    # Told of one, and changed to another while that start was under way.
    await ledger.change_preset(session.id, "planner", by="user")
    await ledger.change_preset(session.id, "inspector", by="user")
    await ledger.preset_was_told(session.id, "planner")
    assert (await ledger.get_session(session.id)).preset_told == "planner"
    # One nothing is kept for is left as it is.
    other = await ledger.create_session(lanlan_name="Lan", name="other", kind="line")
    await ledger.preset_was_told(other.id, "scout")
    assert (await ledger.get_session(other.id)).preset_told == ""


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
    assert [spec.renew_head for spec in specs] == [False, True]
    assert {"write", "edit", "pwsh", "bash", "capability_call", "subagent"} <= set(specs[1].disabled_tools)
    before, after = llm.requests[1], llm.requests[2]
    # Its system prompt and its tools are the new preset's, in the conversation it had.
    assert "Your role: scout." in _system(after) and "Your role:" not in _system(before)
    assert presets.PRESET_ROSTER_INTRO in _system(before) and presets.PRESET_ROSTER_INTRO not in _system(after)
    assert "write" in _tools(llm.overrides[1]) and not {"write", "edit", "subagent"} & _tools(llm.overrides[2])
    assert "header" in _said(after) and _said(after).count(CHANGED) == 1 and CHANGED not in _said(before)
    kept = await host.ledger.get_session(host.member)
    assert read_snapshot(Path(kept.session_dir)).reason == REASON_PRESET
    # Told, and its requests begin as its preset has them: nothing is kept to tell.
    assert kept.preset_told == ""
    assert (await host.board())["projects"][0]["members"][0]["preset_was"] is None

    await _done(host, "look at the fonts of the shop site")

    assert [spec.renew_head for spec in specs] == [False, True, False]
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

    assert [spec.renew_head for spec in specs] == [False, True] and "write" in specs[1].disabled_tools
    assert _said(llm.requests[2]).count(CHANGED) == 1
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
    assert [spec.renew_head for spec in specs] == [False]
    assert "Your role: scout." in _system(llm.requests[0]) and "preset was changed" not in _said(llm.requests[0])


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

    # One the user sees to personally is in nobody's way.
    await host.ledger.update_tree("left", state="kept")
    assert (await host.change_duty(host.member, "scout"))["changed"]


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
