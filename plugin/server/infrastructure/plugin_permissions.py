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
          "legacy_approved": true,     # optional, see LEGACY_APPROVED
          "legacy_denial_superseded": true,  # optional, see LEGACY_DENIAL_SUPERSEDED
          "grants_reset": {"at": "<iso>", "reason": "source_changed"},  # optional, see GRANTS_RESET
          "legacy_decided_at": "<iso>",  # optional, see LEGACY_DECIDED_AT
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

A permission missing from ``permissions`` is undecided, except a permission
with a legacy name (``LEGACY_IDS``) on a record with ``legacy_denied`` (see
LEGACY_DENIED): that one reads denied, so an explicit ``undecided`` entry is
stored for it instead.

When a plugin's source changes, its grants are discarded; only the
plugin-level markers in ``KEPT_ON_SOURCE_CHANGE`` stay (a denial does, an
approval does not; a superseded denial stays superseded).

On the first load the decisions recorded in the legacy ``plugin_consents.json``
are imported once. Once that import is saved, a marker file next to it
(``MARKER_FILENAME``) records it, and the legacy file is not imported again.
The legacy file itself is only ever read here, never written.

Storage errors are logged rate-limited: the first one of a kind right away,
repeats at most once per ``ERROR_LOG_WINDOW`` seconds with a count, and one
line when that kind of operation works again.
"""

from __future__ import annotations

import copy
import os
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable, Iterable

from plugin.logging_config import get_logger
from plugin.sdk.shared.permissions import LEGACY_PERMISSION_NAMES, normalize_permission
from plugin.server.domain.errors import ServerDomainError
from utils.file_utils import read_json_tolerating_replace

logger = get_logger("server.infrastructure.plugin_permissions")

FILENAME = "plugin_permissions.json"
SCHEMA_VERSION = 1

# Permissions the consent dialog's whole-plugin decision covers.
LEGACY_IDS: frozenset[str] = frozenset(LEGACY_PERMISSION_NAMES.values())

LEGACY_FILENAME = "plugin_consents.json"
LEGACY_SCHEMA_VERSION = 2
# Written once the import of LEGACY_FILENAME is saved; its presence alone
# means "already imported".
MARKER_FILENAME = "plugin_consents.imported.json"
MARKER_SCHEMA_VERSION = 1
IMPORT_ALREADY_DONE = "already_imported"

ERROR_LOG_WINDOW = 60.0


class _ErrorLog:
    """Rate-limited logging of repeated storage errors.

    Keyed by ``(operation, error type)``. Messages carry error type names
    and plugin ids only.
    """

    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self._lock = threading.Lock()
        # key -> [last logged at, suppressed since then, failures in a row]
        self._seen: dict[tuple[str, str], list] = {}

    def failed(self, operation: str, error: str, message: str, *args) -> None:
        now = self._clock()
        with self._lock:
            state = self._seen.get((operation, error))
            if state is None:
                if len(self._seen) > 256:
                    self._seen.clear()
                self._seen[(operation, error)] = [now, 0, 1]
                suppressed = None
            else:
                state[2] += 1
                if now - state[0] < ERROR_LOG_WINDOW:
                    state[1] += 1
                    return
                suppressed, state[0], state[1] = state[1], now, 0
        if suppressed:
            logger.error(message + " (repeated; {} more since the last report)", *args, suppressed)
        else:
            logger.error(message, *args)

    def recovered(self, operation: str, message: str) -> None:
        with self._lock:
            keys = [key for key in self._seen if key[0] == operation]
            failures = sum(self._seen.pop(key)[2] for key in keys)
        if failures:
            logger.info(message + " (after {} failure(s))", failures)


_errors = _ErrorLog()


STATE_GRANTED = "granted"
STATE_DENIED = "denied"
STATE_UNDECIDED = "undecided"
STATES = frozenset({STATE_GRANTED, STATE_DENIED})
_STORED_STATES = STATES | {STATE_UNDECIDED}

CONDITION_ANYTIME = "anytime"
CONDITION_IN_USE = "in_use"
CONDITIONS = frozenset({CONDITION_ANYTIME, CONDITION_IN_USE})

SCOPE_ALL = "all"

# Set on a plugin record by a whole-plugin denial from the consent flow
# (imported or decided there): every permission with a legacy name
# (LEGACY_IDS) counts as denied unless the record holds an explicit entry for
# it (including an explicit "undecided"); other permissions are not covered.
LEGACY_DENIED = "legacy_denied"
# Set on a plugin record whose whole-plugin approval came from the consent
# flow (imported or decided there). It only records that the plugin was
# approved, possibly with no permission at all (e.g. an incomplete analysis);
# it grants nothing by itself. Never set together with LEGACY_DENIED.
LEGACY_APPROVED = "legacy_approved"
# Set next to LEGACY_DENIED once the user decided a permission of the plugin
# here (decide / clear): the whole-plugin decision no longer reads denied,
# while the permissions LEGACY_DENIED covers still do. Cleared with the other
# markers by the next consent flow decision.
LEGACY_DENIAL_SUPERSEDED = "legacy_denial_superseded"
_PLUGIN_MARKERS = (LEGACY_DENIED, LEGACY_APPROVED, LEGACY_DENIAL_SUPERSEDED)

# What a source change keeps of a plugin record (besides its source): the
# one place that decides it. Every grant and every other marker is dropped;
# an empty set would make a source change ask again about everything.
KEPT_ON_SOURCE_CHANGE: frozenset[str] = frozenset({LEGACY_DENIED, LEGACY_DENIAL_SUPERSEDED})

# Set when a source change dropped decisions the user had made, so the
# permission panel can tell the user why they are asked again. Cleared by the
# next decision for that plugin.
GRANTS_RESET = "grants_reset"
# When the consent dialog last denied or revoked the whole plugin: the time the
# user answered for the permissions it covers. An approval answers only what
# it granted (their own decided_at), so it does not set this.
LEGACY_DECIDED_AT = "legacy_decided_at"
RESET_SOURCE_CHANGED = "source_changed"


ChangeListener = Callable[["frozenset[str] | None"], None]
_change_listeners: list[ChangeListener] = []
_listeners_lock = threading.Lock()


def add_change_listener(listener: ChangeListener) -> None:
    """Call ``listener`` after any store write that changed stored grants.

    It receives the ids of the plugins whose records changed, or ``None``
    when the whole document was (re)loaded and any plugin may differ. It runs
    on the writing thread while the store lock is held, so it must only
    schedule work and return.
    """
    with _listeners_lock:
        if listener not in _change_listeners:
            _change_listeners.append(listener)


def remove_change_listener(listener: ChangeListener) -> None:
    with _listeners_lock:
        if listener in _change_listeners:
            _change_listeners.remove(listener)


def _notify_change(plugin_ids: "frozenset[str] | None") -> None:
    if plugin_ids is not None and not plugin_ids:
        return
    with _listeners_lock:
        listeners = list(_change_listeners)
    for listener in listeners:
        # Tracked per listener, so one working listener does not report
        # another one as recovered.
        # By identity: two callbacks can share a name (same factory, or
        # methods of two instances of one class).
        operation = f"listener:{getattr(listener, '__qualname__', type(listener).__name__)}:{id(listener)}"
        try:
            listener(plugin_ids)
        except Exception as exc:
            _errors.failed(
                operation,
                type(exc).__name__,
                "plugin permission change listener failed: error={}",
                type(exc).__name__,
            )
        else:
            _errors.recovered(operation, "plugin permission change listener works again")


def _changed_plugins(before: dict | None, after: dict) -> frozenset[str]:
    old = (before or {}).get("plugins") or {}
    new = after.get("plugins") or {}
    return frozenset(
        plugin_id for plugin_id in old.keys() | new.keys() if old.get(plugin_id) != new.get(plugin_id)
    )


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


def _legacy_import_info(document: dict) -> dict:
    """The document's ``legacy_import`` summary; ``{}`` when it is missing or
    not a table (a hand-edited or damaged file), so loading never fails on it."""
    value = document.get("legacy_import")
    if value is None or isinstance(value, dict):
        return value or {}
    _errors.failed(
        "legacy_import_shape",
        type(value).__name__,
        "plugin permission store has a malformed legacy_import entry (ignored): type={}",
        type(value).__name__,
    )
    return {}


def _marker_present(path) -> bool:
    """Whether the legacy import was marked done next to the store ``path``.

    A marker that cannot be checked raises a read failure, so nothing is
    imported or created on an unknown answer."""
    try:
        os.stat(path.with_name(MARKER_FILENAME))
    except FileNotFoundError:
        _errors.recovered("marker_check", "legacy plugin consent import marker can be checked again")
        return False
    except OSError as exc:
        _errors.failed(
            "marker_check",
            type(exc).__name__,
            "legacy plugin consent import marker could not be checked: error={}",
            type(exc).__name__,
        )
        raise _read_failed(
            "Plugin permission storage is unavailable", file=MARKER_FILENAME
        ) from exc
    _errors.recovered("marker_check", "legacy plugin consent import marker can be checked again")
    return True


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


def _had_decisions(record: dict) -> bool:
    """Whether ``record`` holds a decision a source change would drop (an
    explicit "undecided" entry is none)."""
    return any(
        isinstance(entry, dict) and entry.get("state") in STATES
        for entry in (record.get("permissions") or {}).values()
    ) or any(
        record.get(marker) for marker in _PLUGIN_MARKERS if marker not in KEPT_ON_SOURCE_CHANGE
    )


def _mark_reset(record: dict, now: str) -> None:
    record[GRANTS_RESET] = {"at": now, "reason": RESET_SOURCE_CHANGED}


def _supersede_legacy_denial(record: dict) -> bool:
    """Mark a whole-plugin denial as replaced by a per-permission decision;
    returns whether the record changed."""
    if not record.get(LEGACY_DENIED) or record.get(LEGACY_DENIAL_SUPERSEDED):
        return False
    record[LEGACY_DENIAL_SUPERSEDED] = True
    return True


def _drop_for_source_change(record: dict) -> None:
    """Discard what no longer applies once the plugin's code may differ."""
    record["permissions"] = {}
    for marker in _PLUGIN_MARKERS:
        if marker not in KEPT_ON_SOURCE_CHANGE:
            record.pop(marker, None)


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
            _errors.failed(
                "read",
                type(exc).__name__,
                "plugin permission store could not be read: error={}",
                type(exc).__name__,
            )
            raise _load_error(exc) from exc
        _errors.recovered("read", "plugin permission store can be read again")
        unsaved = False
        if document is None:
            document = self._initial_document(path)
            try:
                self._save(manager, document)
            except ServerDomainError:
                # Reads are served from memory; every write first persists the
                # import (see _load_for_write). Until then a restart simply
                # imports the unchanged legacy file again.
                _errors.failed(
                    "pending_import",
                    "unsaved",
                    "imported plugin permissions are held in memory until the next save",
                )
                unsaved = True
        if not unsaved:
            self._mark_imported(manager, path, document)
        self._path, self._document = path, document
        self._unsaved = unsaved
        # Anything read before (or under another path) may differ now.
        _notify_change(None)
        return manager

    def _load_for_write(self):
        """Load for a mutation: an import still held in memory is saved first,
        so a write never reports success while the store is not on disk."""
        manager = self._load()
        if self._unsaved:
            self._save(manager, self._document)
            self._unsaved = False
            self._mark_imported(manager, self._path, self._document)
        return manager

    def _mark_imported(self, manager, path, document: dict) -> None:
        """Record that the legacy file has been imported, once the store
        holding the import is on disk. Best effort: a failed write is retried
        on the next load."""
        legacy = path.with_name(LEGACY_FILENAME)
        if _legacy_import_info(document).get("status") == "absent":
            # The store was created without a legacy file: there was nothing
            # to import, so a file that appeared since is not marked.
            return
        try:
            if not os.path.isfile(legacy) or _marker_present(path):
                return
            manager.save_json_config(
                MARKER_FILENAME,
                {
                    "schema_version": MARKER_SCHEMA_VERSION,
                    "imported_at": document.get("imported_at") or _now(),
                    "into": FILENAME,
                },
            )
        except Exception as exc:
            _errors.failed(
                "mark_import",
                type(exc).__name__,
                "legacy plugin consent import could not be marked: error={}",
                type(exc).__name__,
            )
        else:
            _errors.recovered("mark_import", "legacy plugin consent import is marked")

    def _initial_document(self, path) -> dict:
        if _marker_present(path):
            plugins, summary = {}, {"status": IMPORT_ALREADY_DONE, "plugins": 0, "skipped": 0}
        else:
            plugins, summary = self._import_legacy(path.with_name(LEGACY_FILENAME))
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
            _errors.recovered("legacy_read", "legacy plugin consents can be read again")
            return {}, {"status": "absent", "plugins": 0, "skipped": 0}
        except OSError as exc:
            # Possibly transient: do not create the new file, so the import is
            # attempted again on the next load instead of being lost.
            _errors.failed(
                "legacy_read",
                type(exc).__name__,
                "legacy plugin consents could not be read: error={}",
                type(exc).__name__,
            )
            raise _read_failed(
                "Legacy plugin consent records could not be read", file=LEGACY_FILENAME
            ) from exc
        except (ValueError, UnicodeError) as exc:
            _errors.recovered("legacy_read", "legacy plugin consents can be read again")
            logger.error(
                "legacy plugin consents are not valid JSON and were not imported: error={}",
                type(exc).__name__,
            )
            return {}, {"status": "invalid", "plugins": 0, "skipped": 0}
        _errors.recovered("legacy_read", "legacy plugin consents can be read again")
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
            record[LEGACY_APPROVED] = True
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
            _errors.failed(
                "save",
                type(exc).__name__,
                "plugin permission store could not be saved: error={}",
                type(exc).__name__,
            )
            raise ServerDomainError(
                WRITE_FAILED,
                "Plugin permission records could not be saved",
                503,
            ) from exc
        _errors.recovered("save", "plugin permission store can be saved again")
        _errors.recovered("pending_import", "imported plugin permissions are saved")

    def _commit(self, manager, document: dict) -> None:
        # Persist first; the cache only moves once the disk has the change.
        self._save(manager, document)
        before, self._document = self._document, document
        was_unsaved, self._unsaved = self._unsaved, False
        if was_unsaved:
            # This save also put a pending first-use import on disk.
            self._mark_imported(manager, self._path, document)
        _notify_change(_changed_plugins(before, document))

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def legacy_import(self) -> dict:
        with self._lock:
            self._load()
            return {
                "imported_at": self._document.get("imported_at"),
                **copy.deepcopy(_legacy_import_info(self._document)),
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
            had_reset = GRANTS_RESET in record
            saved_change = record.get("source_changed_at")
            self._apply_source(record, current)
            if GRANTS_RESET in record and not had_reset:
                # Not saved yet: no time to show (it would move on every read).
                record[GRANTS_RESET] = {**record[GRANTS_RESET], "at": None}
            # Only a saved source change time counts (it is a cutoff for
            # earlier refusals); an unsaved one would be "now" on every read.
            if saved_change is None:
                record.pop("source_changed_at", None)
            else:
                record["source_changed_at"] = saved_change
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
                except ServerDomainError as exc:
                    _errors.failed(
                        "bind",
                        exc.code,
                        "plugin source binding was not saved: plugin_id={}",
                        plugin_id,
                    )
                else:
                    _errors.recovered("bind", "plugin source bindings are saved again")
                return copy.deepcopy(record)
            manager = self._load_for_write()
            self._commit(manager, document)
            return copy.deepcopy(record)

    def bind_unbound_source(self, plugin_id: str, source: PluginSource) -> bool:
        """Bind a record that has no source yet to ``source`` and save it.

        For a caller that has confirmed the plugin's source by other means
        before changing it. A record that already has a source, or no record,
        is left alone. Unlike :meth:`sync_source`, a failed save raises; that
        includes a first-use import still held in memory that holds this
        plugin's record, so the record is on disk before the source changes.
        Returns whether the record was bound.
        """
        with self._lock:
            self._load()
            cached = self._document["plugins"].get(plugin_id)
            if cached is None:
                return False
            if PluginSource.from_dict(cached.get("source")) is not None:
                self._load_for_write()
                return False
            document = copy.deepcopy(self._document)
            self._apply_source(document["plugins"][plugin_id], source)
            manager = self._load_for_write()
            self._commit(manager, document)
            return True

    def holds_unbound_grant(self, plugin_id: str) -> bool:
        """Whether the record has no source yet and still grants a permission.

        For a caller about to change the plugin's source without knowing the
        current one. As in :meth:`bind_unbound_source`, no record is left
        alone, and for an existing record a first-use import still held in
        memory is saved first, so a failed save raises.
        """
        with self._lock:
            self._load()
            if plugin_id not in self._document["plugins"]:
                return False
            self._load_for_write()
            record = self._document["plugins"].get(plugin_id)
            if not record or PluginSource.from_dict(record.get("source")) is not None:
                return False
            permissions = record.get("permissions") or {}
            return any(
                isinstance(entry, dict) and entry.get("state") == STATE_GRANTED
                for entry in permissions.values()
            )

    @staticmethod
    def _apply_source(record: dict, current: PluginSource | None) -> bool:
        if current is None:
            return False
        known = PluginSource.from_dict(record.get("source"))
        if known == current:
            return False
        now = _now()
        if known is not None:
            if _had_decisions(record):
                _mark_reset(record, now)
            _drop_for_source_change(record)
            record["source_changed_at"] = now
        record["source"] = current.to_dict()
        record["updated_at"] = now
        return True

    def decide(
        self,
        plugin_id: str,
        permission_id: object,
        state: str,
        *,
        condition: str = CONDITION_IN_USE,
        source: PluginSource | None = None,
    ) -> dict:
        """Record a user decision for one permission.

        The decision replaces any previous one, including an imported
        ``legacy_any`` grant, and marks a ``legacy_denied`` record
        ``LEGACY_DENIAL_SUPERSEDED``.
        """
        if not plugin_id:
            raise ValueError("plugin_id is required")
        known_id = normalize_permission(permission_id)
        if known_id is None:
            raise ValueError(f"Unknown permission: {permission_id!r}")
        permission_id = known_id
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
            record.pop(GRANTS_RESET, None)
            _supersede_legacy_denial(record)
            record.setdefault("permissions", {})[permission_id] = {
                "state": state,
                "condition": condition,
                "scope": SCOPE_ALL,
                "decided_at": now,
            }
            record["updated_at"] = now
            self._commit(manager, document)
            return copy.deepcopy(record)

    def clear(
        self,
        plugin_id: str,
        permission_id: object,
        *,
        source: PluginSource | None = None,
        answer: bool = False,
    ) -> dict:
        """Return one permission to undecided.

        ``answer``: the user answered for refusals of this permission (the
        pending list's "first use" item): the time is stored even when the
        permission was undecided already, so those refusals stay answered
        after the usage index is rebuilt from the audit.

        An explicit ``undecided`` entry with the time is stored, so readers
        can tell what happened before the user's last choice; it counts as
        no decision everywhere (and on a ``legacy_denied`` record it keeps a
        missing entry of a permission with a legacy name from meaning
        denied). Under an unchanged source, a permission that is undecided
        already (an ``undecided`` entry, or no entry unless it has a legacy
        name on a ``legacy_denied`` record) is left as it is, without a
        write. A changed ``source`` is applied and saved first, like in
        :meth:`decide`. An id outside the vocabulary is accepted only when the
        record already holds it (e.g. an id the vocabulary has since
        dropped), and its entry is removed. Like :meth:`decide`, it marks a
        ``legacy_denied`` record ``LEGACY_DENIAL_SUPERSEDED`` (saved even
        when the permission was undecided already).
        """
        known_id = normalize_permission(permission_id)
        if known_id is None and not (isinstance(permission_id, str) and permission_id):
            raise ValueError(f"Unknown permission: {permission_id!r}")
        if not plugin_id:
            raise ValueError("plugin_id is required")
        with self._lock:
            manager = self._load_for_write()
            record = self._document["plugins"].get(plugin_id)
            if known_id is None:
                if record is None or permission_id not in record.get("permissions", {}):
                    raise ValueError(f"Unknown permission: {permission_id!r}")
            if record is None and not (answer and known_id is not None):
                return {}
            known = PluginSource.from_dict(record.get("source")) if record is not None else None
            source_changes = source is not None and known != source
            # Nothing to do only when the record already says undecided under
            # the current source; a source change is applied and saved first.
            superseding = (
                record is not None
                and bool(record.get(LEGACY_DENIED))
                and not record.get(LEGACY_DENIAL_SUPERSEDED)
            )
            if known_id is not None and not source_changes and not answer and not superseding:
                entry = (record.get("permissions") or {}).get(known_id)
                if isinstance(entry, dict) and entry.get("state") == STATE_UNDECIDED:
                    return copy.deepcopy(record)
                if entry is None and not (record.get(LEGACY_DENIED) and known_id in LEGACY_IDS):
                    # Undecided already (a whole-plugin denial covers only
                    # the permissions with a legacy name).
                    return copy.deepcopy(record)
            document = copy.deepcopy(self._document)
            record = document["plugins"].get(plugin_id)
            if record is None:
                record = document["plugins"][plugin_id] = {
                    "source": source.to_dict() if source is not None else None,
                    "permissions": {},
                }
            self._apply_source(record, source)
            now = _now()
            record.pop(GRANTS_RESET, None)
            _supersede_legacy_denial(record)
            permissions = record.setdefault("permissions", {})
            if known_id is None:
                # A source change above may have dropped it already.
                permissions.pop(permission_id, None)
            else:
                permissions[known_id] = {
                    "state": STATE_UNDECIDED,
                    "scope": SCOPE_ALL,
                    "decided_at": now,
                }
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
        """Record a decision made through the consent dialog.

        The consent dialog writes its decisions only here; remove this
        together with that dialog.

        ``decision`` is ``"approved"`` (``capabilities`` are the legacy names
        approved in total, ``newly_approved`` the ones the user was asked
        about this time; only those create grants, earlier approvals are kept
        only if they are still stored, e.g. not discarded by a source
        change), ``"denied"`` or ``None`` (revoked). Only entries
        that came from the consent flow (``legacy_any``) and the
        ``legacy_denied`` / ``legacy_approved`` markers are touched; decisions
        made in the permission flow itself are kept. Saving uses the same failure rules as any
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
                        "legacy capability {!r} has no permission mapping; not recorded",
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
                # Keep grants already recorded and grants / denials decided in
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
            for marker in _PLUGIN_MARKERS:
                record.pop(marker, None)
            if decision is not None:
                record.pop(GRANTS_RESET, None)
            if decision == "denied":
                record[LEGACY_DENIED] = True
                # A fresh whole-plugin denial covers the permissions that
                # flow knows about, including ones set back to undecided
                # earlier (which would otherwise read undecided).
                for permission_id in [
                    key
                    for key, entry in permissions.items()
                    if key in LEGACY_IDS and isinstance(entry, dict) and entry.get("state") == STATE_UNDECIDED
                ]:
                    del permissions[permission_id]
            elif decision == "approved":
                record[LEGACY_APPROVED] = True
            if record == self._document["plugins"].get(plugin_id):
                return copy.deepcopy(record)
            record["updated_at"] = now
            if decision != "approved":
                # Deny / revoke answer every permission the dialog covers; an
                # approval answers only what it granted (their decided_at).
                record[LEGACY_DECIDED_AT] = now
            self._commit(manager, document)
            return copy.deepcopy(record)

    def drop_grants(self, plugin_id: str) -> dict | None:
        """Drop what a source change drops, without changing the source.

        For a caller that cannot record the plugin's new source but knows
        its code changed. Keeps what ``KEPT_ON_SOURCE_CHANGE`` keeps and
        marks ``GRANTS_RESET`` when decisions were dropped; a record left
        with nothing to keep is removed. Returns the record
        before the change (``None`` when there was none).
        """
        with self._lock:
            manager = self._load_for_write()
            cached = self._document["plugins"].get(plugin_id)
            if cached is None:
                return None
            document = copy.deepcopy(self._document)
            record = document["plugins"][plugin_id]
            now = _now()
            if _had_decisions(record):
                _mark_reset(record, now)
                # The code changed: refusals up to now came from the old code.
                record["source_changed_at"] = now
            _drop_for_source_change(record)
            if not any(record.get(marker) for marker in KEPT_ON_SOURCE_CHANGE):
                if not record.get(GRANTS_RESET):
                    del document["plugins"][plugin_id]
                    self._commit(manager, document)
                    return copy.deepcopy(cached)
                # Only the reset note is left. Like a removed record, it no
                # longer claims the earlier source, so the next known source
                # is a first binding and keeps decisions made meanwhile.
                record["source"] = None
            if record == cached:
                return copy.deepcopy(cached)
            else:
                record["updated_at"] = now
            self._commit(manager, document)
            return copy.deepcopy(cached)

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

    def forget_many(
        self,
        plugin_ids: Iterable[str],
        *,
        should_forget: Callable[[str, dict], bool] | None = None,
    ) -> list[str]:
        """Drop the records of several plugins in one write.

        ``should_forget`` sees each existing record and may keep it. Returns
        the dropped plugin ids. When none is dropped nothing is saved here,
        not even an import still held in memory; the drop itself is one save
        of the whole document. (Loading a store that does not exist yet still
        persists its first-use import, as every read does.)
        """
        with self._lock:
            manager = self._load()
            document = copy.deepcopy(self._document)
            dropped = []
            for plugin_id in sorted(set(plugin_ids)):
                record = document["plugins"].get(plugin_id)
                if record is None:
                    continue
                if should_forget is not None and not should_forget(plugin_id, record):
                    continue
                del document["plugins"][plugin_id]
                dropped.append(plugin_id)
            if dropped:
                self._commit(manager, document)
            return dropped

    def restore(self, plugin_id: str, record: dict | None) -> None:
        """Put back an earlier record (rollback only); ``None`` removes it."""
        with self._lock:
            self._load()
            if self._document["plugins"].get(plugin_id) == record:
                return
            manager = self._load_for_write()
            document = copy.deepcopy(self._document)
            if record is None:
                document["plugins"].pop(plugin_id, None)
            else:
                document["plugins"][plugin_id] = copy.deepcopy(record)
            self._commit(manager, document)
