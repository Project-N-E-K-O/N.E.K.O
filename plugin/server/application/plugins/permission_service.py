"""Plugin permission grants bound to the plugin's install source.

A plugin's source is its install channel plus a stable fingerprint:

* ``market``: the Market plugin id;
* ``imported``: the package SHA-256;
* ``builtin`` / ``manual``: no fingerprint, the channel alone.

When the source cannot be determined (install-source lock not initialised,
degraded, or no matching entry) the last known source is kept and the view is
marked ``source_confirmed=False``. Grants are discarded only when two known
sources differ.
"""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Callable

from plugin.logging_config import get_logger
from plugin.server.domain.errors import ServerDomainError
from plugin.server.infrastructure.plugin_permissions import (
    CONDITION_IN_USE,
    PluginPermissionStore,
    PluginSource,
)

logger = get_logger("server.plugins.permissions")


class _SourceUnknown(Exception):
    pass


def _plugin_directory(plugin_id: str) -> Path | None:
    """Directory of the loaded plugin, ``None`` when it is not registered."""
    from plugin.server.infrastructure.config_paths import (
        _resolve_registered_plugin_config_path,
    )

    try:
        path = _resolve_registered_plugin_config_path(plugin_id)
    except TimeoutError as exc:
        # The registry is busy; an unknown source keeps the last known one.
        raise _SourceUnknown from exc
    return path.parent if path is not None else None


def _source_of(entry) -> PluginSource | None:
    from plugin.server.application.install_source.models import (
        SourceDetailImported,
        SourceDetailMarket,
    )

    detail = entry.source_detail
    if entry.channel == "market":
        fingerprint = detail.plugin_market_id if isinstance(detail, SourceDetailMarket) else ""
    elif entry.channel == "imported":
        fingerprint = detail.package_sha256 if isinstance(detail, SourceDetailImported) else ""
    else:
        return PluginSource(entry.channel)
    # A market/imported entry without its fingerprint is an incomplete
    # record, not a different source: treat it as unknown.
    return PluginSource(entry.channel, fingerprint) if fingerprint else None


def current_source(plugin_id: str) -> PluginSource | None:
    """Return the plugin's install source, or ``None`` when it is unknown."""
    from plugin.server.application.install_source import (
        InstallSourceError,
        get_install_source_manager,
    )

    manager = get_install_source_manager()
    if manager is None or manager.is_degraded or not plugin_id:
        return None
    try:
        directory = _plugin_directory(plugin_id)
    except _SourceUnknown:
        return None
    if directory is not None:
        try:
            entry = manager.entry_for_directory(directory)
        except InstallSourceError:
            return None
        # The directory key is exact; an empty plugin_id only means the
        # metadata was unreadable when the lock entry was written. A known
        # directory without an entry must not fall back to another root's
        # entry with the same plugin_id.
        if entry is not None and entry.plugin_id in ("", plugin_id):
            return _source_of(entry)
        return None
    # LockEntry is keyed by directory, so a plugin_id lookup scans every
    # active entry and only trusts a unique match.
    matches = [
        entry
        for entry in manager.list_entries(include_removed=False)
        if entry.plugin_id == plugin_id
    ]
    if len(matches) != 1:
        return None
    return _source_of(matches[0])


permission_store = PluginPermissionStore(source_resolver=current_source)


def permission_record(plugin_id: str) -> dict:
    """The plugin's grants as they apply to its current source.

    Source rules are applied to the returned copy only, so a concurrent
    install or uninstall that briefly exposes another source cannot change
    the stored grants. The only possible write is the store's one-time
    first-use import of the legacy consent file (kept in memory if that
    save fails).
    """
    source = current_source(plugin_id)
    view = permission_store.view(plugin_id, source)
    view.setdefault("permissions", {})
    view.setdefault("source_mismatch", False)
    view["source_confirmed"] = source is not None
    return view


def reconcile_permission_source(plugin_id: str) -> None:
    """Persist the plugin's current source into its stored grants.

    Called when a plugin starts, under the plugin operation lock. A changed
    source discards the old grants on disk; a first known source is bound.
    Never raises: no decision depends on the stored grants yet, so a store
    problem must not stop the plugin from starting.
    """
    try:
        permission_store.sync_source(plugin_id, current_source(plugin_id))
    except ServerDomainError as exc:
        logger.warning(
            "plugin permission source not reconciled: plugin_id={}, code={}",
            plugin_id,
            exc.code,
        )
    except Exception as exc:
        logger.warning(
            "plugin permission source not reconciled: plugin_id={}, error={}",
            plugin_id,
            type(exc).__name__,
        )


def _manifest_plugin_id(target_dir: str) -> str | None:
    import tomllib

    try:
        with open(Path(target_dir) / "plugin.toml", "rb") as handle:
            plugin_id = tomllib.load(handle).get("plugin", {}).get("id")
    except (OSError, ValueError, AttributeError):
        return None
    return plugin_id if isinstance(plugin_id, str) and plugin_id else None


def reconcile_installed_plugins(result: dict) -> None:
    """Reconcile every plugin of a committed install result.

    Grants are keyed by the manifest ``plugin.id``, which may differ from the
    install directory name, so the id is read from each installed manifest.
    Never raises.
    """
    try:
        unpacked = result.get("unpack", result.get("install", result))
        installed = list(unpacked.get("installed_plugins", []))
    except AttributeError:
        return
    for entry in installed:
        target_dir = entry.get("target_dir") if isinstance(entry, dict) else None
        plugin_id = _manifest_plugin_id(str(target_dir)) if target_dir else None
        if plugin_id is None:
            logger.warning(
                "plugin permission source not reconciled: installed manifest id unavailable"
            )
            continue
        reconcile_permission_source(plugin_id)


def mirror_consent_decision(
    plugin_id: str,
    decision: str | None,
    capabilities: list[str] | tuple[str, ...] = (),
    *,
    newly_approved: list[str] | tuple[str, ...] | None = None,
) -> Callable[[], None]:
    """Keep the permission store in step with the consent flow.

    Transitional: remove together with the consent dialog. Called under the
    plugin operation lock before the consent record is written. Returns an
    undo that puts the previous permission record back; call it when the
    consent write fails, so a failed request leaves both files unchanged.
    """
    previous = permission_store.read(plugin_id) or None
    permission_store.apply_legacy_decision(
        plugin_id,
        decision,
        capabilities,
        newly_approved=newly_approved,
        source=current_source(plugin_id),
    )

    def undo() -> None:
        permission_store.restore(plugin_id, previous)

    return undo


def decide_permission(
    plugin_id: str,
    permission_id: str,
    state_value: str,
    *,
    condition: str = CONDITION_IN_USE,
) -> dict:
    return permission_store.decide(
        plugin_id,
        permission_id,
        state_value,
        condition=condition,
        source=current_source(plugin_id),
    )


def forget_permissions(plugin_id: str) -> dict | None:
    """Drop all grants of an uninstalled plugin; returns the removed record."""
    return permission_store.forget(plugin_id)


def restore_permissions(plugin_id: str, record: dict | None) -> None:
    """Put back an earlier record; ``None`` removes the plugin's record."""
    permission_store.restore(plugin_id, record)
