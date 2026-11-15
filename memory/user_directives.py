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

"""
UserDirectivesManager — per-character store for explicit user ban-topic
directives ("别再提 X / stop saying X / その話はもう / ...").

Motivation
--------
The current-round LLM sees the user's original words; no intervention needed
here. But on the next session restart (archive-triggered / cold start /
reconnect), that message has long been wiped by ``compress_history`` and the
model steps on the landmine again. So extracted terms are persisted, and
``_build_initial_prompt`` splices a block into the system prompt tail at
startup.

Two things the startup injection alone does not cover, both wired on top of
this module:
- **Mid-session**: the system prompt is built once per session, so a directive
  recorded at turn N does not reach the prompt until the next rebuild (up to
  ~10 user turns away via hot-swap). ``mark_pending_injection`` /
  ``take_pending_injection`` hand the "there is something new" flag to
  ``main_logic`` (L4), which pushes the rendered block into the live session.
  This module cannot do that itself — ``memory`` (L3) may not import upward.
- **Proactive chat**: its Phase 2 prompt is assembled separately and never went
  through ``_build_initial_prompt``. ``get_active_terms`` feeds both its prompt
  section and its output-side drop gate.

Design notes
--------
- **Dispatch entry**: ``dispatch_user_utterance`` fan-out. This module
  self-registers via ``register_user_utterance_sink`` at import time, same
  style as ``plugin/core/state.py`` (dedup-on-identity; repeated registration
  doesn't re-fire).
- **Extraction**: ``config.prompts.prompts_directives.extract_directives``
  runs all locales in parallel (mixed Chinese/English speech is common); on a
  hit the term is cleaned by ``_trim_term``.
- **Dedup key**: ``(kind, term.casefold())``. Repeated hits → refresh
  ``last_seen_at`` / ``expire_at`` + ``hit_count += 1``; new entries get stored.
- **Storage**: ``memory/{name}/user_directives.json``. Schema: see ``_DEFAULT_FILE``.
  Capped at ``USER_DIRECTIVE_MAX_STORED`` rows; overflow rotates the
  least-recently-seen out (expired rows go first).
- **TTL**: each record's ``expire_at = last_seen_at + _effective_ttl(hit_count)``
  — the base window scaled by how many times the user has repeated it, capped
  at ``USER_DIRECTIVE_TTL_MAX_SECONDS``. Filtered on read; ``purge_expired``
  rewrites the file (optional; lazy is fine).
- **Prompt injection**: ``render_prompt_block(name, lang)`` returns the
  assembled string (with leading newline), "" when empty. Callers just do
  ``prompt += ...``.
- **Concurrency**: per-character ``threading.Lock``, pattern copied from
  ``memory/cursors.py``.

What is not extracted
----------
- Object-less "闭嘴/换话题/shut up": already in this round's context, the
  model sees it; persisting carries no concrete topic, and pushing such intent
  into the next round's prompt would backfire.
- Plain statements like "我不喜欢西瓜" ("I don't like watermelon"): not an
  explicit ban-topic directive; preference extraction belongs to the
  fact/persona pipeline.

False-positive policy
--------
The regex templates are lenient. Cost of a false kill = the user says an
equivalent sentence once more; cost of a miss = the user gets offended again —
so we lean toward over-killing. Terms are stored only when length ∈ [2, 40];
out-of-range ones are dropped.
"""  # noqa: DOCSTRING_CJK
from __future__ import annotations

import os
import threading
import time
from typing import Any, Dict, List, Optional

from config import (
    USER_DIRECTIVE_MAX_ACTIVE,
    USER_DIRECTIVE_MAX_STORED,
    USER_DIRECTIVE_TTL_MAX_SECONDS,
    USER_DIRECTIVE_TTL_SECONDS,
)
from config.prompts.prompts_directives import (
    extract_directives,
    render_directives_block,
)
from utils.config_manager import get_config_manager
from utils.file_utils import atomic_write_json
from utils.logger_config import get_module_logger

logger = get_module_logger(__name__, "Memory")


# Disk schema version: bump directive semantic changes; _load_unlocked handles legacy files.
_SCHEMA_VERSION = 1


def _now() -> float:
    return time.time()


def _default_payload() -> Dict[str, Any]:
    return {"version": _SCHEMA_VERSION, "directives": []}


# Shared storage invariant: stripped terms have length 2..40.
# Both reads and record writes use this helper, dropping legacy
# short/long/nonstring terms on next load to keep disk clean (CodeRabbit Minor).
_TERM_MIN_LEN = 2
_TERM_MAX_LEN = 40


def _normalize_term(raw: Any) -> Optional[str]:
    """Normalize a term: ``str.strip()`` then enforce length ∈ [2, 40], else None."""
    if not isinstance(raw, str):
        return None
    term = raw.strip()
    if not (_TERM_MIN_LEN <= len(term) <= _TERM_MAX_LEN):
        return None
    return term


def _effective_ttl(hit_count: Any) -> float:
    """TTL for a directive repeated ``hit_count`` times, capped at the max.

    Linear in the repeat count: saying it once buys the base window, saying it
    again buys another one, and so on until the cap.
    """
    # Single mentions may reflect temporary emotions; repeated ones indicate stable preferences. Cap growth,
    # or dozens of repeats create permanent personas without a current user-visible deletion UI.
    # Use linear, not exponential growth: exponentials reach 30-day caps by hit_count=5,
    # collapsing meaningful more-repeats/longer-memory distinctions too early.
    try:
        hits = int(hit_count)
    except (TypeError, ValueError):
        hits = 1
    hits = max(1, hits)
    return float(min(USER_DIRECTIVE_TTL_SECONDS * hits, USER_DIRECTIVE_TTL_MAX_SECONDS))


def _normalize_entry(raw: Any) -> Optional[Dict[str, Any]]:
    """Normalize one record read from disk into a dict; invalid / missing fields → None (dropped).

    Backward compat: early versions may have had only ``term`` and
    ``created_at`` — this backfills ``last_seen_at`` / ``expire_at`` /
    ``hit_count`` / ``kind`` / ``locale``.

    ⚠️ Fault tolerance: one dirty record (e.g. ``created_at: "abc"``) must not
    fail the whole file load and reset every valid directive. The whole
    function is wrapped in one try/except; this record returns None for the
    caller to drop while keeping the others (CodeRabbit Minor).
    """
    if not isinstance(raw, dict):
        return None
    try:
        term = _normalize_term(raw.get("term"))
        if term is None:
            return None
        kind = raw.get("kind") or "ban_topic"
        if not isinstance(kind, str):
            kind = "ban_topic"
        locale = raw.get("locale") if isinstance(raw.get("locale"), str) else "und"
        try:
            created_at = float(raw.get("created_at") or 0) or _now()
        except (TypeError, ValueError):
            created_at = _now()
        try:
            last_seen_at = float(raw.get("last_seen_at") or created_at)
        except (TypeError, ValueError):
            last_seen_at = created_at
        # Compute hit_count before expire_at because TTL now depends on it through
        # _effective_ttl; legacy backfills need actual counts, or ten-repeat
        # directives load as one-repeat expirations and instantly lose renewal duration.
        try:
            hit_count = int(raw.get("hit_count") or 1)
        except (TypeError, ValueError):
            hit_count = 1
        hit_count = max(1, hit_count)
        # Backfill missing legacy expire_at from last_seen plus each entry's own TTL.
        try:
            expire_at = float(raw.get("expire_at") or 0) or (
                last_seen_at + _effective_ttl(hit_count)
            )
        except (TypeError, ValueError):
            expire_at = last_seen_at + _effective_ttl(hit_count)
        return {
            "term": term,
            "kind": kind,
            "locale": locale,
            "created_at": created_at,
            "last_seen_at": last_seen_at,
            "expire_at": expire_at,
            "hit_count": max(1, hit_count),
            "source": raw.get("source") or "regex",
        }
    except Exception:
        return None


class UserDirectivesManager:
    """Per-character ban-topic store (thread-safe).

    Usage:
        mgr = UserDirectivesManager()
        mgr.record_from_text(lanlan_name, raw_user_text)
        block = mgr.render_prompt_block(lanlan_name, lang='zh')
        # concat the block straight onto the system prompt tail

    A single process-wide instance ``_GLOBAL_MANAGER`` (see module tail); the
    sink is registered on it too.
    """

    def __init__(self) -> None:
        self._config_manager = get_config_manager()
        self._cache: Dict[str, List[Dict[str, Any]]] = {}
        self._locks: Dict[str, threading.Lock] = {}
        self._locks_guard = threading.Lock()
        # Process-local flags for characters with newly persisted directives not yet injected into live sessions.
        # Store names only, not term lists. Injection requires the complete active
        # list, symmetric with _build_initial_prompt; render_prompt_block
        # computes it fresh. Storing terms adds deduplication/cap handling and
        # conflicts with complete-render semantics over new versus all terms.
        self._pending_injection: set[str] = set()
        self._pending_guard = threading.Lock()

    # ── path / lock ───────────────────────────────────────────

    def _file_path(self, name: str) -> str:
        # Lazy imports avoid memory/__init__.py cycles, matching cursors.py.
        from memory import ensure_character_dir
        return os.path.join(
            ensure_character_dir(self._config_manager.memory_dir, name),
            "user_directives.json",
        )

    def _get_lock(self, name: str) -> threading.Lock:
        if name not in self._locks:
            with self._locks_guard:
                if name not in self._locks:
                    self._locks[name] = threading.Lock()
        return self._locks[name]

    # -- Load/save under caller-held locks --

    def _load_unlocked(self, name: str) -> List[Dict[str, Any]]:
        if name in self._cache:
            return self._cache[name]
        directives: List[Dict[str, Any]] = []
        path = self._file_path(name)
        if os.path.exists(path):
            try:
                import json
                with open(path, encoding="utf-8") as f:
                    raw = json.load(f)
                items_raw = raw.get("directives") if isinstance(raw, dict) else None
                if isinstance(items_raw, list):
                    for r in items_raw:
                        norm = _normalize_entry(r)
                        if norm is not None:
                            directives.append(norm)
            except Exception as exc:  # Corrupt files are nonfatal; restart empty.
                logger.warning(
                    "[UserDirectives] load failed for %s, starting empty: %s",
                    name, exc,
                )
                directives = []
        self._cache[name] = directives
        return directives

    def _save_unlocked(self, name: str) -> None:
        # Deliberately retain synchronous persistence on the event-loop thread:
        #
        # async _process_stream_data_internal calls
        #   synchronous _publish_user_utterance_to_plugin_bus,
        #   then synchronous fan-out dispatch_user_utterance,
        #   then _on_user_utterance -> record_from_text -> here.
        # dispatch_user_utterance promises synchronous fan-out with third-party plugin
        # handlers in plugin.core.state/quota.dropper. Making this async moves every
        # handler into worker threads, an event-bus architectural tradeoff that one disk
        # write should not decide incidentally.
        #
        # Acceptable cost: write only on actual directive regex matches, such as topic bans,
        # a handful per session with JSON payloads of hundreds of bytes.
        #
        # Future changes should mirror anti_repeat's stage/aflush split: update memory on
        # caller threads so same-turn reads see fresh caches, offload fsync only,
        # and add turn-lifecycle flush points.
        #
        # noqa currently suppresses no warning: check_async_blocking scans depth 1,
        # missing this depth-6 call. Retain searchability and documentation for future
        # deeper guards without renewed archaeology.
        path = self._file_path(name)
        payload = {
            "version": _SCHEMA_VERSION,
            "directives": self._cache.get(name, []),
        }
        try:
            atomic_write_json(path, payload, indent=2, ensure_ascii=False)  # noqa: ASYNC_BLOCK — Synchronous fan-out contract, documented above.
        except Exception as exc:
            logger.warning("[UserDirectives] save failed for %s: %s", name, exc)

    # ── public API ────────────────────────────────────────────

    def record(
        self,
        name: str,
        *,
        locale: str,
        kind: str,
        term: str,
        source: str = "regex",
        now: Optional[float] = None,
    ) -> Dict[str, Any]:
        """Register a directive; an existing ``(kind, term.casefold())`` hit refreshes it.

        Returns the final persisted dict (with merged/refreshed fields); invalid
        input (term not a str / empty after trim / length out of range) returns
        an empty dict.

        ⚠️ The write boundary also goes through ``_normalize_term`` — sharing
        the same length invariant as ``_normalize_entry``'s read validation, so
        the on-disk state always satisfies [_TERM_MIN_LEN, _TERM_MAX_LEN]
        (CodeRabbit Minor).
        """
        if not name:
            return {}
        term_norm = _normalize_term(term)
        if term_norm is None:
            return {}
        term = term_norm
        ts = float(now if now is not None else _now())
        key = (kind, term.casefold())
        with self._get_lock(name):
            entries = self._load_unlocked(name)
            for e in entries:
                if (e["kind"], e["term"].casefold()) == key:
                    hits = int(e.get("hit_count", 1)) + 1
                    e["last_seen_at"] = ts
                    # Renewal uses the incremented repeat count; this repetition itself
                    # proves another user reminder and must affect current renewal,
                    # not wait for another future hit.
                    e["expire_at"] = ts + _effective_ttl(hits)
                    e["hit_count"] = hits
                    # Keep first-hit locale as the more diagnostic signal.
                    # Rotate refreshed entries too; legacy files predate caps and may contain
                    # hundreds of entries. Users only repeating old directives never reach
                    # insertion branches, so without rotation here files never shrink,
                    # and each record still fully reads/writes them synchronously
                    # on every user-message chain.
                    self._rotate_unlocked(name, now=ts)
                    self._save_unlocked(name)
                    return self._report_if_kept(name, e)
            new_entry = {
                "term": term,
                "kind": kind,
                "locale": locale,
                "created_at": ts,
                "last_seen_at": ts,
                "expire_at": ts + _effective_ttl(1),
                "hit_count": 1,
                "source": source,
            }
            entries.append(new_entry)
            self._rotate_unlocked(name, now=ts)
            self._save_unlocked(name)
            return self._report_if_kept(name, new_entry)

    def _report_if_kept(
        self, name: str, entry: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Return ``entry`` only if rotate kept it; ``{}`` when it was evicted.

        Caller holds the lock and has already rotated + persisted.
        """
        # Rotation can evict the just-written entry on both new/refresh branches: stable
        # ties retain old order, placing appended new entries at the truncated tail;
        # refresh timestamps may also precede existing ones. Existing cap-sized active
        # sets whose last_seen all >=ts trigger this through
        # NTP/resume/dual-boot timezone clock rollback in Windows desktop usage,
        # or same-millisecond ties.
        #
        # Unconditional success makes record_from_text -> mark_pending_injection
        # schedule rendering for terms absent from disk/memory,
        # disabling repeated bans in both current and next sessions.
        #
        # Share one helper across both branches. Refresh initially omitted this check
        # because its rotation was added later to fix oversized legacy files,
        # without reevaluating return values, caught as greptile P1.
        if any(item is entry for item in self._cache.get(name, ())):
            return dict(entry)
        # Never print raw banned terms: users explicitly rejected hearing them,
        # e.g. ex-partners/illness/deceased names, and this logger persists to disk. The same
        # proactive gate rule already replaced terms=%s with counts, but this
        # helper reintroduced raw terms, caught by greptile P1. Character name and the
        # diagnostic message suffice without exposing which term.
        logger.warning(
            "[UserDirectives] %s: a directive was rotated out immediately "
            "(store at cap with newer timestamps); not reporting it as recorded",
            name,
        )
        return {}

    def _rotate_unlocked(self, name: str, *, now: float) -> int:
        """Cap the stored list at ``USER_DIRECTIVE_MAX_STORED``, oldest first.

        Returns the number of entries dropped. Caller holds the lock.
        """
        entries = self._cache.get(name)
        if not entries or len(entries) <= USER_DIRECTIVE_MAX_STORED:
            return 0
        # Purge expired entries before last_seen eviction: get_active cannot see dead
        # directives, so letting them displace live user intent substitutes
        # dead data for actual preferences.
        alive = [e for e in entries if float(e.get("expire_at", 0)) > now]
        expired_dropped = len(entries) - len(alive)
        if len(alive) > USER_DIRECTIVE_MAX_STORED:
            # Keep latest N by last_seen_at, matching get_active ordering;
            # injection takes newest while eviction removes least recently mentioned.
            alive.sort(key=lambda e: float(e.get("last_seen_at", 0)), reverse=True)
            alive = alive[:USER_DIRECTIVE_MAX_STORED]
        dropped = len(entries) - len(alive)
        self._cache[name] = alive
        if dropped > expired_dropped:
            logger.info(
                "[UserDirectives] %s: rotated out %d directive(s) over the "
                "%d cap (%d of them already expired)",
                name, dropped, USER_DIRECTIVE_MAX_STORED, expired_dropped,
            )
        return dropped

    def record_from_text(
        self,
        name: str,
        text: str,
        *,
        now: Optional[float] = None,
    ) -> List[Dict[str, Any]]:
        """Run the full extract → store pipeline over a user text.

        Returns the list of entries written/refreshed this time (empty = no pattern hit).
        """
        if not name or not text:
            return []
        hits = extract_directives(text)
        if not hits:
            return []
        ts = float(now if now is not None else _now())
        out: List[Dict[str, Any]] = []
        for locale, kind, term in hits:
            out.append(
                self.record(
                    name,
                    locale=locale,
                    kind=kind,
                    term=term,
                    source="regex",
                    now=ts,
                )
            )
        # record returns {} for invalid terms; do not mark injection when all were dropped.
        if any(out):
            self.mark_pending_injection(name)
        return out

    # -- Mid-session pending-injection flags --

    def mark_pending_injection(self, name: str) -> None:
        """Flag that ``name`` has fresh directives not yet pushed into a live session."""
        if not name:
            return
        with self._pending_guard:
            self._pending_injection.add(name)

    def take_pending_injection(self, name: str) -> bool:
        """Consume the pending flag for ``name``; True when there was one.

        Take-once: the flag is cleared even if the caller then fails to inject.
        A dropped injection costs at most the pre-existing behaviour (the block
        still lands at the next session rebuild), whereas a flag that survives
        a failure would re-inject the same block on every later turn.
        """
        if not name:
            return False
        with self._pending_guard:
            if name not in self._pending_injection:
                return False
            self._pending_injection.discard(name)
            return True

    def get_active(
        self,
        name: str,
        *,
        now: Optional[float] = None,
        limit: int = USER_DIRECTIVE_MAX_ACTIVE,
    ) -> List[Dict[str, Any]]:
        """Return up to ``limit`` unexpired records, sorted by last_seen_at descending."""
        if not name:
            return []
        ts = float(now if now is not None else _now())
        with self._get_lock(name):
            entries = self._load_unlocked(name)
            alive = [dict(e) for e in entries if float(e.get("expire_at", 0)) > ts]
        alive.sort(key=lambda e: float(e.get("last_seen_at", 0)), reverse=True)
        if limit and limit > 0:
            alive = alive[:limit]
        return alive

    def purge_expired(self, name: str, *, now: Optional[float] = None) -> int:
        """Lazy cleanup: delete expired entries and persist; returns the number deleted."""
        if not name:
            return 0
        ts = float(now if now is not None else _now())
        with self._get_lock(name):
            entries = self._load_unlocked(name)
            before = len(entries)
            kept = [e for e in entries if float(e.get("expire_at", 0)) > ts]
            removed = before - len(kept)
            if removed:
                self._cache[name] = kept
                self._save_unlocked(name)
            return removed

    def get_active_terms(
        self,
        name: str,
        *,
        now: Optional[float] = None,
    ) -> List[str]:
        """Active terms only, newest-first — the shape output-side filters want."""
        return [e["term"] for e in self.get_active(name, now=now) if e.get("term")]

    def render_prompt_block(
        self,
        name: str,
        lang: str,
        *,
        now: Optional[float] = None,
    ) -> str:
        """Render active terms into a system-prompt fragment. Returns "" when empty."""
        terms = self.get_active_terms(name, now=now)
        if not terms:
            return ""
        return render_directives_block(terms, lang)

    def clear(self, name: str) -> None:
        """Entry point for tests / manual user clearing."""
        if not name:
            return
        with self._get_lock(name):
            self._cache[name] = []
            self._save_unlocked(name)
        # Clearing all injectable content must clear pending flags too, avoiding empty
        # next-turn injections that waste take semantics.
        with self._pending_guard:
            self._pending_injection.discard(name)


# -- Process singleton and registration --
_GLOBAL_MANAGER: Optional[UserDirectivesManager] = None
_GLOBAL_MANAGER_LOCK = threading.Lock()


def get_user_directives_manager() -> UserDirectivesManager:
    global _GLOBAL_MANAGER
    if _GLOBAL_MANAGER is None:
        with _GLOBAL_MANAGER_LOCK:
            if _GLOBAL_MANAGER is None:
                _GLOBAL_MANAGER = UserDirectivesManager()
    return _GLOBAL_MANAGER


def _on_user_utterance(bucket: str, event: Dict[str, Any]) -> None:
    """user_utterance sink: extract and persist. Errors are swallowed (main_logic
    already does a per-sink try/except inside dispatch; this is one more layer
    of defense).

    Dedup rule: a single ``dispatch_user_utterance`` dispatch fans out to both
    the ``"default"`` bucket and the character-name bucket (see the
    ``dict.fromkeys(("default", self.lanlan_name))`` loop in
    ``main_logic/core.py``). Rules:
      - event["lanlan"] non-empty and not "default" → a real character; the
        "default" bucket counts as the duplicate, skip; only store when
        bucket == event["lanlan"]
      - event["lanlan"] empty / "default" → the dispatch only sent the
        "default" copy (character unconfigured / character literally named
        "default"); bucket=="default" goes through normal processing, so the
        whole message isn't missed (codex P1)
    """
    if not isinstance(event, dict):
        return
    canonical = event.get("lanlan")
    if not isinstance(canonical, str):
        canonical = ""
    if canonical and canonical != "default":
        # Real characters skip duplicate default dispatch and process only character buckets.
        if bucket != canonical:
            return
        record_key = canonical
    else:
        # Missing characters or literal default receive only default dispatch,
        # which must be handled.
        if not bucket:
            return
        record_key = bucket  # Empty lanlan_name can persist only to the default bucket.
    text = ""
    raw = event.get("content")
    if isinstance(raw, str):
        text = raw
    elif isinstance(raw, list):
        # Multimodal content lists concatenate text parts.
        parts: List[str] = []
        for p in raw:
            if isinstance(p, dict):
                t = p.get("text")
                if isinstance(t, str):
                    parts.append(t)
            elif isinstance(p, str):
                parts.append(p)
        text = " ".join(parts)
    if not text or not text.strip():
        return
    try:
        get_user_directives_manager().record_from_text(record_key, text)
    except Exception as exc:  # pragma: no cover - defensive
        logger.debug("[UserDirectives] sink failed: %s", exc)


# Sinks cannot self-register here: memory lies below main_logic under
# check_module_layering, and upward imports of main_logic.agent_event_bus
# trigger LAYER_CYCLE. Export _on_user_utterance only; actual wiring belongs
# in app/runtime_bindings.py, where L6 app may access both L4
# main_logic and L3 memory.
#
# Tests/ad-hoc scripts importing this module therefore do not start sinks automatically;
# manually drive _on_user_utterance(bucket,event) to verify extraction/persistence.
# ``tests/unit/test_user_directives.py::test_user_utterance_sink_records``）；
# Integration registers through app.__init__ -> install_runtime_bindings.
