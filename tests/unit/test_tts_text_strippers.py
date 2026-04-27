"""TtsBracketStripper / TtsMarkdownStripper：流式剥离行为与跨 chunk 边界。

覆盖：
- 括号：所有半角/全角类型、嵌套、跨 chunk、未闭合 flush 处理、落单 close
- markdown：bold/italic/strike/code/link/image/heading/list/quote、跨 chunk
  边界（marker 被切开时延后 emit）、pending 上限兜底、flush 残留清理
"""
import os
import sys

import pytest


sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "../../")))

from utils.frontend_utils import TtsBracketStripper, TtsMarkdownStripper


# ============================================================================
# TtsBracketStripper
# ============================================================================


def _feed_chunks(stripper, chunks):
    """模拟流式输入：依次 feed，最后调 flush，返回拼接后的输出。"""
    out = []
    for c in chunks:
        out.append(stripper.feed(c))
    out.append(stripper.flush())
    return "".join(out)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        # 半角
        ("hello (aside) world", "hello  world"),
        # 全角中文括号
        ("她（笑了笑）说道", "她说道"),
        # 全角方括号
        ("话题【打断】继续", "话题继续"),
        # 全角书名号
        ("看《三体》了吗", "看了吗"),
        # 直角引号 / 双重直角引号
        ("「话语」与『内容』", "与"),
        # 角括号
        ("〈引用〉文本", "文本"),
        # 龟甲括号
        ("〔注〕主体", "主体"),
        # 全角方括号（FF3B/FF3D）
        ("前［中间］后", "前后"),
        # 全角圆括号
        ("外（内部）外", "外外"),
        # 嵌套
        ("外（中（深）中）外", "外外"),
        # 混型嵌套
        ("外（中【深】中）外", "外外"),
        # 多个独立括号
        ("a（旁）b（白）c", "abc"),
        # 没有括号 → passthrough
        ("plain text 中文", "plain text 中文"),
        # 落单的 close 不会乱吞内容
        ("题目 50) 三分", "题目 50) 三分"),
        # 空字符串
        ("", ""),
    ],
)
def test_bracket_one_shot(text, expected):
    s = TtsBracketStripper()
    assert _feed_chunks(s, [text]) == expected


def test_bracket_split_across_chunks():
    """``她（笑）说`` 拆 3 个 chunk 时，括号内容仍应整体丢弃。"""
    s = TtsBracketStripper()
    out = _feed_chunks(s, ["她（", "笑", "）说"])
    assert out == "她说"


def test_bracket_open_only_chunk():
    """单独一个 ``（`` chunk 不会 emit 任何内容；后续内容被吞直到 ``）``。"""
    s = TtsBracketStripper()
    assert s.feed("（") == ""
    assert s.feed("旁白") == ""
    assert s.feed("）继续") == "继续"
    assert s.flush() == ""


def test_bracket_unclosed_at_flush_drops():
    """轮次结束未闭合 → 已吞掉的内容不再补出来，flush 清零状态。"""
    s = TtsBracketStripper()
    assert s.feed("正常") == "正常"
    assert s.feed("（未闭合") == ""
    assert s.flush() == ""
    # 状态已清零，下一轮 feed 不受影响
    assert s.feed("新一轮") == "新一轮"


def test_bracket_reset_clears_depth():
    s = TtsBracketStripper()
    s.feed("（深陷")
    s.reset()
    assert s.feed("正常文本") == "正常文本"


def test_bracket_nested_split_across_chunks():
    """嵌套括号跨 chunk：``外（中（深）``、``中）外`` → ``外外``。"""
    s = TtsBracketStripper()
    out = _feed_chunks(s, ["外（中（深）", "中）外"])
    assert out == "外外"


def test_bracket_stray_close_emits_literal():
    """没有 open 的 close 应该按字面 emit，不污染深度状态。"""
    s = TtsBracketStripper()
    out = _feed_chunks(s, ["a)b)c"])
    # 落单 ) 全部保留，内容不变
    assert out == "a)b)c"


# ============================================================================
# TtsMarkdownStripper
# ============================================================================


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        # bold
        ("hello **world** end", "hello world end"),
        ("hello __world__ end", "hello world end"),
        # italic
        ("a *italic* b", "a italic b"),
        # strike
        ("aaa ~~bbb~~ ccc", "aaa bbb ccc"),
        # inline code
        ("var `x` = 1", "var x = 1"),
        # link
        ("see [docs](https://example.com) here", "see docs here"),
        # image 整段删
        ("look ![cat](http://i/cat.png) up", "look  up"),
        # heading 行首
        ("# Title\nbody", "Title\nbody"),
        # blockquote
        ("> quote me\nrest", "quote me\nrest"),
        # bullet list
        ("- item one\n- item two", "item one\nitem two"),
        # numbered list
        ("1. first\n2. second", "first\nsecond"),
        # 嵌套 markdown：bold + italic 在不同位置
        ("**bold** and *it*", "bold and it"),
        # 代码 fence
        ("before\n```python\ncode here\n```\nafter", "before\n\nafter"),
        # 没有 markdown → passthrough
        ("纯中文内容，无任何标记。", "纯中文内容，无任何标记。"),
        # 空字符串
        ("", ""),
    ],
)
def test_markdown_one_shot(text, expected):
    s = TtsMarkdownStripper()
    assert _feed_chunks(s, [text]) == expected


def test_markdown_underscore_in_identifier_preserved():
    """``foo_bar`` 不应被当成 italic 剥成 ``foobar``。"""
    s = TtsMarkdownStripper()
    out = _feed_chunks(s, ["use foo_bar variable"])
    assert out == "use foo_bar variable"


def test_markdown_underscore_emphasis_around_cjk():
    """``你_好_`` 应作为 italic 剥离成 ``你好``——CJK 字符不算 ASCII word boundary。

    回归：早期 _safe_split 用 ``str.isalnum()`` 把 CJK 当 alnum，结果开 ``_``
    被错当 identifier 跳过、emit 后 _strip 又认成 marker——两边语义不一致
    emphasis 漏剥（emit 出字面 ``你_好`` + 末尾 ``_`` 在 flush 删掉）。
    """
    s = TtsMarkdownStripper()
    out = _feed_chunks(s, ["你_好_"])
    assert out == "你好"


def test_markdown_underscore_emphasis_cjk_split_across_chunks():
    """跨 chunk 的 CJK italic：``你_好_`` 切两片仍要正确剥离。"""
    s = TtsMarkdownStripper()
    out = _feed_chunks(s, ["你_好", "_"])
    assert out == "你好"


def test_markdown_bold_split_across_chunks():
    """``**bold**`` 切在中间：marker 跨 chunk 时 hold pending 直到闭合。"""
    s = TtsMarkdownStripper()
    out = _feed_chunks(s, ["before **bo", "ld** after"])
    assert out == "before bold after"


def test_markdown_link_split_across_chunks():
    """``[text](url)`` 切在 ``](`` 之间。"""
    s = TtsMarkdownStripper()
    out = _feed_chunks(s, ["see [doc", "s](http://x) end"])
    assert out == "see docs end"


def test_markdown_unclosed_bold_at_flush_strips_marker():
    """未闭合的 ``**foo``：flush 时把 ``**`` 删掉，保留 ``foo``。"""
    s = TtsMarkdownStripper()
    out = _feed_chunks(s, ["text **未闭合内容"])
    # ``**`` 被删，内容保留
    assert out == "text 未闭合内容"


def test_markdown_unclosed_link_at_flush_drops_brackets():
    """未闭合的 ``[text(`` 类残骸：flush 时去掉孤立 ``[`` ``(`` 等。"""
    s = TtsMarkdownStripper()
    out = _feed_chunks(s, ["see [docs(http://x"])
    # 残留孤立 marker 字符被清掉
    assert "[" not in out and "(" not in out
    assert "see " in out


def test_markdown_reset_clears_pending():
    s = TtsMarkdownStripper()
    s.feed("before **half")  # pending 累积
    s.reset()
    assert s.feed("clean text") == "clean text"


def test_markdown_pending_overflow_force_emit():
    """pending 撑满 _MAX_PENDING 时强制 emit，不会无限累积。"""
    s = TtsMarkdownStripper()
    # 用大量未闭合 ``*`` 制造 pending 长期挂起
    huge = "*" + "a" * (TtsMarkdownStripper._MAX_PENDING + 50)
    out = s.feed(huge)
    # 触发 overflow 兜底，强制 emit（不保证 strip 干净）
    assert out  # 必须有输出
    assert s._pending == ""  # pending 被清空


def test_markdown_chained_with_bracket_link_intact():
    """链接经 markdown 剥成纯文本后，bracket stripper 不会再吃链接文本。

    模拟 _enqueue_tts_text_chunk 的串接顺序。
    """
    md = TtsMarkdownStripper()
    br = TtsBracketStripper()
    # 链接 + 全角括号旁白
    chunks = ["看 [文档](http://x) （旁", "白）继续"]
    out_parts = []
    for c in chunks:
        t = md.feed(c)
        if t:
            t = br.feed(t)
        if t:
            out_parts.append(t)
    # flush
    t = md.flush()
    if t:
        t = br.feed(t)
        if t:
            out_parts.append(t)
    br.flush()
    out = "".join(out_parts)
    # 链接文本 ``文档`` 保留，旁白 ``（旁白）`` 整段不读
    assert out == "看 文档 继续"


def test_markdown_chained_with_bracket_image_dropped():
    """图片 ``![alt](url)`` 经 markdown 整段删，bracket 不会看到任何残留。"""
    md = TtsMarkdownStripper()
    br = TtsBracketStripper()
    text = "前 ![喵](http://i/cat.png) 后"
    out = br.feed(md.feed(text))
    out += br.feed(md.flush())
    out += br.flush()
    assert out == "前  后"
