from __future__ import annotations

import importlib

import plugin.sdk_v2.plugin as plugin_api
from plugin.sdk_v2.plugin import runtime as runtime


def test_plugin_init_reexports_runtime_symbols() -> None:
    mod = importlib.reload(plugin_api)

    for name in runtime.__all__:
        assert hasattr(mod, name)
        assert getattr(mod, name) is getattr(runtime, name)

    assert "_name" not in vars(mod)


def test_plugin_init_all_contains_expected_symbols() -> None:
    mod = importlib.reload(plugin_api)
    required = {
        "NekoPluginBase",
        "PluginMeta",
        "neko_plugin",
        "plugin_entry",
        "plugin",
        "PERSIST_ATTR",
        "CHECKPOINT_ATTR",
        "PluginConfig",
        "Plugins",
        "PluginRouter",
        "Result",
        "Ok",
        "Err",
        "ok",
        "fail",
    }
    assert required.issubset(set(mod.__all__))
    assert len(mod.__all__) == len(set(mod.__all__))
