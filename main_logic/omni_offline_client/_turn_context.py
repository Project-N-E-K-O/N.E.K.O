# -- coding: utf-8 --
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

"""Passive turn context in the offline client's request view.

A typed reply may carry two extra user-role messages that the saved history
never holds (``config.turn_context_settings``):

* the **ephemeral** block (corpus material, memories) is this turn's
  ``_instruction``: every request of the turn carries it, the next turn's
  requests do not;
* the **resident** block (knowledge) is kept beside the history, tied to the
  user message it answered. Every later request of the session splices it
  back in right after that message, so the session's request prefix stays
  the same from one turn to the next. It goes when that message leaves the
  history (a wipe, a discarded turn) or the session is replaced.

A resident block becomes resident only once the provider answered the turn
that carried it; until then it belongs to that turn alone.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional, Sequence

from config.turn_context_settings import TURN_CONTEXT_RESIDENT_KNOWLEDGE_MAX_TOKENS

from ._shared import logger


# Internal metadata key marking a turn-context message. ``metadata`` never
# reaches a provider; the request view reads the mark to tell these synthetic
# user-role messages from what the user really said.
TURN_CONTEXT_MARK = "turn_context"


def is_turn_context_message(message) -> bool:
    metadata = getattr(message, "metadata", None)
    return isinstance(metadata, dict) and bool(metadata.get(TURN_CONTEXT_MARK))


@dataclass(frozen=True)
class TurnContext:
    """What one reply carries besides its history.

    ``resident`` stays in the session's requests after this turn (knowledge),
    ``ephemeral`` rides this turn's requests only (corpus, memories). Either
    may be ``None``. ``resident_ids`` name what ``resident`` holds, so later
    turns do not send it again while the model still sees it.
    """

    resident: Any = None
    resident_ids: tuple[str, ...] = ()
    resident_tokens: int = 0
    ephemeral: Any = None

    @property
    def empty(self) -> bool:
        return self.resident is None and self.ephemeral is None


@dataclass(eq=False)
class _ResidentEntry:
    anchor: Any
    message: Any
    ids: tuple[str, ...]
    tokens: int
    delivered: bool = False


class _TurnContextMixin:
    def _resident_entries(self) -> list:
        entries = getattr(self, "_resident_turn_context", None)
        if entries is None:
            entries = []
            self._resident_turn_context = entries
        return entries

    def _live_resident_entries(self) -> list:
        """Entries whose anchor is still in the history; the rest are dropped."""
        entries = self._resident_entries()
        if not entries:
            return entries
        present = {id(message) for message in self._conversation_history}
        live = [entry for entry in entries if id(entry.anchor) in present]
        if len(live) != len(entries):
            entries[:] = live
        return entries

    def resident_turn_context_ids(self) -> list[str]:
        """Ids of the resident blocks the session's requests still carry.

        Only delivered blocks count: one still waiting on its turn's provider
        answer may be dropped when a newer message supersedes that turn, and
        the newer turn must then be free to bring the same card in.
        """
        ids: list[str] = []
        for entry in self._live_resident_entries():
            if entry.delivered:
                ids.extend(entry.ids)
        return ids

    def _register_resident_context(self, anchor, context: Optional[TurnContext]):
        """Tie ``context.resident`` to ``anchor`` for this turn; None when it
        has none. It is spliced into this turn's requests at once and kept
        for later turns only by ``_settle_resident_context(delivered=True)``.
        """
        if context is None or context.resident is None or anchor is None:
            return None
        _mark(context.resident, "resident")
        entry = _ResidentEntry(
            anchor=anchor,
            message=context.resident,
            ids=tuple(context.resident_ids),
            tokens=max(0, int(context.resident_tokens or 0)),
        )
        self._resident_entries().append(entry)
        return entry

    def _settle_resident_context(self, entry, *, delivered: bool) -> None:
        """Keep ``entry`` for later turns, or drop it when its turn never
        reached the provider."""
        if entry is None:
            return
        entries = self._resident_entries()
        if not delivered:
            entries[:] = [item for item in entries if item is not entry]
            return
        if entry.delivered:
            return
        others = [
            item for item in self._live_resident_entries()
            if item.delivered and item is not entry
        ]
        carried = {card_id for item in others for card_id in item.ids}
        if entry.ids and set(entry.ids) <= carried:
            # Another turn's block with the same cards was delivered while
            # this one's lookup ran: one copy is enough.
            entries[:] = [item for item in entries if item is not entry]
            return
        if entry.ids:
            # An earlier block whose cards this one all carries again goes
            # (its prefix changes once); the newer one holds them all.
            covered = [item for item in others if item.ids and set(item.ids) <= set(entry.ids)]
            if covered:
                entries[:] = [item for item in entries if all(item is not drop for drop in covered)]
        entry.delivered = True
        self._trim_resident_context(keep=entry)

    def _trim_resident_context(self, *, keep=None) -> None:
        """Drop the oldest delivered blocks while the session's resident
        knowledge is over its budget. ``keep`` (the block just delivered) is
        never dropped, even alone over budget."""
        entries = self._live_resident_entries()
        total = sum(entry.tokens for entry in entries if entry.delivered)
        dropped = 0
        while total > TURN_CONTEXT_RESIDENT_KNOWLEDGE_MAX_TOKENS:
            victim = next(
                (entry for entry in entries if entry.delivered and entry is not keep),
                None,
            )
            if victim is None:
                break
            entries.remove(victim)
            total -= victim.tokens
            dropped += 1
        if dropped:
            logger.info(
                "OmniOfflineClient: resident turn context over budget, dropped %d oldest block(s)",
                dropped,
            )

    def _turn_context_message_ids(self, instruction=None) -> set[int]:
        """Identities of the synthetic context messages a request view holds."""
        ids = {id(entry.message) for entry in self._resident_entries()}
        if instruction is not None and is_turn_context_message(instruction):
            ids.add(id(instruction))
        return ids

    def _rebind_resident_anchor(self, old, new) -> None:
        """A history message was replaced by a copy: its blocks follow it."""
        if old is new:
            return
        for entry in self._resident_entries():
            if entry.anchor is old:
                entry.anchor = new

    def _with_resident_context(self, messages: Sequence) -> Sequence:
        """``messages`` with each resident block right after its anchor;
        ``messages`` itself when there is none to splice."""
        entries = self._resident_entries()
        if not entries:
            return messages
        by_anchor: dict[int, list] = {}
        for entry in entries:
            by_anchor.setdefault(id(entry.anchor), []).append(entry.message)
        if not any(id(message) in by_anchor for message in messages):
            return messages
        spliced: list = []
        for message in messages:
            spliced.append(message)
            spliced.extend(by_anchor.get(id(message), ()))
        return spliced


def _mark(message, kind: str) -> None:
    metadata = getattr(message, "metadata", None)
    if isinstance(metadata, dict):
        metadata.setdefault(TURN_CONTEXT_MARK, kind)
