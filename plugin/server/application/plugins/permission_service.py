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
    CORRUPT,
    UNSUPPORTED_VERSION,
    PluginPermissionStore,
    PluginSource,
)

logger = get_logger("server.plugins.permissions")

# A damaged or newer-version store: every read and write fails, so it holds
# no grant that could apply and there is nothing to bind or drop. The records
# are still on disk, though; if the file becomes readable again (e.g. after
# upgrading back to the version that wrote it) they apply as stored, and the
# usual source rules reconcile them on the next plugin start.
_UNUSABLE_STORE_CODES = (CORRUPT, UNSUPPORTED_VERSION)


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


def _installed_plugin_ids(result: dict) -> list[str | None]:
    """Manifest ``plugin.id`` of each plugin of an install result, ``None``
    where it cannot be read.

    Grants are keyed by the manifest id, which may differ from the install
    directory name, so the id is read from each installed manifest.
    """
    try:
        unpacked = result.get("unpack", result.get("install", result))
        installed = list(unpacked.get("installed_plugins") or [])
    except (AttributeError, TypeError):
        return []
    plugin_ids: list[str | None] = []
    for entry in installed:
        target_dir = entry.get("target_dir") if isinstance(entry, dict) else None
        plugin_ids.append(_manifest_plugin_id(str(target_dir)) if target_dir else None)
    return plugin_ids


def reconcile_installed_plugins(result: dict) -> None:
    """Reconcile every plugin of a committed install result. Never raises."""
    for plugin_id in _installed_plugin_ids(result):
        if plugin_id is None:
            logger.warning(
                "plugin permission source not reconciled: installed manifest id unavailable"
            )
            continue
        reconcile_permission_source(plugin_id)


def forget_installed_plugins(result: dict, *, plugin_ids: tuple[str, ...] = ()) -> bool:
    """Drop all grants of every plugin of an install result, plus ``plugin_ids``.

    Used when a replacement committed new code but its install source could
    not be recorded: the grants stay bound to the previous source, so they
    are dropped and the plugin is asked again. Call under the plugin
    operation lock. Never raises; returns whether every grant was dropped.
    A damaged or newer-version store counts as dropped (see
    ``_UNUSABLE_STORE_CODES``), although its records stay on disk.
    """
    targets = set(plugin_ids)
    complete = True
    for plugin_id in _installed_plugin_ids(result):
        if plugin_id is None:
            logger.warning(
                "plugin permission grants not dropped: installed manifest id unavailable"
            )
            complete = False
            continue
        targets.add(plugin_id)
    for plugin_id in sorted(targets):
        try:
            forget_permissions(plugin_id)
        except ServerDomainError as exc:
            if exc.code in _UNUSABLE_STORE_CODES:
                # Holds no usable grant, same as bind_permission_source.
                continue
            logger.error(
                "plugin permission grants could not be dropped plugin_id={} code={}",
                plugin_id,
                exc.code,
            )
            complete = False
        except Exception as exc:
            logger.error(
                "plugin permission grants could not be dropped plugin_id={} err_type={}",
                plugin_id,
                type(exc).__name__,
            )
            complete = False
    return complete


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
    consent write fails, so a failed request leaves this plugin's records in
    both files unchanged (a first-use import may still be saved).
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


def bind_permission_source(plugin_id: str, source: PluginSource) -> None:
    """Bind a record still without a source to ``source``, which the caller
    has confirmed, so a later different source counts as a source change.

    Raises ``ServerDomainError`` when the store cannot be read or saved. A
    damaged or newer store holds no usable grant and is skipped.
    """
    try:
        permission_store.bind_unbound_source(plugin_id, source)
    except ServerDomainError as exc:
        if exc.code not in _UNUSABLE_STORE_CODES:
            raise
        logger.warning(
            "plugin permission source not bound plugin_id={} code={}", plugin_id, exc.code
        )


def bind_current_permission_source(plugin_id: str) -> None:
    """Before a replacement: bind a record still without a source to the
    plugin's current source, so the replacement's source counts as a source
    change rather than a first binding. Nothing happens when the current
    source is unknown. Raises like :func:`bind_permission_source`.
    """
    source = current_source(plugin_id)
    if source is not None:
        bind_permission_source(plugin_id, source)


def forget_permissions(plugin_id: str) -> dict | None:
    """Drop all grants of an uninstalled plugin; returns the removed record."""
    return permission_store.forget(plugin_id)


class PermissionSnapshot:
    """A plugin's permission record taken before a transaction changes its
    source, so a rollback can put it back.

    ``status``: ``"taken"`` (``record`` holds the record, ``None`` = none),
    ``"unusable"`` (the store is damaged or newer: every write fails too, so
    the transaction cannot change it and nothing needs restoring) or
    ``"unavailable"`` (a transient or unexpected failure: the transaction
    may still change the store, so a rollback cannot be complete).
    """

    def __init__(self, status: str, record: dict | None = None):
        self.status = status
        self.record = record

    def restore(self, plugin_id: str, *, store_may_have_changed: bool = True) -> bool:
        """Put the record back; returns whether the permissions are restored.

        ``store_may_have_changed`` is False when nothing in the transaction
        could have written the plugin's grants (no plugin start, which is
        what reconciles them); a missing snapshot then loses nothing.
        """
        if self.status == "unusable":
            return True
        if self.status == "unavailable" and not store_may_have_changed:
            return True
        if self.status != "taken":
            logger.error(
                "plugin permission grants cannot be restored: no snapshot plugin_id={}",
                plugin_id,
            )
            return False
        try:
            permission_store.restore(plugin_id, self.record)
        except Exception as exc:
            logger.error(
                "plugin permission grants could not be restored plugin_id={} err_type={}",
                plugin_id,
                type(exc).__name__,
            )
            return False
        return True


def snapshot_permissions(plugin_id: str) -> PermissionSnapshot:
    """Snapshot before a source change; also performs a pending first-use
    import while the current (old) source is still selected. Never raises."""
    from plugin.server.infrastructure.plugin_permissions import (
        CORRUPT,
        UNSUPPORTED_VERSION,
    )

    try:
        return PermissionSnapshot("taken", permission_store.read(plugin_id) or None)
    except ServerDomainError as exc:
        status = "unusable" if exc.code in (CORRUPT, UNSUPPORTED_VERSION) else "unavailable"
        logger.warning(
            "plugin permission snapshot not taken plugin_id={} code={}", plugin_id, exc.code
        )
        return PermissionSnapshot(status)
    except Exception as exc:
        logger.warning(
            "plugin permission snapshot not taken plugin_id={} err_type={}",
            plugin_id,
            type(exc).__name__,
        )
        return PermissionSnapshot("unavailable")


def restore_permissions(plugin_id: str, record: dict | None) -> None:
    """Put back an earlier record; ``None`` removes the plugin's record."""
    permission_store.restore(plugin_id, record)
