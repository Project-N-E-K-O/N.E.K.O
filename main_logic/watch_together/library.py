"""Lossless, resumable legacy import and separate actual viewing records.

Original files are immutable content-addressed objects. Each source snapshot is
retained as a version of its original job, including incomplete preparations.
No timestamps are inferred as watching activity and no data is auto-evicted.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import tempfile
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone

JOB_ID = re.compile(r"[a-f0-9]{32}")


def application_library() -> "Library":
    from utils.config_manager import get_config_manager
    return Library(Path(get_config_manager().app_docs_dir) / "watch_together")


def digest(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


class Library:
    def __init__(self, root: Path):
        self.root = root.resolve()
        self.objects = self.root / "objects"
        self.objects.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS versions (
                    job TEXT NOT NULL, version TEXT NOT NULL, source TEXT NOT NULL,
                    manifest TEXT NOT NULL, PRIMARY KEY(job, version));
                CREATE TABLE IF NOT EXISTS watches (
                    id TEXT PRIMARY KEY, job TEXT NOT NULL, version TEXT NOT NULL,
                    character TEXT NOT NULL, progress REAL, last_watched TEXT,
                    completed INTEGER, events TEXT NOT NULL DEFAULT '[]');
                CREATE TABLE IF NOT EXISTS audio_assets (
                    name TEXT NOT NULL, sha256 TEXT NOT NULL, bytes INTEGER NOT NULL,
                    PRIMARY KEY(name, sha256));
            """)

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.root / "library.sqlite3", timeout=30)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    def store(self, source: Path) -> dict:
        """Commit only a verified copy. Interrupted temporary copies are reusable garbage."""
        if source.is_symlink():
            raise ValueError("Symlink sources are not supported")
        before = digest(source)
        target = self.objects / before
        if not target.exists() or digest(target) != before:
            fd, temporary = tempfile.mkstemp(prefix="import-", dir=self.objects)
            try:
                with os.fdopen(fd, "wb") as output, source.open("rb") as incoming:
                    for block in iter(lambda: incoming.read(1024 * 1024), b""):
                        output.write(block)
                    output.flush()
                    os.fsync(output.fileno())
                if digest(Path(temporary)) != before or digest(source) != before:
                    raise ValueError(f"Source changed during import: {source.name}")
                os.replace(temporary, target)
            finally:
                Path(temporary).unlink(missing_ok=True)
        return {"sha256": before, "bytes": target.stat().st_size}

    def import_sources(self, sources: list[Path], *, only_job=None, write_report=True) -> dict:
        report = {"sources": [], "jobs": 0, "versions": 0, "files": 0,
                  "bytes": 0, "differences": [], "verified": True}
        observed = {}
        jobs, versions = set(), set()
        for source in sources:
            source = source.resolve(strict=True)
            expected_path = source / "backup-manifest.json"
            expected = {}
            if expected_path.is_file():
                expected = {item["path"].replace("\\", "/"): item
                            for item in json.loads(expected_path.read_text(encoding="utf-8-sig"))}
                for name in expected:
                    parts = name.split('/')
                    if len(parts) < 2 or not JOB_ID.fullmatch(parts[0]) or any(p in {'', '.', '..'} for p in parts):
                        raise ValueError("Invalid archive manifest path")
                if only_job is not None:
                    expected = {name: item for name, item in expected.items() if name.split('/')[0] == only_job}
                for name in expected:
                    if not (source / name).is_file():
                        raise ValueError("Archive is missing files recorded by backup-manifest.json")
            encountered = set()
            count = 0
            for folder in sorted(source.iterdir()):
                if only_job is not None and folder.name != only_job:
                    continue
                if not folder.is_dir() or not JOB_ID.fullmatch(folder.name):
                    continue
                if folder.is_symlink():
                    raise ValueError("Symlink job directories are not supported")
                manifest = {}
                for file in sorted(folder.rglob("*")):
                    if file.is_symlink():
                        raise ValueError("Symlink resources are not supported")
                    if not file.is_file():
                        continue
                    relative = file.relative_to(folder).as_posix()
                    entry = self.store(file)
                    key = f"{folder.name}/{relative}"
                    encountered.add(key)
                    if key in expected:
                        check = expected[key]
                        if (entry["sha256"] != check["sha256"].lower()
                                or entry["bytes"] != check["bytes"]):
                            raise ValueError(f"Archive checksum mismatch: {key}")
                    prior = observed.get(key)
                    if prior and prior != entry["sha256"]:
                        report["differences"].append({"path": key, "before": prior,
                                                      "after": entry["sha256"]})
                    observed[key] = entry["sha256"]
                    manifest[relative] = entry
                    count += 1
                    report["files"] += 1
                    report["bytes"] += entry["bytes"]
                encoded = json.dumps(manifest, sort_keys=True, separators=(",", ":"))
                current_files = {file.relative_to(folder).as_posix() for file in folder.rglob('*') if file.is_file()}
                if current_files != set(manifest) or any(digest(folder / name) != item['sha256'] for name, item in manifest.items()):
                    raise ValueError(f"Source changed during import: {folder.name}; retry safely")
                version = hashlib.sha256(encoded.encode()).hexdigest()
                with self.connect() as db:
                    db.execute("INSERT OR IGNORE INTO versions VALUES (?, ?, ?, ?)",
                               (folder.name, version, str(source), encoded))
                jobs.add(folder.name)
                versions.add((folder.name, version))
            if set(expected) - encountered:
                raise ValueError("Archive is missing files recorded by backup-manifest.json")
            report["sources"].append({"path": str(source), "files": count,
                                      "archive_checks": len(expected)})
        report.update(jobs=len(jobs), versions=len(versions), unique_files=len(observed))
        report_path = self.root / f"migration-{uuid.uuid4().hex}.json"
        if write_report:
            report_path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
        return {**report, "report": str(report_path) if write_report else None}

    def import_audio_assets(self, source: Path) -> list[dict]:
        result = []
        for path in sorted(source.glob("*.mp3")):
            entry = self.store(path)
            with self.connect() as db:
                db.execute("INSERT OR IGNORE INTO audio_assets VALUES(?,?,?)",
                           (path.name, entry["sha256"], entry["bytes"]))
            result.append({"name": path.name, **entry})
        (self.root / "audio-assets-manifest.json").write_text(
            json.dumps(result, indent=2), encoding="utf-8")
        return result

    def manifest(self, job: str, version: str) -> dict:
        with self.connect() as db:
            row = db.execute("SELECT manifest FROM versions WHERE job=? AND version=?",
                             (job, version)).fetchone()
        if row is None:
            raise KeyError(job)
        return json.loads(row["manifest"])

    def resource(self, job: str, version: str, name: str) -> Path:
        entry = self.manifest(job, version).get(name)
        if entry is None:
            raise KeyError(name)
        return self.objects / entry["sha256"]

    def timeline(self, job: str, version: str) -> dict:
        data = json.loads(self.resource(job, version, "timeline.json").read_text(encoding="utf-8-sig"))
        if not isinstance(data, dict):
            raise ValueError("Timeline must be an object")
        prefix = f"/api/watch-together/media/{job}/{version}/"
        def remap(value):
            if isinstance(value, str) and value.startswith(f"/media/{job}/"):
                return prefix + value[len(f"/media/{job}/"):]
            if isinstance(value, dict):
                return {k: remap(v) for k, v in value.items()}
            if isinstance(value, list):
                return [remap(v) for v in value]
            return value
        return {**remap(data), "id": job, "version": version}

    def history(self) -> list[dict]:
        with self.connect() as db:
            rows = db.execute("SELECT job, version FROM versions ORDER BY job, version").fetchall()
        result = []
        for row in rows:
            try:
                timeline = self.timeline(row["job"], row["version"])
            except (KeyError, ValueError, OSError):
                timeline = {"status": "incomplete"}
            result.append({"job": row["job"], "version": row["version"],
                           "title": timeline.get("title", row["job"]),
                           "status": timeline.get("status"), "usage": timeline.get("usage"),
                           "progress": None, "last_watched": None, "completed": None})
        return result

    def start_watch(self, job: str, version: str, character: str) -> str:
        self.manifest(job, version)
        identifier = uuid.uuid4().hex
        with self.connect() as db:
            db.execute("INSERT INTO watches(id,job,version,character) VALUES(?,?,?,?)",
                       (identifier, job, version, character))
        return identifier

    def record_watch(self, identifier: str, progress: float, event: dict) -> None:
        now = datetime.now(timezone.utc).isoformat()
        with self.connect() as db:
            row = db.execute("SELECT events FROM watches WHERE id=?", (identifier,)).fetchone()
            if row is None:
                raise KeyError(identifier)
            events = json.loads(row["events"])
            events.append({**event, "position": progress, "recorded_at": now})
            watched = event.get("type") in {"play", "progress", "ended", "audio-started"}
            db.execute("""UPDATE watches SET progress=?,
                       last_watched=CASE WHEN ? THEN ? ELSE last_watched END,
                       completed=CASE WHEN ? THEN 1 ELSE completed END, events=? WHERE id=?""",
                       (progress, watched, now, any(item.get('type') == 'ended' for item in events), json.dumps(events), identifier))

    def watches(self) -> list[dict]:
        with self.connect() as db:
            return [dict(row) for row in db.execute(
                "SELECT id,job,version,character,progress,last_watched,completed FROM watches ORDER BY last_watched DESC")]

    def verify(self) -> dict:
        failures, checked = [], {}
        with self.connect() as db:
            versions = list(db.execute("SELECT job, version, manifest FROM versions"))
            assets = list(db.execute("SELECT * FROM audio_assets"))
        file_count, logical_bytes = 0, 0
        for row in versions:
            for name, entry in json.loads(row['manifest']).items():
                key = entry['sha256']
                path = self.objects / key
                if key not in checked:
                    checked[key] = path.is_file() and digest(path) == key and path.stat().st_size == entry['bytes']
                if not checked[key]:
                    failures.append(f"{row['job']}/{row['version']}/{name}")
                file_count += 1
                logical_bytes += entry['bytes']
        for entry in assets:
            path = self.objects / entry['sha256']
            if not path.is_file() or digest(path) != entry['sha256'] or path.stat().st_size != entry['bytes']:
                failures.append(entry['name'])
        return {'jobs':len({row['job'] for row in versions}), 'versions':len(versions),
                'files':file_count, 'logical_bytes':logical_bytes, 'audio_assets':len(assets),
                'unique_objects':len(checked), 'failures':failures, 'verified':not failures}
