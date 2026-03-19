from __future__ import annotations

import inspect
from typing import Annotated

import pytest
from pydantic import BaseModel

from plugin.sdk_v2.plugin import decorators as dec


def test_constants_and_exports() -> None:
    assert dec.PERSIST_ATTR == "_neko_persist"
    assert dec.EVENT_META_ATTR == "__neko_event_meta__"
    assert dec.HOOK_META_ATTR == "__neko_hook_meta__"

    for name in dec.__all__:
        assert hasattr(dec, name)
    assert not hasattr(dec, "CHECKPOINT_ATTR")
    assert not hasattr(dec, "EventDecoratorMeta")


def test_neko_plugin_sets_marker() -> None:
    cls = type("P", (), {})
    wrapped = dec.neko_plugin(cls)
    assert wrapped is cls
    assert cls.__neko_plugin__ is True


def test_on_event_validation_and_metadata_attach() -> None:
    with pytest.raises(ValueError, match="event_type must be non-empty"):
        dec.on_event(event_type="   ")

    @dec.on_event(event_type="evt", id="a1", name="Action 1", description="d", metadata={"m": 1})
    def fn() -> str:
        return "ready"

    meta = getattr(fn, dec.EVENT_META_ATTR)
    assert meta.event_type == "evt"
    assert meta.id == "a1"
    assert meta.name == "Action 1"
    assert meta.description == "d"
    assert meta.kind == "action"
    assert meta.persist is None
    assert meta.params is None
    assert meta.model_validate is True
    assert meta.metadata == {"m": 1}


def test_on_event_forwards_mode_seconds_and_extra(monkeypatch) -> None:
    sentinel = object()

    def fake_on_event(**kwargs: object):
        assert kwargs["mode"] == "interval"
        assert kwargs["seconds"] == 5
        assert kwargs["extra"] == {"source": "timer"}
        return sentinel

    monkeypatch.setattr(dec, "_on_event", fake_on_event)

    assert dec.on_event(
        event_type="evt",
        mode="interval",
        seconds=5,
        extra={"source": "timer"},
    ) is sentinel


def test_plugin_entry_defaults_and_persist_flags() -> None:
    @dec.plugin_entry(persist=True, params=dict, model_validate=False, timeout=3.0)
    def run() -> str:
        return "ready"

    meta = getattr(run, dec.EVENT_META_ATTR)
    assert meta.event_type == "plugin_entry"
    assert meta.id == "run"
    assert meta.name == "run"
    assert meta.params is dict
    assert meta.model_validate is False
    assert meta.timeout == 3.0
    assert getattr(run, dec.PERSIST_ATTR) is True

    @dec.plugin_entry(persist=False)
    def run2() -> str:
        return "ready"

    assert getattr(run2, dec.PERSIST_ATTR) is False


def test_plugin_entry_auto_infers_input_schema_from_signature() -> None:
    @dec.plugin_entry()
    def hello(name: str = "world", sleep_seconds: float = 0.6, enabled: bool | None = None, **kwargs) -> str:
        return "ready"

    meta = getattr(hello, dec.EVENT_META_ATTR)
    assert meta.input_schema == {
        "type": "object",
        "properties": {
            "name": {"type": "string", "default": "world"},
            "sleep_seconds": {"type": "number", "default": 0.6},
            "enabled": {"type": ["boolean", "null"], "default": None},
        },
    }


def test_plugin_entry_supports_annotated_and_params_schema() -> None:
    class _ParamsModel:
        @staticmethod
        def model_json_schema() -> dict[str, object]:
            return {
                "type": "object",
                "properties": {"query": {"type": "string"}},
                "required": ["query"],
            }

    @dec.plugin_entry(params=_ParamsModel)
    def by_model() -> str:
        return "ready"

    by_model_meta = getattr(by_model, dec.EVENT_META_ATTR)
    assert by_model_meta.params is _ParamsModel
    assert by_model_meta.input_schema == {
        "type": "object",
        "properties": {"query": {"type": "string"}},
        "required": ["query"],
    }

    @dec.on_event(event_type="evt")
    def annotated(value: Annotated[int, "counter value"], tag: str) -> None:
        return None

    annotated_meta = getattr(annotated, dec.EVENT_META_ATTR)
    assert annotated_meta.input_schema == {
        "type": "object",
        "properties": {
            "value": {"type": "integer", "description": "counter value"},
            "tag": {"type": "string"},
        },
        "required": ["value", "tag"],
    }


def test_plugin_entry_supports_llm_result_fields_and_model_alias() -> None:
    @dec.plugin_entry(llm_result_fields=["title", "summary", "title", " items "])
    def by_fields() -> str:
        return "ready"

    by_fields_meta = getattr(by_fields, dec.EVENT_META_ATTR)
    assert by_fields_meta.llm_result_fields == ["title", "summary", "items"]
    assert by_fields_meta.llm_result_schema == {
        "type": "object",
        "properties": {
            "title": {},
            "summary": {},
            "items": {},
        },
        "required": ["title", "summary", "items"],
    }

    class _ResultModel(BaseModel):
        title: str
        summary: str

    @dec.plugin_entry(fields=_ResultModel)
    def by_model() -> str:
        return "ready"

    by_model_meta = getattr(by_model, dec.EVENT_META_ATTR)
    assert by_model_meta.llm_result_model is _ResultModel
    assert by_model_meta.llm_result_fields == ["title", "summary"]
    assert by_model_meta.llm_result_schema is not None
    assert by_model_meta.llm_result_schema["type"] == "object"
    assert by_model_meta.llm_result_schema["properties"]["title"]["type"] == "string"


def test_plugin_entry_auto_infers_params_and_result_models_from_annotations() -> None:
    class _ParamsModel(BaseModel):
        query: str
        count: int = 5

    class _ResultModel(BaseModel):
        title: str
        summary: str

    @dec.plugin_entry()
    async def search(params: _ParamsModel) -> _ResultModel:
        return _ResultModel(title="demo", summary=params.query)

    meta = getattr(search, dec.EVENT_META_ATTR)
    assert meta.params is _ParamsModel
    assert meta.input_schema is not None
    assert meta.input_schema["type"] == "object"
    assert meta.input_schema["properties"]["query"]["type"] == "string"
    assert meta.llm_result_model is _ResultModel
    assert meta.llm_result_fields == ["title", "summary"]


def test_plugin_entry_auto_infers_class_local_models_after_neko_plugin() -> None:
    @dec.neko_plugin
    class _Plugin:
        class A(BaseModel):
            x: int
            y: str

        class B(BaseModel):
            z: float
            w: str

        @dec.plugin_entry()
        async def another_entry(self, params: A) -> B:
            return self.B(z=3.14, w="hello")

    meta = getattr(_Plugin.another_entry, dec.EVENT_META_ATTR)
    assert meta.params is _Plugin.A
    assert meta.input_schema is not None
    assert meta.input_schema["type"] == "object"
    assert meta.input_schema["properties"]["x"]["type"] == "integer"
    assert meta.llm_result_model is _Plugin.B
    assert meta.llm_result_fields == ["z", "w"]


def test_plugin_entry_proxy_uses_end_user_locals_for_type_inference() -> None:
    class _ResultModel(BaseModel):
        title: str

    @dec.plugin.entry()
    async def search() -> _ResultModel:
        return _ResultModel(title="demo")

    meta = getattr(search, dec.EVENT_META_ATTR)
    assert meta.llm_result_model is _ResultModel
    assert meta.llm_result_fields == ["title"]


def test_plugin_entry_empty_id_rejected() -> None:
    with pytest.raises(ValueError, match="entry id must be non-empty"):
        dec.plugin_entry(id="   ")(lambda: None)


def test_plugin_entry_rejects_positional_arguments() -> None:
    with pytest.raises(TypeError):
        dec.plugin_entry("hello")  # type: ignore[call-arg]


def test_plugin_entry_rejects_input_schema_and_params_together() -> None:
    class _ParamsModel:
        @staticmethod
        def model_json_schema() -> dict[str, object]:
            return {"type": "object"}

    with pytest.raises(ValueError, match="mutually exclusive"):
        dec.plugin_entry(input_schema={"type": "object"}, params=_ParamsModel)


def test_plugin_entry_rejects_multiple_llm_result_contract_forms() -> None:
    class _ResultModel(BaseModel):
        title: str

    with pytest.raises(ValueError, match="mutually exclusive"):
        dec.plugin_entry(llm_result_fields=["title"], llm_result_model=_ResultModel)

    with pytest.raises(ValueError, match="mutually exclusive"):
        dec.plugin_entry(llm_result_fields=["title"], fields=_ResultModel)


def test_on_event_empty_id_rejected() -> None:
    with pytest.raises(ValueError, match="event id must be non-empty"):
        dec.on_event(event_type="evt", id="   ")(lambda: None)


def test_lifecycle_message_timer_and_custom_event() -> None:
    @dec.lifecycle(id="startup", name="Start")
    def on_start() -> None:
        return None

    lmeta = getattr(on_start, dec.EVENT_META_ATTR)
    assert lmeta.event_type == "lifecycle"
    assert lmeta.kind == "lifecycle"
    assert lmeta.id == "startup"

    @dec.message(id="m1", source="telegram")
    def on_msg() -> None:
        return None

    mmeta = getattr(on_msg, dec.EVENT_META_ATTR)
    assert mmeta.event_type == "message"
    assert mmeta.kind == "consumer"
    assert mmeta.metadata["source"] == "telegram"

    with pytest.raises(ValueError, match="seconds must be > 0"):
        dec.timer_interval(id="t1", seconds=0)
    with pytest.raises(ValueError, match="seconds must be > 0"):
        dec.timer_interval(id="t1_neg", seconds=-1)

    @dec.timer_interval(id="t1", seconds=10)
    def on_tick() -> None:
        return None

    tmeta = getattr(on_tick, dec.EVENT_META_ATTR)
    assert tmeta.event_type == "timer"
    assert tmeta.kind == "timer"
    assert tmeta.auto_start is True
    assert tmeta.extra["mode"] == "interval"
    assert tmeta.extra["seconds"] == 10
    assert tmeta.metadata["seconds"] == 10

    @dec.custom_event(event_type="audit", id="c1", trigger_method="manual")
    def on_custom() -> None:
        return None

    cmeta = getattr(on_custom, dec.EVENT_META_ATTR)
    assert cmeta.event_type == "audit"
    assert cmeta.kind == "custom"
    assert cmeta.extra["trigger_method"] == "manual"
    assert cmeta.metadata["trigger_method"] == "manual"


def test_lifecycle_signatures_match_shared_literal_contract() -> None:
    lifecycle_annotation = inspect.signature(dec.lifecycle).parameters["id"].annotation
    plugin_lifecycle_annotation = inspect.signature(dec.plugin.lifecycle).parameters["id"].annotation

    assert lifecycle_annotation == dec._lifecycle.__annotations__["id"]
    assert plugin_lifecycle_annotation == lifecycle_annotation


def test_hook_and_shortcuts_attach_metadata() -> None:
    with pytest.raises(ValueError, match="timing must be one of"):
        dec.hook(timing="invalid")

    @dec.hook(target="x", timing="before", priority=3, condition="ready")
    def h1() -> None:
        return None

    hmeta = getattr(h1, dec.HOOK_META_ATTR)
    assert hmeta.target == "x"
    assert hmeta.timing == "before"
    assert hmeta.priority == 3
    assert hmeta.condition == "ready"

    @dec.before_entry(target="a", priority=1)
    def hb() -> None:
        return None

    @dec.after_entry(target="a", priority=2)
    def ha() -> None:
        return None

    @dec.around_entry(target="a", priority=4)
    def hr() -> None:
        return None

    @dec.replace_entry(target="a", priority=5)
    def hp() -> None:
        return None

    assert getattr(hb, dec.HOOK_META_ATTR).timing == "before"
    assert getattr(ha, dec.HOOK_META_ATTR).timing == "after"
    assert getattr(hr, dec.HOOK_META_ATTR).timing == "around"
    assert getattr(hp, dec.HOOK_META_ATTR).timing == "replace"


def test_plugin_entry_proxy_object_forwards(monkeypatch) -> None:
    sentinel = object()

    def fake_plugin_entry(**kwargs: object):
        assert kwargs["id"] == "x"
        assert kwargs["auto_start"] is True
        return sentinel

    monkeypatch.setattr(dec, "plugin_entry", fake_plugin_entry)
    assert dec.plugin.entry(id="x", auto_start=True) is sentinel


def test_plugin_event_proxy_forwards_mode_seconds_and_extra(monkeypatch) -> None:
    sentinel = object()

    def fake_on_event(**kwargs: object):
        assert kwargs["mode"] == "interval"
        assert kwargs["seconds"] == 3
        assert kwargs["extra"] == {"scope": "demo"}
        return sentinel

    monkeypatch.setattr(dec, "on_event", fake_on_event)
    assert dec.plugin.event(
        event_type="evt",
        mode="interval",
        seconds=3,
        extra={"scope": "demo"},
    ) is sentinel


def test_plugin_entry_proxy_object_forwards_llm_result_kwargs(monkeypatch) -> None:
    sentinel = object()

    def fake_plugin_entry(**kwargs: object):
        assert kwargs["llm_result_fields"] == ["title"]
        assert kwargs["llm_result_model"] is BaseModel
        assert kwargs["fields"] is BaseModel
        return sentinel

    monkeypatch.setattr(dec, "plugin_entry", fake_plugin_entry)
    assert dec.plugin.entry(
        llm_result_fields=["title"],
        llm_result_model=BaseModel,
        fields=BaseModel,
    ) is sentinel


def test_plugin_proxy_object_additional_forwards(monkeypatch) -> None:
    sentinel = object()

    def _sentinel(**kwargs: object):
        return (sentinel, kwargs)

    monkeypatch.setattr(dec, "on_event", _sentinel)
    monkeypatch.setattr(dec, "hook", _sentinel)
    monkeypatch.setattr(dec, "lifecycle", _sentinel)
    monkeypatch.setattr(dec, "message", _sentinel)
    monkeypatch.setattr(dec, "timer_interval", _sentinel)
    monkeypatch.setattr(dec, "custom_event", _sentinel)

    assert dec.plugin.event(event_type="evt", id="e")[0] is sentinel
    assert dec.plugin.hook(target="x")[0] is sentinel
    assert dec.plugin.lifecycle(id="startup")[0] is sentinel
    assert dec.plugin.message(id="m")[0] is sentinel
    assert dec.plugin.timer(id="t", seconds=1)[0] is sentinel
    assert dec.plugin.custom_event(event_type="x", id="c")[0] is sentinel
