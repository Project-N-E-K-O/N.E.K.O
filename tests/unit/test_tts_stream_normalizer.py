"""TtsStreamNormalizer: 跨 chunk 的 ASCII 空格规范化。

覆盖 Gemini Live 输出转录把中文 token 切开（"你 好 世 界"）的典型场景，
同时验证 chunk 边界（尾部空格延后决策、跨 chunk 左上下文继承）不会吃字或留字。
"""
import os
import sys

import pytest


sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "../../")))

from utils.frontend_utils import (
    TtsStreamNormalizer,
    drop_cjk_boundary_spaces,
    replace_blank,
)


# --- replace_blank 基本语义与边界安全性 --------------------------------------

@pytest.mark.parametrize(
    ("text", "expected"),
    [
        # 中文之间 / 中英交界的 ASCII 空格全部丢掉
        ("你 好 世 界", "你好世界"),
        ("hello 你好", "hello你好"),
        ("你好 world", "你好world"),
        # 纯 ASCII 词间空格保留
        ("hello world", "hello world"),
        # 边界空格（没有邻居）一律丢掉，且不能 IndexError / 负索引回绕
        (" ", ""),
        (" a", "a"),
        ("a ", "a"),
        (" hello ", "hello"),
        # 空字符串
        ("", ""),
    ],
)
def test_replace_blank(text, expected):
    assert replace_blank(text) == expected


# --- drop_cjk_boundary_spaces：只删 CJK 邻接空格，不误伤其他脚本 ------------

@pytest.mark.parametrize(
    ("text", "expected"),
    [
        # CJK 邻接空格应删
        ("你 好 世 界", "你好世界"),
        ("hello 你好", "hello你好"),
        ("你好 world", "你好world"),
        # Hiragana / Katakana 也按 CJK 处理
        ("こんにちは 世界", "こんにちは世界"),
        # Korean (Hangul) 不属于 CJK glue 范围,空格应保留
        ("안녕하세요 여러분", "안녕하세요 여러분"),
        # Cyrillic / Arabic / Thai 空格应保留
        ("Привет мир", "Привет мир"),
        ("مرحبا بالعالم", "مرحبا بالعالم"),
        ("สวัสดี ชาวโลก", "สวัสดี ชาวโลก"),
        # 纯 ASCII 词间空格保留
        ("hello world", "hello world"),
        # 边界空格 (没邻居 → 不 CJK → 保留)
        (" hello", " hello"),
        ("hello ", "hello "),
        # 中韩交界:左 CJK → 删(偏保守,Gemini artifact 场景更可能)
        ("你好 안녕", "你好안녕"),
        # 空字符串
        ("", ""),
    ],
)
def test_drop_cjk_boundary_spaces(text, expected):
    assert drop_cjk_boundary_spaces(text) == expected


# --- 单次 feed：不跨 chunk 时 ----------------------------------------------

def test_feed_single_chunk_cjk():
    n = TtsStreamNormalizer()
    assert n.feed("你 好 世 界") == "你好世界"
    n.reset()
    assert n.feed("hello world") == "hello world"


def test_feed_single_chunk_preserves_non_cjk_spaces():
    """Korean / Cyrillic / Arabic 等靠空格分词的脚本不能被动手。"""
    n = TtsStreamNormalizer()
    assert n.feed("안녕하세요 여러분") == "안녕하세요 여러분"
    n.reset()
    assert n.feed("Привет мир") == "Привет мир"
    n.reset()
    assert n.feed("مرحبا بالعالم") == "مرحبا بالعالم"


# --- chunk 边界：尾部空格延后决策 ------------------------------------------

def test_trailing_space_dropped_when_next_chunk_starts_with_chinese():
    """chunk 末尾的 ASCII 空格，若下一 chunk 以中文开头，应删除。

    Gemini Live 常见形态：["你 好", " 世", " 界"] → "你好世界"。
    """
    n = TtsStreamNormalizer()
    assert n.feed("你 好") == "你好"
    # 空格被暂存，没有立即 emit
    assert n.feed(" 世") == "世"
    assert n.feed(" 界") == "界"


def test_trailing_space_kept_when_both_sides_ascii_across_chunks():
    """chunk 边界上的空格若左右都是 ASCII 非空格，应保留。"""
    n = TtsStreamNormalizer()
    # 第一个 chunk 以 ASCII 结尾，尾部空格暂存
    assert n.feed("hello ") == "hello"
    # 下一个 chunk 以 ASCII 开头 → 空格被保留
    assert n.feed("world") == " world"


def test_leading_space_uses_previous_chunk_last_char_as_left_context():
    """下一 chunk 以空格开头时，左邻居是上一 chunk emit 的末位非空格字符。"""
    n = TtsStreamNormalizer()
    assert n.feed("你好") == "你好"
    # 左邻居是 '好'（非 ASCII），空格应删
    assert n.feed(" world") == "world"

    n.reset()
    assert n.feed("hello") == "hello"
    # 左邻居是 'o'（ASCII 非空格），右邻居 'w' 也是 → 空格保留
    assert n.feed(" world") == " world"

    n.reset()
    assert n.feed("hello") == "hello"
    # 左邻居 'o' 是 ASCII，右邻居 '你' 非 ASCII → 空格删
    assert n.feed(" 你好") == "你好"


def test_single_char_chunks_chinese():
    """极端碎片化：Gemini 有时一次就吐一个字符。"""
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
    """中英混排 + 碎片化：常见 Gemini + 英文词汇的情况。"""
    n = TtsStreamNormalizer()
    chunks = ["你", "好", " Hello", " world", " 再", "见"]
    out = "".join(n.feed(c) for c in chunks)
    # "你好" + " Hello" (左'好' 非ASCII → 删) + " world" (左'o' 右'w' 均ASCII → 留)
    # + " 再" (左'd' ASCII 右'再' 非ASCII → 删) + "见"
    assert out == "你好Hello world再见"


# --- flush 与轮次切换 ------------------------------------------------------

def test_flush_drops_trailing_spaces():
    n = TtsStreamNormalizer()
    assert n.feed("hello ") == "hello"  # 尾部空格暂存
    # 轮次结束，没有下一 chunk，空格就地丢掉
    assert n.flush() == ""


def test_reset_clears_both_pending_and_last_char():
    """reset 必须同时清空 pending_spaces 和 last_nonspace。"""
    n = TtsStreamNormalizer()
    n.feed("你好")  # last_nonspace = '好'
    n.reset()
    # last_nonspace 清空后,右邻居 'w' 非 CJK → 保留前导空格
    assert n.feed(" world") == " world"

    n2 = TtsStreamNormalizer()
    n2.feed("hello ")  # pending_spaces = " "
    n2.reset()
    # pending_spaces 清空后,不应把上轮尾空格带到新轮次
    assert n2.feed("world") == "world"


def test_cross_chunk_korean_keeps_space():
    """跨 chunk 韩语空格 regression 保护(codex review P1)。"""
    n = TtsStreamNormalizer()
    assert n.feed("안녕하세요 ") == "안녕하세요"
    # 左 '요' 右 '여' 均非 CJK → 保留空格
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
    """整个 chunk 都是空格时应全部暂存，不 emit。"""
    n = TtsStreamNormalizer()
    assert n.feed("你好") == "你好"
    assert n.feed("   ") == ""  # 全部暂存
    # 下一 chunk 中文开头 → 所有暂存空格都删
    assert n.feed("世界") == "世界"


def test_speech_id_boundary_via_reset_does_not_leak_across_turns():
    """模拟 _enqueue_tts_text_chunk 在 speech_id 切换时 reset。

    上一轮的尾部空格不应被带到下一轮的首个 chunk 里。
    """
    n = TtsStreamNormalizer()
    n.feed("hello ")  # 上一轮 trailing 暂存
    # 上一轮的尾部空格在 flush 时丢掉
    n.flush()
    # 新轮次模拟 reset
    n.reset()
    assert n.feed("world") == "world"


# --- 端到端：拼接全部 emit == 在"完整文本"上跑 drop_cjk_boundary_spaces ----

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
        # 多语言 regression 覆盖
        "안녕하세요 여러분",
        "Привет мир",
        "hello 안녕 world",
        "こんにちは 世界",
    ],
)
def test_stream_matches_whole_text_except_trailing(full_text):
    """任意切分方式下，拼接所有 emit 加上 flush 应等于对完整文本调用
    drop_cjk_boundary_spaces 的结果再去掉**尾部**空格（flush 丢尾空格是
    normalizer 的约定；首位空格若 drop_cjk_boundary_spaces 保留则继续保留）。
    """
    expected = drop_cjk_boundary_spaces(full_text).rstrip(" ")

    # 在多种切分点下都成立
    for split in range(len(full_text) + 1):
        n = TtsStreamNormalizer()
        out = n.feed(full_text[:split]) + n.feed(full_text[split:])
        out += n.flush()
        assert out == expected, (
            f"split={split} full={full_text!r} got={out!r} expected={expected!r}"
        )
