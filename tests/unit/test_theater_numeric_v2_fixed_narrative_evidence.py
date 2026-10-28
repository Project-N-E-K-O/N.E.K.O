"""Delivered fixed originals are bounded sources, with new-record order separate from old replay."""

from copy import deepcopy
import json
from types import SimpleNamespace

import pytest

from services.theater import numeric_v2_narrative as narrative
from services.theater import numeric_v2_narrative_proposer as proposer
from tests.unit.test_theater_numeric_v2_narrative_proposer import _session


def _piece(text, position="after", piece_id="notice"):
    return {"node_id": "scene", "id": piece_id, "text": text, "position": position,
            "bindings": {"catgirl_name": "柚", "player_name": "阿澄"}}


def _event(quote, value, *, source="scene_narration", phase="turn", entity="item:木匣"):
    return {"phase": phase, "subject": "character", "kind": "action", "source": source,
            "quote": quote, "known_to": ["player", "character"],
            "changes": [{"entity": entity, "attribute": "location", "value": value}]}


def _review(performance):
    return SimpleNamespace(reviewed_narrative_events=narrative.narrative_event_table(
        performance, "先观察。", opening=performance.get("test_opening", False)),
        approved_narrative_event_indexes=tuple(range(len(performance["narrative_events"]))),
        reviewed_narrative_body=narrative.narrative_body_fingerprint(performance),
        narrative_review_complete=True)


@pytest.mark.parametrize("phase", ["opening", "turn", "source_response", "target_opening"])
@pytest.mark.parametrize("position", ["before", "after"])
def test_delivered_fixed_text_is_a_single_source_in_its_actual_phase(phase, position):
    row = {"performance": "我先看看。", "fixed_narrations": [_piece("木匣在柜台。", position)]}
    performance = row if phase in {"opening", "turn"} else {"segments": [{"phase": phase, **row}]}
    performance["test_opening"] = phase == "opening"
    performance["narrative_events"] = [_event("木匣在柜台。", "柜台", phase=phase)]
    original = deepcopy(performance)
    table = narrative.narrative_event_table(performance, "先观察。", opening=phase == "opening")
    assert table == tuple(performance["narrative_events"])
    record = narrative.make_narrative_record(performance, "先观察。", _review(performance), opening=phase == "opening")
    assert record["coverage"] == "confirmed_changes"
    state = narrative.apply_narrative_record(None, record, revision=0 if phase == "opening" else 1,
        node_id="scene", from_node_id="scene", performance=performance, message="先观察。")
    assert state["entities"]["item:木匣"]["location"]["value"] == "柜台"
    assert performance == original


@pytest.mark.parametrize("mutation", [
    {"source": "performance"}, {"phase": "source_response"},
    {"quote": "不存在的完整原文。"},
])
def test_wrong_source_phase_and_fabricated_fixed_quotes_do_not_become_evidence(mutation):
    performance = {"performance": "我先观察。", "fixed_narrations": [_piece("木匣在柜台。")],
                   "narrative_events": [{**_event("木匣在柜台。", "柜台"), **mutation}]}
    with pytest.raises(ValueError, match="narrative_candidates_invalid"):
        narrative.narrative_event_table(performance, "先观察。")


def test_quote_cannot_be_spliced_across_separate_fixed_originals():
    performance = {"performance": "我先观察。", "fixed_narrations": [
        _piece("木匣在", piece_id="first"), _piece("柜台。", piece_id="second")],
        "narrative_events": [_event("木匣在\n柜台。", "柜台")]}
    with pytest.raises(ValueError, match="narrative_candidates_invalid"):
        narrative.narrative_event_table(performance, "先观察。")


def test_undelivered_author_definitions_are_never_current_sources():
    performance = {"performance": "我先观察。", "author_fixed_definitions": [_piece("木匣在柜台。")],
                   "narrative_events": [_event("木匣在柜台。", "柜台")]}
    with pytest.raises(ValueError, match="narrative_candidates_invalid"):
        narrative.narrative_event_table(performance, "先观察。")


def test_proposer_exposes_actual_order_without_repeating_dynamic_fields_or_templates():
    performance = {"scene_narration": "木匣先在桌上。", "performance": "（我把木匣放到门边）",
        "fixed_narrations": [_piece("木匣的封条还在。", "before", "first"), _piece("木匣后来在柜台。")],
        "author_fixed_definitions": [_piece("未触发的幕后原文。")], "suggested_inputs": ["未选的动作"]}
    messages = proposer._messages(performance, "先观察。", _session(), False)
    row = json.loads(messages[1].content)["phase_texts"][1]
    assert row == {"phase": "turn", "ordered_sources": [
        {"source": "scene_narration", "text": "木匣先在桌上。"},
        {"source": "scene_narration", "text": "木匣的封条还在。"},
        {"source": "performance", "text": "（我把木匣放到门边）"},
        {"source": "scene_narration", "text": "木匣后来在柜台。"},
    ]}
    assert "未触发的幕后原文" not in messages[1].content and "未选的动作" not in messages[1].content


def test_new_records_order_approved_events_by_display_without_reindexing_the_frozen_table():
    before, action, after = "木匣在桌上。", "（我把木匣放到门边）", "木匣后来在柜台。"
    events = [_event(after, "柜台"), _event(before, "桌上"), _event(action, "门边", source="performance")]
    performance = {"performance": action, "fixed_narrations": [
        _piece(before, "before", "first"), _piece(after)], "narrative_events": events}
    review = _review(performance)
    assert review.reviewed_narrative_events == tuple(events)
    record = narrative.make_narrative_record(performance, "先观察。", review)
    assert [event["quote"] for event in record["events"]] == [before, action, after]
    assert review.reviewed_narrative_events == tuple(events)
    state = narrative.apply_narrative_record(None, record, revision=1, node_id="scene", from_node_id="scene",
        performance=performance, message="先观察。")
    assert state["coverage"] == "confirmed_changes"
    assert state["entities"]["item:木匣"]["location"]["value"] == "柜台"
    assert [event["changes"][0]["before"] for event in state["recent_events"]] == [None, "桌上", "门边"]


@pytest.mark.parametrize("within_block", [False, True])
def test_nonunique_current_source_position_never_creates_a_confirmed_state(within_block):
    text = "木匣在柜台。"
    performance = {"performance": "我先观察。", "fixed_narrations": (
        [_piece(text + text)] if within_block else [_piece(text, "before", "first"), _piece(text)]),
        "narrative_events": [_event(text, "柜台")]}
    record = narrative.make_narrative_record(performance, "先观察。", _review(performance))
    assert record["coverage"] == "unconfirmed" and record["events"] == []


def test_overlapping_quotes_cannot_choose_two_different_values_for_one_attribute():
    text = "木匣先在桌上，后来移到门边。"
    performance = {"performance": text, "fixed_narrations": [_piece("远处的灯亮着。")],
        "narrative_events": [_event(text, "桌上", source="performance"),
                             _event("后来移到门边。", "门边", source="performance")]}
    record = narrative.make_narrative_record(performance, "先观察。", _review(performance))
    assert record["coverage"] == "unconfirmed" and record["events"] == []


def test_exact_dynamic_echo_of_before_original_has_only_one_display_position():
    text = "木匣在柜台。"
    performance = {"scene_narration": text, "performance": "我先观察。",
        "fixed_narrations": [_piece(text, "before")], "narrative_events": [_event(text, "柜台")]}
    record = narrative.make_narrative_record(performance, "先观察。", _review(performance))
    assert record["coverage"] == "confirmed_changes"


def test_fixed_source_order_never_promotes_a_hidden_legacy_scene_update():
    performance = {"scene_update": "木匣在柜台。", "performance": "我先观察。",
        "fixed_narrations": [_piece("远处的风停了。")],
        "narrative_events": [_event("木匣在柜台。", "柜台")]}
    messages = proposer._messages(performance, "先观察。", _session(), False)
    assert "木匣在柜台" not in messages[1].content
    # 旧引用仍可解析／重放，但新记录不能把播放器不展示的别名当作实际时点。
    record = narrative.make_narrative_record(performance, "先观察。", _review(performance))
    assert record["coverage"] == "unconfirmed" and record["events"] == []


def test_after_original_cannot_bind_an_entity_for_an_earlier_pronoun_event():
    early = _event("（把它放到门边）", "门边", source="performance")
    late = _event("木匣后来在柜台。", "柜台")
    performance = {"performance": early["quote"], "fixed_narrations": [_piece(late["quote"])],
                   "narrative_events": [late, early]}
    record = narrative.make_narrative_record(performance, "先观察。", _review(performance))
    assert record["coverage"] == "partial" and record["events"] == [late]


def test_late_fixed_insertion_still_invalidates_prior_body_approval():
    event = _event("（木匣放在门边）", "门边", source="performance")
    performance = {"performance": event["quote"], "narrative_events": [event]}
    review = _review(performance)
    performance["fixed_narrations"] = [_piece("木匣后来在柜台。")]
    record = narrative.make_narrative_record(performance, "先观察。", review)
    assert record["coverage"] == "unconfirmed" and record["events"] == []
