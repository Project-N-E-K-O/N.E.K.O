# Ported from huggingface/tau (https://github.com/huggingface/tau),
# commit 0d256d5729b76ad5dae2fef453b04da9b8241208, file src/tau_agent/__init__.py.
# Copyright (c) 2026 Alejandro AO. MIT License: see LICENSE in the loop package root.
# Local changes are listed below.

"""Portable Pi-compatible agent harness primitives, ported from Tau.

This package is the ``tau_agent`` core loop of huggingface/tau at commit
``0d256d5`` (release 0.4.10), kept in this repository and maintained here. It
is not synchronized with upstream; an upstream fix is taken by porting the
relevant commit by hand.

Changes from upstream:

* Python 3.11: the eleven PEP 695 ``type`` statements are plain assignments,
  and the recursive ``JSONValue`` is a ``typing_extensions.TypeAliasType``.
  Imports name ``nekoclaw.runtime.loop`` instead of ``tau_agent``.
* Live tool progress (``loop.py``): a tool's ``on_update`` calls are emitted as
  ``ToolExecutionUpdateEvent`` while the tool is still running. Upstream
  collected them and emitted them after the tool returned.
* Parallel read-only tools (``loop.py``, ``tools.py``): adjacent calls to tools
  declared ``execution_mode="parallel"`` run together, at most
  ``MAX_PARALLEL_TOOL_CALLS`` at a time; result messages still enter the
  transcript in call order. ``AgentTool.execution_mode`` now defaults to
  ``"sequential"`` so that running in parallel is opt-in.
* ``session/storage.py`` carries two ``noqa: ASYNC_BLOCK`` comments for this
  repository's blocking-call guard. Its coroutines do file work without
  suspending; the runtime drives them in a worker thread.

Everything else keeps upstream behavior: ``prompt`` / ``steer`` /
``follow_up`` / ``cancel``, the tool hooks, ``finish_turn`` and the repair of
interrupted tool calls.
"""

# ruff: noqa: F401 - this module intentionally defines the public facade

from nekoclaw.runtime.loop.events import (
    AgentEndEvent,
    AgentEvent,
    AgentStartEvent,
    MessageEndEvent,
    MessageStartEvent,
    MessageUpdateEvent,
    ToolExecutionEndEvent,
    ToolExecutionStartEvent,
    ToolExecutionUpdateEvent,
    TurnEndEvent,
    TurnStartEvent,
)
from nekoclaw.runtime.loop.harness import (
    AgentHarness,
    AgentHarnessConfig,
    EventListener,
    QueuedMessages,
    SimpleCancellationToken,
)
from nekoclaw.runtime.loop.loop import run_agent_loop
from nekoclaw.runtime.loop.messages import (
    AgentMessage,
    AssistantMessage,
    BashExecutionMessage,
    BranchSummaryMessage,
    CompactionSummaryMessage,
    CustomMessage,
    ImageContent,
    ResponseTiming,
    TextContent,
    ThinkingContent,
    ToolCall,
    ToolResultMessage,
    Usage,
    UsageCost,
    UserMessage,
    content_text,
    message_text,
)
from nekoclaw.runtime.loop.session import (
    BranchSummaryEntry,
    CompactionEntry,
    CustomEntry,
    CustomMessageEntry,
    JsonlSessionStorage,
    LabelEntry,
    LeafEntry,
    MessageEntry,
    ModelChangeEntry,
    SessionEntry,
    SessionInfoEntry,
    SessionState,
    ThinkingLevelChangeEntry,
)
from nekoclaw.runtime.loop.tools import (
    AgentTool,
    AgentToolResult,
    ToolCancellationToken,
    ToolExecutionMode,
    ToolExecutor,
    ToolUpdateCallback,
)
from nekoclaw.runtime.loop.turns import AgentTurnContext, AgentTurnDecision, FinishTurn
from nekoclaw.runtime.loop.types import JSONObject, JSONPrimitive, JSONValue

__all__ = [name for name in globals() if not name.startswith("_")]
