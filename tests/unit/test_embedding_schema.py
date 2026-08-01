# -*- coding: utf-8 -*-
"""Schema-level tests for the embedding cache fields on persona /
reflection / fact entries.

Covers two contracts the rest of P2 relies on:

  1. New entries default the embedding triple to None — they're
     visible to the warmup worker as "needs embedding" without any
     migration step.
  2. The persona ``replace`` branch (resolve_corrections) clears the
     embedding triple alongside the existing token_count cache, so a
     text rewrite never leaves a stale vector pointing at the old text.

The first contract is tested directly on the normalize functions; the
second is an end-to-end test through resolve_corrections, mirroring
test_persona_version_history.py's mock-LLM pattern from PR #941."""
from __future__ import annotations

import json

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from memory.persona import PersonaManager
from memory.reflection import ReflectionEngine


# ── normalize-time defaults ─────────────────────────────────────────


def test_persona_normalize_entry_seeds_embedding_fields_as_none():
    """A fresh persona entry must read None on all three embedding fields
    so the warmup worker picks it up on its next sweep."""
    entry = PersonaManager._normalize_entry("主人喜欢猫")
    assert entry["embedding"] is None
    assert entry["embedding_text_sha256"] is None
    assert entry["embedding_model_id"] is None
    # text + version_history coexist with the embedding fields without
    # collision — defensive against a future refactor that consolidates
    # cache fields into a sub-dict.
    assert entry["text"] == "主人喜欢猫"
    assert entry["version_history"] == []


def test_persona_normalize_entry_preserves_existing_embedding_payload():
    """If a dict already carries an embedding triple (e.g. loaded from
    disk), normalize must NOT clobber it — that's the warmup worker's
    cache hit path."""
    raw = {
        "text": "x",
        "embedding": [0.1, 0.2, 0.3],
        "embedding_text_sha256": "deadbeef",
        "embedding_model_id": "local-text-retrieval-v1-128d-int8",
    }
    entry = PersonaManager._normalize_entry(raw)
    assert entry["embedding"] == [0.1, 0.2, 0.3]
    assert entry["embedding_text_sha256"] == "deadbeef"
    assert entry["embedding_model_id"] == "local-text-retrieval-v1-128d-int8"


def test_reflection_normalize_seeds_embedding_fields_as_none():
    raw = {"id": "r1", "text": "test reflection"}
    out = ReflectionEngine._normalize_reflection(raw)
    assert out["embedding"] is None
    assert out["embedding_text_sha256"] is None
    assert out["embedding_model_id"] is None


def test_reflection_normalize_preserves_existing_embedding():
    raw = {
        "id": "r1",
        "text": "t",
        "embedding": [0.5, 0.5],
        "embedding_text_sha256": "abc",
        "embedding_model_id": "local-text-retrieval-v1-256d-fp32",
    }
    out = ReflectionEngine._normalize_reflection(raw)
    assert out["embedding"] == [0.5, 0.5]
    assert out["embedding_text_sha256"] == "abc"
    assert out["embedding_model_id"] == "local-text-retrieval-v1-256d-fp32"


# ── replace branch invalidates the embedding cache ──────────────────


def _mock_cm(tmpdir: str):
    cm = MagicMock()
    cm.memory_dir = tmpdir
    cm.aget_character_data = AsyncMock(return_value=(
        "主人", "小天", {}, {}, {"human": "主人", "system": "SYS"}, {}, {}, {}, {},
    ))
    cm.get_character_data = MagicMock(return_value=(
        "主人", "小天", {}, {}, {"human": "主人", "system": "SYS"}, {}, {}, {}, {},
    ))
    cm.get_model_api_config = MagicMock(return_value={
        "model": "fake", "base_url": "http://fake", "api_key": "sk-fake",
    })
    cm.aget_model_api_config = AsyncMock(side_effect=lambda mt, **_: cm.get_model_api_config(mt))
    return cm


def _install_pm(tmpdir: str):
    from memory.event_log import EventLog

    cm = _mock_cm(tmpdir)
    with patch("memory.event_log.get_config_manager", return_value=cm), \
         patch("memory.persona.manager.get_config_manager", return_value=cm):
        event_log = EventLog()
        event_log._config_manager = cm
        pm = PersonaManager(event_log=event_log)
        pm._config_manager = cm
    return pm


def _make_llm_mock(payload):
    resp = MagicMock()
    resp.content = json.dumps(payload)

    async def _ainvoke(*_a, **_k):
        return resp

    async def _aclose():
        return None

    llm = MagicMock()
    llm.ainvoke = _ainvoke
    llm.aclose = _aclose
    return llm


async def _seed_master_fact(pm, name: str, text: str, **overrides):
    """Mirror of the helper in test_persona_version_history — appends
    a fact via internal API and returns the on-disk-normalized dict."""
    persona = await pm.aensure_persona(name)
    entry = pm._normalize_entry(text)
    entry.update(overrides)
    pm._get_section_facts(persona, "master").append(entry)
    await pm.asave_persona(name, persona)
    persona = await pm.aensure_persona(name)
    return next(
        e for e in pm._get_section_facts(persona, "master")
        if isinstance(e, dict) and e.get("text") == text
    )


@pytest.mark.asyncio
async def test_replace_invalidates_embedding_cache(tmp_path):
    """Mirrors PR #941's token_count-invalidation test: when text
    changes via the replace branch, the embedding triple MUST be
    cleared so the next worker sweep re-embeds the new text."""
    pm = _install_pm(str(tmp_path))
    seeded = await _seed_master_fact(
        pm, "小天", "主人住在东京",
        embedding=[0.1] * 128,
        embedding_text_sha256="cafef00d" * 8,
        embedding_model_id="local-text-retrieval-v1-128d-int8",
    )
    # Sanity: the seed actually round-tripped to disk with the cache
    # populated, so the assertion below proves invalidation, not a
    # missing seed.
    assert seeded["embedding"] is not None

    await pm._aqueue_correction("小天", "主人住在东京", "主人住在大阪", "master")
    fake_llm = _make_llm_mock([
        {"index": 0, "action": "merge", "text": "主人住在大阪"},
    ])
    with patch("utils.llm_client.create_chat_llm", return_value=fake_llm):
        await pm.resolve_corrections("小天")

    persona = await pm.aensure_persona("小天")
    target = next(
        e for e in pm._get_section_facts(persona, "master")
        if e.get("text") == "主人住在大阪"
    )
    assert target["embedding"] is None
    assert target["embedding_text_sha256"] is None
    assert target["embedding_model_id"] is None
    # And the version-history field still records the prior text — the
    # embedding wipe must NOT also wipe the chain. Same scope contract
    # as the token_count invalidation test in PR #941.
    history = target.get("version_history") or []
    assert history and history[0]["text"] == "主人住在东京"


@pytest.mark.asyncio
async def test_replace_preserves_embedding_when_replace_branch_not_taken(tmp_path):
    """The keep_both branch doesn't touch the existing entry, so its
    embedding cache must survive intact (callers rely on this so a
    'these aren't actually contradictory' decision keeps the warm
    embedding)."""
    pm = _install_pm(str(tmp_path))
    seeded = await _seed_master_fact(
        pm, "小天", "主人喜欢猫",
        embedding=[0.5] * 128,
        embedding_text_sha256="0123abcd" * 8,
        embedding_model_id="local-text-retrieval-v1-128d-int8",
    )
    original_embedding = list(seeded["embedding"])

    await pm._aqueue_correction("小天", "主人喜欢猫", "主人最近养了一只狗", "master")
    fake_llm = _make_llm_mock([
        {"index": 0, "action": "keep_both"},
    ])
    with patch("utils.llm_client.create_chat_llm", return_value=fake_llm):
        await pm.resolve_corrections("小天")

    persona = await pm.aensure_persona("小天")
    cat_entry = next(
        e for e in pm._get_section_facts(persona, "master")
        if e.get("text") == "主人喜欢猫"
    )
    assert cat_entry["embedding"] == original_embedding
    assert cat_entry["embedding_model_id"] == "local-text-retrieval-v1-128d-int8"


@pytest.mark.asyncio
async def test_correction_trust_overrides_model_and_archives_rejected_text(tmp_path):
    pm = _install_pm(str(tmp_path))
    await _seed_master_fact(
        pm, "Neko", "高信任旧记忆",
        speaker_id="qq:2002", speaker_trust=0.8,
    )
    await pm._aqueue_correction(
        "Neko", "高信任旧记忆", "低信任新观察", "master",
        old_speaker_provenance={
            "speaker_id": "qq:2002", "speaker_trust": 0.8,
        },
        new_speaker_provenance={
            "speaker_id": "qq:1001", "speaker_trust": 0.3,
        },
    )
    prompts = []
    response = MagicMock()
    response.content = json.dumps([{"index": 0, "action": "keep_new"}])

    class _RecordingLLM:
        async def ainvoke(self, prompt, *_args, **_kwargs):
            prompts.append(prompt)
            return response

        async def aclose(self):
            return None

    with patch("utils.llm_client.create_chat_llm", return_value=_RecordingLLM()):
        assert await pm.resolve_corrections("Neko") == 1
    persona = await pm.aensure_persona("Neko")
    facts = pm._get_section_facts(persona, "master")
    assert [entry["text"] for entry in facts] == ["高信任旧记忆"]
    rejected = facts[0]["version_history"][-1]
    assert rejected["text"] == "低信任新观察"
    assert rejected["reason"] == "trust_rejected_observation"
    assert rejected["speaker_id"] == "qq:1001"
    assert "trust=high" in prompts[0]
    assert "trust=low" in prompts[0]
    assert "0.8" not in prompts[0] and "0.3" not in prompts[0]


@pytest.mark.asyncio
async def test_correction_trust_does_not_override_keep_both(tmp_path):
    pm = _install_pm(str(tmp_path))
    await _seed_master_fact(
        pm, "Neko", "Alice likes cats",
        speaker_id="qq:2002", speaker_trust=0.8,
    )
    await pm._aqueue_correction(
        "Neko", "Alice likes cats", "Alice likes dogs", "master",
        old_speaker_provenance={
            "speaker_id": "qq:2002", "speaker_trust": 0.8,
        },
        new_speaker_provenance={
            "speaker_id": "qq:1001", "speaker_trust": 0.3,
        },
    )
    fake_llm = _make_llm_mock([{"index": 0, "action": "keep_both"}])
    with patch("utils.llm_client.create_chat_llm", return_value=fake_llm):
        assert await pm.resolve_corrections("Neko") == 1
    persona = await pm.aensure_persona("Neko")
    assert {
        entry["text"] for entry in pm._get_section_facts(persona, "master")
    } == {"Alice likes cats", "Alice likes dogs"}


@pytest.mark.asyncio
async def test_mixed_speaker_merge_clears_single_speaker_provenance(tmp_path):
    pm = _install_pm(str(tmp_path))
    await _seed_master_fact(
        pm, "Neko", "旧来源说法",
        speaker_id="qq:2002", speaker_trust=0.8,
    )
    await pm._aqueue_correction(
        "Neko", "旧来源说法", "另一来源补充", "master",
        old_speaker_provenance={
            "speaker_id": "qq:2002", "speaker_trust": 0.8,
        },
        new_speaker_provenance={
            "speaker_id": "qq:1001", "speaker_trust": 0.7,
        },
    )
    response = MagicMock()
    response.content = json.dumps([{
        "index": 0, "action": "merge", "text": "两个来源的合并说法",
    }])

    class _MergeLLM:
        async def ainvoke(self, *_args, **_kwargs):
            return response

        async def aclose(self):
            return None

    with patch("utils.llm_client.create_chat_llm", return_value=_MergeLLM()):
        assert await pm.resolve_corrections("Neko") == 1
    persona = await pm.aensure_persona("Neko")
    merged = pm._get_section_facts(persona, "master")[0]
    assert merged["text"] == "两个来源的合并说法"
    assert "speaker_id" not in merged
    assert "speaker_trust" not in merged
    history = {row["text"]: row for row in merged["version_history"]}
    assert history["旧来源说法"]["speaker_id"] == "qq:2002"
    assert history["另一来源补充"]["speaker_id"] == "qq:1001"


@pytest.mark.asyncio
async def test_duplicate_pending_correction_upgrades_to_stronger_source(tmp_path):
    pm = _install_pm(str(tmp_path))
    common_old = {"speaker_id": "qq:9000", "speaker_trust": 0.8}
    await pm._aqueue_correction(
        "Neko", "旧事实", "相同的新观察", "master",
        old_speaker_provenance=common_old,
        new_speaker_provenance={
            "speaker_id": "qq:1001", "speaker_trust": 0.3,
        },
    )
    await pm._aqueue_correction(
        "Neko", "旧事实", "相同的新观察", "master",
        old_speaker_provenance=common_old,
        new_speaker_provenance={
            "speaker_id": "qq:2002", "speaker_trust": 0.7,
        },
    )
    pending = await pm.aload_pending_corrections("Neko")
    assert len(pending) == 1
    assert pending[0]["new_speaker_id"] == "qq:2002"
    assert pending[0]["new_speaker_trust"] == pytest.approx(0.7)


@pytest.mark.asyncio
async def test_aadd_fact_carries_both_speakers_into_correction_queue(tmp_path):
    pm = _install_pm(str(tmp_path))
    await _seed_master_fact(
        pm, "Neko", "群友固定周五联机",
        speaker_id="qq:2002", speaker_trust=0.8,
    )
    result = await pm.aadd_fact(
        "Neko", "群友不再周五联机", entity="master",
        speaker_provenance={"speaker_id": "qq:1001", "speaker_trust": 0.3},
    )
    assert result == pm.FACT_QUEUED_CORRECTION
    pending = await pm.aload_pending_corrections("Neko")
    assert len(pending) == 1
    assert pending[0]["old_speaker_id"] == "qq:2002"
    assert pending[0]["old_speaker_trust"] == pytest.approx(0.8)
    assert pending[0]["new_speaker_id"] == "qq:1001"
    assert pending[0]["new_speaker_trust"] == pytest.approx(0.3)


def test_invalidate_embedding_cache_helper_wipes_triple():
    """The shared helper called by every text-rewriting code path
    (resolve_corrections replace branch, amerge_into,
    _apply_character_card_sync) must drop all three fields atomically
    — leaving any one populated would either re-embed unnecessarily
    or pretend a cache hit against the new text (silently corrupts
    retrieval).  Locks the contract so any future caller that bypasses
    the helper still has a regression test pointing at the right
    invariant."""
    entry = {
        "embedding": [0.1, 0.2, 0.3],
        "embedding_text_sha256": "deadbeef" * 8,
        "embedding_model_id": "local-text-retrieval-v1-128d-int8",
    }
    PersonaManager._invalidate_embedding_cache(entry)
    assert entry["embedding"] is None
    assert entry["embedding_text_sha256"] is None
    assert entry["embedding_model_id"] is None


def test_invalidate_embedding_cache_helper_safe_on_missing_fields():
    """Legacy entries without the embedding fields shouldn't crash —
    setting None on absent keys is the same as setting None on present
    keys, but we want an explicit assertion so the contract is locked
    in for callers that hand us bare dicts."""
    entry: dict = {}
    PersonaManager._invalidate_embedding_cache(entry)
    assert entry["embedding"] is None
    assert entry["embedding_text_sha256"] is None
    assert entry["embedding_model_id"] is None


def test_apply_character_card_sync_invalidates_embedding_on_text_change():
    """When characters.json's master/neko fields change, the per-entry
    text on persona is rewritten in place — the embedding cache MUST
    flip to None so the warmup worker re-embeds.  Mirrors the
    token_count invalidation contract added by #939."""
    pm = PersonaManager()
    persona = {
        "master": {"facts": []},
        "neko": {"facts": []},
    }
    # The card-entry id is content-addressed off (entity, field_name);
    # use the helper so the test stays aligned with whatever encoding
    # _card_entry_id picks (currently sha256 prefix).  Use a non-reserved
    # field name so _build_expected actually emits a row for it (reserved
    # fields like "name" are filtered out → entry would be removed,
    # not updated).
    field_name = "personality"
    card_id = pm._card_entry_id("master", field_name)
    # Text format mirrors what _build_expected emits ("{key}: {value}")
    # so the function recognises this as the SAME card row and takes
    # the update branch instead of remove+insert.
    persona["master"]["facts"].append({
        "id": card_id,
        "text": f"{field_name}: old card text",
        "source": "character_card",
        "protected": True,
        "embedding": [0.9] * 4,
        "embedding_text_sha256": "stale" * 12,
        "embedding_model_id": "local-text-retrieval-v1-128d-int8",
    })
    pm._apply_character_card_sync(
        "test", persona,
        master_basic_config={field_name: "new card text"},
        lanlan_basic_config={},
    )
    entry = persona["master"]["facts"][0]
    assert entry["text"] == f"{field_name}: new card text"
    assert entry["embedding"] is None
    assert entry["embedding_text_sha256"] is None
    assert entry["embedding_model_id"] is None
