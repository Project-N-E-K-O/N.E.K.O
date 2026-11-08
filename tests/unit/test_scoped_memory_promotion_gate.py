# -*- coding: utf-8 -*-
"""Scoped (group / member / cross-group person) memory: who gets into persona.

A group gives no feedback, so the only evidence a scoped impression has is
being seen again in others' words. It is written into its subject's persona
only after being seen on SCOPED_PROMOTION_MIN_OBSERVED_DAYS distinct days,
the first SCOPED_PROMOTION_MIN_AGE_DAYS ago, and only with strong memory on
(nothing cleans up after a promotion otherwise). Each time it is seen again
it gains reinforcement, so it keeps its place in the subject's render budget.
The character's own words (fact source ``ai_disclosure``, labelled by the
whole-group extraction) are no sighting.

The time-driven path also stops promoting what the score-driven paths hold
back: a reflection the user turned against (net score below zero) or one
resting only on the AI's own words with no user signal.
"""
from __future__ import annotations

import asyncio
import json
import os
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from memory.scopes import MemorySubject

NAME = "小天"
GROUP = MemorySubject.group_chat("neko_visit", "room1")
MEMBER = MemorySubject.group_participant("neko_visit", "room1", "peer1")


def _day(ago: int) -> str:
    return (datetime.now() - timedelta(days=ago)).date().isoformat()


def _iso(ago_days: float = 0) -> str:
    return (datetime.now() - timedelta(days=ago_days)).isoformat()


READY_DAYS = [_day(d) for d in (40, 30, 20, 10)]


def _scoped(rid: str, *, days=None, counts=None, subject=GROUP, **extra) -> dict:
    row = {
        "id": rid, "text": f"群里常聊 {rid}", "entity": subject.kind,
        "status": "confirmed", "created_at": _iso(45),
        "confirmed_at": _iso(8), "reinforcement": 0.5,
        "rein_last_signal_at": _iso(1), "disputation": 0.0,
        **subject.as_entry_fields(),
    }
    if days is not None:
        row["observed_days"] = list(days)
    if counts is not None:
        row["fact_source_counts"] = dict(counts)
    row.update(extra)
    return row


def _legacy(rid: str, *, status="confirmed", counts=None, **extra) -> dict:
    row = {
        "id": rid, "text": f"主人喜欢 {rid}", "entity": "master",
        "status": status, "created_at": _iso(20), "confirmed_at": _iso(8),
        "reinforcement": 0.5, "rein_last_signal_at": _iso(1), "disputation": 0.0,
    }
    if counts is not None:
        row["fact_source_counts"] = dict(counts)
    row.update(extra)
    return row


def _promotion_engine(reflections, code=None):
    from memory.persona import PersonaManager
    from memory.reflection.promotion import PromotionMixin

    engine = PromotionMixin.__new__(PromotionMixin)
    engine._persona_manager = SimpleNamespace(
        aensure_persona=AsyncMock(return_value={}),
        aget_persona=AsyncMock(return_value={}),
        retired_persona_hides=lambda persona: (lambda reflection: False),
        aadd_fact=AsyncMock(return_value=code or PersonaManager.FACT_ADDED),
    )
    engine._get_alock = lambda name: asyncio.Lock()
    engine._aload_reflections_full = AsyncMock(return_value=reflections)
    engine.asave_reflections = AsyncMock()
    engine._abatch_mark_surfaced_handled = AsyncMock()
    return engine


def _promoted_texts(engine) -> list[str]:
    return [call.args[1] for call in engine._persona_manager.aadd_fact.await_args_list]


# ── observation days ────────────────────────────────────────────────


def test_others_fact_days_skip_the_characters_own_words():
    from memory.evidence import others_fact_days

    facts = [
        {"id": "a", "created_at": _iso(3), "source": "user_observation"},
        {"id": "b", "created_at": _iso(3)},  # no source: the others' words
        {"id": "c", "created_at": _iso(1), "source": "ai_disclosure"},
        {"id": "d", "created_at": "not a time"},
        {"id": "e", "created_at": _iso(5)},
    ]
    assert others_fact_days(facts) == [_day(5), _day(3)]


def test_merge_observed_days_keeps_the_first_and_the_latest():
    from config import SCOPED_OBSERVED_DAYS_MAX
    from memory.evidence import merge_observed_days

    many = [_day(d) for d in range(SCOPED_OBSERVED_DAYS_MAX + 10, 0, -1)]
    merged = merge_observed_days(many[:20], many[10:])
    assert len(merged) == SCOPED_OBSERVED_DAYS_MAX
    assert merged[0] == many[0], "第一次看到的那天必须留着，30 天从它算"
    assert merged[-1] == many[-1]
    assert merged == sorted(set(merged))


def test_observed_days_of_older_lines():
    from memory.evidence import observed_days

    # Predates the record: its creation day, once.
    assert observed_days({"created_at": _iso(12)}) == [_day(12)]
    # ... unless its counts say it rests only on the character's own words.
    assert observed_days({
        "created_at": _iso(12), "fact_source_counts": {"user": 0, "ai": 3},
    }) == []
    assert observed_days({"observed_days": [_day(2), _day(9), _day(2)]}) == [_day(9), _day(2)]


@pytest.mark.parametrize("days, ready", [
    (READY_DAYS, True),
    (READY_DAYS[1:], False),  # three days only
    ([_day(d) for d in (29, 20, 10, 1)], False),  # four days, first 29 days ago
    ([_day(d) for d in (30, 20, 10, 1)], True),
    ([], False),
])
def test_scoped_promotion_ready(days, ready):
    from memory.evidence import scoped_promotion_ready

    assert scoped_promotion_ready({"observed_days": days}, datetime.now()) is ready


# ── the time-driven pass ────────────────────────────────────────────


@pytest.mark.asyncio
async def test_scoped_promotion_needs_enough_days_with_strong_memory():
    reflections = [
        _scoped("ready", days=READY_DAYS),
        _scoped("three_days", days=READY_DAYS[1:]),
        _scoped("too_recent", days=[_day(d) for d in (25, 20, 10, 1)]),
        # Older line without the record: one day, never enough on its own.
        _scoped("legacy_scoped"),
    ]
    engine = _promotion_engine(reflections)
    await engine.aauto_promote_time_driven(NAME, scoped_only=True)

    status = {r["id"]: r["status"] for r in reflections}
    assert status == {
        "ready": "promoted", "three_days": "confirmed",
        "too_recent": "confirmed", "legacy_scoped": "confirmed",
    }
    assert _promoted_texts(engine) == ["群里常聊 ready"]


@pytest.mark.asyncio
async def test_scoped_never_promotes_with_strong_memory_off():
    reflections = [_scoped("ready", days=READY_DAYS), _legacy("private")]
    engine = _promotion_engine(reflections)
    await engine.aauto_promote_time_driven(NAME)

    assert reflections[0]["status"] == "confirmed", "关闭强力记忆时群聊一律不升格"
    assert reflections[1]["status"] == "promoted", "关闭模式的私聊照旧按时间升格"
    assert _promoted_texts(engine) == ["主人喜欢 private"]


@pytest.mark.asyncio
async def test_scoped_line_on_the_characters_own_words_waits_for_others():
    reflections = [
        _scoped("own_words", days=READY_DAYS, counts={"user": 0, "ai": 4}),
        _scoped("mixed", days=READY_DAYS, counts={"user": 1, "ai": 4}),
        _scoped("others", days=READY_DAYS, counts={"user": 3, "ai": 0}),
    ]
    engine = _promotion_engine(reflections)
    await engine.aauto_promote_time_driven(NAME, scoped_only=True)

    status = {r["id"]: r["status"] for r in reflections}
    assert status == {"own_words": "confirmed", "mixed": "promoted", "others": "promoted"}


@pytest.mark.asyncio
async def test_time_driven_does_not_promote_what_the_score_holds_back():
    """Weak memory, private chat: a line the user turned against, or one
    resting only on the AI's own words with no user signal, stays put in
    both steps; normal lines advance as before."""
    reflections = [
        _legacy("normal"),
        _legacy("rebutted", disputation=1.0, disp_last_signal_at=_iso(1)),
        _legacy("ai_only", counts={"user": 0, "ai": 3}),
        _legacy("ai_only_confirmed", counts={"user": 0, "ai": 3}, user_confirm_count=1),
        _legacy("mixed", counts={"user": 1, "ai": 3}),
        _legacy("p_normal", status="pending", created_at=_iso(8)),
        _legacy("p_ignored", status="pending", created_at=_iso(8),
                reinforcement=-0.2, rein_last_signal_at=_iso(2)),
        _legacy("p_ai_only", status="pending", created_at=_iso(8),
                counts={"user": 0, "ai": 2}),
    ]
    engine = _promotion_engine(reflections)
    await engine.aauto_promote_time_driven(NAME)

    status = {r["id"]: r["status"] for r in reflections}
    assert status == {
        "normal": "promoted", "rebutted": "confirmed",
        "ai_only": "confirmed", "ai_only_confirmed": "promoted",
        "mixed": "promoted",
        "p_normal": "confirmed", "p_ignored": "pending", "p_ai_only": "pending",
    }


@pytest.mark.asyncio
async def test_scoped_promotion_sent_to_arbitration_is_done():
    """A scoped promotion that lands in the correction queue is final: the
    arbitration decides what reaches persona. Left confirmed it would be
    queued again after every resolution, one model call per round."""
    from memory.persona import PersonaManager

    reflections = [_scoped("queued", days=READY_DAYS), _legacy("private_queued")]
    engine = _promotion_engine(reflections, PersonaManager.FACT_QUEUED_CORRECTION)
    await engine.aauto_promote_time_driven(NAME, scoped_only=True)

    assert reflections[0]["status"] == "promoted"
    assert reflections[0]["promoted_via"] == "correction_queue"
    await engine.aauto_promote_time_driven(NAME, scoped_only=True)
    assert engine._persona_manager.aadd_fact.await_count == 1, "送审过的不再送审"

    # Weak memory, private: the queue is never consumed, so it retries.
    await engine.aauto_promote_time_driven(NAME)
    assert reflections[1]["status"] == "confirmed"


# ── seen again: synthesis restatement ───────────────────────────────


def _mock_cm(tmpdir: str):
    cm = MagicMock()
    cm.memory_dir = tmpdir
    character = ("主人", NAME, {}, {}, {"human": "主人", "system": "SYS"}, {}, {}, {}, {})
    cm.aget_character_data = AsyncMock(return_value=character)
    cm.get_character_data = MagicMock(return_value=character)
    api = {"model": "fake-model", "base_url": "http://fake", "api_key": "sk-fake"}
    cm.get_model_api_config = MagicMock(return_value=api)
    cm.aget_model_api_config = AsyncMock(return_value=api)
    cm.get_config_value.return_value = False
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
        engine = ReflectionEngine(fs, pm, event_log=event_log)
        engine._config_manager = cm
    return SimpleNamespace(event_log=event_log, fs=fs, pm=pm, engine=engine, cm=cm)


def _group_fact(fid: str, ago: int, source: str = "user_observation") -> dict:
    return {
        "id": fid, "text": f"群里聊了 {fid}", "entity": "group_chat",
        "importance": 6, "absorbed": False, "source": source,
        "created_at": _iso(ago), "signal_processed": True,
        **GROUP.as_entry_fields(),
    }


async def _synthesize_group(env, facts, answer):
    char_dir = os.path.join(env.cm.memory_dir, NAME)
    os.makedirs(char_dir, exist_ok=True)
    with open(os.path.join(char_dir, "facts.json"), "w", encoding="utf-8") as f:
        json.dump(facts, f, ensure_ascii=False)

    async def _fake_thinking(_api, prompt, **_kw):
        resp = MagicMock()
        resp.content = json.dumps(answer, ensure_ascii=False)
        return resp, None

    with patch("memory.thinking_llm.ainvoke_thinking", _fake_thinking), \
         patch("utils.language_utils.get_global_language", return_value="zh"):
        return await env.engine.synthesize_reflections(NAME, subject=GROUP)


def _skip(covered_by: str, based_on) -> dict:
    return {"skip": True, "covered_by": covered_by, "reflection": "",
            "entity": "relationship", "based_on": based_on}


async def _stored(env, rid):
    rows = await env.engine._aload_reflections_full(NAME)
    return next(r for r in rows if r["id"] == rid)


@pytest.mark.asyncio
async def test_new_scoped_line_records_the_days_of_others_words(tmp_path):
    env = _install(str(tmp_path))
    facts = [_group_fact(f"g{i}", 3) for i in range(4)] + [
        _group_fact("own", 1, "ai_disclosure"),
    ]
    answer = [{"entity": "relationship", "relation_type": "preference",
               "reflection": "群里爱聊游戏", "temporal_scope": "pattern",
               "event_when": None, "based_on": [1, 2, 3, 4, 5]}]
    [created] = await _synthesize_group(env, facts, answer)
    assert created["status"] == "confirmed"
    assert created["observed_days"] == [_day(3)], "角色自己说的话不算被看到"
    assert created["fact_source_counts"] == {"user": 4, "ai": 1}

    own = [_group_fact(f"a{i}", 2, "ai_disclosure") for i in range(5)]
    env2 = _install(str(tmp_path / "own"))
    [only_own] = await _synthesize_group(env2, own, [{
        "entity": "relationship", "relation_type": "preference",
        "reflection": "小天觉得这里很舒服", "temporal_scope": "pattern",
        "event_when": None, "based_on": [1, 2, 3, 4, 5],
    }])
    assert only_own["observed_days"] == []
    assert only_own["reinforcement"] > 0, "纯自述的群聊印象照常显示"


@pytest.mark.asyncio
async def test_restated_scoped_line_is_seen_again_and_moves_to_the_front(tmp_path):
    from config import (
        EVIDENCE_REINFORCEMENT_CAP,
        SCOPED_REOBSERVED_REINFORCEMENT,
    )
    from memory.evidence import evidence_score, initial_reinforcement_from_importance

    env = _install(str(tmp_path))
    old = env.engine._normalize_reflection(_scoped(
        "r_old", days=[_day(30)], counts={"user": 4, "ai": 0},
        reinforcement=0.1, rein_last_signal_at=_iso(30),
        counted_fact_ids=["x1"], source_fact_ids=["x1"],
    ))
    await env.engine.asave_reflections(NAME, [old])
    target = {"kind": "reflection", "id": "r_old", "text": old["text"]}
    env.engine._build_existing_impressions_block = AsyncMock(
        return_value=(f"[r_old] {old['text']}\n", {"r_old": target}),
    )
    facts = [_group_fact(f"g{i}", 2) for i in range(4)] + [
        _group_fact("own", 1, "ai_disclosure"),
    ]
    created = await _synthesize_group(env, facts, [_skip("r_old", [1, 2, 3, 4, 5])])

    assert created == []
    row = await _stored(env, "r_old")
    assert row["observed_days"] == [_day(30), _day(2)]
    assert row["reinforcement"] == pytest.approx(0.1 + SCOPED_REOBSERVED_REINFORCEMENT)
    now = datetime.now()
    assert evidence_score(row, now) > initial_reinforcement_from_importance(10), (
        "被重新看到的印象要排在所有新印象前面"
    )
    assert evidence_score(row, now) <= EVIDENCE_REINFORCEMENT_CAP

    # The same facts once more (a rerun): no new sighting, no second boost.
    facts_by_id = {f["id"]: f for f in facts}
    async with env.engine._get_alock(NAME):
        seen: list[str] = []
        assert await env.engine._arecord_covered_facts_locked(
            NAME, target, sorted(facts_by_id), subject=GROUP,
            facts=list(facts_by_id.values()), reobserved=seen,
        )
    assert seen == []


@pytest.mark.asyncio
async def test_restatement_in_the_characters_own_words_is_no_sighting(tmp_path):
    env = _install(str(tmp_path))
    old = env.engine._normalize_reflection(_scoped(
        "r_old", days=[_day(30)], reinforcement=0.1, rein_last_signal_at=_iso(30),
    ))
    await env.engine.asave_reflections(NAME, [old])
    target = {"kind": "reflection", "id": "r_old", "text": old["text"]}
    env.engine._build_existing_impressions_block = AsyncMock(
        return_value=(f"[r_old] {old['text']}\n", {"r_old": target}),
    )
    own = [_group_fact(f"a{i}", 2, "ai_disclosure") for i in range(5)]
    await _synthesize_group(env, own, [_skip("r_old", [1, 2, 3, 4, 5])])

    row = await _stored(env, "r_old")
    assert row["observed_days"] == [_day(30)]
    assert row["reinforcement"] == pytest.approx(0.1)


@pytest.mark.asyncio
async def test_private_restatement_still_adds_no_score(tmp_path):
    env = _install(str(tmp_path))
    old = env.engine._normalize_reflection(_legacy(
        "r_old", status="pending", reinforcement=0.2, rein_last_signal_at=_iso(3),
    ))
    await env.engine.asave_reflections(NAME, [old])
    target = {"kind": "reflection", "id": "r_old", "text": old["text"]}
    async with env.engine._get_alock(NAME):
        seen: list[str] = []
        assert await env.engine._arecord_covered_facts_locked(
            NAME, target, ["f1"],
            facts=[{"id": "f1", "created_at": _iso(0)}], reobserved=seen,
        )
    assert seen == []
    row = await _stored(env, "r_old")
    assert "observed_days" not in row
    assert row["reinforcement"] == pytest.approx(0.2)


# ── seen again: scoped refine merge ─────────────────────────────────


async def _merge(env, sources):
    from memory.scoped_refine import apply_scoped_reflection_merge

    rows = [env.engine._normalize_reflection(dict(s)) for s in sources]
    await env.engine.asave_reflections(NAME, rows)
    actions = [{
        "action": "merge", "source_ids": [r["id"] for r in rows],
        "produce": {"text": "合并后的一条"},
    }]
    applied = await apply_scoped_reflection_merge(
        env.engine, NAME, GROUP, [dict(r) for r in rows], actions, "hash-1",
    )
    assert applied == 1
    rows = await env.engine._aload_reflections_full(NAME)
    return next(r for r in rows if r.get("merged_from_ids"))


@pytest.mark.asyncio
async def test_merge_of_two_backed_lines_counts_as_seen_again(tmp_path):
    from config import SCOPED_REOBSERVED_REINFORCEMENT

    env = _install(str(tmp_path))
    merged = await _merge(env, [
        _scoped("a", days=[_day(40), _day(20)], counts={"user": 3, "ai": 1},
                counted_fact_ids=["f1", "f2"], reinforcement=0.4),
        _scoped("b", days=[_day(20), _day(5)], counts={"user": 2, "ai": 0},
                counted_fact_ids=["f3"], reinforcement=0.2),
    ])
    assert merged["observed_days"] == [_day(40), _day(20), _day(5)]
    assert merged["fact_source_counts"] == {"user": 5, "ai": 1}
    assert merged["counted_fact_ids"] == ["f1", "f2", "f3"]
    assert merged["reinforcement"] == pytest.approx(0.4 + SCOPED_REOBSERVED_REINFORCEMENT)


@pytest.mark.asyncio
async def test_merge_with_a_line_on_own_words_adds_nothing(tmp_path):
    env = _install(str(tmp_path))
    merged = await _merge(env, [
        _scoped("a", days=[_day(40)], counts={"user": 3, "ai": 0}, reinforcement=0.4),
        _scoped("b", days=[], counts={"user": 0, "ai": 4}, reinforcement=0.1),
    ])
    assert merged["observed_days"] == [_day(40)]
    assert merged["fact_source_counts"] == {"user": 3, "ai": 4}
    assert merged["reinforcement"] == pytest.approx(0.4)


@pytest.mark.asyncio
async def test_merge_of_two_lines_on_own_words_stays_held_back(tmp_path):
    from memory.evidence import awaits_user_signal

    env = _install(str(tmp_path))
    merged = await _merge(env, [
        _scoped("a", days=[], counts={"user": 0, "ai": 2}),
        _scoped("b", days=[], counts={"user": 0, "ai": 3}),
    ])
    assert merged["fact_source_counts"] == {"user": 0, "ai": 5}
    assert awaits_user_signal(merged) is True, "合并不能把纯自述的印象放行"
    assert merged["observed_days"] == []


# ── render: impressions under their subject's heading ───────────────


@pytest.mark.asyncio
async def test_scoped_impressions_render_under_their_subjects_heading(tmp_path):
    from config.prompts.prompts_memory import (
        get_persona_section_header,
        get_scoped_persona_section_header,
    )
    from memory.introspection import arender_scoped_injected_memory

    env = _install(str(tmp_path))
    persona = await env.pm.aensure_persona(NAME)
    section = persona.setdefault(GROUP.persona_section_key, {
        **GROUP.as_entry_fields(), "facts": [], "display_name": "猫猫屋",
    })
    entry = env.pm._normalize_entry("群里周五一起打游戏")
    entry.update({"id": "p1", **GROUP.as_entry_fields()})
    section["facts"].append(entry)
    await env.pm.asave_persona(NAME, persona)
    await env.engine.asave_reflections(NAME, [
        env.engine._normalize_reflection(_scoped("g1", text="群里最近在聊新番")),
        env.engine._normalize_reflection(_scoped(
            "m1", subject=MEMBER, text="对方的猫说话很慢",
        )),
    ])

    with patch("utils.language_utils.get_global_language_full", return_value="zh"):
        rendered = await arender_scoped_injected_memory(
            env.pm, env.engine, NAME, [GROUP, MEMBER],
        )
        group_header = get_scoped_persona_section_header(
            GROUP.kind, GROUP.subject_id, "zh", display_name="猫猫屋",
        )
        member_header = get_scoped_persona_section_header(
            MEMBER.kind, MEMBER.subject_id, "zh",
        )
        shared = get_persona_section_header(
            "confirmed_reflections", "zh", ai_name=NAME, master_name="主人",
        )

    blocks = {
        block.splitlines()[0]: block for block in rendered.split("\n\n")
        if block.startswith("### ")
    }
    assert f"### {shared}" not in blocks, "群聊印象不再挤在一节里"
    group_block = blocks[f"### {group_header}"]
    assert "群里周五一起打游戏" in group_block and "群里最近在聊新番" in group_block
    assert group_block.index("群里周五一起打游戏") < group_block.index("群里最近在聊新番")
    assert "对方的猫说话很慢" in blocks[f"### {member_header}"]
    assert "对方的猫说话很慢" not in group_block


# ── whole-group extraction labels the character's own words ─────────


def _msg(kind: str, content: str):
    return SimpleNamespace(type=kind, content=content)


def test_group_history_has_own_words():
    from memory.facts import FactStore

    mixed = [_msg("human", "对方的猫 来坐"), _msg("ai", "好呀")]
    assert FactStore.group_history_has_own_words(mixed, GROUP) is True
    assert FactStore.group_history_has_own_words(mixed[:1], GROUP) is False
    assert FactStore.group_history_has_own_words(mixed, MEMBER) is False
    assert FactStore.group_history_has_own_words(mixed, None) is False


@pytest.mark.asyncio
async def test_own_words_addendum_only_on_request(tmp_path):
    from config.prompts.prompts_memory import (
        get_fact_extraction_group_source_addendum,
        get_fact_extraction_prompt,
    )

    env = _install(str(tmp_path))
    prompts: list[str] = []

    async def _call(prompt, *_a, **_kw):
        prompts.append(prompt)
        return [{"text": "小天家有地毯", "importance": 5, "source": "ai_disclosure"}]

    env.fs._allm_call_with_retries = _call
    messages = [_msg("human", "对方的猫 你家好暖和"), _msg("ai", "我家铺了地毯")]
    with patch("utils.language_utils.get_global_language", return_value="zh"):
        await env.fs._allm_extract_facts(NAME, messages, speaker_label="群聊成员们")
        await env.fs._allm_extract_facts(
            NAME, messages, speaker_label="群聊成员们", label_own_words=True,
        )
    addendum = get_fact_extraction_group_source_addendum("zh").replace("{LANLAN_NAME}", NAME)
    assert addendum not in prompts[0], "私聊和分人那一路的提示词一个字不改"
    assert prompts[1] == prompts[0] + addendum, "整群那一路只在末尾多一段"
    assert "`source`" not in get_fact_extraction_prompt("zh")


@pytest.mark.asyncio
async def test_extracted_source_label_is_stored(tmp_path):
    env = _install(str(tmp_path))
    env.fs._allm_call_with_retries = AsyncMock(return_value=[
        {"text": "小天家铺了地毯", "importance": 5, "source": "ai_disclosure"},
        {"text": "对方的猫喜欢地毯", "importance": 5, "source": "user_observation"},
    ])
    messages = [_msg("human", "对方的猫 好软"), _msg("ai", "我家铺了地毯")]
    with patch("utils.language_utils.get_global_language", return_value="zh"):
        created = await env.fs.extract_facts(
            messages, NAME, subject=GROUP, speaker_label="群聊成员们",
            label_own_words=True,
        )
    sources = {f["text"]: f["source"] for f in created}
    assert sources == {
        "小天家铺了地毯": "ai_disclosure", "对方的猫喜欢地毯": "user_observation",
    }


LANGS = ("zh", "zh-TW", "en", "ja", "ko", "ru", "es", "pt")


@pytest.mark.parametrize("lang", LANGS)
def test_own_words_addendum_in_every_language(lang):
    from config.prompts.prompts_memory import (
        FACT_EXTRACTION_GROUP_SOURCE_ADDENDUM,
        get_fact_extraction_group_source_addendum,
    )

    assert lang in FACT_EXTRACTION_GROUP_SOURCE_ADDENDUM
    text = get_fact_extraction_group_source_addendum(lang)
    for token in ("{LANLAN_NAME}", '"ai_disclosure"', '"user_observation"', "`source`"):
        assert token in text, (lang, token)
    assert "{MASTER_NAME}" not in text


@pytest.mark.asyncio
async def test_impressions_of_a_secondary_account_head_under_the_primary(tmp_path):
    """A person with two accounts gets one heading, named by the primary
    account, even when every impression was written under the other one."""
    from config.prompts.prompts_memory import get_scoped_persona_section_header
    from memory.scopes import ParticipantGroup

    env = _install(str(tmp_path))
    primary = MemorySubject.participant("qq", "1001")
    secondary = MemorySubject.participant("qq", "2002")
    await env.engine.asave_reflections(NAME, [
        env.engine._normalize_reflection(_scoped("s1", subject=secondary, text="她爱聊猫")),
        env.engine._normalize_reflection(_scoped(
            "s2", subject=secondary, text="她周末常在线", reinforcement=0.9,
        )),
    ])
    group = ParticipantGroup.of(primary, [primary, secondary])
    pending = await env.engine.aget_pending_reflections(
        NAME, subjects=[primary, secondary], include_legacy_private=False,
    )
    confirmed = await env.engine.aget_confirmed_reflections(
        NAME, subjects=[primary, secondary], include_legacy_private=False,
    )
    with patch("utils.language_utils.get_global_language_full", return_value="zh"):
        rendered = await env.pm.arender_persona_markdown(
            NAME, pending, confirmed, subjects=[primary, secondary],
            include_legacy_private=False, participant_groups=[group],
        )
        header = get_scoped_persona_section_header(primary.kind, primary.subject_id, "zh")
    assert rendered.count("### ") == 1
    assert rendered.startswith(f"### {header}")
    assert secondary.subject_id not in rendered
