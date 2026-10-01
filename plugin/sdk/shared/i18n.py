from __future__ import annotations

import copy
import json
import os
import re
import stat
import threading
from collections import OrderedDict
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
    return _load_json_file_checked(path)[0]


def _load_json_file_checked(path: Path) -> tuple[dict[str, object], bool]:
    """Return ``(bundle, readable)``; ``readable`` is False on an I/O error."""
    try:
        if not path.is_file() or path.stat().st_size > 512 * 1024:
            return {}, True
        text = path.read_text(encoding="utf-8")
    except UnicodeError:
        # Invalid UTF-8 is a property of the file, not a transient failure:
        # skip this locale like malformed JSON and keep the others.
        return {}, True
    except OSError:
        return {}, False
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        return {}, True
    return (dict(payload) if isinstance(payload, Mapping) else {}), True


# Parsed locale bundles keyed by resolved locales dir. Each entry is validated
# against a stat signature of the ``*.json`` files (names, type, mtime_ns,
# ctime_ns, size), so edits and hot reload are picked up without explicit
# invalidation. Installs and rollbacks can copy files with preserved
# timestamps, so they also call ``clear_plugin_i18n_cache``. Callers always
# receive fresh copies.
_BUNDLE_CACHE_MAX_ENTRIES = 256
_Signature = tuple[tuple[str, int, int, int, int], ...]
_CachedBundle = tuple[dict[str, object], bool]
_bundle_cache: OrderedDict[str, tuple[_Signature, dict[str, _CachedBundle]]] = OrderedDict()
_bundle_cache_lock = threading.Lock()
_IMMUTABLE_JSON_TYPES = (str, int, float, bool, type(None))


def _scan_locale_files(locales_dir: Path) -> tuple[_Signature, list[Path]] | None:
    """Return the stat signature and paths of ``*.json`` files, sorted like
    ``sorted(locales_dir.glob("*.json"))``. ``None`` if the dir is unreadable."""
    entries: list[tuple[str, Path, int, int, int, int]] = []
    try:
        with os.scandir(locales_dir) as it:
            for entry in it:
                if not os.path.normcase(entry.name).endswith(".json"):
                    continue
                try:
                    st = entry.stat()
                except OSError:
                    # Dangling symlink or file removed mid-scan: skip it like
                    # the old glob + is_file() path did, keep other locales.
                    continue
                entries.append((os.path.normcase(entry.name), Path(entry.path), stat.S_IFMT(st.st_mode), st.st_mtime_ns, st.st_ctime_ns, st.st_size))
    except OSError:
        return None
    entries.sort(key=lambda item: item[0])
    signature = tuple(
        (path.name, mode, mtime_ns, ctime_ns, size)
        for _, path, mode, mtime_ns, ctime_ns, size in entries
    )
    return signature, [item[1] for item in entries]


def _read_locale_bundles(paths: list[Path]) -> tuple[dict[str, dict[str, object]], bool]:
    """Return ``(messages, all_readable)``."""
    messages: dict[str, dict[str, object]] = {}
    all_readable = True
    for path in paths:
        locale = path.stem.strip()
        if not locale:
            continue
        bundle, readable = _load_json_file_checked(path)
        all_readable = all_readable and readable
        if bundle:
            messages[locale] = bundle
    return messages, all_readable


def _copy_cached_bundles(cached: Mapping[str, _CachedBundle]) -> dict[str, dict[str, object]]:
    return {
        locale: dict(bundle) if flat else copy.deepcopy(bundle)
        for locale, (bundle, flat) in cached.items()
    }


def clear_plugin_i18n_cache() -> None:
    with _bundle_cache_lock:
        _bundle_cache.clear()


def _load_bundles_cached(locales_dir: Path) -> dict[str, dict[str, object]]:
    """``locales_dir`` must already be resolved; it is used as the cache key."""
    snapshot = _scan_locale_files(locales_dir)
    if snapshot is None:
        return {}
    signature, paths = snapshot
    cache_key = str(locales_dir)

    with _bundle_cache_lock:
        cached = _bundle_cache.get(cache_key)
        if cached is not None and cached[0] == signature:
            _bundle_cache.move_to_end(cache_key)
            return _copy_cached_bundles(cached[1])

    # Read outside the lock. The signature was taken before reading, so a file
    # rewritten mid-read leaves a stale signature and is reloaded next call.
    messages, all_readable = _read_locale_bundles(paths)
    if not all_readable:
        # A read error (e.g. a transient permission or sharing violation) can
        # clear up without changing the stat signature, so don't cache it.
        return messages
    stored: dict[str, _CachedBundle] = {
        locale: (
            copy.deepcopy(bundle),
            all(isinstance(value, _IMMUTABLE_JSON_TYPES) for value in bundle.values()),
        )
        for locale, bundle in messages.items()
    }
    with _bundle_cache_lock:
        _bundle_cache[cache_key] = (signature, stored)
        _bundle_cache.move_to_end(cache_key)
        while len(_bundle_cache) > _BUNDLE_CACHE_MAX_ENTRIES:
            _bundle_cache.popitem(last=False)
    return messages


def load_plugin_i18n_from_dir(locales_dir: Path, *, default_locale: str = DEFAULT_LOCALE) -> PluginI18n:
    try:
        resolved = locales_dir.resolve()
    except (OSError, RuntimeError):
        return PluginI18n(default_locale=default_locale)
    return PluginI18n(_load_bundles_cached(resolved), default_locale=default_locale)


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
    return PluginI18n(_load_bundles_cached(locales_dir), default_locale=default_locale)


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
    "clear_plugin_i18n_cache",
    "interpolate_text",
    "is_i18n_ref",
    "load_plugin_i18n_from_dir",
    "load_plugin_i18n_from_meta",
    "locale_candidates",
    "resolve_i18n_refs",
    "tr",
]
