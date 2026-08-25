from __future__ import annotations

from dataclasses import dataclass
import hashlib
from pathlib import Path
import stat
import tomllib
from typing import Literal
import zipfile

from packaging.version import InvalidVersion, Version

from plugin.neko_plugin_cli.core.archive_utils import (
    read_archive_toml,
    validate_archive_structure,
)
from plugin.neko_plugin_cli.public import inspect_package


InstallAction = Literal["install", "upgrade", "reinstall", "downgrade", "blocked"]
REPLACEMENT_ACTIONS = frozenset({"upgrade", "reinstall", "downgrade"})
PackageType = Literal["plugin", "bundle"]


@dataclass(frozen=True, slots=True)
class PluginInstallPlan:
    action: InstallAction
    package_type: PackageType
    package_id: str
    plugin_id: str
    directory_name: str
    current_version: str
    target_version: str
    confirmation_token: str
    reason: str
    legacy_plugin_ids: tuple[str, ...]
    installed_package_id: str = ""
    manifestless_state: bool = False


def confirmation_token(*, package_path: Path, target_dir: Path) -> str:
    digest = hashlib.sha256()
    with package_path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            digest.update(chunk)
    digest.update(b"\0")
    digest.update(str(target_dir.resolve()).encode("utf-8"))
    digest.update(b"\0")
    manifest_path = target_dir / "plugin.toml"
    if manifest_path.is_file():
        digest.update(manifest_path.read_bytes())
    elif is_manifestless_state_directory(target_dir):
        # The actual state tree is moved into the transaction backup and
        # revalidated before any package bytes are promoted. Changes inside
        # config/data/cache remain user-owned and are preserved.
        digest.update(b"manifestless-state")
    else:
        raise FileNotFoundError(f"installed plugin manifest is missing: {target_dir.name}")
    return digest.hexdigest()


def build_install_plan(*, package_path: Path, plugins_root: Path) -> PluginInstallPlan:
    package_path = package_path.expanduser().resolve()
    plugins_root = plugins_root.expanduser().resolve()
    inspected = inspect_package(package_path)

    if inspected.package_type == "bundle":
        conflicts = _bundle_conflicts(inspected.plugins, plugins_root)
        return PluginInstallPlan(
            action="blocked" if conflicts else "install",
            package_type="bundle",
            package_id=inspected.package_id,
            plugin_id=inspected.package_id,
            directory_name="",
            current_version="",
            target_version=inspected.version,
            confirmation_token="",
            reason="bundle_conflict" if conflicts else "",
            legacy_plugin_ids=(),
        )

    if len(inspected.plugins) != 1:
        return _blocked(
            inspected.package_id,
            inspected.package_id,
            inspected.version,
            reason="invalid_plugin_count",
        )

    packaged_plugin = inspected.plugins[0]
    plugin_id = packaged_plugin.plugin_id
    directory_name = Path(packaged_plugin.archive_path).name
    packaged_manifest = _read_packaged_plugin_manifest(
        package_path,
        archive_path=packaged_plugin.archive_path,
    )
    target_version = _plugin_text(packaged_manifest, "version") or inspected.version
    previous_ids = _previous_ids(packaged_manifest)
    installed = _installed_plugins(plugins_root)

    legacy_ids = tuple(sorted(previous_id for previous_id in previous_ids if previous_id in installed))
    if legacy_ids:
        return PluginInstallPlan(
            action="blocked",
            package_type="plugin",
            package_id=inspected.package_id,
            plugin_id=plugin_id,
            directory_name=directory_name,
            current_version="",
            target_version=target_version,
            confirmation_token="",
            reason="legacy_plugin_present",
            legacy_plugin_ids=legacy_ids,
        )

    target_dir = plugins_root / directory_name
    matching = installed.get(plugin_id, [])
    manifestless_state = False
    if target_dir.exists():
        target_manifest = _read_manifest(target_dir / "plugin.toml")
        if _plugin_text(target_manifest, "id") != plugin_id:
            if is_manifestless_state_directory(target_dir):
                manifestless_state = True
            else:
                return _blocked(
                    inspected.package_id,
                    plugin_id,
                    target_version,
                    reason="directory_identity_conflict",
                    directory_name=directory_name,
                )
    if len(matching) > 1 or (matching and matching[0].resolve() != target_dir.resolve()):
        return _blocked(
            inspected.package_id,
            plugin_id,
            target_version,
            reason="multiple_installations",
            directory_name=directory_name,
        )
    if not target_dir.exists():
        return PluginInstallPlan(
            action="install",
            package_type="plugin",
            package_id=inspected.package_id,
            plugin_id=plugin_id,
            directory_name=directory_name,
            current_version="",
            target_version=target_version,
            confirmation_token="",
            reason="",
            legacy_plugin_ids=(),
        )

    if manifestless_state:
        return PluginInstallPlan(
            action="reinstall",
            package_type="plugin",
            package_id=inspected.package_id,
            plugin_id=plugin_id,
            directory_name=directory_name,
            current_version="",
            target_version=target_version,
            confirmation_token=confirmation_token(
                package_path=package_path,
                target_dir=target_dir,
            ),
            reason="manifestless_state",
            legacy_plugin_ids=(),
            manifestless_state=True,
        )

    current_manifest = _read_manifest(target_dir / "plugin.toml")
    current_version = _plugin_text(current_manifest, "version")
    return PluginInstallPlan(
        action=_replacement_action(
            current_version=current_version,
            target_version=target_version,
        ),
        package_type="plugin",
        package_id=inspected.package_id,
        plugin_id=plugin_id,
        directory_name=directory_name,
        current_version=current_version,
        target_version=target_version,
        confirmation_token=confirmation_token(package_path=package_path, target_dir=target_dir),
        reason="",
        legacy_plugin_ids=(),
    )


def _replacement_action(*, current_version: str, target_version: str) -> InstallAction:
    if current_version and current_version == target_version:
        return "reinstall"
    try:
        current = Version(current_version)
        target = Version(target_version)
    except InvalidVersion:
        # Preserve the historical replacement behavior for plugins that use
        # non-PEP-440 version labels. The confirmation still shows both raw
        # versions instead of blocking an otherwise compatible old package.
        return "upgrade"
    if target == current:
        return "reinstall"
    if target < current:
        return "downgrade"
    return "upgrade"


def _blocked(
    package_id: str,
    plugin_id: str,
    target_version: str,
    *,
    reason: str,
    directory_name: str = "",
) -> PluginInstallPlan:
    return PluginInstallPlan(
        action="blocked",
        package_type="plugin",
        package_id=package_id,
        plugin_id=plugin_id,
        directory_name=directory_name,
        current_version="",
        target_version=target_version,
        confirmation_token="",
        reason=reason,
        legacy_plugin_ids=(),
    )


def _bundle_conflicts(plugins: list[object], plugins_root: Path) -> bool:
    installed = _installed_plugins(plugins_root)
    for packaged in plugins:
        plugin_id = getattr(packaged, "plugin_id", "")
        archive_path = getattr(packaged, "archive_path", "")
        if plugin_id in installed or (plugins_root / Path(archive_path).name).exists():
            return True
    return False


def _installed_plugins(plugins_root: Path) -> dict[str, list[Path]]:
    installed: dict[str, list[Path]] = {}
    if not plugins_root.is_dir():
        return installed
    for manifest_path in plugins_root.glob("*/plugin.toml"):
        manifest = _read_manifest(manifest_path)
        plugin_id = _plugin_text(manifest, "id")
        if plugin_id:
            installed.setdefault(plugin_id, []).append(manifest_path.parent)
    return installed


_RUNTIME_STATE_DIRECTORY_NAMES = frozenset({"config", "data", "cache"})


def _is_link_or_reparse(path: Path) -> bool:
    try:
        metadata = path.lstat()
    except OSError:
        return True
    file_attributes = getattr(metadata, "st_file_attributes", 0)
    reparse_attribute = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
    return path.is_symlink() or bool(file_attributes & reparse_attribute)


def is_manifestless_state_directory(target_dir: Path) -> bool:
    """Recognize an old code-less plugin directory without trusting its contents."""

    if not target_dir.is_dir() or _is_link_or_reparse(target_dir):
        return False
    if (target_dir / "plugin.toml").exists():
        return False
    try:
        children = list(target_dir.iterdir())
    except OSError:
        return False
    if not children:
        return False
    for child in children:
        if child.name.casefold() not in _RUNTIME_STATE_DIRECTORY_NAMES:
            return False
        if not child.is_dir() or _is_link_or_reparse(child):
            return False
        try:
            descendants = child.rglob("*")
            for descendant in descendants:
                if _is_link_or_reparse(descendant):
                    return False
        except OSError:
            return False
    return True


def _read_packaged_plugin_manifest(package_path: Path, *, archive_path: str) -> dict[str, object]:
    member_name = f"{archive_path.rstrip('/')}/plugin.toml"
    with zipfile.ZipFile(package_path) as archive:
        validate_archive_structure(archive)
        manifest = read_archive_toml(archive, member_name, required=True)
        assert manifest is not None
        return manifest


def _read_manifest(path: Path) -> dict[str, object]:
    if not path.is_file():
        return {}
    return tomllib.loads(path.read_text(encoding="utf-8"))


def _plugin_table(manifest: dict[str, object]) -> dict[str, object]:
    value = manifest.get("plugin")
    return value if isinstance(value, dict) else {}


def _plugin_text(manifest: dict[str, object], key: str) -> str:
    value = _plugin_table(manifest).get(key)
    return value.strip() if isinstance(value, str) else ""


def _previous_ids(manifest: dict[str, object]) -> tuple[str, ...]:
    value = _plugin_table(manifest).get("previous_ids")
    if not isinstance(value, list):
        return ()
    return tuple(sorted({item.strip() for item in value if isinstance(item, str) and item.strip()}))
