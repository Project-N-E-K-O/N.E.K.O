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
# Configuration constants
# ---------------------------------------------------------------------------

# Histogram bucket boundaries, shared by milliseconds and numeric values. Cover the main 1ms–10s range; samples beyond the rightmost
# boundary enter an overflow bucket. Fixed buckets stabilize server schema and support direct cross-version comparisons.
#
# Reasons for these boundaries:
# - The 1/2/5/10/... '1-2-5 sequence' is logarithmic while retaining readable integers
# - Covers typical TTFT (~100–2000ms), inverse FPS (~16ms), and startup time (~1–30s) ranges
# - 9 boundaries = 10 buckets, about 40B/histogram serialized, keeping overhead low
_HIST_BOUNDS: tuple = (1, 2, 5, 10, 20, 50, 100, 200, 500, 1000, 2000, 5000, 10000)
_HIST_NUM_BUCKETS = len(_HIST_BOUNDS) + 1  # One extra overflow bucket beyond the rightmost boundary

# Counter/histogram memory cap. Keys are theoretically (name, dims tuple) and should remain bounded,
# but accidental high-cardinality dimensions (such as user_id/message content) need a memory-leak
# safeguard. Drop new keys above the cap (retain existing accumulations), and warn once.
_MAX_COUNTER_KEYS = 5000
_MAX_HISTOGRAM_KEYS = 1000


# ---------------------------------------------------------------------------
# Instrument singleton
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
        # Keys are strings: "name|k1=v1,k2=v2", with dims joined in lexicographic key order.
        # Values are numbers. Flat dictionaries serialize more easily than nested dictionaries and are easier
        # for the server to index.
        self._counters: dict = {}
        # Same key structure; values are [count, sum, [bucket_counts...]]
        self._histograms: dict = {}
        # Snapshot-window start, reset on each snapshot
        self._window_start: float = time.time()
        # High-cardinality warning throttling
        self._cap_warned_counter: bool = False
        self._cap_warned_histogram: bool = False

    # ---- Public API ----

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
                # Capacity guard: silently drop when full to prevent memory exhaustion from high-cardinality dimension misuse
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
        # bisect_left: value <= bound enters that bucket; values above the rightmost boundary overflow
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
        from datetime import date as _date  # Local import avoids an early-call cycle during process startup
        with self._lock:
            if not self._counters and not self._histograms:
                # Update window_start even when empty so subsequent snapshots do not retain an empty window indefinitely:
                # otherwise, after 30 min without instrumentation and one event in minute 31,
                # the reported window would be 31min rather than 1min.
                self._window_start = time.time()
                return {}
            counters = self._counters
            histograms = self._histograms
            window_start = self._window_start
            self._counters = {}
            self._histograms = {}
            self._window_start = time.time()
            # Retain the warning flag; repeated logging is more annoying than repeated warnings
        # Serialize outside the lock to avoid blocking emit()
        hist_out = {}
        for k, v in histograms.items():
            hist_out[k] = {"count": v[0], "sum": v[1], "buckets": list(v[2])}
        return {
            "window_start": window_start,
            "window_end": self._window_start,
            # Client-local calendar day: the server must use this as stat_date, or reports around midnight from devices
            # across time zones split the same day's usage and instrumentation into two days.
            "stat_date": _date.today().isoformat(),
            "bounds": list(_HIST_BOUNDS),
            "counters": counters,
            "histograms": hist_out,
        }


# ---------------------------------------------------------------------------
# Helper: key serialization
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
# Module-level convenience functions, preferred business-code entry points
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
