# -*- coding: utf-8 -*-
"""
Persona-drift stopgap guards.

  - promotion merge pool: ranked by similarity to R (evidence fallback) before
    the whole-line token cut, so recently appended near-duplicates are seen
  - REFLECTION_PROMPT: concrete-event wording, watermark literals kept,
    existing-impressions slot present in every locale
  - synthesis duplicate check: nearest same-subject impressions shown, a valid
    skip absorbs the batch without a new reflection and without evidence,
    an unknown id falls back to a normal add, no vectors means no change
"""
from __future__ import annotations

import json
import os
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


CHAR = "小天"

_LOCALES = ("zh", "zh-TW", "en", "ja", "ko", "ru", "es", "pt")


# ── fixtures ─────────────────────────────────────────────────────────


def _mock_cm(tmpdir: str):
    cm = MagicMock()
    cm.memory_dir = tmpdir
    character_data = (
        "主人", CHAR, {}, {}, {"human": "主人", "system": "SYS"}, {}, {}, {}, {},
    )
    cm.aget_character_data = AsyncMock(return_value=character_data)
    cm.get_character_data = MagicMock(return_value=character_data)
    api = {"model": "fake-model", "base_url": "http://fake", "api_key": "sk-fake"}
    cm.get_model_api_config = MagicMock(return_value=api)
    cm.aget_model_api_config = AsyncMock(return_value=api)
    return cm


def _install(tmpdir: str):
    from memory.event_log import EventLog
    from memory.facts import FactStore
    from memory.persona import PersonaManager
    from memory.reflection import ReflectionEngine

    cm = _mock_cm(tmpdir)
    with patch("memory.event_log.get_config_manager", return_value=cm), \
         patch("memory.facts.get_config_manager", return_value=cm), \
         patch("memory.persona.manager.get_config_manager", return_value=cm), \
         patch("memory.reflection.manager.get_config_manager", return_value=cm):
        event_log = EventLog()
        event_log._config_manager = cm
        fs = FactStore()
        fs._config_manager = cm
        pm = PersonaManager(event_log=event_log)
        pm._config_manager = cm
        re = ReflectionEngine(fs, pm, event_log=event_log)
        re._config_manager = cm
    return fs, pm, re


_AXES = ("晚安", "咖啡", "猫粮")
_MODEL_ID = "fake-model"


def _vector_for(text: str) -> list[float]:
    """One axis per topic keyword; anything else lands on the spare axis."""
    vec = [0.0] * (len(_AXES) + 1)
    for i, word in enumerate(_AXES):
        if word in text:
            vec[i] = 1.0
    if not any(vec):
        vec[-1] = 1.0
    return vec


def _stamp(entry: dict) -> dict:
    """Give an entry the cache the embedding worker would have written."""
    from memory.embeddings import stamp_embedding_fields

    stamp_embedding_fields(entry, _vector_for(entry["text"]), entry["text"], _MODEL_ID)
    return entry


class _FakeEmbeddingService:
    """Deterministic stand-in for the shared embedding model."""

    def __init__(self, *, available: bool = True):
        self._available = available
        self.embedded: list[str] = []

    def is_disabled(self) -> bool:
        return False

    def is_available(self) -> bool:
        return self._available

    def model_id(self) -> str | None:
        return _MODEL_ID if self._available else None

    async def embed_batch(self, texts, **_kw):
        self.embedded.extend(texts)
        return [_vector_for(t) for t in texts]


def _patch_vectors(service: _FakeEmbeddingService):
    return patch("memory.embeddings.get_embedding_service", return_value=service)


def _persona_entry(eid: str, text: str, *, rein: float = 0.0,
                   protected: bool = False, embedded: bool = True,
                   **extra) -> dict:
    now = datetime.now().isoformat(timespec="seconds")
    entry = {
        "id": eid, "text": text,
        "reinforcement": rein, "disputation": 0.0,
        "rein_last_signal_at": now if rein else None,
        "disp_last_signal_at": None,
        "sub_zero_days": 0, "user_fact_reinforce_count": 0,
        "merged_from_ids": [], "protected": protected,
        "suppress": False, "suppressed_at": None, "recent_mentions": [],
        "source": "character_card" if protected else "reflection",
        "source_id": None,
    }
    entry.update(extra)
    return _stamp(entry) if embedded else entry


def _reflection(rid: str, text: str, *, status: str = "confirmed",
                rein: float = 1.0, entity: str = "relationship",
                embedded: bool = True, **extra) -> dict:
    now = datetime.now().isoformat(timespec="seconds")
    entry = {
        "id": rid, "text": text, "entity": entity, "status": status,
        "source_fact_ids": [], "created_at": now,
        "feedback": None, "next_eligible_at": now,
        "reinforcement": rein, "disputation": 0.0,
        "rein_last_signal_at": now, "disp_last_signal_at": None,
    }
    entry.update(extra)
    return _stamp(entry) if embedded else entry


def _write_json(tmpdir: str, filename: str, data) -> None:
    char_dir = os.path.join(tmpdir, CHAR)
    os.makedirs(char_dir, exist_ok=True)
    with open(os.path.join(char_dir, filename), "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)


def _read_json(tmpdir: str, filename: str):
    with open(os.path.join(tmpdir, CHAR, filename), encoding="utf-8") as f:
        return json.load(f)


def _facts(n: int = 5, *, topic: str = "晚安", **extra) -> list[dict]:
    rows = []
    for i in range(n):
        row = {
            "id": f"f{i}", "text": f"主人第{i}次睡前对小天说{topic}",
            "entity": "relationship", "importance": 7, "absorbed": False,
        }
        row.update(extra)
        rows.append(row)
    return rows


def _llm(answer: dict, captured: list[str]):
    async def _fake(api_config, prompt, **_kw):
        captured.append(prompt)
        return SimpleNamespace(content=json.dumps(answer, ensure_ascii=False)), 0
    return patch("memory.thinking_llm.ainvoke_thinking", _fake)


def _pool_lines(prompt: str) -> list[str]:
    return [
        line for line in prompt.splitlines()
        if line.startswith("[persona.") or line.startswith("[reflection.")
    ]


# ── 1. promotion merge pool ranking ──────────────────────────────────


@pytest.mark.asyncio
async def test_promotion_pool_keeps_newest_similar_entry_under_budget(tmp_path):
    """The near-duplicate appended last must survive the token cut."""
    _fs, _pm, re = _install(str(tmp_path))
    filler = [
        ("relationship", _persona_entry(
            f"old_{i:03d}", f"主人提过一件无关的小事，编号 {i:03d}，内容是天气和路况",
            rein=3.0,
        ))
        for i in range(120)
    ]
    newest = ("relationship", _persona_entry(
        "new_dup", "主人每晚睡前都会对小天说晚安", rein=0.1,
    ))
    R = _reflection("ref_R", "主人睡前总会跟小天道晚安", rein=2.5)
    captured: list[str] = []
    service = _FakeEmbeddingService()
    with _patch_vectors(service), \
         patch("config.PERSONA_MERGE_POOL_MAX_TOKENS", 300), \
         _llm({"action": "promote_fresh"}, captured):
        await re._allm_call_promotion_merge(
            R, filler + [newest], [], CHAR, "主人",
        )
    lines = _pool_lines(captured[0])
    assert lines, "pool rendered no rows"
    assert lines[0].startswith("[persona.relationship.new_dup]")
    # Budget is respected and cut on whole lines only.
    assert len(lines) < len(filler)
    assert all(line.endswith(")") for line in lines)


@pytest.mark.asyncio
@pytest.mark.parametrize("giant_rein", [9.0, 0.5], ids=["ranked_first", "ranked_middle"])
async def test_promotion_pool_skips_a_row_larger_than_the_whole_budget(tmp_path, giant_rein):
    """No truncated copy (merge_into would rewrite the target from it), and an
    oversized row ranked mid-pool must not hide the smaller rows behind it."""
    from utils.tokenize import count_tokens

    _fs, _pm, re = _install(str(tmp_path))
    # Same topic vector as `near`; evidence breaks the tie and sets its rank.
    giant = ("relationship", _persona_entry(
        "giant", "主人睡前说晚安。" + "很长的导入备注" * 400, rein=giant_rein,
    ))
    near = ("relationship", _persona_entry("near", "主人每晚对小天说晚安", rein=1.0))
    other = ("relationship", _persona_entry("other", "主人喜欢咖啡", rein=1.0))
    R = _reflection("ref_R", "主人睡前总会跟小天道晚安", rein=2.5)
    captured: list[str] = []
    with _patch_vectors(_FakeEmbeddingService()), \
         patch("config.PERSONA_MERGE_POOL_MAX_TOKENS", 300), \
         _llm({"action": "promote_fresh"}, captured):
        await re._allm_call_promotion_merge(
            R, [giant, near, other], [], CHAR, "主人",
        )
    lines = _pool_lines(captured[0])
    assert [line.split("]")[0] for line in lines] == [
        "[persona.relationship.near", "[persona.relationship.other",
    ]
    assert count_tokens("\n".join(lines)) <= 300


@pytest.mark.asyncio
async def test_promotion_pool_falls_back_to_evidence_order_without_vectors(tmp_path):
    _fs, _pm, re = _install(str(tmp_path))
    persona_pool = [
        ("master", _persona_entry("p_low", "主人喜欢咖啡", rein=0.5)),
        ("master", _persona_entry("p_high", "主人喜欢猫粮广告", rein=3.0)),
    ]
    reflection_pool = [_reflection("ref_mid", "主人常说晚安", rein=1.5)]
    R = _reflection("ref_R", "主人喜欢喝咖啡", rein=2.5)
    captured: list[str] = []
    with _patch_vectors(_FakeEmbeddingService(available=False)), \
         _llm({"action": "promote_fresh"}, captured):
        await re._allm_call_promotion_merge(
            R, persona_pool, reflection_pool, CHAR, "主人",
        )
    order = [line.split("]")[0] for line in _pool_lines(captured[0])]
    assert order == [
        "[persona.master.p_high", "[reflection.ref_mid", "[persona.master.p_low",
    ]


@pytest.mark.asyncio
async def test_promotion_pool_puts_unembeddable_rows_after_ranked_rows(tmp_path):
    _fs, _pm, re = _install(str(tmp_path))
    service = _FakeEmbeddingService()
    rows = [
        ("master", _persona_entry("p_novec", "还没回填向量的条目", rein=9.0,
                                  embedded=False)),
        ("master", _persona_entry("p_far", "主人喜欢猫粮", rein=5.0)),
        ("master", _persona_entry("p_near", "主人喜欢咖啡", rein=0.2)),
    ]
    R = _reflection("ref_R", "主人每天喝咖啡", rein=2.5)
    with _patch_vectors(service):
        ranked = await re._arank_promotion_pool(R, rows, datetime.now())
    assert [e["id"] for _, e in ranked] == ["p_near", "p_far", "p_novec"]


@pytest.mark.asyncio
async def test_similarity_embeds_queries_but_only_reads_cached_candidates():
    """Candidates never go through the shared model; queries may."""
    from memory.reflection.similarity import ascore_against_queries

    service = _FakeEmbeddingService()
    cached = _stamp({"id": "c", "text": "主人喜欢咖啡"})
    stale = {"id": "n", "text": "主人睡前说晚安"}
    cached_query = _stamp({"id": "q1", "text": "咖啡真香"})
    with _patch_vectors(service):
        scores = await ascore_against_queries(
            [cached_query, {"id": "q2", "text": "又是咖啡"}], [cached, stale],
        )
    assert scores is not None
    assert scores[0] == pytest.approx(1.0) and scores[1] is None
    assert service.embedded == ["又是咖啡"]


# ── 2. reflection prompt wording ─────────────────────────────────────


def test_reflection_prompt_locales_are_concrete_and_keep_watermarks():
    from config.prompts.prompts_memory import (
        REFLECTION_EXISTING_IMPRESSIONS_NOTE,
        REFLECTION_PROMPT,
    )

    assert set(REFLECTION_PROMPT) == set(_LOCALES)
    assert set(REFLECTION_EXISTING_IMPRESSIONS_NOTE) == set(_LOCALES)
    for lang in _LOCALES:
        text = REFLECTION_PROMPT[lang]
        assert "======以下为事实======" in text, lang
        assert "======以上为事实======" in text, lang
        assert "======以上为事实======\n\n{EXISTING_IMPRESSIONS_BLOCK}" in text, lang
        assert "{RELATED_CONTEXT_BLOCK}" in text, lang
        note = REFLECTION_EXISTING_IMPRESSIONS_NOTE[lang]
        assert '"skip": true' in note and '"covered_by"' in note, lang
    # The abstract relationship example that seeded hollow lines is gone.
    assert "两人长期互相依赖" not in REFLECTION_PROMPT["zh"]
    assert "兩人長期互相依賴" not in REFLECTION_PROMPT["zh-TW"]
    assert "long-term mutual reliance" not in REFLECTION_PROMPT["en"]
    assert "高层次的反思洞察" not in REFLECTION_PROMPT["zh"]
    # The pattern tie-break stays.
    assert "拿不准时请倾向选 pattern" in REFLECTION_PROMPT["zh"]
    assert "When unsure, prefer pattern" in REFLECTION_PROMPT["en"]


# ── 3. synthesis duplicate check ─────────────────────────────────────


async def _seed_backoff(re, facts: list[dict]) -> str:
    from memory.reflection import _reflection_id_from_facts

    key = _reflection_id_from_facts(sorted(f["id"] for f in facts))
    await re._abump_synth_backoff(CHAR, key, "seed")
    return key


@pytest.mark.asyncio
async def test_synthesis_skip_into_reflection_absorbs_without_new_or_evidence(tmp_path):
    tmpdir = str(tmp_path)
    facts = _facts()
    _write_json(tmpdir, "facts.json", facts)
    existing = _reflection("ref_old", "主人每晚睡前都会对小天说晚安", rein=1.25)
    _write_json(tmpdir, "reflections.json", [existing])
    _fs, _pm, re = _install(tmpdir)
    backoff_key = await _seed_backoff(re, facts)
    captured: list[str] = []
    answer = {
        "skip": True, "covered_by": "reflection.ref_old",
        "entity": "relationship", "reflection": "主人又说了晚安",
        "temporal_scope": "pattern", "event_when": None,
    }
    with _patch_vectors(_FakeEmbeddingService()), _llm(answer, captured), \
         patch("utils.language_utils.get_global_language", return_value="zh"):
        created = await re.synthesize_reflections(CHAR)

    assert created == []
    prompt = captured[0]
    assert "======以下为已有印象======" in prompt
    assert "======以上为已有印象======" in prompt
    assert "[reflection.ref_old] 主人每晚睡前都会对小天说晚安" in prompt
    assert "{EXISTING_IMPRESSIONS_BLOCK}" not in prompt

    stored = _read_json(tmpdir, "reflections.json")
    assert [r["id"] for r in stored] == ["ref_old"]
    assert stored[0]["covered_fact_ids"] == [f["id"] for f in facts]
    # User decision: restating an impression must not raise its score.
    assert stored[0]["reinforcement"] == 1.25
    assert stored[0]["rein_last_signal_at"] == existing["rein_last_signal_at"]
    assert all(f["absorbed"] for f in _read_json(tmpdir, "facts.json"))
    assert backoff_key not in await re._aload_synth_backoff(CHAR)


@pytest.mark.asyncio
async def test_synthesis_skip_into_persona_records_ids_without_evidence(tmp_path):
    tmpdir = str(tmp_path)
    facts = _facts()
    _write_json(tmpdir, "facts.json", facts)
    _write_json(tmpdir, "persona.json", {
        "relationship": {"facts": [
            _persona_entry("prom_night", "主人睡前会对小天说晚安", rein=0.75),
        ]},
    })
    _fs, pm, re = _install(tmpdir)
    captured: list[str] = []
    answer = {"skip": True, "covered_by": "[persona.relationship.prom_night]"}
    with _patch_vectors(_FakeEmbeddingService()), _llm(answer, captured), \
         patch("utils.language_utils.get_global_language", return_value="zh"):
        created = await re.synthesize_reflections(CHAR)

    assert created == []
    assert not os.path.exists(os.path.join(tmpdir, CHAR, "reflections.json")) or \
        _read_json(tmpdir, "reflections.json") == []
    entry = _read_json(tmpdir, "persona.json")["relationship"]["facts"][0]
    assert entry["covered_fact_ids"] == [f["id"] for f in facts]
    assert entry["reinforcement"] == 0.75
    assert all(f["absorbed"] for f in _read_json(tmpdir, "facts.json"))


@pytest.mark.asyncio
async def test_synthesis_skip_with_unknown_id_falls_back_to_new_reflection(tmp_path):
    tmpdir = str(tmp_path)
    facts = _facts()
    _write_json(tmpdir, "facts.json", facts)
    _write_json(tmpdir, "reflections.json", [
        _reflection("ref_old", "主人每晚睡前都会对小天说晚安"),
    ])
    _fs, _pm, re = _install(tmpdir)
    answer = {
        "skip": True, "covered_by": "reflection.ref_not_shown",
        "entity": "relationship", "relation_type": "dynamic",
        "reflection": "主人连着几晚睡前对小天说晚安",
        "temporal_scope": "pattern", "event_when": None,
    }
    with _patch_vectors(_FakeEmbeddingService()), _llm(answer, []), \
         patch("utils.language_utils.get_global_language", return_value="zh"):
        created = await re.synthesize_reflections(CHAR)

    assert len(created) == 1
    stored = {r["id"]: r for r in _read_json(tmpdir, "reflections.json")}
    assert created[0]["id"] in stored
    assert "covered_fact_ids" not in stored["ref_old"]


@pytest.mark.asyncio
async def test_synthesis_without_vectors_keeps_old_prompt_and_ignores_skip(tmp_path):
    tmpdir = str(tmp_path)
    facts = _facts()
    _write_json(tmpdir, "facts.json", facts)
    _write_json(tmpdir, "reflections.json", [
        _reflection("ref_old", "主人每晚睡前都会对小天说晚安"),
    ])
    _fs, pm, re = _install(tmpdir)
    captured: list[str] = []
    answer = {
        "skip": True, "covered_by": "reflection.ref_old",
        "entity": "relationship", "reflection": "主人连着几晚睡前对小天说晚安",
        "temporal_scope": "pattern", "event_when": None,
    }
    persona_reads = AsyncMock(side_effect=pm.aget_persona)
    with _patch_vectors(_FakeEmbeddingService(available=False)), \
         _llm(answer, captured), \
         patch.object(pm, "aget_persona", persona_reads), \
         patch("utils.language_utils.get_global_language", return_value="zh"):
        created = await re.synthesize_reflections(CHAR)

    prompt = captured[0]
    assert "已有印象" not in prompt
    assert "{EXISTING_IMPRESSIONS_BLOCK}" not in prompt
    assert "======以上为事实======\n\n请基于这些事实" in prompt
    assert len(created) == 1
    # Nothing extra is loaded when vectors are off: same work as before.
    persona_reads.assert_not_awaited()


@pytest.mark.asyncio
async def test_synthesis_without_query_vectors_shows_no_impressions(tmp_path):
    tmpdir = str(tmp_path)
    _write_json(tmpdir, "facts.json", _facts())
    _write_json(tmpdir, "reflections.json", [
        _reflection("ref_old", "主人每晚睡前都会对小天说晚安"),
    ])
    _fs, _pm, re = _install(tmpdir)
    service = _FakeEmbeddingService()

    async def _no_vectors(texts, **_kw):
        return [None] * len(texts)

    service.embed_batch = _no_vectors
    captured: list[str] = []
    answer = {
        "entity": "relationship", "reflection": "主人连着几晚睡前对小天说晚安",
        "temporal_scope": "pattern", "event_when": None,
    }
    with _patch_vectors(service), _llm(answer, captured), \
         patch("utils.language_utils.get_global_language", return_value="zh"):
        created = await re.synthesize_reflections(CHAR)

    assert "已有印象" not in captured[0]
    assert len(created) == 1


@pytest.mark.asyncio
async def test_existing_impressions_stay_inside_subject_and_active(tmp_path):
    from memory.scopes import MemorySubject

    tmpdir = str(tmp_path)
    group = MemorySubject.group_chat("qq", "7788")
    other = MemorySubject.group_chat("qq", "9999")
    _write_json(tmpdir, "reflections.json", [
        _reflection("ref_same", "群里大家睡前说晚安", **group.as_entry_fields()),
        _reflection("ref_other", "另一个群睡前说晚安", **other.as_entry_fields()),
        _reflection("ref_legacy", "主人睡前说晚安"),
        _reflection("ref_denied", "群里睡前说晚安被否认", status="denied",
                    **group.as_entry_fields()),
        _reflection("ref_promoted", "群里每晚有人道晚安", status="promoted",
                    **group.as_entry_fields()),
    ])
    _write_json(tmpdir, "persona.json", {
        group.persona_section_key: {"facts": [
            _persona_entry("p_same", "群友睡前总说晚安", **group.as_entry_fields()),
            # The live copy that promoting ref_promoted wrote; it stands in
            # for the historical reflection row.
            _persona_entry("p_promoted_copy", "群里每晚有人道晚安",
                           source_id="ref_promoted", **group.as_entry_fields()),
            _persona_entry("p_card", "设定：睡前说晚安", protected=True,
                           **group.as_entry_fields()),
        ]},
        other.persona_section_key: {"facts": [
            _persona_entry("p_other", "别的群友睡前说晚安", **other.as_entry_fields()),
        ]},
        "relationship": {"facts": [
            _persona_entry("p_legacy", "主人睡前对小天说晚安"),
        ]},
    })
    _fs, _pm, re = _install(tmpdir)
    batch = _facts(**group.as_entry_fields())
    with _patch_vectors(_FakeEmbeddingService()):
        block, candidates = await re._build_existing_impressions_block(
            CHAR, batch, subject=group, prompt_lang="zh",
        )
        _legacy_block, legacy_candidates = await re._build_existing_impressions_block(
            CHAR, _facts(), subject=None, prompt_lang="zh",
        )
    assert set(candidates) == {
        "reflection.ref_same",
        f"persona.{group.persona_section_key}.p_same",
        f"persona.{group.persona_section_key}.p_promoted_copy",
    }
    assert block.startswith("======以下为已有印象======\n")
    assert set(legacy_candidates) == {
        "reflection.ref_legacy", "persona.relationship.p_legacy",
    }


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["reflection", "persona"])
async def test_synthesis_skip_falls_back_when_target_rewritten_meanwhile(tmp_path, kind):
    """Refinement may rewrite the target while the LLM judges the old text."""
    tmpdir = str(tmp_path)
    _write_json(tmpdir, "facts.json", _facts())
    if kind == "reflection":
        _write_json(tmpdir, "reflections.json", [
            _reflection("ref_old", "主人每晚睡前都会对小天说晚安"),
        ])
        key = "reflection.ref_old"
    else:
        _write_json(tmpdir, "persona.json", {"relationship": {"facts": [
            _persona_entry("prom_night", "主人睡前会对小天说晚安"),
        ]}})
        key = "persona.relationship.prom_night"
    _fs, pm, re = _install(tmpdir)
    answer = {
        "skip": True, "covered_by": key,
        "entity": "relationship", "relation_type": "dynamic",
        "reflection": "主人连着几晚睡前对小天说晚安",
        "temporal_scope": "pattern", "event_when": None,
    }

    async def _llm_rewrites_target(api_config, prompt, **_kw):
        if kind == "reflection":
            rows = _read_json(tmpdir, "reflections.json")
            rows[0]["text"] = "主人只在周末对小天说晚安"
            _write_json(tmpdir, "reflections.json", rows)
        else:
            persona = await pm.aget_persona(CHAR)
            persona["relationship"]["facts"][0]["text"] = "主人只在周末对小天说晚安"
        return SimpleNamespace(content=json.dumps(answer, ensure_ascii=False)), 0

    with _patch_vectors(_FakeEmbeddingService()), \
         patch("memory.thinking_llm.ainvoke_thinking", _llm_rewrites_target), \
         patch("utils.language_utils.get_global_language", return_value="zh"):
        created = await re.synthesize_reflections(CHAR)

    assert len(created) == 1
    if kind == "reflection":
        old = {r["id"]: r for r in _read_json(tmpdir, "reflections.json")}["ref_old"]
    else:
        old = (await pm.aget_persona(CHAR))["relationship"]["facts"][0]
    assert "covered_fact_ids" not in old


@pytest.mark.asyncio
async def test_existing_impressions_keep_top_k_nearest(tmp_path):
    tmpdir = str(tmp_path)
    rows = [_reflection(f"ref_far{i}", f"主人喜欢咖啡第{i}种") for i in range(8)]
    rows.append(_reflection("ref_near", "主人睡前说晚安"))
    _write_json(tmpdir, "reflections.json", rows)
    _fs, _pm, re = _install(tmpdir)
    with _patch_vectors(_FakeEmbeddingService()), \
         patch("config.REFLECTION_EXISTING_IMPRESSIONS_TOP_K", 3):
        block, candidates = await re._build_existing_impressions_block(
            CHAR, _facts(), subject=None, prompt_lang="zh",
        )
    assert len(candidates) == 3
    assert block.splitlines()[1].startswith("[reflection.ref_near]")


def test_covered_fact_ids_dedup_and_cap():
    from memory._reflection.schema import merge_covered_fact_ids

    entry = {"covered_fact_ids": ["a", "b"]}
    assert merge_covered_fact_ids(entry, ["b", "c"]) is True
    assert entry["covered_fact_ids"] == ["a", "b", "c"]
    assert merge_covered_fact_ids(entry, ["a", "c"]) is False
    with patch("memory._reflection.schema.REFLECTION_COVERED_FACT_IDS_MAX", 2):
        merge_covered_fact_ids(entry, ["d"])
    assert entry["covered_fact_ids"] == ["c", "d"]
