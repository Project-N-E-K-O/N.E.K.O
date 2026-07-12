"""Auditable resolution and SHA-256 verification for voice-turn assets."""

from __future__ import annotations

import hashlib
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable


MANIFEST_FILENAME = "manifest.json"
DEFAULT_ASSET_DIR_NAME = "vad_models"


class AssetManifestError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class AssetSpec:
    filename: str
    version: str
    source: str
    license: str
    sha256: str
    input_contract: str
    output_contract: str

    @classmethod
    def from_mapping(cls, value: dict[str, Any]) -> "AssetSpec":
        required = {field for field in cls.__dataclass_fields__}
        missing = sorted(required - value.keys())
        if missing:
            raise AssetManifestError(f"asset is missing fields: {', '.join(missing)}")
        spec = cls(**{key: str(value[key]) for key in required})
        if Path(spec.filename).name != spec.filename:
            raise AssetManifestError("asset filename must not contain a path")
        if len(spec.sha256) != 64 or any(c not in "0123456789abcdefABCDEF" for c in spec.sha256):
            raise AssetManifestError(f"invalid sha256 for {spec.filename}")
        return spec


@dataclass(frozen=True, slots=True)
class AssetManifest:
    schema_version: int
    assets: tuple[AssetSpec, ...]

    def asset(self, filename: str) -> AssetSpec:
        for spec in self.assets:
            if spec.filename == filename:
                return spec
        raise AssetManifestError(f"asset is not declared: {filename}")


def load_manifest(directory: Path) -> AssetManifest:
    manifest_path = directory / MANIFEST_FILENAME
    try:
        raw = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise AssetManifestError(f"cannot read {manifest_path}: {exc}") from exc
    if not isinstance(raw, dict) or raw.get("schema_version") != 1:
        raise AssetManifestError("unsupported or missing manifest schema_version")
    values = raw.get("assets")
    if not isinstance(values, list) or not values:
        raise AssetManifestError("manifest assets must be a non-empty list")
    assets = tuple(AssetSpec.from_mapping(value) for value in values if isinstance(value, dict))
    if len(assets) != len(values):
        raise AssetManifestError("every asset entry must be an object")
    names = [asset.filename for asset in assets]
    if len(names) != len(set(names)):
        raise AssetManifestError("manifest contains duplicate asset filenames")
    return AssetManifest(schema_version=1, assets=assets)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
    except OSError as exc:
        raise AssetManifestError(f"cannot read asset {path}: {exc}") from exc
    return digest.hexdigest()


def verify_asset(directory: Path, spec: AssetSpec) -> Path:
    path = directory / spec.filename
    if not path.is_file() or path.stat().st_size == 0:
        raise AssetManifestError(f"asset is missing or empty: {path}")
    actual = sha256_file(path)
    if actual.lower() != spec.sha256.lower():
        raise AssetManifestError(
            f"asset SHA-256 mismatch for {spec.filename}: expected {spec.sha256}, got {actual}"
        )
    return path


def candidate_asset_dirs(override: Path | None = None) -> tuple[Path, ...]:
    """Return override, frozen-app, and source-tree locations in priority order."""

    candidates: list[Path] = []
    if override is not None:
        candidates.append(override)
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        candidates.append(Path(meipass) / "data" / DEFAULT_ASSET_DIR_NAME)
    if getattr(sys, "frozen", False) or "__compiled__" in globals():
        candidates.append(Path(sys.executable).resolve().parent / "data" / DEFAULT_ASSET_DIR_NAME)
    candidates.append(Path(__file__).resolve().parents[2] / "data" / DEFAULT_ASSET_DIR_NAME)
    unique: list[Path] = []
    for path in candidates:
        normalized = path.resolve()
        if normalized not in unique:
            unique.append(normalized)
    return tuple(unique)


def resolve_verified_assets(
    required_filenames: Iterable[str], override: Path | None = None
) -> tuple[Path, AssetManifest, dict[str, Path]]:
    required = tuple(required_filenames)
    failures: list[str] = []
    for directory in candidate_asset_dirs(override):
        try:
            manifest = load_manifest(directory)
            paths = {
                filename: verify_asset(directory, manifest.asset(filename))
                for filename in required
            }
            return directory, manifest, paths
        except AssetManifestError as exc:
            failures.append(str(exc))
    reason = "; ".join(failures) if failures else "no candidate directories"
    raise AssetManifestError(f"no verified voice-turn asset directory: {reason}")
