"""Retreat optional retrieved evidence without sacrificing fixed Actor context."""

from copy import deepcopy
import json

import pytest

from services.theater import numeric_v2_actor as actor
from tests.unit.test_theater_numeric_v2_target_entry_plan import (
    _case as _entry_case,
    _messages as _entry_messages,
)


SYSTEM = "承接已演出的事实，保留玩家选择与世界边界。"
PREFIX = "实际演绎上下文：\n"
REPLY = "（收回手）我等你的决定。"
HISTORY = [{
    "revision": 8,
    "player_input": "先别开，我只是核对封条。",
    "segments": [{
        "phase": "target_opening",
        "scene_narration": "管事说：“许可仍待确认。”",
        "fixed_narration_before": "封存须知：未经签字不得开匣。",
        "performance": REPLY,
        "fixed_narration_after": "封条编号：A-17。",
    }],
}]
# 这是实际交付顺序的独立期望，不把旧目标输出结构重新送给 Actor。
RENDERED_HISTORY = [{
    "revision": 8,
    "player_input": "先别开，我只是核对封条。",
    "committed_text": (
        "场景／NPC | 管事说：“许可仍待确认。”\n"
        "已展示的作者原文 | 封存须知：未经签字不得开匣。\n"
        f"角色（你） | {REPLY}\n已展示的作者原文 | 封条编号：A-17。"
    ),
}]
LARGE = {"revision": 2, "source": "performance", "current_visit": False,
         "text": "过去橱窗布置的普通说明。" * 180}


def _data(evidence):
    return {
        "story_context": {"background": "封存室内未经签字不得开匣。",
                          "narrative_state": {
                              "current": {"player": {"box": {"value": "密封完好",
                                  "source_event": "r8.player_input.1"}}},
                              "last_known": {}, "recent_events": [],
                              "projection": {"quotes_omitted": 0},
                          }},
        "acting_context": {"persona": "温和谨慎，有自己的主见。",
                           "acting_contract": {"memory_state": "available",
                               "forbidden_behaviors": ["不得代玩家开匣。"]}},
        "recent_context": deepcopy(HISTORY),
        "transition": {"source_scene": {"story_direction": "先核对许可，不替玩家决定。"},
                       "target_scene": {"author_entry_plan": {
                           "catgirl_state": "站在桌旁等待。",
                           "player_state": "具体动作只承接实际输入。",
                           "scene_boundaries": ["未获许可不得打开密封匣。"]}},
                       "contract": {"must_preserve": ["密封匣仍由玩家保管。"]}},
        "history_evidence": deepcopy(evidence),
        "player_input": "我只同意去桌旁，先回答封条编号，仍不要打开匣子。",
    }


def _wire_tokens(data, *, system=SYSTEM, prefix=PREFIX):
    return actor.count_tokens(system) + actor.count_tokens(
        prefix + actor.render_prompt_context(data),
    )


def _expected(data, evidence):
    return {**deepcopy(data), "recent_context": deepcopy(RENDERED_HISTORY),
            "history_evidence": deepcopy(evidence)}


def _fit(data, limit, *, selector=None, diagnostics=None):
    return actor._fit_turn_prompt_data(
        system_prompt=SYSTEM, human_prefix=PREFIX, data=data,
        max_tokens=limit, render_committed_history=True,
        evidence_expander=selector, diagnostics=diagnostics,
    )


def test_capacity_reselection_uses_existing_priority_not_chronological_eviction():
    data = _data([LARGE])
    before = deepcopy(data)
    important = {"revision": 1, "source": "performance", "current_visit": False,
                 "text": "旧公开许可已经撤回，尚未重新批准。"}
    newest = {"revision": 7, "source": "performance", "current_visit": False,
              "text": "窗边的装饰说明。" * 100}
    expected = _expected(data, [important])
    limit = _wire_tokens(expected)
    calls = []

    def select(retained, within_prompt):
        calls.append(deepcopy(list(retained)))
        assert retained == [] or retained == ()
        # 优先级来自检索器，不从回合号、长度或父装箱器的 pop 顺序推断。
        assert within_prompt([important])
        assert not within_prompt([important, newest])
        return [important]

    diagnostics = {}
    fitted = _fit(data, limit, selector=select, diagnostics=diagnostics)

    assert calls == [[]]
    assert fitted == expected
    assert diagnostics["final_tokens"] == limit
    assert diagnostics["history_included_revisions"] == [8]
    assert diagnostics["history_dropped_revisions"] == []
    assert data == before


def test_capacity_can_omit_all_optional_evidence_and_keep_every_fixed_field():
    data = _data([LARGE])
    expected = _expected(data, [])
    before = deepcopy(data)
    calls = []

    def select(retained, within_prompt):
        calls.append(deepcopy(list(retained)))
        assert within_prompt([])
        assert not within_prompt([LARGE])
        return []

    fitted = _fit(data, _wire_tokens(expected), selector=select)

    assert calls == [[]]
    assert fitted == expected
    assert data == before


def test_fixed_context_alone_too_large_rejects_without_calling_selector():
    data = _data([LARGE])
    before = deepcopy(data)

    def unexpected(*_args):
        pytest.fail("固定合同与最新完整回合都放不下时，不应浪费一次重选。")

    with pytest.raises(actor.NumericV2ActorError, match="fixed_context_budget_exceeded"):
        _fit(data, _wire_tokens(_expected(data, [])) - 1, selector=unexpected)

    assert data == before


def test_capacity_without_selector_keeps_explicit_rejection():
    data = _data([LARGE])
    before = deepcopy(data)
    with pytest.raises(actor.NumericV2ActorError, match="fixed_context_budget_exceeded"):
        _fit(data, _wire_tokens(_expected(data, [])))
    assert data == before


def test_fitting_baseline_keeps_seed_and_never_restarts_selection_empty():
    seed = {"revision": 3, "source": "performance", "current_visit": False,
            "text": "编号已公开，封条仍然完整。"}
    data = _data([seed])
    before = deepcopy(data)
    expected = _expected(data, [seed])
    calls = []

    def expand(retained, within_prompt):
        calls.append(deepcopy(list(retained)))
        assert list(retained) == [seed]
        assert within_prompt(retained)
        return retained

    fitted = _fit(data, _wire_tokens(expected), selector=expand)

    assert calls == [[seed]]
    assert fitted == expected
    assert data == before


def test_reselected_evidence_deduplicates_only_same_complete_source_and_revision():
    data = _data([LARGE])
    duplicate = {"revision": 8, "source": "performance", "current_visit": True, "text": REPLY}
    near = {**duplicate, "text": REPLY + "请先等管事的正式答复。"}
    other_source = {**duplicate, "source": "player_input"}
    other_revision = {**duplicate, "revision": 7}
    candidates = [duplicate, near, other_source, other_revision]
    expected = _expected(data, [near, other_source, other_revision])
    before = deepcopy((data, candidates))
    calls = []

    def select(retained, within_prompt):
        calls.append(deepcopy(list(retained)))
        # 试装必须按最终保留的历史去重；异来源和近似文本均保留。
        assert within_prompt(candidates)
        return candidates

    fitted = _fit(data, _wire_tokens(expected), selector=select)

    assert calls == [[]]
    assert fitted == expected
    assert (data, candidates) == before


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["accept", "initiate", "natural"])
@pytest.mark.parametrize("restricted", [False, True], ids=["normal", "limited-target"])
async def test_native_formal_capacity_preserves_entry_permissions_history_and_store(
    tmp_path, monkeypatch, mode, restricted,
):
    runtime, current, outcome, turn, expected_plan = await _entry_case(
        tmp_path, mode=mode, restricted=restricted,
    )
    original = deepcopy((runtime.engine.story, current, outcome))
    path = runtime.store._path(current.session.session_id)
    saved = path.read_bytes()
    native_fit = actor._fit_turn_prompt_data
    observed = []

    def capacity_fit(**kwargs):
        # 从真实构建器读取角色／源目标合同，不用巨型旧剧本夹具或伪造 Runtime 结论。
        raw = deepcopy(kwargs["data"])
        # 压力下原装箱器会先裁旧开场整轮；基线只固定必须保留的最新完整回合。
        # 候选仍收到全部 raw 历史，不能为保留旧开场而改变既有裁剪顺序。
        baseline_args = {**kwargs, "data": {**raw, "recent_context": raw["recent_context"][-1:],
                                          "history_evidence": []},
                         "evidence_expander": None, "diagnostics": None}
        baseline = native_fit(**baseline_args)
        limit = _wire_tokens(baseline, system=kwargs["system_prompt"], prefix=kwargs["human_prefix"])
        calls = []

        def select(retained, within_prompt):
            calls.append(deepcopy(list(retained)))
            assert within_prompt([])
            assert not within_prompt([LARGE])
            return []

        fitted = native_fit(**{**kwargs, "data": {**raw, "history_evidence": [deepcopy(LARGE)]},
                              "max_tokens": limit, "evidence_expander": select})
        assert calls == [[]]
        assert fitted == baseline
        assert fitted["player_input"] == turn.message
        from tests.unit.test_theater_numeric_v2_target_entry_plan import _assert_actor_entry_plan
        _assert_actor_entry_plan(kwargs["system_prompt"], fitted["transition"]["target_scene"]["author_entry_plan"], expected_plan)
        assert _wire_tokens(fitted, system=kwargs["system_prompt"], prefix=kwargs["human_prefix"]) <= limit
        observed.append(fitted)
        return fitted

    monkeypatch.setattr(actor, "_fit_turn_prompt_data", capacity_fit)
    _entry_messages(runtime, current, outcome, turn)

    assert len(observed) == 1
    assert (runtime.engine.story, current, outcome) == original
    assert path.read_bytes() == saved


@pytest.mark.parametrize("ambiguous_field", [False, True])
def test_simple_index_deduplicates_only_unambiguous_complete_current_sources(ambiguous_field):
    original = {"revision": 2, "source": "performance", "current_visit": True,
                "text": "许可已经撤回，当前仍不能打开资料。"}
    alternatives = [
        {**original, "revision": 1}, {**original, "source": "player_input"},
        {**original, "current_visit": False},
        {**original, "text": original["text"] + "尾部条件仍有效。"},
    ]
    index = {"evidence": [{**original, "field": "performance"}],
             "omitted_units": 4, "latest_omitted_revision": 1}
    if ambiguous_field:
        index["evidence"].append({**original, "field": "scene_narration"})
    data = {"role": "记忆受限，只能使用已公开来源。", "current_scene": "不得擅自开封。",
            "pacing": "先回应问题。", "next_scene": "尚未获准进入。", "player_input": "只查看封条。"}
    evidence = [original, *alternatives]
    before = deepcopy((data, evidence, index))
    fitted = actor._fit_simple_turn_prompt_data(
        system_prompt=SYSTEM, human_prefix=PREFIX, data=data,
        history_rows=deepcopy(HISTORY), history_preselected=True,
        evidence_records=evidence, fact_index=lambda: index, max_tokens=10000,
    )
    rendered = fitted["story_so_far"]
    retrieved = json.loads(rendered.split("history_evidence（已提交原文）：", 1)[1].split("\n\n", 1)[0])
    assert retrieved == (evidence if ambiguous_field else alternatives)
    assert json.dumps(index, ensure_ascii=False, separators=(",", ":")) in rendered
    assert all(fitted[key] == value for key, value in data.items())
    assert (data, evidence, index) == before


def test_simple_reselects_optional_evidence_before_final_capacity_rejection():
    data = {"role": "记忆受限，不能知道未公开秘密。", "current_scene": "不得擅自开封。",
            "pacing": "先回应问题。", "next_scene": "尚未获准进入。", "player_input": "只查看封条。"}
    important = {"revision": 1, "source": "performance", "current_visit": False,
                 "text": "许可已撤回，仍不能开封。"}
    common = dict(system_prompt=SYSTEM, human_prefix=PREFIX, data=data,
                  history_rows=deepcopy(HISTORY), max_tokens=10000)
    expected = actor._fit_simple_turn_prompt_data(**common, evidence_records=[important])
    limit = _wire_tokens(expected)
    seen = []

    def reselect(retained, within_prompt):
        seen.append(list(retained))
        assert within_prompt([important])
        assert not within_prompt([LARGE])
        return [important]

    fitted = actor._fit_simple_turn_prompt_data(
        **{**common, "max_tokens": limit}, evidence_records=[LARGE], evidence_expander=reselect,
    )
    assert seen == [[]]
    assert fitted == expected
    assert _wire_tokens(fitted) == limit


def test_simple_reselection_cannot_accept_an_oversized_selector_result():
    data = {"role": "当前角色合同完整保留。", "player_input": "这次仅查看，不同意打开。"}
    common = dict(system_prompt=SYSTEM, human_prefix=PREFIX, data=data,
                  history_rows=deepcopy(HISTORY), max_tokens=10000)
    baseline = actor._fit_simple_turn_prompt_data(**common)
    limit = _wire_tokens(baseline)
    with pytest.raises(actor.NumericV2ActorError, match="fixed_context_budget_exceeded"):
        actor._fit_simple_turn_prompt_data(
            **{**common, "max_tokens": limit}, evidence_records=[LARGE],
            evidence_expander=lambda _retained, _fits: [LARGE],
        )


def _fixed_history(field, text, *, revision=12, phase="target_opening"):
    return [{
        "revision": revision,
        "segments": [{"phase": phase, field: text, "performance": ""}],
    }]


def _fixed_evidence(text, **changes):
    item = {"revision": 12, "source": "performance", "current_visit": True, "text": text}
    item.update(changes)
    return item


def test_fixed_history_evidence_deduplicates_one_complete_piece_from_multi_piece_container():
    first = "封存须知：未经签字不得开匣。"
    second = "四项可读字段：来源为天穹集团；回收目标是原型机；持续追踪仍在运行；远程熔断尚未清除。"
    evidence = _fixed_evidence(second)

    assert actor._remaining_history_evidence(
        _fixed_history("fixed_narration_before", first + "\n\n" + second), [evidence],
    ) == []


def test_fixed_history_evidence_matches_piece_with_internal_blank_line():
    piece = "封存须知：第一段仍有效。\n\n补充：保管人当面提出归还时才允许交还。"
    other = "封条编号：A-17。"
    evidence = _fixed_evidence(piece)

    assert actor._remaining_history_evidence(
        _fixed_history("fixed_narration_before", piece + "\n\n" + other), [evidence],
    ) == []


def test_fixed_history_evidence_matches_multi_line_piece():
    piece = "第一行：未经签字不得开匣。\n第二行：例外仅限保管人当面提出归还。\n第三行：其余封条保持完整。"
    other = "柜台上的旧信仍待核对。"
    evidence = _fixed_evidence(piece)

    assert actor._remaining_history_evidence(
        _fixed_history("fixed_narration_before", piece + "\n\n" + other), [evidence],
    ) == []


def test_fixed_history_evidence_matches_after_position_piece():
    piece = "封条编号：A-17。\n\n保管人：未变。"
    other = "匣子仍在原处。"
    evidence = _fixed_evidence(piece)

    assert actor._remaining_history_evidence(
        _fixed_history("fixed_narration_after", piece + "\n\n" + other), [evidence],
    ) == []


def test_fixed_history_evidence_keeps_near_substring_foreign_source_revision_old_visit_and_previous_tail():
    piece = "封存须知：未经签字不得开匣。"
    other = "封条编号：A-17。"
    history = _fixed_history("fixed_narration_before", piece + "\n\n" + other)
    candidates = [
        _fixed_evidence(piece + "尾部条件仍有效。"),  # 近似，不是完整片段
        _fixed_evidence("未经签字"),  # 任意子串
        _fixed_evidence(piece, source="player_input"),
        _fixed_evidence(piece, revision=11),
        _fixed_evidence(piece, current_visit=False),
    ]
    previous_tail = _fixed_history("fixed_narration_before", piece, phase="previous_scene_tail")
    untouched = [*candidates, _fixed_evidence("旧的幕后原文。")]
    assert actor._remaining_history_evidence(history + previous_tail, untouched) == untouched

    assert actor._remaining_history_evidence(
        previous_tail, [_fixed_evidence(piece)],
    ) == [_fixed_evidence(piece)]


def test_fixed_history_evidence_restores_when_recent_history_is_trimmed():
    evidence = [_fixed_evidence("封存须知：未经签字不得开匣。")]
    before = deepcopy(evidence)

    assert actor._remaining_history_evidence([], evidence) == evidence
    assert actor._remaining_history_evidence(
        _fixed_history("fixed_narration_before", "另一条原文。", revision=11), evidence,
    ) == evidence
    assert evidence == before


def test_fixed_history_evidence_does_not_mutate_inputs():
    piece = "封存须知：第一段仍有效。\n\n补充：保管人当面提出归还时才允许交还。"
    other = "封条编号：A-17。"
    history = _fixed_history("fixed_narration_before", piece + "\n\n" + other)
    evidence = [_fixed_evidence(piece), _fixed_evidence(other + "尾部")]
    before = deepcopy((history, evidence))

    actor._remaining_history_evidence(history, evidence)

    assert (history, evidence) == before


def test_fixed_history_evidence_does_not_normalize_whitespace_to_find_a_match():
    piece = "封存须知：未经签字不得开匣。"
    candidates = [_fixed_evidence(' ' + piece), _fixed_evidence(piece + ' ')]
    history = _fixed_history('fixed_narration_before', '另一份原文。\n\n' + piece)
    assert actor._remaining_history_evidence(history, candidates) == candidates


def test_fixed_history_evidence_keeps_the_complete_piece_indentation():
    piece = "  原始说明。\n  条件与缩进保持原样。  "
    history = _fixed_history('fixed_narration_before', '上一份原文。\n\n' + piece + '\n\n下一份原文。')
    assert actor._remaining_history_evidence(history, [_fixed_evidence(piece)]) == []
