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
import itertools
import threading
from pathlib import Path
from typing import Callable

from plugin.logging_config import get_logger
from plugin.server.domain.errors import ServerDomainError
from plugin.sdk.shared.permissions.catalog import PERMISSIONS
from plugin.sdk.shared.permissions.state import PermissionState
from plugin.server.infrastructure.plugin_permissions import (
    CONDITION_IN_USE,
    add_change_listener,
    MESSAGES_PUSH,
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
        restore_permissions(plugin_id, previous)

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
    removed = permission_store.forget(plugin_id)
    _forget_last_record(plugin_id)
    return removed


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
    if record is None:
        _forget_last_record(plugin_id)


# ----------------------------------------------------------------------
# Snapshots sent down to plugin processes (``ctx.permissions``)
# ----------------------------------------------------------------------

_snapshot_lock = threading.Lock()
_snapshot_revision = itertools.count(1)
# Last record read successfully per plugin, with the revision of the snapshot
# that read it: a later read failure keeps using it.
_last_records: dict[str, tuple[int, dict]] = {}
# Bumped whenever a plugin's stored grants change (``_reloads`` when the
# whole store is (re)loaded): a read that started before the change must not
# put the older record back into ``_last_records``.
_removals: dict[str, int] = {}
_reloads = 0


def _invalidate_last_records(plugin_ids: frozenset[str] | None) -> None:
    """The one place remembered records are dropped: forget them and advance
    the generation, so reads that started earlier cannot write them back.
    ``None`` means every plugin (the store was (re)loaded)."""
    global _reloads
    with _snapshot_lock:
        if plugin_ids is None:
            _last_records.clear()
            _reloads += 1
            return
        for plugin_id in plugin_ids:
            _last_records.pop(plugin_id, None)
            _removals[plugin_id] = _removals.get(plugin_id, 0) + 1


def _forget_last_record(plugin_id: str) -> None:
    """A plugin whose grants changed must not fall back to the old ones."""
    _invalidate_last_records(frozenset({plugin_id}))


def _on_store_change(plugin_ids: frozenset[str] | None) -> None:
    """Store change listener: drop remembered records the write made stale,
    so a later read failure never reports a removed or replaced grant."""
    _invalidate_last_records(plugin_ids)


def _generation(plugin_id: str) -> tuple[int, int]:
    return _reloads, _removals.get(plugin_id, 0)


def _read_and_remember(plugin_id: str, revision: int) -> dict:
    """Read the plugin's record and remember it for read failures, unless its
    grants changed while reading. A store (re)load during the read, usually
    triggered by this very read, gets one re-read under the new generation."""
    for attempt in range(2):
        with _snapshot_lock:
            generation = _generation(plugin_id)
        record = permission_record(plugin_id)
        with _snapshot_lock:
            current = _generation(plugin_id)
            if current == generation:
                # Snapshots read concurrently: only a newer read replaces an older one.
                if _last_records.get(plugin_id, (0, None))[0] < revision:
                    _last_records[plugin_id] = (revision, copy.deepcopy(record))
                return record
            if current[1] != generation[1] or attempt:
                # This plugin's grants changed meanwhile: use the read for this
                # snapshot, remember nothing (the change schedules a new push).
                return record
    return record


add_change_listener(_on_store_change)


def _resolve_state(permission: str, record: dict | None, *, checks_off: bool) -> PermissionState:
    from plugin.sdk.shared.permissions.runtime import unavailable_state
    from plugin.server.infrastructure.plugin_permissions import (
        CONDITION_ANYTIME,
        LEGACY_DENIED,
        STATE_GRANTED,
    )

    spec = PERMISSIONS[permission]
    if record is None:
        return unavailable_state(permission, checks_disabled=checks_off)
    enforced = spec.enforced and not checks_off
    entry = (record.get("permissions") or {}).get(permission)
    if isinstance(entry, dict):
        status = entry.get("state")
        background = status == STATE_GRANTED and entry.get("condition") == CONDITION_ANYTIME
    elif record.get(LEGACY_DENIED) and spec.legacy_name is not None:
        # A whole-plugin denial from the earlier consent flow covers the
        # permissions that flow knew about; later ones are undecided.
        status, background = "denied", False
    else:
        status, background = "undecided", False
    if status not in ("granted", "denied", "undecided"):
        status, background = "undecided", False
    if permission == MESSAGES_PUSH and status != "granted":
        # Disclosure only: either the user approved it or it is undecided.
        status = "undecided"
    return PermissionState(status, background=background, enforced=enforced)


def permission_snapshot(plugin_id: str) -> dict:
    """The plugin's resolved permission states, in the shape the plugin
    process takes (see ``plugin.sdk.shared.permissions.runtime``). Never raises.

    When the grants cannot be read, the last record read for this plugin is
    used; when none was ever read, enforced permissions are denied and
    disclosure-only ones undecided. While the master switch is on every
    permission is ``enforced=False``.
    """
    from plugin.core.capability_policy import checks_disabled
    from plugin.sdk.shared.permissions.runtime import state_to_wire

    # Numbered before reading: a snapshot that starts after a write always
    # outranks one that may have read the data from before it.
    with _snapshot_lock:
        revision = next(_snapshot_revision)
    read_failed = False
    try:
        record: dict | None = _read_and_remember(plugin_id, revision)
    except Exception as exc:
        code = getattr(exc, "code", None)
        read_failed = True
        with _snapshot_lock:
            record = copy.deepcopy(_last_records.get(plugin_id, (0, None))[1])
        logger.error(
            "plugin permissions could not be read for the plugin process: plugin_id={}, error={}, using={}",
            plugin_id,
            code or type(exc).__name__,
            "last read" if record is not None else "nothing (enforced permissions count as denied)",
        )
    try:
        checks_off = checks_disabled()
    except Exception as exc:
        # The switch only ever turns checks off; if it cannot be read, keep them on.
        logger.error("permission master switch could not be read: error={}", type(exc).__name__)
        checks_off = False
    states = {
        permission: state_to_wire(_resolve_state(permission, record, checks_off=checks_off))
        for permission in PERMISSIONS
    }
    snapshot: dict = {"revision": revision, "states": states}
    if read_failed:
        # Host-side marker (not sent): the push service retries until a read works.
        snapshot["read_failed"] = True
    return snapshot
