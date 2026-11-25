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

"""Another member beside a busy one, in a project without a leader (``TaskAgentManager._beside``).

Work that is plainly about something else than what the project's member
is at need not wait for it: the user is asked whether another member is to
do it in a git worktree of its own (or one is opened without asking, or it
waits as before, by what the user set). No member is made until the user
allows it; refused, taken back or stopped, the commission is where it was.
The worktrees are the stand-ins of ``test_host_tree_work.py``.
"""

from __future__ import annotations

import asyncio
import dataclasses
import json
import types
from pathlib import Path
from typing import Any

import pytest

from config import nekopower_settings as settings
from nekopower.host.landing_settings import LandingStore
from tests.unit.nekopower.fakes import ScriptedLLM
from tests.unit.nekopower.host_fakes import FakeDecisionLLM, let_go, outbox, scripts_by_commission, wait_until
from tests.unit.nekopower.test_host_tree_work import (
    FakeTrees,
    _dispatch,
    _events,
    _host,
    _report,
    _said_to,
    _session_of,
    _write,
    wait_status,
)

pytestmark = pytest.mark.unit

FIRST = "fix the header of the shop site"
# About something else than the header: none of its words is in what the member is at.
APART = "repair the broken links in the footer on every page of the shop site"
# Apart as well, and from the one above.
APART_TOO = "write an invoice template with tax rows for the accounting of the shop site"
# Goes on with what the member is at: it shares "header" and "fix" with it.
ALONG = "fix the logo in the header of the shop site as well and make that header sticky"
# Says too little of its own to be told apart.
SHORT = "do the footer too in the shop site"


@pytest.fixture(autouse=True)
def _one_after_the_other(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("config.nekopower_settings.TASK_AGENT_FORK_CONTINUE_ENABLED", False)
    monkeypatch.setattr("config.nekopower_settings.TASK_AGENT_REVIEW_ENABLED", False)
    # As shipped: a project in a git repository has no leader.
    monkeypatch.setattr(settings, "LEADER_IN_GIT_REPOSITORIES", False)


@pytest.fixture
async def hosts() -> Any:
    made: list[Any] = []
    yield made
    for host in made:
        await host.shutdown()


def _gate() -> tuple[asyncio.Event, Any]:
    release = asyncio.Event()

    async def wait() -> None:
        await release.wait()

    return release, wait


async def _busy(
    hosts: list[Any], tmp_path: Path, more: dict[str, ScriptedLLM] | None = None, first: Any = None, **kwargs: Any
) -> tuple[Any, FakeTrees, Path, Any, dict[str, ScriptedLLM], list[str]]:
    """The shop, with no leader, and its member at work on the header until ``host.release`` is set.

    ``more`` are the scripts of what is dispatched after, by words of
    theirs; the last that is returned are those words in the order the
    commissions were first worked on. ``first``, given the host, is done
    once the project is there and before anything is dispatched.
    """
    release, wait = _gate()
    scripts = {"fix the header": ScriptedLLM([[wait, *_report("header fixed")]]), **(more or {})}
    seen: list[str] = []
    host, trees = _host(hosts, tmp_path, scripts_by_commission(scripts, seen), **kwargs)
    workdir = tmp_path / "Shop site-files"
    workdir.mkdir()
    await host.space.touch_project("Shop site", workdir=str(workdir))
    host.release = release
    if first is not None:
        await first(host)
    one = await _dispatch(host, FIRST)
    await wait_until(lambda: len(scripts["fix the header"].requests) >= 1, what="the member being at work")
    return host, trees, workdir, one, scripts, seen


async def _filed(host: Any, text: str, project: str | None = "Shop site") -> Any:
    """A commission (of the shop, unless said otherwise) handed to a task agent without her; returns what ``_dispatch`` does of it.

    (Not dispatched: how a sentence said while other commissions are under
    way is read, as a stop, more said to one of them, or new work, is not
    what these tests are about.)
    """
    commission, _created = await host.ledger.create(
        lanlan_name="Lan", title=text, dispatch_text=text, user_text=text, source="dispatch", project=project,
        lang="en",
    )
    await host.task_agents.submit(commission)
    return types.SimpleNamespace(commission_id=commission.id)


def _links(*turns: list[Any]) -> dict[str, ScriptedLLM]:
    return {"broken links": ScriptedLLM(list(turns) or [_write("links.txt", "repaired"), _report("links repaired")])}


async def _asked(host: Any, commission_id: str) -> dict[str, Any]:
    """Wait until the user is asked about a commission, and return what is asked."""
    commission = await wait_status(host, commission_id, "waiting_user")
    return commission.question()


async def _settled(host: Any) -> None:
    """Wait until nobody looks at a commission for another member any more."""
    manager = host.task_agents
    await wait_until(lambda: not manager._starting and not manager._asking_beside, what="the look being through")


async def _members(host: Any) -> list[Any]:
    return await host.ledger.members("Lan", "Shop site")


async def _settled_look(host: Any, commission_id: str) -> None:
    """Wait until the look at a commission that was held (``_hold_the_look``) is through, asked or not."""
    manager = host.task_agents
    await wait_until(
        lambda: manager.landing.get.looks == 0 and commission_id not in manager._asking_beside, what="the look being through"
    )


async def _let_go_of(host: Any, commission_id: str) -> None:
    """Wait until a commission that is over has let go of its session, and have places given out once more after that.

    Letting go gives places out anew in a task of its own, which nothing
    here can wait for by itself (what looks at a commission the user is
    asked about is a task of the same kind, and is not over): so it is
    done once more here, to the end, whatever that task has got to.
    """
    manager = host.task_agents
    await wait_until(
        lambda: commission_id not in manager._held_by.values() and commission_id not in manager._sessions,
        what="its session being let go",
    )
    await manager._start_next()


def _hold_the_look_at_its_end(host: Any) -> asyncio.Event:
    """Hold what looks at a commission for another member at the last thing it reads, until the event returned is set."""
    go = asyncio.Event()
    gate = host.task_agents._permissions
    forbidden = gate.forbidden

    async def held(category: str, key: str | None = None) -> bool:
        held.looks += 1
        try:
            await go.wait()
            return await forbidden(category, key)
        finally:
            held.looks -= 1

    held.looks = 0
    gate.forbidden = held
    return go


def _hold_the_look(host: Any) -> asyncio.Event:
    """Hold what looks at a commission for another member at its first step, until the event returned is set."""
    go = asyncio.Event()
    read = host.task_agents.landing.get

    async def held() -> dict[str, Any]:
        held.looks += 1
        try:
            await go.wait()
            return await read()
        finally:
            held.looks -= 1

    held.looks = 0
    host.task_agents.landing.get = held
    return go


# ---------------------------------------------------------------------------
# Asked, and what the answer comes to
# ---------------------------------------------------------------------------


async def test_work_apart_from_what_a_busy_member_is_at_is_asked_about_and_done_by_another_beside_it(hosts, tmp_path):
    host, trees, workdir, one, scripts, _seen = await _busy(hosts, tmp_path, _links())
    main = await _session_of(host, one.commission_id)
    assert (main.kind, main.preset, main.seat) == ("line", "general", "")

    two = await _dispatch(host, APART)
    question = await _asked(host, two.commission_id)

    # Asked as for any task agent that changes files opened beside the ones a project has, with which it would be.
    assert (question["kind"], question["category"], question["options"]) == (
        "permission", "open_session", ["always", "session", "once", "deny"],
    )
    assert question["detail"] == {
        "tool": "beside",
        "title": APART,
        "preset": "general",
        "tree": True,
        "member": "Shop site/general",
        "project": "Shop site",
        "beside": main.name,
        "beside_session": main.id,
        "at_hand": FIRST,
    }
    (put,) = await outbox(host, "question")
    assert put.payload["task_id"] == two.commission_id and "copy of its own" in put.payload["text"]
    # (What is allowed says where it works should no copy be to be had by the time it starts.)
    assert "in the project folder itself, in its turn, where no copy can be made" in put.payload["text"]
    # It waits for the user where it stood in the queue, at no session, and was never at work.
    waiting = await host.ledger.get(two.commission_id)
    assert (waiting.session_id, waiting.started_at) == (None, None)
    assert host.task_agents.queued == [two.commission_id]
    (waits,) = [item for item in (await host.waits(project="Shop site"))["waits"] if item["kind"] == "user"]
    assert (waits["waiter"]["commission_id"], waits["waiter"]["session_id"]) == (two.commission_id, None)
    # It takes no place while that is asked, should whoever gives places out find its session let go a moment
    # before what is asked is taken back.
    manager = host.task_agents
    held = manager._held_by.pop(main.id)
    assert not manager._session_free_for(two.commission_id) and not manager._take_place(two.commission_id)
    manager._held_by[main.id] = held
    # Nothing is made before the user says so, and the busy member is not the one that waits.
    assert [member.id for member in await _members(host)] == [main.id]
    assert (await host.ledger.get_session(main.id)).status == "running"
    assert scripts["broken links"].requests == [] and trees.made == []

    assert await host.answer(two.commission_id, "yes", choice="once")
    await wait_until(lambda: len(scripts["broken links"].requests) >= 2, what="the other member having written")

    # A member of its own, which works in a worktree beside the first: that one is still at its first step.
    other = await _session_of(host, two.commission_id)
    assert other.id != main.id and len(await _members(host)) == 2
    assert (other.kind, other.preset, other.seat, other.name, other.duty) == (
        "line", "general", "tree", "Shop site/general", APART,
    )
    assert (other.project, other.workdir) == ("Shop site", str(workdir))
    assert len(scripts["fix the header"].requests) == 1 and host.task_agents.queued == []
    (tree,) = await host.ledger.trees()
    assert (tree.commission_id, tree.session_id) == (two.commission_id, other.id)
    assert (Path(tree.workdir) / "links.txt").read_text() == "repaired" and not (workdir / "links.txt").exists()
    assert await _events(host, two.commission_id, "beside_opened") == [
        {"session": other.id, "beside": main.id, "asked": True}
    ]
    assert await _events(host, two.commission_id, "beside_declined") == []
    # On record as started only now, and by no way round through ``working``.
    moves = [(move["from"], move["to"]) for move in await _events(host, two.commission_id, "status")]
    assert moves[:3] == [("queued", "waiting_user"), ("waiting_user", "queued"), ("queued", "working")]
    assert (await host.ledger.get(two.commission_id)).started_at is not None
    assert host.task_agents._asking_beside == {} and host.task_agents._direct_questions == {}

    host.release.set()
    await wait_status(host, one.commission_id, "done")
    done = await wait_status(host, two.commission_id, "done")
    assert (workdir / "links.txt").read_text() == "repaired" and done.session_id == other.id
    assert host.task_agents._apart == {} and host.task_agents._handing_back_to == {}


async def test_refused_it_waits_for_the_busy_member_where_it_stood_and_no_member_is_made(hosts, tmp_path):
    host, trees, workdir, one, scripts, seen = await _busy(
        hosts, tmp_path, {**_links(), "logo": ScriptedLLM([_report("logo fixed")])}
    )
    main = await _session_of(host, one.commission_id)
    two = await _dispatch(host, APART)
    await _asked(host, two.commission_id)
    three = await _filed(host, ALONG)

    assert await host.answer(two.commission_id, "no", choice="deny")
    await _settled(host)

    back = await host.ledger.get(two.commission_id)
    assert (back.status, back.session_id, back.started_at, back.pending_question) == ("queued", main.id, None, None)
    assert await _events(host, two.commission_id, "beside_declined") == [{"beside": main.id}]
    assert await _events(host, two.commission_id, "beside_opened") == []
    # Still before what was handed over after it.
    assert host.task_agents.queued == [two.commission_id, three.commission_id]
    assert [member.id for member in await _members(host)] == [main.id] and trees.made == []
    assert scripts["broken links"].requests == []

    host.release.set()
    done = await wait_status(host, two.commission_id, "done")
    await wait_status(host, three.commission_id, "done")
    # In the member it waited for, in the project's directory, in its turn.
    assert done.session_id == main.id and (workdir / "links.txt").read_text() == "repaired"
    assert seen == ["fix the header", "broken links", "logo"]
    assert await host.ledger.trees() == [] and trees.made == []


async def test_a_member_that_comes_free_first_takes_it_and_what_was_asked_is_taken_back(hosts, tmp_path):
    host, trees, _workdir, one, _scripts, seen = await _busy(
        hosts, tmp_path, {**_links(), "logo": ScriptedLLM([_report("logo fixed")])}
    )
    main = await _session_of(host, one.commission_id)
    two = await _dispatch(host, APART)
    question = await _asked(host, two.commission_id)
    # What goes on with the member's work waits behind it, and is not asked about.
    three = await _filed(host, ALONG)
    assert (await host.ledger.get(three.commission_id)).status == "queued"

    host.release.set()
    done = await wait_status(host, two.commission_id, "done")
    await wait_status(host, three.commission_id, "done")

    # In the member itself, before what was handed over after it; nothing says the user refused.
    assert done.session_id == main.id and seen == ["fix the header", "broken links", "logo"]
    moves = [(move["from"], move["to"], move["reason"]) for move in await _events(host, two.commission_id, "status")]
    assert moves[:3] == [
        ("queued", "waiting_user", "waiting for the user"),
        ("waiting_user", "queued", "what was asked was taken back"),
        ("queued", "working", moves[2][2]),
    ]
    assert await _events(host, two.commission_id, "beside_declined") == []
    assert await _events(host, two.commission_id, "beside_opened") == []
    assert [member.id for member in await _members(host)] == [main.id] and trees.made == []
    # What was asked is no longer to be answered.
    assert not await host.answer(two.commission_id, "yes", choice="once", question_id=question["id"])
    await let_go(host)
    assert host.task_agents._handing_back_to == {} and host.task_agents._asking_beside == {}


async def test_stopped_while_the_user_is_asked_it_is_over_and_nothing_is_made(hosts, tmp_path):
    host, trees, _workdir, one, scripts, _seen = await _busy(hosts, tmp_path, _links())
    main = await _session_of(host, one.commission_id)
    two = await _dispatch(host, APART)
    await _asked(host, two.commission_id)

    await host.abort(two.commission_id)
    await _settled(host)

    stopped = await host.ledger.get(two.commission_id)
    assert (stopped.status, stopped.started_at) == ("cancelled", None)
    # Nothing tried to queue it again.
    assert await _events(host, two.commission_id, "transition_refused") == []
    assert host.task_agents.queued == [] and host.task_agents._direct_questions == {}
    assert not await host.answer(two.commission_id, "yes", choice="once")
    assert await _events(host, two.commission_id, "beside_declined") == []
    assert [member.id for member in await _members(host)] == [main.id] and trees.made == []
    # The member it waited for goes on as if nothing had been.
    host.release.set()
    await wait_status(host, one.commission_id, "done")
    assert scripts["broken links"].requests == [] and host.task_agents._handing_back_to == {}


async def test_new_work_said_while_the_user_is_asked_is_not_taken_for_an_answer_that_was_not_understood(hosts, tmp_path):
    from nekopower.host.decision import read_answer

    host, _trees, _workdir, one, scripts, _seen = await _busy(
        hosts, tmp_path, {**_links(), "price list": ScriptedLLM([_report("prices checked")])}
    )
    two = await _dispatch(host, APART)
    question = await _asked(host, two.commission_id)
    waiting = await host.ledger.get(two.commission_id)

    # What is asked quotes the commission: its title, its project. Sentences that share words with those and
    # say no answer are none; one that says an answer is, with the choice it says.
    for said in ("fix the login page too", "in the shop site, change the footer colour to blue", "what about the links"):
        assert read_answer(said, [waiting]) is None
    answered = read_answer("yes, go ahead with the links", [waiting])
    assert (answered.kind, answered.targets, answered.choice) == ("answer", [two.commission_id], "once")
    assert read_answer("no", [waiting]).choice == "deny"
    # Any other request for approval is read as it was: a word shared with what it asks makes a sentence its answer.
    asked = json.loads(waiting.pending_question)
    other = dataclasses.replace(
        waiting, pending_question=json.dumps({**asked, "detail": {**asked["detail"], "tool": "hand_over"}})
    )
    assert read_answer("fix the login page too", [other]).kind == "answer"

    # So new work said meanwhile is handed over as new work, and what is asked still waits.
    more = await _dispatch(host, "go through the whole price list of the shop site and round every amount")
    assert more.kind not in ("unclear_answer", "answered", "ambiguous")
    assert more.commission_id not in (None, two.commission_id)
    assert (await host.ledger.get(two.commission_id)).question()["id"] == question["id"]
    host.release.set()
    await host.abort(two.commission_id)
    await host.abort(more.commission_id)


async def test_what_is_said_to_one_that_was_stopped_is_not_taken_while_what_was_asked_is_still_taken_back(hosts, tmp_path):
    host, _trees, _workdir, _one, _scripts, _seen = await _busy(hosts, tmp_path, _links())
    manager = host.task_agents
    two = await _dispatch(host, APART)
    await _asked(host, two.commission_id)
    # Held where what was asked is taken back: at the move back among the queued, which a stopped one is refused.
    go, taking_back, transition = asyncio.Event(), asyncio.Event(), host.ledger.transition

    async def held(commission_id: str, status: str, **values: Any) -> Any:
        if commission_id == two.commission_id and status == "queued":
            taking_back.set()
            await go.wait()
        return await transition(commission_id, status, **values)

    host.ledger.transition = held
    await host.abort(two.commission_id)
    await wait_until(taking_back.is_set, what="what was asked being taken back")
    assert two.commission_id in manager._asking_beside

    # Over: nothing is given to it any more, and nothing is kept for it.
    assert not await host.tell(two.commission_id, "use relative links only")
    assert two.commission_id not in manager._notes
    go.set()
    await _settled(host)
    host.release.set()


async def test_another_member_works_where_the_commission_would_have_been_worked_on_had_it_waited(hosts, tmp_path):
    host, _trees, workdir, one, scripts, _seen = await _busy(hosts, tmp_path, _links())
    main = await _session_of(host, one.commission_id)
    # As one that was a session of its own before it became the project's: it keeps the directory it had,
    # and what waits for it is worked on there.
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    await host.ledger.update_session(main.id, workdir=str(elsewhere))
    await host.set_landing_settings(beside="open")

    two = await _dispatch(host, APART)
    await wait_until(lambda: len(scripts["broken links"].requests) >= 1, what="the other member being at work")

    # In a worktree of that directory, to be landed there: who does it changed, not where it lands.
    other = await _session_of(host, two.commission_id)
    (tree,) = await host.ledger.trees()
    assert other.id != main.id and other.workdir == str(elsewhere) != str(workdir)
    assert (tree.session_id, tree.project_dir) == (other.id, str(elsewhere))
    host.release.set()


async def test_what_is_said_to_it_while_the_user_is_asked_goes_along_with_the_work(hosts, tmp_path):
    host, _trees, _workdir, _one, scripts, _seen = await _busy(hosts, tmp_path, _links([*_report("links repaired")]))
    two = await _dispatch(host, APART)
    question = await _asked(host, two.commission_id)

    assert await host.tell(two.commission_id, "use relative links only")

    # No answer to what is asked, which still waits.
    assert (await host.ledger.get(two.commission_id)).question()["id"] == question["id"]
    assert await _events(host, two.commission_id, "beside_declined") == []
    assert await host.answer(two.commission_id, "yes", choice="once")
    await wait_until(lambda: len(scripts["broken links"].requests) >= 1, what="the other member being at work")
    # Allowed knowing what was said: another member does it, with what was said.
    assert len(await _members(host)) == 2 and len(scripts["fix the header"].requests) == 1
    host.release.set()
    await wait_status(host, two.commission_id, "done")
    assert "use relative links only" in _said_to(scripts["broken links"])


async def test_a_host_that_shuts_down_while_the_user_is_asked_does_not_wait_for_the_answer(tmp_path):
    hosts: list[Any] = []
    host, _trees, _workdir, _one, _scripts, _seen = await _busy(hosts, tmp_path, _links())
    two = await _dispatch(host, APART)
    question = await _asked(host, two.commission_id)
    # How it is moved from here on (the ledger is closed once the host has shut down).
    moves: list[tuple[str, bool]] = []
    transition = host.ledger.transition

    async def noting(commission_id: str, status: str, **values: Any) -> Any:
        moved = await transition(commission_id, status, **values)
        if commission_id == two.commission_id:
            moves.append((status, moved.applied))
        return moved

    host.ledger.transition = noting

    # The member it waits for is still busy: nothing but the shutting down takes back what is asked.
    began = asyncio.get_running_loop().time()
    await host.shutdown()

    # (Well within what a start that hangs is waited for.)
    assert asyncio.get_running_loop().time() - began < 3.0
    assert host.task_agents._asking_beside == {} and not host.task_agents._starting
    # It ends as what is queued does, and what was asked is no longer to be answered.
    assert ("failed", True) in moves and ("working", True) not in moves
    assert not await host.answer(two.commission_id, "yes", choice="once", question_id=question["id"])
    host.release.set()
    # Nor is anything looked at for another member any more (a look for a copy that ends now would have it so).
    host.task_agents._apart[two.commission_id] = "a session"
    host.task_agents._try_beside(two.commission_id)
    assert not host.task_agents._starting


async def test_allowed_with_no_place_free_it_is_next_in_the_queue_for_the_new_member(hosts, tmp_path):
    host, _trees, workdir, one, scripts, _seen = await _busy(hosts, tmp_path, _links(), max_task_agents=1)
    main = await _session_of(host, one.commission_id)
    two = await _dispatch(host, APART)
    await _asked(host, two.commission_id)

    assert await host.answer(two.commission_id, "yes", choice="once")
    await _settled(host)

    # The member's, and waiting for the one place there is.
    waiting = await host.ledger.get(two.commission_id)
    other = await host.ledger.get_session(waiting.session_id)
    assert (waiting.status, other.seat) == ("queued", "tree") and other.id != main.id
    assert host.task_agents.queued == [two.commission_id] and scripts["broken links"].requests == []

    host.release.set()
    done = await wait_status(host, two.commission_id, "done")
    assert done.session_id == other.id and (workdir / "links.txt").read_text() == "repaired"


async def test_work_apart_that_finds_the_member_free_is_done_by_it_and_nothing_is_kept_of_the_look(hosts, tmp_path):
    host, _trees, _workdir, one, _scripts, _seen = await _busy(hosts, tmp_path, _links())
    main = await _session_of(host, one.commission_id)
    host.release.set()
    await wait_status(host, one.commission_id, "done")
    await let_go(host)

    two = await _dispatch(host, APART)
    done = await wait_status(host, two.commission_id, "done")

    assert done.session_id == main.id and await outbox(host, "question") == []
    assert "waiting_user" not in [move["to"] for move in await _events(host, two.commission_id, "status")]
    assert host.task_agents._apart == {}


async def test_what_else_comes_free_does_not_take_back_what_is_asked(hosts, tmp_path):
    host, _trees, _workdir, _one, scripts, _seen = await _busy(
        hosts, tmp_path, {**_links(), "dinner": ScriptedLLM([_report("table booked")])}
    )
    two = await _dispatch(host, APART)
    question = await _asked(host, two.commission_id)

    # Something of no project is done meanwhile, in a session of its own: places are given out anew when it ends.
    other = await _filed(host, "book a table for dinner on friday", project=None)
    await wait_status(host, other.commission_id, "done")
    await _let_go_of(host, other.commission_id)

    waiting = await host.ledger.get(two.commission_id)
    assert waiting.status == "waiting_user" and waiting.question()["id"] == question["id"]
    assert host.task_agents._handing_back_to == {}
    assert await host.answer(two.commission_id, "yes", choice="once", question_id=question["id"])
    await wait_until(lambda: len(scripts["broken links"].requests) >= 1, what="the other member being at work")
    host.release.set()


async def test_nobody_is_asked_of_one_whose_member_came_free_while_it_was_looked_at(hosts, tmp_path):
    release_dinner, wait = _gate()
    host, _trees, _workdir, one, scripts, seen = await _busy(
        hosts, tmp_path, {**_links(), "dinner": ScriptedLLM([[wait, *_report("table booked")]])}, max_task_agents=1
    )
    manager = host.task_agents
    main = await _session_of(host, one.commission_id)
    # Waits for the one place there is, before the work apart, in a session of its own.
    other = await _filed(host, "book a table for dinner on friday", project=None)
    # Held once it was seen that the member is busy, before anybody is asked.
    go = _hold_the_look_at_its_end(host)
    two = await _dispatch(host, APART)
    assert manager.queued == [other.commission_id, two.commission_id]
    await wait_until(lambda: manager._permissions.forbidden.looks == 1, what="the look having got to its end")

    # The member comes free, and the place goes to what waited first: the work apart still waits, for a place only.
    host.release.set()
    await wait_status(host, one.commission_id, "done")
    await wait_until(lambda: len(scripts["dinner"].requests) >= 1, what="the other one having the place")
    assert manager.queued == [two.commission_id] and main.id not in manager._held_by
    go.set()
    await wait_until(lambda: manager._permissions.forbidden.looks == 0, what="the look going on")
    await asyncio.sleep(0.3)

    assert await outbox(host, "question") == [] and manager._asking_beside == {}
    assert "waiting_user" not in [move["to"] for move in await _events(host, two.commission_id, "status")]
    release_dinner.set()
    done = await wait_status(host, two.commission_id, "done")
    assert done.session_id == main.id and seen == ["fix the header", "dinner", "broken links"]


async def test_taken_back_before_it_is_put_nothing_is_put_to_the_user(hosts, tmp_path):
    host, _trees, _workdir, one, _scripts, _seen = await _busy(hosts, tmp_path, _links())
    manager = host.task_agents
    main = await _session_of(host, one.commission_id)
    put: list[str] = []
    question = manager._reporter.question

    async def noting(commission: Any, asked: dict[str, Any]) -> Any:
        put.append(commission.id)
        return await question(commission, asked)

    manager._reporter.question = noting
    # Whether another may be opened is read once before it is asked about (that goes through), and again as it is.
    go, read, reads = asyncio.Event(), host.permission_store.mode, 0

    async def held(category: str, key: str | None = None) -> str:
        nonlocal reads
        reads += category == "open_session"
        if category == "open_session" and reads > 1:
            await go.wait()
        return await read(category, key)

    host.permission_store.mode = held
    two = await _dispatch(host, APART)
    await wait_until(lambda: two.commission_id in manager._asking_beside, what="it being about to be asked about")

    host.release.set()
    await wait_status(host, one.commission_id, "done")
    await wait_until(lambda: manager._asking_beside[two.commission_id].withdrawn == "free", what="it being taken back")
    # (Nothing starts in the member until that is seen to, however often places are given out meanwhile.)
    assert manager.queued == [two.commission_id] and manager._handing_back_to == {main.id: 1}
    await manager._start_next()
    assert manager.queued == [two.commission_id] and manager._handing_back_to == {main.id: 1}
    go.set()
    done = await wait_status(host, two.commission_id, "done")

    assert put == [] and done.session_id == main.id
    assert await _events(host, two.commission_id, "beside_declined") == []
    await let_go(host)
    assert manager._handing_back_to == {} and manager._direct_questions == {}


async def test_stopped_while_its_member_is_being_made_it_takes_no_place(hosts, tmp_path):
    host, trees, _workdir, one, scripts, _seen = await _busy(hosts, tmp_path, _links())
    manager = host.task_agents
    main = await _session_of(host, one.commission_id)
    go, making, create = asyncio.Event(), asyncio.Event(), host.ledger.create_session

    async def held(**values: Any) -> Any:
        if values.get("seat") == "tree":
            making.set()
            await go.wait()
        return await create(**values)

    host.ledger.create_session = held
    two = await _dispatch(host, APART)
    await _asked(host, two.commission_id)
    assert await host.answer(two.commission_id, "yes", choice="once")
    await wait_until(making.is_set, what="its member being made")

    await host.abort(two.commission_id)
    go.set()
    await _settled(host)

    assert (await host.ledger.get(two.commission_id)).status == "cancelled"
    assert two.commission_id not in manager._sessions and manager.queued == []
    assert scripts["broken links"].requests == [] and trees.made == []
    # (The member that was being made is there, with nothing to do.)
    assert len(await _members(host)) == 2 and manager._held_by == {main.id: one.commission_id}
    host.release.set()


async def test_two_that_are_opened_at_once_do_not_both_take_the_last_room_for_a_member(hosts, tmp_path, monkeypatch):
    host, _trees, _workdir, one, scripts, _seen = await _busy(
        hosts, tmp_path, {**_links(), "invoice": ScriptedLLM([_report("template written")])}
    )
    manager = host.task_agents
    main = await _session_of(host, one.commission_id)
    await host.set_landing_settings(beside="open")
    monkeypatch.setattr(settings, "PROJECT_MEMBERS_MAX", 2)
    create = host.ledger.create_session

    async def slow(**values: Any) -> Any:
        # Long enough for the other one to count the members meanwhile, were it let to.
        await asyncio.sleep(0.3)
        return await create(**values)

    host.ledger.create_session = slow
    two = await _filed(host, APART)
    three = await _filed(host, APART_TOO)
    await _settled(host)

    # One member more, which is all the project may have: the other commission waits for the busy one.
    assert len(await _members(host)) == 2
    sessions = [(await host.ledger.get(item.commission_id)).session_id for item in (two, three)]
    assert sorted(session == main.id for session in sessions) == [False, True]
    opened = [len(await _events(host, item.commission_id, "beside_opened")) for item in (two, three)]
    assert sorted(opened) == [0, 1] and len(manager.queued) == 1
    host.release.set()
    await wait_status(host, two.commission_id, "done")
    await wait_status(host, three.commission_id, "done")


async def test_what_is_asked_is_taken_back_though_no_place_is_free_for_it_to_start_at(hosts, tmp_path):
    host, _trees, _workdir, one, _scripts, _seen = await _busy(hosts, tmp_path, _links())
    manager = host.task_agents
    main = await _session_of(host, one.commission_id)
    two = await _dispatch(host, APART)
    await _asked(host, two.commission_id)

    # As where the member is let go of while every place is taken by something else (its commission was
    # stopped while it waited for the user with its session closed, say): nothing can start, and places
    # are given out all the same.
    most, manager._max_concurrent = manager._max_concurrent, 1
    held = manager._held_by.pop(main.id)
    await manager._start_next()

    assert manager._asking_beside[two.commission_id].withdrawn == "free"
    await wait_until(lambda: not manager._asking_beside, what="what was asked being taken back")
    back = await host.ledger.get(two.commission_id)
    assert (back.status, back.session_id, back.status_reason) == ("queued", main.id, "what was asked was taken back")
    # It waits for a place where it stood, and starts once there is one.
    assert manager.queued == [two.commission_id] and manager._handing_back_to == {}
    manager._held_by[main.id] = held
    manager._max_concurrent = most
    host.release.set()
    done = await wait_status(host, two.commission_id, "done")
    assert done.session_id == main.id


@pytest.mark.parametrize("fails", ["once", "every_time"])
async def test_what_the_user_answered_stands_though_it_cannot_be_put_back_among_the_queued(hosts, tmp_path, monkeypatch, fails):
    monkeypatch.setattr(settings, "REST_LOOK_RETRY_DELAYS", (0.0,))
    host, trees, workdir, one, scripts, _seen = await _busy(hosts, tmp_path, _links())
    manager = host.task_agents
    main = await _session_of(host, one.commission_id)
    two = await _dispatch(host, APART)
    await _asked(host, two.commission_id)
    transition, failed = host.ledger.transition, 0

    async def failing(commission_id: str, status: str, **values: Any) -> Any:
        nonlocal failed
        if commission_id == two.commission_id and status == "queued" and (fails == "every_time" or not failed):
            failed += 1
            raise RuntimeError("the ledger cannot be written")
        return await transition(commission_id, status, **values)

    host.ledger.transition = failing
    assert await host.answer(two.commission_id, "no", choice="deny")
    await _settled(host)

    # Refused, as the user said: on record as that, and waiting for the member where it stood.
    assert await _events(host, two.commission_id, "beside_declined") == [{"beside": main.id}]
    assert manager.queued == [two.commission_id] and manager._session_of[two.commission_id] == main.id
    stands = await host.ledger.get(two.commission_id)
    if fails == "once":
        # Asked again, it was written.
        assert (failed, stands.status, stands.session_id) == (1, "queued", main.id)
    else:
        # Given up on: on record as waiting still, with no session, until it starts.
        assert (failed, stands.status, stands.session_id) == (2, "waiting_user", None)
    host.release.set()
    done = await wait_status(host, two.commission_id, "done")
    assert done.session_id == main.id and (workdir / "links.txt").read_text() == "repaired"
    assert [member.id for member in await _members(host)] == [main.id] and trees.made == []


async def test_more_said_to_it_before_another_is_opened_with_nobody_asked_keeps_it_waiting(hosts, tmp_path):
    host, trees, _workdir, one, _scripts, _seen = await _busy(hosts, tmp_path, _links())
    manager = host.task_agents
    main = await _session_of(host, one.commission_id)
    await host.set_landing_settings(beside="open")
    go = _hold_the_look(host)
    two = await _dispatch(host, APART)
    await wait_until(lambda: manager.landing.get.looks == 1, what="the look having begun")

    # Said to it while it is queued, which goes with it: what it was told apart by is not all of it any more.
    assert await host.tell(two.commission_id, "go on in the same session with what is there already")
    go.set()
    await _settled_look(host, two.commission_id)
    await _settled(host)

    assert [member.id for member in await _members(host)] == [main.id] and trees.made == []
    assert (await host.ledger.get(two.commission_id)).session_id == main.id and manager.queued == [two.commission_id]
    host.release.set()
    done = await wait_status(host, two.commission_id, "done")
    assert done.session_id == main.id


# ---------------------------------------------------------------------------
# What the user set
# ---------------------------------------------------------------------------


async def test_set_to_open_another_member_does_it_with_nobody_asked(hosts, tmp_path):
    host, _trees, _workdir, one, scripts, _seen = await _busy(hosts, tmp_path, _links())
    main = await _session_of(host, one.commission_id)
    assert (await host.set_landing_settings(beside="open"))["beside"] == "open"

    two = await _dispatch(host, APART)
    await wait_until(lambda: len(scripts["broken links"].requests) >= 2, what="the other member having written")

    other = await _session_of(host, two.commission_id)
    assert other.id != main.id and other.seat == "tree"
    assert await _events(host, two.commission_id, "beside_opened") == [
        {"session": other.id, "beside": main.id, "asked": False}
    ]
    # Never asked: it went from the queue to work.
    assert await outbox(host, "question") == []
    assert [(move["from"], move["to"]) for move in await _events(host, two.commission_id, "status")][:1] == [
        ("queued", "working")
    ]
    host.release.set()
    await wait_status(host, two.commission_id, "done")


@pytest.mark.parametrize(
    "why",
    ["set_to_queue", "forbidden", "forbidden_and_set_to_open", "no_worktree", "as_many_members_as_may_be", "goes_along", "says_little"],
)
async def test_it_waits_for_the_member_as_ever_where_no_other_is_to_be_had(hosts, tmp_path, monkeypatch, why):
    said = {"goes_along": ALONG, "says_little": SHORT}.get(why, APART)
    key = {"goes_along": "logo", "says_little": "footer too"}.get(why, "broken links")
    host, trees, workdir, one, scripts, _seen = await _busy(
        hosts, tmp_path, {key: ScriptedLLM([_write("links.txt", "repaired"), _report("links repaired")])}
    )
    main = await _session_of(host, one.commission_id)
    if why == "set_to_queue":
        await host.set_landing_settings(beside="queue")
    elif why.startswith("forbidden"):
        await host.permission_store.set_mode("open_session", "deny")
        if why.endswith("open"):
            await host.set_landing_settings(beside="open")
    elif why == "no_worktree":
        trees.unusable = "not_a_repository"
    elif why == "as_many_members_as_may_be":
        monkeypatch.setattr(settings, "PROJECT_MEMBERS_MAX", 1)

    two = await _dispatch(host, said)
    await _settled(host)

    waiting = await host.ledger.get(two.commission_id)
    assert (waiting.status, waiting.session_id) == ("queued", main.id)
    assert await outbox(host, "question") == [] and host.task_agents.queued == [two.commission_id]
    assert [member.id for member in await _members(host)] == [main.id]
    assert host.task_agents._apart == {}
    host.release.set()
    done = await wait_status(host, two.commission_id, "done")
    assert done.session_id == main.id and (workdir / "links.txt").exists() and trees.made == []


async def test_allowed_for_good_another_member_is_opened_without_asking_again(hosts, tmp_path):
    host, _trees, _workdir, one, scripts, _seen = await _busy(
        hosts, tmp_path, {**_links(), "price list": ScriptedLLM([_report("prices checked")])}
    )
    main = await _session_of(host, one.commission_id)
    two = await _dispatch(host, APART)
    await _asked(host, two.commission_id)
    assert await host.answer(two.commission_id, "always", choice="always")
    # (It said it is done, and what it changed waits for the first one to be between two steps.)
    await wait_until(lambda: two.commission_id in host.task_agents._dones, what="the second one saying it is done")
    assert (await host.permission_store.snapshot())["categories"]["open_session"] == "allow"
    second = await _session_of(host, two.commission_id)

    # Held against the member last in use, which is the one made a moment ago and still busy.
    three = await _dispatch(host, "go through the whole price list and round every amount in the shop site")
    await wait_until(lambda: len(scripts["price list"].requests) >= 1, what="a third member being at work")

    third = await _session_of(host, three.commission_id)
    assert len({main.id, second.id, third.id}) == 3 and (third.seat, third.name) == ("tree", "Shop site/general 2")
    assert await _events(host, three.commission_id, "beside_opened") == [
        {"session": third.id, "beside": second.id, "asked": False}
    ]
    # Not asked: it went from the queue to work.
    assert await outbox(host, "question") == []
    assert [move["to"] for move in await _events(host, three.commission_id, "status")][:1] == ["working"]
    host.release.set()
    await wait_status(host, three.commission_id, "done")


# ---------------------------------------------------------------------------
# Looked at again by the time it gets to it
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "meanwhile",
    ["a_leader", "the_holder_is_over", "told_to_be_a_side_question", "the_member_only_reads", "the_new_one_would_only_read"],
)
async def test_nobody_is_asked_where_it_no_longer_holds_by_the_time_it_is_looked_at(hosts, tmp_path, meanwhile):
    async def a_worker(host: Any) -> None:
        # The member the user gave the project, of another preset than the one that would be opened beside it.
        await host.open_member("Lan", "Shop site", preset="worker")

    host, trees, _workdir, one, scripts, _seen = await _busy(
        hosts, tmp_path, _links(), first=a_worker if meanwhile == "the_new_one_would_only_read" else None
    )
    manager = host.task_agents
    main = await _session_of(host, one.commission_id)
    assert main.preset == ("worker" if meanwhile == "the_new_one_would_only_read" else "general")
    go = _hold_the_look(host)
    two = await _dispatch(host, APART)
    await wait_until(lambda: manager._starting, what="the look having begun")

    if meanwhile == "a_leader":
        assert (await host.derive_leader("Shop site", "Lan"))["made"] is True
    elif meanwhile == "the_holder_is_over":
        # Over on record, its session not yet let go of.
        assert (await host.ledger.transition(one.commission_id, "blocked", reason="stuck")).applied
        assert manager._held_by.get(main.id) == one.commission_id
    elif meanwhile == "told_to_be_a_side_question":
        manager._side_said[two.commission_id] = ("", (), True)
    else:
        template_of, resolve = manager._template_of, manager.templates.resolve

        async def reads(kept: Any) -> Any:
            return dataclasses.replace(await template_of(kept), changes_files=False)

        def reading(preset: str, project: Any = None) -> Any:
            found = resolve(preset, project)
            return dataclasses.replace(found, changes_files=False) if preset == "general" else found

        if meanwhile == "the_member_only_reads":
            manager._template_of = reads
        else:
            manager.templates.resolve = reading
    go.set()
    await wait_until(lambda: not manager._asking_beside and two.commission_id not in manager._apart, what="the look")
    await asyncio.sleep(0.2)

    assert await outbox(host, "question") == [] and manager._asking_beside == {}
    assert (await host.ledger.get(two.commission_id)).status == "queued"
    assert len([member for member in await _members(host) if member.kind == "line"]) == 1 and trees.made == []
    host.release.set()


@pytest.mark.parametrize("when", ["right_after_the_first_look", "while_the_member_is_named"])
async def test_forbidden_while_it_is_looked_at_no_member_is_opened_with_nobody_asked(hosts, tmp_path, when):
    host, trees, _workdir, one, _scripts, _seen = await _busy(hosts, tmp_path, _links())
    main = await _session_of(host, one.commission_id)
    await host.set_landing_settings(beside="open")
    gate = host.task_agents._permissions
    forbidden, looks = gate.forbidden, 0
    member_name, named = host.ledger.member_name, 0

    async def then_denied(category: str, key: str | None = None) -> bool:
        nonlocal looks
        looks += 1
        found = await forbidden(category, key)
        if looks == 1 and when == "right_after_the_first_look":
            # Allowed when it was first looked at, and forbidden right after.
            await host.permission_store.set_mode("open_session", "deny")
        return found

    async def naming(lanlan_name: str, project: str, wanted: str) -> str:
        nonlocal named
        named += 1
        if named == 2 and when == "while_the_member_is_named":
            # Forbidden while the member that is about to be made is named: the last thing read before it is.
            await host.permission_store.set_mode("open_session", "deny")
        return await member_name(lanlan_name, project, wanted)

    gate.forbidden = then_denied
    host.ledger.member_name = naming
    two = await _dispatch(host, APART)
    await _settled(host)

    assert looks == 2 and [member.id for member in await _members(host)] == [main.id] and trees.made == []
    assert (await host.ledger.get(two.commission_id)).session_id == main.id
    host.release.set()


async def test_what_a_member_is_at_is_read_whole_however_many_commissions_ended_in_it_since(hosts, tmp_path, monkeypatch):
    host, _trees, _workdir, one, _scripts, _seen = await _busy(hosts, tmp_path, _links())
    main = await _session_of(host, one.commission_id)
    # Of each session the latest commission only is read for what it did: one that ended after the one it is at began.
    monkeypatch.setattr(settings, "ORCHESTRATOR_COMMISSIONS_EACH", 1)
    # One that waits for it, with words of the user's that her dispatch does not have; and one that ended after both.
    waiting, _created = await host.ledger.create(
        lanlan_name="Lan", title="banner", dispatch_text="redo the banner", user_text="paint the banner teal please",
        source="dispatch", project="Shop site", lang="en",
    )
    await host.ledger.update(waiting.id, session_id=main.id)
    later, _created = await host.ledger.create(
        lanlan_name="Lan", title="tidy the readme", dispatch_text="tidy the readme", source="dispatch",
        project="Shop site", lang="en",
    )
    await host.ledger.update(later.id, session_id=main.id)
    assert (await host.ledger.transition(later.id, "failed", reason="given up")).applied
    asked, _created = await host.ledger.create(
        lanlan_name="Lan", title=ALONG, dispatch_text=ALONG, source="dispatch", project="Shop site", lang="en"
    )

    standing = await host.task_agents.families.standing(asked)

    member = standing.agent(main.id)
    assert [done.title for done in member.done] == ["tidy the readme"]
    # What it is at is among what a sentence is held against, though it is not among the latest read:
    # the title, what she dispatched and what the user said, of each commission under way in it.
    assert FIRST in member.at_hand
    assert {"banner", "redo the banner", "paint the banner teal please"} <= set(member.at_hand)
    host.release.set()


@pytest.mark.parametrize("meanwhile", ["as_many_members_as_may_be", "switched_off", "forbidden", "a_leader"])
async def test_allowed_after_a_while_nothing_is_made_where_it_no_longer_holds(hosts, tmp_path, monkeypatch, meanwhile):
    host, trees, _workdir, one, scripts, _seen = await _busy(hosts, tmp_path, _links())
    main = await _session_of(host, one.commission_id)
    two = await _dispatch(host, APART)
    await _asked(host, two.commission_id)

    if meanwhile == "as_many_members_as_may_be":
        monkeypatch.setattr(settings, "PROJECT_MEMBERS_MAX", 1)
    elif meanwhile == "forbidden":
        # Set since it was asked: that wins over the answer, and is no refusal by the user.
        await host.permission_store.set_mode("open_session", "deny")
    elif meanwhile == "a_leader":
        # The project has a leader by now, which says who does what.
        assert (await host.derive_leader("Shop site", "Lan"))["made"] is True
    else:
        host.task_agents._switched_on = lambda: False
    assert await host.answer(two.commission_id, "yes", choice="once")
    await _settled(host)

    # Where it was: queued for the member, and not on record as refused.
    back = await host.ledger.get(two.commission_id)
    assert (back.status, back.session_id) == ("queued", main.id)
    assert [member.id for member in await _members(host) if member.kind == "line"] == [main.id] and trees.made == []
    assert await _events(host, two.commission_id, "beside_declined") == []
    assert await _events(host, two.commission_id, "beside_opened") == []
    host.task_agents._switched_on = None
    host.release.set()


async def test_a_member_that_cannot_be_made_leaves_it_waiting_for_the_one_it_waited_for(hosts, tmp_path):
    host, trees, workdir, one, scripts, _seen = await _busy(hosts, tmp_path, _links())
    main = await _session_of(host, one.commission_id)
    await host.set_landing_settings(beside="open")
    update = host.ledger.update

    async def failing(commission_id: str, **values: Any) -> Any:
        if values.get("session_id") not in (None, main.id):
            raise RuntimeError("the ledger cannot be written")
        return await update(commission_id, **values)

    host.ledger.update = failing
    two = await _dispatch(host, APART)
    await _settled(host)

    assert (await host.ledger.get(two.commission_id)).session_id == main.id
    assert host.task_agents.queued == [two.commission_id] and host.task_agents._session_of[two.commission_id] == main.id
    # (The member that was made is there, with nothing to do: it counts among those the project may have.)
    assert len(await _members(host)) == 2
    host.ledger.update = update
    host.release.set()
    done = await wait_status(host, two.commission_id, "done")
    assert done.session_id == main.id and (workdir / "links.txt").exists()


# ---------------------------------------------------------------------------
# After the look for a copy to carry it on in
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("side_question", [False, True])
async def test_it_is_looked_at_for_another_member_only_once_it_is_not_carried_on_in_a_copy(
    hosts, tmp_path, monkeypatch, side_question
):
    monkeypatch.setattr("config.nekopower_settings.TASK_AGENT_FORK_CONTINUE_ENABLED", True)
    decisions = FakeDecisionLLM()
    decisions.side = [{"side_question": side_question}]
    host, trees, _workdir, one, scripts, _seen = await _busy(
        hosts, tmp_path, _links([*_report("links looked at")]), decision_llm=decisions
    )
    main = await _session_of(host, one.commission_id)

    two = await _dispatch(host, APART)

    if side_question:
        # Answered by looking, in a copy of the member's session: nobody is asked, and no member is made.
        done = await wait_status(host, two.commission_id, "done")
        assert (await host.ledger.get_session(done.session_id)).kind == "fork"
        assert await outbox(host, "question") == []
        assert "waiting_user" not in [move["to"] for move in await _events(host, two.commission_id, "status")]
        assert [member.id for member in await _members(host)] == [main.id]
    else:
        # Work: it is not carried on, and the user is asked. Whether it is a side question was asked once, for the copy.
        question = await _asked(host, two.commission_id)
        assert question["detail"]["tool"] == "beside"
    assert len(decisions.side_calls) == 1
    host.release.set()


async def test_nobody_is_asked_while_it_is_still_told_whether_it_is_a_side_question(hosts, tmp_path, monkeypatch):
    monkeypatch.setattr("config.nekopower_settings.TASK_AGENT_FORK_CONTINUE_ENABLED", True)
    host, _trees, _workdir, one, _scripts, _seen = await _busy(hosts, tmp_path, _links([*_report("links looked at")]))
    manager = host.task_agents
    main = await _session_of(host, one.commission_id)
    told, asked = asyncio.Event(), asyncio.Event()

    async def slow(_dispatched: str, _said: str, _waiting_for: str) -> bool:
        asked.set()
        await told.wait()
        return True

    manager.set_side_question_judge(slow)
    two = await _dispatch(host, APART)
    await wait_until(asked.is_set, what="it being asked whether it is a side question")
    await asyncio.sleep(0.3)

    # Telling takes a model a while: the commission waits meanwhile, and the user is asked nothing.
    assert (await host.ledger.get(two.commission_id)).status == "queued"
    assert await outbox(host, "question") == [] and manager._asking_beside == {}
    told.set()
    done = await wait_status(host, two.commission_id, "done")
    assert (await host.ledger.get_session(done.session_id)).kind == "fork"
    assert "waiting_user" not in [move["to"] for move in await _events(host, two.commission_id, "status")]
    assert [member.id for member in await _members(host)] == [main.id]
    host.release.set()


# ---------------------------------------------------------------------------
# The setting, and the status it waits in
# ---------------------------------------------------------------------------


async def test_what_becomes_of_work_beside_a_busy_member_is_a_setting_kept_with_the_others(hosts, tmp_path):
    host, _trees = _host(hosts, tmp_path, ScriptedLLM([]))
    most = settings.WORKTREES_PER_PROJECT

    assert (await host.landing_settings())["beside"] == "ask"
    assert await host.set_landing_settings(beside="queue") == {
        "mode": "auto", "inspect": False, "beside": "queue", "worktrees_per_project": most,
    }
    # The others stay as they are when one is set, and it stays when another is.
    assert (await host.set_landing_settings(mode="ask"))["beside"] == "queue"
    with pytest.raises(ValueError, match="bad_beside"):
        await host.set_landing_settings(beside="sometimes")
    assert (await host.landing_settings())["beside"] == "queue"

    # Read from the file by the next process; what is no choice there is read as nothing set.
    path = host.paths.landing_json()
    assert json.loads(Path(path).read_text(encoding="utf-8"))["beside"] == "queue"
    assert (await LandingStore(path).get())["beside"] == "queue"
    Path(path).write_text(json.dumps({"version": 1, "mode": "ask", "beside": "never"}), encoding="utf-8")
    assert await LandingStore(path).get() == {"mode": "ask", "inspect": False, "beside": "ask"}


async def test_a_queued_commission_waits_for_the_user_without_being_put_down_as_started(hosts, tmp_path):
    host, _trees = _host(hosts, tmp_path, ScriptedLLM([]))
    commission, _created = await host.ledger.create(
        lanlan_name="Lan", title="links", dispatch_text="links", user_text="links", source="dispatch", lang="en"
    )

    asked = await host.ledger.transition(commission.id, "waiting_user", pending_question='{"kind": "permission"}')
    back = await host.ledger.transition(commission.id, "queued")

    assert asked.applied and back.applied
    assert (back.commission.status, back.commission.started_at, back.commission.pending_question) == ("queued", None, None)
