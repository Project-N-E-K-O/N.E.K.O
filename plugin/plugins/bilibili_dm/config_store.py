from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any, Mapping

from utils.file_utils import atomic_write_json_async, read_json_async


class BiliDMConfigStore:
    """Runtime settings stored outside the tracked plugin manifest."""

    FILE_NAME = "business_config.json"
    CREDENTIAL_FIELDS = (
        "sesdata",
        "bili_jct",
        "buvid3",
        "dedeuserid",
        "ac_time_value",
    )
    VALID_PERMISSION_MODES = {"allow_list", "deny_list", "open"}

    def __init__(self, base_dir: Path):
        self._path = Path(base_dir) / self.FILE_NAME
        self._lock = asyncio.Lock()

    @property
    def path(self) -> Path:
        return self._path

    def default_config(self) -> dict[str, Any]:
        return {
            "sesdata": "",
            "bili_jct": "",
            "buvid3": "",
            "dedeuserid": "",
            "ac_time_value": "",
            "permission_mode": "allow_list",
            "max_concurrent_messages": 3,
            "ai_connect_timeout_seconds": 10.0,
            "ai_turn_timeout_seconds": 60.0,
            "handler_shutdown_timeout_seconds": 10.0,
            "show_onboarding": True,
        }

    async def exists(self) -> bool:
        return self._path.is_file()

    @classmethod
    def _permission_mode(cls, value: Any) -> str:
        mode = str(value or "").strip().lower()
        return mode if mode in cls.VALID_PERMISSION_MODES else "allow_list"

    @staticmethod
    def _bounded_int(value: Any, default: int, minimum: int, maximum: int) -> int:
        try:
            parsed = int(value)
        except (TypeError, ValueError):
            parsed = default
        return max(minimum, min(maximum, parsed))

    @staticmethod
    def _bounded_float(value: Any, default: float, minimum: float, maximum: float) -> float:
        try:
            parsed = float(value)
        except (TypeError, ValueError):
            parsed = default
        return max(minimum, min(maximum, parsed))

    def normalize(self, config: Mapping[str, Any] | None) -> dict[str, Any]:
        raw = dict(config or {})
        normalized = self.default_config()
        for field in self.CREDENTIAL_FIELDS:
            normalized[field] = str(raw.get(field) or "").strip()
        normalized["permission_mode"] = self._permission_mode(raw.get("permission_mode"))
        normalized["max_concurrent_messages"] = self._bounded_int(
            raw.get("max_concurrent_messages"), 3, 1, 20
        )
        normalized["ai_connect_timeout_seconds"] = self._bounded_float(
            raw.get("ai_connect_timeout_seconds"), 10.0, 1.0, 120.0
        )
        normalized["ai_turn_timeout_seconds"] = self._bounded_float(
            raw.get("ai_turn_timeout_seconds"), 60.0, 5.0, 600.0
        )
        normalized["handler_shutdown_timeout_seconds"] = self._bounded_float(
            raw.get("handler_shutdown_timeout_seconds"), 10.0, 1.0, 120.0
        )
        normalized["show_onboarding"] = bool(raw.get("show_onboarding", True))
        return normalized

    async def load(self) -> dict[str, Any]:
        if not self._path.is_file():
            return self.default_config()
        payload = await read_json_async(self._path)
        if not isinstance(payload, dict):
            return self.default_config()
        return self.normalize(payload)

    async def create(self, initial: Mapping[str, Any] | None = None) -> dict[str, Any]:
        return await self.save(dict(initial or {}))

    async def save(self, config: Mapping[str, Any]) -> dict[str, Any]:
        async with self._lock:
            normalized = self.normalize(config)
            await atomic_write_json_async(self._path, normalized)
            return normalized
