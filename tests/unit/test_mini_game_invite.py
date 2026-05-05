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


def _make_snapshot(
    state="casual_browsing",
    propensity="open",
    seconds_since_user_msg=None,
    unfinished_thread=None,
):
    """构造一个 ActivitySnapshot duck-typed 替身——只用到 .state /
    .propensity / .seconds_since_user_msg / .unfinished_thread 四个字段。"""
    return types.SimpleNamespace(
        state=state,
        propensity=propensity,
        seconds_since_user_msg=seconds_since_user_msg,
        unfinished_thread=unfinished_thread,
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
    sr._proactive_chat_totals.clear()
    sr._invite_ever_delivered.clear()
    yield
    sr._mini_game_invite_state.clear()
    sr._proactive_chat_history.clear()
    sr._proactive_chat_totals.clear()
    sr._invite_ever_delivered.clear()


@pytest.fixture(autouse=True)
def _force_invite_enabled_default(monkeypatch):
    """每个 test 默认强制 MINI_GAME_INVITE_ENABLED=True。

    本测试套件大部分用例都假定 invite 通道开着、然后验证某条 gate 是否生效。
    如果哪天 module 默认值被翻成 False（例如灰度阶段），这些 deliver / gate
    测试都会静默退化成「ENABLED 短路全部命中」，无法捕获真正的 gate 退化。
    autouse 把契约前置：测试断言「此通道开着且 X gate 生效」，要测 disabled
    分支的用例（test_maybe_deliver_returns_none_when_disabled）自己 setattr
    回 False。"""
    monkeypatch.setattr(sr, 'MINI_GAME_INVITE_ENABLED', True)


@pytest.fixture(autouse=True)
def _stub_persistent_counter(monkeypatch):
    """单测不初始化 shared_state.config_manager，真路径 ``_proactive_chat_totals_path``
    会 RuntimeError。把 load / increment / mark-ever-delivered 都替换成纯内存版
    本，断言能直接读 / 写 ``sr._proactive_chat_totals[lanlan]`` 与
    ``sr._invite_ever_delivered[lanlan]`` 来 setup / verify。"""
    async def _noop_load():
        sr._proactive_chat_totals_loaded = True

    async def _bump_only(lanlan_name: str) -> int:
        n = sr._proactive_chat_totals.get(lanlan_name, 0) + 1
        sr._proactive_chat_totals[lanlan_name] = n
        return n

    async def _mark_only(lanlan_name: str) -> None:
        sr._invite_ever_delivered[lanlan_name] = True

    async def _record_delivery_only(lanlan_name: str) -> int:
        # 模拟原子写盘——bump counter + 置 ever_delivered，单次状态更新。
        n = sr._proactive_chat_totals.get(lanlan_name, 0) + 1
        sr._proactive_chat_totals[lanlan_name] = n
        sr._invite_ever_delivered[lanlan_name] = True
        return n

    monkeypatch.setattr(sr, '_ensure_proactive_chat_totals_loaded', _noop_load)
    monkeypatch.setattr(sr, '_increment_proactive_chat_total', _bump_only)
    monkeypatch.setattr(sr, '_mark_invite_ever_delivered', _mark_only)
    monkeypatch.setattr(sr, '_record_invite_delivery_persistent', _record_delivery_only)
    sr._proactive_chat_totals_loaded = False  # 强制每个 test 走 _noop_load 一次


# ─────────────────────────────────────────────────────────────────────────────
# _mini_game_invite_in_cooldown
# ─────────────────────────────────────────────────────────────────────────────

def test_in_cooldown_false_when_never_delivered():
    """从未投递过邀请 → 不在 cooldown。"""
    assert sr._mini_game_invite_in_cooldown(LANLAN) is False


def test_in_cooldown_true_when_pending():
    """投递了但还没被回应（pending）→ cooldown 锁住，避免又发一次。"""
    sr._mini_game_invite_record_delivered(LANLAN, "test-session-id")
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
    sr._mini_game_invite_advance_response(LANLAN, time.time() - 1.0)
    assert LANLAN not in sr._mini_game_invite_state


def test_advance_response_noop_when_already_responded():
    """已经回应过，再 advance 不再回写时间戳。"""
    state = sr._mini_game_invite_get_state(LANLAN)
    state['delivered_at'] = time.time() - 1000
    original_responded = time.time() - 500
    state['responded_at'] = original_responded
    state['chats_since_response'] = 3

    sr._mini_game_invite_advance_response(LANLAN, time.time() - 10.0)
    assert state['responded_at'] == original_responded
    assert state['chats_since_response'] == 3


def test_advance_response_noop_when_last_user_msg_at_none():
    """caller 没拿到 last_user_msg_at（隐私模式 / tracker 没数据）→ 保留 pending。"""
    sr._mini_game_invite_record_delivered(LANLAN, "test-session-id")
    sr._mini_game_invite_advance_response(LANLAN, None)
    assert sr._mini_game_invite_state[LANLAN]['responded_at'] is None


def test_advance_response_dismisses_pending_invite_with_short_suppression(monkeypatch):
    """用户在 delivered_at 之后发了任意普通消息（非显式 choice / 关键词命中）
    → advance 把 prompt 静默 dismiss + 5min 短抑制，**不**启动长冷却。

    历史：旧 PR #1141 时代「用户说话 = 隐式回应」直接 mark responded_at +
    1h 长锁。CodeRabbit Major 指出会让"用户先发别的话再点按钮"被 endpoint
    误判 expired 并已悄悄进入长冷却（违 D2 语义）。改成等同 'later' 选项的
    reset+短抑制语义：保留 ever_delivered（force-first 不再 fire）但不长锁。"""
    fixed_now = 1_700_000_000.0
    monkeypatch.setattr(sr.time, 'time', lambda: fixed_now)

    delivered_at = fixed_now - 30
    state = sr._mini_game_invite_get_state(LANLAN)
    state['delivered_at'] = delivered_at
    state['responded_at'] = None
    state['chats_since_response'] = 0
    state['pending_session_id'] = 'pre-advance-sess'

    # 用户 5s 前说了话 → 落在 delivered_at 之后
    sr._mini_game_invite_advance_response(LANLAN, fixed_now - 5.0)
    # 不再 set responded_at（避免长冷却）
    assert state['responded_at'] is None, (
        "advance_response 不应再 set responded_at——长冷却只该由显式 accept/decline 触发"
    )
    # state 进 dismissed/reset：delivered_at 清掉，pending session 清掉
    assert state['delivered_at'] is None
    assert state['pending_session_id'] is None
    # 5min 短抑制：suppressed_until = fixed_now + 5min
    assert state['suppressed_until'] == fixed_now + sr.MINI_GAME_INVITE_LATER_SUPPRESS_SECONDS


def test_advance_response_does_not_trigger_long_cooldown(monkeypatch):
    """关键回归保护（CodeRabbit Major 指出场景）：
    1) 邀请投递；
    2) 用户先发普通消息（不命中关键词）→ advance 跑；
    3) 用户随后点 accept 按钮。
    旧逻辑：advance 已 mark responded → endpoint 看到 responded_at != None →
            返 expired，但状态早就进 1h 长冷却（错）。
    新逻辑：advance 仅 dismiss + 5min 短抑制（≠长冷却）；endpoint 看到没
            pending 仍返 expired（按钮已晚），但 5min 后下次 proactive 重新
            走骰子可重新邀请，不长锁。"""
    fixed_now = 1_700_005_000.0
    monkeypatch.setattr(sr.time, 'time', lambda: fixed_now)

    state = sr._mini_game_invite_get_state(LANLAN)
    state['delivered_at'] = fixed_now - 60
    state['responded_at'] = None
    state['chats_since_response'] = 0
    state['pending_session_id'] = 'sess-x'
    sr._invite_ever_delivered[LANLAN] = True

    # 用户 30s 前说了句普通话（advance 抓到）
    sr._mini_game_invite_advance_response(LANLAN, fixed_now - 30.0)

    # cooldown 检查应只反映 5min 短抑制（不到 1h 长锁）
    assert state['responded_at'] is None
    assert state['chats_since_response'] == 0  # 不是被锁的「已回应进入 10 chats 计数」
    assert state['suppressed_until'] is not None
    suppress_window = state['suppressed_until'] - fixed_now
    assert (
        sr.MINI_GAME_INVITE_LATER_SUPPRESS_SECONDS - 1
        <= suppress_window
        <= sr.MINI_GAME_INVITE_LATER_SUPPRESS_SECONDS + 1
    ), f"suppressed_until 应是 5min 短抑制，实际 {suppress_window}s"
    # 5min 后冷却应自然解除
    monkeypatch.setattr(sr.time, 'time', lambda: fixed_now + sr.MINI_GAME_INVITE_LATER_SUPPRESS_SECONDS + 1)
    assert sr._mini_game_invite_in_cooldown(LANLAN) is False


def test_advance_response_does_not_flip_when_last_user_msg_predates_invite():
    """用户 last msg 早于邀请投递时间（投完一直没说话）→ 保留 pending。"""
    now = time.time()
    delivered_at = now - 5
    state = sr._mini_game_invite_get_state(LANLAN)
    state['delivered_at'] = delivered_at
    state['responded_at'] = None

    # 用户 100s 前说了话，远早于 5s 前的投递
    sr._mini_game_invite_advance_response(LANLAN, now - 100.0)
    assert state['responded_at'] is None


# ─────────────────────────────────────────────────────────────────────────────
# _mini_game_invite_count_post_response_chat
# ─────────────────────────────────────────────────────────────────────────────

def test_count_noop_when_never_delivered():
    sr._mini_game_invite_count_post_response_chat(LANLAN)
    assert LANLAN not in sr._mini_game_invite_state


def test_count_noop_during_pending():
    """投递了但没回应（pending）→ counter 不推进，不能靠"邀请自身"耗掉 10 次。"""
    sr._mini_game_invite_record_delivered(LANLAN, "test-session-id")
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
async def test_maybe_deliver_returns_none_when_unfinished_thread_pending(monkeypatch):
    """AI 刚抛了问题用户还没接 → 跟进 thread 优先于 mini-game 邀请。
    与 skip_probability / restricted_screen_only 对 unfinished_thread 的优先级
    约定对齐——promised follow-up 永远不让外部 source / 邀请抢走。"""
    monkeypatch.setattr(sr, 'MINI_GAME_INVITE_TRIGGER_PROBABILITY', 1.0)
    fake_thread = types.SimpleNamespace(
        text='你今天准备几点出发?', age_seconds=60.0,
        follow_up_count=0, max_follow_ups=2,
    )
    out = await sr._maybe_deliver_mini_game_invite(
        lanlan_name=LANLAN, mgr=_make_mgr(),
        activity_snapshot=_make_snapshot(unfinished_thread=fake_thread),
        invite_lang='zh', master_name=MASTER,
    )
    assert out is None


@pytest.mark.asyncio
async def test_maybe_deliver_returns_none_when_in_cooldown(monkeypatch):
    """pending 期间一律抑制掷骰。"""
    monkeypatch.setattr(sr, 'MINI_GAME_INVITE_TRIGGER_PROBABILITY', 1.0)
    sr._mini_game_invite_record_delivered(LANLAN, "test-session-id")
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

def test_invite_lines_cover_all_native_locales_per_game():
    """每个 ``MINI_GAME_INVITE_AVAILABLE_GAMES`` 里列的 game_type 都必须在
    ``MINI_GAME_INVITE_LINES_BY_GAME`` 里有 5 个 native locale 的可格式化模板。
    这条契约是多游戏拓展的「门槛」——加新游戏忘了补对应 locale，line lookup
    会落 _loc 兜底（zh），其它 locale 用户看到中文邀请。"""
    from config import MINI_GAME_INVITE_AVAILABLE_GAMES
    from config.prompts_proactive import MINI_GAME_INVITE_LINES_BY_GAME
    assert MINI_GAME_INVITE_AVAILABLE_GAMES, "AVAILABLE_GAMES 不能空"
    for game in MINI_GAME_INVITE_AVAILABLE_GAMES:
        assert game in MINI_GAME_INVITE_LINES_BY_GAME, \
            f"AVAILABLE_GAMES 列了 {game!r} 但 LINES 里没；多游戏接口契约破了"
        lines = MINI_GAME_INVITE_LINES_BY_GAME[game]
        for lang in ('zh', 'en', 'ja', 'ko', 'ru'):
            line = lines.get(lang)
            assert line, f"{game!r} 缺 {lang!r} 模板"
            assert '{master_name}' in line, f"{game!r}/{lang} 缺 master_name 占位符"
            rendered = line.format(master_name='测试')
            assert '测试' in rendered
            assert 5 <= len(line) <= 200, f"{game!r}/{lang} 模板长度异常: {len(line)}"


def test_format_recent_proactive_chats_renders_mini_game_channel():
    """Runtime 渲染契约：成功投递的 mini_game 邀请被 _record_proactive_chat
    写进 _proactive_chat_history 后，下一轮 proactive 的 prompt 由
    _format_recent_proactive_chats 拼出近期搭话段——这条记录必须能渲染出
    可读的「时间 · 通道」标签，并且至少在 5 个 native locale 下不崩。

    通道 label 走 RECENT_PROACTIVE_CHANNEL_LABELS（不是 PROACTIVE_SOURCE_LABELS！
    后者只在 Phase 1 web 聚合时用，mini_game 短路在 Phase 1 之前不会触达）。
    现 dict 只为 vision/web 提供翻译，music/meme/news/video/home/personal/
    window/mini_game 这些都走 ``cl.get(ch, ch)`` raw-key fallback，所以期望
    输出里直接出现 'mini_game' 字面量——和 music/meme 现状一致。"""
    sample = "{master_name}, ".format(master_name=MASTER) + "要不要踢一会儿足球？"
    sr._proactive_chat_history[LANLAN] = __import__('collections').deque(
        [(time.time() - 30, sample, 'mini_game')], maxlen=10,
    )
    for lang in ('zh', 'en', 'ja', 'ko', 'ru'):
        rendered = sr._format_recent_proactive_chats(LANLAN, lang)
        assert rendered, f"{lang} 渲染输出空"
        assert sample in rendered, f"{lang} 渲染丢了消息正文"
        assert 'mini_game' in rendered, (
            f"{lang} 渲染丢了 channel 标记——"
            f"应至少用 raw-key fallback 暴露 mini_game"
        )


@pytest.mark.asyncio
async def test_invite_e2e_renders_in_recent_chats(monkeypatch):
    """端到端：_maybe_deliver_mini_game_invite 投出来的内容立刻被
    _format_recent_proactive_chats 拼回来，确认整条链路跑通且 channel 标签生效。"""
    monkeypatch.setattr(sr, 'MINI_GAME_INVITE_TRIGGER_PROBABILITY', 1.0)
    mgr = _make_mgr()
    out = await sr._maybe_deliver_mini_game_invite(
        lanlan_name=LANLAN, mgr=mgr,
        activity_snapshot=_make_snapshot(),
        invite_lang='zh', master_name=MASTER,
    )
    assert out is not None
    rendered = sr._format_recent_proactive_chats(LANLAN, 'zh')
    assert MASTER in rendered
    assert 'mini_game' in rendered


# ─────────────────────────────────────────────────────────────────────────────
# Force-first 路径（新用户在第 N 次主动搭话强制邀请）
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_force_first_triggers_when_new_user_at_threshold(monkeypatch):
    """新用户（state.delivered_at is None）+ 持久化 total >= NEW_USER_FORCE_AT - 1
    → 即便 trigger_probability=0 也强制走邀请。这是 spec「第 N 次主动搭话固定
    邀请」的核心契约。"""
    monkeypatch.setattr(sr, 'MINI_GAME_INVITE_TRIGGER_PROBABILITY', 0.0)
    # 默认 NEW_USER_FORCE_AT = 4 → total >= 3 触发
    monkeypatch.setattr(sr, 'MINI_GAME_INVITE_NEW_USER_FORCE_AT', 4)
    sr._proactive_chat_totals[LANLAN] = 3  # 已成功投递过 3 次普通主动搭话

    out = await sr._maybe_deliver_mini_game_invite(
        lanlan_name=LANLAN, mgr=_make_mgr(),
        activity_snapshot=_make_snapshot(),
        invite_lang='zh', master_name=MASTER,
    )
    assert out is not None
    assert out["action"] == "chat"
    assert out["force_first"] is True
    assert out["game_type"] in sr.MINI_GAME_INVITE_AVAILABLE_GAMES


@pytest.mark.asyncio
async def test_force_first_skipped_when_total_below_threshold(monkeypatch):
    """total < threshold → force-first 不生效，dice=0 时返回 None。"""
    monkeypatch.setattr(sr, 'MINI_GAME_INVITE_TRIGGER_PROBABILITY', 0.0)
    monkeypatch.setattr(sr, 'MINI_GAME_INVITE_NEW_USER_FORCE_AT', 4)
    sr._proactive_chat_totals[LANLAN] = 2  # 还差一次

    out = await sr._maybe_deliver_mini_game_invite(
        lanlan_name=LANLAN, mgr=_make_mgr(),
        activity_snapshot=_make_snapshot(),
        invite_lang='zh', master_name=MASTER,
    )
    assert out is None  # 不到第 4 次 + dice=0 → 不发


@pytest.mark.asyncio
async def test_force_first_skipped_when_ever_delivered_persistent_flag_set(monkeypatch):
    """``_invite_ever_delivered`` 持久化标记一旦置 True → force-first 不再生效，
    回归普通 10% 掷骰路径。这是 "is new user" 的真正判定，跟 in-memory 的
    ``state.delivered_at`` 完全独立。"""
    monkeypatch.setattr(sr, 'MINI_GAME_INVITE_TRIGGER_PROBABILITY', 0.0)
    monkeypatch.setattr(sr, 'MINI_GAME_INVITE_NEW_USER_FORCE_AT', 4)
    sr._invite_ever_delivered[LANLAN] = True  # 历史上发过邀请
    sr._proactive_chat_totals[LANLAN] = 99

    out = await sr._maybe_deliver_mini_game_invite(
        lanlan_name=LANLAN, mgr=_make_mgr(),
        activity_snapshot=_make_snapshot(),
        invite_lang='zh', master_name=MASTER,
    )
    assert out is None  # 老用户 + dice=0 → 不发


@pytest.mark.asyncio
async def test_force_first_skipped_after_simulated_restart(monkeypatch):
    """关键回归：codex P1 / CodeRabbit Major 指出的 cross-restart bug——
    重启后 ``_mini_game_invite_state`` 是空 dict，但 ``_proactive_chat_totals``
    与 ``_invite_ever_delivered`` 都从持久化文件加载回来。已经被邀请过的用户
    不应再被 force-first 当新用户重新强制邀请。"""
    monkeypatch.setattr(sr, 'MINI_GAME_INVITE_TRIGGER_PROBABILITY', 0.0)
    monkeypatch.setattr(sr, 'MINI_GAME_INVITE_NEW_USER_FORCE_AT', 4)
    # 模拟"重启后"的状态：state 空（in-memory 清零），但持久化两份都还在
    sr._mini_game_invite_state.clear()
    sr._proactive_chat_totals[LANLAN] = 99
    sr._invite_ever_delivered[LANLAN] = True  # 重启前已发过

    out = await sr._maybe_deliver_mini_game_invite(
        lanlan_name=LANLAN, mgr=_make_mgr(),
        activity_snapshot=_make_snapshot(),
        invite_lang='zh', master_name=MASTER,
    )
    assert out is None, (
        "重启后已邀请过的用户被 force-first 重新邀请——"
        "force-first 检查必须基于持久化的 _invite_ever_delivered，"
        "不能基于 in-memory 的 state.delivered_at"
    )


@pytest.mark.asyncio
async def test_invite_marks_ever_delivered_persistent(monkeypatch):
    """成功投递后必须把 _invite_ever_delivered 置 True（持久化），用于
    跨重启识别"已邀请过的用户"防止 force-first 重复触发。"""
    monkeypatch.setattr(sr, 'MINI_GAME_INVITE_TRIGGER_PROBABILITY', 1.0)
    assert not sr._was_invite_ever_delivered(LANLAN)

    out = await sr._maybe_deliver_mini_game_invite(
        lanlan_name=LANLAN, mgr=_make_mgr(),
        activity_snapshot=_make_snapshot(),
        invite_lang='zh', master_name=MASTER,
    )
    assert out is not None
    assert sr._was_invite_ever_delivered(LANLAN), (
        "投递成功但 ever_delivered 没置 True——下次 force-first 会重复触发"
    )


@pytest.mark.asyncio
async def test_invite_delivery_uses_atomic_persistence_helper(monkeypatch):
    """关键回归：邀请投递必须走 _record_invite_delivery_persistent 一把锁原子
    写盘，不能拆成 _increment_proactive_chat_total + _mark_invite_ever_delivered
    两次独立 await——两次 await 之间 lock 释放，进程崩溃会留下 totals 已 +1 但
    ever_delivered 旧值的中间态，重启后 force-first 重复 fire。CodeRabbit Major
    review 指出。"""
    monkeypatch.setattr(sr, 'MINI_GAME_INVITE_TRIGGER_PROBABILITY', 1.0)

    increment_calls: list[str] = []
    mark_calls: list[str] = []
    record_calls: list[str] = []

    async def _counting_increment(lanlan_name: str) -> int:
        increment_calls.append(lanlan_name)
        n = sr._proactive_chat_totals.get(lanlan_name, 0) + 1
        sr._proactive_chat_totals[lanlan_name] = n
        return n

    async def _counting_mark(lanlan_name: str) -> None:
        mark_calls.append(lanlan_name)
        sr._invite_ever_delivered[lanlan_name] = True

    async def _counting_record(lanlan_name: str) -> int:
        record_calls.append(lanlan_name)
        n = sr._proactive_chat_totals.get(lanlan_name, 0) + 1
        sr._proactive_chat_totals[lanlan_name] = n
        sr._invite_ever_delivered[lanlan_name] = True
        return n

    monkeypatch.setattr(sr, '_increment_proactive_chat_total', _counting_increment)
    monkeypatch.setattr(sr, '_mark_invite_ever_delivered', _counting_mark)
    monkeypatch.setattr(sr, '_record_invite_delivery_persistent', _counting_record)

    out = await sr._maybe_deliver_mini_game_invite(
        lanlan_name=LANLAN, mgr=_make_mgr(),
        activity_snapshot=_make_snapshot(),
        invite_lang='zh', master_name=MASTER,
    )
    assert out is not None

    assert record_calls == [LANLAN], (
        f"_record_invite_delivery_persistent should be called once, "
        f"got {record_calls}"
    )
    assert increment_calls == [], (
        f"_increment_proactive_chat_total should NOT be called from invite "
        f"delivery, got {increment_calls}——拆成两步的 racy pattern 被复活了"
    )
    assert mark_calls == [], (
        f"_mark_invite_ever_delivered should NOT be called from invite "
        f"delivery, got {mark_calls}——拆成两步的 racy pattern 被复活了"
    )


@pytest.mark.asyncio
async def test_force_first_still_respects_unfinished_thread(monkeypatch):
    """force-first 优先级低于 unfinished_thread——AI 刚问完用户没接的轮次
    不允许换话题，即便是新用户的固定第 4 次也得让位。"""
    monkeypatch.setattr(sr, 'MINI_GAME_INVITE_TRIGGER_PROBABILITY', 1.0)
    monkeypatch.setattr(sr, 'MINI_GAME_INVITE_NEW_USER_FORCE_AT', 4)
    sr._proactive_chat_totals[LANLAN] = 3
    fake_thread = types.SimpleNamespace(
        text='你今天准备几点出发?', age_seconds=60.0,
        follow_up_count=0, max_follow_ups=2,
    )
    out = await sr._maybe_deliver_mini_game_invite(
        lanlan_name=LANLAN, mgr=_make_mgr(),
        activity_snapshot=_make_snapshot(unfinished_thread=fake_thread),
        invite_lang='zh', master_name=MASTER,
    )
    assert out is None


@pytest.mark.asyncio
async def test_force_first_still_respects_restricted_screen_only(monkeypatch):
    """force-first 也要让位 propensity=restricted_screen_only——用户在工作 /
    沉浸 gaming 时强制塞邀请反而打扰。"""
    monkeypatch.setattr(sr, 'MINI_GAME_INVITE_TRIGGER_PROBABILITY', 1.0)
    monkeypatch.setattr(sr, 'MINI_GAME_INVITE_NEW_USER_FORCE_AT', 4)
    sr._proactive_chat_totals[LANLAN] = 3
    out = await sr._maybe_deliver_mini_game_invite(
        lanlan_name=LANLAN, mgr=_make_mgr(),
        activity_snapshot=_make_snapshot(
            state='focused_work', propensity='restricted_screen_only',
        ),
        invite_lang='zh', master_name=MASTER,
    )
    assert out is None


# ─────────────────────────────────────────────────────────────────────────────
# 多游戏接口契约（C：跨 game_type 共享冷却）
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_invite_outcome_includes_game_type(monkeypatch):
    """投递成功 → outcome dict 必须带 game_type，让 caller 知道前端应该开哪个
    游戏（PR-B 的「好」按钮用）。state.last_game_type 也写上，跨进程下次
    proactive_chat 还能查到上次邀请发的什么。"""
    monkeypatch.setattr(sr, 'MINI_GAME_INVITE_TRIGGER_PROBABILITY', 1.0)
    out = await sr._maybe_deliver_mini_game_invite(
        lanlan_name=LANLAN, mgr=_make_mgr(),
        activity_snapshot=_make_snapshot(),
        invite_lang='zh', master_name=MASTER,
    )
    assert out is not None
    assert out['game_type'] in sr.MINI_GAME_INVITE_AVAILABLE_GAMES
    state = sr._mini_game_invite_state[LANLAN]
    assert state['last_game_type'] == out['game_type']


@pytest.mark.asyncio
async def test_invite_skipped_when_no_game_available(monkeypatch):
    """配置错位（AVAILABLE_GAMES 列出但 LINES 没对应 key）→ 静默不发，
    不应抛异常或发空字符串。多游戏拓展时这是 defensive guard。"""
    monkeypatch.setattr(sr, 'MINI_GAME_INVITE_TRIGGER_PROBABILITY', 1.0)
    monkeypatch.setattr(sr, 'MINI_GAME_INVITE_AVAILABLE_GAMES', ('nonexistent_game',))
    out = await sr._maybe_deliver_mini_game_invite(
        lanlan_name=LANLAN, mgr=_make_mgr(),
        activity_snapshot=_make_snapshot(),
        invite_lang='zh', master_name=MASTER,
    )
    assert out is None
    assert LANLAN not in sr._mini_game_invite_state


def test_cooldown_is_1h_by_default():
    """spec 改动：24h → 1h。常量值是后续 PR 调整的锚点，pin 住避免回归。"""
    assert sr.MINI_GAME_INVITE_COOLDOWN_SECONDS == 3600


def test_new_user_force_at_is_4_by_default():
    """spec：「从未玩过的用户固定在开场第 4 次主动搭话邀请」。
    pin 住默认值，未来调整由 follow-up 显式翻。"""
    assert sr.MINI_GAME_INVITE_NEW_USER_FORCE_AT == 4


def test_later_suppress_seconds_is_5min_by_default():
    """spec D2：「回头再说」reset state 后 5min 内不再 roll。"""
    assert sr.MINI_GAME_INVITE_LATER_SUPPRESS_SECONDS == 5 * 60


# ─────────────────────────────────────────────────────────────────────────────
# D2 短期抑制（回头再说）
# ─────────────────────────────────────────────────────────────────────────────

def test_in_cooldown_true_when_suppressed_until_in_future():
    """suppressed_until > now → cooldown 锁住，无论 delivered_at 状态。
    这是 D2「回头再说」reset 后的窗口语义。"""
    state = sr._mini_game_invite_get_state(LANLAN)
    state['delivered_at'] = None  # reset 已发生
    state['responded_at'] = None
    state['chats_since_response'] = 0
    state['suppressed_until'] = time.time() + 60
    assert sr._mini_game_invite_in_cooldown(LANLAN) is True


def test_in_cooldown_false_when_suppressed_until_past():
    """suppressed_until <= now → 抑制窗口已过，cooldown 看 delivered_at。
    delivered_at=None（reset 状态）→ 不在 cooldown，下一轮 proactive 重新掷骰。"""
    state = sr._mini_game_invite_get_state(LANLAN)
    state['delivered_at'] = None
    state['responded_at'] = None
    state['chats_since_response'] = 0
    state['suppressed_until'] = time.time() - 1
    assert sr._mini_game_invite_in_cooldown(LANLAN) is False


def test_record_delivered_clears_suppressed_until():
    """新一次邀请投递清掉旧的 D2 短期抑制窗口——既然又投了新邀请，
    那个「回头再说」的等待窗口就过期了。"""
    state = sr._mini_game_invite_get_state(LANLAN)
    state['suppressed_until'] = time.time() + 999
    sr._mini_game_invite_record_delivered(LANLAN, "new-session")
    assert state['suppressed_until'] is None


def test_record_delivered_sets_pending_session_id():
    """新一次投递必须刷新 pending_session_id，让旧 invite session 过期；
    endpoint 收到旧 session_id 时返 expired。"""
    state = sr._mini_game_invite_get_state(LANLAN)
    state['pending_session_id'] = 'old-session'
    sr._mini_game_invite_record_delivered(LANLAN, 'new-session')
    assert state['pending_session_id'] == 'new-session'


# ─────────────────────────────────────────────────────────────────────────────
# _apply_mini_game_invite_choice (state machine)
# ─────────────────────────────────────────────────────────────────────────────

def test_apply_choice_ignored_when_no_pending_invite():
    """没 pending invite → 任何 choice 都返 ignored，不破坏 state。"""
    result = sr._apply_mini_game_invite_choice(LANLAN, 'accept', source='test')
    assert result['action'] == 'ignored'
    assert result.get('reason') == 'no_pending_invite'


def test_apply_choice_ignored_when_already_responded():
    """已经 responded → 重复点击按钮被 ignored。"""
    state = sr._mini_game_invite_get_state(LANLAN)
    state['delivered_at'] = time.time() - 60
    state['responded_at'] = time.time() - 30
    state['pending_session_id'] = 'sess'
    result = sr._apply_mini_game_invite_choice(LANLAN, 'accept', source='test')
    assert result['action'] == 'ignored'


def test_apply_choice_accept_returns_open_game_with_url():
    """accept → 返回 game_url（带 lanlan_name + session_id query）+ 顶层
    session_id 字段（前端 dedupe key），state 翻成 responded（启动 1h+10 chats
    冷却）。"""
    state = sr._mini_game_invite_get_state(LANLAN)
    state['delivered_at'] = time.time() - 30
    state['responded_at'] = None
    state['pending_session_id'] = 'sess-abc'
    state['last_game_type'] = 'soccer'
    result = sr._apply_mini_game_invite_choice(LANLAN, 'accept', source='button')
    assert result['action'] == 'open_game'
    assert result['game_type'] == 'soccer'
    assert result['game_url'].startswith('/soccer_demo?')
    assert 'lanlan_name=' + LANLAN in result['game_url']
    assert 'session_id=sess-abc' in result['game_url']
    # 顶层 session_id 必须有，core.py keyword 路径推 mini_game_launch WS 时
    # 把它放进 payload，让前端 dedupe set 跨「按钮 endpoint」/「keyword WS」
    # 两路都用同一 key，避免双开窗口（codex P2 指出）。
    assert result['session_id'] == 'sess-abc'
    # state 进 cooldown
    assert state['responded_at'] is not None
    assert state['chats_since_response'] == 0


def test_apply_choice_decline_starts_cooldown_no_url():
    """decline → mark responded（同样启动冷却），不开游戏。"""
    state = sr._mini_game_invite_get_state(LANLAN)
    state['delivered_at'] = time.time() - 30
    state['responded_at'] = None
    state['pending_session_id'] = 'sess'
    result = sr._apply_mini_game_invite_choice(LANLAN, 'decline', source='button')
    assert result['action'] == 'cooldown'
    assert 'game_url' not in result
    assert state['responded_at'] is not None


def test_apply_choice_later_resets_state_with_suppression():
    """later (D2) → 完全 reset delivered_at 等，但加 suppressed_until = now+5min。"""
    state = sr._mini_game_invite_get_state(LANLAN)
    state['delivered_at'] = time.time() - 30
    state['responded_at'] = None
    state['chats_since_response'] = 0
    state['pending_session_id'] = 'sess'
    before = time.time()
    result = sr._apply_mini_game_invite_choice(LANLAN, 'later', source='button')
    assert result['action'] == 'suppress'
    assert state['delivered_at'] is None
    assert state['responded_at'] is None
    assert state['chats_since_response'] == 0
    assert state['pending_session_id'] is None
    assert state['suppressed_until'] is not None
    suppress_window = state['suppressed_until'] - before
    assert (
        sr.MINI_GAME_INVITE_LATER_SUPPRESS_SECONDS - 5
        <= suppress_window
        <= sr.MINI_GAME_INVITE_LATER_SUPPRESS_SECONDS + 5
    )


# ─────────────────────────────────────────────────────────────────────────────
# 关键词匹配（E2 文本兜底）
# ─────────────────────────────────────────────────────────────────────────────

def test_keyword_matcher_returns_none_for_empty_text():
    assert sr._match_mini_game_invite_keyword('') is None
    assert sr._match_mini_game_invite_keyword('   ') is None
    assert sr._match_mini_game_invite_keyword(None) is None


def test_keyword_matcher_accept_zh():
    assert sr._match_mini_game_invite_keyword('好啊') == 'accept'
    assert sr._match_mini_game_invite_keyword('来吧') == 'accept'
    assert sr._match_mini_game_invite_keyword('一起玩吧') == 'accept'


def test_keyword_matcher_no_false_positive_on_chong_substring():
    """codex P2 回归保护：单字 '冲' 已从 zh accept 删除——
    "我去冲个澡" / "冲咖啡" 等普通对话不应命中 accept（CJK 走 substring 没
    word boundary 兜底）。"""
    assert sr._match_mini_game_invite_keyword('我去冲个澡') is None
    assert sr._match_mini_game_invite_keyword('在冲咖啡') is None
    # 但完整 phrase 「来吧」仍命中 accept
    assert sr._match_mini_game_invite_keyword('来吧') == 'accept'


def test_keyword_matcher_no_false_positive_on_keyi_negation():
    """codex P2 回归保护：'可以' 已从 zh accept 删除（"不可以" 含 substring
    '可以'，decline 没 '不可以' 时 priority 救不了）。同时 '不可以' 加进 decline
    list 双保险——以防未来误把 '可以' 加回 accept。"""
    # "不可以" 必须命中 decline，绝不能 accept
    assert sr._match_mini_game_invite_keyword('不可以') == 'decline'
    assert sr._match_mini_game_invite_keyword('不可以的') == 'decline'
    # 单独 "可以" 不再命中（用户表达接受意图请改用 '好啊'/'行啊'/'来吧' 等）
    assert sr._match_mini_game_invite_keyword('可以') is None


def test_keyword_matcher_no_false_positive_on_korean_eung():
    """codex P2 回归保护：单字 '응' 已从 ko accept 删除——"적응" / "반응" /
    "응답" 等含 '응' 子串的常规韩语词不应命中 accept。"""
    assert sr._match_mini_game_invite_keyword('적응 중이야') is None
    assert sr._match_mini_game_invite_keyword('반응이 좋네') is None
    # 双字 phrase '좋아' 仍命中
    assert sr._match_mini_game_invite_keyword('좋아') == 'accept'


def test_keyword_matcher_accept_does_not_match_negation():
    """关键词列表配合 priority 双保险：'不好'/'我不行' 不能被 accept 误命中。
    accept 现在用「好啊/好的/行啊」短语，'不好' 不含 substring '好啊'；同时
    priority decline > accept 兜底——任一保护失效另一个仍 catch。"""
    # 从 substring 层面：'不好' / '我不行' 不含 accept 短语
    assert sr._match_mini_game_invite_keyword('不好') == 'decline'
    assert sr._match_mini_game_invite_keyword('我不行') == 'decline'
    assert sr._match_mini_game_invite_keyword('不好玩') == 'decline'


def test_keyword_matcher_accept_en():
    assert sr._match_mini_game_invite_keyword('yes please') == 'accept'
    assert sr._match_mini_game_invite_keyword("Let's go!") == 'accept'
    assert sr._match_mini_game_invite_keyword('OKAY') == 'accept'


def test_keyword_matcher_decline_zh():
    assert sr._match_mini_game_invite_keyword('不要') == 'decline'
    assert sr._match_mini_game_invite_keyword('算了吧') == 'decline'
    assert sr._match_mini_game_invite_keyword('不想玩') == 'decline'


def test_keyword_matcher_later_zh():
    assert sr._match_mini_game_invite_keyword('回头说') == 'later'
    assert sr._match_mini_game_invite_keyword('等会') == 'later'
    assert sr._match_mini_game_invite_keyword('晚点吧') == 'later'


def test_keyword_matcher_priority_decline_over_later_over_accept():
    """priority 是 decline > later > accept（CodeRabbit Major review 后调整）。
    含 negation 的句子绝不能因 accept substring 凑巧命中就反向触发开游戏；
    含 later 信号的句子优先于纯 accept。"""
    # 'decline' > 'later'：含 '不要' (decline) + '回头' (later) → decline
    assert sr._match_mini_game_invite_keyword('不要，回头说吧') == 'decline'
    # 'later' > 'accept'：含 '好的' (accept) + '等下' (later) → later
    # （用户语义"接受但等等"，later 比 accept 准——别立刻开游戏）
    assert sr._match_mini_game_invite_keyword('好的等下') == 'later'
    # 'decline' > 'accept'：含 '不行' (decline) + '可以' (accept)
    assert sr._match_mini_game_invite_keyword('不行吧，但可以下次') == 'decline'


def test_keyword_matcher_decline_via_phrase():
    """「我现在没空」通过 '没空' phrase 命中 decline——pin 住关键词契约，
    防止 '没空' 被从列表里误删（CodeRabbit Minor 指出原版 'None or decline'
    断言放松过头，把"误命中回归"也放过去了）。"""
    result = sr._match_mini_game_invite_keyword('我现在没空')
    assert result == 'decline'


def test_keyword_matcher_no_false_positive_on_common_english_phrases():
    """codex P1 + CodeRabbit Major：英文 'no' 已从 decline 列表删除——
    "I have no idea" / "no worries" / "know" 这些常规英文不应命中 decline。
    pending invite 期间用户随口说这些不该启动 1h 长冷却。"""
    assert sr._match_mini_game_invite_keyword('i have no idea') is None
    assert sr._match_mini_game_invite_keyword('no worries') is None
    assert sr._match_mini_game_invite_keyword('i know what you mean') is None
    # 'no thanks' 是明确拒绝 phrase，应仍命中
    assert sr._match_mini_game_invite_keyword('no thanks') == 'decline'


def test_maybe_apply_keyword_noop_when_no_pending():
    """没 pending invite → keyword matcher hook 直接 None，不动 state。"""
    result = sr._maybe_apply_mini_game_invite_keyword(LANLAN, '好啊')
    assert result is None


def test_maybe_apply_keyword_accept_returns_open_game():
    """pending invite + accept 关键词 → 触发 state 转换，返回 open_game。"""
    state = sr._mini_game_invite_get_state(LANLAN)
    state['delivered_at'] = time.time() - 30
    state['responded_at'] = None
    state['pending_session_id'] = 'kw-sess'
    state['last_game_type'] = 'soccer'
    result = sr._maybe_apply_mini_game_invite_keyword(LANLAN, '好啊')
    assert result is not None
    assert result['action'] == 'open_game'
    assert state['responded_at'] is not None  # mark responded


def test_maybe_apply_keyword_decline_starts_cooldown():
    state = sr._mini_game_invite_get_state(LANLAN)
    state['delivered_at'] = time.time() - 30
    state['responded_at'] = None
    state['pending_session_id'] = 'kw-sess'
    result = sr._maybe_apply_mini_game_invite_keyword(LANLAN, '不要')
    assert result is not None
    assert result['action'] == 'cooldown'
    assert state['responded_at'] is not None


def test_maybe_apply_keyword_later_resets_state():
    state = sr._mini_game_invite_get_state(LANLAN)
    state['delivered_at'] = time.time() - 30
    state['responded_at'] = None
    state['pending_session_id'] = 'kw-sess'
    result = sr._maybe_apply_mini_game_invite_keyword(LANLAN, '回头再说')
    assert result is not None
    assert result['action'] == 'suppress'
    assert state['delivered_at'] is None
    assert state['suppressed_until'] is not None


def test_keyword_matcher_no_false_positive_on_substring_words():
    """codex P1：英文短词 'yes' / 'no' / 'okay' 必须 word-boundary 匹配，不能
    被 'yesterday' / 'no idea' / 'book' 这种 substring 凑巧命中。"""
    # 'yes' 不该命中 'yesterday'
    assert sr._match_mini_game_invite_keyword('yesterday i talked to him') is None
    # 'no' 不该命中 'no idea' —— '"no idea"' 的 'no' 是单独 token，应该命中 decline
    # 让我们换个例子：'know' 含 'no' 子串，但 'no' 应该 word-boundary 不命中 'know'
    assert sr._match_mini_game_invite_keyword('i know what you mean') is None
    # 'sure' 是单独词时命中 accept
    assert sr._match_mini_game_invite_keyword('sure thing') == 'accept'
    # 'sure' 不应命中 'pressure' 这种含 substring 的（虽 'sure' 在 'pressure' 中）
    assert sr._match_mini_game_invite_keyword('feeling pressure') is None


@pytest.mark.asyncio
async def test_button_endpoint_pushes_resolved_ws_for_all_actions(monkeypatch):
    """codex P2：endpoint 处理 accept / decline / later 都必须 push
    mini_game_invite_resolved WS event让所有 page 同步 dismiss prompt
    UI（cross-window 一致性）。原版只对 accept push mini_game_launch，
    decline / later 用户在另一窗口看着按钮挂着。"""
    state = sr._mini_game_invite_get_state(LANLAN)
    state['delivered_at'] = time.time() - 30
    state['responded_at'] = None
    state['pending_session_id'] = 'btn-sess'
    state['last_game_type'] = 'soccer'

    # 模拟 mgr + websocket
    mgr = MagicMock()
    mgr.websocket = MagicMock()
    mgr.websocket.send_json = AsyncMock()
    fake_state = MagicMock()
    fake_state.CONNECTED = fake_state
    mgr.websocket.client_state = fake_state

    await sr._push_mini_game_invite_resolved(
        mgr, session_id='btn-sess', action='cooldown',
    )
    mgr.websocket.send_json.assert_awaited_once()
    payload = mgr.websocket.send_json.await_args.args[0]
    assert payload['type'] == 'mini_game_invite_resolved'
    assert payload['session_id'] == 'btn-sess'
    assert payload['action'] == 'cooldown'
    # cooldown 不带 game_url
    assert 'game_url' not in payload


@pytest.mark.asyncio
async def test_push_resolved_includes_game_url_for_open_game(monkeypatch):
    """accept outcome 的 resolved WS 同时带 game_url——前端按 action=='open_game'
    + game_url 决定 window.open。单一 event 兼当 lifecycle dismiss + launch 信号。"""
    mgr = MagicMock()
    mgr.websocket = MagicMock()
    mgr.websocket.send_json = AsyncMock()
    fake_state = MagicMock()
    fake_state.CONNECTED = fake_state
    mgr.websocket.client_state = fake_state

    await sr._push_mini_game_invite_resolved(
        mgr,
        session_id='accept-sess',
        action='open_game',
        game_url='/soccer_demo?lanlan_name=foo&session_id=accept-sess',
        game_type='soccer',
    )
    payload = mgr.websocket.send_json.await_args.args[0]
    assert payload['action'] == 'open_game'
    assert payload['game_url'].startswith('/soccer_demo?')
    assert payload['game_type'] == 'soccer'


@pytest.mark.asyncio
async def test_push_resolved_noop_without_session_id():
    """空 session_id → no-op（防止误广播过期 invite）。"""
    mgr = MagicMock()
    mgr.websocket = MagicMock()
    mgr.websocket.send_json = AsyncMock()
    await sr._push_mini_game_invite_resolved(mgr, session_id='', action='cooldown')
    mgr.websocket.send_json.assert_not_awaited()


def test_advance_response_returns_outcome_for_caller_ws_push():
    """advance_response 不再仅 mutate state，要 return result dict 让 caller
    push WS（用户隐式 dismiss 也要 cross-window 通知）。"""
    fixed_now = 1_700_020_000.0
    state = sr._mini_game_invite_get_state(LANLAN)
    state['delivered_at'] = fixed_now - 60
    state['responded_at'] = None
    state['pending_session_id'] = 'adv-sess'

    result = sr._mini_game_invite_advance_response(LANLAN, fixed_now - 30)
    assert result is not None
    assert result['action'] == 'suppress'
    assert result['session_id'] == 'adv-sess'


# ─────────────────────────────────────────────────────────────────────────────
# 邀请投递推 WS message + session_id 流转
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_invite_delivery_pushes_options_via_websocket(monkeypatch):
    """投递成功后必须 push 一条 mini_game_invite_options WS message 给前端，
    带 options + session_id + game_type。前端 ChoicePrompt 渲染依赖这条。"""
    monkeypatch.setattr(sr, 'MINI_GAME_INVITE_TRIGGER_PROBABILITY', 1.0)
    mgr = _make_mgr()
    mgr.websocket = MagicMock()
    mgr.websocket.send_json = AsyncMock()
    # mock client_state 让 send_json 通过 connectivity 检查
    fake_state = MagicMock()
    fake_state.CONNECTED = fake_state
    mgr.websocket.client_state = fake_state

    out = await sr._maybe_deliver_mini_game_invite(
        lanlan_name=LANLAN, mgr=mgr,
        activity_snapshot=_make_snapshot(),
        invite_lang='zh', master_name=MASTER,
    )
    assert out is not None and out['action'] == 'chat'
    mgr.websocket.send_json.assert_awaited_once()
    payload = mgr.websocket.send_json.await_args.args[0]
    assert payload['type'] == 'mini_game_invite_options'
    assert payload['session_id'] == out['invite_session_id']
    assert payload['game_type'] == 'soccer'
    assert isinstance(payload['options'], list) and len(payload['options']) == 3
    choices = [opt['choice'] for opt in payload['options']]
    assert choices == ['accept', 'decline', 'later']
    # state 同步存了 pending_session_id
    state = sr._mini_game_invite_state[LANLAN]
    assert state['pending_session_id'] == out['invite_session_id']


# ─────────────────────────────────────────────────────────────────────────────
# i18n 完整性
# ─────────────────────────────────────────────────────────────────────────────

def test_option_labels_cover_all_native_locales():
    """5 个 native locale × 3 个 choice 必须有非空 label。"""
    from config.prompts_proactive import MINI_GAME_INVITE_OPTION_LABELS
    for lang in ('zh', 'en', 'ja', 'ko', 'ru'):
        assert lang in MINI_GAME_INVITE_OPTION_LABELS, f"缺 {lang} option labels"
        labels = MINI_GAME_INVITE_OPTION_LABELS[lang]
        for choice in ('accept', 'decline', 'later'):
            assert choice in labels, f"{lang} 缺 {choice} 标签"
            assert labels[choice].strip(), f"{lang}/{choice} 标签空"


def test_keywords_cover_all_native_locales():
    """5 个 native locale × 3 个 choice 必须各有非空关键词列表。"""
    from config.prompts_proactive import MINI_GAME_INVITE_KEYWORDS
    for lang in ('zh', 'en', 'ja', 'ko', 'ru'):
        assert lang in MINI_GAME_INVITE_KEYWORDS, f"缺 {lang} keywords"
        kws = MINI_GAME_INVITE_KEYWORDS[lang]
        for choice in ('accept', 'decline', 'later'):
            assert choice in kws, f"{lang} 缺 {choice} 关键词"
            assert isinstance(kws[choice], list) and kws[choice], (
                f"{lang}/{choice} 关键词列表空"
            )
