"""Complete music playback resets the music channel's weight decay.

Listening through completion is the strongest positive feedback for music sharing. On the frontend's 'ended' event,
POST /api/proactive/music_played_through once so the backend clears channel fields
for all _proactive_chat_history entries whose channel == 'music'.
Keep these entries in the deque for deduplication, similarity, and format_recent_proactive_chats,
while _compute_source_weights no longer counts them toward music's decay penalty.
"""
import copy
import os
import sys
from collections import deque

import pytest

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "../../")))

from main_logic.proactive_chat import state as sr
from main_logic.proactive_chat import decisions as sr_sources
from main_logic.proactive_chat import contracts as sr_parsing
from tests.fake_clock import patch_module_clock


LL = "测试娘"


@pytest.fixture(autouse=True)
def _isolate_proactive_history():
    """Isolate the module-global _proactive_chat_history:
    give every test a clean snapshot copy and restore it even after assertion failures/exceptions,
    avoiding order sensitivity and cross-test interference."""
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
    # Preserve text/timestamps so deduplication/similarity still match.
    assert history[2][1] == "推荐一首歌"
    assert history[3][1] == "再来一首"
    assert history[2][0] == 3.0


def test_clear_idempotent_when_no_music_entries():
    _setup_history([
        (1.0, "看了张猫片", "vision"),
        (2.0, "热搜聊一下", "web"),
    ])
    assert sr._clear_channel_from_proactive_history(LL, "music") == 0
    # No match -> leave deque order and channels unchanged.
    history = list(sr._proactive_chat_history[LL])
    assert [e[2] for e in history] == ["vision", "web"]


def test_clear_for_unknown_lanlan_returns_zero():
    assert sr._clear_channel_from_proactive_history("不存在的角色", "music") == 0


def test_compute_source_weights_recovers_after_reset(monkeypatch):
    """Key integration check: after 3 consecutive music shares, _filter_sources_by_weight excludes music;
    once playback completion calls _clear_channel_from_proactive_history('music'), music is no longer excluded."""
    fixed_now = 10_000.0
    # Each module reads its own clock: state._record_proactive_chat supplies write timestamps,
    # while decisions._compute_source_weights reads now = time.time() for window/decay calculations.
    # Patching only state makes weights subtract 10000 from real time, immediately pushing records outside the 1-hour window.
    patch_module_clock(monkeypatch, sr, time=lambda: fixed_now)
    patch_module_clock(monkeypatch, sr_sources, time=lambda: fixed_now)

    # Three consecutive music entries within the last hour.
    sr._record_proactive_chat(LL, "歌1", "music")
    sr._record_proactive_chat(LL, "歌2", "music")
    sr._record_proactive_chat(LL, "歌3", "music")

    candidates = ["web", "music", "meme", "reminiscence"]
    weights_before = sr_sources._compute_source_weights(LL, candidates)
    suppressed_before = sr_sources._filter_sources_by_weight(weights_before)
    assert "music" in suppressed_before, (
        f"连续 3 次 music 分享后，music 应该被权重剔除，实际权重: {weights_before}"
    )

    # Listening through completion triggers reset.
    cleared = sr._clear_channel_from_proactive_history(LL, "music")
    assert cleared == 3

    weights_after = sr_sources._compute_source_weights(LL, candidates)
    suppressed_after = sr_sources._filter_sources_by_weight(weights_after)
    assert "music" not in suppressed_after, (
        f"完整播放重置后，music 不应再被剔除，实际权重: {weights_after}"
    )
    # After reset, all four candidate channels should have uniform weights.
    for ch in candidates:
        assert abs(weights_after[ch] - 0.25) < 1e-9


def test_reset_is_counter_zero_not_throttle_off(monkeypatch):
    """Key semantics: reset the counter to zero, never disable throttling permanently.
    After one complete playback, another sequence of music shares still accumulates and is eventually excluded,
    rather than permitting unlimited music after a single completed song."""
    candidates = ["web", "music", "meme", "reminiscence"]

    fake_t = [10_000.0]
    # Likewise, state records timestamps and decisions._compute_source_weights computes decay using now;
    # both must share the same fake clock.
    patch_module_clock(monkeypatch, sr, time=lambda: fake_t[0])
    patch_module_clock(monkeypatch, sr_sources, time=lambda: fake_t[0])

    # Round one: recommend 3 consecutive songs -> exclude music.
    for i in range(3):
        sr._record_proactive_chat(LL, f"first-{i}", "music")
        fake_t[0] += 1.0
    assert "music" in sr_sources._filter_sources_by_weight(
        sr_sources._compute_source_weights(LL, candidates)
    )

    # The user finishes the last song -> reset.
    fake_t[0] += 0.5
    sr._clear_channel_from_proactive_history(LL, "music")
    assert "music" not in sr_sources._filter_sources_by_weight(
        sr_sources._compute_source_weights(LL, candidates)
    ), "复位后立刻应当回到均匀"

    # Round two: 3 more consecutive songs -> accumulate again from zero and exclude again.
    for i in range(3):
        fake_t[0] += 1.0
        sr._record_proactive_chat(LL, f"second-{i}", "music")
    suppressed = sr_sources._filter_sources_by_weight(
        sr_sources._compute_source_weights(LL, candidates)
    )
    assert "music" in suppressed, (
        "复位不能等同于永久关闭 throttle —— 第二轮再连推 3 首必须重新触达阈值"
    )


def test_cleared_entries_still_visible_in_format_recent(monkeypatch):
    """After clearing channel fields, message text must remain in _format_recent_proactive_chats
    to prevent the LLM repeatedly recommending the same song."""
    fixed_now = 20_000.0
    # This exercises only state: both writes (_record_proactive_chat) and rendering
    # (now = time.time() in _format_recent_proactive_chats) live in that module.
    patch_module_clock(monkeypatch, sr, time=lambda: fixed_now)

    sr._record_proactive_chat(LL, "我刚刚发现一首很棒的歌", "music")
    sr._clear_channel_from_proactive_history(LL, "music")

    rendered = sr._format_recent_proactive_chats(LL, lang="zh")
    assert "我刚刚发现一首很棒的歌" in rendered
