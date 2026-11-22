"""The routes the console pages use (``app/agent_server/api_console.py``), over a real host.

The router is built the way the agent process builds it, around a host whose
files live in a temporary directory and whose dsh is a stand-in.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi import FastAPI

from app.agent_server.api_console import TOKEN_HEADER, build_console_router
from nekoclaw.dsh import DshUnavailable

from .host_fakes import make_host

TOKEN = "token-of-this-launch"
HEADERS = {TOKEN_HEADER: TOKEN}


class Installation:
    """What the host reads and asks of a dsh installation."""

    def __init__(self) -> None:
        self.is_installed = False
        self.preference_now = "auto"
        self.removed_with: list[bool] = []
        self.fail_with: str | None = None
        self.hold: asyncio.Event | None = None
        self.last_error: str | None = None
        self.downloading = False

    def installed(self) -> bool:
        return self.is_installed

    def wanted(self) -> bool:
        return self.is_installed and self.preference_now == "auto"

    def preference(self) -> str:
        return self.preference_now

    def status(self) -> dict[str, Any]:
        return {
            "version": "0.2.1rc2", "supported": True, "installed": self.is_installed,
            "downloading": self.downloading, "last_error": self.last_error, "preference": self.preference_now,
        }

    def set_preference(self, value: str) -> None:
        if value not in ("auto", "builtin"):
            raise ValueError(value)
        self.preference_now = value

    def plugins(self) -> list[dict[str, Any]]:
        return [{"name": "a-plugin", "version": "1.0.0", "enabled": True}]

    def forget_session(self, session_dir: Path) -> bool:
        return True

    async def install(self, on_progress: Any = None) -> dict[str, Any]:
        self.downloading, self.last_error = True, None
        try:
            if self.hold is not None:
                await self.hold.wait()
            if self.fail_with is not None:
                self.last_error = self.fail_with
                raise DshUnavailable(self.fail_with)
            self.is_installed = True
        finally:
            self.downloading = False
        return self.status()

    async def uninstall(self, *, purge: bool = False) -> dict[str, Any]:
        self.removed_with.append(purge)
        self.is_installed = False
        return self.status()


async def a_model() -> dict[str, Any]:
    return {"model": "agent-model", "base_url": "http://models.test/v1", "api_key": "k"}


@pytest.fixture
async def console(tmp_path: Path) -> Any:
    """``(client, host, opened)``: a client of the routes, the host behind them, the directories shown."""
    installation = Installation()
    host = make_host(tmp_path, None, dsh=installation, task_agent_model=a_model)
    host.installation = installation
    opened: list[Path] = []

    async def get_host() -> Any:
        return host

    app = FastAPI()
    app.include_router(build_console_router(get_host, lambda: TOKEN, open_directory=opened.append))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://agent.test") as client:
        yield client, host, opened
    await host.shutdown()


async def _until(client: httpx.AsyncClient, wanted: Any) -> dict[str, Any]:
    for _ in range(200):
        dsh = (await client.get("/console/runtime", headers=HEADERS)).json()["dsh"]
        if wanted(dsh):
            return dsh
        await asyncio.sleep(0.01)
    raise AssertionError(f"dsh stayed {dsh}")


# ---------------------------------------------------------------------------
# Who may ask
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("headers", [{}, {TOKEN_HEADER: "another"}, {TOKEN_HEADER: ""}])
async def test_no_route_answers_without_the_token_of_this_launch(console, headers):
    client, host, opened = console
    assert (await client.get("/console/runtime", headers=headers)).status_code == 403
    assert (await client.get("/console/permissions", headers=headers)).status_code == 403
    refused = await client.post("/console/permissions", headers=headers, json={"category": "run_command", "mode": "allow"})
    assert refused.status_code == 403 and refused.json()["detail"] == {"code": "forbidden"}
    assert (await client.post("/console/runtime/dsh/install", headers=headers)).status_code == 403
    assert (await client.post("/console/places/open", headers=headers, json={"key": "space"})).status_code == 403
    # Nothing was done on the way to the refusal.
    assert await host.permission_store.mode("run_command") == "first_ask"
    assert not host.installation.is_installed and opened == []


async def test_a_launch_without_a_token_answers_nobody(tmp_path):
    host = make_host(tmp_path, None, dsh=Installation())

    async def get_host() -> Any:
        return host

    app = FastAPI()
    app.include_router(build_console_router(get_host, lambda: ""))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://agent.test") as client:
        assert (await client.get("/console/runtime", headers={TOKEN_HEADER: ""})).status_code == 403
    await host.shutdown()


# ---------------------------------------------------------------------------
# The runtime
# ---------------------------------------------------------------------------


async def test_the_runtime_page_reads_dsh_and_its_plugins_and_sets_the_preference(console):
    client, host, _ = console
    seen = (await client.get("/console/runtime", headers=HEADERS)).json()
    assert seen["success"] is True and seen["dsh"]["installed"] is False and seen["dsh"]["preference"] == "auto"
    assert seen["dsh"]["sessions"] == 0 and seen["dsh"]["last_error_code"] is None
    assert seen["plugins"] == [{"name": "a-plugin", "version": "1.0.0", "enabled": True}]

    changed = await client.post("/console/runtime/preference", headers=HEADERS, json={"preference": "builtin"})
    assert changed.json()["dsh"]["preference"] == "builtin" and host.installation.preference_now == "builtin"
    refused = await client.post("/console/runtime/preference", headers=HEADERS, json={"preference": "dsh"})
    assert refused.status_code == 400 and refused.json()["detail"]["code"] == "bad_preference"
    assert host.installation.preference_now == "builtin"


async def test_installing_dsh_runs_behind_the_request_and_the_status_says_how_it_went(console):
    client, host, _ = console
    host.installation.hold = asyncio.Event()
    started = (await client.post("/console/runtime/dsh/install", headers=HEADERS)).json()
    assert started["dsh"]["downloading"] is True and started["dsh"]["installed"] is False
    # A second press while it runs starts nothing more, and it cannot be removed meanwhile.
    await client.post("/console/runtime/dsh/install", headers=HEADERS)
    busy = await client.post("/console/runtime/dsh/uninstall", headers=HEADERS, json={})
    assert busy.status_code == 409 and busy.json()["detail"]["code"] == "dsh_downloading"
    host.installation.hold.set()
    done = await _until(client, lambda dsh: dsh["installed"])
    assert done["downloading"] is False and done["last_error"] is None


async def test_a_download_that_fails_is_told_with_a_code_the_page_has_words_for(console):
    client, host, _ = console
    host.installation.fail_with = "dsh could not be downloaded (https://files.pythonhosted.org/packages: ConnectTimeout)"
    await client.post("/console/runtime/dsh/install", headers=HEADERS)
    failed = await _until(client, lambda dsh: dsh["last_error"])
    assert failed["installed"] is False and failed["last_error_code"] == "dsh_download"
    assert "ConnectTimeout" in failed["last_error"]
    # Trying again clears what the last try left.
    host.installation.fail_with = None
    await client.post("/console/runtime/dsh/install", headers=HEADERS)
    assert (await _until(client, lambda dsh: dsh["installed"]))["last_error_code"] is None


async def test_removing_dsh_passes_on_whether_what_it_kept_goes_too(console):
    client, host, _ = console
    host.installation.is_installed = True
    kept = await client.post("/console/runtime/dsh/uninstall", headers=HEADERS, json={})
    assert kept.json()["dsh"]["installed"] is False
    host.installation.is_installed = True
    await client.post("/console/runtime/dsh/uninstall", headers=HEADERS, json={"purge": True})
    # Only ``true`` purges: a page that sends something else removes the program alone.
    host.installation.is_installed = True
    await client.post("/console/runtime/dsh/uninstall", headers=HEADERS, json={"purge": "yes"})
    assert host.installation.removed_with == [False, True, False]


# ---------------------------------------------------------------------------
# Permissions
# ---------------------------------------------------------------------------


async def test_the_permission_table_shows_each_category_and_what_auto_comes_to(console):
    client, _, _ = console
    seen = (await client.get("/console/permissions", headers=HEADERS)).json()
    rows = {row["category"]: row for row in seen["categories"]}
    assert list(rows) == [
        "workdir_files", "outside_read", "outside_write", "run_command",
        "keyboard_mouse", "browser", "plugin_entry", "managed_delivery", "other",
    ]
    assert all(row["mode"] == "auto" for row in rows.values()) and seen["unreadable"] is False
    assert rows["run_command"]["auto"] == "first_ask" and rows["workdir_files"]["auto"] == "allow"
    # The tools of dsh plugins: confirmed at install, not asked for.
    assert rows["other"]["auto"] == "allow"
    assert seen["modes"] == ["auto", "first_ask", "always_ask", "allow", "deny"]


async def test_a_permission_is_set_and_a_change_on_an_older_view_is_refused(console):
    client, host, _ = console
    version = (await client.get("/console/permissions", headers=HEADERS)).json()["version"]
    changed = await client.post(
        "/console/permissions", headers=HEADERS,
        json={"category": "run_command", "mode": "always_ask", "expected_version": version},
    )
    rows = {row["category"]: row["mode"] for row in changed.json()["categories"]}
    assert rows["run_command"] == "always_ask" and changed.json()["version"] != version
    assert await host.permission_store.mode("run_command") == "always_ask"

    stale = await client.post(
        "/console/permissions", headers=HEADERS,
        json={"category": "run_command", "mode": "allow", "expected_version": version},
    )
    assert stale.status_code == 409 and stale.json()["detail"]["code"] == "stale"
    assert await host.permission_store.mode("run_command") == "always_ask"


@pytest.mark.parametrize(
    ("payload", "code"),
    [
        ({"category": "mcp_server", "mode": "allow"}, "bad_category"),
        ({"category": "something_new", "mode": "allow"}, "bad_category"),
        ({"category": "run_command", "mode": "yes"}, "bad_mode"),
        ({"category": "run_command", "mode": "allow", "expected_version": "1"}, "bad_version"),
        ({"category": "run_command", "mode": "allow", "expected_version": True}, "bad_version"),
    ],
)
async def test_what_is_no_setting_of_the_table_is_refused_and_changes_nothing(console, payload, code):
    client, host, _ = console
    refused = await client.post("/console/permissions", headers=HEADERS, json=payload)
    assert refused.status_code == 400 and refused.json()["detail"]["code"] == code
    assert (await host.permission_store.snapshot())["categories"]["run_command"] == "auto"


async def test_a_permission_file_that_cannot_be_read_is_shown_as_such_and_not_overwritten(console):
    client, host, _ = console
    path = host.paths.permissions_json()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{ not json", encoding="utf-8")
    assert (await client.get("/console/permissions", headers=HEADERS)).json()["unreadable"] is True
    refused = await client.post("/console/permissions", headers=HEADERS, json={"category": "run_command", "mode": "allow"})
    assert refused.status_code == 409 and refused.json()["detail"]["code"] == "permissions_unreadable"
    assert path.read_text(encoding="utf-8") == "{ not json"


# ---------------------------------------------------------------------------
# The model's window
# ---------------------------------------------------------------------------


async def test_the_window_of_the_model_is_read_and_changed_and_goes_back_to_the_setting(console):
    from config.nekoclaw_settings import TASK_AGENT_IDLE_COMPACT_AFTER_SECONDS

    client, _, _ = console
    window = (await client.get("/console/window", headers=HEADERS)).json()["window"]
    assert window["model"] == "agent-model" and window["set"] is None
    assert window["idle_compact_after"] == TASK_AGENT_IDLE_COMPACT_AFTER_SECONDS and window["idle_compact_after_set"] is None

    changed = (await client.post(
        "/console/window", headers=HEADERS,
        json={"context_window": 120_000, "compact_at": 90_000, "idle_compact_after": 3600},
    )).json()["window"]
    assert (changed["window"], changed["set"]) == (120_000, 120_000)
    assert (changed["compact_at"], changed["compact_at_set"]) == (90_000, 90_000)
    assert (changed["idle_compact_after"], changed["idle_compact_after_set"]) == (3600.0, 3600.0)

    # One that is not named stays; ``null`` goes back to the setting.
    back = (await client.post("/console/window", headers=HEADERS, json={"idle_compact_after": None})).json()["window"]
    assert back["idle_compact_after_set"] is None and back["idle_compact_after"] == TASK_AGENT_IDLE_COMPACT_AFTER_SECONDS
    assert back["set"] == 120_000 and back["compact_at_set"] == 90_000


async def test_a_value_out_of_range_changes_none_of_what_was_sent_with_it(console):
    client, _, _ = console
    refused = await client.post(
        "/console/window", headers=HEADERS, json={"context_window": 120_000, "idle_compact_after": 5},
    )
    assert refused.status_code == 400 and refused.json()["detail"]["code"] == "out_of_range"
    window = (await client.get("/console/window", headers=HEADERS)).json()["window"]
    assert window["set"] is None and window["idle_compact_after_set"] is None


@pytest.mark.parametrize(
    ("payload", "code"),
    [
        ({}, "nothing_to_change"),
        ({"something": 1}, "nothing_to_change"),
        ({"context_window": "big"}, "bad_number"),
        ({"compact_at": True}, "bad_number"),
    ],
)
async def test_what_is_no_number_for_the_window_is_refused(console, payload, code):
    client, _, _ = console
    refused = await client.post("/console/window", headers=HEADERS, json=payload)
    assert refused.status_code == 400 and refused.json()["detail"]["code"] == code


async def test_without_a_model_there_is_no_window_to_show_or_change(tmp_path):
    host = make_host(tmp_path, None, dsh=Installation())

    async def get_host() -> Any:
        return host

    app = FastAPI()
    app.include_router(build_console_router(get_host, lambda: TOKEN))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://agent.test") as client:
        assert (await client.get("/console/window", headers=HEADERS)).json() == {"success": True, "window": None}
        refused = await client.post("/console/window", headers=HEADERS, json={"compact_at": 90_000})
        assert refused.status_code == 409 and refused.json()["detail"]["code"] == "no_model"
    await host.shutdown()


# ---------------------------------------------------------------------------
# Where things are
# ---------------------------------------------------------------------------


async def test_the_places_are_listed_with_what_they_hold_and_only_they_are_opened(console):
    client, host, opened = console
    space = host.paths.space().root
    (space / "memory").mkdir(parents=True)
    (space / "memory" / "note.md").write_text("12345", encoding="utf-8")
    (space / "AGENTS.md").write_text("123", encoding="utf-8")

    places = {place["key"]: place for place in (await client.get("/console/places", headers=HEADERS)).json()["places"]}
    assert list(places) == ["task_agents", "space", "dsh", "browser_profile"]
    assert places["space"] == {"key": "space", "path": str(space), "exists": True, "bytes": 8, "bytes_whole": True}
    assert places["dsh"]["exists"] is False and places["dsh"]["bytes"] is None

    assert (await client.post("/console/places/open", headers=HEADERS, json={"key": "space"})).json() == {"success": True}
    assert opened == [space]
    missing = await client.post("/console/places/open", headers=HEADERS, json={"key": "dsh"})
    assert missing.status_code == 404 and missing.json()["detail"]["code"] == "place_missing"
    # A path is not a key: nothing but the listed directories is shown.
    for key in (str(space.parent), "../", None, 3):
        refused = await client.post("/console/places/open", headers=HEADERS, json={"key": key})
        assert refused.status_code == 400 and refused.json()["detail"]["code"] == "bad_place"
    assert opened == [space]


def test_the_sizes_stop_being_added_up_when_their_time_is_over(tmp_path, monkeypatch):
    from app.agent_server import api_console

    for name in ("a", "b"):
        (tmp_path / name).mkdir()
        (tmp_path / name / "file").write_text("x" * 10, encoding="utf-8")
    assert api_console._measure([tmp_path / "a", tmp_path / "missing", tmp_path / "b"], 30.0) == [
        (10, True), (None, True), (10, True),
    ]
    # With no time left, what there is of a directory is not passed off as its size.
    assert api_console._measure([tmp_path / "a"], -1.0) == [(0, False)]


def test_the_module_keeps_clear_of_the_agent_process():
    """It is imported by tests and by the agent process alike: nothing of that process at import."""
    source = (Path(__file__).resolve().parents[3] / "app" / "agent_server" / "api_console.py").read_text(encoding="utf-8")
    assert "api_shared" not in source and "from ._shared" not in source
    assert json.dumps("api_runtime") not in source


# ---------------------------------------------------------------------------
# The task agent space
# ---------------------------------------------------------------------------


async def test_the_rules_are_read_and_written_for_the_space_and_for_a_project_apart(console):
    client, host, _ = console
    assert (await client.get("/console/space/rules", headers=HEADERS)).json() == {"success": True, "text": ""}
    written = await client.put("/console/space/rules", headers=HEADERS, json={"text": "- answer in Chinese\n"})
    assert written.json()["text"] == "- answer in Chinese\n"
    await client.put("/console/space/rules", headers=HEADERS, json={"text": "- use Decimal\n", "project": "ledger-tool"})

    assert await host.space.read_rules() == "- answer in Chinese\n"
    assert await host.space.read_rules("ledger-tool") == "- use Decimal\n"
    of_project = await client.get("/console/space/rules", headers=HEADERS, params={"project": "ledger-tool"})
    assert of_project.json()["text"] == "- use Decimal\n"


@pytest.mark.parametrize(
    ("payload", "code"),
    [
        ({"text": 3}, "bad_text"),
        ({}, "bad_text"),
        ({"text": "x", "project": ""}, "bad_name"),
        ({"text": "x", "project": 7}, "bad_name"),
        ({"text": "x", "project": "../outside"}, "bad_name"),
    ],
)
async def test_rules_that_are_no_text_or_name_no_project_are_refused(console, payload, code):
    client, host, _ = console
    refused = await client.put("/console/space/rules", headers=HEADERS, json=payload)
    assert refused.status_code == 400 and refused.json()["detail"]["code"] == code
    assert await host.space.read_rules() == ""


async def test_an_entry_is_kept_listed_read_changed_and_removed(console):
    client, host, _ = console
    kept = await client.post(
        "/console/space/entries", headers=HEADERS,
        json={"title": "Python lives in D:/py312", "content": "Use D:/py312/python.exe.", "description": "where python is"},
    )
    entry = kept.json()["entry"]
    assert kept.status_code == 200 and entry["title"] == "Python lives in D:/py312" and entry["type"] == "lesson"

    listed = (await client.get("/console/space/entries", headers=HEADERS)).json()["entries"]
    assert [item["id"] for item in listed] == [entry["id"]] and listed[0]["content"] == ""
    read = (await client.get(f"/console/space/entries/{entry['id']}", headers=HEADERS)).json()["entry"]
    assert "D:/py312/python.exe" in read["content"]
    assert "Python lives in D:/py312" in (await client.get("/console/space/index", headers=HEADERS)).json()["text"]

    changed = await client.patch(
        f"/console/space/entries/{entry['id']}", headers=HEADERS, json={"title": "Where Python is", "kind": "reference"},
    )
    assert changed.json()["entry"]["title"] == "Where Python is" and changed.json()["entry"]["type"] == "reference"
    assert (await host.space.read_entry(entry["id"])).title == "Where Python is"

    assert (await client.delete(f"/console/space/entries/{entry['id']}", headers=HEADERS)).json() == {"success": True}
    assert (await client.get("/console/space/entries", headers=HEADERS)).json()["entries"] == []
    # What the user removed is remembered, so that it is not kept again.
    removed = (await client.get("/console/space/removed", headers=HEADERS)).json()["removed"]
    assert len(removed) == 1
    for request in (
        client.get(f"/console/space/entries/{entry['id']}", headers=HEADERS),
        client.patch(f"/console/space/entries/{entry['id']}", headers=HEADERS, json={"title": "again"}),
        client.delete(f"/console/space/entries/{entry['id']}", headers=HEADERS),
    ):
        gone = await request
        assert gone.status_code == 404 and gone.json()["detail"]["code"] == "no_entry"


@pytest.mark.parametrize(
    ("payload", "code"),
    [
        ({"title": "t"}, "bad_text"),
        ({"content": "c"}, "bad_text"),
        ({"title": "  ", "content": "c"}, "bad_text"),
        ({"title": "t", "content": "c", "kind": "skill"}, "bad_kind"),
        ({"title": "t", "content": "c", "project": ""}, "bad_name"),
        ({"title": "t", "content": "x" * 200_001}, "bad_text"),
    ],
)
async def test_an_entry_that_is_not_one_is_refused_and_nothing_is_kept(console, payload, code):
    client, host, _ = console
    refused = await client.post("/console/space/entries", headers=HEADERS, json=payload)
    assert refused.status_code == 400 and refused.json()["detail"]["code"] == code
    assert await host.space.list_entries() == []


async def test_changing_an_entry_needs_something_to_change_and_a_title_that_says_something(console):
    client, host, _ = console
    entry, _new = await host.space.file_entry(None, title="A note", content="What it says.")
    for payload, code in (({}, "nothing_to_change"), ({"title": " "}, "bad_text"), ({"kind": "skill"}, "bad_kind")):
        refused = await client.patch(f"/console/space/entries/{entry.id}", headers=HEADERS, json=payload)
        assert refused.status_code == 400 and refused.json()["detail"]["code"] == code
    assert (await host.space.read_entry(entry.id)).title == "A note"


async def test_projects_and_reviews_are_listed_and_what_is_not_there_is_said_to_be_missing(console):
    client, _, _ = console
    assert (await client.get("/console/space/projects", headers=HEADERS)).json() == {"success": True, "projects": []}
    assert (await client.get("/console/space/reviews", headers=HEADERS)).json() == {"success": True, "reviews": []}
    for request in (
        client.get("/console/space/reviews/c-missing", headers=HEADERS),
        client.delete("/console/space/reviews/c-missing", headers=HEADERS),
    ):
        missing = await request
        assert missing.status_code == 404 and missing.json()["detail"]["code"] == "no_review"


# ---------------------------------------------------------------------------
# How the user wants tasks done
# ---------------------------------------------------------------------------


async def test_a_preference_is_listed_changed_switched_off_and_forgotten(console):
    client, host, _ = console
    stated = await host.preferences.state("Tell me the result when it is done", kind="report", lanlan_name="Lan")
    listed = (await client.get("/console/preferences", headers=HEADERS)).json()["preferences"]
    assert [(item["id"], item["text"], item["active"], item["origin"]) for item in listed] == [
        (stated.id, "Tell me the result when it is done", True, "stated"),
    ]

    changed = await client.patch(
        f"/console/preferences/{stated.id}", headers=HEADERS, json={"text": "Report in one line", "active": False},
    )
    preference = changed.json()["preference"]
    assert (preference["text"], preference["active"], preference["origin"]) == ("Report in one line", False, "console")
    assert [item.text for item in await host.preferences.list(active_only=True)] == []

    for payload, code in (({}, "nothing_to_change"), ({"active": "no"}, "bad_flag"), ({"text": "  "}, "bad_text")):
        refused = await client.patch(f"/console/preferences/{stated.id}", headers=HEADERS, json=payload)
        assert refused.status_code == 400 and refused.json()["detail"]["code"] == code

    assert (await client.delete(f"/console/preferences/{stated.id}", headers=HEADERS)).json() == {"success": True}
    assert await host.preferences.list() == []
    for request in (
        client.patch(f"/console/preferences/{stated.id}", headers=HEADERS, json={"active": True}),
        client.delete(f"/console/preferences/{stated.id}", headers=HEADERS),
    ):
        gone = await request
        assert gone.status_code == 404 and gone.json()["detail"]["code"] == "no_preference"


# ---------------------------------------------------------------------------
# The switches and the browser settings of the agent process
# ---------------------------------------------------------------------------


async def test_a_host_on_its_own_has_no_switches_to_show(console):
    client, _, _ = console
    for request in (
        client.get("/console/switches", headers=HEADERS),
        client.get("/console/browser", headers=HEADERS),
        client.post("/console/browser", headers=HEADERS, json={"mode": "attach"}),
    ):
        missing = await request
        assert missing.status_code == 404 and missing.json()["detail"]["code"] == "no_switches"


async def test_the_switches_and_the_browser_settings_are_those_the_agent_process_gives(tmp_path):
    from fastapi import HTTPException

    host = make_host(tmp_path, None, dsh=Installation())
    written: list[dict[str, Any]] = []

    async def get_host() -> Any:
        return host

    async def switches() -> dict[str, Any]:
        return {"enabled": True, "flags": {"browser_use_enabled": True}, "capabilities": {"browser_use": {"ready": True}}}

    async def read_browser() -> dict[str, Any]:
        return {"settings": {"mode": "own_profile"}, "modes": ["own_profile", "attach"], "browsers": []}

    async def write_browser(payload: dict[str, Any]) -> dict[str, Any]:
        if payload.get("mode") not in ("own_profile", "attach"):
            raise HTTPException(400, "unknown mode")
        written.append(payload)
        return {"success": True, "settings": {"mode": payload["mode"]}, "modes": ["own_profile", "attach"], "browsers": []}

    app = FastAPI()
    app.include_router(build_console_router(
        get_host, lambda: TOKEN, switches=switches, read_browser=read_browser, write_browser=write_browser,
    ))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://agent.test") as client:
        assert (await client.get("/console/switches")).status_code == 403
        seen = (await client.get("/console/switches", headers=HEADERS)).json()
        assert seen == {"success": True, "switches": {
            "enabled": True, "flags": {"browser_use_enabled": True}, "capabilities": {"browser_use": {"ready": True}},
        }}
        browser = (await client.get("/console/browser", headers=HEADERS)).json()
        assert browser["success"] is True and browser["settings"] == {"mode": "own_profile"}

        changed = await client.post("/console/browser", headers=HEADERS, json={"mode": "attach"})
        assert changed.json()["settings"] == {"mode": "attach"} and written == [{"mode": "attach"}]
        # What the agent process refuses comes back with a code and its reason.
        refused = await client.post("/console/browser", headers=HEADERS, json={"mode": "incognito"})
        assert refused.status_code == 400
        assert refused.json()["detail"] == {"code": "bad_browser_setting", "message": "unknown mode"}
        assert written == [{"mode": "attach"}]
    await host.shutdown()


async def test_summing_up_idle_sessions_is_switched_off_and_on_from_the_page(console):
    client, host, _ = console
    assert (await client.get("/console/window", headers=HEADERS)).json()["window"]["idle_compact_on"] is True
    off = (await client.post("/console/window", headers=HEADERS, json={"idle_compact_on": False})).json()["window"]
    assert off["idle_compact_on"] is False
    model = await a_model()
    assert await host.model_capabilities.idle_compact_on(model["base_url"], model["model"]) is False
    # With the time, in one go; and a time that is refused leaves the switch as it was.
    refused = await client.post(
        "/console/window", headers=HEADERS, json={"idle_compact_on": True, "idle_compact_after": 5},
    )
    assert refused.status_code == 400 and refused.json()["detail"]["code"] == "out_of_range"
    assert (await client.get("/console/window", headers=HEADERS)).json()["window"]["idle_compact_on"] is False
    on = (await client.post(
        "/console/window", headers=HEADERS, json={"idle_compact_on": True, "idle_compact_after": 7200},
    )).json()["window"]
    assert (on["idle_compact_on"], on["idle_compact_after_set"]) == (True, 7200.0)
    for not_a_switch in ("off", 0, None):
        bad = await client.post("/console/window", headers=HEADERS, json={"idle_compact_on": not_a_switch})
        assert bad.status_code == 400 and bad.json()["detail"]["code"] == "bad_flag"


async def test_the_page_changes_where_a_project_works_and_is_told_why_when_it_cannot(console, tmp_path, monkeypatch):
    client, host, _ = console
    await host.space.touch_project("Ledger tool")
    elsewhere = tmp_path.parent / f"{tmp_path.name}-code"
    elsewhere.mkdir()

    changed = await client.post(
        "/console/space/projects/workdir", headers=HEADERS, json={"project": "Ledger tool", "workdir": str(elsewhere)},
    )
    assert changed.status_code == 200 and changed.json()["project"]["workdir"] == str(elsewhere)
    assert (await host.space.get_project("Ledger tool")).workdir == str(elsewhere)

    for payload, status, code in (
        ({"project": "Ledger tool", "workdir": "relative/path"}, 400, "not_absolute"),
        ({"project": "Ledger tool", "workdir": str(tmp_path / "missing")}, 400, "not_a_directory"),
        ({"project": "Ledger tool", "workdir": str(host.paths.root().parent)}, 400, "host_directory"),
        ({"project": "Ledger tool", "workdir": 3}, 400, "bad_name"),
        ({"workdir": str(elsewhere)}, 400, "bad_name"),
        ({"project": "../outside", "workdir": str(elsewhere)}, 400, "bad_name"),
        ({"project": "No such project", "workdir": str(elsewhere)}, 404, "no_project"),
    ):
        refused = await client.post("/console/space/projects/workdir", headers=HEADERS, json=payload)
        assert (refused.status_code, refused.json()["detail"]["code"]) == (status, code), payload
    assert (await host.space.get_project("Ledger tool")).workdir == str(elsewhere)

    async def at_work(project: str, lanlan_name: Any = None) -> str:
        return "at_work"

    monkeypatch.setattr(host, "_project_busy", at_work)
    again = tmp_path.parent / f"{tmp_path.name}-again"
    again.mkdir()
    busy = await client.post(
        "/console/space/projects/workdir", headers=HEADERS, json={"project": "Ledger tool", "workdir": str(again)},
    )
    assert busy.status_code == 409 and busy.json()["detail"] == {"code": "project_busy", "reason": "at_work"}
    assert (await host.space.get_project("Ledger tool")).workdir == str(elsewhere)


async def test_the_dialog_for_choosing_a_folder_gives_what_was_chosen_and_changes_nothing(tmp_path):
    host = make_host(tmp_path, None, dsh=Installation())
    await host.space.touch_project("Ledger tool", workdir=str(tmp_path))
    asked: list[str] = []
    answer: list[Any] = [str(tmp_path / "code")]

    def pick(start: str) -> str | None:
        asked.append(start)
        if isinstance(answer[0], Exception):
            raise answer[0]
        return answer[0]

    async def get_host() -> Any:
        return host

    app = FastAPI()
    app.include_router(build_console_router(get_host, lambda: TOKEN, pick_directory=pick))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://agent.test") as client:
        assert (await client.post("/console/space/pick-directory")).status_code == 403
        chosen = await client.post("/console/space/pick-directory", headers=HEADERS, json={"start": str(tmp_path)})
        assert chosen.json() == {"success": True, "path": str(tmp_path / "code")} and asked == [str(tmp_path)]
        # Closed without choosing; and a start that is no path is not passed on as one.
        answer[0] = None
        closed = await client.post("/console/space/pick-directory", headers=HEADERS, json={"start": 3})
        assert closed.json() == {"success": True, "cancelled": True} and asked[-1] == ""
        answer[0] = RuntimeError("no dialog on this machine")
        failed = await client.post("/console/space/pick-directory", headers=HEADERS)
        assert failed.status_code == 500 and failed.json()["detail"] == {"code": "picker_unavailable"}
    assert (await host.space.get_project("Ledger tool")).workdir == str(tmp_path)
    await host.shutdown()


# ---------------------------------------------------------------------------
# The task agents at a glance, making one, talking to one
# ---------------------------------------------------------------------------


@pytest.fixture
async def familiars(tmp_path: Path) -> Any:
    """``(client, host)`` over a host whose summary model fills in forms, with a character the user is with."""
    from tests.unit.nekoclaw.host_fakes import fake_model
    from tests.unit.nekoclaw.test_host_board import DraftLLM

    host = make_host(tmp_path, None, dsh=Installation(), task_agent_model=fake_model, decision_llm=DraftLLM())

    async def get_host() -> Any:
        return host

    async def character() -> str | None:
        return "Lan"

    app = FastAPI()
    app.include_router(build_console_router(get_host, lambda: TOKEN, character=character))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://agent.test") as client:
        yield client, host
    await host.shutdown()


async def test_a_task_agent_is_made_with_what_the_user_filled_in_and_shows_on_the_board(familiars, tmp_path):
    client, host = familiars
    assert (await client.get("/console/board")).status_code == 403
    empty = (await client.get("/console/board", headers=HEADERS)).json()
    assert empty["success"] is True and empty["projects"] == [] and empty["loose"] == []
    names = [item["name"] for item in (await client.get("/console/templates", headers=HEADERS)).json()["templates"]]
    assert "boss" in names and "scout" in names

    code = tmp_path.parent / f"{tmp_path.name}-ledger"
    code.mkdir()
    made = await client.post("/console/familiars", headers=HEADERS, json={
        "name": "Ledger tool", "preset": "boss", "title": "Keeper of the books",
        "duty": "Keeps the ledger tool working", "description": "A small bookkeeping program",
        "workdir": str(code), "rules": "- Use Decimal for money.\n",
    })
    assert made.status_code == 200, made.text
    body = made.json()
    assert body["project"]["name"] == "Ledger tool" and body["project"]["workdir"] == str(code)

    project = await host.space.get_project("Ledger tool")
    assert (project.description, project.description_by, project.commissions) == ("A small bookkeeping program", "user", 0)
    assert await host.space.read_rules("Ledger tool") == "- Use Decimal for money.\n"
    session = await host.ledger.get_session(body["session_id"])
    assert (session.kind, session.lanlan_name, session.project, session.workdir) == ("project", "Lan", "Ledger tool", str(code))
    assert (session.preset, session.title, session.title_by, session.duty, session.duty_by) == (
        "boss", "Keeper of the books", "user", "Keeps the ledger tool working", "user",
    )

    board = (await client.get("/console/board", headers=HEADERS)).json()
    (shown,) = board["projects"]
    (card,) = shown["members"]
    assert (shown["name"], card["id"], card["leader"], card["title"], card["state"]) == (
        "Ledger tool", body["session_id"], True, "Keeper of the books", "asleep",
    )
    # Nothing was started for it.
    assert card["commission"] is None
    unchanged = (await client.get("/console/board", headers=HEADERS, params={"known_revision": board["revision"]})).json()
    assert unchanged == {"success": True, "revision": board["revision"], "unchanged": True}
    of_one = (await client.get("/console/board", headers=HEADERS, params={"project": "Ledger tool"})).json()
    assert [item["name"] for item in of_one["projects"]] == ["Ledger tool"]


async def test_a_task_agent_is_not_made_of_what_will_not_do_and_nothing_is_left_behind(familiars, tmp_path):
    client, host = familiars
    await host.space.touch_project("Taken")
    for payload, code in (
        ({"name": "taken"}, "name_taken"),
        ({"name": "Taken"}, "name_taken"),
        ({"name": ""}, "bad_name"),
        ({"name": "../outside"}, "bad_name"),
        ({"name": "_daily"}, "bad_name"),
        ({"name": "New one", "preset": "wizard"}, "bad_preset"),
        ({"name": "New one", "workdir": "relative"}, "not_absolute"),
        ({"name": "New one", "workdir": str(tmp_path / "missing")}, "not_a_directory"),
        ({"name": "New one", "workdir": str(host.paths.root().parent)}, "host_directory"),
        ({"name": "New one", "workdir": 3}, "bad_text"),
        ({"name": "New one", "duty": 3}, "bad_text"),
    ):
        refused = await client.post("/console/familiars", headers=HEADERS, json=payload)
        assert (refused.status_code, refused.json()["detail"]["code"]) == (400, code), payload
    assert [project.name for project in await host.space.list_projects()] == ["Taken"]
    assert await host.ledger.find_sessions() == []


async def test_without_a_character_to_make_it_for_no_task_agent_is_made(tmp_path):
    host = make_host(tmp_path, None, dsh=Installation())

    async def get_host() -> Any:
        return host

    app = FastAPI()
    app.include_router(build_console_router(get_host, lambda: TOKEN))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://agent.test") as client:
        refused = await client.post("/console/familiars", headers=HEADERS, json={"name": "New one"})
        assert refused.status_code == 400 and refused.json()["detail"]["code"] == "no_character"
        # Named by the page, it is made for that character.
        made = await client.post("/console/familiars", headers=HEADERS, json={"name": "New one", "lanlan_name": "Mei"})
        assert made.status_code == 200
        assert (await host.ledger.get_session(made.json()["session_id"])).lanlan_name == "Mei"
    await host.shutdown()


async def test_the_form_of_a_new_task_agent_is_filled_in_and_a_failure_says_which(familiars):
    from tests.unit.nekoclaw.test_host_board import FORM

    client, host = familiars
    host._decision_llm.answer = dict(FORM)
    filled = await client.post("/console/familiars/draft", headers=HEADERS, json={"text": "a task agent for the monthly reports"})
    assert filled.status_code == 200 and filled.json()["draft"] == {**FORM, "preset": "boss"}
    assert len(host._decision_llm.calls) == 1
    # Nothing was made by filling it in.
    assert await host.space.list_projects() == [] and await host.ledger.find_sessions() == []

    empty = await client.post("/console/familiars/draft", headers=HEADERS, json={"text": "   "})
    assert empty.status_code == 400 and empty.json()["detail"] == {"code": "draft_failed", "reason": "empty"}
    assert len(host._decision_llm.calls) == 1
    host._decision_llm.answer = RuntimeError("the model is away")
    failed = await client.post("/console/familiars/draft", headers=HEADERS, json={"text": "a task agent for the reports"})
    assert failed.status_code == 502 and failed.json()["detail"]["code"] == "draft_failed"
    assert failed.json()["detail"]["reason"] in ("failed", "timeout", "unreadable", "incomplete", "no_model")
    for payload in ({}, {"text": 3}, {"text": "x", "workdir": 3}):
        bad = await client.post("/console/familiars/draft", headers=HEADERS, json=payload)
        assert bad.status_code == 400 and bad.json()["detail"]["code"] == "bad_text"


async def test_the_user_writes_a_card_and_a_project_description_which_stay_the_users(familiars):
    client, host = familiars
    made = (await client.post("/console/familiars", headers=HEADERS, json={"name": "Ledger tool"})).json()
    session_id = made["session_id"]

    written = await client.patch(
        f"/console/familiars/{session_id}/card", headers=HEADERS, json={"title": "Bookkeeper", "summary": "Set up, nothing done yet."},
    )
    assert written.status_code == 200 and sorted(written.json()["written"]) == ["summary", "title"]
    session = await host.ledger.get_session(session_id)
    assert (session.title, session.title_by, session.summary, session.summary_by) == (
        "Bookkeeper", "user", "Set up, nothing done yet.", "user",
    )
    # A review does not write over what the user wrote.
    _same, by_review = await host.ledger.write_card(session_id, by="review", title="Something else")
    assert by_review == () and (await host.ledger.get_session(session_id)).title == "Bookkeeper"

    assert (await client.patch(f"/console/familiars/{session_id}/card", headers=HEADERS, json={})).json()["detail"]["code"] == "nothing_to_change"
    missing = await client.patch("/console/familiars/no-such-session/card", headers=HEADERS, json={"title": "x"})
    assert missing.status_code == 404 and missing.json()["detail"]["code"] == "no_familiar"

    described = await client.post(
        "/console/space/projects/description", headers=HEADERS, json={"project": "Ledger tool", "description": "Keeps the books"},
    )
    assert described.json()["project"]["description"] == "Keeps the books"
    assert (await host.space.get_project("Ledger tool")).description_by == "user"
    for payload, status, code in (
        ({"project": "Ledger tool", "description": "  "}, 400, "bad_text"),
        ({"description": "x"}, 400, "bad_text"),
        ({"project": "No such project", "description": "x"}, 404, "no_project"),
    ):
        refused = await client.post("/console/space/projects/description", headers=HEADERS, json=payload)
        assert (refused.status_code, refused.json()["detail"]["code"]) == (status, code), payload


async def test_the_user_talks_to_a_task_agent_at_work_and_stops_it(tmp_path):
    from tests.unit.nekoclaw.fakes import ScriptedLLM
    from tests.unit.nekoclaw.host_fakes import FakeDecisionLLM, fake_model, scripted_runtime, wait_status
    from tests.unit.nekoclaw.test_host_session_features import _gate, _report

    release, wait = _gate()
    model = ScriptedLLM([[wait, *_report("ready", "sorted")], _report("ready", "sorted again")])
    host = make_host(
        tmp_path, None, dsh=Installation(), task_agent_model=fake_model, decision_llm=FakeDecisionLLM(),
        runtime_factory=scripted_runtime(lambda spec: model),
    )

    async def get_host() -> Any:
        return host

    app = FastAPI()
    app.include_router(build_console_router(get_host, lambda: TOKEN))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://agent.test") as client:
        receipt = await host.dispatch("sort the photos by year", lanlan_name="Lan", lang="en")
        commission = receipt.commissions[0]
        await wait_status(host, commission.id, "working")

        assert (await client.post(f"/console/commissions/{commission.id}/tell", headers={}, json={"text": "x"})).status_code == 403
        told = await client.post(f"/console/commissions/{commission.id}/tell", headers=HEADERS, json={"text": "by month, please"})
        assert told.json() == {"success": True}
        for payload in ({}, {"text": "  "}, {"text": 3}):
            bad = await client.post(f"/console/commissions/{commission.id}/tell", headers=HEADERS, json=payload)
            assert bad.status_code == 400 and bad.json()["detail"]["code"] == "bad_text"
        # It asked nothing: an answer is not taken, and one that says nothing is not sent on.
        unasked = await client.post(f"/console/commissions/{commission.id}/answer", headers=HEADERS, json={"text": "yes"})
        assert unasked.status_code == 409 and unasked.json()["detail"]["code"] == "not_taken"
        for payload in ({}, {"text": " "}, {"choice": 3}):
            bad = await client.post(f"/console/commissions/{commission.id}/answer", headers=HEADERS, json=payload)
            assert bad.status_code == 400 and bad.json()["detail"]["code"] == "bad_text"

        stopped = await client.post(f"/console/commissions/{commission.id}/stop", headers=HEADERS)
        assert stopped.status_code == 200 and stopped.json()["status"] == "cancelled"
        release.set()
        assert (await host.ledger.get(commission.id)).status == "cancelled"
        # Over: nothing takes a sentence any more; and one that never was is said to be missing.
        late = await client.post(f"/console/commissions/{commission.id}/tell", headers=HEADERS, json={"text": "still there?"})
        assert late.status_code == 409 and late.json()["detail"]["code"] == "not_taken"
        missing = await client.post("/console/commissions/no-such-commission/stop", headers=HEADERS)
        assert missing.status_code == 404 and missing.json()["detail"]["code"] == "no_commission"
    await host.shutdown()
