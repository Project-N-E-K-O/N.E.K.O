"""``output_schema`` / ``strict_output``: declaration, inference and the runtime check.

The check lives in one place, ``EntryExecutor.invoke``, after an ``Ok`` is
unwrapped. By default a mismatch is a warning and the result is returned as
it is; ``strict_output=True`` turns it into a failure with a stable code.
Logs and errors carry field paths and error types only, never returned data.
"""

from __future__ import annotations

import inspect
import logging
from contextlib import nullcontext
from types import SimpleNamespace

import pytest
from pydantic import BaseModel, ConfigDict, model_validator

from plugin._types.exceptions import (
    OUTPUT_SCHEMA_VIOLATION_CODE,
    PluginExecutionError,
    PluginOutputSchemaError,
)
from plugin.core.entry_executor import EntryExecutor, execute_entry_command
from plugin.sdk.plugin import decorators as plugin_decorators
from plugin.sdk.shared.constants import EVENT_META_ATTR
from plugin.sdk.shared.core import output_contract
from plugin.sdk.shared.core.decorators import neko_plugin
from plugin.sdk.shared.core.events import EventMeta
from plugin.sdk.shared.core.output_contract import build_output_check, output_payload
from plugin.sdk.shared.models import Err, Ok

SECRET = "s3cret-user-text"
SECRET_KEY = "private-key-name"


class Weather(BaseModel):
    city: str
    temperature: int


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str


class Scores(BaseModel):
    scores: dict[str, int]


EXPLICIT = {
    "type": "object",
    "properties": {"count": {"type": "integer"}, "tags": {"type": "array", "items": {"type": "string"}}},
    "required": ["count"],
    "additionalProperties": False,
}


def _executor(entries, metadata):
    return EntryExecutor(
        plugin_id="demo",
        context=SimpleNamespace(
            _handler_scope=lambda _: nullcontext(),
            _run_scope=lambda _: nullcontext(),
            _lanlan_scope=lambda _: nullcontext(),
        ),
        instance=SimpleNamespace(),
        entries=entries,
        metadata=metadata,
        events={},
        default_timeout=5,
        refresh=lambda: None,
        should_persist=lambda _: False,
        persist=None,
        logger=logging.getLogger("test.output_contract"),
    )


def _meta_of(fn) -> EventMeta:
    return getattr(fn, EVENT_META_ATTR)


def _runtime_for(result, **decorator_kwargs):
    @plugin_decorators.plugin_entry(id="entry", **decorator_kwargs)
    async def entry():
        return result

    return _executor({"entry": entry}, {"entry": _meta_of(entry)})


def _runtime_for_model(result, *, strict: bool = False):
    @plugin_decorators.plugin_entry(id="entry", strict_output=strict)
    async def entry() -> Weather:
        return result  # type: ignore[return-value]

    return _executor({"entry": entry}, {"entry": _meta_of(entry)})


# ---------------------------------------------------------------------------
# Declaration and inference
# ---------------------------------------------------------------------------


def test_nothing_declared_means_no_contract():
    @plugin_decorators.plugin_entry(id="plain")
    async def plain() -> dict:
        return {}

    meta = _meta_of(plain)
    assert meta.output_schema is None
    assert meta.output_model is None
    assert meta.strict_output is False


def test_pydantic_return_annotation_infers_output_schema():
    @plugin_decorators.plugin_entry(id="weather")
    async def weather() -> Weather:
        return Weather(city="x", temperature=1)

    meta = _meta_of(weather)
    assert meta.output_model is Weather
    assert meta.output_schema == Weather.model_json_schema()


def test_optional_model_annotation_is_inferred_like_llm_result():
    @plugin_decorators.plugin_entry(id="weather")
    async def weather() -> Weather | None:
        return None

    assert _meta_of(weather).output_model is Weather


def test_forward_reference_is_resolved_against_the_owner_class():
    @neko_plugin
    class Demo:
        class Reply(BaseModel):
            ok: bool

        @plugin_decorators.plugin_entry(id="forward")
        async def forward(self) -> "Reply":
            return self.Reply(ok=True)

    meta = _meta_of(Demo.forward)
    assert meta.output_model is Demo.Reply
    assert meta.output_schema == Demo.Reply.model_json_schema()


def test_explicit_output_schema_wins_over_annotation():
    @plugin_decorators.plugin_entry(id="explicit", output_schema=EXPLICIT)
    async def explicit() -> Weather:
        return Weather(city="x", temperature=1)

    meta = _meta_of(explicit)
    assert meta.output_schema == EXPLICIT
    assert meta.output_schema is not EXPLICIT
    assert meta.output_model is None


def test_output_contract_is_independent_of_llm_result_fields():
    @plugin_decorators.plugin_entry(id="split", llm_result_fields=["city"])
    async def split() -> Weather:
        return Weather(city="x", temperature=1)

    meta = _meta_of(split)
    assert meta.llm_result_fields == ["city"]
    assert meta.output_model is Weather

    @plugin_decorators.plugin_entry(id="explicit_llm", llm_result_model=Strict)
    async def explicit_llm() -> Weather:
        return Weather(city="x", temperature=1)

    meta = _meta_of(explicit_llm)
    assert meta.llm_result_model is Strict
    assert meta.output_model is Weather


def test_output_schema_may_combine_with_any_llm_result_option():
    # Not part of the llm_result_* mutual exclusion group.
    for kwargs in ({"llm_result_fields": ["a"]}, {"llm_result_model": Weather}, {"fields": Weather}):
        plugin_decorators.plugin_entry(id="x", output_schema={"type": "object"}, **kwargs)


def test_output_schema_must_be_a_mapping():
    with pytest.raises(ValueError, match="output_schema"):
        plugin_decorators.plugin_entry(id="bad", output_schema="object")  # type: ignore[arg-type]


def test_both_plugin_facing_entry_decorators_have_the_same_signature():
    function_params = dict(inspect.signature(plugin_decorators.plugin_entry).parameters)
    function_params.pop("_localns")
    method_params = dict(inspect.signature(plugin_decorators._PluginDecorators.entry).parameters)
    assert list(function_params) == list(method_params)
    for name, param in function_params.items():
        assert (param.annotation, param.default) == (method_params[name].annotation, method_params[name].default), name


def test_plugin_entry_method_facade_forwards_output_contract():
    @plugin_decorators.plugin.entry(id="via_facade", output_schema=EXPLICIT, strict_output=True)
    async def via_facade():
        return {}

    meta = _meta_of(via_facade)
    assert meta.output_schema == EXPLICIT
    assert meta.strict_output is True


# ---------------------------------------------------------------------------
# Runtime: default warns, result unchanged
# ---------------------------------------------------------------------------


async def test_matching_result_passes_without_warning(caplog):
    runtime = _runtime_for({"count": 2, "tags": ["a"]}, output_schema=EXPLICIT)
    with caplog.at_level(logging.WARNING, logger="test.output_contract"):
        assert await runtime.invoke("plugin_entry", "entry", {}) == {"count": 2, "tags": ["a"]}
    assert "output_schema" not in caplog.text


async def test_mismatch_warns_and_returns_the_result_unchanged(caplog):
    returned = {"count": "two", "tags": [1]}
    runtime = _runtime_for(returned, output_schema=EXPLICIT)
    with caplog.at_level(logging.WARNING, logger="test.output_contract"):
        result = await runtime.invoke("plugin_entry", "entry", {})
    assert result is returned
    assert "entry=plugin_entry.entry" in caplog.text
    assert "strict=False" in caplog.text
    assert "$.count (type)" in caplog.text
    assert "$.tags[0] (type)" in caplog.text


async def test_model_annotation_warns_on_mismatch(caplog):
    runtime = _runtime_for_model({"city": "x"})
    with caplog.at_level(logging.WARNING, logger="test.output_contract"):
        assert await runtime.invoke("plugin_entry", "entry", {}) == {"city": "x"}
    assert "$.temperature (missing)" in caplog.text


async def test_model_instance_and_matching_dict_pass(caplog):
    with caplog.at_level(logging.WARNING, logger="test.output_contract"):
        instance = Weather(city="x", temperature=3)
        assert await _runtime_for_model(instance).invoke("plugin_entry", "entry", {}) is instance
        assert await _runtime_for_model({"city": "x", "temperature": 3}).invoke("plugin_entry", "entry", {})
    assert "output_schema" not in caplog.text


async def test_entry_without_contract_is_not_checked(monkeypatch):
    calls = []
    monkeypatch.setattr(output_contract, "_JsonSchemaCheck", lambda *a: calls.append(a))
    runtime = _runtime_for({"anything": object()})
    await runtime.invoke("plugin_entry", "entry", {})
    assert calls == []


# ---------------------------------------------------------------------------
# Runtime: strict_output
# ---------------------------------------------------------------------------


async def test_strict_mismatch_fails_with_output_schema_error():
    runtime = _runtime_for({"tags": []}, output_schema=EXPLICIT, strict_output=True)
    with pytest.raises(PluginOutputSchemaError) as caught:
        await runtime.invoke("plugin_entry", "entry", {})
    assert isinstance(caught.value, PluginExecutionError)
    assert caught.value.code == OUTPUT_SCHEMA_VIOLATION_CODE
    assert caught.value.issues == [{"path": "$.count", "type": "required"}]


async def test_strict_match_passes():
    runtime = _runtime_for({"count": 1}, output_schema=EXPLICIT, strict_output=True)
    assert await runtime.invoke("plugin_entry", "entry", {}) == {"count": 1}


async def test_strict_failure_reply_carries_code_and_issues():
    runtime = _runtime_for({"count": 1.5}, output_schema=EXPLICIT, strict_output=True)
    reply = await execute_entry_command(runtime, {"req_id": "r1", "entry_id": "entry", "args": {}})
    assert reply["success"] is False
    assert reply["error"]["code"] == OUTPUT_SCHEMA_VIOLATION_CODE
    assert reply["error"]["details"]["issues"] == [{"path": "$.count", "type": "type"}]
    assert reply["error"]["details"]["event_id"] == "entry"


async def test_strict_without_any_contract_changes_nothing():
    runtime = _runtime_for({"anything": 1}, strict_output=True)
    assert await runtime.invoke("plugin_entry", "entry", {}) == {"anything": 1}


# ---------------------------------------------------------------------------
# Ok / Err / bare / finish envelope
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("wrap", [lambda v: v, Ok], ids=["bare", "ok"])
async def test_ok_and_bare_results_are_checked_alike(wrap):
    good = _runtime_for(wrap({"count": 1}), output_schema=EXPLICIT, strict_output=True)
    assert await good.invoke("plugin_entry", "entry", {}) == {"count": 1}
    bad = _runtime_for(wrap({"count": "x"}), output_schema=EXPLICIT, strict_output=True)
    with pytest.raises(PluginOutputSchemaError):
        await bad.invoke("plugin_entry", "entry", {})


async def test_err_result_keeps_its_own_failure_and_is_not_checked(caplog):
    runtime = _runtime_for(Err(ValueError("plugin said no")), output_schema=EXPLICIT, strict_output=True)
    with caplog.at_level(logging.WARNING, logger="test.output_contract"):
        with pytest.raises(PluginExecutionError, match="plugin said no") as caught:
            await runtime.invoke("plugin_entry", "entry", {})
    assert not isinstance(caught.value, PluginOutputSchemaError)
    assert "output_schema" not in caplog.text


@pytest.mark.parametrize("wrap", [lambda v: v, Ok], ids=["bare", "ok"])
async def test_finish_envelope_is_checked_on_its_data(wrap):
    from plugin.sdk.shared.core.finish import build_finish_envelope

    good = build_finish_envelope(data={"count": 1}, delivery="silent")
    runtime = _runtime_for(wrap(good), output_schema=EXPLICIT, strict_output=True)
    assert await runtime.invoke("plugin_entry", "entry", {}) == good

    bad = build_finish_envelope(data={"tags": []}, delivery="silent")
    runtime = _runtime_for(wrap(bad), output_schema=EXPLICIT, strict_output=True)
    with pytest.raises(PluginOutputSchemaError):
        await runtime.invoke("plugin_entry", "entry", {})


def test_plain_dict_with_some_envelope_keys_is_not_unwrapped():
    value = {"success": True, "data": {"x": 1}}
    assert output_payload(value) is value


# ---------------------------------------------------------------------------
# The check is built once per entry
# ---------------------------------------------------------------------------


async def test_check_is_built_once_per_entry(monkeypatch):
    built = []
    real = output_contract.build_output_check

    def counting(meta):
        built.append(meta)
        return real(meta)

    monkeypatch.setattr("plugin.core.entry_executor.build_output_check", counting)
    runtime = _runtime_for({"count": 1}, output_schema=EXPLICIT)
    for _ in range(3):
        await runtime.invoke("plugin_entry", "entry", {})
    assert len(built) == 1


async def test_check_is_rebuilt_when_the_entry_meta_is_replaced(monkeypatch):
    built = []
    real = output_contract.build_output_check
    monkeypatch.setattr(
        "plugin.core.entry_executor.build_output_check", lambda meta: built.append(meta) or real(meta)
    )

    async def entry():
        return {"count": 1}

    metadata = {"entry": EventMeta(event_type="plugin_entry", id="entry", output_schema=EXPLICIT)}
    runtime = _executor({"entry": entry}, metadata)
    await runtime.invoke("plugin_entry", "entry", {})
    metadata["entry"] = EventMeta(event_type="plugin_entry", id="entry", output_schema={"type": "string"}, strict_output=True)
    with pytest.raises(PluginOutputSchemaError):
        await runtime.invoke("plugin_entry", "entry", {})
    assert len(built) == 2


# ---------------------------------------------------------------------------
# Privacy: neither logs nor errors quote returned data
# ---------------------------------------------------------------------------


def _privacy_cases():
    return [
        pytest.param({"output_schema": EXPLICIT}, {"count": SECRET, "tags": [SECRET]}, id="explicit-type"),
        pytest.param(
            {"output_schema": EXPLICIT},
            {"count": 1, SECRET_KEY: SECRET},
            id="explicit-extra-key",
        ),
        pytest.param(
            {"output_schema": {"type": "object", "properties": {"mode": {"enum": ["a", "b"]}}}},
            {"mode": SECRET},
            id="explicit-enum",
        ),
        pytest.param(
            {"output_schema": {"type": "object", "additionalProperties": {"type": "integer"}}},
            {SECRET_KEY: SECRET},
            id="explicit-additional-schema",
        ),
    ]


@pytest.mark.parametrize(("decl", "returned"), _privacy_cases())
async def test_logs_and_errors_never_contain_returned_values(caplog, decl, returned):
    runtime = _runtime_for(returned, strict_output=True, **decl)
    with caplog.at_level(logging.DEBUG):
        reply = await execute_entry_command(runtime, {"req_id": "r", "entry_id": "entry", "args": {}})
    assert reply["error"]["code"] == OUTPUT_SCHEMA_VIOLATION_CODE
    rendered = repr(reply) + caplog.text
    assert SECRET not in rendered
    assert SECRET_KEY not in rendered


@pytest.mark.parametrize(
    ("model", "returned"),
    [
        pytest.param(Weather, {"city": SECRET, "temperature": SECRET}, id="model-type"),
        pytest.param(Strict, {"name": "n", SECRET_KEY: SECRET}, id="model-extra-forbid"),
        pytest.param(Scores, {"scores": {SECRET_KEY: SECRET}}, id="model-dict-key"),
    ],
)
async def test_model_checks_never_quote_returned_values(caplog, model, returned):
    meta = EventMeta(
        event_type="plugin_entry",
        id="entry",
        output_schema=model.model_json_schema(),
        output_model=model,
        strict_output=True,
    )

    async def entry():
        return returned

    runtime = _executor({"entry": entry}, {"entry": meta})
    with caplog.at_level(logging.DEBUG):
        reply = await execute_entry_command(runtime, {"req_id": "r", "entry_id": "entry", "args": {}})
    assert reply["error"]["code"] == OUTPUT_SCHEMA_VIOLATION_CODE
    rendered = repr(reply) + caplog.text
    assert SECRET not in rendered
    assert SECRET_KEY not in rendered
    assert reply["error"]["details"]["issues"]


# ---------------------------------------------------------------------------
# JSON Schema subset
# ---------------------------------------------------------------------------


def _issues(schema, value):
    check = build_output_check(SimpleNamespace(output_schema=schema, output_model=None))
    assert check is not None
    return [(issue.path, issue.type) for issue in check(value)]


@pytest.mark.parametrize(
    ("schema", "value", "expected"),
    [
        ({"type": "integer"}, True, [("$", "type")]),
        ({"type": "integer"}, 2.0, []),
        ({"type": "number"}, 1, []),
        ({"type": ["string", "null"]}, None, []),
        ({"type": "array", "items": {"type": "string"}}, ("a", 1), [("$[1]", "type")]),
        ({"enum": [1, 2]}, True, [("$", "enum")]),
        ({"const": False}, 0, [("$", "const")]),
        ({"type": "string", "minLength": 2, "pattern": "^a"}, "b", [("$", "minLength"), ("$", "pattern")]),
        ({"type": "number", "minimum": 0, "exclusiveMaximum": 10}, 10, [("$", "exclusiveMaximum")]),
        ({"anyOf": [{"type": "string"}, {"type": "integer"}]}, 1.5, [("$", "anyOf")]),
        ({"oneOf": [{"type": "number"}, {"type": "integer"}]}, 1, [("$", "oneOf")]),
        ({"not": {"type": "null"}}, None, [("$", "not")]),
        (
            {"$defs": {"Item": {"type": "object", "required": ["id"]}}, "type": "array", "items": {"$ref": "#/$defs/Item"}},
            [{"id": 1}, {}],
            [("$[1].id", "required")],
        ),
        ({"type": "object", "properties": {"a": {"type": "string"}}, "x-unknown": 1}, {"a": "b"}, []),
        ({"$ref": "#"}, 1, [("$", "ref_depth")]),
    ],
)
def test_json_schema_subset(schema, value, expected):
    assert _issues(schema, value) == expected


def test_json_schema_subset_reports_a_bounded_number_of_issues():
    schema = {"type": "array", "items": {"type": "string"}}
    assert len(_issues(schema, list(range(500)))) == output_contract.MAX_REPORTED_ISSUES + 1


def test_pydantic_check_is_used_for_inferred_models():
    check = build_output_check(SimpleNamespace(output_schema=Weather.model_json_schema(), output_model=Weather))
    assert check is not None
    # Lax mode, as the model itself would accept it.
    assert check({"city": "x", "temperature": "3"}) == []
    assert [(i.path, i.type) for i in check({"city": "x"})] == [("$.temperature", "missing")]


# ---------------------------------------------------------------------------
# No false positives in strict mode
# ---------------------------------------------------------------------------


class Inner(BaseModel):
    v: int


class Outer(BaseModel):
    inner: Inner


async def test_optional_model_annotation_accepts_none_in_strict_mode():
    @plugin_decorators.plugin_entry(id="entry", strict_output=True)
    async def entry() -> Outer | None:
        return None

    meta = _meta_of(entry)
    # Nullable published schema, with $defs kept at the root so refs resolve.
    assert meta.output_schema["anyOf"][1] == {"type": "null"}
    assert "Inner" in meta.output_schema["$defs"]
    runtime = _executor({"entry": entry}, {"entry": meta})
    assert await runtime.invoke("plugin_entry", "entry", {}) is None
    check = build_output_check(meta)
    assert check is not None
    assert [(i.path, i.type) for i in check({"inner": {"v": "x"}})] == [("$.inner.v", "int_parsing")]


async def test_plain_model_annotation_still_rejects_none():
    runtime = _runtime_for_model(None, strict=True)
    with pytest.raises(PluginOutputSchemaError):
        await runtime.invoke("plugin_entry", "entry", {})


def test_explicit_schema_checks_values_in_their_json_form():
    import datetime
    import enum
    import uuid
    from decimal import Decimal

    class Color(enum.Enum):
        RED = "red"

    schema = {
        "type": "object",
        "properties": {
            "when": {"type": "string"},
            "id": {"type": "string"},
            "color": {"type": "string"},
            "price": {"type": "number"},
            "tags": {"type": "array", "items": {"type": "string"}},
            "raw": {"type": "string"},
        },
    }
    value = {
        "when": datetime.datetime(2026, 1, 1),
        "id": uuid.uuid4(),
        "color": Color.RED,
        "price": Decimal("1.5"),
        "tags": {"a"},
        "raw": b"x",
    }
    assert _issues(schema, value) == []


def test_recursive_schema_accepts_deep_data():
    schema = {"type": "object", "properties": {"kids": {"type": "array", "items": {"$ref": "#"}}}}
    tree: dict[str, object] = {"kids": []}
    for _ in range(60):
        tree = {"kids": [tree]}
    assert _issues(schema, tree) == []
    bad: dict[str, object] = {"kids": ["leaf"]}
    for _ in range(40):
        bad = {"kids": [bad]}
    assert [kind for _path, kind in _issues(schema, bad)] == ["type"]


async def test_checker_failure_is_logged_but_not_a_strict_failure(caplog):
    deep: list[object] = []
    for _ in range(5000):
        deep = [deep]
    runtime = _runtime_for(deep, output_schema={"type": "array", "items": {"$ref": "#"}}, strict_output=True)
    with caplog.at_level(logging.WARNING, logger="test.output_contract"):
        assert await runtime.invoke("plugin_entry", "entry", {}) is deep
    assert "check_error:" in caplog.text


# ---------------------------------------------------------------------------
# Review round 1: the check never touches the returned value; data keys stay out
# ---------------------------------------------------------------------------


class Counted(BaseModel):
    n: int = 0

    @model_validator(mode="after")
    def bump(self):
        self.n += 1
        return self


class Mutating(BaseModel):
    items: list[int]

    @model_validator(mode="before")
    @classmethod
    def eat(cls, data):
        if isinstance(data, dict):
            data["items"].append(99)
        return data


async def test_returned_model_instance_is_not_revalidated():
    returned = Counted()
    assert returned.n == 1
    meta = EventMeta(event_type="plugin_entry", id="entry", output_schema=Counted.model_json_schema(), output_model=Counted)

    async def entry():
        return returned

    runtime = _executor({"entry": entry}, {"entry": meta})
    assert await runtime.invoke("plugin_entry", "entry", {}) is returned
    assert returned.n == 1


async def test_validators_never_mutate_the_returned_data():
    returned = {"items": [1]}
    meta = EventMeta(event_type="plugin_entry", id="entry", output_schema=Mutating.model_json_schema(), output_model=Mutating)

    async def entry():
        return returned

    runtime = _executor({"entry": entry}, {"entry": meta})
    assert await runtime.invoke("plugin_entry", "entry", {}) == {"items": [1]}


class IntKeys(BaseModel):
    scores: dict[int, int]
    rows: list[int]


def test_integer_mapping_keys_are_not_put_in_paths():
    check = build_output_check(SimpleNamespace(output_schema=IntKeys.model_json_schema(), output_model=IntKeys))
    issues = [(i.path, i.type) for i in check({"scores": {123456789: "x"}, "rows": [1, "y"]})]
    assert ("$.scores.*", "int_parsing") in issues
    assert ("$.rows[1]", "int_parsing") in issues
    assert all("123456789" not in path for path, _kind in issues)


class Exploding(BaseModel):
    n: int

    @model_validator(mode="after")
    def boom(self):
        raise TypeError("not a validation error")


async def test_a_crashing_custom_validator_warns_but_does_not_fail_strict(caplog):
    meta = EventMeta(
        event_type="plugin_entry", id="entry",
        output_schema=Exploding.model_json_schema(), output_model=Exploding, strict_output=True,
    )

    async def entry():
        return {"n": 1}

    runtime = _executor({"entry": entry}, {"entry": meta})
    with caplog.at_level(logging.WARNING, logger="test.output_contract"):
        assert await runtime.invoke("plugin_entry", "entry", {}) == {"n": 1}
    assert "check_error:TypeError" in caplog.text


@pytest.mark.parametrize(
    ("schema", "value", "expected"),
    [
        ({"$defs": {"deny": False}, "$ref": "#/$defs/deny"}, 1, [("$", "false")]),
        ({"$defs": {"allow": True}, "$ref": "#/$defs/allow"}, 1, []),
        ({"$ref": "#/$defs/missing"}, 1, []),
        ({"$ref": "https://example.com/schema.json"}, 1, []),
        ({"type": "object", "patternProperties": {}, "additionalProperties": False}, {"unexpected": 1}, [("$.*", "additionalProperties")]),
        (
            {"type": "object", "patternProperties": {"^x_": {"type": "integer"}}, "additionalProperties": False},
            {"x_a": 1, "x_b": "no", "y": 1},
            [("$.*", "type"), ("$.*", "additionalProperties")],
        ),
    ],
)
def test_review_round_one_schema_cases(schema, value, expected):
    assert _issues(schema, value) == expected
