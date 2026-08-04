# -*- coding: utf-8 -*-
"""Persist RVC UI settings outside plugin.toml (no active profile required)."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any, Mapping

from utils.file_utils import atomic_write_json_async, read_json_async

from .service import settings_from_mapping, settings_to_mapping


class RvcCoverConfigStore:
    FILE_NAME = "business_config.json"

    def __init__(self, base_dir: Path, *, logger: Any | None = None) -> None:
        self._path = Path(base_dir) / self.FILE_NAME
        self._lock = asyncio.Lock()
        self._logger = logger

    @property
    def path(self) -> Path:
        return self._path

    def default_config(self) -> dict[str, Any]:
        return settings_to_mapping(settings_from_mapping({}))

    async def exists(self) -> bool:
        return self._path.is_file()

    def normalize(self, config: Mapping[str, Any] | None) -> dict[str, Any]:
        return settings_to_mapping(settings_from_mapping(dict(config or {})))

    async def load(self) -> dict[str, Any]:
        if not await self.exists():
            return self.default_config()
        try:
            raw = await read_json_async(self._path)
        except Exception as exc:
            if self._logger is not None:
                self._logger.warning("rvc config load failed: %s", exc)
            return self.default_config()
        return self.normalize(raw if isinstance(raw, dict) else {})

    async def save(self, config: Mapping[str, Any]) -> dict[str, Any]:
        normalized = self.normalize(config)
        async with self._lock:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            await atomic_write_json_async(self._path, normalized)
        return normalized

    def merge_with_base(self, base: Mapping[str, Any] | None, overlay: Mapping[str, Any] | None) -> dict[str, Any]:
        merged = {**(base or {}), **(overlay or {})}
        return self.normalize(merged)
