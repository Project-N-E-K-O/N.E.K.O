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
