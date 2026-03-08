from __future__ import annotations

import importlib

import plugin.sdk_v2 as sdk2


def test_sdk_v2_root_reexports_primary_surfaces() -> None:
    mod = importlib.reload(sdk2)
    assert hasattr(mod, "NekoPluginBase")
    assert hasattr(mod, "PluginConfig")
    assert hasattr(mod, "Plugins")
    assert hasattr(mod, "AdapterBase")
    assert hasattr(mod, "ExtensionRuntime")
    assert hasattr(mod, "StatePersistence")
    assert mod.StatePersistence is mod.PluginStatePersistence


def test_sdk_v2_public_packages_are_not_developer_surfaces() -> None:
    public_mod = importlib.import_module("plugin.sdk_v2.public")
    public_adapter_mod = importlib.import_module("plugin.sdk_v2.public.adapter")
    assert public_mod.__all__ == []
    assert public_adapter_mod.__all__ == []


def test_sdk_v2_root_exports_common_constants_and_version() -> None:
    mod = importlib.reload(sdk2)
    assert mod.SDK_VERSION == "2.0.0a0"
    assert mod.NEKO_PLUGIN_META_ATTR == "__neko_plugin_meta__"
    assert mod.NEKO_PLUGIN_TAG == "__neko_plugin__"
    assert mod.EVENT_META_ATTR == "__neko_event_meta__"
    assert mod.HOOK_META_ATTR == "__neko_hook_meta__"
    assert mod.PERSIST_ATTR == "_neko_persist"
    assert mod.CHECKPOINT_ATTR == mod.PERSIST_ATTR
