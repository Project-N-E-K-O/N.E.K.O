"""Shared decorators for SDK v2.

This module contains the real decorator behavior and metadata binding.
Plugin-facing layers should re-export from here.
"""

from __future__ import annotations

import inspect
import sys
import types
from dataclasses import dataclass
from typing import Any, Callable, Literal, Mapping, TypeVar, Union, cast, get_args, get_origin, get_type_hints

from plugin.sdk.shared.constants import (
    EVENT_META_ATTR,
    HOOK_META_ATTR,
    NEKO_PLUGIN_TAG,
    PERSIST_ATTR,
)
from .events import EventMeta, QuickActionConfig
from plugin._types.entry_metadata import detached_copy

from .output_contract import is_pydantic_model
from .result_contract import (
    fields_from_schema,
    model_schema_from_type,
    normalize_llm_result_fields,
    schema_from_fields,
)
from .types import InputSchema, JsonValue

F = TypeVar("F", bound=Callable[..., object])
EntryKind = Literal["service", "action", "hook", "custom", "lifecycle", "consumer", "timer", "chat_command"]
_SKIP_PARAMS = frozenset({"self", "cls", "kwargs", "_ctx", "args"})
_PARAMS_MODEL_ATTR = "_neko_params_model"
_AUTO_INFER_PARAMS_ATTR = "_neko_auto_infer_params"
_AUTO_INFER_LLM_RESULT_ATTR = "_neko_auto_infer_llm_result"
_AUTO_INFER_OUTPUT_ATTR = "_neko_auto_infer_output"
_QUICK_ACTION_CONFIG_ATTR = "_neko_quick_action_config"
_TYPE_HINT_ERRORS = (NameError, TypeError, AttributeError, ValueError)
_PY_TYPE_TO_JSON: dict[type, str] = {
    str: "string",
    int: "integer",
    float: "number",
    bool: "boolean",
    dict: "object",
    list: "array",
}


@dataclass(slots=True)
class HookDecoratorMeta:
    target: str
    timing: str
    priority: int
    condition: str | None


def _unwrap_model_annotation(hint: object) -> type | None:
    metadata_items = getattr(hint, "__metadata__", None)
    annotated_args = getattr(hint, "__args__", None)
    if metadata_items is not None and annotated_args:
        hint = annotated_args[0]

    origin = get_origin(hint)
    args = get_args(hint)
    if origin in (Union, types.UnionType):
        non_none = [item for item in args if item is not type(None)]
        if len(non_none) == 1:
            return _unwrap_model_annotation(non_none[0])
        return None

    return hint if isinstance(hint, type) else None


def _schema_for_hint(hint: object) -> dict[str, object]:
    schema: dict[str, object] = {}
    metadata_items = getattr(hint, "__metadata__", None)
    annotated_args = getattr(hint, "__args__", None)
    if metadata_items is not None and annotated_args:
        hint = annotated_args[0]
        for item in metadata_items:
            if isinstance(item, str):
                schema["description"] = item
                break

    origin = get_origin(hint)
    args = get_args(hint)

    if origin in (Union, types.UnionType):
        non_none = [item for item in args if item is not type(None)]
        if len(non_none) == 1:
            inner = _schema_for_hint(non_none[0])
            inner_type = inner.get("type")
            if isinstance(inner_type, str):
                inner["type"] = [inner_type, "null"]
            elif isinstance(inner_type, list) and "null" not in inner_type:
                inner["type"] = [*inner_type, "null"]
            return {**inner, **schema}
        return schema

    if origin is list:
        schema["type"] = "array"
        if args:
            item_schema = _schema_for_hint(args[0])
            if item_schema:
                schema["items"] = item_schema
        return schema

    if origin is dict:
        schema["type"] = "object"
        return schema

    json_type = _PY_TYPE_TO_JSON.get(hint) if isinstance(hint, type) else None
    if json_type is not None:
        schema["type"] = json_type
    return schema


def _get_type_hints_safe(
    fn: Callable[..., object],
    *,
    localns: Mapping[str, object] | None = None,
) -> dict[str, object]:
    try:
        module_globals = vars(sys.modules.get(fn.__module__)) if sys.modules.get(fn.__module__) is not None else {}
        merged_localns = dict(localns) if localns is not None else None
        return get_type_hints(fn, globalns=module_globals, localns=merged_localns, include_extras=True)
    except _TYPE_HINT_ERRORS:
        return {}


def _get_type_hints_for_owner_safe(fn: Callable[..., object], owner: type) -> dict[str, object]:
    try:
        module_globals = vars(sys.modules.get(fn.__module__)) if sys.modules.get(fn.__module__) is not None else {}
        return get_type_hints(fn, globalns=module_globals, localns=vars(owner), include_extras=True)
    except _TYPE_HINT_ERRORS:
        return {}


def _infer_schema_from_func(
    fn: Callable[..., object],
    *,
    localns: Mapping[str, object] | None = None,
) -> dict[str, object]:
    signature = inspect.signature(fn)
    hints = _get_type_hints_safe(fn, localns=localns)

    properties: dict[str, object] = {}
    required: list[str] = []

    for name, parameter in signature.parameters.items():
        if name in _SKIP_PARAMS:
            continue
        if parameter.kind in (inspect.Parameter.VAR_POSITIONAL, inspect.Parameter.VAR_KEYWORD):
            continue

        prop = _schema_for_hint(hints.get(name, parameter.annotation))
        if parameter.default is inspect.Parameter.empty:
            required.append(name)
        else:
            prop["default"] = parameter.default
        properties[name] = prop

    schema: dict[str, object] = {"type": "object", "properties": properties}
    if required:
        schema["required"] = required
    return schema


def _infer_single_params_model_from_func(
    fn: Callable[..., object],
    *,
    localns: Mapping[str, object] | None = None,
) -> tuple[type | None, dict[str, object] | None]:
    signature = inspect.signature(fn)
    hints = _get_type_hints_safe(fn, localns=localns)
    return _infer_single_params_model_from_signature(signature, hints)


def _infer_single_params_model_from_owner(
    fn: Callable[..., object],
    owner: type,
) -> tuple[type | None, dict[str, object] | None]:
    signature = inspect.signature(fn)
    hints = _get_type_hints_for_owner_safe(fn, owner)
    return _infer_single_params_model_from_signature(signature, hints)


def _infer_single_params_model_from_signature(
    signature: inspect.Signature,
    hints: Mapping[str, object],
) -> tuple[type | None, dict[str, object] | None]:
    candidate_name: str | None = None
    candidate_model: type | None = None
    candidate_schema: dict[str, object] | None = None

    for name, parameter in signature.parameters.items():
        if name in _SKIP_PARAMS:
            continue
        if parameter.kind in (inspect.Parameter.VAR_POSITIONAL, inspect.Parameter.VAR_KEYWORD):
            continue
        if candidate_name is not None:
            return None, None
        model_type = _unwrap_model_annotation(hints.get(name, parameter.annotation))
        if model_type is None:
            return None, None
        schema = model_schema_from_type(model_type)
        if schema is None:
            return None, None
        candidate_name = name
        candidate_model = model_type
        candidate_schema = schema

    if candidate_name is None:
        return None, None
    return candidate_model, candidate_schema


def _infer_llm_result_model_from_func(
    fn: Callable[..., object],
    *,
    localns: Mapping[str, object] | None = None,
) -> tuple[type | None, dict[str, object] | None]:
    hints = _get_type_hints_safe(fn, localns=localns)
    return _infer_llm_result_model_from_signature(inspect.signature(fn), hints)


def _infer_llm_result_model_from_owner(
    fn: Callable[..., object],
    owner: type,
) -> tuple[type | None, dict[str, object] | None]:
    hints = _get_type_hints_for_owner_safe(fn, owner)
    return _infer_llm_result_model_from_signature(inspect.signature(fn), hints)


def _infer_llm_result_model_from_signature(
    signature: inspect.Signature,
    hints: Mapping[str, object],
) -> tuple[type | None, dict[str, object] | None]:
    return_hint = hints.get("return", signature.return_annotation)
    model_type = _unwrap_model_annotation(return_hint)
    if model_type is None:
        return None, None
    schema = model_schema_from_type(model_type)
    if schema is None:
        return None, None
    return model_type, schema


def _infer_output_contract_from_func(
    fn: Callable[..., object],
    *,
    localns: Mapping[str, object] | None = None,
) -> tuple[type | None, dict[str, object] | None]:
    hints = _get_type_hints_safe(fn, localns=localns)
    return _output_contract_from_signature(inspect.signature(fn), hints)


def _infer_output_contract_from_owner(
    fn: Callable[..., object],
    owner: type,
) -> tuple[type | None, dict[str, object] | None]:
    hints = _get_type_hints_for_owner_safe(fn, owner)
    return _output_contract_from_signature(inspect.signature(fn), hints)


def _output_contract_from_signature(
    signature: inspect.Signature,
    hints: Mapping[str, object],
) -> tuple[type | None, dict[str, object] | None]:
    """The llm_result inference, plus nullability: ``Model | None`` may return None.

    llm_result_* drop the ``None`` arm (they only describe a payload), but the
    output contract describes the whole return value, so it must accept it.
    """
    model, schema = _infer_llm_result_model_from_signature(signature, hints)
    if model is None or schema is None or not is_pydantic_model(model):
        # llm_result inference also accepts any class with a schema(); the
        # output contract is checked at runtime, so only Pydantic models,
        # which can validate themselves, are inferred.
        return None, None
    # Published like params=Model publishes input_schema (model_json_schema(),
    # validation mode), matching the runtime check, which builds the model.
    schema = dict(schema)
    if _hint_allows_none(hints.get("return", signature.return_annotation)):
        schema = dict(schema)
        defs = schema.pop("$defs", None)
        # Keep $defs at the root so "#/$defs/..." references still resolve.
        nullable: dict[str, object] = {"anyOf": [schema, {"type": "null"}]}
        if defs is not None:
            nullable["$defs"] = defs
        schema = nullable
    return model, schema


def _hint_allows_none(hint: object) -> bool:
    metadata_items = getattr(hint, "__metadata__", None)
    annotated_args = getattr(hint, "__args__", None)
    if metadata_items is not None and annotated_args:
        hint = annotated_args[0]
    if get_origin(hint) in (Union, types.UnionType):
        return type(None) in get_args(hint)
    return False


def _attach_event_meta(fn: F, meta: EventMeta) -> F:
    setattr(fn, EVENT_META_ATTR, meta)
    if meta.persist is not None:
        setattr(fn, PERSIST_ATTR, bool(meta.persist))
    return fn


def _attach_hook_meta(fn: F, meta: HookDecoratorMeta) -> F:
    setattr(fn, HOOK_META_ATTR, meta)
    return fn


def _normalize_mapping(value: Mapping[str, object] | None) -> dict[str, object]:
    return dict(value) if value is not None else {}


def _json_compatible_mapping(value: Mapping[str, object] | None) -> dict[str, JsonValue]:
    return cast(dict[str, JsonValue], dict(value) if value is not None else {})


def _merge_metadata(
    metadata: Mapping[str, object] | None,
    **extra_fields: object,
) -> dict[str, object]:
    merged = _normalize_mapping(metadata)
    for key, value in extra_fields.items():
        if value is not None:
            merged[key] = value
    return merged


def neko_plugin(cls: type) -> type:
    """Class marker for plugin discovery."""
    _finalize_plugin_entry_inference(cls)
    setattr(cls, NEKO_PLUGIN_TAG, True)
    return cls


def _finalize_plugin_entry_inference(owner: type) -> None:
    for member in vars(owner).values():
        if not callable(member):
            continue
        meta = getattr(member, EVENT_META_ATTR, None)
        if not isinstance(meta, EventMeta) or meta.event_type != "plugin_entry":
            continue

        if getattr(member, _AUTO_INFER_PARAMS_ATTR, False):
            inferred_model, inferred_schema = _infer_single_params_model_from_owner(member, owner)
            if inferred_model is not None and inferred_schema is not None:
                meta.params = inferred_model
                meta.input_schema = cast(InputSchema, inferred_schema)
                setattr(member, _PARAMS_MODEL_ATTR, inferred_model)

        if getattr(member, _AUTO_INFER_LLM_RESULT_ATTR, False):
            inferred_llm_model, inferred_llm_schema = _infer_llm_result_model_from_owner(member, owner)
            if inferred_llm_model is not None and inferred_llm_schema is not None:
                meta.llm_result_model = inferred_llm_model
                meta.llm_result_schema = cast(InputSchema, inferred_llm_schema)
                fields = fields_from_schema(inferred_llm_schema)
                meta.llm_result_fields = fields if fields else None

        if getattr(member, _AUTO_INFER_OUTPUT_ATTR, False):
            # Same return-annotation inference as llm_result_*, resolved
            # against the owner class for forward references.
            inferred_output_model, inferred_output_schema = _infer_output_contract_from_owner(member, owner)
            if inferred_output_model is not None and inferred_output_schema is not None:
                meta.output_model = inferred_output_model
                meta.output_schema = cast(InputSchema, inferred_output_schema)


def on_event(
    *,
    event_type: str,
    id: str | None = None,
    name: str | None = None,
    description: str = "",
    input_schema: dict[str, object] | None = None,
    kind: EntryKind = "action",
    auto_start: bool = False,
    persist: bool | None = None,
    mode: str | None = None,
    seconds: int | None = None,
    extra: dict[str, object] | None = None,
    metadata: dict[str, object] | None = None,
) -> Callable[[F], F]:
    event_type_clean = event_type.strip()
    if event_type_clean == "":
        raise ValueError("event_type must be non-empty")

    def _decorator(fn: F) -> F:
        event_id = (id or fn.__name__).strip()
        if event_id == "":
            raise ValueError("event id must be non-empty")
        event_name = (name or event_id).strip() or event_id
        effective_schema = input_schema if input_schema is not None else _infer_schema_from_func(fn)
        meta = EventMeta(
            event_type=event_type_clean,
            id=event_id,
            name=event_name,
            description=description,
            input_schema=cast(InputSchema | None, effective_schema),
            kind=kind,
            auto_start=auto_start,
            persist=persist,
            params=None,
            model_validate=True,
            timeout=None,
            extra=_json_compatible_mapping(_merge_metadata(extra, mode=mode, seconds=seconds)),
            metadata=_json_compatible_mapping(metadata),
        )
        return _attach_event_meta(fn, meta)

    return _decorator


def plugin_entry(
    *,
    id: str | None = None,
    name: object | None = None,
    description: object = "",
    input_schema: dict[str, object] | None = None,
    params: type | None = None,
    kind: EntryKind = "action",
    auto_start: bool = False,
    persist: bool | None = None,
    model_validate: bool = True,
    timeout: float | None = None,
    llm_result_fields: list[str] | None = None,
    llm_result_model: type | None = None,
    fields: type | None = None,
    metadata: dict[str, object] | None = None,
    quick_action: bool = False,
    output_schema: Mapping[str, object] | None = None,
    strict_output: bool = False,
    _localns: Mapping[str, object] | None = None,
) -> Callable[[F], F]:
    if output_schema is not None and not isinstance(output_schema, Mapping):
        raise ValueError("output_schema must be a JSON schema mapping")
    if input_schema is not None and params is not None:
        raise ValueError("input_schema and params are mutually exclusive")
    if sum(
        1
        for item in (llm_result_fields, llm_result_model, fields)
        if item is not None
    ) > 1:
        raise ValueError("llm_result_fields, llm_result_model, and fields are mutually exclusive")

    declaration_locals: dict[str, object] | None = dict(_localns) if _localns is not None else None
    if declaration_locals is None:
        current_frame = inspect.currentframe()
        try:
            caller_frame = current_frame.f_back if current_frame is not None else None
            if caller_frame is not None:
                declaration_locals = dict(caller_frame.f_locals)
        finally:
            del current_frame

    def _decorator(fn: F) -> F:
        event_id = (id or fn.__name__).strip()
        if event_id == "":
            raise ValueError("entry id must be non-empty")
        if name is None:
            event_name: object = event_id
        elif isinstance(name, str):
            event_name = name.strip() or event_id
        else:
            event_name = name
        inferred_params_model: type | None = None
        inferred_params_schema: dict[str, object] | None = None
        if params is None and input_schema is None:
            inferred_params_model, inferred_params_schema = _infer_single_params_model_from_func(
                fn,
                localns=declaration_locals,
            )
        effective_schema = input_schema
        if effective_schema is None and params is not None:
            effective_schema = model_schema_from_type(params)
        if effective_schema is None and inferred_params_schema is not None:
            effective_schema = inferred_params_schema
        if effective_schema is None:
            effective_schema = _infer_schema_from_func(fn, localns=declaration_locals)
        effective_params_model = params if params is not None else inferred_params_model
        llm_model = llm_result_model if llm_result_model is not None else fields
        try:
            normalized_llm_fields = normalize_llm_result_fields(llm_result_fields)
        except TypeError as exc:
            raise ValueError(str(exc)) from exc
        llm_schema = model_schema_from_type(llm_model) if llm_model is not None else None
        if llm_model is None and normalized_llm_fields is None:
            inferred_llm_model, inferred_llm_schema = _infer_llm_result_model_from_func(
                fn,
                localns=declaration_locals,
            )
            if inferred_llm_model is not None:
                llm_model = inferred_llm_model
                llm_schema = inferred_llm_schema
        if llm_model is not None and llm_schema is None:
            raise ValueError("llm_result_model/fields model must provide model_json_schema() or schema()")
        if llm_schema is None:
            llm_schema = schema_from_fields(normalized_llm_fields)
        if normalized_llm_fields is None and llm_schema is not None:
            normalized_llm_fields = fields_from_schema(llm_schema)
        # Output contract: an explicit schema wins; otherwise a Pydantic return
        # annotation is inferred like llm_result_* (its own lookup, so an
        # explicit llm_result_model does not become the output contract).
        output_model: type | None = None
        effective_output_schema: dict[str, object] | None = (
            cast(dict[str, object], detached_copy(output_schema)) if output_schema is not None else None
        )
        if effective_output_schema is None:
            output_model, effective_output_schema = _infer_output_contract_from_func(
                fn,
                localns=declaration_locals,
            )
            if output_model is None or effective_output_schema is None:
                output_model, effective_output_schema = None, None
        meta = EventMeta(
            event_type="plugin_entry",
            id=event_id,
            name=event_name,
            description=description,
            input_schema=cast(InputSchema | None, effective_schema),
            kind=kind,
            auto_start=auto_start,
            persist=persist,
            params=effective_params_model,
            model_validate=model_validate,
            timeout=timeout,
            llm_result_fields=list(normalized_llm_fields) if normalized_llm_fields else None,
            llm_result_schema=cast(InputSchema | None, llm_schema),
            llm_result_model=llm_model,
            output_schema=cast(InputSchema | None, effective_output_schema),
            output_model=output_model,
            strict_output=bool(strict_output),
            metadata=_json_compatible_mapping(metadata),
        )
        wrapped = _attach_event_meta(fn, meta)
        # Read @quick_action config stored on fn by the decorator below
        qa_cfg = getattr(fn, _QUICK_ACTION_CONFIG_ATTR, None)
        if quick_action or isinstance(qa_cfg, QuickActionConfig):
            meta.quick_action = True
            if isinstance(qa_cfg, QuickActionConfig):
                meta.quick_action_config = qa_cfg
        setattr(wrapped, _AUTO_INFER_PARAMS_ATTR, params is None and input_schema is None)
        setattr(wrapped, _AUTO_INFER_LLM_RESULT_ATTR, llm_model is None and normalized_llm_fields is None)
        setattr(wrapped, _AUTO_INFER_OUTPUT_ATTR, output_schema is None and output_model is None)
        if effective_params_model is not None:
            setattr(wrapped, _PARAMS_MODEL_ATTR, effective_params_model)
        return wrapped

    return _decorator


def lifecycle(
    *,
    id: Literal["startup", "shutdown", "reload", "freeze", "unfreeze", "config_change"],
    name: str | None = None,
    description: str = "",
    metadata: dict[str, object] | None = None,
) -> Callable[[F], F]:
    return on_event(
        event_type="lifecycle",
        id=id,
        name=name,
        description=description,
        kind="lifecycle",
        metadata=metadata,
    )


def message(
    *,
    id: str,
    name: str | None = None,
    description: str = "",
    input_schema: dict[str, object] | None = None,
    source: str | None = None,
    metadata: dict[str, object] | None = None,
) -> Callable[[F], F]:
    return on_event(
        event_type="message",
        id=id,
        name=name,
        description=description,
        input_schema=input_schema,
        kind="consumer",
        metadata=_merge_metadata(metadata, source=source),
    )


def timer_interval(
    *,
    id: str,
    seconds: int,
    name: str | None = None,
    description: str = "",
    auto_start: bool = True,
    metadata: dict[str, object] | None = None,
) -> Callable[[F], F]:
    if seconds <= 0:
        raise ValueError("seconds must be > 0")
    return on_event(
        event_type="timer",
        id=id,
        name=name,
        description=description,
        kind="timer",
        auto_start=auto_start,
        mode="interval",
        seconds=seconds,
        metadata=_merge_metadata(metadata, seconds=seconds),
    )


def custom_event(
    *,
    event_type: str,
    id: str,
    name: str | None = None,
    description: str = "",
    input_schema: dict[str, object] | None = None,
    kind: EntryKind = "custom",
    auto_start: bool = False,
    trigger_method: str = "message",
    metadata: dict[str, object] | None = None,
) -> Callable[[F], F]:
    return on_event(
        event_type=event_type,
        id=id,
        name=name,
        description=description,
        input_schema=input_schema,
        kind=kind,
        auto_start=auto_start,
        extra=_merge_metadata(None, trigger_method=trigger_method),
        metadata=_merge_metadata(metadata, trigger_method=trigger_method),
    )


def hook(*, target: str = "*", timing: str = "before", priority: int = 0, condition: str | None = None) -> Callable[[F], F]:
    if timing not in {"before", "after", "around", "replace"}:
        raise ValueError("timing must be one of: before, after, around, replace")

    def _decorator(fn: F) -> F:
        return _attach_hook_meta(
            fn,
            HookDecoratorMeta(target=target, timing=timing, priority=priority, condition=condition),
        )

    return _decorator


def before_entry(*, target: str = "*", priority: int = 0, condition: str | None = None) -> Callable[[F], F]:
    return hook(target=target, timing="before", priority=priority, condition=condition)


def after_entry(*, target: str = "*", priority: int = 0, condition: str | None = None) -> Callable[[F], F]:
    return hook(target=target, timing="after", priority=priority, condition=condition)


def around_entry(*, target: str = "*", priority: int = 0, condition: str | None = None) -> Callable[[F], F]:
    return hook(target=target, timing="around", priority=priority, condition=condition)


def replace_entry(*, target: str = "*", priority: int = 0, condition: str | None = None) -> Callable[[F], F]:
    return hook(target=target, timing="replace", priority=priority, condition=condition)


class _PluginDecorators:
    @staticmethod
    def entry(**kwargs: object) -> Callable[[F], F]:
        return cast(Callable[[F], F], plugin_entry(**cast(dict[str, Any], kwargs)))


plugin = _PluginDecorators()


def quick_action(
    *,
    icon: str | None = None,
    priority: int = 0,
) -> Callable[[F], F]:
    """Mark an entry as a quick action, prioritized in the command palette.

    Place below @plugin_entry (executed first); @plugin_entry reads the configuration.

    Usage::

        @plugin_entry(id="get_weather", name="获取天气")
        @quick_action(icon="🌤️", priority=10)
        async def get_weather(self, ...): ...

    Args:
        icon: Icon displayed in the palette (emoji), overriding the default ⚡.
        priority: Sort weight (higher comes first), default 0.
    """
    cfg = QuickActionConfig(icon=icon, priority=priority)

    def _decorator(fn: F) -> F:
        setattr(fn, _QUICK_ACTION_CONFIG_ATTR, cfg)
        return fn

    return _decorator


__all__ = [
    "EVENT_META_ATTR",
    "EntryKind",
    "EventMeta",
    "HOOK_META_ATTR",
    "HookDecoratorMeta",
    "PERSIST_ATTR",
    "after_entry",
    "around_entry",
    "before_entry",
    "custom_event",
    "hook",
    "lifecycle",
    "message",
    "neko_plugin",
    "on_event",
    "plugin",
    "plugin_entry",
    "quick_action",
    "replace_entry",
    "timer_interval",
]
