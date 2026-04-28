from __future__ import annotations

import json
import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any

I18N_REF_KEY = "$i18n"
DEFAULT_LOCALE = "en"
DEFAULT_LOCALES_DIR = "i18n"

_INTERPOLATION_RE = re.compile(r"\{\{\s*([A-Za-z_][\w.-]*)\s*\}\}|\{\s*([A-Za-z_][\w.-]*)\s*\}")


def tr(key: str, *, default: str = "", **params: Any) -> dict[str, Any]:
    """Declare a delayed plugin-local i18n reference.

    The returned object is JSON-compatible on purpose so decorators, schemas,
    plugin metadata and hosted UI context can all carry it without special
    import-time translation.
    """
    normalized_key = str(key or "").strip()
    if not normalized_key:
        raise ValueError("i18n key must be non-empty")
    ref: dict[str, Any] = {I18N_REF_KEY: normalized_key}
    if default:
        ref["default"] = str(default)
    if params:
        ref["params"] = dict(params)
    return ref


def is_i18n_ref(value: object) -> bool:
    return isinstance(value, Mapping) and isinstance(value.get(I18N_REF_KEY), str)


def locale_candidates(locale: str | None, default_locale: str | None = None) -> list[str]:
    candidates: list[str] = []

    def add(value: str | None) -> None:
        if not value:
            return
        normalized = str(value).strip()
        if normalized and normalized not in candidates:
            candidates.append(normalized)

    add(locale)
    if locale and "-" in locale:
        add(locale.split("-", 1)[0])
    locale_lower = str(locale or "").strip().lower()
    if locale_lower == "zh" or locale_lower.startswith("zh-") or locale_lower.startswith("zh_"):
        add("zh-CN")
    add(default_locale)
    if default_locale and "-" in default_locale:
        add(default_locale.split("-", 1)[0])
    add(DEFAULT_LOCALE)
    return candidates


def interpolate_text(text: str, params: Mapping[str, object] | None = None) -> str:
    if not params:
        return text

    def replace(match: re.Match[str]) -> str:
        key = match.group(1) or match.group(2) or ""
        value = params.get(key)
        return str(value) if value is not None else match.group(0)

    return _INTERPOLATION_RE.sub(replace, text)


class PluginI18n:
    def __init__(
        self,
        messages: Mapping[str, Mapping[str, object]] | None = None,
        *,
        default_locale: str = DEFAULT_LOCALE,
    ) -> None:
        self.messages = {
            str(locale): dict(bundle)
            for locale, bundle in (messages or {}).items()
            if isinstance(bundle, Mapping)
        }
        self.default_locale = default_locale or DEFAULT_LOCALE

    def t(self, key: str, *, locale: str | None = None, default: str = "", **params: object) -> str:
        normalized_key = str(key or "").strip()
        if not normalized_key:
            return default
        for candidate in locale_candidates(locale, self.default_locale):
            bundle = self.messages.get(candidate)
            if not bundle:
                continue
            value = bundle.get(normalized_key)
            if isinstance(value, str):
                return interpolate_text(value, params)
        return interpolate_text(default or normalized_key, params)

    def resolve(self, value: object, *, locale: str | None = None) -> object:
        return resolve_i18n_refs(value, self, locale=locale)


def _load_json_file(path: Path) -> dict[str, object]:
    if not path.is_file() or path.stat().st_size > 512 * 1024:
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return {}
    return dict(payload) if isinstance(payload, Mapping) else {}


def load_plugin_i18n_from_dir(locales_dir: Path, *, default_locale: str = DEFAULT_LOCALE) -> PluginI18n:
    messages: dict[str, dict[str, object]] = {}
    if locales_dir.is_dir():
        for path in sorted(locales_dir.glob("*.json")):
            locale = path.stem.strip()
            if not locale:
                continue
            bundle = _load_json_file(path)
            if bundle:
                messages[locale] = bundle
    return PluginI18n(messages, default_locale=default_locale)


def load_plugin_i18n_from_meta(plugin_meta: Mapping[str, object]) -> PluginI18n:
    config_path_obj = plugin_meta.get("config_path")
    if not isinstance(config_path_obj, str) or not config_path_obj:
        return PluginI18n()

    try:
        plugin_dir = Path(config_path_obj).parent.resolve()
    except Exception:
        return PluginI18n()

    config_obj = plugin_meta.get("i18n")
    config = config_obj if isinstance(config_obj, Mapping) else {}
    default_locale_obj = config.get("default_locale") if isinstance(config, Mapping) else None
    locales_dir_obj = config.get("locales_dir") if isinstance(config, Mapping) else None
    default_locale = str(default_locale_obj).strip() if isinstance(default_locale_obj, str) and default_locale_obj.strip() else DEFAULT_LOCALE
    locales_dir_name = str(locales_dir_obj).strip() if isinstance(locales_dir_obj, str) and locales_dir_obj.strip() else DEFAULT_LOCALES_DIR

    locales_dir = Path(locales_dir_name)
    if locales_dir.is_absolute():
        return PluginI18n(default_locale=default_locale)
    try:
        locales_dir = (plugin_dir / locales_dir).resolve()
        locales_dir.relative_to(plugin_dir)
    except Exception:
        return PluginI18n(default_locale=default_locale)
    return load_plugin_i18n_from_dir(locales_dir, default_locale=default_locale)


def resolve_i18n_refs(value: object, i18n: PluginI18n, *, locale: str | None = None) -> object:
    if is_i18n_ref(value):
        ref = value
        key = str(ref.get(I18N_REF_KEY) or "")
        default = str(ref.get("default") or "")
        params_obj = ref.get("params")
        params = dict(params_obj) if isinstance(params_obj, Mapping) else {}
        return i18n.t(key, locale=locale, default=default, **params)
    if isinstance(value, Mapping):
        return {
            str(key): resolve_i18n_refs(item, i18n, locale=locale)
            for key, item in value.items()
            if isinstance(key, str)
        }
    if isinstance(value, list):
        return [resolve_i18n_refs(item, i18n, locale=locale) for item in value]
    return value


__all__ = [
    "I18N_REF_KEY",
    "PluginI18n",
    "interpolate_text",
    "is_i18n_ref",
    "load_plugin_i18n_from_dir",
    "load_plugin_i18n_from_meta",
    "locale_candidates",
    "resolve_i18n_refs",
    "tr",
]
