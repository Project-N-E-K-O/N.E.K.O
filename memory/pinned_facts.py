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
ordinary pipeline. The section also holds what the character promised to do
(``kind: "commitment"``), so a promise is not forgotten either.

Design notes
--------
- **Input**: ``FactStore`` hands every private-chat Stage-1 result to
  ``aadmit_extracted`` (see ``FactStore.attach_pinned_fact_store``). Entries
  carry optional fields: ``pin`` (bool), ``pin_ttl_days`` (one of
  ``PINNED_FACT_TTL_TIERS_DAYS``), ``pin_kind`` (``"commitment"`` for a
  promise), ``pin_condition`` (what a promise waits for) and ``pin_done`` (a
  report that a promise was kept). ``event_when.end`` dates the end of the
  matter. Output from before these fields existed simply produces no
  candidates, and stored entries without ``kind`` read as facts.
- **Scope**: private chat only. Scoped (group / member) extraction never calls
  in, and only ``/new_dialog`` renders the section, so no scoped prompt can
  show private content through it. ``ai_disclosure`` facts are pinned only as
  commitments, and a promise needs a time or a condition: one with neither
  is a pleasantry.
- **Lifetime**: the tier is a hard ceiling counted from when the entry was
  said. An entry whose matter has a known end leaves
  ``PINNED_FACT_DUE_GRACE_DAYS`` after it when that comes first. Nothing
  extends an entry past the ceiling of its latest mention.
- **Admission**: candidates contradicting the character card are dropped
  (``acontradicts_character_card`` on the persona manager). An empty section
  takes the rest as-is; otherwise one resolve-model call sees the whole
  section and picks add / replace / renew / remove per candidate, and may
  retire entries that are over (kept, called off, or past their date). Target
  ids are checked against the snapshot that was sent, and again against the
  file when the decisions are applied, since another admission may have
  changed it in between. A candidate without a usable model answer is added,
  and an add whose text is already live renews that entry instead.
- **Review**: ``areview`` runs at most daily while the user is idle (the
  memory server schedules it) when no model has looked at the section within
  ``PINNED_FACT_REVIEW_INTERVAL_HOURS``. A cheap model compares the section
  with today's date and the conversation since; it may only retire, and only
  on evidence the code can check (a verbatim quote, or a date that passed).
  Anything unsure stays.
- **Commitments**: an open one renders in its own section and the one due
  soonest is offered to proactive chat. Kept or lapsed, it leaves the section
  for the history with ``status`` ``done`` or ``expired``.
- **Storage**: ``memory/{name}/pinned_facts.json`` (cloudsave-managed, next to
  facts.json). Live entries are capped at ``PINNED_FACT_MAX_ACTIVE``;
  replaced / removed / retired / expired / evicted ones move to a bounded
  ``history``.
- **No in-process cache**: every call reads the file. It is small and read at
  session start and after extraction, and without a cache a rename, delete or
  cloud restore can never leave a stale copy that the next write puts back.
- **Concurrency**: a per-character ``threading.Lock`` around each
  read-modify-write. The locks are module-level so a memory-runtime reload,
  which builds a new store, still serializes against writes in flight on the
  old one. No lock is held across a model call; every decision is checked
  against the ``rev`` of the entries it was made on.
- **Logging**: entry texts never go to the logger; only counts and ids do.
"""
from __future__ import annotations

import asyncio
import json
import math
import os
import re
import secrets
import threading
import time
import unicodedata
from contextlib import contextmanager
from datetime import datetime
from typing import Any, Awaitable, Callable, Dict, List, Optional, Tuple

from config import (
    LLM_OUTPUT_GUARD_MAX_TOKENS,
    MEMORY_LLM_HARD_TIMEOUT_SECONDS,
    PINNED_COMMITMENT_DUE_SOON_DAYS,
    PINNED_COMMITMENT_SURFACE_COOLDOWN_HOURS,
    PINNED_FACT_DEFAULT_TTL_DAYS,
    PINNED_FACT_DUE_GRACE_DAYS,
    PINNED_FACT_HISTORY_MAX,
    PINNED_FACT_MAX_ACTIVE,
    PINNED_FACT_MAX_CANDIDATES_PER_ROUND,
    PINNED_FACT_RENDER_MAX_TOKENS,
    PINNED_FACT_RESOLVE_ATTEMPTS,
    PINNED_FACT_RESOLVE_MODEL_TIER,
    PINNED_FACT_RETIRE_REASON_MAX_TOKENS,
    PINNED_FACT_REVIEW_CONVERSATION_MAX_TOKENS,
    PINNED_FACT_REVIEW_INTERVAL_HOURS,
    PINNED_FACT_REVIEW_LOOKBACK_DAYS,
    PINNED_FACT_REVIEW_MESSAGE_MAX_TOKENS,
    PINNED_FACT_REVIEW_MODEL_TIER,
    PINNED_FACT_TEXT_MAX_TOKENS,
    PINNED_FACT_TTL_TIERS_DAYS,
)
from config.prompts.prompts_memory import (
    get_pinned_commitments_section_header,
    get_pinned_fact_resolve_prompt,
    get_pinned_fact_review_prompt,
    get_pinned_facts_section_header,
    render_pinned_commitment_done_line,
    render_pinned_commitment_line,
    render_pinned_commitment_undated_line,
    render_pinned_fact_line,
    render_pinned_fact_removed_line,
)
from memory.temporal import compute_event_timestamps, normalize_event_when
from utils.config_manager import get_config_manager
from utils.file_utils import atomic_write_json, robust_json_loads
from utils.logger_config import get_module_logger
from utils.tokenize import count_tokens, take_lines_within_token_budget, truncate_to_tokens

logger = get_module_logger(__name__, "Memory")


PINNED_FACTS_FILENAME = "pinned_facts.json"

# Every entry id starts with this (reflection ids start with "ref_").
PINNED_FACT_ID_PREFIX = "pin_"

# Disk schema version; _normalize_entry reads anything older field by field.
_SCHEMA_VERSION = 1

_DAY_SECONDS = 86400.0

_ENTITIES = frozenset({"master", "neko", "relationship"})
_SOURCES = frozenset({"user_observation", "ai_disclosure"})

KIND_FACT = "fact"
KIND_COMMITMENT = "commitment"
_KINDS = frozenset({KIND_FACT, KIND_COMMITMENT})

STATUS_OPEN = "open"
STATUS_DONE = "done"
STATUS_EXPIRED = "expired"
_STATUSES = frozenset({STATUS_OPEN, STATUS_DONE, STATUS_EXPIRED})

ACTION_ADD = "add"
ACTION_REPLACE = "replace"
ACTION_RENEW = "renew"
ACTION_REMOVE = "remove"
ACTION_RETIRE = "retire"
_ACTIONS = frozenset({ACTION_ADD, ACTION_REPLACE, ACTION_RENEW, ACTION_REMOVE})
_TARGETED_ACTIONS = frozenset({ACTION_REPLACE, ACTION_RENEW, ACTION_REMOVE})
# History actions whose record withdraws the text it carries (_live_withdrawals).
_WITHDRAWING_ACTIONS = frozenset({ACTION_REMOVE, ACTION_RETIRE})

OUTCOME_DONE = "done"
OUTCOME_INVALID = "invalid"
OUTCOME_PAST = "past"
_OUTCOMES = frozenset({OUTCOME_DONE, OUTCOME_INVALID, OUTCOME_PAST})

# Wrapping a model may put around a quote it copies from the conversation.
_QUOTE_WRAPPING = "\"'`“”‘’「」『』«»《》 "
# A shorter quote ("ok", "好了") matches almost any conversation.
_QUOTE_MIN_TOKENS = 3

_PLACEHOLDER_RE = re.compile(r"\{(LANLAN_NAME|MASTER_NAME|TODAY|ZONE|CANDIDATES|CONVERSATION)\}")

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


def _fill_template(template: str, values: Dict[str, str]) -> str:
    """Substitute the prompt placeholders in one pass.

    One pass, so a placeholder spelled out inside an entry text or a message
    is never expanded again by a later substitution.
    """
    return _PLACEHOLDER_RE.sub(lambda m: values.get(m.group(1), m.group(0)), template)


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


def _expiry(noted: float, ttl_days: int, due_at: Optional[float]) -> float:
    """When an entry said at ``noted`` leaves the section.

    The tier is the ceiling; a known end of the matter brings it forward to
    one grace period after that end.
    """
    ceiling = noted + ttl_days * _DAY_SECONDS
    if due_at is None:
        return ceiling
    return min(ceiling, due_at + PINNED_FACT_DUE_GRACE_DAYS * _DAY_SECONDS)


def due_date(entry: Dict[str, Any]) -> str:
    """The date an entry's matter is due, as the section renders it; needs ``due_at``."""
    return _format_date(entry["due_at"])


def _is_live(entry: Dict[str, Any], ts: float) -> bool:
    """Unexpired and, for a commitment, still open."""
    return entry["expire_at"] > ts and entry.get("status", STATUS_OPEN) == STATUS_OPEN


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


def _normalize_kind(raw: Any) -> str:
    value = raw.strip().lower() if isinstance(raw, str) else None
    return value if value in _KINDS else KIND_FACT


def _normalize_reason(raw: Any) -> str:
    if not isinstance(raw, str):
        return ""
    return truncate_to_tokens(" ".join(raw.split()), PINNED_FACT_RETIRE_REASON_MAX_TOKENS)


def _text_key(text: str) -> str:
    return text.casefold()


def _quote_key(text: Any) -> str:
    if not isinstance(text, str):
        return ""
    return " ".join(text.strip(_QUOTE_WRAPPING).split()).casefold()


def _is_wide(ch: str) -> bool:
    """CJK, kana, hangul and other full-width characters."""
    return unicodedata.east_asian_width(ch) in ("W", "F")


def _spaced_letter(ch: str) -> bool:
    """A letter or digit of a script that separates its words with spaces."""
    return ch.isalnum() and not _is_wide(ch)


def _contains_quote(text_key: str, quote_key: str) -> bool:
    """Whether ``quote_key`` occurs in ``text_key``, whole words for spaced scripts.

    "ok" must not match inside "book"; a quote in a script written without
    spaces has no word edges to respect.
    """
    start = text_key.find(quote_key)
    while start >= 0:
        end = start + len(quote_key)
        cut_before = start > 0 and _spaced_letter(quote_key[0]) and _spaced_letter(text_key[start - 1])
        cut_after = end < len(text_key) and _spaced_letter(quote_key[-1]) and _spaced_letter(text_key[end])
        if not cut_before and not cut_after:
            return True
        start = text_key.find(quote_key, start + 1)
    return False


def _with_condition(text: Any, condition: Any) -> Any:
    """A promise's text carrying the condition it waits for.

    The model may give the condition only in ``pin_condition``; dropped, the
    promise would read as due right away. Appended unless the text says it.
    """
    if not isinstance(text, str) or not isinstance(condition, str):
        return text
    condition = " ".join(condition.split())
    if not condition or _text_key(condition) in _text_key(" ".join(text.split())):
        return text
    if any(_is_wide(ch) for ch in text + condition):
        return f"{text}（{condition}）"
    return f"{text} ({condition})"


def pin_candidates_from_extracted(
    extracted: Any,
    *,
    default_source: str = "user_observation",
) -> List[Dict[str, Any]]:
    """The entries of a Stage-1 result that the model flagged with ``pin``.

    ``default_source`` mirrors ``FactStore._apersist_new_facts``: an explicit,
    known ``source`` wins, anything else falls back to it. ``ai_disclosure``
    facts are candidates only as commitments. A commitment needs a time
    (``event_when``) or a condition (``pin_condition``), unless it reports a
    kept promise (``pin_done``). A missing or unusable ``pin_ttl_days`` takes
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
        kind = _normalize_kind(raw.get("pin_kind"))
        source = raw.get("source")
        if source not in _SOURCES:
            source = default_source
        if source == "ai_disclosure" and kind != KIND_COMMITMENT:
            continue
        when = normalize_event_when(raw.get("event_when"))
        fulfilled = kind == KIND_COMMITMENT and _pin_requested(raw.get("pin_done"))
        condition = raw.get("pin_condition")
        if (kind == KIND_COMMITMENT and not fulfilled and when is None
                and not (isinstance(condition, str) and condition.strip())):
            # Neither a time nor a condition: a pleasantry ("I'll always be
            # there for you"), not something to follow through on.
            continue
        text = _normalize_text(
            _with_condition(raw.get("text"), condition)
            if kind == KIND_COMMITMENT else raw.get("text")
        )
        if text is None or _text_key(text) in seen:
            continue
        seen.add(_text_key(text))
        candidate = {
            "text": text,
            "entity": _normalize_entity(raw.get("entity")),
            "ttl_days": snap_ttl_days(raw.get("pin_ttl_days")) or PINNED_FACT_DEFAULT_TTL_DAYS,
            "kind": kind,
        }
        if when is not None:
            candidate["when"] = when
        if fulfilled:
            candidate["fulfilled"] = True
        candidates.append(candidate)
        if len(candidates) >= PINNED_FACT_MAX_CANDIDATES_PER_ROUND:
            break
    return candidates


def _due_at(candidate: Dict[str, Any], noted: float) -> Optional[float]:
    """When the matter of a candidate ends, from its ``event_when`` said at ``noted``.

    A fact counts only its end: a start alone ("caught a cold") says nothing
    about when it is over. A promise is due when it is to be done, so its
    start serves when there is no end ("I'll remind you tomorrow").
    """
    when = candidate.get("when")
    if not when:
        return None
    try:
        start_iso, end_iso = compute_event_timestamps(
            when, datetime.fromtimestamp(noted).isoformat(),
            fallback_start=False, fallback_end=False,
        )
        iso = end_iso or (start_iso if candidate.get("kind") == KIND_COMMITMENT else None)
        return datetime.fromisoformat(iso).timestamp() if iso else None
    except (OverflowError, OSError, TypeError, ValueError):
        # An offset no calendar can hold says nothing usable about the end.
        return None


def _retraction_only(candidate: Dict[str, Any]) -> bool:
    """Whether a candidate may only retract an entry, never pin itself.

    Its own lifetime ran out before admission (``outdated``), an earlier round
    read it as a retraction, or it reports a kept promise (``fulfilled``).
    """
    return bool(
        candidate.get("outdated") or candidate.get("retraction") or candidate.get("fulfilled")
    )


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
        kind = _normalize_kind(raw.get("kind"))
        ttl_days = snap_ttl_days(raw.get("ttl_days")) or PINNED_FACT_DEFAULT_TTL_DAYS
        created_at = _as_float(raw.get("created_at")) or _now()
        text_at = _as_float(raw.get("text_at")) or created_at
        updated_at = _as_float(raw.get("updated_at")) or text_at
        due_at = _as_float(raw.get("due_at"))
        expire_at = _as_float(raw.get("expire_at")) or _expiry(text_at, ttl_days, due_at)
        entry = {
            "id": entry_id.strip(),
            "text": text,
            "entity": _normalize_entity(raw.get("entity")),
            "kind": kind,
            "ttl_days": ttl_days,
            "created_at": created_at,
            "text_at": text_at,
            "updated_at": updated_at,
            "expire_at": expire_at,
            "due_at": due_at,
            "renew_count": _as_count(raw.get("renew_count")),
            "rev": _as_count(raw.get("rev")),
        }
        if kind == KIND_COMMITMENT:
            status = raw.get("status")
            entry["status"] = (
                status if isinstance(status, str) and status in _STATUSES else STATUS_OPEN
            )
            entry["surfaced_at"] = _as_float(raw.get("surfaced_at"))
        return entry
    except Exception:
        return None


def _set_kind_fields(entry: Dict[str, Any]) -> None:
    """Give a (re)written entry the fields its kind carries: an open, unoffered promise."""
    if entry["kind"] == KIND_COMMITMENT:
        entry["status"] = STATUS_OPEN
        entry["surfaced_at"] = None
    else:
        entry.pop("status", None)
        entry.pop("surfaced_at", None)


def _live_withdrawals(
    history: List[Dict[str, Any]], now: float, *, actions: frozenset = _WITHDRAWING_ACTIONS,
) -> List[Dict[str, Any]]:
    """History records of ``actions`` whose retracted text has not expired yet.

    Only records that carry the conversation time of the retracting account
    (``noted_at``) count. Each comes back as ``{id, text, text_at,
    withdrawn_at, action}``. Records are kept per (action, id, text), so an
    entry replaced twice still remembers both wordings it moved away from;
    a later record for the same key replaces an earlier one. A retired entry
    counts like a removed one: words said before it was over must not bring
    it back.
    """
    by_key: Dict[tuple, Dict[str, Any]] = {}
    for record in history:
        if not isinstance(record, dict) or record.get("action") not in actions:
            continue
        withdrawn_at = _as_float(record.get("noted_at"))
        expire_at = _as_float(record.get("expire_at"))
        entry_id, text = record.get("id"), record.get("text")
        if (withdrawn_at is None or expire_at is None or expire_at <= now
                or not isinstance(entry_id, str) or not isinstance(text, str)):
            continue
        by_key[(record["action"], entry_id, _text_key(text))] = {
            "id": entry_id,
            "text": text,
            "text_at": _as_float(record.get("text_at")) or withdrawn_at,
            "withdrawn_at": withdrawn_at,
            "action": record["action"],
        }
    return list(by_key.values())


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


def _lowered(raw: Any) -> Optional[str]:
    return raw.strip().lower() if isinstance(raw, str) else None


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
    Retire items are not candidate decisions (see parse_retirements).
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
        action = _lowered(item.get("action"))
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


def parse_retirements(
    results: Any,
    valid_ids: set[str],
    candidate_count: int = 0,
) -> List[Dict[str, Any]]:
    """Retire items from a model answer, at most one per entry, shape-checked only.

    Each comes back as ``{target, outcome, reason, candidate, evidence}``:
    ``candidate`` is the 0-based index of a candidate cited as evidence and
    ``evidence`` a quoted line, each None when absent. ``target`` must be in
    ``valid_ids`` (the live entries the model was shown). Whether a verdict
    may be applied is a separate question (retirement_allowed).
    """
    items: List[Dict[str, Any]] = []
    if isinstance(results, dict):
        results = [results]
    if not isinstance(results, list):
        return items
    seen: set[str] = set()
    for item in results:
        if not isinstance(item, dict) or _lowered(item.get("action")) != ACTION_RETIRE:
            continue
        target = item.get("target")
        target = target.strip() if isinstance(target, str) else ""
        outcome = _lowered(item.get("outcome"))
        if target not in valid_ids or target in seen or outcome not in _OUTCOMES:
            continue
        seen.add(target)
        evidence = item.get("evidence")
        items.append({
            "target": target,
            "outcome": outcome,
            "reason": _normalize_reason(item.get("reason")),
            "candidate": _coerce_candidate_index(item.get("candidate"), candidate_count),
            "evidence": evidence.strip() if isinstance(evidence, str) and evidence.strip() else None,
        })
    return items


def retirement_allowed(
    entry: Dict[str, Any], outcome: str, *, evidenced: bool, now: float,
) -> bool:
    """Whether a model's verdict that ``entry`` is over may be applied.

    With evidence the code has checked (a candidate noted after the entry, or
    a verbatim quote of the conversation), any outcome stands. Without it,
    only ``past`` does, and only when the recorded end of the matter has
    passed, or when there is no recorded end and the entry is not of the
    longest tier: an allergy or a birthday is never over by date alone.
    """
    if outcome not in _OUTCOMES:
        return False
    if evidenced:
        return True
    if outcome != OUTCOME_PAST:
        return False
    due_at = entry.get("due_at")
    if due_at is not None:
        return due_at <= now
    return entry["ttl_days"] < PINNED_FACT_TTL_TIERS_DAYS[-1]


def _quoted_at(
    evidence: Optional[str], shown: List[Dict[str, Any]], not_before: float,
) -> Optional[float]:
    """Time of the earliest shown message after ``not_before`` that contains the quote.

    Strictly after: an entry is dated by the last message of its window, which
    may be the very promise it records, and a promise does not prove itself kept.
    """
    key = _quote_key(evidence)
    if not key or count_tokens(key) < _QUOTE_MIN_TOKENS:
        return None
    for message in shown:
        if message["at"] > not_before and _contains_quote(_quote_key(message["text"]), key):
            return message["at"]
    return None


def _parse_model_json(resp: Any) -> Any:
    raw = resp.content if isinstance(resp.content, str) else str(resp.content)
    raw = raw.strip()
    if raw.startswith("```"):
        raw = raw.replace("```json", "").replace("```", "").strip()
    return robust_json_loads(raw)


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

    def _read_unlocked(
        self, name: str,
    ) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]], Optional[float]]:
        """``(entries, history, reviewed_at)`` as stored."""
        path = self._file_path(name)
        if not os.path.exists(path):
            return [], [], None
        try:
            with open(path, encoding="utf-8") as f:
                raw = json.load(f)
        except Exception as exc:  # A corrupt file reads as empty, like user_directives.
            logger.warning(
                "[PinnedFacts] load failed for %s, treating as empty: %s",
                name, type(exc).__name__,
            )
            return [], [], None
        if not isinstance(raw, dict):
            return [], [], None
        facts: List[Dict[str, Any]] = []
        seen_ids: set[str] = set()
        for item in raw.get("facts") or []:
            entry = _normalize_entry(item)
            if entry is not None and entry["id"] not in seen_ids:
                seen_ids.add(entry["id"])
                facts.append(entry)
        history = [h for h in (raw.get("history") or []) if isinstance(h, dict)]
        return facts, history[-PINNED_FACT_HISTORY_MAX:], _as_float(raw.get("reviewed_at"))

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
        self,
        name: str,
        facts: List[Dict[str, Any]],
        history: List[Dict[str, Any]],
        reviewed_at: Optional[float] = None,
    ) -> bool:
        """Write the file; the caller is inside ``_mutating``.

        ``reviewed_at`` is when a model last looked at the whole section (an
        admission resolve or the idle-time check); it paces the check.
        """
        payload: Dict[str, Any] = {
            "version": _SCHEMA_VERSION,
            "facts": facts,
            "history": history[-PINNED_FACT_HISTORY_MAX:],
        }
        if reviewed_at is not None:
            payload["reviewed_at"] = reviewed_at
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

    def _sweep_unlocked(
        self, facts: List[Dict[str, Any]], history: List[Dict[str, Any]], ts: float,
    ) -> Tuple[List[Dict[str, Any]], int]:
        """Move what is no longer live into ``history``; ``(live entries, moved)``."""
        live: List[Dict[str, Any]] = []
        moved = 0
        for entry in facts:
            if _is_live(entry, ts):
                live.append(entry)
            else:
                history.append(self._history_record(entry, "expire", ts))
                moved += 1
        return live, moved

    # ── read API ──────────────────────────────────────────────

    def get_active(self, name: str, *, now: Optional[float] = None) -> List[Dict[str, Any]]:
        """Live entries in file order (oldest first): unexpired, and open if a commitment."""
        if not name:
            return []
        ts = float(now if now is not None else _now())
        with _file_lock(name):
            facts, _, _ = self._read_unlocked(name)
        return [dict(e) for e in facts if _is_live(e, ts)]

    async def aget_active(self, name: str, *, now: Optional[float] = None) -> List[Dict[str, Any]]:
        return await asyncio.to_thread(self.get_active, name, now=now)

    def get_snapshot(
        self, name: str, *, now: Optional[float] = None,
    ) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
        """``(live entries, withdrawn entries whose lifetime has not run out)``."""
        if not name:
            return [], []
        ts = float(now if now is not None else _now())
        with _file_lock(name):
            facts, history, _ = self._read_unlocked(name)
        active = [dict(e) for e in facts if _is_live(e, ts)]
        live_ids = {e["id"] for e in active}
        removed = [r for r in _live_withdrawals(history, ts) if r["id"] not in live_ids]
        removed.sort(key=lambda r: r["withdrawn_at"], reverse=True)
        return active, removed

    async def aget_snapshot(
        self, name: str, *, now: Optional[float] = None,
    ) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
        return await asyncio.to_thread(self.get_snapshot, name, now=now)

    @staticmethod
    def _entry_line(
        entry: Dict[str, Any], lang: str, *, since: str, text: Optional[str] = None,
    ) -> str:
        """An entry as the section and the model prompts show it, without marker or id."""
        text = entry["text"] if text is None else text
        if entry.get("kind") == KIND_COMMITMENT:
            if entry.get("due_at") is None:
                # Waiting on a condition: there is no deadline to state, and
                # the tier ceiling it expires at is bookkeeping.
                return render_pinned_commitment_undated_line(text, since=since, lang=lang)
            return render_pinned_commitment_line(
                text, since=since, until=due_date(entry), lang=lang,
            )
        return render_pinned_fact_line(
            text, since=since, until=_format_date(entry["expire_at"]), lang=lang,
        )

    def render_prompt_block(self, name: str, lang: str, *, now: Optional[float] = None) -> str:
        """The ``### `` sections for the private-chat prompt; "" when nothing is live.

        Facts come first, then the open commitments under their own header.
        Over ``PINNED_FACT_RENDER_MAX_TOKENS`` (headers included) the least
        recently updated entries are left out. The kept ones render in file
        order, so an unchanged section renders byte-identically between
        sessions.
        """
        active = self.get_active(name, now=now)
        if not active:
            return ""
        headers = {
            KIND_FACT: "### " + get_pinned_facts_section_header(lang),
            KIND_COMMITMENT: "### " + get_pinned_commitments_section_header(lang),
        }
        kinds = [kind for kind in headers if any(e["kind"] == kind for e in active)]
        lines = {
            entry["id"]: "- " + self._entry_line(
                entry, lang, since=_format_date(entry["text_at"]),
            )
            for entry in active
        }
        ranked = sorted(active, key=lambda e: e["updated_at"], reverse=True)
        budget = PINNED_FACT_RENDER_MAX_TOKENS - sum(
            count_tokens(headers[kind] + "\n") for kind in kinds
        ) - (len(kinds) - 1) * count_tokens("\n\n")
        kept, dropped = take_lines_within_token_budget(
            [lines[e["id"]] for e in ranked], budget,
        )
        kept_ids = {e["id"] for e in ranked[:len(kept)]}
        if dropped:
            logger.info(
                "[PinnedFacts] %s: %d entr(ies) left out of this render (token budget %d)",
                name, dropped, PINNED_FACT_RENDER_MAX_TOKENS,
            )
        sections = []
        for kind in kinds:
            body = [lines[e["id"]] for e in active if e["kind"] == kind and e["id"] in kept_ids]
            if body:
                sections.append(headers[kind] + "\n" + "\n".join(body))
        return "\n\n".join(sections)

    async def arender_prompt_block(
        self, name: str, lang: str, *, now: Optional[float] = None,
    ) -> str:
        return await asyncio.to_thread(self.render_prompt_block, name, lang, now=now)

    def get_due_commitment(
        self, name: str, *, now: Optional[float] = None,
    ) -> Optional[Dict[str, Any]]:
        """The open commitment due soonest, for proactive chat; None when there is none.

        Only one with a date of its own, due within
        ``PINNED_COMMITMENT_DUE_SOON_DAYS`` (or already due, while still live),
        counts, and not one offered within
        ``PINNED_COMMITMENT_SURFACE_COOLDOWN_HOURS``. A promise waiting on a
        condition has no deadline: its expiry is the tier ceiling, which says
        nothing about when it is due.
        """
        ts = float(now if now is not None else _now())
        horizon = ts + PINNED_COMMITMENT_DUE_SOON_DAYS * _DAY_SECONDS
        cooldown = PINNED_COMMITMENT_SURFACE_COOLDOWN_HOURS * 3600.0
        pool = [
            entry for entry in self.get_active(name, now=ts)
            if entry["kind"] == KIND_COMMITMENT
            and entry.get("due_at") is not None
            and entry["due_at"] <= horizon
            and (entry.get("surfaced_at") is None or ts - entry["surfaced_at"] >= cooldown)
        ]
        return min(pool, key=lambda entry: entry["due_at"]) if pool else None

    async def aget_due_commitment(
        self, name: str, *, now: Optional[float] = None,
    ) -> Optional[Dict[str, Any]]:
        return await asyncio.to_thread(self.get_due_commitment, name, now=now)

    def mark_surfaced(
        self, name: str, ids: List[Any], *, now: Optional[float] = None,
    ) -> List[str]:
        """Record that proactive chat was offered these commitments; returns the ids found.

        ``surfaced_at`` is bookkeeping, not content, so ``rev`` stays: an
        admission deciding on the entry meanwhile is not sent round again.
        """
        wanted = {i for i in ids if isinstance(i, str)}
        if not name or not wanted:
            return []
        ts = float(now if now is not None else _now())
        with self._mutating(name):
            facts, history, reviewed_at = self._read_unlocked(name)
            found = []
            for entry in facts:
                if entry["id"] in wanted and entry["kind"] == KIND_COMMITMENT and _is_live(entry, ts):
                    entry["surfaced_at"] = ts
                    found.append(entry["id"])
            if found:
                self._write_unlocked(name, facts, history, reviewed_at)
        return found

    async def amark_surfaced(
        self, name: str, ids: List[Any], *, now: Optional[float] = None,
    ) -> List[str]:
        return await asyncio.to_thread(self.mark_surfaced, name, ids, now=now)

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
        retirements: Optional[List[Dict[str, Any]]] = None,
        reviewed_at: Optional[float] = None,
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
        its text and starts its lifetime, which its ``due_at`` (the end of the
        matter) may shorten; ``now`` is when the change is made. A candidate
        that is retraction-only (see _retraction_only) may only remove; a
        removal by one that reports a kept promise marks the promise done.

        ``retirements`` (``{target, outcome, reason, noted_at}``) are applied
        after the candidates, see _retire_unlocked. ``reviewed_at`` records
        that a model has just looked at the whole section. Returns per-action
        counts.
        """
        counts = dict.fromkeys(
            ("added", "replaced", "renewed", "removed", "retired", "expired", "evicted",
             "skipped", "stale"), 0,
        )
        if not name or not candidates:
            return counts
        ts = float(now if now is not None else _now())
        with self._mutating(name):
            facts, history, last_reviewed = self._read_unlocked(name)
            live, counts["expired"] = self._sweep_unlocked(facts, history, ts)
            # Entries the decisions never saw: another admission added them
            # after the snapshot (two first admissions into an empty section,
            # say). An add may contradict one of them, so a checked round
            # decides it again with them in view.
            unseen = (
                {e["id"] for e in live if e["id"] not in expected_revs}
                if expected_revs is not None else set()
            )
            # Withdrawn entries: when the retracting account was given, by id
            # (removals and retirements, for decisions the model matched to
            # one) and by text (also replacements, for verbatim replays of
            # what was retracted).
            withdrawals = _live_withdrawals(
                history, ts, actions=_WITHDRAWING_ACTIONS | {ACTION_REPLACE},
            )
            retracted = {
                w["id"]: w["withdrawn_at"] for w in withdrawals
                if w["action"] in _WITHDRAWING_ACTIONS
            }
            superseded: Dict[str, float] = {}
            for w in withdrawals:
                key = _text_key(w["text"])
                superseded[key] = max(superseded.get(key, w["withdrawn_at"]), w["withdrawn_at"])
            touched: set[str] = set()
            for index, (candidate, (action, target_id)) in enumerate(zip(candidates, decisions)):
                by_id = {e["id"]: e for e in live}
                target = by_id.get(target_id) if target_id else None
                noted = min(_as_float(candidate.get("noted_at")) or ts, ts)
                retracted_at = retracted.get(target_id) if target is None and target_id else None
                # Withdrawn as the decision saw it: already withdrawn in the
                # snapshot. An entry that was live in the snapshot and has been
                # withdrawn since is an outdated view instead, decided again
                # below (except a removal, which has nothing left to do).
                withdrawn_in_view = retracted_at is not None and (
                    expected_revs is None or target_id not in expected_revs
                )
                if action in _TARGETED_ACTIONS and (
                    withdrawn_in_view
                    # A removal whose target someone else already withdrew has
                    # nothing left to do, unless entries this decision never saw
                    # appeared meanwhile ("exam moved to Thursday" next to the
                    # withdrawal): then it is decided again with them in view.
                    or (action == ACTION_REMOVE and retracted_at is not None
                        and not (unseen and retry_stale))
                ):
                    # Matched to an entry that was withdrawn. Removing it again
                    # does nothing (a second "cancelled" must not pin itself);
                    # a replace / renew given before the withdrawal stays out,
                    # one given after it is new.
                    action = (
                        None if action == ACTION_REMOVE or noted < retracted_at
                        else ACTION_ADD
                    )
                elif action in _TARGETED_ACTIONS:
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
                        # Never undo a change this decision did not see, nor
                        # bring back what a later account withdrew.
                        action = (
                            None if action == ACTION_REMOVE
                            or (retracted_at is not None and noted < retracted_at)
                            else ACTION_ADD
                        )
                        target = None
                elif (action == ACTION_ADD and unseen and retry_stale
                        and not _retraction_only(candidate)):
                    counts["stale"] += 1
                    if stale_out is not None:
                        stale_out.append(index)
                    continue
                if _retraction_only(candidate) and action != ACTION_REMOVE:
                    # Its own lifetime is over, an earlier round already read it
                    # as a retraction, or it reports a kept promise: it may only
                    # retract an entry, never pin itself.
                    action = None
                if (action == ACTION_ADD
                        and noted < superseded.get(_text_key(candidate["text"]), float("-inf"))):
                    # The very words a later account withdrew or replaced
                    # (a delayed replay of Monday's "exam on Wednesday" after
                    # Tuesday's "exam cancelled"): keep them out.
                    action = None
                if action == ACTION_ADD:
                    action, target = self._renew_instead_of_duplicate(candidate, live)
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
                    history.append(self._history_record(target, "replace", ts, noted_at=noted))
                    key = _text_key(target["text"])
                    superseded[key] = max(superseded.get(key, noted), noted)
                    due_at = candidate.get("due_at")
                    target.update({
                        "text": candidate["text"],
                        "entity": candidate["entity"],
                        "kind": candidate.get("kind", KIND_FACT),
                        "ttl_days": candidate["ttl_days"],
                        "text_at": noted,
                        "updated_at": ts,
                        "expire_at": _expiry(noted, candidate["ttl_days"], due_at),
                        "due_at": due_at,
                        "rev": target["rev"] + 1,
                    })
                    _set_kind_fields(target)
                    touched.add(target["id"])
                    counts["replaced"] += 1
                elif action == ACTION_RENEW:
                    # A later account's end of the matter wins; an older one, or
                    # one without an end, keeps the entry's own end, which also
                    # bounds what the renewal may add ("exam on Wednesday" said
                    # again on Thursday must not carry it for another tier).
                    due_at = target.get("due_at")
                    if candidate.get("due_at") is not None and noted >= target["text_at"]:
                        due_at = candidate["due_at"]
                    # Saying the same thing again never shortens what is left,
                    # except by the end of the matter: once a later account dates
                    # it ("the exam is tomorrow"), the entry leaves after it.
                    expire_at = max(
                        target["expire_at"], noted + candidate["ttl_days"] * _DAY_SECONDS,
                    )
                    if due_at is not None:
                        expire_at = min(
                            expire_at, due_at + PINNED_FACT_DUE_GRACE_DAYS * _DAY_SECONDS,
                        )
                    target["expire_at"] = expire_at
                    target["due_at"] = due_at
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
                    history.append(self._history_record(
                        target, "remove", ts, noted_at=noted,
                        status=STATUS_DONE if candidate.get("fulfilled") else None,
                    ))
                    retracted[target["id"]] = noted
                    key = _text_key(target["text"])
                    superseded[key] = max(superseded.get(key, noted), noted)
                    live = [e for e in live if e["id"] != target["id"]]
                    touched.discard(target["id"])
                    counts["removed"] += 1
                else:
                    counts["skipped"] += 1
            live, counts["retired"] = self._retire_unlocked(
                live, history, retirements or [], ts,
                expected_revs=expected_revs, touched=touched,
            )
            while len(live) > PINNED_FACT_MAX_ACTIVE:
                # Never evict what this round just wrote: the newest entry may
                # well be the one closest to expiry (a 3-day tier).
                pool = [e for e in live if e["id"] not in touched] or live
                victim = min(pool, key=lambda e: e["expire_at"])
                history.append(self._history_record(victim, "evict", ts))
                live = [e for e in live if e["id"] != victim["id"]]
                counts["evicted"] += 1
            reviewed = reviewed_at is not None and (
                last_reviewed is None or reviewed_at > last_reviewed
            )
            changed = reviewed or any(
                counts[key] for key in counts if key not in ("skipped", "stale")
            )
            if changed:
                self._write_unlocked(
                    name, live, history, reviewed_at if reviewed else last_reviewed,
                )
        return counts

    def apply_retirements(
        self,
        name: str,
        retirements: List[Dict[str, Any]],
        *,
        expected_revs: Dict[str, int],
        now: Optional[float] = None,
        reviewed_at: Optional[float] = None,
    ) -> Dict[str, int]:
        """Apply the idle-time check's verdicts (see _retire_unlocked); returns counts."""
        counts = {"retired": 0, "expired": 0}
        if not name:
            return counts
        ts = float(now if now is not None else _now())
        with self._mutating(name):
            facts, history, last_reviewed = self._read_unlocked(name)
            live, counts["expired"] = self._sweep_unlocked(facts, history, ts)
            live, counts["retired"] = self._retire_unlocked(
                live, history, retirements, ts, expected_revs=expected_revs, touched=set(),
            )
            reviewed = reviewed_at is not None and (
                last_reviewed is None or reviewed_at > last_reviewed
            )
            if reviewed or counts["retired"] or counts["expired"]:
                self._write_unlocked(
                    name, live, history, reviewed_at if reviewed else last_reviewed,
                )
        return counts

    def _retire_unlocked(
        self,
        live: List[Dict[str, Any]],
        history: List[Dict[str, Any]],
        retirements: List[Dict[str, Any]],
        ts: float,
        *,
        expected_revs: Optional[Dict[str, int]],
        touched: set[str],
    ) -> Tuple[List[Dict[str, Any]], int]:
        """Take the entries ``retirements`` name out of ``live``; ``(live, retired)``.

        A retirement is skipped, never retried, when its entry is gone, was
        changed since the snapshot it was decided on (``expected_revs``) or
        by this same round (``touched``), or was said after the evidence
        (``noted_at`` before the entry's ``text_at``). Keeping an entry that is
        over costs it one more day in the section; retiring one on an outdated
        view could take out what a newer account just said. A retired
        commitment leaves with status done when it was kept, else expired.
        """
        retired = 0
        for item in retirements:
            target = next((e for e in live if e["id"] == item["target"]), None)
            if (target is None or target["id"] in touched
                    or (expected_revs is not None
                        and target["rev"] != expected_revs.get(target["id"]))
                    or item["noted_at"] < target["text_at"]):
                continue
            history.append(self._history_record(
                target, ACTION_RETIRE, ts, noted_at=item["noted_at"],
                status=STATUS_DONE if item["outcome"] == OUTCOME_DONE else STATUS_EXPIRED,
                outcome=item["outcome"], reason=item.get("reason") or "",
            ))
            live = [e for e in live if e["id"] != target["id"]]
            retired += 1
        return live, retired

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
        entry_id = PINNED_FACT_ID_PREFIX + secrets.token_hex(4)
        while entry_id in taken:
            entry_id = PINNED_FACT_ID_PREFIX + secrets.token_hex(4)
        due_at = candidate.get("due_at")
        entry = {
            "id": entry_id,
            "text": candidate["text"],
            "entity": candidate["entity"],
            "kind": candidate.get("kind", KIND_FACT),
            "ttl_days": candidate["ttl_days"],
            "created_at": ts,
            "text_at": noted,
            "updated_at": ts,
            "expire_at": _expiry(noted, candidate["ttl_days"], due_at),
            "due_at": due_at,
            "renew_count": 0,
            "rev": 0,
        }
        _set_kind_fields(entry)
        return entry

    @staticmethod
    def _history_record(
        entry: Dict[str, Any],
        action: str,
        ts: float,
        *,
        noted_at: Optional[float] = None,
        status: Optional[str] = None,
        outcome: Optional[str] = None,
        reason: Optional[str] = None,
    ) -> Dict[str, Any]:
        record = {
            "id": entry["id"],
            "text": entry["text"],
            "entity": entry["entity"],
            "kind": entry.get("kind", KIND_FACT),
            "action": action,
            "at": ts,
        }
        if record["kind"] == KIND_COMMITMENT and action != ACTION_REPLACE:
            # A promise leaving the section was kept or lapsed; a replaced one
            # lives on under the same id.
            record["status"] = status or (
                entry["status"] if entry.get("status") == STATUS_DONE else STATUS_EXPIRED
            )
        if outcome is not None:
            record.update({"outcome": outcome, "reason": reason or ""})
        if noted_at is not None:
            # A removal, retirement or replacement keeps when the retracting
            # account was given and what it retracted, so an account from
            # before it can be kept out (see _live_withdrawals).
            record.update({
                "noted_at": noted_at,
                "text_at": entry["text_at"],
                "expire_at": entry["expire_at"],
            })
        return record

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
        exam is tomorrow"), so the entry is dated, its end resolved and its
        lifetime counted from the conversation, not from the admission. A
        candidate whose lifetime already ran out by then may still remove an
        entry it shows to be no longer true ("the exam was cancelled",
        replayed days later) but never adds, replaces or renews anything.

        Decisions are made on a snapshot. When another admission changes an
        entry a decision targets, or adds one an add was not decided against,
        before it is applied, the affected candidates are decided again on
        fresh state, up to PINNED_FACT_RESOLVE_ATTEMPTS rounds. The last round
        still checks, and degrades an outdated decision instead of applying it
        (see apply_decisions), so a slow admission never undoes a newer one.
        The retirements a round's model answer carries are checked the same
        way but never retried: one on an outdated view is dropped.
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
        candidates = [self._date_candidate(c, noted, ts) for c in candidates]
        outdated = sum(1 for c in candidates if c["outdated"])
        candidates, card_rejected = await self._afilter_card_conflicts(name, candidates)
        totals: Dict[str, int] = {"card_rejected": card_rejected, "outdated": outdated}
        pending = list(range(len(candidates)))
        rounds = 0
        resolved_by_model = False
        while pending and rounds < PINNED_FACT_RESOLVE_ATTEMPTS:
            rounds += 1
            batch = [candidates[i] for i in pending]
            active, removed = await self.aget_snapshot(name, now=ts)
            # A withdrawal only matters to candidates given before it.
            removed = [
                r for r in removed
                if any(c["noted_at"] < r["withdrawn_at"] for c in batch)
            ][:PINNED_FACT_MAX_ACTIVE]
            retirements: List[Dict[str, Any]] = []
            reviewed_at: Optional[float] = None
            if not active and not removed:
                decisions: List[Tuple[Optional[str], Optional[str]]] = [
                    (ACTION_ADD, None) for _ in batch
                ]
            else:
                results = await self._aresolve_with_model(
                    name, active, batch, now=ts, removed=removed,
                )
                parsed: Dict[int, Tuple[str, Optional[str]]] = {}
                if results is not None:
                    reviewed_at = ts
                    parsed = parse_resolve_decisions(
                        results, len(batch),
                        {e["id"] for e in active} | {r["id"] for r in removed},
                    )
                    retirements = self._admission_retirements(results, active, batch, ts)
                resolved_by_model = resolved_by_model or bool(parsed) or bool(retirements)
                # No usable answer for a candidate: add it. apply_decisions turns
                # an add of a text that is already live into a renewal of that
                # entry. One cited only as evidence for a retirement has served
                # its purpose.
                cited = {r["candidate"] for r in retirements if r["candidate"] is not None}
                decisions = [
                    parsed.get(i, (None, None) if i in cited else (ACTION_ADD, None))
                    for i in range(len(batch))
                ]
            stale: List[int] = []
            counts = await asyncio.to_thread(
                self.apply_decisions, name, batch, decisions,
                now=ts,
                expected_revs={e["id"]: e["rev"] for e in active},
                retry_stale=rounds < PINNED_FACT_RESOLVE_ATTEMPTS,
                stale_out=stale,
                retirements=retirements,
                reviewed_at=reviewed_at,
            )
            for key, value in counts.items():
                if key != "stale":
                    totals[key] = totals.get(key, 0) + value
            for i in stale:
                if decisions[i][0] == ACTION_REMOVE:
                    # Decided again on fresh state, but still a retraction.
                    batch[i]["retraction"] = True
            pending = [pending[i] for i in stale]
        logger.info(
            "[PinnedFacts] %s: candidates=%d rounds=%d model=%s added=%d replaced=%d "
            "renewed=%d removed=%d retired=%d expired=%d evicted=%d skipped=%d "
            "card_rejected=%d outdated=%d",
            name, len(candidates), rounds, resolved_by_model,
            totals.get("added", 0), totals.get("replaced", 0), totals.get("renewed", 0),
            totals.get("removed", 0), totals.get("retired", 0), totals.get("expired", 0),
            totals.get("evicted", 0), totals.get("skipped", 0), card_rejected, outdated,
        )
        return totals

    @staticmethod
    def _date_candidate(candidate: Dict[str, Any], noted: float, ts: float) -> Dict[str, Any]:
        """A candidate dated by its conversation: ``noted_at``, ``due_at``, ``outdated``."""
        dated = dict(candidate, noted_at=noted)
        due_at = _due_at(candidate, noted)
        if due_at is not None:
            dated["due_at"] = due_at
        dated["outdated"] = _expiry(noted, candidate["ttl_days"], due_at) <= ts
        return dated

    @staticmethod
    def _admission_retirements(
        results: Any, active: List[Dict[str, Any]], batch: List[Dict[str, Any]], ts: float,
    ) -> List[Dict[str, Any]]:
        """The retirements of a resolve answer that may be applied.

        Evidence is a cited candidate; its conversation time dates the
        retirement (apply checks that it is not older than the entry). An
        uncited one stands only as retirement_allowed permits, dated now.
        """
        by_id = {entry["id"]: entry for entry in active}
        allowed: List[Dict[str, Any]] = []
        for item in parse_retirements(results, set(by_id), len(batch)):
            cited = batch[item["candidate"]] if item["candidate"] is not None else None
            if not retirement_allowed(
                by_id[item["target"]], item["outcome"], evidenced=cited is not None, now=ts,
            ):
                continue
            allowed.append(dict(item, noted_at=cited["noted_at"] if cited else ts))
        return allowed

    async def _afilter_card_conflicts(
        self, name: str, candidates: List[Dict[str, Any]],
    ) -> Tuple[List[Dict[str, Any]], int]:
        checker = getattr(self._persona_manager, "acontradicts_character_card", None)
        if checker is None:
            return candidates, 0
        kept: List[Dict[str, Any]] = []
        for candidate in candidates:
            if candidate.get("fulfilled"):
                # Never enters the section, so it cannot contradict the card.
                kept.append(candidate)
                continue
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

    async def _amaster_name(self) -> str:
        try:
            _, _, _, _, name_mapping, _, _, _, _ = (
                await self._config_manager.aget_character_data()
            )
            return (name_mapping or {}).get("human") or "master"
        except Exception:
            return "master"

    async def _aresolve_with_model(
        self,
        name: str,
        active: List[Dict[str, Any]],
        candidates: List[Dict[str, Any]],
        *,
        now: float,
        removed: Optional[List[Dict[str, Any]]] = None,
    ) -> Any:
        """One resolve-model call; the parsed JSON, or None on any failure."""
        from memory.thinking_llm import ainvoke_thinking
        from utils.token_tracker import set_call_type

        master_name = await self._amaster_name()
        prompt = await asyncio.to_thread(
            self._build_resolve_prompt, name, master_name, active, candidates, now,
            removed or [],
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
            return _parse_model_json(resp)
        except Exception as exc:
            logger.warning(
                "[PinnedFacts] %s: resolve model failed, adding the candidates: %s",
                name, type(exc).__name__,
            )
            return None

    @staticmethod
    def _prompt_language(texts: List[str]) -> str:
        from utils.language_utils import (
            detect_prompt_language_with_ascii_fallback,
            get_global_language_full,
        )

        return detect_prompt_language_with_ascii_fallback(
            "\n".join(texts), ui_language=get_global_language_full(),
        )

    @classmethod
    def _zone_lines(cls, active: List[Dict[str, Any]], lang: str) -> str:
        """The live entries with their ids, as both model prompts list them."""
        return "\n".join(
            f"[{entry['id']}] " + cls._entry_line(
                entry, lang, since=_format_moment(entry["text_at"]),
                text=truncate_to_tokens(entry["text"], PINNED_FACT_TEXT_MAX_TOKENS),
            )
            for entry in active[:PINNED_FACT_MAX_ACTIVE]
        )

    @classmethod
    def _build_resolve_prompt(
        cls,
        name: str,
        master_name: str,
        active: List[Dict[str, Any]],
        candidates: List[Dict[str, Any]],
        now: float,
        removed: List[Dict[str, Any]],
    ) -> str:
        """The resolve prompt (token work, so callers run it off the event loop).

        Input stays bounded: at most PINNED_FACT_MAX_ACTIVE live and as many
        withdrawn entries, plus PINNED_FACT_MAX_CANDIDATES_PER_ROUND
        candidates, each text capped at PINNED_FACT_TEXT_MAX_TOKENS.
        """
        lang = cls._prompt_language([c["text"] for c in candidates])
        withdrawn = "\n".join(
            f"[{record['id']}] " + render_pinned_fact_removed_line(
                truncate_to_tokens(record["text"], PINNED_FACT_TEXT_MAX_TOKENS),
                since=_format_moment(record["text_at"]),
                removed=_format_moment(record["withdrawn_at"]),
                lang=lang,
            )
            for record in removed[:PINNED_FACT_MAX_ACTIVE]
        )
        zone = "\n".join(part for part in (cls._zone_lines(active, lang), withdrawn) if part)
        # Candidates carry their own dates too: the model must be able to tell
        # an older account of a matter from a newer one.
        lines = []
        for index, candidate in enumerate(candidates, 1):
            noted = candidate.get("noted_at", now)
            text = truncate_to_tokens(candidate["text"], PINNED_FACT_TEXT_MAX_TOKENS)
            if candidate.get("fulfilled"):
                line = render_pinned_commitment_done_line(
                    text, since=_format_moment(noted), lang=lang,
                )
            else:
                line = cls._entry_line(
                    {
                        "kind": candidate.get("kind", KIND_FACT),
                        "due_at": candidate.get("due_at"),
                        "expire_at": _expiry(noted, candidate["ttl_days"], candidate.get("due_at")),
                    },
                    lang, since=_format_moment(noted), text=text,
                )
            lines.append(f"{index}. {line}")
        return _fill_template(get_pinned_fact_resolve_prompt(lang), {
            "LANLAN_NAME": name,
            "MASTER_NAME": master_name,
            "TODAY": _format_date(now),
            "ZONE": zone,
            "CANDIDATES": "\n".join(lines),
        })

    # ── idle-time review ──────────────────────────────────────

    def _sweep(self, name: str, ts: float) -> Tuple[List[Dict[str, Any]], Optional[float]]:
        """Move what is no longer live to the history; ``(live entries, reviewed_at)``."""
        with self._mutating(name):
            facts, history, reviewed_at = self._read_unlocked(name)
            live, moved = self._sweep_unlocked(facts, history, ts)
            if moved:
                self._write_unlocked(name, live, history, reviewed_at)
        return [dict(e) for e in live], reviewed_at

    async def areview(
        self,
        name: str,
        load_conversation: Callable[[float], Awaitable[List[Dict[str, Any]]]],
        *,
        now: Optional[float] = None,
    ) -> Dict[str, int]:
        """The idle-time check: retire the entries that are over; returns counts.

        Expired entries leave first. The model is asked only when the section
        is not empty and no model has looked at all of it within
        PINNED_FACT_REVIEW_INTERVAL_HOURS. ``load_conversation(since)`` returns
        the messages since that epoch time, oldest first, as ``{"at": epoch,
        "role": "human" | "ai", "text": str}``. The model may only retire: a
        done / invalid verdict counts only when its evidence is a verbatim
        quote of a message not older than the entry, any verdict only as
        retirement_allowed permits, and a failed or unusable answer keeps
        everything. Verdicts are checked against ``rev`` like any decision.
        """
        if not name:
            return {}
        ts = float(now if now is not None else _now())
        active, reviewed_at = await asyncio.to_thread(self._sweep, name, ts)
        if not active or (
            reviewed_at is not None
            and ts - reviewed_at < PINNED_FACT_REVIEW_INTERVAL_HOURS * 3600.0
        ):
            return {}
        since = max(
            min(entry["text_at"] for entry in active),
            ts - PINNED_FACT_REVIEW_LOOKBACK_DAYS * _DAY_SECONDS,
        )
        conversation = await load_conversation(since)
        master_name = await self._amaster_name()
        prompt, shown = await asyncio.to_thread(
            self._build_review_prompt, name, master_name, active, conversation or [], ts,
        )
        results = await self._areview_with_model(name, prompt)
        if results is None:
            return {"failed": 1}
        by_id = {entry["id"]: entry for entry in active}
        retirements = []
        for item in parse_retirements(results, set(by_id)):
            entry = by_id[item["target"]]
            quoted_at = _quoted_at(item["evidence"], shown, entry["text_at"])
            if retirement_allowed(entry, item["outcome"], evidenced=quoted_at is not None, now=ts):
                retirements.append(dict(item, noted_at=quoted_at or ts))
        counts = await asyncio.to_thread(
            self.apply_retirements, name, retirements,
            expected_revs={entry["id"]: entry["rev"] for entry in active},
            now=ts, reviewed_at=ts,
        )
        logger.info(
            "[PinnedFacts] %s: review entries=%d messages=%d verdicts=%d retired=%d",
            name, len(active), len(shown), len(retirements), counts["retired"],
        )
        return counts

    async def _areview_with_model(self, name: str, prompt: str) -> Any:
        """One review-model call, thinking off; the parsed JSON, or None on any failure."""
        from utils.llm_client import create_chat_llm_async
        from utils.token_tracker import set_call_type

        try:
            set_call_type("memory_pinned_facts_review")
            api_config = await self._config_manager.aget_model_api_config(
                PINNED_FACT_REVIEW_MODEL_TIER,
            )
            # The factory default leaves thinking off: the answer is a short
            # list of verdicts, and the code checks every one of them.
            llm = await create_chat_llm_async(
                api_config["model"],
                api_config["base_url"], api_config["api_key"],
                timeout=MEMORY_LLM_HARD_TIMEOUT_SECONDS, max_retries=0,
                max_completion_tokens=LLM_OUTPUT_GUARD_MAX_TOKENS,  # runaway guard; the answer is a short JSON list
                provider_type=api_config.get("provider_type"),
            )
            try:
                resp = await llm.ainvoke(prompt)  # noqa: LLM_INPUT_BUDGET  # prompt built by _build_review_prompt: entries capped at PINNED_FACT_TEXT_MAX_TOKENS, conversation at PINNED_FACT_REVIEW_CONVERSATION_MAX_TOKENS.
            finally:
                await llm.aclose()
            return _parse_model_json(resp)
        except Exception as exc:
            logger.warning(
                "[PinnedFacts] %s: review model failed, keeping every entry: %s",
                name, type(exc).__name__,
            )
            return None

    @classmethod
    def _build_review_prompt(
        cls,
        name: str,
        master_name: str,
        active: List[Dict[str, Any]],
        conversation: List[Dict[str, Any]],
        now: float,
    ) -> Tuple[str, List[Dict[str, Any]]]:
        """The review prompt and the messages it shows, ``{at, text}`` oldest first.

        Each message is capped at PINNED_FACT_REVIEW_MESSAGE_MAX_TOKENS and the
        newest ones are kept within PINNED_FACT_REVIEW_CONVERSATION_MAX_TOKENS;
        evidence is checked against exactly what the model was shown.
        """
        lang = cls._prompt_language([entry["text"] for entry in active])
        speakers = {"human": master_name, "ai": name}
        newest_first = []
        for message in reversed(conversation):
            at = _as_float(message.get("at")) if isinstance(message, dict) else None
            speaker = speakers.get(message.get("role")) if isinstance(message, dict) else None
            raw = message.get("text") if isinstance(message, dict) else None
            text = (
                truncate_to_tokens(" ".join(raw.split()), PINNED_FACT_REVIEW_MESSAGE_MAX_TOKENS)
                if isinstance(raw, str) else ""
            )
            if at is None or speaker is None or not text:
                continue
            newest_first.append((at, text, f"[{_format_moment(at)}] {speaker} | {text}"))
        kept, _ = take_lines_within_token_budget(
            [line for _, _, line in newest_first], PINNED_FACT_REVIEW_CONVERSATION_MAX_TOKENS,
        )
        shown = list(reversed(newest_first[:len(kept)]))
        prompt = _fill_template(get_pinned_fact_review_prompt(lang), {
            "LANLAN_NAME": name,
            "MASTER_NAME": master_name,
            "TODAY": _format_date(now),
            "ZONE": cls._zone_lines(active, lang),
            "CONVERSATION": "\n".join(line for _, _, line in shown),
        })
        return prompt, [{"at": at, "text": text} for at, text, _ in shown]
