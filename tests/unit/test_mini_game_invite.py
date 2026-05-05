"""Mini-game 邀请短路通道测试。

覆盖三层契约：
1. ``_mini_game_invite_in_cooldown`` —— pending（投递了但没回应）一律 cooldown；
   已回应后必须同时跨过 24h 与 10 chats 才能解除。
2. ``_mini_game_invite_advance_response`` —— pending 期间，用户 last msg 时间戳
   晚于 delivered_at 时翻成已回应；activity_snapshot 缺失则保留 pending。
3. ``_maybe_deliver_mini_game_invite`` —— eligibility 顺序：DISABLED →
   activity_snapshot None → restricted_screen_only → away → cooldown → 掷骰；
   命中即走 prepare → feed_tts → finish 三步投递并写 _proactive_chat_history。
"""
from __future__ import annotations

import os
import sys
import time
import types
from unittest.mock import AsyncMock, MagicMock

import pytest

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "../../")))

import main_routers.system_router as sr  # noqa: E402

LANLAN = "test_lanlan"
MASTER = "小明"


def _make_snapshot(state="casual_browsing", propensity="open", seconds_since_user_msg=None):
    """构造一个 ActivitySnapshot duck-typed 替身——只用到 .state /
    .propensity / .seconds_since_user_msg 三个字段。"""
    return types.SimpleNamespace(
        state=state,
        propensity=propensity,
        seconds_since_user_msg=seconds_since_user_msg,
    )


def _make_mgr(*, prepare_ok=True, finish_ok=True, sid="sid-test"):
    mgr = MagicMock()
    mgr.prepare_proactive_delivery = AsyncMock(return_value=prepare_ok)
    mgr.finish_proactive_delivery = AsyncMock(return_value=finish_ok)
    mgr.feed_tts_chunk = AsyncMock()
    mgr.current_speech_id = sid
    mgr.state = MagicMock()
    mgr.state.fire = AsyncMock()
    return mgr


@pytest.fixture(autouse=True)
def _clear_mini_game_state():
    """每个 test 进来前后都清干净 module-level state。"""
    sr._mini_game_invite_state.clear()
    sr._proactive_chat_history.clear()
    yield
    sr._mini_game_invite_state.clear()
    sr._proactive_chat_history.clear()


# ─────────────────────────────────────────────────────────────────────────────
# _mini_game_invite_in_cooldown
# ─────────────────────────────────────────────────────────────────────────────

def test_in_cooldown_false_when_never_delivered():
    """从未投递过邀请 → 不在 cooldown。"""
    assert sr._mini_game_invite_in_cooldown(LANLAN) is False


def test_in_cooldown_true_when_pending():
    """投递了但还没被回应（pending）→ cooldown 锁住，避免又发一次。"""
    sr._mini_game_invite_record_delivered(LANLAN)
    assert sr._mini_game_invite_in_cooldown(LANLAN) is True


def test_in_cooldown_true_when_responded_within_24h_and_under_10_chats():
    """已回应但 24h 没到 + 10 次没到 → cooldown。"""
    state = sr._mini_game_invite_get_state(LANLAN)
    state['delivered_at'] = time.time() - 60
    state['responded_at'] = time.time() - 60
    state['chats_since_response'] = 5
    assert sr._mini_game_invite_in_cooldown(LANLAN) is True


def test_in_cooldown_true_when_only_time_elapsed_chats_short():
    """24h 已过但 chats 没到 10 次 → 仍 cooldown（AND 语义）。"""
    state = sr._mini_game_invite_get_state(LANLAN)
    state['delivered_at'] = time.time() - sr.MINI_GAME_INVITE_COOLDOWN_SECONDS - 100
    state['responded_at'] = time.time() - sr.MINI_GAME_INVITE_COOLDOWN_SECONDS - 100
    state['chats_since_response'] = 3
    assert sr._mini_game_invite_in_cooldown(LANLAN) is True


def test_in_cooldown_true_when_only_chats_done_time_short():
    """chats 跨过 10 但 24h 没到 → 仍 cooldown（AND 语义）。"""
    state = sr._mini_game_invite_get_state(LANLAN)
    state['delivered_at'] = time.time() - 600
    state['responded_at'] = time.time() - 600
    state['chats_since_response'] = 99
    assert sr._mini_game_invite_in_cooldown(LANLAN) is True


def test_in_cooldown_false_when_both_thresholds_passed():
    """24h 和 10 chats 都过了 → 解禁，下次掷骰。"""
    state = sr._mini_game_invite_get_state(LANLAN)
    state['delivered_at'] = time.time() - sr.MINI_GAME_INVITE_COOLDOWN_SECONDS - 100
    state['responded_at'] = time.time() - sr.MINI_GAME_INVITE_COOLDOWN_SECONDS - 100
    state['chats_since_response'] = sr.MINI_GAME_INVITE_COOLDOWN_CHATS
    assert sr._mini_game_invite_in_cooldown(LANLAN) is False


# ─────────────────────────────────────────────────────────────────────────────
# _mini_game_invite_advance_response
# ─────────────────────────────────────────────────────────────────────────────

def test_advance_response_noop_when_never_delivered():
    """没投递过，advance 是 no-op。"""
    sr._mini_game_invite_advance_response(LANLAN, _make_snapshot(seconds_since_user_msg=1.0))
    assert LANLAN not in sr._mini_game_invite_state


def test_advance_response_noop_when_already_responded():
    """已经回应过，再 advance 不再回写时间戳。"""
    state = sr._mini_game_invite_get_state(LANLAN)
    state['delivered_at'] = time.time() - 1000
    original_responded = time.time() - 500
    state['responded_at'] = original_responded
    state['chats_since_response'] = 3

    sr._mini_game_invite_advance_response(
        LANLAN, _make_snapshot(seconds_since_user_msg=10.0)
    )
    assert state['responded_at'] == original_responded
    assert state['chats_since_response'] == 3


def test_advance_response_noop_when_snapshot_none():
    """activity_snapshot 缺失 → 保留 pending。"""
    sr._mini_game_invite_record_delivered(LANLAN)
    sr._mini_game_invite_advance_response(LANLAN, None)
    assert sr._mini_game_invite_state[LANLAN]['responded_at'] is None


def test_advance_response_noop_when_seconds_since_user_msg_none():
    """活动 tracker 没记录用户最后一句话 → 保留 pending。"""
    sr._mini_game_invite_record_delivered(LANLAN)
    sr._mini_game_invite_advance_response(
        LANLAN, _make_snapshot(seconds_since_user_msg=None)
    )
    assert sr._mini_game_invite_state[LANLAN]['responded_at'] is None


def test_advance_response_flips_when_user_spoke_after_invite():
    """用户在 delivered_at 之后说过话 → 标记已回应、计数清零。"""
    delivered_at = time.time() - 30
    state = sr._mini_game_invite_get_state(LANLAN)
    state['delivered_at'] = delivered_at
    state['responded_at'] = None
    state['chats_since_response'] = 0

    # 用户 5s 前说了话 → 落在 delivered_at 之后
    sr._mini_game_invite_advance_response(
        LANLAN, _make_snapshot(seconds_since_user_msg=5.0)
    )
    assert state['responded_at'] is not None
    assert state['responded_at'] >= delivered_at
    assert state['chats_since_response'] == 0


def test_advance_response_does_not_flip_when_last_user_msg_predates_invite():
    """用户 last msg 早于邀请投递时间（投完一直没说话）→ 保留 pending。"""
    delivered_at = time.time() - 5
    state = sr._mini_game_invite_get_state(LANLAN)
    state['delivered_at'] = delivered_at
    state['responded_at'] = None

    # 用户 100s 前说了话，远早于 5s 前的投递
    sr._mini_game_invite_advance_response(
        LANLAN, _make_snapshot(seconds_since_user_msg=100.0)
    )
    assert state['responded_at'] is None


# ─────────────────────────────────────────────────────────────────────────────
# _mini_game_invite_count_post_response_chat
# ─────────────────────────────────────────────────────────────────────────────

def test_count_noop_when_never_delivered():
    sr._mini_game_invite_count_post_response_chat(LANLAN)
    assert LANLAN not in sr._mini_game_invite_state


def test_count_noop_during_pending():
    """投递了但没回应（pending）→ counter 不推进，不能靠"邀请自身"耗掉 10 次。"""
    sr._mini_game_invite_record_delivered(LANLAN)
    sr._mini_game_invite_count_post_response_chat(LANLAN)
    sr._mini_game_invite_count_post_response_chat(LANLAN)
    assert sr._mini_game_invite_state[LANLAN]['chats_since_response'] == 0


def test_count_increments_after_responded():
    """已回应后每次成功投递 +1，与 channel 无关。"""
    state = sr._mini_game_invite_get_state(LANLAN)
    state['delivered_at'] = time.time() - 1000
    state['responded_at'] = time.time() - 500
    state['chats_since_response'] = 0

    for _ in range(7):
        sr._mini_game_invite_count_post_response_chat(LANLAN)
    assert state['chats_since_response'] == 7


# ─────────────────────────────────────────────────────────────────────────────
# _maybe_deliver_mini_game_invite —— eligibility 与 投递路径
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_maybe_deliver_returns_none_when_disabled(monkeypatch):
    monkeypatch.setattr(sr, 'MINI_GAME_INVITE_ENABLED', False)
    out = await sr._maybe_deliver_mini_game_invite(
        lanlan_name=LANLAN, mgr=_make_mgr(),
        activity_snapshot=_make_snapshot(),
        invite_lang='zh', master_name=MASTER,
    )
    assert out is None


@pytest.mark.asyncio
async def test_maybe_deliver_returns_none_when_snapshot_none():
    """隐私模式 / tracker 不可用 → 保守不发——无法判断是否在工作状态。"""
    out = await sr._maybe_deliver_mini_game_invite(
        lanlan_name=LANLAN, mgr=_make_mgr(),
        activity_snapshot=None,
        invite_lang='zh', master_name=MASTER,
    )
    assert out is None


@pytest.mark.asyncio
async def test_maybe_deliver_returns_none_when_restricted_screen_only(monkeypatch):
    """工作状态（focused_work / non-casual gaming）→ 不邀请。"""
    monkeypatch.setattr(sr, 'MINI_GAME_INVITE_TRIGGER_PROBABILITY', 1.0)
    out = await sr._maybe_deliver_mini_game_invite(
        lanlan_name=LANLAN, mgr=_make_mgr(),
        activity_snapshot=_make_snapshot(
            state='focused_work', propensity='restricted_screen_only',
        ),
        invite_lang='zh', master_name=MASTER,
    )
    assert out is None


@pytest.mark.asyncio
async def test_maybe_deliver_returns_none_when_away(monkeypatch):
    """用户离场 → 邀请没人接。"""
    monkeypatch.setattr(sr, 'MINI_GAME_INVITE_TRIGGER_PROBABILITY', 1.0)
    out = await sr._maybe_deliver_mini_game_invite(
        lanlan_name=LANLAN, mgr=_make_mgr(),
        activity_snapshot=_make_snapshot(state='away', propensity='open'),
        invite_lang='zh', master_name=MASTER,
    )
    assert out is None


@pytest.mark.asyncio
async def test_maybe_deliver_returns_none_when_in_cooldown(monkeypatch):
    """pending 期间一律抑制掷骰。"""
    monkeypatch.setattr(sr, 'MINI_GAME_INVITE_TRIGGER_PROBABILITY', 1.0)
    sr._mini_game_invite_record_delivered(LANLAN)
    out = await sr._maybe_deliver_mini_game_invite(
        lanlan_name=LANLAN, mgr=_make_mgr(),
        activity_snapshot=_make_snapshot(),
        invite_lang='zh', master_name=MASTER,
    )
    assert out is None


@pytest.mark.asyncio
async def test_maybe_deliver_returns_none_when_dice_misses(monkeypatch):
    """概率没过 → 不投递。"""
    monkeypatch.setattr(sr, 'MINI_GAME_INVITE_TRIGGER_PROBABILITY', 0.0)
    out = await sr._maybe_deliver_mini_game_invite(
        lanlan_name=LANLAN, mgr=_make_mgr(),
        activity_snapshot=_make_snapshot(),
        invite_lang='zh', master_name=MASTER,
    )
    assert out is None


@pytest.mark.asyncio
async def test_maybe_deliver_chat_when_eligible(monkeypatch):
    """全部 eligibility 通过 + 必中骰子 → 走 prepare → feed_tts → finish 投递；
    state 翻成 pending；写入 _proactive_chat_history。"""
    monkeypatch.setattr(sr, 'MINI_GAME_INVITE_TRIGGER_PROBABILITY', 1.0)
    mgr = _make_mgr(sid='sid-eligible')
    out = await sr._maybe_deliver_mini_game_invite(
        lanlan_name=LANLAN, mgr=mgr,
        activity_snapshot=_make_snapshot(),
        invite_lang='zh', master_name=MASTER,
    )
    assert out is not None
    assert out["action"] == "chat"
    assert out["channel"] == "mini_game"
    assert out["turn_id"] == "sid-eligible"

    mgr.prepare_proactive_delivery.assert_awaited_once()
    mgr.finish_proactive_delivery.assert_awaited_once()
    # feed_tts 与 finish 都要带上当前 speech_id
    feed_call = mgr.feed_tts_chunk.await_args
    assert feed_call.kwargs.get('expected_speech_id') == 'sid-eligible'
    finish_call = mgr.finish_proactive_delivery.await_args
    assert finish_call.kwargs.get('expected_speech_id') == 'sid-eligible'

    # state 进 pending
    state = sr._mini_game_invite_state[LANLAN]
    assert state['delivered_at'] is not None
    assert state['responded_at'] is None
    assert state['chats_since_response'] == 0

    # _proactive_chat_history 也写了一条 channel='mini_game'
    history = sr._proactive_chat_history[LANLAN]
    assert len(history) == 1
    _, message, channel = history[0]
    assert MASTER in message
    assert channel == 'mini_game'


@pytest.mark.asyncio
async def test_maybe_deliver_pass_when_prepare_refuses(monkeypatch):
    """prepare 拒绝（用户刚说过话 / 没 websocket / 等）→ 返回 pass，不写 state。"""
    monkeypatch.setattr(sr, 'MINI_GAME_INVITE_TRIGGER_PROBABILITY', 1.0)
    mgr = _make_mgr(prepare_ok=False)
    out = await sr._maybe_deliver_mini_game_invite(
        lanlan_name=LANLAN, mgr=mgr,
        activity_snapshot=_make_snapshot(),
        invite_lang='zh', master_name=MASTER,
    )
    assert out is not None
    assert out["action"] == "pass"
    mgr.finish_proactive_delivery.assert_not_awaited()
    assert LANLAN not in sr._mini_game_invite_state


@pytest.mark.asyncio
async def test_maybe_deliver_pass_when_user_takes_over_before_finish(monkeypatch):
    """finish_proactive_delivery 返回 False（用户在投递期间抢占）→
    不计入 history、不更新 cooldown state，避免后续被错误抑制。"""
    monkeypatch.setattr(sr, 'MINI_GAME_INVITE_TRIGGER_PROBABILITY', 1.0)
    mgr = _make_mgr(finish_ok=False)
    out = await sr._maybe_deliver_mini_game_invite(
        lanlan_name=LANLAN, mgr=mgr,
        activity_snapshot=_make_snapshot(),
        invite_lang='zh', master_name=MASTER,
    )
    assert out is not None
    assert out["action"] == "pass"
    assert LANLAN not in sr._mini_game_invite_state
    assert LANLAN not in sr._proactive_chat_history


@pytest.mark.asyncio
async def test_maybe_deliver_uses_localized_template(monkeypatch):
    """invite_lang 选英文 → 文案落英文模板，master_name 实名展开。"""
    monkeypatch.setattr(sr, 'MINI_GAME_INVITE_TRIGGER_PROBABILITY', 1.0)
    mgr = _make_mgr()
    out = await sr._maybe_deliver_mini_game_invite(
        lanlan_name=LANLAN, mgr=mgr,
        activity_snapshot=_make_snapshot(),
        invite_lang='en', master_name='Alice',
    )
    assert out is not None
    history = sr._proactive_chat_history[LANLAN]
    _, message, _ = history[0]
    assert 'Alice' in message
    assert 'soccer' in message.lower()


# ─────────────────────────────────────────────────────────────────────────────
# i18n 覆盖契约
# ─────────────────────────────────────────────────────────────────────────────

def test_invite_line_covers_all_native_locales():
    """5 个 native locale（zh/en/ja/ko/ru）都必须有可格式化模板。"""
    from config.prompts_proactive import MINI_GAME_INVITE_LINE
    for lang in ('zh', 'en', 'ja', 'ko', 'ru'):
        line = MINI_GAME_INVITE_LINE[lang]
        assert '{master_name}' in line, f"{lang} 模板缺 master_name 占位符"
        rendered = line.format(master_name='测试')
        assert '测试' in rendered
        # 模板长度合理（不空、不爆）
        assert 5 <= len(line) <= 200, f"{lang} 模板长度异常: {len(line)}"


def test_source_label_covers_mini_game():
    """PROACTIVE_SOURCE_LABELS 必须为 mini_game 通道补齐 5 个 native locale 标签。"""
    from config.prompts_proactive import PROACTIVE_SOURCE_LABELS
    for lang in ('zh', 'en', 'ja', 'ko', 'ru'):
        assert 'mini_game' in PROACTIVE_SOURCE_LABELS[lang], \
            f"{lang} 缺 mini_game 标签"
        assert PROACTIVE_SOURCE_LABELS[lang]['mini_game'].strip()
