"""Retrieving a past question must retain its actual reply without inferring facts."""

from copy import deepcopy
import json
from types import SimpleNamespace

import pytest

from services.theater.numeric_v2_context import history_evidence, performance_history_records
from services.theater.numeric_v2_budget import NUMERIC_V2_ACTOR_BUDGET_PROFILES
from utils.tokenize import count_tokens


def _session(*records):
    return SimpleNamespace(
        current_node_id="current", revision=records[-1]["revision"],
        actor_budget_profile="balanced", opening_performance={},
        performance_history=records,
    )


def _turn(revision, message="", performance=""):
    return dict(revision=revision, from_node_id="old", to_node_id="old",
                input_text=message, performance=performance)


def _entry(revision=3):
    return dict(revision=revision, from_node_id="old", to_node_id="current",
                segments=[dict(phase="target_opening", performance="我们坐下休息。")])


@pytest.mark.parametrize("previous,question,reply", [
    ("观察日记可以公开展示。", "观察日记可以展出吗？", "不，先保密。"),
    ("资料袋暂由管理员保管。", "资料袋现在交给我吗？", "行，你收着吧。"),
])
@pytest.mark.parametrize("lookup_only", [False, True])
def test_old_question_recalls_its_complete_reply_without_repeated_object_name(previous, question, reply, lookup_only):
    session = _session(_turn(1, performance=previous), _turn(2, question, reply), _entry())
    before = deepcopy(session)
    question_row = next(row for row in performance_history_records(session)
                        if row["revision"] == 2 and row["source"] == "player_input")
    lookup = {"evidence": [question_row]} if lookup_only else None
    selected = history_evidence(session, "那件事呢？" if lookup_only else question, lookup=lookup)
    assert any(row["revision"] == 2 and row["source"] == "performance"
               and row["text"] == reply and row["current_visit"] is False for row in selected)
    assert question_row in selected
    assert all(row in performance_history_records(session) for row in selected)
    assert session == before


def test_long_question_cannot_crowd_out_its_short_reply():
    reply = "不同意，仍由我保管。"
    session = _session(_turn(1, "资料袋能交给我吗？" + "这是我的理由。" * 100, reply), _entry(2))
    rows = history_evidence(session, "资料袋能交给我吗？", max_tokens=150)
    assert [row["text"] for row in rows] == [reply]
    assert rows[0]["source"] == "performance" and not rows[0]["current_visit"]
    assert count_tokens(json.dumps(rows, ensure_ascii=False, separators=(",", ":"))) <= 150


@pytest.mark.parametrize("current_entry", [False, True])
@pytest.mark.parametrize("lookup_only", [False, True])
def test_crossing_question_does_not_retrieve_unrelated_target_or_bridge(current_entry, lookup_only):
    crossing = dict(revision=1, from_node_id="old", to_node_id="current" if current_entry else "middle",
                    input_text="观察日记可以展出吗？", segments=[
                        dict(phase="source_response", performance="不，先保密。"),
                        dict(phase="transition_bridge", performance="路上飘起细雨。"),
                        dict(phase="target_opening", performance="窗边摆着两把椅子。"),
                    ])
    session = _session(crossing) if current_entry else _session(crossing, _entry(2))
    question_row = next(row for row in performance_history_records(session) if row["source"] == "player_input")
    selected = history_evidence(session, "那件事呢？" if lookup_only else crossing["input_text"],
                                lookup={"evidence": [question_row]} if lookup_only else None)
    assert question_row in selected
    # A crossing record combines different scenes; sharing a revision does not
    # grant its unrelated source response, bridge or target the question's score.
    assert all(row["source"] == "player_input" for row in selected)


def test_unmatched_question_does_not_recall_unrelated_reply():
    session = _session(_turn(1, "我们现在已经到了哪里？", "先保密。"), _entry(2))
    assert history_evidence(session, "观察日记可以展出吗？") == []


def test_reply_remains_whole_including_final_restriction():
    reply = "我理解你的理由。" * 30 + "但仍然不同意，不能公开。"
    session = _session(_turn(1, "观察日记可以展出吗？", reply), _entry(2))
    rows = history_evidence(session, "观察日记可以展出吗？")
    assert any(row["source"] == "performance" and row["text"] == reply for row in rows)


def test_no_text_is_forced_beyond_evidence_budget():
    session = _session(_turn(1, "观察日记可以展出吗？", "不，先保密。"), _entry(2))
    assert history_evidence(session, "观察日记可以展出吗？", max_tokens=0) == []


@pytest.mark.parametrize("field", ["claims", "focus"])
def test_candidate_claim_or_author_direction_does_not_expand_to_unmatched_reply(field):
    session = _session(_turn(1, "观察日记可以展出吗？", "不，先保密。"), _entry(2))
    rows = history_evidence(session, "天气如何？", **{field: "观察日记可以展出吗？"})
    assert any(row["source"] == "player_input" for row in rows)
    assert all(row["text"] != "不，先保密。" for row in rows)


def test_previous_visible_object_retrieves_its_old_question_and_reply():
    session = _session(_turn(1, "银纹胸针还在木盒里吗？", "不在，后来交给保管人了。"), _entry(2),
                       dict(revision=3, from_node_id="current", to_node_id="current",
                            performance="我们再看看银纹胸针。"))
    rows = history_evidence(session, "还有呢？")
    assert any(row["revision"] == 1 and row["source"] == "performance"
               and row["text"] == "不在，后来交给保管人了。" for row in rows)


def test_single_available_unit_prefers_actual_reply_over_question(monkeypatch):
    session = _session(_turn(1, "观察日记可以展出吗？", "不，先保密。"), _entry(2))
    monkeypatch.setitem(NUMERIC_V2_ACTOR_BUDGET_PROFILES["balanced"], "evidence_max_units", 1)
    rows = history_evidence(session, "观察日记可以展出吗？")
    assert [row["text"] for row in rows] == ["不，先保密。"]
    assert rows[0]["source"] == "performance"


def test_missing_scene_ids_do_not_establish_ordinary_turn_ownership():
    session = _session(dict(revision=1, input_text="观察日记可以展出吗？", performance="不，先保密。"), _entry(2))
    rows = history_evidence(session, "观察日记可以展出吗？")
    assert [row["text"] for row in rows] == ["观察日记可以展出吗？"]


def test_foreign_lookup_question_cannot_seed_reply_association():
    session = _session(_turn(1, "观察日记可以展出吗？", "不，先保密。"), _entry(2))
    original = next(row for row in performance_history_records(session) if row["source"] == "player_input")
    lookup = {"evidence": [{**original, "text": "另一份不存在的问句。"}]}
    assert history_evidence(session, "天气如何？", lookup=lookup) == []


def test_related_reply_fields_keep_original_order_and_source():
    record = {**_turn(1, "观察日记可以展出吗？", "不，先保密。"),
              "scene_narration": "门边响起脚步声。",
              "fixed_narrations": [dict(position="before", text="窗外风吹过。"),
                                   dict(position="after", text="屋里重新安静下来。")]}
    session = _session(record, _entry(2))
    rows = history_evidence(session, "观察日记可以展出吗？")
    assert [row["text"] for row in rows if row["source"] == "performance"] == [
        "门边响起脚步声。", "窗外风吹过。", "不，先保密。", "屋里重新安静下来。",
    ]
    assert all(row in performance_history_records(session) and not row["current_visit"] for row in rows)


@pytest.mark.parametrize("budget,repeats", [(1500, 180)])
def test_associated_long_reply_cannot_displace_direct_lookup_originals(budget, repeats):
    session = _session(_turn(1, performance="公开许可已经撤回，当前仍然保密。"),
                       _turn(2, "晚餐要吃什么？", "这段回应只是在讨论晚餐。" * repeats), _entry())
    units = performance_history_records(session)
    originals = [row for row in units if row["revision"] == 1
                 or (row["revision"] == 2 and row["source"] == "player_input")]
    reply = next(row for row in units if row["revision"] == 2 and row["source"] == "performance")
    # Both direct lookup hits fit. The related dinner reply fits on its own but
    # must not spend the budget ahead of the explicitly retrieved withdrawal.
    tokens = lambda rows: count_tokens(json.dumps(rows, ensure_ascii=False, separators=(",", ":")))
    assert tokens(originals) <= budget and tokens([reply]) <= budget < tokens([*originals, reply])
    rows = history_evidence(session, "那两件事呢？", lookup={"evidence": originals}, max_tokens=budget)
    assert all(row in rows for row in originals)
    assert reply not in rows
    assert tokens(rows) <= budget
