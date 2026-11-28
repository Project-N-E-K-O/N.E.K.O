"""Entry output contract (``output_schema``): the runtime check, symmetric with inputs.

Inputs: ``params=Model`` is validated with the model at runtime; an
``input_schema`` mapping is only published. Outputs work the same way:

- A contract inferred from a Pydantic return annotation is checked by building
  the model from the returned data, with the same helper inputs use
  (``entry_runtime._build_model_instance``). A returned model instance is
  trusted; it was validated when it was built.
- An explicit ``output_schema`` mapping is only published, never checked.

Privacy: an issue carries only a field path and an error type. Validator
messages quote the returned data, so they are never read. Path segments that
are not declared in the schema (keys of free-form mappings, extra keys) come
from the data too and are replaced by ``*``.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import get_args

from .entry_runtime import _build_model_instance
from .result_contract import model_schema_from_type

MAX_REPORTED_ISSUES = 20
# Issue types starting with this mean the check itself could not run; they
# are logged, but strict_output does not turn them into a failure.
CHECK_ERROR_PREFIX = "check_error:"
_WILDCARD = "*"
_CUSTOM_ERROR_TYPE = "custom_error"


@dataclass(frozen=True, slots=True)
class OutputIssue:
    """One mismatch: where (``$.items[0].name``) and what kind (``int_parsing``)."""

    path: str
    type: str

    def as_dict(self) -> dict[str, str]:
        return {"path": self.path, "type": self.type}


OutputCheck = Callable[[object], list[OutputIssue]]


def build_output_check(meta: object) -> OutputCheck | None:
    """The runtime check of an entry, or None (no contract, or an explicit one: publish only)."""
    model = getattr(meta, "output_model", None)
    if not (isinstance(model, type) and is_pydantic_model(model)):
        return None
    schema = getattr(meta, "output_schema", None)
    return _model_check(model, schema if isinstance(schema, Mapping) else None)


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


def _model_check(model: type, schema: Mapping[str, object] | None) -> OutputCheck:
    from pydantic import ValidationError

    declared = _declared_names(schema)
    # ``Model | None`` publishes ``anyOf: [<model>, {"type": "null"}]`` around
    # the model's own schema. A model whose own schema allows null (e.g.
    # ``RootModel[int | None]``) is not that: compare with the model's schema
    # so only the annotation's ``| None`` lets a bare None skip the model.
    branches = schema.get("anyOf") if schema is not None else None
    nullable = (
        isinstance(branches, list)
        and {"type": "null"} in branches
        and schema != model_schema_from_type(model)
    )

    def check(value: object) -> list[OutputIssue]:
        if isinstance(value, model) or (value is None and nullable):
            return []
        try:
            _build_model_instance(model, value)  # type: ignore[arg-type]
        except ValidationError as exc:
            errors = exc.errors(include_url=False, include_input=False, include_context=False)
            return [
                OutputIssue(_loc_path(error.get("loc", ()), declared, value), _error_type(error.get("type")))
                for error in errors[: MAX_REPORTED_ISSUES + 1]
            ]
        except Exception as exc:  # noqa: BLE001 - a crashing custom validator is a checker failure
            return [OutputIssue("$", f"{CHECK_ERROR_PREFIX}{type(exc).__name__}")]
        return []

    return check


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
    "OutputCheck",
    "OutputIssue",
    "build_output_check",
    "format_issues",
    "is_pydantic_model",
    "is_violation",
]
