"""The one host entry point for call-chain capability decisions.

Every judged use (the RPC handlers in ``CAPABILITY_BY_OPERATION`` and the
MESSAGE_PUSH route) goes through ``check``. It reads only the host's call
registry and the consent store; plugin-supplied ``_ctx`` and views are never
consulted. In the observe stage nothing is refused. See
docs/design/plugin-call-chain.md.
"""

from __future__ import annotations

import re
from collections.abc import Awaitable, Callable

from plugin.core.call_registry import (
    FLAG_CONSENT_UNAVAILABLE,
    FLAG_POSSIBLE_LAUNDERING,
    FLAG_UNATTRIBUTED,
    ORIGIN_PLUGIN_SELF,
    CallLink,
    CallRecord,
    CallRegistry,
    Origin,
)
from plugin.core.capability_policy import (
    CAPABILITY_NOT_GRANTED,
    POLICY_OFF,
    Decision,
    current_mode,
    decide,
    not_granted_error,
    should_reject,
)
from plugin.server.application.plugins.call_audit import CallAudit

_CODE = re.compile(r"[A-Z][A-Z0-9_]{0,63}")

registry = CallRegistry()
audit = CallAudit()

# Control-RPC operations whose use is judged. One row per operation; every
# row is wrapped by the same ``capability_checked``.
CAPABILITY_BY_OPERATION: dict[str, str] = {
    "MEMORY_QUERY": "memory.read",
    "MEMORY_SEARCH": "memory.read",
    "MEMORY_PERSONA_GET": "memory.read",
    "USER_CONTEXT_GET": "memory.read",
    "CONVERSATION_REPLY_GET": "conversations.read",
    "CONVERSATION_INTERACTION_GET": "conversations.read",
}
MESSAGE_PUSH_CAPABILITY = "messages.push"


def granted_capabilities(plugin_id: str) -> tuple[frozenset[str], bool]:
    """Approved capabilities from the consent store, and whether the read worked."""
    from plugin.server.domain.errors import ServerDomainError
    from plugin.server.infrastructure.plugin_consent import consent_store

    try:
        record = consent_store.read(plugin_id)
    except ServerDomainError:
        return frozenset(), False
    if record.get("decision") != "approved":
        return frozenset(), True
    approved = record.get("approved_capabilities") or []
    return frozenset(c for c in approved if isinstance(c, str)), True


def _rights_of(chain: tuple[str, ...]) -> frozenset[str] | None:
    rights: frozenset[str] | None = None
    for plugin_id in dict.fromkeys(chain):
        granted, ok = granted_capabilities(plugin_id)
        if not ok:
            return None
        rights = granted if rights is None else rights & granted
    return rights


# ── deliveries ──────────────────────────────────────────────────


def link_for_plugin_call(caller: str, parent_request_id: object) -> CallLink:
    """Chain link for a delivery asked for by ``caller`` over PLUGIN_TO_PLUGIN.

    ``caller`` is the connection-bound identity. The parent id is only a key
    into the caller's own records, so a forged id finds nothing.
    """
    parent = registry.lookup(caller, parent_request_id)
    if parent is not None:
        return CallLink(
            origin=parent.origin,
            caller=caller,
            parent=parent,
            flags=parent.flags & {FLAG_POSSIBLE_LAUNDERING},
        )
    flags = frozenset({FLAG_POSSIBLE_LAUNDERING}) if registry.has_live_chain(caller) else frozenset()
    return CallLink(origin=Origin(ORIGIN_PLUGIN_SELF), caller=caller, flags=flags)


def begin_call(
    target: str, call_id: str, event_type: str, event_id: str, link: CallLink | None
) -> CallRecord | None:
    if current_mode() == POLICY_OFF:
        return None
    return registry.register(
        target=target,
        call_id=call_id,
        event_type=event_type,
        event_id=event_id,
        link=link,
        rights_of=_rights_of,
    )


def chain_payload(record: CallRecord) -> dict[str, object]:
    """The ``_call_chain`` field sent next to the delivery (never inside args)."""
    return {
        "trace_id": record.trace_id,
        "call_id": record.call_id,
        "depth": record.depth,
        "origin": {"kind": record.origin.kind, "ref": record.origin.ref},
        "hops": [list(hop) for hop in record.hops],
        "rights": sorted(record.rights) if record.rights is not None else None,
    }


def result_code(exc: BaseException | None) -> str:
    """A fixed code for the audit; exception text is never recorded."""
    if exc is None:
        return "ok"
    import asyncio

    if isinstance(exc, asyncio.CancelledError):
        return "CANCELLED"
    cause = exc.__cause__
    for candidate in (exc, cause):
        code = getattr(candidate, "code", None)
        if isinstance(code, str) and _CODE.fullmatch(code):
            return code
    if isinstance(exc, TimeoutError):
        return "TIMEOUT"
    return "ERROR"


def end_call(record: CallRecord | None, code: str, duration: float) -> None:
    if record is None:
        return
    uses = registry.remove(record)
    mode = current_mode()
    base = {
        "mode": mode,
        "trace_id": record.trace_id,
        "call_id": record.call_id,
        "parent_call_id": record.parent_call_id,
        "depth": record.depth,
        "target": record.target,
    }
    audit.record({
        "kind": "forward",
        **base,
        "caller": record.caller,
        "event_type": record.event_type,
        "event_id": record.event_id,
        "origin_kind": record.origin.kind,
        "origin_ref": record.origin.ref,
        "result": code,
        "duration_ms": round(max(0.0, duration) * 1000, 1),
        "flags": sorted(record.flags),
    })
    if uses:
        audit.record({"kind": "allow_summary", **base, "counts": dict(sorted(uses.items()))})


# ── uses ────────────────────────────────────────────────────────


def check(plugin_id: str, parent_request_id: object, capability: str, operation: str) -> Decision | None:
    """Judge one use. Returns None when the policy is off."""
    mode = current_mode()
    if mode == POLICY_OFF:
        return None
    record = registry.lookup(plugin_id, parent_request_id)
    flags: set[str] = set()
    if record is None:
        chain: tuple[str, ...] = (plugin_id,)
        flags.add(FLAG_UNATTRIBUTED)
        if registry.has_live_chain(plugin_id):
            flags.add(FLAG_POSSIBLE_LAUNDERING)
    else:
        chain = record.chain
        flags.update(record.flags & {FLAG_POSSIBLE_LAUNDERING})
    cache: dict[str, frozenset[str]] = {}

    def granted(member: str) -> frozenset[str]:
        if member not in cache:
            value, ok = granted_capabilities(member)
            if not ok:
                flags.add(FLAG_CONSENT_UNAVAILABLE)
            cache[member] = value
        return cache[member]

    decision = decide(capability, chain, granted)
    if not decision.judged:
        return decision
    reject = should_reject(decision, mode)
    if not decision.allowed or FLAG_POSSIBLE_LAUNDERING in flags:
        entry = {
            "kind": "decision",
            "mode": mode,
            "plugin": plugin_id,
            "operation": operation,
            "capability": capability,
            "chain": list(chain),
            "verdict": "allow" if decision.allowed else ("deny" if reject else "would_deny"),
            "reason": decision.reason,
            "blocked_by": list(decision.blocked_by),
            "flags": sorted(flags),
            "trace_id": record.trace_id if record else None,
            "call_id": record.call_id if record else None,
            "depth": record.depth if record else 0,
            "origin_kind": record.origin.kind if record else ORIGIN_PLUGIN_SELF,
            "origin_ref": record.origin.ref if record else None,
        }
        if decision.allowed:
            audit.record(entry)
        else:
            audit.record_denial(entry, (plugin_id, capability, decision.reason, decision.blocked_by))
    elif record is not None:
        registry.note_use(record, capability)
    else:
        audit.count_unattributed(plugin_id, capability, mode=mode)
    if reject:
        from plugin.server.domain.errors import ServerDomainError

        error = not_granted_error(decision)
        raise ServerDomainError(
            code=CAPABILITY_NOT_GRANTED,
            message=str(error["message"]),
            status_code=403,
            details=error["details"],
        )
    return decision


Handler = Callable[..., Awaitable[object]]


def capability_checked(capability: str, handler: Handler) -> Handler:
    """Wrap a control-RPC handler with the shared decision entry point."""

    async def checked(ctx, request):
        if current_mode() != POLICY_OFF:
            check(ctx.from_plugin, ctx.parent_request_id, capability, str(request.get("type") or ""))
        return await handler(ctx, request)

    checked.__name__ = getattr(handler, "__name__", "checked")
    checked.__qualname__ = getattr(handler, "__qualname__", checked.__name__)
    checked.__wrapped__ = handler  # type: ignore[attr-defined]
    checked.__capability__ = capability  # type: ignore[attr-defined]
    for name, value in vars(handler).items():
        if name.startswith("__rpc_"):
            setattr(checked, name, value)
    return checked


def flush_and_close(timeout: float = 1.0) -> bool:
    """Shutdown hook: write pending summaries, then a bounded queue flush."""
    try:
        audit.flush_summaries(mode=current_mode())
    finally:
        flushed = audit.close(timeout)
    return flushed


def reopen() -> None:
    audit.reopen()


__all__ = [
    "CAPABILITY_BY_OPERATION",
    "MESSAGE_PUSH_CAPABILITY",
    "audit",
    "begin_call",
    "capability_checked",
    "chain_payload",
    "check",
    "end_call",
    "flush_and_close",
    "granted_capabilities",
    "link_for_plugin_call",
    "registry",
    "reopen",
    "result_code",
]
