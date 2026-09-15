"""Lossless, resumable legacy import and separate actual viewing records.

Original files are immutable content-addressed objects. Each source snapshot is
retained as a version of its original job, including incomplete preparations.
No timestamps are inferred as watching activity and no data is auto-evicted.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
from pathlib import Path
import re
import sqlite3
import subprocess
import tempfile
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from urllib.parse import quote, unquote
from functools import lru_cache
from threading import Lock

JOB_ID = re.compile(r"[a-f0-9]{32}")
# Playback preload budget for distinct reaction audio files; the preparation
# engine enforces the same limits before it marks a job ready.
MAX_REACTION_AUDIO_FILES = 256
MAX_REACTION_AUDIO_BYTES = 64 * 1024 * 1024


_library_lock = Lock()


def _probe_media(path, size, modified, role):
    from main_logic.watch_together.engine import media_binary
    try:
        binary = media_binary('ffprobe')
    except FileNotFoundError:
        return None  # Keep the archive, but unknown media is not playback-ready.
    return _probe_media_cached(binary, path, size, modified, role)


@lru_cache(maxsize=4096)
def _probe_media_cached(binary, path, size, modified, role):
    """Probe immutable objects once; stat keys invalidate externally damaged files."""
    try:
        result = subprocess.run([binary, '-v', 'error', '-show_streams',
            '-of', 'json', str(path)], capture_output=True, timeout=10,
            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        streams = json.loads(result.stdout).get('streams', []) if result.returncode == 0 else []
        return any(stream.get('codec_type') == role and stream.get('codec_name') not in (None, 'unknown') for stream in streams)
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return False


@lru_cache(maxsize=8)
def _library_for_root(root):
    return Library(root)


def application_library() -> "Library":
    from utils.config_manager import get_config_manager
    root = (Path(get_config_manager().app_docs_dir) / "watch_together").resolve()
    with _library_lock:
        return _library_for_root(root)


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
                CREATE INDEX IF NOT EXISTS watches_recent ON watches(last_watched DESC, id DESC);
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
        path = self.resource(job, version, "timeline.json")
        if path.stat().st_size > 8 * 1024 * 1024:
            raise ValueError("Timeline exceeds load budget")
        invalid_constants = []
        try:
            def reject_constant(value):
                invalid_constants.append(value)
                return None
            data = json.loads(path.read_text(encoding="utf-8-sig"), parse_constant=reject_constant)
        except RecursionError as error:
            raise ValueError("Timeline nesting exceeds load budget") from error
        if not isinstance(data, dict):
            raise ValueError("Timeline must be an object")
        if isinstance(data.get('events'), list) and len(data['events']) > 1000:
            data = {**data, 'status': 'incomplete', 'events': []}
        prefix = f"/api/watch-together/media/{job}/{version}/"
        def clean_text(value):
            # JSON accepts escaped lone UTF-16 surrogates; UTF-8 responses do not.
            return re.sub(r'[\ud800-\udfff]', '\ufffd', value)

        def remap(value, depth=0):
            if depth > 32:
                raise ValueError("Timeline nesting exceeds load budget")
            if isinstance(value, float) and not math.isfinite(value):
                invalid_constants.append('non-finite number')
                return None
            if isinstance(value, str):
                value = clean_text(value)
                if value.startswith(f"/media/{job}/"):
                    return prefix + quote(value[len(f"/media/{job}/"):], safe='/')
                return value
            if isinstance(value, dict):
                normalized = {}
                for key, item in value.items():
                    key = clean_text(key)
                    if key in normalized:
                        raise ValueError("Timeline keys collide after Unicode normalization")
                    normalized[key] = remap(item, depth + 1)
                return normalized
            if isinstance(value, list):
                return [remap(v, depth + 1) for v in value]
            return value
        timeline = {**remap(data), "id": job, "version": version}
        bvid = timeline.get('bvid')
        timeline['bvid'] = bvid if isinstance(bvid, str) and re.fullmatch(r'BV[0-9A-Za-z]{10}', bvid) else None
        cover = timeline.get('cover')
        cover_name = unquote(cover[len(prefix):]) if isinstance(cover, str) and cover.startswith(prefix) else None
        cover_entry = self.manifest(job, version).get(cover_name)
        if (cover_entry is None or Path(cover_name).suffix.lower() not in {'.jpg', '.jpeg', '.png', '.webp', '.gif'}
                or not (self.objects / cover_entry['sha256']).is_file()):
            timeline['cover'] = None
        if invalid_constants:
            timeline['status'] = 'incomplete'
        title = timeline.get('title')
        timeline['title'] = title[:500] if isinstance(title, str) else job
        usage = timeline.get('usage')
        timeline['usage'] = usage if isinstance(usage, dict) and len(json.dumps(usage, ensure_ascii=True)) <= 32768 else None
        for key in ('warnings', 'warning_keys'):
            values = timeline.get(key)
            timeline[key] = [value[:500] for value in values[:32] if isinstance(value, str)] if isinstance(values, list) else []
        if timeline.get('status') == 'ready':
            events = timeline.get('events', [])
            if not isinstance(events, list) or any(not isinstance(cue, dict) for cue in events):
                timeline['status'] = 'incomplete'
                return timeline
            def valid_time(value):
                if type(value) not in (int, float):
                    return False
                try:
                    return math.isfinite(value) and value >= 0
                except OverflowError:
                    return False
            if any(not valid_time(cue.get('at')) or
                   (cue.get('audio') is not None and cue.get('audio') != '' and (not isinstance(cue['audio'], str) or
                                         not valid_time(cue.get('duration')) or cue['duration'] == 0))
                   for cue in events):
                timeline['status'] = 'incomplete'
                return timeline
            audio_until = -1
            def exceeds(end, boundary):
                return end > boundary and not math.isclose(end, boundary, rel_tol=0, abs_tol=1e-9)
            video_duration = timeline.get('duration')
            if video_duration is not None and (not valid_time(video_duration) or video_duration == 0):
                timeline['status'] = 'incomplete'
                return timeline
            for cue in sorted(events, key=lambda cue: cue['at']):
                if exceeds(audio_until, cue['at']) or (video_duration is not None and cue['at'] >= video_duration):
                    timeline['status'] = 'incomplete'
                    return timeline
                if cue.get('audio'):
                    audio_until = cue['at'] + cue['duration']
                    if not valid_time(audio_until) or (video_duration is not None and exceeds(audio_until, video_duration)):
                        timeline['status'] = 'incomplete'
                        return timeline
            manifest = self.manifest(job, version)
            audio_urls = {cue['audio'] for cue in events if cue.get('audio')}
            audio_bytes = 0
            for url in audio_urls:
                name = unquote(url[len(prefix):]) if url.startswith(prefix) else None
                audio_bytes += manifest.get(name, {}).get('bytes', 0)
            if len(audio_urls) > MAX_REACTION_AUDIO_FILES or audio_bytes > MAX_REACTION_AUDIO_BYTES:
                timeline['status'] = 'incomplete'
                return timeline
            references = [(timeline.get('video'), {'.mp4', '.webm'})]
            references.extend((cue.get('audio'), {'.wav', '.mp3', '.ogg', '.m4a'}) for cue in events if cue.get('audio'))
            for url, extensions in references:
                name = unquote(url[len(prefix):]) if isinstance(url, str) and url.startswith(prefix) else None
                entry = manifest.get(name)
                if entry is None or Path(name).suffix.lower() not in extensions or not (self.objects / entry['sha256']).is_file():
                    timeline['status'] = 'incomplete'
                    break
                path = self.objects / entry['sha256']
                stat = path.stat()
                role = 'video' if extensions == {'.mp4', '.webm'} else 'audio'
                if _probe_media(path, stat.st_size, stat.st_mtime_ns, role) is not True:
                    timeline['status'] = 'incomplete'
                    break
        return timeline

    def history(self) -> list[dict]:
        return self.history_page()['analyses']

    def history_page(self, limit=50, offset=0) -> dict:
        limit = max(1, min(100, int(limit)))
        offset = max(0, int(offset))
        with self.connect() as db:
            rows = db.execute("SELECT job, version FROM versions ORDER BY rowid DESC LIMIT ? OFFSET ?",
                              (limit + 1, offset)).fetchall()
        result = []
        for row in rows[:limit]:
            try:
                timeline = self.timeline(row["job"], row["version"])
            except (KeyError, ValueError, OSError):
                timeline = {"status": "incomplete"}
            result.append({"job": row["job"], "version": row["version"],
                           "title": timeline.get("title", row["job"]),
                           "status": timeline.get("status"), "usage": timeline.get("usage"),
                           "progress": None, "last_watched": None, "completed": None})
        return {'analyses': result, 'next_offset': offset + limit if len(rows) > limit else None}

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
            db.execute("BEGIN IMMEDIATE")
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

    def watches(self, limit=50, offset=0) -> list[dict]:
        return self.watch_page(limit, offset)['watches']

    def watch_page(self, limit=50, offset=0) -> dict:
        limit = max(1, min(100, int(limit)))
        offset = max(0, int(offset))
        with self.connect() as db:
            rows = [dict(row) for row in db.execute(
                "SELECT id,job,version,character,progress,last_watched,completed FROM watches "
                "ORDER BY last_watched DESC, id DESC LIMIT ? OFFSET ?", (limit + 1, offset))]
        return {'watches': rows[:limit], 'next_offset': offset + limit if len(rows) > limit else None}

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
                    checked[key] = path.stat().st_size if path.is_file() and digest(path) == key else None
                if checked[key] != entry['bytes']:
                    failures.append(f"{row['job']}/{row['version']}/{name}")
                file_count += 1
                logical_bytes += entry['bytes']
        for entry in assets:
            key = entry['sha256']
            path = self.objects / key
            if key not in checked:
                checked[key] = path.stat().st_size if path.is_file() and digest(path) == key else None
            if checked[key] != entry['bytes']:
                failures.append(entry['name'])
        return {'jobs':len({row['job'] for row in versions}), 'versions':len(versions),
                'files':file_count, 'logical_bytes':logical_bytes, 'audio_assets':len(assets),
                'unique_objects':len(checked), 'failures':failures, 'verified':not failures}
