"""Synthetic tests for the maintainer-only candidate miner."""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from scripts import natural_expression_candidate_miner as miner


def _config(**overrides) -> miner.MiningConfig:
    values = {
        "threshold": 3,
        "word_ngram_min": 2,
        "word_ngram_max": 2,
        "cjk_ngram_min": 4,
        "cjk_ngram_max": 4,
        "min_length": 1,
        "exclude_covered": False,
    }
    values.update(overrides)
    return miner.MiningConfig(**values)


def _candidate(report, normalized_phrase: str):
    return next(
        candidate
        for candidate in report["candidates"]
        if candidate["normalized_phrase"] == normalized_phrase
    )


def test_assistant_only_counts_occurrences_and_distinct_messages(tmp_path: Path):
    input_path = tmp_path / "synthetic.jsonl"
    records = [
        {
            "role": "system",
            "content": "Soft silver rain Soft silver rain",
            "lang": "en",
        },
        {"role": "user", "content": "Soft silver rain Soft silver rain", "lang": "en"},
        {
            "role": "assistant",
            "content": "Soft silver rain. Soft silver rain.",
            "lang": "en",
        },
        {"role": "assistant", "content": "Soft silver rain.", "lang": "en"},
    ]
    input_path.write_text(
        "\n".join(json.dumps(record) for record in records) + "\n",
        encoding="utf-8",
    )

    messages, record_count = miner.read_jsonl(input_path)
    report = miner.build_report(
        messages,
        input_record_count=record_count,
        config=_config(word_ngram_min=3, word_ngram_max=3),
        rules_by_language={},
    )

    candidate = _candidate(report, "soft silver rain")
    assert candidate["occurrence_count"] == 3
    assert candidate["message_count"] == 2
    assert report["summary"]["assistant_message_count"] == 2
    assert report["summary"]["input_record_count"] == 4


def test_explicit_language_overrides_missing_or_unknown_record_language(tmp_path: Path):
    input_path = tmp_path / "override.jsonl"
    records = [
        {"role": "assistant", "content": "quiet lantern"},
        {"role": "assistant", "content": "quiet lantern", "lang": "unknown"},
        {"role": "assistant", "content": "quiet lantern", "lang": "ja"},
    ]
    input_path.write_text(
        "\n".join(json.dumps(record) for record in records) + "\n",
        encoding="utf-8",
    )

    messages, record_count = miner.read_jsonl(input_path, language_override="en-US")
    report = miner.build_report(
        messages,
        input_record_count=record_count,
        config=_config(),
        rules_by_language={},
    )

    assert [message.language for message in messages] == ["en", "en", "en"]
    assert _candidate(report, "quiet lantern")["occurrence_count"] == 3


@pytest.mark.parametrize(
    ("language", "phrase", "normalized"),
    [
        ("en", "Gentle Moonlight", "gentle moonlight"),
        ("es", "brisa cálida", "brisa cálida"),
        ("pt-BR", "Coração tranquilo", "coração tranquilo"),
        ("ru", "Тихий свет", "тихий свет"),
    ],
)
def test_unicode_word_ngrams_by_language(language, phrase, normalized):
    messages = [
        miner.SourceMessage(
            language=miner.normalize_language(language),
            content=phrase,
            source_line=index,
        )
        for index in range(1, 4)
    ]

    report = miner.build_report(
        messages,
        input_record_count=3,
        config=_config(),
        rules_by_language={},
    )

    assert _candidate(report, normalized)["occurrence_count"] == 3


@pytest.mark.parametrize(
    ("language", "phrase"),
    [
        ("zh-CN", "嘴角微微上扬"),
        ("zh-TW", "嘴角微微上揚"),
        ("ja", "静かな月明かり"),
    ],
)
def test_cjk_character_ngrams_split_at_punctuation(language, phrase):
    size = len(phrase)
    config = _config(cjk_ngram_min=size, cjk_ngram_max=size)
    messages = [
        miner.SourceMessage(language, f"{phrase}。{phrase}！", 1),
        miner.SourceMessage(language, phrase, 2),
    ]

    report = miner.build_report(
        messages,
        input_record_count=2,
        config=config,
        rules_by_language={},
    )

    candidate = _candidate(report, phrase)
    assert candidate["occurrence_count"] == 3
    assert candidate["message_count"] == 2
    assert all(
        "。" not in item["phrase"] and "！" not in item["phrase"]
        for item in report["candidates"]
    )


def test_korean_uses_word_and_hangul_character_strategies():
    messages = [
        miner.SourceMessage("ko", "조용한 달빛. 두근두근.", index)
        for index in range(1, 4)
    ]

    report = miner.build_report(
        messages,
        input_record_count=3,
        config=_config(),
        rules_by_language={},
    )

    assert _candidate(report, "조용한 달빛")["occurrence_count"] == 3
    assert _candidate(report, "두근두근")["occurrence_count"] == 3


def test_code_urls_and_template_noise_are_protected():
    text = (
        "`hidden phrase` https://example.test/hidden-phrase\n"
        "```text\nhidden phrase\n```\n"
        "{{hidden phrase}} <HIDDEN_PHRASE>\n"
        "visible phrase"
    )
    messages = [miner.SourceMessage("en", text, index) for index in range(1, 4)]

    report = miner.build_report(
        messages,
        input_record_count=3,
        config=_config(),
        rules_by_language={},
    )

    normalized = {candidate["normalized_phrase"] for candidate in report["candidates"]}
    assert "visible phrase" in normalized
    assert "hidden phrase" not in normalized


def test_threshold_filters_below_minimum_occurrence_count():
    messages = [
        miner.SourceMessage("en", "quiet lantern", 1),
        miner.SourceMessage("en", "quiet lantern", 2),
    ]

    report = miner.build_report(
        messages,
        input_record_count=2,
        config=_config(threshold=3),
        rules_by_language={},
    )

    assert report["candidates"] == []


def test_current_rule_coverage_is_read_only_and_can_be_excluded():
    messages = [
        miner.SourceMessage("en", "She smiled warmly", index) for index in range(1, 4)
    ]
    rules = {
        "en": [
            {
                "id": "EN_004",
                "find": r"\b(he|she|they|I|you)\s+smiled\s+(?:warmly|softly)\b",
                "flags": re.IGNORECASE,
            }
        ]
    }

    report = miner.build_report(
        messages,
        input_record_count=3,
        config=_config(word_ngram_min=3, word_ngram_max=3),
        rules_by_language=rules,
    )
    assert _candidate(report, "she smiled warmly")["covered_by_rule_ids"] == ["EN_004"]

    excluded = miner.build_report(
        messages,
        input_record_count=3,
        config=_config(
            word_ngram_min=3,
            word_ngram_max=3,
            exclude_covered=True,
        ),
        rules_by_language=rules,
    )
    assert excluded["candidates"] == []


def test_coverage_reads_the_real_curated_rule_table():
    messages = [
        miner.SourceMessage("en", "She smiled warmly", index) for index in range(1, 4)
    ]

    report = miner.build_report(
        messages,
        input_record_count=3,
        config=_config(word_ngram_min=3, word_ngram_max=3),
    )

    assert _candidate(report, "she smiled warmly")["covered_by_rule_ids"] == ["EN_004"]


def test_output_schema_is_pending_and_not_a_runtime_rule_schema():
    messages = [
        miner.SourceMessage("en", "quiet lantern", index) for index in range(1, 4)
    ]

    report = miner.build_report(
        messages,
        input_record_count=3,
        config=_config(),
        rules_by_language={},
    )
    candidate = _candidate(report, "quiet lantern")

    assert report["schema_version"] == "natural-expression-candidates/v1"
    assert report["artifact_type"] == "maintainer_review_candidates"
    assert candidate["status"] == "pending"
    assert set(candidate) == {
        "covered_by_rule_ids",
        "language",
        "message_count",
        "normalized_phrase",
        "occurrence_count",
        "phrase",
        "status",
    }
    assert "find" not in candidate and "replace" not in candidate
    assert "context" not in candidate and "conversation_id" not in candidate


def test_serialized_output_is_byte_deterministic(tmp_path: Path):
    messages = [
        miner.SourceMessage("en", "quiet lantern", index) for index in range(1, 4)
    ]
    report = miner.build_report(
        messages,
        input_record_count=3,
        config=_config(),
        rules_by_language={},
    )
    first = tmp_path / "first.json"
    second = tmp_path / "second.json"

    miner.write_report(first, report)
    miner.write_report(second, report)

    assert first.read_bytes() == second.read_bytes()


@pytest.mark.parametrize(
    ("line", "error_fragment"),
    [
        ("not-json\n", "invalid JSON"),
        (json.dumps(["not", "an", "object"]) + "\n", "must be an object"),
        (
            json.dumps({"role": "assistant", "content": ["not text"], "lang": "en"})
            + "\n",
            "content must be a string",
        ),
        (json.dumps({"role": "assistant", "content": "hello"}) + "\n", "require lang"),
    ],
)
def test_bad_input_reports_line_without_echoing_content(
    tmp_path: Path, line, error_fragment
):
    input_path = tmp_path / "bad.jsonl"
    input_path.write_text(line, encoding="utf-8")

    with pytest.raises(miner.CandidateMinerError, match=error_fragment) as exc_info:
        miner.read_jsonl(input_path)

    assert "line 1" in str(exc_info.value)
    assert "hello" not in str(exc_info.value)


def test_cli_default_stdout_does_not_print_candidate_text(tmp_path: Path, capsys):
    input_path = tmp_path / "input.jsonl"
    output_path = tmp_path / "review.json"
    records = [
        {"role": "assistant", "content": "private synthetic phrase", "lang": "en"}
        for _ in range(3)
    ]
    input_path.write_text(
        "\n".join(json.dumps(record) for record in records) + "\n",
        encoding="utf-8",
    )

    return_code = miner.main(
        [
            "--input",
            str(input_path),
            "--output",
            str(output_path),
            "--word-ngram-min",
            "3",
            "--word-ngram-max",
            "3",
        ]
    )
    captured = capsys.readouterr()

    assert return_code == 0
    assert "private synthetic phrase" not in captured.out
    assert "private synthetic phrase" in output_path.read_text(encoding="utf-8")
