"""The one host entry point for call-chain capability decisions (observe only).

Judged uses (the RPC operations in ``CAPABILITY_BY_OPERATION`` and the
MESSAGE_PUSH route) all go through ``check``. It reads only the host's call
registry and the consent store, never plugin-supplied ``_ctx`` or views. It
never refuses a call, and a failure in here is logged by type and ignored, so
the caller sees exactly what it saw before. See docs/design/plugin-call-chain.md.
"""

from __future__ import annotations

import asyncio
import functools
import re
import time

from plugin.core.call_registry import CallLink, CallRecord, CallRegistry, Origin
from plugin.core.capability_policy import Decision, current_mode, decide
from plugin.logging_config import get_logger
from plugin.server.application.plugins import call_audit

logger = get_logger("server.plugins.capability_gate")

registry = CallRegistry()

# One row per judged control-RPC operation; all wrapped the same way.
CAPABILITY_BY_OPERATION = {
    "MEMORY_QUERY": "memory.read",
    "MEMORY_SEARCH": "memory.read",
    "MEMORY_PERSONA_GET": "memory.read",
    "USER_CONTEXT_GET": "memory.read",
    "CONVERSATION_REPLY_GET": "conversations.read",
    "CONVERSATION_INTERACTION_GET": "conversations.read",
}

# Codes the host and the standard executor produce; anything else is "ERROR".
_RESULT_CODES = frozenset({
    "PLUGIN_EXECUTION_FAILED", "DEADLINE_EXCEEDED", "OPERATION_TIMEOUT", "CANCELLED",
    "CONNECTION_CLOSED", "TRANSPORT_ERROR", "TRANSPORT_TIMEOUT", "BUSY",
    "SERIALIZATION_FAILED", "PLUGIN_UNAVAILABLE", "PLUGIN_NOT_READY",
    "CALL_DEPTH_EXCEEDED", "REQUEST_FAILED", "INVALID_REQUEST", "UNKNOWN_OPERATION",
    "INTERNAL_ERROR", "PLUGIN_OUTPUT_SCHEMA_VIOLATION",
})
_DEDUP_SECONDS = 60.0
_last_written: dict[tuple, float] = {}
# After a failed consent read, do not retry the file on the event loop for a while.
_CONSENT_RETRY_SECONDS = 5.0
_consent_failed_at = -_CONSENT_RETRY_SECONDS


def granted_capabilities(plugin_id: str) -> tuple[frozenset[str], bool]:
    """Approved capabilities from plugin_consents.json, and whether the read worked."""
    global _consent_failed_at
    if time.monotonic() - _consent_failed_at < _CONSENT_RETRY_SECONDS:
        return frozenset(), False
    try:
        from plugin.server.infrastructure.plugin_consent import consent_store

        record = consent_store.read(plugin_id)
    except Exception:  # the whole store is unreadable: back off for everyone
        _consent_failed_at = time.monotonic()
        return frozenset(), False
    try:
        if record.get("decision") != "approved":
            return frozenset(), True
        # Missing means none, as in consent_policy.derive_consent; a wrong type is malformed.
        approved = record.get("approved_capabilities") or []
        if not isinstance(approved, list) or not all(isinstance(c, str) for c in approved):
            return frozenset(), False  # malformed record: unknown, for this plugin only
        return frozenset(approved), True
    except Exception:  # only this plugin's record is malformed
        return frozenset(), False


def link_for_plugin_call(caller: str, parent_request_id: object) -> CallLink | None:
    """Link for a PLUGIN_TO_PLUGIN delivery; ``caller`` is the connection identity.

    Never raises: on failure the chain starts at the caller.
    """
    try:
        return _link_for_plugin_call(caller, parent_request_id)
    except Exception as exc:
        logger.warning("call-chain link failed: error_type={}", type(exc).__name__)
        # Keep the caller on the chain rather than looking host-initiated.
        return CallLink(Origin("plugin_self"), caller=caller)


def _link_for_plugin_call(caller: str, parent_request_id: object) -> CallLink:
    parent = registry.lookup(caller, parent_request_id)
    if parent is not None and len(parent.chain) > 1:
        return CallLink(parent.origin, caller=caller, parent=parent, flags=parent.flags)
    # Unknown parent, or one of its own direct deliveries: the chain starts at
    # the caller. Suspicious if another plugin is calling it right now.
    flags = frozenset({"possible_laundering"}) if registry.has_live_chain(caller) else frozenset()
    origin = parent.origin if parent is not None else Origin("plugin_self")
    return CallLink(origin, caller=caller, parent=parent, flags=flags)


def begin_call(target: str, call_id: str, event_type: str, event_id: str,
               link: CallLink | None) -> CallRecord | None:
    record = None
    try:
        record = registry.register(target, call_id, event_type, event_id, link)
        if record is not None:
            rights = None
            for plugin_id in dict.fromkeys(record.chain):
                granted, ok = granted_capabilities(plugin_id)
                if not ok:
                    rights = None
                    break
                rights = granted if rights is None else rights & granted
            record.rights = rights
        return record
    except Exception as exc:
        logger.warning("call-chain registration failed: error_type={}", type(exc).__name__)
        if record is not None:
            registry.remove(record)  # the caller will not call end_call
        return None


def chain_payload(record: CallRecord) -> dict:
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
    if isinstance(exc, asyncio.CancelledError):
        return "CANCELLED"
    for candidate in (exc, exc.__cause__):
        code = getattr(candidate, "code", None)
        if code in _RESULT_CODES:
            return code
    return "TIMEOUT" if isinstance(exc, TimeoutError) else "ERROR"


# Word characters (Unicode, so Chinese entry ids pass), ``.:/-``, or empty;
# no whitespace, quotes or control characters that could fake a log line.
_IDENTIFIER = re.compile(r"[\w.:/\-]{0,128}")


def _identifier(value: str) -> str:
    """Event names come from the calling plugin: keep only identifier-shaped ones."""
    return value if isinstance(value, str) and _IDENTIFIER.fullmatch(value) else "<invalid>"


def end_call(record: CallRecord | None, code: str, started: float) -> None:
    if record is None:
        return
    try:
        uses = registry.remove(record)
        base = {"mode": current_mode(), "trace_id": record.trace_id, "call_id": record.call_id,
                "parent_call_id": record.parent_call_id, "depth": record.depth, "target": record.target}
        call_audit.record({
            "kind": "forward", **base, "caller": record.caller, "event_type": _identifier(record.event_type),
            "event_id": _identifier(record.event_id), "origin_kind": record.origin.kind, "origin_ref": record.origin.ref,
            "result": code, "duration_ms": round((time.monotonic() - started) * 1000, 1),
            "flags": sorted(record.flags),
        })
        if uses:
            call_audit.record({"kind": "allow_summary", **base, "counts": dict(sorted(uses.items()))})
    except Exception as exc:
        logger.warning("call-chain removal failed: error_type={}", type(exc).__name__)


def check(plugin_id: str, parent_request_id: object, capability: str, operation: str) -> Decision | None:
    """Judge one use and audit it. Never raises; returns None when off or on failure."""
    if current_mode() == "off":
        return None
    try:
        record = registry.lookup(plugin_id, parent_request_id)
        flags = set()
        if record is None:
            chain = (plugin_id,)
            flags.add("unattributed")
        else:
            chain = record.chain
            flags.update(record.flags)
        if len(chain) == 1 and registry.has_live_chain(plugin_id):
            flags.add("possible_laundering")

        def granted(member: str) -> frozenset[str]:
            value, ok = granted_capabilities(member)
            if not ok:
                flags.add("consent_unavailable")
            return value

        decision = decide(capability, chain, granted)
        if not decision.judged:
            return decision
        if decision.allowed and "possible_laundering" not in flags:
            if record is not None:
                registry.note_use(record, capability)
            return decision
        verdict = "allow" if decision.allowed else "would_deny"
        key = (plugin_id, capability, verdict, decision.reason, decision.blocked_by, tuple(sorted(flags)))
        now = time.monotonic()
        if now - _last_written.get(key, -_DEDUP_SECONDS) >= _DEDUP_SECONDS:
            if len(_last_written) > 4096:
                _last_written.clear()
            _last_written[key] = now
            call_audit.record({
                "kind": "decision", "mode": current_mode(), "plugin": plugin_id, "operation": operation,
                "capability": capability, "chain": list(chain), "verdict": verdict,
                "reason": decision.reason, "blocked_by": list(decision.blocked_by), "flags": sorted(flags),
                "trace_id": record.trace_id if record else None, "call_id": record.call_id if record else None,
                "origin_kind": record.origin.kind if record else "plugin_self",
            })
        return decision
    except Exception as exc:
        logger.warning("call-chain check failed: error_type={}", type(exc).__name__)
        return None


def capability_checked(capability: str, handler):
    """Wrap a control-RPC handler with the shared decision entry point."""

    @functools.wraps(handler)  # keeps the name and markers such as owns_deadline
    async def checked(ctx, request):
        check(ctx.from_plugin, ctx.parent_request_id, capability, str(request.get("type") or ""))
        return await handler(ctx, request)

    checked.__capability__ = capability
    return checked
