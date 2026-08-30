"""Guards for the ingest drop trail (plugin/message_plane/ingest_server.py).

The default ingest output is the anonymous "ingest stats recv=N accepted=N
dropped=N" aggregate. Before these tests, a push discarded for exceeding
MESSAGE_PLANE_PAYLOAD_MAX_BYTES only produced a log line when
MESSAGE_PLANE_INGEST_STATS_LOG_VERBOSE was on -- which it is not by default --
so a plugin author whose entire push vanished had no trace to follow. What is
pinned here is the split: terminal, author-actionable reasons always warn and
name the offender plus the size/limit pair, while topic caps and other
host-side back-pressure stay behind the verbose flag so the warning keeps
meaning something.
"""
from __future__ import annotations

import logging
import time
import uuid
from contextlib import contextmanager
from typing import Iterator, List

import ormsgpack
import pytest

from plugin.message_plane import ingest_server
from plugin.message_plane.ingest_server import MessagePlaneIngestServer
from plugin.message_plane.stores import StoreRegistry, TopicStore

pytestmark = pytest.mark.plugin_unit


# Small enough that a handful of characters blows through it, so the payload
# under test stays readable in the assertions.
_LIMIT = 512
_STORE = "plugin"
_TOPIC = "read.screen"
_PLUGIN_ID = "demo_plugin"


class _ListHandler(logging.Handler):
    def __init__(self, sink: List[logging.LogRecord]) -> None:
        super().__init__(level=logging.DEBUG)
        self._sink = sink

    def emit(self, record: logging.LogRecord) -> None:
        self._sink.append(record)


@contextmanager
def _capture_ingest_logs() -> Iterator[List[logging.LogRecord]]:
    """Capture records straight off the logger the ingest server writes to.

    Deliberately not caplog: the plugin adapter resolves
    ``N.E.K.O.<service>.plugin`` lazily, and whether that name propagates to the
    root logger depends on whether setup_logging already ran in this process.
    Attaching to the resolved logger makes the assertion independent of that.
    """
    target = ingest_server.logger._resolve_logger()
    records: List[logging.LogRecord] = []
    handler = _ListHandler(records)
    previous_level = target.level
    target.addHandler(handler)
    target.setLevel(logging.DEBUG)
    try:
        yield records
    finally:
        target.removeHandler(handler)
        target.setLevel(previous_level)


@pytest.fixture()
def ingest(monkeypatch: pytest.MonkeyPatch) -> Iterator[MessagePlaneIngestServer]:
    # Pinned explicitly rather than trusting the defaults: the whole point of
    # these tests is the behaviour with the verbose flag OFF, and the settings
    # module reads NEKO_MESSAGE_PLANE_* from the environment at import time.
    monkeypatch.setattr(ingest_server, "MESSAGE_PLANE_INGEST_STATS_LOG_VERBOSE", False)
    monkeypatch.setattr(ingest_server, "MESSAGE_PLANE_VALIDATE_PAYLOAD_BYTES", True)
    monkeypatch.setattr(ingest_server, "MESSAGE_PLANE_PAYLOAD_MAX_BYTES", _LIMIT)

    stores = StoreRegistry(default_store=_STORE)
    stores.register(TopicStore(name=_STORE, maxlen=64))
    server = MessagePlaneIngestServer(
        # inproc keeps the bind in-process: no port, no listener, no teardown race.
        endpoint=f"inproc://neko-test-ingest-{uuid.uuid4().hex}",
        stores=stores,
        pub_server=None,
    )
    try:
        yield server
    finally:
        server.close()


def _oversized_payload() -> dict:
    payload = {
        "plugin_id": _PLUGIN_ID,
        "type": "screenshot",
        "source": "game",
        "blob": "x" * (_LIMIT * 4),
    }
    assert len(ormsgpack.packb(payload)) > _LIMIT
    return payload


def _delta_batch(payload: dict) -> dict:
    return {
        "kind": "delta_batch",
        "items": [{"store": _STORE, "topic": _TOPIC, "payload": payload}],
    }


def _messages(records: List[logging.LogRecord]) -> List[str]:
    return [r.getMessage() for r in records]


def test_payload_too_big_warns_with_verbose_off_and_names_size_and_limit(
    ingest: MessagePlaneIngestServer,
) -> None:
    payload = _oversized_payload()
    measured = len(ormsgpack.packb(payload))

    with _capture_ingest_logs() as records:
        ingest._ingest_delta_batch(_delta_batch(payload))

    assert ingest._stats_dropped == 1
    warnings = [r for r in records if r.levelno >= logging.WARNING]
    assert len(warnings) == 1, _messages(records)

    text = warnings[0].getMessage()
    assert "reason=payload_too_big" in text
    assert f"plugin_id={_PLUGIN_ID}" in text
    assert f"store={_STORE}" in text
    assert f"topic={_TOPIC}" in text
    # The measured size and the limit both have to be there: "too big" without
    # the two numbers does not tell an author how much to cut.
    assert f"size={measured}" in text
    assert f"limit={_LIMIT}" in text


def test_publish_error_warns_with_verbose_off(
    ingest: MessagePlaneIngestServer,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = ingest._stores.get(_STORE)
    assert store is not None

    def _boom(topic: str, payload: dict) -> dict:
        raise RuntimeError("store exploded")

    monkeypatch.setattr(store, "publish", _boom)

    with _capture_ingest_logs() as records:
        ingest._ingest_delta_batch(_delta_batch({"plugin_id": _PLUGIN_ID, "text": "hi"}))

    warnings = [r for r in records if r.levelno >= logging.WARNING]
    assert len(warnings) == 1, _messages(records)
    text = warnings[0].getMessage()
    assert "reason=publish_error" in text
    assert f"plugin_id={_PLUGIN_ID}" in text
    assert "err=RuntimeError" in text


def test_topic_cap_drop_stays_behind_the_verbose_flag(
    ingest: MessagePlaneIngestServer,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Topic caps are host-side back-pressure and can fire per message; promoting
    # them alongside payload_too_big would drown the signal the warning carries.
    monkeypatch.setattr(ingest_server, "MESSAGE_PLANE_TOPIC_MAX", 0)

    with _capture_ingest_logs() as records:
        ingest._ingest_delta_batch(_delta_batch({"plugin_id": _PLUGIN_ID, "text": "hi"}))

    assert ingest._stats_dropped == 1
    assert _messages(records) == []


def test_repeated_payload_too_big_is_throttled_then_reported_as_suppressed(
    ingest: MessagePlaneIngestServer,
) -> None:
    payload = _oversized_payload()
    batch = _delta_batch(payload)

    with _capture_ingest_logs() as records:
        ingest._ingest_delta_batch(batch)
        ingest._ingest_delta_batch(batch)
        ingest._ingest_delta_batch(batch)
        # Only the first of the three got through: a plugin pushing an oversized
        # cue every frame must not turn the log into a flood.
        assert len(records) == 1, _messages(records)

        # Backdate the throttle window instead of moving the process clock, then
        # confirm the next warning accounts for what was swallowed meanwhile.
        assert len(ingest._drop_warn_state) == 1
        key, (_last_ts, suppressed) = next(iter(ingest._drop_warn_state.items()))
        assert suppressed == 2
        ingest._drop_warn_state[key] = (
            time.time() - (ingest_server._DROP_WARN_THROTTLE_SECONDS + 1.0),
            suppressed,
        )

        ingest._ingest_delta_batch(batch)

    assert len(records) == 2, _messages(records)
    assert "suppressed=2" in records[1].getMessage()
