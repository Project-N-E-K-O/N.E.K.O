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
