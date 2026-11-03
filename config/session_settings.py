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

"""Session lifecycle, anti-repeat, avatar, omni, and proactive search limits."""

# ---- Main: session / avatar / omni ----
SESSION_ARCHIVE_TRIGGER_TOKENS = 5000
"""Cumulative token threshold triggering session-history archiving.
- Purpose: core.py checks after each turn-end; exceeding sets
  is_preparing_new_session=True, triggering memory compression/new-session preparation.
- Upstream: current session conversation_history.
- Scope: OmniOfflineClient only; realtime keeps no history and uses turn triggers.
- Rationale: average user turn ~150 tokens +AI turn ~400 tokens =
  ~550/round; 5000/550 ≈9 rounds, matching SESSION_TURN_THRESHOLD."""

SESSION_TURN_THRESHOLD = 10
"""User-turn threshold triggering session archiving.
- Purpose: core.py:_session_turn_count >=this value starts new-session preparation,
  ORed with SESSION_ARCHIVE_TRIGGER_TOKENS; either suffices.
- Counts only user inputs, not AI replies; see core.py:980.
- Rationale: ~10 turns correspond to ~5500 total tokens, aligning token triggers."""

USER_DIRECTIVE_TTL_SECONDS = 3 * 86400
"""**Base** lifetime of explicit user ban-topic commands (\"别再提 X / stop saying X\").
- Purpose: memory/user_directives.py active checks plus render_prompt_block
  inject into the next session's system prompt.
- Rationale: attitudes last between one turn and permanent preferences; 3 days covers
  consecutive sessions without hardening transient feelings into lasting personas.
- ⚠ This applies at hit_count==1, mentioned once. Repetitions increase linearly
  up to USER_DIRECTIVE_TTL_MAX_SECONDS; see that constant.
- Upstream: main_logic/core.py:_build_initial_prompt injection;
  memory/user_directives.py:UserDirectivesManager liveness/cleanup."""

USER_DIRECTIVE_TTL_MAX_SECONDS = 30 * 86400
"""Repeated ban-topic TTL ceiling: 30 days.
- Purpose: memory/user_directives.py:_effective_ttl computes
  min(USER_DIRECTIVE_TTL_SECONDS * hit_count, this constant). 1 mention gives 3 days,
  2 gives 6; 10 or more caps at 30 days.
- Rationale: one mention may be momentary emotion, repetitions stable preferences; lifetimes
  should differ, but remain capped or dozens of repeats effectively become permanent personas.
  Ban lists constrain prompts only, currently lacking user deletion UI (management follows separately).
  30 days respects genuine avoidance without letting one extraction mistake persist too long.
- ⚠ Only another **hit** refreshes expire_at; silence never renews. The cap means
  retention after the final mention, not cumulative lifetime."""

USER_DIRECTIVE_MAX_ACTIVE = 20
"""Maximum active ban-topic directives injected into system prompts.
- Purpose: UserDirectivesManager.get_active takes N latest last_seen entries.
- Rationale: over 20 simultaneously active topics almost certainly indicates extraction errors
  or deliberate spam; truncate rather than overload prompts.
- ⚠ This caps **reading/injection**, not persistence; see
  USER_DIRECTIVE_MAX_STORED for disk limits."""

USER_DIRECTIVE_MAX_STORED = 60
"""user_directives.json entry cap; evict oldest last_seen_at first.
- Purpose: UserDirectivesManager.record rotates after writes.
- Need: before incremental TTL, 3-day expiration naturally bounded files; extending
  up to 30 days lets spammers create thousands of entries. Every record reads/writes
  whole files on **synchronous fan-out** (see _save_unlocked),
  slowing every user message as files grow.
- Value: 3 times MAX_ACTIVE=20. Injection needs only latest 20; retaining two extra
  windows allows old neglected entries to be mentioned again; more only wastes disk.
- Eviction mirrors get_active ordering: both use last_seen_at, one injecting newest N,
  the other removing oldest; least recently mentioned leaves first."""

# ── Anti-repeat BM25 settings ─────────────────────────────────
ANTI_REPEAT_BG_WINDOW = 100
"""Anti-repeat background corpus window: latest N AI outputs.
- Purpose: memory/anti_repeat.py retains N texts for DF calculation.
- Rationale: 100 is roughly half-to-one day of dialogue; short windows destabilize IDF,
  while long ones keep week-old incidental topics artificially unique/high-IDF."""

ANTI_REPEAT_FG_WINDOW = 5
"""Anti-repeat foreground window: recent N entries checked for repetition.
- Purpose: BM25 uses these as query corpus for TF; compare new drafts with these 5.
- Rationale: 5 matches perceptible recent repetition; 7 or more becomes hard to remember."""

ANTI_REPEAT_FG_TTL_SECONDS = 600.0
"""Foreground anti-repeat freshness limit, seconds, affecting only FG (TF/repetition),
not BG (DF/IDF background, still count-capped by ANTI_REPEAT_BG_WINDOW).
- Purpose: memory/anti_repeat.py score_draft/top_recent_topics includes only outputs within
  ANTI_REPEAT_FG_TTL_SECONDS in foreground TF; older entries remain in BG
  contributing IDF.
- Rationale: fix idle deadlocks. Proactive chat triggers only while users idle, and drops never
  update corpus, only successful deliveries do; recent same-topic entries (screen commentary)
  freeze FG, repeatedly producing high BM25 ->permanent drops ->no more speech. TTL makes
  FG empty naturally after idle, so bm25_score's `not fg_docs` returns 0 and allows delivery.
- Value: 10 minutes. Guard immediate repetition, not revisiting topics ten minutes later.
  BG IDF context has no TTL, preserving score quality."""

ANTI_REPEAT_UNANSWERED_WINDOW = 64
"""Maximum proactive entries inspected for long-window repetition during continuous user inactivity.
- Unlike FG's latest 5 /10 minutes, detect similarities recurring every several rounds.
- 64 covers a sustained high-frequency proactive period while keeping O(N) small."""

ANTI_REPEAT_UNANSWERED_MAX_AGE_SECONDS = 86400.0
"""Maximum long-window repetition lookback during continuous inactivity: 24 hours.
- Include only proactive messages after the user's latest genuine message or process observation start;
  any user interaction immediately invalidates old unanswered evidence.
- 24 hours catches nonconsecutive cross-hour repetition without making older topics permanent bans."""

ANTI_REPEAT_UNANSWERED_SIMILARITY_THRESHOLD = 0.85
"""Ngram Dice similarity threshold between proactive messages in long windows.
- Target near-verbatim user-visible repetition, without rejecting ordinary same-topic paraphrases.
- Still below literal SequenceMatcher's 0.90 hard threshold; long windows catch highly similar
  content recurring several rounds or hours later.
- Require multiple matches plus continued user inactivity; never block on this signal alone."""

ANTI_REPEAT_UNANSWERED_MIN_MATCHES = 2
"""Minimum similar unanswered proactive-history matches triggering draft intervention.
- 2 historical matches make this draft the third occurrence;
  one topic collision is weak evidence, insufficient for regen/drop."""

ANTI_REPEAT_UNANSWERED_MIN_DRAFT_TOKENS = 4
"""Minimum ngram count for long-window proactive content-shape scoring.
- Separate from generic BM25's 12-token threshold: break reminders are intentionally short;
  English/Spanish/Portuguese/Russian space-tokenized messages often have 4~10 keywords, yet still belong in unanswered evidence windows.
- Keep a 4-token floor so tiny outputs such as “嗯”“好” cannot pollute persisted corpora."""

ANTI_REPEAT_INJECT_TOP_K = 6
"""Recent frequent-topic terms injected into system prompts.
- Purpose: build_recent_topics_block takes top-K BM25 ngrams.
- Rationale: 6 terms cover several topics without long prompts."""

ANTI_REPEAT_REGEN_THRESHOLD = 8.0
"""Proactive outbound BM25 above this total triggers 1 regeneration.
- Purpose: system_router scores completed streams, then resamples once with avoidance prompts
  when over threshold.
- Rationale: empirical starting point, adjustable through testbench later."""

ANTI_REPEAT_DROP_THRESHOLD = 16.0
"""After proactive regeneration, abandon delivery if BM25 still exceeds this value.
- Purpose: prevent LLMs repeatedly stuck on one topic.
- Rationale: 2 times REGEN gives one correction opportunity."""

ANTI_REPEAT_BM25_K1 = 1.5
"""BM25 k1 controlling TF saturation speed; Robertson's classic recommended value."""

ANTI_REPEAT_BM25_B = 0.75
"""BM25 b controlling document-length normalization; Robertson's classic recommended value."""

ANTI_REPEAT_MIN_DRAFT_TOKENS = 12
"""Drafts shorter than this token count bypass scoring.
- Purpose: avoid wrongly rejecting tiny replies such as \"嗯。\" or \"好\".
- Rationale: ~12 ngram tokens are needed for stable BM25 signals."""

ANTI_REPEAT_EXEMPT_SOURCE_TAGS = frozenset({"MUSIC", "MEME"})
"""Source tags whose proactive repetition criterion shifts from dialogue to material.
- Motivation: BM25 detects topic/wording repetition, but material-push channels have inherently
  templated intros (songs \"换首歌 / 这旋律 / 听听看\", memes \"看这个 / 笑死\"); similar wording
  accompanies different tracks/search terms. Dialogue BM25 inherently misclassifies these;
  after several requested songs, music intros fill FG and inflate scores, dropping later spontaneous
  recommendations and making music extremely infrequent.
- Semantics: deduplicate material itself: MUSIC by track, MEME by search
  keywords, not images. Fresh material bypasses dialogue hard gates (literal similarity
  and BM25 regen/drop). Repeated tracks/keywords
  fall back to ordinary dialogue checks, still allowing nonrepetitive wording.
- Also exclude these channels' lines from anti-repeat corpus (see finish_proactive_
  delivery), preventing templated intros polluting FG and other channels' baselines;
  system_router._proactive_material_history deduplicates recent material identifiers.
- Exception: ANTI_REPEAT_VERBATIM_GUARD_SOURCE_TAGS still blocks verbatim MEME text with fresh material."""

ANTI_REPEAT_VERBATIM_GUARD_SOURCE_TAGS = frozenset({"MEME"})
"""Subset of ANTI_REPEAT_EXEMPT_SOURCE_TAGS still blocking verbatim repetition with fresh material.
- Motivation: MEME identity uses search terms, not images; random-trending fallback leaves keywords
  empty, always fresh/exempt, while Phase 2 cannot see images and may repeat captions
  exactly (\"快看这个，笑死我了\").
- Semantics: block only exact text after removing whitespace/punctuation/symbols/emoji, via
  _find_verbatim_recent_proactive_chat. Similar but different captions on new images still
  pass, bypassing 0.90 literal similarity, BM25, and unanswered long windows; text stays outside
  anti-repeat corpus. Hits regenerate once using the original line,
  dropping only if the rewrite remains verbatim.
- Nonfresh material uses full dialogue checks like other channels; this exemption is inactive."""

AVATAR_INTERACTION_DEDUPE_MAX_ITEMS = 32
"""_recent_avatar_interaction_ids deque maxlen.
- Purpose: deduplicate processed avatar interaction IDs.
- Upstream: UI/avatar interaction-event sequence."""

AVATAR_INTERACTION_DEDUPE_WINDOW_MS = 8000
"""Avatar-interaction deduplication window.
- Purpose: cross_server _should_persist_avatar_interaction_memory avoids duplicate persistence
  for identical keys within this window.
- Upstream: UI interaction timestamps."""

AVATAR_INTERACTION_CONTEXT_MAX_TOKENS = 80
"""Avatar-interaction text-context token cap.
- Purpose: _sanitize_avatar_interaction_text_context truncates before LLM
  prompts use it as interaction scene context.
- Upstream: scene text passed through by avatar clients."""

PENDING_USER_IMAGES_MAX = 3
"""Recent images retained in cross_server pending_user_images.
- Purpose: del pending_user_images[:-N] sliding window.
- Upstream: user-uploaded image queue."""

OMNI_RECENT_RESPONSES_MAX = 3
"""Recent AI-reply turns retained by omni_offline /omni_realtime.
- Purpose: _recent_responses pop(0) maintains a sliding window for
  _check_repetition.
- Upstream: AI reply history in the current session."""

OMNI_WS_FRAME_LIMIT_BYTES = 250_000
"""Safe omni_realtime WebSocket frame-size threshold.
- Purpose: reject oversized payloads before sending, below the server's 256KB cap.
- Upstream: serialized WS bytes, not tokens.
- Cross-reference: main_logic/proactive_delivery.py
  TURN_ATTACHED_IMAGE_MAX_TOTAL_BYTES (8 MiB/turn) budgets images on **text paths**,
  not the same gate. Realtime first hits this 250KB frame limit, measured on
  JSON after base64 expands images ~4/3, roughly 180KB image bytes,
  ~45 times tighter than 8 MiB. Passing one does not guarantee delivery through the other; review both on changes."""

# ---- Main: proactive search & emotion ----
PROACTIVE_PHASE1_FETCH_PER_SOURCE = 10
"""Fixed fetch count per Phase-1 source.
- Purpose: shared limit for fetch_news_content/fetch_video_content and related functions.
- Upstream: external web/news/video results."""

PROACTIVE_PHASE1_TOTAL_TOPICS = 12
"""Total candidate topics supplied to Phase-1 filtering LLMs.
- Purpose: merge/deduplicate all sources and take this many for LLM selection.
- Upstream: capped aggregate fetch results.
- Rationale: originally 20, when external content dominated and needed a large pool.
  Phase 2's parallel vision/music/meme/reminiscence channels reduced external
  weighting; 8 marginal extra candidates do not improve top-1 selection,
  but push Phase-1 prompts above 2k tokens. Lowering to
  12 retains diversity while roughly halving per-call tokens."""

PROACTIVE_EXTERNAL_PER_ITEM_MAX_TOKENS = 200
"""Per-item token cap for Phase-2 external content (news/video/social/meme, etc.).
- Purpose: build_phase2_external_section truncates each web
  content item before assembling system prompts.
- Upstream: external API title +source +url +summary.
- Rationale: 200 tokens suffice to identify an item; Phase-2 LLMs
  summarize details themselves."""

PROACTIVE_EXTERNAL_TOTAL_MAX_TOKENS = 1500
"""Total assembled Phase-1 external-candidate token cap (Phase 2 sees only top-1).
- Purpose: truncate serialized selected web items once more by total size.
- Upstream: capped external_section text.
- Rationale: lower alongside PROACTIVE_PHASE1_TOTAL_TOPICS. Original 2000 was
  a hard ceiling for 20 ×200-token candidates. With 12 candidates, 1500 leaves
  ~250 spare tokens and still truncates excess. Phase-2 generation prompts
  actually include only Phase 1's selected web_topic (~50-100 tokens) in
  external_section; this field constrains Phase-1 prompt size."""

PROACTIVE_PHASE2_OUTPUT_MAX_TOKENS = 300
"""Phase-2 streaming-output abort fence.
- Purpose: abort generation above this value, preventing runaway essays.
- Upstream: LLM output, not input."""

PROACTIVE_PHASE2_GENERATE_MAX_TOKENS = int(PROACTIVE_PHASE2_OUTPUT_MAX_TOKENS * 1.5)
"""SDK max_completion_tokens for primary Phase-2 streaming generation.
- Purpose: _make_llm default used by the main Phase-2 stream call.
- Rationale: application streaming in [main_routers/system_router.py] hard-aborts on
  `count_tokens(full_text + chunk) > PROACTIVE_PHASE2_OUTPUT_MAX_TOKENS`,
  so larger SDK limits cannot be used. Set abort fence ×1.5, reserving 50%
  bandwidth for token-count errors and prompt-cache flush boundaries."""

PROACTIVE_PHASE1_UNIFIED_MAX_TOKENS = 1024
"""max_completion_tokens for Phase-1 unified selection LLMs.
- Purpose: _llm_call_with_retry default used by unified Phase-1 prompts
  combining web filtering, music keywords, and meme keywords in one call.
- Upstream: LLM JSON output (topic-ID lists plus brief reasons)."""

PROACTIVE_CHAT_HISTORY_MAX = 10
"""_proactive_chat_history deque maxlen.
- Purpose: recent proactive-chat records per character for 1h deduplication.
- Upstream: proactive chat events."""
