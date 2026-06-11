"""P24 Session-fields audit smoke — §14.4 M5 + §14A.1.

Guards the invariant that each ``Session`` dataclass field has a clear
"persisted / runtime-only / count-only" status across the **four
serialization exits** that every session state eventually leaks through:

    1. ``Session.describe()``           — GET /api/session + /state
    2. ``persistence.serialize_session``— manual Save + autosave
    3. ``snapshot_store.capture()``     — every rewind anchor
    4. ``pipeline.session_export``      — P23 user-facing export

Without this audit, adding a new ``Session`` field (e.g. a runtime-only
cache) silently leaks into archives/exports/snapshots and becomes a
persistent migration headache. Conversely, a field meant to be saved
might be forgotten at one exit and silently lost on reload.

Audit ledger (authoritative; see P24_BLUEPRINT §14A.1):

    PERSIST  = fields that MUST round-trip through save/load/export
    RUNTIME  = fields that MUST NOT leak into any persisted artifact
    COUNTED  = fields surfaced only as ``<x>_count`` in describe()

The smoke fails if:
  * a PERSIST field is absent from serialize_session / snapshot capture
  * a RUNTIME field leaks into any persisted exit (incl. asyncio.Event,
    asyncio.Lock, or module-level singletons like SnapshotStore)
  * a new field appears on the dataclass but isn't classified here
    (forces the dev to consciously decide which bucket it belongs in)

Usage::

    .venv/Scripts/python.exe tests/testbench/smoke/p24_session_fields_audit_smoke.py

Exits 0 on clean, 1 on any audit violation.
"""
from __future__ import annotations

import asyncio
import dataclasses
import io
import json
import os
import sys
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

# Force utf-8 on stdout so unicode bullets don't crash on Windows GBK.
if isinstance(sys.stdout, io.TextIOWrapper):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass


# ── Audit ledger ────────────────────────────────────────────────────────

# Fields that MUST be present in every persistence-facing exit.
PERSIST_FIELDS: frozenset[str] = frozenset({
    "id",
    "name",
    "created_at",
    "messages",
    "persona",
    "model_config",
    "stage_state",
    "eval_results",
    "clock",
})

# Fields that MUST NOT leak into any persisted exit. These are either
# live asyncio primitives, short-lived caches (TTL-bound), or module-
# level singletons whose identity is per-process and can't be restored
# from JSON anyway.
RUNTIME_FIELDS: frozenset[str] = frozenset({
    "lock",                 # asyncio.Lock — not JSON-serializable
    "logger",               # SessionLogger — per-process sink
    "sandbox",              # Sandbox — path pointer into process-specific
                            # testbench_data, surfaced via describe().sandbox
                            # but never serialized raw
    "snapshots",            # legacy pre-P18 list, always empty now;
                            # real timeline lives in snapshot_store
    "snapshot_store",       # SnapshotStore — hot/cold lives are exported
                            # via snapshots_hot/cold_meta in serialize_session
    "autosave_scheduler",   # AutosaveScheduler — asyncio task, resets
                            # on load/restore
    "script_state",         # P12 runtime-only (docstring declares
                            # "not auto-persisted")
    "auto_state",           # P13 runtime (contains asyncio.Event)
    "memory_previews",      # P10 TTL cache (MEMORY_PREVIEW_TTL_SECONDS)
    "state",                # SessionState enum — runtime lifecycle
    "busy_op",              # short-lived op tag, paired with .state
    "last_llm_wire",        # P25 Day 2 polish r4: 最近一次真实发给 LLM 的
                            # wire 快照, 为 Prompt Preview "预览 = 真实"
                            # 契约服务. 仅通过 GET /api/chat/prompt_preview
                            # 暴露, 不入存档/快照/导出 (dataclass docstring
                            # 给出了完整理由).
})


# Fields that appear in describe() only as counters (``<x>_count``),
# never the full payload.
COUNT_ONLY_FIELDS: frozenset[str] = frozenset({
    "messages",     # message_count
    "eval_results", # eval_count
    # snapshots surfaced via snapshot_store.list_metadata() length
})


# ── Env setup ───────────────────────────────────────────────────────────

def _setup_env() -> Path:
    """Point testbench DATA_DIR at a temp dir before any import."""
    tmp_data = Path(tempfile.mkdtemp(prefix="p24_fields_audit_"))
    os.environ["TESTBENCH_DATA_DIR"] = str(tmp_data)
    from tests.testbench import config as tb_config
    tb_config.DATA_DIR = tmp_data
    tb_config.SAVED_SESSIONS_DIR = tmp_data / "saved_sessions"
    tb_config.AUTOSAVE_DIR = tmp_data / "saved_sessions" / "_autosave"
    tb_config.LOGS_DIR = tmp_data / "logs"
    tb_config.SANDBOXES_DIR = tmp_data / "sandboxes"
    for d in [
        tb_config.SAVED_SESSIONS_DIR,
        tb_config.AUTOSAVE_DIR,
        tb_config.LOGS_DIR,
        tb_config.SANDBOXES_DIR,
    ]:
        d.mkdir(parents=True, exist_ok=True)
    return tmp_data


# ── Audit check 1: dataclass field completeness ─────────────────────────

def check_dataclass_fields_classified() -> list[str]:
    """Every Session dataclass field must be in exactly one ledger bucket.

    Catches drift where a new field is added to Session without a
    conscious decision about whether it persists.
    """
    from tests.testbench.session_store import Session

    errors: list[str] = []
    declared = {f.name for f in dataclasses.fields(Session)}
    classified = PERSIST_FIELDS | RUNTIME_FIELDS

    unclassified = declared - classified
    if unclassified:
        errors.append(
            f"[unclassified] {len(unclassified)} Session field(s) "
            f"not in PERSIST/RUNTIME ledger: {sorted(unclassified)} — "
            f"add to p24_session_fields_audit_smoke.py bucket explicitly"
        )

    stale = classified - declared
    if stale:
        errors.append(
            f"[stale] {len(stale)} ledger entries reference fields "
            f"no longer on Session: {sorted(stale)}"
        )

    overlap = PERSIST_FIELDS & RUNTIME_FIELDS
    if overlap:
        errors.append(
            f"[overlap] field(s) in both PERSIST and RUNTIME buckets: "
            f"{sorted(overlap)}"
        )

    return errors


# ── Audit check 2: describe() leakage ───────────────────────────────────

def check_describe_exit(session) -> list[str]:
    """Session.describe() MUST be JSON-safe and MUST NOT leak runtime
    primitives / raw dataclasses into the wire payload.

    ``describe()`` intentionally exposes a few runtime-ish fields as
    JSON-safe sub-dicts (state / busy_op for UI status chips, sandbox
    via ``Sandbox.describe()`` for Diagnostics → Paths). These are
    allow-listed below; the check still guards against non-JSON-safe
    raw primitives (asyncio.Lock / Event / SnapshotStore / etc.) ever
    bleeding into the payload.
    """
    errors: list[str] = []
    payload = session.describe()

    try:
        json.dumps(payload)
    except TypeError as exc:
        errors.append(f"[describe] payload is not JSON-safe: {exc}")

    # Allow-listed runtime fields that describe() legitimately surfaces
    # as JSON-safe sub-dicts / scalars (never the raw object).
    describe_allowed_runtime = {"state", "busy_op", "sandbox"}
    leaked = [
        k for k in RUNTIME_FIELDS
        if k in payload and k not in describe_allowed_runtime
    ]
    if leaked:
        errors.append(
            f"[describe] runtime-only field(s) leaked into describe(): "
            f"{leaked}"
        )

    # And the allow-listed ones must still be JSON-primitive shapes,
    # not raw dataclass / enum / path objects.
    if "sandbox" in payload and not isinstance(payload["sandbox"], dict):
        errors.append(
            f"[describe] 'sandbox' leaked as raw {type(payload['sandbox']).__name__} "
            f"instead of a JSON-safe dict (should go through .describe())"
        )
    if "state" in payload and not isinstance(payload["state"], str):
        errors.append(
            f"[describe] 'state' leaked as raw {type(payload['state']).__name__} "
            f"instead of enum .value string"
        )

    # Counters should show up instead of full lists.
    if "messages" in payload and isinstance(payload["messages"], list):
        errors.append(
            "[describe] raw 'messages' list in describe() — "
            "expected 'message_count' only"
        )
    if "eval_results" in payload and isinstance(payload["eval_results"], list):
        errors.append(
            "[describe] raw 'eval_results' list in describe() — "
            "expected 'eval_count' only"
        )
    if "message_count" not in payload:
        errors.append("[describe] missing 'message_count' counter")
    if "eval_count" not in payload:
        errors.append("[describe] missing 'eval_count' counter")
    if "snapshot_count" not in payload:
        errors.append("[describe] missing 'snapshot_count' counter")

    return errors


# ── Audit check 3: serialize_session (persistence) exit ─────────────────

def check_serialize_exit(session) -> list[str]:
    """persistence.serialize_session must cover every PERSIST field and
    omit every RUNTIME field (asyncio / scheduler / cache)."""
    from tests.testbench.pipeline import persistence as p

    errors: list[str] = []
    archive = p.serialize_session(session, name="p24_fields_audit")

    archive_dict = archive.to_dict() if hasattr(archive, "to_dict") \
        else dataclasses.asdict(archive)

    try:
        json.dumps(archive_dict, default=str)
    except TypeError as exc:
        errors.append(f"[serialize] archive dict not JSON-safe: {exc}")

    missing_persist = []
    if "messages" not in archive_dict:
        missing_persist.append("messages")
    if "persona" not in archive_dict:
        missing_persist.append("persona")
    if "model_config" not in archive_dict:
        missing_persist.append("model_config")
    if "stage_state" not in archive_dict:
        missing_persist.append("stage_state")
    if "eval_results" not in archive_dict:
        missing_persist.append("eval_results")
    if "clock" not in archive_dict:
        missing_persist.append("clock")
    if "session_id" not in archive_dict:
        missing_persist.append("session_id")
    if "session_name" not in archive_dict:
        missing_persist.append("session_name")
    if missing_persist:
        errors.append(
            f"[serialize] missing PERSIST fields in archive: "
            f"{missing_persist}"
        )

    # Runtime leakage — direct top-level keys.
    runtime_leak_keys = (
        "lock", "logger", "sandbox", "snapshot_store", "autosave_scheduler",
        "script_state", "auto_state", "memory_previews", "state", "busy_op",
        "last_llm_wire",
    )
    leaked = [k for k in runtime_leak_keys if k in archive_dict]
    if leaked:
        errors.append(
            f"[serialize] runtime-only field(s) leaked into archive: "
            f"{leaked}"
        )

    return errors


# ── Audit check 4: snapshot_store.capture() exit ────────────────────────

def check_snapshot_capture_exit(session) -> list[str]:
    """Every snapshot must copy only PERSIST fields; snapshot payloads
    are rewindable state and MUST NOT carry runtime-only attachments."""
    errors: list[str] = []
    store = session.snapshot_store
    assert store is not None, "fixture session must have snapshot_store"

    # Capture a fresh snapshot at a known trigger (already done on
    # session create for 't0:init' but we force a new one so the
    # assertion is self-contained).
    snap = store.capture(session, trigger="manual", label="audit_capture")

    fields = {f.name for f in dataclasses.fields(snap)}

    missing_persist = PERSIST_FIELDS - {
        "id", "name", "created_at", "clock"
    }  # these are Snapshot-identity fields with different names
    missing_persist -= fields
    # Snapshot doesn't carry session id/name/created_at raw; its own
    # id/created_at/virtual_now cover timeline identity.

    # Snapshot SHOULD have these payload fields (the field names differ
    # from Session.* — virtual_now vs clock, clock_override vs clock):
    expected_snap_payload = {
        "messages", "memory_files", "model_config", "stage_state",
        "eval_results", "persona", "clock_override",
    }
    missing = expected_snap_payload - fields
    if missing:
        errors.append(
            f"[snapshot] Snapshot dataclass missing payload fields: "
            f"{sorted(missing)}"
        )

    # The crux: Snapshot MUST NOT carry runtime-only Session fields.
    # These names must NOT appear as dataclass fields on Snapshot:
    forbidden_runtime = {
        "lock", "logger", "sandbox", "snapshot_store",
        "autosave_scheduler", "script_state", "auto_state",
        "memory_previews", "state", "busy_op", "last_llm_wire",
    }
    leaked = forbidden_runtime & fields
    if leaked:
        errors.append(
            f"[snapshot] Snapshot dataclass declares runtime-only "
            f"field(s): {sorted(leaked)} — these should live on Session "
            f"only, never in persisted timeline"
        )

    # Also check the to_json_dict() payload at the wire level (the
    # form that spills to cold + ships in archives via snapshots_hot).
    wire = snap.to_json_dict()
    try:
        json.dumps(wire, default=str)
    except TypeError as exc:
        errors.append(f"[snapshot] to_json_dict payload not JSON-safe: {exc}")

    leaked_wire = [k for k in forbidden_runtime if k in wire]
    if leaked_wire:
        errors.append(
            f"[snapshot] runtime field(s) leaked into to_json_dict: "
            f"{leaked_wire}"
        )

    return errors


# ── Audit check 5: session_export exit ──────────────────────────────────

def check_export_exit(session) -> list[str]:
    """pipeline.session_export must only read from PERSIST fields (via
    getattr). A direct reference to a RUNTIME field in the export
    source file indicates a leak risk."""
    import tests.testbench.pipeline.session_export as export_mod

    errors: list[str] = []
    source = Path(export_mod.__file__).read_text(encoding="utf-8")

    # Source-level scan: `getattr(session, "<field>"` and
    # `session.<field>` references for each RUNTIME_FIELDS entry.
    import re
    for field_name in RUNTIME_FIELDS:
        # Skip fields we know are legitimately used (never for
        # serialization, only for meta/routing).
        if field_name in {"sandbox", "snapshot_store"}:
            # sandbox — only used to locate memory_dir for tarball
            #   (path read, not field serialized into payload)
            # snapshot_store — only used for .list_metadata() length
            continue

        patt = rf'(session\.{re.escape(field_name)}\b|getattr\(\s*session\s*,\s*["\']?{re.escape(field_name)}["\']?)'
        if re.search(patt, source):
            errors.append(
                f"[export] session_export.py reads runtime-only field "
                f"'{field_name}' — source-level leak risk"
            )

    # Runtime assertion: actually run the exports and check nothing
    # crashes (end-to-end against a populated session).
    try:
        payload = export_mod.build_export_payload(
            session, scope="full", include_memory=False,
        )
        json.dumps(payload, default=str)
    except Exception as exc:
        errors.append(f"[export] build_export_payload crashed: {exc!r}")

    try:
        md = export_mod.build_export_markdown(session, scope="full")
        assert isinstance(md, str) and md, "markdown empty"
    except Exception as exc:
        errors.append(f"[export] build_export_markdown crashed: {exc!r}")

    # Dialog template scope requires messages with alternating roles.
    try:
        payload = export_mod.build_export_payload(
            session, scope="persona_memory", include_memory=False,
        )
        json.dumps(payload, default=str)
    except Exception as exc:
        errors.append(f"[export] persona_memory scope crashed: {exc!r}")

    return errors


# ── Fixture: a populated session via the real SessionStore ──────────────

async def _make_populated_session():
    """Create a session with every PERSIST field non-empty so the
    audit checks exercise real data paths, not empty defaults."""
    from tests.testbench.session_store import get_session_store

    store = get_session_store()
    session = await store.create(name="p24_fields_audit_fixture")

    # Populate PERSIST fields so serialize/capture/export carry real data.
    session.messages.append({
        "role": "user", "content": "audit fixture hello",
        "timestamp": datetime.now().isoformat(),
    })
    session.messages.append({
        "role": "assistant", "content": "audit fixture reply",
        "timestamp": datetime.now().isoformat(),
    })
    session.persona["name"] = "AuditBot"
    session.persona["description"] = "fixture persona"
    session.model_config["provider"] = "noop"
    session.model_config["model"] = "noop-test"
    session.model_config["api_key"] = "sk-dummy"
    session.eval_results.append({
        "id": "eval_audit_1",
        "created_at": datetime.now().isoformat(),
        "mode": "single",
        "ok": True,
    })
    session.clock.set_now(datetime(2026, 4, 22, 10, 0, 0))
    session.clock.pending_advance = timedelta(seconds=30)

    # Also populate RUNTIME fields to prove they stay put.
    session.memory_previews["facts.extract"] = {
        "created_at": datetime.now(),
        "payload": {"preview": "runtime-only cache entry"},
        "params": {},
    }
    session.script_state = {
        "template_name": "audit_fixture_script",
        "template_source": "user",
        "turns": [],
        "cursor": 0,
        "turns_count": 0,
    }
    # auto_state skipped — it carries asyncio.Event which can't be
    # constructed outside an event loop. Its absence is handled by
    # serialize/export paths already (they use getattr with default).

    return store, session


async def _teardown(store) -> None:
    try:
        await store.destroy(purge_sandbox=True)
    except Exception:
        pass


# ── Orchestration ───────────────────────────────────────────────────────

def _report(title: str, errors: list[str]) -> int:
    print("")
    print(f"* {title}")
    if not errors:
        print("  [ok] no violations")
        return 0
    print(f"  [ERR] {len(errors)} violation(s):")
    for line in errors:
        print(f"    {line}")
    return len(errors)


async def _amain() -> int:
    _setup_env()

    total = 0

    # Check 1 doesn't need a fixture.
    total += _report(
        "1 | Session dataclass fields are all classified "
        "(PERSIST or RUNTIME)",
        check_dataclass_fields_classified(),
    )

    store, session = await _make_populated_session()
    try:
        total += _report(
            "2 | describe() — runtime-only leakage + JSON-safety + "
            "required counters",
            check_describe_exit(session),
        )
        total += _report(
            "3 | persistence.serialize_session — PERSIST coverage + "
            "runtime leak",
            check_serialize_exit(session),
        )
        total += _report(
            "4 | snapshot_store.capture — Snapshot dataclass + "
            "to_json_dict leakage",
            check_snapshot_capture_exit(session),
        )
        total += _report(
            "5 | session_export — source-level + runtime export "
            "payload audit",
            check_export_exit(session),
        )
    finally:
        await _teardown(store)

    print("")
    print("=" * 66)
    if total:
        print(f" [FAIL] {total} violation(s) across Session 4-exit audit.")
        return 1
    print(" [PASS] Session 4-exit audit clean.")
    print(
        "   Ledger: "
        f"{len(PERSIST_FIELDS)} persist / {len(RUNTIME_FIELDS)} runtime"
    )
    return 0


def main() -> int:
    print("=" * 66)
    print(" P24 Session-Fields Audit Smoke  "
          "(describe / serialize / snapshot / export)")
    print("=" * 66)
    return asyncio.run(_amain())


if __name__ == "__main__":
    sys.exit(main())
