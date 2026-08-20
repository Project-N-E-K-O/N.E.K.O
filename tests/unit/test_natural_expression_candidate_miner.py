"""Synthetic tests for the maintainer-only candidate miner."""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

from scripts import natural_expression_candidate_miner as miner
from utils import natural_expression_candidates as candidate_core


def test_compatibility_script_runs_directly_without_pythonpath(tmp_path: Path):
    script = (
        Path(__file__).parents[2] / "scripts" / "natural_expression_candidate_miner.py"
    )
    environment = os.environ.copy()
    environment.pop("PYTHONPATH", None)

    result = subprocess.run(
        [sys.executable, str(script), "--help"],
        cwd=tmp_path,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert "usage: natural_expression_candidate_miner.py" in result.stdout
    assert "--input INPUT" in result.stdout


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


def test_word_tokenization_normalizes_decomposed_accents_before_counting():
    messages = [
        miner.SourceMessage("pt", "café tranquilo", 1),
        miner.SourceMessage("pt", "cafe\u0301 tranquilo", 2),
        miner.SourceMessage("pt", "cafe\u0301 tranquilo", 3),
    ]

    report = miner.build_report(
        messages,
        input_record_count=3,
        config=_config(),
        rules_by_language={},
    )

    assert _candidate(report, "café tranquilo")["occurrence_count"] == 3


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


def test_japanese_iteration_marks_remain_in_script_runs():
    phrase = "時々微笑む"
    messages = [miner.SourceMessage("ja", phrase, index) for index in range(1, 4)]

    report = miner.build_report(
        messages,
        input_record_count=3,
        config=_config(cjk_ngram_min=len(phrase), cjk_ngram_max=len(phrase)),
        rules_by_language={},
    )

    assert _candidate(report, phrase)["occurrence_count"] == 3


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


def test_korean_single_token_is_not_double_counted_across_strategies():
    messages = [miner.SourceMessage("ko", "두근두근", index) for index in range(1, 3)]
    config = _config(
        threshold=1,
        word_ngram_min=1,
        word_ngram_max=1,
    )

    report = miner.build_report(
        messages,
        input_record_count=2,
        config=config,
        rules_by_language={},
    )

    candidate = _candidate(report, "두근두근")
    assert candidate["occurrence_count"] == 2
    assert candidate["message_count"] == 2

    below_threshold = miner.build_report(
        messages,
        input_record_count=2,
        config=_config(
            threshold=3,
            word_ngram_min=1,
            word_ngram_max=1,
        ),
        rules_by_language={},
    )
    assert below_threshold["candidates"] == []


def test_korean_word_candidates_stop_at_the_occurrence_cap(monkeypatch):
    generated = 0
    original_word_candidates = candidate_core._word_candidates

    def counted_word_candidates(*args, **kwargs):
        nonlocal generated
        for candidate in original_word_candidates(*args, **kwargs):
            generated += 1
            yield candidate

    monkeypatch.setattr(candidate_core, "_word_candidates", counted_word_candidates)
    message = candidate_core.SourceMessage(
        "ko",
        " ".join(["조용한"] * 20),
        1,
    )

    with pytest.raises(
        candidate_core.CandidateMinerError,
        match="assistant history exceeds local analysis limit",
    ):
        candidate_core.build_report(
            [message],
            input_record_count=1,
            config=_config(),
            rules_by_language={},
            max_occurrences=3,
        )

    assert generated == 4


def test_code_urls_and_template_noise_are_protected():
    text = (
        "`hidden phrase` https://example.test/hidden-phrase\n"
        "intranet.example/private-path\n"
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
    assert all("intranet" not in phrase for phrase in normalized)
    assert all("example" not in phrase for phrase in normalized)


def test_indented_markdown_code_is_protected():
    text = "visible phrase\n\n    secret_key = value"
    messages = [miner.SourceMessage("en", text, index) for index in range(1, 4)]

    report = miner.build_report(
        messages,
        input_record_count=3,
        config=_config(),
        rules_by_language={},
    )

    normalized = {candidate["normalized_phrase"] for candidate in report["candidates"]}
    assert "visible phrase" in normalized
    assert all("secret_key" not in phrase for phrase in normalized)


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


def test_partially_covered_candidate_is_annotated_but_not_excluded():
    messages = [
        miner.SourceMessage("en", "smiled warmly", index) for index in range(1, 4)
    ]
    messages.append(miner.SourceMessage("en", "She smiled warmly", 4))
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
        input_record_count=4,
        config=_config(exclude_covered=True),
        rules_by_language=rules,
    )

    candidate = _candidate(report, "smiled warmly")
    assert candidate["covered_by_rule_ids"] == ["EN_004"]
    assert candidate["occurrence_count"] == 4


def test_coverage_work_is_cached_across_candidates(monkeypatch):
    text = "quiet lantern silver morning"
    messages = [miner.SourceMessage("en", text, index) for index in range(1, 4)]
    rules = {"en": [{"id": "EN_CACHE", "find": r"\bquiet lantern\b"}]}
    original_compile = candidate_core.re.compile
    original_protected_spans = candidate_core._runtime_protected_spans
    calls = {"compile": 0, "finditer": 0, "protected": 0}

    class CountingPattern:
        def __init__(self, pattern):
            self.pattern = pattern

        def finditer(self, text):
            calls["finditer"] += 1
            return self.pattern.finditer(text)

    def counting_compile(pattern, flags=0):
        calls["compile"] += 1
        return CountingPattern(original_compile(pattern, flags))

    def counting_protected_spans(text):
        calls["protected"] += 1
        return original_protected_spans(text)

    monkeypatch.setattr(candidate_core.re, "compile", counting_compile)
    monkeypatch.setattr(
        candidate_core, "_runtime_protected_spans", counting_protected_spans
    )

    report = miner.build_report(
        messages,
        input_record_count=3,
        config=_config(word_ngram_min=2, word_ngram_max=2),
        rules_by_language=rules,
    )

    assert len(report["candidates"]) == 3
    # Candidate extraction scans each source message; coverage adds one shared scan.
    assert calls == {"compile": 1, "finditer": 1, "protected": 4}


def test_word_coverage_uses_original_sentence_delimiters():
    text = "Он смотрел. Словно само время замерло"
    messages = [miner.SourceMessage("ru", text, index) for index in range(1, 4)]
    rules = {
        "ru": [
            {
                "id": "RU_011",
                "find": (
                    r"(^|[.!?…]\s)(?:Словно|Будто)\s+(?:само\s+)?"
                    r"время\s+(?:замерло|остановилось|застыло)\b"
                ),
            }
        ]
    }

    report = miner.build_report(
        messages,
        input_record_count=3,
        config=_config(
            word_ngram_min=4,
            word_ngram_max=4,
            exclude_covered=True,
        ),
        rules_by_language=rules,
    )

    assert report["candidates"] == []


def test_cjk_coverage_uses_original_punctuation_context():
    text = "张了张嘴，欲言又止"
    messages = [miner.SourceMessage("zh-CN", text, index) for index in range(1, 4)]

    report = miner.build_report(
        messages,
        input_record_count=3,
        config=_config(),
    )

    assert _candidate(report, "张了张嘴")["covered_by_rule_ids"] == ["ZH_026"]
    assert _candidate(report, "欲言又止")["covered_by_rule_ids"] == ["ZH_026"]

    excluded = miner.build_report(
        messages,
        input_record_count=3,
        config=_config(exclude_covered=True),
    )
    assert excluded["candidates"] == []


def test_coverage_does_not_normalize_original_runtime_text():
    text = "aguanto\u0301 el aliento"
    messages = [miner.SourceMessage("es", text, index) for index in range(1, 4)]
    rules = {
        "es": [
            {
                "id": "ES_002",
                "find": r"\b(?:contuvo|aguant[oó]) el aliento\b",
            }
        ]
    }

    report = miner.build_report(
        messages,
        input_record_count=3,
        config=_config(
            word_ngram_min=3,
            word_ngram_max=3,
            exclude_covered=True,
        ),
        rules_by_language=rules,
    )

    assert _candidate(report, "aguantó el aliento")["covered_by_rule_ids"] == []


def test_coverage_preserves_protected_suffixes_in_original_text():
    text = "A beat of silence passed `token`"
    messages = [miner.SourceMessage("en", text, index) for index in range(1, 4)]
    rules = {
        "en": [
            {
                "id": "EN_023",
                "find": (
                    r"\b(?:[Aa]\s+)?(?:beat|moment)\s+of\s+silence\s+"
                    r"(?:passed|hung|stretched|fell|followed|settled)"
                    r"(?=\s*[.,;:!?]|\s*$)"
                ),
            }
        ]
    }

    report = miner.build_report(
        messages,
        input_record_count=3,
        config=_config(
            word_ngram_min=5,
            word_ngram_max=5,
            exclude_covered=True,
        ),
        rules_by_language=rules,
    )

    assert _candidate(report, "a beat of silence passed")["covered_by_rule_ids"] == []


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


def test_traditional_chinese_is_not_covered_by_simplified_runtime_rules():
    phrase = "嘴角微微上揚"
    messages = [miner.SourceMessage("zh-TW", phrase, index) for index in range(1, 4)]
    rules = {"zh": [{"id": "ZH_TEST", "find": phrase}]}

    report = miner.build_report(
        messages,
        input_record_count=3,
        config=_config(
            cjk_ngram_min=len(phrase),
            cjk_ngram_max=len(phrase),
            exclude_covered=True,
        ),
        rules_by_language=rules,
    )

    assert _candidate(report, phrase)["covered_by_rule_ids"] == []


def test_cjk_coverage_checks_the_complete_matched_collocation():
    phrase = "嘴角微微勾起一抹笑意"
    messages = [miner.SourceMessage("zh-CN", phrase, index) for index in range(1, 4)]

    report = miner.build_report(
        messages,
        input_record_count=3,
        config=_config(),
    )
    assert _candidate(report, "嘴角微微")["covered_by_rule_ids"] == ["ZH_002"]

    excluded = miner.build_report(
        messages,
        input_record_count=3,
        config=_config(exclude_covered=True),
    )
    assert excluded["candidates"] == []


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


def test_user_review_requires_three_distinct_assistant_messages():
    one_message = [
        candidate_core.SourceMessage(
            "en",
            "quiet lantern. quiet lantern. quiet lantern",
            1,
        )
    ]

    one_message_report = candidate_core.build_user_review_report(
        one_message,
        rules_by_language={},
    )
    assert one_message_report["candidates"] == []

    three_messages = [
        candidate_core.SourceMessage("en", "quiet lantern", source_line)
        for source_line in range(1, 4)
    ]
    report = candidate_core.build_user_review_report(
        three_messages,
        rules_by_language={},
    )
    candidate = _candidate(report, "quiet lantern")

    assert report["artifact_type"] == "user_review_candidates"
    assert report["parameters"]["message_count_threshold"] == 3
    assert report["summary"] == {
        "assistant_message_count": 3,
        "candidate_count": 1,
        "returned_candidate_count": 1,
        "candidates_truncated": False,
    }
    assert candidate["occurrence_count"] == 3
    assert candidate["message_count"] == 3
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


def test_maintainer_report_keeps_occurrence_only_compatibility():
    messages = [
        candidate_core.SourceMessage(
            "en",
            "quiet lantern. quiet lantern. quiet lantern",
            1,
        )
    ]

    report = candidate_core.build_report(
        messages,
        input_record_count=1,
        config=_config(),
        rules_by_language={},
    )

    assert _candidate(report, "quiet lantern")["message_count"] == 1


def test_report_can_bound_retained_occurrences():
    messages = [candidate_core.SourceMessage("zh-CN", "安静灯笼安静灯笼", 1)]

    with pytest.raises(
        candidate_core.CandidateMinerError,
        match="assistant history exceeds local analysis limit",
    ):
        candidate_core.build_report(
            messages,
            input_record_count=1,
            config=_config(),
            rules_by_language={},
            max_occurrences=2,
        )


def test_user_review_rejects_oversized_history_before_mining(monkeypatch):
    monkeypatch.setattr(candidate_core, "USER_REVIEW_MAX_INPUT_CHARACTERS", 8)
    messages = [candidate_core.SourceMessage("en", "quiet lantern", 1)]

    with pytest.raises(
        candidate_core.CandidateMinerError,
        match="assistant history exceeds local analysis limit",
    ):
        candidate_core.build_user_review_report(messages, rules_by_language={})


def test_user_review_caps_candidates_before_returning_them_to_the_browser(monkeypatch):
    candidates = [
        {
            "normalized_phrase": f"candidate {index}",
            "status": "pending",
        }
        for index in range(3)
    ]
    monkeypatch.setattr(candidate_core, "USER_REVIEW_MAX_CANDIDATES", 2)
    monkeypatch.setattr(
        candidate_core,
        "build_report",
        lambda *args, **kwargs: {
            "candidates": candidates,
            "parameters": {},
        },
    )

    report = candidate_core.build_user_review_report([], rules_by_language={})

    assert report["candidates"] == candidates[:2]
    assert report["summary"] == {
        "assistant_message_count": 0,
        "candidate_count": 3,
        "returned_candidate_count": 2,
        "candidates_truncated": True,
    }
    assert report["parameters"]["candidate_output_limit"] == 2


def test_user_review_rejects_invalid_distinct_message_threshold():
    with pytest.raises(
        candidate_core.CandidateMinerError,
        match="message_count_threshold must be at least 1",
    ):
        candidate_core.build_user_review_report([], message_count_threshold=0)
