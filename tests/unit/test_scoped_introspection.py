# -*- coding: utf-8 -*-
"""Weekly introspection of scoped subjects: a group, a member of a group, a
person met across groups, one at a time.

What it reviews is the block ``/scoped_context`` injects for that subject
(``arender_scoped_injected_memory``, the one definition both use), numbered;
persona lines are tagged ``P`` because a subject's persona lines and
impressions share one heading. Every action carries the subject and is
skipped when a target is no longer stamped with it, and a merged entry keeps
the subject stamp (unstamped rows fail closed on scoped reads). A subject is
reviewed when it was never reviewed, or a week later if its content changed.
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from memory.scopes import MemorySubject

NAME = "小天"
GROUP = MemorySubject.group_chat("neko_visit", "room1")
OTHER_GROUP = MemorySubject.group_chat("neko_visit", "room2")
_TAG_RE = re.compile(r'(?m)^- \[\d+(?: P)?(?: RO)?\] ')


def _day(ago: int) -> str:
    return (datetime.now() - timedelta(days=ago)).date().isoformat()


def _now_iso() -> str:
    return datetime.now().isoformat(timespec='seconds')


def _mock_cm(tmpdir: str):
    cm = MagicMock()
    cm.memory_dir = tmpdir
    data = ("主人", NAME, {}, {NAME: {}}, {"human": "主人", "system": "SYS"}, {}, {}, {}, {})
    cm.aget_character_data = AsyncMock(return_value=data)
    cm.get_character_data = MagicMock(return_value=data)
    cm.aget_model_api_config = AsyncMock(return_value={
        "model": "fake", "base_url": "http://fake", "api_key": "sk-fake",
    })
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
    return SimpleNamespace(event_log=event_log, pm=pm, engine=engine, cm=cm)


async def _seed(env):
    persona = await env.pm.aensure_persona(NAME)
    for subject, rows in (
        (GROUP, [("p1", "群里周五一起打游戏", "r_src1"), ("p2", "群里每周五晚上开黑", "r_src2")]),
        (OTHER_GROUP, [("q1", "另一个群爱聊做饭", None)]),
    ):
        section = persona.setdefault(subject.persona_section_key, {
            **subject.as_entry_fields(), "facts": [],
        })
        for entry_id, text, source_id in rows:
            entry = env.pm._normalize_entry(text)
            entry.update({
                "id": entry_id, "source": "reflection_time_driven",
                "source_id": source_id, **subject.as_entry_fields(),
            })
            section["facts"].append(entry)
    private = env.pm._get_section_facts(persona, "master")
    entry = env.pm._normalize_entry("主人喜欢辣条")
    entry.update({"id": "m1"})
    private.append(entry)
    await env.pm.asave_persona(NAME, persona)

    def _refl(rid, text, subject, days, counts):
        return env.engine._normalize_reflection({
            "id": rid, "text": text, "entity": subject.kind, "status": "confirmed",
            "source_fact_ids": [rid + "_f"], "counted_fact_ids": [rid + "_f"],
            "created_at": _now_iso(), "confirmed_at": _now_iso(),
            "reinforcement": 0.5, "rein_last_signal_at": _now_iso(),
            "observed_days": days, "fact_source_counts": counts,
            **subject.as_entry_fields(),
        })

    await env.engine.asave_reflections(NAME, [
        _refl("g1", "群里成为了大家的庇护所", GROUP, [_day(20)], {"user": 2, "ai": 0}),
        _refl("g2", "群里最近在聊新番", GROUP, [_day(30)], {"user": 1, "ai": 1}),
        _refl("g3", "群里大家都追新番", GROUP, [_day(5)], {"user": 2, "ai": 0}),
        _refl("o1", "另一个群的人很热情", OTHER_GROUP, [_day(3)], {"user": 1, "ai": 0}),
        env.engine._normalize_reflection({
            "id": "c1", "text": "主人不喜欢被催", "entity": "master", "status": "confirmed",
            "source_fact_ids": [], "created_at": _now_iso(),
            "reinforcement": 1.5, "rein_last_signal_at": _now_iso(),
        }),
    ])


# ── the numbered render ─────────────────────────────────────────────


@pytest.mark.asyncio
async def test_annotated_scoped_render_matches_injected_render(tmp_path):
    from memory.introspection import RenderAnnotator, arender_scoped_injected_memory

    env = _install(str(tmp_path))
    await _seed(env)
    injected = await arender_scoped_injected_memory(env.pm, env.engine, NAME, [GROUP])
    annotator = RenderAnnotator(mark_persona=True)
    annotated = await arender_scoped_injected_memory(
        env.pm, env.engine, NAME, [GROUP], annotator=annotator,
    )

    assert _TAG_RE.sub('- ', annotated) == injected, "去掉编号后必须与群聊开场注入的一字不差"
    for text in ("群里周五一起打游戏", "群里成为了大家的庇护所"):
        assert text in injected
    for text in ("另一个群爱聊做饭", "另一个群的人很热情", "主人喜欢辣条", "主人不喜欢被催"):
        assert text not in injected, "只看这一个群的记忆，不看别的群或私聊"
    kinds = {line.text: line.kind for line in annotator.lines.values()}
    assert kinds["群里周五一起打游戏"] == "persona"
    assert kinds["群里成为了大家的庇护所"] == "reflection"
    assert re.search(r'- \[\d+ P\] 群里周五一起打游戏', annotated)
    assert re.search(r'- \[\d+\] 群里成为了大家的庇护所', annotated)


# ── a run ───────────────────────────────────────────────────────────


def _number(prompt: str, text: str) -> int:
    match = re.search(r'\[(\d+)(?: P)?(?: RO)?\] ' + re.escape(text), prompt)
    assert match, f"{text!r} not numbered in the prompt"
    return int(match.group(1))


class _FakeThinking:
    def __init__(self, *replies):
        self._replies = list(replies)
        self.prompts: list[str] = []

    async def __call__(self, api_config, prompt, *, timeout, call_label, output_caps=None):
        self.prompts.append(prompt)
        reply = self._replies.pop(0) if self._replies else "[]"
        content = reply(prompt) if callable(reply) else reply
        return SimpleNamespace(content=content), 8192


@pytest.mark.asyncio
async def test_scoped_run_reviews_one_subject_and_keeps_the_stamp(tmp_path):
    from memory.introspection import arun_introspection, is_permanently_suppressed
    from memory.scopes import entry_matches_subject

    env = _install(str(tmp_path))
    await _seed(env)

    def _first(prompt):
        return json.dumps([
            {"action": "suppress", "id": _number(prompt, "群里成为了大家的庇护所"),
             "issue": "hollow", "reason": "空话"},
            {"action": "merge", "ids": [
                _number(prompt, "群里周五一起打游戏"), _number(prompt, "群里每周五晚上开黑"),
            ], "text": "群里每周五晚上一起打游戏", "reason": "同一件事"},
            {"action": "merge", "ids": [
                _number(prompt, "群里最近在聊新番"), _number(prompt, "群里大家都追新番"),
            ], "text": "群里大家在追新番", "reason": "同一件事"},
        ], ensure_ascii=False)

    fake = _FakeThinking(_first, "[]")
    with patch("memory.thinking_llm.ainvoke_thinking", fake):
        result = await arun_introspection(
            NAME, persona_manager=env.pm, reflection_engine=env.engine,
            config_manager=env.cm, subject=GROUP,
        )

    assert result.applied == 3
    # What the model was shown, line by line (the scheduler compares it
    # with the store to tell whether the review saw everything).
    assert ("reflection", "g1", "群里成为了大家的庇护所") in result.shown
    assert ("persona", "p1", "群里周五一起打游戏") in result.shown
    assert not any(entry_id in ("o1", "q1", "c1", "m1") for _k, entry_id, _t in result.shown)
    prompt = fake.prompts[0]
    assert "self_words" in prompt and "self_rule" not in prompt
    assert "主人" not in prompt, "群聊自省不提私聊用户"

    rows = {r["id"]: r for r in await env.engine._aload_reflections_full(NAME)}
    assert is_permanently_suppressed(rows["g1"])
    merged = next(r for r in rows.values() if r.get("text") == "群里大家在追新番")
    assert entry_matches_subject(merged, GROUP), "合并出的印象必须留着群的标记"
    assert merged["status"] == "confirmed"
    assert merged["observed_days"] == [_day(30), _day(5)]
    assert merged["fact_source_counts"] == {"user": 3, "ai": 1}

    persona = await env.pm.aensure_persona(NAME)
    facts = persona[GROUP.persona_section_key]["facts"]
    merged_p = next(e for e in facts if e.get("text") == "群里每周五晚上一起打游戏")
    assert entry_matches_subject(merged_p, GROUP)
    assert set(merged_p["merged_source_ids"]) == {"r_src1", "r_src2"}

    # Nothing outside the subject was touched.
    assert not is_permanently_suppressed(rows["o1"])
    assert not is_permanently_suppressed(rows["c1"])
    assert all(
        not is_permanently_suppressed(e)
        for e in persona[OTHER_GROUP.persona_section_key]["facts"]
    )

    # And the merged lines render for the group, never in the private block.
    from memory.introspection import arender_injected_memory, arender_scoped_injected_memory

    scoped = await arender_scoped_injected_memory(env.pm, env.engine, NAME, [GROUP])
    assert "群里大家在追新番" in scoped and "群里每周五晚上一起打游戏" in scoped
    private = (await arender_injected_memory(env.pm, env.engine, NAME)).text
    assert "群里大家在追新番" not in private and "群里每周五晚上一起打游戏" not in private


@pytest.mark.asyncio
async def test_action_on_another_subjects_entry_is_stale(tmp_path):
    env = _install(str(tmp_path))
    await _seed(env)
    # Same kind (group_chat) and the text the model saw, but another group.
    outcome = await env.engine.aapply_introspection_actions(NAME, [{
        "action": "suppress", "targets": [("o1", "另一个群的人很热情")],
        "entity": "group_chat", "subject": GROUP, "reason": "x", "issue": "hollow",
    }])
    assert outcome == {"applied": 0, "stale": 1}
    persona_outcome = await env.pm.aapply_introspection_actions(NAME, [{
        "action": "suppress", "targets": [("q1", "另一个群爱聊做饭")],
        "entity": OTHER_GROUP.persona_section_key, "subject": GROUP,
        "reason": "x", "issue": "hollow",
    }])
    assert persona_outcome == {"applied": 0, "stale": 1}


@pytest.mark.parametrize("lang", ("zh", "zh-TW", "en", "ja", "ko", "ru", "es", "pt"))
def test_scoped_prompt_in_every_language(lang):
    from config.prompts.prompts_memory import (
        MEMORY_INTROSPECTION_SCOPED_PROMPT,
        get_memory_introspection_scoped_prompt,
    )

    assert lang in MEMORY_INTROSPECTION_SCOPED_PROMPT
    text = get_memory_introspection_scoped_prompt(lang)
    for token in ("{AI_NAME}", "{MEMORY}", "{MAX_ACTIONS}", "[self_words]", "[5 P]",
                  "======以下为当前注入的记忆======", "======以上为当前注入的记忆======"):
        assert token in text, (lang, token)
    for token in ("{MASTER_NAME}", "self_rule", "card_conflict"):
        assert token not in text, (lang, token)


def test_self_words_retirement_blocks_restatement():
    from memory.introspection import ISSUE_CODES, retirement_blocks_restatement

    assert "self_words" in ISSUE_CODES
    assert retirement_blocks_restatement({
        "permanent_suppression": {"at": _now_iso(), "reason": "x", "issue": "self_words"},
    })


# ── scheduling ──────────────────────────────────────────────────────


@pytest.fixture
def maint_state(tmp_path, monkeypatch):
    from app.memory_server import gates, review

    monkeypatch.setattr(gates, '_maint_state', {})
    monkeypatch.setattr(gates, '_maint_state_path', lambda: str(tmp_path / 'idle_maintenance_state.json'))
    monkeypatch.setattr(review, '_review_spawn_locks', {})
    monkeypatch.setattr(review, '_retired_derived_task_names', set())
    monkeypatch.setattr(review, 'introspection_tasks', {})
    return gates


def test_scoped_subject_due_rules(maint_state):
    from app.memory_server.refine_loops import (
        _scoped_introspection_due,
        _scoped_introspection_state_key,
    )
    from config import MEMORY_INTROSPECTION_RETRY_BACKOFF_SECONDS as LADDER

    now = datetime.now()
    key = _scoped_introspection_state_key(GROUP)

    def _state(**values):
        maint_state._maint_state.setdefault(NAME, {})[key] = json.dumps(values)
        return maint_state._maint_view(NAME)

    assert _scoped_introspection_due(maint_state._maint_view(NAME), GROUP, "fp1", now)
    week = (now - timedelta(days=7, minutes=1)).isoformat()
    assert not _scoped_introspection_due(
        _state(at=(now - timedelta(days=6)).isoformat(), fp="fp0"), GROUP, "fp1", now,
    ), "七天内不重复查"
    assert not _scoped_introspection_due(
        _state(at=week, fp="fp1"), GROUP, "fp1", now,
    ), "没有新内容不查"
    assert _scoped_introspection_due(_state(at=week, fp="fp0"), GROUP, "fp1", now)
    assert not _scoped_introspection_due(
        _state(n=1, fail_at=(now - timedelta(seconds=LADDER[0] - 60)).isoformat()),
        GROUP, "fp1", now,
    )
    assert _scoped_introspection_due(
        _state(n=1, fail_at=(now - timedelta(seconds=LADDER[0] + 60)).isoformat()),
        GROUP, "fp1", now,
    )


def test_scoped_state_gives_up_after_retries_and_drops_gone_subjects():
    from app.memory_server.refine_loops import (
        _mutate_scoped_introspection,
        _scoped_introspection_state_key,
    )

    key = _scoped_introspection_state_key(GROUP)
    gone = _scoped_introspection_state_key(OTHER_GROUP)
    state = {gone: json.dumps({"at": "x"}), "introspection_last_run_at": "keep"}
    for attempt in (1, 2):
        _ok, gave_up = _mutate_scoped_introspection(
            key, f"t{attempt}", "fp", True, 2, frozenset({key}), state,
        )
        assert not gave_up
        assert json.loads(state[key])["n"] == attempt
    assert gone not in state, "没有记忆了的主体，状态一并清掉"
    assert state["introspection_last_run_at"] == "keep"
    _ok, gave_up = _mutate_scoped_introspection(
        key, "t3", "fp", True, 2, frozenset({key}), state,
    )
    assert gave_up
    assert json.loads(state[key]) == {"at": "t3", "fp": None}, (
        "重试用完不算查过：下周不管内容变没变都要再查"
    )


async def _scoped_tick_env(tmp_path, maint_state, monkeypatch, run):
    from app.memory_server import refine_loops, runtime
    from memory import introspection

    env = _install(str(tmp_path))
    await _seed(env)
    cm = MagicMock()
    cm.aload_characters = AsyncMock(return_value={'猫娘': {NAME: {}}})
    monkeypatch.setattr(runtime, '_config_manager', cm, raising=False)
    monkeypatch.setattr(runtime, 'persona_manager', env.pm, raising=False)
    monkeypatch.setattr(runtime, 'reflection_engine', env.engine, raising=False)

    async def _passthrough(name, operation, *args, **kwargs):
        return await operation(*args, **kwargs)

    monkeypatch.setattr(refine_loops, 'run_with_character_prompt_locale', _passthrough)
    monkeypatch.setattr(refine_loops, 'aget_subject_prompt_locale', AsyncMock(return_value=None))
    monkeypatch.setattr(maint_state, '_is_idle', lambda: True)
    monkeypatch.setattr(maint_state, '_ais_powerful_memory_enabled', AsyncMock(return_value=True))
    monkeypatch.setattr(introspection, 'arun_introspection', run)
    return env


async def _saw_everything(*_a, subject, **_kw):
    """A completed review that was shown every line of the subject."""
    from app.memory_server.refine_loops import _scoped_introspection_contents
    from memory import introspection

    entries = {s.key: e for s, _fp, e in await _scoped_introspection_contents(NAME)}
    return introspection.IntrospectionResult(rounds=1, shown=set(entries[subject.key]))


async def _add_impression(env, rid: str, subject=GROUP) -> None:
    rows = await env.engine._aload_reflections_full(NAME)
    rows.append(env.engine._normalize_reflection({
        "id": rid, "text": f"新印象 {rid}", "entity": subject.kind,
        "status": "confirmed", "source_fact_ids": [], "created_at": _now_iso(),
        "reinforcement": 0.1, "rein_last_signal_at": _now_iso(),
        **subject.as_entry_fields(),
    }))
    await env.engine.asave_reflections(NAME, rows)


async def _fingerprint(subject) -> str:
    from app.memory_server.refine_loops import _scoped_introspection_subjects

    return {s.key: fp for s, fp in await _scoped_introspection_subjects(NAME)}[subject.key]


def _week_later(maint_state, subject) -> dict:
    """Age the subject's last review by eight days; returns its state."""
    from app.memory_server.refine_loops import (
        _scoped_introspection_state,
        _scoped_introspection_state_key,
    )

    key = _scoped_introspection_state_key(subject)
    state = _scoped_introspection_state(maint_state._maint_state[NAME], key)
    state["at"] = (datetime.now() - timedelta(days=8)).isoformat()
    maint_state._maint_state[NAME][key] = json.dumps(state)
    return state


@pytest.mark.asyncio
async def test_content_written_during_a_review_still_counts_as_new(
    tmp_path, maint_state, monkeypatch,
):
    """The review records the content it was given, not what is there once
    the model has answered: an impression synthesized meanwhile was never
    shown to it."""
    from app.memory_server import refine_loops
    from memory import introspection

    holder = {}

    async def _run(*_a, subject, **_kw):
        await _add_impression(holder["env"], "written_meanwhile", subject)
        return introspection.IntrospectionResult(rounds=1)

    env = await _scoped_tick_env(tmp_path, maint_state, monkeypatch, _run)
    holder["env"] = env
    before = await _fingerprint(GROUP)
    await refine_loops._run_scoped_introspection_for_subject(NAME, GROUP)

    state = _week_later(maint_state, GROUP)
    assert state["fp"] == before
    after = await _fingerprint(GROUP)
    assert after != before
    assert refine_loops._scoped_introspection_due(
        maint_state._maint_view(NAME), GROUP, after, datetime.now(),
    ), "检查期间新写入的印象，下周还要查"


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["final", "transient"])
async def test_a_review_that_did_not_complete_is_retried_next_week(
    tmp_path, maint_state, monkeypatch, failure,
):
    from app.memory_server import refine_loops
    from config import MEMORY_INTROSPECTION_RETRY_BACKOFF_SECONDS as LADDER
    from memory import introspection

    run = AsyncMock(return_value=introspection.IntrospectionResult(
        failure=failure, failure_reason="x",
    ))
    await _scoped_tick_env(tmp_path, maint_state, monkeypatch, run)
    attempts = 1 if failure == "final" else len(LADDER) + 1
    for _ in range(attempts):
        await refine_loops._run_scoped_introspection_for_subject(NAME, GROUP)

    state = _week_later(maint_state, GROUP)
    assert state["fp"] is None and "n" not in state
    assert refine_loops._scoped_introspection_due(
        maint_state._maint_view(NAME), GROUP, await _fingerprint(GROUP), datetime.now(),
    ), "没查成的，下周不管内容变没变都要再查"


@pytest.mark.asyncio
async def test_tick_reviews_a_scoped_subject_once_its_content_changes(
    tmp_path, maint_state, monkeypatch,
):
    from app.memory_server import refine_loops

    run = AsyncMock(side_effect=_saw_everything)
    env = await _scoped_tick_env(tmp_path, maint_state, monkeypatch, run)

    # The private review is not due: only scoped subjects remain.
    maint_state._maint_state.setdefault(NAME, {}).update(
        introspection_last_run_at=_now_iso(), behavior_review_last_run_at=_now_iso(),
    )
    reviewed = []
    for _ in range(3):
        await refine_loops._run_introspection_tick()
        reviewed.append(run.await_args.kwargs.get("subject"))
    assert {s.key for s in reviewed[:2]} == {GROUP.key, OTHER_GROUP.key}
    assert run.await_count == 2, "两个群各查一次，第三次空闲没有可查的"

    # A week later with nothing new: still nothing to review.
    state = maint_state._maint_state[NAME]
    for key in [k for k in state if k.startswith("scoped_introspection:")]:
        value = json.loads(state[key])
        value["at"] = (datetime.now() - timedelta(days=8)).isoformat()
        state[key] = json.dumps(value)
    await refine_loops._run_introspection_tick()
    assert run.await_count == 2

    # New content in one group: that group is reviewed again.
    await _add_impression(env, "g9")
    await refine_loops._run_introspection_tick()
    assert run.await_count == 3
    assert run.await_args.kwargs["subject"].key == GROUP.key


@pytest.mark.asyncio
async def test_unreadable_memory_keeps_the_other_subjects_state(maint_state, monkeypatch):
    """Recording one subject's review while the memory cannot be read must
    not take every other subject for gone: they would all look never
    reviewed and be sent to the model again."""
    from app.memory_server import refine_loops

    other = refine_loops._scoped_introspection_state_key(OTHER_GROUP)
    maint_state._maint_state.setdefault(NAME, {})[other] = json.dumps({"at": "t0", "fp": "fp0"})
    monkeypatch.setattr(
        refine_loops, "_scoped_introspection_contents",
        AsyncMock(side_effect=OSError("busy")),
    )
    await refine_loops._astamp_scoped_introspection(
        NAME, GROUP, transient_failure=False, reviewed_fingerprint="fp1",
    )
    state = maint_state._maint_state[NAME]
    assert json.loads(state[other]) == {"at": "t0", "fp": "fp0"}
    assert json.loads(state[refine_loops._scoped_introspection_state_key(GROUP)])["fp"] == "fp1"


@pytest.mark.asyncio
async def test_a_line_the_review_did_not_see_is_checked_next_week(
    tmp_path, maint_state, monkeypatch,
):
    """An impression in mention cooldown is not in the injected block, so
    the review never saw it; when the cooldown ends nothing in the store
    has to change, yet it must still be checked: the subject is due again
    a week later even with the same content."""
    from app.memory_server import refine_loops
    from memory.introspection import RenderAnnotator, arender_scoped_injected_memory

    holder = {}

    async def _real_view(*_a, subject, **_kw):
        # What the model is shown: the numbered render of this subject.
        annotator = RenderAnnotator(mark_persona=True)
        await arender_scoped_injected_memory(
            holder["env"].pm, holder["env"].engine, NAME, [subject], annotator=annotator,
        )
        from memory import introspection

        return introspection.IntrospectionResult(rounds=1, shown={
            (line.kind, line.entry_id, line.text) for line in annotator.lines.values()
        })

    env = await _scoped_tick_env(tmp_path, maint_state, monkeypatch, _real_view)
    holder["env"] = env
    rows = await env.engine._aload_reflections_full(NAME)
    for row in rows:
        if row["id"] == "g2":
            row["suppress"] = True
            row["suppressed_at"] = _now_iso()
    await env.engine.asave_reflections(NAME, rows)

    await refine_loops._run_scoped_introspection_for_subject(NAME, GROUP)
    state = _week_later(maint_state, GROUP)
    assert state.get("partial") is True
    assert refine_loops._scoped_introspection_due(
        maint_state._maint_view(NAME), GROUP, await _fingerprint(GROUP), datetime.now(),
    ), "没看到的印象（冷却中、被预算挤掉）下周还要查"

    # Shown everything: a week later with the same content, nothing to do.
    await refine_loops._run_scoped_introspection_for_subject(NAME, OTHER_GROUP)
    state = _week_later(maint_state, OTHER_GROUP)
    assert "partial" not in state
    assert not refine_loops._scoped_introspection_due(
        maint_state._maint_view(NAME), OTHER_GROUP,
        await _fingerprint(OTHER_GROUP), datetime.now(),
    )
