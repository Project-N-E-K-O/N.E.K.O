"""``output_schema`` / ``strict_output``: declaration, inference and the runtime check.

Symmetric with inputs: a Pydantic return annotation is validated at runtime
by building the model from the returned data (as ``params=Model`` does); an
explicit ``output_schema`` mapping is only published, like ``input_schema``.
The check runs in ``EntryExecutor.invoke`` before the automatic state
snapshot. Logs and errors carry field paths and error types only.
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
from typing import Annotated, Literal, Union

import pytest
from pydantic import BaseModel, Field, HttpUrl, RootModel, field_validator

from plugin._types.exceptions import (
    OUTPUT_SCHEMA_VIOLATION_CODE,
    PluginExecutionError,
    PluginOutputSchemaError,
)
from plugin.core import entry_executor as entry_executor_module
from plugin.core.entry_executor import EntryExecutor, execute_entry_command
from plugin.core.responses import fail, ok
from plugin.sdk.plugin import decorators as plugin_decorators
from plugin.sdk.shared.constants import EVENT_META_ATTR
from plugin.sdk.shared.core.decorators import neko_plugin
from plugin.sdk.shared.core.events import EventMeta
from plugin.sdk.shared.core.finish import build_finish_envelope
from plugin.sdk.shared.core.output_contract import build_output_check
from plugin.sdk.shared.models import Err, Ok
from tests.fake_clock import patch_module_clock

SECRET = "s3cret-user-text"
SECRET_KEY = "private-key-name"


class Color(str, enum.Enum):
    RED = "red"
    BLUE = "blue"


class Address(BaseModel):
    city: str
    zip_code: str


class Cat(BaseModel):
    kind: Literal["cat"]
    lives: int


class Dog(BaseModel):
    kind: Literal["dog"]
    good: bool


class Profile(BaseModel):
    name: str
    homepage: HttpUrl
    joined: datetime.datetime
    balance: Decimal
    color: Color
    nickname: str | None = None
    address: Address
    history: list[Address] = []
    point: tuple[int, int]
    pet: Annotated[Union[Cat, Dog], Field(discriminator="kind")]

    @field_validator("name")
    @classmethod
    def not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("blank")
        return value


class Tags(RootModel[list[str]]):
    pass


def _profile_data(**overrides: object) -> dict[str, object]:
    values: dict[str, object] = {
        "name": "Ann",
        "homepage": "https://example.com",
        "joined": datetime.datetime(2026, 1, 1, 12, 0),
        "balance": Decimal("1.50"),
        "color": Color.RED,
        "address": {"city": "Kyoto", "zip_code": "600"},
        "history": [{"city": "Osaka", "zip_code": "530"}],
        "point": (1, 2),
        "pet": {"kind": "cat", "lives": 9},
    }
    values.update(overrides)
    return values


EXPLICIT = {"type": "object", "properties": {"count": {"type": "integer"}}, "required": ["count"]}


def _executor(entries, metadata, *, persisted=None):
    async def persist(reason):
        if persisted is not None:
            persisted.append(reason)

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


def _profile_runtime(result, *, strict=True, persisted=None):
    @plugin_decorators.plugin_entry(id="entry", strict_output=strict)
    async def entry() -> Profile:
        return result  # type: ignore[return-value]

    return _executor({"entry": entry}, {"entry": _meta_of(entry)}, persisted=persisted)


def _profile_check():
    @plugin_decorators.plugin_entry(id="entry")
    async def entry() -> Profile:
        return Profile.model_validate(_profile_data())

    return build_output_check(_meta_of(entry))


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


def test_pydantic_return_annotation_infers_the_contract_like_params_do():
    @plugin_decorators.plugin_entry(id="profile")
    async def profile() -> Profile:
        return Profile.model_validate(_profile_data())

    meta = _meta_of(profile)
    assert meta.output_model is Profile
    # Published the way params=Model publishes input_schema.
    assert meta.output_schema == Profile.model_json_schema()


def test_optional_model_annotation_publishes_a_nullable_contract():
    @plugin_decorators.plugin_entry(id="maybe")
    async def maybe() -> Profile | None:
        return None

    meta = _meta_of(maybe)
    assert meta.output_model is Profile
    assert {"type": "null"} in meta.output_schema["anyOf"]


@pytest.mark.parametrize("annotation", [list[Profile], dict, int])
def test_only_model_and_optional_model_annotations_are_inferred(annotation):
    async def entry():
        return None

    entry.__annotations__["return"] = annotation
    decorated = plugin_decorators.plugin_entry(id="other")(entry)
    assert _meta_of(decorated).output_schema is None


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


def test_explicit_output_schema_wins_and_is_published_only():
    schema = {"type": "object", "properties": {"count": {"type": "integer"}}}

    @plugin_decorators.plugin_entry(id="explicit", output_schema=schema)
    async def explicit() -> Profile:
        return Profile.model_validate(_profile_data())

    meta = _meta_of(explicit)
    assert meta.output_model is None
    assert build_output_check(meta) is None
    schema["properties"]["count"]["type"] = "string"
    assert meta.output_schema["properties"]["count"]["type"] == "integer"


def test_output_contract_is_independent_of_llm_result_fields():
    @plugin_decorators.plugin_entry(id="split", llm_result_fields=["name"])
    async def split() -> Profile:
        return Profile.model_validate(_profile_data())

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
# Model contracts: the model validates returned data
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "returned",
    [
        pytest.param(Profile.model_validate(_profile_data()), id="instance"),
        pytest.param(build_finish_envelope(data=Profile.model_validate(_profile_data()), delivery="silent"), id="finish"),
        pytest.param(_profile_data(), id="dict"),
        pytest.param(Profile.model_validate(_profile_data()).model_dump(), id="model_dump"),
        pytest.param(Profile.model_validate(_profile_data()).model_dump(mode="json"), id="model_dump-json"),
        pytest.param(Ok(_profile_data()), id="ok-dict"),
        pytest.param(ok(data=_profile_data()), id="ok-envelope"),
    ],
)
async def test_mainstream_model_outputs_pass_strict(returned):
    runtime = _profile_runtime(returned)
    expected = returned.value if isinstance(returned, Ok) else returned
    assert await runtime.invoke("plugin_entry", "entry", {}) is expected


@pytest.mark.parametrize(
    ("change", "expected"),
    [
        pytest.param({"homepage": "not a url"}, ("$.homepage", "url_parsing"), id="HttpUrl"),
        pytest.param({"name": "  "}, ("$.name", "value_error"), id="field-validator"),
        pytest.param({"color": "green"}, ("$.color", "enum"), id="Enum"),
        pytest.param({"balance": "abc"}, ("$.balance", "decimal_parsing"), id="Decimal"),
        pytest.param({"joined": "yesterday"}, ("$.joined", "datetime_from_date_parsing"), id="datetime"),
        pytest.param({"address": {"city": "Kyoto"}}, ("$.address.zip_code", "missing"), id="nested-model"),
        pytest.param({"history": [{"city": "Osaka"}]}, ("$.history[0].zip_code", "missing"), id="list-of-models"),
        pytest.param({"point": (1,)}, ("$.point[1]", "missing"), id="tuple"),
        pytest.param({"pet": {"kind": "fish"}}, ("$.pet", "union_tag_invalid"), id="discriminated-union"),
    ],
)
def test_model_rules_apply_to_returned_dicts(change, expected):
    assert _issues(_profile_check(), _profile_data(**change)) == [expected]


def test_root_model_contract():
    @plugin_decorators.plugin_entry(id="tags")
    async def tags() -> Tags:
        return Tags(["a"])

    check = build_output_check(_meta_of(tags))
    assert check(["a", "b"]) == [] == check(Tags(["a"]))
    assert _issues(check, ["a", 1]) == [("$[1]", "string_type")]


async def test_optional_model_accepts_none_and_plain_model_rejects_it():
    @plugin_decorators.plugin_entry(id="entry", strict_output=True)
    async def maybe() -> Profile | None:
        return None

    assert await _executor({"entry": maybe}, {"entry": _meta_of(maybe)}).invoke("plugin_entry", "entry", {}) is None
    with pytest.raises(PluginOutputSchemaError):
        await _profile_runtime(None).invoke("plugin_entry", "entry", {})


async def test_explicit_schema_is_not_checked_at_runtime():
    @plugin_decorators.plugin_entry(id="entry", output_schema=EXPLICIT, strict_output=True)
    async def entry():
        return {"count": "not an int"}

    runtime = _executor({"entry": entry}, {"entry": _meta_of(entry)})
    assert await runtime.invoke("plugin_entry", "entry", {}) == {"count": "not an int"}


# ---------------------------------------------------------------------------
# Runtime: warn, strict, Ok/Err/envelopes, persistence, rate limit
# ---------------------------------------------------------------------------


async def test_mismatch_warns_and_returns_the_result_unchanged(caplog):
    returned = _profile_data(color="green")
    with caplog.at_level(logging.WARNING, logger="test.output_contract"):
        assert await _profile_runtime(returned, strict=False).invoke("plugin_entry", "entry", {}) is returned
    assert "strict=False" in caplog.text and "$.color (enum)" in caplog.text


async def test_strict_failure_reply_carries_code_and_issues():
    reply = await execute_entry_command(
        _profile_runtime(_profile_data(color="green")), {"req_id": "r1", "entry_id": "entry", "args": {}}
    )
    assert reply["success"] is False
    assert reply["error"]["code"] == OUTPUT_SCHEMA_VIOLATION_CODE
    assert reply["error"]["details"]["issues"] == [{"path": "$.color", "type": "enum"}]


async def test_err_and_fail_envelopes_keep_their_own_failure_and_are_not_checked():
    with pytest.raises(PluginExecutionError, match="plugin said no") as caught:
        await _profile_runtime(Err(ValueError("plugin said no"))).invoke("plugin_entry", "entry", {})
    assert not isinstance(caught.value, PluginOutputSchemaError)
    failed = fail("INTERNAL", "nope")
    assert await _profile_runtime(failed).invoke("plugin_entry", "entry", {}) is failed


async def test_strict_failure_skips_the_state_snapshot():
    persisted: list[str] = []
    with pytest.raises(PluginOutputSchemaError):
        await _profile_runtime(_profile_data(color="green"), persisted=persisted).invoke("plugin_entry", "entry", {})
    assert persisted == []
    await _profile_runtime(_profile_data(), persisted=persisted).invoke("plugin_entry", "entry", {})
    assert persisted == ["auto"]


async def test_warnings_are_rate_limited_per_entry_and_kind(caplog, monkeypatch):
    clock = [100.0]
    patch_module_clock(monkeypatch, entry_executor_module, monotonic=lambda: clock[0])
    values = iter([_profile_data(color="green"), _profile_data(name=" ")] * 3)

    @plugin_decorators.plugin_entry(id="entry")
    async def entry() -> Profile:
        return next(values)  # type: ignore[return-value]

    runtime = _executor({"entry": entry}, {"entry": _meta_of(entry)})
    with caplog.at_level(logging.WARNING, logger="test.output_contract"):
        for _ in range(5):
            await runtime.invoke("plugin_entry", "entry", {})
        # Two kinds of mismatch, alternating: each logged once.
        assert caplog.text.count("does not match output_schema") == 2
        clock[0] += entry_executor_module._WARN_INTERVAL + 1
        await runtime.invoke("plugin_entry", "entry", {})
    assert "suppressed=" in caplog.text


async def test_contract_is_the_one_resolved_when_the_call_started():
    release = asyncio.Event()

    async def entry():
        await release.wait()
        return {"city": "Kyoto", "zip_code": "600"}

    metadata = {"entry": EventMeta(event_type="plugin_entry", id="entry", output_model=Address, strict_output=True)}
    runtime = _executor({"entry": entry}, metadata)
    call = asyncio.create_task(runtime.invoke("plugin_entry", "entry", {}))
    await asyncio.sleep(0)
    metadata["entry"] = EventMeta(event_type="plugin_entry", id="entry", output_model=Cat, strict_output=True)
    release.set()
    assert await call == {"city": "Kyoto", "zip_code": "600"}


async def test_check_is_built_once_per_entry(monkeypatch):
    built = []
    real = entry_executor_module.build_output_check
    monkeypatch.setattr(entry_executor_module, "build_output_check", lambda meta: built.append(meta) or real(meta))
    runtime = _profile_runtime(_profile_data())
    for _ in range(3):
        await runtime.invoke("plugin_entry", "entry", {})
    assert len(built) == 1


async def test_a_crashing_validator_warns_but_never_fails_the_call(caplog):
    class Exploding(BaseModel):
        n: int

        @field_validator("n")
        @classmethod
        def boom(cls, value):
            raise TypeError("not a validation error")

    metadata = {"entry": EventMeta(event_type="plugin_entry", id="entry", output_model=Exploding, strict_output=True)}

    async def entry():
        return {"n": 1}

    with caplog.at_level(logging.WARNING, logger="test.output_contract"):
        assert await _executor({"entry": entry}, metadata).invoke("plugin_entry", "entry", {}) == {"n": 1}
    assert "check_error:TypeError" in caplog.text


# ---------------------------------------------------------------------------
# Privacy: neither logs nor errors quote returned data or data-derived keys
# ---------------------------------------------------------------------------


class Scores(BaseModel):
    scores: dict[str, int]
    ids: dict[int, int]
    name: str


async def test_issues_never_contain_returned_values_or_keys(caplog):
    metadata = {"entry": EventMeta(
        event_type="plugin_entry", id="entry", output_model=Scores,
        output_schema=Scores.model_json_schema(), strict_output=True,
    )}

    async def entry():
        return {"scores": {SECRET_KEY: SECRET}, "ids": {123456789: SECRET}, "name": 5}

    with caplog.at_level(logging.DEBUG):
        reply = await execute_entry_command(_executor({"entry": entry}, metadata), {"req_id": "r", "entry_id": "entry", "args": {}})
    rendered = repr(reply) + caplog.text
    assert SECRET not in rendered and SECRET_KEY not in rendered and "123456789" not in rendered
    issues = reply["error"]["details"]["issues"]
    assert {"path": "$.scores.*", "type": "int_parsing"} in issues
    assert {"path": "$.name", "type": "string_type"} in issues


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


class Status(BaseModel):
    success: bool
    error: str | None = None
    time: float
    data: int = 0


async def test_a_model_with_envelope_like_fields_is_not_mistaken_for_an_envelope():
    @plugin_decorators.plugin_entry(id="entry", strict_output=True)
    async def entry() -> Status:
        return {"success": True, "error": None, "time": 1.5, "data": 3}  # type: ignore[return-value]

    runtime = _executor({"entry": entry}, {"entry": _meta_of(entry)})
    assert await runtime.invoke("plugin_entry", "entry", {}) == {"success": True, "error": None, "time": 1.5, "data": 3}


class MaybeInt(RootModel[Union[int, None]]):
    @field_validator("root")
    @classmethod
    def not_none(cls, value):
        if value is None:
            raise ValueError("required")
        return value


def test_a_model_whose_own_schema_allows_null_still_validates_none():
    @plugin_decorators.plugin_entry(id="entry")
    async def entry() -> MaybeInt:
        return MaybeInt(1)

    check = build_output_check(_meta_of(entry))
    # The annotation has no "| None": None goes to the model, which rejects it.
    assert [issue.type for issue in check(None)] == ["value_error"]

    @plugin_decorators.plugin_entry(id="optional")
    async def optional() -> MaybeInt | None:
        return None

    assert build_output_check(_meta_of(optional))(None) == []
