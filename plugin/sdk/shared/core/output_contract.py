"""Entry output contract (``output_schema``): build and run the checker.

The checker runs in the plugin process from ``EntryExecutor.invoke`` and is
built once per entry (the caller caches it). It reuses existing validators:

- A contract inferred from a Pydantic return annotation is checked by the
  model itself (``TypeAdapter``), so field/model validators and formats apply.
  Field names and aliases are both accepted: ``finish(data=model)`` emits
  field names unless the model serializes by alias. A returned model
  instance is trusted; it was validated when it was built.
- An explicit ``output_schema`` mapping is checked with ``jsonschema``
  (imported lazily, only for entries that declare one).

Privacy: an issue carries only a field path and an error type. Validator
messages quote the returned data, so they are never read. Path segments that
are not declared in the schema (keys of free-form mappings, extra keys) come
from the data too and are replaced by ``*``.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from itertools import islice
from typing import get_args

MAX_REPORTED_ISSUES = 20
# Issue types starting with this mean the check itself could not run; they
# are logged, but strict_output does not turn them into a failure.
CHECK_ERROR_PREFIX = "check_error:"
_WILDCARD = "*"
_CUSTOM_ERROR_TYPE = "custom_error"

# Envelope keys written by ``ctx.finish`` (``build_finish_envelope``).
_FINISH_ENVELOPE_KEYS = frozenset({
    "success", "code", "data", "message", "error", "time", "trace_id", "meta",
})


@dataclass(frozen=True, slots=True)
class OutputIssue:
    """One mismatch: where (``$.items[0].name``) and what kind (``type``)."""

    path: str
    type: str

    def as_dict(self) -> dict[str, str]:
        return {"path": self.path, "type": self.type}


OutputCheck = Callable[[object], list[OutputIssue]]


def output_payload(value: object) -> object:
    """The value the output contract describes.

    An entry that returns ``ctx.finish(data=...)`` hands back an envelope; the
    contract describes ``data``, the same payload ``llm_result_*`` checks.
    """
    if (
        isinstance(value, Mapping)
        and value.get("success") is True
        and _FINISH_ENVELOPE_KEYS.issubset(value.keys())
    ):
        return value.get("data")
    return value


def build_output_check(meta: object) -> OutputCheck | None:
    """Return the checker for an entry, or None when it declares no output contract."""
    model = getattr(meta, "output_model", None)
    schema = getattr(meta, "output_schema", None)
    schema = schema if isinstance(schema, Mapping) else None
    if isinstance(model, type) and is_pydantic_model(model):
        return _model_check(model, schema)
    if schema is not None:
        return _jsonschema_check(schema)
    return None


def is_violation(issues: Sequence[OutputIssue]) -> bool:
    """Whether any issue is a real mismatch rather than a checker failure."""
    return any(not issue.type.startswith(CHECK_ERROR_PREFIX) for issue in issues)


def format_issues(issues: Sequence[OutputIssue]) -> str:
    shown = ", ".join(f"{issue.path} ({issue.type})" for issue in issues[:MAX_REPORTED_ISSUES])
    more = len(issues) - MAX_REPORTED_ISSUES
    return f"{shown}, +{more} more" if more > 0 else shown


def is_pydantic_model(model: type) -> bool:
    """Whether ``model`` is a Pydantic v2 ``BaseModel`` subclass."""
    try:
        from pydantic import BaseModel
    except ImportError:  # pragma: no cover - pydantic is a hard dependency
        return False
    try:
        return issubclass(model, BaseModel)
    except TypeError:
        return False


def _check_error(exc: BaseException) -> list[OutputIssue]:
    return [OutputIssue(path="$", type=f"{CHECK_ERROR_PREFIX}{type(exc).__name__}")]


# ---------------------------------------------------------------------------
# Pydantic model contracts
# ---------------------------------------------------------------------------


def _model_check(model: type, schema: Mapping[str, object] | None) -> OutputCheck:
    from pydantic import TypeAdapter, ValidationError

    adapter = TypeAdapter(model)
    declared = _declared_names(schema) | _declared_names(model.model_json_schema())  # type: ignore[attr-defined]
    nullable = _allows_null(schema)

    def check(value: object) -> list[OutputIssue]:
        if isinstance(value, model) or (value is None and nullable):
            return []
        try:
            adapter.validate_python(value, by_alias=True, by_name=True)
        except ValidationError as exc:
            try:
                errors = exc.errors(include_url=False, include_input=False, include_context=False)
                return [
                    OutputIssue(_loc_path(error.get("loc", ()), declared, value), _error_type(error.get("type")))
                    for error in errors[: MAX_REPORTED_ISSUES + 1]
                ]
            except Exception as path_exc:  # noqa: BLE001 - reporting must not fail the call
                return _check_error(path_exc)
        except Exception as exc:  # noqa: BLE001 - a crashing custom validator is a checker failure
            return _check_error(exc)
        return []

    return check


def _allows_null(schema: Mapping[str, object] | None) -> bool:
    """``Model | None`` publishes ``anyOf: [<model>, {"type": "null"}]``."""
    branches = schema.get("anyOf") if schema is not None else None
    return isinstance(branches, list) and {"type": "null"} in branches


def _builtin_error_types() -> frozenset[str]:
    try:
        from pydantic_core.core_schema import ErrorType
    except ImportError:  # pragma: no cover - pydantic_core ships with pydantic
        return frozenset()
    return frozenset(item for item in get_args(ErrorType) if isinstance(item, str))


_BUILTIN_ERROR_TYPES = _builtin_error_types()


def _error_type(raw: object) -> str:
    """Pydantic's own error types only; a custom error type is free text and may embed data."""
    return raw if isinstance(raw, str) and raw in _BUILTIN_ERROR_TYPES else _CUSTOM_ERROR_TYPE


# ---------------------------------------------------------------------------
# Explicit JSON Schema contracts
# ---------------------------------------------------------------------------


def _jsonschema_check(schema: Mapping[str, object]) -> OutputCheck:
    try:
        from jsonschema import validators
        from referencing import Registry
    except Exception as exc:  # noqa: BLE001 - missing dependency: report, never fail calls
        failure = _check_error(exc)
        return lambda _value: failure

    validator_class = validators.validator_for(schema)
    # A tuple is a JSON array too (results are JSON-encoded on the way out).
    type_checker = validator_class.TYPE_CHECKER.redefine(
        "array", lambda _checker, instance: isinstance(instance, (list, tuple))
    )
    # An empty registry without ``retrieve``: a remote or file ``$ref`` is
    # unresolvable (a checker failure) instead of blocking I/O in the plugin.
    validator = validators.extend(validator_class, type_checker=type_checker)(dict(schema), registry=Registry())
    declared = _declared_names(schema)

    def check(value: object) -> list[OutputIssue]:
        try:
            errors = list(islice(validator.iter_errors(value), MAX_REPORTED_ISSUES + 1))
            # ``validator`` is the failing keyword; ``message`` quotes data and is not read.
            return [
                OutputIssue(_loc_path(list(error.absolute_path), declared, value), str(error.validator))
                for error in errors
            ]
        except Exception as exc:  # noqa: BLE001 - malformed schema or unresolvable $ref
            return _check_error(exc)

    return check


# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------


def _loc_path(loc: Sequence[object], declared: frozenset[str], value: object) -> str:
    """Render an error location without any segment that comes from the data.

    The location is walked along the value: an integer is kept only where the
    value holds a list (an index); mapping keys, integer or not, are kept only
    when the schema declares them, otherwise they become ``*``. Only plain
    containers are walked, so no attribute access runs plugin code.
    """
    path = "$"
    current: object = value
    for part in loc:
        if isinstance(current, (list, tuple)) and isinstance(part, int) and not isinstance(part, bool):
            path += f"[{part}]"
            current = current[part] if 0 <= part < len(current) else None
            continue
        path += f".{part}" if isinstance(part, str) and part in declared else f".{_WILDCARD}"
        current = current.get(part) if isinstance(current, Mapping) else None
    return path


def _declared_names(schema: object) -> frozenset[str]:
    """Every property name the schema declares, at any depth (including ``$defs``)."""
    names: set[str] = set()
    stack: list[object] = [schema]
    seen: set[int] = set()
    while stack:
        node = stack.pop()
        if id(node) in seen:
            continue
        seen.add(id(node))
        if isinstance(node, Mapping):
            properties = node.get("properties")
            if isinstance(properties, Mapping):
                names.update(key for key in properties if isinstance(key, str))
            stack.extend(node.values())
        elif isinstance(node, list):
            stack.extend(node)
    return frozenset(names)


__all__ = [
    "CHECK_ERROR_PREFIX",
    "MAX_REPORTED_ISSUES",
    "OutputCheck",
    "OutputIssue",
    "build_output_check",
    "format_issues",
    "is_pydantic_model",
    "is_violation",
    "output_payload",
]
