"""Entry output contract (``output_schema``): build and run the checker.

The checker runs in the plugin process, once per entry call, from
``EntryExecutor.invoke``. It is built once per entry and cached by the caller.

Privacy: an issue carries only a field path and an error type. Error messages
from Pydantic (``input_value=...``) and JSON Schema validators quote the
returned data, so they are never kept. Path segments that are not declared in
the schema (keys of free-form mappings, extra keys) come from the data too and
are replaced by ``*``.

Two checkers:

- The output contract was inferred from a Pydantic model: the model itself
  validates (``TypeAdapter``), so the published schema and the check agree.
- An explicit ``output_schema`` mapping: a built-in checker for a structural
  subset of JSON Schema (listed in ``SUPPORTED_KEYWORDS``). Other keywords are
  ignored rather than guessed at. The full ``jsonschema`` package is not a
  runtime dependency of N.E.K.O, and pulling it in adds a native extension
  (``rpds-py``) to every packaged build for a warning-level feature.
"""

from __future__ import annotations

import copy
import json
import math
import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, get_args
from urllib.parse import unquote

from .finish import normalize_structured_data

MAX_REPORTED_ISSUES = 20
# Issue types starting with this mean the check itself failed to run; they are
# logged, but strict_output does not turn them into a failure.
CHECK_ERROR_PREFIX = "check_error:"
# Consecutive $ref hops without descending into the data: only a reference
# cycle such as {"$ref": "#"} gets this far. Real recursion consumes data.
_MAX_REF_DEPTH = 32
# Schema nodes visited in one check; beyond this the checker gives up.
_MAX_STEPS = 200_000
_WILDCARD = "*"

SUPPORTED_KEYWORDS = frozenset({
    "$ref", "$defs", "definitions",
    "type", "enum", "const",
    "properties", "patternProperties", "required", "additionalProperties",
    "items", "prefixItems", "additionalItems", "minItems", "maxItems",
    "minLength", "maxLength", "pattern",
    "minimum", "maximum", "exclusiveMinimum", "exclusiveMaximum",
    "anyOf", "oneOf", "allOf", "not",
})

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
    if isinstance(model, type) and is_pydantic_model(model):
        return _pydantic_check(model, schema if isinstance(schema, Mapping) else None)
    if isinstance(schema, Mapping):
        return _JsonSchemaCheck(schema)
    return None


def is_violation(issues: Sequence[OutputIssue]) -> bool:
    """Whether any issue is a real mismatch rather than a checker failure."""
    return any(not issue.type.startswith(CHECK_ERROR_PREFIX) for issue in issues)


def format_issues(issues: Sequence[OutputIssue]) -> str:
    shown = ", ".join(f"{issue.path} ({issue.type})" for issue in issues[:MAX_REPORTED_ISSUES])
    more = len(issues) - MAX_REPORTED_ISSUES
    return f"{shown}, +{more} more" if more > 0 else shown


# ---------------------------------------------------------------------------
# Pydantic
# ---------------------------------------------------------------------------


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


def _pydantic_check(model: type, schema: Mapping[str, object] | None) -> OutputCheck:
    from pydantic import TypeAdapter, ValidationError

    adapter = TypeAdapter(model)
    # Field names (what model_dump()/finish() put on the wire, and what the
    # published schema uses) plus aliases (what Pydantic puts in a loc).
    declared = _declared_names(schema) if schema is not None else frozenset()
    try:
        declared = declared | _declared_names(model.model_json_schema())  # type: ignore[attr-defined]
    except Exception:  # noqa: BLE001 - alias names only refine paths
        pass
    # ``Model | None`` publishes a nullable schema; the model alone rejects None.
    nullable = schema is not None and not _JsonSchemaCheck(schema)(None)

    def check(value: object) -> list[OutputIssue]:
        if value is None and nullable:
            return []
        if isinstance(value, model) and not _revalidates(model, value):
            # Validated when it was built, and the model does not ask for
            # instances to be revalidated. Validating anyway would rerun
            # after-validators; when it does ask, a copy is validated below.
            return []
        try:
            # Validators may mutate their input; check a private copy so the
            # returned value is never touched.
            probe = copy.deepcopy(value)
        except Exception as exc:  # noqa: BLE001 - uncopyable data cannot be checked safely
            return [OutputIssue(path="$", type=f"{CHECK_ERROR_PREFIX}{type(exc).__name__}")]
        try:
            # Results arrive by field name (model_dump() in finish(), transport
            # of returned models); accept aliases too.
            adapter.validate_python(probe, **_BY_NAME_AND_ALIAS)
        except ValidationError as exc:
            # include_input/include_context off: only loc and type are read,
            # but nothing that quotes the data is even materialized.
            try:
                errors = exc.errors(include_url=False, include_input=False, include_context=False)
                # Paths are walked along the copy the validator received.
                return [
                    OutputIssue(path=_loc_path(error.get("loc", ()), declared, probe), type=_error_type(error.get("type")))
                    for error in errors[:MAX_REPORTED_ISSUES + 1]
                ]
            except Exception as path_exc:  # noqa: BLE001 - reporting must not turn into a call failure
                return [OutputIssue(path="$", type=f"{CHECK_ERROR_PREFIX}{type(path_exc).__name__}")]
        except Exception as exc:  # noqa: BLE001 - a crashing custom validator is a checker failure
            return [OutputIssue(path="$", type=f"{CHECK_ERROR_PREFIX}{type(exc).__name__}")]
        return []

    return check


def _builtin_error_types() -> frozenset[str]:
    try:
        from pydantic_core.core_schema import ErrorType
    except ImportError:  # pragma: no cover - pydantic_core ships with pydantic
        return frozenset()
    return frozenset(item for item in get_args(ErrorType) if isinstance(item, str))


_BUILTIN_ERROR_TYPES = _builtin_error_types()


def _by_name_and_alias() -> dict[str, bool]:
    """``validate_python`` options accepting field names and aliases (Pydantic >= 2.11)."""
    try:
        from pydantic import VERSION
    except ImportError:  # pragma: no cover
        return {}
    major, minor = (int(part) for part in VERSION.split(".")[:2])
    return {"by_alias": True, "by_name": True} if (major, minor) >= (2, 11) else {}


_BY_NAME_AND_ALIAS = _by_name_and_alias()
_CUSTOM_ERROR_TYPE = "custom_error"


def _error_type(raw: object) -> str:
    """Pydantic's own error types only; a custom validator's type is free text.

    ``PydanticCustomError`` lets a validator choose any type string, which may
    be built from the data, so it is replaced by a fixed code.
    """
    return raw if isinstance(raw, str) and raw in _BUILTIN_ERROR_TYPES else _CUSTOM_ERROR_TYPE


def _revalidates(model: type, value: object) -> bool:
    """Whether the model's own ``revalidate_instances`` setting covers ``value``."""
    config = getattr(model, "model_config", None)
    setting = config.get("revalidate_instances") if isinstance(config, Mapping) else None
    if setting == "always":
        return True
    if setting == "subclass-instances":
        return type(value) is not model
    return False


def _loc_path(loc: Sequence[object], declared: frozenset[str], value: object) -> str:
    """Render a Pydantic ``loc`` without any segment that comes from the data.

    The loc is walked along the input: an integer is kept only where the
    input holds a list (an index); mapping keys, integer or not, are kept only
    when the schema declares them, otherwise they become ``*``.
    """
    path = "$"
    current: object = value
    for part in loc:
        if isinstance(current, (list, tuple)) and isinstance(part, int) and not isinstance(part, bool):
            path += f"[{part}]"
            current = current[part] if 0 <= part < len(current) else None
            continue
        if isinstance(part, str) and part in declared:
            path += f".{part}"
        else:
            path += f".{_WILDCARD}"
        # Only plain containers are walked. Attribute access on other objects
        # (from_attributes models) could run code; past one, segments that are
        # not declared names render as ``*``.
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


# ---------------------------------------------------------------------------
# JSON Schema subset
# ---------------------------------------------------------------------------


class _JsonSchemaCheck:
    """Structural subset of JSON Schema; unsupported keywords are ignored."""

    def __init__(self, schema: Mapping[str, object]):
        self._root = schema
        self._patterns: dict[str, re.Pattern[str] | None] = {}
        self._steps = 0

    def __call__(self, value: object) -> list[OutputIssue]:
        issues: list[OutputIssue] = []
        self._steps = 0
        try:
            self._check(self._root, _json_form(value), "$", issues, 0)
        except _Enough:
            pass
        except _CheckAborted as exc:
            # Partial findings are dropped: an unfinished probe inside
            # anyOf/oneOf/not may have produced them.
            return [OutputIssue(path="$", type=f"{CHECK_ERROR_PREFIX}{exc.args[0]}")]
        except Exception as exc:  # noqa: BLE001 - a malformed schema or overly deep data
            # The checker could not finish: reported, but never a violation.
            issues.append(OutputIssue(path="$", type=f"{CHECK_ERROR_PREFIX}{type(exc).__name__}"))
        return issues

    # -- helpers -----------------------------------------------------------

    def _resolve(self, ref: object) -> object:
        """The referenced schema (a mapping or a boolean), or ``_UNRESOLVED``.

        Local references only: ``#`` is the root, ``#/a/b`` a JSON Pointer
        (RFC 6901) after URI-fragment decoding; empty segments are keys.
        """
        if not isinstance(ref, str) or not ref.startswith("#"):
            return _UNRESOLVED
        pointer = unquote(ref[1:])
        node: object = self._root
        if pointer:
            if not pointer.startswith("/"):
                return _UNRESOLVED  # a plain-name anchor, not supported
            for token in pointer[1:].split("/"):
                token = token.replace("~1", "/").replace("~0", "~")
                if isinstance(node, Mapping) and token in node:
                    node = node[token]
                elif isinstance(node, list) and _ARRAY_INDEX.fullmatch(token) and int(token) < len(node):
                    node = node[int(token)]
                else:
                    return _UNRESOLVED
        return node if isinstance(node, (Mapping, bool)) else _UNRESOLVED

    def _pattern(self, pattern: str) -> re.Pattern[str] | None:
        if pattern not in self._patterns:
            try:
                self._patterns[pattern] = re.compile(pattern)
            except re.error:
                self._patterns[pattern] = None
        return self._patterns[pattern]

    def _matches(self, schema: object, value: object, depth: int) -> bool:
        probe: list[OutputIssue] = []
        try:
            self._check(schema, value, "$", probe, depth, limit=1)
        except _Enough:
            return False
        return not probe

    @staticmethod
    def _add(issues: list[OutputIssue], path: str, kind: str, limit: int) -> None:
        issues.append(OutputIssue(path=path, type=kind))
        if len(issues) >= limit:
            raise _Enough

    # -- the walk ----------------------------------------------------------

    def _check(
        self,
        schema: object,
        value: object,
        path: str,
        issues: list[OutputIssue],
        depth: int,
        *,
        limit: int = MAX_REPORTED_ISSUES + 1,
    ) -> None:
        self._steps += 1
        if self._steps > _MAX_STEPS:
            # Nested anyOf/oneOf/not can re-walk the same subtree many times.
            raise _CheckAborted("budget")
        if schema is True or schema is None:
            return
        if schema is False:
            self._add(issues, path, "false", limit)
            return
        if not isinstance(schema, Mapping):
            return

        if "$ref" in schema:
            target = self._resolve(schema["$ref"])
            # An unresolvable (e.g. external) reference is not ours to judge;
            # the hop budget only counts references actually followed.
            if target is not _UNRESOLVED:
                if depth >= _MAX_REF_DEPTH:
                    # Not a verdict on the data: the checker gives up.
                    raise _CheckAborted("ref_depth")
                self._check(target, value, path, issues, depth + 1, limit=limit)  # $ref hop

        expected = schema.get("type")
        if expected is not None:
            names = [expected] if isinstance(expected, str) else expected
            if isinstance(names, list) and names and not any(_is_type(value, name) for name in names):
                # Further keywords would only repeat the same mistake.
                self._add(issues, path, "type", limit)
                return

        if "const" in schema and not _json_equal(value, schema["const"]):
            self._add(issues, path, "const", limit)
        enum = schema.get("enum")
        if isinstance(enum, list) and not any(_json_equal(value, item) for item in enum):
            self._add(issues, path, "enum", limit)

        all_of = schema.get("allOf")
        if isinstance(all_of, list):
            for branch in all_of:
                self._check(branch, value, path, issues, depth, limit=limit)
        any_of = schema.get("anyOf")
        if isinstance(any_of, list) and any_of and not any(self._matches(b, value, depth) for b in any_of):
            self._add(issues, path, "anyOf", limit)
        one_of = schema.get("oneOf")
        if isinstance(one_of, list) and one_of:
            matched = 0
            for branch in one_of:
                if self._matches(branch, value, depth):
                    matched += 1
                    if matched > 1:
                        break  # already a violation; later branches cannot change it
            if matched != 1:
                self._add(issues, path, "oneOf", limit)
        if "not" in schema and self._matches(schema["not"], value, depth):
            self._add(issues, path, "not", limit)

        if isinstance(value, str):
            self._check_string(schema, value, path, issues, limit)
        elif isinstance(value, (int, float)) and not isinstance(value, bool):
            self._check_number(schema, value, path, issues, limit)
        elif isinstance(value, Mapping):
            self._check_object(schema, value, path, issues, depth, limit)
        elif isinstance(value, list):
            self._check_array(schema, value, path, issues, depth, limit)

    def _check_string(self, schema: Mapping[str, object], value: str, path: str, issues: list[OutputIssue], limit: int) -> None:
        min_length = schema.get("minLength")
        if _is_count(min_length) and len(value) < min_length:  # type: ignore[operator]
            self._add(issues, path, "minLength", limit)
        max_length = schema.get("maxLength")
        if _is_count(max_length) and len(value) > max_length:  # type: ignore[operator]
            self._add(issues, path, "maxLength", limit)
        pattern = schema.get("pattern")
        if isinstance(pattern, str):
            compiled = self._pattern(pattern)
            if compiled is not None and compiled.search(value) is None:
                self._add(issues, path, "pattern", limit)

    def _check_number(self, schema: Mapping[str, object], value: float, path: str, issues: list[OutputIssue], limit: int) -> None:
        minimum = schema.get("minimum")
        if _is_number(minimum) and value < minimum:  # type: ignore[operator]
            self._add(issues, path, "minimum", limit)
        maximum = schema.get("maximum")
        if _is_number(maximum) and value > maximum:  # type: ignore[operator]
            self._add(issues, path, "maximum", limit)
        exclusive_minimum = schema.get("exclusiveMinimum")
        if _is_number(exclusive_minimum) and value <= exclusive_minimum:  # type: ignore[operator]
            self._add(issues, path, "exclusiveMinimum", limit)
        exclusive_maximum = schema.get("exclusiveMaximum")
        if _is_number(exclusive_maximum) and value >= exclusive_maximum:  # type: ignore[operator]
            self._add(issues, path, "exclusiveMaximum", limit)

    def _check_object(
        self,
        schema: Mapping[str, object],
        value: Mapping[Any, object],
        path: str,
        issues: list[OutputIssue],
        depth: int,
        limit: int,
    ) -> None:
        properties = schema.get("properties")
        properties = properties if isinstance(properties, Mapping) else {}
        required = schema.get("required")
        if isinstance(required, list):
            for name in required:
                if isinstance(name, str) and name not in value:
                    self._add(issues, f"{path}.{name}", "required", limit)
        for name, sub_schema in properties.items():
            if name in value:
                self._check(sub_schema, value[name], f"{path}.{name}", issues, 0, limit=limit)
        patterns: list[tuple[re.Pattern[str], object]] = []
        pattern_properties = schema.get("patternProperties")
        if isinstance(pattern_properties, Mapping):
            for raw, sub_schema in pattern_properties.items():
                compiled = self._pattern(raw) if isinstance(raw, str) else None
                if compiled is not None:
                    patterns.append((compiled, sub_schema))
        additional = schema.get("additionalProperties")
        for key, item in value.items():
            matched = [sub for compiled, sub in patterns if isinstance(key, str) and compiled.search(key)]
            declared = key in properties
            for sub_schema in matched:
                # A declared key must satisfy matching patterns too. Other
                # pattern-matched keys come from the data: no name in paths.
                child = f"{path}.{key}" if declared else f"{path}.{_WILDCARD}"
                self._check(sub_schema, item, child, issues, 0, limit=limit)
            if declared or matched or additional is None or additional is True:
                continue
            # Undeclared keys come from the data: never put them in a path.
            child = f"{path}.{_WILDCARD}"
            if additional is False:
                self._add(issues, child, "additionalProperties", limit)
            else:
                self._check(additional, item, child, issues, 0, limit=limit)

    def _check_array(
        self,
        schema: Mapping[str, object],
        value: list[object],
        path: str,
        issues: list[OutputIssue],
        depth: int,
        limit: int,
    ) -> None:
        min_items = schema.get("minItems")
        if _is_count(min_items) and len(value) < min_items:  # type: ignore[operator]
            self._add(issues, path, "minItems", limit)
        max_items = schema.get("maxItems")
        if _is_count(max_items) and len(value) > max_items:  # type: ignore[operator]
            self._add(issues, path, "maxItems", limit)
        prefix = schema.get("prefixItems")
        items = schema.get("items")
        if isinstance(items, list):  # draft-04..07 tuple form
            prefix, items = items, schema.get("additionalItems")
        start = 0
        if isinstance(prefix, list):
            for index, sub_schema in enumerate(prefix[: len(value)]):
                self._check(sub_schema, value[index], f"{path}[{index}]", issues, 0, limit=limit)
            start = len(prefix)
        if items is not None:
            for index in range(start, len(value)):
                self._check(items, value[index], f"{path}[{index}]", issues, 0, limit=limit)


def _json_form(value: object) -> object:
    """The value as a JSON consumer receives it.

    Datetimes, UUIDs, enums and the like arrive as strings, Decimals as numbers
    and sets as arrays, so checking the raw Python objects would reject values
    every caller accepts.
    """
    return json.loads(json.dumps(normalize_structured_data(value), default=_json_default, ensure_ascii=False))


def _json_default(value: object) -> object:
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, (set, frozenset)):
        return list(value)
    if isinstance(value, (bytes, bytearray)):
        return bytes(value).decode("utf-8", errors="replace")
    return str(value)


_UNRESOLVED = object()
# RFC 6901 array index: "0" or no leading zero, ASCII digits only.
_ARRAY_INDEX = re.compile(r"0|[1-9][0-9]*")


class _Enough(Exception):
    """Stop walking once enough issues are collected."""


class _CheckAborted(Exception):
    """The checker gave up (reference cycle, work budget); never a violation.

    Not a subclass of ``_Enough``: ``_matches`` must not read it as "no match".
    """


def _is_count(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def _is_number(value: object) -> bool:
    if isinstance(value, bool):
        return False
    if isinstance(value, int):
        return True  # never converted to float: huge bounds stay exact
    return isinstance(value, float) and math.isfinite(value)


def _is_type(value: object, name: object) -> bool:
    if name == "null":
        return value is None
    if name == "boolean":
        return isinstance(value, bool)
    if name == "integer":
        if isinstance(value, bool):
            return False
        return isinstance(value, int) or (isinstance(value, float) and value.is_integer())
    if name == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if name == "string":
        return isinstance(value, str)
    if name == "object":
        return isinstance(value, Mapping)
    if name == "array":
        return isinstance(value, list)
    # Unknown type names are not ours to reject.
    return True


def _json_equal(left: object, right: object) -> bool:
    """Equality as JSON sees it: ``True`` is not ``1``."""
    if isinstance(left, bool) or isinstance(right, bool):
        return isinstance(left, bool) and isinstance(right, bool) and left is right
    if isinstance(left, Mapping) and isinstance(right, Mapping):
        return left.keys() == right.keys() and all(_json_equal(left[k], right[k]) for k in left)
    if isinstance(left, list) and isinstance(right, list):
        return len(left) == len(right) and all(_json_equal(a, b) for a, b in zip(left, right))
    return left == right


__all__ = [
    "MAX_REPORTED_ISSUES",
    "OutputCheck",
    "OutputIssue",
    "SUPPORTED_KEYWORDS",
    "CHECK_ERROR_PREFIX",
    "build_output_check",
    "is_violation",
    "format_issues",
    "output_payload",
]
