"""The host's tools as an MCP service for dsh sessions, and the listener they are served on."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import httpx
import pytest

from nekoclaw.dsh.endpoint import MODEL_PATH, LocalEndpoint
from nekoclaw.dsh.forward import ModelForwarder
from nekoclaw.dsh.host_tools import HostTools
from nekoclaw.runtime.host import HostCallbacks, NoHost
from nekoclaw.runtime.tools import tool_schemas


def _callbacks(calls: list[tuple[str, str, dict[str, Any]]], **replies: Any) -> HostCallbacks:
    def callback(name: str):
        async def call(session_id: str, payload: dict[str, Any]) -> Any:
            calls.append((name, session_id, payload))
            return replies.get(name)

        return call

    return HostCallbacks(
        decide_permission=callback("decide_permission"),
        ask_user=callback("ask_user"),
        report=callback("report"),
        note_lesson=callback("note_lesson"),
        web_search=callback("web_search"),
        subagent_start=callback("subagent_start"),
        subagent_list=callback("subagent_list"),
        subagent_send=callback("subagent_send"),
        subagent_interrupt=callback("subagent_interrupt"),
    )


def _open(tools: HostTools, tmp_path: Path, session_id: str = "s1", **options: Any) -> list[str]:
    reports: list[dict[str, Any]] = options.pop("reports", [])

    async def take_input(seconds: float) -> list[str]:
        del seconds
        return []

    return tools.open(
        session_id,
        workdir=str(tmp_path),
        session_dir=str(tmp_path / "session"),
        top_level=options.pop("top_level", True),
        disabled=options.pop("disabled", ()),
        take_input=options.pop("take_input", take_input),
        emit_report=reports.append,
    )


def _request(method: str, params: dict[str, Any] | None = None, request_id: Any = 1) -> dict[str, Any]:
    message: dict[str, Any] = {"jsonrpc": "2.0", "id": request_id, "method": method}
    if params is not None:
        message["params"] = params
    return message


async def test_the_tools_are_the_built_in_runtimes_by_name_and_definition(tmp_path: Path) -> None:
    tools = HostTools(_callbacks([]))

    names = _open(tools, tmp_path)
    listed = (await tools.handle("s1", _request("tools/list")))["result"]["tools"]

    assert names == [
        "interrupt_agent",
        "list_agents",
        "note_lesson",
        "report",
        "send_message",
        "subagent",
        "wait_agent",
        "web_fetch",
        "web_search",
    ]
    assert [tool["name"] for tool in listed] == names == tools.names("s1")
    schemas = tool_schemas()
    for tool in listed:
        assert tool["description"] == schemas[tool["name"]]["description"]
        assert tool["inputSchema"] == schemas[tool["name"]]["parameters"]


async def test_only_what_the_host_can_carry_out_is_offered(tmp_path: Path) -> None:
    async def report(session_id: str, payload: dict[str, Any]) -> None:
        return None

    # ``web_fetch`` asks nothing of the host: it is there whatever the host can do.
    assert _open(HostTools(HostCallbacks(report=report)), tmp_path) == ["report", "web_fetch"]
    assert _open(HostTools(NoHost()), tmp_path) == ["web_fetch"]
    child = _open(HostTools(_callbacks([])), tmp_path, top_level=False, disabled=("subagent",))
    assert "subagent" not in child and "wait_agent" in child


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1:48911/",
        "http://localhost:48911/",
        "http://[::1]/",
        "http://192.168.1.1/",
        "http://10.0.0.5:8080/admin",
        "http://169.254.169.254/latest/meta-data/",
    ],
)
async def test_the_fetch_tool_served_to_dsh_reaches_public_addresses_only(tmp_path: Path, url: str) -> None:
    # dsh's own fetch reaches every address and is switched off; this is the built-in runtime's.
    tools = HostTools(NoHost())
    _open(tools, tmp_path)

    result = await tools.call("s1", "web_fetch", {"url": url})

    assert result["isError"] and "blocked URL" in result["content"][0]["text"]
    assert "not a public" in result["content"][0]["text"]


async def test_the_protocol_is_the_small_one_the_gateway_speaks(tmp_path: Path) -> None:
    tools = HostTools(_callbacks([]))
    _open(tools, tmp_path)

    initialized = await tools.handle("s1", _request("initialize", {"protocolVersion": "2025-11-25"}))
    assert initialized["result"]["protocolVersion"] == "2025-06-18" and "tools" in initialized["result"]["capabilities"]
    older = await tools.handle("s1", _request("initialize", {"protocolVersion": "2024-11-05"}))
    assert older["result"]["protocolVersion"] == "2024-11-05"
    assert (await tools.handle("s1", _request("ping")))["result"] == {}
    # dsh's client asks for a newer protocol first and goes on when the method is unknown.
    probe = await tools.handle("s1", _request("server/discover", {}, "probe-1"))
    assert probe["error"]["code"] == -32601 and probe["id"] == "probe-1"
    assert await tools.handle("s1", {"jsonrpc": "2.0", "method": "notifications/initialized"}) is None
    assert await tools.handle("s1", {"jsonrpc": "2.0", "method": "something/else"}) is None
    assert (await tools.handle("s1", {"id": 1, "method": "ping"}))["error"]["code"] == -32600
    batch = await tools.handle("s1", [_request("ping", request_id=1), {"jsonrpc": "2.0", "method": "notifications/x"}, _request("ping", request_id=2)])
    assert [answer["id"] for answer in batch] == [1, 2]
    assert (await tools.handle("nobody", _request("tools/list")))["result"] == {"tools": []}


async def test_a_call_goes_to_the_hosts_callback_of_the_session_that_made_it(tmp_path: Path) -> None:
    calls: list[tuple[str, str, dict[str, Any]]] = []
    reports: list[dict[str, Any]] = []
    tools = HostTools(_callbacks(calls, report={"message": "she will be told"}, subagent_start={"name": "agent-1"}))
    _open(tools, tmp_path, "s1", reports=reports)
    _open(tools, tmp_path, "s2")

    reported = await tools.call("s1", "report", {"status": "blocked", "summary": "no network", "details": "tried twice"})
    started = await tools.call("s2", "subagent", {"description": "look it up", "prompt": "find the file", "cwd": "sub"})

    assert reported == {"content": [{"type": "text", "text": "report delivered (blocked). she will be told"}], "isError": False}
    assert started["content"][0]["text"] == "started subagent agent-1"
    assert calls == [
        ("report", "s1", {"status": "blocked", "summary": "no network", "details": "tried twice", "delivery": None}),
        ("subagent_start", "s2", {"description": "look it up", "prompt": "find the file", "name": None, "cwd": str(tmp_path / "sub")}),
    ]
    # The report is announced once the host took it.
    assert reports == [calls[0][2]]


async def test_what_is_wrong_with_a_call_is_told_to_the_model(tmp_path: Path) -> None:
    async def report(session_id: str, payload: dict[str, Any]) -> None:
        raise RuntimeError("the ledger is closed")

    reports: list[dict[str, Any]] = []
    tools = HostTools(HostCallbacks(report=report))
    _open(tools, tmp_path, reports=reports)

    missing = await tools.call("s1", "report", {"status": "ready"})
    refused = await tools.call("s1", "report", {"status": "ready", "summary": "done"})
    unknown = await tools.call("s1", "ask_user_question", {"questions": []})
    nobody = await tools.call("s9", "report", {"status": "ready", "summary": "done"})

    assert missing["isError"] and "summary" in missing["content"][0]["text"]
    assert refused["isError"] and "the ledger is closed" in refused["content"][0]["text"]
    assert unknown["isError"] and nobody["isError"]
    # dsh puts "Error:" in front of a failed call's text itself.
    assert not missing["content"][0]["text"].startswith("Error")
    assert reports == []


async def test_a_wait_hands_over_what_arrived_and_stays_inside_dshs_minute(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from config import nekoclaw_settings

    monkeypatch.setattr(nekoclaw_settings, "DSH_HOST_TOOL_SECONDS", 50.0)
    asked: list[float] = []
    waiting: list[list[str]] = [["the child is done", "and one more"], []]

    async def take_input(seconds: float) -> list[str]:
        asked.append(seconds)
        return waiting.pop(0)

    tools = HostTools(_callbacks([]))
    _open(tools, tmp_path, take_input=take_input)

    arrived = await tools.call("s1", "wait_agent", {"timeout_ms": 3_600_000})
    nothing = await tools.call("s1", "wait_agent", {})
    invalid = await tools.call("s1", "wait_agent", {"timeout_ms": 5})

    assert arrived["content"][0]["text"] == '{"timedOut":false}\n\nthe child is done\n\nand one more'
    assert nothing["content"][0]["text"] == '{"timedOut":true}'
    # An hour was asked for; the wait is cut to what fits a call on dsh. The default fits as it is.
    assert asked == [45.0, 30.0]
    assert invalid["isError"]


async def test_closing_a_session_takes_its_tools_away(tmp_path: Path) -> None:
    tools = HostTools(_callbacks([]))
    _open(tools, tmp_path)
    _open(tools, tmp_path, "s2")
    tools.close("s1")
    assert tools.names("s1") == [] and "report" in tools.names("s2")
    # The tools another session still has are not its own.
    assert (await tools.call("s1", "report", {"status": "ready", "summary": "x"}))["isError"]
    assert not (await tools.call("s2", "report", {"status": "ready", "summary": "x"}))["isError"]


# ---------------------------------------------------------------------------
# The listener
# ---------------------------------------------------------------------------


@pytest.fixture
async def endpoint(tmp_path: Path):
    calls: list[tuple[str, str, dict[str, Any]]] = []
    tools = HostTools(_callbacks(calls))
    _open(tools, tmp_path)
    forwarder = ModelForwarder(lambda session_id, usage: None, report_tool="mcp__host__report")
    listener = LocalEndpoint(forwarder=forwarder, tools=tools)
    await listener.start()
    try:
        yield listener, calls
    finally:
        await listener.stop()
        await forwarder.aclose()


async def test_the_listener_is_local_and_asks_for_a_token(endpoint) -> None:
    listener, calls = endpoint
    token = listener.issue("s1")
    process_token = listener.issue_process_token()
    model_url = f"{listener.model_url}/chat/completions"
    listing = {"jsonrpc": "2.0", "id": 1, "method": "tools/list"}
    # Only the loopback address is listened on, on a port the system chose.
    sockets = [sock for server in listener._server.servers for sock in server.sockets]
    assert [sock.getsockname()[0] for sock in sockets] == ["127.0.0.1"] and listener.port not in (None, 0)
    async with httpx.AsyncClient(trust_env=False) as client:
        good = await client.post(listener.tools_url, headers={"Authorization": f"Bearer {token}"}, json=listing)
        none = await client.post(listener.tools_url, json=listing)
        wrong = await client.post(listener.tools_url, headers={"Authorization": "Bearer nonsense"}, json=listing)
        # A process's token opens the model endpoint, not a session's tools, and the other way round.
        crossed = await client.post(listener.tools_url, headers={"Authorization": f"Bearer {process_token}"}, json=listing)
        model = await client.post(model_url, headers={"Authorization": f"Bearer {token}"}, json={})
        model_none = await client.post(model_url, json={})
        model_good = await client.post(model_url, headers={"Authorization": f"Bearer {process_token}"}, json={"model": "nobody"})
        # A page in a browser must not drive it, token or not, from wherever it was loaded:
        # it cannot make such a request without an Origin, and dsh never sends one.
        page = await client.post(listener.tools_url, headers={"Authorization": f"Bearer {token}", "Origin": "https://evil.example"}, json=listing)
        local = await client.post(listener.tools_url, headers={"Authorization": f"Bearer {token}", "Origin": "http://127.0.0.1:3000"}, json=listing)
        null = await client.post(model_url, headers={"Authorization": f"Bearer {process_token}", "Origin": "null"}, json={"model": "nobody"})
        got = await client.get(listener.tools_url, headers={"Authorization": f"Bearer {token}"})
        elsewhere = await client.post(f"http://127.0.0.1:{listener.port}/other", headers={"Authorization": f"Bearer {token}"}, json=listing)
        note = await client.post(listener.tools_url, headers={"Authorization": f"Bearer {token}"}, json={"jsonrpc": "2.0", "method": "notifications/initialized"})
        garbage = await client.post(listener.tools_url, headers={"Authorization": f"Bearer {token}"}, content=b"{not json")

    assert good.status_code == 200 and len(good.json()["result"]["tools"]) == 9
    assert (none.status_code, wrong.status_code, crossed.status_code, model.status_code, model_none.status_code) == (401, 401, 401, 401, 401)
    # Let in, and then told that no session holds that model name.
    assert model_good.status_code == 404
    assert (page.status_code, local.status_code, null.status_code) == (403, 403, 403)
    # What dsh's client does on connecting: a GET for a stream it can do without, and a notification.
    assert got.status_code == 405 and got.headers["allow"] == "POST"
    assert elsewhere.status_code == 404 and note.status_code == 202 and note.content == b""
    assert garbage.status_code == 400 and garbage.json()["error"]["code"] == -32700
    assert listener.model_url.endswith("/model/v1") and MODEL_PATH.startswith("/model/v1/")


async def test_a_new_token_replaces_the_old_and_a_revoked_one_stops_working(endpoint) -> None:
    listener, _calls = endpoint
    first = listener.issue("s1")
    second = listener.issue("s1")
    listing = {"jsonrpc": "2.0", "id": 1, "method": "tools/list"}
    async with httpx.AsyncClient(trust_env=False) as client:
        old = await client.post(listener.tools_url, headers={"Authorization": f"Bearer {first}"}, json=listing)
        new = await client.post(listener.tools_url, headers={"Authorization": f"Bearer {second}"}, json=listing)
        listener.revoke("s1")
        gone = await client.post(listener.tools_url, headers={"Authorization": f"Bearer {second}"}, json=listing)
    assert (old.status_code, new.status_code, gone.status_code) == (401, 200, 401)


async def test_a_call_whose_token_is_revoked_while_its_body_comes_in_is_refused(
    endpoint, monkeypatch: pytest.MonkeyPatch
) -> None:
    listener, calls = endpoint
    token = listener.issue("s1")
    looked, go_on = asyncio.Event(), asyncio.Event()
    session_of = listener._session_of

    def watched(offered: str | None) -> str | None:
        looked.set()
        return session_of(offered)

    monkeypatch.setattr(listener, "_session_of", watched)
    call = {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "report", "arguments": {"status": "ready", "summary": "x"}}}

    async def body():
        await go_on.wait()
        yield json.dumps(call).encode("utf-8")

    async with httpx.AsyncClient(trust_env=False) as client:
        posting = asyncio.ensure_future(
            client.post(listener.tools_url, headers={"Authorization": f"Bearer {token}"}, content=body())
        )
        await asyncio.wait_for(looked.wait(), 10)
        # The session is closed and opened again meanwhile: the tools under its
        # id are the new session's, and the old token is no key to them.
        listener.revoke("s1")
        listener.issue("s1")
        go_on.set()
        answer = await asyncio.wait_for(posting, 10)

    assert answer.status_code == 401 and calls == []


async def test_a_call_whose_session_is_opened_again_before_it_is_carried_out_is_refused(
    endpoint, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    listener, calls = endpoint
    tools = listener._tools
    token = listener.issue("s1")
    session_of = listener._session_of
    looks: list[str | None] = []

    def again() -> None:
        # The session is closed and one of the same id opened, between the
        # look at the token and the call being carried out.
        listener.revoke("s1")
        tools.close("s1")
        _open(tools, tmp_path)
        listener.issue("s1")

    def watched(offered: str | None) -> str | None:
        looks.append(offered)
        if len(looks) == 2:
            asyncio.get_running_loop().call_soon(again)
        return session_of(offered)

    monkeypatch.setattr(listener, "_session_of", watched)
    call = _request("tools/call", {"name": "report", "arguments": {"status": "ready", "summary": "x"}})
    async with httpx.AsyncClient(trust_env=False) as client:
        answer = await client.post(listener.tools_url, headers={"Authorization": f"Bearer {token}"}, json=call)

    assert answer.status_code == 200 and answer.json()["result"]["isError"] is True
    assert "closed" in answer.json()["result"]["content"][0]["text"] and calls == []


async def test_a_batch_whose_session_is_opened_again_midway_carries_out_no_more_of_it(tmp_path: Path) -> None:
    calls: list[tuple[str, str, dict[str, Any]]] = []
    host = _callbacks(calls)
    reporting, go_on = asyncio.Event(), asyncio.Event()

    async def slow_report(session_id: str, payload: dict[str, Any]) -> None:
        calls.append(("report", session_id, payload))
        reporting.set()
        await go_on.wait()

    host.report = slow_report
    tools = HostTools(host)
    _open(tools, tmp_path)
    batch = [
        _request("tools/call", {"name": "report", "arguments": {"status": "progress", "summary": "x"}}, 1),
        _request("tools/call", {"name": "note_lesson", "arguments": {"title": "t", "content": "c", "category": "lesson", "confidence": "verified"}}, 2),
    ]
    handling = asyncio.ensure_future(tools.handle("s1", batch))
    await asyncio.wait_for(reporting.wait(), 10)

    # Closed, and one of the same id opened, while the first call of the batch runs:
    # that call is stopped, and the rest of the batch is not carried out.
    tools.close("s1")
    _open(tools, tmp_path)
    go_on.set()
    first, second = await asyncio.wait_for(handling, 10)

    assert first["result"]["isError"] is True and second["result"]["isError"] is True
    assert "closed" in second["result"]["content"][0]["text"]
    assert [name for name, _session, _payload in calls] == ["report"]


async def test_a_call_whose_session_is_opened_again_before_its_tool_starts_is_not_carried_out(tmp_path: Path) -> None:
    calls: list[tuple[str, str, dict[str, Any]]] = []
    tools = HostTools(_callbacks(calls))
    _open(tools, tmp_path)

    def again() -> None:
        tools.close("s1")
        _open(tools, tmp_path)

    # Due before the tool's first step: between the look at the session and the tool starting.
    asyncio.get_running_loop().call_soon(again)
    result = await tools.call("s1", "report", {"status": "ready", "summary": "x"}, offer=tools.offer("s1"))

    assert result["isError"] is True and "closed" in result["content"][0]["text"] and calls == []


async def test_closing_a_session_stops_its_calls_under_way_with_what_they_asked_of_the_host(tmp_path: Path) -> None:
    calls: list[tuple[str, str, dict[str, Any]]] = []
    host = _callbacks(calls)
    asking: asyncio.Event = asyncio.Event()
    stopped: list[bool] = []

    async def slow_report(session_id: str, payload: dict[str, Any]) -> None:
        asking.set()
        try:
            await asyncio.sleep(30)
        except asyncio.CancelledError:
            stopped.append(True)
            raise

    host.report = slow_report
    tools = HostTools(host)
    _open(tools, tmp_path)
    calling = asyncio.ensure_future(tools.call("s1", "report", {"status": "progress", "summary": "x"}))
    await asyncio.wait_for(asking.wait(), 10)

    # Closed while the host works on the call: nothing of it goes on, under its id
    # there may soon be the same commission taken up again.
    tools.close("s1")
    result = await asyncio.wait_for(calling, 10)

    assert result["isError"] is True and "closed" in result["content"][0]["text"] and stopped == [True]


async def test_a_call_of_the_host_that_got_its_answer_just_before_its_session_closed_goes_no_further(
    tmp_path: Path,
) -> None:
    calls: list[tuple[str, str, dict[str, Any]]] = []
    host = _callbacks(calls)
    asking, answered = asyncio.Event(), asyncio.Event()
    went_on: list[str] = []

    async def report(session_id: str, payload: dict[str, Any]) -> None:
        asking.set()
        await answered.wait()
        # What it does once it has its answer: under its id is another session by now.
        went_on.append(session_id)

    host.report = report
    tools = HostTools(host)
    _open(tools, tmp_path)
    calling = asyncio.ensure_future(tools.call("s1", "report", {"status": "progress", "summary": "x"}))
    await asyncio.wait_for(asking.wait(), 10)

    # Its answer is in, and it goes on at its next step; the session is closed and opened again first.
    answered.set()
    tools.close("s1")
    _open(tools, tmp_path)
    result = await asyncio.wait_for(calling, 10)
    await asyncio.sleep(0.05)

    assert went_on == [] and result["isError"] is True and "closed" in result["content"][0]["text"]


async def test_each_dsh_process_has_a_token_of_its_own_that_ends_with_it(endpoint) -> None:
    listener, _calls = endpoint
    one, other = listener.issue_process_token(), listener.issue_process_token()
    model_url = f"{listener.model_url}/chat/completions"
    assert one != other and len(one) >= 32

    async def status(token: str) -> int:
        async with httpx.AsyncClient(trust_env=False) as client:
            answer = await client.post(model_url, headers={"Authorization": f"Bearer {token}"}, json={"model": "nobody"})
        return answer.status_code

    assert (await status(one), await status(other)) == (404, 404)
    listener.revoke_process_token(one)
    assert (await status(one), await status(other)) == (401, 404)
    # A prefix of a token is not the token.
    assert await status(other[:-1]) == 401


async def test_a_call_dsh_gives_up_is_stopped_at_the_host(tmp_path: Path) -> None:
    started = asyncio.Event()
    stopped = asyncio.Event()

    async def search(session_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        started.set()
        try:
            await asyncio.sleep(30)
        except asyncio.CancelledError:
            stopped.set()
            raise
        return {"results": []}

    tools = HostTools(HostCallbacks(web_search=search))
    _open(tools, tmp_path)
    forwarder = ModelForwarder(lambda session_id, usage: None, report_tool="mcp__host__report")
    listener = LocalEndpoint(forwarder=forwarder, tools=tools)
    await listener.start()
    try:
        token = listener.issue("s1")
        call = {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "web_search", "arguments": {"queries": ["q"]}}}
        async with httpx.AsyncClient(trust_env=False, timeout=0.5) as client:
            request = asyncio.ensure_future(client.post(listener.tools_url, headers={"Authorization": f"Bearer {token}"}, json=call))
            await asyncio.wait_for(started.wait(), 5)
            with pytest.raises(httpx.TimeoutException):
                await request
        await asyncio.wait_for(stopped.wait(), 5)
    finally:
        await listener.stop()
        await forwarder.aclose()


async def test_a_listener_that_listens_nowhere_is_closed_and_not_used(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import uvicorn

    stopped: list[bool] = []

    class Nowhere:
        def __init__(self, config: Any) -> None:
            self.started = True
            self.servers: list[Any] = []
            self.should_exit = False

        async def serve(self) -> None:
            while not self.should_exit:
                await asyncio.sleep(0.01)
            stopped.append(True)

    monkeypatch.setattr(uvicorn, "Server", Nowhere)
    listener = LocalEndpoint(
        forwarder=ModelForwarder(lambda session_id, usage: None, report_tool="mcp__host__report"), tools=HostTools(NoHost())
    )

    with pytest.raises(RuntimeError, match="listens nowhere"):
        await listener.start()

    # The listener that came up without a socket was told to stop, and nothing is left of it.
    assert stopped == [True] and listener.port is None


async def test_stopping_the_listener_drops_every_token_and_it_can_be_started_again(endpoint) -> None:
    listener, _calls = endpoint
    session_token = listener.issue("s1")
    process_token = listener.issue_process_token()
    await listener.stop()
    assert listener.port is None
    with pytest.raises(RuntimeError):
        _ = listener.tools_url
    await listener.start()
    # What was issued before it stopped opens nothing after.
    async with httpx.AsyncClient(trust_env=False) as client:
        tools = await client.post(listener.tools_url, headers={"Authorization": f"Bearer {session_token}"}, json={})
        model = await client.post(
            f"{listener.model_url}/chat/completions", headers={"Authorization": f"Bearer {process_token}"}, json={}
        )
    assert tools.status_code == 401 and model.status_code == 401


@pytest.mark.parametrize(
    "how, reached",
    [
        ("takes the answer", True),
        ("hangs up while the call is carried out", False),
        ("hangs up as the answer is ready", False),
        ("hung up a moment before the answer is written, and the write says nothing", False),
        ("hangs up while the answer is written, and the write says nothing", False),
        # What cannot be told: the server knows, the watcher hears of it after the last look.
        ("hung up in the last turn of the loop before the answer is written, and the write says nothing", True),
        ("is gone when the answer is written", False),
    ],
)
async def test_whoever_handed_dsh_something_is_told_whether_the_answer_reached_it(
    endpoint, monkeypatch: pytest.MonkeyPatch, how: str, reached: bool
) -> None:
    from nekoclaw.dsh.endpoint import TOOLS_PATH
    from nekoclaw.dsh.host_tools import ON_ANSWERED

    listener, _calls = endpoint
    token = listener.issue("s1")
    told: list[tuple[str, bool]] = []
    handed, answered, hanging_up = asyncio.Event(), asyncio.Event(), asyncio.Event()
    steps: Any = None
    # Whether whoever watches the request had heard that dsh hung up, as an answer was dropped.
    watcher: list[int] = []
    heard: list[bool] = []
    last_turn = "hung up in the last turn of the loop before the answer is written, and the write says nothing"

    async def handle(session_id: str, payload: Any, **options: Any) -> Any:
        # As two ``wait_agent`` calls of one request, each handing dsh the texts that arrived.
        nonlocal steps
        steps = ON_ANSWERED.get()
        assert steps is not None
        steps.append(lambda delivered: told.append(("the first", delivered)))
        steps.append(lambda delivered: told.append(("the second", delivered)))
        handed.set()
        if how == "hangs up while the call is carried out":
            await asyncio.Event().wait()
        if how == "hung up a moment before the answer is written, and the write says nothing":
            # The server knows as the work finishes; whoever watches the request hears of
            # it a turn of the loop after the answer is ready to be written.
            loop = asyncio.get_running_loop()
            loop.call_soon(loop.call_soon, hanging_up.set)
        if how == last_turn:
            # Later still: neither of the turns of the loop that are waited out before the
            # answer is written brings it to the watcher.
            loop = asyncio.get_running_loop()
            loop.call_soon(loop.call_soon, loop.call_soon, loop.call_soon, hanging_up.set)
        return {"jsonrpc": "2.0", "id": 1, "result": {"content": []}}

    monkeypatch.setattr(listener._tools, "handle", handle)
    body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "wait_agent"}}).encode()
    request: list[dict[str, Any]] = [{"type": "http.request", "body": body, "more_body": False}]

    async def receive() -> dict[str, Any]:
        if request:
            return request.pop(0)
        if how == "is gone when the answer is written":
            await asyncio.Event().wait()
        if how == "takes the answer":
            # As a server does: whoever waits for more of the request is told that the
            # connection is done with once the answer is complete.
            await answered.wait()
        elif how.endswith("and the write says nothing"):
            await hanging_up.wait()
            watcher.append(1)
        else:
            await handed.wait()
        return {"type": "http.disconnect"}

    async def send(message: dict[str, Any]) -> None:
        if how == "is gone when the answer is written":
            raise ConnectionError("the connection is closed")
        if message["type"] == "http.response.body":
            if how == "hangs up while the answer is written, and the write says nothing":
                # The server finds the connection gone as it gets to write: it writes
                # nothing, and returns as if it had.
                hanging_up.set()
                await asyncio.sleep(0)
                await asyncio.sleep(0)
            if how == last_turn:
                # The server has found the connection gone, and writes nothing. Whoever
                # watches the request is told of it apart from that, and a moment later.
                heard.append(bool(watcher))
                return
            answered.set()

    scope = {
        "type": "http",
        "path": TOOLS_PATH,
        "method": "POST",
        "headers": [(b"authorization", f"Bearer {token}".encode())],
    }
    serving = asyncio.ensure_future(listener._asgi(scope, receive, send))
    await asyncio.wait({serving}, timeout=5.0)
    assert serving.done()
    if how == "is gone when the answer is written":
        assert isinstance(serving.exception(), ConnectionError)
    else:
        assert serving.exception() is None
        assert answered.is_set() == (how not in ("hangs up while the call is carried out", last_turn))
    if how == last_turn:
        # The answer did not get there. The server knew; whoever watches the request had not
        # heard of it at the last look: this is the moment that is left, and what was handed
        # over is taken for delivered.
        assert heard == [False] and hanging_up.is_set()

    # (What the call adds to is not the caller's own to see: the call runs in a task of its own.)
    assert ON_ANSWERED.get() is None
    # Both are told, the last to hand over first.
    assert told == [("the second", reached), ("the first", reached)]
    # And one that hands something over after that (the work of a call dsh gave up may still
    # be at it) is told the same on the spot, and is not left without word.
    steps.append(lambda delivered: told.append(("a late one", delivered)))
    assert told[-1] == ("a late one", reached)


async def test_a_wait_that_cannot_be_answered_takes_nothing(tmp_path: Path) -> None:
    asked: list[float] = []

    async def take_input(seconds: float) -> list[str]:
        asked.append(seconds)
        return ["a note that would go nowhere"]

    tools = HostTools(_callbacks([]))
    _open(tools, tmp_path, take_input=take_input)
    wait = {"jsonrpc": "2.0", "method": "tools/call", "params": {"name": "wait_agent", "arguments": {}}}

    # Sent with no id: nothing goes back, so nothing is handed over. Alone, or beside one that is answered.
    assert await tools.handle("s1", wait) is None
    answers = await tools.handle("s1", [wait, _request("ping", request_id=7)])
    assert asked == [] and [answer["id"] for answer in answers] == [7]
    # With an id it is carried out as ever.
    answered = await tools.handle("s1", {**wait, "id": 8})
    assert asked == [30.0] and "a note that would go nowhere" in answered["result"]["content"][0]["text"]
