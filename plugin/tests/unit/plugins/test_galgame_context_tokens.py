from __future__ import annotations

from plugin.plugins.galgame_plugin.context_tokens import (
    count_tokens_heuristic,
    estimate_context_tokens,
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
