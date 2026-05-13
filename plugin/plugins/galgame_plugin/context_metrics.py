"""Lightweight in-memory metrics for galgame prompt context compaction."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class ContextMetric:
    """One prompt-context size sample recorded around an LLM request."""

    operation: str
    raw_tokens: int
    compacted_tokens: int
    raw_chars: int
    compacted_chars: int
    compression_level: int
    cache_hit: bool
    total_time_ms: float


class ContextMetricsCollector:
    """Bounded ring buffer and aggregate statistics for context metrics."""

    def __init__(self, max_records: int = 500) -> None:
        self._records: deque[ContextMetric] = deque(maxlen=max(1, int(max_records)))

    def record(self, metric: ContextMetric) -> None:
        """Append one metric, evicting the oldest item when the buffer is full."""
        self._records.append(metric)

    def records(self) -> list[ContextMetric]:
        """Return a stable snapshot of buffered metrics."""
        return list(self._records)

    def summary_stats(self) -> dict[str, dict[str, Any]]:
        """Return average size and cache-hit statistics grouped by operation."""
        grouped: dict[str, list[ContextMetric]] = {}
        for metric in list(self._records):
            grouped.setdefault(metric.operation, []).append(metric)

        summary: dict[str, dict[str, Any]] = {}
        for operation, records in grouped.items():
            count = len(records)
            if count == 0:
                continue
            cache_hits = sum(1 for item in records if item.cache_hit)
            summary[operation] = {
                "count": count,
                "avg_raw_tokens": sum(item.raw_tokens for item in records) / count,
                "avg_compacted_tokens": (
                    sum(item.compacted_tokens for item in records) / count
                ),
                "avg_raw_chars": sum(item.raw_chars for item in records) / count,
                "avg_compacted_chars": sum(item.compacted_chars for item in records)
                / count,
                "avg_compression_level": (
                    sum(item.compression_level for item in records) / count
                ),
                "cache_hits": cache_hits,
                "cache_hit_rate": cache_hits / count,
                "avg_total_time_ms": sum(item.total_time_ms for item in records) / count,
            }
        return summary
