from __future__ import annotations

from pathlib import Path
import re

from pydantic import BaseModel, ConfigDict, Field, computed_field, field_validator, model_validator

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
    model_config = ConfigDict(
        arbitrary_types_allowed=True,
        validate_assignment=True,
        extra="forbid",
    )


class PluginSource(_BaseModel):
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
    def default_package_name(self) -> str:
        return f"{self.plugin_id}-{self.version}.neko-plugin"


class PayloadBuildResult(_BaseModel):
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
    plugin_id: str
    package_path: Path
    staging_dir: Path
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

    @field_validator("package_path", "staging_dir", mode="before")
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
    def _validate_layout(self) -> PackResult:
        if not self.package_path.is_file():
            raise FileNotFoundError(f"package_path does not exist: {self.package_path}")
        if self.package_path.suffix not in _PACKAGE_SUFFIXES:
            raise ValueError("package_path must use .neko-plugin or .neko-bundle extension")
        if not self.staging_dir.exists():
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
