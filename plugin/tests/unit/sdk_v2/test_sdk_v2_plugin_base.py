from __future__ import annotations

from dataclasses import fields

import pytest

from plugin.sdk_v2.plugin.base import (
    NEKO_PLUGIN_META_ATTR,
    NEKO_PLUGIN_TAG,
    NekoPluginBase,
    PluginMeta,
)


def test_base_constants_and_meta_defaults() -> None:
    assert NEKO_PLUGIN_META_ATTR == "__neko_plugin_meta__"
    assert NEKO_PLUGIN_TAG == "__neko_plugin__"

    meta = PluginMeta(id="p", name="Plugin")
    assert meta.version == "0.0.0"
    assert meta.sdk_version == "2.0.0a0"
    assert meta.sdk_recommended is None
    assert meta.sdk_conflicts == []


def test_plugin_meta_conflicts_default_factory_isolated() -> None:
    a = PluginMeta(id="a", name="A")
    b = PluginMeta(id="b", name="B")
    a.sdk_conflicts.append("x")
    assert b.sdk_conflicts == []


def test_plugin_meta_fields_shape() -> None:
    names = [f.name for f in fields(PluginMeta)]
    assert names == [
        "id",
        "name",
        "version",
        "sdk_version",
        "description",
        "sdk_recommended",
        "sdk_supported",
        "sdk_untested",
        "sdk_conflicts",
    ]


def test_neko_plugin_base_class_defaults() -> None:
    assert NekoPluginBase.__freezable__ == []
    assert NekoPluginBase.__persist_mode__ == "off"


def test_neko_plugin_base_init_raises_contract_error() -> None:
    with pytest.raises(NotImplementedError, match="contract-only facade"):
        NekoPluginBase(ctx=object())


def test_neko_plugin_base_contract_methods_raise_not_implemented() -> None:
    base = object.__new__(NekoPluginBase)

    with pytest.raises(NotImplementedError):
        base.get_input_schema()
    with pytest.raises(NotImplementedError):
        base.include_router(router=object(), prefix="/x")
    with pytest.raises(NotImplementedError):
        base.exclude_router(router="r")
    with pytest.raises(NotImplementedError):
        base.enable_file_logging(log_level="INFO")
