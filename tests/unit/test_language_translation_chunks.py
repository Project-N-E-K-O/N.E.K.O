from __future__ import annotations

from utils import tokenize
from utils.language_utils import _split_text_into_token_chunks


def test_translation_token_chunks_preserve_original_unicode_text() -> None:
    text = "第一段🙂こんにちは。第二段包含 emoji 🚀 and accents café. 结尾"

    chunks = _split_text_into_token_chunks(text, 12)

    assert "".join(chunks) == text
    assert len(chunks) > 1
    assert all(tokenize.count_tokens(chunk) <= 12 for chunk in chunks)


def test_translation_token_chunks_do_not_depend_on_truncated_decode(
    monkeypatch,
) -> None:
    text = "abcdefghijklmnopqrstuvwxyz"
    calls: list[str] = []

    def fake_count_tokens(value: str) -> int:
        calls.append(value)
        return len(value)

    def fail_truncate_to_tokens(*_args: object, **_kwargs: object) -> str:
        raise AssertionError("translation chunking should use source offsets")

    monkeypatch.setattr(tokenize, "count_tokens", fake_count_tokens)
    monkeypatch.setattr(tokenize, "truncate_to_tokens", fail_truncate_to_tokens)

    chunks = _split_text_into_token_chunks(text, 5)

    assert chunks == ["abcde", "fghij", "klmno", "pqrst", "uvwxy", "z"]
    assert "".join(chunks) == text
    assert calls.count(text) == 1
