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
from nekoclaw.host.space import (
    ENTRY_ACTIVE,
    ENTRY_EXPIRED,
    ENTRY_PENDING,
    TaskAgentSpace,
    _LineToPut,
    _NotAsWritten,
    _text_mark,
)
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


async def test_an_entry_that_only_expired_came_back_or_was_seen_again_is_not_news(world):
    one = await world.active("Build with make", "Run make in the root, not in src.")
    await world.active("Tests need a display", "Set DISPLAY before running the UI tests.")
    await world.tidy()
    path = world.memory() / f"{one}.md"
    text = path.read_text(encoding="utf-8")
    path.write_text(text.replace("---\n\n", "status: expired\nexpired: 2027-01-01\n---\n\n", 1), encoding="utf-8")
    await world.tidy()
    path.write_text(text, encoding="utf-8")
    # Filed again, in the same words, by another commission: counted, nothing new said.
    await world.space.propose_entry(
        None, title="Build with make", content="Run make in the root, not in src.", source="c9", observer="c9"
    )
    assert "c9" in (await world.entry(one)).seen_by

    await world.tidy()

    assert len(world.llm.calls) == 1


async def test_what_changed_while_the_model_looked_is_news_to_the_next_round(world):
    one = await world.active("Build with make", "Run make in the root, not in src.")
    await world.active("Tests need a display", "Set DISPLAY before running the UI tests.")

    async def meanwhile(messages: list[dict[str, Any]]) -> dict[str, Any]:
        # Another commission adds to an entry while the model looks.
        await world.space.file_entry(None, title="Build with make", content="make -j8 halves the time.")
        return {"operations": []}

    world.llm.answer = meanwhile
    await world.tidy()
    world.llm.answer = {"operations": []}

    await world.tidy()

    assert len(world.llm.calls) == 2
    assert "make -j8 halves the time." in world.llm.calls[-1][-1]["content"]
    assert (await world.entry(one)).status == ENTRY_ACTIVE


async def test_what_tidying_changed_itself_is_no_news_to_the_next_round(world):
    one = await world.active("Server down", "The staging server was down yesterday.")
    await world.active("Mirror", "The mirror is at mirror.local.")
    world.answer(lambda shown: [{"op": "date", "entry": shown["Server down"], "old": "yesterday", "new": "2027-01-14"}])
    await world.tidy()
    assert (await world.entry(one)).content == "The staging server was down 2027-01-14."

    await world.tidy()

    assert len(world.llm.calls) == 1


async def test_guesses_tidying_had_take_effect_as_one_are_no_news_to_the_next_round(world):
    first = await world.pending("Make needs the root", "make fails when started in src.", by="c1")
    await world.pending("Build only from top", "Building from a subdirectory breaks.", by="c2")
    await world.active("Mirror", "The mirror is at mirror.local.")
    world.answer(lambda shown: [{"op": "merge", "from": shown["Build only from top"], "into": shown["Make needs the root"]}])
    await world.tidy()
    assert (await world.entry(first)).status == ENTRY_ACTIVE

    await world.tidy()

    assert len(world.llm.calls) == 1


async def test_an_entry_the_list_had_no_room_for_is_news_to_the_next_round(world, monkeypatch):
    for number in range(3):
        await world.active(f"Entry {number}", f"Text {number}.")
    one_line = len("- n1: Entry 0 — Text 0. (lesson; not said how it is known; seen 1; 2027-01-15 to 2027-01-15)")
    monkeypatch.setattr("config.nekoclaw_settings.SPACE_CONSOLIDATE_LIST_MAX_TOKENS", one_line // 2)

    await world.tidy()
    first = set(handles(world.llm.calls[-1]))
    await world.tidy()

    assert len(world.llm.calls) == 2
    assert first and not first & set(handles(world.llm.calls[-1]))


async def test_a_layer_of_expired_entries_is_not_looked_over(world):
    for title in ("Build with make", "Tests need a display"):
        entry_id = await world.active(title, f"{title}, as it was.")
        path = world.memory() / f"{entry_id}.md"
        path.write_text(path.read_text(encoding="utf-8").replace("---\n\n", "status: expired\nexpired: 2027-01-01\n---\n\n", 1), encoding="utf-8")

    await world.tidy()

    assert world.llm.calls == []


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


async def test_layers_the_model_cannot_be_asked_about_do_not_keep_another_from_its_turn(world):
    for name in ("alpha", "beta"):
        await world.space.open_project(name)
    for layer in (None, "alpha", "beta"):
        await world.active("Build with make", f"Run make in the root of {layer}.", layer)
        await world.active("Tests need a display", f"Set DISPLAY for {layer}.", layer)

    async def refused(messages: list[dict[str, Any]]) -> Any:
        if "Notes of: project beta" in messages[-1]["content"]:
            return {"operations": []}
        return TimeoutError("refused")

    world.llm.answer = refused

    await world.tidy()
    await world.tidy()

    asked = [call[-1]["content"].split("\n")[1] for call in world.llm.calls]
    assert asked[:2] == ["Notes of: all projects", "Notes of: project alpha"]
    assert "Notes of: project beta" in asked[2:]


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


async def test_a_guess_that_says_what_an_entry_in_effect_says_is_counted_there_and_its_text_left_out(world):
    one = await world.active("Build with make", "Run make in the root, not in src.")
    guess = await world.pending("Make needs the root", "make fails when started in src.", by="c7")
    # Whichever way round the model names them.
    world.answer(lambda shown: [{"op": "merge", "from": shown["Build with make"], "into": shown["Make needs the root"]}])

    await world.tidy()

    kept = await world.entry(one)
    assert kept.status == ENTRY_ACTIVE and "c7" in kept.seen_by
    # Nobody checked what the guess says beyond the entry: it is not put into it.
    assert "make fails when started in src." not in kept.content
    gone = await world.entry(guess)
    assert gone.status == ENTRY_EXPIRED and gone.outdated == "the same as Build with make"
    assert f"memory/{guess}.md" not in await world.space.read_index(None)


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


async def test_guesses_that_take_effect_as_one_wait_on_beside_an_entry_in_effect_of_their_title(world):
    first = await world.pending("Make needs the root", "make fails when started in src.", by="c1")
    await world.pending("Build only from top", "Building from a subdirectory breaks.", by="c2")
    # Filed from the console under that title: an entry of its own, beside the guess.
    one = await world.active("Make needs the root", "Run make in the root.")
    assert one != first and (await world.entry(first)).status == ENTRY_PENDING
    before = (world.memory() / f"{one}.md").read_text(encoding="utf-8")
    world.answer(lambda shown: [{"op": "merge", "from": shown["Build only from top"], "into": shown["Make needs the root"]}])

    await world.tidy()

    # The entry the change did not name is left as it was.
    assert (world.memory() / f"{one}.md").read_text(encoding="utf-8") == before
    kept = await world.entry(first)
    assert kept.status == ENTRY_PENDING and "Building from a subdirectory breaks." in kept.content


async def test_guesses_that_take_effect_as_one_leave_an_entry_of_their_title_the_user_wrote_alone(world):
    first = await world.pending("Make needs the root", "make fails when started in src.", by="c1")
    await world.pending("Build only from top", "Building from a subdirectory breaks.", by="c2")
    mine = world.memory() / "mine.md"
    mine.write_text("---\nname: Make needs the root\n---\n\nAlways run make from the top.\n", encoding="utf-8")
    world.answer(lambda shown: [{"op": "merge", "from": shown["Build only from top"], "into": shown["Make needs the root"]}])

    await world.tidy()

    assert mine.read_text(encoding="utf-8") == "---\nname: Make needs the root\n---\n\nAlways run make from the top.\n"
    kept = await world.entry(first)
    assert kept.status == ENTRY_PENDING and "Building from a subdirectory breaks." in kept.content


async def test_an_entry_merged_into_another_whose_file_cannot_go_is_merged_again_by_the_next_round(world, monkeypatch):
    one = await world.active("Build with make", "Run make in the root, not in src.")
    other = await world.active("Make from the root", "make only works from the repository root.")
    world.answer(lambda shown: [{"op": "merge", "from": shown["Make from the root"], "into": shown["Build with make"]}])
    # Held open by another program: it cannot be removed.
    monkeypatch.setattr(TaskAgentSpace, "_discard", staticmethod(lambda path: None))

    await world.tidy()

    assert (world.memory() / f"{other}.md").exists()
    assert f"memory/{other}.md" not in await world.space.read_index(None)
    monkeypatch.undo()

    await world.tidy()

    assert len(world.llm.calls) == 2
    assert not (world.memory() / f"{other}.md").exists()
    assert (await world.entry(one)).content.count("make only works from the repository root.") == 1


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


async def test_of_two_guesses_the_later_goes_into_the_older_whichever_way_round(world):
    older = await world.pending("Make needs the root", "make fails when started in src.", by="c1")
    world.clock.now += 5 * DAY
    newer = await world.pending("Build broke yesterday", "The build broke yesterday in a subdirectory.", by="c1")
    world.answer(lambda shown: [{"op": "merge", "from": shown["Make needs the root"], "into": shown["Build broke yesterday"]}])

    await world.tidy()

    assert not (world.memory() / f"{newer}.md").exists()
    kept = await world.entry(older)
    assert kept.created == "2027-01-15"
    assert "## 2027-01-20\n\nThe build broke yesterday in a subdirectory." in kept.content


async def test_nothing_is_merged_into_an_entry_saved_just_before_it_is_written(world, monkeypatch):
    from nekoclaw.host import space as space_module

    one = await world.active("Build with make", "Run make in the root.")
    other = await world.active("Make from the root", "make only works from the repository root.")
    path = world.memory() / f"{one}.md"
    recorded = space_module._recorded

    def saved_meanwhile(*args: Any, **kwargs: Any) -> Any:
        # Between the check of the entry and its write.
        path.write_text(path.read_text(encoding="utf-8") + "\nSaved by hand.\n", encoding="utf-8")
        return recorded(*args, **kwargs)

    monkeypatch.setattr(space_module, "_recorded", saved_meanwhile)
    world.answer(lambda shown: [{"op": "merge", "from": shown["Make from the root"], "into": shown["Build with make"]}])

    await world.tidy()

    assert "repository root" not in path.read_text(encoding="utf-8")
    assert (world.memory() / f"{other}.md").exists()


async def test_a_date_is_not_put_into_an_entry_saved_just_before_it_is_written(world, monkeypatch):
    from nekoclaw.host import space as space_module

    one = await world.active("Server down", "The staging server was down yesterday.")
    await world.active("Mirror", "The mirror is at mirror.local.")
    path = world.memory() / f"{one}.md"
    texts_of = space_module._texts_of
    saved: list[bool] = []

    def saved_meanwhile(*args: Any, **kwargs: Any) -> Any:
        # Between the check of the entry and its write.
        if not saved:
            saved.append(True)
            path.write_text(path.read_text(encoding="utf-8") + "\nSaved by hand.\n", encoding="utf-8")
        return texts_of(*args, **kwargs)

    monkeypatch.setattr(space_module, "_texts_of", saved_meanwhile)
    world.answer(lambda shown: [{"op": "date", "entry": shown["Server down"], "old": "yesterday", "new": "2027-01-14"}])

    await world.tidy()

    text = path.read_text(encoding="utf-8")
    assert saved and "Saved by hand." in text and "2027-01-14" not in text


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
    one = await world.active("Tests", "Run pytest with -n 4, in parallel.")
    await world.active("Testing", "Tests need DISPLAY set.")
    world.answer(
        lambda shown: [
            {"op": "retitle", "entry": shown["Tests"], "apart_from": shown["Testing"], "title": "Tests in parallel"}
        ]
    )

    await world.tidy()

    renamed = await world.entry(one)
    assert (renamed.title, renamed.description) == ("Tests in parallel", "Run pytest with -n 4, in parallel.")
    assert f"- [Tests in parallel](memory/{one}.md) — Run pytest with -n 4, in parallel." in await world.space.read_index(None)


async def test_no_entry_is_given_the_title_of_another(world):
    # Its title with words of its own makes the other's title.
    one = await world.active("Tests", "Run them in parallel: pytest with -n 4.")
    await world.active("Tests in parallel", "Tests need DISPLAY set.")
    world.answer(
        lambda shown: [
            {"op": "retitle", "entry": shown["Tests"], "apart_from": shown["Tests in parallel"], "title": "Tests in parallel"}
        ]
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


async def test_an_entry_is_not_retitled_apart_from_one_that_changed_meanwhile(world):
    one = await world.active("Tests", "Run pytest with -n 4.")
    other = await world.active("Testing", "Tests need DISPLAY set.")
    layer = await world.space.read_layer(None)
    path = world.memory() / f"{other}.md"
    path.write_text(path.read_text(encoding="utf-8") + "\nSaved by hand.\n", encoding="utf-8")

    outcome, _changed = await world.space.tidy_entries(
        None, {"op": "retitle", "entry": one, "apart_from": other, "title": "Parallel tests"}, layer.marks
    )

    assert "changed since" in outcome
    assert (await world.entry(one)).title == "Tests"


async def test_no_entry_is_given_a_title_the_user_removed(world):
    one = await world.active("Tests", "Run them in parallel: pytest with -n 4.")
    await world.active("Testing", "Tests need DISPLAY set.")
    gone = await world.active("Tests in parallel", "Old advice.")
    assert await world.space.delete_entry(gone)
    world.answer(
        lambda shown: [{"op": "retitle", "entry": shown["Tests"], "apart_from": shown["Testing"], "title": "Tests in parallel"}]
    )

    await world.tidy()

    assert (await world.entry(one)).title == "Tests"


async def test_two_changes_of_one_entry_are_both_made(world):
    one = await world.active("Server", "The staging server was down yesterday.")
    await world.active("Server status", "Look at status.local first.")
    world.answer(
        lambda shown: [
            {"op": "date", "entry": shown["Server"], "old": "yesterday", "new": "2027-01-14"},
            {"op": "retitle", "entry": shown["Server"], "apart_from": shown["Server status"], "title": "Staging Server"},
        ]
    )

    await world.tidy()

    entry = await world.entry(one)
    assert entry.title == "Staging Server" and "2027-01-14" in entry.content


async def test_a_date_in_the_second_text_of_an_entry_keeps_the_texts_apart(world):
    from nekoclaw.host.space import _digest, _listed, _parse_document, _texts_of

    one = await world.active("Server down", "The staging server is flaky.")
    world.clock.now += DAY
    await world.space.file_entry(None, title="Server down", content="It was down yesterday; use the mirror.")
    await world.active("Mirror", "The mirror is at mirror.local.")
    world.answer(lambda shown: [{"op": "date", "entry": shown["Server down"], "old": "yesterday", "new": "2027-01-15"}])

    await world.tidy()

    document = _parse_document((world.memory() / f"{one}.md").read_text(encoding="utf-8"))
    texts = _texts_of(document.body, _listed(document, "texts"))
    assert texts == ["The staging server is flaky.", "It was down 2027-01-15; use the mirror."]
    assert _listed(document, "texts") == [_digest(text) for text in texts]


async def test_yesterday_is_replaced_by_its_date_and_the_text_is_still_known(world):
    one = await world.active("Server down", "The staging server was down yesterday; use the mirror.")
    await world.active("Mirror", "The mirror is at mirror.local.")
    world.answer(
        lambda shown: [{"op": "date", "entry": shown["Server down"], "old": "yesterday", "new": "2027-01-14"}]
    )

    await world.tidy()

    entry = await world.entry(one)
    assert entry.content == "The staging server was down 2027-01-14; use the mirror."
    # The same text filed again is there already, not a second section.
    again, created = await world.space.file_entry(
        None, title="Server down", content="The staging server was down 2027-01-14; use the mirror."
    )
    assert not created and again.content == entry.content


async def test_words_that_stand_twice_or_a_replacement_with_no_date_are_left(world):
    one = await world.active("Server down", "Down yesterday, and up yesterday evening.")
    await world.active("Mirror", "The mirror is at mirror.local.")
    world.answer(
        lambda shown: [
            {"op": "date", "entry": shown["Server down"], "old": "yesterday", "new": "2027-01-14"},
            {"op": "date", "entry": shown["Server down"], "old": "Down yesterday", "new": "Down the day before"},
            {"op": "date", "entry": shown["Server down"], "old": "Down yesterday", "new": "Down on 2027-02-30"},
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
        return {"operations": [{"op": "date", "entry": handles(messages)["Server down"], "old": "yesterday", "new": "2027-01-14"}]}

    world.llm.answer = meanwhile

    await world.tidy()

    assert "2027-01-14" not in path.read_text(encoding="utf-8")
    assert "Saved by hand." in path.read_text(encoding="utf-8")


async def test_an_entry_the_user_wrote_is_never_changed(world):
    await world.active("Build with make", "Run make in the root.")
    await world.active("Mirror", "The mirror is at mirror.local.")
    mine = world.memory() / "mine.md"
    mine.write_text("---\nname: Mine\n---\n\nThe server was down yesterday.\n", encoding="utf-8")
    layer = await world.space.read_layer(None)

    outcome, changed = await world.space.tidy_entries(
        None, {"op": "date", "entry": "mine", "old": "yesterday", "new": "2027-01-14"}, layer.marks
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
            {"op": "date", "entry": shown["Server down"], "old": "yesterday", "new": "2027-01-14"},
            {"op": "date", "entry": shown["Mirror down"], "old": "yesterday", "new": "2027-01-14"},
        ]
    )

    await world.tidy()

    assert (await world.entry(one)).content == "Down 2027-01-14."
    assert (await world.entry(two)).content == "The mirror was down yesterday."


async def test_of_an_answer_cut_off_the_whole_changes_are_made(world):
    one = await world.active("Server down", "Down yesterday.")
    two = await world.active("Mirror down", "The mirror was down yesterday.")

    async def cut(messages: list[dict[str, Any]]) -> str:
        shown = handles(messages)
        return (
            '{"operations": [{"op": "date", "entry": "%s", "old": "yesterday", "new": "2027-01-14"}, '
            '{"op": "date", "entry": "%s", "old": "yester' % (shown["Server down"], shown["Mirror down"])
        )

    world.llm.answer = cut

    await world.tidy()

    assert (await world.entry(one)).content == "Down 2027-01-14."
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

    messages, shown, _whole = _consolidate_messages(look, [entry.id for entry in entries], world.clock.now)

    system, user = messages[0]["content"], messages[1]["content"]
    assert count_tokens(system) + count_tokens(user) <= settings.SPACE_CONSOLIDATE_INPUT_MAX_TOKENS
    # Not cut at the end: the parts fit by their own caps.
    assert user.rstrip().endswith("…") is False
    assert shown and all(handle in user for handle in shown)


def test_the_longest_answer_that_may_be_kept_fits_the_cap_on_output():
    from config import nekoclaw_settings as settings
    from utils.tokenize import count_tokens

    def within(text: str, cap: int) -> str:
        while count_tokens(text) > cap:
            text = text[:-1]
        return text

    # The longest of each kind that may be kept: a title, a reason.
    kinds = [
        {"op": "retitle", "entry": "n12", "apart_from": "n13", "title": within("标题" * 40, settings.SPACE_ENTRY_TITLE_MAX_TOKENS)},
        {"op": "outdated", "entry": "n12", "by": "n13", "why": within("原因" * 60, settings.SPACE_CONSOLIDATE_REASON_MAX_TOKENS)},
        {"op": "date", "entry": "n12", "old": "上个月", "new": "2027-01-14"},
        {"op": "merge", "from": "n12", "into": "n13"},
    ]
    one = max(kinds, key=lambda kind: count_tokens(json.dumps(kind, ensure_ascii=False)))
    full = count_tokens(
        json.dumps({"operations": [one] * settings.SPACE_CONSOLIDATE_MAX_OPERATIONS}, ensure_ascii=False, indent=1)
    )
    assert full <= settings.SPACE_CONSOLIDATE_OUTPUT_MAX_TOKENS
    assert 1.2 <= settings.SPACE_CONSOLIDATE_OUTPUT_MAX_TOKENS / full < 1.4, full


# ---------------------------------------------------------------------------
# What a pending note shown to the model cannot have changed
# ---------------------------------------------------------------------------


async def test_a_title_with_words_the_entry_does_not_have_is_not_given(world):
    one = await world.active("Git", "Open a pull request for every change.")
    await world.active("Git hooks", "The hooks live in .githooks.")
    two = await world.active("Deploy", "Deploy fast, never in prod.")
    world.answer(
        lambda shown: [
            {"op": "retitle", "entry": shown["Git"], "apart_from": shown["Git hooks"], "title": "Always push to main directly"},
            # Words of its own on both sides of its title: no run of its words.
            {"op": "retitle", "entry": shown["Deploy"], "apart_from": shown["Git hooks"], "title": "fast Deploy never"},
        ]
    )

    await world.tidy()

    entry = await world.entry(one)
    assert entry.title == "Git"
    assert "push to main" not in await world.space.read_index(None)
    assert (await world.entry(two)).title == "Deploy"


async def test_a_date_change_changes_nothing_but_when(world):
    one = await world.active("Cleanup", "Never run rm -rf yesterday's way; ask first.")
    two = await world.active("Server down", "The staging server was down yesterday.")
    three = await world.active("服务器", "测试服务器昨天挂了，改用镜像。")
    four = await world.active("Deploy", "Deploy ran yesterday.")
    world.answer(
        lambda shown: [
            {"op": "date", "entry": shown["Cleanup"], "old": "Never run rm -rf yesterday", "new": "2027-01-14"},
            {"op": "date", "entry": shown["Server down"], "old": "yesterday", "new": "2027-01-14; reboot"},
            {"op": "date", "entry": shown["Deploy"], "old": "yesterday", "new": "2027-02-30"},
            {"op": "date", "entry": shown["服务器"], "old": "昨天", "new": "2027-01-14"},
        ]
    )

    await world.tidy()

    assert (await world.entry(one)).content == "Never run rm -rf yesterday's way; ask first."
    assert (await world.entry(two)).content == "The staging server was down yesterday."
    assert (await world.entry(four)).content == "Deploy ran yesterday."
    assert (await world.entry(three)).content == "测试服务器2027-01-14挂了，改用镜像。"


async def test_a_merge_goes_on_when_its_target_was_only_touched_as_it_was_written(world, monkeypatch):
    from nekoclaw.host import space as space_module

    one = await world.active("Build with make", "Run make in the root.")
    other = await world.active("Make from the root", "make only works from the repository root.")
    target = world.memory() / f"{one}.md"
    put_text = TaskAgentSpace._put_text
    touched: list[bool] = []

    def touched_once(path: Path, text: str) -> bool:
        if path == target and not touched:
            # Saved over at once by a program that wrote back the very same text.
            touched.append(True)
            raise space_module._NotAsWritten("touched")
        return put_text(path, text)

    monkeypatch.setattr(TaskAgentSpace, "_put_text", staticmethod(touched_once))
    world.answer(lambda shown: [{"op": "merge", "from": shown["Make from the root"], "into": shown["Build with make"]}])

    await world.tidy()

    assert touched
    assert "make only works from the repository root." in target.read_text(encoding="utf-8")
    assert not (world.memory() / f"{other}.md").exists()


async def test_a_new_entry_whose_text_did_not_fit_is_news_to_the_next_round(world, monkeypatch):
    await world.active("Build with make", "Run make in the root, not in src.")
    await world.active("Tests need a display", "Set DISPLAY before running the UI tests.")
    # Room for one text only.
    block = "[n1] Build with make (created 2027-01-15, updated 2027-01-15):\nRun make in the root, not in src."
    from utils.tokenize import count_tokens

    monkeypatch.setattr("config.nekoclaw_settings.SPACE_CONSOLIDATE_TEXTS_MAX_TOKENS", count_tokens(block) + 4)

    await world.tidy()
    first = world.llm.calls[-1][-1]["content"]
    await world.tidy()

    assert len(world.llm.calls) == 2
    second = world.llm.calls[-1][-1]["content"]
    in_first = "Set DISPLAY before running the UI tests." in first.split("Full texts")[1]
    texts_now = second.split("Full texts")[1]
    assert ("Set DISPLAY before running the UI tests." in texts_now) != in_first


async def test_guesses_that_take_effect_as_one_wait_on_beside_an_expired_entry_of_their_title(world):
    first = await world.pending("Make needs the root", "make fails when started in src.", by="c1")
    second = await world.pending("Build only from top", "Building from a subdirectory breaks.", by="c2")
    one = await world.active("Make needs the root", "Run make in the root.")
    path = world.memory() / f"{one}.md"
    path.write_text(path.read_text(encoding="utf-8").replace("---\n\n", "status: expired\nexpired: 2027-01-01\n---\n\n", 1), encoding="utf-8")
    world.answer(lambda shown: [{"op": "merge", "from": shown["Build only from top"], "into": shown["Make needs the root"]}])

    await world.tidy()

    # Merged, and seen by both: it would take effect but for the other entry of its title.
    assert not (world.memory() / f"{second}.md").exists()
    kept = await world.entry(first)
    assert "Building from a subdirectory breaks." in kept.content and set(kept.seen_by) == {"c1", "c2"}
    assert kept.status == ENTRY_PENDING


async def test_guesses_that_take_effect_as_one_wait_on_while_an_entry_beside_them_cannot_be_read(world, monkeypatch):
    first = await world.pending("Make needs the root", "make fails when started in src.", by="c1")
    await world.pending("Build only from top", "Building from a subdirectory breaks.", by="c2")
    other = await world.active("Mirror", "The mirror is at mirror.local.")
    held = world.memory() / f"{other}.md"
    world.answer(lambda shown: [{"op": "merge", "from": shown["Build only from top"], "into": shown["Make needs the root"]}])
    read_text = TaskAgentSpace._read_text
    reads: list[Path] = []

    def held_open(path: Path) -> Any:
        # Held by another program once the merge is written: it may be of the same title.
        if path == held and any(item.stem == first for item in reads):
            return None
        reads.append(path)
        return read_text(path)

    monkeypatch.setattr(TaskAgentSpace, "_read_text", staticmethod(held_open))

    await world.tidy()

    kept = await world.entry(first)
    assert set(kept.seen_by) == {"c1", "c2"} and kept.status == ENTRY_PENDING

    # Readable again: the next round settles it, with no model.
    monkeypatch.undo()
    calls = len(world.llm.calls)
    world.llm.answer = {"operations": []}
    await world.tidy()

    assert (await world.entry(first)).status == ENTRY_ACTIVE
    assert f"memory/{first}.md" in await world.space.read_index(None)
    assert len(world.llm.calls) - calls <= 1


async def test_a_time_that_counts_is_left(world):
    one = await world.active("Outage", "The mirror went down 3 days ago.")
    await world.active("Mirror", "The mirror is at mirror.local.")
    world.answer(
        lambda shown: [
            {"op": "date", "entry": shown["Outage"], "old": "3 days ago", "new": "2027-01-12"},
            {"op": "date", "entry": shown["Outage"], "old": "days ago", "new": "2027-01-12"},
        ]
    )

    await world.tidy()

    assert (await world.entry(one)).content == "The mirror went down 3 days ago."


async def test_a_relative_time_inside_a_name_is_left(world):
    one = await world.active("Report", "Use todayTotal for the report, as of today.")
    two = await world.active("Totals", "Use todayTotal for the report.")
    world.answer(
        lambda shown: [
            {"op": "date", "entry": shown["Totals"], "old": "today", "new": "2027-01-15"},
            {"op": "date", "entry": shown["Report"], "old": "today", "new": "2027-01-15"},
        ]
    )

    await world.tidy()

    assert (await world.entry(two)).content == "Use todayTotal for the report."
    assert (await world.entry(one)).content == "Use todayTotal for the report, as of 2027-01-15."


async def test_two_entries_in_effect_merged_keep_their_days(world):
    one = await world.active("Build with make", "Run make in the root, not in src.")
    world.clock.now += 3 * DAY
    await world.active("Make from the root", "make only works from the repository root.")
    world.clock.now += 10 * DAY
    world.answer(lambda shown: [{"op": "merge", "from": shown["Make from the root"], "into": shown["Build with make"]}])

    await world.tidy()

    kept = await world.entry(one)
    # Not made newer by being merged: what is newer decides what is out of date.
    assert (kept.created, kept.updated) == ("2027-01-15", "2027-01-18")


async def test_guesses_merged_and_saved_before_they_take_effect_wait_on(world, monkeypatch):
    first = await world.pending("Make needs the root", "make fails when started in src.", by="c1")
    await world.pending("Build only from top", "Building from a subdirectory breaks.", by="c2")
    path = world.memory() / f"{first}.md"
    discard_as_read = TaskAgentSpace._discard_as_read

    def saved_meanwhile(self: Any, source: Path, before: Any) -> bool:
        # Right after the merge wrote into the guess, before it would take effect.
        path.write_text(path.read_text(encoding="utf-8") + "\nSaved by hand.\n", encoding="utf-8")
        return discard_as_read(self, source, before)

    monkeypatch.setattr(TaskAgentSpace, "_discard_as_read", saved_meanwhile)
    world.answer(lambda shown: [{"op": "merge", "from": shown["Build only from top"], "into": shown["Make needs the root"]}])

    await world.tidy()

    kept = await world.entry(first)
    assert kept.status == ENTRY_PENDING and "Saved by hand." in kept.content


async def test_a_layer_that_cannot_be_read_does_not_keep_the_others_from_being_looked_over(world, monkeypatch):
    await world.space.open_project("alpha")
    for layer in (None, "alpha"):
        await world.active("Build with make", f"Run make in the root of {layer}.", layer)
        await world.active("Tests need a display", f"Set DISPLAY for {layer}.", layer)
    read_layer = TaskAgentSpace.read_layer

    async def unreadable(self: Any, project: Any) -> Any:
        if project is None:
            raise ValueError("no name the space takes")
        return await read_layer(self, project)

    monkeypatch.setattr(TaskAgentSpace, "read_layer", unreadable)

    await world.tidy()

    assert [call[-1]["content"].split("\n")[1] for call in world.llm.calls] == ["Notes of: project alpha"]


async def test_the_entries_of_a_change_that_failed_half_way_are_news_to_the_next_round(world, monkeypatch):
    one = await world.active("Server down", "The staging server was down yesterday.")
    await world.active("Mirror", "The mirror is at mirror.local.")
    world.answer(lambda shown: [{"op": "date", "entry": shown["Server down"], "old": "yesterday", "new": "2027-01-14"}])
    tidy_entries = TaskAgentSpace.tidy_entries

    async def failed(self: Any, *args: Any) -> Any:
        raise OSError("held by another program")

    monkeypatch.setattr(TaskAgentSpace, "tidy_entries", failed)
    await world.tidy()
    monkeypatch.setattr(TaskAgentSpace, "tidy_entries", tidy_entries)

    await world.tidy()

    assert len(world.llm.calls) == 2
    assert (await world.entry(one)).content == "The staging server was down 2027-01-14."


async def test_an_entry_is_not_out_of_date_by_one_saved_as_it_is_written(world, monkeypatch):
    from nekoclaw.host import space as space_module

    old = await world.active("Build with make", "Run make in the root.")
    world.clock.now += DAY
    newer = await world.active("Build with ninja", "The build moved to ninja; make is gone.")
    newer_path = world.memory() / f"{newer}.md"
    cut = space_module._cut
    saved: list[bool] = []

    def saved_meanwhile(*args: Any, **kwargs: Any) -> Any:
        # As the reason is put in words: after the two were checked, before the write.
        if not saved:
            saved.append(True)
            newer_path.write_text(newer_path.read_text(encoding="utf-8") + "\nmake is back after all.\n", encoding="utf-8")
        return cut(*args, **kwargs)

    world.answer(lambda shown: [{"op": "outdated", "entry": shown["Build with make"], "by": shown["Build with ninja"], "why": "moved to ninja"}])
    monkeypatch.setattr(space_module, "_cut", saved_meanwhile)

    await world.tidy()

    assert saved
    assert (await world.entry(old)).status == ENTRY_ACTIVE


async def test_an_entry_saved_as_its_line_is_taken_out_keeps_its_line(world, monkeypatch):
    one = await world.active("Build with make", "Run make in the root, not in src.")
    other = await world.active("Make from the root", "make only works from the repository root.")
    other_path = world.memory() / f"{other}.md"
    index_remove = TaskAgentSpace._index_remove

    def saved_meanwhile(self: Any, index: Path, target: str, **kwargs: Any) -> Any:
        removed = index_remove(self, index, target, **kwargs)
        if other in target:
            other_path.write_text(other_path.read_text(encoding="utf-8") + "\nSaved by hand.\n", encoding="utf-8")
        return removed

    monkeypatch.setattr(TaskAgentSpace, "_index_remove", saved_meanwhile)
    world.answer(lambda shown: [{"op": "merge", "from": shown["Make from the root"], "into": shown["Build with make"]}])

    await world.tidy()

    assert "Saved by hand." in other_path.read_text(encoding="utf-8")
    assert f"memory/{other}.md" in await world.space.read_index(None)
    assert f"memory/{one}.md" in await world.space.read_index(None)


async def test_an_entry_is_not_told_apart_from_one_that_went_out_of_date_this_round(world):
    one = await world.active("Tests", "Testing: run pytest with -n 4, in parallel.")
    await world.active("Testing", "Tests need DISPLAY set.")
    world.clock.now += DAY
    await world.active("Testing now", "Tests run headless now; DISPLAY is not needed.")
    world.answer(
        lambda shown: [
            {"op": "outdated", "entry": shown["Testing"], "by": shown["Testing now"], "why": "headless now"},
            {"op": "retitle", "entry": shown["Tests"], "apart_from": shown["Testing"], "title": "Tests in parallel"},
        ]
    )

    await world.tidy()

    assert (await world.entry(one)).title == "Tests"


async def test_a_new_title_keeps_what_the_user_wrote_in_the_line_of_the_index(world):
    one = await world.active("Tests", "Run them in parallel: pytest with -n 4.")
    await world.active("Testing", "Tests need DISPLAY set.")
    index = world.memory().parent / "MEMORY.md"
    text = index.read_text(encoding="utf-8")
    line = next(item for item in text.split("\n") if f"memory/{one}.md" in item)
    index.write_text(text.replace(line, f"- [Tests](memory/{one}.md) — never against production"), encoding="utf-8")
    world.answer(
        lambda shown: [{"op": "retitle", "entry": shown["Tests"], "apart_from": shown["Testing"], "title": "Tests in parallel"}]
    )

    await world.tidy()

    assert (await world.entry(one)).title == "Tests in parallel"
    assert f"- [Tests in parallel](memory/{one}.md) — never against production" in await world.space.read_index(None)


async def test_a_new_title_keeps_the_old_one_as_it_is_written(world):
    one = await world.active("Set MODE=SAFE", "Run this locally before a release.")
    await world.active("Release checklist", "Tag, build, upload.")
    world.answer(
        lambda shown: [
            {"op": "retitle", "entry": shown["Set MODE=SAFE"], "apart_from": shown["Release checklist"], "title": "Set MODE=safe locally"}
        ]
    )

    await world.tidy()

    assert len(world.llm.calls) == 1
    assert (await world.entry(one)).title == "Set MODE=SAFE"


async def test_a_merged_guess_the_user_saved_before_it_was_settled_waits_on(world, monkeypatch):
    first = await world.pending("Make needs the root", "make fails when started in src.", by="c1")
    await world.pending("Build only from top", "Building from a subdirectory breaks.", by="c2")
    other = await world.active("Mirror", "The mirror is at mirror.local.")
    _held_after_reading(monkeypatch, world.memory() / f"{other}.md", first)
    world.answer(lambda shown: [{"op": "merge", "from": shown["Build only from top"], "into": shown["Make needs the root"]}])
    await world.tidy()
    monkeypatch.undo()
    path = world.memory() / f"{first}.md"
    # Merged, and left to be settled.
    assert "Building from a subdirectory breaks." in path.read_text(encoding="utf-8")
    assert (None, first) in world.space._unsettled
    # Saved by the user before a later round settles it.
    path.write_text(path.read_text(encoding="utf-8") + "\nAnd always delete the build folder first.\n", encoding="utf-8")
    world.llm.answer = {"operations": []}

    await world.tidy()

    kept = await world.entry(first)
    assert kept.status == ENTRY_PENDING
    assert f"memory/{first}.md" not in await world.space.read_index(None)
    # Nothing is left to settle: what the user saved waits like any other guess.
    assert world.space._unsettled == {}


async def test_an_entry_merged_into_one_saved_right_after_stays(world, monkeypatch):
    one = await world.active("Build with make", "Run make in the root.")
    other = await world.active("Make from the root", "make only works from the repository root.")
    target = world.memory() / f"{one}.md"
    file_entry = TaskAgentSpace._file_entry
    merged: list[str] = []

    def saved_after(self: Any, *args: Any, **kwargs: Any) -> Any:
        done = file_entry(self, *args, **kwargs)
        merged.append(target.read_text(encoding="utf-8"))
        # Saved by the user right after the text went in, without it.
        target.write_text(merged[-1].split("## ")[0].rstrip() + "\n\nSaved by hand.\n", encoding="utf-8")
        return done

    monkeypatch.setattr(TaskAgentSpace, "_file_entry", saved_after)
    world.answer(lambda shown: [{"op": "merge", "from": shown["Make from the root"], "into": shown["Build with make"]}])

    await world.tidy()

    assert merged and "make only works from the repository root." in merged[0]
    assert (world.memory() / f"{other}.md").exists()
    assert f"memory/{other}.md" in await world.space.read_index(None)


async def test_an_entry_merged_into_one_saved_as_its_line_was_taken_out_stays(world, monkeypatch):
    one = await world.active("Build with make", "Run make in the root.")
    other = await world.active("Make from the root", "make only works from the repository root.")
    target = world.memory() / f"{one}.md"
    index_remove = TaskAgentSpace._index_remove
    saved: list[str] = []

    def saved_meanwhile(self: Any, index: Path, line_of: str, **kwargs: Any) -> Any:
        removed = index_remove(self, index, line_of, **kwargs)
        if line_of == f"memory/{other}.md":
            saved.append(target.read_text(encoding="utf-8"))
            target.write_text(saved[-1].split("## ")[0].rstrip() + "\n\nSaved by hand.\n", encoding="utf-8")
        return removed

    monkeypatch.setattr(TaskAgentSpace, "_index_remove", saved_meanwhile)
    world.answer(lambda shown: [{"op": "merge", "from": shown["Make from the root"], "into": shown["Build with make"]}])

    await world.tidy()

    assert saved and "make only works from the repository root." in saved[0]
    assert (world.memory() / f"{other}.md").exists()
    assert f"memory/{other}.md" in await world.space.read_index(None)


async def test_a_date_put_into_the_text_is_put_into_the_description_made_from_it(world):
    one = await world.active("Server down", "The staging server was down yesterday.")
    await world.active("Mirror", "The mirror is at mirror.local.")
    world.answer(lambda shown: [{"op": "date", "entry": shown["Server down"], "old": "yesterday", "new": "2027-01-14"}])

    await world.tidy()

    entry = await world.entry(one)
    assert entry.description == "The staging server was down 2027-01-14."
    assert f"- [Server down](memory/{one}.md) — The staging server was down 2027-01-14." in await world.space.read_index(None)


async def test_a_date_leaves_a_description_the_user_wrote(world):
    one = await world.active("Backup", "The backup ran yesterday.")
    await world.active("Mirror", "The mirror is at mirror.local.")
    path = world.memory() / f"{one}.md"
    text = path.read_text(encoding="utf-8")
    assert "description: The backup ran yesterday." in text
    # Another day's "yesterday", in the user's words.
    path.write_text(
        text.replace("description: The backup ran yesterday.", "description: The restore drill ran yesterday."),
        encoding="utf-8",
    )
    world.answer(lambda shown: [{"op": "date", "entry": shown["Backup"], "old": "yesterday", "new": "2027-01-14"}])

    await world.tidy()

    assert "The backup ran 2027-01-14." in path.read_text(encoding="utf-8")
    assert (await world.entry(one)).description == "The restore drill ran yesterday."


async def test_a_date_leaves_a_line_of_the_index_the_user_reworded(world):
    one = await world.active("Server down", "The staging server was down yesterday.")
    await world.active("Mirror", "The mirror is at mirror.local.")
    index = world.memory().parent / "MEMORY.md"
    text = index.read_text(encoding="utf-8")
    line = next(item for item in text.split("\n") if f"memory/{one}.md" in item)
    index.write_text(text.replace(line, f"- [Server down](memory/{one}.md) — mine: down yesterday, use the mirror"), encoding="utf-8")
    world.answer(lambda shown: [{"op": "date", "entry": shown["Server down"], "old": "yesterday", "new": "2027-01-14"}])

    await world.tidy()

    assert "The staging server was down 2027-01-14." in (world.memory() / f"{one}.md").read_text(encoding="utf-8")
    assert "mine: down yesterday, use the mirror" in await world.space.read_index(None)


async def test_a_title_whose_entry_could_not_be_written_leaves_the_line_as_it_was(world, monkeypatch):
    one = await world.active("Tests", "Run them in parallel: pytest with -n 4.")
    await world.active("Testing", "Tests need DISPLAY set.")
    index_path = world.memory().parent / "MEMORY.md"
    text = index_path.read_text(encoding="utf-8")
    line = next(item for item in text.split("\n") if f"memory/{one}.md" in item)
    # Called otherwise by the user there.
    mine = f"- [Parallel tests](memory/{one}.md) — mine: run them with -n 4"
    index_path.write_text(text.replace(line, mine), encoding="utf-8")
    edit_as_shown = TaskAgentSpace._edit_as_shown
    meanwhile: list[str] = []

    def held(self: Any, path: Path, *args: Any) -> Any:
        if path.stem == one:
            meanwhile.append(index_path.read_text(encoding="utf-8"))
            raise OSError("held by another program")
        return edit_as_shown(self, path, *args)

    monkeypatch.setattr(TaskAgentSpace, "_edit_as_shown", held)
    world.answer(
        lambda shown: [{"op": "retitle", "entry": shown["Tests"], "apart_from": shown["Testing"], "title": "Tests in parallel"}]
    )

    await world.tidy()

    assert meanwhile and f"- [Tests in parallel](memory/{one}.md) — mine: run them with -n 4" in meanwhile[0]
    index = await world.space.read_index(None)
    assert mine in index and "Tests in parallel" not in index
    assert (await world.entry(one)).title == "Tests"


async def test_a_title_puts_no_line_the_user_took_out_into_the_index(world):
    one = await world.active("Tests", "Run them in parallel: pytest with -n 4.")
    await world.active("Testing", "Tests need DISPLAY set.")
    index_path = world.memory().parent / "MEMORY.md"
    text = index_path.read_text(encoding="utf-8")
    index_path.write_text("\n".join(item for item in text.split("\n") if f"memory/{one}.md" not in item), encoding="utf-8")
    world.answer(
        lambda shown: [{"op": "retitle", "entry": shown["Tests"], "apart_from": shown["Testing"], "title": "Tests in parallel"}]
    )

    await world.tidy()

    assert (await world.entry(one)).title == "Tests in parallel"
    assert f"memory/{one}.md" not in await world.space.read_index(None)


async def test_one_merged_guess_that_cannot_be_settled_does_not_keep_another_waiting(world, monkeypatch):
    first = await world.pending("Make needs the root", "make fails when started in src.", by="c1")
    await world.pending("Build only from top", "Building from a subdirectory breaks.", by="c2")
    second = await world.pending("Lint before commit", "Run ruff before every commit.", by="c1")
    await world.pending("Style checks first", "Style checks must pass before committing.", by="c2")
    other = await world.active("Mirror", "The mirror is at mirror.local.")
    _held_after_reading(monkeypatch, world.memory() / f"{other}.md", first)
    world.answer(
        lambda shown: [
            {"op": "merge", "from": shown["Build only from top"], "into": shown["Make needs the root"]},
            {"op": "merge", "from": shown["Style checks first"], "into": shown["Lint before commit"]},
        ]
    )
    await world.tidy()
    monkeypatch.undo()
    assert {(None, first), (None, second)} <= set(world.space._unsettled)
    tidy_confirm = TaskAgentSpace._tidy_confirm

    def fails_for_first(self: Any, project: Any, entry: Any, *args: Any) -> Any:
        if entry.id == first:
            raise OSError("held by another program")
        return tidy_confirm(self, project, entry, *args)

    monkeypatch.setattr(TaskAgentSpace, "_tidy_confirm", fails_for_first)

    assert await world.space.settle_tidied(None) == 1

    # The one that failed is kept for later; the one after it took effect all the same.
    assert (None, first) in world.space._unsettled
    assert (None, second) not in world.space._unsettled
    assert (await world.entry(second)).status == ENTRY_ACTIVE
    assert (await world.entry(first)).status == ENTRY_PENDING


def _held_after_reading(monkeypatch: Any, held: Path, first: str) -> None:
    """Have ``held`` not to be read once ``first`` has been: an entry beside a merged guess, held by another program."""
    read_text = TaskAgentSpace._read_text
    reads: list[Path] = []

    def held_open(path: Path) -> Any:
        if path == held and any(item.stem == first for item in reads):
            return None
        reads.append(path)
        return read_text(path)

    monkeypatch.setattr(TaskAgentSpace, "_read_text", staticmethod(held_open))


async def test_a_merged_guess_dated_in_the_same_round_still_takes_effect_later(world, monkeypatch):
    first = await world.pending("Make needs the root", "make failed yesterday when started in src.", by="c1")
    await world.pending("Build only from top", "Building from a subdirectory breaks.", by="c2")
    other = await world.active("Mirror", "The mirror is at mirror.local.")
    _held_after_reading(monkeypatch, world.memory() / f"{other}.md", first)
    world.answer(
        lambda shown: [
            {"op": "merge", "from": shown["Build only from top"], "into": shown["Make needs the root"]},
            {"op": "date", "entry": shown["Make needs the root"], "old": "yesterday", "new": "2027-01-14"},
        ]
    )
    await world.tidy()
    monkeypatch.undo()
    dated = (world.memory() / f"{first}.md").read_text(encoding="utf-8")
    assert "2027-01-14" in dated
    assert (await world.entry(first)).status == ENTRY_PENDING
    assert world.space._unsettled == {(None, first): _text_mark(dated)}
    world.llm.answer = {"operations": []}

    await world.tidy()

    assert (await world.entry(first)).status == ENTRY_ACTIVE
    assert world.space._unsettled == {}


async def test_a_merged_guess_the_user_saved_and_tidying_dated_waits_on(world, monkeypatch):
    first = await world.pending("Make needs the root", "make failed yesterday when started in src.", by="c1")
    await world.pending("Build only from top", "Building from a subdirectory breaks.", by="c2")
    other = await world.active("Mirror", "The mirror is at mirror.local.")
    # Enough beside it to be looked over while the mirror cannot be read.
    await world.active("Docs", "The docs are built with mkdocs.")
    _held_after_reading(monkeypatch, world.memory() / f"{other}.md", first)
    world.answer(lambda shown: [{"op": "merge", "from": shown["Build only from top"], "into": shown["Make needs the root"]}])
    await world.tidy()
    path = world.memory() / f"{first}.md"
    path.write_text(path.read_text(encoding="utf-8") + "\nAnd always delete the build folder first.\n", encoding="utf-8")
    world.answer(lambda shown: [{"op": "date", "entry": shown["Make needs the root"], "old": "yesterday", "new": "2027-01-14"}])
    await world.tidy()
    monkeypatch.undo()
    assert "2027-01-14" in path.read_text(encoding="utf-8")
    world.llm.answer = {"operations": []}

    await world.tidy()

    assert (await world.entry(first)).status == ENTRY_PENDING


async def test_a_merged_guess_that_took_effect_gets_its_line_by_a_later_round(world, monkeypatch):
    first = await world.pending("Make needs the root", "make fails when started in src.", by="c1")
    await world.pending("Build only from top", "Building from a subdirectory breaks.", by="c2")
    await world.active("Mirror", "The mirror is at mirror.local.")
    index_add = TaskAgentSpace._index_add
    failed: list[bool] = []

    def fails_once(self: Any, index: Path, line: str, target: str) -> None:
        if target == f"memory/{first}.md" and not failed:
            failed.append(True)
            raise OSError("held by another program")
        index_add(self, index, line, target)

    monkeypatch.setattr(TaskAgentSpace, "_index_add", fails_once)
    world.answer(lambda shown: [{"op": "merge", "from": shown["Build only from top"], "into": shown["Make needs the root"]}])
    await world.tidy()
    assert failed
    assert (await world.entry(first)).status == ENTRY_ACTIVE
    assert f"memory/{first}.md" not in await world.space.read_index(None)
    world.llm.answer = {"operations": []}

    await world.tidy()

    assert f"[Make needs the root](memory/{first}.md)" in await world.space.read_index(None)
    assert world.space._relines == {}


async def test_an_entry_merged_into_gets_its_missing_line_by_a_later_round(world, monkeypatch):
    one = await world.active("Build with make", "Run make in the root.")
    await world.active("Make from the root", "make only works from the repository root.")
    index_path = world.memory().parent / "MEMORY.md"
    text = index_path.read_text(encoding="utf-8")
    index_path.write_text("\n".join(item for item in text.split("\n") if f"memory/{one}.md" not in item), encoding="utf-8")
    index_add = TaskAgentSpace._index_add
    failed: list[bool] = []

    def fails_once(self: Any, index: Path, line: str, target: str) -> None:
        if target == f"memory/{one}.md" and not failed:
            failed.append(True)
            raise OSError("held by another program")
        index_add(self, index, line, target)

    monkeypatch.setattr(TaskAgentSpace, "_index_add", fails_once)
    world.answer(lambda shown: [{"op": "merge", "from": shown["Make from the root"], "into": shown["Build with make"]}])
    await world.tidy()
    assert failed
    assert f"memory/{one}.md" not in await world.space.read_index(None)
    world.llm.answer = {"operations": []}

    await world.tidy()

    assert f"[Build with make](memory/{one}.md)" in await world.space.read_index(None)


async def test_a_date_whose_line_could_not_be_written_gets_it_by_a_later_round(world, monkeypatch):
    one = await world.active("Server down", "The staging server was down yesterday.")
    await world.active("Mirror", "The mirror is at mirror.local.")
    index_reline = TaskAgentSpace._index_reline
    failed: list[bool] = []

    def fails_once(self: Any, *args: Any) -> None:
        if not failed:
            failed.append(True)
            raise OSError("held by another program")
        index_reline(self, *args)

    monkeypatch.setattr(TaskAgentSpace, "_index_reline", fails_once)
    world.answer(lambda shown: [{"op": "date", "entry": shown["Server down"], "old": "yesterday", "new": "2027-01-14"}])
    await world.tidy()
    assert failed
    assert f"- [Server down](memory/{one}.md) — The staging server was down yesterday." in await world.space.read_index(None)
    world.llm.answer = {"operations": []}

    await world.tidy()

    assert f"- [Server down](memory/{one}.md) — The staging server was down 2027-01-14." in await world.space.read_index(None)
    assert world.space._relines == {}


async def test_a_merged_guess_takes_effect_by_what_the_merge_wrote_when_it_cannot_be_read_back(world, monkeypatch):
    first = await world.pending("Make needs the root", "make fails when started in src.", by="c1")
    await world.pending("Build only from top", "Building from a subdirectory breaks.", by="c2")
    file_entry, entry_from = TaskAgentSpace._file_entry, TaskAgentSpace._entry_from
    inside: list[bool] = []
    unread: list[str] = []

    def filing(self: Any, *args: Any, **kwargs: Any) -> Any:
        inside.append(True)
        try:
            return file_entry(self, *args, **kwargs)
        finally:
            inside.pop()

    def read_back(self: Any, path: Path, *args: Any, **kwargs: Any) -> Any:
        if inside and path.stem == first:
            # Read back as it is written, and failed: what was filed into it stays unknown.
            unread.append(path.stem)
            return None
        return entry_from(self, path, *args, **kwargs)

    monkeypatch.setattr(TaskAgentSpace, "_file_entry", filing)
    monkeypatch.setattr(TaskAgentSpace, "_entry_from", read_back)
    world.answer(lambda shown: [{"op": "merge", "from": shown["Build only from top"], "into": shown["Make needs the root"]}])

    await world.tidy()

    assert unread
    assert (await world.entry(first)).status == ENTRY_ACTIVE


async def test_a_merged_guess_whose_first_settling_failed_is_settled_by_a_later_round(world, monkeypatch):
    first = await world.pending("Make needs the root", "make fails when started in src.", by="c1")
    await world.pending("Build only from top", "Building from a subdirectory breaks.", by="c2")
    confirm = TaskAgentSpace._confirm
    failed: list[bool] = []

    def fails_once(self: Any, *args: Any, **kwargs: Any) -> Any:
        if not failed:
            failed.append(True)
            raise OSError("held by another program")
        return confirm(self, *args, **kwargs)

    monkeypatch.setattr(TaskAgentSpace, "_confirm", fails_once)
    world.answer(lambda shown: [{"op": "merge", "from": shown["Build only from top"], "into": shown["Make needs the root"]}])
    await world.tidy()
    assert failed
    assert (await world.entry(first)).status == ENTRY_PENDING
    world.llm.answer = {"operations": []}

    await world.tidy()

    assert (await world.entry(first)).status == ENTRY_ACTIVE
    assert world.space._unsettled == {}


async def test_a_line_left_to_be_added_is_added_only_for_an_entry_in_effect(world):
    waiting = await world.pending("Make needs the root", "make fails when started in src.", by="c1")
    world.space._relines[(None, waiting)] = _LineToPut(missing=True)
    world.space._relines[(None, "gone")] = _LineToPut(missing=True)

    await world.space.settle_tidied(None)

    index = await world.space.read_index(None)
    assert f"memory/{waiting}.md" not in index and "memory/gone.md" not in index
    assert world.space._relines == {}


async def test_a_line_added_later_has_the_title_tidying_gave_meanwhile(world, monkeypatch):
    first = await world.pending("Make needs the root", "make fails when started in src.", by="c1")
    await world.pending("Build only from top", "Building from a subdirectory breaks.", by="c2")
    await world.active("Mirror", "The mirror is at mirror.local.")
    index_add = TaskAgentSpace._index_add
    failed: list[bool] = []

    def fails_once(self: Any, index: Path, line: str, target: str) -> None:
        if target == f"memory/{first}.md" and not failed:
            failed.append(True)
            raise OSError("held by another program")
        index_add(self, index, line, target)

    monkeypatch.setattr(TaskAgentSpace, "_index_add", fails_once)
    world.answer(
        lambda shown: [
            {"op": "merge", "from": shown["Build only from top"], "into": shown["Make needs the root"]},
            {"op": "retitle", "entry": shown["Make needs the root"], "apart_from": shown["Mirror"], "title": "Make needs the root in src"},
        ]
    )
    await world.tidy()
    assert failed
    assert (await world.entry(first)).title == "Make needs the root in src"
    world.llm.answer = {"operations": []}

    await world.tidy()

    index = await world.space.read_index(None)
    assert f"[Make needs the root in src](memory/{first}.md)" in index
    assert f"[Make needs the root](memory/{first}.md)" not in index


def _fails_once(monkeypatch: Any, name: str) -> list[bool]:
    """Have the first call of a method of the space fail as a file held by another program would; returns whether it did."""
    method = getattr(TaskAgentSpace, name)
    failed: list[bool] = []

    def once(self: Any, *args: Any, **kwargs: Any) -> Any:
        if not failed:
            failed.append(True)
            raise OSError("held by another program")
        return method(self, *args, **kwargs)

    monkeypatch.setattr(TaskAgentSpace, name, once)
    return failed


async def test_a_date_whose_line_failed_is_put_right_after_a_title_was_given_too(world, monkeypatch):
    one = await world.active("Server down", "The staging server was down yesterday.")
    await world.active("Server mirror", "The mirror is at mirror.local.")
    failed = _fails_once(monkeypatch, "_index_reline")
    world.answer(
        lambda shown: [
            {"op": "date", "entry": shown["Server down"], "old": "yesterday", "new": "2027-01-14"},
            {"op": "retitle", "entry": shown["Server down"], "apart_from": shown["Server mirror"], "title": "Server down staging"},
        ]
    )
    await world.tidy()
    assert failed
    assert f"[Server down staging](memory/{one}.md) — The staging server was down yesterday." in await world.space.read_index(None)
    world.llm.answer = {"operations": []}

    await world.tidy()

    assert f"- [Server down staging](memory/{one}.md) — The staging server was down 2027-01-14." in await world.space.read_index(None)


async def test_two_dates_whose_first_line_failed_are_both_put_into_it(world, monkeypatch):
    one = await world.active("Server down", "The server was down yesterday and back today.")
    await world.active("Mirror", "The mirror is at mirror.local.")
    failed = _fails_once(monkeypatch, "_index_reline")
    world.answer(
        lambda shown: [
            {"op": "date", "entry": shown["Server down"], "old": "yesterday", "new": "2027-01-14"},
            {"op": "date", "entry": shown["Server down"], "old": "today", "new": "2027-01-15"},
        ]
    )
    await world.tidy()
    assert failed
    assert (await world.entry(one)).description == "The server was down 2027-01-14 and back 2027-01-15."
    world.llm.answer = {"operations": []}

    await world.tidy()

    assert f"- [Server down](memory/{one}.md) — The server was down 2027-01-14 and back 2027-01-15." in await world.space.read_index(
        None
    )


async def _retitle_left_in_the_line(world: Any, monkeypatch: Any) -> str:
    """A title put into the line of an entry that could not be written, and could not be taken out of the line again."""
    one = await world.active("Tests", "Run them in parallel: pytest with -n 4.")
    await world.active("Testing", "Tests need DISPLAY set.")
    edit_as_shown = TaskAgentSpace._edit_as_shown

    def held(self: Any, path: Path, *args: Any) -> Any:
        if path.stem == one:
            raise OSError("held by another program")
        return edit_as_shown(self, path, *args)

    monkeypatch.setattr(TaskAgentSpace, "_edit_as_shown", held)
    failed = _fails_once(monkeypatch, "_index_reline")
    world.answer(
        lambda shown: [{"op": "retitle", "entry": shown["Tests"], "apart_from": shown["Testing"], "title": "Tests in parallel"}]
    )
    await world.tidy()
    monkeypatch.undo()
    assert failed
    assert f"[Tests in parallel](memory/{one}.md)" in await world.space.read_index(None)
    world.llm.answer = {"operations": []}
    return one


async def test_a_title_left_in_a_line_is_taken_out_by_a_later_round(world, monkeypatch):
    one = await _retitle_left_in_the_line(world, monkeypatch)

    await world.tidy()

    index = await world.space.read_index(None)
    assert f"[Tests](memory/{one}.md)" in index and "Tests in parallel" not in index


async def test_a_title_left_in_a_line_stays_once_the_user_gave_the_entry_that_title(world, monkeypatch):
    one = await _retitle_left_in_the_line(world, monkeypatch)
    path = world.memory() / f"{one}.md"
    path.write_text(path.read_text(encoding="utf-8").replace("name: Tests\n", "name: Tests in parallel\n"), encoding="utf-8")
    assert (await world.entry(one)).title == "Tests in parallel"

    await world.tidy()

    assert f"[Tests in parallel](memory/{one}.md)" in await world.space.read_index(None)


async def test_a_title_put_back_is_the_line_before_it_was_written_though_it_was_written_twice(world, monkeypatch):
    one = await world.active("Tests", "Run them in parallel: pytest with -n 4.")
    await world.active("Testing", "Tests need DISPLAY set.")
    edit_as_shown, put_text = TaskAgentSpace._edit_as_shown, TaskAgentSpace._put_text
    again: list[bool] = []

    def held(self: Any, path: Path, *args: Any) -> Any:
        if path.stem == one:
            raise OSError("held by another program")
        return edit_as_shown(self, path, *args)

    def saved_right_after(path: Path, text: str) -> bool:
        seen = put_text(path, text)
        if path.name == "MEMORY.md" and "Tests in parallel" in text and not again:
            # Something else saved it right after: it is written again.
            again.append(True)
            raise _NotAsWritten("saved right after")
        return seen

    monkeypatch.setattr(TaskAgentSpace, "_edit_as_shown", held)
    monkeypatch.setattr(TaskAgentSpace, "_put_text", staticmethod(saved_right_after))
    world.answer(
        lambda shown: [{"op": "retitle", "entry": shown["Tests"], "apart_from": shown["Testing"], "title": "Tests in parallel"}]
    )

    await world.tidy()

    assert again
    index = await world.space.read_index(None)
    assert f"[Tests](memory/{one}.md)" in index and "Tests in parallel" not in index


async def test_an_entry_kept_from_a_merge_gets_no_line_the_user_took_out(world, monkeypatch):
    one = await world.active("Build with make", "Run make in the root.")
    other = await world.active("Make from the root", "make only works from the repository root.")
    index_path = world.memory().parent / "MEMORY.md"
    text = index_path.read_text(encoding="utf-8")
    index_path.write_text("\n".join(item for item in text.split("\n") if f"memory/{other}.md" not in item), encoding="utf-8")
    target = world.memory() / f"{one}.md"
    index_remove = TaskAgentSpace._index_remove
    saved: list[bool] = []

    def saved_meanwhile(self: Any, index: Path, line_of: str, **kwargs: Any) -> Any:
        removed = index_remove(self, index, line_of, **kwargs)
        if line_of == f"memory/{other}.md":
            saved.append(True)
            target.write_text(target.read_text(encoding="utf-8").split("## ")[0].rstrip() + "\n\nSaved by hand.\n", encoding="utf-8")
        return removed

    monkeypatch.setattr(TaskAgentSpace, "_index_remove", saved_meanwhile)
    world.answer(lambda shown: [{"op": "merge", "from": shown["Make from the root"], "into": shown["Build with make"]}])

    await world.tidy()

    assert saved
    assert (world.memory() / f"{other}.md").exists()
    assert f"memory/{other}.md" not in await world.space.read_index(None)


@pytest.mark.parametrize(
    "said",
    [
        # At the end of a longer word, at the start of one, and before a
        # stress mark that is part of its last letter.
        "Миграция завершилась позавчера.",
        "Отчёт вчерашний готов.",
        "Отчёт вчера\u0301шний готов.",
    ],
)
async def test_a_date_is_not_put_inside_a_longer_word_of_another_alphabet(world, said):
    one = await world.active("Миграция", said)
    await world.active("Зеркало", "Зеркало на mirror.local.")
    world.answer(lambda shown: [{"op": "date", "entry": shown["Миграция"], "old": "вчера", "new": "2027-01-14"}])

    await world.tidy()

    assert said in (world.memory() / f"{one}.md").read_text(encoding="utf-8")


async def test_a_date_is_put_for_a_word_of_another_alphabet(world):
    one = await world.active("Миграция", "Миграция завершилась вчера.")
    await world.active("Зеркало", "Зеркало на mirror.local.")
    world.answer(lambda shown: [{"op": "date", "entry": shown["Миграция"], "old": "вчера", "new": "2027-01-14"}])

    await world.tidy()

    assert "Миграция завершилась 2027-01-14." in (world.memory() / f"{one}.md").read_text(encoding="utf-8")


def _without_line(world: Any, entry_id: str) -> None:
    """Take the line of an entry out of the index, as the user may."""
    index = world.memory().parent / "MEMORY.md"
    text = index.read_text(encoding="utf-8")
    index.write_text("\n".join(item for item in text.split("\n") if f"memory/{entry_id}.md" not in item), encoding="utf-8")


async def test_an_entry_saved_as_it_was_merged_away_gets_no_line_the_user_took_out(world, monkeypatch):
    await world.active("Build with make", "Run make in the root.")
    other = await world.active("Make from the root", "make only works from the repository root.")
    _without_line(world, other)
    source = world.memory() / f"{other}.md"
    discard_as_read = TaskAgentSpace._discard_as_read
    saved: list[bool] = []

    def saved_meanwhile(self: Any, path: Path, before: Any) -> bool:
        if path == source:
            saved.append(True)
            path.write_text(path.read_text(encoding="utf-8") + "\nSaved by hand.\n", encoding="utf-8")
        return discard_as_read(self, path, before)

    monkeypatch.setattr(TaskAgentSpace, "_discard_as_read", saved_meanwhile)
    world.answer(lambda shown: [{"op": "merge", "from": shown["Make from the root"], "into": shown["Build with make"]}])

    await world.tidy()

    assert saved and source.exists()
    assert f"memory/{other}.md" not in await world.space.read_index(None)


async def test_a_date_whose_line_failed_adds_no_line_the_user_took_out_since(world, monkeypatch):
    one = await world.active("Server down", "The staging server was down yesterday.")
    await world.active("Mirror", "The mirror is at mirror.local.")
    failed = _fails_once(monkeypatch, "_index_reline")
    world.answer(lambda shown: [{"op": "date", "entry": shown["Server down"], "old": "yesterday", "new": "2027-01-14"}])
    await world.tidy()
    assert failed and world.space._relines
    _without_line(world, one)
    world.llm.answer = {"operations": []}

    await world.tidy()

    assert f"memory/{one}.md" not in await world.space.read_index(None)
    assert world.space._relines == {}


async def test_a_date_is_not_put_for_words_that_are_part_of_a_longer_relative_time(world):
    # "一昨日" is the day before "昨日": the shorter one does not stand in it.
    one = await world.active("移行", "移行は一昨日に完了しました。")
    await world.active("ミラー", "ミラーは mirror.local にあります。")
    world.answer(lambda shown: [{"op": "date", "entry": shown["移行"], "old": "昨日", "new": "2027-01-14"}])

    await world.tidy()

    assert "移行は一昨日に完了しました。" in (world.memory() / f"{one}.md").read_text(encoding="utf-8")


async def test_a_date_is_put_for_words_of_a_text_with_no_spaces(world):
    one = await world.active("移行", "移行は昨日に完了しました。")
    await world.active("ミラー", "ミラーは mirror.local にあります。")
    world.answer(lambda shown: [{"op": "date", "entry": shown["移行"], "old": "昨日", "new": "2027-01-14"}])

    await world.tidy()

    assert "移行は2027-01-14に完了しました。" in (world.memory() / f"{one}.md").read_text(encoding="utf-8")


async def test_titles_left_in_a_line_round_after_round_are_all_taken_out(world, monkeypatch):
    one = await world.active("Tests", "Run them in parallel: pytest with -n 4.")
    await world.active("Testing", "Tests need DISPLAY set.")
    edit_as_shown = TaskAgentSpace._edit_as_shown

    def held(self: Any, path: Path, *args: Any) -> Any:
        if path.stem == one:
            raise OSError("held by another program")
        return edit_as_shown(self, path, *args)

    def never(self: Any, *args: Any) -> Any:
        raise OSError("held by another program")

    monkeypatch.setattr(TaskAgentSpace, "_edit_as_shown", held)
    monkeypatch.setattr(TaskAgentSpace, "_index_reline", never)
    # Nor is the line put right between the two rounds.
    monkeypatch.setattr(TaskAgentSpace, "_put_line_right", never)
    world.answer(
        lambda shown: [{"op": "retitle", "entry": shown["Tests"], "apart_from": shown["Testing"], "title": "Tests in parallel"}]
    )
    await world.tidy()
    world.answer(
        lambda shown: [{"op": "retitle", "entry": shown["Tests"], "apart_from": shown["Testing"], "title": "Tests pytest with"}]
    )
    await world.tidy()
    assert f"[Tests pytest with](memory/{one}.md)" in await world.space.read_index(None)
    monkeypatch.undo()
    world.llm.answer = {"operations": []}

    await world.tidy()

    index = await world.space.read_index(None)
    assert f"[Tests](memory/{one}.md)" in index and "Tests in parallel" not in index and "Tests pytest with" not in index


async def test_a_title_the_user_gave_the_line_first_is_not_taken_back(world, monkeypatch):
    one = await world.active("Tests", "Run them in parallel: pytest with -n 4.")
    await world.active("Testing", "Tests need DISPLAY set.")
    edit_as_shown, index_retitle = TaskAgentSpace._edit_as_shown, TaskAgentSpace._index_retitle
    text_to_change = TaskAgentSpace._text_to_change
    retitling: list[bool] = []
    saved: list[bool] = []

    def held(self: Any, path: Path, *args: Any) -> Any:
        if path.stem == one:
            raise OSError("held by another program")
        return edit_as_shown(self, path, *args)

    def in_retitle(self: Any, *args: Any, **kwargs: Any) -> Any:
        retitling.append(True)
        try:
            return index_retitle(self, *args, **kwargs)
        finally:
            retitling.pop()

    def saved_first(path: Path) -> str:
        text = text_to_change(path)
        if retitling and not saved:
            # The user gives the line that title in an editor, before the host writes it.
            saved.append(True)
            path.write_text(text.replace(f"[Tests](memory/{one}.md)", f"[Tests in parallel](memory/{one}.md)"), encoding="utf-8")
        return text

    monkeypatch.setattr(TaskAgentSpace, "_edit_as_shown", held)
    monkeypatch.setattr(TaskAgentSpace, "_index_retitle", in_retitle)
    monkeypatch.setattr(TaskAgentSpace, "_text_to_change", staticmethod(saved_first))
    world.answer(
        lambda shown: [{"op": "retitle", "entry": shown["Tests"], "apart_from": shown["Testing"], "title": "Tests in parallel"}]
    )

    await world.tidy()

    assert saved
    assert f"[Tests in parallel](memory/{one}.md)" in await world.space.read_index(None)


async def test_a_line_put_right_as_the_entry_was_saved_is_put_right_again(world, monkeypatch):
    one = await world.active("Server down", "The staging server was down yesterday.")
    await world.active("Mirror", "The mirror is at mirror.local.")
    failed = _fails_once(monkeypatch, "_index_reline")
    world.answer(lambda shown: [{"op": "date", "entry": shown["Server down"], "old": "yesterday", "new": "2027-01-14"}])
    await world.tidy()
    assert failed
    path = world.memory() / f"{one}.md"
    from nekoclaw.host import space as space_module

    line_put_right = space_module._line_put_right
    saved: list[bool] = []

    def saved_meanwhile(*args: Any) -> Any:
        if not saved:
            # Saved by the user after the host read it, before the line is written.
            saved.append(True)
            path.write_text(
                path.read_text(encoding="utf-8").replace(
                    "description: The staging server was down 2027-01-14.", "description: Down on 2027-01-14; use the mirror."
                ),
                encoding="utf-8",
            )
        return line_put_right(*args)

    monkeypatch.setattr(space_module, "_line_put_right", saved_meanwhile)
    world.llm.answer = {"operations": []}
    await world.tidy()
    assert saved
    assert "— The staging server was down 2027-01-14." in await world.space.read_index(None)

    await world.tidy()

    assert f"- [Server down](memory/{one}.md) — Down on 2027-01-14; use the mirror." in await world.space.read_index(None)
    assert world.space._relines == {}


async def test_a_line_added_as_the_entry_was_saved_is_not_added_again_once_the_user_took_it_out(world, monkeypatch):
    first = await world.pending("Make needs the root", "make fails when started in src.", by="c1")
    await world.pending("Build only from top", "Building from a subdirectory breaks.", by="c2")
    await world.active("Mirror", "The mirror is at mirror.local.")
    index_add = TaskAgentSpace._index_add
    failed: list[bool] = []

    def fails_once(self: Any, index: Path, line: str, target: str) -> None:
        if target == f"memory/{first}.md" and not failed:
            failed.append(True)
            raise OSError("held by another program")
        index_add(self, index, line, target)

    monkeypatch.setattr(TaskAgentSpace, "_index_add", fails_once)
    world.answer(lambda shown: [{"op": "merge", "from": shown["Build only from top"], "into": shown["Make needs the root"]}])
    await world.tidy()
    assert failed
    path = world.memory() / f"{first}.md"
    saved: list[bool] = []
    index_text = TaskAgentSpace._text_to_change

    def saved_meanwhile(index: Path) -> str:
        text = index_text(index)
        if index.name == "MEMORY.md" and (None, first) in world.space._relines and not saved:
            saved.append(True)
            path.write_text(path.read_text(encoding="utf-8") + "\nSaved by hand.\n", encoding="utf-8")
        return text

    monkeypatch.setattr(TaskAgentSpace, "_text_to_change", staticmethod(saved_meanwhile))
    world.llm.answer = {"operations": []}
    await world.tidy()
    monkeypatch.undo()
    assert saved
    assert f"memory/{first}.md" in await world.space.read_index(None)
    assert (None, first) in world.space._relines
    _without_line(world, first)

    await world.tidy()

    assert f"memory/{first}.md" not in await world.space.read_index(None)


async def test_a_description_the_user_put_into_the_line_is_not_taken_for_the_hosts(world, monkeypatch):
    one = await world.active("Server down", "The staging server was down yesterday.")
    await world.active("Mirror", "The mirror is at mirror.local.")
    path = world.memory() / f"{one}.md"
    index = world.memory().parent / "MEMORY.md"
    # The user reworded both the entry and its line, the same way.
    path.write_text(
        path.read_text(encoding="utf-8").replace(
            "description: The staging server was down yesterday.", "description: Down, use the mirror."
        ),
        encoding="utf-8",
    )
    text = index.read_text(encoding="utf-8")
    line = next(item for item in text.split("\n") if f"memory/{one}.md" in item)
    mine = f"- [Server down](memory/{one}.md) — Down, use the mirror."
    index.write_text(text.replace(line, mine), encoding="utf-8")
    world.space._relines[(None, one)] = _LineToPut(described={"The staging server was down yesterday."})
    from nekoclaw.host import space as space_module

    line_put_right = space_module._line_put_right
    saved: list[bool] = []

    def saved_meanwhile(*args: Any) -> Any:
        if not saved:
            # Saved again by the user, after the host read it.
            saved.append(True)
            path.write_text(
                path.read_text(encoding="utf-8").replace("description: Down, use the mirror.", "description: Back up."),
                encoding="utf-8",
            )
        return line_put_right(*args)

    monkeypatch.setattr(space_module, "_line_put_right", saved_meanwhile)
    await world.space.settle_tidied(None)
    assert saved

    await world.space.settle_tidied(None)

    assert mine in await world.space.read_index(None)


async def test_a_line_added_before_a_try_after_it_failed_is_not_added_again(world, monkeypatch):
    one = await world.active("Server down", "The staging server was down yesterday.")
    await world.active("Mirror", "The mirror is at mirror.local.")
    _without_line(world, one)
    world.space._relines[(None, one)] = _LineToPut(missing=True)
    put_text, text_to_change = TaskAgentSpace._put_text, TaskAgentSpace._text_to_change
    writes: list[str] = []

    def saved_right_after(path: Path, text: str) -> bool:
        seen = put_text(path, text)
        if path.name == "MEMORY.md" and not writes:
            # Written, and something else saved it right after: it is tried again.
            writes.append(text)
            raise _NotAsWritten("saved right after")
        return seen

    def held_then(path: Path) -> str:
        if path.name == "MEMORY.md" and writes:
            raise OSError("held by another program")
        return text_to_change(path)

    monkeypatch.setattr(TaskAgentSpace, "_put_text", staticmethod(saved_right_after))
    monkeypatch.setattr(TaskAgentSpace, "_text_to_change", staticmethod(held_then))
    await world.space.settle_tidied(None)
    monkeypatch.undo()
    assert writes and f"memory/{one}.md" in await world.space.read_index(None)
    # The user takes the line it added out.
    _without_line(world, one)

    await world.space.settle_tidied(None)

    assert f"memory/{one}.md" not in await world.space.read_index(None)


async def test_a_line_put_back_from_a_merge_is_the_line_as_the_user_last_worded_it(world, monkeypatch):
    one = await world.active("Build with make", "Run make in the root.")
    other = await world.active("Make from the root", "make only works from the repository root.")
    target = world.memory() / f"{one}.md"
    index = world.memory().parent / "MEMORY.md"
    mine = f"- [Make from the root](memory/{other}.md) — mine: never from src"
    index_remove = TaskAgentSpace._index_remove
    saved: list[bool] = []

    def reworded_then_saved(self: Any, path: Path, line_of: str, **kwargs: Any) -> Any:
        if line_of == f"memory/{other}.md" and not saved:
            saved.append(True)
            # The user rewords the line just before it is taken out...
            text = index.read_text(encoding="utf-8")
            line = next(item for item in text.split("\n") if line_of in item)
            index.write_text(text.replace(line, mine), encoding="utf-8")
            removed = index_remove(self, path, line_of, **kwargs)
            # ...and saves the entry it went into, without what went in.
            target.write_text(target.read_text(encoding="utf-8").split("## ")[0].rstrip() + "\n\nSaved by hand.\n", encoding="utf-8")
            return removed
        return index_remove(self, path, line_of, **kwargs)

    monkeypatch.setattr(TaskAgentSpace, "_index_remove", reworded_then_saved)
    world.answer(lambda shown: [{"op": "merge", "from": shown["Make from the root"], "into": shown["Build with make"]}])

    await world.tidy()

    assert saved
    assert (world.memory() / f"{other}.md").exists()
    assert mine in await world.space.read_index(None)


async def test_a_line_the_user_added_is_not_added_again_once_they_took_it_out(world, monkeypatch):
    one = await world.active("Server down", "The staging server was down yesterday.")
    await world.active("Mirror", "The mirror is at mirror.local.")
    # Missing when it was to be added; the user has given it one since.
    world.space._relines[(None, one)] = _LineToPut(missing=True)
    path = world.memory() / f"{one}.md"
    from nekoclaw.host import space as space_module

    line_put_right = space_module._line_put_right
    saved: list[bool] = []

    def saved_meanwhile(*args: Any) -> Any:
        if not saved:
            # Saved by the user after the host read it: left for a later round.
            saved.append(True)
            path.write_text(path.read_text(encoding="utf-8") + "\nSaved by hand.\n", encoding="utf-8")
        return line_put_right(*args)

    monkeypatch.setattr(space_module, "_line_put_right", saved_meanwhile)
    await world.space.settle_tidied(None)
    assert saved and (None, one) in world.space._relines
    _without_line(world, one)

    await world.space.settle_tidied(None)

    assert f"memory/{one}.md" not in await world.space.read_index(None)


async def test_a_title_taken_back_leaves_the_users_description_theirs(world, monkeypatch):
    one = await world.active("Tests", "Run them in parallel: pytest with -n 4.")
    await world.active("Testing", "Tests need DISPLAY set.")
    path = world.memory() / f"{one}.md"
    index = world.memory().parent / "MEMORY.md"
    # The user wrote the entry's description and its line the same way; a
    # title tidying put into the line was not taken out.
    path.write_text(
        path.read_text(encoding="utf-8").replace(
            "description: Run them in parallel: pytest with -n 4.", "description: mine: -n 4"
        ),
        encoding="utf-8",
    )
    text = index.read_text(encoding="utf-8")
    line = next(item for item in text.split("\n") if f"memory/{one}.md" in item)
    index.write_text(text.replace(line, f"- [Tests in parallel](memory/{one}.md) — mine: -n 4"), encoding="utf-8")
    world.space._relines[(None, one)] = _LineToPut(titled={"Tests in parallel": ("Tests", "Tests")})
    from nekoclaw.host import space as space_module

    line_put_right = space_module._line_put_right
    saved: list[bool] = []

    def saved_meanwhile(*args: Any) -> Any:
        if not saved:
            # Saved by the user after the host read it: left for a later round.
            saved.append(True)
            path.write_text(path.read_text(encoding="utf-8").replace("description: mine: -n 4", "description: Back to -n 2."), encoding="utf-8")
        return line_put_right(*args)

    monkeypatch.setattr(space_module, "_line_put_right", saved_meanwhile)
    await world.space.settle_tidied(None)
    assert saved
    assert f"- [Tests](memory/{one}.md) — mine: -n 4" in await world.space.read_index(None)

    await world.space.settle_tidied(None)

    assert f"- [Tests](memory/{one}.md) — mine: -n 4" in await world.space.read_index(None)
