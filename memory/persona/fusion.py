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
"""External-memory LLM fusion for persona import.

WHY THIS EXISTS
---------------
When persona is rendered into the system prompt there is a **strict token
ceiling** (``PERSONA_RENDER_MAX_TOKENS``, shared by all non-protected entries in
one pool). But OpenClaw / Hermes ``USER.md`` / ``SOUL.md`` are dozens of lines of
free-form Markdown -- appending them entry-by-entry after exact dedup (as the old
``aimport_external_facts`` did) would quickly overflow the persona pool and crowd
out the impressions the character has naturally accumulated in conversation. So
``USER.md`` / ``SOUL.md`` must first go through one LLM fusion (summarise / merge /
dedupe / disambiguate / rank by importance) that compresses the content into the
per-entity budget (``EXTERNAL_IMPORT_PERSONA_{NEKO,MASTER}_MAX_TOKENS``) before it
is persisted as non-protected persona entries.

LOCK DISCIPLINE
---------------
Fusion calls an LLM (tens of seconds). The persona per-character lock is **also**
held while ``/new_dialog`` assembles a live prompt, so the LLM must never run
inside the lock -- otherwise the character cannot reply during fusion. This
follows the three-phase shape of ``promotion_merge``:
  Phase 1 (locked)  : snapshot the external-import bucket + folded-fingerprint idempotency check
  Phase 2 (unlocked): fuse the existing bucket + the new candidates (LLM merges/dedupes across sources)
  Phase 3 (locked)  : CAS on the folded set, then evict + rewrite the bucket, atomic save

IDEMPOTENCY & MULTI-SOURCE ACCUMULATION
---------------------------------------
LLM fusion is non-deterministic; re-importing the same workspace must not grow the
pool with every import. Each import computes a stable fingerprint over its own
candidate set, and every produced entry carries a ``folded_fingerprints`` set
naming the source versions already folded into the bucket. On re-import:

* fingerprint already in the folded set -> strict idempotent no-op (LLM not called);
* new fingerprint -> **fold**: re-fuse (existing bucket entries + new candidates)
  into a fresh digest, so a second source (e.g. OpenClaw then Hermes) accumulates
  instead of clobbering, and cross-source duplicates are merged by the same fusion
  LLM. The digest is trimmed back to the per-entity budget, so accumulation stays
  bounded.

A merged digest loses per-source attribution, so re-importing an *edited* version of
an already-folded source cannot precisely retract its old contribution -- the fusion
LLM reconciles overlaps, but a contradictory edit may leave residue (an accepted
trade-off). Everything is keyed on ``source == 'external_import'`` only -- protected
(character card) / conversational accumulation / reflection entries are never folded
or evicted. Phase 3 re-checks the folded set (CAS) so a concurrent import cannot be
silently clobbered.
"""
from __future__ import annotations

import hashlib
import re
from datetime import datetime

from config import (
    EXTERNAL_IMPORT_FUSION_ENTRY_MAX_TOKENS,
    EXTERNAL_IMPORT_FUSION_INPUT_MAX_TOKENS,
    EXTERNAL_IMPORT_PERSONA_MASTER_MAX_TOKENS,
    EXTERNAL_IMPORT_PERSONA_NEKO_MAX_TOKENS,
    LLM_OUTPUT_GUARD_MAX_TOKENS,
    MEMORY_LLM_HARD_TIMEOUT_SECONDS,
)
from config.prompts.prompts_memory import (
    get_persona_fusion_entity_label,
    get_persona_fusion_prompt,
)
from memory.evidence import initial_reinforcement_from_importance
from utils.file_utils import robust_json_loads
from utils.language_utils import get_global_language
from utils.token_tracker import set_call_type
from utils.tokenize import count_tokens, truncate_to_tokens

from ._shared import logger

# 只有 master(USER.md) / neko(SOUL.md) 走外部融合。relationship 不产 persona
# 分支（见 memory/external_markdown_import.py 的分类），不在此表即跳过。
_ENTITY_BUDGET = {
    "master": EXTERNAL_IMPORT_PERSONA_MASTER_MAX_TOKENS,
    "neko": EXTERNAL_IMPORT_PERSONA_NEKO_MAX_TOKENS,
}

_JSON_FENCE_RE = re.compile(r"^```(?:json)?\s*|\s*```$", re.IGNORECASE)


class ExternalMemoryFusionError(RuntimeError):
    """Terminal failure of the fusion LLM (call failed / parse failed / produced 0 entries).

    The caller **must not** fall back to per-entry appends (that would bypass the
    token budget and overflow the pool) -- the original import material is kept so
    the user can retry; retries are idempotent (same fingerprint -> skip / new
    fingerprint -> re-fold). Dual to ``FactExtractionFailed``: it distinguishes a
    "retriable terminal failure" from "succeeded but empty".
    """


class ExternalFusionMixin:
    """PersonaManager mixin: fuse external-import material via an LLM, then persist into persona.

    Dual to ``RefinementMixin`` / ``CorrectionsMixin`` -- all three are persona LLM
    write paths, each living in its own mixin file.
    """

    async def afuse_external_facts(
        self, name: str, entity: str, candidates: list[dict], source_format: str,
    ) -> dict:
        """Fold one entity's external-import candidates into persona (3-phase).

        The existing external-import bucket and the new candidates are fused
        together, so a second source accumulates (and cross-source duplicates
        merge) instead of clobbering the first -- see the module IDEMPOTENCY note.

        Returns ``{'added': int, 'skipped': int, 'fused': bool}`` (``added`` is the
        size of the rewritten bucket). Raises ``ExternalMemoryFusionError`` on
        terminal LLM / parse failure.
        """
        entity = str(entity or "master")
        budget = _ENTITY_BUDGET.get(entity)
        cands = [
            c for c in (candidates or [])
            if isinstance(c, dict) and str(c.get("text") or "").strip()
        ]
        if budget is None or not cands:
            return {"added": 0, "skipped": len(cands), "fused": False}

        fingerprint = self._fusion_fingerprint(cands)

        # ── Phase 1 (locked): 快照外部导入桶 + folded 指纹幂等判定 ──
        async with self._get_alock(name):
            persona = await self._aensure_persona_locked(name)
            existing_external = [
                f for f in self._get_section_facts(persona, entity)
                if isinstance(f, dict) and f.get("source") == "external_import"
            ]
            base_folded = self._collect_folded_fingerprints(existing_external)
            if fingerprint in base_folded:
                # 该源此版本已折叠进桶 → 严格幂等 no-op，不进 LLM
                return {"added": 0, "skipped": len(cands), "fused": False}
            existing_texts = [
                str(f.get("text") or "").strip()
                for f in existing_external
                if str(f.get("text") or "").strip()
            ]

        # ── Phase 2 (unlocked): 融合「已存桶 + 新候选」（几十秒，绝不持锁）──
        # 已存桶放前面：_allm_call_fusion 按 token 上限从**尾部**截断，把已累积的多源
        # digest 放头部保证不被截掉——否则一次超大新导入会把旧源挤出融合输入，而
        # Phase 3 又会清空整个桶、按只见过新源的 LLM 输出重写，静默抹掉旧源（Codex
        # P2）。已存桶被 per-entity 预算钉死(≤budget≪输入上限)，恒有余量给新候选；
        # 真溢出时被截的是本次新素材尾部，而非已累积状态。
        fold_input = [{"text": t} for t in existing_texts] + list(cands)
        fused = await self._allm_call_fusion(name, entity, fold_input, budget)
        if fused is None:
            raise ExternalMemoryFusionError(f"persona fusion LLM failed: {name}/{entity}")
        fused = self._trim_fused_to_budget(fused, budget)
        if not fused:
            # LLM 成功但融合出 0 条 —— 视为可重试的终态失败，不静默丢用户数据
            raise ExternalMemoryFusionError(
                f"persona fusion produced no entries: {name}/{entity}"
            )

        # ── Phase 3 (locked): CAS folded 集合 + 剔旧桶 + 写新 digest，原子 save ──
        imported_at = datetime.now().astimezone().isoformat()
        new_folded = sorted(base_folded | {fingerprint})
        source_files = sorted(
            {str(c.get("source_file") or "") for c in cands if c.get("source_file")}
        )
        metadata = {
            "format": source_format,
            "files": source_files,
            "section": "fused",
            "fusion_fingerprint": fingerprint,
            "folded_fingerprints": new_folded,
            "imported_at": imported_at,
            "fused": True,
        }
        source_id = f"{source_format}:fusion:{entity}"
        async with self._get_alock(name):
            persona = await self._aensure_persona_locked(name)
            section_facts = self._get_section_facts(persona, entity)
            current_external = [
                f for f in section_facts
                if isinstance(f, dict) and f.get("source") == "external_import"
            ]
            # CAS：Phase 2 基于 base_folded 快照融合。若期间并发导入改了外部桶
            # （folded 集合变了），本次结果陈旧（会丢对方叠加的源）→ 抛可重试错误，
            # 绝不静默覆盖。同源重导仍走上面的幂等 skip。
            if self._collect_folded_fingerprints(current_external) != base_folded:
                raise ExternalMemoryFusionError(
                    f"persona fusion base changed under concurrent import: {name}/{entity}"
                )
            # 剔旧与写新在同一临界区原子完成。只剔 external_import 桶，protected /
            # 对话积累 / reflection 不动（旧桶内容已折叠进新 digest）。
            section_facts[:] = [
                f for f in section_facts
                if not (isinstance(f, dict) and f.get("source") == "external_import")
            ]
            # 与角色卡（protected character_card）矛盾的融合条目无条件丢弃——外部导入
            # 不得架空角色卡；复用 aadd_fact 同一道轻量守卫（Codex P2）。
            stop_names = await self._aget_entity_stop_names(name)
            added = 0
            for item in fused:
                code, _ = self._evaluate_fact_contradiction(
                    name, item["text"], section_facts, stop_names,
                )
                if code == self.FACT_REJECTED_CARD:
                    continue
                entry = self._build_fact_entry(item["text"], "external_import", source_id)
                entry["reinforcement"] = initial_reinforcement_from_importance(item["importance"])
                entry["external_import"] = dict(metadata)
                section_facts.append(entry)
                added += 1
            await self.asave_persona(name, persona)
        return {"added": added, "skipped": 0, "fused": True}

    # ── helpers ──────────────────────────────────────────────────────

    @staticmethod
    def _fusion_fingerprint(candidates: list[dict]) -> str:
        """Stable, order-independent fingerprint over the entity's candidate texts."""
        norm = sorted(
            " ".join(str(c.get("text") or "").casefold().split()) for c in candidates
        )
        return hashlib.sha256("\n".join(norm).encode("utf-8")).hexdigest()

    @staticmethod
    def _collect_folded_fingerprints(entries: list[dict]) -> set[str]:
        """Union of source fingerprints already folded into these external entries.

        Reads each entry's ``external_import.folded_fingerprints`` list, falling
        back to the legacy single ``fusion_fingerprint`` for entries written before
        multi-source folding. Empty set for a fresh (never-imported) bucket.
        """
        folded: set[str] = set()
        for f in entries:
            if not isinstance(f, dict):
                continue
            meta = f.get("external_import") or {}
            fps = meta.get("folded_fingerprints")
            if isinstance(fps, list):
                folded.update(str(x) for x in fps if x)
            legacy = meta.get("fusion_fingerprint")
            if legacy:
                folded.add(str(legacy))
        return folded

    async def _allm_call_fusion(
        self, name: str, entity: str, candidates: list[dict], budget: int,
    ) -> list[dict] | None:
        """Run the fusion LLM (UNLOCKED). Returns parsed ``[{text, importance}]`` or None.

        None = call/parse failure (caller raises ExternalMemoryFusionError). Mirrors
        the reflection promote-merge LLM shape: correction tier + thinking + single
        shot (max_retries=0) + robust JSON parse.
        """
        from utils.llm_client import create_chat_llm_async

        lang = get_global_language()
        # names（避免物化：master 缺名用中性占位，不抄 rendering 的 '主人' 兜底）
        try:
            _, _, _, _, name_mapping, _, _, _, _ = await self._config_manager.aget_character_data()
        except Exception:
            name_mapping = {}
        ai_name = name
        master_name = (name_mapping or {}).get("human") or "用户"
        entity_label = get_persona_fusion_entity_label(entity, lang)

        lines = []
        for idx, cand in enumerate(candidates, 1):
            section = str(cand.get("source_section") or "").strip()
            text = str(cand.get("text") or "").strip()
            prefix = f"{section}: " if section and section.casefold() not in text.casefold() else ""
            lines.append(f"{idx}. {prefix}{text}")
        cand_text = truncate_to_tokens("\n".join(lines), EXTERNAL_IMPORT_FUSION_INPUT_MAX_TOKENS)

        prompt = get_persona_fusion_prompt(lang).format(
            AI_NAME=ai_name,
            MASTER_NAME=master_name,
            ENTITY_LABEL=entity_label,
            TOKEN_BUDGET=budget,
            CANDIDATES=cand_text,
        )

        set_call_type("persona_external_fusion")
        # 客户端构造（含 correction 模型配置读取）也可能抛（配置非法等）；必须与
        # ainvoke 一样收敛成 None，否则异常绕过 ExternalMemoryFusionError——在第二个
        # entity 上（第一个已落盘）会让端点返 generic 500 而非 external_import_partial，
        # 丢掉前端重试所需的 partial 元数据（Codex P2）。
        try:
            api_config = self._config_manager.get_model_api_config("correction")
            llm = await create_chat_llm_async(
                api_config["model"],
                api_config["base_url"],
                api_config["api_key"],
                timeout=MEMORY_LLM_HARD_TIMEOUT_SECONDS,
                max_retries=0,
                max_completion_tokens=LLM_OUTPUT_GUARD_MAX_TOKENS,
                extra_body=None,
                provider_type=api_config.get("provider_type"),
            )
        except Exception as exc:
            logger.warning(f"[PersonaFusion] {name}/{entity} 融合 LLM 构造失败: {exc}")
            return None
        try:
            # noqa 理由：cand_text 已 truncate_to_tokens 到 EXTERNAL_IMPORT_FUSION_INPUT_MAX_TOKENS
            resp = await llm.ainvoke(prompt)  # noqa: LLM_INPUT_BUDGET
        except Exception as exc:
            logger.warning(f"[PersonaFusion] {name}/{entity} 融合 LLM 调用失败: {exc}")
            return None
        finally:
            # aclose is cleanup: a close failure must not mask the call outcome —
            # a finally exception would replace the return None / valid resp and
            # propagate past ExternalMemoryFusionError into a generic 500 (Codex P2).
            try:
                await llm.aclose()
            except Exception as exc:
                logger.warning(f"[PersonaFusion] {name}/{entity} 融合 LLM 关闭失败: {exc}")

        raw = resp.content if hasattr(resp, "content") else str(resp)
        return self._parse_fusion_response(raw)

    @staticmethod
    def _parse_fusion_response(raw: str) -> list[dict] | None:
        """Parse LLM output → ``[{text: str, importance: int(1..10)}]`` or None."""
        if not isinstance(raw, str):
            return None
        stripped = _JSON_FENCE_RE.sub("", raw.strip()).strip()
        try:
            data = robust_json_loads(stripped)
        except Exception:
            return None
        if not isinstance(data, list):
            return None
        out: list[dict] = []
        for item in data:
            if not isinstance(item, dict):
                continue
            text = str(item.get("text") or "").strip()
            if not text:
                continue
            try:
                importance = int(item.get("importance", 5))
            except (TypeError, ValueError):
                importance = 5
            out.append({"text": text, "importance": max(1, min(10, importance))})
        return out

    @staticmethod
    def _trim_fused_to_budget(fused: list[dict], budget: int) -> list[dict]:
        """Sort by importance desc, per-entry soft-cap, greedily accumulate to budget.

        Whole-entry greedy (stop as soon as total+t>budget), matching the rendering
        layer's _ascore_trim_entries. Keeps at least 1 entry so boundary cases don't
        drop the whole batch. Deduplicates by normalized text (keeping the highest
        importance, since sorted desc) so an LLM that repeats a line can't mint two
        entries sharing one timestamp+text-hash id (Codex P2).
        """
        ordered = sorted(fused, key=lambda x: x.get("importance", 5), reverse=True)
        kept: list[dict] = []
        seen: set[str] = set()
        total = 0
        for item in ordered:
            text = truncate_to_tokens(item["text"], EXTERNAL_IMPORT_FUSION_ENTRY_MAX_TOKENS)
            if not text:
                continue
            norm = " ".join(text.casefold().split())
            if norm in seen:
                continue
            t = count_tokens(text)
            if kept and total + t > budget:
                break
            kept.append({"text": text, "importance": item["importance"]})
            seen.add(norm)
            total += t
        return kept
