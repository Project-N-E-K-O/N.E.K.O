"""Keep model duties separate from the story's character and player identities."""

from copy import deepcopy
from dataclasses import replace
import json

import pytest

from config.prompts import prompts_theater_pipeline as pipeline
from services.theater import numeric_v2_actor as actor
from services.theater import numeric_v2_evaluator as evaluator
from services.theater import numeric_v2_narrative_proposer as proposer
from services.theater.numeric_v2_runtime import TurnRequestV2
from services.theater_workshop.sdk.generation import numeric_v2 as generation
from services.theater_workshop.sdk.generation import staged
from services.theater_workshop.sdk.generation import quality
from services.theater_workshop.sdk.generation.facts import FACT_REVIEW_PROMPT
from services.theater_workshop.sdk.generation.evidence import EVIDENCE_REVIEW_PROMPT
from services.theater_workshop.sdk.generation.plan_review import PLAN_REVIEW_PROMPT
from services.theater_workshop.sdk.generation.repair import REPAIR_PLAN_RULE
from services.theater_workshop.sdk.generation.runtime_rules import SCORING_RUNTIME_RULES
from tests.unit.test_theater_numeric_v2_author_entry_plan import _case
from tests.utils.theater_prompt import prompt_payload


AMBIGUOUS_SUBJECTS = ("当前你", "你名", "你状态", "你全名", "自己的你", "你的完整姓名")


@pytest.mark.parametrize("prompt", [
    pipeline._FRAME_PROMPT, pipeline._STATE_PROMPT, staged._SCENE_PROMPT,
    staged._ENDING_PROMPT, generation._MAINLINE_PROMPT,
    generation._MAINLINE_CONTINUATION_PROMPT, generation._NODE_ENHANCEMENT_PROMPT,
    generation._BRANCH_ENDING_PROMPT, generation._BRANCH_PATH_PROMPT,
    SCORING_RUNTIME_RULES, proposer._SOURCE_SYSTEM, proposer._REVIEW_SYSTEM,
    quality._ASSESSMENT_PROMPT, quality._NODE_OPTIMIZATION_PROMPT,
    FACT_REVIEW_PROMPT, EVIDENCE_REVIEW_PROMPT, PLAN_REVIEW_PROMPT, REPAIR_PLAN_RULE,
], ids=["frame", "state", "scene", "ending", "mainline", "continuation",
        "enhancement", "branch_ending", "branch_path", "scoring", "ledger", "ledger_review",
        "literature", "node_repair", "facts", "evidence", "plan_review", "repair_plan"])
def test_non_actor_prompts_never_assign_the_character_identity_to_the_reader(prompt):
    for ambiguous in AMBIGUOUS_SUBJECTS:
        assert ambiguous not in prompt


def test_evaluator_and_review_message_builders_do_not_claim_the_character_identity():
    engine, session = _case()
    player = "好，我们进去。"
    outcome = engine.resolve_turn(replace(session, transition_offered=True),
                                  TurnRequestV2("roles", 0, player), (), transition_intent="accept")
    candidate = {"performance": "（点头）走吧。", "suggested_inputs": []}
    groups = [evaluator._build_messages(engine, session, player)]
    for transition in (None, outcome):
        groups.append(evaluator._build_transition_judge_messages(
            engine, session, actor_performance=candidate, player_input=player,
            transition_outcome=transition, route_changed=transition is not None,
        )[0])
    for terminal in (False, True):
        groups.append(evaluator._build_contract_check_messages(
            required=["不得替玩家决定。"], candidate_text=candidate["performance"],
            player_input=player, terminal_input_closed=terminal,
        ))
    for messages in groups:
        for ambiguous in AMBIGUOUS_SUBJECTS:
            assert ambiguous not in messages[0].content


def test_player_action_review_binds_pronouns_by_speaker_without_rewriting_evidence():
    engine, session = _case()
    session = replace(session, player_address_known=True, catgirl_binding={
        **session.catgirl_binding, "catgirl_name": "小雨", "player_address": "林舟",
    })
    player = "你拿着吧，我先不动。"
    performance = {"performance": "（接住）那我拿着，你不用动。", "scene_narration": "店主说：我去找盒子。"}
    before = deepcopy((session.to_dict(), performance))
    messages = evaluator._build_player_action_review_messages(
        engine, session, player_input=player, actor_performance=performance,
        transition_outcome=None, public_destination_quote="", confirmed_acceptance=False,
        confirmed_silent_acceptance="",
    )
    system, data = messages[0].content, json.loads(messages[1].content)
    for ambiguous in AMBIGUOUS_SUBJECTS:
        assert ambiguous not in system
    assert "你是审核者，不扮演任何人物" in system
    assert "performance中的第一人称‘我/人家’指当前角色" in system
    assert "player_input中的第一人称指玩家" in system
    assert "引述或NPC对白按其明确发言者与受话人理解" in system
    assert "不能仅凭‘你’或‘她’判断主体" in system
    assert "显式出现其他主体时按原文判断，不因字段归属忽略代做" in system
    assert data["actors"] == {"player": "林舟", "catgirl": "小雨"}
    assert data["player_input"] == player
    assert data["candidate_segments"][0]["performance"] == performance["performance"]
    assert (session.to_dict(), performance) == before


@pytest.mark.parametrize("offered", [False, True])
def test_recommender_has_its_own_duty_and_preserves_visible_utterances(offered):
    player = "你先拿着。"
    performance = {"performance": "我收好了，你想坐哪边？"}
    messages = actor._suggestion_fill_messages(
        catgirl_name="小雨", performance=performance, player_input=player,
        max_tokens=4800, transition_offered=offered,
    )
    system, data = messages[0].content, json.loads(messages[1].content)
    for ambiguous in AMBIGUOUS_SUBJECTS:
        assert ambiguous not in system
    assert "玩家输入推荐补全器" in system
    assert "当前演出角色的姓名是“小雨”" in system
    assert "你只拟写玩家候选输入，不能替该角色说话或行动" in system
    assert "当前你" not in system
    assert "不能明写你" not in system
    assert data["player_input"] == player
    assert performance["performance"] in data["visible_performance"]


@pytest.mark.parametrize("source_refs", [False, True])
def test_ledger_character_role_is_story_data_not_the_extractors_identity(source_refs):
    _, session = _case()
    messages = proposer._messages({"performance": "（收好盒子）放好了。"}, "请你保管。",
                                 session, False, source_refs=source_refs)
    data = json.loads(messages[1].content)
    assert data["protagonists"]["character"]["role"] == "当前演出角色"
    if source_refs:
        assert "player是玩家，character是当前演出角色" in messages[0].content
    else:
        assert "你独立复核已交付正文的叙事账本候选" in messages[0].content


def test_workshop_state_extraction_names_both_owners_explicitly():
    assert "catgirl_state以cast_names.catgirl_name的完整姓名开头" in pipeline._STATE_PROMPT
    assert "player_state以cast_names.player_name的完整姓名开头" in pipeline._STATE_PROMPT
    assert "你负责摘录，不是剧中人物" in pipeline._STATE_PROMPT
    assert "catgirl_name 是女主姓名" in pipeline._FRAME_PROMPT
    assert "player_name 是玩家姓名" in pipeline._FRAME_PROMPT


def test_workshop_bridge_preserves_authorized_movement_and_ending_has_no_new_bridge():
    assert "不新增未经授权的玩家身体行动" in generation._BRANCH_PATH_PROMPT
    assert "接受或主动发起已公开出口所授权的普通移动" in generation._BRANCH_PATH_PROMPT
    assert "entry_bridge保持空字符串" in staged._ENDING_PROMPT


@pytest.mark.parametrize("phase", ["opening", "turn", "transition_compact", "reinvitation"])
def test_actor_action_subject_and_single_ending_schema_are_consistent(phase):
    system = actor._system_prompt(catgirl_name="小雨", phase=phase, single_ending_response=True)
    assert "微动作主语只用她或该角色名" not in system
    assert "两个 performance" not in system
    assert "你" in system


@pytest.mark.parametrize('phase', ['opening', 'turn'])
def test_unknown_player_uses_explicit_subject_in_author_data_without_rewriting_speech(phase):
    engine, session = _case()
    engine.story['intro'].update(player_name='林舟', player_identity='林舟，押镖人。',
                                  catgirl_name='小雨', catgirl_identity='小雨，同行向导。')
    engine.nodes['start']['story_beat']['opening_scene'] = '林舟把剑匣递向旧友，小雨站在旁边。'
    player = '请您收下。'
    if phase == 'opening':
        messages = actor._opening_messages(engine, '谨慎。', '小雨', '你', False, max_tokens=10000)
    else:
        turn = TurnRequestV2('handoff', 0, player)
        outcome = engine.resolve_turn(session, turn, ())
        messages = actor._turn_messages(engine, session, outcome, player, '谨慎。', '小雨',
                                       '你', False)
    data = prompt_payload(messages[1].content)
    author = data['current_scene'] if phase == 'turn' else data['current_story_beat']['opening_scene']
    assert '玩家把剑匣递向旧友，小雨站在旁边。' in author
    if phase == 'turn':
        assert '玩家身份：玩家，押镖人。' in data['role']
        assert data['player_input'] == player
    else:
        assert data['story_context']['player_identity'] == '玩家，押镖人。'
    assert 'performance 的说话人仍是角色设定中的你' in actor._system_prompt(catgirl_name='小雨')
