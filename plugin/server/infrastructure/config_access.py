"""Request-local source identity for managed plugin configuration operations."""
from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class ConfigAccessSnapshot:
    plugin_id: str
    manifest_path: Path
    host: object | None


_CONFIG_ACCESS: ContextVar[ConfigAccessSnapshot | None] = ContextVar("plugin_config_access", default=None)


def get_config_access(plugin_id: str) -> ConfigAccessSnapshot | None:
    snapshot = _CONFIG_ACCESS.get()
    return snapshot if snapshot is not None and snapshot.plugin_id == plugin_id else None


@contextmanager
def bind_config_access(snapshot: ConfigAccessSnapshot):
    token = _CONFIG_ACCESS.set(snapshot)
    try:
        yield
    finally:
        _CONFIG_ACCESS.reset(token)
