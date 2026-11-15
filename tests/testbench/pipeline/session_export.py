"""Multi-format / multi-scope session export (P23).

PLAN §P23: turn an ongoing testbench session into a shareable artifact according to
user-selected **scope** (included content) and **format** (output format). This layer is
pure Python, without acquiring ``asyncio.Lock`` or importing ``session_store``, matching
:mod:`judge_export` / :mod:`persistence` in their \"session-agnostic\"
style (§3A A11). The router (:mod:`session_router`) handles locking and sends the
``Response`` to the browser with ``Content-Disposition: attachment``.

Scope × Format matrix
-------------------

::

                       │ json  │ markdown │ dialog_template │
    ───────────────────┼───────┼──────────┼─────────────────┤
    full               │  ✓    │    ✓     │       ✗         │
    persona_memory     │  ✓    │    ✓     │       ✗         │
    conversation       │  ✓    │    ✓     │       ✓         │
    conversation_eval  │  ✓    │    ✓     │       ✗         │
    evaluations        │  ✓    │    ✓     │       ✗         │

(**11 valid combinations** total.) ``dialog_template`` intentionally supports only
``conversation`` because it converts an existing conversation into a repeatable script schema.
Including eval_results is meaningless (scripts do not run judgers), and including persona_memory
would also conflict with scripts' turn-level semantics.

Scope inclusion semantics
--------------

* ``full`` — complete serialization (reuse :func:`persistence.serialize_session`
  output) for offline analysis of an entire test session. The JSON output structure
  **matches** the ``<name>.json`` archive (aligned schema_version), allowing P23 export
  payloads to be loaded back via ``/api/session/import`` (requires the matching
  tarball; the later \"include_memory\" option embeds it as base64 in the
  payload, matching :func:`persistence.export_to_payload`).

* ``persona_memory`` — retain only persona + clock + model_config (redacted).
  Reuse the same persona to start another set of test sessions. A memory tar.gz
  may still be attached via ``include_memory=True``.

* ``conversation`` — retain only messages + essential clock/persona metadata
  (character_name/master_name), without eval_results. Share dialogue with reviewers
  or use it as the next script's input (with ``format=dialog_template``).

* ``conversation_evaluations`` — ``conversation`` + all eval_results
  + aggregate statistics: a dialogue + scoring report for reviewers to compare.

* ``evaluations`` — retain only eval_results + aggregate + dialogue metadata
  (session name/persona header), without expanding messages, for bulk comparisons
  across sessions concerned only with scoring.

Format contracts
-----------

* ``json`` — pretty-printed UTF-8, ``ensure_ascii=False`` (literal CJK),
  root ``{kind, scope, format, generated_at, session_ref, payload}``.
  ``kind=\"testbench_session_export\"`` is identical for every scope; frontend/downstream
  identification uses only ``scope``. For ``scope=full``, ``payload`` matches
  the structure of :meth:`persistence.SessionArchive.to_json_dict`.

* ``markdown`` — assemble scope-specific sections (see :func:`build_export_markdown`).
  The **Comparative gap table** reuses :func:`judge_export.build_report_markdown`'s
  \"gap_trajectory\" + \"By schema\" blocks instead of duplicating their implementation.

* ``dialog_template`` — emit the JSON schema understood by :mod:`script_runner`
  (name / description / bootstrap / turns), UTF-8 with two-space indentation.
  Derive ``turns`` from session.messages: retain assistant content as
  ``expected``; derive each user message's ``time.advance`` from the difference
  between its ``timestamp`` and the previous message's (write only >= 60 seconds,
  ignoring shorter intervals, matching :mod:`script_runner._parse_duration_text`).

API Key redaction
------------

Default ``redact_api_keys=True`` matches :mod:`persistence`; P23 also imposes a
**hard constraint** that exported ``redact_api_keys`` is always ``True``:
export deliberately shares data, so there is no plaintext-export option. The router's Pydantic body
does not even expose the switch (§3A A5: contracts may relax, security may not).

export -> import round trip
-------------------------

Only ``scope=full`` + ``format=json`` + ``include_memory=True`` output is
fully compatible with :func:`persistence.import_from_payload` (all three conditions
are required for lossless import). Other scopes intentionally do not match import format;
their payloads optimize readability / small size and omit fields such as
``archive_kind`` required by the import path.
"""
from __future__ import annotations

import base64
import copy
import json
import re
from datetime import datetime, timedelta, timezone
from typing import Any, Final, Literal

from tests.testbench.pipeline import judge_export, persistence
from tests.testbench.pipeline.judge_export import aggregate_results

# ── enums / constants ──────────────────────────────────────────────

Scope = Literal[
    "full",
    "persona_memory",
    "conversation",
    "conversation_evaluations",
    "evaluations",
]
Format = Literal["json", "markdown", "dialog_template"]

SESSION_EXPORT_SCOPES: Final[tuple[str, ...]] = (
    "full",
    "persona_memory",
    "conversation",
    "conversation_evaluations",
    "evaluations",
)
EXPORT_FORMATS: Final[tuple[str, ...]] = ("json", "markdown", "dialog_template")

#: ``(scope, format)`` allowlist. The router uses :func:`is_valid_combination`
#: for input validation; reject unsupported combinations with 400 before builders produce incomplete output.
VALID_COMBINATIONS: Final[frozenset[tuple[str, str]]] = frozenset({
    ("full", "json"),
    ("full", "markdown"),
    ("persona_memory", "json"),
    ("persona_memory", "markdown"),
    ("conversation", "json"),
    ("conversation", "markdown"),
    ("conversation", "dialog_template"),
    ("conversation_evaluations", "json"),
    ("conversation_evaluations", "markdown"),
    ("evaluations", "json"),
    ("evaluations", "markdown"),
})

EXPORT_KIND: Final[str] = "testbench_session_export"
EXPORT_SCHEMA_VERSION: Final[int] = 1

_SAFE_FILENAME_RE = re.compile(r"[^A-Za-z0-9._-]+")


class SessionExportError(ValueError):
    """Raised for invalid scope/format combinations or missing session data.

    Subclasses :class:`ValueError` so the router maps it cleanly to a
    400 via :func:`session_router._persistence_error_to_http`-style
    handling; callers outside HTTP land can ``except SessionExportError``
    without worrying about HTTPException import graph.
    """

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


# ── combination validation ─────────────────────────────────────────


def is_valid_combination(scope: str, fmt: str) -> bool:
    """Return ``True`` iff ``(scope, fmt)`` is in :data:`VALID_COMBINATIONS`."""
    return (scope, fmt) in VALID_COMBINATIONS


def ensure_valid_combination(scope: str, fmt: str) -> None:
    """Raise :class:`SessionExportError` when ``(scope, fmt)`` is not allowed."""
    if scope not in SESSION_EXPORT_SCOPES:
        raise SessionExportError(
            "UnknownScope",
            f"unknown export scope {scope!r}; expected one of "
            f"{SESSION_EXPORT_SCOPES}",
        )
    if fmt not in EXPORT_FORMATS:
        raise SessionExportError(
            "UnknownFormat",
            f"unknown export format {fmt!r}; expected one of {EXPORT_FORMATS}",
        )
    if not is_valid_combination(scope, fmt):
        raise SessionExportError(
            "InvalidCombination",
            f"combination scope={scope!r} × format={fmt!r} is not supported. "
            f"dialog_template only applies to scope=conversation; all other "
            f"scopes accept json or markdown only.",
        )


# ── filename helper ────────────────────────────────────────────────


_FORMAT_EXT: Final[dict[str, str]] = {
    "json": "json",
    "markdown": "md",
    # dialog_template uses JSON with .json extension and a 'dialog_template' prefix
    # so users can distinguish a script schema from ordinary JSON immediately.
    "dialog_template": "json",
}


def session_export_filename(
    *,
    session_name: str,
    scope: str,
    fmt: str,
    now: datetime | None = None,
) -> str:
    """Return ``tbsession_<session>_<scope>_<YYYYMMDD_HHMMSS>.<ext>``.

    Filter ``session_name`` with :data:`_SAFE_FILENAME_RE` so downloads on Windows/
    macOS/Linux avoid colon / space / Chinese-character conflicts (Chinese needs extra encoding in HTTP
    ``Content-Disposition``, so ASCII filtering is simplest).
    dialog_template uses the ``tbscript`` prefix for distinction (automatically recognized by
    downstream script_runner's user template directory).
    """
    prefix = "tbscript" if fmt == "dialog_template" else "tbsession"
    ts = (now or datetime.now()).strftime("%Y%m%d_%H%M%S")
    safe_name = _SAFE_FILENAME_RE.sub("_", session_name or "session") or "session"
    safe_scope = _SAFE_FILENAME_RE.sub("_", scope) or scope
    ext = _FORMAT_EXT.get(fmt, "txt")
    # dialog_template omits the scope segment (implicitly conversation), keeping filenames
    # shorter and avoiding redundancy.
    if fmt == "dialog_template":
        return f"{prefix}_{safe_name}_{ts}.{ext}"
    return f"{prefix}_{safe_name}_{safe_scope}_{ts}.{ext}"


# ── helpers ────────────────────────────────────────────────────────


def _now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def _safe_persona_summary(session: Any) -> dict[str, Any]:
    """Return a small, always-JSON-safe view of the persona.

    Export payloads only quote a handful of persona fields for header /
    metadata sections; the full persona dict (with long system_prompt
    blocks) still ships in ``scope=full`` via serialize_session, but we
    don't want to inline it in every other scope's metadata.
    """
    persona = getattr(session, "persona", None) or {}
    if not isinstance(persona, dict):
        return {}
    return {
        "master_name": persona.get("master_name"),
        "character_name": persona.get("character_name"),
        "language": persona.get("language"),
    }


def _session_ref(session: Any) -> dict[str, Any]:
    """Shared top-level ``session_ref`` stamp included in every export payload.

    Small, stable shape so downstream tooling can diff two exports by
    reading ``session_ref`` alone (id / name / message_count / eval_count
    / clock cursor) without scanning the full payload.
    """
    created = getattr(session, "created_at", None)
    created_iso = created.isoformat(timespec="seconds") if created else None
    clock = getattr(session, "clock", None)
    clock_dict = clock.to_dict() if clock is not None else {}
    return {
        "session_id": getattr(session, "id", None),
        "session_name": getattr(session, "name", None),
        "session_created_at": created_iso,
        "message_count": len(getattr(session, "messages", []) or []),
        "eval_count": len(getattr(session, "eval_results", []) or []),
        "clock_cursor": clock_dict.get("cursor"),
        "persona": _safe_persona_summary(session),
    }


def _parse_ts(raw: Any) -> datetime | None:
    if not raw or not isinstance(raw, str):
        return None
    try:
        return datetime.fromisoformat(raw)
    except (TypeError, ValueError):
        return None


def _format_advance(seconds: int) -> str:
    """Pretty-print an integer second count as ``script_runner`` duration text.

    Inverse of :func:`script_runner._parse_duration_text`: the
    parser accepts ``Ns / Nm / Nh / Nd``; choose a nearby unit to minimize fractional loss,
    preferring ``m`` / ``h`` / ``d`` commonly written in script JSON. Second-precision
    dialogue intervals are rare in tests, so fractional \"1.5h\" support is unnecessary.
    """
    if seconds <= 0:
        return "0s"
    if seconds % 86400 == 0:
        return f"{seconds // 86400}d"
    if seconds % 3600 == 0:
        return f"{seconds // 3600}h"
    if seconds % 60 == 0:
        return f"{seconds // 60}m"
    return f"{seconds}s"


# ── JSON builders ──────────────────────────────────────────────────


def _build_json_full(session: Any) -> dict[str, Any]:
    """Payload body for ``scope=full`` (superset of SessionArchive)."""
    archive = persistence.serialize_session(
        session,
        name=getattr(session, "name", "export"),
        # Hard constraint: always redact exports (earlier API Key redaction decision).
        redact_api_keys=True,
    )
    return archive.to_json_dict()


def _build_json_persona_memory(session: Any) -> dict[str, Any]:
    """Payload body for ``scope=persona_memory`` (no messages/evals)."""
    model_cfg = dict(getattr(session, "model_config", {}) or {})
    model_cfg = persistence.redact_model_config(model_cfg)
    clock = getattr(session, "clock", None)
    return {
        "persona": copy.deepcopy(getattr(session, "persona", {}) or {}),
        "model_config": model_cfg,
        "clock": clock.to_dict() if clock is not None else {},
        # stage_state is tightly coupled to persona editing (P14), so export together;
        # its size is bounded (fixed current + history list).
        "stage_state": copy.deepcopy(
            getattr(session, "stage_state", {}) or {},
        ),
    }


def _build_json_conversation(session: Any) -> dict[str, Any]:
    """Payload body for ``scope=conversation`` (messages + light meta)."""
    clock = getattr(session, "clock", None)
    return {
        "messages": copy.deepcopy(getattr(session, "messages", []) or []),
        "persona": _safe_persona_summary(session),
        "clock": clock.to_dict() if clock is not None else {},
    }


def _build_json_conversation_evaluations(session: Any) -> dict[str, Any]:
    """Payload body for ``scope=conversation_evaluations``."""
    results = list(getattr(session, "eval_results", []) or [])
    body = _build_json_conversation(session)
    body["eval_results"] = copy.deepcopy(results)
    body["aggregate"] = aggregate_results(results)
    return body


def _build_json_evaluations(session: Any) -> dict[str, Any]:
    """Payload body for ``scope=evaluations`` (no messages)."""
    results = list(getattr(session, "eval_results", []) or [])
    return {
        "persona": _safe_persona_summary(session),
        "eval_results": copy.deepcopy(results),
        "aggregate": aggregate_results(results),
    }


_SCOPE_JSON_BUILDERS: Final[dict[str, Any]] = {
    "full": _build_json_full,
    "persona_memory": _build_json_persona_memory,
    "conversation": _build_json_conversation,
    "conversation_evaluations": _build_json_conversation_evaluations,
    "evaluations": _build_json_evaluations,
}


def build_export_payload(
    session: Any, *, scope: str, include_memory: bool = False,
) -> dict[str, Any]:
    """Build the ``format=json`` payload envelope for ``scope``.

    ``include_memory`` only applies to ``scope in {full, persona_memory}``
    — for the other scopes the memory tar has no natural place to attach
    (conversation/evaluations exports are about narrative content, not
    sandbox state) and requesting it is silently ignored (no hard error,
    since the frontend defaults the checkbox to ``False`` and users who
    flip it for a non-memory scope likely just didn't realise).

    Returns a dict with top-level keys::

        {
            "kind": "testbench_session_export",
            "schema_version": 1,
            "scope": "<scope>",
            "format": "json",
            "generated_at": "<iso>",
            "session_ref": {...},          # see _session_ref
            "payload": {...},              # scope-specific body
            # Only when include_memory=True and scope supports it:
            "memory_tarball_b64": "<base64>",
            "memory_sha256": "<hex>"
        }
    """
    if scope not in _SCOPE_JSON_BUILDERS:
        raise SessionExportError(
            "UnknownScope",
            f"unknown export scope {scope!r}; expected one of "
            f"{SESSION_EXPORT_SCOPES}",
        )
    body = _SCOPE_JSON_BUILDERS[scope](session)
    envelope: dict[str, Any] = {
        "kind": EXPORT_KIND,
        "schema_version": EXPORT_SCHEMA_VERSION,
        "scope": scope,
        "format": "json",
        "generated_at": _now_iso(),
        "session_ref": _session_ref(session),
        "payload": body,
    }

    if include_memory and scope in ("full", "persona_memory"):
        sandbox = getattr(session, "sandbox", None)
        if sandbox is not None:
            app_docs = getattr(sandbox, "_app_docs", None)
            if app_docs is not None:
                tar_bytes = persistence.pack_memory_tarball(app_docs)
                envelope["memory_tarball_b64"] = base64.b64encode(
                    tar_bytes,
                ).decode("ascii")
                envelope["memory_sha256"] = persistence.compute_memory_sha256(
                    tar_bytes,
                )
                envelope["memory_tarball_bytes"] = len(tar_bytes)

    return envelope


def serialize_json(payload: dict[str, Any]) -> str:
    """Render ``payload`` with stable formatting (2-space indent, UTF-8)."""
    return json.dumps(payload, ensure_ascii=False, indent=2) + "\n"


# ── dialog_template builder ────────────────────────────────────────


def build_dialog_template(
    session: Any, *,
    name: str | None = None,
    description: str | None = None,
    user_persona_hint: str | None = None,
) -> dict[str, Any]:
    """Reverse-engineer a ``script_runner``-compatible template from messages.

    Walk the session's ``messages`` list in order; for each user turn we
    compute ``time.advance`` from the timestamp delta to the previous
    stored message (rounded to whole seconds). Assistant turns drop their
    content into ``expected`` and omit ``time`` (per script schema).
    system messages are skipped (they're synthetic injects, not part of
    a reproducible script).

    Bootstrap is derived from :attr:`session.clock`:
    ``bootstrap.virtual_now`` = ``clock.bootstrap_at or cursor``,
    ``bootstrap.last_gap_minutes`` = ``initial_last_gap_seconds // 60``
    (only when present; else omitted).

    Empty user/assistant content is tolerated (the turn is still emitted
    so the sequence length survives the round-trip) but the caller
    should ideally gate the export on ``message_count > 0`` at the UI.
    """
    messages = list(getattr(session, "messages", []) or [])
    clock = getattr(session, "clock", None)
    persona = _safe_persona_summary(session)

    base_name = (name or "").strip()
    if not base_name:
        sess_name = str(getattr(session, "name", "") or "")
        base_name = f"{sess_name}_replay" if sess_name else "session_replay"

    turns: list[dict[str, Any]] = []
    prev_ts: datetime | None = None
    for msg in messages:
        role = msg.get("role")
        content = msg.get("content") or ""
        ts = _parse_ts(msg.get("timestamp"))

        if role == "user":
            turn: dict[str, Any] = {"role": "user", "content": content}
            # Omit time from the first user turn (bootstrap already supplies its initial time anchor).
            # Later user turns populate time.advance with the difference from the preceding message.
            # P24 §12.5 L3 defensive: clamp negative deltas to 0. The
            # `append_message` chokepoint should guarantee monotonicity
            # at the write path, but archives saved before Day 2 / hand-
            # edited archives / imports from external sources may still
            # contain non-monotonic messages. Writing a negative advance
            # would produce a dialog_template script_runner refuses to
            # parse — clamping to 0 keeps the export usable, and the
            # user-visible archive's Chat UI will show 0-second gaps
            # (same visual as "same-second messages") rather than crash.
            if prev_ts is not None and ts is not None:
                delta = max(0, int((ts - prev_ts).total_seconds()))
                if delta >= 60:
                    turn["time"] = {"advance": _format_advance(delta)}
            turns.append(turn)
            prev_ts = ts or prev_ts
        elif role == "assistant":
            turns.append({"role": "assistant", "expected": content})
            prev_ts = ts or prev_ts
        else:
            # system / other roles: skip but update prev_ts so later user turns
            # do not assign silence between injections to the next turn's time.advance.
            prev_ts = ts or prev_ts

    bootstrap: dict[str, Any] = {}
    if clock is not None:
        clock_dict = clock.to_dict()
        bootstrap_at = clock_dict.get("bootstrap_at") or clock_dict.get("cursor")
        if bootstrap_at:
            bootstrap["virtual_now"] = bootstrap_at
        last_gap_seconds = clock_dict.get("initial_last_gap_seconds")
        if isinstance(last_gap_seconds, (int, float)) and last_gap_seconds > 0:
            bootstrap["last_gap_minutes"] = int(last_gap_seconds // 60)

    default_desc = ""
    if persona.get("character_name") or persona.get("master_name"):
        default_desc = (
            f"Exported from session {getattr(session, 'name', '?')!r} "
            f"(character={persona.get('character_name') or '-'}, "
            f"master={persona.get('master_name') or '-'})."
        )

    template: dict[str, Any] = {
        "name": base_name,
        "description": (description or default_desc).strip(),
        "user_persona_hint": (user_persona_hint or "").strip(),
        "turns": turns,
    }
    if bootstrap:
        template["bootstrap"] = bootstrap
    return template


# ── Markdown builders ──────────────────────────────────────────────


def _md_header(session: Any, *, scope: str) -> list[str]:
    lines = ["# Testbench Session Export", ""]
    lines.append(f"_Generated at {_now_iso()}_")
    lines.append("")
    ref = _session_ref(session)
    lines.append("## Context")
    lines.append("")
    lines.append(
        f"- **Session**: {ref.get('session_name') or '(unnamed)'} "
        f"(`{ref.get('session_id') or '-'}`)",
    )
    lines.append(f"- **Scope**: `{scope}`")
    p = ref.get("persona") or {}
    if p.get("character_name") or p.get("master_name"):
        lines.append(
            f"- **Persona**: character=`{p.get('character_name') or '-'}`, "
            f"master=`{p.get('master_name') or '-'}`",
        )
    if ref.get("session_created_at"):
        lines.append(f"- **Created at**: `{ref['session_created_at']}`")
    if ref.get("clock_cursor"):
        lines.append(f"- **Clock cursor**: `{ref['clock_cursor']}`")
    lines.append(f"- **Message count**: {ref.get('message_count', 0)}")
    lines.append(f"- **Evaluation count**: {ref.get('eval_count', 0)}")
    lines.append("")
    return lines


def _md_persona_block(session: Any) -> list[str]:
    persona = getattr(session, "persona", {}) or {}
    if not isinstance(persona, dict) or not persona:
        return []
    lines = ["## Persona", ""]
    for key in ("master_name", "character_name", "language"):
        val = persona.get(key)
        if val is None or val == "":
            continue
        lines.append(f"- **{key}**: `{val}`")
    sys_prompt = persona.get("system_prompt")
    if isinstance(sys_prompt, str) and sys_prompt.strip():
        lines.append("")
        lines.append("**System prompt**:")
        lines.append("")
        lines.append("```text")
        lines.append(sys_prompt.rstrip())
        lines.append("```")
    lines.append("")
    return lines


def _md_clock_block(session: Any) -> list[str]:
    clock = getattr(session, "clock", None)
    if clock is None:
        return []
    c = clock.to_dict()
    lines = ["## Virtual clock", ""]
    if c.get("is_real_time"):
        lines.append("- Real-time mode (no virtual cursor)")
    else:
        lines.append(f"- Cursor: `{c.get('cursor') or '-'}`")
        if c.get("bootstrap_at"):
            lines.append(f"- Bootstrap at: `{c['bootstrap_at']}`")
    if c.get("initial_last_gap_seconds"):
        lines.append(
            f"- Initial last-gap: {int(c['initial_last_gap_seconds']) // 60} min",
        )
    if c.get("per_turn_default_seconds"):
        lines.append(
            f"- Per-turn default: {int(c['per_turn_default_seconds'])} s",
        )
    lines.append("")
    return lines


def _md_model_config_block(session: Any) -> list[str]:
    mc = dict(getattr(session, "model_config", {}) or {})
    if not mc:
        return []
    mc = persistence.redact_model_config(mc)
    lines = ["## Model config (api_key redacted)", ""]
    for group, cfg in mc.items():
        if not isinstance(cfg, dict):
            continue
        lines.append(f"### {group}")
        for k in ("provider", "model", "base_url", "temperature", "max_tokens", "timeout"):
            if k in cfg and cfg[k] not in (None, ""):
                lines.append(f"- `{k}`: `{cfg[k]}`")
        # api_key is always redacted; list it separately so it is not mistaken for an empty value.
        lines.append(f"- `api_key`: `{cfg.get('api_key', '-')}`")
        lines.append("")
    return lines


def _md_memory_sizes_block(session: Any) -> list[str]:
    sandbox = getattr(session, "sandbox", None)
    if sandbox is None:
        return []
    app_docs = getattr(sandbox, "_app_docs", None)
    if app_docs is None or not app_docs.exists():
        return []
    # Report only sizes of top-level memory/ files, without recursion, preventing thousands of snapshots
    # from bloating the report.
    try:
        mem_dir = app_docs / "memory"
        if not mem_dir.exists() or not mem_dir.is_dir():
            return []
        files = []
        for p in sorted(mem_dir.rglob("*")):
            if p.is_file():
                try:
                    files.append((p.relative_to(app_docs).as_posix(), p.stat().st_size))
                except OSError:
                    continue
    except OSError:
        return []
    if not files:
        return []
    lines = ["## Memory files", ""]
    lines.append("| Path | Size (bytes) |")
    lines.append("|---|---|")
    for relpath, size in files:
        lines.append(f"| `{relpath}` | {size} |")
    lines.append("")
    return lines


def _md_messages_block(session: Any, *, include_wire: bool = False) -> list[str]:
    """Render the conversation messages in chronological order."""
    messages = list(getattr(session, "messages", []) or [])
    lines = ["## Conversation", ""]
    if not messages:
        lines.append("_No messages yet._")
        lines.append("")
        return lines
    for idx, m in enumerate(messages, start=1):
        role = m.get("role") or "?"
        ts = m.get("timestamp") or "-"
        source = m.get("source") or "-"
        content = (m.get("content") or "").rstrip()
        # Markdown preserves original newlines: prefix each blockquote line with '> ', retaining empty lines as '>'
        # to preserve consecutive paragraphs.
        lines.append(
            f"### {idx}. `{role}` · `{ts}` · source=`{source}`",
        )
        lines.append("")
        if content:
            for ln in content.split("\n"):
                lines.append(f"> {ln}" if ln else ">")
        else:
            lines.append("_(empty)_")
        ref = m.get("reference_content")
        if isinstance(ref, str) and ref.strip():
            lines.append("")
            lines.append("**Reference (expected)**:")
            lines.append("")
            for ln in ref.rstrip().split("\n"):
                lines.append(f"> {ln}" if ln else ">")
        lines.append("")
    return lines


def _md_evaluations_block(session: Any) -> list[str]:
    """Reuse judge_export.build_report_markdown for the evaluations section.

    Trimmed: we drop the "Filter" header (our export has no filter) and
    shift the heading from H1 to H2 so it nests correctly under the
    session export top heading.
    """
    results = list(getattr(session, "eval_results", []) or [])
    if not results:
        return [
            "## Evaluations",
            "",
            "_No evaluation results._",
            "",
        ]
    persona = _safe_persona_summary(session)
    metadata = {
        "session_id": getattr(session, "id", None),
        "session_name": getattr(session, "name", None),
        "character_name": persona.get("character_name"),
        "master_name": persona.get("master_name"),
    }
    agg = aggregate_results(results)
    full = judge_export.build_report_markdown(
        results=results,
        aggregate=agg,
        metadata=metadata,
    )
    # Original report starts with '# Evaluation Report' and '_Generated at_' on two lines;
    # replace with session export's '## Evaluations' to avoid conflicting H1 headings.
    body = full.split("\n", 3)[-1] if full.count("\n") >= 3 else full
    lines = ["## Evaluations", ""]
    lines.extend(body.splitlines())
    if lines[-1] != "":
        lines.append("")
    return lines


def _md_snapshot_meta_block(session: Any) -> list[str]:
    store = getattr(session, "snapshot_store", None)
    if store is None:
        return []
    try:
        metas = store.list_metadata()
    except Exception:  # noqa: BLE001
        return []
    if not metas:
        return []
    lines = ["## Snapshots", ""]
    lines.append("| # | ID | Trigger | Label | Created at (virtual) |")
    lines.append("|---|---|---|---|---|")
    for i, m in enumerate(metas, start=1):
        lines.append(
            f"| {i} | `{m.get('id') or '-'}` | `{m.get('trigger') or '-'}` "
            f"| {m.get('label') or '-'} | `{m.get('created_at_virtual') or '-'}` |",
        )
    lines.append("")
    return lines


def build_export_markdown(
    session: Any, *, scope: str,
) -> str:
    """Render a scope-specific Markdown report (no ``dialog_template``).

    Layout per scope:

    ``full``                      → header + persona + clock + model_config
                                    + memory sizes + snapshot meta +
                                    conversation + evaluations
    ``persona_memory``            → header + persona + clock +
                                    model_config + memory sizes
    ``conversation``              → header + persona + clock + conversation
    ``conversation_evaluations``  → header + persona + clock + conversation
                                    + evaluations
    ``evaluations``               → header + persona + clock + evaluations
    """
    lines: list[str] = []
    lines.extend(_md_header(session, scope=scope))

    want_persona = True  # every scope benefits from persona header
    want_clock = True

    want_model_config = scope in ("full", "persona_memory")
    want_memory = scope in ("full", "persona_memory")
    want_snapshots = scope == "full"
    want_messages = scope in ("full", "conversation", "conversation_evaluations")
    want_evaluations = scope in (
        "full", "conversation_evaluations", "evaluations",
    )

    if want_persona:
        lines.extend(_md_persona_block(session))
    if want_clock:
        lines.extend(_md_clock_block(session))
    if want_model_config:
        lines.extend(_md_model_config_block(session))
    if want_memory:
        lines.extend(_md_memory_sizes_block(session))
    if want_snapshots:
        lines.extend(_md_snapshot_meta_block(session))
    if want_messages:
        lines.extend(_md_messages_block(session))
    if want_evaluations:
        lines.extend(_md_evaluations_block(session))

    return "\n".join(lines).rstrip() + "\n"


# ── Top-level dispatcher ───────────────────────────────────────────


def export_session(
    session: Any,
    *,
    scope: str,
    fmt: str,
    include_memory: bool = False,
) -> tuple[str, str]:
    """Produce ``(body_text, media_type)`` for the given scope × format.

    Single entry point the router calls. Does combination validation
    up front, then dispatches to the right builder. Memory inclusion
    only applies to ``fmt=json`` + ``scope in {full, persona_memory}``;
    other combinations ignore it silently so the frontend doesn't have
    to gate the checkbox.
    """
    ensure_valid_combination(scope, fmt)
    if fmt == "dialog_template":
        template = build_dialog_template(session)
        body = json.dumps(template, ensure_ascii=False, indent=2) + "\n"
        return body, "application/json; charset=utf-8"
    if fmt == "markdown":
        return build_export_markdown(session, scope=scope), \
            "text/markdown; charset=utf-8"
    payload = build_export_payload(
        session, scope=scope, include_memory=include_memory,
    )
    return serialize_json(payload), "application/json; charset=utf-8"


__all__ = [
    "EXPORT_FORMATS",
    "EXPORT_KIND",
    "EXPORT_SCHEMA_VERSION",
    "SESSION_EXPORT_SCOPES",
    "SessionExportError",
    "VALID_COMBINATIONS",
    "build_dialog_template",
    "build_export_markdown",
    "build_export_payload",
    "ensure_valid_combination",
    "export_session",
    "is_valid_combination",
    "serialize_json",
    "session_export_filename",
]
