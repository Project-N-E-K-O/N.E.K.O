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
