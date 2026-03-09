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


def test_extension_decorators_construct() -> None:
    def fn() -> str:
        return "ok"
    assert dec.extension_entry()(fn) is fn
    assert getattr(fn, dec.EXTENSION_ENTRY_META).id is None
    assert dec.extension_hook()(fn) is fn
    assert getattr(fn, dec.EXTENSION_HOOK_META).target == "*"


def test_extension_decorators_return_paths() -> None:
    def fn() -> str:
        return "ok"

    assert dec.extension_entry()(fn) is fn
    assert dec.extension_hook()(fn) is fn


@pytest.mark.asyncio
async def test_extension_runtime_health() -> None:
    class _Router:
        def name(self) -> str:
            return "router"
    rt = extension.ExtensionRuntime(config=object(), router=_Router(), transport=object())
    health = await rt.health()
    assert health.is_ok()
    assert health.unwrap()["status"] == "ok"


def test_extension_runtime_common_exports() -> None:
    assert extension.SDK_VERSION == "0.1.0"
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


def test_extension_runtime_surface_is_more_aligned() -> None:
    assert extension.PluginConfigError is not None
    assert extension.ConfigPathError is not None
    assert extension.ConfigValidationError is not None
    assert extension.PluginRouterError is not None
    assert extension.EntryConflictError is not None
    assert extension.RouteHandler is not None
    assert extension.CallChain.__name__ == "CallChain"
    assert extension.AsyncCallChain.__name__ == "AsyncCallChain"
    assert isinstance(extension.CircularCallError("e"), RuntimeError)
    assert isinstance(extension.CallChainTooDeepError("e"), RuntimeError)
