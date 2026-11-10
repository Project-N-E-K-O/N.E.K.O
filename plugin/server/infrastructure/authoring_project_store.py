"""Persistent identities of authoring workbench projects.

Only the project identity and its source directory are stored; every other
attribute is read from the project itself when listed. Mutating callers must
hold the cross-process plugin operation lock; the thread lock fences the
read-modify-write cycle inside one process.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from plugin import settings
from plugin.server.domain.plugin_project_errors import ProjectErrorCode as Code
from plugin.server.domain.plugin_project_errors import project_error
from utils.file_utils import atomic_write_json, read_json_tolerating_replace

STORE_VERSION = 1
_RECORD_FIELDS = frozenset({"project_id", "source_dir", "added_at"})
_lock = threading.RLock()


@dataclass(frozen=True)
class ProjectRecord:
    project_id: str
    source_dir: Path
    added_at: str


def store_path() -> Path:
    return settings.get_plugin_state_root().parent / "plugin-authoring.json"


def _parse(data: object) -> list[ProjectRecord]:
    if not isinstance(data, dict) or data.keys() != {"version", "projects"}:
        raise ValueError("unexpected top-level structure")
    if data["version"] != STORE_VERSION:
        raise ValueError(f"unsupported version {data['version']!r}")
    if not isinstance(data["projects"], list):
        raise ValueError("projects must be a list")
    records: list[ProjectRecord] = []
    ids: set[str] = set()
    dirs: set[Path] = set()
    for item in data["projects"]:
        if not isinstance(item, dict) or item.keys() != _RECORD_FIELDS:
            raise ValueError("invalid project record")
        if not all(isinstance(item[key], str) and item[key] for key in _RECORD_FIELDS):
            raise ValueError("project fields must be non-empty strings")
        source_dir = Path(item["source_dir"])
        if not source_dir.is_absolute():
            raise ValueError("source_dir must be absolute")
        if item["project_id"] in ids or source_dir in dirs:
            raise ValueError("duplicate project record")
        ids.add(item["project_id"])
        dirs.add(source_dir)
        records.append(ProjectRecord(item["project_id"], source_dir, item["added_at"]))
    return records


def _read() -> list[ProjectRecord]:
    path = store_path()
    try:
        data = read_json_tolerating_replace(path)
    except FileNotFoundError:
        return []
    except OSError as exc:
        # Still locked or inaccessible after the busy-file retries; the content
        # may be intact, so this must not be reported as damage.
        raise project_error(
            Code.PROJECT_STORE_UNREADABLE, f"Cannot read {path.name}: {exc}", 503
        ) from exc
    except ValueError as exc:
        raise project_error(
            Code.PROJECT_STORE_INVALID, f"{path.name} is not valid JSON: {exc}", 500
        ) from exc
    try:
        return _parse(data)
    except ValueError as exc:
        # Never fall back to an empty list: the next write would erase the file.
        raise project_error(
            Code.PROJECT_STORE_INVALID, f"{path.name} is invalid: {exc}", 500
        ) from exc


def _write(records: list[ProjectRecord]) -> None:
    path = store_path()
    data = {
        "version": STORE_VERSION,
        "projects": [
            {
                "project_id": record.project_id,
                "source_dir": str(record.source_dir),
                "added_at": record.added_at,
            }
            for record in records
        ],
    }
    try:
        atomic_write_json(path, data)
    except OSError as exc:
        raise project_error(
            Code.PROJECT_STORE_WRITE_FAILED, f"Cannot write {path.name}: {exc}", 500
        ) from exc


def list_records() -> list[ProjectRecord]:
    with _lock:
        return _read()


def add_record(source_dir: Path) -> ProjectRecord:
    """Register an absolute, already validated directory."""
    with _lock:
        records = _read()
        if any(record.source_dir == source_dir for record in records):
            raise project_error(
                Code.PROJECT_ALREADY_REGISTERED,
                f"Project directory is already in the workbench: {source_dir}",
                409,
            )
        record = ProjectRecord(
            project_id=uuid4().hex,
            source_dir=source_dir,
            added_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        )
        _write([*records, record])
        return record


def remove_record(project_id: str) -> ProjectRecord:
    with _lock:
        records = _read()
        record = next((item for item in records if item.project_id == project_id), None)
        if record is None:
            raise project_error(
                Code.PROJECT_NOT_FOUND, f"Project is not in the workbench: {project_id}", 404
            )
        _write([item for item in records if item is not record])
        return record
