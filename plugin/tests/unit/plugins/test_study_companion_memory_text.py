from __future__ import annotations

import pytest

from plugin.plugins.study_companion.memory_text import (
    build_cloze_prompt,
    diff_recitation,
    normalize_tags,
    split_passage_text,
)

pytestmark = pytest.mark.unit


def test_normalize_tags_splits_dedupes_truncates_and_limits() -> None:
    raw = " Math，math;SCIENCE  " + "x" * 50 + " extra ignored"

    assert normalize_tags(raw, limit=3) == ["Math", "SCIENCE", "x" * 40]
    assert normalize_tags(["A", "a", "", None, "B"]) == ["A", "B"]
    assert normalize_tags({"not": "supported"}) == []


def test_split_passage_text_handles_empty_paragraphs_sentences_and_long_chunks() -> None:
    assert split_passage_text("  \n ") == []

    chunks = split_passage_text("First sentence. Second?\n\n" + "A" * 5101)

    assert chunks[0]["paragraph_index"] == 1
    assert chunks[0]["sentences"] == ["First sentence.", "Second?"]
    assert [chunk["chunk_index"] for chunk in chunks[1:]] == [1, 2]
    assert [len(chunk["text"]) for chunk in chunks[1:]] == [5000, 101]


def test_build_cloze_prompt_prefers_long_word_and_falls_back_to_character() -> None:
    assert build_cloze_prompt("") == {"prompt": "", "answer": "", "hint": ""}

    word = build_cloze_prompt("Remember important vocabulary.")
    cjk = build_cloze_prompt("你好世界")

    assert word["answer"] == "Remember"
    assert word["prompt"].startswith("____ important")
    assert cjk["answer"]
    assert "____" in cjk["prompt"]


def test_diff_recitation_counts_missing_extra_wrong_order_and_hints() -> None:
    diff = diff_recitation("alpha beta gamma", "beta alpha delta", hint_count=-1)
    hinted = diff_recitation("alpha beta", "alpha", hint_count=2)

    assert diff["missing_count"] > 0
    assert diff["extra_count"] > 0
    assert diff["wrong_order_count"] > 0
    assert diff["hint_count"] == 0
    assert hinted["hint_count"] == 2
    assert hinted["score"] < 1.0
