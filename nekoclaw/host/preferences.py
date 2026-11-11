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

"""Task preferences: lasting instructions about how things get done.

Kept in the ``preferences`` table of the ledger file, apart from the
commission log (design section 5.1). What is recorded: which capability to use
for a kind of task, default arguments, how to report, what must be asked first.

Who writes, and when it takes effect:

* something she states through a dispatch (``state``) applies at once;
* something inferred from being stopped or corrected (``observe``) applies
  once the same tendency was seen ``PREFERENCE_INFERRED_THRESHOLD`` times and
  is marked as inferred. ``feedback`` a familiar files with ``note_lesson`` is
  handled this way too;
* the console edits entries directly (``edit``, ``set_active``, ``remove``).

Who reads: the fine selection and a familiar's instructions get the relevant
entries through ``render``, at most ``PREFERENCE_PROMPT_MAX_TOKENS`` tokens.

A preference is guidance for a model. It is never consulted when a permission
is decided, and it neither reads nor writes persona memory.
"""

from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass
from typing import Any

from nekoclaw.host.ledger import Ledger
from nekoclaw.host.selection import similarity

KINDS: tuple[str, ...] = ("capability", "default", "report", "ask_first", "other")
# Kinds that concern every task, whatever it is about.
_GENERAL_KINDS = frozenset({"report", "ask_first"})
ORIGINS: tuple[str, ...] = ("stated", "inferred", "console")
INFERRED_MARK = "(inferred)"
_SAME_STATEMENT_SIMILARITY = 0.9


@dataclass(frozen=True, slots=True)
class Preference:
    id: int
    kind: str
    text: str
    # "stated", "inferred" or "console".
    origin: str
    # How often an inferred tendency was seen.
    observations: int
    active: bool
    lanlan_name: str | None
    source_commission: str | None
    created_at: float
    updated_at: float


def _preference(row: sqlite3.Row) -> Preference:
    return Preference(
        id=row["id"],
        kind=row["kind"],
        text=row["text"],
        origin=row["origin"],
        observations=row["observations"],
        active=bool(row["active"]),
        lanlan_name=row["lanlan_name"],
        source_commission=row["source_commission"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def _clean(text: str) -> str:
    from config.nekoclaw_settings import PREFERENCE_TEXT_MAX_TOKENS
    from utils.tokenize import truncate_to_tokens

    collapsed = " ".join(str(text).split())
    return truncate_to_tokens(collapsed, PREFERENCE_TEXT_MAX_TOKENS)


def _kind(kind: str) -> str:
    return kind if kind in KINDS else "other"


_KIND_ORDER: tuple[str, ...] = ("ask_first", "report", "capability", "default")
_kind_patterns: dict[str, list[re.Pattern[str]]] | None = None


def preference_kind(text: str) -> str:
    """Return which kind a stated preference is, from its words alone.

    The word lists are ``PREFERENCE_KIND_PATTERNS`` (eight languages); no
    model is asked. A sentence none of them recognises is ``other``.
    """
    global _kind_patterns
    if _kind_patterns is None:
        from config.prompts.prompts_nekoclaw_brain import PREFERENCE_KIND_PATTERNS

        _kind_patterns = {
            kind: [re.compile(table[kind], re.IGNORECASE) for table in PREFERENCE_KIND_PATTERNS.values()]
            for kind in _KIND_ORDER
        }
    for kind in _KIND_ORDER:
        if any(pattern.search(text) for pattern in _kind_patterns[kind]):
            return kind
    return "other"


class PreferenceStore:
    """Reads and writes task preferences through the ledger's connection."""

    def __init__(self, ledger: Ledger) -> None:
        self._ledger = ledger

    # -- reading ----------------------------------------------------------

    @staticmethod
    def _all(conn: sqlite3.Connection, *, active_only: bool = False) -> list[Preference]:
        sql = "SELECT * FROM preferences"
        if active_only:
            sql += " WHERE active = 1"
        sql += " ORDER BY updated_at DESC, id DESC"
        return [_preference(row) for row in conn.execute(sql).fetchall()]

    async def list(self, *, active_only: bool = False) -> list[Preference]:
        """Return the preferences, most recently changed first."""
        return await self._ledger.run(lambda conn: self._all(conn, active_only=active_only))

    async def get(self, preference_id: int) -> Preference | None:
        def work(conn: sqlite3.Connection) -> Preference | None:
            row = conn.execute("SELECT * FROM preferences WHERE id = ?", (preference_id,)).fetchone()
            return _preference(row) if row is not None else None

        return await self._ledger.run(work)

    # -- writing ----------------------------------------------------------

    def _now(self) -> float:
        return self._ledger._clock()

    def _insert(
        self,
        conn: sqlite3.Connection,
        *,
        kind: str,
        text: str,
        origin: str,
        active: bool,
        lanlan_name: str | None,
        source_commission: str | None,
    ) -> Preference:
        now = self._now()
        cursor = conn.execute(
            "INSERT INTO preferences (kind, text, origin, observations, active, lanlan_name, "
            "source_commission, created_at, updated_at) VALUES (?, ?, ?, 1, ?, ?, ?, ?, ?)",
            (kind, text, origin, int(active), lanlan_name, source_commission, now, now),
        )
        row = conn.execute("SELECT * FROM preferences WHERE id = ?", (cursor.lastrowid,)).fetchone()
        return _preference(row)

    def _fetch(self, conn: sqlite3.Connection, preference_id: int) -> Preference:
        return _preference(conn.execute("SELECT * FROM preferences WHERE id = ?", (preference_id,)).fetchone())

    async def state(
        self,
        text: str,
        *,
        kind: str = "other",
        lanlan_name: str | None = None,
        source_commission: str | None = None,
        origin: str = "stated",
    ) -> Preference | None:
        """Record an explicit instruction. It applies from now on.

        Stating what is already on record refreshes that entry instead of
        adding a second one; an inferred entry about the same thing becomes a
        stated one.
        """
        cleaned = _clean(text)
        if not cleaned:
            return None
        if origin not in ("stated", "console"):
            raise ValueError("state() records stated or console entries only")
        from config.nekoclaw_settings import PREFERENCE_SAME_TENDENCY_SIMILARITY

        def work(conn: sqlite3.Connection) -> Preference:
            for existing in self._all(conn):
                score = similarity(existing.text, cleaned)
                same_statement = score >= _SAME_STATEMENT_SIMILARITY
                same_tendency = existing.origin == "inferred" and score >= PREFERENCE_SAME_TENDENCY_SIMILARITY
                if same_statement or same_tendency:
                    conn.execute(
                        "UPDATE preferences SET text = ?, kind = ?, origin = ?, active = 1, updated_at = ? WHERE id = ?",
                        (cleaned, _kind(kind), origin, self._now(), existing.id),
                    )
                    return self._fetch(conn, existing.id)
            return self._insert(
                conn,
                kind=_kind(kind),
                text=cleaned,
                origin=origin,
                active=True,
                lanlan_name=lanlan_name,
                source_commission=source_commission,
            )

        return await self._ledger.run(work)

    async def observe(
        self,
        text: str,
        *,
        kind: str = "other",
        lanlan_name: str | None = None,
        source_commission: str | None = None,
    ) -> Preference | None:
        """Record one observation of an inferred tendency.

        The first observation is stored but has no effect. When the same
        tendency is observed again the entry takes effect; it stays marked as
        inferred. An observation that matches a stated entry, or one set by
        hand on the console, changes nothing.
        """
        cleaned = _clean(text)
        if not cleaned:
            return None
        from config.nekoclaw_settings import (
            PREFERENCE_INFERRED_THRESHOLD,
            PREFERENCE_SAME_TENDENCY_SIMILARITY,
        )

        def work(conn: sqlite3.Connection) -> Preference:
            best: Preference | None = None
            best_score = 0.0
            for existing in self._all(conn):
                score = similarity(existing.text, cleaned)
                if score >= PREFERENCE_SAME_TENDENCY_SIMILARITY and score > best_score:
                    best, best_score = existing, score
            if best is None:
                return self._insert(
                    conn,
                    kind=_kind(kind),
                    text=cleaned,
                    origin="inferred",
                    active=PREFERENCE_INFERRED_THRESHOLD <= 1,
                    lanlan_name=lanlan_name,
                    source_commission=source_commission,
                )
            if best.origin != "inferred":
                # Already said in so many words; an inference adds nothing.
                return best
            observations = best.observations + 1
            conn.execute(
                "UPDATE preferences SET observations = ?, active = ?, updated_at = ? WHERE id = ?",
                (observations, int(best.active or observations >= PREFERENCE_INFERRED_THRESHOLD), self._now(), best.id),
            )
            return self._fetch(conn, best.id)

        return await self._ledger.run(work)

    async def edit(self, preference_id: int, *, text: str | None = None, kind: str | None = None) -> Preference | None:
        """Change an entry from the console. The entry then counts as set by hand."""

        def work(conn: sqlite3.Connection) -> Preference | None:
            row = conn.execute("SELECT * FROM preferences WHERE id = ?", (preference_id,)).fetchone()
            if row is None:
                return None
            current = _preference(row)
            new_text = _clean(text) if text is not None else current.text
            conn.execute(
                "UPDATE preferences SET text = ?, kind = ?, origin = 'console', updated_at = ? WHERE id = ?",
                (new_text or current.text, _kind(kind) if kind is not None else current.kind, self._now(), preference_id),
            )
            return self._fetch(conn, preference_id)

        return await self._ledger.run(work)

    async def set_active(self, preference_id: int, active: bool) -> Preference | None:
        """Switch an entry on or off from the console.

        An inferred entry then counts as set by hand, so a later observation
        of the same tendency no longer switches it back; stating it in so
        many words still does.
        """

        def work(conn: sqlite3.Connection) -> Preference | None:
            conn.execute(
                "UPDATE preferences SET active = ?, updated_at = ?, "
                "origin = CASE WHEN origin = 'inferred' THEN 'console' ELSE origin END WHERE id = ?",
                (int(active), self._now(), preference_id),
            )
            row = conn.execute("SELECT * FROM preferences WHERE id = ?", (preference_id,)).fetchone()
            return _preference(row) if row is not None else None

        return await self._ledger.run(work)

    async def remove(self, preference_id: int) -> bool:
        def work(conn: sqlite3.Connection) -> bool:
            return conn.execute("DELETE FROM preferences WHERE id = ?", (preference_id,)).rowcount > 0

        return await self._ledger.run(work)

    # -- for prompts ------------------------------------------------------

    async def render(self, about: str, *, max_tokens: int | None = None) -> str:
        """Return the entries that bear on ``about``, as lines for a prompt.

        Entries about reporting and about what to ask first always qualify;
        the others need words in common with ``about``. The most relevant come
        first, and the text never exceeds ``max_tokens``
        (``PREFERENCE_PROMPT_MAX_TOKENS`` by default); an entry that does not
        fit whole is left out.
        """
        from config.nekoclaw_settings import PREFERENCE_PROMPT_MAX_TOKENS
        from utils.tokenize import count_tokens, truncate_to_tokens

        budget = PREFERENCE_PROMPT_MAX_TOKENS if max_tokens is None else max_tokens
        if budget <= 0:
            return ""
        ranked: list[tuple[float, Preference]] = []
        for entry in await self.list(active_only=True):
            score = similarity(entry.text, about)
            if entry.kind in _GENERAL_KINDS:
                score += 1.0
            if score > 0:
                ranked.append((score, entry))
        ranked.sort(key=lambda item: (item[0], item[1].updated_at), reverse=True)

        lines: list[str] = []
        used = 0
        for _score, entry in ranked:
            mark = f" {INFERRED_MARK}" if entry.origin == "inferred" else ""
            line = f"- {entry.text}{mark}"
            cost = count_tokens(line) + (1 if lines else 0)
            if used + cost > budget:
                continue
            lines.append(line)
            used += cost
        # The per-line count is an estimate of the joined text; this holds the bound.
        return truncate_to_tokens("\n".join(lines), budget)


def describe_stop(title: str, target: str) -> str:
    """Return the inferred-tendency sentence for a commission that was stopped."""
    return f"The user stopped a task done with {target}: {title}"


def feedback_text(payload: dict[str, Any]) -> str:
    """Return the preference text for ``feedback`` filed through ``note_lesson``."""
    title = str(payload.get("title", "")).strip()
    content = str(payload.get("content", "")).strip()
    if title and content and title.lower() not in content.lower():
        return f"{title}: {content}"
    return content or title
