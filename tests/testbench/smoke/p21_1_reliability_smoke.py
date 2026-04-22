"""P21.1 persistence reliability smoke — G1 / G2 / G8 coverage.

Run after touching ``persistence.py`` / ``session_router.py`` reliability
bits (PLAN.md §11 — P21.1 持久化可靠性加固 pass)::

    .venv\\Scripts\\python.exe tests/testbench/smoke/p21_1_reliability_smoke.py

Exits non-zero on any assertion failure.

Covers:

  G2 (Load TarballMissing → 400):
    * Delete the companion ``.memory.tar.gz`` externally between save
      and load → the load endpoint must surface 400 TarballMissing,
      **not** silently load with empty memory. Also asserts the active
      session is unchanged (pre-load).
    * Also verifies ``list_saved`` still renders the row as broken
      (``error`` field populated with ``TarballMissing:``), which is
      P21's earlier surfacing-gap fix.

  G8 (delete order — tar first, JSON second):
    * Unit test of ``persistence.delete_saved`` inspecting the order
      via a fault-injection mock on ``Path.unlink`` that raises on the
      **second** call. After the failure the leftover should be the
      JSON (tar gone, JSON present), **not** the other way round.
      Because a residual JSON with missing tarball renders as a
      recoverable broken row in the UI, whereas a residual tarball with
      missing JSON is an invisible disk leak.

  G1 (atomic write flush+fsync):
    * Source-level check: both ``_atomic_write_bytes`` and
      ``_atomic_write_json`` contain ``os.fsync`` between the body
      write and ``os.replace``. We can't portably crash-test fsync
      behavior cross-OS in a smoke test, so this is a regression guard
      against future edits that drop the flush.
"""
from __future__ import annotations

import base64
import inspect
import json
import os
import sys
import tempfile
from pathlib import Path
from unittest import mock


def main() -> int:
    tmp_data = Path(tempfile.mkdtemp())
    os.environ["TESTBENCH_DATA_DIR"] = str(tmp_data)

    from tests.testbench import config as tb_config
    tb_config.DATA_DIR = tmp_data
    tb_config.SAVED_SESSIONS_DIR = tmp_data / "saved_sessions"
    tb_config.AUTOSAVE_DIR = tmp_data / "saved_sessions" / "_autosave"
    tb_config.LOGS_DIR = tmp_data / "logs"
    for d in [
        tb_config.SAVED_SESSIONS_DIR, tb_config.AUTOSAVE_DIR, tb_config.LOGS_DIR,
    ]:
        d.mkdir(parents=True, exist_ok=True)

    from fastapi.testclient import TestClient
    from tests.testbench.pipeline import persistence as p
    from tests.testbench.server import create_app
    from tests.testbench.session_store import get_session_store

    # ── G1: source-level fsync regression guard ────────────────────
    src_bytes = inspect.getsource(p._atomic_write_bytes)
    src_json = inspect.getsource(p._atomic_write_json)
    for name, src in [("_atomic_write_bytes", src_bytes),
                      ("_atomic_write_json", src_json)]:
        assert "fh.flush()" in src, (
            f"G1 regression: {name} missing fh.flush() — "
            "see PLAN §11 G1"
        )
        assert "os.fsync" in src, (
            f"G1 regression: {name} missing os.fsync — "
            "see PLAN §11 G1"
        )
    print("G1: atomic_write fsync guards present")

    app = create_app()
    client = TestClient(app)

    # Bootstrap session.
    r = client.post("/api/session", json={"name": "g2_fixture"})
    assert r.status_code == 201, r.text
    session = get_session_store().require()
    session.messages.append({"role": "user", "content": "canary"})
    session.persona = {"character_name": "Canary"}

    # Save archive "g2" so we can then delete its tarball.
    r = client.post(
        "/api/session/save_as",
        json={"name": "g2", "redact_api_keys": True},
    )
    assert r.status_code == 200, r.text

    tar_path = tb_config.SAVED_SESSIONS_DIR / "g2.memory.tar.gz"
    json_path = tb_config.SAVED_SESSIONS_DIR / "g2.json"
    assert tar_path.exists(), "tarball companion should exist after save"
    assert json_path.exists(), "archive JSON should exist after save"

    # ── G2 (list_saved surfaces broken): delete tarball out-of-band,
    # list_saved should mark the row as broken so the UI disables the
    # Load button — this was the earlier P21 broken-archive detection
    # fix we verify still works here.
    tar_path.unlink()
    listing = client.get("/api/session/saved").json()["items"]
    g2_meta = next(m for m in listing if m["name"] == "g2")
    assert g2_meta.get("error"), (
        f"list_saved should mark missing-tarball row as broken, got: {g2_meta}"
    )
    assert "TarballMissing" in g2_meta["error"], g2_meta["error"]
    print("G2/part1: list_saved surfaces TarballMissing as broken row")

    # ── G2 (load rejects TarballMissing): clicking Load must hard-fail
    # with 400, NOT silently load with empty memory (pre-P21.1 regr).
    # Also assert the active session (still "g2_fixture") is unchanged.
    pre_load_session_id = get_session_store().require().id
    r = client.post("/api/session/load/g2")
    assert r.status_code == 400, (
        f"load should reject TarballMissing with 400, got {r.status_code}: {r.text}"
    )
    assert r.json()["detail"]["error_type"] == "TarballMissing", r.json()
    post_load_session_id = get_session_store().require().id
    assert pre_load_session_id == post_load_session_id, (
        "active session must be untouched when load fails pre-destroy"
    )
    print("G2/part2: load/<bad> returns 400 TarballMissing, no silent fallback")

    # Clean up the broken g2 row so it doesn't mess with subsequent tests.
    r = client.delete("/api/session/saved/g2")
    assert r.status_code == 200, r.text

    # ── G8: delete order — fault-inject to assert tar dies first ───
    # Re-save a fresh archive "g8", then simulate "we deleted the tar
    # but crashed before deleting the JSON". The residual should be
    # JSON-only (recoverable broken row), never tar-only (invisible
    # orphan).
    r = client.post(
        "/api/session/save_as",
        json={"name": "g8", "redact_api_keys": True},
    )
    assert r.status_code == 200, r.text

    g8_tar = tb_config.SAVED_SESSIONS_DIR / "g8.memory.tar.gz"
    g8_json = tb_config.SAVED_SESSIONS_DIR / "g8.json"
    assert g8_tar.exists() and g8_json.exists()

    # Monkey-patch Path.unlink to raise on the 2nd call (after the
    # first unlink has already removed the tarball).
    real_unlink = Path.unlink
    call_count = {"n": 0}

    def flaky_unlink(self, *args, **kwargs):
        call_count["n"] += 1
        if call_count["n"] == 2:
            raise OSError("simulated crash between delete steps")
        return real_unlink(self, *args, **kwargs)

    with mock.patch.object(Path, "unlink", flaky_unlink):
        try:
            p.delete_saved("g8")
        except p.PersistenceError as exc:
            assert exc.code == "WriteFailed", exc
        else:
            raise AssertionError("expected WriteFailed on fault-injected delete")

    assert not g8_tar.exists(), (
        "G8 regression: tarball should have been deleted first"
    )
    assert g8_json.exists(), (
        "G8 regression: JSON should still be on disk so list_saved "
        "can surface the row as broken-for-retry (idempotent closure)"
    )
    print("G8: delete order tar-first / JSON-second verified via fault injection")

    # Sanity: re-listing should show g8 as broken (TarballMissing) and
    # a retry delete closes the loop.
    listing = client.get("/api/session/saved").json()["items"]
    g8_meta = next((m for m in listing if m["name"] == "g8"), None)
    assert g8_meta is not None, "g8 should still be listed as broken row"
    assert g8_meta.get("error") and "TarballMissing" in g8_meta["error"]
    r = client.delete("/api/session/saved/g8")
    assert r.status_code == 200, r.text
    assert not g8_json.exists(), "retry delete should cleanly remove JSON"
    print("G8/retry: broken row can be cleanly re-deleted (idempotent)")

    # Cleanup.
    client.delete("/api/session")

    print("P21.1 RELIABILITY SMOKE OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
