"""轻量 i18n 模块：从 locales/*.json 加载翻译。"""

from __future__ import annotations

import json
import time
from collections import OrderedDict
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

_DEFAULT_LOCALE = "zh-CN"
_SUPPORTED_LOCALES = ("zh-CN", "zh-TW", "en")
_CACHE_MAX_ENTRIES = 32


class LRUCache:
    """简易 LRU 缓存，带 TTL 和容量上限。"""

    def __init__(self, max_size: int = _CACHE_MAX_ENTRIES):
        self._store: OrderedDict[str, Tuple[Any, float]] = OrderedDict()
        self._max_size = max(1, max_size)

    def get(self, key: str, ttl: float) -> Any:
        item = self._store.get(key)
        if item is None:
            return None
        data, ts = item
        if (time.time() - ts) >= ttl:
            self._store.pop(key, None)
            return None
        self._store.move_to_end(key)
        return data

    def put(self, key: str, data: Any) -> None:
        self._store[key] = (data, time.time())
        self._store.move_to_end(key)
        while len(self._store) > self._max_size:
            self._store.popitem(last=False)


class I18n:
    """从 locales/*.json 加载翻译，支持 dot-path 取值和 {key} 模板插值。"""

    def __init__(self, locales_dir: Path, default: str = _DEFAULT_LOCALE):
        self._bundles: Dict[str, Dict[str, Any]] = {}
        self._default = default
        self._locale = default
        for code in _SUPPORTED_LOCALES:
            fp = locales_dir / f"{code}.json"
            if fp.exists():
                with open(fp, "r", encoding="utf-8") as f:
                    self._bundles[code] = json.load(f)

    @property
    def locale(self) -> str:
        return self._locale

    def set_locale(self, code: str) -> None:
        normalized = self._normalize(code)
        if normalized in self._bundles:
            self._locale = normalized

    def _normalize(self, code: str) -> str:
        if not code:
            return self._default
        c = code.strip().replace("_", "-")
        if c in self._bundles:
            return c
        lower = c.lower()
        for key in self._bundles:
            if key.lower() == lower:
                return key
        prefix = lower.split("-")[0]
        for key in self._bundles:
            if key.lower().startswith(prefix):
                return key
        return self._default

    def t(self, path: str, locale: Optional[str] = None, **kwargs: Any) -> str:
        for code in self._resolve_chain(locale):
            bundle = self._bundles.get(code)
            if bundle is None:
                continue
            val = self._get_nested(bundle, path)
            if val is not None:
                text = str(val)
                if kwargs:
                    try:
                        text = text.format(**kwargs)
                    except (KeyError, IndexError):
                        pass
                return text
        return path

    def _resolve_chain(self, locale: Optional[str]) -> List[str]:
        chain: List[str] = []
        if locale:
            n = self._normalize(locale)
            chain.append(n)
        if self._locale not in chain:
            chain.append(self._locale)
        if self._default not in chain:
            chain.append(self._default)
        return chain

    @staticmethod
    def _get_nested(d: Dict[str, Any], path: str) -> Any:
        cur: Any = d
        for p in path.split("."):
            if isinstance(cur, dict):
                cur = cur.get(p)
            else:
                return None
        return cur
