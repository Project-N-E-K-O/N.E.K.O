"""Shared path resolution helpers for CLI commands."""

from __future__ import annotations

from pathlib import Path
import re

from ..paths import CliDefaults

_MARKET_REPO_PREFIX = "n.e.k.o_plugin_"


def parse_github_repository_remote(remote_url: str) -> str | None:
    """Return owner/repository for a supported GitHub remote URL."""
    owner = r"[A-Za-z0-9]+(?:-[A-Za-z0-9]+)*"
    repository = r"[A-Za-z0-9._-]+?"
    patterns = (
        rf"https?://github\.com/(?P<repo>{owner}/{repository})(?:\.git)?/?$",
        rf"git@github\.com:(?P<repo>{owner}/{repository})(?:\.git)?$",
        rf"ssh://git@github\.com/(?P<repo>{owner}/{repository})(?:\.git)?/?$",
        rf"ssh://git@ssh\.github\.com:443/(?P<repo>{owner}/{repository})(?:\.git)?/?$",
    )
    value = remote_url.strip()
    for pattern in patterns:
        match = re.fullmatch(pattern, value, flags=re.IGNORECASE)
        if match:
            return match.group("repo")
    return None


def resolve_plugin_dirs(*, plugin_names: list[str], pack_all: bool, defaults: CliDefaults) -> list[Path]:
    plugin_root = defaults.plugins_root

    if pack_all:
        plugin_dirs = sorted(
            path.parent.resolve()
            for path in plugin_root.glob("*/plugin.toml")
            if path.is_file()
        )
        if not plugin_dirs:
            raise FileNotFoundError(
                f"no plugin.toml files found under '{plugin_root}'. "
                f"Make sure plugin directories exist and each contains a plugin.toml file."
            )
        return plugin_dirs

    if not plugin_names:
        raise ValueError(
            "no plugin names provided. Specify one or more plugin directory names "
            "(e.g. 'neko-plugin build my_plugin'), or use --all to build every plugin "
            f"found under '{plugin_root}'."
        )

    return [resolve_plugin_dir_candidate(item, defaults=defaults) for item in plugin_names]


def resolve_plugin_dir_candidate(raw: str, *, defaults: CliDefaults) -> Path:
    candidate = Path(raw).expanduser()
    if candidate.exists():
        plugin_dir = candidate.resolve()
    else:
        plugin_dir = (defaults.plugins_root / raw).resolve()
        if not (plugin_dir / "plugin.toml").is_file():
            market_repo_dir = (defaults.plugins_root / f"{_MARKET_REPO_PREFIX}{raw}").resolve()
            if (market_repo_dir / "plugin.toml").is_file():
                plugin_dir = market_repo_dir

    plugin_toml = plugin_dir / "plugin.toml"
    if not plugin_toml.is_file():
        raise FileNotFoundError(
            f"plugin.toml not found for plugin '{raw}'.\n"
            f"  looked in: {plugin_dir}\n"
            f"Make sure the plugin directory name is correct and contains a plugin.toml file. "
            f"Available plugins can be found under '{defaults.plugins_root}'."
        )
    return plugin_dir


def resolve_package_path(raw: str, *, defaults: CliDefaults) -> Path:
    candidate = Path(raw).expanduser()
    if candidate.exists():
        return candidate.resolve()

    target_candidate = (defaults.target_dir / raw).resolve()
    if target_candidate.exists():
        return target_candidate

    raise FileNotFoundError(
        f"package file not found: '{raw}'.\n"
        f"  looked in: {candidate}\n"
        f"  also tried: {target_candidate}\n"
        f"Provide a full path to the .neko-plugin or .neko-bundle file, "
        f"or a filename that exists under '{defaults.target_dir}'."
    )
