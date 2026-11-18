"""Read-only call-chain view for plugin code (``self.ctx.call_chain``).

The host sends a ``_call_chain`` field next to each TRIGGER / TRIGGER_CUSTOM.
The plugin process exposes it through a context variable, the same way
``current_lanlan`` works. The host never reads this view back: its decisions
come from its own call registry. See docs/design/plugin-call-chain.md.
"""

from __future__ import annotations

import contextlib
import contextvars
from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from typing import Any

_MAX_HOPS = 64
_MAX_TEXT = 256


@dataclass(frozen=True, slots=True)
class CallOrigin:
    kind: str
    ref: str | None = None
    # Reserved for owner / non-owner identity; always None for now.
    speaker: str | None = None


@dataclass(frozen=True, slots=True)
class CallHop:
    plugin_id: str
    event_type: str | None = None
    event_id: str | None = None


@dataclass(frozen=True, slots=True)
class CallChainView:
    """Where the current invocation came from.

    ``rights`` is the intersection of capabilities approved for every plugin
    on the chain, as the host saw it when it delivered the call. When the host
    did not provide it (outside an invocation, or the policy is off),
    ``rights_known`` is False and ``rights`` is empty.

    ``allows`` is advisory and never widens: it answers False when the rights
    are unknown. The host makes the final decision on every use.
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
    return CallChainView(
        plugin_id=plugin_id,
        trace_id=None,
        call_id=None,
        depth=0,
        origin=CallOrigin(kind="plugin_self"),
        hops=(CallHop(plugin_id),),
        callers=(),
        rights=frozenset(),
        rights_known=False,
    )


def _text(value: object) -> str | None:
    if isinstance(value, str) and value:
        return value[:_MAX_TEXT]
    return None


def view_from_wire(plugin_id: str, payload: object) -> CallChainView:
    """Decode the host's ``_call_chain`` field; anything malformed means self-only."""
    if not isinstance(payload, Mapping):
        return self_only_view(plugin_id)
    hops: list[CallHop] = []
    raw_hops = payload.get("hops")
    if isinstance(raw_hops, list):
        for raw in raw_hops[:_MAX_HOPS]:
            if isinstance(raw, (list, tuple)) and raw and _text(raw[0]):
                hops.append(CallHop(
                    _text(raw[0]),  # type: ignore[arg-type]
                    _text(raw[1]) if len(raw) > 1 else None,
                    _text(raw[2]) if len(raw) > 2 else None,
                ))
    if not hops or hops[-1].plugin_id != plugin_id:
        return self_only_view(plugin_id)
    origin_raw = payload.get("origin")
    origin = CallOrigin(kind="system")
    if isinstance(origin_raw, Mapping) and _text(origin_raw.get("kind")):
        origin = CallOrigin(kind=_text(origin_raw.get("kind")), ref=_text(origin_raw.get("ref")))  # type: ignore[arg-type]
    rights_raw = payload.get("rights")
    rights_known = isinstance(rights_raw, list)
    rights = frozenset(r for r in rights_raw if isinstance(r, str)) if rights_known else frozenset()
    depth = payload.get("depth")
    return CallChainView(
        plugin_id=plugin_id,
        trace_id=_text(payload.get("trace_id")),
        call_id=_text(payload.get("call_id")),
        depth=depth if isinstance(depth, int) and not isinstance(depth, bool) and depth >= 0 else len(hops) - 1,
        origin=origin,
        hops=tuple(hops),
        callers=tuple(hop.plugin_id for hop in hops[:-1]),
        rights=rights,
        rights_known=rights_known,
    )


class _ViewScope:
    __slots__ = ("view", "closed")

    def __init__(self, view: CallChainView) -> None:
        self.view = view
        self.closed = False


_CURRENT_CALL_CHAIN: contextvars.ContextVar[_ViewScope | None] = contextvars.ContextVar(
    "plugin_current_call_chain", default=None
)


@contextlib.contextmanager
def call_chain_scope(plugin_id: str, payload: Any) -> Iterator[None]:
    """Expose the host's view for one invocation.

    Tasks and threads copied from this context share the scope object; it is
    closed when the invocation ends, so surviving background work sees the
    self-only chain, matching how the host attributes its requests.
    """
    if payload is None:
        yield
        return
    scope = _ViewScope(view_from_wire(plugin_id, payload))
    token = _CURRENT_CALL_CHAIN.set(scope)
    try:
        yield
    finally:
        scope.closed = True
        _CURRENT_CALL_CHAIN.reset(token)


def current_call_chain(plugin_id: str) -> CallChainView:
    scope = _CURRENT_CALL_CHAIN.get()
    if scope is None or scope.closed or scope.view.plugin_id != plugin_id:
        return self_only_view(plugin_id)
    return scope.view


__all__ = [
    "CallChainView",
    "CallHop",
    "CallOrigin",
    "call_chain_scope",
    "current_call_chain",
    "self_only_view",
    "view_from_wire",
]
