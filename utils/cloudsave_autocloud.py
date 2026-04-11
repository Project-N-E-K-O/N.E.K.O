from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

from utils.cloudsave_runtime import (
    _runtime_root_has_user_content,
    bootstrap_local_cloudsave_environment,
    export_local_cloudsave_snapshot,
    import_local_cloudsave_snapshot,
    load_cloudsave_manifest,
)
from utils.steam_cloud_bundle import (
    download_cloudsave_bundle_from_steam,
    upload_cloudsave_bundle_to_steam,
)


STEAM_AUTO_CLOUD_SYNC_BACKEND = "steam_auto_cloud"

PROJECT_ROOT = Path(__file__).resolve().parents[1]
STEAM_APP_ID_PATH = PROJECT_ROOT / "steam_appid.txt"


def _load_steam_app_id() -> str:
    try:
        return STEAM_APP_ID_PATH.read_text(encoding="utf-8").strip()
    except Exception:
        return ""


def _build_recommended_root_paths(config_manager) -> dict[str, Any]:
    app_name = str(getattr(config_manager, "app_name", "N.E.K.O") or "N.E.K.O")
    subdirectory = f"{app_name}/cloudsave"
    return {
        "cross_platform_strategy": "single_root_with_overrides",
        "primary_root": {
            "root": "WinAppDataLocal",
            "subdirectory": subdirectory,
            "pattern": "*",
            "os": "All OSes",
            "recursive": True,
        },
        "root_overrides": [
            {
                "os": "macOS",
                "new_root": "MacAppSupport",
                "add_replace_path": subdirectory,
                "replace_path": True,
            },
            {
                "os": "Linux",
                "new_root": "LinuxXdgDataHome",
                "add_replace_path": subdirectory,
                "replace_path": True,
            },
        ],
    }


def _build_current_platform_rule_preview(config_manager) -> dict[str, str]:
    app_name = str(getattr(config_manager, "app_name", "N.E.K.O") or "N.E.K.O")
    subdirectory = f"{app_name}/cloudsave"
    if sys.platform == "win32":
        return {
            "platform": "windows",
            "root": "WinAppDataLocal",
            "subdirectory": subdirectory,
        }
    if sys.platform == "darwin":
        return {
            "platform": "macOS",
            "root": "MacAppSupport",
            "subdirectory": subdirectory,
        }
    return {
        "platform": "linux",
        "root": "LinuxXdgDataHome",
        "subdirectory": subdirectory,
    }


def _build_missing_snapshot_hint(status: dict[str, Any]) -> str:
    cloudsave_root = str(status.get("cloudsave_root") or "")
    runtime_root = str(status.get("runtime_root") or "")
    launched_from_source = not getattr(sys, "frozen", False) and Path(sys.argv[0]).suffix.lower() == ".py"

    hint_parts = [
        f"No staged Steam Auto-Cloud snapshot was found under {cloudsave_root}.",
    ]
    if launched_from_source:
        hint_parts.append(
            "If you are validating cross-device Steam Cloud sync, launch through Steam or the desktop launcher once "
            "so Steam can download cloudsave/ before startup import."
        )
    else:
        hint_parts.append(
            "If you expected cloud data on this device, launch through Steam once and confirm Auto-Cloud finished "
            "downloading cloudsave/ before startup import."
        )
    if runtime_root and bool(status.get("runtime_has_user_content")):
        hint_parts.append(
            f"The current runtime root {runtime_root} already has local user content, so this session will continue "
            "with local data until a staged snapshot appears."
        )
    return " ".join(hint_parts)


def _build_steam_connectivity_status(steamworks) -> dict[str, bool]:
    if steamworks is None:
        return {
            "available": False,
            "running": False,
            "logged_on": False,
        }

    try:
        running = bool(steamworks.IsSteamRunning())
    except Exception:
        running = False
    try:
        logged_on = bool(steamworks.Users.LoggedOn())
    except Exception:
        logged_on = False
    return {
        "available": running and logged_on,
        "running": running,
        "logged_on": logged_on,
    }


class CloudSaveManager:
    def __init__(self, config_manager):
        self.config_manager = config_manager

    def _try_download_remote_bundle(self, *, steamworks=None, deadline_monotonic: float | None = None) -> dict[str, Any]:
        try:
            return download_cloudsave_bundle_from_steam(
                self.config_manager,
                steamworks=steamworks,
                deadline_monotonic=deadline_monotonic,
            )
        except Exception as exc:
            return {
                "success": False,
                "action": "failed",
                "reason": "remote_bundle_download_failed",
                "message": str(exc),
            }

    def _try_upload_remote_bundle(self, *, steamworks=None, deadline_monotonic: float | None = None) -> dict[str, Any]:
        try:
            return upload_cloudsave_bundle_to_steam(
                self.config_manager,
                steamworks=steamworks,
                deadline_monotonic=deadline_monotonic,
            )
        except Exception as exc:
            return {
                "success": False,
                "action": "failed",
                "reason": "remote_bundle_upload_failed",
                "message": str(exc),
            }

    def build_status(self, *, steamworks=None) -> dict[str, Any]:
        bootstrap_local_cloudsave_environment(self.config_manager)
        cloud_state = self.config_manager.load_cloudsave_local_state()
        manifest = load_cloudsave_manifest(self.config_manager)
        manifest_files = manifest.get("files") if isinstance(manifest.get("files"), dict) else {}
        manifest_fingerprint = str(manifest.get("fingerprint") or "")
        last_applied_manifest_fingerprint = str(cloud_state.get("last_applied_manifest_fingerprint") or "")
        runtime_has_user_content = _runtime_root_has_user_content(self.config_manager.app_docs_dir)
        has_snapshot = bool(manifest_files)
        startup_import_required = bool(
            has_snapshot
            and (
                not runtime_has_user_content
                or not last_applied_manifest_fingerprint
                or not manifest_fingerprint
                or manifest_fingerprint != last_applied_manifest_fingerprint
            )
        )

        steam_status = _build_steam_connectivity_status(steamworks)
        recommended_paths = _build_recommended_root_paths(self.config_manager)
        current_platform_rule = _build_current_platform_rule_preview(self.config_manager)
        return {
            "backend": STEAM_AUTO_CLOUD_SYNC_BACKEND,
            "app_id": _load_steam_app_id(),
            "has_snapshot": has_snapshot,
            "manifest_fingerprint": manifest_fingerprint,
            "last_applied_manifest_fingerprint": last_applied_manifest_fingerprint,
            "startup_import_required": startup_import_required,
            "runtime_has_user_content": runtime_has_user_content,
            "last_successful_export_at": str(cloud_state.get("last_successful_export_at") or ""),
            "last_successful_import_at": str(cloud_state.get("last_successful_import_at") or ""),
            "steam_available": bool(steam_status["available"]),
            "steam_running": bool(steam_status["running"]),
            "steam_logged_on": bool(steam_status["logged_on"]),
            "runtime_root": str(self.config_manager.app_docs_dir),
            "cloudsave_root": str(self.config_manager.cloudsave_dir),
            "manifest_path": str(self.config_manager.cloudsave_manifest_path),
            "manifest_exists": bool(self.config_manager.cloudsave_manifest_path.is_file()),
            "recommended_paths": recommended_paths,
            "current_platform_rule": current_platform_rule,
        }

    def import_if_needed(
        self,
        *,
        reason: str = "",
        force: bool = False,
        steamworks=None,
        deadline_monotonic: float | None = None,
    ) -> dict[str, Any]:
        remote_bundle_result = self._try_download_remote_bundle(
            steamworks=steamworks,
            deadline_monotonic=deadline_monotonic,
        )
        status = self.build_status(steamworks=steamworks)
        if not status["has_snapshot"]:
            return {
                "success": True,
                "action": "skipped",
                "reason": "no_snapshot",
                "requested_reason": str(reason or ""),
                "hint": _build_missing_snapshot_hint(status),
                "remote_bundle_result": remote_bundle_result,
                "status": status,
            }
        if not force and not status["startup_import_required"]:
            return {
                "success": True,
                "action": "skipped",
                "reason": "already_applied",
                "requested_reason": str(reason or ""),
                "remote_bundle_result": remote_bundle_result,
                "status": status,
            }
        result = import_local_cloudsave_snapshot(
            self.config_manager,
            deadline_monotonic=deadline_monotonic,
        )
        return {
            "success": True,
            "action": "imported",
            "requested_reason": str(reason or ""),
            "result": result,
            "remote_bundle_result": remote_bundle_result,
            "status": self.build_status(steamworks=steamworks),
        }

    def export_snapshot(
        self,
        *,
        reason: str = "",
        steamworks=None,
        deadline_monotonic: float | None = None,
    ) -> dict[str, Any]:
        result = export_local_cloudsave_snapshot(
            self.config_manager,
            deadline_monotonic=deadline_monotonic,
        )
        remote_bundle_result = self._try_upload_remote_bundle(
            steamworks=steamworks,
            deadline_monotonic=deadline_monotonic,
        )
        return {
            "success": True,
            "action": "exported",
            "requested_reason": str(reason or ""),
            "result": result,
            "remote_bundle_result": remote_bundle_result,
            "status": self.build_status(steamworks=steamworks),
        }


def get_cloudsave_manager(config_manager) -> CloudSaveManager:
    return CloudSaveManager(config_manager)


def build_steam_autocloud_status(config_manager, *, steamworks=None) -> dict[str, Any]:
    return get_cloudsave_manager(config_manager).build_status(steamworks=steamworks)
