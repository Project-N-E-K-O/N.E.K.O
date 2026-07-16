from __future__ import annotations

import argparse
import gc
import os
import sys
from types import ModuleType, SimpleNamespace

import psutil
import pytest

from scripts import runtime_memory_baseline
from scripts.runtime_memory_baseline import (
    _embedding_scenario,
    _process_row,
    _register_embedding_service,
    _runtime_resource_counts,
    _series_summary,
)
from scripts.runtime_memory_soak import (
    FeatureUnavailable,
    _audio_cycle,
    _chat_cycle,
    _parse_features,
    _plugin_cycle,
    _slope_per_hour,
    _trend_analysis,
)


def test_process_row_records_threads_handles_and_onnx_maps() -> None:
    row = _process_row(psutil.Process(os.getpid()))

    assert row is not None
    assert row["threads"] is not None
    assert row["threads"] >= 1
    assert "handles" in row
    if row["onnx_map_count"] is not None:
        assert row["onnx_map_count"] >= 0
    if row["onnx_mapped_rss_mib"] is not None:
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
                    "rss_mib": 105.0,
                    "uss_mib": 82.0,
                    "threads": None,
                    "handles": None,
                    "onnx_map_count": None,
                    "onnx_mapped_rss_mib": None,
                }
            },
            "total": {
                "rss_mib": 105.0,
                "uss_mib": 82.0,
                "threads": None,
                "handles": None,
                "onnx_map_count": None,
                "onnx_mapped_rss_mib": None,
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
    assert summary["total"]["median_onnx_map_count"] == 2.5
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


def test_runtime_resource_counts_tracks_direct_embedding_services() -> None:
    class _Service:
        _session = object()
        _tokenizer = object()

    gc.collect()
    baseline = _runtime_resource_counts()
    service = _Service()
    _register_embedding_service(service)

    active = _runtime_resource_counts()
    assert active["embedding_service_instances"] == (
        baseline["embedding_service_instances"] + 1
    )
    assert active["embedding_session_refs"] == baseline["embedding_session_refs"] + 1
    assert active["embedding_tokenizer_refs"] == (
        baseline["embedding_tokenizer_refs"] + 1
    )

    del service
    gc.collect()
    assert _runtime_resource_counts() == baseline


@pytest.mark.asyncio
async def test_audio_cycle_skips_when_native_denoiser_is_unavailable(
    monkeypatch,
) -> None:
    instances = []

    class _AudioProcessor:
        def __init__(self, **_kwargs) -> None:
            self._denoiser = None
            self.closed = False
            instances.append(self)

        def close(self) -> None:
            self.closed = True

    audio_processor = ModuleType("utils.audio_processor")
    audio_processor.AudioProcessor = _AudioProcessor
    monkeypatch.setitem(sys.modules, "utils.audio_processor", audio_processor)

    with pytest.raises(FeatureUnavailable, match="native_denoiser_unavailable"):
        await _audio_cycle(SimpleNamespace(audio_chunks=1), 1)

    assert instances[0].closed is True


@pytest.mark.asyncio
async def test_plugin_cycle_rejects_unsuccessful_trigger(monkeypatch, tmp_path) -> None:
    instances = []

    class _PluginProcessHost:
        def __init__(self, **_kwargs) -> None:
            self.shutdown_called = False
            instances.append(self)

        async def start(self, **_kwargs) -> None:
            return None

        async def trigger(self, *_args, **_kwargs) -> dict[str, bool]:
            return {"ok": False}

        async def shutdown(self, **_kwargs) -> None:
            self.shutdown_called = True

    host_module = ModuleType("plugin.core.host")
    host_module.PluginProcessHost = _PluginProcessHost
    monkeypatch.setitem(sys.modules, "plugin.core.host", host_module)
    config_path = tmp_path / "plugin.toml"
    config_path.write_text("[plugin]\nname='synthetic'\n", encoding="utf-8")

    with pytest.raises(RuntimeError, match="synthetic_plugin_trigger_failed"):
        await _plugin_cycle(
            SimpleNamespace(
                plugin_config=str(config_path),
                plugin_entry="synthetic:Plugin",
                plugin_timeout=1.0,
            ),
            1,
        )

    assert instances[0].shutdown_called is True


@pytest.mark.asyncio
async def test_chat_cycle_requires_backend_pid_for_resource_sampling() -> None:
    with pytest.raises(
        FeatureUnavailable, match="verified_isolated_chat_backend_pid_not_configured"
    ):
        await _chat_cycle(SimpleNamespace(chat_port=48911, chat_pid=0), 1)


def test_parse_features_deduplicates_and_rejects_unknown_names() -> None:
    assert _parse_features("audio,ocr,audio") == ["audio", "ocr"]
    with pytest.raises(argparse.ArgumentTypeError, match="unknown feature"):
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
