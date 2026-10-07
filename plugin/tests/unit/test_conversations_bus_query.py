"""Guards for ``ctx.bus.conversations.get_by_id`` -- the lookup, not just the read.

``ConversationClient`` has always put ``conversation_id`` into a ``bus.query``
request, and nothing downstream ever looked at it. Two independent breaks, each
of which this file pins separately because fixing one leaves the other silent:

* ``BusQueryArgs`` is ``extra="forbid"`` and the default validate mode is
  ``strict``, so an undeclared ``conversation_id`` was not ignored -- the call
  came back BAD_ARGS.
* ``MessagePlaneRpcServer`` never forwarded the field to ``TopicStore.query``.
  With validation relaxed the caller therefore got a page of unrelated recent
  turns and no error, which is the worse of the two failures: a plugin that
  asked for one conversation and got somebody else's cannot tell.

Both stayed harmless while the ``conversations`` store had no writer at all.
This branch adds the first one, so the lookup is now the first thing a plugin
author touches.

Semantics pinned here: the lookup is an EXACT match on the id, a turn that
carries no id is not part of any conversation and is never returned, and an
unknown id is an empty result -- not an error, matching every other filter on
``bus.query``.

Every "returns the right turns" assertion below is paired with a "and not the
others" assertion. That pairing is the whole point: against the broken code a
lookup still returned a well-formed list of ConversationRecords, so a test that
only checked "returns something" passed.
"""

from __future__ import annotations

import inspect
import time
import uuid
from typing import Any, Dict, Iterator, List, Optional

import pytest

from plugin.core.bus.conversations import ConversationClient
from plugin.message_plane import rpc_server as rpc_mod
from plugin.message_plane.protocol import PROTOCOL_VERSION, BusQueryArgs
from plugin.message_plane.rpc_server import MessagePlaneRpcServer
from plugin.message_plane.stores import (
    CONVERSATIONS_STORE_NAME,
    CONVERSATIONS_TOPIC,
    TopicStore,
    build_default_store_registry,
)

pytestmark = pytest.mark.plugin_unit


# -- corpus -------------------------------------------------------------


def _turn_record(
    *,
    conversation_id: Optional[str],
    content: str,
    event_id: str,
    turn_type: str = "proactive_reply",
) -> Dict[str, Any]:
    """The record shape ``_forward_conversation_turn`` writes.

    ``conversation_id`` sits in ``metadata`` because that is the only place
    ``TopicStore._extract_index`` looks for it; a copy at the top level would
    never reach the index the predicate reads.
    """
    metadata: Dict[str, Any] = {"turn_type": turn_type, "message_count": 2}
    if conversation_id:
        metadata["conversation_id"] = conversation_id
    return {
        "kind": "conversation",
        "type": "conversation_turn",
        "source": "proactive",
        "timestamp": time.time(),
        "content": content,
        "metadata": metadata,
        "id": event_id,
    }


# Interleaved, and the target conversation is deliberately NOT the tail: a
# "return the most recent N" bug must not be able to pass by accident.
_CORPUS = (
    ("conv-a", "a-first"),
    ("conv-b", "b-first"),
    ("conv-a", "a-second"),
    ("conv-b", "b-second"),
    (None, "orphan-no-id"),
    ("conv-b", "b-third"),
)

_ALL_CONTENTS = {content for _, content in _CORPUS}


def _fill(store: TopicStore) -> None:
    for i, (cid, content) in enumerate(_CORPUS):
        store.publish(
            CONVERSATIONS_TOPIC,
            _turn_record(conversation_id=cid, content=content, event_id="turn-%d" % i),
        )


def _contents(items: List[Dict[str, Any]]) -> List[Any]:
    return [(ev.get("payload") or {}).get("content") for ev in items]


# -- the schema ---------------------------------------------------------


def test_the_query_schema_declares_the_conversation_id_filter() -> None:
    """extra="forbid" plus strict mode means undeclared == rejected, not ignored."""
    args = BusQueryArgs.model_validate(
        {
            "store": CONVERSATIONS_STORE_NAME,
            "topic": CONVERSATIONS_TOPIC,
            "limit": 50,
            "conversation_id": "conv-a",
        }
    )
    assert args.conversation_id == "conv-a"

    # The acceptance above is only meaningful because this model really does
    # reject what it does not declare.
    with pytest.raises(Exception):
        BusQueryArgs.model_validate(
            {
                "store": CONVERSATIONS_STORE_NAME,
                "topic": CONVERSATIONS_TOPIC,
                "limit": 50,
                "not_a_real_filter": "x",
            }
        )


def test_the_client_sends_exactly_what_the_schema_declares() -> None:
    """The field name is a contract across three files; a rename breaks it silently."""
    assert "conversation_id" in BusQueryArgs.model_fields

    client_source = inspect.getsource(ConversationClient._get_impl)
    assert 'args["conversation_id"] = conversation_id' in client_source

    server_source = inspect.getsource(MessagePlaneRpcServer._handle)
    assert 'conversation_id=args.get("conversation_id")' in server_source


# -- the store predicate ------------------------------------------------


def test_a_lookup_by_id_returns_that_conversation_and_not_other_recent_turns() -> None:
    store = TopicStore(name=CONVERSATIONS_STORE_NAME, maxlen=64)
    _fill(store)

    items = store.query(topic=CONVERSATIONS_TOPIC, conversation_id="conv-a", limit=50)

    assert _contents(items) == ["a-second", "a-first"]
    # The half that fails against the broken code: nothing else came along.
    assert all(ev["index"]["conversation_id"] == "conv-a" for ev in items)
    assert "b-third" not in _contents(items)
    assert "orphan-no-id" not in _contents(items)

    # ...and the corpus is not trivially single-conversation: an unfiltered
    # read of the same store really does hand back the others.
    everything = _contents(store.query(topic=CONVERSATIONS_TOPIC, limit=50))
    assert set(everything) == _ALL_CONTENTS


def test_a_small_limit_does_not_degrade_into_recent_turns() -> None:
    """limit is applied AFTER the filter, so a tight limit still means "this id".

    Against the unfiltered code a limit of 2 returns the two newest records in
    the store -- neither of which belongs to conv-a.
    """
    store = TopicStore(name=CONVERSATIONS_STORE_NAME, maxlen=64)
    _fill(store)

    items = store.query(topic=CONVERSATIONS_TOPIC, conversation_id="conv-a", limit=2)
    assert _contents(items) == ["a-second", "a-first"]


def test_an_unknown_conversation_id_is_an_empty_result_not_an_error() -> None:
    store = TopicStore(name=CONVERSATIONS_STORE_NAME, maxlen=64)
    _fill(store)

    assert store.query(topic=CONVERSATIONS_TOPIC, conversation_id="conv-nope", limit=50) == []


def test_an_empty_conversation_id_is_not_a_filter() -> None:
    """Absent stays absent: an empty string means "no id given", not "id is blank"."""
    store = TopicStore(name=CONVERSATIONS_STORE_NAME, maxlen=64)
    _fill(store)

    items = store.query(topic=CONVERSATIONS_TOPIC, conversation_id="", limit=50)
    assert len(items) == len(_CORPUS)


def test_the_id_is_read_off_the_index_the_writer_actually_fills() -> None:
    """A predicate reading the payload top level would match nothing at all.

    The writer files conversation_id inside ``metadata``; ``_extract_index``
    is what lifts it somewhere comparable.
    """
    store = TopicStore(name=CONVERSATIONS_STORE_NAME, maxlen=64)
    event = store.publish(
        CONVERSATIONS_TOPIC,
        _turn_record(conversation_id="conv-a", content="a-first", event_id="turn-0"),
    )
    assert "conversation_id" not in event["payload"]
    assert event["payload"]["metadata"]["conversation_id"] == "conv-a"
    assert event["index"]["conversation_id"] == "conv-a"


# -- the RPC hop --------------------------------------------------------


@pytest.fixture()
def rpc_plane(monkeypatch: pytest.MonkeyPatch) -> Iterator[Dict[str, Any]]:
    """A real MessagePlaneRpcServer, bound but never served.

    Bound on inproc so no port is taken, and closed in teardown: pyzmq terms
    the shared context at exit and that call BLOCKS on an open socket, which
    turns a red test into a hung CI job.

    Validation is pinned to ``strict`` rather than inherited from the
    environment. ``NEKO_MESSAGE_PLANE_VALIDATE_MODE`` defaults to strict, which
    is exactly the mode that turned this lookup into BAD_ARGS -- letting the
    env decide would let the schema half of the fix go untested.
    """
    monkeypatch.setattr(rpc_mod, "MESSAGE_PLANE_VALIDATE_MODE", "strict")

    registry = build_default_store_registry(maxlen=64, frames_maxlen=4)
    server = MessagePlaneRpcServer(
        endpoint="inproc://neko-conv-query-" + uuid.uuid4().hex,
        pub_server=None,
        stores=registry,
    )
    store = registry.get(CONVERSATIONS_STORE_NAME)
    assert store is not None
    _fill(store)
    try:
        yield {"server": server, "store": store}
    finally:
        server.close()


def _query(server: MessagePlaneRpcServer, **args: Any) -> Dict[str, Any]:
    return server._handle(
        {
            "v": PROTOCOL_VERSION,
            "op": "bus.query",
            "req_id": uuid.uuid4().hex[:16],
            "args": {
                "store": CONVERSATIONS_STORE_NAME,
                "topic": CONVERSATIONS_TOPIC,
                "limit": 50,
                **args,
            },
        }
    )


def test_the_rpc_server_accepts_the_filter_under_strict_validation(
    rpc_plane: Dict[str, Any],
) -> None:
    resp = _query(rpc_plane["server"], conversation_id="conv-a")
    assert resp["ok"] is True, resp.get("error")


def test_the_rpc_server_forwards_the_filter_to_the_store(
    rpc_plane: Dict[str, Any],
) -> None:
    """Accepting the arg and acting on it are two different fixes.

    A server that validates ``conversation_id`` and then drops it on the floor
    answers ok=True with somebody else's turns, which is the failure a caller
    cannot detect.
    """
    resp = _query(rpc_plane["server"], conversation_id="conv-a")
    items = resp["result"]["items"]

    assert _contents(items) == ["a-second", "a-first"]
    assert "b-third" not in _contents(items)

    unfiltered = _query(rpc_plane["server"])["result"]["items"]
    assert len(unfiltered) == len(_CORPUS)


def test_an_unknown_id_over_rpc_is_an_empty_ok_not_an_error(
    rpc_plane: Dict[str, Any],
) -> None:
    resp = _query(rpc_plane["server"], conversation_id="conv-nope")
    assert resp["ok"] is True
    assert resp["result"]["items"] == []


# -- the SDK accessor, end to end ---------------------------------------


class _LoopbackRpc:
    """The wire shape a MessagePlaneRpcClient would send, minus the socket."""

    def __init__(self, server: MessagePlaneRpcServer) -> None:
        self._server = server
        self.sent: List[Dict[str, Any]] = []

    def request(self, *, op: str, args: Dict[str, Any], timeout: float = 5.0) -> Any:
        req = {
            "v": PROTOCOL_VERSION,
            "op": op,
            "req_id": uuid.uuid4().hex[:16],
            "args": dict(args),
        }
        self.sent.append(req)
        return self._server._handle(req)


class _ReaderCtx:
    """Only what ``_ensure_rpc`` reads off a PluginContext."""

    def __init__(self, rpc: _LoopbackRpc) -> None:
        self.plugin_id = "conversation_reader"
        self._mp_rpc_client = rpc


def test_get_by_id_returns_that_conversation_and_not_other_recent_turns(
    rpc_plane: Dict[str, Any],
) -> None:
    """The whole chain a plugin author hits: accessor -> envelope -> store."""
    rpc = _LoopbackRpc(rpc_plane["server"])
    client = ConversationClient(_ReaderCtx(rpc))

    records = list(client.get_by_id("conv-a"))

    assert [r.content for r in records] == ["a-second", "a-first"]
    assert {r.conversation_id for r in records} == {"conv-a"}
    assert rpc.sent[-1]["op"] == "bus.query"
    assert rpc.sent[-1]["args"]["conversation_id"] == "conv-a"

    # And the store it read really did hold the other turns.
    everything = list(client.get(max_count=50))
    assert {r.content for r in everything} == _ALL_CONTENTS
    assert len(everything) > len(records)


def test_get_by_id_on_an_unknown_conversation_is_an_empty_list(
    rpc_plane: Dict[str, Any],
) -> None:
    client = ConversationClient(_ReaderCtx(_LoopbackRpc(rpc_plane["server"])))
    assert list(client.get_by_id("conv-nope")) == []
