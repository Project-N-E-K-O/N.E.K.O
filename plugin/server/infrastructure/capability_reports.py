"""Advisory reports are independent of persistent user authorization."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from plugin.server.infrastructure.config_storage import atomic_write_bytes
from utils.file_utils import read_json_tolerating_replace


class CapabilityReportStore:
    def __init__(self, root: Path | None = None):
        self._root = root

    def _path(self, plugin_id: str) -> Path:
        from utils.config_manager import get_config_manager

        root = self._root or get_config_manager().get_runtime_config_path(
            "plugin_capability_reports"
        )
        return root / (hashlib.sha256(plugin_id.encode()).hexdigest() + ".json")

    def read(self, plugin_id: str) -> dict | None:
        try:
            value = read_json_tolerating_replace(self._path(plugin_id))
        except FileNotFoundError:
            return None
        if not isinstance(value, dict) or "status" not in value:
            raise ValueError("Invalid capability report")
        return value

    def save(self, plugin_id: str, report: dict) -> None:
        path = self._path(plugin_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_bytes(
            target=path,
            payload=json.dumps(report, ensure_ascii=False).encode(),
            prefix="capabilities-",
        )

    def delete(self, plugin_id: str) -> None:
        self._path(plugin_id).unlink(missing_ok=True)


report_store = CapabilityReportStore()
