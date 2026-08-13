"""`adjust_group_attention` entry: positive delta boosts, negative consumes, zero no-ops."""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from plugin.plugins.qq_auto_reply import QQAutoReplyPlugin


class _FakeAttention:
    def __init__(self):
        self.boosted = []
        self.consumed = []
        self.state = SimpleNamespace(attention_score=5.0)

    async def boost_attention(self, group_id, amount, reason=""):
        self.boosted.append((group_id, amount, reason))

    async def consume_attention(self, group_id, amount, reason=""):
        self.consumed.append((group_id, amount, reason))

    def get_state(self, group_id):
        return self.state


def _plugin(attention=None):
    # 轻量桩：不跑 __init__，只注入 attention_service
    inst = QQAutoReplyPlugin.__new__(QQAutoReplyPlugin)
    inst.attention_service = attention
    inst._emit_log = lambda *a, **k: None
    return inst


async def _adjust(plugin, gid, delta):
    return await plugin.adjust_group_attention(group_id=gid, delta=delta)


def test_positive_delta_boosts():
    attn = _FakeAttention()
    plugin = _plugin(attn)
    result = asyncio.run(_adjust(plugin, "g1", 1.5))
    assert attn.boosted == [("g1", 1.5, "manual_adjust")]
    assert attn.consumed == []
    assert result.value["attention_score"] == 5.0


def test_negative_delta_consumes():
    attn = _FakeAttention()
    plugin = _plugin(attn)
    result = asyncio.run(_adjust(plugin, "g1", -2))
    assert attn.consumed == [("g1", 2.0, "manual_adjust")]
    assert attn.boosted == []
    assert result.value["delta"] == -2


def test_zero_delta_noop():
    attn = _FakeAttention()
    plugin = _plugin(attn)
    result = asyncio.run(_adjust(plugin, "g1", 0))
    assert attn.boosted == []
    assert attn.consumed == []
    assert result.value.get("note") == "noop"


def test_missing_service_returns_error():
    plugin = _plugin(attention=None)
    result = asyncio.run(_adjust(plugin, "g1", 1))
    assert result.is_err


class _KwPlugin:
    """Minimal stub that drives update_on_message through the keyword-match branch."""
    def __init__(self, keyword_ratio):
        self._qq_settings = {
            "enable_group_attention": True,
            "attention_message_boost": 1.0,      # 简化：基础加成=1，便于断言
            "attention_keyword_boost_ratio": keyword_ratio,
            "group_attention_max_score": 10.0,
            "backlog_labels": [],
        }
        self.backlog_store = None
        self.group_permission_mgr = None
        self.permission_mgr = None
        self._emit_log = lambda *a, **k: None

    def _current_time(self):
        return 100


async def _classify_message_boost(keyword_ratio, category="issue"):
    from types import SimpleNamespace

    from plugin.plugins.qq_auto_reply.attention_service import QQAttentionService

    service = QQAttentionService(_KwPlugin(keyword_ratio))
    # 冻结 decay：last_decay_at 置为当前，避免相位推进干扰
    svc_now = 100
    service._current_time = lambda: svc_now
    service._load_state("g1").last_decay_at = svc_now
    service._write_state(service._load_state("g1"))
    # 直接驱动一条分类消息（category 由调用方注入）
    await service.update_on_message({
        "group_id": "g1", "user_id": "u", "content": "有报错",
        "timestamp": svc_now, "is_at_bot": False, "category": category,
    })
    return service.get_state("g1").attention_score


def test_keyword_boost_ratio_is_consumed():
    """update_on_message uses attention_keyword_boost_ratio instead of hardcoded 1.8."""
    import asyncio
    low = asyncio.run(_classify_message_boost(1.0))     # 1.0 * 1.0 = 1.0
    high = asyncio.run(_classify_message_boost(5.0))    # 1.0 * 5.0 = 5.0
    assert low == pytest.approx(1.0, rel=1e-3)
    assert high == pytest.approx(5.0, rel=1e-3)
    assert high > low
