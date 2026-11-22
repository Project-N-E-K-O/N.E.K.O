"""Persistent per-plugin permission grants.

Grants live in ``plugin_permissions.json`` under the runtime config directory.
Each plugin has one record that is bound to the plugin's install source
(channel plus a stable fingerprint); a grant only applies to the source it was
given for.

Document shape (``schema_version`` 1)::

    {
      "schema_version": 1,
      "imported_at": "<iso>",            # when legacy decisions were imported
      "legacy_import": {"status": "...", "plugins": 0, "skipped": 0},
      "plugins": {
        "<plugin_id>": {
          "source": {"channel": "market", "fingerprint": "..."} | null,
          "legacy_denied": true,       # optional, see LEGACY_DENIED
          "permissions": {
            "<permission id>": {
              "state": "granted" | "denied" | "undecided",
              "condition": "anytime" | "in_use",   # not on "undecided"
              "legacy_any": true,      # optional
              "scope": "all",
              "decided_at": "<iso>"
            }
          },
          "updated_at": "<iso>"
        }
      }
    }

A permission missing from ``permissions`` is undecided, except on a record
with ``legacy_denied`` (see LEGACY_DENIED), where an explicit ``undecided``
entry is stored instead.

On the first load the decisions recorded in the legacy ``plugin_consents.json``
are imported once. The legacy file is only ever read here, never written.
"""

from __future__ import annotations

import copy
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable

from plugin.logging_config import get_logger
from plugin.server.domain.errors import ServerDomainError
from utils.file_utils import read_json_tolerating_replace

logger = get_logger("server.infrastructure.plugin_permissions")

FILENAME = "plugin_permissions.json"
SCHEMA_VERSION = 1

LEGACY_FILENAME = "plugin_consents.json"
LEGACY_SCHEMA_VERSION = 2

# Permission ids that legacy capability names map to.
# TODO: import these from plugin.sdk.shared.permissions (catalog) once it lands;
# names and values here mirror that vocabulary so the swap is a pure import.
MEMORY_READ = "plugin_sdk.main_agent.memory:read"
CONVERSATIONS_READ = "plugin_sdk.main_agent.conversations:read"
MESSAGES_PUSH = "plugin_sdk.main_agent.messages:push"

LEGACY_PERMISSION_NAMES: dict[str, str] = {
    "conversations.read": CONVERSATIONS_READ,
    "memory.read": MEMORY_READ,
    "messages.push": MESSAGES_PUSH,
}

STATE_GRANTED = "granted"
STATE_DENIED = "denied"
STATE_UNDECIDED = "undecided"
STATES = frozenset({STATE_GRANTED, STATE_DENIED})
_STORED_STATES = STATES | {STATE_UNDECIDED}

CONDITION_ANYTIME = "anytime"
CONDITION_IN_USE = "in_use"
CONDITIONS = frozenset({CONDITION_ANYTIME, CONDITION_IN_USE})

SCOPE_ALL = "all"

# Set on a plugin record imported from a legacy whole-plugin denial: every
# implicitly declared permission of that plugin counts as denied unless the
# record holds an explicit entry for it (including an explicit "undecided").
LEGACY_DENIED = "legacy_denied"


def normalize_permission(name: str) -> str:
    """Map a legacy capability name to its permission id; other ids pass through."""
    return LEGACY_PERMISSION_NAMES.get(name, name)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass(frozen=True)
class PluginSource:
    """Where an installed plugin came from: install channel + stable fingerprint."""

    channel: str
    fingerprint: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {"channel": self.channel, "fingerprint": self.fingerprint}

    @classmethod
    def from_dict(cls, value: Any) -> "PluginSource | None":
        if not isinstance(value, dict) or not isinstance(value.get("channel"), str):
            return None
        fingerprint = value.get("fingerprint")
        return cls(value["channel"], fingerprint if isinstance(fingerprint, str) else None)


SourceResolver = Callable[[str], "PluginSource | None"]


# Stable error codes for a store that cannot be read. Callers (and API
# clients) can rely on these; see ``READ_ERROR_CODES``.
READ_FAILED = "PLUGIN_PERMISSIONS_READ_FAILED"  # I/O error, may be transient
CORRUPT = "PLUGIN_PERMISSIONS_CORRUPT"  # content is not a valid store
UNSUPPORTED_VERSION = "PLUGIN_PERMISSIONS_UNSUPPORTED_VERSION"  # written by a newer version
READ_ERROR_CODES = frozenset({READ_FAILED, CORRUPT, UNSUPPORTED_VERSION})
WRITE_FAILED = "PLUGIN_PERMISSIONS_WRITE_FAILED"


def _read_failed(message: str, *, file: str = FILENAME) -> ServerDomainError:
    return ServerDomainError(
        READ_FAILED, message, 503, details={"file": file, "retryable": True}
    )


class _UnsupportedVersion(ValueError):
    def __init__(self, found: int):
        super().__init__("Unsupported permission document version")
        self.found = found


def _validate_document(raw: Any) -> dict:
    if not isinstance(raw, dict):
        raise ValueError("Invalid permission document")
    version = raw.get("schema_version")
    if not isinstance(version, int) or isinstance(version, bool):
        raise ValueError("Invalid permission document version")
    if version > SCHEMA_VERSION:
        raise _UnsupportedVersion(version)
    if version != SCHEMA_VERSION:
        raise ValueError("Invalid permission document version")
    plugins = raw.get("plugins")
    if not isinstance(plugins, dict):
        raise ValueError("Invalid permission document")
    for record in plugins.values():
        if not isinstance(record, dict) or not isinstance(
            record.get("permissions", {}), dict
        ):
            raise ValueError("Invalid permission record")
        for entry in record.get("permissions", {}).values():
            state = entry.get("state") if isinstance(entry, dict) else None
            if not isinstance(state, str) or state not in _STORED_STATES:
                raise ValueError("Invalid permission entry")
    return raw


def _load_error(exc: Exception) -> ServerDomainError:
    if isinstance(exc, _UnsupportedVersion):
        return ServerDomainError(
            UNSUPPORTED_VERSION,
            "Plugin permission records were saved by a newer version of N.E.K.O "
            "and cannot be read by this version",
            500,
            details={
                "file": FILENAME,
                "schema_version": exc.found,
                "supported_schema_version": SCHEMA_VERSION,
                "retryable": False,
            },
        )
    if isinstance(exc, OSError):
        return _read_failed("Plugin permission records could not be read")
    return ServerDomainError(
        CORRUPT,
        "Plugin permission records are damaged and cannot be read",
        500,
        details={"file": FILENAME, "retryable": False},
    )


class PluginPermissionStore:
    def __init__(self, manager=None, *, source_resolver: SourceResolver | None = None):
        self._manager_override = manager
        self._source_resolver = source_resolver
        self._lock = threading.RLock()
        self._path = None
        self._document: dict | None = None
        # True while a first-use import is held in memory only.
        self._unsaved = False

    # ------------------------------------------------------------------
    # Loading
    # ------------------------------------------------------------------

    def _location(self):
        from utils.config_manager import get_config_manager

        try:
            manager = self._manager_override or get_config_manager()
            path = manager.get_runtime_config_path(FILENAME)
        except (OSError, ValueError) as exc:
            raise _read_failed("Plugin permission storage is unavailable") from exc
        return manager, path

    def _load(self):
        manager, path = self._location()
        if self._document is not None and self._path == path:
            return manager
        try:
            document = _validate_document(read_json_tolerating_replace(path))
        except FileNotFoundError:
            document = None
        except (OSError, ValueError, UnicodeError) as exc:
            # The file is left untouched: overwriting it would drop every
            # grant on disk. Nothing is cached, so the next call reads again.
            logger.error(
                "plugin permission store could not be read: error={}",
                type(exc).__name__,
            )
            raise _load_error(exc) from exc
        unsaved = False
        if document is None:
            document = self._initial_document(path.with_name(LEGACY_FILENAME))
            try:
                self._save(manager, document)
            except ServerDomainError:
                # Reads are served from memory; every write first persists the
                # import (see _load_for_write). Until then a restart simply
                # imports the unchanged legacy file again.
                logger.error("imported plugin permissions are held in memory until the next save")
                unsaved = True
        self._path, self._document = path, document
        self._unsaved = unsaved
        return manager

    def _load_for_write(self):
        """Load for a mutation: an import still held in memory is saved first,
        so a write never reports success while the store is not on disk."""
        manager = self._load()
        if self._unsaved:
            self._save(manager, self._document)
            self._unsaved = False
        return manager

    def _initial_document(self, legacy_path) -> dict:
        plugins, summary = self._import_legacy(legacy_path)
        return {
            "schema_version": SCHEMA_VERSION,
            "imported_at": _now(),
            "legacy_import": summary,
            "plugins": plugins,
        }

    def _import_legacy(self, legacy_path) -> tuple[dict, dict]:
        """Read legacy consent decisions; the legacy file is never modified."""
        try:
            raw = read_json_tolerating_replace(legacy_path)
        except FileNotFoundError:
            return {}, {"status": "absent", "plugins": 0, "skipped": 0}
        except OSError as exc:
            # Possibly transient: do not create the new file, so the import is
            # attempted again on the next load instead of being lost.
            logger.error(
                "legacy plugin consents could not be read: error={}",
                type(exc).__name__,
            )
            raise _read_failed(
                "Legacy plugin consent records could not be read", file=LEGACY_FILENAME
            ) from exc
        except (ValueError, UnicodeError) as exc:
            logger.error(
                "legacy plugin consents are not valid JSON and were not imported: error={}",
                type(exc).__name__,
            )
            return {}, {"status": "invalid", "plugins": 0, "skipped": 0}
        if (
            not isinstance(raw, dict)
            or raw.get("schema_version") != LEGACY_SCHEMA_VERSION
            or not isinstance(raw.get("plugins"), dict)
        ):
            logger.error("legacy plugin consents have an unsupported shape and were not imported")
            return {}, {"status": "invalid", "plugins": 0, "skipped": 0}

        plugins: dict[str, dict] = {}
        skipped = 0
        for plugin_id, legacy in raw["plugins"].items():
            record = self._import_legacy_record(plugin_id, legacy)
            if record is None:
                skipped += 1
                continue
            plugins[plugin_id] = record
        if skipped:
            logger.warning(
                "skipped {} legacy plugin consent record(s) during import", skipped
            )
        return plugins, {"status": "imported", "plugins": len(plugins), "skipped": skipped}

    def _import_legacy_record(self, plugin_id, legacy) -> dict | None:
        if not isinstance(plugin_id, str) or not plugin_id or not isinstance(legacy, dict):
            return None
        decision = legacy.get("decision")
        now = _now()
        decided_at = legacy.get("decided_at")
        if not isinstance(decided_at, str) or not decided_at:
            decided_at = now
        record: dict[str, Any] = {"permissions": {}, "updated_at": now}
        if decision == "approved":
            names = legacy.get("approved_capabilities")
            if not isinstance(names, list):
                return None
            for name in names:
                permission_id = LEGACY_PERMISSION_NAMES.get(name) if isinstance(name, str) else None
                if permission_id is None:
                    logger.warning(
                        "legacy capability {!r} has no permission mapping; not imported",
                        name,
                    )
                    continue
                record["permissions"][permission_id] = {
                    "state": STATE_GRANTED,
                    "condition": CONDITION_ANYTIME,
                    "legacy_any": True,
                    "scope": SCOPE_ALL,
                    "decided_at": decided_at,
                }
        elif decision == "denied":
            record[LEGACY_DENIED] = True
        else:
            return None
        # Legacy records carry no source; bind the current install record.
        # When it is unknown the record is bound on the first known source.
        source = self._resolve_source(plugin_id)
        record["source"] = source.to_dict() if source is not None else None
        return record

    def _resolve_source(self, plugin_id: str) -> PluginSource | None:
        if self._source_resolver is None:
            return None
        try:
            return self._source_resolver(plugin_id)
        except Exception as exc:
            logger.warning(
                "could not resolve plugin source: plugin_id={}, error={}",
                plugin_id,
                type(exc).__name__,
            )
            return None

    # ------------------------------------------------------------------
    # Saving
    # ------------------------------------------------------------------

    def _save(self, manager, document: dict) -> None:
        try:
            manager.save_json_config(FILENAME, document)
        except Exception as exc:
            logger.error(
                "plugin permission store could not be saved: error={}",
                type(exc).__name__,
            )
            raise ServerDomainError(
                WRITE_FAILED,
                "Plugin permission records could not be saved",
                503,
            ) from exc

    def _commit(self, manager, document: dict) -> None:
        # Persist first; the cache only moves once the disk has the change.
        self._save(manager, document)
        self._document = document
        self._unsaved = False

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def legacy_import(self) -> dict:
        with self._lock:
            self._load()
            return {
                "imported_at": self._document.get("imported_at"),
                **copy.deepcopy(self._document.get("legacy_import") or {}),
            }

    def read(self, plugin_id: str) -> dict:
        with self._lock:
            self._load()
            return copy.deepcopy(self._document["plugins"].get(plugin_id, {}))

    def view(self, plugin_id: str, current: PluginSource | None) -> dict:
        """The record as it applies to ``current``, without saving anything.

        Same rules as :meth:`sync_source`; read paths use this so a source
        seen in passing (e.g. mid-uninstall) is never persisted.
        """
        with self._lock:
            self._load()
            record = copy.deepcopy(self._document["plugins"].get(plugin_id) or {})
        if record:
            known = PluginSource.from_dict(record.get("source"))
            record["source_mismatch"] = (
                known is not None and current is not None and known != current
            )
            self._apply_source(record, current)
        return record

    def sync_source(self, plugin_id: str, current: PluginSource | None) -> dict:
        """Reconcile a record with the plugin's current source and return it.

        This persists the result, so callers must hold the plugin operation
        lock to keep it ordered with install / uninstall.

        * ``current is None`` (source unavailable): keep the last known source.
        * Record has no source yet: bind ``current``; this is not a change.
        * Both known and different: the grants are discarded and the record is
          rebound to ``current``.
        """
        with self._lock:
            # Decide on the loaded data first; only a needed write goes
            # through _load_for_write (which may first save a pending import).
            self._load()
            cached = self._document["plugins"].get(plugin_id)
            if cached is None or current is None:
                return copy.deepcopy(cached or {})
            known = PluginSource.from_dict(cached.get("source"))
            if known == current:
                return copy.deepcopy(cached)
            document = copy.deepcopy(self._document)
            record = document["plugins"][plugin_id]
            self._apply_source(record, current)
            if known is None:
                # First binding is not a source change; a failed write
                # (including saving a pending import) must not turn a read
                # into an error. The next call retries.
                try:
                    manager = self._load_for_write()
                    self._commit(manager, document)
                except ServerDomainError:
                    logger.error(
                        "plugin source binding was not saved: plugin_id={}", plugin_id
                    )
                return copy.deepcopy(record)
            manager = self._load_for_write()
            self._commit(manager, document)
            return copy.deepcopy(record)

    @staticmethod
    def _apply_source(record: dict, current: PluginSource | None) -> bool:
        if current is None:
            return False
        known = PluginSource.from_dict(record.get("source"))
        if known == current:
            return False
        now = _now()
        if known is not None:
            record["permissions"] = {}
            record.pop(LEGACY_DENIED, None)
            record["source_changed_at"] = now
        record["source"] = current.to_dict()
        record["updated_at"] = now
        return True

    def decide(
        self,
        plugin_id: str,
        permission_id: str,
        state: str,
        *,
        condition: str = CONDITION_IN_USE,
        source: PluginSource | None = None,
    ) -> dict:
        """Record a user decision for one permission.

        The decision replaces any previous one, including an imported
        ``legacy_any`` grant.
        """
        if not plugin_id:
            raise ValueError("plugin_id is required")
        permission_id = normalize_permission(permission_id)
        if not permission_id:
            raise ValueError("permission_id is required")
        if state not in STATES:
            raise ValueError(f"Unsupported permission state: {state!r}")
        if condition not in CONDITIONS:
            raise ValueError(f"Unsupported permission condition: {condition!r}")
        with self._lock:
            manager = self._load_for_write()
            document = copy.deepcopy(self._document)
            record = document["plugins"].get(plugin_id)
            if record is None:
                record = document["plugins"][plugin_id] = {
                    "source": source.to_dict() if source is not None else None,
                    "permissions": {},
                }
            else:
                self._apply_source(record, source)
            now = _now()
            record.setdefault("permissions", {})[permission_id] = {
                "state": state,
                "condition": condition,
                "scope": SCOPE_ALL,
                "decided_at": now,
            }
            record["updated_at"] = now
            self._commit(manager, document)
            return copy.deepcopy(record)

    def clear(self, plugin_id: str, permission_id: str) -> dict:
        """Return one permission to undecided.

        On a ``legacy_denied`` record a missing entry still means denied, so
        an explicit ``undecided`` entry is stored instead of deleting it.
        """
        permission_id = normalize_permission(permission_id)
        with self._lock:
            manager = self._load_for_write()
            record = self._document["plugins"].get(plugin_id)
            if record is None:
                return {}
            entry = record.get("permissions", {}).get(permission_id)
            if record.get(LEGACY_DENIED):
                if entry is not None and entry.get("state") == STATE_UNDECIDED:
                    return copy.deepcopy(record)
            elif entry is None:
                return copy.deepcopy(record)
            document = copy.deepcopy(self._document)
            record = document["plugins"][plugin_id]
            now = _now()
            permissions = record.setdefault("permissions", {})
            if record.get(LEGACY_DENIED):
                permissions[permission_id] = {
                    "state": STATE_UNDECIDED,
                    "scope": SCOPE_ALL,
                    "decided_at": now,
                }
            else:
                del permissions[permission_id]
            record["updated_at"] = now
            self._commit(manager, document)
            return copy.deepcopy(record)

    def apply_legacy_decision(
        self,
        plugin_id: str,
        decision: str | None,
        capabilities: list[str] | tuple[str, ...] = (),
        *,
        newly_approved: list[str] | tuple[str, ...] | None = None,
        source: PluginSource | None = None,
    ) -> dict:
        """Mirror a decision made through the existing consent flow.

        Transitional: kept only while the consent dialog is still a writer;
        remove together with that dialog.

        ``decision`` is ``"approved"`` (``capabilities`` are the legacy names
        approved in total, ``newly_approved`` the ones the user was asked
        about this time; only those create grants, earlier approvals are kept
        only if they are still stored, e.g. not discarded by a source
        change), ``"denied"`` or ``None`` (revoked). Only entries
        that came from the consent flow (``legacy_any``) and the
        ``legacy_denied`` marker are touched; decisions made in the permission
        flow itself are kept. Saving uses the same failure rules as any
        write; the call is idempotent, so retrying converges.
        """
        if decision not in ("approved", "denied", None):
            raise ValueError(f"Unsupported consent decision: {decision!r}")
        approved: set[str] = set()
        asked: set[str] = set()
        if decision == "approved":
            if newly_approved is None:
                newly_approved = capabilities
            asked = {
                LEGACY_PERMISSION_NAMES[name]
                for name in newly_approved
                if name in LEGACY_PERMISSION_NAMES
            }
            for name in capabilities:
                permission_id = LEGACY_PERMISSION_NAMES.get(name)
                if permission_id is None:
                    logger.warning(
                        "legacy capability {!r} has no permission mapping; not mirrored",
                        name,
                    )
                    continue
                approved.add(permission_id)
        with self._lock:
            manager = self._load_for_write()
            document = copy.deepcopy(self._document)
            plugins = document["plugins"]
            record = plugins.get(plugin_id)
            if record is None:
                if decision is None:
                    return {}
                record = plugins[plugin_id] = {
                    "source": source.to_dict() if source is not None else None,
                    "permissions": {},
                }
            else:
                self._apply_source(record, source)
            now = _now()
            permissions = record.setdefault("permissions", {})
            for permission_id in [
                key for key, entry in permissions.items() if entry.get("legacy_any")
            ]:
                if permission_id not in approved:
                    del permissions[permission_id]
            for permission_id in sorted(approved & asked):
                existing = permissions.get(permission_id)
                # Keep grants already mirrored and grants / denials decided in
                # the permission flow; an explicit "undecided" (from clear())
                # is no decision, so this approval replaces it.
                if existing is not None and existing.get("state") != STATE_UNDECIDED:
                    continue
                permissions[permission_id] = {
                    "state": STATE_GRANTED,
                    "condition": CONDITION_ANYTIME,
                    "legacy_any": True,
                    "scope": SCOPE_ALL,
                    "decided_at": now,
                }
            if decision == "denied":
                record[LEGACY_DENIED] = True
            else:
                record.pop(LEGACY_DENIED, None)
            if record == self._document["plugins"].get(plugin_id):
                return copy.deepcopy(record)
            record["updated_at"] = now
            self._commit(manager, document)
            return copy.deepcopy(record)

    def forget(self, plugin_id: str) -> dict | None:
        """Drop every grant of ``plugin_id``; returns the removed record."""
        with self._lock:
            manager = self._load_for_write()
            if plugin_id not in self._document["plugins"]:
                return None
            document = copy.deepcopy(self._document)
            removed = document["plugins"].pop(plugin_id)
            self._commit(manager, document)
            return removed

    def restore(self, plugin_id: str, record: dict | None) -> None:
        """Put back an earlier record (rollback only); ``None`` removes it."""
        with self._lock:
            manager = self._load_for_write()
            if self._document["plugins"].get(plugin_id) == record:
                return
            document = copy.deepcopy(self._document)
            if record is None:
                document["plugins"].pop(plugin_id, None)
            else:
                document["plugins"][plugin_id] = copy.deepcopy(record)
            self._commit(manager, document)
