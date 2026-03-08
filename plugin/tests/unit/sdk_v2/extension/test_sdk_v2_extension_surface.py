from __future__ import annotations

import pytest

import plugin.sdk_v2.extension as extension
from plugin.sdk_v2.extension import decorators as dec


def test_extension_exports_exist() -> None:
    for name in extension.__all__:
        assert hasattr(extension, name)


def test_extension_meta_construct() -> None:
    meta = extension.ExtensionMeta(id="ext", name="Extension")
    assert meta.version == "0.0.0"
    assert meta.capabilities == []


def test_extension_decorators_raise() -> None:
    with pytest.raises(NotImplementedError):
        dec.extension_entry()
    with pytest.raises(NotImplementedError):
        dec.extension_hook()


def test_extension_decorators_return_paths(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(dec, "_not_impl", lambda *_args, **_kwargs: None)

    def fn() -> str:
        return "ok"

    assert dec.extension_entry()(fn) is fn
    assert dec.extension_hook()(fn) is fn


@pytest.mark.asyncio
async def test_extension_runtime_not_implemented() -> None:
    rt = extension.ExtensionRuntime(config=object(), router=object(), transport=object())
    with pytest.raises(NotImplementedError):
        await rt.health()


def test_extension_runtime_common_exports() -> None:
    assert extension.SDK_VERSION == "2.0.0a0"
    assert extension.ok is not None
    assert extension.fail is not None
    assert extension.Result is not None
    assert extension.ErrorCode is not None


def test_extension_decorator_metadata_exports() -> None:
    assert dec.EXTENSION_ENTRY_META == "__extension_entry_meta__"
    assert dec.EXTENSION_HOOK_META == "__extension_hook_meta__"
    assert dec.ExtensionEntryMeta(id="x", name=None, description="", timeout=None).id == "x"
    assert dec.ExtensionHookMeta(target="*", timing="before", priority=0).timing == "before"


def test_extension_proxy_object_forwards(monkeypatch: pytest.MonkeyPatch) -> None:
    sentinel = object()

    def fake_entry(**kwargs: object):
        assert kwargs == {"id": "x"}
        return sentinel

    def fake_hook(**kwargs: object):
        assert kwargs == {"target": "a"}
        return sentinel

    original_entry = dec.extension_entry
    original_hook = dec.extension_hook
    dec.extension_entry = fake_entry  # type: ignore[assignment]
    dec.extension_hook = fake_hook  # type: ignore[assignment]
    try:
        assert dec.extension.entry(id="x") is sentinel
        assert dec.extension.hook(target="a") is sentinel
    finally:
        dec.extension_entry = original_entry  # type: ignore[assignment]
        dec.extension_hook = original_hook  # type: ignore[assignment]
