"""``bus.publish`` must not be a back door into the host's own stores.

The RPC socket carries no caller identity: it is loopback, any plugin process
can connect, and the ``plugin_id`` inside a payload is whatever the sender
wrote. Three stores turn such a record into something the user sees or into
something attributed to the host --

* ``messages`` — ``ProactiveBridge`` subscribes to the ``messages.`` prefix and
  pushes what it finds to main_server, so a forged record is a line the
  character says;
* ``frames`` — records here are "what the model was shown";
* ``conversations`` — the turn context plugins read back.

-- so ``bus.publish`` refuses them outright. The host is unaffected because it
writes through the ingest socket, which is a different server with a token.
"""

from __future__ import annotations

import pytest

from plugin.message_plane.ingest_server import MessagePlaneIngestServer
from plugin.message_plane.protocol import PROTOCOL_VERSION
from plugin.message_plane.rpc_server import MessagePlaneRpcServer
from plugin.message_plane.stores import (
    HOST_OWNED_STORE_NAMES,
    build_default_store_registry,
)


def _server():
    registry = build_default_store_registry(maxlen=64, frames_maxlen=4)
    # No socket is bound: _handle is the same function the serve loop calls,
    # and going through ZMQ here would only test ZMQ.
    rpc = MessagePlaneRpcServer.__new__(MessagePlaneRpcServer)
    rpc._stores = registry
    rpc._pub = None
    return rpc, registry


def _publish(rpc, store: str, payload: dict):
    # A well-formed envelope on purpose: a request the validator rejects would
    # make the refusal tests pass without the deny-list ever running.
    return rpc._handle(
        {
            "v": PROTOCOL_VERSION,
            "req_id": "req-1",
            "op": "bus.publish",
            "args": {"store": store, "topic": "all", "payload": payload},
        }
    )


@pytest.mark.plugin_unit
@pytest.mark.parametrize("store", sorted(HOST_OWNED_STORE_NAMES))
def test_direct_rpc_writes_to_host_stores_are_refused(store: str) -> None:
    """Mutation: drop the ``HOST_OWNED_STORE_NAMES`` check in ``bus.publish``."""
    rpc, registry = _server()

    resp = _publish(rpc, store, {"plugin_id": "victim", "content": "forged cue"})

    assert not resp.get("ok"), f"{store} 接受了未鉴权的直写"
    st = registry.get(store)
    assert st is not None and st.get_recent("all", 10) == [], (
        f"{store} 里留下了伪造记录"
    )


@pytest.mark.plugin_unit
def test_other_stores_still_accept_publish() -> None:
    """The guard is a narrow deny-list, not a shutdown of the op.

    Without this, deleting ``bus.publish`` entirely would also pass the test
    above -- and that is a different, much larger change.
    """
    rpc, registry = _server()

    resp = _publish(rpc, "runs", {"value": 1})

    assert resp.get("ok"), resp
    st = registry.get("runs")
    assert st is not None and len(st.get_recent("all", 10)) == 1


@pytest.mark.plugin_unit
@pytest.mark.parametrize("store", sorted(HOST_OWNED_STORE_NAMES))
def test_the_host_writer_is_not_blocked(store: str) -> None:
    """The deny-list must sit on the RPC op only.

    Putting it one layer lower would also cut off the host, which writes these
    stores through the ingest server -- and the failure would be silent: the
    bus simply goes empty.
    """
    registry = build_default_store_registry(maxlen=64, frames_maxlen=4)
    ingest = MessagePlaneIngestServer.__new__(MessagePlaneIngestServer)
    ingest._stores = registry
    ingest._pub = None
    ingest._drops = {}
    ingest._stats_accepted = 0
    ingest._stats_dropped = 0
    ingest._stats_last_store = None
    ingest._stats_last_topic = None
    ingest._record_drop = lambda *a, **k: None

    ingest._ingest_delta_batch(
        {"items": [{"store": store, "topic": "all", "payload": {"content": "host"}}]}
    )

    st = registry.get(store)
    assert st is not None and len(st.get_recent("all", 10)) == 1, (
        f"宿主经 ingest 写 {store} 被挡住了"
    )
