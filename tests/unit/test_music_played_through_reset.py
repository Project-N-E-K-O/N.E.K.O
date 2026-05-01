"""音乐完整播放 → 重置 music 通道权重衰减。

完整播放是用户对"音乐分享"通道最强的正向反馈，所以前端 'ended' 事件命中
后会向 /api/proactive/music_played_through 发一发 POST，让后端把
_proactive_chat_history 中所有 channel == 'music' 的 entry 通道字段置空。
该 entry 仍然保留在 deque 里参与 dedup / similarity / format_recent_proactive_chats，
但 _compute_source_weights 不会再把它计入 music 的衰减惩罚。
"""
import copy
import os
import sys
from collections import deque

import pytest

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "../../")))

from main_routers import system_router as sr


LL = "测试娘"


@pytest.fixture(autouse=True)
def _isolate_proactive_history():
    """隔离 _proactive_chat_history 这块模块级全局：
    每个测试拿到一份干净的快照副本，无论中途断言失败 / 异常都还原原状，
    避免用例顺序敏感 / 跨测试串扰。"""
    snapshot = copy.deepcopy(sr._proactive_chat_history)
    sr._proactive_chat_history.clear()
    yield
    sr._proactive_chat_history.clear()
    sr._proactive_chat_history.update(snapshot)


def _setup_history(entries):
    sr._proactive_chat_history[LL] = deque(entries, maxlen=10)


def test_clear_music_channel_rewrites_only_music_entries():
    _setup_history([
        (1.0, "看了张猫片", "vision"),
        (2.0, "热搜聊一下", "web"),
        (3.0, "推荐一首歌", "music"),
        (4.0, "再来一首", "music"),
        (5.0, "看个表情包", "meme"),
    ])

    cleared = sr._clear_channel_from_proactive_history(LL, "music")
    assert cleared == 2

    history = list(sr._proactive_chat_history[LL])
    channels = [e[2] for e in history]
    assert channels == ["vision", "web", "", "", "meme"]
    # 文本与时间戳保持原样，便于 dedup / similarity 仍然命中
    assert history[2][1] == "推荐一首歌"
    assert history[3][1] == "再来一首"
    assert history[2][0] == 3.0


def test_clear_idempotent_when_no_music_entries():
    _setup_history([
        (1.0, "看了张猫片", "vision"),
        (2.0, "热搜聊一下", "web"),
    ])
    assert sr._clear_channel_from_proactive_history(LL, "music") == 0
    # 没命中 → 不改写 deque，原顺序原 channel
    history = list(sr._proactive_chat_history[LL])
    assert [e[2] for e in history] == ["vision", "web"]


def test_clear_for_unknown_lanlan_returns_zero():
    assert sr._clear_channel_from_proactive_history("不存在的角色", "music") == 0


def test_compute_source_weights_recovers_after_reset(monkeypatch):
    """关键集成验证：连续 3 次 music 分享后 music 应被 _filter_sources_by_weight 剔除；
    一旦完整播放调用 _clear_channel_from_proactive_history('music')，music 应不再被剔除。"""
    fixed_now = 10_000.0
    monkeypatch.setattr(sr.time, "time", lambda: fixed_now)

    # 连续 3 次都是 music（最近 1h 内）
    sr._record_proactive_chat(LL, "歌1", "music")
    sr._record_proactive_chat(LL, "歌2", "music")
    sr._record_proactive_chat(LL, "歌3", "music")

    candidates = ["web", "music", "meme", "reminiscence"]
    weights_before = sr._compute_source_weights(LL, candidates)
    suppressed_before = sr._filter_sources_by_weight(weights_before)
    assert "music" in suppressed_before, (
        f"连续 3 次 music 分享后，music 应该被权重剔除，实际权重: {weights_before}"
    )

    # 用户完整听完 → 触发重置
    cleared = sr._clear_channel_from_proactive_history(LL, "music")
    assert cleared == 3

    weights_after = sr._compute_source_weights(LL, candidates)
    suppressed_after = sr._filter_sources_by_weight(weights_after)
    assert "music" not in suppressed_after, (
        f"完整播放重置后，music 不应再被剔除，实际权重: {weights_after}"
    )
    # 重置后 4 个候选通道应回到均匀分布
    for ch in candidates:
        assert abs(weights_after[ch] - 0.25) < 1e-9


def test_reset_is_counter_zero_not_throttle_off(monkeypatch):
    """关键语义：重置 = counter 归 0，不是永久关闭 throttle。
    听完一次后，下一轮再连续分享 music 仍然会重新累加并最终被剔除——
    否则相当于"用户听完一次 → 此后无限放音乐"。"""
    candidates = ["web", "music", "meme", "reminiscence"]

    fake_t = [10_000.0]
    monkeypatch.setattr(sr.time, "time", lambda: fake_t[0])

    # 第一轮：连推 3 首 → music 被剔除
    for i in range(3):
        sr._record_proactive_chat(LL, f"first-{i}", "music")
        fake_t[0] += 1.0
    assert "music" in sr._filter_sources_by_weight(
        sr._compute_source_weights(LL, candidates)
    )

    # 用户听完最后一首 → 复位
    fake_t[0] += 0.5
    sr._clear_channel_from_proactive_history(LL, "music")
    assert "music" not in sr._filter_sources_by_weight(
        sr._compute_source_weights(LL, candidates)
    ), "复位后立刻应当回到均匀"

    # 第二轮：再连推 3 首 → 应当从 0 重新累加，再次被剔除
    for i in range(3):
        fake_t[0] += 1.0
        sr._record_proactive_chat(LL, f"second-{i}", "music")
    suppressed = sr._filter_sources_by_weight(
        sr._compute_source_weights(LL, candidates)
    )
    assert "music" in suppressed, (
        "复位不能等同于永久关闭 throttle —— 第二轮再连推 3 首必须重新触达阈值"
    )


def test_cleared_entries_still_visible_in_format_recent(monkeypatch):
    """清空 channel 字段后，message 文本仍要在 _format_recent_proactive_chats 里出现，
    避免 LLM 反复推同一首歌。"""
    fixed_now = 20_000.0
    monkeypatch.setattr(sr.time, "time", lambda: fixed_now)

    sr._record_proactive_chat(LL, "我刚刚发现一首很棒的歌", "music")
    sr._clear_channel_from_proactive_history(LL, "music")

    rendered = sr._format_recent_proactive_chats(LL, lang="zh")
    assert "我刚刚发现一首很棒的歌" in rendered
