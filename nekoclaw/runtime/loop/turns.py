# Ported from huggingface/tau (https://github.com/huggingface/tau),
# commit 0d256d5729b76ad5dae2fef453b04da9b8241208, file src/tau_agent/turns.py.
# Copyright (c) 2026 Alejandro AO. MIT License: see LICENSE in the loop package root.
# Local changes are listed in nekoclaw/runtime/loop/__init__.py.

"""Portable completed-turn context and host scheduling decisions."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Literal

from nekoclaw.runtime.loop.messages import AgentMessage, AssistantMessage, ToolResultMessage
from nekoclaw.runtime.loop.provider import CancellationToken


@dataclass(frozen=True, slots=True)
class AgentTurnContext:
    """Finalized turn with shallow transcript snapshots, before ``turn_end``."""

    message: AssistantMessage
    tool_results: tuple[ToolResultMessage, ...]
    messages: tuple[AgentMessage, ...]
    new_messages: tuple[AgentMessage, ...]


@dataclass(frozen=True, slots=True)
class AgentTurnDecision:
    """End before queue polling, or ensure one subsequent provider request."""

    action: Literal["end", "continue"]


FinishTurn = Callable[
    [AgentTurnContext, CancellationToken | None],
    AgentTurnDecision | None | Awaitable[AgentTurnDecision | None],
]
