"""TtsBracketStripper / TtsMarkdownStripper：流式剥离行为与跨 chunk 边界。

覆盖：
- 括号：常见半角/全角类型、嵌套、跨 chunk、未闭合 flush 处理、落单 close；
  书名号 ``《》`` 豁免并透传
- markdown：bold/italic/strike/code/link/image/heading/list/quote、跨 chunk
  边界（marker 被切开时延后 emit）、pending 上限兜底、flush 残留清理
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
        # 书名号豁免：标题内容应进入 TTS
        ("看《三体》了吗", "看《三体》了吗"),
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


def test_bracket_book_title_marks_passthrough_across_chunks():
    """书名号不是 TTS 旁白括号，跨 chunk 也不能吞标题内容。"""
    s = TtsBracketStripper()
    out = _feed_chunks(s, ["看《三", "体》了吗"])
    assert out == "看《三体》了吗"


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


def test_bracket_mismatched_close_does_not_close_other_type():
    """``（旁白]继续`` 里 ``]`` 不应被当成 ``（`` 的合法闭合。

    回归 CodeRabbit Major：旧版用 depth 计数，``]`` 把 ``（`` 的 depth
    错误地减为 0，``继续`` 因此被当作括号外内容朗读出来——而旁白其实
    没有真正闭合。type-pair stack 下 ``]`` 与 top ``）`` 不配对，作为
    括号内标点随上下文一起丢，整个括号到 flush 才被清掉。
    """
    s = TtsBracketStripper()
    out = _feed_chunks(s, ["（旁白]继续"])
    # ``（`` 从未真正闭合 → 整段（含 ``继续``）都不应朗读出来
    assert out == ""


def test_bracket_mismatched_nesting_pairs_correctly():
    """嵌套不同括号类型时按 type 配对，不会因为外层 close 提前匹配内层。"""
    s = TtsBracketStripper()
    # 标准嵌套：内层 ``】`` 配对内层 ``【``，外层 ``）`` 配对外层 ``（``
    out = _feed_chunks(s, ["（中【深】中）"])
    assert out == ""
    # 反过来：外层 close 出现在内层未闭合时，作为括号内标点丢掉
    s2 = TtsBracketStripper()
    out2 = _feed_chunks(s2, ["（外【内）外】"])
    # ``）`` 与 top ``】`` 不配对 → 丢；``】`` 配对 ``【`` → pop；
    # ``（`` 仍未闭合 → flush 时清空，所有内容丢弃
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


def test_markdown_link_split_at_bracket_paren_boundary():
    """``[docs]`` / ``(url)`` 分两 chunk —— 不能让上一块就把 ``[docs]`` 提前 emit。

    回归 CodeRabbit Major：旧版 ``_safe_split`` 只在 buf 已经包含 ``](``
    时才 hold，所以这种刚好切在 ``]`` 后的 chunk 边界会让 ``[docs]``
    早 emit，下游 bracket stripper 把它当成普通方括号整段吞掉，``docs``
    根本不会朗读出来。
    """
    s = TtsMarkdownStripper()
    out = _feed_chunks(s, ["see [docs]", "(http://x) end"])
    assert out == "see docs end"


def test_markdown_link_split_immediately_after_close_bracket_not_link():
    """``]`` 在 buf 末尾、下一 chunk 不是 ``(`` —— markdown 字面透传 ``[ ]``。

    chunk1 末尾是 ``]`` 时 ``_safe_split`` 必须 hold（无法判断是否链接）；
    待 chunk2 到达确认非 ``(`` 才 emit。markdown stripper 本身不剥非链接
    的方括号，``[ref]`` 字面输出，由下游 bracket stripper 接力处理。
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
    # 链表既定设计：``[ref]`` 走 bracket 被整段吞掉，剩 ``see X end``
    assert out == "see X end"


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


# ---------------------------------------------------------------------------
# strip_tts_muted_symbols：会被念出来的符号在入队前删掉
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        # 会被念成「百分号 / 井号 / 等于 …」的符号删掉
        ("进度100%的人", "进度100的人"),
        ("标签#热门", "标签热门"),
        ("邮箱@测试", "邮箱测试"),
        ("开心/开心了", "开心开心了"),
        ("表情😀好", "表情好"),
        # 复合 emoji 的变体选择符 / 零宽连接符 / 键帽组合符不留残渣
        ("好\u2764\ufe0f呀", "好呀"),
        ("\U0001f469\u200d\U0001f4bb写代码", "写代码"),
        ("1\ufe0f\u20e3号", "1号"),
        # 天城文里正常使用的零宽连接符保留
        ("\u0915\u094d\u200d\u0937", "\u0915\u094d\u200d\u0937"),
        ("温度≈25", "温度≈25"),
        # 摄氏度 / 华氏度会被读成单位，保留
        ("温度≈25℃", "温度≈25℃"),
        ("华氏77℉", "华氏77℉"),
        # 用度数符号拼的单位（22°C / 72°F）和单独的度数同样保留
        ("气温22°C", "气温22°C"),
        ("It is 72°F today", "It is 72°F today"),
        ("转90°", "转90°"),
        # 夹在 ASCII 字母数字之间的换成空格，免得连成另一个数 / 词
        ("3~5天", "3 5天"),
        ("9/28号", "9 28号"),
        ("价格￥10$5", "价格￥10$5"),
        ("well-known", "well known"),
        ("A+B*C", "A+B C"),
        # 句读标点、撇号保留
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
        # 货币符号保留：TTS 念成「一百美元」，删掉就丢了单位
        ("$100", "$100"),
        ("€20", "€20"),
        # 数字前的负号保留，零下不能变零上；夹在数字之间的仍是连接号
        ("温度-5℃", "温度-5℃"),
        ("-5度", "-5度"),
        ("x = -3", "x = -3"),
        ("3-5天", "3 5天"),
        # 常用运算符保留，算式念得出来
        ("±5", "±5"),
        ("约≈3", "约≈3"),
        ("2+2=4", "2+2=4"),
        ("3×4", "3×4"),
        ("10÷2", "10÷2"),
        ("1≤x≥0≠2", "1≤x≥0≠2"),
        # CJK 兼容单位保留
        ("50㎡", "50㎡"),
        ("3㎏", "3㎏"),
        # C#、F# 这类名字保留「#」；其它位置的「#」照常删
        ("C++ 和 C#", "C++ 和 C#"),
        ("C#\U0001f600 dev", "C# dev"),
        ("C#-5", "C#-5"),
        # 负号前是 emoji 以外的被删符号时按连接号处理，与分块时一致
        ("3%-5", "3 5"),
        ("a\U0001f600-5", "a-5"),
        ("a#%b", "a b"),
        ("F#。", "F#。"),
        ("第#1名", "第1名"),
        ("a#b", "a b"),
        # emoji 紧贴负号时：emoji 删掉，负号留下
        ("\U0001f321\ufe0f-5°C", "-5°C"),
        ("温度\U0001f321\ufe0f-5℃", "温度-5℃"),
        ("temp\U0001f321\ufe0f-5°C", "temp-5°C"),
        # 〜（U+301C）和 ~ / ～ 一样处理
        ("好的〜", "好的"),
        ("好的～", "好的"),
        ("3〜5天", "3 5天"),
        # emoji、装饰符、箭头仍然删
        ("好的→下一步", "好的下一步"),
        ("★重点★", "重点"),
    ],
)
def test_filter_keeps_symbols_that_carry_meaning(text, expected):
    assert strip_tts_muted_symbols(text) == expected


def test_strip_tts_muted_symbols_at_chunk_edges():
    # 流式分块的首尾空格属于拼接用，不能吃掉。
    assert strip_tts_muted_symbols(" 你好#") == " 你好"
    assert strip_tts_muted_symbols("#世界 ") == "世界 "
    # 分块正好切在两个数字之间的符号上：留空格，下一块接上时不会连成一个数。
    assert strip_tts_muted_symbols("3~") + strip_tts_muted_symbols("5天") == "3 5天"
    assert strip_tts_muted_symbols("%") == ""


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


def _drain(q):
    items = []
    while not q.empty():
        items.append(q.get_nowait())
    return items


def test_enqueue_drops_spoken_symbols_and_symbol_only_chunks():
    mgr = _bare_tts_runtime()
    mgr._enqueue_tts_text_chunk("s1", "进度100%，")
    mgr._enqueue_tts_text_chunk("s1", "#")
    mgr._enqueue_tts_text_chunk("s1", "温度25℃")
    assert _drain(mgr.tts_request_queue) == [("s1", "进度100，"), ("s1", "温度25℃")]


def test_enqueue_keeps_the_gap_a_symbol_only_chunk_stood_for():
    # 流式常把「9/28」切成 "9" / "/" / "28"：中间那块删空后不能让两边连成「928」。
    # （「~」会先被 markdown 剥离器当删除线标记缓存，不走到这里。）
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
        (("3%-", "5"), [("s1", "3 "), ("s1", "5")]),
        (("C#-", "5"), [("s1", "C"), ("s1", "#-5")]),
        # Symbol-only chunks before the minus count as if unsplit.
        (("C", "#", "-", "5"), [("s1", "C"), ("s1", "#-5")]),
        (("C#", "-", "5"), [("s1", "C"), ("s1", "#-5")]),
        (("C#", "%", "-", "5"), [("s1", "C"), ("s1", " 5")]),
        (("a", "\U0001f600", "-", "5"), [("s1", "a"), ("s1", "-5")]),
        (("3", "%", "-", "5"), [("s1", "3"), ("s1", " 5")]),
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
        (("a", "#", "%", "b"), [("s1", "a"), ("s1", " b")]),
        (("a", "#", "%b"), [("s1", "a"), ("s1", " b")]),
        (("a#", "%", "b"), [("s1", "a"), ("s1", " b")]),
        (("a#", "b"), [("s1", "a"), ("s1", " b")]),
        (("C#", " dev"), [("s1", "C"), ("s1", "# dev")]),
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
    # 不经 normalizer 的流式 provider 靠独立的空格块分隔「9」「28」，不能丢。
    mgr = _bare_tts_runtime()
    for chunk in ("9", " ", "28"):
        mgr._enqueue_tts_text_chunk("s1", chunk)
    assert _drain(mgr.tts_request_queue) == [("s1", "9"), ("s1", " "), ("s1", "28")]


def test_markdown_flush_can_leave_symbol_markers_for_the_symbol_filter():
    md = TtsMarkdownStripper()
    md.feed("x~y(z")
    # 默认行为不变：所有悬挂 marker 都删。
    assert md.flush() == "yz"
    md.feed("x~y(z")
    # TTS 入队路径：括号仍删（否则会被括号剥离器吞掉后文），* _ ~ ` 留给符号过滤。
    assert md.flush(keep_symbol_markers=True) == "~yz"


def test_tilde_held_by_markdown_still_keeps_the_gap_at_turn_end():
    # "3" / "~" / "5天"：markdown 把「~5天」当删除线缓存到收尾才放出，
    # 收尾时也不能让「3」「5」连成「35」。
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
    # 复合 emoji（人物 + 零宽连接符 + 电脑）被切成「写+人物」「零宽连接符+电脑+代码」两块：
    # 后一块开头的零宽连接符不能单独送进 TTS。
    mgr = _bare_tts_runtime()
    for chunk in ("写\U0001f469", "\u200d\U0001f4bb代码"):
        mgr._enqueue_tts_text_chunk("s1", chunk)
    assert _drain(mgr.tts_request_queue) == [("s1", "写"), ("s1", "代码")]


def test_joiner_in_text_after_plain_chunk_is_kept():
    # 天城文里正常使用的零宽连接符：上一块没有以符号结尾，不能被当成残余删掉。
    mgr = _bare_tts_runtime()
    for chunk in ("\u0915\u094d", "\u200d\u0937"):
        mgr._enqueue_tts_text_chunk("s1", chunk)
    assert _drain(mgr.tts_request_queue) == [("s1", "\u0915\u094d"), ("s1", "\u200d\u0937")]


def test_joiner_after_a_non_emoji_symbol_is_kept():
    # 天城文被「%」切开：前一块以「%」结尾（不是 emoji），下一块开头的零宽连接符
    # 属于文字本身，不能当成 emoji 残余删掉；「%」本身按词间分隔换成空格。
    mgr = _bare_tts_runtime()
    for chunk in ("\u0915\u094d%", "\u200d\u0937"):
        mgr._enqueue_tts_text_chunk("s1", chunk)
    assert _drain(mgr.tts_request_queue) == [("s1", "\u0915\u094d "), ("s1", "\u200d\u0937")]


def test_trailing_symbol_between_digit_chunks_keeps_the_gap():
    # 「3%」「5」：末尾的「%」左边是数字、右边是块尾，直接换成空格，不会拼成「35」。
    mgr = _bare_tts_runtime()
    for chunk in ("3%", "5"):
        mgr._enqueue_tts_text_chunk("s1", chunk)
    assert _drain(mgr.tts_request_queue) == [("s1", "3 "), ("s1", "5")]


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        # 靠空格分词的文字（西里尔、希腊、天城文、韩文）：符号换成空格，词不粘在一起
        # 汉字、假名、泰文这类不用空格分词的文字：直接删
        ("\u043f\u043e-\u0440\u0443\u0441\u0441\u043a\u0438", "\u043f\u043e \u0440\u0443\u0441\u0441\u043a\u0438"),
        ("\u03b1/\u03b2", "\u03b1 \u03b2"),
        ("\u0938\u094c%\u0926\u094b", "\u0938\u094c \u0926\u094b"),
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
