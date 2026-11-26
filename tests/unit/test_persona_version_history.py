# -*- coding: utf-8 -*-
"""Unit tests for fact version chain (RFC memory-enhancements §2).

When resolve_corrections decides `replace`, the old text must be
preserved in `version_history` on the replacing entry, chained across
multiple corrections so temporal context (e.g., the owner used to live in Tokyo → moved to Osaka)
stays traceable. `replace` is an in-place edit: id / source / evidence
counters must survive the rewrite — only `text` and the history tail
change."""
from __future__ import annotations

import json

from unittest.mock import AsyncMock, MagicMock, patch

import pytest


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


def _install(tmpdir: str):
    from memory.event_log import EventLog
    from memory.persona import PersonaManager

    cm = _mock_cm(tmpdir)
    with patch("memory.event_log.get_config_manager", return_value=cm), \
         patch("memory.persona.manager.get_config_manager", return_value=cm):
        event_log = EventLog()
        event_log._config_manager = cm
        pm = PersonaManager(event_log=event_log)
        pm._config_manager = cm
    return pm, cm


def _make_llm_mock(payload: list[dict]):
    """Fake chat-llm whose ainvoke yields `payload` as JSON."""
    resp = MagicMock()
    resp.content = json.dumps(payload)

    async def _ainvoke(*_args, **_kwargs):
        return resp

    async def _aclose():
        return None

    llm = MagicMock()
    llm.ainvoke = _ainvoke
    llm.aclose = _aclose
    return llm


async def _seed_master_fact(pm, name: str, text: str, **overrides) -> dict:
    """Append a fact to `name`'s master section via the public API and
    return the stored dict so the test can mutate/inspect it."""
    persona = await pm.aensure_persona(name)
    entry = pm._normalize_entry(text)
    entry.update(overrides)
    pm._get_section_facts(persona, "master").append(entry)
    await pm.asave_persona(name, persona)
    # Re-read so callers see the on-disk normalized copy.
    persona = await pm.aensure_persona(name)
    return next(
        e for e in pm._get_section_facts(persona, "master")
        if isinstance(e, dict) and e.get("text") == text
    )


@pytest.mark.asyncio
async def test_replace_records_old_text_in_version_history(tmp_path):
    """The replace action must push old text into version_history; the new entry's text is merged."""
    pm, _ = _install(str(tmp_path))
    await _seed_master_fact(pm, "小天", "主人住在东京")

    await pm._aqueue_correction("小天", "主人住在东京", "主人住在大阪", "master")
    fake_llm = _make_llm_mock([{
        "index": 0, "action": "merge", "text": "主人后来搬到了大阪",
    }])
    with patch("utils.llm_client.create_chat_llm", return_value=fake_llm):
        resolved = await pm.resolve_corrections("小天")
    assert resolved == 1

    persona = await pm.aensure_persona("小天")
    master_facts = pm._get_section_facts(persona, "master")
    target = next(e for e in master_facts if e.get("text") == "主人后来搬到了大阪")
    history = target.get("version_history") or []
    assert len(history) == 1
    assert history[0]["text"] == "主人住在东京"
    assert history[0]["reason"] == "correction"
    assert history[0]["replaced_at"]


@pytest.mark.asyncio
async def test_replace_chains_history_across_multiple_corrections(tmp_path):
    """Two consecutive replace actions must retain the entire history chain rather than overwrite it."""
    pm, _ = _install(str(tmp_path))
    await _seed_master_fact(pm, "小天", "主人住在东京")

    await pm._aqueue_correction("小天", "主人住在东京", "主人住在大阪", "master")
    fake_llm = _make_llm_mock([{"index": 0, "action": "merge", "text": "主人住在大阪"}])
    with patch("utils.llm_client.create_chat_llm", return_value=fake_llm):
        await pm.resolve_corrections("小天")

    await pm._aqueue_correction("小天", "主人住在大阪", "主人住在福冈", "master")
    fake_llm = _make_llm_mock([{"index": 0, "action": "merge", "text": "主人住在福冈"}])
    with patch("utils.llm_client.create_chat_llm", return_value=fake_llm):
        await pm.resolve_corrections("小天")

    persona = await pm.aensure_persona("小天")
    master_facts = pm._get_section_facts(persona, "master")
    target = next(e for e in master_facts if e.get("text") == "主人住在福冈")
    history = target.get("version_history") or []
    assert [h["text"] for h in history] == ["主人住在东京", "主人住在大阪"]
    for h in history:
        assert h["reason"] == "correction"


@pytest.mark.asyncio
async def test_replace_preserves_id_source_and_evidence_metadata(tmp_path):
    """CodeRabbit-flagged: `replace` must be in-place — id / source /
    reinforcement / recent_mentions survive the text rewrite."""
    pm, _ = _install(str(tmp_path))
    await _seed_master_fact(
        pm, "小天", "主人住在东京",
        id="prom_ref_tokyo",
        source="reflection",
        source_id="ref_tokyo",
        reinforcement=2.5,
        disputation=0.5,
        rein_last_signal_at="2026-04-01T10:00:00",
        recent_mentions=["2026-04-20T10:00:00", "2026-04-21T11:00:00"],
        user_fact_reinforce_count=3,
    )

    await pm._aqueue_correction("小天", "主人住在东京", "主人住在大阪", "master")
    fake_llm = _make_llm_mock([{"index": 0, "action": "merge", "text": "主人住在大阪"}])
    with patch("utils.llm_client.create_chat_llm", return_value=fake_llm):
        await pm.resolve_corrections("小天")

    persona = await pm.aensure_persona("小天")
    master_facts = pm._get_section_facts(persona, "master")
    target = next(e for e in master_facts if e.get("text") == "主人住在大阪")
    # Identity + provenance preserved.
    assert target["id"] == "prom_ref_tokyo"
    assert target["source"] == "reflection"
    assert target["source_id"] == "ref_tokyo"
    # Evidence counters preserved — a confirmed entry stays confirmed after
    # a text correction; callers rely on this for rein/disp accounting.
    assert target["reinforcement"] == pytest.approx(2.5)
    assert target["disputation"] == pytest.approx(0.5)
    assert target["rein_last_signal_at"] == "2026-04-01T10:00:00"
    assert target["user_fact_reinforce_count"] == 3
    assert target["recent_mentions"] == [
        "2026-04-20T10:00:00", "2026-04-21T11:00:00",
    ]
    # History still records the old text.
    assert target["version_history"][0]["text"] == "主人住在东京"


@pytest.mark.asyncio
async def test_replace_invalidates_token_count_cache(tmp_path):
    """When text changes, reset the token_count / sha / tokenizer triple."""
    pm, _ = _install(str(tmp_path))
    await _seed_master_fact(
        pm, "小天", "主人住在东京",
        token_count=7,
        token_count_text_sha256="deadbeef" * 8,
        token_count_tokenizer="tiktoken:o200k_base",
    )

    await pm._aqueue_correction("小天", "主人住在东京", "主人住在大阪", "master")
    fake_llm = _make_llm_mock([{"index": 0, "action": "merge", "text": "主人住在大阪"}])
    with patch("utils.llm_client.create_chat_llm", return_value=fake_llm):
        await pm.resolve_corrections("小天")

    persona = await pm.aensure_persona("小天")
    master_facts = pm._get_section_facts(persona, "master")
    target = next(e for e in master_facts if e.get("text") == "主人住在大阪")
    assert target["token_count"] is None
    assert target["token_count_text_sha256"] is None
    assert target["token_count_tokenizer"] is None


@pytest.mark.asyncio
async def test_non_replace_actions_do_not_record_version_history(tmp_path):
    """keep_new / keep_old / keep_both do not write version_history (scope explicitly defined in §2)."""
    pm, _ = _install(str(tmp_path))
    await _seed_master_fact(pm, "小天", "主人喜欢绿茶")

    await pm._aqueue_correction("小天", "主人喜欢绿茶", "主人喜欢红茶", "master")
    fake_llm = _make_llm_mock([{"index": 0, "action": "keep_new"}])
    with patch("utils.llm_client.create_chat_llm", return_value=fake_llm):
        await pm.resolve_corrections("小天")

    persona = await pm.aensure_persona("小天")
    master_facts = pm._get_section_facts(persona, "master")
    new_entry = next(e for e in master_facts if e.get("text") == "主人喜欢红茶")
    assert new_entry.get("version_history") == []


@pytest.mark.asyncio
@pytest.mark.parametrize("bad_text", [None, "", "   ", 123, []])
async def test_merge_with_invalid_or_empty_text_leaves_persona_and_queue_untouched(tmp_path, bad_text):
    """Invalid or empty merge text must be rejected without corrupting persona or resolving the item."""
    pm, _ = _install(str(tmp_path))
    await _seed_master_fact(pm, "小天", "主人喜欢猫")
    await pm._aqueue_correction("小天", "主人喜欢猫", "主人喜欢狗", "master")

    fake_llm = _make_llm_mock([{"index": 0, "action": "merge", "text": bad_text}])
    with patch("utils.llm_client.create_chat_llm", return_value=fake_llm):
        resolved = await pm.resolve_corrections("小天")

    assert resolved == 0
    persona = await pm.aensure_persona("小天")
    master_facts = pm._get_section_facts(persona, "master")
    assert [e["text"] for e in master_facts] == ["主人喜欢猫"]
    assert len(await pm.aload_pending_corrections("小天")) == 1


@pytest.mark.asyncio
async def test_merge_with_unchanged_text_does_not_pollute_version_history(tmp_path):
    """Merging to the identical text must mark resolved and stamp cid without adding a redundant history item."""
    pm, _ = _install(str(tmp_path))
    await _seed_master_fact(pm, "小天", "主人喜欢猫")
    await pm._aqueue_correction("小天", "主人喜欢猫", "主人喜欢小猫", "master")
    pending = await pm.aload_pending_corrections("小天")
    cid = pending[0]["correction_id"]

    fake_llm = _make_llm_mock([{"index": 0, "action": "merge", "text": "  主人喜欢猫  "}])
    with patch("utils.llm_client.create_chat_llm", return_value=fake_llm):
        resolved = await pm.resolve_corrections("小天")

    assert resolved == 1
    persona = await pm.aensure_persona("小天")
    target = pm._get_section_facts(persona, "master")[0]
    assert target["text"] == "主人喜欢猫"
    assert target.get("version_history") == []
    assert target.get("applied_correction_id") == cid
    assert await pm.aload_pending_corrections("小天") == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("action", "with_cid", "extra_result"),
    [
        ("keep_new", True, {}),
        ("keep_new", False, {}),
        ("merge", True, {"text": "合并后文本"}),
        ("keep_both", True, {}),
        ("keep_old", True, {}),
    ],
)
async def test_apply_correction_results_replay_is_idempotent_after_queue_write_failure(
    tmp_path, monkeypatch, action, with_cid, extra_result,
):
    """Replay after asave_persona succeeds and persona_corrections.json save fails must not duplicate entries."""
    from memory.persona import corrections as corrections_mod

    pm, _ = _install(str(tmp_path))
    await _seed_master_fact(pm, "小天", "旧偏好")
    await pm._aqueue_correction("小天", "旧偏好", "新偏好", "master")
    queued = await pm.aload_pending_corrections("小天")
    if not with_cid:
        queued[0].pop("correction_id", None)
        await corrections_mod.atomic_write_json_async(pm._corrections_path("小天"), queued, indent=2, ensure_ascii=False)

    real_write = corrections_mod.atomic_write_json_async

    async def _fail_corrections_write(path, *args, **kwargs):
        if str(path).endswith("persona_corrections.json"):
            raise OSError("disk failure after persona save")
        return await real_write(path, *args, **kwargs)

    monkeypatch.setattr(corrections_mod, "atomic_write_json_async", _fail_corrections_write)
    payload = [{"index": 0, "action": action, **extra_result}]
    with patch("utils.llm_client.create_chat_llm", return_value=_make_llm_mock(payload)):
        with pytest.raises(OSError, match="disk failure"):
            await pm.resolve_corrections("小天")

    # Queue item survived on disk; replay now with restored disk write.
    monkeypatch.setattr(corrections_mod, "atomic_write_json_async", real_write)
    with patch("utils.llm_client.create_chat_llm", return_value=_make_llm_mock(payload)):
        resolved = await pm.resolve_corrections("小天")

    assert resolved == 1
    assert await pm.aload_pending_corrections("小天") == []
    facts = pm._get_section_facts(await pm.aensure_persona("小天"), "master")
    texts = [e["text"] for e in facts]
    if action == "keep_new":
        assert texts == ["新偏好"]
    elif action == "merge":
        assert texts == ["合并后文本"]
    elif action == "keep_both":
        assert texts == ["旧偏好", "新偏好"]
    elif action == "keep_old":
        assert texts == ["旧偏好"]


@pytest.mark.asyncio
async def test_apply_correction_results_checks_corrections_writable_before_saving_persona(tmp_path, monkeypatch):
    """MaintenanceModeError on persona_corrections.json target must abort before asave_persona."""
    from memory.persona import corrections as corrections_mod
    from utils.cloudsave_runtime import MaintenanceModeError

    pm, _ = _install(str(tmp_path))
    await _seed_master_fact(pm, "小天", "旧偏好")
    await pm._aqueue_correction("小天", "旧偏好", "新偏好", "master")

    def _block_corrections(_cm, *, operation="", target=""):
        if "persona_corrections.json" in target:
            raise MaintenanceModeError("read_only", operation=operation, target=target)

    monkeypatch.setattr(corrections_mod, "assert_cloudsave_writable", _block_corrections)
    with patch("utils.llm_client.create_chat_llm", return_value=_make_llm_mock([{"index": 0, "action": "keep_new"}])):
        with pytest.raises(MaintenanceModeError):
            await pm.resolve_corrections("小天")

    facts = pm._get_section_facts(await pm.aensure_persona("小天"), "master")
    assert [e["text"] for e in facts] == ["旧偏好"]


@pytest.mark.asyncio
async def test_merge_with_unchanged_text_still_folds_speaker_trust_and_event_fields(tmp_path):
    """Unchanged merge text must skip version_history while still folding speaker trust and event windows."""
    pm, _ = _install(str(tmp_path))
    await _seed_master_fact(
        pm, "小天", "主人喜欢猫",
        speaker_id="qq:alice",
        speaker_trust=0.9,
        event_when_raw="2026-04",
        event_start_at="2026-04-01T00:00:00",
        event_end_at="2026-04-30T23:59:59",
    )
    await pm._aqueue_correction(
        "小天", "主人喜欢猫", "主人喜欢橘猫", "master",
        old_speaker_provenance={"speaker_id": "qq:alice", "speaker_trust": 0.9},
        new_speaker_provenance={
            "speaker_id": "qq:alice",
            "speaker_trust": 0.2,
            "event_when_raw": "2026-05",
            "event_start_at": "2026-05-01T00:00:00",
            "event_end_at": "2026-05-31T23:59:59",
        },
    )
    cid = (await pm.aload_pending_corrections("小天"))[0]["correction_id"]
    fake_llm = _make_llm_mock([{"index": 0, "action": "merge", "text": "主人喜欢猫"}])
    with patch("utils.llm_client.create_chat_llm", return_value=fake_llm):
        resolved = await pm.resolve_corrections("小天")

    assert resolved == 1
    target = pm._get_section_facts(await pm.aensure_persona("小天"), "master")[0]
    assert target["text"] == "主人喜欢猫"
    assert target.get("version_history") == []
    assert target["speaker_trust"] == pytest.approx(0.2)
    assert target["event_start_at"] == "2026-04-01T00:00:00"
    assert target["event_end_at"] == "2026-05-31T23:59:59"
    assert target["applied_correction_id"] == cid


@pytest.mark.asyncio
@pytest.mark.parametrize("action", ["keep_new", "keep_both"])
async def test_corrections_ignore_permanently_suppressed_matching_new_entry(tmp_path, action):
    """Permanently suppressed rows matching new_text must not suppress creating a live entry."""
    from memory.introspection import PERMANENT_SUPPRESSION_KEY, build_permanent_suppression

    pm, _ = _install(str(tmp_path))
    await _seed_master_fact(pm, "小天", "旧观察")
    await _seed_master_fact(
        pm, "小天", "新观察",
        id="retired_new_1",
        **{PERMANENT_SUPPRESSION_KEY: build_permanent_suppression("stale", "2026-11-01T00:00:00")},
    )
    await pm._aqueue_correction("小天", "旧观察", "新观察", "master")

    fake_llm = _make_llm_mock([{"index": 0, "action": action}])
    with patch("utils.llm_client.create_chat_llm", return_value=fake_llm):
        resolved = await pm.resolve_corrections("小天")

    assert resolved == 1
    facts = pm._get_section_facts(await pm.aensure_persona("小天"), "master")
    live_new = [e for e in facts if e.get("text") == "新观察" and not e.get(PERMANENT_SUPPRESSION_KEY)]
    retired = [e for e in facts if e.get("id") == "retired_new_1"]
    assert len(live_new) == 1
    assert len(retired) == 1
    assert bool(retired[0].get(PERMANENT_SUPPRESSION_KEY)) is True
    assert "applied_correction_id" not in retired[0]
    if action == "keep_new":
        assert all(e.get("text") != "旧观察" for e in facts)


@pytest.mark.asyncio
@pytest.mark.parametrize("arbitrated", [True, False])
async def test_keep_new_with_existing_live_new_entry_merges_history_only_when_arbitrated(tmp_path, arbitrated):
    """When live new_text already exists, trust-arbitrated keep_new retains old history/metadata while non-arbitrated skips history."""
    pm, _ = _install(str(tmp_path))
    prior_snapshot = {"text": "更早版本", "replaced_at": "2026-01-01T00:00:00", "reason": "correction", "source_fact_id": None}
    await _seed_master_fact(
        pm, "小天", "主人喜欢猫",
        speaker_id="qq:bob",
        speaker_trust=0.2 if arbitrated else 0.8,
        event_when_raw="2026-06",
        event_start_at="2026-06-01T00:00:00",
        event_end_at="2026-06-30T23:59:59",
        version_history=[prior_snapshot],
    )
    await _seed_master_fact(
        pm, "小天", "主人不喜欢猫",
        speaker_id="qq:alice",
        speaker_trust=0.95,
    )
    await pm._aqueue_correction(
        "小天", "主人喜欢猫", "主人不喜欢猫", "master",
        old_speaker_provenance={
            "speaker_id": "qq:bob",
            "speaker_trust": 0.2 if arbitrated else 0.8,
            "event_when_raw": "2026-06",
            "event_start_at": "2026-06-01T00:00:00",
            "event_end_at": "2026-06-30T23:59:59",
        },
        new_speaker_provenance={
            "speaker_id": "qq:alice",
            "speaker_trust": 0.9 if arbitrated else 0.8,
            "event_when_raw": "2026-06",
            "event_start_at": "2026-06-01T00:00:00",
            "event_end_at": "2026-06-30T23:59:59",
        },
    )
    cid = (await pm.aload_pending_corrections("小天"))[0]["correction_id"]
    fake_llm = _make_llm_mock([{"index": 0, "action": "keep_old" if arbitrated else "keep_new"}])
    with patch("utils.llm_client.create_chat_llm", return_value=fake_llm):
        resolved = await pm.resolve_corrections("小天")

    assert resolved == 1
    facts = pm._get_section_facts(await pm.aensure_persona("小天"), "master")
    assert [e["text"] for e in facts] == ["主人不喜欢猫"]
    survivor = facts[0]
    assert survivor["applied_correction_id"] == cid
    assert survivor["event_when_raw"] == "2026-06"
    if arbitrated:
        assert survivor["speaker_trust"] == pytest.approx(0.9)
        reasons = [h.get("reason") for h in survivor.get("version_history") or []]
        assert reasons == ["correction", "trust_superseded"]
        assert survivor["version_history"][1]["text"] == "主人喜欢猫"
    else:
        assert survivor.get("version_history") == []


@pytest.mark.asyncio
async def test_keep_new_existing_live_entry_folds_metadata_even_when_old_entry_missing(tmp_path):
    """When old_entries is empty on an existing live new_text entry, keep_new must still fold metadata and cid."""
    pm, _ = _install(str(tmp_path))
    await _seed_master_fact(pm, "小天", "A", speaker_id="qq:alice", speaker_trust=0.95)
    await _seed_master_fact(pm, "小天", "C", speaker_id="qq:alice", speaker_trust=0.95)
    await pm._aqueue_correction("小天", "A", "B", "master")
    await pm._aqueue_correction(
        "小天", "A", "C", "master",
        old_speaker_provenance={"speaker_id": "qq:alice", "speaker_trust": 0.95},
        new_speaker_provenance={
            "speaker_id": "qq:alice",
            "speaker_trust": 0.3,
            "event_when_raw": "2026-07",
            "event_start_at": "2026-07-01T00:00:00",
            "event_end_at": "2026-07-31T23:59:59",
        },
    )
    queued = await pm.aload_pending_corrections("小天")
    cid2 = queued[1]["correction_id"]
    fake_llm = _make_llm_mock([
        {"index": 0, "action": "keep_new"},
        {"index": 1, "action": "keep_new"},
    ])
    with patch("utils.llm_client.create_chat_llm", return_value=fake_llm):
        resolved = await pm.resolve_corrections("小天")

    assert resolved == 2
    facts = pm._get_section_facts(await pm.aensure_persona("小天"), "master")
    entry_c = next(e for e in facts if e["text"] == "C")
    assert entry_c["speaker_trust"] == pytest.approx(0.3)
    assert entry_c["event_when_raw"] == "2026-07"
    assert entry_c["event_start_at"] == "2026-07-01T00:00:00"
    assert entry_c["applied_correction_id"] == cid2
    assert entry_c.get("version_history") == []


@pytest.mark.asyncio
@pytest.mark.parametrize("preseed_c", [False, True])
async def test_chained_keep_new_replay_preserves_inherited_correction_ids(tmp_path, monkeypatch, preseed_c):
    """Chained keep_new A->B->C must inherit prior applied_correction_ids onto C so replay stays idempotent."""
    from memory.persona import corrections as corrections_mod

    pm, _ = _install(str(tmp_path))
    await _seed_master_fact(pm, "小天", "A", speaker_id="qq:alice", speaker_trust=0.9)
    if preseed_c:
        await _seed_master_fact(pm, "小天", "C", speaker_id="qq:alice", speaker_trust=0.9)
    await pm._aqueue_correction("小天", "A", "B", "master")
    await pm._aqueue_correction("小天", "B", "C", "master")
    queued = await pm.aload_pending_corrections("小天")
    cid1, cid2 = queued[0]["correction_id"], queued[1]["correction_id"]

    real_write = corrections_mod.atomic_write_json_async

    async def _fail_corrections_write(path, *args, **kwargs):
        if str(path).endswith("persona_corrections.json"):
            raise OSError("disk failure after persona save")
        return await real_write(path, *args, **kwargs)

    monkeypatch.setattr(corrections_mod, "atomic_write_json_async", _fail_corrections_write)
    payload = [
        {"index": 0, "action": "keep_new"},
        {"index": 1, "action": "keep_new"},
    ]
    with patch("utils.llm_client.create_chat_llm", return_value=_make_llm_mock(payload)):
        with pytest.raises(OSError, match="disk failure"):
            await pm.resolve_corrections("小天")

    monkeypatch.setattr(corrections_mod, "atomic_write_json_async", real_write)
    with patch("utils.llm_client.create_chat_llm", return_value=_make_llm_mock(payload)):
        resolved = await pm.resolve_corrections("小天")

    assert resolved == 2
    assert await pm.aload_pending_corrections("小天") == []
    facts = pm._get_section_facts(await pm.aensure_persona("小天"), "master")
    assert [e["text"] for e in facts] == ["C"]
    survivor = facts[0]
    assert cid1 in (survivor.get("applied_correction_ids") or [])
    assert cid2 in (survivor.get("applied_correction_ids") or [])
    assert survivor["applied_correction_id"] == cid2


@pytest.mark.asyncio
async def test_keep_new_migrating_legacy_string_entry_assigns_non_empty_id(tmp_path):
    """When keep_new migrates an existing plain-string entry to a dict, it must receive a non-empty corr_ ID."""
    pm, _ = _install(str(tmp_path))
    persona = await pm.aensure_persona("小天")
    pm._get_section_facts(persona, "master").extend(["A", "C"])
    await pm.asave_persona("小天", persona)

    await pm._aqueue_correction("小天", "A", "C", "master")
    cid = (await pm.aload_pending_corrections("小天"))[0]["correction_id"]
    with patch("utils.llm_client.create_chat_llm", return_value=_make_llm_mock([{"index": 0, "action": "keep_new"}])):
        resolved = await pm.resolve_corrections("小天")

    assert resolved == 1
    facts = pm._get_section_facts(await pm.aensure_persona("小天"), "master")
    assert len(facts) == 1
    survivor = facts[0]
    assert isinstance(survivor, dict)
    assert survivor["text"] == "C"
    assert str(survivor.get("id") or "").startswith("corr_")
    assert survivor["applied_correction_id"] == cid


@pytest.mark.asyncio
async def test_retired_old_entry_does_not_force_keep_old(tmp_path):
    """Permanently suppressed old entries must be ignored by trust arbitration so keep_new writes live new_text."""
    from memory.introspection import PERMANENT_SUPPRESSION_KEY, build_permanent_suppression

    pm, _ = _install(str(tmp_path))
    await _seed_master_fact(
        pm, "小天", "旧观察",
        id="retired_old_arb",
        speaker_id="qq:alice",
        speaker_trust=0.95,
        **{PERMANENT_SUPPRESSION_KEY: build_permanent_suppression("merged_into", "2026-11-01T00:00:00")},
    )
    await pm._aqueue_correction(
        "小天", "旧观察", "新观察", "master",
        old_speaker_provenance={"speaker_id": "qq:alice", "speaker_trust": 0.95},
        new_speaker_provenance={"speaker_id": "qq:bob", "speaker_trust": 0.2},
    )
    cid = (await pm.aload_pending_corrections("小天"))[0]["correction_id"]

    with patch("utils.llm_client.create_chat_llm", return_value=_make_llm_mock([{"index": 0, "action": "keep_new"}])):
        resolved = await pm.resolve_corrections("小天")

    assert resolved == 1
    assert await pm.aload_pending_corrections("小天") == []
    facts = pm._get_section_facts(await pm.aensure_persona("小天"), "master")
    retired = next(e for e in facts if e.get("id") == "retired_old_arb")
    live = [e for e in facts if not e.get(PERMANENT_SUPPRESSION_KEY)]
    assert bool(retired.get(PERMANENT_SUPPRESSION_KEY)) is True
    assert "applied_correction_id" not in retired
    assert len(live) == 1
    assert live[0]["text"] == "新观察"
    assert live[0]["applied_correction_id"] == cid


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("action", "extra", "expected_text"),
    [
        ("merge", {"text": "融合新观察"}, "融合新观察"),
        ("keep_old", {}, "新观察"),
    ],
)
async def test_retired_old_entry_merge_and_keep_old_preserve_observation(
    tmp_path, monkeypatch, action, extra, expected_text,
):
    """When only a permanently suppressed old entry exists, merge and keep_old must preserve the observation idempotently."""
    from memory.introspection import PERMANENT_SUPPRESSION_KEY, build_permanent_suppression
    from memory.persona import corrections as corrections_mod
    from memory.trust_store import TrustSnapshot

    pm, _ = _install(str(tmp_path))
    await _seed_master_fact(
        pm, "小天", "旧观察",
        id="retired_old_1",
        speaker_id="qq:bob",
        speaker_trust=0.2,
        **{PERMANENT_SUPPRESSION_KEY: build_permanent_suppression("merged_into", "2026-11-01T00:00:00")},
    )
    await pm._aqueue_correction(
        "小天", "旧观察", "新观察", "master",
        old_speaker_provenance={"speaker_id": "qq:bob", "speaker_trust": 0.2},
        new_speaker_provenance={"speaker_id": "qq:alice", "speaker_trust": 0.9},
    )
    cid = (await pm.aload_pending_corrections("小天"))[0]["correction_id"]

    real_write = corrections_mod.atomic_write_json_async

    async def _fail_corrections_write(path, *args, **kwargs):
        if str(path).endswith("persona_corrections.json"):
            raise OSError("disk failure after persona save")
        return await real_write(path, *args, **kwargs)

    monkeypatch.setattr(corrections_mod, "atomic_write_json_async", _fail_corrections_write)
    payload = [{"index": 0, "action": action, **extra}]

    with patch("memory.trust_store.trust_snapshot", return_value=TrustSnapshot({}, loaded=True)):
        with patch("utils.llm_client.create_chat_llm", return_value=_make_llm_mock(payload)):
            with pytest.raises(OSError, match="disk failure"):
                await pm.resolve_corrections("小天")

        monkeypatch.setattr(corrections_mod, "atomic_write_json_async", real_write)
        with patch("utils.llm_client.create_chat_llm", return_value=_make_llm_mock(payload)):
            resolved = await pm.resolve_corrections("小天")

    assert resolved == 1
    assert await pm.aload_pending_corrections("小天") == []
    facts = pm._get_section_facts(await pm.aensure_persona("小天"), "master")
    retired = next(e for e in facts if e.get("id") == "retired_old_1")
    live = [e for e in facts if not e.get(PERMANENT_SUPPRESSION_KEY)]
    assert bool(retired.get(PERMANENT_SUPPRESSION_KEY)) is True
    assert "applied_correction_id" not in retired
    assert len(live) == 1
    assert live[0]["text"] == expected_text
    assert str(live[0].get("id") or "").startswith("corr_")
    assert live[0]["applied_correction_id"] == cid
    if action == "merge":
        assert live[0].get("speaker_provenance_mixed") is True
        assert "speaker_id" not in live[0]
