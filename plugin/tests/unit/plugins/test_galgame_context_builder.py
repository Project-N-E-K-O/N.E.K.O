from __future__ import annotations

from plugin.plugins.galgame_plugin import context_builder
from plugin.plugins.galgame_plugin.models import DATA_SOURCE_OCR_READER


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
