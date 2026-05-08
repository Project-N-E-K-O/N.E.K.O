"""Testbench choke-point replica of the main-program avatar-interaction dedupe semantics.

This module reproduces the avatar-interaction dedupe semantics from
``main_logic/cross_server.py`` inside the testbench without importing across
packages. Per LESSONS_LEARNED L30 the strategy is copy + drift smoke: the
guarded region below is a byte-equivalent clone of the upstream source, and
``p25_avatar_dedupe_drift_smoke.py`` hashes both files to detect silent drift.

Copy-protected region (byte-equivalent, behaviour must not be modified):
- ``AVATAR_INTERACTION_MEMORY_DEDUPE_WINDOW_MS``
- ``_should_persist_avatar_interaction_memory``
Drift smoke compares the byte hash of the guarded block against
``main_logic/cross_server.py``; any behaviour change here will fail CI.

Testbench-only additions (not part of the copy contract, not aligned with the
main program, free to evolve here):
- ``_AvatarDedupeCache`` — adds LRU eviction, a soft entry cap, and a
  once-per-fill-cycle overflow notice hook used by ``external_events.py``.
"""

from __future__ import annotations

import time
from collections import OrderedDict
from typing import Callable, Optional


# ─────── BEGIN COPY (do not modify, see drift smoke) ───────
AVATAR_INTERACTION_MEMORY_DEDUPE_WINDOW_MS = 8000


def _should_persist_avatar_interaction_memory(
    cache: dict[str, dict[str, int | str]],
    memory_note: str,
    dedupe_key: str = '',
    dedupe_rank: int = 1,
) -> bool:
    note = str(memory_note or '').strip()
    if not note:
        return False

    key = str(dedupe_key or note).strip() or note
    try:
        rank = max(1, int(dedupe_rank))
    except (TypeError, ValueError):
        rank = 1

    now_ms = int(time.time() * 1000)
    expired_keys = [
        cache_key
        for cache_key, entry in cache.items()
        if now_ms - int((entry or {}).get('ts', 0) or 0) >= AVATAR_INTERACTION_MEMORY_DEDUPE_WINDOW_MS
    ]
    for cache_key in expired_keys:
        cache.pop(cache_key, None)

    previous = cache.get(key)
    if previous:
        previous_ts = int(previous.get('ts', 0) or 0)
        previous_rank = int(previous.get('rank', 1) or 1)
        if now_ms - previous_ts < AVATAR_INTERACTION_MEMORY_DEDUPE_WINDOW_MS and rank <= previous_rank:
            return False

    cache[key] = {
        'ts': now_ms,
        'rank': rank,
        'note': note,
    }
    return True
# ─────── END COPY ───────


# ─────────────────────────────────────────────────────────────
# testbench-only supplement (non-copy; free to evolve without drift smoke)
# ─────────────────────────────────────────────────────────────
class _AvatarDedupeCache:
    """Per-session avatar-dedupe state wrapper with LRU + soft cap + overflow notice.

    Wraps the ``dict`` that the copy-protected helper above mutates in place,
    so the semantic contract (the L30 dedupe rules) lives entirely in the
    protected region; this class only layers *operational* concerns on top:
    eviction order and a diagnostics notification when the cap is first hit.

    L27 Q1 (soft cap?): see ``_MAX_ENTRIES`` below.
    """

    # L27 Q1: soft cap so a high-frequency tester cannot grow this dict
    # unboundedly. 100 is intentionally generous — dedupe window is 8s, so a
    # tester producing >100 distinct keys within 8s is pathological.
    _MAX_ENTRIES: int = 100

    def __init__(self, on_full: Optional[Callable[[int], None]] = None) -> None:
        self._cache: OrderedDict[str, dict[str, int | str]] = OrderedDict()
        # Overflow hook is injected by the upper layer (``external_events.py``)
        # and typically routes into ``diagnostics_ops`` recording. Kept as a
        # plain callable so this module stays stdlib-only.
        self._on_full = on_full
        # L27 Q3: once-per-fill-cycle latch. We only want to notify diagnostics
        # the first time the cap trips; the latch rearms when **either**
        # (a) the tester manually clears the cache via ``clear()`` (L27 Q4),
        # **or** (b) the 8 s expiry sweep inside
        # ``_should_persist_avatar_interaction_memory`` drops the cache size
        # back below the cap. Aligns with the sibling ``diagnostics_store.py``
        # warn-once latch (P24 Day 10 §14.4 M4 — "Resets when the ring is
        # cleared or drops below the cap"). Pre-fix the latch was rearm-on-
        # ``clear()`` only, so a high-volume tester who paused for 8 s and
        # then triggered another 100+ keys would silently never see the
        # second AVATAR_DEDUPE_CACHE_FULL diagnostics entry (GH AI-review
        # issue, 2nd batch #2).
        self._full_notified: bool = False

    def should_persist(
        self,
        memory_note: str,
        dedupe_key: str = '',
        dedupe_rank: int = 1,
    ) -> bool:
        """Run the copy-protected dedupe helper, then apply LRU + overflow bookkeeping.

        Returns True when the event is "worth persisting" (the tester may write
        the memory pair to ``session.messages`` / ``recent.json``).

        L27 Q2 (behaviour at cap?): LRU — the oldest entry is dropped via
        ``popitem(last=False)`` once length exceeds ``_MAX_ENTRIES``. We keep
        popping in a ``while`` loop to stay correct even if ``_MAX_ENTRIES``
        is lowered at runtime between calls.
        """
        allowed = _should_persist_avatar_interaction_memory(
            self._cache, memory_note, dedupe_key, dedupe_rank
        )

        # The protected helper already mutated ``self._cache`` in place; we
        # must not write the entry again (would double-stamp ``ts``). All we
        # owe the cache is LRU ordering + cap enforcement.
        note = str(memory_note or '').strip()
        # Key derivation mirrors the protected helper's rule byte-for-byte so
        # LRU touches the same key the helper just inserted/updated. The
        # ``strip()`` matters: the helper also strips, so an un-stripped key
        # here would miss the entry and leave it stuck at the front of the LRU.
        key = str(dedupe_key or note).strip() or note
        # LRU semantics = "most-recently *admitted*". A rejected dedupe-window
        # repoke is **not** an admission and must not refresh the entry's LRU
        # position; otherwise a noisy zone repoked dryly forever would pin
        # itself at the tail and prevent legitimate other zones from filling
        # the cap (GH AI-review issue #3). The 8 s expiry sweep + 100 cap
        # bound the realistic damage today, but the semantic divergence
        # surfaces under ≥100 distinct admitted zones + a stuck repoke key.
        if allowed and key and key in self._cache:
            self._cache.move_to_end(key, last=True)

        while len(self._cache) > self._MAX_ENTRIES:
            self._cache.popitem(last=False)
            if not self._full_notified and self._on_full is not None:
                try:
                    self._on_full(self._MAX_ENTRIES)
                except Exception:  # noqa: BLE001
                    # on_full is diagnostics-only; a faulty recorder must not
                    # break the dedupe path, so we swallow everything here.
                    pass
                self._full_notified = True

        # L27 Q3 second-half rearm: if the expiry sweep at the top of the
        # protected helper dropped the cache **strictly below** the cap (i.e.
        # there is real headroom now, not just the popitem aftermath that
        # sits at exactly _MAX_ENTRIES), unlatch so the next fill cycle is
        # allowed to notify diagnostics again. Mirrors diagnostics_store.py's
        # ``_RING_FULL_NOTICE_FIRED`` rearm-when-below-cap behaviour. Strict
        # ``<`` (not ``<=``) is deliberate: at == _MAX_ENTRIES we are still
        # in the "just-full, may pop on next admit" steady state — counting
        # that as headroom would let one bouncy admit/evict cycle re-fire
        # the notice every call and spam the diagnostics ring.
        if self._full_notified and len(self._cache) < self._MAX_ENTRIES:
            self._full_notified = False

        return allowed

    def snapshot(self) -> dict[str, dict[str, int | str]]:
        """Return a shallow copy of the cache for ``GET /api/session/external-event/dedupe-info``.

        Inner value dicts (``ts`` / ``rank`` / ``note``) are shallow-copied;
        the tester must treat them as read-only.
        """
        return {k: dict(v) for k, v in self._cache.items()}

    def clear(self) -> None:
        """L27 Q4: tester-visible manual reset used by ``POST /api/session/external-event/dedupe-reset``.

        Also rearms the once-per-fill-cycle overflow notice so the next fill
        cycle after a manual reset is allowed to notify diagnostics again.
        """
        self._cache.clear()
        self._full_notified = False

    def __len__(self) -> int:
        return len(self._cache)


__all__ = [
    "AVATAR_INTERACTION_MEMORY_DEDUPE_WINDOW_MS",
    "_should_persist_avatar_interaction_memory",
    "_AvatarDedupeCache",
]
