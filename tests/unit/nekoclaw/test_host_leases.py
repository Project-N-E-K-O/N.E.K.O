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

"""Which commission occupies the desktop and the browser, and when the host lets go of them."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from nekoclaw.host.capabilities import (
    BUILTIN_GROUP_BROWSER,
    BUILTIN_GROUP_KEYBOARD_MOUSE,
    BuiltinOutcome,
    builtin_tool,
)
from nekoclaw.host.leases import BuiltinLeases, Occupation
from tests.unit.nekoclaw.host_fakes import FakeDecisionLLM, make_host, wait_until

BROWSER = BUILTIN_GROUP_BROWSER
DESKTOP = BUILTIN_GROUP_KEYBOARD_MOUSE
# Long enough that a test which expects to be woken never runs into it.
NEVER = 60.0


class Heard:
    """An activity listener that writes down what it is told."""

    def __init__(self) -> None:
        self.events: list[tuple[str, str, str]] = []

    def __call__(self, group: str, state: str, team: str) -> None:
        self.events.append((group, state, team))


class Cleanups:
    """An ``on_released`` callback that finishes a moment after it is called."""

    def __init__(self) -> None:
        self.done: list[tuple[str, str]] = []

    async def __call__(self, group: str, team: str) -> None:
        await asyncio.sleep(0)
        self.done.append((group, team))


def _leases(parents: dict[str, str] | None = None, **kwargs: Any) -> BuiltinLeases:
    """Leases whose teams are read from ``parents`` (child -> the commission that started it)."""
    started_by = parents or {}

    async def root_of(commission_id: str) -> str:
        current = commission_id
        while current in started_by:
            current = started_by[current]
        return current

    kwargs.setdefault("wait_seconds", 0.05)
    return BuiltinLeases(root_of=root_of, **kwargs)


async def _waiting(leases: BuiltinLeases, group: str, commission_id: str, **kwargs: Any) -> "asyncio.Task[Any]":
    """Start an ``acquire`` that has to wait, and return it once it waits."""
    before = len(leases._waiters)
    task = asyncio.ensure_future(leases.acquire(group, commission_id, **kwargs))
    await wait_until(lambda: len(leases._waiters) > before or task.done(), what="the commission waiting for the group")
    assert not task.done(), "it was expected to wait"
    return task


# ---------------------------------------------------------------------------
# One group, one team at a time
# ---------------------------------------------------------------------------


async def test_the_first_commission_gets_the_group_at_once_and_the_listener_hears_it():
    heard = Heard()
    leases = _leases(on_activity=heard)

    in_the_way = await leases.acquire(BROWSER, "c1", title="book the flight")

    assert in_the_way is None
    assert leases.holder(BROWSER) == Occupation(team="c1", title="book the flight", members={"c1"})
    assert leases.occupied() == {BROWSER: "c1"}
    assert heard.events == [(BROWSER, "started", "c1")]


async def test_a_group_nobody_uses_has_no_holder():
    leases = _leases()

    assert leases.holder(BROWSER) is None
    assert leases.occupied() == {}


async def test_the_desktop_and_the_browser_are_occupied_apart():
    leases = _leases()

    assert await leases.acquire(BROWSER, "c1") is None
    assert await leases.acquire(DESKTOP, "c2") is None

    assert leases.occupied() == {BROWSER: "c1", DESKTOP: "c2"}


async def test_a_commission_of_another_team_waits_and_gets_the_group_when_the_first_lets_go():
    heard = Heard()
    leases = _leases(on_activity=heard, wait_seconds=NEVER)
    await leases.acquire(BROWSER, "c1", title="first")

    second = await _waiting(leases, BROWSER, "c2", title="second")
    assert leases.holder(BROWSER).team == "c1"
    freed = leases.release("c1")

    assert freed == [BROWSER]
    assert await asyncio.wait_for(second, 5) is None
    assert leases.holder(BROWSER) == Occupation(team="c2", title="second", members={"c2"})
    assert heard.events == [(BROWSER, "started", "c1"), (BROWSER, "ended", "c1"), (BROWSER, "started", "c2")]


async def test_teams_that_wait_get_the_group_one_after_the_other():
    heard = Heard()
    leases = _leases(on_activity=heard, wait_seconds=NEVER)
    await leases.acquire(BROWSER, "c1")
    second = await _waiting(leases, BROWSER, "c2")
    third = await _waiting(leases, BROWSER, "c3")

    leases.release("c1")
    done, pending = await asyncio.wait({second, third}, timeout=5, return_when=asyncio.FIRST_COMPLETED)

    # One of the two has it now; the other still waits, for that one.
    assert len(done) == 1 and len(pending) == 1
    winner = leases.holder(BROWSER).team
    assert winner in ("c2", "c3")
    leases.release(winner)
    assert await asyncio.wait_for(pending.pop(), 5) is None
    loser = "c3" if winner == "c2" else "c2"
    assert leases.holder(BROWSER).team == loser
    assert heard.events == [
        (BROWSER, "started", "c1"),
        (BROWSER, "ended", "c1"),
        (BROWSER, "started", winner),
        (BROWSER, "ended", winner),
        (BROWSER, "started", loser),
    ]


async def test_the_wait_is_bounded_and_then_returns_the_occupation_in_the_way():
    heard = Heard()
    leases = _leases(on_activity=heard, wait_seconds=0.05)
    await leases.acquire(BROWSER, "c1", title="book the flight")

    in_the_way = await asyncio.wait_for(leases.acquire(BROWSER, "c2", title="read the news"), 5)

    assert in_the_way == Occupation(team="c1", title="book the flight", members={"c1"})
    # Nothing changed hands, and nobody is left waiting.
    assert leases.holder(BROWSER) is in_the_way
    assert leases._waiters == set()
    assert heard.events == [(BROWSER, "started", "c1")]


async def test_with_no_time_to_wait_the_occupation_in_the_way_comes_back_at_once():
    leases = _leases(wait_seconds=0)
    await leases.acquire(BROWSER, "c1")

    in_the_way = await asyncio.wait_for(leases.acquire(BROWSER, "c2"), 5)

    assert in_the_way is not None and in_the_way.team == "c1"


async def test_the_wait_comes_from_the_settings_when_none_is_given(monkeypatch):
    import config.nekoclaw_settings as settings

    async def root_of(commission_id: str) -> str:
        return commission_id

    leases = BuiltinLeases(root_of=root_of)
    assert leases._wait_limit() == settings.BUILTIN_OCCUPIED_WAIT_SECONDS == 120.0
    # Read when a commission has to wait, so a change applies from then on.
    monkeypatch.setattr(settings, "BUILTIN_OCCUPIED_WAIT_SECONDS", 0.05)
    await leases.acquire(BROWSER, "c1")

    in_the_way = await asyncio.wait_for(leases.acquire(BROWSER, "c2"), 5)

    assert in_the_way is not None and in_the_way.team == "c1"


# ---------------------------------------------------------------------------
# A commission and the task agents it started are one team
# ---------------------------------------------------------------------------


async def test_commissions_of_one_team_share_the_occupation():
    heard = Heard()
    leases = _leases({"child": "lead", "grandchild": "child"}, on_activity=heard)

    assert await leases.acquire(BROWSER, "lead", title="plan the trip") is None
    # Neither waits for the other: a lead waits for its task agents inside its own run.
    assert await asyncio.wait_for(leases.acquire(BROWSER, "child", title="look up trains"), 5) is None
    assert await asyncio.wait_for(leases.acquire(BROWSER, "grandchild"), 5) is None

    held = leases.holder(BROWSER)
    assert (held.team, held.title, held.members) == ("lead", "plan the trip", {"lead", "child", "grandchild"})
    # One occupation, announced once.
    assert heard.events == [(BROWSER, "started", "lead")]


async def test_a_team_is_named_after_its_lead_whoever_of_it_comes_first():
    heard = Heard()
    leases = _leases({"child": "lead"}, on_activity=heard)

    await leases.acquire(BROWSER, "child")

    assert leases.occupied() == {BROWSER: "lead"}
    assert leases.holder(BROWSER).members == {"child"}
    assert heard.events == [(BROWSER, "started", "lead")]


async def test_the_group_comes_free_only_when_the_last_of_the_team_lets_go():
    heard = Heard()
    leases = _leases({"child": "lead"}, on_activity=heard, wait_seconds=NEVER)
    await leases.acquire(BROWSER, "lead")
    assert await asyncio.wait_for(leases.acquire(BROWSER, "child"), 5) is None
    other = await _waiting(leases, BROWSER, "other")

    assert leases.release("lead") == []

    # The child still uses it: nothing came free and nothing was announced.
    assert leases.holder(BROWSER).members == {"child"}
    assert heard.events == [(BROWSER, "started", "lead")]
    assert not other.done()

    assert leases.release("child") == [BROWSER]

    assert await asyncio.wait_for(other, 5) is None
    assert heard.events == [(BROWSER, "started", "lead"), (BROWSER, "ended", "lead"), (BROWSER, "started", "other")]


async def test_a_task_agent_of_another_team_waits_like_any_other_commission():
    leases = _leases({"child": "lead", "stranger": "elsewhere"}, wait_seconds=0.05)
    await leases.acquire(BROWSER, "child")

    in_the_way = await asyncio.wait_for(leases.acquire(BROWSER, "stranger"), 5)

    assert in_the_way is not None and in_the_way.team == "lead"


# ---------------------------------------------------------------------------
# Letting go
# ---------------------------------------------------------------------------


async def test_release_lets_go_of_every_group_the_commission_occupies():
    leases = _leases()
    await leases.acquire(BROWSER, "c1")
    await leases.acquire(DESKTOP, "c1")

    assert sorted(leases.release("c1")) == sorted([BROWSER, DESKTOP])
    assert leases.occupied() == {}


async def test_releasing_a_commission_that_holds_nothing_frees_nothing():
    heard = Heard()
    cleanups = Cleanups()
    leases = _leases(on_activity=heard, on_released=cleanups, wait_seconds=NEVER)
    await leases.acquire(BROWSER, "c1")
    other = await _waiting(leases, BROWSER, "c2")

    assert leases.release("c2") == []
    assert leases.release("nobody") == []
    await leases.settle()

    assert leases.holder(BROWSER).team == "c1"
    assert heard.events == [(BROWSER, "started", "c1")]
    assert cleanups.done == []
    assert not other.done()
    leases.release("c1")
    await asyncio.wait_for(other, 5)


async def test_the_listener_hears_started_and_ended_once_per_occupation():
    heard = Heard()
    leases = _leases(on_activity=heard)

    # Many small steps of one commission are one occupation.
    for _step in range(3):
        assert await leases.acquire(BROWSER, "c1") is None
    assert leases.release("c1") == [BROWSER]
    assert leases.release("c1") == []

    assert heard.events == [(BROWSER, "started", "c1"), (BROWSER, "ended", "c1")]
    # Taken again later, it is a new occupation.
    await leases.acquire(BROWSER, "c1")
    assert heard.events[-1] == (BROWSER, "started", "c1")
    assert len(heard.events) == 3


async def test_the_cleanup_after_a_release_is_awaited_through_settle():
    release_cleanup = asyncio.Event()
    cleaned: list[tuple[str, str]] = []

    async def on_released(group: str, team: str) -> None:
        await release_cleanup.wait()
        cleaned.append((group, team))

    leases = _leases({"child": "lead"}, on_released=on_released)
    await leases.acquire(BROWSER, "child")

    # Releasing does not wait for the cleanup; ``settle`` does.
    assert leases.release("child") == [BROWSER]
    settling = asyncio.ensure_future(leases.settle())
    for _turn in range(5):
        await asyncio.sleep(0)
    assert cleaned == [] and not settling.done()
    release_cleanup.set()
    await asyncio.wait_for(settling, 5)

    # Told the group and the team that had it, not the member that let go last.
    assert cleaned == [(BROWSER, "lead")]
    assert leases._cleanups == set()


async def test_settle_with_nothing_released_returns_at_once():
    await asyncio.wait_for(_leases(on_released=Cleanups()).settle(), 5)


async def test_a_cleanup_that_fails_leaves_the_group_free_and_settle_returns():
    async def broken(group: str, team: str) -> None:
        raise RuntimeError("the browser would not close")

    leases = _leases(on_released=broken)
    await leases.acquire(BROWSER, "c1")

    assert leases.release("c1") == [BROWSER]
    await asyncio.wait_for(leases.settle(), 5)

    assert leases._cleanups == set()
    assert await leases.acquire(BROWSER, "c2") is None


async def test_a_listener_that_raises_does_not_break_acquiring_or_releasing():
    cleanups = Cleanups()

    def broken(group: str, state: str, team: str) -> None:
        raise RuntimeError("the banner is gone")

    leases = _leases(on_activity=broken, on_released=cleanups)

    assert await leases.acquire(BROWSER, "c1") is None
    assert leases.holder(BROWSER).team == "c1"
    assert leases.release("c1") == [BROWSER]
    await leases.settle()

    assert leases.holder(BROWSER) is None
    assert cleanups.done == [(BROWSER, "c1")]


async def test_the_listener_can_be_set_later():
    leases = _leases()
    await leases.acquire(BROWSER, "c1")
    heard = Heard()

    leases.set_activity_listener(heard)
    leases.release("c1")

    assert heard.events == [(BROWSER, "ended", "c1")]


async def test_release_all_lets_go_of_every_occupation():
    heard = Heard()
    cleanups = Cleanups()
    leases = _leases({"child": "lead"}, on_activity=heard, on_released=cleanups, wait_seconds=NEVER)
    await leases.acquire(BROWSER, "lead")
    assert await asyncio.wait_for(leases.acquire(BROWSER, "child"), 5) is None
    await leases.acquire(DESKTOP, "c2")
    waiting = await _waiting(leases, DESKTOP, "c3")

    leases.release_all()
    await leases.settle()

    ended = [event for event in heard.events if event[1] == "ended"]
    assert sorted(ended) == sorted([(BROWSER, "ended", "lead"), (DESKTOP, "ended", "c2")])
    assert sorted(cleanups.done) == sorted([(BROWSER, "lead"), (DESKTOP, "c2")])
    # What was freed is free for whoever waited.
    assert await asyncio.wait_for(waiting, 5) is None
    assert leases.occupied() == {DESKTOP: "c3"}


# ---------------------------------------------------------------------------
# The host: when a commission lets go, and who is told
# ---------------------------------------------------------------------------


class Provider:
    """A provider of built-in tools that writes down which groups it was told were released."""

    def __init__(self, *, cleanup_fails: bool = False) -> None:
        self.cleanup_fails = cleanup_fails
        self.released_groups: list[tuple[str, str]] = []

    def available(self) -> list[Any]:
        return [builtin_tool(BROWSER, {"name": "browser_list_pages", "description": "List the open pages."})]

    async def run(self, capability: Any, *, commission_id: str, arguments: dict[str, Any]) -> BuiltinOutcome:
        return BuiltinOutcome(True, "done")

    async def released(self, group: str, commission_id: str) -> None:
        self.released_groups.append((group, commission_id))
        if self.cleanup_fails:
            raise RuntimeError("the browser would not close")


@pytest.fixture
async def hosts() -> Any:
    made: list[Any] = []
    yield made
    for host in made:
        await host.shutdown()


def _host(hosts: list[Any], tmp_path: Any, **kwargs: Any) -> Any:
    kwargs.setdefault("decision_llm", FakeDecisionLLM())
    host = make_host(tmp_path, None, **kwargs)
    hosts.append(host)
    return host


async def _working(host: Any, title: str = "plan the trip", **values: Any) -> Any:
    values.setdefault("source", "dispatch")
    commission, _created = await host.ledger.create(
        lanlan_name="Lan", title=title, dispatch_text=title, route="task_agent", **values
    )
    await host.ledger.transition(commission.id, "working")
    return commission


@pytest.mark.parametrize("status", ["done", "failed", "cancelled", "blocked", "waiting_user"])
async def test_a_commission_that_ends_or_waits_for_the_user_lets_go_of_what_it_occupies(hosts, tmp_path, status):
    provider = Provider()
    heard = Heard()
    host = _host(hosts, tmp_path, builtins=provider, on_builtin_activity=heard)
    commission = await _working(host)
    await host.leases.acquire(BROWSER, commission.id, title=commission.title)
    await host.leases.acquire(DESKTOP, commission.id, title=commission.title)

    await host.ledger.transition(commission.id, status, reason="test")
    await host.leases.settle()

    assert host.leases.occupied() == {}
    assert sorted(heard.events) == sorted(
        [
            (BROWSER, "started", commission.id),
            (DESKTOP, "started", commission.id),
            (BROWSER, "ended", commission.id),
            (DESKTOP, "ended", commission.id),
        ]
    )
    # The provider is told, so it can drop what it kept for the commission.
    assert sorted(provider.released_groups) == sorted([(BROWSER, commission.id), (DESKTOP, commission.id)])


async def test_a_commission_that_goes_on_working_keeps_what_it_occupies(hosts, tmp_path):
    host = _host(hosts, tmp_path, builtins=Provider())
    commission = await _working(host)
    other = await _working(host, "read the news")
    await host.leases.acquire(BROWSER, commission.id)

    # Another commission ending, and this one being written to, change nothing for it.
    await host.ledger.transition(other.id, "done", reason="test")
    await host.ledger.update(commission.id, result_summary="half way")

    assert host.leases.occupied() == {BROWSER: commission.id}


async def test_a_commission_and_the_task_agents_it_started_are_one_team_for_the_host(hosts, tmp_path):
    provider = Provider()
    heard = Heard()
    host = _host(hosts, tmp_path, builtins=provider, on_builtin_activity=heard)
    lead = await _working(host)
    child = await _working(host, "look up trains", parent_id=lead.id, name="agent-1", source="task_agent")
    grandchild = await _working(host, "compare prices", parent_id=child.id, name="agent-2", source="task_agent")

    assert await host.leases.acquire(BROWSER, grandchild.id) is None
    assert await asyncio.wait_for(host.leases.acquire(BROWSER, lead.id), 5) is None
    held = host.leases.holder(BROWSER)
    assert (held.team, held.members) == (lead.id, {lead.id, grandchild.id})

    # The task agent is done; its lead still uses the browser.
    await host.ledger.transition(grandchild.id, "done", reason="test")
    assert host.leases.occupied() == {BROWSER: lead.id}
    await host.ledger.transition(lead.id, "waiting_user", reason="test")
    await host.leases.settle()

    assert host.leases.occupied() == {}
    assert heard.events == [(BROWSER, "started", lead.id), (BROWSER, "ended", lead.id)]
    assert provider.released_groups == [(BROWSER, lead.id)]


async def test_shutting_down_releases_everything_and_tells_the_provider(tmp_path):
    provider = Provider()
    heard = Heard()
    host = make_host(tmp_path, None, decision_llm=FakeDecisionLLM(), builtins=provider, on_builtin_activity=heard)
    one = await _working(host)
    two = await _working(host, "read the news")
    await host.leases.acquire(BROWSER, one.id)
    await host.leases.acquire(DESKTOP, two.id)

    await host.shutdown()

    assert host.leases.occupied() == {}
    assert sorted(provider.released_groups) == sorted([(BROWSER, one.id), (DESKTOP, two.id)])
    assert sorted(event for event in heard.events if event[1] == "ended") == sorted(
        [(BROWSER, "ended", one.id), (DESKTOP, "ended", two.id)]
    )


async def test_shutting_down_waits_for_the_providers_cleanup_before_it_goes_on(tmp_path):
    order: list[str] = []

    class SlowCleanup(Provider):
        async def released(self, group: str, commission_id: str) -> None:
            # Closing a browser takes a while.
            for _turn in range(10):
                await asyncio.sleep(0)
            order.append("the provider cleaned up")

    host = make_host(tmp_path, None, decision_llm=FakeDecisionLLM(), builtins=SlowCleanup())
    stop = host.gateway.stop

    async def stopping() -> None:
        order.append("the gateway is stopped")
        await stop()

    host.gateway.stop = stopping
    commission = await _working(host)
    await host.leases.acquire(BROWSER, commission.id)

    await host.shutdown()

    assert order == ["the provider cleaned up", "the gateway is stopped"]


async def test_a_provider_that_fails_to_clean_up_does_not_stop_the_shutdown(tmp_path):
    provider = Provider(cleanup_fails=True)
    host = make_host(tmp_path, None, decision_llm=FakeDecisionLLM(), builtins=provider)
    commission = await _working(host)
    await host.leases.acquire(BROWSER, commission.id)

    await asyncio.wait_for(host.shutdown(), 30)

    assert provider.released_groups == [(BROWSER, commission.id)]
    assert host.closed


async def test_without_a_provider_a_release_has_nobody_to_tell(hosts, tmp_path):
    host = _host(hosts, tmp_path)
    commission = await _working(host)
    await host.leases.acquire(BROWSER, commission.id)

    await host.ledger.transition(commission.id, "done", reason="test")
    await asyncio.wait_for(host.leases.settle(), 5)

    assert host.leases.occupied() == {}
