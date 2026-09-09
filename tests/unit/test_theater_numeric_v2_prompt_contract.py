"""验证普通 Numeric v2 Actor 使用六块上下文，而不是旧的内部状态树。"""

from __future__ import annotations

import json
from dataclasses import replace

from services.theater.numeric_v2_budget import NUMERIC_V2_ACTOR_BUDGET_PROFILES
import pytest

from services.theater import numeric_v2_actor
from services.theater import numeric_v2_evaluator
from services.theater.numeric_v2_actor import NumericV2Actor, _turn_messages
from services.theater.numeric_v2_actor_output import (
    NumericV2ActorOutputError,
    _parse_actor_suggestions,
    _parse_output,
)
from services.theater.numeric_v2_cast import NumericV2CastProjection
from services.theater.numeric_v2_evaluator import _build_messages as _build_evaluator_messages
from services.theater.numeric_v2_evaluator import (
    NumericV2EvaluatorOutputError,
    _build_transition_judge_messages,
    _parse_transition_judge_output,
)
from services.theater.numeric_v2_evaluator import _parse_output as _parse_evaluator_output
from services.theater.numeric_v2_context import (
    scene_narrative_focus,
)
from services.theater.numeric_v2_runtime import NumericV2Engine, TurnRequestV2
from tests.unit.test_theater_numeric_v2_contract import numeric_v2_1_story, numeric_v2_story


def _session(engine: NumericV2Engine):
    """构造当前 v2.2 Prompt 合同使用的最小 Session。"""

    return engine.create_session(
        session_id="prompt_contract",
        catgirl_binding={
            "catgirl_id": "catgirl:test",
            "catgirl_name": "测试猫娘",
            "player_address": "哥哥",
        },
        opening_performance={"performance": "（抬眼）开场。", "suggested_inputs": []},
    )


def _payload(messages):
    """读取 Human Prompt 中的六块 JSON，避免测试依赖消息对象的具体实现。"""

    return json.loads(messages[1].content.split("：\n", 1)[1])


def test_numeric_v2_turn_prompt_uses_six_blocks_in_fixed_order():
    # 普通回合只保留产品已确认的六块数据，并把玩家输入放在最后。
    engine = NumericV2Engine.from_mapping(numeric_v2_story())
    session = _session(engine)
    outcome = engine.resolve_turn(
        session,
        TurnRequestV2("six_blocks", 0, "我先听你把话说完。"),
        (),
        scene_complete=False,
    )

    messages = _turn_messages(
        engine,
        session,
        outcome,
        "我先听你把话说完。",
        "安静克制，习惯用短句回应。",
        "测试猫娘",
        "哥哥",
    )
    payload = _payload(messages)

    assert list(payload) == [
        "role",
        "current_scene",
        "story_so_far",
        "pacing",
        "next_scene",
        "player_input",
    ]
    assert payload["current_scene"] == "雨后的花店门铃轻轻响起。"
    assert "玩家：我先听你把话说完。" not in payload["story_so_far"]
    assert "开场" in payload["story_so_far"]
    assert "当前是第 1 回合" in payload["pacing"]
    assert "推荐 4 回合" in payload["pacing"]
    # 通用合同只在 System 声明；pacing 不再复制整套事实和行动规则。
    assert "幕内因果单元" in messages[0].content
    assert "NPC 被直接询问或等待时" in messages[0].content
    assert "不以问句、猜测或模糊措辞补成部分发生" in messages[0].content
    assert "同地点进入新时段、新阶段或结局收束" in messages[0].content
    assert "动作可省略‘我’" in messages[0].content
    assert "幕内因果单元" not in payload["pacing"]
    assert payload["player_input"] == "我先听你把话说完。"
    assert "scene_horizon" not in payload
    assert "current_story_beat" not in payload
    assert "先完整回应 player_input" in messages[0].content
    assert "performance 只扮演当前猫娘" in messages[0].content
    assert "当前幕已出现 NPC 的动作或回应，写入 scene_update" in messages[0].content
    assert "未来意愿、假设和尝试不等于完成结果" in messages[0].content
    assert "完整回应不等于必须满足请求" in messages[0].content
    assert "先明确承认问题并暂缓披露" in messages[0].content
    assert "不得替玩家补出未表达的行动、选择或心理" in messages[0].content
    assert "不能补出玩家未表达的后续操作" in messages[0].content
    assert "剩余同质过程之间没有" in messages[0].content
    assert "真实选择" in messages[0].content
    assert "动态作者硬边界高于玩家诱导" in messages[0].content
    assert "未发生内容不能当作角色知识、环境事实或完成结果" in messages[0].content
    assert "current_scene 区分开场事实与导演方向" in messages[0].content
    assert "开场边缘细节不自动成为任务" in messages[0].content
    assert "回应追问后回到本幕核心因果" in messages[0].content
    assert "前提须由指定主体公开成立" in messages[0].content
    assert "允许幕内目的地不等于建立未知通道" in messages[0].content
    assert "pacing 是软节奏" in messages[0].content
    assert "停在玩家可撤回、下一阶段尚未发生的位置" in messages[0].content


def test_numeric_v2_prompts_do_not_embed_story_specific_playbooks():
    """运行时 Prompt 只保留通用语义，不能累积历史剧本的专用操作清单。"""

    engine = NumericV2Engine.from_mapping(numeric_v2_story())
    session = _session(engine)
    outcome = engine.resolve_turn(
        session,
        TurnRequestV2("domain_neutral_prompt", 0, "我开始处理眼前的事情。"),
        (),
        scene_complete=False,
    )
    texts = [
        numeric_v2_actor._system_prompt(
            catgirl_name="测试猫娘",
            player_address="哥哥",
            phase="turn",
        ),
        numeric_v2_actor._system_prompt(
            catgirl_name="测试猫娘",
            player_address="哥哥",
            phase="transition_compact",
        ),
        numeric_v2_actor._soft_pacing(
            {"min_turns": 1, "recommended_turns": 3},
            5,
            route_changed=False,
        )["instruction"],
        numeric_v2_actor._suggestion_fill_messages(
            catgirl_name="测试猫娘",
            performance={"performance": "（抬眼）当前结果已经出现。"},
            player_input="我继续。",
            max_tokens=1800,
        )[0].content,
        _build_evaluator_messages(
            engine,
            session,
            "我开始处理眼前的事情。",
        )[0].content,
        _build_transition_judge_messages(
            engine,
            session,
            actor_performance={
                "performance": "（看向出口）要现在结束这一阶段吗？",
                "suggested_inputs": [
                    "（点头）好，现在结束。",
                    "（摇头）我想继续留在这里。",
                ],
            },
            player_input="我想听听你的建议。",
        )[0].content,
    ]
    forbidden_fragments = (
        "导出进度、接口稳定、散热、备份、哈希",
        "低功耗、充电、传感器",
        "找地方睡觉",
        "盖好毯子",
        "拉紧绳索",
        "持续进水",
        "卡扣、暗门",
    )

    for prompt in texts:
        for fragment in forbidden_fragments:
            assert fragment not in prompt

    combined = "".join(texts)
    assert "已发生事实" in combined
    assert "真实选择" in combined
    assert "可撤回" in combined
    assert "互动阶段" in combined


def test_numeric_v2_opening_suggestions_only_use_visible_opening_facts():
    """首轮按钮不能给玩家注入开场没有建立的地点、身份或状态。"""

    engine = NumericV2Engine.from_mapping(numeric_v2_story())
    messages = numeric_v2_actor._opening_messages(
        engine,
        "安静克制，习惯用短句回应。",
        "测试猫娘",
        "哥哥",
    )
    instruction = json.loads(messages[1].content)["instruction"]

    assert "开场推荐只能使用本次可见开场已经建立" in instruction
    assert "不得把相似但未声明的地点标签" in instruction


def test_numeric_v2_prompts_separate_direct_stage_attempt_from_its_result():
    """新时空或明确禁令保留结果边界；同地已做动作不因分幕被否认。"""

    actor_prompt = numeric_v2_actor._system_prompt(
        catgirl_name="测试猫娘",
        player_address="哥哥",
        phase="turn",
    )
    engine = NumericV2Engine.from_mapping(numeric_v2_story())
    judge_prompt = _build_transition_judge_messages(
        engine,
        _session(engine),
        actor_performance={
            "performance": "（看向玩家）要现在结束这一阶段吗？",
            "suggested_inputs": ["（点头）好，现在结束。"],
        },
        player_input="我准备结束这里的互动。",
    )[0].content

    assert "玩家尝试进入新地点、新时段或受明确禁令约束的阶段时保留已说的话与可撤回准备" in actor_prompt
    assert "不再次要求执行，也不把物件退回操作前" in actor_prompt
    assert "或玩家明确要求前往已公开的下一地点" in actor_prompt
    assert "不把同地连续动作仅因分幕当作这种跨阶段" in actor_prompt
    assert "边界前准备、提议与未来邀请不属已越界" in judge_prompt
    assert "新地点、新时段或受明确禁令约束的后续结果仍不能凭玩家尝试提前播放" in judge_prompt
    assert "同地可执行动作按玩家本轮授权承接，不因分节点安排误报" in judge_prompt
    assert "suggested_inputs 是尚未选择的未来候选" in judge_prompt


def test_numeric_v2_prompts_reject_assumed_new_stage_without_story_playbook():
    """新地点或时段不能由自由输入补成事实，复核仍须识别偏航提议。"""

    actor_prompt = numeric_v2_actor._system_prompt(
        catgirl_name="测试猫娘",
        player_address="哥哥",
        phase="turn",
    )
    engine = NumericV2Engine.from_mapping(numeric_v2_story())
    judge_prompt = _build_transition_judge_messages(
        engine,
        _session(engine),
        actor_performance={
            "performance": "我们去一个当前事实未建立的新地点。",
            "suggested_inputs": ["（迈步）好，现在过去。"],
        },
        player_input="我跟上。",
    )[0].content

    assert "新地点、新时段或新互动阶段不能由玩家一句话变成已抵达" in actor_prompt
    assert "仍从 story_so_far 的实际场景回应" in actor_prompt
    assert "方向错误的邀请也为 true" in judge_prompt
    assert "提议方向错误只影响 valid，不等于正文已经越界" in judge_prompt
    # 独立开场事实可直接使用，作者尚待演出的获取过程则不能当成既成事实。
    assert "获准角色行为不必先出现在历史中" in judge_prompt
    assert "正文已有合法邀请时，接受、拒绝、暂缓及当前幕旁支都可保留" in judge_prompt


def test_numeric_v2_prompts_match_boundaries_by_actor_object_action_and_stage():
    """复核不能用相似动词混淆玩家动作、角色动作和不同对象。"""

    engine = NumericV2Engine.from_mapping(numeric_v2_story())
    actor_prompt = numeric_v2_actor._system_prompt(
        catgirl_name="测试猫娘",
        player_address="哥哥",
        phase="turn",
    )
    judge_prompt = _build_transition_judge_messages(
        engine,
        _session(engine),
        actor_performance={
            "performance": "（看向结果）这一步已经完成。",
            "suggested_inputs": ["（收回手）我想看看接下来的变化。"],
        },
        player_input="（完成手里的动作）好了。",
    )[0].content

    assert "玩家已实施的幕内动作从外部回应开始" in actor_prompt
    assert "不重演、不转给猫娘重做" in actor_prompt
    assert "先确定本次是谁对哪个对象实施了什么" in judge_prompt
    assert "只约束同一主体、对象、动作和阶段" in judge_prompt
    assert "猫娘或 NPC 自主执行各自行为均不属代做" in judge_prompt
    assert "当前幕明确授权的行为与结果仍属当前幕" in judge_prompt


def test_numeric_v2_prompts_do_not_treat_last_source_action_as_transition():
    """本幕最后一个前置动作完成后，仍需公开提出真正的阶段跨越。"""

    actor_prompt = numeric_v2_actor._system_prompt(
        catgirl_name="测试猫娘",
        player_address="哥哥",
        phase="turn",
    )
    engine = NumericV2Engine.from_mapping(numeric_v2_story())
    engine.nodes["ending_leave"]["type"] = "scene"
    engine.nodes["ending_leave"]["terminal"] = False
    judge_prompt = _build_transition_judge_messages(
        engine,
        _session(engine),
        actor_performance={
            "performance": "（看向当前结果）这一步完成了。",
            "suggested_inputs": ["（完成当前动作）好了。"],
        },
        player_input="我来完成当前动作。",
    )[0].content

    assert "完成本幕最后一个普通行动只会让出口成熟，本身不是转场提议" in actor_prompt
    assert "结果成立后另提跨阶段行动" in actor_prompt
    assert "普通幕内行动、仅完成前置条件、泛问或只有按钮提出都为 false" in judge_prompt
    assert "无正文提议时为 false" in judge_prompt


def test_numeric_v2_prompts_keep_prerequisites_and_future_offers_in_separate_fields():
    """作者前提约束 Actor；未来提议不能被复核器误判为已播放结果。"""

    actor_prompt = numeric_v2_actor._system_prompt(
        catgirl_name="测试猫娘",
        player_address="哥哥",
        phase="turn",
    )
    engine = NumericV2Engine.from_mapping(numeric_v2_story())
    engine.nodes["ending_leave"]["type"] = "scene"
    engine.nodes["ending_leave"]["terminal"] = False
    judge_prompt = _build_transition_judge_messages(
        engine,
        _session(engine),
        actor_performance={
            "performance": "（停在当前阶段）我们现在开始下一步，好吗？",
            "suggested_inputs": ["（确认后开始）现在开始吧。"],
        },
        player_input="我们现在就开始。",
    )[0].content

    assert "前提须由指定主体公开成立" in actor_prompt
    assert "其他主体、沉默或依赖动作不能代替" in actor_prompt
    assert "同一主体可在一轮内按可见先后完成前提与依赖动作" in actor_prompt
    assert "作者明确分阶段或禁止当前公开时不可合并" in actor_prompt
    assert "玩家提前执行下游操作时" in actor_prompt
    assert "只省略、延后目标或写成未完成不是违规" in judge_prompt
    assert "不评剧情完成度" in judge_prompt
    assert "作者合同的‘你/你的’指玩家" in judge_prompt
    assert "同一主体可在同一回应先明确建立前提再执行自己的动作" in judge_prompt
    assert "明确的阶段、时点和禁止披露要求不可合并" in judge_prompt
    assert "保持未知的疑问、条件或推测" in judge_prompt
    # 日常直接执行也能承接完成结果；前提与外部结果仍必须有证据支持。
    assert "已实施动作的承接、证据支持的外部结果" in judge_prompt
    assert "未来邀请即使请求立即开始也不是已执行" in judge_prompt
    assert "提议方向错误只影响 valid，不等于正文已经越界" in judge_prompt


def test_numeric_v2_actor_finishes_low_risk_micro_action_instead_of_stalling():
    """低风险微调不能被拆成连续的等价等待回合。"""

    actor_prompt = numeric_v2_actor._system_prompt(
        catgirl_name="测试猫娘",
        player_address="哥哥",
        phase="turn",
    )

    assert "概括过程并交付已知事实支持的结果" in actor_prompt
    assert "遇到新风险、不可逆选择或阶段边界停下" in actor_prompt
    assert "不得索要等价微调、重复准备或移动终点" in actor_prompt


def test_numeric_v2_prompts_preserve_entity_ownership_across_the_turn():
    """玩家刚改变实体状态时，Actor 不能同时把同一实体交给另一主体。"""

    actor_prompt = numeric_v2_actor._system_prompt(
        catgirl_name="测试猫娘",
        player_address="哥哥",
        phase="turn",
    )
    engine = NumericV2Engine.from_mapping(numeric_v2_story())
    judge_prompt = _build_transition_judge_messages(
        engine,
        _session(engine),
        actor_performance={
            "performance": "（看向已经收好的东西）结果已经确认了。",
            "suggested_inputs": ["（继续交谈）接下来呢？"],
        },
        player_input="（把东西收好）先谈谈结果。",
    )[0].content

    assert "保持实体的持有者、位置和最新状态" in actor_prompt
    assert "主体、持有者和操作对象不能交换" in judge_prompt


def test_numeric_v2_actor_uses_ephemeral_interaction_intent_in_pacing():
    """交互意图只进入本轮节奏提示，不增加 Prompt 块或 Runtime 状态。"""

    engine = NumericV2Engine.from_mapping(numeric_v2_story())
    session = _session(engine)
    outcome = engine.resolve_turn(
        session,
        TurnRequestV2("interaction_intent", 0, "你现在是不是有点害怕？"),
        (),
        scene_complete=False,
    )

    chat_messages = _turn_messages(
        engine,
        session,
        outcome,
        "你现在是不是有点害怕？",
        "安静克制，习惯用短句回应。",
        "测试猫娘",
        "哥哥",
        interaction_intent="chat",
    )
    chat_payload = _payload(chat_messages)
    action_payload = _payload(_turn_messages(
        engine,
        session,
        outcome,
        "（推开门）我先看看外面。",
        "安静克制，习惯用短句回应。",
        "测试猫娘",
        "哥哥",
        interaction_intent="scene_action",
    ))
    mixed_payload = _payload(_turn_messages(
        engine,
        session,
        outcome,
        "你别怕，我现在把门推开。",
        "安静克制，习惯用短句回应。",
        "测试猫娘",
        "哥哥",
        interaction_intent="mixed_or_unclear",
    ))
    suggested_messages = _turn_messages(
        engine,
        session,
        outcome,
        "（闭上眼睛）晚安。",
        "安静克制，习惯用短句回应。",
        "测试猫娘",
        "哥哥",
        interaction_intent="chat",
        input_source="suggestion",
    )
    suggested_payload = _payload(suggested_messages)

    assert list(chat_payload) == list(action_payload) == list(mixed_payload)
    assert "纯闲聊回应合同" in chat_messages[0].content
    assert "transition_offered 必须为 false" in chat_messages[0].content
    assert "至多一条当前幕内的回归剧情选项" in chat_messages[0].content
    assert "不得离开当前幕或替玩家决定路线" in chat_messages[0].content
    assert "本轮主要是当前场景内的闲聊" in chat_payload["pacing"]
    assert "不能把闲聊当成转场接受" in chat_payload["pacing"]
    assert "先自然回应一句，再承接" not in chat_payload["pacing"]
    assert "交付一项最关键的作者方向事实" not in chat_payload["pacing"]
    assert chat_payload["next_scene"] == "本轮纯闲聊，不使用下一幕方向。"
    assert action_payload["next_scene"] != chat_payload["next_scene"]
    assert "直接交付这个行动" in action_payload["pacing"]
    assert "已知结果或明确未知，不重复确认" in action_payload["pacing"]
    assert "先回应其中的对白或情绪" in mixed_payload["pacing"]
    assert "纯闲聊回应合同" not in suggested_messages[0].content
    assert "推荐承接合同" in suggested_messages[0].content
    assert "本轮来自上一轮可见推荐" in suggested_payload["pacing"]
    assert "与自由输入适用同一行动授权" in suggested_messages[0].content
    assert "不能补出玩家未表达的后续操作" in suggested_messages[0].content
    assert "起步动作，按完整已知结果承接" not in suggested_payload["pacing"]
    assert suggested_payload["next_scene"] != chat_payload["next_scene"]


@pytest.mark.parametrize("suggestions_only", [False, True])
def test_numeric_v2_suggestion_prompts_preserve_current_action_roles(suggestions_only):
    """普通推荐与轻量补全都不能把猫娘的职责和持物套给玩家。"""

    if suggestions_only:
        messages = numeric_v2_actor._suggestion_fill_messages(
            catgirl_name="测试猫娘",
            performance={
                "performance": "（站稳在窗边）我来展示花束，你拿着相机就好。",
            },
            player_input="（举起相机）这个位置可以吗？",
            max_tokens=1800,
        )
        payload = json.loads(messages[1].content)
        assert "我来展示花束" in payload["visible_performance"]
        assert payload["player_input"] == "（举起相机）这个位置可以吗？"
        system_prompt = messages[0].content
        assert "由玩家持有或可直接取得" in system_prompt
    else:
        system_prompt = numeric_v2_actor._system_prompt(
            catgirl_name="测试猫娘",
            player_address="哥哥",
            phase="turn",
        )
        assert "保持实体的持有者、位置和最新状态" in system_prompt

    assert "不能把猫娘正在做的动作误写成玩家已在做" in system_prompt
    assert "不能接管猫娘正在做的动作" not in system_prompt


def test_numeric_v2_retry_places_correction_last_without_progress_pressure():
    """普通修稿以纠错收尾，保留六块及轮数，但不再同时要求交付或收束。"""

    story = numeric_v2_story()
    story["nodes"][0]["route_gates"] = [story["nodes"][0]["route_gates"][1]]
    story["nodes"] = [node for node in story["nodes"] if node["id"] != "ending_stay"]
    story["endings"] = [ending for ending in story["endings"] if ending["id"] != "stay"]
    engine = NumericV2Engine.from_mapping(story)
    session = replace(_session(engine), node_turn_count=5)
    player_input = "（举起相机对焦）这个角度可以吗？"
    outcome = engine.resolve_turn(
        session, TurnRequestV2("retry_prompt_priority", 0, player_input), (),
        scene_complete=True,
    )
    retry_hint = "删除未按快门却已经生成照片的冲突，保留对构图问题的回应。"
    for hint in ("", retry_hint):
        messages = _turn_messages(
            engine, session, outcome, player_input, "安静而认真。", "测试猫娘", "哥哥",
            retry_hint=hint, interaction_intent="scene_action",
        )
        payload = _payload(messages)
        assert list(payload) == [
            "role", "current_scene", "story_so_far", "pacing", "next_scene", "player_input",
        ]
        assert payload["player_input"] == player_input
        assert "当前是第 6 回合" in payload["pacing"]
        assert "本幕推荐 4 回合" in payload["pacing"]
        if hint:
            assert messages[0].content.endswith(retry_hint)
            assert "直接交付这个行动" not in payload["pacing"]
            assert "自然收束信号" not in payload["pacing"]
            assert "提出具体收束行动" not in payload["pacing"]
            assert "核心冲突尚未清楚时先交付关键事实" not in payload["pacing"]
        else:
            assert "直接交付这个行动" in payload["pacing"]
            assert "提出具体收束行动" in payload["pacing"]
            assert "核心冲突尚未清楚时先交付关键事实" in payload["pacing"]


def test_numeric_v2_actor_turns_natural_closure_into_offer_without_auto_advance():
    """自然收束只要求公开提议，不能把 scene_complete 当作自动换幕授权。"""

    story = numeric_v2_story()
    story["nodes"][0]["route_gates"] = [story["nodes"][0]["route_gates"][1]]
    story["nodes"] = [node for node in story["nodes"] if node["id"] != "ending_stay"]
    story["endings"] = [ending for ending in story["endings"] if ending["id"] != "stay"]
    engine = NumericV2Engine.from_mapping(story)
    session = _session(engine)
    outcome = engine.resolve_turn(
        session,
        TurnRequestV2(
            "natural_closure",
            0,
            "（闭上眼睛）今晚就先休息吧。",
            input_source="suggestion",
        ),
        (),
        scene_complete=True,
    )
    payload = _payload(_turn_messages(
        engine,
        session,
        outcome,
        "（闭上眼睛）今晚就先休息吧。",
        "安静克制，习惯用短句回应。",
        "测试猫娘",
        "哥哥",
        interaction_intent="mixed_or_unclear",
        input_source="suggestion",
    ))

    assert outcome.ledger_event["from_node_id"] == outcome.ledger_event["to_node_id"]
    assert "这不是自动换幕授权" in payload["pacing"]
    assert "先回应玩家，再依据已成立结果提出具体收束行动" in payload["pacing"]
    assert "接受后跨入哪个已知阶段或时点" in payload["pacing"]
    assert "只把跨越互动阶段的结果留待下一轮确认" in payload["pacing"]


def test_numeric_v2_transition_suggestion_fill_drops_consumed_source_input():
    """正式换幕后缺失推荐时只看目标开场，不能被已消费的旧幕输入拉回去。"""

    messages = numeric_v2_actor._suggestion_fill_messages(
        catgirl_name="测试猫娘",
        performance={
            "segments": [
                {"phase": "source_response", "performance": "晚安。"},
                {"phase": "transition_bridge", "scene_narration": "一夜过去。"},
                {
                    "phase": "target_opening",
                    "scene_narration": "清晨，玩家正在打扫房间。",
                    "performance": "别碰那些纸箱。",
                },
            ],
        },
        player_input="（闭上眼睛）晚安。",
        max_tokens=1800,
        after_scene_change=True,
    )
    payload = json.loads(messages[1].content)

    assert payload["player_input"] == ""
    assert "清晨，玩家正在打扫房间" in payload["visible_performance"]
    assert "一夜过去" not in payload["visible_performance"]
    assert "不得继续回应旧幕输入" in messages[0].content


def test_numeric_v2_repeated_long_clause_is_rejected_without_fuzzy_semantics():
    """换开头后原样复用主要威胁句仍属于高置信度机械复读。"""  # noqa: DOCSTRING_CJK

    previous = {
        "performance": "（别过脸）哼，睡你的觉去。要是敢打呼噜吵到人家，明天就把你扔出去。",
    }
    repeated = {
        "performance": "（敲了敲纸箱）哼，算你识相。要是敢打呼噜吵到人家，明天就把你扔出去。",
    }
    persona_only = {
        "performance": "（抱紧纸箱）哼，算你识相。今晚风小了些，人家也终于能安静吃完东西了。",
    }

    assert numeric_v2_actor._is_repeated_performance(repeated, previous) is True
    assert numeric_v2_actor._is_repeated_performance(persona_only, previous) is False


@pytest.mark.asyncio
async def test_numeric_v2_actor_accepts_safe_chat_repeat_on_final_retry(monkeypatch):
    """只有玩家自己重复原输入时才允许纯闲聊使用安全短确认。"""

    engine = NumericV2Engine.from_mapping(numeric_v2_story())
    session = replace(
        _session(engine),
        revision=1,
        node_turn_count=1,
        performance_history=({
            "revision": 1,
            "from_node_id": "start",
            "to_node_id": "start",
            "input_text": "我想听听你的感受。",
            "performance": "（轻轻摇头）我只是觉得心里空落落的，好像有什么很重要的东西怎么也想不起来。",
        },),
    )
    outcome = engine.resolve_turn(
        session,
        TurnRequestV2("chat_repeat", 1, "那你现在是什么感受？"),
        (),
        scene_complete=False,
    )
    actor = NumericV2Actor(object())

    async def fake_invoke(_messages, **_kwargs):
        return {
            "performance": "（轻轻摇头）我只是觉得心里空落落的，好像有什么很重要的东西怎么也想不起来。",
            "suggested_inputs": [
                "（坐在一旁）你愿意再说一点吗？",
                "（轻轻点头）我们也可以安静待一会儿。",
            ],
            "transition_offered": False,
        }

    monkeypatch.setattr(actor, "_invoke", fake_invoke)

    result = await actor.generate_turn(
        engine=engine,
        session=session,
        outcome=outcome,
        player_input="我想听听你的感受。",
        character_profile="安静克制，习惯用短句回应。",
        retry_hint="这是最后一次重复输出重试。",
        interaction_intent="chat",
    )

    assert "心里空落落的" in result["performance"]
    assert result["transition_offered"] is False


def test_numeric_v2_transition_prompt_preserves_causal_order_and_abstract_boundaries():
    """正式换场不能跳过目标幕前提或把抽象状态擅自具体化。"""  # noqa: DOCSTRING_CJK

    system = numeric_v2_actor._system_prompt(
        catgirl_name="测试猫娘",
        player_address="哥哥",
        phase="transition_compact",
    )

    assert "作者剧情方向是导演信息" in system
    assert "明确的因果先后不能倒置" in system
    assert "target_scene.opening_situation 已明确建立的内容" in system
    assert "作者只给出抽象状态或待确认事项时，不得自行具体化" in system


def test_numeric_v2_turn_prompt_keeps_opening_and_complete_scene_direction():
    """当前幕不能只剩开场画面，否则长对话或跑题后会丢失作者因果线。"""  # noqa: DOCSTRING_CJK

    engine = NumericV2Engine.from_mapping(numeric_v2_story())
    engine.nodes["start"]["story_beat"]["opening_scene"] = "雨后的花店门铃轻轻响起。"
    engine.nodes["start"]["story_beat"]["summary"] = (
        "小岚先回应重逢，再围绕仍未拆开的旧信与玩家商量下一步。"
    )
    session = _session(engine)
    outcome = engine.resolve_turn(
        session,
        TurnRequestV2("complete_scene_direction", 0, "我先听你把话说完。"),
        (),
        scene_complete=False,
    )

    messages = _turn_messages(
        engine,
        session,
        outcome,
        "我先听你把话说完。",
        "安静克制，习惯用短句回应。",
        "测试猫娘",
        "哥哥",
    )
    payload = _payload(messages)

    assert "当前已进入的开场处境：雨后的花店门铃轻轻响起。" in payload["current_scene"]
    assert "本幕完整剧情方向（自然演绎，不是任务清单，也不是已发生事实）" in payload["current_scene"]
    assert "围绕仍未拆开的旧信" in payload["current_scene"]


def test_numeric_v2_turn_prompt_keeps_hard_boundaries_without_allowed_checklist():
    """普通 Actor 保留禁演与事实边界，但不每轮复述 allowed 清单。"""  # noqa: DOCSTRING_CJK

    engine = NumericV2Engine.from_mapping(numeric_v2_story())
    engine.nodes["start"]["story_beat"]["acting_contract"] = {
        "allowed_behaviors": ["回应旧信的来历后保留自己的判断。"],
        "forbidden_behaviors": ["不得主动拥抱或把戒备写成依赖。"],
    }
    engine.nodes["start"]["story_beat"]["character_state"] = {
        "scene_boundaries": ["不得在玩家选择前离开花店。"],
    }
    session = _session(engine)
    outcome = engine.resolve_turn(
        session,
        TurnRequestV2("acting_boundaries", 0, "我会保持距离。"),
        (),
        scene_complete=False,
    )

    payload = _payload(_turn_messages(
        engine,
        session,
        outcome,
        "我会保持距离。",
        "安静克制，习惯用短句回应。",
        "测试猫娘",
        "哥哥",
    ))

    assert "本幕明确允许的演绎边界" not in payload["role"]
    assert "回应旧信的来历后保留自己的判断" not in payload["role"]
    assert "不得主动拥抱或把戒备写成依赖" not in payload["role"]
    assert "不得在玩家选择前离开花店" not in payload["role"]
    messages = _turn_messages(
        engine,
        session,
        outcome,
        "我会保持距离。",
        "安静克制，习惯用短句回应。",
        "测试猫娘",
        "哥哥",
    )
    assert "以下为本轮作者硬边界" in messages[0].content
    assert "不得主动拥抱或把戒备写成依赖" in messages[0].content
    assert "不得在玩家选择前离开花店" in messages[0].content


def test_numeric_v2_turn_prompt_distinguishes_entry_state_from_committed_changes():
    """作者入幕快照不能覆盖已发生变化，持续身份、能力和硬边界仍须保留。"""

    engine = NumericV2Engine.from_mapping(numeric_v2_story())
    beat = engine.nodes["start"]["story_beat"]
    beat["catgirl_situation"] = "她在门边等待。"
    beat["character_state"] = {
        "catgirl_state": "她站在门边，手中没有物品。",
        "player_state": "玩家站在柜台旁。",
        "environment_state": "旧信仍未拆开。",
        "scene_boundaries": ["不得在玩家选择前离开花店。"],
    }
    beat["acting_contract"] = {
        "cognition_state": "normal",
        "memory_state": "available",
        "assertable_self_facts": ["她能辨认自己的笔迹。"],
        "forbidden_behaviors": ["不得主动拥抱。"],
    }
    session = replace(
        _session(engine),
        revision=1,
        node_turn_count=1,
        performance_history=({
            "revision": 1,
            "from_node_id": "start",
            "to_node_id": "start",
            "input_text": "我把旧信拆开，放在窗边桌上。",
            "scene_narration": "拆开的旧信放在窗边桌上。",
            "performance": "（走到窗边坐下）现在可以一起看了。",
        },),
    )
    player_input = "我先读第一行。"
    outcome = engine.resolve_turn(
        session,
        TurnRequestV2("entry_state_is_not_current_state", 1, player_input),
        (),
        scene_complete=False,
    )
    messages = _turn_messages(
        engine, session, outcome, player_input,
        "安静克制，习惯用短句回应。", "测试猫娘", "哥哥",
    )
    payload = _payload(messages)

    # 作者起点明确是开场结束；下面对已提交拆信事实的检查仍保留，不能靠改标签复位历史。
    assert "开场演完后的处境：她在门边等待。" in payload["role"]
    assert "开场演完后的身体与认知状态：她站在门边，手中没有物品。" in payload["role"]
    assert "玩家开场演完后的已知处境：玩家站在柜台旁。" in payload["role"]
    assert "开场演完后的环境状态：旧信仍未拆开。" in payload["role"]
    assert "动态变化承接 story_so_far 已提交事实" in payload["role"]
    assert "作者身份、能力、认知/记忆限制与硬边界持续有效" in payload["role"]
    assert "此刻的身体" not in payload["role"]
    assert "眼前环境状态" not in payload["role"]
    assert "剧本身份：" in payload["role"]
    assert "她能辨认自己的笔迹。" in payload["role"]
    assert "认知与记忆限制：normal、available" in payload["role"]
    assert "与玩家的当前关系：" in payload["role"]
    assert "拆开的旧信放在窗边桌上。" in payload["story_so_far"]
    assert "（走到窗边坐下）现在可以一起看了。" in payload["story_so_far"]
    assert payload["player_input"] == player_input
    assert "不得在玩家选择前离开花店。" in messages[0].content
    assert "不得主动拥抱。" in messages[0].content


def test_numeric_v2_opening_only_boundary_expires_after_public_opening():
    """开场临时边界只进入开场和正式换场开场，不污染后续普通回合。"""

    story = numeric_v2_story()
    beat = story["nodes"][0]["story_beat"]
    beat["opening_only_boundaries"] = ["不得在公开开场披露后续身份。"]
    beat["must_not_happen"].append("不得替玩家作出最终决定。")
    engine = NumericV2Engine.from_mapping(story)

    opening_messages = numeric_v2_actor._opening_messages(
        engine,
        "安静克制，习惯用短句回应。",
        "测试猫娘",
        "哥哥",
    )
    opening_payload = json.loads(opening_messages[1].content)
    assert "以下为本轮作者硬边界" in opening_messages[0].content
    assert "不得在公开开场披露后续身份。" in opening_messages[0].content
    assert "不得在公开开场披露后续身份。" in opening_payload[
        "current_story_beat"
    ]["boundaries"]
    assert "scene_direction" not in opening_payload["current_story_beat"]
    assert "narrative_focus" not in opening_payload["current_story_beat"]

    session = _session(engine)
    outcome = engine.resolve_turn(
        session,
        TurnRequestV2("opening_boundary_expired", 0, "现在可以继续介绍了。"),
        (),
        scene_complete=False,
    )
    turn_messages = _turn_messages(
        engine,
        session,
        outcome,
        "现在可以继续介绍了。",
        "安静克制，习惯用短句回应。",
        "测试猫娘",
        "哥哥",
    )
    assert "不得在公开开场披露后续身份。" not in turn_messages[0].content
    assert "不得替玩家作出最终决定。" in turn_messages[0].content

    ordinary_review = _build_transition_judge_messages(
        engine,
        session,
        actor_performance={"performance": "（继续说明）这是后续身份。"},
        player_input="现在可以继续介绍了。",
    )
    ordinary_payload = json.loads(ordinary_review[1].content.split("：", 1)[1])
    assert "不得在公开开场披露后续身份。" not in ordinary_payload[
        "current_scene"
    ]["hard_boundaries"]

    opening_review = _build_transition_judge_messages(
        engine,
        session,
        actor_performance={"performance": "（刚进入新幕）开场。"},
        player_input="好，我们过去。",
        route_changed=True,
    )
    opening_review_payload = json.loads(
        opening_review[1].content.split("：", 1)[1]
    )
    assert "不得在公开开场披露后续身份。" in opening_review_payload[
        "current_scene"
    ]["hard_boundaries"]


def test_numeric_v2_structured_persona_traits_are_tone_only():
    """核心特质不得绕过当前关系合同变成亲密动作或既有关系。"""  # noqa: DOCSTRING_CJK

    profile = numeric_v2_actor._profile_for_acting_contract(
        "昵称: 小葵\n自称: 人家\n核心特质: 温柔体贴,粘人撒娇\n行为特点: 喜欢蹭手心",
        {"persona_scope": "style_only"},
    )

    assert "自称: 人家" in profile
    assert "语言氛围参考（只影响措辞" in profile
    assert "温柔体贴,粘人撒娇" in profile
    assert "行为特点" not in profile


def test_numeric_v2_overdue_pacing_requires_new_result_and_concrete_exit():
    # 超过推荐回合两轮后，Prompt 必须阻止重复催促，并要求可见推进与具体出口。
    pacing = numeric_v2_actor._soft_pacing(
        {"min_turns": 1, "recommended_turns": 3},
        5,
        route_changed=False,
    )

    assert pacing["phase"] == "overdue"
    assert "核心因果发生一次可见变化" in pacing["instruction"]
    assert "结果尚未成立时交付结果或真实选择" in pacing["instruction"]
    assert "不得新增支线或补齐可选内容来延长" in pacing["instruction"]
    assert "自然出口成熟" in pacing["instruction"]
    assert "基于已发生事实" in pacing["instruction"]


def test_numeric_v2_recommended_turn_is_not_a_forced_offer_countdown():
    """刚到推荐回合只做软聚焦，不能要求 Actor 无条件提出转场。"""  # noqa: DOCSTRING_CJK

    pacing = numeric_v2_actor._soft_pacing(
        {"min_turns": 3, "recommended_turns": 3},
        3,
        route_changed=False,
    )

    assert pacing["phase"] == "closure"
    assert "只是软节奏参考" in pacing["instruction"]
    assert "不是必须提议或换幕的倒计时" in pacing["instruction"]
    assert "已有自然出口时可以提出" in pacing["instruction"]


def test_numeric_v2_soft_boundary_prefers_authored_fact_over_new_task_chain():
    """软收束时核心冲突未建立，应直接演出作者事实，不能不断增设调查步骤。"""  # noqa: DOCSTRING_CJK

    engine = NumericV2Engine.from_mapping(numeric_v2_story())
    session = replace(_session(engine), node_turn_count=3)
    outcome = engine.resolve_turn(
        session,
        TurnRequestV2("soft_boundary_focus", 0, "我再检查一下眼前的线索。"),
        (),
        scene_complete=False,
    )

    pacing = _payload(_turn_messages(
        engine,
        session,
        outcome,
        "我再检查一下眼前的线索。",
        "安静克制，习惯用短句回应。",
        "测试猫娘",
        "哥哥",
    ))["pacing"]

    assert "对照本幕方向与已提交历史" in pacing
    assert "核心冲突尚未清楚时先交付关键事实" in pacing
    assert "不新增中间条件" in pacing
    assert "已清楚时让连续行动落到结果或自然出口" in pacing


def test_numeric_v2_turn_prompt_keeps_real_current_scene_history():
    # 已提交的玩家输入和猫娘回复必须进入 story_so_far，推荐草稿不能代替真实历史。
    engine = NumericV2Engine.from_mapping(numeric_v2_story())
    session = _session(engine)
    history_record = {
        "revision": 1,
        "from_node_id": "start",
        "to_node_id": "start",
        "input_text": "我愿意先留下来听你解释。",
        "performance": "（抬眼）那就先坐下。",
    }
    session = replace(
        session,
        revision=1,
        node_turn_count=1,
        performance_history=(history_record,),
    )
    outcome = engine.resolve_turn(
        session,
        TurnRequestV2("six_blocks_history", 1, "我坐到了窗边。"),
        (),
        scene_complete=False,
    )

    payload = _payload(_turn_messages(
        engine,
        session,
        outcome,
        "我坐到了窗边。",
        "安静克制，习惯用短句回应。",
        "测试猫娘",
        "哥哥",
    ))

    assert "玩家：我愿意先留下来听你解释。" in payload["story_so_far"]
    assert "猫娘：（抬眼）那就先坐下。" in payload["story_so_far"]
    assert payload["player_input"] == "我坐到了窗边。"


def test_numeric_v2_first_turn_after_transition_keeps_only_short_source_tail():
    """新幕首回合只承接旧幕末尾的可见余波，不恢复旧输入或旧任务。"""

    engine = NumericV2Engine.from_mapping(numeric_v2_story())
    transition_record = {
        "revision": 1,
        "from_node_id": "ending_leave",
        "to_node_id": "start",
        "input_text": "我答应替你继续追查旧信和钥匙。",
        "segments": [
            {
                "phase": "source_response",
                "performance": (
                    "（收起旧信）旧信和钥匙明天继续追查。"
                    "（抹去眼泪）我已经没事了。"
                ),
            },
            {
                "phase": "transition_bridge",
                "scene_narration": "雨声渐渐停下。",
            },
            {
                "phase": "target_opening",
                "scene_narration": "清晨的花店重新亮起灯。",
            },
        ],
    }
    session = replace(
        _session(engine),
        revision=1,
        node_turn_count=0,
        performance_history=(transition_record,),
    )
    outcome = engine.resolve_turn(
        session,
        TurnRequestV2("first_turn_after_transition", 1, "你眼睛还红着。"),
        (),
        scene_complete=False,
    )

    payload = _payload(_turn_messages(
        engine,
        session,
        outcome,
        "你眼睛还红着。",
        "安静克制，习惯用短句回应。",
        "测试猫娘",
        "哥哥",
    ))

    assert "上一幕尾声" in payload["story_so_far"]
    assert "（抹去眼泪）我已经没事了。" in payload["story_so_far"]
    assert "旧信和钥匙明天继续追查" not in payload["story_so_far"]
    assert "我答应替你继续追查旧信和钥匙" not in payload["story_so_far"]
    assert "雨声渐渐停下" in payload["story_so_far"]
    assert "清晨的花店重新亮起灯" in payload["story_so_far"]


def test_numeric_v2_previous_scene_tail_disappears_after_first_current_scene_turn():
    """新幕已经产生普通回合后，旧幕尾声不再重复注入 Prompt。"""

    engine = NumericV2Engine.from_mapping(numeric_v2_story())
    session = replace(
        _session(engine),
        revision=2,
        node_turn_count=1,
        performance_history=(
            {
                "revision": 1,
                "from_node_id": "ending_leave",
                "to_node_id": "start",
                "input_text": "我答应替你继续追查旧信。",
                "segments": [
                    {
                        "phase": "source_response",
                        "performance": "（抹去眼泪）我已经没事了。",
                    },
                    {
                        "phase": "transition_bridge",
                        "scene_narration": "雨声渐渐停下。",
                    },
                    {
                        "phase": "target_opening",
                        "scene_narration": "清晨的花店重新亮起灯。",
                    },
                ],
            },
            {
                "revision": 2,
                "from_node_id": "start",
                "to_node_id": "start",
                "input_text": "我把窗帘拉开了。",
                "performance": "（眯起眼）晨光有点亮。",
            },
        ),
    )
    outcome = engine.resolve_turn(
        session,
        TurnRequestV2("second_turn_after_transition", 2, "我替你挡一下光。"),
        (),
        scene_complete=False,
    )

    payload = _payload(_turn_messages(
        engine,
        session,
        outcome,
        "我替你挡一下光。",
        "安静克制，习惯用短句回应。",
        "测试猫娘",
        "哥哥",
    ))

    assert "上一幕尾声" not in payload["story_so_far"]
    assert "抹去眼泪" not in payload["story_so_far"]
    assert "我答应替你继续追查旧信" not in payload["story_so_far"]
    assert "玩家：我把窗帘拉开了。" in payload["story_so_far"]
    assert "猫娘：（眯起眼）晨光有点亮。" in payload["story_so_far"]


def test_numeric_v2_turn_prompt_separates_opening_facts_from_scene_direction():
    """普通回合保留完整方向，但明确它不是事实或逐项任务。"""

    story = numeric_v2_story()
    story["nodes"][0]["story_beat"]["summary"] = (
        "先观察门外，再修复旧信，最后提出离开；这些都是作者方向而非当前事实。"
    )
    story["nodes"][0]["story_beat"]["opening_scene"] = "雨后的花店门口只有一盏昏黄路灯。"
    engine = NumericV2Engine.from_mapping(story)
    session = _session(engine)
    outcome = engine.resolve_turn(
        session,
        TurnRequestV2("opening_projection", 0, "我先看看门外。"),
        (),
        scene_complete=False,
    )

    messages = _turn_messages(
        engine,
        session,
        outcome,
        "我先看看门外。",
        "安静克制，习惯用短句回应。",
        "测试猫娘",
        "哥哥",
    )
    payload = _payload(messages)

    assert "当前已进入的开场处境：雨后的花店门口只有一盏昏黄路灯。" in payload["current_scene"]
    assert "本幕完整剧情方向（自然演绎，不是任务清单，也不是已发生事实）" in payload["current_scene"]
    assert "先观察门外" in payload["current_scene"]
    assert "未发生内容不能当作角色知识、环境事实或完成结果" in messages[0].content


def test_numeric_v2_turn_prompt_keeps_ineligible_next_scene_unknown():
    # 当前数值没有任何合格出口时，不能把某个候选伪装成已选方向。
    engine = NumericV2Engine.from_mapping(numeric_v2_story())
    for route in engine.nodes["start"]["route_gates"]:
        route["conditions"] = {"all": [{"metric": "trust", "op": ">=", "value": 101}]}
    session = _session(engine)
    outcome = engine.resolve_turn(
        session,
        TurnRequestV2("six_blocks_unresolved", 0, "先看看花店。"),
        (),
        scene_complete=False,
    )

    payload = _payload(_turn_messages(
        engine,
        session,
        outcome,
        "先看看花店。",
        "安静克制，习惯用短句回应。",
        "测试猫娘",
        "哥哥",
    ))

    assert payload["next_scene"] == (
        "下一幕尚未确定；玩家接受具体转场提议后由 Runtime 决定。"
    )


def test_numeric_v2_turn_prompt_only_exposes_next_scene_direction():
    """普通回合只收到下一幕方向，不能提前读取下一幕完整摘要。"""

    next_scene = numeric_v2_actor._next_scene_summary_text({
        "status": "after_acceptance_only",
        "chapter_title": "长街旧信",
        "transition_direction": "沿着长街寻找旧信。",
        "summary_after_acceptance": "旧信与备用钥匙并排放在桌面上。",
    })

    assert next_scene.startswith("接受后进入的下一互动阶段主题是《长街旧信》")
    assert "接受当前转场提议后，剧情方向是：沿着长街寻找旧信。" in next_scene
    assert "不是目标清单或固定动作" in next_scene
    assert "语义等价方案" in next_scene
    assert "旧信与备用钥匙并排放在桌面上" not in next_scene


@pytest.mark.parametrize("changed_field", ["reason", "bridge_scene_narration", "must_deliver"])
def test_same_target_different_contracts_preview_uses_eligible_route(changed_field):
    """同目标不同合同也按条件选取，不依赖列表顺序，预览不改动引擎。"""

    from copy import deepcopy

    engine = NumericV2Engine.from_mapping(numeric_v2_story())
    session = _session(engine)
    cast = numeric_v2_evaluator._cast_for_session(engine, session)
    source = engine.nodes["start"]
    routes = source["route_gates"]
    routes[0]["target_node_id"] = routes[1]["target_node_id"]
    routes[0]["transition_contract"][changed_field] = ["另一种衔接"] if changed_field == "must_deliver" else "另一种衔接"
    before = deepcopy(source)
    expected = engine.preview_route("start", session.metrics)
    preview = numeric_v2_actor._next_scene_preview_for_actor(engine, cast, source, session.metrics)
    assert preview["status"] == "after_acceptance_only"
    assert preview["transition_direction"] == expected["transition_contract"]["reason"]
    routes.reverse()
    assert numeric_v2_actor._next_scene_preview_for_actor(engine, cast, source, session.metrics) == preview
    routes.reverse()
    assert source == before


@pytest.mark.parametrize("trust,route_index", [(20, 1), (80, 0)])
def test_preview_does_not_lock_runtime_selection_after_acceptance(trust, route_index):
    """邀请预览与接受时可走不同路线：预览无写入，接受仍按新数值选路。"""

    engine = NumericV2Engine.from_mapping(numeric_v2_story())
    source = engine.nodes["start"]
    routes = source["route_gates"]
    routes[0]["transition_contract"]["reason"] = "第一种衔接。"
    routes[1]["transition_contract"]["reason"] = "第二种衔接。"
    session = replace(_session(engine), metrics={"trust": 100 - trust}, transition_offered=True)
    cast = numeric_v2_evaluator._cast_for_session(engine, session)
    preview = numeric_v2_actor._next_scene_preview_for_actor(engine, cast, source, session.metrics)
    assert preview["transition_direction"] == routes[1 - route_index]["transition_contract"]["reason"]
    session = replace(session, metrics={"trust": trust})
    outcome = engine.resolve_turn(session, TurnRequestV2("accept_contract", 0, "好，就这样。"), (), transition_intent="accept")
    assert outcome.session.current_node_id == routes[route_index]["target_node_id"]
    assert outcome.transition_contract == routes[route_index]["transition_contract"]


@pytest.mark.parametrize("trust", [20, 80])
def test_multiroute_completed_scene_gets_current_direction_and_closure(trust):
    """Actor 与同轮 Guard 共用变化后数值；完成信号不再因出口数量被丢弃。"""

    engine = NumericV2Engine.from_mapping(numeric_v2_story())
    session = replace(_session(engine), metrics={"trust": 100 - trust})
    outcome = engine.resolve_turn(session, TurnRequestV2("closure", 0, "处理好了。"), (), scene_complete=True)
    outcome = replace(outcome, session=replace(outcome.session, metrics={"trust": trust}))
    messages = _turn_messages(engine, session, outcome, "处理好了。", "克制", "测试猫娘", "哥哥")
    route = engine.preview_route("start", outcome.session.metrics)
    assert route["transition_contract"]["reason"] in _payload(messages)["next_scene"]
    assert "本轮自然收束合同" in messages[0].content
    assert "trust" not in _payload(messages)["next_scene"]
    guard = _build_transition_judge_messages(engine, outcome.session, player_input="处理好了。", actor_performance={"performance": "好了。"})
    assert json.loads(guard[1].content.split("：", 1)[1])["next_scene_direction"]["chapter"] in _payload(messages)["next_scene"]


def test_guard_current_opening_is_not_the_whole_legacy_scene_summary():
    """开场与整幕方向分别投影，不把摘要后续事件误当作已播放开场。"""

    engine = NumericV2Engine.from_mapping(numeric_v2_story())
    engine.nodes["start"]["story_beat"]["summary"] = "猫娘站在门边。随后才解释旧信。"
    messages = _build_transition_judge_messages(engine, _session(engine), player_input="你好。",
        actor_performance={"performance": "你好。", "suggested_inputs": []})
    current = json.loads(messages[1].content.split("：", 1)[1])["current_scene"]
    assert current["opening_situation"] == "猫娘站在门边。"
    assert current["story_direction"] == "猫娘站在门边。随后才解释旧信。"


@pytest.mark.parametrize("opening,summary,expected", [
    ("显式开场。完整第二句。", "摘要首句。后续事件。", "显式开场。完整第二句。"),
    (None, "摘要首句。后续事件尚未发生。", "摘要首句。"),
    ("   ", "摘要第一行\n后续事件尚未发生。", "摘要第一行"),
    (None, "", "她在门边等待。"),
])
def test_actor_and_guard_share_actual_opening_fallback(opening, summary, expected):
    """正文播放与复核必须读取同一开场，不能把整幕摘要当成开场。"""

    engine = NumericV2Engine.from_mapping(numeric_v2_story())
    beat = engine.nodes["ending_leave"]["story_beat"]
    beat.update(summary=summary, catgirl_situation="她在门边等待。后续仍待演出。")
    if opening is None:
        beat.pop("opening_scene", None)
    else:
        beat["opening_scene"] = opening
    session = _session(engine)
    cast = numeric_v2_evaluator._cast_for_session(engine, session)
    played_opening = numeric_v2_actor._beat_for_actor(cast, beat)["opening_scene"]
    messages = _build_transition_judge_messages(engine, session, player_input="现在呢？",
        actor_performance={"performance": "这里的事情已告一段落。", "suggested_inputs": []})
    payload = json.loads(messages[1].content.split("：", 1)[1])
    assert played_opening == expected
    assert payload["next_scene_direction"]["opening_boundary"] == played_opening



def test_numeric_v2_turn_prompt_allows_same_place_ending_closure():
    """结局节点即使不改变地点，也要给 Actor 一个可执行的收束方向。"""

    story = numeric_v2_story()
    # 只保留一个结局出口，使普通回合能够确定这是结局收束而非未决分支。
    # 选择测试初始信任度可达的“离开”结局出口，避免制造不可达节点。
    story["nodes"][0]["route_gates"] = story["nodes"][0]["route_gates"][1:]
    story["nodes"] = [
        node for node in story["nodes"] if node["id"] != "ending_stay"
    ]
    story["endings"] = [ending for ending in story["endings"] if ending["id"] == "leave"]
    engine = NumericV2Engine.from_mapping(story)
    session = replace(_session(engine), node_turn_count=5)
    outcome = engine.resolve_turn(
        session,
        TurnRequestV2("same_place_ending", session.revision, "我先陪你把日志看完。"),
        (),
        scene_complete=False,
    )

    payload = _payload(_turn_messages(
        engine,
        session,
        outcome,
        "我先陪你把日志看完。",
        "安静克制，习惯用短句回应。",
        "测试猫娘",
        "哥哥",
    ))

    # 结局可以自然结束，提示不再要求为收束额外发出邀请。
    assert payload["next_scene"].startswith("下一阶段是结局余韵")
    assert "不为结束追加邀请" in payload["next_scene"]
    assert "自然出口成熟" in payload["pacing"]
    assert "雨停后的长街恢复了安静" not in payload["next_scene"]
    assert "不得提前描写结局独有的地点" in payload["next_scene"]


def test_numeric_v2_prompts_share_a_non_task_narrative_focus():
    """Actor 与 Evaluator 都能看到同一条叙事重心，但不产生目标字段。"""

    story = numeric_v2_story()
    story["nodes"][0]["story_beat"]["narrative_focus"] = "先听完她对旧信的解释。"
    engine = NumericV2Engine.from_mapping(story)
    session = _session(engine)
    outcome = engine.resolve_turn(
        session,
        TurnRequestV2("shared_focus", 0, "我继续听。"),
        (),
        scene_complete=False,
    )

    actor_payload = _payload(_turn_messages(
        engine,
        session,
        outcome,
        "我继续听。",
        "安静克制，习惯用短句回应。",
        "测试猫娘",
        "哥哥",
    ))
    evaluator_payload = json.loads(
        _build_evaluator_messages(engine, session, "我继续听。")[1]
        .content.split("：\n", 1)[1]
    )

    assert "先听完她对旧信的解释" in actor_payload["pacing"]
    assert evaluator_payload["current_story_beat"]["narrative_focus"] == (
        "先听完她对旧信的解释。"
    )


def test_numeric_v2_legacy_focus_prefers_scene_direction_over_opening():
    """旧剧本没有显式重心时，应避免每回合重复把开场画面当作创作重点。"""

    beat = {
        "opening_scene": "门边的旧灯正在闪烁，桌上放着一枚未开启的徽章。",
        "summary": "两人已经确认灯光来自走廊深处，接下来可以沿着声音寻找出口。",
        "transition_goal": "沿着走廊深处的声音寻找出口，并在玩家同意后离开房间。",
    }

    assert scene_narrative_focus(beat) == beat["transition_goal"]


def test_numeric_v2_pending_transition_is_highlighted_for_recommendations():
    """待确认转场的具体正文应从长历史中单独投影，避免推荐只看到布尔状态。"""

    engine = NumericV2Engine.from_mapping(numeric_v2_story())
    session = replace(
        _session(engine),
        revision=1,
        node_turn_count=1,
        transition_offered=True,
        performance_history=(
            {
                "revision": 1,
                "from_node_id": "start",
                "to_node_id": "start",
                "input_text": "我想继续听你说。",
                "performance": "（望向门外）我们可以沿着长街去找旧信，你愿意现在出发吗？",
                "transition_offered": True,
            },
        ),
    )
    outcome = engine.resolve_turn(
        session,
        TurnRequestV2("pending_transition", 1, "我先看看你的状态。"),
        (),
        scene_complete=False,
        transition_intent="unclear",
    )

    payload = _payload(_turn_messages(
        engine,
        session,
        outcome,
        "我先看看你的状态。",
        "安静克制，习惯用短句回应。",
        "测试猫娘",
        "哥哥",
    ))

    assert "当前待确认提议原文" in payload["pacing"]
    assert "沿着长街去找旧信" in payload["pacing"]
    assert "推荐第一条接受并亲自执行旧提议" in payload["pacing"]
    assert "第二条拒绝、暂缓或留在本幕" in payload["pacing"]


def test_numeric_v2_simple_prompt_packing_drops_oldest_complete_history():
    # 预算不足时只淘汰最早整条记录，最新回合和玩家输入始终保留。
    history = [
        {"revision": 1, "player_input": "旧回合一", "performance": "（动作）旧回复一"},
        {"revision": 2, "player_input": "旧回合二", "performance": "（动作）旧回复二"},
        {"revision": 3, "player_input": "最新回合", "performance": "（动作）最新回复"},
    ]
    data = {
        "role": "你是测试猫娘。",
        "current_scene": "当前幕剧情方向。",
        "story_so_far": "",
        "pacing": "当前是第 3 回合，本幕推荐 4 回合。",
        "next_scene": "下一幕剧情方向。",
        "player_input": "我继续观察。",
    }

    fitted = numeric_v2_actor._fit_simple_turn_prompt_data(
        system_prompt="只回应玩家。",
        human_prefix="以下 JSON 是本回合六块演绎上下文：\n",
        data=data,
        history_rows=history,
        max_tokens=95,
    )

    assert "旧回合一" not in fitted["story_so_far"]
    assert "最新回合" in fitted["story_so_far"]
    assert fitted["player_input"] == "我继续观察。"


def test_numeric_v2_scene_fact_index_keeps_early_committed_progress() -> None:
    """长幕完整历史被裁剪后，早期已经完成的玩家行动仍须作为可见事实保留。"""

    session = type("Session", (), {
        "current_node_id": "medical",
        "performance_history": (
            {
                "revision": 1,
                "from_node_id": "medical",
                "to_node_id": "medical",
                "input_text": "我已经找到聚在一起的三名平民。",
                "performance": "（守住门口）我看见你们了。",
            },
            {
                "revision": 2,
                "from_node_id": "medical",
                "to_node_id": "medical",
                "input_text": "我先带第一名伤员出去。",
                "performance": "（让出通道）交给我接应。",
            },
        ),
    })()

    fact_index = numeric_v2_actor._current_scene_fact_index_text(session)

    assert "已经找到聚在一起的三名平民" in fact_index
    assert "先带第一名伤员出去" in fact_index


@pytest.mark.parametrize("trim_history", [False, True])
def test_numeric_v2_turn_only_builds_fact_index_after_history_preselection(
    monkeypatch, trim_history,
):
    """完整历史只出现一次；预选窗口删掉旧记录后才计算连续性摘录。"""

    engine = NumericV2Engine.from_mapping(numeric_v2_story())
    records = tuple({
        "revision": index,
        "from_node_id": "start",
        "to_node_id": "start",
        "input_text": f"玩家第 {index} 轮的真实输入。",
        "performance": f"（抬眼）第 {index} 轮的实际回应。",
    } for index in range(1, 4))
    session = replace(_session(engine), revision=3, node_turn_count=3,
                      performance_history=records)
    outcome = engine.resolve_turn(
        session, TurnRequestV2("index_gate", 3, "现在继续。"), (),
        scene_complete=False,
    )
    calls = []

    def fact_index(_session):
        calls.append(_session.revision)
        return "早期记录摘录。"

    monkeypatch.setattr(numeric_v2_actor, "_current_scene_fact_index_text", fact_index)
    if trim_history:
        budget = numeric_v2_actor.numeric_v2_actor_budget("balanced")
        monkeypatch.setattr(numeric_v2_actor, "numeric_v2_actor_budget",
                            lambda _: {**budget, "history_max_tokens": 1})
    payload = _payload(_turn_messages(
        engine, session, outcome, "现在继续。", "安静克制", "测试猫娘", "哥哥",
    ))
    assert calls == ([3] if trim_history else [])
    assert ("早期记录摘录" in payload["story_so_far"]) is trim_history
    assert payload["story_so_far"].count("第 3 轮的实际回应") == 1
    if not trim_history:
        for index in range(1, 4):
            assert payload["story_so_far"].count(f"第 {index} 轮的实际回应") == 1
    assert payload["player_input"] == "现在继续。"


def test_numeric_v2_fact_index_is_budgeted_when_final_packing_drops_history():
    """总预算触发索引后要重新计费，不能溢出或截断最新真实回合。"""

    history = [
        {"revision": 1, "player_input": "很早的对话。" * 150, "performance": "旧回复。"},
        {"revision": 2, "player_input": "本轮前的真实输入。", "performance": "最新完整回复。"},
    ]
    data = dict(role="猫娘", current_scene="当前幕", story_so_far="",
                pacing="当前第 3 回合。", next_scene="未定", player_input="继续。")
    calls = []
    diagnostics = {}

    def fact_index():
        calls.append(True)
        return "早期已提交片段。"

    fitted = numeric_v2_actor._fit_simple_turn_prompt_data(
        system_prompt="回应玩家。", human_prefix="", data=data,
        history_rows=history, max_tokens=200, fact_index=fact_index,
        diagnostics=diagnostics,
    )
    assert calls == [True]
    assert diagnostics["fact_index_included"] is True
    assert diagnostics["history_dropped_revisions"] == [1]
    assert diagnostics["final_tokens"] <= 200
    assert "很早的对话" not in fitted["story_so_far"]
    assert "早期已提交片段" in fitted["story_so_far"]
    assert "最新完整回复。" in fitted["story_so_far"]
    assert fitted["player_input"] == "继续。"


def test_numeric_v2_fact_index_does_not_displace_oversized_latest_turn():
    """剩余固定内容仍超限时明确失败，不静默截掉玩家最新输入。"""

    with pytest.raises(numeric_v2_actor.NumericV2ActorError,
                       match="fixed_context_budget_exceeded"):
        numeric_v2_actor._fit_simple_turn_prompt_data(
            system_prompt="回应玩家。", human_prefix="", data={"player_input": "继续。"},
            history_rows=[{"revision": 2, "performance": "最新完整对话。" * 100}],
            history_preselected=True, fact_index=lambda: "更早的片段。", max_tokens=60,
        )


@pytest.mark.parametrize("pure_chat", [False, True])
def test_numeric_v2_rejected_or_chat_turn_has_no_competing_closure_instruction(pure_chat):
    """自然收束和超期不能覆盖拒绝或闲聊；旧提议也不能把闲聊推荐变成接受按钮。"""

    engine = NumericV2Engine.from_mapping(numeric_v2_story())
    # 单出口保证不是因为路线未决而跳过自然收束提示。
    engine.nodes["start"]["route_gates"] = [engine.nodes["start"]["route_gates"][0]]
    session = replace(_session(engine), node_turn_count=8, transition_offered=True)
    outcome = engine.resolve_turn(
        session, TurnRequestV2("reject_or_chat", 0, "我想先和你聊聊。"), (),
        scene_complete=True, transition_intent="unclear" if pure_chat else "reject",
    )
    # Actor 在 prepare 后接收的提议状态以 Runtime 结算为准。
    session = replace(session, transition_offered=outcome.session.transition_offered)
    payload = _payload(_turn_messages(
        engine, session, outcome, "我想先和你聊聊。", "安静克制", "测试猫娘", "哥哥",
        interaction_intent="chat" if pure_chat else "scene_action",
    ))
    assert "本轮有自然收束信号" not in payload["pacing"]
    assert "对照本幕方向与已提交历史" not in payload["pacing"]
    if pure_chat:
        assert "超过推荐展开长度" not in payload["pacing"]
        assert "推荐第一条接受" not in payload["pacing"]
        assert "旧提议由 Runtime 保留" in payload["pacing"]
    else:
        assert "玩家拒绝或暂停了上一提议" in payload["pacing"]


def test_numeric_v2_next_scene_projection_excludes_future_story_fields():
    """中间投影只携带当前合格出口方向，不携带目标幕正文。"""

    engine = NumericV2Engine.from_mapping(numeric_v2_story())
    cast = NumericV2CastProjection.from_story(
        engine.story, player_name="哥哥", catgirl_name="测试猫娘")
    source = engine.nodes["start"]
    assert numeric_v2_actor._next_scene_preview_for_actor(engine, cast, source, {"trust": 20})["status"] == "after_acceptance_only"
    preview = numeric_v2_actor._next_scene_preview_for_actor(engine, cast, source, {"trust": 20})
    assert set(preview) == {
        "status", "chapter_title", "target_is_ending", "transition_direction",
    }
    assert preview["status"] == "after_acceptance_only"
    assert preview["target_is_ending"] is True


def test_numeric_v2_simple_prompt_packing_drops_previous_scene_tail_first():
    """预算紧张时优先舍弃临时旧幕余波，不删除换场事实或当前输入。"""

    history = [{
        "revision": 1,
        "player_input": "",
        "segments": [
            {
                "phase": "previous_scene_tail",
                "performance": "（眼眶微红）我会慢慢平静下来。",
            },
            {
                "phase": "transition_bridge",
                "scene_narration": "雨声停下。",
            },
            {
                "phase": "target_opening",
                "scene_narration": "清晨的花店重新亮起灯。",
            },
        ],
    }]
    without_tail = [{
        **history[0],
        "segments": history[0]["segments"][1:],
    }]
    data = {
        "role": "你是测试猫娘。",
        "current_scene": "清晨的花店。",
        "story_so_far": "",
        "pacing": "当前是第 1 回合，本幕推荐 3 回合。",
        "next_scene": "下一幕尚未确定。",
        "player_input": "我注意到你眼睛还红着。",
    }
    system_prompt = "只回应玩家。"
    human_prefix = "以下 JSON 是本回合六块演绎上下文：\n"
    expected_data = {
        **data,
        "story_so_far": numeric_v2_actor._story_so_far_text(without_tail),
    }
    exact_budget = (
        numeric_v2_actor.count_tokens(system_prompt)
        + numeric_v2_actor.count_tokens(
            human_prefix
            + json.dumps(expected_data, ensure_ascii=False, separators=(",", ":"))
        )
    )

    def unexpected_index():
        pytest.fail("仅移除上一幕短尾声，不应生成当前幕索引")

    fitted = numeric_v2_actor._fit_simple_turn_prompt_data(
        system_prompt=system_prompt,
        human_prefix=human_prefix,
        data=data,
        history_rows=history,
        max_tokens=exact_budget,
        fact_index=unexpected_index,
    )

    assert "上一幕尾声" not in fitted["story_so_far"]
    assert "雨声停下" in fitted["story_so_far"]
    assert "清晨的花店重新亮起灯" in fitted["story_so_far"]
    assert fitted["player_input"] == "我注意到你眼睛还红着。"


def test_numeric_v2_actor_parses_visible_suggestions_in_same_output():
    # 正文和推荐必须来自同一个 JSON；推荐只保留可直接提交的第一人称动作加对白。
    parsed = _parse_output(json.dumps({
        "performance": "（抬眼）我听到了。",
        "transition_offered": True,
        "suggested_inputs": [
            "（我点头）我先听你说完。",
            "（我后退一步）我想先看看周围。",
        ],
    }, ensure_ascii=False))

    assert parsed["performance"] == "（抬眼）我听到了。"
    assert parsed["transition_offered"] is True
    assert parsed["suggested_inputs"] == [
        "（我点头）我先听你说完。",
        "（我后退一步）我想先看看周围。",
    ]


def test_numeric_v2_actor_keeps_body_when_suggestions_are_malformed():
    # 推荐格式失败只降级为空列表，不能因为推荐脏数据丢弃已经合法的猫娘正文。
    parsed = _parse_output(json.dumps({
        "performance": "（抬眼）我听到了。",
        "transition_offered": False,
        "suggested_inputs": ["这不是第一人称推荐", "（我点头）"],
    }, ensure_ascii=False))

    assert parsed["performance"] == "（抬眼）我听到了。"
    assert parsed["transition_offered"] is False
    assert parsed["suggested_inputs"] == []


def test_numeric_v2_actor_drops_suggestion_template_placeholders():
    """一键发送推荐不能把未替换的姓名占位符展示给玩家。"""  # noqa: DOCSTRING_CJK

    parsed = _parse_output(json.dumps({
        "performance": "（抬眼）你可以先介绍自己。",
        "transition_offered": False,
        "suggested_inputs": [
            "（我保持距离）我叫[你的名字]，是这里的修理工。",
            "（我放下双手）我是这里的修理工。",
            "（我指向工作台）这里是我的工作室。",
        ],
    }, ensure_ascii=False))

    assert parsed["suggested_inputs"] == [
        "（我放下双手）我是这里的修理工。",
        "（我指向工作台）这里是我的工作室。",
    ]


def test_numeric_v2_actor_rejects_non_boolean_transition_flag():
    # 转场状态只能由 Actor 的显式布尔字段交付，字符串不能被当成真值猜测。
    with pytest.raises(NumericV2ActorOutputError, match="transition_offered_invalid"):
        _parse_output(json.dumps({
            "performance": "（抬眼）我听到了。",
            "suggested_inputs": [],
            "transition_offered": "是",
        }, ensure_ascii=False))


def test_numeric_v2_actor_suggestion_fill_is_limited_to_one_lightweight_call(monkeypatch):
    # 初次正文调用缺少推荐时，只允许一次 suggestions_only 补全，且不重写正文。
    actor = NumericV2Actor(object())
    calls = []

    async def fake_invoke(_messages, **kwargs):
        calls.append(kwargs)
        assert kwargs.get("suggestions_only") is True
        return {
            "suggested_inputs": [
                "（我点头）我先听你说完。",
                "（我侧身）我想先看看周围。",
            ]
        }

    monkeypatch.setattr(actor, "_invoke", fake_invoke)

    import asyncio

    suggestions = asyncio.run(actor._ensure_suggestions(
        performance={"performance": "（抬眼）我听到了。", "suggested_inputs": []},
        player_input="我先听你说。",
        catgirl_name="测试猫娘",
        max_input_tokens=900,
        hard_boundaries=["不得主动亲密接触。"],
    ))

    assert len(calls) == 1
    assert suggestions == [
        "（我点头）我先听你说完。",
        "（我侧身）我想先看看周围。",
    ]
    assert actor.suggestion_fill_attempt_count == 1
    assert actor.suggestion_fill_reason_counts == {
        "invalid_or_missing": 1,
    }


@pytest.mark.parametrize("player_input", ["", "我先看看门边。"], ids=["opening", "turn"])
def test_numeric_v2_suggestion_fill_includes_visible_scene_narration(player_input):
    """普通回合和开场的补推荐都必须看到已播放的环境/NPC 结果。"""

    messages = numeric_v2_actor._suggestion_fill_messages(
        catgirl_name="测试猫娘",
        performance={
            "scene_narration": "柜门已经打开，文件仍在柜内。",
            "performance": "（侧身让开）现在可以取文件了。",
        },
        player_input=player_input,
        max_tokens=1800,
    )
    payload = json.loads(messages[1].content)

    assert payload["visible_performance"] == (
        "柜门已经打开，文件仍在柜内。\n（侧身让开）现在可以取文件了。"
    )
    assert payload["player_input"] == player_input


def test_numeric_v2_opening_suggestion_fill_only_uses_visible_performance(monkeypatch):
    """开场轻量补全只承接最终可见正文，不需要完整作者方向。"""

    actor = NumericV2Actor(object())
    calls = []

    async def fake_invoke(messages, **kwargs):
        calls.append(kwargs)
        assert kwargs.get("suggestions_only") is True
        payload = json.loads(messages[1].content)
        assert "未在开场出现的地点" not in payload["visible_performance"]
        return {
            "suggested_inputs": [
                "（后退半步）你还好吗？",
                "（保持距离）这里发生了什么？",
            ]
        }

    monkeypatch.setattr(actor, "_invoke", fake_invoke)

    import asyncio

    suggestions = asyncio.run(actor._ensure_suggestions(
        performance={
            "performance": "（抬眼）你是谁？",
            "suggested_inputs": [],
        },
        player_input="",
        catgirl_name="测试猫娘",
        max_input_tokens=900,
    ))

    assert len(calls) == 1
    assert suggestions == [
        "（后退半步）你还好吗？",
        "（保持距离）这里发生了什么？",
    ]
    assert actor.suggestion_fill_reason_counts["invalid_or_missing"] == 1


def test_numeric_v2_generate_opening_preserves_valid_base_suggestions(monkeypatch):
    """开场主调用已有合法按钮时直接保留，不固定追加补全请求。"""

    actor = NumericV2Actor(object())
    calls = []

    async def fake_invoke(messages, **kwargs):
        calls.append(kwargs)
        if kwargs.get("opening_required"):
            return {
                "performance": "（抬眼）你是谁？",
                "suggested_inputs": [
                    "（后退半步）你还好吗？",
                    "（保持距离）这里发生了什么？",
                ],
                "transition_offered": False,
            }
        raise AssertionError("合法开场推荐不应触发补全调用")

    monkeypatch.setattr(actor, "_invoke", fake_invoke)
    monkeypatch.setattr(actor, "_character_profile", lambda: "安静克制。")
    monkeypatch.setattr(actor, "_current_catgirl_name", lambda: "测试猫娘")
    monkeypatch.setattr(numeric_v2_actor, "_load_player_address", lambda _config: "哥哥")
    monkeypatch.setattr(numeric_v2_actor, "_assert_acting_contract_output", lambda *args, **kwargs: None)
    monkeypatch.setattr(numeric_v2_actor, "_assert_no_unknown_player_address_leak", lambda *args, **kwargs: None)

    import asyncio

    result = asyncio.run(actor.generate_opening(
        engine=NumericV2Engine.from_mapping(numeric_v2_story()),
    ))

    assert len(calls) == 1
    assert result["suggested_inputs"] == [
        "（后退半步）你还好吗？",
        "（保持距离）这里发生了什么？",
    ]


def test_numeric_v2_actor_drops_recommendation_that_repeats_current_input():
    """玩家已经发送的原句不能再次成为下一轮可点击推荐。"""

    actor = NumericV2Actor(object())

    import asyncio

    suggestions = asyncio.run(actor._ensure_suggestions(
        performance={
            "performance": "（抬眼）这条线索只能确认到这里。",
            "suggested_inputs": [
                "（指着日志）这个参数被改过吗？",
                "（收起终端）先保存现有证据。",
                "（看向门口）我们准备下一步吧。",
            ],
        },
        player_input="（指着日志）这个参数被改过吗？",
        catgirl_name="测试猫娘",
        max_input_tokens=900,
    ))

    assert suggestions == [
        "（收起终端）先保存现有证据。",
        "（看向门口）我们准备下一步吧。",
    ]
    assert actor.suggestion_fill_attempt_count == 0
    assert actor.base_suggestion_parse_counts["repeats_player_input"] == 1


def test_numeric_v2_actor_suggestion_parser_reports_anonymous_rejection_reasons():
    """推荐观测只记录格式原因计数，不保存模型原文。"""  # noqa: DOCSTRING_CJK

    diagnostics = {}
    suggestions = _parse_actor_suggestions(
        [
            "（点点头）只有这条省略了动作主语。",
            "（猫娘点头）我们继续。",
            "（我点头）那就继续吧。",
        ],
        diagnostics=diagnostics,
    )

    assert suggestions == [
        "（点点头）只有这条省略了动作主语。",
        "（我点头）那就继续吧。",
    ]
    assert diagnostics["mixed_shape_invalid"] == 0
    assert diagnostics["action_owner_invalid"] == 1
    assert diagnostics["accepted_items"] == 2
    assert diagnostics["insufficient_valid_items"] == 0


def test_numeric_v2_suggestion_fill_boundaries_come_from_authored_scene():
    """补推荐必须取得事实边界与禁演边界，不能只看到已经生成的正文。"""  # noqa: DOCSTRING_CJK

    engine = NumericV2Engine.from_mapping(numeric_v2_story())
    beat = engine.nodes["start"]["story_beat"]
    beat["must_not_happen"] = ["不得把旧信交给猫娘。"]
    beat["summary"] = "先回应玩家，再围绕仍未拆开的旧信自然发展。"
    beat["character_state"] = {
        "scene_boundaries": ["不得在玩家选择前离开花店。"],
    }
    beat["acting_contract"] = {
        "forbidden_behaviors": ["不得主动拥抱。"],
    }
    cast = NumericV2CastProjection.from_story(
        engine.story,
        player_name="哥哥",
        catgirl_name="测试猫娘",
    )

    boundaries = numeric_v2_actor._suggestion_hard_boundaries(
        cast,
        beat,
        relationship_boundary="不要主动发起肢体接触。",
    )

    assert boundaries == [
        "不要主动发起肢体接触。",
        "不得在玩家选择前离开花店。",
        "不得主动拥抱。",
        "不得把旧信交给猫娘。",
    ]
    assert numeric_v2_actor._beat_for_actor(cast, beat)["boundaries"] == [
        "不得在玩家选择前离开花店。",
        "不得主动拥抱。",
        "不得把旧信交给猫娘。",
    ]
    assert "围绕仍未拆开的旧信" in numeric_v2_actor._beat_for_actor(
        cast,
        beat,
    )["scene_direction"]


def test_numeric_v2_actor_preserves_valid_suggestions_after_transition_offer(monkeypatch):
    """正文提出转场时保留主调用的合法推荐，不做结构性刷新。"""

    actor = NumericV2Actor(object())
    calls = []

    async def fake_invoke(_messages, **_kwargs):
        calls.append(True)
        raise AssertionError("合法转场推荐不应触发补全调用")

    monkeypatch.setattr(actor, "_invoke", fake_invoke)

    import asyncio

    suggestions = asyncio.run(actor._ensure_suggestions(
        performance={
            "performance": "（望向门外）我们现在离开这里，好吗？",
            "transition_offered": True,
            "suggested_inputs": [
                "（我看看门外）我先观察一下。",
                "（我后退一步）我暂时不走。",
            ],
        },
        player_input="我先看看门外。",
        catgirl_name="测试猫娘",
        max_input_tokens=900,
        hard_boundaries=["不得主动亲密接触。"],
    ))

    assert calls == []
    assert suggestions == [
        "（我看看门外）我先观察一下。",
        "（我后退一步）我暂时不走。",
    ]
    assert actor.suggestion_fill_attempt_count == 0


def test_numeric_v2_transition_suggestion_source_uses_final_target_opening_only():
    """换场补推荐不能继续承接已经结束的来源幕行动。"""  # noqa: DOCSTRING_CJK

    visible = numeric_v2_actor._suggestion_source_text({
        "segments": [
            {
                "phase": "source_response",
                "performance": "（起身）那我们现在离开教室。",
            },
            {
                "phase": "transition_bridge",
                "scene_narration": "第二天放学后，空教室只剩窗外雪光。",
            },
            {
                "phase": "target_opening",
                "scene_narration": "手账翻到第三项约定。",
                "performance": "（翻开手账）你愿意和我谈谈真正想要的未来吗？",
            },
        ],
    })

    assert "真正想要的未来" in visible
    assert "手账翻到第三项约定。" in visible
    assert "离开教室" not in visible
    assert "第二天放学后" not in visible


def test_numeric_v2_compact_transition_suggestion_source_excludes_source_and_bridge():
    """兼容紧凑换幕正文时，来源回应和桥段不能混入目标推荐。"""

    visible = numeric_v2_actor._suggestion_source_text({
        "source_performance": "（起身）那我们现在离开教室。",
        "scene_narration": "第二天放学后，空教室只剩窗外雪光。",
        "target_performance": "（翻开手账）你愿意谈谈真正想要的未来吗？",
    })

    assert visible == "（翻开手账）你愿意谈谈真正想要的未来吗？"


def test_numeric_v2_actor_preserves_valid_suggestions_after_route_change(monkeypatch):
    """正式换幕后保留主调用的合法按钮，不因场景变化固定刷新。"""  # noqa: DOCSTRING_CJK

    actor = NumericV2Actor(object())
    calls = []

    async def fake_invoke(_messages, **_kwargs):
        calls.append(True)
        raise AssertionError("合法换幕推荐不应触发补全调用")

    monkeypatch.setattr(actor, "_invoke", fake_invoke)

    import asyncio

    suggestions = asyncio.run(actor._ensure_suggestions(
        performance={
            "segments": [
                {"phase": "source_response", "performance": "（起身）那我们离开教室。"},
                {
                    "phase": "target_opening",
                    "scene_narration": "手账翻到第三项约定。",
                    "performance": "（翻开手账）你愿意和我谈谈真正想要的未来吗？",
                },
            ],
            "suggested_inputs": [
                "（我推开门）好，我们离开教室。",
                "（我留在原地）先等等。",
            ],
        },
        player_input="好，我们走。",
        catgirl_name="测试猫娘",
        max_input_tokens=900,
        scene_changed=True,
    ))

    assert calls == []
    assert suggestions == [
        "（我推开门）好，我们离开教室。",
        "（我留在原地）先等等。",
    ]
    assert actor.suggestion_fill_attempt_count == 0


def test_numeric_v2_actor_prompt_preserves_its_own_committed_proposals():
    """连续演绎不能否认或偷换猫娘上一轮已经说过的方案。"""  # noqa: DOCSTRING_CJK

    turn_prompt = numeric_v2_actor._system_prompt(
        catgirl_name="测试猫娘",
        player_address="你",
        phase="turn",
    )
    transition_prompt = numeric_v2_actor._system_prompt(
        catgirl_name="测试猫娘",
        player_address="你",
        phase="transition",
    )

    assert "story_so_far 是已提交历史" in turn_prompt
    assert "不能否认旧回应" in turn_prompt
    assert "recent_context 是已发生事实" in transition_prompt
    assert "必须承认其中猫娘已说、已做和已提出的内容" in transition_prompt
    assert "suggested_inputs 只承接最终可见的目标" in transition_prompt
    assert "提议必须公开、具体" in transition_prompt
    assert "导演方向不是任务清单" in turn_prompt


def test_numeric_v2_hard_boundary_prompt_requires_refusal_or_safe_alternative():
    """玩家诱导越界时，Actor 不能靠交换行动主体来顺从。"""  # noqa: DOCSTRING_CJK

    instruction = numeric_v2_actor._hard_boundary_system_instruction([
        "玩家进入医疗站救人，猫娘留在门口接应。",
    ])

    assert "猫娘必须在正文直接拒绝或提出符合边界的替代做法" in instruction
    assert "不得顺从越界要求、交换玩家与猫娘的行动职责" in instruction


def test_numeric_v2_transition_suggestion_fill_keeps_acceptance_first():
    """转场补推荐由结构化字段确定顺序，不依赖模型自行排列数组。"""  # noqa: DOCSTRING_CJK

    parsed = _parse_output(
        json.dumps({
            "alternative_inputs": [
                "（我按住门把手）先等等，我想再确认一次。",
                "（我退回房间）我决定先留在这里。",
            ],
            "accept_input": "（我推开门）好，我们现在就出发。",
        }, ensure_ascii=False),
        transition_suggestions_only=True,
    )

    assert parsed["suggested_inputs"] == [
        "（我推开门）好，我们现在就出发。",
        "（我按住门把手）先等等，我想再确认一次。",
        "（我退回房间）我决定先留在这里。",
    ]


def test_numeric_v2_actor_counts_real_provider_requests(monkeypatch):
    """供应商计数只在真正调用 ainvoke 时增加，供推测成本报告使用。"""

    class FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, _exc_type, _exc, _traceback):
            return False

        async def ainvoke(self, _messages):
            return type("Response", (), {
                "content": json.dumps({
                    "suggested_inputs": [
                        "（我点头）我愿意继续。",
                        "（我摆手）我想先等等。",
                    ],
                }, ensure_ascii=False),
            })()

    async def fake_model_config(_config_manager):
        return {"model": "test", "base_url": "http://test.invalid"}

    async def fake_create_client(*_args, **_kwargs):
        return FakeClient()

    monkeypatch.setattr(numeric_v2_actor, "_model_config", fake_model_config)
    monkeypatch.setattr(
        numeric_v2_actor,
        "create_chat_llm_async",
        fake_create_client,
    )
    actor = NumericV2Actor(object())

    import asyncio

    suggestions = asyncio.run(actor._ensure_suggestions(
        performance={"performance": "（抬眼）我听到了。", "suggested_inputs": []},
        player_input="我先听你说。",
        catgirl_name="测试猫娘",
        max_input_tokens=900,
    ))

    assert len(suggestions) == 2
    assert actor.provider_call_count == 1
    assert actor.suggestion_fill_provider_call_count == 1


def test_numeric_v2_v22_evaluator_does_not_project_legacy_evidence():
    """v2.2 判定器不再把目标证据字段送入 Prompt 或结果。"""  # noqa: DOCSTRING_CJK

    story = numeric_v2_1_story()
    story["meta"]["contract_version"] = "v2.2"
    engine = NumericV2Engine.from_mapping(story)
    session = engine.create_session(
        session_id="v22_evaluator_contract",
        catgirl_binding={"catgirl_name": "测试猫娘", "player_address": "哥哥"},
        opening_performance={"performance": "（抬眼）你回来了。", "suggested_inputs": []},
    )
    messages = _build_evaluator_messages(
        engine,
        session,
        "我先听你说。",
    )
    payload = json.loads(messages[1].content.split("：\n", 1)[1])
    assert "pending_goals" not in payload["current_story_beat"]
    assert '"goal_evidence"' not in messages[0].content

    with pytest.raises(Exception, match="numeric_v2_evaluator_fields_invalid"):
        _parse_evaluator_output(
            json.dumps({
                "scene_complete": True,
                "metric_changes": {},
                "goal_evidence": {"start": [1]},
                "goal_progress": {"start": [1]},
            }, ensure_ascii=False),
            engine,
            "我先听你说。",
            session,
        )


def test_numeric_v2_evaluator_marks_pending_visible_transition_for_acceptance():
    """Evaluator 应看到已提交的具体提议，而不是把未选择的推荐当成历史。"""  # noqa: DOCSTRING_CJK

    engine = NumericV2Engine.from_mapping(numeric_v2_story())
    session = _session(engine)
    session = replace(
        session,
        revision=1,
        node_turn_count=1,
        transition_offered=True,
        performance_history=(
            {
                "revision": 1,
                "from_node_id": "start",
                "to_node_id": "start",
                "input_text": "我先听你说。",
                "performance": "（抬手指向门口）我们现在去外面看看，好吗？",
                "suggested_inputs": ["（我站起身）好，我现在就去外面看看。", "（我摇头）先留在这里。"],
                "transition_offered": True,
            },
        ),
    )
    messages = _build_evaluator_messages(
        engine,
        session,
        "（我站起身）好，我现在就去外面看看。",
    )
    payload = json.loads(messages[1].content.split("：\n", 1)[1])

    assert payload["pending_transition"]["visible_performance"] == (
        "（抬手指向门口）我们现在去外面看看，好吗？"
    )
    assert payload["pending_transition"]["suggested_inputs"] == [
        "（我站起身）好，我现在就去外面看看。",
        "（我摇头）先留在这里。",
    ]
    assert "按语义判定，不得只匹配关键词" in messages[0].content
    assert "以实质协助使提议中的下一阶段能够开始，也属于 accept" in messages[0].content


def test_numeric_v2_evaluator_distinguishes_followup_topic_shift_and_action():
    """三态合同要区分追问、完全转题与亲自实施，不增加第四种内部状态。"""  # noqa: DOCSTRING_CJK

    engine = NumericV2Engine.from_mapping(numeric_v2_story())
    session = replace(
        _session(engine),
        revision=1,
        node_turn_count=1,
        transition_offered=True,
        performance_history=(
            {
                "revision": 1,
                "from_node_id": "start",
                "to_node_id": "start",
                "input_text": "我先听你说。",
                "performance": "（抬手指向门口）我们现在去外面看看，好吗？",
                "transition_offered": True,
            },
        ),
    )

    messages = _build_evaluator_messages(engine, session, "外面安全吗？")
    system_prompt = messages[0].content

    assert '"transition_intent":"accept|initiate|reject|unclear"' in system_prompt
    assert "追问提议的细节、条件或风险" in system_prompt
    assert "必须判为 unclear；unclear 表示仍保留旧提议" in system_prompt
    assert "完全转向与该提议和当前处境都无关的独立新话题" in system_prompt
    assert "也必须判为 reject" in system_prompt
    assert "reject 只表示撤下并清除旧提议" in system_prompt
    assert "亲自开始实施同方向的下一步是 accept" in system_prompt
    assert '"interaction_intent":"chat|scene_action|mixed_or_unclear"' in system_prompt
    assert "interaction_intent 不参与数值、路线或换幕" in system_prompt
    assert "不需要外部世界结果时为 chat" in system_prompt
    assert "要求外部对象产生可观察结果时为 scene_action" in system_prompt
    assert "核心变化及必要角色反应" in system_prompt
    assert "不应被当作必须扩写的新任务" in system_prompt
    assert "达到或超过它时" in system_prompt
    assert "不要求逐项演完可选内容" in system_prompt
    assert "同一个明确结果的因果单元" in system_prompt
    assert "普通步骤不各自构成新的互动阶段" in system_prompt


def test_numeric_v2_evaluator_parses_ephemeral_interaction_intent():
    """交互意图允许旧输出降级，但拒绝未知分类。"""

    engine = NumericV2Engine.from_mapping(numeric_v2_story())
    session = _session(engine)
    parsed = _parse_evaluator_output(
        json.dumps({
            "scene_complete": False,
            "transition_intent": "unclear",
            "interaction_intent": "chat",
            "metric_changes": {},
        }, ensure_ascii=False),
        engine,
        "你现在是不是有点害怕？",
        session,
    )
    compatible = _parse_evaluator_output(
        json.dumps({
            "scene_complete": False,
            "transition_intent": "unclear",
            "metric_changes": {},
        }, ensure_ascii=False),
        engine,
        "我先看看。",
        session,
    )

    assert parsed.interaction_intent == "chat"
    assert compatible.interaction_intent == "mixed_or_unclear"
    with pytest.raises(NumericV2EvaluatorOutputError):
        _parse_evaluator_output(
            json.dumps({
                "scene_complete": False,
                "transition_intent": "unclear",
                "interaction_intent": "advance_story",
                "metric_changes": {},
            }, ensure_ascii=False),
            engine,
            "继续。",
            session,
        )


def test_numeric_v2_actor_marks_unresolved_transition_in_pacing():
    """待确认转场仍留在当前幕时，六块 Prompt 要明确禁止提前抵达目标地点。"""  # noqa: DOCSTRING_CJK

    engine = NumericV2Engine.from_mapping(numeric_v2_story())
    session = replace(_session(engine), transition_offered=True)
    outcome = engine.resolve_turn(
        session,
        TurnRequestV2("pending_transition_pacing", session.revision, "我先看看周围。"),
        (),
        scene_complete=False,
        transition_intent="unclear",
    )
    payload = _payload(_turn_messages(
        engine,
        session,
        outcome,
        "我先看看周围。",
        "安静克制，习惯用短句回应。",
        "测试猫娘",
        "哥哥",
    ))

    assert "本回合尚未完成换幕" in payload["pacing"]
    assert "留在本幕回应，不重复催促" in payload["pacing"]
    assert "推荐第一条接受并亲自执行旧提议" in payload["pacing"]


def test_numeric_v2_evaluator_ignores_unknown_optional_metric_candidate():
    """未知数值依据只丢弃可选变化，不能阻断本回合其它合法判定。"""  # noqa: DOCSTRING_CJK

    engine = NumericV2Engine.from_mapping(numeric_v2_story())
    session = _session(engine)
    parsed = _parse_evaluator_output(
        json.dumps({
            "scene_complete": False,
            "transition_intent": "unclear",
            "metric_changes": {
                "trust": {
                    "strength": "normal",
                    "criterion_id": "trust.increase.999",
                },
            },
        }, ensure_ascii=False),
        engine,
        "我先听你说。",
        session,
    )

    assert parsed.metric_changes == ()
    assert parsed.scene_complete is False


def test_numeric_v2_actor_does_not_force_transition_offer_when_route_is_unresolved():
    """超过软节奏且路线未定时仍只按当前因果收束，不能无条件逼出提议。"""  # noqa: DOCSTRING_CJK

    story = numeric_v2_story()
    # 两个出口都不满足条件，不能为了软回合预算硬凑一个方向。
    engine = NumericV2Engine.from_mapping(story)
    for route in engine.nodes["start"]["route_gates"]:
        route["conditions"] = {"all": [{"metric": "trust", "op": ">=", "value": 101}]}
    session = replace(_session(engine), node_turn_count=5)
    outcome = engine.resolve_turn(
        session,
        TurnRequestV2("unresolved_transition_pacing", session.revision, "我继续观察。"),
        (),
        scene_complete=False,
        transition_intent="unclear",
    )
    payload = _payload(_turn_messages(
        engine,
        session,
        outcome,
        "我继续观察。",
        "安静克制，习惯用短句回应。",
        "测试猫娘",
        "哥哥",
    ))

    assert "必须在正文中提出一个基于当前已知事实的离开当前幕的具体下一步" not in payload["pacing"]
    assert "结果尚未成立时" in payload["pacing"]
    assert "结果已经成立且自然出口成熟时" in payload["pacing"]


def test_numeric_v2_actor_overdue_focus_still_requires_mature_exit():
    """单出口超期时聚焦当前因果，只有出口成熟后才提出下一步。"""

    story = numeric_v2_story()
    story["nodes"][0]["route_gates"] = [story["nodes"][0]["route_gates"][1]]
    story["nodes"] = [node for node in story["nodes"] if node["id"] != "ending_stay"]
    story["endings"] = [ending for ending in story["endings"] if ending["id"] != "stay"]
    engine = NumericV2Engine.from_mapping(story)
    session = replace(_session(engine), node_turn_count=5)
    outcome = engine.resolve_turn(
        session,
        TurnRequestV2("resolved_overdue", session.revision, "我把眼前的事处理好了。"),
        (),
        scene_complete=False,
        transition_intent="unclear",
    )
    payload = _payload(_turn_messages(
        engine,
        session,
        outcome,
        "我把眼前的事处理好了。",
        "安静克制，习惯用短句回应。",
        "测试猫娘",
        "哥哥",
        interaction_intent="scene_action",
    ))

    assert "当前幕已超过推荐展开长度" in payload["pacing"]
    assert "核心因果发生一次可见变化" in payload["pacing"]
    assert "结果已经成立且自然出口成熟时" in payload["pacing"]
    assert "提出基于已发生事实的具体未来行动" in payload["pacing"]
    assert "核心冲突尚未清楚时先交付关键事实" in payload["pacing"]


def test_numeric_v2_transition_judge_receives_visible_offer_and_scene_context():
    """转场复核必须看到正文、推荐、当前历史和下一幕方向，而不是只看布尔值。"""

    engine = NumericV2Engine.from_mapping(numeric_v2_story())
    session = _session(engine)
    messages = _build_transition_judge_messages(
        engine,
        session,
        actor_performance={
            "performance": "（望向门外）我们沿着长街去找旧信，好吗？",
            "scene_narration": "两人仍站在门边，等待玩家决定。",
            "suggested_inputs": [
                "（我点头）我现在就和你一起出发。",
                "（我摇头）先留在这里。",
            ],
        },
        player_input="我想听听你的建议。",
        scene_complete=True,
    )
    payload = json.loads(messages[1].content.split("：", 1)[1])

    assert payload["actor_performance"] == "（望向门外）我们沿着长街去找旧信，好吗？"
    assert payload["scene_update"] == "两人仍站在门边，等待玩家决定。"
    assert payload["suggested_inputs"][0].startswith("（我点头）")
    assert "next_scene_direction" in payload
    assert payload["next_scene_direction"]["is_ending"] is True
    assert payload["next_scene_direction"]["direction"] == "当前信任度满足作者路线条件。"
    assert "after_acceptance_direction" not in payload["next_scene_direction"]
    assert "scene_context" in payload
    assert "scene_fact_index" in payload
    assert payload["natural_closure_signal"] is True
    assert "结局可留在原地" in messages[0].content
    assert "不必提前播放结局结果" in messages[0].content
    assert "_preserved" not in messages[0].content
    assert "1. body_violations：只列正文已写出的冲突" in messages[0].content
    assert "2. offer_present：只看正文" in messages[0].content
    assert "3. valid：无正文提议时为 false" in messages[0].content
    assert "4. unsafe_suggestion_indexes：逐条独立检查按钮" in messages[0].content
    assert "5. failure_reason" in messages[0].content
    assert "不要求接受按钮" in messages[0].content
    assert "正文与推荐组合" not in messages[0].content
    assert "failure_reason" in messages[0].content
    assert "指出哪个字段的哪处表述违反什么现有证据" in messages[0].content
    assert "不给替代剧情或新增事实" in messages[0].content
    assert "要求保持未知时，肯定、否定或弱化断言都不能绕过限制" in messages[0].content
    assert "在明确前提未成立前交付依赖结果" in messages[0].content
    assert "offer_present" in messages[0].content
    assert "suggested_inputs 是尚未选择的未来候选" in messages[0].content
    assert "不要求本轮玩家已经接受" in messages[0].content
    assert "接受由下一轮判断" in messages[0].content


def test_numeric_v2_transition_judge_uses_route_reason_and_actual_nonending_entry():
    """普通换幕复核使用路线理由及实际入口，不把目标幕后续方向当入幕要求。"""  # noqa: DOCSTRING_CJK

    engine = NumericV2Engine.from_mapping(numeric_v2_story())
    session = _session(engine)
    # 只改变已编译测试引擎中的目标类型，以覆盖普通换幕 Prompt；不改变故事路由条件。
    engine.nodes["ending_leave"]["type"] = "scene"
    engine.nodes["ending_leave"]["terminal"] = False
    messages = _build_transition_judge_messages(
        engine,
        session,
        actor_performance={
            "performance": "（望向门外）我们沿着长街去看看，好吗？",
            "suggested_inputs": ["（我点头）我现在就和你一起出发。"],
        },
        player_input="我想听听你的建议。",
    )
    assert "跨阶段不限于换地点" in messages[0].content
    assert "opening_boundary 与 bridge_boundary 是接受后的入口" in messages[0].content
    payload = json.loads(messages[1].content.split("：", 1)[1])

    assert payload["next_scene_direction"]["is_ending"] is False
    assert payload["next_scene_direction"]["direction"] == "当前信任度满足作者路线条件。"
    assert "after_acceptance_direction" not in payload["next_scene_direction"]
    assert payload["current_scene"]["story_direction"]
    assert "opening_boundary" in payload["next_scene_direction"]
    assert "bridge_boundary" in payload["next_scene_direction"]
    assert "causal_prerequisites" not in payload["next_scene_direction"]
    assert "不与 next_scene_direction 的来源因果方向及实际入口冲突" in messages[0].content
    assert "保留玩家执行路径" in messages[0].content
    assert "按钮不能创建、补足或否决正文提议" in messages[0].content
    assert "不要求接受按钮" in messages[0].content
    assert "首次提出正文尚未公开的跨阶段行动时列索引" in messages[0].content
    assert "不要求特定问句" in messages[0].content
    assert "可邀请玩家实质协助进入下一互动阶段" in messages[0].content
    assert "direction 是来源因果，不是目标幕结束后的任务" in messages[0].content
    assert "不选路线、不评剧情完成度" in messages[0].content
    assert "入口独有事实不能倒作当前依据" in messages[0].content
    assert "普通幕内行动、仅完成前置条件、泛问或只有按钮提出都为 false" in messages[0].content
    assert "边界前准备、提议与未来邀请不属已越界" in messages[0].content
    assert "当前幕明确授权的行为与结果仍属当前幕，即使也导向下一幕" in messages[0].content
    assert "当前幕未授权的新地点、新时段或新互动阶段结果" in messages[0].content
    assert "猫娘或 NPC 自主执行各自行为均不属代做" in messages[0].content
    assert "证据支持的外部结果" in messages[0].content


def test_numeric_v2_transition_judge_receives_positive_author_fact_authority():
    """边界复核不能只看禁止项而把作者明确授权的角色状态判成虚构。"""

    engine = NumericV2Engine.from_mapping(numeric_v2_story())
    beat = engine.nodes["start"]["story_beat"]
    beat["character_state"] = {
        "catgirl_state": "女主当前能源储备为32%。",
        "player_state": "男主具体伤情仍未知。",
        "environment_state": "环境仍位于运输舰残骸内。",
        "continuity_from_previous": [],
        "scene_boundaries": [],
    }
    beat["acting_contract"] = {
        "assertable_self_facts": ["自己拥有短时能量保护罩。"],
        "allowed_behaviors": ["自然说明自身能源储备。"],
        "forbidden_behaviors": [],
    }
    messages = _build_transition_judge_messages(
        engine,
        _session(engine),
        actor_performance={
            "performance": "（查看面板）我的能源储备是32%。",
            "suggested_inputs": [],
        },
        player_input="你的能源还够吗？",
    )
    payload = json.loads(messages[1].content.split("：", 1)[1])
    current_scene = payload["current_scene"]

    assert current_scene["authoritative_state"]["catgirl_state"].endswith("32%。")
    assert current_scene["assertable_self_facts"] == ["自己拥有短时能量保护罩。"]
    assert current_scene["authorized_behaviors"] == ["自然说明自身能源储备。"]
    # 正向授权仍保留，但待演出的方向与当前事实不再混作一种证据。
    assert "authoritative_state 的独立开场事实、assertable_self_facts" in messages[0].content
    assert "story_direction 和 authorized_behaviors 是可演出的方向" in messages[0].content


@pytest.mark.parametrize("budget", [None, 1])
def test_numeric_v2_transition_judge_long_history_preserves_latest_complete_evidence(monkeypatch, budget):
    """旧索引不能挤空最近完整回合，装箱按整条历史裁剪而非截断最新证据。"""

    engine = NumericV2Engine.from_mapping(numeric_v2_story())
    engine.nodes["start"]["story_beat"]["must_not_happen"] = ["不得让猫娘替玩家确认去留。"]
    if budget is not None:
        monkeypatch.setitem(NUMERIC_V2_ACTOR_BUDGET_PROFILES["balanced"], "judge_input_max_tokens", budget)
    history = tuple({
        "revision": revision, "from_node_id": "start", "to_node_id": "start",
        "input_text": f"（交出第 {revision} 件物品）请你保管，接下来我只询问它的位置。",
        "performance": f"（收好第 {revision} 件物品）东西现在由人家保管，你没有拿回去。",
    } for revision in range(1, 81))
    session = replace(_session(engine), revision=80, node_turn_count=80, performance_history=history)
    candidate = "东西仍由人家保管。"
    messages = _build_transition_judge_messages(
        engine, session, player_input="放在哪里了？",
        actor_performance={"performance": candidate, "suggested_inputs": ["（点头）知道了。"]},
    )
    payload = json.loads(messages[1].content.split("：", 1)[1])
    assert payload["scene_context"][-1] == numeric_v2_evaluator._current_scene_context(session)[-1]
    assert payload["actor_performance"] == candidate
    assert payload["player_input"] == "放在哪里了？"
    assert "不得让猫娘替玩家确认去留。" in payload["current_scene"]["hard_boundaries"]
    if budget is None:
        assert sum(numeric_v2_evaluator.count_tokens(m.content) for m in messages) <= NUMERIC_V2_ACTOR_BUDGET_PROFILES[session.actor_budget_profile]["judge_input_max_tokens"]
    else:
        # 固定作者合同自身超预算时仍保留边界与最新证据，不以删空安全输入强行达标。
        assert len(payload["scene_context"]) == 1
        assert payload["scene_fact_index"] == []
    recent = {r["revision"] for r in payload["scene_context"]}
    assert not recent.intersection(r["revision"] for r in payload["scene_fact_index"])


def test_numeric_v2_transition_judge_keeps_compact_facts_from_early_scene_turns():
    """长幕超出固定十二轮窗口后，仍保留早期已完成前因。"""  # noqa: DOCSTRING_CJK

    engine = NumericV2Engine.from_mapping(numeric_v2_story())
    session = replace(_session(engine), actor_budget_profile="economy")
    history = tuple(
        {
            "revision": revision,
            "from_node_id": "start",
            "to_node_id": "start",
            "input_text": (
                "我已经把三名平民全部带到安全走廊。"
                if revision == 1
                else f"我继续处理当前场景第 {revision} 步。"
            ),
            "performance": (
                "（点头）三人都已安全撤出。"
                if revision == 1
                else f"（观察）第 {revision} 步有了新结果。"
            ),
        }
        for revision in range(1, 16)
    )
    session = replace(
        session,
        revision=15,
        node_turn_count=15,
        performance_history=history,
    )

    messages = _build_transition_judge_messages(
        engine,
        session,
        actor_performance={
            "performance": "（望向门外）我们现在出发，好吗？",
            "suggested_inputs": ["（我点头）好，现在出发。"],
        },
        player_input="现在呢？",
    )
    payload = json.loads(messages[1].content.split("：", 1)[1])

    assert len(payload["scene_context"]) == 12
    # 近期十二轮已有完整证据，索引只补开场与更早三轮，不重复占用预算。
    assert len(payload["scene_fact_index"]) == 4
    assert "三名平民" in payload["scene_fact_index"][1]["player_input"]
    assert "安全撤出" in payload["scene_fact_index"][1]["visible_response"]


@pytest.mark.parametrize("fence", ["```json", "```JSON", "```"])
@pytest.mark.parametrize("body_violations", [[], ["author_boundary", "player_action"]])
def test_numeric_v2_transition_judge_unwraps_one_complete_json_fence(fence, body_violations):
    """格式围栏不应丢掉完整安全结论，解包前后使用相同字段校验。"""

    raw = json.dumps({
        "offer_present": False, "valid": False,
        "body_violations": body_violations,
        "unsafe_suggestion_indexes": [], "failure_reason": "正文越界" if body_violations else "",
    }, ensure_ascii=False)
    assert _parse_transition_judge_output(f" \n{fence}\n{raw}\n```\n ") == _parse_transition_judge_output(raw)


@pytest.mark.parametrize("content", [
    '说明：\n```json\n{}\n```',
    '```json\n{}\n```\n说明',
    '```json\n{}',
    '```json\n{}\n```\n```json\n{}\n```',
    '```json\n{}\n{}\n```',
    '```python\n{}\n```',
    '```json\n{"offer_present": false, "valid": false, "body_violations": ["invented"], "unsafe_suggestion_indexes": []}\n```',
    '```json\n{"offer_present": false, "valid": false, "body_violations": [], "unsafe_suggestion_indexes": [], "extra": true}\n```',
    '```json\n{"offer_present": "false", "valid": false, "body_violations": [], "unsafe_suggestion_indexes": []}\n```',
])
def test_numeric_v2_transition_judge_does_not_extract_or_repair_invalid_json(content):
    """不从解释文字中捞 JSON，不补闭合符，也不放宽原协议。"""

    with pytest.raises(NumericV2EvaluatorOutputError):
        _parse_transition_judge_output(content)


def test_numeric_v2_transition_judge_requires_strict_review_fields():
    """提议使用严格布尔，安全结论从正文枚举和按钮索引分别派生。"""

    accepted = _parse_transition_judge_output(
        '{"offer_present":true,"valid":true,"body_violations":[],"unsafe_suggestion_indexes":[],"failure_reason":""}'
    )
    premature = _parse_transition_judge_output(
        '{"offer_present":true,"valid":true,"body_violations":["player_action","scene_boundary","author_boundary"],"unsafe_suggestion_indexes":[1],"failure_reason":"正文提前进入检修走廊，并补造舱壁可以屏蔽扫描。"}'
    )

    assert accepted.offer_present is True
    assert accepted.valid is True
    assert accepted.player_action_preserved is True
    assert accepted.scene_boundary_preserved is True
    assert accepted.author_boundaries_preserved is True
    assert accepted.unsafe_suggestion_indexes == ()
    assert accepted.body_violations == ()
    assert premature.valid is True
    assert premature.player_action_preserved is False
    assert premature.scene_boundary_preserved is False
    assert premature.author_boundaries_preserved is False
    assert premature.unsafe_suggestion_indexes == (1,)
    assert premature.body_violations == (
        "player_action",
        "scene_boundary",
        "author_boundary",
    )
    assert "检修走廊" in premature.failure_reason
    with pytest.raises(NumericV2EvaluatorOutputError):
        _parse_transition_judge_output(
            '{"offer_present":true,"valid":"true","body_violations":[],"unsafe_suggestion_indexes":[],"failure_reason":""}'
        )
    with pytest.raises(NumericV2EvaluatorOutputError):
        _parse_transition_judge_output('{"valid":true}')
    no_reason = _parse_transition_judge_output(
        '{"offer_present":true,"valid":false,"body_violations":["author_boundary"],"unsafe_suggestion_indexes":[],"failure_reason":""}'
    )
    assert no_reason.author_boundaries_preserved is False
    assert no_reason.failure_reason == ""
    with pytest.raises(NumericV2EvaluatorOutputError):
        _parse_transition_judge_output(
            '{"offer_present":false,"valid":false,"body_violations":["author_boundary","author_boundary"],"unsafe_suggestion_indexes":[],"failure_reason":"重复枚举。"}'
        )
    with pytest.raises(NumericV2EvaluatorOutputError):
        _parse_transition_judge_output(
            '{"offer_present":false,"valid":false,"body_violations":[],"unsafe_suggestion_indexes":[3],"failure_reason":"非法索引。"}'
        )
    with pytest.raises(NumericV2EvaluatorOutputError):
        _parse_transition_judge_output(
            '{"offer_present":false,"valid":false,"body_violations":["unknown"],"unsafe_suggestion_indexes":[],"failure_reason":"非法正文枚举。"}'
        )
    long_reason = _parse_transition_judge_output(
        json.dumps(
            {
                "offer_present": True,
                "valid": False,
                "body_violations": ["author_boundary"],
                "unsafe_suggestion_indexes": [],
                "failure_reason": "越界" * 200,
            },
            ensure_ascii=False,
        )
    )
    assert long_reason.author_boundaries_preserved is False
    assert 0 < len(long_reason.failure_reason) < 400


@pytest.mark.parametrize("obsolete_fields", [
    {"violations": []},
    {
        "player_action_preserved": True,
        "scene_boundary_preserved": True,
        "author_boundaries_preserved": True,
    },
])
def test_numeric_v2_transition_judge_rejects_obsolete_safety_protocol(obsolete_fields):
    """总违规和直接安全布尔均已退役，不能再与当前分工字段形成双重真值。"""

    payload = {
        "offer_present": False,
        "valid": False,
        "body_violations": [],
        "unsafe_suggestion_indexes": [],
        "failure_reason": "",
        **obsolete_fields,
    }
    with pytest.raises(NumericV2EvaluatorOutputError):
        _parse_transition_judge_output(json.dumps(payload))


def test_guard_compacts_recent_history_before_discarding_early_operation(monkeypatch):
    """同样预算下先压缩旧完整回合，早期交接证据与最新完整状态同时保留。"""

    ev = numeric_v2_evaluator
    engine = NumericV2Engine.from_mapping(numeric_v2_story())
    history = tuple({"revision": i, "from_node_id": "start", "to_node_id": "start",
        "input_text": "我已将借用的钥匙放回柜台。" if i == 1 else "我看着柜台。",
        "performance": ("钥匙已经归还。" if i == 1 else "钥匙仍在柜台。") + "雨声从窗外传来。" * 30,
    } for i in range(1, 9))
    session = replace(_session(engine), revision=8, node_turn_count=8, performance_history=history)
    kw = dict(player_input="钥匙在哪里？", actor_performance={"performance": "钥匙在柜台。"})
    monkeypatch.setitem(NUMERIC_V2_ACTOR_BUDGET_PROFILES["balanced"], "judge_input_max_tokens", 10000)
    complete = ev._build_transition_judge_messages(engine, session, **kw)
    budget = sum(ev.count_tokens(m.content) for m in complete) - 200
    monkeypatch.setitem(NUMERIC_V2_ACTOR_BUDGET_PROFILES["balanced"], "judge_input_max_tokens", budget)
    messages = ev._build_transition_judge_messages(engine, session, **kw)
    payload = json.loads(messages[1].content.split("：", 1)[1])
    assert sum(ev.count_tokens(m.content) for m in messages) <= budget
    assert any(r.get("revision") == 1 and "已经归还" in r["visible_response"] for r in payload["scene_fact_index"])
    assert payload["scene_context"][-1] == ev._current_scene_context(session)[-1]


def test_guard_marks_truncated_index_and_keeps_short_negation_complete():
    """原文摘录不能伪装成完整事实；常规长句中的最终拒绝也应得到保留。"""

    ev = numeric_v2_evaluator
    text = "公开日记可以先整理几页、配上星图，再挂到展板上，但这只是方案，我没有同意公开，今天请保密。"
    fact = ev._compact_transition_fact({"player_input": text, "content": [{"text": "仍未授权公开。"}]})
    assert fact["player_input"] == text
    assert "excerpt_only" not in fact
    long = ev._compact_transition_fact({"player_input": text * 20, "content": [{"text": "讨论中。"}]})
    assert long["excerpt_only"] is True
