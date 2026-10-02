from __future__ import annotations

import asyncio
from collections.abc import Mapping
from pathlib import Path

from fastapi import HTTPException

from plugin.core.state import state
from plugin.logging_config import get_logger
from plugin.server.application.plugins.development import registration_for_plugin_sync
from plugin.server.domain import RUNTIME_ERRORS
from plugin.server.domain.errors import ServerDomainError
from plugin.server.infrastructure.config_merge import deep_merge
from plugin.server.infrastructure.config_paths import get_plugin_config_path
from plugin.server.infrastructure.config_resolver import resolve_plugin_config
from plugin.server.infrastructure.config_profiles import (
    get_profile_config as infrastructure_get_profile_config,
)
from plugin.server.infrastructure.config_profiles import (
    get_profiles_state as infrastructure_get_profiles_state,
)
from plugin.server.infrastructure.config_queries import (
    load_plugin_base_config as infrastructure_load_plugin_base_config,
)
from plugin.utils.time_utils import now_iso
from plugin.server.infrastructure.config_queries import (
    load_plugin_config as infrastructure_load_plugin_config,
)
from plugin.server.infrastructure.config_queries import (
    load_plugin_effective_base_config as infrastructure_load_plugin_effective_base_config,
)
from plugin.server.infrastructure.config_queries import (
    load_plugin_config_toml as infrastructure_load_plugin_config_toml,
)
from plugin.server.infrastructure.config_queries import (
    parse_toml_to_config as infrastructure_parse_toml_to_config,
)
from plugin.server.infrastructure.config_queries import (
    render_config_to_toml as infrastructure_render_config_to_toml,
)

logger = get_logger("server.application.config.query")

def _to_message(detail: object, *, fallback: str) -> str:
    if isinstance(detail, str) and detail:
        return detail
    return fallback


def _from_http_exception(
    error: HTTPException,
    *,
    code: str,
    fallback_message: str,
) -> ServerDomainError:
    status_code = error.status_code if isinstance(error.status_code, int) else 500
    return ServerDomainError(
        code=code,
        message=_to_message(error.detail, fallback=fallback_message),
        status_code=status_code,
        details={},
    )


def _normalize_payload(payload: object, *, context: str) -> dict[str, object]:
    if not isinstance(payload, Mapping):
        raise ServerDomainError(
            code="INVALID_DATA_SHAPE",
            message=f"{context} returned invalid payload",
            status_code=500,
            details={"payload_type": type(payload).__name__},
        )
    normalized: dict[str, object] = {}
    for key_obj, value in payload.items():
        if not isinstance(key_obj, str):
            raise ServerDomainError(
                code="INVALID_DATA_SHAPE",
                message=f"{context} returned non-string key",
                status_code=500,
                details={"key_type": type(key_obj).__name__},
            )
        normalized[key_obj] = value
    return normalized


def _normalize_config_mapping(
    value: object,
    *,
    field: str,
    allow_none: bool = False,
) -> dict[str, object]:
    if value is None and allow_none:
        return {}
    if not isinstance(value, Mapping):
        raise ServerDomainError(
            code="INVALID_DATA_SHAPE",
            message=f"{field} is not an object",
            status_code=500,
            details={"field": field, "value_type": type(value).__name__},
        )
    normalized: dict[str, object] = {}
    for key_obj, item in value.items():
        if not isinstance(key_obj, str):
            raise ServerDomainError(
                code="INVALID_DATA_SHAPE",
                message=f"{field} contains non-string key",
                status_code=500,
                details={"key_type": type(key_obj).__name__},
            )
        normalized[key_obj] = item
    return normalized


def _normalize_profile_name(profile_name: object) -> str:
    if not isinstance(profile_name, str) or not profile_name.strip():
        raise ServerDomainError(
            code="INVALID_ARGUMENT",
            message="Invalid profile_name",
            status_code=400,
            details={},
        )
    return profile_name.strip()


def _application_state_sync(
    *,
    plugin_id: str,
    persisted_fingerprint: str | None,
) -> dict[str, object]:
    """Compare a persisted identity with the current, owned host."""
    observed_at = now_iso()
    current_config_path = _current_owner_config_path_sync(plugin_id)

    with state.acquire_plugin_hosts_read_lock():
        host = state.plugin_hosts.get(plugin_id)
        host_path_obj = getattr(host, "config_path", None) if host is not None else None
        try:
            host_path = Path(host_path_obj).resolve() if host_path_obj is not None else None
        except (OSError, RuntimeError, ValueError, TypeError):
            host_path = None
        owner_matches = current_config_path is not None and host_path == current_config_path
        alive: bool | None = False
        if host is not None:
            try:
                alive = bool(host.is_alive())
            except Exception:
                alive = None
        applied_fingerprint = (
            getattr(host, "applied_config_fingerprint", None)
            if owner_matches and alive
            else None
        )

    if alive is False:
        config_state = "not_running"
        lifecycle_status = "not_running"
    elif alive is None or not owner_matches:
        # Ownership failure fences the fingerprint, not the live process. It
        # must not clear a reload hint as though the process had stopped.
        config_state = "unknown"
        lifecycle_status = "running" if alive else "unknown"
    elif not isinstance(applied_fingerprint, str) or not applied_fingerprint:
        config_state = "unknown"
        lifecycle_status = "running"
    elif not isinstance(persisted_fingerprint, str) or not persisted_fingerprint:
        config_state = "unknown"
        lifecycle_status = "running"
    else:
        config_state = "matched" if persisted_fingerprint == applied_fingerprint else "pending"
        lifecycle_status = "running"

    return {
        "plugin_id": plugin_id,
        "lifecycle_status": lifecycle_status,
        "config_state": config_state,
        "persisted_fingerprint": persisted_fingerprint,
        "applied_fingerprint": applied_fingerprint,
        "observed_at": observed_at,
    }


def _current_owner_config_path_sync(plugin_id: str) -> Path | None:
    """Resolve the current registry owner without consulting the host itself."""
    try:
        registration = registration_for_plugin_sync(plugin_id)
    except Exception:
        # A stale development registration must fence the old host out of
        # application-state comparisons. Ordinary plugins return ``None`` from
        # the registration helper and continue through registry metadata.
        return None
    if registration is not None:
        return (registration.source_dir / "plugin.toml").resolve()

    with state.acquire_plugins_read_lock():
        metadata = state.plugins.get(plugin_id)
    candidate = metadata.get("config_path") if isinstance(metadata, Mapping) else None
    if isinstance(candidate, str) and candidate:
        try:
            return Path(candidate).resolve()
        except (OSError, RuntimeError, ValueError):
            return None
    # If there is no registry owner, treating a host as current would allow a
    # stale host left by uninstall/rebind to report a false match.
    return None


class ConfigQueryService:
    async def get_plugin_config(self, *, plugin_id: str) -> dict[str, object]:
        try:
            payload = await asyncio.to_thread(infrastructure_load_plugin_config, plugin_id)
            return _normalize_payload(payload, context="load_plugin_config")
        except HTTPException as exc:
            raise _from_http_exception(
                exc,
                code="PLUGIN_CONFIG_QUERY_FAILED",
                fallback_message="Failed to load plugin config",
            ) from exc
        except RUNTIME_ERRORS as exc:
            logger.error(
                "get_plugin_config failed: plugin_id={}, err_type={}, err={}",
                plugin_id,
                type(exc).__name__,
                str(exc),
            )
            raise ServerDomainError(
                code="PLUGIN_CONFIG_QUERY_FAILED",
                message="Failed to load plugin config",
                status_code=500,
                details={"plugin_id": plugin_id, "error_type": type(exc).__name__},
            ) from exc

    async def get_plugin_config_application_state(self, *, plugin_id: str) -> dict[str, object]:
        try:
            # State queries only need the effective snapshot identity, not the
            # editor schema, schema validation, or its warning logs.
            persisted = await asyncio.to_thread(
                resolve_plugin_config,
                plugin_id,
                include_effective_config=True,
                validate_schema=False,
            )
            normalized = _normalize_payload(persisted, context="resolve_plugin_config")
            fingerprint_obj = normalized.get("config_fingerprint")
            persisted_fingerprint = fingerprint_obj if isinstance(fingerprint_obj, str) else None
            return await asyncio.to_thread(
                _application_state_sync,
                plugin_id=plugin_id,
                persisted_fingerprint=persisted_fingerprint,
            )
        except HTTPException as exc:
            raise _from_http_exception(
                exc,
                code="PLUGIN_CONFIG_APPLICATION_STATE_QUERY_FAILED",
                fallback_message="Failed to load plugin config application state",
            ) from exc
        except RUNTIME_ERRORS as exc:
            logger.error(
                "get_plugin_config_application_state failed: plugin_id={}, err_type={}, err={}",
                plugin_id,
                type(exc).__name__,
                str(exc),
            )
            raise ServerDomainError(
                code="PLUGIN_CONFIG_APPLICATION_STATE_QUERY_FAILED",
                message="Failed to load plugin config application state",
                status_code=500,
                details={"plugin_id": plugin_id, "error_type": type(exc).__name__},
            ) from exc

    async def get_plugin_config_toml(self, *, plugin_id: str) -> dict[str, object]:
        try:
            payload = await asyncio.to_thread(infrastructure_load_plugin_config_toml, plugin_id)
            return _normalize_payload(payload, context="load_plugin_config_toml")
        except HTTPException as exc:
            raise _from_http_exception(
                exc,
                code="PLUGIN_CONFIG_TOML_QUERY_FAILED",
                fallback_message="Failed to load plugin config toml",
            ) from exc
        except RUNTIME_ERRORS as exc:
            logger.error(
                "get_plugin_config_toml failed: plugin_id={}, err_type={}, err={}",
                plugin_id,
                type(exc).__name__,
                str(exc),
            )
            raise ServerDomainError(
                code="PLUGIN_CONFIG_TOML_QUERY_FAILED",
                message="Failed to load plugin config toml",
                status_code=500,
                details={"plugin_id": plugin_id, "error_type": type(exc).__name__},
            ) from exc

    async def get_plugin_base_config(self, *, plugin_id: str) -> dict[str, object]:
        try:
            payload = await asyncio.to_thread(infrastructure_load_plugin_base_config, plugin_id)
            return _normalize_payload(payload, context="load_plugin_base_config")
        except HTTPException as exc:
            raise _from_http_exception(
                exc,
                code="PLUGIN_BASE_CONFIG_QUERY_FAILED",
                fallback_message="Failed to load plugin base config",
            ) from exc
        except RUNTIME_ERRORS as exc:
            logger.error(
                "get_plugin_base_config failed: plugin_id={}, err_type={}, err={}",
                plugin_id,
                type(exc).__name__,
                str(exc),
            )
            raise ServerDomainError(
                code="PLUGIN_BASE_CONFIG_QUERY_FAILED",
                message="Failed to load plugin base config",
                status_code=500,
                details={"plugin_id": plugin_id, "error_type": type(exc).__name__},
            ) from exc

    async def get_plugin_effective_base_config(self, *, plugin_id: str) -> dict[str, object]:
        try:
            payload = await asyncio.to_thread(infrastructure_load_plugin_effective_base_config, plugin_id)
            return _normalize_payload(payload, context="load_plugin_effective_base_config")
        except HTTPException as exc:
            raise _from_http_exception(
                exc,
                code="PLUGIN_EFFECTIVE_BASE_CONFIG_QUERY_FAILED",
                fallback_message="Failed to load plugin effective base config",
            ) from exc
        except RUNTIME_ERRORS as exc:
            logger.error(
                "get_plugin_effective_base_config failed: plugin_id={}, err_type={}, err={}",
                plugin_id,
                type(exc).__name__,
                str(exc),
            )
            raise ServerDomainError(
                code="PLUGIN_EFFECTIVE_BASE_CONFIG_QUERY_FAILED",
                message="Failed to load plugin effective base config",
                status_code=500,
                details={"plugin_id": plugin_id, "error_type": type(exc).__name__},
            ) from exc

    async def parse_toml_to_config(self, *, plugin_id: str, toml: str) -> dict[str, object]:
        try:
            payload = await asyncio.to_thread(infrastructure_parse_toml_to_config, plugin_id, toml)
            return _normalize_payload(payload, context="parse_toml_to_config")
        except HTTPException as exc:
            raise _from_http_exception(
                exc,
                code="PLUGIN_CONFIG_PARSE_FAILED",
                fallback_message="Failed to parse toml",
            ) from exc
        except RUNTIME_ERRORS as exc:
            logger.error(
                "parse_toml_to_config failed: plugin_id={}, err_type={}, err={}",
                plugin_id,
                type(exc).__name__,
                str(exc),
            )
            raise ServerDomainError(
                code="PLUGIN_CONFIG_PARSE_FAILED",
                message="Failed to parse toml",
                status_code=500,
                details={"plugin_id": plugin_id, "error_type": type(exc).__name__},
            ) from exc

    async def render_config_to_toml(
        self,
        *,
        plugin_id: str,
        config: dict[str, object],
    ) -> dict[str, object]:
        try:
            payload = await asyncio.to_thread(infrastructure_render_config_to_toml, plugin_id, config)
            return _normalize_payload(payload, context="render_config_to_toml")
        except HTTPException as exc:
            raise _from_http_exception(
                exc,
                code="PLUGIN_CONFIG_RENDER_FAILED",
                fallback_message="Failed to render toml",
            ) from exc
        except RUNTIME_ERRORS as exc:
            logger.error(
                "render_config_to_toml failed: plugin_id={}, err_type={}, err={}",
                plugin_id,
                type(exc).__name__,
                str(exc),
            )
            raise ServerDomainError(
                code="PLUGIN_CONFIG_RENDER_FAILED",
                message="Failed to render toml",
                status_code=500,
                details={"plugin_id": plugin_id, "error_type": type(exc).__name__},
            ) from exc

    async def get_plugin_profiles_state(self, *, plugin_id: str) -> dict[str, object]:
        try:
            config_path = await asyncio.to_thread(get_plugin_config_path, plugin_id)
            payload = await asyncio.to_thread(
                infrastructure_get_profiles_state,
                plugin_id=plugin_id,
                config_path=config_path,
            )
            return _normalize_payload(payload, context="get_plugin_profiles_state")
        except HTTPException as exc:
            raise _from_http_exception(
                exc,
                code="PLUGIN_PROFILE_STATE_QUERY_FAILED",
                fallback_message="Failed to query plugin profile state",
            ) from exc
        except RUNTIME_ERRORS as exc:
            logger.error(
                "get_plugin_profiles_state failed: plugin_id={}, err_type={}, err={}",
                plugin_id,
                type(exc).__name__,
                str(exc),
            )
            raise ServerDomainError(
                code="PLUGIN_PROFILE_STATE_QUERY_FAILED",
                message="Failed to query plugin profile state",
                status_code=500,
                details={"plugin_id": plugin_id, "error_type": type(exc).__name__},
            ) from exc

    async def get_plugin_profile_config(
        self,
        *,
        plugin_id: str,
        profile_name: object,
    ) -> dict[str, object]:
        normalized_profile_name = _normalize_profile_name(profile_name)
        try:
            config_path = await asyncio.to_thread(get_plugin_config_path, plugin_id)
            payload = await asyncio.to_thread(
                infrastructure_get_profile_config,
                plugin_id=plugin_id,
                profile_name=normalized_profile_name,
                config_path=config_path,
            )
            return _normalize_payload(payload, context="get_plugin_profile_config")
        except HTTPException as exc:
            raise _from_http_exception(
                exc,
                code="PLUGIN_PROFILE_QUERY_FAILED",
                fallback_message="Failed to query plugin profile config",
            ) from exc
        except RUNTIME_ERRORS as exc:
            logger.error(
                "get_plugin_profile_config failed: plugin_id={}, profile_name={}, err_type={}, err={}",
                plugin_id,
                normalized_profile_name,
                type(exc).__name__,
                str(exc),
            )
            raise ServerDomainError(
                code="PLUGIN_PROFILE_QUERY_FAILED",
                message="Failed to query plugin profile config",
                status_code=500,
                details={
                    "plugin_id": plugin_id,
                    "profile_name": normalized_profile_name,
                    "error_type": type(exc).__name__,
                },
            ) from exc

    async def get_plugin_effective_config(
        self,
        *,
        plugin_id: str,
        profile_name: object,
    ) -> dict[str, object]:
        if profile_name is None:
            return await self.get_plugin_config(plugin_id=plugin_id)

        normalized_profile_name = _normalize_profile_name(profile_name)
        base_payload = await self.get_plugin_effective_base_config(plugin_id=plugin_id)
        overlay_payload = await self.get_plugin_profile_config(
            plugin_id=plugin_id,
            profile_name=normalized_profile_name,
        )

        base_config = _normalize_config_mapping(base_payload.get("config"), field="base config")
        overlay_config = _normalize_config_mapping(
            overlay_payload.get("config"),
            field="profile config",
            allow_none=True,
        )

        if "plugin" in overlay_config:
            raise ServerDomainError(
                code="INVALID_ARGUMENT",
                message="Profile config must not define top-level 'plugin' section.",
                status_code=400,
                details={},
            )

        merged_config: dict[str, object] = dict(base_config)
        for key, value in overlay_config.items():
            if key == "plugin":
                continue
            current_value = merged_config.get(key)
            if isinstance(current_value, dict) and isinstance(value, dict):
                merged_config[key] = deep_merge(current_value, value)
            else:
                merged_config[key] = value

        effective_payload: dict[str, object] = dict(base_payload)
        effective_payload["config"] = merged_config
        effective_payload["effective_profile"] = normalized_profile_name
        return effective_payload
