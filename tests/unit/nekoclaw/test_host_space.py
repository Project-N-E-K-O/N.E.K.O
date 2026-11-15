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


async def test_an_index_that_cannot_be_read_is_not_written_over(space, root):
    index = root / "projects" / DEFAULT_PROJECT / "MEMORY.md"
    index.parent.mkdir(parents=True)
    # Saved by some editor in an encoding this does not read.
    unreadable = "- 我自己的笔记\n".encode("utf-16")
    index.write_bytes(unreadable)

    message = await space.submit(_commission(), _lesson("Photo dates", "Use the EXIF date."))

    assert message == "not kept: the files of the task agent space could not be written"
    assert index.read_bytes() == unreadable


async def test_an_entry_that_cannot_be_read_is_not_written_over(space, root):
    await space.submit(_commission(), _lesson("Photo dates", "Use the EXIF date."))
    entry = root / "projects" / DEFAULT_PROJECT / "memory" / "photo-dates.md"
    unreadable = b"\xff\xfe\xff not text at all \x80\x81"
    entry.write_bytes(unreadable)

    # It cannot be told what it is about, so what comes in gets a file of its own.
    made, created = await space.file_entry(DEFAULT_PROJECT, title="photo-dates", content="Videos differ.")

    assert entry.read_bytes() == unreadable
    assert (created, made.id) == (True, "photo-dates-2")
    assert (entry.parent / "photo-dates-2.md").is_file()


def test_changing_a_file_that_cannot_be_read_is_refused(space, root):
    path = root / "MEMORY.md"
    path.parent.mkdir(parents=True)
    unreadable = b"\xff\xfe\xff not text at all \x80\x81"
    path.write_bytes(unreadable)

    with pytest.raises(OSError):
        space._edit_text(path, lambda text: text + "- a new line\n")

    assert path.read_bytes() == unreadable
    # A file that is not there is started from nothing.
    fresh = root / "projects" / "blog" / "MEMORY.md"
    assert space._edit_text(fresh, lambda text: text + "- a new line\n") is True
    assert fresh.read_text(encoding="utf-8") == "- a new line\n"


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
        "- [project: Budget app](<projects/Budget app/MEMORY.md>) — a small app that tracks spending; files in D:/w\n"
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
        f"- [archived project: Budget app](<archive/{year}/Budget app/SUMMARY.md>) — spending tracker, paused\n"
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
        "- [project: Budget app](<projects/Budget app/MEMORY.md>) — tracks spending; files in D:/w\n"
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


async def test_a_second_archive_of_a_name_is_reached_by_its_directory(space, root):
    await space.touch_project("Budget app")
    await space.file_entry("Budget app", title="First", content="Of the first one.")
    await space.write_review("Budget app", _commission(id="early", ended_at=1_800_000_000.0), "Of the first one too.")
    first = await space.archive_project("Budget app", summary="first")
    await space.touch_project("Budget app")
    await space.file_entry("Budget app", title="Second", content="Of the second one.")
    await space.write_review("Budget app", _commission(id="late"), "Of the second one too.")
    second = await space.archive_project("Budget app", summary="second")

    year = first.archive_year
    assert (first.directory, second.directory) == ("Budget app", "Budget app-2")
    # Each review is found where it is, and listed once.
    assert (await space.read_review("early")).content.endswith("Of the first one too.")
    assert (await space.read_review("late")).content.endswith("Of the second one too.")
    assert sorted(item.commission_id for item in await space.list_reviews()) == ["early", "late"]
    assert [entry.id for entry in await space.list_entries(first.directory, archive_year=year)] == ["first"]
    assert [entry.id for entry in await space.list_entries(second.directory, archive_year=year)] == ["second"]
    assert (await space.read_entry("second", second.directory, archive_year=year)).content == "Of the second one."
    assert await space.read_entry("second", first.directory, archive_year=year) is None
    listed = [(item.name, item.directory) for item in await space.list_projects() if item.status == "archived"]
    assert listed == [("Budget app", "Budget app"), ("Budget app", "Budget app-2")]


async def test_a_project_a_commission_was_filed_under_meanwhile_is_not_archived(space, root, clock):
    project = await space.touch_project("Budget app")
    seen = project.last_commission_at
    clock.now += 40 * 86400
    # While its summary was being written, a commission that names it started.
    await space.assign_project(_commission(dispatch_text="go on with the budget app"))

    assert await space.archive_project("Budget app", summary="s", unless_touched_since=seen) is None
    assert (root / "projects" / "Budget app").is_dir() and not (root / "archive").exists()
    assert not (root / "projects" / "Budget app" / "SUMMARY.md").exists()
    # Untouched since it was looked at, it is archived.
    current = await space.get_project("Budget app")
    assert (await space.archive_project("Budget app", unless_touched_since=current.last_commission_at)).status == "archived"


async def test_a_project_in_use_says_so_and_is_not_archived_under_the_commission(space, root, clock):
    project = await space.touch_project("Budget app")
    seen = project.last_commission_at
    clock.now += 40 * 86400

    # A commission of it is taken up again: nothing is filed, the ledger does not show it yet.
    await space.use_project("Budget app")

    current = await space.get_project("Budget app")
    assert (current.last_commission_at, current.commissions) == (clock.now, 1)
    # Archiving looked at the project before that, and finds it changed when it comes to move it.
    assert await space.archive_project("Budget app", summary="s", unless_touched_since=seen) is None
    assert (root / "projects" / "Budget app").is_dir()


async def test_using_a_project_brings_it_back_from_the_archive(space, root, clock):
    await space.touch_project("Budget app")
    await space.file_entry("Budget app", title="Stack", content="Flask and SQLite.")
    await space.archive_project("Budget app", summary="s")
    clock.now += 100

    await space.use_project("Budget app")

    current = await space.get_project("Budget app")
    assert (current.status, current.last_commission_at) == ("active", clock.now)
    assert (root / "projects" / "Budget app" / "memory" / "stack.md").is_file()


async def test_a_project_record_that_cannot_be_read_is_never_written_over(space, root, clock):
    await space.touch_project("Budget app", description="tracks spending", workdir="D:/w")
    record = root / "projects" / "Budget app" / "project.json"
    # Held open by another program, or cut off by a crash: not what this can read.
    unreadable = b"{ \"name\": \"Budget app\", \"workdir\": \"D:/w\""
    record.write_bytes(unreadable)

    # A commission of it is taken up again.
    await space.use_project("Budget app")
    assert record.read_bytes() == unreadable

    # A commission is not filed under a project whose record is unknown.
    with pytest.raises(OSError):
        await space.touch_project("Budget app")
    with pytest.raises(OSError):
        await space.assign_project(_commission(dispatch_text="go on with the budget app"))
    assert record.read_bytes() == unreadable
    assert await space.note_archive_attempt("Budget app") == 0
    assert record.read_bytes() == unreadable
    # Nor is it archived by a round of tidying, which cannot tell how long it was idle.
    assert await space.archive_project("Budget app", unless_touched_since=0.0) is None

    # Archived and brought back, its record travels as it is.
    archived = await space.archive_project("Budget app", summary="s")
    moved = root / "archive" / archived.archive_year / "Budget app" / "project.json"
    assert moved.read_bytes() == unreadable
    await space.use_project("Budget app")
    assert record.read_bytes() == unreadable

    # Put right by the user, it is a project with its files again.
    record.write_text('{"name": "Budget app", "workdir": "D:/w", "description": "tracks spending"}', encoding="utf-8")
    project = await space.touch_project("Budget app")
    assert (project.workdir, project.description) == ("D:/w", "tracks spending")


@pytest.mark.parametrize("action", ["touch", "assign", "use", "attempt", "restore"])
async def test_a_record_that_could_not_be_read_a_moment_ago_is_not_written_over(space, root, monkeypatch, action):
    await space.touch_project("Budget app", description="tracks spending", workdir="D:/w")
    if action == "restore":
        await space.archive_project("Budget app", summary="s")
    (record,) = list(root.rglob("project.json"))
    before = record.read_bytes()
    read = Path.read_text
    failures = {"left": 1}

    def read_text(self, *args, **kwargs):
        # Held by another program for a moment: the first read fails, the next works.
        if self.name == "project.json" and failures["left"] > 0:
            failures["left"] -= 1
            raise PermissionError(13, "in use")
        return read(self, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", read_text)

    if action == "touch":
        with pytest.raises(OSError):
            await space.touch_project("Budget app")
    elif action == "assign":
        # Here the read that fails is the one that lists the projects. The
        # record is then read once, whole, and written back with all it had.
        filed = await space.assign_project(_commission(dispatch_text="go on with the budget app"))
        assert (filed.workdir, filed.description, filed.commissions) == ("D:/w", "tracks spending", 2)
    elif action == "use":
        await space.use_project("Budget app")
    elif action == "attempt":
        assert await space.note_archive_attempt("Budget app") == 0
    else:
        failures["left"] = 0
        # Listed while it could be read; unreadable for a moment once it is back.
        (archived,) = [item for item in await space.list_projects() if item.status == "archived"]
        failures["left"] = 1
        await asyncio.to_thread(space._restore, archived)
        (record,) = list(root.rglob("project.json"))

    assert failures["left"] == 0
    monkeypatch.undo()
    project = (await space.list_projects())[0]
    assert (project.workdir, project.description) == ("D:/w", "tracks spending")
    if action != "assign":
        assert record.read_bytes() == before
        assert project.commissions == 1


async def test_a_record_that_cannot_be_read_when_a_commission_is_filed_under_it_stops_the_filing(space, root, monkeypatch):
    await space.touch_project("Budget app", description="tracks spending", workdir="D:/w")
    record = root / "projects" / "Budget app" / "project.json"
    before = record.read_bytes()
    read = Path.read_text
    reads: list[str] = []

    def read_text(self, *args, **kwargs):
        if self.name == "project.json":
            reads.append(str(self))
            if len(reads) == 2:
                # Listed a moment ago, by its name in what was dispatched; held by another program now.
                raise PermissionError(13, "in use")
        return read(self, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", read_text)

    with pytest.raises(OSError):
        await space.assign_project(_commission(dispatch_text="go on with the budget app"))

    monkeypatch.undo()
    # The listing read it, the filing could not: nothing was written, no commission was counted.
    assert len(reads) == 2
    assert record.read_bytes() == before
    assert (await space.get_project("Budget app")).commissions == 1


async def test_using_a_project_that_is_not_there_makes_nothing(space, root):
    await space.use_project("No such project")
    await space.use_project(DEFAULT_PROJECT)

    assert not root.exists()
    with pytest.raises(ValueError):
        await space.use_project("../outside")


def test_a_copy_to_another_volume_is_published_whole_or_not_at_all(tmp_path, monkeypatch):
    import errno
    import os
    import shutil

    from nekoclaw.host.space import _move

    source, target = tmp_path / "archive" / "2026" / "Budget app", tmp_path / "projects" / "Budget app"
    (source / "memory").mkdir(parents=True)
    (source / "memory" / "stack.md").write_text("Flask.", encoding="utf-8")
    (source / "project.json").write_text("{}", encoding="utf-8")
    target.parent.mkdir()
    rename = os.replace

    def other_volume(first, second):
        if (str(first), str(second)) == (str(source), str(target)):
            raise OSError(errno.EXDEV, "cross-device link")
        return rename(first, second)

    monkeypatch.setattr(os, "replace", other_volume)
    copy = shutil.copytree

    def volume_full(first, second, *args, **kwargs):
        # Half of it arrives, then the volume is full.
        os.makedirs(second)
        shutil.copy(os.path.join(first, "project.json"), second)
        raise OSError(errno.ENOSPC, "no space left on device")

    monkeypatch.setattr(shutil, "copytree", volume_full)

    with pytest.raises(OSError):
        _move(source, target)

    # Nothing under the project's name, nothing left of the copy, the source whole.
    assert sorted(item.name for item in target.parent.iterdir()) == []
    assert (source / "memory" / "stack.md").is_file() and (source / "project.json").is_file()

    monkeypatch.setattr(shutil, "copytree", copy)
    _move(source, target)

    assert (target / "memory" / "stack.md").read_text(encoding="utf-8") == "Flask."
    assert not source.exists()
    assert sorted(item.name for item in target.parent.iterdir()) == ["Budget app"]
    assert sorted(item.name for item in source.parent.iterdir()) == []


def test_a_source_that_cannot_be_set_aside_stays_whole_and_nothing_is_published(tmp_path, monkeypatch):
    import errno
    import os

    from nekoclaw.host.space import _move

    source, target = tmp_path / "projects" / "Budget app", tmp_path / "archive" / "Budget app"
    (source / "memory").mkdir(parents=True)
    (source / "memory" / "stack.md").write_text("Flask.", encoding="utf-8")
    target.parent.mkdir()
    rename = os.replace

    def other_volume_and_in_use(first, second):
        if (str(first), str(second)) == (str(source), str(target)):
            raise OSError(errno.EXDEV, "cross-device link")
        if str(first) == str(source):
            raise PermissionError(13, "a file in it is held open")
        return rename(first, second)

    monkeypatch.setattr(os, "replace", other_volume_and_in_use)

    with pytest.raises(PermissionError):
        _move(source, target)

    assert (source / "memory" / "stack.md").is_file()
    assert sorted(item.name for item in target.parent.iterdir()) == []
    assert sorted(item.name for item in source.parent.iterdir()) == ["Budget app"]


async def test_what_is_left_of_a_project_copied_elsewhere_is_not_the_project(space, root, clock, monkeypatch):
    import errno
    import os
    import shutil

    await space.touch_project("Budget app", description="tracks spending")
    await space.file_entry("Budget app", title="Stack", content="Flask and SQLite.")
    await space.file_entry("Budget app", title="Export", content="CSV with a BOM.")
    rename = os.replace
    remove = shutil.rmtree

    def other_volume(first, second):
        # The archive is on another volume than the projects.
        parents = {Path(first).parent.name, Path(second).parent.parent.name, Path(first).parent.parent.name, Path(second).parent.name}
        if {"projects", "archive"} <= parents and not Path(first).name.startswith(".") and not Path(second).name.startswith("."):
            raise OSError(errno.EXDEV, "cross-device link")
        return rename(first, second)

    def half_removed(path, *args, **kwargs):
        # Removing what was set aside stops half way: one file is gone, the rest is held open.
        if Path(path).name.startswith(".removing-"):
            (Path(path) / "memory" / "stack.md").unlink(missing_ok=True)
            return None
        return remove(path, *args, **kwargs)

    monkeypatch.setattr(os, "replace", other_volume)
    monkeypatch.setattr(shutil, "rmtree", half_removed)

    archived = await space.archive_project("Budget app", summary="s")

    # Archived whole. What is left among the projects is no project, and is not used as one.
    assert archived is not None and archived.status == "archived"
    assert sorted(item.name for item in (root / "projects").iterdir()) == [".removing-Budget app"]
    assert [(item.name, item.status) for item in await space.list_projects()] == [("Budget app", "archived")]

    # A commission of it is taken up again: the whole project comes back from the archive.
    monkeypatch.setattr(shutil, "rmtree", remove)
    await space.use_project("Budget app")

    assert sorted(entry.id for entry in await space.list_entries("Budget app")) == ["export", "stack"]
    assert [(item.name, item.status) for item in await space.list_projects()] == [("Budget app", "active")]


async def test_a_copy_that_was_cut_off_is_not_taken_for_a_project(space, root):
    await space.touch_project("Budget app")
    # Left by a process that was killed while it copied.
    (root / "projects" / ".unfinished-Blog").mkdir()
    (root / "archive" / "2026" / ".unfinished-Old").mkdir(parents=True)

    assert [item.name for item in await space.list_projects()] == ["Budget app"]
    assert await space.assign_project(_commission(dispatch_text="the .unfinished-Blog thing")) is None


async def test_what_is_written_under_an_archived_project_brings_it_back_instead_of_splitting_it(space, root):
    await space.touch_project("Budget app", description="tracks spending")
    await space.file_entry("Budget app", title="Stack", content="Flask and SQLite.")
    archived = await space.archive_project("Budget app", summary="s")
    home = root / "archive" / archived.archive_year / "Budget app"

    # A commission of the project files a lesson a moment after the project was moved.
    await space.submit(_commission(project="Budget app"), _lesson("Export", "CSV with a BOM."))

    assert not home.exists()
    assert sorted(entry.id for entry in await space.list_entries("Budget app")) == ["export", "stack"]
    assert [(item.name, item.status) for item in await space.list_projects()] == [("Budget app", "active")]

    again = await space.archive_project("Budget app", summary="s2")
    await space.write_review("Budget app", _commission(id="late"), "It finished late.")

    assert not (root / "archive" / again.archive_year / "Budget app").exists()
    assert (root / "projects" / "Budget app" / "reviews" / "late.md").is_file()
    assert (root / "projects" / "Budget app" / "memory" / "stack.md").is_file()


async def test_the_daily_project_is_never_archived(space):
    await space.touch_project(DEFAULT_PROJECT)

    assert await space.archive_project(DEFAULT_PROJECT) is None


async def test_a_project_that_cannot_be_moved_stays(space, root, monkeypatch):
    await space.touch_project("Budget app")

    def stuck(source, target):
        raise PermissionError(13, "in use")

    monkeypatch.setattr("nekoclaw.host.space._move", stuck)

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


def test_a_directory_that_cannot_be_renamed_is_not_copied_and_removed_instead(tmp_path, monkeypatch):
    import errno
    import os

    from nekoclaw.host.space import _move

    source, target = tmp_path / "projects" / "Budget app", tmp_path / "archive" / "Budget app"
    (source / "memory").mkdir(parents=True)
    (source / "memory" / "stack.md").write_text("Flask.", encoding="utf-8")
    target.parent.mkdir()
    attempts: list[str] = []

    def in_use(first, second):
        attempts.append("rename")
        raise PermissionError(13, "a file in it is held open")

    monkeypatch.setattr(os, "replace", in_use)
    monkeypatch.setattr("shutil.copytree", lambda *args, **kwargs: attempts.append("copy"))

    with pytest.raises(PermissionError):
        _move(source, target)

    # Tried again a few times, never copied: the source is whole and there is no target.
    assert attempts == ["rename"] * 3
    assert (source / "memory" / "stack.md").is_file() and not target.exists()

    # Another volume is the one case renaming cannot work in: then it is copied.
    monkeypatch.undo()
    rename = os.replace

    def other_volume(first, second):
        if (str(first), str(second)) == (str(source), str(target)):
            raise OSError(errno.EXDEV, "cross-device link")
        return rename(first, second)

    monkeypatch.setattr(os, "replace", other_volume)
    _move(source, target)
    assert (target / "memory" / "stack.md").is_file() and not source.exists()


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


async def test_a_project_archived_a_moment_ago_is_brought_back_before_its_notes_are_read(space, root):
    await space.touch_project("Budget app")
    await space.write_rules("Use Flask.", "Budget app")
    await space.file_entry("Budget app", title="Stack", content="Flask and SQLite.")
    # Archived just as a commission of it is taken up again.
    await space.archive_project("Budget app", summary="s")

    brief = await space.brief("Budget app")

    assert "Rules of project Budget app:\nUse Flask." in brief
    assert "Index of project Budget app:\n- [Stack](memory/stack.md)" in brief
    assert [(item.name, item.status) for item in await space.list_projects()] == [("Budget app", "active")]
    assert not (root / "archive" / "2027").exists() or not any((root / "archive").rglob("project.json"))


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


# ---------------------------------------------------------------------------
# The ninth review round
# ---------------------------------------------------------------------------


async def test_a_link_to_a_project_whose_name_has_spaces_or_brackets_is_still_a_link(space, root):
    await space.touch_project("My App (v2)", description="second try")
    await space.touch_project("blog")

    lines = (root / "MEMORY.md").read_text(encoding="utf-8").splitlines()

    assert lines == [
        "- [project: blog](projects/blog/MEMORY.md)",
        "- [project: My App (v2)](<projects/My App (v2)/MEMORY.md>) — second try",
    ]
    # One line each, however often they are used; and the line goes when the project does.
    await space.touch_project("My App (v2)")
    assert (root / "MEMORY.md").read_text(encoding="utf-8").splitlines() == lines
    archived = await space.archive_project("My App (v2)", summary="s")
    index = (root / "MEMORY.md").read_text(encoding="utf-8")
    assert "projects/My App (v2)/" not in index
    assert f"(<archive/{archived.archive_year}/My App (v2)/SUMMARY.md>)" in index
    await space.use_project("My App (v2)")
    index = (root / "MEMORY.md").read_text(encoding="utf-8")
    assert "archive/" not in index and index.count("(<projects/My App (v2)/MEMORY.md>)") == 1


async def test_a_line_the_user_wrote_without_angle_brackets_is_the_projects_line_all_the_same(space, root):
    await space.touch_project("Budget app")
    index = root / "MEMORY.md"
    index.write_text("- [my budget thing](projects/Budget app/MEMORY.md) — my own words\n", encoding="utf-8")
    # Its record is gone, so it is made anew, and its line is looked for.
    (root / "projects" / "Budget app" / "project.json").unlink()

    await space.touch_project("Budget app")

    assert index.read_text(encoding="utf-8") == "- [my budget thing](projects/Budget app/MEMORY.md) — my own words\n"
    await space.archive_project("Budget app", summary="s")
    assert "my budget thing" not in index.read_text(encoding="utf-8")


async def test_an_entry_of_an_archived_project_is_changed_and_removed_where_it_is(space, root):
    await space.touch_project("Budget app")
    await space.file_entry("Budget app", title="Stack", content="Flask and SQLite.")
    await space.file_entry("Budget app", title="Export", content="CSV with a BOM.")
    archived = await space.archive_project("Budget app", summary="s")
    year, directory = archived.archive_year, archived.directory
    home = root / "archive" / year / directory

    edited = await space.edit_entry("stack", directory, archive_year=year, title="The stack", content="FastAPI now.")

    assert (edited.title, edited.content) == ("The stack", "FastAPI now.")
    assert "FastAPI now." in (home / "memory" / "stack.md").read_text(encoding="utf-8")
    assert "- [The stack](memory/stack.md)" in (home / "MEMORY.md").read_text(encoding="utf-8")

    assert await space.delete_entry("export", directory, archive_year=year) is True
    assert await space.delete_entry("export", directory, archive_year=year) is False

    assert [entry.id for entry in await space.list_entries(directory, archive_year=year)] == ["stack"]
    assert "export.md" not in (home / "MEMORY.md").read_text(encoding="utf-8")
    # Changed where it is: nothing was brought back, nothing was made among the projects.
    assert [(item.name, item.status) for item in await space.list_projects()] == [("Budget app", "archived")]
    assert not (root / "projects" / "Budget app").exists()
    # Without the year it is the active project of that name that is meant, and there is none.
    assert await space.edit_entry("stack", "Budget app", content="x") is None
    assert await space.delete_entry("stack", "Budget app") is False


async def test_a_project_that_cannot_be_moved_stays_and_its_summary_is_kept_for_the_next_try(space, root, monkeypatch):
    await space.touch_project("Budget app", description="tracks spending", workdir="D:/w")
    await space.file_entry("Budget app", title="Stack", content="Flask and SQLite.")

    def held_open(source, target):
        raise PermissionError(13, "a file in it is held open")

    monkeypatch.setattr("nekoclaw.host.space._move", held_open)

    # Asked by hand, without a summary: there is nothing to keep, and nothing is written.
    untouched = (root / "projects" / "Budget app" / "project.json").read_bytes()
    assert await space.archive_project("Budget app") is None
    assert (root / "projects" / "Budget app" / "project.json").read_bytes() == untouched
    assert (await space.get_project("Budget app")).kept_summary() is None
    # With a summary, which a model was asked for: kept in the project's record.
    assert await space.archive_project("Budget app", summary="An app.", description="spending tracker, paused") is None
    record = (root / "projects" / "Budget app" / "project.json").read_bytes()
    assert await space.archive_project("Budget app") is None

    project = await space.get_project("Budget app")
    assert project.kept_summary() == ("An app.", "spending tracker, paused")
    assert (project.archive_attempts, project.status, project.workdir) == (0, "active", "D:/w")
    # A try that brings nothing new writes nothing.
    assert (root / "projects" / "Budget app" / "project.json").read_bytes() == record
    assert [entry.id for entry in await space.list_entries("Budget app")] == ["stack"]
    assert "[project: Budget app]" in (root / "MEMORY.md").read_text(encoding="utf-8")

    # Once it can be moved, the summary that was kept is used though the try brings none.
    monkeypatch.undo()
    archived = await space.archive_project("Budget app")

    home = root / "archive" / archived.archive_year / "Budget app"
    assert (home / "SUMMARY.md").read_text(encoding="utf-8") == "An app.\n"
    assert f"(<archive/{archived.archive_year}/Budget app/SUMMARY.md>) — spending tracker, paused" in (
        root / "MEMORY.md"
    ).read_text(encoding="utf-8")
    assert archived.kept_summary() is None
    assert "pending_archive" not in (home / "project.json").read_text(encoding="utf-8")


@pytest.mark.parametrize("use", ["a new commission", "taken up again"])
async def test_a_summary_that_was_kept_no_longer_holds_once_the_project_is_used(space, clock, monkeypatch, use):
    await space.touch_project("Budget app")

    def held_open(source, target):
        raise PermissionError(13, "a file in it is held open")

    monkeypatch.setattr("nekoclaw.host.space._move", held_open)
    assert await space.archive_project("Budget app", summary="An app.") is None
    assert (await space.get_project("Budget app")).kept_summary() == ("An app.", "")
    monkeypatch.undo()
    clock.now += 60

    if use == "a new commission":
        await space.touch_project("Budget app")
    else:
        await space.use_project("Budget app")

    # It is another project now than the one that was summed up.
    assert (await space.get_project("Budget app")).kept_summary() is None


async def test_a_summary_that_cannot_be_saved_is_kept_in_the_projects_record(space, root, monkeypatch):
    await space.touch_project("Budget app")
    put = TaskAgentSpace._put_text

    def no_summary(path, text):
        if path.name == "SUMMARY.md":
            raise PermissionError(13, "in use")
        return put(path, text)

    monkeypatch.setattr(TaskAgentSpace, "_put_text", staticmethod(no_summary))

    assert await space.archive_project("Budget app", summary="An app.", description="paused") is None

    project = await space.get_project("Budget app")
    assert (project.status, project.archive_attempts) == ("active", 0)
    assert project.kept_summary() == ("An app.", "paused")
    assert not (root / "archive").exists()

    monkeypatch.undo()
    archived = await space.archive_project("Budget app")

    assert (root / "archive" / archived.archive_year / "Budget app" / "SUMMARY.md").read_text(encoding="utf-8") == "An app.\n"


@pytest.mark.parametrize(
    "command",
    [
        "printf 'x' >> {root}/MEMORY.md",
        "printf 'x' >> {backslashes}\\MEMORY.md",
        'Add-Content "{upper}/memory/a.md" x',
        "cat /d/Documents/data/nekoclaw/task_agent_space/AGENTS.md",
        "echo x > nekoclaw/task_agent_space/AGENTS.md",
        "echo x > nekoclaw\\\\task_agent_space\\\\AGENTS.md",
        "cd ~/data/NekoClaw/Task_Agent_Space && ls",
        # First into the directory above, then a path from there.
        "cd ../../../nekoclaw && printf x > task_agent_space/MEMORY.md",
        "cd nekoclaw; cd task_agent_space; echo x > AGENTS.md",
        # Only a mention of the name: refused with the rest.
        "grep -rn task_agent_space nekoclaw/",
    ],
)
async def test_a_command_that_names_the_space_is_seen_however_the_path_is_written(tmp_path, command):
    space = TaskAgentSpace(lambda: SpacePaths(tmp_path / "nekoclaw" / "task_agent_space"))
    root = (tmp_path / "nekoclaw" / "task_agent_space").as_posix()
    command = command.format(root=root, backslashes=root.replace("/", "\\"), upper=root.upper())

    assert await space.named_in(command) is True


@pytest.mark.parametrize(
    "command",
    [
        "ls",
        "python -m pytest tests/unit/nekoclaw/test_host_space.py",
        "cat nekoclaw/task_agents/projects/_daily/sessions/1/work/notes.txt",
        "echo task-agent-space taskagentspace task_agent",
        "",
    ],
)
async def test_a_command_about_something_else_is_not_taken_for_one_about_the_space(tmp_path, command):
    space = TaskAgentSpace(lambda: SpacePaths(tmp_path / "nekoclaw" / "task_agent_space"))

    assert await space.named_in(command) is False


# ---------------------------------------------------------------------------
# The tenth review round
# ---------------------------------------------------------------------------


def test_at_every_step_of_a_move_to_another_volume_the_directory_is_whole_under_its_name_somewhere(tmp_path, monkeypatch):
    import errno
    import os
    import shutil

    from nekoclaw.host.space import _move

    source, target = tmp_path / "projects" / "Budget app", tmp_path / "archive" / "Budget app"
    (source / "memory").mkdir(parents=True)
    for name in ("stack.md", "export.md"):
        (source / "memory" / name).write_text("kept", encoding="utf-8")
    target.parent.mkdir()
    rename, copy, remove = os.replace, shutil.copytree, shutil.rmtree
    found: list[tuple[str, bool]] = []

    def whole(directory: Path) -> bool:
        return all((directory / "memory" / name).is_file() for name in ("stack.md", "export.md"))

    def watched(step: str, function: Any) -> Any:
        def run(*args: Any, **kwargs: Any) -> Any:
            try:
                return function(*args, **kwargs)
            finally:
                # Were the process stopped right here, this is what the next start would find.
                found.append((step, whole(source) or whole(target)))

        return run

    def other_volume(first, second):
        if (str(first), str(second)) == (str(source), str(target)):
            raise OSError(errno.EXDEV, "cross-device link")
        return rename(first, second)

    monkeypatch.setattr(os, "replace", watched("rename", other_volume))
    monkeypatch.setattr(shutil, "copytree", watched("copy", copy))
    monkeypatch.setattr(shutil, "rmtree", watched("remove", remove))

    _move(source, target)

    assert [step for step, _whole in found].count("rename") >= 3 and ("copy", True) in found
    assert all(seen for _step, seen in found), found
    assert whole(target) and not source.exists()


@pytest.mark.parametrize("named", ["taken up again", "in what was dispatched"])
async def test_a_move_to_another_volume_cut_off_before_the_source_was_set_aside_leaves_the_project_in_use(
    space, root, clock, monkeypatch, named
):
    import errno
    import os

    from nekoclaw.host.space import _move

    await space.touch_project("Budget app", description="tracks spending", workdir="D:/w")
    await space.file_entry("Budget app", title="Stack", content="Flask and SQLite.")
    await space.file_entry("Budget app", title="Export", content="CSV with a BOM.")
    year = "2027"
    source, target = root / "projects" / "Budget app", root / "archive" / year / "Budget app"
    target.parent.mkdir(parents=True)
    rename = os.replace
    stopped: list[bool] = []

    class Stopped(BaseException):
        """The process is gone: nothing after this happens."""

    def other_volume_and_then_stopped(first, second):
        if stopped:
            raise Stopped()
        if (str(first), str(second)) == (str(source), str(target)):
            raise OSError(errno.EXDEV, "cross-device link")
        if Path(second).name.startswith(".removing-"):
            # Stopped right before the source is set aside: the copy has its name already.
            stopped.append(True)
            raise Stopped()
        return rename(first, second)

    monkeypatch.setattr(os, "replace", other_volume_and_then_stopped)
    with pytest.raises(Stopped):
        _move(source, target)
    monkeypatch.undo()

    # Started again: the project is where it was, whole, and is the one in use.
    again = TaskAgentSpace(lambda: SpacePaths(root), clock=clock)
    assert [(item.name, item.status) for item in await again.list_projects()] == [
        ("Budget app", "active"),
        ("Budget app", "archived"),
    ]
    await again.use_project("Budget app")
    assert sorted(entry.id for entry in await again.list_entries("Budget app")) == ["export", "stack"]
    assert (await again.get_project("Budget app")).workdir == "D:/w"
    assert "- [Stack](memory/stack.md)" in await again.brief("Budget app")

    # It is archived later like any other, and what comes back then is that later one.
    await again.file_entry("Budget app", title="Import", content="From CSV.")
    clock.now += 86400
    later = await again.archive_project("Budget app", summary="later")
    assert (later.archive_year, later.directory) == (year, "Budget app-2")
    # No commission was filed in between, so both archived ones were used last at the same time.
    if named == "taken up again":
        await again.use_project("Budget app")
    else:
        back = await again.assign_project(_commission(dispatch_text="go on with the budget app"))
        assert (back.name, back.status) == ("Budget app", "active")
    assert sorted(entry.id for entry in await again.list_entries("Budget app")) == ["export", "import", "stack"]


async def test_opening_a_project_leaves_an_archived_one_archived_when_told_to(space, root):
    await space.touch_project("Budget app", description="tracks spending")
    archived = await space.archive_project("Budget app", summary="s")

    assert await space.open_project("budget APP", unless_archived=True) is None

    assert [(item.name, item.status, item.commissions) for item in await space.list_projects()] == [
        ("Budget app", "archived", 1)
    ]
    assert not (root / "projects").exists() or list((root / "projects").iterdir()) == []
    assert (root / "archive" / archived.archive_year / "Budget app").is_dir()
    # A new name is made, an active one is used, whatever was said about archived ones.
    assert (await space.open_project("Blog", unless_archived=True)).name == "Blog"
    assert (await space.open_project("BLOG", unless_archived=True)).commissions == 2
    # Without it the archived one comes back, as before.
    assert (await space.open_project("budget app")).status == "active"


async def test_of_two_archived_projects_of_a_name_the_one_archived_last_comes_back(space, root, clock):
    await space.touch_project("Blog")
    await space.file_entry("Blog", title="Old stack", content="Jekyll.")
    await space.archive_project("Blog", summary="the first")
    clock.now += 86400
    await space.touch_project("Blog")
    await space.file_entry("Blog", title="New stack", content="Hugo.")
    await space.archive_project("Blog", summary="the second")
    clock.now += 60

    # A lesson of a commission that ended a moment after its project was archived.
    await space.file_entry("Blog", title="Deploy", content="Push to main.")

    assert sorted(entry.id for entry in await space.list_entries("Blog")) == ["deploy", "new-stack"]
    assert [(item.name, item.status, item.directory) for item in await space.list_projects()] == [
        ("Blog", "active", "Blog"),
        ("Blog", "archived", "Blog"),
    ]


@pytest.mark.parametrize("name", ["C# notes", "50% done", "notes [draft]", "a&b", "it's"])
async def test_a_link_to_a_project_whose_name_has_other_marks_is_in_angle_brackets_and_is_its_path(space, root, name):
    await space.touch_project(name)

    assert (root / "MEMORY.md").read_text(encoding="utf-8") == f"- [project: {name.replace('[', '(').replace(']', ')')}](<projects/{name}/MEMORY.md>)\n"
    # One line, and gone with the project.
    await space.touch_project(name)
    await space.archive_project(name, summary="s")
    index = (root / "MEMORY.md").read_text(encoding="utf-8")
    assert f"projects/{name}/" not in index and index.count("\n") == 1


@pytest.mark.parametrize("name", ["blog", "记账小程序", "notes-2.1_final", "Würfel"])
async def test_a_link_to_a_project_with_a_plain_name_is_written_as_it_is(space, root, name):
    await space.touch_project(name)

    assert (root / "MEMORY.md").read_text(encoding="utf-8") == f"- [project: {name}](projects/{name}/MEMORY.md)\n"


# ---------------------------------------------------------------------------
# The twelfth review round
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("category", ["progress", " Progress "])
async def test_how_a_project_stands_is_kept_in_the_project_whatever_scope_it_came_with(space, root, category):
    kept = await space.submit(
        _commission(project="Budget app"), _lesson("Where it stands", "The export is half done.", category, "global")
    )

    assert "project Budget app" in kept
    assert await space.list_entries() == []
    (entry,) = await space.list_entries("Budget app")
    assert (entry.id, entry.type) == ("where-it-stands", "progress")
    # Anything else that is for every project still goes there.
    await space.submit(_commission(project="Budget app"), _lesson("Use uv", "Run Python through uv.", "lesson", "global"))
    assert [item.id for item in await space.list_entries()] == ["use-uv"]


async def test_what_the_user_saves_while_an_entry_is_changed_is_not_written_over(space, root):
    await space.file_entry(None, title="Stack", content="Flask.")
    path = root / "memory" / "stack.md"
    real = space._signature
    saved = {"done": False}

    def signature(checked):
        if Path(checked) == path:
            if getattr(signature, "seen", False) and not saved["done"]:
                # Right before the file is replaced: the user saves another text from an editor.
                saved["done"] = True
                path.write_text(path.read_text(encoding="utf-8").replace("Flask.", "FastAPI now."), encoding="utf-8")
            signature.seen = True
        return real(checked)

    space._signature = signature

    entry = await space.edit_entry("stack", title="The stack")

    assert saved["done"]
    assert (entry.title, entry.content.strip()) == ("The stack", "FastAPI now.")
    assert "- [The stack](memory/stack.md)" in (root / "MEMORY.md").read_text(encoding="utf-8")


async def test_changing_an_entry_that_is_not_there_makes_nothing(space, root):
    assert await space.edit_entry("nothing", title="x") is None
    assert not root.exists()


async def test_who_filed_an_entry_is_noted_even_when_its_text_is_there_already(space, root, clock):
    first, second = _commission(id="c-first"), _commission(id="c-second")
    await space.submit(first, _lesson("Dates of photos", "Use the EXIF date."))
    before = (root / "projects" / DEFAULT_PROJECT / "memory" / "dates-of-photos.md").read_text(encoding="utf-8")
    clock.now += 86400

    await space.submit(second, _lesson("Dates of photos", "Use the EXIF date."))

    (entry,) = await space.list_entries(DEFAULT_PROJECT)
    full = await space.read_entry(entry.id, DEFAULT_PROJECT)
    assert full.sources == ("c-first", "c-second")
    # Nothing else of it changed: no second copy of the text, no new date.
    assert full.content.count("Use the EXIF date.") == 1 and full.updated == entry.created
    # The same commission again changes nothing at all.
    now = (root / "projects" / DEFAULT_PROJECT / "memory" / "dates-of-photos.md").read_text(encoding="utf-8")
    await space.submit(second, _lesson("Dates of photos", "Use the EXIF date."))
    assert (root / "projects" / DEFAULT_PROJECT / "memory" / "dates-of-photos.md").read_text(encoding="utf-8") == now
    assert now != before

    # So it is not the first commission's own, and does not go with it to a project.
    await space.touch_project("Budget app")
    assert await space.refile_entries("c-first", DEFAULT_PROJECT, "Budget app") == []
    assert [item.id for item in await space.list_entries(DEFAULT_PROJECT)] == ["dates-of-photos"]


async def test_a_line_the_user_reworded_goes_with_its_entry_to_another_project(space, root):
    commission = _commission(id="c-1")
    await space.submit(commission, _lesson("Stack", "It uses Flask and SQLite."))
    await space.submit(commission, _lesson("Export", "CSV with a BOM."))
    daily = root / "projects" / DEFAULT_PROJECT / "MEMORY.md"
    daily.write_text(
        daily.read_text(encoding="utf-8").replace(
            "- [Stack](memory/stack.md) — It uses Flask and SQLite.", "- [What it is built on](memory/stack.md) — my own words"
        ),
        encoding="utf-8",
    )
    await space.touch_project("Budget app")
    await space.file_entry("Budget app", title="Stack", content="An older note.")

    moved = await space.refile_entries("c-1", DEFAULT_PROJECT, "Budget app")

    assert sorted(moved) == ["export", "stack-2"]
    lines = (root / "projects" / "Budget app" / "MEMORY.md").read_text(encoding="utf-8").splitlines()
    # The user's wording, pointing at the file under its new name; the other line as the host wrote it.
    assert "- [What it is built on](memory/stack-2.md) — my own words" in lines
    assert "- [Export](memory/export.md) — CSV with a BOM." in lines
    assert "- [Stack](memory/stack.md) — An older note." in lines
    assert daily.read_text(encoding="utf-8").strip() == ""


# ---------------------------------------------------------------------------
# The thirteenth review round
# ---------------------------------------------------------------------------


async def test_an_entry_the_user_saves_while_it_moves_to_a_project_goes_there_as_saved(space, root):
    await space.submit(_commission(id="c-1"), _lesson("Stack", "It uses Flask."))
    await space.touch_project("Budget app")
    old = root / "projects" / DEFAULT_PROJECT / "memory" / "stack.md"
    real = space._signature
    saved = {"done": False}

    def signature(checked):
        if Path(checked) == old:
            if getattr(signature, "seen", False) and not saved["done"]:
                # After it was read and copied, before it is removed: the user saves another text.
                saved["done"] = True
                old.write_text(old.read_text(encoding="utf-8").replace("It uses Flask.", "It uses FastAPI now."), encoding="utf-8")
            signature.seen = True
        return real(checked)

    space._signature = signature

    assert await space.refile_entries("c-1", DEFAULT_PROJECT, "Budget app") == ["stack"]

    assert saved["done"]
    moved = await space.read_entry("stack", "Budget app")
    assert moved.content.strip() == "It uses FastAPI now."
    assert not old.exists() and await space.list_entries(DEFAULT_PROJECT) == []


async def test_an_entry_that_keeps_being_saved_while_it_moves_stays_where_it_was_too(space, root):
    await space.submit(_commission(id="c-1"), _lesson("Stack", "It uses Flask."))
    await space.touch_project("Budget app")
    old = root / "projects" / DEFAULT_PROJECT / "memory" / "stack.md"
    real = space._signature
    saves: list[int] = []

    def signature(checked):
        if Path(checked) == old:
            # Saved again every time it is looked at.
            saves.append(len(saves) + 1)
            old.write_text(old.read_text(encoding="utf-8") + f"\nline {saves[-1]}\n", encoding="utf-8")
        return real(checked)

    space._signature = signature

    assert await space.refile_entries("c-1", DEFAULT_PROJECT, "Budget app") == ["stack"]

    del space._signature
    # Nothing the user saved is gone: the file is still where it was, with every line.
    assert old.read_text(encoding="utf-8").count("line ") == len(saves) > 0
    assert [entry.id for entry in await space.list_entries(DEFAULT_PROJECT)] == ["stack"]
    assert "stack.md" in (root / "projects" / DEFAULT_PROJECT / "MEMORY.md").read_text(encoding="utf-8")
    assert [entry.id for entry in await space.list_entries("Budget app")] == ["stack"]


async def test_a_project_archived_right_before_the_process_stopped_is_the_one_that_comes_back(space, root, clock, monkeypatch):
    await space.touch_project("Blog")
    await space.file_entry("Blog", title="Old stack", content="Jekyll.")
    await space.archive_project("Blog", summary="the first")
    clock.now += 86400
    await space.touch_project("Blog")
    await space.file_entry("Blog", title="New stack", content="Hugo.")
    clock.now += 60

    put = TaskAgentSpace._put_project

    def stopped_after_the_move(self, directory, project):
        # The directory is in the archive: the process stops before anything after the move is written.
        if "archive" in directory.parts:
            raise RuntimeError("the process stopped here")
        return put(self, directory, project)

    monkeypatch.setattr(TaskAgentSpace, "_put_project", stopped_after_the_move)
    with pytest.raises(RuntimeError):
        await space.archive_project("Blog", summary="the second")
    monkeypatch.undo()

    # Started again. The record went with the directory, and says when.
    again = TaskAgentSpace(lambda: SpacePaths(root), clock=clock)
    projects = await again.list_projects()
    assert [(item.name, item.status, item.directory) for item in projects] == [
        ("Blog", "archived", "Blog"),
        ("Blog", "archived", "Blog-2"),
    ]
    assert projects[1].archived_at == clock.now and projects[0].archived_at == clock.now - 86460
    # What comes back by the name is the one archived last, not the older one of the name.
    await again.file_entry("Blog", title="Deploy", content="Push to main.")
    assert sorted(entry.id for entry in await again.list_entries("Blog")) == ["deploy", "new-stack"]
    index = (root / "MEMORY.md").read_text(encoding="utf-8")
    assert index.count("[project: Blog](projects/Blog/MEMORY.md)") == 1


# ---------------------------------------------------------------------------
# The fifteenth review round
# ---------------------------------------------------------------------------


async def test_a_summary_in_use_is_still_kept_when_the_process_stops_before_the_move(space, root, clock, monkeypatch):
    await space.touch_project("Budget app", description="tracks spending", workdir="D:/w")

    def stopped(source, target):
        raise RuntimeError("the process stopped here")

    # Its record is written ahead of the move; the process stops before the move.
    monkeypatch.setattr("nekoclaw.host.space._move", stopped)
    with pytest.raises(RuntimeError):
        await space.archive_project("Budget app", summary="An app.", description="paused")
    monkeypatch.undo()

    again = TaskAgentSpace(lambda: SpacePaths(root), clock=clock)
    project = await again.get_project("Budget app")
    # Still among the projects, with what it had, and the summary is not to be asked for again.
    assert (project.status, project.workdir, project.archived_at) == ("active", "D:/w", None)
    assert project.kept_summary() == ("An app.", "paused")

    archived = await again.archive_project("Budget app")

    home = root / "archive" / archived.archive_year / "Budget app"
    assert (home / "SUMMARY.md").read_text(encoding="utf-8") == "An app.\n"
    assert "pending_archive" not in (home / "project.json").read_text(encoding="utf-8")
    assert "— paused" in (root / "MEMORY.md").read_text(encoding="utf-8")


async def test_a_project_brought_back_keeps_no_summary_from_when_it_was_archived(space, root, clock, monkeypatch):
    await space.touch_project("Budget app")
    put = TaskAgentSpace._put_project

    def stopped_after_the_move(self, directory, project):
        # The directory is in the archive; the process stops before its record is tidied.
        if "archive" in directory.parts:
            raise RuntimeError("the process stopped here")
        return put(self, directory, project)

    monkeypatch.setattr(TaskAgentSpace, "_put_project", stopped_after_the_move)
    with pytest.raises(RuntimeError):
        await space.archive_project("Budget app", summary="An app.")
    monkeypatch.undo()
    again = TaskAgentSpace(lambda: SpacePaths(root), clock=clock)
    (archived,) = await again.list_projects()
    assert (archived.status, archived.kept_summary()) == ("archived", ("An app.", ""))

    # A lesson of a commission that ended late brings it back; nothing was commissioned for it.
    await again.file_entry("Budget app", title="Deploy", content="Push to main.")

    back = await again.get_project("Budget app")
    assert (back.status, back.kept_summary()) == ("active", None)


async def test_entries_are_not_moved_while_the_index_that_lists_them_cannot_be_read(space, root, monkeypatch):
    await space.submit(_commission(id="c-1"), _lesson("Stack", "It uses Flask."))
    daily = root / "projects" / DEFAULT_PROJECT / "MEMORY.md"
    daily.write_text("- [What it is built on](memory/stack.md) — my own words\n", encoding="utf-8")
    await space.touch_project("Budget app")
    read = Path.read_text

    def read_text(self, *args, **kwargs):
        if self == daily:
            raise PermissionError(13, "in use")
        return read(self, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", read_text)

    with pytest.raises(OSError):
        await space.refile_entries("c-1", DEFAULT_PROJECT, "Budget app")

    monkeypatch.undo()
    # Nothing moved, and the line the user wrote is where it was.
    assert [entry.id for entry in await space.list_entries(DEFAULT_PROJECT)] == ["stack"]
    assert await space.list_entries("Budget app") == []
    assert daily.read_text(encoding="utf-8") == "- [What it is built on](memory/stack.md) — my own words\n"
    # Once it reads again, the entry goes with the user's line.
    assert await space.refile_entries("c-1", DEFAULT_PROJECT, "Budget app") == ["stack"]
    assert "- [What it is built on](memory/stack.md) — my own words" in await space.read_index("Budget app")


# ---------------------------------------------------------------------------
# The sixteenth review round
# ---------------------------------------------------------------------------


async def test_a_line_the_user_rewords_while_its_entry_moves_goes_along_as_they_left_it(space, root):
    await space.submit(_commission(id="c-1"), _lesson("Stack", "It uses Flask."))
    await space.touch_project("Budget app")
    daily = root / "projects" / DEFAULT_PROJECT / "MEMORY.md"
    old = root / "projects" / DEFAULT_PROJECT / "memory" / "stack.md"
    real = space._signature
    saved = {"done": False}

    def signature(checked):
        if Path(checked) == old:
            if getattr(signature, "seen", False) and not saved["done"]:
                # The entry is copied; before it is removed the user rewords its line and saves the index.
                saved["done"] = True
                daily.write_text("# Mine\n\n- [What it is built on](memory/stack.md) — reworded just now\n", encoding="utf-8")
            signature.seen = True
        return real(checked)

    space._signature = signature

    assert await space.refile_entries("c-1", DEFAULT_PROJECT, "Budget app") == ["stack"]

    del space._signature
    assert saved["done"]
    assert "- [What it is built on](memory/stack.md) — reworded just now" in await space.read_index("Budget app")
    # Only that line went; what else the user wrote there stays.
    assert daily.read_text(encoding="utf-8") == "# Mine\n\n"
    assert await space.list_entries(DEFAULT_PROJECT) == []


async def test_an_entry_that_cannot_be_removed_where_it_was_keeps_its_line_there(space, root, monkeypatch):
    await space.submit(_commission(id="c-1"), _lesson("Stack", "It uses Flask."))
    await space.touch_project("Budget app")
    daily = root / "projects" / DEFAULT_PROJECT / "MEMORY.md"
    old = root / "projects" / DEFAULT_PROJECT / "memory" / "stack.md"
    before = daily.read_text(encoding="utf-8")
    unlink = Path.unlink

    def held_open(self, *args, **kwargs):
        if self == old:
            raise PermissionError(13, "in use")
        return unlink(self, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", held_open)

    assert await space.refile_entries("c-1", DEFAULT_PROJECT, "Budget app") == ["stack"]

    monkeypatch.undo()
    # It is in both places, listed in both: nothing is lost, nothing is out of sight.
    assert [entry.id for entry in await space.list_entries(DEFAULT_PROJECT)] == ["stack"]
    assert daily.read_text(encoding="utf-8") == before
    assert [entry.id for entry in await space.list_entries("Budget app")] == ["stack"]
    assert "- [Stack](memory/stack.md) — It uses Flask." in await space.read_index("Budget app")


# ---------------------------------------------------------------------------
# The seventeenth review round
# ---------------------------------------------------------------------------


async def test_a_text_that_only_occurs_inside_what_an_entry_says_is_still_added(space, root, clock):
    first = await space.submit(_commission(id="c-1"), _lesson("Running the tests", "Use pytest-xdist for parallel runs."))
    clock.now += 86400

    # Shorter, and a part of what is there: another thing to know all the same.
    second = await space.submit(_commission(id="c-2"), _lesson("Running the tests", "Use pytest"))
    # Word for word what was added, and word for word what it said first.
    third = await space.submit(_commission(id="c-3"), _lesson("Running the tests", "Use pytest"))
    fourth = await space.submit(_commission(id="c-4"), _lesson("Running the tests", "  Use pytest-xdist for parallel runs.\n"))

    assert first == "kept in project _daily as running-the-tests.md"
    assert second == "added to running-the-tests.md in project _daily"
    assert third == fourth == "already in running-the-tests.md in project _daily, word for word; nothing was added"
    entry = await space.read_entry("running-the-tests", DEFAULT_PROJECT)
    assert entry.content.count("Use pytest-xdist for parallel runs.") == 1
    assert entry.content.rstrip().endswith("Use pytest") and entry.content.count("## ") == 1
    # Who filed it is noted all the same.
    assert entry.sources == ("c-1", "c-2", "c-3", "c-4")


async def test_what_the_space_keeps_about_its_upkeep_is_not_written_over_when_it_cannot_be_read(space, root, monkeypatch):
    await space.write_state(upkeep_at=10.0, since=5.0, sessions_to_retry=["c-1", "c-2"])
    state = root / "state.json"
    before = state.read_bytes()
    read = Path.read_text

    def read_text(self, *args, **kwargs):
        if self == state:
            raise PermissionError(13, "in use")
        return read(self, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", read_text)

    with pytest.raises(OSError):
        await space.read_state()
    with pytest.raises(OSError):
        await space.write_state(upkeep_at=99.0)

    monkeypatch.undo()
    assert state.read_bytes() == before
    kept = await space.read_state()
    assert (kept["upkeep_at"], kept["since"], kept["sessions_to_retry"]) == (10.0, 5.0, ["c-1", "c-2"])


@pytest.mark.parametrize("content", [b"not json at all", b"[1, 2]", b"\xff\xfe\x00broken"])
async def test_a_state_file_that_is_not_what_the_space_writes_is_begun_anew(space, root, content):
    root.mkdir(parents=True)
    (root / "state.json").write_bytes(content)

    assert await space.read_state() == {}
    await space.write_state(upkeep_at=7.0)

    assert (await space.read_state())["upkeep_at"] == 7.0


async def test_an_entry_saved_once_more_while_its_line_moves_stays_where_it_was(space, root):
    await space.submit(_commission(id="c-1"), _lesson("Stack", "It uses Flask."))
    await space.touch_project("Budget app")
    daily = root / "projects" / DEFAULT_PROJECT / "MEMORY.md"
    old = root / "projects" / DEFAULT_PROJECT / "memory" / "stack.md"
    listed = daily.read_text(encoding="utf-8")
    real = space._signature
    looks: list[int] = []

    def signature(checked):
        if Path(checked) == old:
            looks.append(len(looks) + 1)
            if len(looks) == 3:
                # Copied, compared, its line moved: the user saves it once more before it is removed.
                old.write_text(old.read_text(encoding="utf-8").replace("It uses Flask.", "It uses FastAPI now."), encoding="utf-8")
        return real(checked)

    space._signature = signature

    assert await space.refile_entries("c-1", DEFAULT_PROJECT, "Budget app") == ["stack"]

    del space._signature
    assert len(looks) == 3
    # What the user saved last is still there, and still listed there.
    assert "It uses FastAPI now." in old.read_text(encoding="utf-8")
    assert daily.read_text(encoding="utf-8") == listed
    # The project has what was copied before that.
    assert (await space.read_entry("stack", "Budget app")).content.strip() == "It uses Flask."


# ---------------------------------------------------------------------------
# The eighteenth review round
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("step", ["archived", "brought back"])
async def test_a_project_is_moved_though_the_index_of_the_space_cannot_be_changed(space, root, monkeypatch, step):
    await space.touch_project("Budget app")
    await space.file_entry("Budget app", title="Stack", content="Flask.")
    if step == "brought back":
        await space.archive_project("Budget app", summary="s")
    index = root / "MEMORY.md"
    before = index.read_text(encoding="utf-8")
    edit = TaskAgentSpace._edit_text

    def keeps_changing(self, path, change):
        if path == index:
            raise OSError("MEMORY.md kept changing while it was being written")
        return edit(self, path, change)

    monkeypatch.setattr(TaskAgentSpace, "_edit_text", keeps_changing)

    if step == "archived":
        moved = await space.archive_project("Budget app", summary="s")
        assert moved is not None and moved.status == "archived"
    else:
        await space.use_project("Budget app")

    monkeypatch.undo()
    # Moved, whole; only the line in the index is as it was.
    (project,) = await space.list_projects()
    assert project.status == ("archived" if step == "archived" else "active")
    where = (project.directory, project.archive_year) if step == "archived" else ("Budget app", None)
    assert [entry.id for entry in await space.list_entries(where[0], archive_year=where[1])] == ["stack"]
    assert index.read_text(encoding="utf-8") == before


async def test_the_copy_of_an_entry_that_went_away_while_it_was_moved_is_not_left_behind(space, root):
    await space.submit(_commission(id="c-1"), _lesson("Stack", "It uses Flask."))
    await space.touch_project("Budget app")
    old = root / "projects" / DEFAULT_PROJECT / "memory" / "stack.md"
    real = space._signature
    looks: list[int] = []

    def signature(checked):
        if Path(checked) == old:
            looks.append(len(looks) + 1)
            if len(looks) == 2:
                # Copied; before it is compared the user removes the file.
                old.unlink()
        return real(checked)

    space._signature = signature

    assert await space.refile_entries("c-1", DEFAULT_PROJECT, "Budget app") == []

    del space._signature
    assert len(looks) >= 2
    assert await space.list_entries("Budget app") == []
    assert not (root / "projects" / "Budget app" / "memory" / "stack.md").exists()
    assert "stack.md" not in await space.read_index("Budget app")


async def test_a_summary_kept_does_not_hold_for_a_commission_filed_later_though_it_ended_earlier(space, clock, monkeypatch):
    await space.touch_project("Budget app")

    def held_open(source, target):
        raise PermissionError(13, "a file in it is held open")

    monkeypatch.setattr("nekoclaw.host.space._move", held_open)
    assert await space.archive_project("Budget app", summary="An app.") is None
    monkeypatch.undo()
    last = (await space.get_project("Budget app")).last_commission_at

    # A review, a day late, of a commission that ended before the one the project had last.
    project = await space.open_project("Budget app", at=last - 3600)

    assert (project.commissions, project.last_commission_at) == (2, last)
    assert (await space.get_project("Budget app")).kept_summary() is None


# ---------------------------------------------------------------------------
# The nineteenth review round
# ---------------------------------------------------------------------------


async def test_a_note_with_a_dated_heading_of_its_own_is_kept_once(space, root, clock):
    note = "Before the change.\n## 2026-11-15\nAfter the change."
    first = await space.submit(_commission(id="c-1"), _lesson("The change", note))
    clock.now += 86400
    second = await space.submit(_commission(id="c-2"), _lesson("The change", note))
    # Added to on another day, and then the same thing filed once more.
    await space.submit(_commission(id="c-3"), _lesson("The change", "A later note.\n## 2026-11-16\nWith a heading too."))
    clock.now += 86400
    again = await space.submit(_commission(id="c-4"), _lesson("The change", "A later note.\n## 2026-11-16\nWith a heading too."))
    third = await space.submit(_commission(id="c-5"), _lesson("The change", note))

    assert first == "kept in project _daily as the-change.md"
    assert second == again == third == "already in the-change.md in project _daily, word for word; nothing was added"
    entry = await space.read_entry("the-change", DEFAULT_PROJECT)
    assert entry.content.count("Before the change.") == 1 and entry.content.count("A later note.") == 1
    assert entry.sources == ("c-1", "c-2", "c-3", "c-4", "c-5")


async def test_an_entry_stays_listed_where_it_was_when_the_index_of_where_it_goes_cannot_be_written(space, root, monkeypatch):
    await space.submit(_commission(id="c-1"), _lesson("Stack", "It uses Flask."))
    daily = root / "projects" / DEFAULT_PROJECT / "MEMORY.md"
    daily.write_text("- [What it is built on](memory/stack.md) — my own words\n", encoding="utf-8")
    await space.touch_project("Budget app")
    there = root / "projects" / "Budget app" / "MEMORY.md"
    edit = TaskAgentSpace._edit_text

    def held_open(self, path, change):
        if path == there:
            raise PermissionError(13, "in use")
        return edit(self, path, change)

    monkeypatch.setattr(TaskAgentSpace, "_edit_text", held_open)

    with pytest.raises(OSError):
        await space.refile_entries("c-1", DEFAULT_PROJECT, "Budget app")

    monkeypatch.undo()
    # Where it was, listed in the user's words; nothing of it is left where it did not get to.
    assert [entry.id for entry in await space.list_entries(DEFAULT_PROJECT)] == ["stack"]
    assert daily.read_text(encoding="utf-8") == "- [What it is built on](memory/stack.md) — my own words\n"
    assert await space.list_entries("Budget app") == []
    assert not (root / "projects" / "Budget app" / "memory" / "stack.md").exists()


# ---------------------------------------------------------------------------
# The twentieth review round
# ---------------------------------------------------------------------------


async def test_a_text_that_is_the_end_of_a_note_with_a_dated_heading_is_another_text(space, root, clock):
    note = "Setup\n\n## 2026-11-15\n\nUse pytest"
    await space.submit(_commission(id="c-1"), _lesson("Running the tests", note))
    clock.now += 86400

    # What comes after the heading inside that note, filed by itself: not what the entry said, nor an addition to it.
    second = await space.submit(_commission(id="c-2"), _lesson("Running the tests", "Use pytest"))
    third = await space.submit(_commission(id="c-3"), _lesson("Running the tests", "Use pytest"))
    fourth = await space.submit(_commission(id="c-4"), _lesson("Running the tests", note))

    assert second == "added to running-the-tests.md in project _daily"
    assert third == fourth == "already in running-the-tests.md in project _daily, word for word; nothing was added"
    entry = await space.read_entry("running-the-tests", DEFAULT_PROJECT)
    assert entry.content.count("Use pytest") == 2 and entry.content.count("Setup") == 1


async def test_a_text_the_user_took_out_of_an_entry_is_added_when_it_is_filed_again(space, root, clock):
    await space.submit(_commission(id="c-1"), _lesson("Stack", "It uses Flask."))
    clock.now += 86400
    await space.submit(_commission(id="c-2"), _lesson("Stack", "It uses SQLite."))
    path = root / "projects" / DEFAULT_PROJECT / "memory" / "stack.md"
    # The user removes what was added, in an editor.
    text = path.read_text(encoding="utf-8")
    path.write_text(text[: text.index("## ")], encoding="utf-8")

    again = await space.submit(_commission(id="c-3"), _lesson("Stack", "It uses SQLite."))

    assert again == "added to stack.md in project _daily"
    assert (await space.read_entry("stack", DEFAULT_PROJECT)).content.count("It uses SQLite.") == 1


async def test_an_entry_written_by_hand_has_a_text_already_only_when_that_is_all_it_says(space, root):
    directory = root / "projects" / DEFAULT_PROJECT / "memory"
    directory.mkdir(parents=True)
    (directory / "stack.md").write_text("It uses Flask.\n", encoding="utf-8")

    same = await space.submit(_commission(id="c-1"), _lesson("stack", "It uses Flask."))
    other = await space.submit(_commission(id="c-2"), _lesson("stack", "It uses"))

    assert same == "already in stack.md in project _daily, word for word; nothing was added"
    assert other == "added to stack.md in project _daily"
    assert (directory / "stack.md").read_text(encoding="utf-8").count("It uses") == 2


# ---------------------------------------------------------------------------
# The twenty-first review round
# ---------------------------------------------------------------------------


async def test_a_text_an_entry_was_given_in_the_console_is_not_added_when_it_is_filed(space, root, clock):
    await space.submit(_commission(id="c-1"), _lesson("Stack", "It uses Flask."))
    await space.edit_entry("stack", DEFAULT_PROJECT, content="It uses FastAPI now.")
    clock.now += 86400

    same = await space.submit(_commission(id="c-2"), _lesson("Stack", "It uses FastAPI now."))
    # Something is added to it, and then the text it was given is filed once more.
    added = await space.submit(_commission(id="c-3"), _lesson("Stack", "And SQLite."))
    again = await space.submit(_commission(id="c-4"), _lesson("Stack", "It uses FastAPI now."))
    # What it said before it was changed is not what it says: filed again, it is added.
    before = await space.submit(_commission(id="c-5"), _lesson("Stack", "It uses Flask."))

    already = "already in stack.md in project _daily, word for word; nothing was added"
    assert (same, added, again) == (already, "added to stack.md in project _daily", already)
    assert before == "added to stack.md in project _daily"
    content = (await space.read_entry("stack", DEFAULT_PROJECT)).content
    assert content.count("It uses FastAPI now.") == 1 and content.count("And SQLite.") == 1


async def test_a_text_that_replaced_an_entrys_in_an_editor_is_not_added_when_it_is_filed(space, root):
    await space.submit(_commission(id="c-1"), _lesson("Stack", "It uses Flask."))
    path = root / "projects" / DEFAULT_PROJECT / "memory" / "stack.md"
    # Saved from an editor: the text is another, the marks in the front matter are the old ones.
    path.write_text(path.read_text(encoding="utf-8").replace("It uses Flask.", "It uses FastAPI now."), encoding="utf-8")

    # Filed by the commission the entry is from already: nothing of the entry changes but the mark of the text.
    same = await space.submit(_commission(id="c-1"), _lesson("Stack", "It uses FastAPI now."))
    # Something else is added, and then the text from the editor is filed once more.
    added = await space.submit(_commission(id="c-3"), _lesson("Stack", "And SQLite."))
    again = await space.submit(_commission(id="c-2"), _lesson("Stack", "It uses FastAPI now."))

    already = "already in stack.md in project _daily, word for word; nothing was added"
    assert (same, added, again) == (already, "added to stack.md in project _daily", already)
    content = (await space.read_entry("stack", DEFAULT_PROJECT)).content
    assert content.count("It uses FastAPI now.") == 1 and content.count("And SQLite.") == 1


# ---------------------------------------------------------------------------
# The twenty-second review round
# ---------------------------------------------------------------------------


async def test_a_text_found_inside_another_sentence_is_not_the_text_that_was_filed(space, root, clock):
    await space.submit(_commission(id="c-1"), _lesson("Running the tests", "Use pytest"))
    clock.now += 86400
    await space.submit(_commission(id="c-2"), _lesson("Running the tests", "The old instruction was Use pytest yesterday."))
    path = root / "projects" / DEFAULT_PROJECT / "memory" / "running-the-tests.md"
    # The user takes out what it said first; the same words are still inside the sentence that was added.
    text = path.read_text(encoding="utf-8")
    heading = text.index("## ")
    # The last time the words occur before the heading: in the text, not in the front matter.
    start = text.rindex("Use pytest", 0, heading)
    path.write_text(text[:start] + text[heading:], encoding="utf-8")
    assert "texts: " in path.read_text(encoding="utf-8")
    assert "The old instruction was Use pytest yesterday." in path.read_text(encoding="utf-8")

    again = await space.submit(_commission(id="c-3"), _lesson("Running the tests", "Use pytest"))
    once_more = await space.submit(_commission(id="c-4"), _lesson("Running the tests", "Use pytest"))

    assert again == "added to running-the-tests.md in project _daily"
    assert once_more == "already in running-the-tests.md in project _daily, word for word; nothing was added"


@pytest.mark.parametrize("saved", ["an entry is changed", "a file is added", "a file is removed"])
def test_a_directory_changed_while_it_is_copied_to_another_volume_stays_where_it_was(tmp_path, monkeypatch, saved):
    import errno
    import os
    import shutil

    from nekoclaw.host.space import _move

    source, target = tmp_path / "projects" / "Budget app", tmp_path / "archive" / "Budget app"
    (source / "memory").mkdir(parents=True)
    (source / "memory" / "stack.md").write_text("Flask.", encoding="utf-8")
    (source / "AGENTS.md").write_text("Old rules.", encoding="utf-8")
    target.parent.mkdir()
    rename, copy = os.replace, shutil.copytree

    def other_volume(first, second):
        if (str(first), str(second)) == (str(source), str(target)):
            raise OSError(errno.EXDEV, "cross-device link")
        return rename(first, second)

    def copied_and_then_saved(first, second, *args, **kwargs):
        result = copy(first, second, *args, **kwargs)
        if Path(first) != source:
            # A directory inside it: the copying is not done yet.
            return result
        # The copy is made; the user saves from an editor before the source is set aside.
        if saved == "an entry is changed":
            (source / "memory" / "stack.md").write_text("FastAPI now, and more.", encoding="utf-8")
        elif saved == "a file is added":
            (source / "memory" / "export.md").write_text("CSV.", encoding="utf-8")
        else:
            (source / "AGENTS.md").unlink()
        return result

    monkeypatch.setattr(os, "replace", other_volume)
    monkeypatch.setattr(shutil, "copytree", copied_and_then_saved)

    with pytest.raises(OSError, match="was changed while it was copied"):
        _move(source, target)

    # Where it was, as the user left it; nothing of it at the target, nothing left over.
    if saved == "an entry is changed":
        assert (source / "memory" / "stack.md").read_text(encoding="utf-8") == "FastAPI now, and more."
    elif saved == "a file is added":
        assert (source / "memory" / "export.md").read_text(encoding="utf-8") == "CSV."
    else:
        assert not (source / "AGENTS.md").exists() and (source / "memory" / "stack.md").is_file()
    assert sorted(item.name for item in source.parent.iterdir()) == ["Budget app"]
    assert sorted(item.name for item in target.parent.iterdir()) == []


async def test_a_line_taken_out_of_an_index_that_could_not_be_read_back_is_not_lost(space, root, monkeypatch):
    await space.submit(_commission(id="c-1"), _lesson("Stack", "It uses Flask."))
    daily = root / "projects" / DEFAULT_PROJECT / "MEMORY.md"
    daily.write_text("- [What it is built on](memory/stack.md) — my own words\n", encoding="utf-8")
    await space.touch_project("Budget app")
    put = TaskAgentSpace._put_text
    failed: list[bool] = []

    def written_and_not_read_back(path, text):
        put(path, text)
        if path == daily and not failed:
            # Replaced, and then what is there could not be checked.
            failed.append(True)
            raise OSError("the file does not hold what was written")

    monkeypatch.setattr(TaskAgentSpace, "_put_text", staticmethod(written_and_not_read_back))

    assert await space.refile_entries("c-1", DEFAULT_PROJECT, "Budget app") == ["stack"]

    monkeypatch.undo()
    assert failed
    words = "- [What it is built on](memory/stack.md) — my own words"
    # The user's line is where the entry went, and back where the entry still is.
    assert words in await space.read_index("Budget app")
    assert words in daily.read_text(encoding="utf-8")
    assert [entry.id for entry in await space.list_entries(DEFAULT_PROJECT)] == ["stack"]
