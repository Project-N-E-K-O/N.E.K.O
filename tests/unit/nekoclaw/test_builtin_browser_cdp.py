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

"""The protocol connection, against a WebSocket server on this computer that plays the browser."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable
from typing import Any

import pytest
from websockets.asyncio.server import serve

from nekoclaw.builtin.browser.cdp import SESSION_GONE_CODE, CdpClosed, CdpConnection, CdpError, CdpTimeout

Behaviour = Callable[[Any, dict[str, Any]], Awaitable[None]]


class Endpoint:
    """The browser's side: records what it is sent and answers as the test tells it to."""

    def __init__(self) -> None:
        self.url = ""
        self.received: list[dict[str, Any]] = []
        self.raw: list[str] = []
        self.sockets: list[Any] = []
        self.behaviours: dict[str, Behaviour] = {}
        self.disconnected = asyncio.Event()

    async def handler(self, websocket: Any) -> None:
        self.sockets.append(websocket)
        try:
            async for raw in websocket:
                self.raw.append(raw)
                message = json.loads(raw)
                self.received.append(message)
                behaviour = self.behaviours.get(message["method"])
                if behaviour is None:
                    await self.answer(websocket, message, {"echo": message["method"]})
                else:
                    await behaviour(websocket, message)
        finally:
            self.disconnected.set()

    @staticmethod
    async def answer(websocket: Any, message: dict[str, Any], result: Any) -> None:
        await websocket.send(json.dumps({"id": message["id"], "result": result}))

    async def emit(self, payload: Any) -> None:
        await self.sockets[-1].send(payload if isinstance(payload, str) else json.dumps(payload))


async def _silent(websocket: Any, message: dict[str, Any]) -> None:
    """Never answer."""


async def _until(predicate: Callable[[], Any], what: str) -> None:
    deadline = asyncio.get_running_loop().time() + 10.0
    while not predicate():
        if asyncio.get_running_loop().time() > deadline:
            raise AssertionError(f"{what} did not happen")
        await asyncio.sleep(0.005)


@pytest.fixture
async def endpoint() -> Any:
    fake = Endpoint()
    server = await serve(fake.handler, "127.0.0.1", 0, max_size=None)
    port = server.sockets[0].getsockname()[1]
    fake.url = f"ws://127.0.0.1:{port}/devtools/browser/test"
    try:
        yield fake
    finally:
        server.close()
        await server.wait_closed()


@pytest.fixture
async def connection(endpoint: Endpoint) -> Any:
    opened = await CdpConnection.connect(endpoint.url)
    try:
        yield opened
    finally:
        await opened.close()


# ---------------------------------------------------------------------------
# Commands and their answers
# ---------------------------------------------------------------------------


async def test_a_command_is_a_json_message_with_an_id_and_gets_the_result_of_its_answer(
    endpoint: Endpoint, connection: CdpConnection
) -> None:
    assert await connection.send("Browser.getVersion") == {"echo": "Browser.getVersion"}
    assert await connection.send("Page.navigate", {"url": "http://127.0.0.1/"}, session_id="S1") == {"echo": "Page.navigate"}
    assert endpoint.received == [
        {"id": 1, "method": "Browser.getVersion"},
        {"id": 2, "method": "Page.navigate", "params": {"url": "http://127.0.0.1/"}, "sessionId": "S1"},
    ]


async def test_empty_parameters_and_an_empty_session_are_left_out_of_the_message(
    endpoint: Endpoint, connection: CdpConnection
) -> None:
    await connection.send("Page.enable", {}, session_id="")
    await connection.send("Page.enable", None, session_id=None)
    assert endpoint.received == [{"id": 1, "method": "Page.enable"}, {"id": 2, "method": "Page.enable"}]


async def test_text_beyond_ascii_is_sent_as_it_is(endpoint: Endpoint, connection: CdpConnection) -> None:
    await connection.send("Input.insertText", {"text": "猫 café"})
    assert "猫 café" in endpoint.raw[0]
    assert endpoint.received[0]["params"] == {"text": "猫 café"}


async def test_answers_find_their_commands_by_id_whatever_order_they_come_in(
    endpoint: Endpoint, connection: CdpConnection
) -> None:
    held: list[tuple[Any, dict[str, Any]]] = []

    async def hold(websocket: Any, message: dict[str, Any]) -> None:
        held.append((websocket, message))

    async def answer_all_backwards(websocket: Any, message: dict[str, Any]) -> None:
        await endpoint.answer(websocket, message, {"for": "fast"})
        for socket, waiting in reversed(held):
            await endpoint.answer(socket, waiting, {"for": waiting["params"]["name"]})

    endpoint.behaviours["Test.slow"] = hold
    endpoint.behaviours["Test.fast"] = answer_all_backwards
    first = asyncio.ensure_future(connection.send("Test.slow", {"name": "first"}))
    second = asyncio.ensure_future(connection.send("Test.slow", {"name": "second"}))
    await _until(lambda: len(held) == 2, "both commands arriving")
    assert await connection.send("Test.fast") == {"for": "fast"}
    assert await first == {"for": "first"}
    assert await second == {"for": "second"}


async def test_an_answer_without_a_result_object_is_an_empty_result(endpoint: Endpoint, connection: CdpConnection) -> None:
    async def bare(websocket: Any, message: dict[str, Any]) -> None:
        await websocket.send(json.dumps({"id": message["id"]}))

    async def listed(websocket: Any, message: dict[str, Any]) -> None:
        await websocket.send(json.dumps({"id": message["id"], "result": [1, 2]}))

    endpoint.behaviours["Test.bare"] = bare
    endpoint.behaviours["Test.listed"] = listed
    assert await connection.send("Test.bare") == {}
    assert await connection.send("Test.listed") == {}


async def test_an_answer_as_large_as_a_screenshot_arrives_whole(endpoint: Endpoint, connection: CdpConnection) -> None:
    picture = "A" * (3 * 1024 * 1024)

    async def big(websocket: Any, message: dict[str, Any]) -> None:
        await endpoint.answer(websocket, message, {"data": picture})

    endpoint.behaviours["Page.captureScreenshot"] = big
    assert (await connection.send("Page.captureScreenshot"))["data"] == picture


async def test_an_error_answer_raises_with_the_method_the_code_and_the_message(
    endpoint: Endpoint, connection: CdpConnection
) -> None:
    async def refuse(websocket: Any, message: dict[str, Any]) -> None:
        await websocket.send(json.dumps({"id": message["id"], "error": {"code": -32000, "message": "No node with given id found"}}))

    endpoint.behaviours["DOM.resolveNode"] = refuse
    with pytest.raises(CdpError) as caught:
        await connection.send("DOM.resolveNode", {"backendNodeId": 7})
    assert caught.value.method == "DOM.resolveNode"
    assert caught.value.code == -32000
    assert caught.value.message == "No node with given id found"
    assert str(caught.value) == "DOM.resolveNode: No node with given id found"
    # The connection goes on working.
    assert await connection.send("Page.enable") == {"echo": "Page.enable"}
    assert not connection.closed


async def test_what_waits_for_a_session_that_is_gone_fails_at_once_and_nothing_else_does(
    endpoint: Endpoint, connection: CdpConnection
) -> None:
    endpoint.behaviours["Runtime.evaluate"] = _silent
    endpoint.behaviours["Page.reload"] = _silent
    first = asyncio.ensure_future(connection.send("Runtime.evaluate", {"expression": "1"}, session_id="S1"))
    second = asyncio.ensure_future(connection.send("Page.reload", session_id="S1"))
    other = asyncio.ensure_future(connection.send("Runtime.evaluate", {"expression": "2"}, session_id="S2"))
    whole = asyncio.ensure_future(connection.send("Page.reload"))
    await _until(lambda: len(endpoint.received) == 4, "the commands being sent")

    # The page of S1 closed: the browser will never answer what was sent to it.
    connection.fail_session("S1")
    for waiting, method in ((first, "Runtime.evaluate"), (second, "Page.reload")):
        with pytest.raises(CdpError) as caught:
            await asyncio.wait_for(waiting, 1.0)
        assert (caught.value.method, caught.value.code) == (method, SESSION_GONE_CODE)
    await asyncio.sleep(0.02)
    assert not other.done() and not whole.done()
    other.cancel()
    whole.cancel()
    await asyncio.gather(other, whole, return_exceptions=True)

    # With nothing waiting it does nothing, and the connection goes on working.
    connection.fail_session("S1")
    connection.fail_session("S9")
    assert await connection.send("Page.enable", session_id="S1") == {"echo": "Page.enable"}
    assert not connection.closed


async def test_no_answer_in_time_raises_and_a_late_answer_is_dropped(endpoint: Endpoint, connection: CdpConnection) -> None:
    waiting: list[tuple[Any, dict[str, Any]]] = []

    async def hold(websocket: Any, message: dict[str, Any]) -> None:
        waiting.append((websocket, message))

    endpoint.behaviours["Runtime.evaluate"] = hold
    with pytest.raises(CdpTimeout, match="Runtime.evaluate got no answer within"):
        await connection.send("Runtime.evaluate", {"expression": "while(true){}"}, timeout=0.05)
    assert not connection.closed
    # The answer comes after all: nobody waits for it, and the next command gets its own.
    socket, message = waiting[0]
    await endpoint.answer(socket, message, {"late": True})
    assert await connection.send("Page.enable") == {"echo": "Page.enable"}


async def test_frames_that_are_not_protocol_messages_are_ignored(endpoint: Endpoint, connection: CdpConnection) -> None:
    events: list[str] = []
    connection.on_event(lambda method, params, session_id: events.append(method))
    await connection.send("Page.enable")
    await endpoint.emit("this is not json")
    await endpoint.emit([1, 2, 3])
    await endpoint.emit({"neither": "an answer nor an event"})
    await endpoint.emit({"id": 99, "result": {"for": "nobody"}})
    await endpoint.emit({"method": 7})
    await endpoint.emit({"method": "Test.afterTheNoise"})
    await _until(lambda: events == ["Test.afterTheNoise"], "the event after the noise")
    assert await connection.send("Page.enable") == {"echo": "Page.enable"}


# ---------------------------------------------------------------------------
# Events
# ---------------------------------------------------------------------------


async def test_events_reach_every_listener_in_order_with_the_session_they_came_from(
    endpoint: Endpoint, connection: CdpConnection
) -> None:
    first: list[tuple[str, dict[str, Any], str | None]] = []
    second: list[str] = []
    connection.on_event(lambda method, params, session_id: first.append((method, params, session_id)))
    connection.on_event(lambda method, params, session_id: second.append(method))
    await connection.send("Page.enable")
    await endpoint.emit({"method": "Target.targetCreated", "params": {"targetInfo": {"targetId": "T1"}}})
    await endpoint.emit({"method": "Page.frameNavigated", "params": {"frame": {"id": "F1"}}, "sessionId": "S1"})
    await endpoint.emit({"method": "Page.loadEventFired", "sessionId": "S2"})
    await endpoint.emit({"method": "Page.odd", "params": "not an object", "sessionId": "S2"})
    await _until(lambda: len(first) == 4, "the events arriving")
    assert first == [
        ("Target.targetCreated", {"targetInfo": {"targetId": "T1"}}, None),
        ("Page.frameNavigated", {"frame": {"id": "F1"}}, "S1"),
        ("Page.loadEventFired", {}, "S2"),
        ("Page.odd", {}, "S2"),
    ]
    assert second == ["Target.targetCreated", "Page.frameNavigated", "Page.loadEventFired", "Page.odd"]


async def test_a_listener_that_was_removed_is_no_longer_called(endpoint: Endpoint, connection: CdpConnection) -> None:
    kept: list[str] = []
    removed: list[str] = []
    connection.on_event(lambda method, params, session_id: kept.append(method))
    remove = connection.on_event(lambda method, params, session_id: removed.append(method))
    await connection.send("Page.enable")
    await endpoint.emit({"method": "Test.one"})
    await _until(lambda: kept == ["Test.one"], "the first event")
    remove()
    remove()  # Removing twice is harmless.
    await endpoint.emit({"method": "Test.two"})
    await _until(lambda: kept == ["Test.one", "Test.two"], "the second event")
    assert removed == ["Test.one"]


async def test_a_listener_that_raises_does_not_stop_the_others_or_the_connection(
    endpoint: Endpoint, connection: CdpConnection
) -> None:
    after: list[str] = []

    def broken(method: str, params: dict[str, Any], session_id: str | None) -> None:
        raise RuntimeError("a listener with a bug")

    connection.on_event(broken)
    connection.on_event(lambda method, params, session_id: after.append(method))
    await connection.send("Page.enable")
    await endpoint.emit({"method": "Test.one"})
    await endpoint.emit({"method": "Test.two"})
    await _until(lambda: after == ["Test.one", "Test.two"], "both events reaching the second listener")
    assert await connection.send("Page.enable") == {"echo": "Page.enable"}


async def test_an_event_that_arrives_while_a_command_waits_is_delivered_before_the_answer(
    endpoint: Endpoint, connection: CdpConnection
) -> None:
    order: list[str] = []

    async def event_then_answer(websocket: Any, message: dict[str, Any]) -> None:
        await websocket.send(json.dumps({"method": "Page.javascriptDialogOpening", "params": {"type": "alert"}, "sessionId": "S1"}))
        await endpoint.answer(websocket, message, {})

    endpoint.behaviours["Input.dispatchMouseEvent"] = event_then_answer
    connection.on_event(lambda method, params, session_id: order.append(method))
    await connection.send("Input.dispatchMouseEvent", {"type": "mouseReleased"}, session_id="S1")
    order.append("answered")
    assert order == ["Page.javascriptDialogOpening", "answered"]


# ---------------------------------------------------------------------------
# The connection going away
# ---------------------------------------------------------------------------


async def test_the_browser_closing_fails_what_waits_and_tells_the_close_listeners_once(
    endpoint: Endpoint, connection: CdpConnection
) -> None:
    endpoint.behaviours["Test.never"] = _silent
    told: list[str] = []
    connection.on_close(lambda: told.append("first"))
    connection.on_close(lambda: told.append("second"))
    one = asyncio.ensure_future(connection.send("Test.never", timeout=None))
    two = asyncio.ensure_future(connection.send("Test.never", timeout=30.0))
    await _until(lambda: len(endpoint.received) == 2, "both commands arriving")
    assert not connection.closed
    await endpoint.sockets[0].close()
    # Bounded: a command left waiting for ever would otherwise hang the test instead of failing it.
    with pytest.raises(CdpClosed):
        await asyncio.wait_for(one, 10.0)
    with pytest.raises(CdpClosed):
        await asyncio.wait_for(two, 10.0)
    assert connection.closed
    assert told == ["first", "second"]
    with pytest.raises(CdpClosed, match="the connection to the browser is closed"):
        await connection.send("Page.enable")
    # Nothing more went out, and closing afterwards tells nobody again.
    assert len(endpoint.received) == 2
    await connection.close()
    assert told == ["first", "second"]


async def test_a_close_listener_that_raises_does_not_stop_the_others(endpoint: Endpoint, connection: CdpConnection) -> None:
    told: list[str] = []

    def broken() -> None:
        raise RuntimeError("a listener with a bug")

    connection.on_close(broken)
    connection.on_close(lambda: told.append("after"))
    await connection.send("Page.enable")
    await endpoint.sockets[0].close()
    await _until(lambda: connection.closed, "the connection noticing")
    assert told == ["after"]


async def test_a_close_listener_added_after_the_end_is_called_at_once(endpoint: Endpoint, connection: CdpConnection) -> None:
    await connection.close()
    told: list[str] = []
    connection.on_close(lambda: told.append("late"))
    assert told == ["late"]


async def test_closing_fails_what_waits_closes_the_socket_and_may_be_repeated(endpoint: Endpoint) -> None:
    connection = await CdpConnection.connect(endpoint.url)
    endpoint.behaviours["Test.never"] = _silent
    told: list[str] = []
    connection.on_close(lambda: told.append("closed"))
    waiting = asyncio.ensure_future(connection.send("Test.never", timeout=None))
    await _until(lambda: len(endpoint.received) == 1, "the command arriving")
    await connection.close()
    with pytest.raises(CdpClosed):
        await asyncio.wait_for(waiting, 10.0)
    assert connection.closed
    assert told == ["closed"]
    await asyncio.wait_for(endpoint.disconnected.wait(), 10.0)
    await connection.close()
    await connection.close()
    assert told == ["closed"]
    with pytest.raises(CdpClosed):
        await connection.send("Page.enable")


async def test_a_command_cancelled_while_it_waits_leaves_the_connection_usable(
    endpoint: Endpoint, connection: CdpConnection
) -> None:
    endpoint.behaviours["Test.never"] = _silent
    waiting = asyncio.ensure_future(connection.send("Test.never", timeout=None))
    await _until(lambda: len(endpoint.received) == 1, "the command arriving")
    waiting.cancel()
    with pytest.raises(asyncio.CancelledError):
        await waiting
    assert not connection.closed
    assert await connection.send("Page.enable") == {"echo": "Page.enable"}


class _BrokenSocket:
    """A socket that reads nothing and cannot be written to."""

    def __init__(self) -> None:
        self.closed = False

    def __aiter__(self) -> "_BrokenSocket":
        return self

    async def __anext__(self) -> str:
        await asyncio.Event().wait()
        raise StopAsyncIteration

    async def send(self, text: str) -> None:
        raise ConnectionResetError("the pipe is gone")

    async def close(self) -> None:
        self.closed = True


async def test_a_socket_that_fails_on_sending_ends_the_connection() -> None:
    socket = _BrokenSocket()
    connection = CdpConnection(socket)
    told: list[str] = []
    connection.on_close(lambda: told.append("closed"))
    with pytest.raises(CdpClosed, match="the connection to the browser broke: ConnectionResetError"):
        await connection.send("Page.enable")
    assert connection.closed
    assert told == ["closed"]
    with pytest.raises(CdpClosed, match="is closed"):
        await connection.send("Page.enable")
    await connection.close()
    assert socket.closed


class _FullSocket:
    """A socket whose other end reads nothing: sending waits for as long as it is let, or as long as told."""

    def __init__(self, sending_takes: float | None = None) -> None:
        self.sending_takes = sending_takes
        self.sent: list[str] = []

    def __aiter__(self) -> "_FullSocket":
        return self

    async def __anext__(self) -> str:
        await asyncio.Event().wait()
        raise StopAsyncIteration

    async def send(self, text: str) -> None:
        if self.sending_takes is None:
            await asyncio.Event().wait()
        await asyncio.sleep(self.sending_takes or 0.0)
        self.sent.append(text)

    async def close(self) -> None:
        return None


async def test_a_browser_that_reads_nothing_is_waited_for_no_longer_than_one_that_does_not_answer() -> None:
    connection = CdpConnection(_FullSocket())
    try:
        started = asyncio.get_running_loop().time()
        with pytest.raises(CdpTimeout, match="Page.enable could not be sent within 0s"):
            await asyncio.wait_for(connection.send("Page.enable", timeout=0.2), 5.0)
        assert asyncio.get_running_loop().time() - started < 3.0
        # Nothing waits for an answer to it, and the connection is as usable as after any command that timed out.
        assert connection._pending == {} and not connection.closed
    finally:
        await connection.close()


async def test_the_time_sending_took_is_not_given_to_the_answer_once_more() -> None:
    socket = _FullSocket(sending_takes=1.0)
    connection = CdpConnection(socket)
    try:
        started = asyncio.get_running_loop().time()
        with pytest.raises(CdpTimeout, match="Page.enable got no answer within 1s"):
            await asyncio.wait_for(connection.send("Page.enable", timeout=1.4), 10.0)
        took = asyncio.get_running_loop().time() - started
        # Sent after 1.0s, it is waited for 0.4s more: 2.4s would be the time of the command twice over.
        assert len(socket.sent) == 1 and 1.3 < took < 2.1, took
    finally:
        await connection.close()


class _TimedOutSocket(_BrokenSocket):
    async def send(self, text: str) -> None:
        # What the system raises for a connection that went dead: not the time of the command running out.
        raise TimeoutError(110, "Connection timed out")


async def test_a_socket_the_system_gave_up_on_is_a_connection_that_broke_and_not_a_command_out_of_time() -> None:
    connection = CdpConnection(_TimedOutSocket())
    with pytest.raises(CdpClosed, match="the connection to the browser broke: TimeoutError"):
        await connection.send("Page.enable", timeout=30.0)
    assert connection.closed
