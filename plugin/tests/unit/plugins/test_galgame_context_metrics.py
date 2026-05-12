from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from plugin.plugins.galgame_plugin.context_metrics import (
    ContextMetric,
    ContextMetricsCollector,
)
from plugin.plugins.galgame_plugin.llm_backend import GalgameLLMBackend
from plugin.plugins.galgame_plugin import llm_gateway as llm_gateway_module
from plugin.plugins.galgame_plugin.llm_gateway import LLMGateway


class _Backend:
    async def invoke(self, *, operation: str, context: dict[str, Any]) -> dict[str, Any]:
        del operation, context
        return {
            "summary": "ok",
            "key_points": [
                {
                    "type": "plot",
                    "text": "ok",
                    "line_id": "",
                    "speaker": "",
                    "scene_id": "",
                    "route_id": "",
                }
            ],
        }

    async def shutdown(self) -> None:
        return None

    def consume_prompt_metadata(self) -> dict[str, Any]:
        return {
            "raw_tokens": 100,
            "compacted_tokens": 40,
            "raw_chars": 200,
            "compacted_chars": 80,
            "compression_level": 2,
        }


class _ConfigAwareBackend:
    def __init__(self, config: SimpleNamespace) -> None:
        self._config = config
        self.calls = 0

    async def invoke(self, *, operation: str, context: dict[str, Any]) -> dict[str, Any]:
        del context
        self.calls += 1
        assert operation == "summarize_scene"
        return {
            "summary": (
                f"{self._config.context_counting_mode}:"
                f"{self._config.context_max_tokens}"
            ),
            "key_points": [],
        }

    async def shutdown(self) -> None:
        return None

    def consume_prompt_metadata(self) -> dict[str, Any]:
        return {}


class _RealPromptBackend(GalgameLLMBackend):
    async def _call_model(self, *, operation: str, messages):
        self.last_messages = messages
        assert operation == "agent_reply"
        assert "context:" in messages[-1]["content"]
        return '{"reply": "ok"}'


def _config(**overrides: Any) -> SimpleNamespace:
    values = {
        "llm_target_entry_ref": "",
        "llm_call_timeout_seconds": 1.0,
        "llm_max_in_flight": 2,
        "llm_request_cache_ttl_seconds": 0.0,
        "llm_scene_summary_cache_ttl_seconds": 0.0,
        "context_metrics_enabled": False,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def test_context_metrics_collector_uses_ring_buffer() -> None:
    collector = ContextMetricsCollector()
    for index in range(510):
        collector.record(
            ContextMetric(
                operation="op",
                raw_tokens=index,
                compacted_tokens=index,
                raw_chars=index,
                compacted_chars=index,
                compression_level=0,
                cache_hit=False,
                total_time_ms=1.0,
            )
        )

    records = collector.records()
    assert len(records) == 500
    assert records[0].raw_tokens == 10


def test_context_metrics_summary_stats_groups_by_operation() -> None:
    collector = ContextMetricsCollector()
    collector.record(ContextMetric("explain", 100, 50, 200, 100, 1, False, 10.0))
    collector.record(ContextMetric("explain", 300, 150, 600, 300, 3, True, 30.0))
    collector.record(ContextMetric("summarize", 10, 10, 20, 20, 0, False, 5.0))

    summary = collector.summary_stats()

    assert summary["explain"]["count"] == 2
    assert summary["explain"]["avg_raw_tokens"] == 200
    assert summary["explain"]["avg_compression_level"] == 2
    assert summary["explain"]["cache_hits"] == 1
    assert summary["explain"]["cache_hit_rate"] == 0.5
    assert summary["summarize"]["count"] == 1


def test_llm_gateway_metric_recording_preserves_zero_metadata_values() -> None:
    gateway = LLMGateway(
        None,
        None,
        _config(context_metrics_enabled=True),
        backend=_Backend(),
    )

    gateway._record_context_metric(
        operation="agent_reply",
        context={},
        prompt_metadata={
            "raw_tokens": 0,
            "compacted_tokens": 0,
            "raw_chars": 0,
            "compacted_chars": 0,
            "compression_level": 0,
        },
        cache_hit=False,
        total_time_ms=0.0,
    )

    assert gateway.context_metrics is not None
    record = gateway.context_metrics.records()[0]
    assert record.raw_tokens == 0
    assert record.compacted_tokens == 0
    assert record.raw_chars == 0
    assert record.compacted_chars == 0


def test_llm_gateway_metric_recording_does_not_render_context_with_complete_metadata(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    gateway = LLMGateway(
        None,
        None,
        _config(context_metrics_enabled=True),
        backend=_Backend(),
    )

    def fail_json_dumps(*args: Any, **kwargs: Any) -> str:
        raise AssertionError("context should not be rendered when metadata is complete")

    monkeypatch.setattr(llm_gateway_module.json, "dumps", fail_json_dumps)

    gateway._record_context_metric(
        operation="agent_reply",
        context={"large": ["x"] * 100},
        prompt_metadata={
            "raw_tokens": 100,
            "compacted_tokens": 40,
            "raw_chars": 200,
            "compacted_chars": 80,
            "compression_level": 2,
        },
        cache_hit=True,
        total_time_ms=1.0,
    )

    assert gateway.context_metrics is not None
    record = gateway.context_metrics.records()[0]
    assert record.raw_tokens == 100
    assert record.raw_chars == 200


@pytest.mark.asyncio
async def test_llm_gateway_does_not_create_metrics_collector_when_disabled() -> None:
    gateway = LLMGateway(None, None, _config(), backend=_Backend())

    result = await gateway.summarize_scene({"scene_id": "scene-a", "recent_lines": []})

    assert result["degraded"] is False
    assert gateway.context_metrics is None


@pytest.mark.asyncio
async def test_llm_gateway_records_metrics_for_call_and_cache_hit() -> None:
    gateway = LLMGateway(
        None,
        None,
        _config(
            context_metrics_enabled=True,
            llm_request_cache_ttl_seconds=60.0,
            llm_scene_summary_cache_ttl_seconds=60.0,
        ),
        backend=_Backend(),
    )

    context = {"scene_id": "scene-a", "recent_lines": []}
    await gateway.summarize_scene(context)
    await gateway.summarize_scene(context)

    assert gateway.context_metrics is not None
    records = gateway.context_metrics.records()
    assert len(records) == 2
    assert records[0].cache_hit is False
    assert records[0].raw_tokens == 100
    assert records[0].compacted_tokens == 40
    assert records[1].cache_hit is True


@pytest.mark.asyncio
async def test_llm_gateway_records_cache_hit_metrics_outside_lock() -> None:
    class _LockAssertingGateway(LLMGateway):
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            super().__init__(*args, **kwargs)
            self.metric_lock_states: list[bool] = []

        def _record_context_metric(self, **kwargs: Any) -> None:
            self.metric_lock_states.append(bool(self._lock and self._lock.locked()))
            super()._record_context_metric(**kwargs)

    gateway = _LockAssertingGateway(
        None,
        None,
        _config(
            context_metrics_enabled=True,
            llm_request_cache_ttl_seconds=60.0,
            llm_scene_summary_cache_ttl_seconds=60.0,
        ),
        backend=_Backend(),
    )

    context = {"scene_id": "scene-a", "recent_lines": []}
    await gateway.summarize_scene(context)
    await gateway.summarize_scene(context)

    assert gateway.metric_lock_states == [False, False]
    assert gateway.context_metrics is not None
    assert [record.cache_hit for record in gateway.context_metrics.records()] == [
        False,
        True,
    ]


@pytest.mark.asyncio
async def test_llm_gateway_cache_is_scoped_to_prompt_budget_config() -> None:
    config = _config(
        context_counting_mode="token",
        context_max_tokens=300,
        llm_request_cache_ttl_seconds=60.0,
        llm_scene_summary_cache_ttl_seconds=60.0,
    )
    backend = _ConfigAwareBackend(config)
    gateway = LLMGateway(None, None, config, backend=backend)
    context = {"scene_id": "scene-a", "recent_lines": [{"text": "same input"}]}

    first = await gateway.summarize_scene(context)
    cached = await gateway.summarize_scene(context)
    assert first["summary"] == "token:300"
    assert cached["summary"] == "token:300"
    assert backend.calls == 1

    next_config = _config(
        context_counting_mode="token",
        context_max_tokens=1000,
        llm_request_cache_ttl_seconds=60.0,
        llm_scene_summary_cache_ttl_seconds=60.0,
    )
    gateway.update_config(next_config)

    after_update = await gateway.summarize_scene(context)

    assert after_update["summary"] == "token:1000"
    assert backend.calls == 2


@pytest.mark.asyncio
async def test_llm_gateway_records_real_prompt_metadata_end_to_end() -> None:
    backend = _RealPromptBackend(logger=None, config=_config(context_metrics_enabled=True))
    gateway = LLMGateway(
        None,
        None,
        _config(
            context_metrics_enabled=True,
            context_counting_mode="token",
            context_max_tokens=1000,
            llm_request_cache_ttl_seconds=60.0,
        ),
        backend=backend,
    )

    result = await gateway.agent_reply({"prompt": "status", "public_context": {}})

    assert result["reply"] == "ok"
    assert gateway.context_metrics is not None
    record = gateway.context_metrics.records()[0]
    assert record.operation == "agent_reply"
    assert record.cache_hit is False
    assert record.raw_tokens > 0
    assert record.compacted_tokens > 0
