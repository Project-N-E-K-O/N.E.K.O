"""Opt-in realtime wire and arbiter decision trace (NEKO_REALTIME_WIRE_TRACE).

Contracts, each written so it can be falsified:

1.  Off by default, and off means silent: neither the transport nor the
    arbiter emits a trace line. The capture itself is probed, so an empty
    result cannot come from a logger that never reached the handler.
2.  On, every send and receive becomes one parseable JSON record carrying the
    structural fields, and no record carries conversation content: text,
    instructions, tool arguments or outputs, transcripts, or audio.
3.  Audio appends are counted, never logged one by one; streaming deltas log
    their first frame and fold the rest into counts on the terminal.
4.  A malformed event or a failing logger cannot make tracing raise into the
    transport, and the send still goes out.
5.  The arbiter names the branch a terminal took: a response.id that differs
    from the owner's known id reads ``orphan_mismatch``.
"""

import asyncio
import json
import logging

import pytest

from main_logic.omni_realtime_client import OmniRealtimeClient
from main_logic.omni_realtime_client import _response_arbiter as arbiter_module
from main_logic.omni_realtime_client import _shared as shared_module
from main_logic.omni_realtime_client._protocol_capabilities import (
    LANLAN_APP_REALTIME_PROTOCOL_CAPABILITIES,
)
from main_logic.omni_realtime_client._response_arbiter import RealtimeResponseArbiter
from main_logic.omni_realtime_client._shared import realtime_wire_trace_enabled
from main_logic.omni_realtime_client._wire_trace import (
    ARBITER_TRACE_PREFIX,
    WIRE_TRACE_PREFIX,
    RealtimeWireTrace,
)

WIRE_TRACE_ENV_VAR = "NEKO_REALTIME_WIRE_TRACE"

PRIVATE_TEXT = "private-user-sentence-7f3a"
PRIVATE_INSTRUCTIONS = "private-system-prompt-91bc"
PRIVATE_ARGUMENT = "private-tool-argument-55de"
PRIVATE_ARGUMENTS = '{"query": "private-tool-argument-55de"}'
PRIVATE_OUTPUT = "private-tool-output-a9e1"
PRIVATE_TRANSCRIPT = "private-transcript-c0ff"
PRIVATE_AUDIO = "cHJpdmF0ZS1hdWRpby1ieXRlcw=="
PRIVATE_TOOL_DESCRIPTION = "private-tool-description-3b7d"
PRIVATE_STRINGS = (
    PRIVATE_TEXT,
    PRIVATE_INSTRUCTIONS,
    PRIVATE_ARGUMENT,
    PRIVATE_OUTPUT,
    PRIVATE_TRANSCRIPT,
    PRIVATE_AUDIO,
    PRIVATE_TOOL_DESCRIPTION,
)


class _QueueSocket:
    def __init__(self) -> None:
        self._messages: asyncio.Queue = asyncio.Queue()
        self.sent: list[dict] = []
        self.closed = False

    def __aiter__(self):
        return self

    async def __anext__(self):
        message = await self._messages.get()
        if message is None:
            raise StopAsyncIteration
        return json.dumps(message)

    def feed(self, event: dict) -> None:
        self._messages.put_nowait(event)

    def finish(self) -> None:
        self._messages.put_nowait(None)

    async def send(self, payload: str) -> None:
        self.sent.append(json.loads(payload))

    async def close(self) -> None:
        self.closed = True


class _ExplodingLogger:
    def info(self, *args, **kwargs) -> None:
        raise RuntimeError("logger exploded")


class _Unserializable:
    pass


@pytest.fixture(autouse=True)
def _trace_loggers_reach_caplog(monkeypatch):
    # Both loggers live under N.E.K.O.Main. Any test module that imports
    # main_logic.core runs setup_logging at collection, which stops N.E.K.O
    # propagating to root -- and caplog only listens on root. Re-open the
    # chain for the duration of each test.
    for logger in (shared_module.logger, arbiter_module.logger):
        while logger is not None:
            monkeypatch.setattr(logger, "propagate", True)
            logger = logger.parent


def _capture(caplog) -> None:
    caplog.set_level(logging.DEBUG, logger=shared_module.logger.name)
    caplog.set_level(logging.DEBUG, logger=arbiter_module.logger.name)


def _messages(caplog) -> list[str]:
    return [record.getMessage() for record in caplog.records]


def _trace_records(caplog, prefix: str) -> list[dict]:
    records = []
    for message in _messages(caplog):
        if message.startswith(prefix):
            records.append(json.loads(message[len(prefix):]))
    return records


def _assert_no_private_content(caplog) -> None:
    for message in _messages(caplog):
        if WIRE_TRACE_PREFIX not in message and ARBITER_TRACE_PREFIX not in message:
            continue
        for private in PRIVATE_STRINGS:
            assert private not in message, (private, message)


def _make_client() -> OmniRealtimeClient:
    return OmniRealtimeClient(
        "wss://example.invalid/realtime",
        "test-key",
        model="gpt-realtime",
        api_type="gpt",
    )


def _session_update_event() -> dict:
    return {
        "type": "session.update",
        "event_id": "event-session-1",
        "session": {
            "instructions": PRIVATE_INSTRUCTIONS,
            "modalities": ["text", "audio"],
            "turn_detection": None,
            "tools": [
                {
                    "type": "function",
                    "function": {
                        "name": "recall_memory",
                        "description": PRIVATE_TOOL_DESCRIPTION,
                        "parameters": {"type": "object", "properties": {}},
                    },
                },
                {
                    "type": "function",
                    "name": "minecraft_task",
                    "description": PRIVATE_TOOL_DESCRIPTION,
                },
            ],
        },
    }


def _user_item_event() -> dict:
    return {
        "type": "conversation.item.create",
        "event_id": "event-item-1",
        "previous_item_id": "item-prev",
        "item": {
            "id": "item-user-1",
            "type": "message",
            "role": "user",
            "content": [
                {"type": "input_text", "text": PRIVATE_TEXT},
                {"type": "input_audio", "audio": PRIVATE_AUDIO},
            ],
        },
    }


def _tool_output_item_event() -> dict:
    return {
        "type": "conversation.item.create",
        "event_id": "event-output-1",
        "item": {
            "id": "item-out-1",
            "type": "function_call_output",
            "call_id": "call-1",
            "output": PRIVATE_OUTPUT,
        },
    }


def _response_create_event() -> dict:
    return {
        "type": "response.create",
        "event_id": "event-create-1",
        "response": {
            "instructions": PRIVATE_INSTRUCTIONS,
            "modalities": ["text", "audio"],
        },
    }


@pytest.mark.unit
@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (None, False),
        ("", False),
        ("0", False),
        ("false", False),
        ("1", True),
        ("true", True),
        (" YES ", True),
        ("on", True),
    ],
)
def test_wire_trace_switch_reads_the_documented_values(monkeypatch, raw, expected):
    if raw is None:
        monkeypatch.delenv(WIRE_TRACE_ENV_VAR, raising=False)
    else:
        monkeypatch.setenv(WIRE_TRACE_ENV_VAR, raw)
    assert realtime_wire_trace_enabled() is expected


@pytest.mark.unit
@pytest.mark.asyncio
async def test_flag_off_emits_no_trace_lines(monkeypatch, caplog):
    monkeypatch.delenv(WIRE_TRACE_ENV_VAR, raising=False)
    _capture(caplog)
    client = _make_client()
    assert client._wire_trace is None
    assert client._response_arbiter._trace is False
    assert client._response_arbiter._trace_tag is None
    assert client._response_arbiter._trace_generation is None
    socket = _QueueSocket()
    client.ws = socket
    client._on_connection_attached()

    assert await client.send_event(_session_update_event())
    assert await client.send_event(_user_item_event())
    assert await client.send_event(
        {"type": "input_audio_buffer.append", "audio": PRIVATE_AUDIO}
    )
    receive_loop = asyncio.create_task(client.handle_messages())
    socket.feed({"type": "session.updated", "session": {"modalities": ["audio"]}})
    socket.feed({"type": "response.created", "response": {"id": "resp-A"}})
    socket.feed({"type": "response.done", "response": {"id": "resp-A"}})
    socket.finish()
    await asyncio.wait_for(receive_loop, timeout=1)
    assert len(socket.sent) == 3

    async def send(_event):
        return None

    arbiter = RealtimeResponseArbiter(send)
    try:
        ticket = await arbiter.enqueue(source="owner")
        await asyncio.wait_for(ticket.sent, 0.5)
        arbiter.notify_response_created(
            {"type": "response.created", "response": {"id": "resp-owner"}}
        )
        arbiter.notify_response_terminal(
            {"type": "response.done", "response": {"id": "resp-other"}}
        )
    finally:
        await arbiter.shutdown()

    # Prove both loggers reach the capture, so the emptiness below is real.
    shared_module.logger.info("transport-capture-probe")
    arbiter_module.logger.info("arbiter-capture-probe")
    messages = _messages(caplog)
    assert "transport-capture-probe" in messages
    assert "arbiter-capture-probe" in messages
    assert not any(WIRE_TRACE_PREFIX in message for message in messages)
    assert not any(ARBITER_TRACE_PREFIX in message for message in messages)


@pytest.mark.unit
@pytest.mark.asyncio
async def test_flag_on_send_records_structure_without_content(monkeypatch, caplog):
    monkeypatch.setenv(WIRE_TRACE_ENV_VAR, "1")
    _capture(caplog)
    client = _make_client()
    assert client._wire_trace is not None
    assert client._response_arbiter._trace is True
    socket = _QueueSocket()
    client.ws = socket
    client._on_connection_attached()

    assert await client.send_event(_session_update_event())
    for _ in range(3):
        assert await client.send_event(
            {"type": "input_audio_buffer.append", "audio": PRIVATE_AUDIO}
        )
    assert await client.send_event(_user_item_event())
    assert await client.send_event(_tool_output_item_event())
    assert await client.send_event(_response_create_event())
    assert await client.send_event(
        {"type": "response.cancel", "event_id": "event-cancel-1", "response_id": "resp-9"}
    )
    assert await client.send_event(
        {
            "type": "conversation.item.delete",
            "event_id": "event-delete-1",
            "item_id": "item-user-1",
        }
    )

    raw_lines = [
        message[len(WIRE_TRACE_PREFIX):]
        for message in _messages(caplog)
        if message.startswith(WIRE_TRACE_PREFIX)
    ]
    sends = [json.loads(line) for line in raw_lines]
    # Compact JSON, one record per line.
    assert raw_lines[0] == json.dumps(
        sends[0], ensure_ascii=False, separators=(",", ":")
    )
    # Appends are counted rather than logged individually.
    assert [record["type"] for record in sends] == [
        "session.update",
        "conversation.item.create",
        "conversation.item.create",
        "response.create",
        "response.cancel",
        "conversation.item.delete",
    ]
    assert all(record["dir"] == "send" for record in sends)
    session, item, output, create, cancel, delete = sends

    assert session["gen"] == client._connection_generation
    assert isinstance(session["t"], float)
    assert session["event_id"] == "event-session-1"
    assert session["tools_count"] == 2
    assert session["tool_names"] == ["recall_memory", "minecraft_task"]
    assert session["turn_detection"] is None
    assert session["modalities"] == ["text", "audio"]
    assert "audio_appends" not in session

    assert item["item.id"] == "item-user-1"
    assert item["item.type"] == "message"
    assert item["item.role"] == "user"
    assert item["previous_item_id"] == "item-prev"
    assert item["content_types"] == ["input_text", "input_audio"]
    assert item["audio_appends"] == 3
    assert item["audio_appends_total"] == 3

    assert output["item.type"] == "function_call_output"
    assert output["item.call_id"] == "call-1"
    assert "audio_appends" not in output

    assert create["event_id"] == "event-create-1"
    assert create["response_keys"] == ["instructions", "modalities"]
    assert cancel["response_id"] == "resp-9"
    assert delete["item_id"] == "item-user-1"

    # The transport sent exactly what it was given, appends included.
    assert len(socket.sent) == 9
    _assert_no_private_content(caplog)


@pytest.mark.unit
@pytest.mark.asyncio
async def test_flag_on_receive_records_before_dispatch_without_content(
    monkeypatch, caplog
):
    monkeypatch.setenv(WIRE_TRACE_ENV_VAR, "1")
    _capture(caplog)
    client = _make_client()
    socket = _QueueSocket()
    client.ws = socket
    client._on_connection_attached()
    receive_loop = asyncio.create_task(client.handle_messages())

    socket.feed(
        {
            "type": "session.updated",
            "event_id": "srv-1",
            "session": {
                "instructions": PRIVATE_INSTRUCTIONS,
                "tools": [{"type": "function", "name": "recall_memory"}],
                "turn_detection": {"type": "server_vad"},
                "modalities": ["audio"],
            },
        }
    )
    socket.feed(
        {"type": "response.created", "event_id": "srv-2", "response": {"id": "resp-A"}}
    )
    for index in range(3):
        socket.feed(
            {
                "type": "response.audio_transcript.delta",
                "event_id": "srv-t%d" % index,
                "response_id": "resp-A",
                "item_id": "item-a",
                "output_index": 0,
                "content_index": 0,
                "delta": PRIVATE_TRANSCRIPT,
            }
        )
    for index in range(2):
        socket.feed(
            {
                "type": "response.audio.delta",
                "event_id": "srv-a%d" % index,
                "response_id": "resp-A",
                "item_id": "item-a",
                "output_index": 0,
                "content_index": 0,
                "delta": PRIVATE_AUDIO,
            }
        )
    socket.feed(
        {
            "type": "response.done",
            "event_id": "srv-9",
            "response": {
                "id": "resp-A",
                "status": "completed",
                "output": [
                    {
                        "type": "message",
                        "id": "item-a",
                        "role": "assistant",
                        "status": "completed",
                        "content": [
                            {"type": "audio", "transcript": PRIVATE_TRANSCRIPT}
                        ],
                    }
                ],
            },
        }
    )
    socket.finish()
    await asyncio.wait_for(receive_loop, timeout=1)

    receives = [
        record
        for record in _trace_records(caplog, WIRE_TRACE_PREFIX)
        if record["dir"] == "recv"
    ]
    assert [record["type"] for record in receives] == [
        "session.updated",
        "response.created",
        "response.audio_transcript.delta",
        "response.audio.delta",
        "response.done",
    ]
    session, created, transcript, audio, done = receives
    assert session["tool_names"] == ["recall_memory"]
    assert session["turn_detection"] == "server_vad"
    assert created["response.id"] == "resp-A"
    assert transcript["first_delta"] is True
    assert transcript["response_id"] == "resp-A"
    assert transcript["item_id"] == "item-a"
    assert audio["first_delta"] is True
    assert done["response.id"] == "resp-A"
    assert done["response.status"] == "completed"
    assert done["output"] == [
        {"type": "message", "id": "item-a", "status": "completed"}
    ]
    assert done["delta_counts"] == {
        "response.audio_transcript.delta": 3,
        "response.audio.delta": 2,
    }
    assert "other_delta_counts" not in done

    terminals = [
        record
        for record in _trace_records(caplog, ARBITER_TRACE_PREFIX)
        if record["decision"] == "terminal"
    ]
    assert terminals and terminals[-1]["response.id"] == "resp-A"
    _assert_no_private_content(caplog)


@pytest.mark.unit
def test_recorder_folds_function_call_deltas_and_truncates_errors(caplog):
    _capture(caplog)
    trace = RealtimeWireTrace(shared_module.logger)
    for _ in range(4):
        trace.record_recv(
            {
                "type": "response.function_call_arguments.delta",
                "response_id": "resp-fc",
                "call_id": "call-1",
                "name": "recall_memory",
                "delta": PRIVATE_ARGUMENTS,
            },
            generation=3,
        )
    trace.record_recv(
        {
            "type": "response.function_call_arguments.delta",
            "response_id": "resp-fc",
            "call_id": "call-2",
            "name": "recall_memory",
            "delta": PRIVATE_ARGUMENTS,
        },
        generation=3,
    )
    trace.record_recv(
        {
            "type": "response.function_call_arguments.done",
            "response_id": "resp-fc",
            "call_id": "call-1",
            "name": "recall_memory",
            "arguments": PRIVATE_ARGUMENTS,
        },
        generation=3,
    )
    trace.record_recv(
        {
            "type": "response.done",
            "response": {
                "id": "resp-done",
                "status": "completed",
                "output": [
                    {
                        "type": "function_call",
                        "id": "item-fc",
                        "call_id": "call-1",
                        "name": "recall_memory",
                        "arguments": PRIVATE_ARGUMENTS,
                        "status": "completed",
                    }
                ],
            },
        },
        generation=3,
    )
    trace.record_recv(
        {
            "type": "error",
            "error": {
                "type": "invalid_request_error",
                "code": "bad_thing",
                "event_id": "event-x",
                "message": "m" * 300,
            },
        },
        generation=3,
    )

    records = _trace_records(caplog, WIRE_TRACE_PREFIX)
    assert [record["type"] for record in records] == [
        "response.function_call_arguments.delta",
        "response.function_call_arguments.delta",
        "response.function_call_arguments.done",
        "response.done",
        "error",
    ]
    assert [record["call_id"] for record in records[:3]] == ["call-1", "call-2", "call-1"]
    assert records[2]["response_id"] == "resp-fc"
    assert records[2]["name"] == "recall_memory"
    done = records[3]
    assert done["response.id"] == "resp-done"
    assert done["output"] == [
        {
            "type": "function_call",
            "id": "item-fc",
            "call_id": "call-1",
            "name": "recall_memory",
            "status": "completed",
        }
    ]
    # The deltas' response id never reached a terminal of its own, so they are
    # reported beside the done that did arrive rather than silently dropped.
    assert "delta_counts" not in done
    assert done["other_delta_counts"] == {
        "resp-fc": {"response.function_call_arguments.delta": 5}
    }
    error = records[4]
    assert error["error.type"] == "invalid_request_error"
    assert error["error.code"] == "bad_thing"
    assert error["error.event_id"] == "event-x"
    assert len(error["error.message"]) == 200
    _assert_no_private_content(caplog)


@pytest.mark.unit
@pytest.mark.asyncio
async def test_malformed_events_and_a_failing_logger_never_raise(monkeypatch, caplog):
    _capture(caplog)
    trace = RealtimeWireTrace(shared_module.logger)
    malformed = (
        None,
        ["not", "a", "dict"],
        "string",
        42,
        {"type": 7, "item": "not-a-dict", "response": ["x"]},
        {
            "type": "session.update",
            "session": {
                "tools": [None, 3, {"function": "x"}],
                "turn_detection": "server_vad",
                "modalities": "audio",
            },
        },
        {"type": "response.done", "response": {"id": _Unserializable(), "output": [None, "x"]}},
        {"type": "response.audio.delta", "response_id": {"nested": "dict"}},
        {"type": "error", "error": _Unserializable()},
    )
    for event in malformed:
        trace.record_send(event, generation=_Unserializable())
        trace.record_recv(event, generation=_Unserializable())
    for record in _trace_records(caplog, WIRE_TRACE_PREFIX):
        assert isinstance(record, dict)

    exploding = RealtimeWireTrace(_ExplodingLogger())
    exploding.record_send({"type": "response.create"})
    exploding.record_recv({"type": "response.done"})

    monkeypatch.setenv(WIRE_TRACE_ENV_VAR, "1")
    client = _make_client()
    client._wire_trace = exploding
    socket = _QueueSocket()
    client.ws = socket
    client._on_connection_attached()
    assert await client.send_event(_user_item_event()) is True
    assert socket.sent[-1]["type"] == "conversation.item.create"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_arbiter_trace_names_orphan_mismatch_terminal(caplog):
    _capture(caplog)

    async def send(_event):
        return None

    arbiter = RealtimeResponseArbiter(send, trace=True)
    try:
        ticket = await arbiter.enqueue(source="proactive")
        await asyncio.wait_for(ticket.sent, 0.5)
        arbiter.notify_response_created(
            {"type": "response.created", "response": {"id": "resp-owner"}}
        )
        assert not arbiter.notify_response_terminal(
            {
                "type": "response.done",
                "response_id": "resp-top",
                "response": {"id": "resp-other", "status": "completed"},
            }
        )
        assert arbiter.notify_response_terminal(
            {"type": "response.done", "response": {"id": "resp-owner", "status": "completed"}}
        )
        await asyncio.wait_for(ticket.done, 0.5)
    finally:
        await arbiter.shutdown()

    records = _trace_records(caplog, ARBITER_TRACE_PREFIX)
    assert all(isinstance(record["t"], float) for record in records)
    enqueue = [record for record in records if record["decision"] == "enqueue"]
    assert enqueue and enqueue[0]["source"] == "proactive"
    phases = [
        record["phase"] for record in records if record["decision"] == "dispatch"
    ]
    assert "response_create_sent" in phases
    assert phases[-1] == "completed"
    created = [record for record in records if record["decision"] == "created"]
    assert created[0]["outcome"] == "owner_claimed"
    terminals = [record for record in records if record["decision"] == "terminal"]
    assert [record["outcome"] for record in terminals] == ["orphan_mismatch", "resolved"]
    orphan = terminals[0]
    assert orphan["response_id"] == "resp-top"
    assert orphan["response.id"] == "resp-other"
    assert orphan["owner_response_id"] == "resp-owner"
    assert orphan["owner_source"] == "proactive"
    assert orphan["status"] == "completed"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_arbiter_trace_rejects_unreliable_function_call_identity(caplog):
    # The proxy's function-call ID is not its terminal ID. Do not bind it.
    _capture(caplog)

    async def send(_event):
        return None

    arbiter = RealtimeResponseArbiter(
        send,
        protocol_capabilities=LANLAN_APP_REALTIME_PROTOCOL_CAPABILITIES,
        trace=True,
    )
    try:
        ticket = await arbiter.enqueue(source="hot_swap")
        await asyncio.wait_for(ticket.sent, 0.5)
        content = {
            "type": "response.function_call_arguments.done",
            "response_id": "resp-fc",
            "call_id": "call-1",
            "name": "recall_memory",
            "arguments": PRIVATE_ARGUMENTS,
        }
        assert not arbiter.notify_response_content(content)
        # A repeat is refused and, being a duplicate refusal, logged once.
        assert not arbiter.notify_response_content(content)
        assert not arbiter.notify_response_content(content)
        assert arbiter.notify_response_terminal(
            {"type": "response.done", "response": {"id": "resp-done"}}
        )
        await asyncio.wait_for(ticket.done, 0.5)
    finally:
        await arbiter.shutdown()

    records = _trace_records(caplog, ARBITER_TRACE_PREFIX)
    contents = [record for record in records if record["decision"] == "content"]
    assert [record["outcome"] for record in contents] == [
        "ignored_other",
    ]
    accepted = contents[0]
    assert accepted["event_type"] == "response.function_call_arguments.done"
    assert accepted["response_id"] == "resp-fc"
    assert accepted["call_id"] == "call-1"
    assert accepted["owner_source"] == "hot_swap"
    assert accepted["owner_response_id"] is None
    assert accepted["route"] == "lanlan_app_gemini"
    assert contents[0]["reason"] == "not_eligible"
    terminals = [record for record in records if record["decision"] == "terminal"]
    assert terminals[0]["outcome"] == "claimed_never_announced"
    assert terminals[0]["response.id"] == "resp-done"
    assert terminals[0]["owner_response_id"] == "resp-done"
    _assert_no_private_content(caplog)


@pytest.mark.unit
def test_unrelated_done_leaves_each_response_its_full_delta_total(caplog):
    # Interleaved streams are the H_a/H_c situation: another response finishing
    # mid-stream must not take part of this stream's count with it.
    _capture(caplog)
    trace = RealtimeWireTrace(shared_module.logger)

    def audio_delta():
        trace.record_recv(
            {
                "type": "response.audio.delta",
                "response_id": "resp-X",
                "delta": PRIVATE_AUDIO,
            },
            generation=1,
        )

    for _ in range(10):
        audio_delta()
    trace.record_recv({"type": "response.done", "response": {"id": "resp-Y"}}, generation=1)
    trace.record_recv({"type": "response.done", "response": {"id": "resp-Z"}}, generation=1)
    for _ in range(5):
        audio_delta()
    trace.record_recv({"type": "response.done", "response": {"id": "resp-X"}}, generation=1)

    dones = [
        record
        for record in _trace_records(caplog, WIRE_TRACE_PREFIX)
        if record["type"] == "response.done"
    ]
    assert [record["response.id"] for record in dones] == ["resp-Y", "resp-Z", "resp-X"]
    assert dones[0]["other_delta_counts"] == {"resp-X": {"response.audio.delta": 10}}
    # Unchanged since it was last shown, so not repeated.
    assert "other_delta_counts" not in dones[1]
    assert dones[2]["delta_counts"] == {"response.audio.delta": 15}
    assert "other_delta_counts" not in dones[2]
    _assert_no_private_content(caplog)


@pytest.mark.unit
def test_function_call_deltas_without_call_id_log_each_call_once(caplog):
    _capture(caplog)
    trace = RealtimeWireTrace(shared_module.logger)
    calls = (("resp-fc-0", 0), ("resp-fc-0", 1), ("resp-fc-1", 0))
    for response_id, output_index in calls:
        for _ in range(3):
            trace.record_recv(
                {
                    "type": "response.function_call_arguments.delta",
                    "response_id": response_id,
                    "output_index": output_index,
                    "delta": PRIVATE_ARGUMENTS,
                },
                generation=2,
            )

    firsts = [
        record
        for record in _trace_records(caplog, WIRE_TRACE_PREFIX)
        if record["type"] == "response.function_call_arguments.delta"
    ]
    assert [(record["response_id"], record["output_index"]) for record in firsts] == [
        ("resp-fc-0", 0),
        ("resp-fc-0", 1),
        ("resp-fc-1", 0),
    ]
    assert all(record["first_delta"] is True for record in firsts)
    _assert_no_private_content(caplog)


class _GenerationBumpingSocket(_QueueSocket):
    def __init__(self, client) -> None:
        super().__init__()
        self._client = client

    async def send(self, payload: str) -> None:
        # A replacement connection attaches while this write is in flight.
        self._client._connection_generation += 1
        await super().send(payload)


@pytest.mark.unit
@pytest.mark.asyncio
async def test_send_record_keeps_the_generation_it_was_written_on(monkeypatch, caplog):
    monkeypatch.setenv(WIRE_TRACE_ENV_VAR, "1")
    _capture(caplog)
    client = _make_client()
    socket = _GenerationBumpingSocket(client)
    client.ws = socket
    client._on_connection_attached()
    written_on = client._connection_generation

    assert await client.send_event(_response_create_event())

    assert client._connection_generation == written_on + 1
    sends = [
        record
        for record in _trace_records(caplog, WIRE_TRACE_PREFIX)
        if record["dir"] == "send"
    ]
    assert [record["type"] for record in sends] == ["response.create"]
    assert sends[0]["gen"] == written_on


@pytest.mark.unit
@pytest.mark.asyncio
async def test_arbiter_records_carry_the_wire_client_tag_and_generation(
    monkeypatch, caplog
):
    monkeypatch.setenv(WIRE_TRACE_ENV_VAR, "1")
    _capture(caplog)
    client = _make_client()
    socket = _QueueSocket()
    client.ws = socket
    client._on_connection_attached()
    tag = client._wire_trace.client_tag

    assert await client.send_event(_user_item_event())
    client._response_arbiter.notify_response_terminal(
        {"type": "response.done", "response": {"id": "resp-free-1"}}
    )
    # The lazily built fallback arbiter is stamped the same way.
    client._response_arbiter = None
    fallback = client._ensure_response_arbiter()
    fallback.notify_response_terminal(
        {"type": "response.done", "response": {"id": "resp-free-2"}}
    )

    wire = _trace_records(caplog, WIRE_TRACE_PREFIX)
    assert wire and wire[-1]["cid"] == tag
    terminals = [
        record
        for record in _trace_records(caplog, ARBITER_TRACE_PREFIX)
        if record["decision"] == "terminal"
    ]
    assert [record["response.id"] for record in terminals] == [
        "resp-free-1",
        "resp-free-2",
    ]
    for record in terminals:
        assert record["cid"] == tag
        assert record["gen"] == client._connection_generation == wire[-1]["gen"]
