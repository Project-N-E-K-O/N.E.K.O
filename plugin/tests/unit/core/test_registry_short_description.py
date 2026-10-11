from __future__ import annotations

import pytest

from plugin.core.registry import _build_plugin_meta
from utils import tokenize


pytestmark = pytest.mark.plugin_unit


@pytest.mark.parametrize("description", ["", "a" * 200, "猫" * 66, "😀" * 50, "\ud800", "  短描述  "])
def test_short_description_does_not_initialize_tokenizer(monkeypatch, description: str) -> None:
    def unexpected_encoder(*_args):
        raise AssertionError("A description within the byte budget must not load a tokenizer")

    monkeypatch.setattr(tokenize, "_get_encoder", unexpected_encoder)

    meta = _build_plugin_meta("demo", {"short_description": description})

    assert meta.short_description == description.strip()


@pytest.mark.parametrize("description", ["a" * 201, "猫" * 67, "😀" * 51, "\ud800" * 67])
def test_long_description_keeps_existing_fallback_cap(monkeypatch, description: str) -> None:
    monkeypatch.setattr(tokenize, "_get_encoder", lambda *_args: None)
    expected = tokenize.truncate_to_tokens(description, 200)

    meta = _build_plugin_meta("demo", {"short_description": description})

    assert meta.short_description == expected
    assert tokenize.count_tokens(meta.short_description) <= 200


def test_long_description_uses_exact_count_before_truncating(monkeypatch) -> None:
    description = "a" * 500
    monkeypatch.setattr(tokenize, "count_tokens", lambda text: 100)
    monkeypatch.setattr(
        tokenize, "truncate_to_tokens",
        lambda *_args: pytest.fail("A long but low-token description must remain unchanged"),
    )

    assert _build_plugin_meta("demo", {"short_description": description}).short_description == description


def test_description_over_exact_cap_is_truncated(monkeypatch) -> None:
    monkeypatch.setattr(tokenize, "count_tokens", lambda text: 201)
    monkeypatch.setattr(tokenize, "truncate_to_tokens", lambda text, limit: "capped")

    assert _build_plugin_meta("demo", {"short_description": "a" * 201}).short_description == "capped"
