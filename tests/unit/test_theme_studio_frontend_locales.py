"""Keep Theme Studio controls and their interpolation variables available in every locale."""

import json
import re
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
PLACEHOLDER = re.compile(r"{{\s*([\w.]+)\s*}}")


def _entries(locale):
    def flatten(mapping, prefix=""):
        for name, value in mapping.items():
            key = prefix + name
            if isinstance(value, dict):
                yield from flatten(value, key + ".")
            else:
                yield key, value

    path = ROOT / "static" / "locales" / f"{locale}.json"
    return dict(flatten(json.loads(path.read_text(encoding="utf-8"))["themeStudio"]))


@pytest.mark.parametrize("locale", ["es", "ja", "ko", "pt", "ru", "zh-CN", "zh-TW"])
def test_translated_controls_match_english_keys_and_placeholders(locale):
    english, translated = _entries("en"), _entries(locale)
    assert translated.keys() == english.keys()
    for key, value in translated.items():
        assert value.strip(), key
        assert sorted(PLACEHOLDER.findall(value)) == sorted(PLACEHOLDER.findall(english[key])), key


def test_panel_markup_references_translated_controls():
    panel = (ROOT / "templates" / "theme_studio.html").read_text(encoding="utf-8")
    keys = set(re.findall(r'data-i18n(?:-[\w-]+)?=["\']themeStudio\.([\w.-]+)["\']', panel))
    assert keys
    assert not keys.difference(_entries("en"))
