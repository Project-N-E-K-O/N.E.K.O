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
        "analyzed_message_count": 3,
        "analyzed_source_lines": [1, 2, 3],
        "messages_truncated": False,
        "content_truncated": False,
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


def test_user_review_narrows_window_when_character_budget_is_exceeded(monkeypatch):
    """An oversized history narrows the window instead of failing the request.

    It narrows to the DISTINCT-message threshold and no further: below
    that, every candidate is removed for want of occurrences and the panel
    reports "not enough history" about a window that had it. This used to
    assert two, which was the bug written down.
    """
    monkeypatch.setattr(candidate_core, "USER_REVIEW_MAX_INPUT_CHARACTERS", 30)
    messages = [
        candidate_core.SourceMessage("en", "quiet lantern", source_line)
        for source_line in range(1, 5)
    ]

    report = candidate_core.build_user_review_report(messages, rules_by_language={})

    summary = report["summary"]
    assert summary["assistant_message_count"] == 4
    assert summary["analyzed_message_count"] == 3
    assert summary["messages_truncated"] is True


def test_user_review_narrows_window_when_occurrence_budget_is_exceeded(monkeypatch):
    """The n-gram budget bites long before the character budget does.

    100 replies of ~280 unbroken Han characters blow through
    ``USER_REVIEW_MAX_OCCURRENCES`` at roughly 21% of
    ``USER_REVIEW_MAX_INPUT_CHARACTERS``.  Raising there produced a 422 the
    browser could only render as "please try again", which never succeeded.
    """
    monkeypatch.setattr(candidate_core, "USER_REVIEW_MAX_OCCURRENCES", 400)
    unbroken = "今天天气真好我们一起去散步你觉得怎么样我觉得非常开心因为可以和你聊天"
    messages = [
        candidate_core.SourceMessage("zh-CN", unbroken, source_line)
        for source_line in range(1, 17)
    ]

    report = candidate_core.build_user_review_report(messages, rules_by_language={})

    summary = report["summary"]
    assert summary["assistant_message_count"] == 16
    assert summary["messages_truncated"] is True
    assert 0 < summary["analyzed_message_count"] < 16


def test_user_review_truncates_a_single_over_budget_reply(monkeypatch):
    """One long reply must narrow, not dead-end the panel.

    Halving the window floors at one message. Rethrowing there turned every
    selection containing that reply into a 422 the panel can only render as
    "please try again", and retrying never helps -- the same reply is still
    the newest one. The body is halved instead.
    """
    # ~5 occurrences per CJK character, so this budget fits about 4k
    # characters -- comfortably above the floor, which is what makes this
    # exercise the halving rather than the give-up branch next door.
    budget = 20_000
    monkeypatch.setattr(candidate_core, "USER_REVIEW_MAX_OCCURRENCES", budget)
    body = "今天天气真好啊我们一起去散步吧" * 800
    assert budget // 5 > candidate_core._USER_REVIEW_MIN_TRUNCATED_CHARACTERS, (
        "the budget has to leave room above the floor, or this would pin "
        "the give-up branch next door instead"
    )
    assert len(body) > budget // 5, "the reply has to start over budget"

    report = candidate_core.build_user_review_report(
        [candidate_core.SourceMessage("zh-CN", body, 1)],
        message_count_threshold=1,
        rules_by_language={},
    )

    summary = report["summary"]
    assert summary["content_truncated"] is True
    assert summary["analyzed_message_count"] == 1
    # Reported separately from a dropped window: nothing was dropped here.
    assert summary["messages_truncated"] is False


def test_a_chinese_reply_inside_the_advertised_limit_is_analysable():
    """The character limit the panel advertises has to be reachable.

    Mining generates a fixed number of n-grams per character -- about five
    for CJK -- so the occurrence budget bites long before the character one:
    measured, an uninterrupted Chinese reply stopped fitting at roughly 20k
    characters, a sixth of the 128 KiB advertised. This asserts only that a
    reply well inside the advertised limit returns a report rather than
    raising, so it stays honest if either budget is retuned.
    """
    body = ("今天天气真好啊我们一起去散步吧" * 1500)[:21_000]
    assert len(body) < candidate_core.USER_REVIEW_MAX_INPUT_CHARACTERS, (
        "the point of this test is a reply INSIDE the advertised limit"
    )

    report = candidate_core.build_user_review_report(
        [candidate_core.SourceMessage("zh-CN", body, 1)],
        message_count_threshold=1,
        rules_by_language={},
    )

    assert report["summary"]["assistant_message_count"] == 1
    assert isinstance(report["candidates"], list)


def test_user_review_still_reports_a_single_unanalyzable_message(monkeypatch):
    """The narrowing loop floors rather than looping forever.

    A single over-budget reply now has its body halved instead of raising,
    so what is left here is the floor: below
    ``_USER_REVIEW_MIN_TRUNCATED_CHARACTERS`` no amount of halving fits, and
    that means the budget was configured too small to analyse anything.
    """
    monkeypatch.setattr(candidate_core, "USER_REVIEW_MAX_OCCURRENCES", 1)
    messages = [
        candidate_core.SourceMessage("en", "quiet lantern glows", 1),
    ]

    with pytest.raises(candidate_core.CandidateBudgetExceededError):
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
        "analyzed_message_count": 0,
        "analyzed_source_lines": [],
        "messages_truncated": False,
        "content_truncated": False,
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


def test_narrowed_window_can_fall_below_the_distinct_message_threshold(monkeypatch):
    """A narrowed window can still make the 3-message threshold unsatisfiable.

    The browser bases its minimum-sample check on `analyzed_message_count` for
    exactly this case: with fewer analyzed replies than the threshold no
    candidate can qualify, so "no candidates found" would misreport an
    impossible evaluation as a genuine absence.

    Narrowing now tries to hold the sample at the threshold first, shortening
    bodies instead of dropping replies. This case is what remains once that is
    exhausted -- every message already at the minimum useful length and the
    window still over budget -- and the floor yields rather than returning a
    422 the panel can only render as "try again".
    """
    # One such reply mines to 145 occurrences, so a 200 budget admits
    # exactly one message and the window narrows all the way down.
    monkeypatch.setattr(candidate_core, "USER_REVIEW_MAX_OCCURRENCES", 200)
    unbroken = "今天天气真好我们一起去散步你觉得怎么样我觉得非常开心因为可以和你聊天"
    messages = [
        candidate_core.SourceMessage("zh-CN", unbroken, source_line)
        for source_line in range(1, 33)
    ]

    summary = candidate_core.build_user_review_report(
        messages, rules_by_language={}
    )["summary"]

    assert summary["assistant_message_count"] == 32
    assert summary["messages_truncated"] is True
    assert summary["analyzed_message_count"] < 3
    assert summary["candidate_count"] == 0


def _blockquoted(*lines: str) -> str:
    return "\n".join("> " + line for line in lines)




def test_nested_and_unclosed_blockquoted_fences_stay_protected():
    nested = "\n".join(
        [
            ">> ```python",
            ">> secret_token_helper = compute_secret_token_helper()",
            ">> ```",
        ]
    )
    unclosed = _blockquoted(
        "```python",
        "secret_token_helper = compute_secret_token_helper()",
    )

    for text in (nested, unclosed):
        messages = [
            candidate_core.SourceMessage("en", text, source_line)
            for source_line in range(1, 4)
        ]
        report = candidate_core.build_report(
            messages,
            input_record_count=3,
            config=candidate_core.MiningConfig(),
            rules_by_language={},
        )
        assert report["candidates"] == [], text


def test_ordinary_quoted_prose_is_still_mined():
    """Blockquote handling must not over-protect: quoted prose is normal text."""
    text = _blockquoted(
        "I really think we should go outside today",
        "I really think we should go outside today",
    )
    messages = [
        candidate_core.SourceMessage("en", text, source_line)
        for source_line in range(1, 4)
    ]

    report = candidate_core.build_report(
        messages,
        input_record_count=3,
        config=candidate_core.MiningConfig(),
        rules_by_language={},
    )
    assert any(
        candidate["phrase"] == "go outside today" for candidate in report["candidates"]
    )




def _mined(text: str) -> list[str]:
    messages = [
        candidate_core.SourceMessage("en", text, source_line)
        for source_line in range(1, 4)
    ]
    report = candidate_core.build_report(
        messages,
        input_record_count=3,
        config=candidate_core.MiningConfig(),
        rules_by_language={},
    )
    return [candidate["phrase"] for candidate in report["candidates"]]




def test_html_code_elements_protect_their_contents():
    """`<pre>` / `<code>` mark code as explicitly as a Markdown fence does.

    The generic `<...>` template pattern protected only the TAGS, leaving the
    code between them mineable and exportable.
    """
    for text in (
        "she said <code>secret token helper</code> again and again",
        "<pre><code>secret_token_helper = compute()</code></pre>",
        "she said <CODE>secret token helper</CODE> again and again",
    ):
        assert not [
            phrase for phrase in _mined(text) if "secret" in phrase or "helper" in phrase
        ], text


def test_unclosed_html_code_container_protects_to_end_of_text():
    """A reply truncated mid-code-block must not leak its body.

    Requiring a closing tag left the body of an unmatched container exposed all
    the way into persistence: `build_repeat_signature` returned the code
    identifier verbatim, which `anti_repeat_effects.json` would then store —
    against that module's stated no-code boundary. Unclosed fences already
    protect to end-of-text; the HTML containers now match that.
    """
    from memory.anti_repeat_effects import build_repeat_signature

    for text in (
        "she said <code>secret_token_helper = compute()",
        "she said <pre>secret_token_helper = compute()",
        "<code>aaa</code> middle text <code>secret_token_helper",
    ):
        assert not [
            phrase for phrase in _mined(text) if "secret" in phrase or "helper" in phrase
        ], text
        assert (
            build_repeat_signature(
                text, ["secret_token_helper"], language="en"
            )
            is None
        ), text


def test_html_code_protection_does_not_swallow_prose():
    for text in (
        "we always say the exact same thing",
        "if a < b and b > c we always say the exact same thing",
        "we always decode the exact same thing and encode it",
    ):
        assert any("always" in phrase for phrase in _mined(text)), text


def test_script_and_style_bodies_are_protected():
    """`<script>` / `<style>` are raw-text elements; their bodies are code."""
    for text in (
        "<script>secret_token_helper = compute()</script>",
        "<style>.secret_token_helper{color:red}</style>",
        "<script>secret_token_helper = compute()",
    ):
        assert not [
            phrase for phrase in _mined(text) if "secret" in phrase or "helper" in phrase
        ], text




def test_a_single_oversized_reply_is_truncated_to_the_character_budget():
    """Dropping whole messages floors at one, so the last one must be trimmed.

    Otherwise a reply larger than the advertised limit is mined in full and the
    report claims a budget it never enforced.
    """
    oversized = "alpha beta gamma delta. " * 9000
    assert len(oversized) > candidate_core.USER_REVIEW_MAX_INPUT_CHARACTERS

    summary = candidate_core.build_user_review_report(
        [candidate_core.SourceMessage("en", oversized, 1)], rules_by_language={}
    )["summary"]

    assert summary["analyzed_message_count"] == 1
    # Dropping messages did NOT happen; the single body was clipped.
    assert summary["messages_truncated"] is False
    assert summary["content_truncated"] is True




def test_an_unquoted_marker_does_not_close_a_quoted_fence():
    """A shallower marker is not a closer either, and this test used to say it was.

    It originally asserted that the trailing unquoted marker closes the quoted
    fence and that the prose after it is mineable -- pinning a leak as correct.
    Per CommonMark, leaving the blockquote implicitly ends the inner fence and
    the unquoted marker opens a NEW outer fence, so everything after it is still
    code.
    """
    from memory.anti_repeat_effects import build_repeat_signature

    text = _lines("> ```", "> API_TOKEN = 'zqxjleak'", "```", "DB_PASSWORD = 'zqxjleak2'")

    unprotected = _unprotected(text)

    assert "API_TOKEN" not in unprotected
    assert "DB_PASSWORD" not in unprotected
    assert build_repeat_signature(text, ["DB_PASSWORD"], language="en") is None




def _lines(*rows: str, eol: str = "\n") -> str:
    return eol.join(rows) + eol


def _unprotected(text: str) -> str:
    spans = candidate_core._protected_spans(text)
    return "".join(
        char
        for index, char in enumerate(text)
        if not any(start <= index < end for start, end in spans)
    )










def test_inline_code_protection_still_leaves_prose_minable():
    text = "we always say the exact same thing"

    assert "always" in _unprotected(text)






# Fullwidth punctuation that a CJK IME produces is SPEECH, not code. Treating
# it as an inline delimiter protected the text between two occurrences and
# silently dropped real catchphrases -- the exact thing this feature exists to
# surface. Inline delimiters are the ASCII backtick only, and the fence
# alphabet excludes the fullwidth tilde because a line of them is a decorative
# divider whose unclosed fence eats the rest of the reply.
_SPEECH_ELONGATION_CASES = [
    ("japanese", "そうですね～また明日ね～", "また明日ね"),
    ("chinese", "好呀～我们一起去吧～", "我们一起去吧"),
    ("repeated tildes", "はい～～ありがとう～～", "ありがとう"),
    ("ascii tilde prose", "we always say ~the exact same thing~ here", "always"),
    # U+FF40 in a kaomoji face, which is where it actually occurs.
    ("kaomoji pair", "（｀・ω・´）今天也要加油哦！（｀・ω・´）", "今天也要加油哦"),
    ("kaomoji unpaired", "(*･ω｀*) また一緒に散歩しようね", "また一緒に散歩しようね"),
    # A fullwidth-tilde divider line: an unclosed fence would protect to the
    # end of the text, so this one row costs the whole remainder of the reply.
    ("tilde divider", "你好呀\n～～～\n晚安啦做个好梦", "晚安啦做个好梦"),
    ("tilde divider behind marker", "- ～～～\n- 我们一起去吃饭吧", "我们一起去吃饭吧"),
]


@pytest.mark.parametrize(
    "label, text, must_remain_visible",
    _SPEECH_ELONGATION_CASES,
    ids=[row[0] for row in _SPEECH_ELONGATION_CASES],
)
def test_tilde_elongation_is_speech_not_code(label, text, must_remain_visible):
    assert must_remain_visible in _unprotected(text), label


# Deliberately NOT here: `a ｀SECRET=1｀ b` and a ～～～ fence. Both were pinned
# and both are removed on purpose -- they are structurally indistinguishable
# from the kaomoji and divider rows in _SPEECH_ELONGATION_CASES above, and the
# speech direction is the one that fails silently. The fullwidth FENCE forms
# stay, so a code block typed in Chinese input mode is still protected.
_FULLWIDTH_CODE_CASES = [
    ("ascii inline", "a `SECRET=1` b"),
    ("ascii fence", "```\nSECRET=1\n```\ntail"),
    ("tilde fence", "~~~\nSECRET=1\n~~~\ntail"),
    ("fullwidth tick fence", "｀｀｀\nSECRET=1\n｀｀｀\ntail"),
]


@pytest.mark.parametrize(
    "label, text",
    _FULLWIDTH_CODE_CASES,
    ids=[row[0] for row in _FULLWIDTH_CODE_CASES],
)
def test_fullwidth_code_delimiters_still_protect(label, text):
    assert "SECRET" not in _unprotected(text), label


# A list marker may only introduce an OPENING fence. Stripping it on every line
# let a CONTENT line that happens to read "- ```" be rewritten into a bare
# delimiter run, close the active fence early, and expose the rest of the block.
_LIST_MARKER_AS_CONTENT_CASES = [
    ("ascii backtick", "```\n- ```\nDB_PASSWORD = hunter2\n```\ntail"),
    ("ascii tilde", "~~~\n- ~~~\nDB_PASSWORD = hunter2\n~~~\ntail"),
    ("ordered marker", "~~~\n1. ~~~\nDB_PASSWORD = hunter2\n~~~\ntail"),
    ("quoted marker", "~~~\n> - ~~~\nDB_PASSWORD = hunter2\n~~~\ntail"),
    ("fullwidth tick", "｀｀｀\n- ｀｀｀\nDB_PASSWORD = hunter2\n｀｀｀\ntail"),
]


@pytest.mark.parametrize(
    "label, text",
    _LIST_MARKER_AS_CONTENT_CASES,
    ids=[row[0] for row in _LIST_MARKER_AS_CONTENT_CASES],
)
def test_a_list_marker_inside_a_fence_is_content_not_a_closer(label, text):
    assert "DB_PASSWORD" not in _unprotected(text), label


# A template body that merely wrapped was left unprotected while its single-line
# twin was masked. The two-line budget is what keeps that from becoming an
# over-protection bug: an unbounded newline-crossing match turns one stray
# delimiter in prose into a span that swallows the rest of the reply, and
# `<...>` is excluded outright because emoticons pair up across lines.
_MULTILINE_TEMPLATE_CASES = [
    ("jinja", "{{\nsecret helper phrase\n}}"),
    ("shell interpolation", "${\nsecret helper phrase\n}"),
    ("erb scriptlet", "<%\nsecret helper phrase\n%>"),
    ("jinja with a filter", "{{ secret helper phrase\n | default('x') }}"),
    # Opener and closer on their own lines with a two-line body: the shape a
    # hand-written template actually has, and the one a two-newline budget missed.
    ("jinja block", "{{\nalpha\nsecret helper phrase\n}}"),
    ("shell block", "${\nalpha\nsecret helper phrase\n}"),
    ("erb block", "<%\nalpha\nsecret helper phrase\n%>"),
    ("crlf jinja", "{{\r\nsecret helper phrase\r\n}}"),
    # Statement and comment blocks, which the expression form does not cover.
    ("jinja statement", "{%\nsecret helper phrase\n%}"),
    ("jinja comment", "{#\nsecret helper phrase\n#}"),
    # A template body may hold a brace of its own. Forbidding every brace
    # made all three containers miss a dict literal and mine the payload.
    ("jinja expression with a dict", '{{ {"k": "secret helper phrase"} }}'),
    ("jinja statement with a dict", '{% set c = {"k": "secret helper phrase"} %}'),
    ("jinja comment with a dict", '{# {"k": "secret helper phrase"} #}'),
]


@pytest.mark.parametrize(
    "label, text",
    _MULTILINE_TEMPLATE_CASES,
    ids=[row[0] for row in _MULTILINE_TEMPLATE_CASES],
)
def test_wrapped_template_bodies_are_protected(label, text):
    assert "secret helper phrase" not in _unprotected(text), label










_NESTED_CONTAINER_SPEECH = [
    ("bulleted speech", ["- 今天也辛苦了呢", "- 我们一起去吃饭吧"]),
    ("quoted speech", ["> 今天也辛苦了呢", "> 我们一起去吃饭吧"]),
    ("bulleted quoted speech", ["- > 今天也辛苦了呢", "- > 我们一起去吃饭吧"]),
]


@pytest.mark.parametrize(
    "label, rows",
    _NESTED_CONTAINER_SPEECH,
    ids=[row[0] for row in _NESTED_CONTAINER_SPEECH],
)
def test_container_markers_alone_do_not_protect_speech(label, rows):
    assert "我们一起去吃饭吧" in _unprotected(_lines(*rows)), label


# A code-span closer must be a run of EXACTLY the opening length; a longer run is
# content. `find` accepted the opening-length prefix of a longer run, so a span
# ended mid-run and, once a second shorter run paired with the leftovers, the
# body after it was mined as prose. Harmless while the search was bounded to one
# line -- the leftovers re-opened and the coverage merged -- but this file now
# searches to the end of the paragraph, which turns it into a real leak that
# also reaches the persisted signature.
_LONGER_RUN_CASES = [
    ("two runs", "run this `echo ```a`` export SECRET_TOKEN` ok"),
    ("single longer run", "reply `code ``inner`` SECRET_TOKEN` done"),
    ("opened with two", "reply ``code ```x``` SECRET_TOKEN`` done"),
    ("across lines", "你好 `代码 x\n继续 ```y`` SECRET_TOKEN\n` 完毕"),
]


@pytest.mark.parametrize(
    "label, text",
    _LONGER_RUN_CASES,
    ids=[row[0] for row in _LONGER_RUN_CASES],
)
def test_a_longer_run_inside_a_code_span_is_content(label, text):
    assert "SECRET_TOKEN" not in _unprotected(text), label


















_REAL_CONTAINER_CASES = [
    ("balanced parens target", "see [x](/api/f(1)/SECRET_TOKEN) here"),
    ("nested parens target", "see [x](/api/secret(SECRET_TOKEN)) here"),
    # Targets nest to any depth, so this is a scanner, not a pattern: a regex
    # allowing one level simply fails to match deeper ones and mines the
    # target as if there were no rule at all.
    ("two levels deep", "see [x](/api/f(g(SECRET_TOKEN))) here"),
    ("three levels deep", "see [x](/a(b(c(SECRET_TOKEN)))) here"),
    ("escaped close inside", "see [x](/api/SECRET_TOKEN\\)more) here"),
    ("escaped open inside", "see [x](/api/\\(SECRET_TOKEN) here"),
    ("target with a title", 'see [x](/api/SECRET_TOKEN "t") here'),
    ("real comment", "<!--\nSECRET_TOKEN\n-->"),
    ("real code tag", "<code>SECRET_TOKEN</code>"),
    ("nested same tag", "<code>a <code>b</code> SECRET_TOKEN</code>"),
]


@pytest.mark.parametrize(
    "label, text",
    _REAL_CONTAINER_CASES,
    ids=[row[0] for row in _REAL_CONTAINER_CASES],
)
def test_real_containers_still_protect(label, text):
    assert "SECRET_TOKEN" not in _unprotected(text), label


# A URL is protected because this module promises never to persist one. Both
# halves of that promise are pinned here: the tail has to reach the end of a
# real URL, and it must not run past one into the sentence that follows.
_URL_LEAK_CASES = [
    ("parenthesised path", "see https://example.com/(SECRET_TOKEN) here"),
    ("parens mid path", "see https://example.com/a(SECRET_TOKEN)/b here"),
    # A path nests parentheses to any depth, so this is a scanner and not a
    # pattern: one level encoded in the regex stopped at the inner "(" and
    # left the rest of the path minable -- and persisted.
    ("nested parens", "see https://example.com/f(g(SECRET_TOKEN)) here"),
    ("three paren levels", "see https://example.com/a(b(c(SECRET_TOKEN))) here"),
    ("tail after a group", "see https://example.com/f(g)/SECRET_TOKEN here"),
    ("two groups", "see https://example.com/f(g)/h(i(SECRET_TOKEN)) here"),
    ("nested parens on a bare host", "see example.com/f(g(SECRET_TOKEN)) here"),
    ("bare host after hanzi", "看这个吧h.io/SECRET_TOKEN，很好玩哦"),
    ("bare host after kana", "ネコはneko.jp/SECRET_TOKEN"),
    ("mixed case host", "see Example.com/SECRET_TOKEN here"),
    # DNS is case-insensitive, so an UPPER-case TLD is a real host. Only a
    # Capitalised one is rejected, because that is a resumed sentence.
    ("uppercase tld", "see Example.COM/SECRET_TOKEN here"),
    ("all caps host", "see EXAMPLE.COM/SECRET_TOKEN here"),
    # Punycode has to be tried BEFORE the generic TLD branch, which matches
    # the bare "xn" and stops there because everything after a TLD is
    # optional -- leaving the rest of the label and the path minable.
    ("punycode tld", "see example.xn--p1ai/SECRET_TOKEN here"),
    ("punycode both labels", "see xn--fiqs8s.xn--fiqz9s/SECRET_TOKEN here"),
    ("punycode upper", "see EXAMPLE.XN--P1AI/SECRET_TOKEN here"),
    ("mixed case tld", "see Example.CoM/SECRET_TOKEN here"),
    ("mixed case tld lower host", "see example.Com/SECRET_TOKEN here"),
    # A query or fragment may follow the host with no path at all.
    ("query with no path", "see example.com?token=SECRET_TOKEN here"),
    ("fragment with no path", "see example.com#SECRET_TOKEN here"),
    ("localhost query", "see localhost:8080?q=SECRET_TOKEN here"),
    # A textarea body is raw text, exactly like pre/code/script/style.
    ("textarea body", "<textarea>SECRET_TOKEN</textarea>"),
    ("unterminated textarea", "<textarea rows=2>SECRET_TOKEN"),
    ("scheme upper", "see HTTP://Example.TEST/SECRET_TOKEN here"),
    ("www upper", "see WWW.Example.TEST/SECRET_TOKEN here"),
    ("localhost upper", "see LOCALHOST:8080/SECRET_TOKEN here"),
    ("localhost", "see localhost:8080/SECRET_TOKEN here"),
    # Other URI schemes carry the same payload, and an address carries
    # PERSONAL data: only its domain half used to match, so the local part
    # -- the identifying half -- was mined and persisted.
    ("mailto", "write to mailto:SECRET_TOKEN@example.com now"),
    ("bare address", "write to SECRET_TOKEN@example.com now"),
    ("address after hanzi", "请联系SECRET_TOKEN@example.com啊"),
    ("tel", "see tel:+1555SECRET_TOKEN now"),
    ("data uri", "see data:text/plain,SECRET_TOKEN now"),
    ("file uri", "see file:///c/SECRET_TOKEN now"),
    ("ftp", "see ftp://host/SECRET_TOKEN now"),
]


@pytest.mark.parametrize(
    "label, text",
    _URL_LEAK_CASES,
    ids=[row[0] for row in _URL_LEAK_CASES],
)
def test_urls_are_protected_through_their_tail(label, text):
    from memory.anti_repeat_effects import build_repeat_signature

    assert "SECRET_TOKEN" not in _unprotected(text), label
    assert build_repeat_signature(text, ["SECRET_TOKEN"], language="en") is None, label










_BLOCK_LEAK_CASES = [
    # A TAB is worth four columns, so it can never be the "at most three spaces"
    # of padding a container marker is allowed. Stripping it as padding measured
    # the residual indent as zero and mined the code line as prose.
    ("tab then list marker", "see this:\n\n\t- SECRET_TOKEN here\n"),
    ("tab then blockquote", "see this:\n\n\t>SECRET_TOKEN here"),
    # A marker OPENS a block, so the paragraph above it is not continuing and
    # the indented content really is code.
    ("list marker interrupts a paragraph", "sure\n-     SECRET_TOKEN = 1\ndone"),
    ("quote marker interrupts a paragraph", "sure\n>     SECRET_TOKEN = 1\ndone"),
    ("spaces and tab then ordered marker", "see this:\n\n  \t1. SECRET_TOKEN here"),
    # Two list markers: stripping stopped after the first, so the fence never
    # opened, while the one-marker spelling protected the block in full.
    ("fence two list levels deep", "- - ~~~\nSECRET_TOKEN\n~~~"),
    ("fence list then ordered", "- 2) ~~~\nSECRET_TOKEN\n~~~"),
    # Line one is NOT an opener, so line two opens the real, unclosed fence.
    # Reading line one as an opener put every later delimiter one out of step.
    ("backtick info string offsets the fence", "```a`\n```\nSECRET_TOKEN"),
]


@pytest.mark.parametrize(
    "label, text",
    _BLOCK_LEAK_CASES,
    ids=[row[0] for row in _BLOCK_LEAK_CASES],
)
def test_block_code_behind_a_container_prefix_is_protected(label, text):
    from memory.anti_repeat_effects import build_repeat_signature

    assert "SECRET_TOKEN" not in _unprotected(text), label
    assert build_repeat_signature(text, ["SECRET_TOKEN"], language="en") is None, label






_DISPLAYED_CLOSER_CASES = [
    ("displayed comment closer", "<!-- alpha `-->` SECRET_TOKEN here -->"),
    ("displayed code closer", "<code>alpha `</code>` SECRET_TOKEN here </code>"),
    ("displayed closer in a fence", "<code>\n```\n</code>\n```\nSECRET_TOKEN here</code>"),
]


@pytest.mark.parametrize(
    "label, text",
    _DISPLAYED_CLOSER_CASES,
    ids=[row[0] for row in _DISPLAYED_CLOSER_CASES],
)
def test_a_displayed_closer_closes_nothing(label, text):
    from memory.anti_repeat_effects import build_repeat_signature

    assert "SECRET_TOKEN" not in _unprotected(text), label
    assert build_repeat_signature(text, ["SECRET_TOKEN"], language="en") is None, label




def test_url_paren_extension_scans_in_linear_time():
    """Extending each URL match by walking forward was O(n^2).

    A failed opener scanned ahead to the stop character, then the next match
    started one token later and scanned the same tail again. Measured on the
    first version of this fix: 25 s at 48 KB and 99 s at 96 KB, against an
    accepted reply size of 128 KB -- worse than the leak it closed. The bound
    below is two orders of magnitude looser than the two-pass version needs.
    """
    import time

    text = "a.com(" * 21_334
    started = time.perf_counter()
    candidate_core._runtime_protected_spans(text)
    assert time.perf_counter() - started < 5.0


# A scheme is matched by RULE, not by a list. Every entry here is chosen so that
# adding one more name to an allowlist could NOT rescue it.
_SCHEME_RULE_LEAK_CASES = [
    ("otpauth totp secret", "scan otpauth://totp/N?secret=SECRET_TOKEN now"),
    ("database password", "db is postgres://neko:SECRET_TOKEN@dbhost/app ok"),
    ("magnet", "grab magnet:?xt=urn:btih:SECRET_TOKEN now"),
    ("windows path", "open file:///C:/Users/me/SECRET_TOKEN here"),
    # A scheme name nobody would ever enumerate: this row is the whole point.
    ("arbitrary scheme shape", "odd zq7+x-.foo:SECRET_TOKEN here"),
]


@pytest.mark.parametrize(
    "label, text",
    _SCHEME_RULE_LEAK_CASES,
    ids=[row[0] for row in _SCHEME_RULE_LEAK_CASES],
)
def test_any_uri_scheme_is_protected(label, text):
    from memory.anti_repeat_effects import build_repeat_signature

    assert "SECRET_TOKEN" not in _unprotected(text), label
    assert build_repeat_signature(text, ["SECRET_TOKEN"], language="en") is None, label


_SCHEME_RULE_SPEECH_CASES = [
    # The opaque-part lookaheads are what separate a scheme from a colon in
    # speech. "together:D" is the discriminating one: a variant that merely
    # forbids CJK after the colon still eats it.
    ("emoticon after a colon", "see you together:D that was so much fun today"),
    ("cjk after a colon", "note:今天要早点睡觉哦明天还要早起呢我们一起加油吧"),
    ("ratio", "比例是 3:4 啊我们一起去吃饭吧"),
    ("clock", "时间 12:30 见面好不好呀"),
]


@pytest.mark.parametrize(
    "label, text",
    _SCHEME_RULE_SPEECH_CASES,
    ids=[row[0] for row in _SCHEME_RULE_SPEECH_CASES],
)
def test_a_colon_in_speech_is_not_a_scheme(label, text):
    assert candidate_core._runtime_protected_spans(text) == [], label


def test_a_reference_definition_destination_is_protected():
    """``[label]: /path`` puts a destination where the inline form hides it.

    The inline scanner only knows ``](``, so the reference form was mined and
    persisted.
    """
    from memory.anti_repeat_effects import build_repeat_signature

    text = "morning\n\n[cfg]: /srv/lanlan/keys/SECRET_TOKEN\n"
    assert "SECRET_TOKEN" not in _unprotected(text)
    assert build_repeat_signature(text, ["SECRET_TOKEN"], language="en") is None


def test_only_a_reference_definition_protects_that_path():
    """Reverse anchor: the same path in prose has to stay minable.

    Without this, widening ``_URL_RE`` until every bare ``/path`` is protected
    would satisfy the test above -- a far larger change that would eat every
    slash in ordinary speech.
    """
    assert candidate_core._runtime_protected_spans(
        "we saved it to /srv/lanlan/keys/owner-token today ok"
    ) == []


def test_a_bracketed_speaker_beat_is_not_a_reference_definition():
    """The destination has to LOOK like one, or a script beat runs to line end."""
    assert candidate_core._runtime_protected_spans(
        "[小八]:我们一起去吃饭吧！今天也辛苦了呢！"
    ) == []
    assert candidate_core._runtime_protected_spans(
        "[^1]: 我今天真的很开心呢"
    ) == []






def test_pre_and_code_still_count_nesting():
    """The dual: ordinary elements really do nest, so they keep the counter.

    Without this the fix could be 'stop counting everywhere', which ends
    `<code>a <code>b</code> SECRET</code>` at the inner closer and leaks the
    rest of the outer element.
    """
    assert "SECRET_TOKEN" not in _unprotected(
        "<code>a <code>b</code> SECRET_TOKEN</code>"
    )
    assert "SECRET_TOKEN" not in _unprotected("<script>SECRET_TOKEN</script>")
    assert "SECRET_TOKEN" not in _unprotected("<script>SECRET_TOKEN")


def test_a_reference_definition_may_omit_the_space_after_its_colon():
    """Markdown allows `[cfg]:/api/token`; requiring a space left it minable."""
    from memory.anti_repeat_effects import build_repeat_signature

    text = "morning\n\n[cfg]:/api/SECRET_TOKEN\n"
    assert "SECRET_TOKEN" not in _unprotected(text)
    assert build_repeat_signature(text, ["/api/SECRET_TOKEN"], language="en") is None
    # The destination-shape check, not the space, is what keeps this off speech.
    assert candidate_core._runtime_protected_spans("[小八]:我们一起去吃饭吧！") == []


def test_the_pre_narrowing_clip_is_what_saves_an_oversized_oldest_reply():
    """The clip has to run BEFORE the window narrows, and this is where it shows.

    With the oversized reply NEWEST, the fair-share eviction added later reaches
    the same answer on its own, so a guard written there passes with the clip
    deleted -- measured, it did. OLDEST is the position where the clip is the
    only thing that keeps the window whole: without it the window narrows to 3
    messages and no body is cut, with it all 4 survive.
    """
    phrase = "\u6211\u4eec\u4e00\u8d77\u53bb\u516c\u56ed\u6563\u6b65\u5427"
    fillers = "\u554a\u55ef\u597d\u5462\u5440\u54e6"
    budget = candidate_core.USER_REVIEW_MAX_INPUT_CHARACTERS

    messages = [
        candidate_core.SourceMessage("zh", "\u5c0f" * (2 * budget), 1)
    ] + [
        candidate_core.SourceMessage(
            "zh", fillers[i] + " " + phrase + " " + fillers[i + 1], i + 2
        )
        for i in range(3)
    ]

    report = candidate_core.build_user_review_report(
        messages, message_count_threshold=1, rules_by_language={}
    )
    summary = report["summary"]

    assert summary["analyzed_message_count"] == 4, (
        "the oversized oldest reply cost the window %d of its 4 messages"
        % (4 - summary["analyzed_message_count"])
    )
    assert summary["content_truncated"] is True, (
        "nothing was clipped, so the window can only have narrowed instead"
    )


def test_one_oversized_reply_does_not_hide_the_history_behind_it(monkeypatch):
    """A long latest reply must not make a full history look like one message.

    Narrowing ran first, so an oversized newest reply held the total over
    budget until every older reply had been dropped, and only the survivor
    was clipped. The window then held one message, the distinct-message
    threshold removed every candidate, and the panel reported not enough
    history for a character that had plenty.

    Clipping it to the whole budget would not fix that either -- it would
    still fill the window alone. It has to leave room.

    The OCCURRENCE budget is raised here so the CHARACTER budget is the one
    under test. With the shipped constants the occurrence budget binds first
    -- a reply long enough to exceed 128 KiB is far past 100,000 occurrences
    -- and reaches the same one-message window through its own narrowing
    loop. That loop is not restructured here; it halves the body, which is
    what keeps the request from failing outright.
    """
    monkeypatch.setattr(
        candidate_core, "USER_REVIEW_MAX_OCCURRENCES", 100_000_000
    )
    line = "今天天气真好啊我们一起去散步吧"
    older = [
        candidate_core.SourceMessage("zh-CN", line * 40, index)
        for index in range(1, 5)
    ]
    huge = candidate_core.SourceMessage(
        "zh-CN",
        line * (candidate_core.USER_REVIEW_MAX_INPUT_CHARACTERS // len(line) + 10),
        5,
    )
    assert len(huge.content) > candidate_core.USER_REVIEW_MAX_INPUT_CHARACTERS

    report = candidate_core.build_user_review_report(
        older + [huge],
        message_count_threshold=3,
        rules_by_language={},
    )

    summary = report["summary"]
    assert summary["content_truncated"] is True
    assert summary["analyzed_message_count"] >= 3, (
        "the oversized reply crowded the window down to %d message(s), so a "
        "3-message threshold can never be met"
        % summary["analyzed_message_count"]
    )
    assert report["candidates"], (
        "no candidates survived, which is what the panel renders as "
        "insufficient history"
    )




def test_an_occurrence_heavy_reply_does_not_hide_the_history_behind_it():
    """The occurrence budget has the same ordering problem as the character one.

    Its narrowing loop halves toward the newest reply and only clips a body
    once nothing else is left, so one very long reply discards the very
    history that makes the distinct-message threshold reachable. The report
    then holds one message, the repeated phrase from the earlier replies is
    gone, and the panel says there is not enough history.

    This is the path that binds with the shipped constants -- a reply long
    enough to matter passes the occurrence budget long before the character
    one.
    """
    phrase = "晚安啦做个好梦"
    filler = "今天天气真好啊"
    ordinary = [
        candidate_core.SourceMessage(
            "zh-CN", filler + phrase + filler, index
        )
        for index in range(1, 4)
    ]
    heavy = candidate_core.SourceMessage(
        "zh-CN", filler * (30_000 // len(filler)), 4
    )

    report = candidate_core.build_user_review_report(
        ordinary + [heavy],
        message_count_threshold=3,
        rules_by_language={},
    )

    summary = report["summary"]
    assert summary["analyzed_message_count"] >= 3, (
        "one long reply crowded the window down to %d message(s)"
        % summary["analyzed_message_count"]
    )
    normalized = {
        candidate.get("normalized_phrase", "")
        for candidate in report["candidates"]
    }
    assert any(phrase in value for value in normalized), (
        "the phrase repeated across the earlier replies was lost with them"
    )


def test_a_uniformly_large_window_narrows_without_cutting_a_body():
    """No outlier means dropping messages, which is the cheaper cut.

    Clipping the newest whenever the budget is exceeded would shave a reply
    that is no larger than its neighbours, losing its content for nothing --
    the window is over budget as a whole, not because of one reply. The
    outlier test is what keeps those two cases apart.
    """
    filler = "今天天气真好啊"
    uniform = [
        candidate_core.SourceMessage(
            "zh-CN", filler * (4_000 // len(filler)), index
        )
        for index in range(1, 13)
    ]
    lengths = {len(message.content) for message in uniform}
    assert len(lengths) == 1, "the window has to be uniform for this test"

    report = candidate_core.build_user_review_report(
        uniform, message_count_threshold=1, rules_by_language={}
    )

    summary = report["summary"]
    assert summary["messages_truncated"] is True, (
        "this window should have been over budget and narrowed"
    )
    assert summary["content_truncated"] is False, (
        "a reply no larger than its neighbours had its body cut, which "
        "loses content the narrowing alone would have kept"
    )


def _protects(text, needle):
    """True when ``needle`` sits inside a protected span of ``text``."""
    at = text.find(needle)
    assert at >= 0, needle
    return any(
        start <= at < end for start, end in candidate_core._protected_spans(text)
    )




def test_an_internationalised_mailto_is_protected():
    """The generic scheme rule requires ASCII in the opaque part; an address may have none.

    That requirement is the guard keeping the rule off "note:<CJK>" prose, so it
    is not the thing to loosen. But it also rejected a fully internationalised
    address, and the local part is the identifying half -- matching only from
    the domain would be worse than not matching at all. A structured mailto
    alternative, which requires the local@domain.tld shape, is what makes the
    unbounded alphabet safe here.
    """
    local = "\u7528\u6237\u79d8\u5bc6"
    address = "mailto:" + local + "@\u4f8b\u5b50.\u516c\u53f8"

    assert _protects("\u5199\u4fe1\u7ed9 " + address + " \u5427", local), (
        "an internationalised mailto address was mined"
    )

    # The dual that matters most: the generic guard is untouched, so ordinary
    # CJK speech after a colon is still minable rather than swallowed to the
    # end of the text.
    catchphrase = "please remember to rest"
    assert not _protects(
        "\u5907\u6ce8:" + catchphrase + " " + catchphrase, catchphrase
    ), "a colon in speech began protecting the rest of the reply"
    assert not _protects(
        "I said mailto \u5427 " + catchphrase + " " + catchphrase, catchphrase
    ), "the bare word mailto swallowed the reply"
    # And an ASCII address still resolves, through the same alternative.
    assert _protects("write to mailto:ops@example.com now", "ops@example.com")






def test_a_long_html_tag_still_protects_its_attributes():
    """An 80-character cap on the attribute run made a real tag fail to match.

    Long class/style/data-* attributes are ordinary, and busting the cap left
    the WHOLE tag unrecognised -- so a data-key payload inside it was mined and
    persisted, while its short twin was masked.

    The cap never bought what it looked like it bought: "a <b and c> d" matched
    under it just as well. What keeps this off "<3", ">_<" and "->" is the
    leading-letter requirement, not the length. So the cost of removing it is
    one line of speech between a tag-shaped opener and a later ">", which is
    over-protection, against a leak.
    """
    secret = "secret helper phrase"
    short_tag = '<div data-key="' + secret + '">hello</div>'
    long_tag = (
        '<div data-pad="' + "x" * 90 + '" data-key="' + secret + '">hello</div>'
    )

    assert _protects(short_tag, secret)
    assert _protects(long_tag, secret), (
        "a tag with long attributes was left unprotected and its payload mined"
    )

    # The duals, in the runaway direction. A tag-shaped opener is still
    # required, and the run is still bounded to its own line.
    catchphrase = "please remember to rest"
    assert not _protects(
        "3 < 5 and 10 > 7 " + catchphrase + " " + catchphrase, catchphrase
    )
    assert not _protects(
        "see <a href" + chr(10) + chr(10) + catchphrase + " " + catchphrase,
        catchphrase,
    ), "an unclosed tag opener ran past its line into the speech after it"


def test_an_oversized_reply_anywhere_does_not_take_the_history_with_it():
    """The outlier is found by POSITION, not assumed to be the newest.

    Both budgets clipped only the newest and then evicted history, so an
    oversized reply sitting anywhere else took the whole window down with it:
    the evictions threw away the replies that make the distinct-message
    threshold reachable, and then threw away the outlier too. The panel
    reported not enough history for a window that had plenty.

    The outlier sits SECOND-NEWEST here, so the newest-only clip above cannot
    be what rescues it.
    """
    phrase = "我们一起去公园散步吧"
    messages = [
        candidate_core.SourceMessage("zh", "\u554a " + phrase + " \u5462", 1),
        candidate_core.SourceMessage("zh", "\u55ef " + phrase + " \u5440", 2),
        candidate_core.SourceMessage("zh", "\u597d " + phrase + " \u54e6", 3),
        candidate_core.SourceMessage(
            "zh",
            "\u5c0f" * (candidate_core.USER_REVIEW_MAX_INPUT_CHARACTERS + 10),
            4,
        ),
        candidate_core.SourceMessage("zh", "\u597d\u7684", 5),
    ]

    report = candidate_core.build_user_review_report(messages)
    summary = report["summary"]

    assert summary["analyzed_message_count"] == len(messages), (
        "the window was narrowed to %d messages, so the replies carrying the "
        "repeated phrase were evicted to make room for an outlier that was "
        "then evicted too" % summary["analyzed_message_count"]
    )
    assert summary["content_truncated"] is True
    # Candidates are n-grams OF the shared phrase, so the claim to assert is
    # that evidence from all THREE ordinary replies survived -- that is what
    # the eviction used to destroy, and what the distinct-message threshold
    # needs.
    from_three = [
        candidate
        for candidate in report["candidates"]
        if candidate["message_count"] == 3
        and str(candidate["phrase"]) in phrase
    ]
    assert from_three, (
        "no candidate came from all three replies that shared the phrase, "
        "so their evidence did not survive the narrowing"
    )

    # The dual, which is what stops this from being "always cut a body": a
    # window that is merely large as a WHOLE has no dominant message, so it
    # still narrows by dropping. Pinned by
    # test_a_uniformly_large_window_narrows_without_cutting_a_body above.
    uniform = [
        candidate_core.SourceMessage(
            "zh",
            "\u5c0f" * (candidate_core.USER_REVIEW_MAX_INPUT_CHARACTERS // 3),
            index,
        )
        for index in range(1, 6)
    ]
    assert candidate_core._clip_dominant_message(uniform) is None, (
        "a uniformly large window was treated as having an outlier"
    )
    # And a single message is never 'dominant' -- the callers handle that case.
    assert candidate_core._clip_dominant_message(uniform[:1]) is None






def test_two_comparably_heavy_replies_do_not_evict_the_history():
    """"Longer than all the others combined" catches exactly one outlier.

    Two comparably heavy replies evaded it, and the eviction that followed threw
    away the ordinary replies carrying the repeated phrase -- the very defect the
    single-outlier clip was added to stop. Dominance is measured against the
    AVERAGE of the rest instead.
    """
    phrase = "我们一起去公园散步吧"
    budget = candidate_core.USER_REVIEW_MAX_INPUT_CHARACTERS
    messages = [
        candidate_core.SourceMessage("zh", "\u554a " + phrase + " \u5462", 1),
        candidate_core.SourceMessage("zh", "\u55ef " + phrase + " \u5440", 2),
        candidate_core.SourceMessage("zh", "\u597d " + phrase + " \u54e6", 3),
        candidate_core.SourceMessage("zh", "\u5c0f" * budget, 4),
        candidate_core.SourceMessage("zh", "\u5927" * budget, 5),
    ]

    report = candidate_core.build_user_review_report(messages)
    summary = report["summary"]

    assert summary["analyzed_message_count"] == len(messages), (
        "two comparable heavies evicted the window down to %d messages"
        % summary["analyzed_message_count"]
    )
    assert any(
        candidate["message_count"] == 3 and str(candidate["phrase"]) in phrase
        for candidate in report["candidates"]
    ), "the evidence from the three ordinary replies did not survive"

    # The ratio is what separates this from a uniformly large window, so pin it
    # rather than only exercising it: a derived test that merely reads the
    # constant would still pass if the constant changed.
    assert candidate_core._USER_REVIEW_OUTLIER_RATIO == 2
    uniform = [
        candidate_core.SourceMessage("zh", "\u5c0f" * (budget // 3), index)
        for index in range(1, 6)
    ]
    assert candidate_core._clip_dominant_message(uniform) is None, (
        "a uniformly large window was treated as having an outlier, so every "
        "body would be cut instead of the window narrowing"
    )
    # Exactly at the ratio is NOT dominant; just past it is. Four messages, so
    # the average of the rest is the sum of the other three divided by three.
    rest = 3 * 3000
    at_ratio = [
        candidate_core.SourceMessage("zh", "x" * 3000, index) for index in (1, 2, 3)
    ] + [candidate_core.SourceMessage("zh", "x" * (2 * rest // 3), 4)]
    assert candidate_core._clip_dominant_message(at_ratio) is None
    past_ratio = at_ratio[:3] + [
        candidate_core.SourceMessage("zh", "x" * (2 * rest // 3 + 1), 4)
    ]
    assert candidate_core._clip_dominant_message(past_ratio) is not None


def test_short_old_replies_are_not_evicted_for_heavy_new_ones():
    """A short old reply is not what busted the budget.

    Eviction took the OLDEST message each time, so with four budget-sized
    replies in the window the three short ones ahead of them were thrown away
    one by one and the window collapsed to a single message. The repeated
    phrase went with them and the panel reported not enough history -- the same
    outcome the outlier clip was added to stop, reached by the other half of the
    loop.

    Measured across two to six heavy replies, so this is not one lucky shape:
    the collapse started at four, which is exactly where no single reply is
    dominant any more.
    """
    phrase = "我们一起去公园散步吧"
    budget = candidate_core.USER_REVIEW_MAX_INPUT_CHARACTERS
    fillers = "啊嗯好呢呀哦吧干嘛真"

    for heavy_count in (4, 5, 6):
        messages = [
            candidate_core.SourceMessage(
                "zh", fillers[i] + " " + phrase + " " + fillers[i + 1], i + 1
            )
            for i in range(3)
        ] + [
            candidate_core.SourceMessage(
                "zh", fillers[i % len(fillers)] * budget, 100 + i
            )
            for i in range(heavy_count)
        ]

        report = candidate_core.build_user_review_report(messages)
        summary = report["summary"]

        assert summary["analyzed_message_count"] > 1, (
            "with %d heavy replies the window collapsed to %d message(s)"
            % (heavy_count, summary["analyzed_message_count"])
        )
        assert any(
            candidate["message_count"] >= 3
            and str(candidate["phrase"]) in phrase
            for candidate in report["candidates"]
        ), (
            "the three short replies carrying the phrase were evicted for %d "
            "heavy ones" % heavy_count
        )

    # The dual, and the reason this is not simply "never evict": when NOTHING is
    # over its fair share there is no better victim, so the oldest still goes
    # and bodies stay intact. That is the uniform case
    # test_a_uniformly_large_window_narrows_without_cutting_a_body pins.
    uniform = [
        candidate_core.SourceMessage("zh", "x" * (budget // 4), index)
        for index in range(1, 7)
    ]
    report = candidate_core.build_user_review_report(
        uniform, message_count_threshold=1, rules_by_language={}
    )
    assert report["summary"]["messages_truncated"] is True


def _persists(text, needle):
    """True when the needle survives into a signature the sidecar would store."""
    from memory.anti_repeat_effects import build_repeat_signature

    return build_repeat_signature(text, [needle], language="en") is not None


def test_a_discarded_template_match_does_not_hide_what_it_covered():
    """finditer skips past matches this layer throws away.

    A tag-shaped opener inside inline code makes _TEMPLATE_RE match from there
    to the next ">" anywhere on the line. _protected_spans discards that match
    because it starts inside a runtime span -- but finditer has already advanced
    past everything it covered, so a "${...}" payload sitting in that window was
    never matched by any alternative.

    Harmless while the tag run was capped at 80 characters. Removing the cap
    turned the skip window into a whole line, which is what made it reachable.
    """
    secret = "zzsecretzz"
    pad = "x" * 82
    draft = (
        "note " + chr(96) + "if a<b then" + chr(96) + " " + pad
        + " the token is ${" + secret + "} and 3>2 ok"
    )

    assert _protects(draft, secret), (
        "the template payload after a discarded match was left unprotected"
    )
    assert not _persists(draft, secret), "and it reached the 120-day sidecar"

    # The control that isolates the cause: the same draft with nothing
    # tag-shaped inside the code span.
    assert _protects(draft.replace("a<b then", "ab then"), secret)


def test_an_escaped_closer_never_leaves_the_destination_with_no_span_at_all():
    """The escape handling must be monotone: never less protected than before.

    Consuming "\\>" as a unit makes the angle form need a LATER unescaped ">".
    With none, the alternative fails outright, the whitespace-delimited form
    stops at the first space, and the shape check rejects that fragment for
    having no ">" -- so the destination got NO span, where the old truncating
    pattern gave it a full one. The old form stays behind the new one.
    """
    secret = "zzsecretzz"
    draft = (
        "[cfg]: <../my notes/" + secret + chr(92) + ">" + chr(10)
        + "thanks for listening today"
    )

    assert _protects(draft, secret), (
        "an escaped closer with nothing after it lost the whole destination"
    )
    assert not _persists(draft, secret)

    # Still better than the old form where a later closer exists, which is the
    # improvement this must not give back.
    assert _protects(
        "[cfg]: <../a" + chr(92) + "> " + secret + ">" + chr(10) + chr(10) + "ok",
        secret,
    )


def test_the_remaining_container_bodies_stop_only_at_their_own_closer():
    """A body may legitimately hold the character its class excluded.

    "${...}" excluded "{" and "<%...%>" excluded "%", neither of which is the
    container's closer, so a body holding one made the alternative fail entirely
    and nothing else picked the payload up. The three brace containers were
    already tempered for exactly this reason.
    """
    secret = "zzsecretzz"
    holding_a_brace = 'the config is ${ {"key": "' + secret + '"} } yes'
    holding_a_percent = "the config is <% rate = 50% key = " + secret + " %> yes"

    for draft in (holding_a_brace, holding_a_percent):
        assert _protects(draft, secret), draft
        assert not _persists(draft, secret), draft

    # Controls: the same drafts without the inner character were always fine,
    # and the tempered sibling holding the identical brace always was too. They
    # are what show the body CLASS rather than the payload shape was deciding.
    assert _protects('the config is ${ "' + secret + '" } yes', secret)
    assert _protects("the config is <% key = " + secret + " %> yes", secret)
    assert _protects(
        'the config is {% set c = {"k": "' + secret + '"} %} yes', secret
    )


def test_a_bracketed_placeholder_has_no_length_cap_either():
    """The same cap, in the same regex literal, as the one removed from the tag run.

    A 65-character name failed the whole alternative, so nothing was protected
    and the identifier was mined -- while its 64-character twin in the same
    sentence was masked. One character of length was the only difference.
    """
    name = "GITHUB_PERSONAL_ACCESS_TOKEN_FOR_PROD_DEPLOY_PIPELINE_ROTATION_2"
    assert len(name) == 64, "the fixture has to sit exactly on the old cap"

    for candidate in (name, name + "X"):
        draft = "the config key is [" + candidate + "] and you must set it"
        assert _protects(draft, candidate), len(candidate)

    # A single letter in brackets is still not a placeholder, so this did not
    # become "any bracketed capital".
    catchphrase = "please remember to rest"
    assert not _protects("[A] " + catchphrase + " " + catchphrase, catchphrase)


def test_a_displayed_parenthesis_closes_no_link_target():
    """The rule both bracket arms follow was never applied to the parentheses.

    A ")" shown inside a code span closed the target early, so the rest of the
    destination was mined -- while the identical link without the code span was
    fully protected.
    """
    tail = "/keys/secret-token-value"
    draft = "[docs](/api/" + chr(96) + ")" + chr(96) + tail + ") \u597d\u5440"

    assert _protects(draft, tail), (
        "a displayed ')' closed the target and mined the rest of it"
    )
    # The control, which was already passing: the same link with no code span.
    assert _protects("[docs](/api/x" + tail + ") \u597d\u5440", tail)










def test_a_line_of_openers_does_not_take_quadratic_time():
    """Without the line fallback each opener re-scans the rest of the line.

    Measured at the accepted reply size, one line of nothing but openers took
    around thirty seconds per container kind -- on the live turn path, since the
    effects sidecar masks every draft. The budget here is deliberately loose:
    this guard is for the difference between milliseconds and half a minute, not
    for a performance target.
    """
    import time

    budget = candidate_core.USER_REVIEW_MAX_INPUT_CHARACTERS
    for unit in ("${ ", "<% ", "{{ ", "{% ", "{# "):
        draft = (unit * (budget // len(unit)))[:budget]
        started = time.perf_counter()
        candidate_core._protected_spans(draft)
        elapsed = time.perf_counter() - started
        assert elapsed < 5.0, (
            "%r openers on one line took %.1fs" % (unit, elapsed)
        )




def test_a_reference_label_honours_escapes_too():
    """Fixing only the DESTINATION left the same half-fix in the same pattern.

    The label class stopped at a displayed "]", so "[cfg\\]]: /secret" was not
    recognised as a definition at all and its destination stayed minable -- the
    exact shape the destination alternative had already been fixed for.
    """
    target = "secret-helper-phrase"

    assert _protects(
        "[cfg" + chr(92) + "]]: /" + target + chr(10) + chr(10) + "ok", target
    ), "an escaped bracket in the label hid the whole definition"
    # The dual: a plain label still resolves, and bracketed speech still does
    # not become a definition.
    assert _protects("[cfg]: /" + target + chr(10) + chr(10) + "ok", target)
    catchphrase = "please remember to rest"
    assert not _protects("[" + catchphrase + "] " + catchphrase, catchphrase)








def test_an_unclosed_block_comment_is_decided_in_one_substring_scan():
    """An unbounded lazy body scans to end of text once per opener.

    Measured 4.1s on a full-budget line of nothing but openers. The case is
    decidable in a single substring scan, because if the closer appears NOWHERE
    then no opener can match -- and when it does appear, the first opener
    consumes everything up to it, so the openers behind it are already inside
    that span.

    Loose budget on purpose: this guard is for the difference between
    milliseconds and seconds, not a performance target.
    """
    import time

    budget = candidate_core.USER_REVIEW_MAX_INPUT_CHARACTERS
    unit = "{% comment %} "
    for label, draft in (
        ("no closer", (unit * (budget // len(unit)))[:budget]),
        (
            "one closer at the end",
            (unit * (budget // len(unit)))[: budget - 16] + "{% endcomment %}",
        ),
    ):
        started = time.perf_counter()
        candidate_core._protected_spans(draft)
        elapsed = time.perf_counter() - started
        assert elapsed < 2.0, "%s took %.1fs" % (label, elapsed)


def test_a_reference_definition_may_sit_inside_a_container():
    """A definition nested in a blockquote or a list is ordinary Markdown.

    The bare "^" anchor saw the container marker and nothing else, so
    "> [cfg]: /secret" was not a definition at all and its destination stayed
    minable -- while every reader resolves it.
    """
    target = "secret-helper-phrase"
    tail = chr(10) + chr(10) + "ok"

    for prefix in ("> ", ">", "- ", "1. ", "  > ", "> > "):
        assert _protects("%s[cfg]: /%s%s" % (prefix, target, tail), target), prefix

    # The duals: the unnested form still resolves, and bracketed speech behind a
    # marker is still not a definition.
    assert _protects("[cfg]: /" + target + tail, target)
    catchphrase = "please remember to rest"
    assert not _protects("> [" + catchphrase + "] " + catchphrase, catchphrase)














def test_the_block_precheck_needs_a_real_closing_tag():
    """A reply that merely MENTIONS a closer keyword must not re-arm the scan.

    The pre-check picks between two compiled patterns. Keyed on the bare word,
    prose containing "endraw" selected the unbounded one, and every unmatched
    opener then rescanned the rest of the text: measured 3.11s at 96 KiB with
    the word present against 0.05s without, growing 4x per doubling.

    A regex for the real closing TAG is exact AND still one linear pass, so this
    is not a trade between the two.
    """
    import time

    budget = candidate_core.USER_REVIEW_MAX_INPUT_CHARACTERS
    tail = " the endraw keyword is mentioned here"
    draft = ("{% comment %} " * 9000)[: budget - len(tail)] + tail

    started = time.perf_counter()
    candidate_core._protected_spans(draft)
    elapsed = time.perf_counter() - started
    assert elapsed < 1.0, "a mentioned keyword re-armed the scan (%.1fs)" % elapsed

    # The dual, and the reason this cannot be fixed by dropping the pre-check:
    # a REAL closer, in every modifier spelling, still selects the full pattern.
    secret = "secret helper phrase"
    for modifier in ("+", "-", ""):
        opener = "{%" + modifier + " comment " + modifier + "%}"
        closer = "{%" + modifier + " endcomment " + modifier + "%}"
        assert _protects(opener * 10 + " " + secret + " " + closer, secret), modifier










def test_an_opener_with_an_unpaired_quote_still_protects_its_body():
    """Malformed, but every parser still reads it as an opener.

    The quote-pairing form of the attribute run cannot match it -- there is no
    closing quote on the line -- so without the loose fallback beside it the
    element is not recognised at all and its body is mined. That is the
    leaking direction, which is the one this module cannot accept.
    """
    secret = "secret helper phrase"
    quote = chr(34)

    assert _protects(
        "<code a=" + quote + "b>" + secret + "</code>", secret
    ), "an unpaired quote stopped the opener being recognised at all"


def test_the_attribute_run_cannot_chain_across_tags():
    """A quote must belong to exactly one branch of the run.

    When a quote could match either the quoted branch or the bare one, the
    closing quote of one tag re-paired with the opening quote of the NEXT,
    so a run with no ">" chained across the document and the scan went
    quadratic: 4.8s on 128 KiB against 0.26s for the pattern it replaced.
    The bound here is deliberately loose -- it is separating 0.04s from
    several seconds, not measuring anything.
    """
    import time

    from utils.natural_expression_candidates import (
        USER_REVIEW_MAX_INPUT_CHARACTERS,
    )

    quote = chr(34)
    unit = "<code data-x=" + quote + "aaaa" + quote + " "
    cap = USER_REVIEW_MAX_INPUT_CHARACTERS
    text = (unit * (cap // len(unit) + 1))[:cap]

    started = time.perf_counter()
    candidate_core._protected_spans(text)
    elapsed = time.perf_counter() - started

    assert elapsed < 2.0, (
        "scanning %d characters of unclosed openers took %.2fs -- the "
        "attribute run is backtracking across tag boundaries again"
        % (len(text), elapsed)
    )






def test_the_two_uncapped_families_are_gated_separately():
    """Their closers are different, so one gate cannot stand for both.

    Sharing one meant a stray "#}" selected the pattern carrying the
    uncapped "{% comment %}" alternative as well, and unpaired comment
    openers then each scanned to end of text -- 4.27s at the input cap
    against 0.05s. The bound here is loose on purpose: it separates
    milliseconds from seconds.
    """
    import time

    from utils.natural_expression_candidates import (
        USER_REVIEW_MAX_INPUT_CHARACTERS,
    )

    unit = "{% comment %} "
    cap = USER_REVIEW_MAX_INPUT_CHARACTERS
    text = (unit * (cap // len(unit) + 1))[:cap]
    # One stray closer belonging to the OTHER family.
    text = text[:-2] + "#}"

    started = time.perf_counter()
    candidate_core._protected_spans(text)
    elapsed = time.perf_counter() - started

    assert elapsed < 1.0, (
        "unpaired comment openers beside a stray '#}' took %.2fs -- the two "
        "uncapped families are sharing a gate again" % elapsed
    )






def test_one_block_family_does_not_pay_for_another():
    """The three families have three closers, so they need three gates.

    Sharing one meant a single valid "{% raw %}x{% endraw %}" anywhere
    selected the pattern carrying the unbounded COMMENT alternative too, and
    every unpaired "{% comment %}" opener then rescanned the rest of the text
    for an "endcomment" that was never there. Reported at 112 KiB: about 7
    seconds, and still quadratic.

    The bound is loose on purpose -- it separates milliseconds from seconds.
    """
    import time

    from utils.natural_expression_candidates import (
        USER_REVIEW_MAX_INPUT_CHARACTERS,
    )

    unit = "{% comment %} "
    cap = USER_REVIEW_MAX_INPUT_CHARACTERS
    text = (unit * (cap // len(unit) + 1))[:cap]
    # One valid pair belonging to a DIFFERENT family.
    text = text[:-32] + "{% raw %}x{% endraw %}"

    started = time.perf_counter()
    candidate_core._protected_spans(text)
    elapsed = time.perf_counter() - started

    assert elapsed < 1.0, (
        "unpaired comment openers beside a valid raw pair took %.2fs -- the "
        "block families are sharing a gate again" % elapsed
    )

    # And each family still protects its own body, so the gate is not simply
    # refusing everything.
    secret = "secret helper phrase"
    nl = chr(10)
    for opener, closer in (
        ("comment", "endcomment"),
        ("raw", "endraw"),
        ("verbatim", "endverbatim"),
    ):
        body = (
            "{% " + opener + " %}" + nl * 4 + secret + nl + "{% " + closer + " %}"
        )
        assert _protects(body, secret), (
            "a %s body past the line budget was left minable" % opener
        )


def test_a_reference_destination_may_be_a_bare_fragment():
    """"[cfg]: #config" is a valid definition and the shape check refused it.

    So the whole definition stayed minable, title included -- and the title
    is the half most likely to read as speech.
    """
    secret = "secret helper phrase"
    quote = chr(34)
    nl = chr(10)

    assert _protects(
        "[cfg]: #config " + quote + secret + quote + nl + nl + "ok", secret
    ), "a fragment destination was not recognised as one"

    # The duals: a path destination still resolves, and ordinary speech that
    # merely starts with a hash is not a definition.
    assert _protects(
        "[cfg]: /api/key " + quote + secret + quote + nl + nl + "ok", secret
    )
    catchphrase = "please remember to rest"
    assert not _protects(
        "# heading" + nl + nl + catchphrase + " " + catchphrase, catchphrase
    )


def test_three_oversized_replies_still_yield_their_shared_phrase(monkeypatch):
    """Narrowing must not take the window below the sample a candidate needs.

    Three uniformly large replies sharing a phrase: nothing dominates, so
    nothing gets clipped, and eviction took the window to two. Every
    candidate was then removed for want of occurrences in three distinct
    messages, and the panel reported "not enough history" about a window
    that had it in every single reply.

    A shorter look at three messages can still find a phrase in all three.
    Two whole messages cannot find it in three.
    """
    monkeypatch.setattr(candidate_core, "USER_REVIEW_MAX_INPUT_CHARACTERS", 300)
    phrase = "quiet lantern by the window"
    filler = "x" * 140
    messages = [
        candidate_core.SourceMessage(
            "en", phrase + " " + filler, source_line
        )
        for source_line in range(1, 4)
    ]

    report = candidate_core.build_user_review_report(
        messages, message_count_threshold=3, rules_by_language={}
    )

    summary = report["summary"]
    assert summary["assistant_message_count"] == 3
    assert summary["analyzed_message_count"] == 3, (
        "the window narrowed below the distinct-message threshold, so the "
        "phrase could not be found in three replies however often it appears"
    )
    # CONTENT, not messages: the whole point is that no message left.
    assert summary["content_truncated"] is True
    assert summary["messages_truncated"] is False
    assert report["candidates"], (
        "no candidate came back from three replies that all carry the phrase"
    )


# ---------------------------------------------------------------------------
# The opener detector.
#
# Eight span scanners used to compute exactly WHERE protected text ended, so
# the prose around it stayed minable. Keeping a catchphrase is not worth that:
# an opener now discards the whole reply, and the tests that pinned those
# boundaries are gone with the machinery they described.
#
# Their INPUTS are not gone. The table below was lifted mechanically out of
# those deleted tests: every string literal in them that the old span layer
# protected. It is the leak-direction coverage they provided, carried across
# without depending on anyone's judgement about which ones mattered.
# ---------------------------------------------------------------------------

_SHAPES_THE_SPAN_LAYER_PROTECTED = (
    '`hidden phrase` https://example.test/hidden-phrase\nintranet.example/private-path\n```text\nhidden phrase\n```\n{{hidden phrase}} <HIDDEN_PHRASE>\nvisible phrase',
    'visible phrase\n\n    secret_key = value',
    'One long reply must narrow, not dead-end the panel.\n\n    Halving the window floors at one message. Rethrowing there turned every\n    selection containing that reply into a 422 the panel can only render as\n    "please try again", and retrying never helps -- the same reply is still\n    the newest one. The body is halved instead.\n    ',
    'we always say the exact same thing and then ```code``` follows',
    '`<pre>` / `<code>` mark code as explicitly as a Markdown fence does.\n\n    The generic `<...>` template pattern protected only the TAGS, leaving the\n    code between them mineable and exportable.\n    ',
    '`<script>` / `<style>` are raw-text elements; their bodies are code.',
    '<pre>alpha</pre> we always say the exact same thing <code>beta</code>',
    'Dropping whole messages floors at one, so the last one must be trimmed.\n\n    Otherwise a reply larger than the advertised limit is mined in full and the\n    report claims a budget it never enforced.\n    ',
    'CommonMark: a fence opened at depth 0 is not closed by a deeper line.\n\n    Stripping the blockquote prefix unconditionally made "> ```" close a fence\n    opened outside any quote, exposing the rest of the block — strictly worse\n    than before blockquote handling existed. This is the regression that shipped\n    because the blockquote tests only covered fully-quoted fences.\n    ',
    "hello there\n```\nAPI_TOKEN = 'zqxjleak'\n> ```\nDB_PASSWORD = 'zqxjleak2'\n",
    '``[label]: /path`` puts a destination where the inline form hides it.\n\n    The inline scanner only knows ``](``, so the reference form was mined and\n    persisted.\n    ',
    'morning\n\n[cfg]: /srv/lanlan/keys/SECRET_TOKEN\n',
    'Reverse anchor: the same path in prose has to stay minable.\n\n    Without this, widening ``_URL_RE`` until every bare ``/path`` is protected\n    would satisfy the test above -- a far larger change that would eat every\n    slash in ordinary speech.\n    ',
    'Markdown allows `[cfg]:/api/token`; requiring a space left it minable.',
    'morning\n\n[cfg]:/api/SECRET_TOKEN\n',
    'No outlier means dropping messages, which is the cheaper cut.\n\n    Clipping the newest whenever the budget is exceeded would shave a reply\n    that is no larger than its neighbours, losing its content for nothing --\n    the window is over budget as a whole, not because of one reply. The\n    outlier test is what keeps those two cases apart.\n    ',
    'True when ``needle`` sits inside a protected span of ``text``.',
    '"Longer than all the others combined" catches exactly one outlier.\n\n    Two comparably heavy replies evaded it, and the eviction that followed threw\n    away the ordinary replies carrying the repeated phrase -- the very defect the\n    single-outlier clip was added to stop. Dominance is measured against the\n    AVERAGE of the rest instead.\n    ',
    'Fixing only the DESTINATION left the same half-fix in the same pattern.\n\n    The label class stopped at a displayed "]", so "[cfg\\]]: /secret" was not\n    recognised as a definition at all and its destination stayed minable -- the\n    exact shape the destination alternative had already been fixed for.\n    ',
    '"${...}" ends on a single "}", including one inside a quoted literal.\n\n    The tag alternative already skips a ">" written inside quotes; the\n    interpolation did not skip a "}", so \'${"}" + "SECRET"}\' ended at the quoted\n    brace and the rest was mined. It ended there BEFORE the line fallback could\n    run, so the fallback did not catch it either.\n    ',
    '{% comment %} ',
    '``<template>`` contents are inert by definition.\n\n    The parser does not render them and nothing in a reply is speaking them, so\n    the body between the two tags is exactly what must not be mined. The generic\n    ``<...>`` pattern covered only the TAGS, which is worse than not handling it\n    because it looks handled.\n    ',
    'CommonMark allows it, and the title is the readable half of a definition.\n\n    Accepting only spaces and tabs before the title protected the destination\n    alone and left the quoted string one line down minable -- the half most\n    likely to read as speech, and the half that reaches the sidecar.\n    ',
    'Malformed, but every parser still reads it as an opener.\n\n    The quote-pairing form of the attribute run cannot match it -- there is no\n    closing quote on the line -- so without the loose fallback beside it the\n    element is not recognised at all and its body is mined. That is the\n    leaking direction, which is the one this module cannot accept.\n    ',
    'HTML start tags may split across lines and every parser reads them as one.\n\n    Rejecting the newline left the opener unrecognised, so the element was\n    never entered: only its closing tag got masked and the whole body was\n    mined.\n    ',
    'Their closers are different, so one gate cannot stand for both.\n\n    Sharing one meant a stray "#}" selected the pattern carrying the\n    uncapped "{% comment %}" alternative as well, and unpaired comment\n    openers then each scanned to end of text -- 4.27s at the input cap\n    against 0.05s. The bound here is loose on purpose: it separates\n    milliseconds from seconds.\n    ',
    '"[cfg]: #config" is a valid definition and the shape check refused it.\n\n    So the whole definition stayed minable, title included -- and the title\n    is the half most likely to read as speech.\n    ',
    'synthetic.jsonl',
    'override.jsonl',
    'first.json',
    'second.json',
    'bad.jsonl',
    'input.jsonl',
    'review.json',
    '```python',
    '```',
    'she said <code>secret token helper</code> again and again',
    '<pre><code>secret_token_helper = compute()</code></pre>',
    'she said <CODE>secret token helper</CODE> again and again',
    'she said <code>secret_token_helper = compute()',
    'she said <pre>secret_token_helper = compute()',
    '<code>aaa</code> middle text <code>secret_token_helper',
    '<script>secret_token_helper = compute()</script>',
    '<style>.secret_token_helper{color:red}</style>',
    '<script>secret_token_helper = compute()',
    '> ```',
    'she said `SECRET=1` again',
    'she said `a =\nSECRET=1` again',
    'she said ``a =\nSECRET=1`` again',
    'she said `SECRET=1',
    'she said `a =\nSECRET=1\nmore prose',
    'she said `a =\n\nSECRET=1 is prose',
    'she said `a =\n\nSECRET=1 is prose and `x` here',
    '`a` prose here `SECRET=1`',
    '```\n`SECRET=1`\n```\ntail',
    '<code>SECRET=1</code> tail',
    '<code>a <code>b</code> SECRET=1</code> tail',
    '<pre>a <pre>b</pre> SECRET=1</pre> tail',
    '<code>1<code>2<code>3</code>4</code> SECRET=1</code> tail',
    '<code>SECRET=1</code> mid <code>SECRET=2</code> tail',
    'she said <code>SECRET=1',
    '<script>SECRET=1</script> tail',
    '<style>.SECRET=1{}</style> tail',
    'a `SECRET=1` b',
    '```\nSECRET=1\n```\ntail',
    '~~~\nSECRET=1\n~~~\ntail',
    '｀｀｀\nSECRET=1\n｀｀｀\ntail',
    '```\n- ```\nDB_PASSWORD = hunter2\n```\ntail',
    '~~~\n- ~~~\nDB_PASSWORD = hunter2\n~~~\ntail',
    '~~~\n1. ~~~\nDB_PASSWORD = hunter2\n~~~\ntail',
    '~~~\n> - ~~~\nDB_PASSWORD = hunter2\n~~~\ntail',
    '｀｀｀\n- ｀｀｀\nDB_PASSWORD = hunter2\n｀｀｀\ntail',
    '{{\nsecret helper phrase\n}}',
    '${\nsecret helper phrase\n}',
    '<%\nsecret helper phrase\n%>',
    "{{ secret helper phrase\n | default('x') }}",
    '{{\nalpha\nsecret helper phrase\n}}',
    '${\nalpha\nsecret helper phrase\n}',
    '<%\nalpha\nsecret helper phrase\n%>',
    '{{\r\nsecret helper phrase\r\n}}',
    '{%\nsecret helper phrase\n%}',
    '{#\nsecret helper phrase\n#}',
    '{{ {"k": "secret helper phrase"} }}',
    '{% set c = {"k": "secret helper phrase"} %}',
    '{# {"k": "secret helper phrase"} #}',
    '那个 ${\nA呢\n我们一起去吃饭吧\nB呢\n最后那个括号 }',
    'run this `echo ```a`` export SECRET_TOKEN` ok',
    'reply `code ``inner`` SECRET_TOKEN` done',
    'reply ``code ```x``` SECRET_TOKEN`` done',
    '你好 `代码 x\n继续 ```y`` SECRET_TOKEN\n` 完毕',
    '好呀我们一起去吧 ` 真开心',
    '好呀`一起去吧`真开心',
    '好呀`我们`一起去吧',
    "it's a `great day out there friend",
    'see [endpoint](/api/SECRET_TOKEN) here',
    'see [x](/api/SECRET_TOKEN "t") here',
    '<!--\nSECRET_TOKEN should never render\n-->',
    'hello <!--\nSECRET_TOKEN\n--> bye',
    'hello <!--\nSECRET_TOKEN',
    '- >     SECRET_TOKEN = 1\n- >     more = 2',
    '> -     SECRET_TOKEN = 1',
    '-     SECRET_TOKEN = 1',
    '1.     SECRET_TOKEN = 1',
    '[我们一起去吃饭吧](/x) 好不好',
    '数组 a[0](1) 然后 我们一起去吃饭吧',
    '```\n<!-- showing this\n```\n我们一起去吃饭吧',
    '```\n<code>example\n```\n我们一起去吃饭吧',
    '```\n<script>x=1\n```\n我们一起去吃饭吧',
    '看这个 `<!--` 符号\n我们一起去吃饭吧',
    'hello <!-- <code> --> 我们一起去吃饭吧',
    'hello <!--\n<code>\n--> 我们一起去吃饭吧',
    'see [x](/api/f(1)/SECRET_TOKEN) here',
    'see [x](/api/secret(SECRET_TOKEN)) here',
    'see [x](/api/f(g(SECRET_TOKEN))) here',
    'see [x](/a(b(c(SECRET_TOKEN)))) here',
    'see [x](/api/SECRET_TOKEN\\)more) here',
    'see [x](/api/\\(SECRET_TOKEN) here',
    '<!--\nSECRET_TOKEN\n-->',
    '<code>SECRET_TOKEN</code>',
    '<code>a <code>b</code> SECRET_TOKEN</code>',
    'see https://example.com/(SECRET_TOKEN) here',
    'see https://example.com/a(SECRET_TOKEN)/b here',
    'see https://example.com/f(g(SECRET_TOKEN)) here',
    'see https://example.com/a(b(c(SECRET_TOKEN))) here',
    'see https://example.com/f(g)/SECRET_TOKEN here',
    'see https://example.com/f(g)/h(i(SECRET_TOKEN)) here',
    'see example.com/f(g(SECRET_TOKEN)) here',
    '看这个吧h.io/SECRET_TOKEN，很好玩哦',
    'ネコはneko.jp/SECRET_TOKEN',
    'see Example.com/SECRET_TOKEN here',
    'see Example.COM/SECRET_TOKEN here',
    'see EXAMPLE.COM/SECRET_TOKEN here',
    'see example.xn--p1ai/SECRET_TOKEN here',
    'see xn--fiqs8s.xn--fiqz9s/SECRET_TOKEN here',
    'see EXAMPLE.XN--P1AI/SECRET_TOKEN here',
    'see Example.CoM/SECRET_TOKEN here',
    'see example.Com/SECRET_TOKEN here',
    'see example.com?token=SECRET_TOKEN here',
    'see example.com#SECRET_TOKEN here',
    'see localhost:8080?q=SECRET_TOKEN here',
    '<textarea>SECRET_TOKEN</textarea>',
    '<textarea rows=2>SECRET_TOKEN',
    'see HTTP://Example.TEST/SECRET_TOKEN here',
    'see WWW.Example.TEST/SECRET_TOKEN here',
    'see LOCALHOST:8080/SECRET_TOKEN here',
    'see localhost:8080/SECRET_TOKEN here',
    'write to mailto:SECRET_TOKEN@example.com now',
    'write to SECRET_TOKEN@example.com now',
    '请联系SECRET_TOKEN@example.com啊',
    'see tel:+1555SECRET_TOKEN now',
    'see data:text/plain,SECRET_TOKEN now',
    'see file:///c/SECRET_TOKEN now',
    'see ftp://host/SECRET_TOKEN now',
    '请看https://a.com。我们一起去吃饭吧！',
    '看这个吧h.io/a，我们一起去吃饭吧',
    '请看https://a.com/x(然后我们一起去吃饭吧',
    '请看https://a.com/x(然后。)我们一起去吃饭吧',
    '请看https://a.com/x(然后 空格)我们一起去吃饭吧',
    '你好呀\n\n\t```\n我们一起去吃饭吧！',
    '```a`\n我们一起去吃饭吧！',
    'see this:\n\n\t- SECRET_TOKEN here\n',
    'see this:\n\n\t>SECRET_TOKEN here',
    'sure\n-     SECRET_TOKEN = 1\ndone',
    'sure\n>     SECRET_TOKEN = 1\ndone',
    'see this:\n\n  \t1. SECRET_TOKEN here',
    '- - ~~~\nSECRET_TOKEN\n~~~',
    '- 2) ~~~\nSECRET_TOKEN\n~~~',
    '```a`\n```\nSECRET_TOKEN',
    '看这个 [标签] 好呀`](`我们一起去吃饭吧)！',
    '看这个 [标签] 好呀\n```\n](\n```\n我们一起去吃饭吧)！',
    'Use `{{` repeated helper phrase `}}` in templates',
    '<code>a `<code>` x</code> 我们一起去吃饭吧',
    '<!-- alpha `-->` SECRET_TOKEN here -->',
    '<code>alpha `</code>` SECRET_TOKEN here </code>',
    '<code>\n```\n</code>\n```\nSECRET_TOKEN here</code>',
    'a.com(',
    'scan otpauth://totp/N?secret=SECRET_TOKEN now',
    'db is postgres://neko:SECRET_TOKEN@dbhost/app ok',
    'grab magnet:?xt=urn:btih:SECRET_TOKEN now',
    'open file:///C:/Users/me/SECRET_TOKEN here',
    'odd zq7+x-.foo:SECRET_TOKEN here',
    '<script>const marker = "<script>";</script> 我们一起去吃饭吧',
    '<style>a { content: "<style>"; }</style>\n\n我们一起去吃饭吧',
    '<textarea>a <textarea> b</textarea> 我们一起去吃饭吧',
    'write to mailto:ops@example.com now',
    'ops@example.com',
    '">hello</div>',
    'write to ops.secret@example.com now',
    'ops.secret',
    '${',
    '<%',
    '{{',
    '{%',
    '{#',
    '${ ',
    '<% ',
    '{{ ',
    '{% ',
    '{# ',
    '{% raw %}x{% endraw %}',
    '>> ```python',
    '>> ```',
    '>     secret_token_helper = compute()',
    '>>     secret_token_helper = compute()',
    '~~~',
    '`````',
    '- ```',
    '  ```',
    '1. ```',
    '   ```',
    '- > ~~~python',
    '  > ~~~',
    '> - ~~~python',
    '>   ~~~',
    '- > > ~~~',
    '  > > ~~~',
    '1. > ~~~',
    '   > ~~~',
    '- > - > ~~~',
    '  >   > ~~~',
    '<script>SECRET_TOKEN</script>',
    '<script>SECRET_TOKEN',
    'the config is ${ {"key": "',
    'the config is <% rate = 50% key = ',
    ' {% endcomment %}',
    '{% endcomment %}',
    '[cfg]: /api/token',
    '</template>',
    '</template></template>',
    '</code>',
    'see [the docs](https://example.com/',
    '[a[b]](https://example.com/x) ',
    'see [the `]` docs](',
    '[cfg]: /api/',
    '[a](/x) and [b](',
    '[docs](/api/[v1) then [b](',
    '[a](/x [b](/y) and [c](',
    ' the token is ${',
    'the config is ${ "',
    'the config is <% key = ',
    'the config is {% set c = {"k": "',
    '${({ k: "ok" }).k + ',
    '${ {{a}} + ',
    "{% set token = '",
    "${'}' + '",
    '[cfg]: /',
    '<template>',
    '<template><template>',
    '<code>',
    '<code>a <code>b</code> ',
    '[LAUGHS] okay](',
    '`[LAUGHS` okay](',
    '[docs](/api/[v1) okay](',
    ' {% end',
    '>inner</code> ',
    '配置是 ${name}，',
    '<div class=x> ',
    '{% comment %}',
    '${ x + ',
    '[cfg]: /api/key ',
    '<template>x</template> ',
    "${'a",
    '{%- ',
    '{%- end',
    '{% end',
    '{%-',
    '{%-end',
    '<code>x</code> ',
    '<code>a <code>b</code> c</code> ',
    '<code>y</code> ',
    '> </code>',
    '[cfg]: /api/key (a',
    '[cfg]: /api/key',
    '{# opener with no closer',
    '>y</code> ',
    '{% comment %} x',
    '[cfg]: /api/x',
    '{%- raw -%} x',
    '{%+ raw +%} x',
    '<code><code data-x=',
    '[cfg]: #config ',
)


def test_every_shape_the_span_layer_protected_still_fires():
    """No shape the eight scanners caught may fall through the detector.

    A failure here is a leak: text the old implementation kept out of the
    report AND out of the effects sidecar would now reach both.
    """
    missed = [
        shape
        for shape in _SHAPES_THE_SPAN_LAYER_PROTECTED
        if not candidate_core.contains_code_shape(shape)
    ]
    assert missed == [], "%d shape(s) lost protection: %r" % (len(missed), missed[:5])


# Ordinary speech that must NOT cost a reply its analysis. Seeded from the
# shapes actually measured in this project's stored replies: the bracketed
# timestamp prefix every row carries, kaomoji whose face parts NFKC maps onto
# code delimiters, and the emoticons the tag rule's leading-letter requirement
# exists to survive.
_SPEECH_THAT_MUST_STAY_ANALYSABLE = (
    "喵～今天也要加油哦",
    "（｀・ω・´）",
    "（*/ω＼*）",
    "～～～",
    "唔……好困",
    "<3",
    ">_<",
    "->",
    "a -> b",
    "3 < 5 且 5 > 3",
    "1/2 和 and/or",
    "2026/08/27",
    "第 1/2 章",
    "{^_^}",
    "笑死w",
    "※注意",
    "♪～",
    "[20260612 Fri 22:28] 今天也辛苦了呢",
    "[小八]:我们一起去吃饭吧！今天也辛苦了呢！",
    "[^1]: 我今天真的很开心呢",
    "[旁白] 她转过身",
)


@pytest.mark.parametrize("speech", _SPEECH_THAT_MUST_STAY_ANALYSABLE)
def test_ordinary_speech_does_not_fire(speech):
    """Over-protection is accepted, but not at the cost of ordinary speech.

    Each of these fires SOME opener-shaped rule naively read -- a fullwidth
    backtick, a tilde run, an angle bracket, a bracketed label followed by a
    colon. Measured on the real corpus, letting any of them through drops
    between 12% and 100% of replies.
    """
    assert candidate_core.contains_code_shape(speech) is False


def test_protection_is_all_or_nothing():
    """There is no partial span left to get the boundary of wrong."""
    clean = "今天天气真好呀，我们一起去公园散步吧"
    assert candidate_core._protected_spans(clean) == []
    dirty = clean + " `code` " + clean
    assert candidate_core._protected_spans(dirty) == [(0, len(dirty))]
    # The persistence path answers identically. These once differed: the
    # template placeholders were layered on for the report only, so a
    # "${...}" payload stayed out of the panel and still reached the sidecar.
    assert candidate_core._runtime_protected_spans(dirty) == [(0, len(dirty))]


def test_the_reported_shapes_that_motivated_the_rewrite_fire():
    """The three review findings the boundary-free rule closes by construction.

    Named individually so a regression points at the report it came from
    rather than at a table row.
    """
    # A raw-text whitelist of six tags did not include <noscript>.
    assert candidate_core.contains_code_shape("<noscript>secret helper phrase</noscript>")
    # A relative destination need not start with "/", "./", "../" or "#".
    assert candidate_core.contains_code_shape('[cfg]: api/key "secret helper phrase"')
    # A tag whose attributes wrap was protected by the miner and waved
    # through by the sidecar's own line-bounded copy of the rule.
    assert candidate_core.contains_code_shape('<div\n data-note="secret helper phrase">')


def test_the_detector_runs_in_linear_time():
    """The inputs that made the span layer quadratic.

    Each of these was a reported stall: a full-budget line of openers with no
    closer (30s), one closer arming three block families (7s at 112 KiB), a
    "{# #}" pair at the input cap (4.27s), and repeated "](" (seconds at
    16 KiB). Nothing looks for a closer any more, so all of them are one
    scan. The budget is loose on purpose -- it is here to catch a return to
    quadratic, not to measure the machine.
    """
    import time

    pathological = (
        "{{ " * 40000,
        "{% comment %}" * 20000 + "{% endcomment %}",
        "{#" * 40000 + "#}",
        "](" * 40000,
        "<a " * 40000,
        "`" * 2 + "x" * 80000,
    )
    for text in pathological:
        started = time.perf_counter()
        candidate_core.contains_code_shape(text)
        elapsed = time.perf_counter() - started
        assert elapsed < 2.0, "%.2fs on %r" % (elapsed, text[:24])


def test_the_occurrence_budget_keeps_the_minimum_sample():
    """Three uniformly heavy replies must not narrow to one.

    The character budget already floors at ``message_count_threshold``; the
    occurrence budget halved the WINDOW instead, and this is the path that
    actually binds -- an uninterrupted CJK reply busts the occurrence budget
    at roughly 20k characters, a sixth of the advertised character limit.

    So three replies that each carry the phrase collapsed to one, and the
    panel reported "not enough history" for a window in which every single
    message had it. Measured before the fix: analyzed_message_count 1, zero
    candidates.
    """
    phrase = "公园散步"
    messages = [
        candidate_core.SourceMessage(
            language="zh",
            content=("今天天气真好呀" * 1200) + phrase,
            source_line=index + 1,
        )
        for index in range(3)
    ]

    report = candidate_core.build_user_review_report(
        messages, message_count_threshold=3
    )

    assert report["summary"]["analyzed_message_count"] == 3
    assert report["candidates"], "a phrase in all three replies found nothing"
    # Every surviving candidate really does span the three messages, so this
    # cannot pass by loosening the threshold instead of keeping the sample.
    assert all(
        candidate["message_count"] == 3 for candidate in report["candidates"]
    )


def test_both_budgets_floor_through_one_helper():
    """The two paths drifted precisely because they were two.

    The character budget truncated every message and kept them; the
    occurrence budget sliced the list. A second spelling of "shorten
    everything, keep the count" is a second thing to forget to fix.
    """
    import inspect

    source = inspect.getsource(candidate_core.build_user_review_report)
    assert source.count("_truncate_each(") == 2, (
        "a budget stopped using the shared floor helper"
    )
    assert "analyzed[-(len(analyzed) // 2):]" in source, (
        "window halving is still the fallback ABOVE the floor, not below it"
    )


_DOTLESS_ADDRESSES_THAT_FIRE = (
    "contact user@localhost secret helper phrase",
    "邮箱是 user@localhost 哦",
    "ping admin@intranet then",
)

# The CJK spelling is deliberately NOT covered: "我@他一下" is how people say
# they will @ someone, so reading "用户@内网" as an address swallows that whole
# class of reply. Third time in this module a shape that is markup in English
# is ordinary speech in Chinese.
_AT_SIGNS_THAT_ARE_SPEECH = (
    "我@他一下",
    "用户@内网 好不好",
    "喵@喵",
    "看看 @小八 你好",
)


@pytest.mark.parametrize("text", _DOTLESS_ADDRESSES_THAT_FIRE)
def test_a_dotless_local_address_is_protected(text):
    """The URL rule requires a dot, so "user@localhost" reached the sidecar."""
    assert candidate_core.contains_code_shape(text) is True


@pytest.mark.parametrize("text", _AT_SIGNS_THAT_ARE_SPEECH)
def test_an_at_sign_in_speech_is_not_an_address(text):
    assert candidate_core.contains_code_shape(text) is False
