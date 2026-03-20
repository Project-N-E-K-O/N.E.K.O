"""Helpers for entry-level LLM result contracts.

This module keeps output-contract declaration and validation logic separate
from the plugin-facing decorator/context facades.
"""

from __future__ import annotations

import types
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, get_args, get_origin


class LlmResultValidationError(ValueError):
    """Raised when a reply-bound plugin result violates its declared contract."""


@dataclass(slots=True)
class LlmResultContract:
    fields: tuple[str, ...] = ()
    schema: dict[str, object] | None = None
    model: type | None = None

    @property
    def enabled(self) -> bool:
        return bool(self.fields) or self.schema is not None or self.model is not None


def model_schema_from_type(model_type: type) -> dict[str, object] | None:
    model_json_schema = getattr(model_type, "model_json_schema", None)
    if callable(model_json_schema):
        try:
            schema = model_json_schema()
        except Exception:
            schema = None
        if isinstance(schema, dict):
            return dict(schema)

    fallback = _schema_from_declared_fields(model_type)
    if fallback is not None:
        return fallback

    schema_fn = getattr(model_type, "schema", None)
    if callable(schema_fn):
        try:
            schema = schema_fn()
        except Exception:
            schema = None
        if isinstance(schema, dict):
            return dict(schema)

    return None


_PY_TYPE_TO_JSON: dict[type, str] = {
    str: "string",
    int: "integer",
    float: "number",
    bool: "boolean",
    dict: "object",
    list: "array",
}
_UNDEFINED_SENTINELS = frozenset({"PydanticUndefined", "Undefined"})


def _schema_for_annotation(annotation: object) -> dict[str, object]:
    schema: dict[str, object] = {}
    metadata_items = getattr(annotation, "__metadata__", None)
    annotated_args = getattr(annotation, "__args__", None)
    if metadata_items is not None and annotated_args:
        annotation = annotated_args[0]

    origin = get_origin(annotation)
    args = get_args(annotation)

    if origin in (types.UnionType, getattr(types, "UnionType", object)) or str(origin) == "typing.Union":
        non_none = [item for item in args if item is not type(None)]
        if len(non_none) == 1:
            inner = _schema_for_annotation(non_none[0])
            inner_type = inner.get("type")
            if isinstance(inner_type, str):
                inner["type"] = [inner_type, "null"]
            elif isinstance(inner_type, list) and "null" not in inner_type:
                inner["type"] = [*inner_type, "null"]
            return inner
        return schema

    if origin is list:
        schema["type"] = "array"
        if args:
            item_schema = _schema_for_annotation(args[0])
            if item_schema:
                schema["items"] = item_schema
        return schema

    if origin is dict:
        schema["type"] = "object"
        return schema

    json_type = _PY_TYPE_TO_JSON.get(annotation) if isinstance(annotation, type) else None
    if json_type is not None:
        schema["type"] = json_type
    return schema


def _is_required_field(field: object) -> bool:
    is_required = getattr(field, "is_required", None)
    if callable(is_required):
        try:
            return bool(is_required())
        except Exception:
            return False
    required = getattr(field, "required", None)
    if isinstance(required, bool):
        return required
    default = getattr(field, "default", ...)
    return type(default).__name__ in _UNDEFINED_SENTINELS


def _field_annotation(field: object) -> object:
    return getattr(field, "annotation", getattr(field, "outer_type_", getattr(field, "type_", object)))


def _field_default(field: object) -> object:
    return getattr(field, "default", ...)


def _schema_from_declared_fields(model_type: type) -> dict[str, object] | None:
    model_fields = getattr(model_type, "model_fields", None)
    if isinstance(model_fields, Mapping) and model_fields:
        properties: dict[str, object] = {}
        required: list[str] = []
        for name, field in model_fields.items():
            if not isinstance(name, str):
                continue
            prop = _schema_for_annotation(_field_annotation(field))
            default = _field_default(field)
            if _is_required_field(field):
                required.append(name)
            elif default is not ... and type(default).__name__ not in _UNDEFINED_SENTINELS:
                prop["default"] = default
            properties[name] = prop
        if properties:
            schema: dict[str, object] = {"type": "object", "properties": properties}
            if required:
                schema["required"] = required
            return schema

    legacy_fields = getattr(model_type, "__fields__", None)
    if isinstance(legacy_fields, Mapping) and legacy_fields:
        properties = {}
        required = []
        for name, field in legacy_fields.items():
            if not isinstance(name, str):
                continue
            prop = _schema_for_annotation(_field_annotation(field))
            default = _field_default(field)
            if _is_required_field(field):
                required.append(name)
            elif default is not ... and type(default).__name__ not in _UNDEFINED_SENTINELS:
                prop["default"] = default
            properties[name] = prop
        if properties:
            schema = {"type": "object", "properties": properties}
            if required:
                schema["required"] = required
            return schema

    return None


def normalize_llm_result_fields(value: object) -> list[str] | None:
    if value is None:
        return None
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        raise TypeError("llm_result_fields must be a sequence of field names")

    normalized: list[str] = []
    seen: set[str] = set()
    for item in value:
        if not isinstance(item, str):
            raise TypeError("llm_result_fields must contain only strings")
        field_name = item.strip()
        if not field_name or field_name in seen:
            continue
        seen.add(field_name)
        normalized.append(field_name)
    return normalized


def schema_from_fields(fields: Sequence[str] | None) -> dict[str, object] | None:
    if not fields:
        return None
    properties = {field_name: {} for field_name in fields}
    return {
        "type": "object",
        "properties": properties,
        "required": list(fields),
    }


def fields_from_schema(schema: Mapping[str, object] | None) -> list[str] | None:
    if not isinstance(schema, Mapping):
        return None
    required_obj = schema.get("required")
    if not isinstance(required_obj, Sequence) or isinstance(required_obj, (str, bytes, bytearray)):
        return None
    fields: list[str] = []
    for item in required_obj:
        if not isinstance(item, str):
            continue
        field_name = item.strip()
        if field_name:
            fields.append(field_name)
    return fields


def contract_from_meta(meta: object) -> LlmResultContract:
    raw_fields = getattr(meta, "llm_result_fields", None)
    fields: tuple[str, ...] = ()
    normalized = normalize_llm_result_fields(raw_fields)
    if normalized is not None:
        fields = tuple(normalized)

    raw_schema = getattr(meta, "llm_result_schema", None)
    schema: dict[str, object] | None = dict(raw_schema) if isinstance(raw_schema, Mapping) else None

    if not fields and schema is not None:
        derived_fields = fields_from_schema(schema)
        if derived_fields is not None:
            fields = tuple(derived_fields)

    model = getattr(meta, "llm_result_model", None)
    return LlmResultContract(fields=fields, schema=schema, model=model if isinstance(model, type) else None)


def _context_text(*, plugin_id: str | None, entry_id: str | None) -> str:
    if plugin_id and entry_id:
        return f" for {plugin_id}.{entry_id}"
    if plugin_id:
        return f" for {plugin_id}"
    return ""


def _validate_model_payload(model_type: type, value: object) -> None:
    validator = getattr(model_type, "model_validate", None)
    if callable(validator):
        validator(value)
        return

    parser = getattr(model_type, "parse_obj", None)
    if callable(parser):
        parser(value)
        return

    if isinstance(value, Mapping):
        model_type(**dict(value))
        return

    model_type(value)


def validate_reply_payload(
    contract: LlmResultContract,
    payload: object,
    *,
    export_type: str | None = None,
    plugin_id: str | None = None,
    entry_id: str | None = None,
) -> object:
    if not contract.enabled:
        return payload

    context = _context_text(plugin_id=plugin_id, entry_id=entry_id)
    if export_type is not None and export_type != "json":
        raise LlmResultValidationError(
            f"reply output{context} must use JSON payloads when llm_result contract is declared"
        )

    if contract.model is not None:
        try:
            _validate_model_payload(contract.model, payload)
        except Exception as exc:
            raise LlmResultValidationError(
                f"reply output{context} does not satisfy llm_result_model: {exc}"
            ) from exc
        return payload

    if contract.fields:
        if not isinstance(payload, Mapping):
            raise LlmResultValidationError(
                f"reply output{context} must be an object containing {list(contract.fields)}"
            )
        missing = [field_name for field_name in contract.fields if field_name not in payload]
        if missing:
            raise LlmResultValidationError(
                f"reply output{context} is missing required llm_result fields: {missing}"
            )
        return payload

    if contract.schema is not None:
        schema_type = contract.schema.get("type")
        if schema_type == "object" and not isinstance(payload, Mapping):
            raise LlmResultValidationError(f"reply output{context} must be an object")
    return payload


__all__ = [
    "LlmResultContract",
    "LlmResultValidationError",
    "contract_from_meta",
    "fields_from_schema",
    "model_schema_from_type",
    "normalize_llm_result_fields",
    "schema_from_fields",
    "validate_reply_payload",
]
