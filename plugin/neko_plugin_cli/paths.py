"""Default path resolution for the neko-plugin CLI.

All paths are resolved lazily and can be overridden via CLI arguments.
The library layer (``core/``) never imports this module — it receives
all paths as explicit parameters.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path


@dataclass(frozen=True)
class CliDefaults:
    """Resolved default paths for the CLI layer."""

    plugin_root: Path
    target_dir: Path
    plugins_root: Path
    profiles_root: Path

    @property
    def repo_root(self) -> Path:
        return self.plugin_root.parent

    def with_plugins_root(self, plugins_root: str | None) -> CliDefaults:
        """Return these defaults with a ``--plugins-root`` override applied when given."""
        if not plugins_root:
            return self
        return replace(self, plugins_root=Path(plugins_root).expanduser().resolve())


def resolve_default_paths(*, cli_root: Path | None = None) -> CliDefaults:
    """Resolve default paths relative to the CLI installation location.

    When running from the source repository the layout is::

        <repo>/plugin/neko_plugin_cli/paths.py
        <repo>/plugin/plugins/
        <repo>/plugin/.neko-package-profiles/

    The *cli_root* parameter allows tests and the legacy shim to override
    the base directory.
    """
    if cli_root is None:
        cli_root = Path(__file__).resolve().parent

    repo_root = cli_root.parent.parent
    plugin_root = repo_root / "plugin"

    return CliDefaults(
        plugin_root=plugin_root,
        target_dir=cli_root / "target",
        plugins_root=plugin_root / "plugins",
        profiles_root=plugin_root / ".neko-package-profiles",
    )
