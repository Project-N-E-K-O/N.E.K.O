"""Bounded session-scoped memory of phrases THE ROOM made, and when NEKO may
call one back.

Why this exists
---------------
Every other recall mechanism in this plugin points one way: do not repeat.
``spent_output_family``, the recent topic/beat/axis/shape windows and the host
BM25 guard all suppress material that appeared in the last few outputs. That is
correct for what it covers, but it leaves no state in which something is *old
enough to be worth saying again*: a motif is either too fresh (suppressed) or
has fallen out of a fixed-length deque and ceased to exist.

A live room's most valuable material is exactly the thing that survives that
gap. `docs/live-effect-literature-research-report.md` puts it as: a one-off is a
laugh, something that can be brought back later is an asset.

The comedy-callback literature adds three constraints that make "say it again"
work rather than read as parroting, and they are the three fields this module
tracks that nothing else does:

* a callback needs a GAP — referenced again immediately, it is just repetition,
  so a confirmed ritual stays locked for ``RITUAL_CALLBACK_MIN_GAP_SECONDS``;
* the strongest callbacks RECONTEXTUALIZE — so a callback is refused when the
  room context key has not changed since the last payoff;
* running gags wear out — so each ritual retires after ``RITUAL_MAX_USES``.

Promotion is deliberately strict: the room must produce the same signal in two
separate windows, each backed by at least two distinct viewers (the existing
`RoomTopicContext` repeated-signal detector already enforces the viewer rule, so
one person spamming can never mint a ritual). NEKO's own output never promotes
anything — a cat repeating herself is not a room ritual.

Boundaries: session-scoped and bounded, no timer, no model call, no network, no
persistence. It never schedules a turn, never grants speaking authority, and
never bypasses selection, Safety Guard, or the dispatcher. Like the RoomPulse
representative example, the bounded phrase may reach the prompt but must never
reach status or audit.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable


RITUAL_MEMORY_VERSION = 0
# Tracked rituals per session. Small on purpose: a room that "has eight running
# jokes" has none, and the cap bounds both memory and prompt competition.
RITUAL_MAX_TRACKED = 8
# A repeat inside this window is the same collective moment, not the room
# bringing the bit back. Must exceed RoomTopicContext's 45s candidate window so
# one burst cannot self-confirm.
RITUAL_CONFIRM_MIN_GAP_SECONDS = 60.0
# Confirmation needs the room to produce the signal in this many separate
# windows ("the second occurrence").
RITUAL_CONFIRM_OBSERVATIONS = 2
# Minimum distinct viewers behind one observation.
RITUAL_MIN_VIEWER_SUPPORT = 2
# The forgetting gap before a confirmed ritual may be paid off again.
RITUAL_CALLBACK_MIN_GAP_SECONDS = 180.0
# Retirement: running gags die from over-use, not from age.
RITUAL_MAX_USES = 3
# A ritual nobody has echoed for this long stops being current.
RITUAL_STALE_SECONDS = 900.0
# Stored phrase budget. Short by design: a ritual is a token, not a quote.
RITUAL_PHRASE_MAX_CHARS = 24
RITUAL_PROMPT_MAX_CHARS = 90

_RITUAL_KINDS = {"reaction", "content"}


@dataclass(frozen=True, slots=True)
class RitualPrompt:
    text: str = ""
    reason: str = "no_ritual"
    key: str = ""

    @property
    def characters(self) -> int:
        return len(self.text)


class _Ritual:
    __slots__ = (
        "key", "kind", "phrase", "first_seen_at", "last_seen_at",
        "observations", "peak_support", "uses", "last_used_at",
        "last_used_context",
    )

    def __init__(self, key: str, kind: str, phrase: str, now: float, support: int) -> None:
        self.key = key
        self.kind = kind
        self.phrase = phrase
        self.first_seen_at = now
        self.last_seen_at = now
        self.observations = 1
        self.peak_support = support
        self.uses = 0
        self.last_used_at = 0.0
        self.last_used_context = ""

    @property
    def confirmed(self) -> bool:
        return self.observations >= RITUAL_CONFIRM_OBSERVATIONS

    @property
    def retired(self) -> bool:
        return self.uses >= RITUAL_MAX_USES


class RitualMemory:
    """Session-local ritual store; pure state, no scheduling or output."""

    def __init__(self, *, now: Callable[[], float]) -> None:
        self._now = now
        self.reset()

    def reset(self) -> None:
        self._rituals: dict[str, _Ritual] = {}
        self._confirmed_count = 0
        self._callback_offers = 0
        self._callback_uses = 0
        self._last_skip_reason = ""

    # ── observation ─────────────────────────────────────────────────────
    def observe_repeated_signal(
        self,
        *,
        kind: str,
        support: Any,
        phrase: str,
    ) -> bool:
        """Record one collective repeat reported by ``RoomTopicContext``.

        Returns True when this observation confirmed a new ritual. Callers pass
        the detector's own output; this module adds only the across-window
        "did the room come back to it" judgement.
        """
        kind = str(kind or "").strip()
        if kind not in _RITUAL_KINDS:
            return False
        try:
            support_count = int(support)
        except (TypeError, ValueError):
            return False
        if support_count < RITUAL_MIN_VIEWER_SUPPORT:
            return False
        key = self._normalize(phrase)
        if not key:
            return False

        now = self._now()
        self._expire(now)
        ritual = self._rituals.get(key)
        if ritual is None:
            if len(self._rituals) >= RITUAL_MAX_TRACKED:
                self._evict_one()
            self._rituals[key] = _Ritual(
                key=key,
                kind=kind,
                phrase=key[:RITUAL_PHRASE_MAX_CHARS],
                now=now,
                support=support_count,
            )
            return False

        ritual.peak_support = max(ritual.peak_support, support_count)
        if now - ritual.last_seen_at < RITUAL_CONFIRM_MIN_GAP_SECONDS:
            # Same collective moment still running — refresh recency only, so a
            # single long burst cannot confirm itself.
            ritual.last_seen_at = now
            return False
        was_confirmed = ritual.confirmed
        ritual.observations += 1
        ritual.last_seen_at = now
        if not was_confirmed and ritual.confirmed:
            self._confirmed_count += 1
            return True
        return False

    # ── callback offer ──────────────────────────────────────────────────
    def callback_for_context(self, context_key: str) -> RitualPrompt:
        """Offer at most one ritual worth paying off in ``context_key``.

        The caller decides whether to use it and must report back through
        :meth:`mark_used`; an offer alone changes nothing.
        """
        now = self._now()
        self._expire(now)
        context = str(context_key or "").strip()[:48]

        best: _Ritual | None = None
        reason = "no_ritual"
        for ritual in self._rituals.values():
            if not ritual.confirmed:
                reason = self._weaker(reason, "unconfirmed")
                continue
            if ritual.retired:
                reason = self._weaker(reason, "retired")
                continue
            if ritual.uses and now - ritual.last_used_at < RITUAL_CALLBACK_MIN_GAP_SECONDS:
                # Too soon: this would read as repetition, not a callback.
                reason = self._weaker(reason, "too_soon")
                continue
            if ritual.uses and context and ritual.last_used_context == context:
                # Same context as the last payoff — a callback has to land
                # somewhere new to be a callback.
                reason = self._weaker(reason, "same_context")
                continue
            if best is None or self._better(ritual, best):
                best = ritual
        if best is None:
            self._last_skip_reason = reason
            return RitualPrompt(reason=reason)

        text = self._render(best)
        if not text:
            self._last_skip_reason = "empty"
            return RitualPrompt(reason="empty")
        self._callback_offers += 1
        self._last_skip_reason = ""
        return RitualPrompt(text=text, reason="offered", key=best.key)

    def mark_used(self, key: str, context_key: str = "") -> bool:
        """Record that NEKO actually paid a ritual off, starting its gap."""
        ritual = self._rituals.get(self._normalize(key))
        if ritual is None:
            return False
        ritual.uses += 1
        ritual.last_used_at = self._now()
        ritual.last_used_context = str(context_key or "").strip()[:48]
        self._callback_uses += 1
        return True

    # ── projection ──────────────────────────────────────────────────────
    def status(self) -> dict[str, Any]:
        """Privacy-safe diagnostics: counts and reason codes only.

        The ritual phrase itself is prompt-only and must never appear here or
        in audit, matching the RoomPulse representative-example boundary.
        """
        now = self._now()
        confirmed = [r for r in self._rituals.values() if r.confirmed]
        return {
            "ritual_memory_version": RITUAL_MEMORY_VERSION,
            "ritual_tracked_count": len(self._rituals),
            "ritual_confirmed_count": len(confirmed),
            "ritual_retired_count": sum(1 for r in confirmed if r.retired),
            "ritual_callback_offers": self._callback_offers,
            "ritual_callback_uses": self._callback_uses,
            "ritual_last_skip_reason": self._last_skip_reason or "",
            "ritual_oldest_confirmed_age_seconds": (
                round(max((now - r.first_seen_at for r in confirmed), default=0.0), 1)
            ),
        }

    def is_confirmed_ritual(self, phrase: str) -> bool:
        """True when a phrase is an established room ritual.

        Used by anti-repeat consumers: an established ritual returning after its
        gap is a callback, not the drift-back-to-an-old-bit that the recent
        material windows exist to prevent.
        """
        ritual = self._rituals.get(self._normalize(phrase))
        return bool(ritual and ritual.confirmed and not ritual.retired)

    # ── internals ───────────────────────────────────────────────────────
    def _render(self, ritual: _Ritual) -> str:
        phrase = ritual.phrase.strip()
        if not phrase:
            return ""
        if ritual.kind == "reaction":
            body = f"房间反复出现的反应「{phrase}」"
        else:
            body = f"房间自己玩起来的梗「{phrase}」"
        text = f"[room ritual] {body}，可以自然回收一次，换个说法，不要解释它的来历。"
        return text[:RITUAL_PROMPT_MAX_CHARS]

    def _expire(self, now: float) -> None:
        stale = [
            key for key, ritual in self._rituals.items()
            if now - ritual.last_seen_at > RITUAL_STALE_SECONDS
        ]
        for key in stale:
            ritual = self._rituals.pop(key, None)
            if ritual is not None and ritual.confirmed:
                self._confirmed_count = max(0, self._confirmed_count - 1)

    def _evict_one(self) -> None:
        # Drop the least valuable slot: prefer retired, then unconfirmed, then
        # least recently echoed. A live confirmed ritual outranks a fresh
        # unconfirmed candidate.
        victim = min(
            self._rituals.values(),
            key=lambda r: (not r.retired, r.confirmed, r.last_seen_at),
        )
        self._rituals.pop(victim.key, None)

    @staticmethod
    def _better(candidate: _Ritual, current: _Ritual) -> bool:
        # Prefer never-used, then wider viewer support, then more observations.
        return (
            (candidate.uses, -candidate.peak_support, -candidate.observations)
            < (current.uses, -current.peak_support, -current.observations)
        )

    @staticmethod
    def _weaker(current: str, candidate: str) -> str:
        # Report the most informative blocker: an existing ritual that is merely
        # cooling down explains more than "nothing confirmed yet".
        order = {"no_ritual": 0, "unconfirmed": 1, "retired": 2, "same_context": 3, "too_soon": 4}
        return candidate if order.get(candidate, 0) > order.get(current, 0) else current

    @staticmethod
    def _normalize(phrase: Any) -> str:
        text = str(phrase or "").strip()
        if not text:
            return ""
        dense = "".join(ch for ch in text if not ch.isspace())
        return dense.casefold()[:RITUAL_PHRASE_MAX_CHARS]
