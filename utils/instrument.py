# -*- coding: utf-8 -*-
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

"""
General-purpose instrumentation SDK (counter / histogram / event)

Business code only imports three functions — ``counter`` / ``histogram`` / ``event`` —
and never cares about buffering / snapshots / reporting channels. All data is picked up
by TokenTracker's 60s periodic save and leaves through the same HTTP channel, the same
device_id and the same HMAC signature as daily_stats.

Channel selection (what to use when):

================= ================================ ============================
Channel           When to use                      Backend presentation
================= ================================ ============================
counter           additive counts: messages sent / rolled up as "period total +
                  clicks                           dimension slices"
histogram         distribution measurements:       bucket distribution + count + sum
                  latency / FPS / size
event             sparse with context: crash /     stored verbatim as an event
                  step                             stream (events.jsonl)
================= ================================ ============================

When **not** to use:
- Don't counter() every mouse move / scroll. Pick meaningful events (message sent,
  button pressed, first feature use), otherwise it's just noise.
- Don't put message content / persona text / master_name into fields. Dimensions may
  only be enum-like tags (surface, feature_name, error_class, etc.).

Overhead:
- counter / histogram: in-process lock + dict op, ~300ns per call
- event: handed to event_logger, nanosecond-scale deque.append
- snapshot: once per 60s, clear-on-read, no accumulation
"""
from __future__ import annotations

import bisect
import threading
import time
from typing import Optional

from utils.event_logger import emit as _event_emit
from utils.logger_config import get_module_logger

logger = get_module_logger(__name__)


# ---------------------------------------------------------------------------
# 配置常量
# ---------------------------------------------------------------------------

# Histogram 桶边界（毫秒/数值通用）。覆盖 1ms~10s 主要分布范围；超出最右边
# 界的样本进溢出桶。固定桶让服务端 schema 稳定，跨版本对比直接做。
#
# 选这组边界的理由：
# - 1/2/5/10/... 的"1-2-5 序列"是 logarithmic 但保留整数可读
# - 覆盖 TTFT (~100-2000ms)、FPS 倒数 (~16ms)、startup time (~1-30s) 主要范围
# - 9 个边界 = 10 个桶，序列化后 ~40B/histogram，便宜
_HIST_BOUNDS: tuple = (1, 2, 5, 10, 20, 50, 100, 200, 500, 1000, 2000, 5000, 10000)
_HIST_NUM_BUCKETS = len(_HIST_BOUNDS) + 1  # 最右边界右侧多一个溢出桶

# Counter / histogram 内存上限。理论上 key 是 (name, dims tuple)，不该爆，
# 但万一业务把高基数维度（如 user_id / 消息内容）塞进 dims，要兜底防内存
# 泄漏。超过此值丢弃新 key（保留已有的累积值），并打一次 warning。
_MAX_COUNTER_KEYS = 5000
_MAX_HISTOGRAM_KEYS = 1000


# ---------------------------------------------------------------------------
# Instrument 单例
# ---------------------------------------------------------------------------


class Instrument:
    """Process-wide singleton counter + histogram accumulator.

    snapshot() is called by TokenTracker.save during its 60s cycle. Business code uses
    the module-level ``counter`` / ``histogram`` / ``event``; never touch this class
    directly.
    """

    _instance: Optional["Instrument"] = None
    _init_lock = threading.Lock()

    @classmethod
    def get_instance(cls) -> "Instrument":
        if cls._instance is None:
            with cls._init_lock:
                if cls._instance is None:
                    cls._instance = cls()
        return cls._instance

    def __init__(self) -> None:
        self._lock = threading.Lock()
        # key 是 string："name|k1=v1,k2=v2"（dims 已经按 key 字典序拼好）。
        # value 是数字。flat dict 比嵌套 dict-of-dict 序列化更友好，服务端
        # 也更容易索引。
        self._counters: dict = {}
        # 同样的 key 结构，value 是 [count, sum, [bucket_counts...]]
        self._histograms: dict = {}
        # snapshot 窗口起点（每次 snapshot 重置）
        self._window_start: float = time.time()
        # 高基数告警节流
        self._cap_warned_counter: bool = False
        self._cap_warned_histogram: bool = False

    # ---- 公开 API ----

    def counter(self, name: str, value: int = 1, **dims) -> None:
        """Increment a counter.

        Args:
            name: metric name (snake_case, e.g. "user_message_sent")
            value: increment, default 1. Negative (decrement) is allowed but rarely useful.
            **dims: dimension tags. Values must be hashable simple types like string / int / bool.
                Never pass high-cardinality values like message content or user_id.

        Example:
            counter("user_message_sent", 1, surface="pet_widget")
            counter("feature_invoked", 1, feature="galgame", first_use=True)
        """
        if not name or not isinstance(value, (int, float)):
            return
        key = _make_key(name, dims)
        with self._lock:
            if key in self._counters:
                self._counters[key] += value
            elif len(self._counters) < _MAX_COUNTER_KEYS:
                self._counters[key] = value
            else:
                # 容量保护：满了就静默丢，避免业务方误用高基数维度炸内存
                if not self._cap_warned_counter:
                    logger.warning(
                        f"instrument: counter map full ({_MAX_COUNTER_KEYS} keys), "
                        f"dropping new keys. Check if any dim has high cardinality."
                    )
                    self._cap_warned_counter = True

    def histogram(self, name: str, value: float, **dims) -> None:
        """Record a distribution measurement.

        Args:
            name: metric name (snake_case, e.g. "ttft_ms", "live2d_fps")
            value: measured value (number). Bucketed into the corresponding _HIST_BOUNDS bucket.
            **dims: same as counter; dimension tags must be low-cardinality.

        Example:
            histogram("ttft_ms", 234)
            histogram("live2d_fps", 58.5, surface="pet_widget")
        """
        if not name or not isinstance(value, (int, float)):
            return
        # bisect_left：value <= bound 落到该桶；超过最右边界进溢出桶
        bucket_idx = bisect.bisect_left(_HIST_BOUNDS, value)
        key = _make_key(name, dims)
        with self._lock:
            entry = self._histograms.get(key)
            if entry is None:
                if len(self._histograms) >= _MAX_HISTOGRAM_KEYS:
                    if not self._cap_warned_histogram:
                        logger.warning(
                            f"instrument: histogram map full ({_MAX_HISTOGRAM_KEYS} keys), "
                            f"dropping new keys. Check if any dim has high cardinality."
                        )
                        self._cap_warned_histogram = True
                    return
                entry = [0, 0.0, [0] * _HIST_NUM_BUCKETS]
                self._histograms[key] = entry
            entry[0] += 1
            entry[1] += value
            entry[2][bucket_idx] += 1

    def event(self, name: str, **fields) -> None:
        """Record a sparse event with context (forwarded straight to event_logger).

        Difference from counter: an event is a discrete event stream ("name happened at
        ts=X"), preserved record by record; a counter is an aggregate number ("name
        happened N times within the window").

        Example:
            event("crash", traceback_hash="a3f8", module="agent_router")
            event("onboarding_step", step="persona_selected", duration_ms=1500)
        """
        _event_emit(name, **fields)

    # ---- snapshot ----

    def has_data(self) -> bool:
        """Whether accumulated data is waiting for a snapshot. For the reporting channel to peek at when deciding whether to send a request.

        TokenTracker would normally skip reporting when daily_stats is empty, but
        instrument itself may have counters/histograms waiting; this method lets the
        reporting channel judge "is a request worth sending" without consuming data.
        """
        with self._lock:
            return bool(self._counters or self._histograms)

    def snapshot(self) -> dict:
        """Take the current accumulated values + reset + return. Called by the TokenTracker reporting channel.

        Returns:
            dict with keys "window_start", "window_end", "stat_date",
            "bounds", "counters", "histograms", or an empty dict (nothing accumulated).

            ``stat_date`` is the **client-local** calendar day (``YYYY-MM-DD``), the
            same convention as ``daily_stats``. The server lands SQL rows by it, so
            same-day usage / instrument data from cross-timezone clients isn't split
            into two days by server timezone differences.

        Failure handling: once the returned snapshot is handed to token_tracker,
        instrument's internals are immediately reset. If reporting fails, the 60s
        window's counter / histogram data is lost — a deliberate trade-off:
        sparse_event has a local jsonl fallback via event_logger, while counter /
        histogram are aggregates and losing one window barely affects trend analysis —
        not worth maintaining another unsent queue. Only daily_stats (LLM tokens) must
        not be lost.
        """
        from datetime import date as _date  # 局部 import 防进程启动时早调用环
        with self._lock:
            if not self._counters and not self._histograms:
                # 即使空也更新 window_start，避免下次 snapshot 把空窗口
                # 一直挂着 —— 否则前 30 min 没埋点活动、第 31 min 有一条，
                # 上报的 window 会显示 31min 而不是 1min。
                self._window_start = time.time()
                return {}
            counters = self._counters
            histograms = self._histograms
            window_start = self._window_start
            self._counters = {}
            self._histograms = {}
            self._window_start = time.time()
            # warning 标志保留 —— 反复打日志比反复 warn 更烦
        # 序列化在锁外，让 emit() 不被阻塞
        hist_out = {}
        for k, v in histograms.items():
            hist_out[k] = {"count": v[0], "sum": v[1], "buckets": list(v[2])}
        return {
            "window_start": window_start,
            "window_end": self._window_start,
            # 客户端本地日历天 —— 服务端必须按这个落 stat_date，否则跨时区
            # 设备午夜前后上报会把同一天的 usage 和 instrument 拆到两天。
            "stat_date": _date.today().isoformat(),
            "bounds": list(_HIST_BOUNDS),
            "counters": counters,
            "histograms": hist_out,
        }


# ---------------------------------------------------------------------------
# 辅助：key 序列化
# ---------------------------------------------------------------------------


def _esc_dim(s) -> str:
    r"""Escape metric_key separators (``\`` ``|`` ``,`` ``=``).

    Frontend WS telemetry accepts arbitrary string dim values; if a value contains ``,``
    or ``=``, unescaped concatenation collapses distinct dim combos into the same
    metric_key (e.g. ``{a:"x,b=y"}`` and ``{a:"x", b:"y"}`` both become ``a=x,b=y``),
    silently confusing dashboard slices. Backslash escaping guarantees injectivity:
    distinct (k,v) sets always produce distinct keys (Codex). ``\`` is escaped first,
    so other escape sequences never get escaped twice.
    """
    return (
        str(s)
        .replace("\\", "\\\\")
        .replace("|", "\\|")
        .replace(",", "\\,")
        .replace("=", "\\=")
    )


def _make_key(name: str, dims: dict) -> str:
    """Build a stable flat key from (name, dims).

    Format: ``name`` or ``name|k1=v1,k2=v2`` (dims joined in key lexicographic order;
    both k/v go through _esc_dim separator escaping). Without dims the ``|`` is
    omitted, keeping simple-case keys short.

    Values are converted with ``str()`` — callers are obligated to pass only
    serializable, low-cardinality dimensions.

    name is _esc_dim-escaped too: an untrusted WS client could send a name containing
    ``|`` ``,`` ``=`` (like ``foo|a=1``) that collides with a legitimate
    ``name=foo,dims={a:1}``, silently confusing counters/histograms (Codex). Legal
    names (snake_case) carry no separators, so escaping is a no-op.
    """
    if not dims:
        return _esc_dim(name)
    parts = [f"{_esc_dim(k)}={_esc_dim(dims[k])}" for k in sorted(dims.keys())]
    return f"{_esc_dim(name)}|{','.join(parts)}"


# ---------------------------------------------------------------------------
# 模块级便捷函数（业务侧首选入口）
# ---------------------------------------------------------------------------


def counter(name: str, value: int = 1, **dims) -> None:
    Instrument.get_instance().counter(name, value, **dims)


def histogram(name: str, value: float, **dims) -> None:
    Instrument.get_instance().histogram(name, value, **dims)


def event(name: str, **fields) -> None:
    Instrument.get_instance().event(name, **fields)


def snapshot() -> dict:
    """Called by TokenTracker; business code usually doesn't need it."""
    return Instrument.get_instance().snapshot()


def has_data() -> bool:
    """For TokenTracker to peek; business code usually doesn't need it."""
    return Instrument.get_instance().has_data()
