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

"""Guards against a session giving up half way (design section 8.6).

* ``RepeatToolGuard`` notices the same tool call being repeated with the same
  arguments and produces a reminder on the 3rd, 5th and 8th call in a row. It
  only reminds; it never blocks. Logic and wording follow dsh
  ``packages/guard/repeat-tool-reminder``.
* ``next_goal_round`` decides whether a finished run is followed by another
  automatic round towards the session's goal, as dsh
  ``packages/goal/goal-round-driver`` does.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from config.prompts import prompts_nekoclaw as texts
from nekoclaw.runtime.tools.goal import GoalState


def canonical_arguments(arguments: Any) -> str:
    """Serialize arguments so that key order does not matter."""
    return json.dumps(arguments, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


class RepeatToolGuard:
    """Counts consecutive identical tool calls of one session."""

    def __init__(
        self,
        thresholds: Sequence[int] = texts.REPEAT_TOOL_THRESHOLDS,
        preview_chars: int = texts.REPEAT_TOOL_ARGUMENTS_PREVIEW_CHARS,
    ) -> None:
        if not thresholds or any(value < 2 for value in thresholds):
            raise ValueError("thresholds must be integers of at least 2")
        self._thresholds = sorted(set(thresholds))
        self._preview_chars = preview_chars
        self._key: str | None = None
        self._count = 0

    def observe(self, tool_name: str, arguments: Mapping[str, Any]) -> str | None:
        """Record one call; return a reminder when its run length hits a threshold."""
        canonical = canonical_arguments(arguments)
        key = json.dumps([tool_name, canonical], ensure_ascii=False)
        self._count = self._count + 1 if key == self._key else 1
        self._key = key
        if self._count not in self._thresholds:
            return None
        if self._count == self._thresholds[0]:
            return texts.REPEAT_TOOL_GENTLE_REMINDER
        preview = canonical
        if len(preview) > self._preview_chars:
            extra = len(canonical) - self._preview_chars
            preview = f"{canonical[: self._preview_chars]}… (+{extra} more chars)"
        return texts.repeat_tool_detailed_reminder(tool_name, self._count, preview)

    def reset(self) -> None:
        """Forget the chain: text from the user changes the context."""
        self._key = None
        self._count = 0


@dataclass(frozen=True, slots=True)
class GoalRound:
    """Identity of one automatic round, checked by ``update_goal``."""

    goal_id: str
    revision: int
    number: int


def next_goal_round(goals: GoalState) -> tuple[str, GoalRound] | None:
    """Return the prompt for the next automatic round, if the goal calls for one.

    A goal that used up its rounds is marked blocked with the code
    ``round-limit`` and gets no further round.
    """
    goal = goals.current
    if goal is None or goal.phase != "active" or goal.activation != "armed":
        return None
    if goal.rounds_started >= goal.max_goal_rounds:
        goals.block(goal, "round-limit", texts.goal_round_limit_message(goal.max_goal_rounds))
        return None
    number = goals.begin_round(goal)
    prompt = texts.goal_round_prompt(
        json.dumps(goal.objective, ensure_ascii=False), number, goal.max_goal_rounds
    )
    return prompt, GoalRound(goal.id, goal.revision, number)
