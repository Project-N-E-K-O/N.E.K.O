import pytest

from plugin.plugins.qq_auto_reply.prompting import QQAutoReplyPromptingMixin


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("普通回复", "普通回复"),
        (
            "<think_never_used_51bce0c785ca2f68081bfa7d91973934></think_never_used_51bce0c785ca2f68081bfa7d91973934>我明白啦",
            "我明白啦",
        ),
        (
            "先想想\n</think_never_used_abc123>\n最终回复",
            "最终回复",
        ),
        (
            "<think>内部推理</think>对外回复",
            "对外回复",
        ),
        (
            "<thinking_trace_variant>分析</thinking_trace_variant>结论",
            "结论",
        ),
        (
            "对外回复</think_never_used_trailing>",
            "对外回复",
        ),
    ],
)
def test_sanitize_generated_reply_strips_thinking_variants(raw, expected):
    assert QQAutoReplyPromptingMixin._sanitize_generated_reply(raw) == expected
