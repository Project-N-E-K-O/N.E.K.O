# -*- coding: utf-8 -*-
from __future__ import annotations

import sys
from pathlib import Path


_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from utils.llm_tool_leak_filter import ToolLeakFilterEvent


def _drain(filter_, chunks: list[str]) -> tuple[str, list[ToolLeakFilterEvent]]:
    output: list[str] = []
    events: list[ToolLeakFilterEvent] = []
    for chunk in chunks:
        visible, event = filter_.feed(chunk)
        output.append(visible)
        if event:
            events.append(event)
    visible, event = filter_.finalize()
    output.append(visible)
    if event:
        events.append(event)
    return "".join(output), events


def test_complete_seed_tool_call_is_stripped():
    from utils.llm_tool_leak_filter import ToolLeakFilter

    visible, events = _drain(
        ToolLeakFilter(tool_names={"recall_memory"}),
        ["before <seed:tool_call><function><name>recall_memory</name></function></seed:tool_call> after"],
    )

    assert visible == "before  after"
    assert len(events) == 1
    assert events[0].pattern == "seed_tool_call"


def test_seed_tool_call_ignores_seed_close_text_inside_argument():
    from utils.llm_tool_leak_filter import ToolLeakFilter

    leak = (
        '<seed:tool_call><function><name>recall_memory</name>'
        '<parameter name="query">x </seed:tool_call> y</parameter>'
        "</function></seed:tool_call> after"
    )
    visible, events = _drain(ToolLeakFilter(tool_names={"recall_memory"}), ["before ", leak])

    assert visible == "before  after"
    assert "y</parameter>" not in visible
    assert len(events) == 1
    assert events[0].pattern == "seed_tool_call"


def test_recall_memory_tail_fragment_is_stripped_without_open_seed():
    from utils.llm_tool_leak_filter import ToolLeakFilter

    leak = 'recall_memory</name><parameter name="query" string="true">secret</parameter></function></seed:tool_call>'
    visible, events = _drain(ToolLeakFilter(tool_names={"recall_memory"}), ["before ", leak, " after"])

    assert visible == "before  after"
    assert len(events) == 1
    assert events[0].pattern == "structured_tool_call"


def test_structured_tool_call_does_not_strip_prior_tool_name_mention():
    from utils.llm_tool_leak_filter import ToolLeakFilter

    leak = 'recall_memory</name><parameter name="query">secret</parameter></function>'
    visible, events = _drain(
        ToolLeakFilter(tool_names={"recall_memory"}),
        [f"recall_memory is available; {leak} after"],
    )

    assert visible == "recall_memory is available;  after"
    assert "secret" not in visible
    assert len(events) == 1
    assert events[0].pattern == "structured_tool_call"


def test_structured_tool_call_variant_across_chunks_is_stripped():
    from utils.llm_tool_leak_filter import ToolLeakFilter

    visible, events = _drain(
        ToolLeakFilter(tool_names={"recall_memory"}),
        [
            "before ",
            'recall_memory</ name><parameter type="x" ',
            'name="query">secret</parameter></function></seed:tool_call> after',
        ],
    )

    assert visible == "before  after"
    assert "secret" not in visible
    assert len(events) == 1
    assert events[0].pattern == "structured_tool_call"
    assert events[0].cross_chunk is True


def test_structured_tool_call_closes_at_function_end_without_seed_close():
    from utils.llm_tool_leak_filter import ToolLeakFilter

    leak = 'recall_memory</name><parameter name="query">secret</parameter></function> after'
    visible, events = _drain(ToolLeakFilter(tool_names={"recall_memory"}), ["before ", leak])

    assert visible == "before  after"
    assert "secret" not in visible
    assert len(events) == 1
    assert events[0].pattern == "structured_tool_call"
    assert events[0].finalized is False


def test_structured_tool_call_ignores_seed_close_text_inside_argument():
    from utils.llm_tool_leak_filter import ToolLeakFilter

    leak = (
        'recall_memory</name><parameter name="query">'
        "x </seed:tool_call> y</parameter></function> after"
    )
    visible, events = _drain(ToolLeakFilter(tool_names={"recall_memory"}), ["before ", leak])

    assert visible == "before  after"
    assert "y</parameter>" not in visible
    assert len(events) == 1
    assert events[0].pattern == "structured_tool_call"


def test_structured_tool_call_ignores_function_close_text_inside_argument():
    from utils.llm_tool_leak_filter import ToolLeakFilter

    leak = (
        'recall_memory</name><parameter name="query">'
        "x </function> y</parameter></function> after"
    )
    visible, events = _drain(ToolLeakFilter(tool_names={"recall_memory"}), ["before ", leak])

    assert visible == "before  after"
    assert "y</parameter>" not in visible
    assert len(events) == 1
    assert events[0].pattern == "structured_tool_call"


def test_structured_tool_call_requires_current_parameter_close_before_function_close():
    from utils.llm_tool_leak_filter import ToolLeakFilter

    leak = (
        'recall_memory</name><parameter name="scope">recent</parameter>'
        '<parameter name="query">secret </function> tail</parameter></function> after'
    )
    visible, events = _drain(ToolLeakFilter(tool_names={"recall_memory"}), ["before ", leak])

    assert visible == "before  after"
    assert "tail</parameter>" not in visible
    assert len(events) == 1
    assert events[0].pattern == "structured_tool_call"


def test_structured_tool_call_treats_self_closing_parameter_as_closed():
    from utils.llm_tool_leak_filter import ToolLeakFilter

    leak = 'recall_memory</name><parameter name="query"/></function> after'
    visible, events = _drain(ToolLeakFilter(tool_names={"recall_memory"}), ["before ", leak])

    assert visible == "before  after"
    assert "<parameter" not in visible
    assert len(events) == 1
    assert events[0].pattern == "structured_tool_call"


def test_structured_tool_call_keeps_suppressing_when_seed_close_precedes_later_function_close():
    from utils.llm_tool_leak_filter import ToolLeakFilter

    visible, events = _drain(
        ToolLeakFilter(tool_names={"recall_memory"}),
        [
            "before ",
            'recall_memory</name><parameter name="query">x </seed:tool_call> y',
            "</parameter></function> after",
        ],
    )

    assert visible == "before  after"
    assert "</parameter></function>" not in visible
    assert "x </seed:tool_call> y" not in visible
    assert len(events) == 1
    assert events[0].pattern == "structured_tool_call"


def test_structured_tool_call_wins_over_inner_seed_opener():
    from utils.llm_tool_leak_filter import ToolLeakFilter

    leak = (
        'recall_memory</name><parameter name="query">'
        "x <seed:tool_call> y</parameter></function></seed:tool_call> after"
    )
    visible, events = _drain(ToolLeakFilter(tool_names={"recall_memory"}), ["before ", leak])

    assert visible == "before  after"
    assert "recall_memory</name>" not in visible
    assert "x <seed:tool_call>" not in visible
    assert len(events) == 1
    assert events[0].pattern == "structured_tool_call"


def test_structured_tool_call_split_close_preserves_following_text():
    from utils.llm_tool_leak_filter import ToolLeakFilter

    visible, events = _drain(
        ToolLeakFilter(tool_names={"recall_memory"}),
        [
            "before ",
            'recall_memory</name><parameter name="query">secret</parameter></seed:tool_',
            "call> after",
        ],
    )

    assert visible == "before  after"
    assert "secret" not in visible
    assert len(events) == 1
    assert events[0].pattern == "structured_tool_call"
    assert events[0].finalized is False


def test_structured_tool_call_split_wrapped_close_preserves_following_text():
    from utils.llm_tool_leak_filter import ToolLeakFilter

    visible, events = _drain(
        ToolLeakFilter(tool_names={"recall_memory"}),
        [
            "before ",
            'recall_memory</name><parameter name="query">secret</parameter></function></seed:tool_',
            "call> after",
        ],
    )

    assert visible == "before  after"
    assert "</seed:tool_call>" not in visible
    assert "secret" not in visible
    assert len(events) == 1
    assert events[0].pattern == "structured_tool_call"
    assert events[0].finalized is False


def test_structured_tool_call_waits_for_seed_close_after_function_boundary():
    from utils.llm_tool_leak_filter import ToolLeakFilter

    visible, events = _drain(
        ToolLeakFilter(tool_names={"recall_memory"}),
        [
            "before ",
            'recall_memory</name><parameter name="query">secret</parameter></function>',
            "</seed:tool_call> after",
        ],
    )

    assert visible == "before  after"
    assert "</seed:tool_call>" not in visible
    assert "secret" not in visible
    assert len(events) == 1
    assert events[0].pattern == "structured_tool_call"
    assert events[0].finalized is False


def test_structured_tool_call_consumes_formatted_seed_close_after_function():
    from utils.llm_tool_leak_filter import ToolLeakFilter

    visible, events = _drain(
        ToolLeakFilter(tool_names={"recall_memory"}),
        [
            "before ",
            'recall_memory</name><parameter name="query">secret</parameter></function>',
            "\n </seed:tool_call> after",
        ],
    )

    assert visible == "before  after"
    assert "</seed:tool_call>" not in visible
    assert "secret" not in visible
    assert len(events) == 1
    assert events[0].pattern == "structured_tool_call"
    assert events[0].finalized is False


def test_structured_tool_call_keeps_function_close_pending_after_whitespace():
    from utils.llm_tool_leak_filter import ToolLeakFilter

    visible, events = _drain(
        ToolLeakFilter(tool_names={"recall_memory"}),
        [
            "before ",
            'recall_memory</name><parameter name="query">secret</parameter></function> ',
            "after",
        ],
    )

    assert visible == "before  after"
    assert "secret" not in visible
    assert len(events) == 1
    assert events[0].pattern == "structured_tool_call"
    assert events[0].finalized is False


def test_structured_tool_call_tracks_split_parameter_close_before_seed_close():
    from utils.llm_tool_leak_filter import ToolLeakFilter

    visible, events = _drain(
        ToolLeakFilter(tool_names={"recall_memory"}),
        [
            "before ",
            'recall_memory</name><parameter name="query">secret</p',
            "arameter></function><",
            "/seed:tool_call> after",
        ],
    )

    assert visible == "before  after"
    assert "</seed:tool_call>" not in visible
    assert "secret" not in visible
    assert len(events) == 1
    assert events[0].pattern == "structured_tool_call"
    assert events[0].finalized is False


def test_structured_tool_call_standalone_function_close_waits_for_seed_close():
    from utils.llm_tool_leak_filter import ToolLeakFilter

    visible, events = _drain(
        ToolLeakFilter(tool_names={"recall_memory"}),
        [
            "before ",
            'recall_memory</name><parameter name="query">secret</parameter>',
            "</function>",
            "</seed:tool_call> after",
        ],
    )

    assert visible == "before  after"
    assert "</seed:tool_call>" not in visible
    assert "secret" not in visible
    assert len(events) == 1
    assert events[0].pattern == "structured_tool_call"
    assert events[0].finalized is False


def test_structured_tool_call_strips_function_name_opener():
    from utils.llm_tool_leak_filter import ToolLeakFilter

    leak = '<function><name>recall_memory</name><parameter name="query">secret</parameter></function> after'
    visible, events = _drain(ToolLeakFilter(tool_names={"recall_memory"}), ["before ", leak])

    assert visible == "before  after"
    assert "<function><name>" not in visible
    assert "secret" not in visible
    assert len(events) == 1
    assert events[0].pattern == "structured_tool_call"


def test_structured_tool_call_strips_attributed_function_name_opener():
    from utils.llm_tool_leak_filter import ToolLeakFilter

    leak = (
        '<function><name type="x">recall_memory</name>'
        '<parameter name="query">secret</parameter></function> after'
    )
    visible, events = _drain(ToolLeakFilter(tool_names={"recall_memory"}), ["before ", leak])

    assert visible == "before  after"
    assert '<function><name type="x">' not in visible
    assert "secret" not in visible
    assert len(events) == 1
    assert events[0].pattern == "structured_tool_call"


def test_structured_tool_call_opener_prefix_across_chunks_is_stripped():
    from utils.llm_tool_leak_filter import ToolLeakFilter

    visible, events = _drain(
        ToolLeakFilter(tool_names={"recall_memory"}),
        [
            "before <function><name>rec",
            'all_memory</name><parameter name="query">secret</parameter></function> after',
        ],
    )

    assert visible == "before  after"
    assert "<function><name>" not in visible
    assert "secret" not in visible
    assert len(events) == 1
    assert events[0].pattern == "structured_tool_call"


def test_structured_tool_call_uppercase_tool_name_prefix_across_chunks_is_stripped():
    from utils.llm_tool_leak_filter import ToolLeakFilter

    visible, events = _drain(
        ToolLeakFilter(tool_names={"MyTool"}),
        [
            "before <function><name>My",
            'Tool</name><parameter name="query">secret</parameter></function> after',
        ],
    )

    assert visible == "before  after"
    assert "<function><name>" not in visible
    assert "secret" not in visible
    assert len(events) == 1
    assert events[0].pattern == "structured_tool_call"


def test_structured_zero_arg_tool_split_function_close_is_stripped():
    from utils.llm_tool_leak_filter import ToolLeakFilter

    visible, events = _drain(
        ToolLeakFilter(tool_names={"sts2_get_status"}),
        ["before ", "sts2_get_status</name></fun", "ction> after"],
    )

    assert visible == "before  after"
    assert "sts2_get_status" not in visible
    assert len(events) == 1
    assert events[0].pattern == "structured_tool_call"


def test_seed_marker_across_chunks_is_stripped():
    from utils.llm_tool_leak_filter import ToolLeakFilter

    visible, events = _drain(
        ToolLeakFilter(tool_names={"recall_memory"}),
        ["before <seed:tool_", "call>secret</seed:tool_call> after"],
    )

    assert visible == "before  after"
    assert len(events) == 1
    assert events[0].cross_chunk is True


def test_whitespace_tolerant_seed_marker_across_chunks_is_stripped():
    from utils.llm_tool_leak_filter import ToolLeakFilter

    visible, events = _drain(
        ToolLeakFilter(tool_names={"recall_memory"}),
        ["before <seed: ", "tool_call>secret</seed:tool_call> after"],
    )

    assert visible == "before  after"
    assert "secret" not in visible
    assert len(events) == 1
    assert events[0].pattern == "seed_tool_call"
    assert events[0].cross_chunk is True


def test_suppressed_long_arguments_are_not_output():
    from utils.llm_tool_leak_filter import ToolLeakFilter

    secret = "x" * 5000
    visible, events = _drain(
        ToolLeakFilter(tool_names={"recall_memory"}),
        ["ok ", "<seed:tool_call>", secret, "</seed:tool_call>", " done"],
    )

    assert visible == "ok  done"
    assert secret not in visible
    assert events[0].chars >= len(secret)


def test_unclosed_seed_fragment_is_dropped_on_finalize():
    from utils.llm_tool_leak_filter import ToolLeakFilter

    visible, events = _drain(
        ToolLeakFilter(tool_names={"recall_memory"}),
        ["before ", "<seed:tool_call>secret"],
    )

    assert visible == "before "
    assert len(events) == 1
    assert events[0].finalized is True


def test_normal_xml_html_and_code_examples_are_preserved():
    from utils.llm_tool_leak_filter import ToolLeakFilter

    text = '<div data-x="1">ok</div>\n```xml\n<function><name>demo</name></function>\n```'
    visible, events = _drain(ToolLeakFilter(tool_names={"recall_memory"}), [text])

    assert visible == text
    assert events == []


def test_tool_call_markup_inside_code_fence_is_replaced_not_revealed():
    from utils.llm_tool_leak_filter import ToolLeakFilter

    text = "```xml\n<seed:tool_call>secret query</seed:tool_call>\n```"
    visible, events = _drain(ToolLeakFilter(tool_names={"recall_memory"}), [text])

    assert visible == "```xml\n[tool-call markup omitted]\n```"
    assert "secret query" not in visible
    assert len(events) == 1
    assert events[0].pattern == "seed_tool_call"


def test_structured_tool_call_inside_code_fence_is_replaced_not_revealed():
    from utils.llm_tool_leak_filter import ToolLeakFilter

    text = (
        "```xml\n"
        'recall_memory</name><parameter name="query">secret query</parameter></function></seed:tool_call>\n'
        "```"
    )
    visible, events = _drain(ToolLeakFilter(tool_names={"recall_memory"}), [text])

    assert visible == "```xml\n[tool-call markup omitted]\n```"
    assert "secret query" not in visible
    assert "recall_memory</name>" not in visible
    assert len(events) == 1
    assert events[0].pattern == "structured_tool_call"


def test_lonely_tool_name_close_is_preserved():
    from utils.llm_tool_leak_filter import ToolLeakFilter

    text = "这里是代码示例：recall_memory</name>"
    visible, events = _drain(ToolLeakFilter(tool_names={"recall_memory"}), [text])

    assert visible == text
    assert events == []


def test_no_seed_requires_registered_tool_and_strong_structure():
    from utils.llm_tool_leak_filter import ToolLeakFilter

    leak = 'unknown_tool</name><parameter name="query">secret</parameter></function></seed:tool_call>'
    visible, events = _drain(ToolLeakFilter(tool_names={"recall_memory"}), [leak])

    assert "unknown_tool" in visible
    assert events == []


def test_event_metadata_does_not_include_raw_text_or_query():
    from utils.llm_tool_leak_filter import ToolLeakFilter

    query = "secret query"
    _visible, events = _drain(
        ToolLeakFilter(tool_names={"recall_memory"}),
        [f'recall_memory</name><parameter name="query">{query}</parameter></function></seed:tool_call>'],
    )

    event_text = repr(events[0])
    assert query not in event_text
    assert "parameter" not in event_text


# ── Calls written into the reply as text (inline tool-call syntax) ──────────

_PVZ_TOOLS = {"pvz_instruction", "pvz_start", "recall_memory"}

# (leaked text, what the user should see). Taken from replies that reached TTS:
# the native tool calls had stopped and the model wrote them into the reply.
_INLINE_CALL_CASES = [
    (
        "好的！declaration:default_api:pvz_instruction{instruction:立刻在第四和第五行交界处使用樱桃炸弹，…}",
        "好的！",
    ),
    (
        "冲呀asynccall:pvz_instruction{instruction:种豌豆}asynccall:pvz_instruction{instruction:种向日葵}"
        "asynccall:pvz_instruction{instruction:补坚果}看我的",
        "冲呀看我的",
    ),
    ("我来pvz_instruction(instruction='第三行种坚果')。", "我来。"),
    ("先开局 default_api:pvz_start{goal:通关}，然后", "先开局 ，然后"),
    ('好 default_api.pvz_start(goal="win") 走起', "好  走起"),
    ('收到pvz_instruction{"instruction": "铲掉第一行"}喵', "收到喵"),
]


def _split_everywhere(text: str):
    for cut in range(len(text) + 1):
        yield [text[:cut], text[cut:]]
    for first in range(0, len(text) + 1, 3):
        for second in range(first, len(text) + 1, 5):
            yield [text[:first], text[first:second], text[second:]]


def test_inline_tool_calls_are_stripped_whole():
    from utils.llm_tool_leak_filter import ToolLeakFilter

    for leaked, expected in _INLINE_CALL_CASES:
        visible, events = _drain(ToolLeakFilter(tool_names=_PVZ_TOOLS), [leaked])
        assert visible == expected, leaked
        assert events and {e.pattern for e in events} == {"inline_tool_call"}
        assert not any(e.finalized for e in events), "every call closed on its bracket"


def test_inline_tool_calls_are_stripped_at_any_chunk_split():
    from utils.llm_tool_leak_filter import ToolLeakFilter

    for leaked, expected in _INLINE_CALL_CASES:
        for chunks in _split_everywhere(leaked):
            visible, _events = _drain(ToolLeakFilter(tool_names=_PVZ_TOOLS), chunks)
            assert visible == expected, chunks


def test_inline_call_split_marks_the_event_cross_chunk():
    from utils.llm_tool_leak_filter import ToolLeakFilter

    visible, events = _drain(
        ToolLeakFilter(tool_names=_PVZ_TOOLS),
        ["好的 async", "call:pvz_instruction{instruction:种豌豆} 完毕"],
    )
    assert visible == "好的  完毕"
    assert len(events) == 1 and events[0].cross_chunk is True


def test_inline_call_brackets_nest_and_quoted_closers_do_not_end_it():
    from utils.llm_tool_leak_filter import ToolLeakFilter

    leaked = (
        'A pvz_instruction{"instruction": "a } b ) c", "plan": {"rows": [1, [2, 3]], '
        '"note": "say \\"hi\\" }"}} B'
    )
    for chunks in _split_everywhere(leaked):
        visible, events = _drain(ToolLeakFilter(tool_names=_PVZ_TOOLS), chunks)
        assert visible == "A  B", chunks
        assert len(events) == 1 and events[0].finalized is False


def test_an_apostrophe_inside_an_unquoted_value_does_not_swallow_the_reply():
    from utils.llm_tool_leak_filter import ToolLeakFilter

    visible, _events = _drain(
        ToolLeakFilter(tool_names=_PVZ_TOOLS),
        ["pvz_instruction{instruction: don't plant here} and that's all"],
    )
    assert visible == " and that's all"


def test_an_opener_left_unclosed_inside_a_value_ends_at_the_outer_closer():
    """Values are natural language: a half-written ``(`` or ``[`` in one must
    not keep the call open and take the rest of the reply with it."""
    from utils.llm_tool_leak_filter import ToolLeakFilter, strip_tool_call_leaks

    for leaked in (
        "好呀 asynccall:pvz_instruction{instruction: 在第3行(靠左放坚果} 接下来我们继续玩吧！",
        "好呀 pvz_instruction(instruction=[第3行放坚果) 接下来我们继续玩吧！",
        "好呀 asynccall:pvz_instruction{instruction: 好难 :( 先种坚果} 接下来我们继续玩吧！",
    ):
        assert strip_tool_call_leaks(leaked, tool_names=_PVZ_TOOLS) == "好呀  接下来我们继续玩吧！"
        for chunks in _split_everywhere(leaked):
            visible, _events = _drain(ToolLeakFilter(tool_names=_PVZ_TOOLS), chunks)
            assert visible == "好呀  接下来我们继续玩吧！", chunks


def test_finished_text_without_any_opener_marker_skips_the_scan(monkeypatch):
    """The finished-text helpers run on the event loop: a reply with no marker
    and no tool name never reaches the per-position scan."""
    from utils import llm_tool_leak_filter as module

    def no_scan(*_args, **_kwargs):
        raise AssertionError("scanned a reply that holds no opener")

    monkeypatch.setattr(module.ToolLeakFilter, "feed", no_scan)
    plain = "今天天气不错，我们去公园散步吧。" * 50
    assert module.strip_tool_call_leaks(plain, tool_names=_PVZ_TOOLS) == plain
    assert module.strip_tool_call_leaks_from_parts([plain, plain], tool_names=_PVZ_TOOLS) == [plain, plain]


def test_a_call_that_never_closes_gives_back_the_reply_after_its_outer_closer():
    """An unclosed quote or a same-kind opener left open in a value keeps the
    call open to the end; the reply after its last outer closer still shows
    (and is still stored)."""
    from utils.llm_tool_leak_filter import ToolLeakFilter, strip_tool_call_leaks

    for leaked, expected in (
        ('default_api:pvz_start{goal:"do it} Done.', " Done."),
        ("好呀 pvz_instruction(instruction=在第3行(靠左放坚果) 接下来我们继续玩吧！", "好呀  接下来我们继续玩吧！"),
        ("好 asynccall:pvz_instruction{instruction:'种坚果} 好了 asynccall:pvz_start{goal:a} 完", "好  好了  完"),
    ):
        assert strip_tool_call_leaks(leaked, tool_names=_PVZ_TOOLS) == expected
        for chunks in _split_everywhere(leaked):
            visible, events = _drain(ToolLeakFilter(tool_names=_PVZ_TOOLS), chunks)
            assert visible == expected, chunks
            assert events and events[0].finalized is True


def test_many_calls_that_never_close_are_recovered_without_recursion():
    """Each recovered stretch can hold another unclosed call; finalize walks
    them in a loop."""
    from utils.llm_tool_leak_filter import ToolLeakFilter, strip_tool_call_leaks

    leaked = "开始 " + "pvz_start(goal=(b) " * 1500 + "结束"
    assert strip_tool_call_leaks(leaked, tool_names=_PVZ_TOOLS).split() == ["开始", "结束"]
    visible, events = _drain(ToolLeakFilter(tool_names=_PVZ_TOOLS), [leaked])
    assert visible.split() == ["开始", "结束"]
    assert events and events[-1].finalized is True


def test_a_call_left_open_long_after_its_outer_closer_is_given_up_there():
    """Past the recovery window the reply after the outer closer comes back
    at once (streamed, not held to the end), and what follows is read for
    further calls."""
    from utils.llm_tool_leak_filter import ToolLeakFilter

    tail = "后面的正文很长。" * 100
    leaked = f'前 default_api:pvz_start{{goal:"do it}}{tail}asynccall:pvz_start{{goal:a}}完'
    leak_filter = ToolLeakFilter(tool_names=_PVZ_TOOLS)
    streamed, _event = leak_filter.feed(leaked)
    assert streamed.startswith("前 " + tail), "given back while streaming, not held to the end"
    visible, events = _drain(ToolLeakFilter(tool_names=_PVZ_TOOLS), [leaked])
    assert visible == "前 " + tail + "完"


def test_finished_text_helpers_read_tool_names_once():
    """``tool_names`` may be a one-shot iterable: the pre-check must not use
    it up before the filter reads it."""
    from utils.llm_tool_leak_filter import strip_tool_call_leaks, strip_tool_call_leaks_from_parts

    bare = "我来pvz_instruction(instruction='第三行种坚果')。"
    assert strip_tool_call_leaks(bare, tool_names=iter(["pvz_instruction"])) == "我来。"
    assert strip_tool_call_leaks_from_parts([bare], tool_names=iter(["pvz_instruction"])) == ["我来。"]


def test_every_prefixed_opener_has_a_pre_check_marker():
    from utils.llm_tool_leak_filter import _OPENER_MARKERS, _PREFIXED_CALL_OPENERS

    for steps in _PREFIXED_CALL_OPENERS:
        assert steps[0][1] in _OPENER_MARKERS


def test_an_unclosed_inline_call_is_dropped_on_finalize():
    from utils.llm_tool_leak_filter import ToolLeakFilter

    visible, events = _drain(
        ToolLeakFilter(tool_names=_PVZ_TOOLS),
        ["我来 asynccall:pvz_instruction{instruction:在第四行", "种樱桃"],
    )
    assert visible == "我来 "
    assert len(events) == 1 and events[0].finalized is True


def test_mentions_and_narration_of_tools_are_not_inline_calls():
    """Only call syntax is stripped: talking about a tool, a bare ``name()``,
    an unregistered ``name(x=1)``, a prefix glued to a longer word and the
    full-width narration some models write are all kept as is."""
    from utils.llm_tool_leak_filter import ToolLeakFilter

    for text in [
        "我刚用 pvz_instruction 下了指令，pvz_start 之后再说。",
        "pvz_start() 不带参数也能开局",
        "plant(row=3) 是我瞎编的函数名，unknown_tool{x: 1} 也是",
        "（调用工具pvz_start，目标：先通关第一关）",
        "（调用工具 pvz_instruction，指令：种坚果）",
        "my_pvz_instruction(instruction=1) 和 xasynccall:foo{a:1} 都不是",
        "the default value is fine, a default_api is just a word here",
        "pvz_instruction（instruction=全角括号不算）",
    ]:
        for chunks in _split_everywhere(text):
            visible, events = _drain(ToolLeakFilter(tool_names=_PVZ_TOOLS), chunks)
            assert visible == text, chunks
            assert events == []


def test_bare_tool_name_calls_need_a_registered_name():
    from utils.llm_tool_leak_filter import ToolLeakFilter

    text = "我来pvz_instruction(instruction='第三行种坚果')。"
    visible, events = _drain(ToolLeakFilter(tool_names={"recall_memory"}), [text])
    assert visible == text and events == []


def test_inline_call_inside_code_fence_is_replaced_not_revealed():
    from utils.llm_tool_leak_filter import ToolLeakFilter

    text = "```\nasynccall:pvz_instruction{instruction:secret plan}\n```"
    visible, events = _drain(ToolLeakFilter(tool_names=_PVZ_TOOLS), [text])
    assert visible == "```\n[tool-call markup omitted]\n```"
    assert "secret plan" not in visible
    assert len(events) == 1 and events[0].pattern == "inline_tool_call"


def test_strip_tool_call_leaks_without_tool_names_keeps_to_prefixed_syntax():
    """Memory has no tool registry: only the forms that are call syntax
    whatever the tool are removed there."""
    from utils.llm_tool_leak_filter import strip_tool_call_leaks

    assert strip_tool_call_leaks(
        "好的！declaration:default_api:pvz_instruction{instruction:丢樱桃炸弹}"
    ) == "好的！"
    assert strip_tool_call_leaks(
        "冲asynccall:pvz_instruction{instruction:a}asynccall:pvz_instruction{instruction:b}"
    ) == "冲"
    bare = "我来pvz_instruction(instruction='第三行种坚果')。"
    assert strip_tool_call_leaks(bare) == bare
    assert strip_tool_call_leaks(bare, tool_names={"pvz_instruction"}) == "我来。"
    assert strip_tool_call_leaks("") == ""
    assert strip_tool_call_leaks("（调用工具pvz_start，目标：…）") == "（调用工具pvz_start，目标：…）"


def test_text_parts_with_nothing_cut_keep_their_boundaries():
    """Holding back a possible opener at a part's end must not move text
    between parts when no call follows."""
    from utils.llm_tool_leak_filter import strip_tool_call_leaks_from_parts

    for parts in (["I have a", " plan"], ["the default", "_value"], ["好的 async", "hronous 也行"]):
        assert strip_tool_call_leaks_from_parts(parts) == parts
    assert strip_tool_call_leaks_from_parts(
        ["好的 async", "call:pvz_instruction{instruction:a} 完毕"],
    ) == ["好的 ", " 完毕"]
    # Held back but no call: it stays in its own part even when a later
    # part does hold one.
    assert strip_tool_call_leaks_from_parts(
        ["alpha default_", "nothing asynccall:x{a:1} omega"],
    ) == ["alpha default_", "nothing  omega"]
    assert strip_tool_call_leaks_from_parts(
        ["前面 async", "后面 asynccall:x{a:1} 完"],
    ) == ["前面 async", "后面  完"]
