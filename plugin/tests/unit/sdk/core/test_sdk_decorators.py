from __future__ import annotations

from typing import Optional, Union

import pytest
import plugin.sdk.decorators as deco
from plugin.sdk.decorators import PERSIST_ATTR, _PARAMS_MODEL_ATTR, on_event, plugin_entry
from plugin.sdk.events import EVENT_META_ATTR
from plugin.sdk.hooks import HOOK_META_ATTR


@pytest.mark.plugin_unit
def test_plugin_entry_auto_infers_schema() -> None:
    def handler(self, name: str, age: int = 18, enabled: Optional[bool] = None, **kwargs):
        return {"ok": True}

    decorated = plugin_entry()(handler)
    meta = getattr(decorated, EVENT_META_ATTR)

    assert meta.id == "handler"
    assert meta.input_schema["type"] == "object"
    assert meta.input_schema["properties"]["name"]["type"] == "string"
    assert meta.input_schema["properties"]["age"]["type"] == "integer"
    assert "required" in meta.input_schema
    assert "name" in meta.input_schema["required"]


@pytest.mark.plugin_unit
def test_plugin_entry_with_params_model_attaches_model() -> None:
    class Params:
        @classmethod
        def model_json_schema(cls):
            return {
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                },
                "required": ["query"],
            }

    def handler(self, **kwargs):
        return {"ok": True}

    decorated = plugin_entry(id="search", params=Params)(handler)
    meta = getattr(decorated, EVENT_META_ATTR)

    assert getattr(decorated, _PARAMS_MODEL_ATTR) is Params
    assert meta.id == "search"
    assert isinstance(meta.input_schema, dict)
    assert "properties" in meta.input_schema


@pytest.mark.plugin_unit
def test_on_event_sets_persist_attribute() -> None:
    def handler(self, **kwargs):
        return {"ok": True}

    decorated = on_event(event_type="plugin_entry", id="x", persist=True)(handler)
    assert getattr(decorated, PERSIST_ATTR) is True


@pytest.mark.plugin_unit
def test_plugin_entry_timeout_is_written_to_metadata() -> None:
    def handler(self, **kwargs):
        return {"ok": True}

    decorated = plugin_entry(timeout=9.5)(handler)
    meta = getattr(decorated, EVENT_META_ATTR)
    assert meta.metadata["timeout"] == 9.5


@pytest.mark.plugin_unit
def test_decorators_plugin_marker_and_entry_alias_and_checkpoint() -> None:
    class _Plugin:
        pass

    marked = deco.neko_plugin(_Plugin)
    assert marked is _Plugin
    assert getattr(marked, deco.NEKO_PLUGIN_TAG) is True

    def handler(self, **kwargs):  # noqa: ANN001
        return {"ok": True}

    by_alias = deco.plugin.entry(id="alias_entry", checkpoint=False)(handler)
    meta = getattr(by_alias, EVENT_META_ATTR)
    assert meta.id == "alias_entry"
    assert getattr(by_alias, PERSIST_ATTR) is False


@pytest.mark.plugin_unit
def test_infer_schema_more_paths(monkeypatch: pytest.MonkeyPatch) -> None:
    class CustomType:
        pass

    def handler(self, b: Union[str, int], c: CustomType, d: int = 1, *items, **more):  # noqa: ANN001, ANN002, ANN003
        return {"ok": True}

    class _FakeAnnotated:
        __args__ = (int,)
        __metadata__ = ("field a",)

    monkeypatch.setattr(deco, "get_type_hints", lambda *a, **k: {"a": _FakeAnnotated(), "b": Union[str, int], "c": CustomType, "d": int})

    schema = deco._infer_schema_from_func(handler)
    assert "items" not in schema["properties"]
    assert "more" not in schema["properties"]
    assert "type" not in schema["properties"]["b"]
    assert "type" not in schema["properties"]["c"]
    assert schema["properties"]["d"]["default"] == 1

    monkeypatch.setattr(deco, "get_type_hints", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("x")))

    def fallback(self, x, **kwargs):  # noqa: ANN001, ANN003
        return {"ok": True}

    schema2 = deco._infer_schema_from_func(fallback)
    assert "x" in schema2["properties"]

    def annotated_handler(self, a, **kwargs):  # noqa: ANN001, ANN003
        return {"ok": True}

    monkeypatch.setattr(deco, "get_type_hints", lambda *a, **k: {"a": _FakeAnnotated()})
    schema3 = deco._infer_schema_from_func(annotated_handler)
    assert schema3["properties"]["a"]["type"] == "integer"
    assert schema3["properties"]["a"]["description"] == "field a"
    assert "a" in schema3["required"]


@pytest.mark.plugin_unit
def test_plugin_entry_params_model_invalid_and_extra_metadata() -> None:
    class BadParams:
        pass

    with pytest.raises(TypeError):
        plugin_entry(params=BadParams)

    def handler(self, **kwargs):  # noqa: ANN001, ANN003
        return {"ok": True}

    decorated = plugin_entry(extra={"x": 1}, timeout=1.2)(handler)
    meta = getattr(decorated, EVENT_META_ATTR)
    assert meta.metadata["x"] == 1
    assert meta.metadata["timeout"] == 1.2


@pytest.mark.plugin_unit
def test_lifecycle_message_timer_custom_event_and_hook_aliases() -> None:
    def fn(self, **kwargs):  # noqa: ANN001, ANN003
        return {"ok": True}

    l1 = deco.lifecycle(id="startup", metadata={"k": 1})(fn)
    l1_meta = getattr(l1, EVENT_META_ATTR)
    assert l1_meta.event_type == "lifecycle"
    assert l1_meta.metadata["k"] == 1

    l2 = deco.lifecycle(id="shutdown", extra={"e": 1})(fn)
    l2_meta = getattr(l2, EVENT_META_ATTR)
    assert l2_meta.metadata["e"] == 1

    m1 = deco.message(id="m1", source="chat", metadata={"s": "x"})(fn)
    m1_meta = getattr(m1, EVENT_META_ATTR)
    assert m1_meta.event_type == "message"
    assert m1_meta.metadata["source"] == "chat"
    assert m1_meta.metadata["s"] == "x"
    assert "text" in m1_meta.input_schema["properties"]

    m2 = deco.message(id="m2", input_schema={"type": "object"}, extra={"e": 1})(fn)
    m2_meta = getattr(m2, EVENT_META_ATTR)
    assert m2_meta.input_schema == {"type": "object"}
    assert m2_meta.metadata["e"] == 1

    t1 = deco.timer_interval(id="t1", seconds=3, metadata={"m": 1})(fn)
    t1_meta = getattr(t1, EVENT_META_ATTR)
    assert t1_meta.event_type == "timer"
    assert t1_meta.metadata["mode"] == "interval"
    assert t1_meta.metadata["seconds"] == 3
    assert t1_meta.metadata["m"] == 1
    assert t1_meta.description == "Run every 3s"

    t2 = deco.timer_interval(id="t2", seconds=5, description="d", extra={"x": 1})(fn)
    t2_meta = getattr(t2, EVENT_META_ATTR)
    assert t2_meta.description == "d"
    assert t2_meta.metadata["x"] == 1

    c1 = deco.custom_event(event_type="file_change", id="c1", trigger_method="command", extra={"k": "v"})(fn)
    c1_meta = getattr(c1, EVENT_META_ATTR)
    assert c1_meta.event_type == "file_change"
    assert c1_meta.metadata["trigger_method"] == "command"
    assert c1_meta.metadata["k"] == "v"

    for std in ("plugin_entry", "lifecycle", "message", "timer"):
        with pytest.raises(ValueError):
            deco.custom_event(event_type=std, id="x")

    hb = deco.hook(target="*", timing="before", priority=3, condition="ok")(fn)
    hb_meta = getattr(hb, HOOK_META_ATTR)
    assert hb_meta.target == "*"
    assert hb_meta.timing == "before"
    assert hb_meta.priority == 3
    assert hb_meta.condition == "ok"

    for builder, timing in (
        (deco.before_entry, "before"),
        (deco.after_entry, "after"),
        (deco.around_entry, "around"),
    ):
        wrapped = builder(target="entry_x", priority=1, condition="c")(fn)
        meta = getattr(wrapped, HOOK_META_ATTR)
        assert meta.target == "entry_x"
        assert meta.timing == timing
        assert meta.priority == 1
        assert meta.condition == "c"

    replaced = deco.replace_entry(target="entry_y", priority=9, condition="cc")(fn)
    replaced_meta = getattr(replaced, HOOK_META_ATTR)
    assert replaced_meta.timing == "replace"
    assert replaced_meta.target == "entry_y"
