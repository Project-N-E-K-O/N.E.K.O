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

"""Scoped lite refine engine — per-subject merge/disambiguation for group memory.

Scoped (group / participant) reflections and persona entries structurally
never merge: the shared ``MemoryRefineEngine`` crons enumerate only the
legacy entities ``('master', 'neko', 'relationship')``, and score-driven
promote-with-merge is unreachable for scoped rows. Two contradictory group
memories therefore coexist forever, and which one enters the prompt is
decided by the 2000-token render trim.

This module is a deliberately separate, cheaper engine rather than a widened
shared one — the isolation boundary is the subject from the first line, so a
bucketing or stamping bug here can only ever affect the group side:

* bucket key    ``(subject.key, subject.scope)`` per store — NEVER entity
                (every group shares entity='group_chat'; entity-bucketing
                would cluster group A with group B and merge across the
                isolation boundary; see ``fact_dedup._bucket_key``).
* trigger       only when a single subject's per-store pool reaches
                ``SCOPED_REFINE_MIN_ENTRIES``.
* work per pass at most ONE cluster of ONE subject across all buckets.
* model         summary tier, thinking off (``extra_body`` omitted →
                provider-dialect disable), short timeout.
* action set    ``merge`` only. The prompt requires contradictions to merge
                into a single conclusion (temporal change wording or the
                better-supported claim) instead of coexisting; anything the
                model is unsure about is left untouched.

Every produced entry is stamped with ``subject.as_entry_fields()`` before it
is handed to the store — an unstamped row is fail-closed invisible on every
scoped read path, so a merge that lost the stamp would silently destroy the
memories it consumed.

Speaker-trust hook (series 7/7): ``refine_pass`` and the prompt renderer
accept a trust callback. Prompt lines receive only coarse high/medium/low
bands; exact values stay in the code-side merge tie-break.
"""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass, field
from datetime import datetime
from typing import Awaitable, Callable

from config import (
    LLM_OUTPUT_GUARD_MAX_TOKENS,
    PERSONA_VERSION_HISTORY_MAX,
    SCOPED_REFINE_CLUSTER_SIZE_MAX,
    SCOPED_REFINE_COSINE_THRESHOLD,
    SCOPED_REFINE_LLM_TIMEOUT_SECONDS,
    SCOPED_REFINE_MIN_ENTRIES,
    SCOPED_REFINE_REVISIT_AFTER_DAYS,
    SCOPED_REFINE_TOPK_PER_ENTRY,
)

try:
    from memory.embeddings import (
        decode_embedding,
        get_embedding_service,
        is_cached_embedding_valid,
        parse_dim_from_model_id,
    )
except ImportError:
    # 同 memory/refine.py：embedding 栈不可用时用 stub，聚类恒空 → 整个
    # pass no-op，行为契约不变。
    from memory.embeddings_fallback import (
        decode_embedding,
        get_embedding_service,
        is_cached_embedding_valid,
        parse_dim_from_model_id,
        _warn_once,
    )
    _warn_once(__name__)
from memory._reflection.schema import normalize_reflection, refine_reflection_id
from memory.scopes import MemorySubject, entry_matches_subject, subject_from_entry
from memory.temporal import to_naive_local
from utils.logger_config import get_module_logger
from utils.token_tracker import set_call_type

logger = get_module_logger(__name__, "Memory")


STORE_PERSONA = 'persona'
STORE_REFLECTION = 'reflection'

# lite 引擎唯一合法 action。比本体四件套小得多的失效面：split/modify/
# discard 对 scoped 的增量价值低，而每多一种 action 就多一类需要变异验证
# 的错误实现。
VALID_SCOPED_REFINE_ACTIONS = frozenset({'merge'})

# trust_of 回调签名（系列 7/7 的 speaker_trust 接入点）。
TrustFn = Callable[[dict], str | float | None]


def _parse_temporal_boundary(value: object) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return to_naive_local(datetime.fromisoformat(value))
    except (TypeError, ValueError):
        return None


def _winner_precedes_newer_temporal_source(
    winner: dict, sources: list[dict],
) -> bool:
    """Return True when a bounded winner predates another source state."""
    winner_end = _parse_temporal_boundary(winner.get('event_end_at'))
    if winner_end is None and winner.get('temporal_scope') in {'episode', 'past'}:
        winner_end = _parse_temporal_boundary(winner.get('event_start_at'))
    if winner_end is None:
        return False
    for source in sources:
        if source is winner:
            continue
        start = _parse_temporal_boundary(source.get('event_start_at'))
        if start is not None and winner_end < start:
            return True
    return False


def _latest_temporal_source(sources: list[dict]) -> dict:
    """Choose metadata from the newest represented source when available."""
    dated: list[tuple[datetime, dict]] = []
    for source in sources:
        start = _parse_temporal_boundary(source.get('event_start_at'))
        if start is not None:
            dated.append((start, source))
    return max(dated, key=lambda item: item[0])[1] if dated else sources[0]


def _trust_weighted_merge_text(
    sources: list[dict], proposed_text: str,
) -> tuple[str, list[dict]]:
    """Prefer the highest-trust source in code when the margin is decisive."""
    from memory.speaker_trust import normalize_trust, stable_speaker_id

    usable = [
        source for source in sources
        if source.get('speaker_provenance_mixed') is not True
        and stable_speaker_id(source.get('speaker_id')) is not None
        and isinstance(source.get('speaker_trust'), (int, float))
        and not isinstance(source.get('speaker_trust'), bool)
        and math.isfinite(float(source.get('speaker_trust')))
    ]
    # A deterministic winner may replace the model merge only when every
    # source being consumed participated in the comparison.  Otherwise an
    # unscored legacy/mixed row could disappear behind a scored winner.
    if len(usable) != len(sources):
        return proposed_text, sources
    speaker_ids = [
        stable_speaker_id(source.get('speaker_id')) for source in usable
    ]
    if len(set(speaker_ids)) < 2:
        return proposed_text, sources
    # Multiple snapshots from one speaker are content that still needs the
    # model merge.  Trust is per speaker, so score drift between their rows
    # must not make an older statement beat their own later statement and a
    # different speaker at once.
    if len(speaker_ids) != len(set(speaker_ids)):
        return proposed_text, sources
    ordered = sorted(
        usable, key=lambda source: normalize_trust(source.get('speaker_trust')),
        reverse=True,
    )
    # The leader must beat the runner-up, not merely the weakest outlier.
    # Otherwise 0.80/0.75/0.30 would discard the unresolved 0.75 source.
    from memory.speaker_trust import preferred_by_trust
    if preferred_by_trust(
        ordered[0].get('speaker_trust'),
        ordered[1].get('speaker_trust'),
    ) != 'old':
        return proposed_text, sources
    from memory.speaker_trust import deterministic_relation
    # Scoped ``merge`` also covers duplicates, complementary details, and
    # mixed clusters.  Replacing the whole merge with one source is safe only
    # when that winner conflicts with every row it would consume; a conflict
    # between two other rows must not let an unrelated leader erase both.
    winner = ordered[0]
    # Trust resolves conflicting reports about the same state, not temporal
    # evolution.  A bounded historical winner must not erase a later/current
    # opposite state merely because its speaker has a higher score; retain the
    # model's transition merge and both audit sources instead.
    if _winner_precedes_newer_temporal_source(winner, ordered):
        return proposed_text, sources
    if not all(
        deterministic_relation(
            str(winner.get('text') or ''), str(other.get('text') or ''),
        ) == 'correction'
        for other in ordered[1:]
    ):
        return proposed_text, sources
    winner_text = str(winner.get('text') or '').strip()
    return (winner_text or proposed_text), [winner]


def _pick_temporal_boundary(values: list[str], *, latest: bool) -> str | None:
    """Pick an original ISO boundary after ordering represented instants."""
    parsed: list[tuple[datetime, str]] = []
    for value in values:
        try:
            instant = to_naive_local(datetime.fromisoformat(value))
        except (TypeError, ValueError):
            continue
        if instant is not None:
            parsed.append((instant, value))
    if parsed:
        pick = max if latest else min
        return pick(parsed, key=lambda item: item[0])[1]
    # Preserve a legacy malformed boundary instead of silently deleting it.
    return values[0] if values else None


@dataclass
class ScopedRefineBucket:
    """One subject's candidate pool inside one store."""

    subject: MemorySubject
    store: str  # STORE_PERSONA | STORE_REFLECTION
    entries: list[dict] = field(default_factory=list)

    @property
    def marker(self) -> tuple[str, str, str]:
        return (self.subject.key, self.subject.scope, self.store)


def gather_scoped_refine_buckets(
    persona: dict,
    reflections: list[dict],
    *,
    min_entries: int | None = None,
    max_attempts: int | None = None,
    self_heal_seconds: float | None = None,
) -> list[ScopedRefineBucket]:
    """Bucket scoped entries by (subject.key, scope) per store.

    Authority is the ENTRY-LEVEL stamp (``subject_from_entry``), never the
    persona section key — a section can legally contain rows from multiple
    custom scopes, and corrupt partial stamps are excluded on every read
    path so they must not enter a merge pool either (they would resurrect
    invisible data). Liveness mirrors the shared refine gather: entries at
    ``refine_attempts >= max_attempts`` stay out until the dead-letter
    self-heal cooldown lets one probe through.
    """
    from config import (
        MEMORY_DEAD_LETTER_SELF_HEAL_SECONDS,
        MEMORY_LIVENESS_MAX_ATTEMPTS,
    )
    from memory.facts import safe_int_field
    from memory.temporal import cooldown_elapsed

    if min_entries is None:
        min_entries = SCOPED_REFINE_MIN_ENTRIES
    if max_attempts is None:
        max_attempts = MEMORY_LIVENESS_MAX_ATTEMPTS
    if self_heal_seconds is None:
        self_heal_seconds = MEMORY_DEAD_LETTER_SELF_HEAL_SECONDS

    def _alive(entry: dict) -> bool:
        return (
            safe_int_field(entry, 'refine_attempts') < max_attempts
            or cooldown_elapsed(
                entry.get('last_refine_attempt_at'), self_heal_seconds,
            )
        )

    buckets: dict[tuple, ScopedRefineBucket] = {}

    def _collect(entry: dict, store: str) -> None:
        if not isinstance(entry, dict):
            return
        # suppress 条目排除：渲染层刻意把它们放「不主动提及」通道，merge
        # 产物是普通可见条目，把 suppressed 内容并进去等于解除抑制。
        if entry.get('protected') or entry.get('suppress') or not entry.get('id'):
            return
        subject = subject_from_entry(entry)
        if subject is None:
            return
        if not _alive(entry):
            return
        key = (subject.key, subject.scope, store)
        bucket = buckets.get(key)
        if bucket is None:
            bucket = ScopedRefineBucket(subject=subject, store=store)
            buckets[key] = bucket
        bucket.entries.append(entry)

    for section in (persona or {}).values():
        if not isinstance(section, dict):
            continue
        for entry in section.get('facts', []) or []:
            _collect(entry, STORE_PERSONA)
    for entry in reflections or []:
        _collect(entry, STORE_REFLECTION)

    ready = [b for b in buckets.values() if len(b.entries) >= min_entries]
    ready.sort(key=lambda b: b.marker)
    return ready


# apply_fn(bucket, cluster, actions, cluster_hash) -> 应用成功的 action 数；
# failure_fn(bucket, cluster, cluster_hash)。存储读写全在回调侧（manager
# 锁内），engine 不碰磁盘——同本体 refine 的分工。返回值参与失败判定：
# 非空 actions 全被拒（语义垃圾）按 cluster 失败计 refine_attempts。
ScopedApplyFn = Callable[
    [ScopedRefineBucket, list[dict], list[dict], str], Awaitable[int]
]
ScopedFailureFn = Callable[
    [ScopedRefineBucket, list[dict], str], Awaitable[None]
]


class ScopedLiteRefineEngine:
    """Cost-bounded merge engine for scoped memory pools.

    Stateless apart from the embedding-service handle; the rotation cursor
    lives with the caller (``start_after`` in / ``served`` out) so a fresh
    engine per cron tick keeps fairness across subjects.
    """

    def __init__(self, config_manager):
        self._cm = config_manager
        self._service = get_embedding_service()

    async def refine_pass(
        self,
        buckets: list[ScopedRefineBucket],
        *,
        apply_fn: ScopedApplyFn,
        scope_label: str,
        failure_fn: ScopedFailureFn | None = None,
        start_after: tuple | None = None,
        trust_of: TrustFn | None = None,
    ) -> dict:
        """Process at most ONE cluster of ONE bucket; return pass stats.

        Buckets are visited in marker order starting after ``start_after``
        (rotating cursor — a poison bucket must not starve the others).
        Bucket scanning is LLM-free; the single LLM call happens on the
        first workable (non-hash-fresh) cluster found. ``served`` in the
        result is that bucket's marker, or ``None`` when nothing ran.
        """
        result = {
            'buckets_seen': len(buckets),
            'clusters_seen': 0,
            'clusters_skipped': 0,
            'resolved': 0,
            'failed': 0,
            'served': None,
        }
        if not buckets or self._service.is_disabled():
            return result

        ordered = sorted(buckets, key=lambda b: b.marker)
        start = 0
        if start_after is not None:
            for i, bucket in enumerate(ordered):
                if bucket.marker > tuple(start_after):
                    start = i
                    break
        rotation = ordered[start:] + ordered[:start]

        for bucket in rotation:
            clusters = self._compute_clusters(bucket.entries)
            if not clusters:
                continue
            result['clusters_seen'] += len(clusters)
            active: list[tuple[list[dict], str]] = []
            for cluster in clusters:
                cluster_hash = self._cluster_hash(cluster)
                if self._all_stamped_fresh(cluster, cluster_hash):
                    result['clusters_skipped'] += 1
                    continue
                active.append((cluster, cluster_hash))
            if not active:
                continue
            active.sort(key=lambda t: self._cluster_starvation_key(t[0]))
            cluster, cluster_hash = active[0]
            result['served'] = bucket.marker
            cluster_failed = False
            try:
                ok = await self._resolve_cluster(
                    bucket, cluster, cluster_hash, apply_fn, trust_of,
                )
                if ok:
                    result['resolved'] = 1
                else:
                    result['failed'] = 1
                    cluster_failed = True
            except Exception as e:  # noqa: BLE001 — refine is best-effort
                result['failed'] = 1
                cluster_failed = True
                logger.warning(
                    f"[ScopedRefine] {scope_label} cluster {cluster_hash} 异常"
                    f"（计入 refine_attempts）: {e}"
                )
            if cluster_failed and failure_fn is not None:
                try:
                    await failure_fn(bucket, cluster, cluster_hash)
                except Exception as fe:  # noqa: BLE001 — 兜底自身失败不挂主路径
                    logger.warning(
                        f"[ScopedRefine] {scope_label} cluster {cluster_hash} "
                        f"failure_fn 异常: {fe}"
                    )
            # 成本上界：每 pass 只处理一个 cluster，无论成败。
            break
        return result

    # ── clustering（同本体算法，SCOPED_* 参数） ─────────────────────

    def _compute_clusters(self, entries: list[dict]) -> list[list[dict]]:
        if len(entries) < 2:
            return []
        if not self._service.is_available():
            return []
        model_id = self._service.model_id()
        if not model_id:
            return []
        target_dim = parse_dim_from_model_id(model_id)

        import numpy as np

        valid: list[dict] = []
        vecs: list = []
        for e in entries:
            text = e.get('text', '')
            if not is_cached_embedding_valid(e, text, model_id):
                continue
            v = decode_embedding(e.get('embedding'))
            if v is None or v.size == 0:
                continue
            if target_dim is None:
                target_dim = int(v.size)
            elif v.size != target_dim:
                continue
            valid.append(e)
            vecs.append(v)

        if len(valid) < 2:
            return []

        matrix = np.stack(vecs)
        sim_matrix = matrix @ matrix.T
        np.fill_diagonal(sim_matrix, -1.0)

        threshold = SCOPED_REFINE_COSINE_THRESHOLD
        topk = SCOPED_REFINE_TOPK_PER_ENTRY
        n = len(valid)

        adj: list[list[int]] = [[] for _ in range(n)]
        for i in range(n):
            row = sim_matrix[i]
            cand = [
                (int(j), float(row[j]))
                for j in range(n) if float(row[j]) >= threshold
            ]
            cand.sort(key=lambda x: -x[1])
            adj[i] = [j for j, _ in cand[:topk]]

        parent = list(range(n))

        def find(x: int) -> int:
            while parent[x] != x:
                parent[x] = parent[parent[x]]
                x = parent[x]
            return x

        def union(a: int, b: int) -> None:
            ra, rb = find(a), find(b)
            if ra != rb:
                parent[ra] = rb

        for i in range(n):
            for j in adj[i]:
                union(i, j)

        groups: dict[int, list[int]] = {}
        for i in range(n):
            groups.setdefault(find(i), []).append(i)

        clusters: list[list[dict]] = []
        for indices in groups.values():
            if len(indices) < 2:
                continue
            if len(indices) > SCOPED_REFINE_CLUSTER_SIZE_MAX:
                strengths = []
                for i in indices:
                    others = [j for j in indices if j != i]
                    max_sim = max(
                        (float(sim_matrix[i][j]) for j in others), default=-1.0,
                    )
                    strengths.append((i, max_sim))
                strengths.sort(key=lambda x: -x[1])
                indices = [s[0] for s in strengths[:SCOPED_REFINE_CLUSTER_SIZE_MAX]]
            clusters.append([valid[i] for i in indices])
        return clusters

    # ── hash skip + starvation（同本体语义；池内无 fact，全员计入） ──

    @staticmethod
    def _cluster_hash(cluster: list[dict]) -> str:
        ids = sorted(str(e.get('id', '')) for e in cluster if e.get('id'))
        return hashlib.sha1('|'.join(ids).encode('utf-8')).hexdigest()[:16]

    @staticmethod
    def _all_stamped_fresh(cluster: list[dict], cluster_hash: str) -> bool:
        from datetime import timedelta
        cutoff = datetime.now() - timedelta(days=SCOPED_REFINE_REVISIT_AFTER_DAYS)
        for e in cluster:
            if e.get('last_refine_cluster_hash') != cluster_hash:
                return False
            last_at = e.get('last_refine_at')
            if not last_at:
                return False
            try:
                if datetime.fromisoformat(last_at) < cutoff:
                    return False
            except (ValueError, TypeError):
                return False
        return True

    @staticmethod
    def _cluster_starvation_key(cluster: list[dict]) -> str:
        timestamps = [(e.get('last_refine_at') or '') for e in cluster]
        return min(timestamps) if timestamps else ''

    # ── LLM call + parse + delegate ─────────────────────────────────

    async def _resolve_cluster(
        self,
        bucket: ScopedRefineBucket,
        cluster: list[dict],
        cluster_hash: str,
        apply_fn: ScopedApplyFn,
        trust_of: TrustFn | None,
    ) -> bool:
        cluster_text = self._render_cluster(cluster, trust_of)
        if not cluster_text:
            return False

        from config.prompts.prompts_memory import get_scoped_memory_refine_prompt
        from utils.language_utils import get_global_language
        from utils.llm_client import create_chat_llm_async

        prompt = (
            get_scoped_memory_refine_prompt(get_global_language())
            .replace('{CLUSTER}', cluster_text)
            .replace('{COUNT}', str(len(cluster)))
        )

        # lite 管线的成本契约（存在前提就是比本体便宜）：
        #   summary tier（本体用 correction tier）；
        #   不传 extra_body → create_chat_llm 缺省带各 provider 的「关
        #   thinking」方言（本体显式传 None 开 thinking）；
        #   60s 短超时（本体 110s）。
        set_call_type("memory_scoped_refine")
        api_config = await self._cm.aget_model_api_config('summary')
        llm = await create_chat_llm_async(
            api_config['model'],
            api_config['base_url'],
            api_config['api_key'],
            timeout=SCOPED_REFINE_LLM_TIMEOUT_SECONDS,
            max_retries=0,
            max_completion_tokens=LLM_OUTPUT_GUARD_MAX_TOKENS,
            provider_type=api_config.get('provider_type'),
        )
        try:
            resp = await llm.ainvoke(prompt)  # noqa: LLM_INPUT_BUDGET  # cluster bounded by SCOPED_REFINE_CLUSTER_SIZE_MAX short entries
        finally:
            await llm.aclose()

        raw = (resp.content or "").strip()
        if raw.startswith("```"):
            raw = raw.replace("```json", "").replace("```", "").strip()
        if not raw:
            logger.warning(
                f"[ScopedRefine] LLM 返回空 (cluster_hash={cluster_hash})"
            )
            return False
        try:
            actions = json.loads(raw)
        except json.JSONDecodeError as e:
            logger.warning(
                f"[ScopedRefine] LLM JSON 解析失败 (cluster_hash={cluster_hash}): {e}"
            )
            return False
        if not isinstance(actions, list):
            logger.warning(
                f"[ScopedRefine] LLM 输出非 list (cluster_hash={cluster_hash}): "
                f"{type(actions)}"
            )
            return False

        applied = await apply_fn(bucket, cluster, actions, cluster_hash)
        if actions and not applied:
            # 非空 actions 但没有一条通过 apply 校验 = 语义垃圾输出。apply
            # 侧刻意不 stamp（等下轮重试），这里必须按失败计——否则毒
            # cluster 既不 stamp 也不进 refine_attempts，每个 cron 周期
            # 白打一次 LLM 直到永远，戳穿 lite 管线的成本契约。空数组是
            # 明确 no-op：apply 已 stamp，按成功计。
            logger.warning(
                f"[ScopedRefine] LLM 输出 {len(actions)} 条 action 全部无效 "
                f"(cluster_hash={cluster_hash})，按失败计入 refine_attempts"
            )
            return False
        return True

    @staticmethod
    def _render_cluster(
        cluster: list[dict], trust_of: TrustFn | None = None,
    ) -> str:
        """Numbered prompt lines; optional per-entry trust annotation.

        Numeric values and pre-banded values both render only as
        ``trust=high|medium|low``. With ``trust_of=None`` lines render
        without the field.
        """
        lines = []
        for i, e in enumerate(cluster):
            text = e.get('text', '')
            eid = e.get('id', '')
            if not text or not eid:
                continue
            trust = trust_of(e) if trust_of is not None else None
            from memory.speaker_trust import trust_band
            trust_label = (
                trust_band(trust)
                if isinstance(trust, (int, float)) and not isinstance(trust, bool)
                else trust if trust in {'high', 'medium', 'low'} else None
            )
            trust_part = f", trust={trust_label}" if trust_label else ""
            lines.append(f"[{i}] (id={eid}{trust_part}) {text}")
        return "\n".join(lines)


# ── apply（存储侧写回；manager 锁内） ────────────────────────────────


def _valid_merge_source_ids(
    action: dict, cluster_ids: set, by_id: dict, consumed: set,
    cluster_text_by_id: dict,
) -> list[str]:
    """Return the action's source ids, or ``[]`` unless EVERY one is valid.

    All-or-nothing on purpose: the LLM wrote its conclusion text from the
    COMPLETE snapshot it was shown. If any named source became invalid
    during the unlocked LLM window (edited / suppressed / protected /
    consumed / gone terminal / hallucinated foreign id), partially merging
    the remaining sources would persist a conclusion asserting content
    whose only support was the invalidated source — suppressed content in
    particular would resurface through that seam. Rejecting the whole
    action costs one retry round; the cluster re-forms and gets re-judged
    on current state.
    """
    src_ids_raw = action.get('source_ids') or []
    if not isinstance(src_ids_raw, list):
        return []
    # 去重保序（LLM 偶发重复 id 会让 evidence 继承重复计数）。
    seen: set = set()
    unique_ids: list[str] = []
    for sid in src_ids_raw:
        if sid not in seen:
            unique_ids.append(sid)
            seen.add(sid)
    # 文本快照校验：LLM 决策是针对 cluster 里那份文本做出的。锁外 LLM
    # 窗口期间若有并发写者改了该行文本，按 id 盲信会把「模型没见过的
    # 内容」合并掉。suppress 同理在锁内重验：gather 时未抑制、窗口内被
    # 标记 suppress 的源若被消费，其内容会以普通可见条目的身份复活。
    # 任一源失效 → 整条 action 拒绝（见 docstring 的 all-or-nothing）。
    for sid in unique_ids:
        if (
            sid not in cluster_ids
            or sid not in by_id
            or sid in consumed
            or by_id[sid].get('protected')
            or by_id[sid].get('suppress')
            or by_id[sid].get('text') != cluster_text_by_id.get(sid)
        ):
            return []
    return unique_ids


async def apply_scoped_persona_merge(
    persona_manager,
    name: str,
    subject: MemorySubject,
    cluster: list[dict],
    actions: list[dict],
    cluster_hash: str,
) -> int:
    """Apply merge actions to one subject's persona section entries.

    Mirrors the shared persona refine apply (lock → validate → consume +
    produce → stamp survivors → save) with two scoped deltas: the produced
    entry is stamped with ``subject.as_entry_fields()`` (an unstamped row is
    fail-closed invisible on scoped render), and only entries matching this
    exact subject are addressable — a cluster can never touch another
    scope's rows even if the LLM hallucinates their ids.
    """
    async with persona_manager._get_alock(name):
        persona = await persona_manager._aensure_persona_locked(name)
        section = persona_manager._get_section_facts(
            persona, subject.kind, subject=subject,
        )
        by_id = {
            e.get('id'): e for e in section
            if isinstance(e, dict) and e.get('id')
            and entry_matches_subject(e, subject)
        }
        cluster_ids = {
            e.get('id') for e in cluster if isinstance(e, dict) and e.get('id')
        }
        cluster_text_by_id = {
            e.get('id'): e.get('text') for e in cluster
            if isinstance(e, dict) and e.get('id')
        }
        consumed: set[str] = set()
        produced: list[dict] = []
        applied = 0
        now_iso = datetime.now().isoformat()

        for act_obj in actions:
            if not isinstance(act_obj, dict):
                continue
            act = act_obj.get('action')
            if act not in VALID_SCOPED_REFINE_ACTIONS:
                logger.warning(f"[ScopedRefine apply] persona: 非法 action {act!r}")
                continue
            valid_ids = _valid_merge_source_ids(
                act_obj, cluster_ids, by_id, consumed, cluster_text_by_id,
            )
            if len(valid_ids) < 2:
                continue
            text = str((act_obj.get('produce') or {}).get('text', '')).strip() \
                if isinstance(act_obj.get('produce'), dict) \
                else str(act_obj.get('text', '')).strip()
            if not text:
                continue
            sources = [by_id[sid] for sid in valid_ids]
            text, provenance_sources = _trust_weighted_merge_text(sources, text)
            semantic_sources = (
                provenance_sources if len(provenance_sources) == 1 else sources
            )
            merged = persona_manager._normalize_entry(text)
            merged['id'] = persona_manager._refine_persona_id(text)
            history = []
            max_rein = 0.0
            max_disp = 0.0
            max_user_count = 0
            max_sub_zero_days = 0
            latest_rein_signal_at: str | None = None
            latest_disp_signal_at: str | None = None
            latest_sub_zero_increment: str | None = None
            inherited_source = None
            inherited_source_id = None
            # 全部源的上游 reflection source_id（含源条目此前 merge 累积
            # 的清单）：time-driven 晋升的幂等检查按 source_id 找 persona
            # 载体，只继承首源会让其余 reflection 的半提交重试重复晋升。
            merged_source_ids: list = []
            for sid in valid_ids:
                src = by_id[sid]
                history_entry = {
                    'text': src.get('text', ''),
                    'replaced_at': now_iso,
                    'reason': 'scoped_refine_merge',
                    'source_fact_id': None,
                }
                for provenance_key in (
                    'speaker_id', 'speaker_label', 'speaker_trust',
                ):
                    if src.get(provenance_key) is not None:
                        history_entry[provenance_key] = src[provenance_key]
                history.append(history_entry)
                for upstream in (
                    [src.get('source_id')] + list(src.get('merged_source_ids') or [])
                ):
                    if upstream and upstream not in merged_source_ids:
                        merged_source_ids.append(upstream)
            for src in semantic_sources:
                max_rein = max(max_rein, float(src.get('reinforcement', 0) or 0))
                max_disp = max(max_disp, float(src.get('disputation', 0) or 0))
                max_user_count = max(
                    max_user_count,
                    int(src.get('user_fact_reinforce_count', 0) or 0),
                )
                max_sub_zero_days = max(
                    max_sub_zero_days, int(src.get('sub_zero_days', 0) or 0),
                )
                for key in (
                    'rein_last_signal_at',
                    'disp_last_signal_at',
                    'sub_zero_last_increment_date',
                ):
                    v = src.get(key)
                    if not v:
                        continue
                    if key == 'rein_last_signal_at':
                        if latest_rein_signal_at is None or v > latest_rein_signal_at:
                            latest_rein_signal_at = v
                    elif key == 'disp_last_signal_at':
                        if latest_disp_signal_at is None or v > latest_disp_signal_at:
                            latest_disp_signal_at = v
                    else:
                        if (
                            latest_sub_zero_increment is None
                            or v > latest_sub_zero_increment
                        ):
                            latest_sub_zero_increment = v
                if inherited_source is None:
                    inherited_source = src.get('source')
                    inherited_source_id = src.get('source_id')
            merged['version_history'] = history[-PERSONA_VERSION_HISTORY_MAX:]
            merged['reinforcement'] = max_rein
            merged['disputation'] = max_disp
            merged['user_fact_reinforce_count'] = max_user_count
            merged['sub_zero_days'] = max_sub_zero_days
            merged['rein_last_signal_at'] = latest_rein_signal_at
            merged['disp_last_signal_at'] = latest_disp_signal_at
            merged['sub_zero_last_increment_date'] = latest_sub_zero_increment
            merged['source'] = inherited_source or 'scoped_refine'
            merged['source_id'] = inherited_source_id
            merged['merged_from_ids'] = list(valid_ids)
            merged['merged_source_ids'] = merged_source_ids
            from memory.speaker_trust import provenance_of_entries
            merged.update(provenance_of_entries(provenance_sources))
            # subject 戳：无戳条目在 scoped 渲染路径 fail-closed 掉队，
            # 漏掉这行等于把被合并的记忆整体蒸发。
            merged.update(subject.as_entry_fields())
            # 产物 id 撞车守卫：id 由产出文本+时间盐派生，同批两条相同
            # 文本可能撞 id——撞车的 action 跳过（不消费源），走「不
            # stamp、下轮重审」的既有路径。
            if merged['id'] in {p.get('id') for p in produced} or merged['id'] in by_id:
                logger.warning(
                    f"[ScopedRefine apply] persona: 产物 id 撞车，跳过该 action "
                    f"(id={merged['id']})"
                )
                continue
            produced.append(merged)
            consumed.update(valid_ids)
            applied += 1

        # stamp 三分支：同本体 refine apply 的语义（垃圾输出不 stamp，
        # 等下轮重试；明确 no-op 也 stamp 防 hash skip 失效）。
        if applied == 0 and actions:
            return 0

        new_section = [
            e for e in section
            if not (isinstance(e, dict) and e.get('id') in consumed)
        ]
        new_section.extend(produced)

        stamped = 0
        for e in new_section:
            if not isinstance(e, dict):
                continue
            eid = e.get('id')
            # 幸存者盖 stamp 前重验文本快照：cluster_hash 只含 id，LLM 窗
            # 口内被并发改写的行若照常 stamp，新文本会被 hash-skip 静默压
            # 制 30 天——文本漂移的幸存者不 stamp，下轮重新入审。
            if (
                eid in cluster_ids
                and eid not in consumed
                and e.get('text') == cluster_text_by_id.get(eid)
            ):
                e['last_refine_cluster_hash'] = cluster_hash
                e['last_refine_at'] = now_iso
                if e.get('refine_attempts'):
                    e['refine_attempts'] = 0
                stamped += 1

        if applied == 0 and stamped == 0:
            return 0

        section[:] = new_section
        await persona_manager.asave_persona(name, persona)
        logger.info(
            f"[ScopedRefine] {name} [{subject.kind}/{subject.subject_id}]: "
            f"persona 应用 {applied} merge (cluster_hash={cluster_hash}, "
            f"stamped={stamped}, +{len(produced)} produced, "
            f"-{len(consumed)} consumed)"
        )
    return applied


async def apply_scoped_reflection_merge(
    reflection_engine,
    name: str,
    subject: MemorySubject,
    cluster: list[dict],
    actions: list[dict],
    cluster_hash: str,
) -> int:
    """Apply merge actions to one subject's active reflections.

    Consumed sources are NOT deleted: they flip to the existing terminal
    vocabulary ``status='merged'`` + ``absorbed_into=<new id>`` (invisible
    to render/recall, preserved in reflections.json — the shared refine
    physically deletes its sources; the scoped side keeps the data, in the
    same spirit as subject archival). The produced reflection re-enters the
    normal scoped lifecycle: ``confirmed`` now → time-driven promotion
    later, so a merge that happens before promotion also deduplicates the
    eventual persona write.
    """
    async with reflection_engine._get_alock(name):
        reflections = await reflection_engine.aload_reflections(name)
        by_id = {
            r.get('id'): r for r in reflections
            if isinstance(r, dict) and r.get('id')
            and entry_matches_subject(r, subject)
        }
        cluster_ids = {
            e.get('id') for e in cluster if isinstance(e, dict) and e.get('id')
        }
        cluster_text_by_id = {
            e.get('id'): e.get('text') for e in cluster
            if isinstance(e, dict) and e.get('id')
        }
        consumed: set[str] = set()
        produced: list[dict] = []
        applied = 0
        now_iso = datetime.now().isoformat()

        for act_obj in actions:
            if not isinstance(act_obj, dict):
                continue
            act = act_obj.get('action')
            if act not in VALID_SCOPED_REFINE_ACTIONS:
                logger.warning(
                    f"[ScopedRefine apply] reflection: 非法 action {act!r}"
                )
                continue
            valid_ids = _valid_merge_source_ids(
                act_obj, cluster_ids, by_id, consumed, cluster_text_by_id,
            )
            if len(valid_ids) < 2:
                continue
            text = str((act_obj.get('produce') or {}).get('text', '')).strip() \
                if isinstance(act_obj.get('produce'), dict) \
                else str(act_obj.get('text', '')).strip()
            if not text:
                continue
            sources = [by_id[sid] for sid in valid_ids]
            text, provenance_sources = _trust_weighted_merge_text(sources, text)
            # A decisive trust arbitration narrows semantic content to one
            # source.  Its ontology and event metadata must follow the same
            # winner; all original sources remain below as audit provenance.
            semantic_sources = (
                provenance_sources if len(provenance_sources) == 1 else sources
            )
            semantic_source = (
                semantic_sources[0]
                if len(semantic_sources) == 1
                else _latest_temporal_source(semantic_sources)
            )
            source_fact_ids: list[str] = []
            for src in semantic_sources:
                for fid in src.get('source_fact_ids') or []:
                    if fid not in source_fact_ids:
                        source_fact_ids.append(fid)
            audit_source_fact_ids: list[str] = []
            for src in sources:
                for fid in src.get('source_fact_ids') or []:
                    if fid not in audit_source_fact_ids:
                        audit_source_fact_ids.append(fid)
            # 事件窗取并集而非继承首源：矛盾合并的结论（「曾X后Y」）覆盖
            # 全部源的时间跨度，只抄首源会把结论锚在旧时段，recall_by_time
            # 按当前时段召回时会漏掉它。start 取最早；end 有任一源为 None
            # （pattern/进行中，无结束点）则并集也无结束点，否则取最晚。
            starts = [
                s.get('event_start_at') for s in semantic_sources
                if s.get('event_start_at')
            ]
            ends = [s.get('event_end_at') for s in semantic_sources]
            merged_start = _pick_temporal_boundary(starts, latest=False)
            merged_end = (
                None if (not ends or any(e is None for e in ends))
                else _pick_temporal_boundary(ends, latest=True)
            )
            merged = normalize_reflection({
                'id': refine_reflection_id(text),
                'text': text,
                'entity': subject.kind,
                # scoped 无 pending 通道：合并产物直接回到 confirmed 态，
                # 走 time-driven 尾程（对齐 synthesize_reflections 的
                # scoped 分支）。
                'status': 'confirmed',
                'source_fact_ids': source_fact_ids,
                'audit_source_fact_ids': audit_source_fact_ids,
                'created_at': now_iso,
                'confirmed_at': now_iso,
                'auto_confirmed': True,
                'feedback': None,
                'relation_type': semantic_source.get('relation_type'),
                'temporal_scope': semantic_source.get('temporal_scope'),
                'subject': semantic_source.get('subject'),
                'event_when_raw': semantic_source.get('event_when_raw'),
                'event_start_at': merged_start,
                'event_end_at': merged_end,
                'schema_version': semantic_source.get('schema_version', 1),
                'merged_from_ids': list(valid_ids),
            })
            # confirmed 渲染门要求 evidence_score > 0：继承源里最高的
            # reinforcement，floor 0.1（对齐 scoped 合成的最小正种子）。
            max_rein = max(
                (
                    float(s.get('reinforcement', 0) or 0)
                    for s in semantic_sources
                ),
                default=0.0,
            )
            merged['reinforcement'] = max(max_rein, 0.1)
            merged['rein_last_signal_at'] = now_iso
            from memory.speaker_trust import provenance_of_entries
            merged.update(provenance_of_entries(provenance_sources))
            # subject 戳：无戳 reflection 在 scoped 读路径 fail-closed 掉队。
            merged.update(subject.as_entry_fields())
            # 产物 id 撞车守卫（同 persona 侧）：撞车会让源条目的
            # absorbed_into 指向不唯一目标、溯源链断裂。
            if merged['id'] in {p.get('id') for p in produced} or merged['id'] in by_id:
                logger.warning(
                    f"[ScopedRefine apply] reflection: 产物 id 撞车，跳过该 "
                    f"action (id={merged['id']})"
                )
                continue
            for sid in valid_ids:
                src = by_id[sid]
                src['status'] = 'merged'
                src['absorbed_into'] = merged['id']
                src['merged_at'] = now_iso
            produced.append(merged)
            consumed.update(valid_ids)
            applied += 1

        if applied == 0 and actions:
            return 0

        stamped = 0
        for r in reflections:
            if not isinstance(r, dict):
                continue
            rid = r.get('id')
            # 同 persona 侧：文本漂移的幸存者不 stamp，防 hash-skip 把
            # 未经模型看过的新文本压制 30 天。
            if (
                rid in cluster_ids
                and rid not in consumed
                and r.get('text') == cluster_text_by_id.get(rid)
            ):
                r['last_refine_cluster_hash'] = cluster_hash
                r['last_refine_at'] = now_iso
                if r.get('refine_attempts'):
                    r['refine_attempts'] = 0
                stamped += 1

        if applied == 0 and stamped == 0:
            return 0

        reflections.extend(produced)
        await reflection_engine.asave_reflections(name, reflections)
        logger.info(
            f"[ScopedRefine] {name} [{subject.kind}/{subject.subject_id}]: "
            f"reflection 应用 {applied} merge (cluster_hash={cluster_hash}, "
            f"stamped={stamped}, +{len(produced)} produced, "
            f"-{len(consumed)} merged-away)"
        )
    return applied


# ── liveness bump（失败路径；同本体字段，scoped 寻址） ────────────────


async def abump_scoped_persona_refine_attempts(
    persona_manager,
    name: str,
    subject: MemorySubject,
    cluster: list[dict],
    cluster_hash: str,
) -> None:
    """Persisted failure counter for scoped persona cluster members.

    The shared ``_abump_refine_attempts`` addresses sections by legacy
    entity name and would create a bogus top-level ``group_chat`` section
    for scoped members — hence this scoped-addressed twin (same fields,
    same dead-letter semantics).
    """
    from memory.facts import safe_int_field
    from config import MEMORY_LIVENESS_MAX_ATTEMPTS

    member_ids = {
        e.get('id') for e in cluster if isinstance(e, dict) and e.get('id')
    }
    if not member_ids:
        return
    async with persona_manager._get_alock(name):
        persona = await persona_manager._aensure_persona_locked(name)
        section = persona_manager._get_section_facts(
            persona, subject.kind, subject=subject,
        )
        modified = False
        now_iso = datetime.now().isoformat()
        for e in section:
            if not isinstance(e, dict) or e.get('id') not in member_ids:
                continue
            if not entry_matches_subject(e, subject):
                continue
            new_attempts = safe_int_field(e, 'refine_attempts') + 1
            e['refine_attempts'] = new_attempts
            e['last_refine_attempt_at'] = now_iso
            modified = True
            if new_attempts == MEMORY_LIVENESS_MAX_ATTEMPTS:
                logger.warning(
                    f"[ScopedRefine] {name}: persona entry id={e.get('id')} "
                    f"[{subject.kind}/{subject.subject_id}] "
                    f"refine_attempts={new_attempts} 达 dead-letter 阈值 "
                    f"(cluster_hash={cluster_hash})"
                )
        if modified:
            await persona_manager.asave_persona(name, persona)


async def abump_scoped_reflection_refine_attempts(
    reflection_engine,
    name: str,
    subject: MemorySubject,
    cluster: list[dict],
    cluster_hash: str,
) -> None:
    """Reflection twin of ``abump_scoped_persona_refine_attempts``."""
    from memory.facts import safe_int_field
    from config import MEMORY_LIVENESS_MAX_ATTEMPTS

    member_ids = {
        e.get('id') for e in cluster if isinstance(e, dict) and e.get('id')
    }
    if not member_ids:
        return
    async with reflection_engine._get_alock(name):
        reflections = await reflection_engine.aload_reflections(name)
        modified = False
        now_iso = datetime.now().isoformat()
        for r in reflections:
            if not isinstance(r, dict) or r.get('id') not in member_ids:
                continue
            if not entry_matches_subject(r, subject):
                continue
            new_attempts = safe_int_field(r, 'refine_attempts') + 1
            r['refine_attempts'] = new_attempts
            r['last_refine_attempt_at'] = now_iso
            modified = True
            if new_attempts == MEMORY_LIVENESS_MAX_ATTEMPTS:
                logger.warning(
                    f"[ScopedRefine] {name}: reflection id={r.get('id')} "
                    f"[{subject.kind}/{subject.subject_id}] "
                    f"refine_attempts={new_attempts} 达 dead-letter 阈值 "
                    f"(cluster_hash={cluster_hash})"
                )
        if modified:
            await reflection_engine.asave_reflections(name, reflections)
