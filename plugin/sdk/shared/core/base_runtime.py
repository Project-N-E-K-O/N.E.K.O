"""Internal runtime wiring helpers for shared.core.base.

Historical lesson: this file once attached per-component sinks with loguru.add/remove,
using SDK callers' arbitrary log_dir paths; AppImage then crashed writing to squashfs.
All logging now uses utils.logger_config.RobustLoggerConfig through setup_sdk_logging
and plugin.logging_config. log_dir / max_bytes / backup_count remain only for
legacy compatibility and no longer affect behavior; the host controls paths and rotation.
The maintainer emphatically forbids reintroducing loguru here. Lint: scripts/check_no_loguru.py.
"""

from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path, PurePosixPath, PureWindowsPath

from plugin.sdk.shared.logging import LogLevel, setup_sdk_logging


def resolve_plugin_dir(ctx: object) -> Path:
    config_path = getattr(ctx, "config_path", None)
    return Path(config_path).parent if config_path is not None else Path.cwd()


def _safe_plugin_dirname(plugin_id: object) -> str:
    value = str(plugin_id or "plugin").strip() or "plugin"
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", value).strip("._") or "plugin"


def _normalize_absolute_path(raw: object) -> Path | None:
    value = str(raw or "").strip()
    if not value:
        return None
    try:
        path = Path(value).expanduser()
        if not path.is_absolute():
            return None
        return path.resolve(strict=False)
    except Exception:
        return None


def _standard_runtime_root() -> Path:
    app_name = "N.E.K.O"
    if sys.platform == "win32":
        base = os.environ.get("LOCALAPPDATA") or os.environ.get("APPDATA")
        if base:
            return Path(base).expanduser().resolve(strict=False) / app_name
        return Path.home() / "AppData" / "Local" / app_name
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / app_name
    xdg_data_home = os.environ.get("XDG_DATA_HOME")
    base = Path(xdg_data_home).expanduser() if xdg_data_home else Path.home() / ".local" / "share"
    return base.resolve(strict=False) / app_name


def _read_policy_selected_root(anchor_root: Path) -> Path | None:
    try:
        payload = json.loads((anchor_root / "state" / "storage_policy.json").read_text(encoding="utf-8"))
    except Exception:
        return None
    if not isinstance(payload, dict):
        return None
    return _normalize_absolute_path(payload.get("selected_root"))


def resolve_runtime_data_root() -> Path:
    selected_root = _normalize_absolute_path(os.environ.get("NEKO_STORAGE_SELECTED_ROOT"))
    if selected_root is not None:
        return selected_root

    anchor_root = _normalize_absolute_path(os.environ.get("NEKO_STORAGE_ANCHOR_ROOT")) or _standard_runtime_root()
    policy_root = _read_policy_selected_root(anchor_root)
    return policy_root or anchor_root


def resolve_plugin_storage_dir(ctx: object) -> Path:
    return resolve_runtime_data_root() / "plugins" / _safe_plugin_dirname(getattr(ctx, "plugin_id", "plugin"))


def resolve_plugin_data_dir(ctx: object) -> Path:
    return resolve_plugin_storage_dir(ctx) / "data"


def resolve_plugin_cache_dir(ctx: object) -> Path:
    return resolve_plugin_storage_dir(ctx) / "cache"


def resolve_plugin_runtime_config_path(ctx: object) -> Path:
    return resolve_plugin_storage_dir(ctx) / "config" / "plugin.toml"


PLUGIN_SHARED_DIRNAME = "plugin_shared"


def resolve_plugin_shared_dir(plugin_id: object) -> Path:
    """Return the directory a plugin publishes for other plugins to read.

    ``<data root>/plugin_shared/<plugin id>/``, next to the plugins' private
    state and outside it: removing a plugin leaves what it published.
    """
    return resolve_runtime_data_root() / PLUGIN_SHARED_DIRNAME / _shared_dirname(plugin_id)


_SHARED_PLAIN_CHARACTERS = frozenset("abcdefghijklmnopqrstuvwxyz0123456789_-")
_WINDOWS_DEVICE_NAMES = frozenset(
    {"con", "prn", "aux", "nul"} | {f"{prefix}{digit}" for prefix in ("com", "lpt") for digit in "123456789"}
)


def _shared_dirname(plugin_id: object) -> str:
    """Return the directory name a plugin id publishes under; two ids never get the same one.

    Lower-case letters, digits, ``_`` and ``-`` stand for themselves, so an
    ordinary id is its own directory name. Every other character is written
    as ``~`` and the two hex digits of each of its bytes: upper-case letters
    too, because ``Weather`` and ``weather`` are one directory on Windows
    and macOS. A plugin id cannot contain ``~``, so an escaped name is never
    another plugin's plain one, and the mapping can be read backwards.

    The mapping private state uses (``_safe_plugin_dirname``) drops
    characters instead: ``weather_`` and ``weather`` end up in one
    directory there. It is kept as it is, being a contract with what is on
    users' disks; here two plugins would write over each other's files.
    """
    raw = str(plugin_id or "").strip()
    if not raw:
        return "~"
    name = "".join(
        char if char in _SHARED_PLAIN_CHARACTERS else "".join(f"~{byte:02x}" for byte in char.encode("utf-8"))
        for char in raw
    )
    if name in _WINDOWS_DEVICE_NAMES:
        # Windows reads these as devices, not as directories. Two tildes
        # cannot come from an escaped character, which is one tilde and hex.
        return f"~~{name}"
    return name


def _declared_shared_reads(plugin_section: object) -> frozenset[str] | None:
    """Read ``[plugin.shared] reads`` from a ``[plugin]`` table; None when the table is not one."""
    if not isinstance(plugin_section, dict):
        return None
    shared = plugin_section.get("shared")
    reads = shared.get("reads") if isinstance(shared, dict) else None
    if isinstance(reads, str):
        reads = [reads]
    if not isinstance(reads, (list, tuple)):
        return frozenset()
    return frozenset(item.strip() for item in reads if isinstance(item, str) and item.strip())


def resolve_shared_reads(ctx: object) -> frozenset[str]:
    """Return the ids of the plugins whose shared directory this plugin declares it reads.

    From the plugin's effective configuration when the context has it, else
    from the manifest itself.
    """
    declared = _declared_shared_reads(resolve_effective_config(ctx).get("plugin"))
    if declared is not None:
        return declared
    config_path = getattr(ctx, "config_path", None)
    if not isinstance(config_path, (str, Path)):
        return frozenset()
    try:
        import tomllib
    except ModuleNotFoundError:  # pragma: no cover - Python 3.10
        import tomli as tomllib  # type: ignore[no-redef]
    try:
        with Path(config_path).open("rb") as stream:
            manifest = tomllib.load(stream)
    except Exception:
        return frozenset()
    return _declared_shared_reads(manifest.get("plugin")) or frozenset()


def join_shared_path(base: Path, parts: tuple[str, ...]) -> Path | None:
    """Join ``parts`` below a shared directory; None when they would lead out of it."""
    for part in parts:
        # Read as a Windows path and as a POSIX path, whatever the platform:
        # a drive, a leading separator or ".." in either reading leads out.
        for flavour in (PureWindowsPath, PurePosixPath):
            pure = flavour(str(part))
            if pure.anchor or ".." in pure.parts or ":" in str(part):
                return None
    return base.joinpath(*(str(part) for part in parts)) if parts else base


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
    """Configure SDK component logging.

    Compatibility parameters log_dir/max_bytes/backup_count are deprecated; the host's RobustLoggerConfig
    controls paths and rotation. Call setup_sdk_logging only to apply level and return fixed sink_id=0
    to preserve base.py's self._file_sink_id: int | None contract.
    """
    setup_sdk_logging(component=component, level=parsed_level)
    if previous_sink_id is None:
        return 0
    return previous_sink_id


__all__ = [
    "resolve_plugin_cache_dir",
    "resolve_db_config",
    "resolve_effective_config",
    "resolve_plugin_data_dir",
    "resolve_plugin_dir",
    "resolve_plugin_runtime_config_path",
    "resolve_plugin_shared_dir",
    "resolve_plugin_storage_dir",
    "resolve_shared_reads",
    "join_shared_path",
    "PLUGIN_SHARED_DIRNAME",
    "resolve_runtime_data_root",
    "resolve_state_backend",
    "resolve_store_enabled",
    "setup_plugin_file_logging",
]
