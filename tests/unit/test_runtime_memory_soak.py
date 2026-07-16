from __future__ import annotations

import os
import sys
from types import ModuleType, SimpleNamespace

import psutil
import pytest

from scripts import runtime_memory_baseline
from scripts.runtime_memory_baseline import (
    _embedding_scenario,
    _process_row,
    _series_summary,
)
from scripts.runtime_memory_soak import (
    _parse_features,
    _slope_per_hour,
    _trend_analysis,
)


def test_process_row_records_threads_handles_and_onnx_maps() -> None:
    row = _process_row(psutil.Process(os.getpid()))

    assert row is not None
    assert row["threads"] is not None
    assert row["threads"] >= 1
    assert "handles" in row
    assert row["onnx_map_count"] >= 0
    assert row["onnx_mapped_rss_mib"] >= 0


def test_series_summary_aggregates_resource_high_water_marks() -> None:
    samples = [
        {
            "categories": {
                "python": {
                    "count": 1,
                    "rss_mib": 100.0,
                    "uss_mib": 80.0,
                    "threads": 4,
                    "handles": 20,
                    "onnx_map_count": 2,
                    "onnx_mapped_rss_mib": 12.0,
                }
            },
            "total": {
                "rss_mib": 100.0,
                "uss_mib": 80.0,
                "threads": 4,
                "handles": 20,
                "onnx_map_count": 2,
                "onnx_mapped_rss_mib": 12.0,
            },
            "processes": [],
        },
        {
            "categories": {
                "python": {
                    "count": 1,
                    "rss_mib": 110.0,
                    "uss_mib": 85.0,
                    "threads": 5,
                    "handles": 24,
                    "onnx_map_count": 3,
                    "onnx_mapped_rss_mib": 13.0,
                }
            },
            "total": {
                "rss_mib": 110.0,
                "uss_mib": 85.0,
                "threads": 5,
                "handles": 24,
                "onnx_map_count": 3,
                "onnx_mapped_rss_mib": 13.0,
            },
            "processes": [],
        },
    ]

    summary = _series_summary(samples)

    assert summary["total"]["max_threads"] == 5
    assert summary["total"]["max_handles"] == 24
    assert summary["total"]["max_onnx_map_count"] == 3
    assert summary["total"]["peak_onnx_mapped_rss_mib"] == 13.0
    assert summary["categories"]["python"]["max_threads"] == 5


@pytest.mark.asyncio
async def test_embedding_scenario_captures_model_id_before_close(monkeypatch) -> None:
    instances = []

    class _EmbeddingService:
        def __init__(self, **_kwargs) -> None:
            self.closed = False
            instances.append(self)

        async def request_load(self) -> bool:
            return True

        async def embed(self, _text: str) -> list[float]:
            return [0.1, 0.2, 0.3]

        def model_id(self) -> str:
            if self.closed:
                raise RuntimeError("model_id accessed after close")
            return "synthetic-3d"

        def disable_reason(self) -> str:
            return ""

        async def close(self) -> None:
            self.closed = True

    embeddings = ModuleType("memory.embeddings")
    embeddings.EmbeddingService = _EmbeddingService
    monkeypatch.setitem(sys.modules, "memory.embeddings", embeddings)
    monkeypatch.setattr(
        runtime_memory_baseline,
        "_in_process_checkpoint",
        lambda label, **_kwargs: {"label": label},
    )

    result = await _embedding_scenario(
        SimpleNamespace(embedding_root="synthetic-model-root", window=0, interval=0)
    )

    assert result["embedding"] == {
        "ready": True,
        "model_id": "synthetic-3d",
        "model_root": str(
            runtime_memory_baseline.Path("synthetic-model-root").resolve()
        ),
        "vector_dimensions": 3,
    }
    assert instances[0].closed is True


def test_parse_features_deduplicates_and_rejects_unknown_names() -> None:
    assert _parse_features("audio,ocr,audio") == ["audio", "ocr"]
    with pytest.raises(Exception, match="unknown feature"):
        _parse_features("audio,private-input")


def test_slope_per_hour_uses_elapsed_seconds() -> None:
    assert _slope_per_hour([(0.0, 10.0), (1800.0, 18.0), (3600.0, 26.0)]) == 16.0


def test_trend_analysis_distinguishes_native_growth_from_traced_heap() -> None:
    start = 10_000.0
    cycles = []
    for index in range(5):
        cycles.append(
            {
                "features": {},
                "released_checkpoint": {
                    "label": f"cycle_{index + 1:04d}_all_released",
                    "captured_perf_counter": start + index * 900.0,
                    "total": {
                        "median_rss_mib": 200.0 + index * 8.0,
                        "median_uss_mib": 150.0 + index * 6.0,
                        "median_threads": 8,
                        "median_handles": 100,
                        "median_onnx_map_count": 1,
                        "median_onnx_mapped_rss_mib": 20.0,
                    },
                    "tracemalloc": {
                        "current_mib": 40.0 + index * 0.2,
                        "peak_mib": 60.0,
                    },
                    "categories": {},
                    "resources": {
                        "embedding_session_refs": 0,
                        "rapidocr_cache_owners": 0,
                    },
                },
            }
        )

    analysis = _trend_analysis({"started_perf_counter": start, "cycles": cycles})

    assert analysis["released_series"]["uss_mib"]["slope_per_hour"] == 24.0
    assert analysis["released_series"]["traced_current_mib"]["slope_per_hour"] == 0.8
    assert (
        "native_or_allocator_retention_review_required" in analysis["heuristic_signals"]
    )
    assert (
        "onnx_dll_or_model_mapping_residency_without_python_owner"
        in analysis["heuristic_signals"]
    )
