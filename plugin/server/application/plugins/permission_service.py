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

from plugin.logging_config import get_logger
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


def load_permissions() -> None:
    """Load the store now, so a first-use import resolves today's sources."""
    permission_store.ensure_loaded()


def permission_record(plugin_id: str) -> dict:
    """The plugin's grants as they apply to its current source.

    Read-only: nothing is saved, so a concurrent install or uninstall that
    briefly exposes another source cannot change the stored grants.
    """
    source = current_source(plugin_id)
    view = permission_store.view(plugin_id, source)
    view.setdefault("permissions", {})
    view["source_confirmed"] = source is not None
    return view


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


def restore_permissions(plugin_id: str, record: dict) -> None:
    permission_store.restore(plugin_id, record)
