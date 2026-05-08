from __future__ import annotations

from pathlib import Path

import pytest

from plugin.core import registry as module


def _make_ctx(pid: str, host_pid: str, *, enabled: bool, conf_enabled: bool) -> module.PluginContext:
    return module.PluginContext(
        pid=pid,
        toml_path=Path(f"/tmp/{pid}/plugin.toml"),
        conf={"plugin_runtime": {"enabled": conf_enabled}},
        pdata={
            "id": pid,
            "type": "extension",
            "host": {"plugin_id": host_pid, "prefix": ""},
        },
        entry=f"tests.fake_{pid}:Plugin",
        dependencies=[],
        sdk_supported_str=None,
        sdk_recommended_str=None,
        sdk_untested_str=None,
        sdk_conflicts_list=[],
        enabled=enabled,
        auto_start=False,
    )


@pytest.mark.plugin_unit
def test_build_extension_map_respects_ctx_enabled_user_override():
    """Regression: user override (mirrored into ctx.enabled) must gate host injection.

    When the user disables an extension via the UI we persist the override and
    apply it during ``_parse_single_plugin_config``, which sets ``ctx.enabled``.
    ``_build_extension_map`` previously re-read ``ctx.conf['plugin_runtime']``
    instead, which holds the raw manifest value — so the extension would still
    be injected into the host on next start despite the override.
    """
    ctx_disabled_via_override = _make_ctx(
        "ext_off", "host", enabled=False, conf_enabled=True
    )
    ctx_normal = _make_ctx("ext_on", "host", enabled=True, conf_enabled=True)

    extension_map = module._build_extension_map([ctx_disabled_via_override, ctx_normal])

    assert "host" in extension_map
    assert [item["ext_id"] for item in extension_map["host"]] == ["ext_on"]


@pytest.mark.plugin_unit
def test_build_extension_map_skips_when_ctx_disabled_even_if_conf_enabled():
    ctx = _make_ctx("ext", "host", enabled=False, conf_enabled=True)
    assert module._build_extension_map([ctx]) == {}


@pytest.mark.plugin_unit
def test_build_extension_map_includes_when_ctx_enabled_and_conf_missing():
    """conf 没声明 plugin_runtime 时也走 ctx.enabled。"""
    ctx = module.PluginContext(
        pid="ext",
        toml_path=Path("/tmp/ext/plugin.toml"),
        conf={},  # 无 plugin_runtime 段
        pdata={
            "id": "ext",
            "type": "extension",
            "host": {"plugin_id": "host", "prefix": ""},
        },
        entry="tests.fake_ext:Plugin",
        dependencies=[],
        sdk_supported_str=None,
        sdk_recommended_str=None,
        sdk_untested_str=None,
        sdk_conflicts_list=[],
        enabled=True,
        auto_start=False,
    )
    extension_map = module._build_extension_map([ctx])
    assert extension_map == {"host": [{"ext_id": "ext", "ext_entry": "tests.fake_ext:Plugin", "prefix": ""}]}
