"""Phase 3 cache tests for ``LLMGateway``.

Covers two new behaviours added in the Galgame plugin context optimisation
Phase 3:

* Per-operation cache TTL (``llm_explain_cache_ttl_seconds``,
  ``llm_choice_cache_ttl_seconds`` etc.).
* Near-match cache (``llm_near_match_cache_enabled``) — only enabled for
  ``explain_line`` and ``summarize_scene`` and guarded by
  ``_validate_near_match`` against stale ``stable_lines`` / ``current_line``
  / ``observed_lines``.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from plugin.plugins.galgame_plugin.llm_gateway import (
    LLMGateway,
    _NEAR_MATCH_OBSERVED_SIMILARITY_THRESHOLD,
    _hash_line,
    _hash_stable_lines,
    _line_similarity_signature,
    _observed_similarity,
)


class _Logger:
    def info(self, *_args: Any, **_kwargs: Any) -> None:
        return None

    def warning(self, *_args: Any, **_kwargs: Any) -> None:
        return None

    def error(self, *_args: Any, **_kwargs: Any) -> None:
        return None

    def debug(self, *_args: Any, **_kwargs: Any) -> None:
        return None

    def exception(self, *_args: Any, **_kwargs: Any) -> None:
        return None


class _ExplainBackend:
    def __init__(self) -> None:
        self.calls = 0

    async def invoke(self, *, operation: str, context: dict[str, Any]) -> dict[str, Any]:
        assert operation == "explain_line"
        self.calls += 1
        return {
            "explanation": f"explanation-{self.calls}",
            "evidence": [
                {
                    "type": "current_line",
                    "text": str(context.get("current_line", {}).get("text") or ""),
                    "line_id": str(context.get("current_line", {}).get("line_id") or ""),
                    "speaker": str(context.get("current_line", {}).get("speaker") or ""),
                    "scene_id": str(context.get("scene_id") or ""),
                    "route_id": "",
                }
            ],
        }

    async def shutdown(self) -> None:
        return None


class _ChoiceBackend:
    def __init__(self) -> None:
        self.calls = 0

    async def invoke(self, *, operation: str, context: dict[str, Any]) -> dict[str, Any]:
        assert operation == "suggest_choice"
        self.calls += 1
        visible = context.get("visible_choices") or []
        return {
            "choices": [
                {
                    "choice_id": str(item.get("choice_id") or ""),
                    "text": str(item.get("text") or ""),
                    "rank": idx + 1,
                    "reason": "default",
                }
                for idx, item in enumerate(visible)
            ]
        }

    async def shutdown(self) -> None:
        return None


def _make_config(**overrides: Any) -> SimpleNamespace:
    base = {
        "llm_max_in_flight": 4,
        "llm_call_timeout_seconds": 5.0,
        "llm_request_cache_ttl_seconds": 2.0,
        "llm_scene_summary_cache_ttl_seconds": 10.0,
        "llm_target_entry_ref": "",
        "context_counting_mode": "char",
        "context_max_tokens": 6000,
        "context_metrics_enabled": False,
        "llm_explain_cache_ttl_seconds": 8.0,
        "llm_choice_cache_ttl_seconds": 4.0,
        "llm_near_match_cache_enabled": False,
        "llm_near_match_cache_ttl_seconds": 15.0,
    }
    base.update(overrides)
    return SimpleNamespace(**base)


def _explain_context(
    *,
    scene_id: str = "scene-a",
    current_text: str = "current",
    stable_text: str = "stable",
    observed_text: str = "observed",
    screen_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "scene_id": scene_id,
        "current_line": {
            "line_id": "line-100",
            "speaker": "雪乃",
            "text": current_text,
        },
        "stable_lines": [
            {"line_id": "line-1", "speaker": "雪乃", "text": stable_text},
        ],
        "observed_lines": [
            {"line_id": "line-99", "speaker": "雪乃", "text": observed_text},
        ],
        "screen_context": screen_context or {},
        "diagnostic": "",
    }


@pytest.mark.plugin_unit
def test_ttl_for_operation_returns_per_operation_values() -> None:
    config = _make_config(
        llm_request_cache_ttl_seconds=2.0,
        llm_scene_summary_cache_ttl_seconds=10.0,
        llm_explain_cache_ttl_seconds=8.0,
        llm_choice_cache_ttl_seconds=4.0,
    )
    gateway = LLMGateway(plugin=SimpleNamespace(plugins=None), logger=_Logger(), config=config)

    assert gateway._ttl_for_operation("explain_line") == 8.0
    assert gateway._ttl_for_operation("summarize_scene") == 10.0
    assert gateway._ttl_for_operation("scene_summary") == 10.0
    assert gateway._ttl_for_operation("suggest_choice") == 4.0
    assert gateway._ttl_for_operation("agent_reply") == 2.0
    assert gateway._ttl_for_operation("unknown") == 2.0


@pytest.mark.plugin_unit
def test_ttl_for_operation_falls_back_to_request_ttl_for_invalid_values() -> None:
    config = _make_config(
        llm_request_cache_ttl_seconds=3.0,
        llm_explain_cache_ttl_seconds="not-a-number",
        llm_choice_cache_ttl_seconds=-5.0,
    )
    gateway = LLMGateway(plugin=SimpleNamespace(plugins=None), logger=_Logger(), config=config)

    assert gateway._ttl_for_operation("explain_line") == 3.0
    assert gateway._ttl_for_operation("suggest_choice") == 0.0


@pytest.mark.plugin_unit
def test_near_match_disabled_by_default() -> None:
    gateway = LLMGateway(
        plugin=SimpleNamespace(plugins=None),
        logger=_Logger(),
        config=_make_config(),
    )
    assert gateway._near_match_enabled() is False


@pytest.mark.plugin_unit
def test_near_match_fingerprint_excludes_volatile_fields() -> None:
    gateway = LLMGateway(
        plugin=SimpleNamespace(plugins=None),
        logger=_Logger(),
        config=_make_config(llm_near_match_cache_enabled=True),
    )
    base = _explain_context()
    with_screen = _explain_context(screen_context={"background": "abc"})
    with_diagnostic = dict(base)
    with_diagnostic["diagnostic"] = "some warning"
    fp_base = gateway._near_match_fingerprint("explain_line", base)
    fp_screen = gateway._near_match_fingerprint("explain_line", with_screen)
    fp_diag = gateway._near_match_fingerprint("explain_line", with_diagnostic)
    assert fp_base is not None
    # screen_context and diagnostic are excluded → identical fingerprint
    assert fp_screen == fp_base
    assert fp_diag == fp_base


@pytest.mark.plugin_unit
def test_near_match_fingerprint_skipped_for_unsupported_operations() -> None:
    gateway = LLMGateway(
        plugin=SimpleNamespace(plugins=None),
        logger=_Logger(),
        config=_make_config(llm_near_match_cache_enabled=True),
    )
    context = _explain_context()
    assert gateway._near_match_fingerprint("suggest_choice", context) is None
    assert gateway._near_match_fingerprint("agent_reply", context) is None


@pytest.mark.plugin_unit
def test_validate_near_match_rejects_mismatched_stable_or_current_line() -> None:
    context = _explain_context()
    meta = LLMGateway._build_near_match_meta(context)

    assert LLMGateway._validate_near_match(meta, context) is True

    other_stable = _explain_context(stable_text="different stable")
    assert LLMGateway._validate_near_match(meta, other_stable) is False

    other_current = _explain_context(current_text="different current")
    assert LLMGateway._validate_near_match(meta, other_current) is False


@pytest.mark.plugin_unit
def test_validate_near_match_rejects_low_observed_similarity() -> None:
    cached_context = _explain_context(observed_text="今天发生了一件惊天动地的大事情啊")
    meta = LLMGateway._build_near_match_meta(cached_context)
    diverged = _explain_context(observed_text="完全不一样的内容啦啦啦XYZ")
    assert LLMGateway._validate_near_match(meta, diverged) is False


@pytest.mark.plugin_unit
def test_observed_similarity_helpers_jaccard_behaviour() -> None:
    sig_a = _line_similarity_signature(
        [{"speaker": "雪乃", "text": "她说：今天天气真好啊，要不要去外面散步呢？"}]
    )
    sig_b = _line_similarity_signature(
        [{"speaker": "雪乃", "text": "她说：今天天气真好啊，要不要去外面散步呢。"}]
    )
    sig_c = _line_similarity_signature(
        [{"speaker": "雪乃", "text": "完全不同的句子"}]
    )
    assert "今天天气真好" in sig_a
    assert _observed_similarity(sig_a, sig_b) >= _NEAR_MATCH_OBSERVED_SIMILARITY_THRESHOLD
    assert _observed_similarity(sig_a, sig_c) < _NEAR_MATCH_OBSERVED_SIMILARITY_THRESHOLD
    assert _observed_similarity("", "anything") == 0.0
    assert _observed_similarity("anything", "") == 0.0


@pytest.mark.plugin_unit
def test_hash_helpers_normalise_inputs() -> None:
    assert _hash_stable_lines(None) == ""
    assert _hash_line(None) == ""
    assert _hash_line({"speaker": "a", "text": "b"}) == _hash_line(
        {"speaker": "a", "text": "b", "ignored": "x"}
    )


@pytest.mark.asyncio
@pytest.mark.plugin_unit
async def test_near_match_cache_returns_cached_payload_for_similar_context() -> None:
    backend = _ExplainBackend()
    config = _make_config(
        llm_near_match_cache_enabled=True,
        llm_near_match_cache_ttl_seconds=30.0,
        llm_explain_cache_ttl_seconds=0.0,  # disable exact cache to isolate near-match
    )
    gateway = LLMGateway(
        plugin=SimpleNamespace(plugins=None),
        logger=_Logger(),
        config=config,
        backend=backend,
    )

    first_context = _explain_context(
        observed_text="她对春希说：今天天气真好啊，要不要去外面散步呢",
    )
    # Slightly edited observed text and volatile screen_context differ, but
    # the normalized text remains similar enough for near-match reuse.
    second_context = _explain_context(
        observed_text="她对春希说：今天天气真好啊，要不要去外面散步呢。",
        screen_context={"background": "different"},
    )

    first = await gateway.explain_line(first_context)
    second = await gateway.explain_line(second_context)
    await gateway.shutdown()

    assert first["explanation"] == "explanation-1"
    assert second["explanation"] == "explanation-1"  # served by near-match
    assert backend.calls == 1


@pytest.mark.asyncio
@pytest.mark.plugin_unit
async def test_near_match_cache_rejects_different_scene_or_stable_lines() -> None:
    backend = _ExplainBackend()
    config = _make_config(
        llm_near_match_cache_enabled=True,
        llm_explain_cache_ttl_seconds=0.0,
    )
    gateway = LLMGateway(
        plugin=SimpleNamespace(plugins=None),
        logger=_Logger(),
        config=config,
        backend=backend,
    )

    await gateway.explain_line(_explain_context())
    # Different scene_id → fingerprint differs → not a near-match hit
    await gateway.explain_line(_explain_context(scene_id="scene-b"))
    # Same fingerprint but different stable_lines → _validate_near_match
    # rejects → fresh backend call
    diverged = _explain_context(stable_text="完全不同的剧情")
    await gateway.explain_line(diverged)
    await gateway.shutdown()

    assert backend.calls == 3


@pytest.mark.asyncio
@pytest.mark.plugin_unit
async def test_near_match_cache_disabled_does_not_store_or_hit() -> None:
    backend = _ExplainBackend()
    config = _make_config(
        llm_near_match_cache_enabled=False,
        llm_explain_cache_ttl_seconds=0.0,
    )
    gateway = LLMGateway(
        plugin=SimpleNamespace(plugins=None),
        logger=_Logger(),
        config=config,
        backend=backend,
    )

    await gateway.explain_line(_explain_context())
    await gateway.explain_line(_explain_context())
    await gateway.shutdown()

    assert backend.calls == 2
    # Disabled: no entries added to the near-match cache.
    assert len(gateway._near_match_cache) == 0


@pytest.mark.asyncio
@pytest.mark.plugin_unit
async def test_near_match_cache_does_not_apply_to_suggest_choice() -> None:
    backend = _ChoiceBackend()
    config = _make_config(
        llm_near_match_cache_enabled=True,
        llm_choice_cache_ttl_seconds=0.0,
    )
    gateway = LLMGateway(
        plugin=SimpleNamespace(plugins=None),
        logger=_Logger(),
        config=config,
        backend=backend,
    )

    visible_choices = [{"choice_id": "c1", "text": "选择一", "index": 0, "enabled": True}]
    context_one: dict[str, Any] = {
        "scene_id": "scene-a",
        "visible_choices": visible_choices,
        "current_snapshot": {"choices": visible_choices, "scene_id": "scene-a"},
    }
    context_two = dict(context_one)
    context_two["screen_context"] = {"x": 1}

    await gateway.suggest_choice(context_one)
    await gateway.suggest_choice(context_two)
    await gateway.shutdown()

    # suggest_choice is intentionally excluded → both calls miss any cache
    assert backend.calls == 2
    assert len(gateway._near_match_cache) == 0


@pytest.mark.asyncio
@pytest.mark.plugin_unit
async def test_update_config_clears_near_match_cache_on_toggle() -> None:
    backend = _ExplainBackend()
    config = _make_config(
        llm_near_match_cache_enabled=True,
        llm_explain_cache_ttl_seconds=0.0,
    )
    gateway = LLMGateway(
        plugin=SimpleNamespace(plugins=None),
        logger=_Logger(),
        config=config,
        backend=backend,
    )
    await gateway.explain_line(_explain_context())
    assert len(gateway._near_match_cache) == 1
    gateway.update_config(_make_config(llm_near_match_cache_enabled=False))
    assert len(gateway._near_match_cache) == 0
    await gateway.shutdown()


@pytest.mark.asyncio
@pytest.mark.plugin_unit
async def test_update_config_clears_exact_cache_when_operation_ttl_changes() -> None:
    backend = _ExplainBackend()
    gateway = LLMGateway(
        plugin=SimpleNamespace(plugins=None),
        logger=_Logger(),
        config=_make_config(llm_explain_cache_ttl_seconds=60.0),
        backend=backend,
    )

    first = await gateway.explain_line(_explain_context())
    assert len(gateway._cache) == 1

    gateway.update_config(_make_config(llm_explain_cache_ttl_seconds=0.0))
    assert len(gateway._cache) == 0

    second = await gateway.explain_line(_explain_context())
    await gateway.shutdown()

    assert first["explanation"] == "explanation-1"
    assert second["explanation"] == "explanation-2"
    assert backend.calls == 2


@pytest.mark.asyncio
@pytest.mark.plugin_unit
async def test_update_config_clears_near_match_cache_when_ttl_changes() -> None:
    backend = _ExplainBackend()
    gateway = LLMGateway(
        plugin=SimpleNamespace(plugins=None),
        logger=_Logger(),
        config=_make_config(
            llm_near_match_cache_enabled=True,
            llm_near_match_cache_ttl_seconds=60.0,
            llm_explain_cache_ttl_seconds=0.0,
        ),
        backend=backend,
    )

    await gateway.explain_line(_explain_context())
    assert len(gateway._near_match_cache) == 1

    gateway.update_config(
        _make_config(
            llm_near_match_cache_enabled=True,
            llm_near_match_cache_ttl_seconds=0.0,
            llm_explain_cache_ttl_seconds=0.0,
        )
    )
    assert len(gateway._near_match_cache) == 0
    await gateway.shutdown()
