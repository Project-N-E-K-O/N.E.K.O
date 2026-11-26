# -*- coding: utf-8 -*-
"""What a NekoPower commission asks the user reaches the page, and the answer reaches the agent process.

The chat window and the task HUD are pages of the main server. A question is
pushed to the page of the character it belongs to (``nekopower_question``),
and so is the word that it is over; the answer goes back through
``POST /api/agent/commissions/{id}/answer``, passed on to the agent process
as it is, with what that answers passed back as it is.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import httpx
import pytest
from starlette.websockets import WebSocketState

QUESTION = {"kind": "permission", "id": "q1", "options": ["once", "deny"], "line": "run the command npm test"}


class Socket:
    """Stands in for a page's WebSocket."""

    def __init__(self, *, connected: bool = True) -> None:
        self.client_state = WebSocketState.CONNECTED if connected else WebSocketState.DISCONNECTED
        self.payloads: list[dict[str, Any]] = []

    async def send_json(self, payload: dict[str, Any]) -> None:
        self.payloads.append(payload)


class Session:
    def __init__(self, websocket: Any) -> None:
        self.websocket = websocket


@pytest.fixture
def pages(monkeypatch: pytest.MonkeyPatch) -> dict[str, Socket]:
    from app.main_server import character_runtime

    sockets = {"cat": Socket(), "dog": Socket(), "closed": Socket(connected=False)}
    sessions = [(name, Session(socket)) for name, socket in sockets.items()]
    monkeypatch.setattr(character_runtime, "_iter_session_managers", lambda: iter(sessions))
    monkeypatch.setattr(character_runtime, "_get_session_manager", lambda name: dict(sessions).get(name))
    return sockets


async def test_a_question_is_sent_to_the_page_of_the_character_it_belongs_to(pages):
    from app.main_server import character_runtime

    await character_runtime._send_nekopower_question(
        {
            "event_type": "proactive_message", "channel": "nekopower", "lanlan_name": "cat", "task_id": "c1",
            "title": "add tests", "route": "task_agent", "question": dict(QUESTION), "text": "what she passes on",
        }
    )

    assert pages["cat"].payloads == [
        {
            "type": "nekopower_question", "task_id": "c1", "title": "add tests", "route": "task_agent", "lanlan_name": "cat",
            "question": QUESTION, "question_id": "q1",
        }
    ]
    # What she is to pass on is hers: the page is given the question, not the sentence.
    assert pages["dog"].payloads == []


async def test_that_a_question_is_over_is_told_with_which_one_it_was(pages):
    from app import main_server

    await main_server._handle_agent_event(
        {"event_type": "nekopower_question", "lanlan_name": "cat", "task_id": "c1", "question": None, "question_id": "q1"}
    )

    (told,) = pages["cat"].payloads
    assert (told["type"], told["task_id"], told["question"], told["question_id"]) == ("nekopower_question", "c1", None, "q1")
    assert pages["dog"].payloads == []


async def test_a_question_of_a_character_with_no_page_goes_to_every_connected_one(pages):
    from app import main_server

    await main_server._handle_agent_event(
        {"event_type": "nekopower_question", "lanlan_name": "nobody", "task_id": "c1", "question": None, "question_id": ""}
    )

    assert len(pages["cat"].payloads) == len(pages["dog"].payloads) == 1
    assert pages["closed"].payloads == []


async def test_nothing_is_sent_for_an_event_that_names_no_task(pages):
    from app import main_server

    await main_server._handle_agent_event({"event_type": "nekopower_question", "lanlan_name": "cat", "question": dict(QUESTION)})

    assert pages["cat"].payloads == []


# ---------------------------------------------------------------------------
# The answer, and reading what waits
# ---------------------------------------------------------------------------


@pytest.fixture
def router(monkeypatch: pytest.MonkeyPatch):
    from main_routers import agent_router

    monkeypatch.setattr(agent_router, "_remote_backend_block", lambda: None)
    yield agent_router


def _client(calls: list[dict[str, Any]], status_code: int = 200, content: Any = None, raising: Exception | None = None) -> MagicMock:
    client = MagicMock()

    async def request(url: str, **kwargs: Any) -> Any:
        calls.append({"url": url, **kwargs})
        if raising is not None:
            raise raising
        response = MagicMock()
        response.status_code = status_code
        response.is_success = 200 <= status_code < 300
        if isinstance(content, Exception):
            response.json = MagicMock(side_effect=content)
        else:
            response.json = MagicMock(return_value=content if content is not None else {"success": True})
        return response

    client.post = request
    client.get = request
    return client


async def test_an_answer_is_passed_on_to_the_agent_process_with_the_token_of_this_launch(router, monkeypatch):
    from config import AUTOSTART_CSRF_TOKEN

    calls: list[dict[str, Any]] = []
    monkeypatch.setattr(router, "_get_http_client", lambda: _client(calls, content={"success": True, "note_taken": False}))
    body = {"choice": "deny", "question_id": "q1", "note": "use pnpm"}

    answer = await router.proxy_commission_answer("c 1/..", dict(body))

    assert answer.status_code == 200
    assert answer.body == b'{"success":true,"note_taken":false}'
    (call,) = calls
    # The id is one segment of the path, whatever it holds.
    assert call["url"] == f"{router.TOOL_SERVER_BASE}/console/commissions/c%201%2F../answer"
    assert call["json"] == body
    assert call["headers"] == {"X-CSRF-Token": AUTOSTART_CSRF_TOKEN}


@pytest.mark.parametrize(
    "status_code, content",
    [
        # Answered, taken back or replaced meanwhile: the page says so and reads anew.
        (409, {"detail": {"code": "not_taken"}}),
        (400, {"detail": {"code": "bad_text", "field": "text"}}),
    ],
)
async def test_what_the_agent_process_answers_is_passed_back_as_it_is(router, monkeypatch, status_code, content):
    import json

    monkeypatch.setattr(router, "_get_http_client", lambda: _client([], status_code, content))

    answer = await router.proxy_commission_answer("c1", {"text": "yes"})

    assert answer.status_code == status_code
    assert json.loads(answer.body) == content


@pytest.mark.parametrize(
    "raising, status_code, content, expected",
    [
        # It never got there: not taken, and the page offers to give it again.
        (httpx.ConnectError("refused"), 200, None, 502),
        (httpx.ConnectTimeout("no route"), 200, None, 502),
        (httpx.PoolTimeout("busy"), 200, None, 502),
        (None, 404, ValueError("not json"), 502),
        # It was sent and nothing is known of what came of it: the page reads how things stand
        # first. Said to have failed, an approval that was taken would be given a second time.
        (httpx.ReadTimeout("slow"), 200, None, 504),
        (httpx.ReadError("reset"), 200, None, 504),
        (httpx.RemoteProtocolError("closed"), 200, None, 504),
        (None, 500, ValueError("not json"), 504),
        (None, 200, ValueError("not json"), 504),
    ],
)
async def test_an_agent_process_that_does_not_answer_is_no_stale_question(
    router, monkeypatch, raising, status_code, content, expected
):
    monkeypatch.setattr(router, "_get_http_client", lambda: _client([], status_code, content, raising))

    answer = await router.proxy_commission_answer("c1", {"text": "yes"})

    # Never 409: the page would take the question for answered.
    assert answer.status_code == expected


async def test_what_waits_is_read_from_the_agent_process(router, monkeypatch):
    calls: list[dict[str, Any]] = []
    rows = [{"task_id": "c1", "title": "add tests", "question": dict(QUESTION), "route": "task_agent", "lanlan_name": "cat"}]
    monkeypatch.setattr(router, "_get_http_client", lambda: _client(calls, content={"success": True, "questions": rows}))

    assert await router.proxy_questions() == {"questions": rows}
    assert calls[0]["url"] == f"{router.TOOL_SERVER_BASE}/console/questions"

    monkeypatch.setattr(router, "_get_http_client", lambda: _client([], 503, {"detail": "starting"}))
    down = await router.proxy_questions()
    assert down.status_code == 502


def test_the_answer_route_is_guarded_as_every_agent_mutation_is(router):
    routes = {route.path: route for route in router.router.routes}

    answer = routes["/api/agent/commissions/{commission_id}/answer"]
    assert answer.methods == {"POST"}
    assert type(answer).__name__ == "_AgentMutationRoute"
    assert routes["/api/agent/questions"].methods == {"GET"}
