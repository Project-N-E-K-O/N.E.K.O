# -*- coding: utf-8 -*-
"""Evidence graded by source, plus de-extremization.

The AI's own statements (fact source ``ai_disclosure``) are no evidence about
the user: a reflection built only on them starts at 0, is not confirmed or
promoted by score until the user backs it, and is kept out of the chat prompt
while pending. A feedback reply that only goes on with the topic ("engaged")
adds a little score but no backing. Reinforcement is capped, and the
synthesis / promotion-merge / refine / introspection prompts forbid absolute
words in every language. OFF-mode extraction reads the user's messages only
(see tests/unit/test_memory_server_cache_settle.py).
"""
from __future__ import annotations

import json
import os
import re
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

LANGS = ("zh", "zh-TW", "en", "ja", "ko", "ru", "es", "pt")


def _mock_cm(tmpdir: str):
    cm = MagicMock()
    cm.memory_dir = tmpdir
    character = ("主人", "小天", {}, {}, {"human": "主人", "system": "SYS"}, {}, {}, {}, {})
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
    return event_log, fs, pm, engine, cm


def _reflection(rid: str, text: str, *, status: str = "pending", rein: float = 0.0,
                counts: dict | None = None, confirms: int = 0,
                user_facts: int = 0) -> dict:
    fresh = datetime.now().isoformat(timespec="seconds")
    entry = {
        "id": rid, "text": text, "entity": "master", "status": status,
        "source_fact_ids": [], "created_at": fresh,
        "feedback": None, "next_eligible_at": fresh,
        "reinforcement": rein, "disputation": 0.0,
        "rein_last_signal_at": fresh if rein else None,
        "disp_last_signal_at": None,
        "sub_zero_days": 0, "sub_zero_last_increment_date": None,
        "user_fact_reinforce_count": user_facts,
        "user_confirm_count": confirms,
        "absorbed_into": None,
        "last_promote_attempt_at": None, "promote_attempt_count": 0,
        "promote_blocked_reason": None,
        "recent_mentions": [], "suppress": False, "suppressed_at": None,
    }
    if counts is not None:
        entry["fact_source_counts"] = dict(counts)
    return entry


AI_ONLY = {"user": 0, "ai": 6}
MIXED = {"user": 2, "ai": 4}


# ── which reflections wait for the user ─────────────────────────────


@pytest.mark.parametrize("entry, waits", [
    (_reflection("a", "t", counts=AI_ONLY), True),
    (_reflection("b", "t", counts=AI_ONLY, confirms=1), False),
    (_reflection("c", "t", counts=AI_ONLY, user_facts=1), False),
    (_reflection("d", "t", counts=MIXED), False),
    # Predates the accounting: never held back.
    (_reflection("e", "t"), False),
    # Nothing behind it at all is not "only the AI's words".
    (_reflection("f", "t", counts={"user": 0, "ai": 0}), False),
])
def test_awaits_user_signal(entry, waits):
    from memory.evidence import awaits_user_signal

    assert awaits_user_signal(entry) is waits


def test_snapshot_counts_only_explicit_confirmations():
    from memory.evidence import compute_evidence_snapshot

    now = datetime.now().isoformat()
    base = {"reinforcement": 0.0}
    assert compute_evidence_snapshot(
        base, {"reinforcement": 1.0}, now, "user_confirm",
    )["user_confirm_count"] == 1
    for source in ("user_engage", "user_fact", "user_ignore"):
        assert compute_evidence_snapshot(
            base, {"reinforcement": 0.3}, now, source,
        )["user_confirm_count"] == 0
    assert compute_evidence_snapshot(
        base, {"disputation": 1.0}, now, "user_confirm",
    )["user_confirm_count"] == 0


# ── reinforcement cap ───────────────────────────────────────────────


def test_reinforcement_capped_on_read_and_on_write():
    from config import EVIDENCE_REINFORCEMENT_CAP
    from memory.evidence import compute_evidence_snapshot, effective_reinforcement

    now = datetime.now()
    legacy = {"reinforcement": 160.0, "rein_last_signal_at": now.isoformat()}
    assert effective_reinforcement(legacy, now) == pytest.approx(EVIDENCE_REINFORCEMENT_CAP)

    # Combo reinforcement no longer climbs past the cap.
    entry = {"reinforcement": 2.5, "user_fact_reinforce_count": 5}
    snap = compute_evidence_snapshot(entry, {"reinforcement": 0.5}, now.isoformat(), "user_fact")
    assert snap["reinforcement"] == pytest.approx(EVIDENCE_REINFORCEMENT_CAP)

    # Any write brings a legacy over-cap value down, even a disputation-only one.
    snap = compute_evidence_snapshot(legacy, {"disputation": 1.0}, now.isoformat(), "user_rebut")
    assert snap["reinforcement"] == pytest.approx(EVIDENCE_REINFORCEMENT_CAP)

    # A negative delta on an over-cap legacy value still counts (not 159.8 -> cap).
    snap = compute_evidence_snapshot(legacy, {"reinforcement": -0.2}, now.isoformat(), "user_ignore")
    assert snap["reinforcement"] == pytest.approx(EVIDENCE_REINFORCEMENT_CAP - 0.2)


def test_capped_entry_is_overturned_by_a_few_rebuttals():
    """At 160 a rebuttal of 1.0 could never move the line; at the cap three do."""
    from config import USER_REBUT_DELTA
    from memory.evidence import compute_evidence_snapshot, evidence_score

    now = datetime.now()
    entry = {"reinforcement": 160.0, "rein_last_signal_at": now.isoformat()}
    for _ in range(3):
        entry.update(compute_evidence_snapshot(
            entry, {"disputation": USER_REBUT_DELTA}, now.isoformat(), "user_rebut",
        ))
    assert evidence_score(entry, now) <= 0.0


# ── synthesis ───────────────────────────────────────────────────────


def _write_facts(tmpdir: str, character: str, facts: list[dict]) -> None:
    char_dir = os.path.join(tmpdir, character)
    os.makedirs(char_dir, exist_ok=True)
    with open(os.path.join(char_dir, "facts.json"), "w", encoding="utf-8") as f:
        json.dump(facts, f, ensure_ascii=False)


def _stored_facts(tmp_path) -> list[dict]:
    with open(os.path.join(str(tmp_path), "小天", "facts.json"), encoding="utf-8") as f:
        return json.load(f)


def _fact(fid: str, text: str, importance: int, source: str | None) -> dict:
    fact = {"id": fid, "text": text, "entity": "master",
            "importance": importance, "absorbed": False}
    if source is not None:
        fact["source"] = source
    return fact


async def _synthesize(tmp_path, facts, based_on=None):
    _ev, _fs, _pm, engine, _cm = _install(str(tmp_path))
    _write_facts(str(tmp_path), "小天", facts)
    prompts: list[str] = []
    answer = {"entity": "master", "relation_type": "preference",
              "reflection": "主人喜欢在夜里听歌", "temporal_scope": "pattern",
              "event_when": None}
    if based_on is not None:
        answer["based_on"] = based_on

    async def _fake_thinking(_api, prompt, **_kw):
        prompts.append(prompt)
        resp = MagicMock()
        resp.content = json.dumps(answer, ensure_ascii=False)
        return resp, None

    with patch("memory.thinking_llm.ainvoke_thinking", _fake_thinking), \
         patch("utils.language_utils.get_global_language", return_value="zh"):
        created = await engine.synthesize_reflections("小天")
    assert len(created) == 1 and len(prompts) == 1
    return created[0], prompts[0]


@pytest.mark.asyncio
async def test_synthesis_marks_ai_facts_and_seeds_from_user_facts_only(tmp_path):
    facts = [_fact(f"ai{i}", f"小天说自己喜欢夜曲{i}", 10, "ai_disclosure") for i in range(5)]
    facts.append(_fact("u1", "主人晚上会听歌", 7, None))  # legacy: no source = the user's
    reflection, prompt = await _synthesize(tmp_path, facts)

    assert "小天说自己喜欢夜曲0（小天自述） (importance: 10)" in prompt
    assert "主人晚上会听歌 (importance: 7)" in prompt
    assert "主人晚上会听歌（小天自述）" not in prompt
    # The importance-10 head start belonged to the AI's words; only the
    # user's importance-7 fact counts.
    assert reflection["reinforcement"] == pytest.approx(0.2)
    assert reflection["fact_source_counts"] == {"user": 1, "ai": 5}


@pytest.mark.asyncio
async def test_synthesis_counts_only_the_facts_the_reflection_rests_on(tmp_path):
    """An unrelated user fact elsewhere in the batch is no backing: with
    ``based_on`` naming only the AI's statements, the line starts at 0 and
    waits for the user. It lists only those facts; the whole batch is still
    absorbed."""
    from memory.evidence import awaits_user_signal

    facts = [_fact(f"ai{i}", f"小天说自己喜欢夜曲{i}", 10, "ai_disclosure") for i in range(5)]
    facts.append(_fact("u1", "主人下周要出差", 9, "user_observation"))
    reflection, prompt = await _synthesize(tmp_path, facts, based_on=[1, 2, "3"])

    assert "- [6] 主人下周要出差 (importance: 9)" in prompt
    assert reflection["fact_source_counts"] == {"user": 0, "ai": 3}
    assert reflection["reinforcement"] == 0.0
    assert awaits_user_signal(reflection) is True
    assert reflection["source_fact_ids"] == ["ai0", "ai1", "ai2"]
    stored = _stored_facts(tmp_path)
    assert all(f["absorbed"] for f in stored)


@pytest.mark.asyncio
async def test_refine_counts_a_batch_fact_the_synthesis_left_out(tmp_path):
    """The batch's user fact is absorbed without being counted (``based_on``
    named only the AI's words). When a refine later absorbs it into the line,
    it must count: dedupe is by ``counted_fact_ids``, and a line written before
    per-line ``source_fact_ids`` (the whole batch) has it there uncounted."""
    from memory.evidence import awaits_user_signal
    from memory.refine import annotate_entry

    facts = [_fact(f"ai{i}", f"小天说自己喜欢夜曲{i}", 10, "ai_disclosure") for i in range(5)]
    user_fact = _fact("u1", "主人说夜里会听小天推荐的歌", 8, "user_observation")
    facts.append(user_fact)
    _ev, _fs, _pm, engine, _cm = _install(str(tmp_path))
    _write_facts(str(tmp_path), "小天", facts)

    async def _fake_thinking(_api, _prompt, **_kw):
        resp = MagicMock()
        resp.content = json.dumps({
            "entity": "master", "relation_type": "preference",
            "reflection": "小天喜欢夜曲", "temporal_scope": "pattern",
            "event_when": None, "based_on": [1, 2],
        }, ensure_ascii=False)
        return resp, None

    with patch("memory.thinking_llm.ainvoke_thinking", _fake_thinking), \
         patch("utils.language_utils.get_global_language", return_value="zh"):
        [reflection] = await engine.synthesize_reflections("小天")
    assert reflection["counted_fact_ids"] == ["ai0", "ai1"]
    assert awaits_user_signal(reflection) is True

    cluster = [
        annotate_entry(reflection, type_="reflection", entity="master"),
        annotate_entry(user_fact, type_="fact", entity="master"),
    ]
    actions = [{"action": "modify", "source_id": reflection["id"],
                "absorbed_from_fact_ids": ["u1"],
                "produce": {"text": "小天喜欢夜曲，主人也会听"}, "reason": "x"}]
    assert await engine.apply_refine_actions("小天", "master", cluster, actions, "h") == 1
    [row] = [r for r in await engine._aload_reflections_full("小天") if r["id"] == reflection["id"]]
    assert row["fact_source_counts"] == {"user": 1, "ai": 2}
    assert row["counted_fact_ids"] == ["ai0", "ai1", "u1"]
    assert awaits_user_signal(row) is False

    # A refine merge dedupes the same way.
    r_a = _reflection("r_a", "小天觉得夜曲好听", counts={"user": 0, "ai": 2})
    r_a.update(source_fact_ids=["a1", "a2", "u2"], counted_fact_ids=["a1", "a2"])
    r_b = _reflection("r_b", "小天睡前爱哼歌", counts={"user": 0, "ai": 1})
    r_b.update(source_fact_ids=["a3"], counted_fact_ids=["a3"])
    await engine.asave_reflections("小天", [r_a, r_b])
    u2 = {"id": "u2", "text": "主人说睡前会听歌", "importance": 7}
    cluster = [annotate_entry(r, type_="reflection", entity="master") for r in (r_a, r_b)]
    cluster.append(annotate_entry(u2, type_="fact", entity="master"))
    actions = [{"action": "merge", "source_ids": ["r_a", "r_b"], "absorbed_from_fact_ids": ["u2"],
                "produce": {"text": "小天和主人睡前都听歌", "relation_type": "habit",
                            "temporal_scope": "pattern"}}]
    assert await engine.apply_refine_actions("小天", "master", cluster, actions, "h2") == 1
    merged = next(r for r in await engine._aload_reflections_full("小天")
                  if r["text"] == "小天和主人睡前都听歌")
    assert merged["fact_source_counts"] == {"user": 1, "ai": 3}
    assert merged["counted_fact_ids"] == ["a1", "a2", "a3", "u2"]


@pytest.mark.asyncio
async def test_synthesis_without_usable_based_on_counts_the_whole_batch(tmp_path):
    facts = [_fact(f"ai{i}", f"小天说自己喜欢夜曲{i}", 10, "ai_disclosure") for i in range(5)]
    facts.append(_fact("u1", "主人晚上会听歌", 9, "user_observation"))
    reflection, _prompt = await _synthesize(
        tmp_path, facts, based_on=["x", 0, 99, True, 1.5, None],
    )
    assert reflection["fact_source_counts"] == {"user": 1, "ai": 5}
    assert reflection["reinforcement"] == pytest.approx(0.6)


# ── several reflections per synthesis call ──────────────────────────


def _line(text: str, based_on, **extra) -> dict:
    line = {"entity": "master", "relation_type": "preference", "reflection": text,
            "temporal_scope": "pattern", "event_when": None, "based_on": based_on}
    line.update(extra)
    return line


async def _synthesize_answer(tmp_path, facts, answer, engine=None):
    if engine is None:
        _ev, _fs, _pm, engine, _cm = _install(str(tmp_path))
    _write_facts(str(tmp_path), "小天", facts)
    calls: list[str] = []

    async def _fake_thinking(_api, prompt, **_kw):
        calls.append(prompt)
        resp = MagicMock()
        resp.content = json.dumps(answer, ensure_ascii=False)
        return resp, None

    with patch("memory.thinking_llm.ainvoke_thinking", _fake_thinking), \
         patch("utils.language_utils.get_global_language", return_value="zh"):
        created = await engine.synthesize_reflections("小天")
    return created, calls, engine


def _topic_facts() -> list[dict]:
    return [
        _fact("f1", "主人每天晚上听歌", 8, "user_observation"),
        _fact("f2", "主人睡前一定要听歌", 7, "user_observation"),
        _fact("f3", "主人在学日语", 8, "user_observation"),
        _fact("f4", "主人每周上两次日语课", 7, "user_observation"),
        _fact("f5", "小天说自己怕打雷", 9, "ai_disclosure"),
        _fact("f6", "小天说下雨天会躲进被子", 6, "ai_disclosure"),
    ]


@pytest.mark.asyncio
async def test_synthesis_writes_one_line_per_topic_up_to_the_cap(tmp_path):
    from config import REFLECTION_SYNTHESIS_MAX_PER_BATCH
    from memory.evidence import awaits_user_signal
    from memory.reflection import _reflection_id_from_facts

    facts = _topic_facts()
    answer = [
        _line("主人睡前习惯听歌", [1, 2]),
        _line("主人在认真学日语", [3, 4]),
        _line("小天怕打雷", [5, 6], entity="neko", relation_type="self_awareness"),
        _line("多出来的第四条", [1]),
    ]
    created, calls, engine = await _synthesize_answer(tmp_path, facts, answer)

    assert len(calls) == 1
    assert len(created) == REFLECTION_SYNTHESIS_MAX_PER_BATCH == 3
    by_text = {r["text"]: r for r in created}
    assert by_text["主人睡前习惯听歌"]["source_fact_ids"] == ["f1", "f2"]
    assert by_text["主人在认真学日语"]["source_fact_ids"] == ["f3", "f4"]
    assert by_text["小天怕打雷"]["fact_source_counts"] == {"user": 0, "ai": 2}
    assert awaits_user_signal(by_text["小天怕打雷"]) is True
    assert by_text["主人睡前习惯听歌"]["reinforcement"] == pytest.approx(0.4)

    batch_id = _reflection_id_from_facts(sorted(f["id"] for f in facts))
    assert created[0]["id"] == batch_id
    assert len({r["id"] for r in created}) == 3
    assert all(r["synthesis_batch_id"] == batch_id for r in created)
    stored = await engine.aload_reflections("小天")
    assert {r["id"] for r in stored} == {r["id"] for r in created}
    facts_on_disk = _stored_facts(tmp_path)
    assert all(f["absorbed"] for f in facts_on_disk)


@pytest.mark.asyncio
async def test_synthesized_batch_is_not_written_twice(tmp_path):
    """A batch already written (found by synthesis_batch_id, not only by the
    first line's id) skips the model and just absorbs the facts again."""
    from memory.reflection import _reflection_id_from_facts

    _ev, _fs, _pm, engine, _cm = _install(str(tmp_path))
    facts = _topic_facts()
    batch_id = _reflection_id_from_facts(sorted(f["id"] for f in facts))
    earlier = _reflection("ref_other", "主人在认真学日语")
    earlier["synthesis_batch_id"] = batch_id
    await engine.asave_reflections("小天", [earlier])

    created, calls, _engine = await _synthesize_answer(
        tmp_path, facts, [_line("不该写入", [1])], engine=engine,
    )
    assert created == [] and calls == []
    facts_on_disk = _stored_facts(tmp_path)
    assert all(f["absorbed"] for f in facts_on_disk)


@pytest.mark.asyncio
async def test_synthesis_skips_a_restated_line_and_keeps_the_rest(tmp_path):
    _ev, _fs, _pm, engine, _cm = _install(str(tmp_path))
    target = {"kind": "reflection", "id": "ref_old", "text": "主人睡前听歌"}
    covered_calls: list[list[str]] = []

    async def _covered(_name, tgt, fact_ids, *, subject=None, facts=()):
        covered_calls.append(list(fact_ids))
        return tgt is target

    engine._build_existing_impressions_block = AsyncMock(
        return_value=("[ref_old] 主人睡前听歌\n", {"ref_old": target}),
    )
    engine._arecord_covered_facts_locked = _covered
    answer = [
        _line("", [1, 2], skip=True, covered_by="ref_old"),
        _line("主人在认真学日语", [3, 4]),
        _line("主人在认真学日语", [3]),  # same line twice is one memory
    ]
    created, _calls, _engine = await _synthesize_answer(tmp_path, _topic_facts(), answer, engine=engine)

    assert covered_calls == [["f1", "f2"]]
    assert [r["text"] for r in created] == ["主人在认真学日语"]
    assert created[0]["source_fact_ids"] == ["f3", "f4"]


@pytest.mark.asyncio
async def test_user_restating_an_ai_only_line_backs_it(tmp_path):
    """The user's words restating a pending line that rested only on the AI's
    own: synthesis skips it as covered, and those words now count as the
    user's backing (it is no longer held back). No score is added."""
    from memory.evidence import awaits_user_signal

    _ev, _fs, _pm, engine, _cm = _install(str(tmp_path))
    old = _reflection("r_old", "小天觉得主人睡前离不开歌", counts={"user": 0, "ai": 3})
    old.update(source_fact_ids=["a1", "a2", "a3"], counted_fact_ids=["a1", "a2", "a3"])
    await engine.asave_reflections("小天", [old])
    target = {"kind": "reflection", "id": "r_old", "text": "小天觉得主人睡前离不开歌"}
    engine._build_existing_impressions_block = AsyncMock(
        return_value=("[r_old] 小天觉得主人睡前离不开歌\n", {"r_old": target}),
    )
    answer = [_line("", [1, 5], skip=True, covered_by="r_old")]
    created, _calls, _engine = await _synthesize_answer(tmp_path, _topic_facts(), answer, engine=engine)

    assert created == []
    [row] = [r for r in await engine._aload_reflections_full("小天") if r["id"] == "r_old"]
    assert row["fact_source_counts"] == {"user": 1, "ai": 4}
    assert row["counted_fact_ids"] == ["a1", "a2", "a3", "f1", "f5"]
    assert set(row["covered_fact_ids"]) == {"f1", "f5"}
    assert awaits_user_signal(row) is False
    assert row["reinforcement"] == 0.0

    # The same facts covered again are not counted twice.
    facts = {f["id"]: f for f in _topic_facts()}
    async with engine._get_alock("小天"):
        assert await engine._arecord_covered_facts_locked(
            "小天", target, ["f1", "f5"], facts=[facts["f1"], facts["f5"]],
        )
    [row] = [r for r in await engine._aload_reflections_full("小天") if r["id"] == "r_old"]
    assert row["fact_source_counts"] == {"user": 1, "ai": 4}


@pytest.mark.asyncio
async def test_synthesis_of_only_ai_facts_starts_at_zero(tmp_path):
    facts = [_fact(f"ai{i}", f"小天说自己怕打雷{i}", 10, "ai_disclosure") for i in range(6)]
    reflection, _prompt = await _synthesize(tmp_path, facts)

    assert reflection["reinforcement"] == 0.0
    assert reflection["fact_source_counts"] == {"user": 0, "ai": 6}


# ── promotion gate ──────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_engaged_replies_do_not_confirm_ai_only_reflection(tmp_path):
    """Four "engaged" replies push the score past 1.0 but are no backing; one
    explicit confirmation is. Goes through aapply_signal, so the counter is the
    one the event carries."""
    from config import USER_CONFIRM_DELTA, USER_ENGAGED_DELTA

    _ev, _fs, _pm, engine, _cm = _install(str(tmp_path))
    await engine.asave_reflections("小天", [
        _reflection("r_ai", "小天觉得主人怕黑", counts=AI_ONLY),
        _reflection("r_legacy", "主人喜欢猫", rein=0.0),
    ])
    for rid in ("r_ai", "r_legacy"):
        for _ in range(4):
            await engine.aapply_signal("小天", rid, {"reinforcement": USER_ENGAGED_DELTA},
                                       source="user_engage")
    await engine.aauto_promote_stale("小天")
    status = {r["id"]: r["status"] for r in await engine._aload_reflections_full("小天")}
    assert status["r_ai"] == "pending"
    # No accounting, no gate: unchanged behavior.
    assert status["r_legacy"] == "confirmed"

    recorded = engine._event_log.arecord_and_save
    payloads: list[dict] = []

    async def _spy(name, event_type, payload, **kw):
        payloads.append(dict(payload))
        return await recorded(name, event_type, payload, **kw)

    with patch.object(engine._event_log, "arecord_and_save", _spy):
        await engine.aapply_signal("小天", "r_ai", {"reinforcement": USER_CONFIRM_DELTA},
                                   source="user_confirm")
    # The event carries the counter, so a replay restores the backing.
    assert payloads[-1]["user_confirm_count"] == 1
    await engine.aauto_promote_stale("小天")
    rows = {r["id"]: r for r in await engine._aload_reflections_full("小天")}
    assert rows["r_ai"]["user_confirm_count"] == 1
    assert rows["r_ai"]["status"] == "confirmed"


def test_sync_auto_promote_stale_applies_the_same_gate(tmp_path):
    _ev, _fs, _pm, engine, _cm = _install(str(tmp_path))
    engine.save_reflections("小天", [
        _reflection("r_ai", "小天觉得主人怕黑", rein=1.5, counts=AI_ONLY),
        _reflection("r_mixed", "主人喜欢猫", rein=1.5, counts=MIXED),
    ])
    engine.auto_promote_stale("小天")
    status = {r["id"]: r["status"] for r in engine.load_reflections("小天")}
    assert status == {"r_ai": "pending", "r_mixed": "confirmed"}


@pytest.mark.asyncio
async def test_score_driven_promotion_skips_ai_only_reflection(tmp_path):
    _ev, _fs, _pm, engine, _cm = _install(str(tmp_path))
    await engine.asave_reflections("小天", [
        _reflection("r_ai", "小天觉得主人怕黑", status="confirmed", rein=2.5, counts=AI_ONLY),
        _reflection("r_backed", "小天觉得主人怕冷", status="confirmed", rein=2.5,
                    counts=AI_ONLY, user_facts=1),
    ])
    promote = AsyncMock(return_value="promote_fresh")
    with patch.object(engine, "_apromote_with_merge", promote):
        await engine._aauto_promote_score_driven("小天")
    assert [call.args[1]["id"] for call in promote.await_args_list] == ["r_backed"]


@pytest.mark.asyncio
async def test_promote_fresh_records_source_counts_on_persona_entry(tmp_path):
    _ev, _fs, pm, engine, _cm = _install(str(tmp_path))
    reflection = _reflection("r_mixed", "主人喜欢在夜里听歌", status="confirmed",
                             rein=2.5, counts=MIXED)
    await engine.asave_reflections("小天", [reflection])
    await pm.asave_persona("小天", {"master": {"facts": []}})
    with patch.object(engine, "_allm_call_promotion_merge",
                      AsyncMock(return_value={"action": "promote_fresh", "reason": "new"})):
        assert await engine._apromote_with_merge("小天", reflection) == "promote_fresh"
    persona = await pm.aget_persona("小天")
    entry = next(e for e in persona["master"]["facts"] if e["text"] == "主人喜欢在夜里听歌")
    assert entry["fact_source_counts"] == MIXED


def test_replay_restores_user_confirm_count(tmp_path):
    from memory.evidence_handlers import make_reflection_evidence_handler

    _ev, _fs, _pm, engine, _cm = _install(str(tmp_path))
    engine.save_reflections("小天", [_reflection("r_ai", "小天觉得主人怕黑", counts=AI_ONLY)])
    apply = make_reflection_evidence_handler(engine)
    assert apply("小天", {"reflection_id": "r_ai", "user_confirm_count": 1}) is True
    with open(engine._reflections_path("小天"), encoding="utf-8") as f:
        row = next(r for r in json.load(f) if r["id"] == "r_ai")
    assert row["user_confirm_count"] == 1


# ── refine / introspection keep the accounting ──────────────────────


def test_split_and_merge_carry_source_counts_and_confirmations():
    from memory._reflection.refine import build_merge_reflection, build_split_reflection

    now = datetime.now()
    kwargs = {"id_factory": lambda text: f"ref_{text}", "normalizer": lambda e: e}
    src = _reflection("a", "t", counts=AI_ONLY, confirms=1)
    split = build_split_reflection(src, {"text": "x"}, "master", now, split_count=2, **kwargs)
    assert split["fact_source_counts"] == AI_ONLY
    assert split["user_confirm_count"] == 1

    other = _reflection("b", "t", counts=AI_ONLY)
    merged = build_merge_reflection(
        [src, other], ["f_user"], {"text": "m"}, "master", now,
        absorbed_facts=[{"id": "f_user", "text": "u"}], **kwargs,
    )
    assert merged["fact_source_counts"] == {"user": 1, "ai": 12}
    assert merged["user_confirm_count"] == 1

    # A source without counts: the merged line has no accounting (no gate).
    legacy = build_merge_reflection([other, _reflection("c", "t")], [], {"text": "m"},
                                    "master", now, **kwargs)
    assert "fact_source_counts" not in legacy

    # Backing from the user's own words survives too (it used to reset to 0,
    # which would hold the merged line back again).
    from memory.evidence import awaits_user_signal

    backed = _reflection("d", "t", counts=AI_ONLY, user_facts=2)
    for produced in (
        build_split_reflection(backed, {"text": "x"}, "master", now, split_count=2, **kwargs),
        build_merge_reflection([backed, other], [], {"text": "m"}, "master", now, **kwargs),
    ):
        assert produced["user_fact_reinforce_count"] == 2
        assert awaits_user_signal(produced) is False


@pytest.mark.asyncio
async def test_refine_apply_counts_absorbed_facts_on_merge_and_modify(tmp_path):
    """Through ``apply_refine_actions``: the cluster's fact rows join the
    accounting, so a line that absorbed one of the user's facts no longer
    waits for the user."""
    from memory.evidence import awaits_user_signal
    from memory.refine import annotate_entry

    _ev, _fs, _pm, engine, _cm = _install(str(tmp_path))
    rows = [
        _reflection("r1", "小天觉得主人怕黑", counts=AI_ONLY),
        _reflection("r2", "小天觉得主人夜里会醒", counts=AI_ONLY),
        _reflection("r3", "小天觉得主人怕冷", counts=AI_ONLY),
    ]
    await engine.asave_reflections("小天", rows)
    user_fact = {"id": "f_user", "text": "主人说自己夜里不敢关灯", "importance": 7}
    cluster = [annotate_entry(r, type_="reflection", entity="master") for r in rows]
    cluster.append(annotate_entry(user_fact, type_="fact", entity="master"))
    actions = [
        {"action": "merge", "source_ids": ["r1", "r2"], "absorbed_from_fact_ids": ["f_user"],
         "produce": {"text": "主人夜里怕黑", "relation_type": "trait", "temporal_scope": "pattern"}},
        {"action": "modify", "source_id": "r3", "absorbed_from_fact_ids": ["f_user"],
         "produce": {"text": "主人怕冷也怕黑"}, "reason": "x"},
    ]
    assert await engine.apply_refine_actions("小天", "master", cluster, actions, "h") == 2
    by_text = {r["text"]: r for r in await engine._aload_reflections_full("小天")}
    assert by_text["主人夜里怕黑"]["fact_source_counts"] == {"user": 1, "ai": 12}
    assert by_text["主人怕冷也怕黑"]["fact_source_counts"] == {"user": 1, "ai": 6}
    assert not awaits_user_signal(by_text["主人夜里怕黑"])
    assert not awaits_user_signal(by_text["主人怕冷也怕黑"])


def test_refine_modify_counts_absorbed_user_fact():
    from memory.evidence import awaits_user_signal
    from memory.reflection.refinement import RefinementMixin

    src = _reflection("a", "t", counts=AI_ONLY)
    src["source_fact_ids"] = ["f_ai"]
    RefinementMixin._apply_refine_modify(
        src, "新文本", "refine", datetime.now().isoformat(),
        absorbed_fact_ids=["f_ai", "f_user"],
        absorbed_facts=[
            {"id": "f_ai", "source": "ai_disclosure"},  # already counted
            {"id": "f_user", "text": "u"},
        ],
    )
    assert src["fact_source_counts"] == {"user": 1, "ai": 6}
    assert awaits_user_signal(src) is False


def test_introspection_merge_keeps_user_confirmation():
    from memory.evidence import merged_evidence_state

    state = merged_evidence_state([
        _reflection("a", "t", confirms=1), _reflection("b", "t"),
    ])
    assert state["user_confirm_count"] == 1
    # Persona sources never carry the key and do not get it.
    assert "user_confirm_count" not in merged_evidence_state([{"reinforcement": 1.0}])


# ── chat prompt render ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_render_hides_ai_only_pending_but_followups_keep_it(tmp_path):
    _ev, _fs, pm, engine, _cm = _install(str(tmp_path))
    persona = {"master": {"facts": []}}
    pm._personas["小天"] = persona

    async def _aensure(_name):
        return persona
    pm.aensure_persona = _aensure  # type: ignore[assignment]
    pm.aupdate_suppressions = AsyncMock()

    ai_only = _reflection("r_ai", "小天觉得主人怕黑", counts=AI_ONLY)
    backed = _reflection("r_backed", "小天觉得主人怕冷", counts=AI_ONLY, confirms=1)
    mixed = _reflection("r_mixed", "主人喜欢猫", counts=MIXED)
    md = await pm.arender_persona_markdown(
        "小天", pending_reflections=[ai_only, backed, mixed], confirmed_reflections=[],
    )
    assert "小天觉得主人怕冷" in md
    assert "主人喜欢猫" in md
    assert "小天觉得主人怕黑" not in md

    past = (datetime.now() - timedelta(minutes=1)).isoformat()
    ai_only["next_eligible_at"] = past
    assert [r["id"] for r in engine._filter_followup_candidates([ai_only])] == ["r_ai"]


# ── feedback: "engaged" ─────────────────────────────────────────────


def test_feedback_prompt_categories_match_accepted_kinds():
    from config.prompts.prompts_memory import REFLECTION_FEEDBACK_PROMPT
    from memory.reflection.surfacing import REFLECTION_FEEDBACK_KINDS

    assert set(REFLECTION_FEEDBACK_PROMPT) == set(LANGS)
    for lang, prompt in REFLECTION_FEEDBACK_PROMPT.items():
        kinds = set(re.findall(r"^- ([a-z]+): ", prompt, flags=re.M))
        assert kinds == REFLECTION_FEEDBACK_KINDS, lang


@pytest.mark.asyncio
async def test_check_feedback_records_engaged(tmp_path):
    _ev, _fs, _pm, engine, _cm = _install(str(tmp_path))
    await engine.asave_surfaced("小天", [
        {"reflection_id": "r1", "text": "主人喜欢猫", "feedback": None},
    ])

    class _FakeLLM:
        def __init__(self, *a, **kw):
            pass

        async def ainvoke(self, prompt):
            resp = MagicMock()
            resp.content = '[{"reflection_id": "r1", "feedback": "engaged"}]'
            return resp

        async def aclose(self):
            return None

    async def _create(*a, **kw):
        return _FakeLLM()

    with patch("utils.llm_client.create_chat_llm_async", _create):
        await engine.check_feedback("小天", ["猫确实很可爱，我家那只今天又闹了"])
    surfaced = await engine.aload_surfaced("小天")
    assert surfaced[0]["feedback"] == "engaged"


@pytest.mark.asyncio
async def test_post_turn_engaged_feedback_adds_small_signal():
    from app import memory_server
    from config import USER_ENGAGED_DELTA
    from memory.event_log import EVIDENCE_SOURCE_USER_ENGAGE
    from utils.llm_client import AIMessage, HumanMessage

    fake_fact_store = MagicMock()
    fake_fact_store.extract_facts = AsyncMock(return_value=[])
    fake_persona_manager = MagicMock()
    fake_persona_manager.arecord_mentions = AsyncMock(return_value=None)
    engine = MagicMock()
    engine.arecord_mentions = AsyncMock(return_value=None)
    engine.aload_surfaced = AsyncMock(return_value=[{"reflection_id": "r1", "feedback": None}])
    engine.check_feedback = AsyncMock(return_value=[{"reflection_id": "r1", "feedback": "engaged"}])
    engine.aapply_signal = AsyncMock(return_value=True)
    engine.areject_promotion = AsyncMock()
    engine.aauto_promote_time_driven = AsyncMock(return_value=0)

    with patch.object(memory_server.runtime, "fact_store", fake_fact_store), \
         patch.object(memory_server.runtime, "persona_manager", fake_persona_manager), \
         patch.object(memory_server.runtime, "reflection_engine", engine), \
         patch.object(memory_server.signal_extraction, "_signal_check_record_turn", MagicMock()), \
         patch.object(memory_server.gates, "_ais_powerful_memory_enabled", AsyncMock(return_value=False)):
        await memory_server._run_post_turn_signals(
            [AIMessage(content="你好像很喜欢猫？"), HumanMessage(content="我家猫今天又闹了")],
            "小天",
        )

    engine.aapply_signal.assert_awaited_once_with(
        "小天", "r1", {"reinforcement": USER_ENGAGED_DELTA}, source=EVIDENCE_SOURCE_USER_ENGAGE,
    )
    engine.areject_promotion.assert_not_awaited()


# ── prompts ─────────────────────────────────────────────────────────


def test_reflection_prompt_explains_the_ai_mark_in_every_language():
    from config.prompts.prompts_memory import REFLECTION_AI_DISCLOSURE_MARK, REFLECTION_PROMPT

    assert set(REFLECTION_AI_DISCLOSURE_MARK) == set(REFLECTION_PROMPT) == set(LANGS)
    for lang in LANGS:
        mark = REFLECTION_AI_DISCLOSURE_MARK[lang]
        assert "{LANLAN_NAME}" in mark, lang
        assert mark.strip() in REFLECTION_PROMPT[lang], lang
        # Asked for in the instructions and present in the JSON template.
        assert REFLECTION_PROMPT[lang].count("based_on") >= 2, lang
        assert '"based_on": [' in REFLECTION_PROMPT[lang], lang
        # Several memories per call: an array template and the cap stated.
        assert '\n[{"entity": ' in REFLECTION_PROMPT[lang], lang
        assert "3" in REFLECTION_PROMPT[lang].split("{EXISTING_IMPRESSIONS_BLOCK}")[1][:200], lang


# The words each language uses for "absolute wording" in the rule.
_ABSOLUTE_RULE = {
    "zh": "绝对的说法", "zh-TW": "絕對的說法", "en": "absolute words",
    "ja": "断定的な言い方", "ko": "단정적인 표현", "ru": "абсолютные слова",
    "es": "palabras absolutas", "pt": "palavras absolutas",
}


@pytest.mark.parametrize("name", [
    "REFLECTION_PROMPT", "PROMOTION_MERGE_PROMPT",
    "MEMORY_REFINE_PROMPT", "MEMORY_INTROSPECTION_PROMPT",
])
def test_absolute_words_rule_in_every_language(name):
    from config.prompts.prompts_memory import (
        MEMORY_INTROSPECTION_PROMPT,
        MEMORY_REFINE_PROMPT,
        PROMOTION_MERGE_PROMPT,
        REFLECTION_PROMPT,
    )

    table = {
        "REFLECTION_PROMPT": REFLECTION_PROMPT,
        "PROMOTION_MERGE_PROMPT": PROMOTION_MERGE_PROMPT,
        "MEMORY_REFINE_PROMPT": MEMORY_REFINE_PROMPT,
        "MEMORY_INTROSPECTION_PROMPT": MEMORY_INTROSPECTION_PROMPT,
    }[name]
    assert set(table) == set(LANGS)
    for lang in LANGS:
        assert _ABSOLUTE_RULE[lang] in table[lang], (name, lang)
