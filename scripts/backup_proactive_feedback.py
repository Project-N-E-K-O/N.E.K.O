"""Create an independent verified SQLite snapshot; never restore or downgrade live data.

Run with ``uv run python scripts/backup_proactive_feedback.py --database ...
--output-dir <new-directory>``. A completed manifest is the publication marker.
Failure leaves the owned directory and in-progress marker for inspection.
The database and manifest contain private data; keep the directory private.
"""
from __future__ import annotations

import argparse
from contextlib import closing
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import time


def _write_json_exclusive(path, value):
    with path.open("x", encoding="utf-8") as stream:
        json.dump(value, stream, ensure_ascii=False, sort_keys=True, indent=2)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())


def backup_feedback(database: Path, output_dir: Path, *, timeout_seconds: float = 30):
    """SQLite online backup, then verify and atomically publish its manifest.

    This is a preservation artifact, not a v2-to-v1 converter. Use a v2-compatible
    application for rollback; the pre-upgrade v1 snapshot cannot include v2 facts.
    No existing output directory is reused and no source statement writes data.
    The deadline covers copying, verification, hashing and manifest publication.
    SQLite work and hash blocks cooperate with cancellation; an individual OS
    filesystem call cannot be interrupted, so its return is checked before publish.
    """
    if not 0 < timeout_seconds <= 60:
        raise ValueError("timeout_seconds must be between 0 and 60")
    deadline = time.monotonic() + timeout_seconds

    def check_deadline():
        if time.monotonic() >= deadline:
            raise TimeoutError("feedback backup deadline exceeded")

    source = database.resolve(strict=True)
    if not source.is_file():
        raise ValueError("database must be an existing file")
    # mkdir, rather than a preceding exists check, owns this destination atomically.
    output_dir = output_dir.absolute()
    output_dir.mkdir(mode=0o700, parents=False, exist_ok=False)
    target = output_dir / "feedback.sqlite3"
    _write_json_exclusive(output_dir / "backup-in-progress.json", {
        "format": "neko-feedback-backup-v1", "source": str(source),
        "snapshot": target.name, "started_at": time.time(),
        "recovery": "Preserve this directory on failure. Only manifest.json confirms completion.",
    })
    check_deadline()

    def progress(status, remaining, total):
        check_deadline()

    def sql_progress():
        return int(time.monotonic() >= deadline)

    # Source URI read-only is enforced by SQLite, including for a live WAL database.
    # Let backup's retry/progress loop check the deadline instead of hiding a
    # fixed busy wait inside a SQLite call.
    with closing(sqlite3.connect(source.as_uri() + "?mode=ro", uri=True, timeout=0)) as original:
        with closing(sqlite3.connect(target, timeout=0)) as snapshot:
            original.backup(snapshot, pages=256, progress=progress, sleep=0.05)
            check_deadline()
            snapshot.set_progress_handler(sql_progress, 10_000)
            try:
                integrity = snapshot.execute("PRAGMA integrity_check").fetchall()
                check_deadline()
                if integrity != [("ok",)]:
                    raise ValueError("snapshot failed SQLite integrity verification")
                if snapshot.execute("PRAGMA foreign_key_check").fetchone() is not None:
                    raise ValueError("snapshot failed foreign key verification")
                check_deadline()
                version = snapshot.execute("PRAGMA user_version").fetchone()[0]
                names = [row[0] for row in snapshot.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name")]
                counts = {}
                for name in names:
                    check_deadline()
                    counts[name] = snapshot.execute('SELECT count(*) FROM "' + name.replace('"', '""') + '"').fetchone()[0]
            except sqlite3.OperationalError:
                check_deadline()  # Convert a deadline-induced SQL interrupt to TimeoutError.
                raise
            finally:
                snapshot.set_progress_handler(None, 0)
    check_deadline()
    sha = hashlib.sha256()
    with target.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            check_deadline()
            sha.update(block)
    check_deadline()
    manifest = {"format": "neko-feedback-backup-v1", "completed_at": time.time(),
                "source": str(source), "snapshot": target.name, "sha256": sha.hexdigest(),
                "snapshot_bytes": target.stat().st_size, "sqlite_schema_version": version,
                "tables": counts, "integrity": "ok", "foreign_keys": "ok",
                "application_rollback": "Use a schema-v2-compatible release; this tool does not downgrade or restore live data."}
    staging = output_dir / "manifest.json.tmp"
    check_deadline()
    _write_json_exclusive(staging, manifest)
    check_deadline()
    os.replace(staging, output_dir / "manifest.json")
    # Keep the original marker as provenance. The complete manifest takes precedence.
    return manifest


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        manifest = backup_feedback(args.database, args.output_dir)
    except (OSError, sqlite3.Error, ValueError, TimeoutError):
        print(json.dumps({"ok": False, "error": "backup_failed",
                          "output_dir": str(args.output_dir),
                          "recovery": "Preserve any created directory; it may contain an incomplete snapshot."}))
        return 1
    print(json.dumps({"ok": True, "output_dir": str(args.output_dir),
                      "sqlite_schema_version": manifest["sqlite_schema_version"],
                      "sha256": manifest["sha256"]}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
