from __future__ import annotations

import pytest

from scripts import analyze_realtime_wire_trace as analyzer


def _rec(kind: str, t: float, data: dict, seq: int = 0) -> analyzer.Record:
    return analyzer.Record(kind, t, "", data, "test.log", seq + 1, seq)


@pytest.mark.unit
def test_analyze_h_b_does_not_misattribute_unmatched_create_or_exceed_done_window():
    """F0323: when source is None, other owners' dispatch/terminal/timeout must not match and window is 60s-bounded."""
    seg = analyzer.Segment(("c1", 1))
    seg.records = [
        # Missing event_id on response.create + missing create_event_id on another owner's dispatch
        _rec("wire", 10.0, {"dir": "send", "type": "response.create"}, 1),
        _rec(
            "arbiter",
            10.1,
            {
                "decision": "dispatch",
                "phase": "response_create_sent",
                "source": "other_owner",
            },
            2,
        ),
        # Unrelated response.done arriving while arbiter is active must NOT truncate the window
        _rec("wire", 12.0, {"dir": "recv", "type": "response.done", "response": {"id": "r_other"}}, 3),
        # Other owner's timeout, terminal, and dispatch completed past DONE_WINDOW_S (10 + 60 = 70)
        _rec("arbiter", 15.0, {"decision": "timeout", "source": "other_owner", "kind": "response_done"}, 4),
        _rec(
            "arbiter",
            16.0,
            {"decision": "terminal", "owner_source": "other_owner", "outcome": "resolved"},
            5,
        ),
        _rec(
            "arbiter",
            85.0,
            {"decision": "dispatch", "phase": "completed", "source": "other_owner"},
            6,
        ),
    ]

    results = analyzer.analyze_h_b(seg)
    assert len(results) == 1
    entry = results[0]
    assert entry["source"] is None
    assert entry["end_reason"] == "60s bound (no terminal observed)"
    assert entry["window_ms"] == 60000
    assert entry["timeouts"] == []
    assert entry["resolved_by"] is None
    assert entry["started_by"] is None
    assert entry["classification"] == "inconclusive"


@pytest.mark.unit
def test_analyze_h_b_clamps_arbiter_scan_to_done_window_even_when_source_known():
    """F0323: matched source still clamps arbiter dispatch/connection scan to DONE_WINDOW_S."""
    seg = analyzer.Segment(("c1", 2))
    seg.records = [
        _rec("wire", 100.0, {"dir": "send", "type": "response.create", "event_id": "evt_1"}, 1),
        _rec(
            "arbiter",
            100.2,
            {
                "decision": "dispatch",
                "phase": "response_create_sent",
                "create_event_id": "evt_1",
                "source": "speaker",
            },
            2,
        ),
        # Completed well beyond 60s
        _rec(
            "arbiter",
            200.0,
            {"decision": "dispatch", "phase": "completed", "source": "speaker"},
            3,
        ),
    ]

    results = analyzer.analyze_h_b(seg)
    assert len(results) == 1
    assert results[0]["source"] == "speaker"
    assert results[0]["end_reason"] == "60s bound (no terminal observed)"
    assert results[0]["window_ms"] == 60000


@pytest.mark.unit
def test_analyze_h_b_preserves_no_arbiter_fallback_and_valid_attribution():
    seg_no_arbiter = analyzer.Segment(("c1", 3))
    seg_no_arbiter.records = [
        _rec("wire", 10.0, {"dir": "send", "type": "response.create", "event_id": "e1"}, 1),
        _rec("wire", 12.5, {"dir": "recv", "type": "response.done", "response": {"id": "r1"}}, 2),
    ]
    res_no_arb = analyzer.analyze_h_b(seg_no_arbiter)
    assert res_no_arb[0]["end_reason"] == "first_response_done (no arbiter data)"
    assert res_no_arb[0]["classification"] == "refuted"

    seg_with_arbiter = analyzer.Segment(("c1", 4))
    seg_with_arbiter.records = [
        _rec("wire", 10.0, {"dir": "send", "type": "response.create", "event_id": "e2"}, 1),
        _rec(
            "arbiter",
            10.1,
            {
                "decision": "dispatch",
                "phase": "response_create_sent",
                "create_event_id": "e2",
                "source": "speaker",
            },
            2,
        ),
        _rec(
            "arbiter",
            11.0,
            {"decision": "terminal", "owner_source": "speaker", "outcome": "resolved"},
            3,
        ),
        _rec(
            "arbiter",
            11.1,
            {"decision": "dispatch", "phase": "completed", "source": "speaker"},
            4,
        ),
    ]
    res_arb = analyzer.analyze_h_b(seg_with_arbiter)
    assert res_arb[0]["end_reason"] == "dispatch_completed"
    assert res_arb[0]["classification"] == "refuted"
    assert res_arb[0]["resolved_by"] is not None
