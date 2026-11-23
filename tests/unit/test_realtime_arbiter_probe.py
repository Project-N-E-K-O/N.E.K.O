from __future__ import annotations

import pytest

from scripts.realtime_arbiter_probe import _Probe, _Turn


@pytest.mark.unit
def test_probe_route_app_does_not_attribute_late_events_to_new_open_turn():
    """F0334: without response.created (--route app), binding first-seen response_id keeps late turn-1 events isolated."""
    probe = _Probe(route="app", verbose=False)

    turn1 = _Turn(label="t1", sent_at=0.0)
    probe._open = turn1
    probe._open_response_id = None

    # Turn 1 receives a delta with response_id="resp-1" (no response.created on --route app)
    probe._record({"type": "response.audio.delta", "response_id": "resp-1"})
    assert turn1.stream_events == 1
    assert turn1.stream_events_with_id == 1
    assert probe._first_delta.is_set()

    # Turn 1 closes/retires, Turn 2 opens
    probe._open = None
    probe._open_response_id = None
    probe._first_delta.clear()
    probe._terminal.clear()

    turn2 = _Turn(label="t2", sent_at=1.0)
    probe._open = turn2
    probe._open_response_id = None

    # Turn 2 gets its own response_id="resp-2"
    probe._record({"type": "response.text.delta", "response_id": "resp-2"})
    assert turn2.stream_events == 1
    assert probe._first_delta.is_set()

    # Late response.done from Turn 1 arrives while Turn 2 is open
    probe._record({"type": "response.done", "response": {"id": "resp-1", "status": "cancelled"}})
    assert turn1.done_at is not None
    assert turn1.status == "cancelled"
    assert turn2.done_at is None
    assert not probe._terminal.is_set()

    # Unknown / unsolicited response_id must not resolve to already-bound turn2
    probe._record({"type": "response.done", "response": {"id": "resp-unknown", "status": "completed"}})
    assert turn2.done_at is None
    assert not probe._terminal.is_set()


@pytest.mark.unit
def test_probe_idless_stream_events_bind_only_while_open_turn_active():
    """F0334: id-less events still attribute to an active open turn (idless_stream_turns), but not after done."""
    probe = _Probe(route="app", verbose=False)
    turn = _Turn(label="t1", sent_at=0.0)
    probe._open = turn
    probe._open_response_id = None

    probe._record({"type": "response.output_text.delta"})
    assert turn.stream_events == 1
    assert turn.stream_events_with_id == 0
    assert probe._first_delta.is_set()

    probe._record({"type": "response.done", "response": {"status": "completed"}})
    assert turn.done_at is not None
    assert probe._terminal.is_set()

    # Late id-less event after done_at is set must be ignored
    probe._record({"type": "response.output_text.delta"})
    assert turn.stream_events == 1
