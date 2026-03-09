"""Internal runtime wiring helpers for shared.core.base."""

from __future__ import annotations

from pathlib import Path

from loguru import logger as _loguru_logger

from plugin.logging_config import FORMAT_FILE, LOG_COMPRESSION, LOG_DIR, LOG_MAX_SIZE, LOG_RETENTION
from plugin.sdk_v2.shared.logging import LogLevel, setup_sdk_logging


def resolve_plugin_dir(ctx: object) -> Path:
    config_path = getattr(ctx, "config_path", None)
    return Path(config_path).parent if config_path is not None else Path.cwd()


def resolve_effective_config(ctx: object) -> dict[str, object]:
    effective_cfg = getattr(ctx, "_effective_config", None)
    return effective_cfg if isinstance(effective_cfg, dict) else {}


def resolve_store_enabled(effective_cfg: dict[str, object]) -> bool:
    store_cfg = effective_cfg.get("plugin", {}).get("store", {}) if isinstance(effective_cfg.get("plugin"), dict) else {}
    return bool(store_cfg.get("enabled", True)) if isinstance(store_cfg, dict) else True


def resolve_db_config(effective_cfg: dict[str, object]) -> tuple[bool, str]:
    db_cfg = effective_cfg.get("plugin", {}).get("database", {}) if isinstance(effective_cfg.get("plugin"), dict) else {}
    enabled = bool(db_cfg.get("enabled", True)) if isinstance(db_cfg, dict) else True
    name = str(db_cfg.get("name", "plugin.db")) if isinstance(db_cfg, dict) else "plugin.db"
    return enabled, name


def resolve_state_backend(effective_cfg: dict[str, object]) -> str:
    state_cfg = effective_cfg.get("plugin_state", {}) if isinstance(effective_cfg.get("plugin_state"), dict) else {}
    return str(state_cfg.get("backend", "file")) if isinstance(state_cfg, dict) else "file"


def setup_plugin_file_logging(
    *,
    component: str,
    parsed_level: LogLevel,
    log_dir: str | Path | None,
    max_bytes: int | None,
    backup_count: int | None,
    previous_sink_id: int | None,
) -> int | None:
    setup_sdk_logging(component=component, level=parsed_level)

    if log_dir is None and max_bytes is None and backup_count is None:
        return previous_sink_id

    target_dir = Path(log_dir) if log_dir is not None else LOG_DIR
    target_dir.mkdir(parents=True, exist_ok=True)
    log_file = target_dir / f"{component.replace('.', '_')}.log"

    if isinstance(previous_sink_id, int):
        _loguru_logger.remove(previous_sink_id)

    sink_id = _loguru_logger.add(
        str(log_file),
        format=FORMAT_FILE,
        level=parsed_level.value,
        rotation=max_bytes if max_bytes is not None else LOG_MAX_SIZE,
        retention=backup_count if backup_count is not None else LOG_RETENTION,
        compression=LOG_COMPRESSION,
        encoding="utf-8",
        filter=lambda record, c=component: record["extra"].get("component", "") == c,
    )
    return sink_id


__all__ = [
    "resolve_plugin_dir",
    "resolve_effective_config",
    "resolve_store_enabled",
    "resolve_db_config",
    "resolve_state_backend",
    "setup_plugin_file_logging",
]
