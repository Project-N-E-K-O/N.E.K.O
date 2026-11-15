"""TtsBracketStripper / TtsMarkdownStripper streaming stripping across chunk boundaries.

Coverage:
- Brackets: half/fullwidth types, nesting, cross-chunk, unclosed flush, unmatched close;
  book-title ``《》`` exempt and passed through
- Markdown: bold/italic/strike/code/link/image/heading/list/quote across chunks,
  deferred split-marker emission, pending cap fallback, flush residue cleanup
"""
import os
import sys

import pytest


sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "../../")))

from utils.frontend_utils import TtsBracketStripper, TtsMarkdownStripper, strip_tts_muted_symbols


# ============================================================================
# TtsBracketStripper
# ============================================================================


def _feed_chunks(stripper, chunks):
    """Simulate streaming feeds then flush, returning concatenated output."""
    out = []
    for c in chunks:
        out.append(stripper.feed(c))
    out.append(stripper.flush())
    return "".join(out)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        # Halfwidth.
        ("hello (aside) world", "hello  world"),
        # Fullwidth Chinese parentheses.
        ("她（笑了笑）说道", "她说道"),
        # Fullwidth square brackets.
        ("话题【打断】继续", "话题继续"),
        # Book-title bracket exemption: title content enters TTS.
        ("看《三体》了吗", "看《三体》了吗"),
        # Corner / double corner quotes.
        ("「话语」与『内容』", "与"),
        # Angle brackets.
        ("〈引用〉文本", "文本"),
        # Tortoise-shell brackets.
        ("〔注〕主体", "主体"),
        # Fullwidth square brackets (FF3B/FF3D).
        ("前［中间］后", "前后"),
        # Fullwidth parentheses.
        ("外（内部）外", "外外"),
        # Nested.
        ("外（中（深）中）外", "外外"),
        # Mixed-type nesting.
        ("外（中【深】中）外", "外外"),
        # Multiple separate bracket pairs.
        ("a（旁）b（白）c", "abc"),
        # No brackets → passthrough.
        ("plain text 中文", "plain text 中文"),
        # Unmatched closing bracket does not swallow content.
        ("题目 50) 三分", "题目 50) 三分"),
        # Empty string.
        ("", ""),
    ],
)
def test_bracket_one_shot(text, expected):
    s = TtsBracketStripper()
    assert _feed_chunks(s, [text]) == expected


def test_bracket_split_across_chunks():
    """``她（笑）说`` split across three chunks still discards all parenthetical content."""  # noqa: DOCSTRING_CJK
    s = TtsBracketStripper()
    out = _feed_chunks(s, ["她（", "笑", "）说"])
    assert out == "她说"


def test_bracket_book_title_marks_passthrough_across_chunks():
    """Book-title brackets are not TTS narration; never swallow titles across chunks."""
    s = TtsBracketStripper()
    out = _feed_chunks(s, ["看《三", "体》了吗"])
    assert out == "看《三体》了吗"


def test_bracket_open_only_chunk():
    """An isolated ``（`` chunk emits nothing, swallowing subsequent content until ``）``."""
    s = TtsBracketStripper()
    assert s.feed("（") == ""
    assert s.feed("旁白") == ""
    assert s.feed("）继续") == "继续"
    assert s.flush() == ""


def test_bracket_unclosed_at_flush_drops():
    """Unclosed at turn end: never restore swallowed content; flush resets state."""
    s = TtsBracketStripper()
    assert s.feed("正常") == "正常"
    assert s.feed("（未闭合") == ""
    assert s.flush() == ""
    # State reset; next feed unaffected.
    assert s.feed("新一轮") == "新一轮"


def test_bracket_reset_clears_depth():
    s = TtsBracketStripper()
    s.feed("（深陷")
    s.reset()
    assert s.feed("正常文本") == "正常文本"


def test_bracket_nested_split_across_chunks():
    """Nested brackets across chunks: ``外（中（深）``, ``中）外`` → ``外外``."""  # noqa: DOCSTRING_CJK
    s = TtsBracketStripper()
    out = _feed_chunks(s, ["外（中（深）", "中）外"])
    assert out == "外外"


def test_bracket_stray_close_emits_literal():
    """Unmatched close emits literally without affecting depth."""
    s = TtsBracketStripper()
    out = _feed_chunks(s, ["a)b)c"])
    # Retain every unmatched ), preserving content.
    assert out == "a)b)c"


def test_bracket_mismatched_close_does_not_close_other_type():
    """``（旁白]继续`` must not treat ``]`` as a valid close for ``（``.

    CodeRabbit Major regression: depth counting let ``]`` decrement ``（`` depth
    to zero, reading ``继续`` as outside text despite narration never closing.
    Type-pair stack mismatches ``]`` with top ``）``; discard as internal
    punctuation, clearing the entire bracket only on flush.
    """  # noqa: DOCSTRING_CJK
    s = TtsBracketStripper()
    out = _feed_chunks(s, ["（旁白]继续"])
    # ``（`` never actually closed; entire segment including ``继续`` must not be spoken.
    assert out == ""


def test_bracket_mismatched_nesting_pairs_correctly():
    """Mixed nested brackets match by type, never prematurely close inner with outer close."""
    s = TtsBracketStripper()
    # Normal nesting: inner ``】`` matches ``【``, outer ``）`` matches ``（``.
    out = _feed_chunks(s, ["（中【深】中）"])
    assert out == ""
    # Reverse: outer close while inner unclosed is discarded as internal punctuation.
    s2 = TtsBracketStripper()
    out2 = _feed_chunks(s2, ["（外【内）外】"])
    # ``）`` mismatches top ``】`` → discard; ``】`` matches ``【`` → pop;
    # ``（`` remains unclosed → flush clears all content.
    assert out2 == ""


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
        # Remove entire image.
        ("look ![cat](http://i/cat.png) up", "look  up"),
        # Heading at line start.
        ("# Title\nbody", "Title\nbody"),
        # blockquote
        ("> quote me\nrest", "quote me\nrest"),
        # bullet list
        ("- item one\n- item two", "item one\nitem two"),
        # numbered list
        ("1. first\n2. second", "first\nsecond"),
        # Nested Markdown: bold + italic at different positions.
        ("**bold** and *it*", "bold and it"),
        # Code fence.
        ("before\n```python\ncode here\n```\nafter", "before\n\nafter"),
        # No Markdown → passthrough.
        ("纯中文内容，无任何标记。", "纯中文内容，无任何标记。"),
        # Empty string.
        ("", ""),
    ],
)
def test_markdown_one_shot(text, expected):
    s = TtsMarkdownStripper()
    assert _feed_chunks(s, [text]) == expected


def test_markdown_underscore_in_identifier_preserved():
    """Never strip ``foo_bar`` as italic into ``foobar``."""
    s = TtsMarkdownStripper()
    out = _feed_chunks(s, ["use foo_bar variable"])
    assert out == "use foo_bar variable"


def test_markdown_underscore_emphasis_around_cjk():
    """``你_好_`` strips italic into ``你好``; CJK is not an ASCII word boundary.

    Regression: old _safe_split used ``str.isalnum()``, treating CJK as alphanumeric and opening ``_``
    as identifier; after emission _strip recognized it as marker, inconsistent semantics
    leaving literal ``你_好`` and deleting trailing ``_`` only on flush.
    """  # noqa: DOCSTRING_CJK
    s = TtsMarkdownStripper()
    out = _feed_chunks(s, ["你_好_"])
    assert out == "你好"


def test_markdown_underscore_emphasis_cjk_split_across_chunks():
    """Cross-chunk CJK italic: ``你_好_`` split into two must still strip correctly."""  # noqa: DOCSTRING_CJK
    s = TtsMarkdownStripper()
    out = _feed_chunks(s, ["你_好", "_"])
    assert out == "你好"


def test_markdown_bold_split_across_chunks():
    """``**bold**`` split midway: hold pending across marker boundaries until closed."""
    s = TtsMarkdownStripper()
    out = _feed_chunks(s, ["before **bo", "ld** after"])
    assert out == "before bold after"


def test_markdown_link_split_across_chunks():
    """``[text](url)`` split between ``](``."""
    s = TtsMarkdownStripper()
    out = _feed_chunks(s, ["see [doc", "s](http://x) end"])
    assert out == "see docs end"


def test_markdown_link_split_at_bracket_paren_boundary():
    """``[docs]`` / ``(url)`` in two chunks must not emit ``[docs]`` early.

    CodeRabbit Major regression: old ``_safe_split`` held only when buf contained ``](``,
    so chunk boundaries just after ``]`` prematurely emitted ``[docs]``;
    downstream bracket stripper swallowed it as plain brackets, making ``docs``
    never spoken.
    """
    s = TtsMarkdownStripper()
    out = _feed_chunks(s, ["see [docs]", "(http://x) end"])
    assert out == "see docs end"


def test_markdown_link_split_immediately_after_close_bracket_not_link():
    """``]`` at buffer end, next chunk not ``(``: Markdown passes literal ``[ ]``.

    ``_safe_split`` must hold trailing ``]`` until link status known;
    emit only after next chunk confirms not ``(``. Markdown does not strip nonlink
    brackets; literal ``[ref]`` passes to downstream bracket stripper.
    """
    md = TtsMarkdownStripper()
    br = TtsBracketStripper()
    chunks = ["see [ref]", "X end"]
    parts = []
    for c in chunks:
        t = md.feed(c)
        if t:
            parts.append(br.feed(t))
    parts.append(br.feed(md.flush()))
    br.flush()
    out = "".join(parts)
    # Established pipeline: ``[ref]`` consumed wholly by bracket stage, leaving ``see X end``.
    assert out == "see X end"


def test_markdown_unclosed_bold_at_flush_strips_marker():
    """Unclosed ``**foo``: flush removes ``**``, retains ``foo``."""
    s = TtsMarkdownStripper()
    out = _feed_chunks(s, ["text **未闭合内容"])
    # Strip ``**``, retain content.
    assert out == "text 未闭合内容"


def test_markdown_unclosed_link_at_flush_drops_brackets():
    """Unclosed ``[text(`` residue: flush removes isolated ``[`` ``(``, etc."""
    s = TtsMarkdownStripper()
    out = _feed_chunks(s, ["see [docs(http://x"])
    # Clear remaining isolated marker characters.
    assert "[" not in out and "(" not in out
    assert "see " in out


def test_markdown_reset_clears_pending():
    s = TtsMarkdownStripper()
    s.feed("before **half")  # Accumulate pending.
    s.reset()
    assert s.feed("clean text") == "clean text"


def test_markdown_pending_overflow_force_emit():
    """Force emit when pending reaches _MAX_PENDING, never accumulate indefinitely."""
    s = TtsMarkdownStripper()
    # Many unclosed ``*`` keep pending indefinitely.
    huge = "*" + "a" * (TtsMarkdownStripper._MAX_PENDING + 50)
    out = s.feed(huge)
    # Trigger overflow fallback, forcing emission without guaranteed complete stripping.
    assert out  # Must produce output.
    assert s._pending == ""  # Pending cleared.


def test_markdown_chained_with_bracket_link_intact():
    """After Markdown converts link to plain text, bracket stripper preserves link text.

    Simulate _enqueue_tts_text_chunk pipeline order.
    """
    md = TtsMarkdownStripper()
    br = TtsBracketStripper()
    # Link + fullwidth-parenthesis narration.
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
    # Retain link text ``文档``; never speak entire ``（旁白）`` narration.
    assert out == "看 文档 继续"


def test_markdown_chained_with_bracket_image_dropped():
    """Markdown removes all ``![alt](url)`` image content, leaving no bracket-stripper residue."""
    md = TtsMarkdownStripper()
    br = TtsBracketStripper()
    text = "前 ![喵](http://i/cat.png) 后"
    out = br.feed(md.feed(text))
    out += br.feed(md.flush())
    out += br.flush()
    assert out == "前  后"


# ---------------------------------------------------------------------------
# strip_tts_muted_symbols removes speakable symbols before enqueue.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        # Remove symbols otherwise read as percent/hash/equals, etc.
        ("进度100@的人", "进度100的人"),
        ("标签#热门", "标签热门"),
        ("邮箱@测试", "邮箱测试"),
        ("开心/开心了", "开心开心了"),
        ("表情😀好", "表情好"),
        # Compound emoji variation selectors / zero-width joiners / keycap combiners leave no residue.
        ("好\u2764\ufe0f呀", "好呀"),
        ("\U0001f469\u200d\U0001f4bb写代码", "写代码"),
        ("1\ufe0f\u20e3号", "1号"),
        # Preserve legitimate Devanagari zero-width joiners.
        ("\u0915\u094d\u200d\u0937", "\u0915\u094d\u200d\u0937"),
        ("温度≈25", "温度≈25"),
        # Celsius/Fahrenheit signs spoken as units remain.
        ("温度≈25℃", "温度≈25℃"),
        ("华氏77℉", "华氏77℉"),
        # Preserve degree-based units (22°C / 72°F) and standalone degrees too.
        ("气温22°C", "气温22°C"),
        ("It is 72°F today", "It is 72°F today"),
        ("转90°", "转90°"),
        # Replace between ASCII alphanumerics with spaces, avoiding merged numbers/words.
        ("3~5天", "3 5天"),
        ("9/28号", "9 28号"),
        ("价格￥10$5", "价格￥10$5"),
        ("well-known", "well known"),
        ("A+B*C", "A+B C"),
        # Preserve sentence punctuation/apostrophes.
        ("你好，世界！", "你好，世界！"),
        ("他说：“好的。”", "他说：“好的。”"),
        ("Hello, world! Don't.", "Hello, world! Don't."),
        ("没有符号", "没有符号"),
        ("", ""),
    ],
)
def test_strip_tts_muted_symbols(text, expected):
    assert strip_tts_muted_symbols(text) == expected


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        # Preserve currency symbols: TTS reads hundred dollars; deletion loses unit.
        ("$100", "$100"),
        ("€20", "€20"),
        # Preserve numeric minus signs, never turn below zero positive; between digits remains separator.
        ("温度-5℃", "温度-5℃"),
        ("-5度", "-5度"),
        ("x = -3", "x = -3"),
        ("3-5天", "3 5天"),
        # Preserve common arithmetic operators for spoken equations.
        ("±5", "±5"),
        ("约≈3", "约≈3"),
        ("2+2=4", "2+2=4"),
        ("3×4", "3×4"),
        ("10÷2", "10÷2"),
        ("1≤x≥0≠2", "1≤x≥0≠2"),
        # Preserve CJK compatibility units.
        ("50㎡", "50㎡"),
        ("3㎏", "3㎏"),
        # Preserve # in names C#/F#; remove elsewhere.
        ("C++ 和 C#", "C++ 和 C#"),
        ("C#\U0001f600 dev", "C# dev"),
        ("C#-5", "C#-5"),
        # Minus after removed nonemoji symbols is a separator, matching chunked behavior.
        ("3@-5", "3 5"),
        # Preserve minus if preceding symbol run does not follow alphanumerics.
        ("~-5°C", "-5°C"),
        ("温度~-5℃", "温度-5℃"),
        ("x = @-5", "x = -5"),
        ("3@" + "\U0001f600" * 40 + "-5", "3 5"),
        ("a\U0001f600-5", "a-5"),
        ("a#@b", "a b"),
        ("F#。", "F#。"),
        ("第#1名", "第1名"),
        ("a#b", "a b"),
        # Emoji adjacent to minus: remove emoji, keep minus.
        ("\U0001f321\ufe0f-5°C", "-5°C"),
        ("温度\U0001f321\ufe0f-5℃", "温度-5℃"),
        ("temp\U0001f321\ufe0f-5°C", "temp-5°C"),
        # Treat 〜 (U+301C) like ~ / ～.
        ("好的〜", "好的"),
        ("好的～", "好的"),
        ("3〜5天", "3 5天"),
        # Still remove emoji/decorations/arrows.
        ("好的→下一步", "好的下一步"),
        ("★重点★", "重点"),
        # Remove # in hashtags/ordinary words; only standalone single letter + # is a name.
        ("#ChatGPT#", " ChatGPT "),
        ("#ChatGPT#很火", " ChatGPT很火"),
        ("#AI#话题", " AI话题"),
        ("tag#热门", "tag热门"),
        ("#C#", " C "),
        ("F#语言", "F#语言"),
        ("用C#写", "用C#写"),
        # Below-zero ranges: keep second minus separated from preceding number.
        ("气温-10~-5℃", "气温-10 -5℃"),
        ("气温-10～-5℃", "气温-10 -5℃"),
        ("气温-10〜-5℃", "气温-10 -5℃"),
        ("明天-3~-1℃", "明天-3 -1℃"),
        # Comparisons: <= >= != become ≤ ≥ ≠; retain < > between operands or adjacent to =.
        ("x <= 5", "x ≤ 5"),
        ("a >= b", "a ≥ b"),
        ("a != b", "a ≠ b"),
        ("x＜＝5", "x≤5"),
        ("3<5", "3<5"),
        ("a > b", "a > b"),
        ("=>", "=>"),
        ("<提示>", "提示"),
    ],
)
def test_filter_keeps_symbols_that_carry_meaning(text, expected):
    assert strip_tts_muted_symbols(text) == expected


def test_strip_tts_muted_symbols_at_chunk_edges():
    # Leading/trailing chunk spaces enable joining and must remain.
    assert strip_tts_muted_symbols(" 你好#") == " 你好"
    assert strip_tts_muted_symbols("#世界 ") == "世界 "
    # Chunk ends on a between-number symbol: keep space so next chunk cannot merge numbers.
    assert strip_tts_muted_symbols("3~") + strip_tts_muted_symbols("5天") == "3 5天"
    assert strip_tts_muted_symbols("@") == ""


def _bare_tts_runtime():
    import queue

    from main_logic.core import LLMSessionManager
    from utils.frontend_utils import TtsStreamNormalizer

    mgr = LLMSessionManager.__new__(LLMSessionManager)
    mgr.tts_request_queue = queue.Queue()
    mgr._tts_stream_normalizer = TtsStreamNormalizer()
    mgr._tts_markdown_stripper = TtsMarkdownStripper()
    mgr._tts_bracket_stripper = TtsBracketStripper()
    mgr._tts_norm_speech_id = None
    mgr._tts_normalize_enabled = False
    mgr._remember_tts_replay_chunk = lambda *_a: None
    mgr._remember_tts_sent_chunk = lambda *_a: None
    mgr._remember_pending_ai_voice_echo = lambda *_a: None
    mgr._arm_tts_soft_flush = lambda *_a: None
    return mgr


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        # A percent sign changes the number's meaning and TTS reads it
        # correctly ("50%" is "fifty percent"), like currency: kept.
        ("降价50%", "降价50%"),
        ("100%的", "100%的"),
        ("3.5%", "3.5%"),
        ("50％的人", "50％的人"),
        # A range between percentages is a range, as between plain numbers.
        ("50%-60%", "50% 60%"),
        ("3%-5", "3% 5"),
    ],
)
def test_percent_sign_is_kept(text: str, expected: str) -> None:
    assert strip_tts_muted_symbols(text) == expected


def test_percent_range_split_across_chunks_matches_unsplit() -> None:
    mgr = _bare_tts_runtime()
    for chunk in ("50%", "-", "60%"):
        mgr._enqueue_tts_text_chunk("s1", chunk)
    assert "".join(text for _, text in _drain(mgr.tts_request_queue)) == "50% 60%"


def _drain(q):
    items = []
    while not q.empty():
        items.append(q.get_nowait())
    return items


def test_enqueue_drops_spoken_symbols_and_symbol_only_chunks():
    mgr = _bare_tts_runtime()
    mgr._enqueue_tts_text_chunk("s1", "进度100@，")
    mgr._enqueue_tts_text_chunk("s1", "#")
    mgr._enqueue_tts_text_chunk("s1", "温度25℃")
    assert _drain(mgr.tts_request_queue) == [("s1", "进度100，"), ("s1", "温度25℃")]


def test_enqueue_keeps_the_gap_a_symbol_only_chunk_stood_for():
    # Streams split 9/28 into "9" / "/" / "28"; deleting middle must never join into 928.
    # (~ is first buffered as strikethrough by Markdown stripper, bypassing this path.)
    mgr = _bare_tts_runtime()
    for chunk in ("9", "/", "28号"):
        mgr._enqueue_tts_text_chunk("s1", chunk)
    assert _drain(mgr.tts_request_queue) == [("s1", "9"), ("s1", " 28号")]


def test_symbol_only_chunk_between_cjk_adds_no_space():
    mgr = _bare_tts_runtime()
    for chunk in ("开心", "／", "开心了"):
        mgr._enqueue_tts_text_chunk("s1", chunk)
    assert _drain(mgr.tts_request_queue) == [("s1", "开心"), ("s1", "开心了")]


@pytest.mark.parametrize(
    ("chunks", "expected"),
    [
        (("温度", "-", "5℃"), [("s1", "温度"), ("s1", "-5℃")]),
        (("温度-", "5℃"), [("s1", "温度"), ("s1", "-5℃")]),
        # A minus not followed by a digit is dropped, as before.
        (("温度", "-", "很低"), [("s1", "温度"), ("s1", "很低")]),
        # Between two numbers it stays a range separator, also when the sign
        # starts the next chunk.
        (("3", "-", "5天"), [("s1", "3"), ("s1", " 5天")]),
        (("3", "-5天"), [("s1", "3"), ("s1", " 5天")]),
        (("3@-", "5"), [("s1", "3 "), ("s1", "5")]),
        (("C#-", "5"), [("s1", "C"), ("s1", "#-5")]),
        # Symbol-only chunks before the minus count as if unsplit.
        (("C", "#", "-", "5"), [("s1", "C"), ("s1", "#-5")]),
        (("C#", "-", "5"), [("s1", "C"), ("s1", "#-5")]),
        (("C#", "@", "-", "5"), [("s1", "C"), ("s1", " 5")]),
        (("温度", "@", "-", "5℃"), [("s1", "温度"), ("s1", "-5℃")]),
        (("x = ", "@-", "5"), [("s1", "x = "), ("s1", "-5")]),
        # However many symbol-only chunks pile up, the verdict matches the
        # unsplit text ("3@😀…-5" is "3 5", "a😀…-5" is "a-5").
        (("3", "@") + ("\U0001f600",) * 40 + ("-", "5"), [("s1", "3"), ("s1", " 5")]),
        (("a",) + ("\U0001f600",) * 40 + ("-", "5"), [("s1", "a"), ("s1", "-5")]),
        (("a", "\U0001f600", "-", "5"), [("s1", "a"), ("s1", "-5")]),
        (("3", "@", "-", "5"), [("s1", "3"), ("s1", " 5")]),
        (("温度\U0001f321\ufe0f-", "5℃"), [("s1", "温度"), ("s1", "-5℃")]),
        (("x = ", "-5"), [("s1", "x = "), ("s1", "-5")]),
        (("温度", "-5℃"), [("s1", "温度"), ("s1", "-5℃")]),
        # The "#" of a name split off its letter is kept ...
        (("C", "#", " developer"), [("s1", "C"), ("s1", "# developer")]),
        (("C", "#", "。"), [("s1", "C"), ("s1", "#。")]),
        (("C", "#", " ", "dev"), [("s1", "C"), ("s1", "# "), ("s1", "dev")]),
        # ... but between two letters it is still a separator.
        (("a", "#", "b"), [("s1", "a"), ("s1", " b")]),
        (("标签", "#", "热门"), [("s1", "标签"), ("s1", "热门")]),
        # Symbol-only chunks after a held "#" do not settle it; the next chunk
        # with content does, as the unsplit text would.
        (("C", "#", "\U0001f600", " dev"), [("s1", "C"), ("s1", "# dev")]),
        (("a", "#", "@", "b"), [("s1", "a"), ("s1", " b")]),
        (("a", "#", "@b"), [("s1", "a"), ("s1", " b")]),
        (("a#", "@", "b"), [("s1", "a"), ("s1", " b")]),
        (("a#", "b"), [("s1", "a"), ("s1", " b")]),
        (("C#", " dev"), [("s1", "C"), ("s1", "# dev")]),
        # A lone-letter name split off its "#" across chunks.
        (("用C", "#", "写"), [("s1", "用C"), ("s1", "#写")]),
        (("#AI", "#", "话题"), [("s1", " AI"), ("s1", "话题")]),
    ],
)
def test_minus_split_off_at_a_chunk_edge_is_reattached(chunks, expected):
    mgr = _bare_tts_runtime()
    for chunk in chunks:
        mgr._enqueue_tts_text_chunk("s1", chunk)
    assert _drain(mgr.tts_request_queue) == expected


def test_symbol_gap_does_not_leak_into_the_next_speech():
    mgr = _bare_tts_runtime()
    mgr._enqueue_tts_text_chunk("s1", "9")
    mgr._enqueue_tts_text_chunk("s1", "/")
    mgr._enqueue_tts_text_chunk("s2", "28")
    assert _drain(mgr.tts_request_queue) == [("s1", "9"), ("s2", "28")]


def test_whitespace_only_chunk_is_passed_through():
    # Providers bypassing normalizer rely on separate space chunks between 9 and 28; retain them.
    mgr = _bare_tts_runtime()
    for chunk in ("9", " ", "28"):
        mgr._enqueue_tts_text_chunk("s1", chunk)
    assert _drain(mgr.tts_request_queue) == [("s1", "9"), ("s1", " "), ("s1", "28")]


def test_markdown_flush_can_leave_symbol_markers_for_the_symbol_filter():
    md = TtsMarkdownStripper()
    md.feed("x~y(z")
    # Defaults unchanged: remove all dangling markers.
    assert md.flush() == "yz"
    md.feed("x~y(z")
    # TTS enqueue still removes brackets (or bracket stripper swallows subsequent text), leaving * _ ~ ` to symbol filtering.
    assert md.flush(keep_symbol_markers=True) == "~yz"


def test_tilde_held_by_markdown_still_keeps_the_gap_at_turn_end():
    # "3" / "~" / "5天": Markdown buffers "~5天" as strikethrough until finalization;
    # finalization must not join 3 and 5 into 35 either.
    from types import SimpleNamespace

    mgr = _bare_tts_runtime()
    mgr.tts_thread = SimpleNamespace(is_alive=lambda: True)
    mgr.tts_ready = True
    mgr.tts_pending_chunks = []
    mgr._tts_done_queued_for_turn = False
    mgr._tts_done_pending_until_ready = False
    mgr._cancel_tts_soft_flush = lambda: None
    for chunk in ("3", "~", "5天"):
        mgr._enqueue_tts_text_chunk("s1", chunk)
    assert mgr._request_tts_done_locked() == "queued"
    assert _drain(mgr.tts_request_queue) == [("s1", "3"), ("s1", " 5天"), (None, None)]


def test_soft_flush_waits_while_a_name_hash_is_unresolved():
    # A realtime provider with soft flush: "C" arms the idle timer, then "#"
    # is held. Flushing now would synthesize "C" alone and lose "C#".
    mgr = _bare_tts_runtime()
    armed: list[str] = []
    cancelled: list[bool] = []
    mgr._arm_tts_soft_flush = lambda speech_id: armed.append(speech_id)
    mgr._cancel_tts_soft_flush = lambda: cancelled.append(True)
    mgr._enqueue_tts_text_chunk("s1", "C")
    assert armed == ["s1"] and cancelled == []
    mgr._enqueue_tts_text_chunk("s1", "#")
    assert cancelled == [True]
    assert armed == ["s1"]
    mgr._enqueue_tts_text_chunk("s1", " dev")
    assert armed == ["s1", "s1"]
    assert _drain(mgr.tts_request_queue) == [("s1", "C"), ("s1", "# dev")]


@pytest.mark.parametrize(
    "chunks",
    [
        ("气温-10~-5℃",),
        ("气温-10", "~", "-5℃"),
        ("气温-10~", "-5℃"),
        ("气温-10~-", "5℃"),
    ],
)
def test_below_zero_range_keeps_both_signs_however_it_is_split(chunks):
    from types import SimpleNamespace

    mgr = _bare_tts_runtime()
    mgr.tts_thread = SimpleNamespace(is_alive=lambda: True)
    mgr.tts_ready = True
    mgr.tts_pending_chunks = []
    mgr._tts_done_queued_for_turn = False
    mgr._tts_done_pending_until_ready = False
    mgr._cancel_tts_soft_flush = lambda: None
    for chunk in chunks:
        mgr._enqueue_tts_text_chunk("s1", chunk)
    assert mgr._request_tts_done_locked() == "queued"
    items = _drain(mgr.tts_request_queue)
    assert items[-1] == (None, None)
    assert "".join(text for _sid, text in items[:-1]) == "气温-10 -5℃"


def test_held_name_hash_is_released_at_turn_end():
    # A turn that ends on "C" / "#" has no next chunk to settle the "#":
    # the turn-end flush must still send it, or "C#" is read as "C".
    from types import SimpleNamespace

    mgr = _bare_tts_runtime()
    mgr.tts_thread = SimpleNamespace(is_alive=lambda: True)
    mgr.tts_ready = True
    mgr.tts_pending_chunks = []
    mgr._tts_done_queued_for_turn = False
    mgr._tts_done_pending_until_ready = False
    mgr._cancel_tts_soft_flush = lambda: None
    for chunk in ("C", "#", "\U0001f600"):
        mgr._enqueue_tts_text_chunk("s1", chunk)
    assert mgr._request_tts_done_locked() == "queued"
    assert _drain(mgr.tts_request_queue) == [("s1", "C"), ("s1", "#"), (None, None)]


def test_name_hash_held_by_the_turn_end_markdown_flush_is_released():
    # The markdown stripper holds "*C#" (unmatched emphasis) until the turn
    # ends; filtering that tail holds its trailing "#" again, and the done
    # path must still send it before the end signal.
    from types import SimpleNamespace

    mgr = _bare_tts_runtime()
    mgr.tts_thread = SimpleNamespace(is_alive=lambda: True)
    mgr.tts_ready = True
    mgr.tts_pending_chunks = []
    mgr._tts_done_queued_for_turn = False
    mgr._tts_done_pending_until_ready = False
    mgr._cancel_tts_soft_flush = lambda: None
    mgr._enqueue_tts_text_chunk("s1", "This uses *C#")
    assert mgr._request_tts_done_locked() == "queued"
    items = _drain(mgr.tts_request_queue)
    assert items[-1] == (None, None)
    spoken = "".join(text for _sid, text in items[:-1])
    assert spoken.endswith("C#")


def test_split_compound_emoji_leaves_no_joiner_in_the_next_chunk():
    # Compound emoji (person + ZWJ + computer) splits into write+person and ZWJ+computer+code chunks:
    # next chunk's leading ZWJ must not enter TTS alone.
    mgr = _bare_tts_runtime()
    for chunk in ("写\U0001f469", "\u200d\U0001f4bb代码"):
        mgr._enqueue_tts_text_chunk("s1", chunk)
    assert _drain(mgr.tts_request_queue) == [("s1", "写"), ("s1", "代码")]


def test_joiner_in_text_after_plain_chunk_is_kept():
    # Legitimate Devanagari ZWJ: preceding chunk ended without symbols, so never treat as residue.
    mgr = _bare_tts_runtime()
    for chunk in ("\u0915\u094d", "\u200d\u0937"):
        mgr._enqueue_tts_text_chunk("s1", chunk)
    assert _drain(mgr.tts_request_queue) == [("s1", "\u0915\u094d"), ("s1", "\u200d\u0937")]


def test_joiner_after_a_non_emoji_symbol_is_kept():
    # Devanagari split by @: preceding chunk ends nonemoji @; next leading ZWJ
    # belongs to text, not emoji residue. @ itself becomes word-separating space.
    mgr = _bare_tts_runtime()
    for chunk in ("\u0915\u094d@", "\u200d\u0937"):
        mgr._enqueue_tts_text_chunk("s1", chunk)
    assert _drain(mgr.tts_request_queue) == [("s1", "\u0915\u094d "), ("s1", "\u200d\u0937")]


def test_trailing_symbol_between_digit_chunks_keeps_the_gap():
    # "3@" / "5": @ follows digit at chunk end, replaced with space, never forming 35.
    mgr = _bare_tts_runtime()
    for chunk in ("3@", "5"):
        mgr._enqueue_tts_text_chunk("s1", chunk)
    assert _drain(mgr.tts_request_queue) == [("s1", "3 "), ("s1", "5")]


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        # Space-delimited scripts (Cyrillic/Greek/Devanagari/Korean): symbols become spaces, avoiding joined words.
        # Unspaced scripts (Han/kana/Thai): delete directly.
        ("\u043f\u043e-\u0440\u0443\u0441\u0441\u043a\u0438", "\u043f\u043e \u0440\u0443\u0441\u0441\u043a\u0438"),
        ("\u03b1/\u03b2", "\u03b1 \u03b2"),
        ("\u0938\u094c@\u0926\u094b", "\u0938\u094c \u0926\u094b"),
        ("\ud55c\uad6d-\uc5b4", "\ud55c\uad6d \uc5b4"),
        ("\u0e20\u0e32\u0e29\u0e32-\u0e44\u0e17\u0e22", "\u0e20\u0e32\u0e29\u0e32\u0e44\u0e17\u0e22"),
        ("\u306d\u3053/\u3044\u306c", "\u306d\u3053\u3044\u306c"),
    ],
)
def test_symbol_between_non_ascii_words_follows_the_script(text, expected):
    assert strip_tts_muted_symbols(text) == expected


def test_symbol_only_chunk_between_cyrillic_chunks_keeps_the_gap():
    mgr = _bare_tts_runtime()
    for chunk in ("\u043f\u043e", "-", "\u0440\u0443\u0441\u0441\u043a\u0438"):
        mgr._enqueue_tts_text_chunk("s1", chunk)
    assert _drain(mgr.tts_request_queue) == [("s1", "\u043f\u043e"), ("s1", " \u0440\u0443\u0441\u0441\u043a\u0438")]
