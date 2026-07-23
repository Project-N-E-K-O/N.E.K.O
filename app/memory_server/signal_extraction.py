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
    'last_a_msg_ts': datetime,    # path A 实际处理过的最晚 msg ts (path B 上游边界)
    'last_b_check_ts': datetime,  # ISO cursor for path B window start
    'b_tick_counter': int,        # ticks since last path B trigger
    # Liveness counters (in-memory only)：cursor key → 连续失败次数。
    # 成功 mark_done 时清空对应 path 的 counter。重启清零是有意为之的"软兜底"
    # ——重启后再试 MEMORY_LIVENESS_MAX_ATTEMPTS 次再 dead-letter，避免内存
    # counter 错误地把短暂 transient 失败永久放弃。
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
        # 未 check 过 → 走空闲分支（需要 idle）
        return gates._is_idle() and state['turns_since'] > 0
    try:
        last_dt = datetime.fromisoformat(last)
    except (ValueError, TypeError):
        return True
    if (now - last_dt).total_seconds() >= EVIDENCE_SIGNAL_CHECK_IDLE_MINUTES * 60:
        return state['turns_since'] > 0
    return False


def _signal_check_record_turn(name: str) -> None:
    state = _signal_check_state.setdefault(name, {'turns_since': 0, 'last_check_ts': None})
    state['turns_since'] = int(state.get('turns_since', 0) or 0) + 1


def _signal_check_mark_done(name: str, now: datetime) -> None:
    state = _signal_check_state.setdefault(name, {'turns_since': 0, 'last_check_ts': None})
    state['turns_since'] = 0
    state['last_check_ts'] = now.isoformat()
    # Cursor 推进 → path A 的旧 cursor key 永远不会再被命中，清空 counter
    # 避免内存 dict 随 cursor 历史无限增长。同时把"曾经失败但靠新数据冲过去
    # 了"的窗口归零，下次毒窗口出现按 fresh attempt 计算。
    state['a_extract_failures'] = {}


def _stage1_path_a_bump_failure(
    name: str, state: dict, cursor_key: str, now: datetime,
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
    _signal_check_mark_done(name, now)  # 会顺带把 a_extract_failures 清空
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
        # A 还没成功处理过任何 batch，B 无源可看
        return
    # 防御性 TZ normalize：`_coerce_db_ts` 已经在写入 state 时归一化成 naive
    # 是主路径保护，但外部 state injection / 升级前残留的 aware 值仍可能漏进
    # 来——下面所有 cursor 比较 + known_pool created_at 比较都按 naive 工作，
    # 这里再 strip 一遍把整个 _run_path_b 变成自包含 naive-only 域（Codex P2
    # round-8 on PR #1408 双侧 case）。
    if last_a_msg_ts.tzinfo is not None:
        last_a_msg_ts = last_a_msg_ts.replace(tzinfo=None)
        state['last_a_msg_ts'] = last_a_msg_ts

    last_b = state.get('last_b_check_ts')
    if last_b is not None and last_b.tzinfo is not None:
        last_b = last_b.replace(tzinfo=None)
        state['last_b_check_ts'] = last_b
    if last_b is None:
        # Cold start lookback：B 第一次 trigger 时 last_b 无值，需要估个起点。
        # A tick 不一定按 IDLE gate 节律走——也可能被 turn-count gate
        # (EVIDENCE_SIGNAL_CHECK_EVERY_N_TURNS 累积) 触发，或在 sparse turn
        # 场景（user 间歇性发声、turn 间隔 >> IDLE_MIN）下两 tick 之间跨度
        # 远超 IDLE_MIN。只按 piggyback 估算 (N_TICKS × IDLE_MIN) 会让 cold
        # start 起点落在 A 真正处理过的范围之内，B 永久 skip 那段之前的
        # AI-only msg（Codex P2 round-6 on PR #1408）。
        # 修法：取 max(piggyback 节律, turn-count 节律) × IDLE_MIN 当估算
        # 上限。默认下 max(3, 10) × 10min = 100min。LIMIT 兜底防爆 prompt，
        # Stage-1 dedup hash 防双写——overshoot 是安全的。
        cold_start_ticks_estimate = max(
            EVIDENCE_AI_AWARE_EVERY_N_A_TICKS,
            EVIDENCE_SIGNAL_CHECK_EVERY_N_TURNS,
        )
        estimated_a_coverage = timedelta(
            minutes=cold_start_ticks_estimate * EVIDENCE_SIGNAL_CHECK_IDLE_MINUTES
        )
        last_b = last_a_msg_ts - estimated_a_coverage

    if last_b >= last_a_msg_ts:
        # 窗口为空（B 已追上 A）
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
        # `aretrieve_original_by_timeframe` 在 SQL exception / engine init 失败
        # / 维护态等情况下都 swallow + 返 []（见 timeindex.py 实现），从 caller
        # 端无法区分"真空窗口"vs"transient 读失败"。保守起见 cursor 不推：
        # - 真空窗口：A 刚成功处理了同段范围，B 这里几乎不可能真空（除非
        #   A 的 SQL 看到 row 但 B 的 SQL 同段读不到——意味着 SQL 层异常）。
        #   下次 B trigger 再 query 一次 0 rows 也是常数代价（SQLite 空范围
        #   scan 极快）。
        # - Transient 失败：保留 cursor 让下次 trigger 重试该窗口，避免把整段
        #   [last_b, last_a_msg_ts] 永久 skip（Codex P1 round-5 on PR #1408）。
        logger.debug(
            f"[PathB] {name}: 窗口 {last_b.isoformat(timespec='seconds')} → "
            f"{last_a_msg_ts.isoformat(timespec='seconds')} 取回 0 rows "
            f"(可能 SQL transient 失败 swallow 成 []), 保留 cursor 下次 trigger 复查"
        )
        return

    # 解析 SQL 实际取到的最后一行 ts —— 后续所有 cursor 推进点都用这个值，
    # 不能用 last_a_msg_ts。差别只在窗口被 MAX_AI_AWARE_WINDOW_MSGS LIMIT
    # 截断时显现：截断时 last_fetched_ts < last_a_msg_ts，未取到的尾巴留
    # 给下次 B trigger 继续处理；若推到 last_a_msg_ts 会让尾巴永久 skip
    # （Codex P1 round-1 on PR #1408, P2 round-2 covers filtered-empty case）。
    last_fetched_ts = _coerce_db_ts(rows[-1][0])
    if last_fetched_ts is None:
        # 防御：_coerce_db_ts 解析失败退回 last_a_msg_ts（避免 cursor 不动
        # 死循环）。正常路径不触发——rows[-1][0] 是 SQLite 返回的 ts 字符串。
        last_fetched_ts = last_a_msg_ts

    # 同 ts 簇 LIMIT 截断死循环防御（Codex P2 round-3 on PR #1408）：
    # aretrieve_original_by_timeframe 用 inclusive `BETWEEN`，若窗口里
    # > MAX_AI_AWARE_WINDOW_MSGS 行共享同一 ts（极端情况：bulk import 或
    # store_conversation 给一次请求里所有 row 写同 ts），那么 LIMIT 切出
    # 的最早 N 行全在同 ts，cursor 推到 last_fetched_ts 后下次 BETWEEN
    # 仍把这批 row 全部捞回来 → 无限循环、该 ts 簇后面的 row 永远 skip。
    # 检测：LIMIT 拉满 AND 所有 fetched row 同 ts → cursor +1μs 越过该 ts。
    # 代价：该 ts 簇 LIMIT 之后的 tail row 被 skip（罕见——一次正常对话
    # turn 写 2~5 行，远 < MAX_AI_AWARE_WINDOW_MSGS=200）。无更便宜的修法
    # 除非把 cursor 改成 (ts, rowid) 复合键、改写 SQL，太重不划算。
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
        # 全是 system msg / 空内容。cursor 推到 last fetched（不是 last_a_msg_ts），
        # 截断时未取尾巴可能含有效 msg。
        state['last_b_check_ts'] = last_fetched_ts
        state['b_extract_failures'] = {}
        return

    # 截到 user msg bracket：首条 user msg 到末条 user msg 之间（含两端）。
    # Product thesis 防廉价层污染——首尾的 AI 残段（user 没印证过的试探 /
    # user 没回应过的独白）不该当 fact 沉淀。
    message_dicts = _trim_to_user_msg_bracket(message_dicts)
    if not message_dicts:
        # 窗口内完全无 user msg → AI-only 廉价层，故意 skip。cursor 照常推
        # 进，下次 B trigger 不会再来覆盖这段。
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

    # 已知 fact 池：用 path A 在本 B 窗口内 / 之后写的 fact 当 do-not-repeat 提示。
    # 只设下界 ``created_at >= last_b``、不设上界（CodeRabbit on PR #1408）：
    # A 的 idle/polling 延迟让"刚扫完本 B 窗口"那批 fact 的 created_at 普遍
    # 略晚于 last_a_msg_ts，若用 created_at <= last_a_msg_ts 过滤会把最新一
    # 批 A facts 整批排除——known_pool 对"刚被 A 抽过的内容"失效，path B 更
    # 容易和 A 重复抽同一窗口。多包含一些"窗口后"的 A fact 是安全的：known
    # _pool 只是 LLM 的提示，多余条目至多让 B 多抑制少量新 fact，且 Stage-1
    # dedup hash 仍是兜底。按 importance DESC 取前 MAX_KNOWN_POOL_FACTS。
    try:
        all_facts = await runtime.fact_store.aload_facts(name)
    except Exception as e:
        logger.debug(f"[PathB] {name}: aload_facts 失败，known pool 留空: {e}")
        all_facts = []

    # Importance 用 safe_importance 兜底——legacy/手改 facts.json 里可能
    # 有 'importance': "high" / None / list 等脏值，raw int(...) cast 会
    # ValueError 把整个 B 跑挂、下次 trigger 又同样脏值同样挂，path B 对该
    # 角色永久哑火（Codex P2 round-1 on PR #1408）。
    from memory.facts import safe_importance
    from memory.scopes import is_legacy_private_entry

    known_pool: list[dict] = []
    for f in all_facts:
        if not isinstance(f, dict):
            continue
        # Path B 是私聊 AI-aware 抽取：known pool 只能含 legacy 私聊
        # facts。scoped（群/成员）fact 混进来会把群内容渲进私聊 Stage-1
        # prompt（跨边界泄漏），且在繁忙群把 top-N 名额挤满、抑制私聊
        # fact 抽取。
        if not is_legacy_private_entry(f):
            continue
        created_at_raw = f.get('created_at') or ''
        try:
            # 完整 ISO 解析（含微秒）—— `created_at` 是 datetime.now().isoformat()
            # 写盘的，截到 [:19] 会丢微秒，让 created_at == last_b + 0.x 秒的
            # fact 在 `>= last_b` 比较里被误判出窗口（CodeRabbit on PR #1408）。
            created_at = datetime.fromisoformat(created_at_raw)
        except (ValueError, TypeError):
            continue
        # 防御：本仓库 `_apersist_new_facts` 写的 `created_at` 都是 naive
        # datetime.now().isoformat()，但若 import/migration 路径写入了 TZ-aware
        # 值（如 "...+00:00"），跟 naive 的 last_b 比较会抛 TypeError 让
        # `_run_path_b` 一直 fail，path B 对该角色永久哑火（Codex P1 round-7
        # on PR #1408）。比较口径上把 aware 当 naive 处理——绝大多数场景就是
        # 同一 wall-clock 时间，时区差异不应让 fact 抽取整段挂掉。
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
        # Stage-1 LLM 终态失败（重试耗尽）。cursor 保留不推进，下次 B trigger
        # 重试同窗口（fact dedup hash 防双写）。区分 None vs [] 至关重要：
        # 若把失败折叠成"成功 0 抽"，失败窗口会被永久 skip（CodeRabbit / Codex
        # P1 round-2 on PR #1408）。
        #
        # Liveness 兜底（path A 的对偶）：同一 last_b_check_ts cursor 反复
        # 失败 ≥ MEMORY_LIVENESS_MAX_ATTEMPTS 时强推 cursor 到 last_fetched_ts，
        # 避免毒窗口让 B pipeline 永久卡死该角色的 AI-aware fact 抽取。
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
    # Cursor 推进 → 旧 cursor key 永远不会再被命中，清空 path-B counter
    # 避免内存 dict 随 cursor 历史无限增长（对偶 _signal_check_mark_done
    # 在 path A 成功路径上清 a_extract_failures）。
    state['b_extract_failures'] = {}


async def _periodic_signal_extraction_loop():
    """Polls every EVIDENCE_SIGNAL_CHECK_INTERVAL_SECONDS; when the trigger condition is
    met, runs Stage-1 + Stage-2 + signal dispatch for each catgirl (RFC §3.4.3).

    First run delayed by _INITIAL_DELAY_SIGNAL seconds (staggered against the other background loops).
    """
    await asyncio.sleep(_INITIAL_DELAY_SIGNAL)
    while True:
        # 强力记忆关 → Stage-1 + Stage-2 evidence 抽取整段停。这是 evidence-RFC
        # 引入的 token 大头（每 40s 轮询一次，trigger 时跑 Stage-1 + Stage-2 两
        # 个 LLM 调用，Stage-2 还开 thinking）。关闭后 evidence_score 不再变化，
        # confirmed/promoted 走 time-driven fallback。
        #
        # 关态推进 last_check_ts 到 now（同 rebuttal 处的理由）：避免重开后
        # 把关闭期间的所有 user msg 当成"积压"一次性塞进 Stage-1+Stage-2 prompt。
        if not await gates._ais_powerful_memory_enabled():
            try:
                character_data = await runtime._config_manager.aload_characters()
                catgirl_names = list(character_data.get('猫娘', {}).keys())
                cursor_now = datetime.now()
                for name in catgirl_names:
                    try:
                        _signal_check_mark_done(name, cursor_now)
                    except Exception as cursor_e:
                        # 单角色 last_check_ts 推进失败不致命——同 rebuttal
                        # 处的理由，下一轮再试。
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

        async def _signal_check_one(name: str):
            """Stage-1 + Stage-2 + signal dispatch for a single character. Characters are
            mutually independent (per-char event_log lock / files); the outer gather runs
            them in parallel. A failure doesn't block other characters, and the cursor
            only advances on the fully successful path."""
            try:
                if not _signal_check_should_run(name, now):
                    return
                # 窗口起点：优先用上次成功 check 时戳（cursor 语义），避免
                # 长对话期间 >10 分钟的消息被永远 skip（§3.4.3 游标推进）。
                # 冷启动 / cursor 缺失时回退到 IDLE_MINUTES*2。
                start_time = _signal_check_window_start(name, now)
                rows = await runtime.time_manager.aretrieve_original_by_timeframe(
                    name, start_time, now,
                )
                if not rows:
                    _signal_check_mark_done(name, now)
                    return
                user_msgs_text = _extract_user_messages_from_rows(rows)
                if not user_msgs_text:
                    # 窗口里没 user msg —— 纯 proactive / AI 自言自语 / tool
                    # turn。这种内容**故意**不进 memory：
                    # 1. Path A 抽 user_observation fact 需要 user 发声当源
                    # 2. Path B 拣 AI 自我披露**也**只在 user 有 engagement
                    #    的窗口里跑（B 是 piggyback A，不是独立路径）
                    # 设计原则：用户不搭理 = 内容廉价层 ("90% 没心没肺"
                    # product thesis)，不该被自动当 fact 沉淀污染 memory。
                    # cursor 照常推进、计数清零，让下次有 user msg 的窗口
                    # 直接进入正常 A+B 流程。
                    _signal_check_mark_done(name, now)
                    return

                # 组装成 BaseMessage-like 结构给 extract_facts 使用
                from utils.llm_client import convert_to_messages
                message_dicts = [
                    {'type': 'human', 'data': {'content': m}}
                    for m in user_msgs_text
                ]
                # convert_to_messages 只接 list，不再解 JSON 字符串（PR #547 以来的契约）；
                # 这里之前的 json.dumps 让函数走 isinstance(data, list)==False 分支直接返回 []，
                # → messages=[] → _format_conversation render 出空字符串 → Stage-1 prompt
                # 里 ======以下为对话====== 跟 ======以上为对话====== 之间为空 → LLM 合理
                # 返回 []，整套 fact 抽取 + 后续 Stage-2 evidence 都被静默跳过。
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
                    # Liveness 兜底：同一窗口反复失败 ≥ MEMORY_LIVENESS
                    # _MAX_ATTEMPTS 强推 cursor 到 now，避免毒窗口让
                    # fact pipeline 永久卡死。
                    state = _signal_check_state.setdefault(
                        name, {'turns_since': 0, 'last_check_ts': None},
                    )
                    # CodeRabbit: 用 start_time 当 key，不要字面 'cold'。
                    # 字面 'cold' 把所有冷启动多轮失败聚合到同一桶，
                    # 第 N 次会强推 cursor 到当时的 now，把那段时间内进来的
                    # 正常 msg 也跟着 dead-letter。改用 start_time（每轮
                    # window 起点）：有稳定 cursor 时 start_time == cursor
                    # （`_signal_check_window_start` 直接返 cursor），冷启动
                    # 时 start_time 是 `now - IDLE_MINUTES*2`，每轮不同 →
                    # 冷启动阶段不会错误聚合 dead-letter。
                    cursor_key = start_time.isoformat(timespec='microseconds')
                    if not _stage1_path_a_bump_failure(name, state, cursor_key, now):
                        logger.warning(
                            f"[SignalLoop] {name}: Stage-1 失败保留 cursor 重试: {e}"
                        )
                    return

                # 先 dispatch 再 mark_done：dispatch 中途有任何 aapply 失败
                # cursor 不推进，下轮 Stage-1 在同一窗口重新抽取（Stage-1
                # dedup 保证 fact 不会翻倍写入，Stage-2 会重新生成 signal
                # 再试一次）。CodeRabbit PR #929 fix：之前 dispatch 吞异常
                # 后 mark_done 仍跑，单次 aapply 失败会永久丢一条 evidence。
                dispatch_ok = True
                if signals:
                    dispatch_ok = await _adispatch_evidence_signals(
                        name, signals, source=EVIDENCE_SOURCE_USER_FACT,
                    )
                    logger.info(
                        f"[SignalLoop] {name}: dispatch {len(signals)} 个 evidence 信号"
                    )

                # Drain checkpoint：dispatch 全部成功（含 signals=[] 即 LLM
                # 看过没关联）才 mark batch processed。任何 aapply 失败保留
                # signal_processed=False 让下轮 idle 重试这批 fact，避免
                # 把没落地的 signal 永久跳过（CodeRabbit fingerprint c755101c）。
                if dispatch_ok and batch_fact_ids:
                    await runtime.fact_store.amark_signal_processed(name, batch_fact_ids)

                if not dispatch_ok:
                    logger.warning(
                        f"[SignalLoop] {name}: dispatch 有失败，保留 cursor 下轮重试"
                    )
                    return  # 保留 cursor（不调 _signal_check_mark_done）

                # 信号写完后触发一次 score-driven pending→confirmed 扫描；
                # 独立 try/except：本步失败不应阻止 cursor 推进（score 下
                # 轮会自然重算）。
                try:
                    await runtime.reflection_engine.aauto_promote_stale(name)
                except Exception as e:
                    logger.debug(f"[SignalLoop] {name}: auto_promote_stale 失败: {e}")

                # Stage-1 + dispatch 都跨过了，cursor 推进。
                _signal_check_mark_done(name, now)

                # 记录 A 实际处理过的最晚 msg ts，给 path B 当下游边界用
                # （rows 已 ORDER BY ts ASC，最后一行就是 window 内最晚 msg）。
                # 用真实 msg ts 而不是 wall-clock now：保证 path B 看到的
                # 消息严格被 path A 看过，避免"A scan SQL 完成那一刻之后才入
                # SQLite 的 msg 被 B 抢先处理"的 race。
                state = _signal_check_state.setdefault(
                    name, {'turns_since': 0, 'last_check_ts': None},
                )
                last_msg_ts = _coerce_db_ts(rows[-1][0])
                if last_msg_ts is not None:
                    state['last_a_msg_ts'] = last_msg_ts

                # Path B trigger：A 成功跑完后 bump counter；达 N 触发
                # _run_path_b（AI-aware Stage-1 only，详见函数 docstring）。
                state['b_tick_counter'] = state.get('b_tick_counter', 0) + 1
                if state['b_tick_counter'] >= EVIDENCE_AI_AWARE_EVERY_N_A_TICKS:
                    state['b_tick_counter'] = 0
                    try:
                        await _run_path_b(name, state)
                    except Exception as e:
                        # B 失败完全不应该影响 A 路径（A 已经在 mark_done 之
                        # 后了）；只 log warning。下次 b_tick_counter 又满 N
                        # 时 B 自动重试，cursor 是 last_b_check_ts 推进的，
                        # 失败时不推 cursor → 下次 B 重新覆盖同窗口。
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

