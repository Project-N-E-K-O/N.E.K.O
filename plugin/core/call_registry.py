"""Host-side registry of plugin deliveries in flight.

Every TRIGGER / TRIGGER_CUSTOM the host sends is keyed by the target plugin
and the request id the host itself generated. The plugin process adopts that
id as the parent of any nested request, so a later request from the same
connection can be attributed by looking up ``(connection plugin id, parent
id)``. Nothing a plugin sends (``_ctx``, ``_rpc_*`` fields, its own view) is
used to build a chain. See docs/design/plugin-call-chain.md.

A call site describes its delivery with ``delivery_link(...)``. The link is a
task-local value read by the communication manager on the caller's loop and
then passed explicitly across any event-loop hop.
"""

from __future__ import annotations

import contextvars
import threading
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field

from plugin.core.rpc import bounded_parent_request_id

ORIGIN_RUN = "run"
ORIGIN_CHAT_TOOL = "chat_tool"
ORIGIN_CHAT_HOOK = "chat_hook"
ORIGIN_UI_ACTION = "ui_action"
ORIGIN_PLUGIN_SELF = "plugin_self"
ORIGIN_SYSTEM = "system"

FLAG_UNATTRIBUTED = "unattributed"
FLAG_POSSIBLE_LAUNDERING = "possible_laundering"
FLAG_CONSENT_UNAVAILABLE = "consent_unavailable"
FLAG_AFTER_RETURN = "after_return"

_MAX_REF_LENGTH = 128


def _ref(value: object) -> str | None:
    if isinstance(value, str) and value:
        return value[:_MAX_REF_LENGTH]
    return None


@dataclass(frozen=True, slots=True)
class Origin:
    """What started the chain. Only pointers (ids), never content."""

    kind: str
    ref: str | None = None

    @classmethod
    def of(cls, kind: str, ref: object = None) -> "Origin":
        return cls(kind, _ref(ref))


@dataclass(eq=False, slots=True)
class CallRecord:
    call_id: str
    target: str
    event_type: str
    event_id: str
    caller: str | None
    parent_call_id: str | None
    trace_id: str
    depth: int
    chain: tuple[str, ...]
    hops: tuple[tuple[str, str | None, str | None], ...]
    origin: Origin
    flags: frozenset[str]
    started: float
    rights: frozenset[str] | None = None
    uses: dict[str, int] = field(default_factory=dict)
    ended_at: float | None = None


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

    A plain context manager on purpose: ``contextlib.contextmanager`` rewrites
    ``__traceback__`` of a passing exception, which frozen domain errors
    (``ServerDomainError``) refuse.
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
            self._token = None
        return False


def current_delivery_link() -> CallLink | None:
    return _DELIVERY_LINK.get()


def normalize_parent_id(value: object) -> str | None:
    """A plugin-supplied parent id is only ever a lookup key; bound its shape."""
    return bounded_parent_request_id(value)


class CallRegistry:
    """Live deliveries, shared by the router loop and every owner loop.

    A delivery that ended without a result (caller timeout, cancellation,
    failure) may still be running in the plugin. Such a record lingers for
    ``linger`` seconds so late uses are still judged against its real chain.
    """

    MAX_LIVE = 4096

    def __init__(self, *, max_live: int = MAX_LIVE, clock: Callable[[], float] = time.monotonic):
        self._lock = threading.Lock()
        self._records: dict[tuple[str, str], CallRecord] = {}
        self._by_target: dict[str, set[CallRecord]] = {}
        self._lingering: deque[tuple[float, CallRecord]] = deque()
        self._max_live = max_live
        self._clock = clock
        self.registry_full = 0
        self.duplicates = 0

    def __len__(self) -> int:
        with self._lock:
            self._prune_locked()
            return len(self._records)

    def register(
        self,
        *,
        target: str,
        call_id: str,
        event_type: str,
        event_id: str,
        link: CallLink | None,
        rights_of: Callable[[tuple[str, ...]], frozenset[str] | None] | None = None,
    ) -> CallRecord | None:
        hop = (target, event_type or None, event_id or None)
        link = link or CallLink(Origin(ORIGIN_SYSTEM))
        parent = link.parent
        if parent is not None:
            chain = (*parent.chain, target)
            hops = (*parent.hops, hop)
            trace_id, origin, parent_call_id = parent.trace_id, parent.origin, parent.call_id
            caller = parent.target
        elif link.caller:
            chain = (link.caller, target)
            hops = ((link.caller, None, None), hop)
            trace_id, origin, parent_call_id = call_id, link.origin, None
            caller = link.caller
        else:
            chain = (target,)
            hops = (hop,)
            trace_id, origin, parent_call_id = call_id, link.origin, None
            caller = None
        record = CallRecord(
            call_id=call_id,
            target=target,
            event_type=event_type,
            event_id=event_id,
            caller=caller,
            parent_call_id=parent_call_id,
            trace_id=trace_id,
            depth=len(chain) - 1,
            chain=chain,
            hops=hops,
            origin=origin,
            flags=link.flags,
            started=self._clock(),
        )
        if rights_of is not None:
            record.rights = rights_of(chain)
        key = (target, call_id)
        with self._lock:
            self._prune_locked()
            if key in self._records:
                self.duplicates += 1
                return None
            if len(self._records) >= self._max_live:
                self.registry_full += 1
                return None
            self._records[key] = record
            self._by_target.setdefault(target, set()).add(record)
        return record

    def remove(self, record: CallRecord, *, linger: float = 0.0) -> dict[str, int]:
        """End a delivery; returns the capability uses it counted so far."""
        key = (record.target, record.call_id)
        with self._lock:
            if self._records.get(key) is not record or record.ended_at is not None:
                return {}
            uses, record.uses = record.uses, {}
            if linger > 0:
                record.ended_at = self._clock()
                self._lingering.append((record.ended_at + linger, record))
            else:
                self._drop_locked(record)
            return uses

    def _drop_locked(self, record: CallRecord) -> None:
        if self._records.get((record.target, record.call_id)) is record:
            del self._records[(record.target, record.call_id)]
        group = self._by_target.get(record.target)
        if group is not None:
            group.discard(record)
            if not group:
                del self._by_target[record.target]

    def _prune_locked(self) -> None:
        now = self._clock()
        while self._lingering and self._lingering[0][0] <= now:
            _, record = self._lingering.popleft()
            self._drop_locked(record)

    def lookup(self, plugin_id: str, request_id: object) -> CallRecord | None:
        parent = normalize_parent_id(request_id)
        if parent is None:
            return None
        with self._lock:
            self._prune_locked()
            return self._records.get((plugin_id, parent))

    def has_wider_live_chain(self, plugin_id: str, chain: tuple[str, ...]) -> bool:
        """Whether another delivery to the plugin carries callers outside ``chain``.

        A request attributed to a narrower chain while such a delivery is in
        flight may be dropping its callers on purpose (possible laundering).
        """
        members = set(chain)
        with self._lock:
            self._prune_locked()
            return any(
                not members.issuperset(record.chain)
                for record in self._by_target.get(plugin_id, ())
            )

    def has_live_chain(self, plugin_id: str) -> bool:
        """Whether the plugin is currently being called by another plugin."""
        return self.has_wider_live_chain(plugin_id, (plugin_id,))

    def note_use(self, record: CallRecord, capability: str) -> bool:
        """Count an allowed use for the end-of-call summary (live records only)."""
        key = (record.target, record.call_id)
        with self._lock:
            if self._records.get(key) is not record or record.ended_at is not None:
                return False
            record.uses[capability] = record.uses.get(capability, 0) + 1
            return True


__all__ = [
    "FLAG_AFTER_RETURN",
    "FLAG_CONSENT_UNAVAILABLE",
    "FLAG_POSSIBLE_LAUNDERING",
    "FLAG_UNATTRIBUTED",
    "ORIGIN_CHAT_HOOK",
    "ORIGIN_CHAT_TOOL",
    "ORIGIN_PLUGIN_SELF",
    "ORIGIN_RUN",
    "ORIGIN_SYSTEM",
    "ORIGIN_UI_ACTION",
    "CallLink",
    "CallRecord",
    "CallRegistry",
    "Origin",
    "current_delivery_link",
    "delivery_link",
    "normalize_parent_id",
]
