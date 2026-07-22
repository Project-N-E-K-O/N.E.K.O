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

"""Proactive-chat state, history, deduplication and persistent counters."""

import asyncio
import difflib
import hashlib
import logging
import re
import time
from collections import deque
from pathlib import Path
from typing import Any
from config import (
    PROACTIVE_CHAT_HISTORY_MAX,
    PROACTIVE_SOURCE_FORGET_P,
    PROACTIVE_SOURCE_HALF_LIFE_BY_KIND,
    PROACTIVE_SOURCE_HALF_LIFE_DEFAULT,
    PROACTIVE_SOURCE_HARD_SKIP_SECONDS,
)
from config.prompts.prompts_sys import _loc
from config.prompts.prompts_proactive import (
    RECENT_PROACTIVE_CHATS_HEADER, RECENT_PROACTIVE_CHATS_FOOTER,
    RECENT_PROACTIVE_TIME_LABELS,
    RECENT_PROACTIVE_CHANNEL_LABELS,
)
from utils.config_manager import get_config_manager
from utils.file_utils import atomic_write_json_async, read_json


logger = logging.getLogger(__name__)


# --- Global source decay history (cross-character, persisted) ---
_SOURCE_HISTORY_FILENAME = "proactive_source_history.json"
_SOURCE_HISTORY_SCHEMA_VERSION = 1
_source_history: dict[str, dict[str, Any]] = {}
_source_history_lock = asyncio.Lock()
_source_history_loaded = False


def _source_history_path() -> Path:
    return Path(get_config_manager().memory_dir) / _SOURCE_HISTORY_FILENAME


def _source_hash(url: str = '', fallback_title: str = '') -> str:
    """Return a stable URL hash, falling back to a normalized title."""
    norm = (url or '').strip().lower().rstrip('/')
    if norm:
        return hashlib.sha256(norm.encode('utf-8')).hexdigest()
    title_norm = re.sub(r'\s+', ' ', (fallback_title or '').strip().lower())
    if title_norm:
        return hashlib.sha256(('t::' + title_norm).encode('utf-8')).hexdigest()
    return ''


def _half_life_for(kind: str) -> float:
    return PROACTIVE_SOURCE_HALF_LIFE_BY_KIND.get(
        kind,
        PROACTIVE_SOURCE_HALF_LIFE_DEFAULT,
    )


def _source_skip_probability(age: float, half_life: float) -> float:
    if age < PROACTIVE_SOURCE_HARD_SKIP_SECONDS:
        return 1.0
    decay_age = age - PROACTIVE_SOURCE_HARD_SKIP_SECONDS
    return 0.5 ** (decay_age / half_life)


def _get_source_history_entry(url_hash: str) -> dict[str, Any] | None:
    """Return the current in-memory source record for a stable hash."""
    if not url_hash:
        return None
    return _source_history.get(url_hash)


async def _ensure_source_history_loaded() -> None:
    """Load source history once without blocking the event loop."""
    global _source_history_loaded
    if _source_history_loaded:
        return
    async with _source_history_lock:
        if _source_history_loaded:
            return
        path = _source_history_path()
        try:
            data = await asyncio.to_thread(read_json, path)
            entries = data.get('entries') if isinstance(data, dict) else None
            if isinstance(entries, dict):
                now = time.time()
                for source_hash, entry in entries.items():
                    if not isinstance(entry, dict):
                        continue
                    age = now - float(entry.get('ts', 0.0) or 0.0)
                    probability = _source_skip_probability(
                        age,
                        _half_life_for(entry.get('kind', 'web')),
                    )
                    if probability >= PROACTIVE_SOURCE_FORGET_P:
                        _source_history[source_hash] = entry
        except FileNotFoundError:
            pass
        except Exception as exc:
            logger.warning(
                "加载 %s 失败，按空历史处理: %s: %s",
                _SOURCE_HISTORY_FILENAME,
                type(exc).__name__,
                exc,
            )
        _source_history_loaded = True


async def _record_source_used(
    *,
    url: str,
    kind: str,
    title: str = '',
) -> None:
    """Update, prune and persist one consumed source record."""
    source_hash = _source_hash(url, title)
    if not source_hash:
        return
    async with _source_history_lock:
        _source_history[source_hash] = {
            "ts": time.time(),
            "kind": kind,
            "title": (title or '')[:80],
        }
        now = time.time()
        forgotten = [
            existing_hash
            for existing_hash, entry in _source_history.items()
            if _source_skip_probability(
                now - float(entry.get('ts', 0.0) or 0.0),
                _half_life_for(entry.get('kind', 'web')),
            )
            < PROACTIVE_SOURCE_FORGET_P
        ]
        for existing_hash in forgotten:
            _source_history.pop(existing_hash, None)
        snapshot = {
            "v": _SOURCE_HISTORY_SCHEMA_VERSION,
            "entries": dict(_source_history),
        }
    try:
        await atomic_write_json_async(_source_history_path(), snapshot)
    except Exception as exc:
        logger.warning(
            "落盘 %s 失败: %s: %s",
            _SOURCE_HISTORY_FILENAME,
            type(exc).__name__,
            exc,
        )


# --- 主动搭话近期记录暂存区 ---
# {lanlan_name: deque([(timestamp, message), ...], maxlen=10)}
_proactive_chat_history: dict[str, deque] = {}


# --- 主动搭话"素材标识"近期去重暂存区（ANTI_REPEAT_EXEMPT_SOURCE_TAGS 用）---
# {lanlan_name: {source_tag: deque([(timestamp, material_key), ...], maxlen=N)}}
# 素材推送类 channel（MUSIC/MEME）豁免台词级复读判定，改按"素材本身"去重：
# MUSIC 看曲目（title|artist），MEME 看搜索关键词。本轮素材与近期不雷同就放行；
# 雷同才回落到台词判定。进程内、重启清零——短期复读保护，与 _proactive_chat_
# history / _mini_game_invite_state 同样是内存态即可。
_proactive_material_history: dict[str, dict[str, deque]] = {}


_PROACTIVE_MATERIAL_HISTORY_MAX = 10


# --- 持久化"该角色累计成功投递的主动搭话次数 + 是否曾被邀请过"---
# 单文件 schema：
#   {"version": 2,
#    "totals": {<lanlan_name>: <int>, ...},
#    "ever_delivered": {<lanlan_name>: true, ...}}
# 跨进程重启保留。两份数据合一个文件方便维护。
#
# - totals: 「新用户第 N 次主动搭话强制走 mini-game 邀请」(N=NEW_USER_FORCE_AT)
#   必须依赖跨重启的累计计数——否则用户每次重启 app，force-trigger 会反复触发，
#   体感邀请密度抖。计数语义与 _record_proactive_chat 对齐：仅在「成功投递给
#   用户」时 +1，PASS 不算（spec 上"第 N 次主动搭话"指用户实际收到的）。
# - ever_delivered: 「该角色是否曾经被发过 mini-game 邀请」一次性 true 标记，
#   force-first 的 "is new user" 判定基础。和 in-memory 的 ``state.delivered_at``
#   不同：后者跟随 PR-B 的 D2「回头再说」会被 reset，但 ever_delivered 一旦置
#   True 就不再翻——「曾经被邀请过」是历史事实，不能被反悔。codex review (P1)
#   指出，没这条 force-first 在重启后会把已邀请过的用户当新用户重新强制邀请。
_PROACTIVE_CHAT_TOTALS_FILENAME = "proactive_chat_totals.json"


_PROACTIVE_CHAT_TOTALS_SCHEMA_VERSION = 2


_proactive_chat_totals: dict[str, int] = {}


_invite_ever_delivered: dict[str, bool] = {}


_proactive_chat_totals_lock = asyncio.Lock()


_proactive_chat_totals_loaded = False


_RECENT_CHAT_MAX_AGE_SECONDS = 3600  # 1小时内的搭话记录


_PROACTIVE_SIMILARITY_THRESHOLD = 0.90  # 保守硬拦截阈值：90% 以上重复直接放弃本轮


def _recent_proactive_chat_entries(lanlan_name: str) -> tuple[tuple, ...]:
    """Return an immutable snapshot of one character's recent chat records."""
    history = _proactive_chat_history.get(lanlan_name)
    return tuple(history) if history else ()


def _format_recent_proactive_chats(lanlan_name: str, lang: str = 'zh') -> str:
    """
    Format recent proactive-chat records into a text block injectable into the prompt (with relative time and source channel).
    Logic:
    - fetch the given model's proactive-chat records from _proactive_chat_history
    - filter to records within the last _RECENT_CHAT_MAX_AGE_SECONDS seconds
    - format the time label according to lang ('zh', 'en', 'ja', 'ko')
    - format the source channel label ('vision', 'web')
    """
    history = _proactive_chat_history.get(lanlan_name)
    if not history:
        return ""
    now = time.time()
    recent = [entry for entry in history if now - entry[0] < _RECENT_CHAT_MAX_AGE_SECONDS]
    if not recent:
        return ""

    tl = RECENT_PROACTIVE_TIME_LABELS.get(lang, RECENT_PROACTIVE_TIME_LABELS['en'])
    cl = RECENT_PROACTIVE_CHANNEL_LABELS.get(lang, RECENT_PROACTIVE_CHANNEL_LABELS['en'])

    def _rel(ts):
        """
        Format the time label.
        args:
        - ts: timestamp (seconds)
        returns:
        - str: formatted time label
        """
        d = int(now - ts)
        if d < 60:
            return tl[0]
        m = d // 60
        if m < 60:
            return tl['m'].format(m)
        return tl['h'].format(m // 60)

    header = _loc(RECENT_PROACTIVE_CHATS_HEADER, lang)
    footer = _loc(RECENT_PROACTIVE_CHATS_FOOTER, lang)
    lines = []
    for entry in recent:
        ts, msg = entry[0], entry[1]
        ch = entry[2] if len(entry) > 2 else ''
        # 过滤掉 vision 通道的记录，避免 AI 引用已过期的屏幕内容产生幻觉
        if ch == 'vision':
            continue
        tag = _rel(ts)
        if ch:
            tag += f"·{cl.get(ch, ch)}"
        lines.append(f"- [{tag}] {msg}")
    if not lines:
        return ""
    return f"\n{header}\n" + "\n".join(lines) + f"\n{footer}\n"


# Reminiscence usage buffer — separate from _proactive_chat_history because
# the latter feeds dedup / similarity checks (_format_recent_proactive_chats /
# _is_similar_to_recent_proactive_chat) and any double-recording there would
# inflate similarity scores against its own message. This buffer is read
# only by _compute_source_weights to factor reminiscence into channel
# weight decay alongside web/news/etc.
#
# Why 50 (not tied to PROACTIVE_CHAT_HISTORY_MAX=10): the two buffers serve
# opposite sizing constraints. PROACTIVE_CHAT_HISTORY_MAX bounds *dedup*
# memory (1h text-similarity check, 10 entries are plenty). This buffer
# bounds *decay-signal completeness* — _compute_source_weights walks every
# timestamp inside the _SOURCE_WEIGHT_WINDOW (=1h) for the exponential
# decay sum, so the maxlen MUST be larger than the worst-case usage count
# in that window or oldest entries get evicted and the channel under-
# counts. 50 leaves ~5× safety margin for high-cadence proactive cycles.
# Kept as a private module constant alongside the other _SOURCE_WEIGHT_*
# tunables (_SOURCE_WEIGHT_DECAY_LAMBDA / _K / _FLOOR / _WINDOW) — it's
# tied to that model's calibration, not a user-facing config knob.
_REMINISCENCE_USAGE_MAX = 50


_reminiscence_usage_history: dict[str, deque[float]] = {}


def _reminiscence_usage_entries(lanlan_name: str) -> tuple[float, ...]:
    """Return an immutable snapshot of reminiscence usage timestamps."""
    history = _reminiscence_usage_history.get(lanlan_name)
    return tuple(history) if history else ()


def _record_reminiscence_usage(lanlan_name: str) -> None:
    """Record one reminiscence usage timestamp for source-weight decay.

    Kept separate from ``_record_proactive_chat`` to avoid polluting
    the dedup / similarity history (which compares the proactive
    response text against past entries by channel-agnostic match).
    """
    if lanlan_name not in _reminiscence_usage_history:
        _reminiscence_usage_history[lanlan_name] = deque(maxlen=_REMINISCENCE_USAGE_MAX)
    _reminiscence_usage_history[lanlan_name].append(time.time())


def _record_proactive_chat(lanlan_name: str, message: str, channel: str = ''):
    """
    Record one successful proactive chat (with its source channel).
    Logic:
    - get the current timestamp
    - append the record (timestamp, message content, channel) to the given model's queue in _proactive_chat_history
    - if the queue is full, the oldest record is popped automatically, keeping the length within maxlen (default 10)
    args:
    - lanlan_name: model name
    - message: chat content
    - channel: source channel (optional, default 'vision')
    """
    if lanlan_name not in _proactive_chat_history:
        _proactive_chat_history[lanlan_name] = deque(maxlen=PROACTIVE_CHAT_HISTORY_MAX)
    _proactive_chat_history[lanlan_name].append((time.time(), message, channel))

    # Telemetry：主动搭话实际投递。channel 是低基数 enum（vision/news/video/
    # personal/music/meme/mini_game/...），截断防意外高基数。配合 settings_state
    # 的 proactive 配置档，能看深度用户每天实际被主动搭话几次。
    #
    # 不在这里做 responded 回应率配对：这会要求 core turn 与主动搭话状态共享
    # “上次投递时刻”，扩大两个生命周期之间的耦合。回应率先由 server 端使用
    # proactive_fired 时刻与用户消息活动 timestamp 关联粗估；精确配对另行设计。
    try:
        from utils.instrument import counter as _instr_counter
        _instr_counter("proactive_fired", channel=(str(channel) or "default")[:24])
    except Exception:
        # 埋点失败不能影响主动搭话投递
        pass


def _normalize_material_key(raw: str) -> str:
    """Normalize a material identity string for exact-match dedup (lowercase + collapse whitespace)."""
    s = (raw or "").strip().lower()
    return re.sub(r'\s+', ' ', s)


def _proactive_material_key(
    source_tag: str | None,
    selected_music_link: dict | None,
    meme_content: dict | None,
) -> str:
    """Compute the dedup identity of the material this round pushes.

    - MUSIC → the picked track (title|artist); two different songs never collide
    - MEME → the **search keyword** (not the image): same keyword reused soon is a
      repeat, a fresh keyword is not. Random hot-word fallback has an empty keyword
      → empty key → treated as "never a repeat" (each random fetch is varied)

    Empty/unknown → "" (caller treats as non-repeat, i.e. always exempt).
    """
    if source_tag == 'MUSIC' and selected_music_link:
        title = (selected_music_link.get('title') or '').strip()
        artist = (selected_music_link.get('artist') or '').strip()
        return _normalize_material_key(f"{title}|{artist}") if (title or artist) else ""
    if source_tag == 'MEME' and meme_content:
        return _normalize_material_key(meme_content.get('keyword') or '')
    return ""


def _is_recent_proactive_material(lanlan_name: str, source_tag: str, key: str) -> bool:
    """Whether *key* was pushed for *source_tag* within the recent window (exact match).

    Empty key → never a repeat (no material identity to compare on).
    """
    if not key:
        return False
    bucket = _proactive_material_history.get(lanlan_name, {}).get(source_tag)
    if not bucket:
        return False
    now = time.time()
    return any(
        k == key and now - ts < _RECENT_CHAT_MAX_AGE_SECONDS
        for ts, k in bucket
    )


def _record_proactive_material(lanlan_name: str, source_tag: str, key: str) -> None:
    """Record one successfully delivered material identity (skip empty keys)."""
    if not key:
        return
    per_tag = _proactive_material_history.setdefault(lanlan_name, {})
    if source_tag not in per_tag:
        per_tag[source_tag] = deque(maxlen=_PROACTIVE_MATERIAL_HISTORY_MAX)
    per_tag[source_tag].append((time.time(), key))


def _proactive_chat_totals_path() -> Path:
    return Path(get_config_manager().memory_dir) / _PROACTIVE_CHAT_TOTALS_FILENAME


async def _ensure_proactive_chat_totals_loaded() -> None:
    """Lazy-load the persisted cumulative counters + ever_delivered. Idempotent. File reads go to the thread pool.

    schema: {"version": 2,
             "totals": {<lanlan_name>: <int>, ...},
             "ever_delivered": {<lanlan_name>: true, ...}}

    A missing file / corrupted JSON is not fatal — start from empty, and the next
    increment writes a fresh file. The old schema v1 has no ever_delivered field,
    so it loads as an empty dict — after upgrading, the first proactive chat will
    "force-first re-deliver once" for existing users (at most once, because
    ever_delivered is set True and persisted immediately after delivery); this is
    a one-off v1→v2 migration cost and needs no dedicated migration script."""
    global _proactive_chat_totals_loaded
    if _proactive_chat_totals_loaded:
        return
    async with _proactive_chat_totals_lock:
        if _proactive_chat_totals_loaded:
            return
        path = _proactive_chat_totals_path()
        try:
            data = await asyncio.to_thread(read_json, path)
            totals = data.get('totals') if isinstance(data, dict) else None
            if isinstance(totals, dict):
                for k, v in totals.items():
                    if isinstance(k, str) and isinstance(v, (int, float)):
                        _proactive_chat_totals[k] = int(v)
            ever = data.get('ever_delivered') if isinstance(data, dict) else None
            if isinstance(ever, dict):
                for k, v in ever.items():
                    if isinstance(k, str) and bool(v):
                        _invite_ever_delivered[k] = True
        except FileNotFoundError:
            # 首次启动 / cleanup 后没文件——按全空起步，下次 increment 会创建。
            # 不是异常，不打 warning。
            pass
        except Exception as exc:
            logger.warning("proactive_chat_totals load failed: %s", exc)
        _proactive_chat_totals_loaded = True


def _get_proactive_chat_total(lanlan_name: str) -> int:
    """Synchronous read of cached counter. 0 if loaded-but-unset or not loaded yet.

    `_maybe_deliver_mini_game_invite` calls this after the caller has already
    awaited `_ensure_proactive_chat_totals_loaded()`, so there is no await here."""
    return int(_proactive_chat_totals.get(lanlan_name, 0))


def _was_invite_ever_delivered(lanlan_name: str) -> bool:
    """Synchronous read of ever-delivered flag.

    The caller must await ``_ensure_proactive_chat_totals_loaded()`` first."""
    return bool(_invite_ever_delivered.get(lanlan_name, False))


async def _persist_totals_unlocked() -> None:
    """Persist totals + ever_delivered to disk. The caller must hold _proactive_chat_totals_lock."""
    try:
        await atomic_write_json_async(
            _proactive_chat_totals_path(),
            {
                'version': _PROACTIVE_CHAT_TOTALS_SCHEMA_VERSION,
                'totals': dict(_proactive_chat_totals),
                'ever_delivered': dict(_invite_ever_delivered),
            },
            indent=2,
            ensure_ascii=False,
        )
    except Exception as exc:
        logger.warning(
            "proactive_chat_totals persist failed (in-memory still up-to-date): %s",
            exc,
        )


async def _increment_proactive_chat_total(lanlan_name: str) -> int:
    """+1 cached counter and persist atomically. Returns new value.

    Serialization is guaranteed by ``_proactive_chat_totals_lock``: concurrent
    proactive_chat calls each await a serial update, so no increment is lost.
    Persistence failures are not raised to the caller — the counter is
    best-effort; losing one +1 is not fatal, but the log line is kept."""
    await _ensure_proactive_chat_totals_loaded()
    async with _proactive_chat_totals_lock:
        new_value = _proactive_chat_totals.get(lanlan_name, 0) + 1
        _proactive_chat_totals[lanlan_name] = new_value
        await _persist_totals_unlocked()
    return new_value


async def _mark_invite_ever_delivered(lanlan_name: str) -> None:
    """One-shot set-True + persist. Skips the disk write when already True to save IO.

    Shares ``_proactive_chat_totals_lock`` with ``_increment_proactive_chat_total``
    so concurrent updates write totals + ever_delivered together atomically.

    ⚠️ The invite delivery path must not call ``_increment_proactive_chat_total +
    _mark_invite_ever_delivered`` separately — the lock is released between the
    two awaits, and a process dying in between leaves a ``totals: N+1,
    ever_delivered: stale`` half-state on disk, making force-first fire once more
    after restart. Use ``_record_invite_delivery_persistent`` for one atomic
    write under a single lock."""
    await _ensure_proactive_chat_totals_loaded()
    async with _proactive_chat_totals_lock:
        if _invite_ever_delivered.get(lanlan_name):
            return
        _invite_ever_delivered[lanlan_name] = True
        await _persist_totals_unlocked()


async def _record_invite_delivery_persistent(lanlan_name: str) -> int:
    """Atomic persistent record of one successfully delivered mini-game invite:
    counter +1 + ever_delivered=True written to disk once under one lock.
    Returns the new total.

    Reason to exist: doing +1 then mark as two separate awaits releases the lock
    in between; a process crash / coroutine cancel can leave a ``totals: N+1,
    ever_delivered: stale`` half-state on disk — after restart
    ``_was_invite_ever_delivered`` sees the stale false and force-first fires
    again. Pointed out by CodeRabbit Major review."""
    await _ensure_proactive_chat_totals_loaded()
    async with _proactive_chat_totals_lock:
        new_value = _proactive_chat_totals.get(lanlan_name, 0) + 1
        _proactive_chat_totals[lanlan_name] = new_value
        _invite_ever_delivered[lanlan_name] = True
        await _persist_totals_unlocked()
    return new_value


def _clear_channel_from_proactive_history(lanlan_name: str, channel: str) -> int:
    """Blank out the channel mark of the given channel's entries in _proactive_chat_history.

    Purpose: when the user gives strong positive feedback (e.g. a recommended
    song played all the way through), that amounts to explicitly accepting this
    channel's recent output, so _compute_source_weights should no longer
    penalize the channel for "just used". Clearing the channel field stops
    raw_score from accumulating those entries, while the message text stays in
    the deque for dedup / similarity / format_recent_proactive_chats reuse.

    Returns the number of entries cleared.
    """
    history = _proactive_chat_history.get(lanlan_name)
    if not history:
        return 0
    rewritten: list[tuple] = []
    cleared = 0
    for entry in history:
        if len(entry) >= 3 and entry[2] == channel:
            rewritten.append((entry[0], entry[1], ''))
            cleared += 1
        else:
            rewritten.append(entry)
    if cleared == 0:
        return 0
    history.clear()
    history.extend(rewritten)
    return cleared


def _normalize_text_for_similarity(text: str) -> str:
    """
    Text normalization (conservative strategy):
    - lowercase
    - collapse consecutive whitespace
    Only light normalization, to avoid false kills from over-cleaning.
    """
    text = (text or "").strip().lower()
    return re.sub(r'\s+', ' ', text)


def _is_similar_to_recent_proactive_chat(lanlan_name: str, message: str) -> tuple[bool, float]:
    """
    Check whether message is highly similar to recent proactive chats (high threshold against false kills).
    Returns (is_duplicate, best_score).
    """
    history = _proactive_chat_history.get(lanlan_name)
    if not history or not message.strip():
        return False, 0.0

    now = time.time()
    current = _normalize_text_for_similarity(message)
    if not current:
        return False, 0.0

    best = 0.0
    for entry in history:
        ts, old_msg = entry[0], entry[1]
        if now - ts >= _RECENT_CHAT_MAX_AGE_SECONDS:
            continue
        old_norm = _normalize_text_for_similarity(old_msg)
        if not old_norm:
            continue
        score = difflib.SequenceMatcher(None, current, old_norm).ratio()
        if score > best:
            best = score
        if score >= _PROACTIVE_SIMILARITY_THRESHOLD:
            return True, score
    return False, best
