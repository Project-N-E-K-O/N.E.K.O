from __future__ import annotations

import pytest

from plugin.sdk_v2.plugin import decorators as dec


def test_constants_and_exports() -> None:
    assert dec.PERSIST_ATTR == "_neko_persist"
    assert dec.CHECKPOINT_ATTR == dec.PERSIST_ATTR
    assert dec.EVENT_META_ATTR == "__neko_event_meta__"
    assert dec.HOOK_META_ATTR == "__neko_hook_meta__"

    for name in dec.__all__:
        assert hasattr(dec, name)


def test_neko_plugin_sets_marker() -> None:
    cls = type("P", (), {})
    wrapped = dec.neko_plugin(cls)
    assert wrapped is cls
    assert getattr(cls, "__neko_plugin__") is True


def test_on_event_validation_and_metadata_attach() -> None:
    with pytest.raises(ValueError, match="event_type must be non-empty"):
        dec.on_event(event_type="   ")

    @dec.on_event(event_type="evt", id="a1", name="Action 1", description="d", metadata={"m": 1}, extra={"x": 2})
    def fn() -> str:
        return "ok"

    meta = getattr(fn, dec.EVENT_META_ATTR)
    assert meta.event_type == "evt"
    assert meta.id == "a1"
    assert meta.name == "Action 1"
    assert meta.description == "d"
    assert meta.kind == "action"
    assert meta.model_validate is True
    assert meta.metadata == {"m": 1}
    assert meta.extra == {"x": 2}


def test_plugin_entry_defaults_and_persist_flags() -> None:
    @dec.plugin_entry(persist=True, params=dict, model_validate=False, timeout=3.0)
    def run() -> str:
        return "ok"

    meta = getattr(run, dec.EVENT_META_ATTR)
    assert meta.event_type == "plugin_entry"
    assert meta.id == "run"
    assert meta.name == "run"
    assert meta.params is dict
    assert meta.model_validate is False
    assert meta.timeout == 3.0
    assert getattr(run, dec.PERSIST_ATTR) is True

    @dec.plugin_entry(checkpoint=False)
    def run2() -> str:
        return "ok"

    assert getattr(run2, dec.PERSIST_ATTR) is False


def test_plugin_entry_empty_id_rejected() -> None:
    with pytest.raises(ValueError, match="entry id must be non-empty"):
        dec.plugin_entry(id="   ")(lambda: None)


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
    assert mmeta.extra["source"] == "telegram"

    with pytest.raises(ValueError, match="seconds must be > 0"):
        dec.timer_interval(id="t1", seconds=0)

    @dec.timer_interval(id="t1", seconds=10)
    def on_tick() -> None:
        return None

    tmeta = getattr(on_tick, dec.EVENT_META_ATTR)
    assert tmeta.event_type == "timer"
    assert tmeta.kind == "timer"
    assert tmeta.auto_start is True
    assert tmeta.extra["seconds"] == 10

    @dec.custom_event(event_type="audit", id="c1", trigger_method="manual")
    def on_custom() -> None:
        return None

    cmeta = getattr(on_custom, dec.EVENT_META_ATTR)
    assert cmeta.event_type == "audit"
    assert cmeta.kind == "custom"
    assert cmeta.extra["trigger_method"] == "manual"


def test_hook_and_shortcuts_attach_metadata() -> None:
    with pytest.raises(ValueError, match="timing must be one of"):
        dec.hook(timing="invalid")

    @dec.hook(target="x", timing="before", priority=3, condition="ok")
    def h1() -> None:
        return None

    hmeta = getattr(h1, dec.HOOK_META_ATTR)
    assert hmeta.target == "x"
    assert hmeta.timing == "before"
    assert hmeta.priority == 3
    assert hmeta.condition == "ok"

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


def test_plugin_entry_proxy_object_forwards() -> None:
    sentinel = object()

    def fake_plugin_entry(**kwargs: object):
        assert kwargs == {"id": "x", "auto_start": True}
        return sentinel

    original = dec.plugin_entry
    dec.plugin_entry = fake_plugin_entry  # type: ignore[assignment]
    try:
        assert dec.plugin.entry(id="x", auto_start=True) is sentinel
    finally:
        dec.plugin_entry = original  # type: ignore[assignment]


def test_plugin_proxy_object_additional_forwards() -> None:
    sentinel = object()

    def _sentinel(**kwargs: object):
        return (sentinel, kwargs)

    originals = {
        "on_event": dec.on_event,
        "hook": dec.hook,
        "lifecycle": dec.lifecycle,
        "message": dec.message,
        "timer_interval": dec.timer_interval,
        "custom_event": dec.custom_event,
    }
    dec.on_event = _sentinel  # type: ignore[assignment]
    dec.hook = _sentinel  # type: ignore[assignment]
    dec.lifecycle = _sentinel  # type: ignore[assignment]
    dec.message = _sentinel  # type: ignore[assignment]
    dec.timer_interval = _sentinel  # type: ignore[assignment]
    dec.custom_event = _sentinel  # type: ignore[assignment]
    try:
        assert dec.plugin.event(id="e")[0] is sentinel
        assert dec.plugin.hook(target="x")[0] is sentinel
        assert dec.plugin.lifecycle(id="startup")[0] is sentinel
        assert dec.plugin.message(id="m")[0] is sentinel
        assert dec.plugin.timer(id="t", seconds=1)[0] is sentinel
        assert dec.plugin.custom_event(event_type="x", id="c")[0] is sentinel
    finally:
        dec.on_event = originals["on_event"]  # type: ignore[assignment]
        dec.hook = originals["hook"]  # type: ignore[assignment]
        dec.lifecycle = originals["lifecycle"]  # type: ignore[assignment]
        dec.message = originals["message"]  # type: ignore[assignment]
        dec.timer_interval = originals["timer_interval"]  # type: ignore[assignment]
        dec.custom_event = originals["custom_event"]  # type: ignore[assignment]
