from __future__ import annotations

import importlib

import plugin.sdk_v2 as sdk2


def test_sdk_v2_root_exposes_namespaces_and_sdk_constants() -> None:
    mod = importlib.reload(sdk2)
    assert mod.plugin is not None
    assert mod.extension is not None
    assert mod.adapter is not None
    assert mod.shared is not None
    assert mod.SDK_VERSION == "2.0.0a0"
    assert mod.NEKO_PLUGIN_META_ATTR == "__neko_plugin_meta__"
    assert mod.NEKO_PLUGIN_TAG == "__neko_plugin__"
    assert mod.EVENT_META_ATTR == "__neko_event_meta__"
    assert mod.HOOK_META_ATTR == "__neko_hook_meta__"
    assert mod.PERSIST_ATTR == "_neko_persist"
    assert mod.CHECKPOINT_ATTR == mod.PERSIST_ATTR


def test_sdk_v2_root_is_conservative_about_reexports() -> None:
    mod = importlib.reload(sdk2)
    assert not hasattr(mod, "NekoPluginBase")
    assert not hasattr(mod, "PluginConfig")
    assert not hasattr(mod, "Plugins")
    assert not hasattr(mod, "AdapterBase")
    assert not hasattr(mod, "ExtensionRuntime")


def test_sdk_v2_public_packages_are_not_developer_surfaces() -> None:
    public_mod = importlib.import_module("plugin.sdk_v2.public")
    public_adapter_mod = importlib.import_module("plugin.sdk_v2.public.adapter")
    assert public_mod.__all__ == []
    assert public_adapter_mod.__all__ == []
