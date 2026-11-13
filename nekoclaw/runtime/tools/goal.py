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

"""Goals and the ``create_goal`` / ``get_goal`` / ``update_goal`` tools.

A goal keeps a session working: when a run ends and the goal is still active,
the session starts another round by itself (see ``guards.GoalRoundDriver``).
Rules and wording follow dsh ``packages/goal``:

* ``create``, ``edit``, ``pause`` and ``resume`` need a run the user gave text
  to, on a top-level session.
* ``complete`` and ``blocked`` are also allowed inside the goal's own automatic
  round; ``blocked`` only from the third consecutive round on.
* A goal loaded from a saved transcript, or whose run was aborted, is
  *disarmed*: it stays active but starts no round until it is resumed.

One difference: dsh lets only the user resume a paused goal, through its own
interface. The driver interface here has no such command, so ``resume`` from a
run the user gave text to is accepted for a paused goal as well.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Literal

from config.prompts.prompts_nekoclaw import (
    GOAL_BLOCKED_AFTER_ROUNDS,
    goal_blocked_wrapup,
    goal_complete_wrapup,
)
from nekoclaw.runtime.loop.tools import AgentTool
from nekoclaw.runtime.tools.base import ToolCallInfo, ToolContext, ToolError, define_tool

GoalPhase = Literal["active", "paused", "blocked", "complete"]
GoalActivation = Literal["armed", "disarmed"]

GOAL_NAMESPACE = "nekoclaw.goal"


@dataclass(slots=True)
class Goal:
    id: str
    revision: int
    objective: str
    phase: GoalPhase
    rounds_started: int
    max_goal_rounds: int
    activation: GoalActivation
    blocked_code: str | None = None
    blocked_message: str | None = None

    def to_json(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "revision": self.revision,
            "objective": self.objective,
            "phase": self.phase,
            "rounds_started": self.rounds_started,
            "max_goal_rounds": self.max_goal_rounds,
            "activation": self.activation,
            "blocked_code": self.blocked_code,
            "blocked_message": self.blocked_message,
        }


class GoalState:
    """The goal of one session, if it has one."""

    def __init__(
        self,
        *,
        default_rounds: int,
        max_rounds: int,
        on_change: Callable[[], None] | None = None,
    ) -> None:
        self.current: Goal | None = None
        self._default_rounds = default_rounds
        self._max_rounds = max_rounds
        self._on_change = on_change

    # -- persistence ----------------------------------------------------------

    def to_json(self) -> dict[str, Any]:
        return {"goal": self.current.to_json() if self.current is not None else None}

    def restore(self, data: object) -> None:
        """Load a saved goal. It comes back disarmed, as after a session resume."""
        payload = data.get("goal") if isinstance(data, dict) else None
        if not isinstance(payload, dict):
            self.current = None
            return
        try:
            self.current = Goal(
                id=str(payload["id"]),
                revision=int(payload["revision"]),
                objective=str(payload["objective"]),
                phase=payload["phase"],
                rounds_started=int(payload["rounds_started"]),
                max_goal_rounds=int(payload["max_goal_rounds"]),
                activation="disarmed",
                blocked_code=payload.get("blocked_code"),
                blocked_message=payload.get("blocked_message"),
            )
        except (KeyError, TypeError, ValueError):
            self.current = None

    def _changed(self) -> None:
        if self._on_change is not None:
            self._on_change()

    # -- operations -----------------------------------------------------------

    def _rounds(self, requested: object) -> int:
        if requested is None:
            return min(self._default_rounds, self._max_rounds)
        if isinstance(requested, bool) or not isinstance(requested, (int, float)):
            raise ToolError("max_goal_rounds must be a positive integer")
        if float(requested) != int(requested) or int(requested) < 1:
            raise ToolError("max_goal_rounds must be a positive integer")
        if int(requested) > self._max_rounds:
            raise ToolError(f"max_goal_rounds must be at most {self._max_rounds}")
        return int(requested)

    def create(self, objective: str, max_rounds: object = None) -> Goal:
        if not objective.strip():
            raise ToolError("objective must be a non-empty string")
        current = self.current
        if current is not None and current.phase in ("active", "paused"):
            raise ToolError(
                f'goal "{current.id}" is still {current.phase}; complete it or edit it instead of '
                "creating another"
            )
        self.current = Goal(
            id=f"goal-{uuid.uuid4().hex[:12]}",
            revision=1,
            objective=objective,
            phase="active",
            rounds_started=0,
            max_goal_rounds=self._rounds(max_rounds),
            activation="armed",
        )
        self._changed()
        return self.current

    def require(self, goal_id: str, revision: object) -> Goal:
        """Return the current goal if ``goal_id`` and ``revision`` name it exactly."""
        if (
            not goal_id
            or goal_id != goal_id.strip()
            or isinstance(revision, bool)
            or not isinstance(revision, (int, float))
            or float(revision) != int(revision)
            or int(revision) < 1
        ):
            raise ToolError("goal_id must be non-empty and revision must be a positive safe integer")
        current = self.current
        if current is None:
            raise ToolError("there is no current goal")
        if current.id != goal_id or current.revision != int(revision):
            raise ToolError(
                "goal_id and revision do not match the current goal; call get_goal and retry with "
                "the values it returns"
            )
        return current

    def _bump(self, goal: Goal) -> Goal:
        goal.revision += 1
        self._changed()
        return goal

    def edit(self, goal: Goal, objective: str | None, max_rounds: object) -> Goal:
        if objective is None and max_rounds is None:
            raise ToolError("goal edit requires objective and/or max_goal_rounds")
        # Checked before anything is changed: a rejected edit leaves the goal as
        # it was, in memory as well as in what was saved.
        rounds = self._rounds(max_rounds) if max_rounds is not None else None
        if objective is not None:
            goal.objective = objective
        if rounds is not None:
            goal.max_goal_rounds = rounds
        return self._bump(goal)

    def pause(self, goal: Goal) -> Goal:
        if goal.phase != "active":
            raise ToolError(f"only an active goal can be paused; this goal is {goal.phase}")
        goal.phase = "paused"
        goal.activation = "disarmed"
        return self._bump(goal)

    def resume(self, goal: Goal) -> Goal:
        if goal.phase not in ("active", "paused"):
            raise ToolError(f"a {goal.phase} goal cannot be resumed; create a new goal instead")
        if goal.rounds_started >= goal.max_goal_rounds:
            raise ToolError(
                f'goal "{goal.id}" exhausted {goal.max_goal_rounds} goal rounds; increase '
                "max_goal_rounds before resuming"
            )
        goal.phase = "active"
        goal.activation = "armed"
        return self._bump(goal)

    def complete(self, goal: Goal) -> Goal:
        if goal.phase not in ("active", "paused"):
            raise ToolError(f"this goal is already {goal.phase}")
        goal.phase = "complete"
        goal.activation = "disarmed"
        return self._bump(goal)

    def block(self, goal: Goal, code: str, message: str) -> Goal:
        if goal.phase not in ("active", "paused"):
            raise ToolError(f"this goal is already {goal.phase}")
        goal.phase = "blocked"
        goal.activation = "disarmed"
        goal.blocked_code = code
        goal.blocked_message = message
        return self._bump(goal)

    def disarm(self) -> None:
        """Stop automatic rounds without changing the goal's phase."""
        goal = self.current
        if goal is not None and goal.activation == "armed":
            goal.activation = "disarmed"
            self._changed()

    def begin_round(self, goal: Goal) -> int:
        """Count one automatic round and return its number."""
        goal.rounds_started += 1
        self._changed()
        return goal.rounds_started

    # -- model-facing view ----------------------------------------------------

    def view(self) -> dict[str, Any]:
        goal = self.current
        if goal is None:
            return {"goal": None}
        body: dict[str, Any] = {
            "id": goal.id,
            "revision": goal.revision,
            "objective": goal.objective,
            "phase": goal.phase,
            "roundsStarted": goal.rounds_started,
            "maxGoalRounds": goal.max_goal_rounds,
        }
        if goal.blocked_code is not None and goal.blocked_message is not None:
            body["blockedReason"] = {"code": goal.blocked_code, "message": goal.blocked_message}
        return {"goal": body, "activation": goal.activation}


def _render(state: GoalState) -> str:
    return json.dumps(state.view(), ensure_ascii=False, separators=(",", ":"))


def build_goal_tools(ctx: ToolContext, schemas: dict[str, dict[str, Any]]) -> list[AgentTool]:
    state = ctx.goals
    inbox = ctx.inbox

    def direct_human() -> bool:
        return ctx.top_level and inbox.in_direct_human_turn()

    def require_direct_human() -> None:
        if not direct_human():
            raise ToolError("this goal operation requires a direct human turn on a top-level agent")

    async def get_goal(args: dict[str, Any], call: ToolCallInfo) -> str:
        del args, call
        return _render(state)

    async def create_goal(args: dict[str, Any], call: ToolCallInfo) -> str:
        del call
        require_direct_human()
        state.create(str(args["objective"]), args.get("max_goal_rounds"))
        return _render(state)

    async def update_goal(args: dict[str, Any], call: ToolCallInfo) -> str:
        del call
        goal = state.require(str(args["goal_id"]), args["revision"])
        action = str(args["action"])
        if action == "edit":
            require_direct_human()
            objective = args.get("objective") or None
            rounds = args.get("max_goal_rounds") or None
            state.edit(goal, str(objective) if objective is not None else None, rounds)
            return _render(state)
        if action == "pause":
            require_direct_human()
            state.pause(goal)
            return _render(state)
        if action == "resume":
            require_direct_human()
            state.resume(goal)
            return _render(state)

        in_round = False
        if not direct_human():
            in_round = inbox.in_goal_round(goal.id, goal.revision, goal.rounds_started)
            if not in_round:
                raise ToolError(
                    "complete and blocked require a direct human turn or the current goal round"
                )
        reason = str(args.get("blocked_reason") or "").strip()
        if action == "blocked":
            if not reason:
                raise ToolError("blocked_reason is required with action blocked")
            if in_round and goal.rounds_started < GOAL_BLOCKED_AFTER_ROUNDS:
                raise ToolError(
                    f"blocked requires at least {GOAL_BLOCKED_AFTER_ROUNDS} consecutive goal "
                    f"rounds; current round is {goal.rounds_started}"
                )
        objective_json = json.dumps(goal.objective, ensure_ascii=False)
        if action == "complete":
            state.complete(goal)
            wrapup = goal_complete_wrapup(objective_json)
        else:
            state.block(goal, "model-reported", reason)
            wrapup = goal_blocked_wrapup(objective_json, json.dumps(reason, ensure_ascii=False))
        if in_round:
            inbox.deliver_notice("goal", wrapup)
        return _render(state)

    return [
        define_tool(schemas["create_goal"], create_goal),
        define_tool(schemas["get_goal"], get_goal),
        define_tool(schemas["update_goal"], update_goal),
    ]
