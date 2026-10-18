"""
Plugin configuration schema validation

Provides structural validation of plugin configuration files (plugin.toml).
Uses Pydantic v2 for type checking and constraint validation.
"""
from __future__ import annotations

import math
import re
from typing import Annotated, Any, Dict, List, Literal, Optional, Union

from pydantic import BaseModel, Field, StrictBool, StrictStr, TypeAdapter, ValidationError, field_validator, model_validator
from plugin._types.plugin_types import (
    PluginType,
    SUPPORTED_PLUGIN_TYPES,
    format_plugin_type_choice_error,
    format_removed_plugin_host,
    require_supported_plugin_type,
)

_PLUGIN_RUNTIME_TIMEOUT_MAX = 300.0
_PLUGIN_INSTALL_KIND_PATTERN = re.compile(r"^[a-z][a-z0-9_]*$")

ModelCapability = Literal["text", "image_input", "tool_calling", "streaming"]
PluginModelUsageId = Annotated[
    StrictStr,
    Field(min_length=1, max_length=64, pattern=r"^[a-z][a-z0-9_]*$"),
]


class PluginModelRequirementSchema(BaseModel):
    """One model purpose declared under ``[plugin.models.<usage_id>]``.

    The usage ID is stable across user-managed model-slot changes. These
    declarations describe requirements only; bindings and credentials belong
    to the host configuration.
    """

    model_config = {"extra": "forbid"}

    label: StrictStr = Field(min_length=1, max_length=256)
    description: StrictStr = Field(default="", max_length=2048)
    required: StrictBool = True
    capabilities: List[ModelCapability] = Field(default_factory=lambda: ["text"], strict=True)

    @field_validator("label")
    @classmethod
    def validate_label(cls, value: str) -> str:
        if value.strip() != value:
            raise ValueError("label must be non-empty and unpadded")
        return value

    @field_validator("capabilities")
    @classmethod
    def validate_capabilities(cls, value: List[ModelCapability]) -> List[ModelCapability]:
        if len(value) != len(set(value)):
            raise ValueError("model capabilities must not contain duplicates")
        return value


class PluginAuthorSchema(BaseModel):
    """Plugin author information schema"""
    name: Optional[str] = None
    email: Optional[str] = None
    url: Optional[str] = None


class PluginSdkSchema(BaseModel):
    """SDK version-constraint schema"""
    recommended: Optional[str] = None
    supported: Optional[str] = None
    untested: Optional[str] = None
    conflicts: Optional[List[str]] = None


class PluginStoreSchema(BaseModel):
    """Plugin storage-configuration schema"""
    enabled: bool = False
    backend: Optional[str] = None


class PluginConfigProfilesFilesSchema(BaseModel):
    """Profile file-mapping schema"""
    model_config = {"extra": "allow"}  # Allow arbitrary profile names as keys


class PluginConfigProfilesSchema(BaseModel):
    """Profile configuration schema"""
    active: Optional[str] = None
    files: Optional[Dict[str, str]] = None


class PluginSafetySchema(BaseModel):
    """Plugin security-configuration schema"""
    sync_call_in_handler: Optional[Literal["warn", "reject"]] = None


class PluginInstallKindSchema(BaseModel):
    """One host-managed install action declared by a plugin manifest."""

    model_config = {"extra": "forbid"}

    entry_id: StrictStr
    label: StrictStr
    queued_message: StrictStr
    entry_timeout: float

    @field_validator("entry_id")
    @classmethod
    def validate_entry_id(cls, value: str) -> str:
        if not value or value.strip() != value:
            raise ValueError("entry_id must be non-empty and unpadded")
        return value

    @field_validator("label")
    @classmethod
    def validate_label(cls, value: str) -> str:
        if not value or value.strip() != value:
            raise ValueError("label must be non-empty and unpadded")
        return value

    @field_validator("queued_message")
    @classmethod
    def validate_queued_message(cls, value: str) -> str:
        if not value or value.strip() != value:
            raise ValueError("queued_message must be non-empty and unpadded")
        return value

    @field_validator("entry_timeout", mode="before")
    @classmethod
    def validate_entry_timeout(cls, value: object) -> object:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError("entry_timeout must be a finite number greater than zero")
        try:
            timeout = float(value)
        except (OverflowError, ValueError) as exc:
            raise ValueError(
                "entry_timeout must be a finite number greater than zero"
            ) from exc
        if not math.isfinite(timeout) or timeout <= 0:
            raise ValueError("entry_timeout must be a finite number greater than zero")
        return timeout


class PluginInstallSchema(BaseModel):
    """Declarative install capabilities under ``[plugin.install]``."""

    model_config = {"extra": "forbid"}

    enabled: StrictBool
    ui_i18n_dir: Optional[StrictStr] = None
    tutorial_enabled: StrictBool = False
    kinds: Dict[str, PluginInstallKindSchema] = Field(default_factory=dict)

    @field_validator("kinds")
    @classmethod
    def validate_kind_names(
        cls,
        value: Dict[str, PluginInstallKindSchema],
    ) -> Dict[str, PluginInstallKindSchema]:
        for kind in value:
            if not _PLUGIN_INSTALL_KIND_PATTERN.fullmatch(kind):
                raise ValueError(
                    "install kinds must match ^[a-z][a-z0-9_]*$ without normalization"
                )
        return value

    @field_validator("ui_i18n_dir")
    @classmethod
    def validate_i18n_path_string(cls, value: str | None) -> str | None:
        if value is not None and (not value or value.strip() != value):
            raise ValueError("ui_i18n_dir must be non-empty and unpadded")
        return value

    @model_validator(mode="after")
    def validate_disabled_contract(self) -> "PluginInstallSchema":
        if self.enabled:
            return self
        if self.kinds:
            raise ValueError("disabled plugin install declarations must not define kinds")
        if self.tutorial_enabled:
            raise ValueError("disabled plugin install declarations must not enable tutorials")
        if self.ui_i18n_dir is not None:
            raise ValueError("disabled plugin install declarations must not define ui_i18n_dir")
        return self


class PluginDependencySchema(BaseModel):
    """Plugin dependency schema"""
    id: Optional[str] = None
    entry: Optional[str] = None
    custom_event: Optional[str] = None
    providers: Optional[List[str]] = None
    recommended: Optional[str] = None
    supported: Optional[str] = None
    untested: Optional[str] = None
    conflicts: Optional[Union[List[str], bool]] = None

    @model_validator(mode="after")
    def validate_dependency(self) -> "PluginDependencySchema":
        if not any([self.id, self.entry, self.custom_event, self.providers]):
            raise ValueError("依赖配置至少需要提供 id、entry、custom_event 或 providers 中的一个")
        if self.entry is not None and self.custom_event is not None:
            raise ValueError("entry 和 custom_event 不能同时使用")
        return self


class PluginSectionSchema(BaseModel):
    """[plugin] section schema - required core configuration"""
    # Required fields
    id: str = Field(..., min_length=1, max_length=128, pattern=r'^[a-zA-Z0-9_-]+$')
    name: str = Field(..., min_length=1, max_length=256)
    entry: str = Field(..., pattern=r'^[a-zA-Z0-9_.]+:[a-zA-Z0-9_]+$')
    
    # Optional fields
    type: PluginType = "plugin"
    description: str = ""
    version: str = Field(default="0.1.0", pattern=r'^\d+\.\d+\.\d+.*$')
    
    # Nested configuration
    author: Optional[PluginAuthorSchema] = None
    sdk: Optional[PluginSdkSchema] = None
    store: Optional[PluginStoreSchema] = None
    config_profiles: Optional[PluginConfigProfilesSchema] = None
    safety: Optional[PluginSafetySchema] = None
    install: Optional[PluginInstallSchema] = None
    models: Dict[PluginModelUsageId, PluginModelRequirementSchema] = Field(default_factory=dict)
    dependencies: Optional[List[PluginDependencySchema]] = None

    @model_validator(mode="before")
    @classmethod
    def reject_removed_host_config(cls, value: object) -> object:
        if isinstance(value, dict) and "host" in value:
            raise ValueError(format_removed_plugin_host())
        return value

    @field_validator("type", mode="before")
    @classmethod
    def validate_plugin_type(cls, value: object) -> str:
        return require_supported_plugin_type(value)

    @field_validator('id')
    @classmethod
    def validate_id(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("plugin.id 不能为空")
        # Check for path-traversal characters
        if '..' in v or '/' in v or '\\' in v:
            raise ValueError("plugin.id 不能包含路径遍历字符")
        return v.strip()

    @field_validator('entry')
    @classmethod
    def validate_entry(cls, v: str) -> str:
        if ':' not in v:
            raise ValueError("plugin.entry 格式错误，应为 'module.path:ClassName'")
        module_path, class_name = v.rsplit(':', 1)
        if not module_path or not class_name:
            raise ValueError("plugin.entry 的模块路径和类名都不能为空")
        return v


class PluginRuntimeSchema(BaseModel):
    """[plugin_runtime] section schema - runtime controls"""
    enabled: bool = True
    auto_start: bool = False
    priority: Optional[int] = None
    timeout: Optional[float] = None
    startup_failure: Literal["warn", "fail", "ignore"] = "warn"

    @field_validator("timeout", mode="before")
    @classmethod
    def validate_timeout(cls, value: object) -> object:
        if value is None:
            return value
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError("plugin_runtime.timeout must be a number in range 0 < timeout <= 300")
        timeout = float(value)
        if not math.isfinite(timeout) or timeout <= 0 or timeout > _PLUGIN_RUNTIME_TIMEOUT_MAX:
            raise ValueError("plugin_runtime.timeout must be a number in range 0 < timeout <= 300")
        return timeout

    @field_validator("startup_failure", mode="before")
    @classmethod
    def validate_startup_failure(cls, value: object) -> object:
        if value is None:
            return "warn"
        if value not in {"warn", "fail", "ignore"}:
            raise ValueError("plugin_runtime.startup_failure must be one of: warn, fail, ignore")
        return value


class PluginConfigSchema(BaseModel):
    """
    Complete plugin configuration schema
    
    Corresponds to the top-level plugin.toml structure.
    The [plugin] section is required; other sections are optional.
    """
    plugin: PluginSectionSchema
    plugin_runtime: Optional[PluginRuntimeSchema] = None
    
    # Allow additional custom configuration sections
    model_config = {"extra": "allow"}


class ProfileConfigSchema(BaseModel):
    """
    User profile configuration schema
    
    Profile configuration cannot contain [plugin], which the main configuration manages.
    """
    model_config = {"extra": "allow"}
    
    @model_validator(mode="before")
    @classmethod
    def forbid_plugin_section(cls, data: Any) -> Any:
        if isinstance(data, dict) and "plugin" in data:
            raise ValueError("用户 Profile 配置不允许包含 [plugin] 段，该段由主配置文件管理")
        return data


# ============ Validation functions ============

class ConfigValidationError(Exception):
    """Configuration validation error"""
    def __init__(self, message: str, field: Optional[str] = None, details: Optional[List[Dict[str, Any]]] = None):
        self.message = message
        self.field = field
        self.details = details or []
        super().__init__(message)
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "error": "ConfigValidationError",
            "message": self.message,
            "field": self.field,
            "details": self.details,
        }


def validate_plugin_config(config: Dict[str, Any], *, strict: bool = True) -> PluginConfigSchema:
    """
    Validate plugin configuration
    
    Args:
        config: Configuration dictionary loaded from TOML
        strict: Enable strict mode (default True)
    
    Returns:
        Validated PluginConfigSchema instance
    
    Raises:
        ConfigValidationError: Validation failure, with detailed errors
    """
    try:
        return PluginConfigSchema.model_validate(config)
    except Exception as e:
        # Parse Pydantic validation errors into friendly messages
        errors = _parse_validation_errors(e)
        if errors:
            first_error = errors[0]
            raise ConfigValidationError(
                message=first_error.get("msg", str(e)),
                field=first_error.get("loc"),
                details=errors,
            ) from e
        raise ConfigValidationError(message=str(e)) from e


def validate_profile_config(config: Dict[str, Any]) -> ProfileConfigSchema:
    """
    Validate user profile configuration
    
    Args:
        config: Configuration dictionary loaded from profile TOML
    
    Returns:
        Validated ProfileConfigSchema instance
    
    Raises:
        ConfigValidationError: Validation failure
    """
    try:
        return ProfileConfigSchema.model_validate(config)
    except Exception as e:
        errors = _parse_validation_errors(e)
        if errors:
            first_error = errors[0]
            raise ConfigValidationError(
                message=first_error.get("msg", str(e)),
                field=first_error.get("loc"),
                details=errors,
            ) from e
        raise ConfigValidationError(message=str(e)) from e


def validate_plugin_config_partial(
    config: Dict[str, Any],
    *,
    require_plugin_section: bool = True,
) -> Dict[str, Any]:
    """
    Partially validate plugin configuration for updates
    
    Validate only supplied fields without requiring every required field.
    
    Args:
        config: Configuration dictionary
        require_plugin_section: Require the [plugin] section
    
    Returns:
        Original configuration dictionary after validation
    
    Raises:
        ConfigValidationError: Validation failure
    """
    if require_plugin_section and "plugin" not in config:
        raise ConfigValidationError(
            message="配置必须包含 [plugin] 段",
            field="plugin",
        )
    
    plugin_section = config.get("plugin")
    if plugin_section is not None:
        if not isinstance(plugin_section, dict):
            raise ConfigValidationError(
                message="[plugin] 段必须是一个表（table）",
                field="plugin",
            )

        plugin_type = plugin_section.get("type")
        if plugin_type is not None and (
            not isinstance(plugin_type, str) or plugin_type not in SUPPORTED_PLUGIN_TYPES
        ):
            raise ConfigValidationError(
                message=format_plugin_type_choice_error("plugin.type"),
                field="plugin.type",
            )

        if "host" in plugin_section:
            raise ConfigValidationError(
                message=format_removed_plugin_host(),
                field="plugin.host",
            )

        if "models" in plugin_section:
            try:
                TypeAdapter(Dict[PluginModelUsageId, PluginModelRequirementSchema]).validate_python(
                    plugin_section["models"]
                )
            except ValidationError as exc:
                errors = _parse_validation_errors(exc)
                for error in errors:
                    suffix = error.get("loc")
                    error["loc"] = f"plugin.models.{suffix}" if suffix else "plugin.models"
                raise ConfigValidationError(
                    message=errors[0]["msg"],
                    field=errors[0]["loc"],
                    details=errors,
                ) from exc
        
        # Validate plugin.id format if present
        plugin_id = plugin_section.get("id")
        if plugin_id is not None:
            if not isinstance(plugin_id, str) or not plugin_id.strip():
                raise ConfigValidationError(
                    message="plugin.id 必须是非空字符串",
                    field="plugin.id",
                )
            import re
            if not re.match(r'^[a-zA-Z0-9_-]+$', plugin_id):
                raise ConfigValidationError(
                    message="plugin.id 只能包含字母、数字、下划线和连字符",
                    field="plugin.id",
                )
        
        # Validate plugin.entry format if present
        entry = plugin_section.get("entry")
        if entry is not None:
            if not isinstance(entry, str) or ':' not in entry:
                raise ConfigValidationError(
                    message="plugin.entry 格式错误，应为 'module.path:ClassName'",
                    field="plugin.entry",
                )
    
    # Validate plugin_runtime section if present
    runtime_section = config.get("plugin_runtime")
    if runtime_section is not None:
        if not isinstance(runtime_section, dict):
            raise ConfigValidationError(
                message="[plugin_runtime] 段必须是一个表（table）",
                field="plugin_runtime",
            )
        
        enabled = runtime_section.get("enabled")
        if enabled is not None and not isinstance(enabled, bool):
            # Allow string-form booleans
            if isinstance(enabled, str):
                if enabled.lower() not in ("true", "false", "1", "0", "yes", "no", "on", "off"):
                    raise ConfigValidationError(
                        message="plugin_runtime.enabled 必须是布尔值",
                        field="plugin_runtime.enabled",
                    )
            else:
                raise ConfigValidationError(
                    message="plugin_runtime.enabled 必须是布尔值",
                    field="plugin_runtime.enabled",
                )
    
    return config


def _parse_validation_errors(exc: Exception) -> List[Dict[str, Any]]:
    """Parse Pydantic validation errors into a friendly error list"""
    from pydantic import ValidationError
    
    errors = []
    
    # Try retrieving detailed Pydantic ValidationError information
    if isinstance(exc, ValidationError):
        try:
            for err in exc.errors():
                loc = err.get('loc', ())
                loc_str = '.'.join(str(x) for x in loc) if loc else None
                errors.append({
                    "loc": loc_str,
                    "msg": _translate_error_message(err.get('msg', ''), err.get('type', '')),
                    "type": err.get('type', 'unknown'),
                    "input": err.get('input'),
                })
        except Exception:
            pass
    
    if not errors:
        errors.append({
            "loc": None,
            "msg": str(exc),
            "type": "unknown",
        })
    
    return errors


def _translate_error_message(msg: str, error_type: str) -> str:
    """Translate Pydantic error messages into Chinese"""
    translations = {
        "Field required": "字段必填",
        "field required": "字段必填",
        "none is not an allowed value": "不允许为空值",
        "value is not a valid integer": "值必须是整数",
        "value is not a valid float": "值必须是数字",
        "value is not a valid boolean": "值必须是布尔值",
        "value is not a valid list": "值必须是列表",
        "value is not a valid dict": "值必须是字典/表",
        "string does not match regex": "字符串格式不匹配",
        "ensure this value has at least": "值长度不足",
        "ensure this value has at most": "值长度超限",
        "String should match pattern": "字符串格式不匹配",
        "String should have at least": "字符串长度不足",
        "String should have at most": "字符串长度超限",
    }
    
    for en, zh in translations.items():
        if en.lower() in msg.lower():
            return msg.replace(en, zh)
    
    return msg


# ============ Convenience functions ============

def is_valid_plugin_id(plugin_id: str) -> bool:
    """Check plugin ID validity"""
    import re
    if not plugin_id or not isinstance(plugin_id, str):
        return False
    return bool(re.match(r'^[a-zA-Z0-9_-]+$', plugin_id.strip()))


def is_valid_entry_point(entry: str) -> bool:
    """Check entry-point format validity"""
    if not entry or not isinstance(entry, str):
        return False
    if ':' not in entry:
        return False
    module_path, class_name = entry.rsplit(':', 1)
    return bool(module_path and class_name)


__all__ = [
    # Schema models
    "PluginConfigSchema",
    "PluginSectionSchema",
    "PluginRuntimeSchema",
    "PluginAuthorSchema",
    "PluginSdkSchema",
    "PluginStoreSchema",
    "PluginInstallKindSchema",
    "PluginInstallSchema",
    "PluginModelRequirementSchema",
    "PluginModelUsageId",
    "ModelCapability",
    "PluginConfigProfilesSchema",
    "PluginDependencySchema",
    "PluginType",
    "ProfileConfigSchema",
    # Validation functions
    "validate_plugin_config",
    "validate_profile_config",
    "validate_plugin_config_partial",
    # Exceptions
    "ConfigValidationError",
    # Convenience functions
    "is_valid_plugin_id",
    "is_valid_entry_point",
]
