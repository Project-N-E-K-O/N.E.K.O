"""Persistent capability decisions, cached per runtime root."""

from __future__ import annotations

import copy
import threading
from datetime import datetime, timezone

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

    def update(self, plugin_id: str, change) -> dict:
        with self._lock:
            manager = self._load()
            records = copy.deepcopy(self._records)
            record = records.setdefault(plugin_id, {})
            change(record)
            self._save(manager, records)
            self._records = records
            return copy.deepcopy(record)

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

    def decide(
        self,
        plugin_id: str,
        decision: str,
        capabilities: list[str],
        version: str,
    ) -> dict:
        def change(record):
            record.clear()
            record.update(
                decision=decision,
                approved_capabilities=sorted(set(capabilities)),
                version=version,
                decided_at=datetime.now(timezone.utc).isoformat(),
            )

        return self.update(plugin_id, change)

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
