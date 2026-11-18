"""The one host entry point for call-chain capability decisions.

Every judged use (the RPC handlers in ``CAPABILITY_BY_OPERATION`` and the
MESSAGE_PUSH route) goes through ``check``. It reads only the host's call
registry and the consent store; plugin-supplied ``_ctx`` and views are never
consulted. In the observe stage nothing is refused, and a failure inside this
module never changes what the caller sees (fail-open, error type logged). See
docs/design/plugin-call-chain.md.
"""

from __future__ import annotations

import re
from collections.abc import Awaitable, Callable

from plugin.core.call_registry import (
    FLAG_AFTER_RETURN,
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
from plugin.logging_config import get_logger
from plugin.server.application.plugins.call_audit import CallAudit

logger = get_logger("server.plugins.capability_gate")

_CODE = re.compile(r"[A-Z][A-Z0-9_]{0,63}")
# A delivery that ended without the plugin's reply may still be running in
# the plugin; keep its record that long so late uses keep their chain.
LINGER_SECONDS = 30.0
# Only ends where the host stopped waiting before the plugin answered; an
# error reply means the entry is over.
_MAY_STILL_RUN = frozenset({
    "CANCELLED", "TIMEOUT", "DEADLINE_EXCEEDED", "TRANSPORT_TIMEOUT", "TRANSPORT_ERROR",
})

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


class CapabilityNotGranted(Exception):
    """Raised by ``check`` only when a stage enforces the decision."""

    def __init__(self, decision: Decision):
        super().__init__(decision.capability)
        self.decision = decision


def _log_failure(where: str, exc: BaseException) -> None:
    # Type only: exception text can carry plugin or user data.
    logger.warning("call-chain {} failed: error_type={}", where, type(exc).__name__)


def granted_capabilities(plugin_id: str) -> tuple[frozenset[str], bool]:
    """Approved capabilities from the consent store, and whether the read worked."""
    try:
        from plugin.server.infrastructure.plugin_consent import consent_store

        record = consent_store.read(plugin_id)
    except Exception:
        return frozenset(), False
    if not isinstance(record, dict) or record.get("decision") != "approved":
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


def link_for_plugin_call(caller: str, parent_request_id: object) -> CallLink | None:
    """Chain link for a delivery asked for by ``caller`` over PLUGIN_TO_PLUGIN.

    ``caller`` is the connection-bound identity. The parent id is only a key
    into the caller's own records, so another plugin's id finds nothing; a
    single-hop record of its own is flagged while it is also being called
    by another plugin.
    """
    try:
        parent = registry.lookup(caller, parent_request_id)
        if parent is not None:
            flags = set(parent.flags & {FLAG_POSSIBLE_LAUNDERING})
            if len(parent.chain) == 1 and registry.has_live_chain(caller):
                flags.add(FLAG_POSSIBLE_LAUNDERING)
            return CallLink(origin=parent.origin, caller=caller, parent=parent, flags=frozenset(flags))
        flags = frozenset({FLAG_POSSIBLE_LAUNDERING}) if registry.has_live_chain(caller) else frozenset()
        return CallLink(origin=Origin(ORIGIN_PLUGIN_SELF), caller=caller, flags=flags)
    except Exception as exc:
        _log_failure("link", exc)
        return None


def begin_call(
    target: str, call_id: str, event_type: str, event_id: str, link: CallLink | None
) -> CallRecord | None:
    if current_mode() == POLICY_OFF:
        return None
    try:
        record = registry.register(
            target=target,
            call_id=call_id,
            event_type=event_type,
            event_id=event_id,
            link=link,
            rights_of=_rights_of,
        )
        if record is None:
            # Not silent: its requests will look unattributed from here on.
            audit.record_denial(
                {"kind": "not_registered", "mode": current_mode(), "target": target,
                 "event_type": event_type, "event_id": event_id,
                 "registry_full": registry.registry_full},
                ("not_registered", target),
            )
        return record
    except Exception as exc:
        _log_failure("registration", exc)
        return None


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
    for candidate in (exc, exc.__cause__):
        code = getattr(candidate, "code", None)
        if isinstance(code, str) and _CODE.fullmatch(code):
            return code
    if isinstance(exc, TimeoutError):
        return "TIMEOUT"
    return "ERROR"


def end_call(record: CallRecord | None, code: str, duration: float) -> None:
    if record is None:
        return
    try:
        uses = registry.remove(record, linger=LINGER_SECONDS if code in _MAY_STILL_RUN else 0.0)
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
    except Exception as exc:
        _log_failure("removal", exc)


# ── uses ────────────────────────────────────────────────────────


def _judge(plugin_id: str, parent_request_id: object, capability: str, operation: str, mode: str) -> Decision:
    record = registry.lookup(plugin_id, parent_request_id)
    flags: set[str] = set()
    if record is None:
        chain: tuple[str, ...] = (plugin_id,)
        flags.add(FLAG_UNATTRIBUTED)
    else:
        chain = record.chain
        flags.update(record.flags & {FLAG_POSSIBLE_LAUNDERING})
        if record.ended_at is not None:
            flags.add(FLAG_AFTER_RETURN)
    # Attributed to the plugin alone while another plugin is calling it.
    # Two concurrent multi-hop chains are not compared (a P2 question).
    if len(chain) == 1 and registry.has_live_chain(plugin_id):
        flags.add(FLAG_POSSIBLE_LAUNDERING)
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
    notable = flags & {FLAG_POSSIBLE_LAUNDERING, FLAG_AFTER_RETURN}
    if decision.allowed and not notable:
        if record is None:
            audit.count_unattributed(plugin_id, capability, mode=mode)
        else:
            registry.note_use(record, capability)
    else:
        verdict = "allow" if decision.allowed else ("deny" if reject else "would_deny")
        entry = {
            "kind": "decision",
            "mode": mode,
            "plugin": plugin_id,
            "operation": operation,
            "capability": capability,
            "chain": list(chain),
            "verdict": verdict,
            "reason": decision.reason,
            "blocked_by": list(decision.blocked_by),
            "flags": sorted(flags),
            "trace_id": record.trace_id if record else None,
            "call_id": record.call_id if record else None,
            "depth": record.depth if record else 0,
            "origin_kind": record.origin.kind if record else ORIGIN_PLUGIN_SELF,
            "origin_ref": record.origin.ref if record else None,
        }
        audit.record_denial(
            entry,
            (plugin_id, capability, verdict, decision.reason, decision.blocked_by, tuple(sorted(flags))),
        )
    if reject:
        raise CapabilityNotGranted(decision)
    return decision


def check(plugin_id: str, parent_request_id: object, capability: str, operation: str) -> Decision | None:
    """Judge one use. Returns None when the policy is off or the check failed.

    Only an enforcing stage can make this raise, and then only
    ``ServerDomainError(CAPABILITY_NOT_GRANTED)``.
    """
    mode = current_mode()
    if mode == POLICY_OFF:
        return None
    try:
        return _judge(plugin_id, parent_request_id, capability, operation, mode)
    except CapabilityNotGranted as refused:
        from plugin.server.domain.errors import ServerDomainError

        error = not_granted_error(refused.decision)
        raise ServerDomainError(
            code=CAPABILITY_NOT_GRANTED,
            message=str(error["message"]),
            status_code=403,
            details=error["details"],
        ) from None
    except Exception as exc:
        _log_failure("check", exc)
        return None


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
    "LINGER_SECONDS",
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
