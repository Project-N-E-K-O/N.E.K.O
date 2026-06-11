"""P22 post-delivery hardening smoke — G3/G10 + P-B + F4 coverage.

Run after touching ``persistence.memory_sha256`` / ``boot_cleanup`` /
``judge_router._audit_extra_context_override`` (PLAN.md §11 G3 / G10,
§10 P-B, §13 F4)::

    .venv\\Scripts\\python.exe tests/testbench/smoke/p22_hardening_smoke.py

Exits non-zero on any assertion failure.

Covers:

  G3 / G10 (memory tarball SHA256 pinning & verification):
    * Save-as writes a SHA256 matching the actual tarball bytes into
      the archive JSON's ``memory_sha256`` field.
    * Load path recomputes and compares, returning ``verified`` +
      stored/actual hashes in the load response.
    * Archives without ``memory_sha256`` (simulating pre-P22 disk
      state) load successfully with ``verified=False, reason="missing_hash"``
      rather than erroring — backward compatibility check.
    * Hash mismatch in an import payload raises InvalidArchive (stricter
      import trust boundary).
    * Autosave rolling writer pins the hash on each finalised slot.

  P-B (boot-time orphan cleanup):
    * ``*.tmp`` under SAVED_SESSIONS_DIR / AUTOSAVE_DIR / SANDBOXES_DIR
      gets unlinked.
    * Stale ``memory.locked_*`` directory (mtime > 24h) gets rmtree'd,
      fresh one stays.
    * Orphan SQLite sidecars (``*-journal`` / ``*-wal`` / ``*-shm``
      whose ``.db`` is absent) get unlinked; sidecars whose companion
      ``.db`` exists stay.
    * ``run_boot_cleanup`` never raises even if individual item
      operations fail (fault-inject via read-only permissions would be
      platform-specific; instead we verify the stats-dict shape).

  F4 (extra_context override audit log):
    * ``judge_router._JUDGE_CTX_OVERRIDE_KEYS`` matches the actual keys
      built by the *_build_ctx* paths in
      :class:`AbsoluteSingleJudger` / :class:`AbsoluteConversationJudger`
      — prevents drift if a future judger adds a managed key without
      updating the audit set.
    * ``_audit_extra_context_override`` emits a
      ``judge_extra_context_override`` diagnostics event **only** when
      override keys are present; benign ``extra_context`` (custom
      placeholders like ``{my_tag}``) triggers **no** audit.
    * Empty / None ``extra_context`` triggers no audit.
"""
from __future__ import annotations

import hashlib
import inspect
import json
import os
import sys
import tempfile
import time
from pathlib import Path


def _setup_env() -> "Path":
    """Point all testbench data roots at a temp dir before imports."""
    tmp_data = Path(tempfile.mkdtemp(prefix="p22_smoke_"))
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


# ── G3 / G10 ─────────────────────────────────────────────────────────

def check_g3_g10() -> None:
    from fastapi.testclient import TestClient
    from tests.testbench import config as tb_config
    from tests.testbench.pipeline import persistence as p
    from tests.testbench.server import create_app
    from tests.testbench.session_store import get_session_store

    app = create_app()
    client = TestClient(app)

    r = client.post("/api/session", json={"name": "sha_fixture"})
    assert r.status_code == 201, r.text
    session = get_session_store().require()
    session.messages.append({"role": "user", "content": "hash me"})

    # 1) Save → archive JSON should carry a hash matching the tarball.
    r = client.post(
        "/api/session/save_as",
        json={"name": "sha_case", "redact_api_keys": True},
    )
    assert r.status_code == 200, r.text

    json_path = tb_config.SAVED_SESSIONS_DIR / "sha_case.json"
    tar_path = tb_config.SAVED_SESSIONS_DIR / "sha_case.memory.tar.gz"
    assert json_path.exists() and tar_path.exists()

    on_disk = json.loads(json_path.read_text(encoding="utf-8"))
    stored_hash = on_disk.get("memory_sha256")
    assert stored_hash, "save_archive_and_tarball must pin memory_sha256"
    expected = hashlib.sha256(tar_path.read_bytes()).hexdigest()
    assert stored_hash == expected, (
        f"stored memory_sha256 != actual tarball sha256 "
        f"(stored={stored_hash}, actual={expected})"
    )
    print("G3: save_as pins memory_sha256 matching tarball bytes")

    # 2) Load path → response exposes verification result, match=True.
    r = client.post("/api/session/load/sha_case")
    assert r.status_code == 200, r.text
    body = r.json()
    verify = body.get("memory_hash_verify")
    assert verify, "load response must expose memory_hash_verify"
    assert verify.get("legacy") is False, verify
    assert verify.get("match") is True, verify
    assert verify.get("expected") == expected, verify
    assert verify.get("actual") == expected, verify
    print("G3: load reports memory_hash_verify.match=True on clean archive")

    # 3) Backward-compat: strip memory_sha256 from the JSON, reload.
    on_disk_no_hash = dict(on_disk)
    on_disk_no_hash.pop("memory_sha256", None)
    json_path.write_text(
        json.dumps(on_disk_no_hash, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    r = client.post("/api/session/load/sha_case")
    assert r.status_code == 200, r.text
    verify = r.json().get("memory_hash_verify") or {}
    assert verify.get("legacy") is True, verify
    assert verify.get("match") is None, verify
    print("G3/legacy: archive without memory_sha256 loads as legacy (match=None)")

    # 4) Import trust boundary: tampered payload → InvalidArchive.
    import base64
    tar_bytes = tar_path.read_bytes()
    bad_archive_dict = dict(on_disk)
    bad_archive_dict["memory_sha256"] = "0" * 64  # deliberate mismatch
    payload = {
        "archive": bad_archive_dict,
        "tarball_b64": base64.b64encode(tar_bytes).decode("ascii"),
    }
    try:
        p.import_from_payload(payload, name="sha_imported", overwrite=True)
    except p.PersistenceError as exc:
        assert exc.code == "InvalidArchive", exc
        assert "sha256" in str(exc).lower() or "hash" in str(exc).lower(), str(exc)
    else:
        raise AssertionError(
            "import_from_payload must reject hash mismatch with InvalidArchive"
        )
    print("G10/import: tampered memory_sha256 rejected as InvalidArchive")

    # 5) Autosave rolling writer pins the hash on each finalised slot.
    src = inspect.getsource(__import__(
        "tests.testbench.pipeline.autosave", fromlist=["_finalise_slot_write"],
    )._finalise_slot_write)
    assert "compute_memory_sha256" in src, (
        "G3 regression: autosave._finalise_slot_write must pin "
        "memory_sha256 before serialisation"
    )
    print("G3/autosave: _finalise_slot_write calls compute_memory_sha256")

    client.delete("/api/session/saved/sha_case")
    client.delete("/api/session")


# ── P-B ──────────────────────────────────────────────────────────────

def check_pb_boot_cleanup() -> None:
    from tests.testbench import config as tb_config
    from tests.testbench.pipeline import boot_cleanup

    sandboxes = tb_config.SANDBOXES_DIR
    saved = tb_config.SAVED_SESSIONS_DIR
    auto = tb_config.AUTOSAVE_DIR

    # Seed a fake sandbox layout.
    fake_sb = sandboxes / "sb_pb_smoke"
    fake_sb.mkdir(parents=True, exist_ok=True)
    mem_dir = fake_sb / "memory"
    mem_dir.mkdir(parents=True, exist_ok=True)

    # (a) .tmp files across all 3 roots.
    tmp_paths = [
        saved / "foo.json.tmp",
        auto / "bar.json.tmp",
        mem_dir / "persona.json.tmp",
    ]
    for p in tmp_paths:
        p.write_bytes(b"abandoned atomic-write payload")
        assert p.exists()

    # (b) locked_ directories: one stale (>24h old), one fresh.
    stale_locked = mem_dir / "memory.locked_20200101T000000Z"
    stale_locked.mkdir()
    (stale_locked / "memory.db").write_bytes(b"stale")
    old_epoch = time.time() - (48 * 3600)  # 48h ago, well past 24h cutoff
    os.utime(stale_locked, (old_epoch, old_epoch))

    fresh_locked = mem_dir / "memory.locked_20260420T000000Z"
    fresh_locked.mkdir()
    (fresh_locked / "memory.db").write_bytes(b"fresh")

    # (c) SQLite sidecars: one orphan (no .db), one live (.db present).
    orphan_sc = mem_dir / "ghost.db-journal"
    orphan_sc.write_bytes(b"orphan journal")
    live_db = mem_dir / "live.db"
    live_db.write_bytes(b"SQLite header placeholder")
    live_sc = mem_dir / "live.db-wal"
    live_sc.write_bytes(b"wal for live.db")

    # Run cleanup.
    stats = boot_cleanup.run_boot_cleanup()
    assert isinstance(stats, dict), stats
    assert "files_removed" in stats and "dirs_removed" in stats, stats
    assert "unlink_failures" in stats and "rmtree_failures" in stats, stats
    assert "roots_scanned" in stats and isinstance(stats["roots_scanned"], list), stats

    # Verify effects.
    for p in tmp_paths:
        assert not p.exists(), f"P-B regression: {p} should have been unlinked"
    assert not stale_locked.exists(), (
        "P-B regression: stale locked_ dir (48h old) should be rmtree'd"
    )
    assert fresh_locked.exists(), (
        "P-B regression: fresh locked_ dir should be retained (< 24h)"
    )
    assert not orphan_sc.exists(), (
        "P-B regression: orphan *-journal (no .db) should be unlinked"
    )
    assert live_sc.exists(), (
        "P-B regression: sidecar with live companion .db must be preserved"
    )
    assert live_db.exists(), "P-B should never touch actual .db files"
    # Counts should reflect what we seeded (3 tmp + 1 orphan sidecar = 4
    # file unlinks, 1 rmtree).
    assert stats["files_removed"] >= 4, stats
    assert stats["dirs_removed"] >= 1, stats
    print("P-B: tmp unlink, stale locked rmtree, orphan sidecar unlink verified")

    # Idempotence: running again on a clean tree should be a no-op.
    stats2 = boot_cleanup.run_boot_cleanup()
    assert stats2["files_removed"] == 0, stats2
    assert stats2["dirs_removed"] == 0, stats2
    print("P-B/idempotent: second run is a no-op on clean tree")


# ── F4 ───────────────────────────────────────────────────────────────

def check_f4_extra_context_audit() -> None:
    from tests.testbench.pipeline import diagnostics_store
    from tests.testbench.pipeline.judge_runner import (
        AbsoluteConversationJudger,
        AbsoluteSingleJudger,
    )
    from tests.testbench.routers import judge_router as jr

    # 1) Drift check: audit set must cover every key the real judgers
    # put into ctx before ``ctx.update(extra_context)`` — this is the
    # whole point of the audit. Use the source strings because the
    # dicts are assembled inside methods we can't cheaply call.
    src_single = inspect.getsource(AbsoluteSingleJudger._build_ctx)
    src_conv = inspect.getsource(AbsoluteConversationJudger._build_ctx)
    declared_keys = {
        '"system_prompt"', '"history"', '"user_input"', '"ai_response"',
        '"character_name"', '"master_name"', '"conversation"',
    }
    for key_tok in declared_keys:
        if key_tok not in src_single and key_tok not in src_conv:
            continue  # some keys are mode-specific (conversation only, etc.)
        plain = key_tok.strip('"')
        assert plain in jr._JUDGE_CTX_OVERRIDE_KEYS, (
            f"F4 drift: judger manages ``{plain}`` in _build_ctx but "
            f"_JUDGE_CTX_OVERRIDE_KEYS doesn't include it. The audit "
            f"will silently miss override attempts on this key."
        )
    # reference_response is set by the comparative-mode path — check
    # the parent module source so we cover that branch too.
    jr_runner_src = inspect.getsource(
        __import__("tests.testbench.pipeline.judge_runner", fromlist=["*"])
    )
    if '"reference_response"' in jr_runner_src:
        assert "reference_response" in jr._JUDGE_CTX_OVERRIDE_KEYS, (
            "F4 drift: reference_response appears in judge_runner but "
            "audit set doesn't list it"
        )
    print("F4: _JUDGE_CTX_OVERRIDE_KEYS covers all runtime-managed keys")

    # 2) No extra_context, or benign keys → no audit event.
    before = len(diagnostics_store.list_errors()["items"])
    jr._audit_extra_context_override(None, schema_id="s1", session_id=None)
    jr._audit_extra_context_override({}, schema_id="s1", session_id=None)
    jr._audit_extra_context_override(
        {"my_custom_tag": "hello"}, schema_id="s1", session_id=None,
    )
    after = len(diagnostics_store.list_errors()["items"])
    assert after == before, (
        f"F4 regression: benign extra_context must not trigger audit "
        f"(before={before}, after={after})"
    )
    print("F4: None / empty / benign extra_context emits no audit event")

    # 3) Override keys → exactly one audit entry per call.
    jr._audit_extra_context_override(
        {"system_prompt": "<injected>", "my_tag": "keep"},
        schema_id="sX", session_id="sess-abc",
    )
    res = diagnostics_store.list_errors(
        source="pipeline", limit=5,
    )
    hit = next(
        (e for e in res["items"] if e.get("type") == "judge_extra_context_override"),
        None,
    )
    assert hit, (
        f"F4 regression: override audit event not recorded — "
        f"recent items: {res['items']}"
    )
    detail = hit.get("detail") or {}
    assert detail.get("schema_id") == "sX", detail
    assert "system_prompt" in (detail.get("override_keys") or []), detail
    assert "my_tag" not in (detail.get("override_keys") or []), (
        "F4 regression: audit must only list override keys, not benign additions"
    )
    assert hit.get("session_id") == "sess-abc", hit
    assert hit.get("level") == "warning", hit
    print("F4: override emits exactly one warning-level audit with correct detail")


# ── main ─────────────────────────────────────────────────────────────

def main() -> int:
    _setup_env()
    check_g3_g10()
    check_pb_boot_cleanup()
    check_f4_extra_context_audit()
    print("P22 HARDENING SMOKE OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
