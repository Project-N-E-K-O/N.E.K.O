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

"""Weekly behavior review: short reminders on how she talks, from what was said.

Weekly introspection reviews what the memory block says about her. Nothing
reviews what she actually says: a reaction the user keeps grumbling about, or a
way of talking that has slid away from her card, stays that way until the user
edits the card by hand. This module runs one model call after each weekly
introspection, over a numbered sample of recent conversation, and leaves at
most a handful of reminders for the next session start.

- **Two kinds of note.** ``style``: a reaction the user complained about across
  sittings (``complaint``) or asked once and clearly to change for good
  (``request``). ``drift``: a way of talking that departs from her card, needing
  no complaint, since the card is what the note steers back to.
- **Code counts the evidence.** The model cites sample line numbers; sittings and
  days are computed here from those lines. A line cited as the user's must be a
  user line, so her own words can never pass for the user's opinion.
- **No lines to recite.** A note says what to do less of and in which direction,
  never what to say. Quotation marks, and any run copied from a reply or from the
  habitual-phrase list, are rejected rather than cut.
- **Nothing is permanent.** A note lapses three weekly cycles after its last
  renewal, and renewal needs the same evidence all over again, so a note cannot
  keep itself alive. ``retire`` removes one early.
- **Only a reminder.** The file is read when ``/new_dialog`` builds a session and
  nothing else: not the card, not persona entries, no tool for the model. The
  block stays out of ``arender_injected_memory``, so weekly introspection never
  acts on text the model itself wrote here; this review checks its own notes.

Stored shape (``memory/{name}/behavior_notes.json``), recomputable and so left out
of cloud saves. It holds the note text and evidence counts, never the user's
words::

    {
      "schema_version": "behavior-notes/v1",
      "language": <miner language tag, e.g. "zh-CN">,
      "reviewed_at": <epoch seconds>,
      "source_end_at": <time of the newest message reviewed>,
      "notes": [{"id", "kind", "basis", "avoid", "direction", "created_at",
                 "renewed_at", "expires_at", "evidence"}],
      "retired": [{"avoid", "direction", "outcome", "at"}]
    }

Logging: conversation and note text go to stdout only; ``logger`` records counts.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import threading
import time
from collections import Counter
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence

from config import (
    HABITUAL_PHRASE_SESSION_GAP_SECONDS,
    MEMORY_BEHAVIOR_NOTE_AVOID_MAX_TOKENS,
    MEMORY_BEHAVIOR_NOTE_CORRECTED_MIN_SESSIONS,
    MEMORY_BEHAVIOR_NOTE_DIRECTION_MAX_TOKENS,
    MEMORY_BEHAVIOR_NOTE_EVIDENCE_GATES,
    MEMORY_BEHAVIOR_NOTE_MAX_PER_KIND,
    MEMORY_BEHAVIOR_NOTE_RETIRED_KEEP_SECONDS,
    MEMORY_BEHAVIOR_NOTE_TTL_SECONDS,
    MEMORY_BEHAVIOR_NOTES_PROMPT_MAX_TOKENS,
    MEMORY_BEHAVIOR_REVIEW_CARD_MAX_TOKENS,
    MEMORY_BEHAVIOR_REVIEW_LINE_MAX_TOKENS,
    MEMORY_BEHAVIOR_REVIEW_MIN_REPLIES,
    MEMORY_BEHAVIOR_REVIEW_MIN_SESSIONS,
    MEMORY_BEHAVIOR_REVIEW_RETIRED_SHOWN,
    MEMORY_BEHAVIOR_REVIEW_SAMPLE_MAX_TOKENS,
    MEMORY_BEHAVIOR_REVIEW_SIGNALS_MAX_TOKENS,
)
from config.prompts.prompts_behavior_notes import (
    get_behavior_review_none,
    get_behavior_review_prompt,
    render_behavior_note_item,
    render_behavior_notes_block,
    scan_behavior_feedback_cues,
)
from config.prompts.prompts_directives import scan_negative_keywords
from memory.habitual_phrases import (
    _REPLY_STAMP_RE,
    _SPACED_LANGUAGES,
    _as_count,
    _as_time,
    _compact,
    _defang,
    _file_lock,
    _is_sublist,
    _sessions_of,
    _short_language,
    _words,
    card_text_from_character,
    get_habitual_phrase_store,
)
from memory.introspection import (
    FAILURE_FINAL,
    FAILURE_TRANSIENT,
    _parse_number,
    _print_text,
    classify_model_failure,
    is_permanently_suppressed,
    parse_actions,
)
from utils.config_manager import get_config_manager
from utils.file_utils import atomic_write_json
from utils.logger_config import get_module_logger
from utils.tokenize import count_tokens, take_lines_within_token_budget, truncate_to_tokens

logger = get_module_logger(__name__, "Memory")

__all__ = [
    "FAILURE_FINAL",
    "FAILURE_TRANSIENT",
    "BEHAVIOR_NOTES_FILENAME",
    "BehaviorNoteStore",
    "BehaviorReviewResult",
    "ReviewMessage",
    "arun_behavior_review",
    "build_review_sample",
    "get_behavior_note_store",
    "plan_note_actions",
]


BEHAVIOR_NOTES_FILENAME = "behavior_notes.json"
SCHEMA_VERSION = "behavior-notes/v1"

ROLE_USER = "human"
ROLE_REPLY = "ai"

KIND_STYLE = "style"
KIND_DRIFT = "drift"
BASIS_COMPLAINT = "complaint"
BASIS_REQUEST = "request"
# A drift note has no basis to choose; it reads its gate under this key.
_BASIS_DRIFT = "drift"

ACTION_ADD = "add"
ACTION_KEEP = "keep"
ACTION_RETIRE = "retire"

OUTCOME_CORRECTED = "corrected"
OUTCOME_CARD_CONFLICT = "card_conflict"
OUTCOME_STALE = "stale"
_OUTCOMES = frozenset({OUTCOME_CORRECTED, OUTCOME_CARD_CONFLICT, OUTCOME_STALE})

# Rejection reasons, counted per run.
REJECT_INVALID = "invalid"
REJECT_UNKNOWN_ID = "unknown_id"
REJECT_DUPLICATE = "duplicate_target"
REJECT_BAD_EVIDENCE = "bad_evidence"
REJECT_BELOW_GATE = "below_gate"
REJECT_SCRIPTED = "scripted"
REJECT_TOO_LONG = "too_long"
REJECT_OVER_CAP = "over_cap"
REJECT_NO_CARD = "no_card"
REJECT_NOT_YET_CORRECTED = "not_yet_corrected"
REJECT_CARD_CONFLICT_AGAIN = "card_conflict_again"
REJECT_SAME_AS_LIVE = "same_as_live"

# Runs that end without a model call.
SKIP_NO_HISTORY = "no_history"
SKIP_NO_NEW_CONVERSATION = "no_new_conversation"
SKIP_TOO_FEW = "too_few"

_REASON_MAX_CHARS = 200
# A clock this far ahead of the stored time is a rollback, not a fresh note.
_FUTURE_SKEW_SECONDS = 86400.0
# Retired notes kept on file; the prompt shows MEMORY_BEHAVIOR_REVIEW_RETIRED_SHOWN.
_RETIRED_MAX = 24

# Per-line bookkeeping in the sample budget: the "[123 U] " tag and the newline.
_LINE_OVERHEAD_TOKENS = 8
# A "--- 2026-11-09 21:30" line opening a sitting.
_SESSION_HEADER_TOKENS = 14
# Lines taken from each sitting per round of the fill, so an evening of talk
# cannot crowd out the others.
_FILL_LINES_PER_ROUND = 4

# A note may not hold a run this long copied from a reply or a listed phrase.
_ECHO_CHARACTERS = 8
_ECHO_WORDS = 5
# Quotation marks and title marks: a note that needs them is carrying a line.
# The closing ASCII-style apostrophe is left out; it is how "don't" gets typed.
_QUOTE_RE = re.compile(r"[「」『』“”‘\"《》«»„＂]")
_DELIMITER_RE = re.compile(r"={3,}")

_TAG_OF_ROLE = {ROLE_USER: "U", ROLE_REPLY: "A"}


def _collapse(raw: Any) -> str:
    return " ".join(raw.split()) if isinstance(raw, str) else ""


def _gate_for(basis: str) -> Mapping[str, Sequence[int]]:
    return MEMORY_BEHAVIOR_NOTE_EVIDENCE_GATES[basis]


# ── the conversation sample ─────────────────────────────────────────


@dataclass(frozen=True)
class ReviewMessage:
    """One stored message: naive local time, role, and the text that was visible."""

    at: datetime
    role: str
    text: str


@dataclass(frozen=True)
class SampleLine:
    number: int
    role: str
    session: int
    day: date
    at: float
    text: str


@dataclass(frozen=True)
class ReviewSample:
    text: str
    lines: Mapping[int, SampleLine]

    def replies(self) -> list[str]:
        return [line.text for line in self.lines.values() if line.role == ROLE_REPLY]


def _clean_line(text: str) -> str:
    body = _REPLY_STAMP_RE.sub("", text or "", count=1)
    return truncate_to_tokens(
        _defang(" ".join(body.split())), MEMORY_BEHAVIOR_REVIEW_LINE_MAX_TOKENS,
    )


def _pick_sample_indices(
    messages: Sequence[ReviewMessage],
    sessions: Sequence[int],
    texts: Sequence[str],
    *,
    language: str,
    budget: int,
) -> set[int]:
    """Which messages enter the sample.

    Stretches where the user seems to be talking about how she talks come first,
    newest first and capped at half the budget, each with the line before and
    after it. The rest goes round the sittings, newest sitting first and each
    from its newest line back, so a quiet week is not outweighed by one long
    evening and drift, which needs replies from several sittings, can be seen.
    """
    costs = [count_tokens(text) + _LINE_OVERHEAD_TOKENS for text in texts]
    picked: set[int] = set()
    opened: set[int] = set()
    used = 0

    def take(index: int, limit: int) -> bool:
        nonlocal used
        if index in picked:
            return True
        cost = costs[index] + (0 if sessions[index] in opened else _SESSION_HEADER_TOKENS)
        if used + cost > limit:
            return False
        picked.add(index)
        opened.add(sessions[index])
        used += cost
        return True

    for hit in range(len(messages) - 1, -1, -1):
        message = messages[hit]
        if message.role != ROLE_USER or not (
            scan_negative_keywords(message.text, language)
            or scan_behavior_feedback_cues(message.text, language)
        ):
            continue
        for index in (hit, hit - 1, hit + 1):
            if 0 <= index < len(messages) and sessions[index] == sessions[hit]:
                take(index, budget // 2)

    by_session: dict[int, list[int]] = {}
    for index, session in enumerate(sessions):
        by_session.setdefault(session, []).append(index)
    queues = [by_session[session] for session in sorted(by_session, reverse=True)]
    while any(queues):
        for queue in queues:
            taken = 0
            while queue and taken < _FILL_LINES_PER_ROUND:
                index = queue.pop()
                if index not in picked and take(index, budget):
                    taken += 1
    return picked


def build_review_sample(
    messages: Iterable[ReviewMessage],
    *,
    language: str,
    budget: int = MEMORY_BEHAVIOR_REVIEW_SAMPLE_MAX_TOKENS,
) -> ReviewSample:
    """Number a budgeted selection of ``messages`` for the prompt.

    Lines are chosen until the budget is spent and never cut afterwards, so every
    number the model can cite stands for a line it really saw.
    """
    usable = [
        (message, text)
        for message, text in (
            (message, _clean_line(message.text))
            for message in sorted(messages, key=lambda item: item.at)
        )
        if text and message.role in _TAG_OF_ROLE
    ]
    ordered = [message for message, _text in usable]
    texts = [text for _message, text in usable]
    sessions = _sessions_of(ordered, HABITUAL_PHRASE_SESSION_GAP_SECONDS)
    picked = _pick_sample_indices(ordered, sessions, texts, language=language, budget=budget)

    rendered: list[str] = []
    lines: dict[int, SampleLine] = {}
    current_session: int | None = None
    for index in sorted(picked):
        message = ordered[index]
        if sessions[index] != current_session:
            current_session = sessions[index]
            rendered.append(f"--- {message.at:%Y-%m-%d %H:%M}")
        number = len(lines) + 1
        rendered.append(f"[{number} {_TAG_OF_ROLE[message.role]}] {texts[index]}")
        lines[number] = SampleLine(
            number=number,
            role=message.role,
            session=sessions[index],
            day=message.at.date(),
            at=message.at.timestamp(),
            text=texts[index],
        )
    return ReviewSample("\n".join(rendered), lines)


# ── evidence and action validation ──────────────────────────────────


@dataclass(frozen=True)
class EvidenceCount:
    messages: int = 0
    sessions: int = 0
    days: int = 0

    def meets(self, gate: Sequence[int]) -> bool:
        return (
            self.messages >= gate[0]
            and self.sessions >= gate[1]
            and self.days >= gate[2]
        )


def _count_cited(raw: Any, lines: Mapping[int, SampleLine], role: str) -> Optional[EvidenceCount]:
    """Evidence behind the cited numbers; None when any is not a shown line of ``role``."""
    if raw is None:
        return EvidenceCount()
    if not isinstance(raw, list):
        return None
    cited: dict[int, SampleLine] = {}
    for item in raw:
        number = _parse_number(item)
        line = lines.get(number) if number is not None else None
        if line is None or line.role != role:
            return None
        cited[line.number] = line
    return EvidenceCount(
        messages=len(cited),
        sessions=len({line.session for line in cited.values()}),
        days=len({line.day for line in cited.values()}),
    )


def _evidence_summary(user: EvidenceCount, reply: EvidenceCount) -> dict[str, int]:
    return {
        "user_messages": user.messages,
        "user_sessions": user.sessions,
        "user_days": user.days,
        "replies": reply.messages,
        "reply_sessions": reply.sessions,
        "reply_days": reply.days,
    }


class _ScriptGuard:
    """Tells whether note text carries a line of hers instead of describing a way of talking."""

    def __init__(self, replies: Iterable[str], phrases: Iterable[str], language: str) -> None:
        self._spaced = _short_language(language) in _SPACED_LANGUAGES
        phrases = list(phrases)
        sources = [*replies, *phrases]
        self._phrases_compact = tuple(filter(None, (_compact(p) for p in phrases)))
        self._phrases_words = tuple(filter(None, (tuple(_words(p)) for p in phrases)))
        # Letters and digits only inside each entry, so the newline can never be
        # part of a run and a run cannot span two replies.
        self._corpus = "\n".join(_compact(text) for text in sources)
        self._word_runs: set[tuple[str, ...]] = set()
        if self._spaced:
            for text in sources:
                words = _words(text)
                self._word_runs.update(
                    tuple(words[i : i + _ECHO_WORDS])
                    for i in range(len(words) - _ECHO_WORDS + 1)
                )

    def echoes(self, text: str) -> bool:
        if _QUOTE_RE.search(text):
            return True
        if self._spaced:
            words = _words(text)
            if any(
                tuple(words[i : i + _ECHO_WORDS]) in self._word_runs
                for i in range(len(words) - _ECHO_WORDS + 1)
            ):
                return True
            return any(_is_sublist(phrase, words) for phrase in self._phrases_words)
        compact = _compact(text)
        if any(
            compact[i : i + _ECHO_CHARACTERS] in self._corpus
            for i in range(len(compact) - _ECHO_CHARACTERS + 1)
        ):
            return True
        return any(phrase in compact for phrase in self._phrases_compact)


@dataclass
class NotePlan:
    added: list[dict] = field(default_factory=list)
    # Note id → the evidence summary it was renewed on.
    renewed: dict[str, dict[str, int]] = field(default_factory=dict)
    # ``(note, outcome, reason)``.
    retired: list[tuple[dict, str, str]] = field(default_factory=list)
    rejected: Counter = field(default_factory=Counter)

    @property
    def changes(self) -> int:
        return len(self.added) + len(self.renewed) + len(self.retired)


def _check_note_text(
    avoid: str, direction: str, guard: _ScriptGuard,
) -> Optional[str]:
    """The reason this note text is unusable, or None."""
    if not avoid or not direction:
        return REJECT_INVALID
    if _DELIMITER_RE.search(avoid) or _DELIMITER_RE.search(direction):
        return REJECT_INVALID
    if (
        count_tokens(avoid) > MEMORY_BEHAVIOR_NOTE_AVOID_MAX_TOKENS
        or count_tokens(direction) > MEMORY_BEHAVIOR_NOTE_DIRECTION_MAX_TOKENS
    ):
        return REJECT_TOO_LONG
    if guard.echoes(avoid) or guard.echoes(direction):
        return REJECT_SCRIPTED
    return None


def _count_gated(
    action: Mapping[str, Any], basis: str, lines: Mapping[int, SampleLine],
) -> tuple[Optional[dict[str, int]], Optional[str]]:
    """``(evidence summary, None)``, or ``(None, rejection reason)`` under ``basis``'s gate."""
    gate = _gate_for(basis)
    # A drift note rests on replies alone, so nothing cited as the user's is read.
    user = (
        EvidenceCount() if basis == _BASIS_DRIFT
        else _count_cited(action.get("user_evidence"), lines, ROLE_USER)
    )
    reply = _count_cited(action.get("reply_evidence"), lines, ROLE_REPLY)
    if user is None or reply is None:
        return None, REJECT_BAD_EVIDENCE
    if not user.meets(gate["user"]) or not reply.meets(gate["reply"]):
        return None, REJECT_BELOW_GATE
    return _evidence_summary(user, reply), None


def plan_note_actions(
    raw_actions: Any,
    *,
    lines: Mapping[int, SampleLine],
    notes: Sequence[Mapping[str, Any]],
    retired: Sequence[Mapping[str, Any]],
    phrases: Sequence[str],
    has_card: bool,
    language: str,
    now: float,
) -> NotePlan:
    """Check the model's actions against the sample and the live notes.

    ``notes`` are the live ones. Retirements are read first, then renewals, then
    additions, so a slot freed in the same answer can be used whatever order the
    model wrote its actions in. Anything that fails is dropped and counted.
    """
    plan = NotePlan()
    by_id = {str(note["id"]): note for note in notes}
    used: set[str] = set()
    by_verb: dict[str, list[dict]] = {ACTION_RETIRE: [], ACTION_KEEP: [], ACTION_ADD: []}
    for action in raw_actions if isinstance(raw_actions, list) else ():
        verb = action.get("action") if isinstance(action, dict) else None
        bucket = by_verb.get(verb) if isinstance(verb, str) else None
        if bucket is None:
            plan.rejected[REJECT_INVALID] += 1
        else:
            bucket.append(action)

    def target(action: Mapping[str, Any]) -> Optional[Mapping[str, Any]]:
        note_id = str(action.get("id") or "").strip()
        note = by_id.get(note_id)
        if note is None:
            plan.rejected[REJECT_UNKNOWN_ID] += 1
            return None
        if note_id in used:
            plan.rejected[REJECT_DUPLICATE] += 1
            return None
        return note

    for action in by_verb[ACTION_RETIRE]:
        note = target(action)
        if note is None:
            continue
        outcome = action.get("outcome")
        if not isinstance(outcome, str) or outcome not in _OUTCOMES:
            plan.rejected[REJECT_INVALID] += 1
            continue
        if outcome == OUTCOME_CORRECTED:
            # Sittings seen since the note last held: too few cannot show it stopped.
            since = float(note["renewed_at"])
            later = {line.session for line in lines.values() if line.at > since}
            if len(later) < MEMORY_BEHAVIOR_NOTE_CORRECTED_MIN_SESSIONS:
                plan.rejected[REJECT_NOT_YET_CORRECTED] += 1
                continue
        used.add(str(note["id"]))
        reason = _collapse(action.get("reason"))[:_REASON_MAX_CHARS]
        plan.retired.append((dict(note), outcome, reason))

    for action in by_verb[ACTION_KEEP]:
        note = target(action)
        if note is None:
            continue
        evidence, problem = _count_gated(action, _basis_of(note), lines)
        if evidence is None:
            plan.rejected[problem] += 1
            continue
        used.add(str(note["id"]))
        plan.renewed[str(note["id"])] = evidence

    guard = _ScriptGuard(
        [line.text for line in lines.values() if line.role == ROLE_REPLY], phrases, language,
    )
    gone = {str(item[0]["id"]) for item in plan.retired}
    staying = [note for note in notes if str(note["id"]) not in gone]
    live = Counter(str(note["kind"]) for note in staying)
    # Every note the model was shown, the ones it is retiring included: a note
    # retired and added back in one answer would come out with a fresh lifetime.
    live_keys = {_compact(str(note["avoid"])) for note in notes}
    conflicted = {
        _compact(str(item.get("avoid") or "")) for item in retired
        if item.get("outcome") == OUTCOME_CARD_CONFLICT
    }
    next_number = _next_note_number(notes)
    for action in by_verb[ACTION_ADD]:
        kind = action.get("kind")
        if kind == KIND_STYLE:
            basis = action.get("basis")
            if not isinstance(basis, str) or basis not in (BASIS_COMPLAINT, BASIS_REQUEST):
                plan.rejected[REJECT_INVALID] += 1
                continue
        elif kind == KIND_DRIFT:
            if not has_card:
                # The card is the whole baseline; without one there is nothing to drift from.
                plan.rejected[REJECT_NO_CARD] += 1
                continue
            basis = _BASIS_DRIFT
        else:
            plan.rejected[REJECT_INVALID] += 1
            continue
        avoid = _collapse(action.get("avoid"))
        direction = _collapse(action.get("direction"))
        problem = _check_note_text(avoid, direction, guard)
        if problem is not None:
            plan.rejected[problem] += 1
            continue
        evidence, problem = _count_gated(action, basis, lines)
        if evidence is None:
            plan.rejected[problem] += 1
            continue
        key = _compact(avoid)
        if key in live_keys:
            plan.rejected[REJECT_SAME_AS_LIVE] += 1
            continue
        if key in conflicted:
            plan.rejected[REJECT_CARD_CONFLICT_AGAIN] += 1
            continue
        if live[kind] >= MEMORY_BEHAVIOR_NOTE_MAX_PER_KIND[kind]:
            plan.rejected[REJECT_OVER_CAP] += 1
            continue
        live[kind] += 1
        live_keys.add(key)
        plan.added.append({
            "id": f"N{next_number}",
            "kind": kind,
            "basis": None if kind == KIND_DRIFT else basis,
            "avoid": avoid,
            "direction": direction,
            "created_at": float(now),
            "renewed_at": float(now),
            "expires_at": float(now) + MEMORY_BEHAVIOR_NOTE_TTL_SECONDS,
            "evidence": evidence,
        })
        next_number += 1
    return plan


def _basis_of(note: Mapping[str, Any]) -> str:
    if note.get("kind") == KIND_DRIFT:
        return _BASIS_DRIFT
    return BASIS_REQUEST if note.get("basis") == BASIS_REQUEST else BASIS_COMPLAINT


def _next_note_number(notes: Sequence[Mapping[str, Any]]) -> int:
    highest = 0
    for note in notes:
        match = re.fullmatch(r"N(\d{1,9})", str(note.get("id") or ""))
        if match:
            highest = max(highest, int(match.group(1)))
    return highest + 1


def build_payload(
    plan: NotePlan,
    *,
    notes: Sequence[Mapping[str, Any]],
    retired: Sequence[Mapping[str, Any]],
    language: str,
    now: float,
    source_end_at: float,
) -> dict[str, Any]:
    """The file content after a completed review."""
    dropped = {str(item[0]["id"]) for item in plan.retired}
    kept: list[dict] = []
    for note in notes:
        note_id = str(note["id"])
        if note_id in dropped:
            continue
        updated = dict(note)
        if note_id in plan.renewed:
            updated["renewed_at"] = float(now)
            updated["expires_at"] = float(now) + MEMORY_BEHAVIOR_NOTE_TTL_SECONDS
            updated["evidence"] = plan.renewed[note_id]
        kept.append(updated)
    newly_retired = [
        {
            "avoid": note["avoid"],
            "direction": note["direction"],
            "outcome": outcome,
            "at": float(now),
        }
        for note, outcome, _reason in plan.retired
    ]
    return {
        "schema_version": SCHEMA_VERSION,
        "language": language,
        "reviewed_at": float(now),
        "source_end_at": float(source_end_at),
        "notes": kept + plan.added,
        "retired": _recent_retired([*retired, *newly_retired], now=now)[:_RETIRED_MAX],
    }


# ── storage ─────────────────────────────────────────────────────────


def _normalize_note(raw: Any) -> Optional[Dict[str, Any]]:
    if not isinstance(raw, dict):
        return None
    kind = raw.get("kind")
    if not isinstance(kind, str) or kind not in MEMORY_BEHAVIOR_NOTE_MAX_PER_KIND:
        return None
    note_id = raw.get("id")
    avoid = _collapse(raw.get("avoid"))
    direction = _collapse(raw.get("direction"))
    created_at = _as_time(raw.get("created_at"))
    renewed_at = _as_time(raw.get("renewed_at"))
    expires_at = _as_time(raw.get("expires_at"))
    if (
        not isinstance(note_id, str)
        or not note_id.strip()
        or not avoid
        or not direction
        or created_at is None
        or renewed_at is None
        or expires_at is None
    ):
        return None
    basis = raw.get("basis")
    evidence = raw.get("evidence")
    return {
        "id": note_id.strip(),
        "kind": kind,
        "basis": basis if isinstance(basis, str) and basis in (BASIS_COMPLAINT, BASIS_REQUEST) else None,
        "avoid": avoid,
        "direction": direction,
        "created_at": created_at,
        "renewed_at": renewed_at,
        "expires_at": expires_at,
        "evidence": {
            str(key): _as_count(value) for key, value in evidence.items()
        } if isinstance(evidence, dict) else {},
    }


def _normalize_retired(raw: Any) -> Optional[Dict[str, Any]]:
    if not isinstance(raw, dict):
        return None
    avoid = _collapse(raw.get("avoid"))
    direction = _collapse(raw.get("direction"))
    outcome = raw.get("outcome")
    at = _as_time(raw.get("at"))
    if not avoid or not isinstance(outcome, str) or outcome not in _OUTCOMES or at is None:
        return None
    return {"avoid": avoid, "direction": direction, "outcome": outcome, "at": at}


def _normalize_payload(raw: Any) -> Optional[Dict[str, Any]]:
    """A readable payload, or None for an absent, foreign or damaged one. Never raises."""
    if not isinstance(raw, dict) or raw.get("schema_version") != SCHEMA_VERSION:
        return None
    language = raw.get("language")
    reviewed_at = _as_time(raw.get("reviewed_at"))
    source_end_at = _as_time(raw.get("source_end_at"))
    raw_notes = raw.get("notes")
    raw_retired = raw.get("retired", [])
    if (
        not isinstance(language, str)
        or not language
        or reviewed_at is None
        or source_end_at is None
        or not isinstance(raw_notes, list)
        or not isinstance(raw_retired, list)
    ):
        return None
    return {
        "schema_version": SCHEMA_VERSION,
        "language": language,
        "reviewed_at": reviewed_at,
        "source_end_at": source_end_at,
        "notes": [n for n in (_normalize_note(item) for item in raw_notes) if n is not None],
        "retired": [r for r in (_normalize_retired(item) for item in raw_retired) if r is not None],
    }


def _live_notes(notes: Iterable[Mapping[str, Any]], *, now: float) -> list[dict]:
    """Notes still in force, last renewed first. A rollback-stamped note is not trusted."""
    live = [
        dict(note) for note in notes
        if note["expires_at"] > now and note["renewed_at"] - now <= _FUTURE_SKEW_SECONDS
    ]
    live.sort(key=lambda note: note["renewed_at"], reverse=True)
    return live


def _recent_retired(retired: Iterable[Mapping[str, Any]], *, now: float) -> list[dict]:
    recent = [
        dict(item) for item in retired
        if 0 <= now - float(item["at"]) <= MEMORY_BEHAVIOR_NOTE_RETIRED_KEEP_SECONDS
    ]
    recent.sort(key=lambda item: item["at"], reverse=True)
    return recent


class BehaviorNoteStore:
    """Reads and writes ``memory/{name}/behavior_notes.json``.

    Usage:
        store = get_behavior_note_store()
        block = await store.aprompt_block(name, lang)
    """

    def __init__(self) -> None:
        self._config_manager = get_config_manager()

    def _file_path(self, name: str) -> str:
        """Path for READING; never creates the character directory."""
        return os.path.join(str(self._config_manager.memory_dir), name, BEHAVIOR_NOTES_FILENAME)

    def _write_file_path(self, name: str) -> Optional[str]:
        """Save target, or None while a rename / delete fences this name."""
        from memory import _is_within_memory_root, ensure_character_dir
        from utils.character_memory import is_character_write_fenced

        if is_character_write_fenced(name):
            return None
        memory_dir = str(self._config_manager.memory_dir)
        if not _is_within_memory_root(memory_dir, name, os.path.join(memory_dir, name)):
            return None
        return os.path.join(ensure_character_dir(memory_dir, name), BEHAVIOR_NOTES_FILENAME)

    def read(self, name: str) -> Optional[Dict[str, Any]]:
        """The stored payload, normalized; None when absent or unreadable."""
        if not name:
            return None
        path = self._file_path(name)
        with _file_lock(f"{name}/{BEHAVIOR_NOTES_FILENAME}"):
            if not os.path.exists(path):
                return None
            try:
                with open(path, encoding="utf-8") as f:
                    raw = json.load(f)
            except Exception as exc:  # A damaged file reads as absent.
                logger.warning(
                    "[BehaviorNotes] load failed for %s, treating as absent: %s",
                    name, type(exc).__name__,
                )
                return None
        return _normalize_payload(raw)

    async def aread(self, name: str) -> Optional[Dict[str, Any]]:
        return await asyncio.to_thread(self.read, name)

    def write(self, name: str, payload: Mapping[str, Any]) -> bool:
        """Replace the file; False when fenced or the write failed.

        Raises MaintenanceModeError while a cloud apply is replacing the
        character's memory, like every other memory writer.
        """
        from utils.cloudsave_runtime import cloudsave_writable_transaction

        with cloudsave_writable_transaction(
            self._config_manager,
            operation="save",
            target=f"memory/{name}/{BEHAVIOR_NOTES_FILENAME}",
        ):
            with _file_lock(f"{name}/{BEHAVIOR_NOTES_FILENAME}"):
                target = self._write_file_path(name)
                if target is None:
                    logger.info("[BehaviorNotes] %s: write fenced, update dropped", name)
                    return False
                try:
                    atomic_write_json(target, dict(payload), indent=2, ensure_ascii=False)
                except Exception as exc:
                    logger.warning(
                        "[BehaviorNotes] save failed for %s: %s", name, type(exc).__name__,
                    )
                    return False
        return True

    async def awrite(self, name: str, payload: Mapping[str, Any]) -> bool:
        return await asyncio.to_thread(self.write, name, payload)

    def prompt_block(self, name: str, lang: str, *, now: Optional[float] = None) -> str:
        """The block for a session start; "" when there is nothing to say.

        Nothing is computed here. A file written in another language than this
        session speaks yields nothing, and neither does a lapsed note. Whole notes
        drop, oldest renewal first, until the block fits the token budget.
        """
        payload = self.read(name)
        if payload is None or _short_language(payload["language"]) != _short_language(lang):
            return ""
        ts = float(now if now is not None else time.time())
        items = [
            render_behavior_note_item(_defang(note["avoid"]), _defang(note["direction"]), lang)
            for note in _live_notes(payload["notes"], now=ts)
        ]
        for keep in range(len(items), 0, -1):
            block = render_behavior_notes_block(items[:keep], name=name, lang=lang)
            if count_tokens(block) <= MEMORY_BEHAVIOR_NOTES_PROMPT_MAX_TOKENS:
                if keep < len(items):
                    logger.info(
                        "[BehaviorNotes] %s: %d note(s) left out (token budget %d)",
                        name, len(items) - keep, MEMORY_BEHAVIOR_NOTES_PROMPT_MAX_TOKENS,
                    )
                return block
        return ""

    async def aprompt_block(self, name: str, lang: str, *, now: Optional[float] = None) -> str:
        return await asyncio.to_thread(self.prompt_block, name, lang, now=now)


_store: Optional[BehaviorNoteStore] = None
_store_guard = threading.Lock()


def get_behavior_note_store() -> BehaviorNoteStore:
    global _store
    if _store is None:
        with _store_guard:
            if _store is None:
                _store = BehaviorNoteStore()
    return _store


# ── the prompt ──────────────────────────────────────────────────────


def _day_label(epoch: float) -> str:
    try:
        return datetime.fromtimestamp(epoch).strftime("%Y-%m-%d")
    except (OverflowError, OSError, ValueError):
        # A hand-edited time out of the platform's range costs only the label.
        return "?"


def _note_line(note: Mapping[str, Any], language: str) -> str:
    tags = [str(note["id"]), str(note["kind"])]
    if note.get("kind") == KIND_STYLE:
        tags.append(_basis_of(note))
    tags.append(_day_label(float(note["renewed_at"])))
    item = render_behavior_note_item(_defang(note["avoid"]), _defang(note["direction"]), language)
    return f"[{' '.join(tags)}] {item}"


def _retired_line(item: Mapping[str, Any], language: str) -> str:
    text = render_behavior_note_item(
        _defang(str(item["avoid"])), _defang(str(item.get("direction") or "")), language,
    )
    return f"[{item['outcome']} {_day_label(float(item['at']))}] {text}"


def build_review_prompt(
    *,
    ai_name: str,
    master_name: str,
    card_text: str,
    phrases: Sequence[str],
    signals: Sequence[str],
    notes: Sequence[Mapping[str, Any]],
    retired: Sequence[Mapping[str, Any]],
    sample_text: str,
    language: str,
) -> str:
    none = get_behavior_review_none(language)
    card = truncate_to_tokens(
        _defang((card_text or "").strip()), MEMORY_BEHAVIOR_REVIEW_CARD_MAX_TOKENS,
    )
    values = {
        "AI_NAME": ai_name,
        "MASTER_NAME": master_name,
        "CARD": card or none,
        "HABITUAL": "\n".join(f"- {_defang(phrase)}" for phrase in phrases) or none,
        "SIGNALS": "\n".join(signals) or none,
        "RETIRED": "\n".join(
            _retired_line(item, language) for item in retired[:MEMORY_BEHAVIOR_REVIEW_RETIRED_SHOWN]
        ) or none,
        "NOTES": "\n".join(_note_line(note, language) for note in notes) or none,
        "SAMPLE": sample_text or none,
        "MAX_STYLE": str(MEMORY_BEHAVIOR_NOTE_MAX_PER_KIND[KIND_STYLE]),
        "MAX_DRIFT": str(MEMORY_BEHAVIOR_NOTE_MAX_PER_KIND[KIND_DRIFT]),
    }
    # One pass, so a placeholder spelled inside the card or a reply is not
    # expanded in turn.
    return re.sub(
        r"\{(AI_NAME|MASTER_NAME|CARD|HABITUAL|SIGNALS|RETIRED|NOTES|SAMPLE|MAX_STYLE|MAX_DRIFT)\}",
        lambda match: values[match.group(1)],
        get_behavior_review_prompt(language),
    )


async def _acollect_user_signals(name: str, persona_manager, reflection_engine) -> list[str]:
    """Private impressions the user confirmed (+) or denied (-), latest signal first.

    Reference only, and never counted toward a gate. An ``engaged`` reply is not
    among them: it says nothing about what the user thinks. A failure costs only
    this section.
    """
    from memory.scopes import filter_entries_for_subjects

    try:
        persona = await persona_manager.aget_persona(name)
        view = persona_manager._persona_view_for_subjects(persona)
        entries: list[dict] = [
            entry
            for section in view.values() if isinstance(section, dict)
            for entry in section.get("facts") or () if isinstance(entry, dict)
        ]
        entries += filter_entries_for_subjects(await reflection_engine.aload_reflections(name))
    except Exception as exc:  # noqa: BLE001 — reference data only
        logger.debug("[BehaviorNotes] %s: user signals unavailable: %s", name, type(exc).__name__)
        return []
    ranked: list[tuple[str, str]] = []
    for entry in entries:
        if is_permanently_suppressed(entry) or entry.get("status") in ("merged", "archived"):
            continue
        text = _clean_line(str(entry.get("text") or ""))
        if not text:
            continue
        try:
            disputed = float(entry.get("disputation", 0) or 0) > 0
        except (TypeError, ValueError):
            disputed = False
        if disputed or entry.get("status") == "denied":
            mark = "-"
        elif _as_count(entry.get("user_confirm_count")) > 0:
            mark = "+"
        else:
            continue
        latest = max(
            str(entry.get("rein_last_signal_at") or ""),
            str(entry.get("disp_last_signal_at") or ""),
        )
        ranked.append((latest, f"{mark} {text}"))
    ranked.sort(key=lambda item: item[0], reverse=True)
    if not ranked:
        return []
    kept, _dropped = take_lines_within_token_budget(
        [line for _latest, line in ranked], MEMORY_BEHAVIOR_REVIEW_SIGNALS_MAX_TOKENS,
    )
    return kept


async def _aread_phrases(name: str, language: str) -> list[str]:
    try:
        return await get_habitual_phrase_store().aprompt_phrases(name, language)
    except Exception as exc:  # noqa: BLE001 — a read-only reference, not worth failing the run
        logger.debug("[BehaviorNotes] %s: avoid list unavailable: %s", name, type(exc).__name__)
        return []


async def _ainvoke(config_manager, prompt: str, *, name: str):
    """One model call: ``(actions or None, output_ran_out)``."""
    from config import MEMORY_INTROSPECTION_OUTPUT_CAPS, MEMORY_LLM_HARD_TIMEOUT_SECONDS
    from memory.thinking_llm import ainvoke_thinking, response_hit_output_limit
    from utils.token_tracker import set_call_type

    set_call_type("memory_behavior_review")
    # The same tier as introspection, thinking on: what comes out is read at
    # every session start, so a wrong note costs more than the call does.
    api_config = await config_manager.aget_model_api_config('correction')
    resp, cap = await ainvoke_thinking(  # noqa: LLM_INPUT_BUDGET  # build_review_prompt and build_review_sample bound every block by token budget.
        api_config, prompt,
        timeout=MEMORY_LLM_HARD_TIMEOUT_SECONDS,
        call_label=f"{name} memory_behavior_review",
        output_caps=MEMORY_INTROSPECTION_OUTPUT_CAPS,
    )
    actions = parse_actions(getattr(resp, 'content', '') or '')
    return actions, response_hit_output_limit(resp, cap)


# ── the run ─────────────────────────────────────────────────────────


@dataclass
class BehaviorReviewResult:
    # Why no model call happened, when none did.
    skipped: str = ''
    added: int = 0
    renewed: int = 0
    retired: int = 0
    rejected: Counter = field(default_factory=Counter)
    # None, FAILURE_TRANSIENT or FAILURE_FINAL; see classify_model_failure.
    failure: Optional[str] = None
    failure_reason: str = ''


def _print_plan(name: str, plan: NotePlan, language: str) -> None:
    for note in plan.added:
        item = render_behavior_note_item(note["avoid"], note["direction"], language)
        _print_text(f"[BehaviorNotes] {name} 新增 {note['id']}({note['kind']}): {item}")
    for note_id in plan.renewed:
        _print_text(f"[BehaviorNotes] {name} 延期 {note_id}")
    for note, outcome, reason in plan.retired:
        item = render_behavior_note_item(note["avoid"], note["direction"], language)
        _print_text(f"[BehaviorNotes] {name} 撤掉 {note['id']}({outcome}): {item}（{reason[:60]}）")


async def arun_behavior_review(
    name: str,
    *,
    messages: Sequence[ReviewMessage],
    language: str,
    config_manager,
    persona_manager,
    reflection_engine,
    store: Optional[BehaviorNoteStore] = None,
    now: Optional[float] = None,
) -> BehaviorReviewResult:
    """Review one character's recent talk and update her notes.

    ``messages`` are chronological, visible text only. Raises on a failed save,
    which the caller treats as a transient failure, like introspection.
    """
    store = store or get_behavior_note_store()
    ts = float(now if now is not None else time.time())
    result = BehaviorReviewResult()
    if not messages:
        result.skipped = SKIP_NO_HISTORY
        return result

    stored = await store.aread(name)
    # A file in another language is never injected, so its notes do not count.
    same_language = (
        stored is not None
        and _short_language(stored["language"]) == _short_language(language)
    )
    # Clamped: a row stamped ahead of the clock must not hide what comes after it.
    newest_at = min(max(message.at for message in messages).timestamp(), ts)
    if same_language and newest_at <= stored["source_end_at"]:
        result.skipped = SKIP_NO_NEW_CONVERSATION
        return result

    sitting_count = len(set(_sessions_of(
        sorted(messages, key=lambda item: item.at), HABITUAL_PHRASE_SESSION_GAP_SECONDS,
    )))
    reply_count = sum(1 for message in messages if message.role == ROLE_REPLY)
    if (
        sitting_count < MEMORY_BEHAVIOR_REVIEW_MIN_SESSIONS
        or reply_count < MEMORY_BEHAVIOR_REVIEW_MIN_REPLIES
    ):
        result.skipped = SKIP_TOO_FEW
        return result

    sample = await asyncio.to_thread(build_review_sample, messages, language=language)
    notes = _live_notes(stored["notes"], now=ts) if same_language else []
    retired = _recent_retired(stored["retired"], now=ts) if same_language else []
    character = await config_manager.aget_character_data()
    catgirl_data, name_mapping, prompt_map = character[3], character[4], character[5]
    card_text, _short_values = card_text_from_character(
        (catgirl_data or {}).get(name), (prompt_map or {}).get(name, ""),
    )
    phrases = await _aread_phrases(name, language)
    signals = await _acollect_user_signals(name, persona_manager, reflection_engine)
    prompt = build_review_prompt(
        ai_name=name,
        master_name=str((name_mapping or {}).get("human") or "User"),
        card_text=card_text,
        phrases=phrases,
        signals=signals,
        notes=notes,
        retired=retired,
        sample_text=sample.text,
        language=language,
    )
    try:
        raw_actions, ran_out = await _ainvoke(config_manager, prompt, name=name)
    except Exception as exc:  # noqa: BLE001 — classified; the caller schedules the retry
        result.failure = classify_model_failure(exc)
        result.failure_reason = type(exc).__name__
        logger.warning(
            f"[BehaviorNotes] {name}: 模型调用失败（{result.failure}）: {type(exc).__name__}"
        )
        return result
    if raw_actions is None:
        # Out of output, or not a JSON array: the same input would most likely
        # end the same way, so no early retry.
        result.failure = FAILURE_FINAL
        result.failure_reason = 'output_exhausted' if ran_out else 'unparseable'
        logger.warning(f"[BehaviorNotes] {name}: 没有可用的 JSON 数组（{result.failure_reason}）")
        return result

    plan = plan_note_actions(
        raw_actions,
        lines=sample.lines,
        notes=notes,
        retired=retired,
        phrases=phrases,
        has_card=bool(card_text.strip()),
        language=language,
        now=ts,
    )
    result.rejected.update(plan.rejected)
    payload = build_payload(
        plan,
        notes=notes,
        retired=retired,
        language=language,
        now=ts,
        source_end_at=newest_at,
    )
    if not await store.awrite(name, payload):
        # Fenced by a rename / delete, or the disk refused: nothing took effect.
        raise OSError("behavior notes not saved")
    result.added = len(plan.added)
    result.renewed = len(plan.renewed)
    result.retired = len(plan.retired)
    _print_plan(name, plan, language)
    logger.info(
        f"[BehaviorNotes] {name}: 样本 {len(sample.lines)} 行，"
        f"新增 {result.added}，延期 {result.renewed}，撤掉 {result.retired}，"
        f"现存 {len(payload['notes'])}，拒绝 {dict(plan.rejected)}"
    )
    return result
