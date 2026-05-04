from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from utils.config_manager import get_config_manager


INSTALL_TERMINAL_STATUSES = frozenset({"completed", "failed", "canceled"})
INSTALL_KINDS = frozenset({"textractor", "tesseract", "rapidocr", "dxcam"})


def _runtime_root() -> Path:
    return get_config_manager().app_docs_dir / "plugin-runtime" / "galgame_plugin"


def _normalize_kind(kind: str) -> str:
    normalized = str(kind or "textractor").strip().lower()
    if normalized not in INSTALL_KINDS:
        raise ValueError(f"unsupported install task kind: {kind!r}")
    return normalized


def _tasks_dir(kind: str = "textractor") -> Path:
    normalized_kind = _normalize_kind(kind)
    path = _runtime_root() / f"{normalized_kind}-installs"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _normalize_task_id(task_id: str) -> str:
    normalized = str(task_id or "").strip()
    if not normalized:
        raise ValueError("task_id is required")
    if ".." in normalized or "/" in normalized or "\\" in normalized:
        raise ValueError("invalid task_id")
    return normalized


def install_task_state_path(task_id: str, *, kind: str = "textractor") -> Path:
    return _tasks_dir(kind) / f"{_normalize_task_id(task_id)}.json"


def latest_install_task_path(*, kind: str = "textractor") -> Path:
    return _tasks_dir(kind) / "latest.json"


def build_install_task_state(
    *,
    task_id: str,
    kind: str = "textractor",
    run_id: str | None = None,
    plugin_id: str = "galgame_plugin",
    status: str = "queued",
    phase: str = "queued",
    message: str = "",
    progress: float = 0.0,
    downloaded_bytes: int = 0,
    total_bytes: int = 0,
    resume_from: int = 0,
    release_name: str = "",
    asset_name: str = "",
    target_dir: str = "",
    detected_path: str = "",
    error: str = "",
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    now = time.time()
    payload: dict[str, Any] = {
        "task_id": _normalize_task_id(task_id),
        "kind": _normalize_kind(kind),
        "run_id": str(run_id or task_id or ""),
        "plugin_id": plugin_id,
        "status": status,
        "phase": phase,
        "message": message,
        "progress": float(progress),
        "downloaded_bytes": int(downloaded_bytes),
        "total_bytes": int(total_bytes),
        "resume_from": int(resume_from),
        "release_name": release_name,
        "asset_name": asset_name,
        "target_dir": target_dir,
        "detected_path": detected_path,
        "error": error,
        "started_at": now,
        "updated_at": now,
        "completed_at": now if status in INSTALL_TERMINAL_STATUSES else None,
    }
    if extra:
        payload.update(extra)
    return payload


def _atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    tmp_path.write_text(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2),
        encoding="utf-8",
    )
    tmp_path.replace(path)


def write_install_task_state(
    task_id: str,
    payload: dict[str, Any],
    *,
    kind: str = "textractor",
) -> dict[str, Any]:
    normalized = dict(payload)
    normalized["task_id"] = _normalize_task_id(task_id)
    normalized["kind"] = _normalize_kind(str(normalized.get("kind") or kind))
    normalized["run_id"] = str(normalized.get("run_id") or normalized["task_id"])
    normalized["plugin_id"] = str(normalized.get("plugin_id") or "galgame_plugin")
    normalized["status"] = str(normalized.get("status") or "queued")
    normalized["phase"] = str(normalized.get("phase") or normalized["status"])
    normalized["message"] = str(normalized.get("message") or "")
    normalized["progress"] = float(normalized.get("progress") or 0.0)
    normalized["downloaded_bytes"] = int(normalized.get("downloaded_bytes") or 0)
    normalized["total_bytes"] = int(normalized.get("total_bytes") or 0)
    normalized["resume_from"] = int(normalized.get("resume_from") or 0)
    normalized["release_name"] = str(normalized.get("release_name") or "")
    normalized["asset_name"] = str(normalized.get("asset_name") or "")
    normalized["target_dir"] = str(normalized.get("target_dir") or "")
    normalized["detected_path"] = str(normalized.get("detected_path") or "")
    normalized["error"] = str(normalized.get("error") or "")
    started_at = normalized.get("started_at")
    normalized["started_at"] = float(started_at) if isinstance(started_at, (int, float)) else time.time()
    normalized["updated_at"] = time.time()
    if normalized["status"] in INSTALL_TERMINAL_STATUSES:
        completed_at = normalized.get("completed_at")
        normalized["completed_at"] = (
            float(completed_at)
            if isinstance(completed_at, (int, float))
            else normalized["updated_at"]
        )
    else:
        normalized["completed_at"] = None
    _atomic_write_json(
        install_task_state_path(task_id, kind=normalized["kind"]),
        normalized,
    )
    _atomic_write_json(
        latest_install_task_path(kind=normalized["kind"]),
        {
            "task_id": normalized["task_id"],
            "kind": normalized["kind"],
            "run_id": normalized["run_id"],
            "plugin_id": normalized["plugin_id"],
            "updated_at": normalized["updated_at"],
        },
    )
    return normalized


def load_install_task_state(task_id: str, *, kind: str = "textractor") -> dict[str, Any] | None:
    path = install_task_state_path(task_id, kind=kind)
    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return None
    return payload if isinstance(payload, dict) else None


def update_install_task_state(
    task_id: str,
    *,
    kind: str = "textractor",
    **changes: Any,
) -> dict[str, Any]:
    normalized_kind = _normalize_kind(kind)
    current = load_install_task_state(task_id, kind=normalized_kind) or build_install_task_state(
        task_id=task_id,
        kind=normalized_kind,
    )
    current.update(changes)
    current["kind"] = str(current.get("kind") or normalized_kind)
    return write_install_task_state(task_id, current, kind=normalized_kind)


def load_latest_install_task_ref(*, kind: str = "textractor") -> dict[str, Any] | None:
    path = latest_install_task_path(kind=kind)
    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return None
    return payload if isinstance(payload, dict) else None


def load_latest_install_task_state(*, kind: str = "textractor") -> dict[str, Any] | None:
    latest = load_latest_install_task_ref(kind=kind)
    if not isinstance(latest, dict):
        return None
    task_id = str(latest.get("task_id") or "").strip()
    if not task_id:
        return None
    return load_install_task_state(task_id, kind=str(latest.get("kind") or kind))
