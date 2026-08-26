"""One-time migration from the legacy mixed code/state plugin layout."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import shutil
import stat
import tomllib
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from plugin.settings import (
    PLUGIN_EXEC_STATE_ROOT_COLLISION,
    PluginExecStateRootCollisionError,
    ensure_plugin_exec_state_roots_separated,
    get_builtin_plugin_config_root,
    get_plugin_state_root,
    get_user_package_profiles_root,
    get_user_plugin_exec_root,
)

LAYOUT_LEDGER_FILENAME = ".neko-plugin-layout-v1.json"
LAYOUT_LEDGER_VERSION = 1
_STAGING_PREFIX = ".neko-layout-v1-"
_STAGING_SUFFIX = ".staging"
_INSTALL_STAGING_PREFIX = ".neko_staging_"
_SOURCE_SWITCH_STAGING_PREFIX = ".neko_override_staging_"
_SOURCE_SWITCH_UNPACK_PREFIX = ".neko_override_unpack_"
_STANDARD_STATE_DIRECTORY_NAMES = frozenset({"config", "data", "cache"})
_PLUGIN_ID_RE = re.compile(r"^[A-Za-z0-9_-]+$")
_UUID_HEX_PATTERN = r"[0-9a-f]{32}"
_LAYOUT_STAGING_RE = re.compile(
    rf"{re.escape(_STAGING_PREFIX)}[A-Za-z0-9_-]+-{_UUID_HEX_PATTERN}"
    rf"{re.escape(_STAGING_SUFFIX)}"
)
_INSTALL_STAGING_RE = re.compile(
    rf"{re.escape(_INSTALL_STAGING_PREFIX)}{_UUID_HEX_PATTERN}"
)
_SOURCE_SWITCH_STAGING_RE = re.compile(
    rf"{re.escape(_SOURCE_SWITCH_STAGING_PREFIX)}{_UUID_HEX_PATTERN}"
)
_SOURCE_SWITCH_UNPACK_RE = re.compile(
    rf"{re.escape(_SOURCE_SWITCH_UNPACK_PREFIX)}{_UUID_HEX_PATTERN}"
)


@dataclass(frozen=True, slots=True)
class LayoutMigrationIssue:
    code: str
    message: str
    plugin_id: str = ""
    path: str = ""


@dataclass(frozen=True, slots=True)
class LayoutMigrationResult:
    migrated: tuple[str, ...] = ()
    skipped: tuple[str, ...] = ()
    blocked: tuple[LayoutMigrationIssue, ...] = ()
    cleaned_staging: tuple[str, ...] = ()


class _LayoutMigrationBlocked(Exception):
    def __init__(self, code: str, message: str) -> None:
        self.code = code
        self.message = message
        super().__init__(message)


def _is_same_or_within(path: Path, root: Path) -> bool:
    try:
        resolved = path.resolve(strict=False)
        resolved_root = root.resolve(strict=False)
        return resolved == resolved_root or resolved.is_relative_to(resolved_root)
    except (OSError, RuntimeError, ValueError):
        return False


def _is_link_or_junction(path: Path) -> bool:
    try:
        metadata = path.lstat()
    except OSError:
        return True
    file_attributes = getattr(metadata, "st_file_attributes", 0)
    reparse_attribute = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
    return stat.S_ISLNK(metadata.st_mode) or bool(file_attributes & reparse_attribute)


def _ensure_tree_has_no_links(source: Path) -> None:
    if _is_link_or_junction(source):
        raise _LayoutMigrationBlocked(
            "PLUGIN_LAYOUT_MIGRATION_SYMLINK",
            f"legacy plugin path is a symbolic link or junction: {source}",
        )
    for current_root, directory_names, file_names in os.walk(source, followlinks=False):
        current = Path(current_root)
        for name in (*directory_names, *file_names):
            child = current / name
            if _is_link_or_junction(child):
                raise _LayoutMigrationBlocked(
                    "PLUGIN_LAYOUT_MIGRATION_SYMLINK",
                    f"legacy plugin contains a symbolic link or junction: {child}",
                )


def _read_manifest(plugin_dir: Path) -> tuple[str, str, bytes]:
    manifest_path = plugin_dir / "plugin.toml"
    try:
        manifest_bytes = manifest_path.read_bytes()
        manifest = tomllib.loads(manifest_bytes.decode("utf-8"))
    except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError) as exc:
        raise _LayoutMigrationBlocked(
            "PLUGIN_LAYOUT_MIGRATION_MANIFEST_INVALID",
            f"failed to read legacy plugin manifest {manifest_path}: {exc}",
        ) from exc

    plugin = manifest.get("plugin")
    if not isinstance(plugin, dict):
        raise _LayoutMigrationBlocked(
            "PLUGIN_LAYOUT_MIGRATION_MANIFEST_INVALID",
            f"legacy manifest is missing [plugin]: {manifest_path}",
        )
    plugin_id = plugin.get("id")
    entry = plugin.get("entry")
    if not isinstance(plugin_id, str) or not _PLUGIN_ID_RE.fullmatch(plugin_id):
        raise _LayoutMigrationBlocked(
            "PLUGIN_LAYOUT_MIGRATION_MANIFEST_INVALID",
            f"legacy manifest has an invalid [plugin].id: {manifest_path}",
        )
    if not isinstance(entry, str) or ":" not in entry:
        raise _LayoutMigrationBlocked(
            "PLUGIN_LAYOUT_MIGRATION_ENTRY_INVALID",
            f"legacy manifest has an invalid [plugin].entry: {manifest_path}",
        )
    return plugin_id, entry, manifest_bytes


def _entry_module_candidates(plugin_dir: Path, plugin_id: str, entry: str) -> tuple[Path, Path]:
    module_name, class_name = entry.split(":", 1)
    if not class_name.strip():
        raise _LayoutMigrationBlocked(
            "PLUGIN_LAYOUT_MIGRATION_ENTRY_INVALID",
            f"plugin entry is missing its class name: {entry!r}",
        )

    parts = module_name.split(".")
    if not parts or any(not part for part in parts):
        raise _LayoutMigrationBlocked(
            "PLUGIN_LAYOUT_MIGRATION_ENTRY_INVALID",
            f"plugin entry has an invalid module path: {entry!r}",
        )

    relative_parts: list[str]
    for prefix in (("plugin", "plugins", plugin_id), ("plugins", plugin_id)):
        prefix_list = list(prefix)
        if parts[: len(prefix_list)] == prefix_list:
            relative_parts = parts[len(prefix_list) :]
            break
    else:
        raise _LayoutMigrationBlocked(
            "PLUGIN_LAYOUT_MIGRATION_ENTRY_INVALID",
            "legacy plugin entry is not supported by isolated loading: "
            f"{entry!r}",
        )

    if any(not part.isidentifier() for part in relative_parts):
        raise _LayoutMigrationBlocked(
            "PLUGIN_LAYOUT_MIGRATION_ENTRY_INVALID",
            f"plugin entry has an invalid module path: {entry!r}",
        )

    if relative_parts:
        base = plugin_dir.joinpath(*relative_parts)
        return base.with_suffix(".py"), base / "__init__.py"
    return plugin_dir / "__init__.py", plugin_dir / "__init__.py"


def _validate_plugin_tree(
    plugin_dir: Path,
    expected_id: str,
    *,
    require_directory_name: bool = True,
) -> tuple[str, bytes]:
    plugin_id, entry, manifest_bytes = _read_manifest(plugin_dir)
    if plugin_id != expected_id or (require_directory_name and plugin_dir.name != expected_id):
        raise _LayoutMigrationBlocked(
            "PLUGIN_LAYOUT_MIGRATION_ID_MISMATCH",
            f"manifest id {plugin_id!r} does not match directory {plugin_dir.name!r}",
        )
    module_file, package_init = _entry_module_candidates(plugin_dir, plugin_id, entry)
    if not module_file.is_file() and not package_init.is_file():
        raise _LayoutMigrationBlocked(
            "PLUGIN_LAYOUT_MIGRATION_ENTRY_INVALID",
            f"plugin entry module does not exist inside {plugin_dir}: {entry!r}",
        )
    return hashlib.sha256(manifest_bytes).hexdigest(), manifest_bytes


def _load_ledger(ledger_path: Path) -> dict[str, Any]:
    if not ledger_path.exists():
        return {"version": LAYOUT_LEDGER_VERSION, "entries": []}
    try:
        payload = json.loads(ledger_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise _LayoutMigrationBlocked(
            "PLUGIN_LAYOUT_MIGRATION_LEDGER_INVALID",
            f"layout migration ledger cannot be read: {ledger_path}: {exc}",
        ) from exc
    if (
        not isinstance(payload, dict)
        or payload.get("version") != LAYOUT_LEDGER_VERSION
        or not isinstance(payload.get("entries"), list)
    ):
        raise _LayoutMigrationBlocked(
            "PLUGIN_LAYOUT_MIGRATION_LEDGER_INVALID",
            f"layout migration ledger has an unsupported shape: {ledger_path}",
        )
    return payload


def _atomic_write_ledger(ledger_path: Path, payload: dict[str, Any]) -> None:
    ledger_path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = ledger_path.with_name(f"{ledger_path.name}.{uuid.uuid4().hex}.tmp")
    encoded = (json.dumps(payload, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    try:
        with temp_path.open("xb") as handle:
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_path, ledger_path)
    finally:
        try:
            temp_path.unlink(missing_ok=True)
        except OSError:
            pass


def _cleanup_staging(
    root: Path,
    *,
    include_layout_migration: bool,
) -> tuple[list[str], list[LayoutMigrationIssue]]:
    cleaned: list[str] = []
    blocked: list[LayoutMigrationIssue] = []

    def _record_cleanup_failure(path: Path, exc: OSError) -> None:
        blocked.append(
            LayoutMigrationIssue(
                code="PLUGIN_LAYOUT_MIGRATION_STAGING_CLEANUP_FAILED",
                message=f"failed to inspect or clean staging path: {exc}",
                path=str(path),
            )
        )

    try:
        if not root.is_dir():
            return cleaned, blocked
        children = tuple(root.iterdir())
    except OSError as exc:
        _record_cleanup_failure(root, exc)
        return cleaned, blocked

    for child in children:
        is_layout_staging = include_layout_migration and bool(
            _LAYOUT_STAGING_RE.fullmatch(child.name)
        )
        is_install_staging = bool(_INSTALL_STAGING_RE.fullmatch(child.name))
        is_source_switch_staging = bool(
            _SOURCE_SWITCH_STAGING_RE.fullmatch(child.name)
        )
        is_source_switch_unpack = bool(
            _SOURCE_SWITCH_UNPACK_RE.fullmatch(child.name)
        )
        if not any(
            (
                is_layout_staging,
                is_install_staging,
                is_source_switch_staging,
                is_source_switch_unpack,
            )
        ):
            continue
        try:
            if _is_link_or_junction(child):
                blocked.append(
                    LayoutMigrationIssue(
                        code="PLUGIN_LAYOUT_MIGRATION_STAGING_SYMLINK",
                        message="refusing to clean linked migration staging path",
                        path=str(child),
                    )
                )
                continue
            # Every transaction creates a directory. An exact-name regular file
            # was not produced by these flows and must be left untouched.
            if not child.is_dir():
                continue
            shutil.rmtree(child)
        except OSError as exc:
            _record_cleanup_failure(child, exc)
            continue
        cleaned.append(str(child))
    return cleaned, blocked


def _copy_legacy_plugin_tree(source: Path, staging: Path) -> None:
    """Copy package files while keeping standard persistent state in place.

    Only root-level SDK state directories are excluded. Everything else,
    including package static assets, is copied. A plugin which writes state to
    ``__file__/data`` uses the unsupported legacy pattern called out by the
    migration contract; safety takes precedence over copying that subtree.
    """

    resolved_source = source.resolve(strict=False)

    def _ignore(current: str, names: list[str]) -> list[str]:
        current_path = Path(current)
        if current_path.resolve(strict=False) != resolved_source:
            return []
        ignored: list[str] = []
        for name in names:
            candidate = current_path / name
            if not candidate.is_dir():
                continue
            folded_name = name.casefold()
            if folded_name not in _STANDARD_STATE_DIRECTORY_NAMES:
                continue
            if name == folded_name:
                ignored.append(name)
                continue
            try:
                if os.path.samefile(candidate, current_path / folded_name):
                    ignored.append(name)
            except OSError:
                continue
        return ignored

    shutil.copytree(source, staging, symlinks=False, ignore=_ignore)


def _migrate_legacy_plugin_layout_sync(
    *,
    state_root: Path,
    exec_root: Path,
    ledger_path: Path,
    builtin_root: Path,
    profiles_root: Path | None = None,
) -> LayoutMigrationResult:
    state_root = state_root.resolve(strict=False)
    exec_root = exec_root.resolve(strict=False)
    ledger_path = ledger_path.resolve(strict=False)
    builtin_root = builtin_root.resolve(strict=False)
    resolved_profiles_root = (
        profiles_root.resolve(strict=False) if profiles_root is not None else None
    )
    separation_pairs = [
        (exec_root, state_root),
        (exec_root, builtin_root),
    ]
    if resolved_profiles_root is not None:
        separation_pairs.extend(
            (
                (resolved_profiles_root, state_root),
                (resolved_profiles_root, exec_root),
                (resolved_profiles_root, builtin_root),
            )
        )
    for writable_root, protected_root in separation_pairs:
        try:
            ensure_plugin_exec_state_roots_separated(
                exec_root=writable_root,
                state_root=protected_root,
            )
        except PluginExecStateRootCollisionError as exc:
            return LayoutMigrationResult(
                blocked=(
                    LayoutMigrationIssue(
                        code=PLUGIN_EXEC_STATE_ROOT_COLLISION,
                        message=str(exc),
                        path=str(writable_root),
                    ),
                )
            )

    exec_root.mkdir(parents=True, exist_ok=True)
    cleaned, blocked = _cleanup_staging(
        exec_root,
        include_layout_migration=True,
    )
    if resolved_profiles_root is not None:
        if resolved_profiles_root != exec_root:
            profile_cleaned, profile_blocked = _cleanup_staging(
                resolved_profiles_root,
                include_layout_migration=False,
            )
            cleaned.extend(profile_cleaned)
            blocked.extend(profile_blocked)
    try:
        ledger = _load_ledger(ledger_path)
    except _LayoutMigrationBlocked as exc:
        return LayoutMigrationResult(
            blocked=tuple(blocked)
            + (LayoutMigrationIssue(code=exc.code, message=exc.message, path=str(ledger_path)),),
            cleaned_staging=tuple(cleaned),
        )

    ledger_entries = ledger["entries"]
    migrated_ids: set[str] = set()
    for item in ledger_entries:
        if not isinstance(item, dict):
            continue
        plugin_id = item.get("plugin_id")
        recorded_new_path = item.get("new_path")
        if not isinstance(plugin_id, str) or not isinstance(recorded_new_path, str):
            continue
        try:
            expected_destination = (exec_root / plugin_id).resolve(strict=False)
            recorded_destination = Path(recorded_new_path).resolve(strict=False)
        except (OSError, RuntimeError, ValueError):
            continue
        if recorded_destination == expected_destination:
            migrated_ids.add(plugin_id)
    migrated: list[str] = []
    skipped: list[str] = []

    try:
        if not state_root.is_dir():
            return LayoutMigrationResult(
                migrated=(),
                skipped=(),
                blocked=tuple(blocked),
                cleaned_staging=tuple(cleaned),
            )
        legacy_entries = sorted(
            state_root.iterdir(),
            key=lambda item: item.name.casefold(),
        )
    except OSError as exc:
        blocked.append(
            LayoutMigrationIssue(
                code="PLUGIN_LAYOUT_MIGRATION_IO_FAILED",
                message=f"failed to list the legacy plugin state root: {exc}",
                path=str(state_root),
            )
        )
        return LayoutMigrationResult(
            migrated=(),
            skipped=(),
            blocked=tuple(blocked),
            cleaned_staging=tuple(cleaned),
        )

    for source in legacy_entries:
        manifest_path = source / "plugin.toml"
        if not source.is_dir() or not manifest_path.is_file():
            continue
        directory_id = source.name
        if directory_id in migrated_ids:
            # The ledger is authoritative even if the destination was later
            # uninstalled; this prevents the legacy copy from resurrecting.
            skipped.append(directory_id)
            continue
        destination = exec_root / directory_id
        staging = exec_root / f"{_STAGING_PREFIX}{directory_id}-{uuid.uuid4().hex}{_STAGING_SUFFIX}"
        promoted = False
        try:
            if not _is_same_or_within(source, state_root) or not _is_same_or_within(destination, exec_root):
                raise _LayoutMigrationBlocked(
                    "PLUGIN_LAYOUT_MIGRATION_PATH_OUTSIDE_ROOT",
                    "legacy source or migration destination escapes its configured root",
                )
            _ensure_tree_has_no_links(source)
            source_hash, _ = _validate_plugin_tree(source, directory_id)
            builtin_manifest = builtin_root / directory_id / "plugin.toml"
            if builtin_manifest.is_file():
                raise _LayoutMigrationBlocked(
                    "PLUGIN_LAYOUT_MIGRATION_BUILTIN_CONFLICT",
                    (
                        "legacy plugin collides with an immutable builtin; "
                        "automatic migration cannot create an unverified override"
                    ),
                )
            if destination.exists():
                raise _LayoutMigrationBlocked(
                    "PLUGIN_LAYOUT_MIGRATION_DESTINATION_EXISTS",
                    f"migration destination already exists: {destination}",
                )
            _copy_legacy_plugin_tree(source, staging)
            staging_hash, _ = _validate_plugin_tree(
                staging,
                directory_id,
                require_directory_name=False,
            )
            if staging_hash != source_hash:
                raise _LayoutMigrationBlocked(
                    "PLUGIN_LAYOUT_MIGRATION_MANIFEST_CHANGED",
                    f"manifest changed while copying legacy plugin {directory_id}",
                )
            os.replace(staging, destination)
            promoted = True
            entry = {
                "plugin_id": directory_id,
                "old_path": str(source),
                "new_path": str(destination),
                "manifest_sha256": source_hash,
                "migrated_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
            }
            ledger_entries.append(entry)
            try:
                _atomic_write_ledger(ledger_path, ledger)
            except Exception as exc:
                ledger_entries.pop()
                raise _LayoutMigrationBlocked(
                    "PLUGIN_LAYOUT_MIGRATION_LEDGER_WRITE_FAILED",
                    f"failed to persist migration ledger for {directory_id}: {exc}",
                ) from exc
            migrated_ids.add(directory_id)
            migrated.append(directory_id)
        except _LayoutMigrationBlocked as exc:
            if promoted:
                try:
                    shutil.rmtree(destination)
                except OSError:
                    pass
            blocked.append(
                LayoutMigrationIssue(
                    code=exc.code,
                    message=exc.message,
                    plugin_id=directory_id,
                    path=str(source),
                )
            )
        except OSError as exc:
            if promoted:
                try:
                    shutil.rmtree(destination)
                except OSError:
                    pass
            blocked.append(
                LayoutMigrationIssue(
                    code="PLUGIN_LAYOUT_MIGRATION_IO_FAILED",
                    message=str(exc),
                    plugin_id=directory_id,
                    path=str(source),
                )
            )
        finally:
            if staging.exists() and not _is_link_or_junction(staging):
                try:
                    shutil.rmtree(staging)
                except OSError:
                    pass

    return LayoutMigrationResult(
        migrated=tuple(migrated),
        skipped=tuple(skipped),
        blocked=tuple(blocked),
        cleaned_staging=tuple(cleaned),
    )


async def migrate_legacy_plugin_layout(
    *,
    state_root: Path | None = None,
    exec_root: Path | None = None,
    ledger_path: Path | None = None,
    profiles_root: Path | None = None,
    builtin_root: Path | None = None,
) -> LayoutMigrationResult:
    """Migrate legacy user plugin code without blocking the event loop."""

    resolved_state = state_root or get_plugin_state_root()
    resolved_exec = exec_root or get_user_plugin_exec_root()
    resolved_ledger = ledger_path or (resolved_state.parent / LAYOUT_LEDGER_FILENAME)
    resolved_builtin = builtin_root or get_builtin_plugin_config_root()
    resolved_profiles = profiles_root
    if resolved_profiles is None and state_root is None and exec_root is None:
        resolved_profiles = get_user_package_profiles_root()
    return await asyncio.to_thread(
        _migrate_legacy_plugin_layout_sync,
        state_root=resolved_state,
        exec_root=resolved_exec,
        ledger_path=resolved_ledger,
        profiles_root=resolved_profiles,
        builtin_root=resolved_builtin,
    )


__all__ = [
    "LAYOUT_LEDGER_FILENAME",
    "LayoutMigrationIssue",
    "LayoutMigrationResult",
    "migrate_legacy_plugin_layout",
]
