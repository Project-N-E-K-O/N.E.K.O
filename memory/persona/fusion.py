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
  Phase 1 (locked)  : snapshot same-source entries + compute fingerprint; on a hit, idempotent skip
  Phase 2 (unlocked): run the fusion LLM (tens of seconds)
  Phase 3 (locked)  : CAS-evict old same-source entries + write the fused result, atomic save

IDEMPOTENCY
-----------
LLM fusion is non-deterministic; re-importing the same workspace must not grow the
pool with every import. A stable fingerprint over the entity's candidate set is
stored on the produced entries; on re-import, same fingerprint -> skip the whole
batch without calling the LLM (strict idempotent no-op), changed fingerprint ->
evict the old same-source entries and re-fuse from the original candidates (the
entry count is pinned by the source's upper bound, so only the wording changes, it
does not accumulate). Eviction only touches ``source == 'external_import'``
entries -- protected (character card) / conversational accumulation / reflection
are all left untouched.
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
    the user can retry; retries are idempotent (same fingerprint -> skip / changed
    -> replace-then-fuse). Dual to ``FactExtractionFailed``: it distinguishes a
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
        """Fuse one entity's external-import candidates into persona (3-phase).

        Returns ``{'added': int, 'skipped': int, 'fused': bool}``.
        Raises ``ExternalMemoryFusionError`` on terminal LLM / parse failure.
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

        # ── Phase 1 (locked): 快照同源条目 + 幂等指纹判定 ──
        async with self._get_alock(name):
            persona = await self._aensure_persona_locked(name)
            existing_external = [
                f for f in self._get_section_facts(persona, entity)
                if isinstance(f, dict) and f.get("source") == "external_import"
            ]
            if existing_external and all(
                (f.get("external_import") or {}).get("fusion_fingerprint") == fingerprint
                for f in existing_external
            ):
                # 同源未变 → 严格幂等 no-op，不进 LLM
                return {"added": 0, "skipped": len(cands), "fused": False}

        # ── Phase 2 (unlocked): 跑融合 LLM（几十秒，绝不持锁）──
        fused = await self._allm_call_fusion(name, entity, cands, budget)
        if fused is None:
            raise ExternalMemoryFusionError(f"persona fusion LLM failed: {name}/{entity}")
        fused = self._trim_fused_to_budget(fused, budget)
        if not fused:
            # LLM 成功但融合出 0 条 —— 视为可重试的终态失败，不静默丢用户数据
            raise ExternalMemoryFusionError(
                f"persona fusion produced no entries: {name}/{entity}"
            )

        # ── Phase 3 (locked): CAS 剔旧同源 + 写融合结果，原子 save ──
        imported_at = datetime.now().astimezone().isoformat()
        source_files = sorted(
            {str(c.get("source_file") or "") for c in cands if c.get("source_file")}
        )
        metadata = {
            "format": source_format,
            "files": source_files,
            "section": "fused",
            "fusion_fingerprint": fingerprint,
            "imported_at": imported_at,
            "fused": True,
        }
        source_id = f"{source_format}:fusion:{entity}"
        async with self._get_alock(name):
            persona = await self._aensure_persona_locked(name)
            section_facts = self._get_section_facts(persona, entity)
            # 剔旧与写新在同一临界区原子完成——杜绝「剔除后崩溃」把同源条目清空却
            # 没补回。只剔 external_import 同源，protected / 对话积累 / reflection 不动。
            section_facts[:] = [
                f for f in section_facts
                if not (isinstance(f, dict) and f.get("source") == "external_import")
            ]
            added = 0
            for item in fused:
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
        try:
            # noqa 理由：cand_text 已 truncate_to_tokens 到 EXTERNAL_IMPORT_FUSION_INPUT_MAX_TOKENS
            resp = await llm.ainvoke(prompt)  # noqa: LLM_INPUT_BUDGET
        except Exception as exc:
            logger.warning(f"[PersonaFusion] {name}/{entity} 融合 LLM 调用失败: {exc}")
            return None
        finally:
            await llm.aclose()

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
        drop the whole batch.
        """
        ordered = sorted(fused, key=lambda x: x.get("importance", 5), reverse=True)
        kept: list[dict] = []
        total = 0
        for item in ordered:
            text = truncate_to_tokens(item["text"], EXTERNAL_IMPORT_FUSION_ENTRY_MAX_TOKENS)
            if not text:
                continue
            t = count_tokens(text)
            if kept and total + t > budget:
                break
            kept.append({"text": text, "importance": item["importance"]})
            total += t
        return kept
