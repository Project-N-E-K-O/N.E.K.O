"""State persistence contracts for SDK v2 shared storage."""

from __future__ import annotations

from datetime import date, datetime, timedelta
from enum import Enum
from pathlib import Path

from plugin.sdk_v2.shared.core.types import JsonObject
from plugin.sdk_v2.shared.models import Result


EXTENDED_TYPES = (datetime, date, timedelta, Enum, set, frozenset, Path)


class PluginStatePersistence:
    """Async-only freeze/unfreeze state contract."""

    def __init__(self, *args: object, **kwargs: object):
        raise NotImplementedError("sdk_v2 contract-only facade: shared.storage.state not implemented")

    async def save(self, instance: object) -> Result[bool, Exception]:
        raise NotImplementedError

    async def load(self, instance: object) -> Result[bool, Exception]:
        raise NotImplementedError

    async def clear(self) -> Result[bool, Exception]:
        raise NotImplementedError

    async def snapshot(self) -> Result[JsonObject, Exception]:
        raise NotImplementedError


__all__ = ["EXTENDED_TYPES", "PluginStatePersistence"]
