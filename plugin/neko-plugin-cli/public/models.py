from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
import re
from typing import Annotated, Literal

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field, computed_field, model_validator

# Keep low-level validation helpers module-local so the public models stay small
# and consistent across CLI/API/service usage.
_PLUGIN_ID_RE = re.compile(r"^[A-Za-z0-9_-]+$")
_HEX_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_PACKAGE_TYPES = {"plugin", "bundle", "extension", "adapter"}
_PACKAGE_SUFFIXES = {".neko-plugin", ".neko-bundle"}


def _normalize_path(value: Path | str) -> Path:
    return Path(value).expanduser().resolve()


def _normalize_optional_path(value: Path | str | None) -> Path | None:
    if value is None:
        return None
    return _normalize_path(value)


def _normalize_text(value: object) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _ensure_within(path: Path, root: Path, *, field_name: str) -> Path:
    try:
        path.relative_to(root)
    except ValueError as exc:
        raise ValueError(f"{field_name} must be located inside {root}") from exc
    return path


def _normalize_plugin_id(value: str) -> str:
    normalized = str(value).strip()
    if not normalized:
        raise ValueError("plugin_id must be a non-empty string")
    if not _PLUGIN_ID_RE.fullmatch(normalized):
        raise ValueError("plugin_id must match ^[A-Za-z0-9_-]+$")
    return normalized


def _normalize_required_string(value: str, *, field_name: str) -> str:
    normalized = _normalize_text(value)
    if not normalized:
        raise ValueError(f"{field_name} must be a non-empty string")
    return normalized


def _normalize_package_type(value: str) -> str:
    normalized = str(value).strip().lower()
    if normalized not in _PACKAGE_TYPES:
        raise ValueError("package_type must be one of: plugin, bundle, extension, adapter")
    return normalized


def _normalize_sha256(value: str) -> str:
    normalized = _normalize_text(value).lower()
    if not _HEX_SHA256_RE.fullmatch(normalized):
        raise ValueError("payload_hash must be a 64-character lowercase sha256 hex string")
    return normalized


def _normalize_optional_sha256(value: object) -> str:
    normalized = _normalize_text(value).lower()
    if not normalized:
        return ""
    return _normalize_sha256(normalized)


def _normalize_boundary_package_type(value: object) -> str:
    normalized = _normalize_text(value).lower()
    if normalized not in {"plugin", "bundle"}:
        raise ValueError("package_type must be either plugin or bundle")
    return normalized


def _normalize_conflict_strategy(value: object) -> str:
    normalized = _normalize_text(value).lower()
    if normalized not in {"rename", "fail"}:
        raise ValueError("conflict_strategy must be either rename or fail")
    return normalized


def _normalize_non_empty_text(value: object) -> str:
    normalized = _normalize_text(value)
    if not normalized:
        raise ValueError("value must be a non-empty string")
    return normalized


def _normalize_path_list(value: list[Path] | list[str] | None) -> list[Path]:
    if not value:
        return []
    return sorted(_normalize_path(item) for item in value)


def _normalize_plugin_ids(value: list[str] | None) -> list[str]:
    if not value:
        return []
    normalized = sorted({_normalize_plugin_id(item) for item in value if _normalize_text(item)})
    return normalized


def _normalize_string_list(value: list[str] | None) -> list[str]:
    if not value:
        return []
    return sorted({item for item in (_normalize_text(raw) for raw in value) if item})


ResolvedPath = Annotated[Path, BeforeValidator(_normalize_path)]
OptionalResolvedPath = Annotated[Path | None, BeforeValidator(_normalize_optional_path)]
ResolvedPathList = Annotated[list[Path], BeforeValidator(_normalize_path_list)]
PluginIdValue = Annotated[str, BeforeValidator(_normalize_plugin_id)]
PackageTypeValue = Annotated[Literal["plugin", "bundle"], BeforeValidator(_normalize_boundary_package_type)]
OptionalText = Annotated[str, BeforeValidator(_normalize_text)]
PayloadHashValue = Annotated[str, BeforeValidator(_normalize_sha256)]
OptionalPayloadHashValue = Annotated[str, BeforeValidator(_normalize_optional_sha256)]
PluginIdList = Annotated[list[str], BeforeValidator(_normalize_plugin_ids)]
StringList = Annotated[list[str], BeforeValidator(_normalize_string_list)]
NonEmptyText = Annotated[str, BeforeValidator(_normalize_non_empty_text)]
ConflictStrategyValue = Annotated[Literal["rename", "fail"], BeforeValidator(_normalize_conflict_strategy)]


class _BaseModel(BaseModel):
    # These models act as boundary DTOs for the packaging pipeline, so we keep
    # them strict and reject unknown fields early.
    model_config = ConfigDict(
        arbitrary_types_allowed=True,
        validate_assignment=True,
        extra="forbid",
    )


@dataclass(slots=True)
class PluginSource:
    """Normalized plugin metadata used inside the pack/analyze pipeline."""

    plugin_dir: Path
    plugin_toml_path: Path
    pyproject_toml_path: Path | None = None
    plugin_id: str = ""
    name: str = ""
    version: str = ""
    package_type: str = "plugin"
    plugin_toml: dict[str, object] = field(default_factory=dict)
    pyproject_toml: dict[str, object] | None = None

    def __post_init__(self) -> None:
        self.plugin_dir = _normalize_path(self.plugin_dir)
        self.plugin_toml_path = _normalize_path(self.plugin_toml_path)
        self.pyproject_toml_path = _normalize_optional_path(self.pyproject_toml_path)
        self.plugin_id = _normalize_plugin_id(self.plugin_id)
        self.name = _normalize_required_string(self.name, field_name="name")
        self.version = _normalize_required_string(self.version, field_name="version")
        self.package_type = _normalize_package_type(self.package_type)
        if not isinstance(self.plugin_toml, dict):
            raise TypeError("plugin_toml must be a dict")
        if self.pyproject_toml is not None and not isinstance(self.pyproject_toml, dict):
            raise TypeError("pyproject_toml must be a dict or None")

    @property
    def has_pyproject(self) -> bool:
        return self.pyproject_toml_path is not None

    @property
    def plugin_table(self) -> dict[str, object]:
        value = self.plugin_toml.get("plugin")
        return value if isinstance(value, dict) else {}

    @property
    def description(self) -> str:
        value = self.plugin_table.get("description")
        return value.strip() if isinstance(value, str) else ""

    @property
    def entry_point(self) -> str:
        value = self.plugin_table.get("entry")
        return value.strip() if isinstance(value, str) else ""

    @property
    def author_name(self) -> str:
        author = self.plugin_table.get("author")
        if isinstance(author, dict):
            value = author.get("name")
            if isinstance(value, str):
                return value.strip()
        return ""

    @property
    def author_email(self) -> str:
        author = self.plugin_table.get("author")
        if isinstance(author, dict):
            value = author.get("email")
            if isinstance(value, str):
                return value.strip()
        return ""

    @property
    def sdk_table(self) -> dict[str, object]:
        value = self.plugin_table.get("sdk")
        return value if isinstance(value, dict) else {}

    @property
    def sdk_supported(self) -> str:
        value = self.sdk_table.get("supported")
        return value.strip() if isinstance(value, str) else ""

    @property
    def sdk_recommended(self) -> str:
        value = self.sdk_table.get("recommended")
        return value.strip() if isinstance(value, str) else ""

    @property
    def sdk_untested(self) -> str:
        value = self.sdk_table.get("untested")
        return value.strip() if isinstance(value, str) else ""

    @property
    def default_package_name(self) -> str:
        return f"{self.plugin_id}-{self.version}.neko-plugin"


@dataclass(slots=True)
class PayloadBuildResult:
    """In-memory payload staging summary used before archive export."""

    staging_dir: Path
    payload_dir: Path
    plugin_payload_dir: Path
    profiles_dir: Path
    packaged_files: list[Path] = field(default_factory=list)
    profile_files: list[Path] = field(default_factory=list)
    payload_hash: str = ""

    def __post_init__(self) -> None:
        self.staging_dir = _normalize_path(self.staging_dir)
        self.payload_dir = _normalize_path(self.payload_dir)
        self.plugin_payload_dir = _normalize_path(self.plugin_payload_dir)
        self.profiles_dir = _normalize_path(self.profiles_dir)
        self.packaged_files = sorted(_normalize_path(item) for item in self.packaged_files)
        self.profile_files = sorted(_normalize_path(item) for item in self.profile_files)
        self.payload_hash = _normalize_sha256(self.payload_hash)

    @property
    def packaged_file_count(self) -> int:
        return len(self.packaged_files)

    @property
    def profile_file_count(self) -> int:
        return len(self.profile_files)


class PackResult(_BaseModel):
    """Final result returned by the public `pack_plugin(...)` entrypoint."""

    plugin_id: PluginIdValue
    package_type: PackageTypeValue = "plugin"
    plugin_ids: PluginIdList = Field(default_factory=list)
    package_name: OptionalText = ""
    version: OptionalText = ""
    package_path: ResolvedPath
    staging_dir: OptionalResolvedPath = None
    profile_files: ResolvedPathList = Field(default_factory=list)
    packaged_files: ResolvedPathList = Field(default_factory=list)
    payload_hash: PayloadHashValue

    @model_validator(mode="after")
    def _validate_layout(self) -> PackResult:
        if not self.package_path.is_file():
            raise FileNotFoundError(f"package_path does not exist: {self.package_path}")
        if self.package_path.suffix not in _PACKAGE_SUFFIXES:
            raise ValueError("package_path must use .neko-plugin or .neko-bundle extension")
        if self.package_type == "plugin" and self.package_path.suffix != ".neko-plugin":
            raise ValueError("plugin package_path must use .neko-plugin extension")
        if self.package_type == "bundle" and self.package_path.suffix != ".neko-bundle":
            raise ValueError("bundle package_path must use .neko-bundle extension")
        if self.staging_dir is not None and not self.staging_dir.exists():
            raise FileNotFoundError(f"staging_dir does not exist: {self.staging_dir}")
        if self.package_type == "plugin":
            if self.plugin_ids and self.plugin_ids != [self.plugin_id]:
                raise ValueError("plugin package plugin_ids must be empty or contain only plugin_id")
        if self.package_type == "bundle" and len(self.plugin_ids) < 2:
            raise ValueError("bundle package must contain at least two plugin_ids")

        for file_path in self.profile_files:
            if not file_path.is_file():
                raise FileNotFoundError(f"profile file does not exist: {file_path}")
        for file_path in self.packaged_files:
            if not file_path.is_file():
                raise FileNotFoundError(f"packaged file does not exist: {file_path}")
        return self

    @computed_field
    @property
    def package_size_bytes(self) -> int:
        return self.package_path.stat().st_size

    @computed_field
    @property
    def packaged_file_count(self) -> int:
        return len(self.packaged_files)

    @computed_field
    @property
    def profile_file_count(self) -> int:
        return len(self.profile_files)

    @computed_field
    @property
    def plugin_count(self) -> int:
        return len(self.plugin_ids) if self.plugin_ids else 1


class SharedDependency(_BaseModel):
    """Dependency referenced by multiple plugins in a bundle candidate."""

    name: str
    plugin_ids: PluginIdList = Field(default_factory=list)
    requirement_texts: dict[str, str] = Field(default_factory=dict)

    @computed_field
    @property
    def plugin_count(self) -> int:
        return len(self.plugin_ids)


class BundleSdkAnalysis(_BaseModel):
    """Lightweight SDK compatibility summary across multiple plugins."""

    kind: OptionalText
    plugin_specifiers: dict[str, str] = Field(default_factory=dict)
    has_overlap: bool
    matching_versions: StringList = Field(default_factory=list)
    current_sdk_version: OptionalText = ""
    current_sdk_supported_by_all: bool | None = None


class BundleAnalysisResult(_BaseModel):
    """Pre-pack analysis result for bundle candidates."""

    plugin_ids: PluginIdList = Field(default_factory=list)
    shared_dependencies: list[SharedDependency] = Field(default_factory=list)
    common_dependencies: list[SharedDependency] = Field(default_factory=list)
    sdk_supported_analysis: BundleSdkAnalysis | None = None
    sdk_recommended_analysis: BundleSdkAnalysis | None = None

    @computed_field
    @property
    def plugin_count(self) -> int:
        return len(self.plugin_ids)


class InspectedPackagePlugin(_BaseModel):
    """Plugin entry discovered inside a packaged archive payload."""

    plugin_id: PluginIdValue
    archive_path: NonEmptyText
    has_plugin_toml: bool = True


class PackageInspectResult(_BaseModel):
    """Read-only inspection summary for a package archive."""

    package_path: ResolvedPath
    package_type: PackageTypeValue
    package_id: NonEmptyText
    schema_version: OptionalText = ""
    package_name: OptionalText = ""
    package_description: OptionalText = ""
    version: OptionalText = ""
    metadata_found: bool = False
    payload_hash: OptionalPayloadHashValue = ""
    payload_hash_verified: bool | None = None
    plugins: list[InspectedPackagePlugin] = Field(default_factory=list)
    profile_names: StringList = Field(default_factory=list)

    @model_validator(mode="after")
    def _validate_layout(self) -> PackageInspectResult:
        if not self.package_path.is_file():
            raise FileNotFoundError(f"package_path does not exist: {self.package_path}")
        return self

    @computed_field
    @property
    def plugin_count(self) -> int:
        return len(self.plugins)

    @computed_field
    @property
    def profile_count(self) -> int:
        return len(self.profile_names)


class UnpackedPlugin(_BaseModel):
    """Mapping between archived plugin folder and final extracted folder."""

    source_folder: NonEmptyText
    target_plugin_id: PluginIdValue
    target_dir: ResolvedPath
    renamed: bool = False


class UnpackResult(_BaseModel):
    """Result of extracting a package archive into runtime directories."""

    package_path: ResolvedPath
    package_type: PackageTypeValue
    package_id: NonEmptyText
    plugins_root: ResolvedPath
    profiles_root: OptionalResolvedPath = None
    unpacked_plugins: list[UnpackedPlugin] = Field(default_factory=list)
    profile_dir: OptionalResolvedPath = None
    metadata_found: bool = False
    payload_hash: OptionalPayloadHashValue = ""
    payload_hash_verified: bool | None = None
    conflict_strategy: ConflictStrategyValue = "rename"

    @model_validator(mode="after")
    def _validate_layout(self) -> UnpackResult:
        if not self.package_path.is_file():
            raise FileNotFoundError(f"package_path does not exist: {self.package_path}")
        if not self.plugins_root.exists():
            raise FileNotFoundError(f"plugins_root does not exist: {self.plugins_root}")
        if not self.plugins_root.is_dir():
            raise NotADirectoryError(f"plugins_root is not a directory: {self.plugins_root}")
        if self.profiles_root is not None and not self.profiles_root.is_dir():
            raise NotADirectoryError(f"profiles_root is not a directory: {self.profiles_root}")
        if self.profile_dir is not None and self.profiles_root is not None:
            _ensure_within(self.profile_dir, self.profiles_root, field_name="profile_dir")
        for item in self.unpacked_plugins:
            _ensure_within(item.target_dir, self.plugins_root, field_name="unpacked plugin target_dir")
        return self

    @computed_field
    @property
    def unpacked_plugin_count(self) -> int:
        return len(self.unpacked_plugins)
