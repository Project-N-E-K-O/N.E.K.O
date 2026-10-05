"""Regress the four authoring prompt contracts with injected model replies only."""
from __future__ import annotations

from copy import deepcopy
import json

import pytest
import tiktoken

from theater_workshop.sdk.generation.numeric_v2 import (
    NumericV2GenerationError,
    NumericV2Generator,
    _BRANCH_PATH_PROMPT,
    _MAINLINE_PROMPT,
)
from theater_workshop.sdk.model import LLMCallFailure
from theater_workshop.sdk.numeric_v2_branch import NumericV2BranchService

from .test_numeric_v2_branch import _character_state, _ending_goal
from .test_numeric_v2_generation import _generation_setup, _idea_outline


def test_branch_bridge_prompt_keeps_facts_without_promising_verbatim_delivery():
    assert "Runtime 原文展示的确定性换场旁白" not in _BRANCH_PATH_PROMPT
    assert "进入下一节点前原文展示的唯一换场旁白" not in _BRANCH_PATH_PROMPT
    assert "运行时会依据实际游玩历史改写桥段与开场措辞" in _BRANCH_PATH_PROMPT
    assert "fixed_narrations" in _BRANCH_PATH_PROMPT


@pytest.mark.parametrize("path,template_path", [
    ("world", ["world"]),
    ("story_protagonist", ["story_protagonist"]),
    ("player_role", ["player_role"]),
    ("relationship_arc", ["relationship_arc"]),
    ("relationship_arc.stages[3]", ["relationship_arc", "stages", 0]),
    ("character_state_arc", ["character_state_arc"]),
    ("character_state_arc.stages[3].acting_contract",
     ["character_state_arc", "stages", 0, "acting_contract"]),
    ("character_state_arc.ending_stage", ["character_state_arc", "ending_stage"]),
    ("key_props[2].states", ["key_props", 0, "states"]),
    ("mainline_chapters", ["mainline_chapters"]),
    ("mainline_chapters[3]", ["mainline_chapters", 0]),
    ("mainline_chapters[3].ordered_goals[2].owner",
     ["mainline_chapters", 0, "ordered_goals", 0, "owner"]),
    ("mainline_chapters[3].exit_plan", ["mainline_chapters", 0, "exit_plan"]),
    ("ending", ["ending"]),
])
def test_continuation_supplies_requested_contract_from_actual_initial_prompt(path, template_path):
    # Read the contract that the first model actually receives, independently of helper internals.
    expected, _ = json.JSONDecoder().raw_decode(_MAINLINE_PROMPT.split("结构必须为：\n", 1)[1])
    for part in template_path:
        expected = expected[part]
    calls = []
    generator = NumericV2Generator()
    generator.call_llm = lambda messages, **options: calls.append(messages) or "{}"

    generator._call_outline_continuation(
        normalized_idea=_generation_setup()["brief"], length_preset="short",
        minimum=3, maximum=6, candidate={}, issues=[{"path": path, "code": "expected_object"}],
        requested_paths=[path],
    )

    request = json.loads(calls[0][1]["content"])
    assert request["requested_paths"] == [path]
    assert request["requested_replacements"][0]["output_contract"] == expected
    assert "output_contract" in calls[0][0]["content"]
    assert "原样路径" in calls[0][0]["content"]
    assert len(calls[0]) == 2


def test_missing_state_arc_continuation_has_all_enums_and_preserves_other_story_fields():
    candidate = _idea_outline()
    expected_arc = candidate.pop("character_state_arc")
    original_candidate = deepcopy(candidate)
    calls = []
    generator = NumericV2Generator()

    def reply(messages, **options):
        calls.append(options)
        request = json.loads(messages[1]["content"])
        assert request["requested_paths"] == ["character_state_arc"]
        contract = request["requested_replacements"][0]["output_contract"]
        acting = contract["stages"][0]["acting_contract"]
        assert acting["cognition_state"] == "fresh_boot | limited | normal"
        assert acting["memory_state"] == "empty | partial | available"
        assert acting["self_reference_mode"] == "system_neutral | persona_allowed"
        assert acting["persona_scope"] == "style_only | full"
        assert "assertable_self_facts" in acting
        return json.dumps({"replacements": {"character_state_arc": expected_arc}}, ensure_ascii=False)

    generator.call_llm = reply
    generated = generator.generate(
        title="缺失状态线恢复", setup=_generation_setup(), checkpoint={"candidate": candidate},
    )

    assert len(calls) == 1
    assert calls[0]["operation"] == "numeric_v2_mainline_continuation"
    assert candidate == original_candidate
    assert generated["story"]["nodes"][2]["story_beat"]["summary"] == candidate["mainline_chapters"][2]["narrative"]


@pytest.mark.parametrize("failed_reply", ["technical", "invalid_json", "non_object"])
@pytest.mark.parametrize("third_valid", [False, True])
def test_initial_third_call_respects_total_cap_and_retains_explicit_recovery(failed_reply, third_valid):
    candidate = _idea_outline()
    if not third_valid:
        candidate["world"]["background"] = ""
    calls = []
    generator = NumericV2Generator()

    def reply(messages, **options):
        calls.append(options["operation"])
        if len(calls) <= 2:
            if failed_reply == "technical":
                return LLMCallFailure("simulated timeout", error_code="model_timeout", exception_type="TimeoutError")
            return "not-json" if failed_reply == "invalid_json" else "[]"
        if len(calls) == 3:
            return json.dumps(candidate, ensure_ascii=False)
        return json.dumps({"replacements": {"world.background": "雨季小镇。"}}, ensure_ascii=False)

    generator.call_llm = reply
    if third_valid:
        assert generator.generate(title="三次上限", setup=_generation_setup())["story"]
    else:
        with pytest.raises(NumericV2GenerationError) as caught:
            generator.generate(title="三次上限", setup=_generation_setup())
        error = caught.value
        assert error.attempts == 3
        assert error.code == "invalid_mainline_generation"
        assert error.checkpoint["candidate"] == candidate
        assert [issue["path"] for issue in error.issues] == ["world.background"]
        assert error.checkpoint["issues"] == list(error.issues)
        assert len(calls) == 3
        # Only an explicit later call may spend a fresh attempt to resume the saved candidate.
        resumed = generator.generate(title="三次上限", setup=_generation_setup(), checkpoint=error.checkpoint)
        assert resumed["story"]["intro"]["background"] == "雨季小镇。"
        assert calls[-1] == "numeric_v2_mainline_continuation"
    assert calls[:3] == ["numeric_v2_mainline_generation"] * 3
    assert len(calls) == (3 if third_valid else 4)


@pytest.mark.parametrize("text", [
    " ".join(["A"] * 2000),
    "A" + "\u0001" * 1990 + "B",
    "A" + "\t" * 3990 + "B",
], ids=["full-2000-token-text", "json-control-escaping", "json-tab-escaping"])
def test_branch_ending_reserves_structure_and_serialized_fixed_text_output(text):
    encoder = tiktoken.get_encoding("o200k_base")
    assert len(encoder.encode(text)) <= 2000
    candidate = {
        "title": "归档", "summary": "调查记录已经归档。",
        "opening_scene": "归档后的调查记录安放在花店的桌上。",
        "ordered_goals": [_ending_goal("归档后的调查记录安放在花店的桌上。")],
        "irreversible_facts": ["记录已归档。"], "character_state": _character_state(),
        "catgirl_situation": "女主记得已经成立的调查事实。", "tone": "克制",
        "fixed_narrations": [{"id": "archive", "text": text, "trigger": {"type": "entry"},
                              "after": [], "required_before_exit": False}],
    }
    candidate = NumericV2BranchService()._validate_ending(candidate)
    payload = json.dumps(candidate, ensure_ascii=False, separators=(",", ":"))
    calls = []
    generator = NumericV2Generator()

    def reply(messages, **options):
        calls.append(options)
        assert options["max_tokens"] >= len(encoder.encode(payload))
        return payload

    generator.call_llm = reply
    result = generator.generate_branch_ending(context={
        "author_intent": {"direction": "在结局原样展示作者已有日志"},
        "source": {"story_beat": {"fixed_narrations": candidate["fixed_narrations"]}},
    })

    assert result["fixed_narrations"][0]["text"] == text
    assert len(calls) == 1
    assert calls[0]["max_retries"] == 1
    assert calls[0]["operation"] == "numeric_v2_branch_ending"

def test_continuation_repairs_forbidden_field_absent_from_initial_output_contract():
    candidate = _idea_outline()
    candidate["character_state_arc"]["ending_stage"]["chapter_index"] = 1
    calls = []
    generator = NumericV2Generator()

    def reply(messages, **options):
        calls.append(options)
        request = json.loads(messages[1]["content"])
        replacement = request["requested_replacements"][0]
        assert replacement["path"] == "character_state_arc.ending_stage.chapter_index"
        assert replacement["output_contract"] is None
        assert replacement["issue"]["code"] == "character_state_ending_index_forbidden"
        return json.dumps({"replacements": {replacement["path"]: None}})

    generator.call_llm = reply
    generated = generator.generate(
        title="移除结局章节序号", setup=_generation_setup(), checkpoint={"candidate": candidate},
    )

    assert generated["story"]["nodes"][-1]["type"] == "ending"
    assert len(calls) == 1
    assert calls[0]["operation"] == "numeric_v2_mainline_continuation"
    assert candidate["character_state_arc"]["ending_stage"]["chapter_index"] == 1



def test_branch_ending_resolves_author_text_without_model_copying_or_input_mutation():
    literal = '记录“原文”\\路径\n' + '铭牌' * 1000
    direction = '请逐字展示以下原文：\n' + literal + '\n原文结束；保持克制。'
    context = {'author_intent': {'direction': direction}}
    before = deepcopy(context)
    wire = {'title': '记录', 'fixed_narrations': [{
        'id': 'archive', 'text_source': {'start_after': '请逐字展示以下原文：\n',
                                        'end_before': '\n原文结束；保持克制。'},
        'trigger': {'type': 'entry'}, 'after': [], 'required_before_exit': False,
    }]}
    generator = NumericV2Generator()
    calls = []

    def reply(messages, **options):
        calls.append(options)
        assert json.loads(messages[1]['content']) == context
        return json.dumps(wire, ensure_ascii=False)

    generator.call_llm = reply
    result = generator.generate_branch_ending(context=context)
    piece = result['fixed_narrations'][0]
    assert piece['text'] == literal
    assert set(piece) == {'id', 'text', 'trigger', 'after', 'required_before_exit'}
    assert context == before
    assert len(calls) == 1
    assert calls[0]['max_retries'] == 1


@pytest.mark.parametrize('source,reference,text', [
    ('原文：日志', {'start_after': '原文：', 'end_before': ''}, None),
    ('日志。结束', {'start_after': '', 'end_before': '。结束'}, None),
    ('日志', {'start_after': '', 'end_before': ''}, None),
    ('原文：日志', {'start_after': '缺失', 'end_before': ''}, None),
    ('原文：甲；原文：乙', {'start_after': '原文：', 'end_before': ''}, None),
    ('结尾；原文：日志', {'start_after': '原文：', 'end_before': '结尾；'}, None),
    ('原文：日志', {'start_after': '原文：', 'end_before': '缺失'}, None),
    ('原文：', {'start_after': '原文：', 'end_before': ''}, None),
    (None, {'start_after': '', 'end_before': ''}, None),
    ('日志', {'start_after': 0, 'end_before': ''}, None),
    ('日志', {'start_after': '', 'end_before': '', 'path': '/other'}, None),
    ('日志', {'start_after': '', 'end_before': ''}, '模型重写'),
])
def test_branch_ending_text_source_requires_unambiguous_exact_boundaries(source, reference, text):
    piece = {'id': 'archive', 'text_source': reference}
    if text is not None:
        piece['text'] = text
    generator = NumericV2Generator()
    generator.call_llm = lambda *args, **kwargs: json.dumps({'fixed_narrations': [piece]})
    context = {'author_intent': {'direction': source}}
    if source in ('原文：日志', '日志。结束', '日志') and text is None and reference in (
        {'start_after': '原文：', 'end_before': ''},
        {'start_after': '', 'end_before': '。结束'},
        {'start_after': '', 'end_before': ''},
    ):
        assert generator.generate_branch_ending(context=context)['fixed_narrations'][0]['text'] == '日志'
    else:
        with pytest.raises(NumericV2GenerationError, match='invalid_model_json'):
            generator.generate_branch_ending(context=context)
