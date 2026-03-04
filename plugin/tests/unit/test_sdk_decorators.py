from __future__ import annotations

from typing import Optional

import pytest
from pydantic import BaseModel

from plugin.sdk.decorators import (
    PERSIST_ATTR,
    WORKER_MODE_ATTR,
    _PARAMS_MODEL_ATTR,
    on_event,
    plugin_entry,
    worker,
)
from plugin.sdk.events import EVENT_META_ATTR


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
    class Params(BaseModel):
        query: str

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
def test_worker_sets_worker_config_attribute() -> None:
    def handler(self, **kwargs):
        return {"ok": True}

    decorated = worker(timeout=9.5, priority=3)(handler)
    config = getattr(decorated, WORKER_MODE_ATTR)
    assert config.timeout == 9.5
    assert config.priority == 3
