"""Development archives live outside the remotely accessible package root."""
from pathlib import Path

from plugin.server.application.plugin_cli.paths import PluginCliPathPolicy
from plugin.server.domain.errors import ServerDomainError


def development_artifacts_root(package_root: Path) -> Path:
    package_root = package_root.resolve()
    root = package_root.with_name(package_root.name + "-development").resolve()
    if root.is_relative_to(package_root) or package_root.is_relative_to(root):
        raise ValueError("Development artifacts must be separate from ordinary packages")
    return root


def resolve_development_download_sync(package: str) -> Path:
    root = development_artifacts_root(PluginCliPathPolicy.from_settings().package_artifacts_root)
    path = Path(package).expanduser()
    resolved = (path if path.is_absolute() else root / path).resolve()
    if not resolved.is_relative_to(root) or resolved.suffix not in {".neko-plugin", ".neko-bundle"}:
        raise ServerDomainError(code="DEVELOPMENT_PACKAGE_INVALID", message="Invalid development package path", status_code=400)
    if not resolved.is_file():
        raise ServerDomainError(code="DEVELOPMENT_PACKAGE_NOT_FOUND", message="Development package not found", status_code=404)
    return resolved
