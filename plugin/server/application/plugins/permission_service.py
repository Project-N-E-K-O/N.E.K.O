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

from plugin.core.state import state
from plugin.logging_config import get_logger
from plugin.server.infrastructure.plugin_permissions import (
    CONDITION_IN_USE,
    PluginPermissionStore,
    PluginSource,
)

logger = get_logger("server.plugins.permissions")


def _plugin_directory(plugin_id: str) -> Path | None:
    with state.acquire_plugins_read_lock():
        meta = state.plugins.get(plugin_id)
        path = meta.get("config_path") if isinstance(meta, dict) else None
    return Path(path).parent if path else None


def _source_of(entry) -> PluginSource:
    from plugin.server.application.install_source.models import (
        SourceDetailImported,
        SourceDetailMarket,
    )

    detail = entry.source_detail
    fingerprint: str | None = None
    if entry.channel == "market" and isinstance(detail, SourceDetailMarket):
        fingerprint = detail.plugin_market_id or None
    elif entry.channel == "imported" and isinstance(detail, SourceDetailImported):
        fingerprint = detail.package_sha256 or None
    return PluginSource(entry.channel, fingerprint)


def current_source(plugin_id: str) -> PluginSource | None:
    """Return the plugin's install source, or ``None`` when it is unknown."""
    from plugin.server.application.install_source import (
        InstallSourceError,
        get_install_source_manager,
    )

    manager = get_install_source_manager()
    if manager is None or manager.is_degraded or not plugin_id:
        return None
    directory = _plugin_directory(plugin_id)
    if directory is not None:
        try:
            entry = manager.entry_for_directory(directory)
        except InstallSourceError:
            entry = None
        if entry is not None:
            # The directory key is exact; an empty plugin_id only means the
            # metadata was unreadable when the lock entry was written.
            if entry.plugin_id in ("", plugin_id):
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
    """The plugin's grants after reconciling them with its current source."""
    source = current_source(plugin_id)
    record = permission_store.sync_source(plugin_id, source)
    view = copy.deepcopy(record)
    view.setdefault("permissions", {})
    # After sync_source a known source always equals the stored one.
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
