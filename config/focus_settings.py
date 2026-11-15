# -*- coding: utf-8 -*-
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

"""Master-emotion analysis and focus-mode tuning settings."""

# -- Master emotion profile infrastructure --
# Analyze only the user's own words immediately with the emotion-tier small model, producing instantaneous valence-arousal
# readings (valence -1..+1, arousal 0..1). Independent infrastructure and the sole authority: FocusScorer's
# emotion signal is its first consumer; later memory/UI/proactive reactions can share the state. Never reuse
# lanlan avatar outward-emotion, which represents the character's face rather than the user's feelings. Privacy-
# independent: input is conversation, not screen content, so privacy mode does not gate it, as with focus (developer-notes rule 6).
MASTER_EMOTION_ENABLED = True
"""Master emotion-profile toggle, enabled by default.
- Purpose: enabled runs asynchronous VA analysis for each throttled user message, updating instantaneous readings; disabled
  skips analysis and keeps readings empty, automatically removing Focus emotion signals and reverting to keyword+cadence.
- Upstream: _note_user_turn fire-and-forget trigger; FocusScorer.emotion signal availability."""

MASTER_EMOTION_MIN_INTERVAL_SEC = 6.0
"""Minimum interval between VA analyses (seconds), throttling rapid messages to protect the emotion tier.
- Purpose: MasterEmotionTracker returns early based on its last analysis timestamp.
- Lower means fresher but more tokens; higher saves tokens with older readings."""

MASTER_EMOTION_TIMEOUT_SEC = 8.0
"""Timeout for one emotion-tier VA analysis call (seconds).
- Purpose: timeout passed to _invoke_emotion_tier; expiration retains prior readings without updating this turn.
- Uses an independent emotion-tier model, unaffected by slow Gemini Live main conversation."""

MASTER_EMOTION_MAX_INPUT_CHARS = 500
"""Maximum user-text characters for VA analysis; truncate overflow.
- Purpose: emotion judgment needs only an initial excerpt; avoid sending entire pasted articles to the emotion tier
  (tokens / cost / input budget).
- Upstream: MasterEmotionTracker._invoke truncates before assembling prompts."""

MASTER_EMOTION_READING_TTL_SEC = 120.0
"""Emotion-reading validity period (seconds); latest returns None after expiry.
- Purpose: emotion alone can trigger Focus within one turn. Without expiry, neutral messages after long pauses
  could reuse minutes-old distress, wrongly reentering/maintaining Focus. TTL invalidates stale readings,
  leaving normal conversation with seconds/tens-of-seconds turn spacing unaffected.
- Zero disables aging. Upstream: MasterEmotionTracker.latest."""

# -- Focus mode (docs/design/focus-truename-mode.md) --
# Signal-triggered, transparent per-turn thinking/stronger-model activation realizes the divine-descent 10% of the
# 90/10 product proposition. Centralize A/B tuning knobs here for rollout adjustments; multilingual emotion-keyword
# vocabularies belong in config/prompts/prompts_focus.py under i18n rules, not here.
FOCUS_MODE_ENABLED = True
"""Focus master toggle, enabled by default.
- Purpose: enabled runs FocusScorer and lets SM enter/exit FOCUS through accumulated charge; triggering inline turns
  raise tiers and enable thinking, while proactive follows episode cooldown. Disabled returns both trigger paths to ordinary
  behavior (proactive still disable_thinking, stream_text never upgrades), byte-for-byte unchanged.
- History: initially disabled for inert rollout because thresholds lacked real signal-distribution tuning and thinking-enabled
  end-to-end behavior (inline reasoning leaking through streamed content, provider thinking overhead) had not been verified
  against real models. Now enabled for real-signal measurement/tuning; see docs/design/focus-truename-mode.md.
- Upstream: FocusScorer / SessionStateMachine entry early-return checks."""

# -- Accumulated entry model: leaky accumulator --
# Entry uses accumulated charge across turns rather than one turn exceeding a score threshold:
#   charge = charge * FOCUS_CHARGE_RETENTION + current-turn score.
# Enter at charge >= ENTER, exit below EXIT, creating hysteresis. Scattered vulnerability signals can accumulate enough to enter;
# once neutral, retention leaks charge each turn until exit. This replaces consecutive-K-low-score
# streaks, which single noisy turns could reset indefinitely (observed in PR testing).
FOCUS_CHARGE_RETENTION = 0.5
"""Charge retained per turn (0–1).
- Purpose: charge = charge × this value + current score. 0.5 retains half and leaks half each turn.
- Higher (0.7/0.8) remembers longer, accumulates scattered evidence more readily, and stays engaged longer;
  lower (0.3) leaks quickly, accumulates less readily, and exits cleanly.
- Steady state: constant score=s gives charge -> s/(1-retention), e.g. retention=0.5, s=0.5 approaches 1.0.
- Primary sensitivity control, used only by inline user-speech paths."""

# Idle proactive-chat cooldown never raises charge, only decays it; only inline user speech drives focus entry/maintenance.
# Previously speaking/silent tiers differed because speaking costs more focus; now both use the same retention,
# gently cooling at the same rate regardless of whether proactive speech was emitted. Duration depends
# on proactive-trigger frequency rather than elapsed time alone. Retain two knobs for future separation, but require both >0,
# <1, with replied <= silent.
FOCUS_IDLE_SILENT_RETENTION = 0.8
"""Charge retention when proactive speech does not actually occur this turn.
- Includes action != chat (guards/takeover, empty content, [PASS]) plus Phase 2 thinking
  timeouts / streaming failures resulting in aborted, ultimately action=pass: thinking was enabled but failed within
  its time limit, so apply the same cooling tier.
- Purpose: charge = charge × this value. 0.8 gently cools each turn; currently matches replied at 0.8,
  cooling speech and silence equally.
- Lower means silence/timeouts cool faster."""

FOCUS_IDLE_REPLIED_RETENTION = 0.8
"""Charge retention when proactive speech actually occurs (action == chat, delivered proactive dialogue).
- Purpose: charge = charge × this value. 0.8 gently consumes charge per utterance; from cap=1.0, about six
  proactive utterances bring it below EXIT(0.3), keeping Focus engaged longer.
- Must be <= FOCUS_IDLE_SILENT_RETENTION: speaking never cools more slowly than silence. Both currently equal 0.8.
- Upstream: SM.update_focus retention_override, selected by action during idle finalization."""

# Tuning guard: fail-fast validation enforces cooldown semantics against accidental inversion;
# >=1.0 makes idle ticks increase instead of decay, violating never-increase; replied > silent makes speaking
# cool more slowly than silence, reversing tiers. Equality is allowed: currently both are 0.8.
# Validate at module loading so bad configuration fails instead of silently misbehaving.
if not (0.0 < FOCUS_IDLE_REPLIED_RETENTION <= FOCUS_IDLE_SILENT_RETENTION < 1.0):
    raise ValueError(
        "Focus idle retentions must satisfy 0 < replied <= silent < 1 "
        f"(got replied={FOCUS_IDLE_REPLIED_RETENTION}, "
        f"silent={FOCUS_IDLE_SILENT_RETENTION})"
    )

FOCUS_CHARGE_ENTER = 0.6
"""Focus-entry charge threshold and fully activated point.
- Purpose: charge ≥ threshold causes REGULAR->FOCUS, nonlinearly increasing frontend edge glow and starting its breathing.
- One strong signal can enter immediately: strong distress (emotion maximum 0.7, crossing at roughly ≥0.86) or
  maximum complex question (question 1.0×0.6=0.6). Vulnerability keywords alone cannot enter in one turn (maximum 0.5 < threshold,
  deliberately, as vocabulary is a cheap signal); combine emotion or accumulate across turns. Scattered signals accumulate charge toward entry.
  Score is now a weighted signal sum with no denominator; see FOCUS_SIGNAL_WEIGHTS.
- Charge no longer caps here: see FOCUS_CHARGE_CAP, continuing above 0.6 to 1.0 for stronger, longer glow.
- Time decay switches here: charge < ENTER decays quickly (FOCUS_TIME_DECAY_PER_SEC); ≥ ENTER, fully activated,
  decays at half speed (FOCUS_TIME_DECAY_PER_SEC_ACTIVATED), making charge above 0.6 last longer."""

FOCUS_CHARGE_CAP = 1.0
"""Charge ceiling.
- Purpose: accumulation limit. ENTER(0.6) marks entry/full activation; 0.6->CAP means deeper activation,
  with frontend edge-glow peaks increasing until capped here.
- Must be ≥ ENTER."""

FOCUS_TIME_DECAY_PER_SEC = 0.02
"""Charge time-decay amount per second before full activation (charge < ENTER).
- Purpose: time component of dual decay alongside per-turn retention. Even without new turns, charge follows
  actual wall-clock time, computed lazily in update_focus; frontend extrapolates glow at the same rate.
- 0.02/s drains 0.6 to zero in roughly 30s without new evidence; higher cools faster."""

FOCUS_TIME_DECAY_PER_SEC_ACTIVATED = 0.01
"""Charge time decay per second after full activation (charge ≥ ENTER), halved for longer persistence.
- Purpose: halve decay after Focus entry so she remains longer; higher charge sits farther from ENTER,
  extending presence above 0.6.
- Floor: activated time decay lowers charge only to ENTER(0.6), **never below 0.6 through time alone**;
  exiting activation requires a conversational turn's retention (see _decay_charge_over_time). Below 0.6, time can drain to zero.
- Must be < FOCUS_TIME_DECAY_PER_SEC, slower after activation than before."""

if not (FOCUS_TIME_DECAY_PER_SEC_ACTIVATED < FOCUS_TIME_DECAY_PER_SEC):
    raise ValueError(
        "FOCUS_TIME_DECAY_PER_SEC_ACTIVATED must be < FOCUS_TIME_DECAY_PER_SEC "
        f"(got activated={FOCUS_TIME_DECAY_PER_SEC_ACTIVATED}, "
        f"base={FOCUS_TIME_DECAY_PER_SEC})"
    )

FOCUS_CHARGE_EXIT = 0.3
"""Focus-exit charge threshold (low hysteresis gate, must be < ENTER).
- Purpose: exit FOCUS when charge falls below this value.
- After returning neutral, charge leaks from cap through retention: retention=0.5/cap=ENTER(0.6) exits below 0.3 after about 1–2 turns,
  letting her stay another turn or two after arriving. Lower means stickier, longer engagement."""

FOCUS_HARD_CAP_TURNS = 8
"""Maximum turns M per Focus episode, a hard backstop.
- Purpose: force exit/finalization after M turns even with charge above EXIT due to sustained intense user dialogue,
  preventing indefinitely prolonged episodes.
- Upstream: SM focus_turn_count."""

FOCUS_SIGNAL_WEIGHTS: dict[str, float] = {
    "keyword": 0.5,       # User message matches vulnerable-emotion vocabulary.
    "cadence": 0.2,       # Reply length drops sharply relative to baseline; count only with distress evidence.
    "emotion": 0.7,       # Master emotion profile, the main signed signal: negative-valence distress is positive, positive-valence joy negative; see MasterEmotionTracker.
    "question": 0.6,      # Cognitive bonus when the master model identifies a complex objective math/logic/reasoning question.
}
"""FocusScorer signal weights, inline only: score only the user's own speech.
- Purpose: **sum applicable weighted signals directly, without normalization or denominator**, producing this turn's score for
  accumulation. Weights are absolute contributions: present signals add weight×value, absent signals contribute zero.
- Two signal categories:
  · keyword / emotion / question trigger: absent returns None, contributes zero without diluting others.
    emotion upgrades keyword vocabulary through a real model (hence vocabulary weight 0.5 < modeled emotion 0.7), and is **signed**:
    negative-valence distress is positive, positive-valence joy negative, neutral None; joy pulls score/charge
    down. question adds a cognitive axis for complex objective mathematics/logic/reasoning deserving thinking-enabled responses,
    orthogonal to distress but sharing charge.
  · cadence is behavioral, included only with sufficient samples and **distress evidence** (keyword / question / emotion>0),
    preventing short happy messages from incorrectly pushing Focus. Without a denominator, 0.0 (no sudden message-length drop)
    contributes zero like absence; only real sudden drops increase score.
- No denominator means score is **not capped at 1.0**: all maximum signals sum current weights (0.5+0.2+0.7+0.6=2.0),
  capped downstream by FOCUS_CHARGE_CAP. Weights directly control absolute signal force and entry difficulty relative to ENTER.
- One-signal entry requires weight×maximum ≥ ENTER. Without normalization, keyword maximum is only 0.5,
  cadence 0.2, neither exceeding ENTER(0.6) alone. Keywords require emotion or cross-turn accumulation, deliberately because vocabulary
  is cheap evidence; only emotion (roughly ≥0.86) and full question (1.0×0.6) can enter within one turn alone.
- emotion reads MasterEmotionTracker's latest computed VA reading, mapping to
  distress = max(0,-valence) × (FOCUS_EMOTION_AROUSAL_FLOOR + (1-floor)×arousal):
  negative valence dominates, with bounded-floor arousal amplification (see FOCUS_EMOTION_AROUSAL_FLOOR). **One-turn lag**
  occurs because profiling is asynchronous and inline reads the previous turn;
  disabled MASTER_EMOTION_ENABLED or absent readings/distress returns None, automatically reverting to keyword+cadence.
- Idle/proactive does not score; FOCUS_IDLE_SILENT/REPLIED_RETENTION only decreases charge,
  never increasing it, so is outside this table. Inline alone drives Focus entry/maintenance.
- Upstream: each subsignal normalizes itself to [0,1].
- Design: keyword/emotion are strongest emotional signals and have highest weights. These directly change triggering personality; tune carefully."""

FOCUS_KEYWORD_SATURATION = 3
"""Saturation point for vulnerability-keyword hit counts.
- Purpose: divide scan_vulnerability_keywords hits by this value, clipping at 1.0 for the keyword
  subsignal: one 累 (tired) nudges lightly; 撑不住 + 一个人 + 没意思 together saturate.
- Upstream: config/prompts/prompts_focus.scan_vulnerability_keywords hit count."""

FOCUS_EMOTION_AROUSAL_FLOOR = 0.5
"""Arousal-amplifier floor in the emotion signal.
- Mapping: distress = max(0,-valence) × (floor + (1-floor) × arousal).
- Meaning: negative valence drives distress, while arousal scales strength only within [floor,1],
  no longer acting as an AND gate. Vulnerability/confiding often combines low arousal + strong negative valence (quiet sadness),
  suppressed near zero by the former distress = arousal × negativity product.
  An arousal floor lets strong negative valence retain most of its contribution despite low arousal.
- Values: 0 restores the old product and arousal gate; 1 ignores arousal and uses valence alone;
  0.5 compromises, guaranteeing half strength at low arousal and full strength at high arousal. Must lie in [0,1].
- Upstream: FocusScorer._signal_emotion. Affects only emotion, not keyword/cadence."""

FOCUS_EMOTION_POSITIVE_SCALE = 0.5
"""Anti-Focus emotion magnitude factor for positive valence, when users are happy.
- Emotion is now **signed**: negative valence yields positive distress pushing Focus (maximum +1); positive valence yields negatives
  pulling charge down to avoid interrupting happiness, capped in magnitude by this factor.
- Positive-side mapping: emotion = -(positivity × m × this factor), positivity=max(0,valence),
  m = AROUSAL_FLOOR + (1-AROUSAL_FLOOR)×arousal, sharing distress-side amplification in [0.5,1].
- At 0.5, positive valence reaches -0.5 (valence=+1, arousal=1, m=1); valence=+1, arousal=0.3 gives
  m=0.65 -> emotion=-0.5×0.65=-0.325. Maximum positive-valence pull is half negative-valence distress.
- Must lie in [0,1]; zero disables happy anti-Focus, restoring positive-valence abstention (None)."""

FOCUS_CADENCE_BASELINE_WINDOW = 6
"""Cadence baseline window: median lengths of the latest N user messages.
- Purpose: per-session rolling buffer maxlen in FocusScorer.
- Upstream: character lengths of genuine user messages."""

FOCUS_CADENCE_MIN_SAMPLES = 3
"""Minimum sample count for cadence activation.
- Purpose: fewer than N buffer samples makes cadence inapplicable, excluded from normalization,
  avoiding unstable early-session baselines triggering randomly.
- Upstream: current rolling-buffer length."""

FOCUS_CADENCE_DROP_RATIO = 0.4
"""Current-length / baseline-median ratio yielding maximum cadence.
- Purpose: current length ≤ ratio × baseline median gives cadence=1.0; ≥ baseline gives zero;
  interpolate linearly between. Example: baseline 30 characters and ratio 0.4 means ≤12 characters (嗯。 / 知道了。) saturate.
- Upstream: current message length and baseline median."""

# NOTE: silence/open_thread signals are removed. Idle proactive paths no longer score; they only decay
# charge with FOCUS_IDLE_SILENT/REPLIED_RETENTION. Only inline input drives focus entry/maintenance.
# Retire FOCUS_SILENCE_MIN_SECONDS / FOCUS_SILENCE_FULL_SECONDS too, avoiding dead settings.

# NOTE: FOCUS_IDLE_THRESHOLD_MULTIPLIER would lower idle thresholds during focus to proactively follow up
# once or twice after descent. This Path B idle-threshold-drop feature is not wired yet,
# so add its knob with implementation rather than leaving dead configuration. See blueprint.

# NOTE: likewise omit FOCUS_EPISODE_MEMORY_ENABLED, which would batch-organize reflection/persona/
# facts/ban-list on focus exit; the FOCUS_EXIT -> memory subscriber is not wired yet.
# Add its knob with that PR; see docs/design/focus-truename-mode.md.
