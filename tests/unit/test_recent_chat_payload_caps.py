# -*- coding: utf-8 -*-
"""Tests for ``validate_chat_payload`` size caps.

背景:``/recent_file/save`` 接收用户粘贴进 recent 的整段对话,之前
``validate_chat_payload`` 只校验结构(list of {role, text:str}),对
单条 text 长度 / 总量 / 条数都没有上限。用户复制一坨长文本粘贴进来 →
原样写盘 → 后续 memory pipeline 喂给 embedder → 触发 batch padding 内
存炸点(已在 embedder 侧加 token-budget 兜底,这里加入边界硬上限,
避免异常大对象漫到 ndjson / db / recall 等所有下游)。
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
    """单条 text > 32K 字符 → 拒收。这是堵掉「粘贴一大坨」的主入口。"""
    chat = [{"role": "user", "text": "x" * (_RECENT_MESSAGE_TEXT_MAX_CHARS + 1)}]
    ok, err = validate_chat_payload(chat)
    assert not ok
    assert "单条上限" in err


def test_accepts_at_limit_single_message():
    """边界值:正好等于上限的 text 应当通过(off-by-one 守护)。"""
    chat = [{"role": "user", "text": "x" * _RECENT_MESSAGE_TEXT_MAX_CHARS}]
    ok, err = validate_chat_payload(chat)
    assert ok, err


def test_rejects_total_chars_overflow():
    """每条都不超单条上限,但累计超 2MB → 拒收(总量攻击防御)。"""
    # 单条 30K,要超过 2MB 需要 ~70 条
    per_msg = _RECENT_MESSAGE_TEXT_MAX_CHARS - 1000  # < single cap
    n = (_RECENT_CHAT_TOTAL_CHARS_MAX // per_msg) + 2  # 保证超总量
    chat = [{"role": "user", "text": "x" * per_msg} for _ in range(n)]
    ok, err = validate_chat_payload(chat)
    assert not ok
    assert "累计" in err or "总量" in err


def test_rejects_too_many_messages():
    """条数超 10000 → 拒收(冗余防御,即使每条都短)。"""
    chat = [{"role": "user", "text": "a"} for _ in range(_RECENT_CHAT_MAX_MESSAGES + 1)]
    ok, err = validate_chat_payload(chat)
    assert not ok
    assert "消息数" in err


def test_structural_validation_still_works():
    """加上限不影响原有结构校验。"""
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
    """没 text 字段的 message 应当通过(原行为)。"""
    chat = [{"role": "system"}]
    ok, err = validate_chat_payload(chat)
    assert ok, err
