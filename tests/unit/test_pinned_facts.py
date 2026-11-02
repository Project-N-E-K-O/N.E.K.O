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

    async def model(api_config, prompt, *, timeout, call_label):
        store.apply_decisions(
            _NAME, [{"text": "主人对花生过敏", "entity": "master", "ttl_days": 365}],
            [("add", None)], now=_T0 + 1,
        )
        newcomer.update(store.get_active(_NAME, now=_T0 + 1)[-1])
        answer = [{"candidate": 1, "action": "replace", "target": newcomer["id"]}]
        return SimpleNamespace(content=json.dumps(answer)), 0

    monkeypatch.setattr("memory.thinking_llm.ainvoke_thinking", model)

    asyncio.run(store.aadmit_extracted(_NAME, [_pin("主人周六去北京", 3)], now=_T0 + 2))

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


def test_targets_gone_before_apply_degrade(tmp_path):
    # The model answered against a snapshot; another admission removed the
    # entry before this one applied.
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
        side_effect=lambda name, text: "讨厌咖啡" in text,
    ))
    _install_model(monkeypatch, _Model(error=AssertionError("no model call")))
    store = _store(tmp_path, persona_manager=persona)

    counts = asyncio.run(store.aadmit_extracted(
        _NAME, [_pin("小天其实不讨厌咖啡"), _pin("主人下周三考试")], now=_T0,
    ))

    assert counts["card_rejected"] == 1
    assert [e["text"] for e in store.get_active(_NAME, now=_T0)] == ["主人下周三考试"]
    persona.acontradicts_character_card.assert_any_await(_NAME, "小天其实不讨厌咖啡")


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

    asyncio.run(fs.aextract_facts_and_detect_signals(_NAME, []))

    recorder.aadmit_extracted.assert_awaited_once_with(
        _NAME, extracted, default_source="user_observation",
    )


def test_extract_facts_feeds_private_chat_only(tmp_path):
    from memory.scopes import MemorySubject

    fs, recorder = _fact_store(tmp_path)
    extracted = [_pin("主人下周三考试")]
    fs._allm_extract_facts = AsyncMock(return_value=extracted)
    fs._apersist_new_facts = AsyncMock(return_value=[{"id": "f1"}])

    assert asyncio.run(fs.extract_facts([], _NAME)) == [{"id": "f1"}]
    recorder.aadmit_extracted.assert_awaited_once_with(
        _NAME, extracted, default_source="user_observation",
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

    asyncio.run(fs.aextract_facts_with_known_pool(_NAME, [], []))
    recorder.aadmit_extracted.assert_awaited_once_with(
        _NAME, extracted, default_source="ai_disclosure",
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
                  pm.PINNED_FACT_RESOLVE_PROMPT):
        assert set(table) == _LOCALES
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
