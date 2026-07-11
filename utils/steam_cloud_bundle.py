# Copyright 2025-2026 Project N.E.K.O. Team
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

from __future__ import annotations

import hashlib
import io
import json
import logging
import os
import shutil
import sys
import tempfile
import zipfile
import ctypes
from contextlib import contextmanager
from ctypes import POINTER, c_bool, c_char_p, c_int32, c_void_p, create_string_buffer
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any
from uuid import uuid4

from utils.cloudsave_runtime import (
    CloudsaveDeadlineExceeded,
    assert_deadline_not_exceeded,
    atomic_copy_file,
    create_staging_workspace,
    load_cloudsave_manifest,
    load_json_if_exists,
)


REMOTE_BUNDLE_FILENAME = "__neko_cloudsave_bundle__.zip"
REMOTE_META_FILENAME = "__neko_cloudsave_bundle_meta__.json"
REMOTE_META_SCHEMA_VERSION = 1
_SOURCE_SCRIPT_SUFFIXES = {".py", ".pyw"}
logger = logging.getLogger(__name__)


def _steam_remote_bundle_supported_platform() -> bool:
    return sys.platform in {"win32", "darwin", "linux", "linux2"}


def is_source_launch() -> bool:
    argv0 = Path(sys.argv[0]).name.lower()
    if "pytest" in argv0 or os.environ.get("PYTEST_CURRENT_TEST"):
        return False
    return not getattr(sys, "frozen", False) and Path(sys.argv[0]).suffix.lower() in _SOURCE_SCRIPT_SUFFIXES


def _is_packaged_launch() -> bool:
    if getattr(sys, "frozen", False):
        return True
    if "__compiled__" in globals() or "__nuitka_binary_dir" in globals():
        return True
    main_mod = sys.modules.get("__main__")
    return bool(
        main_mod is not None
        and (hasattr(main_mod, "__compiled__") or hasattr(main_mod, "__nuitka_binary_dir"))
    )


def _steam_remote_bundle_launch_allowed() -> bool:
    return bool(is_source_launch() or _is_packaged_launch())


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
    local_manifest = load_json_if_exists(config_manager.cloudsave_manifest_path)
    if not isinstance(local_manifest, dict):
        return False
    if str(local_manifest.get("fingerprint") or "") != manifest_fingerprint:
        return False
    manifest_files = local_manifest.get("files") if isinstance(local_manifest.get("files"), dict) else {}
    for relative_path, expected_metadata in manifest_files.items():
        if not isinstance(expected_metadata, dict):
            return False
        local_path = config_manager.cloudsave_dir / relative_path
        if not local_path.is_file():
            return False
        if "size" in expected_metadata:
            try:
                expected_size = int(expected_metadata.get("size"))
            except (TypeError, ValueError):
                return False
            if local_path.stat().st_size != expected_size:
                return False
        expected_sha256 = str(expected_metadata.get("sha256") or expected_metadata.get("hash") or "").strip().lower()
        if expected_sha256:
            actual_sha256 = hashlib.sha256(local_path.read_bytes()).hexdigest().lower()
            if actual_sha256 != expected_sha256:
                return False
    return config_manager.cloudsave_manifest_path.is_file()


def _parse_manifest_export_time(value: object) -> datetime | None:
    raw_value = str(value or "").strip()
    if not raw_value:
        return None
    if raw_value.endswith("Z"):
        raw_value = f"{raw_value[:-1]}+00:00"
    try:
        parsed = datetime.fromisoformat(raw_value)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _local_cloudsave_snapshot_is_newer_or_equal(config_manager, remote_manifest_like: dict[str, Any]) -> bool:
    local_manifest = load_json_if_exists(config_manager.cloudsave_manifest_path)
    if not isinstance(local_manifest, dict) or not isinstance(remote_manifest_like, dict):
        return False
    local_fingerprint = str(local_manifest.get("fingerprint") or "")
    remote_fingerprint = str(
        remote_manifest_like.get("manifest_fingerprint") or remote_manifest_like.get("fingerprint") or ""
    )
    if local_fingerprint and remote_fingerprint and local_fingerprint == remote_fingerprint:
        return _cloudsave_manifest_matches_local_files(config_manager, remote_fingerprint)
    if not local_fingerprint or not _cloudsave_manifest_matches_local_files(config_manager, local_fingerprint):
        return False

    local_exported_at = _parse_manifest_export_time(local_manifest.get("exported_at_utc"))
    remote_exported_at = _parse_manifest_export_time(remote_manifest_like.get("exported_at_utc"))
    if local_exported_at is None or remote_exported_at is None:
        return False
    return local_exported_at >= remote_exported_at


def _write_remote_bundle(
    stage_bundle_path: Path,
    config_manager,
    *,
    deadline_monotonic: float | None = None,
) -> dict[str, Any]:
    relative_paths = _managed_cloudsave_relative_paths(config_manager)
    if not relative_paths:
        raise FileNotFoundError("no local cloudsave snapshot is available to bundle")

    assert_deadline_not_exceeded(
        deadline_monotonic,
        operation="steam_remote_upload",
        stage="bundle_prepare",
    )
    with zipfile.ZipFile(stage_bundle_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for relative_path in relative_paths:
            assert_deadline_not_exceeded(
                deadline_monotonic,
                operation="steam_remote_upload",
                stage=f"bundle_write_start:{relative_path}",
            )
            source_path = config_manager.cloudsave_dir / relative_path
            if not source_path.is_file():
                raise FileNotFoundError(f"cloudsave file missing while bundling: {relative_path}")
            archive.write(source_path, arcname=relative_path)
            assert_deadline_not_exceeded(
                deadline_monotonic,
                operation="steam_remote_upload",
                stage=f"bundle_write_done:{relative_path}",
            )
    assert_deadline_not_exceeded(
        deadline_monotonic,
        operation="steam_remote_upload",
        stage="bundle_finalize",
    )

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


def _extract_bundle_archive_safely(
    stage_cloudsave_root: Path,
    bundle_bytes: bytes,
    *,
    deadline_monotonic: float | None = None,
) -> None:
    assert_deadline_not_exceeded(
        deadline_monotonic,
        operation="steam_remote_download",
        stage="extract_begin",
    )
    with zipfile.ZipFile(io.BytesIO(bundle_bytes), "r") as archive:
        for member in archive.infolist():
            assert_deadline_not_exceeded(
                deadline_monotonic,
                operation="steam_remote_download",
                stage=f"extract_start:{member.filename}",
            )
            target_path = _resolve_safe_archive_target(stage_cloudsave_root, member.filename)
            if member.is_dir():
                target_path.mkdir(parents=True, exist_ok=True)
                assert_deadline_not_exceeded(
                    deadline_monotonic,
                    operation="steam_remote_download",
                    stage=f"extract_done:{member.filename}",
                )
                continue
            target_path.parent.mkdir(parents=True, exist_ok=True)
            with archive.open(member, "r") as src, target_path.open("wb") as dst:
                shutil.copyfileobj(src, dst)
            assert_deadline_not_exceeded(
                deadline_monotonic,
                operation="steam_remote_download",
                stage=f"extract_done:{member.filename}",
            )


def _apply_bundle_to_local_cloudsave(
    config_manager,
    bundle_bytes: bytes,
    meta_payload: dict[str, Any] | None,
    *,
    deadline_monotonic: float | None = None,
    preserve_newer_local: bool = False,
) -> dict[str, Any]:
    assert_deadline_not_exceeded(
        deadline_monotonic,
        operation="steam_remote_download",
        stage="apply_prepare",
    )
    stage_root = create_staging_workspace(config_manager, "remote-bundle")
    stage_root.mkdir(parents=True, exist_ok=True)
    stage_cloudsave_root = stage_root / "cloudsave"
    stage_cloudsave_root.mkdir(parents=True, exist_ok=True)

    _extract_bundle_archive_safely(
        stage_cloudsave_root,
        bundle_bytes,
        deadline_monotonic=deadline_monotonic,
    )

    stage_manifest_path = stage_cloudsave_root / "manifest.json"
    stage_manifest = load_json_if_exists(stage_manifest_path)
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
    payload_relative_paths = sorted(path for path in managed_relative_paths if path != "manifest.json")

    # manifest 里的 files key 与 zip 条目名同属不可信输入，staging 内的读写
    # 拼接统一走 _resolve_safe_archive_target 约束在各自 stage 根内。
    staged_source_paths: dict[str, Path] = {}
    for relative_path in managed_relative_paths:
        staged_file = _resolve_safe_archive_target(stage_cloudsave_root, relative_path)
        if not staged_file.is_file():
            raise FileNotFoundError(f"remote cloudsave bundle is missing {relative_path}")
        staged_source_paths[relative_path] = staged_file

    if preserve_newer_local and _local_cloudsave_snapshot_is_newer_or_equal(config_manager, stage_manifest):
        return {
            "manifest_fingerprint": remote_fingerprint,
            "downloaded_file_count": 0,
            "skipped": True,
            "reason": "local_snapshot_newer_or_equal",
        }

    stage_replacement_root = stage_root / "cloudsave-replacement"
    stage_replacement_root.mkdir(parents=True, exist_ok=True)
    for relative_path in payload_relative_paths:
        assert_deadline_not_exceeded(
            deadline_monotonic,
            operation="steam_remote_download",
            stage=f"apply_copy_start:{relative_path}",
        )
        atomic_copy_file(
            staged_source_paths[relative_path],
            _resolve_safe_archive_target(stage_replacement_root, relative_path),
        )
        assert_deadline_not_exceeded(
            deadline_monotonic,
            operation="steam_remote_download",
            stage=f"apply_copy_done:{relative_path}",
        )
    assert_deadline_not_exceeded(
        deadline_monotonic,
        operation="steam_remote_download",
        stage="apply_copy_manifest_start",
    )
    atomic_copy_file(stage_manifest_path, stage_replacement_root / "manifest.json")
    assert_deadline_not_exceeded(
        deadline_monotonic,
        operation="steam_remote_download",
        stage="apply_copy_manifest_done",
    )

    target_cloudsave_dir = config_manager.cloudsave_dir
    target_cloudsave_dir.parent.mkdir(parents=True, exist_ok=True)
    backup_cloudsave_dir = target_cloudsave_dir.parent / f"{target_cloudsave_dir.name}.rollback-{uuid4().hex}"
    has_existing_cloudsave_dir = target_cloudsave_dir.exists()

    if has_existing_cloudsave_dir:
        assert_deadline_not_exceeded(
            deadline_monotonic,
            operation="steam_remote_download",
            stage="apply_swap_backup",
        )
        os.replace(target_cloudsave_dir, backup_cloudsave_dir)

    try:
        assert_deadline_not_exceeded(
            deadline_monotonic,
            operation="steam_remote_download",
            stage="apply_swap_live",
        )
        os.replace(stage_replacement_root, target_cloudsave_dir)
    except Exception:
        if has_existing_cloudsave_dir and backup_cloudsave_dir.exists():
            os.replace(backup_cloudsave_dir, target_cloudsave_dir)
        raise
    else:
        if backup_cloudsave_dir.exists():
            shutil.rmtree(backup_cloudsave_dir, ignore_errors=True)

    return {
        "manifest_fingerprint": remote_fingerprint,
        "downloaded_file_count": len(managed_relative_paths),
    }


def _steam_api_library_name() -> str:
    if sys.platform == "win32":
        return "steam_api64.dll"
    if sys.platform == "darwin":
        return "libsteam_api.dylib"
    return "libsteam_api.so"


def _steam_api_library_path() -> Path:
    library_name = _steam_api_library_name()
    repo_root = Path(__file__).resolve().parents[1]
    candidates = [
        repo_root / library_name,
        repo_root / "steamworks" / library_name,
    ]
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    return candidates[0]


def _load_steam_api_library(path: Path):
    if sys.platform == "win32":
        return ctypes.WinDLL(str(path.resolve()))
    return ctypes.CDLL(str(path.resolve()))


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
        if not _steam_remote_bundle_supported_platform():
            raise RuntimeError(
                f"Steam RemoteStorage bundle bridge is not supported on platform={sys.platform}"
            )
        if self.steamworks is None:
            from steamworks import STEAMWORKS

            self._owned_steamworks = STEAMWORKS()
            self._owned_steamworks.initialize()
            self.steamworks = self._owned_steamworks

        self._api = _load_steam_api_library(_steam_api_library_path())
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
        self._batch_api_available = False
        try:
            self._api.SteamAPI_ISteamRemoteStorage_BeginFileWriteBatch.argtypes = [c_void_p]
            self._api.SteamAPI_ISteamRemoteStorage_BeginFileWriteBatch.restype = c_bool
            self._api.SteamAPI_ISteamRemoteStorage_EndFileWriteBatch.argtypes = [c_void_p]
            self._api.SteamAPI_ISteamRemoteStorage_EndFileWriteBatch.restype = c_bool
            self._batch_api_available = True
        except AttributeError:
            self._batch_api_available = False
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

    def begin_file_write_batch(self) -> bool:
        if not self._batch_api_available:
            return False
        return bool(self._api.SteamAPI_ISteamRemoteStorage_BeginFileWriteBatch(self._remote))

    def end_file_write_batch(self) -> bool:
        if not self._batch_api_available:
            return False
        return bool(self._api.SteamAPI_ISteamRemoteStorage_EndFileWriteBatch(self._remote))

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
    if not _steam_remote_bundle_launch_allowed():
        return {
            "success": True,
            "action": "skipped",
            "reason": "not_source_launch",
        }
    if not _steam_remote_bundle_supported_platform():
        return {
            "success": True,
            "action": "skipped",
            "reason": "unsupported_platform",
            "platform": sys.platform,
        }

    assert_deadline_not_exceeded(
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

        meta_payload: dict[str, Any] | None = None
        remote_fingerprint = ""
        try:
            parsed_meta = json.loads(bridge.read_file(REMOTE_META_FILENAME).decode("utf-8"))
            if isinstance(parsed_meta, dict):
                meta_payload = parsed_meta
                remote_fingerprint = str(meta_payload.get("manifest_fingerprint") or "")
            else:
                logger.warning(
                    "steam_cloud_bundle: remote meta payload is not a JSON object (type=%s), skip fingerprint pre-check",
                    type(parsed_meta).__name__,
                )
        except Exception as exc:
            logger.warning("steam_cloud_bundle: failed to parse remote meta payload, skip fingerprint pre-check: %s", exc)

        if remote_fingerprint and _cloudsave_manifest_matches_local_files(config_manager, remote_fingerprint):
            return {
                "success": True,
                "action": "skipped",
                "reason": "already_synced",
                "meta": meta_payload,
            }
        if isinstance(meta_payload, dict) and _local_cloudsave_snapshot_is_newer_or_equal(config_manager, meta_payload):
            return {
                "success": True,
                "action": "skipped",
                "reason": "local_snapshot_newer_or_equal",
                "meta": meta_payload,
            }

        assert_deadline_not_exceeded(
            deadline_monotonic,
            operation="steam_remote_download",
            stage="download_bundle",
        )
        bundle_bytes = bridge.read_file(REMOTE_BUNDLE_FILENAME)
        apply_result = _apply_bundle_to_local_cloudsave(
            config_manager,
            bundle_bytes,
            meta_payload,
            deadline_monotonic=deadline_monotonic,
            preserve_newer_local=True,
        )
        if apply_result.get("skipped"):
            return {
                "success": True,
                "action": "skipped",
                "reason": str(apply_result.get("reason") or "local_snapshot_newer_or_equal"),
                "meta": meta_payload,
                "result": apply_result,
            }
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
    if not _steam_remote_bundle_launch_allowed():
        return {
            "success": True,
            "action": "skipped",
            "reason": "not_source_launch",
        }
    if not _steam_remote_bundle_supported_platform():
        return {
            "success": True,
            "action": "skipped",
            "reason": "unsupported_platform",
            "platform": sys.platform,
        }

    assert_deadline_not_exceeded(
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
        bundle_payload = _write_remote_bundle(
            bundle_path,
            config_manager,
            deadline_monotonic=deadline_monotonic,
        )
        bundle_bytes = bundle_path.read_bytes()
        meta_bytes = json.dumps(bundle_payload["meta"], ensure_ascii=False, indent=2).encode("utf-8")

        assert_deadline_not_exceeded(
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
            previous_bundle_bytes = None
            previous_meta_bytes = None
            if bridge.file_exists(REMOTE_BUNDLE_FILENAME):
                previous_bundle_bytes = bridge.read_file(REMOTE_BUNDLE_FILENAME)
            if bridge.file_exists(REMOTE_META_FILENAME):
                previous_meta_bytes = bridge.read_file(REMOTE_META_FILENAME)

            batch_started = False
            begin_batch = getattr(bridge, "begin_file_write_batch", None)
            end_batch = getattr(bridge, "end_file_write_batch", None)
            if callable(begin_batch) and callable(end_batch):
                try:
                    batch_started = bool(begin_batch())
                except Exception as exc:
                    logger.warning("steam_cloud_bundle: failed to begin remote write batch; continuing: %s", exc)
            try:
                bridge.write_file(REMOTE_BUNDLE_FILENAME, bundle_bytes)
                try:
                    bridge.write_file(REMOTE_META_FILENAME, meta_bytes)
                except Exception as exc:
                    rollback_errors: list[str] = []
                    try:
                        if previous_bundle_bytes is None:
                            bridge.delete_file(REMOTE_BUNDLE_FILENAME)
                        else:
                            bridge.write_file(REMOTE_BUNDLE_FILENAME, previous_bundle_bytes)
                    except Exception as restore_bundle_error:
                        rollback_errors.append(f"restore bundle failed: {restore_bundle_error}")
                    try:
                        if previous_meta_bytes is None:
                            bridge.delete_file(REMOTE_META_FILENAME)
                        else:
                            bridge.write_file(REMOTE_META_FILENAME, previous_meta_bytes)
                    except Exception as restore_meta_error:
                        rollback_errors.append(f"restore meta failed: {restore_meta_error}")
                    if rollback_errors:
                        raise RuntimeError(
                            "failed to write remote cloudsave meta and rollback previous remote bundle state: "
                            + "; ".join(rollback_errors)
                        ) from exc
                    raise
            finally:
                if batch_started and callable(end_batch):
                    try:
                        # Steam's batch must be closed once opened, even if the
                        # first write failed or rollback writes happened inside it.
                        end_batch()
                    except Exception as exc:
                        logger.warning("steam_cloud_bundle: failed to end remote write batch: %s", exc)
            return {
                "success": True,
                "action": "uploaded",
                "meta": bundle_payload["meta"],
                "bundle_size": len(bundle_bytes),
            }
