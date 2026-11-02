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

"""Stage-1/Stage-2 signal extraction (RFC §3.4.3 / §3.4.5).

Path A (user messages → facts + evidence signals) and path B (AI-aware
window, Stage-1 only). Owns the in-memory per-character cursor/failure
state (``_signal_check_state``) co-located with the loop that consumes it,
plus the evidence-signal dispatch helper and the negative-keyword hook.
"""

import asyncio
from datetime import datetime, timedelta

from config import (
    EVIDENCE_AI_AWARE_EVERY_N_A_TICKS,
    EVIDENCE_NEGATIVE_TARGET_MODEL_TIER,
    EVIDENCE_SIGNAL_CHECK_EVERY_N_TURNS,
    EVIDENCE_SIGNAL_CHECK_IDLE_MINUTES,
    EVIDENCE_SIGNAL_CHECK_INTERVAL_SECONDS,
    MAX_AI_AWARE_WINDOW_MSGS,
    MAX_KNOWN_POOL_FACTS,
    USER_FACT_NEGATE_DELTA,
    USER_FACT_REINFORCE_DELTA,
    USER_KEYWORD_REBUT_DELTA,
)
from config.prompts.prompts_directives import (
    get_negative_target_check_prompt,
    scan_negative_keywords,
)
from memory.event_log import (
    EVIDENCE_SOURCE_USER_FACT,
    EVIDENCE_SOURCE_USER_KEYWORD_REBUT,
)
from memory.facts import FactExtractionFailed

from . import gates, runtime
from ._shared import logger
from .gates import _INITIAL_DELAY_SIGNAL
from .rows import (
    _coerce_db_ts,
    _extract_role_tagged_messages_from_rows,
    _extract_user_messages_from_rows,
    _trim_to_user_msg_bracket,
)


# ── memory-evidence-rfc §3.4.3: background signal extraction loop ───

_signal_check_state: dict[str, dict] = {}
"""Per-character signal extraction state.

Schema:
  {
    'turns_since': int,           # turn counter since last successful check
    'last_check_ts': str | None,  # ISO cursor for path A window start
    'last_a_msg_ts': datetime,    # latest message timestamp actually processed by path A (path B's upstream boundary)
    'last_b_check_ts': datetime,  # ISO cursor for path B window start
    'b_tick_counter': int,        # ticks since last path B trigger
    # Liveness counters (in-memory only): cursor key -> consecutive failure count.
    # Successful mark_done clears the corresponding path's counter. Resetting on restart is an intentional soft fallback:
    # retry MEMORY_LIVENESS_MAX_ATTEMPTS times after restart before dead-lettering, preventing memory
    # counters from permanently abandoning transient failures.
    'a_extract_failures': dict[str, int],  # path A cursor (last_check_ts) → fail count
    'b_extract_failures': dict[str, int],  # path B cursor (last_b_check_ts) → fail count
  }
"""


def _signal_check_should_run(name: str, now: datetime) -> bool:
    state = _signal_check_state.setdefault(name, {'turns_since': 0, 'last_check_ts': None})
    if state['turns_since'] >= EVIDENCE_SIGNAL_CHECK_EVERY_N_TURNS:
        return True
    last = state.get('last_check_ts')
    if last is None:
        # Never checked: use the idle branch, which requires idleness.
        return gates._is_idle() and state['turns_since'] > 0
    try:
        last_dt = datetime.fromisoformat(last)
    except (ValueError, TypeError):
        return True
    if (now - last_dt).total_seconds() >= EVIDENCE_SIGNAL_CHECK_IDLE_MINUTES * 60:
        return state['turns_since'] > 0
    return False


def _signal_check_record_turn(
    name: str,
    *,
    language: str | None = None,
    locale_order: int | None = None,
) -> None:
    state = _signal_check_state.setdefault(name, {'turns_since': 0, 'last_check_ts': None})
    state['turns_since'] = int(state.get('turns_since', 0) or 0) + 1
    _signal_check_persist_locale(
        name,
        language=language,
        locale_order=locale_order,
    )


def _signal_check_persist_locale(
    name: str,
    *,
    language: str | None = None,
    locale_order: int | None = None,
) -> str | None:
    from utils.language_utils import is_supported_language_code

    if not is_supported_language_code(language):
        return None

    from .locale_state import record_character_prompt_locale

    return record_character_prompt_locale(
        name,
        language,
        order=locale_order,
    )


async def _run_signal_check_with_character_locale(name: str, operation):
    from .locale_state import run_with_character_prompt_locale

    return await run_with_character_prompt_locale(name, operation, name)


def _signal_check_mark_done(
    name: str,
    now: datetime,
    *,
    processed_turns: int | None = None,
) -> None:
    state = _signal_check_state.setdefault(name, {'turns_since': 0, 'last_check_ts': None})
    current_turns = int(state.get('turns_since', 0) or 0)
    state['turns_since'] = (
        0
        if processed_turns is None
        else max(0, current_turns - processed_turns)
    )
    state['last_check_ts'] = now.isoformat()
    # Cursor advanced: path A's old cursor key will never be reused; clear its counter
    # to prevent unbounded dictionary growth. Also reset windows that failed before new data
    # let them advance, so the next toxic window starts with a fresh attempt count.
    state['a_extract_failures'] = {}


def _stage1_path_a_bump_failure(
    name: str,
    state: dict,
    cursor_key: str,
    now: datetime,
    *,
    processed_turns: int | None = None,
) -> bool:
    """Liveness fallback for Path A Stage-1 LLM terminal failures.

    Bumps the failure counter for the (cursor_key) current window; when it reaches
    ``MEMORY_LIVENESS_MAX_ATTEMPTS``, force-pushes the cursor to now (counts as
    abandoning fact extraction for that window) and returns True; below the limit
    it returns False (the caller takes the original "keep the cursor, retry next
    round" path).

    Why: a poison msg (safety filter / content policy / output that can never be
    parsed) permanently exhausts ``_allm_call_with_retries``; the original code
    caught it and returned without moving the cursor → next round re-reads the
    same window → that character's fact pipeline is stuck forever (the liveness
    gap behind the PR #1399 "26 days, 0 facts" incident). Force-pushing the
    cursor means giving up fact extraction for that window, with a cost ceiling
    of N × interval (≈ 3 minutes) — far better than "0 facts forever".
    """
    from config import MEMORY_LIVENESS_MAX_ATTEMPTS
    fails = state.setdefault('a_extract_failures', {})
    fails[cursor_key] = int(fails.get(cursor_key, 0) or 0) + 1
    if fails[cursor_key] < MEMORY_LIVENESS_MAX_ATTEMPTS:
        return False
    logger.warning(
        f"[SignalLoop] {name}: Stage-1 path A 在 cursor {cursor_key!r} "
        f"累计失败 {fails[cursor_key]} 次 ≥ {MEMORY_LIVENESS_MAX_ATTEMPTS}，"
        f"强推 cursor 到 {now.isoformat(timespec='seconds')} "
        f"放弃该窗口（dead-letter）。Why: 毒窗口 liveness 兜底。"
    )
    _signal_check_mark_done(
        name,
        now,
        processed_turns=processed_turns,
    )  # Also clears a_extract_failures.
    return True


def _stage1_path_b_bump_failure(
    name: str, state: dict, cursor_key: str, force_to: datetime,
) -> bool:
    """Liveness fallback for Path B Stage-1 LLM terminal failures (the dual of path A's).

    Bumps the failure counter for the (cursor_key) current B window; when it
    reaches ``MEMORY_LIVENESS_MAX_ATTEMPTS``, force-pushes
    ``state['last_b_check_ts']`` to ``force_to`` (= last_fetched_ts) and returns
    True; below the limit returns False (the caller takes the original "keep the
    cursor, retry at the next trigger" path).

    Why: same root problem as path A — B's ``persisted is None`` branch originally
    returned without moving ``last_b_check_ts`` → the next B trigger re-reads the
    same [last_b_check_ts, last_a_msg_ts] window → still stuck. Force-pushing the
    cursor to last_fetched_ts means giving up that window from the AI-aware
    perspective, with a cost ceiling of N × the B trigger interval.
    """
    from config import MEMORY_LIVENESS_MAX_ATTEMPTS
    fails = state.setdefault('b_extract_failures', {})
    fails[cursor_key] = int(fails.get(cursor_key, 0) or 0) + 1
    if fails[cursor_key] < MEMORY_LIVENESS_MAX_ATTEMPTS:
        return False
    logger.warning(
        f"[PathB] {name}: Stage-1 path B 在 cursor {cursor_key!r} "
        f"累计失败 {fails[cursor_key]} 次 ≥ {MEMORY_LIVENESS_MAX_ATTEMPTS}，"
        f"强推 last_b_check_ts 到 {force_to.isoformat(timespec='seconds')} "
        f"放弃该窗口（dead-letter）。Why: 毒窗口 liveness 兜底。"
    )
    state['last_b_check_ts'] = force_to
    state['b_extract_failures'] = {}
    return True


def _signal_check_window_start(name: str, now: datetime) -> datetime:
    """Compute the start of the SQL window for the signal-extraction cycle.

    Use the previous successful `last_check_ts` when available so long
    active sessions do not silently drop messages older than the fallback
    window. Cold-start (first run or after corrupt state) falls back to
    `now - EVIDENCE_SIGNAL_CHECK_IDLE_MINUTES * 2` — wider than a single
    idle trigger window but bounded so the initial scan is not unbounded.
    """
    state = _signal_check_state.get(name, {})
    last = state.get('last_check_ts')
    if last:
        try:
            ts = datetime.fromisoformat(last)
            # Clock-skew safety: never let cursor land in the future
            if ts <= now:
                return ts
        except (ValueError, TypeError) as e:
            # Corrupt cursor value in in-memory state (shouldn't happen —
            # we always write ISO-8601 — but stay defensive so one bad
            # character doesn't stall the signal loop). Fall through to
            # the bounded fallback window below.
            logger.debug(
                f"[SignalLoop] {name}: last_check_ts {last!r} 解析失败 ({e}), 用 fallback 窗口"
            )
    return now - timedelta(minutes=EVIDENCE_SIGNAL_CHECK_IDLE_MINUTES * 2)


async def _adispatch_evidence_signals(
    lanlan_name: str, signals: list[dict], source: str,
) -> bool:
    """Apply each signal through ReflectionEngine / PersonaManager aapply_signal.

    Delta mapping (§3.4.1 v1.2.1 weight scheme):
      source='user_fact' + reinforces → USER_FACT_REINFORCE_DELTA (indirect,
        silver; combo bonus handled inside compute_evidence_snapshot)
      source='user_fact' + negates    → USER_FACT_NEGATE_DELTA
      source='user_keyword_rebut'     → USER_KEYWORD_REBUT_DELTA (always negates)

    Defensive: unknown target_type / missing manager refs are skipped.

    Returns True if ALL signals applied successfully; False if any raised
    (`aapply_signal` raises for critical IO / event-log errors, but returns
    False silently for unknown target_id). Caller can use the return value
    to decide whether to advance its cursor (CodeRabbit PR #929 major).
    """
    all_ok = True
    for s in signals:
        if not isinstance(s, dict):
            continue
        signal_kind = s.get('signal')
        if signal_kind == 'reinforces':
            # Indirect inference (Stage-2) gets half weight; combo logic in
            # `compute_evidence_snapshot` re-inflates it past the threshold.
            delta = {'reinforcement': USER_FACT_REINFORCE_DELTA}
        elif signal_kind == 'negates':
            # keyword_rebut uses a different constant from fact-derived negates
            # only in name — both currently 1.0. Pick by source for clarity.
            if source == EVIDENCE_SOURCE_USER_KEYWORD_REBUT:
                delta = {'disputation': USER_KEYWORD_REBUT_DELTA}
            else:
                delta = {'disputation': USER_FACT_NEGATE_DELTA}
        else:
            continue

        target_type = s.get('target_type')
        target_id = s.get('target_id')
        if not target_id:
            continue

        try:
            if target_type == 'reflection':
                await runtime.reflection_engine.aapply_signal(
                    lanlan_name, target_id, delta, source=source,
                )
            elif target_type == 'persona':
                entity_key = s.get('entity_key')
                if not entity_key:
                    logger.warning(
                        f"[Signal] {lanlan_name}: persona signal 缺 entity_key，丢弃"
                    )
                    continue
                await runtime.persona_manager.aapply_signal(
                    lanlan_name, entity_key, target_id, delta, source=source,
                )
            else:
                logger.warning(f"[Signal] {lanlan_name}: 未知 target_type={target_type}")
        except Exception as e:
            # Critical failure (event_log fsync / atomic_write_json fail,
            # etc.) — flag so caller can preserve the cursor; subsequent
            # signals in this batch still attempted (best-effort).
            all_ok = False
            logger.warning(
                f"[Signal] {lanlan_name}: aapply_signal 失败 ({target_type}/{target_id}): {e}"
            )
    return all_ok


async def _run_path_b(name: str, state: dict) -> None:
    """Path B: AI-aware Stage-1 only (does not enter the Stage-2 evidence loop).

    Piggybacks on the path A loop, triggered once every
    ``EVIDENCE_AI_AWARE_EVERY_N_A_TICKS`` A ticks. The window's downstream boundary
    is the latest msg ts path A actually processed, guaranteeing every message B
    sees was strictly seen by A — avoiding the race where "a msg inserted into
    SQLite right after A's scan SQL finished gets grabbed by B first".

    Design points:
      1. Window = [last_b_check_ts, last_a_msg_ts]. Cold-start last_b is derived =
         last_a_msg_ts - max(N_TICKS, N_TURNS) × IDLE_MINUTES (the more
         conservative of the two A trigger cadences, covering sparse-turn cases)
      2. SQL-level LIMIT MAX_AI_AWARE_WINDOW_MSGS guards against extreme long
         windows blowing up the prompt
      3. Known-fact pool: pull facts with created_at ≥ last_b from facts.json (no
         upper bound — A's idle delay makes the latest batch of A facts'
         created_at slightly later than last_a_msg_ts; an upper bound would drop
         that whole batch), take the top MAX_KNOWN_POOL_FACTS by importance DESC
         into the prompt, so the LLM's output layer actively dedups content path A
         already extracted
      4. Persisted with default_source='ai_disclosure'; an explicit LLM source
         field takes precedence
         Note: messages fed to Stage-1 are first trimmed to the user-msg bracket
         (first user msg through last user msg, inclusive) — product thesis,
         guarding against cheap-layer pollution; leading/trailing AI fragments
         shouldn't settle as facts via path B
      5. Cursor advancement rules:
         - SQL returns 0 rows → push to last_a_msg_ts (window genuinely empty)
         - SQL returns N rows but all system/empty msgs → push to last fetched
           row ts (the unfetched tail may have content)
         - Stage-1 LLM terminal failure (aextract_facts_with_known_pool returns
           None) → cursor stays put; the next trigger retries the same window
           (fact dedup prevents double writes)
         - all other normal paths → push to last fetched row ts (< last_a_msg_ts
           when truncated)

    Differences from path A:
    - does not enter the Stage-2 evidence loop (_apersist_new_facts writes
      signal_processed=True + the source filter inside
      aextract_facts_and_detect_signals as double defense)
    - Stage-1 failures are swallowed, not raised (path A's own
      FactExtractionFailed has an independent retry path; B is supplementary and
      shouldn't block), but the cursor must be preserved — a failed window is
      retried at the next trigger, never collapsed into a silent
      "succeeded with 0 extractions" skip
    """
    last_a_msg_ts = state.get('last_a_msg_ts')
    if last_a_msg_ts is None:
        # A has never successfully processed a batch; B has no source to inspect.
        return
    # Defensive TZ normalization: _coerce_db_ts already stores naive timestamps in state,
    # protecting the main path, but externally injected state / pre-upgrade aware values can leak
    # through. All cursor and known_pool created_at comparisons below expect naive values;
    # strip timezone information again so _run_path_b is a self-contained naive-only domain (Codex P2,
    # round 8 on PR #1408, both-side cases).
    if last_a_msg_ts.tzinfo is not None:
        last_a_msg_ts = last_a_msg_ts.replace(tzinfo=None)
        state['last_a_msg_ts'] = last_a_msg_ts

    last_b = state.get('last_b_check_ts')
    if last_b is not None and last_b.tzinfo is not None:
        last_b = last_b.replace(tzinfo=None)
        state['last_b_check_ts'] = last_b
    if last_b is None:
        # Cold-start lookback: last_b is unset on B's first trigger, so estimate a start.
        # A ticks need not follow the IDLE gate cadence: the turn-count gate
        # (accumulated EVIDENCE_SIGNAL_CHECK_EVERY_N_TURNS) can also trigger them, and sparse turns
        # (intermittent user speech, turn intervals >> IDLE_MIN) may separate ticks
        # by much more than IDLE_MIN. Estimating only N_TICKS x IDLE_MIN could place the cold-start
        # boundary inside A's already-processed range, making B permanently skip earlier content.
        # AI-only msg（Codex P2 round-6 on PR #1408）。
        # Fix: use max(piggyback cadence, turn-count cadence) x IDLE_MIN as the estimate's
        # upper bound: max(3, 10) x 10min = 100min by default. LIMIT bounds prompts;
        # Stage-1 dedup hashes prevent duplicate writes, so overshooting is safe.
        cold_start_ticks_estimate = max(
            EVIDENCE_AI_AWARE_EVERY_N_A_TICKS,
            EVIDENCE_SIGNAL_CHECK_EVERY_N_TURNS,
        )
        estimated_a_coverage = timedelta(
            minutes=cold_start_ticks_estimate * EVIDENCE_SIGNAL_CHECK_IDLE_MINUTES
        )
        last_b = last_a_msg_ts - estimated_a_coverage

    if last_b >= last_a_msg_ts:
        # Empty window: B has caught up with A.
        return

    try:
        rows = await runtime.time_manager.aretrieve_original_by_timeframe(
            name, last_b, last_a_msg_ts,
            limit_rows=MAX_AI_AWARE_WINDOW_MSGS,
        )
    except Exception as e:
        logger.warning(f"[PathB] {name}: 读取窗口失败: {e}")
        return
    if not rows:
        # aretrieve_original_by_timeframe swallows SQL errors / engine initialization failures /
        # maintenance conditions and returns [] (see timeindex.py), so callers cannot distinguish
        # a genuinely empty window from a transient read failure. Conservatively keep the cursor:
        # - Truly empty: A just processed this range successfully; B should almost never find it empty
        #   unless SQL is faulty (A saw rows that B cannot read in the same range).
        #   Querying zero rows again on B's next trigger costs only constant time;
        #   SQLite scans of empty ranges are very fast.
        # - Transient failure: retain the cursor so the next trigger retries this window instead of
        #   permanently skipping [last_b, last_a_msg_ts] (Codex P1, round 5 on PR #1408).
        logger.debug(
            f"[PathB] {name}: 窗口 {last_b.isoformat(timespec='seconds')} → "
            f"{last_a_msg_ts.isoformat(timespec='seconds')} 取回 0 rows "
            f"(可能 SQL transient 失败 swallow 成 []), 保留 cursor 下次 trigger 复查"
        )
        return

    # Parse the last row timestamp actually fetched by SQL; use it for all later cursor advances,
    # never last_a_msg_ts. The distinction matters when MAX_AI_AWARE_WINDOW_MSGS LIMIT
    # truncates the window: last_fetched_ts < last_a_msg_ts, leaving the unfetched tail
    # for the next B trigger. Advancing to last_a_msg_ts would skip that tail permanently.
    # （Codex P1 round-1 on PR #1408, P2 round-2 covers filtered-empty case）。
    last_fetched_ts = _coerce_db_ts(rows[-1][0])
    if last_fetched_ts is None:
        # Defensive fallback: if _coerce_db_ts fails, use last_a_msg_ts to avoid a stuck-cursor
        # loop. The normal path never needs this: rows[-1][0] is SQLite's timestamp string.
        last_fetched_ts = last_a_msg_ts

    # Guard against LIMIT loops within tied timestamps (Codex P2, round 3 on PR #1408):
    # aretrieve_original_by_timeframe uses inclusive BETWEEN. If more than
    # MAX_AI_AWARE_WINDOW_MSGS rows share a timestamp (e.g. bulk import or
    # store_conversation assigning one timestamp to all rows in a request), LIMIT returns
    # the earliest N rows with that timestamp. Advancing to last_fetched_ts then rereads
    # the same rows forever, permanently skipping rows after the cluster.
    # Detect LIMIT reached AND all fetched timestamps equal; advance cursor by 1 microsecond.
    # Cost: skip the cluster's tail beyond LIMIT (rare: a normal conversation
    # turn writes 2-5 rows, far below MAX_AI_AWARE_WINDOW_MSGS=200). A better fix requires
    # a composite (ts, rowid) cursor and SQL changes, too costly for this case.
    first_fetched_ts = _coerce_db_ts(rows[0][0])
    if (
        len(rows) >= MAX_AI_AWARE_WINDOW_MSGS
        and first_fetched_ts is not None
        and last_fetched_ts == first_fetched_ts
    ):
        logger.warning(
            f"[PathB] {name}: 同 ts 簇 {first_fetched_ts.isoformat(timespec='microseconds')} "
            f"行数 ≥ LIMIT ({MAX_AI_AWARE_WINDOW_MSGS})，cursor +1μs 越过避免死循环；"
            f"该 ts 簇 LIMIT 之后的 tail row 会被 skip"
        )
        last_fetched_ts = last_fetched_ts + timedelta(microseconds=1)

    message_dicts = _extract_role_tagged_messages_from_rows(rows)
    if not message_dicts:
        # Only system messages / empty content: advance to last fetched, not last_a_msg_ts;
        # a truncated tail may still contain valid messages.
        state['last_b_check_ts'] = last_fetched_ts
        state['b_extract_failures'] = {}
        return

    # Restrict to the user-message bracket, from first through last user message inclusive.
    # Product thesis: avoid low-value-layer pollution. Leading/trailing AI fragments (unconfirmed
    # guesses / monologues the user never answered) must not become facts.
    message_dicts = _trim_to_user_msg_bracket(message_dicts)
    if not message_dicts:
        # No user messages: deliberately skip the AI-only low-value layer. Advance the cursor
        # normally so B's next trigger does not revisit it.
        logger.debug(
            f"[PathB] {name}: 窗口 {last_b.isoformat(timespec='seconds')} → "
            f"{last_fetched_ts.isoformat(timespec='seconds')} 无 user msg bracket "
            f"(纯 AI-only 内容，product thesis 跳过)"
        )
        state['last_b_check_ts'] = last_fetched_ts
        state['b_extract_failures'] = {}
        return

    from utils.llm_client import convert_to_messages
    messages = convert_to_messages(message_dicts)

    # Known-fact pool: use facts written by path A during / after this B window as do-not-repeat hints.
    # Apply only ``created_at >= last_b``, with no upper bound (CodeRabbit on PR #1408):
    # A's idle/polling delay means facts for the just-scanned B window commonly have created_at
    # slightly later than last_a_msg_ts. An upper bound would exclude the newest entire
    # A batch, making known_pool ineffective for content A just extracted, and B more
    # likely to duplicate it. Including some later A facts is safe: known_pool
    # only guides the LLM; extra entries may suppress a few new facts at worst, while Stage-1
    # dedup hashes remain a safeguard. Take MAX_KNOWN_POOL_FACTS ordered by descending importance.
    try:
        all_facts = await runtime.fact_store.aload_facts(name)
    except Exception as e:
        logger.debug(f"[PathB] {name}: aload_facts 失败，known pool 留空: {e}")
        all_facts = []

    # Use safe_importance: legacy / manually edited facts.json can contain
    # dirty importance values like "high", None, or lists. Raw int(...) would raise
    # ValueError on every B trigger, permanently disabling path B for that
    # character (Codex P2, round 1 on PR #1408).
    from memory.facts import safe_importance
    from memory.scopes import is_legacy_private_entry

    known_pool: list[dict] = []
    for f in all_facts:
        if not isinstance(f, dict):
            continue
        # Path B extracts AI-aware facts for private chats; the known pool must contain only legacy
        # private-chat facts. Scoped group/member facts would render group content in the private Stage-1
        # prompt (cross-boundary leakage), and busy groups could fill top-N slots, suppressing
        # private-chat fact extraction.
        if not is_legacy_private_entry(f):
            continue
        created_at_raw = f.get('created_at') or ''
        try:
            # Parse full ISO timestamps including microseconds: created_at is written with datetime.now().isoformat().
            # Truncating to [:19] loses microseconds and incorrectly excludes facts whose
            # created_at == last_b + 0.x seconds from the >= last_b window (CodeRabbit on PR #1408).
            created_at = datetime.fromisoformat(created_at_raw)
        except (ValueError, TypeError):
            continue
        # Defensive handling: _apersist_new_facts writes naive datetime.now().isoformat(),
        # but import/migration paths may write timezone-aware values
        # (e.g. "...+00:00"). Comparing to naive last_b raises TypeError, causing
        # _run_path_b to fail forever for that character (Codex P1, round 7
        # on PR #1408). Treat aware values as naive for comparison: in most cases
        # they represent the same wall-clock time; timezone differences must not disable fact extraction.
        if created_at.tzinfo is not None:
            created_at = created_at.replace(tzinfo=None)
        if created_at >= last_b:
            known_pool.append(f)
    known_pool.sort(key=lambda f: -safe_importance(f))
    known_pool = known_pool[:MAX_KNOWN_POOL_FACTS]

    persisted = await runtime.fact_store.aextract_facts_with_known_pool(
        name, messages, known_pool,
    )
    if persisted is None:
        # Stage-1 LLM terminal failure (retries exhausted): retain cursor and retry the window on
        # B's next trigger (fact dedup hashes prevent duplicate writes). Distinguishing None from [] is crucial:
        # treating failure as successful zero extraction would permanently skip the window (CodeRabbit / Codex
        # P1 round-2 on PR #1408）。
        #
        # Liveness fallback, symmetric with path A: repeated failures at last_b_check_ts reaching
        # MEMORY_LIVENESS_MAX_ATTEMPTS force cursor to last_fetched_ts,
        # preventing a toxic window from permanently stalling that character's AI-aware extraction.
        cursor_key = (
            last_b.isoformat(timespec='microseconds') if last_b else 'cold'
        )
        if not _stage1_path_b_bump_failure(name, state, cursor_key, last_fetched_ts):
            logger.warning(
                f"[PathB] {name}: Stage-1 终态失败，保留 cursor 下次 trigger 重试 "
                f"(window={last_b.isoformat(timespec='seconds')} → "
                f"{last_fetched_ts.isoformat(timespec='seconds')})"
            )
        return
    if persisted:
        logger.info(
            f"[PathB] {name}: AI-aware Stage-1 抽出 {len(persisted)} 条新 fact "
            f"(window={last_b.isoformat(timespec='seconds')} → "
            f"{last_fetched_ts.isoformat(timespec='seconds')}, "
            f"known_pool={len(known_pool)})"
        )

    state['last_b_check_ts'] = last_fetched_ts
    # Cursor advanced: clear the path-B counter, since its old key will never be used again,
    # preventing unbounded history growth (symmetric with _signal_check_mark_done
    # clearing a_extract_failures on path A success).
    state['b_extract_failures'] = {}


async def _periodic_signal_extraction_loop():
    """Polls every EVIDENCE_SIGNAL_CHECK_INTERVAL_SECONDS; when the trigger condition is
    met, runs Stage-1 + Stage-2 + signal dispatch for each catgirl (RFC §3.4.3).

    First run delayed by _INITIAL_DELAY_SIGNAL seconds (staggered against the other background loops).
    """
    await asyncio.sleep(_INITIAL_DELAY_SIGNAL)
    while True:
        # Strong memory disabled: stop Stage-1 + Stage-2 evidence extraction entirely. These are
        # evidence-RFC's largest token costs (polling every 40s, with two LLM calls
        # on trigger and thinking enabled in Stage-2). evidence_score then stops changing;
        # confirmed/promoted transitions use the time-driven fallback.
        #
        # While disabled, advance last_check_ts to now (same rationale as rebuttal), avoiding
        # loading all disabled-period user messages as backlog into Stage-1 + Stage-2 on re-enable.
        if not await gates._ais_powerful_memory_enabled():
            try:
                character_data = await runtime._config_manager.aload_characters()
                catgirl_names = list(character_data.get('猫娘', {}).keys())
                cursor_now = datetime.now()
                for name in catgirl_names:
                    try:
                        _signal_check_mark_done(name, cursor_now)
                    except Exception as cursor_e:
                        # A single character's last_check_ts update failure is nonfatal: retry next round,
                        # for the same reasons as the rebuttal path.
                        logger.debug(
                            f"[SignalLoop] {name}: 关态 cursor 推进失败: {cursor_e}"
                        )
            except Exception as e:
                logger.debug(f"[SignalLoop] 关态 cursor 推进 batch 失败: {e}")
            await asyncio.sleep(EVIDENCE_SIGNAL_CHECK_INTERVAL_SECONDS)
            continue

        try:
            character_data = await runtime._config_manager.aload_characters()
            catgirl_names = list(character_data.get('猫娘', {}).keys())
        except Exception as e:
            logger.debug(f"[SignalLoop] 加载角色列表失败: {e}")
            await asyncio.sleep(EVIDENCE_SIGNAL_CHECK_INTERVAL_SECONDS)
            continue

        now = datetime.now()
        # Snapshot counters in the same event-loop turn as the SQL window end.
        # A user turn recorded after this point falls outside ``now`` and must
        # remain pending when the awaited extraction pass eventually completes.
        processed_turns_by_name = {
            name: int(
                _signal_check_state.get(name, {}).get('turns_since', 0) or 0
            )
            for name in catgirl_names
        }

        async def _signal_check_one(name: str):
            return await _run_signal_check_with_character_locale(
                name,
                _signal_check_one_with_locale,
            )

        async def _signal_check_one_with_locale(name: str):
            """Stage-1 + Stage-2 + signal dispatch for a single character. Characters are
            mutually independent (per-char event_log lock / files); the outer gather runs
            them in parallel. A failure doesn't block other characters, and the cursor
            only advances on the fully successful path."""
            try:
                if not _signal_check_should_run(name, now):
                    return
                processed_turns = processed_turns_by_name.get(name, 0)
                # Window start: prefer the last successful check timestamp (cursor semantics), avoiding
                # permanent skips of messages older than 10 minutes in long chats (section 3.4.3 cursor advancement).
                # Fall back to IDLE_MINUTES*2 on cold start / missing cursor.
                start_time = _signal_check_window_start(name, now)
                rows = await runtime.time_manager.aretrieve_original_by_timeframe(
                    name, start_time, now,
                )
                if not rows:
                    _signal_check_mark_done(
                        name, now, processed_turns=processed_turns,
                    )
                    return
                user_msgs_text = _extract_user_messages_from_rows(rows)
                if not user_msgs_text:
                    # No user messages: purely proactive / AI monologue / tool
                    # turns. Deliberately exclude these from memory:
                    # 1. Path A needs user speech as the source for user_observation facts.
                    # 2. Path B collects AI self-disclosure only in windows with user engagement
                    #    too (B piggybacks A rather than running independently).
                    # Design principle: user disengagement means low-value content (the "90% carefree"
                    # product thesis), which must not automatically become facts and pollute memory.
                    # Advance cursor and reset counters normally so the next window containing user messages
                    # enters the normal A+B flow directly.
                    _signal_check_mark_done(
                        name, now, processed_turns=processed_turns,
                    )
                    return

                # Build BaseMessage-like structures for extract_facts.
                from utils.llm_client import convert_to_messages
                message_dicts = [
                    {'type': 'human', 'data': {'content': m}}
                    for m in user_msgs_text
                ]
                # convert_to_messages accepts only lists, no JSON decoding (contract since PR #547).
                # The previous json.dumps hit isinstance(data, list)==False and returned [] directly,
                # giving messages=[] and an empty _format_conversation output. The Stage-1 prompt
                # then had no text between ======以下为对话====== and ======以上为对话======, so the LLM correctly
                # returned [], silently skipping all fact extraction and subsequent Stage-2 evidence work.
                messages = convert_to_messages(message_dicts)

                try:
                    persisted, signals, batch_fact_ids = await runtime.fact_store.aextract_facts_and_detect_signals(
                        name, messages,
                        reflection_engine=runtime.reflection_engine,
                        persona_manager=runtime.persona_manager,
                    )
                except FactExtractionFailed as e:
                    # Stage-1 terminal failure — cursor NOT advanced, next
                    # cycle retries the same message window (§3.4.3)。
                    # Liveness fallback: repeated failures for one window reaching MEMORY_LIVENESS
                    # _MAX_ATTEMPTS force cursor to now, preventing a toxic window from
                    # permanently stalling the fact pipeline.
                    state = _signal_check_state.setdefault(
                        name, {'turns_since': 0, 'last_check_ts': None},
                    )
                    # CodeRabbit: use start_time as the key, not the literal 'cold'.
                    # A literal 'cold' combines failures across all cold-start rounds in one bucket;
                    # its Nth failure advances cursor to the current now, dead-lettering
                    # valid messages arriving during that time too. Use start_time, the current
                    # window's start: with a stable cursor, start_time == cursor
                    # (_signal_check_window_start returns cursor directly); on cold start,
                    # start_time is now - IDLE_MINUTES*2 and changes every round,
                    # so unrelated cold-start failures cannot be incorrectly combined for dead-lettering.
                    cursor_key = start_time.isoformat(timespec='microseconds')
                    if not _stage1_path_a_bump_failure(
                        name,
                        state,
                        cursor_key,
                        now,
                        processed_turns=processed_turns,
                    ):
                        logger.warning(
                            f"[SignalLoop] {name}: Stage-1 失败保留 cursor 重试: {e}"
                        )
                    return

                # Dispatch before mark_done: any intermediate aapply failure keeps
                # cursor unchanged so next Stage-1 retries the same window; Stage-1
                # dedup prevents duplicate facts and Stage-2 regenerates signals
                # for retry. CodeRabbit PR #929 fix: dispatch previously swallowed errors
                # while mark_done still ran, permanently losing evidence after one aapply failure.
                dispatch_ok = True
                if signals:
                    dispatch_ok = await _adispatch_evidence_signals(
                        name, signals, source=EVIDENCE_SOURCE_USER_FACT,
                    )
                    logger.info(
                        f"[SignalLoop] {name}: dispatch {len(signals)} 个 evidence 信号"
                    )

                # Drain checkpoint: mark processed only after every dispatch succeeds, including signals=[]
                # when the LLM saw no relation. Any aapply failure preserves
                # signal_processed=False for next idle retry, preventing permanently skipping
                # unpersisted signals (CodeRabbit fingerprint c755101c).
                if dispatch_ok and batch_fact_ids:
                    await runtime.fact_store.amark_signal_processed(name, batch_fact_ids)

                if not dispatch_ok:
                    logger.warning(
                        f"[SignalLoop] {name}: dispatch 有失败，保留 cursor 下轮重试"
                    )
                    return  # Retain cursor without calling _signal_check_mark_done.

                # After writing signals, trigger one score-driven pending->confirmed scan;
                # isolate try/except so its failure cannot prevent cursor advancement,
                # since scores naturally recompute next round.
                try:
                    await runtime.reflection_engine.aauto_promote_stale(name)
                except Exception as e:
                    logger.debug(f"[SignalLoop] {name}: auto_promote_stale 失败: {e}")

                # Stage-1 and dispatch both succeeded: advance cursor.
                _signal_check_mark_done(
                    name, now, processed_turns=processed_turns,
                )

                # Record A's latest actually processed message timestamp as path B's downstream boundary.
                # Rows are already ORDER BY ts ASC, making the final row the latest within the window.
                # Use actual message timestamps rather than wall-clock now so B sees only
                # messages A already inspected, avoiding B processing messages inserted into
                # SQLite after A's SQL scan finished.
                state = _signal_check_state.setdefault(
                    name, {'turns_since': 0, 'last_check_ts': None},
                )
                last_msg_ts = _coerce_db_ts(rows[-1][0])
                if last_msg_ts is not None:
                    state['last_a_msg_ts'] = last_msg_ts

                # Path B trigger: increment counter after successful A; at N invoke
                # _run_path_b, AI-aware Stage-1 only, documented in its docstring.
                state['b_tick_counter'] = state.get('b_tick_counter', 0) + 1
                if state['b_tick_counter'] >= EVIDENCE_AI_AWARE_EVERY_N_A_TICKS:
                    state['b_tick_counter'] = 0
                    try:
                        await _run_path_b(name, state)
                    except Exception as e:
                        # B failures must never affect A, already beyond mark_done;
                        # warn only. B retries when b_tick_counter next reaches N;
                        # last_b_check_ts advances its cursor only on success,
                        # so failures retry the same window next time.
                        logger.warning(
                            f"[PathB] {name}: AI-aware Stage-1 失败 (skip 本轮，下次 B trigger 重试): {e}"
                        )
            except Exception as e:
                logger.debug(f"[SignalLoop] {name}: 处理失败: {e}")

        if catgirl_names:
            await asyncio.gather(
                *(_signal_check_one(name) for name in catgirl_names),
                return_exceptions=True,
            )

        await asyncio.sleep(EVIDENCE_SIGNAL_CHECK_INTERVAL_SECONDS)


# ── memory-evidence-rfc §3.4.5: negative-keyword hook helpers ───────

async def _amaybe_trigger_negative_keyword_hook(
    lanlan_name: str, user_messages: list[str], lang: str,
) -> None:
    """If any user message hits NEGATIVE_KEYWORDS_I18N, fire the async LLM
    target-check and dispatch disputation signals. Non-blocking for the
    calling conversation path."""
    if not user_messages:
        return
    hit = any(scan_negative_keywords(m, lang) for m in user_messages)
    if not hit:
        return

    # Assemble observation pool (§3.4.5 prompt inputs)
    try:
        observations = await runtime.fact_store._aload_signal_targets(
            lanlan_name,
            reflection_engine=runtime.reflection_engine,
            persona_manager=runtime.persona_manager,
        )
    except Exception as e:
        logger.debug(f"[NegKW] {lanlan_name}: 观察集加载失败: {e}")
        return
    if not observations:
        return

    from config import (
        NEGATIVE_KEYWORD_CHECK_CONTEXT_ITEMS,
        EVIDENCE_PER_OBSERVATION_MAX_TOKENS,
        EVIDENCE_OBSERVATIONS_TOTAL_MAX_TOKENS,
    )
    from utils.tokenize import truncate_to_tokens
    user_msg_text = "\n".join(user_messages[-NEGATIVE_KEYWORD_CHECK_CONTEXT_ITEMS:])
    obs_text = "\n".join(
        f"[{o['id']}] {truncate_to_tokens(o.get('text', '') or '', EVIDENCE_PER_OBSERVATION_MAX_TOKENS)}"
        for o in observations
    )
    obs_text = truncate_to_tokens(obs_text, EVIDENCE_OBSERVATIONS_TOTAL_MAX_TOKENS)
    prompt = get_negative_target_check_prompt(lang) \
        .replace('{USER_MESSAGES}', user_msg_text) \
        .replace('{OBSERVATIONS}', obs_text)

    parsed = await runtime.fact_store._allm_call_with_retries(
        prompt, lanlan_name,
        tier=EVIDENCE_NEGATIVE_TARGET_MODEL_TIER,
        call_type="memory_negative_target_check",
        max_retries=2,
    )
    if parsed is None or not isinstance(parsed, dict):
        return
    targets = parsed.get('targets', [])
    if not isinstance(targets, list) or not targets:
        return

    # Validate + dispatch (same defensive filter as Stage-2)
    valid_ids = {o['id']: o for o in observations}
    signals: list[dict] = []
    for t in targets:
        if not isinstance(t, dict):
            continue
        tid = t.get('target_id')
        if not tid:
            continue
        # Accept raw or prefixed id
        full_id = tid if tid in valid_ids else next(
            (vid for vid in valid_ids if vid.endswith(f".{tid}")), None,
        )
        if full_id is None:
            logger.warning(f"[NegKW] {lanlan_name}: 未知 target_id={tid}, 丢弃")
            continue
        obs = valid_ids[full_id]
        signals.append({
            'signal': 'negates',
            'target_type': obs['target_type'],
            'target_id': obs['raw_id'],
            'entity_key': obs.get('entity_key'),
        })

    if signals:
        # Negative-keyword hook is inline with conversation turn — no cursor
        # to preserve on dispatch failure; best-effort fire-and-forget.
        await _adispatch_evidence_signals(
            lanlan_name, signals, source=EVIDENCE_SOURCE_USER_KEYWORD_REBUT,
        )
        logger.info(
            f"[NegKW] {lanlan_name}: 关键词触发 {len(signals)} 个 disputation 信号"
        )
