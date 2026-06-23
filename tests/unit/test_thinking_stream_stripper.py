# -*- coding: utf-8 -*-
"""``ThinkingStreamStripper`` — streaming-safe sibling of
``strip_thinking_segments``.

Focus (thinking-on) turns stream token-by-token straight into TTS + UI, so the
Qwen3.5/3.6/3.7 hybrid leak (whole chain-of-thought dumped into ``content``
ending in a lone ``</think>``) can't wait for the non-streaming cleanup. The
stripper holds content until the first close tag, drops everything up to and
including it, then passes the real answer through. Clean providers route
reasoning to ``reasoning_content`` and never get a stripper (see
``leaks_thinking_in_content``).
"""
import os
import sys

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "../../")))

from config.providers import leaks_thinking_in_content
from utils.llm_client import ThinkingStreamStripper


def _drain(chunks):
    """Feed ``chunks`` through a fresh stripper, return (emitted_text, residual)."""
    s = ThinkingStreamStripper()
    out = "".join(s.feed(c) for c in chunks)
    return out, s.flush()


def test_dangling_leak_split_across_chunks_drops_cot():
    # CoT arrives in pieces (each held → ""), then </think>, then the answer.
    out, residual = _drain(["用户让我描述", "图片。草稿2更准确。\n", "</think>\n\n", "这张图片包含一个红色矩形。"])
    assert out == "\n\n这张图片包含一个红色矩形。"
    assert residual == ""


def test_paired_block_streamed():
    out, residual = _drain(["<think>", "reason here", "</think>", "final answer"])
    assert out == "final answer"
    assert residual == ""


def test_close_tag_split_across_chunk_boundary():
    # The close tag itself straddles two chunks — buffer must accumulate.
    out, residual = _drain(["cot here</thi", "nk>answer"])
    assert out == "answer"
    assert residual == ""


def test_answer_on_same_chunk_as_close_tag():
    out, residual = _drain(["reasoning</think>the answer"])
    assert out == "the answer"
    assert residual == ""


def test_clean_content_no_close_tag_held_until_flush():
    # Leak-prone model that didn't actually think this turn: no </think> ever
    # arrives, so content is held and returned intact at flush (never lost).
    out, residual = _drain(["你好", "呀，", "今天怎么样？"])
    assert out == ""
    assert residual == "你好呀，今天怎么样？"


def test_passthrough_is_verbatim_after_close():
    s = ThinkingStreamStripper()
    assert s.feed("cot</think>") == ""
    # Everything after the close streams through byte-for-byte, no stripping.
    assert s.feed("first ") == "first "
    assert s.feed("second") == "second"
    assert s.flush() == ""


def test_reset_rearms_for_next_segment():
    s = ThinkingStreamStripper()
    s.feed("cot</think>answer")
    s.reset()
    # After reset it buffers again until the next close tag.
    assert s.feed("more reasoning") == ""
    assert s.feed("</think>done") == "done"


def test_empty_feed_is_noop():
    s = ThinkingStreamStripper()
    assert s.feed("") == ""
    assert s.flush() == ""


def test_classifier_flags_qwen_hybrids_only():
    for leaky in ("qwen3.5-plus", "qwen3.6-flash", "qwen3.7-plus-2026-05-26",
                  "Qwen/Qwen3.5-397B-A17B", "qwen/qwen3.5-9b"):
        assert leaks_thinking_in_content(leaky) is True, leaky
    for clean in ("qwen3-vl-plus", "qwen3-vl-flash", "gpt-4o", "claude-opus-4-8",
                  "step-2-mini", "", None):
        assert leaks_thinking_in_content(clean) is False, clean
