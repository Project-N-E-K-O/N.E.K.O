"""The one host entry point for call-chain permission decisions (observe only).

Judged uses (the RPC operations in ``CAPABILITY_BY_OPERATION`` and the
MESSAGE_PUSH route) all go through ``check``. It reads only the host's call
registry and the permission store (the same resolved states the plugin
process is sent, see ``permission_service.resolved_states``), never
plugin-supplied ``_ctx`` or views. It runs whatever the master switch says,
and never refuses a call yet (``REFUSES_CALLS``); a failure in here is logged
by type and ignored, so the caller sees exactly what it saw before.
Permission ids come from ``plugin.sdk.shared.permissions``.

``record_system_uses`` writes what a plugin process reports about its own
``system.*`` uses (``plugin/core/system_audit.py``) to the same audit.
"""

from __future__ import annotations

import asyncio
import functools
import re
import time

from plugin.core.call_registry import CallLink, CallRecord, CallRegistry, Origin
from plugin.core.capability_policy import Decision, current_mode, decide, is_enforced
from plugin.sdk.shared.permissions import (
    PERMISSIONS,
    PLUGIN_PERMISSION_DENIED,
    legacy_name,
)
from plugin.logging_config import get_logger
from plugin.server.application.plugins import call_audit

logger = get_logger("server.plugins.capability_gate")

registry = CallRegistry()

# Looked up in the vocabulary, so an id that leaves it fails at import.
MEMORY_READ = PERMISSIONS["plugin_sdk.main_agent.memory:read"].id
CONVERSATIONS_READ = PERMISSIONS["plugin_sdk.main_agent.conversations:read"].id
MESSAGES_PUSH = PERMISSIONS["plugin_sdk.main_agent.messages:push"].id

# One row per judged control-RPC operation; all wrapped the same way.
CAPABILITY_BY_OPERATION = {
    "MEMORY_QUERY": MEMORY_READ,
    "MEMORY_SEARCH": MEMORY_READ,
    "MEMORY_PERSONA_GET": MEMORY_READ,
    "USER_CONTEXT_GET": MEMORY_READ,
    "CONVERSATION_REPLY_GET": CONVERSATIONS_READ,
    "CONVERSATION_INTERACTION_GET": CONVERSATIONS_READ,
}

# Codes the host and the standard executor produce; anything else is "ERROR".
_RESULT_CODES = frozenset({
    "PLUGIN_EXECUTION_FAILED", "DEADLINE_EXCEEDED", "OPERATION_TIMEOUT", "CANCELLED",
    "CONNECTION_CLOSED", "TRANSPORT_ERROR", "TRANSPORT_TIMEOUT", "BUSY",
    "SERIALIZATION_FAILED", "PLUGIN_UNAVAILABLE", "PLUGIN_NOT_READY",
    "CALL_DEPTH_EXCEEDED", "REQUEST_FAILED", "INVALID_REQUEST", "UNKNOWN_OPERATION",
    "INTERNAL_ERROR", "PLUGIN_OUTPUT_SCHEMA_VIOLATION", PLUGIN_PERMISSION_DENIED,
})
_DEDUP_SECONDS = 60.0
_last_written: dict[tuple, float] = {}
# After a failed grant read, do not retry the store on the event loop for a
# while; the last record read is used meanwhile.
_CONSENT_RETRY_SECONDS = 5.0
_consent_failed_at = -_CONSENT_RETRY_SECONDS

# Whether the gate refuses the uses it judges denied. Observe only for now:
# every audit record says ``enforcing: False``. ``mode`` keeps its meaning
# (what the master switch says), so it is not proof that a call was refused.
REFUSES_CALLS = False


def _enforcing(refused: bool) -> bool:
    """The audit ``enforcing`` field: whether a refusal was carried out."""
    return REFUSES_CALLS and refused


def granted_capabilities(plugin_id: str) -> tuple[frozenset[str], bool]:
    """Granted permission ids, and whether the grants could be read.

    Only a ``granted`` state counts. When the read fails, the plugin's last
    record read is used (none: nothing granted) and the second value is False.
    """
    global _consent_failed_at
    from plugin.server.application.plugins.permission_service import resolved_states

    backing_off = time.monotonic() - _consent_failed_at < _CONSENT_RETRY_SECONDS
    _, states, read_failed = resolved_states(plugin_id, read=not backing_off)
    if read_failed and not backing_off:
        # The store is unreadable for everyone: back off for everyone.
        _consent_failed_at = time.monotonic()
    granted = frozenset(
        permission for permission, state in states.items() if state.status == "granted"
    )
    return granted, not read_failed


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
        "rights": _wire_rights(record.rights),
    }


def _wire_rights(rights: frozenset[str] | None) -> list[str] | None:
    """Permission ids plus, for one release, their earlier names."""
    if rights is None:
        return None
    return sorted(rights | {name for name in map(legacy_name, rights) if name})


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
        # No use is refused while REFUSES_CALLS is False; once refusals are
        # carried out, pass whether this call had one.
        base = {"mode": current_mode(), "enforcing": _enforcing(False), "trace_id": record.trace_id, "call_id": record.call_id,
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
    """Judge one use and audit it. Never raises; returns None on failure."""
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
            # An allowance based on the last record read (store unreadable)
            # is also audited below, so it can be told apart.
            if "consent_unavailable" not in flags:
                return decision
        verdict = "allow" if decision.allowed else "would_deny"
        key = (plugin_id, capability, verdict, decision.reason, decision.blocked_by, tuple(sorted(flags)))
        now = time.monotonic()
        if now - _last_written.get(key, -_DEDUP_SECONDS) >= _DEDUP_SECONDS:
            if len(_last_written) > 4096:
                _last_written.clear()
            _last_written[key] = now
            call_audit.record({
                "kind": "decision", "mode": current_mode(),
                "enforcing": _enforcing(not decision.allowed and is_enforced(capability)),
                "plugin": plugin_id, "operation": operation,
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


# What a plugin process may report about its own system.* uses. The report is
# the plugin's own word (its process could also stay silent, and it can write
# files anyway): it is bounded and reduced to fixed vocabularies before it
# reaches the audit file, but the privacy reduction of a path target is the
# SDK's (system_audit.redact_path) and is not redone here.
SYSTEM_USE_PERMISSIONS = frozenset({
    PERMISSIONS["system.network:connect"].id,
    PERMISSIONS["system.process:spawn"].id,
    PERMISSIONS["system.fs:read"].id,
    PERMISSIONS["system.fs:write"].id,
})
_SYSTEM_USE_FLAGS = frozenset({"unattributed", "loopback"})
_SYSTEM_USE_BATCH_LIMIT = 100
_SYSTEM_USE_PER_MINUTE = 600
_SYSTEM_USE_WINDOW = 60.0
_SYSTEM_USE_REASONS = frozenset({"denied", "undecided"})
# plugin_id -> [window start, entries written, lost not yet reported, loss reported in this window]
_system_use_budget: dict[str, list] = {}
# Printable text without line breaks or quotes that could fake a log line.
_TARGET = re.compile(r"[^\x00-\x1f\x7f\"\\]{1,200}")


def _system_use_target(value: object) -> str:
    return value if isinstance(value, str) and _TARGET.fullmatch(value) else "<invalid>"


def _system_use_entry(plugin_id: str, item: object, mode: str) -> dict | None:
    """The audit entry for one reported use, or None when it is malformed."""
    if not isinstance(item, dict):
        return None
    permission = item.get("permission")
    # isinstance first: an unhashable value must not reach the set lookups.
    if not isinstance(permission, str) or permission not in SYSTEM_USE_PERMISSIONS:
        return None
    would_deny = item.get("verdict") == "would_deny"
    reason = item.get("reason")
    flags = item.get("flags")
    flags = {flag for flag in flags if isinstance(flag, str) and flag in _SYSTEM_USE_FLAGS} if isinstance(flags, list) else set()
    return {
        "kind": "system_use", "mode": mode,
        # Whether the process refused it (system_audit.REFUSES_SYSTEM_USES).
        "enforcing": would_deny and item.get("enforcing") is True,
        "plugin": plugin_id, "capability": permission,
        "operation": _identifier(item.get("event")),
        "target": _system_use_target(item.get("target")),
        "verdict": "would_deny" if would_deny else "allow",
        "reason": reason if would_deny and isinstance(reason, str) and reason in _SYSTEM_USE_REASONS else None,
        "flags": sorted(flags) + ["plugin_reported"],
    }


def flush_system_use_loss(plugin_id: str) -> None:
    """Report what is still counted as lost for ``plugin_id`` (its connection
    is closing, so no later batch would carry it). Never raises."""
    try:
        state = _system_use_budget.pop(plugin_id, None)
        if state is not None and state[2]:
            call_audit.record({"kind": "system_use_lost", "plugin": plugin_id, "count": state[2]})
    except Exception as exc:
        logger.warning("system use loss flush failed: error_type={}", type(exc).__name__)


def record_system_uses(plugin_id: str, message: object) -> int:
    """Audit one ``SYSTEM_AUDIT`` batch from ``plugin_id``'s process.

    Returns how many entries were written. Never raises. Malformed items are
    skipped one by one. Per plugin and minute at most ``_SYSTEM_USE_PER_MINUTE``
    uses are written; the rest, items past ``_SYSTEM_USE_BATCH_LIMIT`` and what
    the process itself could not send are counted, and reported in at most one
    ``system_use_lost`` entry a minute.
    """
    now = time.monotonic()
    state = _system_use_budget.get(plugin_id)
    if state is None or now - state[0] >= _SYSTEM_USE_WINDOW:
        state = [now, 0, state[2] if state is not None else 0, False]
    written = 0
    try:
        if not isinstance(message, dict):
            return 0
        items = message.get("items")
        items = items if isinstance(items, list) else []
        lost = message.get("dropped")
        if isinstance(lost, int) and not isinstance(lost, bool) and lost > 0:
            state[2] += lost
        state[2] += max(0, len(items) - _SYSTEM_USE_BATCH_LIMIT)
        mode = current_mode()
        for item in items[:_SYSTEM_USE_BATCH_LIMIT]:
            try:
                entry = _system_use_entry(plugin_id, item, mode)
            except Exception:
                entry = None  # a malformed item is skipped, the rest still count
            if entry is None:
                continue
            if state[1] >= _SYSTEM_USE_PER_MINUTE:
                state[2] += 1
                continue
            state[1] += 1
            call_audit.record(entry)
            written += 1
        if state[2] and not state[3]:
            call_audit.record({"kind": "system_use_lost", "plugin": plugin_id, "count": state[2]})
            state[2], state[3] = 0, True
        return written
    except Exception as exc:
        logger.warning("system use audit failed: error_type={}", type(exc).__name__)
        return written
    finally:
        # Saved whatever happened above, so a failing batch still uses budget.
        if plugin_id not in _system_use_budget and len(_system_use_budget) > 1024:
            _system_use_budget.clear()
        _system_use_budget[plugin_id] = state
