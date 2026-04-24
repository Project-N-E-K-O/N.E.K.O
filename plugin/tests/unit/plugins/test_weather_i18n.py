"""Tests for weather plugin I18n and LRUCache."""

from __future__ import annotations

import time
from pathlib import Path

from plugin.plugins.weather._i18n import I18n, LRUCache

_LOCALES_DIR = Path(__file__).parent.parent.parent.parent / "plugins" / "weather" / "locales"


class TestI18n:
    def test_load_locales(self):
        i18n = I18n(_LOCALES_DIR)
        assert i18n.locale == "zh-CN"

    def test_set_locale(self):
        i18n = I18n(_LOCALES_DIR)
        i18n.set_locale("en")
        assert i18n.locale == "en"

    def test_set_locale_normalize(self):
        i18n = I18n(_LOCALES_DIR)
        i18n.set_locale("zh")
        assert i18n.locale == "zh-CN"
        i18n.set_locale("zh-TW")
        assert i18n.locale == "zh-TW"
        i18n.set_locale("en-US")
        assert i18n.locale == "en"

    def test_t_basic(self):
        i18n = I18n(_LOCALES_DIR)
        assert i18n.t("wmo.0") == "晴"
        i18n.set_locale("en")
        assert i18n.t("wmo.0") == "Clear sky"

    def test_t_template(self):
        i18n = I18n(_LOCALES_DIR)
        result = i18n.t("summary.weather", city="上海", weather="晴", temp=25, feels=23, humidity=65)
        assert "上海" in result
        assert "25" in result

    def test_t_fallback(self):
        i18n = I18n(_LOCALES_DIR)
        assert i18n.t("nonexistent.key") == "nonexistent.key"

    def test_t_locale_override(self):
        i18n = I18n(_LOCALES_DIR)
        i18n.set_locale("zh-CN")
        result = i18n.t("wmo.0", locale="en")
        assert result == "Clear sky"

    def test_t_chain_fallback(self):
        i18n = I18n(_LOCALES_DIR, default="zh-CN")
        i18n.set_locale("zh-TW")
        # zh-TW has this key
        assert i18n.t("wmo.0") == "晴"


class TestLRUCache:
    def test_put_and_get(self):
        cache = LRUCache(max_size=3)
        cache.put("a", {"temp": 25})
        assert cache.get("a", ttl=60) == {"temp": 25}

    def test_ttl_expiry(self):
        cache = LRUCache(max_size=3)
        cache.put("a", "data")
        assert cache.get("a", ttl=0) is None  # TTL=0 means expired immediately

    def test_lru_eviction(self):
        cache = LRUCache(max_size=2)
        cache.put("a", 1)
        cache.put("b", 2)
        cache.put("c", 3)  # evicts "a"
        assert cache.get("a", ttl=60) is None
        assert cache.get("b", ttl=60) == 2
        assert cache.get("c", ttl=60) == 3

    def test_access_refreshes_lru(self):
        cache = LRUCache(max_size=2)
        cache.put("a", 1)
        cache.put("b", 2)
        cache.get("a", ttl=60)  # refresh "a"
        cache.put("c", 3)  # evicts "b" (not "a")
        assert cache.get("a", ttl=60) == 1
        assert cache.get("b", ttl=60) is None

    def test_miss_returns_none(self):
        cache = LRUCache(max_size=3)
        assert cache.get("nonexistent", ttl=60) is None
