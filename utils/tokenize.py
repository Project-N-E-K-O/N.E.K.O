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
from utils.cjk import count_cjk_chars, is_cjk_char

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
    cjk = count_cjk_chars(text)
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


def truncate_to_tokens(
    text: str,
    max_tokens: int,
    encoding: str = PERSONA_RENDER_ENCODING,
) -> str:
    """Return ``text`` truncated to at most ``max_tokens`` tokens.

    If ``text`` already fits, it is returned unchanged. Otherwise the
    tiktoken-encoded token sequence is sliced and decoded back to a
    string. Decoding may yield a string slightly shorter than the original
    cut boundary because BPE merges can split a multi-byte char across
    tokens — that is acceptable for our budget-trim use cases (LLM prompt
    truncation, log truncation): we'd rather drop a partial char than
    leak past the limit.

    Heuristic fallback: when tiktoken is unavailable, we under-estimate
    `max_tokens` worth of chars by inverting the same per-class density
    used in `_count_tokens_heuristic`, scanning prefix characters until
    the running token estimate hits the cap. We never over-count; if the
    heuristic mis-classifies the budget will be slightly tighter than
    intended, never looser.
    """
    if not text or max_tokens <= 0:
        return "" if max_tokens <= 0 else text
    enc = _get_encoder(encoding)
    if enc is None:
        return _truncate_to_tokens_heuristic(text, max_tokens)
    tokens = enc.encode(text)
    if len(tokens) <= max_tokens:
        return text
    return enc.decode(tokens[:max_tokens])


async def atruncate_to_tokens(
    text: str,
    max_tokens: int,
    encoding: str = PERSONA_RENDER_ENCODING,
) -> str:
    """Async twin of `truncate_to_tokens` — see module docstring for why
    the encode hop into a worker thread matters on the FastAPI loop."""
    if not text or max_tokens <= 0:
        return "" if max_tokens <= 0 else text
    return await asyncio.to_thread(truncate_to_tokens, text, max_tokens, encoding)


_SENTENCE_END_CHARS = '.!?。！？…\n'
"""Sentence-final terminators for `truncate_to_last_sentence_end`. Commas
and other mid-sentence punctuation are intentionally excluded — keeping
text up to a comma would leave the prefix mid-thought; we'd rather drop
the whole partial sentence than ship a half-finished clause. Newline is
included so paragraph-level cuts are also valid sentence boundaries."""


def truncate_to_last_sentence_end(text: str) -> str:
    """Return the prefix of ``text`` up to and including the last
    sentence-terminating punctuation mark (one of ``_SENTENCE_END_CHARS``).

    Returns the original ``text`` unchanged if it already ends with a
    sentence terminator. Returns ``""`` if no terminator is present
    (caller should fall through to the discarded-overflow UX).

    Use case: token-cap (``max_completion_tokens``) truncation can chop
    LLM output mid-sentence. Apply this helper after the cap to reset
    the boundary to the last clean sentence end.
    """
    if not text:
        return text
    if text[-1] in _SENTENCE_END_CHARS:
        return text
    last = max((text.rfind(ch) for ch in _SENTENCE_END_CHARS), default=-1)
    if last < 0:
        return ""
    return text[:last + 1]


def truncate_head_tail_tokens(
    text: str,
    head_tokens: int,
    tail_tokens: int,
    *,
    separator: str = "…[省略中段]…",
    encoding: str = PERSONA_RENDER_ENCODING,
) -> str:
    """Truncate ``text`` keeping head/tail tokens, joined by ``separator``.

    Use case: long user / assistant messages where both opening context
    (greeting / topic) and closing intent (question / conclusion) carry
    semantic weight and a plain ``[:N]`` cut would drop the most important
    half. If ``text`` already fits within ``head_tokens + tail_tokens``,
    it is returned unchanged.

    Token budget semantics: the returned string's total token count is
    guaranteed ``≤ head_tokens + tail_tokens``. The separator is paid for
    out of the head/tail allocation (head shrinks first, then tail) so
    callers passing ``head=tail=N`` can trust the output never exceeds
    ``2N`` tokens — earlier behaviour silently let ``count_tokens(separator)``
    leak past the budget.

    Negative arguments are clamped to 0 so misconfiguration can't bypass
    the budget by passing a sentinel like ``-1``.
    """
    if not text:
        return text
    head_tokens = max(0, head_tokens)
    tail_tokens = max(0, tail_tokens)
    total = head_tokens + tail_tokens
    if total <= 0:
        return ""
    if count_tokens(text, encoding) <= total:
        return text
    # Reserve room for the separator out of head/tail so the final
    # `head + sep + tail` never exceeds `total`. Bias the deduction
    # toward `head` first (head_alloc shrinks first, then tail) — keeps
    # tail (last sentence / question) intact when budgets are tight.
    sep_tokens = count_tokens(separator, encoding) if separator else 0
    if sep_tokens >= total:
        # Pathological: budget can't even fit the separator. Degrade to
        # a plain head-only truncation (better than returning sep alone).
        return truncate_to_tokens(text, total, encoding)
    head_alloc = max(0, head_tokens - sep_tokens)
    tail_alloc = tail_tokens
    if head_alloc + tail_alloc + sep_tokens > total:
        # Sanity: head_alloc was already shaved; if rounding misbehaved
        # take the rest from tail.
        tail_alloc = max(0, total - head_alloc - sep_tokens)
    enc = _get_encoder(encoding)
    if enc is None:
        # Heuristic fallback: cut by char position using same weighting
        # as `_count_tokens_heuristic`. Approximate but bounded.
        head_str = _truncate_to_tokens_heuristic(text, head_alloc) if head_alloc else ""
        # For tail, scan from the end using the same logic. The for/else
        # covers both branches: if the budget is exceeded mid-scan we set
        # cut_idx via the break path; if the entire string fits within
        # tail_alloc (rare here since we already returned early when
        # text fits in head+tail) the else clause sets cut_idx = 0.
        if tail_alloc <= 0:
            tail_str = ""
        else:
            running = 0.0
            for i in range(len(text) - 1, -1, -1):
                weight = 1.5 if is_cjk_char(text[i]) else 0.25
                if math.ceil(running + weight) > tail_alloc:
                    cut_idx = i + 1
                    break
                running += weight
            else:
                cut_idx = 0
            tail_str = text[cut_idx:]
        if not head_str and not tail_str:
            return ""
        return f"{head_str}{separator}{tail_str}"
    tokens = enc.encode(text)
    head_tok = tokens[:head_alloc] if head_alloc else []
    tail_tok = tokens[-tail_alloc:] if tail_alloc > 0 else []
    head_str = enc.decode(head_tok) if head_tok else ""
    tail_str = enc.decode(tail_tok) if tail_tok else ""
    return f"{head_str}{separator}{tail_str}"


async def atruncate_head_tail_tokens(
    text: str,
    head_tokens: int,
    tail_tokens: int,
    *,
    separator: str = "…[省略中段]…",
    encoding: str = PERSONA_RENDER_ENCODING,
) -> str:
    """Async twin of `truncate_head_tail_tokens`."""
    if not text:
        return text
    return await asyncio.to_thread(
        truncate_head_tail_tokens,
        text,
        head_tokens,
        tail_tokens,
        separator=separator,
        encoding=encoding,
    )


def _truncate_to_tokens_heuristic(text: str, max_tokens: int) -> str:
    """Heuristic prefix scan that mirrors `_count_tokens_heuristic`'s
    weighting (CJK 1.5 / non-CJK 0.25). Walks the string once and stops
    just before the running estimate would exceed `max_tokens`."""
    running = 0.0
    for i, c in enumerate(text):
        weight = 1.5 if is_cjk_char(c) else 0.25
        if math.ceil(running + weight) > max_tokens:
            return text[:i]
        running += weight
    return text


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
