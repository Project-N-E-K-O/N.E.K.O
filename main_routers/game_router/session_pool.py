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

"""Game session pool: ``_game_sessions`` and its full lifecycle.

Owns the per-``(lanlan_name, game_type, session_id)`` pool of offline LLM
clients: cache-key construction/parsing, per-key creation locks, session
build + registration, instruction refresh and close/eviction. The dicts
here are process-wide singletons shared by reference with ``runtime`` and
``postgame`` -- rebinding or copying them would break that sharing.

Split out of ``main_routers/game_router/runtime.py``.
"""

from ._shared import _infer_service_source, logger
from .badminton_scores import _is_badminton_game_type, _normalize_badminton_mode
from .balance import _badminton_duel_difficulty_control_prompt
from .char_info import _get_character_info
from .game_context import _build_game_context_prompt_payload, _format_game_context_for_prompt
from .pregame import (
    _format_badminton_pregame_context_for_prompt,
    _format_soccer_pregame_context_for_prompt,
)
from .route_lifecycle import _find_game_route_state_for_session

import asyncio
import re
import time
import uuid
from typing import Dict, Optional
from config.prompts.prompts_soccer import get_soccer_system_prompt
from config.prompts.prompts_badminton import get_badminton_system_prompt
from utils.language_utils import get_global_language


# ── Session 池 ─────────────────────────────────────────────────────
# key = f"{lanlan_name}:{game_type}:{session_id}"
# value = { session: OmniOfflineClient, reply_chunks: list, last_activity: float, lock: asyncio.Lock }
_game_sessions: Dict[str, dict] = {}


# 超时清理：30 分钟无活动自动销毁
_SESSION_TIMEOUT_SECONDS = 30 * 60


_SESSION_CLEANUP_SWEEP_SECONDS = 60.0


# Per-(lanlan, game_type, session_id) creation lock for ``_get_or_create_session``.
#
# B6: without this, two concurrent ``_run_game_chat`` calls for the same
# key both miss the cache, both build an ``OmniOfflineClient`` and both
# ``await session.connect(...)``. The second insertion overwrites
# ``_game_sessions[key]`` so the first ``entry`` is now an orphan: its
# ``lock`` is held by the first ``_run_game_chat``, but the cache no
# longer points at it, so nothing will ever ``close()`` that session.
#
# Lifecycle (codex P2 follow-up): the create lock for a key is only
# meaningful while a session for that key may be created or alive. After
# ``_close_and_remove_session`` evicts the session from
# ``_game_sessions``, any further ``_get_or_create_session`` call for
# the same key would build a fresh session anyway — the lock entry just
# accumulates without protecting anything. So evict the create lock at
# the same time as the session, otherwise the dict grows unbounded over
# uptime as session_ids churn.
_game_session_create_locks: Dict[str, asyncio.Lock] = {}


def _get_session_create_lock(key: str) -> asyncio.Lock:
    """Lazy-init the per-key creation lock; sync helper, never awaits."""
    lock = _game_session_create_locks.get(key)
    if lock is None:
        lock = _game_session_create_locks.setdefault(key, asyncio.Lock())
    return lock


def _build_game_prompt(
    game_type: str,
    lanlan_name: str,
    lanlan_prompt: str,
    pre_game_context: dict | None = None,
    game_context: dict | None = None,
    language: str | None = None,
    mode: str = "spectator",
) -> str:
    """Build the game system prompt."""
    if game_type == "soccer":
        prompt = get_soccer_system_prompt(language).format(name=lanlan_name, personality=lanlan_prompt)
        context_prompt = _format_soccer_pregame_context_for_prompt(pre_game_context, language)
        in_game_context_prompt = _format_game_context_for_prompt(game_context, language)
        return f"{prompt}{context_prompt}{in_game_context_prompt}"
    if _is_badminton_game_type(game_type):
        prompt = get_badminton_system_prompt(language, mode=mode).format(name=lanlan_name, personality=lanlan_prompt)
        if _normalize_badminton_mode(mode) == "duel":
            prompt = f"{prompt}{_badminton_duel_difficulty_control_prompt(language)}"
        context_prompt = _format_badminton_pregame_context_for_prompt(pre_game_context, language, mode=mode)
        in_game_context_prompt = _format_game_context_for_prompt(game_context, language)
        return f"{prompt}{context_prompt}{in_game_context_prompt}"
    # 未来其他游戏在这里扩展
    output_language = str(language or get_global_language() or "en")
    return (
        f"You are {lanlan_name}. {lanlan_prompt}\n"
        f"You are playing a game. Generate short in-character lines in {output_language} for each game event."
    )


async def _get_or_create_session(
    game_type: str,
    session_id: str,
    lanlan_name: str = "",
    *,
    postgame_snapshot: Optional[dict] = None,
) -> dict:
    """Get or create a game session.

    B6: serialize the cache-miss → ctor → connect → cache-insert sequence
    under a per-key ``asyncio.Lock`` so two concurrent ``_run_game_chat``
    calls for the same ``(lanlan, game_type, session_id)`` cannot both
    build a fresh ``OmniOfflineClient`` and overwrite each other in
    ``_game_sessions``, leaking the loser's connection.

    CodeRabbit follow-up: ``lanlan_name`` may be empty on entry
    (caller-supplied) but canonicalizes to ``char_info["lanlan_name"]``.
    Resolve the canonical key BEFORE acquiring the create lock so we
    only ever take one lock — under the canonical key. The previous
    "lock under raw key, then re-lock under canonical key" shape left
    an orphan ``_game_session_create_locks[raw_key]`` entry whenever
    the canonical resolution changed the key, because
    ``_close_and_remove_session`` only evicts the lock keyed by the
    session's actual storage key (the canonical one).

    The fast-path cache check still uses the raw key so a hit on the
    pre-canonicalization shape (rare; only happens if a session was
    cached under an empty lanlan_name) short-circuits without paying
    the ``_get_character_info`` lookup.
    """
    key = _game_session_key(lanlan_name, game_type, session_id)

    if key in _game_sessions:
        entry = _game_sessions[key]
        entry['last_activity'] = time.time()
        return entry

    # 与 core/lifecycle.py 的会话准备路径对偶：下面建出来的 OmniOfflineClient 会连
    # base_url 一起缓存进会话池、整场不再复议，所以要先给仍在飞的区域探测一个收尾
    # 窗口。已落定时零开销；自配 API 用户不会因此发起探测（内部按免费路由设门）。
    #
    # 刻意 fail-open 并 warning（区别于主会话/热切换路径的异常传播）：小游戏是轻量
    # 入口，不该因区域探测本身出错而开不了；落定失败时退化到当前配置（最坏用国内
    # 兜底线路）仍可用，比 fail-closed 更符合游戏场景。warning 而非静默，便于诊断
    # 「海外用户偶尔一整场走国内线路」。
    from ..shared_state import get_config_manager as _get_cm
    try:
        await _get_cm().aensure_region_resolved()
    except Exception:
        logger.warning("[GeoIP] 游戏会话区域落定失败，退化到当前配置继续", exc_info=True)

    char_info = _get_character_info(lanlan_name)
    canonical_lanlan = str(char_info.get("lanlan_name") or lanlan_name or "").strip()
    canonical_key = _game_session_key(canonical_lanlan, game_type, session_id)

    # Fast path: canonical_key may already be cached (e.g. another caller
    # passed the canonical lanlan_name and built it).
    if canonical_key in _game_sessions:
        entry = _game_sessions[canonical_key]
        entry['last_activity'] = time.time()
        return entry

    create_lock = _get_session_create_lock(canonical_key)
    async with create_lock:
        if canonical_key in _game_sessions:
            entry = _game_sessions[canonical_key]
            entry['last_activity'] = time.time()
            return entry
        try:
            return await _build_and_register_game_session(
                canonical_key, game_type, session_id, char_info,
                postgame_snapshot=postgame_snapshot,
            )
        except BaseException:
            # codex P2 (PR #1127 r3182157092): if the build raises,
            # nothing inserts into ``_game_sessions`` for this key, so
            # ``_close_and_remove_session`` will never run for it and
            # the per-key create lock would leak forever. Evict it
            # here.
            #
            # Why conditional pop on ``_waiters``: if a peer task is
            # already awaiting THIS lock (concurrent miss for the same
            # canonical_key), unconditionally popping would let a new
            # arrival call ``_get_session_create_lock`` and receive a
            # FRESH lock object — distinct from the one the peer is
            # awaiting — defeating the build serialization. Leaving
            # the lock in place when there are waiters keeps them on
            # the same Lock instance; the next successful build path
            # registers an entry and ``_close_and_remove_session``
            # will pop normally; another failed build will hit this
            # branch again and eventually find ``_waiters`` empty.
            # ``_waiters`` is CPython-private but stable across all
            # supported Python versions.
            waiters = getattr(create_lock, "_waiters", None)
            if not waiters:
                _game_session_create_locks.pop(canonical_key, None)
            raise


async def _build_and_register_game_session(
    key: str,
    game_type: str,
    session_id: str,
    char_info: dict,
    *,
    postgame_snapshot: Optional[dict] = None,
) -> dict:
    """Build a fresh game session entry; caller must already hold the
    per-key creation lock (see ``_get_or_create_session``).

    ``postgame_snapshot`` (when set) is the authoritative prompt-context
    source for postgame builds — see ``_build_postgame_context_snapshot``.
    Without it, a postgame build that races a fresh ``/route/start`` for
    the same ``(lanlan, game_type, session_id)`` would reverse-resolve
    live route_state and pick up the NEW route's preGameContext /
    game_context.
    """
    from main_logic.omni_offline_client import OmniOfflineClient
    from utils.token_tracker import set_call_type

    lanlan_name = str(char_info.get("lanlan_name") or "").strip()
    reply_chunks: list[str] = []

    async def on_text_delta(text: str, is_first: bool, **_kwargs):
        # **_kwargs 吞掉 ui_enabled / tts_enabled（OmniOfflineClient summary 路径会传，
        # 但 game 短台词跑的是非 summary 模式，理论上不会发 UI/TTS 分流。保留 kwargs
        # forward-compat 防签名漂移触发 TypeError。）
        reply_chunks.append(text)

    set_call_type("game_chat")

    session = OmniOfflineClient(
        base_url=char_info['base_url'],
        api_key=char_info['api_key'],
        model=char_info['model'],
        on_text_delta=on_text_delta,
        max_response_length=100,  # 游戏台词要短
        lanlan_name=char_info['lanlan_name'],
        master_name=char_info['master_name'],
    )

    if postgame_snapshot is not None:
        pre_game_context = postgame_snapshot.get("pre_game_context")
        game_context = postgame_snapshot.get("game_context")
        route_mode = _normalize_badminton_mode(postgame_snapshot.get("mode"))
    else:
        route_state = _find_game_route_state_for_session(game_type, _route_session_id(session_id), lanlan_name)
        pre_game_context = route_state.get("preGameContext") if isinstance(route_state, dict) else None
        game_context = _build_game_context_prompt_payload(route_state, include_recent=False)
        route_mode = _normalize_badminton_mode(route_state.get("mode") if isinstance(route_state, dict) else "")
    prompt_args = (
        game_type,
        char_info['lanlan_name'],
        char_info['lanlan_prompt'],
        pre_game_context if isinstance(pre_game_context, dict) else None,
        game_context if isinstance(game_context, dict) else None,
        char_info.get("user_language"),
    )
    if _is_badminton_game_type(game_type):
        system_prompt = _build_game_prompt(*prompt_args, mode=route_mode)
    else:
        system_prompt = _build_game_prompt(*prompt_args)
    try:
        await session.connect(instructions=system_prompt)
    except asyncio.CancelledError:
        # Why: CancelledError doesn't inherit from Exception in Python
        # 3.8+; without this branch a cancelled connect leaks the half-
        # open client.
        try:
            await session.close()
        except Exception:
            # Why: cleanup must remain idempotent on the cancellation
            # path — a close() failure here would mask the original
            # CancelledError that we re-raise below.
            pass
        raise
    except Exception:
        # Connect failed — ensure we don't leak a half-open client. close
        # is idempotent / tolerant of "never connected".
        try:
            await session.close()
        except Exception:
            # Why: cleanup must not raise from the exception path —
            # a close() failure would mask the original connect error
            # that we re-raise below.
            pass
        raise

    entry = {
        'session': session,
        'reply_chunks': reply_chunks,
        'lanlan_name': char_info['lanlan_name'],
        'lanlan_prompt': char_info.get('lanlan_prompt') or '',
        'user_language': char_info.get('user_language'),
        'source': _infer_service_source(
            char_info.get('base_url', ''),
            char_info.get('model', ''),
            char_info.get('api_type', ''),
        ),
        'last_activity': time.time(),
        'lock': asyncio.Lock(),
        'instructions': system_prompt,
        'mode': route_mode,
    }
    _game_sessions[key] = entry

    logger.info(
        "🎮 创建游戏LLM会话: 游戏=%s 会话=%s 角色=%s 模型=%s 人格提示长度=%d字",
        game_type,
        session_id,
        char_info['lanlan_name'],
        char_info['model'],
        len(char_info.get('lanlan_prompt') or ''),
    )
    return entry


async def _refresh_game_session_instructions(
    entry: dict,
    game_type: str,
    session_id: str,
    lanlan_name: str = "",
    *,
    postgame_snapshot: Optional[dict] = None,
) -> None:
    session = entry.get("session") if isinstance(entry, dict) else None
    update = getattr(session, "update_session", None)
    if not callable(update):
        return

    lanlan_name = str(lanlan_name or entry.get("lanlan_name") or "").strip()
    char_info = _get_character_info(lanlan_name)
    entry["user_language"] = char_info.get("user_language")
    if postgame_snapshot is not None:
        pre_game_context = postgame_snapshot.get("pre_game_context")
        game_context = postgame_snapshot.get("game_context")
        route_mode = _normalize_badminton_mode(postgame_snapshot.get("mode"))
    else:
        route_state = _find_game_route_state_for_session(game_type, _route_session_id(session_id), char_info["lanlan_name"])
        pre_game_context = route_state.get("preGameContext") if isinstance(route_state, dict) else None
        game_context = _build_game_context_prompt_payload(route_state, include_recent=False)
        route_mode = _normalize_badminton_mode(route_state.get("mode") if isinstance(route_state, dict) else "")
    prompt_args = (
        game_type,
        char_info["lanlan_name"],
        char_info["lanlan_prompt"],
        pre_game_context if isinstance(pre_game_context, dict) else None,
        game_context if isinstance(game_context, dict) else None,
        char_info.get("user_language"),
    )
    if _is_badminton_game_type(game_type):
        instructions = _build_game_prompt(*prompt_args, mode=route_mode)
    else:
        instructions = _build_game_prompt(*prompt_args)
    if entry.get("instructions") == instructions:
        entry["mode"] = route_mode
        return
    await update({"instructions": instructions})
    entry["instructions"] = instructions
    entry["mode"] = route_mode


def _game_session_key(lanlan_name: str, game_type: str, session_id: str) -> str:
    lanlan = str(lanlan_name or "").strip()
    if lanlan:
        return f"{lanlan}:{game_type}:{session_id}"
    return f"{game_type}:{session_id}"


_POSTGAME_SESSION_MARKER = "::postgame::"


# Why: ``_make_postgame_session_id`` produces ``<session_id>::postgame::<uuid4.hex>``
# where ``uuid4().hex`` is exactly 32 lowercase hex chars. ``_route_session_id``
# strips ONLY this exact synthetic suffix shape so a legitimate client-supplied
# session_id that happens to contain ``::postgame::`` is left untouched.
_POSTGAME_UUID_TAIL_RE = re.compile(r"[0-9a-f]{32}\Z")


def _make_postgame_session_id(session_id: str) -> str:
    # Why: postgame's freshly-built session lives at a private cache key
    # so a racing ``/route/start`` reusing the same user-facing session_id
    # cannot land on the same ``_game_sessions`` slot. Without this, a
    # peer's first ``/game_chat`` would be handed back postgame's cached
    # entry by ``_get_or_create_session``, and postgame's ``finally``
    # close (still identity-matching since the cache wasn't replaced)
    # would tear down the active route's session mid-turn.
    return f"{str(session_id or '')}{_POSTGAME_SESSION_MARKER}{uuid.uuid4().hex}"


def _route_session_id(session_id: str) -> str:
    # Why: this helper is now defensive. The critical postgame paths
    # (``_build_and_register_game_session`` / ``_refresh_game_session_instructions``)
    # use a frozen ``postgame_snapshot`` instead of reverse-resolving live
    # route_state, so the marker no longer needs to round-trip through
    # ``_find_game_route_state_for_session`` for prompt context. We still
    # tolerate the synthetic shape elsewhere by stripping ONLY the exact
    # suffix produced by ``_make_postgame_session_id`` (marker + 32-char
    # uuid4 hex tail at end of string). A legitimate client session_id
    # that happens to contain ``::postgame::`` mid-string — or with a
    # non-uuid tail — is returned unchanged.
    raw = str(session_id or "")
    idx = raw.rfind(_POSTGAME_SESSION_MARKER)
    if idx == -1:
        return raw
    tail = raw[idx + len(_POSTGAME_SESSION_MARKER):]
    if not _POSTGAME_UUID_TAIL_RE.fullmatch(tail):
        return raw
    return raw[:idx]


def _parse_game_session_key(key: str) -> tuple[str, str, str]:
    parts = str(key or "").split(":", 2)
    if len(parts) == 3:
        return parts[0], parts[1], parts[2]
    game_type, _, session_id = str(key or "").partition(":")
    return "", game_type, session_id


async def _close_and_remove_session(
    game_type: str,
    session_id: str,
    lanlan_name: str = "",
) -> bool:
    """Close and remove the specified game session.

    B1: serialize against in-flight ``_run_game_chat`` work for the same
    entry by acquiring ``entry['lock']`` before popping + closing. Without
    this, a concurrent close (from ``/route/start`` finalize, heartbeat
    sweep, or ``/route/end``) would yank the session out of the cache and
    close it while a chat call still held a reference and was mid
    ``stream_text``, producing reads against a closed client.

    The entry-level lock keeps the wait bounded — chat work is capped by
    the 15s ``stream_text`` timeout. New chats arriving after we set the
    route's ``_exit_flow_started`` flag short-circuit before they ever
    touch the entry lock.

    codex P1 (PR #1127 r3182582714): identity-gate the cache eviction.
    Two close callers can read the same ``entry`` then queue on its
    lock; while they wait, a peer may pop the cache, a fresh
    ``/route/start`` may build ``entry_NEW`` under the same key, and we
    would otherwise wake up and pop ``entry_NEW`` — closing a live
    session a different route owns. Mirrors the postgame ownership gate
    in ``_deliver_postgame_text_bubble``'s finally. We always close OUR
    captured ``entry``'s session (it's ours since the top of this
    function) but only touch the cache / create-lock dicts if they
    still point at us.
    """
    keys = []
    if lanlan_name:
        keys.append(_game_session_key(lanlan_name, game_type, session_id))
    keys.append(_game_session_key("", game_type, session_id))

    # First locate the entry (without popping) to grab its lock, then pop
    # under the lock. Two close callers racing here both serialize on the
    # same lock and only one observes a non-None entry after pop.
    key = ""
    entry = None
    for candidate in keys:
        key = candidate
        entry = _game_sessions.get(candidate)
        if entry:
            break
    if not entry:
        return False

    entry_lock = entry.get('lock')
    if isinstance(entry_lock, asyncio.Lock):
        async with entry_lock:
            cache_owned = _game_sessions.get(key) is entry
            if cache_owned:
                _game_sessions.pop(key, None)
                _game_session_create_locks.pop(key, None)
    else:
        cache_owned = _game_sessions.get(key) is entry
        if cache_owned:
            _game_sessions.pop(key, None)
            _game_session_create_locks.pop(key, None)

    # Why: ``entry`` was captured at the top of this function; we own its
    # lifecycle even if a peer closer rotated the cache to ``entry_NEW``
    # while we waited on the lock. Always close OUR session so the
    # client cannot leak. ``OmniOfflineClient.close`` is idempotent
    # (omni_offline_client.py:1815-1835), so any peer's prior close on
    # the same object is safe to re-run.
    session = entry.get('session')
    if session:
        try:
            await session.close()
        except Exception as e:
            logger.debug("🎮 关闭游戏 session 失败: key=%s err=%s", key, e, exc_info=True)

    logger.info("🎮 结束游戏 session: %s cache_owned=%s", key, cache_owned)
    return True
