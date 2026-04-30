"""SDK helpers for plugin-owned LLM tools.

A plugin marks methods with :func:`llm_tool` (the decorator), and the
SDK wires each one up so the LLM running inside ``main_server`` can
call it directly. Concretely:

* The decorator stores metadata on the method (a JSON-Schema-shaped
  ``parameters`` block, a name and description for the model, and a
  per-call timeout).
* During the plugin's startup lifecycle, ``NekoPluginBase`` walks every
  decorated method, registers it as a *dynamic plugin entry* under the
  reserved id ``__llm_tool__{name}``, and emits an
  ``LLM_TOOL_REGISTER`` IPC notification.
* The host (``user_plugin_server``) handles that notification by
  POSTing to ``main_server`` ``/api/tools/register`` with a callback
  URL pointing at the plugin server's
  ``/api/llm-tools/callback/{plugin_id}/{name}`` route.
* When the LLM picks the tool, ``main_server`` POSTs the call back to
  that route, which forwards it through ``host.trigger`` IPC into the
  plugin's running process — exactly like a regular plugin entry.

Why route through plugin entries instead of a separate command type?
The entry trigger path already handles run isolation, cancellation,
result framing, and timeouts. Reusing it means LLM tools inherit all
of those guarantees without a parallel implementation.

Naming
------
``name`` is the model-visible tool identifier and must satisfy
``main_server``'s ``ToolRegisterRequest`` constraints
(``min_length=1, max_length=64``). We also reject characters that
would break URL path inlining (the callback URL embeds the name as a
path segment).
"""
from __future__ import annotations

import inspect
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

# Module-level attribute used by the decorator to mark methods. Pulling
# this onto a constant lets ``NekoPluginBase`` discover decorated
# methods via ``getattr(method, _LLM_TOOL_META_ATTR, None)`` without a
# circular import.
LLM_TOOL_META_ATTR = "__neko_llm_tool_meta__"

# Reserved entry-id prefix for LLM tools. Picked to be unlikely to
# collide with hand-written entry ids (which are conventionally
# snake_case). The prefix is also visible in IPC logs which makes
# triage easier.
LLM_TOOL_ENTRY_PREFIX = "__llm_tool__"

# Tool name validation. main_server enforces a 64-char cap; we also
# require that names be safe to inline into a URL path segment so the
# callback URL never needs encoding. Pattern: letters, digits,
# underscore, dash, dot.
_TOOL_NAME_PATTERN = re.compile(r"^[A-Za-z0-9_.\-]{1,64}$")


def entry_id_for_tool(tool_name: str) -> str:
    """Returns the reserved dynamic-entry id under which the SDK
    registers an LLM tool's handler. Kept as a module-level helper so
    the host route (``plugin/server/routes/llm_tools.py``) can build the
    same id without importing the SDK."""
    return f"{LLM_TOOL_ENTRY_PREFIX}{tool_name}"


def validate_tool_name(name: str) -> str:
    if not isinstance(name, str) or not _TOOL_NAME_PATTERN.fullmatch(name):
        raise ValueError(
            f"LLM tool name {name!r} must match {_TOOL_NAME_PATTERN.pattern}"
        )
    return name


@dataclass
class LlmToolMeta:
    """Metadata captured by the :func:`llm_tool` decorator.

    Stored as an attribute on the decorated function, then read by
    :class:`~plugin.sdk.plugin.base.NekoPluginBase` during startup to
    drive registration. Kept as a plain dataclass so it survives
    pickling for any lifecycle that re-imports the plugin module.
    """

    name: str
    description: str
    parameters: dict[str, Any] = field(default_factory=lambda: {"type": "object", "properties": {}})
    timeout_seconds: float = 30.0
    role: str | None = None

    def to_ipc_payload(self, *, plugin_id: str) -> dict[str, Any]:
        """Build the LLM_TOOL_REGISTER IPC message payload.

        ``plugin_id`` is injected by the caller (the SDK base class
        knows it; the decorator does not).
        """
        return {
            "type": "LLM_TOOL_REGISTER",
            "plugin_id": plugin_id,
            "name": self.name,
            "description": self.description,
            "parameters": dict(self.parameters),
            "timeout_seconds": float(self.timeout_seconds),
            "role": self.role,
        }


def llm_tool(
    *,
    name: str | None = None,
    description: str = "",
    parameters: dict[str, Any] | None = None,
    timeout: float = 30.0,
    role: str | None = None,
) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """Decorator marking a plugin method as a model-callable LLM tool.

    Example::

        from plugin.sdk.plugin import neko_plugin, NekoPluginBase, llm_tool, lifecycle, Ok

        @neko_plugin
        class WeatherPlugin(NekoPluginBase):
            @lifecycle(id="startup")
            async def startup(self, **_):
                # auto-registration happens inside the base startup hook;
                # nothing to do here besides any plugin-specific bring-up.
                return Ok({"status": "ready"})

            @llm_tool(
                name="get_weather",
                description="Get the current weather for a city.",
                parameters={
                    "type": "object",
                    "properties": {
                        "city": {"type": "string", "description": "City name"},
                    },
                    "required": ["city"],
                },
            )
            async def get_weather(self, *, city: str):
                return {"city": city, "temp_c": 21}

    Parameters
    ----------
    name:
        The model-visible tool name. Defaults to the decorated method's
        ``__name__``. Must match ``[A-Za-z0-9_.\\-]{1,64}``.
    description:
        Free-text description shown to the LLM. Be specific about what
        the tool does and what arguments it expects — the model uses
        this to decide when to call it.
    parameters:
        JSON Schema for the tool's arguments. Should be a JSON-Schema
        object with ``type: "object"`` at the top level. Defaults to
        ``{"type": "object", "properties": {}}`` (no arguments).
    timeout:
        Per-call timeout in seconds. Capped server-side at 300s by
        ``main_server``'s ``ToolRegisterRequest``. Defaults to 30s.
    role:
        Optional role / character name to scope the tool to. ``None``
        registers globally (visible to every character/role's
        ``LLMSessionManager``). Pass a string like ``"Lanlan"`` to
        scope to a single role.

    Notes
    -----
    The decorated method receives the parsed JSON arguments as keyword
    arguments. For example, if ``parameters`` declares a ``city`` field
    and the LLM emits ``{"city": "Tokyo"}``, the method is called as
    ``self.get_weather(city="Tokyo")``. Use ``**kwargs`` to ignore
    extra keys safely.

    The return value is sent back to the LLM. Plain Python values
    (``str``, ``int``, ``dict``, ``list``) are JSON-serialised. Return
    a dict shaped ``{"output": ..., "is_error": True, "error": "..."}``
    if the handler wants to flag a tool-level error to the model
    without raising.
    """

    def decorator(func: Callable[..., Any]) -> Callable[..., Any]:
        if not callable(func):
            raise TypeError("@llm_tool may only decorate callables")

        # Resolve final name: explicit > __name__. Async or sync — both
        # are accepted; the SDK awaits if the call returns a coroutine.
        resolved_name = name if isinstance(name, str) and name else func.__name__
        validate_tool_name(resolved_name)

        # Validate the parameters schema lightly. We don't fully
        # validate against the JSON Schema spec — main_server will
        # accept whatever shape we forward — but we do require it to
        # be a dict so accidental list/None values fail fast at decorator
        # application time rather than at registration.
        params = parameters if parameters is not None else {"type": "object", "properties": {}}
        if not isinstance(params, dict):
            raise TypeError(f"@llm_tool parameters must be a dict, got {type(params).__name__}")

        timeout_seconds = float(timeout)
        if timeout_seconds <= 0:
            raise ValueError(f"@llm_tool timeout must be > 0, got {timeout_seconds}")

        meta = LlmToolMeta(
            name=resolved_name,
            description=description,
            parameters=params,
            timeout_seconds=timeout_seconds,
            role=role,
        )
        setattr(func, LLM_TOOL_META_ATTR, meta)
        return func

    return decorator


def collect_llm_tool_methods(instance: Any) -> list[tuple[LlmToolMeta, Callable[..., Any]]]:
    """Walk a plugin instance and return every method tagged with the
    :func:`llm_tool` decorator, paired with its metadata. Order follows
    Python's normal MRO method discovery (i.e. matches what ``dir()``
    returns, which is essentially the source declaration order within a
    class)."""
    out: list[tuple[LlmToolMeta, Callable[..., Any]]] = []
    seen: set[int] = set()
    for _, member in inspect.getmembers(type(instance)):
        if not callable(member):
            continue
        meta = getattr(member, LLM_TOOL_META_ATTR, None)
        if not isinstance(meta, LlmToolMeta):
            continue
        bound = getattr(instance, member.__name__, None)
        if not callable(bound):
            continue
        # Avoid duplicates from MRO (a subclass overrides the parent's
        # decorator-tagged method but inherits the tag).
        ident = id(bound)
        if ident in seen:
            continue
        seen.add(ident)
        out.append((meta, bound))
    return out


__all__ = [
    "LLM_TOOL_ENTRY_PREFIX",
    "LLM_TOOL_META_ATTR",
    "LlmToolMeta",
    "collect_llm_tool_methods",
    "entry_id_for_tool",
    "llm_tool",
    "validate_tool_name",
]
