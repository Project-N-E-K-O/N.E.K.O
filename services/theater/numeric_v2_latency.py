"""Request-local timing of model waits; no prose, credentials or story-state writes."""

import asyncio
from contextlib import contextmanager
from contextvars import ContextVar
from time import monotonic as _clock
from typing import Mapping


_current: ContextVar["TurnLatency | None"] = ContextVar("numeric_v2_latency", default=None)


class TurnLatency:
    def __init__(self):
        self.started_at = _clock()
        self.calls = []

    def snapshot(self):
        total = round((_clock() - self.started_at) * 1000, 3)
        intervals = sorted((row["started_ms"], row["finished_ms"]) for row in self.calls
                           if row.get("finished_ms") is not None)
        occupied, end = 0.0, 0.0
        for start, finish in intervals:
            occupied += max(0.0, finish - max(start, end))
            end = max(end, finish)
        return {"total_wall_ms": total, "model_wall_ms": round(occupied, 3),
                "model_sum_ms": round(sum(row.get("duration_ms") or 0 for row in self.calls), 3),
                "other_wall_ms": round(max(0.0, total - occupied), 3),
                "model_calls": [dict(row) for row in self.calls]}


@contextmanager
def turn_latency_scope():
    timing = TurnLatency()
    token = _current.set(timing)
    try:
        yield timing
    finally:
        _current.reset(token)


@contextmanager
def model_call_timing(stage):
    timing = _current.get()
    if timing is None:
        yield None
        return
    started = _clock()
    row = {"stage": stage, "started_ms": round((started - timing.started_at) * 1000, 3),
           "finished_ms": None, "duration_ms": None, "status": "pending",
           "input_tokens": None, "output_tokens": None,
           "cache_read_input_tokens": None, "reasoning_tokens": None}
    timing.calls.append(row)
    try:
        yield row
    except BaseException as exc:
        row.update(status="cancelled" if isinstance(exc, asyncio.CancelledError) else "failed",
                   error_type=type(exc).__name__)
        raise
    else:
        row["status"] = "returned"
    finally:
        finished = _clock()
        row.update(finished_ms=round((finished - timing.started_at) * 1000, 3),
                   duration_ms=round((finished - started) * 1000, 3))


def reported_tokens(usage):
    """Missing provider measurements remain unknown, including cache hits and reasoning."""
    usage = usage if isinstance(usage, Mapping) else {}
    def count(value):
        return value if type(value) is int and value >= 0 else None

    prompt = count(usage.get("prompt_tokens"))
    if prompt is None and count(usage.get("input_tokens")) is not None:
        prompt = sum(count(usage.get(key)) or 0 for key in (
            "input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens"))
    cached = count(usage.get("cache_read_input_tokens"))
    details = usage.get("prompt_tokens_details")
    if cached is None and isinstance(details, Mapping):
        cached = count(details.get("cached_tokens"))
    completion_details = usage.get("completion_tokens_details")
    return {"input_tokens": prompt,
            "output_tokens": count(usage.get("completion_tokens", usage.get("output_tokens"))),
            "cache_read_input_tokens": cached,
            "reasoning_tokens": count(completion_details.get("reasoning_tokens"))
                if isinstance(completion_details, Mapping) else None}
