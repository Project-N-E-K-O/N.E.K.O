from __future__ import annotations

import importlib

import plugin.sdk_v2 as sdk2
import plugin.sdk_v2.adapter as adapter
import plugin.sdk_v2.public.adapter as public_adapter


def test_sdk_v2_root_reexports_primary_surfaces() -> None:
    mod = importlib.reload(sdk2)
    assert hasattr(mod, "NekoPluginBase")
    assert hasattr(mod, "PluginConfig")
    assert hasattr(mod, "Plugins")
    assert hasattr(mod, "AdapterBase")
    assert hasattr(mod, "ExtensionRuntime")
    assert hasattr(mod, "StatePersistence")
    assert mod.StatePersistence is mod.PluginStatePersistence


def test_public_adapter_alias_matches_adapter_surface() -> None:
    pub = importlib.reload(public_adapter)
    ada = importlib.reload(adapter)

    assert set(pub.__all__) == set(ada.__all__)
    for name in pub.__all__:
        assert hasattr(pub, name)
        assert getattr(pub, name) is getattr(ada, name)
