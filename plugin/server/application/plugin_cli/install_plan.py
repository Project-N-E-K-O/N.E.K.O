from __future__ import annotations

from dataclasses import dataclass
import hashlib
import os
from pathlib import Path
import tomllib
from typing import Literal, Protocol
import zipfile

from plugin.core.plugin_layout import resolve_plugin_layout
from plugin.neko_plugin_cli.public import inspect_package
from plugin.server.infrastructure.path_safety import is_link_or_reparse_point


InstallAction = Literal["install", "upgrade", "blocked"]
PackageType = Literal["plugin", "bundle"]
InstallTargetPlaceholder = Literal["absent", "empty", "state_only", "conflict"]


class _Digest(Protocol):
    def update(self, data: bytes, /) -> object: ...


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
    target_ownership: Literal["new", "managed", "unmanaged"] = "new"


def confirmation_token(
    *,
    package_path: Path,
    target_dir: Path,
    snapshot_dir: Path | None = None,
) -> str:
    digest = hashlib.sha256()
    with package_path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            digest.update(chunk)
    digest.update(b"\0")
    digest.update(str(target_dir.resolve()).encode("utf-8"))
    _update_digest_with_target_snapshot(digest, snapshot_dir or target_dir)
    return digest.hexdigest()


def _update_digest_with_target_snapshot(
    digest: _Digest,
    target_dir: Path,
) -> None:
    """Bind upgrade confirmation to every byte in the current target.

    The installer must not accept a confirmation captured before a developer
    edits Python, vendored dependencies, or package assets. Symlinks are
    recorded as links and are never followed outside the plugin directory.
    """

    pending = [target_dir]
    while pending:
        current = pending.pop()
        children = sorted(current.iterdir(), key=lambda item: item.name)
        directories: list[Path] = []
        for child in children:
            relative = child.relative_to(target_dir).as_posix().encode("utf-8")
            digest.update(b"\0path\0")
            digest.update(relative)
            if is_link_or_reparse_point(child):
                digest.update(b"\0link\0")
                digest.update(os.readlink(child).encode("utf-8"))
                continue
            if child.is_dir():
                digest.update(b"\0dir")
                directories.append(child)
                continue
            if not child.is_file():
                raise ValueError(
                    f"unsupported plugin target entry while planning upgrade: {relative!r}"
                )
            digest.update(b"\0file\0")
            with child.open("rb") as handle:
                for chunk in iter(lambda: handle.read(65536), b""):
                    digest.update(chunk)
        pending.extend(reversed(directories))


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
    install_target_placeholder: InstallTargetPlaceholder = "conflict"
    matching = installed.get(plugin_id, [])
    if target_dir.exists():
        target_manifest = _read_manifest(target_dir / "plugin.toml")
        target_manifest_id = _plugin_text(target_manifest, "id")
        if not target_manifest_id:
            install_target_placeholder = classify_install_target_placeholder(
                target_dir,
                plugin_id=plugin_id,
            )
        if target_manifest_id != plugin_id and install_target_placeholder not in {
            "empty",
            "state_only",
        }:
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
    if not target_dir.exists() or install_target_placeholder in {"empty", "state_only"}:
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

    current_manifest = _read_manifest(target_dir / "plugin.toml")
    return PluginInstallPlan(
        action="upgrade",
        package_type="plugin",
        package_id=inspected.package_id,
        plugin_id=plugin_id,
        directory_name=directory_name,
        current_version=_plugin_text(current_manifest, "version"),
        target_version=target_version,
        confirmation_token=confirmation_token(package_path=package_path, target_dir=target_dir),
        reason="",
        legacy_plugin_ids=(),
    )


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


def classify_install_target_placeholder(
    target_dir: Path,
    *,
    plugin_id: str,
) -> InstallTargetPlaceholder:
    """Classify a manifest-less user target without reading plugin state.

    The default runtime storage layout deliberately keeps ``config/``,
    ``data/`` and ``cache/`` under ``<storage-root>/plugins/<logical-id>``.
    That path is also the user installation target. A built-in plugin can
    therefore create those state directories before a user overlay exists.
    They do not claim package ownership and must remain untouched.
    """

    if not target_dir.exists():
        return "absent"
    if is_link_or_reparse_point(target_dir) or not target_dir.is_dir():
        return "conflict"
    try:
        children = tuple(target_dir.iterdir())
    except OSError:
        return "conflict"
    if not children:
        return "empty"

    layout = resolve_plugin_layout(plugin_id, target_dir)
    state_root = layout.data_dir.parent
    if state_root.resolve(strict=False) != target_dir.resolve(strict=False):
        return "conflict"
    allowed_state_dirs = {
        layout.config_path.parent.resolve(strict=False),
        layout.data_dir.resolve(strict=False),
        layout.cache_dir.resolve(strict=False),
    }
    for child in children:
        if is_link_or_reparse_point(child) or not child.is_dir():
            return "conflict"
        if child.resolve(strict=False) not in allowed_state_dirs:
            return "conflict"
    return "state_only"


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


def _read_packaged_plugin_manifest(package_path: Path, *, archive_path: str) -> dict[str, object]:
    member_name = f"{archive_path.rstrip('/')}/plugin.toml"
    with zipfile.ZipFile(package_path) as archive:
        return tomllib.loads(archive.read(member_name).decode("utf-8"))


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
