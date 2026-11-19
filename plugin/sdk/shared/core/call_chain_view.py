"""Read-only call-chain view for plugin code (``self.ctx.call_chain``).

The host sends a ``_call_chain`` field next to each TRIGGER / TRIGGER_CUSTOM
and the plugin process exposes it through a context variable, like
``current_lanlan``. The host never reads it back; its decisions come from its
own registry. See docs/design/plugin-call-chain.md.
"""

from __future__ import annotations

import contextlib
import contextvars
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class CallOrigin:
    kind: str
    ref: str | None = None
    speaker: str | None = None  # reserved; always None for now


@dataclass(frozen=True, slots=True)
class CallHop:
    plugin_id: str
    event_type: str | None = None
    event_id: str | None = None


@dataclass(frozen=True, slots=True)
class CallChainView:
    """Where the current invocation came from.

    ``rights`` is the intersection of capabilities approved for every plugin
    on the chain when the host delivered the call. ``rights_known`` is False
    when the host did not provide it (outside an invocation, or policy off).
    ``allows`` is advisory and never widens: False whenever rights are
    unknown. The host makes the final decision on every use.
    """

    plugin_id: str
    trace_id: str | None
    call_id: str | None
    depth: int
    origin: CallOrigin
    hops: tuple[CallHop, ...]
    callers: tuple[str, ...]
    rights: frozenset[str]
    rights_known: bool

    def allows(self, capability: str) -> bool:
        return self.rights_known and capability in self.rights


def self_only_view(plugin_id: str) -> CallChainView:
    return CallChainView(plugin_id, None, None, 0, CallOrigin("plugin_self"), (CallHop(plugin_id),),
                         (), frozenset(), False)


def view_from_wire(plugin_id: str, payload: Any) -> CallChainView:
    """Decode the host's ``_call_chain`` field; anything unexpected means self-only."""
    try:
        hops = tuple(CallHop(*hop) for hop in payload["hops"])
        origin = payload["origin"]
        rights = payload.get("rights")
        if not hops or hops[-1].plugin_id != plugin_id:
            return self_only_view(plugin_id)
        return CallChainView(
            plugin_id, payload.get("trace_id"), payload.get("call_id"), len(hops) - 1,
            CallOrigin(origin["kind"], origin.get("ref")), hops,
            tuple(hop.plugin_id for hop in hops[:-1]),
            frozenset(rights or ()), isinstance(rights, list),
        )
    except Exception:
        return self_only_view(plugin_id)


class _Scope:
    __slots__ = ("view", "closed")

    def __init__(self, view: CallChainView) -> None:
        self.view, self.closed = view, False


_CURRENT: contextvars.ContextVar[_Scope | None] = contextvars.ContextVar("plugin_call_chain", default=None)


@contextlib.contextmanager
def call_chain_scope(plugin_id: str, payload: Any):
    """Expose the host's view for one invocation.

    Work copied from this context shares the scope; it is closed when the
    invocation ends, so surviving background tasks see the self-only chain,
    matching how the host attributes their requests.
    """
    if payload is None:
        yield
        return
    scope = _Scope(view_from_wire(plugin_id, payload))
    token = _CURRENT.set(scope)
    try:
        yield
    finally:
        scope.closed = True
        _CURRENT.reset(token)


def current_call_chain(plugin_id: str) -> CallChainView:
    scope = _CURRENT.get()
    if scope is None or scope.closed or scope.view.plugin_id != plugin_id:
        return self_only_view(plugin_id)
    return scope.view
