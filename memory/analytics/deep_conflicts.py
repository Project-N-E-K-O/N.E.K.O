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

"""Manual developer-mode semantic conflict and duplicate audit via correction tier.

LLM discipline:
- Uses ``correction`` tier from ``config_manager.aget_model_api_config('correction')``.
- Never passes ``temperature``.
- Enforces both input token budget (``DEEP_CONFLICT_CHECK_INPUT_MAX_TOKENS``) and
  output budget + timeout (``DEEP_CONFLICT_CHECK_MAX_TOKENS``,
  ``DEEP_CONFLICT_CHECK_TIMEOUT_SECONDS``).
- Strictly manual on-demand trigger; never runs automatically on page open.
"""
from __future__ import annotations

import asyncio
import json
import math
from datetime import datetime
from pathlib import Path
from typing import Any

from config import (
    DEEP_CONFLICT_CHECK_INPUT_MAX_TOKENS,
    DEEP_CONFLICT_CHECK_MAX_TOKENS,
    DEEP_CONFLICT_CHECK_TIMEOUT_SECONDS,
)
from config.prompts.prompts_memory import get_deep_conflict_check_prompt
from utils.config_manager import get_config_manager
from utils.language_utils import get_global_language_full, normalize_language_code
from utils.llm_client import create_chat_llm_async
from utils.logger_config import get_module_logger
from utils.tokenize import count_tokens, truncate_to_tokens
from utils.token_tracker import set_call_type

from .imprint import _CORE_ENTITIES, _META_ENTITY, _read_json_safe, _text_bigrams

try:
    from memory.embeddings import decode_embedding
except ImportError:
    from memory.embeddings_fallback import decode_embedding

logger = get_module_logger(__name__, "Memory")

_MAX_CANDIDATE_PAIRS = 12
_VECTOR_SIM_THRESHOLD = 0.72
_BIGRAM_SIM_THRESHOLD = 0.36
_VALID_VERDICTS = frozenset({"contradiction", "duplicate", "complementary", "unrelated"})


def _cosine_sim(a: list[float], b: list[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na <= 0.0 or nb <= 0.0:
        return 0.0
    return dot / (na * nb)


def _jaccard_sim(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    inter = len(a & b)
    if inter == 0:
        return 0.0
    return inter / len(a | b)


def _collect_candidate_pairs_sync(
    character_name: str,
    *,
    memory_dir: Path,
    max_pairs: int = _MAX_CANDIDATE_PAIRS,
) -> list[dict[str, Any]]:
    char_dir = memory_dir / character_name
    items: list[dict[str, Any]] = []

    persona_raw = _read_json_safe(char_dir / "persona.json", {})
    if isinstance(persona_raw, dict):
        for ent, sec in persona_raw.items():
            if ent == _META_ENTITY or ent not in _CORE_ENTITIES or not isinstance(sec, dict):
                continue
            for entry in sec.get("facts") or []:
                if not isinstance(entry, dict):
                    continue
                if entry.get("protected") or entry.get("source") == "character_card":
                    continue
                text = str(entry.get("text") or "").strip()
                eid = str(entry.get("id") or "").strip()
                if not text or not eid:
                    continue
                vec = None
                emb = entry.get("embedding")
                if emb:
                    try:
                        decoded = decode_embedding(emb)
                        if decoded is not None:
                            vec = [float(v) for v in decoded]
                    except Exception:
                        vec = None
                items.append({
                    "kind": "persona",
                    "entity": ent,
                    "id": eid,
                    "text": text,
                    "vec": vec,
                    "bigrams": _text_bigrams(text),
                })

    reflections_raw = _read_json_safe(char_dir / "reflections.json", [])
    if isinstance(reflections_raw, list):
        for r in reflections_raw:
            if not isinstance(r, dict):
                continue
            status = str(r.get("status") or "pending")
            if status in ("archived", "merged", "denied"):
                continue
            ent = str(r.get("entity") or "master")
            if ent not in _CORE_ENTITIES:
                continue
            text = str(r.get("text") or "").strip()
            rid = str(r.get("id") or "").strip()
            if not text or not rid:
                continue
            vec = None
            emb = r.get("embedding")
            if emb:
                try:
                    decoded = decode_embedding(emb)
                    if decoded is not None:
                        vec = [float(v) for v in decoded]
                except Exception:
                    vec = None
            items.append({
                "kind": "reflection",
                "entity": ent,
                "id": rid,
                "text": text,
                "vec": vec,
                "bigrams": _text_bigrams(text),
            })

    scored: list[tuple[float, dict[str, Any], dict[str, Any]]] = []
    n = len(items)
    for i in range(n):
        a = items[i]
        for j in range(i + 1, n):
            b = items[j]
            if a["entity"] != b["entity"]:
                continue
            sim = 0.0
            if a["vec"] is not None and b["vec"] is not None and len(a["vec"]) == len(b["vec"]):
                sim = _cosine_sim(a["vec"], b["vec"])
                if sim < _VECTOR_SIM_THRESHOLD:
                    continue
            else:
                sim = _jaccard_sim(a["bigrams"], b["bigrams"])
                if sim < _BIGRAM_SIM_THRESHOLD:
                    continue
            scored.append((sim, a, b))

    scored.sort(key=lambda t: -t[0])
    pairs: list[dict[str, Any]] = []
    for idx, (sim, a, b) in enumerate(scored[:max_pairs], start=1):
        pairs.append({
            "pair_id": f"P{idx}",
            "entity": a["entity"],
            "similarity": round(float(sim), 3),
            "left": {"kind": a["kind"], "id": a["id"], "entity": a["entity"], "text": a["text"]},
            "right": {"kind": b["kind"], "id": b["id"], "entity": b["entity"], "text": b["text"]},
        })
    return pairs


def _extract_json_array(raw: str) -> list[dict[str, Any]] | None:
    text = (raw or "").strip()
    if not text:
        return None
    if text.startswith("```"):
        text = text.replace("```json", "").replace("```", "").strip()
    try:
        parsed = json.loads(text)
        if isinstance(parsed, list):
            return [x for x in parsed if isinstance(x, dict)]
        if isinstance(parsed, dict) and isinstance(parsed.get("results"), list):
            return [x for x in parsed["results"] if isinstance(x, dict)]
    except json.JSONDecodeError:
        start = text.find("[")
        end = text.rfind("]")
        if start != -1 and end > start:
            try:
                parsed = json.loads(text[start : end + 1])
                if isinstance(parsed, list):
                    return [x for x in parsed if isinstance(x, dict)]
            except json.JSONDecodeError:
                return None
    return None


async def arun_deep_conflict_check(
    character_name: str,
    *,
    language: str | None = None,
    config_manager=None,
    memory_dir: Path | None = None,
) -> dict[str, Any]:
    """Run an on-demand LLM semantic conflict check on top similar memory pairs."""
    cm = config_manager or get_config_manager()
    resolved_dir = Path(memory_dir) if memory_dir is not None else Path(cm.memory_dir)

    pairs = await asyncio.to_thread(
        _collect_candidate_pairs_sync,
        character_name,
        memory_dir=resolved_dir,
    )
    checked_at = datetime.now().isoformat()
    if not pairs:
        return {
            "character_name": character_name,
            "checked_at": checked_at,
            "checked_pairs": 0,
            "findings": [],
            "summary": {
                "contradiction": 0,
                "duplicate": 0,
                "complementary": 0,
                "unrelated": 0,
            },
        }

    raw_lang = language or get_global_language_full()
    full_lang = normalize_language_code(raw_lang, format="full")
    prompt_lang = "zh-TW" if full_lang == "zh-TW" else normalize_language_code(full_lang, format="short")

    template = get_deep_conflict_check_prompt(prompt_lang)
    shell_tokens = count_tokens(
        template.replace("{CHARACTER_NAME}", str(character_name))
        .replace("{PAIRS}", "")
        .replace("{PAIRS_BLOCK}", "")
    )
    pair_budget = max(256, DEEP_CONFLICT_CHECK_INPUT_MAX_TOKENS - shell_tokens)

    # Allocate budget per complete candidate pair so checked_pairs & findings
    # only account for pairs actually transmitted to the model.
    lines: list[str] = []
    sent_pairs: list[dict[str, Any]] = []
    pair_by_id: dict[str, dict[str, Any]] = {}
    used_tokens = 0
    for p in pairs:
        block = (
            f"[{p['pair_id']}] (entity={p['entity']}, sim={p['similarity']})\n"
            f"  A ({p['left']['kind']}): {p['left']['text']}\n"
            f"  B ({p['right']['kind']}): {p['right']['text']}"
        )
        block_tokens = count_tokens(block)
        if sent_pairs and (used_tokens + block_tokens > pair_budget):
            break
        if not sent_pairs and block_tokens > pair_budget:
            block = truncate_to_tokens(block, pair_budget)
            block_tokens = pair_budget
        lines.append(block)
        sent_pairs.append(p)
        pair_by_id[p["pair_id"]] = p
        used_tokens += block_tokens

    pairs_block = "\n".join(lines)
    prompt = (
        template.replace("{CHARACTER_NAME}", str(character_name))
        .replace("{PAIRS}", pairs_block)
        .replace("{PAIRS_BLOCK}", pairs_block)
    )
    prompt = truncate_to_tokens(prompt, DEEP_CONFLICT_CHECK_INPUT_MAX_TOKENS)

    api_config = await cm.aget_model_api_config("correction")
    set_call_type("memory_deep_conflict_check")
    llm = await create_chat_llm_async(
        api_config["model"],
        api_config["base_url"],
        api_config["api_key"],
        timeout=DEEP_CONFLICT_CHECK_TIMEOUT_SECONDS,
        max_retries=0,
        max_completion_tokens=DEEP_CONFLICT_CHECK_MAX_TOKENS,
        provider_type=api_config.get("provider_type"),
    )
    try:
        resp = await llm.ainvoke(prompt)
    finally:
        await llm.aclose()

    parsed = _extract_json_array(str(getattr(resp, "content", "") or ""))
    if parsed is None:
        raise ValueError("Deep conflict check model returned invalid JSON")

    verdict_by_id: dict[str, dict[str, Any]] = {}
    for row in parsed:
        pid = str(row.get("pair_id") or "").strip()
        if pid in pair_by_id:
            verdict = str(row.get("verdict") or "").strip().lower()
            if verdict not in _VALID_VERDICTS:
                verdict = "unverified"
            verdict_by_id[pid] = {
                "verdict": verdict,
                "reason": str(row.get("reason") or "").strip()[:240],
                "suggestion": str(row.get("suggestion") or "").strip()[:240],
            }

    counts = {k: 0 for k in ("contradiction", "duplicate", "complementary", "unrelated", "unverified")}
    findings: list[dict[str, Any]] = []
    priority = {"contradiction": 0, "duplicate": 1, "complementary": 2, "unverified": 3, "unrelated": 4}
    for p in sent_pairs:
        v = verdict_by_id.get(
            p["pair_id"],
            {"verdict": "unverified", "reason": "", "suggestion": ""},
        )
        counts[v["verdict"]] += 1
        findings.append({
            "pair_id": p["pair_id"],
            "entity": p["entity"],
            "similarity": p["similarity"],
            "verdict": v["verdict"],
            "reason": v["reason"],
            "suggestion": v["suggestion"],
            "left": p["left"],
            "right": p["right"],
        })

    findings.sort(key=lambda item: (priority.get(item["verdict"], 9), -item["similarity"]))
    return {
        "character_name": character_name,
        "checked_at": checked_at,
        "checked_pairs": len(sent_pairs),
        "skipped_pairs": max(0, len(pairs) - len(sent_pairs)),
        "findings": findings,
        "summary": counts,
    }
