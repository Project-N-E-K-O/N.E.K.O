# -*- coding: utf-8 -*-
"""MemoryRefineEngine — cosine cluster + LLM 决议四件套 refine engine。

Phase A-3 of memory enhancements. Drives PERSONA_REFINE and
REFLECTION_REFINE crons. cluster 内成员同 entity（engine 层强制切片），
reflection refine 的 cluster 可混入 absorbed fact 作为只读信息源
（fact 不可被 split/discard/modify，代码层兜底）。

Pipeline 每 pass:
  1. 采集候选 entries（按 type/entity 切片）
  2. 同 entity 池内 cosine 邻接 → connected component（双 cap：阈值 +
     topk-per-entry；溢出 cluster 按 cosine 强度截到 CLUSTER_SIZE_MAX）
  3. cluster_hash skip（hash 全员命中 + 未超 REVISIT_AFTER_DAYS → 跳过）
  4. 饥饿度排序（cluster 内 min(last_refine_at)，None 视为 ''）
  5. 取前 CLUSTERS_PER_PASS 个 cluster 调 LLM
  6. action 应用委托给 manager（manager 内 lock + apply + stamp + save）

Embedding 不可用 / 候选不足 → 整 pass no-op，不报错。
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta
from typing import Awaitable, Callable

from config import (
    MEMORY_LLM_HARD_TIMEOUT_SECONDS,
    MEMORY_REFINE_CLUSTER_SIZE_MAX,
    MEMORY_REFINE_CLUSTERS_PER_PASS,
    MEMORY_REFINE_COSINE_THRESHOLD,
    MEMORY_REFINE_REVISIT_AFTER_DAYS,
    MEMORY_REFINE_TOPK_PER_ENTRY,
)
try:
    from memory.embeddings import (
        decode_embedding,
        get_embedding_service,
        is_cached_embedding_valid,
        parse_dim_from_model_id,
    )
except ImportError:
    # See ``embedding_worker`` for context. With the disabled-service
    # stub, ``MemoryRefineEngine`` sees ``is_available() == False`` on
    # every pass and short-circuits the cluster scan — the module
    # docstring already calls out the "embedding unavailable → no-op"
    # contract.
    from memory.embeddings_fallback import (
        decode_embedding,
        get_embedding_service,
        is_cached_embedding_valid,
        parse_dim_from_model_id,
        _warn_once,
    )
    _warn_once(__name__)
from utils.logger_config import get_module_logger
from utils.token_tracker import set_call_type

logger = get_module_logger(__name__, "Memory")


# Internal markers used to annotate cluster member dicts during processing.
# Stripped before any disk write (managers should not persist these keys).
REFINE_TYPE_KEY = '_refine_type'      # 'persona' | 'reflection' | 'fact'
REFINE_ENTITY_KEY = '_refine_entity'  # 'master' | 'neko' | 'relationship'

# 四件套合法 action set；apply 层用这个集合 reject 越界 action。
VALID_REFINE_ACTIONS = frozenset({'split', 'merge', 'modify', 'discard'})


# ── Annotation helpers (manager-side use) ─────────────────────────────


def annotate_entry(entry: dict, *, type_: str, entity: str) -> dict:
    """Return a copy of ``entry`` with refine metadata attached. Callers
    that build candidate pools use this to tag each row with its type
    (persona / reflection / fact) and entity slice so downstream code
    (cluster + hash + render) can branch without re-deriving them."""
    copy = dict(entry)
    copy[REFINE_TYPE_KEY] = type_
    copy[REFINE_ENTITY_KEY] = entity
    return copy


def strip_refine_metadata(entry: dict) -> dict:
    """Return a copy with refine internal markers removed. Manager apply
    paths use this just before persisting produced entries."""
    copy = dict(entry)
    copy.pop(REFINE_TYPE_KEY, None)
    copy.pop(REFINE_ENTITY_KEY, None)
    return copy


# ── Public engine class ───────────────────────────────────────────────


# Manager-supplied apply callback signature.
# Args: cluster (annotated entries), actions (parsed LLM JSON), cluster_hash.
# Returns: set of entry ids that survived (kept or produced) so the engine
# can confirm the action ran. Manager is responsible for lock / save /
# stamp internally; engine doesn't touch storage.
ApplyFn = Callable[[list[dict], list[dict], str], Awaitable[set[str]]]

# Manager-supplied failure callback signature.
# Args: cluster (annotated entries), cluster_hash.
# Triggered when ``_resolve_cluster`` returns False（LLM 输出空 / parse 失败 /
# 非 list）**或**抛异常（LLM 超时 / apply_fn 持久化异常等）。先前只在 False
# 时调、异常按 transient 不计（Codex P1 round-3 on PR #1412），但持续性故障
# （模型快照下线一直超时）那样会变成每 30min 无限重打；现在异常也计入预算。
# Manager bumps ``refine_attempts`` on each non-fact cluster member (and
# saves persona/reflection file), so the next refine pass can filter
# out entries that have repeatedly failed (Site 4 liveness 兜底)。
FailureFn = Callable[[list[dict], str], Awaitable[None]]


class MemoryRefineEngine:
    """Stateless apart from the embedding service handle and config
    manager. Construct once per refine cron and call refine_persona_pass
    / refine_reflection_pass per character per pass."""

    def __init__(self, config_manager):
        self._cm = config_manager
        self._service = get_embedding_service()

    # ── public entrypoints ──────────────────────────────────────────

    async def refine_pass(
        self,
        candidates_by_entity: dict[str, list[dict]],
        *,
        apply_fn: ApplyFn,
        scope_label: str,  # for logging: "persona/character" etc.
        failure_fn: FailureFn | None = None,
    ) -> dict:
        """通用 pass：候选已按 entity 切片（每条带 annotate_entry 的标签），
        engine 跑 cluster + hash skip + ranking + LLM + apply。

        Returns: {'clusters_seen', 'clusters_skipped', 'clusters_resolved',
                  'clusters_failed'}.

        Embedding 不可用 → 返回零计数，no-op。

        ``failure_fn``: 可选回调，在 ``_resolve_cluster`` 返 False（LLM 输出
        空 / parse 失败 / 非 list）**或** 抛异常（LLM 超时 / apply_fn 持久化
        失败等）时都调用，传入 ``(cluster, cluster_hash)``。Manager 在回调里
        bump ``refine_attempts``，达 N 次后下次候选 gather 把成员过滤掉。

        为什么异常路径也计数（修正先前 Codex P1 round-3 on PR #1412 的设计）：
        原本把异常按"瞬态、不计数"处理，怕单次网络/IO 抖动冤枉具体 entry。
        但当"瞬态"其实是**持续性**的——correction 模型快照下线一直超时、
        cloudsave 卡维护态、FS 只读——这条不计数路径就变成无限重试风暴，
        每 30min 把同一个毒 cluster 原样重打 LLM 永不放弃。N=
        ``MEMORY_LIVENESS_MAX_ATTEMPTS`` 的预算足够跨过偶发抖动（要连续
        失败才 dead-letter），且 cluster 内容一变 hash 就变、attempts 随
        新成员天然复位，所以持续故障收敛、偶发抖动无损。
        """
        zero = {
            'clusters_seen': 0,
            'clusters_skipped': 0,
            'clusters_resolved': 0,
            'clusters_failed': 0,
        }
        if self._service.is_disabled():
            return zero
        if not candidates_by_entity:
            return zero

        # Build all clusters across entities.
        all_clusters: list[tuple[str, list[dict]]] = []
        for entity, entries in candidates_by_entity.items():
            for cluster in self._compute_clusters(entries):
                all_clusters.append((entity, cluster))

        if not all_clusters:
            return zero

        # Hash-skip filter.
        active: list[tuple[str, list[dict], str]] = []
        skipped = 0
        for entity, cluster in all_clusters:
            cluster_hash = self._cluster_hash(cluster)
            if self._all_stamped_fresh(cluster, cluster_hash):
                skipped += 1
                continue
            active.append((entity, cluster, cluster_hash))

        if not active:
            return {
                'clusters_seen': len(all_clusters),
                'clusters_skipped': skipped,
                'clusters_resolved': 0,
                'clusters_failed': 0,
            }

        # Starvation-first ordering (smallest last_refine_at first).
        active.sort(key=lambda t: self._cluster_starvation_key(t[1]))

        to_process = active[:MEMORY_REFINE_CLUSTERS_PER_PASS]
        resolved = 0
        failed = 0
        for entity, cluster, cluster_hash in to_process:
            # _resolve_cluster 返 False（LLM 输出空 / parse 失败 / 非 list）
            # 或抛异常（LLM 超时 / apply_fn 持久化失败）都 bump refine_attempts。
            # 持续性故障必须计入预算才能 dead-letter（见函数 docstring）；偶发
            # 抖动靠 N 次预算 + cluster 内容变即复位兜住，不会冤枉。
            cluster_failed = False
            try:
                ok = await self._resolve_cluster(
                    entity, cluster, cluster_hash, apply_fn,
                )
                if ok:
                    resolved += 1
                else:
                    failed += 1
                    cluster_failed = True
            except Exception as e:  # noqa: BLE001 — refine is best-effort
                failed += 1
                cluster_failed = True
                logger.warning(
                    f"[Refine] {scope_label} cluster {cluster_hash} 异常"
                    f"（计入 refine_attempts）: {e}"
                )
            if cluster_failed and failure_fn is not None:
                try:
                    await failure_fn(cluster, cluster_hash)
                except Exception as fe:  # noqa: BLE001 — failure_fn 是兜底，自己再失败也不该挂主路径
                    logger.warning(
                        f"[Refine] {scope_label} cluster {cluster_hash} "
                        f"failure_fn 异常: {fe}"
                    )
        return {
            'clusters_seen': len(all_clusters),
            'clusters_skipped': skipped,
            'clusters_resolved': resolved,
            'clusters_failed': failed,
        }

    # ── cluster algorithm ───────────────────────────────────────────

    def _compute_clusters(self, entries: list[dict]) -> list[list[dict]]:
        """Same-entity cosine adjacency → connected components.

        Double cap: edges where cosine ≥ MEMORY_REFINE_COSINE_THRESHOLD,
        and each entry retains at most MEMORY_REFINE_TOPK_PER_ENTRY edges
        (strongest first). Clusters larger than MEMORY_REFINE_CLUSTER_SIZE_MAX
        are truncated by per-member max-cosine strength. Singletons dropped.
        """
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
        # Vectors are L2-normalized by the embedding service, so dot
        # product == cosine. Skip self-similarity by zeroing the diagonal.
        sim_matrix = matrix @ matrix.T
        np.fill_diagonal(sim_matrix, -1.0)

        threshold = MEMORY_REFINE_COSINE_THRESHOLD
        topk = MEMORY_REFINE_TOPK_PER_ENTRY
        n = len(valid)

        # Per-entry top-K neighbor edges above threshold.
        adj: list[list[int]] = [[] for _ in range(n)]
        for i in range(n):
            row = sim_matrix[i]
            # gather candidates above threshold
            cand = [(int(j), float(row[j])) for j in range(n) if float(row[j]) >= threshold]
            cand.sort(key=lambda x: -x[1])
            adj[i] = [j for j, _ in cand[:topk]]

        # Union-find for connected components.
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
            if len(indices) > MEMORY_REFINE_CLUSTER_SIZE_MAX:
                # Truncate by per-member max cosine within the cluster.
                # Members with the weakest internal pull drop first.
                strengths = []
                for i in indices:
                    others = [j for j in indices if j != i]
                    max_sim = max((float(sim_matrix[i][j]) for j in others), default=-1.0)
                    strengths.append((i, max_sim))
                strengths.sort(key=lambda x: -x[1])
                indices = [s[0] for s in strengths[:MEMORY_REFINE_CLUSTER_SIZE_MAX]]
            clusters.append([valid[i] for i in indices])

        return clusters

    # ── cluster_hash + skip ─────────────────────────────────────────

    @staticmethod
    def _cluster_hash(cluster: list[dict]) -> str:
        """sha1(sorted member ids). fact entries excluded — they're
        immutable info sources and shouldn't invalidate the hash by
        moving in/out of clusters."""
        ids = sorted(
            str(e.get('id', '')) for e in cluster
            if e.get(REFINE_TYPE_KEY) != 'fact' and e.get('id')
        )
        return hashlib.sha1('|'.join(ids).encode('utf-8')).hexdigest()[:16]

    @staticmethod
    def _all_stamped_fresh(cluster: list[dict], cluster_hash: str) -> bool:
        """True ⇔ every non-fact member stamped on this exact cluster_hash
        within REVISIT_AFTER_DAYS. Any None / stale / mismatch ⇒ False
        ⇒ cluster goes back through LLM."""
        cutoff = datetime.now() - timedelta(days=MEMORY_REFINE_REVISIT_AFTER_DAYS)
        for e in cluster:
            if e.get(REFINE_TYPE_KEY) == 'fact':
                continue
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
        """Cluster sort key: smallest non-fact last_refine_at first.
        Empty string sorts before any ISO timestamp, so unstamped members
        push the cluster to the front of the queue."""
        timestamps = [
            (e.get('last_refine_at') or '')
            for e in cluster
            if e.get(REFINE_TYPE_KEY) != 'fact'
        ]
        return min(timestamps) if timestamps else ''

    # ── LLM call + parse + delegate ────────────────────────────────

    async def _resolve_cluster(
        self,
        entity: str,
        cluster: list[dict],
        cluster_hash: str,
        apply_fn: ApplyFn,
    ) -> bool:
        """Render cluster → call LLM → parse JSON → hand off to apply_fn.
        Returns True if the apply call ran (even if it produced 0
        surviving changes); False on any prep / LLM / parse failure."""
        cluster_text = self._render_cluster(cluster)
        if not cluster_text:
            return False

        from config.prompts.prompts_memory import get_memory_refine_prompt
        from utils.language_utils import get_global_language
        from utils.llm_client import create_chat_llm

        template = get_memory_refine_prompt(get_global_language())
        prompt = (
            template
            .replace('{ENTITY}', entity)
            .replace('{CLUSTER}', cluster_text)
        )

        # refine 跟 persona correction 同性质：后果不可逆（split/merge/
        # modify/discard 直接改 persona/reflection 写盘），值得用 correction
        # tier + thinking + 长 timeout。对齐 PersonaManager.resolve_corrections
        # 的调用配置。
        set_call_type("memory_refine")
        api_config = self._cm.get_model_api_config('correction')
        llm = create_chat_llm(
            api_config['model'],
            api_config['base_url'],
            api_config['api_key'],
            timeout=MEMORY_LLM_HARD_TIMEOUT_SECONDS,
            max_retries=0,
            extra_body=None,  # 显式开 thinking（同 correction）
        )
        try:
            resp = await llm.ainvoke(prompt)
        finally:
            await llm.aclose()

        raw = (resp.content or "").strip()
        if raw.startswith("```"):
            raw = raw.replace("```json", "").replace("```", "").strip()
        if not raw:
            logger.warning(f"[Refine] LLM 返回空 (cluster_hash={cluster_hash})")
            return False
        try:
            actions = json.loads(raw)
        except json.JSONDecodeError as e:
            logger.warning(
                f"[Refine] LLM JSON 解析失败 (cluster_hash={cluster_hash}): "
                f"{e}; raw[:200]={raw[:200]}"
            )
            return False
        if not isinstance(actions, list):
            logger.warning(
                f"[Refine] LLM 输出非 list (cluster_hash={cluster_hash}): {type(actions)}"
            )
            return False

        # Manager owns lock / apply / stamp / save.
        await apply_fn(cluster, actions, cluster_hash)
        return True

    @staticmethod
    def _render_cluster(cluster: list[dict]) -> str:
        """Render cluster members as numbered lines for the LLM prompt.

        Each line carries enough metadata for the LLM to choose actions:
        - persona: just id + text
        - reflection: id + relation_type + temporal_scope + text
        - fact: id + importance + text (the prompt already states fact
          is read-only, no explicit marker added per design)
        """
        lines = []
        for i, e in enumerate(cluster):
            etype = e.get(REFINE_TYPE_KEY, 'unknown')
            text = e.get('text', '')
            eid = e.get('id', '')
            if not text or not eid:
                continue
            if etype == 'fact':
                imp = e.get('importance', 5)
                lines.append(f"[{i}] (fact id={eid}, importance={imp}) {text}")
            elif etype == 'reflection':
                rt = e.get('relation_type') or 'uncategorized'
                ts = e.get('temporal_scope') or 'unknown'
                lines.append(
                    f"[{i}] (reflection id={eid}, relation_type={rt}, "
                    f"temporal_scope={ts}) {text}"
                )
            else:  # persona
                lines.append(f"[{i}] (persona id={eid}) {text}")
        return "\n".join(lines)
