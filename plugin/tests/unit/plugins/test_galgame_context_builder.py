from __future__ import annotations

from plugin.plugins.galgame_plugin import context_builder
from plugin.plugins.galgame_plugin.models import DATA_SOURCE_OCR_READER, GalgameLLMConfig


def test_scene_lines_filters_scene_and_keeps_tail() -> None:
    lines = [
        {"scene_id": "a", "line_id": "1"},
        {"scene_id": "b", "line_id": "2"},
        {"scene_id": "a", "line_id": "3"},
        {"scene_id": "c", "line_id": "4"},
    ]

    result = context_builder._scene_lines(
        lines,
        "a",
        limit=3,
        extra_scene_ids=["c"],
    )

    assert [item["line_id"] for item in result] == ["1", "3", "4"]
    assert result[0] is not lines[0]


def test_scene_selected_choices_filters_action_and_scene() -> None:
    choices = [
        {"action": "shown", "scene_id": "a", "choice_id": "shown"},
        {"action": "selected", "scene_id": "b", "choice_id": "other"},
        {"action": "selected", "scene_id": "a", "choice_id": "first"},
        {"action": "selected", "scene_id": "a", "choice_id": "second"},
    ]

    result = context_builder._scene_selected_choices(choices, "a", limit=1)

    assert result == [{"action": "selected", "scene_id": "a", "choice_id": "second"}]


def test_append_unique_line_dedupes_by_scene_speaker_text() -> None:
    existing = [{"scene_id": "s", "speaker": "A", "text": "hello", "line_id": "1"}]

    same = context_builder._append_unique_line(
        existing,
        {"scene_id": "s", "speaker": "A", "text": "hello", "line_id": "2"},
        limit=4,
    )
    new = context_builder._append_unique_line(
        existing,
        {"scene_id": "s", "speaker": "B", "text": "hello", "line_id": "3"},
        limit=4,
    )

    assert same == existing
    assert [item["line_id"] for item in new] == ["1", "3"]


def test_dialogue_context_lines_filters_diagnostics_and_dedupes() -> None:
    lines = [
        {"speaker": "A", "text": "hello", "scene_id": "s", "line_id": "1"},
        {"speaker": "A", "text": "hello", "scene_id": "s", "line_id": "2"},
        {"speaker": "", "text": "{\"debug\": true}", "scene_id": "s", "line_id": "debug"},
        {"speaker": "B", "text": "world", "scene_id": "s", "line_id": "3"},
    ]

    result = context_builder._dialogue_context_lines(lines, limit=10)

    assert [item["line_id"] for item in result] == ["2", "3"]


def test_build_input_degraded_context_marks_ocr_identifiers() -> None:
    source, degraded, reasons = context_builder._build_input_degraded_context(
        {"active_data_source": DATA_SOURCE_OCR_READER},
        scene_id="ocr:scene",
        line_id="ocr:line",
        choice_ids=["ocr:choice"],
    )

    assert source == DATA_SOURCE_OCR_READER
    assert degraded is True
    assert reasons == [
        "ocr_reader_source",
        "ocr_reader_scene",
        "ocr_reader_line",
        "ocr_reader_choice",
    ]


def test_resolve_target_line_prefers_history_matches() -> None:
    result = context_builder._resolve_target_line(
        {
            "latest_snapshot": {},
            "history_lines": [{"line_id": "stable", "text": "stable text"}],
            "history_observed_lines": [{"line_id": "observed", "text": "observed text"}],
        },
        line_id="observed",
    )

    assert result == {"line_id": "observed", "text": "observed text"}


def test_snapshot_for_stable_summary_seed_blanks_unstable_ocr_snapshot() -> None:
    snapshot = {
        "speaker": "A",
        "text": "unstable",
        "line_id": "line-1",
        "stability": "tentative",
    }

    result = context_builder._snapshot_for_stable_summary_seed(
        {"active_data_source": DATA_SOURCE_OCR_READER},
        snapshot,
        stable_lines=[],
    )

    assert result["speaker"] == ""
    assert result["text"] == ""
    assert result["line_id"] == ""
    assert result["stability"] == ""


def test_condense_dialogue_batch_merges_same_speaker_short_lines() -> None:
    lines = [
        {"speaker": "A", "text": "hello", "scene_id": "s", "line_id": "1"},
        {"speaker": "A", "text": "again", "scene_id": "s", "line_id": "2"},
    ]

    result = context_builder._condense_dialogue_batch(lines)

    assert len(result) == 1
    assert result[0]["text"] == "hello\nagain"
    assert result[0]["_condensed_line_ids"] == ["1", "2"]
    assert result[0]["_condensed_count"] == 2


def test_condense_dialogue_batch_keeps_alternating_speakers_separate() -> None:
    lines = [
        {"speaker": "A", "text": "hello", "scene_id": "s", "line_id": "1"},
        {"speaker": "B", "text": "again", "scene_id": "s", "line_id": "2"},
    ]

    result = context_builder._condense_dialogue_batch(lines)

    assert [item["line_id"] for item in result] == ["1", "2"]
    assert all("_condensed_count" not in item for item in result)


def test_condense_dialogue_batch_keeps_different_stability_separate() -> None:
    lines = [
        {"speaker": "A", "text": "stable", "scene_id": "s", "line_id": "1", "stability": "stable"},
        {"speaker": "A", "text": "observed", "scene_id": "s", "line_id": "2", "stability": "tentative"},
    ]

    result = context_builder._condense_dialogue_batch(lines)

    assert [item["line_id"] for item in result] == ["1", "2"]
    assert all("_condensed_count" not in item for item in result)


def test_condense_dialogue_batch_keeps_different_sources_separate() -> None:
    lines = [
        {"speaker": "A", "text": "stable", "scene_id": "s", "line_id": "1", "source": "stable"},
        {"speaker": "A", "text": "observed", "scene_id": "s", "line_id": "2", "source": "observed"},
    ]

    result = context_builder._condense_dialogue_batch(lines)

    assert [item["line_id"] for item in result] == ["1", "2"]
    assert all("_condensed_count" not in item for item in result)


def test_condense_dialogue_batch_keeps_different_routes_separate() -> None:
    lines = [
        {"speaker": "A", "text": "left", "scene_id": "s", "route_id": "left", "line_id": "1"},
        {"speaker": "A", "text": "right", "scene_id": "s", "route_id": "right", "line_id": "2"},
    ]

    result = context_builder._condense_dialogue_batch(lines)

    assert [item["line_id"] for item in result] == ["1", "2"]
    assert all("_condensed_count" not in item for item in result)


def test_condense_dialogue_batch_keeps_emotional_punctuation_separate() -> None:
    lines = [
        {"speaker": "A", "text": "hello!", "scene_id": "s", "line_id": "1"},
        {"speaker": "A", "text": "again", "scene_id": "s", "line_id": "2"},
    ]

    result = context_builder._condense_dialogue_batch(lines)

    assert [item["line_id"] for item in result] == ["1", "2"]


def test_condense_dialogue_batch_keeps_cjk_emotional_punctuation_separate() -> None:
    for punctuation in ["！", "？", "…"]:
        lines = [
            {"speaker": "A", "text": f"待って{punctuation}", "scene_id": "s", "line_id": "1"},
            {"speaker": "A", "text": "again", "scene_id": "s", "line_id": "2"},
        ]

        result = context_builder._condense_dialogue_batch(lines)

        assert [item["line_id"] for item in result] == ["1", "2"]


def test_condense_dialogue_batch_keeps_long_lines_separate() -> None:
    lines = [
        {"speaker": "A", "text": "x" * 31, "scene_id": "s", "line_id": "1"},
        {"speaker": "A", "text": "again", "scene_id": "s", "line_id": "2"},
    ]

    result = context_builder._condense_dialogue_batch(lines)

    assert [item["line_id"] for item in result] == ["1", "2"]


def test_compute_dynamic_line_limit_empty_list_returns_min() -> None:
    assert context_builder._compute_dynamic_line_limit([], 4, 16, 800) == 4


def test_compute_dynamic_line_limit_empty_text_returns_max() -> None:
    lines = [{"text": ""}, {"text": "   "}]

    assert context_builder._compute_dynamic_line_limit(lines, 4, 16, 800) == 16


def test_compute_dynamic_line_limit_dense_cjk_near_min_sparse_english_near_max() -> None:
    dense = [{"text": "漢" * 200} for _ in range(4)]
    sparse = [{"text": "ok"} for _ in range(4)]

    dense_limit = context_builder._compute_dynamic_line_limit(dense, 4, 16, 800)
    sparse_limit = context_builder._compute_dynamic_line_limit(sparse, 4, 16, 800)

    assert dense_limit == 4
    assert sparse_limit == 16


def test_compute_dynamic_line_limit_uses_recent_twenty_lines_only() -> None:
    old_dense_lines = [{"text": "日" * 1000} for _ in range(200)]
    recent_sparse_lines = [{"text": "ok"} for _ in range(20)]

    result = context_builder._compute_dynamic_line_limit(
        [*old_dense_lines, *recent_sparse_lines],
        4,
        16,
        800,
    )

    assert result == 16


def test_dynamic_line_sample_uses_timestamp_recency_across_sources() -> None:
    recent_sparse_stable = [
        {
            "text": "ok",
            "ts": f"2026-05-14T00:{index:02d}:00Z",
            "line_id": f"stable-{index}",
        }
        for index in range(20)
    ]
    old_dense_observed = [
        {
            "text": "dense" * 1000,
            "ts": f"2026-05-13T00:{index % 60:02d}:00Z",
            "line_id": f"observed-{index}",
        }
        for index in range(200)
    ]

    sample = context_builder._recency_ordered_context_lines(
        recent_sparse_stable,
        old_dense_observed,
    )
    result = context_builder._compute_dynamic_line_limit(sample, 4, 16, 800)

    assert [item["line_id"] for item in sample[-20:]] == [
        f"stable-{index}" for index in range(20)
    ]
    assert result == 16


def test_recency_ordered_lines_tags_stream_source_before_condensing() -> None:
    stable_lines = [
        {
            "speaker": "A",
            "text": "stable",
            "scene_id": "scene-a",
            "line_id": "stable-1",
            "stability": "stable",
            "ts": "2026-05-14T00:00:00Z",
        }
    ]
    observed_lines = [
        {
            "speaker": "A",
            "text": "observed",
            "scene_id": "scene-a",
            "line_id": "observed-1",
            "stability": "stable",
            "ts": "2026-05-14T00:00:01Z",
        }
    ]

    recent_lines = context_builder._recency_ordered_context_lines(
        stable_lines,
        observed_lines,
    )
    condensed = context_builder._condense_dialogue_batch(recent_lines)

    assert [item["source"] for item in recent_lines] == ["stable", "observed"]
    assert [item["line_id"] for item in condensed] == ["stable-1", "observed-1"]
    assert all("_condensed_count" not in item for item in condensed)


def test_context_window_bounds_preserves_zero_until_minimum_clamp() -> None:
    config = GalgameLLMConfig(
        context_explain_min_lines=0,
        context_explain_max_lines=0,
        context_window_target_tokens=0,
    )

    assert context_builder._context_window_bounds(config, max_floor=1) == (1, 1, 1)


def test_context_window_bounds_default_respects_small_configured_maximum() -> None:
    config = GalgameLLMConfig(
        context_explain_min_lines=2,
        context_explain_max_lines=3,
        context_window_target_tokens=64,
    )

    assert context_builder._context_window_bounds(config) == (2, 3, 64)


def test_summarize_context_respects_small_configured_maximum() -> None:
    lines = [
        {
            "speaker": "A",
            "text": f"line {index}.",
            "scene_id": "scene-a",
            "line_id": f"line-{index}",
            "stability": "stable",
        }
        for index in range(10)
    ]
    config = GalgameLLMConfig(
        context_explain_min_lines=1,
        context_explain_max_lines=3,
        context_window_target_tokens=800,
    )

    result = context_builder.build_summarize_context(
        {
            "latest_snapshot": {"scene_id": "scene-a"},
            "history_lines": lines,
            "history_observed_lines": [],
            "history_choices": [],
        },
        scene_id="scene-a",
        config=config,
    )

    assert [item["line_id"] for item in result["stable_lines"]] == ["line-7", "line-8", "line-9"]
