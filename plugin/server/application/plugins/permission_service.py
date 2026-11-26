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
import os
import threading
from datetime import datetime, timezone
from pathlib import Path

from plugin.logging_config import get_logger
from plugin.server.domain.errors import ServerDomainError
from plugin.sdk.shared.permissions.catalog import PERMISSIONS
from plugin.sdk.shared.permissions.state import PermissionState
from plugin.server.infrastructure.plugin_permissions import (
    CONDITION_IN_USE,
    CORRUPT,
    UNSUPPORTED_VERSION,
    add_change_listener,
    PluginPermissionStore,
    PluginSource,
)

logger = get_logger("server.plugins.permissions")

MESSAGES_PUSH = "plugin_sdk.main_agent.messages:push"

# A damaged or newer-version store: every read and write fails, so it holds
# no grant that could apply and there is nothing to bind or drop. The records
# are still on disk, though; if the file becomes readable again (e.g. after
# upgrading back to the version that wrote it) they apply as stored, and the
# usual source rules reconcile them on the next plugin start.
UNUSABLE_STORE_CODES = (CORRUPT, UNSUPPORTED_VERSION)

# A replacement cannot tell which source a source-less grant record belongs to.
SOURCE_UNKNOWN = "PLUGIN_PERMISSION_SOURCE_UNKNOWN"


class _SourceUnknown(Exception):
    pass


def _registered_candidates(plugin_id: str) -> tuple[object, ...]:
    from plugin.server.infrastructure.config_paths import (
        _registered_plugin_config_candidates,
    )

    try:
        return _registered_plugin_config_candidates(plugin_id)
    except TimeoutError as exc:
        # The registry is busy; an unknown source keeps the last known one.
        raise _SourceUnknown from exc


def _plugin_directory(plugin_id: str, candidates: tuple[str, ...] | None = None) -> Path | None:
    """Directory of the loaded plugin, ``None`` when it is not registered.

    ``candidates`` are config files already resolved and found to exist (the
    cache key's own paths, see ``_candidates_key``): the first ``plugin.toml``
    among them is used without looking at the file system again, so the
    directory always matches the key it is cached under.
    """
    from plugin.server.infrastructure.config_paths import (
        _resolve_registered_plugin_config_path,
    )

    if candidates is not None:
        for candidate in candidates:
            path = Path(candidate)
            if path.name == "plugin.toml":
                return path.parent
        return None
    try:
        path = _resolve_registered_plugin_config_path(plugin_id)
    except TimeoutError as exc:
        raise _SourceUnknown from exc
    return path.parent if path is not None else None


# plugin_id -> (manager, lock snapshot, registered config paths and whether
# each exists, source). The source is worked out from exactly these inputs;
# the install-source lock is copy-on-write (every change publishes a new
# snapshot object), so a different snapshot, manager, registration or a
# config file appearing / disappearing or a symlink on its path switched
# means it is worked out again (a stat and a realpath per candidate). Spares the per-use path resolution, logging and lock-entry
# lookup in the host's checks.
_source_cache: dict[str, tuple[object, object, tuple[object, ...], PluginSource | None]] = {}
_SOURCE_CACHE_LIMIT = 1024


def _candidates_key(candidates: tuple[object, ...]) -> tuple[object, ...]:
    """The candidates and where each really is now (None when it is not a file).

    Which one is used, and its directory, depends on both: a config file
    appearing, or a symlink on its path switched to another directory, is a
    different input.
    """
    resolved = []
    for candidate in candidates:
        try:
            resolved.append(
                os.path.realpath(candidate) if candidate is not None and os.path.isfile(candidate) else None
            )
        except (TypeError, ValueError, OSError):
            resolved.append(None)
    return (candidates, tuple(resolved))


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
    from plugin.server.application.install_source import get_install_source_manager

    manager = get_install_source_manager()
    if manager is None or manager.is_degraded or not plugin_id:
        return None
    try:
        candidates = _registered_candidates(plugin_id)
    except _SourceUnknown:
        return None
    try:
        snapshot = manager.snapshot()
    except Exception:
        snapshot = None
    key = _candidates_key(candidates)
    # The directory is chosen from the same resolved paths as the key, so a
    # link switched between the two looks can't file B's source under A's key.
    resolved = tuple(path for path in key[1] if path is not None)
    cached = _source_cache.get(plugin_id)
    if (
        snapshot is not None and cached is not None and cached[0] is manager
        and cached[1] is snapshot and cached[2] == key
    ):
        return cached[3]
    try:
        source = _resolve_source(plugin_id, manager, resolved, snapshot)
    except _SourceUnknown:
        return None  # not cached: a busy registry or lock error is not an answer
    if snapshot is None:
        return source
    if len(_source_cache) >= _SOURCE_CACHE_LIMIT:
        _source_cache.clear()
    _source_cache[plugin_id] = (manager, snapshot, key, source)
    return source


def _resolve_source(
    plugin_id: str, manager, candidates: tuple[object, ...], snapshot: object = None
) -> PluginSource | None:
    """The source from ``snapshot`` (the one the cache entry is keyed by),
    never a later state of the manager; the current one when it is None."""
    from plugin.server.application.install_source import InstallSourceError

    pinned = {"snapshot": snapshot} if snapshot is not None else {}
    directory = _plugin_directory(plugin_id, candidates)
    if directory is not None:
        try:
            # The directory comes from the key's resolved paths: classify it
            # as it is, not wherever a link points by now.
            entry = manager.entry_for_directory(
                directory, **pinned, **({"path_resolved": True} if pinned else {})
            )
        except InstallSourceError as exc:
            raise _SourceUnknown from exc
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
        for entry in manager.list_entries(include_removed=False, **pinned)
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


def forget_plugin_grants(plugin_ids: tuple[str, ...]) -> bool:
    """Drop the grants of ``plugin_ids`` as a source change would.

    Used where the plugin's code changed (or may have) but its new install
    source could not be recorded: inside a replacement, before the plugin
    restarts, and when a rollback leaves the new code on disk. The grants
    would stay bound to the previous source, so they are dropped and the
    plugin is asked again; what a source change keeps stays (see
    ``KEPT_ON_SOURCE_CHANGE``). A rollback that restores the previous code
    puts them back from its snapshot. Call under the plugin operation lock.
    Never raises; returns whether every grant was dropped. A damaged or
    newer-version store counts as dropped (see ``UNUSABLE_STORE_CODES``),
    although its records stay on disk.
    """
    complete = True
    for plugin_id in sorted(set(plugin_ids)):
        try:
            drop_grants_on_source_change(plugin_id)
        except ServerDomainError as exc:
            if exc.code in UNUSABLE_STORE_CODES:
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


def record_consent_decision(
    plugin_id: str,
    decision: str | None,
    capabilities: list[str] | tuple[str, ...] = (),
    *,
    newly_approved: list[str] | tuple[str, ...] | None = None,
) -> dict:
    """Store a decision made in the consent dialog; returns the record.

    The permission store is where the dialog's decisions live. Remove
    together with the consent dialog. Call under the plugin operation lock.
    Raises ``ServerDomainError`` when the store cannot be read or saved.
    """
    return permission_store.apply_legacy_decision(
        plugin_id,
        decision,
        capabilities,
        newly_approved=newly_approved,
        source=current_source(plugin_id),
    )


def consent_decision(record: dict | None) -> dict:
    """The consent dialog's view of a permission record.

    ``record`` is the record as it applies to the plugin's current source
    (see :func:`permission_record`). Returns ``decision`` (``"approved"``,
    ``"denied"`` or no key), ``approved_capabilities`` (the earlier names of
    the granted permissions) and ``decided_at``, the fields the consent
    dialog shows; an empty dict when nothing was decided.
    """
    from plugin.server.infrastructure.plugin_permissions import (
        LEGACY_APPROVED,
        LEGACY_DENIED,
        STATE_GRANTED,
    )

    if not record:
        return {}
    permissions = record.get("permissions") or {}
    granted = sorted(
        {
            spec.legacy_name
            for permission_id, entry in permissions.items()
            if isinstance(entry, dict)
            and entry.get("state") == STATE_GRANTED
            and (spec := PERMISSIONS.get(permission_id)) is not None
            and spec.legacy_name is not None
        }
    )
    if record.get(LEGACY_DENIED):
        decision, approved = "denied", []
    elif granted or record.get(LEGACY_APPROVED):
        decision, approved = "approved", granted
    else:
        return {}
    # When the dialog's decision was made: the newest granted entry of an
    # approval; otherwise (a denial, or an approval without permissions) the
    # record's last change, the closest stored time.
    times = [
        entry.get("decided_at")
        for permission_id, entry in permissions.items()
        if decision == "approved"
        and isinstance(entry, dict)
        and entry.get("state") == STATE_GRANTED
        and isinstance(entry.get("decided_at"), str)
        and (spec := PERMISSIONS.get(permission_id)) is not None
        and spec.legacy_name is not None
    ]
    decided_at = max(times) if times else record.get("updated_at")
    return {
        "decision": decision,
        "approved_capabilities": approved,
        "decided_at": decided_at if isinstance(decided_at, str) else None,
    }


def consent_record(plugin_id: str) -> dict:
    """:func:`consent_decision` for the plugin's current source.

    Raises ``ServerDomainError`` when the store cannot be read.
    """
    return consent_decision(permission_record(plugin_id))


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


def clear_permission(plugin_id: str, permission_id: str) -> dict:
    """Return one permission of the plugin to undecided."""
    return permission_store.clear(plugin_id, permission_id)


def bind_permission_source(plugin_id: str, source: PluginSource) -> None:
    """Bind a record still without a source to ``source``, which the caller
    has confirmed, so a later different source counts as a source change.

    Raises ``ServerDomainError`` when the store cannot be read or saved. A
    damaged or newer store holds no usable grant and is skipped.
    """
    try:
        permission_store.bind_unbound_source(plugin_id, source)
    except ServerDomainError as exc:
        if exc.code not in UNUSABLE_STORE_CODES:
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


def bind_entry_permission_source(plugin_id: str, entry) -> None:
    """Before a replacement: bind a record still without a source to the
    source of ``entry``, the install-source lock entry the caller has
    validated under the plugin operation lock, so the replacement's source
    counts as a source change rather than a first binding.

    Unlike :func:`bind_current_permission_source` this does not look the
    plugin up again. When the entry's source is incomplete (a Market or
    imported entry without its fingerprint) and the record has no source
    but still grants a permission, raises ``ServerDomainError`` with
    ``SOURCE_UNKNOWN`` (409), so the new source cannot take those grants
    over. A record holding only denials or nothing is left alone. Otherwise
    raises like :func:`bind_permission_source`.
    """
    source = _source_of(entry)
    if source is not None:
        bind_permission_source(plugin_id, source)
        return
    try:
        held = permission_store.holds_unbound_grant(plugin_id)
    except ServerDomainError as exc:
        if exc.code not in UNUSABLE_STORE_CODES:
            raise
        logger.warning(
            "plugin permission source not bound plugin_id={} code={}", plugin_id, exc.code
        )
        return
    if held:
        raise ServerDomainError(
            SOURCE_UNKNOWN,
            "the plugin's current install source is incomplete, so its permission "
            "grants cannot be bound to it before the replacement",
            409,
            details={"plugin_id": plugin_id},
        )


def drop_grants_on_source_change(plugin_id: str) -> dict | None:
    """Drop what a source change drops (see ``KEPT_ON_SOURCE_CHANGE``)
    without recording a new source; returns the record as it was before."""
    return permission_store.drop_grants(plugin_id)


def forget_permissions(plugin_id: str) -> dict | None:
    """Drop all grants of an uninstalled plugin; returns the removed record."""
    # The store's change notification drops the remembered record (see
    # _on_store_change), in order with any later write.
    return permission_store.forget(plugin_id)


def _parse_time(value: object) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=timezone.utc)


def _directory_gone(root: object, directory_name: str) -> bool:
    """Whether the plugin directory is absent under a root that is present."""
    if root is None or not Path(root).is_dir():
        return False
    try:
        (Path(root) / directory_name).lstat()
    except FileNotFoundError:
        return True
    except (OSError, ValueError):
        return False
    return False


_MANIFEST_UNKNOWN = object()


def _manifest_plugin_id_state(root: object, directory_name: str):
    """Id the directory's plugin.toml declares now.

    ``""`` when there is no manifest entry (not a plugin), the id when it
    reads, ``_MANIFEST_UNKNOWN`` when it cannot be read or checked.
    """
    from plugin.server.application.install_source.scanner import (
        PluginDirectoryScanner,
    )

    if root is None or not Path(root).is_dir():
        # An unavailable root hides whatever the directory declares.
        return _MANIFEST_UNKNOWN
    directory = Path(root) / directory_name
    try:
        (directory / "plugin.toml").lstat()
    except FileNotFoundError:
        return ""
    except (OSError, ValueError):
        return _MANIFEST_UNKNOWN
    return PluginDirectoryScanner._load_plugin_id(directory) or _MANIFEST_UNKNOWN


def _uninstalled_plugin_cutoffs(manager) -> dict[str, datetime] | None:
    """Plugins the install-source lock proves uninstalled, with their removal time.

    A plugin counts only when it has a removed entry whose directory is gone
    and no entry in any root that is active or not provably gone. Absence
    from the lock is never evidence (workbench projects and other sources
    outside the managed roots are not in it), and neither is a builtin
    tombstone. Returns ``None`` when the lock cannot be trusted for this.

    Reconcile does not refresh the plugin_id of an entry whose directory
    stays in place, so every directory that may still be installed protects
    both its recorded id and the id its manifest declares now; a manifest
    that cannot be read skips the pass.
    """
    if manager is None or manager.is_degraded:
        return None
    roots = {"builtin": manager.builtin_root, "user": manager.user_root}
    active: set[str] = set()
    removed_at: dict[str, datetime] = {}
    for entry in manager.list_entries(include_removed=True):
        root = roots.get(entry.root_id)
        if entry.removed and _directory_gone(root, entry.directory_name):
            when = _parse_time(entry.removed_at)
            # Builtins cannot be uninstalled (mark_removed rejects them); a
            # builtin tombstone only means the directory vanished, e.g. on
            # a branch switch or an update, which is no uninstall.
            if not entry.plugin_id or when is None or entry.root_id == "builtin":
                continue
            if entry.plugin_id not in removed_at or when > removed_at[entry.plugin_id]:
                removed_at[entry.plugin_id] = when
            continue
        # An active entry, or a tombstone whose directory may still be there
        # (root unmounted, listing failed, symlinked): the plugin may be
        # installed under its recorded id or under the id its manifest
        # declares now, since reconcile keeps a recorded id once set.
        if entry.plugin_id:
            active.add(entry.plugin_id)
        manifest = _manifest_plugin_id_state(root, entry.directory_name)
        if manifest is _MANIFEST_UNKNOWN:
            # Could be any of the removed plugins: prune nothing this time.
            logger.debug(
                "uninstalled plugin grants not pruned: manifest unreadable root={} directory={}",
                entry.root_id,
                entry.directory_name,
            )
            return None
        if manifest:
            active.add(manifest)
    return {pid: when for pid, when in removed_at.items() if pid not in active}


def _plugin_ids_in_use() -> set[str] | None:
    """Ids served from outside the lock: registered plugins and workbench projects.

    The registry may not be populated yet at startup (it is refreshed after
    the install-source reconcile in embedded mode), so registered ids are a
    best-effort extra; the lock and the workbench store are the checks that
    always apply. Returns ``None`` when a workbench project cannot be read,
    since its plugin id is then unknown.
    """
    from plugin.core.state import state
    from plugin.server.application.plugins.project_sources import (
        list_project_sources_sync,
    )

    with state.acquire_plugins_read_lock():
        in_use = set(state.plugins)
    try:
        sources, failures = list_project_sources_sync()
    except Exception as exc:
        logger.warning(
            "workbench projects unreadable, uninstalled plugin grants kept: err_type={}",
            type(exc).__name__,
        )
        return None
    if failures:
        logger.info(
            "workbench project unreadable, uninstalled plugin grants kept: failures={}",
            len(failures),
        )
        return None
    in_use.update(source.plugin_id for source in sources)
    return in_use


def prune_uninstalled_plugin_grants(manager) -> list[str]:
    """Drop grant records left behind by plugins that were uninstalled.

    An uninstall leaves the records in place when the permission store was
    unusable at the time (see ``UNUSABLE_STORE_CODES``). Run once at startup,
    after the install-source reconcile, under the plugin operation lock.

    A record is dropped only when the lock proves the plugin uninstalled, no
    registered plugin or workbench project uses the id, and the record was
    last updated before the removal. A degraded lock or an unreadable store
    skips the pass; the next startup tries again. Never raises; returns the
    dropped plugin ids.
    """
    try:
        cutoffs = _uninstalled_plugin_cutoffs(manager)
        if not cutoffs:
            return []
        in_use = _plugin_ids_in_use()
        if in_use is None:
            return []
        for plugin_id in in_use:
            cutoffs.pop(plugin_id, None)
        if not cutoffs:
            return []

        def _older_than_removal(plugin_id: str, record: dict) -> bool:
            if "updated_at" not in record:
                return True
            updated = _parse_time(record.get("updated_at"))
            # An unreadable timestamp is not evidence the record is stale.
            return updated is not None and updated <= cutoffs[plugin_id]

        dropped = permission_store.forget_many(
            cutoffs, should_forget=_older_than_removal
        )
    except ServerDomainError as exc:
        logger.warning(
            "uninstalled plugin grants not pruned, retried next startup: code={}",
            exc.code,
        )
        return []
    except Exception as exc:
        logger.warning(
            "uninstalled plugin grants not pruned, retried next startup: err_type={}",
            type(exc).__name__,
        )
        return []
    if dropped:
        logger.info("dropped grants of uninstalled plugins: plugin_ids={}", dropped)
    return dropped


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
# Consecutive failed reads per plugin: the first is logged as an error, then
# one line per READ_FAILURE_LOG_EVERY more (the push service retries them).
READ_FAILURE_LOG_EVERY = 10
_read_failures: dict[str, int] = {}


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


def forget_remembered_permissions(plugin_id: str) -> None:
    """Drop the in-process copy of the plugin's last read grants without
    touching the store.

    For an uninstall that could not clear an unusable store: nothing is
    written, so no change notification drops the copy, and a later read
    failure would otherwise hand the removed plugin's grants to whatever
    runs under the same id in this process. The read-failure count goes
    too, so the next plugin under this id logs its first failure.
    """
    _invalidate_last_records(frozenset({plugin_id}))
    with _snapshot_lock:
        _read_failures.pop(plugin_id, None)


def _on_store_change(plugin_ids: frozenset[str] | None) -> None:
    """Store change listener: drop remembered records the write made stale,
    so a later read failure never reports a removed or replaced grant."""
    _invalidate_last_records(plugin_ids)


def _generation(plugin_id: str) -> tuple[int, int]:
    return _reloads, _removals.get(plugin_id, 0)


def _read_and_remember(plugin_id: str, revision: int) -> dict:
    """Read the plugin's record and remember it for read failures, unless its
    grants changed while reading. A store (re)load during the read, usually
    triggered by this very read, gets one re-read under the new generation;
    if that re-read fails, the first read is used but not remembered."""
    with _snapshot_lock:
        generation = _generation(plugin_id)
    record = permission_record(plugin_id)
    with _snapshot_lock:
        current = _generation(plugin_id)
        if current == generation:
            _remember(plugin_id, revision, record)
            return record
    if current[1] != generation[1]:
        # This plugin's grants changed meanwhile: use the read for this
        # snapshot, remember nothing (the change schedules a new push).
        return record
    generation = current
    try:
        again = permission_record(plugin_id)
    except Exception as exc:
        logger.warning(
            "plugin permissions re-read after a store reload failed; using the first read: plugin_id={}, error={}",
            plugin_id,
            getattr(exc, "code", None) or type(exc).__name__,
        )
        return record
    with _snapshot_lock:
        if _generation(plugin_id) == generation:
            _remember(plugin_id, revision, again)
    return again


def _remember(plugin_id: str, revision: int, record: dict) -> None:
    # Caller holds _snapshot_lock. Snapshots read concurrently: only a newer
    # read replaces an older one.
    if _last_records.get(plugin_id, (0, None))[0] < revision:
        _last_records[plugin_id] = (revision, copy.deepcopy(record))


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


def resolved_states(
    plugin_id: str, *, read: bool = True
) -> tuple[int, dict[str, PermissionState], bool]:
    """The plugin's resolved permission states: ``(revision, states,
    read_failed)``. Never raises.

    The one place permission states are worked out, for the plugin process
    (:func:`permission_snapshot`) and for the host's own checks alike, so
    both see the same state. When the grants cannot be read (or ``read`` is
    False, for a caller backing off after a failure) the last record read
    for this plugin is used; when none was ever read, enforced permissions
    are denied and disclosure-only ones undecided. While the master switch
    is on every permission is ``enforced=False``; the status is unchanged.
    """
    from plugin.core.capability_policy import checks_disabled

    # Numbered before reading: a snapshot that starts after a write always
    # outranks one that may have read the data from before it.
    with _snapshot_lock:
        revision = next(_snapshot_revision)
    read_failed = False
    if not read:
        read_failed = True
        with _snapshot_lock:
            record = copy.deepcopy(_last_records.get(plugin_id, (0, None))[1])
    else:
        try:
            record = _read_and_remember(plugin_id, revision)
        except Exception as exc:
            code = getattr(exc, "code", None)
            read_failed = True
            with _snapshot_lock:
                record = copy.deepcopy(_last_records.get(plugin_id, (0, None))[1])
                failures = _read_failures.get(plugin_id, 0) + 1
                _read_failures[plugin_id] = failures
            if failures == 1 or failures % READ_FAILURE_LOG_EVERY == 0:
                logger.error(
                    "plugin permissions could not be read: plugin_id={}, error={}, using={}, "
                    "failures_in_a_row={}",
                    plugin_id,
                    code or type(exc).__name__,
                    "last read" if record is not None else "nothing (enforced permissions count as denied)",
                    failures,
                )
        else:
            with _snapshot_lock:
                recovered = _read_failures.pop(plugin_id, None)
            if recovered:
                logger.info("plugin permissions can be read again: plugin_id={}", plugin_id)
    try:
        checks_off = checks_disabled()
    except Exception as exc:
        # The switch only ever turns checks off; if it cannot be read, keep them on.
        logger.error("permission master switch could not be read: error={}", type(exc).__name__)
        checks_off = False
    states = {
        permission: _resolve_state(permission, record, checks_off=checks_off)
        for permission in PERMISSIONS
    }
    return revision, states, read_failed


def permission_snapshot(plugin_id: str) -> dict:
    """The plugin's resolved permission states, in the shape the plugin
    process takes (see ``plugin.sdk.shared.permissions.runtime``). Never
    raises; see :func:`resolved_states`.
    """
    from plugin.sdk.shared.permissions.runtime import state_to_wire

    revision, states, read_failed = resolved_states(plugin_id)
    snapshot: dict = {
        "revision": revision,
        "states": {permission: state_to_wire(state) for permission, state in states.items()},
    }
    if read_failed:
        # Host-side marker (not sent): the push service retries until a read works.
        snapshot["read_failed"] = True
    return snapshot


def record_states(record: dict | None, *, checks_off: bool) -> dict[str, PermissionState]:
    """Every permission's state for ``record`` (as returned by
    :func:`permission_record`), with the same rules the plugin process and the
    host's checks use (see :func:`resolved_states`)."""
    return {
        permission: _resolve_state(permission, record, checks_off=checks_off)
        for permission in PERMISSIONS
    }
