"""Single-writer chokepoint helper for prompt-injection scan-and-record.

Context
-------
Before Day 2 polish r5, prompt-injection detection was wired ad-hoc at
a single chat-send entry point (``chat_runner.stream_send``). Subagent C's
r5 audit found **five P0 coverage gaps** — tester-editable text fields
that reach the LLM wire without any detect-and-record step:

1. ``simulate_avatar_interaction.payload.text_context`` (avatar event
   "文本上下文" textarea)
2. ``simulate_agent_callback.payload.callbacks[]`` (merged)
3. ``simulate_proactive.payload.topic``  (r5-new "主动对话话题")
4. ``chat_router.inject_system.body.content`` ("[注入 sys]" button)
5. ``auto_dialog._run_simuser_step`` SimUser draft + persona_hint +
   extra_hint (goes through ``stream_send`` via ``user_content=None``
   rerun path, so the normal chat-send scan never sees it)

L33 / L36 §7.25 single-chokepoint pattern — instead of copy-pasting the
"detect → record_internal → log-skip" snippet at 5+ sites, **all callers
funnel through** :func:`scan_and_record` here. This means:

* Any future pattern-library change (new jailbreak variant, severity
  tuning, cross-lang normalisation) lands in one file.
* The dict shape we write to ``diagnostics_store`` stays uniform — the
  Errors subpage filter ``op_type=prompt_injection_suspected`` can rely
  on a single ``detail`` schema.
* A test that stubs :func:`scan_and_record` (e.g. to inject hits) covers
  every entry point with one monkeypatch.

Design
------
* **Never filter / rewrite text** (repeats the top-of-file rule in
  ``prompt_injection_detect.py``). We detect + record only.
* **Never raise out of this helper** — injection detection is advisory;
  a pattern-library bug must not take down external_events or
  inject_system. All exceptions are caught and funnelled to
  ``python_logger().warning`` so the audit trail survives.
* **Idempotent** — callers may call this helper once per field, or once
  per concatenated blob. We don't dedupe across calls on purpose; if
  the same text appears in two ``source`` slots (e.g. avatar ``raw`` vs
  ``normalized``), both records exist, which is the right audit signal.
* **Hits → dict detail** — the ``detail`` payload is the union of
  ``summarize(hits)`` plus ``hits[:20].to_dict()`` plus caller-provided
  ``extra``. The tail-20 cap matches the pattern from
  ``chat_runner.stream_send`` and keeps ring-buffer footprint bounded.
"""
from __future__ import annotations

from typing import Any, Optional

from tests.testbench.logger import python_logger


def scan_and_record(
    text: str,
    *,
    source: str,
    session_id: Optional[str] = None,
    category: Optional[str] = None,
    extra: Optional[dict[str, Any]] = None,
) -> list[Any]:
    """Scan ``text`` for prompt-injection patterns and record a
    diagnostics entry if any hits are found.

    Parameters
    ----------
    text
        The tester-supplied free-form text to scan. ``None`` / empty is
        a no-op.
    source
        Dotted source identifier used both in the log ``detail.source``
        field AND in the human-readable diagnostics message. Use the
        same namespace convention as existing security ops: ``<subsystem>
        .<operation>.<field>`` — e.g. ``avatar_event.text_context.raw``,
        ``agent_callback.callbacks``, ``chat.inject_system``,
        ``auto_dialog.simuser.persona_hint``.
    session_id
        Forwarded to ``diagnostics_store.record_internal`` so the Errors
        subpage's session filter works.
    category
        Free-form tag for UI grouping — not required; if set, appended
        to the diagnostics message in parentheses.
    extra
        Caller-provided fields merged into ``detail``. Use this for
        fields the diagnostics consumer needs but that aren't part of
        the uniform schema (e.g. avatar ``payload.interaction_id``
        for cross-referencing the event trace).

    Returns
    -------
    list of InjectionHit
        The raw hits (possibly empty). Callers can use this to attach
        non-log behavior such as SSE ``injection_warning`` frames
        (``chat_runner.stream_send`` does this for the user-typed path).
        Do NOT use the return value to decide whether to mutate /
        block the content — this helper is advisory.
    """
    blob = text if isinstance(text, str) else ""
    if not blob:
        return []

    try:
        from tests.testbench.pipeline import prompt_injection_detect as _pid
    except Exception as exc:  # noqa: BLE001
        python_logger().warning(
            "injection_audit.scan_and_record: failed to import "
            "prompt_injection_detect (%s: %s) — scan skipped for "
            "source=%r", type(exc).__name__, exc, source,
        )
        return []

    try:
        hits = _pid.detect(blob)
    except Exception as exc:  # noqa: BLE001
        python_logger().warning(
            "injection_audit.scan_and_record: detect() raised %s: %s — "
            "scan skipped for source=%r", type(exc).__name__, exc, source,
        )
        return []

    if not hits:
        return []

    try:
        from tests.testbench.pipeline import diagnostics_store as _ds
        from tests.testbench.pipeline.diagnostics_ops import DiagnosticsOp

        summary = _pid.summarize(hits)
        categories = sorted({h.category for h in hits})
        msg_suffix = f" ({category})" if category else ""
        # Human message mirrors ``chat_runner.stream_send``'s wording
        # so the Errors subpage rows feel uniform.
        message = (
            f"{source} 命中 {len(hits)} 条注入模式"
            + msg_suffix
            + ": "
            + ", ".join(h.pattern_id for h in hits[:5])
            + (" ..." if len(hits) > 5 else "")
        )
        detail: dict[str, Any] = {
            "source": source,
            "category": category,
            "hits_count": len(hits),
            "categories": categories,
            "hits": [h.to_dict() for h in hits[:20]],
            "summary": summary,
            "content_length": len(blob),
        }
        if extra:
            # Caller-provided fields win on conflict — this is on
            # purpose: the site knows its own schema better than us.
            detail.update(extra)
        _ds.record_internal(
            DiagnosticsOp.PROMPT_INJECTION_SUSPECTED,
            message,
            level="warning",
            session_id=session_id,
            detail=detail,
        )
    except Exception as exc:  # noqa: BLE001
        python_logger().warning(
            "injection_audit.scan_and_record: record_internal failed "
            "(%s: %s) for source=%r — hits detected but not persisted",
            type(exc).__name__, exc, source,
        )

    return list(hits)


def scan_many(
    fields: dict[str, str],
    *,
    source_prefix: str,
    session_id: Optional[str] = None,
    category: Optional[str] = None,
    extra: Optional[dict[str, Any]] = None,
) -> dict[str, list[Any]]:
    """Convenience: scan each ``{name: text}`` entry separately.

    Every field is recorded as its own diagnostics entry with source
    ``{source_prefix}.{name}`` — so ``source_prefix='judge.chat'`` with
    ``fields={'system_prompt': ..., 'user_input': ...}`` yields two
    records: ``judge.chat.system_prompt`` and ``judge.chat.user_input``.

    Returns a ``{name: [hits]}`` dict for callers that want the per-
    field hit list without re-scanning.
    """
    out: dict[str, list[Any]] = {}
    for name, text in (fields or {}).items():
        out[name] = scan_and_record(
            text,
            source=f"{source_prefix}.{name}",
            session_id=session_id,
            category=category,
            extra=extra,
        )
    return out


__all__ = ["scan_and_record", "scan_many"]
