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
"""Asking the user to let her switch NekoPower on: the card, its answer, the refusal cooldown.

With the NekoPower master switch off she has the ``enable_nekopower`` tool
(``main_logic/core/tool_calling.py``). Calling it puts a NekoPower question card
in the chat: the same card a task agent asks with, so the compact chat and the
badge of the minimized ball show it as they show those. Its task id starts with
``TASK_ID_PREFIX``; the main server's ``/api/agent/questions`` and
``/api/agent/commissions/{id}/answer`` handle such ids here instead of passing
them to the agent process.

Rules (owner, 2026-11-27):
- Only a click on "no" counts as a refusal. Two in a row and she may not ask
  again for ``COOLDOWN_SECONDS``: the tool fails at once meanwhile. A click on
  "yes" starts the count again.
- A card nobody answers is withdrawn after ``CARD_SECONDS``; that is no refusal.
- Words typed instead of a click are no refusal either; she is told them.
- While a card waits, the tool fails at once instead of putting up another.

Kept in memory, per character: a restart forgets it all.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

from config.prompts.prompts_nekopower_enable import (
    ENABLE_CARD_QUESTION,
    ENABLE_CARD_TITLE,
    NEKOPOWER_NAME,
)
from config.prompts.prompts_sys import _loc

TASK_ID_PREFIX = "nekopower-enable:"
REFUSALS_TO_COOL_DOWN = 2
COOLDOWN_SECONDS = 5 * 60
CARD_SECONDS = 2 * 60
# How long the report waits for the session renewal (renew_session_now waits 60 s itself).
RENEWAL_WAIT_SECONDS = 90

# What a card's answer comes to.
ACCEPTED = "accepted"
DECLINED = "declined"
WORDS = "words"
STALE = "stale"

# What asking comes to.
ASKED = "asked"
WAITING = "waiting"
COOLING_DOWN = "cooling_down"


@dataclass
class _Card:
    question_id: str
    title: str
    question: Dict[str, Any]
    at: float


@dataclass
class _State:
    refusals: int = 0
    cooldown_until: float = 0.0
    card: Optional[_Card] = None


_states: Dict[str, _State] = {}


def _now(now: Optional[float]) -> float:
    return time.time() if now is None else float(now)


def _state(lanlan_name: str) -> _State:
    return _states.setdefault(lanlan_name or "", _State())


def task_id_for(lanlan_name: str) -> str:
    """The task id of a character's card."""
    return TASK_ID_PREFIX + (lanlan_name or "")


def is_task_id(task_id: Any) -> bool:
    return isinstance(task_id, str) and task_id.startswith(TASK_ID_PREFIX)


def lanlan_of(task_id: str) -> str:
    return task_id[len(TASK_ID_PREFIX):] if is_task_id(task_id) else ""


def cooling_down(lanlan_name: str, *, now: Optional[float] = None) -> bool:
    state = _state(lanlan_name)
    if state.cooldown_until and _now(now) >= state.cooldown_until:
        state.cooldown_until = 0.0
    return bool(state.cooldown_until)


def cooldown_minutes_left(lanlan_name: str, *, now: Optional[float] = None) -> int:
    """Whole minutes, rounded up, until she may ask again (0 when she may)."""
    if not cooling_down(lanlan_name, now=now):
        return 0
    left = _state(lanlan_name).cooldown_until - _now(now)
    return max(1, int(-(-left // 60)))


def waiting_card(lanlan_name: str) -> Optional[str]:
    """The question id of the card that waits for the user, if one does."""
    card = _state(lanlan_name).card
    return card.question_id if card else None


def ask(lanlan_name: str, *, lang: str, name: str, now: Optional[float] = None) -> Tuple[str, Optional[str]]:
    """Put up a card unless the rules say no: ``(ASKED, question_id)``, ``(WAITING, None)`` or ``(COOLING_DOWN, None)``."""
    if cooling_down(lanlan_name, now=now):
        return COOLING_DOWN, None
    state = _state(lanlan_name)
    if state.card is not None:
        return WAITING, None
    question_id = "enable-" + uuid.uuid4().hex[:12]
    state.card = _Card(
        question_id=question_id,
        title=_loc(ENABLE_CARD_TITLE, lang),
        question={
            "id": question_id,
            "kind": "confirmation",
            "question": _loc(ENABLE_CARD_QUESTION, lang).format(name=name or ""),
        },
        at=_now(now),
    )
    return ASKED, question_id


def answer(
    lanlan_name: str,
    question_id: str,
    *,
    choice: str = "",
    text: str = "",
    now: Optional[float] = None,
) -> str:
    """Take the user's answer to the card ``question_id``: ACCEPTED, DECLINED, WORDS or STALE.

    The id must be the card's own: an answer that names no card answers none.
    """
    state = _state(lanlan_name)
    if state.card is None or question_id != state.card.question_id:
        return STALE
    if choice == "yes":
        outcome = ACCEPTED
        state.refusals = 0
    elif choice == "no":
        outcome = DECLINED
        state.refusals += 1
        if state.refusals >= REFUSALS_TO_COOL_DOWN:
            state.cooldown_until = _now(now) + COOLDOWN_SECONDS
            state.refusals = 0
    elif (text or "").strip():
        outcome = WORDS
    else:
        return STALE
    state.card = None
    return outcome


def withdraw(lanlan_name: str, question_id: Optional[str] = None) -> Optional[str]:
    """Take the card down (it expired, or NekoPower came on otherwise); its question id, if one was up."""
    state = _state(lanlan_name)
    card = state.card
    if card is None or (question_id and question_id != card.question_id):
        return None
    state.card = None
    return card.question_id


def card_frame(lanlan_name: str) -> Optional[Dict[str, Any]]:
    """The ``nekopower_question`` frame that shows the waiting card on the page."""
    card = _state(lanlan_name).card
    if card is None:
        return None
    return {
        "type": "nekopower_question",
        "task_id": task_id_for(lanlan_name),
        "title": card.title,
        "route": None,
        "lanlan_name": lanlan_name,
        "question": dict(card.question),
        "question_id": card.question_id,
    }


def closed_frame(lanlan_name: str, question_id: str) -> Dict[str, Any]:
    """The frame that takes a card down on the page."""
    return {
        "type": "nekopower_question",
        "task_id": task_id_for(lanlan_name),
        "title": "",
        "route": None,
        "lanlan_name": lanlan_name,
        "question": None,
        "question_id": question_id,
    }


def waiting_rows() -> List[Dict[str, Any]]:
    """The cards that wait, as ``/api/agent/questions`` lists questions."""
    rows = []
    for lanlan_name, state in _states.items():
        if state.card is None:
            continue
        rows.append({
            "task_id": task_id_for(lanlan_name),
            "title": state.card.title,
            "lanlan_name": lanlan_name,
            "route": None,
            "question": dict(state.card.question),
        })
    return rows


def report(lanlan_name: str, text: str, *, lang: str) -> Dict[str, Any]:
    """A report she is to answer, as a proactive callback (``submit_proactive_callback``)."""
    return {
        "event": "agent_task_callback",
        "origin": "event",
        "task_id": task_id_for(lanlan_name),
        "channel": "nekopower_enable",
        "status": "completed",
        "success": True,
        "summary": text,
        "detail": "",
        "source_kind": "unknown",
        "source_name": _loc(NEKOPOWER_NAME, lang),
        "delivery_mode": "proactive",
        "coalesce_key": task_id_for(lanlan_name),
        "timestamp": "",
        "metadata": {"context_type": "nekopower_enable", "lang": lang},
        "context_type": "nekopower_enable",
    }


def reset_for_tests() -> None:
    _states.clear()
