"""Focus-first gating: non-focus groups are blocked (including reply-to-bot), @bot keeps force reply.

Pins the gating order of `attention_gate_service.evaluate()`:
1. @bot direct mention -> only bypass, force-replies in any group
2. Other messages (plain / keyword / reply-to-bot) -> non-focus groups are blocked,
   attention still accumulates, skip reason is logged
3. Inside the focus group -> keyword / reply-to-bot force reply; plain messages go to the LLM
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from plugin.plugins.qq_auto_reply.attention_gate_service import QQAttentionGateService


class _FakeAttention:
    """Minimal attention stub recording calls and returning a fixed focus group and score."""

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
    """A plain message in a non-focus group is blocked, but attention still accumulates."""
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
    """A reply-to-bot message in a non-focus group is also blocked (user-requested gating)."""
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
    """A pure @bot (no reply) force-replies even in a non-focus group (only bypass)."""
    attention = _FakeAttention(focus_group="g2")
    plugin = _plugin(attention)

    decision, _ = asyncio.run(_evaluate(plugin, group_id="g1", is_at_bot=True))

    assert decision.action == "reply"
    assert decision.force_reply is True
    assert "mark_focus:g1" in attention.calls
    assert "wake_boost:g1" in attention.calls


def test_at_and_reply_combined_non_focus_blocked():
    """A message with both @ and reply-to-bot is treated as a reply and blocked in non-focus groups."""
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
    """A message with both @ and reply-to-bot force-replies inside the focus group."""
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
    """A plain message in the focus group is not force-replied; the LLM decides (reason=focus_group)."""
    attention = _FakeAttention(focus_group="g1")
    plugin = _plugin(attention)

    decision, _ = asyncio.run(_evaluate(plugin, group_id="g1"))

    assert decision.action == "reply"
    assert decision.reason == "focus_group"
    assert decision.force_reply is False


def test_focus_group_reply_to_bot_force_replies():
    """A reply-to-bot message inside the focus group force-replies."""
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
    """A keyword hit inside the focus group force-replies."""
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
    """A non-focus group is blocked even below the threshold; reason stays non_focus."""
    attention = _FakeAttention(focus_group="g2", score=0.5)
    plugin = _plugin(attention)

    decision, _ = asyncio.run(_evaluate(plugin, group_id="g1"))

    assert decision.action == "ignore"
    assert decision.reason.startswith("non_focus")


def test_focus_group_low_attention_blocked():
    """A focus group below the minimum threshold is blocked with the reason logged."""
    attention = _FakeAttention(focus_group="g1", score=0.5)
    plugin = _plugin(attention)

    decision, _ = asyncio.run(_evaluate(plugin, group_id="g1"))

    assert decision.action == "ignore"
    assert decision.reason.startswith("focus_low_attention")


class _OrderSensitiveAttention:
    """Verifies get_focus_group captures the receipt-time focus before update_on_message.

    Before the fix: update_on_message boosted the current group first, so a later
    get_focus_group could see it as focus and admit a non-@ message. After: the focus
    is captured first (receipt-time), so a boost-induced focus does not admit it.
    """

    def __init__(self):
        self.calls: list[str] = []
        self._now = 1000

    def _enabled(self) -> bool:
        return True

    def _current_time(self) -> int:
        return self._now

    def _minimum_threshold(self) -> float:
        return 1.0

    def get_focus_group(self) -> str | None:
        self.calls.append("get_focus_group")
        # 接收时焦点是 g2（非 g1）；g1 只有 boost 后才会成为焦点
        return "g2"

    def get_state(self, group_id: str):
        self.calls.append("get_state")
        return SimpleNamespace(attention_score=6.0)  # 分数足以成焦点

    async def update_on_message(self, message: dict) -> None:
        self.calls.append(f"update_on_message:{message.get('group_id')}")

    def mark_focus(self, group_id: str) -> None:
        self.calls.append(f"mark_focus:{group_id}")

    def wake_boost(self, group_id: str) -> None:
        self.calls.append(f"wake_boost:{group_id}")


def test_focus_captured_before_attention_update():
    """get_focus_group must run before update_on_message (receipt-time focus),
    or a boosted non-focus group could fake being the focus and be admitted."""
    attention = _OrderSensitiveAttention()
    plugin = _plugin(attention)

    decision, _ = asyncio.run(_evaluate(plugin, group_id="g1"))

    # 接收时 g1 非焦点（get_focus_group 返回 g2）→ 即使分数 6.0 也应 block
    assert decision.action == "ignore"
    assert decision.reason.startswith("non_focus")
    # get_focus_group 必须先于 update_on_message
    assert attention.calls.index("get_focus_group") < attention.calls.index("update_on_message:g1")
