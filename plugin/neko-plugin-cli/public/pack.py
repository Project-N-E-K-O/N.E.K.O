from __future__ import annotations

import hashlib
import shutil
import tempfile
import zipfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover
    import tomli as tomllib  # type: ignore[no-redef]

from .models import PackResult, PayloadBuildResult, PluginSource
from .pack_rules import PackRuleSet, load_pack_rules, should_skip_path

_SCHEMA_VERSION = "1.0"
_TOOL_NAME = "neko-plugin-cli"
_TOOL_VERSION = "0.1.0"


@dataclass(slots=True)
class PackPaths:
    """Resolved staging layout for a single pack operation."""

    staging_root: Path
    payload_dir: Path
    plugins_dir: Path
    profiles_dir: Path
    plugin_payload_dir: Path
    manifest_path: Path
    metadata_path: Path

    @classmethod
    def create(cls, *, plugin_id: str) -> PackPaths:
        # All packaging currently flows through a temporary staging tree first.
        # This keeps manifest generation, hashing, and archive export working on
        # a single normalized layout.
        staging_root = Path(tempfile.mkdtemp(prefix=f"neko_pack_{plugin_id}_")).resolve()
        payload_dir = staging_root / "payload"
        plugins_dir = payload_dir / "plugins"
        profiles_dir = payload_dir / "profiles"
        plugin_payload_dir = plugins_dir / plugin_id
        manifest_path = staging_root / "manifest.toml"
        metadata_path = staging_root / "metadata.toml"

        plugin_payload_dir.mkdir(parents=True, exist_ok=True)
        profiles_dir.mkdir(parents=True, exist_ok=True)

        return cls(
            staging_root=staging_root,
            payload_dir=payload_dir,
            plugins_dir=plugins_dir,
            profiles_dir=profiles_dir,
            plugin_payload_dir=plugin_payload_dir,
            manifest_path=manifest_path,
            metadata_path=metadata_path,
        )


class PluginPacker:
    """Service object that owns the single-plugin packaging pipeline."""

    def pack_plugin(self, plugin_dir: str | Path, out_file: str | Path | None = None) -> PackResult:
        source = self.load_plugin_source(plugin_dir)
        paths = PackPaths.create(plugin_id=source.plugin_id)
        pack_rules = load_pack_rules(source.pyproject_toml)
        payload = self.build_payload(source, paths, rules=pack_rules)
        self.write_manifest(source, paths)
        self.write_metadata(source, payload, paths, rules=pack_rules)

        package_path = (
            Path(out_file).expanduser().resolve()
            if out_file is not None
            else source.plugin_dir.parent / source.default_package_name
        )
        self.export_package(paths.staging_root, package_path)
        return self.build_pack_result(source, payload, package_path, paths)

    def load_plugin_source(self, plugin_dir: str | Path) -> PluginSource:
        plugin_dir = Path(plugin_dir).expanduser().resolve()
        plugin_toml_path = plugin_dir / "plugin.toml"
        if not plugin_toml_path.is_file():
            raise FileNotFoundError(f"plugin.toml not found: {plugin_toml_path}")

        plugin_toml = _load_toml(plugin_toml_path)
        plugin_table = _require_table(plugin_toml, "plugin", plugin_toml_path)
        plugin_id = _require_string(plugin_table, "id", plugin_toml_path)
        name = _optional_string(plugin_table, "name") or plugin_id
        version = _optional_string(plugin_table, "version") or "0.1.0"
        package_type = _optional_string(plugin_table, "type") or "plugin"

        pyproject_toml_path = plugin_dir / "pyproject.toml"
        pyproject_toml = _load_toml(pyproject_toml_path) if pyproject_toml_path.is_file() else None

        return PluginSource(
            plugin_dir=plugin_dir,
            plugin_toml_path=plugin_toml_path,
            pyproject_toml_path=pyproject_toml_path if pyproject_toml_path.is_file() else None,
            plugin_id=plugin_id,
            name=name,
            version=version,
            package_type=package_type,
            plugin_toml=plugin_toml,
            pyproject_toml=pyproject_toml,
        )

    def build_payload(
        self,
        source: PluginSource,
        paths: PackPaths,
        *,
        rules: PackRuleSet,
    ) -> PayloadBuildResult:
        # Payload assembly is separated from archive export so future bundle
        # packing and inspect/dry-run flows can reuse the same staging logic.
        packaged_files = self.copy_plugin_runtime_files(
            source.plugin_dir,
            paths.plugin_payload_dir,
            rules=rules,
        )
        profile_files = self.write_default_profile(source, paths.profiles_dir)
        payload_hash = self.compute_payload_hash(paths.payload_dir)

        return PayloadBuildResult(
            staging_dir=paths.staging_root,
            payload_dir=paths.payload_dir,
            plugin_payload_dir=paths.plugin_payload_dir,
            profiles_dir=paths.profiles_dir,
            packaged_files=packaged_files,
            profile_files=profile_files,
            payload_hash=payload_hash,
        )

    def copy_plugin_runtime_files(
        self,
        source_dir: Path,
        destination_dir: Path,
        *,
        rules: PackRuleSet,
    ) -> list[Path]:
        copied: list[Path] = []
        for path in sorted(source_dir.rglob("*")):
            relative = path.relative_to(source_dir)
            if self.should_skip(relative, is_dir=path.is_dir(), rules=rules):
                continue
            destination_path = destination_dir / relative
            if path.is_dir():
                destination_path.mkdir(parents=True, exist_ok=True)
                continue
            destination_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, destination_path)
            copied.append(destination_path.resolve())
        return copied

    def should_skip(self, relative_path: Path, *, is_dir: bool, rules: PackRuleSet) -> bool:
        return should_skip_path(relative_path, is_dir=is_dir, rules=rules)

    def write_default_profile(self, source: PluginSource, profiles_dir: Path) -> list[Path]:
        # First pass profile extraction is intentionally conservative: derive a
        # single portable default profile from plugin.toml, then refine later.
        profile_path = profiles_dir / "default.toml"
        lines: list[str] = [
            'name = "default"',
            f'enabled_plugins = ["{_escape_string(source.plugin_id)}"]',
            "",
            f"[plugin.{_toml_bare_or_quoted_key(source.plugin_id)}]",
            "enabled = true",
        ]

        plugin_runtime = source.plugin_toml.get("plugin_runtime")
        if isinstance(plugin_runtime, dict):
            auto_start = plugin_runtime.get("auto_start")
            if isinstance(auto_start, bool):
                lines.append(f"auto_start = {_toml_bool(auto_start)}")

        runtime_config = self.extract_runtime_config(source.plugin_toml)
        if runtime_config:
            lines.extend(
                [
                    "",
                    f"[plugin.{_toml_bare_or_quoted_key(source.plugin_id)}.{_toml_bare_or_quoted_key(source.plugin_id)}]",
                ]
            )
            lines.extend(_dump_mapping(runtime_config))

        profile_path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
        return [profile_path.resolve()]

    def extract_runtime_config(self, plugin_toml: dict[str, object]) -> dict[str, object]:
        # `plugin` and `plugin_runtime` stay as package metadata; everything else
        # is currently treated as portable runtime config for the default profile.
        reserved = {"plugin", "plugin_runtime"}
        return {
            key: value
            for key, value in plugin_toml.items()
            if key not in reserved
        }

    def write_manifest(self, source: PluginSource, paths: PackPaths) -> None:
        lines = [
            f'schema_version = "{_SCHEMA_VERSION}"',
            'package_type = "plugin"',
            "",
            f'id = "{_escape_string(source.plugin_id)}"',
            f'package_name = "{_escape_string(source.name)}"',
            f'version = "{_escape_string(source.version)}"',
        ]

        if source.description:
            lines.append(f'package_description = "{_escape_string(source.description)}"')

        lines.append("")
        content = "\n".join(lines)
        paths.manifest_path.write_text(content, encoding="utf-8")

    def write_metadata(
        self,
        source: PluginSource,
        payload: PayloadBuildResult,
        paths: PackPaths,
        *,
        rules: PackRuleSet,
    ) -> None:
        content = "\n".join(
            [
                "[payload]",
                'hash_algorithm = "sha256"',
                f'hash = "{payload.payload_hash}"',
                "",
                "[source]",
                'kind = "local"',
                f'path = "{_escape_string(str(source.plugin_dir))}"',
                "",
            ]
        )
        paths.metadata_path.write_text(content, encoding="utf-8")

    def export_package(self, staging_root: Path, package_path: Path) -> None:
        # Archive export only sees the staging tree. That keeps the resulting zip
        # layout deterministic regardless of source plugin directory structure.
        package_path.parent.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(package_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for path in sorted(staging_root.rglob("*")):
                if path.is_dir():
                    continue
                archive.write(path, arcname=path.relative_to(staging_root).as_posix())

    def compute_payload_hash(self, payload_dir: Path) -> str:
        # Hash the normalized payload content instead of the final zip bytes so
        # compression-level or zip metadata changes do not invalidate payload identity.
        digest = hashlib.sha256()
        for path in sorted(payload_dir.rglob("*")):
            if path.is_dir():
                continue
            relative = path.relative_to(payload_dir).as_posix()
            digest.update(relative.encode("utf-8"))
            digest.update(b"\0")
            digest.update(path.read_bytes())
            digest.update(b"\0")
        return digest.hexdigest()

    def build_pack_result(
        self,
        source: PluginSource,
        payload: PayloadBuildResult,
        package_path: Path,
        paths: PackPaths,
    ) -> PackResult:
        return PackResult(
            plugin_id=source.plugin_id,
            package_path=package_path,
            staging_dir=paths.staging_root,
            profile_files=payload.profile_files,
            packaged_files=payload.packaged_files,
            payload_hash=payload.payload_hash,
        )


def pack_plugin(plugin_dir: str | Path, out_file: str | Path | None = None) -> PackResult:
    """Public convenience wrapper for one-shot single-plugin packaging."""

    return PluginPacker().pack_plugin(plugin_dir=plugin_dir, out_file=out_file)


def _load_toml(path: Path) -> dict[str, object]:
    with path.open("rb") as file_obj:
        data = tomllib.load(file_obj)
    if not isinstance(data, dict):
        raise ValueError(f"TOML root must be a table: {path}")
    return data


def _require_table(data: dict[str, object], key: str, source_path: Path) -> dict[str, object]:
    value = data.get(key)
    if not isinstance(value, dict):
        raise ValueError(f"required TOML table [{key}] missing in {source_path}")
    return value


def _require_string(data: dict[str, object], key: str, source_path: Path) -> str:
    value = data.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"required string '{key}' missing in {source_path}")
    return value.strip()


def _optional_string(data: dict[str, object], key: str) -> str | None:
    value = data.get(key)
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _toml_bool(value: bool) -> str:
    return "true" if value else "false"


def _escape_string(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"')


def _toml_bare_or_quoted_key(key: str) -> str:
    if key and all(ch.isalnum() or ch in ("_", "-") for ch in key):
        return key
    return f'"{_escape_string(key)}"'


def _dump_mapping(mapping: dict[str, object]) -> list[str]:
    lines: list[str] = []
    for key, value in mapping.items():
        lines.extend(_dump_value_assignment(key, value))
    return lines


def _dump_value_assignment(key: str, value: object) -> list[str]:
    rendered = _render_toml_value(value)
    return [f"{_toml_bare_or_quoted_key(key)} = {rendered}"]


def _render_toml_value(value: object) -> str:
    if isinstance(value, bool):
        return _toml_bool(value)
    if isinstance(value, int) and not isinstance(value, bool):
        return str(value)
    if isinstance(value, float):
        return repr(value)
    if isinstance(value, str):
        return f'"{_escape_string(value)}"'
    if isinstance(value, list):
        rendered_items = ", ".join(_render_toml_value(item) for item in value)
        return f"[{rendered_items}]"
    if isinstance(value, dict):
        pairs = []
        for item_key, item_value in value.items():
            pairs.append(f"{_toml_bare_or_quoted_key(str(item_key))} = {_render_toml_value(item_value)}")
        return "{ " + ", ".join(pairs) + " }"
    if value is None:
        return '""'
    return f'"{_escape_string(str(value))}"'
