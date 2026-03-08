from __future__ import annotations

import pytest

from plugin.sdk_v2.plugin import decorators as dec


def test_not_impl_always_raises() -> None:
    with pytest.raises(NotImplementedError, match="decorators not implemented"):
        dec._not_impl(1, a=2)


def test_constants_and_exports() -> None:
    assert dec.PERSIST_ATTR == "_neko_persist"
    assert dec.CHECKPOINT_ATTR == dec.PERSIST_ATTR

    for name in dec.__all__:
        assert hasattr(dec, name)


def test_decorator_factories_raise_not_implemented() -> None:
    with pytest.raises(NotImplementedError):
        dec.neko_plugin(type("X", (), {}))

    with pytest.raises(NotImplementedError):
        dec.on_event(event_type="evt")
    with pytest.raises(NotImplementedError):
        dec.plugin_entry()
    with pytest.raises(NotImplementedError):
        dec.lifecycle(id="startup")
    with pytest.raises(NotImplementedError):
        dec.message(id="m")
    with pytest.raises(NotImplementedError):
        dec.timer_interval(id="t", seconds=1)
    with pytest.raises(NotImplementedError):
        dec.custom_event(event_type="x", id="c")
    with pytest.raises(NotImplementedError):
        dec.hook()


def test_decorator_factories_return_contract_decorators_when_not_impl_disabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(dec, "_not_impl", lambda *_a, **_k: None)

    cls = type("P", (), {})
    assert dec.neko_plugin(cls) is cls

    def fn() -> str:
        return "x"

    factories = [
        dec.on_event(event_type="evt"),
        dec.plugin_entry(),
        dec.lifecycle(id="startup"),
        dec.message(id="m"),
        dec.timer_interval(id="t", seconds=1),
        dec.custom_event(event_type="x", id="c"),
        dec.hook(),
    ]
    for factory in factories:
        wrapped = factory(fn)
        assert wrapped is fn


def test_shortcut_decorators_forward_to_hook(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[str, int, str | None]] = []

    def fake_hook(*, target: str = "*", timing: str = "before", priority: int = 0, condition: str | None = None):
        calls.append((timing, priority, condition))

        def _deco(fn):
            return fn

        return _deco

    monkeypatch.setattr(dec, "hook", fake_hook)

    def fn() -> str:
        return "ok"

    assert dec.before_entry(priority=1, condition="a")(fn) is fn
    assert dec.after_entry(priority=2, condition="b")(fn) is fn
    assert dec.around_entry(priority=3, condition="c")(fn) is fn
    assert dec.replace_entry(priority=4, condition="d")(fn) is fn

    assert calls == [
        ("before", 1, "a"),
        ("after", 2, "b"),
        ("around", 3, "c"),
        ("replace", 4, "d"),
    ]


def test_plugin_entry_proxy_object_forwards(monkeypatch: pytest.MonkeyPatch) -> None:
    sentinel = object()

    def fake_plugin_entry(**kwargs):
        assert kwargs == {"id": "x", "auto_start": True}
        return sentinel

    monkeypatch.setattr(dec, "plugin_entry", fake_plugin_entry)
    assert dec.plugin.entry(id="x", auto_start=True) is sentinel
