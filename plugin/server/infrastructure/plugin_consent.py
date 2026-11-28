"""The earlier per-plugin consent decisions (``plugin_consents.json``).

Decisions now live in the permission store, which imports this file once
(see ``plugin_permissions``). Nothing writes decisions here any more; the
only writes left remove entries: an uninstalled plugin's entry, and a
plugin's denied entry once a per-permission grant replaces it (see
``permission_service``).
"""

from __future__ import annotations

import copy
import threading

from plugin.server.domain.errors import ServerDomainError
from utils.file_utils import read_json_tolerating_replace

FILENAME = "plugin_consents.json"
SCHEMA_VERSION = 2


class PluginConsentStore:
    def __init__(self, manager=None):
        self._manager_override = manager
        self._lock = threading.RLock()
        self._path = None
        self._records: dict | None = None

    def _location(self):
        from utils.config_manager import get_config_manager

        try:
            manager = self._manager_override or get_config_manager()
            path = manager.get_runtime_config_path(FILENAME)
        except (OSError, ValueError) as exc:
            raise ServerDomainError(
                "PLUGIN_CONSENT_READ_FAILED",
                "Plugin consent storage is unavailable",
                503,
            ) from exc
        return manager, path

    def _load(self):
        manager, path = self._location()
        if self._records is None or self._path != path:
            try:
                raw = read_json_tolerating_replace(path)
                if (
                    not isinstance(raw, dict)
                    or raw.get("schema_version") != SCHEMA_VERSION
                    or not isinstance(raw.get("plugins"), dict)
                ):
                    raise ValueError("Invalid consent document")
                records = raw["plugins"]
                if any(not isinstance(record, dict) for record in records.values()):
                    raise ValueError("Invalid consent record")
            except FileNotFoundError:
                records = {}
            except (OSError, ValueError, UnicodeError) as exc:
                raise ServerDomainError(
                    "PLUGIN_CONSENT_READ_FAILED",
                    "Plugin consent records could not be read",
                    503,
                ) from exc
            self._path, self._records = path, records
        return manager

    def read(self, plugin_id: str) -> dict:
        with self._lock:
            self._load()
            return copy.deepcopy(self._records.get(plugin_id, {}))

    def _save(self, manager, records: dict) -> None:
        try:
            manager.save_json_config(
                FILENAME, {"schema_version": SCHEMA_VERSION, "plugins": records}
            )
        except OSError as exc:
            raise ServerDomainError(
                "PLUGIN_CONSENT_WRITE_FAILED",
                "Plugin consent records could not be saved",
                503,
            ) from exc

    def delete(self, plugin_id: str) -> None:
        with self._lock:
            manager = self._load()
            if plugin_id not in self._records:
                return
            records = copy.deepcopy(self._records)
            del records[plugin_id]
            self._save(manager, records)
            self._records = records


consent_store = PluginConsentStore()
