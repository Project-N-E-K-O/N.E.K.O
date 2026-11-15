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

"""Reviews of finished commissions and the tidying of the task agent space."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest

from nekoclaw.host.decision import TIER_ARCHIVE, TIER_REVIEW
from nekoclaw.host.ledger import Ledger, LedgerEvent
from nekoclaw.host.paths import DEFAULT_PROJECT, HostPaths
from nekoclaw.host.space import TaskAgentSpace
from nekoclaw.host.space_keeper import SpaceKeeper, account, tool_calls

DAY = 86400.0


class Clock:
    def __init__(self, now: float = 1_800_000_000.0) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now


class FakeLLM:
    """Answers the two calls of the space and records them."""

    def __init__(self, review: Any = None, archive: Any = None) -> None:
        self.answers: dict[str, list[Any]] = {
            TIER_REVIEW: [review if review is not None else {"review": "It was done."}],
            TIER_ARCHIVE: [archive if archive is not None else {"summary": "A project.", "line": "a project"}],
        }
        self.calls: list[tuple[str, list[dict[str, Any]]]] = []
        self.running = 0
        self.most_at_once = 0
        self.gate: asyncio.Event | None = None

    def count(self, tier: str) -> int:
        return sum(1 for called, _ in self.calls if called == tier)

    def last(self, tier: str) -> list[dict[str, Any]]:
        return next(messages for called, messages in reversed(self.calls) if called == tier)

    async def complete(self, tier: str, messages: list[dict[str, Any]]) -> str:
        self.calls.append((tier, messages))
        self.running += 1
        self.most_at_once = max(self.most_at_once, self.running)
        try:
            if self.gate is not None:
                await self.gate.wait()
            await asyncio.sleep(0)
            answers = self.answers[tier]
            answer = answers[min(self.count(tier) - 1, len(answers) - 1)]
            if callable(answer):
                # Something happens while the model is thinking.
                answer = await answer()
            if isinstance(answer, BaseException):
                raise answer
            return answer if isinstance(answer, str) else json.dumps(answer, ensure_ascii=False)
        finally:
            self.running -= 1


class World:
    """A ledger, a space and a keeper over one temporary data root."""

    def __init__(self, tmp_path: Path, llm: Any) -> None:
        self.clock = Clock()
        self.paths = HostPaths(tmp_path / "nekoclaw")
        self.ledger = Ledger(self.paths.ledger_db, clock=self.clock)
        self.space = TaskAgentSpace(self.paths.space, ledger=self.ledger, clock=self.clock)
        self.llm = llm
        self.keeper = self.new_keeper()
        self.ledger.subscribe(self.keeper.watch)

    def new_keeper(self) -> SpaceKeeper:
        return SpaceKeeper(
            space=self.space,
            ledger=self.ledger,
            llm=self.llm,
            session_dir_of=lambda commission: self.paths.session_dir(commission.id, commission.project),
            clock=self.clock,
            review_delay=0,
        )

    @property
    def root(self) -> Path:
        return self.paths.space().root

    async def file(self, text: str = "sort the photos in Downloads", **values: Any) -> Any:
        values.setdefault("lanlan_name", "Lan")
        values.setdefault("title", text)
        values.setdefault("source", "dispatch")
        values.setdefault("route", "task_agent")
        values.setdefault("lang", "en")
        commission, _created = await self.ledger.create(dispatch_text=text, **values)
        return commission

    async def work(self, commission_id: str, calls: int = 3, tool: str = "bash") -> None:
        await self.ledger.transition(commission_id, "working", reason="a task agent took it up")
        rows: list[tuple[str, dict[str, Any]]] = [("text", {"text": "I will look at the folder first."})]
        for number in range(calls):
            call_id = f"call-{number}"
            rows.append(("tool_start", {"tool_call_id": call_id, "tool_name": tool, "args": {"command": f"step {number}"}}))
            rows.append(("tool_end", {"tool_call_id": call_id, "tool_name": tool, "is_error": False, "text": f"result {number}"}))
        await self.ledger.append_events(commission_id, rows)

    async def work_more(self, commission_id: str, calls: int) -> None:
        rows: list[tuple[str, dict[str, Any]]] = []
        for number in range(calls):
            call_id = f"more-{number}"
            rows.append(("tool_start", {"tool_call_id": call_id, "tool_name": "bash", "args": {"command": f"more {number}"}}))
            rows.append(("tool_end", {"tool_call_id": call_id, "tool_name": "bash", "is_error": False, "text": "ok"}))
        await self.ledger.append_events(commission_id, rows)

    async def end(self, commission_id: str, status: str = "done", summary: str = "sorted 12 photos") -> Any:
        moved = await self.ledger.transition(commission_id, status, reason="test", result_summary=summary)
        assert moved.applied, moved.refusal
        await self.keeper.settle()
        return moved.commission

    async def finished(self, text: str = "sort the photos in Downloads", *, calls: int = 3, status: str = "done", **values: Any) -> Any:
        commission = await self.file(text, **values)
        await self.work(commission.id, calls)
        return await self.end(commission.id, status)

    async def reviews(self, commission_id: str) -> list[dict[str, Any]]:
        return [event.payload for event in await self.ledger.events(commission_id) if event.type == "review"]


@pytest.fixture
async def world(tmp_path: Path) -> Any:
    made = World(tmp_path, FakeLLM())
    yield made
    await made.keeper.aclose()
    await made.ledger.close()


# ---------------------------------------------------------------------------
# Reviews
# ---------------------------------------------------------------------------


async def test_a_finished_commission_is_reviewed_once(world):
    commission = await world.finished()

    path = world.root / "projects" / DEFAULT_PROJECT / "reviews" / f"{commission.id}.md"
    text = path.read_text(encoding="utf-8")
    assert "status: done" in text and "It was done." in text
    assert "**Commission**: sort the photos in Downloads" in text
    assert await world.reviews(commission.id) == [{"status": "written", "project": DEFAULT_PROJECT, "entries": []}]
    assert world.llm.count(TIER_REVIEW) == 1
    # Asked again, by hand or by a later round of tidying: nothing more.
    assert await world.keeper.review(commission.id) == "already"
    assert world.llm.count(TIER_REVIEW) == 1


async def test_the_review_is_shown_what_was_asked_what_was_done_and_what_is_on_record(world):
    await world.space.touch_project("Budget app", description="tracks spending")
    await world.space.file_entry(None, title="Use uv", content="Run Python through uv.")
    commission = await world.file("sort the photos in Downloads", user_text="can you sort my photos", lang="zh-CN")
    await world.work(commission.id)
    await world.ledger.append_event(commission.id, "lesson", {"title": "Photo dates", "category": "lesson"})
    await world.end(commission.id, summary="sorted 12 photos")

    system, user = (message["content"] for message in world.llm.last(TIER_REVIEW))
    # What the free tier recognises a request by.
    assert system.startswith("You are an agentic automation assessment agent,")
    assert "in Simplified Chinese" in system
    for expected in (
        "Commission: sort the photos in Downloads",
        "What the user said: can you sort my photos",
        "Ended as: done",
        "Result: sorted 12 photos",
        "said: I will look at the folder first.",
        'bash({"command": "step 0"}) -> ok: result 0',
        "Already filed during the commission:\n- Photo dates",
        "Known projects:\n- Budget app: tracks spending",
        "- [Use uv](memory/use-uv.md)",
    ):
        assert expected in user, expected


async def test_entries_of_a_review_are_kept_where_they_belong(world):
    world.llm.answers[TIER_REVIEW] = [
        {
            "review": "It sorted the photos.",
            "entries": [
                {"title": "Photo dates", "description": "which date to trust", "type": "reflection", "scope": "project", "content": "Use the EXIF date."},
                {"title": "Use uv", "type": "lesson", "scope": "global", "content": "Run Python through uv."},
                {"title": "Where it stands", "type": "progress", "scope": "global", "content": "Half the folders are sorted."},
                {"title": "One too many", "type": "lesson", "content": "Dropped."},
            ],
        }
    ]

    commission = await world.finished()

    daily = {entry.id: entry for entry in await world.space.list_entries(DEFAULT_PROJECT)}
    everywhere = {entry.id: entry for entry in await world.space.list_entries()}
    # Progress is a project's, whatever the model said; the fourth entry is over the limit.
    assert sorted(daily) == ["photo-dates", "where-it-stands"] and sorted(everywhere) == ["use-uv"]
    assert (daily["photo-dates"].type, daily["photo-dates"].description) == ("reflection", "which date to trust")
    assert daily["photo-dates"].sources == (commission.id,)
    (event,) = await world.reviews(commission.id)
    assert event["entries"] == ["_daily/photo-dates", "use-uv", "_daily/where-it-stands"]
    review = await world.space.read_review(commission.id)
    assert review.entries == ("_daily/photo-dates", "use-uv", "_daily/where-it-stands")


async def test_a_review_can_start_a_project(world, tmp_path):
    workdir = str(tmp_path / "work")
    world.llm.answers[TIER_REVIEW] = [
        {
            "review": "It built the first screen.",
            "entries": [{"title": "Stack", "type": "reference", "content": "Flask and SQLite."}],
            "project": "Budget app",
            "project_description": "a small app that tracks spending",
        }
    ]

    commission = await world.finished("build me a budget app", workdir=workdir)

    project = await world.space.get_project("Budget app")
    assert (project.description, project.workdir, project.commissions) == ("a small app that tracks spending", workdir, 1)
    assert project.last_commission_at == commission.ended_at
    assert (world.root / "projects" / "Budget app" / "reviews" / f"{commission.id}.md").is_file()
    assert (world.root / "projects" / "Budget app" / "memory" / "stack.md").is_file()
    assert "[project: Budget app](<projects/Budget app/MEMORY.md>)" in await world.space.read_index()
    assert not (world.root / "projects" / DEFAULT_PROJECT).exists()
    # Where the session is kept follows from the ledger's project, which stays.
    assert (await world.ledger.get(commission.id)).project is None


async def test_a_review_that_names_a_known_project_uses_that_project(world):
    await world.space.touch_project("Budget app", description="tracks spending")
    world.llm.answers[TIER_REVIEW] = [{"review": "It added an export.", "project": "budget  APP", "project_description": "x"}]

    commission = await world.finished("add an export")

    assert [item.name for item in await world.space.list_projects()] == ["Budget app"]
    assert (await world.space.read_review(commission.id)).project == "Budget app"
    assert (await world.space.get_project("Budget app")).commissions == 2


async def test_a_review_that_names_an_archived_project_does_not_bring_it_back(world):
    await world.space.touch_project("Budget app", description="tracks spending")
    archived = await world.space.archive_project("Budget app", summary="s")
    world.llm.answers[TIER_REVIEW] = [
        {
            "review": "It went on with it.",
            "project": "budget APP",
            "entries": [{"title": "Export", "type": "lesson", "content": "CSV with a BOM."}],
        }
    ]

    commission = await world.finished("go on with that thing")

    # What a model answered is not what brings a project back; naming it in a commission is.
    projects = [(item.name, item.status, item.commissions) for item in await world.space.list_projects()]
    assert ("Budget app", "archived", 1) in projects
    assert [name for name, status, _count in projects if status == "active"] in ([], [DEFAULT_PROJECT])
    assert not (world.root / "projects" / "Budget app").exists()
    assert (world.root / "archive" / archived.archive_year / "Budget app").is_dir()
    assert (await world.space.read_review(commission.id)).project == DEFAULT_PROJECT
    assert (await world.reviews(commission.id))[-1]["status"] == "written"


async def test_a_review_that_names_a_project_goes_to_the_active_one_of_two_of_that_name(world):
    await world.space.touch_project("Budget app")
    await world.space.archive_project("Budget app", summary="s")
    await world.space.touch_project("Budget app")
    world.llm.answers[TIER_REVIEW] = [{"review": "It went on with it.", "project": "budget app"}]

    commission = await world.finished("go on with that thing")

    assert (await world.space.read_review(commission.id)).project == "Budget app"
    assert (await world.space.get_project("Budget app")).commissions == 2


async def test_a_commission_filed_under_a_project_stays_there(world):
    await world.space.touch_project("Budget app")
    world.llm.answers[TIER_REVIEW] = [{"review": "It added an export.", "project": "Something else"}]

    commission = await world.finished("add an export", project="Budget app")

    assert [item.name for item in await world.space.list_projects()] == ["Budget app"]
    assert (await world.space.read_review(commission.id)).project == "Budget app"


@pytest.mark.parametrize("name", ["", "  ", "../outside", "a/b", "_daily", "_secret", ".hidden", "CON", 7, None])
async def test_a_project_name_that_cannot_be_used_leaves_the_commission_where_it_is(world, name):
    world.llm.answers[TIER_REVIEW] = [{"review": "Done.", "project": name}]

    commission = await world.finished()

    assert (await world.space.read_review(commission.id)).project == DEFAULT_PROJECT
    assert [item.name for item in await world.space.list_projects()] == [DEFAULT_PROJECT]
    assert not (world.root.parent / "outside").exists()


@pytest.mark.parametrize("calls,tool", [(0, "bash"), (2, "bash"), (9, "report"), (9, "note_lesson"), (9, "todo_write")])
async def test_a_commission_with_next_to_no_work_is_not_reviewed(world, calls, tool):
    commission = await world.file()
    await world.work(commission.id, calls, tool)
    await world.end(commission.id)

    assert world.llm.calls == []
    (event,) = await world.reviews(commission.id)
    assert (event["status"], event["reason"]) == ("skipped", "short")
    assert await world.space.list_reviews() == []
    # Decided once.
    assert await world.keeper.review(commission.id) == "already"


async def test_three_tool_calls_are_enough(world):
    commission = await world.finished(calls=3)

    assert (await world.reviews(commission.id))[0]["status"] == "written"


async def test_a_stopped_commission_that_did_work_is_reviewed(world):
    commission = await world.finished(status="cancelled")

    assert (await world.reviews(commission.id))[0]["status"] == "written"
    assert "status: cancelled" in (world.root / "projects" / DEFAULT_PROJECT / "reviews" / f"{commission.id}.md").read_text(encoding="utf-8")


async def test_a_failed_commission_is_reviewed_only_when_it_was_left_failed_for_a_day(world):
    # The first round of tidying: from here on commissions are looked after.
    await world.keeper.tidy(force=True)
    commission = await world.finished(status="failed")

    assert world.llm.calls == [] and await world.reviews(commission.id) == []

    world.clock.now += DAY - 60
    await world.keeper.tidy(force=True)
    assert world.llm.calls == []

    world.clock.now += 120
    await world.keeper.tidy(force=True)
    assert world.llm.count(TIER_REVIEW) == 1
    assert (await world.reviews(commission.id))[0]["status"] == "written"

    world.clock.now += DAY
    await world.keeper.tidy(force=True)
    assert world.llm.count(TIER_REVIEW) == 1


async def test_a_failed_commission_taken_up_again_is_reviewed_once_when_it_is_done(world):
    await world.keeper.tidy(force=True)
    commission = await world.finished(status="failed")
    world.clock.now += 3600
    await world.ledger.transition(commission.id, "working", reason="taken up again")
    world.clock.now += 2 * DAY
    await world.keeper.tidy(force=True)
    assert world.llm.calls == []

    await world.end(commission.id, "done")
    world.clock.now += 2 * DAY
    await world.keeper.tidy(force=True)

    assert world.llm.count(TIER_REVIEW) == 1
    assert [event["status"] for event in await world.reviews(commission.id)] == ["written"]


async def test_a_review_of_a_commission_taken_up_again_meanwhile_is_thrown_away(world):
    await world.keeper.tidy(force=True)
    commission = await world.finished(status="failed")
    world.clock.now += DAY + 60

    async def meanwhile() -> dict[str, Any]:
        # The user's words take it up again while the model is writing about its failure.
        await world.ledger.transition(commission.id, "working", reason="taken up again")
        return {"review": "It failed.", "entries": [{"title": "Stale", "content": "From the failure."}], "project": "Stale project"}

    world.llm.answers[TIER_REVIEW] = [meanwhile, {"review": "It was done in the end."}]

    await world.keeper.tidy(force=True)

    # Nothing of the stale answer was kept, and nothing says it was reviewed.
    assert await world.reviews(commission.id) == []
    assert await world.space.list_reviews() == [] and await world.space.list_entries(DEFAULT_PROJECT) == []
    assert [item.name for item in await world.space.list_projects()] == []

    await world.end(commission.id, "done")

    assert [event["status"] for event in await world.reviews(commission.id)] == ["written"]
    assert (await world.space.read_review(commission.id)).content.endswith("It was done in the end.")


async def test_a_review_of_a_commission_that_failed_anew_meanwhile_is_thrown_away(world):
    await world.keeper.tidy(force=True)
    commission = await world.finished(status="failed")
    world.clock.now += DAY + 60

    async def meanwhile() -> dict[str, Any]:
        # Taken up again and failed again: failed as before, but it is another end.
        await world.ledger.transition(commission.id, "working", reason="taken up again")
        world.clock.now += 5
        await world.ledger.transition(commission.id, "failed", reason="failed again")
        return {"review": "It failed the first time."}

    world.llm.answers[TIER_REVIEW] = [meanwhile]

    assert await world.keeper.review(commission.id) == "stale"
    assert await world.reviews(commission.id) == [] and await world.space.list_reviews() == []


async def test_a_failed_commission_in_use_gets_no_stale_review_and_one_reviewed_is_held_meanwhile(world):
    await world.keeper.tidy(force=True)
    in_use = await world.finished("in use by the time the model answers", status="failed")
    free = await world.finished("left failed", status="failed")
    done = await world.finished("done")
    held: list[str] = []
    released: list[str] = []

    def hold(commission_id: str) -> bool:
        if commission_id == in_use.id:
            return False
        held.append(commission_id)
        return True

    world.keeper._hold_session = hold
    world.keeper._release_session = released.append

    assert await world.keeper.review(in_use.id) == "stale"
    assert await world.keeper.review(free.id) == "written"

    assert await world.reviews(in_use.id) == []
    assert held == [free.id] and released == [free.id]
    # A commission that is done cannot be taken up again and is not held at all.
    assert (await world.reviews(done.id))[0]["status"] == "written"


async def test_a_short_commission_taken_up_again_meanwhile_is_not_marked_as_skipped(world):
    await world.keeper.tidy(force=True)
    commission = await world.finished(calls=1, status="failed")
    world.clock.now += DAY + 60
    read = world.keeper._events

    async def events(commission_id: str) -> Any:
        found = await read(commission_id)
        # Taken up again while what it did was being read.
        await world.ledger.transition(commission_id, "working", reason="taken up again")
        return found

    world.keeper._events = events

    assert await world.keeper.review(commission.id) == "stale"
    assert await world.reviews(commission.id) == []

    # It goes on to do real work, and that is what is reviewed.
    world.keeper._events = read
    await world.work_more(commission.id, 3)
    await world.end(commission.id, "done")

    assert [event["status"] for event in await world.reviews(commission.id)] == ["written"]


async def test_a_blocked_commission_is_not_reviewed(world):
    await world.keeper.tidy(force=True)
    commission = await world.finished(status="blocked")
    world.clock.now += 5 * DAY
    await world.keeper.tidy(force=True)

    assert world.llm.calls == [] and await world.reviews(commission.id) == []
    assert await world.keeper.review(commission.id) == "not_due"


async def test_a_review_a_shutdown_cut_off_is_made_by_a_later_round(world):
    await world.keeper.tidy(force=True)
    world.llm.gate = asyncio.Event()
    commission = await world.file()
    await world.work(commission.id)
    await world.ledger.transition(commission.id, "done", reason="test")
    while not world.llm.calls:
        await asyncio.sleep(0.01)
    await world.keeper.aclose()
    assert await world.reviews(commission.id) == []

    world.llm.gate = None
    world.keeper = world.new_keeper()
    world.clock.now += DAY + 60
    await world.keeper.tidy(force=True)

    assert [event["status"] for event in await world.reviews(commission.id)] == ["written"]


async def test_commissions_that_ended_before_the_space_was_first_tidied_are_not_reviewed_after_the_fact(world):
    # Ended while no keeper was listening: a build from before the space.
    world.keeper._closed = True
    old = await world.finished()
    world.keeper._closed = False
    world.clock.now += 3 * DAY

    await world.keeper.tidy(force=True)
    world.clock.now += 3 * DAY
    await world.keeper.tidy(force=True)

    assert world.llm.calls == [] and await world.reviews(old.id) == []


async def test_at_most_a_few_late_reviews_per_round(world):
    from config.nekoclaw_settings import TASK_AGENT_REVIEWS_PER_UPKEEP

    await world.keeper.tidy(force=True)
    for number in range(TASK_AGENT_REVIEWS_PER_UPKEEP + 2):
        await world.finished(f"task number {number} with words{number}", status="failed")
    world.clock.now += DAY + 60

    await world.keeper.tidy(force=True)

    assert world.llm.count(TIER_REVIEW) == TASK_AGENT_REVIEWS_PER_UPKEEP


@pytest.mark.parametrize("change", [{"route": "plugin"}, {"parent_id": "parent"}])
async def test_only_her_own_commissions_done_by_a_task_agent_are_reviewed(world, change):
    values = {"source": "task_agent"} if "parent_id" in change else {}
    commission = await world.finished(**change, **values)

    assert world.llm.calls == [] and await world.reviews(commission.id) == []
    assert await world.keeper.review(commission.id) == "not_due"


@pytest.mark.parametrize(
    "answer,reason",
    [("not json at all", "unreadable"), ("[1, 2]", "unreadable"), ({"review": "  "}, "empty"), ({"entries": []}, "empty")],
)
async def test_an_answer_that_cannot_be_used_is_recorded_and_not_asked_for_again(world, answer, reason):
    world.llm.answers[TIER_REVIEW] = [answer]

    commission = await world.finished()

    assert await world.reviews(commission.id) == [{"status": "failed", "reason": reason}]
    assert await world.space.list_reviews() == []
    world.clock.now += 2 * DAY
    await world.keeper.tidy(force=True)
    assert world.llm.count(TIER_REVIEW) == 1


async def test_a_model_that_fails_is_recorded_and_not_asked_again(world):
    world.llm.answers[TIER_REVIEW] = [RuntimeError("no connection")]

    commission = await world.finished()

    assert await world.reviews(commission.id) == [{"status": "failed", "reason": "RuntimeError"}]
    assert await world.keeper.review(commission.id) == "already"
    assert world.llm.count(TIER_REVIEW) == 1


async def test_an_answer_in_a_code_fence_is_read(world):
    world.llm.answers[TIER_REVIEW] = ['```json\n{"review": "Fenced."}\n```']

    commission = await world.finished()

    assert (await world.space.read_review(commission.id)).content.endswith("Fenced.")


async def test_without_a_model_nothing_is_reviewed_and_nothing_is_recorded(tmp_path):
    world = World(tmp_path, None)
    try:
        commission = await world.finished()

        assert await world.reviews(commission.id) == []
        assert await world.keeper.review(commission.id) == "no_model"
    finally:
        await world.keeper.aclose()
        await world.ledger.close()


async def test_reviews_are_made_one_at_a_time(world):
    commissions = [await world.file(f"task number {number} with words{number}") for number in range(4)]
    for commission in commissions:
        await world.work(commission.id)
    # The model holds every call until it is let go.
    world.llm.gate = asyncio.Event()
    await asyncio.gather(*(world.ledger.transition(commission.id, "done", reason="test") for commission in commissions))
    while not world.llm.calls:
        await asyncio.sleep(0.01)
    # Time enough for the other three to reach the model, were they allowed to.
    await asyncio.sleep(0.3)
    assert (len(world.llm.calls), world.llm.running) == (1, 1)

    world.llm.gate.set()
    await world.keeper.settle()

    assert world.llm.count(TIER_REVIEW) == 4
    assert world.llm.most_at_once == 1


async def test_what_is_sent_for_a_review_stays_within_its_cap(world):
    from config.nekoclaw_settings import TASK_AGENT_REVIEW_INPUT_MAX_TOKENS, TASK_AGENT_REVIEW_PROCESS_MAX_TOKENS
    from utils.tokenize import count_tokens

    for number in range(40):
        await world.space.touch_project(f"Project number {number} long name", description="description words " * 30)
        await world.space.file_entry(None, title=f"Global topic {number} g{number}", content="text " * 200)
        await world.space.file_entry(DEFAULT_PROJECT, title=f"Daily topic {number} d{number}", content="text " * 200)
    commission = await world.file("dispatch words " * 400, user_text="user words " * 400)
    await world.ledger.transition(commission.id, "working", reason="test")
    rows: list[tuple[str, dict[str, Any]]] = []
    for number in range(400):
        call_id = f"call-{number}"
        rows.append(("text", {"text": f"thinking about step {number} " * 50}))
        rows.append(("tool_start", {"tool_call_id": call_id, "tool_name": "bash", "args": {"command": "x" * 3000}}))
        rows.append(("tool_end", {"tool_call_id": call_id, "tool_name": "bash", "is_error": number % 7 == 0, "text": "y" * 3000}))
        rows.append(("lesson", {"title": f"Lesson {number} " * 20, "category": "lesson"}))
    await world.ledger.append_events(commission.id, rows)
    await world.end(commission.id, summary="result words " * 400)

    system, user = (message["content"] for message in world.llm.last(TIER_REVIEW))
    total = count_tokens(system) + count_tokens(user)
    assert total <= TASK_AGENT_REVIEW_INPUT_MAX_TOKENS
    # What the owner was told: one call per commission, about 4000 tokens in at most.
    assert TASK_AGENT_REVIEW_INPUT_MAX_TOKENS == 4000
    # The account keeps how it began and how it ended.
    process = user.split("What the task agent did:\n", 1)[1].split("\n\nAlready filed", 1)[0]
    assert count_tokens(process) <= TASK_AGENT_REVIEW_PROCESS_MAX_TOKENS
    assert process.startswith("said: thinking about step 0 ")
    assert "steps left out ...]" in process
    assert process.rstrip().split("\n")[-1].startswith("bash(")
    # With every part at its cap, none was pushed out by another.
    assert "Index of this project:\n- [Daily topic" in user


async def test_the_whole_of_a_review_is_capped_whatever_its_parts_are(world, monkeypatch):
    from config.nekoclaw_settings import TASK_AGENT_REVIEW_INPUT_MAX_TOKENS
    from utils.tokenize import count_tokens

    # A part that outgrows the room it was meant to have.
    monkeypatch.setattr("config.nekoclaw_settings.TASK_AGENT_REVIEW_PROCESS_MAX_TOKENS", 50_000)
    commission = await world.file()
    await world.ledger.transition(commission.id, "working", reason="test")
    rows: list[tuple[str, dict[str, Any]]] = []
    for number in range(600):
        call_id = f"call-{number}"
        rows.append(("tool_start", {"tool_call_id": call_id, "tool_name": "bash", "args": {"command": f"step {number} " * 20}}))
        rows.append(("tool_end", {"tool_call_id": call_id, "tool_name": "bash", "is_error": False, "text": f"result {number} " * 20}))
    await world.ledger.append_events(commission.id, rows)
    await world.end(commission.id)

    system, user = (message["content"] for message in world.llm.last(TIER_REVIEW))
    assert count_tokens(system) + count_tokens(user) <= TASK_AGENT_REVIEW_INPUT_MAX_TOKENS
    assert "steps left out" not in user


def test_the_parts_of_a_review_fit_its_cap_so_that_none_is_cut_off_at_the_end():
    from config import nekoclaw_settings as settings
    from config.prompts import prompts_nekoclaw_space as texts
    from utils.tokenize import count_tokens

    system = texts.TASK_AGENT_REVIEW_SYSTEM_PROMPT.format(language="Traditional Chinese", max_entries=settings.TASK_AGENT_REVIEW_MAX_ENTRIES)
    frame = texts.TASK_AGENT_REVIEW_USER_TEMPLATE.format(
        dispatch_text="", user_text="", status="cancelled", result="", project="", process="", filed="",
        projects="", global_index="", project_index="",
    )
    parts = (
        settings.DISPATCH_TEXT_MAX_TOKENS
        + settings.DISPATCH_USER_TEXT_MAX_TOKENS
        + settings.TASK_AGENT_RESULT_SUMMARY_MAX_TOKENS
        + settings.TASK_AGENT_REVIEW_PROCESS_MAX_TOKENS
        + 120  # what was already filed during the commission
        + settings.TASK_AGENT_REVIEW_PROJECTS_MAX_TOKENS
        + 2 * settings.TASK_AGENT_REVIEW_INDEX_MAX_TOKENS
        + settings.SPACE_PROJECT_NAME_MAX_TOKENS
    )

    assert count_tokens(system) + count_tokens(frame) + parts <= settings.TASK_AGENT_REVIEW_INPUT_MAX_TOKENS


def test_the_review_asks_for_no_more_output_than_was_agreed():
    from config.nekoclaw_settings import SPACE_ARCHIVE_OUTPUT_MAX_TOKENS, TASK_AGENT_REVIEW_OUTPUT_MAX_TOKENS

    assert TASK_AGENT_REVIEW_OUTPUT_MAX_TOKENS <= 800
    assert SPACE_ARCHIVE_OUTPUT_MAX_TOKENS <= 600


def test_the_two_calls_of_the_space_are_metered_under_their_own_names(monkeypatch):
    import utils.token_tracker as tracker
    from nekoclaw.host import decision

    named: list[str] = []
    monkeypatch.setattr(tracker, "set_call_type", named.append)

    class Client:
        async def ainvoke(self, messages: Any) -> Any:
            return type("Response", (), {"content": "ok"})()

    llm = decision.ConfiguredDecisionLLM()
    seen: list[tuple[str, int]] = []

    async def client(tier: str, budget: int) -> Any:
        seen.append((tier, budget))
        return Client()

    monkeypatch.setattr(llm, "_client", client)

    async def run() -> None:
        for tier in (TIER_REVIEW, TIER_ARCHIVE, decision.TIER_COARSE):
            await llm.complete(tier, [])

    asyncio.run(run())

    assert named == ["task_agent_review", "task_agent_archive", "agent"]
    assert seen[0] == (TIER_REVIEW, 800) and seen[1] == (TIER_ARCHIVE, 600)
    # Both run on the summary model, like the fine selection.
    assert {TIER_REVIEW, TIER_ARCHIVE, decision.TIER_FINE} == decision._SUMMARY_TIERS


# ---------------------------------------------------------------------------
# The account of what a task agent did
# ---------------------------------------------------------------------------


def _event(kind: str, **payload: Any) -> LedgerEvent:
    return LedgerEvent("c1", 1, 0.0, kind, payload)


def test_the_account_has_one_line_per_step():
    events = [
        _event("created", title="x"),
        _event("text", text="First I "),
        _event("text", text="look around."),
        _event("tool_start", tool_call_id="a", tool_name="glob", args={"pattern": "*.jpg"}),
        _event("tool_update", tool_call_id="a", tool_name="glob", text="..."),
        _event("tool_end", tool_call_id="a", tool_name="glob", is_error=False, text="12 files"),
        _event("usage"),
        _event("tool_start", tool_call_id="b", tool_name="bash", args='{"command": "mv"}'),
        _event("tool_end", tool_call_id="b", tool_name="bash", is_error=True, text="Error: no such file"),
        _event("told", text="use the EXIF date"),
        _event("tool_start", tool_call_id="c", tool_name="report", args={"status": "ready"}),
        _event("tool_end", tool_call_id="c", tool_name="report", is_error=False, text="delivered"),
        _event("report", status="ready", summary="sorted 12 photos"),
        _event("error", message="the model refused"),
        _event("subagent_started", name="agent-1"),
        _event("status", to="done"),
    ]

    assert account(events, 2000, 60).split("\n") == [
        "said: First I look around.",
        'glob({"pattern": "*.jpg"}) -> ok: 12 files',
        'bash({"command": "mv"}) -> error: Error: no such file',
        "user: use the EXIF date",
        "report ready: sorted 12 photos",
        "error: the model refused",
        "started task agent agent-1",
    ]
    assert tool_calls(events) == 2
    assert account([], 2000, 60) == ""


# ---------------------------------------------------------------------------
# Tidying
# ---------------------------------------------------------------------------


async def test_tidying_runs_at_most_every_few_hours_and_remembers_across_restarts(world):
    from config.nekoclaw_settings import SPACE_UPKEEP_INTERVAL_SECONDS

    assert await world.keeper.tidy() is True
    first = (await world.space.read_state())["upkeep_at"]
    world.clock.now += SPACE_UPKEEP_INTERVAL_SECONDS - 1
    assert await world.keeper.tidy() is False
    # A new host process reads when it was last done.
    assert await world.new_keeper().tidy() is False
    assert (await world.space.read_state())["upkeep_at"] == first

    world.clock.now += 2
    assert await world.new_keeper().tidy() is True
    state = await world.space.read_state()
    assert state["upkeep_at"] == world.clock.now and state["since"] == first


async def test_a_commission_ending_is_what_starts_a_round(world):
    assert await world.space.read_state() == {}

    await world.finished(route="plugin")

    assert (await world.space.read_state())["upkeep_at"] == world.clock.now


async def test_an_idle_project_is_archived_with_a_summary(world):
    from config.nekoclaw_settings import SPACE_ARCHIVE_INPUT_MAX_TOKENS, SPACE_PROJECT_IDLE_SECONDS
    from utils.tokenize import count_tokens

    await world.space.touch_project("Budget app", description="tracks spending", workdir="D:/w")
    await world.space.file_entry("Budget app", title="Stack", content="Flask and SQLite.")
    review = await world.finished("add an export", project="Budget app")
    await world.space.touch_project("Blog")
    world.llm.answers[TIER_ARCHIVE] = [
        {
            "summary": "An app that tracks spending. It has an export.",
            "line": "spending tracker with CSV export, paused",
            "entries": [{"title": "CSV on Windows", "type": "lesson", "content": "Write a BOM so Excel reads UTF-8."}],
        }
    ]
    world.clock.now += SPACE_PROJECT_IDLE_SECONDS - 60
    await world.keeper.tidy(force=True)
    assert world.llm.count(TIER_ARCHIVE) == 0

    world.clock.now += 120
    await world.space.touch_project("Blog")
    await world.keeper.tidy(force=True)

    assert world.llm.count(TIER_ARCHIVE) == 1
    assert [(item.name, item.status) for item in await world.space.list_projects()] == [
        ("Blog", "active"),
        (DEFAULT_PROJECT, "active"),
        ("Budget app", "archived"),
    ] or [(item.name, item.status) for item in await world.space.list_projects()] == [
        ("Blog", "active"),
        ("Budget app", "archived"),
    ]
    (archived,) = [item for item in await world.space.list_projects() if item.status == "archived"]
    home = world.root / "archive" / archived.archive_year / "Budget app"
    assert (home / "SUMMARY.md").read_text(encoding="utf-8") == "An app that tracks spending. It has an export.\n"
    assert (home / "reviews" / f"{review.id}.md").is_file() and (home / "memory" / "stack.md").is_file()
    index = await world.space.read_index()
    assert f"[archived project: Budget app](<archive/{archived.archive_year}/Budget app/SUMMARY.md>) — spending tracker with CSV export, paused" in index
    assert "[project: Budget app]" not in index
    # What holds beyond the project went to the space of every project.
    assert [entry.id for entry in await world.space.list_entries()] == ["csv-on-windows"]
    system, user = (message["content"] for message in world.llm.last(TIER_ARCHIVE))
    assert system.startswith("You are an agentic automation assessment agent,")
    assert "Project: Budget app" in user and "- [Stack](memory/stack.md)" in user and "add an export (done)" in user
    assert count_tokens(system) + count_tokens(user) <= SPACE_ARCHIVE_INPUT_MAX_TOKENS


async def test_what_is_sent_for_a_summary_stays_within_its_cap(world):
    from config.nekoclaw_settings import SPACE_ARCHIVE_INPUT_MAX_TOKENS, SPACE_PROJECT_IDLE_SECONDS
    from utils.tokenize import count_tokens

    # The path of its files has no cap of its own.
    await world.space.touch_project("Budget app", description="tracks spending " * 50, workdir="D:/" + "folder/" * 4000)
    for number in range(60):
        await world.space.file_entry("Budget app", title=f"Topic {number} t{number}", content="text " * 300)
        await world.space.file_entry(None, title=f"Global {number} g{number}", content="text " * 300)
        commission = await world.file(f"task {number} " * 40)
        await world.space.write_review("Budget app", commission, "review words " * 400)
    world.clock.now += SPACE_PROJECT_IDLE_SECONDS + 60

    await world.keeper.tidy(force=True)

    system, user = (message["content"] for message in world.llm.last(TIER_ARCHIVE))
    assert count_tokens(system) + count_tokens(user) <= SPACE_ARCHIVE_INPUT_MAX_TOKENS
    assert SPACE_ARCHIVE_INPUT_MAX_TOKENS == 3000


async def test_a_project_with_a_commission_under_way_is_not_archived(world):
    from config.nekoclaw_settings import SPACE_PROJECT_IDLE_SECONDS

    await world.space.touch_project("Budget app")
    commission = await world.file("add an export", project="Budget app")
    await world.work(commission.id)
    world.clock.now += SPACE_PROJECT_IDLE_SECONDS + 60

    await world.keeper.tidy(force=True)

    assert world.llm.count(TIER_ARCHIVE) == 0
    assert [(item.name, item.status) for item in await world.space.list_projects()] == [("Budget app", "active")]


async def test_a_project_that_gets_a_commission_while_its_summary_is_written_is_not_archived(world):
    from config.nekoclaw_settings import SPACE_PROJECT_IDLE_SECONDS

    await world.space.touch_project("Budget app")
    world.clock.now += SPACE_PROJECT_IDLE_SECONDS + 60

    async def meanwhile() -> dict[str, Any]:
        # A commission that names the project starts before the model answers.
        await world.space.assign_project(await world.file("go on with the budget app"))
        return {"summary": "A project.", "line": "a project"}

    world.llm.answers[TIER_ARCHIVE] = [meanwhile]

    await world.keeper.tidy(force=True)

    assert world.llm.count(TIER_ARCHIVE) == 1
    assert [(item.name, item.status) for item in await world.space.list_projects()] == [("Budget app", "active")]
    assert not (world.root / "archive").exists()


async def test_a_project_whose_commission_is_taken_up_while_its_summary_is_written_is_not_archived(world):
    from config.nekoclaw_settings import SPACE_PROJECT_IDLE_SECONDS

    await world.space.touch_project("Budget app")
    failed = await world.finished("add an export", calls=0, status="failed", project="Budget app")
    world.clock.now += SPACE_PROJECT_IDLE_SECONDS + 60

    async def meanwhile() -> dict[str, Any]:
        # Taken up again where it failed: no commission is filed, the ledger alone shows it.
        await world.ledger.transition(failed.id, "working", reason="taken up again")
        return {"summary": "A project.", "line": "a project"}

    world.llm.answers[TIER_ARCHIVE] = [meanwhile]

    await world.keeper.tidy(force=True)

    assert [(item.name, item.status) for item in await world.space.list_projects()] == [("Budget app", "active")]


async def test_the_daily_project_is_never_archived(world):
    from config.nekoclaw_settings import SPACE_PROJECT_IDLE_SECONDS

    await world.space.touch_project(DEFAULT_PROJECT)
    world.clock.now += 10 * SPACE_PROJECT_IDLE_SECONDS

    await world.keeper.tidy(force=True)

    assert world.llm.calls == []
    assert [(item.name, item.status) for item in await world.space.list_projects()] == [(DEFAULT_PROJECT, "active")]


async def test_a_summary_that_cannot_be_written_is_asked_for_a_few_times_and_then_done_without(world):
    from config.nekoclaw_settings import SPACE_ARCHIVE_MAX_ATTEMPTS, SPACE_PROJECT_IDLE_SECONDS

    await world.space.touch_project("Budget app", description="tracks spending")
    world.llm.answers[TIER_ARCHIVE] = [RuntimeError("no connection")]
    world.clock.now += SPACE_PROJECT_IDLE_SECONDS + 60

    for attempt in range(1, SPACE_ARCHIVE_MAX_ATTEMPTS):
        await world.keeper.tidy(force=True)
        assert world.llm.count(TIER_ARCHIVE) == attempt
        assert [item.status for item in await world.space.list_projects()] == ["active"]

    await world.keeper.tidy(force=True)

    assert world.llm.count(TIER_ARCHIVE) == SPACE_ARCHIVE_MAX_ATTEMPTS
    (archived,) = await world.space.list_projects()
    assert archived.status == "archived"
    home = world.root / "archive" / archived.archive_year / "Budget app"
    assert not (home / "SUMMARY.md").exists()
    assert "[archived project: Budget app]" in await world.space.read_index() and "tracks spending" in await world.space.read_index()
    # Archived: no further round asks.
    await world.keeper.tidy(force=True)
    assert world.llm.count(TIER_ARCHIVE) == SPACE_ARCHIVE_MAX_ATTEMPTS


async def test_without_a_model_an_idle_project_is_archived_without_a_summary(tmp_path):
    from config.nekoclaw_settings import SPACE_PROJECT_IDLE_SECONDS

    world = World(tmp_path, None)
    try:
        await world.space.touch_project("Budget app")
        world.clock.now += SPACE_PROJECT_IDLE_SECONDS + 60

        await world.keeper.tidy(force=True)

        assert [item.status for item in await world.space.list_projects()] == ["archived"]
    finally:
        await world.keeper.aclose()
        await world.ledger.close()


async def test_at_most_a_few_projects_are_archived_per_round(world):
    from config.nekoclaw_settings import SPACE_ARCHIVES_PER_UPKEEP, SPACE_PROJECT_IDLE_SECONDS

    for number in range(SPACE_ARCHIVES_PER_UPKEEP + 2):
        await world.space.touch_project(f"Project {number}")
    world.clock.now += SPACE_PROJECT_IDLE_SECONDS + 60

    await world.keeper.tidy(force=True)

    assert world.llm.count(TIER_ARCHIVE) == SPACE_ARCHIVES_PER_UPKEEP
    statuses = [item.status for item in await world.space.list_projects()]
    assert statuses.count("archived") == SPACE_ARCHIVES_PER_UPKEEP


def _fill_session(directory: Path) -> None:
    (directory / "outputs").mkdir(parents=True)
    (directory / "work" / "sub").mkdir(parents=True)
    (directory / "transcript.jsonl").write_text("{}\n", encoding="utf-8")
    (directory / "outputs" / "1.txt").write_text("big output", encoding="utf-8")
    (directory / "work" / "sub" / "result.txt").write_text("the user's file", encoding="utf-8")
    (directory / "notes.txt").write_text("something else", encoding="utf-8")


async def test_old_sessions_lose_what_was_said_and_keep_their_working_directory(world):
    from config.nekoclaw_settings import SPACE_TRANSCRIPT_KEPT_SECONDS

    old = [await world.finished(f"old task {status}", calls=0, status=status) for status in ("done", "failed", "cancelled")]
    child = await world.finished("old child", calls=0, parent_id=old[0].id, source="task_agent")
    stuck = await world.finished("stuck", calls=0, status="blocked")
    busy = await world.file("still going")
    await world.work(busy.id, 0)
    by_plugin = await world.finished("by a plugin", calls=0, route="plugin")
    world.clock.now += SPACE_TRANSCRIPT_KEPT_SECONDS - 3600
    young = await world.finished("young", calls=0)
    everyone = [*old, child, stuck, busy, by_plugin, young]
    for commission in everyone:
        _fill_session(world.paths.session_dir(commission.id))
    world.clock.now += 7200

    await world.keeper.tidy(force=True)

    for commission in (*old, child):
        directory = world.paths.session_dir(commission.id)
        assert not (directory / "transcript.jsonl").exists() and not (directory / "outputs").exists(), commission.title
        assert (directory / "work" / "sub" / "result.txt").read_text(encoding="utf-8") == "the user's file"
        assert (directory / "notes.txt").is_file()
        assert [event.type for event in await world.ledger.events(commission.id)][-1] == "session_cleaned"
    for commission in (stuck, busy, by_plugin, young):
        directory = world.paths.session_dir(commission.id)
        assert (directory / "transcript.jsonl").is_file() and (directory / "outputs" / "1.txt").is_file(), commission.title
    assert (await world.space.read_state())["sessions_cleaned_until"] == world.clock.now - SPACE_TRANSCRIPT_KEPT_SECONDS

    # The young one's turn comes when it is old enough.
    world.clock.now += SPACE_TRANSCRIPT_KEPT_SECONDS
    await world.keeper.tidy(force=True)
    assert not (world.paths.session_dir(young.id) / "transcript.jsonl").exists()
    assert (world.paths.session_dir(young.id) / "work" / "sub" / "result.txt").is_file()


async def test_a_commission_taken_up_again_since_the_query_keeps_its_transcript(world):
    from config.nekoclaw_settings import SPACE_TRANSCRIPT_KEPT_SECONDS

    resumed = await world.finished("failed long ago", calls=0, status="failed")
    other = await world.finished("done long ago", calls=0)
    for commission in (resumed, other):
        _fill_session(world.paths.session_dir(commission.id))
    world.clock.now += SPACE_TRANSCRIPT_KEPT_SECONDS + 60
    found = world.keeper._ended_long_ago

    def query(conn: Any, after: float, after_id: str, until: float) -> Any:
        rows = found(conn, after, after_id, until)
        # Between the query and the removal the user's words take the failed one up again.
        conn.execute("UPDATE commissions SET status = 'working' WHERE id = ?", (resumed.id,))
        return rows

    world.keeper._ended_long_ago = query

    await world.keeper.tidy(force=True)

    assert (world.paths.session_dir(resumed.id) / "transcript.jsonl").is_file()
    assert (world.paths.session_dir(resumed.id) / "outputs" / "1.txt").is_file()
    assert not (world.paths.session_dir(other.id) / "transcript.jsonl").exists()


async def test_a_session_in_use_is_not_cleaned_and_a_cleaned_one_is_held_meanwhile(world):
    from config.nekoclaw_settings import SPACE_TRANSCRIPT_KEPT_SECONDS

    in_use = await world.finished("in use", calls=0)
    free = await world.finished("free", calls=0)
    for commission in (in_use, free):
        _fill_session(world.paths.session_dir(commission.id))
    world.clock.now += SPACE_TRANSCRIPT_KEPT_SECONDS + 60
    held: list[str] = []
    released: list[str] = []
    seen_held_while_removing: list[bool] = []
    remove = world.keeper._clean_session

    def hold(commission_id: str) -> bool:
        if commission_id == in_use.id:
            return False
        held.append(commission_id)
        return True

    def removing(directory: Path, commission_id: str) -> str:
        seen_held_while_removing.append(commission_id in held and commission_id not in released)
        return remove(directory, commission_id)

    world.keeper._hold_session = hold
    world.keeper._release_session = released.append
    world.keeper._clean_session = removing

    await world.keeper.tidy(force=True)

    assert (world.paths.session_dir(in_use.id) / "transcript.jsonl").is_file()
    assert not (world.paths.session_dir(free.id) / "transcript.jsonl").exists()
    assert held == [free.id] and released == [free.id]
    assert seen_held_while_removing == [True]
    # The one in use is not passed over: its taking up may fail without its
    # status changing, and then it is as old as it was.
    assert (await world.space.read_state())["sessions_to_retry"] == [in_use.id]

    world.keeper._hold_session = lambda commission_id: True
    world.clock.now += 3600
    await world.keeper.tidy(force=True)

    assert not (world.paths.session_dir(in_use.id) / "transcript.jsonl").exists()
    assert (await world.space.read_state())["sessions_to_retry"] == []


async def test_a_session_whose_files_could_not_be_removed_is_tried_again_next_round(world):
    from config.nekoclaw_settings import SPACE_TRANSCRIPT_KEPT_SECONDS

    stuck = await world.finished("its transcript is open in an editor", calls=0)
    world.clock.now += 100
    later = await world.finished("ended later", calls=0)
    for commission in (stuck, later):
        _fill_session(world.paths.session_dir(commission.id))
    world.clock.now += SPACE_TRANSCRIPT_KEPT_SECONDS + 60
    remove = world.keeper._clean_session
    busy = {"yes": True}

    def removing(directory: Path, commission_id: str) -> str:
        if commission_id == stuck.id and busy["yes"]:
            return "not_removed"
        return remove(directory, commission_id)

    world.keeper._clean_session = removing

    await world.keeper.tidy(force=True)

    assert (world.paths.session_dir(stuck.id) / "transcript.jsonl").is_file()
    assert not (world.paths.session_dir(later.id) / "transcript.jsonl").exists()
    # Not forgotten: it is remembered by itself, and the scan has moved on.
    state = await world.space.read_state()
    assert state["sessions_to_retry"] == [stuck.id]
    assert state["sessions_cleaned_until"] == world.clock.now - SPACE_TRANSCRIPT_KEPT_SECONDS
    assert "session_cleaned" not in [event.type for event in await world.ledger.events(stuck.id)]

    busy["yes"] = False
    world.clock.now += 3600
    await world.keeper.tidy(force=True)

    assert not (world.paths.session_dir(stuck.id) / "transcript.jsonl").exists()
    state = await world.space.read_state()
    assert state["sessions_to_retry"] == []
    assert state["sessions_cleaned_until"] == world.clock.now - SPACE_TRANSCRIPT_KEPT_SECONDS
    assert [event.type for event in await world.ledger.events(stuck.id)][-1] == "session_cleaned"


async def test_a_session_that_never_lets_go_does_not_hold_the_later_ones_back(world, monkeypatch):
    from config.nekoclaw_settings import SPACE_TRANSCRIPT_KEPT_SECONDS

    monkeypatch.setattr("nekoclaw.host.space_keeper._CLEAN_BATCH", 2)
    commissions = []
    for number in range(5):
        commissions.append(await world.finished(f"old task number {number} words{number}", calls=0))
        world.clock.now += 10
    for commission in commissions:
        _fill_session(world.paths.session_dir(commission.id))
    stuck = commissions[0]
    world.clock.now += SPACE_TRANSCRIPT_KEPT_SECONDS + 60
    remove = world.keeper._clean_session
    tried: list[str] = []

    def removing(directory: Path, commission_id: str) -> str:
        if commission_id == stuck.id:
            tried.append(commission_id)
            return "not_removed"
        return remove(directory, commission_id)

    world.keeper._clean_session = removing

    for _round in range(4):
        await world.keeper.tidy(force=True)

    # Two a round: every other session was reached, though the first never let go.
    for commission in commissions[1:]:
        assert not (world.paths.session_dir(commission.id) / "transcript.jsonl").exists(), commission.title
    assert (world.paths.session_dir(stuck.id) / "transcript.jsonl").is_file()
    # Tried once a round, not once per session of the round.
    assert tried == [stuck.id] * 4
    assert (await world.space.read_state())["sessions_to_retry"] == [stuck.id]


async def test_every_session_that_could_not_be_cleaned_stays_remembered_however_many(world, monkeypatch):
    from config.nekoclaw_settings import SPACE_TRANSCRIPT_KEPT_SECONDS

    monkeypatch.setattr("nekoclaw.host.space_keeper._CLEAN_BATCH", 2)
    commissions = []
    for number in range(5):
        commissions.append(await world.finished(f"old task number {number} words{number}", calls=0))
        world.clock.now += 10
    for commission in commissions:
        _fill_session(world.paths.session_dir(commission.id))
    world.clock.now += SPACE_TRANSCRIPT_KEPT_SECONDS + 60
    remove = world.keeper._clean_session
    world.keeper._clean_session = lambda directory, commission_id: "not_removed"

    for _round in range(4):
        await world.keeper.tidy(force=True)

    # More than a round can try, and none of them dropped.
    state = await world.space.read_state()
    assert sorted(state["sessions_to_retry"]) == sorted(commission.id for commission in commissions)
    assert state["sessions_cleaned_until"] == world.clock.now - SPACE_TRANSCRIPT_KEPT_SECONDS

    # The files let go: a batch a round, the ones that waited longest first.
    world.keeper._clean_session = remove
    for _round in range(3):
        await world.keeper.tidy(force=True)

    for commission in commissions:
        assert not (world.paths.session_dir(commission.id) / "transcript.jsonl").exists(), commission.title
    assert (await world.space.read_state())["sessions_to_retry"] == []


async def test_a_cleaning_that_is_cancelled_holds_the_session_until_the_removing_is_done(world):
    import threading

    from config.nekoclaw_settings import SPACE_TRANSCRIPT_KEPT_SECONDS

    commission = await world.finished(calls=0)
    _fill_session(world.paths.session_dir(commission.id))
    world.clock.now += SPACE_TRANSCRIPT_KEPT_SECONDS + 60
    removing = threading.Event()
    go_on = threading.Event()
    remove = world.keeper._clean_session
    released: list[str] = []

    def slow(directory: Path, commission_id: str) -> str:
        removing.set()
        go_on.wait(10)
        return remove(directory, commission_id)

    world.keeper._clean_session = slow
    world.keeper._hold_session = lambda commission_id: True
    world.keeper._release_session = released.append

    task = asyncio.ensure_future(world.keeper.tidy(force=True))
    await asyncio.to_thread(removing.wait, 10)
    # The host shuts down while the thread is at the files.
    task.cancel()
    await asyncio.sleep(0.2)

    # A thread cannot be stopped: the hold stays, and so does the wait, until it is done.
    assert released == [] and not task.done()
    assert (world.paths.session_dir(commission.id) / "outputs").exists()

    go_on.set()
    (outcome,) = await asyncio.gather(task, return_exceptions=True)
    # Cancelled, as it was told to be, once the thread was done.
    assert isinstance(outcome, asyncio.CancelledError)
    assert released == [commission.id]
    assert not (world.paths.session_dir(commission.id) / "transcript.jsonl").exists()


async def test_commissions_that_ended_at_one_moment_are_all_reached_across_batches(world, monkeypatch):
    from config.nekoclaw_settings import SPACE_TRANSCRIPT_KEPT_SECONDS

    monkeypatch.setattr("nekoclaw.host.space_keeper._CLEAN_BATCH", 2)
    # Stopped together: the same time on every one of them.
    commissions = [await world.finished(f"old task number {number} words{number}", calls=0) for number in range(5)]
    assert len({commission.updated_at for commission in commissions}) == 1
    later = await world.finished("ended a little later", calls=0)
    for commission in (*commissions, later):
        _fill_session(world.paths.session_dir(commission.id))
    world.clock.now += SPACE_TRANSCRIPT_KEPT_SECONDS + 60
    looked_at: list[str] = []
    remove = world.keeper._clean_session

    def removing(directory: Path, commission_id: str) -> str:
        looked_at.append(commission_id)
        return remove(directory, commission_id)

    world.keeper._clean_session = removing

    for _round in range(4):
        await world.keeper.tidy(force=True)

    for commission in (*commissions, later):
        assert not (world.paths.session_dir(commission.id) / "transcript.jsonl").exists(), commission.title
    # Each once: a round goes on right after the last one of the round before.
    assert sorted(looked_at) == sorted(commission.id for commission in (*commissions, later))
    state = await world.space.read_state()
    assert state["sessions_cleaned_until"] == world.clock.now - SPACE_TRANSCRIPT_KEPT_SECONDS


async def test_a_session_of_exactly_the_time_the_scan_reached_is_looked_at_once(world):
    from config.nekoclaw_settings import SPACE_TRANSCRIPT_KEPT_SECONDS

    commission = await world.finished(calls=0)
    _fill_session(world.paths.session_dir(commission.id))
    # Old enough to the second: the scan ends exactly at it.
    world.clock.now = commission.updated_at + SPACE_TRANSCRIPT_KEPT_SECONDS
    looked_at: list[str] = []
    remove = world.keeper._clean_session

    def removing(directory: Path, commission_id: str) -> str:
        looked_at.append(commission_id)
        return remove(directory, commission_id)

    world.keeper._clean_session = removing

    await world.keeper.tidy(force=True)
    assert looked_at == [commission.id]
    assert not (world.paths.session_dir(commission.id) / "transcript.jsonl").exists()

    world.clock.now += 3600
    await world.keeper.tidy(force=True)
    await world.keeper.tidy(force=True)

    assert looked_at == [commission.id]


async def test_closing_waits_a_bounded_time_for_a_thread_that_removes_files(world, monkeypatch):
    import threading

    from config.nekoclaw_settings import SPACE_TRANSCRIPT_KEPT_SECONDS

    monkeypatch.setattr("config.nekoclaw_settings.SPACE_KEEPER_CLOSE_WAIT_SECONDS", 0.2)
    commission = await world.finished(calls=0)
    _fill_session(world.paths.session_dir(commission.id))
    world.clock.now += SPACE_TRANSCRIPT_KEPT_SECONDS + 60
    removing = threading.Event()
    go_on = threading.Event()
    remove = world.keeper._clean_session
    released: list[str] = []

    def stuck(directory: Path, commission_id: str) -> str:
        # A storage that does not answer.
        removing.set()
        go_on.wait(20)
        return remove(directory, commission_id)

    world.keeper._clean_session = stuck
    world.keeper._hold_session = lambda commission_id: True
    world.keeper._release_session = released.append
    world.keeper._spawn(world.keeper.tidy(force=True))
    await asyncio.to_thread(removing.wait, 10)

    # Shutting down goes on: the outbox and the ledger are closed after this.
    await asyncio.wait_for(world.keeper.aclose(), timeout=3)

    # The thread is still at the files, so the session stays claimed.
    assert released == [] and len(world.keeper._lingering) == 1

    go_on.set()
    for _attempt in range(200):
        if not world.keeper._lingering:
            break
        await asyncio.sleep(0.02)
    assert released == [commission.id] and not world.keeper._lingering
    assert not (world.paths.session_dir(commission.id) / "transcript.jsonl").exists()


async def test_a_remembered_session_that_was_taken_up_again_is_let_go(world):
    from config.nekoclaw_settings import SPACE_TRANSCRIPT_KEPT_SECONDS

    stuck = await world.finished("failed long ago", calls=0, status="failed")
    _fill_session(world.paths.session_dir(stuck.id))
    world.clock.now += SPACE_TRANSCRIPT_KEPT_SECONDS + 60
    remove = world.keeper._clean_session
    world.keeper._clean_session = lambda directory, commission_id: "not_removed"
    await world.keeper.tidy(force=True)
    assert (await world.space.read_state())["sessions_to_retry"] == [stuck.id]

    # Taken up again and done: its transcript is in use, and young again.
    await world.ledger.transition(stuck.id, "working", reason="taken up again")
    await world.end(stuck.id, "done")
    world.keeper._clean_session = remove
    world.clock.now += 3600
    await world.keeper.tidy(force=True)

    assert (world.paths.session_dir(stuck.id) / "transcript.jsonl").is_file()
    assert (await world.space.read_state())["sessions_to_retry"] == []


def test_removing_says_when_something_is_still_there(tmp_path, monkeypatch):
    import shutil

    directory = tmp_path / "sessions" / "c1"
    _fill_session(directory)

    def refuse(path: Any, *args: Any, **kwargs: Any) -> None:
        raise PermissionError(13, "in use")

    monkeypatch.setattr(shutil, "rmtree", refuse)

    assert SpaceKeeper._clean_session(directory, "c1") == "not_removed"
    assert (directory / "outputs" / "1.txt").is_file() and not (directory / "transcript.jsonl").exists()
    monkeypatch.undo()
    assert SpaceKeeper._clean_session(directory, "c1") == "removed"
    assert SpaceKeeper._clean_session(directory, "c1") == "nothing"
    assert SpaceKeeper._clean_session(tmp_path / "elsewhere" / "c1", "c1") == "nothing"


async def test_a_directory_that_is_not_a_session_is_left_alone(world, tmp_path):
    from config.nekoclaw_settings import SPACE_TRANSCRIPT_KEPT_SECONDS

    elsewhere = tmp_path / "users-project"
    _fill_session(elsewhere)
    commission = await world.finished(calls=0)
    world.keeper._session_dir_of = lambda _commission: elsewhere
    world.clock.now += SPACE_TRANSCRIPT_KEPT_SECONDS + 60

    await world.keeper.tidy(force=True)

    assert (elsewhere / "transcript.jsonl").is_file() and (elsewhere / "outputs" / "1.txt").is_file()
    assert "session_cleaned" not in [event.type for event in await world.ledger.events(commission.id)]


async def test_a_step_that_fails_does_not_stop_the_others(world, monkeypatch):
    from config.nekoclaw_settings import SPACE_TRANSCRIPT_KEPT_SECONDS

    commission = await world.finished(calls=0)
    _fill_session(world.paths.session_dir(commission.id))
    world.clock.now += SPACE_TRANSCRIPT_KEPT_SECONDS + 60

    async def broken(*_args: Any) -> None:
        raise RuntimeError("archiving broke")

    monkeypatch.setattr(world.keeper, "_archive_idle", broken)

    assert await world.keeper.tidy(force=True) is True
    assert not (world.paths.session_dir(commission.id) / "transcript.jsonl").exists()


async def test_closing_stops_what_is_under_way(world):
    world.llm.gate = asyncio.Event()
    commission = await world.file()
    await world.work(commission.id)
    await world.ledger.transition(commission.id, "done", reason="test")
    while not world.llm.calls:
        await asyncio.sleep(0.01)

    await world.keeper.aclose()

    assert world.keeper._tasks == set()
    # Nothing is started after that either.
    other = await world.file("another task entirely")
    await world.work(other.id)
    await world.ledger.transition(other.id, "done", reason="test")
    assert world.keeper._tasks == set()


# ---------------------------------------------------------------------------
# The ninth review round
# ---------------------------------------------------------------------------


async def test_a_project_that_cannot_be_moved_is_asked_about_a_few_times_and_does_not_hold_up_the_others(world, monkeypatch):
    import nekoclaw.host.space as module
    from config.nekoclaw_settings import (
        SPACE_ARCHIVE_MAX_ATTEMPTS,
        SPACE_ARCHIVES_PER_UPKEEP,
        SPACE_PROJECT_IDLE_SECONDS,
    )

    stuck = [f"Alpha {number}" for number in range(SPACE_ARCHIVES_PER_UPKEEP)]
    for name in (*stuck, "Gamma"):
        await world.space.touch_project(name)
    world.llm.answers[TIER_ARCHIVE] = [
        {
            "summary": "A project.",
            "line": "a project",
            "entries": [{"title": "CSV on Windows", "type": "lesson", "content": "Write a BOM."}],
        }
    ]
    world.clock.now += SPACE_PROJECT_IDLE_SECONDS + 60
    move = module._move

    def held_open(source, target):
        # On Windows: a file in it is open in another program.
        if source.name in stuck:
            raise PermissionError(13, "a file in it is held open")
        return move(source, target)

    monkeypatch.setattr(module, "_move", held_open)

    async def active() -> list[str]:
        return [item.name for item in await world.space.list_projects() if item.status == "active"]

    # While they are still asked about, the stuck ones use up the round's summaries.
    for attempt in range(1, SPACE_ARCHIVE_MAX_ATTEMPTS + 1):
        await world.keeper.tidy(force=True)
        assert world.llm.count(TIER_ARCHIVE) == attempt * SPACE_ARCHIVES_PER_UPKEEP
        assert await active() == [*stuck, "Gamma"]
        assert [(await world.space.get_project(name)).archive_attempts for name in stuck] == [attempt] * len(stuck)
        # What a summary says holds everywhere is kept once its project is archived, not before.
        assert await world.space.list_entries() == []

    # Then they are not asked about any more, and the next one has its turn.
    await world.keeper.tidy(force=True)

    asked = SPACE_ARCHIVE_MAX_ATTEMPTS * SPACE_ARCHIVES_PER_UPKEEP + 1
    assert world.llm.count(TIER_ARCHIVE) == asked
    assert await active() == stuck
    assert "Project: Gamma" in world.llm.last(TIER_ARCHIVE)[1]["content"]
    assert [entry.id for entry in await world.space.list_entries()] == ["csv-on-windows"]

    # And no round after that asks a model about them again.
    await world.keeper.tidy(force=True)
    assert world.llm.count(TIER_ARCHIVE) == asked

    # Once the file is let go, they are archived, with the summary written the last time.
    monkeypatch.setattr(module, "_move", move)
    await world.keeper.tidy(force=True)

    assert world.llm.count(TIER_ARCHIVE) == asked
    assert await active() == []
    (first, *_rest) = [item for item in await world.space.list_projects() if item.name == stuck[0]]
    home = world.root / "archive" / first.archive_year / stuck[0]
    assert (home / "SUMMARY.md").read_text(encoding="utf-8") == "A project.\n"


async def test_a_summary_that_cannot_be_saved_does_not_end_the_round(world, monkeypatch):
    from config.nekoclaw_settings import SPACE_PROJECT_IDLE_SECONDS

    await world.space.touch_project("Alpha")
    await world.space.touch_project("Beta")
    world.clock.now += SPACE_PROJECT_IDLE_SECONDS + 60
    put = TaskAgentSpace._put_text

    def no_summary_for_alpha(path, text):
        if path.name == "SUMMARY.md" and path.parent.name == "Alpha":
            raise PermissionError(13, "in use")
        return put(path, text)

    monkeypatch.setattr(TaskAgentSpace, "_put_text", staticmethod(no_summary_for_alpha))

    await world.keeper.tidy(force=True)

    assert [(item.name, item.status) for item in await world.space.list_projects()] == [
        ("Alpha", "active"),
        ("Beta", "archived"),
    ]
    assert (await world.space.get_project("Alpha")).archive_attempts == 1


async def test_archiving_that_fails_outright_does_not_end_the_round(world, monkeypatch):
    from config.nekoclaw_settings import SPACE_PROJECT_IDLE_SECONDS

    await world.space.touch_project("Alpha")
    await world.space.touch_project("Beta")
    world.clock.now += SPACE_PROJECT_IDLE_SECONDS + 60
    archive = TaskAgentSpace._archive

    def broken_for_alpha(self, name, *args, **kwargs):
        if name == "Alpha":
            raise OSError(5, "the disk said no")
        return archive(self, name, *args, **kwargs)

    monkeypatch.setattr(TaskAgentSpace, "_archive", broken_for_alpha)

    await world.keeper.tidy(force=True)

    assert [(item.name, item.status) for item in await world.space.list_projects()] == [
        ("Alpha", "active"),
        ("Beta", "archived"),
    ]


async def test_without_a_model_projects_that_cannot_be_moved_do_not_use_up_the_round(tmp_path, monkeypatch):
    import nekoclaw.host.space as module
    from config.nekoclaw_settings import SPACE_ARCHIVES_PER_UPKEEP, SPACE_PROJECT_IDLE_SECONDS

    world = World(tmp_path, None)
    try:
        stuck = [f"Alpha {number}" for number in range(SPACE_ARCHIVES_PER_UPKEEP)]
        for name in (*stuck, "Gamma"):
            await world.space.touch_project(name)
        world.clock.now += SPACE_PROJECT_IDLE_SECONDS + 60
        move = module._move

        def held_open(source, target):
            if source.name in stuck:
                raise PermissionError(13, "a file in it is held open")
            return move(source, target)

        monkeypatch.setattr(module, "_move", held_open)

        await world.keeper.tidy(force=True)

        assert [item.name for item in await world.space.list_projects() if item.status == "archived"] == ["Gamma"]
        # Nothing was asked of a model, so there is nothing to count.
        assert [(await world.space.get_project(name)).archive_attempts for name in stuck] == [0] * len(stuck)
    finally:
        await world.keeper.aclose()
        await world.ledger.close()


async def test_a_summary_is_written_from_the_newest_reviews_read_in_one_pass(world, monkeypatch):
    from config.nekoclaw_settings import SPACE_PROJECT_IDLE_SECONDS
    from nekoclaw.host.space_keeper import _ARCHIVE_REVIEWS

    await world.space.touch_project("Budget app")
    for number in range(_ARCHIVE_REVIEWS + 3):
        commission = await world.file(f"task number {number:02d}")
        world.clock.now += 60
        await world.space.write_review("Budget app", replace_ended(commission, world.clock.now), f"what was done in {number:02d}")
    for number in range(5):
        await world.space.touch_project(f"Other {number}")
    world.clock.now += SPACE_PROJECT_IDLE_SECONDS + 60
    for number in range(5):
        await world.space.touch_project(f"Other {number}")
    listed: list[int] = []
    directories = TaskAgentSpace._review_directories

    def counted(self):
        listed.append(1)
        return directories(self)

    monkeypatch.setattr(TaskAgentSpace, "_review_directories", counted)

    await world.keeper.tidy(force=True)

    user = world.llm.last(TIER_ARCHIVE)[1]["content"]
    for number in range(3):
        assert f"task number {number:02d}" not in user
    for number in range(3, _ARCHIVE_REVIEWS + 3):
        assert f"- task number {number:02d} (" in user and f"what was done in {number:02d}" in user
    # The projects were gone through once for it, not once per review.
    assert len(listed) == 1


def replace_ended(commission: Any, ended_at: float) -> Any:
    from dataclasses import replace

    return replace(commission, ended_at=ended_at)


async def test_without_a_model_too_at_most_a_few_projects_are_archived_per_round(tmp_path):
    from config.nekoclaw_settings import SPACE_ARCHIVES_PER_UPKEEP, SPACE_PROJECT_IDLE_SECONDS

    world = World(tmp_path, None)
    try:
        for number in range(SPACE_ARCHIVES_PER_UPKEEP + 2):
            await world.space.touch_project(f"Project {number}")
        world.clock.now += SPACE_PROJECT_IDLE_SECONDS + 60

        await world.keeper.tidy(force=True)

        statuses = [item.status for item in await world.space.list_projects()]
        assert statuses.count("archived") == SPACE_ARCHIVES_PER_UPKEEP
    finally:
        await world.keeper.aclose()
        await world.ledger.close()
