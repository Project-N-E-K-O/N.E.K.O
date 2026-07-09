# Copyright 2025-2026 Project N.E.K.O. Team
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Master (user) emotion tracker — instantaneous valence-arousal reading.

The single source of truth for "how the user feels right now". One instance
per session, owned alongside ``UserActivityTracker`` / ``FocusScorer``.

  * Fed asynchronously from each real user turn (``_note_user_turn``),
    throttled by ``MASTER_EMOTION_MIN_INTERVAL_SEC`` so rapid messages don't
    hammer the emotion-tier model.
  * Produces a two-dimensional reading — ``valence`` (negative ↔ positive) and
    ``arousal`` (calm ↔ activated) — via the small ``emotion`` model tier,
    reusing ``llm_enrichment._invoke_emotion_tier``.
  * Consumed first by ``FocusScorer``'s ``emotion`` signal (distress = high
    arousal × negative valence); later by memory / UI / proactive reactions.

Hard boundaries:
  * This is NOT the ``OUTWARD_EMOTION_ANALYSIS`` pipeline that drives lanlan's
    avatar face — that analyzes the *character's* reply. This analyzes the
    *user's* own utterance and never touches the avatar channel.
  * Privacy-independent BY CONSTRUCTION: the input is what the user said, not
    screen / app state. So it is NOT gated on privacy mode (see
    ``docs/contributing/developer-notes.md`` rule 6), mirroring Focus.

Only the latest reading is kept — long-term aggregation (dominant emotion /
volatility / triggers) is a deferred extension; ``to_profile_sample`` is the
hook a future memory consumer pulls from.
"""
from __future__ import annotations

import hashlib
import logging
import math
import re
import time
import weakref
from dataclasses import dataclass, replace
from typing import Optional

import config

logger = logging.getLogger(__name__)

# ```json ... ``` fence the emotion tier (e.g. Gemini) may wrap the JSON in.
# Mirrors system_router.emotion_analysis's stripping before robust_json_loads.
# Case-insensitive: models also emit ```JSON / ```Json.
_JSON_FENCE_RE = re.compile(r"```(?:json)?\s*(.+?)\s*```", flags=re.S | re.I)


@dataclass(frozen=True)
class MasterEmotionReading:
    """One instantaneous read of the user's emotional state.

    ``valence`` ∈ [-1, 1] (negative → positive), ``arousal`` ∈ [0, 1]
    (calm → activated), ``confidence`` ∈ [0, 1]. ``complexity`` ∈ [0, 1] is an
    orthogonal cognitive read — how much the speaker is posing a COMPLEX,
    OBJECTIVE question (math / logic / reasoning), a Focus bonus axis independent
    of emotion. ``external_intent`` ∈ [0, 1] (or ``None``) is a second orthogonal
    read — how much the turn needs an EXTERNAL capability: either an explicit
    outward action / operation, OR external / real-time / beyond-known
    information to answer (weather, prices, news, …). It rides this same cheap
    call so the agent analyzer can cheaply gate its expensive turn-end
    assessment. ``None`` means the model gave no usable signal: the consuming
    gate MUST fail open (run the assessment), never treat it as "no external
    need". ``source_excerpt`` keeps a short prefix for diagnostics only.
    """

    valence: float
    arousal: float
    confidence: float
    updated_at: float
    complexity: float = 0.0
    external_intent: Optional[float] = None
    source_excerpt: str = ""
    # Stable fingerprint (sha1 of the whitespace-normalized FULL text) of the
    # analyzed turn, used to match this reading to the turn it came from — the
    # agent pre-gate must not consume a stale reading from an earlier turn.
    # See ``_normalize_for_match``.
    source_norm: str = ""


def _clamp(value, lo: float, hi: float, default: float) -> float:
    """Coerce ``value`` to a float in [lo, hi]; return ``default`` on junk/NaN."""
    try:
        v = float(value)
    except (TypeError, ValueError):
        return default
    if math.isnan(v):
        return default
    return max(lo, min(hi, v))


def _coerce_axis(value) -> Optional[float]:
    """Coerce a VA axis to float, or ``None`` if missing / non-numeric / NaN.

    Unlike ``_clamp``, this distinguishes "absent" from a real value so a
    partial response can be rejected rather than silently defaulted to 0.
    """
    try:
        v = float(value)
    except (TypeError, ValueError):
        return None
    if math.isnan(v):
        return None
    return v


def _external_intent_from(value) -> Optional[float]:
    """Clamp a parsed ``external_intent`` to [0, 1], or ``None`` when absent/junk.

    Unlike the emotion axes, ``None`` is meaningful downstream: the agent gate
    must fail open (run the assessment) when it has no usable external signal,
    instead of reading a phantom ``0.0`` as "confidently no external need" and
    wrongly braking a real tool / info-fetch request.
    """
    v = _coerce_axis(value)
    if v is None:
        return None
    return max(0.0, min(1.0, v))


def _normalize_for_match(text: Optional[str]) -> str:
    """A stable fingerprint of the analyzed text, to match a cached reading to
    the turn it came from. Whitespace is collapsed and the FULL text is hashed
    (not a prefix) so two long messages that merely share a prefix never collide
    into a false "same turn" match — which would let a stale reading brake a
    different turn. Any real difference → different fingerprint → mismatch → the
    gate fails open (run the assessment), never a wrong brake. ``""`` for empty
    input (never matches a real reading)."""
    norm = " ".join((text or "").split())
    if not norm:
        return ""
    return hashlib.sha1(norm.encode("utf-8")).hexdigest()


# Per-lanlan registry of live trackers, so an out-of-band caller — the
# cross-server analyze_request publisher at turn_end — can read the latest
# ``external_intent`` without holding the session / core handle. A
# ``WeakValueDictionary`` so a torn-down session's tracker auto-evicts (no leak
# across sessions); a stale entry is harmless anyway since ``.latest`` is
# TTL-gated.
_TRACKERS_BY_LANLAN: "weakref.WeakValueDictionary[str, MasterEmotionTracker]" = (
    weakref.WeakValueDictionary()
)


def gate_signal_for(lanlan_name: str, user_text: str) -> Optional[float]:
    """Combined agent-relevance signal for the cheap analyzer pre-gate, or
    ``None`` when there is no usable, current signal (→ the agent fails open and
    runs its assessment). Purely an optimization hint, never a hard brake.

    Returns ``None`` unless the live tracker holds a reading that:
    (a) passes the MASTER_EMOTION_ENABLED switch + reading TTL (via ``.latest``);
    (b) was produced from THIS turn's ``user_text`` — a freshness match, so a
        stale reading from an earlier (chattier) turn can never gate the current
        one (master-emotion is throttled + fire-and-forget, so ``.latest`` may
        lag the current turn);
    (c) carried a usable ``external_intent``.

    When all hold, returns ``max(external_intent, complexity)`` so a hard reasoning
    turn (high complexity — e.g. an openfang multi-step request) keeps the gate
    open even at low external_intent.
    """
    tracker = _TRACKERS_BY_LANLAN.get(lanlan_name)
    if tracker is None:
        return None
    reading = tracker.latest
    if reading is None:
        return None
    norm = _normalize_for_match(user_text)
    if not norm or norm != reading.source_norm:
        return None  # stale / different turn → fail open
    if reading.external_intent is None:
        return None  # no usable external signal → fail open
    return max(reading.external_intent, reading.complexity)


class MasterEmotionTracker:
    """Per-session instantaneous master-emotion state. Async-fed, throttled."""

    def __init__(self, lanlan_name: str) -> None:
        self.lanlan_name = lanlan_name
        self._latest: Optional[MasterEmotionReading] = None
        self._last_attempt_at: float = 0.0
        self._seq: int = 0
        # Register so the cross-server analyze_request publisher can read the
        # latest external_intent by lanlan_name without the core/session handle.
        _TRACKERS_BY_LANLAN[lanlan_name] = self

    # ── public API ──────────────────────────────────────────────────
    @property
    def latest(self) -> Optional[MasterEmotionReading]:
        """The most recent reading, or ``None`` if never analyzed / reset / disabled / expired.

        Honors the master switch: flipping ``MASTER_EMOTION_ENABLED`` off makes
        the reading disappear immediately (consumers fall back) instead of
        serving a stale reading until the next analyze.

        Also age-gated by ``MASTER_EMOTION_READING_TTL_SEC``: the emotion signal
        can trigger Focus on its own, so an indefinitely-served old distress
        reading would mis-enter Focus on an unrelated neutral message after a
        long pause. Normal turn cadence (seconds) stays well within the TTL.
        """
        if not bool(getattr(config, "MASTER_EMOTION_ENABLED", True)):
            return None
        r = self._latest
        if r is None:
            return None
        ttl = float(getattr(config, "MASTER_EMOTION_READING_TTL_SEC", 120.0))
        if ttl > 0 and (time.time() - r.updated_at) > ttl:
            return None
        return r

    def reset(self) -> None:
        """Drop the current reading (call on session teardown / hot-swap)."""
        self._latest = None
        self._last_attempt_at = 0.0
        self._seq += 1

    async def analyze(
        self, text: Optional[str], *, now: Optional[float] = None,
    ) -> Optional[MasterEmotionReading]:
        """Analyze one user utterance → update the latest VA reading.

        Throttled by ``MASTER_EMOTION_MIN_INTERVAL_SEC`` and gated by
        ``MASTER_EMOTION_ENABLED``. Best-effort: any failure (tier disabled,
        bad JSON, timeout) leaves the previous reading intact and returns
        ``None``. Returns the new reading on success.
        """
        if now is None:
            now = time.time()
        cleaned = (text or "").strip()
        if not cleaned:
            return None
        if self._should_skip(now):
            return None
        # Reserve the throttle slot up-front (before the await) so concurrent
        # turns inside the same interval don't all fire a model call. ``_seq``
        # tags this analysis so an out-of-order completion can't clobber a newer
        # turn's reading (see the stale-result guard below).
        self._last_attempt_at = now
        self._seq += 1
        my_seq = self._seq

        raw = await self._invoke(cleaned)
        if not raw:
            return None
        reading = self._parse(raw, now=now, source=cleaned)
        if reading is None:
            return None
        # Truncation guard: the model only saw ``cleaned[:MAX_INPUT_CHARS]``. If
        # the turn was longer, an action verb / info request in the unseen tail
        # makes the external_intent read an unreliable basis for the agent gate —
        # null it so the gate fails open (run the assessment). Emotion /
        # complexity, which are judgeable from the opening, stay valid. The
        # source_norm fingerprint remains the FULL text so the freshness match is
        # unaffected.
        _max_chars = max(1, int(getattr(config, "MASTER_EMOTION_MAX_INPUT_CHARS", 500)))
        if reading.external_intent is not None and len(cleaned) > _max_chars:
            reading = replace(reading, external_intent=None)
        # Stale-result guard: if a newer analysis (or a reset) was kicked off
        # while this one awaited — possible under a slow tier — the newer turn
        # wins; never let this older turn's reading overwrite it.
        if my_seq != self._seq:
            return None
        self._latest = reading
        logger.info(
            "[%s] master emotion: valence=%.2f arousal=%.2f conf=%.2f complexity=%.2f",
            self.lanlan_name, reading.valence, reading.arousal, reading.confidence,
            reading.complexity,
        )
        return reading

    def to_profile_sample(self) -> Optional[dict]:
        """Future long-term-profile hook: one sample = the latest reading.

        Long-term aggregation is deferred; this lets a memory / reflection
        consumer pull the current sample without reaching into private state.
        Reads via ``self.latest`` so it honors the MASTER_EMOTION_ENABLED gate.
        """
        r = self.latest
        if r is None:
            return None
        return {
            "valence": r.valence,
            "arousal": r.arousal,
            "confidence": r.confidence,
            "at": r.updated_at,
        }

    # ── internals ────────────────────────────────────────────────────
    def _should_skip(self, now: float) -> bool:
        if not bool(getattr(config, "MASTER_EMOTION_ENABLED", True)):
            return True
        interval = float(getattr(config, "MASTER_EMOTION_MIN_INTERVAL_SEC", 6.0))
        return (now - self._last_attempt_at) < interval

    async def _invoke(self, text: str) -> Optional[str]:
        # Reuse the package-internal emotion-tier call (same small model the
        # activity enrichment uses). Telemetry currently attributes these to
        # the shared ``activity_enrichment`` call type.
        from config.prompts.prompts_emotion import get_master_emotion_va_prompt
        from main_logic.activity.llm_enrichment import _invoke_emotion_tier

        # Bound the input: emotion is judgeable from the opening; truncating
        # stops a pasted wall of text from going to the emotion tier whole.
        max_chars = max(1, int(getattr(config, "MASTER_EMOTION_MAX_INPUT_CHARS", 500)))
        bounded = text[:max_chars]
        lang = self._resolve_lang(bounded)
        # The VA prompt already says "the speaker in the conversation below",
        # so the user's utterance follows directly — no extra delimiter needed.
        prompt = get_master_emotion_va_prompt(lang) + "\n\n" + bounded
        timeout = float(getattr(config, "MASTER_EMOTION_TIMEOUT_SEC", 8.0))
        return await _invoke_emotion_tier(prompt, timeout=timeout, label="master_emotion")

    @staticmethod
    def _resolve_lang(text: str) -> str:
        # Prompt language follows the language the user spoke in.
        try:
            from utils.language_utils import detect_language, normalize_language_code
            return normalize_language_code(detect_language(text), format="short")
        except Exception:
            return "zh"

    @staticmethod
    def _parse(
        raw: str, *, now: float, source: str,
    ) -> Optional[MasterEmotionReading]:
        from utils.file_utils import robust_json_loads

        text = (raw or "").strip()
        fence = _JSON_FENCE_RE.search(text)
        if fence:
            text = fence.group(1).strip()
        try:
            data = robust_json_loads(text)
        except Exception:
            return None
        if not isinstance(data, dict):
            return None
        # A complete reading needs BOTH axes as real numbers. A partial response
        # (e.g. only ``valence``) must NOT be filled with 0 — a missing arousal
        # would zero the distress signal (distress ∝ arousal) and silently
        # misread strong negative affect as calm. Reject → keep the last reading.
        valence = _coerce_axis(data.get("valence"))
        arousal = _coerce_axis(data.get("arousal"))
        if valence is None or arousal is None:
            return None
        return MasterEmotionReading(
            valence=max(-1.0, min(1.0, valence)),
            arousal=max(0.0, min(1.0, arousal)),
            confidence=_clamp(data.get("confidence"), 0.0, 1.0, 0.5),
            updated_at=now,
            # complexity is an optional extra axis — unlike valence/arousal a
            # missing value safely defaults to 0 (no cognitive bonus), so it
            # never blocks a valid emotional reading.
            complexity=_clamp(data.get("complexity"), 0.0, 1.0, 0.0),
            # external_intent: keep None when the model omitted it / gave junk so
            # the consuming agent gate fails open instead of reading a phantom
            # 0.0 as "confidently no external need" and braking a real request.
            external_intent=_external_intent_from(data.get("external_intent")),
            source_excerpt=source[:80],
            source_norm=_normalize_for_match(source),
        )
