# -*- coding: utf-8 -*-
"""
Storage-location bootstrap API for the main web app.

Stage 3 keeps the same homepage bootstrap entry, adds the shutdown/restart
checkpoint flow, and exposes maintenance-state diagnostics for the web UI.
"""

from __future__ import annotations

import asyncio
import logging
import os
import shutil
import sys
import inspect
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Request, Response
from pydantic import BaseModel, Field, field_validator

from config import APP_NAME
from main_routers.shared_state import (
    get_config_manager,
    get_request_app_shutdown,
    get_release_storage_startup_barrier,
)
from utils.cloudsave_runtime import ROOT_MODE_MAINTENANCE_READONLY, ROOT_MODE_NORMAL, set_root_mode
from utils.storage_location_bootstrap import (
    STORAGE_STARTUP_BLOCKING_REASONS,
    STORAGE_STATUS_POLL_INTERVAL_MS,
    build_storage_location_bootstrap_payload,
)
from utils.storage_migration import (
    MIGRATED_RUNTIME_ENTRY_NAMES,
    STORAGE_MIGRATION_STATUS_COMPLETED,
    STORAGE_MIGRATION_STATUS_FAILED,
    create_pending_storage_migration,
    delete_storage_migration,
    is_retained_root_cleanup_available,
    load_storage_migration,
    save_storage_migration,
)
from utils.storage_policy import (
    StorageSelectionValidationError,
    compute_anchor_root,
    get_storage_policy_path,
    is_runtime_root_available,
    load_storage_policy,
    normalize_runtime_root,
    paths_equal,
    save_storage_policy,
    validate_selected_root,
)
from utils.config_manager import get_config_manager as get_runtime_config_manager

router = APIRouter(prefix="/api/storage/location", tags=["storage_location"])
logger = logging.getLogger(__name__)
_storage_mutation_lock = asyncio.Lock()


class StorageLocationSelectionRequest(BaseModel):
    selected_root: str = Field(..., min_length=1, max_length=4096)
    selection_source: str = Field(default="user_selected", min_length=1, max_length=64)
    confirm_existing_target_content: bool = False

    @field_validator("selected_root", "selection_source")
    @classmethod
    def _strip_whitespace(cls, value: str) -> str:
        stripped = str(value or "").strip()
        if not stripped:
            raise ValueError("value cannot be empty")
        return stripped


class StorageLocationCleanupRequest(BaseModel):
    retained_root: str = Field(default="", min_length=0, max_length=4096)


class StorageLocationDirectoryPickerRequest(BaseModel):
    start_path: str = Field(default="", min_length=0, max_length=4096)


class _DirectoryPickerCancelled(Exception):
    pass


class _DirectoryPickerUnavailable(RuntimeError):
    def __init__(self, error_code: str, message: str):
        super().__init__(message)
        self.error_code = str(error_code or "directory_picker_unavailable").strip() or "directory_picker_unavailable"
        self.message = str(message or "当前环境暂不支持系统目录选择，请手动输入路径。").strip() or "当前环境暂不支持系统目录选择，请手动输入路径。"


class _OpenStorageRootUnavailable(RuntimeError):
    def __init__(self, error_code: str, message: str):
        super().__init__(message)
        self.error_code = str(error_code or "open_storage_root_unavailable").strip() or "open_storage_root_unavailable"
        self.message = str(message or "当前环境暂不支持直接打开目录。").strip() or "当前环境暂不支持直接打开目录。"


def _set_no_cache_headers(response: Response) -> None:
    response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    response.headers["Pragma"] = "no-cache"
    response.headers["Expires"] = "0"


def _normalize_optional_path(value: Any) -> str:
    raw_value = str(value or "").strip()
    if not raw_value:
        return ""
    return str(normalize_runtime_root(raw_value))


def _path_is_within(candidate: Path | str | None, root: Path | str | None) -> bool:
    if not candidate or not root:
        return False
    candidate_path = normalize_runtime_root(candidate)
    root_path = normalize_runtime_root(root)
    try:
        candidate_path.relative_to(root_path)
        return True
    except ValueError:
        return False


def _dedupe_paths(paths: list[Path | str]) -> list[str]:
    normalized_paths: list[str] = []
    seen: set[str] = set()
    for candidate in paths:
        normalized = _normalize_optional_path(candidate)
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        normalized_paths.append(normalized)
    return normalized_paths


def _get_storage_config_manager():
    try:
        return get_config_manager()
    except RuntimeError:
        # During limited startup, the storage bootstrap endpoints must stay usable
        # even if main_server shared_state has not been fully published yet.
        return get_runtime_config_manager(APP_NAME, migrate=False)


def _snapshot_storage_mutation_state(config_manager, *, anchor_root: Path) -> dict[str, Any]:
    return {
        "root_state": config_manager.load_root_state(),
        "policy": load_storage_policy(config_manager, anchor_root=anchor_root),
        "migration": load_storage_migration(config_manager, anchor_root=anchor_root),
    }


def _restore_storage_mutation_state(
    config_manager,
    snapshot: dict[str, Any],
    *,
    anchor_root: Path,
) -> None:
    previous_migration = snapshot.get("migration")
    if isinstance(previous_migration, dict):
        save_storage_migration(config_manager, previous_migration, anchor_root=anchor_root)
    else:
        delete_storage_migration(config_manager, anchor_root=anchor_root)

    policy_path = get_storage_policy_path(config_manager, anchor_root=anchor_root)
    previous_policy = snapshot.get("policy")
    if isinstance(previous_policy, dict):
        from utils.file_utils import atomic_write_json

        atomic_write_json(policy_path, previous_policy, ensure_ascii=False, indent=2)
    else:
        try:
            os.unlink(policy_path)
        except FileNotFoundError:
            pass

    previous_root_state = snapshot.get("root_state")
    if isinstance(previous_root_state, dict):
        config_manager.save_root_state(previous_root_state)


async def _release_storage_startup_barrier_or_rollback(
    config_manager,
    *,
    snapshot: dict[str, Any],
    anchor_root: Path,
    reason: str,
) -> None:
    try:
        await _release_storage_startup_barrier_if_needed(reason=reason)
    except Exception:
        try:
            _restore_storage_mutation_state(config_manager, snapshot, anchor_root=anchor_root)
        except Exception:
            logger.exception(
                "failed to rollback storage mutation state after startup barrier release failed",
            )
        raise


def _safe_path_size(path: Path) -> int:
    try:
        if path.is_symlink():
            return 0
        if path.is_file():
            return int(path.stat().st_size)
        if not path.is_dir():
            return 0
    except OSError:
        return 0

    total = 0
    stack = [path]
    while stack:
        current = stack.pop()
        try:
            children = list(current.iterdir())
        except OSError:
            continue
        for child in children:
            try:
                if child.is_symlink():
                    continue
                if child.is_dir():
                    stack.append(child)
                    continue
                if child.is_file():
                    total += int(child.stat().st_size)
            except OSError:
                continue
    return total


def _estimate_runtime_payload_bytes(source_root: Path) -> int:
    total = 0
    for name in MIGRATED_RUNTIME_ENTRY_NAMES:
        total += _safe_path_size(source_root / name)
    return total


def _target_root_has_user_content(target_root: Path, config_manager) -> bool:
    try:
        from utils.cloudsave_runtime import runtime_root_has_user_content

        return bool(runtime_root_has_user_content(target_root, config_manager=config_manager))
    except Exception:
        if not target_root.exists() or not target_root.is_dir():
            return False
        try:
            return any(target_root.iterdir())
        except OSError:
            return False


def _find_existing_ancestor(path: Path) -> Path:
    candidate = path.expanduser()
    while True:
        if candidate.exists():
            return candidate
        parent = candidate.parent
        if parent == candidate:
            return candidate
        candidate = parent


def _path_chain_has_symlink(path: Path) -> bool:
    candidate = path.expanduser()
    while True:
        if candidate.exists():
            try:
                return candidate.is_symlink()
            except OSError:
                return False
        parent = candidate.parent
        if parent == candidate:
            return False
        candidate = parent


def _path_segments(path: Path) -> list[str]:
    return [
        segment.strip().lower()
        for segment in str(path).replace("\\", "/").split("/")
        if segment.strip()
    ]


def _is_cloud_sync_path_segment(segment: str) -> bool:
    normalized_segment = str(segment or "").strip().lower()

    def matches_client_folder(prefix: str) -> bool:
        if normalized_segment == prefix:
            return True
        if not normalized_segment.startswith(prefix):
            return False
        suffix = normalized_segment[len(prefix) :].lstrip()
        return bool(suffix) and suffix[0] in {"(", "-", "["}

    if any(
        matches_client_folder(prefix)
        for prefix in ("icloud drive", "google drive", "googledrive", "dropbox")
    ):
        return True
    return (
        normalized_segment == "onedrive"
        or normalized_segment.startswith("onedrive - ")
        or normalized_segment.startswith("onedrive (")
    )


def _collect_warning_codes(current_root: Path, target_root: Path) -> list[str]:
    warning_codes: list[str] = []
    raw_target = str(target_root)
    normalized_target = raw_target.replace("\\", "/").lower()

    if any(_is_cloud_sync_path_segment(segment) for segment in _path_segments(target_root)):
        warning_codes.append("sync_folder")
    if raw_target.startswith("\\\\") or normalized_target.startswith("//"):
        warning_codes.append("network_share")
    if _path_chain_has_symlink(target_root):
        warning_codes.append("symlink_path")

    if sys.platform == "win32":
        current_drive = str(current_root.drive or "").lower()
        target_drive = str(target_root.drive or "").lower()
        if current_drive and target_drive and current_drive != target_drive:
            warning_codes.append("external_volume")
    elif normalized_target.startswith("/volumes/") or normalized_target.startswith("/media/") or normalized_target.startswith("/mnt/"):
        warning_codes.append("external_volume")

    return sorted(set(warning_codes))


def _build_restart_preflight(
    current_root: Path,
    target_root: Path,
    *,
    config_manager=None,
    estimated_required_bytes: int | None = None,
    allow_existing_target_content: bool = False,
) -> dict[str, Any]:
    target_root = normalize_runtime_root(target_root)
    if estimated_required_bytes is None:
        estimated_required_bytes = _estimate_runtime_payload_bytes(current_root)
    existing_anchor = _find_existing_ancestor(target_root)

    target_free_bytes = 0
    try:
        target_free_bytes = int(shutil.disk_usage(str(existing_anchor)).free)
    except OSError:
        target_free_bytes = 0

    if target_root.exists():
        permission_probe = target_root
    else:
        permission_probe = existing_anchor
    permission_ok = os.access(str(permission_probe), os.W_OK)
    target_has_existing_content = bool(
        config_manager is not None
        and _target_root_has_user_content(target_root, config_manager)
    )
    requires_existing_target_confirmation = bool(
        target_has_existing_content
        and not allow_existing_target_content
    )

    blocking_error_code = ""
    blocking_error_message = ""
    if not permission_ok:
        blocking_error_code = "target_not_writable"
        blocking_error_message = "目标路径当前不可写，无法开始关闭后的迁移流程。"
    elif (
        estimated_required_bytes > 0
        and target_free_bytes > 0
        and target_free_bytes < estimated_required_bytes
    ):
        blocking_error_code = "insufficient_space"
        blocking_error_message = "目标卷剩余空间不足，无法安全执行关闭后的迁移。"

    return {
        "target_root": str(target_root),
        "estimated_required_bytes": estimated_required_bytes,
        "target_free_bytes": target_free_bytes,
        "permission_ok": permission_ok,
        "warning_codes": _collect_warning_codes(current_root, target_root),
        "target_has_existing_content": target_has_existing_content,
        "requires_existing_target_confirmation": requires_existing_target_confirmation,
        "existing_target_confirmation_message": (
            "目标路径已经包含现有数据。确认后迁移会覆盖目标中的同名运行时数据目录，"
            "目标目录中的其他文件会保留。请确认已选择正确目录。"
            if requires_existing_target_confirmation
            else ""
        ),
        "blocking_error_code": blocking_error_code,
        "blocking_error_message": blocking_error_message,
    }


def _load_committed_selected_root(config_manager, *, anchor_root: Path, fallback_root: Path) -> Path:
    policy = load_storage_policy(config_manager, anchor_root=anchor_root)
    if not isinstance(policy, dict):
        return fallback_root

    selected_root_value = str(policy.get("selected_root") or "").strip()
    if not selected_root_value:
        return fallback_root

    try:
        return normalize_runtime_root(selected_root_value)
    except Exception:
        return fallback_root


def _is_selected_root_missing_recovery(config_manager, *, current_root: Path, anchor_root: Path) -> bool:
    if not bool(getattr(config_manager, "recovery_committed_root_unavailable", False)):
        return False
    committed_selected_root = _load_committed_selected_root(
        config_manager,
        anchor_root=anchor_root,
        fallback_root=current_root,
    )
    return not paths_equal(committed_selected_root, current_root)


def _build_maintenance_message(bootstrap_payload: dict[str, Any]) -> str:
    blocking_reason = str(bootstrap_payload.get("blocking_reason") or "").strip()
    last_error_summary = str(bootstrap_payload.get("last_error_summary") or "").strip()

    if blocking_reason == "migration_pending":
        return "正在优化存储布局，当前实例关闭后会继续迁移并自动恢复。"
    if blocking_reason == "recovery_required":
        return last_error_summary or "检测到需要恢复的存储状态，请先重新确认本次使用的存储位置。"
    if blocking_reason == "selection_required":
        return "需要先确认本次运行使用的存储位置，主页主功能会继续保持阻断。"
    return ""


def _normalize_directory_picker_start_path(raw_value: str) -> str:
    candidate_text = str(raw_value or "").strip()
    if not candidate_text:
        return ""

    try:
        candidate = normalize_runtime_root(candidate_text)
    except Exception:
        candidate = Path(candidate_text).expanduser()
        if not candidate.is_absolute():
            return ""

    if candidate.exists() and candidate.is_dir():
        return str(candidate)

    current = candidate.parent
    while current != current.parent:
        if current.exists() and current.is_dir():
            return str(current)
        current = current.parent

    if current.exists() and current.is_dir():
        return str(current)
    return ""


def _resolve_executable_name(*candidates: str) -> str:
    for candidate in candidates:
        if not candidate:
            continue
        if os.path.isabs(candidate) and os.path.exists(candidate):
            return candidate
        resolved = shutil.which(candidate)
        if resolved:
            return resolved
    return candidates[0]


def _pick_directory_via_osascript(*, start_path: str) -> str:
    command = [_resolve_executable_name("/usr/bin/osascript", "osascript")]
    if start_path:
        safe_start_path = start_path.replace("\\", "\\\\").replace('"', '\\"')
        command.extend(
            [
                "-e",
                'tell application "Finder" to activate',
                "-e",
                f'set defaultLocation to POSIX file "{safe_start_path}"',
                "-e",
                'set selectedFolder to choose folder with prompt "请选择存储位置目录" default location defaultLocation',
            ]
        )
    else:
        command.extend(
            [
                "-e",
                'tell application "Finder" to activate',
                "-e",
                'set selectedFolder to choose folder with prompt "请选择存储位置目录"',
            ]
        )
    command.extend(["-e", "POSIX path of selectedFolder"])

    try:
        completed = subprocess.run(
            command,
            capture_output=True,
            text=True,
            check=False,
            timeout=120,
        )
    except FileNotFoundError as exc:
        raise _DirectoryPickerUnavailable(
            "directory_picker_unavailable",
            "当前环境暂不支持系统目录选择，请手动输入路径。",
        ) from exc
    except Exception as exc:
        raise _DirectoryPickerUnavailable(
            "directory_picker_unavailable",
            f"打开系统目录选择器失败: {exc}",
        ) from exc

    if completed.returncode != 0:
        stderr = str(completed.stderr or "").strip()
        if "User canceled" in stderr or "(-128)" in stderr:
            raise _DirectoryPickerCancelled()
        raise _DirectoryPickerUnavailable(
            "directory_picker_failed",
            f"打开系统目录选择器失败: {stderr or completed.returncode}",
        )

    selected_root = str(completed.stdout or "").strip()
    if not selected_root:
        raise _DirectoryPickerCancelled()
    return selected_root


def _pick_directory_via_powershell(*, start_path: str) -> str:
    powershell_executable = _resolve_executable_name(
        os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32", "WindowsPowerShell", "v1.0", "powershell.exe"),
        "powershell.exe",
        "powershell",
        "pwsh.exe",
        "pwsh",
    )
    if not os.path.isabs(powershell_executable) and not shutil.which(powershell_executable):
        raise _DirectoryPickerUnavailable(
            "directory_picker_unavailable",
            "当前系统未找到 PowerShell，无法打开目录选择器。",
        )

    escaped_start_path = start_path.replace("'", "''")
    script = """
Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing
$owner = New-Object System.Windows.Forms.Form
$owner.Text = 'N.E.K.O'
$owner.StartPosition = [System.Windows.Forms.FormStartPosition]::CenterScreen
$owner.Size = New-Object System.Drawing.Size(1, 1)
$owner.FormBorderStyle = [System.Windows.Forms.FormBorderStyle]::FixedToolWindow
$owner.ShowInTaskbar = $false
$owner.Opacity = 0
$owner.TopMost = $true
$dialog = New-Object System.Windows.Forms.FolderBrowserDialog
$dialog.Description = '请选择存储位置目录'
$dialog.ShowNewFolderButton = $true
if ('{start_path}') {{
    $dialog.SelectedPath = '{start_path}'
}}
$owner.Show()
$owner.Activate()
$owner.BringToFront()
[System.Windows.Forms.Application]::DoEvents()
$result = $dialog.ShowDialog($owner)
if ($result -eq [System.Windows.Forms.DialogResult]::OK) {{
    Write-Output $dialog.SelectedPath
    exit 0
}}
exit 2
""".strip().format(start_path=escaped_start_path)

    try:
        completed = subprocess.run(
            [powershell_executable, "-NoProfile", "-STA", "-Command", script],
            capture_output=True,
            text=True,
            check=False,
            timeout=120,
        )
    except FileNotFoundError as exc:
        raise _DirectoryPickerUnavailable(
            "directory_picker_unavailable",
            "当前系统未找到 PowerShell，无法打开目录选择器。",
        ) from exc
    except Exception as exc:
        raise _DirectoryPickerUnavailable(
            "directory_picker_failed",
            f"打开系统目录选择器失败: {exc}",
        ) from exc

    if completed.returncode == 2:
        raise _DirectoryPickerCancelled()
    if completed.returncode != 0:
        stderr = str(completed.stderr or "").strip()
        raise _DirectoryPickerUnavailable(
            "directory_picker_failed",
            f"打开系统目录选择器失败: {stderr or completed.returncode}",
        )

    selected_root = str(completed.stdout or "").strip()
    if not selected_root:
        raise _DirectoryPickerCancelled()
    return selected_root


def _pick_directory_via_linux_dialog(*, start_path: str) -> str:
    commands: list[list[str]] = []
    zenity_executable = _resolve_executable_name("/usr/bin/zenity", "/bin/zenity", "zenity")
    if os.path.isabs(zenity_executable) and os.path.exists(zenity_executable) or shutil.which(zenity_executable):
        command = [zenity_executable, "--file-selection", "--directory", "--title=请选择存储位置目录"]
        if start_path:
            command.append(f"--filename={start_path.rstrip('/')}/")
        commands.append(command)
    kdialog_executable = _resolve_executable_name("/usr/bin/kdialog", "/bin/kdialog", "kdialog")
    if os.path.isabs(kdialog_executable) and os.path.exists(kdialog_executable) or shutil.which(kdialog_executable):
        command = [kdialog_executable, "--getexistingdirectory"]
        if start_path:
            command.append(start_path)
        commands.append(command)
    yad_executable = _resolve_executable_name("/usr/bin/yad", "/bin/yad", "yad")
    if os.path.isabs(yad_executable) and os.path.exists(yad_executable) or shutil.which(yad_executable):
        command = [yad_executable, "--file-selection", "--directory", "--title=请选择存储位置目录"]
        if start_path:
            command.append(f"--filename={start_path.rstrip('/')}/")
        commands.append(command)

    if not commands:
        raise _DirectoryPickerUnavailable(
            "directory_picker_unavailable",
            "当前系统未安装可用的图形目录选择器。",
        )

    last_error = None
    for command in commands:
        try:
            completed = subprocess.run(
                command,
                capture_output=True,
                text=True,
                check=False,
                timeout=120,
            )
        except Exception as exc:
            last_error = exc
            continue

        if completed.returncode == 0:
            selected_root = str(completed.stdout or "").strip()
            if selected_root:
                return selected_root
            raise _DirectoryPickerCancelled()
        if completed.returncode in (1, 252):
            raise _DirectoryPickerCancelled()
        last_error = str(completed.stderr or "").strip() or completed.returncode

    raise _DirectoryPickerUnavailable(
        "directory_picker_failed",
        f"打开系统目录选择器失败: {last_error}",
    )


def _pick_directory_via_tkinter(*, start_path: str) -> str:
    try:
        import tkinter as tk
        from tkinter import filedialog
    except Exception as exc:
        raise _DirectoryPickerUnavailable(
            "directory_picker_unavailable",
            "当前环境暂不支持系统目录选择，请手动输入路径。",
        ) from exc

    root = None
    try:
        root = tk.Tk()
        root.withdraw()
        try:
            root.attributes("-topmost", True)
            root.lift()
            root.focus_force()
        except Exception:
            pass
        root.update_idletasks()
        selected_root = filedialog.askdirectory(
            parent=root,
            initialdir=start_path or None,
            title="请选择存储位置目录",
            mustexist=False,
        )
    except Exception as exc:
        raise _DirectoryPickerUnavailable(
            "directory_picker_failed",
            f"打开系统目录选择器失败: {exc}",
        ) from exc
    finally:
        if root is not None:
            try:
                root.destroy()
            except Exception:
                pass

    if not selected_root:
        raise _DirectoryPickerCancelled()
    return str(selected_root)


def _pick_storage_location_directory(*, start_path: str) -> str:
    normalized_start_path = _normalize_directory_picker_start_path(start_path)
    if sys.platform == "darwin":
        try:
            return _pick_directory_via_osascript(start_path=normalized_start_path)
        except _DirectoryPickerUnavailable:
            return _pick_directory_via_tkinter(start_path=normalized_start_path)
    if sys.platform == "win32":
        try:
            return _pick_directory_via_powershell(start_path=normalized_start_path)
        except _DirectoryPickerUnavailable:
            return _pick_directory_via_tkinter(start_path=normalized_start_path)
    try:
        return _pick_directory_via_linux_dialog(start_path=normalized_start_path)
    except _DirectoryPickerUnavailable:
        return _pick_directory_via_tkinter(start_path=normalized_start_path)


def _open_path_in_file_manager(path: Path | str) -> None:
    target_path = normalize_runtime_root(path)
    if not target_path.exists() or not target_path.is_dir():
        raise _OpenStorageRootUnavailable(
            "storage_root_unavailable",
            "当前数据目录不存在或不可访问。",
        )

    try:
        if sys.platform == "win32":
            os.startfile(str(target_path))  # type: ignore[attr-defined]
            return
        if sys.platform == "darwin":
            subprocess.Popen(["open", str(target_path)])
            return

        opener = shutil.which("xdg-open") or shutil.which("gio")
        if not opener:
            raise _OpenStorageRootUnavailable(
                "open_storage_root_unavailable",
                "当前系统未找到可用的文件管理器打开命令。",
            )
        if os.path.basename(opener) == "gio":
            subprocess.Popen([opener, "open", str(target_path)])
        else:
            subprocess.Popen([opener, str(target_path)])
    except _OpenStorageRootUnavailable:
        raise
    except Exception as exc:
        raise _OpenStorageRootUnavailable(
            "open_storage_root_failed",
            f"打开当前数据目录失败: {exc}",
        ) from exc


def _build_status_payload(config_manager) -> dict[str, Any]:
    bootstrap_payload = build_storage_location_bootstrap_payload(config_manager)
    blocking_reason = str(bootstrap_payload.get("blocking_reason") or "").strip()
    migration_payload = bootstrap_payload.get("migration") if isinstance(bootstrap_payload.get("migration"), dict) else {}
    completion_notice = _build_completed_migration_notice(config_manager, bootstrap_payload=bootstrap_payload)

    lifecycle_state = "ready"
    if blocking_reason == "migration_pending":
        lifecycle_state = "maintenance"
    elif blocking_reason == "recovery_required":
        lifecycle_state = "recovery_required"
    elif blocking_reason == "selection_required":
        lifecycle_state = "selection_required"

    return {
        "ok": True,
        "ready": lifecycle_state == "ready",
        "status": lifecycle_state,
        "lifecycle_state": lifecycle_state,
        "migration_stage": str(migration_payload.get("status") or "").strip(),
        "maintenance_message": _build_maintenance_message(bootstrap_payload),
        "poll_interval_ms": int(bootstrap_payload.get("poll_interval_ms") or STORAGE_STATUS_POLL_INTERVAL_MS),
        "effective_root": str(normalize_runtime_root(config_manager.app_docs_dir)),
        "last_error_summary": str(bootstrap_payload.get("last_error_summary") or "").strip(),
        "blocking_reason": blocking_reason,
        "completion_notice": completion_notice,
        "storage": {
            "selection_required": bool(bootstrap_payload.get("selection_required")),
            "migration_pending": bool(bootstrap_payload.get("migration_pending")),
            "recovery_required": bool(bootstrap_payload.get("recovery_required")),
            "legacy_cleanup_pending": bool(bootstrap_payload.get("legacy_cleanup_pending")),
            "stage": bootstrap_payload.get("stage") or "",
        },
        "migration": migration_payload,
    }


def _build_runtime_entry_diagnostic(
    *,
    name: str,
    write_root: Path | str,
    read_roots: list[Path | str],
    effective_root: Path | str,
    retained_source_root: Path | str | None,
    notes: list[str] | None = None,
) -> dict[str, Any]:
    normalized_write_root = _normalize_optional_path(write_root)
    normalized_read_roots = _dedupe_paths(read_roots)
    reads_outside_effective_root = [
        path for path in normalized_read_roots if not _path_is_within(path, effective_root)
    ]
    reads_from_retained_source_root = [
        path for path in normalized_read_roots if _path_is_within(path, retained_source_root)
    ]
    return {
        "name": name,
        "write_root": normalized_write_root,
        "read_roots": normalized_read_roots,
        "write_within_effective_root": _path_is_within(normalized_write_root, effective_root),
        "reads_outside_effective_root": reads_outside_effective_root,
        "reads_from_retained_source_root": reads_from_retained_source_root,
        "all_reads_within_effective_root": not reads_outside_effective_root,
        "notes": list(notes or []),
    }


def _build_storage_location_diagnostics_payload(config_manager) -> dict[str, Any]:
    bootstrap_payload = build_storage_location_bootstrap_payload(config_manager)
    migration_payload = (
        bootstrap_payload.get("migration")
        if isinstance(bootstrap_payload.get("migration"), dict)
        else {}
    )
    effective_root = normalize_runtime_root(config_manager.app_docs_dir)
    anchor_root = normalize_runtime_root(config_manager.anchor_root)
    committed_selected_root = normalize_runtime_root(
        getattr(config_manager, "committed_selected_root", config_manager.app_docs_dir)
    )
    retained_source_root = _normalize_optional_path(
        migration_payload.get("retained_source_root")
        or migration_payload.get("backup_root")
        or ""
    )

    live2d_lookup = getattr(config_manager, "get_live2d_lookup_roots", None)
    if callable(live2d_lookup):
        live2d_read_roots = list(live2d_lookup())
    else:
        live2d_read_roots = [getattr(config_manager, "live2d_dir", effective_root / "live2d")]

    runtime_entries = {
        "config": _build_runtime_entry_diagnostic(
            name="config",
            write_root=config_manager.config_dir,
            read_roots=[config_manager.config_dir],
            effective_root=effective_root,
            retained_source_root=retained_source_root,
        ),
        "memory": _build_runtime_entry_diagnostic(
            name="memory",
            write_root=config_manager.memory_dir,
            read_roots=[config_manager.memory_dir],
            effective_root=effective_root,
            retained_source_root=retained_source_root,
        ),
        "plugins": _build_runtime_entry_diagnostic(
            name="plugins",
            write_root=config_manager.plugins_dir,
            read_roots=[config_manager.plugins_dir],
            effective_root=effective_root,
            retained_source_root=retained_source_root,
        ),
        "live2d": _build_runtime_entry_diagnostic(
            name="live2d",
            write_root=config_manager.live2d_dir,
            read_roots=live2d_read_roots,
            effective_root=effective_root,
            retained_source_root=retained_source_root,
            notes=(
                ["windows_cfa_fallback_read_enabled"]
                if bool(getattr(config_manager, "is_windows_cfa_fallback_active", False))
                else []
            ),
        ),
        "vrm": _build_runtime_entry_diagnostic(
            name="vrm",
            write_root=config_manager.vrm_dir,
            read_roots=[config_manager.vrm_dir],
            effective_root=effective_root,
            retained_source_root=retained_source_root,
        ),
        "mmd": _build_runtime_entry_diagnostic(
            name="mmd",
            write_root=config_manager.mmd_dir,
            read_roots=[config_manager.mmd_dir],
            effective_root=effective_root,
            retained_source_root=retained_source_root,
        ),
        "workshop": _build_runtime_entry_diagnostic(
            name="workshop",
            write_root=config_manager.workshop_dir,
            read_roots=[config_manager.workshop_dir],
            effective_root=effective_root,
            retained_source_root=retained_source_root,
        ),
        "character_cards": _build_runtime_entry_diagnostic(
            name="character_cards",
            write_root=config_manager.chara_dir,
            read_roots=[config_manager.chara_dir],
            effective_root=effective_root,
            retained_source_root=retained_source_root,
        ),
        "jukebox": _build_runtime_entry_diagnostic(
            name="jukebox",
            write_root=Path(config_manager.app_docs_dir) / "jukebox",
            read_roots=[Path(config_manager.app_docs_dir) / "jukebox"],
            effective_root=effective_root,
            retained_source_root=retained_source_root,
        ),
    }

    entries_with_reads_outside_effective_root = [
        name
        for name, payload in runtime_entries.items()
        if payload["reads_outside_effective_root"]
    ]
    entries_reading_retained_source_root = [
        name
        for name, payload in runtime_entries.items()
        if payload["reads_from_retained_source_root"]
    ]

    return {
        "ok": True,
        "layout": {
            "effective_root": str(effective_root),
            "committed_selected_root": str(committed_selected_root),
            "reported_current_root": _normalize_optional_path(
                getattr(config_manager, "reported_current_root", config_manager.app_docs_dir)
            ),
            "anchor_root": str(anchor_root),
            "retained_source_root": retained_source_root,
            "cloudsave_root": str(config_manager.cloudsave_dir),
            "state_root": str(config_manager.local_state_dir),
            "recovery_committed_root_unavailable": bool(
                getattr(config_manager, "recovery_committed_root_unavailable", False)
            ),
            "windows_cfa_fallback_active": bool(
                getattr(config_manager, "is_windows_cfa_fallback_active", False)
            ),
        },
        "runtime_entries": runtime_entries,
        "anchored_entries": {
            "cloudsave": {
                "root": _normalize_optional_path(config_manager.cloudsave_dir),
                "anchored_to": "anchor_root",
            },
            "state": {
                "root": _normalize_optional_path(config_manager.local_state_dir),
                "anchored_to": "anchor_root",
            },
        },
        "summary": {
            "runtime_entries_checked": len(runtime_entries),
            "entries_with_reads_outside_effective_root": entries_with_reads_outside_effective_root,
            "entries_reading_retained_source_root": entries_reading_retained_source_root,
            "all_runtime_entries_read_from_effective_root_only": not entries_with_reads_outside_effective_root,
        },
        "storage": {
            "selection_required": bool(bootstrap_payload.get("selection_required")),
            "migration_pending": bool(bootstrap_payload.get("migration_pending")),
            "recovery_required": bool(bootstrap_payload.get("recovery_required")),
            "blocking_reason": str(bootstrap_payload.get("blocking_reason") or "").strip(),
            "last_error_summary": str(bootstrap_payload.get("last_error_summary") or "").strip(),
        },
    }


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _build_completed_migration_notice(
    config_manager,
    *,
    bootstrap_payload: dict[str, Any] | None = None,
    require_existing_retained_root: bool = False,
) -> dict[str, Any]:
    bootstrap = (
        bootstrap_payload
        if isinstance(bootstrap_payload, dict)
        else build_storage_location_bootstrap_payload(config_manager)
    )
    migration_payload = bootstrap.get("migration") if isinstance(bootstrap.get("migration"), dict) else {}
    if str(migration_payload.get("status") or "").strip() != STORAGE_MIGRATION_STATUS_COMPLETED:
        return {
            "completed": False,
        }
    if str(migration_payload.get("retained_source_mode") or "").strip() == "cleaned":
        return {
            "completed": False,
        }

    current_root = normalize_runtime_root(config_manager.app_docs_dir)
    anchor_root = compute_anchor_root(config_manager, current_root=current_root)
    target_root = str(migration_payload.get("target_root") or "").strip()
    source_root = str(migration_payload.get("source_root") or "").strip()
    retained_root = str(
        migration_payload.get("retained_source_root")
        or migration_payload.get("backup_root")
        or source_root
        or ""
    ).strip()
    retained_exists = bool(retained_root and Path(retained_root).exists())
    cleanup_available = is_retained_root_cleanup_available(
        retained_root,
        current_root=current_root,
        anchor_root=anchor_root,
        target_root=target_root,
        require_exists=True,
        allow_anchor_root=True,
    )
    if require_existing_retained_root and not cleanup_available:
        return {
            "completed": False,
        }

    return {
        "completed": True,
        "selection_source": str(migration_payload.get("selection_source") or "").strip(),
        "source_root": source_root,
        "target_root": target_root,
        "retained_root": retained_root,
        "retained_root_exists": retained_exists,
        "cleanup_available": cleanup_available,
        "completed_at": str(migration_payload.get("completed_at") or "").strip(),
        "message": "存储位置迁移已完成，旧数据目录当前仍保留，需手动清理。",
    }


def _cleanup_retained_runtime_root(
    retained_path: Path,
    *,
    current_root: Path,
    anchor_root: Path,
    target_root: Path | str | None = None,
) -> None:
    if not is_retained_root_cleanup_available(
        retained_path,
        current_root=current_root,
        anchor_root=anchor_root,
        target_root=target_root,
        require_exists=True,
        allow_anchor_root=True,
    ):
        raise ValueError("保留目录当前不满足安全清理条件。")

    if paths_equal(retained_path, anchor_root):
        for entry_name in MIGRATED_RUNTIME_ENTRY_NAMES:
            entry_path = retained_path / entry_name
            if entry_path.is_dir() and not entry_path.is_symlink():
                shutil.rmtree(entry_path)
            elif entry_path.exists():
                entry_path.unlink()
        return

    if retained_path.is_dir() and not retained_path.is_symlink():
        shutil.rmtree(retained_path)
    elif retained_path.exists():
        retained_path.unlink()


async def _release_storage_startup_barrier_if_needed(*, reason: str) -> None:
    callback = get_release_storage_startup_barrier()
    if not callable(callback):
        return

    result = callback(reason=reason)
    if inspect.isawaitable(result):
        await result


async def _request_app_shutdown(request_app_shutdown) -> None:
    result = request_app_shutdown()
    if inspect.isawaitable(result):
        await result


@router.get("/bootstrap")
async def get_storage_location_bootstrap(response: Response):
    _set_no_cache_headers(response)

    config_manager = _get_storage_config_manager()
    return build_storage_location_bootstrap_payload(config_manager)


@router.get("/status")
async def get_storage_location_status(response: Response):
    _set_no_cache_headers(response)

    config_manager = _get_storage_config_manager()
    return _build_status_payload(config_manager)


@router.post("/exit")
async def post_storage_location_exit(request: Request, response: Response):
    _set_no_cache_headers(response)

    if request.headers.get("X-Neko-Storage-Action") != "exit":
        response.status_code = 403
        return {
            "ok": False,
            "error_code": "storage_exit_forbidden",
            "error": "缺少存储退出确认标记。",
        }

    config_manager = _get_storage_config_manager()
    bootstrap_payload = build_storage_location_bootstrap_payload(config_manager)
    blocking_reason = str(bootstrap_payload.get("blocking_reason") or "").strip()
    root_mode = str((config_manager.load_root_state() or {}).get("mode") or "").strip()
    if (
        blocking_reason not in STORAGE_STARTUP_BLOCKING_REASONS
        and root_mode != ROOT_MODE_MAINTENANCE_READONLY
    ):
        response.status_code = 409
        return {
            "ok": False,
            "error_code": "storage_exit_not_required",
            "error": "当前没有需要阻断启动的存储状态。",
            "blocking_reason": blocking_reason,
        }

    request_app_shutdown = get_request_app_shutdown()
    if not callable(request_app_shutdown):
        response.status_code = 503
        return {
            "ok": False,
            "error_code": "restart_unavailable",
            "error": "当前实例暂时无法执行受控关闭，请稍后重试。",
        }

    try:
        await _request_app_shutdown(request_app_shutdown)
    except Exception as exc:
        response.status_code = 500
        return {
            "ok": False,
            "error_code": "restart_schedule_failed",
            "error": f"受控关闭启动失败: {exc}",
        }

    return {
        "ok": True,
        "result": "shutdown_initiated",
    }


@router.get("/diagnostics")
async def get_storage_location_diagnostics(response: Response):
    _set_no_cache_headers(response)

    config_manager = _get_storage_config_manager()
    return _build_storage_location_diagnostics_payload(config_manager)


@router.get("/retained-source")
async def get_storage_location_retained_source(response: Response):
    _set_no_cache_headers(response)

    config_manager = _get_storage_config_manager()
    notice = _build_completed_migration_notice(
        config_manager,
        require_existing_retained_root=False,
    )
    return {
        "ok": True,
        **notice,
    }


@router.post("/pick-directory")
async def post_storage_location_pick_directory(
    payload: StorageLocationDirectoryPickerRequest,
    response: Response,
):
    _set_no_cache_headers(response)

    try:
        selected_root = await asyncio.to_thread(
            _pick_storage_location_directory,
            start_path=payload.start_path,
        )
    except _DirectoryPickerCancelled:
        return {
            "ok": True,
            "cancelled": True,
            "selected_root": "",
        }
    except _DirectoryPickerUnavailable as exc:
        response.status_code = 503
        return {
            "ok": False,
            "error_code": exc.error_code,
            "error": exc.message,
        }

    return {
        "ok": True,
        "cancelled": False,
        "selected_root": str(normalize_runtime_root(selected_root)),
    }


@router.post("/open-current")
async def post_storage_location_open_current(response: Response):
    _set_no_cache_headers(response)

    config_manager = _get_storage_config_manager()
    current_root = normalize_runtime_root(config_manager.app_docs_dir)
    try:
        await asyncio.to_thread(_open_path_in_file_manager, current_root)
    except _OpenStorageRootUnavailable as exc:
        response.status_code = 503
        return {
            "ok": False,
            "error_code": exc.error_code,
            "error": exc.message,
            "current_root": str(current_root),
        }

    return {
        "ok": True,
        "current_root": str(current_root),
    }


@router.post("/retained-source/cleanup")
async def post_storage_location_retained_source_cleanup(
    payload: StorageLocationCleanupRequest,
    response: Response,
):
    async with _storage_mutation_lock:
        return await _post_storage_location_retained_source_cleanup_locked(payload, response)


async def _post_storage_location_retained_source_cleanup_locked(
    payload: StorageLocationCleanupRequest,
    response: Response,
):
    _set_no_cache_headers(response)

    config_manager = _get_storage_config_manager()
    notice = _build_completed_migration_notice(
        config_manager,
        require_existing_retained_root=True,
    )
    if notice.get("completed") is not True:
        response.status_code = 404
        return {
            "ok": False,
            "error_code": "retained_source_not_found",
            "error": "当前没有可清理的旧数据保留目录。",
        }

    expected_retained_root = str(notice.get("retained_root") or "").strip()
    requested_retained_root = str(payload.retained_root or "").strip() or expected_retained_root
    if not paths_equal(requested_retained_root, expected_retained_root):
        response.status_code = 409
        return {
            "ok": False,
            "error_code": "retained_source_mismatch",
            "error": "请求的清理路径与当前保留目录不一致，请刷新后重试。",
        }

    retained_path = Path(expected_retained_root)
    current_root = normalize_runtime_root(config_manager.app_docs_dir)
    anchor_root = compute_anchor_root(config_manager, current_root=current_root)
    try:
        await asyncio.to_thread(
            _cleanup_retained_runtime_root,
            retained_path,
            current_root=current_root,
            anchor_root=anchor_root,
            target_root=notice.get("target_root") or "",
        )
    except Exception as exc:
        response.status_code = 500
        return {
            "ok": False,
            "error_code": "retained_source_cleanup_failed",
            "error": f"清理旧数据保留目录失败: {exc}",
        }

    migration_payload = load_storage_migration(config_manager, anchor_root=anchor_root) or {}
    if isinstance(migration_payload, dict):
        updated_payload = dict(migration_payload)
        updated_payload["backup_root"] = ""
        updated_payload["retained_source_root"] = ""
        updated_payload["retained_source_mode"] = "cleaned"
        updated_payload["updated_at"] = _utc_now_iso()
        updated_payload["cleanup_completed_at"] = _utc_now_iso()
        save_storage_migration(config_manager, updated_payload, anchor_root=anchor_root)

    try:
        root_state = config_manager.load_root_state()
        if isinstance(root_state, dict):
            updated_root_state = dict(root_state)
            updated_root_state["legacy_cleanup_pending"] = False
            if paths_equal(updated_root_state.get("last_migration_backup") or "", expected_retained_root):
                updated_root_state["last_migration_backup"] = ""
            config_manager.save_root_state(updated_root_state)
    except Exception:
        pass

    return {
        "ok": True,
        "cleaned_root": expected_retained_root,
    }


@router.post("/select")
async def post_storage_location_select(
    payload: StorageLocationSelectionRequest,
    response: Response,
):
    async with _storage_mutation_lock:
        return await _post_storage_location_select_locked(payload, response)


async def _post_storage_location_select_locked(
    payload: StorageLocationSelectionRequest,
    response: Response,
):
    _set_no_cache_headers(response)

    config_manager = _get_storage_config_manager()
    current_root = normalize_runtime_root(config_manager.app_docs_dir)
    anchor_root = compute_anchor_root(config_manager, current_root=current_root)

    try:
        normalized_selected_root = validate_selected_root(
            config_manager,
            payload.selected_root,
            current_root=current_root,
            anchor_root=anchor_root,
            selection_source=payload.selection_source,
        )
    except StorageSelectionValidationError as exc:
        response.status_code = 400
        return {
            "ok": False,
            "error_code": exc.error_code,
            "error": exc.message,
        }

    blocking_bootstrap = build_storage_location_bootstrap_payload(config_manager)
    selected_root_missing_recovery = _is_selected_root_missing_recovery(
        config_manager,
        current_root=current_root,
        anchor_root=anchor_root,
    )
    committed_selected_root = _load_committed_selected_root(
        config_manager,
        anchor_root=anchor_root,
        fallback_root=current_root,
    )
    if paths_equal(normalized_selected_root, current_root):
        if bool(blocking_bootstrap.get("migration_pending")):
            response.status_code = 409
            return {
                "ok": False,
                "error_code": "storage_bootstrap_blocking",
                "error": "当前存储状态仍需恢复或迁移，暂时不能继续当前会话。",
            }
        if bool(blocking_bootstrap.get("recovery_required")):
            if not selected_root_missing_recovery:
                migration_payload = load_storage_migration(
                    config_manager,
                    anchor_root=anchor_root,
                ) or {}
                migration_failed_on_current_root = (
                    str(migration_payload.get("status") or "").strip() == STORAGE_MIGRATION_STATUS_FAILED
                    and paths_equal(migration_payload.get("source_root") or "", current_root)
                )
                if not migration_failed_on_current_root:
                    response.status_code = 409
                    return {
                        "ok": False,
                        "error_code": "storage_bootstrap_blocking",
                        "error": "当前存储状态仍需恢复或迁移，暂时不能继续当前会话。",
                    }

                state_snapshot = _snapshot_storage_mutation_state(config_manager, anchor_root=anchor_root)
                delete_storage_migration(config_manager, anchor_root=anchor_root)
                policy_payload = save_storage_policy(
                    config_manager,
                    selected_root=current_root,
                    selection_source=payload.selection_source,
                    anchor_root=anchor_root,
                )
                set_root_mode(
                    config_manager,
                    ROOT_MODE_NORMAL,
                    current_root=str(current_root),
                    last_known_good_root=str(current_root),
                    last_migration_result=f"recovered:failed_migration:{migration_payload.get('error_code') or 'unknown'}",
                )
                try:
                    await _release_storage_startup_barrier_or_rollback(
                        config_manager,
                        snapshot=state_snapshot,
                        anchor_root=anchor_root,
                        reason="storage_selection_continue_current_session",
                    )
                except Exception as exc:
                    response.status_code = 503
                    return {
                        "ok": False,
                        "error_code": "startup_release_failed",
                        "error": f"当前会话暂时无法解除受限启动，请重试或刷新页面后再继续。{exc}",
                    }
                return {
                    "ok": True,
                    "result": "continue_current_session",
                    "selected_root": str(current_root),
                    "selection_source": policy_payload["selection_source"],
                }

            state_snapshot = _snapshot_storage_mutation_state(config_manager, anchor_root=anchor_root)
            policy_payload = save_storage_policy(
                config_manager,
                selected_root=current_root,
                selection_source=payload.selection_source,
                anchor_root=anchor_root,
            )
            set_root_mode(
                config_manager,
                ROOT_MODE_NORMAL,
                current_root=str(current_root),
                last_known_good_root=str(current_root),
                last_migration_result=f"recovered:selected_root_unavailable:{committed_selected_root}",
            )
            try:
                await _release_storage_startup_barrier_or_rollback(
                    config_manager,
                    snapshot=state_snapshot,
                    anchor_root=anchor_root,
                    reason="storage_selection_continue_current_session",
                )
            except Exception as exc:
                response.status_code = 503
                return {
                    "ok": False,
                    "error_code": "startup_release_failed",
                    "error": f"当前会话暂时无法解除受限启动，请重试或刷新页面后再继续。{exc}",
                }
            return {
                "ok": True,
                "result": "continue_current_session",
                "selected_root": str(current_root),
                "selection_source": policy_payload["selection_source"],
            }
        state_snapshot = _snapshot_storage_mutation_state(config_manager, anchor_root=anchor_root)
        policy_payload = save_storage_policy(
            config_manager,
            selected_root=current_root,
            selection_source=payload.selection_source,
            anchor_root=anchor_root,
        )
        try:
            await _release_storage_startup_barrier_or_rollback(
                config_manager,
                snapshot=state_snapshot,
                anchor_root=anchor_root,
                reason="storage_selection_continue_current_session",
            )
        except Exception as exc:
            response.status_code = 503
            return {
                "ok": False,
                "error_code": "startup_release_failed",
                "error": f"当前会话暂时无法解除受限启动，请重试或刷新页面后再继续。{exc}",
            }
        return {
            "ok": True,
            "result": "continue_current_session",
            "selected_root": str(current_root),
            "selection_source": policy_payload["selection_source"],
        }

    if bool(blocking_bootstrap.get("recovery_required")) and selected_root_missing_recovery:
        if not paths_equal(normalized_selected_root, committed_selected_root):
            response.status_code = 409
            return {
                "ok": False,
                "error_code": "recovery_source_unavailable",
                "error": "原始数据路径当前不可用。请先重连原路径，或显式切回推荐默认路径继续当前会话。",
            }
        if not is_runtime_root_available(committed_selected_root):
            response.status_code = 409
            return {
                "ok": False,
                "error_code": "selected_root_unavailable",
                "error": "原始数据路径当前仍不可用，请先恢复该路径后再重试。",
            }
        restart_preflight = _build_restart_preflight(
            current_root,
            normalized_selected_root,
            config_manager=config_manager,
            estimated_required_bytes=0,
            allow_existing_target_content=True,
        )
        return {
            "ok": True,
            "result": "restart_required",
            "restart_mode": "rebind_only",
            "selected_root": str(normalized_selected_root),
            "selection_source": payload.selection_source,
            **restart_preflight,
        }

    restart_preflight = _build_restart_preflight(
        current_root,
        normalized_selected_root,
        config_manager=config_manager,
    )
    return {
        "ok": True,
        "result": "restart_required",
        "restart_mode": "migrate_after_shutdown",
        "selected_root": str(normalized_selected_root),
        "selection_source": payload.selection_source,
        **restart_preflight,
    }


@router.post("/preflight")
async def post_storage_location_preflight(
    payload: StorageLocationSelectionRequest,
    response: Response,
):
    _set_no_cache_headers(response)

    config_manager = _get_storage_config_manager()
    current_root = normalize_runtime_root(config_manager.app_docs_dir)
    anchor_root = compute_anchor_root(config_manager, current_root=current_root)

    blocking_bootstrap = build_storage_location_bootstrap_payload(config_manager)
    blocking_reason = str(blocking_bootstrap.get("blocking_reason") or "").strip()
    root_state = config_manager.load_root_state()
    root_mode = str(root_state.get("mode") or ROOT_MODE_NORMAL).strip() or ROOT_MODE_NORMAL
    if blocking_reason or root_mode == ROOT_MODE_MAINTENANCE_READONLY:
        response.status_code = 409
        if blocking_reason == "migration_pending" or root_mode == ROOT_MODE_MAINTENANCE_READONLY:
            return {
                "ok": False,
                "error_code": "migration_already_pending",
                "error": "当前存储状态仍需恢复或迁移，暂时不能发起新的存储位置变更。",
                "blocking_reason": blocking_reason or "maintenance_readonly",
            }
        return {
            "ok": False,
            "error_code": "storage_bootstrap_blocking",
            "error": "当前存储状态仍需恢复或迁移，暂时不能发起新的存储位置变更。",
            "blocking_reason": blocking_reason,
        }

    try:
        normalized_selected_root = validate_selected_root(
            config_manager,
            payload.selected_root,
            current_root=current_root,
            anchor_root=anchor_root,
            selection_source=payload.selection_source,
        )
    except StorageSelectionValidationError as exc:
        response.status_code = 400
        return {
            "ok": False,
            "error_code": exc.error_code,
            "error": exc.message,
        }

    if paths_equal(normalized_selected_root, current_root):
        return {
            "ok": True,
            "result": "restart_not_required",
            "selected_root": str(normalized_selected_root),
            "target_root": str(normalized_selected_root),
            "selection_source": payload.selection_source,
        }

    restart_preflight = _build_restart_preflight(
        current_root,
        normalized_selected_root,
        config_manager=config_manager,
    )
    return {
        "ok": True,
        "result": "restart_required",
        "restart_mode": "migrate_after_shutdown",
        "selected_root": str(normalized_selected_root),
        "selection_source": payload.selection_source,
        **restart_preflight,
    }


@router.post("/restart")
async def post_storage_location_restart(
    payload: StorageLocationSelectionRequest,
    response: Response,
):
    async with _storage_mutation_lock:
        return await _post_storage_location_restart_locked(payload, response)


async def _post_storage_location_restart_locked(
    payload: StorageLocationSelectionRequest,
    response: Response,
):
    _set_no_cache_headers(response)

    config_manager = _get_storage_config_manager()
    current_root = normalize_runtime_root(config_manager.app_docs_dir)
    anchor_root = compute_anchor_root(config_manager, current_root=current_root)

    try:
        normalized_selected_root = validate_selected_root(
            config_manager,
            payload.selected_root,
            current_root=current_root,
            anchor_root=anchor_root,
            selection_source=payload.selection_source,
        )
    except StorageSelectionValidationError as exc:
        response.status_code = 400
        return {
            "ok": False,
            "error_code": exc.error_code,
            "error": exc.message,
        }

    if paths_equal(normalized_selected_root, current_root):
        response.status_code = 409
        return {
            "ok": False,
            "error_code": "restart_not_required",
            "error": "目标路径与当前路径一致，不需要关闭当前实例。",
        }

    request_app_shutdown = get_request_app_shutdown()
    if not callable(request_app_shutdown):
        response.status_code = 503
        return {
            "ok": False,
            "error_code": "restart_unavailable",
            "error": "当前实例暂时无法执行受控关闭，请稍后重试。",
        }

    blocking_bootstrap = build_storage_location_bootstrap_payload(config_manager)
    if bool(blocking_bootstrap.get("migration_pending")):
        response.status_code = 409
        return {
            "ok": False,
            "error_code": "migration_already_pending",
            "error": "已有存储迁移正在等待执行，请先完成或恢复当前迁移后再发起新的重启迁移。",
        }
    selected_root_missing_recovery = _is_selected_root_missing_recovery(
        config_manager,
        current_root=current_root,
        anchor_root=anchor_root,
    )
    committed_selected_root = _load_committed_selected_root(
        config_manager,
        anchor_root=anchor_root,
        fallback_root=current_root,
    )
    if bool(blocking_bootstrap.get("recovery_required")) and selected_root_missing_recovery:
        if not paths_equal(normalized_selected_root, committed_selected_root):
            response.status_code = 409
            return {
                "ok": False,
                "error_code": "recovery_source_unavailable",
                "error": "原始数据路径当前不可用。请先重连原路径，或显式切回推荐默认路径继续当前会话。",
            }
        if not is_runtime_root_available(committed_selected_root):
            response.status_code = 409
            return {
                "ok": False,
                "error_code": "selected_root_unavailable",
                "error": "原始数据路径当前仍不可用，请先恢复该路径后再重试。",
            }

        restart_preflight = _build_restart_preflight(
            current_root,
            normalized_selected_root,
            config_manager=config_manager,
            estimated_required_bytes=0,
            allow_existing_target_content=True,
        )
        if restart_preflight["blocking_error_code"]:
            response.status_code = 409
            return {
                "ok": False,
                "error_code": restart_preflight["blocking_error_code"],
                "error": restart_preflight["blocking_error_message"],
                "restart_mode": "rebind_only",
                **restart_preflight,
            }

        state_snapshot = _snapshot_storage_mutation_state(config_manager, anchor_root=anchor_root)
        try:
            delete_storage_migration(config_manager, anchor_root=anchor_root)
            save_storage_policy(
                config_manager,
                selected_root=normalized_selected_root,
                selection_source=payload.selection_source,
                anchor_root=anchor_root,
            )
            set_root_mode(
                config_manager,
                ROOT_MODE_MAINTENANCE_READONLY,
                last_migration_source=str(normalized_selected_root),
                last_migration_result=f"restart_rebind:{normalized_selected_root}",
            )
            await _request_app_shutdown(request_app_shutdown)
        except Exception as exc:
            try:
                _restore_storage_mutation_state(config_manager, state_snapshot, anchor_root=anchor_root)
            except Exception:
                logger.exception(
                    "failed to rollback storage mutation state after restart scheduling failed",
                )

            response.status_code = 500
            return {
                "ok": False,
                "error_code": "restart_schedule_failed",
                "error": f"受控关闭启动失败: {exc}",
                "restart_mode": "rebind_only",
                **restart_preflight,
            }
        return {
            "ok": True,
            "result": "restart_initiated",
            "restart_mode": "rebind_only",
            "selected_root": str(normalized_selected_root),
            "selection_source": payload.selection_source,
            **restart_preflight,
        }

    restart_preflight = _build_restart_preflight(
        current_root,
        normalized_selected_root,
        config_manager=config_manager,
    )
    if restart_preflight["blocking_error_code"]:
        response.status_code = 409
        return {
            "ok": False,
            "error_code": restart_preflight["blocking_error_code"],
            "error": restart_preflight["blocking_error_message"],
            **restart_preflight,
        }
    if restart_preflight["requires_existing_target_confirmation"] and not payload.confirm_existing_target_content:
        response.status_code = 409
        return {
            "ok": False,
            "error_code": "target_confirmation_required",
            "error": restart_preflight["existing_target_confirmation_message"],
            **restart_preflight,
        }

    previous_root_state = config_manager.load_root_state()
    previous_migration_payload = load_storage_migration(config_manager, anchor_root=anchor_root)
    migration_payload = create_pending_storage_migration(
        config_manager,
        source_root=current_root,
        target_root=normalized_selected_root,
        selection_source=payload.selection_source,
        anchor_root=anchor_root,
        confirmed_existing_target_content=bool(payload.confirm_existing_target_content),
    )

    try:
        set_root_mode(
            config_manager,
            ROOT_MODE_MAINTENANCE_READONLY,
            last_migration_source=str(current_root),
            last_migration_result=f"restart_pending:{normalized_selected_root}",
        )
        await _request_app_shutdown(request_app_shutdown)
    except Exception as exc:
        try:
            if isinstance(previous_migration_payload, dict):
                save_storage_migration(config_manager, previous_migration_payload, anchor_root=anchor_root)
            else:
                delete_storage_migration(config_manager, anchor_root=anchor_root)
        except Exception:
            try:
                delete_storage_migration(config_manager, anchor_root=anchor_root)
            except Exception:
                pass
        try:
            config_manager.save_root_state(previous_root_state)
        except Exception:
            pass
        response.status_code = 500
        return {
            "ok": False,
            "error_code": "restart_schedule_failed",
            "error": f"受控关闭启动失败: {exc}",
            **restart_preflight,
        }

    return {
        "ok": True,
        "result": "restart_initiated",
        "restart_mode": "migrate_after_shutdown",
        "selected_root": str(normalized_selected_root),
        "selection_source": payload.selection_source,
        "migration": migration_payload,
        **restart_preflight,
    }
