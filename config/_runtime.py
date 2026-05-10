# -*- coding: utf-8 -*-
"""
Runtime callbacks registered by higher layers.

``config`` lives at L0 (foundation) of the dependency stack and must not
import from ``utils`` (L1) or above — see ``scripts/check_module_layering.py``.
A few prompt-builders inside ``config/prompts/`` legitimately need to call
helpers that live higher up the stack (language detection, tokenize-aware
truncation). The classic dependency-inversion pattern:

* higher layers register the concrete implementation at app startup
  (e.g. ``app/main_server.py``)
* ``config`` callers use the resolvers below

If nothing is registered (unit tests / standalone tooling), each resolver
returns a sensible fallback rather than raising — config code never crashes
on a cold-imported module.

Wiring lives in ``app/runtime_bindings.py`` (called from every server
entrypoint at startup).
"""
from __future__ import annotations

from typing import Any, Callable, Optional

# ---------------------------------------------------------------------------
# Language resolution
# ---------------------------------------------------------------------------

_global_language_resolver: Optional[Callable[[], str]] = None
_steam_language_resolver: Optional[Callable[[], Optional[str]]] = None
_language_normalizer: Optional[Callable[..., str]] = None


def register_global_language_resolver(fn: Callable[[], str]) -> None:
    """Install the function returning the user-facing global language code.

    Concrete impl lives in ``utils.language_utils.get_global_language_full``.
    """
    global _global_language_resolver
    _global_language_resolver = fn


def register_steam_language_resolver(fn: Callable[[], Optional[str]]) -> None:
    """Install the function probing Steam for a language code (may return None).

    Concrete impl lives in ``utils.language_utils._get_steam_language``.
    """
    global _steam_language_resolver
    _steam_language_resolver = fn


def register_language_normalizer(fn: Callable[..., str]) -> None:
    """Install ``normalize_language_code(lang, format='short'|'full')``.

    Concrete impl lives in ``utils.language_utils.normalize_language_code``.
    """
    global _language_normalizer
    _language_normalizer = fn


def resolve_global_language(default: str = "zh-CN") -> str:
    """Return the global language code, or ``default`` if no resolver bound.

    Never raises — failures fall back to ``default`` so prompt builders keep
    working in test / scripted contexts.
    """
    fn = _global_language_resolver
    if fn is None:
        return default
    try:
        value = fn()
        return value if isinstance(value, str) and value else default
    except Exception:
        return default


def resolve_steam_language() -> Optional[str]:
    """Return Steam-reported language code, or ``None`` if unknown / unbound."""
    fn = _steam_language_resolver
    if fn is None:
        return None
    try:
        return fn()
    except Exception:
        return None


def normalize_language_code(lang: Any, format: str = "short") -> str:
    """Forward to the registered normalizer; return ``str(lang)`` if unbound.

    The registered normalizer accepts ``format='short'|'full'``.
    """
    fn = _language_normalizer
    if fn is None:
        return str(lang) if lang else ""
    try:
        return fn(lang, format=format)
    except Exception:
        return str(lang) if lang else ""


# ---------------------------------------------------------------------------
# Token-aware truncation (tiktoken-backed in utils.tokenize)
# ---------------------------------------------------------------------------

_truncate_to_tokens: Optional[Callable[[str, int], str]] = None


def register_truncate_to_tokens(fn: Callable[[str, int], str]) -> None:
    """Install ``truncate_to_tokens(text, max_tokens)``.

    Concrete impl lives in ``utils.tokenize.truncate_to_tokens``.
    """
    global _truncate_to_tokens
    _truncate_to_tokens = fn


def truncate_to_tokens(text: str, max_tokens: int) -> str:
    """Forward to the registered truncator. Falls back to a char-based
    cap (~4 chars per token) when nothing is bound, so prompt builders never
    return raw oversized text in test / cold-import contexts.
    """
    fn = _truncate_to_tokens
    if fn is not None:
        try:
            return fn(text, max_tokens)
        except Exception:
            pass
    # Fallback: rough char-budget cap — tiktoken o200k_base averages ~4 chars
    # per token across CJK + Latin mixed prompts. Conservative for safety.
    if not isinstance(text, str):
        return ""
    cap = max(0, int(max_tokens) * 4)
    return text if len(text) <= cap else text[:cap]
