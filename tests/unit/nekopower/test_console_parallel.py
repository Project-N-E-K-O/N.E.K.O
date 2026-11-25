"""The routes of the console for several task agents at work in one project.

Adding a member to a project, giving a project its leader, the git
worktrees members work in and what the user decides on them, the landing
settings, and looking for words in what was said in a session
(``app/agent_server/api_console.py``), over a real host. Landing itself is
the host's (``test_host_tree_work.py``); here a worktree on record whose
directory is not a git worktree stands in for one, which is enough for what
the routes say.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi import FastAPI

import config.nekopower_settings as settings
from app.agent_server.api_console import TOKEN_HEADER, build_console_router
from nekopower.host.paths import DSH_SESSION_NAME, TRANSCRIPT_NAME
from tests.unit.nekopower.host_fakes import FakeDecisionLLM, fake_model, make_host

TOKEN = "token-of-this-launch"
HEADERS = {TOKEN_HEADER: TOKEN}


class _Console:
    """A client of the routes over a host of its own; closed with the test."""

    def __init__(self, tmp_path: Path, *, character: str | None = "Lan") -> None:
        self.host = make_host(tmp_path, None, task_agent_model=fake_model, decision_llm=FakeDecisionLLM())
        _no_reconciling(self.host)
        self.opened: list[Path] = []

        async def get_host() -> Any:
            return self.host

        async def who() -> str | None:
            return character

        app = FastAPI()
        app.include_router(build_console_router(get_host, lambda: TOKEN, character=who, open_directory=self.opened.append))
        self.client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://agent.test")

    async def get(self, path: str, **params: Any) -> httpx.Response:
        return await self.client.get(f"/console{path}", headers=HEADERS, params=params or None)

    async def post(self, path: str, payload: Any = None) -> httpx.Response:
        return await self.client.post(f"/console{path}", headers=HEADERS, json=payload)

    async def aclose(self) -> None:
        await self.client.aclose()
        await self.host.shutdown()


@pytest.fixture
async def consoles() -> Any:
    made: list[_Console] = []

    def make(*args: Any, **kwargs: Any) -> _Console:
        made.append(_Console(*args, **kwargs))
        return made[-1]

    yield make
    for console in made:
        await console.aclose()


def _no_reconciling(host: Any) -> None:
    """Keep the host from going through the worktrees on record as it is first used.

    It would do so in the background, taking each in hand meanwhile; the
    worktrees these tests put on record stand for what the user decides on,
    and are no leftovers of an earlier run (``test_host_tree_work.py``).
    """

    async def nothing() -> dict[str, list[str]]:
        return {"removed": [], "kept": []}

    host.task_agents.reconcile_trees = nothing


def _code(response: httpx.Response) -> tuple[int, str]:
    return response.status_code, response.json()["detail"]["code"]


async def _project(console: _Console, name: str = "Garden", workdir: Path | None = None) -> str:
    """Make a project with its first member from the page; return that member's session."""
    payload: dict[str, Any] = {"name": name, "preset": "general"}
    if workdir is not None:
        payload["workdir"] = str(workdir)
    made = await console.post("/familiars", payload)
    assert made.status_code == 200, made.text
    return made.json()["session_id"]


async def _tree(console: _Console, tmp_path: Path, name: str, state: str, **more: Any) -> str:
    """Put a worktree on record whose directory is there but is no git worktree; return its id."""
    path = tmp_path / "trees" / name
    path.mkdir(parents=True)
    values = {
        "id": name, "project_dir": str(tmp_path / "repo"), "path": str(path), "workdir": str(path),
        "project": "Garden", "lanlan_name": "Lan", "state": state, **more,
    }
    await console.host.ledger.add_tree(**values)
    return name


# ---------------------------------------------------------------------------
# Who may ask
# ---------------------------------------------------------------------------


async def test_none_of_these_routes_answers_without_the_token_of_this_launch(consoles, tmp_path):
    console = consoles(tmp_path)
    await _project(console)
    tree = await _tree(console, tmp_path, "t1", "unlanded", reason="conflict")
    asked = [
        ("get", "/console/worktrees", None),
        ("post", f"/console/worktrees/{tree}/land", None),
        ("post", f"/console/worktrees/{tree}/discard", None),
        ("post", f"/console/worktrees/{tree}/keep", None),
        ("post", f"/console/worktrees/{tree}/open", None),
        ("get", "/console/landing", None),
        ("post", "/console/landing", {"mode": "ask"}),
        ("post", "/console/familiars/member", {"project": "Garden"}),
        ("post", "/console/space/projects/leader", {"project": "Garden"}),
        ("get", "/console/familiars/x/transcript/search?q=beans", None),
    ]
    for method, path, payload in asked:
        answered = await console.client.request(method, path, headers={TOKEN_HEADER: "another"}, json=payload)
        assert answered.status_code == 403, path
    # Nothing was done on the way to the refusals.
    assert (await console.host.ledger.get_tree(tree)).state == "unlanded"
    assert (await console.host.landing_settings())["mode"] == "auto"
    assert len((await console.host.board("Garden"))["projects"][0]["members"]) == 1
    assert console.opened == []


# ---------------------------------------------------------------------------
# More task agents for a project that is there
# ---------------------------------------------------------------------------


async def test_a_member_is_added_to_a_project_that_is_there_and_works_where_the_project_does(consoles, tmp_path):
    console = consoles(tmp_path)
    first = await _project(console)
    added = await console.post("/familiars/member", {"project": "Garden", "preset": "scout", "title": "Finder", "duty": "looks"})
    assert added.status_code == 200, added.text
    body = added.json()
    assert body["success"] and body["session_id"] != first
    # Of a project that is no git repository it works in the project's directory: no worktree.
    assert body["seat"] == ""
    made = await console.host.ledger.get_session(body["session_id"])
    assert (made.project, made.kind, made.lanlan_name, made.preset) == ("Garden", "line", "Lan", "scout")
    assert (made.title, made.title_by, made.duty, made.duty_by) == ("Finder", "user", "looks", "user")
    # With no preset named, one that does the work.
    plain = (await console.post("/familiars/member", {"project": "Garden", "lanlan_name": "Mei"})).json()
    plain_session = await console.host.ledger.get_session(plain["session_id"])
    assert (plain_session.preset, plain_session.lanlan_name) == ("general", "Mei")


@pytest.mark.parametrize(
    ("payload", "wanted"),
    [
        ({}, (400, "bad_name")),
        ({"project": ""}, (400, "bad_name")),
        ({"project": 3}, (400, "bad_name")),
        ({"project": "Nowhere"}, (404, "no_project")),
        ({"project": "Garden", "preset": "boss"}, (400, "bad_preset")),
        ({"project": "Garden", "preset": "nothing-of-the-kind"}, (400, "bad_preset")),
        ({"project": "Garden", "title": 5}, (400, "bad_text")),
    ],
)
async def test_a_member_that_cannot_be_added_is_refused_with_a_code(consoles, tmp_path, payload, wanted):
    console = consoles(tmp_path)
    await _project(console)
    assert _code(await console.post("/familiars/member", payload)) == wanted
    assert len((await console.host.board("Garden"))["projects"][0]["members"]) == 1


async def test_a_project_with_as_many_members_as_it_may_takes_no_more(consoles, tmp_path, monkeypatch):
    console = consoles(tmp_path)
    await _project(console)
    monkeypatch.setattr(settings, "PROJECT_MEMBERS_MAX", 2)
    assert (await console.post("/familiars/member", {"project": "Garden"})).status_code == 200
    assert _code(await console.post("/familiars/member", {"project": "Garden"})) == (409, "too_many_members")
    assert len((await console.host.board("Garden"))["projects"][0]["members"]) == 2


async def test_a_member_for_nobody_is_refused(consoles, tmp_path):
    console = consoles(tmp_path, character=None)
    made = await console.post("/familiars", {"name": "Garden", "preset": "general", "lanlan_name": "Lan"})
    assert made.status_code == 200
    assert _code(await console.post("/familiars/member", {"project": "Garden"})) == (400, "no_character")
    assert _code(await console.post("/space/projects/leader", {"project": "Garden"})) == (400, "no_character")


async def test_a_project_is_given_its_leader_once(consoles, tmp_path):
    console = consoles(tmp_path)
    member = await _project(console)
    given = await console.post("/space/projects/leader", {"project": "Garden"})
    assert given.status_code == 200, given.text
    body = given.json()
    assert body["made"] is True and body["session_id"] != member
    leader = await console.host.ledger.get_session(body["session_id"])
    assert (leader.kind, leader.project) == ("project", "Garden")
    again = (await console.post("/space/projects/leader", {"project": "Garden"})).json()
    assert (again["made"], again["session_id"]) == (False, body["session_id"])
    # Shown as the project's leader on the board, beside the member that was there.
    cards = (await console.get("/board", project="Garden")).json()["projects"][0]["members"]
    assert {card["id"]: card["leader"] for card in cards} == {body["session_id"]: True, member: False}
    assert _code(await console.post("/space/projects/leader", {"project": "Nowhere"})) == (404, "no_project")
    assert _code(await console.post("/space/projects/leader", {})) == (400, "bad_name")


# ---------------------------------------------------------------------------
# The worktrees, and what the user decides on them
# ---------------------------------------------------------------------------


async def test_the_worktrees_are_listed_of_a_project_or_of_all(consoles, tmp_path):
    console = consoles(tmp_path)
    await _project(console)
    await _tree(console, tmp_path, "t1", "unlanded", reason="conflict", conflicts=("a.py",))
    await _tree(console, tmp_path, "t2", "held", reason="ask", project="Other")
    listed = (await console.get("/worktrees")).json()
    assert listed["success"] and [item["id"] for item in listed["worktrees"]] == ["t1", "t2"]
    first = listed["worktrees"][0]
    assert (first["state"], first["reason"], first["conflicts"], first["title"]) == ("unlanded", "conflict", ["a.py"], "")
    assert [item["id"] for item in (await console.get("/worktrees", project="Garden")).json()["worktrees"]] == ["t1"]
    assert (await console.get("/worktrees", project="Nowhere")).json()["worktrees"] == []
    assert _code(await console.get("/worktrees", project=" ")) == (400, "bad_name")


async def test_a_worktree_the_user_sees_to_is_kept_and_one_at_work_is_not_touched(consoles, tmp_path):
    console = consoles(tmp_path)
    await _project(console)
    left = await _tree(console, tmp_path, "t1", "unlanded", reason="failed")
    working = await _tree(console, tmp_path, "t2", "active")
    kept = await console.post(f"/worktrees/{left}/keep")
    assert kept.status_code == 200, kept.text
    assert kept.json()["worktree"]["state"] == "kept"
    assert (await console.host.ledger.get_tree(left)).state == "kept"
    # Kept already, or at work: nothing to decide on.
    assert _code(await console.post(f"/worktrees/{left}/keep")) == (409, "at_work")
    for action in ("land", "discard", "keep"):
        assert _code(await console.post(f"/worktrees/{working}/{action}")) == (409, "at_work")
        assert _code(await console.post(f"/worktrees/nothing/{action}")) == (404, "no_worktree")
    assert (await console.host.ledger.get_tree(working)).state == "active"


async def test_a_worktree_whose_directory_is_gone_is_said_to_be_and_can_be_discarded(consoles, tmp_path):
    console = consoles(tmp_path)
    await _project(console)
    tree = await _tree(console, tmp_path, "t1", "unlanded", reason="conflict")
    # The repository it was made of is not there (no git worktree of it can be found again).
    assert _code(await console.post(f"/worktrees/{tree}/land")) == (409, "gone")
    assert (await console.host.ledger.get_tree(tree)).state == "unlanded"
    discarded = await console.post(f"/worktrees/{tree}/discard")
    assert discarded.status_code == 200, discarded.text
    assert discarded.json()["success"] is True and isinstance(discarded.json()["removed"], bool)
    assert await console.host.ledger.get_tree(tree) is None
    assert _code(await console.post(f"/worktrees/{tree}/discard")) == (404, "no_worktree")


@pytest.mark.parametrize(
    ("raised", "wanted"), [("not_opened", "not_opened"), ("moved", "moved"), ("something_new", "worktree_refused")]
)
async def test_a_worktree_git_cannot_open_just_now_is_said_apart_from_one_that_is_gone(consoles, tmp_path, monkeypatch, raised, wanted):
    console = consoles(tmp_path)
    await _project(console)
    tree = await _tree(console, tmp_path, "t1", "unlanded", reason="conflict")

    async def refused(tree_id: str) -> Any:
        raise ValueError(raised)

    monkeypatch.setattr(console.host.task_agents, "land_tree", refused)
    assert _code(await console.post(f"/worktrees/{tree}/land")) == (409, wanted)


async def test_a_decision_on_a_worktree_while_another_is_carried_out_is_refused(consoles, tmp_path):
    console = consoles(tmp_path)
    await _project(console)
    tree = await _tree(console, tmp_path, "t1", "held", reason="ask")
    console.host.task_agents._trees_in_hand.add(tree)
    try:
        for action in ("land", "discard", "keep"):
            assert _code(await console.post(f"/worktrees/{tree}/{action}")) == (409, "busy")
    finally:
        console.host.task_agents._trees_in_hand.discard(tree)
    assert (await console.host.ledger.get_tree(tree)).state == "held"


async def test_the_directory_of_a_worktree_is_shown_only_for_one_on_record(consoles, tmp_path):
    console = consoles(tmp_path)
    await _project(console)
    tree = await _tree(console, tmp_path, "t1", "unlanded", reason="conflict")
    shown = await console.post(f"/worktrees/{tree}/open")
    assert shown.status_code == 200 and console.opened == [tmp_path / "trees" / "t1"]
    assert _code(await console.post("/worktrees/elsewhere/open")) == (404, "no_worktree")
    (tmp_path / "trees" / "t1").rmdir()
    assert _code(await console.post(f"/worktrees/{tree}/open")) == (404, "place_missing")
    assert console.opened == [tmp_path / "trees" / "t1"]


async def test_a_directory_that_cannot_be_shown_is_said_with_a_code(tmp_path):
    host = make_host(tmp_path, None, task_agent_model=fake_model, decision_llm=FakeDecisionLLM())
    _no_reconciling(host)

    async def get_host() -> Any:
        return host

    def broken(path: Path) -> None:
        raise RuntimeError("no file manager")

    app = FastAPI()
    app.include_router(build_console_router(get_host, lambda: TOKEN, open_directory=broken))
    path = tmp_path / "trees" / "t1"
    path.mkdir(parents=True)
    await host.ledger.add_tree(id="t1", project_dir=str(tmp_path), path=str(path), workdir=str(path), state="unlanded")
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://agent.test") as client:
        answered = await client.post("/console/worktrees/t1/open", headers=HEADERS)
        assert _code(answered) == (500, "open_failed")
    await host.shutdown()


# ---------------------------------------------------------------------------
# The landing settings
# ---------------------------------------------------------------------------


async def test_the_landing_settings_are_shown_and_set_one_at_a_time(consoles, tmp_path):
    console = consoles(tmp_path)
    shown = (await console.get("/landing")).json()
    assert (shown["success"], shown["mode"], shown["inspect"]) == (True, "auto", False)
    # How many worktrees a project may have, for the page to say when one is full.
    assert shown["worktrees_per_project"] == settings.WORKTREES_PER_PROJECT
    asked = (await console.post("/landing", {"mode": "ask"})).json()
    assert (asked["mode"], asked["inspect"]) == ("ask", False)
    inspected = (await console.post("/landing", {"inspect": True})).json()
    assert (inspected["mode"], inspected["inspect"]) == ("ask", True)
    assert {key: value for key, value in (await console.host.landing_settings()).items() if key != "worktrees_per_project"} == {
        "mode": "ask", "inspect": True,
    }
    both = (await console.post("/landing", {"mode": "auto", "inspect": False})).json()
    assert (both["mode"], both["inspect"]) == ("auto", False)


@pytest.mark.parametrize(
    ("payload", "wanted"),
    [
        ({}, (400, "nothing_to_change")),
        ({"mode": "sometimes"}, (400, "bad_mode")),
        ({"mode": None}, (400, "bad_mode")),
        ({"inspect": "yes"}, (400, "bad_flag")),
        ({"inspect": None}, (400, "bad_flag")),
        ({"mode": "ask", "inspect": 1}, (400, "bad_flag")),
        ({"beside": "sometimes"}, (400, "bad_beside")),
        ({"beside": None}, (400, "bad_beside")),
        ({"beside": ["ask"]}, (400, "bad_beside")),
        ({"mode": "ask", "beside": "never"}, (400, "bad_beside")),
    ],
)
async def test_a_landing_setting_that_is_none_is_refused_and_nothing_is_set(consoles, tmp_path, payload, wanted):
    console = consoles(tmp_path)
    assert _code(await console.post("/landing", payload)) == wanted
    assert (await console.host.landing_settings())["mode"] == "auto"
    assert (await console.host.landing_settings())["inspect"] is False


async def test_what_becomes_of_work_beside_a_busy_member_is_passed_on_with_nothing_else(consoles, tmp_path, monkeypatch):
    console = consoles(tmp_path)
    given: list[dict[str, Any]] = []

    async def set_landing_settings(**changes: Any) -> dict[str, Any]:
        given.append(changes)
        return {"mode": "auto", "inspect": False, "beside": changes.get("beside", "ask"), "worktrees_per_project": 8}

    monkeypatch.setattr(console.host, "set_landing_settings", set_landing_settings)
    for beside in ("open", "queue", "ask"):
        assert (await console.post("/landing", {"beside": beside})).json()["beside"] == beside
    # What is not given is not passed on, beside or the others.
    await console.post("/landing", {"mode": "ask"})
    await console.post("/landing", {"inspect": True, "beside": "queue"})
    assert given == [{"beside": "open"}, {"beside": "queue"}, {"beside": "ask"}, {"mode": "ask"}, {"inspect": True, "beside": "queue"}]


async def test_a_value_of_beside_the_host_does_not_take_is_said_with_its_code(consoles, tmp_path, monkeypatch):
    console = consoles(tmp_path)

    async def set_landing_settings(**changes: Any) -> dict[str, Any]:
        raise ValueError("bad_beside")

    monkeypatch.setattr(console.host, "set_landing_settings", set_landing_settings)
    assert _code(await console.post("/landing", {"beside": "open"})) == (400, "bad_beside")


async def test_a_landing_setting_that_could_not_be_written_is_said_with_a_code(consoles, tmp_path, monkeypatch):
    from nekopower.host import landing_settings

    console = consoles(tmp_path)

    def full(self: Any, values: Any, path: Any) -> None:
        raise OSError("the disk is full")

    monkeypatch.setattr(landing_settings.LandingStore, "_write", full)
    assert _code(await console.post("/landing", {"mode": "ask"})) == (409, "landing_not_saved")
    assert (await console.get("/landing")).json()["mode"] == "auto"


# ---------------------------------------------------------------------------
# Looking for words in what was said
# ---------------------------------------------------------------------------


def _record(console: _Console, session: Any, lines: list[dict[str, Any]]) -> Path:
    session_dir = Path(console.host.task_agents.session_dir_of(session))
    session_dir.mkdir(parents=True, exist_ok=True)
    path = session_dir / TRANSCRIPT_NAME
    path.write_text("".join(json.dumps(line) + "\n" for line in lines), encoding="utf-8")
    return path


def _message(role: str, content: Any) -> dict[str, Any]:
    return {"type": "message", "message": {"role": role, "content": content}}


async def test_words_are_looked_for_in_what_was_said_and_each_place_is_opened_whole(consoles, tmp_path):
    console = consoles(tmp_path)
    session_id = await _project(console)
    session = await console.host.ledger.get_session(session_id)
    _record(console, session, [
        _message("user", "plant the Beans by the wall"),
        _message("assistant", [{"type": "text", "text": "Looking for seeds."}, {"type": "toolCall", "name": "glob", "arguments": {"pattern": "beans/*"}}]),
        _message("assistant", "the tomatoes are in"),
        _message("assistant", "beans planted by the WALL"),
    ])
    found = (await console.get(f"/familiars/{session_id}/transcript/search", q="beans wall")).json()
    assert (found["success"], found["readable"], found["total"], found["whole"]) == (True, True, 2, True)
    # The latest first; every word, in any case.
    assert [hit["role"] for hit in found["hits"]] == ["assistant", "user"]
    assert "beans planted" in found["hits"][0]["excerpt"].lower()
    # The arguments of a call are looked through as well.
    called = (await console.get(f"/familiars/{session_id}/transcript/search", q="beans/*")).json()
    assert called["total"] == 1 and called["hits"][0]["role"] == "assistant"
    # A place is the one the transcript opens one entry at.
    whole = (await console.get(f"/familiars/{session_id}/transcript", at=found["hits"][1]["at"])).json()
    assert [item["text"] for item in whole["entries"]] == ["plant the Beans by the wall"]
    none = (await console.get(f"/familiars/{session_id}/transcript/search", q="cucumbers")).json()
    assert (none["total"], none["hits"]) == (0, [])


async def test_only_so_many_places_are_named_and_how_many_there_are_is_said(consoles, tmp_path, monkeypatch):
    console = consoles(tmp_path)
    session_id = await _project(console)
    session = await console.host.ledger.get_session(session_id)
    _record(console, session, [_message("assistant", f"bean number {index}") for index in range(7)])
    monkeypatch.setattr(settings, "CONSOLE_TRANSCRIPT_SEARCH_HITS", 3)
    found = (await console.get(f"/familiars/{session_id}/transcript/search", q="bean")).json()
    assert found["total"] == 7 and [hit["excerpt"] for hit in found["hits"]] == [
        "bean number 6", "bean number 5", "bean number 4",
    ]


async def test_of_a_long_record_only_its_end_is_looked_through_and_that_is_said(consoles, tmp_path, monkeypatch):
    console = consoles(tmp_path)
    session_id = await _project(console)
    session = await console.host.ledger.get_session(session_id)
    path = _record(console, session, [_message("assistant", f"bean {index} " + "x" * 200) for index in range(10)])
    line = len(path.read_bytes()) // 10
    # Three and a half lines' worth: the half line it begins in is not looked through.
    monkeypatch.setattr(settings, "CONSOLE_TRANSCRIPT_SEARCH_MAX_BYTES", line * 3 + line // 2)
    found = (await console.get(f"/familiars/{session_id}/transcript/search", q="bean")).json()
    assert found["whole"] is False and found["total"] == 3
    assert [hit["excerpt"].split()[1] for hit in found["hits"]] == ["9", "8", "7"]
    opened = (await console.get(f"/familiars/{session_id}/transcript", at=found["hits"][-1]["at"])).json()
    assert opened["entries"][0]["text"].startswith("bean 7 ")


async def test_a_line_being_written_is_not_looked_through(consoles, tmp_path):
    console = consoles(tmp_path)
    session_id = await _project(console)
    session = await console.host.ledger.get_session(session_id)
    path = _record(console, session, [_message("assistant", "bean one")])
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(_message("assistant", "bean two")))
    found = (await console.get(f"/familiars/{session_id}/transcript/search", q="bean")).json()
    assert [hit["excerpt"] for hit in found["hits"]] == ["bean one"]


@pytest.mark.parametrize("query", [None, "", "   ", "x" * 501])
async def test_no_words_to_look_for_is_refused(consoles, tmp_path, query):
    console = consoles(tmp_path)
    session_id = await _project(console)
    params = {} if query is None else {"q": query}
    assert _code(await console.get(f"/familiars/{session_id}/transcript/search", **params)) == (400, "bad_query")


async def test_a_search_says_why_there_is_nothing_to_look_through(consoles, tmp_path, monkeypatch):
    from nekopower.host import records

    console = consoles(tmp_path)
    session_id = await _project(console)
    assert _code(await console.get("/familiars/nobody/transcript/search", q="beans")) == (404, "no_familiar")
    empty = (await console.get(f"/familiars/{session_id}/transcript/search", q="beans")).json()
    assert (empty["readable"], empty["reason"], empty["hits"]) == (False, "empty", [])

    session = await console.host.ledger.get_session(session_id)
    session_dir = Path(console.host.task_agents.session_dir_of(session))
    session_dir.mkdir(parents=True, exist_ok=True)
    (session_dir / DSH_SESSION_NAME).write_text("{}", encoding="utf-8")
    in_dsh = (await console.get(f"/familiars/{session_id}/transcript/search", q="beans")).json()
    assert (in_dsh["readable"], in_dsh["reason"]) == (False, "dsh")

    _record(console, session, [_message("user", "plant the beans")])

    def held(*args: Any, **kwargs: Any) -> Any:
        raise records.RecordUnreadable("the record of this session cannot be read now (PermissionError)")

    monkeypatch.setattr(records, "search_page", held)
    assert _code(await console.get(f"/familiars/{session_id}/transcript/search", q="beans")) == (409, "record_unreadable")
