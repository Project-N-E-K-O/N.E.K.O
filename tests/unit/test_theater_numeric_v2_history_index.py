"""Complete committed excerpts retain outcomes without making a second fact store."""

import json
from copy import deepcopy
from types import SimpleNamespace

import pytest

from services.theater.numeric_v2_context import current_scene_history_index
from services.theater.numeric_v2_actor import _current_scene_fact_index
from utils.tokenize import count_tokens


def _session(*records):
    return SimpleNamespace(
        current_node_id="current",
        revision=records[-1]["revision"] if records else 0,
        opening_performance={},
        performance_history=records,
    )


def _turn(revision, message="", performance="", **fields):
    return {
        "revision": revision,
        "from_node_id": "current",
        "to_node_id": "current",
        "input_text": message,
        "performance": performance,
        **fields,
    }


@pytest.mark.parametrize("outcome", [
    "没有获得对方许可，这次不能打开资料袋，资料袋仍由保管人拿着。",
])
def test_long_request_cannot_replace_its_actual_complete_outcome(outcome):
    session = _session(_turn(1, "我想打开资料袋。" + "请先听我解释具体考虑。" * 100, outcome))
    before = deepcopy(session)
    index = current_scene_history_index(session, "资料袋处理了吗？", max_tokens=300)
    assert [unit["text"] for unit in index["evidence"]] == [outcome]
    assert index["evidence"][0]["source"] == "performance"
    assert index["evidence"][0]["revision"] == 1
    assert index["omitted_units"] == 1
    assert session == before


def test_actor_index_adapter_preserves_complete_reply_and_query_relevance():
    """The actual Actor adapter must not reintroduce the old request-prefix index."""
    outcome = "不同意公开观察记录，请继续保密。"
    session = _session(
        _turn(1, "能公开观察记录吗？" + "我想解释我的理由。" * 100, outcome),
        *(_turn(i, performance="门边的风铃轻响。" * 8) for i in range(2, 10)),
    )
    index = _current_scene_fact_index(session, query="观察记录可以公开吗？", max_tokens=180)
    rendered = json.dumps(index, ensure_ascii=False, separators=(",", ":"))
    assert index["evidence"][0]["text"] == outcome
    assert index["evidence"][0]["source"] == "performance"
    assert index["omitted_units"] > 0
    assert count_tokens(rendered) <= 180


def test_question_keeps_its_old_reply_even_without_repeating_the_object_name():
    result = "不行，今天没有同意公开；请继续保密。"
    session = _session(
        _turn(1, "能公开观察记录吗？" + "请听完我的解释。" * 100, result),
        *(_turn(i, performance="桌边的风铃轻轻响了一声。" * 8) for i in range(2, 12)),
    )
    index = current_scene_history_index(session, "观察记录可以公开吗？", max_tokens=160)
    assert index["evidence"][0]["text"] == result
    assert index["omitted_units"] > 0


def test_original_permission_and_later_withdrawal_keep_time_and_speaker():
    session = _session(
        _turn(1, performance="观察记录允许公开展示。"),
        _turn(2, message="我撤回观察记录的公开授权。", performance="收到，继续保密。"),
    )
    index = current_scene_history_index(session, "观察记录还能公开吗？")
    assert [(unit["revision"], unit["source"], unit["text"]) for unit in index["evidence"]] == [
        (1, "performance", "观察记录允许公开展示。"),
        (2, "player_input", "我撤回观察记录的公开授权。"),
        (2, "performance", "收到，继续保密。"),
    ]
    assert index["omitted_units"] == 0


def test_index_respects_final_wire_capacity_and_preserves_later_correction():
    session = _session(
        _turn(1, performance="观察记录可以公开展示。"),
        _turn(2, performance="观察记录授权已撤回。"),
    )
    before = deepcopy(session)
    full = current_scene_history_index(session, "观察记录可以公开吗？")
    expected = {"evidence": [full["evidence"][-1]], "omitted_units": 1,
                "latest_omitted_revision": 1}
    limit = count_tokens(json.dumps(expected, ensure_ascii=False, separators=(",", ":")))
    selected = current_scene_history_index(
        session, "观察记录可以公开吗？",
        within_prompt=lambda value: count_tokens(json.dumps(
            value, ensure_ascii=False, separators=(",", ":"))) <= limit,
    )
    assert selected == expected
    assert session == before


def test_small_budget_prefers_later_change_over_longer_old_query_match():
    session = _session(
        _turn(1, performance="观察记录允许公开展示，观察记录的公开授权已经确认。"),
        _turn(2, performance="观察记录授权已撤回。"),
    )
    full = current_scene_history_index(session, "观察记录的公开授权已经确认吗？")
    last_only = {"evidence": [full["evidence"][-1]], "omitted_units": 1, "latest_omitted_revision": 1}
    budget = count_tokens(json.dumps(last_only, ensure_ascii=False, separators=(",", ":")))
    assert current_scene_history_index(
        session, "观察记录的公开授权已经确认吗？", max_tokens=budget,
    ) == last_only


def test_newer_oversized_original_cannot_be_replaced_by_older_permission():
    correction = "观察记录的授权有变。" + "这一段解释当前条件。" * 100 + "最终没有同意公开。"
    session = _session(
        _turn(1, performance="观察记录允许公开展示。"),
        _turn(2, performance=correction),
    )
    index = current_scene_history_index(session, "观察记录是否能公开？", max_tokens=180)
    assert index["evidence"] == []
    assert index["omitted_units"] == 2
    assert index["latest_omitted_revision"] == 2
    assert correction[:30] not in json.dumps(index, ensure_ascii=False)


def test_current_visit_includes_entry_bridge_and_target_but_not_old_source():
    old = _turn(1, performance="这是上一次访问留下的话。")
    crossing = {
        "revision": 3, "from_node_id": "other", "to_node_id": "current",
        "input_text": "我同意刚才的邀请。",
        "segments": [
            {"phase": "source_response", "performance": "上一幕的收尾。"},
            {"phase": "transition_bridge", "scene_narration": "途中下起小雨。"},
            {"phase": "target_opening", "scene_narration": "这里摆着一张长桌。",
             "performance": "我们到了。"},
        ],
    }
    session = _session(old, crossing, _turn(4, "先坐下。", "好的。"))
    index = current_scene_history_index(session, max_tokens=900)
    assert [unit["text"] for unit in index["evidence"]] == [
        "途中下起小雨。", "这里摆着一张长桌。", "我们到了。", "先坐下。", "好的。",
    ]
    assert all(unit["current_visit"] and unit["revision"] >= 3 for unit in index["evidence"])


def test_legacy_crossing_keeps_original_without_claiming_current_visit_authority():
    session = _session({
        "revision": 1, "from_node_id": "other", "to_node_id": "current",
        "input_text": "同意上一幕的安排。",
        "narration": "两人来到长桌旁，旧话题暂时搁下。",
        "dialogue": [{"speaker_id": "active_catgirl", "text": "这里先坐一会。"}],
    })
    index = current_scene_history_index(session)
    assert [unit["text"] for unit in index["evidence"]] == [
        "两人来到长桌旁，旧话题暂时搁下。", "这里先坐一会。",
    ]
    assert all(not unit["current_visit"] for unit in index["evidence"])


def test_fixed_originals_and_mixed_performance_remain_complete_in_display_order():
    before = "编号：  A-7\n保留   内部空白。"
    after = "  末尾原文。\n"
    performance = "（放下记录板）我会等你的决定。"
    session = _session(_turn(
        1, performance=performance, scene_narration=before,
        fixed_narrations=[
            {"position": "before", "text": before},
            {"position": "after", "text": after},
        ],
        suggested_inputs=["假建议不能进入历史证据。"],
        author_plan="作者期待的结果不能进入历史证据。",
    ))
    index = current_scene_history_index(session)
    assert [(unit["field"], unit["text"]) for unit in index["evidence"]] == [
        ("fixed_narration_before", before),
        ("performance", performance),
        ("fixed_narration_after", after),
    ]
    assert index["omitted_units"] == 0


@pytest.mark.parametrize("budget", [0, 40, 100, 300])
def test_budget_counts_provenance_and_omission_metadata_without_partial_text(budget):
    session = _session(
        _turn(1, "先查看记录。", "（拿起记录板）内容尚未公开，先等对方答复。"),
        _turn(2, "现在有答复了吗？", "还没有，不能推定对方同意。"),
    )
    full = current_scene_history_index(session)
    index = current_scene_history_index(session, "记录公开了吗？", max_tokens=budget)
    if index is None:
        return
    assert count_tokens(json.dumps(index, ensure_ascii=False, separators=(",", ":"))) <= budget
    assert all(unit in full["evidence"] for unit in index["evidence"])
    assert len(index["evidence"]) + index["omitted_units"] == len(full["evidence"])


def test_empty_history_does_not_invent_an_index():
    assert current_scene_history_index(_session()) is None
