"""SDK v2 context facade.

This wrapper hides the legacy host context shape and exposes the async-first
SDK v2 contract that plugins should code against.
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any, Protocol, cast

from .bus_context import SdkBusContext, ensure_sdk_bus_context
from .finish import build_finish_envelope, normalize_structured_data
from .result_contract import contract_from_meta, validate_reply_payload
from .types import LoggerLike, Metadata, PluginContextProtocol

_UNSET = object()
_SDK_CONTEXT_ATTR_NAMES = ("plugin_id", "metadata", "logger", "config_path", "bus")
_SDK_CONTEXT_METHOD_NAMES = (
    "get_own_config",
    "get_own_base_config",
    "get_own_profiles_state",
    "get_own_profile_config",
    "get_own_effective_config",
    "update_own_config",
    "upsert_own_profile_config",
    "delete_own_profile_config",
    "set_own_active_profile",
    "query_plugins",
    "trigger_plugin_event",
    "get_system_config",
    "query_memory",
    "run_update",
    "export_push",
    "finish",
    "push_message",
    "update_status",
)


class _HostContextProtocol(Protocol):
    plugin_id: object
    metadata: object
    logger: object
    config_path: object
    bus: object
    _effective_config: object

    async def get_own_config(self, timeout: float = 5.0) -> object: ...

    async def get_own_base_config(self, timeout: float = 5.0) -> object: ...

    async def get_own_profiles_state(self, timeout: float = 5.0) -> object: ...

    async def get_own_profile_config(self, profile_name: str, timeout: float = 5.0) -> object: ...

    async def get_own_effective_config(self, profile_name: str | None = None, timeout: float = 5.0) -> object: ...

    async def update_own_config(self, updates: dict[str, Any], timeout: float = 10.0) -> object: ...

    async def upsert_own_profile_config(
        self,
        profile_name: str,
        config: dict[str, Any],
        *,
        make_active: bool = False,
        timeout: float = 10.0,
    ) -> object: ...

    async def delete_own_profile_config(self, profile_name: str, timeout: float = 10.0) -> object: ...

    async def set_own_active_profile(self, profile_name: str, timeout: float = 10.0) -> object: ...

    async def query_plugins(self, filters: dict[str, object], timeout: float = 5.0) -> object: ...

    async def trigger_plugin_event(self, **kwargs: object) -> object: ...

    async def get_system_config(self, timeout: float = 5.0) -> object: ...

    async def query_memory(self, bucket_id: str, query: str, timeout: float = 5.0) -> object: ...

    async def run_update_async(self, **kwargs: object) -> object: ...

    async def export_push_async(self, **kwargs: object) -> object: ...

    def push_message(self, **kwargs: object) -> object: ...

    def update_status(self, status: dict[str, object]) -> None: ...


class SdkContext:
    """Typed async-first context exposed to SDK v2 plugins."""

    def __init__(self, host_ctx: object):
        self._host_ctx = cast(_HostContextProtocol, host_ctx)
        self._bus_ctx: SdkBusContext | None | object = _UNSET

    @staticmethod
    def _normalize_export_metadata(
        metadata: dict[str, object] | None,
        *,
        reply: bool | None,
    ) -> dict[str, object] | None:
        normalized: dict[str, object] = {}
        if isinstance(metadata, dict):
            for key_obj, value in metadata.items():
                if isinstance(key_obj, str):
                    normalized[key_obj] = value

        if reply is None:
            return normalized if metadata is not None else None

        raw_agent_meta = normalized.get("agent")
        agent_meta: dict[str, object] = {}
        if isinstance(raw_agent_meta, dict):
            for key_obj, value in raw_agent_meta.items():
                if isinstance(key_obj, str):
                    agent_meta[key_obj] = value
        agent_meta["reply"] = bool(reply)
        if reply and "include" not in agent_meta:
            agent_meta["include"] = True
        normalized["agent"] = agent_meta
        return normalized

    @staticmethod
    def _metadata_reply_flag(metadata: Mapping[str, object] | None) -> bool | None:
        if not isinstance(metadata, Mapping):
            return None
        raw_agent_meta = metadata.get("agent")
        if not isinstance(raw_agent_meta, Mapping):
            return None
        reply_obj = raw_agent_meta.get("reply")
        return reply_obj if isinstance(reply_obj, bool) else None

    def _current_entry_meta(self) -> object | None:
        getter = getattr(self._host_ctx, "get_current_entry_meta", None)
        if getter is None:
            return None
        if callable(getter):
            return getter()
        return None

    def _validate_reply_payload(self, payload: object, *, export_type: str | None = None) -> None:
        entry_meta = self._current_entry_meta()
        if entry_meta is None:
            return
        validate_reply_payload(
            contract_from_meta(entry_meta),
            payload,
            export_type=export_type,
            plugin_id=self.plugin_id,
            entry_id=getattr(entry_meta, "id", None),
        )

    @property
    def plugin_id(self) -> str:
        return str(getattr(self._host_ctx, "plugin_id", "plugin"))

    @property
    def metadata(self) -> Metadata:
        value = getattr(self._host_ctx, "metadata", None)
        return value if isinstance(value, Mapping) else {}

    @property
    def logger(self) -> LoggerLike | None:
        logger = getattr(self._host_ctx, "logger", None)
        return cast(LoggerLike | None, logger)

    @property
    def config_path(self) -> str | Path | None:
        value = getattr(self._host_ctx, "config_path", None)
        if value is None or isinstance(value, (str, Path)):
            return value
        return str(value)

    @property
    def _effective_config(self) -> dict[str, object]:
        value = getattr(self._host_ctx, "_effective_config", None)
        return value if isinstance(value, dict) else {}

    @property
    def bus(self) -> SdkBusContext | None:
        cached = self._bus_ctx
        if cached is not _UNSET:
            return cast(SdkBusContext | None, cached)
        self._bus_ctx = ensure_sdk_bus_context(getattr(self._host_ctx, "bus", None), host_ctx=self._host_ctx)
        return cast(SdkBusContext | None, self._bus_ctx)

    async def get_own_config(self, timeout: float = 5.0) -> object:
        return await self._host_ctx.get_own_config(timeout=timeout)

    async def get_own_base_config(self, timeout: float = 5.0) -> object:
        return await self._host_ctx.get_own_base_config(timeout=timeout)

    async def get_own_profiles_state(self, timeout: float = 5.0) -> object:
        return await self._host_ctx.get_own_profiles_state(timeout=timeout)

    async def get_own_profile_config(self, profile_name: str, timeout: float = 5.0) -> object:
        return await self._host_ctx.get_own_profile_config(profile_name, timeout=timeout)

    async def get_own_effective_config(self, profile_name: str | None = None, timeout: float = 5.0) -> object:
        return await self._host_ctx.get_own_effective_config(profile_name, timeout=timeout)

    async def update_own_config(self, updates: dict[str, Any], timeout: float = 10.0) -> object:
        return await self._host_ctx.update_own_config(updates, timeout=timeout)

    async def upsert_own_profile_config(
        self,
        profile_name: str,
        config: dict[str, Any],
        *,
        make_active: bool = False,
        timeout: float = 10.0,
    ) -> object:
        return await self._host_ctx.upsert_own_profile_config(
            profile_name,
            config,
            make_active=make_active,
            timeout=timeout,
        )

    async def delete_own_profile_config(self, profile_name: str, timeout: float = 10.0) -> object:
        return await self._host_ctx.delete_own_profile_config(profile_name, timeout=timeout)

    async def set_own_active_profile(self, profile_name: str, timeout: float = 10.0) -> object:
        return await self._host_ctx.set_own_active_profile(profile_name, timeout=timeout)

    async def query_plugins(self, filters: dict[str, object], timeout: float = 5.0) -> object:
        return await self._host_ctx.query_plugins(filters, timeout=timeout)

    async def trigger_plugin_event(self, **kwargs: object) -> object:
        return await self._host_ctx.trigger_plugin_event(**kwargs)

    async def get_system_config(self, timeout: float = 5.0) -> object:
        return await self._host_ctx.get_system_config(timeout=timeout)

    async def query_memory(self, bucket_id: str, query: str, timeout: float = 5.0) -> object:
        return await self._host_ctx.query_memory(bucket_id, query, timeout=timeout)

    async def run_update(
        self,
        *,
        run_id: str | None = None,
        progress: float | None = None,
        stage: str | None = None,
        message: str | None = None,
        step: int | None = None,
        step_total: int | None = None,
        eta_seconds: float | None = None,
        metrics: dict[str, object] | None = None,
        timeout: float = 5.0,
    ) -> object:
        return await self._host_ctx.run_update_async(
            run_id=run_id,
            progress=progress,
            stage=stage,
            message=message,
            step=step,
            step_total=step_total,
            eta_seconds=eta_seconds,
            metrics=metrics,
            timeout=timeout,
        )

    async def export_push(
        self,
        *,
        export_type: str,
        run_id: str | None = None,
        text: str | None = None,
        json_data: dict[str, object] | None = None,
        url: str | None = None,
        binary_data: bytes | None = None,
        binary_url: str | None = None,
        mime: str | None = None,
        description: str | None = None,
        label: str | None = None,
        metadata: dict[str, object] | None = None,
        reply: bool | None = None,
        timeout: float = 5.0,
    ) -> object:
        normalized_metadata = self._normalize_export_metadata(metadata, reply=reply)
        reply_requested = reply if reply is not None else self._metadata_reply_flag(normalized_metadata)
        normalized_json_data = normalize_structured_data(json_data) if json_data is not None else None
        if reply_requested is True:
            payload: object = normalized_json_data if export_type == "json" else text if export_type == "text" else None
            self._validate_reply_payload(payload, export_type=export_type)
        return await self._host_ctx.export_push_async(
            export_type=export_type,
            run_id=run_id,
            text=text,
            json_data=normalized_json_data,
            url=url,
            binary_data=binary_data,
            binary_url=binary_url,
            mime=mime,
            description=description,
            label=label,
            metadata=normalized_metadata,
            timeout=timeout,
        )

    async def finish(
        self,
        *,
        data: object = None,
        reply: bool = True,
        message: str = "",
        trace_id: str | None = None,
        meta: dict[str, object] | None = None,
    ) -> Any:
        normalized_data = normalize_structured_data(data)
        if reply:
            self._validate_reply_payload(normalized_data, export_type="json")
        return build_finish_envelope(
            data=normalized_data,
            reply=reply,
            message=message,
            trace_id=trace_id,
            meta=meta,
        )

    def push_message(
        self,
        *,
        source: str,
        message_type: str,
        description: str = "",
        priority: int = 0,
        content: str | None = None,
        binary_data: bytes | None = None,
        binary_url: str | None = None,
        metadata: dict[str, object] | None = None,
        unsafe: bool = False,
        fast_mode: bool = False,
        target_lanlan: str | None = None,
    ) -> object:
        return self._host_ctx.push_message(
            source=source,
            message_type=message_type,
            description=description,
            priority=priority,
            content=content,
            binary_data=binary_data,
            binary_url=binary_url,
            metadata=metadata,
            unsafe=unsafe,
            fast_mode=fast_mode,
            target_lanlan=target_lanlan,
        )

    def update_status(self, status: dict[str, object]) -> None:
        self._host_ctx.update_status(status)


def _is_sdk_context_compatible(ctx: object) -> bool:
    return all(hasattr(ctx, attr_name) for attr_name in _SDK_CONTEXT_ATTR_NAMES) and all(
        callable(getattr(ctx, method_name, None)) for method_name in _SDK_CONTEXT_METHOD_NAMES
    )


def ensure_sdk_context(ctx: PluginContextProtocol | object) -> PluginContextProtocol:
    if isinstance(ctx, SdkContext):
        return ctx
    if _is_sdk_context_compatible(ctx):
        return cast(PluginContextProtocol, ctx)
    return cast(PluginContextProtocol, SdkContext(ctx))


__all__ = ["SdkContext", "ensure_sdk_context"]
