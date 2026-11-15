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

"""The task agent space as files: entries, indexes, rules, projects, reviews."""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from nekoclaw.host.paths import DEFAULT_PROJECT, SpacePaths
from nekoclaw.host.space import SpaceError, TaskAgentSpace, slug


class Clock:
    def __init__(self, now: float = 1_800_000_000.0) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now


def _commission(**values: Any) -> Any:
    base = {
        "id": "c1",
        "project": None,
        "title": "Sort photos",
        "dispatch_text": "sort the photos",
        "user_text": "",
        "status": "done",
        "started_at": 1_800_000_000.0,
        "ended_at": 1_800_000_100.0,
        "workdir": None,
    }
    base.update(values)
    return SimpleNamespace(**base)


@pytest.fixture
def clock() -> Clock:
    return Clock()


@pytest.fixture
def root(tmp_path: Path) -> Path:
    return tmp_path / "nekoclaw" / "task_agent_space"


@pytest.fixture
def space(root: Path, clock: Clock) -> TaskAgentSpace:
    return TaskAgentSpace(lambda: SpacePaths(root), clock=clock)


def _lesson(title: str, content: str, category: str = "lesson", scope: str = "project") -> dict[str, Any]:
    return {"title": title, "content": content, "category": category, "scope": scope}


# ---------------------------------------------------------------------------
# Entries and the index
# ---------------------------------------------------------------------------


async def test_a_lesson_becomes_a_file_and_a_line_of_the_index(space, root):
    message = await space.submit(_commission(), _lesson("Photo dates", "Use the EXIF date, not the file date."))

    entry = root / "projects" / DEFAULT_PROJECT / "memory" / "photo-dates.md"
    text = entry.read_text(encoding="utf-8")
    assert text.startswith("---\nname: Photo dates\n")
    assert "type: lesson" in text and "sources: c1" in text
    assert text.rstrip().endswith("Use the EXIF date, not the file date.")
    index = (root / "projects" / DEFAULT_PROJECT / "MEMORY.md").read_text(encoding="utf-8")
    assert index == "- [Photo dates](memory/photo-dates.md) — Use the EXIF date, not the file date.\n"
    assert message == "kept in project _daily as photo-dates.md"
    # Nothing for every project was written.
    assert not (root / "MEMORY.md").exists()


async def test_scope_global_goes_to_the_space_itself(space, root):
    message = await space.submit(
        _commission(project="blog"), _lesson("Use uv", "Run Python through uv.", scope="global")
    )

    assert (root / "memory" / "use-uv.md").is_file()
    assert "(memory/use-uv.md)" in (root / "MEMORY.md").read_text(encoding="utf-8")
    assert not (root / "projects").exists()
    assert message == "kept in the space of every project as use-uv.md"


@pytest.mark.parametrize("category", ["lesson", "reflection", "reference", "progress"])
async def test_the_four_kinds_are_kept_as_they_are(space, category):
    await space.submit(_commission(), _lesson("A title", "Some text.", category=category))

    (entry,) = await space.list_entries(DEFAULT_PROJECT)
    assert entry.type == category


async def test_the_same_topic_is_added_to_and_nothing_is_overwritten(space, root, clock):
    await space.submit(_commission(), _lesson("Photo dates", "Use the EXIF date."))
    entry = root / "projects" / DEFAULT_PROJECT / "memory" / "photo-dates.md"
    # The user rewrites the entry and its line in the index by hand.
    edited = entry.read_text(encoding="utf-8").replace("Use the EXIF date.", "Use the EXIF date. (checked by me)")
    entry.write_text(edited, encoding="utf-8")
    index = root / "projects" / DEFAULT_PROJECT / "MEMORY.md"
    index.write_text("# My notes\n\n- [Photo dates](memory/photo-dates.md) — my own wording\n", encoding="utf-8")

    clock.now += 86400 * 3
    message = await space.submit(
        _commission(id="c2"), _lesson("photo  DATES", "Videos have no EXIF date; use the creation time.")
    )

    text = entry.read_text(encoding="utf-8")
    assert "Use the EXIF date. (checked by me)" in text
    assert "Videos have no EXIF date; use the creation time." in text
    assert text.index("(checked by me)") < text.index("## ") < text.index("Videos have no EXIF")
    assert "sources: c1, c2" in text
    assert message == "added to photo-dates.md in project _daily"
    # One file, and the index exactly as the user left it.
    assert [item.name for item in entry.parent.iterdir()] == ["photo-dates.md"]
    assert index.read_text(encoding="utf-8") == "# My notes\n\n- [Photo dates](memory/photo-dates.md) — my own wording\n"


async def test_a_similar_title_counts_as_the_same_topic_and_a_different_one_does_not(space):
    await space.submit(_commission(), _lesson("Renaming photos by date", "Use the EXIF date."))
    await space.submit(_commission(id="c2"), _lesson("Renaming the photos by date", "Keep the extension."))
    await space.submit(_commission(id="c3"), _lesson("Installing Python packages", "Use uv."))

    entries = await space.list_entries(DEFAULT_PROJECT)
    assert sorted(entry.title for entry in entries) == ["Installing Python packages", "Renaming photos by date"]
    renamed = await space.read_entry("renaming-photos-by-date", DEFAULT_PROJECT)
    assert "Use the EXIF date." in renamed.content and "Keep the extension." in renamed.content
    assert renamed.sources == ("c1", "c2")


async def test_the_same_text_twice_is_kept_once(space):
    await space.submit(_commission(), _lesson("Photo dates", "Use the EXIF date."))
    await space.submit(_commission(), _lesson("Photo dates", "Use the EXIF date."))

    entry = await space.read_entry("photo-dates", DEFAULT_PROJECT)
    assert entry.content == "Use the EXIF date."


async def test_new_lines_go_first_and_lines_of_the_user_stay(space, root):
    index = root / "projects" / DEFAULT_PROJECT / "MEMORY.md"
    index.parent.mkdir(parents=True)
    index.write_text("# Notes\n\nA paragraph of mine.\n\n- my own line, no link\n", encoding="utf-8")

    await space.submit(_commission(), _lesson("First", "One."))
    await space.submit(_commission(), _lesson("Second", "Two."))

    assert index.read_text(encoding="utf-8").split("\n") == [
        "# Notes",
        "",
        "A paragraph of mine.",
        "",
        "- [Second](memory/second.md) — Two.",
        "- [First](memory/first.md) — One.",
        "- my own line, no link",
        "",
    ]


async def test_an_entry_whose_head_the_user_broke_is_added_to_and_left_as_it_is(space, root):
    await space.submit(_commission(), _lesson("Photo dates", "Use the EXIF date."))
    entry = root / "projects" / DEFAULT_PROJECT / "memory" / "photo-dates.md"
    entry.write_text("Just my own notes, no head at all.\n", encoding="utf-8")

    await space.submit(_commission(), _lesson("photo-dates", "Videos differ."))

    text = entry.read_text(encoding="utf-8")
    assert text.startswith("Just my own notes, no head at all.\n")
    assert text.rstrip().endswith("Videos differ.")
    assert "---" not in text


async def test_many_lessons_at_once_all_end_up_in_the_index(space, root):
    commissions = [_commission(id=f"c{number}") for number in range(30)]

    await asyncio.gather(
        *(
            space.submit(commission, _lesson(f"Topic number {number} alpha{number}", f"Text {number}."))
            for number, commission in enumerate(commissions)
        )
    )

    lines = (root / "projects" / DEFAULT_PROJECT / "MEMORY.md").read_text(encoding="utf-8").strip().split("\n")
    assert len(lines) == 30
    assert len({line for line in lines}) == 30
    assert len(list((root / "projects" / DEFAULT_PROJECT / "memory").iterdir())) == 30


async def test_a_title_in_chinese_names_the_file_in_chinese(space, root):
    await space.submit(_commission(), _lesson("照片按日期整理", "用 EXIF 里的拍摄日期。"))

    assert (root / "projects" / DEFAULT_PROJECT / "memory" / "照片按日期整理.md").is_file()


@pytest.mark.parametrize(
    "title,expected",
    [
        ("Photo dates", "photo-dates"),
        ("  a/b\\c: d?  ", "a-b-c-d"),
        ("[link](x) #1 100%", "link-x-1-100"),
        ("x" * 200, "x" * 60),
    ],
)
def test_slug(title, expected):
    assert slug(title) == expected


@pytest.mark.parametrize("title", ["", "...", "CON", "nul", "///"])
def test_a_title_that_gives_no_file_name_is_refused(title):
    with pytest.raises(ValueError):
        slug(title)


async def test_a_title_that_gives_no_file_name_is_still_kept(space, root):
    await space.submit(_commission(), _lesson("CON", "A reserved name on Windows."))

    assert (root / "projects" / DEFAULT_PROJECT / "memory" / "entry.md").is_file()


async def test_a_lesson_without_text_is_not_kept(space, root):
    message = await space.submit(_commission(), _lesson("Title", "   "))

    assert message.startswith("not kept")
    assert not root.exists()


async def test_long_text_is_cut_by_tokens(space):
    from config.nekoclaw_settings import (
        SPACE_ENTRY_CONTENT_MAX_TOKENS,
        SPACE_ENTRY_DESCRIPTION_MAX_TOKENS,
        SPACE_ENTRY_TITLE_MAX_TOKENS,
    )
    from utils.tokenize import count_tokens

    await space.submit(_commission(), _lesson("title " * 100, "word " * 5000))

    (entry,) = await space.list_entries(DEFAULT_PROJECT)
    full = await space.read_entry(entry.id, DEFAULT_PROJECT)
    assert count_tokens(full.title) <= SPACE_ENTRY_TITLE_MAX_TOKENS
    assert count_tokens(full.description) <= SPACE_ENTRY_DESCRIPTION_MAX_TOKENS
    assert count_tokens(full.content) <= SPACE_ENTRY_CONTENT_MAX_TOKENS


async def test_what_the_user_saves_while_the_host_writes_is_not_written_over(space, root):
    await space.submit(_commission(), _lesson("First", "One."))
    index = root / "projects" / DEFAULT_PROJECT / "MEMORY.md"
    real = space._signature
    saved = {"done": False}

    def signature(path):
        # The second look at the index is the one right before it is replaced:
        # the user saves a line of their own just before that.
        if Path(path) == index and not saved["done"] and getattr(signature, "seen", False):
            saved["done"] = True
            index.write_text(index.read_text(encoding="utf-8") + "- a line the user just added\n", encoding="utf-8")
        if Path(path) == index:
            signature.seen = True
        return real(path)

    space._signature = signature

    await space.submit(_commission(), _lesson("Second", "Two."))

    lines = index.read_text(encoding="utf-8").strip().split("\n")
    assert saved["done"]
    assert "- a line the user just added" in lines
    assert "- [Second](memory/second.md) — Two." in lines and "- [First](memory/first.md) — One." in lines


async def test_a_file_in_use_is_tried_again(space, root, monkeypatch):
    import utils.file_utils as file_utils

    real = file_utils.atomic_write_text
    failures = {"left": 2}

    def flaky(path, content, **kwargs):
        if failures["left"] > 0:
            failures["left"] -= 1
            raise PermissionError(13, "in use")
        return real(path, content, **kwargs)

    monkeypatch.setattr(file_utils, "atomic_write_text", flaky)

    await space.submit(_commission(), _lesson("Photo dates", "Use the EXIF date."))

    assert failures["left"] == 0
    assert (root / "projects" / DEFAULT_PROJECT / "memory" / "photo-dates.md").is_file()


async def test_a_file_that_stays_in_use_fails_the_write(space, monkeypatch):
    import utils.file_utils as file_utils

    def busy(path, content, **kwargs):
        raise PermissionError(13, "in use")

    monkeypatch.setattr(file_utils, "atomic_write_text", busy)

    with pytest.raises(PermissionError):
        await space.file_entry(None, title="Photo dates", content="Use the EXIF date.")


async def test_a_write_that_did_not_land_is_noticed(space, monkeypatch):
    import utils.file_utils as file_utils

    def lossy(path, content, **kwargs):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        Path(path).write_text("something else", encoding="utf-8")

    monkeypatch.setattr(file_utils, "atomic_write_text", lossy)

    with pytest.raises(OSError):
        await space.file_entry(None, title="Photo dates", content="Use the EXIF date.")


# ---------------------------------------------------------------------------
# What the console uses
# ---------------------------------------------------------------------------


async def test_entries_can_be_listed_read_edited_and_removed(space, root):
    await space.submit(_commission(), _lesson("Photo dates", "Use the EXIF date."))
    await space.submit(_commission(), _lesson("Use uv", "Run Python through uv.", scope="global"))

    assert [entry.id for entry in await space.list_entries(DEFAULT_PROJECT)] == ["photo-dates"]
    assert [entry.id for entry in await space.list_entries()] == ["use-uv"]
    assert (await space.list_entries(DEFAULT_PROJECT))[0].content == ""
    assert (await space.read_entry("photo-dates", DEFAULT_PROJECT)).content == "Use the EXIF date."
    assert await space.read_entry("missing", DEFAULT_PROJECT) is None

    edited = await space.edit_entry(
        "photo-dates", DEFAULT_PROJECT, title="Dates of photos", description="which date to use", content="New text."
    )

    assert (edited.title, edited.description, edited.content) == ("Dates of photos", "which date to use", "New text.")
    index = root / "projects" / DEFAULT_PROJECT / "MEMORY.md"
    assert index.read_text(encoding="utf-8") == "- [Dates of photos](memory/photo-dates.md) — which date to use\n"
    assert await space.edit_entry("missing", DEFAULT_PROJECT, content="x") is None

    assert await space.delete_entry("photo-dates", DEFAULT_PROJECT) is True
    assert await space.delete_entry("photo-dates", DEFAULT_PROJECT) is False
    assert await space.list_entries(DEFAULT_PROJECT) == []
    assert index.read_text(encoding="utf-8").strip() == ""
    # The other index is untouched.
    assert "(memory/use-uv.md)" in await space.read_index()


@pytest.mark.parametrize("name", ["../outside", "a/b", "..", "CON"])
async def test_an_entry_id_cannot_leave_its_directory(space, name):
    with pytest.raises(ValueError):
        await space.read_entry(name, DEFAULT_PROJECT)
    with pytest.raises(ValueError):
        await space.delete_entry(name, DEFAULT_PROJECT)


async def test_rules_are_the_users_and_are_read_back(space, root):
    assert await space.read_rules() == ""

    await space.write_rules("Always answer in short sentences.\r\nNever delete files.")
    await space.write_rules("Use pnpm.", "blog")

    assert await space.read_rules() == "Always answer in short sentences.\nNever delete files."
    assert await space.read_rules("blog") == "Use pnpm."
    assert (root / "AGENTS.md").is_file() and (root / "projects" / "blog" / "AGENTS.md").is_file()


async def test_reviews_are_kept_per_commission_and_found_by_its_id(space, root):
    commission = _commission(id="abc123", dispatch_text="sort the photos in Downloads")

    review = await space.write_review("photos", commission, "It sorted 12 photos.", entries=("photos/photo-dates",))

    path = root / "projects" / "photos" / "reviews" / "abc123.md"
    text = path.read_text(encoding="utf-8")
    assert "commission: abc123" in text and "status: done" in text and "entries: photos/photo-dates" in text
    assert "**Commission**: sort the photos in Downloads" in text and "It sorted 12 photos." in text
    assert review.entries == ("photos/photo-dates",)
    found = await space.read_review("abc123")
    assert (found.project, found.title) == ("photos", "Sort photos")
    assert await space.read_review("other") is None
    assert [item.commission_id for item in await space.list_reviews()] == ["abc123"]
    assert [item.commission_id for item in await space.list_reviews("photos")] == ["abc123"]
    assert await space.list_reviews("blog") == []
    assert await space.delete_review("abc123") is True
    assert await space.delete_review("abc123") is False


# ---------------------------------------------------------------------------
# Projects
# ---------------------------------------------------------------------------


async def test_a_project_is_made_once_and_listed_in_the_index_of_the_space(space, root, clock):
    project = await space.touch_project("Budget app", description="a small app that tracks spending", workdir="D:/w")
    clock.now += 100
    again = await space.touch_project("Budget app", description="ignored", workdir="D:/other")

    assert (project.name, project.description, project.workdir) == ("Budget app", "a small app that tracks spending", "D:/w")
    assert (again.description, again.workdir, again.commissions) == ("a small app that tracks spending", "D:/w", 2)
    assert again.last_commission_at == clock.now and again.created_at == clock.now - 100
    index = (root / "MEMORY.md").read_text(encoding="utf-8")
    assert index == (
        "- [project: Budget app](projects/Budget app/MEMORY.md) — a small app that tracks spending; files in D:/w\n"
    )
    assert [item.name for item in await space.list_projects()] == ["Budget app"]


async def test_the_daily_project_gets_no_line_in_the_index(space, root):
    await space.touch_project(DEFAULT_PROJECT)

    assert not (root / "MEMORY.md").exists()


@pytest.mark.parametrize("name", ["../x", "a/b", "", "CON", "x" * 200])
async def test_a_project_name_must_be_a_directory_name(space, name):
    with pytest.raises(ValueError):
        await space.touch_project(name)


async def test_a_commission_is_filed_under_the_project_its_words_name(space):
    await space.touch_project("Budget app")
    await space.touch_project("Budget app for kids")

    by_dispatch = await space.assign_project(_commission(dispatch_text="add an export to the budget APP"))
    by_user = await space.assign_project(_commission(dispatch_text="add export", user_text="for the Budget  App please"))
    longest = await space.assign_project(_commission(dispatch_text="fix the budget app for kids"))
    none = await space.assign_project(_commission(dispatch_text="what is the weather"))

    assert (by_dispatch.name, by_user.name, longest.name, none) == ("Budget app", "Budget app", "Budget app for kids", None)
    assert by_user.commissions == 3


@pytest.mark.parametrize(
    "name,text,expected",
    [
        ("app", "what happened to my application", False),
        ("app", "open the app now", True),
        ("app", "App: fix the login", True),
        ("Budget app", "the budget   APP's export is broken", True),
        ("Budget app", "use the budget apps list", False),
        ("blog", "weblog", False),
        ("v2", "ship v2.", True),
        ("v2", "ship v20", False),
        ("记账小程序", "给记账小程序加一个导出", True),
        ("记账", "我的记账小程序", True),
        ("блог", "мой блог готов", True),
        ("блог", "блогер", False),
        ("", "anything", False),
    ],
)
def test_a_project_name_occurs_as_words_of_its_own(name, text, expected):
    from nekoclaw.host.space import name_occurs

    assert name_occurs(name, text) is expected


async def test_a_name_inside_another_word_takes_no_commission(space):
    await space.touch_project("app")

    assert await space.assign_project(_commission(dispatch_text="what happened to my application")) is None
    assert (await space.assign_project(_commission(dispatch_text="open the app please"))).name == "app"


async def test_a_short_name_takes_no_commission(space):
    await space.touch_project("ab")
    await space.touch_project("天气")

    assert await space.assign_project(_commission(dispatch_text="ab and the 天气 today")) is None


async def test_the_daily_project_is_never_matched(space):
    await space.touch_project(DEFAULT_PROJECT)

    assert await space.assign_project(_commission(dispatch_text="clean up _daily please")) is None


async def test_an_idle_project_is_archived_and_comes_back_when_it_is_named(space, root, clock):
    await space.touch_project("Budget app", description="tracks spending", workdir="D:/w")
    await space.submit(_commission(project="Budget app"), _lesson("Database", "It uses SQLite."))
    clock.now += 40 * 86400

    archived = await space.archive_project("Budget app", summary="An app that tracks spending.", description="spending tracker, paused")

    year = archived.archive_year
    home = root / "archive" / year / "Budget app"
    assert archived.status == "archived" and archived.archived_at == clock.now
    assert not (root / "projects" / "Budget app").exists()
    assert (home / "SUMMARY.md").read_text(encoding="utf-8") == "An app that tracks spending.\n"
    assert (home / "memory" / "database.md").is_file()
    assert (root / "MEMORY.md").read_text(encoding="utf-8") == (
        f"- [archived project: Budget app](archive/{year}/Budget app/SUMMARY.md) — spending tracker, paused\n"
    )
    assert [(item.name, item.status) for item in await space.list_projects()] == [("Budget app", "archived")]
    assert await space.list_projects(include_archived=False) == []
    assert [entry.id for entry in await space.list_entries("Budget app", archive_year=year)] == ["database"]

    clock.now += 86400
    back = await space.assign_project(_commission(dispatch_text="go on with the budget app"))

    assert (back.name, back.status, back.archived_at, back.workdir) == ("Budget app", "active", None, "D:/w")
    assert back.last_commission_at == clock.now
    assert (root / "projects" / "Budget app" / "memory" / "database.md").is_file()
    assert not home.exists()
    assert (root / "MEMORY.md").read_text(encoding="utf-8") == (
        "- [project: Budget app](projects/Budget app/MEMORY.md) — tracks spending; files in D:/w\n"
    )


async def test_archiving_does_not_write_over_an_archived_project_of_the_same_name(space, root, clock):
    await space.touch_project("Budget app")
    first = await space.archive_project("Budget app", summary="first")
    await space.touch_project("Budget app")
    second = await space.archive_project("Budget app", summary="second")

    year = first.archive_year
    assert (root / "archive" / year / "Budget app" / "SUMMARY.md").read_text(encoding="utf-8") == "first\n"
    assert (root / "archive" / year / "Budget app-2" / "SUMMARY.md").read_text(encoding="utf-8") == "second\n"
    assert second.name == "Budget app"


async def test_the_daily_project_is_never_archived(space):
    await space.touch_project(DEFAULT_PROJECT)

    assert await space.archive_project(DEFAULT_PROJECT) is None


async def test_a_project_that_cannot_be_moved_stays(space, root, monkeypatch):
    import nekoclaw.host.space as module

    await space.touch_project("Budget app")

    def stuck(source, target):
        raise PermissionError(13, "in use")

    monkeypatch.setattr(module, "_move", stuck)

    assert await space.archive_project("Budget app", summary="s") is None
    assert (root / "projects" / "Budget app" / "project.json").is_file()
    assert [item.status for item in await space.list_projects()] == ["active"]


async def test_an_archived_project_does_not_replace_one_of_its_name_that_is_in_use(space, root):
    await space.touch_project("Budget app", description="the old one")
    archived = await space.archive_project("Budget app", summary="old summary")
    # A project of the same name was started since, and has an entry of its own.
    await space.touch_project("Budget app", description="the new one")
    await space.file_entry("Budget app", title="Stack", content="Flask and SQLite.")

    project = await space.assign_project(_commission(dispatch_text="go on with the budget app"))

    assert (project.name, project.description, project.status) == ("Budget app", "the new one", "active")
    assert (root / "projects" / "Budget app" / "memory" / "stack.md").is_file()
    # The archived one stays in the archive, untouched.
    home = root / "archive" / archived.archive_year / "Budget app"
    assert (home / "SUMMARY.md").read_text(encoding="utf-8") == "old summary\n"
    assert not (root / "projects" / "Budget app" / "SUMMARY.md").exists()
    # Matching prefers the one in use, so restoring is not even tried above.
    # Were it tried (two commissions racing), it gives way to the one in use
    # rather than move a directory onto it.
    stored = [item for item in await space.list_projects() if item.status == "archived"]
    kept = await asyncio.to_thread(space._restore, stored[0])
    assert (kept.description, kept.status) == ("the new one", "active")
    assert (home / "SUMMARY.md").is_file()
    assert (root / "projects" / "Budget app" / "memory" / "stack.md").is_file()


async def test_open_project_brings_an_archived_one_back_instead_of_making_a_second(space, root):
    await space.touch_project("Budget app", description="tracks spending")
    await space.archive_project("Budget app", summary="s")

    project = await space.open_project("budget APP", description="other words", workdir="D:/new")

    assert (project.name, project.description, project.commissions) == ("Budget app", "tracks spending", 2)
    assert [item.name for item in (root / "projects").iterdir()] == ["Budget app"]


# ---------------------------------------------------------------------------
# What a task agent is handed
# ---------------------------------------------------------------------------


async def test_an_empty_space_adds_nothing(space, root):
    assert await space.brief(None) == ""
    assert await space.brief("blog") == ""
    # Asking does not make the directory either.
    assert not root.exists()


async def test_the_brief_has_the_four_parts_in_order(space, root):
    await space.write_rules("Never delete files.")
    await space.write_rules("Use pnpm.", "blog")
    await space.submit(_commission(), _lesson("Use uv", "Run Python through uv.", scope="global"))
    await space.submit(_commission(project="blog"), _lesson("Deploy", "Deploy with the script."))

    brief = await space.brief("blog")

    assert str(root) in brief and "projects/blog" in brief and "note_lesson" in brief
    # What was kept is told apart from what the task agent is asked to do.
    assert "not instructions" in brief
    order = [
        "Rules for every commission:\nNever delete files.",
        "Index, all projects:\n- [Use uv](memory/use-uv.md)",
        "Rules of project blog:\nUse pnpm.",
        "Index of project blog:\n- [Deploy](memory/deploy.md)",
    ]
    positions = [brief.index(part) for part in order]
    assert positions == sorted(positions)
    # A commission of no project gets the daily project's parts, which are empty here.
    daily = await space.brief(None)
    assert "Rules of project" not in daily and "Index of project" not in daily
    assert "Rules for every commission:" in daily


async def test_every_part_of_the_brief_has_its_cap_and_so_has_the_whole(space, root):
    from config.nekoclaw_settings import (
        SPACE_BRIEF_GLOBAL_INDEX_MAX_TOKENS,
        SPACE_BRIEF_GLOBAL_RULES_MAX_TOKENS,
        SPACE_BRIEF_MAX_TOKENS,
        SPACE_BRIEF_PROJECT_INDEX_MAX_TOKENS,
        SPACE_BRIEF_PROJECT_RULES_MAX_TOKENS,
    )
    from config.prompts import prompts_nekoclaw_space as texts
    from utils.tokenize import count_tokens

    project = "a project with quite a long name to be safe"
    await space.write_rules("global rule. " * 2000)
    await space.write_rules("project rule. " * 2000, project)
    (root / "MEMORY.md").write_text("- [global entry](memory/x.md) — words\n" * 2000, encoding="utf-8")
    (root / "projects" / project / "MEMORY.md").write_text("- [project entry](memory/y.md) — words\n" * 2000, encoding="utf-8")

    brief = await space.brief(project)

    assert count_tokens(brief) <= SPACE_BRIEF_MAX_TOKENS
    caps = [
        ("Rules for every commission:", SPACE_BRIEF_GLOBAL_RULES_MAX_TOKENS),
        ("Index, all projects:", SPACE_BRIEF_GLOBAL_INDEX_MAX_TOKENS),
        (f"Rules of project {project}:", SPACE_BRIEF_PROJECT_RULES_MAX_TOKENS),
        (f"Index of project {project}:", SPACE_BRIEF_PROJECT_INDEX_MAX_TOKENS),
    ]
    sections = brief.split("\n\n")[1:]
    assert len(sections) == 4
    for section, (header, cap) in zip(sections, caps, strict=True):
        assert section.startswith(header + "\n")
        body = section[len(header) + 1 :]
        assert body.endswith("\n" + texts.SPACE_BRIEF_CUT_NOTE)
        kept = body[: -len(texts.SPACE_BRIEF_CUT_NOTE) - 1]
        assert count_tokens(kept) <= cap
        if header.startswith("Index"):
            # An index is cut between lines.
            assert all(line.endswith("— words") for line in kept.split("\n"))
    # What the owner was told: at most 1400 tokens, headings and the lines
    # that say where the files are included. The four parts leave room for those.
    assert SPACE_BRIEF_MAX_TOKENS == 1400
    assert sum(cap for _header, cap in caps) == 1200


async def test_a_long_project_name_cannot_push_the_whole_over_its_cap(space):
    from config.nekoclaw_settings import SPACE_BRIEF_MAX_TOKENS
    from utils.tokenize import count_tokens

    # The name is repeated in two headings and in the line that says where the files are.
    project = "很长的项目名字" * 8
    assert count_tokens(project) >= 40
    await space.write_rules("global rule. " * 2000)
    await space.write_rules("project rule. " * 2000, project)
    for number in range(150):
        await space.file_entry(None, title=f"Global {number} g{number}", content="text " * 30)
        await space.file_entry(project, title=f"Project {number} p{number}", content="text " * 30)

    brief = await space.brief(project)

    assert f"Index of project {project}:" in brief
    assert count_tokens(brief) <= SPACE_BRIEF_MAX_TOKENS


async def test_the_newest_lines_of_an_index_survive_the_cap(space):
    for number in range(120):
        await space.file_entry(None, title=f"Topic {number} zeta{number}", content=f"Text about topic {number}.")

    brief = await space.brief(None)

    assert "Topic 119 zeta119" in brief and "Topic 0 zeta0" not in brief


async def test_contains_tells_what_is_inside_the_space(space, root, tmp_path):
    await space.submit(_commission(), _lesson("Photo dates", "Use the EXIF date."))

    assert await space.contains(str(root / "projects" / DEFAULT_PROJECT / "memory" / "photo-dates.md"))
    assert await space.contains(str(root))
    assert await space.contains(str(root / "memory" / "not-there-yet.md"))
    assert not await space.contains(str(root.parent / "task_agents" / "projects" / "_daily" / "sessions" / "c1"))
    assert not await space.contains(str(root / ".." / "ledger.db"))
    assert not await space.contains(str(tmp_path))


async def test_state_is_remembered(space):
    assert await space.read_state() == {}

    await space.write_state(upkeep_at=5.0)
    await space.write_state(since=3.0)

    assert await space.read_state() == {"upkeep_at": 5.0, "since": 3.0, "version": 1}


async def test_entry_errors_are_space_errors(space):
    with pytest.raises(SpaceError):
        await space.file_entry(None, title="", content="text")
