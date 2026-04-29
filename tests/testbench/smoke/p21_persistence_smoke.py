"""P21 persistence smoke — backend TestClient.

Run once after touching ``persistence.py`` / ``session_router.py``::

    .venv\\Scripts\\python.exe tests/testbench/smoke/p21_persistence_smoke.py

Exits non-zero on any assertion failure. Mocks network (no LLM calls
happen during save/load/list/delete/import — they are disk-only).

Covers:
  1. No session → save / save_as return 404.
  2. Invalid archive name → 400 with ``InvalidName`` code.
  3. ``save_as`` twice same name → second is 409 ``ArchiveExists``.
  4. ``save`` (overwrite=True) twice same name → both 200.
  5. ``/saved`` lists archives newest-first.
  6. ``load/{name}`` replaces the session, writes pre_load_backup.
  7. ``load/nonesuch`` → 404.
  8. ``delete`` happy path + 404 on second call.
  9. ``import`` accepts a payload produced by ``export_to_payload``;
     invalid payload shape → 400.
 10. api_key is persisted as ``<redacted>`` on disk.
 11. Schema version 999 JSON → 400 ``SchemaVersionTooNew``.
"""
from __future__ import annotations

import base64
import json
import os
import sys
import tempfile
from pathlib import Path


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

    app = create_app()
    client = TestClient(app)

    # 1. No session → save 404.
    r = client.post("/api/session/save", json={"name": "foo"})
    assert r.status_code == 404, r.text

    # Create a session and inject data.
    r = client.post("/api/session", json={"name": "smoke_session"})
    assert r.status_code == 201, r.text
    from tests.testbench.session_store import get_session_store
    session = get_session_store().require()
    session.messages.append({"role": "user", "content": "hello"})
    session.model_config = {
        "chat": {"api_key": "sk-LEAK", "model": "gpt-4o"},
        "judge": {"api_key": "", "model": "gpt-4o"},
    }
    session.persona = {"character_name": "NEKO"}

    # 2. Invalid name.
    r = client.post(
        "/api/session/save_as", json={"name": "../bad", "redact_api_keys": True},
    )
    assert r.status_code == 400, r.text
    assert r.json()["detail"]["error_type"] == "InvalidName", r.json()

    # 3. save_as twice conflict.
    r = client.post(
        "/api/session/save_as", json={"name": "a1", "redact_api_keys": True},
    )
    assert r.status_code == 200, r.text
    r2 = client.post(
        "/api/session/save_as", json={"name": "a1", "redact_api_keys": True},
    )
    assert r2.status_code == 409, r2.text
    assert r2.json()["detail"]["error_type"] == "ArchiveExists"

    # 4. save overwrite twice.
    r3 = client.post("/api/session/save", json={"name": "a1", "redact_api_keys": True})
    assert r3.status_code == 200, r3.text
    r4 = client.post("/api/session/save", json={"name": "a1", "redact_api_keys": True})
    assert r4.status_code == 200, r4.text

    # 5. List — newest first. Sleep so saved_at strings differ
    #    (ISO seconds resolution; two saves in the same second tie-break
    #    on iterdir order which is filesystem-defined).
    import time
    time.sleep(1.1)
    r = client.post(
        "/api/session/save_as", json={"name": "a2", "redact_api_keys": True},
    )
    assert r.status_code == 200, r.text
    r = client.get("/api/session/saved")
    assert r.status_code == 200, r.text
    items = r.json()["items"]
    names = [m["name"] for m in items]
    assert "a1" in names and "a2" in names, names
    assert names[0] == "a2", f"expected newest first, got {names}"

    # 10. API key redaction on disk.
    with (tb_config.SAVED_SESSIONS_DIR / "a1.json").open("r", encoding="utf-8") as fh:
        data = json.load(fh)
    assert data["session"]["model_config"]["chat"]["api_key"] == "<redacted>", data[
        "session"
    ]["model_config"]

    # 6 + 7. Load happy path + 404.
    r = client.post("/api/session/load/a1")
    assert r.status_code == 200, r.text
    assert r.json()["ok"] is True
    assert (tb_config.AUTOSAVE_DIR).iterdir(), "pre_load_backup should exist"
    assert any(
        p_.name.startswith("pre_load_") for p_ in tb_config.AUTOSAVE_DIR.iterdir()
    ), "no pre_load_backup file found"
    new_session = get_session_store().require()
    assert new_session.messages == [{"role": "user", "content": "hello"}]
    assert new_session.persona["character_name"] == "NEKO"
    assert new_session.model_config["chat"]["api_key"] == "<redacted>"

    r = client.post("/api/session/load/nonesuch")
    assert r.status_code == 404, r.text
    assert r.json()["detail"]["error_type"] == "ArchiveNotFound"

    # 8. Delete + 404 on second call.
    r = client.delete("/api/session/saved/a1")
    assert r.status_code == 200, r.text
    r = client.delete("/api/session/saved/a1")
    assert r.status_code == 404, r.text

    # 9. Import.
    archive = p.load_archive("a2")
    tar = p.read_tarball_bytes("a2")
    payload = p.export_to_payload(archive, tar)
    r = client.post(
        "/api/session/import",
        json={"payload": payload, "name": "imported", "overwrite": True},
    )
    assert r.status_code == 200, r.text
    assert r.json()["name"] == "imported"

    r = client.post("/api/session/import", json={"payload": {"wrong": "shape"}})
    assert r.status_code == 400, r.text

    # 11. Schema version 999 → 400.
    bad_payload = {
        "archive": {
            "archive_kind": "testbench_session",
            "schema_version": 999,
            "name": "future",
            "saved_at": "2099-01-01T00:00:00",
            "redact_api_keys": True,
            "session": {"id": "x", "name": "x", "created_at": "x"},
            "snapshots": {"hot": [], "cold_meta": []},
        },
        "tarball_b64": base64.b64encode(b"").decode("ascii"),
    }
    r = client.post(
        "/api/session/import", json={"payload": bad_payload, "name": "future"},
    )
    assert r.status_code == 400, r.text
    assert r.json()["detail"]["error_type"] == "SchemaVersionTooNew", r.json()

    # Round-trip: load the imported archive works.
    r = client.post("/api/session/load/imported")
    assert r.status_code == 200, r.text

    # Cleanup.
    client.delete("/api/session")

    print("P21 BACKEND SMOKE OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
