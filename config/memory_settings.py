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

"""Memory evidence, recall, reflection, persona, and vector tuning settings."""

from .network import _read_bool_env, _read_str_env

TIME_ORIGINAL_TABLE_NAME = "time_indexed_original"
TIME_COMPRESSED_TABLE_NAME = "time_indexed_compressed"


# ── Memory evidence mechanism (docs/design/memory-evidence-rfc.md) ────
# User-driven evidence counter constants. Scores measured in net user confirmations
# (§3.1.2 differs from task-spec formula by removing importance). Changing thresholds
# changes behavior; see RFC §6.5 pre-merge reviewer gates.

# §3.1.4 Derived-state thresholds.
EVIDENCE_CONFIRMED_THRESHOLD = 1.0   # score ≥ 1 → confirmed
EVIDENCE_PROMOTED_THRESHOLD = 2.0    # score ≥ 2 → promoted
EVIDENCE_ARCHIVE_THRESHOLD = -2.0    # score ≤ -2 → archive_candidate

# Time-driven fallback when powerful_memory_enabled=False.
# Pre-RFC: advance lifecycle solely by reflection age, without evidence_score or
# LLM cost. Previous 3 days proved too aggressive (no denial for3days is not agreement);
# extend to7days for more opportunity to dispute.
WEAK_MEMORY_AUTO_CONFIRM_DAYS = 7   # pending → confirmed, measured from created_at.
WEAK_MEMORY_AUTO_PROMOTE_DAYS = 7   # confirmed → promoted, measured from confirmed_at.

# §3.5.3 Archival (sub_zero_days counts + shard-size cap).
EVIDENCE_ARCHIVE_DAYS = 14           # Archive after this many accumulated sub_zero days.
ARCHIVE_FILE_MAX_ENTRIES = 500       # Maximum entries per archive shard.

# §3.1.5 Ignored penalty.
IGNORED_REINFORCEMENT_DELTA = -0.2   # check_feedback ignored → reinforcement += delta

# §3.1.8 Delta weights by signal source (v1.2.1 direct vs indirect).
# Direct signals (explicit surfaced-reflection responses / negative-keyword hits) weight1.0;
# indirect Stage2 model-inferred fact/reflection relations weight0.5 to avoid
# rapid evidence pollution through false LLM associations.
USER_FACT_REINFORCE_DELTA = 0.5      # Stage2 reinforces (indirect, silver standard).
USER_FACT_NEGATE_DELTA = 1.0         # Stage2 negates retains strong weight even indirectly,
                                     # since model-negated relations usually have clearer semantics.
USER_CONFIRM_DELTA = 1.0             # check_feedback confirmed (direct, gold standard).
USER_REBUT_DELTA = 1.0               # check_feedback denied (direct).
USER_KEYWORD_REBUT_DELTA = 1.0       # Keywords + LLM target validation (direct + explicit).

# user_fact reinforces combo bonus: after count exceeds threshold, each new signal
# adds bonus so repeated indirect user expression can catch one direct confirmation's weight.
# Default first2 signals0.5 each; from third,0.5 +0.5 bonus=1.0.
USER_FACT_REINFORCE_COMBO_THRESHOLD = 2   # Active when count > threshold.
USER_FACT_REINFORCE_COMBO_BONUS = 0.5     # Extra weight per signal above threshold.

# §3.4.3 Background signal-extraction triggers.
EVIDENCE_SIGNAL_CHECK_ENABLED = True             # Independent switch.
EVIDENCE_SIGNAL_CHECK_EVERY_N_TURNS = 10         # Trigger after N accumulated turns.
EVIDENCE_SIGNAL_CHECK_IDLE_MINUTES = 5           # Or after N idle minutes.
EVIDENCE_SIGNAL_CHECK_INTERVAL_SECONDS = 40      # Poll interval aligned with IDLE_CHECK_INTERVAL.
EVIDENCE_DETECT_SIGNALS_MAX_OBSERVATIONS = 30    # Maximum obs entering prompt after Stage2 LLM rerank (reduce NxM pairing decisions).

# ── activity_guess adaptive backoff ──────────────────────────────────────
# Activity heartbeat (main_logic/activity/tracker.py:_activity_guess_loop) uses emotion-tier
# LLM to narrate user activity solely for proactive prompts. Controls refresh rate while activity
# is substantively unchanged; switching between two apps previously burned a call every~40s
# silently forever. See main_logic/activity/activity_guess_gate.py.
# Every same-activity narration multiplies next interval by MULTIPLIER, capped at CAP:30→120→480→900.
# CAP900s matches AWAY_IDLE_SECONDS (state_machine.py; heartbeat exits after15min away),
# so stable activity approaches away at maximum backoff. get_snapshot reads cache without TTL;
# increasing cap does not make narration stale: unchanged activity remains accurate.
ACTIVITY_GUESS_BACKOFF_BASE_SECONDS = 30.0   # Hard minimum between calls + first repeat interval.
ACTIVITY_GUESS_BACKOFF_MULTIPLIER = 4.0      # Backoff growth multiplier after each narration, must >1.
ACTIVITY_GUESS_BACKOFF_CAP_SECONDS = 900.0   # Maximum stable-activity narration interval (aligned15min AWAY_IDLE).
ACTIVITY_GUESS_SIG_CACHE_SIZE = 8            # Number of distinct activity signatures retained for backoff.

# ── AI-aware Stage-1 (path B) ─────────────────────────────────────────
# Original SignalLoop (path A) reads only user messages, missing AI disclosures and proactive
# screen/activity context introduced after PR #1346. Path B piggybacks on every N A ticks:
# after A finishes, increment b_tick_counter and run B at N; bound the window by the latest
# message timestamp A actually processed, not wall-clock now, so B sees only messages A saw.
EVIDENCE_AI_AWARE_EVERY_N_A_TICKS = 3
"""Trigger path B every N path-A ticks (piggyback on A's loop without a separate wall-clock cadence).
- Choose 3: A averages one tick per 5 min -> B averages 15 min. Tempo adapts to conversation intensity:
  more conversation triggers B more often, matching the need for supplementary AI-fact extraction at higher volume.
- B cold-start lookback automatically = N × EVIDENCE_SIGNAL_CHECK_IDLE_MINUTES = 15 min."""

MAX_AI_AWARE_WINDOW_MSGS = 200
"""SQL LIMIT cap per path-B window. Restarts after idle / sudden post-idle bursts can make
[last_b_check_ts, last_a_msg_ts] span hours and over a hundred messages; cap it to bound prompts.
Apply LIMIT in SQL (aretrieve_original_by_timeframe limit_rows),
using ORDER BY ts ASC for the earliest N entries, preserving monotonic cursor progress."""

MAX_KNOWN_POOL_FACTS = 30
"""Known-fact pool cap in path-B prompts (top N by importance DESC).
- 30 × ~30 tok = ~900 tok overhead, around 20% of the full prompt budget.
- Purpose: show path B which facts path A already extracted, avoiding repeated user-content
  extraction; matching facts usually have source='user_observation'."""

# §3.5 / §6.5 Gate 4: background archive scan interval.
# Hourly: sub_zero_days debounces by calendar day (at most +1 per day),
# so at least daily scans suffice; 1h detects a score crossing below zero on the same day
# promptly rather than waiting until midnight. This is much less frequent than the evidence
# signal loop (40s), with negligible IO/CPU impact.
EVIDENCE_ARCHIVE_SWEEP_INTERVAL_SECONDS = 3600

# §3.6 render budget (placeholder for PR-3).
# Private/base legacy mode (empty subjects) uses two global pools; scoped rendering
# (groups, nonempty subjects) gives each subject its own pools, backed by
# SCOPED_RENDER_TOTAL_MAX_TOKENS. Previously group and member subjects competed
# for the same 2000, diluting the group's persona as membership grew.
PERSONA_RENDER_MAX_TOKENS = 2000         # Non-protected persona budget (per subject).
REFLECTION_RENDER_MAX_TOKENS = 2000      # Reflection rendering budget (pending + confirmed, per subject).
PERSONA_RENDER_ENCODING = "o200k_base"   # tiktoken encoding

# ── Overall cap for scoped (group) L10 core memory ──────────────────────────
# Per-subject budgets stop competition but not total growth: rendering five
# subjects costs 5 × 4000 = 20k, consuming most context with core memory alone.
# This hard cap covers the entire block, allocated in caller-provided subject order;
# no subject type reserves capacity or jumps the queue. Ordering is the caller's only
# priority control (existing callers put the group first). A former group reservation
# was dead code for all callers; interactions (multiple/empty groups, exempt-only groups, unused capacity spillover)
# could reverse the order it intended to protect, so it was removed.
# 16000 lets four subjects fill their (2000 persona + 2000 reflection) allowances. Current
# callers pass [group, current speaker]; later PRs expand to [group, current speaker,
# three recent others], five total: the last gets the remainder, as this cap intends.
SCOPED_RENDER_TOTAL_MAX_TOKENS = 16000

# Minimum useful subject allowance: skip the entire subject below this remaining budget,
# rather than insert one or two entries. Partial persona is worse than absence: the model
# treats an incomplete persona as complete ("this person has just one preference"); absence is honest.
SCOPED_RENDER_SUBJECT_MIN_TOKENS = 200

# Extra rendering overhead per kept entry charged to the overall cap. Distinct accounting:
#   · Both per-subject pools count entry text, identically in scoped and legacy modes;
#     one constant must not mean different things in the two modes.
#   · The overall cap counts rendered lines: text + this constant (compose's "- "
#     prefix and newline). Markup dominates short entries; counting only text is no real cap.
# Accept entries only when both budgets fit (see budget / gate_budget in _score_trim_entries).
#
# 12 is worst-case per-entry decoration, not the typical value:
#   "- " prefix 2 + newline 1 = 3 for normal entries;
#   compose adds a localized time-label prefix to outdated reflections
#   (e.g. `[13 ヶ月前] ` / `[hace 13mes] `; measured maximum 8 tok across seven languages).
# 3 + 8 = 11, rounded to 12 for margin. Typical values let many short entries
# exceed the overall cap; this budget must hold in the worst case, so use that case.
# Section headings are excluded: bounded by subject count, not entry count, with no per-entry allocation.
SCOPED_RENDER_ENTRY_MARKUP_TOKENS = 12

# ── Entry-count caps for privileged sections (protected / suppressed) ───────
# Both sections deliberately bypass token budgets: protected entries come from character cards
# (evidence_score returns inf), so trimming fractures persona; suppressed means "remember, don't mention",
# whose meaning requires completeness. Budget exemption does not permit unlimited growth:
# importing hundreds of card entries or accumulating suppression cooldowns can silently consume
# all context. Cap entry counts and warn on truncation (token caps cut entries; count caps drop whole entries).
PERSONA_RENDER_PROTECTED_MAX_ENTRIES = 80
PERSONA_RENDER_SUPPRESSED_MAX_ENTRIES = 40

# ── Budget for L21 long-term recall (/query_memory → prompt) ─────────────────
# Previously recall took only the top five, with no per-entry text cap: one long LLM-merged
# reflection could swell the block to thousands of tokens. Separate constants cap entries and the block.
# 400 per entry fits a complete fact/reflection (typically 30–80 tokens), including merged
# long entries, without consuming the whole block. Truncate excess rather than discard:
# recall is relevance-ordered; even the first half of a match is more useful than none.
RECALL_RENDER_ENTRY_MAX_TOKENS = 400
# Overall cap. ⚠️ Per-entry caps truncate text, while the overall gate counts the entire line
# `f"{i}. {tag} {text}{suffix}"`: numbering + localized tier/entity label + date
# suffix, measured around 17–25 tok/line (English labels are longer). Thus "5 × 400 = 2000"
# is incorrect: five full entries actually cost 5 × 425 ≈ 2125; a 2000 cap silently drops
# the fifth-most-relevant entry. Allow 40 tok/line of overhead.
#
# Yet 5 × (400 + 40) = 2200 still misses one item: take_lines_within_token_budget
# joins lines with a separator, which also costs tokens (see its docstring: bare-line counting
# misses one token per gap). N lines have N-1 gaps; measured newline cost is 1 tok, so five lines
# actually require 5 × (400 + 40) + 4 × 1 = 2204. At 2200, the fifth entry falls just
# outside the cap and is dropped. Normal lines (6–8 tok tags) cost about 417 tok; malformed/full
# data exposes this four-token shortfall. If later PRs raise limit, this gate becomes binding;
# rederive it as limit × (ENTRY + OVERHEAD) + (limit - 1) × newline.
#
# ⚠️ This derivation covers callers passing the unchanged cap (QQ plugin render_relevant_memory).
# The base recall_memory tool (main_logic/core/tool_calling.py) first subtracts the initial
# i18n overview's header_reserve, then passes a smaller value to the helper,
# intentionally leaving insufficient room for limit full lines: header and entries share a string,
# so omitting that reserve would exceed the budget. Check both accounting paths when changing this constant.
RECALL_RENDER_LINE_OVERHEAD_TOKENS = 40
RECALL_RENDER_LINE_SEPARATOR_TOKENS = 1  # "\n": gap cost in take_lines_within_token_budget.
RECALL_RENDER_TOTAL_MAX_TOKENS = 2204

# ── Scoped batch extraction (segments form of write path /scoped_history) ────
# Member memory extraction previously scaled with speaker count: one subject = one LLM
# extraction, costing the same for one or 150 messages. Batching combines multiple (subject, speaker, messages)
# segments in one request/LLM call, reducing O(speakers) calls to O(total messages / batch capacity).
#
# Eight-segment cap matches subjects 1..8 at the three read endpoints and the plugin's
# maximum buckets drained per pass (GROUP_MEMBER_MAX_PARTICIPANTS).
SCOPED_HISTORY_BATCH_MAX_SEGMENTS = 8
# Batch message cap retains legacy's 200 per single-subject request, keeping the input work
# bound unchanged by batching (the premise that batches are no slower than single requests,
# allowing reuse of the plugin's 30s timeout and derived settlement wait). Each member bucket
# caps at 150 (GROUP_MEMBER_HARD_LIMIT) < 200, so no bucket needs splitting across batches.
SCOPED_HISTORY_BATCH_MAX_MESSAGES = 200
# Retention for auxiliary data from idempotency-key writes (visit digest / diary,
# docs/design/visit-infrastructure.md §4.6): staged residue, retired records and tombstones for terminal keys.
# done / cancelled key records remain forever and are exempt.
MEMORY_IDEMPOTENCY_TTL_S = 365 * 86400
# Per-message body cap before batch extraction prompts, matching recent compression:
# 500 tokens; retain head/tail and replace the middle with a locale-specific visible marker.
SCOPED_HISTORY_PER_MESSAGE_MAX_TOKENS = 500
# Batch message bodies get 8000 tokens total. A 500-token per-message cap alone still permits
# 200 × 500 = 100k tokens, exceeding some summary model contexts and the plugin's 30s timeout.
# On exceeding the overall cap, distribute remaining budget / remaining messages fairly; charge short
# messages their actual size and carry savings forward, avoiding greedy ordering starving later bodies.
SCOPED_HISTORY_BATCH_CONTENT_MAX_TOKENS = 8000
# Bytes in the one-time segment-header token (token_hex doubles this length in hexadecimal).
# Prevents members forging [SEGMENT n | speaker: 别人] inside their own messages: the batch template
# tells the model that headers determine ownership; successful forgery writes to another subject
# and borrows their speaker_trust. Attacker messages are fixed before nonce generation, so cannot
# predict the request token. Four bytes = eight hex characters block blind guessing (fresh per
# request, with no repeated probing), without substantial prompt token overhead.
SCOPED_BATCH_SEGMENT_NONCE_BYTES = 4

# ── Subject shape for group recall reads ────────────────────────────────────
# Subjects per group reply: one group + at most this many members (current speaker + the other
# N-1 most recent speakers in this turn's context). Group always comes first: subject order allocates
# rendering budget (SCOPED_RENDER_TOTAL_MAX_TOKENS is first-come-first-served). Total
# 1 + 4 = 5 stays below the read endpoint's 1..8 cap. The second group slot is deliberately reserved:
# only another group's memory could fill it today, requiring a separate cross-group disclosure decision
# (the current cross-group section reads active session memory only, never the memory store).
GROUP_RECALL_MAX_MEMBER_SUBJECTS = 4
# Recent group messages scanned for three other people: repeated messages from one speaker are common,
# so three messages rarely yield three people; 30 cover a few minutes at the cost of one backlog read.
GROUP_RECALL_RECENT_SPEAKER_SCAN_LIMIT = 30

# ── Speaker trust (speaker_trust) fields ─────────────────────────────────────
# Speakers differ in credibility, affecting conflict overwrite priority; this phase adds fields only:
# extraction tags each scoped fact with speaker_label (who said it) and speaker_trust
# (initial 0..1, derived from caller permission level and included in the request). Consumers (fact_dedup
# LLM arbitration / corrections queue / refine merge / aadd_fact conflict scan)
# belong to later PRs; no current path may use these fields for decisions.
#
# Initial values derive from the QQ plugin's four permission tiers (permission.py); only order matters
# (admin > trusted > normal > none), with absolute values tuned by the consumer PR.
SPEAKER_TRUST_DEFAULT = 0.5
SPEAKER_TRUST_BY_PERMISSION_LEVEL = {
    "admin": 1.0,
    "trusted": 0.8,
    "normal": 0.5,
    "none": 0.3,
}

# Trust arbitration / evolution (group memory series 7/7). memory_server owns the trust pool
# globally by account_id (platform:actor), persisted at <memory_dir>/speaker_trust.json:
# one person shares it across groups, characters, and non-admin private participants.
# This product-approved cross-scope/cross-character channel must not become subject-local or character state.
SPEAKER_TRUST_ARBITRATION_MARGIN = 0.15
SPEAKER_TRUST_ACTIVITY_WEIGHT = 0.001
SPEAKER_TRUST_ACTIVITY_MAX_BONUS = 0.02
SPEAKER_TRUST_CONFIRMATION_DELTA = 0.04
SPEAKER_TRUST_CORRECTION_DELTA = 0.08
SPEAKER_TRUST_ADJUSTMENT_LIMIT = 0.30
SPEAKER_TRUST_EVENT_HISTORY_LIMIT = 128
"""Deprecated: activity-ring cap for plugin-local trust pools. The sole consumer
(`permission.py`) was removed after moving pools server-side; the server ring uses
`SPEAKER_TRUST_ACTIVITY_EVENT_HISTORY_LIMIT`. Retain the constant and re-export because
removing public constants breaks compatibility, including possible out-of-tree plugins."""

# ── Server trust pool (platform-neutral) ─────────────────────────────────────
# Persist as a flat file directly under memory_dir, never a subdirectory:
# import cleanup's `delete_dir_targets` in `utils/cloudsave_runtime/operations.py`
# recursively removes every memory_dir subdirectory absent from the imported character list, while
# `delete_file_targets` recognizes only three-component `memory/<角色>/<白名单叶名>` paths. Root-level
# flat files avoid both. Precedent: `app/memory_server/gates.py`'s
# `idle_maintenance_state.json`。
# Future shards must likewise use flat filenames such as `speaker_trust.<n>.json`.
SPEAKER_TRUST_POOL_FILENAME = "speaker_trust.json"

# Cap for self-reported base channels (platforms without four permission tiers, e.g. danmaku guard_level).
# Clamp the final score here, not just base: clamping base alone permits
# 0.8 + 0.30(adjustment) + 0.02(activity) = 1.0, matching admin rights; the safety claim
# "cap 0.8 < admin 1.0, preventing guard_level mapping to owner arbitration rights"
# would be arithmetically false. Moving the clamp introduces deliberate asymmetry: `tier='trusted'`
# (base 0.8, backed by platform permissions) can earn adjustment/activity up to
# 1.0, while unauthenticated self-reported `base=0.8` can never exceed 0.8.
SPEAKER_TRUST_MAX_REPORTED_BASE = 0.8

# Legal wire values for `speaker_tier`. Use Literal rather than `.get(level, DEFAULT)`
# silent defaults: misspelled "Admin" must return 422, never silently become a middle tier.
SPEAKER_TRUST_PERMISSION_TIERS = ("admin", "trusted", "normal", "none")

# Server activity-event ring cap. Must exceed the maximum batch message count (200), or later
# messages evict earlier IDs from the same batch and retries double count. Never truncate the signal ring
# (append-only ledger; truncation replays old correction events); handle the two rings separately.
SPEAKER_TRUST_ACTIVITY_EVENT_HISTORY_LIMIT = 256

# Profiles allowed per legacy import request. Callers control chunking; exceeding the contract is
# a bug ⇒ 422. Individual profile `processed_signal_events` has no cap.
SPEAKER_TRUST_LEGACY_IMPORT_CHUNK_MAX = 500

# Legacy migration gate. First-created pools initialize each platform from this table as `status="pending"`;
# pending accounts get resolve_trust=None (abstain; no speaker_trust key),
# no activity accounting, signals added to signals_deferred, and no reconcile.
# This is the sole defense against import-window double counting: without it, owner restatements while pending
# replay against an empty ledger first, then import adds legacy adjustment already including that correction,
# counting one correction twice: 0.16 > arbitration margin 0.15. The ring stores IDs only, not
# kind, making later reversal impossible.
SPEAKER_TRUST_LEGACY_BARRIERS = {
    "qq": "qq_auto_reply.business_config.speaker_trust_profiles.v1",
}

# Maximum channel length (observational attribute). Channel is not a key: collision detection and operational
# diagnostics only, never ledger partitioning, bind/merge criteria, or authorization.
SPEAKER_TRUST_CHANNEL_MAX_LEN = 16

# Maximum accounts per entity on one platform. Excess bind/merge returns 409.
# Reject at bind time instead of truncating reads: starting from A₁ versus A₅ would otherwise
# expand to different member sets, yielding structurally unequal ParticipantGroups that value deduplication
# cannot repair, violating one participant per entity per group. Binding
# is a rare manual operation where 409 is acceptable; read-time truncation is not.
IDENTITY_MAX_ACCOUNTS_PER_ENTITY_PER_PLATFORM = 8

# Platform-specific tighter caps: {platform: (cap, reason)}; effective only below the generic cap.
# Excess-cap 409 errors include this reason, so explain each restriction clearly.
# neko_visit (visits, VISIT_MEMORY_PLATFORM) = 1: two visit accounts in one entity
# make visit clearing fan out to the other account; visit clients lack target_epochs,
# so targets lack persistent tombstones and late old writes restore cleared memory. Forbid linking until integrated.
IDENTITY_MAX_ACCOUNTS_PER_ENTITY_BY_PLATFORM = {
    "neko_visit": (
        1,
        "串门清除还没接上 target_epochs，关联后的扇出目标没有持久墓碑，"
        "见 issue #3405",
    ),
}


def _assert_activity_bonus_reachable() -> None:
    """Startup assertion: the activity count cap must be able to reach MAX_BONUS.

    The read-side formula is ``min(MAX_BONUS, min(cap, sum_mc) * WEIGHT)`` with
    ``cap = ceil(MAX_BONUS / WEIGHT)``. The two ceilings are numerically
    redundant, and THAT REDUNDANCY IS WHAT BLOCKS MULTI-ACCOUNT FARMING:
    deleting the outer ``min(MAX_BONUS, ...)`` makes the supremum ``0.02 * N``,
    which at N=8 is 0.16 — past the 0.15 arbitration margin, and reachable in
    practice. That outer min is exactly the kind of thing a refactor deletes as
    "implied by the cap".

    This assertion guards the other half: if any constant drifts so that the
    cap can no longer reach MAX_BONUS, fail loud instead of silently turning
    the activity ceiling into a number nobody declared.
    """
    import math

    if SPEAKER_TRUST_ACTIVITY_MAX_BONUS <= 0 or SPEAKER_TRUST_ACTIVITY_WEIGHT <= 0:
        return
    cap = math.ceil(
        SPEAKER_TRUST_ACTIVITY_MAX_BONUS / SPEAKER_TRUST_ACTIVITY_WEIGHT
    )
    if cap * SPEAKER_TRUST_ACTIVITY_WEIGHT < SPEAKER_TRUST_ACTIVITY_MAX_BONUS:
        raise AssertionError(
            "SPEAKER_TRUST_ACTIVITY_MAX_BONUS is unreachable: "
            f"ceil({SPEAKER_TRUST_ACTIVITY_MAX_BONUS}/"
            f"{SPEAKER_TRUST_ACTIVITY_WEIGHT}) * "
            f"{SPEAKER_TRUST_ACTIVITY_WEIGHT} < "
            f"{SPEAKER_TRUST_ACTIVITY_MAX_BONUS}"
        )


_assert_activity_bonus_reachable()

# ── Hybrid memory recall (recall_memory tool backend) ────────────────────────
# When the model calls recall_memory(query), memory_server runs in-memory BM25 +
# cosine recall in parallel, filters each by threshold and top-K, then RRF-fuses and returns at most N.
#
# Candidate scope:
#   - BM25 pool: facts.json + reflections.json + facts_archive.json
#                  (BM25 is cheap over large pools; archives can match rare keywords).
#   - Embedding pool: facts.json + reflections.json
#                  (embeddings are expensive and archives lie beyond normal memory windows;
#                   exclude persona entirely: already rendered into the system prompt,
#                   so retrieving it would be redundant).
#
# Empirical thresholds, to tune in practice: cosine uses the common sentence-embedding
# relevance floor 0.3; BM25 0.1 approximates "any meaningful overlap" (zero overlap is already
# excluded by _bm25_rank's score > 0; 0.1 mainly rejects incidental common-word matches).
#
# ⚠️ Do not set BM25 threshold high: Okapi IDF is naturally low for small pools;
# even exact matches in a single-document pool peak near 0.72 (``log((1-1+0.5)/(1+0.5)
# + 1) × (k1+1)``); two documents sharing the word yield IDF ~0.18. Initial
# 1.0 came from large-corpus experience and filtered all new-user/small-corpus/common-word
# queries, effectively disabling BM25 fallback (Codex P1 review on PR #1385).
HYBRID_RECALL_BUDGET_EACH = 4            # Per-channel (BM25 / embedding) top-K cap.
HYBRID_RECALL_BUDGET_TOTAL = 8           # Total cap after RRF fusion (deduplicate both channels and take top N by score).
HYBRID_RECALL_TIME_BUDGET = 8            # Maximum nearest entries returned for temporal recall (recall_memory time parameter).
HYBRID_RECALL_COSINE_THRESHOLD = 0.3     # cosine below threshold is irrelevant.
HYBRID_RECALL_BM25_THRESHOLD = 0.1       # BM25 below threshold is irrelevant (retain small-pool exact matches).
HYBRID_RECALL_RRF_K = 60                 # RRF constant (k=60, Elastic / OpenSearch default).

# Maximum files retained in recall pool parsing cache (memory.hybrid_recall._POOL_CACHE).
# Each character uses two files (facts_archive.json + reflections.json), so six keeps about
# three characters warm simultaneously.
#
# This gate bounds residency of idle characters: without eviction, every recalled character's
# entire archive stays in memory forever, paying for all installed characters despite usually
# using only one. LRU naturally evicts idle ones.
#
# ⚠️ This does not cap one huge archive: evicting the active character's pool merely brings back
# per-recall parsing costs, defeating the cache's purpose. Limiting a single character's total
# corpus is a separate issue (archive sharding, see #2716).
# Reducing to two reparses every turn when two characters alternate; don't do that to save tens of MB.
HYBRID_RECALL_POOL_CACHE_MAX_FILES = 6

# Resident-byte cap for decoded recall vectors (memory.hybrid_recall._VEC_CACHE).
# Each cosine query decodes every candidate's base64-fp16 embedding into
# fp32: measured ~49ms for 5000 entries (base64+fp16→fp32 ~32ms, per-entry isfinite
# ~7ms, sha256 ~4ms), all on memory_server's event loop. Pool rows themselves
# are already cached by file identity in _POOL_CACHE; stable corpora decode identical vectors
# every time, wasting work. Hits require unchanged (model_id, text object, both row stamps,
# original embedding string); any mismatch falls back to full decoding and validation,
# so this cap affects memory only, never correctness.
#
# ⚠️ The cap counts bytes, not entries, because size varies by dimension
# and includes more than vectors: besides fp32 data (512d ≈ 2KB, 768d ≈ 3KB), entries
# retain references to the row's original embedding string and text. Normally these strings
# live in pool rows without extra overhead, but after _POOL_CACHE evicts those rows,
# these references alone keep them resident. A 512d base64-fp16 string costs ~1.4KB:
# worst-case 2KB + 1.4KB + text ≈ 3.7KB per entry. Count caps can nearly double
# nominal residency (old 10000-entry estimate 20MB, actual worst case ~34MB; 768d exceeds 50MB).
# Byte accounting matches nominal to actual usage, without recalibration for changed dimensions.
#
# ⚠️ Accounting is worst-case: those two strings become cache-exclusive only after
# pool-row eviction. Most vectors are active facts using FactStore's own _facts
# process cache, whose six invalidation sites are exception paths (except: pop; raise); normal writes retain it.
# Thus emb/text usually remain held by pool rows; cache references add no extra
# residency, and actual growth is vector + bookkeeping (~2.4KB measured at 512d versus conservative
# ~3.9KB). Do not read this cap as actual expected memory consumption.
#
# 32MB aims to restore typical actual growth to 20MB: conservative 512d accounting holds
# ~8300 entries, which add 8300 × 2.4KB ≈ 19MB with shared strings, matching
# the count cap's intended 20MB. Sizing capacity for worst-case usage needlessly halves
# typical hit rate, so accounting is conservative but capacity targets typical usage. Vector pools contain active facts +
# reflections only (archive embeddings are projected out before pooling), normally far below this gate;
# it protects extreme multi-character × large-corpus residency, like the file-count gate above.
HYBRID_RECALL_VEC_CACHE_MAX_BYTES = 32 * 1024 * 1024

# Resident byte limit for the BM25 token cache (memory.hybrid_recall._TOKEN_CACHE).
# Passive injection runs BM25 over the entire recall pool on every text turn; previously each call retokenized
# from scratch (CJK 2/3-gram sliding windows account for about three quarters of BM25 time for 5000 entries). Cache by stop names +
# original text, storing a single \x00-joined string (about 10 times less memory than token tuples;
# roughly 3MB for 5000 entries), then split on a hit. Accounting remains conservative: include the original text
# itself, even though pool rows usually retain it. 32MB holds about 50,000 short memories.
HYBRID_RECALL_TOKEN_CACHE_MAX_BYTES = 32 * 1024 * 1024

# ========================================================================
# §3.7 LLM Context & Output Budget
# ------------------------------------------------------------------------
# All input components assembled into LLM messages and output max_tokens
# are centralized here. Design: docs/design/llm-prompt-budget.md
#
# Naming conventions:
#   *_MAX_TOKENS                       → tiktoken o200k_base token count
#                                         （≈ 1.3-1.5 CJK char / 4 EN char）
#   *_TRIGGER_TOKENS                   → token threshold triggering an action (not a hard cap)
#   *_MAX_ITEMS / *_MAX                → item count (messages / deque maxlen / list[-N:])
#   *_MAX_CHARS                        → character count (non-prompt-facing UI /
#                                         payload growth protection only; not evidence of
#                                         an LLM input budget)
#   *_BYTES                            → bytes
#   *_MS                               → milliseconds
#
# Comment format (per constant):
#   - "Purpose": component bounded by this value
#   - "Upstream": capped content source (user input / external API / internal computation)
#   - Design rationale / interactions (if any)
#
# Known user-responsibility items (NOT capped by design):
#   - Verbatim user input assembled into HumanMessage (omni_offline_client.py:413)
#   - OpenClaw magic intent user_text (1MB input for 80-token classification is self-imposed)
#   - User text for emotion analysis
#   - bilibili knowledge_context (user-configured knowledge base)
#   - Plugin custom prompt / strategy files (managed by plugins)
# See docs/design/llm-prompt-budget.md, "Known uncapped items".
# ========================================================================

# ---- Memory: recent history compression ----
RECENT_HISTORY_MAX_ITEMS = 10
"""Recent messages retained after compression.
- Purpose: after CompressedRecentHistoryManager compresses old messages exceeding
  compress_threshold into 1 summary, retain the original list's final N messages.
- Upstream: user/AI conversation stream.
- Interaction: paired with RECENT_COMPRESS_THRESHOLD_ITEMS; N retained messages +
  1 Stage-1 summary = N+1 entries counted toward the next compression."""

RECENT_COMPRESS_THRESHOLD_ITEMS = 20
"""Message-count threshold triggering LLM compression.
- Purpose: call compress_history once when a character's user_histories
  accumulates more than this value.
- Upstream: accumulated conversation-message count."""

RECENT_SUMMARY_MAX_TOKENS = 1000
"""Stage-1 compressed-output token cap.
- Purpose: Stage-1 LLM compresses N original messages into text; output
  above this value triggers Stage-2 further compression (hard limit 500 chars/words).
- Upstream: length of freely generated Stage-1 summaries.
- Trigger: output_tokens above this value -> further_compress() second pass."""

RECENT_PER_MESSAGE_MAX_TOKENS = 500
"""Per-message token cap for compression input.
- Purpose: before prompt assembly, compress_history truncates each original message, retaining head/tail
  via utils.tokenize.truncate_head_tail_tokens, head=tail=250.
- Upstream: user/AI original text, normally 30-500 tokens per turn; long pastes can reach several KB.
- Strategy: retain 250 tokens at each end, replacing the middle with \"…[省略中段]…\"."""

RECENT_COMPRESS_INPUT_BUDGET_TOKENS = 8000
"""Per-segment input token budget for best-effort background compression (segmentation threshold).
- Purpose: if rendered backlog exceeds this value, compress_history uses segmented
  map-reduce: chunks no larger than this value become intermediate summaries, then reduce into a final
  memo, shrinking individual LLM inputs and avoiding oversized-input timeouts. Ordinary compression
  below the threshold retains the original single-pass behavior.
- Upstream: token count of rendered conversation backlog."""

RECENT_HARD_CAP_TOKENS = 60000
"""Hard recent-history limit, a last resort normally unused.
- Purpose: if persistent compression failures (e.g. ongoing 429s, even background fallback failing)
  leave history growing indefinitely, update_history checks total tokens before preserving
  full history; above this value, drop oldest uncompressed dialogue, retaining recent entries
  plus the memo to bound prompts. Intentionally large, serving only as final defense.
- Upstream: accumulated uncompressed recent-history tokens."""

# ---- Memory: reflection ----
REFLECTION_TEXT_MAX_TOKENS = 150
"""Soft cap for one reflection's text.
- Purpose: exceeding this value strips ontology fields on save
  (relation_type / temporal_scope), preserving the text itself.
- Upstream: LLM reflections synthesizing several facts."""

REFLECTION_SURFACE_TOP_K = 3
"""Maximum reflections returned per surfacing query.
- Purpose: truncate get_pending_reflections_for_check / followup and related queries.
- Upstream: candidate reflections with evidence_score≥0 and expired cooldowns."""

REFLECTION_SYNTHESIS_FACTS_MAX = 20
"""Maximum unabsorbed facts per reflection synthesis.
- Purpose: _synthesize_reflections_locked sorts by importance/creation
  time before sending at most this count to LLMs.
- Upstream: prolonged unabsorbed facts accumulate; the outer aget_unabsorbed_facts
  loop currently has no count limit, making this the only protection.
- Rationale: 30 entries × average 50 tokens = 1500 tokens, sufficient for synthesis."""

MEMORY_REFLECTION_SYNTHESIS_INTERVAL_SECONDS = 180
"""Polling interval in seconds for ``_periodic_reflection_synthesis_loop``.
- Purpose: periodically call ``reflection_engine.synthesize_reflections`` per character.
- Rationale: synthesize_reflections idempotently short-circuits same source_fact_ids ->same rid;
  without new unabsorbed facts no LLM runs, so this only caps scheduling frequency.
  **Actual LLM frequency approximately equals occasions when users accumulate ≥5 new unabsorbed
  facts within N seconds**, tied to SignalLoop output rather than this constant. Shortening
  600s to 180s does not proportionally increase LLM cost.
- Choose 180s: align ``AUTO_PROMOTE_CHECK_INTERVAL = 180s``. One loop creates
  pending entries and the other promotes them to confirmed; matching cadence minimizes visible
  state-machine delay (synthesis becomes eligible for promotion within the next tick). Also stagger against
  ``EVIDENCE_SIGNAL_CHECK_IDLE_MINUTES * 60 = 300s``, letting reflection consume new
  SignalLoop facts within 1-2 ticks.
- History: synthesis once ran inside ``/api/proactive_chat`` (added in PR #1015;
  see main_routers/system_router.py blame), tightly coupling the whole chain to frontend
  setTimeout: no frontend / no proactive trigger meant reflections never grew.
  This backend loop decouples synthesis from HTTP/frontend, matching 9 other periodic
  loops (rebuttal / auto_promote / idle_maint / signal_extraction / archive /
  refine, etc.)."""

REFLECTION_RELATED_PER_QUERY_K = 3
"""Maximum absorbed facts recalled by each unabsorbed fact's separate query
at reflection synthesis.
- Upstream: synthesize_reflections allocates independent query budgets through this constant when calling
  ``MemoryRecallReranker.aretrieve_per_query_topk``.
- Rationale (PR #1401 thread decision): global max-pool top-K=6 lets common themes
  crowd out rare themes when 20 unabsorbed facts span many topics. Per-query
  K=3 plus a global cap gives each source its own top-3 anchors unless query
  embedding fails or the candidate pool lacks semantic matches.
- 3 rather than 1 per query accommodates fuzzy themes (Minecraft discussions include
  redstone and mining; cosine top-1 may cover only one), giving LLMs two additional
  examples to recognize topic clusters."""

REFLECTION_RELATED_TOTAL_CAP = 20
"""Final cap after cross-query union/dedup in ``aretrieve_per_query_topk``.
- Rationale: match ``REFLECTION_SYNTHESIS_FACTS_MAX`` (=20), allowing anchor sets
  as large as source sets at worst; actual counts are usually smaller due to overlapping
  nearest neighbors and dedup. Typical batch: 10 unabsorbed ×per_query=3
  =30 candidates -> ~10-15 deduplicated anchors.
- Defensive truncation: worst-case 20 entirely disjoint themes produce per_query=3 ×20=60
  candidates; without duplicates, cap at 20 to prevent prompt growth.
- Prompt cost: 20 ×~50 tok ≈1000 anchor tokens +20 ×~50 tok ≈1000
  source tokens =2k maximum, easily handled by summary-tier models."""

# ---- Memory: temporal scope (memory/temporal.py) ─────────────────────
# Reflection temporal decay uses four temporal_scope values (pattern / state / episode / past).
# state and episode have TTLs and automatically enter the outdated block on expiry; pattern never expires.
# `past` is a legacy-compatible value (possibly stored in old data), rendered directly in the outdated block.
MEMORY_STATE_PAST_DAYS = 7
"""Days after event before state reflections become outdated.
- Purpose: memory.temporal.is_past_for_render moves these into outdated blocks on render.
- Upstream: reflection synthesis entries labeled temporal_scope='state'."""

MEMORY_EPISODE_PAST_DAYS = 3
"""Days after event before episode reflections become outdated.
- Purpose: as above, but one-off episodes decay faster.
- Upstream: reflection synthesis entries labeled temporal_scope='episode'."""

MEMORY_SCHEMA_VERSION_CURRENT = 2
"""Current fact/reflection schema version.
- v1 (missing or explicit 1): old ontology (current/ongoing/None temporal_scope, without
  event_when).
- v2: new ontology (pattern/state/episode) +event_start_at /event_end_at.
- Purpose: background loops gradually reassess/upgrade entries with schema_version<CURRENT."""

# ---- Memory: slow recheck loop (memory/temporal.py + app/memory_server/) ─
MEMORY_RECHECK_ENABLED = True
"""Master switch for slow memory reassessment.
- Purpose: gate app/memory_server/evidence_loops.py _periodic_slow_memory_recheck_loop.
- Disabled: old data stays unupgraded; render fallback treats it as nonfading pattern."""

MEMORY_RECHECK_INTERVAL_SECONDS = 30
"""Per-entry interval for slow reassessment.
- Purpose: reassess 1 reflection/fact every N seconds.
- Upstream: background-loop sleep; design follows §3.5 archive_sweep (slower, low I/O)."""

MEMORY_RECHECK_INITIAL_DELAY_SECONDS = 180
"""Staggered startup delay for slow reassessment.
- Purpose: stagger with 6 existing loops to avoid startup peaks.
- Existing _INITIAL_DELAY_* span 20s~250s; 180s is near the end."""

MEMORY_RECHECK_MAX_ATTEMPTS = 5
"""Failures before abandoning 1 v1 entry, avoiding starvation of later valid v1 entries.
- Failure: LLM exceptions, non-dict results, or invalid temporal_scope
  (reflections allow only pattern/state/episode).
- Counter: `recheck_attempts` (int) on reflection/fact entries.
- At threshold retain schema_version<2 without silently legitimizing upgrades, but filter
  these entries out so others get processed. Developers inspect backlog through logger.debug."""

MEMORY_LIVENESS_MAX_ATTEMPTS = 5
"""Shared limit before advancing progress markers /dead-lettering after N terminal LLM failures.
- Applies to background paths where identical input without counters plus permanent failure
  stalls forever: signal extraction A/B, rebuttal feedback, persona
  correction resolution, fact dedup resolution, refine clusters, outbox handlers.
- Approach: follow `MEMORY_RECHECK_MAX_ATTEMPTS` schema-recheck dead-lettering,
  bounding repeated LLM calls for one cursor /queue head /cluster_hash /operation,
  preventing poisoned windows/payloads from disabling pipelines.
- Failure: None results, exceptions, handler raises, parse failures, and other terminal outcomes.
- 5 matches `MEMORY_RECHECK_MAX_ATTEMPTS`; at 40s per cycle, roughly 3 minutes
  accommodates transient failures; more failures indicate genuinely poisoned input."""

MEMORY_THINKING_OUTPUT_MAX_TOKENS = 8192
"""Shared output allowance for memory calls intentionally enabling thinking.

Applies to history review, Stage-2 signal detection, refine, persona correction /external fusion,
reflection synthesis /rebuttal checks /promotion merge. These bypass factory thinking-disabled
extra_body, using native thinking or reduced modes from ``config.providers.memory_thinking_extra_body``;
reasoning and JSON body share the allowance. Shared guard
``LLM_OUTPUT_GUARD_MAX_TOKENS`` (4096) can be exhausted before JSON appears on default-thinking models
(#3319: qwen3.8-flash signal detection hit exactly 4096 with empty body). 8192 reserves reasoning headroom;
raising limits alone is insufficient, so signal/review prompts also constrain reasoning length.

Do not raise the global guard: many compatible endpoints return 400 when ``max_completion_tokens``
exceeds model output limits. ``memory.thinking_llm`` retries once at 4096 for that 400.
Short nonthinking JSON calls (summaries, dedup, recheck) retain 4096."""

MEMORY_REVIEW_OUTPUT_EXHAUSTION_MAX_ATTEMPTS = 3
"""Consecutive output-exhaustion failures before pausing history review.

- Count only explicit provider ``length`` /``max_tokens``, or empty bodies with output tokens
  reaching the actual requested allowance; networking, 429, ordinary JSON errors still use
  ``MEMORY_LIVENESS_MAX_ATTEMPTS`` fingerprint-based backoff.
- After 3 failures pause review per character. New messages do not resume it; only current context
  tokens strictly below the minimum seen during failure (usually recent compression) reset/resume."""

MEMORY_DEAD_LETTER_SELF_HEAL_SECONDS = 5 * 60 * 60
"""Time-based dead-letter cooldown recovery window, seconds.

- Problem: entries frozen at `MEMORY_LIVENESS_MAX_ATTEMPTS` (reflection synthesis /
  schema recheck /refine clusters) reset only after success or changed input. A **single prolonged outage**
  (retired correction snapshots timing out /cloud-save maintenance /read-only filesystems)
  drives innocent entries to MAX, permanently freezing them even after recovery because
  content is unchanged and they cannot reenter candidates.
- Remedy: allow **one** probe per window after freezing. Success resets counts and restores
  processing; failure restarts cooldown. Temporary outages self-heal after 5h,
  while genuinely poisoned input is limited to one attempt every 5h.
- **Exclude memory_review**: tail-fingerprint changes already reset it
  whenever users send new messages; time-based recovery is unnecessary and inappropriate while idle.
- 5h: refine cron runs every 30min, so >2.5h outages drive entries to MAX;
  5h allows probing next cycle after recovery while far exceeding incidental jitter windows."""

# ---- Memory: followup picker (memory/reflection.py) ─
REFLECTION_FOLLOWUP_WEIGHTED = True
"""Whether proactive follow-up sampling is randomly weighted by evidence_score.
- Purpose: _filter_followup_candidates; False restores old behavior, taking
  top-K in persisted order.
- Rationale: large pools otherwise repeatedly choose the same entries, making proactive speech repetitive."""

REFLECTION_FOLLOWUP_WEIGHT_BASE = 0.5
"""Minimum sampling weight, including score=0, preventing degeneracy when all scores are 0."""

# ---- Memory: summary stale prompt (memory/recent.py) ─
RECENT_SUMMARY_STALE_HOURS = 1
"""After this many hours since the last actual LLM past-block update, prepend an elapsed-time
hint on the next compression so the LLM moves outdated material into the summary's
internal past block.
- Anchor: not last summary time, since summaries run on every compression; anchoring there
  perpetually delays hints until 1 hour after the latest compression, defeating a stable
  every-N-hours past-block update cadence. Record actual hint injection time instead,
  when the LLM was explicitly asked to refresh the past block.
- Upstream: recent_meta.json last_past_block_update_at.
- Note: summary past blocks temporarily demote content only in current sessions, without persisting
  to reflections/persona."""

# ---- Memory: persona ----
PERSONA_MERGE_POOL_MAX_TOKENS = 4000
"""Total token cap for same-entity persona+reflection pools during promotion merge.
- Purpose: _allm_call_promotion_merge assembles all confirmed/promoted
  persona/reflections for one entity; cap prevents runaway pool size.
- Upstream: persona/reflections accumulated over time for one entity.
- Do not reuse PERSONA_RENDER_MAX_TOKENS: rendering feeds main conversations,
  while merging feeds promotion LLMs that need larger pools to judge merges."""

# ---- Memory: external memory import · persona LLM fusion budget ----
# Background and rationale: persona rendering in the system prompt has a strict
# token cap (PERSONA_RENDER_MAX_TOKENS; scoped group rendering gives each subject its own,
# but external imports write only legacy private corpora, so the single pool applies here).
# External workspaces (OpenClaw/Hermes USER.md / SOUL.md) often contain dozens of free-form
# Markdown entries; appending after exact deduplication quickly overfills persona and displaces
# the character's impressions. USER.md / SOUL.md require LLM fusion (summarize/merge/deduplicate/disambiguate/rank) into this budget
# before persisting as non-protected persona entries.
# Per-entity fusion output caps (tokens). neko (SOUL.md assistant persona) gets more than
# master (USER.md user); combined total < PERSONA_RENDER_MAX_TOKENS leaves competition space
# for persona entries naturally accumulated in conversation.
EXTERNAL_IMPORT_PERSONA_NEKO_MAX_TOKENS = 1000
EXTERNAL_IMPORT_PERSONA_MASTER_MAX_TOKENS = 600
# Fusion LLM input-pool cap (total raw-candidate tokens, preventing oversized prompts).
EXTERNAL_IMPORT_FUSION_INPUT_MAX_TOKENS = 6000
# Soft per-entry fusion output cap (tokens): prevents merging many entries into one long text
# that displaces others during rendering's greedy whole-entry trimming.
EXTERNAL_IMPORT_FUSION_ENTRY_MAX_TOKENS = 200
# Token cap for each fusion candidate's breadcrumb (source_section) prefix: breadcrumbs provide section
# context only; without a cap, long titles exhaust the input pool and displace later candidate bodies
# from LLM input via tail truncation, permanently omitting later memories.
EXTERNAL_IMPORT_FUSION_BREADCRUMB_MAX_TOKENS = 24
# When daily diaries (memory/·memories/YYYY-MM-DD.md) undergo LLM fact extraction, cap
# one day's combined diary input tokens to prevent oversized prompts.
EXTERNAL_IMPORT_DAILY_INPUT_MAX_TOKENS = 6000
# Daily extraction concurrency cap: one LLM call per day; 30 serial days cost ~300–900s, hitting
# upstream commit forwarding's 240s wall. Bounded concurrency reduces wall time to days / concurrency × call time.
# Keep it moderate: extraction shares provider quotas, and per-character locks serialize disk writes.
EXTERNAL_IMPORT_DAILY_MAX_CONCURRENCY = 4
# Maximum diary days actually requiring extraction per import (daily fingerprint idempotent skips excluded):
# 45 days / concurrency 4 ≈ 12 batches × ten-plus seconds fits 240s. Excess returns too_large
# to prompt split imports; later imports skip prior days by fingerprint, avoiding repeated costs.
EXTERNAL_IMPORT_DAILY_MAX_FILES = 45

PERSONA_CORRECTION_BATCH_LIMIT = 10
"""Batch size for one persona-correction resolution.
- Purpose: _resolve_corrections_locked sends the first N entries from
  pending_corrections to LLM judgment, leaving the rest for later cycles.
- Upstream: pending_corrections queue."""

PERSONA_VERSION_HISTORY_MAX = 5
"""Maximum version_history per persona entry (Phase B-1).
- Purpose: trim to the latest K after resolve_corrections replace/merge or
  apply_refine_actions merge/modify appends, bounding long-running growth.
- Discard old versions directly; version_history is audit metadata, not data, with little value beyond 5 entries."""

MEMORY_LLM_HARD_TIMEOUT_SECONDS = 110
"""Hard timeout in seconds for every background memory LLM call.
- Upstream proxy hard-times out at 120s; clients need ≥10s margin or proxies
  terminate first without any response. **Never exceed 110**.
- Covers reflection synthesis /persona correction /memory_refine /
  recent review_history and all background LLM calls.
- Excludes user-facing chat/realtime paths, which have separate stricter timeouts."""

LLM_OUTPUT_GUARD_MAX_TOKENS = 4096
"""max_completion_tokens **runaway guard** for variable-length LLM output, not a tight budget.
- Purpose: calls with naturally variable output and no tight task-specific budget: structured memory
  JSON (reflection /recall /persona /facts /refine /dedup recheck),
  fact dedup, card-assist, window-title keywords, etc.
- Value: 4096. **Stay within common providers' output limits**: `max_completion_tokens`
  is a ceiling, not a target, but OpenAI/compatible providers often validate it against
  model output limits and return 400 rather than defaulting. Hence `omni_offline_client.
  _budget_to_max_tokens` deliberately **omits** unlimited fields (\"large fixed values get
  rejected as out-of-range by some providers\"). 8000 breaks older/self-hosted models below 8000;
  4096 is accepted by most summary/correction/agent-tier models,
  while still providing ample normal-output headroom, including thinking reasoning.
- Policy: LLM_OUTPUT_BUDGET lint requires token budgets for each client; this constant centralizes
  calls lacking tight budgets but still requiring a ceiling (docs/design/llm-prompt-budget.md §0).
- Excludes explicitly tight-budget calls (emotion /translation /vision /plugin coarse filtering),
  which retain dedicated *_MAX_TOKENS constants rather than substituting this guard.
- Remaining boundary: very old/small models below 4096 output may still return 400; installations can lower this constant.
  Full robustness needs per-model metadata, currently untracked, so use a conservative fixed value."""

ICEBREAKER_FREE_TEXT_INTERPRETER_TIMEOUT_SECONDS = 20.0
"""New-user icebreaker free-input interpreter LLM timeout, seconds. Fail quickly on stalled user-facing short classification/reply calls."""

ICEBREAKER_FREE_TEXT_OUTPUT_MAX_TOKENS = 512
"""New-user icebreaker free-input interpreter output cap. Output is fixed JSON; 512 is only a short-task ceiling."""

ICEBREAKER_FREE_TEXT_ASSISTANT_LINE_MAX_TOKENS = 800
"""Icebreaker free-input interpreter: input-token cap for current YUI dialogue."""

ICEBREAKER_FREE_TEXT_USER_TEXT_MAX_TOKENS = 800
"""Icebreaker free-input interpreter: user free-input token cap."""

ICEBREAKER_FREE_TEXT_OPTION_LABEL_MAX_TOKENS = 200
"""Icebreaker free-input interpreter: token cap per option label."""

ICEBREAKER_FREE_TEXT_HISTORY_TEXT_MAX_TOKENS = 240
"""Icebreaker free-input interpreter: token cap per recent free-input text segment."""

ICEBREAKER_FREE_TEXT_HISTORY_MAX_ITEMS = 4
"""Icebreaker free-input interpreter: maximum recent free-input records included."""

ICEBREAKER_FREE_TEXT_REPLY_MAX_TOKENS = 240
"""Icebreaker free-input interpreter: token cap for sanitized model reply fields."""

DIALOG_LLM_STREAM_TIMEOUT_SECONDS = 180
"""Total main-conversation streaming LLM request timeout, seconds, as a hang guard.
- Purpose: OmniOfflineClient streaming chat client, shared by stream_text /
  prompt_ephemeral through self.llm. SDK timeout caps the entire request,
  meaning complete-response generation for streaming.
- Value: deliberately large at 180s; normal short TTS /3000-token summary-mode replies
  finish well below it. Only truly stuck upstream streams (neither tokens nor closure)
  trigger fallback connection release. Larger than MEMORY_LLM_HARD_TIMEOUT_SECONDS because
  user-facing main conversation should wait longer rather than wrongly truncate normal replies.
- Policy: LLM_OUTPUT_BUDGET lint requires every client timeout; this constant supplies
  the main streaming-conversation value."""

FOCUS_THINKING_EXTRA_TOKENS = 800
"""Additional max_completion_tokens allowance for focus /thinking-on turns.
- Background: thinking models (Qwen enable_thinking /GLM·Kimi·Doubao thinking.type /
  OpenRouter reasoning.effort) share one max_completion_tokens pool between reasoning
  and final replies (docs/design/llm-prompt-budget.md §0),
  so thinking consumes reply allowance and shortens visible answers.
- Purpose: temporarily raise API max_completion_tokens by this value only on thinking_on
  turns, reserving reasoning headroom without changing Python-side length guards;
  visible replies still end at max_response_length.
- Routing: per-call override through _focus_stream_overrides ->astream ->
  ChatOpenAI._params, never mutating self.llm (same per-call route as extra_body,
  concurrency-safe, automatically restored next turn).
- Scope: Claude focus remains thinking-off (config/providers.py), making this naturally
  a noop; Gemini's separate thinking_budget=800 is also covered by this headroom.
- Value: flat 800 without provider branches, effective only when thinking actually runs."""

# ---- Memory: refine (Phase A-3) — MemoryRefineEngine cron parameters ----
# Generic cosine clustering + LLM decision pipeline reused by PERSONA_REFINE and
# REFLECTION_REFINE cron jobs. Facts are immutable (information sources for merge/modify
# only, never split/discard targets).

MEMORY_REFINE_COSINE_THRESHOLD = 0.82
"""Refine-cluster cosine threshold, slightly looser than FACT_DEDUP's 0.85: persona
and reflection texts are longer, making 0.85+ harder; clustering seeks relatedness
rather than deduplication equivalence, so looser matching is appropriate."""

MEMORY_REFINE_TOPK_PER_ENTRY = 5
"""Maximum retained neighbors per entry in adjacency graphs, the second dual cap. Prevent highly
referenced hub entries from pulling large groups of weakly related items into one cluster."""

MEMORY_REFINE_CLUSTER_SIZE_MAX = 6
"""Maximum members per cluster. More than 6 is difficult for consistent LLM processing;
truncate overflow to the top 6 by cosine strength."""

MEMORY_REFINE_REVISIT_AFTER_DAYS = 30
"""Time before re-reviewing the same cluster_hash, even when all members match hashes.
Monthly LLM behavior may drift; 1 review per month has manageable cost."""

MEMORY_REFINE_CLUSTERS_PER_PASS = 3
"""Maximum clusters sent to LLMs per cron trigger. Take N by ascending starvation
min(last_refine_at) within each cluster. About 3 LLM calls block for 60-90s."""

MEMORY_REFINE_CRON_INTERVAL_SECONDS = 1800
"""PERSONA_REFINE / REFLECTION_REFINE polling interval, seconds.
- Every 30 minutes; engine cluster_hash checks skip recently reviewed clusters
  at zero cost, so frequent polls do not waste LLM tokens.
- Both cron loops share the interval, staggering starts through _INITIAL_DELAY_*."""

# ---- Memory: scoped subject eviction (group memory series 5/7, track one) ----
# Scoped group/member entries receive no evidence signals (facts persist with
# signal_processed=True, reflections directly confirmed, surfacing legacy-only),
# making score-driven sub_zero archiving structurally unreachable. Scoped eviction uses
# time only: archive subjects whose last write expired. Derive criteria from data
# (per-subject max(created_at), primarily facts, conservatively including reflection/persona timestamps
# in the maximum). Deliberately avoid a sidecar ledger: PR #2394 showed ledgers outliving data
# become permanently disconnected; data-embedded criteria cannot structurally diverge.

SCOPED_SUBJECT_ARCHIVE_ENABLED = True
"""Master switch for time-driven scoped-subject archiving. Disabled sweeps skip this stage,
without affecting score-driven legacy archiving."""

SCOPED_SUBJECT_STALE_DAYS = 90
"""Strictly more than this many days since a subject's last write archives all its
fact/reflection/persona entries. Exactly N days does not archive. 90 days,
roughly a quarter without new group/member writes, makes continued rendering/recall
value lower than contamination/contradiction risk."""

SCOPED_SUBJECT_ARCHIVE_DRY_RUN = False
"""True logs only would-archive scope identifiers/counts without original text,
changing no data. Diagnostic escape hatch for investigating criteria."""

SCOPED_SUBJECT_ARCHIVE_MIN_INTERVAL_SECONDS = 21600
"""Minimum scoped-subject archiving interval within archive sweeps, seconds.
Day-level criteria need only 6h checks; throttle via in-process timestamps
inside the 1h _periodic_archive_sweep_loop."""

# ---- Memory: lightweight scoped refine (group memory series 5/7, track two) ----
# Scoped entries cannot enter the base MemoryRefineEngine's two cron jobs (entity whitelist
# permits only master/neko/relationship), so semantic duplicates/conflicts coexist forever;
# the 2000-token trim decides what enters prompts. This is the lightweight scoped counterpart:
#   Base = correction tier + thinking + three clusters/turn + periodic full scans;
#   Scoped = summary tier + no thinking + one cluster per turn across all characters +
#            triggers only when one subject reaches the entry-count threshold.
# Bucket key must be (kind, subject_id, scope): all groups share entity='group_chat',
# so entity bucketing would place groups A and B in one cosine cluster, overwriting across groups
# (documented precedent: _bucket_key docstring in memory/fact_dedup.py).

SCOPED_REFINE_MIN_ENTRIES = 8
"""Minimum entries triggering refine per (subject, store) pool. Per-subject rendering
budget 2000 tokens fits ~10-15 typical entries; reviewing from 8 enables merges
before budget trimming silently discards entries."""

SCOPED_REFINE_COSINE_THRESHOLD = 0.82
"""Scoped-cluster cosine threshold, matching main refine, which seeks
relatedness rather than deduplication equivalence."""

SCOPED_REFINE_TOPK_PER_ENTRY = 5
"""Maximum graph neighbors retained per entry, matching main refine's second dual cap."""

SCOPED_REFINE_CLUSTER_SIZE_MAX = 5
"""Per-cluster cap, tighter than main refine's 6. Lite prompts lack thinking;
limit each decision's scope for stable summary-tier output."""

SCOPED_REFINE_REVISIT_AFTER_DAYS = 30
"""Re-review window for identical cluster_hash, matching main refine."""

SCOPED_REFINE_CRON_INTERVAL_SECONDS = 1800
"""Scoped-refine polling interval, seconds. At most 1 LLM call across all characters
per cycle (1 cluster); cluster_hash skipping makes frequent empty polls free."""

SCOPED_REFINE_LLM_TIMEOUT_SECONDS = 60
"""Scoped-refine LLM timeout, seconds. No thinking and small single-cluster output
allow 60s, matching fact_dedup judgment calls, rather than main refine's 110s; lite
pipelines exist specifically to cost less than main refine."""

# ---- Memory: recall ----
RECALL_COARSE_OVERSAMPLE = 3
"""Vector coarse-rank oversampling multiplier.
- Purpose: top_k = budget * this value; coarse ranking retrieves 3× candidates for LLM rerank
  selection.
- Upstream: embedding-search candidate pool."""

RECALL_PER_CANDIDATE_MAX_TOKENS = 200
"""Per-candidate text cap for LLM reranking input.
- Purpose: _fine_rank truncates each candidate.text before assembling candidates.
- Upstream: archived fact/observation text."""

RECALL_CANDIDATES_TOTAL_MAX_TOKENS = 15000
"""Total token cap after assembling LLM-rerank candidates.
- Purpose: count/per-entry caps can still multiply into oversized input; final fallback.
- Upstream: serialized capped candidate lists.
- Rationale: theoretical budget*3 ×per_candidate =600*200=120k; 25k is
  practically safe. Trim excess from the tail, retaining higher scores."""

# ---- Memory: evidence signal detection ----
EVIDENCE_PER_OBSERVATION_MAX_TOKENS = 200
"""Per-observation text cap for Stage-2 signal-detection input.
- Purpose: _allm_detect_signals truncates each text before observation assembly.
- Upstream: archived fact/observation text."""

EVIDENCE_OBSERVATIONS_TOTAL_MAX_TOKENS = 15000
"""Total token cap after assembling Stage-2 signal-detection observations.
- Purpose: fallback preventing per-entry limits multiplied by counts from overflowing.
- Upstream: serialized capped observation lists."""

EVIDENCE_DETECT_SIGNALS_MAX_NEW_FACTS = 20
"""Maximum new_facts per Stage-2 signal-detection batch.
- Purpose: _allm_detect_signals takes top N new_facts by importance DESC;
  excess remains in facts.json with `signal_processed=False`, drained in another
  idle-maintenance batch.
- Match FACT_DEDUP_BATCH_LIMIT (comfortable LLM N×M matching batches
  around 20), avoiding lost focus across 30+ new_facts.
- Upstream: new facts extracted by Stage-1 LLM."""

NEGATIVE_KEYWORD_CHECK_CONTEXT_ITEMS = 3
"""User-message context count for negative-keyword checks.
- Purpose: memory_server._amaybe_trigger_negative_keyword_hook takes the final N
  user messages as LLM context.
- Upstream: conversation stream."""

# §3.9 merge-on-promote throttling (used by PR-3).
EVIDENCE_PROMOTE_RETRY_BACKOFF_MINUTES = 30      # Consecutive failure throttling window.
EVIDENCE_PROMOTE_MAX_RETRIES = 5                 # Dead-letter threshold.

# §6.5 pre-merge reviewer gates: draft values retained until reviewer approval.
# Gate 1: half-life (§3.5.2).
EVIDENCE_REIN_HALF_LIFE_DAYS = 30        # Reinforcement half-life.
EVIDENCE_DISP_HALF_LIFE_DAYS = 180       # Disputation half-life (longer than rein).

# Gate 2: context volume for reflection synthesis (§3.4.3 stage 2).
REFLECTION_SYNTHESIS_CONTEXT_ABSORBED_COUNT = 10   # Reference the latest N absorbed facts.
REFLECTION_SYNTHESIS_CONTEXT_ABSORBED_DAYS = 14    # Within N days as well.

# Gate 3: LLM tier selection (candidates in RFC §6.5 Gate 3 table).
# "summary" = qwen-plus class; "correction" = qwen-max class; "emotion" = qwen-flash class.
EVIDENCE_EXTRACT_FACTS_MODEL_TIER = "summary"       # Stage-1 fact extraction.
EVIDENCE_DETECT_SIGNALS_MODEL_TIER = "summary"      # Stage-2 signal mapping decisions.
EVIDENCE_NEGATIVE_TARGET_MODEL_TIER = "emotion"     # Secondary keyword classification (latency-sensitive).
EVIDENCE_PROMOTION_MERGE_MODEL_TIER = "correction"  # Promote merge decisions.


# memory-enhancements P2: vector hybrid retrieval (memory/embeddings.py).
# Master kill switch + auto-resolve hints. The service degrades to no-op
# under any of: VECTORS_ENABLED=False / RAM < min / no onnxruntime / no
# model file. See memory/embeddings.py docstring for the full fallback
# matrix. Defaults are tuned so the feature is opt-out at the install
# level (drop the model file → on; remove it → off) without a config edit.
# Defaults unchanged; optional env override provides an escape hatch, otherwise original auto policy applies.
# Typical use: auto disables vectors on old CPUs lacking AVX-VNNI; users can set
# NEKO_VECTORS_QUANTIZATION=int8 to force int8 (slow but correct) without repackaging.
VECTORS_ENABLED = _read_bool_env("VECTORS_ENABLED", True)        # master kill switch
VECTORS_EMBEDDING_DIM = "auto"               # "auto" | 32/64/128/256/512/768
VECTORS_QUANTIZATION = _read_str_env(        # "auto" | "int8" | "fp32" (fp32 needs model.onnx on disk)
    "VECTORS_QUANTIZATION", "auto", allowed=("auto", "int8", "fp32"),
)
VECTORS_MIN_RAM_GB = 4.0                     # below this → disabled regardless
VECTORS_MODEL_PROFILE_ID = "local-text-retrieval-v1"  # anonymous profile id + local model folder
# Warmup: the ONNX session (~150 MB unpack) loads on first triggering
# event after startup. The warmup task waits up to this many seconds
# after startup OR until first /process call, whichever comes first.
VECTORS_WARMUP_DELAY_SECONDS = 30
