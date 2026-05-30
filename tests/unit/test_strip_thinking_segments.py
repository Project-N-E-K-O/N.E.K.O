# -*- coding: utf-8 -*-
"""``strip_thinking_segments`` — defensive chain-of-thought removal for
non-streaming replies.

Background: qwen3-vl-* route reasoning to the ``reasoning_content`` field
(``content`` stays clean), but the Qwen3.5/3.6 hybrid models never populate
``reasoning_content`` over the OpenAI-compatible endpoint — the whole
chain-of-thought lands in ``content`` with only a *dangling* ``</think>`` (no
opening tag) before the real answer. A paired-tag regex can't catch that;
these cases pin the dangling-close behavior plus the well-formed and
passthrough cases.
"""
import os
import sys

import pytest

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "../../")))

from utils.llm_client import strip_thinking_segments


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        # 1) Qwen3.5 leak: implicit-open thinking + lone </think> + answer.
        ("用户让我描述图片。草稿2更准确简洁。\n</think>\n\n这张图片包含一个红色的矩形。",
         "这张图片包含一个红色的矩形。"),
        # 2) Well-formed paired block.
        ("<think>reason here</think>final answer", "final answer"),
        # 3) <thinking> long-form variant, paired.
        ("<thinking>step 1\nstep 2</thinking>\nDone.", "Done."),
        # 4) Multiple paired blocks.
        ("<think>a</think>X<think>b</think>Y", "XY"),
        # 5) Clean reply (qwen3-vl path) passes through untouched.
        ("图中左侧是一个红色矩形，右侧是一个蓝色圆形。",
         "图中左侧是一个红色矩形，右侧是一个蓝色圆形。"),
        # 6) Multiline reasoning before the dangling close (real probe shape).
        ("1. 识别主体\n2. 组织语言\n精简一下：\n</think>\n\n答案在这里", "答案在这里"),
        # 7) Case-insensitive close tag.
        ("thinking...</THINK>answer", "answer"),
        # 8) Empty / falsy inputs.
        ("", ""),
        (None, ""),
    ],
)
def test_strip(raw, expected):
    assert strip_thinking_segments(raw) == expected


def test_no_answer_after_dangling_close_yields_empty():
    """Pure-thinking reply with a trailing close tag → nothing left."""
    assert strip_thinking_segments("just reasoning, no answer\n</think>") == ""


def test_plain_text_with_no_tags_is_identity():
    txt = "这是一段普通回复，没有任何思考标签，应原样返回。"
    assert strip_thinking_segments(txt) == txt
