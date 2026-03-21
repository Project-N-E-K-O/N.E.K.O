"""Runtime helpers for invoking plugin entries.

These helpers are intentionally generic so the host can use them for both
legacy metadata shapes and SDK v2 metadata shapes.
"""

from __future__ import annotations

import dataclasses
import inspect
from collections.abc import Mapping
from typing import Any

from plugin._types.exceptions import PluginExecutionError


_SPECIAL_ARG_NAMES = frozenset({"self", "cls", "_ctx", "args", "kwargs"})


def resolve_entry_timeout(meta: object | None, default_timeout: float | None) -> float | None:
    """Resolve per-entry timeout from metadata.

    Precedence:
    1. `meta.timeout` when present
    2. legacy `meta.extra["timeout"]`
    3. v2 `meta.metadata["timeout"]`
    4. fallback `default_timeout`
    """

    if meta is None:
        return default_timeout

    direct_timeout = getattr(meta, "timeout", None)
    resolved = _normalize_timeout_value(direct_timeout)
    if resolved is not _UNSET:
        return resolved

    for attr_name in ("extra", "metadata"):
        value = getattr(meta, attr_name, None)
        if isinstance(value, Mapping):
            resolved = _normalize_timeout_value(value.get("timeout"))
            if resolved is not _UNSET:
                return resolved
    return default_timeout


def prepare_entry_kwargs(
    *,
    plugin_id: str,
    entry_id: str,
    handler: object,
    meta: object | None,
    args: dict[str, Any],
) -> dict[str, Any]:
    """Validate and normalize kwargs before calling a handler."""

    normalized_args = dict(args)
    if meta is None:
        return normalized_args

    params_model = getattr(meta, "params", None)
    if params_model is None or not bool(getattr(meta, "model_validate", True)):
        return normalized_args

    payload = {key: value for key, value in normalized_args.items() if key != "_ctx"}
    try:
        model_instance = _build_model_instance(params_model, payload)
        validated_payload = _dump_model_instance(model_instance)
    except Exception as error:  # pragma: no cover - exercised via callers
        raise PluginExecutionError(plugin_id, entry_id, f"parameter validation failed: {error}") from error

    signature = inspect.signature(handler)
    final_args = dict(normalized_args)
    inject_name = _resolve_model_injection_name(signature, params_model, validated_payload)

    if inject_name is not None:
        final_args[inject_name] = model_instance
    else:
        final_args.update(validated_payload)

    return _filter_supported_kwargs(signature, final_args)


_UNSET = object()


def _normalize_timeout_value(value: object) -> float | None | object:
    if value is None:
        return _UNSET
    try:
        timeout = float(value)
    except Exception:
        return _UNSET
    return None if timeout <= 0 else timeout


def _build_model_instance(params_model: type, payload: dict[str, Any]) -> object:
    model_validate = getattr(params_model, "model_validate", None)
    if callable(model_validate):
        return model_validate(payload)

    parse_obj = getattr(params_model, "parse_obj", None)
    if callable(parse_obj):
        return parse_obj(payload)

    return params_model(**payload)


def _dump_model_instance(instance: object) -> dict[str, Any]:
    model_dump = getattr(instance, "model_dump", None)
    if callable(model_dump):
        dumped = model_dump(mode="python")
        if isinstance(dumped, Mapping):
            return {str(key): value for key, value in dumped.items()}

    dict_fn = getattr(instance, "dict", None)
    if callable(dict_fn):
        dumped = dict_fn()
        if isinstance(dumped, Mapping):
            return {str(key): value for key, value in dumped.items()}

    if isinstance(instance, Mapping):
        return {str(key): value for key, value in instance.items()}

    if dataclasses.is_dataclass(instance):
        dumped = dataclasses.asdict(instance)
        return dumped if isinstance(dumped, dict) else {}

    raw = getattr(instance, "__dict__", None)
    if isinstance(raw, dict):
        return dict(raw)
    return {}


def _resolve_model_injection_name(
    signature: inspect.Signature,
    params_model: type,
    validated_payload: dict[str, Any],
) -> str | None:
    parameters = signature.parameters
    params_parameter = parameters.get("params")
    if params_parameter is not None and params_parameter.kind not in (
        inspect.Parameter.VAR_POSITIONAL,
        inspect.Parameter.VAR_KEYWORD,
    ):
        return "params"

    candidate_names: list[str] = []
    for name, parameter in parameters.items():
        if name in _SPECIAL_ARG_NAMES:
            continue
        if parameter.kind in (inspect.Parameter.VAR_POSITIONAL, inspect.Parameter.VAR_KEYWORD):
            continue
        if parameter.annotation is params_model:
            return name
        candidate_names.append(name)

    if len(candidate_names) == 1 and candidate_names[0] not in validated_payload:
        return candidate_names[0]
    return None


def _filter_supported_kwargs(signature: inspect.Signature, args: dict[str, Any]) -> dict[str, Any]:
    if any(parameter.kind is inspect.Parameter.VAR_KEYWORD for parameter in signature.parameters.values()):
        return args

    allowed = {
        name
        for name, parameter in signature.parameters.items()
        if parameter.kind in (
            inspect.Parameter.POSITIONAL_OR_KEYWORD,
            inspect.Parameter.KEYWORD_ONLY,
        )
    }
    return {key: value for key, value in args.items() if key in allowed}


__all__ = ["resolve_entry_timeout", "prepare_entry_kwargs"]
