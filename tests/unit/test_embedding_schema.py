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


@pytest.mark.asyncio
async def test_missing_correction_trust_stays_unknown_and_cannot_override(tmp_path):
    pm = _install_pm(str(tmp_path))
    await _seed_master_fact(pm, "Neko", "旧观察", speaker_id="qq:2002")
    await pm._aqueue_correction(
        "Neko", "旧观察", "新观察", "master",
        old_speaker_provenance={"speaker_id": "qq:2002"},
        new_speaker_provenance={
            "speaker_id": "qq:1001", "speaker_trust": 0.3,
        },
    )
    pending = await pm.aload_pending_corrections("Neko")
    assert "old_speaker_trust" not in pending[0]
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
    facts = pm._get_section_facts(await pm.aensure_persona("Neko"), "master")
    assert [entry["text"] for entry in facts] == ["新观察"]
    assert "trust=unknown" in prompts[0]


@pytest.mark.asyncio
async def test_mixed_correction_trust_is_hidden_from_prompt(tmp_path):
    from utils.file_utils import atomic_write_json_async

    pm = _install_pm(str(tmp_path))
    await _seed_master_fact(pm, "Neko", "旧观察", speaker_id="qq:2002")
    await pm._aqueue_correction(
        "Neko", "旧观察", "新观察", "master",
        old_speaker_provenance={
            "speaker_id": "qq:2002", "speaker_trust": 0.8,
        },
        new_speaker_provenance={
            "speaker_id": "qq:1001", "speaker_trust": 0.8,
        },
    )
    pending = await pm.aload_pending_corrections("Neko")
    pending[0]["old_speaker_provenance_mixed"] = True
    pending[0]["new_speaker_provenance_mixed"] = True
    await atomic_write_json_async(
        pm._corrections_path("Neko"), pending,
        indent=2, ensure_ascii=False,
    )
    prompts = []
    response = MagicMock()
    response.content = json.dumps([{"index": 0, "action": "keep_old"}])

    class _RecordingLLM:
        async def ainvoke(self, prompt, *_args, **_kwargs):
            prompts.append(prompt)
            return response

        async def aclose(self):
            return None

    with patch("utils.llm_client.create_chat_llm", return_value=_RecordingLLM()):
        assert await pm.resolve_corrections("Neko") == 1
    assert prompts[0].count("trust=unknown") == 2
    assert "trust=high" not in prompts[0]


def test_duplicate_partial_correction_does_not_invent_trust():
    from memory.persona.corrections import CorrectionsMixin

    corrections = [{
        "old_text": "旧观察", "new_text": "新观察",
        "entity": "master", "scope": None,
    }]
    assert CorrectionsMixin._build_correction_list(
        corrections, "旧观察", "新观察", "master",
        new_speaker_provenance={"speaker_id": "qq:1001"},
    ) is corrections
    assert corrections[0]["new_speaker_id"] == "qq:1001"
    assert "new_speaker_trust" not in corrections[0]


@pytest.mark.asyncio
async def test_correction_entries_omit_missing_speaker_trust(tmp_path):
    pm = _install_pm(str(tmp_path))
    await _seed_master_fact(pm, "Neko", "旧观察", speaker_id="qq:2002")
    await pm._aqueue_correction(
        "Neko", "旧观察", "新观察", "master",
        old_speaker_provenance={"speaker_id": "qq:2002"},
        new_speaker_provenance={"speaker_id": "qq:1001"},
    )
    response = MagicMock()
    response.content = json.dumps([{"index": 0, "action": "keep_new"}])
    llm = MagicMock()
    llm.ainvoke = AsyncMock(return_value=response)
    llm.aclose = AsyncMock()

    with patch("utils.llm_client.create_chat_llm", return_value=llm):
        assert await pm.resolve_corrections("Neko") == 1
    facts = pm._get_section_facts(await pm.aensure_persona("Neko"), "master")
    replacement = next(entry for entry in facts if entry["text"] == "新观察")
    assert replacement["speaker_id"] == "qq:1001"
    assert "speaker_trust" not in replacement

    history_pm = _install_pm(str(tmp_path / "history"))
    await _seed_master_fact(
        history_pm, "Neko", "旧观察", speaker_id="qq:2002",
    )
    await history_pm._aqueue_correction(
        "Neko", "旧观察", "新观察", "master",
        old_speaker_provenance={"speaker_id": "qq:2002"},
        new_speaker_provenance={"speaker_id": "qq:1001"},
    )
    response.content = json.dumps([{
        "index": 0, "action": "merge", "text": "合并观察",
    }])
    with patch("utils.llm_client.create_chat_llm", return_value=llm):
        assert await history_pm.resolve_corrections("Neko") == 1
    history_facts = history_pm._get_section_facts(
        await history_pm.aensure_persona("Neko"), "master",
    )
    merged = next(entry for entry in history_facts if entry["text"] == "合并观察")
    assert "speaker_id" not in merged and "speaker_trust" not in merged
    assert {item["speaker_id"] for item in merged["version_history"]} == {
        "qq:1001", "qq:2002",
    }
    assert all(
        "speaker_trust" not in item for item in merged["version_history"]
    )


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
        pm, "Neko", "小明喜欢猫",
        speaker_id="qq:2002", speaker_trust=0.8,
    )
    await pm._aqueue_correction(
        "Neko", "小明喜欢猫", "小明不喜欢猫", "master",
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
    assert [entry["text"] for entry in facts] == ["小明喜欢猫"]
    rejected = facts[0]["version_history"][-1]
    assert rejected["text"] == "小明不喜欢猫"
    assert rejected["reason"] == "trust_rejected_observation"
    assert rejected["speaker_id"] == "qq:1001"
    assert "trust=high" in prompts[0]
    assert "trust=low" in prompts[0]
    assert "0.8" not in prompts[0] and "0.3" not in prompts[0]


@pytest.mark.asyncio
@pytest.mark.parametrize("mixed_side", ["old", "new"])
async def test_mixed_correction_provenance_cannot_override_model(
    tmp_path, mixed_side,
):
    pm = _install_pm(str(tmp_path))
    await _seed_master_fact(
        pm, "Neko", "小明喜欢猫",
        speaker_id="qq:1001", speaker_trust=0.3,
    )
    correction = {
        "old_text": "小明喜欢猫", "new_text": "小明不喜欢猫",
        "entity": "master",
        "old_speaker_id": "qq:1001", "old_speaker_trust": 0.3,
        "new_speaker_id": "qq:2002", "new_speaker_trust": 0.8,
        f"{mixed_side}_speaker_provenance_mixed": True,
    }

    assert await pm._apply_correction_results(
        "Neko", [correction], {0},
        [{"index": 0, "action": "keep_old"}],
    ) == 1
    facts = pm._get_section_facts(await pm.aensure_persona("Neko"), "master")
    assert [entry["text"] for entry in facts] == ["小明喜欢猫"]


@pytest.mark.asyncio
@pytest.mark.parametrize("new_speaker_id", ["QQ:1001", "legacy-user"])
async def test_invalid_or_same_canonical_persona_speaker_cannot_drive_trust(
    tmp_path, new_speaker_id,
):
    pm = _install_pm(str(tmp_path))
    await _seed_master_fact(
        pm, "Neko", "小明喜欢猫",
        speaker_id="qq:1001", speaker_trust=0.8,
    )
    correction = {
        "old_text": "小明喜欢猫", "new_text": "小明不喜欢猫",
        "entity": "master",
        "old_speaker_id": "qq:1001", "old_speaker_trust": 0.8,
        "new_speaker_id": new_speaker_id, "new_speaker_trust": 0.3,
    }

    assert await pm._apply_correction_results(
        "Neko", [correction], {0},
        [{"index": 0, "action": "keep_new"}],
    ) == 1

    facts = pm._get_section_facts(await pm.aensure_persona("Neko"), "master")
    assert [entry["text"] for entry in facts] == ["小明不喜欢猫"]


@pytest.mark.asyncio
@pytest.mark.parametrize("action", ["keep_new", "keep_both"])
async def test_model_selected_mixed_new_provenance_stays_fail_closed(
    tmp_path, action,
):
    """Legacy residual speaker fields cannot override a mixed marker."""
    pm = _install_pm(str(tmp_path))
    await _seed_master_fact(pm, "Neko", "旧观察")
    correction = {
        "old_text": "旧观察", "new_text": "新观察", "entity": "master",
        "new_speaker_id": "qq:1001", "new_speaker_trust": 0.9,
        "new_speaker_provenance_mixed": True,
    }

    assert await pm._apply_correction_results(
        "Neko", [correction], {0}, [{"index": 0, "action": action}],
    ) == 1
    facts = pm._get_section_facts(await pm.aensure_persona("Neko"), "master")
    new_entry = next(entry for entry in facts if entry["text"] == "新观察")
    assert new_entry["speaker_provenance_mixed"] is True
    assert "speaker_id" not in new_entry
    assert "speaker_trust" not in new_entry


@pytest.mark.asyncio
async def test_model_merge_keeps_mixed_new_provenance_fail_closed(tmp_path):
    pm = _install_pm(str(tmp_path))
    await _seed_master_fact(
        pm, "Neko", "旧观察",
        speaker_id="qq:1001", speaker_trust=0.9,
    )
    correction = {
        "old_text": "旧观察", "new_text": "新观察", "entity": "master",
        "new_speaker_id": "qq:1001", "new_speaker_trust": 0.9,
        "new_speaker_provenance_mixed": True,
    }

    assert await pm._apply_correction_results(
        "Neko", [correction], {0},
        [{"index": 0, "action": "merge", "text": "合并观察"}],
    ) == 1
    merged = pm._get_section_facts(
        await pm.aensure_persona("Neko"), "master",
    )[0]
    assert merged["speaker_provenance_mixed"] is True
    assert "speaker_id" not in merged
    assert "speaker_trust" not in merged


@pytest.mark.asyncio
async def test_trust_rejected_history_is_idempotent_after_queue_write_failure(
    tmp_path,
):
    pm = _install_pm(str(tmp_path))
    await _seed_master_fact(
        pm, "Neko", "小明喜欢猫",
        speaker_id="qq:2002", speaker_trust=0.8,
    )
    await pm._aqueue_correction(
        "Neko", "小明喜欢猫", "小明不喜欢猫", "master",
        old_speaker_provenance={
            "speaker_id": "qq:2002", "speaker_trust": 0.8,
        },
        new_speaker_provenance={
            "speaker_id": "qq:1001", "speaker_trust": 0.3,
        },
    )
    correction = (await pm.aload_pending_corrections("Neko"))[0]
    result = [{"index": 0, "action": "keep_new"}]

    with patch(
        "memory.persona.corrections.atomic_write_json_async",
        side_effect=OSError("queue write failed"),
    ):
        with pytest.raises(OSError, match="queue write failed"):
            await pm._apply_correction_results("Neko", [correction], {0}, result)

    assert await pm._apply_correction_results(
        "Neko", [correction], {0}, result,
    ) == 1
    facts = pm._get_section_facts(await pm.aensure_persona("Neko"), "master")
    rejected = [
        item for item in facts[0].get("version_history", [])
        if item.get("reason") == "trust_rejected_observation"
    ]
    assert len(rejected) == 1
    assert rejected[0]["text"] == "小明不喜欢猫"
    assert rejected[0].get("correction_id")


@pytest.mark.asyncio
async def test_correction_trust_does_not_override_merge_without_conflict(tmp_path):
    pm = _install_pm(str(tmp_path))
    await _seed_master_fact(
        pm, "Neko", "Alice likes cats",
        speaker_id="qq:2002", speaker_trust=0.8,
    )
    await pm._aqueue_correction(
        "Neko", "Alice likes cats", "Alice likes cats and dogs", "master",
        old_speaker_provenance={
            "speaker_id": "qq:2002", "speaker_trust": 0.8,
        },
        new_speaker_provenance={
            "speaker_id": "qq:1001", "speaker_trust": 0.3,
        },
    )
    fake_llm = _make_llm_mock([{
        "index": 0, "action": "merge", "text": "Alice likes cats and dogs",
    }])
    with patch("utils.llm_client.create_chat_llm", return_value=fake_llm):
        assert await pm.resolve_corrections("Neko") == 1
    persona = await pm.aensure_persona("Neko")
    assert [
        entry["text"] for entry in pm._get_section_facts(persona, "master")
    ] == ["Alice likes cats and dogs"]


@pytest.mark.asyncio
async def test_trust_forced_replacement_preserves_prior_history(tmp_path):
    pm = _install_pm(str(tmp_path))
    await _seed_master_fact(
        pm, "Neko", "小明不喜欢猫",
        speaker_id="qq:1001", speaker_trust=0.3,
        version_history=[{"text": "小明曾经讨厌猫", "reason": "merged"}],
    )
    await pm._aqueue_correction(
        "Neko", "小明不喜欢猫", "小明喜欢猫", "master",
        old_speaker_provenance={
            "speaker_id": "qq:1001", "speaker_trust": 0.3,
        },
        new_speaker_provenance={
            "speaker_id": "qq:2002", "speaker_trust": 0.8,
        },
    )
    fake_llm = _make_llm_mock([{
        "index": 0, "action": "merge", "text": "小明现在喜欢猫",
    }])
    with patch("utils.llm_client.create_chat_llm", return_value=fake_llm):
        assert await pm.resolve_corrections("Neko") == 1
    persona = await pm.aensure_persona("Neko")
    replacement = next(
        entry for entry in pm._get_section_facts(persona, "master")
        if entry["text"] == "小明喜欢猫"
    )
    assert [item["text"] for item in replacement["version_history"]] == [
        "小明曾经讨厌猫", "小明不喜欢猫",
    ]


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
@pytest.mark.parametrize("result", [
    {"index": 0, "action": "retry"},
    {"index": 0},
])
async def test_correction_trust_does_not_validate_malformed_actions(
    tmp_path, result,
):
    pm = _install_pm(str(tmp_path))
    await _seed_master_fact(
        pm, "Neko", "高信任旧记忆",
        speaker_id="qq:2002", speaker_trust=0.8,
    )
    correction = {
        "old_text": "高信任旧记忆",
        "new_text": "低信任新观察",
        "entity": "master",
        "old_speaker_id": "qq:2002",
        "old_speaker_trust": 0.8,
        "new_speaker_id": "qq:1001",
        "new_speaker_trust": 0.3,
    }

    assert await pm._apply_correction_results(
        "Neko", [correction], {0}, [result],
    ) == 0
    persona = await pm.aensure_persona("Neko")
    assert [
        entry["text"] for entry in pm._get_section_facts(persona, "master")
    ] == ["高信任旧记忆"]


@pytest.mark.asyncio
async def test_correction_action_normalization_precedes_trust_override(tmp_path):
    pm = _install_pm(str(tmp_path))
    await _seed_master_fact(
        pm, "Neko", "旧观察", speaker_id="qq:2002", speaker_trust=0.8,
    )
    correction = {
        "old_text": "旧观察", "new_text": "独立新观察", "entity": "master",
        "old_speaker_id": "qq:2002", "old_speaker_trust": 0.8,
        "new_speaker_id": "qq:1001", "new_speaker_trust": 0.3,
    }

    assert await pm._apply_correction_results(
        "Neko", [correction], {0},
        [{"index": 0, "action": " KEEP_BOTH "}],
    ) == 1
    persona = await pm.aensure_persona("Neko")
    assert {
        entry["text"] for entry in pm._get_section_facts(persona, "master")
    } == {"旧观察", "独立新观察"}


@pytest.mark.asyncio
async def test_duplicate_correction_index_is_applied_only_once(tmp_path):
    pm = _install_pm(str(tmp_path))
    await _seed_master_fact(
        pm, "Neko", "旧观察", speaker_id="qq:2002", speaker_trust=0.3,
    )
    correction = {
        "old_text": "旧观察", "new_text": "新观察", "entity": "master",
        "old_speaker_id": "qq:2002", "old_speaker_trust": 0.3,
        "new_speaker_id": "qq:1001", "new_speaker_trust": 0.8,
    }

    assert await pm._apply_correction_results(
        "Neko", [correction], {0}, [
            {"index": 0, "action": "merge"},
            {"index": 0, "action": "merge"},
        ],
    ) == 1
    facts = pm._get_section_facts(await pm.aensure_persona("Neko"), "master")
    assert [entry["text"] for entry in facts] == ["新观察"]


@pytest.mark.asyncio
async def test_mixed_correction_response_keeps_invalid_item_queued(tmp_path):
    pm = _install_pm(str(tmp_path))
    await pm._aqueue_correction(
        "Neko", "旧观察甲", "新观察甲", "master",
    )
    await pm._aqueue_correction(
        "Neko", "旧观察乙", "新观察乙", "master",
    )
    corrections = await pm.aload_pending_corrections("Neko")

    assert await pm._apply_correction_results(
        "Neko", corrections, {0, 1}, [
            {"index": 0, "action": "keep_both"},
            {"index": 1, "action": "retry"},
        ],
    ) == 1
    pending = await pm.aload_pending_corrections("Neko")
    assert [(item["old_text"], item["new_text"]) for item in pending] == [
        ("旧观察乙", "新观察乙"),
    ]


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
async def test_mixed_correction_history_omits_residual_single_speaker(tmp_path):
    pm = _install_pm(str(tmp_path))
    await _seed_master_fact(
        pm, "Neko", "旧来源说法",
        speaker_id="qq:2002", speaker_trust=0.8,
    )
    correction = {
        "old_text": "旧来源说法",
        "new_text": "混合来源补充",
        "entity": "master",
        "old_speaker_id": "qq:2002",
        "old_speaker_trust": 0.8,
        "new_speaker_provenance_mixed": True,
        # Legacy rows may retain these stale single-speaker fields.
        "new_speaker_id": "qq:1001",
        "new_speaker_trust": 0.9,
    }

    assert await pm._apply_correction_results(
        "Neko", [correction], {0}, [{
            "index": 0, "action": "merge", "text": "合并说法",
        }],
    ) == 1

    merged = pm._get_section_facts(
        await pm.aensure_persona("Neko"), "master",
    )[0]
    history = {row["text"]: row for row in merged["version_history"]}
    mixed = history["混合来源补充"]
    assert mixed["speaker_provenance_mixed"] is True
    assert "speaker_id" not in mixed
    assert "speaker_trust" not in mixed


@pytest.mark.asyncio
async def test_same_speaker_merge_folds_trust_to_conservative_minimum(tmp_path):
    pm = _install_pm(str(tmp_path))
    await _seed_master_fact(
        pm, "Neko", "早期较高信任说法",
        speaker_id="qq:1001", speaker_trust=0.8,
        speaker_label="Alice(1001)",
    )
    await pm._aqueue_correction(
        "Neko", "早期较高信任说法", "后续较低信任补充", "master",
        old_speaker_provenance={
            "speaker_id": "qq:1001", "speaker_trust": 0.8,
        },
        new_speaker_provenance={
            "speaker_id": "qq:1001", "speaker_trust": 0.5,
        },
    )
    fake_llm = _make_llm_mock([{
        "index": 0, "action": "merge", "text": "同一人的合并说法",
    }])
    with patch("utils.llm_client.create_chat_llm", return_value=fake_llm):
        assert await pm.resolve_corrections("Neko") == 1
    merged = pm._get_section_facts(
        await pm.aensure_persona("Neko"), "master",
    )[0]
    assert merged["speaker_id"] == "qq:1001"
    assert merged["speaker_trust"] == pytest.approx(0.5)
    assert merged["speaker_label"] == "Alice(1001)"


@pytest.mark.asyncio
async def test_duplicate_pending_correction_marks_different_sources_mixed(tmp_path):
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
    assert "new_speaker_id" not in pending[0]
    assert "new_speaker_trust" not in pending[0]
    assert pending[0]["new_speaker_provenance_mixed"] is True


@pytest.mark.asyncio
async def test_correction_apply_requeues_changed_provenance_after_llm_window(tmp_path):
    pm = _install_pm(str(tmp_path))
    await _seed_master_fact(
        pm, "Neko", "小明喜欢猫",
        speaker_id="qq:9000", speaker_trust=0.8,
    )
    common_old = {"speaker_id": "qq:9000", "speaker_trust": 0.8}
    await pm._aqueue_correction(
        "Neko", "小明喜欢猫", "小明不喜欢猫", "master",
        old_speaker_provenance=common_old,
        new_speaker_provenance={
            "speaker_id": "qq:1001", "speaker_trust": 0.3,
        },
    )
    stale = [dict(item) for item in await pm.aload_pending_corrections("Neko")]
    await pm._aqueue_correction(
        "Neko", "小明喜欢猫", "小明不喜欢猫", "master",
        old_speaker_provenance=common_old,
        new_speaker_provenance={
            "speaker_id": "qq:2002", "speaker_trust": 0.7,
        },
    )
    current = await pm.aload_pending_corrections("Neko")
    assert current[0]["new_speaker_provenance_mixed"] is True
    assert "new_speaker_id" not in current[0]
    assert "new_speaker_trust" not in current[0]

    assert await pm._apply_correction_results(
        "Neko", stale, {0}, [{
            "index": 0,
            "action": "merge",
            "text": "模型保留的合并结论",
        }],
        refresh_pending=True,
    ) == 0
    assert await pm.aload_pending_corrections("Neko") == current
    persona = await pm.aensure_persona("Neko")
    facts = pm._get_section_facts(persona, "master")
    assert [item["text"] for item in facts] == ["小明喜欢猫"]


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
