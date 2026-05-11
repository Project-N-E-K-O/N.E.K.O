from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from utils.file_utils import atomic_write_json_async, read_json_async


class QQAutoReplyConfigStore:
    FILE_NAME = "business_config.json"

    def __init__(self, base_dir: Path):
        self._path = Path(base_dir) / self.FILE_NAME
        self._lock = asyncio.Lock()

    @property
    def path(self) -> Path:
        return self._path

    def default_config(self) -> dict[str, Any]:
        return {
            "onebot_url": "ws://127.0.0.1:3001",
            "token": "",
            "trusted_users": [],
            "trusted_groups": [],
            "normal_relay_probability": 0.1,
            "open_reply_probability": 0.1,
            "show_onboarding": True,
            "guide_step_config_done": False,
            "guide_step_settings_done": False,
            "guide_step_runtime_done": False,
            "max_concurrent_messages": 3,
            "ai_connect_timeout_seconds": 10.0,
            "ai_turn_timeout_seconds": 60.0,
            "handler_shutdown_timeout_seconds": 10.0,
            "napcat_directory": "",
            "show_napcat_window": True,
        }

    async def exists(self) -> bool:
        return self._path.is_file()

    async def load(self) -> dict[str, Any]:
        if not self._path.is_file():
            return self.default_config()
        payload = await read_json_async(self._path)
        if not isinstance(payload, dict):
            return self.default_config()
        merged = self.default_config()
        merged.update(payload)
        merged["trusted_users"] = payload.get("trusted_users") if isinstance(payload.get("trusted_users"), list) else []
        merged["trusted_groups"] = payload.get("trusted_groups") if isinstance(payload.get("trusted_groups"), list) else []
        return merged

    async def create_empty(self) -> dict[str, Any]:
        config = self.default_config()
        await self.save(config)
        return config

    async def save(self, config: dict[str, Any]) -> dict[str, Any]:
        async with self._lock:
            normalized = self.default_config()
            normalized.update(dict(config or {}))
            normalized["trusted_users"] = list(normalized.get("trusted_users") or [])
            normalized["trusted_groups"] = list(normalized.get("trusted_groups") or [])
            await atomic_write_json_async(self._path, normalized)
            return normalized
