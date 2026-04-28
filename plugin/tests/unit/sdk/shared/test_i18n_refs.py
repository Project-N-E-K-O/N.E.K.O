from __future__ import annotations

import json
from pathlib import Path

from plugin.sdk.shared.i18n import PluginI18n, load_plugin_i18n_from_meta


def test_non_chinese_locale_does_not_fallback_to_zh_cn() -> None:
    i18n = PluginI18n(
        {
            "zh-CN": {"hello": "你好"},
            "en": {},
        },
        default_locale="en",
    )

    assert i18n.t("hello", locale="ja", default="Hello") == "Hello"


def test_chinese_locale_can_fallback_to_zh_cn() -> None:
    i18n = PluginI18n({"zh-CN": {"hello": "你好"}}, default_locale="en")

    assert i18n.t("hello", locale="zh-TW", default="Hello") == "你好"


def test_i18n_locales_dir_must_stay_inside_plugin_dir(tmp_path: Path) -> None:
    plugin_dir = tmp_path / "plugin"
    plugin_dir.mkdir()
    outside_dir = tmp_path / "outside"
    outside_dir.mkdir()
    (outside_dir / "en.json").write_text(json.dumps({"secret": "leaked"}), encoding="utf-8")
    config_path = plugin_dir / "plugin.toml"
    config_path.write_text("[plugin]\nid='demo'\n", encoding="utf-8")

    i18n = load_plugin_i18n_from_meta({
        "config_path": str(config_path),
        "i18n": {
            "default_locale": "en",
            "locales_dir": "../outside",
        },
    })

    assert i18n.t("secret", locale="en", default="safe") == "safe"


def test_i18n_absolute_locales_dir_is_rejected(tmp_path: Path) -> None:
    plugin_dir = tmp_path / "plugin"
    plugin_dir.mkdir()
    outside_dir = tmp_path / "outside"
    outside_dir.mkdir()
    (outside_dir / "en.json").write_text(json.dumps({"secret": "leaked"}), encoding="utf-8")
    config_path = plugin_dir / "plugin.toml"
    config_path.write_text("[plugin]\nid='demo'\n", encoding="utf-8")

    i18n = load_plugin_i18n_from_meta({
        "config_path": str(config_path),
        "i18n": {
            "default_locale": "en",
            "locales_dir": str(outside_dir),
        },
    })

    assert i18n.t("secret", locale="en", default="safe") == "safe"
