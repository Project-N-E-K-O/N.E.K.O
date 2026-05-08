"""Internal runtime wiring helpers for shared.core.base.

⚠ 历史教训：本文件曾用 loguru.add/remove 给每个插件组件挂独立 sink，
日志路径由 SDK 调用者乱传 log_dir 决定 → AppImage 打包后写到 squashfs 直接崩。
现在统一走 utils.logger_config.RobustLoggerConfig（通过 setup_sdk_logging
→ plugin.logging_config）。log_dir / max_bytes / backup_count 形参保留只为
兼容旧调用，**不再生效**；本体决定路径与轮转。
谁再往这里塞 loguru —— 按维护者口径就把谁杀了。lint: scripts/check_no_loguru.py。
"""

from __future__ import annotations

from pathlib import Path

from plugin.sdk.shared.logging import LogLevel, setup_sdk_logging


def resolve_plugin_dir(ctx: object) -> Path:
    config_path = getattr(ctx, "config_path", None)
    return Path(config_path).parent if config_path is not None else Path.cwd()


def resolve_effective_config(ctx: object) -> dict[str, object]:
    effective_cfg = getattr(ctx, "_effective_config", None)
    return effective_cfg if isinstance(effective_cfg, dict) else {}


def resolve_store_enabled(effective_cfg: dict[str, object]) -> bool:
    store_cfg = effective_cfg.get("plugin", {}).get("store", {}) if isinstance(effective_cfg.get("plugin"), dict) else {}
    return bool(store_cfg.get("enabled", False)) if isinstance(store_cfg, dict) else False


def resolve_db_config(effective_cfg: dict[str, object]) -> tuple[bool, str]:
    db_cfg = effective_cfg.get("plugin", {}).get("database", {}) if isinstance(effective_cfg.get("plugin"), dict) else {}
    enabled = bool(db_cfg.get("enabled", False)) if isinstance(db_cfg, dict) else False
    name = str(db_cfg.get("name", "plugin.db")) if isinstance(db_cfg, dict) else "plugin.db"
    return enabled, name


def resolve_state_backend(effective_cfg: dict[str, object]) -> str:
    state_cfg = effective_cfg.get("plugin_state", {}) if isinstance(effective_cfg.get("plugin_state"), dict) else {}
    return str(state_cfg.get("backend", "off")) if isinstance(state_cfg, dict) else "off"


def setup_plugin_file_logging(
    *,
    component: str,
    parsed_level: LogLevel,
    log_dir: str | Path | None,
    max_bytes: int | None,
    backup_count: int | None,
    previous_sink_id: int | None,
) -> int | None:
    """配置 SDK 组件日志。

    兼容形参 log_dir/max_bytes/backup_count 已废弃 —— 本体 RobustLoggerConfig
    统一管路径与轮转。仅调用 setup_sdk_logging 应用 level，返回固定 sink_id=0
    保持 base.py 的 self._file_sink_id: int | None 契约。
    """
    setup_sdk_logging(component=component, level=parsed_level)
    if previous_sink_id is None:
        return 0
    return previous_sink_id


__all__ = [
    "resolve_db_config",
    "resolve_effective_config",
    "resolve_plugin_dir",
    "resolve_state_backend",
    "resolve_store_enabled",
    "setup_plugin_file_logging",
]
