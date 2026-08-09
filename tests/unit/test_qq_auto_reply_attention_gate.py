"""焦点门控前置：非焦点群一律 block（含回复猫娘的消息），@bot 保留强制回复。

钉死 `attention_gate_service.evaluate()` 的门控顺序：
1. @bot 直接点名 → 唯一旁路，任何群都强制回复
2. 其余消息（普通 / 关键词 / 回复猫娘）→ 非焦点群 block，注意力照常累计，输出跳过原因
3. 焦点群内 → 关键词 / 回复 bot 强制回复；普通消息交 LLM 自行判断
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from plugin.plugins.qq_auto_reply.attention_gate_service import QQAttentionGateService


class _FakeAttention:
    """记录调用、返回固定焦点群与分数的最小注意力桩。"""

    def __init__(self, *, focus_group: str, enabled: bool = True, score: float = 5.0):
        self._focus = focus_group
        self._enabled_flag = enabled
        self._score = score
        self.calls: list[str] = []
        self._now = 1000

    def _enabled(self) -> bool:
        return self._enabled_flag

    def _current_time(self) -> int:
        return self._now

    def get_focus_group(self) -> str | None:
        self.calls.append("get_focus_group")
        return self._focus or None

    def get_state(self, group_id: str):
        self.calls.append("get_state")
        return SimpleNamespace(attention_score=self._score)

    def _minimum_threshold(self) -> float:
        return 1.0

    async def update_on_message(self, message: dict) -> None:
        self.calls.append(f"update_on_message:{message.get('group_id')}")

    def mark_focus(self, group_id: str) -> None:
        self.calls.append(f"mark_focus:{group_id}")

    def wake_boost(self, group_id: str) -> None:
        self.calls.append(f"wake_boost:{group_id}")


def _plugin(attention) -> SimpleNamespace:
    return SimpleNamespace(
        attention_service=attention,
        qq_client=SimpleNamespace(needs_attention=True, _sent_message_ids={}),
        permission_mgr=None,
        logger=SimpleNamespace(info=lambda *a, **k: None, warning=lambda *a, **k: None),
        _qq_settings={"backlog_labels": []},
        _emit_log=lambda *a, **k: None,
        _run_with_session_lock=None,
        reply_buffer_service=None,
        fatigue_service=None,
        session_memory_service=None,
        reply_pipeline=None,
        runtime_service=None,
        _admin_qq="0",
        _build_session_key=lambda **k: "",
    )


async def _evaluate(plugin, **kwargs) -> tuple:
    gate = QQAttentionGateService(plugin)
    default = dict(
        group_id="g1",
        sender_id="u1",
        is_at_bot=False,
        message_text="hello",
        quoted_message_id="",
        timestamp=1000,
    )
    default.update(kwargs)
    decision = await gate.evaluate(**default)
    return decision, gate


def test_non_focus_plain_message_blocked_and_attention_accumulated():
    """非焦点群的普通消息：block，但 update_on_message 照常执行（注意力累计）。"""
    attention = _FakeAttention(focus_group="g2")  # 焦点是 g2，本消息来自 g1
    plugin = _plugin(attention)

    decision, _ = asyncio.run(_evaluate(plugin, group_id="g1"))

    assert decision.action == "ignore"
    assert decision.reason.startswith("non_focus")
    assert not decision.force_reply
    # 注意力必须先累计（即使后续被 block）
    assert "update_on_message:g1" in attention.calls
    assert "mark_focus:g1" not in attention.calls  # 非焦点不抢焦点


def test_non_focus_reply_to_bot_blocked():
    """非焦点群的「回复猫娘的消息」同样 block —— 这是用户点名要求的门控。"""
    attention = _FakeAttention(focus_group="g2")
    plugin = _plugin(attention)

    decision, _ = asyncio.run(_evaluate(
        plugin,
        group_id="g1",
        quoted_message_id="m1",
        is_reply_to_bot=True,  # 连接层已判定这是回复猫娘的消息
    ))

    assert decision.action == "ignore"
    assert decision.reason.startswith("non_focus")
    assert "update_on_message:g1" in attention.calls


def test_at_bot_bypasses_focus_gate():
    """纯 @bot（未回复）直接点名 → 非焦点群也强制回复（唯一旁路）。"""
    attention = _FakeAttention(focus_group="g2")
    plugin = _plugin(attention)

    decision, _ = asyncio.run(_evaluate(plugin, group_id="g1", is_at_bot=True))

    assert decision.action == "reply"
    assert decision.force_reply is True
    assert "mark_focus:g1" in attention.calls
    assert "wake_boost:g1" in attention.calls


def test_at_and_reply_combined_non_focus_blocked():
    """消息同时带 @ 和 回复猫娘 → 按回复处理，非焦点群 block（回复优先于 @）。"""
    attention = _FakeAttention(focus_group="g2")
    plugin = _plugin(attention)

    decision, _ = asyncio.run(_evaluate(
        plugin,
        group_id="g1",
        is_at_bot=True,
        is_reply_to_bot=True,
    ))

    assert decision.action == "ignore"
    assert decision.reason.startswith("non_focus")
    # 不抢焦点（被 block）
    assert "mark_focus:g1" not in attention.calls


def test_at_and_reply_combined_focus_replies():
    """消息同时带 @ 和 回复猫娘 → 焦点群内强制回复（reason=reply_to_bot）。"""
    attention = _FakeAttention(focus_group="g1")
    plugin = _plugin(attention)

    decision, _ = asyncio.run(_evaluate(
        plugin,
        group_id="g1",
        is_at_bot=True,
        is_reply_to_bot=True,
    ))

    assert decision.action == "reply"
    assert decision.reason == "reply_to_bot"
    assert decision.force_reply is True


def test_focus_group_plain_message_passes_to_llm():
    """焦点群的普通消息：不强制回复，交给 LLM 自行判断（reason=focus_group）。"""
    attention = _FakeAttention(focus_group="g1")
    plugin = _plugin(attention)

    decision, _ = asyncio.run(_evaluate(plugin, group_id="g1"))

    assert decision.action == "reply"
    assert decision.reason == "focus_group"
    assert decision.force_reply is False


def test_focus_group_reply_to_bot_force_replies():
    """焦点群内回复猫娘的消息：强制回复。"""
    attention = _FakeAttention(focus_group="g1")
    plugin = _plugin(attention)

    decision, _ = asyncio.run(_evaluate(
        plugin,
        group_id="g1",
        quoted_message_id="m1",
        is_reply_to_bot=True,
    ))

    assert decision.action == "reply"
    assert decision.reason == "reply_to_bot"
    assert decision.force_reply is True


def test_focus_group_keyword_force_replies():
    """焦点群内命中关键词：强制回复。"""
    attention = _FakeAttention(focus_group="g1")
    plugin = _plugin(attention)
    plugin._qq_settings = {
        "backlog_labels": [{
            "id": "issue", "label": "问题",
            "keywords": ["报错"], "priority": 100,
        }],
    }

    decision, _ = asyncio.run(_evaluate(plugin, group_id="g1", message_text="有报错"))

    assert decision.action == "reply"
    assert decision.reason == "keyword:issue"
    assert decision.force_reply is True


def test_non_focus_low_attention_still_blocked():
    """非焦点群即使注意力低于阈值也 block，reason 仍是非焦点（不泄露焦点原因）。"""
    attention = _FakeAttention(focus_group="g2", score=0.5)
    plugin = _plugin(attention)

    decision, _ = asyncio.run(_evaluate(plugin, group_id="g1"))

    assert decision.action == "ignore"
    assert decision.reason.startswith("non_focus")


def test_focus_group_low_attention_blocked():
    """焦点群注意力低于最小阈值：block 并输出原因。"""
    attention = _FakeAttention(focus_group="g1", score=0.5)
    plugin = _plugin(attention)

    decision, _ = asyncio.run(_evaluate(plugin, group_id="g1"))

    assert decision.action == "ignore"
    assert decision.reason.startswith("focus_low_attention")
