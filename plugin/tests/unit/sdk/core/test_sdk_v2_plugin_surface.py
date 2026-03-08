from __future__ import annotations

import plugin.sdk_v2.plugin as sdk2


def test_sdk_v2_plugin_surface_has_core_exports() -> None:
    assert hasattr(sdk2, "NekoPluginBase")
    assert hasattr(sdk2, "neko_plugin")
    assert hasattr(sdk2, "plugin_entry")
    assert hasattr(sdk2, "PluginConfig")
    assert hasattr(sdk2, "Plugins")
    assert hasattr(sdk2, "PluginRouter")
    assert hasattr(sdk2, "ok")
    assert hasattr(sdk2, "fail")


def test_sdk_v2_plugin_surface_has_result_exports() -> None:
    assert hasattr(sdk2, "Ok")
    assert hasattr(sdk2, "Err")
    assert hasattr(sdk2, "Result")
    assert hasattr(sdk2, "must")
    assert hasattr(sdk2, "capture")
