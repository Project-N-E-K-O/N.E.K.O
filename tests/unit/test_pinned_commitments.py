# -*- coding: utf-8 -*-
"""Unit tests for the pinned section's promises, dated expiry and retirement.

Contract groups:

1. Promises (``kind: "commitment"``): the narrow ai_disclosure opening, the
   time-or-condition rule, kept-promise reports; entries written before kinds
   existed read as facts.
2. Lifetime: a known end of the matter brings expiry forward to one day after
   it, never past the tier ceiling; a renewal without an end keeps the end.
3. Retirement in the resolve call: by date, by a cited candidate, never a
   long-lived entry without evidence, never on a changed entry.
4. The daily idle-time check: when it runs, verbatim-quote evidence, what is
   kept when unsure, rev conflicts, the conversation budget, and the idle
   loop's scheduling.
5. Proactive chat: the promise due soonest leads the memory cues, with an
   offer cooldown; rendering and prompt tables.
"""
from __future__ import annotations

import asyncio
import json
import os
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from config import (
    PINNED_COMMITMENT_SURFACE_COOLDOWN_HOURS,
    PINNED_FACT_REVIEW_CONVERSATION_MAX_TOKENS,
    PINNED_FACT_REVIEW_INTERVAL_HOURS,
)
from memory import pinned_facts as pinned_mod
from memory.pinned_facts import PINNED_FACTS_FILENAME, pin_candidates_from_extracted
from tests.unit.test_pinned_facts import (
    _DAY,
    _LOCALES,
    _NAME,
    _T0,
    _Model,
    _candidate,
    _install_model,
    _pin,
    _read_file,
    _store,
)

_HOUR = 3600.0


# ── helpers ──────────────────────────────────────────────────────────


def _when(*, start=None, end=None) -> dict:
    return {
        "start": None if start is None else {"offset": start, "unit": "day"},
        "end": None if end is None else {"offset": end, "unit": "day"},
    }


def _promise(text, ttl=3, **extra) -> dict:
    """A Stage-1 entry the model flagged as a promise."""
    return {"text": text, "importance": 8, "entity": "neko", "pin": True,
            "pin_ttl_days": ttl, "pin_kind": "commitment", **extra}


def _add(store, candidate, *, now=_T0) -> dict:
    """Add one candidate (as apply_decisions receives it) and return its entry."""
    store.apply_decisions(_NAME, [candidate], [("add", None)], now=now)
    return next(e for e in store.get_active(_NAME, now=now) if e["text"] == candidate["text"])


def _write_raw(tmp_path, facts, history=()) -> None:
    folder = os.path.join(str(tmp_path), _NAME)
    os.makedirs(folder, exist_ok=True)
    with open(os.path.join(folder, PINNED_FACTS_FILENAME), "w", encoding="utf-8") as f:
        json.dump({"version": 1, "facts": list(facts), "history": list(history)}, f,
                  ensure_ascii=False)


class _ReviewModel:
    """Stands in for ``create_chat_llm_async``; records every prompt it is sent."""

    def __init__(self, answer=None, *, error: Exception | None = None):
        self.answer = answer
        self.error = error
        self.prompts: list[str] = []

    async def __call__(self, *args, **kwargs):
        model = self

        class _LLM:
            async def ainvoke(self, prompt):
                model.prompts.append(prompt)
                if model.error is not None:
                    raise model.error
                return SimpleNamespace(content=json.dumps(model.answer, ensure_ascii=False))

            async def aclose(self):
                return None

        return _LLM()


def _install_review_model(monkeypatch, store, model: _ReviewModel) -> _ReviewModel:
    store._config_manager.aget_model_api_config = AsyncMock(
        return_value={"model": "m", "base_url": "http://127.0.0.1:1", "api_key": "k"},
    )
    monkeypatch.setattr("utils.llm_client.create_chat_llm_async", model)
    return model


def _loader(messages):
    """A conversation loader that records the ``since`` it is asked for."""
    asked: list[float] = []

    async def load(since):
        asked.append(since)
        return list(messages)

    load.asked = asked
    return load


def _between(prompt: str, head: str, tail: str) -> str:
    return prompt[prompt.index(head) + len(head):prompt.index(tail)]


# ── 1. promises ──────────────────────────────────────────────────────


def test_a_promise_needs_a_time_or_a_condition():
    extracted = [
        _promise("小天答应明天提醒主人带准考证", event_when=_when(start=1)),
        _promise("小天答应等主人考完把故事讲完", pin_condition="等主人考完"),
        _promise("小天说会一直陪着主人"),
        _promise("小天说有空再聊", pin_condition="   "),
        _pin("小天说自己喜欢秋天"),
        _promise("小天已经提醒主人带准考证", pin_done=True),
    ]

    candidates = pin_candidates_from_extracted(extracted, default_source="ai_disclosure")

    # Pleasantries (no time, no condition) and plain self-disclosure stay out;
    # a kept-promise report needs neither.
    assert [(c["text"], c["kind"], c.get("fulfilled", False)) for c in candidates] == [
        ("小天答应明天提醒主人带准考证", "commitment", False),
        ("小天答应等主人考完把故事讲完", "commitment", False),
        ("小天已经提醒主人带准考证", "commitment", True),
    ]


def test_a_condition_given_only_in_its_field_stays_with_the_promise():
    candidates = pin_candidates_from_extracted([
        _promise("小天答应给主人讲完那个故事", pin_condition="等主人考完"),
        _promise("Tian promised to finish the story", pin_condition="after the  exam"),
        _promise("小天答应等主人考完把故事讲完", pin_condition="等主人考完"),
    ], default_source="ai_disclosure")

    assert [c["text"] for c in candidates] == [
        "小天答应给主人讲完那个故事（等主人考完）",
        "Tian promised to finish the story (after the exam)",
        "小天答应等主人考完把故事讲完",
    ]


def test_a_promise_from_path_b_enters_as_an_open_commitment(tmp_path):
    store = _store(tmp_path)

    asyncio.run(store.aadmit_extracted(
        _NAME, [_promise("小天答应明天提醒主人带准考证", event_when=_when(start=1))],
        default_source="ai_disclosure", now=_T0,
    ))

    [entry] = store.get_active(_NAME, now=_T0)
    assert (entry["kind"], entry["status"], entry["entity"]) == ("commitment", "open", "neko")
    assert entry["due_at"] == pytest.approx(_T0 + _DAY, abs=_HOUR)
    assert entry["expire_at"] == pytest.approx(_T0 + 2 * _DAY, abs=_HOUR)


def test_entries_written_before_kinds_read_as_facts(tmp_path):
    from config.prompts.prompts_memory import get_pinned_facts_section_header

    store = _store(tmp_path)
    legacy = {"id": "pin_0000aaaa", "text": "主人下周三考试", "entity": "master",
              "ttl_days": 14, "created_at": _T0, "text_at": _T0, "updated_at": _T0,
              "expire_at": _T0 + 14 * _DAY, "renew_count": 0, "rev": 0}
    _write_raw(tmp_path, [legacy])

    [entry] = store.get_active(_NAME, now=_T0)
    assert (entry["kind"], entry["due_at"]) == ("fact", None)
    assert "status" not in entry
    assert store.render_prompt_block(_NAME, "zh", now=_T0) == (
        "### " + get_pinned_facts_section_header("zh") + "\n"
        "- 主人下周三考试（记于 2026-11-02，到 2026-11-16）"
    )

    # The next write keeps it as it was, now with the new fields.
    store.apply_decisions(_NAME, [_candidate("主人周六去北京", 3)], [("add", None)], now=_T0 + 1)
    stored = _read_file(tmp_path)["facts"][0]
    assert (stored["kind"], stored["expire_at"], stored["rev"]) == ("fact", _T0 + 14 * _DAY, 0)


def test_only_open_promises_are_live(tmp_path):
    store = _store(tmp_path)
    base = {"entity": "neko", "kind": "commitment", "ttl_days": 14, "created_at": _T0,
            "text_at": _T0, "updated_at": _T0, "expire_at": _T0 + 14 * _DAY}
    _write_raw(tmp_path, [
        dict(base, id="pin_000000aa", text="小天答应周末陪主人看电影", status="done"),
        dict(base, id="pin_000000bb", text="小天答应提醒主人浇花", status=["?"]),
    ])

    # A kept one neither renders nor counts; a garbled status reads as open.
    assert [e["id"] for e in store.get_active(_NAME, now=_T0)] == ["pin_000000bb"]
    assert "陪主人看电影" not in store.render_prompt_block(_NAME, "zh", now=_T0)


# ── 2. lifetime ──────────────────────────────────────────────────────


def test_a_dated_event_leaves_one_day_after_it_ends(tmp_path):
    store = _store(tmp_path)

    asyncio.run(store.aadmit_extracted(
        _NAME, [_pin("主人下周三考试", 14, event_when=_when(start=9, end=9))], now=_T0,
    ))

    [exam] = store.get_active(_NAME, now=_T0)
    assert exam["due_at"] == pytest.approx(_T0 + 9 * _DAY, abs=_HOUR)
    assert exam["expire_at"] == pytest.approx(_T0 + 10 * _DAY, abs=_HOUR)
    assert store.get_active(_NAME, now=exam["expire_at"] - 60)
    assert store.get_active(_NAME, now=exam["expire_at"] + 60) == []


def test_an_end_beyond_the_tier_never_extends_it(tmp_path):
    store = _store(tmp_path)

    asyncio.run(store.aadmit_extracted(
        _NAME, [_pin("主人下个月出国", 14, event_when=_when(end=40))], now=_T0,
    ))

    [trip] = store.get_active(_NAME, now=_T0)
    assert trip["expire_at"] == _T0 + 14 * _DAY


def test_only_a_promise_takes_its_start_as_its_due_date(tmp_path):
    store = _store(tmp_path)

    asyncio.run(store.aadmit_extracted(_NAME, [
        _pin("主人感冒了", 3, event_when=_when(start=0)),
        _promise("小天答应明天提醒主人吃药", 3, event_when=_when(start=1)),
    ], now=_T0))

    by_text = {e["text"]: e for e in store.get_active(_NAME, now=_T0)}
    # "caught a cold" says nothing about when it is over.
    assert by_text["主人感冒了"]["due_at"] is None
    assert by_text["主人感冒了"]["expire_at"] == _T0 + 3 * _DAY
    assert by_text["小天答应明天提醒主人吃药"]["expire_at"] == pytest.approx(
        _T0 + 2 * _DAY, abs=_HOUR,
    )


def test_saying_a_dated_event_again_without_its_date_keeps_its_end(tmp_path):
    store = _store(tmp_path)
    _add(store, _candidate("主人下周三考试", 14, kind="fact", due_at=_T0 + 9 * _DAY))

    # Thursday's "my exam on Wednesday" without a date renews the entry.
    store.apply_decisions(
        _NAME, [_candidate("主人下周三考试", 14, kind="fact")], [("add", None)],
        now=_T0 + 3 * _DAY,
    )

    [exam] = store.get_active(_NAME, now=_T0 + 3 * _DAY)
    assert exam["renew_count"] == 1
    assert exam["expire_at"] == _T0 + 10 * _DAY


def test_a_later_account_that_dates_the_end_caps_the_entry(tmp_path):
    store = _store(tmp_path)
    _add(store, _candidate("主人下周三考试", 14, kind="fact"))

    # The next day it turns out the exam is tomorrow.
    store.apply_decisions(
        _NAME, [_candidate("主人下周三考试", 14, kind="fact", due_at=_T0 + 2 * _DAY)],
        [("add", None)], now=_T0 + _DAY,
    )

    [exam] = store.get_active(_NAME, now=_T0 + _DAY)
    assert (exam["due_at"], exam["expire_at"]) == (_T0 + 2 * _DAY, _T0 + 3 * _DAY)


# ── 3. retirement in the resolve call ────────────────────────────────


def test_the_resolve_call_retires_what_is_over_by_date_but_not_long_lived_entries(
    tmp_path, monkeypatch,
):
    store = _store(tmp_path)
    promise = _add(store, _candidate("小天答应周三早上提醒主人带准考证", 14, kind="commitment"))
    allergy = _add(store, _candidate("主人对花生过敏", 365, kind="fact"))
    model = _install_model(monkeypatch, _Model([
        {"candidate": 1, "action": "add", "target": None},
        {"candidate": None, "action": "retire", "target": promise["id"],
         "outcome": "past", "reason": "周三已经过了"},
        {"candidate": None, "action": "retire", "target": allergy["id"],
         "outcome": "past", "reason": "记了很久了"},
    ]))

    counts = asyncio.run(store.aadmit_extracted(
        _NAME, [_pin("主人周六去北京", 3)], now=_T0 + 5 * _DAY,
    ))

    assert counts["retired"] == 1
    assert [e["text"] for e in store.get_active(_NAME, now=_T0 + 5 * _DAY)] == [
        "主人对花生过敏", "主人周六去北京",
    ]
    [record] = [h for h in _read_file(tmp_path)["history"] if h["action"] == "retire"]
    assert (record["id"], record["outcome"], record["status"], record["reason"]) == (
        promise["id"], "past", "expired", "周三已经过了",
    )
    assert "答应于" in model.prompts[0]


def test_a_cited_candidate_retires_an_entry_and_is_not_added(tmp_path, monkeypatch):
    store = _store(tmp_path)
    allergy = _add(store, _candidate("主人对花生过敏", 365, kind="fact"))
    _install_model(monkeypatch, _Model([
        {"candidate": 1, "action": "retire", "target": allergy["id"],
         "outcome": "invalid", "reason": "主人说已经脱敏了"},
    ]))

    counts = asyncio.run(store.aadmit_extracted(
        _NAME, [_pin("主人说花生过敏已经治好了", 365)], now=_T0 + _DAY,
    ))

    assert (counts["retired"], counts.get("added", 0)) == (1, 0)
    assert store.get_active(_NAME, now=_T0 + _DAY) == []


def test_evidence_older_than_the_entry_retires_nothing(tmp_path, monkeypatch):
    store = _store(tmp_path)
    exam = _add(store, _candidate("主人周五考试", 14, kind="fact"), now=_T0 + 2 * _DAY)
    _install_model(monkeypatch, _Model([
        {"candidate": 1, "action": "retire", "target": exam["id"],
         "outcome": "invalid", "reason": "考试取消了"},
    ]))

    counts = asyncio.run(store.aadmit_extracted(
        _NAME, [_pin("主人的考试取消了", 14)], noted_at=_T0 + _DAY, now=_T0 + 3 * _DAY,
    ))

    assert counts["retired"] == 0
    assert [e["id"] for e in store.get_active(_NAME, now=_T0 + 3 * _DAY)] == [exam["id"]]


@pytest.mark.parametrize("answer_shape", ["retire", "remove"])
def test_a_kept_promise_report_marks_the_promise_done(tmp_path, monkeypatch, answer_shape):
    store = _store(tmp_path)
    promise = _add(store, _candidate("小天答应明天提醒主人带准考证", 3, kind="commitment"))
    answer = (
        {"candidate": 1, "action": "retire", "target": promise["id"],
         "outcome": "done", "reason": "已经提醒过了"}
        if answer_shape == "retire" else
        {"candidate": 1, "action": "remove", "target": promise["id"]}
    )
    model = _install_model(monkeypatch, _Model([answer]))

    asyncio.run(store.aadmit_extracted(
        _NAME, [_promise("小天已经提醒主人带准考证", pin_done=True)],
        default_source="ai_disclosure", now=_T0 + _HOUR,
    ))

    assert store.get_active(_NAME, now=_T0 + _HOUR) == []
    record = _read_file(tmp_path)["history"][-1]
    assert (record["id"], record["status"]) == (promise["id"], "done")
    assert "承诺已兑现" in model.prompts[0]


def test_a_kept_promise_report_never_pins_itself(tmp_path, monkeypatch):
    store = _store(tmp_path)
    other = _add(store, _candidate("主人周六去北京", 3, kind="fact"))
    _install_model(monkeypatch, _Model([{"candidate": 1, "action": "add", "target": None}]))

    asyncio.run(store.aadmit_extracted(
        _NAME, [_promise("小天已经提醒主人带准考证", pin_done=True)],
        default_source="ai_disclosure", now=_T0 + _HOUR,
    ))

    assert [e["id"] for e in store.get_active(_NAME, now=_T0 + _HOUR)] == [other["id"]]


def test_a_retirement_decided_on_an_older_version_is_dropped(tmp_path):
    store = _store(tmp_path)
    exam = _add(store, _candidate("主人周五考试", 14, kind="fact"))
    store.apply_decisions(_NAME, [_candidate("主人周五考试", 14)], [("add", None)], now=_T0 + 1)

    counts = store.apply_decisions(
        _NAME, [_candidate("主人周六去北京", 3)], [("add", None)], now=_T0 + 2,
        expected_revs={exam["id"]: 0}, retry_stale=False,
        retirements=[{"target": exam["id"], "outcome": "past", "reason": "", "noted_at": _T0 + 2}],
    )

    assert counts["retired"] == 0
    assert exam["id"] in {e["id"] for e in store.get_active(_NAME, now=_T0 + 2)}


def test_parse_retirements_validates_shape():
    results = [
        {"action": "retire", "target": "pin_a", "outcome": "done", "reason": "ok", "candidate": 2},
        {"action": "retire", "target": "pin_a", "outcome": "past"},
        {"action": "retire", "target": "pin_zz", "outcome": "past"},
        {"action": "retire", "target": "pin_b", "outcome": "maybe"},
        {"action": "RETIRE", "target": " pin_c ", "outcome": "Past", "evidence": "  "},
        {"candidate": 1, "action": "add", "target": None},
        "junk",
    ]

    parsed = pinned_mod.parse_retirements(results, {"pin_a", "pin_b", "pin_c"}, 3)

    assert [(r["target"], r["outcome"], r["candidate"], r["evidence"]) for r in parsed] == [
        ("pin_a", "done", 1, None),
        ("pin_c", "past", None, None),
    ]


# ── 4. the daily check ───────────────────────────────────────────────


def test_the_daily_check_retires_on_a_verbatim_quote(tmp_path, monkeypatch):
    store = _store(tmp_path)
    promise = _add(store, _candidate("小天答应给主人推荐一本书", 14, kind="commitment"))
    model = _install_review_model(monkeypatch, store, _ReviewModel([
        {"target": promise["id"], "action": "retire", "outcome": "done",
         "evidence": "“你上次说的书我收到了”", "reason": "主人说收到了"},
    ]))
    load = _loader([
        {"at": _T0 - _HOUR, "role": "human", "text": "你上次说的书我收到了吗"},
        {"at": _T0 + _DAY, "role": "human", "text": "对了，你上次说的书我收到了，很好看"},
    ])

    counts = asyncio.run(store.areview(_NAME, load, now=_T0 + 2 * _DAY))

    assert counts["retired"] == 1
    record = _read_file(tmp_path)["history"][-1]
    assert (record["action"], record["status"], record["noted_at"]) == ("retire", "done", _T0 + _DAY)
    assert load.asked == [_T0]
    [prompt] = model.prompts
    assert f"[{promise['id']}]" in prompt
    assert "你上次说的书我收到了，很好看" in _between(
        prompt, "======以下为对话======", "======以上为对话======",
    )


@pytest.mark.parametrize("evidence", [
    "主人说书到了",          # a paraphrase, not a quote
    "收",                    # too short to prove anything
    None,                    # no evidence at all
    "你上次说的书我收到了吗",  # only in a message from before the promise
    "我一定给你推荐一本好书",  # the promise itself, which dates the entry
])
def test_the_daily_check_keeps_an_entry_without_checkable_evidence(
    tmp_path, monkeypatch, evidence,
):
    store = _store(tmp_path)
    promise = _add(store, _candidate("小天答应给主人推荐一本书", 14, kind="commitment"))
    _install_review_model(monkeypatch, store, _ReviewModel([
        {"target": promise["id"], "action": "retire", "outcome": "done",
         "evidence": evidence, "reason": "?"},
    ]))
    load = _loader([
        {"at": _T0 - _HOUR, "role": "human", "text": "你上次说的书我收到了吗"},
        {"at": _T0, "role": "ai", "text": "我一定给你推荐一本好书"},
        {"at": _T0 + _DAY, "role": "human", "text": "今天好累，收拾了一下午房间"},
    ])

    counts = asyncio.run(store.areview(_NAME, load, now=_T0 + 2 * _DAY))

    assert counts["retired"] == 0
    assert [e["id"] for e in store.get_active(_NAME, now=_T0 + 2 * _DAY)] == [promise["id"]]


@pytest.mark.parametrize(("evidence", "retired"), [
    ("ok", 0),                                # too short to prove anything
    ("got it", 0),
    ("ook you recommended arrived", 0),       # starts inside "book"
    ("the book you recommend", 0),            # ends inside "recommended"
    ("the book you recommended arrived", 1),
])
def test_a_quote_in_a_spaced_script_must_be_whole_words(
    tmp_path, monkeypatch, evidence, retired,
):
    store = _store(tmp_path)
    promise = _add(store, _candidate("Tian promised to recommend a book", 14, kind="commitment"))
    _install_review_model(monkeypatch, store, _ReviewModel([
        {"target": promise["id"], "action": "retire", "outcome": "done",
         "evidence": evidence, "reason": "?"},
    ]))
    load = _loader([
        {"at": _T0 + _DAY, "role": "human",
         "text": "ok so the book you recommended arrived today, got it"},
    ])

    counts = asyncio.run(store.areview(_NAME, load, now=_T0 + 2 * _DAY))

    assert counts["retired"] == retired


def test_the_daily_check_retires_by_date_only_what_can_be_over_by_date(tmp_path, monkeypatch):
    store = _store(tmp_path)
    allergy = _add(store, _candidate("主人对花生过敏", 365, kind="fact"))
    undated = _add(store, _candidate("主人下周三考试", 14, kind="fact"))
    upcoming = _add(store, _candidate("主人下下周五面试", 14, kind="fact", due_at=_T0 + 12 * _DAY))
    _install_review_model(monkeypatch, store, _ReviewModel([
        {"target": entry["id"], "action": "retire", "outcome": "past", "evidence": None}
        for entry in (allergy, undated, upcoming)
    ]))

    counts = asyncio.run(store.areview(_NAME, _loader([]), now=_T0 + 10 * _DAY))

    assert counts["retired"] == 1
    assert [e["id"] for e in store.get_active(_NAME, now=_T0 + 10 * _DAY)] == [
        allergy["id"], upcoming["id"],
    ]


def test_the_daily_check_asks_only_when_the_section_has_not_been_looked_at(
    tmp_path, monkeypatch,
):
    store = _store(tmp_path)
    review = _install_review_model(monkeypatch, store, _ReviewModel([]))
    load = _loader([])

    # Empty section: nothing to look at, not even the conversation.
    assert asyncio.run(store.areview(_NAME, load, now=_T0)) == {}
    assert (review.prompts, load.asked) == ([], [])

    asyncio.run(store.aadmit_extracted(_NAME, [_pin("主人下周三考试", 14)], now=_T0))
    _install_model(monkeypatch, _Model([{"candidate": 1, "action": "add", "target": None}]))
    asyncio.run(store.aadmit_extracted(_NAME, [_pin("主人周六去北京", 3)], now=_T0 + _HOUR))
    resolved_at = _T0 + _HOUR
    assert _read_file(tmp_path)["reviewed_at"] == resolved_at

    # The admission's resolve call looked at the whole section an hour ago.
    asyncio.run(store.areview(_NAME, load, now=resolved_at + 20 * _HOUR))
    assert review.prompts == []

    later = resolved_at + PINNED_FACT_REVIEW_INTERVAL_HOURS * _HOUR + 1
    asyncio.run(store.areview(_NAME, load, now=later))
    asyncio.run(store.areview(_NAME, load, now=later + _HOUR))
    assert len(review.prompts) == 1
    assert _read_file(tmp_path)["reviewed_at"] == later


def test_a_failed_daily_check_keeps_everything(tmp_path, monkeypatch):
    store = _store(tmp_path)
    promise = _add(store, _candidate("小天答应给主人推荐一本书", 14, kind="commitment"))
    _install_review_model(monkeypatch, store, _ReviewModel(error=TimeoutError()))

    counts = asyncio.run(store.areview(_NAME, _loader([]), now=_T0 + _DAY))

    assert counts == {"failed": 1}
    assert [e["id"] for e in store.get_active(_NAME, now=_T0 + _DAY)] == [promise["id"]]
    assert "reviewed_at" not in _read_file(tmp_path)


def test_a_daily_verdict_on_an_entry_changed_meanwhile_is_dropped(tmp_path, monkeypatch):
    store = _store(tmp_path)
    text = "小天答应给主人推荐一本书"
    promise = _add(store, _candidate(text, 14, kind="commitment"))
    _install_review_model(monkeypatch, store, _ReviewModel([
        {"target": promise["id"], "action": "retire", "outcome": "done",
         "evidence": "书收到了", "reason": "?"},
    ]))

    async def load(since):
        # An admission renews the promise while the check is under way.
        store.apply_decisions(
            _NAME, [_candidate(text, 14, kind="commitment")], [("add", None)],
            now=_T0 + 2 * _DAY,
        )
        return [{"at": _T0 + _DAY, "role": "human", "text": "书收到了"}]

    counts = asyncio.run(store.areview(_NAME, load, now=_T0 + 2 * _DAY + 60))

    assert counts["retired"] == 0
    [entry] = store.get_active(_NAME, now=_T0 + 2 * _DAY + 60)
    assert (entry["id"], entry["rev"]) == (promise["id"], 1)


def test_the_daily_check_reads_the_newest_messages_within_budget(tmp_path, monkeypatch):
    from utils.tokenize import count_tokens

    store = _store(tmp_path)
    _add(store, _candidate("小天答应给主人推荐一本书", 14, kind="commitment"))
    review = _install_review_model(monkeypatch, store, _ReviewModel([]))
    messages = [
        {"at": _T0 + i * 60, "role": "human" if i % 2 else "ai",
         "text": f"第{i:03d}条消息" + "今天聊了很多很多琐碎的事情" * 6}
        for i in range(300)
    ]

    asyncio.run(store.areview(_NAME, _loader(messages), now=_T0 + _DAY))

    [prompt] = review.prompts
    block = _between(prompt, "======以下为对话======\n", "\n======以上为对话======")
    assert count_tokens(block) <= PINNED_FACT_REVIEW_CONVERSATION_MAX_TOKENS
    assert "第299条消息" in block and "第000条消息" not in block
    assert block.index("第298条消息") < block.index("第299条消息")


def test_placeholders_inside_entries_and_messages_stay_literal(tmp_path, monkeypatch):
    store = _store(tmp_path)
    _add(store, _candidate("主人说 {CONVERSATION} 是口头禅", 14, kind="fact"))
    review = _install_review_model(monkeypatch, store, _ReviewModel([]))

    asyncio.run(store.areview(_NAME, _loader([
        {"at": _T0 + _HOUR, "role": "human", "text": "我刚学会写 {ZONE} 这个词"},
    ]), now=_T0 + _DAY))

    [prompt] = review.prompts
    assert "主人说 {CONVERSATION} 是口头禅" in prompt
    assert "我刚学会写 {ZONE} 这个词" in prompt
    assert prompt.count("我刚学会写") == 1


class _Clock(datetime):
    current = datetime(2026, 11, 2, 12, 0, 0)

    @classmethod
    def now(cls, tz=None):
        return cls.current


def _wire_idle_check(monkeypatch, store):
    from app.memory_server import evidence_loops, gates, runtime

    monkeypatch.setattr(gates, "_maint_state", {})
    monkeypatch.setattr(gates, "_persist_maint_state_locked", lambda: None)
    monkeypatch.setattr(runtime, "pinned_fact_store", store)
    monkeypatch.setattr(runtime, "time_manager", SimpleNamespace(
        aretrieve_original_by_timeframe=AsyncMock(return_value=[
            ("2026-11-02 11:00:00", "s", json.dumps(
                {"type": "human", "data": {"content": "书收到了"}}, ensure_ascii=False,
            )),
        ]),
    ))

    async def run_with_locale(name, operation, *args):
        return await operation(*args)

    monkeypatch.setattr(evidence_loops, "run_with_character_prompt_locale", run_with_locale)
    monkeypatch.setattr(evidence_loops, "datetime", _Clock)
    return evidence_loops


def test_the_idle_check_looks_at_each_character_once_a_day(monkeypatch):
    conversations = []

    async def areview(name, load_conversation, *, now):
        conversations.append(await load_conversation(now - _DAY))
        return {}

    loops = _wire_idle_check(monkeypatch, SimpleNamespace(areview=areview))
    start = _Clock.current

    asyncio.run(loops._review_pinned_facts(_NAME))
    asyncio.run(loops._review_pinned_facts(_NAME))
    _Clock.current = start + timedelta(hours=PINNED_FACT_REVIEW_INTERVAL_HOURS - 1)
    asyncio.run(loops._review_pinned_facts(_NAME))
    _Clock.current = start + timedelta(hours=PINNED_FACT_REVIEW_INTERVAL_HOURS, seconds=1)
    try:
        asyncio.run(loops._review_pinned_facts(_NAME))
    finally:
        _Clock.current = start

    assert len(conversations) == 2
    [message] = conversations[0]
    assert (message["role"], message["text"]) == ("human", "书收到了")
    assert message["at"] == datetime(2026, 11, 2, 11, 0, 0).timestamp()


@pytest.mark.parametrize("activity", ["idle", "busy", "busy_after_review"])
def test_the_idle_loop_runs_the_check_only_while_idle(monkeypatch, activity):
    from app.memory_server import evidence_loops, gates, runtime

    class _Stop(Exception):
        pass

    sleeps = []
    idle = {"now": activity != "busy"}

    async def fake_sleep(seconds):
        sleeps.append(seconds)
        if len(sleeps) > 1:
            raise _Stop

    async def memory_review(_name):
        if activity == "busy_after_review":
            # The user speaks while the tidy-up review is being started.
            idle["now"] = False

    cm = SimpleNamespace(aload_characters=AsyncMock(return_value={"猫娘": {_NAME: {}}}))
    monkeypatch.setattr(asyncio, "sleep", fake_sleep)
    monkeypatch.setattr(gates, "_is_idle", lambda: idle["now"])
    monkeypatch.setattr(gates, "_ais_powerful_memory_enabled", AsyncMock(return_value=False))
    monkeypatch.setattr(runtime, "_config_manager", cm)
    monkeypatch.setattr(runtime, "recent_history_manager", SimpleNamespace(
        aget_recent_history=AsyncMock(return_value=[]), compress_threshold=20,
    ))
    monkeypatch.setattr(evidence_loops, "_spawn_review_with_character_language", memory_review)
    check = AsyncMock(return_value={})
    monkeypatch.setattr(evidence_loops, "_review_pinned_facts", check)

    with pytest.raises(_Stop):
        asyncio.run(evidence_loops._periodic_idle_maintenance_loop())

    assert check.await_count == (1 if activity == "idle" else 0)


# ── 5. proactive chat, rendering, prompt tables ─────────────────────


def test_the_promise_offered_to_proactive_chat_is_the_one_due_soonest(tmp_path):
    store = _store(tmp_path)
    later = _add(store, _candidate("小天答应下周给主人推荐歌", 14, kind="commitment",
                                   due_at=_T0 + 5 * _DAY))
    next_ = _add(store, _candidate("小天答应后天陪主人复习", 3, kind="commitment",
                                   due_at=_T0 + 1.5 * _DAY))
    soon = _add(store, _candidate("小天答应明天提醒主人带伞", 3, kind="commitment",
                                  due_at=_T0 + _DAY))
    _add(store, _candidate("主人明天出差", 3, kind="fact", due_at=_T0 + _DAY))

    assert store.get_due_commitment(_NAME, now=_T0)["id"] == soon["id"]

    assert store.mark_surfaced(_NAME, [soon["id"], "ref_123", "pin_gone"], now=_T0) == [soon["id"]]
    # The soonest one is in its cooldown; the next one due is offered instead.
    assert store.get_due_commitment(_NAME, now=_T0 + _HOUR)["id"] == next_["id"]
    store.mark_surfaced(_NAME, [next_["id"]], now=_T0 + _HOUR)
    # Both in their cooldown, and the last one is not due yet.
    assert store.get_due_commitment(_NAME, now=_T0 + 2 * _HOUR) is None
    cooled = _T0 + PINNED_COMMITMENT_SURFACE_COOLDOWN_HOURS * _HOUR + 1
    assert store.get_due_commitment(_NAME, now=cooled)["id"] == soon["id"]
    assert store.get_due_commitment(_NAME, now=_T0 + 3.5 * _DAY)["id"] == later["id"]
    # Offering a promise is bookkeeping, not a change of content.
    assert next(e for e in store.get_active(_NAME, now=_T0) if e["id"] == soon["id"])["rev"] == 0


def test_a_promise_waiting_on_a_condition_has_no_deadline(tmp_path):
    from config.prompts.prompts_memory import get_pinned_commitments_section_header

    store = _store(tmp_path)
    promise = _add(store, _candidate("小天答应给主人讲完那个故事（等主人考完）", 3, kind="commitment"))

    # Its expiry is the tier ceiling, not a date it is due by: never offered
    # as "due soon", and rendered without one.
    assert store.get_due_commitment(_NAME, now=promise["expire_at"] - _HOUR) is None
    assert store.render_prompt_block(_NAME, "zh", now=_T0) == (
        "### " + get_pinned_commitments_section_header("zh") + "\n"
        "- 小天答应给主人讲完那个故事（等主人考完）（答应于 2026-11-02）"
    )


def test_followup_topics_carry_the_due_promise_and_surfacing_is_split(tmp_path, monkeypatch):
    from app.memory_server import routes, runtime

    now = pinned_mod._now()
    store = _store(tmp_path)
    promise = _add(store, _candidate("小天答应明天提醒主人带伞", 3, kind="commitment",
                                     due_at=now + _DAY), now=now)
    reflections = SimpleNamespace(
        aget_followup_topics=AsyncMock(return_value=[{"id": "ref_1", "text": "主人喜欢的那家店"}]),
        arecord_surfaced=AsyncMock(),
    )
    monkeypatch.setattr(runtime, "reflection_engine", reflections)
    monkeypatch.setattr(runtime, "pinned_fact_store", store)

    body = asyncio.run(routes.api_followup_topics(_NAME))

    assert body["topics"] == [{"id": "ref_1", "text": "主人喜欢的那家店"}]
    assert body["commitment"] == {
        "id": promise["id"], "text": "小天答应明天提醒主人带伞",
        "due": datetime.fromtimestamp(now + _DAY).strftime("%Y-%m-%d"),
    }

    request = SimpleNamespace(json=AsyncMock(return_value={
        "reflection_ids": ["ref_1"], "commitment_ids": [promise["id"]],
    }))
    asyncio.run(routes.api_record_surfaced(request, _NAME))

    reflections.arecord_surfaced.assert_awaited_once_with(_NAME, ["ref_1"])
    assert asyncio.run(routes.api_followup_topics(_NAME))["commitment"] is None

    # Offered on its own: the reflections' surfaced log is not touched.
    reflections.arecord_surfaced.reset_mock()
    request = SimpleNamespace(json=AsyncMock(return_value={
        "reflection_ids": [], "commitment_ids": [promise["id"]],
    }))
    asyncio.run(routes.api_record_surfaced(request, _NAME))
    reflections.arecord_surfaced.assert_not_awaited()


def test_the_due_promise_leads_the_proactive_memory_cues():
    from config.prompts.prompts_activity import (
        TOPIC_COMMITMENT_CUE_INTROS,
        TOPIC_MEMORY_CUE_INTROS,
    )
    from main_logic.proactive_chat.service import (
        _offered_commitment_ids,
        _render_followup_topic_hooks,
    )

    commitment = {"id": "pin_00aa11bb", "text": "明天提醒主人带准考证", "due": "2026-11-03"}

    prompt, ids = _render_followup_topic_hooks(
        "zh", [{"id": "ref_1", "text": "主人喜欢的那家店"}], commitment=commitment,
    )
    assert prompt.index(TOPIC_COMMITMENT_CUE_INTROS["zh"]) < prompt.index(TOPIC_MEMORY_CUE_INTROS["zh"])
    assert "2026-11-03" in prompt and "明天提醒主人带准考证" in prompt
    # The promise is no reflection: it must not count as use of that channel.
    assert ids == ["ref_1"]
    assert _offered_commitment_ids(commitment) == ["pin_00aa11bb"]

    prompt, ids = _render_followup_topic_hooks("en", [], commitment=commitment)
    assert prompt.startswith(TOPIC_COMMITMENT_CUE_INTROS["en"]) and ids == []
    blank = {"id": "pin_x", "text": " "}
    assert _render_followup_topic_hooks("en", [], commitment=blank) == ("", [])
    assert _offered_commitment_ids(blank) == []


def test_a_due_promise_alone_keeps_the_proactive_round_going():
    from main_logic.proactive_chat.generation import _decide_phase1_channels

    assert _decide_phase1_channels([], None, has_unfinished_thread=False).result is not None
    decision = _decide_phase1_channels(
        [], None, has_unfinished_thread=False, has_due_commitment=True,
    )
    assert decision.result is None and decision.active_channels == []


@pytest.mark.parametrize(("reflection_ids", "commitment_ids"), [([], ["pin_00aa11bb"]), (["ref_1"], [])])
def test_delivering_a_promise_cools_it_down_without_using_up_reminiscence(
    monkeypatch, reflection_ids, commitment_ids,
):
    from main_logic.proactive_chat import delivery as proactive_delivery
    from main_logic.proactive_chat.delivery import CommittedDelivery, _record_committed_delivery

    for name in ("_record_proactive_chat", "_record_proactive_material",
                 "_mini_game_invite_count_post_response_chat"):
        monkeypatch.setattr(proactive_delivery, name, lambda *a, **k: None)
    monkeypatch.setattr(proactive_delivery, "_increment_proactive_chat_total", AsyncMock())
    reminiscence = []
    monkeypatch.setattr(proactive_delivery, "_record_reminiscence_usage", reminiscence.append)
    client = SimpleNamespace(post=AsyncMock())
    monkeypatch.setattr(proactive_delivery, "_get_internal_http_client", lambda: client)
    delivery = CommittedDelivery(
        turn_id="t", primary_channel="chat", source_links=[], delivered_tag="CHAT",
        delivered_music_link=None, is_music_used=False, action_note="",
        vision_screenshot_b64=None,
    )

    asyncio.run(_record_committed_delivery(
        mgr=SimpleNamespace(current_speech_id="t"), delivery=delivery,
        lanlan_name=_NAME, response_text="记得带伞哦", source_tag="CHAT", active_channels=[],
        has_unfinished_thread=False, surfaced_reflection_ids=reflection_ids,
        surfaced_commitment_ids=commitment_ids,
        selected_web_link=None, selected_web_topic_key=None, web_parsed=None,
        selected_music_link=None, selected_music_topic_key=None, selected_meme_link=None,
        selected_meme_topic_key=None, meme_content=None,
    ))

    assert reminiscence == ([_NAME] if reflection_ids else [])
    [call] = client.post.await_args_list
    assert call.kwargs["json"] == {"reflection_ids": reflection_ids, "commitment_ids": commitment_ids}


def test_open_promises_render_in_their_own_section(tmp_path):
    from config.prompts.prompts_memory import (
        get_pinned_commitments_section_header,
        get_pinned_facts_section_header,
    )

    store = _store(tmp_path)
    _add(store, _candidate("主人下周三考试", 14, kind="fact"))
    promise = _add(store, _candidate("小天答应明天提醒主人带准考证", 3, kind="commitment",
                                     due_at=_T0 + _DAY))

    assert store.render_prompt_block(_NAME, "zh", now=_T0) == (
        "### " + get_pinned_facts_section_header("zh") + "\n"
        "- 主人下周三考试（记于 2026-11-02，到 2026-11-16）\n\n"
        "### " + get_pinned_commitments_section_header("zh") + "\n"
        "- 小天答应明天提醒主人带准考证（答应于 2026-11-02，期限 2026-11-03）"
    )

    store.apply_retirements(
        _NAME, [{"target": promise["id"], "outcome": "done", "reason": "", "noted_at": _T0 + 1}],
        expected_revs={promise["id"]: 0}, now=_T0 + 1,
    )
    assert get_pinned_commitments_section_header("zh") not in store.render_prompt_block(
        _NAME, "zh", now=_T0 + 1,
    )


def test_commitment_and_review_prompt_tables_cover_every_locale():
    from config.prompts import prompts_activity as pa
    from config.prompts import prompts_memory as pm

    for table in (pm.PINNED_COMMITMENTS_SECTION_HEADER, pm.PINNED_COMMITMENT_LINE,
                  pm.PINNED_COMMITMENT_UNDATED_LINE, pm.PINNED_COMMITMENT_DONE_LINE,
                  pm.PINNED_FACT_REVIEW_PROMPT):
        assert set(table) == _LOCALES
    for lang, line in pm.PINNED_COMMITMENT_UNDATED_LINE.items():
        assert "2026-01-02" in line.format(text="T", since="2026-01-02"), lang
    for lang, line in pm.PINNED_COMMITMENT_LINE.items():
        rendered = line.format(text="T", since="2026-01-02", until="2026-03-04")
        assert "2026-01-02" in rendered and "2026-03-04" in rendered, lang
    for lang, line in pm.PINNED_COMMITMENT_DONE_LINE.items():
        assert "2026-01-02 08:00" in line.format(text="T", since="2026-01-02 08:00"), lang
    for lang, prompt in pm.PINNED_FACT_REVIEW_PROMPT.items():
        for token in ("{LANLAN_NAME}", "{MASTER_NAME}", "{TODAY}", "{ZONE}", "{CONVERSATION}",
                      '"retire"', "keep", '"evidence"', "done", "invalid", "past"):
            assert token in prompt, (lang, token)
        delimiters = [ln for ln in prompt.splitlines() if ln.startswith("======")]
        assert len(delimiters) == 4, lang
        assert "======以下为对话======" in delimiters and "======以上为对话======" in delimiters
    for lang, prompt in pm.PINNED_FACT_RESOLVE_PROMPT.items():
        assert '"retire"' in prompt and '"outcome"' in prompt, lang
    for table in (pm.FACT_EXTRACTION_PROMPT, pm.FACT_EXTRACTION_AI_AWARE_PROMPT):
        for lang, prompt in table.items():
            for token in ('"pin_kind": "commitment"', '"pin_condition"', '"pin_done": true',
                          '"end": {"offset": 1, "unit": "day"}'):
                assert token in prompt, (lang, token)
    assert set(pa.TOPIC_COMMITMENT_CUE_INTROS) == set(pa.TOPIC_MEMORY_CUE_INTROS)
    assert set(pa.TOPIC_COMMITMENT_CUE_LABELS) == set(pa.TOPIC_MEMORY_CUE_LABELS)
    for lang, label in pa.TOPIC_COMMITMENT_CUE_LABELS.items():
        assert "2026-11-03" in label.format(due="2026-11-03"), lang
