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
import re
import time
import unicodedata
from contextlib import suppress
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from config import (
    PROACTIVE_CHAT_HISTORY_MAX,
    PROACTIVE_SOURCE_FORGET_P,
    PROACTIVE_SOURCE_HALF_LIFE_BY_KIND,
    PROACTIVE_SOURCE_HALF_LIFE_DEFAULT,
    PROACTIVE_SOURCE_HARD_SKIP_SECONDS,
)
from config.prompts.prompts_proactive import (
    RECENT_PROACTIVE_CHANNEL_LABELS,
    RECENT_PROACTIVE_CHATS_FOOTER,
    RECENT_PROACTIVE_CHATS_HEADER,
    RECENT_PROACTIVE_TIME_LABELS,
)
from config.prompts.prompts_sys import _loc
from utils.config_manager import get_config_manager
from utils.file_utils import atomic_write_json_async, read_json
from utils.logger_config import get_module_logger

logger = get_module_logger(__name__, "Main")


# --- Global source decay history (cross-character, persisted) ---
_SOURCE_HISTORY_FILENAME = "proactive_source_history.json"
_SOURCE_HISTORY_SCHEMA_VERSION = 1
_source_history: dict[str, dict[str, Any]] = {}
_source_history_lock = asyncio.Lock()
_source_history_loaded = False
_source_history_loaded_path: Path | None = None
# Whether **read paths** may treat memory as authoritative. Distinct from _source_history_loaded:
# that says memory represents the root at _source_history_loaded_path, while this says memory represents
# the root requested just now. They diverge only after switching roots when the new root fails to load; memory
# still contains the previous root's history. The write path checks the return value, but _should_skip_source would use A's
# history to suppress B's candidates, making her avoid material never discussed with this character.
# Reads prefer missed suppression (at worst repeat material) over cross-root suppression, so fail open here.
_source_history_authoritative = False
# Consecutive-failure counts only throttle logs. A transient read failure and permanent stoppage look identical
# in one warning; these two accumulated counts distinguish them. Reset after any success.
_source_history_read_failures = 0
_source_history_skipped_records = 0


def _should_log_repeated_failure(count: int) -> bool:
    """Report the first few consecutive failures, then back off to every 50th."""
    # Permanent read failure (e.g. broken file read ACL but writable directory, so os.replace still works) sends every
    # record through failure/skip. Warning every time becomes noise, hiding that she no longer records
    # used material. Report initial attempts for visibility, then every 50 failures
    # with cumulative counts, letting one log distinguish a hiccup from total stoppage.
    return count <= 3 or count % 50 == 0


def _resolve_memory_dir(memory_dir: str | Path | None) -> Path:
    """Resolve the persistence root while preserving the legacy singleton fallback."""
    if memory_dir is None:
        memory_dir = get_config_manager().memory_dir
    return Path(memory_dir)


def _source_history_path(*, memory_dir: str | Path | None = None) -> Path:
    return _resolve_memory_dir(memory_dir) / _SOURCE_HISTORY_FILENAME


def _source_hash(url: str = "", fallback_title: str = "") -> str:
    """Return a stable URL hash, falling back to a normalized title."""
    norm = (url or "").strip().lower().rstrip("/")
    if norm:
        return hashlib.sha256(norm.encode("utf-8")).hexdigest()
    title_norm = re.sub(r"\s+", " ", (fallback_title or "").strip().lower())
    if title_norm:
        return hashlib.sha256(("t::" + title_norm).encode("utf-8")).hexdigest()
    return ""


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
    """Return the in-memory source record, or None when it is not authoritative."""
    if not url_hash:
        return None
    if not _source_history_authoritative:
        # A previous load failed; memory may contain **another root's** history. memory_dir is absent from the signature;
        # adding it requires threading through _should_skip_source → candidate_selection / decisions /
        # two service sites / system_router re-export, five call sites, excessive for a fail-open
        # criterion. Have the loader record its conclusion in this flag, and reads check only the flag.
        return None
    return _source_history.get(url_hash)


async def _ensure_source_history_loaded(
    *, memory_dir: str | Path | None = None
) -> bool:
    """Load source history once without blocking the event loop.

    Returns whether the in-memory history is authoritative for *memory_dir*.
    A false return means the read failed and nothing may be overwritten yet.
    """
    # The return value matters: _record_source_used **overwrites all data** (entries is the entire
    # _source_history). On read failure, memory is either empty or contains the prior root's data;
    # treating either as loaded would truncate or replace all disk history at the next recording. Thus
    # this function must determine whether memory currently represents memory_dir, rather than leaving callers to guess.
    global _source_history_loaded, _source_history_loaded_path
    global _source_history_read_failures, _source_history_authoritative
    path = _source_history_path(memory_dir=memory_dir)
    if _source_history_loaded and _source_history_loaded_path == path:
        _source_history_authoritative = True
        return True
    async with _source_history_lock:
        if _source_history_loaded and _source_history_loaded_path == path:
            _source_history_authoritative = True
            return True
        # Parse into this local dict throughout; replace _source_history only at the end of the function.
        # This decouples exceptions/cancellation from global state:
        #   * Cancellation (the only suspension is to_thread below; CancelledError inherits BaseException,
        #     caught by neither except) no longer leaves flag=True, path=old root, memory empty;
        #     exactly the state being eliminated, where the next normal record overwrites everything with empty memory.
        #   * Read failure retains old-root memory unchanged and consistent with flag/path, requiring no flag clearing.
        # In other words, global flag / path / memory advance together or remain entirely unchanged.
        loaded: dict[str, dict[str, Any]] = {}
        try:
            data = await asyncio.to_thread(read_json, path)
        except FileNotFoundError:
            # Missing file (first startup / previously cleaned) means normal empty history, not failure.
            # Mark loaded normally; the next recording creates the file.
            data = None
        except (ValueError, TypeError, RecursionError) as exc:
            # Entire file cannot be parsed structurally (JSONDecodeError / UnicodeDecodeError subclass ValueError):
            # disk content is already unusable history with nothing worth protecting, and every retry
            # would fail. Start empty and let later overwrites repair it, or one corrupt file would permanently
            # stop recording used material.
            #
            # RecursionError belongs here too: sufficiently deep JSON nesting (`[[[[...]]]]`) raises it,
            # and it inherits neither ValueError nor OSError. Falling into the transient-failure branch below
            # creates the worst combination: content that **always** fails is treated as retryable,
            # permanently stopping recording, precisely what recovery is meant to prevent.
            logger.warning(
                "%s 内容损坏，按空历史起步并等待覆盖重建: %s: %s",
                _SOURCE_HISTORY_FILENAME,
                type(exc).__name__,
                exc,
            )
            data = None
        except Exception as exc:
            # Real read failure (IO / permissions / temporary antivirus or indexer file lock): disk content is probably
            # intact but temporarily unreadable. Never mark loaded, which would make empty memory falsely authoritative
            # and let the next full overwrite truncate all history. Leave everything untouched and retry next call.
            #
            # Every call retries, so permanent failures would spam here too; throttle using consecutive counts.
            _source_history_read_failures += 1
            if _should_log_repeated_failure(_source_history_read_failures):
                logger.warning(
                    "加载 %s 失败，本次不视为已加载（下次重试）: %s: %s，连续失败 %d 次",
                    _SOURCE_HISTORY_FILENAME,
                    type(exc).__name__,
                    exc,
                    _source_history_read_failures,
                )
            # Retain memory unchanged (clearing revives loaded flag / old-root path / empty-memory state,
            # causing the next old-root recording to overwrite everything with empty memory), but prohibit reads from using it
            # from now on, or old-root history would suppress new-root candidates.
            _source_history_authoritative = False
            return False
        entries = data.get("entries") if isinstance(data, dict) else None
        if isinstance(entries, dict):
            now = time.time()
            damaged = 0
            for source_hash, entry in entries.items():
                if not isinstance(entry, dict):
                    continue
                try:
                    age = now - float(entry.get("ts", 0.0) or 0.0)
                    probability = _source_skip_probability(
                        age,
                        _half_life_for(entry.get("kind", "web")),
                    )
                except (ValueError, TypeError, OverflowError):
                    # Skip individual entries rather than invalidating everything: string ts raises ValueError, unhashable kind
                    # raises TypeError, float() of a hundreds-digit integer ts raises OverflowError
                    # (an ArithmeticError rather than ValueError subclass; missing it lets it escape
                    # this function and break every proactive attempt). Each means only **one row** is broken. Partial corruption
                    # (one out of hundreds) is far more common than invalid JSON; discarding valid entries needlessly makes her
                    # repeat all that material. The broken row should be forgotten anyway, so dropping it costs nothing.
                    damaged += 1
                    continue
                if probability >= PROACTIVE_SOURCE_FORGET_P:
                    loaded[source_hash] = entry
            if damaged:
                # Summarize once per load instead of logging every row.
                logger.warning(
                    "%s 有 %d 条记录无法解析，已跳过（其余 %d 条正常载入）",
                    _SOURCE_HISTORY_FILENAME,
                    damaged,
                    len(loaded),
                )
        _source_history.clear()
        _source_history.update(loaded)
        _source_history_loaded = True
        _source_history_loaded_path = path
        _source_history_read_failures = 0
        _source_history_authoritative = True
        return True


async def _persist_source_history_unlocked(
    *, memory_dir: str | Path | None = None
) -> None:
    """Persist the in-memory source history. The caller must hold _source_history_lock."""
    # Intentionally take the snapshot here under lock, not from callers: a caller snapshot followed by unlocked persistence
    # lets two submissions reach os.replace in reverse snapshot order, overwriting newer history with older values;
    # concurrent os.replace to one Windows target also causes PermissionError(WinError 5).
    # Accepting no snapshot parameter structurally removes that reversal window rather than relying on calling conventions.
    try:
        await atomic_write_json_async(
            _source_history_path(memory_dir=memory_dir),
            {
                "v": _SOURCE_HISTORY_SCHEMA_VERSION,
                "entries": dict(_source_history),
            },
        )
    except Exception as exc:
        logger.warning(
            "落盘 %s 失败: %s: %s",
            _SOURCE_HISTORY_FILENAME,
            type(exc).__name__,
            exc,
        )


async def _record_source_used(
    *,
    url: str,
    kind: str,
    title: str = "",
    memory_dir: str | Path | None = None,
) -> None:
    """Update, prune and persist one consumed source record."""
    global _source_history_skipped_records
    source_hash = _source_hash(url, title)
    if not source_hash:
        return
    # Load before locking: _ensure_source_history_loaded acquires the same
    # _source_history_lock, so calling under lock self-deadlocks. Already-loaded checks compare only flag + path,
    # at negligible cost.
    #
    # Load again here because 'always load before recording' previously relied only on
    # handle_proactive_chat statement order: load was try-body statement 53, recordings at
    # 115/170, separated by thousands of lines and dozens of awaits. Moving a delivery branch earlier could
    # overwrite all history from empty memory silently without a failing test. Enforce the invariant within
    # this function so line ordering is no longer its only guarantee.
    # Pass memory_dir unchanged; loading and persistence both resolve through _source_history_path(memory_dir=...),
    # guaranteeing the same root on both sides.
    if not await _ensure_source_history_loaded(memory_dir=memory_dir):
        # Do not write if unreadable. This function overwrites everything; empty-memory writing would truncate disk history
        # to one entry. Missing this recording may repeat this material later, whereas truncating the database
        # can repeat every item: the impact differs by the entire inventory.
        #
        # This does not stop recording forever on read failure: every call retries loading, resuming as soon as disk
        # recovers. Permanent content corruption already loads as empty history and heals through overwriting,
        # never reaching this branch.
        #
        # Permanently unreadable disk is possible (broken read ACL, writable directory); recording then
        # stops forever and users see repeated material. Logs are the only visibility,
        # so include cumulative counts and throttle, avoiding spam without hiding stoppage.
        _source_history_skipped_records += 1
        if _should_log_repeated_failure(_source_history_skipped_records):
            logger.warning(
                "%s 未能加载，跳过本次 source 记录以免覆盖写截断盘上历史: "
                "kind=%s, 连续跳过 %d 次",
                _SOURCE_HISTORY_FILENAME,
                kind,
                _source_history_skipped_records,
            )
        return
    # Reset consecutive skips after successful loading: isolated failures must not accumulate
    # and delay the first report of a later true stoppage until attempt 50.
    _source_history_skipped_records = 0
    async with _source_history_lock:
        # Guard checks run outside the lock (_ensure acquires it and would self-deadlock); full writes run inside,
        # leaving a window: _ensure awaits IO while locked; after it releases and before this coroutine
        # acquires the lock, another root's _ensure can replace all memory. Recheck memory only here,
        # without calling _ensure (no deadlock or extra IO), confirming it still represents
        # the root being written. On mismatch, write nothing: overwriting root_a's file with root_b's contents
        # is far worse than missing one record.
        if not (
            _source_history_loaded
            and _source_history_loaded_path
            == _source_history_path(memory_dir=memory_dir)
        ):
            logger.warning(
                "%s 在加载与写入之间被换成了别的根，跳过本次 source 记录: kind=%s",
                _SOURCE_HISTORY_FILENAME,
                kind,
            )
            return
        _source_history[source_hash] = {
            "ts": time.time(),
            "kind": kind,
            "title": (title or "")[:80],
        }
        now = time.time()
        forgotten = [
            existing_hash
            for existing_hash, entry in _source_history.items()
            if _source_skip_probability(
                now - float(entry.get("ts", 0.0) or 0.0),
                _half_life_for(entry.get("kind", "web")),
            )
            < PROACTIVE_SOURCE_FORGET_P
        ]
        for existing_hash in forgotten:
            _source_history.pop(existing_hash, None)
        # Persist under lock too, symmetric with _increment_proactive_chat_total calling _persist_totals_unlocked:
        # this is one shared cross-character file; locking only memory during concurrent deliveries is ineffective.
        # Writing still runs via to_thread (inside atomic_write_json_async); the lock is asyncio.Lock,
        # not threading.Lock, so the event loop continues serving other requests while waiting.
        #
        # Once submitted, to_thread cannot be cancelled; it runs through os.replace. A direct
        # await is unsafe: cancellation (especially during shutdown) makes async with release the lock as
        # CancelledError passes through while os.replace remains in flight, breaking locked-write invariance. shield keeps
        # cancellation outside and the write running; explicitly await completion before propagating CancelledError.
        writer = asyncio.ensure_future(
            _persist_source_history_unlocked(memory_dir=memory_dir)
        )
        try:
            await asyncio.shield(writer)
        except asyncio.CancelledError:
            # Loop rather than wait once: a second cancellation (common at shutdown) interrupts asyncio.wait itself,
            # again releasing the lock early. The to_thread writer necessarily finishes, so the loop terminates.
            while not writer.done():
                with suppress(asyncio.CancelledError):
                    await asyncio.wait({writer})
            raise


# --- Recent proactive-chat records buffer ---
# {lanlan_name: deque([(timestamp, message), ...], maxlen=10)}
_proactive_chat_history: dict[str, deque] = {}


# --- Recent proactive material-identifier deduplication buffer (for ANTI_REPEAT_EXEMPT_SOURCE_TAGS) ---
# {lanlan_name: {source_tag: deque([(timestamp, material_key), ...], maxlen=N)}}
# Material-push channels (MUSIC/MEME) bypass dialogue-level repetition checks and deduplicate the material itself:
# MUSIC checks track (title|artist), MEME search keywords. Allow when current material differs from recent material;
# only similar material falls back to dialogue checks (MEME still blocks verbatim repetition with fresh material; see
# ANTI_REPEAT_VERBATIM_GUARD_SOURCE_TAGS). Process-local, reset on restart: short-term repetition protection,
# like _proactive_chat_history / _mini_game_invite_state, needs only memory state.
_proactive_material_history: dict[str, dict[str, deque]] = {}


_PROACTIVE_MATERIAL_HISTORY_MAX = 10


# --- Persist per-character successful proactive delivery count + whether ever invited ---
# Single-file schema:
#   {"version": 2,
#    "totals": {<lanlan_name>: <int>, ...},
#    "ever_delivered": {<lanlan_name>: true, ...}}
# Retain across restarts; one file for both data sets simplifies maintenance.
#
# - totals: force mini-game invitation on a new user's Nth proactive delivery (N=NEW_USER_FORCE_AT)
#   requires a count surviving restarts, or each app restart repeatedly forces invitations,
#   making invitation frequency inconsistent. Match _record_proactive_chat semantics: increment only on successful
#   user delivery, excluding PASS (the spec's Nth proactive message means actually received).
# - ever_delivered: one-way true flag indicating whether this character ever received a mini-game invitation,
#   used by force-first's new-user check. Unlike in-memory ``state.delivered_at``,
#   which PR-B's D2 'later' choice resets, ever_delivered never reverts after
#   True: a prior invitation is historical fact. Codex P1 review
#   noted that without it, force-first treats previously invited users as new after restarting and forces another invitation.
_PROACTIVE_CHAT_TOTALS_FILENAME = "proactive_chat_totals.json"


_PROACTIVE_CHAT_TOTALS_SCHEMA_VERSION = 2


_proactive_chat_totals: dict[str, int] = {}


_invite_ever_delivered: dict[str, bool] = {}


_proactive_chat_totals_lock = asyncio.Lock()


_proactive_chat_totals_loaded = False
_proactive_chat_totals_loaded_path: Path | None = None


_RECENT_CHAT_MAX_AGE_SECONDS = 3600  # Proactive records within one hour.


_PROACTIVE_SIMILARITY_THRESHOLD = 0.90  # Conservative hard-block threshold: abandon this attempt above 90% repetition.


def _recent_proactive_chat_entries(lanlan_name: str) -> tuple[tuple, ...]:
    """Return an immutable snapshot of one character's recent chat records."""
    history = _proactive_chat_history.get(lanlan_name)
    return tuple(history) if history else ()


def _format_recent_proactive_chats(lanlan_name: str, lang: str = "zh") -> str:
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
    recent = [
        entry for entry in history if now - entry[0] < _RECENT_CHAT_MAX_AGE_SECONDS
    ]
    if not recent:
        return ""

    tl = RECENT_PROACTIVE_TIME_LABELS.get(lang, RECENT_PROACTIVE_TIME_LABELS["en"])
    cl = RECENT_PROACTIVE_CHANNEL_LABELS.get(
        lang, RECENT_PROACTIVE_CHANNEL_LABELS["en"]
    )

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
            return tl["m"].format(m)
        return tl["h"].format(m // 60)

    header = _loc(RECENT_PROACTIVE_CHATS_HEADER, lang)
    footer = _loc(RECENT_PROACTIVE_CHATS_FOOTER, lang)
    lines = []
    for entry in recent:
        ts, msg = entry[0], entry[1]
        ch = entry[2] if len(entry) > 2 else ""
        # Filter vision-channel records to avoid hallucinations from citing expired screen content.
        if ch == "vision":
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


def _record_proactive_chat(lanlan_name: str, message: str, channel: str = ""):
    """
    Record one successful proactive chat (with its source channel).
    Logic:
    - get the current timestamp
    - append the record (timestamp, message content, channel) to the given model's queue in _proactive_chat_history
    - if the queue is full, the oldest record is popped automatically, keeping the length within maxlen (default 10)
    args:
    - lanlan_name: model name
    - message: chat content
    - channel: source channel (optional, default '')
    """
    if lanlan_name not in _proactive_chat_history:
        _proactive_chat_history[lanlan_name] = deque(maxlen=PROACTIVE_CHAT_HISTORY_MAX)
    _proactive_chat_history[lanlan_name].append((time.time(), message, channel))

    # Telemetry: actual proactive delivery. channel is a low-cardinality enum (vision/news/video/
    # personal/music/meme/mini_game/...), truncated to prevent accidental high cardinality. Combined with settings_state's
    # proactive configuration, this reveals heavy users' actual daily proactive-delivery frequency.
    #
    # Do not pair responded rates here: that would require core turns and proactive state to share
    # last-delivery time, increasing lifecycle coupling. Initially estimate response rates server-side by correlating
    # proactive_fired times with user-message activity timestamps; design exact pairing separately.
    try:
        from utils.instrument import counter as _instr_counter

        _instr_counter("proactive_fired", channel=(str(channel) or "default")[:24])
    except Exception:
        # Telemetry failure must not affect proactive delivery.
        pass


def _normalize_material_key(raw: str) -> str:
    """Normalize a material identity string for exact-match dedup (lowercase + collapse whitespace)."""
    s = (raw or "").strip().lower()
    return re.sub(r"\s+", " ", s)


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
    if source_tag == "MUSIC" and selected_music_link:
        title = (selected_music_link.get("title") or "").strip()
        artist = (selected_music_link.get("artist") or "").strip()
        return _normalize_material_key(f"{title}|{artist}") if (title or artist) else ""
    if source_tag == "MEME" and meme_content:
        return _normalize_material_key(meme_content.get("keyword") or "")
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
    return any(k == key and now - ts < _RECENT_CHAT_MAX_AGE_SECONDS for ts, k in bucket)


def _record_proactive_material(lanlan_name: str, source_tag: str, key: str) -> None:
    """Record one successfully delivered material identity (skip empty keys)."""
    if not key:
        return
    per_tag = _proactive_material_history.setdefault(lanlan_name, {})
    if source_tag not in per_tag:
        per_tag[source_tag] = deque(maxlen=_PROACTIVE_MATERIAL_HISTORY_MAX)
    per_tag[source_tag].append((time.time(), key))


def _proactive_turn_still_owned(mgr: Any, proactive_sid: Any) -> bool:
    """Whether cleanup may still target the rejected proactive turn safely."""
    return (
        mgr.current_speech_id == proactive_sid
        and getattr(mgr.state, "proactive_sid", proactive_sid) == proactive_sid
        and not mgr.state.is_proactive_preempted(proactive_sid)
    )


async def _enter_proactive_phase2(
    mgr: Any,
    proactive_sid: Any,
    *,
    log: Any = None,
    round_id: str | None = None,
) -> bool:
    """Enter Phase 2 only if user engagement stays unchanged across the await."""
    from main_logic.session_state import SessionEvent

    active_logger = log or logger
    expected_user_engagement_time = getattr(
        mgr,
        "last_user_engagement_time",
        None,
    )
    round_payload = {"round_id": round_id} if round_id is not None else {}
    await mgr.state.fire(SessionEvent.PROACTIVE_PHASE2, **round_payload)
    if (
        (round_id is not None and mgr.state.proactive_round_id != round_id)
        or mgr.state.is_proactive_preempted(proactive_sid)
        or getattr(mgr, "last_user_engagement_time", None)
        != expected_user_engagement_time
    ):
        active_logger.info(
            "proactive Phase 2 abandoned: user engaged during transition"
        )
        if _proactive_turn_still_owned(mgr, proactive_sid):
            await mgr.handle_new_message()
        return False
    return True


def _proactive_feed_rejected_for_takeover(
    mgr: Any,
    proactive_sid: Any,
    expected_user_engagement_time: Any,
) -> bool:
    """Distinguish a guarded-feed rejection from a local TTS enqueue failure."""
    return (
        not _proactive_turn_still_owned(mgr, proactive_sid)
        or getattr(mgr, "last_user_engagement_time", None)
        != expected_user_engagement_time
    )


def _proactive_chat_totals_path(*, memory_dir: str | Path | None = None) -> Path:
    return _resolve_memory_dir(memory_dir) / _PROACTIVE_CHAT_TOTALS_FILENAME


async def _ensure_proactive_chat_totals_loaded(
    *, memory_dir: str | Path | None = None
) -> None:
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
    global _proactive_chat_totals_loaded, _proactive_chat_totals_loaded_path
    path = _proactive_chat_totals_path(memory_dir=memory_dir)
    if _proactive_chat_totals_loaded and _proactive_chat_totals_loaded_path == path:
        return
    async with _proactive_chat_totals_lock:
        if _proactive_chat_totals_loaded and _proactive_chat_totals_loaded_path == path:
            return
        _proactive_chat_totals.clear()
        _invite_ever_delivered.clear()
        try:
            data = await asyncio.to_thread(read_json, path)
            totals = data.get("totals") if isinstance(data, dict) else None
            if isinstance(totals, dict):
                for k, v in totals.items():
                    if isinstance(k, str) and isinstance(v, (int, float)):
                        _proactive_chat_totals[k] = int(v)
            ever = data.get("ever_delivered") if isinstance(data, dict) else None
            if isinstance(ever, dict):
                for k, v in ever.items():
                    if isinstance(k, str) and bool(v):
                        _invite_ever_delivered[k] = True
        except FileNotFoundError:
            # No file on first startup / after cleanup: start entirely empty; the next increment creates it.
            # This is normal; do not warn.
            pass
        except Exception as exc:
            logger.warning("proactive_chat_totals load failed: %s", exc)
        _proactive_chat_totals_loaded = True
        _proactive_chat_totals_loaded_path = path


def _get_proactive_chat_total(lanlan_name: str) -> int:
    """Synchronous read of cached counter. 0 if loaded-but-unset or not loaded yet.

    `_maybe_deliver_mini_game_invite` calls this after the caller has already
    awaited `_ensure_proactive_chat_totals_loaded()`, so there is no await here."""
    return int(_proactive_chat_totals.get(lanlan_name, 0))


def _was_invite_ever_delivered(lanlan_name: str) -> bool:
    """Synchronous read of ever-delivered flag.

    The caller must await ``_ensure_proactive_chat_totals_loaded()`` first."""
    return bool(_invite_ever_delivered.get(lanlan_name, False))


async def _persist_totals_unlocked(*, memory_dir: str | Path | None = None) -> None:
    """Persist totals + ever_delivered to disk. The caller must hold _proactive_chat_totals_lock."""
    try:
        await atomic_write_json_async(
            _proactive_chat_totals_path(memory_dir=memory_dir),
            {
                "version": _PROACTIVE_CHAT_TOTALS_SCHEMA_VERSION,
                "totals": dict(_proactive_chat_totals),
                "ever_delivered": dict(_invite_ever_delivered),
            },
            indent=2,
            ensure_ascii=False,
        )
    except Exception as exc:
        logger.warning(
            "proactive_chat_totals persist failed (in-memory still up-to-date): %s",
            exc,
        )


async def _increment_proactive_chat_total(
    lanlan_name: str, *, memory_dir: str | Path | None = None
) -> int:
    """+1 cached counter and persist atomically. Returns new value.

    Serialization is guaranteed by ``_proactive_chat_totals_lock``: concurrent
    proactive_chat calls each await a serial update, so no increment is lost.
    Persistence failures are not raised to the caller — the counter is
    best-effort; losing one +1 is not fatal, but the log line is kept."""
    await _ensure_proactive_chat_totals_loaded(memory_dir=memory_dir)
    async with _proactive_chat_totals_lock:
        new_value = _proactive_chat_totals.get(lanlan_name, 0) + 1
        _proactive_chat_totals[lanlan_name] = new_value
        await _persist_totals_unlocked(memory_dir=memory_dir)
    return new_value


async def _mark_invite_ever_delivered(
    lanlan_name: str, *, memory_dir: str | Path | None = None
) -> None:
    """One-shot set-True + persist. Skips the disk write when already True to save IO.

    Shares ``_proactive_chat_totals_lock`` with ``_increment_proactive_chat_total``
    so concurrent updates write totals + ever_delivered together atomically.

    ⚠️ The invite delivery path must not call ``_increment_proactive_chat_total +
    _mark_invite_ever_delivered`` separately — the lock is released between the
    two awaits, and a process dying in between leaves a ``totals: N+1,
    ever_delivered: stale`` half-state on disk, making force-first fire once more
    after restart. Use ``_record_invite_delivery_persistent`` for one atomic
    write under a single lock."""
    await _ensure_proactive_chat_totals_loaded(memory_dir=memory_dir)
    async with _proactive_chat_totals_lock:
        if _invite_ever_delivered.get(lanlan_name):
            return
        _invite_ever_delivered[lanlan_name] = True
        await _persist_totals_unlocked(memory_dir=memory_dir)


async def _record_invite_delivery_persistent(
    lanlan_name: str, *, memory_dir: str | Path | None = None
) -> int:
    """Atomic persistent record of one successfully delivered mini-game invite:
    counter +1 + ever_delivered=True written to disk once under one lock.
    Returns the new total.

    Reason to exist: doing +1 then mark as two separate awaits releases the lock
    in between; a process crash / coroutine cancel can leave a ``totals: N+1,
    ever_delivered: stale`` half-state on disk — after restart
    ``_was_invite_ever_delivered`` sees the stale false and force-first fires
    again. Pointed out by CodeRabbit Major review."""
    await _ensure_proactive_chat_totals_loaded(memory_dir=memory_dir)
    async with _proactive_chat_totals_lock:
        new_value = _proactive_chat_totals.get(lanlan_name, 0) + 1
        _proactive_chat_totals[lanlan_name] = new_value
        _invite_ever_delivered[lanlan_name] = True
        await _persist_totals_unlocked(memory_dir=memory_dir)
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
            rewritten.append((entry[0], entry[1], ""))
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
    return re.sub(r"\s+", " ", text)


@dataclass(frozen=True, slots=True)
class ProactiveSimilarityMatch:
    """Detailed evidence for the recent-proactive literal similarity guard."""

    is_duplicate: bool = False
    best_score: float = 0.0
    matched_text: str = ""
    common_fragment: str = ""


def _find_similar_recent_proactive_chat(
    lanlan_name: str,
    message: str,
) -> ProactiveSimilarityMatch:
    """Return the best recent literal-similarity match and bounded evidence.

    The existing compatibility wrapper below intentionally keeps returning the
    historical ``(bool, score)`` tuple.  Runtime observability needs the matched
    text only long enough to derive a privacy-bounded repeat fragment; callers
    must not persist ``matched_text`` itself.
    """
    history = _proactive_chat_history.get(lanlan_name)
    if not history or not message.strip():
        return ProactiveSimilarityMatch()

    now = time.time()
    current = _normalize_text_for_similarity(message)
    if not current:
        return ProactiveSimilarityMatch()

    best_score = 0.0
    best_text = ""
    best_fragment = ""
    for entry in history:
        ts, old_msg = entry[0], entry[1]
        if now - ts >= _RECENT_CHAT_MAX_AGE_SECONDS:
            continue
        old_norm = _normalize_text_for_similarity(old_msg)
        if not old_norm:
            continue
        matcher = difflib.SequenceMatcher(None, current, old_norm)
        score = matcher.ratio()
        if score <= best_score:
            continue
        blocks = matcher.get_matching_blocks()
        longest = max(blocks, key=lambda block: block.size)
        best_score = score
        best_text = old_msg
        best_fragment = current[longest.a : longest.a + longest.size].strip()

    return ProactiveSimilarityMatch(
        is_duplicate=best_score >= _PROACTIVE_SIMILARITY_THRESHOLD,
        best_score=best_score,
        matched_text=best_text,
        common_fragment=best_fragment,
    )


# Emoji glue that is not category P/S/Z: variation selectors (Mn), the ZWJ that
# joins sequences (Cf), the keycap mark (Me) and the tag characters that spell
# subdivision flags (Cf, U+E0020..U+E007F). Listed explicitly rather than
# dropping every Mn/Cf so accents and other real combining marks survive.
_VERBATIM_EMOJI_GLUE = frozenset(
    "\ufe0e\ufe0f\u200d\u20e3" + "".join(map(chr, range(0xE0020, 0xE0080)))
)


def _is_verbatim_content_char(ch: str) -> bool:
    return (
        not ch.isspace()
        and ch not in _VERBATIM_EMOJI_GLUE
        and unicodedata.category(ch)[0] not in "PSZ"
    )


def _verbatim_key(text: str) -> str:
    """Reduce *text* to its words: drop whitespace/punctuation/symbols/emoji, NFKC, lowercase."""
    # Filter on the ORIGINAL categories first: NFKC expands compatibility
    # symbols into letters (™ -> "TM", ℡ -> "TEL", ㈱ -> 株), which would make
    # a trailing symbol count as content. Filter again afterwards because NFKC
    # can also emit new separators (½ -> "1⁄2", NBSP -> space).
    content = "".join(filter(_is_verbatim_content_char, text or ""))
    normalized = unicodedata.normalize("NFKC", content).lower()
    return "".join(filter(_is_verbatim_content_char, normalized))


def _find_verbatim_recent_proactive_chat(
    lanlan_name: str,
    message: str,
) -> ProactiveSimilarityMatch:
    """Match only a recent proactive chat that says exactly the same words.

    Stricter than the similarity guard on purpose: channels that push fresh
    material (see ``ANTI_REPEAT_VERBATIM_GUARD_SOURCE_TAGS``) may reuse a similar
    caption, and only a pure repeat that differs in punctuation, spacing or
    emoji at most counts as a duplicate.
    """
    current = _verbatim_key(message)
    history = _proactive_chat_history.get(lanlan_name)
    if not current or not history:
        return ProactiveSimilarityMatch()
    now = time.time()
    for entry in reversed(history):
        ts, old_msg = entry[0], entry[1]
        if now - ts >= _RECENT_CHAT_MAX_AGE_SECONDS:
            continue
        if _verbatim_key(old_msg) == current:
            return ProactiveSimilarityMatch(
                is_duplicate=True,
                best_score=1.0,
                matched_text=old_msg,
                common_fragment=_normalize_text_for_similarity(message),
            )
    return ProactiveSimilarityMatch()


def _is_similar_to_recent_proactive_chat(
    lanlan_name: str, message: str
) -> tuple[bool, float]:
    """
    Check whether message is highly similar to recent proactive chats (high threshold against false kills).
    Returns (is_duplicate, best_score).
    """
    match = _find_similar_recent_proactive_chat(lanlan_name, message)
    return match.is_duplicate, match.best_score
