# Copyright 2025-2026 Project N.E.K.O. Team
# SPDX-License-Identifier: Apache-2.0
"""Subscriptions & plans panel: every string it shows exists in all eight locales, and the page wires it up."""

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
LOCALES = ("en", "zh-CN", "zh-TW", "ja", "ko", "es", "pt", "ru")
SCRIPT = ROOT / "static/js/subscription_settings.js"


def _lookup(messages, dotted):
    node = messages
    for part in dotted.split("."):
        if not isinstance(node, dict) or part not in node:
            return None
        node = node[part]
    return node


def _used_keys():
    script = SCRIPT.read_text(encoding="utf-8")
    keys = {f"api.subscriptions.{key}" for key in re.findall(r"\bt\('([a-zA-Z_.]+)'", script)}
    # Keys built from a template literal: error codes and regions.
    keys |= {f"api.subscriptions.error.{code}" for code in (
        "network", "models", "no_model", "missing_key", "not_signed_in", "no_tiers",
        "unknown_plan", "unsupported_tier", "no_key", "unknown_region", "busy",
    )}
    plans = json.loads((ROOT / "config/api_providers.json").read_text(encoding="utf-8"))["subscription_plans"]
    for plan in plans.values():
        keys |= {f"api.subscriptions.regions.{region}" for region in plan.get("regions", {})}
    keys |= set(re.findall(r"'(api\.[a-zA-Z]+(?:ModelConfig|ApiConfigTitle))'", script))
    return keys


def test_every_key_the_panel_uses_exists_in_all_locales():
    used = _used_keys()
    assert "api.subscriptions.title" in used and "api.conversationModelConfig" in used
    for locale in LOCALES:
        messages = json.loads((ROOT / "static/locales" / f"{locale}.json").read_text(encoding="utf-8"))
        missing = sorted(key for key in used if not isinstance(_lookup(messages, key), str) or not _lookup(messages, key))
        assert not missing, f"{locale} lacks {missing}"


def test_placeholders_survive_translation():
    english = json.loads((ROOT / "static/locales/en.json").read_text(encoding="utf-8"))["api"]["subscriptions"]

    def flat(node, prefix=""):
        for key, value in node.items():
            if isinstance(value, dict):
                yield from flat(value, f"{prefix}{key}.")
            else:
                yield f"{prefix}{key}", value

    expected = {key: set(re.findall(r"\{\{\w+\}\}", value)) for key, value in flat(english)}
    for locale in LOCALES:
        messages = json.loads((ROOT / "static/locales" / f"{locale}.json").read_text(encoding="utf-8"))["api"]["subscriptions"]
        for key, value in flat(messages):
            assert set(re.findall(r"\{\{\w+\}\}", value)) == expected[key], (locale, key)


def test_page_loads_the_panel_and_hides_chatgpt_from_slot_dropdowns():
    template = (ROOT / "templates/api_key_settings.html").read_text(encoding="utf-8")
    assert '<script src="/static/js/subscription_settings.js?v={{ static_asset_version }}"></script>' in template
    assert template.index("chatgpt_settings.js") < template.index("subscription_settings.js")
    assert 'id="subscription-summary"' in template and 'id="subscription-cards"' in template
    providers = json.loads((ROOT / "config/api_providers.json").read_text(encoding="utf-8"))["assist_api_providers"]
    assert providers["chatgpt"]["subscription_panel_only"] is True
    settings = (ROOT / "static/js/api_key_settings.js").read_text(encoding="utf-8")
    assert "subscription_panel_only" in settings


def test_panel_never_posts_to_the_settings_save_endpoint():
    script = SCRIPT.read_text(encoding="utf-8")
    assert "/api/config/subscriptions" in script
    assert "core_api" not in script
