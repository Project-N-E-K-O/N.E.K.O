from __future__ import annotations

import io
import json
import os
import shutil
import sys
import tempfile
import zipfile
from contextlib import contextmanager
from ctypes import POINTER, WinDLL, byref, c_bool, c_char_p, c_int32, c_void_p, create_string_buffer
from pathlib import Path, PurePosixPath
from typing import Any

from utils.cloudsave_runtime import (
    CloudsaveDeadlineExceeded,
    _assert_deadline_not_exceeded,
    _atomic_copy_file,
    _cleanup_empty_parent_dirs,
    _create_staging_workspace,
    _list_existing_cloudsave_files,
    _load_json_if_exists,
    load_cloudsave_manifest,
)


REMOTE_BUNDLE_FILENAME = "__neko_cloudsave_bundle__.zip"
REMOTE_META_FILENAME = "__neko_cloudsave_bundle_meta__.json"
REMOTE_META_SCHEMA_VERSION = 1


def is_source_launch() -> bool:
    argv0 = Path(sys.argv[0]).name.lower()
    if "pytest" in argv0 or os.environ.get("PYTEST_CURRENT_TEST"):
        return False
    return not getattr(sys, "frozen", False) and Path(sys.argv[0]).suffix.lower() == ".py"


def _managed_cloudsave_relative_paths(config_manager) -> list[str]:
    manifest = load_cloudsave_manifest(config_manager)
    manifest_files = manifest.get("files") if isinstance(manifest.get("files"), dict) else {}
    relative_paths = sorted(str(path) for path in manifest_files.keys())
    if relative_paths:
        return ["manifest.json", *relative_paths]
    if config_manager.cloudsave_manifest_path.is_file():
        return ["manifest.json"]
    return []


def _bundle_meta_payload(config_manager) -> dict[str, Any]:
    manifest = load_cloudsave_manifest(config_manager)
    manifest_files = manifest.get("files") if isinstance(manifest.get("files"), dict) else {}
    return {
        "schema_version": REMOTE_META_SCHEMA_VERSION,
        "bundle_format": "zip",
        "manifest_fingerprint": str(manifest.get("fingerprint") or ""),
        "sequence_number": int(manifest.get("sequence_number") or 0),
        "exported_at_utc": str(manifest.get("exported_at_utc") or ""),
        "file_count": len(manifest_files),
    }


def _cloudsave_manifest_matches_local_files(config_manager, manifest_fingerprint: str) -> bool:
    manifest_fingerprint = str(manifest_fingerprint or "")
    if not manifest_fingerprint:
        return False
    local_manifest = _load_json_if_exists(config_manager.cloudsave_manifest_path)
    if not isinstance(local_manifest, dict):
        return False
    if str(local_manifest.get("fingerprint") or "") != manifest_fingerprint:
        return False
    manifest_files = local_manifest.get("files") if isinstance(local_manifest.get("files"), dict) else {}
    for relative_path in manifest_files.keys():
        if not (config_manager.cloudsave_dir / relative_path).is_file():
            return False
    return config_manager.cloudsave_manifest_path.is_file()


def _write_remote_bundle(stage_bundle_path: Path, config_manager) -> dict[str, Any]:
    relative_paths = _managed_cloudsave_relative_paths(config_manager)
    if not relative_paths:
        raise FileNotFoundError("no local cloudsave snapshot is available to bundle")

    with zipfile.ZipFile(stage_bundle_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for relative_path in relative_paths:
            source_path = config_manager.cloudsave_dir / relative_path
            if not source_path.is_file():
                raise FileNotFoundError(f"cloudsave file missing while bundling: {relative_path}")
            archive.write(source_path, arcname=relative_path)

    return {
        "relative_paths": relative_paths,
        "meta": _bundle_meta_payload(config_manager),
    }


def _resolve_safe_archive_target(stage_cloudsave_root: Path, member_name: str) -> Path:
    normalized_name = str(member_name or "").replace("\\", "/")
    pure_path = PurePosixPath(normalized_name)
    if not normalized_name or pure_path.is_absolute() or ".." in pure_path.parts:
        raise ValueError(f"unsafe archive entry: {member_name}")

    target_path = (stage_cloudsave_root / Path(*pure_path.parts)).resolve()
    stage_root_resolved = stage_cloudsave_root.resolve()
    try:
        target_path.relative_to(stage_root_resolved)
    except ValueError as exc:
        raise ValueError(f"unsafe archive entry: {member_name}") from exc
    return target_path


def _extract_bundle_archive_safely(stage_cloudsave_root: Path, bundle_bytes: bytes) -> None:
    with zipfile.ZipFile(io.BytesIO(bundle_bytes), "r") as archive:
        for member in archive.infolist():
            target_path = _resolve_safe_archive_target(stage_cloudsave_root, member.filename)
            if member.is_dir():
                target_path.mkdir(parents=True, exist_ok=True)
                continue
            target_path.parent.mkdir(parents=True, exist_ok=True)
            with archive.open(member, "r") as src, target_path.open("wb") as dst:
                shutil.copyfileobj(src, dst)


def _apply_bundle_to_local_cloudsave(config_manager, bundle_bytes: bytes, meta_payload: dict[str, Any] | None) -> dict[str, Any]:
    stage_root = _create_staging_workspace(config_manager, "remote-bundle")
    stage_root.mkdir(parents=True, exist_ok=True)
    stage_cloudsave_root = stage_root / "cloudsave"
    stage_cloudsave_root.mkdir(parents=True, exist_ok=True)
    existing_relative_paths = {"manifest.json", *_list_existing_cloudsave_files(config_manager)}

    _extract_bundle_archive_safely(stage_cloudsave_root, bundle_bytes)

    stage_manifest_path = stage_cloudsave_root / "manifest.json"
    stage_manifest = _load_json_if_exists(stage_manifest_path)
    if not isinstance(stage_manifest, dict):
        raise ValueError("remote cloudsave bundle does not contain a valid manifest.json")

    remote_fingerprint = str(stage_manifest.get("fingerprint") or "")
    expected_fingerprint = ""
    if isinstance(meta_payload, dict):
        expected_fingerprint = str(meta_payload.get("manifest_fingerprint") or "")
    if expected_fingerprint and remote_fingerprint and expected_fingerprint != remote_fingerprint:
        raise ValueError("remote cloudsave bundle fingerprint does not match metadata")

    manifest_files = stage_manifest.get("files") if isinstance(stage_manifest.get("files"), dict) else {}
    managed_relative_paths = {"manifest.json", *(str(path) for path in manifest_files.keys())}

    for relative_path in managed_relative_paths:
        staged_file = stage_cloudsave_root / relative_path
        if not staged_file.is_file():
            raise FileNotFoundError(f"remote cloudsave bundle is missing {relative_path}")

    for relative_path in managed_relative_paths:
        _atomic_copy_file(stage_cloudsave_root / relative_path, config_manager.cloudsave_dir / relative_path)

    stale_relative_paths = existing_relative_paths - managed_relative_paths
    stale_relative_paths.discard("manifest.json")
    for relative_path in sorted(stale_relative_paths):
        target_path = config_manager.cloudsave_dir / relative_path
        if target_path.exists():
            target_path.unlink()
            _cleanup_empty_parent_dirs(target_path, config_manager.cloudsave_dir)

    return {
        "manifest_fingerprint": remote_fingerprint,
        "downloaded_file_count": len(managed_relative_paths),
    }


def _steam_api_dll_path() -> Path:
    return Path(__file__).resolve().parents[1] / "steam_api64.dll"


class SteamCloudBundleBridge:
    def __init__(self, steamworks):
        self._owned_steamworks = None
        self.steamworks = steamworks
        self._api = None
        self._remote = None

    @classmethod
    def create(cls, steamworks=None):
        bridge = cls(steamworks=steamworks)
        bridge._initialize()
        return bridge

    def _initialize(self) -> None:
        if self.steamworks is None:
            from steamworks import STEAMWORKS

            self._owned_steamworks = STEAMWORKS()
            self._owned_steamworks.initialize()
            self.steamworks = self._owned_steamworks

        self._api = WinDLL(str(_steam_api_dll_path().resolve()))
        self._api.SteamAPI_SteamRemoteStorage_v016.restype = c_void_p
        self._api.SteamAPI_ISteamRemoteStorage_IsCloudEnabledForAccount.argtypes = [c_void_p]
        self._api.SteamAPI_ISteamRemoteStorage_IsCloudEnabledForAccount.restype = c_bool
        self._api.SteamAPI_ISteamRemoteStorage_IsCloudEnabledForApp.argtypes = [c_void_p]
        self._api.SteamAPI_ISteamRemoteStorage_IsCloudEnabledForApp.restype = c_bool
        self._api.SteamAPI_ISteamRemoteStorage_GetFileCount.argtypes = [c_void_p]
        self._api.SteamAPI_ISteamRemoteStorage_GetFileCount.restype = c_int32
        self._api.SteamAPI_ISteamRemoteStorage_GetFileNameAndSize.argtypes = [c_void_p, c_int32, POINTER(c_int32)]
        self._api.SteamAPI_ISteamRemoteStorage_GetFileNameAndSize.restype = c_char_p
        self._api.SteamAPI_ISteamRemoteStorage_FileExists.argtypes = [c_void_p, c_char_p]
        self._api.SteamAPI_ISteamRemoteStorage_FileExists.restype = c_bool
        self._api.SteamAPI_ISteamRemoteStorage_FileDelete.argtypes = [c_void_p, c_char_p]
        self._api.SteamAPI_ISteamRemoteStorage_FileDelete.restype = c_bool
        self._api.SteamAPI_ISteamRemoteStorage_FileWrite.argtypes = [c_void_p, c_char_p, c_void_p, c_int32]
        self._api.SteamAPI_ISteamRemoteStorage_FileWrite.restype = c_bool
        self._api.SteamAPI_ISteamRemoteStorage_FileRead.argtypes = [c_void_p, c_char_p, c_void_p, c_int32]
        self._api.SteamAPI_ISteamRemoteStorage_FileRead.restype = c_int32
        self._api.SteamAPI_ISteamRemoteStorage_GetFileSize.argtypes = [c_void_p, c_char_p]
        self._api.SteamAPI_ISteamRemoteStorage_GetFileSize.restype = c_int32
        self._remote = self._api.SteamAPI_SteamRemoteStorage_v016()
        if not self._remote:
            raise RuntimeError("Steam RemoteStorage interface is unavailable")

    def close(self) -> None:
        if self._owned_steamworks is not None:
            try:
                self._owned_steamworks.unload()
            except Exception:
                pass
            self._owned_steamworks = None

    def cloud_enabled(self) -> bool:
        return bool(
            self._api.SteamAPI_ISteamRemoteStorage_IsCloudEnabledForAccount(self._remote)
            and self._api.SteamAPI_ISteamRemoteStorage_IsCloudEnabledForApp(self._remote)
        )

    def file_exists(self, remote_name: str) -> bool:
        return bool(self._api.SteamAPI_ISteamRemoteStorage_FileExists(self._remote, remote_name.encode("ascii")))

    def read_file(self, remote_name: str) -> bytes:
        size = int(self._api.SteamAPI_ISteamRemoteStorage_GetFileSize(self._remote, remote_name.encode("ascii")))
        if size < 0:
            raise FileNotFoundError(remote_name)
        buffer = create_string_buffer(max(1, size))
        actual_size = int(
            self._api.SteamAPI_ISteamRemoteStorage_FileRead(
                self._remote,
                remote_name.encode("ascii"),
                buffer,
                size,
            )
        )
        if actual_size < 0:
            raise RuntimeError(f"failed to read remote storage file: {remote_name}")
        return bytes(buffer.raw[:actual_size])

    def write_file(self, remote_name: str, payload: bytes) -> None:
        buffer = create_string_buffer(payload, max(1, len(payload)))
        success = bool(
            self._api.SteamAPI_ISteamRemoteStorage_FileWrite(
                self._remote,
                remote_name.encode("ascii"),
                buffer,
                len(payload),
            )
        )
        if not success:
            raise RuntimeError(f"failed to write remote storage file: {remote_name}")

    def delete_file(self, remote_name: str) -> bool:
        return bool(self._api.SteamAPI_ISteamRemoteStorage_FileDelete(self._remote, remote_name.encode("ascii")))


@contextmanager
def steam_cloud_bundle_bridge(steamworks=None):
    bridge = SteamCloudBundleBridge.create(steamworks=steamworks)
    try:
        yield bridge
    finally:
        bridge.close()


def download_cloudsave_bundle_from_steam(
    config_manager,
    *,
    steamworks=None,
    deadline_monotonic: float | None = None,
) -> dict[str, Any]:
    if not is_source_launch():
        return {
            "success": True,
            "action": "skipped",
            "reason": "not_source_launch",
        }

    _assert_deadline_not_exceeded(
        deadline_monotonic,
        operation="steam_remote_download",
        stage="initialize",
    )
    with steam_cloud_bundle_bridge(steamworks=steamworks) as bridge:
        if not bridge.cloud_enabled():
            return {
                "success": True,
                "action": "skipped",
                "reason": "cloud_disabled",
            }
        if not bridge.file_exists(REMOTE_META_FILENAME) or not bridge.file_exists(REMOTE_BUNDLE_FILENAME):
            return {
                "success": True,
                "action": "skipped",
                "reason": "no_remote_bundle",
            }

        meta_payload = json.loads(bridge.read_file(REMOTE_META_FILENAME).decode("utf-8"))
        remote_fingerprint = str(meta_payload.get("manifest_fingerprint") or "")
        if _cloudsave_manifest_matches_local_files(config_manager, remote_fingerprint):
            return {
                "success": True,
                "action": "skipped",
                "reason": "already_synced",
                "meta": meta_payload,
            }

        _assert_deadline_not_exceeded(
            deadline_monotonic,
            operation="steam_remote_download",
            stage="download_bundle",
        )
        bundle_bytes = bridge.read_file(REMOTE_BUNDLE_FILENAME)
        apply_result = _apply_bundle_to_local_cloudsave(config_manager, bundle_bytes, meta_payload)
        return {
            "success": True,
            "action": "downloaded",
            "meta": meta_payload,
            "result": apply_result,
        }


def upload_cloudsave_bundle_to_steam(
    config_manager,
    *,
    steamworks=None,
    deadline_monotonic: float | None = None,
) -> dict[str, Any]:
    if not is_source_launch():
        return {
            "success": True,
            "action": "skipped",
            "reason": "not_source_launch",
        }

    _assert_deadline_not_exceeded(
        deadline_monotonic,
        operation="steam_remote_upload",
        stage="initialize",
    )
    relative_paths = _managed_cloudsave_relative_paths(config_manager)
    if not relative_paths:
        return {
            "success": True,
            "action": "skipped",
            "reason": "no_local_snapshot",
        }

    with tempfile.TemporaryDirectory(prefix="neko-cloudsave-bundle-") as temp_dir:
        bundle_path = Path(temp_dir) / REMOTE_BUNDLE_FILENAME
        bundle_payload = _write_remote_bundle(bundle_path, config_manager)
        bundle_bytes = bundle_path.read_bytes()
        meta_bytes = json.dumps(bundle_payload["meta"], ensure_ascii=False, indent=2).encode("utf-8")

        _assert_deadline_not_exceeded(
            deadline_monotonic,
            operation="steam_remote_upload",
            stage="write_remote",
        )
        with steam_cloud_bundle_bridge(steamworks=steamworks) as bridge:
            if not bridge.cloud_enabled():
                return {
                    "success": True,
                    "action": "skipped",
                    "reason": "cloud_disabled",
                }
            bridge.write_file(REMOTE_BUNDLE_FILENAME, bundle_bytes)
            bridge.write_file(REMOTE_META_FILENAME, meta_bytes)
            return {
                "success": True,
                "action": "uploaded",
                "meta": bundle_payload["meta"],
                "bundle_size": len(bundle_bytes),
            }
