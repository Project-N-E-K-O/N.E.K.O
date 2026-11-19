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

"""Tidying the entries of the task agent space with a model: what is looked over, and which changes are made."""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from nekoclaw.host.decision import TIER_CONSOLIDATE
from nekoclaw.host.ledger import Ledger
from nekoclaw.host.paths import HostPaths
from nekoclaw.host.space import ENTRY_ACTIVE, ENTRY_EXPIRED, ENTRY_PENDING, TaskAgentSpace
from nekoclaw.host.space_keeper import SpaceKeeper, _consolidate_messages

DAY = 86400.0


class Clock:
    def __init__(self, now: float = 1_800_000_000.0) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now


class FakeLLM:
    """Answers the call that looks over a layer: with what ``answer`` makes of what it is sent."""

    def __init__(self) -> None:
        self.answer: Any = {"operations": []}
        self.calls: list[list[dict[str, Any]]] = []

    async def complete(self, tier: str, messages: list[dict[str, Any]]) -> str:
        assert tier == TIER_CONSOLIDATE
        self.calls.append(messages)
        answer = self.answer
        if callable(answer):
            answer = await answer(messages)
        if isinstance(answer, BaseException):
            raise answer
        return answer if isinstance(answer, str) else json.dumps(answer, ensure_ascii=False)


class World:
    def __init__(self, tmp_path: Path) -> None:
        self.clock = Clock()
        self.paths = HostPaths(tmp_path / "nekoclaw")
        self.ledger = Ledger(self.paths.ledger_db, clock=self.clock)
        self.space = TaskAgentSpace(self.paths.space, ledger=self.ledger, clock=self.clock)
        self.llm = FakeLLM()
        self.keeper = SpaceKeeper(space=self.space, ledger=self.ledger, llm=self.llm, clock=self.clock, review_delay=0)

    @property
    def root(self) -> Path:
        return self.paths.space().root

    def memory(self, project: str | None = None) -> Path:
        return self.paths.space().memory_dir(project)

    async def active(self, title: str, content: str, project: str | None = None) -> str:
        entry, _created = await self.space.file_entry(project, title=title, content=content)
        return entry.id

    async def pending(self, title: str, content: str, *, by: str = "c1", project: str | None = None) -> str:
        waits: list[str] = []
        entry, _created = await self.space.propose_entry(
            project, title=title, content=content, source=by, observer=by, waits=waits
        )
        assert entry is None and waits
        return waits[0]

    async def entry(self, entry_id: str, project: str | None = None) -> Any:
        return await self.space.read_entry(entry_id, project)

    async def tidy(self) -> bool:
        return await self.keeper.tidy(force=True)

    def answer(self, make: Callable[[dict[str, str]], list[dict[str, Any]]]) -> None:
        """Have the model answer with changes made from the handles of the titles it was shown."""

        async def answered(messages: list[dict[str, Any]]) -> dict[str, Any]:
            return {"operations": make(handles(messages))}

        self.llm.answer = answered


def handles(messages: list[dict[str, Any]]) -> dict[str, str]:
    """Return the handle of every entry the model was shown, by its title."""
    shown = re.findall(r"^- (n\d+): (.+?)(?: — | \()", messages[-1]["content"], flags=re.MULTILINE)
    return {title: handle for handle, title in shown}


@pytest.fixture
async def world(tmp_path: Path) -> Any:
    made = World(tmp_path)
    yield made
    await made.keeper.aclose()
    await made.ledger.close()


# ---------------------------------------------------------------------------
# What is looked over
# ---------------------------------------------------------------------------


async def test_a_layer_is_looked_over_again_only_when_an_entry_of_it_says_something_new(world):
    await world.active("Build with make", "Run make in the root, not in src.")
    await world.active("Tests need a display", "Set DISPLAY before running the UI tests.")

    await world.tidy()
    assert len(world.llm.calls) == 1

    # Nothing new: no call.
    await world.tidy()
    assert len(world.llm.calls) == 1

    # A pending one is new.
    await world.pending("Cache lives in tmp", "The build cache is under /tmp/cache.")
    await world.tidy()
    assert len(world.llm.calls) == 2

    # Added to: new as well.
    await world.space.file_entry(None, title="Build with make", content="make -j8 halves the time.")
    await world.tidy()
    assert len(world.llm.calls) == 3


async def test_an_entry_that_only_expired_or_came_back_is_not_news(world):
    await world.active("Build with make", "Run make in the root, not in src.")
    await world.active("Tests need a display", "Set DISPLAY before running the UI tests.")
    await world.tidy()
    path = world.memory() / "Build-with-make.md"
    text = path.read_text(encoding="utf-8")
    path.write_text(text.replace("---\n\n", "status: expired\nexpired: 2027-01-01\n---\n\n", 1), encoding="utf-8")

    await world.tidy()

    assert len(world.llm.calls) == 1


async def test_a_layer_with_fewer_than_two_entries_of_the_host_is_not_looked_over(world):
    await world.active("Build with make", "Run make in the root, not in src.")
    # One the user wrote does not count.
    (world.memory() / "mine.md").write_text("Always ask before deleting.\n", encoding="utf-8")

    await world.tidy()

    assert world.llm.calls == []


async def test_nothing_is_looked_over_without_a_model_or_when_it_is_switched_off(world, monkeypatch):
    await world.active("Build with make", "Run make in the root, not in src.")
    await world.active("Tests need a display", "Set DISPLAY before running the UI tests.")
    monkeypatch.setattr("config.nekoclaw_settings.SPACE_CONSOLIDATE_ENABLED", False)

    await world.tidy()

    assert world.llm.calls == []


async def test_at_most_two_layers_a_round_the_ones_looked_over_longest_ago_first(world):
    for name in ("alpha", "beta"):
        await world.space.open_project(name)
    for layer in (None, "alpha", "beta"):
        await world.active("Build with make", f"Run make in the root of {layer}.", layer)
        await world.active("Tests need a display", f"Set DISPLAY for {layer}.", layer)

    await world.tidy()
    first = [call[-1]["content"].split("\n")[1] for call in world.llm.calls]
    assert first == ["Notes of: all projects", "Notes of: project alpha"]

    await world.tidy()
    assert world.llm.calls[-1][-1]["content"].split("\n")[1] == "Notes of: project beta"
    assert len(world.llm.calls) == 3

    # All three changed again: the two looked over longest ago go first.
    for layer in (None, "alpha", "beta"):
        await world.space.file_entry(layer, title="Build with make", content=f"Use -j8 in {layer}.")
    await world.tidy()
    assert [call[-1]["content"].split("\n")[1] for call in world.llm.calls[3:]] == [
        "Notes of: all projects",
        "Notes of: project alpha",
    ]


async def test_a_model_that_cannot_be_asked_is_asked_again_and_one_that_answers_nonsense_is_not(world):
    await world.active("Build with make", "Run make in the root, not in src.")
    await world.active("Tests need a display", "Set DISPLAY before running the UI tests.")
    world.llm.answer = TimeoutError("no answer")

    await world.tidy()
    await world.tidy()
    assert len(world.llm.calls) == 2

    world.llm.answer = "I could not decide."
    await world.tidy()
    await world.tidy()
    assert len(world.llm.calls) == 3


async def test_the_model_sees_the_new_and_pending_texts_and_the_users_entries_without_a_handle(world):
    await world.active("Build with make", "Run make in the root, not in src.")
    await world.active("Mirror", "The mirror is at mirror.local.")
    await world.tidy()
    await world.active("Tests need a display", "Set DISPLAY before running the UI tests.")
    await world.pending("Cache lives in tmp", "The build cache is under /tmp/cache.")
    (world.memory() / "mine.md").write_text("---\nname: Mine\n---\n\nAlways ask before deleting.\n", encoding="utf-8")

    await world.tidy()

    user = world.llm.calls[-1][-1]["content"]
    assert set(handles(world.llm.calls[-1])) == {"Build with make", "Mirror", "Tests need a display", "Cache lives in tmp"}
    assert "- Mine" in user
    # Texts: the new one and the pending one, not the one that did not change since.
    shown = handles(world.llm.calls[-1])
    assert f"[{shown['Tests need a display']}] Tests need a display (created" in user
    assert f"[{shown['Cache lives in tmp']}] Cache lives in tmp (created" in user
    assert f"[{shown['Build with make']}] Build with make (created" not in user
    assert "Today: 2027-01-15" in user


# ---------------------------------------------------------------------------
# Merging
# ---------------------------------------------------------------------------


async def test_two_entries_in_effect_that_say_the_same_become_one(world):
    one = await world.active("Build with make", "Run make in the root, not in src.")
    world.clock.now += 3 * DAY
    other = await world.active("Make from the root", "make only works from the repository root.")
    world.clock.now += 2 * DAY
    world.answer(lambda shown: [{"op": "merge", "from": shown["Make from the root"], "into": shown["Build with make"]}])

    await world.tidy()

    assert not (world.memory() / f"{other}.md").exists()
    kept = await world.entry(one)
    assert "Run make in the root, not in src." in kept.content
    # Under the day it was written on, not the day it was moved.
    assert "## 2027-01-18\n\nmake only works from the repository root." in kept.content
    index = await world.space.read_index(None)
    assert f"memory/{one}.md" in index and f"memory/{other}.md" not in index
    # Not remembered as removed by the user: it can be filed again.
    assert (await world.space.read_notes(None))["removed"] == []


async def test_a_pending_guess_merged_into_an_entry_in_effect_goes_into_it_whichever_way_round(world):
    one = await world.active("Build with make", "Run make in the root, not in src.")
    guess = await world.pending("Make needs the root", "make fails when started in src.", by="c7")
    world.answer(lambda shown: [{"op": "merge", "from": shown["Build with make"], "into": shown["Make needs the root"]}])

    await world.tidy()

    assert not (world.memory() / f"{guess}.md").exists()
    kept = await world.entry(one)
    assert kept.status == ENTRY_ACTIVE
    assert "make fails when started in src." in kept.content
    assert "c7" in kept.seen_by


async def test_two_guesses_two_commissions_filed_in_other_words_take_effect_as_one(world):
    first = await world.pending("Make needs the root", "make fails when started in src.", by="c1")
    second = await world.pending("Build only from top", "Building from a subdirectory breaks.", by="c2")
    world.answer(lambda shown: [{"op": "merge", "from": shown["Build only from top"], "into": shown["Make needs the root"]}])

    await world.tidy()

    assert not (world.memory() / f"{second}.md").exists()
    kept = await world.entry(first)
    assert kept.status == ENTRY_ACTIVE
    assert kept.seen == 2 and set(kept.seen_by) == {"c1", "c2"}
    assert "Building from a subdirectory breaks." in kept.content
    assert f"memory/{first}.md" in await world.space.read_index(None)


async def test_two_guesses_of_one_commission_stay_a_guess_once_merged(world):
    first = await world.pending("Make needs the root", "make fails when started in src.", by="c1")
    second = await world.pending("Build only from top", "Building from a subdirectory breaks.", by="c1")
    world.answer(lambda shown: [{"op": "merge", "from": shown["Build only from top"], "into": shown["Make needs the root"]}])

    await world.tidy()

    assert not (world.memory() / f"{second}.md").exists()
    kept = await world.entry(first)
    assert kept.status == ENTRY_PENDING
    assert f"memory/{first}.md" not in await world.space.read_index(None)


async def test_merging_two_guesses_does_not_have_them_wait_from_today(world):
    first = await world.pending("Make needs the root", "make fails when started in src.", by="c1")
    world.clock.now += 5 * DAY
    await world.pending("Build only from top", "Building from a subdirectory breaks.", by="c1")
    world.clock.now += 20 * DAY
    world.answer(lambda shown: [{"op": "merge", "from": shown["Build only from top"], "into": shown["Make needs the root"]}])

    await world.tidy()

    kept = await world.entry(first)
    assert (kept.created, kept.updated) == ("2027-01-15", "2027-01-20")


async def test_a_guess_merged_into_an_entry_keeps_the_day_it_was_filed(world):
    await world.pending("Make needs the root", "make fails when started in src.", by="c7")
    world.clock.now += 2 * DAY
    one = await world.active("Build with make", "Run make in the root, not in src.")
    world.answer(lambda shown: [{"op": "merge", "from": shown["Make needs the root"], "into": shown["Build with make"]}])

    await world.tidy()

    assert "## 2027-01-15\n\nmake fails when started in src." in (await world.entry(one)).content


async def test_guesses_that_take_effect_as_one_go_into_the_entry_of_their_title(world):
    first = await world.pending("Make needs the root", "make fails when started in src.", by="c1")
    await world.pending("Build only from top", "Building from a subdirectory breaks.", by="c2")
    # Filed from the console under that title: an entry of its own, beside the guess.
    one = await world.active("Make needs the root", "Run make in the root.")
    assert one != first and (await world.entry(first)).status == ENTRY_PENDING
    world.answer(lambda shown: [{"op": "merge", "from": shown["Build only from top"], "into": shown["Make needs the root"]}])

    await world.tidy()

    assert not (world.memory() / f"{first}.md").exists()
    kept = await world.entry(one)
    assert "make fails when started in src." in kept.content
    assert "Building from a subdirectory breaks." in kept.content


async def test_an_entry_that_expired_is_not_merged(world):
    one = await world.active("Build with make", "Run make in the root.")
    other = await world.active("Make from the root", "make only works from the repository root.")
    path = world.memory() / f"{other}.md"
    path.write_text(path.read_text(encoding="utf-8").replace("---\n\n", "status: expired\nexpired: 2027-01-01\n---\n\n", 1), encoding="utf-8")
    layer = await world.space.read_layer(None)

    outcome, _changed = await world.space.tidy_entries(None, {"op": "merge", "from": other, "into": one}, layer.marks)

    assert "expired" in outcome
    assert path.exists()


async def test_an_entry_the_user_saved_while_the_model_looked_is_not_merged(world):
    one = await world.active("Build with make", "Run make in the root.")
    other = await world.active("Make from the root", "make only works from the repository root.")
    path = world.memory() / f"{other}.md"

    async def meanwhile(messages: list[dict[str, Any]]) -> dict[str, Any]:
        path.write_text(path.read_text(encoding="utf-8") + "\nSaved by hand.\n", encoding="utf-8")
        shown = handles(messages)
        return {"operations": [{"op": "merge", "from": shown["Make from the root"], "into": shown["Build with make"]}]}

    world.llm.answer = meanwhile

    await world.tidy()

    assert path.exists() and "Saved by hand." in path.read_text(encoding="utf-8")
    assert "repository root" not in (await world.entry(one)).content


async def test_a_merge_that_would_make_an_entry_too_long_is_not_made(world, monkeypatch):
    monkeypatch.setattr("config.nekoclaw_settings.SPACE_CONSOLIDATE_MERGED_MAX_TOKENS", 20)
    one = await world.active("Build with make", "Run make in the root, not in src.")
    other = await world.active("Make from the root", "make only works from the repository root, and from nowhere else.")
    world.answer(lambda shown: [{"op": "merge", "from": shown["Make from the root"], "into": shown["Build with make"]}])

    await world.tidy()

    assert (world.memory() / f"{other}.md").exists()
    assert "repository root" not in (await world.entry(one)).content


# ---------------------------------------------------------------------------
# Out of date
# ---------------------------------------------------------------------------


async def test_an_entry_a_newer_one_contradicts_expires_for_good(world, tmp_path):
    workdir = tmp_path / "work"
    workdir.mkdir()
    (workdir / "Makefile").write_text("all:\n", encoding="utf-8")
    await world.space.open_project("alpha", workdir=str(workdir))
    # One that rests on a file that is there: a look at its files would bring it back.
    old, _created = await world.space.propose_entry(
        "alpha", title="Build with make", content="Run make in the root.", bases=[str(workdir)], evidence=["Makefile"]
    )
    assert old is not None and old.evidence
    world.clock.now += DAY
    await world.active("Build with ninja", "The build moved to ninja; make is gone.", "alpha")
    world.answer(
        lambda shown: [
            {"op": "outdated", "entry": shown["Build with make"], "by": shown["Build with ninja"], "why": "the build moved to ninja"}
        ]
    )

    await world.tidy()

    gone = await world.entry(old.id, "alpha")
    assert gone.status == ENTRY_EXPIRED and gone.outdated == "the build moved to ninja"
    assert f"memory/{old.id}.md" not in await world.space.read_index("alpha")
    assert (world.memory("alpha") / f"{old.id}.md").exists()

    await world.space.check_evidence()
    assert (await world.entry(old.id, "alpha")).status == ENTRY_EXPIRED


async def test_an_entry_that_is_out_of_date_is_back_when_it_is_filed_again(world):
    old = await world.active("Build with make", "Run make in the root.")
    world.clock.now += DAY
    await world.active("Build with ninja", "The build moved to ninja; make is gone.")
    world.answer(lambda shown: [{"op": "outdated", "entry": shown["Build with make"], "by": shown["Build with ninja"]}])
    await world.tidy()

    await world.space.file_entry(None, title="Build with make", content="make is back for the docs build.")

    back = await world.entry(old)
    assert back.status == ENTRY_ACTIVE and back.outdated == ""
    assert "outdated" not in (world.memory() / f"{old}.md").read_text(encoding="utf-8")


async def test_an_entry_is_not_out_of_date_by_an_older_one_or_a_guess(world):
    newer = await world.active("Build with make", "Run make in the root.")
    world.clock.now -= DAY
    older = await world.active("Build with ninja", "The build moved to ninja.")
    world.clock.now += 2 * DAY
    guess = await world.pending("Build with bazel", "Maybe bazel is used.")
    world.answer(
        lambda shown: [
            {"op": "outdated", "entry": shown["Build with make"], "by": shown["Build with ninja"]},
            {"op": "outdated", "entry": shown["Build with make"], "by": shown["Build with bazel"]},
        ]
    )

    await world.tidy()

    assert (await world.entry(newer)).status == ENTRY_ACTIVE
    assert (await world.entry(older)).status == ENTRY_ACTIVE
    assert (await world.entry(guess)).status == ENTRY_PENDING


# ---------------------------------------------------------------------------
# Titles and dates
# ---------------------------------------------------------------------------


async def test_an_entry_easily_taken_for_another_is_given_a_title_that_tells_it_apart(world):
    one = await world.active("Tests", "Run pytest with -n 4.")
    await world.active("Testing", "Tests need DISPLAY set.")
    world.answer(
        lambda shown: [
            {"op": "retitle", "entry": shown["Tests"], "apart_from": shown["Testing"], "title": "Run tests in parallel", "description": "pytest -n 4"}
        ]
    )

    await world.tidy()

    renamed = await world.entry(one)
    assert (renamed.title, renamed.description) == ("Run tests in parallel", "pytest -n 4")
    assert f"- [Run tests in parallel](memory/{one}.md) — pytest -n 4" in await world.space.read_index(None)


async def test_no_entry_is_given_the_title_of_another(world):
    one = await world.active("Tests", "Run pytest with -n 4.")
    await world.active("Testing", "Tests need DISPLAY set.")
    world.answer(
        lambda shown: [{"op": "retitle", "entry": shown["Tests"], "apart_from": shown["Testing"], "title": "testing"}]
    )

    await world.tidy()

    assert (await world.entry(one)).title == "Tests"


async def test_an_entry_is_retitled_only_apart_from_one_that_is_there(world):
    one = await world.active("Tests", "Run pytest with -n 4.")
    await world.active("Testing", "Tests need DISPLAY set.")
    layer = await world.space.read_layer(None)

    outcome, _changed = await world.space.tidy_entries(
        None, {"op": "retitle", "entry": one, "apart_from": "nothing-here", "title": "Parallel tests"}, layer.marks
    )

    assert "is not there" in outcome
    assert (await world.entry(one)).title == "Tests"


async def test_no_entry_is_given_a_title_the_user_removed(world):
    one = await world.active("Tests", "Run pytest with -n 4.")
    await world.active("Testing", "Tests need DISPLAY set.")
    gone = await world.active("Run tests in parallel", "Old advice.")
    assert await world.space.delete_entry(gone)
    world.answer(
        lambda shown: [{"op": "retitle", "entry": shown["Tests"], "apart_from": shown["Testing"], "title": "Run tests in parallel"}]
    )

    await world.tidy()

    assert (await world.entry(one)).title == "Tests"


async def test_two_changes_of_one_entry_are_both_made(world):
    one = await world.active("Server", "The staging server was down yesterday.")
    await world.active("Server status", "Look at status.local first.")
    world.answer(
        lambda shown: [
            {"op": "date", "entry": shown["Server"], "old": "yesterday", "new": "on 2027-01-14"},
            {"op": "retitle", "entry": shown["Server"], "apart_from": shown["Server status"], "title": "Staging outage"},
        ]
    )

    await world.tidy()

    entry = await world.entry(one)
    assert entry.title == "Staging outage" and "on 2027-01-14" in entry.content


async def test_a_date_in_the_second_text_of_an_entry_keeps_the_texts_apart(world):
    from nekoclaw.host.space import _digest, _listed, _parse_document, _texts_of

    one = await world.active("Server down", "The staging server is flaky.")
    world.clock.now += DAY
    await world.space.file_entry(None, title="Server down", content="It was down yesterday; use the mirror.")
    await world.active("Mirror", "The mirror is at mirror.local.")
    world.answer(lambda shown: [{"op": "date", "entry": shown["Server down"], "old": "yesterday", "new": "on 2027-01-15"}])

    await world.tidy()

    document = _parse_document((world.memory() / f"{one}.md").read_text(encoding="utf-8"))
    texts = _texts_of(document.body, _listed(document, "texts"))
    assert texts == ["The staging server is flaky.", "It was down on 2027-01-15; use the mirror."]
    assert _listed(document, "texts") == [_digest(text) for text in texts]


async def test_yesterday_is_replaced_by_its_date_and_the_text_is_still_known(world):
    one = await world.active("Server down", "The staging server was down yesterday; use the mirror.")
    await world.active("Mirror", "The mirror is at mirror.local.")
    world.answer(
        lambda shown: [{"op": "date", "entry": shown["Server down"], "old": "yesterday", "new": "on 2027-01-14"}]
    )

    await world.tidy()

    entry = await world.entry(one)
    assert entry.content == "The staging server was down on 2027-01-14; use the mirror."
    # The same text filed again is there already, not a second section.
    again, created = await world.space.file_entry(
        None, title="Server down", content="The staging server was down on 2027-01-14; use the mirror."
    )
    assert not created and again.content == entry.content


async def test_words_that_stand_twice_or_a_replacement_with_no_date_are_left(world):
    one = await world.active("Server down", "Down yesterday, and up yesterday evening.")
    await world.active("Mirror", "The mirror is at mirror.local.")
    world.answer(
        lambda shown: [
            {"op": "date", "entry": shown["Server down"], "old": "yesterday", "new": "on 2027-01-14"},
            {"op": "date", "entry": shown["Server down"], "old": "Down yesterday", "new": "Down the day before"},
        ]
    )

    await world.tidy()

    assert (await world.entry(one)).content == "Down yesterday, and up yesterday evening."


# ---------------------------------------------------------------------------
# What is never changed
# ---------------------------------------------------------------------------


async def test_an_entry_the_user_saved_while_the_model_looked_is_left_as_they_saved_it(world):
    one = await world.active("Server down", "The staging server was down yesterday.")
    await world.active("Mirror", "The mirror is at mirror.local.")
    path = world.memory() / f"{one}.md"

    async def meanwhile(messages: list[dict[str, Any]]) -> dict[str, Any]:
        path.write_text(path.read_text(encoding="utf-8") + "\nSaved by hand.\n", encoding="utf-8")
        return {"operations": [{"op": "date", "entry": handles(messages)["Server down"], "old": "yesterday", "new": "on 2027-01-14"}]}

    world.llm.answer = meanwhile

    await world.tidy()

    assert "down yesterday" in path.read_text(encoding="utf-8")
    assert "Saved by hand." in path.read_text(encoding="utf-8")


async def test_an_entry_the_user_wrote_is_never_changed(world):
    await world.active("Build with make", "Run make in the root.")
    await world.active("Mirror", "The mirror is at mirror.local.")
    mine = world.memory() / "mine.md"
    mine.write_text("---\nname: Mine\n---\n\nThe server was down yesterday.\n", encoding="utf-8")
    layer = await world.space.read_layer(None)

    outcome, changed = await world.space.tidy_entries(
        None, {"op": "date", "entry": "mine", "old": "yesterday", "new": "on 2027-01-14"}, layer.marks
    )

    assert "written by the user" in outcome and changed == {}
    assert "yesterday" in mine.read_text(encoding="utf-8")


async def test_a_change_naming_an_entry_it_was_not_shown_is_not_made(world):
    one = await world.active("Build with make", "Run make in the root.")
    await world.active("Mirror", "The mirror is at mirror.local.")
    world.answer(lambda shown: [{"op": "merge", "from": "n99", "into": shown["Mirror"]}, {"op": "delete", "entry": shown["Mirror"]}])

    await world.tidy()

    assert (await world.entry(one)).status == ENTRY_ACTIVE
    assert len(list(world.memory().glob("*.md"))) == 2


async def test_no_more_changes_are_made_than_may_be(world, monkeypatch):
    monkeypatch.setattr("config.nekoclaw_settings.SPACE_CONSOLIDATE_MAX_OPERATIONS", 1)
    one = await world.active("Server down", "Down yesterday.")
    two = await world.active("Mirror down", "The mirror was down yesterday.")
    world.answer(
        lambda shown: [
            {"op": "date", "entry": shown["Server down"], "old": "yesterday", "new": "on 2027-01-14"},
            {"op": "date", "entry": shown["Mirror down"], "old": "yesterday", "new": "on 2027-01-14"},
        ]
    )

    await world.tidy()

    assert (await world.entry(one)).content == "Down on 2027-01-14."
    assert (await world.entry(two)).content == "The mirror was down yesterday."


async def test_of_an_answer_cut_off_the_whole_changes_are_made(world):
    one = await world.active("Server down", "Down yesterday.")
    two = await world.active("Mirror down", "The mirror was down yesterday.")

    async def cut(messages: list[dict[str, Any]]) -> str:
        shown = handles(messages)
        return (
            '{"operations": [{"op": "date", "entry": "%s", "old": "yesterday", "new": "on 2027-01-14"}, '
            '{"op": "date", "entry": "%s", "old": "yester' % (shown["Server down"], shown["Mirror down"])
        )

    world.llm.answer = cut

    await world.tidy()

    assert (await world.entry(one)).content == "Down on 2027-01-14."
    assert (await world.entry(two)).content == "The mirror was down yesterday."


# ---------------------------------------------------------------------------
# Caps
# ---------------------------------------------------------------------------


async def test_what_the_model_is_sent_fits_its_cap_with_every_part_full(world):
    from config import nekoclaw_settings as settings
    from nekoclaw.host.space import Entry, Layer
    from utils.tokenize import count_tokens

    word = "长句子很长 "
    entries = []
    for number in range(80):
        status = ENTRY_PENDING if number % 2 else ENTRY_ACTIVE
        entries.append(
            Entry(
                id=f"e{number}",
                project=None,
                title=(word * 10)[:40],
                description=(word * 20)[:70],
                type="lesson",
                created="2027-01-01",
                updated="2027-01-02",
                content=word * 400,
                confidence="inferred",
                status=status,
            )
        )
    users = [
        Entry(id=f"u{number}", project=None, title=word * 5, description=word * 10, type="lesson", created="", updated="")
        for number in range(20)
    ]
    look = Layer(None, (*entries, *users), frozenset(entry.id for entry in entries), {}, {})

    messages, shown = _consolidate_messages(look, [entry.id for entry in entries], world.clock.now)

    system, user = messages[0]["content"], messages[1]["content"]
    assert count_tokens(system) + count_tokens(user) <= settings.SPACE_CONSOLIDATE_INPUT_MAX_TOKENS
    # Not cut at the end: the parts fit by their own caps.
    assert user.rstrip().endswith("…") is False
    assert shown and all(handle in user for handle in shown)


def test_the_longest_answer_that_may_be_kept_fits_the_cap_on_output():
    from config import nekoclaw_settings as settings
    from utils.tokenize import count_tokens

    title = "标题" * 40
    description = "说明" * 60
    one = {
        "op": "retitle",
        "entry": "n12",
        "apart_from": "n13",
        "title": title,
        "description": description,
    }
    while count_tokens(one["title"]) > settings.SPACE_ENTRY_TITLE_MAX_TOKENS:
        one["title"] = one["title"][:-1]
    while count_tokens(one["description"]) > settings.SPACE_ENTRY_DESCRIPTION_MAX_TOKENS:
        one["description"] = one["description"][:-1]
    full = count_tokens(
        json.dumps({"operations": [one] * settings.SPACE_CONSOLIDATE_MAX_OPERATIONS}, ensure_ascii=False, indent=1)
    )
    assert full <= settings.SPACE_CONSOLIDATE_OUTPUT_MAX_TOKENS
    assert 1.2 <= settings.SPACE_CONSOLIDATE_OUTPUT_MAX_TOKENS / full < 1.4
