# -*- coding: utf-8 -*-
"""Unit tests for memory.pinned_facts: the pinned important-fact section.

Contract groups:

1. Candidates: only entries flagged ``pin`` become candidates; Stage-1 output
   from before the new fields existed yields none; lifetimes snap to the four
   tiers; ai_disclosure facts are never pinned.
2. Admission into an empty section writes directly, without a model call.
3. The four resolve actions (add / replace / renew / remove), validation of
   the ids the model returns, and the fallback when the model fails.
4. Expiry, renewal and the entry cap.
5. Character-card conflicts are rejected.
6. Rendering: format, token budget, and the private-only boundary
   (/new_dialog renders the section ahead of the persona; nothing else does).
7. FactStore wiring: private-chat entry points feed the section, scoped ones
   never do, and an admission failure never breaks extraction.
8. Prompt tables cover all eight locales and agree with the tier constant.
"""
from __future__ import annotations

import ast
import asyncio
import json
import os
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from config import (
    PINNED_FACT_DEFAULT_TTL_DAYS,
    PINNED_FACT_MAX_ACTIVE,
    PINNED_FACT_RENDER_MAX_TOKENS,
    PINNED_FACT_TTL_TIERS_DAYS,
)
from memory import pinned_facts as pinned_mod
from memory.pinned_facts import (
    PINNED_FACTS_FILENAME,
    PinnedFactStore,
    parse_resolve_decisions,
    pin_candidates_from_extracted,
    snap_ttl_days,
)

_REPO_ROOT = Path(__file__).resolve().parents[2]
_DAY = 86400.0
_T0 = datetime(2026, 11, 2, 12, 0, 0).timestamp()
_NAME = "小天"
_LOCALES = {"zh", "zh-TW", "en", "ja", "ko", "ru", "es", "pt"}


# ── helpers ──────────────────────────────────────────────────────────


def _cm(tmp_path):
    cm = MagicMock()
    cm.memory_dir = str(tmp_path)
    cm.local_state_dir = Path(tmp_path)
    cm.app_docs_dir = Path(tmp_path)
    cm.load_root_state.return_value = {"mode": "normal"}
    cm.aget_character_data = AsyncMock(return_value=(
        "主人", _NAME, {}, {}, {"human": "主人", "ai": _NAME}, {}, {}, {}, {},
    ))
    cm.aget_model_api_config = AsyncMock(return_value={"model": "fake"})
    return cm


def _store(tmp_path, persona_manager=None) -> PinnedFactStore:
    cm = _cm(tmp_path)
    with patch("memory.pinned_facts.get_config_manager", return_value=cm):
        store = PinnedFactStore(persona_manager=persona_manager)
    return store


def _read_file(tmp_path, name=_NAME) -> dict:
    with open(os.path.join(str(tmp_path), name, PINNED_FACTS_FILENAME), encoding="utf-8") as f:
        return json.load(f)


def _pin(text, ttl=14, **extra) -> dict:
    return {"text": text, "importance": 9, "entity": "master", "pin": True,
            "pin_ttl_days": ttl, **extra}


def _candidate(text, ttl, **extra) -> dict:
    """A candidate as apply_decisions receives it."""
    return {"text": text, "entity": "master", "ttl_days": ttl, **extra}


class _Model:
    """Stands in for ``ainvoke_thinking``; records every prompt it is sent."""

    def __init__(self, answer=None, *, error: Exception | None = None):
        self.answer = answer
        self.error = error
        self.prompts: list[str] = []

    async def __call__(self, api_config, prompt, *, timeout, call_label):
        self.prompts.append(prompt)
        if self.error is not None:
            raise self.error
        return SimpleNamespace(content=json.dumps(self.answer, ensure_ascii=False)), 0


def _install_model(monkeypatch, model: _Model) -> _Model:
    monkeypatch.setattr("memory.thinking_llm.ainvoke_thinking", model)
    return model


async def _seed(store, texts_ttls, *, now=_T0) -> list[dict]:
    """Admit into an empty section (no model call) and return the live entries."""
    await store.aadmit_extracted(_NAME, [_pin(t, ttl) for t, ttl in texts_ttls], now=now)
    return store.get_active(_NAME, now=now)


# ── 1. candidates ────────────────────────────────────────────────────


def test_legacy_output_without_pin_fields_yields_no_candidates(tmp_path):
    legacy = [
        {"text": "主人喜欢喝咖啡", "importance": 7, "entity": "master", "event_when": None},
        {"text": "主人下周三考试", "importance": 10, "entity": "master"},
    ]
    assert pin_candidates_from_extracted(legacy) == []
    store = _store(tmp_path)
    assert asyncio.run(store.aadmit_extracted(_NAME, legacy, now=_T0)) == {}
    assert not os.path.exists(os.path.join(str(tmp_path), _NAME, PINNED_FACTS_FILENAME))


@pytest.mark.parametrize("raw,expected", [
    (3, 3), (14, 14), (90, 90), (365, 365),
    (1, 3), (2.5, 3), (7, 14), (30, 90), (180, 365), (400, 365),
    ("90", 90), (" 14 ", 14), (14.0, 14),
    (0, None), (-3, None), (True, None), (False, None), ("abc", None),
    (None, None), (float("nan"), None), (float("inf"), None),
])
def test_snap_ttl_days_rounds_up_to_a_tier(raw, expected):
    assert snap_ttl_days(raw) == expected


def test_tier_constant_matches_the_prompt_tiers():
    # The extraction prompts spell the tiers out ("3 / 14 / 90 / 365"); a
    # changed constant without changed prompts would snap the model's answers
    # to tiers it was never offered.
    assert PINNED_FACT_TTL_TIERS_DAYS == (3, 14, 90, 365)
    assert PINNED_FACT_DEFAULT_TTL_DAYS in PINNED_FACT_TTL_TIERS_DAYS


def test_candidates_need_pin_and_take_the_default_tier():
    extracted = [
        _pin("主人下周三考试", 7),
        {"text": "主人对花生过敏", "pin": "true", "entity": "master"},
        {"text": "主人喜欢猫", "pin": False, "pin_ttl_days": 365},
        {"text": "  ", "pin": True},
        {"pin": True, "pin_ttl_days": 3},
        _pin("主人下周三考试", 90),
        "a bare string",
    ]
    assert pin_candidates_from_extracted(extracted) == [
        {"text": "主人下周三考试", "entity": "master", "ttl_days": 14},
        {"text": "主人对花生过敏", "entity": "master",
         "ttl_days": PINNED_FACT_DEFAULT_TTL_DAYS},
    ]


def test_ai_disclosure_is_never_pinned():
    extracted = [
        _pin("小天说自己喜欢秋天", source="ai_disclosure"),
        _pin("主人周五面试", source="user_observation"),
        _pin("主人搬家了"),
    ]
    assert [c["text"] for c in pin_candidates_from_extracted(extracted)] == [
        "主人周五面试", "主人搬家了",
    ]
    # Path B persists a fact without an explicit source as ai_disclosure.
    assert [
        c["text"] for c in pin_candidates_from_extracted(
            extracted, default_source="ai_disclosure",
        )
    ] == ["主人周五面试"]


# ── 2. admission into an empty section ───────────────────────────────


def test_empty_section_admits_directly_without_model(tmp_path, monkeypatch):
    model = _install_model(monkeypatch, _Model(error=AssertionError("no model call")))
    store = _store(tmp_path)

    counts = asyncio.run(store.aadmit_extracted(
        _NAME, [_pin("主人下周三有数学考试", 14), _pin("主人对花生过敏", 365)], now=_T0,
    ))

    assert counts["added"] == 2
    assert model.prompts == []
    stored = _read_file(tmp_path)["facts"]
    assert [(e["text"], e["ttl_days"]) for e in stored] == [
        ("主人下周三有数学考试", 14), ("主人对花生过敏", 365),
    ]
    assert stored[0]["expire_at"] == pytest.approx(_T0 + 14 * _DAY)
    assert stored[1]["expire_at"] == pytest.approx(_T0 + 365 * _DAY)
    assert all(e["id"].startswith("pin_") for e in stored)


# ── 3. the four actions, id validation, fallback ─────────────────────


def test_model_add_keeps_both_entries(tmp_path, monkeypatch):
    store = _store(tmp_path)
    [exam] = asyncio.run(_seed(store, [("主人下周三考试", 14)]))
    model = _install_model(monkeypatch, _Model([{"candidate": 1, "action": "add", "target": None}]))

    counts = asyncio.run(store.aadmit_extracted(_NAME, [_pin("主人对猫毛过敏", 365)], now=_T0 + _DAY))

    assert counts["added"] == 1
    assert [e["text"] for e in store.get_active(_NAME, now=_T0 + _DAY)] == [
        "主人下周三考试", "主人对猫毛过敏",
    ]
    # The whole section and the candidate went to the model, with today's date.
    [prompt] = model.prompts
    assert f"[{exam['id']}]" in prompt and "主人下周三考试" in prompt
    assert "1. 主人对猫毛过敏" in prompt
    assert datetime.fromtimestamp(_T0 + _DAY).strftime("%Y-%m-%d") in prompt
    for placeholder in ("{LANLAN_NAME}", "{MASTER_NAME}", "{TODAY}", "{ZONE}", "{CANDIDATES}"):
        assert placeholder not in prompt


def test_model_replace_keeps_id_and_moves_old_text_to_history(tmp_path, monkeypatch):
    store = _store(tmp_path)
    [exam] = asyncio.run(_seed(store, [("主人下周三考试", 14)]))
    _install_model(monkeypatch, _Model([
        {"candidate": 1, "action": "replace", "target": exam["id"]},
    ]))
    later = _T0 + 2 * _DAY

    counts = asyncio.run(store.aadmit_extracted(_NAME, [_pin("主人的考试改到下周五", 14)], now=later))

    assert counts["replaced"] == 1
    [entry] = store.get_active(_NAME, now=later)
    assert entry["id"] == exam["id"]
    assert entry["text"] == "主人的考试改到下周五"
    assert entry["text_at"] == pytest.approx(later)
    assert entry["expire_at"] == pytest.approx(later + 14 * _DAY)
    history = _read_file(tmp_path)["history"]
    assert [(h["action"], h["text"], h["id"]) for h in history] == [
        ("replace", "主人下周三考试", exam["id"]),
    ]


def test_model_renew_extends_and_never_shortens(tmp_path, monkeypatch):
    store = _store(tmp_path)
    exam, allergy = asyncio.run(_seed(store, [("主人下周三考试", 14), ("主人对花生过敏", 365)]))
    _install_model(monkeypatch, _Model([
        {"candidate": 1, "action": "renew", "target": exam["id"]},
        {"candidate": 2, "action": "renew", "target": allergy["id"]},
    ]))
    later = _T0 + 10 * _DAY

    counts = asyncio.run(store.aadmit_extracted(
        _NAME, [_pin("别忘了我下周三考试", 14), _pin("我花生过敏", 3)], now=later,
    ))

    assert counts["renewed"] == 2
    by_id = {e["id"]: e for e in store.get_active(_NAME, now=later)}
    assert by_id[exam["id"]]["text"] == "主人下周三考试"
    assert by_id[exam["id"]]["expire_at"] == pytest.approx(later + 14 * _DAY)
    assert by_id[exam["id"]]["renew_count"] == 1
    # A 3-day renewal of a 365-day entry leaves its expiry alone.
    assert by_id[allergy["id"]]["expire_at"] == pytest.approx(_T0 + 365 * _DAY)
    assert _read_file(tmp_path)["history"] == []


def test_model_remove_deletes_entry_and_drops_candidate(tmp_path, monkeypatch):
    store = _store(tmp_path)
    exam, allergy = asyncio.run(_seed(store, [("主人下周三考试", 14), ("主人对花生过敏", 365)]))
    _install_model(monkeypatch, _Model([
        {"candidate": 1, "action": "remove", "target": exam["id"]},
    ]))

    counts = asyncio.run(store.aadmit_extracted(_NAME, [_pin("主人的考试取消了", 3)], now=_T0 + _DAY))

    assert counts["removed"] == 1
    assert [e["id"] for e in store.get_active(_NAME, now=_T0 + _DAY)] == [allergy["id"]]
    assert [(h["action"], h["id"]) for h in _read_file(tmp_path)["history"]] == [
        ("remove", exam["id"]),
    ]


def test_ids_outside_the_section_are_rejected(tmp_path, monkeypatch):
    store = _store(tmp_path)
    [exam] = asyncio.run(_seed(store, [("主人下周三考试", 14)]))
    _install_model(monkeypatch, _Model([
        {"candidate": 1, "action": "replace", "target": "pin_ffffffff"},
        {"candidate": 2, "action": "remove", "target": "pin_eeeeeeee"},
        {"candidate": 3, "action": "renew", "target": None},
    ]))

    counts = asyncio.run(store.aadmit_extracted(_NAME, [
        _pin("主人周六去北京"), _pin("主人的面试取消了"), _pin("主人下周三考试"),
    ], now=_T0 + _DAY))

    # Bad replace → fallback add; bad remove → dropped; renew without a
    # target → fallback renews the identical text.
    assert (counts["added"], counts["skipped"], counts["renewed"]) == (1, 1, 1)
    live = store.get_active(_NAME, now=_T0 + _DAY)
    assert [e["text"] for e in live] == ["主人下周三考试", "主人周六去北京"]
    assert live[0]["id"] == exam["id"] and live[0]["renew_count"] == 1


def test_model_may_only_target_ids_it_was_shown(tmp_path, monkeypatch):
    # Another admission lands while the model is thinking. Its entry is in the
    # file by apply time, but this model never saw it, so an answer naming it
    # is a guess and must not replace it.
    store = _store(tmp_path)
    asyncio.run(_seed(store, [("主人下周三考试", 14)]))
    newcomer: dict = {}
    prompts: list[str] = []

    async def model(api_config, prompt, *, timeout, call_label):
        prompts.append(prompt)
        if len(prompts) == 1:
            store.apply_decisions(
                _NAME, [{"text": "主人对花生过敏", "entity": "master", "ttl_days": 365}],
                [("add", None)], now=_T0 + 1,
            )
            newcomer.update(store.get_active(_NAME, now=_T0 + 1)[-1])
            answer = [{"candidate": 1, "action": "replace", "target": newcomer["id"]}]
        else:
            answer = [{"candidate": 1, "action": "add", "target": None}]
        return SimpleNamespace(content=json.dumps(answer)), 0

    monkeypatch.setattr("memory.thinking_llm.ainvoke_thinking", model)

    asyncio.run(store.aadmit_extracted(_NAME, [_pin("主人周六去北京", 3)], now=_T0 + 2))

    # The guess is rejected, and since the file gained an entry the model had
    # not seen, the add is decided again with it in view.
    assert len(prompts) == 2 and "主人对花生过敏" in prompts[1]
    live = {e["id"]: e for e in store.get_active(_NAME, now=_T0 + 2)}
    assert live[newcomer["id"]]["text"] == "主人对花生过敏"
    assert sorted(e["text"] for e in live.values()) == sorted([
        "主人下周三考试", "主人对花生过敏", "主人周六去北京",
    ])


def test_parse_resolve_decisions_validates_shape():
    valid = {"pin_a", "pin_b"}
    results = [
        {"candidate": "1", "action": "REPLACE", "target": " pin_a "},
        {"candidate": 1, "action": "add"},            # second answer for 1: ignored
        {"candidate": 2, "action": "explode", "target": "pin_b"},
        {"candidate": 3, "action": "renew", "target": "pin_zzz"},
        {"candidate": 4, "action": "remove", "target": "pin_zzz"},
        {"candidate": 9, "action": "add"},            # out of range
        {"candidate": True, "action": "add"},
        "garbage",
    ]
    assert parse_resolve_decisions(results, 4, valid) == {
        0: ("replace", "pin_a"),
        3: (None, None),
    }
    assert parse_resolve_decisions({"candidate": 1, "action": "add"}, 1, valid) == {
        0: ("add", None),
    }
    assert parse_resolve_decisions("not json", 1, valid) == {}


@pytest.mark.parametrize("error", [RuntimeError("upstream down"), None])
def test_model_failure_falls_back_to_renew_identical_or_add(tmp_path, monkeypatch, error):
    store = _store(tmp_path)
    [exam] = asyncio.run(_seed(store, [("主人下周三考试", 14)]))
    # Either the call raises, or it answers with nothing usable.
    _install_model(monkeypatch, _Model({"unexpected": "shape"}, error=error))

    counts = asyncio.run(store.aadmit_extracted(
        _NAME, [_pin("主人下周三考试", 14), _pin("主人周六去北京", 3)], now=_T0 + _DAY,
    ))

    assert (counts["renewed"], counts["added"]) == (1, 1)
    live = store.get_active(_NAME, now=_T0 + _DAY)
    assert [e["text"] for e in live] == ["主人下周三考试", "主人周六去北京"]
    assert live[0]["id"] == exam["id"]


def test_targets_gone_degrade_without_a_version_check(tmp_path):
    # The last resolve round: the model answered against a snapshot and
    # another admission removed the entry before this one applied.
    store = _store(tmp_path)
    candidates = [
        {"text": "主人周六去北京", "entity": "master", "ttl_days": 3},
        {"text": "主人的考试取消了", "entity": "master", "ttl_days": 3},
    ]
    counts = store.apply_decisions(
        _NAME, candidates, [("replace", "pin_gone"), ("remove", "pin_gone")], now=_T0,
    )
    assert (counts["added"], counts["skipped"], counts["removed"]) == (1, 1, 0)
    assert [e["text"] for e in store.get_active(_NAME, now=_T0)] == ["主人周六去北京"]


def test_version_check_reports_changed_targets_instead_of_applying(tmp_path):
    store = _store(tmp_path)
    [exam] = asyncio.run(_seed(store, [("主人下周三考试", 14)]))
    snapshot_revs = {exam["id"]: exam["rev"]}
    # Someone else renews the entry after our snapshot was taken.
    store.apply_decisions(_NAME, [_candidate("主人下周三考试", 14)], [("renew", exam["id"])], now=_T0 + 1)

    stale: list[int] = []
    counts = store.apply_decisions(
        _NAME,
        [_candidate("主人的考试取消了", 3), _candidate("主人周六去北京", 3)],
        [("remove", exam["id"]), ("add", None)],
        now=_T0 + 2, expected_revs=snapshot_revs, stale_out=stale,
    )

    assert stale == [0]
    assert (counts["stale"], counts["removed"], counts["added"]) == (1, 0, 1)
    assert [e["text"] for e in store.get_active(_NAME, now=_T0 + 2)] == [
        "主人下周三考试", "主人周六去北京",
    ]


def test_a_decision_on_a_changed_entry_is_made_again(tmp_path, monkeypatch):
    # Greptile #3598: an older "exam cancelled" decision must not delete an
    # entry a newer admission already changed to "exam moved to Friday".
    store = _store(tmp_path)
    [exam] = asyncio.run(_seed(store, [("主人下周三考试", 14)]))
    prompts: list[str] = []

    async def model(api_config, prompt, *, timeout, call_label):
        prompts.append(prompt)
        if len(prompts) == 1:
            store.apply_decisions(
                _NAME, [_candidate("主人的考试改到下周五", 14, noted_at=_T0 + 1)],
                [("replace", exam["id"])], now=_T0 + 1,
            )
            answer = [{"candidate": 1, "action": "remove", "target": exam["id"]}]
        else:
            answer = [{"candidate": 1, "action": "renew", "target": exam["id"]}]
        return SimpleNamespace(content=json.dumps(answer)), 0

    monkeypatch.setattr("memory.thinking_llm.ainvoke_thinking", model)

    counts = asyncio.run(store.aadmit_extracted(
        _NAME, [_pin("主人的考试取消了", 3)], noted_at=_T0, now=_T0 + 2,
    ))

    assert len(prompts) == 2
    assert "主人的考试改到下周五" in prompts[1]
    assert (counts["removed"], counts["renewed"]) == (0, 1)
    [entry] = store.get_active(_NAME, now=_T0 + 2)
    assert entry["text"] == "主人的考试改到下周五"


def test_concurrent_first_admissions_are_reconciled(tmp_path, monkeypatch):
    # Cloud review #3598: two admissions into an empty section skip the model;
    # without a check both land, "exam Wednesday" next to "exam moved to Friday".
    store = _store(tmp_path)
    real_snapshot = store.aget_snapshot
    snapshots = 0

    async def snapshot(name, *, now=None):
        nonlocal snapshots
        snapshots += 1
        snap = await real_snapshot(name, now=now)
        if snapshots == 1:
            # The other admission lands right after our (empty) snapshot.
            store.apply_decisions(
                _NAME, [_candidate("主人下周三考试", 14)], [("add", None)], now=_T0,
            )
        return snap

    monkeypatch.setattr(store, "aget_snapshot", snapshot)
    prompts: list[str] = []

    async def model(api_config, prompt, *, timeout, call_label):
        prompts.append(prompt)
        [exam] = store.get_active(_NAME, now=_T0)
        answer = [{"candidate": 1, "action": "replace", "target": exam["id"]}]
        return SimpleNamespace(content=json.dumps(answer)), 0

    monkeypatch.setattr("memory.thinking_llm.ainvoke_thinking", model)

    counts = asyncio.run(store.aadmit_extracted(
        _NAME, [_pin("主人的考试改到下周五", 14)], now=_T0 + 1,
    ))

    assert len(prompts) == 1 and "主人下周三考试" in prompts[0]
    assert (counts["added"], counts["replaced"]) == (0, 1)
    assert [e["text"] for e in store.get_active(_NAME, now=_T0 + 1)] == ["主人的考试改到下周五"]


def test_resolve_rounds_are_bounded(tmp_path, monkeypatch):
    from config import PINNED_FACT_RESOLVE_ATTEMPTS

    store = _store(tmp_path)
    [exam] = asyncio.run(_seed(store, [("主人下周三考试", 14)]))
    calls = 0

    async def model(api_config, prompt, *, timeout, call_label):
        nonlocal calls
        calls += 1
        # Every round, another admission touches the target first.
        store.apply_decisions(
            _NAME, [_candidate("主人下周三考试", 14)], [("renew", exam["id"])], now=_T0 + calls,
        )
        answer = [{"candidate": 1, "action": "replace", "target": exam["id"]}]
        return SimpleNamespace(content=json.dumps(answer)), 0

    monkeypatch.setattr("memory.thinking_llm.ainvoke_thinking", model)

    counts = asyncio.run(store.aadmit_extracted(
        _NAME, [_pin("主人的考试改到下周五", 14)], now=_T0 + 10,
    ))

    assert calls == PINNED_FACT_RESOLVE_ATTEMPTS
    # No retry left: the outdated replace becomes an add and leaves the entry
    # the other admission kept changing alone.
    assert (counts["replaced"], counts["added"]) == (0, 1)
    assert [e["text"] for e in store.get_active(_NAME, now=_T0 + 10)] == [
        "主人下周三考试", "主人的考试改到下周五",
    ]


def test_last_round_never_undoes_a_newer_correction(tmp_path, monkeypatch):
    # Cloud review #3598: "exam cancelled" decided on an old snapshot must not
    # delete the "exam moved to Friday" another admission wrote meanwhile,
    # not even once the retries are used up.
    from config import PINNED_FACT_RESOLVE_ATTEMPTS

    store = _store(tmp_path)
    [exam] = asyncio.run(_seed(store, [("主人下周三考试", 14)]))
    calls = 0

    async def model(api_config, prompt, *, timeout, call_label):
        nonlocal calls
        calls += 1
        store.apply_decisions(
            _NAME, [_candidate(f"主人的考试改到下周五（第{calls}次）", 14)],
            [("replace", exam["id"])], now=_T0 + calls,
        )
        answer = [{"candidate": 1, "action": "remove", "target": exam["id"]}]
        return SimpleNamespace(content=json.dumps(answer)), 0

    monkeypatch.setattr("memory.thinking_llm.ainvoke_thinking", model)

    counts = asyncio.run(store.aadmit_extracted(
        _NAME, [_pin("主人的考试取消了", 3)], now=_T0 + 10,
    ))

    assert calls == PINNED_FACT_RESOLVE_ATTEMPTS
    assert (counts["removed"], counts["skipped"]) == (0, 1)
    [entry] = store.get_active(_NAME, now=_T0 + 10)
    assert entry["text"] == f"主人的考试改到下周五（第{PINNED_FACT_RESOLVE_ATTEMPTS}次）"


def test_mutation_holds_the_cloudsave_fence_across_read_and_write(tmp_path, monkeypatch):
    # Greptile #3598: a cloud restore between the read and the write must not
    # be overwritten with the admission's copy of the old file.
    from contextlib import contextmanager

    store = _store(tmp_path)
    events: list[str] = []

    @contextmanager
    def fence(config_manager, *, operation, target):
        events.append("fence-enter")
        yield
        events.append("fence-exit")

    real_read, real_write = store._read_unlocked, store._write_unlocked
    monkeypatch.setattr(
        "utils.cloudsave_runtime.cloudsave_writable_transaction", fence,
    )
    monkeypatch.setattr(
        store, "_read_unlocked", lambda name: (events.append("read"), real_read(name))[1],
    )
    monkeypatch.setattr(
        store, "_write_unlocked",
        lambda name, facts, history: (events.append("write"), real_write(name, facts, history))[1],
    )

    store.apply_decisions(_NAME, [_candidate("主人周六去北京", 3)], [("add", None)], now=_T0)

    assert events == ["fence-enter", "read", "write", "fence-exit"]


def test_entries_are_dated_by_the_conversation_not_the_admission(tmp_path, monkeypatch):
    # Greptile #3598: a retried window or a replayed turn is admitted later
    # than it was said; "tomorrow" must keep pointing at the right day.
    store = _store(tmp_path)
    said = _T0 - 2 * _DAY

    counts = asyncio.run(store.aadmit_extracted(
        _NAME, [_pin("主人明天考试", 14), _pin("主人感冒了", 3), _pin("主人今晚加班", 3)],
        noted_at=said, now=_T0,
    ))

    assert counts["added"] == 3
    exam = store.get_active(_NAME, now=_T0)[0]
    assert exam["text_at"] == pytest.approx(said)
    assert exam["expire_at"] == pytest.approx(said + 14 * _DAY)
    assert "记于 2026-10-31" in store.render_prompt_block(_NAME, "zh", now=_T0)

    # Said five days ago with a 3-day lifetime: already over. Even when the
    # model says add, it is not admitted.
    _install_model(monkeypatch, _Model([{"candidate": 1, "action": "add", "target": None}]))
    counts = asyncio.run(store.aadmit_extracted(
        _NAME, [_pin("主人头疼", 3)], noted_at=_T0 - 5 * _DAY, now=_T0,
    ))
    assert (counts["outdated"], counts["added"], counts["skipped"]) == (1, 0, 1)
    assert "主人头疼" not in [e["text"] for e in store.get_active(_NAME, now=_T0)]


def test_an_outdated_correction_still_retracts_a_live_entry(tmp_path, monkeypatch):
    # Greptile #3598: "the exam was cancelled" (3-day tier) replayed four days
    # later must still remove the 14-day exam entry it cancels.
    store = _store(tmp_path)
    asyncio.run(store.aadmit_extracted(
        _NAME, [_pin("主人下周三考试", 14)], noted_at=_T0 - 6 * _DAY, now=_T0 - 6 * _DAY,
    ))
    [exam] = store.get_active(_NAME, now=_T0)
    model = _install_model(monkeypatch, _Model([
        {"candidate": 1, "action": "remove", "target": exam["id"]},
    ]))

    counts = asyncio.run(store.aadmit_extracted(
        _NAME, [_pin("主人的考试取消了", 3)], noted_at=_T0 - 4 * _DAY, now=_T0,
    ))

    assert len(model.prompts) == 1
    assert (counts["outdated"], counts["removed"]) == (1, 1)
    assert store.get_active(_NAME, now=_T0) == []


def test_outdated_candidates_into_an_empty_section_do_nothing(tmp_path, monkeypatch):
    model = _install_model(monkeypatch, _Model(error=AssertionError("no model call")))
    store = _store(tmp_path)

    counts = asyncio.run(store.aadmit_extracted(
        _NAME, [_pin("主人今晚加班", 3)], noted_at=_T0 - 4 * _DAY, now=_T0,
    ))

    assert model.prompts == []
    assert (counts["outdated"], counts.get("added", 0)) == (1, 0)
    assert not os.path.exists(os.path.join(str(tmp_path), _NAME, PINNED_FACTS_FILENAME))


def test_the_same_words_said_later_move_the_noted_date_forward_only(tmp_path):
    # Codex #3598: "working late tonight" on Monday, then again on Wednesday,
    # must read as Wednesday; a replay of Monday's must not pull it back.
    store = _store(tmp_path)
    monday, wednesday = _T0, _T0 + 2 * _DAY
    store.apply_decisions(_NAME, [_candidate("主人今晚加班", 3, noted_at=monday)],
                          [("add", None)], now=monday)

    store.apply_decisions(_NAME, [_candidate("主人今晚加班", 3, noted_at=wednesday)],
                          [("add", None)], now=wednesday)
    [entry] = store.get_active(_NAME, now=wednesday)
    assert (entry["text_at"], entry["renew_count"]) == (pytest.approx(wednesday), 1)
    assert entry["expire_at"] == pytest.approx(wednesday + 3 * _DAY)

    store.apply_decisions(_NAME, [_candidate("主人今晚加班", 3, noted_at=monday)],
                          [("add", None)], now=wednesday + 1)
    [entry] = store.get_active(_NAME, now=wednesday + 1)
    assert entry["text_at"] == pytest.approx(wednesday)


def test_an_older_account_never_overrides_a_newer_entry(tmp_path):
    # Codex #3598: within one day the model sees no order; the code enforces
    # it. An older "exam cancelled" / "exam on Wednesday" against a newer
    # "exam moved to Friday" can at most renew it.
    store = _store(tmp_path)
    morning, evening = _T0, _T0 + 8 * 3600
    store.apply_decisions(_NAME, [_candidate("主人的考试改到下周五", 14, noted_at=evening)],
                          [("add", None)], now=evening)
    [entry] = store.get_active(_NAME, now=evening)

    counts = store.apply_decisions(
        _NAME,
        [_candidate("主人的考试取消了", 3, noted_at=morning),
         _candidate("主人下周三考试", 14, noted_at=morning)],
        [("remove", entry["id"]), ("replace", entry["id"])],
        now=evening + 60,
    )

    assert (counts["removed"], counts["replaced"], counts["skipped"], counts["renewed"]) == (0, 0, 1, 1)
    [after] = store.get_active(_NAME, now=evening + 60)
    assert (after["text"], after["text_at"]) == ("主人的考试改到下周五", pytest.approx(evening))


def _withdrawn_exam(store) -> dict:
    """Sunday's exam entry, withdrawn by Tuesday's "exam cancelled"."""
    store.apply_decisions(_NAME, [_candidate("主人下周三考试", 14, noted_at=_T0)],
                          [("add", None)], now=_T0)
    [exam] = store.get_active(_NAME, now=_T0)
    store.apply_decisions(_NAME, [_candidate("主人的考试取消了", 3, noted_at=_T0 + 2 * _DAY)],
                          [("remove", exam["id"])], now=_T0 + 2 * _DAY)
    assert store.get_active(_NAME, now=_T0 + 2 * _DAY) == []
    return exam


def test_a_removal_records_when_it_was_said(tmp_path):
    store = _store(tmp_path)
    exam = _withdrawn_exam(store)
    [record] = [h for h in _read_file(tmp_path)["history"] if h["action"] == "remove"]
    assert record["id"] == exam["id"]
    assert record["noted_at"] == pytest.approx(_T0 + 2 * _DAY)
    assert record["text_at"] == pytest.approx(_T0)
    assert record["expire_at"] == pytest.approx(_T0 + 14 * _DAY)


def test_a_verbatim_replay_of_a_withdrawn_entry_stays_out(tmp_path, monkeypatch):
    # Greptile #3598: Monday's delayed "exam on Wednesday" lands after Tuesday's
    # "exam cancelled" removed it. Even an empty section asks the model, and
    # the exact words stay out whatever it answers.
    store = _store(tmp_path)
    _withdrawn_exam(store)
    model = _install_model(monkeypatch, _Model([{"candidate": 1, "action": "add", "target": None}]))

    counts = asyncio.run(store.aadmit_extracted(
        _NAME, [_pin("主人下周三考试", 14)], noted_at=_T0 + _DAY, now=_T0 + 3 * _DAY,
    ))

    [prompt] = model.prompts
    assert "主人下周三考试（记于 " in prompt and "撤销）" in prompt
    assert (counts["added"], counts["skipped"]) == (0, 1)
    assert store.get_active(_NAME, now=_T0 + 3 * _DAY) == []


def test_a_withdrawn_entry_matched_by_the_model_is_settled_by_time(tmp_path, monkeypatch):
    store = _store(tmp_path)
    exam = _withdrawn_exam(store)
    model = _install_model(monkeypatch, _Model([
        {"candidate": 1, "action": "remove", "target": exam["id"]},
        {"candidate": 2, "action": "replace", "target": exam["id"]},
    ]))

    # Worded differently, so only the model can match them to the withdrawn
    # entry: the one from before the withdrawal stays out, the later one is new.
    counts = asyncio.run(store.aadmit_extracted(_NAME, [
        _pin("主人周三要考试", 14), _pin("主人又定了下周五考试", 14),
    ], noted_at=_T0 + _DAY, now=_T0 + 3 * _DAY))
    assert (counts["added"], counts["skipped"]) == (0, 2)
    # The model knowingly matched them to a withdrawn entry: settled at once,
    # not re-asked as if its view were outdated.
    assert len(model.prompts) == 1

    counts = store.apply_decisions(
        _NAME, [_candidate("主人又定了下周五考试", 14, noted_at=_T0 + 3 * _DAY)],
        [("replace", exam["id"])], now=_T0 + 3 * _DAY + 1,
    )
    assert counts["added"] == 1
    assert [e["text"] for e in store.get_active(_NAME, now=_T0 + 3 * _DAY + 1)] == [
        "主人又定了下周五考试",
    ]


def test_a_verbatim_replay_of_replaced_words_stays_out(tmp_path):
    store = _store(tmp_path)
    store.apply_decisions(_NAME, [_candidate("主人下周三考试", 14, noted_at=_T0)],
                          [("add", None)], now=_T0)
    [exam] = store.get_active(_NAME, now=_T0)
    store.apply_decisions(_NAME, [_candidate("主人的考试改到下周五", 14, noted_at=_T0 + 2 * _DAY)],
                          [("replace", exam["id"])], now=_T0 + 2 * _DAY)

    counts = store.apply_decisions(
        _NAME, [_candidate("主人下周三考试", 14, noted_at=_T0 + _DAY)], [("add", None)],
        now=_T0 + 3 * _DAY,
    )

    assert (counts["added"], counts["skipped"]) == (0, 1)
    assert [e["text"] for e in store.get_active(_NAME, now=_T0 + 3 * _DAY)] == ["主人的考试改到下周五"]


def test_removing_a_withdrawn_entry_again_pins_nothing(tmp_path):
    # Greptile #3598: two cancellations of one entry, in one batch or in two,
    # remove it once; the second must not land as an entry of its own.
    store = _store(tmp_path)
    store.apply_decisions(_NAME, [_candidate("主人下周三考试", 14, noted_at=_T0)],
                          [("add", None)], now=_T0)
    [exam] = store.get_active(_NAME, now=_T0)

    counts = store.apply_decisions(
        _NAME,
        [_candidate("主人的考试取消了", 3, noted_at=_T0 + _DAY),
         _candidate("主人不用考试了", 3, noted_at=_T0 + _DAY)],
        [("remove", exam["id"]), ("remove", exam["id"])],
        now=_T0 + _DAY,
    )
    assert (counts["removed"], counts["added"], counts["skipped"]) == (1, 0, 1)

    counts = store.apply_decisions(
        _NAME, [_candidate("主人的考试确实取消了", 3, noted_at=_T0 + 2 * _DAY)],
        [("remove", exam["id"])], now=_T0 + 2 * _DAY,
    )
    assert (counts["removed"], counts["added"], counts["skipped"]) == (0, 0, 1)
    assert store.get_active(_NAME, now=_T0 + 2 * _DAY) == []


def _exam_withdrawn_after_the_snapshot(store) -> tuple[dict, dict]:
    """The model saw the exam live; another admission then withdrew it and pinned a related entry."""
    store.apply_decisions(_NAME, [_candidate("主人下周三考试", 14, noted_at=_T0)],
                          [("add", None)], now=_T0)
    [exam] = store.get_active(_NAME, now=_T0)
    snapshot_revs = {exam["id"]: exam["rev"]}
    store.apply_decisions(
        _NAME,
        [_candidate("主人的考试取消了", 3, noted_at=_T0 + _DAY),
         _candidate("主人改成交一篇论文", 14, noted_at=_T0 + _DAY)],
        [("remove", exam["id"]), ("add", None)], now=_T0 + _DAY,
    )
    return exam, snapshot_revs


def test_a_target_withdrawn_after_the_snapshot_is_decided_again(tmp_path):
    # CodeRabbit #3598: replacing an entry that was live in the model's view
    # but withdrawn since must be decided again (with the newly pinned entry
    # in view), not turned straight into an add.
    store = _store(tmp_path)
    exam, snapshot_revs = _exam_withdrawn_after_the_snapshot(store)

    stale: list[int] = []
    counts = store.apply_decisions(
        _NAME, [_candidate("主人的论文改成下周五交", 14, noted_at=_T0 + 2 * _DAY)],
        [("replace", exam["id"])], now=_T0 + 2 * _DAY,
        expected_revs=snapshot_revs, stale_out=stale,
    )

    assert stale == [0] and counts["added"] == 0
    assert [e["text"] for e in store.get_active(_NAME, now=_T0 + 2 * _DAY)] == ["主人改成交一篇论文"]


def test_removing_what_someone_else_already_withdrew_is_not_retried(tmp_path):
    # Cloud review #3598: the removal's goal is already met; a retry would
    # see no entry to remove and might pin the cancellation instead.
    store = _store(tmp_path)
    exam, snapshot_revs = _exam_withdrawn_after_the_snapshot(store)

    stale: list[int] = []
    counts = store.apply_decisions(
        _NAME, [_candidate("主人不用考试了", 3, noted_at=_T0 + 2 * _DAY)],
        [("remove", exam["id"])], now=_T0 + 2 * _DAY,
        expected_revs=snapshot_revs, stale_out=stale,
    )

    assert stale == []
    assert (counts["skipped"], counts["added"], counts["removed"]) == (1, 0, 0)


def test_last_round_never_brings_back_what_a_later_account_withdrew(tmp_path):
    store = _store(tmp_path)
    exam, snapshot_revs = _exam_withdrawn_after_the_snapshot(store)

    counts = store.apply_decisions(
        _NAME,
        [_candidate("主人下周三考两门", 14, noted_at=_T0 + 3600),
         _candidate("主人的论文改成下周五交", 14, noted_at=_T0 + 2 * _DAY)],
        [("replace", exam["id"]), ("replace", exam["id"])], now=_T0 + 2 * _DAY,
        expected_revs=snapshot_revs, retry_stale=False,
    )

    # Said before the withdrawal: stays out. Said after it: no retry left,
    # so it is added (adding loses nothing).
    assert (counts["skipped"], counts["added"]) == (1, 1)
    assert sorted(e["text"] for e in store.get_active(_NAME, now=_T0 + 2 * _DAY)) == sorted([
        "主人改成交一篇论文", "主人的论文改成下周五交",
    ])


def test_a_second_removal_in_a_checked_round_pins_nothing(tmp_path):
    # Within one checked batch the first removal is this batch's own change,
    # so a second one aimed at it is a no-op, not an outdated view to retry.
    store = _store(tmp_path)
    store.apply_decisions(_NAME, [_candidate("主人下周三考试", 14, noted_at=_T0)],
                          [("add", None)], now=_T0)
    [exam] = store.get_active(_NAME, now=_T0)

    stale: list[int] = []
    counts = store.apply_decisions(
        _NAME,
        [_candidate("主人的考试取消了", 3, noted_at=_T0 + _DAY),
         _candidate("主人不用考试了", 3, noted_at=_T0 + _DAY)],
        [("remove", exam["id"]), ("remove", exam["id"])], now=_T0 + _DAY,
        expected_revs={exam["id"]: exam["rev"]}, stale_out=stale,
    )

    assert stale == []
    assert (counts["removed"], counts["added"], counts["skipped"]) == (1, 0, 1)


def test_every_replaced_wording_stays_out(tmp_path):
    # Cloud review #3598: replaced twice (A -> B -> C), a late replay of A
    # must stay out as well as one of B.
    store = _store(tmp_path)
    store.apply_decisions(_NAME, [_candidate("主人下周三考试", 14, noted_at=_T0)],
                          [("add", None)], now=_T0)
    [exam] = store.get_active(_NAME, now=_T0)
    for step, text in ((1, "主人的考试改到下周四"), (2, "主人的考试改到下周五")):
        store.apply_decisions(_NAME, [_candidate(text, 14, noted_at=_T0 + step * _DAY)],
                              [("replace", exam["id"])], now=_T0 + step * _DAY)

    counts = store.apply_decisions(
        _NAME,
        [_candidate("主人下周三考试", 14, noted_at=_T0 + 3600),
         _candidate("主人的考试改到下周四", 14, noted_at=_T0 + _DAY + 3600)],
        [("add", None), ("add", None)],
        now=_T0 + 3 * _DAY,
    )

    assert (counts["added"], counts["skipped"]) == (0, 2)
    assert [e["text"] for e in store.get_active(_NAME, now=_T0 + 3 * _DAY)] == ["主人的考试改到下周五"]


def test_resolve_prompt_dates_carry_the_time_of_day(tmp_path, monkeypatch):
    store = _store(tmp_path)
    asyncio.run(_seed(store, [("主人下周三考试", 14)]))
    model = _install_model(monkeypatch, _Model([{"candidate": 1, "action": "add", "target": None}]))

    asyncio.run(store.aadmit_extracted(
        _NAME, [_pin("主人周六去北京", 3)], noted_at=_T0 + 3600, now=_T0 + 7200,
    ))

    [prompt] = model.prompts
    assert datetime.fromtimestamp(_T0).strftime("%Y-%m-%d %H:%M") in prompt
    assert datetime.fromtimestamp(_T0 + 3600).strftime("%Y-%m-%d %H:%M") in prompt


def test_model_add_of_a_live_text_renews_instead_of_duplicating(tmp_path, monkeypatch):
    store = _store(tmp_path)
    [exam] = asyncio.run(_seed(store, [("主人下周三考试", 14)]))
    _install_model(monkeypatch, _Model([{"candidate": 1, "action": "add", "target": None}]))

    asyncio.run(store.aadmit_extracted(_NAME, [_pin("主人下周三考试")], now=_T0 + _DAY))

    [entry] = store.get_active(_NAME, now=_T0 + _DAY)
    assert entry["id"] == exam["id"] and entry["renew_count"] == 1


# ── 4. expiry and cap ────────────────────────────────────────────────


def test_expired_entries_leave_the_section_and_go_to_history(tmp_path, monkeypatch):
    model = _install_model(monkeypatch, _Model(error=AssertionError("no model call")))
    store = _store(tmp_path)
    [cold] = asyncio.run(_seed(store, [("主人感冒了", 3)]))
    after = _T0 + 3 * _DAY + 1

    assert store.get_active(_NAME, now=_T0 + 3 * _DAY - 1)
    assert store.get_active(_NAME, now=after) == []
    assert store.render_prompt_block(_NAME, "zh", now=after) == ""

    # The next write purges it; an expired-only section counts as empty.
    asyncio.run(store.aadmit_extracted(_NAME, [_pin("主人周六去北京", 3)], now=after))
    assert model.prompts == []
    data = _read_file(tmp_path)
    assert [e["text"] for e in data["facts"]] == ["主人周六去北京"]
    assert [(h["action"], h["id"]) for h in data["history"]] == [("expire", cold["id"])]


def test_cap_evicts_the_soonest_expiring_untouched_entry(tmp_path, monkeypatch):
    store = _store(tmp_path)
    tiers = [365, 90, 14] * 7
    full = [
        {"text": f"事实{i}", "entity": "master", "ttl_days": tiers[i]}
        for i in range(PINNED_FACT_MAX_ACTIVE)
    ]
    store.apply_decisions(_NAME, full, [("add", None)] * len(full), now=_T0)
    seeded = store.get_active(_NAME, now=_T0)
    assert len(seeded) == PINNED_FACT_MAX_ACTIVE
    soonest = min(seeded, key=lambda e: e["expire_at"])
    _install_model(monkeypatch, _Model([{"candidate": 1, "action": "add", "target": None}]))

    # The newcomer has the shortest tier of all and must survive anyway.
    counts = asyncio.run(store.aadmit_extracted(_NAME, [_pin("主人今晚加班", 3)], now=_T0 + 1))

    assert (counts["added"], counts["evicted"]) == (1, 1)
    live = store.get_active(_NAME, now=_T0 + 1)
    assert len(live) == PINNED_FACT_MAX_ACTIVE
    assert "主人今晚加班" in [e["text"] for e in live]
    assert soonest["id"] not in {e["id"] for e in live}
    assert _read_file(tmp_path)["history"][-1]["action"] == "evict"


# ── 5. character card ────────────────────────────────────────────────


def test_card_conflicts_are_rejected(tmp_path, monkeypatch):
    persona = SimpleNamespace(acontradicts_character_card=AsyncMock(
        side_effect=lambda name, text, entity=None: "讨厌咖啡" in text,
    ))
    _install_model(monkeypatch, _Model(error=AssertionError("no model call")))
    store = _store(tmp_path, persona_manager=persona)

    counts = asyncio.run(store.aadmit_extracted(
        _NAME, [_pin("小天其实不讨厌咖啡"), _pin("主人下周三考试")], now=_T0,
    ))

    assert counts["card_rejected"] == 1
    assert [e["text"] for e in store.get_active(_NAME, now=_T0)] == ["主人下周三考试"]
    persona.acontradicts_character_card.assert_any_await(
        _NAME, "小天其实不讨厌咖啡", entity="master",
    )


def test_persona_card_check_reuses_the_contradiction_rule(monkeypatch):
    from memory.persona import facts as persona_facts_mod
    from memory.persona.manager import PersonaManager

    monkeypatch.setattr(
        persona_facts_mod, "logger", SimpleNamespace(info=lambda *a, **k: None),
    )
    pm = PersonaManager.__new__(PersonaManager)
    persona = {
        "neko": {"facts": [{"text": "她讨厌咖啡", "source": "character_card"}]},
        "master": {"facts": [{"text": "主人讨厌下雨", "source": "reflection"}]},
    }
    pm.aensure_persona = AsyncMock(return_value=persona)
    pm._aget_entity_stop_names = AsyncMock(return_value=[])

    assert asyncio.run(pm.acontradicts_character_card("Neko", "她不讨厌咖啡")) is True
    assert asyncio.run(pm.acontradicts_character_card("Neko", "主人下周三考试")) is False
    # A non-card entry never rejects, however close it is.
    assert asyncio.run(pm.acontradicts_character_card("Neko", "主人不讨厌下雨")) is False
    # With an entity only that section's card entries count.
    assert asyncio.run(
        pm.acontradicts_character_card("Neko", "她不讨厌咖啡", entity="neko")
    ) is True
    assert asyncio.run(
        pm.acontradicts_character_card("Neko", "她不讨厌咖啡", entity="master")
    ) is False
    # A relationship fact has no card section of its own: both cards count.
    assert asyncio.run(
        pm.acontradicts_character_card("Neko", "她不讨厌咖啡", entity="relationship")
    ) is True


# ── 6. rendering ─────────────────────────────────────────────────────


def test_render_format_and_dates(tmp_path):
    from config.prompts.prompts_memory import get_pinned_facts_section_header

    store = _store(tmp_path)
    asyncio.run(_seed(store, [("主人下周三考试", 14)]))

    block = store.render_prompt_block(_NAME, "zh", now=_T0)

    assert block == (
        "### " + get_pinned_facts_section_header("zh") + "\n"
        "- 主人下周三考试（记于 2026-11-02，到 2026-11-16）"
    )
    assert store.render_prompt_block("someone-else", "zh", now=_T0) == ""


def test_render_respects_token_budget_and_prefers_recent(tmp_path):
    from utils.tokenize import count_tokens

    store = _store(tmp_path)
    long_text = "主人" + "非常重要的长期安排细节" * 8
    candidates = [
        {"text": f"{i:02d}{long_text}", "entity": "master", "ttl_days": 365}
        for i in range(PINNED_FACT_MAX_ACTIVE)
    ]
    for i, candidate in enumerate(candidates):
        store.apply_decisions(_NAME, [candidate], [("add", None)], now=_T0 + i)

    block = store.render_prompt_block(_NAME, "zh", now=_T0 + 100)

    assert count_tokens(block) <= PINNED_FACT_RENDER_MAX_TOKENS
    lines = block.split("\n")[1:]
    assert 0 < len(lines) < PINNED_FACT_MAX_ACTIVE
    prefixes = [line[2:4] for line in lines]
    # The most recently updated entries survive, rendered in file order.
    assert prefixes == sorted(prefixes)
    assert prefixes[-1] == f"{PINNED_FACT_MAX_ACTIVE - 1:02d}"
    assert "00" not in prefixes


def _fake_new_dialog_runtime(monkeypatch, tmp_path, pinned_store):
    from app.memory_server import locale_state, routes, runtime

    cm = _cm(tmp_path)
    cm.aload_characters = AsyncMock(return_value={"猫娘": {_NAME: {}}})
    monkeypatch.setattr(runtime, "_config_manager", cm)
    monkeypatch.setattr(runtime, "_get_settle_lock", lambda _name: asyncio.Lock())
    monkeypatch.setattr(locale_state, "get_character_prompt_locale", lambda _name: None)
    monkeypatch.setattr("app.memory_server.long_term_sync.consume_for_name", AsyncMock())
    monkeypatch.setattr(runtime, "reflection_engine", SimpleNamespace(
        aupdate_suppressions=AsyncMock(),
        aget_pending_reflections=AsyncMock(return_value=[]),
        aget_confirmed_reflections=AsyncMock(return_value=[]),
    ))
    monkeypatch.setattr(runtime, "persona_manager", SimpleNamespace(
        arender_persona_markdown=AsyncMock(return_value="### 关于主人\n- 主人喜欢咖啡"),
    ))
    monkeypatch.setattr(runtime, "recent_history_manager", SimpleNamespace(
        aget_recent_history=AsyncMock(return_value=[]),
    ))
    monkeypatch.setattr(runtime, "time_manager", SimpleNamespace(
        aget_last_conversation_time=AsyncMock(return_value=None),
    ))
    monkeypatch.setattr("utils.holiday_cache.get_holiday_context_line", lambda _lang: None)
    monkeypatch.setattr(runtime, "pinned_fact_store", pinned_store)
    return routes


def test_new_dialog_renders_pinned_section_before_persona(tmp_path, monkeypatch):
    from config.prompts.prompts_memory import PERSONA_HEADER, get_pinned_facts_section_header

    store = _store(tmp_path)
    asyncio.run(_seed(store, [("主人下周三考试", 14)], now=pinned_mod._now()))
    routes = _fake_new_dialog_runtime(monkeypatch, tmp_path, store)

    response = asyncio.run(routes._new_dialog(_NAME, render_language="zh"))
    text = response.body.decode("utf-8")

    header = PERSONA_HEADER["zh"].format(name=_NAME)
    pinned = "### " + get_pinned_facts_section_header("zh")
    assert text.startswith(header + pinned + "\n- 主人下周三考试（记于 ")
    assert text.index(pinned) < text.index("### 关于主人")


def test_new_dialog_without_pinned_entries_is_unchanged(tmp_path, monkeypatch):
    from config.prompts.prompts_memory import PERSONA_HEADER

    routes = _fake_new_dialog_runtime(monkeypatch, tmp_path, _store(tmp_path))
    with_empty_store = asyncio.run(routes._new_dialog(_NAME, render_language="zh")).body
    routes = _fake_new_dialog_runtime(monkeypatch, tmp_path, None)
    without_store = asyncio.run(routes._new_dialog(_NAME, render_language="zh")).body

    assert with_empty_store == without_store
    assert without_store.decode("utf-8").startswith(
        PERSONA_HEADER["zh"].format(name=_NAME) + "### 关于主人"
    )


def _functions_referencing(tree: ast.AST, attr: str) -> set[str]:
    found = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for sub in ast.walk(node):
                name = sub.attr if isinstance(sub, ast.Attribute) else (
                    sub.id if isinstance(sub, ast.Name) else None
                )
                if name == attr:
                    found.add(node.name)
    return found


def test_only_the_private_new_dialog_renders_the_pinned_section():
    """Scoped (group / member) prompts must never carry private pinned facts.

    Discovery-based: every function in app/ and main_logic/ that touches the
    store or the render helper is listed, so a new renderer shows up here.
    """
    touching_store: dict[str, set[str]] = {}
    calling_render: dict[str, set[str]] = {}
    scanned = 0
    for root in ("app", "main_logic"):
        for path in (_REPO_ROOT / root).rglob("*.py"):
            rel = path.relative_to(_REPO_ROOT).as_posix()
            tree = ast.parse(path.read_text(encoding="utf-8"))
            scanned += 1
            if names := _functions_referencing(tree, "pinned_fact_store"):
                touching_store[rel] = names
            if names := _functions_referencing(tree, "_render_pinned_facts_block"):
                calling_render[rel] = names
    assert scanned > 100
    # runtime.py only builds and wires it; routes.py reads it in one helper.
    assert touching_store == {
        "app/memory_server/runtime.py": {
            "reload_memory_components", "ensure_memory_server_runtime_initialized",
        },
        "app/memory_server/routes.py": {"_render_pinned_facts_block"},
    }
    assert calling_render == {"app/memory_server/routes.py": {"_new_dialog"}}


# ── 7. FactStore wiring ──────────────────────────────────────────────


def _fact_store(tmp_path):
    from memory.facts import FactStore

    cm = _cm(tmp_path)
    with patch("memory.facts.get_config_manager", return_value=cm):
        fs = FactStore()
    fs._config_manager = cm
    recorder = SimpleNamespace(aadmit_extracted=AsyncMock(return_value={}))
    fs.attach_pinned_fact_store(recorder)
    return fs, recorder


def test_stage1_and_stage2_entry_feeds_the_raw_result(tmp_path):
    fs, recorder = _fact_store(tmp_path)
    extracted = [_pin("主人下周三考试")]
    fs._allm_extract_facts = AsyncMock(return_value=extracted)
    # Dedup folded the fact into an existing row: nothing new persisted, but
    # the pinned entry still needs its renewal.
    fs._apersist_new_facts = AsyncMock(return_value=[])
    fs.aload_facts = AsyncMock(return_value=[])

    window_end = datetime(2026, 11, 1, 21, 30)
    asyncio.run(fs.aextract_facts_and_detect_signals(
        _NAME, [], conversation_at=window_end,
    ))

    recorder.aadmit_extracted.assert_awaited_once_with(
        _NAME, extracted, default_source="user_observation",
        noted_at=window_end.timestamp(),
    )


def test_extract_facts_feeds_private_chat_only(tmp_path):
    from memory.scopes import MemorySubject

    fs, recorder = _fact_store(tmp_path)
    extracted = [_pin("主人下周三考试")]
    fs._allm_extract_facts = AsyncMock(return_value=extracted)
    fs._apersist_new_facts = AsyncMock(return_value=[{"id": "f1"}])

    assert asyncio.run(fs.extract_facts([], _NAME)) == [{"id": "f1"}]
    recorder.aadmit_extracted.assert_awaited_once_with(
        _NAME, extracted, default_source="user_observation", noted_at=None,
    )

    recorder.aadmit_extracted.reset_mock()
    asyncio.run(fs.extract_facts(
        [], _NAME, subject=MemorySubject.group_chat("qq", "g1"),
    ))
    recorder.aadmit_extracted.assert_not_awaited()


def test_path_b_feeds_private_chat_only_with_its_source_default(tmp_path):
    from memory.scopes import MemorySubject

    fs, recorder = _fact_store(tmp_path)
    extracted = [_pin("主人周五面试", source="user_observation")]
    fs._allm_extract_facts_with_known_pool = AsyncMock(return_value=extracted)
    fs._apersist_new_facts = AsyncMock(return_value=[])

    window_end = datetime(2026, 11, 1, 21, 30)
    asyncio.run(fs.aextract_facts_with_known_pool(
        _NAME, [], [], conversation_at=window_end,
    ))
    recorder.aadmit_extracted.assert_awaited_once_with(
        _NAME, extracted, default_source="ai_disclosure",
        noted_at=window_end.timestamp(),
    )

    recorder.aadmit_extracted.reset_mock()
    asyncio.run(fs.aextract_facts_with_known_pool(
        _NAME, [], [], subject=MemorySubject.participant("qq", "u1"),
    ))
    recorder.aadmit_extracted.assert_not_awaited()


def test_admission_failure_never_breaks_extraction(tmp_path):
    fs, recorder = _fact_store(tmp_path)
    recorder.aadmit_extracted.side_effect = RuntimeError("disk full")
    fs._allm_extract_facts = AsyncMock(return_value=[_pin("主人下周三考试")])
    fs._apersist_new_facts = AsyncMock(return_value=[{"id": "f1"}])

    assert asyncio.run(fs.extract_facts([], _NAME)) == [{"id": "f1"}]


def test_end_to_end_legacy_output_through_fact_store(tmp_path, monkeypatch):
    """A real store behind a real FactStore: legacy output pins nothing."""
    fs, _ = _fact_store(tmp_path)
    store = _store(tmp_path)
    fs.attach_pinned_fact_store(store)
    fs._apersist_new_facts = AsyncMock(return_value=[])
    fs._allm_extract_facts = AsyncMock(return_value=[
        {"text": "主人喜欢喝咖啡", "importance": 7, "entity": "master"},
    ])
    asyncio.run(fs.extract_facts([], _NAME))
    assert store.get_active(_NAME) == []

    fs._allm_extract_facts = AsyncMock(return_value=[_pin("主人下周三考试")])
    asyncio.run(fs.extract_facts([], _NAME))
    assert [e["text"] for e in store.get_active(_NAME)] == ["主人下周三考试"]


def test_post_turn_payload_carries_the_turn_time_into_replay(monkeypatch):
    """A turn replayed after a restart is extracted with the time it happened."""
    from app.memory_server import post_turn, runtime
    from utils.llm_client import convert_to_messages, messages_to_dict

    messages = convert_to_messages([{"type": "human", "data": {"content": "记住我明天考试"}}])
    captured: dict = {}

    async def append_pending(name, op, payload):
        captured.update(payload)
        return "op-1"

    async def run_op(name, op):
        return None

    monkeypatch.setattr(runtime, "outbox", SimpleNamespace(aappend_pending=append_pending))
    monkeypatch.setattr(post_turn.outbox_infra, "_run_outbox_op", run_op)
    monkeypatch.setattr(runtime, "_spawn_background_task", lambda coro: coro.close())
    monkeypatch.setattr(post_turn, "_track_character_post_turn_task", lambda name, task: task)

    before = pinned_mod._now()
    asyncio.run(post_turn._spawn_outbox_post_turn_signals(_NAME, messages))
    assert before <= captured["turn_at"] <= pinned_mod._now()

    seen: dict = {}

    async def run_signals(msgs, name, **kwargs):
        seen.update(kwargs)

    monkeypatch.setattr(post_turn, "_run_post_turn_signals", run_signals)
    asyncio.run(post_turn._outbox_post_turn_signals_handler(_NAME, {**captured, "turn_at": _T0}))
    assert seen["conversation_at"] == datetime.fromtimestamp(_T0)

    # Payloads written before this field existed replay as "now", unchanged.
    seen.clear()
    legacy = {k: v for k, v in captured.items() if k != "turn_at"}
    asyncio.run(post_turn._outbox_post_turn_signals_handler(_NAME, legacy))
    assert seen and "conversation_at" not in seen


def test_extraction_callers_pass_the_conversation_time():
    """Every app-layer call into a pinned-feeding extraction entry dates it."""
    entries = {"aextract_facts_and_detect_signals", "aextract_facts_with_known_pool", "extract_facts"}
    calls = []
    for rel in ("app/memory_server/signal_extraction.py", "app/memory_server/post_turn.py"):
        tree = ast.parse((_REPO_ROOT / rel).read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                    and node.func.attr in entries):
                calls.append((rel, node.func.attr, {k.arg for k in node.keywords}))
    assert sorted((rel, attr) for rel, attr, _ in calls) == [
        ("app/memory_server/post_turn.py", "extract_facts"),
        ("app/memory_server/signal_extraction.py", "aextract_facts_and_detect_signals"),
        ("app/memory_server/signal_extraction.py", "aextract_facts_with_known_pool"),
    ]
    assert all("conversation_at" in keywords for _, _, keywords in calls)


def test_runtime_wires_the_store_on_startup_and_reload():
    source = (_REPO_ROOT / "app" / "memory_server" / "runtime.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    wired_in = {
        node.name
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        for sub in ast.walk(node)
        if isinstance(sub, ast.Attribute) and sub.attr == "attach_pinned_fact_store"
    }
    dedup_wired_in = {
        node.name
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        for sub in ast.walk(node)
        if isinstance(sub, ast.Attribute) and sub.attr == "attach_dedup_resolver"
    }
    assert wired_in == dedup_wired_in == {
        "reload_memory_components", "ensure_memory_server_runtime_initialized",
    }


# ── 8. prompt tables ─────────────────────────────────────────────────


def test_pinned_prompt_tables_cover_every_locale():
    from config.prompts import prompts_memory as pm

    for table in (pm.PINNED_FACTS_SECTION_HEADER, pm.PINNED_FACT_LINE,
                  pm.PINNED_FACT_REMOVED_LINE, pm.PINNED_FACT_RESOLVE_PROMPT):
        assert set(table) == _LOCALES
    for lang, line in pm.PINNED_FACT_REMOVED_LINE.items():
        rendered = line.format(text="T", since="2026-01-02 08:00", removed="2026-01-03 09:00")
        assert "2026-01-02 08:00" in rendered and "2026-01-03 09:00" in rendered, lang
    for lang, line in pm.PINNED_FACT_LINE.items():
        rendered = line.format(text="T", since="2026-01-02", until="2026-03-04")
        assert "2026-01-02" in rendered and "2026-03-04" in rendered, lang
    for lang, prompt in pm.PINNED_FACT_RESOLVE_PROMPT.items():
        for token in ("{LANLAN_NAME}", "{MASTER_NAME}", "{TODAY}", "{ZONE}", "{CANDIDATES}",
                      '"add"', '"replace"', "renew", "remove"):
            assert token in prompt, (lang, token)
        delimiters = [ln for ln in prompt.splitlines() if ln.startswith("======")]
        assert len(delimiters) == 4, lang


def test_both_private_extraction_prompts_carry_the_pin_fields():
    from config.prompts import prompts_memory as pm

    for table in (pm.FACT_EXTRACTION_PROMPT, pm.FACT_EXTRACTION_AI_AWARE_PROMPT):
        assert set(table) == _LOCALES
        for lang, prompt in table.items():
            assert '"pin": true' in prompt, lang
            assert '"pin_ttl_days": 14' in prompt, lang
            assert "3 / 14 / 90 / 365" in prompt, lang
    # The scoped batch prompt deliberately has no pin fields: scoped facts
    # never enter the private-only section.
    for lang, prompt in pm.FACT_EXTRACTION_BATCH_PROMPT.items():
        assert "pin_ttl_days" not in prompt, lang
