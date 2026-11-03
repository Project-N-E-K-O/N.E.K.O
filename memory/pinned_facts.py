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
PinnedFactStore — per-character resident section of important facts.

Motivation
--------
The ordinary pipeline only lets an extracted fact reach the prompt after it
has been synthesized into a reflection and that reflection has collected
enough evidence to be promoted. ``importance: 10`` merely starts the
reflection with a head start, so "remember that my exam is on Wednesday" can
take days to surface (at least two weeks with powerful memory off) and may be
folded into a vague one-line summary on the way. Facts the extraction model
flags with ``pin`` skip that pipeline and stay in a small resident section of
the private-chat long-term memory until their lifetime runs out. Expiry only
takes an entry out of the section; the original fact keeps going through the
ordinary pipeline.

Design notes
--------
- **Input**: ``FactStore`` hands every private-chat Stage-1 result to
  ``aadmit_extracted`` (see ``FactStore.attach_pinned_fact_store``). Entries
  carry two optional fields: ``pin`` (bool) and ``pin_ttl_days`` (one of
  ``PINNED_FACT_TTL_TIERS_DAYS``). Output from before these fields existed
  simply produces no candidates.
- **Scope**: private chat only. Scoped (group / member) extraction never calls
  in, and only ``/new_dialog`` renders the section, so no scoped prompt can
  show private content through it. ``ai_disclosure`` facts are never pinned.
- **Admission**: candidates contradicting the character card are dropped
  (``acontradicts_character_card`` on the persona manager). An empty section
  takes the rest as-is; otherwise one resolve-model call sees the whole
  section and picks add / replace / renew / remove per candidate. Target ids
  are checked against the snapshot that was sent, and again against the file
  when the decisions are applied, since another admission may have changed it
  in between. A candidate without a usable model answer is added, and an add
  whose text is already live renews that entry instead.
- **Storage**: ``memory/{name}/pinned_facts.json`` (cloudsave-managed, next to
  facts.json). Live entries are capped at ``PINNED_FACT_MAX_ACTIVE``;
  replaced / removed / expired / evicted ones move to a bounded ``history``.
- **No in-process cache**: every call reads the file. It is small and read at
  session start and after extraction, and without a cache a rename, delete or
  cloud restore can never leave a stale copy that the next write puts back.
- **Concurrency**: a per-character ``threading.Lock`` around each
  read-modify-write. The locks are module-level so a memory-runtime reload,
  which builds a new store, still serializes against writes in flight on the
  old one. No lock is held across the model call.
- **Logging**: entry texts never go to the logger; only counts and ids do.
"""
from __future__ import annotations

import asyncio
import json
import math
import os
import secrets
import threading
import time
from contextlib import contextmanager
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from config import (
    MEMORY_LLM_HARD_TIMEOUT_SECONDS,
    PINNED_FACT_DEFAULT_TTL_DAYS,
    PINNED_FACT_HISTORY_MAX,
    PINNED_FACT_MAX_ACTIVE,
    PINNED_FACT_MAX_CANDIDATES_PER_ROUND,
    PINNED_FACT_RENDER_MAX_TOKENS,
    PINNED_FACT_RESOLVE_ATTEMPTS,
    PINNED_FACT_RESOLVE_MODEL_TIER,
    PINNED_FACT_TEXT_MAX_TOKENS,
    PINNED_FACT_TTL_TIERS_DAYS,
)
from config.prompts.prompts_memory import (
    get_pinned_fact_resolve_prompt,
    get_pinned_facts_section_header,
    render_pinned_fact_line,
)
from utils.config_manager import get_config_manager
from utils.file_utils import atomic_write_json, robust_json_loads
from utils.logger_config import get_module_logger
from utils.tokenize import count_tokens, take_lines_within_token_budget, truncate_to_tokens

logger = get_module_logger(__name__, "Memory")


PINNED_FACTS_FILENAME = "pinned_facts.json"

# Disk schema version; _normalize_entry reads anything older field by field.
_SCHEMA_VERSION = 1

_DAY_SECONDS = 86400.0

_ENTITIES = frozenset({"master", "neko", "relationship"})
_SOURCES = frozenset({"user_observation", "ai_disclosure"})

ACTION_ADD = "add"
ACTION_REPLACE = "replace"
ACTION_RENEW = "renew"
ACTION_REMOVE = "remove"
_ACTIONS = frozenset({ACTION_ADD, ACTION_REPLACE, ACTION_RENEW, ACTION_REMOVE})
_TARGETED_ACTIONS = frozenset({ACTION_REPLACE, ACTION_RENEW, ACTION_REMOVE})

_FILE_LOCKS: Dict[str, threading.Lock] = {}
_FILE_LOCKS_GUARD = threading.Lock()


def _now() -> float:
    return time.time()


def _file_lock(name: str) -> threading.Lock:
    with _FILE_LOCKS_GUARD:
        lock = _FILE_LOCKS.get(name)
        if lock is None:
            lock = _FILE_LOCKS[name] = threading.Lock()
        return lock


def _as_float(raw: Any) -> Optional[float]:
    if isinstance(raw, bool):
        return None
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return None
    return value if math.isfinite(value) and value > 0 else None


def _as_count(raw: Any) -> int:
    if isinstance(raw, bool):
        return 0
    try:
        return max(0, int(raw or 0))
    except (TypeError, ValueError):
        return 0


def _format_date(ts: float) -> str:
    return datetime.fromtimestamp(ts).strftime("%Y-%m-%d")


def _format_moment(ts: float) -> str:
    # Down to the minute: two accounts of one matter given on the same day
    # still have to be told apart by the resolve model.
    return datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M")


def snap_ttl_days(raw: Any) -> Optional[int]:
    """Map a model-emitted lifetime to an allowed tier; None when unusable.

    Any positive number rounds up to the smallest tier that covers it (a dated
    event must stay pinned until it is over) and anything beyond the longest
    tier is clamped to it. bool is rejected even though it subclasses int.
    """
    if isinstance(raw, str):
        try:
            raw = float(raw.strip())
        except ValueError:
            return None
    if isinstance(raw, bool) or not isinstance(raw, (int, float)):
        return None
    value = float(raw)
    if not math.isfinite(value) or value <= 0:
        return None
    for tier in PINNED_FACT_TTL_TIERS_DAYS:
        if value <= tier:
            return int(tier)
    return int(PINNED_FACT_TTL_TIERS_DAYS[-1])


def _pin_requested(raw: Any) -> bool:
    if isinstance(raw, bool):
        return raw
    if isinstance(raw, str):
        return raw.strip().lower() in ("true", "yes", "1")
    if isinstance(raw, int):
        return raw == 1
    return False


def _normalize_text(raw: Any) -> Optional[str]:
    """One-line entry text within the per-entry token cap, or None when blank."""
    if not isinstance(raw, str):
        return None
    text = " ".join(raw.split())
    if not text:
        return None
    return truncate_to_tokens(text, PINNED_FACT_TEXT_MAX_TOKENS) or None


def _normalize_entity(raw: Any) -> str:
    return raw if isinstance(raw, str) and raw in _ENTITIES else "master"


def _text_key(text: str) -> str:
    return text.casefold()


def pin_candidates_from_extracted(
    extracted: Any,
    *,
    default_source: str = "user_observation",
) -> List[Dict[str, Any]]:
    """The entries of a Stage-1 result that the model flagged with ``pin``.

    ``default_source`` mirrors ``FactStore._apersist_new_facts``: an explicit,
    known ``source`` wins, anything else falls back to it. ``ai_disclosure``
    facts are never candidates. A missing or unusable ``pin_ttl_days`` takes
    ``PINNED_FACT_DEFAULT_TTL_DAYS``. Identical texts collapse to the first,
    and at most ``PINNED_FACT_MAX_CANDIDATES_PER_ROUND`` are returned.
    """
    if not isinstance(extracted, list):
        return []
    if default_source not in _SOURCES:
        default_source = "user_observation"
    candidates: List[Dict[str, Any]] = []
    seen: set[str] = set()
    for raw in extracted:
        if not isinstance(raw, dict) or not _pin_requested(raw.get("pin")):
            continue
        source = raw.get("source")
        if source not in _SOURCES:
            source = default_source
        if source == "ai_disclosure":
            continue
        text = _normalize_text(raw.get("text"))
        if text is None or _text_key(text) in seen:
            continue
        seen.add(_text_key(text))
        candidates.append({
            "text": text,
            "entity": _normalize_entity(raw.get("entity")),
            "ttl_days": snap_ttl_days(raw.get("pin_ttl_days")) or PINNED_FACT_DEFAULT_TTL_DAYS,
        })
        if len(candidates) >= PINNED_FACT_MAX_CANDIDATES_PER_ROUND:
            break
    return candidates


def _normalize_entry(raw: Any) -> Optional[Dict[str, Any]]:
    """Normalize one stored entry; a record missing its id or text is dropped.

    One malformed record must not take the whole file down with it, hence the
    blanket except.
    """
    if not isinstance(raw, dict):
        return None
    try:
        entry_id = raw.get("id")
        if not isinstance(entry_id, str) or not entry_id.strip():
            return None
        text = _normalize_text(raw.get("text"))
        if text is None:
            return None
        ttl_days = snap_ttl_days(raw.get("ttl_days")) or PINNED_FACT_DEFAULT_TTL_DAYS
        created_at = _as_float(raw.get("created_at")) or _now()
        text_at = _as_float(raw.get("text_at")) or created_at
        updated_at = _as_float(raw.get("updated_at")) or text_at
        expire_at = _as_float(raw.get("expire_at")) or (text_at + ttl_days * _DAY_SECONDS)
        return {
            "id": entry_id.strip(),
            "text": text,
            "entity": _normalize_entity(raw.get("entity")),
            "ttl_days": ttl_days,
            "created_at": created_at,
            "text_at": text_at,
            "updated_at": updated_at,
            "expire_at": expire_at,
            "renew_count": _as_count(raw.get("renew_count")),
            "rev": _as_count(raw.get("rev")),
        }
    except Exception:
        return None


def _coerce_candidate_index(raw: Any, count: int) -> Optional[int]:
    """0-based index for a model-emitted 1-based candidate number, or None."""
    if isinstance(raw, bool):
        return None
    if isinstance(raw, str):
        try:
            raw = int(raw.strip())
        except ValueError:
            return None
    if isinstance(raw, float) and raw.is_integer():
        raw = int(raw)
    if not isinstance(raw, int) or not (1 <= raw <= count):
        return None
    return raw - 1


def parse_resolve_decisions(
    results: Any,
    candidate_count: int,
    valid_ids: set[str],
) -> Dict[int, Tuple[str, Optional[str]]]:
    """Validated ``{candidate index: (action, target id)}`` from the model answer.

    The first usable decision per candidate wins. A targeted action must name
    an id from ``valid_ids`` (the section as sent in the prompt). An unusable
    replace / renew is left out, so the caller adds the candidate; an unusable
    remove maps to ``(None, None)``, i.e. drop the candidate: it reports that
    something stopped being true, which is not worth an entry of its own.
    """
    decisions: Dict[int, Tuple[str, Optional[str]]] = {}
    if isinstance(results, dict):
        results = [results]
    if not isinstance(results, list):
        return decisions
    for item in results:
        if not isinstance(item, dict):
            continue
        index = _coerce_candidate_index(item.get("candidate"), candidate_count)
        if index is None or index in decisions:
            continue
        action = item.get("action")
        action = action.strip().lower() if isinstance(action, str) else None
        if action not in _ACTIONS:
            continue
        if action == ACTION_ADD:
            decisions[index] = (ACTION_ADD, None)
            continue
        target = item.get("target")
        target = target.strip() if isinstance(target, str) else ""
        if target in valid_ids:
            decisions[index] = (action, target)
        elif action == ACTION_REMOVE:
            decisions[index] = (None, None)
    return decisions


class PinnedFactStore:
    """Per-character pinned-fact section (thread-safe, no in-process cache).

    Usage:
        store = PinnedFactStore(persona_manager=persona_manager)
        await store.aadmit_extracted(lanlan_name, stage1_result)
        block = await store.arender_prompt_block(lanlan_name, lang)
    """

    def __init__(self, *, persona_manager=None) -> None:
        self._config_manager = get_config_manager()
        # Supplies the character-card contradiction check; None skips it.
        self._persona_manager = persona_manager

    # ── path ──────────────────────────────────────────────────

    def _file_path(self, name: str) -> str:
        """Path for READING; never creates the character directory."""
        return os.path.join(str(self._config_manager.memory_dir), name, PINNED_FACTS_FILENAME)

    def _write_file_path(self, name: str) -> Optional[str]:
        """Save target, or None while a rename / delete fences this name."""
        from memory import ensure_character_dir
        from utils.character_memory import is_character_write_fenced

        if is_character_write_fenced(name):
            return None
        return os.path.join(
            ensure_character_dir(self._config_manager.memory_dir, name),
            PINNED_FACTS_FILENAME,
        )

    # ── load / save (caller holds the file lock) ──────────────

    def _read_unlocked(self, name: str) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
        path = self._file_path(name)
        if not os.path.exists(path):
            return [], []
        try:
            with open(path, encoding="utf-8") as f:
                raw = json.load(f)
        except Exception as exc:  # A corrupt file reads as empty, like user_directives.
            logger.warning(
                "[PinnedFacts] load failed for %s, treating as empty: %s",
                name, type(exc).__name__,
            )
            return [], []
        if not isinstance(raw, dict):
            return [], []
        facts: List[Dict[str, Any]] = []
        seen_ids: set[str] = set()
        for item in raw.get("facts") or []:
            entry = _normalize_entry(item)
            if entry is not None and entry["id"] not in seen_ids:
                seen_ids.add(entry["id"])
                facts.append(entry)
        history = [h for h in (raw.get("history") or []) if isinstance(h, dict)]
        return facts, history[-PINNED_FACT_HISTORY_MAX:]

    @contextmanager
    def _mutating(self, name: str):
        """Cloud-apply fence plus the file lock, held across read, change and write.

        A cloud import replaces the whole memory/<name>/ tree. Fencing only the
        final write would let an admission read the old file, wait out the
        import, then write its changed copy of the old file over the imported
        one. Raises MaintenanceModeError while an import is running.
        """
        from utils.cloudsave_runtime import cloudsave_writable_transaction

        with cloudsave_writable_transaction(
            self._config_manager,
            operation="save",
            target=f"memory/{name}/{PINNED_FACTS_FILENAME}",
        ):
            with _file_lock(name):
                yield

    def _write_unlocked(
        self, name: str, facts: List[Dict[str, Any]], history: List[Dict[str, Any]],
    ) -> bool:
        """Write the file; the caller is inside ``_mutating``."""
        payload = {
            "version": _SCHEMA_VERSION,
            "facts": facts,
            "history": history[-PINNED_FACT_HISTORY_MAX:],
        }
        try:
            target = self._write_file_path(name)
            if target is None:
                logger.info("[PinnedFacts] %s: write fenced, update dropped", name)
                return False
            atomic_write_json(target, payload, indent=2, ensure_ascii=False)
            return True
        except Exception as exc:
            logger.warning(
                "[PinnedFacts] save failed for %s: %s", name, type(exc).__name__,
            )
            return False

    # ── read API ──────────────────────────────────────────────

    def get_active(self, name: str, *, now: Optional[float] = None) -> List[Dict[str, Any]]:
        """Unexpired entries in file order (oldest first)."""
        if not name:
            return []
        ts = float(now if now is not None else _now())
        with _file_lock(name):
            facts, _ = self._read_unlocked(name)
        return [dict(e) for e in facts if e["expire_at"] > ts]

    async def aget_active(self, name: str, *, now: Optional[float] = None) -> List[Dict[str, Any]]:
        return await asyncio.to_thread(self.get_active, name, now=now)

    def render_prompt_block(self, name: str, lang: str, *, now: Optional[float] = None) -> str:
        """The ``### `` section for the private-chat prompt; "" when nothing is live.

        Over ``PINNED_FACT_RENDER_MAX_TOKENS`` the least recently updated
        entries are left out. The kept ones render in file order, so an
        unchanged section renders byte-identically between sessions.
        """
        active = self.get_active(name, now=now)
        if not active:
            return ""
        header = "### " + get_pinned_facts_section_header(lang)
        lines = {
            entry["id"]: "- " + render_pinned_fact_line(
                entry["text"],
                since=_format_date(entry["text_at"]),
                until=_format_date(entry["expire_at"]),
                lang=lang,
            )
            for entry in active
        }
        ranked = sorted(active, key=lambda e: e["updated_at"], reverse=True)
        budget = PINNED_FACT_RENDER_MAX_TOKENS - count_tokens(header + "\n")
        kept, dropped = take_lines_within_token_budget(
            [lines[e["id"]] for e in ranked], budget,
        )
        kept_ids = {e["id"] for e in ranked[:len(kept)]}
        if dropped:
            logger.info(
                "[PinnedFacts] %s: %d entr(ies) left out of this render (token budget %d)",
                name, dropped, PINNED_FACT_RENDER_MAX_TOKENS,
            )
        return header + "\n" + "\n".join(
            lines[e["id"]] for e in active if e["id"] in kept_ids
        )

    async def arender_prompt_block(
        self, name: str, lang: str, *, now: Optional[float] = None,
    ) -> str:
        return await asyncio.to_thread(self.render_prompt_block, name, lang, now=now)

    # ── write API ─────────────────────────────────────────────

    def apply_decisions(
        self,
        name: str,
        candidates: List[Dict[str, Any]],
        decisions: List[Tuple[Optional[str], Optional[str]]],
        *,
        now: Optional[float] = None,
        expected_revs: Optional[Dict[str, int]] = None,
        retry_stale: bool = True,
        stale_out: Optional[List[int]] = None,
    ) -> Dict[str, int]:
        """Apply one decision per candidate (same order) against the current file.

        ``expected_revs`` maps entry ids to their ``rev`` in the snapshot the
        decisions were made on. With it, a targeted action whose entry another
        admission has changed or removed since was made on an outdated view,
        and so is an add while the file holds an entry the snapshot lacked.
        With ``retry_stale`` such decisions are NOT applied: their indices go
        to ``stale_out`` and the caller decides again on fresh state. Without
        it (no retry left) an outdated targeted action degrades instead of
        undoing the newer change: remove is dropped, replace / renew become
        add; an add goes ahead, since adding loses nothing. A target that is
        simply gone degrades the same way when there is no snapshot at all.
        An add whose text is already live renews that entry instead, so the
        section never holds the same text twice.

        A candidate's ``noted_at`` (when it was said; defaults to ``now``) dates
        its text and starts its lifetime; ``now`` is when the change is made.
        A candidate flagged ``outdated`` (its lifetime ended before admission)
        may only remove. Returns per-action counts.
        """
        counts = dict.fromkeys(
            ("added", "replaced", "renewed", "removed", "expired", "evicted",
             "skipped", "stale"), 0,
        )
        if not name or not candidates:
            return counts
        ts = float(now if now is not None else _now())
        with self._mutating(name):
            facts, history = self._read_unlocked(name)
            live: List[Dict[str, Any]] = []
            for entry in facts:
                if entry["expire_at"] > ts:
                    live.append(entry)
                else:
                    history.append(self._history_record(entry, "expire", ts))
                    counts["expired"] += 1
            # Entries the decisions never saw: another admission added them
            # after the snapshot (two first admissions into an empty section,
            # say). An add may contradict one of them, so a checked round
            # decides it again with them in view.
            unseen = (
                {e["id"] for e in live if e["id"] not in expected_revs}
                if expected_revs is not None else set()
            )
            touched: set[str] = set()
            for index, (candidate, (action, target_id)) in enumerate(zip(candidates, decisions)):
                by_id = {e["id"]: e for e in live}
                target = by_id.get(target_id) if target_id else None
                if action in _TARGETED_ACTIONS:
                    outdated_view = expected_revs is not None and (
                        target is None
                        or (target_id not in touched
                            and target["rev"] != expected_revs.get(target_id))
                    )
                    if outdated_view and retry_stale:
                        counts["stale"] += 1
                        if stale_out is not None:
                            stale_out.append(index)
                        continue
                    if outdated_view or target is None:
                        # Never undo a change this decision did not see.
                        action = None if action == ACTION_REMOVE else ACTION_ADD
                        target = None
                elif (action == ACTION_ADD and unseen and retry_stale
                        and not candidate.get("outdated")):
                    counts["stale"] += 1
                    if stale_out is not None:
                        stale_out.append(index)
                    continue
                if candidate.get("outdated") and action != ACTION_REMOVE:
                    # Its own lifetime is over: it may only retract an entry.
                    action = None
                if action == ACTION_ADD:
                    action, target = self._renew_instead_of_duplicate(candidate, live)
                noted = min(_as_float(candidate.get("noted_at")) or ts, ts)
                lifetime_end = noted + candidate["ttl_days"] * _DAY_SECONDS
                if (action in (ACTION_REPLACE, ACTION_REMOVE) and target is not None
                        and noted < target["text_at"]):
                    # An older account never overrides the newer one it targets
                    # (a replayed "exam cancelled" against a later "exam moved"),
                    # whatever the model decided: it can at most renew it.
                    action = ACTION_RENEW if action == ACTION_REPLACE else None
                if action == ACTION_ADD:
                    entry = self._new_entry(candidate, ts, noted, {e["id"] for e in live})
                    live.append(entry)
                    touched.add(entry["id"])
                    counts["added"] += 1
                elif action == ACTION_REPLACE:
                    history.append(self._history_record(target, "replace", ts))
                    target.update({
                        "text": candidate["text"],
                        "entity": candidate["entity"],
                        "ttl_days": candidate["ttl_days"],
                        "text_at": noted,
                        "updated_at": ts,
                        "expire_at": lifetime_end,
                        "rev": target["rev"] + 1,
                    })
                    touched.add(target["id"])
                    counts["replaced"] += 1
                elif action == ACTION_RENEW:
                    # Saying the same thing again never shortens what is left.
                    target["expire_at"] = max(target["expire_at"], lifetime_end)
                    if (noted > target["text_at"]
                            and _text_key(candidate["text"]) == _text_key(target["text"])):
                        # The same words said again later anchor to the later
                        # day ("working late tonight" on Monday, then Wednesday).
                        target["text_at"] = noted
                    target["ttl_days"] = max(target["ttl_days"], candidate["ttl_days"])
                    target["updated_at"] = ts
                    target["renew_count"] += 1
                    target["rev"] += 1
                    touched.add(target["id"])
                    counts["renewed"] += 1
                elif action == ACTION_REMOVE:
                    history.append(self._history_record(target, "remove", ts))
                    live = [e for e in live if e["id"] != target["id"]]
                    touched.discard(target["id"])
                    counts["removed"] += 1
                else:
                    counts["skipped"] += 1
            while len(live) > PINNED_FACT_MAX_ACTIVE:
                # Never evict what this round just wrote: the newest entry may
                # well be the one closest to expiry (a 3-day tier).
                pool = [e for e in live if e["id"] not in touched] or live
                victim = min(pool, key=lambda e: e["expire_at"])
                history.append(self._history_record(victim, "evict", ts))
                live = [e for e in live if e["id"] != victim["id"]]
                counts["evicted"] += 1
            changed = any(counts[key] for key in counts if key not in ("skipped", "stale"))
            if changed:
                self._write_unlocked(name, live, history)
        return counts

    @staticmethod
    def _renew_instead_of_duplicate(
        candidate: Dict[str, Any], live: List[Dict[str, Any]],
    ) -> Tuple[str, Optional[Dict[str, Any]]]:
        key = _text_key(candidate["text"])
        for entry in live:
            if _text_key(entry["text"]) == key:
                return ACTION_RENEW, entry
        return ACTION_ADD, None

    @staticmethod
    def _new_entry(
        candidate: Dict[str, Any], ts: float, noted: float, taken: set[str],
    ) -> Dict[str, Any]:
        entry_id = "pin_" + secrets.token_hex(4)
        while entry_id in taken:
            entry_id = "pin_" + secrets.token_hex(4)
        return {
            "id": entry_id,
            "text": candidate["text"],
            "entity": candidate["entity"],
            "ttl_days": candidate["ttl_days"],
            "created_at": ts,
            "text_at": noted,
            "updated_at": ts,
            "expire_at": noted + candidate["ttl_days"] * _DAY_SECONDS,
            "renew_count": 0,
            "rev": 0,
        }

    @staticmethod
    def _history_record(entry: Dict[str, Any], action: str, ts: float) -> Dict[str, Any]:
        return {
            "id": entry["id"],
            "text": entry["text"],
            "entity": entry["entity"],
            "action": action,
            "at": ts,
        }

    def clear(self, name: str) -> None:
        """Entry point for tests / manual clearing; keeps nothing, history included."""
        if not name:
            return
        with self._mutating(name):
            self._write_unlocked(name, [], [])

    # ── admission ─────────────────────────────────────────────

    async def aadmit_extracted(
        self,
        name: str,
        extracted: Any,
        *,
        default_source: str = "user_observation",
        noted_at: Optional[float] = None,
        now: Optional[float] = None,
    ) -> Dict[str, int]:
        """Admit the pinned candidates of one private-chat Stage-1 result.

        ``noted_at`` is when the extracted conversation took place (its latest
        message). Extraction can run well after that (a retried window, a turn
        replayed after a restart), and entry texts are often relative ("my
        exam is tomorrow"), so the entry is dated and its lifetime counted from
        the conversation, not from the admission. A candidate whose lifetime
        already ran out by then may still remove an entry it shows to be no
        longer true ("the exam was cancelled", replayed days later) but never
        adds, replaces or renews anything.

        Decisions are made on a snapshot. When another admission changes an
        entry a decision targets, or adds one an add was not decided against,
        before it is applied, the affected candidates are decided again on
        fresh state, up to PINNED_FACT_RESOLVE_ATTEMPTS rounds. The last round
        still checks, and degrades an outdated decision instead of applying it
        (see apply_decisions), so a slow admission never undoes a newer one.
        """
        if not name or not extracted:
            return {}
        # Off the loop: candidate texts are token-truncated.
        candidates = await asyncio.to_thread(
            pin_candidates_from_extracted, extracted, default_source=default_source,
        )
        if not candidates:
            return {}
        ts = float(now if now is not None else _now())
        noted = min(_as_float(noted_at) or ts, ts)
        candidates = [
            dict(c, noted_at=noted, outdated=noted + c["ttl_days"] * _DAY_SECONDS <= ts)
            for c in candidates
        ]
        outdated = sum(1 for c in candidates if c["outdated"])
        candidates, card_rejected = await self._afilter_card_conflicts(name, candidates)
        totals: Dict[str, int] = {"card_rejected": card_rejected, "outdated": outdated}
        pending = list(range(len(candidates)))
        rounds = 0
        resolved_by_model = False
        while pending and rounds < PINNED_FACT_RESOLVE_ATTEMPTS:
            rounds += 1
            batch = [candidates[i] for i in pending]
            active = await self.aget_active(name, now=ts)
            if not active:
                decisions: List[Tuple[Optional[str], Optional[str]]] = [
                    (ACTION_ADD, None) for _ in batch
                ]
            else:
                results = await self._aresolve_with_model(name, active, batch, now=ts)
                parsed = (
                    parse_resolve_decisions(results, len(batch), {e["id"] for e in active})
                    if results is not None else {}
                )
                resolved_by_model = resolved_by_model or bool(parsed)
                # No usable answer for a candidate: add it. apply_decisions turns
                # an add of a text that is already live into a renewal of that entry.
                decisions = [parsed.get(i, (ACTION_ADD, None)) for i in range(len(batch))]
            stale: List[int] = []
            counts = await asyncio.to_thread(
                self.apply_decisions, name, batch, decisions,
                now=ts,
                expected_revs={e["id"]: e["rev"] for e in active},
                retry_stale=rounds < PINNED_FACT_RESOLVE_ATTEMPTS,
                stale_out=stale,
            )
            for key, value in counts.items():
                if key != "stale":
                    totals[key] = totals.get(key, 0) + value
            pending = [pending[i] for i in stale]
        logger.info(
            "[PinnedFacts] %s: candidates=%d rounds=%d model=%s added=%d replaced=%d "
            "renewed=%d removed=%d expired=%d evicted=%d skipped=%d "
            "card_rejected=%d outdated=%d",
            name, len(candidates), rounds, resolved_by_model,
            totals.get("added", 0), totals.get("replaced", 0), totals.get("renewed", 0),
            totals.get("removed", 0), totals.get("expired", 0), totals.get("evicted", 0),
            totals.get("skipped", 0), card_rejected, outdated,
        )
        return totals

    async def _afilter_card_conflicts(
        self, name: str, candidates: List[Dict[str, Any]],
    ) -> Tuple[List[Dict[str, Any]], int]:
        checker = getattr(self._persona_manager, "acontradicts_character_card", None)
        if checker is None:
            return candidates, 0
        kept: List[Dict[str, Any]] = []
        for candidate in candidates:
            try:
                # Same-entity card entries only: master and character names are
                # stripped before the overlap test, so "the master's birthday is
                # ..." would otherwise collide with the character's own birthday.
                if await checker(name, candidate["text"], entity=candidate["entity"]):
                    continue
            except Exception as exc:
                # An unreadable persona says nothing about this candidate; keep it.
                logger.warning(
                    "[PinnedFacts] %s: character-card check failed, admitting: %s",
                    name, type(exc).__name__,
                )
            kept.append(candidate)
        return kept, len(candidates) - len(kept)

    async def _aresolve_with_model(
        self,
        name: str,
        active: List[Dict[str, Any]],
        candidates: List[Dict[str, Any]],
        *,
        now: float,
    ) -> Any:
        """One resolve-model call; the parsed JSON, or None on any failure."""
        from memory.thinking_llm import ainvoke_thinking
        from utils.token_tracker import set_call_type

        try:
            _, _, _, _, name_mapping, _, _, _, _ = (
                await self._config_manager.aget_character_data()
            )
            master_name = (name_mapping or {}).get("human") or "master"
        except Exception:
            master_name = "master"
        prompt = await asyncio.to_thread(
            self._build_resolve_prompt, name, master_name, active, candidates, now,
        )
        try:
            set_call_type("memory_pinned_facts")
            api_config = await self._config_manager.aget_model_api_config(
                PINNED_FACT_RESOLVE_MODEL_TIER,
            )
            resp, _ = await ainvoke_thinking(  # noqa: LLM_INPUT_BUDGET  # prompt built by _build_resolve_prompt, every text capped at PINNED_FACT_TEXT_MAX_TOKENS.
                api_config, prompt,
                timeout=MEMORY_LLM_HARD_TIMEOUT_SECONDS,
                call_label=f"{name} memory_pinned_facts",
            )
            raw = resp.content if isinstance(resp.content, str) else str(resp.content)
            raw = raw.strip()
            if raw.startswith("```"):
                raw = raw.replace("```json", "").replace("```", "").strip()
            return robust_json_loads(raw)
        except Exception as exc:
            logger.warning(
                "[PinnedFacts] %s: resolve model failed, adding the candidates: %s",
                name, type(exc).__name__,
            )
            return None

    @staticmethod
    def _build_resolve_prompt(
        name: str,
        master_name: str,
        active: List[Dict[str, Any]],
        candidates: List[Dict[str, Any]],
        now: float,
    ) -> str:
        """The resolve prompt (token work, so callers run it off the event loop).

        Input stays bounded: at most PINNED_FACT_MAX_ACTIVE entries plus
        PINNED_FACT_MAX_CANDIDATES_PER_ROUND candidates, each text capped at
        PINNED_FACT_TEXT_MAX_TOKENS.
        """
        from utils.language_utils import (
            detect_prompt_language_with_ascii_fallback,
            get_global_language_full,
        )

        lang = detect_prompt_language_with_ascii_fallback(
            "\n".join(c["text"] for c in candidates),
            ui_language=get_global_language_full(),
        )
        zone = "\n".join(
            f"[{entry['id']}] " + render_pinned_fact_line(
                truncate_to_tokens(entry["text"], PINNED_FACT_TEXT_MAX_TOKENS),
                since=_format_moment(entry["text_at"]),
                until=_format_date(entry["expire_at"]),
                lang=lang,
            )
            for entry in active[:PINNED_FACT_MAX_ACTIVE]
        )
        # Candidates carry their own dates too: the model must be able to tell
        # an older account of a matter from a newer one.
        listed = "\n".join(
            f"{index}. " + render_pinned_fact_line(
                truncate_to_tokens(candidate["text"], PINNED_FACT_TEXT_MAX_TOKENS),
                since=_format_moment(candidate.get("noted_at", now)),
                until=_format_date(
                    candidate.get("noted_at", now) + candidate["ttl_days"] * _DAY_SECONDS
                ),
                lang=lang,
            )
            for index, candidate in enumerate(candidates, 1)
        )
        return (
            get_pinned_fact_resolve_prompt(lang)
            .replace("{LANLAN_NAME}", name)
            .replace("{MASTER_NAME}", master_name)
            .replace("{TODAY}", _format_date(now))
            .replace("{ZONE}", zone)
            .replace("{CANDIDATES}", listed)
        )
