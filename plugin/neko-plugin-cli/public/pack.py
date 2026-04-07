from __future__ import annotations

import hashlib
import shutil
import tempfile
import zipfile
from dataclasses import dataclass
from pathlib import Path

from .models import PackResult, PayloadBuildResult, PluginSource
from .pack_rules import PackRuleSet, load_pack_rules, should_skip_path
from .plugin_source import load_plugin_source
from .profile import write_default_profile
from .toml_utils import escape_string

_SCHEMA_VERSION = "1.0"


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

    def pack_plugin(
        self,
        plugin_dir: str | Path,
        out_file: str | Path | None = None,
        *,
        keep_staging: bool = False,
    ) -> PackResult:
        source = load_plugin_source(plugin_dir)
        if source.package_type != "plugin":
            raise ValueError(
                f"single-plugin pack only supports package_type='plugin', got {source.package_type!r}"
            )
        paths = PackPaths.create(plugin_id=source.plugin_id)
        try:
            pack_rules = load_pack_rules(source.pyproject_toml)
            payload = self.build_payload(source, paths, rules=pack_rules)
            self.write_manifest(source, paths)
            self.write_metadata(source, payload, paths)

            package_path = (
                Path(out_file).expanduser().resolve()
                if out_file is not None
                else source.plugin_dir.parent / source.default_package_name
            )
            self.export_package(paths.staging_root, package_path)
            return self.build_pack_result(
                source,
                payload,
                package_path,
                paths,
                keep_staging=keep_staging,
            )
        finally:
            if not keep_staging:
                shutil.rmtree(paths.staging_root, ignore_errors=True)

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
        profile_files = write_default_profile(source, paths.profiles_dir)
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

    def write_manifest(self, source: PluginSource, paths: PackPaths) -> None:
        lines = [
            f'schema_version = "{_SCHEMA_VERSION}"',
            f'package_type = "{escape_string(source.package_type)}"',
            "",
            f'id = "{escape_string(source.plugin_id)}"',
            f'package_name = "{escape_string(source.name)}"',
            f'version = "{escape_string(source.version)}"',
        ]

        if source.description:
            lines.append(f'package_description = "{escape_string(source.description)}"')

        lines.append("")
        content = "\n".join(lines)
        paths.manifest_path.write_text(content, encoding="utf-8")

    def write_metadata(
        self,
        source: PluginSource,
        payload: PayloadBuildResult,
        paths: PackPaths,
    ) -> None:
        content = "\n".join(
            [
                "[payload]",
                'hash_algorithm = "sha256"',
                f'hash = "{payload.payload_hash}"',
                "",
                "[source]",
                'kind = "local"',
                f'path = "{escape_string(str(source.plugin_dir))}"',
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
        *,
        keep_staging: bool,
    ) -> PackResult:
        return PackResult(
            plugin_id=source.plugin_id,
            package_path=package_path,
            staging_dir=paths.staging_root if keep_staging else None,
            profile_files=payload.profile_files if keep_staging else [],
            packaged_files=payload.packaged_files if keep_staging else [],
            payload_hash=payload.payload_hash,
        )


def pack_plugin(
    plugin_dir: str | Path,
    out_file: str | Path | None = None,
    *,
    keep_staging: bool = False,
) -> PackResult:
    """Public convenience wrapper for one-shot single-plugin packaging."""

    return PluginPacker().pack_plugin(
        plugin_dir=plugin_dir,
        out_file=out_file,
        keep_staging=keep_staging,
    )
