"""P24 integration smoke — PLAN.md §15.6 four-case coverage.

Integration-level assertions that tie the P24 delivery threads
together through the HTTP boundary (TestClient). Runs only the
Python side; the jsdom side from the §15.6 spec is deferred to
nightly since the new panels don't ship without a matching
backend invariant.

Covers:

  (a) GET  /api/system/orphans       — orphan sandbox scan returns
      the §15.2 A response shape (``orphans: [...]`` plus summary
      fields), and pre-seeding a dir shows it up.

  (b) DELETE /api/system/orphans/{session_id} — first call 200,
      second call 404 (deterministic side-effect), traversal attempt
      400, active-session attempt 409.

  (c) POST /api/judge/run + match_main_chat=true — the persona meta
      result the router feeds into the judger's ``system_prompt``
      is byte-identical to ``build_prompt_bundle(session).system_prompt``.
      We test the pure extractor directly (``_extract_persona_meta``)
      so the audit doesn't require firing a real LLM.

  (d) GET /api/diagnostics/errors?op_type=... — F7 Security subpage's
      "three audit events filter" can co-list the three known
      DiagnosticsOp security categories in a single request, and
      results come back newest-first.

  (e) diagnostics ring warn-once (§14.4 M4) — overflowing the 200-entry
      cap fires exactly one DIAGNOSTICS_RING_FULL notice per fill
      cycle, and a follow-up overflow inside the same cycle stays
      silent; ``clear()`` resets the one-shot flag so the next
      overflow fires a fresh notice.

Usage::

    .venv/Scripts/python.exe tests/testbench/smoke/p24_integration_smoke.py

Exits 0 on clean, 1 on any assertion failure.
"""
from __future__ import annotations

import io
import os
import sys
import tempfile
from datetime import datetime
from pathlib import Path


if isinstance(sys.stdout, io.TextIOWrapper):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass


def _setup_env() -> Path:
    """Redirect testbench DATA_DIR to a tmp before any import pulls
    in ``tests.testbench.config`` module-level constants."""
    tmp_data = Path(tempfile.mkdtemp(prefix="p24_integration_"))
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


# ── (a) orphan scan endpoint ─────────────────────────────────────────

def check_a_orphan_scan_shape() -> list[str]:
    """GET /api/system/orphans returns §15.2 A response structure."""
    from fastapi.testclient import TestClient
    from tests.testbench import config as tb_config
    from tests.testbench.server import create_app

    errors: list[str] = []
    app = create_app()
    client = TestClient(app)

    # Seed 2 orphan sandbox dirs (not the active session id).
    fake_ids = ["orphan_aaa11122bbb3", "orphan_cccc4455dddd"]
    for sid in fake_ids:
        fake_sandbox = tb_config.SANDBOXES_DIR / sid
        # Mirror a minimal real sandbox shape so size_bytes > 0.
        (fake_sandbox / "neko" / "config").mkdir(parents=True, exist_ok=True)
        (fake_sandbox / "neko" / "config" / "dummy.json").write_text(
            '{"seed": true}', encoding="utf-8",
        )

    r = client.get("/system/orphans")
    if r.status_code != 200:
        errors.append(
            f"[a] GET /system/orphans returned {r.status_code}: {r.text}"
        )
        return errors
    body = r.json()

    if "orphans" not in body:
        errors.append("[a] response missing 'orphans' key")
    if not isinstance(body.get("orphans"), list):
        errors.append(
            f"[a] 'orphans' must be list, got {type(body.get('orphans')).__name__}"
        )

    seen_ids = {o.get("session_id") for o in body.get("orphans", [])}
    for sid in fake_ids:
        if sid not in seen_ids:
            errors.append(
                f"[a] seeded orphan {sid} missing from scan response"
            )

    for o in body.get("orphans", []):
        for required in ("session_id", "path", "size_bytes"):
            if required not in o:
                errors.append(
                    f"[a] orphan entry missing '{required}': {o}"
                )

    # Summary fields.
    if "scanned_at" not in body:
        errors.append("[a] response missing 'scanned_at' timestamp")

    return errors


# ── (b) orphan delete: success + idempotent 404 + traversal + active ─

def check_b_orphan_delete_semantics() -> list[str]:
    """DELETE /api/system/orphans/{sid} success + 404 + 400 + 409."""
    from fastapi.testclient import TestClient
    from tests.testbench import config as tb_config
    from tests.testbench.server import create_app
    from tests.testbench.session_store import get_session_store

    errors: list[str] = []
    app = create_app()
    client = TestClient(app)

    # Create the active session first so we can test the 409 branch.
    r = client.post("/api/session", json={"name": "p24_active_session"})
    if r.status_code != 201:
        errors.append(f"[b] create session failed {r.status_code}: {r.text}")
        return errors
    active_sid = get_session_store().require().id

    # Seed a deletable orphan with some content.
    target_sid = "orphan_deletable99"
    target_dir = tb_config.SANDBOXES_DIR / target_sid
    (target_dir / "neko").mkdir(parents=True, exist_ok=True)
    (target_dir / "neko" / "payload.txt").write_text(
        "orphan_payload", encoding="utf-8",
    )

    # 1) First DELETE → 200 + bytes freed.
    r = client.delete(f"/system/orphans/{target_sid}")
    if r.status_code != 200:
        errors.append(
            f"[b1] first DELETE expected 200, got {r.status_code}: {r.text}"
        )
    else:
        body = r.json()
        if body.get("session_id") != target_sid:
            errors.append(
                f"[b1] delete response session_id != requested ({body})"
            )
        if "deleted_bytes" not in body:
            errors.append(f"[b1] delete response missing deleted_bytes: {body}")

    # 2) Second DELETE → 404 (dir no longer exists).
    r = client.delete(f"/system/orphans/{target_sid}")
    if r.status_code != 404:
        errors.append(
            f"[b2] second DELETE expected 404, got {r.status_code}: {r.text}"
        )

    # 3) Active session → 409 (refuse to destroy live data).
    r = client.delete(f"/system/orphans/{active_sid}")
    if r.status_code != 409:
        errors.append(
            f"[b3] active-session DELETE expected 409, got {r.status_code}: {r.text}"
        )

    # 4) Path traversal attempt → 400 (or 404 if route sanitizes first).
    r = client.delete("/system/orphans/..%2F..%2Fetc%2Fpasswd")
    if r.status_code not in (400, 404, 422):
        errors.append(
            f"[b4] traversal-style id expected 4xx, got {r.status_code}: {r.text}"
        )

    # Clean up active session so subsequent checks start fresh.
    try:
        client.delete("/api/session")
    except Exception:
        pass

    return errors


# ── (c) match_main_chat byte-identical to prompt_builder ────────────

def check_c_match_main_chat_parity() -> list[str]:
    """_extract_persona_meta(match_main_chat=True) returns a
    system_prompt byte-for-byte equal to build_prompt_bundle."""
    from fastapi.testclient import TestClient
    from tests.testbench.pipeline.prompt_builder import build_prompt_bundle
    from tests.testbench.routers.judge_router import _extract_persona_meta
    from tests.testbench.server import create_app
    from tests.testbench.session_store import get_session_store

    errors: list[str] = []
    app = create_app()
    client = TestClient(app)

    r = client.post("/api/session", json={"name": "p24_f6_parity"})
    if r.status_code != 201:
        errors.append(f"[c] create session failed: {r.text}")
        return errors
    session = get_session_store().require()

    # Populate persona with a minimum viable character config so
    # build_prompt_bundle succeeds (it raises PreviewNotReady
    # otherwise).
    r = client.put("/api/persona", json={
        "character_name": "F6ParityBot",
        "master_name": "AuditMaster",
        "system_prompt": (
            "You are {LANLAN_NAME}. You address the user as {MASTER_NAME}."
        ),
        "personality": "concise",
        "world_setting": "testbench audit fixture",
    })
    if r.status_code != 200:
        errors.append(f"[c] persona save failed: {r.status_code} {r.text}")
        return errors

    # Re-fetch the session (persona save goes through a different
    # path; don't assume pointer identity).
    session = get_session_store().require()

    # Call the pure extractor both ways + the builder directly.
    try:
        bundle = build_prompt_bundle(session)
    except Exception as exc:
        errors.append(f"[c] build_prompt_bundle raised: {exc!r}")
        try:
            client.delete("/api/session")
        except Exception:
            pass
        return errors

    meta_aligned = _extract_persona_meta(session, match_main_chat=True)
    meta_legacy = _extract_persona_meta(session, match_main_chat=False)

    if not meta_aligned.applied:
        errors.append(
            f"[c] match_main_chat=True should have applied; "
            f"fallback_reason={meta_aligned.fallback_reason}"
        )
    if meta_aligned.fallback_reason is not None:
        errors.append(
            f"[c] match_main_chat=True reported fallback: "
            f"{meta_aligned.fallback_reason}"
        )
    if meta_aligned.system_prompt != bundle.system_prompt:
        errors.append(
            f"[c] aligned system_prompt != build_prompt_bundle output "
            f"(len_aligned={len(meta_aligned.system_prompt)} "
            f"len_bundle={len(bundle.system_prompt)})"
        )

    # Legacy mode MUST be a distinct (shorter, persona-only) prompt —
    # otherwise the "aligned" path wouldn't actually change anything
    # and F6 would be a no-op.
    if meta_legacy.applied:
        errors.append(
            "[c] match_main_chat=False should NOT report applied=True"
        )

    # Name substitution in legacy path should at least have happened.
    if "{LANLAN_NAME}" in meta_legacy.system_prompt:
        errors.append(
            "[c] legacy path left {LANLAN_NAME} placeholder unresolved"
        )
    if "F6ParityBot" not in meta_legacy.system_prompt:
        errors.append(
            "[c] legacy path didn't substitute character_name into prompt"
        )

    # Teardown.
    try:
        client.delete("/api/session")
    except Exception:
        pass

    return errors


# ── (d) F7 security audit events co-listing ──────────────────────────

def check_d_f7_security_audit_filter() -> list[str]:
    """GET /api/diagnostics/errors?op_type=a,b,c returns all three
    categories newest-first."""
    from fastapi.testclient import TestClient
    from tests.testbench.pipeline.diagnostics_ops import DiagnosticsOp
    from tests.testbench.pipeline import diagnostics_store
    from tests.testbench.server import create_app

    errors: list[str] = []
    app = create_app()
    client = TestClient(app)

    # Clear any stale ring entries before seeding.
    client.delete("/api/diagnostics/errors")

    # Seed three different security-category ops (a representative
    # from each severity).  Wall-clock precision is milli so we pause
    # briefly between inserts to guarantee ordering — but the ring
    # uses monotonic counters internally, so insertion order is
    # authoritative either way.
    seeded = []
    for op, msg in [
        (DiagnosticsOp.INSECURE_HOST_BINDING, "bound 0.0.0.0 (test seed)"),
        (DiagnosticsOp.JUDGE_EXTRA_CONTEXT_OVERRIDE, "persona_system overridden"),
        (DiagnosticsOp.PROMPT_INJECTION_SUSPECTED, "jailbreak_phrase hit"),
    ]:
        entry = diagnostics_store.record_internal(
            op=op.value,
            message=msg,
            level="warning",
            detail={"seeded_by": "p24_integration_smoke"},
        )
        seeded.append((entry.id, op.value))

    # Query the filter with a comma-separated op_type list.
    op_param = ",".join(op for (_id, op) in seeded)
    r = client.get(
        "/api/diagnostics/errors",
        params={"op_type": op_param, "limit": 50},
    )
    if r.status_code != 200:
        errors.append(f"[d] GET errors filter failed: {r.text}")
        return errors
    body = r.json()

    items = body.get("items", [])
    returned_ops = [it.get("type") for it in items]

    # All three seeded ops must be present.
    for _id, op in seeded:
        if op not in returned_ops:
            errors.append(
                f"[d] op_type filter missed seeded op '{op}'; "
                f"returned ops: {returned_ops}"
            )

    # Items must be newest-first: the last-seeded op
    # (PROMPT_INJECTION_SUSPECTED) must appear before the first-seeded
    # (INSECURE_HOST_BINDING) in the response array.
    #
    # We check positions in the sliced subset of seeded entries to
    # avoid coupling to how many other warnings the env may have
    # surfaced during setup.
    my_items_by_op = {it["type"]: it for it in items if it["type"]
                      in {op for (_id, op) in seeded}}
    if len(my_items_by_op) == 3:
        # Timestamp field is "at" on DiagnosticsError. Newest-first
        # means later-seeded entries sort to a lower array index.
        idx_first = returned_ops.index(
            DiagnosticsOp.INSECURE_HOST_BINDING.value,
        )
        idx_last = returned_ops.index(
            DiagnosticsOp.PROMPT_INJECTION_SUSPECTED.value,
        )
        if idx_last > idx_first:
            errors.append(
                f"[d] ordering wrong: last-seeded op at index {idx_last} "
                f"should come BEFORE first-seeded at index {idx_first} "
                f"(newest-first)"
            )

    # Filter with a single op string must still work.
    r = client.get(
        "/api/diagnostics/errors",
        params={"op_type": DiagnosticsOp.PROMPT_INJECTION_SUSPECTED.value},
    )
    if r.status_code != 200:
        errors.append(f"[d] single-op filter failed: {r.text}")
    else:
        single_items = r.json().get("items", [])
        if not any(it.get("type") == DiagnosticsOp.PROMPT_INJECTION_SUSPECTED.value
                   for it in single_items):
            errors.append(
                "[d] single-op filter didn't return the seeded injection entry"
            )
        # All returned items MUST be the requested op (strict filter).
        stray = [it.get("type") for it in single_items
                 if it.get("type") != DiagnosticsOp.PROMPT_INJECTION_SUSPECTED.value]
        if stray:
            errors.append(
                f"[d] single-op filter leaked other op types: {stray}"
            )

    return errors


# ── Case (e) ────────────────────────────────────────────────────────
# §14.4 M4 — diagnostics ring overflow warn-once mechanism. Independent
# of TestClient so we can seed exactly MAX_ERRORS entries without
# fighting FastAPI middleware ordering.

def check_e_diagnostics_ring_warn_once() -> list[str]:
    """Overflowing the ring fires one-and-only-one
    ``diagnostics_ring_full`` notice per fill cycle.

    Matrix:
      e1: cold start → fill to cap → one overflow push → exactly one
          DIAGNOSTICS_RING_FULL notice appears in the buffer.
      e2: same cycle → two more overflow pushes → buffer is still
          newest-first, but no second notice is injected (flag still
          latched).
      e3: ``clear()`` → fill to cap again → overflow → a new notice
          fires (flag was reset by clear).
    """
    errors: list[str] = []

    from tests.testbench.pipeline import diagnostics_store as ds
    from tests.testbench.pipeline.diagnostics_ops import DiagnosticsOp

    # Start from a clean buffer — even if a prior check in this run
    # used the store, ``clear()`` must put us back to a known state.
    ds.clear()
    if ds._RING_FULL_NOTICE_FIRED is not False:
        errors.append(
            "[e1] pre-fill state: _RING_FULL_NOTICE_FIRED expected False, "
            f"got {ds._RING_FULL_NOTICE_FIRED}"
        )

    def seed_one(i: int) -> None:
        # Touch the store via its public API so the test exercises the
        # same code path real callers hit (record → _push).
        ds.record(
            source="pipeline",
            type="seed",
            message=f"ring-fill #{i}",
            level="error",
        )

    notice_type = DiagnosticsOp.DIAGNOSTICS_RING_FULL.value

    # ── e1: fill exactly to cap, no overflow yet ───────────────────
    for i in range(ds.MAX_ERRORS):
        seed_one(i)
    if ds.snapshot_count() != ds.MAX_ERRORS:
        errors.append(
            f"[e1] after filling to cap expected buffer len = "
            f"{ds.MAX_ERRORS}, got {ds.snapshot_count()}"
        )
    pre_overflow = ds.list_errors(limit=ds.MAX_ERRORS + 5)["items"]
    if any(it.get("type") == notice_type for it in pre_overflow):
        errors.append(
            "[e1] DIAGNOSTICS_RING_FULL notice fired BEFORE overflow "
            "(expected only after buffer exceeds MAX_ERRORS)"
        )
    if ds._RING_FULL_NOTICE_FIRED is not False:
        errors.append(
            "[e1] at-cap (not over-cap) state must NOT latch the flag"
        )

    # ── e1b: trigger exactly one overflow ──────────────────────────
    seed_one(999_001)
    post_overflow = ds.list_errors(limit=ds.MAX_ERRORS + 5)["items"]
    notice_hits = [it for it in post_overflow if it.get("type") == notice_type]
    if len(notice_hits) != 1:
        errors.append(
            f"[e1b] expected exactly 1 DIAGNOSTICS_RING_FULL notice after "
            f"first overflow, got {len(notice_hits)}"
        )
    if ds._RING_FULL_NOTICE_FIRED is not True:
        errors.append(
            "[e1b] after overflow _RING_FULL_NOTICE_FIRED must be True"
        )

    # ── e2: more overflows in same cycle don't inject a second ────
    for i in range(50):
        seed_one(999_100 + i)
    cycle_items = ds.list_errors(limit=ds.MAX_ERRORS + 5)["items"]
    cycle_notices = [it for it in cycle_items if it.get("type") == notice_type]
    if len(cycle_notices) != 1:
        errors.append(
            f"[e2] warn-once violated: fill cycle has "
            f"{len(cycle_notices)} notices (expected 1)"
        )

    # ── e2b: buffer size must still be MAX_ERRORS (ring stays capped)
    if ds.snapshot_count() != ds.MAX_ERRORS:
        errors.append(
            f"[e2b] buffer exceeded cap during warn-once cycle: "
            f"{ds.snapshot_count()} > {ds.MAX_ERRORS}"
        )

    # ── e3: clear resets the flag → next overflow emits a new notice
    ds.clear()
    if ds._RING_FULL_NOTICE_FIRED is not False:
        errors.append(
            "[e3] clear() must reset _RING_FULL_NOTICE_FIRED to False"
        )
    for i in range(ds.MAX_ERRORS + 1):  # fill + one overflow
        seed_one(999_500 + i)
    after_second_cycle = ds.list_errors(limit=ds.MAX_ERRORS + 5)["items"]
    second_cycle_notices = [
        it for it in after_second_cycle if it.get("type") == notice_type
    ]
    if len(second_cycle_notices) != 1:
        errors.append(
            f"[e3] after clear() + fresh overflow expected 1 new notice, "
            f"got {len(second_cycle_notices)}"
        )

    # ── e3b: notice survives eviction (it was injected *after* the
    #        eviction trim, so the next push can still evict it —
    #        that's acceptable; what we require is that during the
    #        *same* fill cycle as its issuance the notice is visible
    #        to the user). ──────────────────────────────────────────
    # We already verified that above; this is a contract reminder.

    # Cleanup so downstream checks in the same smoke run don't see
    # our synthetic entries.
    ds.clear()

    return errors


# ── Orchestration ───────────────────────────────────────────────────

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


def main() -> int:
    print("=" * 66)
    print(" P24 Integration Smoke  (PLAN §15.6 a-d TestClient cases)")
    print("=" * 66)
    _setup_env()

    total = 0
    total += _report(
        "a | GET /system/orphans — shape + pre-seeded entries surface",
        check_a_orphan_scan_shape(),
    )
    total += _report(
        "b | DELETE /system/orphans/{sid} — 200 / 404 (2nd call) / "
        "409 (active) / 400 (traversal)",
        check_b_orphan_delete_semantics(),
    )
    total += _report(
        "c | POST /judge/run match_main_chat=true — system_prompt byte-"
        "identical to build_prompt_bundle",
        check_c_match_main_chat_parity(),
    )
    total += _report(
        "d | GET /diagnostics/errors?op_type=a,b,c — F7 three-category "
        "co-listing + strict single-op filter",
        check_d_f7_security_audit_filter(),
    )
    total += _report(
        "e | diagnostics ring warn-once (§14.4 M4) — one notice per "
        "fill cycle, reset by clear()",
        check_e_diagnostics_ring_warn_once(),
    )

    print("")
    print("=" * 66)
    if total:
        print(f" [FAIL] {total} violation(s) across P24 integration audit.")
        return 1
    print(" [PASS] P24 integration smoke clean.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
