"""``output_schema`` / ``strict_output``: declaration, inference and the runtime check.

The check lives in ``EntryExecutor.invoke``: after the handler returns and
before the automatic state snapshot. By default a mismatch is a warning and
the result is returned as it is; ``strict_output=True`` fails the call with a
stable code. Logs and errors carry field paths and error types only.
"""

from __future__ import annotations

import asyncio
import datetime
import enum
import inspect
import logging
from contextlib import nullcontext
from decimal import Decimal
from types import SimpleNamespace

import pytest
from pydantic import BaseModel, ConfigDict, Field, HttpUrl, field_validator

from plugin._types.exceptions import (
    OUTPUT_SCHEMA_VIOLATION_CODE,
    PluginExecutionError,
    PluginOutputSchemaError,
)
from plugin.core import entry_executor as entry_executor_module
from plugin.core.entry_executor import EntryExecutor, execute_entry_command
from plugin.sdk.plugin import decorators as plugin_decorators
from plugin.sdk.shared.constants import EVENT_META_ATTR
from plugin.sdk.shared.core.decorators import neko_plugin
from plugin.sdk.shared.core.events import EventMeta
from plugin.sdk.shared.core.finish import build_finish_envelope
from plugin.sdk.shared.core.output_contract import build_output_check, output_payload
from plugin.sdk.shared.models import Err, Ok
from tests.fake_clock import patch_module_clock

SECRET = "s3cret-user-text"
SECRET_KEY = "private-key-name"


class Color(str, enum.Enum):
    RED = "red"
    BLUE = "blue"


class Address(BaseModel):
    city: str
    zip_code: str = Field(alias="zipCode")


class Profile(BaseModel):
    name: str
    homepage: HttpUrl
    joined: datetime.datetime
    balance: Decimal
    color: Color
    nickname: str | None = None
    address: Address
    history: list[Address] = []

    @field_validator("name")
    @classmethod
    def not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("blank")
        return value


def _profile(**overrides: object) -> Profile:
    values: dict[str, object] = {
        "name": "Ann",
        "homepage": "https://example.com",
        "joined": datetime.datetime(2026, 1, 1, 12, 0),
        "balance": Decimal("1.50"),
        "color": Color.RED,
        "address": {"city": "Kyoto", "zipCode": "600"},
        "history": [{"city": "Osaka", "zipCode": "530"}],
    }
    values.update(overrides)
    return Profile.model_validate(values)


EXPLICIT = {
    "type": "object",
    "properties": {"count": {"type": "integer", "minimum": 0}, "tags": {"type": "array", "items": {"type": "string"}}},
    "required": ["count"],
    "additionalProperties": False,
}


def _executor(entries, metadata, *, persisted=None):
    async def persist(_reason):
        if persisted is not None:
            persisted.append(_reason)

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
        should_persist=lambda _: persisted is not None,
        persist=persist,
        logger=logging.getLogger("test.output_contract"),
    )


def _meta_of(fn) -> EventMeta:
    return getattr(fn, EVENT_META_ATTR)


def _runtime_for(result, *, persisted=None, **decorator_kwargs):
    @plugin_decorators.plugin_entry(id="entry", **decorator_kwargs)
    async def entry():
        return result

    return _executor({"entry": entry}, {"entry": _meta_of(entry)}, persisted=persisted)


def _profile_runtime(result, *, strict=True, persisted=None):
    @plugin_decorators.plugin_entry(id="entry", strict_output=strict)
    async def entry() -> Profile:
        return result  # type: ignore[return-value]

    return _executor({"entry": entry}, {"entry": _meta_of(entry)}, persisted=persisted)


def _issues(check, value):
    return [(issue.path, issue.type) for issue in check(value)]


# ---------------------------------------------------------------------------
# Declaration and inference
# ---------------------------------------------------------------------------


def test_nothing_declared_means_no_contract():
    @plugin_decorators.plugin_entry(id="plain")
    async def plain() -> dict:
        return {}

    meta = _meta_of(plain)
    assert (meta.output_schema, meta.output_model, meta.strict_output) == (None, None, False)


def test_pydantic_return_annotation_infers_the_contract():
    @plugin_decorators.plugin_entry(id="profile")
    async def profile() -> Profile:
        return _profile()

    meta = _meta_of(profile)
    assert meta.output_model is Profile
    # Validation mode, keys as finish()/model_dump() emit them (field names).
    assert meta.output_schema == Profile.model_json_schema(by_alias=False)


def test_optional_model_annotation_publishes_a_nullable_contract():
    @plugin_decorators.plugin_entry(id="maybe")
    async def maybe() -> Profile | None:
        return None

    meta = _meta_of(maybe)
    assert meta.output_model is Profile
    assert {"type": "null"} in meta.output_schema["anyOf"]
    assert "$defs" in meta.output_schema


def test_forward_reference_is_resolved_against_the_owner_class():
    class Demo:
        @plugin_decorators.plugin_entry(id="forward")
        async def forward(self) -> "Reply":
            return self.Reply(ok=True)

        class Reply(BaseModel):
            ok: bool

    assert _meta_of(Demo.forward).output_schema is None
    neko_plugin(Demo)
    assert _meta_of(Demo.forward).output_model is Demo.Reply


def test_explicit_output_schema_wins_and_is_detached():
    schema = {"type": "object", "properties": {"count": {"type": "integer"}}}

    @plugin_decorators.plugin_entry(id="explicit", output_schema=schema)
    async def explicit() -> Profile:
        return _profile()

    meta = _meta_of(explicit)
    assert meta.output_model is None
    schema["properties"]["count"]["type"] = "string"
    assert meta.output_schema["properties"]["count"]["type"] == "integer"


def test_output_contract_is_independent_of_llm_result_fields():
    @plugin_decorators.plugin_entry(id="split", llm_result_fields=["name"])
    async def split() -> Profile:
        return _profile()

    meta = _meta_of(split)
    assert meta.llm_result_fields == ["name"]
    assert meta.output_model is Profile
    # Not part of the llm_result_* mutual exclusion group.
    plugin_decorators.plugin_entry(id="x", output_schema={"type": "object"}, llm_result_model=Profile)


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


# ---------------------------------------------------------------------------
# Pydantic contracts: the model validates the output
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "returned",
    [
        pytest.param(_profile(), id="instance"),
        pytest.param(build_finish_envelope(data=_profile(), delivery="silent"), id="finish-data"),
        pytest.param(_profile().model_dump(), id="dict-by-name"),
        pytest.param(_profile().model_dump(by_alias=True), id="dict-by-alias"),
        pytest.param(_profile().model_dump(mode="json"), id="dict-json-mode"),
        pytest.param(Ok(_profile().model_dump()), id="ok-dict"),
    ],
)
async def test_mainstream_model_outputs_pass_strict(returned):
    runtime = _profile_runtime(returned)
    assert await runtime.invoke("plugin_entry", "entry", {}) is (returned.value if isinstance(returned, Ok) else returned)


@pytest.mark.parametrize(
    ("change", "expected"),
    [
        pytest.param({"homepage": "not a url"}, ("$.homepage", "url_parsing"), id="HttpUrl"),
        pytest.param({"name": "  "}, ("$.name", "value_error"), id="field-validator"),
        pytest.param({"color": "green"}, ("$.color", "enum"), id="Enum"),
        pytest.param({"balance": "abc"}, ("$.balance", "decimal_parsing"), id="Decimal"),
        pytest.param({"joined": "yesterday"}, ("$.joined", "datetime_from_date_parsing"), id="datetime"),
        pytest.param({"address": {"city": "Kyoto"}}, ("$.address.zipCode", "missing"), id="nested-model-alias"),
        pytest.param({"history": [{"city": "Osaka"}]}, ("$.history[0].zipCode", "missing"), id="list-of-models"),
    ],
)
async def test_model_validators_and_formats_apply_to_dict_output(change, expected):
    returned = {**_profile().model_dump(), **change}
    check = build_output_check(_meta_of(_profile_runtime(None)._entries["entry"]))
    assert _issues(check, returned) == [expected]


async def test_optional_model_accepts_none_and_plain_model_rejects_it():
    @plugin_decorators.plugin_entry(id="entry", strict_output=True)
    async def maybe() -> Profile | None:
        return None

    assert await _executor({"entry": maybe}, {"entry": _meta_of(maybe)}).invoke("plugin_entry", "entry", {}) is None
    with pytest.raises(PluginOutputSchemaError):
        await _profile_runtime(None).invoke("plugin_entry", "entry", {})


def test_serialize_by_alias_models_publish_and_accept_alias_keys():
    class Out(BaseModel):
        model_config = ConfigDict(serialize_by_alias=True)
        user_id: int = Field(alias="userId")

    @plugin_decorators.plugin_entry(id="entry")
    async def entry() -> Out:
        return Out(userId=1)

    meta = _meta_of(entry)
    assert set(meta.output_schema["properties"]) == {"userId"}
    check = build_output_check(meta)
    assert check(Out(userId=3).model_dump()) == [] == check({"user_id": 3})


# ---------------------------------------------------------------------------
# Explicit contracts: jsonschema
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ({"count": 1, "tags": ["a"]}, []),
        ({"count": 1, "tags": ("a",)}, []),
        ({"tags": []}, [("$", "required")]),
        ({"count": -1}, [("$.count", "minimum")]),
        ({"count": 1, "tags": [1]}, [("$.tags[0]", "type")]),
        ({"count": 1, SECRET_KEY: 1}, [("$", "additionalProperties")]),
    ],
)
def test_explicit_schema_is_checked_with_jsonschema(value, expected):
    check = build_output_check(SimpleNamespace(output_schema=EXPLICIT, output_model=None))
    assert _issues(check, value) == expected


def test_jsonschema_format_and_multiple_of_need_no_custom_code():
    check = build_output_check(SimpleNamespace(
        output_schema={"type": "object", "properties": {"n": {"type": "number", "multipleOf": 0.5}}},
        output_model=None,
    ))
    assert _issues(check, {"n": 1.25}) == [("$.n", "multipleOf")]


def test_remote_refs_are_never_fetched(monkeypatch):
    import urllib.request

    fetched: list[object] = []
    monkeypatch.setattr(urllib.request, "urlopen", lambda *args, **kwargs: fetched.append(args) or None)
    check = build_output_check(SimpleNamespace(
        output_schema={"properties": {"a": {"$ref": "http://127.0.0.1:9/remote.json"}}},
        output_model=None,
    ))
    issues = check({"a": 1})
    assert fetched == []
    assert [issue.type.startswith("check_error:") for issue in issues] == [True]


def test_model_and_jsonschema_agree_on_the_published_schema():
    """Same schema and data: the model check and a JSON Schema check reach the same verdict."""
    model_check = build_output_check(SimpleNamespace(output_schema=Address.model_json_schema(), output_model=Address))
    schema_check = build_output_check(SimpleNamespace(output_schema=Address.model_json_schema(), output_model=None))
    cases = [
        {"city": "Kyoto", "zipCode": "600"},
        {"city": "Kyoto"},
        {"city": 1, "zipCode": "600"},
        {"zipCode": "600"},
    ]
    for value in cases:
        assert bool(model_check(value)) == bool(schema_check(value)), value
    # Known, explained difference: the model also accepts field names (by_name).
    assert model_check({"city": "Kyoto", "zip_code": "600"}) == []
    assert schema_check({"city": "Kyoto", "zip_code": "600"}) != []


# ---------------------------------------------------------------------------
# Runtime: warn, strict, Ok/Err/bare/finish, persistence, rate limit
# ---------------------------------------------------------------------------


async def test_mismatch_warns_and_returns_the_result_unchanged(caplog):
    returned = {"count": "two"}
    runtime = _runtime_for(returned, output_schema=EXPLICIT)
    with caplog.at_level(logging.WARNING, logger="test.output_contract"):
        assert await runtime.invoke("plugin_entry", "entry", {}) is returned
    assert "strict=False" in caplog.text and "$.count (type)" in caplog.text


async def test_strict_failure_reply_carries_code_and_issues():
    runtime = _runtime_for({"count": 1.5}, output_schema=EXPLICIT, strict_output=True)
    reply = await execute_entry_command(runtime, {"req_id": "r1", "entry_id": "entry", "args": {}})
    assert reply["success"] is False
    assert reply["error"]["code"] == OUTPUT_SCHEMA_VIOLATION_CODE
    assert reply["error"]["details"]["issues"] == [{"path": "$.count", "type": "type"}]


@pytest.mark.parametrize("wrap", [lambda v: v, Ok, lambda v: build_finish_envelope(data=v, delivery="silent")],
                         ids=["bare", "ok", "finish"])
async def test_ok_bare_and_finish_results_are_checked_alike(wrap):
    good = _runtime_for(wrap({"count": 1}), output_schema=EXPLICIT, strict_output=True)
    await good.invoke("plugin_entry", "entry", {})
    bad = _runtime_for(wrap({"count": "x"}), output_schema=EXPLICIT, strict_output=True)
    with pytest.raises(PluginOutputSchemaError):
        await bad.invoke("plugin_entry", "entry", {})


async def test_err_result_keeps_its_own_failure_and_is_not_checked():
    runtime = _runtime_for(Err(ValueError("plugin said no")), output_schema=EXPLICIT, strict_output=True)
    with pytest.raises(PluginExecutionError, match="plugin said no") as caught:
        await runtime.invoke("plugin_entry", "entry", {})
    assert not isinstance(caught.value, PluginOutputSchemaError)


async def test_strict_failure_skips_the_state_snapshot():
    persisted: list[str] = []
    with pytest.raises(PluginOutputSchemaError):
        await _runtime_for({"count": "x"}, output_schema=EXPLICIT, strict_output=True, persisted=persisted).invoke(
            "plugin_entry", "entry", {}
        )
    assert persisted == []
    await _runtime_for({"count": 1}, output_schema=EXPLICIT, strict_output=True, persisted=persisted).invoke(
        "plugin_entry", "entry", {}
    )
    assert persisted == ["auto"]


async def test_warnings_are_rate_limited_per_entry(caplog, monkeypatch):
    clock = [100.0]
    patch_module_clock(monkeypatch, entry_executor_module, monotonic=lambda: clock[0])
    runtime = _runtime_for({"count": "x"}, output_schema=EXPLICIT)
    with caplog.at_level(logging.WARNING, logger="test.output_contract"):
        for _ in range(5):
            await runtime.invoke("plugin_entry", "entry", {})
        assert caplog.text.count("does not match output_schema") == 1
        clock[0] += entry_executor_module._WARN_INTERVAL + 1
        await runtime.invoke("plugin_entry", "entry", {})
    assert caplog.text.count("does not match output_schema") == 2
    assert "suppressed=4" in caplog.text


async def test_rate_limit_is_per_kind_of_mismatch(caplog):
    values = iter([{"count": "x"}, {"tags": []}, {"count": "y"}, {"tags": []}])

    @plugin_decorators.plugin_entry(id="entry", output_schema=EXPLICIT)
    async def entry():
        return next(values)

    runtime = _executor({"entry": entry}, {"entry": _meta_of(entry)})
    with caplog.at_level(logging.WARNING, logger="test.output_contract"):
        for _ in range(4):
            await runtime.invoke("plugin_entry", "entry", {})
    # Two kinds of mismatch, alternating: each is logged once.
    assert caplog.text.count("does not match output_schema") == 2


async def test_checker_failure_never_fails_the_call(caplog):
    class Hostile(dict):
        def get(self, *args, **kwargs):
            raise RuntimeError("hostile mapping")

    returned = Hostile(success=True)
    runtime = _runtime_for(returned, output_schema=EXPLICIT, strict_output=True)
    with caplog.at_level(logging.WARNING, logger="test.output_contract"):
        assert await runtime.invoke("plugin_entry", "entry", {}) is returned
    assert "could not be checked" in caplog.text


async def test_contract_is_the_one_resolved_when_the_call_started():
    release = asyncio.Event()

    async def entry():
        await release.wait()
        return {"count": 1}

    metadata = {"entry": EventMeta(event_type="plugin_entry", id="entry", output_schema=EXPLICIT, strict_output=True)}
    runtime = _executor({"entry": entry}, metadata)
    call = asyncio.create_task(runtime.invoke("plugin_entry", "entry", {}))
    await asyncio.sleep(0)
    metadata["entry"] = EventMeta(event_type="plugin_entry", id="entry", output_schema={"type": "string"}, strict_output=True)
    release.set()
    assert await call == {"count": 1}


async def test_check_is_built_once_per_entry(monkeypatch):
    built = []
    real = entry_executor_module.build_output_check
    monkeypatch.setattr(entry_executor_module, "build_output_check", lambda meta: built.append(meta) or real(meta))
    runtime = _runtime_for({"count": 1}, output_schema=EXPLICIT)
    for _ in range(3):
        await runtime.invoke("plugin_entry", "entry", {})
    assert len(built) == 1


def test_plain_dict_with_some_envelope_keys_is_not_unwrapped():
    value = {"success": True, "data": {"x": 1}}
    assert output_payload(value) is value


# ---------------------------------------------------------------------------
# Privacy: neither logs nor errors quote returned data or data-derived keys
# ---------------------------------------------------------------------------


class Scores(BaseModel):
    scores: dict[str, int]
    ids: dict[int, int]


@pytest.mark.parametrize(
    ("decl", "returned"),
    [
        pytest.param({"output_schema": EXPLICIT}, {"count": SECRET, "tags": [SECRET]}, id="jsonschema-type"),
        pytest.param({"output_schema": EXPLICIT}, {"count": 1, SECRET_KEY: SECRET}, id="jsonschema-extra-key"),
        pytest.param(
            {"output_schema": {"type": "object", "additionalProperties": {"type": "integer"}}},
            {SECRET_KEY: SECRET},
            id="jsonschema-additional-schema",
        ),
        pytest.param(
            {"output_schema": {"type": "object", "properties": {"m": {"enum": ["a"]}}}}, {"m": SECRET}, id="jsonschema-enum"
        ),
    ],
)
async def test_jsonschema_issues_never_contain_returned_values(caplog, decl, returned):
    runtime = _runtime_for(returned, strict_output=True, **decl)
    with caplog.at_level(logging.DEBUG):
        reply = await execute_entry_command(runtime, {"req_id": "r", "entry_id": "entry", "args": {}})
    assert reply["error"]["code"] == OUTPUT_SCHEMA_VIOLATION_CODE
    rendered = repr(reply) + caplog.text
    assert SECRET not in rendered and SECRET_KEY not in rendered


async def test_model_issues_never_contain_returned_values_or_keys(caplog):
    @plugin_decorators.plugin_entry(id="entry", strict_output=True)
    async def entry() -> Scores:
        return {"scores": {SECRET_KEY: SECRET}, "ids": {123456789: SECRET}}  # type: ignore[return-value]

    runtime = _executor({"entry": entry}, {"entry": _meta_of(entry)})
    with caplog.at_level(logging.DEBUG):
        reply = await execute_entry_command(runtime, {"req_id": "r", "entry_id": "entry", "args": {}})
    rendered = repr(reply) + caplog.text
    assert SECRET not in rendered and SECRET_KEY not in rendered and "123456789" not in rendered
    assert {"path": "$.scores.*", "type": "int_parsing"} in reply["error"]["details"]["issues"]


def test_custom_error_types_are_replaced_by_a_fixed_code():
    from pydantic_core import PydanticCustomError

    class Custom(BaseModel):
        n: int

        @field_validator("n")
        @classmethod
        def leak(cls, value):
            raise PydanticCustomError(f"bad_{value}_{SECRET}", "nope")

    check = build_output_check(SimpleNamespace(output_schema=Custom.model_json_schema(), output_model=Custom))
    assert _issues(check, {"n": 7}) == [("$.n", "custom_error")]
