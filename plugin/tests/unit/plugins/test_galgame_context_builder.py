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


def test_build_suggest_context_includes_fixed_character_pov_reference() -> None:
    local_state = {
        "active_game_id": "game-a",
        "active_session_id": "session-a",
        "latest_snapshot": {
            "scene_id": "scene-a",
            "route_id": "route-a",
            "choices": [{"choice_id": "c1", "text": "Protect the promise", "index": 0}],
        },
        "history_lines": [{"scene_id": "scene-a", "line_id": "l1", "speaker": "A", "text": "line"}],
        "history_observed_lines": [],
        "history_choices": [],
        "character_mode": "fixed",
        "character_fixed_name": "Murasame",
        "character_profile_game_id": "senren_banka",
        "character_profile_match_reason": "window_title_contains",
        "character_profiles": {
            "Murasame": {
                "identity": "A guarded blade spirit",
                "relationships": {"Mas臣": "contract holder"},
                "background": ["sealed for centuries"],
                "character_voice": {
                    "core_traits": [
                        {
                            "trait": "proud but caring",
                            "speech_effect": "rejects concern before revealing it",
                        }
                    ],
                    "first_person_pronoun": "warawa",
                },
            }
        },
        "character_runtime_state": {
            "Murasame": {"current_emotion": "guarded hope"}
        },
    }

    context = context_builder.build_suggest_context(local_state)

    pov = context["fixed_character_pov"]
    assert pov["character_name"] == "Murasame"
    assert pov["profile_known"] is True
    assert pov["role"] == "bounded_strategy_reference"
    assert "strategy lens" in pov["strategy_instruction"]
    assert pov["identity"] == "A guarded blade spirit"
    assert pov["runtime_state"]["current_emotion"] == "guarded hope"


def test_build_summarize_context_marks_missing_fixed_profile_without_crashing() -> None:
    local_state = {
        "active_game_id": "game-a",
        "active_session_id": "session-a",
        "latest_snapshot": {"scene_id": "scene-a", "route_id": "route-a"},
        "history_lines": [{"scene_id": "scene-a", "line_id": "l1", "speaker": "A", "text": "line"}],
        "history_observed_lines": [],
        "history_choices": [],
        "character_mode": "fixed",
        "character_fixed_name": "Missing",
        "character_profiles": {},
    }

    context = context_builder.build_summarize_context(local_state, scene_id="scene-a")

    pov = context["fixed_character_pov"]
    assert pov["character_name"] == "Missing"
    assert pov["profile_known"] is False
    assert pov["applies_to"] == [
        "suggest_choice",
        "scene_summary",
        "cat_consultation",
        "push",
    ]


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


def test_build_fallback_summary_uses_template_and_key_dialogue() -> None:
    summary = context_builder.build_fallback_summary(
        "scene-a",
        [
            {"speaker": "A", "text": "ただいま", "line_id": "1"},
            {"speaker": "B", "text": "どうしてここに！？", "line_id": "2"},
            {"speaker": "C", "text": "secret", "line_id": "3", "route_id": "r"},
        ],
        [{"choice_id": "choice-a"}],
        {},
    )

    assert summary.startswith("场景 scene-a：A、B、C正在对话。")
    assert "已做出 1 次选择。" in summary
    assert "关键对白：" in summary
    assert "B：どうしてここに！？" in summary
    assert "；" not in summary


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


def test_recency_ordered_lines_interleaves_sources_with_same_timestamp() -> None:
    stable_lines = [
        {"line_id": f"stable-{index}", "text": f"stable {index}", "ts": "2026-05-14T00:00:00Z"}
        for index in range(3)
    ]
    observed_lines = [
        {
            "line_id": f"observed-{index}",
            "text": f"observed {index}",
            "ts": "2026-05-14T00:00:00Z",
        }
        for index in range(3)
    ]

    result = context_builder._recency_ordered_context_lines(stable_lines, observed_lines)

    assert [item["line_id"] for item in result] == [
        "stable-0",
        "observed-0",
        "stable-1",
        "observed-1",
        "stable-2",
        "observed-2",
    ]


def test_recency_ordered_lines_preserves_cross_stream_append_order_without_timestamps() -> None:
    stable_lines = [
        {"line_id": f"s{index}", "text": f"stable {index}"}
        for index in range(3, 6)
    ]
    observed_lines = [
        {"line_id": f"o{index}", "text": f"observed {index}"}
        for index in range(3, 6)
    ]

    result = context_builder._recency_ordered_context_lines(stable_lines, observed_lines)

    assert [item["line_id"] for item in result] == ["s3", "s4", "s5", "o3", "o4", "o5"]
    assert [item["line_id"] for item in result[-3:]] == ["o3", "o4", "o5"]


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


def test_context_window_bounds_min_floor_raises_minimum_and_maximum() -> None:
    config = GalgameLLMConfig(
        context_explain_min_lines=2,
        context_explain_max_lines=3,
        context_window_target_tokens=64,
    )

    assert context_builder._context_window_bounds(config, min_floor=16) == (16, 16, 64)


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


def test_summarize_context_applies_line_limit_across_all_dialogue_streams() -> None:
    stable_lines = [
        {
            "speaker": "A",
            "text": f"stable line {index}.",
            "scene_id": "scene-a",
            "line_id": f"stable-{index}",
            "ts": f"2026-05-14T00:00:0{index}Z",
        }
        for index in range(6)
    ]
    observed_lines = [
        {
            "speaker": "B",
            "text": f"observed line {index}.",
            "scene_id": "scene-a",
            "line_id": f"observed-{index}",
            "ts": f"2026-05-14T00:00:1{index}Z",
        }
        for index in range(6)
    ]
    config = GalgameLLMConfig(
        context_explain_min_lines=4,
        context_explain_max_lines=4,
        context_window_target_tokens=800,
    )

    result = context_builder.build_summarize_context(
        {
            "latest_snapshot": {"scene_id": "scene-a"},
            "history_lines": stable_lines,
            "history_observed_lines": observed_lines,
            "history_choices": [],
        },
        scene_id="scene-a",
        config=config,
    )

    assert len(result["recent_lines"]) == 4
    assert len(result["stable_lines"]) + len(result["observed_lines"]) == 4
    assert [item["line_id"] for item in result["stable_lines"]] == []
    assert [item["line_id"] for item in result["observed_lines"]] == [
        "observed-2",
        "observed-3",
        "observed-4",
        "observed-5",
    ]

def test_suggest_context_applies_line_limit_across_all_dialogue_streams() -> None:
    stable_lines = [
        {
            "speaker": "A",
            "text": f"stable line {index}.",
            "scene_id": "scene-a",
            "line_id": f"stable-{index}",
            "ts": f"2026-05-14T00:00:0{index}Z",
        }
        for index in range(6)
    ]
    observed_lines = [
        {
            "speaker": "B",
            "text": f"observed line {index}.",
            "scene_id": "scene-a",
            "line_id": f"observed-{index}",
            "ts": f"2026-05-14T00:00:1{index}Z",
        }
        for index in range(6)
    ]
    config = GalgameLLMConfig(
        context_explain_min_lines=4,
        context_explain_max_lines=4,
        context_window_target_tokens=800,
    )

    result = context_builder.build_suggest_context(
        {
            "latest_snapshot": {"scene_id": "scene-a"},
            "history_lines": stable_lines,
            "history_observed_lines": observed_lines,
            "history_choices": [],
        },
        config=config,
    )

    assert len(result["recent_lines"]) == 4
    assert len(result["stable_lines"]) + len(result["observed_lines"]) == 4


def test_explain_context_applies_line_limit_across_all_dialogue_streams() -> None:
    stable_lines = [
        {
            "speaker": "A",
            "text": f"stable line {index}.",
            "scene_id": "scene-a",
            "line_id": f"stable-{index}",
            "ts": f"2026-05-14T00:00:0{index}Z",
        }
        for index in range(6)
    ]
    observed_lines = [
        {
            "speaker": "B",
            "text": f"observed line {index}.",
            "scene_id": "scene-a",
            "line_id": f"observed-{index}",
            "ts": f"2026-05-14T00:00:1{index}Z",
        }
        for index in range(6)
    ]
    config = GalgameLLMConfig(
        context_explain_min_lines=4,
        context_explain_max_lines=4,
        context_window_target_tokens=800,
    )

    result = context_builder.build_explain_context(
        {
            "latest_snapshot": {
                "scene_id": "scene-a",
                "line_id": "observed-5",
                "speaker": "B",
                "text": "observed line 5.",
            },
            "history_lines": stable_lines,
            "history_observed_lines": observed_lines,
            "history_choices": [],
        },
        line_id="observed-5",
        config=config,
    )

    assert len(result["recent_lines"]) == 4
    assert len(result["stable_lines"]) + len(result["observed_lines"]) == 4


def test_explain_context_exposes_matching_restored_snapshot() -> None:
    result = context_builder.build_explain_context(
        {
            "active_game_id": "demo.alpha",
            "latest_snapshot": {
                "scene_id": "scene-a",
                "route_id": "route-a",
                "line_id": "line-1",
                "speaker": "A",
                "text": "current line.",
            },
            "history_lines": [
                {
                    "scene_id": "scene-a",
                    "route_id": "route-a",
                    "line_id": "line-1",
                    "speaker": "A",
                    "text": "current line.",
                }
            ],
            "history_observed_lines": [],
            "history_choices": [],
            "context_snapshot": {
                "game_id": "demo.alpha",
                "scene_id": "scene-a",
                "route_id": "route-a",
                "summary_seed": "restored summary",
                "stable_line_ids": ["line-0"],
            },
        },
        line_id="line-1",
    )

    assert result["restored_context_snapshot"]["summary_seed"] == "restored summary"
    assert result["restored_context_snapshot"]["stable_line_ids"] == ["line-0"]


def test_line_importance_score_rewards_plot_and_route_signals() -> None:
    filler = {"text": "ok", "line_id": "filler"}
    important = {
        "speaker": "A",
        "text": "但是我终于想起那个秘密约定了！",
        "route_id": "route-a",
        "line_id": "important",
    }

    assert context_builder._line_importance_score(important) > (
        context_builder._line_importance_score(filler) + 5
    )


def test_importance_compaction_keeps_high_score_older_line() -> None:
    lines = [
        {
            "speaker": "A",
            "text": "但是我终于想起那个秘密约定了！",
            "route_id": "route-a",
            "line_id": "old-important",
            "scene_id": "scene-a",
        },
        {"speaker": "B", "text": "ok", "line_id": "new-filler", "scene_id": "scene-a"},
    ]

    result = context_builder.build_summarize_context(
        {
            "latest_snapshot": {"scene_id": "scene-a"},
            "history_lines": lines,
            "history_observed_lines": [],
            "history_choices": [],
        },
        scene_id="scene-a",
        config=GalgameLLMConfig(
            context_explain_min_lines=1,
            context_explain_max_lines=1,
            context_line_importance_enabled=True,
        ),
    )

    assert [item["line_id"] for item in result["recent_lines"]] == ["old-important"]
    assert "_importance_score" not in result["recent_lines"][0]


def test_importance_compaction_preserves_target_line() -> None:
    lines = [
        {
            "speaker": "A",
            "text": "但是我终于想起那个秘密约定了！",
            "route_id": "route-a",
            "line_id": "old-important",
            "scene_id": "scene-a",
        }
    ]
    target_line = {
        "speaker": "B",
        "text": "ok",
        "line_id": "target",
        "scene_id": "scene-a",
    }

    _, _, recent = context_builder._global_scene_context_window(
        lines,
        [],
        "scene-a",
        line_limit=1,
        target_line=target_line,
        line_importance_enabled=True,
    )

    assert [item["line_id"] for item in recent] == ["target"]


def test_recency_window_preserves_target_line_when_importance_disabled() -> None:
    target_line = {
        "speaker": "A",
        "text": "target",
        "line_id": "target",
        "scene_id": "scene-a",
    }
    lines = [
        target_line,
        {"speaker": "B", "text": "newer", "line_id": "newer", "scene_id": "scene-a"},
    ]

    _, _, recent = context_builder._global_scene_context_window(
        lines,
        [],
        "scene-a",
        line_limit=1,
        target_line=target_line,
        line_importance_enabled=False,
    )

    assert [item["line_id"] for item in recent] == ["target"]


def test_disabled_importance_keeps_recency_behavior() -> None:
    lines = [
        {
            "speaker": "A",
            "text": "但是我终于想起那个秘密约定了！",
            "route_id": "route-a",
            "line_id": "old-important",
            "scene_id": "scene-a",
        },
        {"speaker": "B", "text": "ok", "line_id": "new-filler", "scene_id": "scene-a"},
    ]

    result = context_builder.build_summarize_context(
        {
            "latest_snapshot": {"scene_id": "scene-a"},
            "history_lines": lines,
            "history_observed_lines": [],
            "history_choices": [],
        },
        scene_id="scene-a",
        config=GalgameLLMConfig(context_explain_min_lines=1, context_explain_max_lines=1),
    )

    assert [item["line_id"] for item in result["recent_lines"]] == ["new-filler"]


def test_cumulative_light_summary_preserves_previous_summary() -> None:
    result = context_builder._cumulative_scene_summary(
        scene_id="scene-a",
        route_id="route-a",
        lines=[{"speaker": "A", "text": "新的台词。"}],
        selected_choices=[],
        snapshot={},
        previous_summary="之前两人约好放学后见面。",
        mode="cumulative_light",
    )

    assert "之前两人约好放学后见面" in result
    assert "新的台词" in result


def test_cumulative_llm_uses_refined_summary_after_trigger() -> None:
    result = context_builder._cumulative_scene_summary(
        scene_id="scene-a",
        route_id="",
        lines=[{"speaker": "A", "text": f"line {index}"} for index in range(3)],
        selected_choices=[],
        snapshot={},
        previous_summary="previous",
        mode="cumulative_llm",
        llm_refined_summary="refined summary",
        llm_trigger_lines=3,
    )

    assert result == "refined summary"


def test_cumulative_llm_trigger_uses_full_scene_history_not_window_size() -> None:
    result = context_builder.build_summarize_context(
        {
            "active_game_id": "game-a",
            "latest_snapshot": {"scene_id": "scene-a"},
            "history_lines": [
                {"scene_id": "scene-a", "speaker": "A", "text": "第一句。", "line_id": "1"},
                {"scene_id": "scene-a", "speaker": "A", "text": "第二句。", "line_id": "2"},
                {"scene_id": "scene-a", "speaker": "A", "text": "第三句。", "line_id": "3"},
            ],
            "history_observed_lines": [],
            "history_choices": [],
            "llm_refined_scene_summary": "LLM 精炼后的完整摘要。",
        },
        scene_id="scene-a",
        config=GalgameLLMConfig(
            context_scene_summary_mode="cumulative_llm",
            context_cumulative_llm_trigger_lines=3,
            context_explain_min_lines=1,
            context_explain_max_lines=1,
        ),
    )

    assert result["scene_summary_seed"] == "LLM 精炼后的完整摘要。"


def test_cumulative_summary_is_bounded_for_large_inputs() -> None:
    result = context_builder._cumulative_scene_summary(
        scene_id="scene-a",
        route_id="",
        lines=[{"speaker": "A", "text": "x" * 100} for _ in range(120)],
        selected_choices=[],
        snapshot={},
        previous_summary="p" * 3000,
        mode="cumulative_light",
    )

    assert len(result) <= 1600


def test_previous_summary_accepts_context_snapshot_only_for_same_game_and_route() -> None:
    state = {
        "active_game_id": "game-a",
        "context_snapshot": {
            "game_id": "game-a",
            "route_id": "route-a",
            "summary_seed": "同一游戏同一路线的摘要。",
        },
    }

    assert (
        context_builder._previous_summary_from_state(
            state,
            current_game_id="game-a",
            current_route_id="route-a",
        )
        == "同一游戏同一路线的摘要。"
    )
    assert (
        context_builder._previous_summary_from_state(
            state,
            current_game_id="game-b",
            current_route_id="route-a",
        )
        == ""
    )
    assert (
        context_builder._previous_summary_from_state(
            state,
            current_game_id="game-a",
            current_route_id="route-b",
        )
        == ""
    )


def test_previous_summary_accepts_context_snapshot_when_both_game_ids_missing() -> None:
    state = {
        "active_game_id": "",
        "context_snapshot": {
            "game_id": "",
            "route_id": "route-a",
            "summary_seed": "summary without game id",
        },
    }

    assert (
        context_builder._previous_summary_from_state(
            state,
            current_game_id="",
            current_route_id="route-a",
        )
        == "summary without game id"
    )
    assert (
        context_builder._previous_summary_from_state(
            state,
            current_game_id="game-a",
            current_route_id="route-a",
        )
        == ""
    )


def test_matching_context_snapshot_ignores_non_dict_values() -> None:
    assert (
        context_builder._matching_context_snapshot(
            {"context_snapshot": "broken"},
            scene_id="scene-a",
            route_id="route-a",
        )
        == {}
    )


def test_summarize_context_does_not_reuse_persisted_seed_across_game_or_route() -> None:
    config = GalgameLLMConfig(context_scene_summary_mode="cumulative_light")
    state = {
        "active_game_id": "game-b",
        "latest_snapshot": {"scene_id": "scene-b", "route_id": "route-b"},
        "history_lines": [
            {
                "scene_id": "scene-b",
                "route_id": "route-b",
                "speaker": "B",
                "text": "新的游戏路线台词。",
            }
        ],
        "history_observed_lines": [],
        "history_choices": [],
        "context_snapshot": {
            "game_id": "game-a",
            "route_id": "route-a",
            "summary_seed": "旧游戏旧路线摘要。",
        },
    }

    result = context_builder.build_summarize_context(
        state,
        scene_id="scene-b",
        config=config,
    )

    assert "新的游戏路线台词" in result["scene_summary_seed"]
    assert "旧游戏旧路线摘要" not in result["scene_summary_seed"]


def test_summarize_context_keeps_live_route_when_restored_scene_differs() -> None:
    result = context_builder.build_summarize_context(
        {
            "active_game_id": "demo.alpha",
            "latest_snapshot": {"scene_id": "scene-b", "route_id": "route-a"},
            "history_lines": [],
            "history_observed_lines": [],
            "history_choices": [],
            "context_snapshot": {
                "game_id": "demo.alpha",
                "scene_id": "scene-a",
                "route_id": "route-a",
                "summary_seed": "旧场景总结不应复用。",
            },
        },
        scene_id="",
    )

    assert result["scene_id"] == "scene-b"
    assert result["route_id"] == "route-a"
    assert "旧场景总结不应复用。" not in result["scene_summary_seed"]


def test_scene_context_hint_new_scene_no_history() -> None:
    result = context_builder.build_summarize_context(
        {
            "latest_snapshot": {"scene_id": "scene-b"},
            "previous_scene_id": "scene-a",
            "history_lines": [{"scene_id": "scene-a", "speaker": "A", "text": "old."}],
            "history_observed_lines": [],
            "history_choices": [],
        },
        scene_id="scene-b",
    )

    assert result["scene_context"] == "new_scene_no_history"


def test_scene_context_hint_cold_start_only_without_history() -> None:
    cold = context_builder.build_summarize_context(
        {"latest_snapshot": {"scene_id": "scene-a"}},
        scene_id="scene-a",
    )
    warm = context_builder.build_summarize_context(
        {
            "latest_snapshot": {"scene_id": "scene-a"},
            "history_lines": [{"scene_id": "scene-a", "speaker": "A", "text": "line."}],
        },
        scene_id="scene-a",
    )

    assert cold["scene_context"] == "cold_start"
    assert "scene_context" not in warm
