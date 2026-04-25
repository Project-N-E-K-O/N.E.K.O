"""Unified chokepoint for ``session.messages.append()``.

Background (see P24_BLUEPRINT §12.5 + AGENT_NOTES §3A A7 revision)
-------------------------------------------------------------------
``session.messages`` carries the invariant "timestamps monotonically
non-decreasing". Downstream code (time separator bars, recent-history
slicing in prompt_builder, dialog_template time.advance computation
in P23 export, UI chronological rendering) assumes it.

The guard function ``chat_messages.check_timestamp_monotonic`` existed
since P09, but was only called from **2** router entry points
(``POST /messages`` manual add + ``PATCH /messages/{id}/timestamp``).
**5 other write paths bypassed the check** (SSE ``/chat/send`` user
append, SSE assistant append, ``/chat/inject_system``, ``auto_dialog``
user append, and SimUser via chat_runner). This went undetected until
a user manually rewound the virtual clock to the past and continued
chatting — downstream silently produced negative time deltas, broken
dialog_template exports, and scrambled UI time separators.

The fix is **structural**, not procedural: all writes must go through
this module's :func:`append_message`, which enforces monotonicity
with a configurable policy. A ``.cursor/rules/single-append-message.mdc``
pre-commit blocks raw ``session.messages.append(`` outside the helper
whitelist.

See also
--------
* ``~/.cursor/skills/single-writer-choke-point/SKILL.md`` — general pattern
* ``P24_BLUEPRINT §12.5`` — the user-reported bug that exposed this
* ``.cursor/rules/single-append-message.mdc`` — CI enforcement
"""
from __future__ import annotations

import copy
from dataclasses import dataclass
from typing import Any, Literal, Optional, TYPE_CHECKING

from tests.testbench.chat_messages import (
    _parse_stored_ts,
    check_timestamp_monotonic,
)
from tests.testbench.logger import python_logger
from tests.testbench.pipeline import diagnostics_store
from tests.testbench.pipeline.diagnostics_ops import DiagnosticsOp

if TYPE_CHECKING:
    from tests.testbench.session_store import Session

ViolationPolicy = Literal[
    "raise",   # Block the write, raise TimestampOutOfOrder (strict mode)
    "coerce",  # Auto-bump ts to prev_ts, log warning (forgiving mode)
    "warn",    # Let through unchanged, log warning, downstream handles
]


@dataclass
class AppendResult:
    """Return value of :func:`append_message`.

    ``msg`` is the (possibly ts-coerced) message dict as written into
    ``session.messages``. ``coerced`` is ``None`` on the happy path; on
    violation handled by the ``coerce`` policy it's a small dict the
    caller can forward to the UI (e.g. SSE ``warning`` frame → composer
    toast). Keeping this info out of ``msg`` itself means the persisted
    archive / export don't accumulate transient "_coerced" fields.
    """

    msg: dict[str, Any]
    coerced: Optional[dict[str, Any]] = None


__all__ = [
    "AppendResult",
    "TimestampOutOfOrder",
    "ViolationPolicy",
    "append_message",
    "append_messages_bulk",
]


class TimestampOutOfOrder(Exception):
    """Raised by :func:`append_message` when ``on_violation="raise"``.

    Callers on the manual-edit path (``POST /messages``) wrap this in
    HTTPException(422) for UI display. SSE / Auto / Script paths use
    ``coerce`` and never see this exception.
    """

    def __init__(self, code: str, detail: str) -> None:
        super().__init__(detail)
        self.code = code
        self.detail = detail


def append_message(
    session: "Session",
    msg: dict[str, Any],
    *,
    on_violation: ViolationPolicy = "coerce",
) -> AppendResult:
    """Append ``msg`` to ``session.messages`` with monotonicity guarantee.

    All code paths in the repo MUST go through this — raw
    ``session.messages.append(msg)`` is pre-commit-blocked outside this
    module's whitelist (see .cursor/rules/single-append-message.mdc).

    Policy selection by context
    ---------------------------
    * ``on_violation="raise"`` — manual user actions (POST /messages).
      User intent is explicit; a 422 with a specific code gives
      actionable feedback.
    * ``on_violation="coerce"`` — SSE streams, auto-dialog, scripts.
      The operation must not fail mid-stream; bumping the timestamp
      to the previous one preserves the invariant. Returned
      ``AppendResult.coerced`` tells the caller to surface a user-
      visible warning (SSE frame, toast).
    * ``on_violation="warn"`` — almost never used. Downstream code
      already assumes monotonicity, so letting non-monotonic writes
      through is risky. Reserved for edge cases where we truly don't
      care about order (none in current codebase).

    Return value
    ------------
    :class:`AppendResult` with ``.msg`` (the dict written, possibly with
    coerced ``timestamp``) and ``.coerced`` (``None`` on happy path, or a
    dict ``{original_ts, coerced_ts, code, role}`` callers forward to UI
    for user-visible feedback — SSE warning frame / toast).

    Side effects
    ------------
    * Mutates ``msg["timestamp"]`` in-place on coerce (keeps the stored
      message monotonic; the original ts is returned via ``coerced`` dict
      so it's recoverable for UI).
    * Writes a ``diagnostics_store.record_internal(op=TIMESTAMP_COERCED)``
      entry for forensic replay even when the caller doesn't surface
      the coerce (e.g. non-SSE pathways).
    """
    # Parse the incoming ts; `_parse_stored_ts` handles both ISO str and
    # tz-naive datetime, returning None if the field is missing/malformed.
    parsed_ts = _parse_stored_ts(msg)
    coerced_info: Optional[dict[str, Any]] = None

    if parsed_ts is not None:
        err = check_timestamp_monotonic(
            session.messages, len(session.messages), parsed_ts,
        )
        if err is not None:
            code, detail = err
            if on_violation == "raise":
                raise TimestampOutOfOrder(code, detail)

            prev_ts = None
            if session.messages:
                prev_ts = _parse_stored_ts(session.messages[-1])

            if on_violation == "coerce" and prev_ts is not None:
                original_ts_iso = msg.get("timestamp")
                coerced_ts_iso = prev_ts.isoformat()
                msg["timestamp"] = coerced_ts_iso
                coerced_info = {
                    "original_ts": str(original_ts_iso),
                    "coerced_ts": coerced_ts_iso,
                    "code": code,
                    "role": msg.get("role"),
                    "reason": "virtual_clock_rewound",
                }
                try:
                    diagnostics_store.record_internal(
                        DiagnosticsOp.TIMESTAMP_COERCED,
                        (
                            f"消息时间戳被自动前移以保证单调: "
                            f"{original_ts_iso} → {coerced_ts_iso} "
                            f"(角色: {msg.get('role') or 'unknown'})"
                        ),
                        level="warning",
                        session_id=getattr(session, "id", None),
                        detail=coerced_info,
                    )
                except Exception:  # noqa: BLE001
                    python_logger().exception(
                        "messages_writer: record_internal failed "
                        "during coerce (non-fatal)",
                    )
            else:  # "warn" or prev_ts missing (edge case)
                # ``check_timestamp_monotonic`` already short-circuits when
                # the prior message's ts is unparseable, so reaching this
                # branch with ``on_violation == 'coerce' and prev_ts is
                # None`` is *defense-in-depth*: if a future refactor of
                # the predecessor check ever returns an error without a
                # parseable prev_ts, we still want the audit log to make
                # the actual fallback policy obvious instead of reporting
                # ``policy=coerce`` while silently letting the violation
                # through (GH AI-review issue #10).
                actual_policy = (
                    on_violation
                    if (on_violation == "warn" or prev_ts is not None)
                    else "warn"
                )
                fallback_reason = (
                    "prev_ts_unparseable"
                    if (on_violation == "coerce" and prev_ts is None)
                    else None
                )
                try:
                    diagnostics_store.record_internal(
                        DiagnosticsOp.TIMESTAMP_COERCED,
                        f"消息时间戳违反单调规则 (策略={actual_policy}): {detail}",
                        level="warning",
                        session_id=getattr(session, "id", None),
                        detail={
                            "code": code,
                            "policy": actual_policy,
                            "requested_policy": on_violation,
                            "fallback_reason": fallback_reason,
                        },
                    )
                except Exception:  # noqa: BLE001
                    python_logger().exception(
                        "messages_writer: record_internal failed during warn (non-fatal)",
                    )

    session.messages.append(msg)
    return AppendResult(msg=msg, coerced=coerced_info)


def append_messages_bulk(
    session: "Session",
    msgs: list[dict[str, Any]],
    *,
    on_violation: ViolationPolicy = "coerce",
) -> list[AppendResult]:
    """Append multiple messages in order, applying ``on_violation`` per element.

    Mostly a convenience for restore / import paths that replay a whole
    list; internally just loops :func:`append_message`. Deep-copies each
    msg before append so callers can't mutate post-append via their
    original references.
    """
    return [
        append_message(session, copy.deepcopy(m), on_violation=on_violation)
        for m in msgs
    ]
