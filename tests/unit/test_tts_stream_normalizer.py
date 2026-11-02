"""TtsStreamNormalizer: ASCII-space normalization across chunks.

Cover Gemini Live transcripts splitting Chinese tokens ("你 好 世 界"),
while verifying chunk boundaries, deferred trailing spaces, and inherited left context neither lose nor retain unwanted characters.
"""  # noqa: DOCSTRING_CJK
import os
import sys

import pytest


sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "../../")))

from utils.frontend_utils import (
    TtsStreamNormalizer,
    drop_cjk_boundary_spaces,
    replace_blank,
)


# -- replace_blank semantics and boundary safety --

@pytest.mark.parametrize(
    ("text", "expected"),
    [
        # Remove all ASCII spaces between Chinese characters or at Chinese/English boundaries.
        ("你 好 世 界", "你好世界"),
        ("hello 你好", "hello你好"),
        ("你好 world", "你好world"),
        # Preserve spaces between pure ASCII words.
        ("hello world", "hello world"),
        # Remove boundary spaces without neighbors, avoiding IndexError and negative-index wrapping.
        (" ", ""),
        (" a", "a"),
        ("a ", "a"),
        (" hello ", "hello"),
        # Empty string.
        ("", ""),
    ],
)
def test_replace_blank(text, expected):
    assert replace_blank(text) == expected


# -- drop_cjk_boundary_spaces removes CJK-adjacent spaces without affecting other scripts --

@pytest.mark.parametrize(
    ("text", "expected"),
    [
        # Remove CJK-adjacent spaces.
        ("你 好 世 界", "你好世界"),
        ("hello 你好", "hello你好"),
        ("你好 world", "你好world"),
        # Treat hiragana/katakana as CJK too.
        ("こんにちは 世界", "こんにちは世界"),
        # Korean Hangul is outside the CJK glue range; preserve spaces.
        ("안녕하세요 여러분", "안녕하세요 여러분"),
        # Preserve Cyrillic / Arabic / Thai spaces.
        ("Привет мир", "Привет мир"),
        ("مرحبا بالعالم", "مرحبا بالعالم"),
        ("สวัสดี ชาวโลก", "สวัสดี ชาวโลก"),
        # Preserve spaces between pure ASCII words.
        ("hello world", "hello world"),
        # Boundary spaces: no neighbor -> not CJK -> preserve.
        (" hello", " hello"),
        ("hello ", "hello "),
        # Chinese/Korean boundary: CJK on the left -> remove, conservatively favoring the likelier Gemini artifact case.
        ("你好 안녕", "你好안녕"),
        # Empty string.
        ("", ""),
    ],
)
def test_drop_cjk_boundary_spaces(text, expected):
    assert drop_cjk_boundary_spaces(text) == expected


# -- Single feed without chunk boundaries --

def test_feed_single_chunk_cjk():
    n = TtsStreamNormalizer()
    assert n.feed("你 好 世 界") == "你好世界"
    n.reset()
    assert n.feed("hello world") == "hello world"


def test_feed_single_chunk_preserves_non_cjk_spaces():
    """Leave space-delimited scripts such as Korean / Cyrillic / Arabic untouched."""
    n = TtsStreamNormalizer()
    assert n.feed("안녕하세요 여러분") == "안녕하세요 여러분"
    n.reset()
    assert n.feed("Привет мир") == "Привет мир"
    n.reset()
    assert n.feed("مرحبا بالعالم") == "مرحبا بالعالم"


# -- Chunk boundaries defer trailing-space decisions --

def test_trailing_space_dropped_when_next_chunk_starts_with_chinese():
    """Remove chunk-final ASCII spaces if the next chunk starts with Chinese.

    Common Gemini Live shape: ["你 好", " 世", " 界"] -> "你好世界".
    """  # noqa: DOCSTRING_CJK
    n = TtsStreamNormalizer()
    assert n.feed("你 好") == "你好"
    # Buffer spaces without immediately emitting them.
    assert n.feed(" 世") == "世"
    assert n.feed(" 界") == "界"


def test_trailing_space_kept_when_both_sides_ascii_across_chunks():
    """Preserve chunk-boundary spaces when both neighbors are nonspace ASCII."""
    n = TtsStreamNormalizer()
    # First chunk ends with ASCII; buffer trailing spaces.
    assert n.feed("hello ") == "hello"
    # Next chunk starts with ASCII -> preserve spaces.
    assert n.feed("world") == " world"


def test_leading_space_uses_previous_chunk_last_char_as_left_context():
    """When the next chunk begins with spaces, its left neighbor is the previous emission's last nonspace character."""
    n = TtsStreamNormalizer()
    assert n.feed("你好") == "你好"
    # Left neighbor '好' is non-ASCII, so remove spaces.
    assert n.feed(" world") == "world"

    n.reset()
    assert n.feed("hello") == "hello"
    # Left 'o' and right 'w' are both nonspace ASCII, so preserve spaces.
    assert n.feed(" world") == " world"

    n.reset()
    assert n.feed("hello") == "hello"
    # Left 'o' is ASCII and right '你' non-ASCII, so remove spaces.
    assert n.feed(" 你好") == "你好"


def test_single_char_chunks_chinese():
    """Extreme fragmentation: Gemini sometimes emits one character at a time."""
    n = TtsStreamNormalizer()
    out = []
    for c in "你 好 世 界":
        out.append(n.feed(c))
    assert "".join(out) == "你好世界"


def test_single_char_chunks_english():
    n = TtsStreamNormalizer()
    out = []
    for c in "hello world":
        out.append(n.feed(c))
    assert "".join(out) == "hello world"


def test_mixed_chinese_english_fragmented():
    """Mixed Chinese/English fragmentation, common with Gemini and English vocabulary."""
    n = TtsStreamNormalizer()
    chunks = ["你", "好", " Hello", " world", " 再", "见"]
    out = "".join(n.feed(c) for c in chunks)
    # "你好" + " Hello" (non-ASCII left '好' -> remove) + " world" (ASCII left 'o'/right 'w' -> preserve)
    # + " 再" (ASCII left 'd', non-ASCII right '再' -> remove) + "见".
    assert out == "你好Hello world再见"


# -- Flush and turn changes --

def test_flush_drops_trailing_spaces():
    n = TtsStreamNormalizer()
    assert n.feed("hello ") == "hello"  # Buffer trailing spaces.
    # Turn ends without another chunk; discard those spaces.
    assert n.flush() == ""


def test_reset_clears_both_pending_and_last_char():
    """reset must clear both pending_spaces and last_nonspace."""
    n = TtsStreamNormalizer()
    n.feed("你好")  # last_nonspace = '好'.
    n.reset()
    # With last_nonspace cleared, right neighbor 'w' is not CJK; preserve leading spaces.
    assert n.feed(" world") == " world"

    n2 = TtsStreamNormalizer()
    n2.feed("hello ")  # pending_spaces = " "
    n2.reset()
    # Clearing pending_spaces must prevent previous trailing spaces from entering the new turn.
    assert n2.feed("world") == "world"


def test_cross_chunk_korean_keeps_space():
    """Protect Korean spaces across chunks against regression (codex review P1)."""
    n = TtsStreamNormalizer()
    assert n.feed("안녕하세요 ") == "안녕하세요"
    # Neither left '요' nor right '여' is CJK; preserve spaces.
    assert n.feed("여러분") == " 여러분"


def test_cross_chunk_cyrillic_keeps_space():
    n = TtsStreamNormalizer()
    assert n.feed("Привет ") == "Привет"
    assert n.feed("мир") == " мир"


def test_empty_chunk_is_noop():
    n = TtsStreamNormalizer()
    assert n.feed("") == ""
    assert n.feed("你 好") == "你好"
    assert n.feed("") == ""


def test_chunk_of_only_spaces_holds_all_for_later():
    """Buffer entire whitespace-only chunks without emission."""
    n = TtsStreamNormalizer()
    assert n.feed("你好") == "你好"
    assert n.feed("   ") == ""  # Buffer everything.
    # Next chunk starts with Chinese -> discard all buffered spaces.
    assert n.feed("世界") == "世界"


def test_speech_id_boundary_via_reset_does_not_leak_across_turns():
    """Simulate _enqueue_tts_text_chunk resetting on speech_id changes.

    Previous trailing spaces must not enter the next turn's first chunk.
    """
    n = TtsStreamNormalizer()
    n.feed("hello ")  # Previous turn's trailing buffer.
    # Flush discards the previous turn's trailing spaces.
    n.flush()
    # Simulate new-turn reset.
    n.reset()
    assert n.feed("world") == "world"


# -- End-to-end: concatenated emissions equal drop_cjk_boundary_spaces on complete text --

@pytest.mark.parametrize(
    "full_text",
    [
        "你 好 世 界",
        "hello world 你好",
        "hello 你好 world",
        " 前导空格和中文",
        "尾部空格和中文 ",
        "你好     world",
        "a b c 一 二 三",
        # Multilingual regression coverage.
        "안녕하세요 여러분",
        "Привет мир",
        "hello 안녕 world",
        "こんにちは 世界",
    ],
)
def test_stream_matches_whole_text_except_trailing(full_text):
    """For arbitrary splits, concatenated emissions plus flush must equal applying
    drop_cjk_boundary_spaces to the complete text and removing trailing spaces, per the flush
    contract; preserve leading spaces whenever drop_cjk_boundary_spaces does.
    """
    expected = drop_cjk_boundary_spaces(full_text).rstrip(" ")

    # Holds for varied split points.
    for split in range(len(full_text) + 1):
        n = TtsStreamNormalizer()
        out = n.feed(full_text[:split]) + n.feed(full_text[split:])
        out += n.flush()
        assert out == expected, (
            f"split={split} full={full_text!r} got={out!r} expected={expected!r}"
        )
