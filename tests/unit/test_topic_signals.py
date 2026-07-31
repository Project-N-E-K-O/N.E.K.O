import json
import time
import weakref

from main_logic.topic.signals import TopicSignalStore, TopicTurnSignal


def test_topic_turn_signal_uses_slots_and_preserves_weakrefs():
    signal = TopicTurnSignal(actor="user", text="hello", timestamp=1.0)

    assert not hasattr(signal, "__dict__")
    assert weakref.ref(signal)() is signal


def test_topic_signal_store_keeps_filler_chat_below_ready_even_after_many_turns():
    store = TopicSignalStore(min_user_turns_for_topic=4)
    now = time.time()

    for idx, text in enumerate(["嗯", "哈哈", "好", "可以", "啊", "行", "哦", "没事", "对", "不知道"]):
        store.note_turn("妮可", actor="user", text=text, now=now + idx)

    # All filler → no meaningful turns → never ready, however many arrive.
    assert store.readiness_percent("妮可") == 0
    assert store.is_ready("妮可") is False
    formatted = store.format_global_signals("妮可")
    # Only the raw evidence list is emitted — no stats head, no inner header.
    assert "收集进度:" not in formatted
    assert "全局证据:" not in formatted
    assert "- [" in formatted  # turn lines still render


def test_topic_signal_store_ready_after_enough_meaningful_user_turns():
    store = TopicSignalStore(min_user_turns_for_topic=4)
    now = time.time()

    store.note_turn("妮可", actor="user", text="我最近一直在纠结要不要换工作，怕换了之后更坑", now=now)
    store.note_turn("妮可", actor="ai", text="你像是在怕失去可控感。", now=now + 1)
    store.note_turn("妮可", actor="user", text="对，我不是怕累，是怕选错了以后回不了头", now=now + 2)
    store.note_turn("妮可", actor="user", text="但现在这个工作又真的让我每天都很烦", now=now + 3)
    store.note_turn("妮可", actor="user", text="要不要干脆换个城市重新开始", now=now + 4)

    # 4 meaningful user turns reach the gate (the AI turn does not count).
    assert store.is_ready("妮可") is True
    assert store.readiness_percent("妮可") >= 80
    formatted = store.format_global_signals("妮可")
    assert "稳定度:" not in formatted
    assert "换工作" in formatted


def test_topic_signal_store_default_ready_requires_eight_meaningful_user_turns():
    store = TopicSignalStore()
    now = time.time()

    for idx in range(7):
        store.note_turn("妮可", actor="user", text=f"第{idx}个认真话题信号", now=now + idx)

    assert store.is_ready("妮可") is False

    store.note_turn("妮可", actor="user", text="第8个认真话题信号", now=now + 8)

    assert store.is_ready("妮可") is True


def test_topic_signal_store_localizes_evidence_lines():
    store = TopicSignalStore(min_user_turns_for_topic=1)
    now = time.time()
    store.note_turn("neko", actor="user", text="I keep thinking about moving to a quieter city", now=now)
    store.note_turn("neko", actor="ai", text="That sounds like a need for more control.", now=now + 1)

    formatted = store.format_global_signals("neko", lang="en")

    # No stats head / inner header; the per-line actor + age label localizes.
    assert "moving to a quieter city" in formatted
    assert "User:" in formatted
    assert "Global evidence:" not in formatted
    assert "收集进度:" not in formatted


def test_filler_turns_do_not_count_toward_readiness():
    now = time.time()
    substantive = TopicSignalStore(min_user_turns_for_topic=4)
    for idx, text in enumerate([
        "我在纠结要不要换工作",
        "怕选错了以后回不了头",
        "现在的工作让我每天都很烦",
        "想去个安静点的城市重新开始",
    ]):
        substantive.note_turn("妮可", actor="user", text=text, now=now + idx)

    filler = TopicSignalStore(min_user_turns_for_topic=4)
    for idx, text in enumerate(["嗯", "哈哈", "好", "可以"]):
        filler.note_turn("妮可", actor="user", text=text, now=now + idx)

    assert substantive.is_ready("妮可") is True
    assert filler.is_ready("妮可") is False
    assert substantive.readiness_percent("妮可") > filler.readiness_percent("妮可")


def test_topic_signal_store_renders_the_full_rolling_window():
    store = TopicSignalStore(min_user_turns_for_topic=1, max_turns=60)
    now = time.time()

    for idx in range(65):
        store.note_turn("妮可", actor="user", text=f"第{idx}条长期信号", now=now + idx)

    formatted = store.format_global_signals("妮可")

    assert "第0条长期信号" not in formatted
    assert "第4条长期信号" not in formatted
    assert "第5条长期信号" in formatted
    assert "第64条长期信号" in formatted
    assert formatted.count("- [") == 60


def test_topic_signal_store_persists_runtime_retention_prune(tmp_path):
    path = tmp_path / "topic_signals.json"
    now = time.time()
    store = TopicSignalStore(
        min_user_turns_for_topic=1,
        retention_seconds=10,
        persistence_path=path,
        persistence_flush_delay_seconds=60,
    )
    store.note_turn("妮可", actor="user", text="已经过期的原始证据", now=now - 20)
    store.note_turn("妮可", actor="user", text="仍在窗口内的新证据", now=now)
    store.flush()

    assert store.last_turn_at("妮可") == now
    store.flush()

    payload = json.loads(path.read_text(encoding="utf-8"))
    texts = [
        item["text"]
        for item in payload["characters"]["妮可"]
    ]
    assert texts == ["仍在窗口内的新证据"]


# ── persistence failure handling (issue #2528) ────────────────────────────
#
# `_write_payload` returns False on any write error; `flush` used to answer
# that with a fixed 1s `threading.Timer`, with no attempt cap and no backoff,
# so a permanently unwritable state dir became one silent write attempt per
# second forever. And `flush` is the atexit hook: a daemon Timer started there
# can never fire, so the exit path was pretending to schedule a retry while
# actually dropping the window silently.

import contextlib
import logging
from unittest.mock import patch

from main_logic.topic.signals import (
    _PERSIST_MAX_RETRIES,
    TopicSignalStore,
)


_SIGNALS_LOGGER = "N.E.K.O.Main.topic.signals"


class _FakeTimer:
    """Records (delay, fn) and never starts a thread.

    Real timers are unusable here: the retry chain under test is exactly the
    thing that would leak background threads into later tests, and driving the
    chain by hand is the only way to observe the delay sequence.
    """

    def __init__(self, delay, fn, *args, **kwargs):
        self.delay = float(delay)
        self.fn = fn
        self.daemon = False
        self.started = False
        self.cancelled = False

    def start(self):
        self.started = True

    def cancel(self):
        self.cancelled = True

    def is_alive(self):
        return self.started and not self.cancelled


@contextlib.contextmanager
def _fake_timers():
    """Swap threading.Timer as seen by signals.py, collecting every instance."""
    created: list[_FakeTimer] = []

    def _make(delay, fn, *args, **kwargs):
        timer = _FakeTimer(delay, fn, *args, **kwargs)
        created.append(timer)
        return timer

    with patch("main_logic.topic.signals.threading.Timer", _make):
        yield created


@contextlib.contextmanager
def _write_gate():
    """Control whether atomic_write_json succeeds; ``gate['fail']`` toggles it."""
    gate = {"fail": True, "writes": 0}

    def _write(*_args, **_kwargs):
        gate["writes"] += 1
        if gate["fail"]:
            raise OSError("simulated read-only state dir")

    with patch("main_logic.topic.signals.atomic_write_json", side_effect=_write):
        yield gate


class _RecordSink(logging.Handler):
    def __init__(self):
        super().__init__(level=logging.DEBUG)
        self.records: list[logging.LogRecord] = []

    def emit(self, record):
        self.records.append(record)

    def at_least(self, level):
        return [r for r in self.records if r.levelno >= level]


@contextlib.contextmanager
def _capture_logger(name: str):
    """Attach a handler straight to the logger.

    ``caplog`` relies on propagation to root, which this project's logging
    setup breaks depending on import order (same note as
    tests/unit/test_callback_instruction_origin.py).
    """
    log = logging.getLogger(name)
    sink = _RecordSink()
    prior = log.level
    log.addHandler(sink)
    log.setLevel(logging.DEBUG)
    try:
        yield sink
    finally:
        log.removeHandler(sink)
        log.setLevel(prior)


def _persistent_store(tmp_path, flush_delay=1.0):
    # atexit.register 在这里会把 store 挂到解释器退出钩子上、污染整个测试进程。
    with patch("main_logic.topic.signals.atexit.register"):
        return TopicSignalStore(
            min_user_turns_for_topic=1,
            persistence_path=tmp_path / "state" / "topic_signals.json",
            persistence_flush_delay_seconds=flush_delay,
        )


def _drive(created, *, start=0, limit=20):
    """Fire recorded timers in order, up to a hard cap.

    The cap is load-bearing: an unbounded retry chain would otherwise keep
    appending work to this very loop and the test would hang instead of
    failing.
    """
    idx = start
    for _ in range(limit):
        if idx >= len(created):
            break
        timer = created[idx]
        idx += 1
        timer.fn()


def test_flush_retry_chain_is_bounded_with_exponential_backoff(tmp_path):
    with _fake_timers() as created, _write_gate():
        store = _persistent_store(tmp_path)
        store.note_turn("neko", actor="user", text="我一直在想要不要换个城市")
        # 第一个 timer 来自 _request_persist（delay=base），不是重试。
        assert len(created) == 1 and created[0].delay == 1.0

        _drive(created)

    delays = [t.delay for t in created]
    assert len(created) <= 1 + _PERSIST_MAX_RETRIES, delays
    assert delays[1:] == [1.0, 2.0, 4.0, 8.0, 16.0], delays


def test_backoff_delay_stops_growing_at_the_cap(tmp_path):
    """The cap only bites once the configured flush delay is large enough.

    A base of 1.0 cannot reach ``min()`` at all: 1/2/4/8/16 ends exactly on the
    cap, so dropping the call changes nothing. This case configures the flush
    delay to 5.0 so 20/40/80 are actually clamped back to 16 — without that the
    last retry would land more than a minute out, which is no longer self-heal.
    """
    with _fake_timers() as created, _write_gate():
        store = _persistent_store(tmp_path, flush_delay=5.0)
        store.note_turn("neko", actor="user", text="我一直在想要不要换个城市")
        assert len(created) == 1 and created[0].delay == 5.0

        _drive(created)

    delays = [t.delay for t in created]
    assert len(created) == 1 + _PERSIST_MAX_RETRIES, delays
    assert delays[1:] == [5.0, 10.0, 16.0, 16.0, 16.0], delays


def test_zero_flush_delay_still_bounds_the_retry_chain(tmp_path):
    """A zero debounce collapses the backoff but not the attempt budget.

    ``persistence_flush_delay_seconds`` is only clamped at the bottom by
    ``max(0.0, …)``, so a caller may configure 0 and turn the backoff into a
    run of zero-delay timers. That is deliberately not floored — the chain is
    bounded by ``_PERSIST_MAX_RETRIES`` either way, and a floor would be one
    more knob that never fires on the production path.
    """
    with _fake_timers() as created, _write_gate() as gate:
        store = _persistent_store(tmp_path, flush_delay=0.0)
        store.note_turn("neko", actor="user", text="我一直在想要不要换个城市")
        _drive(created)

    assert [t.delay for t in created] == [0.0] * (1 + _PERSIST_MAX_RETRIES)
    # 每个 timer 都真的试过写一次，不是空转。
    assert gate["writes"] == 1 + _PERSIST_MAX_RETRIES


def test_successful_write_resets_the_retry_budget(tmp_path):
    with _fake_timers() as created, _write_gate() as gate:
        store = _persistent_store(tmp_path)
        store.note_turn("neko", actor="user", text="我一直在想要不要换个城市")
        for _ in range(3):
            created[-1].fn()
        assert [t.delay for t in created] == [1.0, 1.0, 2.0, 4.0]

        gate["fail"] = False
        created[-1].fn()          # succeeds -> budget clears, no new timer
        assert len(created) == 4

        gate["fail"] = True
        store.note_turn("neko", actor="user", text="另一条证据")
        assert len(created) == 5  # _request_persist again
        _drive(created, start=4)

    delays_after_success = [t.delay for t in created[5:]]
    assert delays_after_success == [1.0, 2.0, 4.0, 8.0, 16.0], delays_after_success


def test_atexit_flush_does_not_schedule_a_timer_it_cannot_run(tmp_path):
    with _fake_timers() as created, _write_gate():
        store = _persistent_store(tmp_path)
        store.note_turn("neko", actor="user", text="我一直在想要不要换个城市")
        assert len(created) == 1

        with _capture_logger(_SIGNALS_LOGGER) as sink:
            store._atexit_flush()
            # 退出中到来的 turn 也不该再排一个跑不了的 timer。
            store.note_turn("neko", actor="user", text="退出途中还在说话")

    assert len(created) == 1, [t.delay for t in created]
    assert store._persist_timer is None
    messages = [r.getMessage() for r in sink.at_least(logging.WARNING)]
    assert any("at exit" in m and "lost" in m for m in messages), messages


def test_atexit_registers_the_shutdown_wrapper_not_flush(tmp_path):
    registered: list = []
    with patch("main_logic.topic.signals.atexit.register", side_effect=registered.append):
        TopicSignalStore(
            min_user_turns_for_topic=1,
            persistence_path=tmp_path / "state" / "topic_signals.json",
            persistence_flush_delay_seconds=1.0,
        )

    assert len(registered) == 1
    # 盯调用点本身：只测被调函数的行为，把注册改回 flush 的回归照样绿。
    assert registered[0].__func__ is TopicSignalStore._atexit_flush


def test_persistence_failure_is_visible_in_logs(tmp_path):
    with _fake_timers() as created, _write_gate():
        store = _persistent_store(tmp_path)
        with _capture_logger(_SIGNALS_LOGGER) as sink:
            store.note_turn("neko", actor="user", text="我一直在想要不要换个城市")
            _drive(created)

    attempts = [
        r for r in sink.records
        if r.levelno == logging.DEBUG and "write failed" in r.getMessage()
    ]
    assert len(attempts) == 1 + _PERSIST_MAX_RETRIES, len(attempts)
    assert all(r.exc_info for r in attempts)
    warnings = [r.getMessage() for r in sink.at_least(logging.WARNING)]
    assert any("giving up" in m for m in warnings), warnings
