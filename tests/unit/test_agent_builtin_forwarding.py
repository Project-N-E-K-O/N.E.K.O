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

"""From the agent process to the banner: the main server's part and the page's part.

While a task agent uses the keyboard and mouse or the browser, a banner is
shown over the desktop or the browser window, and Esc on it stops the task.
The agent process sends a ``builtin_activity`` event and offers the routes
(covered in ``tests/unit/nekoclaw/test_agent_wiring.py``). Here:

* the main server hands the event to every connected page as
  ``agent_builtin_activity``;
* the page keeps what is in use per group and dispatches the window event
  the desktop shell listens for;
* the main server's routes pass the banner's requests, and the browser
  settings, on to the agent process.
"""

from __future__ import annotations

import json
import shutil
import textwrap
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketState

from tests.node_harness import run_node_script

REPO_ROOT = Path(__file__).resolve().parents[2]
APP_WEBSOCKET_PATH = REPO_ROOT / "static" / "app" / "app-websocket.js"

ACTIVITY = {
    "group": "browser",
    "state": "started",
    "commission_id": "c1",
    "window": {"left": 40, "top": 60, "width": 1280, "height": 800},
}


# ---------------------------------------------------------------------------
# The main server: the event goes to every connected page
# ---------------------------------------------------------------------------


class Socket:
    """Stands in for a page's WebSocket."""

    def __init__(self, *, connected: bool = True, broken: bool = False) -> None:
        self.client_state = WebSocketState.CONNECTED if connected else WebSocketState.DISCONNECTED
        self.broken = broken
        self.payloads: list[dict[str, Any]] = []

    async def send_json(self, payload: dict[str, Any]) -> None:
        if self.broken:
            raise RuntimeError("the page went away")
        self.payloads.append(payload)


class Session:
    def __init__(self, websocket: Any) -> None:
        self.websocket = websocket


@pytest.fixture
def pages(monkeypatch: pytest.MonkeyPatch) -> dict[str, Socket]:
    from app.main_server import character_runtime

    sockets = {
        "cat": Socket(),
        "dog": Socket(),
        "closed": Socket(connected=False),
    }
    sessions = [(name, Session(socket)) for name, socket in sockets.items()]
    # A character whose page was never opened.
    sessions.append(("never opened", Session(None)))
    monkeypatch.setattr(character_runtime, "_iter_session_managers", lambda: iter(sessions))
    monkeypatch.setattr(character_runtime, "_get_session_manager", lambda name: dict(sessions).get(name))
    return sockets


@pytest.mark.parametrize("lanlan_name", ["cat", None, "a character with no page"])
async def test_builtin_activity_is_told_to_every_connected_page_whichever_character_it_names(pages, lanlan_name):
    from app import main_server

    await main_server._handle_agent_event(
        {"event_type": "builtin_activity", "lanlan_name": lanlan_name, "activity": dict(ACTIVITY)}
    )

    # The banner is over the desktop or the browser window, not in one
    # character's page: whichever page is open has to hear of it.
    told = {"type": "agent_builtin_activity", "activity": ACTIVITY}
    assert pages["cat"].payloads == [told]
    assert pages["dog"].payloads == [told]
    assert pages["closed"].payloads == []


async def test_the_end_of_a_use_is_told_the_same_way(pages):
    from app import main_server

    ended = {"group": "keyboard_mouse", "state": "ended", "commission_id": "c1", "window": None}

    await main_server._handle_agent_event({"event_type": "builtin_activity", "lanlan_name": "cat", "activity": ended})

    assert pages["dog"].payloads == [{"type": "agent_builtin_activity", "activity": ended}]


@pytest.mark.parametrize("activity", [None, "started", ["browser"], 1])
async def test_an_event_without_an_activity_tells_no_page_anything(pages, activity):
    from app import main_server

    event: dict[str, Any] = {"event_type": "builtin_activity", "lanlan_name": "cat"}
    if activity is not None:
        event["activity"] = activity

    await main_server._handle_agent_event(event)

    assert [socket.payloads for socket in pages.values()] == [[], [], []]


async def test_a_page_that_cannot_be_reached_does_not_keep_the_others_from_being_told(pages):
    from app import main_server

    pages["cat"].broken = True

    await main_server._handle_agent_event(
        {"event_type": "builtin_activity", "lanlan_name": "cat", "activity": dict(ACTIVITY)}
    )

    assert pages["dog"].payloads == [{"type": "agent_builtin_activity", "activity": ACTIVITY}]


# ---------------------------------------------------------------------------
# The page: what is in use is kept per group, and the window is told
# ---------------------------------------------------------------------------

BRANCH_OPENER = "} else if (response.type === 'agent_builtin_activity') {"


def _branch_body(source: str) -> str:
    """Return the body of the ``agent_builtin_activity`` branch, braces balanced."""
    assert source.count(BRANCH_OPENER) == 1, "the branch must be there exactly once"
    rest = source.split(BRANCH_OPENER, 1)[1]
    depth, index, quote = 1, 0, ""
    while index < len(rest):
        char = rest[index]
        if quote:
            if char == "\\":
                index += 1
            elif char == quote:
                quote = ""
        elif char in "'\"`":
            quote = char
        elif rest.startswith("//", index):
            index = rest.index("\n", index)
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return rest[:index]
        index += 1
    raise AssertionError("the branch is not closed")


def test_the_page_branch_names_the_event_and_the_record_the_desktop_shell_reads():
    body = _branch_body(APP_WEBSOCKET_PATH.read_text(encoding="utf-8"))

    # The names are the contract with the desktop shell, which is another repository.
    assert "new CustomEvent('neko-agent-builtin-activity', { detail: activity })" in body
    assert "window._agentBuiltinActivity[activity.group] = activity;" in body
    assert "delete window._agentBuiltinActivity[activity.group];" in body
    assert "activity.state === 'started'" in body


def _run_branch(scenario: str) -> dict[str, Any]:
    node_path = shutil.which("node")
    if not node_path:
        pytest.skip("node is not installed; skipping the page's agent_builtin_activity harness")
    body = _branch_body(APP_WEBSOCKET_PATH.read_text(encoding="utf-8"))
    script = textwrap.dedent(
        """
        const events = [];
        const warnings = [];
        let dispatchFails = false;
        const window = {
          dispatchEvent(event) {
            if (dispatchFails) throw new Error('no listener can be reached');
            events.push({ type: event.type, detail: event.detail });
            return true;
          },
        };
        class CustomEvent {
          constructor(type, init) { this.type = type; this.detail = init ? init.detail : undefined; }
        }
        const console = { warn(...args) { warnings.push(String(args[0])); } };
        function handle(response) {
          if (response.type === 'agent_task_update') {
            throw new Error('another message type must not reach this branch');
          %(opener)s
        %(body)s
          }
        }
        function kept() { return JSON.parse(JSON.stringify(window._agentBuiltinActivity || null)); }
        function tell(activity) { handle({ type: 'agent_builtin_activity', activity }); }
        const out = {};
        %(scenario)s
        out.events = events;
        out.warnings = warnings;
        process.stdout.write(JSON.stringify(out));
        """
    ) % {"opener": BRANCH_OPENER, "body": body, "scenario": textwrap.dedent(scenario)}
    result = run_node_script(
        node_path, script, cwd=str(REPO_ROOT), capture_output=True, text=True, check=False, timeout=60
    )
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def test_the_page_keeps_what_is_in_use_per_group_and_tells_the_window_each_time():
    browser = dict(ACTIVITY)
    keyboard = {"group": "keyboard_mouse", "state": "started", "commission_id": "c2", "window": None}
    out = _run_branch(
        f"""
        const browser = {json.dumps(browser)};
        const keyboard = {json.dumps(keyboard)};
        tell(browser);
        out.afterTheBrowser = kept();
        tell(keyboard);
        out.afterBoth = kept();
        tell(Object.assign({{}}, browser, {{ state: 'ended', window: null }}));
        out.afterTheBrowserEnded = kept();
        // Anything but "started" means the group is no longer in use.
        tell(Object.assign({{}}, keyboard, {{ state: 'stopped' }}));
        out.afterAll = kept();
        """
    )

    assert out["afterTheBrowser"] == {"browser": browser}
    assert out["afterBoth"] == {"browser": browser, "keyboard_mouse": keyboard}
    assert out["afterTheBrowserEnded"] == {"keyboard_mouse": keyboard}
    assert out["afterAll"] == {}
    # The desktop shell draws the banner from these: one event per message, the activity as its detail.
    assert out["events"] == [
        {"type": "neko-agent-builtin-activity", "detail": browser},
        {"type": "neko-agent-builtin-activity", "detail": keyboard},
        {"type": "neko-agent-builtin-activity", "detail": {**browser, "state": "ended", "window": None}},
        {"type": "neko-agent-builtin-activity", "detail": {**keyboard, "state": "stopped"}},
    ]
    assert out["warnings"] == []


def test_the_page_survives_a_message_it_cannot_make_sense_of_or_pass_on():
    out = _run_branch(
        """
        // No activity at all, and one that names no group: nothing is kept.
        handle({ type: 'agent_builtin_activity' });
        tell({ state: 'started', commission_id: 'c1' });
        out.kept = kept();
        out.eventsBefore = events.length;
        // A listener that throws must not take the message handler down.
        dispatchFails = true;
        tell({ group: 'browser', state: 'started', commission_id: 'c1', window: null });
        out.keptAfterTheFailure = kept();
        """
    )

    assert out["kept"] == {}
    assert out["eventsBefore"] == 2
    assert out["keptAfterTheFailure"] == {
        "browser": {"group": "browser", "state": "started", "commission_id": "c1", "window": None}
    }
    assert len(out["warnings"]) == 1


# ---------------------------------------------------------------------------
# The main server's routes pass the requests on to the agent process
# ---------------------------------------------------------------------------


class Answer:
    def __init__(self, status_code: int, body: Any) -> None:
        self.status_code = status_code
        self.is_success = 200 <= status_code < 300
        self._body = body

    def json(self) -> Any:
        return self._body


class AgentProcess:
    """Stands in for the HTTP client to the agent process: answers as told, or fails."""

    def __init__(self) -> None:
        self.requests: list[tuple[str, str, Any]] = []
        self.answer = Answer(200, {"success": True})
        self.error: Exception | None = None

    def _take(self, method: str, url: str, options: dict[str, Any]) -> Answer:
        self.requests.append((method, url, options.get("json")))
        if self.error is not None:
            raise self.error
        return self.answer

    async def get(self, url: str, **options: Any) -> Answer:
        return self._take("GET", url, options)

    async def post(self, url: str, **options: Any) -> Answer:
        return self._take("POST", url, options)


@pytest.fixture
def agent(monkeypatch: pytest.MonkeyPatch) -> AgentProcess:
    from main_routers import agent_router

    monkeypatch.delenv("NEKO_ACTIVITY_TRACKER_REMOTE", raising=False)
    monkeypatch.delenv("ACTIVITY_TRACKER_REMOTE", raising=False)
    fake = AgentProcess()
    monkeypatch.setattr(agent_router, "_get_http_client", lambda: fake)
    return fake


@pytest.fixture
def client() -> TestClient:
    from main_routers import agent_router

    app = FastAPI()
    app.include_router(agent_router.router)
    return TestClient(app)


def _agent_url(path: str) -> str:
    from main_routers import agent_router

    return f"{agent_router.TOOL_SERVER_BASE}{path}"


def test_the_activity_route_passes_on_what_the_agent_process_says(agent, client):
    agent.answer = Answer(200, {"activity": [ACTIVITY]})

    response = client.get("/api/agent/builtin/activity")

    assert (response.status_code, response.json()) == (200, {"activity": [ACTIVITY]})
    assert agent.requests == [("GET", _agent_url("/builtin/activity"), None)]


@pytest.mark.parametrize("failure", [Answer(503, {"detail": "starting"}), httpx.ConnectError("refused")])
def test_the_activity_route_answers_with_no_activity_when_the_agent_process_cannot_say(agent, client, failure):
    if isinstance(failure, Exception):
        agent.error = failure
    else:
        agent.answer = failure

    response = client.get("/api/agent/builtin/activity")

    # A banner that asks while the agent process is away has nothing to show.
    assert response.status_code == 502
    assert response.json()["activity"] == []
    assert response.json()["error"]


@pytest.mark.parametrize(
    ("sent", "passed_on"),
    [({"group": "browser"}, {"group": "browser"}), ({}, {}), (None, {})],
)
def test_the_stop_route_passes_the_group_on_and_returns_what_was_stopped(agent, client, sent, passed_on):
    agent.answer = Answer(200, {"success": True, "stopped": ["c1"]})

    response = client.post("/api/agent/builtin/stop", json=sent) if sent is not None else client.post(
        "/api/agent/builtin/stop"
    )

    assert (response.status_code, response.json()) == (200, {"success": True, "stopped": ["c1"]})
    assert agent.requests == [("POST", _agent_url("/builtin/stop"), passed_on)]


def test_the_stop_route_keeps_the_agent_processs_refusal_of_an_unknown_group(agent, client):
    agent.answer = Answer(400, {"detail": "unknown group"})

    response = client.post("/api/agent/builtin/stop", json={"group": "desktop"})

    assert response.status_code == 400
    assert response.json()["success"] is False


@pytest.mark.parametrize(
    ("error", "status"),
    [
        # The request reached the agent process; stopping took longer than the wait.
        (httpx.ReadTimeout("read timed out"), 504),
        (httpx.ConnectError("refused"), 502),
        (httpx.ConnectTimeout("connect timed out"), 502),
    ],
)
def test_the_stop_route_tells_a_stop_that_may_have_happened_from_one_that_never_arrived(
    agent, client, error, status
):
    agent.error = error

    response = client.post("/api/agent/builtin/stop", json={"group": "browser"})

    assert response.status_code == status
    assert response.json()["success"] is False


def test_the_browser_settings_are_read_from_the_agent_process(agent, client):
    view = {
        "settings": {"mode": "own_profile", "executable": None, "headless": False},
        "modes": ["own_profile", "attach"],
        "browsers": [{"kind": "edge", "name": "Microsoft Edge", "executable": "C:/edge.exe"}],
    }
    agent.answer = Answer(200, view)

    response = client.get("/api/agent/builtin/browser/settings")

    assert (response.status_code, response.json()) == (200, view)
    assert agent.requests == [("GET", _agent_url("/builtin/browser/settings"), None)]


def test_a_change_of_the_browser_settings_is_passed_on_as_it_is(agent, client):
    saved = {"success": True, "settings": {"mode": "attach", "executable": None, "headless": False}}
    agent.answer = Answer(200, saved)

    response = client.post("/api/agent/builtin/browser/settings", json={"mode": "attach"})

    assert (response.status_code, response.json()) == (200, saved)
    assert agent.requests == [("POST", _agent_url("/builtin/browser/settings"), {"mode": "attach"})]


def test_a_refused_change_of_the_browser_settings_keeps_its_status(agent, client):
    agent.answer = Answer(400, {"detail": "unknown mode"})

    response = client.post("/api/agent/builtin/browser/settings", json={"mode": "incognito"})

    assert response.status_code == 400
    assert response.json()["success"] is False


@pytest.mark.parametrize(
    ("method", "path", "body"),
    [
        ("get", "/api/agent/builtin/browser/settings", None),
        ("post", "/api/agent/builtin/browser/settings", {"mode": "attach"}),
    ],
)
def test_the_browser_settings_routes_say_so_when_the_agent_process_cannot_be_reached(
    agent, client, method, path, body
):
    agent.error = httpx.ConnectError("refused")

    response = client.get(path) if method == "get" else client.post(path, json=body)

    assert response.status_code == 502
    assert response.json()["success"] is False


def test_reading_the_browser_settings_from_an_agent_process_that_fails_is_a_bad_gateway(agent, client):
    agent.answer = Answer(500, {"detail": "boom"})

    response = client.get("/api/agent/builtin/browser/settings")

    assert response.status_code == 502


@pytest.mark.parametrize(
    ("method", "path", "body"),
    [
        ("get", "/api/agent/builtin/activity", None),
        ("post", "/api/agent/builtin/stop", {"group": "browser"}),
        ("get", "/api/agent/builtin/browser/settings", None),
        ("post", "/api/agent/builtin/browser/settings", {"mode": "attach"}),
    ],
)
def test_on_a_remote_backend_none_of_these_routes_reaches_the_agent_process(
    agent, client, monkeypatch, method, path, body
):
    # There the desktop and the browser are the server's, not the user's.
    monkeypatch.setenv("NEKO_ACTIVITY_TRACKER_REMOTE", "1")

    response = client.get(path) if method == "get" else client.post(path, json=body)

    assert response.status_code == 501
    assert "NEKO_ACTIVITY_TRACKER_REMOTE" in response.json()["error"]
    assert agent.requests == []
