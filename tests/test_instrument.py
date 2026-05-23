# -*- coding: utf-8 -*-
"""utils/instrument.py 单元测试。

覆盖 counter / histogram / event 三 API、snapshot clear-on-read、has_data
peek、容量保护、_make_key 字典序、histogram 桶边界。

集成层（counter → token_tracker 上报 → server 落库）由
scripts/telemetry_smoke.py 端到端验证，这里只测 SDK 自身行为。
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
    """每个 test 用干净的单例，避免跨 test 累积污染。"""
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
    # dims 必须按 key 字典序拼，保证同一组 dims 不论传入顺序都得到同一 key
    k1 = _make_key("evt", {"b": 2, "a": 1})
    k2 = _make_key("evt", {"a": 1, "b": 2})
    assert k1 == k2 == "evt|a=1,b=2"


def test_make_key_value_types():
    assert _make_key("e", {"s": "x", "n": 3, "b": True}) == "e|b=True,n=3,s=x"


def test_make_key_escapes_delimiters_no_collision():
    # 含分隔符的值不能让不同 dim 组合塌缩成同一 key（Codex P2）
    k1 = _make_key("e", {"a": "x,b=y"})       # 单个 dim，值里带 , 和 =
    k2 = _make_key("e", {"a": "x", "b": "y"})  # 两个 dim
    assert k1 != k2, f"delimiter collision: {k1!r} == {k2!r}"


def test_make_key_escape_is_injective():
    # "a,b" 和 "a=b" 转义后必须区分（简单 replace 成 _ 会塌缩，escape 不会）
    assert _make_key("e", {"k": "a,b"}) != _make_key("e", {"k": "a=b"})


def test_make_key_pipe_escaped():
    # 值里的 | 也转义，不破坏 name|dims 结构
    assert _make_key("e", {"k": "a|b"}) == "e|k=a\\|b"


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
    # 灌超过 cap 的唯一 key，超出的被丢弃
    for i in range(_MAX_COUNTER_KEYS + 100):
        inst.counter("c", 1, dim=str(i))
    assert len(inst._counters) == _MAX_COUNTER_KEYS
    assert inst._cap_warned_counter is True


def test_counter_cap_still_accumulates_existing():
    # 容量满后，已存在的 key 仍能继续累加（只是不收新 key）
    inst = _inst()
    for i in range(_MAX_COUNTER_KEYS):
        inst.counter("c", 1, dim=str(i))
    inst.counter("c", 5, dim="0")  # 已存在
    inst.counter("c", 1, dim="overflow")  # 新 key，被丢
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
    # bisect_left(_HIST_BOUNDS, v) 决定桶 index
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
    # 超过最右边界进溢出桶（最后一个）
    inst = _inst()
    inst.histogram("h", 999999)
    snap = inst.snapshot()
    buckets = snap["histograms"]["h"]["buckets"]
    assert buckets[-1] == 1


def test_histogram_boundary_inclusive_left():
    # bisect_left: value == bound 落到该 bound 的 index（左侧桶）
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
# event（转发 event_logger）
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
    # event 走 event_logger，不进 counter/histogram map
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
    assert inst.has_data() is True  # peek 不消费
    assert inst._counters  # 仍在


def test_snapshot_clears_on_read():
    inst = _inst()
    inst.counter("c", 1)
    inst.histogram("h", 5)
    snap1 = inst.snapshot()
    assert snap1["counters"] and snap1["histograms"]
    # 第二次 snapshot 应该是空（clear-on-read）
    snap2 = inst.snapshot()
    assert snap2 == {}
    assert not inst.has_data()


def test_snapshot_empty_returns_empty_dict():
    assert _inst().snapshot() == {}


def test_snapshot_includes_stat_date_and_bounds():
    inst = _inst()
    inst.counter("c", 1)
    snap = inst.snapshot()
    # stat_date 是客户端本地日历天 YYYY-MM-DD
    assert "stat_date" in snap
    assert len(snap["stat_date"]) == 10 and snap["stat_date"][4] == "-"
    assert snap["bounds"] == list(_HIST_BOUNDS)
    assert "window_start" in snap and "window_end" in snap


def test_snapshot_empty_still_advances_window():
    # 空 snapshot 也更新 window_start，避免空窗口一直挂着
    inst = _inst()
    w0 = inst._window_start
    import time
    time.sleep(0.01)
    inst.snapshot()  # 空
    # 严格 > ：空 snapshot 也必须推进 window_start，否则空窗口会一直挂着。
    # 用 >= 的话即使实现完全不更新也会通过，兜不住回归。
    assert inst._window_start > w0
