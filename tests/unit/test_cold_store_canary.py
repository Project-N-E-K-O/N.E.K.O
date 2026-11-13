# -*- coding: utf-8 -*-
"""Canary: cold memory text never reaches a reader outside memory.cold_store.

One character directory is seeded with

* cold rows, each carrying a unique token:
  - a dedup-loser fact in ``fact_cold/`` (``ZQXCANARYFACT``),
  - an archived reflection in ``reflection_archive/`` (``ZQXCANARYREFL``),
  - an archived persona entry in ``persona_archive/`` (``ZQXCANARYPERSONA``),
  - a demoted fact still waiting in facts_archive.json to move to cold
    storage (``ZQXCANARYLEGACY``), the pre-cold-store layout;
* ordinary memory that each reader must still return (positive controls,
  ``ZQXPOS*``), so a passing test proves the reader actually ran over the
  seeded data instead of returning nothing.

Every reader that feeds a prompt, a recall result, a card, a snapshot or a
dedup decision is then driven, and its whole output is searched for the
cold tokens. Models and embeddings are stubbed; nothing touches the network.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import random
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import numpy as np
import pytest

pytestmark = pytest.mark.unit

NAME = "小天"
MODEL_ID = "test-model"
DAY = "2026-05-01T10:00:00"
OLD = "2025-01-01T10:00:00"  # older than every positive row: the flashback's first pick

CANARY_FACT = "ZQXCANARYFACT"
CANARY_REFL = "ZQXCANARYREFL"
CANARY_PERSONA = "ZQXCANARYPERSONA"
CANARY_LEGACY = "ZQXCANARYLEGACY"
CANARIES = (CANARY_FACT, CANARY_REFL, CANARY_PERSONA, CANARY_LEGACY)

POS_FACT = "ZQXPOSFACT"
POS_ABSORBED = "ZQXPOSABSORBED"
POS_ARCHIVE = "ZQXPOSARCHIVE"
POS_REFL = "ZQXPOSREFL"
POS_PERSONA = "ZQXPOSPERSONA"

# Cold texts mirror the positive ones word for word except for the token, so
# any keyword / vector search that matches one would match the other. Spaces
# keep each token its own BM25 term (a mixed CJK/Latin run is one token).
TEXT_LIVE = f"用户养了一只布偶猫 名字叫 {POS_FACT}"
TEXT_COLD = f"用户养了一只布偶猫 名字叫 {CANARY_FACT}"
TEXT_ABSORBED = f"用户的布偶猫很怕打雷 名字叫 {POS_ABSORBED}"
TEXT_ARCHIVE = f"用户以前的布偶猫 名字叫 {POS_ARCHIVE}"
TEXT_LEGACY = f"用户以前的布偶猫 名字叫 {CANARY_LEGACY}"
TEXT_REFL_LIVE = f"用户很宠爱自己的布偶猫 {POS_REFL}"
TEXT_REFL_COLD = f"用户很宠爱自己的布偶猫 {CANARY_REFL}"
TEXT_PERSONA_LIVE = f"用户家的布偶猫叫 {POS_PERSONA}"
TEXT_PERSONA_COLD = f"用户家的布偶猫叫 {CANARY_PERSONA}"

COLD_IDS = {"f_cold", "r_cold", "p_cold", "f_legacy_cold"}


def _hash(text: str) -> str:
    # Stage-1 content hash of a private, non-daily fact (facts.py).
    return hashlib.sha256(text.encode()).hexdigest()[:16]


def _vec(row: dict) -> dict:
    import memory.embeddings as emb
    emb.stamp_embedding_fields(
        row, np.asarray([1.0, 0.0, 0.0, 0.0], dtype=np.float32), row["text"], MODEL_ID,
    )
    return row


def _fact(fid: str, text: str, **extra) -> dict:
    return {
        "id": fid, "text": text, "entity": "master", "importance": 9,
        "hash": _hash(text), "created_at": DAY, "event_start_at": DAY,
        "absorbed": False, "signal_processed": True, "source": "user_observation",
        **extra,
    }


def _leaks(value) -> list[str]:
    blob = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, default=str)
    return [token for token in CANARIES if token in blob]


def _has(value, token: str) -> bool:
    blob = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, default=str)
    return token in blob


class _NoVectors:
    def is_available(self):
        return False

    def is_disabled(self):
        return True

    def disable_reason(self):
        return "test"

    def model_id(self):
        return None


class _Vectors:
    def is_available(self):
        return True

    def is_disabled(self):
        return False

    def model_id(self):
        return MODEL_ID


class _FakeIndex:
    """FTS near-dup index stub: returns fixed (fact_id, overlap) hits."""

    def __init__(self, hits):
        self.hits = list(hits)

    async def asearch_similar_facts(self, _name, _text, limit):
        return sorted(self.hits[:limit], key=lambda hit: hit[1], reverse=True)

    async def aindex_fact(self, *_a, **_k):
        return None

    def fts_index_needs_backfill(self, _name):
        return False


def _mock_cm(tmpdir: str):
    cm = MagicMock()
    cm.memory_dir = tmpdir
    data = ("主人", NAME, {}, {}, {"human": "主人", "system": "SYS"}, {}, {}, {}, {})
    cm.aget_character_data = AsyncMock(return_value=data)
    cm.get_character_data = MagicMock(return_value=data)
    return cm


@pytest.fixture(autouse=True)
def _fresh_recall_caches():
    from memory.hybrid_recall import _invalidate_pool_cache
    _invalidate_pool_cache()
    yield
    _invalidate_pool_cache()


@pytest.fixture
async def canary(tmp_path):
    """FactStore / PersonaManager / ReflectionEngine on tmp_path, seeded hot and cold."""
    from memory import cold_store
    from memory.archive_shards import append_to_shard_sync
    from memory.event_log import EventLog
    from memory.facts import FactStore
    from memory.persona import PersonaManager
    from memory.reflection import ReflectionEngine
    from utils.file_utils import atomic_write_json

    cm = _mock_cm(str(tmp_path))
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

    # ── hot memory (positive controls) ──
    live = _vec(_fact("f_live", TEXT_LIVE))
    # Absorbed but recent: an older one would leave for facts_archive.json on save.
    absorbed = _vec(_fact("f_absorbed", TEXT_ABSORBED, absorbed=True,
                          created_at=datetime.now().isoformat()))
    fs._facts[NAME] = [live, absorbed]
    await fs.asave_facts(NAME)
    char_dir = fs._character_dir(NAME)

    archived = _vec(_fact("f_arch", TEXT_ARCHIVE, absorbed=True))
    # Pre-cold-store layout: a demoted row still in facts_archive.json.
    legacy = _vec(_fact(
        "f_legacy_cold", TEXT_LEGACY, absorbed=True, created_at=OLD, event_start_at=OLD,
        arbitration_archived_at=DAY, arbitration_reason="trust_preferred_existing",
        superseded_by="f_arch",
    ))
    atomic_write_json(fs._facts_archive_path(NAME), [archived, legacy], indent=2, ensure_ascii=False)

    await re.asave_reflections(NAME, [{
        "id": "r_live", "text": TEXT_REFL_LIVE, "entity": "master", "status": "confirmed",
        "confirmed_at": DAY, "auto_confirmed": True, "source_fact_ids": [],
        "created_at": DAY, "event_start_at": DAY, "reinforcement": 0.1,
        "rein_last_signal_at": DAY,
    }])

    persona = await pm.aensure_persona(NAME)
    entry = pm._build_fact_entry(TEXT_PERSONA_LIVE, "reflection_time_driven", None)
    entry["created_at"] = DAY
    pm._get_section_facts(persona, "master").append(entry)
    await pm.asave_persona(NAME, persona)

    # ── cold memory (canaries) ──
    cold_fact = _vec(_fact(
        "f_cold", TEXT_COLD, absorbed=True, created_at=OLD, event_start_at=OLD,
        arbitration_archived_at=DAY, arbitration_reason="trust_preferred_existing",
        superseded_by="f_live",
    ))
    written = cold_store.append(char_dir, [cold_fact])
    assert written == 1
    append_to_shard_sync(re._reflections_archive_dir(NAME), [{
        "id": "r_cold", "text": TEXT_REFL_COLD, "entity": "master",
        # Deliberately a live-looking status: only the cold location keeps it out.
        "status": "confirmed", "confirmed_at": OLD, "source_fact_ids": [],
        "created_at": OLD, "event_start_at": DAY, "archived_at": DAY,
    }])
    append_to_shard_sync(pm._persona_archive_dir(NAME), [{
        "id": "p_cold", "text": TEXT_PERSONA_COLD, "entity_key": "master",
        "source": "reflection_time_driven", "created_at": OLD, "archived_at": DAY,
    }])

    # Sanity: the cold rows really are on disk, text included.
    assert {r["id"] for r in cold_store.read_full(char_dir)} == {"f_cold"}
    assert {r["id"] for r in cold_store.read_full(char_dir, kind="reflection")} == {"r_cold"}
    assert {r["id"] for r in cold_store.read_full(char_dir, kind="persona")} == {"p_cold"}

    return SimpleNamespace(fs=fs, pm=pm, re=re, cm=cm, char_dir=char_dir, tmp_path=tmp_path)


def _recall_patches():
    import memory.embeddings as emb
    return (
        patch.object(emb, "get_embedding_service", MagicMock(return_value=_NoVectors())),
        patch("memory.hybrid_recall._cosine_rank", new=AsyncMock(return_value=[])),
        patch("memory.hybrid_recall.HYBRID_RECALL_BM25_THRESHOLD", 0.0),
        patch("memory.stop_names.collect_stop_names", return_value=[]),
    )


# ── 0. FactStore full-pool loader ──────────────────────────────────────


async def test_load_facts_full_excludes_cold_and_stubs_carry_no_text(canary):
    fs = canary.fs
    full = fs.load_facts_full(NAME)
    assert {r["id"] for r in full} == {"f_live", "f_absorbed", "f_arch"}
    assert _leaks(full) == []

    stubbed = fs.load_facts_full(NAME, include_cold_stubs=True)
    by_id = {r.get("id"): r for r in stubbed}
    assert {"f_cold", "f_legacy_cold"} <= set(by_id)
    for cold_id in ("f_cold", "f_legacy_cold"):
        assert "text" not in by_id[cold_id]
        assert "embedding" not in by_id[cold_id]
        assert by_id[cold_id]["text_sha256"]
    assert by_id["f_cold"]["text_sha256"] == hashlib.sha256(TEXT_COLD.encode()).hexdigest()
    assert _leaks(stubbed) == []
    assert _leaks(await fs.aload_facts_full(NAME, include_cold_stubs=True)) == []


# ── 1. keyword recall ──────────────────────────────────────────────────


async def test_archive_pool_loader_skips_cold(canary):
    from memory.hybrid_recall import _aload_archive_facts

    pool = await _aload_archive_facts(canary.fs, NAME)
    assert {r["id"] for r in pool} == {"f_arch"}
    assert _leaks(pool) == []


async def test_keyword_recall_never_returns_cold_text(canary):
    from memory.hybrid_recall import hybrid_recall

    queries = [TEXT_COLD, TEXT_LEGACY, TEXT_REFL_COLD, TEXT_PERSONA_COLD,
               " ".join(CANARIES)]
    outputs = []
    p1, p2, p3, p4 = _recall_patches()
    with p1, p2, p3, p4:
        for query in queries:
            outputs.append(await hybrid_recall(
                lanlan_name=NAME, query=query, fact_store=canary.fs,
                reflection_engine=canary.re, config_manager=canary.cm,
                persona_manager=canary.pm,
            ))
    for out in outputs:
        # The query is echoed back; only what the reader found counts.
        assert _leaks(out["results"]) == [], out
        assert not COLD_IDS & {r["id"] for r in out["results"]}
    # Positive control: the near-identical hot rows were found.
    assert _has(outputs[0], POS_FACT)
    assert _has(outputs[1], POS_ARCHIVE)
    assert _has(outputs[2], POS_REFL)


# ── 2. recall by time ──────────────────────────────────────────────────


async def test_recall_by_time_never_returns_cold_text(canary):
    from memory.hybrid_recall import recall_by_time

    outputs = [
        await recall_by_time(
            lanlan_name=NAME, time_spec=spec, fact_store=canary.fs,
            reflection_engine=canary.re,
        )
        for spec in ("2026-05-01", "2025-01-01")
    ]
    for out in outputs:
        assert _leaks(out) == [], out
        assert not COLD_IDS & {r["id"] for r in out["results"]}
    in_window = outputs[0]
    for token in (POS_FACT, POS_ABSORBED, POS_ARCHIVE, POS_REFL):
        assert _has(in_window, token), token


# ── 3. passive retrieval (turn context) ────────────────────────────────


async def test_passive_retrieval_pools_exclude_cold(canary):
    import memory.embeddings as emb
    import memory.turn_context as mtc

    with patch.object(emb, "get_embedding_service", MagicMock(return_value=_NoVectors())):
        bm25_pool, embedding_pool = await mtc._aload_pools(
            NAME, canary.fs, canary.re, canary.pm, None,
        )
    for pool in (bm25_pool, embedding_pool):
        assert _leaks(pool) == []
        assert not COLD_IDS & {str(r.get("id")) for r in pool}
    for token in (POS_FACT, POS_ABSORBED, POS_ARCHIVE):
        assert _has(bm25_pool, token), token
    assert _has(embedding_pool, POS_FACT)


async def test_passive_retrieval_context_never_shows_cold_text(canary):
    import memory.embeddings as emb
    import memory.turn_context as mtc

    mtc._probes.vectors.clear()
    mtc._backgrounds.clear()
    results = []
    with patch.object(emb, "get_embedding_service", MagicMock(return_value=_NoVectors())), \
         patch("memory.stop_names.collect_stop_names", return_value=[]):
        for text in (TEXT_COLD, TEXT_LEGACY, TEXT_REFL_COLD, TEXT_PERSONA_COLD, TEXT_LIVE):
            results.append(await mtc.amemory_turn_context(
                lanlan_name=NAME, text=text, fact_store=canary.fs,
                reflection_engine=canary.re, persona_manager=canary.pm,
                config_manager=canary.cm, budget_ms=2000, language="zh",
            ))
    for result in results:
        assert result["result"] != "error", result
        assert _leaks(result["context"]) == [], result
        assert not COLD_IDS & set(result["memory_ids"])
    # Positive control: the live fact's own wording brings it in.
    assert results[-1]["result"] == "matched", results[-1]
    assert POS_FACT in results[-1]["context"]


# ── 4. card forge facts ────────────────────────────────────────────────


@pytest.fixture
def forge_context(canary, monkeypatch):
    from main_logic import card_forge_facts as F

    monkeypatch.delenv("NEKO_FORGE_FACTS_URL", raising=False)
    monkeypatch.delenv("NEKO_CARD_FORGE_ALLOW_CHARACTER_OVERRIDE", raising=False)
    monkeypatch.setattr(F, "get_global_language_full", lambda: "en")

    async def context(*_args, **_kwargs):
        return F.ActiveNekoContext(
            master_name="主人", lanlan_name=NAME, memory_dir=canary.tmp_path,
            facts_path=Path(canary.char_dir) / "facts.json", source="test",
        )

    monkeypatch.setattr(F, "resolve_active_neko_context", context)
    return canary


async def test_card_forge_payload_never_carries_cold_text(forge_context):
    from main_logic import card_forge_facts as F

    payloads = [
        await F.build_forge_facts_payload(
            runtime_character_hint=NAME, min_importance=0, limit=limit,
        )
        for limit in (5, 20)
    ]
    for payload in payloads:
        assert _leaks(payload) == [], payload
        assert not COLD_IDS & {f.get("id") for f in payload["facts"]}
        for token in (POS_FACT, POS_ABSORBED, POS_ARCHIVE):
            assert _has(payload["facts"], token), token


# ── 5. farewell flashback ──────────────────────────────────────────────


async def test_farewell_flashback_never_picks_cold_text(forge_context, monkeypatch):
    from main_logic import card_forge_facts as F
    from main_logic import farewell_summary as S
    import utils.config_manager as config_manager_module

    monkeypatch.setattr(config_manager_module, "get_config_manager", lambda: forge_context.cm)
    summary = await S.build_farewell_summary()
    assert _leaks(summary) == [], summary
    assert summary["flashback"] is not None
    assert summary["flashback"]["id"] not in COLD_IDS

    char_dir = Path(forge_context.char_dir)
    raw = F._load_facts_json(char_dir / "facts.json")
    raw_archive = F._load_facts_json(char_dir / "facts_archive.json")
    picks = [S.select_flashback(raw, raw_archive, rng=random.Random(seed)) for seed in range(30)]
    assert all(pick is not None for pick in picks)
    assert _leaks(picks) == []
    assert {pick["id"] for pick in picks} <= {"f_live", "f_absorbed", "f_arch"}


async def test_forge_and_farewell_skip_a_stale_active_copy_of_a_cold_row(forge_context, monkeypatch):
    """A cloud-save rollback can bring back an older facts.json still holding a
    row that went cold here; those raw-file readers treat it as cold too."""
    from main_logic import card_forge_facts as F
    from main_logic import farewell_summary as S
    from memory import cold_store
    from utils.file_utils import atomic_write_json
    import utils.config_manager as config_manager_module

    char_dir = Path(forge_context.char_dir)
    [cold] = [row for row in cold_store.read_full(str(char_dir)) if row["id"] == "f_cold"]
    stale = {key: value for key, value in cold.items()
             if key not in ("arbitration_archived_at", "arbitration_reason", "superseded_by")}
    for name in ("facts.json", "facts_archive.json"):
        rows = F._load_facts_json(char_dir / name)
        atomic_write_json(str(char_dir / name), rows + [dict(stale)], indent=2, ensure_ascii=False)
    payload = await F.build_forge_facts_payload(runtime_character_hint=NAME, min_importance=0, limit=20)
    assert _leaks(payload) == [] and "f_cold" not in {f.get("id") for f in payload["facts"]}
    monkeypatch.setattr(config_manager_module, "get_config_manager", lambda: forge_context.cm)
    summary = await S.build_farewell_summary()
    assert _leaks(summary) == []


# ── 6. reflection synthesis background ─────────────────────────────────


async def test_synthesis_related_context_never_shows_cold_text(canary):
    captured = {}

    async def _whole_pool(self, pool, query_texts, **kwargs):
        captured["pool"] = list(pool)
        return list(pool)  # everything offered comes back: the block shows the whole pool

    unabsorbed = [r for r in await canary.fs.aload_facts(NAME) if r["id"] == "f_live"]
    with patch("memory.embeddings.get_embedding_service", return_value=_Vectors()), \
         patch("memory.recall.MemoryRecallReranker.aretrieve_per_query_topk", _whole_pool):
        block = await canary.re._build_related_context_block(NAME, unabsorbed)
    assert _leaks(captured["pool"]) == []
    assert not COLD_IDS & {r.get("id") for r in captured["pool"]}
    assert _leaks(block) == [], block
    assert POS_ABSORBED in block and POS_ARCHIVE in block


# ── 7. write dedup (FTS near-dup guard) ────────────────────────────────


async def test_fts_guard_does_not_block_a_new_fact_matching_cold_text(canary):
    fs = canary.fs
    fs._time_indexed = _FakeIndex([("f_cold", 1.0), ("f_legacy_cold", 1.0)])
    created = await fs._apersist_new_facts(
        NAME, [{"text": TEXT_COLD, "importance": 7, "entity": "master"}],
        default_source="user_observation", semantic_dedup=True,
    )
    assert len(created) == 1
    assert created[0]["id"] != "f_cold" and created[0]["text"] == TEXT_COLD
    assert created[0]["id"] in {r["id"] for r in await fs.aload_facts(NAME)}

    created_legacy = await fs._apersist_new_facts(
        NAME, [{"text": TEXT_LEGACY, "importance": 7, "entity": "master"}],
        default_source="user_observation", semantic_dedup=True,
    )
    assert len(created_legacy) == 1

    # Positive control: the same guard still blocks an absorbed-archived duplicate.
    fs._time_indexed = _FakeIndex([("f_arch", 1.0)])
    blocked = await fs._apersist_new_facts(
        NAME, [{"text": TEXT_ARCHIVE, "importance": 7, "entity": "master"}],
        default_source="user_observation", semantic_dedup=True,
    )
    assert blocked == []


async def test_fts_guard_ignores_an_unmarked_archive_copy_of_a_cold_row(canary):
    """A cloud save can bring back an older facts_archive.json holding a cold row
    without its marker; that copy is cold too and must not block a restatement."""
    from memory import cold_store
    from utils.file_utils import atomic_write_json

    fs = canary.fs
    [cold] = [row for row in cold_store.read_full(canary.char_dir) if row["id"] == "f_cold"]
    stale = {key: value for key, value in cold.items()
             if key not in ("arbitration_archived_at", "arbitration_reason", "superseded_by")}
    archive = json.loads(Path(fs._facts_archive_path(NAME)).read_text(encoding="utf-8"))
    atomic_write_json(fs._facts_archive_path(NAME), archive + [stale], indent=2, ensure_ascii=False)
    fs._time_indexed = _FakeIndex([("f_cold", 1.0)])
    created = await fs._apersist_new_facts(
        NAME, [{"text": TEXT_COLD, "importance": 7, "entity": "master"}],
        default_source="user_observation", semantic_dedup=True,
    )
    assert len(created) == 1 and created[0]["id"] != "f_cold"


# ── 8. near-dup pairing ────────────────────────────────────────────────


async def test_fts_stage2_never_pairs_a_new_fact_with_cold(canary):
    fs = canary.fs
    resolver = MagicMock()
    resolver.aenqueue_candidates = AsyncMock(return_value=1)
    fs.attach_dedup_resolver(resolver)
    # Cold hits rank first; only the live row may become the arbitration partner.
    fs._time_indexed = _FakeIndex([("f_cold", 0.95), ("f_legacy_cold", 0.95), ("f_live", 0.6)])
    new_text = "用户养了一只布偶猫 名字叫 ZQXNEWFACT"
    created = await fs._apersist_new_facts(
        NAME, [{"text": new_text, "importance": 7, "entity": "master"}],
        default_source="user_observation", semantic_dedup=True,
    )
    assert len(created) == 1
    for _ in range(50):
        if resolver.aenqueue_candidates.await_count:
            break
        await asyncio.sleep(0.02)
    resolver.aenqueue_candidates.assert_awaited_once()
    _name, pairs = resolver.aenqueue_candidates.await_args.args[:2]
    assert [p["existing_id"] for p in pairs] == ["f_live"]
    assert all(p["candidate_id"] == created[0]["id"] for p in pairs)
    pending = created[0].get("_near_dup_pending")
    assert pending is None or pending.get("existing_id") == "f_live"


async def test_vector_stage2_candidates_never_pair_with_cold(canary):
    from memory.fact_dedup import FactDedupResolver

    fs = canary.fs
    fresh = _vec(_fact("f_new", "用户养了一只布偶猫 名字叫 ZQXNEWFACT",
                       created_at="2026-06-01T10:00:00"))
    fs._facts[NAME].append(fresh)
    # The pool the embedding worker hands to detect_candidates (active facts) ...
    worker_pool = await fs.aload_facts(NAME)
    pairs = FactDedupResolver.detect_candidates(worker_pool, threshold=0.5, only_for_ids={"f_new"})
    # ... and the widest pool a caller could build: active + archive + cold stubs.
    widest = await fs.aload_facts_full(NAME, include_cold_stubs=True)
    pairs_wide = FactDedupResolver.detect_candidates(widest, threshold=0.5, only_for_ids={"f_new"})
    for found in (pairs, pairs_wide):
        assert not COLD_IDS & {p["existing_id"] for p in found}
        # Positive control: the live, unabsorbed row with the same vector pairs up.
        assert "f_live" in {p["existing_id"] for p in found}


# ── 9. long-term sync export ───────────────────────────────────────────


async def test_long_term_sync_export_lists_cold_by_id_only(canary):
    from memory.long_term_sync import export_document, read_views

    document = export_document(read_views(canary.char_dir), 3000)
    blob = json.dumps(document, ensure_ascii=False)
    assert _leaks(blob) == []
    assert {item["id"] for item in document.get("cold", [])} == COLD_IDS
    statuses = {item["id"]: item["status"] for item in document["cold"]}
    assert statuses == {
        "f_cold": "superseded", "f_legacy_cold": "superseded",
        "r_cold": "archived", "p_cold": "archived",
    }
    assert not COLD_IDS & {entry["id"] for entry in document.get("entries", [])}
    for token in (POS_FACT, POS_REFL, POS_PERSONA):
        assert token in blob, token
