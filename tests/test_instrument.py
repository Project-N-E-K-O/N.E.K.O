# -*- coding: utf-8 -*-
"""Unit tests for utils/instrument.py.

Cover counter / histogram / event APIs, clear-on-read snapshots, has_data
peek, capacity protection, _make_key ordering, and histogram boundaries.

Integration (counter -> token_tracker reporting -> server persistence) is verified end-to-end by
scripts/telemetry_smoke.py; test only SDK behavior here.
"""
import pytest

from utils.instrument import (
    Instrument,
    _make_key,
    _HIST_BOUNDS,
    _HIST_NUM_BUCKETS,
    _MAX_COUNTER_KEYS,
    _MAX_HISTOGRAM_KEYS,
)


@pytest.fixture(autouse=True)
def _fresh_instrument():
    """Use a clean singleton per test to prevent cross-test accumulation."""
    Instrument._instance = None
    yield
    Instrument._instance = None


def _inst():
    return Instrument.get_instance()


# ---------------------------------------------------------------------------
# _make_key
# ---------------------------------------------------------------------------

def test_make_key_no_dims():
    assert _make_key("foo", {}) == "foo"


def test_make_key_sorts_dims():
    # Sort dimension keys lexicographically so identical dimensions yield one key regardless of input order.
    k1 = _make_key("evt", {"b": 2, "a": 1})
    k2 = _make_key("evt", {"a": 1, "b": 2})
    assert k1 == k2 == "evt|a=1,b=2"


def test_make_key_value_types():
    assert _make_key("e", {"s": "x", "n": 3, "b": True}) == "e|b=True,n=3,s=x"


def test_make_key_escapes_delimiters_no_collision():
    # Delimiter-bearing values must not collapse distinct dimension combinations into one key (Codex P2).
    k1 = _make_key("e", {"a": "x,b=y"})       # One dimension with comma and equals signs in its value.
    k2 = _make_key("e", {"a": "x", "b": "y"})  # Two dimensions.
    assert k1 != k2, f"delimiter collision: {k1!r} == {k2!r}"


def test_make_key_escape_is_injective():
    # Escaped "a,b" and "a=b" must differ; replacing both delimiters with underscores would collide.
    assert _make_key("e", {"k": "a,b"}) != _make_key("e", {"k": "a=b"})


def test_make_key_pipe_escaped():
    # Escape value pipes too, preserving name|dims structure.
    assert _make_key("e", {"k": "a|b"}) == "e|k=a\\|b"


def test_make_key_escapes_name_no_collision():
    # Untrusted names containing delimiters must not collide with name+dims combinations (Codex).
    assert _make_key("foo|a=1", {}) != _make_key("foo", {"a": "1"})


def test_make_key_clean_name_unchanged():
    # Legal snake_case names have no delimiters, so escaping is a noop and leaves keys unchanged.
    assert _make_key("session_start", {}) == "session_start"
    assert _make_key("ws_connect", {"reason": "x"}) == "ws_connect|reason=x"


# ---------------------------------------------------------------------------
# counter
# ---------------------------------------------------------------------------

def test_counter_accumulates():
    inst = _inst()
    inst.counter("msg", 1)
    inst.counter("msg", 1)
    inst.counter("msg", 3)
    snap = inst.snapshot()
    assert snap["counters"]["msg"] == 5


def test_counter_dim_separates_keys():
    inst = _inst()
    inst.counter("msg", 1, surface="pet")
    inst.counter("msg", 1, surface="chat")
    inst.counter("msg", 1, surface="pet")
    snap = inst.snapshot()
    assert snap["counters"]["msg|surface=pet"] == 2
    assert snap["counters"]["msg|surface=chat"] == 1


def test_counter_ignores_empty_name():
    inst = _inst()
    inst.counter("", 1)
    assert not inst.has_data()


def test_counter_ignores_non_numeric_value():
    inst = _inst()
    inst.counter("msg", "not a number")  # type: ignore[arg-type]
    assert not inst.has_data()


def test_counter_cap_protection():
    inst = _inst()
    # Insert more unique keys than capacity; discard excess keys.
    for i in range(_MAX_COUNTER_KEYS + 100):
        inst.counter("c", 1, dim=str(i))
    assert len(inst._counters) == _MAX_COUNTER_KEYS
    assert inst._cap_warned_counter is True


def test_counter_cap_still_accumulates_existing():
    # At capacity, existing keys still accumulate; only new keys are rejected.
    inst = _inst()
    for i in range(_MAX_COUNTER_KEYS):
        inst.counter("c", 1, dim=str(i))
    inst.counter("c", 5, dim="0")  # Existing key.
    inst.counter("c", 1, dim="overflow")  # New key, discarded.
    assert inst._counters["c|dim=0"] == 6
    assert "c|dim=overflow" not in inst._counters


# ---------------------------------------------------------------------------
# histogram
# ---------------------------------------------------------------------------

def test_histogram_count_and_sum():
    inst = _inst()
    inst.histogram("lat", 100)
    inst.histogram("lat", 200)
    snap = inst.snapshot()
    h = snap["histograms"]["lat"]
    assert h["count"] == 2
    assert h["sum"] == 300


def test_histogram_bucket_placement():
    # bisect_left(_HIST_BOUNDS, v) determines bucket index.
    # _HIST_BOUNDS = (1,2,5,10,20,50,100,200,500,1000,2000,5000,10000)
    inst = _inst()
    inst.histogram("h", 156)   # 100<156<=200 -> idx 7
    inst.histogram("h", 234)   # 200<234<=500 -> idx 8
    inst.histogram("h", 412)   # idx 8
    inst.histogram("h", 8500)  # 5000<8500<=10000 -> idx 12
    snap = inst.snapshot()
    buckets = snap["histograms"]["h"]["buckets"]
    assert len(buckets) == _HIST_NUM_BUCKETS
    assert buckets[7] == 1
    assert buckets[8] == 2
    assert buckets[12] == 1


def test_histogram_overflow_bucket():
    # Values above the rightmost boundary enter the final overflow bucket.
    inst = _inst()
    inst.histogram("h", 999999)
    snap = inst.snapshot()
    buckets = snap["histograms"]["h"]["buckets"]
    assert buckets[-1] == 1


def test_histogram_boundary_inclusive_left():
    # bisect_left: value == bound enters that bound's index, the left bucket.
    inst = _inst()
    inst.histogram("h", 100)  # ==bound[6]=100 -> bisect_left=6
    snap = inst.snapshot()
    assert snap["histograms"]["h"]["buckets"][6] == 1


def test_histogram_ignores_non_numeric():
    inst = _inst()
    inst.histogram("h", "nope")  # type: ignore[arg-type]
    assert not inst.has_data()


def test_histogram_cap_protection():
    inst = _inst()
    for i in range(_MAX_HISTOGRAM_KEYS + 50):
        inst.histogram("h", 1, dim=str(i))
    assert len(inst._histograms) == _MAX_HISTOGRAM_KEYS
    assert inst._cap_warned_histogram is True


# ---------------------------------------------------------------------------
# Events forward to event_logger.
# ---------------------------------------------------------------------------

def test_event_forwards_to_event_logger(monkeypatch):
    captured = []
    monkeypatch.setattr(
        "utils.instrument._event_emit",
        lambda name, **fields: captured.append((name, fields)),
    )
    _inst().event("crash", module="agent_router", hash="abc")
    assert captured == [("crash", {"module": "agent_router", "hash": "abc"})]


def test_event_does_not_touch_counters():
    # Events use event_logger rather than counter/histogram maps.
    inst = _inst()
    inst.event("e", k="v")
    assert not inst._counters
    assert not inst._histograms


# ---------------------------------------------------------------------------
# snapshot / has_data
# ---------------------------------------------------------------------------

def test_has_data_peek_does_not_consume():
    inst = _inst()
    inst.counter("c", 1)
    assert inst.has_data() is True
    assert inst.has_data() is True  # Peek without consuming.
    assert inst._counters  # Still present.


def test_snapshot_clears_on_read():
    inst = _inst()
    inst.counter("c", 1)
    inst.histogram("h", 5)
    snap1 = inst.snapshot()
    assert snap1["counters"] and snap1["histograms"]
    # The second snapshot must be empty (clear-on-read).
    snap2 = inst.snapshot()
    assert snap2 == {}
    assert not inst.has_data()


def test_snapshot_empty_returns_empty_dict():
    assert _inst().snapshot() == {}


def test_snapshot_includes_stat_date_and_bounds():
    inst = _inst()
    inst.counter("c", 1)
    snap = inst.snapshot()
    # stat_date is the client's local calendar date, YYYY-MM-DD.
    assert "stat_date" in snap
    assert len(snap["stat_date"]) == 10 and snap["stat_date"][4] == "-"
    assert snap["bounds"] == list(_HIST_BOUNDS)
    assert "window_start" in snap and "window_end" in snap


def test_snapshot_empty_still_advances_window():
    # Even empty snapshots update window_start so empty windows cannot remain open forever.
    inst = _inst()
    w0 = inst._window_start
    import time
    time.sleep(0.01)
    inst.snapshot()  # Empty.
    # Strict >: empty snapshots must advance window_start or empty windows remain open indefinitely.
    # >= would pass even if the implementation never updated it, missing regressions.
    assert inst._window_start > w0
