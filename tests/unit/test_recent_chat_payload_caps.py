# -*- coding: utf-8 -*-
"""Tests for ``validate_chat_payload`` size caps.

Background: ``/recent_file/save`` accepts complete conversations pasted into recent. Previously,
``validate_chat_payload`` checked only structure (list of {role, text:str}), without limits on
individual text length, total size, or item count. A user could paste a huge text block →
write it unchanged to disk → feed the embedder through the memory pipeline → trigger batch-padding
memory exhaustion (the embedder already has a token-budget safeguard; add hard boundary caps here
to keep oversized objects from reaching ndjson / db / recall and other downstream components).
"""
from __future__ import annotations

from main_routers.memory_router import (
    _RECENT_CHAT_MAX_MESSAGES,
    _RECENT_CHAT_TOTAL_CHARS_MAX,
    _RECENT_MESSAGE_TEXT_MAX_CHARS,
    validate_chat_payload,
)


def test_normal_payload_passes():
    chat = [
        {"role": "user", "text": "你好"},
        {"role": "ai", "text": "你好,有什么可以帮你的?"},
    ]
    ok, err = validate_chat_payload(chat)
    assert ok, err


def test_rejects_oversized_single_message():
    """Reject individual text > 32K characters: the main defense against pasting a huge block."""
    chat = [{"role": "user", "text": "x" * (_RECENT_MESSAGE_TEXT_MAX_CHARS + 1)}]
    ok, err = validate_chat_payload(chat)
    assert not ok
    assert "单条上限" in err


def test_accepts_at_limit_single_message():
    """Boundary value: text exactly at the limit must pass (off-by-one guard)."""
    chat = [{"role": "user", "text": "x" * _RECENT_MESSAGE_TEXT_MAX_CHARS}]
    ok, err = validate_chat_payload(chat)
    assert ok, err


def test_rejects_total_chars_overflow():
    """Reject totals above 2MB even when each item is within its limit (aggregate-size attack defense)."""
    # At 30K per item, exceeding 2MB takes about 70 items
    per_msg = _RECENT_MESSAGE_TEXT_MAX_CHARS - 1000  # < single cap
    n = (_RECENT_CHAT_TOTAL_CHARS_MAX // per_msg) + 2  # Ensure the total limit is exceeded
    chat = [{"role": "user", "text": "x" * per_msg} for _ in range(n)]
    ok, err = validate_chat_payload(chat)
    assert not ok
    assert "累计" in err or "总量" in err


def test_rejects_too_many_messages():
    """Reject more than 10000 items (defense in depth even if each is short)."""
    chat = [{"role": "user", "text": "a"} for _ in range(_RECENT_CHAT_MAX_MESSAGES + 1)]
    ok, err = validate_chat_payload(chat)
    assert not ok
    assert "消息数" in err


def test_structural_validation_still_works():
    """Adding limits does not change existing structural validation."""
    ok, err = validate_chat_payload("not a list")
    assert not ok
    assert "列表" in err

    ok, err = validate_chat_payload([{"text": "no role"}])
    assert not ok
    assert "role" in err

    ok, err = validate_chat_payload([{"role": 123}])
    assert not ok
    assert "字符串" in err

    ok, err = validate_chat_payload([{"role": "user", "text": 123}])
    assert not ok
    assert "字符串" in err


def test_empty_payload_passes():
    ok, err = validate_chat_payload([])
    assert ok, err


def test_text_field_optional():
    """Messages without a text field must pass, preserving existing behavior."""
    chat = [{"role": "system"}]
    ok, err = validate_chat_payload(chat)
    assert ok, err
