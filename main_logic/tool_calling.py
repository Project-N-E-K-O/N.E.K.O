"""Unified tool calling primitives shared by ``OmniOfflineClient``,
``OmniRealtimeClient``, and ``LLMSessionManager``.

Design goals
============
- One canonical ``ToolDefinition`` shape (OpenAI-style JSON schema for
  parameters) that every provider adapter knows how to translate into its
  on-the-wire format (OpenAI Realtime / Gemini Live / GLM / StepFun / OpenAI
  Chat Completions / google-genai).
- ``ToolRegistry`` lives on ``LLMSessionManager`` so callers can
  ``register_tool(...)`` / ``unregister_tool(...)`` from anywhere
  (including agent_server / plugins via the cross-process RPC layer).
- The registry executes ``ToolCall`` → ``ToolResult`` and the active
  client (offline or realtime) feeds the result back to the model and
  resumes generation.

Provider-specific schema translation lives in the client classes
(``OmniOfflineClient`` / ``OmniRealtimeClient``); this module stays
provider-agnostic.
"""
from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Dict, List, Optional, Union

from utils.logger_config import get_module_logger

logger = get_module_logger(__name__, "Main")


ToolHandler = Callable[[Dict[str, Any]], Union[Awaitable[Any], Any]]


@dataclass
class ToolDefinition:
    """Canonical, provider-agnostic tool description.

    ``parameters`` is an OpenAI-flavoured JSON Schema object
    (``{"type": "object", "properties": {...}, "required": [...]}``).
    Provider adapters convert it to their wire format.

    ``handler`` is optional — if absent, the registry treats the tool as
    "remote" and dispatches via ``remote_dispatcher`` (used for
    plugin/agent_server tools that live in another process).
    """

    name: str
    description: str
    parameters: Dict[str, Any] = field(default_factory=lambda: {"type": "object", "properties": {}})
    handler: Optional[ToolHandler] = None
    # Free-form metadata: e.g. {"source": "plugin", "plugin_id": "...", "version": "..."}
    # Used by the cross-process RPC layer to route remote calls.
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_openai_chat(self) -> Dict[str, Any]:
        """OpenAI Chat Completions / StepFun Realtime / Qwen-text format
        (nested under ``function``)."""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }

    def to_openai_realtime(self) -> Dict[str, Any]:
        """OpenAI Realtime / GLM Realtime format (flat — name, description,
        parameters at the top level alongside ``type``)."""
        return {
            "type": "function",
            "name": self.name,
            "description": self.description,
            "parameters": self.parameters,
        }

    def to_gemini_function_declaration(self) -> Dict[str, Any]:
        """Gemini Live + google-genai chat: ``function_declarations`` entry."""
        return {
            "name": self.name,
            "description": self.description,
            "parameters": self.parameters,
        }


@dataclass
class ToolCall:
    """Parsed tool invocation from the model.

    ``call_id`` originates from the provider:
        - OpenAI Realtime / StepFun: ``call_id`` field on the event
        - Gemini Live / google-genai: ``function_call.id``
        - GLM Realtime: synthesized from ``response_id + output_index`` (no
          native call_id; the protocol echoes it back in the response item
          and we don't need to round-trip it)
        - OpenAI Chat Completions streaming: ``tool_calls[].id``

    ``arguments`` is parsed JSON; ``raw_arguments`` is the original string
    if parsing failed (some providers stream incomplete JSON; clients
    accumulate then attempt parse).
    """

    name: str
    arguments: Dict[str, Any]
    call_id: str = ""
    raw_arguments: str = ""
    # Used internally by some providers for state tracking; opaque to callers.
    provider_meta: Dict[str, Any] = field(default_factory=dict)


@dataclass
class ToolResult:
    """Outcome of executing a ``ToolCall``."""

    call_id: str
    name: str
    # Result payload — typically a dict; will be JSON-encoded by the
    # provider adapter when sending back. Strings pass through unchanged
    # for providers that expect a raw string body.
    output: Any
    is_error: bool = False
    error_message: str = ""

    def output_as_json_string(self) -> str:
        """Render ``output`` as the JSON string that OpenAI Realtime / GLM /
        StepFun expect in ``conversation.item.create.item.output``."""
        if isinstance(self.output, str):
            return self.output
        try:
            return json.dumps(self.output, ensure_ascii=False)
        except (TypeError, ValueError):
            return json.dumps({"result": str(self.output)}, ensure_ascii=False)


# Callback shape exposed to the clients. Clients invoke this when the
# model emits a tool call; the implementation (registry on
# LLMSessionManager) returns the result, and the client sends it back to
# the provider on the wire.
OnToolCallCallback = Callable[[ToolCall], Awaitable[ToolResult]]


class ToolRegistryError(Exception):
    pass


class ToolRegistry:
    """Process-local tool registry.

    Local handlers run in-process (any sync/async callable). Remote tools
    have ``handler=None`` and rely on ``remote_dispatcher``, which the
    plugin/agent_server RPC layer plugs in (see
    ``main_routers/tool_router.py`` for the HTTP wiring).
    """

    def __init__(
        self,
        *,
        remote_dispatcher: Optional[Callable[[ToolCall, Dict[str, Any]], Awaitable[ToolResult]]] = None,
    ) -> None:
        self._tools: Dict[str, ToolDefinition] = {}
        self._lock = asyncio.Lock()
        self._remote_dispatcher = remote_dispatcher
        # Telemetry: last execution timing for each tool. Useful for
        # surface-level observability without having to plumb full tracing.
        self._last_invocation_ms: Dict[str, float] = {}

    # ---- registration ---------------------------------------------------

    def register(self, tool: ToolDefinition, *, replace: bool = True) -> None:
        if not tool.name:
            raise ToolRegistryError("ToolDefinition.name must be non-empty")
        if not replace and tool.name in self._tools:
            raise ToolRegistryError(f"tool '{tool.name}' already registered")
        self._tools[tool.name] = tool
        logger.info(
            "ToolRegistry: registered '%s' (handler=%s, source=%s)",
            tool.name,
            "local" if tool.handler else "remote",
            tool.metadata.get("source", "unknown"),
        )

    def unregister(self, name: str) -> bool:
        existed = self._tools.pop(name, None) is not None
        if existed:
            logger.info("ToolRegistry: unregistered '%s'", name)
        return existed

    def get(self, name: str) -> Optional[ToolDefinition]:
        return self._tools.get(name)

    def names(self) -> List[str]:
        return list(self._tools.keys())

    def all(self) -> List[ToolDefinition]:
        return list(self._tools.values())

    def clear(self, *, source: Optional[str] = None) -> int:
        """Remove all tools, or only those with a matching ``metadata.source``.

        Returns the number removed. Callers like ``unregister_plugin_tools``
        use ``source="plugin:<id>"`` to drop just that plugin's tools.
        """
        if source is None:
            n = len(self._tools)
            self._tools.clear()
            return n
        to_drop = [k for k, t in self._tools.items() if t.metadata.get("source") == source]
        for k in to_drop:
            self._tools.pop(k, None)
        return len(to_drop)

    # ---- execution ------------------------------------------------------

    async def execute(self, call: ToolCall) -> ToolResult:
        """Execute a tool call. Never raises — wraps errors into a
        ``ToolResult(is_error=True)`` so the calling client can still feed
        a structured response back to the model (model sees the error as
        a normal tool result string, often recoverable)."""
        tool = self._tools.get(call.name)
        if tool is None:
            msg = f"tool '{call.name}' is not registered"
            logger.warning("ToolRegistry.execute: %s", msg)
            return ToolResult(
                call_id=call.call_id,
                name=call.name,
                output={"error": msg, "available_tools": self.names()},
                is_error=True,
                error_message=msg,
            )

        start = time.time()
        try:
            if tool.handler is not None:
                result_value = tool.handler(call.arguments or {})
                if asyncio.iscoroutine(result_value) or isinstance(result_value, asyncio.Future):
                    result_value = await result_value
                return ToolResult(
                    call_id=call.call_id,
                    name=call.name,
                    output=result_value,
                    is_error=False,
                )

            # Remote tool — delegate to the dispatcher (plugin/agent_server).
            if self._remote_dispatcher is None:
                msg = f"tool '{call.name}' is remote but no dispatcher is bound"
                logger.error("ToolRegistry.execute: %s", msg)
                return ToolResult(
                    call_id=call.call_id,
                    name=call.name,
                    output={"error": msg},
                    is_error=True,
                    error_message=msg,
                )
            return await self._remote_dispatcher(call, tool.metadata)
        except Exception as e:
            err_text = f"{type(e).__name__}: {e}"
            logger.exception("ToolRegistry.execute: '%s' raised: %s", call.name, err_text)
            return ToolResult(
                call_id=call.call_id,
                name=call.name,
                output={"error": err_text},
                is_error=True,
                error_message=err_text,
            )
        finally:
            self._last_invocation_ms[call.name] = (time.time() - start) * 1000.0

    # ---- export ---------------------------------------------------------

    def specs_for(self, *, dialect: str) -> List[Dict[str, Any]]:
        """Return all tool specs serialized for the given provider dialect.

        ``dialect`` ∈ {"openai_chat", "openai_realtime", "gemini"}.
        Provider adapters in the clients call this to fill the wire
        ``tools`` field at session-config time.
        """
        if not self._tools:
            return []
        if dialect == "openai_chat":
            return [t.to_openai_chat() for t in self._tools.values()]
        if dialect == "openai_realtime":
            return [t.to_openai_realtime() for t in self._tools.values()]
        if dialect == "gemini":
            return [t.to_gemini_function_declaration() for t in self._tools.values()]
        raise ToolRegistryError(f"unknown dialect: {dialect}")

    def gemini_tools_config(self) -> List[Any]:
        """Return ``[types.Tool(function_declarations=[…])]`` ready for
        ``GenerateContentConfig(tools=…)`` / ``LiveConnectConfig(tools=…)``.

        Lazy-imports ``google.genai.types`` so this module is importable
        on systems without the SDK (the realtime client already does this
        dance — we mirror it here for the offline path)."""
        if not self._tools:
            return []
        try:
            from google.genai import types as genai_types  # noqa: WPS433
        except Exception as e:
            raise ToolRegistryError(f"google-genai SDK unavailable: {e}")
        decls = [t.to_gemini_function_declaration() for t in self._tools.values()]
        return [genai_types.Tool(function_declarations=decls)]


# ---------------------------------------------------------------------------
# Argument parsing helpers used by client adapters
# ---------------------------------------------------------------------------


def parse_arguments_json(arguments: Union[str, Dict[str, Any], None]) -> Dict[str, Any]:
    """Best-effort JSON decode for streamed tool-call arguments.

    Providers like OpenAI Realtime / StepFun stream argument fragments
    that the client accumulates into a single string before parsing;
    google-genai already exposes a parsed dict. This helper normalizes
    both."""
    if arguments is None:
        return {}
    if isinstance(arguments, dict):
        return arguments
    text = (arguments or "").strip()
    if not text:
        return {}
    try:
        parsed = json.loads(text)
        return parsed if isinstance(parsed, dict) else {"value": parsed}
    except json.JSONDecodeError:
        # Some providers emit Python-literal-ish strings; fall back to a
        # raw passthrough so the model still sees what it intended.
        return {"_raw": text}
