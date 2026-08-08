"""Snapshot engine for the bundled data backup plugin."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import sqlite3
import stat
import threading
import uuid
from contextlib import closing
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Iterable


BACKUP_GROUPS: dict[str, tuple[str, ...]] = {
    "core": ("config", "character_cards", "memory"),
    "assets": ("card_faces", "live2d", "vrm", "mmd", "pngtuber", "workshop"),
}
_SNAPSHOT_ID_RE = re.compile(r"^\d{8}T\d{12}Z-[0-9a-f]{8}$")
_SNAPSHOT_RETENTION = 3


class BackupError(RuntimeError):
    """Raised when a snapshot operation cannot be completed safely."""


class BackupEngine:
    def __init__(self, data_root: Path, backup_root: Path) -> None:
        self.data_root = data_root.expanduser().resolve(strict=False)
        self.backup_root = backup_root.expanduser().resolve(strict=False)
        self.retention = _SNAPSHOT_RETENTION
        self._lock = threading.RLock()
        self._last_snapshot_time: datetime | None = None

        if self.backup_root == self.data_root:
            raise BackupError("backup directory cannot be the data root")
        for paths in BACKUP_GROUPS.values():
            for relative in paths:
                source_root = (self.data_root / relative).resolve(strict=False)
                if (
                    self.backup_root == source_root
                    or source_root in self.backup_root.parents
                ):
                    raise BackupError(
                        "backup directory cannot be inside a backed-up data directory"
                    )
        self.backup_root.mkdir(parents=True, exist_ok=True)

    def create_snapshot(
        self, group: str, *, protected: Iterable[str] = ()
    ) -> dict[str, Any]:
        with self._lock:
            paths = self._group_paths(group)
            present_paths = [path for path in paths if (self.data_root / path).exists()]
            if not present_paths:
                raise BackupError(f"backup group has no existing data: {group}")

            snapshot_id = self._new_snapshot_id()
            group_root = self.backup_root / group
            stage = group_root / f".tmp-{snapshot_id}"
            destination = group_root / snapshot_id
            files_root = stage / "files"
            previous = self._latest_manifest(group)
            previous_files = previous.get("files", {}) if previous else {}
            previous_root = (
                Path(previous["_snapshot_path"]) / "files" if previous else None
            )
            manifest_files: dict[str, dict[str, Any]] = {}
            manifest_directories: set[str] = set()
            warnings: list[str] = []
            total_bytes = 0

            group_root.mkdir(parents=True, exist_ok=True)
            stage.mkdir(parents=True)
            try:
                for relative_root in present_paths:
                    if (self.data_root / relative_root).is_symlink():
                        raise BackupError(
                            f"symbolic-link backup roots are not allowed: {relative_root}"
                        )
                    source_root = self._safe_source(relative_root)
                    for source_directory in self._iter_directories(source_root):
                        relative_directory = source_directory.relative_to(self.data_root)
                        manifest_directories.add(relative_directory.as_posix())
                        (files_root / relative_directory).mkdir(parents=True, exist_ok=True)
                    for source in self._iter_files(source_root):
                        relative = source.relative_to(self.data_root)
                        key = relative.as_posix()
                        target = files_root / relative
                        target.parent.mkdir(parents=True, exist_ok=True)

                        previous_meta = previous_files.get(key)
                        previous_file = (
                            previous_root / relative if previous_root else None
                        )
                        digest, size, mode, warning = self._copy_snapshot_file(
                            source,
                            target,
                            previous_file=previous_file,
                            previous_meta=previous_meta,
                        )

                        manifest_files[key] = {
                            "sha256": digest,
                            "size": size,
                            "mode": mode,
                        }
                        if warning:
                            warnings.append(warning)
                        total_bytes += size

                created_at = datetime.now(UTC).isoformat()
                manifest = {
                    "version": 2,
                    "id": snapshot_id,
                    "group": group,
                    "created_at": created_at,
                    "paths": list(paths),
                    "present_paths": present_paths,
                    "file_count": len(manifest_files),
                    "total_bytes": total_bytes,
                    "directories": sorted(manifest_directories),
                    "files": manifest_files,
                }
                (stage / "manifest.json").write_text(
                    json.dumps(manifest, ensure_ascii=False, indent=2),
                    encoding="utf-8",
                )
                stage.replace(destination)
                try:
                    self._make_read_only(destination / "manifest.json")
                except OSError as exc:
                    warnings.append(f"failed to protect manifest {snapshot_id}: {exc}")
            except Exception:
                self._remove_tree(stage, ignore_errors=True)
                raise

            warnings.extend(self._prune(group, protected={snapshot_id, *protected}))
            result = self._public_manifest(manifest)
            result["warnings"] = warnings
            return result

    def list_snapshots(self, group: str) -> list[dict[str, Any]]:
        with self._lock:
            self._group_paths(group)
            snapshots: list[dict[str, Any]] = []
            for path in self._snapshot_dirs(group):
                try:
                    snapshots.append(self._public_manifest(self._load_manifest(path)))
                except (BackupError, OSError, json.JSONDecodeError):
                    continue
            return sorted(snapshots, key=lambda item: item["id"], reverse=True)

    def restore_snapshot(self, group: str, snapshot_id: str) -> dict[str, Any]:
        with self._lock:
            paths = self._group_paths(group)
            snapshot = self._snapshot_path(group, snapshot_id)
            manifest = self._load_manifest(snapshot)
            if (
                manifest.get("group") != group
                or tuple(manifest.get("paths", ())) != paths
            ):
                raise BackupError("snapshot group metadata does not match")
            self._verify_snapshot(snapshot, manifest)

            current_roots = [self.data_root / relative for relative in paths]
            if any(path.is_symlink() for path in current_roots):
                raise BackupError(
                    "symbolic-link backup roots must be removed before restore"
                )
            safety = (
                self.create_snapshot(group, protected={snapshot_id})
                if any(path.exists() for path in current_roots)
                else None
            )
            safety_path = (
                self._snapshot_path(group, safety["id"]) if safety is not None else None
            )
            safety_manifest = (
                self._load_manifest(safety_path) if safety_path is not None else {}
            )
            warnings = list(safety.get("warnings", ())) if safety is not None else []
            restore_token = uuid.uuid4().hex
            stage = self.data_root / f".data-backup-restore-{restore_token}"
            old_root = self.data_root / f".data-backup-old-{restore_token}"
            present_paths = set(manifest.get("present_paths", ()))
            safety_present_paths = set(safety_manifest.get("present_paths", ()))
            moved_old: list[str] = []
            installed: list[str] = []
            restored_in_place: list[str] = []

            try:
                for relative_root in paths:
                    if relative_root in present_paths:
                        source = snapshot / "files" / relative_root
                        target = stage / relative_root
                        if source.exists():
                            shutil.copytree(source, target, copy_function=shutil.copy2)
                        else:
                            target.mkdir(parents=True, exist_ok=True)
                self._restore_staged_modes(stage, manifest)

                for relative_root in paths:
                    current = self.data_root / relative_root
                    old = old_root / relative_root
                    replacement = stage / relative_root
                    if current.exists():
                        old.parent.mkdir(parents=True, exist_ok=True)
                        try:
                            current.replace(old)
                            moved_old.append(relative_root)
                        except PermissionError:
                            restored_in_place.append(relative_root)
                            self._restore_root_in_place(
                                relative_root,
                                replacement if relative_root in present_paths else None,
                            )
                            continue
                    if relative_root in present_paths:
                        current.parent.mkdir(parents=True, exist_ok=True)
                        replacement.replace(current)
                        installed.append(relative_root)
            except Exception as exc:
                rollback_errors: list[str] = []
                for relative_root in reversed(installed):
                    current = self.data_root / relative_root
                    try:
                        self._remove_path(current)
                    except Exception as rollback_exc:
                        rollback_errors.append(
                            f"remove installed path {current}: {rollback_exc}"
                        )
                for relative_root in reversed(moved_old):
                    old = old_root / relative_root
                    current = self.data_root / relative_root
                    if old.exists():
                        try:
                            current.parent.mkdir(parents=True, exist_ok=True)
                            old.replace(current)
                        except Exception as rollback_exc:
                            rollback_errors.append(
                                f"restore {old} to {current}: {rollback_exc}"
                            )
                for relative_root in reversed(restored_in_place):
                    try:
                        if safety_path is None:
                            raise BackupError("safety snapshot is unavailable")
                        self._restore_root_in_place(
                            relative_root,
                            safety_path / "files" / relative_root
                            if relative_root in safety_present_paths
                            else None,
                        )
                    except Exception as rollback_exc:
                        rollback_errors.append(
                            f"restore in place {relative_root}: {rollback_exc}"
                        )
                if rollback_errors:
                    raise BackupError(
                        "restore failed and rollback also failed: "
                        + "; ".join(rollback_errors)
                    ) from exc
                raise BackupError(f"restore failed and was rolled back: {exc}") from exc
            finally:
                self._remove_tree(stage, ignore_errors=True)

            if old_root.exists():
                try:
                    self._remove_tree(old_root)
                except OSError as exc:
                    warnings.append(f"failed to remove previous data staging area: {exc}")
            protected = {snapshot_id}
            if safety is not None:
                protected.add(safety["id"])
            warnings.extend(self._prune(group, protected=protected))
            return {
                "restored": snapshot_id,
                "safety_snapshot": safety["id"] if safety is not None else None,
                "restart_required": True,
                "warnings": warnings,
            }

    def delete_snapshot(self, group: str, snapshot_id: str) -> dict[str, str]:
        with self._lock:
            snapshot = self._snapshot_path(group, snapshot_id)
            if not snapshot.is_dir():
                raise BackupError("snapshot not found")
            self._remove_tree(snapshot)
            return {"deleted": snapshot_id}

    def status(self) -> dict[str, Any]:
        with self._lock:
            groups = {
                group: {
                    "paths": list(paths),
                    "snapshots": snapshots,
                    "retention_exceeded": len(snapshots) > self.retention,
                }
                for group, paths in BACKUP_GROUPS.items()
                for snapshots in (self.list_snapshots(group),)
            }
            warnings = [
                f"{group} has {len(details['snapshots'])} snapshots; retention is {self.retention}"
                for group, details in groups.items()
                if details["retention_exceeded"]
            ]
            return {
                "data_root": str(self.data_root),
                "backup_root": str(self.backup_root),
                "retention": self.retention,
                "groups": groups,
                "warnings": warnings,
            }

    def _group_paths(self, group: str) -> tuple[str, ...]:
        try:
            return BACKUP_GROUPS[group]
        except KeyError as exc:
            raise BackupError(f"unknown backup group: {group}") from exc

    def _safe_source(self, relative: str) -> Path:
        candidate = (self.data_root / relative).resolve(strict=False)
        if candidate == self.data_root or self.data_root not in candidate.parents:
            raise BackupError(f"path escapes data root: {relative}")
        return candidate

    @staticmethod
    def _iter_directories(root: Path) -> Iterable[Path]:
        for directory, dirnames, _filenames in os.walk(root, followlinks=False):
            base = Path(directory)
            dirnames[:] = [name for name in dirnames if not (base / name).is_symlink()]
            yield base

    @staticmethod
    def _iter_files(root: Path) -> Iterable[Path]:
        for directory, dirnames, filenames in os.walk(root, followlinks=False):
            base = Path(directory)
            dirnames[:] = [name for name in dirnames if not (base / name).is_symlink()]
            for filename in filenames:
                path = base / filename
                if BackupEngine._is_sqlite_sidecar(path):
                    continue
                if path.is_file() and not path.is_symlink():
                    yield path

    def _copy_snapshot_file(
        self,
        source: Path,
        target: Path,
        *,
        previous_file: Path | None,
        previous_meta: Any,
    ) -> tuple[str, int, int, str | None]:
        source_mode = stat.S_IMODE(source.stat().st_mode)
        warning: str | None = None
        if self._is_sqlite_database(source):
            self._backup_sqlite(source, target)
            digest = self._sha256(target)
        else:
            digest = self._sha256(source)
            if self._can_link_previous(previous_file, previous_meta, digest):
                try:
                    os.link(previous_file, target)
                    return digest, target.stat().st_size, source_mode, None
                except OSError:
                    pass
            shutil.copy2(source, target)
            digest = self._sha256(target)
        try:
            self._make_read_only(target)
        except OSError as exc:
            warning = f"snapshot file could not be made read-only: {source}: {exc}"
        return digest, target.stat().st_size, source_mode, warning

    def _can_link_previous(
        self, previous_file: Path | None, previous_meta: Any, digest: str
    ) -> bool:
        return bool(
            isinstance(previous_meta, dict)
            and previous_meta.get("sha256") == digest
            and previous_file is not None
            and previous_file.is_file()
            and not previous_file.is_symlink()
            and not self._has_write_bits(previous_file)
            and self._sha256(previous_file) == digest
        )

    @staticmethod
    def _has_write_bits(path: Path) -> bool:
        return bool(
            stat.S_IMODE(path.stat().st_mode)
            & (stat.S_IWUSR | stat.S_IWGRP | stat.S_IWOTH)
        )

    @staticmethod
    def _make_read_only(path: Path) -> None:
        mode = stat.S_IMODE(path.stat().st_mode)
        os.chmod(path, mode & ~(stat.S_IWUSR | stat.S_IWGRP | stat.S_IWOTH))

    @staticmethod
    def _is_sqlite_database(path: Path) -> bool:
        try:
            with path.open("rb") as stream:
                return stream.read(16) == b"SQLite format 3\x00"
        except OSError:
            return False

    @classmethod
    def _is_sqlite_sidecar(cls, path: Path) -> bool:
        for suffix in ("-wal", "-shm"):
            if path.name.endswith(suffix):
                return cls._is_sqlite_database(
                    path.with_name(path.name[: -len(suffix)])
                )
        return False

    @staticmethod
    def _backup_sqlite(source: Path, target: Path) -> None:
        source_uri = f"{source.resolve(strict=True).as_uri()}?mode=ro"
        try:
            with closing(
                sqlite3.connect(source_uri, uri=True, timeout=10)
            ) as source_db:
                with closing(sqlite3.connect(target, timeout=10)) as target_db:
                    source_db.backup(target_db)
        except sqlite3.Error as exc:
            raise BackupError(f"SQLite backup failed: {source}") from exc

    def _restore_root_in_place(
        self, relative_root: str, replacement: Path | None
    ) -> None:
        current = self._safe_source(relative_root)
        expected: set[str] = set()

        if replacement is not None:
            current.mkdir(parents=True, exist_ok=True)
            for source in self._iter_files(replacement):
                relative = source.relative_to(replacement)
                expected.add(relative.as_posix())
                target = current / relative
                resolved_target = target.resolve(strict=False)
                if self.data_root not in resolved_target.parents:
                    raise BackupError(f"restore target escapes data root: {relative}")
                target.parent.mkdir(parents=True, exist_ok=True)
                if self._is_sqlite_database(source) and self._is_sqlite_database(
                    target
                ):
                    self._restore_sqlite(source, target)
                else:
                    self._replace_file(source, target)

        if current.exists():
            self._remove_unexpected_files(current, expected)

    @staticmethod
    def _restore_staged_modes(stage: Path, manifest: dict[str, Any]) -> None:
        files = manifest.get("files", {})
        for relative, metadata in files.items():
            target = stage / Path(relative)
            mode = metadata.get("mode") if isinstance(metadata, dict) else None
            if isinstance(mode, int) and not isinstance(mode, bool):
                os.chmod(target, mode)
            else:
                current_mode = stat.S_IMODE(target.stat().st_mode)
                os.chmod(target, current_mode | stat.S_IWRITE)

    @staticmethod
    def _replace_file(source: Path, target: Path) -> None:
        temporary = target.with_name(f".{target.name}.restore-{uuid.uuid4().hex}.tmp")
        try:
            shutil.copy2(source, temporary)
            os.replace(temporary, target)
        finally:
            temporary.unlink(missing_ok=True)

    @staticmethod
    def _restore_sqlite(source: Path, target: Path) -> None:
        source_uri = f"{source.resolve(strict=True).as_uri()}?mode=ro"
        try:
            with closing(
                sqlite3.connect(source_uri, uri=True, timeout=10)
            ) as source_db:
                with closing(sqlite3.connect(target, timeout=10)) as target_db:
                    source_db.backup(target_db)
            os.chmod(target, stat.S_IMODE(source.stat().st_mode))
        except sqlite3.Error as exc:
            raise BackupError(f"SQLite restore failed: {target}") from exc

    def _remove_unexpected_files(self, root: Path, expected: set[str]) -> None:
        for directory, dirnames, filenames in os.walk(
            root, topdown=False, followlinks=False
        ):
            base = Path(directory)
            for filename in filenames:
                path = base / filename
                relative = path.relative_to(root).as_posix()
                if relative in expected:
                    continue
                if self._is_sqlite_sidecar(path):
                    main_relative = relative.rsplit("-", 1)[0]
                    if main_relative in expected:
                        continue
                path.unlink()
            for dirname in dirnames:
                path = base / dirname
                if path.is_symlink():
                    path.unlink()
                else:
                    try:
                        path.rmdir()
                    except OSError:
                        pass
        if not expected:
            try:
                root.rmdir()
            except OSError:
                pass

    @staticmethod
    def _sha256(path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()

    def _new_snapshot_id(self) -> str:
        now = datetime.now(UTC)
        if self._last_snapshot_time is not None and now <= self._last_snapshot_time:
            now = self._last_snapshot_time + timedelta(microseconds=1)
        self._last_snapshot_time = now
        stamp = now.strftime("%Y%m%dT%H%M%S%fZ")
        return f"{stamp}-{uuid.uuid4().hex[:8]}"

    def _snapshot_dirs(self, group: str) -> list[Path]:
        group_root = self.backup_root / group
        if not group_root.is_dir():
            return []
        return [
            path
            for path in group_root.iterdir()
            if path.is_dir()
            and not path.is_symlink()
            and _SNAPSHOT_ID_RE.fullmatch(path.name)
        ]

    def _snapshot_path(self, group: str, snapshot_id: str) -> Path:
        self._group_paths(group)
        if not _SNAPSHOT_ID_RE.fullmatch(snapshot_id):
            raise BackupError("invalid snapshot id")
        return self.backup_root / group / snapshot_id

    def _load_manifest(self, snapshot: Path) -> dict[str, Any]:
        if snapshot.is_symlink():
            raise BackupError(f"unsafe snapshot path: {snapshot.name}")
        try:
            payload = json.loads(
                (snapshot / "manifest.json").read_text(encoding="utf-8")
            )
        except (OSError, json.JSONDecodeError) as exc:
            raise BackupError(f"invalid snapshot manifest: {snapshot.name}") from exc
        if not isinstance(payload, dict) or payload.get("id") != snapshot.name:
            raise BackupError(f"invalid snapshot manifest: {snapshot.name}")
        payload["_snapshot_path"] = str(snapshot)
        return payload

    def _latest_manifest(self, group: str) -> dict[str, Any] | None:
        snapshots = sorted(self._snapshot_dirs(group), reverse=True)
        for snapshot in snapshots:
            try:
                return self._load_manifest(snapshot)
            except BackupError:
                continue
        return None

    def _verify_snapshot(self, snapshot: Path, manifest: dict[str, Any]) -> None:
        version = manifest.get("version")
        if version not in (1, 2):
            raise BackupError("snapshot manifest version is unsupported")
        files = manifest.get("files")
        if not isinstance(files, dict):
            raise BackupError("snapshot file manifest is missing")
        directories = manifest.get("directories", [])
        if version >= 2 and (
            not isinstance(directories, list)
            or any(not isinstance(item, str) for item in directories)
        ):
            raise BackupError("snapshot directory manifest is invalid")
        files_root = (snapshot / "files").resolve(strict=False)
        actual_files: set[str] = set()
        actual_directories: set[str] = set()
        if files_root.exists():
            for directory, dirnames, filenames in os.walk(
                files_root, followlinks=False
            ):
                base = Path(directory)
                if any((base / name).is_symlink() for name in dirnames):
                    raise BackupError("snapshot contains a symbolic link")
                actual_directories.update(
                    (base / name).relative_to(files_root).as_posix()
                    for name in dirnames
                )
                for filename in filenames:
                    path = base / filename
                    if path.is_symlink():
                        raise BackupError("snapshot contains a symbolic link")
                    actual_files.add(path.relative_to(files_root).as_posix())
        if actual_files != set(files):
            raise BackupError("snapshot files do not match the manifest")
        if version >= 2 and actual_directories != set(directories):
            raise BackupError("snapshot directories do not match the manifest")
        for relative, metadata in files.items():
            if not isinstance(relative, str) or not isinstance(metadata, dict):
                raise BackupError("snapshot file manifest is invalid")
            source = (snapshot / "files" / Path(relative)).resolve(strict=False)
            if (
                files_root not in source.parents
                or not source.is_file()
                or source.is_symlink()
            ):
                raise BackupError(f"snapshot file is missing or unsafe: {relative}")
            if self._sha256(source) != metadata.get("sha256"):
                raise BackupError(f"snapshot checksum mismatch: {relative}")

    def _prune(self, group: str, *, protected: set[str]) -> list[str]:
        snapshots = sorted(self._snapshot_dirs(group), reverse=True)
        keep = {path.name for path in snapshots if path.name in protected}
        for path in snapshots:
            if len(keep) >= self.retention:
                break
            keep.add(path.name)
        warnings: list[str] = []
        for path in snapshots:
            if path.name not in keep:
                try:
                    self._remove_tree(path)
                except OSError as exc:
                    warnings.append(f"failed to prune snapshot {path.name}: {exc}")
        return warnings

    @staticmethod
    def _remove_tree(path: Path, *, ignore_errors: bool = False) -> None:
        errors: list[OSError] = []

        def retry_with_write_access(function, raw_path, _exc_info) -> None:
            try:
                target = Path(raw_path)
                os.chmod(target, stat.S_IMODE(target.stat().st_mode) | stat.S_IWRITE)
                function(raw_path)
            except OSError as exc:
                errors.append(exc)

        shutil.rmtree(path, onerror=retry_with_write_access)
        if errors and not ignore_errors:
            raise errors[0]

    @classmethod
    def _remove_path(cls, path: Path) -> None:
        if path.is_dir() and not path.is_symlink():
            cls._remove_tree(path)
        elif path.exists() or path.is_symlink():
            path.unlink()

    @staticmethod
    def _public_manifest(manifest: dict[str, Any]) -> dict[str, Any]:
        return {
            key: value
            for key, value in manifest.items()
            if key not in {"files", "_snapshot_path"}
        }
