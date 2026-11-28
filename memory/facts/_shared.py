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
"""Shared helpers, constants, and exceptions for memory.facts."""

from __future__ import annotations

import itertools
import json
import threading
from memory.scopes import subject_from_entry
from utils.language_utils import detect_prompt_language_with_ascii_fallback
from utils.logger_config import get_module_logger

from memory import facts as _facts_facade

logger = get_module_logger("memory.facts", "Memory")


# A pair nominated because both facts cite the same chat message, not because
# their wording overlaps. A real text match is always above zero.
SHARED_SOURCE_OVERLAP = 0.0


# ``extraction_path`` stamped on facts the AI-aware pass (path B) writes. Path
# B lets the model label each fact's source itself; the marker lets those
# labels be audited later against the conversation (who really said it).
EXTRACTION_PATH_B = "path_b"


def _detect_fact_extraction_prompt_language(
    text: str,
    *,
    ui_language: str | None = None,
) -> str:
    """Resolve Stage-1 prompt language without losing ASCII es/pt input."""
    return detect_prompt_language_with_ascii_fallback(
        text,
        ui_language=ui_language or _facts_facade.get_global_language_full(),
    )


_ARCHIVE_AGE_DAYS = 7          # Archive absorbed facts older than this many days.


_ARCHIVE_COOLDOWN_HOURS = 24   # Minimum interval between archive attempts.


def safe_importance(f: dict, default: int = 5) -> int:
    """Defensively coerce ``f['importance']`` to int.

    Normal entries pass through `_apersist_new_facts` where importance is
    clamped to 1..10, so this only matters for hand-edited facts.json or
    legacy data — but a malformed value here would otherwise raise
    ValueError inside a sort key and stall the entire drain loop for that
    character. Falls back to ``default`` on any failure.
    """
    try:
        val = f.get('importance', default)
        return int(val) if val else default
    except (ValueError, TypeError):
        return default


def safe_int_field(d: dict, key: str, default: int = 0) -> int:
    """Defensively coerce ``d[key]`` to int (Codex P2 on PR #1412).

    Liveness attempt counters (``refine_attempts`` / ``resolve_attempts`` /
    ``_attempt_count``) are all read from dict fields deserialized from JSON /
    ndjson; once a manual edit / legacy data / migration noise writes a dirty
    value like ``""`` / ``"unknown"`` / list / dict, the original
    ``int(d.get(key, 0) or 0)`` raises ValueError / TypeError and takes down the
    whole list comprehension (candidate gather) → that pass fails forever → the
    liveness fallback itself becomes a new liveness gap.

    Difference from ``safe_importance``: this helper treats ``0`` / ``"0"`` as
    legitimate and returns 0 (an attempt counter of 0 is a valid count) instead
    of falling back to the default. ``safe_importance`` mapping all falsy values
    to the default is importance-specific semantics.
    """
    try:
        val = d.get(key)
        if val is None:
            return default
        return int(val)
    except (ValueError, TypeError):
        return default


class FactExtractionFailed(RuntimeError):
    """Stage-1 LLM call exhausted retries (RFC §3.4.2, last paragraph).

    Distinct from "Stage-1 returned an empty list" — the latter is a
    successful zero-result run that should advance the signal-extraction
    cursor, while the former must leave the cursor untouched so the next
    idle cycle retries the same message window.
    """


def _readable_fact_id(entry: dict):
    """The fact's id when it can be used as a lookup key, else ``None``.

    Ids are written as strings (``fact_<timestamp>_<hash>``), but facts.json is
    a plain file users and older versions have edited: a row can arrive with no
    id at all, or with a list/dict where the id should be. Indexing those
    directly raises ``KeyError`` / ``TypeError: unhashable``, neither of which
    the read-merge's ``except (json.JSONDecodeError, OSError)`` catches, so one
    malformed row would abort every future save instead of being overwritten.

    Only genuinely unusable ids are dropped. A legacy scalar id such as
    ``12345`` still matches between cache and disk exactly as it did before, so
    excluding it would silently stop preserving that row's ``absorbed`` /
    ``signal_processed`` flags — trading the crash for quiet data loss.
    """
    fact_id = entry.get('id')
    # Exclude only absent IDs: None / missing / empty strings (empty IDs collide across all rows
    # lacking real IDs). Never use `not fact_id`: that drops legacy ID 0 too,
    # a fully usable key, trading crashes for silent marker loss again.
    if fact_id is None or fact_id == '':
        return None
    try:
        hash(fact_id)
    except TypeError:  # list / dict, etc., cannot enter a set.
        return None
    return fact_id


_TYPED_TRUST_FACT_ID_PREFIX = "__neko_typed_fact_id_v1__:"


def _speaker_trust_fact_id(fact_id: object) -> str:
    """Serialize a fact id without collapsing distinct scalar types.

    Normal string ids keep their historical wire representation.  Strings
    using our reserved prefix are escaped, while non-string legacy scalar ids
    are tagged with their type so ``1`` and ``"1"`` cannot address the same
    trust-signal row.
    """
    if isinstance(fact_id, str) and not fact_id.startswith(
        _TYPED_TRUST_FACT_ID_PREFIX
    ):
        return fact_id
    try:
        payload = json.dumps(
            fact_id, ensure_ascii=False, separators=(",", ":"),
            sort_keys=True, allow_nan=False,
        )
    except (TypeError, ValueError):
        payload = repr(fact_id)
    kind = "str" if isinstance(fact_id, str) else type(fact_id).__name__
    return f"{_TYPED_TRUST_FACT_ID_PREFIX}{kind}:{payload}"


def _speaker_trust_fact_identity(entry: dict) -> tuple[str, str, str, str] | None:
    """Return the full scoped identity used to attach a trust signal."""
    if not isinstance(entry, dict):
        return None
    fact_id = _readable_fact_id(entry)
    subject = subject_from_entry(entry)
    if fact_id is None or subject is None:
        return None
    return (
        _speaker_trust_fact_id(fact_id), subject.kind,
        subject.subject_id, subject.scope,
    )


def _fact_scoped_identity(entry: dict) -> tuple[str, str, str, str] | None:
    """Return an archive-safe identity without collapsing scoped duplicate ids."""
    if not isinstance(entry, dict):
        return None
    fact_id = _readable_fact_id(entry)
    if fact_id is None:
        return None
    subject = subject_from_entry(entry)
    typed_fact_id = _speaker_trust_fact_id(fact_id)
    if subject is not None:
        return typed_fact_id, subject.kind, subject.subject_id, subject.scope
    return (
        typed_fact_id,
        str(entry.get('subject_kind') or ''),
        str(entry.get('subject_id') or ''),
        str(entry.get('scope') or ''),
    )


# Markers of a demoted fact row. A row carrying either belongs in cold storage
# (memory/cold_store.py); one still in facts_archive.json was demoted before
# cold storage existed and moves there at the next archive window.
_COLD_MARKERS = ('arbitration_archived_at', 'subject_archived_at')


def _is_cold_row(entry: object) -> bool:
    return isinstance(entry, dict) and any(entry.get(marker) for marker in _COLD_MARKERS)


def _merge_archive_entries(existing: list, incoming: list) -> list[dict]:
    """Merge archive rows by full scoped identity; later occurrence wins.

    facts_archive.json is appended by a two-file commit (archive first, then
    facts.json — see ``_archive_absorbed``). An interrupted commit leaves a row
    in BOTH files, so the next archive pass re-appends it. Keying on
    ``_fact_scoped_identity`` makes that append idempotent without folding two
    subjects that happen to reuse the same fact id. Merging ``existing`` against
    itself also heals duplicates an earlier half-commit already wrote to disk.

    Later wins because ``incoming`` is the live copy being archived right now —
    it may carry flag updates the older archived copy predates. The one
    exception is an existing arbitration marker: an archive-first crash must
    not let a later subject archive turn that loser into a restorable row.

    Rows with an unusable id are all kept: there is no key to compare them on,
    and folding them together would trade a duplicate for silent data loss (the
    same call ``_readable_fact_id`` already makes).
    """
    out: list[dict] = []
    pos: dict = {}
    for entry in itertools.chain(existing, incoming):
        if not isinstance(entry, dict):
            continue
        identity = _fact_scoped_identity(entry)
        if identity is None:
            out.append(entry)
            continue
        if identity in pos:
            previous = out[pos[identity]]
            replacement = entry
            if (
                previous.get('arbitration_archived_at')
                and not entry.get('arbitration_archived_at')
            ):
                # Arbitration commits archive-first. If a crash leaves the
                # loser active, a later subject archive can re-append that
                # active copy. Keep newer live fields, but never erase the
                # marker that excludes it from ordinary subject restore.
                replacement = dict(entry)
                for key in (
                    'arbitration_archived_at', 'arbitration_reason',
                    'superseded_by',
                ):
                    if key in previous:
                        replacement[key] = previous[key]
            out[pos[identity]] = replacement
        else:
            pos[identity] = len(out)
            out.append(entry)
    return out


# Mutex for lazy recheck-mirror creation (see FactStore._recheck_mem). Module-level rather than
# instance-level because it protects the moment before an instance has its lock. Guards creation
# lines only, never subsequent reads/writes.
_RECHECK_MEM_BOOTSTRAP = threading.Lock()
