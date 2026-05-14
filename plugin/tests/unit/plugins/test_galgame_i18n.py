from __future__ import annotations

import inspect

import pytest
from plugin.sdk.shared.constants import EVENT_META_ATTR
from plugin.sdk.shared.i18n import load_plugin_i18n_from_dir, resolve_i18n_refs, tr

from plugin.plugins.galgame_plugin import GalgamePlugin

_EXPECTED_RUNTIME_KEYS = [
    "install.textractor.ok",
    "install.textractor.fail",
    "install.tesseract.ok",
    "install.tesseract.fail",
    "errors.not_configured",
    "errors.install_in_progress",
]

_EXPECTED_LOCALES = ["zh-CN", "en", "ja", "ru", "ko"]
_PARTIAL_LOCALES = ["zh-TW"]


def _galgame_entry_ids() -> list[str]:
    entry_ids: list[str] = []
    for attr_name in dir(GalgamePlugin):
        if attr_name.startswith("_"):
            continue
        target = inspect.getattr_static(GalgamePlugin, attr_name)
        target = target.__func__ if isinstance(target, (staticmethod, classmethod)) else target
        meta = getattr(target, EVENT_META_ATTR, None)
        if getattr(meta, "event_type", None) == "plugin_entry":
            entry_ids.append(str(getattr(meta, "id", "") or attr_name))
    return sorted(entry_ids)


def _assert_bundle_has_key(i18n, locale: str, key: str) -> None:
    bundle = i18n.messages.get(locale) or {}
    assert key in bundle
    assert isinstance(bundle[key], str) and bundle[key]


@pytest.mark.parametrize("locale", _EXPECTED_LOCALES)
def test_i18n_all_locales_have_all_keys(galgame_i18n_dir, locale) -> None:
    i18n = load_plugin_i18n_from_dir(galgame_i18n_dir)
    reference_keys = set(i18n.messages[_EXPECTED_LOCALES[0]])
    assert set(i18n.messages[locale]) == reference_keys
    for entry_id in _galgame_entry_ids():
        _assert_bundle_has_key(i18n, locale, f"entries.{entry_id}.name")
        _assert_bundle_has_key(i18n, locale, f"entries.{entry_id}.description")
    for key in _EXPECTED_RUNTIME_KEYS:
        _assert_bundle_has_key(i18n, locale, key)


@pytest.mark.parametrize("locale", _PARTIAL_LOCALES)
def test_i18n_partial_locales_cover_current_entry_additions(galgame_i18n_dir, locale) -> None:
    i18n = load_plugin_i18n_from_dir(galgame_i18n_dir)
    for entry_id in (
        "galgame_download_rapidocr_models",
        "galgame_set_rapidocr_lang",
    ):
        _assert_bundle_has_key(i18n, locale, f"entries.{entry_id}.name")
        _assert_bundle_has_key(i18n, locale, f"entries.{entry_id}.description")


def test_tr_ref_resolves_to_correct_locale(galgame_i18n_dir) -> None:
    ref = tr("entries.galgame_get_status.name", default="fallback")
    i18n = load_plugin_i18n_from_dir(galgame_i18n_dir)

    zh = resolve_i18n_refs(ref, i18n, locale="zh-CN")
    en = resolve_i18n_refs(ref, i18n, locale="en")

    assert zh == "获取 galgame 插件状态"
    assert en == "Get galgame plugin status"


def test_tr_default_fallback(galgame_i18n_dir) -> None:
    ref = tr("entries.nonexistent.key", default="默认值")
    i18n = load_plugin_i18n_from_dir(galgame_i18n_dir)

    result = resolve_i18n_refs(ref, i18n, locale="en")

    assert result == "默认值"
