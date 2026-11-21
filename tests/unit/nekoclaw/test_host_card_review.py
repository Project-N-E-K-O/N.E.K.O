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

"""The review of a finished commission writes the card of its session, in the same call."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from nekoclaw.host.decision import TIER_REVIEW
from nekoclaw.host.paths import DEFAULT_PROJECT
from tests.unit.nekoclaw.test_host_space_keeper import FakeLLM, World

CARD = {
    "title": "Photo sorter",
    "duty": "sorting and renaming photo folders",
    "summary": "Sorted the Downloads photos by month. Renaming is still open.",
    "description": "",
}


@pytest.fixture
async def world(tmp_path: Path) -> Any:
    made = World(tmp_path, FakeLLM({"review": "It was done.", "card": dict(CARD)}))
    yield made
    await made.keeper.aclose()
    await made.ledger.close()


async def _card(world: World, session_id: str) -> tuple[str, ...]:
    session = await world.ledger.get_session(session_id)
    return (session.title, session.title_by, session.duty, session.duty_by, session.summary, session.summary_by)


async def _in_project(world: World, project: str, text: str = "add a chart to the budget app", **described: Any) -> Any:
    """A commission filed under a project and handed to the project's session."""
    await world.space.touch_project(project, **described)
    commission, _created = await world.ledger.create(
        dispatch_text=text, lanlan_name="Lan", title=text, source="dispatch", route="task_agent", lang="en", project=project
    )
    commission, _session = await world.ledger.give_session(commission.id, kind="project", name=project)
    return commission


# ---------------------------------------------------------------------------
# What is written
# ---------------------------------------------------------------------------


async def test_the_review_writes_the_title_the_duty_and_the_summary_of_the_session(world):
    commission = await world.file()
    await world.ledger.write_card(commission.session_id, by="creator", duty="sort the photos in Downloads")
    await world.work(commission.id)

    await world.end(commission.id)

    assert await _card(world, commission.session_id) == (
        "Photo sorter", "review", "sorting and renaming photo folders", "review",
        "Sorted the Downloads photos by month. Renaming is still open.", "review",
    )
    # In the one call a review always was: nothing more was asked of a model.
    assert world.llm.count(TIER_REVIEW) == 1 and len(world.llm.calls) == 1
    assert await world.reviews(commission.id) == [
        {"status": "written", "project": DEFAULT_PROJECT, "entries": [], "card": ["title", "duty", "summary"]}
    ]
    # And the review itself is kept as it was.
    assert (await world.space.read_review(commission.id)).content.endswith("It was done.")


async def test_the_review_is_shown_the_card_as_it_stands_and_told_how_to_write_one(world):
    commission = await _in_project(world, "Budget app", description="A budget for the household.")
    await world.ledger.write_card(commission.session_id, by="review", title="Budget keeper", summary="Made the table.")
    await world.ledger.write_card(commission.session_id, by="creator", duty="the budget app")
    await world.work(commission.id)

    await world.end(commission.id)

    system, user = (message["content"] for message in world.llm.last(TIER_REVIEW))
    assert (
        "Project: Budget app\n"
        "Card of its session now:\n"
        "title: Budget keeper\nduty: the budget app\nsummary: Made the table.\ndescription: A budget for the household.\n\n"
        "What the task agent did:\n"
    ) in user
    assert '"card": {"title": "", "duty": "", "summary": "", "description": ""}}' in system
    for line in ("title: what the user would call this task agent", "duty: one line", "summary: two to four sentences", "description: one line"):
        assert line in system
    assert system.rstrip().endswith("Write the review, the entries, project_description and the card in English.")


async def test_a_card_nobody_wrote_yet_is_shown_as_empty(world):
    commission = await world.finished()

    user = world.llm.calls[0][1][1]["content"]
    assert "Card of its session now:\ntitle: (none)\nduty: (none)\nsummary: (none)\ndescription: (none)\n\n" in user
    assert (await world.ledger.get_session(commission.session_id)).title == "Photo sorter"


# ---------------------------------------------------------------------------
# What the user wrote stays
# ---------------------------------------------------------------------------


async def test_a_line_the_user_wrote_is_not_written_over_and_the_others_are(world):
    commission = await world.file()
    await world.ledger.write_card(commission.session_id, by="user", title="我的相册管家")
    await world.work(commission.id)

    await world.end(commission.id)

    assert await _card(world, commission.session_id) == (
        "我的相册管家", "user", "sorting and renaming photo folders", "review",
        "Sorted the Downloads photos by month. Renaming is still open.", "review",
    )
    assert (await world.reviews(commission.id))[0]["card"] == ["duty", "summary"]


async def test_what_the_session_said_it_takes_is_not_written_over_and_the_review_is_told_which_lines_are_not_its(world):
    commission = await _in_project(world, "Budget app", description="a budget")
    await world.space.describe_project("Budget app", "我们家的记账本", by="user")
    await world.ledger.write_card(commission.session_id, by="self", duty="the budget app and its charts")
    await world.ledger.write_card(commission.session_id, by="user", title="记账管家")
    await world.ledger.write_card(commission.session_id, by="review", summary="Made the table.")
    world.llm.answers[TIER_REVIEW] = [{"review": "It was done.", "card": {**CARD, "description": "Something else."}}]
    await world.work(commission.id)

    await world.end(commission.id)

    # What the user wrote and what the session said of itself stay; the summary is the review's to write.
    assert await _card(world, commission.session_id) == (
        "记账管家", "user", "the budget app and its charts", "self",
        "Sorted the Downloads photos by month. Renaming is still open.", "review",
    )
    assert (await world.reviews(commission.id))[0]["card"] == ["summary"]
    assert (await world.space.get_project("Budget app")).description == "我们家的记账本"
    user = world.llm.last(TIER_REVIEW)[1]["content"]
    kept = " (not yours to change: leave it empty)"
    assert (
        f"title: 记账管家{kept}\n"
        f"duty: the budget app and its charts{kept}\n"
        "summary: Made the table.\n"
        f"description: 我们家的记账本{kept}\n\n"
    ) in user


async def test_a_line_the_review_leaves_empty_is_left_as_it_is(world):
    world.llm.answers[TIER_REVIEW] = [{"review": "It was done.", "card": {"title": "", "duty": "  ", "summary": "Sorted them."}}]
    commission = await world.file()
    await world.ledger.write_card(commission.session_id, by="creator", duty="sort the photos")
    await world.ledger.write_card(commission.session_id, by="review", title="Photo sorter")
    await world.work(commission.id)

    await world.end(commission.id)

    assert await _card(world, commission.session_id) == (
        "Photo sorter", "review", "sort the photos", "creator", "Sorted them.", "review",
    )
    assert (await world.reviews(commission.id))[0]["card"] == ["summary"]


# ---------------------------------------------------------------------------
# What a project is for
# ---------------------------------------------------------------------------


async def test_the_review_writes_what_the_project_is_for_unless_the_user_did(world):
    world.llm.answers[TIER_REVIEW] = [
        {"review": "It was done.", "card": {**CARD, "description": "Tracks what the household spends, month by month."}}
    ]
    commission = await _in_project(world, "Budget app", description="a budget")
    assert (await world.space.get_project("Budget app")).description_by == "creator"
    await world.work(commission.id)

    await world.end(commission.id)

    project = await world.space.get_project("Budget app")
    assert (project.description, project.description_by) == ("Tracks what the household spends, month by month.", "review")
    assert (await world.reviews(commission.id))[0]["card"] == ["title", "duty", "summary", "description"]
    # Its line in the index of all projects says the same.
    index = (world.root / "MEMORY.md").read_text(encoding="utf-8")
    assert "Tracks what the household spends, month by month." in index and "a budget;" not in index

    # The user says what it is for: from then on a review leaves it.
    await world.space.describe_project("Budget app", "我们家的记账本", by="user")
    world.llm.answers[TIER_REVIEW] = [{"review": "Done again.", "card": {**CARD, "description": "Something else."}}]
    again = await _in_project(world, "Budget app", text="add a table to the budget app")
    await world.work(again.id)
    await world.end(again.id)

    project = await world.space.get_project("Budget app")
    assert (project.description, project.description_by) == ("我们家的记账本", "user")
    assert (await world.reviews(again.id))[0]["card"] == ["title", "duty", "summary"]


async def test_a_commission_the_review_files_under_a_project_does_not_write_over_what_that_project_is_for(world):
    # The model was shown the description of the project the commission was filed under: none, for
    # an errand of no project. What it then writes is not what the project it names is for.
    await world.space.touch_project("Budget app", description="A household budgeting web page.")
    world.llm.answers[TIER_REVIEW] = [
        {"review": "It was done.", "project": "Budget app", "card": {**CARD, "description": "A script that renames photos."}}
    ]

    commission = await world.finished()

    assert (await world.reviews(commission.id))[0] == {
        "status": "written", "project": "Budget app", "entries": [], "card": ["title", "duty", "summary"],
    }
    project = await world.space.get_project("Budget app")
    assert (project.description, project.description_by) == ("A household budgeting web page.", "creator")
    assert "A household budgeting web page." in (world.root / "MEMORY.md").read_text(encoding="utf-8")
    assert "description: (none)" in world.llm.last(TIER_REVIEW)[1]["content"]


async def test_what_a_project_is_for_is_not_written_for_a_commission_of_no_project(world):
    world.llm.answers[TIER_REVIEW] = [{"review": "It was done.", "card": {**CARD, "description": "An errand."}}]

    commission = await world.finished()

    assert (await world.reviews(commission.id))[0]["card"] == ["title", "duty", "summary"]
    assert (await world.space.get_project(DEFAULT_PROJECT)).description == ""


async def test_a_project_the_review_names_is_described_by_the_review(world):
    world.llm.answers[TIER_REVIEW] = [
        {"review": "It was done.", "project": "Photo archive", "project_description": "The family's photos, sorted."}
    ]

    await world.finished()

    project = await world.space.get_project("Photo archive")
    assert (project.description, project.description_by) == ("The family's photos, sorted.", "review")


async def test_what_a_project_is_for_is_written_by_one_of_three_and_only_where_there_is_a_project(world):
    await world.space.touch_project("Budget app")
    assert (await world.space.get_project("Budget app")).description_by == ""

    for nobody in ("model", "self"):
        # (A session says what it takes itself, not what its project is for.)
        with pytest.raises(ValueError, match="unknown author"):
            await world.space.describe_project("Budget app", "x", by=nobody)
    assert await world.space.describe_project("No such project", "x", by="review") is None
    assert not (world.root / "projects" / "No such project").exists()
    # Nor of the folder commissions of no project are filed under, which is there and has a record.
    await world.space.touch_project(DEFAULT_PROJECT)
    assert await world.space.describe_project(DEFAULT_PROJECT, "x", by="review") is None
    assert (await world.space.get_project(DEFAULT_PROJECT)).description == ""
    assert await world.space.describe_project("Budget app", "   ", by="review") is None
    made = await world.space.describe_project("Budget app", "a\n budget", by="creator")
    assert (made.description, made.description_by) == ("a budget", "creator")
    # A record written before there was an author reads as written by nobody, and is written over.
    assert (await world.space.describe_project("Budget app", "a household budget", by="review")).description_by == "review"
    # The user's stays, whoever comes after; the user changes it.
    await world.space.describe_project("Budget app", "ours", by="user")
    assert await world.space.describe_project("Budget app", "theirs", by="review") is None
    assert await world.space.describe_project("Budget app", "theirs", by="creator") is None
    assert (await world.space.describe_project("Budget app", "ours, still", by="user")).description == "ours, still"


async def test_a_project_whose_record_cannot_be_read_is_not_described_and_not_written_over(world):
    await world.space.touch_project("Budget app", description="a budget")
    record = world.root / "projects" / "Budget app" / "project.json"
    record.write_text("{ this is not json", encoding="utf-8")

    assert await world.space.describe_project("Budget app", "something else", by="review") is None
    assert record.read_text(encoding="utf-8") == "{ this is not json"
    # A folder that has no record yet is no project to describe either.
    (world.root / "projects" / "Bare folder").mkdir()
    assert await world.space.describe_project("Bare folder", "x", by="review") is None
    assert not (world.root / "projects" / "Bare folder" / "project.json").exists()


async def test_what_a_project_is_for_is_kept_though_its_line_in_the_index_cannot_be_written(world, monkeypatch):
    await world.space.touch_project("Budget app", description="a budget")

    def failing(*args: Any, **kwargs: Any) -> None:
        raise OSError("the index is held by another program")

    monkeypatch.setattr(world.space, "_index_set", failing)

    made = await world.space.describe_project("Budget app", "a household budget", by="review")

    assert (made.description, made.description_by) == ("a household budget", "review")
    assert (await world.space.get_project("Budget app")).description == "a household budget"


async def test_the_review_is_written_though_what_the_project_is_for_cannot_be_read(world, monkeypatch):
    commission = await _in_project(world, "Budget app", description="a budget")
    await world.work(commission.id)
    get_project = world.space.get_project

    async def failing(name: str) -> Any:
        raise OSError("the disk does not answer")

    monkeypatch.setattr(world.space, "get_project", failing)
    await world.end(commission.id)
    monkeypatch.setattr(world.space, "get_project", get_project)

    user = world.llm.last(TIER_REVIEW)[1]["content"]
    assert "\nsummary: (none)\ndescription: (none)\n\nWhat the task agent did:" in user
    assert (await world.reviews(commission.id))[0]["status"] == "written"
    assert (await world.ledger.get_session(commission.session_id)).title == "Photo sorter"


# ---------------------------------------------------------------------------
# A card that is missing or wrong does not cost the review
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "answer",
    [
        {"review": "It was done."},
        {"review": "It was done.", "card": "Photo sorter"},
        {"review": "It was done.", "card": ["Photo sorter"]},
        {"review": "It was done.", "card": None},
        {"review": "It was done.", "card": {}},
        {"review": "It was done.", "card": {"title": 5, "duty": None, "summary": ["Sorted."], "description": {"a": 1}}},
        {"review": "It was done.", "card": {"title": "", "duty": "", "summary": "", "description": ""}},
        # Cut off inside the card, where the cap on what the model writes fell.
        '{"review": "It was done.", "entries": [], "project": "", "project_description": "", "card": {"title": "Photo so',
    ],
)
async def test_a_review_whose_card_is_missing_or_wrong_is_kept_and_writes_no_card(world, answer, caplog):
    world.llm.answers[TIER_REVIEW] = [answer]
    commission = await world.file()
    await world.ledger.write_card(commission.session_id, by="creator", duty="sort the photos")
    await world.work(commission.id)

    await world.end(commission.id)

    assert await world.reviews(commission.id) == [{"status": "written", "project": DEFAULT_PROJECT, "entries": []}]
    assert (await world.space.read_review(commission.id)).content.endswith("It was done.")
    assert await _card(world, commission.session_id) == ("", "", "sort the photos", "creator", "", "")
    # Told apart from a card that could not be written: nothing went wrong here.
    assert "could not be kept" not in caplog.text


@pytest.mark.parametrize("description", [5, ["A budget."], {"text": "A budget."}, "   ", None])
async def test_what_a_project_is_for_is_written_only_from_a_text(world, description, caplog):
    world.llm.answers[TIER_REVIEW] = [{"review": "It was done.", "card": {**CARD, "description": description}}]
    commission = await _in_project(world, "Budget app", description="a budget")
    await world.work(commission.id)

    await world.end(commission.id)

    project = await world.space.get_project("Budget app")
    assert (project.description, project.description_by) == ("a budget", "creator")
    assert (await world.reviews(commission.id))[0]["card"] == ["title", "duty", "summary"]
    assert "could not be kept" not in caplog.text


async def test_a_card_that_cannot_be_written_does_not_cost_the_review(world, monkeypatch, caplog):
    async def failing(*args: Any, **kwargs: Any) -> Any:
        raise OSError("the disk is full")

    monkeypatch.setattr(world.ledger, "write_card", failing)

    commission = await world.finished()

    assert await world.reviews(commission.id) == [{"status": "written", "project": DEFAULT_PROJECT, "entries": []}]
    assert (await world.space.read_review(commission.id)).content.endswith("It was done.")
    assert "the card a review wrote could not be kept (OSError)" in caplog.text


async def test_a_review_that_wrote_nothing_usable_writes_no_card(world):
    world.llm.answers[TIER_REVIEW] = [{"review": "", "card": dict(CARD)}]

    commission = await world.finished()

    assert await world.reviews(commission.id) == [{"status": "failed", "reason": "empty"}]
    assert await _card(world, commission.session_id) == ("", "", "", "", "", "")


async def test_a_commission_too_short_to_review_leaves_the_card_alone(world):
    commission = await world.finished(calls=1)

    assert (await world.reviews(commission.id))[0]["status"] == "skipped"
    assert world.llm.calls == []
    assert await _card(world, commission.session_id) == ("", "", "", "", "", "")


# ---------------------------------------------------------------------------
# A commission worked on in a copy of a session
# ---------------------------------------------------------------------------


async def test_a_commission_worked_on_in_a_copy_writes_no_card(world):
    origin = await world.ledger.create_session(lanlan_name="Lan", name="Budget app", kind="project", project="Budget app")
    copy = await world.ledger.create_session(
        lanlan_name="Lan", name="Budget app/fork", kind="fork", project="Budget app", forked_from=origin.id
    )
    await world.space.touch_project("Budget app", description="a budget")
    commission, _created = await world.ledger.create(
        dispatch_text="where does the chart stand", lanlan_name="Lan", title="where does the chart stand",
        source="dispatch", route="task_agent", lang="en", project="Budget app", session_id=copy.id,
    )
    world.llm.answers[TIER_REVIEW] = [{"review": "It was looked up.", "card": {**CARD, "description": "Something else."}}]
    await world.work(commission.id)

    await world.end(commission.id)

    # The review is written; neither the copy's card nor the session's own, nor what the project is for.
    assert await world.reviews(commission.id) == [{"status": "written", "project": "Budget app", "entries": []}]
    assert await _card(world, copy.id) == ("", "", "", "", "", "")
    assert await _card(world, origin.id) == ("", "", "", "", "", "")
    assert (await world.space.get_project("Budget app")).description == "a budget"
    # And the model was told there is none to write.
    user = world.llm.last(TIER_REVIEW)[1]["content"]
    assert "Card of its session now:\n(it has none: leave the lines of card empty)\n\n" in user
