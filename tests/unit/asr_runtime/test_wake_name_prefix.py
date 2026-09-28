"""Literal prefix boundaries for the final wake-name transcript correction."""

import pytest

from main_logic.voice_input.wake_word.transcript import correct_wake_name_prefix


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("悠宜。", "悠怡。"),
        ("友谊，帮我打开灯。", "悠怡，帮我打开灯。"),
        ("悠宜帮我打开灯。", "悠怡帮我打开灯。"),
        ("悠宜悠宜。", "悠怡悠怡。"),
        ("呦呦呦。", "悠怡悠怡。"),
        ("哟哟哟，帮我打开灯。", "悠怡悠怡，帮我打开灯。"),
        ("\t\n　“ 悠宜，帮我打开灯。”  ", "\t\n　“ 悠怡，帮我打开灯。”  "),
        ("\"悠宜\"", "\"悠怡\""),
        ("'悠宜'", "'悠怡'"),
        ("‘悠宜’", "‘悠怡’"),
        ("「悠宜」", "「悠怡」"),
        ("『悠宜』", "『悠怡』"),
        ("悠宜，解释一下友谊和悠宜。", "悠怡，解释一下友谊和悠宜。"),
    ],
)
def test_corrects_only_the_listed_leading_spelling(text, expected):
    assert correct_wake_name_prefix(text) == expected


@pytest.mark.parametrize(
    "text",
    [
        "",
        " \t\n“",
        "悠怡，帮我打开灯。",
        "我刚才喊了悠宜。",
        "这是友谊。",
        "  “我刚才喊了悠宜。”",
        "，悠宜。",
        "”悠宜。",
        "youyi，帮我打开灯。",
        "悠悠。",
        "呦呦。",
        "哟哟。",
        "悠怡，悠宜。",
    ],
)
def test_preserves_unlisted_or_interior_text(text):
    assert correct_wake_name_prefix(text) == text
