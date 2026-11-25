"""What the console pages ask of the task agents goes through this server to the agent process."""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest
from fastapi import FastAPI

from plugin.server.infrastructure import mutation_auth
from plugin.server.infrastructure.exceptions import register_exception_handlers
from plugin.server.routes import console_proxy
from utils.host_origin_guard import HostOriginGuardMiddleware

pytestmark = pytest.mark.plugin_unit

PAGE = "http://127.0.0.1:48916"


class Agent:
    """Stands in for the agent process: records what arrives, answers what the test set."""

    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []
        self.answer: Any = httpx.Response(200, json={"success": True})

    def handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if isinstance(self.answer, Exception):
            raise self.answer
        return self.answer


@pytest.fixture
def agent(monkeypatch: pytest.MonkeyPatch) -> Agent:
    stand_in = Agent()
    client = httpx.AsyncClient(transport=httpx.MockTransport(stand_in.handle))
    monkeypatch.setattr(console_proxy, "_http", lambda: client)
    monkeypatch.setattr(console_proxy, "_remote", lambda: False)
    return stand_in


@pytest.fixture
def app() -> FastAPI:
    app = FastAPI()
    register_exception_handlers(app)
    app.include_router(console_proxy.router)
    app.add_middleware(HostOriginGuardMiddleware)
    return app


def _client(app: FastAPI, headers: dict[str, str] | None = None) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app, client=("127.0.0.1", 1234)), base_url=PAGE, headers=headers or {},
    )


def _page_reads() -> dict[str, str]:
    """What a browser sends with a request of the console page for something of its own server."""
    return {"Sec-Fetch-Site": "same-origin", "Sec-Fetch-Mode": "cors", "Referer": f"{PAGE}/ui/"}


def _page_changes() -> dict[str, str]:
    return {"Origin": PAGE, "X-CSRF-Token": mutation_auth.AUTOSTART_CSRF_TOKEN}


async def test_a_read_of_the_console_page_reaches_the_agent_with_the_token_and_its_answer_comes_back(app, agent):
    agent.answer = httpx.Response(200, json={"success": True, "categories": []})
    async with _client(app, _page_reads()) as client:
        answer = await client.get("/console/space/entries", params={"project": "ledger tool", "archive_year": "2026"})
    assert answer.status_code == 200 and answer.json() == {"success": True, "categories": []}
    (request,) = agent.requests
    assert request.method == "GET" and request.url.path == "/console/space/entries"
    assert dict(request.url.params) == {"project": "ledger tool", "archive_year": "2026"}
    assert request.headers["X-CSRF-Token"] == mutation_auth.AUTOSTART_CSRF_TOKEN
    assert request.url.host == "127.0.0.1"


async def test_a_change_of_the_console_page_reaches_the_agent_with_its_body(app, agent):
    async with _client(app, _page_changes()) as client:
        answer = await client.post("/console/permissions", json={"category": "run_command", "mode": "deny"})
    assert answer.status_code == 200
    (request,) = agent.requests
    assert request.method == "POST" and request.url.path == "/console/permissions"
    assert json.loads(request.content) == {"category": "run_command", "mode": "deny"}
    assert request.headers["X-CSRF-Token"] == mutation_auth.AUTOSTART_CSRF_TOKEN


@pytest.mark.parametrize("method", ["PUT", "PATCH", "DELETE"])
async def test_every_changing_method_is_passed_on_as_it_came(app, agent, method):
    async with _client(app, _page_changes()) as client:
        answer = await client.request(method, "/console/space/entries/a-note", json={"title": "x"})
    assert answer.status_code == 200 and agent.requests[0].method == method


@pytest.mark.parametrize(
    "headers",
    [
        {"Origin": "https://evil.example"},
        {"Sec-Fetch-Site": "cross-site", "Sec-Fetch-Mode": "no-cors"},
        {"Referer": "https://evil.example/page"},
    ],
)
async def test_a_page_of_another_site_reads_nothing_through_this_server(app, agent, headers):
    async with _client(app, headers) as client:
        answer = await client.get("/console/permissions")
    assert answer.status_code == 403 and agent.requests == []


@pytest.mark.parametrize(
    "headers",
    [
        {"Origin": "https://evil.example", "X-CSRF-Token": "guess"},
        {"Origin": PAGE},
        {"Origin": PAGE, "X-CSRF-Token": "guess"},
        {"Sec-Fetch-Site": "cross-site"},
    ],
)
async def test_a_change_without_the_page_and_its_token_is_refused_before_the_agent_hears_of_it(app, agent, headers):
    async with _client(app, headers) as client:
        answer = await client.post("/console/permissions", json={"category": "run_command", "mode": "allow"})
    assert answer.status_code == 403 and agent.requests == []


async def test_with_the_backend_away_from_the_user_things_are_read_and_not_changed(app, agent, monkeypatch):
    monkeypatch.setattr(console_proxy, "_remote", lambda: True)
    async with _client(app, _page_changes()) as client:
        refused = await client.post("/console/runtime/dsh/install")
    assert refused.status_code == 501 and refused.json()["detail"] == {"code": "remote_backend"}
    assert agent.requests == []
    async with _client(app, _page_reads()) as client:
        assert (await client.get("/console/runtime")).status_code == 200
    assert len(agent.requests) == 1


async def test_what_the_agent_refuses_is_told_as_the_agent_told_it(app, agent):
    agent.answer = httpx.Response(409, json={"detail": {"code": "stale"}})
    async with _client(app, _page_changes()) as client:
        answer = await client.post("/console/permissions", json={"category": "run_command", "mode": "allow"})
    assert answer.status_code == 409 and answer.json() == {"detail": {"code": "stale"}}


@pytest.mark.parametrize("answer", [httpx.ConnectError("refused"), httpx.ReadTimeout("slow"), httpx.Response(200, text="<html>")])
async def test_an_agent_that_does_not_answer_is_told_as_such(app, agent, answer):
    agent.answer = answer
    async with _client(app, _page_reads()) as client:
        seen = await client.get("/console/runtime")
    assert seen.status_code == 502 and seen.json()["detail"] == {"code": "agent_unreachable"}


@pytest.mark.parametrize(
    ("payload", "command"),
    [
        ({"enabled": True}, {"command": "set_agent_enabled", "enabled": True}),
        ({"enabled": False}, {"command": "set_agent_enabled", "enabled": False}),
        ({"key": "browser_use_enabled", "value": True}, {"command": "set_flag", "key": "browser_use_enabled", "value": True}),
        ({"key": "computer_use_enabled", "value": False}, {"command": "set_flag", "key": "computer_use_enabled", "value": False}),
        # Not a capability: whether the main page shows the task HUD.
        ({"key": "task_hud_visible", "value": False}, {"command": "set_task_hud_visible", "value": False}),
        ({"key": "task_hud_visible", "value": True}, {"command": "set_task_hud_visible", "value": True}),
    ],
)
async def test_a_switch_goes_to_the_main_server_as_the_command_it_is(app, agent, payload, command):
    async with _client(app, _page_changes()) as client:
        answer = await client.post("/console/switch", json=payload)
    assert answer.status_code == 200 and answer.json() == {"success": True}
    (request,) = agent.requests
    import config

    assert request.url.path == "/api/agent/command" and request.url.port == config.MAIN_SERVER_PORT
    assert request.url.host == "127.0.0.1"
    assert request.headers["X-CSRF-Token"] == mutation_auth.AUTOSTART_CSRF_TOKEN
    assert "origin" not in request.headers
    assert request.headers["Referer"] == f"http://127.0.0.1:{config.MAIN_SERVER_PORT}/"
    assert json.loads(request.content) == command


@pytest.mark.parametrize(
    "payload",
    [
        {"key": "user_plugin_enabled", "value": False},
        {"key": "agent_enabled", "value": True},
        {"enabled": "yes"},
        {"enabled": True, "key": "browser_use_enabled", "value": True},
        {"key": "browser_use_enabled", "value": 1},
        {"key": "task_hud_visible", "value": "false"},
        {"key": "task_hud_visible", "value": None},
        {"command": "set_task_hud_visible", "value": True},
        {"command": "refresh_state"},
        {"command": "set_agent_enabled", "profile": {"x": 1}},
        {"key": [], "value": True},
        {"key": {"a": 1}, "value": True},
        [],
    ],
)
async def test_nothing_but_those_switches_reaches_the_command_route(app, agent, payload):
    async with _client(app, _page_changes()) as client:
        answer = await client.post("/console/switch", json=payload)
    assert answer.status_code == 400 and answer.json()["detail"] == {"code": "bad_switch"}
    assert agent.requests == []


async def test_a_switch_needs_the_page_and_its_token_and_a_backend_that_is_with_the_user(app, agent, monkeypatch):
    async with _client(app, {"Origin": PAGE}) as client:
        assert (await client.post("/console/switch", json={"enabled": True})).status_code == 403
    monkeypatch.setattr(console_proxy, "_remote", lambda: True)
    async with _client(app, _page_changes()) as client:
        refused = await client.post("/console/switch", json={"enabled": True})
    assert refused.status_code == 501 and agent.requests == []


@pytest.mark.parametrize(
    ("answer", "code"),
    [
        (httpx.Response(200, json={"success": False, "error": "tool_server responded 500"}), "switch_failed"),
        (httpx.Response(502, json={"success": False}), "switch_failed"),
        (httpx.ConnectError("refused"), "agent_unreachable"),
        (httpx.Response(200, text="<html>"), "agent_unreachable"),
    ],
)
async def test_a_switch_that_was_not_taken_is_told_as_such(app, agent, answer, code):
    agent.answer = answer
    async with _client(app, _page_changes()) as client:
        seen = await client.post("/console/switch", json={"enabled": True})
    assert seen.status_code == 502 and seen.json()["detail"] == {"code": code}


@pytest.mark.parametrize(
    "path",
    [
        "/console/..%2Fagent%2Fcommand",
        "/console/..%2Fnekopower%2Fdispatch",
        "/console/space/..%2F..%2Fagent%2Fflags",
        "/console/space/%2E%2E/%2E%2E/agent/flags",
        "/console/.%2Fruntime",
        "/console/space%5C..%5C..%5Cagent%5Cflags",
        "/console/runtime%2F",
        "/console/space/%0Aentries",
    ],
)
@pytest.mark.parametrize("method", ["GET", "POST"])
async def test_a_path_that_climbs_out_of_the_console_routes_reaches_nothing(app, agent, method, path):
    headers = _page_reads() if method == "GET" else _page_changes()
    async with _client(app, headers) as client:
        answer = await client.request(method, path, json=None if method == "GET" else {"command": "set_agent_enabled"})
    assert answer.status_code == 404
    assert agent.requests == []


async def test_a_name_in_the_path_is_sent_on_as_one_segment(app, agent):
    async with _client(app, _page_reads()) as client:
        answer = await client.get("/console/space/entries/a%20b%3Fc%23d", params={"project": "ledger tool"})
    assert answer.status_code == 200
    (request,) = agent.requests
    assert request.url.raw_path.split(b"?")[0] == b"/console/space/entries/a%20b%3Fc%23d"
    assert dict(request.url.params) == {"project": "ledger tool"}


async def test_the_routes_that_wait_for_the_user_or_a_model_are_given_the_time(app, agent):
    async with _client(app, _page_changes()) as client:
        await client.post("/console/space/pick-directory", json={})
        await client.post("/console/familiars/draft", json={"text": "x"})
        await client.post("/console/permissions", json={})
    picked, drafted, other = agent.requests
    # The dialog stays open for two minutes, a draft is given 45 seconds.
    assert picked.extensions["timeout"]["read"] > 120
    assert drafted.extensions["timeout"]["read"] > 45
    assert other.extensions["timeout"]["read"] < 45


def test_the_router_is_part_of_the_plugin_server():
    from pathlib import Path

    source = (Path(console_proxy.__file__).resolve().parents[1] / "http_app.py").read_text(encoding="utf-8")
    assert "console_proxy_router" in source
    # Awaited where the server shuts down, not only imported.
    assert "await close_console_proxy()" in source


async def test_switch_succeeds_through_full_main_middleware_stack_behind_proxy(
    app: FastAPI, monkeypatch: pytest.MonkeyPatch
):
    import config
    from main_routers import agent_router
    from main_routers.system_router import _shared as system_shared
    from utils.asgi_body_limit import InboundBodySizeLimitMiddleware
    from utils.instance_access import InstanceAccessMiddleware
    from utils.internal_http_client import internal_service_event_hooks

    monkeypatch.setenv("NEKO_BEHIND_PROXY", "true")
    monkeypatch.delenv("NEKO_ACTIVITY_TRACKER_REMOTE", raising=False)
    monkeypatch.delenv("ACTIVITY_TRACKER_REMOTE", raising=False)
    monkeypatch.setattr(system_shared, "AUTOSTART_CSRF_TOKEN", mutation_auth.AUTOSTART_CSRF_TOKEN)
    monkeypatch.setattr(console_proxy, "_remote", lambda: False)
    monkeypatch.setattr(agent_router, "get_session_manager", dict)

    class _StubCfg:
        @staticmethod
        def get_character_data():
            return ("", "", "", "", "", "", "", "", "")

        @staticmethod
        def is_agent_free():
            return False

    monkeypatch.setattr(agent_router, "get_config_manager", _StubCfg)

    tool_requests: list[httpx.Request] = []

    def _tool_handler(request: httpx.Request) -> httpx.Response:
        tool_requests.append(request)
        return httpx.Response(200, json={"success": True})

    tool_client = httpx.AsyncClient(transport=httpx.MockTransport(_tool_handler))
    monkeypatch.setattr(agent_router, "_get_http_client", lambda: tool_client)

    main_app = FastAPI()
    main_app.include_router(agent_router.router)
    main_app.add_middleware(InboundBodySizeLimitMiddleware)
    main_app.add_middleware(InstanceAccessMiddleware)
    main_app.add_middleware(HostOriginGuardMiddleware)

    main_base = f"http://127.0.0.1:{config.MAIN_SERVER_PORT}"
    main_client = httpx.AsyncClient(
        transport=httpx.ASGITransport(app=main_app, client=("127.0.0.1", 54321)),
        base_url=main_base,
        event_hooks=internal_service_event_hooks(),
    )
    unsigned_client = httpx.AsyncClient(
        transport=httpx.ASGITransport(app=main_app, client=("127.0.0.1", 54321)),
        base_url=main_base,
    )
    monkeypatch.setattr(console_proxy, "_http", lambda: main_client)

    try:
        async with _client(app, _page_changes()) as client:
            answer = await client.post("/console/switch", json={"enabled": True})
        assert answer.status_code == 200 and answer.json() == {"success": True}
        assert len(tool_requests) == 1
        assert json.loads(tool_requests[0].content) == {
            "command": "set_agent_enabled",
            "enabled": True,
            "lanlan_name": "",
        }

        # Control: an unsigned request with an Origin header under NEKO_BEHIND_PROXY=true trips InstanceAccessMiddleware (401).
        rejected = await unsigned_client.post(
            "/api/agent/command",
            json={"command": "set_agent_enabled", "enabled": True},
            headers={"Origin": main_base, "X-CSRF-Token": mutation_auth.AUTOSTART_CSRF_TOKEN},
        )
        assert rejected.status_code == 401
        assert "instance_authorization_required" in rejected.text
    finally:
        await unsigned_client.aclose()
        await main_client.aclose()
        await tool_client.aclose()
