from __future__ import annotations

from pathlib import Path
import re

from pydantic import BaseModel, ConfigDict, Field, computed_field, field_validator, model_validator

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


def _ensure_within(path: Path, root: Path, *, field_name: str) -> Path:
    try:
        path.relative_to(root)
    except ValueError as exc:
        raise ValueError(f"{field_name} must be located inside {root}") from exc
    return path


class _BaseModel(BaseModel):
    # These models act as boundary DTOs for the packaging pipeline, so we keep
    # them strict and reject unknown fields early.
    model_config = ConfigDict(
        arbitrary_types_allowed=True,
        validate_assignment=True,
        extra="forbid",
    )


class PluginSource(_BaseModel):
    """Normalized plugin source metadata loaded from a plugin directory."""

    plugin_dir: Path
    plugin_toml_path: Path
    pyproject_toml_path: Path | None = None
    plugin_id: str
    name: str
    version: str
    package_type: str
    plugin_toml: dict[str, object]
    pyproject_toml: dict[str, object] | None = None

    @field_validator("plugin_dir", "plugin_toml_path", mode="before")
    @classmethod
    def _validate_required_path(cls, value: Path | str) -> Path:
        return _normalize_path(value)

    @field_validator("pyproject_toml_path", mode="before")
    @classmethod
    def _validate_optional_path(cls, value: Path | str | None) -> Path | None:
        return _normalize_optional_path(value)

    @field_validator("plugin_id")
    @classmethod
    def _validate_plugin_id(cls, value: str) -> str:
        normalized = str(value).strip()
        if not normalized:
            raise ValueError("plugin_id must be a non-empty string")
        if not _PLUGIN_ID_RE.fullmatch(normalized):
            raise ValueError("plugin_id must match ^[A-Za-z0-9_-]+$")
        return normalized

    @field_validator("name", "version")
    @classmethod
    def _validate_non_empty_string(cls, value: str, info) -> str:
        normalized = str(value).strip()
        if not normalized:
            raise ValueError(f"{info.field_name} must be a non-empty string")
        return normalized

    @field_validator("package_type")
    @classmethod
    def _validate_package_type(cls, value: str) -> str:
        normalized = str(value).strip().lower()
        if normalized not in _PACKAGE_TYPES:
            raise ValueError(
                "package_type must be one of: plugin, bundle, extension, adapter"
            )
        return normalized

    @field_validator("plugin_toml")
    @classmethod
    def _validate_plugin_toml(cls, value: dict[str, object]) -> dict[str, object]:
        if not isinstance(value, dict):
            raise TypeError("plugin_toml must be a dict")
        return value

    @field_validator("pyproject_toml")
    @classmethod
    def _validate_pyproject_toml(cls, value: dict[str, object] | None) -> dict[str, object] | None:
        if value is not None and not isinstance(value, dict):
            raise TypeError("pyproject_toml must be a dict or None")
        return value

    @model_validator(mode="after")
    def _validate_layout(self) -> PluginSource:
        # The source model guarantees that later pipeline steps can treat these
        # paths as trusted inputs and avoid repeating the same filesystem checks.
        if not self.plugin_dir.is_dir():
            raise FileNotFoundError(f"plugin_dir does not exist or is not a directory: {self.plugin_dir}")
        if not self.plugin_toml_path.is_file():
            raise FileNotFoundError(f"plugin_toml_path does not exist: {self.plugin_toml_path}")
        _ensure_within(self.plugin_toml_path, self.plugin_dir, field_name="plugin_toml_path")

        if self.pyproject_toml_path is not None:
            if not self.pyproject_toml_path.is_file():
                raise FileNotFoundError(f"pyproject_toml_path does not exist: {self.pyproject_toml_path}")
            _ensure_within(self.pyproject_toml_path, self.plugin_dir, field_name="pyproject_toml_path")
        return self

    @computed_field
    @property
    def has_pyproject(self) -> bool:
        return self.pyproject_toml_path is not None

    @computed_field
    @property
    def plugin_table(self) -> dict[str, object]:
        value = self.plugin_toml.get("plugin")
        return value if isinstance(value, dict) else {}

    @computed_field
    @property
    def description(self) -> str:
        value = self.plugin_table.get("description")
        return value.strip() if isinstance(value, str) else ""

    @computed_field
    @property
    def entry_point(self) -> str:
        value = self.plugin_table.get("entry")
        return value.strip() if isinstance(value, str) else ""

    @computed_field
    @property
    def author_name(self) -> str:
        author = self.plugin_table.get("author")
        if isinstance(author, dict):
            value = author.get("name")
            if isinstance(value, str):
                return value.strip()
        return ""

    @computed_field
    @property
    def author_email(self) -> str:
        author = self.plugin_table.get("author")
        if isinstance(author, dict):
            value = author.get("email")
            if isinstance(value, str):
                return value.strip()
        return ""

    @computed_field
    @property
    def sdk_table(self) -> dict[str, object]:
        value = self.plugin_table.get("sdk")
        return value if isinstance(value, dict) else {}

    @computed_field
    @property
    def sdk_supported(self) -> str:
        value = self.sdk_table.get("supported")
        return value.strip() if isinstance(value, str) else ""

    @computed_field
    @property
    def sdk_recommended(self) -> str:
        value = self.sdk_table.get("recommended")
        return value.strip() if isinstance(value, str) else ""

    @computed_field
    @property
    def sdk_untested(self) -> str:
        value = self.sdk_table.get("untested")
        return value.strip() if isinstance(value, str) else ""

    @computed_field
    @property
    def default_package_name(self) -> str:
        return f"{self.plugin_id}-{self.version}.neko-plugin"


class PayloadBuildResult(_BaseModel):
    """Result of building the staging payload before archive export."""

    staging_dir: Path
    payload_dir: Path
    plugin_payload_dir: Path
    profiles_dir: Path
    packaged_files: list[Path] = Field(default_factory=list)
    profile_files: list[Path] = Field(default_factory=list)
    payload_hash: str

    @field_validator("staging_dir", "payload_dir", "plugin_payload_dir", "profiles_dir", mode="before")
    @classmethod
    def _validate_path(cls, value: Path | str) -> Path:
        return _normalize_path(value)

    @field_validator("packaged_files", "profile_files", mode="before")
    @classmethod
    def _validate_path_list(cls, value: list[Path] | list[str]) -> list[Path]:
        normalized = [_normalize_path(item) for item in value]
        normalized.sort()
        return normalized

    @field_validator("payload_hash")
    @classmethod
    def _validate_sha256(cls, value: str) -> str:
        normalized = str(value).strip().lower()
        if not _HEX_SHA256_RE.fullmatch(normalized):
            raise ValueError("payload_hash must be a 64-character lowercase sha256 hex string")
        return normalized

    @model_validator(mode="after")
    def _validate_layout(self) -> PayloadBuildResult:
        # These checks intentionally verify path relationships, not only
        # existence, so later steps cannot accidentally point outside staging.
        for field_name in ("staging_dir", "payload_dir", "plugin_payload_dir", "profiles_dir"):
            path = getattr(self, field_name)
            if not path.exists():
                raise FileNotFoundError(f"{field_name} does not exist: {path}")

        _ensure_within(self.payload_dir, self.staging_dir, field_name="payload_dir")
        _ensure_within(self.plugin_payload_dir, self.payload_dir, field_name="plugin_payload_dir")
        _ensure_within(self.profiles_dir, self.payload_dir, field_name="profiles_dir")

        for file_path in self.packaged_files:
            if not file_path.is_file():
                raise FileNotFoundError(f"packaged file does not exist: {file_path}")
            _ensure_within(file_path, self.plugin_payload_dir, field_name="packaged_files item")

        for file_path in self.profile_files:
            if not file_path.is_file():
                raise FileNotFoundError(f"profile file does not exist: {file_path}")
            _ensure_within(file_path, self.profiles_dir, field_name="profile_files item")
        return self

    @computed_field
    @property
    def packaged_file_count(self) -> int:
        return len(self.packaged_files)

    @computed_field
    @property
    def profile_file_count(self) -> int:
        return len(self.profile_files)


class PackResult(_BaseModel):
    """Final result returned by the public `pack_plugin(...)` entrypoint."""

    plugin_id: str
    package_path: Path
    staging_dir: Path | None = None
    profile_files: list[Path] = Field(default_factory=list)
    packaged_files: list[Path] = Field(default_factory=list)
    payload_hash: str

    @field_validator("plugin_id")
    @classmethod
    def _validate_plugin_id(cls, value: str) -> str:
        normalized = str(value).strip()
        if not normalized:
            raise ValueError("plugin_id must be a non-empty string")
        if not _PLUGIN_ID_RE.fullmatch(normalized):
            raise ValueError("plugin_id must match ^[A-Za-z0-9_-]+$")
        return normalized

    @field_validator("package_path", mode="before")
    @classmethod
    def _validate_path(cls, value: Path | str) -> Path:
        return _normalize_path(value)

    @field_validator("staging_dir", mode="before")
    @classmethod
    def _validate_optional_path(cls, value: Path | str | None) -> Path | None:
        return _normalize_optional_path(value)

    @field_validator("packaged_files", "profile_files", mode="before")
    @classmethod
    def _validate_path_list(cls, value: list[Path] | list[str]) -> list[Path]:
        normalized = [_normalize_path(item) for item in value]
        normalized.sort()
        return normalized

    @field_validator("payload_hash")
    @classmethod
    def _validate_sha256(cls, value: str) -> str:
        normalized = str(value).strip().lower()
        if not _HEX_SHA256_RE.fullmatch(normalized):
            raise ValueError("payload_hash must be a 64-character lowercase sha256 hex string")
        return normalized

    @model_validator(mode="after")
    def _validate_layout(self) -> PackResult:
        if not self.package_path.is_file():
            raise FileNotFoundError(f"package_path does not exist: {self.package_path}")
        if self.package_path.suffix not in _PACKAGE_SUFFIXES:
            raise ValueError("package_path must use .neko-plugin or .neko-bundle extension")
        if self.staging_dir is not None and not self.staging_dir.exists():
            raise FileNotFoundError(f"staging_dir does not exist: {self.staging_dir}")

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


class SharedDependency(_BaseModel):
    """Dependency referenced by multiple plugins in a bundle candidate."""

    name: str
    plugin_ids: list[str] = Field(default_factory=list)
    requirement_texts: dict[str, str] = Field(default_factory=dict)

    @computed_field
    @property
    def plugin_count(self) -> int:
        return len(self.plugin_ids)


class BundleSdkAnalysis(_BaseModel):
    """Lightweight SDK compatibility summary across multiple plugins."""

    kind: str
    plugin_specifiers: dict[str, str] = Field(default_factory=dict)
    has_overlap: bool
    matching_versions: list[str] = Field(default_factory=list)
    current_sdk_version: str = ""
    current_sdk_supported_by_all: bool | None = None


class BundleAnalysisResult(_BaseModel):
    """Pre-pack analysis result for bundle candidates."""

    plugin_ids: list[str] = Field(default_factory=list)
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

    plugin_id: str
    archive_path: str
    has_plugin_toml: bool = True

    @field_validator("plugin_id", "archive_path")
    @classmethod
    def _validate_non_empty_string(cls, value: str, info) -> str:
        normalized = str(value).strip()
        if not normalized:
            raise ValueError(f"{info.field_name} must be a non-empty string")
        return normalized


class PackageInspectResult(_BaseModel):
    """Read-only inspection summary for a package archive."""

    package_path: Path
    package_type: str
    package_id: str
    schema_version: str = ""
    package_name: str = ""
    package_description: str = ""
    version: str = ""
    metadata_found: bool = False
    payload_hash: str = ""
    payload_hash_verified: bool | None = None
    plugins: list[InspectedPackagePlugin] = Field(default_factory=list)
    profile_names: list[str] = Field(default_factory=list)

    @field_validator("package_path", mode="before")
    @classmethod
    def _validate_path(cls, value: Path | str) -> Path:
        return _normalize_path(value)

    @field_validator(
        "package_type",
        "package_id",
        "schema_version",
        "package_name",
        "package_description",
        "version",
        mode="before",
    )
    @classmethod
    def _normalize_string(cls, value: object) -> str:
        if value is None:
            return ""
        return str(value).strip()

    @field_validator("payload_hash")
    @classmethod
    def _validate_payload_hash(cls, value: str) -> str:
        normalized = str(value).strip().lower()
        if not normalized:
            return ""
        if not _HEX_SHA256_RE.fullmatch(normalized):
            raise ValueError("payload_hash must be empty or a 64-character lowercase sha256 hex string")
        return normalized

    @field_validator("profile_names", mode="before")
    @classmethod
    def _normalize_profile_names(cls, value: list[str]) -> list[str]:
        normalized = sorted({str(item).strip() for item in value if str(item).strip()})
        return normalized

    @model_validator(mode="after")
    def _validate_layout(self) -> PackageInspectResult:
        if not self.package_path.is_file():
            raise FileNotFoundError(f"package_path does not exist: {self.package_path}")
        if self.package_type not in {"plugin", "bundle"}:
            raise ValueError("package_type must be either plugin or bundle")
        if not self.package_id:
            raise ValueError("package_id must be a non-empty string")
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

    source_folder: str
    target_plugin_id: str
    target_dir: Path
    renamed: bool = False

    @field_validator("source_folder", "target_plugin_id")
    @classmethod
    def _validate_non_empty(cls, value: str, info) -> str:
        normalized = str(value).strip()
        if not normalized:
            raise ValueError(f"{info.field_name} must be a non-empty string")
        return normalized

    @field_validator("target_dir", mode="before")
    @classmethod
    def _validate_target_dir(cls, value: Path | str) -> Path:
        return _normalize_path(value)


class UnpackResult(_BaseModel):
    """Result of extracting a package archive into runtime directories."""

    package_path: Path
    package_type: str
    package_id: str
    plugins_root: Path
    profiles_root: Path | None = None
    unpacked_plugins: list[UnpackedPlugin] = Field(default_factory=list)
    profile_dir: Path | None = None
    metadata_found: bool = False
    payload_hash: str = ""
    payload_hash_verified: bool | None = None
    conflict_strategy: str = "rename"

    @field_validator("package_path", "plugins_root", mode="before")
    @classmethod
    def _validate_required_path(cls, value: Path | str) -> Path:
        return _normalize_path(value)

    @field_validator("profiles_root", "profile_dir", mode="before")
    @classmethod
    def _validate_optional_path(cls, value: Path | str | None) -> Path | None:
        return _normalize_optional_path(value)

    @field_validator("package_type")
    @classmethod
    def _validate_package_type(cls, value: str) -> str:
        normalized = str(value).strip().lower()
        if normalized not in {"plugin", "bundle"}:
            raise ValueError("package_type must be either plugin or bundle")
        return normalized

    @field_validator("package_id")
    @classmethod
    def _validate_package_id(cls, value: str) -> str:
        normalized = str(value).strip()
        if not normalized:
            raise ValueError("package_id must be a non-empty string")
        return normalized

    @field_validator("payload_hash")
    @classmethod
    def _validate_payload_hash(cls, value: str) -> str:
        normalized = str(value).strip().lower()
        if not normalized:
            return ""
        if not _HEX_SHA256_RE.fullmatch(normalized):
            raise ValueError("payload_hash must be empty or a 64-character lowercase sha256 hex string")
        return normalized

    @field_validator("conflict_strategy")
    @classmethod
    def _validate_conflict_strategy(cls, value: str) -> str:
        normalized = str(value).strip().lower()
        if normalized not in {"rename", "fail"}:
            raise ValueError("conflict_strategy must be either rename or fail")
        return normalized

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
