# -*- coding: utf-8 -*-
# Copyright 2025-2026 Project N.E.K.O. Team
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Mini-game invite state, policy, delivery and keyword handling."""

import logging
import re
import time
from typing import Any
from uuid import uuid4

from .state import (
    _ensure_proactive_chat_totals_loaded,
    _get_proactive_chat_total,
    _record_invite_delivery_persistent,
    _record_proactive_chat,
    _was_invite_ever_delivered,
)
from .contracts import (
    PROACTIVE_REASON_CHAT_DELIVERED,
    PROACTIVE_REASON_DELIVERY_PREEMPTED,
    PROACTIVE_REASON_PASS_DELIVERY_BUSY,
    _proactive_chat_body,
    _proactive_pass_body,
)
from config import (
    MINI_GAME_INVITE_ENABLED,
    MINI_GAME_INVITE_FORCE_GAME_TYPE,
    MINI_GAME_INVITE_TRIGGER_PROBABILITY,
    MINI_GAME_INVITE_COOLDOWN_AFTER_ACCEPT_SECONDS,
    MINI_GAME_INVITE_COOLDOWN_AFTER_DECLINE_SECONDS,
    MINI_GAME_INVITE_COOLDOWN_CHATS,
    MINI_GAME_INVITE_NEW_USER_FORCE_AT,
    MINI_GAME_INVITE_AVAILABLE_GAMES,
    MINI_GAME_INVITE_LATER_SUPPRESS_SECONDS,
    MINI_GAME_LAUNCH_URL_BY_GAME,
)
from config.prompts.prompts_sys import _loc
from config.prompts.prompts_proactive import (
    MINI_GAME_INVITE_LINES_BY_GAME, MINI_GAME_INVITE_OPTION_LABELS,
    MINI_GAME_INVITE_KEYWORDS,
)


logger = logging.getLogger(__name__)


# --- Mini-game 邀请短路状态（每角色独立）---
# {lanlan_name: {'delivered_at': float|None,
#                'responded_at': float|None,
#                'chats_since_response': int,
#                'last_game_type': str|None,
#                'last_response_choice': 'accept'|'decline'|None}}
# - delivered_at: 上次成功投递邀请的时间戳。None=从未发过。
# - responded_at: 投递后被用户回应（任何用户消息时间戳 > delivered_at）的时间。
#   pending（delivered_at!=None and responded_at=None）期间一律抑制掷骰，避免
#   邀请挂着不响应又再发第二次。
# - chats_since_response: responded_at 设上后成功投递的"普通主动搭话"次数。
#   两条件（time-by-choice 且 >= COOLDOWN_CHATS）都跨过才允许下次掷骰。
#   冷却跨 game_type 共享——每角色一个全局冷却，一次邀请 → 冷却窗内全部 mini-game
#   静默；spec 没说邀请要密集，多游戏只是丰富选项不是加密。
# - last_game_type: 上次邀请发的是哪个游戏（从 MINI_GAME_INVITE_AVAILABLE_GAMES
#   里 random.choice 出来的）；用于 PR-B 按钮判断"打开哪个游戏"。
# - last_response_choice: 上次回应是 accept 还是 decline；用于 cooldown 函数按
#   choice 取不同 SECONDS 阈值（accept=2h、decline=5h）。later/隐式 dismiss 走
#   reset 路径不会落到这里。None = 从未回应过（pending or 全新）。
# 进程内 dict，重启清零——时间+10 chats 是软冷却，重启后多发一次邀请的代价远小
# 于持久化存储引依赖的代价；与 _proactive_chat_history 同样是内存。
_mini_game_invite_state: dict[str, dict[str, Any]] = {}


# ---------- Mini-game 邀请短路状态管理 ----------
# 入口在 proactive_chat 内部、过完 propensity / skip_probability /
# restricted_screen_only 几道门之后调 _maybe_deliver_mini_game_invite。命中
# 即静态 i18n 模板 → feed_tts_chunk + finish_proactive_delivery 直投递；不走
# Phase 1/2 LLM。冷却语义：一次邀请被回应后，必须同时跨过
#   ``time.time() - responded_at >= threshold_by_choice``
#     其中 threshold = MINI_GAME_INVITE_COOLDOWN_AFTER_ACCEPT_SECONDS (2h) 若
#     last_response_choice='accept'，否则 ..._AFTER_DECLINE_SECONDS (5h)；
# 与
#   ``chats_since_response >= MINI_GAME_INVITE_COOLDOWN_CHATS``
# 才允许下次掷骰。pending（投递了但还没被回应）期间一律抑制，避免邀请挂着
# 不响应又再发第二次。

def _mini_game_invite_get_state(lanlan_name: str) -> dict[str, Any]:
    """Lazy-init per-character state。"""
    state = _mini_game_invite_state.get(lanlan_name)
    if state is None:
        state = {
            'delivered_at': None,
            'responded_at': None,
            'chats_since_response': 0,
            'last_game_type': None,
            'response_cooldowns': {},
            # 当前 pending 邀请的 session_id；endpoint 收到回应时校验匹配，避免
            # 用户点击过期邀请被错算成响应当前 pending。一旦投递新邀请会被刷新。
            'pending_session_id': None,
            # D2「回头再说」短期抑制：reset 后不允许下一次 proactive 立刻又掷骰。
            # _in_cooldown 多查一道这个 gate。秒级 epoch；None = 不抑制。
            'suppressed_until': None,
            # accept/decline 走不同 cooldown 阈值。later/隐式 dismiss reset 时
            # 清回 None。pending 期间也是 None；cooldown 函数只在 responded_at
            # !=None 时读它。
            'last_response_choice': None,
        }
        _mini_game_invite_state[lanlan_name] = state
    else:
        state.setdefault('response_cooldowns', {})
    return state


def _mini_game_invite_advance_response(
    lanlan_name: str, last_user_msg_at: float | None,
) -> dict[str, Any] | None:
    """During a pending invite the user sent any ordinary message (not an explicit
    choice / keyword hit) → silently dismiss the prompt + 5min short suppression,
    **without** starting the long cooldown.

    Returns: a dict with the same shape as ``_apply_mini_game_invite_choice``
    (containing ``action='suppress'`` + ``session_id``); the caller uses it to
    push the ``mini_game_invite_resolved`` WS event so the frontend dismisses the
    UI. Returns None when there is nothing to do.

    Called once on every entry into proactive_chat (both the voice fast path and
    the text path). last_user_msg_at is "the timestamp of the user's last
    activity" — the caller is responsible for deriving it from the right source:
    the text path back-computes it from activity_snapshot.seconds_since_user_msg;
    the voice path uses mgr.last_user_activity_time directly (voice does not go
    through the activity tracker, but the session itself tracks RMS / text input
    activity). If either is missing (None), this is a noop.

    Difference between historical and current semantics (changed after CodeRabbit
    Major):
    - Old PR #1141 era: no ChoicePrompt; "the user spoke after the invite" =
      "implicit response" → mark responded_at directly, starting the 1h+10 chats
      long cooldown.
    - Now PR #1145 introduces explicit three-option buttons + a keyword text
      fallback; the long-cooldown semantics should only be triggered by an
      **explicit choice** (accept / decline). Any non-matching message merely
      "dismisses the prompt" — keeping ever_delivered (force-first will not fire
      again) + a 5min short suppression (so the next proactive does not
      immediately re-invite), but no long lock. Equivalent to the 'later'
      choice. Otherwise, if the user first says something else and then clicks a
      button → the endpoint sees responded_at != None → "expired", and the state
      has quietly entered the 1h long cooldown (violating D2 semantics, bad UX)."""
    state = _mini_game_invite_state.get(lanlan_name)
    if not state:
        return None
    if state['delivered_at'] is None or state['responded_at'] is not None:
        return None
    if last_user_msg_at is None:
        return None
    if last_user_msg_at <= state['delivered_at']:
        return None
    # 任意消息 = 隐式 dismiss → 等同 'later' choice 的 reset+短抑制语义。
    # 复用 _apply_mini_game_invite_choice 保持单一事实源；source 标 'implicit_dismiss'
    # 让日志能区分按钮路径与隐式路径。
    return _apply_mini_game_invite_choice(
        lanlan_name, 'later', source='implicit_dismiss',
    )


def _mini_game_invite_in_cooldown(lanlan_name: str, game_type: str | None = None) -> bool:
    """Return whether this character is in a mini-game invite cooldown.

    A true value means the current turn should not roll another invite. This
    covers short suppression from the "later" choice, pending invites, and
    replied invites that have not crossed both the time and chat-count gates.
    Cooldowns after completed responses are scoped to the same game type, while
    pending invites still suppress all game types for the character.
    """
    state = _mini_game_invite_state.get(lanlan_name)
    if not state:
        return False
    suppressed_until = state.get('suppressed_until')
    if suppressed_until is not None and time.time() < float(suppressed_until):
        return True
    if game_type:
        cooldowns = state.get('response_cooldowns')
        if isinstance(cooldowns, dict) and isinstance(cooldowns.get(game_type), dict):
            response_state = cooldowns[game_type]
            elapsed = time.time() - float(response_state.get('responded_at') or 0.0)
            if response_state.get('last_response_choice') == 'decline':
                time_threshold = MINI_GAME_INVITE_COOLDOWN_AFTER_DECLINE_SECONDS
            else:
                time_threshold = MINI_GAME_INVITE_COOLDOWN_AFTER_ACCEPT_SECONDS
            chats_since_response = int(response_state.get('chats_since_response') or 0)
            if elapsed < time_threshold or chats_since_response < MINI_GAME_INVITE_COOLDOWN_CHATS:
                return True
            cooldowns.pop(game_type, None)
    if game_type:
        last_game_type = state.get('last_game_type')
        pending = state.get('delivered_at') is not None and state.get('responded_at') is None
        if last_game_type and last_game_type != game_type and not pending:
            return False
    if state['delivered_at'] is None:
        return False
    if state['responded_at'] is None:
        return True
    elapsed = time.time() - state['responded_at']
    if state.get('last_response_choice') == 'decline':
        time_threshold = MINI_GAME_INVITE_COOLDOWN_AFTER_DECLINE_SECONDS
    else:
        time_threshold = MINI_GAME_INVITE_COOLDOWN_AFTER_ACCEPT_SECONDS
    return (
        elapsed < time_threshold
        or state['chats_since_response'] < MINI_GAME_INVITE_COOLDOWN_CHATS
    )


def _mini_game_invite_record_delivered(lanlan_name: str, session_id: str) -> None:
    """Record a successfully delivered invite. Resets responded/counter, entering a new pending round.

    session_id comes from the caller (the uuid generated by
    ``_maybe_deliver_mini_game_invite`` at delivery); the endpoint verifies the
    user's response against the current pending one. A new delivery refreshes
    this id — a stale session_id left by the previous delivery is recognized as
    stale and rejected on the endpoint side."""
    state = _mini_game_invite_get_state(lanlan_name)
    state['delivered_at'] = time.time()
    state['responded_at'] = None
    state['chats_since_response'] = 0
    state['pending_session_id'] = session_id
    state['last_response_choice'] = None
    # 新邀请投递清掉 D2 的 short-suppression：本来是「上次回头再说」的窗口，
    # 既然现在又投了新邀请说明那个窗口已过期，没必要保留。
    state['suppressed_until'] = None


def _mini_game_invite_count_post_response_chat(lanlan_name: str) -> None:
    """Advance invite cooldown chat counters after a delivered proactive turn.

    This runs immediately after _record_proactive_chat. Any channel counts as
    long as the AI actually delivered a proactive message. Pending invites are
    no-ops so the invite message itself does not spend the response gate.
    """
    state = _mini_game_invite_state.get(lanlan_name)
    if not state:
        return
    if state.get('delivered_at') is not None and state.get('responded_at') is None:
        return
    if state.get('responded_at') is not None:
        state['chats_since_response'] += 1
    cooldowns = state.get('response_cooldowns')
    if isinstance(cooldowns, dict):
        for response_state in cooldowns.values():
            if isinstance(response_state, dict) and response_state.get('responded_at') is not None:
                response_state['chats_since_response'] = int(response_state.get('chats_since_response') or 0) + 1


def _mini_game_invite_record_response_cooldown(
    state: dict[str, Any],
    game_type: str,
    choice: str,
    responded_at: float,
) -> None:
    cooldowns = state.setdefault('response_cooldowns', {})
    if not isinstance(cooldowns, dict):
        cooldowns = {}
        state['response_cooldowns'] = cooldowns
    cooldowns[game_type] = {
        'responded_at': responded_at,
        'chats_since_response': 0,
        'last_response_choice': choice,
    }


def _mini_game_launch_url(game_type: str, lanlan_name: str, session_id: str) -> str | None:
    url_template = MINI_GAME_LAUNCH_URL_BY_GAME.get(game_type)
    if not url_template:
        return None
    from urllib.parse import urlencode as _urlencode

    query = {
        "lanlan_name": lanlan_name,
        "session_id": session_id,
    }
    separator = "&" if "?" in url_template else "?"
    return f"{url_template}{separator}{_urlencode(query)}"


def _pick_mini_game_type(lanlan_name: str | None = None) -> str | None:
    """Pick an available mini-game type with invite copy configured.

    Games missing invite lines are skipped, and character-specific cooldowns are
    respected when a character name is provided.
    """
    candidates = [
        g for g in MINI_GAME_INVITE_AVAILABLE_GAMES
        if g in MINI_GAME_INVITE_LINES_BY_GAME
    ]
    if lanlan_name:
        candidates = [
            g for g in candidates
            if not _mini_game_invite_in_cooldown(lanlan_name, g)
        ]
    if not candidates:
        return None
    import random as _random
    return _random.choice(candidates)


async def _maybe_deliver_mini_game_invite(
    *,
    lanlan_name: str,
    mgr,
    activity_snapshot,
    invite_lang: str,
    master_name: str,
    user_toggle_enabled: bool = True,
) -> dict | None:
    """On a hit, deliver the mini-game invite and return the JSON dict for _end_proactive; returns None on no hit.

    Short-circuit conditions (any one unmet → return None and the caller
    continues the original Phase1/2 pipeline):
      - MINI_GAME_INVITE_ENABLED=False (global kill switch, the production master toggle)
      - user_toggle_enabled=False (the user turned the
        ``proactiveMiniGameInviteEnabled`` toggle off in the frontend CHAT_MODE_CONFIG)
      - activity_snapshot is None (privacy mode / tracker unavailable — be conservative, do not send)
      - propensity == 'restricted_screen_only' (focused_work / non-casual gaming)
      - state == 'away' (user absent; nobody to receive the invite)
      - activity_snapshot.unfinished_thread is not None (the AI just asked a
        question the user has not answered; following the thread takes priority
        over changing topics — aligned with the precedence convention of
        skip_probability / restricted_screen_only over unfinished_thread)
      - _mini_game_invite_in_cooldown
      - on the non-force-first path, random() >= MINI_GAME_INVITE_TRIGGER_PROBABILITY

    Debug flag: when ``config.MINI_GAME_INVITE_FORCE_GAME_TYPE`` is non-None it
    bypasses every gate except ``MINI_GAME_INVITE_ENABLED`` (including the user
    toggle, cooldown, probability, unfinished_thread, snapshot None / propensity
    / away, and the force-first decision), pinning game_type to the flag value.
    Local manual testing only; keep it None in production.

    Force-first branch: when
      ``state.delivered_at is None`` and
      ``proactive_chat_total >= MINI_GAME_INVITE_NEW_USER_FORCE_AT - 1``
    the 10% dice roll is bypassed and the invite goes straight out — giving
    users who have never played one deterministic "being invited" moment instead
    of relying on probability. The other gates (propensity / unfinished_thread /
    cooldown) still apply.

    The delivery path fully mirrors
    ``main_routers/game_router._deliver_postgame_text_bubble``:
    prepare_proactive_delivery → feed_tts_chunk → finish_proactive_delivery.
    No Phase 1/2 LLM involved; the line is picked from
    ``MINI_GAME_INVITE_LINES_BY_GAME[game_type]`` and game_type comes from
    random.choice over ``MINI_GAME_INVITE_AVAILABLE_GAMES``."""
    if not MINI_GAME_INVITE_ENABLED:
        return None

    # 调试旗标短路：非 None 时跳过所有 snapshot/cooldown/概率 gate，把 game_type
    # 钉到旗标值上。仍然要求该 game_type 有对应文案；非法值 warn + 退出而不 raise，
    # 避免在配置抖动时把整个 proactive 流水线带挂。Force-first 标记成 True 让 caller
    # 路径与正常 first-time 邀请等价（不影响 ever_delivered 持久化）。
    #
    # 但用户级 toggle (proactiveMiniGameInviteEnabled) 仍要尊重——开发者本机
    # 调试用旗标不应该绕过普通用户在前端关掉 mini-game source 的明确意图。
    force_game = MINI_GAME_INVITE_FORCE_GAME_TYPE
    debug_force = bool(force_game)
    if debug_force and not user_toggle_enabled:
        return None
    if debug_force:
        if force_game not in MINI_GAME_INVITE_LINES_BY_GAME:
            logger.warning(
                "[%s] MINI_GAME_INVITE_FORCE_GAME_TYPE=%r is not in "
                "MINI_GAME_INVITE_LINES_BY_GAME=%r — skipping invite. "
                "Set the flag to a valid key or back to None.",
                lanlan_name, force_game, list(MINI_GAME_INVITE_LINES_BY_GAME.keys()),
            )
            return None
        await _ensure_proactive_chat_totals_loaded()
        game_type = force_game
        # 让下面 success-log 共用同一字段；调试旗标语义上等同于 "强制走 first-time
        # 路径"，print 出来好认。
        force_first = True
    else:
        if not user_toggle_enabled:
            return None
        if activity_snapshot is None:
            return None
        propensity = getattr(activity_snapshot, 'propensity', None)
        state_label = getattr(activity_snapshot, 'state', None)
        if propensity == 'restricted_screen_only':
            return None
        if state_label == 'away':
            return None
        # AI 上一轮抛了问题（含 ?/吗/呢/么 等）用户还没接 → 跟进 thread 优先。
        # skip_probability 在 system_router.py 同一文件的 propensity 段也是这条
        # 优先级，统一不让 mini-game 邀请把 promised follow-up 抢走。
        if getattr(activity_snapshot, 'unfinished_thread', None) is not None:
            return None
        # Force-first：从未发过邀请 + 累计已成功投递 N-1 条主动搭话 → 本条强制变邀请。
        # proactive_chat_total 在 _record_proactive_chat 之后才 +1，所以"第 N 次"的
        # 当下值是 N-1。计数走持久化文件，跨重启保留——否则用户每次重启都再"第 N 次"
        # 一回，邀请密度抖。
        #
        # "is new user" 必须查持久化的 ever_delivered，不能查 in-memory 的
        # ``state.delivered_at is None``——后者会被 PR-B「回头再说」reset，且重启清零；
        # codex review (P1) 指出，没这条 force-first 在每次重启后都会把已邀请过的
        # 用户当新用户重新强制邀请。
        await _ensure_proactive_chat_totals_loaded()
        never_delivered = not _was_invite_ever_delivered(lanlan_name)
        total_so_far = _get_proactive_chat_total(lanlan_name)
        force_first = (
            never_delivered
            and total_so_far >= max(0, MINI_GAME_INVITE_NEW_USER_FORCE_AT - 1)
        )

        game_type = _pick_mini_game_type(lanlan_name)
        if game_type is None:
            logger.warning(
                "[%s] mini-game invite skipped: no game_type available "
                "(MINI_GAME_INVITE_AVAILABLE_GAMES=%r, LINES keys=%r)",
                lanlan_name,
                MINI_GAME_INVITE_AVAILABLE_GAMES,
                list(MINI_GAME_INVITE_LINES_BY_GAME.keys()),
            )
            return None

        if not force_first:
            import random as _random
            if _random.random() >= MINI_GAME_INVITE_TRIGGER_PROBABILITY:
                return None
    template = _loc(MINI_GAME_INVITE_LINES_BY_GAME[game_type], invite_lang)
    safe_master = (master_name or '').strip()
    try:
        invite_text = template.format(master_name=safe_master).strip()
    except Exception:
        invite_text = template.replace('{master_name}', safe_master).strip()
    if not invite_text:
        return None

    if not await mgr.prepare_proactive_delivery(min_idle_secs=10.0):
        return _proactive_pass_body(
            PROACTIVE_REASON_PASS_DELIVERY_BUSY,
            message="mini-game invite skipped: prepare_proactive_delivery refused",
        )
    proactive_sid = mgr.current_speech_id
    from main_logic.session_state import SessionEvent as _SE
    await mgr.state.fire(_SE.PROACTIVE_PHASE2)
    try:
        feed = getattr(mgr, 'feed_tts_chunk', None)
        if callable(feed):
            await feed(invite_text, expected_speech_id=proactive_sid)
    except Exception as exc:
        logger.warning(
            "[%s] mini-game invite feed_tts_chunk failed: %s", lanlan_name, exc,
        )
    committed = await mgr.finish_proactive_delivery(
        invite_text,
        expected_speech_id=proactive_sid,
    )
    if not committed:
        return _proactive_pass_body(
            PROACTIVE_REASON_DELIVERY_PREEMPTED,
            message="mini-game invite skipped: user took over before delivery",
        )
    # 给本次邀请生成独立 session_id，前端按钮点击 / 文本关键词命中走 endpoint 时
    # 必须带回这个 id 给后端校验：避免 stale 邀请的延迟回应被错算成响应当前 pending。
    invite_session_id = str(uuid4())

    _record_proactive_chat(lanlan_name, invite_text, channel='mini_game')
    _mini_game_invite_record_delivered(lanlan_name, invite_session_id)
    _mini_game_invite_get_state(lanlan_name)['last_game_type'] = game_type
    # counter +1 + ever_delivered=True 一把锁内原子写盘。两份持久化数据必须
    # 一起落盘，否则 partial-state（totals 已 +1 但 ever_delivered 还是旧 false）
    # 会让重启后 force-first 重复触发——CodeRabbit Major review 指出。
    await _record_invite_delivery_persistent(lanlan_name)

    try:
        from utils.instrument import counter as _instr_counter
        # channel 维度区分两条邀请投递通道：proactive（本函数）与 work_break
        # （水分提醒组合路径，见 _deliver_break_reminder_via_llm 下游）。两条都
        # 共享同一 invite state/cooldown，邀请总数需把两通道相加。force_first 仅
        # proactive 通道有意义。
        _instr_counter(
            "mini_game_invited",
            game_type=str(game_type)[:24],
            channel="proactive",
            force_first=bool(force_first),
        )
    except Exception:
        # 埋点失败不能影响邀请投递
        pass

    # 推 WS message 给前端展示三选项按钮。前端复用 ChoicePrompt 抽象（与 galgame
    # options 共用渲染），但 source='mini_game_invite' 走独立 endpoint，不翻
    # galgame mode 开关。Pet 主窗收到后通过现有 RAW_MESSAGE IPC forwarding 自动
    # 转给 chat.html，不需要新 IPC channel。
    options_payload = _build_mini_game_invite_options_payload(
        invite_lang=invite_lang,
        game_type=game_type,
        session_id=invite_session_id,
    )
    try:
        if mgr.websocket and hasattr(mgr.websocket, 'send_json'):
            client_state = getattr(mgr.websocket, 'client_state', None)
            if client_state is None or client_state == client_state.CONNECTED:
                await mgr.websocket.send_json(options_payload)
    except Exception as exc:
        logger.warning(
            "[%s] mini-game invite options WS push failed: %s",
            lanlan_name, exc,
        )

    print(
        f"[{lanlan_name}] Mini-game invite delivered "
        f"(game={game_type}, force_first={force_first}, "
        f"session_id={invite_session_id[:8]}…): {invite_text[:60]}…"
    )
    return _proactive_chat_body(
        PROACTIVE_REASON_CHAT_DELIVERED,
        message="mini-game invite delivered",
        channel="mini_game",
        game_type=game_type,
        force_first=force_first,
        lanlan_name=lanlan_name,
        turn_id=proactive_sid,
        invite_session_id=invite_session_id,
    )


def _build_mini_game_invite_options_payload(
    *,
    invite_lang: str,
    game_type: str,
    session_id: str,
) -> dict[str, Any]:
    """Build the WS payload for the frontend ChoicePrompt.

    Labels go through i18n (the accept/decline/later options); choice is the
    wire-format identifier (used when the frontend button click posts back to
    the endpoint) and stays unchanged."""
    labels = MINI_GAME_INVITE_OPTION_LABELS.get(
        invite_lang,
        MINI_GAME_INVITE_OPTION_LABELS.get('zh', {}),
    )
    options = [
        {'choice': 'accept', 'label': labels.get('accept', 'Yes')},
        {'choice': 'decline', 'label': labels.get('decline', 'No')},
        {'choice': 'later', 'label': labels.get('later', 'Later')},
    ]
    return {
        'type': 'mini_game_invite_options',
        'session_id': session_id,
        'game_type': game_type,
        'options': options,
    }


def _apply_mini_game_invite_choice(
    lanlan_name: str, choice: str, *, source: str,
) -> dict[str, Any]:
    """Handle the three-option state transition of a mini-game invite. Returns a
    structured result shared by the endpoint / keyword matcher.

    - accept: mark responded (starts the 2h+10 chats cooldown) + return game_url
    - decline: mark responded (starts the 5h+10 chats cooldown, without opening the game)
    - later (D2): reset state (delivered_at=None, restoring both force-first and
      the normal 10%) + add ``suppressed_until = now + 5min`` so the next
      proactive does not immediately roll the dice again

    state must be already-pending (delivered_at != None and responded_at is None);
    otherwise treat it as stale and return ``action='ignored'``, letting the
    caller decide whether to inform the user."""
    state = _mini_game_invite_state.get(lanlan_name)
    if not state or state.get('delivered_at') is None:
        return {'action': 'ignored', 'reason': 'no_pending_invite'}
    if state.get('responded_at') is not None:
        return {'action': 'ignored', 'reason': 'already_responded'}

    now = time.time()
    if choice == 'accept':
        state['responded_at'] = now
        state['chats_since_response'] = 0
        state['last_response_choice'] = 'accept'
        # session_id 既进 game_url query，又作为 result 顶层字段返回——keyword 路径
        # core.py 要把它放进 mini_game_launch WS payload，前端 dedupe 才能跨路径
        # 共享 key（codex P2 review 指出：缺这个 dedupe 就失效，同 invite 多路径
        # 触发会双开窗口）。
        invite_session_id = state.get('pending_session_id') or ''
        game_type = state.get('last_game_type') or 'soccer'
        launch_game_type = game_type
        game_url = _mini_game_launch_url(launch_game_type, lanlan_name, invite_session_id)
        if not game_url:
            logger.warning(
                "[%s] accept invite but no launch URL for game_type=%r; "
                "fallback /soccer_demo", lanlan_name, game_type,
            )
            launch_game_type = "soccer"
            game_url = _mini_game_launch_url(launch_game_type, lanlan_name, invite_session_id) or "/soccer_demo"
        state['last_game_type'] = game_type
        _mini_game_invite_record_response_cooldown(state, game_type, 'accept', now)
        logger.info(
            "[%s] mini-game invite accepted via %s -> %s",
            lanlan_name, source, game_url,
        )
        return {
            'action': 'open_game',
            'game_type': launch_game_type,
            'game_url': game_url,
            'session_id': invite_session_id,
        }
    if choice == 'decline':
        # 留 session_id 给 caller 推 mini_game_invite_resolved 用——所有
        # outcome 都需要前端 dismiss prompt（codex P2）。
        decline_session_id = state.get('pending_session_id') or ''
        game_type = state.get('last_game_type') or 'soccer'
        state['responded_at'] = now
        state['chats_since_response'] = 0
        state['last_response_choice'] = 'decline'
        _mini_game_invite_record_response_cooldown(state, game_type, 'decline', now)
        logger.info(
            "[%s] mini-game invite declined via %s; cooldown started",
            lanlan_name, source,
        )
        return {'action': 'cooldown', 'session_id': decline_session_id}
    if choice == 'later':
        # D2：完全 reset 但加短期 suppression。reset 之后 force-first 仍受
        # ever_delivered（持久化）压制——已经被邀请过的用户即便 state 清掉也
        # 不会被当成新用户重邀。
        later_session_id = state.get('pending_session_id') or ''
        state['delivered_at'] = None
        state['responded_at'] = None
        state['chats_since_response'] = 0
        state['pending_session_id'] = None
        state['last_response_choice'] = None
        state['suppressed_until'] = now + MINI_GAME_INVITE_LATER_SUPPRESS_SECONDS
        logger.info(
            "[%s] mini-game invite deferred via %s; suppressed for %.0fs",
            lanlan_name, source, float(MINI_GAME_INVITE_LATER_SUPPRESS_SECONDS),
        )
        return {'action': 'suppress', 'session_id': later_session_id}
    return {'action': 'ignored', 'reason': f'unknown_choice:{choice}'}


async def _push_mini_game_invite_resolved(
    mgr,
    *,
    session_id: str,
    action: str,
    game_url: str | None = None,
    game_type: str | None = None,
) -> None:
    """Push the WS event so the frontend dismisses the ChoicePrompt (cleared on any outcome, consistent across windows).
    On accept the payload also carries game_url; the frontend treats
    ``action=='open_game'`` as the "launch" signal for window.open.

    Replaces the original ``mini_game_launch`` event — a single WS event covers
    both lifecycle termination (always clear the prompt) + optional game launch
    (on accept). codex P2 / CodeRabbit pointed out: the original only pushed
    ``mini_game_launch`` on accept, so after a decline / later keyword hit the
    frontend prompt never disappeared even though the state had already changed."""
    if not mgr or not session_id:
        return
    payload: dict[str, Any] = {
        'type': 'mini_game_invite_resolved',
        'session_id': session_id,
        'action': action,
    }
    if game_url:
        payload['game_url'] = game_url
    if game_type:
        payload['game_type'] = game_type
    try:
        ws = getattr(mgr, 'websocket', None)
        if ws is None or not hasattr(ws, 'send_json'):
            return
        client_state = getattr(ws, 'client_state', None)
        if client_state is not None and client_state != client_state.CONNECTED:
            return
        await ws.send_json(payload)
    except Exception as exc:
        logger.warning(
            "mini_game_invite_resolved WS push failed (session=%s, action=%s): %s",
            session_id, action, exc,
        )


# ASCII / Cyrillic keyword 用 word-boundary regex 匹配；其它（CJK / Hiragana /
# Katakana / Hangul）走 substring。Python `\b` 在 \w 边界判定，但中日韩字符也
# 算 \w——同一脚本的字符之间没有 \b，硬套 word-boundary 会把"我好啊"漏掉。
# Cyrillic 同 Latin 都是 letter-only，\b 工作良好。codex P1 指出，避免 'yes'
# 命中 'yesterday'、'no' 命中 'no idea' 等英文误命中。
_LETTER_ONLY_KW_RE = re.compile(r"^[A-Za-z0-9\s'\-Ѐ-ӿ]+$")


_KEYWORD_PATTERN_CACHE: dict[str, "re.Pattern[str]"] = {}


def _keyword_matches(keyword: str, norm_text: str) -> bool:
    """Locale-aware substring/word-boundary match.

    Keywords made of ASCII / digits / Cyrillic / spaces / apostrophes / hyphens
    go through a word-boundary regex (``\\b...\\b``); other scripts (CJK /
    Hiragana / Katakana / Hangul) use substring matching — Python's regex counts
    those characters as \\w, so adding \\b would cause misses (in "我好啊" there
    is no boundary before '好')."""  # noqa: DOCSTRING_CJK
    if not keyword or not norm_text:
        return False
    if _LETTER_ONLY_KW_RE.fullmatch(keyword):
        pattern = _KEYWORD_PATTERN_CACHE.get(keyword)
        if pattern is None:
            pattern = re.compile(r'\b' + re.escape(keyword) + r'\b', re.IGNORECASE)
            _KEYWORD_PATTERN_CACHE[keyword] = pattern
        return bool(pattern.search(norm_text))
    return keyword in norm_text


def _match_mini_game_invite_keyword(text: str) -> str | None:
    """Return accept/decline/later for a user text, or None when unmatched.

    All native locale keyword lists are scanned because users may type in a
    language different from the active UI language. ASCII and Cyrillic keywords
    use word-boundary matching to avoid substring false positives; CJK keywords
    keep substring matching.

    **Priority decline > later > accept**: a sentence with an explicit negation
    must not open a game just because it also contains an accept keyword.

    Empty text and unmatched text return None.
    """
    if not text:
        return None
    norm = text.lower().strip()
    if not norm:
        return None
    hit_accept = False
    hit_decline = False
    hit_later = False
    for lang_kw in MINI_GAME_INVITE_KEYWORDS.values():
        if not hit_accept and any(_keyword_matches(kw, norm) for kw in lang_kw.get('accept', [])):
            hit_accept = True
        if not hit_later and any(_keyword_matches(kw, norm) for kw in lang_kw.get('later', [])):
            hit_later = True
        if not hit_decline and any(_keyword_matches(kw, norm) for kw in lang_kw.get('decline', [])):
            hit_decline = True
    # decline > later > accept：negation-priority。
    if hit_decline:
        return 'decline'
    if hit_later:
        return 'later'
    if hit_accept:
        return 'accept'
    return None


def _maybe_apply_mini_game_invite_keyword(
    lanlan_name: str, text: str,
) -> dict[str, Any] | None:
    """Apply mini-game invite keywords for one user-message text entry.

    Pending invites try accept, decline, and later keywords. Without a pending
    invite this helper is a no-op: ordinary chat text must not launch mini
    games implicitly. This helper does not consume the user message; normal
    chat handling should still continue.
    """
    state = _mini_game_invite_state.get(lanlan_name)
    if not state or state.get('delivered_at') is None or state.get('responded_at') is not None:
        return None
    choice = _match_mini_game_invite_keyword(text)
    if choice is None:
        return None
    result = _apply_mini_game_invite_choice(lanlan_name, choice, source='keyword')
    if result.get('action') == 'ignored':
        return None
    return result


# Self-register the mini-game-invite keyword matcher with main_logic's
# event bus. Same rationale as plugin/core/state.py: ``main_logic.core``
# previously imported this function directly (a layering inversion);
# after the inversion was removed, the only way this hook gets attached
# is via ``register_text_user_message_hook``. Registering at module import
# time keeps the path alive for any context that loads system_router
# directly (testbench, ad-hoc scripts) without going through
# ``app/runtime_bindings.py``. ``register_text_user_message_hook`` dedupes
# on identity, so the explicit wiring in ``app/runtime_bindings.py`` is a
# no-op once we've fired here.
try:
    from main_logic.agent_event_bus import register_text_user_message_hook as _register_text_hook
    _register_text_hook(_maybe_apply_mini_game_invite_keyword)
except Exception as _exc:
    # Same discriminator pattern as plugin/core/state.py: only
    # ``ModuleNotFoundError`` whose missing module IS one of the top-level
    # targets here is a legit partial-env case (and even that is rare —
    # main_logic should always be importable when system_router loads).
    # A transitive failure or a register_* regression must be logged so
    # the silent dispatcher no-op doesn't hide a real bug. Codex P2 catch.
    _expected_absent = {"main_logic", "main_logic.agent_event_bus"}
    _is_expected_absent = (
        isinstance(_exc, ModuleNotFoundError)
        and getattr(_exc, "name", None) in _expected_absent
    )
    if not _is_expected_absent:
        logger.warning(
            "proactive_chat: failed to self-register text_user_message_hook",
            exc_info=True,
        )
