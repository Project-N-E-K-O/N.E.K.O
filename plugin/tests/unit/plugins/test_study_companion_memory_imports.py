from __future__ import annotations

from typing import Any

import pytest

from plugin.plugins.study_companion.memory_imports import (
    import_word_rows,
    normalize_csv_fieldnames,
)

pytestmark = pytest.mark.unit


def test_normalize_csv_fieldnames_handles_bom_case_and_empty() -> None:
    assert normalize_csv_fieldnames(None) is None
    assert normalize_csv_fieldnames(["\ufeff Word ", "Meaning"]) == ["word", "meaning"]


def test_import_word_rows_reports_imported_updated_skipped_and_preview() -> None:
    calls: list[dict[str, Any]] = []

    def add_word(**kwargs: Any) -> dict[str, Any]:
        calls.append(kwargs)
        created = kwargs["word"] != "known"
        return {"created": created, "item": {"word": kwargs["word"], "meaning": kwargs["meaning"]}}

    result = import_word_rows(
        add_word,
        deck_id="deck",
        line_offset=2,
        rows=[
            {"word": "new", "meaning": "fresh", "example_sentence": "ex", "pronunciation": "n", "tags": "t"},
            {"word": "", "meaning": "", "tags": ""},
            {"word": "bad", "meaning": ""},
            {"word": "known", "meaning": "old"},
        ],
    )

    assert result["imported_count"] == 1
    assert result["updated_count"] == 1
    assert result["skipped_rows"] == [{"line": 4, "reason": "word and meaning are required"}]
    assert result["preview"] == result["items"]
    assert calls[0]["deck_id"] == "deck"
    assert calls[0]["example_sentence"] == "ex"
