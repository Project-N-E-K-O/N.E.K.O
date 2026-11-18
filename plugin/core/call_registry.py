"""Host-side registry of plugin deliveries in flight.

Every TRIGGER / TRIGGER_CUSTOM the host sends is keyed by the target plugin
and the request id the host generated. The plugin process adopts that id as
the parent of its nested requests, so a request is attributed only by looking
up ``(connection plugin id, parent id)`` here. Nothing the plugin sends
(``_ctx``, ``_rpc_*`` fields, its own view) builds a chain.

A host call site describes its delivery with ``delivery_link(...)``; the
communication manager reads it on the caller's loop and passes it on
explicitly across any loop hop. See docs/design/plugin-call-chain.md.
"""

from __future__ import annotations

import contextvars
import threading
import time
from collections import OrderedDict
from dataclasses import dataclass, field

from plugin.core.rpc import bounded_parent_request_id


@dataclass(frozen=True, slots=True)
class Origin:
    """What started the chain: a kind and an id pointer, never content."""

    kind: str
    ref: str | None = None

    @classmethod
    def of(cls, kind: str, ref: object = None) -> "Origin":
        return cls(kind, ref[:128] if isinstance(ref, str) and ref else None)


@dataclass(eq=False, slots=True)
class CallRecord:
    call_id: str
    target: str
    event_type: str
    event_id: str
    caller: str | None
    parent_call_id: str | None
    trace_id: str
    chain: tuple[str, ...]
    hops: tuple[tuple[str, str | None, str | None], ...]
    origin: Origin
    flags: frozenset[str]
    rights: frozenset[str] | None = None
    uses: dict[str, int] = field(default_factory=dict)

    @property
    def depth(self) -> int:
        return len(self.chain) - 1


@dataclass(frozen=True, slots=True)
class CallLink:
    """Chain information a host call site attaches to one delivery."""

    origin: Origin
    caller: str | None = None
    parent: CallRecord | None = None
    flags: frozenset[str] = frozenset()


_DELIVERY_LINK: contextvars.ContextVar[CallLink | None] = contextvars.ContextVar(
    "plugin_delivery_link", default=None
)


class delivery_link:
    """Describe the deliveries started by the enclosed host code.

    Not ``contextlib.contextmanager``: it rewrites ``__traceback__`` of a
    passing exception, which the frozen ``ServerDomainError`` refuses.
    """

    __slots__ = ("_link", "_token")

    def __init__(self, link: CallLink | None) -> None:
        self._link = link
        self._token: contextvars.Token | None = None

    def __enter__(self) -> None:
        self._token = _DELIVERY_LINK.set(self._link)

    def __exit__(self, *exc_info: object) -> bool:
        if self._token is not None:
            _DELIVERY_LINK.reset(self._token)
        return False


def current_delivery_link() -> CallLink | None:
    return _DELIVERY_LINK.get()


class CallRegistry:
    """Live deliveries, shared by the router loop and every owner loop."""

    MAX_LIVE = 4096
    # A push travels on its own uplink and can arrive after the call's reply;
    # finished records stay findable (lookup only) for this long.
    RECENT_SECONDS = 10.0
    MAX_RECENT = 1024

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._records: dict[tuple[str, str], CallRecord] = {}
        self._recent: OrderedDict[tuple[str, str], tuple[float, CallRecord]] = OrderedDict()
        self._callee_counts: dict[str, int] = {}
        self.registry_full = 0

    def __len__(self) -> int:
        return len(self._records)

    def register(self, target: str, call_id: str, event_type: str, event_id: str,
                 link: CallLink | None) -> CallRecord | None:
        hop = (target, event_type or None, event_id or None)
        link = link or CallLink(Origin("system"))
        parent = link.parent
        if parent is not None:
            chain, hops, trace_id = (*parent.chain, target), (*parent.hops, hop), parent.trace_id
            origin, caller, parent_call_id = parent.origin, parent.target, parent.call_id
        elif link.caller:
            chain, hops, trace_id = (link.caller, target), ((link.caller, None, None), hop), call_id
            origin, caller, parent_call_id = link.origin, link.caller, None
        else:
            chain, hops, trace_id = (target,), (hop,), call_id
            origin, caller, parent_call_id = link.origin, None, None
        record = CallRecord(call_id, target, event_type, event_id, caller, parent_call_id,
                            trace_id, chain, hops, origin, link.flags)
        with self._lock:
            if len(self._records) >= self.MAX_LIVE:
                self.registry_full += 1
                return None
            self._records[(target, call_id)] = record
            if len(chain) > 1:
                self._callee_counts[target] = self._callee_counts.get(target, 0) + 1
        return record

    def remove(self, record: CallRecord) -> dict[str, int]:
        """Drop a finished delivery; returns the capability uses it counted."""
        with self._lock:
            if self._records.pop((record.target, record.call_id), None) is not record:
                return {}
            self._recent[(record.target, record.call_id)] = (time.monotonic() + self.RECENT_SECONDS, record)
            if len(self._recent) > self.MAX_RECENT:
                self._recent.popitem(last=False)
            if len(record.chain) > 1:
                count = self._callee_counts.pop(record.target, 0) - 1
                if count > 0:
                    self._callee_counts[record.target] = count
            return record.uses

    def lookup(self, plugin_id: str, request_id: object) -> CallRecord | None:
        parent = bounded_parent_request_id(request_id)
        if parent is None:
            return None
        key = (plugin_id, parent)
        with self._lock:
            record = self._records.get(key)
            if record is None and key in self._recent:
                expires, record = self._recent[key]
                if expires < time.monotonic():
                    del self._recent[key]
                    record = None
            return record

    def has_live_chain(self, plugin_id: str) -> bool:
        """Whether another plugin is calling this one right now."""
        with self._lock:
            return self._callee_counts.get(plugin_id, 0) > 0

    def note_use(self, record: CallRecord, capability: str) -> None:
        # Uses after the call ended are allowed and not summarized.
        with self._lock:
            if self._records.get((record.target, record.call_id)) is record:
                record.uses[capability] = record.uses.get(capability, 0) + 1
