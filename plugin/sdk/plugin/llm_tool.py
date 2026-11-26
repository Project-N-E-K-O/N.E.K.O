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
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any, TypedDict

from plugin.sdk.shared.permissions.narrowing import normalize_narrowing

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
    reply_tail: bool = False
    # Set by register_llm_tool_async for each registration and sent to
    # main_server, so that unregister (and the cleanup after a failed
    # register) can remove exactly this registration and never a newer one
    # of the same name. ``None`` on the decorator / sync paths.
    registration_id: str | None = None
    # Component narrowing (``permissions=``): checked when the tool is
    # registered and recorded on its ``__llm_tool__{name}`` entry. Not sent
    # to main_server.
    permissions: tuple[str, ...] | None = None

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
            **({"reply_tail": True} if self.reply_tail else {}),
            **({"registration_id": self.registration_id} if self.registration_id else {}),
        }


def llm_tool(
    *,
    name: str | None = None,
    description: str = "",
    parameters: dict[str, Any] | None = None,
    timeout: float = 30.0,
    role: str | None = None,
    reply_tail: bool = False,
    permissions: list[str] | None = None,
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
    reply_tail:
        Opt in to v1 original-reply image context under ``_ctx.host_reply``.
        The handler must accept ``_ctx`` or ``**kwargs``. False preserves the
        existing arguments. Realtime and old frontends receive no capability.
    permissions:
        Optional narrowing: the permission ids (a subset of what plugin.toml
        declares) this tool needs. Left out, the tool inherits every declared
        permission. A tool listing an undeclared permission is not registered
        and the reason is logged.

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

    Returning images
    ----------------
    A handler may also hand back pictures by returning the envelope
    shape with an ``images`` list::

        return {
            "output": {"shot_id": "shot_7"},
            "images": [{
                "data_b64": "<base64, no data: prefix>",
                "mime": "image/jpeg",          # or image/png
                "vision_prompt": "what to look for in this frame",
            }],
        }

    Whether a picture actually reaches the model depends on the session
    the tool was called from, and the handler cannot see that. A text
    session with a vision model configured switches to it and is shown
    the frame. A realtime voice session cannot be shown one at all --
    a tool result goes back over that wire as a string -- and neither
    can a text session with no vision model configured; there the frame
    is skipped and the model is told so under ``_image_warnings``. Write
    the tool so its ``output`` still reads sensibly in that case.
    ``vision_prompt`` becomes the caption beside the image, and is the
    only thing telling the model why it was handed a frame.

    Limits enforced by ``main_server``: at most 2 images per call, 2 MB
    of base64 each, ``image/jpeg`` or ``image/png`` only, and 2,000
    characters per ``vision_prompt``. Anything rejected or truncated is
    reported to the model under ``_image_warnings`` rather than silently
    dropped. Delivered images are removed from conversation history at
    the end of the turn, so do not rely on the model still seeing a
    frame on a later turn — give it a handle and a way to ask for the
    picture again.
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
        narrowed = normalize_narrowing(permissions, where="@llm_tool")

        meta = LlmToolMeta(
            name=resolved_name,
            description=description,
            parameters=params,
            timeout_seconds=timeout_seconds,
            role=role,
            reply_tail=reply_tail,
            permissions=narrowed,
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


# ---------------------------------------------------------------------------
# Results of register_llm_tool_async / unregister_llm_tool_async
# ---------------------------------------------------------------------------
#
# Built on the host from what ``main_server`` actually answered (see
# ``plugin/server/messaging/handlers/llm_tools.py``) and normalised again on
# the SDK side, so both ends share one shape.
#
# ``ok`` is the one flag plugins should branch on; the SDK commits its local
# state change (keep the tool after register, drop it after unregister) only
# when ``ok`` is True.
#
# ``error`` codes:
#   host side  -- "invalid_request", "main_server_unavailable" (the
#                 request could not reach main_server: connect failure or
#                 no pooled connection), "main_server_error" (HTTP status >= 400),
#                 "rejected" (register: no role accepted the tool),
#                 "partial_failure" (unregister: some roles failed).
#   both       -- "timeout" (no usable answer: none from the host within
#                 ``request_timeout``, or none from main_server after the
#                 request was sent, or one that is not a JSON object; the
#                 outcome on main_server is unknown).
#   SDK side   -- "host_unavailable" (the request could not reach the host or
#                 the host failed to handle it), "busy" (earlier calls for
#                 the same name used up the budget; nothing was sent; also
#                 an unregister whose first request ran but left too little
#                 budget to remove a reported stale registration: that
#                 removal was not sent),
#                 "not_registered"
#                 (unregister: no local record and main_server removed
#                 nothing of this plugin's under that name; with a local
#                 record the same answer is ok=True, removed=False).


class LlmToolRoleFailure(TypedDict, total=False):
    role: str
    error: str


class LlmToolRoleRefusal(TypedDict, total=False):
    role: str
    owned_by: str
    # "different registration": same owner, but not the registration this
    # unregister targeted (e.g. a newer one); left untouched.
    reason: str
    # With that reason: the id of the registration holding the name.
    registration_id: str


class LlmToolUnregisterResult(TypedDict):
    """``ok`` is True when no role still holds this plugin's tool.
    ``removed`` says whether main_server actually removed anything;
    ``refused_roles`` lists roles where the name belongs to another
    source (left untouched, and not ours to remove), or, with
    ``reason="different registration"``, to another registration of this
    plugin than the one targeted. Such a registration is stale; the
    cleanup after a failed register and ``unregister_llm_tool_async`` then
    remove each one by the ``registration_id`` reported with it, and
    ``ok`` requires all of them gone."""

    ok: bool
    removed: bool
    affected_roles: list[str]
    failed_roles: list[LlmToolRoleFailure]
    refused_roles: list[LlmToolRoleRefusal]
    error: str | None
    message: str | None


class LlmToolRegisterResult(TypedDict):
    """``ok`` is True when at least one role accepted the tool;
    ``failed_roles`` then lists the roles that did not.

    ``cleanup`` is ``None`` unless the register failed after the request
    was sent; it is then the result of the unregister the SDK issued so
    that main_server does not keep a tool the plugin has no handler for
    (main_server may have stored it before failing). If ``cleanup`` is
    not ``ok``, call ``unregister_llm_tool_async(name)`` later to retry."""

    ok: bool
    affected_roles: list[str]
    failed_roles: list[LlmToolRoleFailure]
    error: str | None
    message: str | None
    cleanup: LlmToolUnregisterResult | None


def _result_roles(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, str)]


def _result_role_records(value: object) -> list[dict[str, str]]:
    if not isinstance(value, list):
        return []
    return [
        {str(k): str(v) for k, v in item.items() if v is not None}
        for item in value
        if isinstance(item, dict)
    ]


def _result_text(value: object) -> str | None:
    return value if isinstance(value, str) and value else None


def build_register_result(
    body: Mapping[str, Any], *, ok: bool, error: str | None = None, message: str | None = None,
) -> LlmToolRegisterResult:
    """Normalise a register outcome (main_server body or host reply)."""
    return {
        "ok": ok,
        "affected_roles": _result_roles(body.get("affected_roles")),
        "failed_roles": _result_role_records(body.get("failed_roles")),  # type: ignore[typeddict-item]
        "error": _result_text(error),
        "message": _result_text(message),
        "cleanup": None,
    }


def build_unregister_result(
    body: Mapping[str, Any], *, ok: bool, error: str | None = None, message: str | None = None,
) -> LlmToolUnregisterResult:
    """Normalise an unregister outcome (main_server body or host reply)."""
    return {
        "ok": ok,
        "removed": body.get("removed") is True,
        "affected_roles": _result_roles(body.get("affected_roles")),
        "failed_roles": _result_role_records(body.get("failed_roles")),  # type: ignore[typeddict-item]
        "refused_roles": _result_role_records(body.get("refused_roles")),  # type: ignore[typeddict-item]
        "error": _result_text(error),
        "message": _result_text(message),
    }


__all__ = [
    "LLM_TOOL_ENTRY_PREFIX",
    "LLM_TOOL_META_ATTR",
    "LlmToolMeta",
    "LlmToolRegisterResult",
    "LlmToolRoleFailure",
    "LlmToolRoleRefusal",
    "LlmToolUnregisterResult",
    "build_register_result",
    "build_unregister_result",
    "collect_llm_tool_methods",
    "entry_id_for_tool",
    "llm_tool",
    "validate_tool_name",
]
