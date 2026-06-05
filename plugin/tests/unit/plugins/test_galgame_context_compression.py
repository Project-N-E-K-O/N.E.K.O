from __future__ import annotations

from typing import Any

import pytest

from plugin.plugins.galgame_plugin.context_builder import build_fallback_summary
from plugin.plugins.galgame_plugin.cross_scene_memory import (
    LOW_CONFIDENCE_THRESHOLD,
    REBUILD_AFTER_STREAK,
    empty_memory,
    parse_memory_update_response,
    render_for_push,
    sanitize_memory,
    update_cross_scene_memory,
)
from plugin.plugins.galgame_plugin.layered_memory import LayeredMemory
from plugin.plugins.galgame_plugin.push_composer import (
    MAX_PUSH_TOKENS,
    PLOT_MIN_TOKENS,
    PushComposer,
)


# ---------------------------------------------------------------------------
# PushComposer — token counting + composition
# ---------------------------------------------------------------------------


def _basic_plot_payload(seq: int = 1, key_line_count: int = 2) -> dict[str, Any]:
    return {
        "push_seq": seq,
        "mode": "incremental",
        "scene_id": "scene_12",
        "scene_anchor": {
            "location": "建实神社",
            "characters_present": ["叢雨", "有地将臣"],
            "since_push_seq": max(0, seq - 1),
        },
        "plot_update": {
            "summary": "将臣发现叢雨偷偷为他找回护身符，叢雨慌乱嘴硬。",
            "key_lines": [
                {"speaker": "叢雨", "text": f"わらわは別に気にしてなどおらぬ_{i}"}
                for i in range(key_line_count)
            ],
            "new_choices": [
                {"text": "坦率道谢", "context": "直接表达感激"},
            ],
        },
        "available_queries": [
            {"entry": "galgame_get_scene_context", "description": "..."},
        ],
    }


def _basic_character_payload() -> dict[str, Any]:
    return {
        "format": "character_payload",
        "level": "L1",
        "fixed_character": "叢雨",
        "characters": {
            "叢雨": "【叢雨】身份：丛雨丸的刀灵\n当前情绪：慌乱嘴硬",
        },
    }


def test_compose_returns_self_contained_payload() -> None:
    composer = PushComposer()
    result = composer.compose(
        _basic_plot_payload(),
        _basic_character_payload(),
        push_seq=5,
        mode="incremental",
    )
    assert result["push_seq"] == 5
    assert result["mode"] == "incremental"
    assert result["format_version"] == 1
    assert result["plot"]["scene_id"] == "scene_12"
    assert result["characters"]["fixed_character"] == "叢雨"
    assert result["_metrics"]["total_tokens"] > 0
    assert result["_metrics"]["total_tokens"] <= MAX_PUSH_TOKENS
    assert result["_metrics"]["truncated_plot"] is False
    assert any(
        q["entry"] == "galgame_get_scene_context" for q in result["available_queries"]
    )


def test_compose_handles_missing_blocks() -> None:
    composer = PushComposer()
    result = composer.compose(None, None, push_seq=1, mode="full")
    assert result["plot"] is None
    assert result["characters"] is None
    assert result["_metrics"]["total_tokens"] == 0
    assert result["available_queries"] == []


def test_wrap_push_message_drops_empty_blocks() -> None:
    composer = PushComposer()
    text = composer.wrap_push_message("", "character only")
    assert text.startswith("======[游戏动态]")
    assert "character only" in text
    assert "📍" not in text  # no plot block leaked through


def test_enforce_budget_trims_oldest_key_lines() -> None:
    # Choose a budget above PLOT_MIN_TOKENS so the trim path is exercised but
    # the floor doesn't dominate.
    budget = PLOT_MIN_TOKENS + 60
    composer = PushComposer(max_tokens=budget)
    payload = _basic_plot_payload(key_line_count=8)
    result = composer.compose(payload, None, push_seq=1, mode="incremental")
    assert result["_metrics"]["truncated_plot"] is True
    # Tolerate small marker overshoot (a few tokens for the …[truncated] suffix)
    assert result["_metrics"]["total_tokens"] <= budget + 8


def test_enforce_budget_keeps_min_floor_when_budget_is_tight() -> None:
    composer = PushComposer(max_tokens=PLOT_MIN_TOKENS - 30)
    payload = _basic_plot_payload(key_line_count=3)
    result = composer.compose(payload, None, push_seq=1, mode="incremental")
    # When budget < PLOT_MIN_TOKENS we keep PLOT_MIN_TOKENS as a readability
    # floor and surface the truncation flag.
    assert result["_metrics"]["truncated_plot"] is True
    assert result["_metrics"]["plot_tokens"] <= PLOT_MIN_TOKENS + 8


def test_count_tokens_falls_back_when_tokenizer_missing() -> None:
    composer = PushComposer(encoding_name="not_a_real_encoding")
    # No tokenizer resolved → heuristic path
    assert composer._count_tokens("你好世界") > 0


# ---------------------------------------------------------------------------
# LayeredMemory — Layer 0 / 1 / 2 behavior
# ---------------------------------------------------------------------------


def test_layered_memory_line_buffer_ring_size() -> None:
    mem = LayeredMemory(max_lines=4)
    for i in range(10):
        mem.append_line({"scene_id": "s1", "text": f"L{i}"})
    assert mem.line_count() == 4
    recent = mem.get_recent_lines(4)
    assert [line["text"] for line in recent] == ["L6", "L7", "L8", "L9"]


def test_layered_memory_extend_lines_atomic() -> None:
    mem = LayeredMemory(max_lines=5)
    mem.extend_lines(
        [{"text": "a"}, "garbage", {"text": "b"}, {"text": "c"}]
    )
    assert [line["text"] for line in mem.get_recent_lines(10)] == ["a", "b", "c"]


def test_layered_memory_scene_summary_ring_and_lookup() -> None:
    mem = LayeredMemory(max_scenes=3)
    for i in range(5):
        mem.add_scene_summary(
            f"scene_{i}",
            f"summary {i}",
            key_lines=[{"speaker": "A", "text": str(i)}],
            push_seq=i,
        )
    assert mem.scene_count() == 3
    ctx = mem.get_scene_context()
    assert ctx is not None
    assert ctx["scene_id"] == "scene_4"


def test_layered_memory_scene_lookup_includes_lines_in_scene() -> None:
    mem = LayeredMemory()
    mem.extend_lines(
        [
            {"scene_id": "scene_a", "text": "A1"},
            {"scene_id": "scene_b", "text": "B1"},
            {"scene_id": "scene_a", "text": "A2"},
        ]
    )
    mem.add_scene_summary("scene_a", "summary A", key_lines=[])
    ctx = mem.get_scene_context("scene_a")
    assert ctx is not None
    assert [line["text"] for line in ctx["recent_lines"]] == ["A1", "A2"]


def test_layered_memory_story_so_far_default_text() -> None:
    mem = LayeredMemory()
    assert mem.get_story_so_far() == "故事刚开始。"
    assert mem.has_story_so_far() is False


@pytest.mark.asyncio
async def test_update_story_so_far_swaps_atomically() -> None:
    mem = LayeredMemory()
    mem.add_scene_summary("scene_1", "scene1 summary", push_seq=10)
    mem.add_scene_summary("scene_2", "scene2 summary", push_seq=20)

    class _Summarizer:
        def __init__(self) -> None:
            self.calls: list[tuple[str, tuple[str, ...]]] = []

        async def summarize_story(
            self, *, current_story: str, new_scenes: list[str]
        ) -> str:
            self.calls.append((current_story, tuple(new_scenes)))
            return "新的全局摘要"

    summarizer = _Summarizer()
    changed = await mem.update_story_so_far(summarizer)
    assert changed is True
    assert mem.get_story_so_far() == "新的全局摘要"
    assert mem.story_last_updated_seq == 20
    assert summarizer.calls[-1][1] == ("scene1 summary", "scene2 summary")


@pytest.mark.asyncio
async def test_update_story_so_far_keeps_state_on_empty_response() -> None:
    mem = LayeredMemory()
    mem.set_story_so_far("已有摘要", push_seq=5)
    mem.add_scene_summary("scene_1", "x", push_seq=10)

    class _Summarizer:
        async def summarize_story(
            self, *, current_story: str, new_scenes: list[str]
        ) -> str:
            return "   "  # empty after strip

    changed = await mem.update_story_so_far(_Summarizer())
    assert changed is False
    assert mem.get_story_so_far() == "已有摘要"
    assert mem.story_last_updated_seq == 5


def test_layered_memory_snapshot_round_trip() -> None:
    mem = LayeredMemory(max_lines=10, max_scenes=10)
    mem.append_line({"text": "a", "scene_id": "s1"})
    mem.add_scene_summary("s1", "summary", push_seq=2)
    mem.set_story_so_far("故事", push_seq=2)
    payload = mem.snapshot()

    restored = LayeredMemory(max_lines=10, max_scenes=10)
    restored.restore(payload)
    assert restored.get_recent_lines(10) == [{"text": "a", "scene_id": "s1"}]
    assert restored.get_scene_context("s1")["summary"] == "summary"
    assert restored.get_story_so_far() == "故事"
    assert restored.story_last_updated_seq == 2


# ---------------------------------------------------------------------------
# Fallback summary template (from mechanical phase) interplay
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# CrossSceneMemory — incremental update + rebuild gating
# ---------------------------------------------------------------------------


class _StubUpdater:
    """Synchronous-style stub returning a canned response."""

    def __init__(self, response: str) -> None:
        self.response = response
        self.calls: list[dict[str, Any]] = []

    async def update_memory(
        self,
        *,
        current_memory: dict[str, Any],
        recent_scene_summaries: list[dict[str, Any]],
        full_rebuild: bool,
    ) -> str:
        self.calls.append(
            {
                "current_memory": dict(current_memory),
                "recent_scene_summaries": list(recent_scene_summaries),
                "full_rebuild": full_rebuild,
            }
        )
        return self.response


def test_sanitize_memory_yields_canonical_shape() -> None:
    raw: dict[str, Any] = {
        "characters": {
            "x": {"arc": "stage", "confidence": "0.5"},
            "missing_dict": "ignored",  # type: ignore[dict-item]
        },
        "plot_threads": [{"thread": "t", "status": "s", "key_scenes": ["a", "", "b"]}],
        "last_updated_seq": "42",
    }
    sanitized = sanitize_memory(raw)
    assert sanitized["characters"]["x"]["confidence"] == 0.5
    assert "missing_dict" not in sanitized["characters"]
    assert sanitized["plot_threads"][0]["key_scenes"] == ["a", "b"]
    assert sanitized["last_updated_seq"] == 42


def test_sanitize_memory_defaults_non_numeric_sequences() -> None:
    sanitized = sanitize_memory(
        {
            "last_updated_seq": "NaN",
            "low_confidence_streak": "oops",
            "plot_threads": [{"thread": "t", "updated_at_seq": "bad"}],
        }
    )

    assert sanitized["last_updated_seq"] == 0
    assert sanitized["low_confidence_streak"] == 0
    assert sanitized["plot_threads"][0]["updated_at_seq"] == 0


def test_parse_memory_update_response_handles_fenced_block() -> None:
    raw = "```json\n{\"characters\": {}}\n```"
    parsed = parse_memory_update_response(raw)
    assert isinstance(parsed, dict)
    assert parsed["characters"] == {}


def test_parse_memory_update_response_returns_none_on_bad_json() -> None:
    assert parse_memory_update_response("not json") is None
    assert parse_memory_update_response("[\"array\", \"not\", \"object\"]") is None


@pytest.mark.asyncio
async def test_update_cross_scene_memory_records_high_confidence() -> None:
    updater = _StubUpdater(
        response=(
            '{"characters": {"叢雨": {"arc": "接纳感情", '
            '"current_emotion": "慌乱", "confidence": 0.8}}, '
            '"plot_threads": [{"thread": "感情萌芽", "status": "嘴硬", '
            '"confidence": 0.85}]}'
        )
    )
    result = await update_cross_scene_memory(
        current_memory=empty_memory(),
        scene_memory=[
            {"scene_id": "scene_5", "summary": "叢雨深夜翻找护身符", "push_seq": 5}
        ],
        updater=updater,
        push_seq=5,
    )
    assert result.updated is True
    assert result.confidence > LOW_CONFIDENCE_THRESHOLD
    assert result.memory["low_confidence_streak"] == 0
    assert result.memory["last_updated_seq"] == 5


@pytest.mark.asyncio
async def test_update_cross_scene_memory_bumps_streak_on_parse_error() -> None:
    base = sanitize_memory({"low_confidence_streak": 1})
    updater = _StubUpdater(response="not_a_valid_json")
    result = await update_cross_scene_memory(
        current_memory=base,
        scene_memory=[{"scene_id": "s1", "summary": "x", "push_seq": 1}],
        updater=updater,
        push_seq=1,
    )
    assert result.updated is False
    assert result.memory["low_confidence_streak"] == 2
    assert result.parse_error.startswith("not_a_valid_json")


@pytest.mark.asyncio
async def test_update_cross_scene_memory_triggers_rebuild_after_streak() -> None:
    base = sanitize_memory(
        {"low_confidence_streak": REBUILD_AFTER_STREAK}
    )
    updater = _StubUpdater(
        response='{"characters": {"x": {"arc": "y", "confidence": 0.9}}}'
    )
    scenes = [
        {"scene_id": f"scene_{i}", "summary": f"s{i}", "push_seq": i}
        for i in range(7)
    ]
    result = await update_cross_scene_memory(
        current_memory=base,
        scene_memory=scenes,
        updater=updater,
        push_seq=7,
    )
    assert result.updated is True
    assert result.triggered_rebuild is True
    # On rebuild, ``current_memory`` passed to updater must be empty
    assert updater.calls[-1]["current_memory"] == {}
    # Window size on rebuild is 5
    assert len(updater.calls[-1]["recent_scene_summaries"]) == 5


def test_render_for_push_skips_when_empty() -> None:
    assert render_for_push(empty_memory()) == ""
    assert render_for_push(None) == ""


def test_render_for_push_truncates_long_output() -> None:
    memory = sanitize_memory(
        {
            "characters": {
                f"char_{i}": {"arc": "long arc text " * 4, "current_emotion": "x"}
                for i in range(10)
            }
        }
    )
    rendered = render_for_push(memory, max_chars=80)
    assert len(rendered) <= 80


# ---------------------------------------------------------------------------
# Fallback summary template (from mechanical phase) interplay
# ---------------------------------------------------------------------------


def test_fallback_summary_is_template_not_semicolon_list() -> None:
    lines = [
        {
            "speaker": "叢雨",
            "text": "わらわは別に気にしてなどおらぬ",
            "_importance_score": 2.5,
        },
        {"speaker": "有地将臣", "text": "でも顔が赤いぞ", "_importance_score": 1.5},
        {"speaker": "叢雨", "text": "ち、違う！", "_importance_score": 2.0},
    ]
    summary = build_fallback_summary("scene_12", lines, [], snapshot={})
    assert "场景 scene_12" in summary
    assert "关键对白" in summary
    # Should NOT be a simple semicolon-joined dump
    assert ";" not in summary
    assert "；" not in summary or summary.count("；") < 5
