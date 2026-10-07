"""File-ownership policy for destructive plugin installation operations."""

from __future__ import annotations

from pathlib import Path
from typing import Protocol

from plugin.server.application.install_source.manager import InstallSourceError
from plugin.server.application.install_source.models import LockEntry
from plugin.server.application.install_source.scanner import PluginDirectoryScanner


class InstallSourceOwnershipReader(Protocol):
    """Read-only install-source surface needed by the ownership gate."""

    @property
    def is_degraded(self) -> bool: ...

    def entry_for_directory(
        self,
        directory_path: Path,
        *,
        include_removed: bool = False,
    ) -> LockEntry | None: ...


class UninstallOwnershipError(RuntimeError):
    """A stable, user-explainable refusal from the uninstall ownership gate."""

    def __init__(
        self,
        *,
        code: str,
        message: str,
        status_code: int,
        details: dict[str, object] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code
        self.details = dict(details or {})


def can_neko_uninstall(entry: LockEntry) -> bool:
    """Return whether an active source entry proves N.E.K.O owns its files."""

    return (
        not entry.removed
        and entry.root_id == "user"
        and entry.channel in {"imported", "market"}
    )


def require_uninstall_ownership(
    *,
    manager: InstallSourceOwnershipReader | None,
    runtime_plugin_id: str,
    config_path: Path,
) -> LockEntry:
    """Return the exact managed entry or fail before any destructive action."""

    plugin_dir = config_path.parent
    if manager is None:
        raise _ownership_unknown(
            runtime_plugin_id=runtime_plugin_id,
            reason="install_source_manager_unavailable",
        )

    if manager.is_degraded:
        raise UninstallOwnershipError(
            code="INSTALL_SOURCE_READ_ONLY",
            message="Plugin ownership cannot be verified while install-source data is read-only",
            status_code=503,
            details={
                "plugin_id": runtime_plugin_id,
                "reason": "install_source_degraded",
            },
        )

    try:
        entry = manager.entry_for_directory(plugin_dir, include_removed=False)
    except InstallSourceError as exc:
        raise _ownership_unknown(
            runtime_plugin_id=runtime_plugin_id,
            reason="install_source_lookup_failed",
            install_source_error=exc.code,
        ) from exc

    if entry is None:
        raise _ownership_unknown(
            runtime_plugin_id=runtime_plugin_id,
            reason="active_entry_missing",
        )

    declared_plugin_id = _read_declared_plugin_id(
        config_path,
        runtime_plugin_id=runtime_plugin_id,
    )

    # entry_for_directory performs the install-source subsystem's canonical,
    # cross-platform (root_id, directory_name) lookup. Do not repeat that path
    # comparison with raw strings here: it would regress Windows casing and
    # macOS Unicode normalization handled by the manager.
    # Legacy/import-window rows may carry the documented empty plugin_id
    # placeholder.  The exact directory lookup above still proves which slot
    # the entry describes; only installer-owned channels may use that narrow
    # compatibility path.
    entry_identity_matches = entry.plugin_id == declared_plugin_id or (
        entry.plugin_id == "" and can_neko_uninstall(entry)
    )
    if entry.removed or not entry_identity_matches:
        raise _ownership_unknown(
            runtime_plugin_id=runtime_plugin_id,
            reason="entry_identity_mismatch",
            entry=entry,
            declared_plugin_id=declared_plugin_id,
        )

    if entry.root_id == "builtin" and entry.channel == "builtin":
        raise UninstallOwnershipError(
            code="PLUGIN_UNINSTALL_BUILTIN_FORBIDDEN",
            message=f"Builtin plugin '{runtime_plugin_id}' cannot be uninstalled",
            status_code=403,
            details={
                "plugin_id": runtime_plugin_id,
                "declared_plugin_id": declared_plugin_id,
                "root_id": entry.root_id,
                "channel": entry.channel,
            },
        )

    if entry.root_id == "user" and entry.channel == "manual":
        raise UninstallOwnershipError(
            code="PLUGIN_MANUAL_NOT_MANAGED",
            message=(
                f"Plugin '{runtime_plugin_id}' is maintained manually and is not "
                "managed by N.E.K.O"
            ),
            status_code=409,
            details={
                "plugin_id": runtime_plugin_id,
                "declared_plugin_id": declared_plugin_id,
                "root_id": entry.root_id,
                "channel": entry.channel,
            },
        )

    if not can_neko_uninstall(entry):
        raise _ownership_unknown(
            runtime_plugin_id=runtime_plugin_id,
            reason="unsupported_ownership",
            entry=entry,
            declared_plugin_id=declared_plugin_id,
        )

    return entry


def _ownership_unknown(
    *,
    runtime_plugin_id: str,
    reason: str,
    install_source_error: str | None = None,
    entry: LockEntry | None = None,
    declared_plugin_id: str | None = None,
) -> UninstallOwnershipError:
    details: dict[str, object] = {
        "plugin_id": runtime_plugin_id,
        "reason": reason,
    }
    if declared_plugin_id is not None:
        details["declared_plugin_id"] = declared_plugin_id
    if install_source_error:
        details["install_source_error"] = install_source_error
    if entry is not None:
        details.update(
            {
                "entry_plugin_id": entry.plugin_id,
                "entry_root_id": entry.root_id,
                "entry_directory_name": entry.directory_name,
                "entry_channel": entry.channel,
                "entry_removed": entry.removed,
            }
        )
    return UninstallOwnershipError(
        code="PLUGIN_UNINSTALL_OWNERSHIP_UNKNOWN",
        message=f"Plugin ownership could not be verified for '{runtime_plugin_id}'",
        status_code=409,
        details=details,
    )


def _read_declared_plugin_id(
    config_path: Path,
    *,
    runtime_plugin_id: str,
) -> str:
    declared_plugin_id = PluginDirectoryScanner._load_plugin_id(config_path.parent)
    if not declared_plugin_id:
        raise _ownership_unknown(
            runtime_plugin_id=runtime_plugin_id,
            reason="manifest_identity_unavailable",
        )
    return declared_plugin_id
