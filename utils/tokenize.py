# -*- coding: utf-8 -*-
"""
Token counting helpers for memory-evidence render budget (RFC §3.6.6).

Why a dedicated module:
- `tiktoken.get_encoding(...)` reads ~1.5 MB encoding files from disk on
  first call. We cache the resulting `Encoding` instance per encoding name
  so subsequent calls (~thousands per render) are pure CPU.
- `Encoding.encode` releases the GIL inside the Rust core (tiktoken 0.5+),
  but the FastAPI event loop still stalls if we call it directly from a
  coroutine. The async twin (`acount_tokens`) hops to `asyncio.to_thread`
  to keep the loop responsive.
- Packaging products (Nuitka / PyInstaller) sometimes ship without the
  `o200k_base.tiktoken` data file. The first time we fall back to a
  heuristic counter we emit a single warning so operators notice — RFC §8
  S13 mandates "no silent heuristic fallback in shipped binaries".
"""
from __future__ import annotations

import asyncio
import logging
import math

from config import PERSONA_RENDER_ENCODING

logger = logging.getLogger(__name__)

# Encoder cache keyed by encoding name (e.g. "o200k_base"). Values are
# either the loaded `tiktoken.Encoding` instance or `None` if loading
# failed permanently — caching the failure avoids retrying disk IO on
# every render.
_ENCODERS: dict = {}

# One-shot warning latch (per process). Set on the first heuristic
# fallback so we don't spam the log on subsequent calls.
_FALLBACK_WARNED = False

# Bump this string whenever the heuristic formula in
# `_count_tokens_heuristic` changes — the persona/reflection token-count
# cache keys off `tokenizer_identity()`, and a formula change must
# invalidate old heuristic-cached counts. tiktoken identity is keyed by
# the `tiktoken:<encoding>` pair, which already changes automatically if
# someone flips PERSONA_RENDER_ENCODING.
_HEURISTIC_VERSION = "v1"


def _get_encoder(encoding: str):
    """Return the cached `tiktoken.Encoding` for `encoding`, or `None`
    if tiktoken / the data file is unavailable. Emits a one-shot warning
    on the first failure so packaging issues surface in logs.
    """
    global _FALLBACK_WARNED
    if encoding in _ENCODERS:
        return _ENCODERS[encoding]
    try:
        import tiktoken
        enc = tiktoken.get_encoding(encoding)
        _ENCODERS[encoding] = enc
        return enc
    except Exception as e:  # noqa: BLE001 — any failure → fallback path
        # Cache the failure so we don't retry per-call. ``None`` is a
        # legal sentinel here because the caller treats it as "use
        # heuristic".
        _ENCODERS[encoding] = None
        if not _FALLBACK_WARNED:
            logger.warning(
                "tiktoken 不可用 (%s)，降级到启发式 token 计数；如果这是"
                "打包产物，请检查 Nuitka/PyInstaller 配置是否包含 tiktoken "
                "encoding 文件",
                e,
            )
            _FALLBACK_WARNED = True
        return None


def _count_tokens_heuristic(text: str) -> int:
    """Cheap character-class fallback when tiktoken is unavailable.

    The constants are chosen to **over-estimate** rather than under: the
    render budget is a soft cap and rendering a few entries less is
    preferable to silently exceeding the model context window.

    - CJK (Han / Kana / Hangul) → 1.5 tokens / char
    - Other (latin / digits / punct) → 0.25 tokens / char (≈ 4 char per
      token, matches GPT tokenizer ballpark on English prose)
    """
    if not text:
        # Empty stays 0 — both for math sanity and because callers
        # (count_tokens / acount_tokens) already short-circuit empty.
        # Defensive double-check kept here so direct callers of the
        # heuristic (tests, future callsites) get the same contract.
        return 0
    cjk = sum(
        1 for c in text
        if '\u4e00' <= c <= '\u9fff'
        or '\u3040' <= c <= '\u30ff'
        or '\uac00' <= c <= '\ud7af'
    )
    non_cjk = len(text) - cjk
    # Floor of 1 for non-empty text: int() truncated short latin strings
    # (e.g. "ok" → 0.5 → 0), which made score-trim treat them as free
    # and bypass the budget. ceil + clamp avoids that without
    # under-counting longer text.
    return max(1, math.ceil(cjk * 1.5 + non_cjk * 0.25))


def count_tokens(text: str, encoding: str = PERSONA_RENDER_ENCODING) -> int:
    """Synchronous token count. Used by tests and migration scripts.

    Production render path uses `acount_tokens` to keep the event loop
    responsive — see module docstring.
    """
    if not text:
        return 0
    enc = _get_encoder(encoding)
    if enc is None:
        return _count_tokens_heuristic(text)
    return len(enc.encode(text))


async def acount_tokens(
    text: str, encoding: str = PERSONA_RENDER_ENCODING,
) -> int:
    """Async twin of `count_tokens` — runs the (Rust-backed but
    GIL-stalling-from-the-loop's-POV) encode in a worker thread."""
    if not text:
        return 0
    return await asyncio.to_thread(count_tokens, text, encoding)


def tokenizer_identity(encoding: str = PERSONA_RENDER_ENCODING) -> str:
    """Short fingerprint of the counter that `count_tokens` currently
    uses, for use as part of a cache key.

    Returns:
        - ``"tiktoken:<encoding>"`` when the real tiktoken encoder is
          loaded (or can be loaded on first call and cached)
        - ``"heuristic:<version>"`` when we're running the character-
          class fallback (tiktoken missing, encoding data file missing,
          etc. — same conditions as `_get_encoder` returning None)

    The key is bucketed per `encoding` so a deployment that changes
    ``PERSONA_RENDER_ENCODING`` also invalidates the old cache
    automatically. The heuristic version string is bumped whenever
    `_count_tokens_heuristic`'s formula changes.

    Cheap: piggybacks on the `_ENCODERS` cache, so after the first
    call it's a single dict lookup.
    """
    enc = _get_encoder(encoding)
    if enc is None:
        return f"heuristic:{_HEURISTIC_VERSION}"
    return f"tiktoken:{encoding}"


def _reset_fallback_warned_for_tests() -> None:
    """Test-only helper: reset the one-shot warning latch so each test can
    assert the warning fires on first heuristic use without leaking state.
    Not part of the public API."""
    global _FALLBACK_WARNED
    _FALLBACK_WARNED = False
    _ENCODERS.clear()
