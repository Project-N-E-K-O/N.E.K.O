from __future__ import annotations

from plugin.plugins.galgame_plugin.context_tokens import (
    count_tokens_heuristic,
    estimate_context_tokens,
    truncate_tokens_heuristic,
)


def test_count_tokens_heuristic_handles_empty_text() -> None:
    assert count_tokens_heuristic("") == 0


def test_count_tokens_heuristic_counts_ascii_compactly() -> None:
    assert count_tokens_heuristic("abcd") == 1
    assert count_tokens_heuristic("a" * 100) == 25


def test_count_tokens_heuristic_counts_cjk_conservatively() -> None:
    assert count_tokens_heuristic("中文") == 3


def test_count_tokens_heuristic_counts_mixed_text() -> None:
    assert count_tokens_heuristic("abc中文") == 4


def test_estimate_context_tokens_uses_prompt_json_rendering() -> None:
    context = {"text": "中文", "items": ["abc", {"nested": "かな"}]}

    first = estimate_context_tokens(context)
    second = estimate_context_tokens(dict(context))

    assert first > count_tokens_heuristic("中文")
    assert first == second


def test_count_tokens_heuristic_handles_long_text() -> None:
    text = ("abc123" * 1000) + ("日本語" * 1000)

    assert count_tokens_heuristic(text) > count_tokens_heuristic("abc123" * 1000)


def test_truncate_tokens_heuristic_bounds_long_text_with_notice() -> None:
    text = "prefix " + ("日" * 500)

    result = truncate_tokens_heuristic(
        text,
        80,
        notice_template="\n...[truncated {omitted} chars]",
    )

    assert count_tokens_heuristic(result) <= 80
    assert result.startswith("prefix ")
    assert "...[truncated " in result


def test_truncate_tokens_heuristic_falls_back_when_notice_does_not_fit() -> None:
    text = "abcdefghijklmnopqrstuvwxyz"

    result = truncate_tokens_heuristic(
        text,
        1,
        notice_template="\n...[truncated {omitted} chars]",
    )

    assert count_tokens_heuristic(result) <= 1
    assert result == "abcd"
    assert "...[truncated " not in result
