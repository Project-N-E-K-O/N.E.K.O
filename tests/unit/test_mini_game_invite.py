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
    sr._mini_game_invite_record_delivered(LANLAN)
    sr._mini_game_invite_advance_response(LANLAN, None)
    assert sr._mini_game_invite_state[LANLAN]['responded_at'] is None


def test_advance_response_flips_when_user_spoke_after_invite(monkeypatch):
    """用户在 delivered_at 之后说过话 → 标记已回应、计数清零。

    `responded_at` 必须 anchor 到 *真实* 回应时间（last_user_msg_at），
    而不是「检测到回应的此刻」。advance_response 只在下次 proactive_chat 才跑，
    user 回完到下次 proactive 可能隔几小时；anchor 用 now 会让 24h 冷却被这段
    间隔白白拉长。

    冻结 sr.time.time —— 测试断言不依赖真实时钟、CI 拥塞下也确定。"""
    fixed_now = 1_700_000_000.0
    monkeypatch.setattr(sr.time, 'time', lambda: fixed_now)

    delivered_at = fixed_now - 30
    state = sr._mini_game_invite_get_state(LANLAN)
    state['delivered_at'] = delivered_at
    state['responded_at'] = None
    state['chats_since_response'] = 0

    # 用户 5s 前说了话 → 落在 delivered_at 之后
    sr._mini_game_invite_advance_response(LANLAN, fixed_now - 5.0)
    assert state['responded_at'] is not None
    assert state['chats_since_response'] == 0
    # Anchor 精确等于 last_user_msg_at = fixed_now - 5；冻结时钟下没有漂移，
    # 不需要容差——直接精确断言，回归到 now（=fixed_now）会立刻挂。
    assert state['responded_at'] == fixed_now - 5.0, (
        f"responded_at={state['responded_at']:.3f} 应等于 last_user_msg_at "
        f"({fixed_now - 5:.3f})；={fixed_now} 说明回归到了 now"
    )


def test_advance_response_anchors_even_when_proactive_runs_long_after_reply(monkeypatch):
    """关键回归保护：用户回完话后过 2 小时才跑下一次 proactive_chat，
    `responded_at` 必须 anchor 到 ~2 小时前的回应时刻，而不是 now。否则
    24h 冷却会被这段静默期白吃，整体冷却变成 ~26 小时，违背 spec。

    冻结 sr.time.time —— anchor 是不是真的 = last_user_msg_at 可以精确比对。"""
    fixed_now = 1_700_010_000.0
    monkeypatch.setattr(sr.time, 'time', lambda: fixed_now)

    # 模拟：邀请投递在 2h+10s 前；用户 2h 前回过话；之后一直没动；现在
    # 第二次 proactive_chat 进来终于把 advance 跑上。
    state = sr._mini_game_invite_get_state(LANLAN)
    state['delivered_at'] = fixed_now - 2 * 3600 - 10
    state['responded_at'] = None
    state['chats_since_response'] = 0

    sr._mini_game_invite_advance_response(LANLAN, fixed_now - 2 * 3600)
    assert state['responded_at'] is not None
    # 冻结时钟下应精确等于 fixed_now - 2h；回归到 now 会让 24h 冷却被多锁 2h。
    expected = fixed_now - 2 * 3600
    assert state['responded_at'] == expected, (
        f"responded_at={state['responded_at']:.1f} 应严格等于 "
        f"{expected:.1f}（fixed_now={fixed_now}），偏差说明 anchor 出错"
    )


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
